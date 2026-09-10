"""
Bee Tracker — Motion-Triggered Burst Capture (v2)
====================================================
Bombus vosnesenskii movement tracking
University of Portland — Franz River Campus / SLUG Garden

Replaces v1's continuous-video detection loop (scripts/detect.py) with a
camera-trap style pipeline, suited to a telephoto lens + power-bank/SD-card
constrained Pi Zero W2 deployment:

    1. IDLE    — a cheap, low-resolution/low-framerate preview stream is
                 diffed frame-to-frame to watch for motion. This is the
                 only thing running most of the time, so it's light on
                 both CPU and battery.
    2. TRIGGER — the moment motion crosses threshold, the camera is
                 reconfigured to a full-resolution still mode with exposure
                 and gain *locked* (no auto-exposure hunting), and fires a
                 fast burst of frames back-to-back with nothing but a file
                 write between them — no AprilTag detection or other
                 processing happens during the burst itself, so frame
                 timing isn't affected by it.
    3. IDENTIFY — AprilTag detection over the saved burst runs afterward,
                 in a background thread, while the main loop has already
                 dropped back into cheap idle motion-watching for the next
                 bee.

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

Logs to (same layout as v1 — sync_to_sheets.py and sync_images.py need no changes):
    data/site_A_detections.csv
    data/verification_images/site_A/tag<ID>_<timestamp>.jpg       — crop around the tag
    data/verification_images/site_A/tag<ID>_<timestamp>_full.jpg  — full burst frame the tag was found in

New in v2 — NOT yet picked up by sync_images.py, see the README note in this
repo's docs/ for the follow-up needed before these will get uploaded/cleaned
up automatically:
    data/bursts/site_A/<timestamp>/frame_00.png ... frame_NN.png  (or .dng with --raw)

Auto-start via crontab:
    @reboot sleep 20 && python3 /home/pi/beetracking/scripts/capture.py --site A >> /home/pi/beetracking/data/log.txt 2>&1

Requirements (Raspberry Pi OS Bookworm — prefer apt over pip so picamera2
matches the system libcamera build):
    sudo apt install -y python3-picamera2 python3-opencv
    pip3 install opencv-contrib-python numpy --break-system-packages   # only if apt's opencv build lacks cv2.aruco

NOTE: written and reviewed off-hardware (no Pi/picamera2/libcamera available
on the machine this was authored on) — validate the Picamera2 call surface
(capture_request / make_array / save_dng / sensor_resolution) against
whatever picamera2 version is installed on the actual Pi before relying on
it in the field.
"""

import argparse
import csv
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
from picamera2 import Picamera2

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

ARUCO_DICT           = cv2.aruco.DICT_APRILTAG_36h11
MIN_CORNER_DISTANCE  = 10
TAG_COOLDOWN_SECONDS = 10     # per-tag dedupe window, same as v1

LORES_SIZE            = (320, 240)   # idle preview stream — cheap on CPU and power
MOTION_CHECK_HZ        = 2.0
MOTION_PIXEL_DELTA      = 25    # per-pixel luma delta to count as "changed"
MOTION_AREA_FRACTION    = 0.02  # fraction of pixels that must change to call it motion
POST_BURST_COOLDOWN_SEC = 8.0   # settle time after a burst before re-arming motion watch

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
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "verification_images", f"site_{site}"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "bursts", f"site_{site}"), exist_ok=True)


def get_csv_path(site):
    return os.path.join(DATA_DIR, f"site_{site}_detections.csv")


def init_csv(csv_path):
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerow([
                "tag_id", "site", "date", "time",
                "timestamp_unix", "confidence",
                "frame_width", "frame_height"
            ])
        print(f"[INIT] Created: {csv_path}")
    else:
        print(f"[INIT] Appending: {csv_path}")


