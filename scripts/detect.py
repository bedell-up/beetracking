"""
Bee Tracker — Combined AprilTag + Color Detection
===================================================
Bombus vosnesenskii movement and group tracking
University of Portland — Franz River Campus / SLUG Garden

Detects:
  1. AprilTag 36h11 markers — individual bee identification
  2. Posca paint dots — bee group identification (Red, Orange, Pink, Blue, Light Blue, Green)

Usage:
    python3 scripts/detect.py --site A
    python3 scripts/detect.py --site A --calibrate   # Calibrate color ranges first

Logs to:
    data/site_A_detections.csv          — AprilTag detections
    data/site_A_color_detections.csv    — Color group detections
    data/verification_images/site_A/    — AprilTag crops
    data/verification_images/color/site_A/ — Color crops

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
import json
from datetime import datetime
from picamera2 import Picamera2

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

ARUCO_DICT   = cv2.aruco.DICT_APRILTAG_36h11
FRAME_WIDTH  = 3280
FRAME_HEIGHT = 2464
FRAME_SKIP   = 5

# AprilTag settings
MIN_CORNER_DISTANCE  = 10
TAG_COOLDOWN_SECONDS = 10

# Color dot settings
MIN_DOT_AREA          = 15
MAX_DOT_AREA          = 2000
COLOR_COOLDOWN_SECONDS = 10

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
CAL_DIR  = os.path.join(DATA_DIR, "calibration")

# ─────────────────────────────────────────────
# DEFAULT HSV COLOR RANGES (Posca outdoor)
# Run --calibrate to tune for your site lighting
# ─────────────────────────────────────────────

DEFAULT_COLORS = {
    "Red": {
        "ranges": [
            ([0,   120, 70],  [10,  255, 255]),
            ([165, 120, 70],  [179, 255, 255]),
        ],
        "bgr": (0, 0, 220)
    },
    "Orange": {
        "ranges": [([8,  150, 100], [22,  255, 255])],
        "bgr": (0, 100, 255)
    },
    "Pink": {
        "ranges": [([140, 50, 150], [165, 200, 255])],
        "bgr": (180, 100, 255)
    },
    "Blue": {
        "ranges": [([100, 130, 80], [125, 255, 255])],
        "bgr": (220, 50, 0)
    },
    "LightBlue": {
        "ranges": [([85, 60, 150], [105, 200, 255])],
        "bgr": (255, 180, 100)
    },
    "Green": {
        "ranges": [([40, 80, 60], [85, 255, 220])],
        "bgr": (0, 180, 0)
    },
}


# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

def setup_dirs(site):
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "verification_images", f"site_{site}"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "verification_images", "color", f"site_{site}"), exist_ok=True)
    os.makedirs(CAL_DIR, exist_ok=True)


def init_csv(path, headers):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(headers)
        print(f"[INIT] Created: {path}")
    else:
        print(f"[INIT] Appending: {path}")


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


def setup_tag_detector():
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


def load_colors(site):
    cal_path = os.path.join(CAL_DIR, f"site_{site}_colors.json")
    if os.path.exists(cal_path):
        with open(cal_path) as f:
            cal = json.load(f)
        colors = dict(DEFAULT_COLORS)
        for name, ranges in cal.items():
            if name in colors:
                colors[name]["ranges"] = ranges
        print(f"[COLORS] Loaded calibrated ranges from {cal_path}")
        return colors
    print("[COLORS] Using default HSV ranges — run --calibrate to tune for your site")
    return DEFAULT_COLORS


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log_tag(csv_path, tag_id, site, confidence):
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
    print(f"  ✓ TAG    → ID {tag_id:>4} | Site {site} | {now.strftime('%H:%M:%S')}")


def log_color(csv_path, color_name, site, area):
    now = datetime.now()
    with open(csv_path, "a", newline="") as f:
        csv.writer(f).writerow([
            color_name, site,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            now.timestamp(),
            f"{area:.0f}",
            FRAME_WIDTH, FRAME_HEIGHT
        ])
    print(f"  ✓ COLOR  → {color_name:10} | Site {site} | {now.strftime('%H:%M:%S')}")


def save_tag_image(frame, corners, tag_id, site):
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
        print(f"  [WARN] Could not save tag image: {e}")


def save_color_image(frame, contour, color_name, site):
    try:
        x, y, w, h = cv2.boundingRect(contour)
        pad  = 40
        x1   = max(0, x - pad)
        y1   = max(0, y - pad)
        x2   = min(FRAME_WIDTH,  x + w + pad)
        y2   = min(FRAME_HEIGHT, y + h + pad)
        crop = frame[y1:y2, x1:x2]
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(DATA_DIR, "verification_images",
                            "color", f"site_{site}", f"{color_name}_{ts}.jpg")
        cv2.imwrite(path, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save color image: {e}")


# ─────────────────────────────────────────────
# DETECTION
# ─────────────────────────────────────────────

def detect_tags(gray, detector):
    corners, ids, _ = detector.detectMarkers(gray)
    return corners, ids


def detect_colors(frame, colors):
    hsv      = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    detected = []
    kernel   = np.ones((3, 3), np.uint8)

    for color_name, color_data in colors.items():
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in color_data["ranges"]:
            m    = cv2.inRange(hsv, np.array(lower, np.uint8), np.array(upper, np.uint8))
            mask = cv2.bitwise_or(mask, m)

        mask    = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
        mask    = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if MIN_DOT_AREA <= area <= MAX_DOT_AREA:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    detected.append((color_name, area, cnt))
    return detected


# ─────────────────────────────────────────────
# CALIBRATION MODE
# ─────────────────────────────────────────────

def run_calibration(site):
    print(f"\n[CALIBRATION] Site {site}")
    print("Hold each Posca pen cap centered in front of the camera when prompted.")
    print("Keep it about 15cm away. Press Enter when ready.\n")

    cam         = setup_camera()
    calibrated  = {}
    color_order = ["Red", "Orange", "Pink", "Blue", "LightBlue", "Green"]

    for color_name in color_order:
        input(f"  → Hold {color_name} Posca cap in front of camera, press Enter...")
        samples = []
        for _ in range(10):
            frame  = cam.capture_array()
            hsv    = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
            cy, cx = FRAME_HEIGHT // 2, FRAME_WIDTH // 2
            region = hsv[cy-30:cy+30, cx-30:cx+30]
            samples.append(region.reshape(-1, 3))

        all_s = np.vstack(samples)
        h_min = max(0,   int(np.percentile(all_s[:, 0], 5))  - 10)
        h_max = min(179, int(np.percentile(all_s[:, 0], 95)) + 10)
        s_min = max(0,   int(np.percentile(all_s[:, 1], 5))  - 20)
        s_max = min(255, int(np.percentile(all_s[:, 1], 95)) + 20)
        v_min = max(0,   int(np.percentile(all_s[:, 2], 5))  - 20)
        v_max = min(255, int(np.percentile(all_s[:, 2], 95)) + 20)

        if color_name == "Red":
            calibrated[color_name] = [
                [[0, s_min, v_min],   [10,  s_max, v_max]],
                [[165, s_min, v_min], [179, s_max, v_max]]
            ]
        else:
            calibrated[color_name] = [[[h_min, s_min, v_min], [h_max, s_max, v_max]]]

        print(f"    ✓ {color_name}: H={h_min}-{h_max} S={s_min}-{s_max} V={v_min}-{v_max}")

    cam.stop()
    cal_path = os.path.join(CAL_DIR, f"site_{site}_colors.json")
    os.makedirs(CAL_DIR, exist_ok=True)
    with open(cal_path, "w") as f:
        json.dump(calibrated, f, indent=2)
    print(f"\n[CALIBRATION] Complete. Saved to {cal_path}")
    print("Run detection normally now.")


# ─────────────────────────────────────────────
# MAIN DETECTION LOOP
# ─────────────────────────────────────────────

def run_detection(site):
    setup_dirs(site)

    tag_csv   = os.path.join(DATA_DIR, f"site_{site}_detections.csv")
    color_csv = os.path.join(DATA_DIR, f"site_{site}_color_detections.csv")

    init_csv(tag_csv, ["tag_id", "site", "date", "time",
                       "timestamp_unix", "confidence",
                       "frame_width", "frame_height"])
    init_csv(color_csv, ["color_group", "site", "date", "time",
                         "timestamp_unix", "dot_area_px",
                         "frame_width", "frame_height"])

    cam      = setup_camera()
    detector = setup_tag_detector()
    colors   = load_colors(site)

    tag_cooldown   = {}   # {tag_id: last_logged_unix}
    color_cooldown = {}   # {color_name: last_logged_unix}

    print(f"\n[RUNNING] Site {site} — AprilTag + Color detection active.")
    print(f"[RUNNING] Press Ctrl+C to stop.\n")

    frame_count = 0
    try:
        while True:
            frame = cam.capture_array()
            frame_count += 1
            if frame_count % (FRAME_SKIP + 1) != 0:
                continue

            gray     = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            now_unix = time.time()

            # ── AprilTag detection ─────────────────────────────
            corners, ids = detect_tags(gray, detector)
            if ids is not None:
                for i, tag_id in enumerate(ids.flatten()):
                    tag_corners = corners[i]
                    side_length = np.linalg.norm(
                        tag_corners[0][0] - tag_corners[0][1])
                    if side_length < MIN_CORNER_DISTANCE:
                        continue
                    confidence = min(side_length / 100.0, 1.0)
                    if tag_id in tag_cooldown:
                        if now_unix - tag_cooldown[tag_id] < TAG_COOLDOWN_SECONDS:
                            continue
                    tag_cooldown[tag_id] = now_unix
                    print(f"  → TAG detected: ID {tag_id} (size: {side_length:.1f}px)")
                    log_tag(tag_csv, tag_id, site, confidence)
                    save_tag_image(frame, tag_corners, tag_id, site)

            # ── Color dot detection ────────────────────────────
            color_detections = detect_colors(frame, colors)
            for color_name, area, contour in color_detections:
                if color_name in color_cooldown:
                    if now_unix - color_cooldown[color_name] < COLOR_COOLDOWN_SECONDS:
                        continue
                color_cooldown[color_name] = now_unix
                print(f"  → COLOR detected: {color_name} (area: {area:.0f}px²)")
                log_color(color_csv, color_name, site, area)
                save_color_image(frame, contour, color_name, site)

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Site {site} session ended.")
        print(f"  Tags saved to:   {tag_csv}")
        print(f"  Colors saved to: {color_csv}")
    finally:
        cam.stop()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bee AprilTag + Color Detector")
    parser.add_argument("--site",      required=True,
                        help="Site identifier e.g. A, B, C ...")
    parser.add_argument("--calibrate", action="store_true",
                        help="Calibrate color HSV ranges for your site lighting")
    args = parser.parse_args()

    if args.calibrate:
        run_calibration(args.site)
    else:
        run_detection(args.site)
