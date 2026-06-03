"""
Bee AprilTag Detector - Raspberry Pi Camera Script
===================================================
Bombus vosnesenskii movement tracking
University of Portland - SLUG / Pollinator Research

Usage:
    python detect.py --site A
    python detect.py --site B

Logs detections to: ../data/site_A_detections.csv (or site_B)
Also saves a small verification image for every new detection.

Requirements:
    pip install picamera2 opencv-contrib-python numpy
"""

import cv2
import numpy as np
import csv
import os
import time
import argparse
from datetime import datetime
from picamera2 import Picamera2

# ─────────────────────────────────────────────
# CONFIGURATION — edit these if needed
# ─────────────────────────────────────────────

# AprilTag dictionary — 36h11 is the most robust for small tags
ARUCO_DICT = cv2.aruco.DICT_APRILTAG_36h11

# Minimum detection confidence (lower = more detections, more false positives)
MIN_CORNER_DISTANCE = 10  # pixels — filters out tiny ghost detections

# Cooldown: ignore re-detections of same tag within this many seconds
# Prevents logging the same bee 50 times as it sits on a flower
COOLDOWN_SECONDS = 10

# Camera resolution — higher = better detection but slower processing
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

# How many frames to skip between detection attempts (0 = every frame)
# Set to 2 on Pi Zero, 0 on Pi 4
FRAME_SKIP = 1

# Data and image output directories (relative to this script)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
VERIFY_DIR = os.path.join(DATA_DIR, "verification_images")

# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

def setup_dirs(site):
    os.makedirs(DATA_DIR, exist_ok=True)
    verify_path = os.path.join(VERIFY_DIR, f"site_{site}")
    os.makedirs(verify_path, exist_ok=True)
    return verify_path


def get_csv_path(site):
    return os.path.join(DATA_DIR, f"site_{site}_detections.csv")


def init_csv(csv_path, site):
    """Create CSV with header if it doesn't already exist."""
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["tag_id", "site", "date", "time", "timestamp_unix",
                             "confidence", "frame_width", "frame_height"])
        print(f"[INIT] Created new log: {csv_path}")
    else:
        print(f"[INIT] Appending to existing log: {csv_path}")


def setup_camera():
    """Initialize Pi camera with settings tuned for sunny outdoor use."""
    cam = Picamera2()
    config = cam.create_video_configuration(
        main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"},
        controls={
            "ExposureTime": 2000,       # 2ms — fast shutter to reduce motion blur
            "AnalogueGain": 2.0,        # Low gain — plenty of light outside
            "AeEnable": True,           # Auto-exposure ON (handles cloud cover)
            "AwbEnable": True,          # Auto white balance ON
        }
    )
    cam.configure(config)
    cam.start()
    time.sleep(2)  # Allow camera to settle
    print(f"[CAMERA] Started at {FRAME_WIDTH}×{FRAME_HEIGHT}")
    return cam


def setup_detector():
    """Configure AprilTag detector tuned for small fast-moving tags."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    params = cv2.aruco.DetectorParameters()

    # Tuned for small (4-5mm) tags on moving bees
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 23
    params.adaptiveThreshWinSizeStep = 4
    params.minMarkerPerimeterRate = 0.01   # Allow very small markers
    params.maxMarkerPerimeterRate = 0.3
    params.polygonalApproxAccuracyRate = 0.05
    params.minCornerDistanceRate = 0.01
    params.errorCorrectionRate = 0.9       # High error correction for small tags

    detector = cv2.aruco.ArucoDetector(aruco_dict, params)
    print("[DETECTOR] AprilTag 36h11 detector ready")
    return detector


# ─────────────────────────────────────────────
# DETECTION LOOP
# ─────────────────────────────────────────────

def log_detection(csv_path, tag_id, site, confidence):
    now = datetime.now()
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            tag_id,
            site,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            now.timestamp(),
            f"{confidence:.3f}",
            FRAME_WIDTH,
            FRAME_HEIGHT
        ])
    print(f"  ✓ LOGGED → Tag {tag_id:>4} | Site {site} | {now.strftime('%H:%M:%S')}")


def save_verification_image(frame, corners, tag_id, site, verify_dir):
    """Save a cropped image around the detected tag for manual verification."""
    try:
        pts = corners[0].astype(int)
        x_min, y_min = pts.min(axis=0) - 20
        x_max, y_max = pts.max(axis=0) + 20
        x_min, y_min = max(0, x_min), max(0, y_min)
        x_max = min(FRAME_WIDTH, x_max)
        y_max = min(FRAME_HEIGHT, y_max)

        crop = frame[y_min:y_max, x_min:x_max]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tag{tag_id:04d}_{ts}.jpg"
        filepath = os.path.join(verify_dir, filename)
        cv2.imwrite(filepath, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save verification image: {e}")


def run_detection(site):
    verify_dir = setup_dirs(site)
    csv_path = get_csv_path(site)
    init_csv(csv_path, site)

    cam = setup_camera()
    detector = setup_detector()

    # cooldown tracker: {tag_id: last_logged_unix_time}
    last_seen = {}

    print(f"\n[RUNNING] Site {site} detection active. Press Ctrl+C to stop.\n")

    frame_count = 0
    try:
        while True:
            frame = cam.capture_array()
            frame_count += 1

            # Skip frames to reduce CPU load
            if frame_count % (FRAME_SKIP + 1) != 0:
                continue

            # Convert to grayscale for detection
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

            # Run AprilTag detection
            corners, ids, rejected = detector.detectMarkers(gray)

            if ids is not None:
                for i, tag_id in enumerate(ids.flatten()):
                    tag_corners = corners[i]

                    # Filter out tiny ghost detections
                    side_length = np.linalg.norm(
                        tag_corners[0][0] - tag_corners[0][1]
                    )
                    if side_length < MIN_CORNER_DISTANCE:
                        continue

                    # Confidence proxy: larger apparent size = better detection
                    confidence = min(side_length / 100.0, 1.0)

                    # Cooldown check
                    now_unix = time.time()
                    if tag_id in last_seen:
                        if now_unix - last_seen[tag_id] < COOLDOWN_SECONDS:
                            continue  # Same bee, too soon, skip

                    last_seen[tag_id] = now_unix
                    print(f"  → Tag {tag_id:>4} detected (size: {side_length:.1f}px)")

                    log_detection(csv_path, tag_id, site, confidence)
                    save_verification_image(frame, tag_corners, tag_id, site, verify_dir)

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Site {site} session ended. Data saved to {csv_path}")
    finally:
        cam.stop()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bee AprilTag Detector")
    parser.add_argument("--site", required=True,
                        help="Which green space this station is monitoring (A or B)")
    args = parser.parse_args()
    run_detection(args.site)
