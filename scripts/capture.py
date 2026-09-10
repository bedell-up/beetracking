"""
Bee Tracker — Motion-Triggered Burst Capture, camera-only (v2)
==================================================================
Bombus vosnesenskii movement tracking
University of Portland — Franz River Campus / SLUG Garden

The Pi's ONLY job now is: watch for motion cheaply, and when a bee lands,
grab a fast burst of full-resolution frames. No AprilTag identification
happens here anymore — that used to run on-device (see scripts/detect.py,
v1) but moved to the server (scripts/detect_tags.py, running on the VPS)
so the Pi Zero W2 doesn't have to spend battery/CPU on OpenCV aruco
detection. burst_uploader.py (a separate, independent process) is what
gets the burst frames off the Pi and onto the server for that to process
— this script doesn't upload anything itself, it only captures and saves.

    1. IDLE    — a cheap, low-resolution/low-framerate preview stream is
                 diffed frame-to-frame to watch for motion. This is the
                 only thing running most of the time, so it's light on
                 both CPU and battery. (This step CANNOT move to the
                 server — shipping every idle frame over the network just
                 to ask "is there motion" would cost far more power than
                 the cheap local diff it replaces.)
    2. TRIGGER — the moment motion crosses threshold, the camera is
                 reconfigured to a full-resolution still mode with exposure
                 and gain *locked* (no auto-exposure hunting), and fires a
                 fast burst of frames back-to-back with nothing but a file
                 write between them.
    3. SAVE    — frames land in data/bursts/site_X/<timestamp>/ and just
                 sit there for burst_uploader.py to pick up on its own
                 schedule. This script never blocks on the network.

CLI-flag equivalents, mapped to the Picamera2 controls actually used below
(this project drives the camera through the Picamera2 Python API, per v1,
rather than shelling out to libcamera-still):
    --shutter 1000   -> ExposureTime=1000  (microseconds; forces 1/1000s)
    --gain 4         -> AnalogueGain=4.0
    --immediate      -> AeEnable=False, on a stream that's reconfigured
                         *before* the burst starts — so there's no AF/AE
                         convergence wait to skip during the burst itself

Usage:
    python3 scripts/capture.py --site A
    python3 scripts/capture.py --site A --raw                       # save raw DNG instead of PNG
    python3 scripts/capture.py --site A --burst-count 10 --burst-seconds 2.5
    python3 scripts/capture.py --site A --shutter 800 --gain 6      # dimmer scene / denser shade

Saves to:
    data/bursts/site_A/<timestamp>/frame_00.png ... frame_NN.png  (or .dng with --raw)

Run alongside (separately) burst_uploader.py to actually get these off the
Pi — see that script's docstring. Auto-start both via crontab/systemd:
    @reboot sleep 20 && python3 /home/pi/beetracking/scripts/capture.py --site A >> /home/pi/beetracking/data/log.txt 2>&1

Requirements (Raspberry Pi OS Bookworm — prefer apt over pip so picamera2
matches the system libcamera build). Note this is now a much lighter
dependency list than v1/the previous version of this script — no OpenCV
aruco/contrib module needed on the Pi anymore, just plain image-diffing:
    sudo apt install -y python3-picamera2 python3-opencv
    pip3 install opencv-python-headless numpy --break-system-packages   # only if apt's opencv build is missing

NOTE: written and reviewed off-hardware (no Pi/picamera2/libcamera available
on the machine this was authored on) — validate the Picamera2 call surface
(capture_request / make_array / save_dng / sensor_resolution) against
whatever picamera2 version is installed on the actual Pi before relying on
it in the field.
"""

import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
from picamera2 import Picamera2

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

LORES_SIZE               = (320, 240)   # idle preview stream — cheap on CPU and power
MOTION_CHECK_HZ           = 2.0
MOTION_PIXEL_DELTA        = 25    # per-pixel luma delta to count as "changed"
MOTION_AREA_FRACTION      = 0.02  # fraction of pixels that must change to call it motion
POST_BURST_COOLDOWN_SEC   = 8.0   # settle time after a burst before re-arming motion watch

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")


@dataclass
class Settings:
    site: str
    burst_count: int
    burst_seconds: float
    shutter_us: int
    gain: float
    save_raw: bool


# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

def setup_dirs(site):
    os.makedirs(os.path.join(DATA_DIR, "bursts", f"site_{site}"), exist_ok=True)


def build_configs(cam, settings: Settings):
    """Two configs the camera flips between: a tiny idle preview and a
    full-resolution still mode with exposure/gain locked for the burst.
    Sensor resolution is read from the camera itself (cam.sensor_resolution)
    rather than hardcoded, since a lens swap doesn't change it but a sensor
    swap would."""
    full_size = cam.sensor_resolution
    print(f"[CAMERA] Sensor max resolution: {full_size[0]}x{full_size[1]}")

    idle_config = cam.create_video_configuration(
        main={"size": LORES_SIZE, "format": "YUV420"},
        buffer_count=2,
    )

    still_kwargs = dict(
        main={"size": full_size, "format": "RGB888"},
        buffer_count=settings.burst_count + 2,
        controls={
            "AeEnable": False,
            "AnalogueGain": settings.gain,
            "ExposureTime": settings.shutter_us,
        },
    )
    if settings.save_raw:
        # A "raw" stream is required for CompletedRequest.save_dng() —
        # only requested when --raw is passed, since it adds sensor-readout
        # overhead we don't want to pay on every burst otherwise.
        still_kwargs["raw"] = {}
    still_config = cam.create_still_configuration(**still_kwargs)

    return idle_config, still_config, full_size


