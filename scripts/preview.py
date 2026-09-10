"""
Bee Tracker — Camera Alignment Preview (setup-only)
======================================================
A manual tool for aiming the camera at a flower/feeder when you're setting
up a station in the field. NOT part of the always-on capture pipeline —
run this, aim the camera, Ctrl+C out of it, then unplug the screen and
start capture.py for real. This way the screen costs nothing in battery
during actual deployment.

Recommended hardware: don't bother with a GPIO/SPI display + driver
library for this — since it's only ever used briefly during setup and
disconnected afterward, a cheap mini-HDMI monitor plugged straight into
the Pi Zero W2's mini-HDMI port is simpler, cheaper, and needs zero extra
code (no display driver to install/debug). A ~$15-20 5" HDMI panel (or
any small HDMI screen you already own, via a mini-HDMI adapter) is plenty
— you're just checking framing, not judging image quality on it.

This uses Picamera2's built-in DRM preview, which renders straight to the
attached HDMI display without needing a desktop/X11 session running —
just boot the Pi to a console with the screen attached and run this.

Usage:
    python3 scripts/preview.py
    python3 scripts/preview.py --seconds 120     # auto-stop after 2 minutes instead of waiting for Enter

Requirements: same as capture.py (python3-picamera2). No extra packages.

NOTE: written and reviewed off-hardware — validate Preview.DRM actually
renders on your specific screen/HDMI setup before relying on it in the
field; some setups may need Preview.QTGL (desktop) instead if DRM doesn't
find a display.
"""

import argparse
import time

from picamera2 import Picamera2, Preview

PREVIEW_SIZE = (1024, 768)


def run(seconds):
    cam = Picamera2()
    config = cam.create_preview_configuration(main={"size": PREVIEW_SIZE})
    cam.configure(config)

    cam.start_preview(Preview.DRM)
    cam.start()
    print("[PREVIEW] Live feed should now be showing on the attached screen.")

    try:
        if seconds:
            print(f"[PREVIEW] Stopping automatically in {seconds}s...")
            time.sleep(seconds)
        else:
            input("[PREVIEW] Aim the camera, then press Enter here to stop...\n")
    except KeyboardInterrupt:
        pass
    finally:
        cam.stop_preview()
        cam.stop()
        print("[PREVIEW] Stopped. Safe to disconnect the screen now.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live camera preview for aiming the station during setup")
    parser.add_argument("--seconds", type=float, default=0, help="Auto-stop after this many seconds (default: wait for Enter)")
    args = parser.parse_args()
    run(args.seconds)
