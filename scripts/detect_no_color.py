"""
Bee Tracker — AprilTag Detection
==================================
Bombus vosnesenskii movement tracking
University of Portland — Franz River Campus / SLUG Garden

Usage:
    python3 scripts/detect.py --site A

Logs to:
    data/site_A_detections.csv
    data/verification_images/site_A/

Auto-start via crontab:
    @reboot sleep 20 && python3 /home/pi/beetracking/scripts/detect.py --site A >> /home/pi/beetracking/data/log.txt 2>&1

Requirements:
    pip3 install picamera2 opencv-contrib-python numpy --break-system-packages
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
# CONFIGURATION
# ─────────────────────────────────────────────

ARUCO_DICT          = cv2.aruco.DICT_APRILTAG_36h11
FRAME_WIDTH         = 3280
FRAME_HEIGHT        = 2464
FRAME_SKIP          = 5
MIN_CORNER_DISTANCE = 10
COOLDOWN_SECONDS    = 10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")


# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

def setup_dirs(site):
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "verification_images", f"site_{site}"), exist_ok=True)


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


def setup_camera():
    cam = Picamera2()
    config = cam.create_video_configuration(
        main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"},
        controls={"AeEnable": True, "AwbEnable": True}
    )
    cam.configure(config)
    cam.start()
    time.sleep(3)
    print(f"[CAMERA] Started at {FRAME_WIDTH}x{FRAME_HEIGHT}")
    return cam


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


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log_detection(csv_path, tag_id, site, confidence):
    now = datetime.now()
    with open(csv_path, "a", newline="") as f:
        csv.writer(f).writerow([
            tag_id, site,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            now.timestamp(),
            f"{confidence:.3f}",
            FRAME_WIDTH, FRAME_HEIGHT
        ])
    print(f"  ✓ LOGGED → Tag {tag_id:>4} | Site {site} | {now.strftime('%H:%M:%S')}")


def save_verification_image(frame, corners, tag_id, site):
    try:
        pts  = corners[0].astype(int)
        xmin = max(0, pts.min(axis=0)[0] - 20)
        ymin = max(0, pts.min(axis=0)[1] - 20)
        xmax = min(FRAME_WIDTH,  pts.max(axis=0)[0] + 20)
        ymax = min(FRAME_HEIGHT, pts.max(axis=0)[1] + 20)
        crop = frame[ymin:ymax, xmin:xmax]
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DATA_DIR, "verification_images",
                            f"site_{site}", f"tag{tag_id:04d}_{ts}.jpg")
        cv2.imwrite(path, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save image: {e}")


# ─────────────────────────────────────────────
# MAIN DETECTION LOOP
# ─────────────────────────────────────────────

def run_detection(site):
    setup_dirs(site)
    csv_path = get_csv_path(site)
    init_csv(csv_path)

    cam      = setup_camera()
    detector = setup_detector()
    last_seen = {}

    print(f"\n[RUNNING] Site {site} detection active. Press Ctrl+C to stop.\n")

    frame_count = 0
    try:
        while True:
            frame = cam.capture_array()
            frame_count += 1
            if frame_count % (FRAME_SKIP + 1) != 0:
                continue

            gray     = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            now_unix = time.time()

            corners, ids, _ = detector.detectMarkers(gray)

            if ids is not None:
                for i, tag_id in enumerate(ids.flatten()):
                    tag_corners = corners[i]
                    side_length = np.linalg.norm(
                        tag_corners[0][0] - tag_corners[0][1])
                    if side_length < MIN_CORNER_DISTANCE:
                        continue
                    confidence = min(side_length / 100.0, 1.0)
                    if tag_id in last_seen:
                        if now_unix - last_seen[tag_id] < COOLDOWN_SECONDS:
                            continue
                    last_seen[tag_id] = now_unix
                    print(f"  → Tag {tag_id:>4} detected (size: {side_length:.1f}px)")
                    log_detection(csv_path, tag_id, site, confidence)
                    save_verification_image(frame, tag_corners, tag_id, site)

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
                        help="Site identifier e.g. A, B, C ...")
    args = parser.parse_args()
    run_detection(args.site)