def switch_config(cam, config, settle_sec):
    cam.stop()
    cam.configure(config)
    cam.start()
    time.sleep(settle_sec)   # let the first frame or two of stale buffer flush


# ─────────────────────────────────────────────
# IDLE MOTION WATCH
# ─────────────────────────────────────────────

def _luma(frame_yuv420):
    """YUV420 comes back as a packed array with the luma (Y) plane in the
    first `height` rows — slice it straight out, no color conversion needed."""
    return frame_yuv420[:LORES_SIZE[1], :]


def wait_for_motion(cam):
    """Poll the idle preview stream at MOTION_CHECK_HZ looking for a
    frame-to-frame change large enough to be a bee landing rather than
    sensor noise or a leaf twitching in the wind. Blocks until found."""
    period = 1.0 / MOTION_CHECK_HZ
    prev = None
    while True:
        t0 = time.monotonic()
        frame = cam.capture_array("main")
        gray = cv2.GaussianBlur(_luma(frame), (5, 5), 0)

        if prev is not None:
            diff = cv2.absdiff(gray, prev)
            changed_fraction = float(np.mean(diff > MOTION_PIXEL_DELTA))
            if changed_fraction > MOTION_AREA_FRACTION:
                print(f"  [MOTION] {changed_fraction * 100:.1f}% of frame changed — triggering burst")
                return

        prev = gray
        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, period - elapsed))


# ─────────────────────────────────────────────
# BURST CAPTURE
# ─────────────────────────────────────────────

def fire_burst(cam, settings: Settings, burst_dir):
    """Fires up to burst_count full-resolution frames, capped at
    burst_seconds elapsed. Nothing but a file write happens between
    frames — no detection, no upload, nothing that could slow the burst
    down. burst_uploader.py picks these files up later, independently."""
    os.makedirs(burst_dir, exist_ok=True)
    saved = 0
    t_start = time.monotonic()

    for i in range(settings.burst_count):
        if time.monotonic() - t_start > settings.burst_seconds:
            break
        request = cam.capture_request()
        try:
            if settings.save_raw:
                path = os.path.join(burst_dir, f"frame_{i:02d}.dng")
                request.save_dng(path)
            else:
                arr = request.make_array("main")
                path = os.path.join(burst_dir, f"frame_{i:02d}.png")
                cv2.imwrite(path, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            saved += 1
        finally:
            request.release()

    elapsed = time.monotonic() - t_start
    print(f"  [BURST] {saved} frames in {elapsed:.2f}s -> {burst_dir}")
    return saved


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def run(settings: Settings):
    setup_dirs(settings.site)
    bursts_dir = os.path.join(DATA_DIR, "bursts", f"site_{settings.site}")

    cam = Picamera2()
    idle_config, still_config, full_size = build_configs(cam, settings)

    switch_config(cam, idle_config, settle_sec=2.0)
    print(f"\n[RUNNING] Site {settings.site} — idle motion watch active "
          f"({LORES_SIZE[0]}x{LORES_SIZE[1]} @ {MOTION_CHECK_HZ}Hz). Press Ctrl+C to stop.\n")

    try:
        while True:
            wait_for_motion(cam)

            switch_config(cam, still_config, settle_sec=0.3)
            burst_dir = os.path.join(bursts_dir, datetime.now().strftime("%Y%m%d_%H%M%S"))
            fire_burst(cam, settings, burst_dir)

            switch_config(cam, idle_config, settle_sec=0.3)
            time.sleep(POST_BURST_COOLDOWN_SEC)

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Site {settings.site} session ended.")
    finally:
        cam.stop()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bee Tracker — motion-triggered burst capture (camera-only)")
    parser.add_argument("--site", required=True, help="Site identifier e.g. A, B, C ...")
    parser.add_argument("--burst-count", type=int, default=8, help="Max frames per burst (default: 8)")
    parser.add_argument("--burst-seconds", type=float, default=2.0, help="Max burst duration in seconds (default: 2.0)")
    parser.add_argument("--shutter", type=int, default=1000, help="Exposure time in microseconds (default: 1000 = 1/1000s)")
    parser.add_argument("--gain", type=float, default=4.0, help="Analogue gain / ISO multiplier (default: 4.0)")
    parser.add_argument("--raw", action="store_true", help="Save burst frames as raw DNG instead of PNG")
    args = parser.parse_args()

    run(Settings(
        site=args.site,
        burst_count=args.burst_count,
        burst_seconds=args.burst_seconds,
        shutter_us=args.shutter,
        gain=args.gain,
        save_raw=args.raw,
    ))