def setup_detector():
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    params     = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin    = 3
    params.adaptiveThreshWinSizeMax    = 53
    params.adaptiveThreshWinSizeStep   = 4
    params.minMarkerPerimeterRate      = 0.01
    params.maxMarkerPerimeterRate      = 4.0
    params.polygonalApproxAccuracyRate = 0.1
    params.minCornerDistanceRate       = 0.01
    params.errorCorrectionRate         = 1.0
    params.useAruco3Detection          = True
    params.minSideLengthCanonicalImg   = 32
    detector = cv2.aruco.ArucoDetector(aruco_dict, params)
    print("[DETECTOR] AprilTag 36h11 detector ready")
    return detector


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
    burst_seconds elapsed. Nothing but a file write happens between frames —
    AprilTag detection is deliberately NOT run here, see process_burst()."""
    os.makedirs(burst_dir, exist_ok=True)
    frames = []
    t_start = time.monotonic()

    for i in range(settings.burst_count):
        if time.monotonic() - t_start > settings.burst_seconds:
            break
        request = cam.capture_request()
        try:
            arr = request.make_array("main")
            if settings.save_raw:
                path = os.path.join(burst_dir, f"frame_{i:02d}.dng")
                request.save_dng(path)
            else:
                path = os.path.join(burst_dir, f"frame_{i:02d}.png")
                cv2.imwrite(path, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
            frames.append((path, arr))
        finally:
            request.release()

    elapsed = time.monotonic() - t_start
    print(f"  [BURST] {len(frames)} frames in {elapsed:.2f}s -> {burst_dir}")
    return frames


# ─────────────────────────────────────────────
# POST-BURST IDENTIFICATION (background thread)
# ─────────────────────────────────────────────

csv_lock       = threading.Lock()
last_seen_lock = threading.Lock()


def log_detection(csv_path, tag_id, site, confidence, frame_width, frame_height):
    now = datetime.now()
    with csv_lock:
        with open(csv_path, "a", newline="") as f:
            csv.writer(f).writerow([
                tag_id, site,
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                now.timestamp(),
                f"{confidence:.3f}",
                frame_width, frame_height
            ])
    print(f"  ✓ LOGGED -> Tag {tag_id:>4} | Site {site} | {now.strftime('%H:%M:%S')}")


def save_tag_images(frame_rgb, corners, tag_id, site, verify_dir):
    """Saves both the full burst frame the tag was found in and a tight
    crop around the tag, sharing the same tag<ID>_<timestamp> stem — kept
    identical in shape/naming to v1 so sync_images.py needs no changes."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"tag{tag_id:04d}_{ts}"
    h, w = frame_rgb.shape[:2]

    try:
        full_path = os.path.join(verify_dir, f"{stem}_full.jpg")
        cv2.imwrite(full_path, cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save full image: {e}")

    try:
        pts  = corners[0].astype(int)
        xmin = max(0, pts.min(axis=0)[0] - 20)
        ymin = max(0, pts.min(axis=0)[1] - 20)
        xmax = min(w, pts.max(axis=0)[0] + 20)
        ymax = min(h, pts.max(axis=0)[1] + 20)
        crop = frame_rgb[ymin:ymax, xmin:xmax]
        crop_path = os.path.join(verify_dir, f"{stem}.jpg")
        cv2.imwrite(crop_path, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save crop image: {e}")


def process_burst(frames, site, csv_path, verify_dir, detector, last_seen):
    """Runs off the capture hot path (in its own thread): scans the burst
    for AprilTags, logs one CSV row + one JPEG verification pair per tag
    (first frame it's found in), respecting the same per-tag cooldown as
    v1. The main loop has already gone back to watching for motion by the
    time this is doing any real work."""
    now_unix = time.time()
    seen_this_burst = set()

    for _path, frame_rgb in frames:
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        corners, ids, _ = detector.detectMarkers(gray)
        if ids is None:
            continue

        for i, raw_id in enumerate(ids.flatten()):
            tag_id = int(raw_id)
            if tag_id in seen_this_burst:
                continue

            tag_corners = corners[i]
            side_length = np.linalg.norm(tag_corners[0][0] - tag_corners[0][1])
            if side_length < MIN_CORNER_DISTANCE:
                continue

            with last_seen_lock:
                if tag_id in last_seen and now_unix - last_seen[tag_id] < TAG_COOLDOWN_SECONDS:
                    continue
                last_seen[tag_id] = now_unix

            seen_this_burst.add(tag_id)
            confidence = min(side_length / 100.0, 1.0)
            h, w = frame_rgb.shape[:2]
            print(f"  -> Tag {tag_id:>4} identified in burst (size: {side_length:.1f}px)")
            log_detection(csv_path, tag_id, site, confidence, w, h)
            save_tag_images(frame_rgb, tag_corners, tag_id, site, verify_dir)

    if not seen_this_burst:
        print("  [BURST] No tags identified in this burst.")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def run(settings: Settings):
    setup_dirs(settings.site)
    csv_path   = get_csv_path(settings.site)
    verify_dir = os.path.join(DATA_DIR, "verification_images", f"site_{settings.site}")
    bursts_dir = os.path.join(DATA_DIR, "bursts", f"site_{settings.site}")
    init_csv(csv_path)

    detector  = setup_detector()
    last_seen = {}

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
            frames = fire_burst(cam, settings, burst_dir)

            if frames:
                threading.Thread(
                    target=process_burst,
                    args=(frames, settings.site, csv_path, verify_dir, detector, last_seen),
                    daemon=True,
                ).start()

            switch_config(cam, idle_config, settle_sec=0.3)
            time.sleep(POST_BURST_COOLDOWN_SEC)

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Site {settings.site} session ended. Data saved to {csv_path}")
    finally:
        cam.stop()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bee Tracker — motion-triggered burst capture")
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
