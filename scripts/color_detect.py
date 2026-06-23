"""
Bee Color Detection Script
===========================
Detects Posca paint dot colors on Bombus vosnesenskii workers
to identify bee groups independently of AprilTag detection.

Colors detected:
    Red, Orange, Pink, Blue, Light Blue, Green

Usage:
    python3 scripts/color_detect.py --site A           # Normal detection mode
    python3 scripts/color_detect.py --site A --calibrate # Calibration mode

Auto-start via crontab (runs alongside detect.py):
    @reboot sleep 25 && python3 /home/pi/beetracking/scripts/color_detect.py --site A >> /home/pi/beetracking/data/color_log.txt 2>&1

Logs to: data/site_A_color_detections.csv
Images:  data/verification_images/color/site_A/

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

FRAME_WIDTH  = 1920
FRAME_HEIGHT = 1080
FRAME_SKIP   = 2

# Minimum contour area to consider as a paint dot (pixels²)
MIN_DOT_AREA = 15
MAX_DOT_AREA = 2000

# Cooldown per color group — seconds before re-logging same color
COOLDOWN_SECONDS = 10

# Calibration file — saved per site after running --calibrate
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "..", "data")
CAL_DIR    = os.path.join(DATA_DIR, "calibration")

# ─────────────────────────────────────────────
# DEFAULT HSV COLOR RANGES (Posca outdoor)
# These are good starting points but run --calibrate
# to tune them for your specific field site lighting.
#
# HSV format: [H_min, S_min, V_min], [H_max, S_max, V_max]
# Hue: 0-179, Saturation: 0-255, Value: 0-255
# ─────────────────────────────────────────────

DEFAULT_COLORS = {
    "Red": {
        "ranges": [
            ([0,   120, 70],  [10,  255, 255]),   # Lower red hue
            ([165, 120, 70],  [179, 255, 255]),    # Upper red hue (wraps)
        ],
        "bgr": (0, 0, 220)
    },
    "Orange": {
        "ranges": [
            ([8,   150, 100], [22,  255, 255]),
        ],
        "bgr": (0, 100, 255)
    },
    "Pink": {
        "ranges": [
            ([140, 50,  150], [165, 200, 255]),
        ],
        "bgr": (180, 100, 255)
    },
    "Blue": {
        "ranges": [
            ([100, 130, 80],  [125, 255, 255]),
        ],
        "bgr": (220, 50, 0)
    },
    "LightBlue": {
        "ranges": [
            ([85,  60,  150], [105, 200, 255]),
        ],
        "bgr": (255, 180, 100)
    },
    "Green": {
        "ranges": [
            ([40,  80,  60],  [85,  255, 220]),
        ],
        "bgr": (0, 180, 0)
    },
}


def get_cal_path(site):
    os.makedirs(CAL_DIR, exist_ok=True)
    return os.path.join(CAL_DIR, f"site_{site}_colors.json")


def load_colors(site):
    """Load calibrated colors if available, otherwise use defaults."""
    cal_path = get_cal_path(site)
    if os.path.exists(cal_path):
        with open(cal_path) as f:
            cal = json.load(f)
        print(f"[COLORS] Loaded calibrated ranges from {cal_path}")
        # Merge calibrated ranges into DEFAULT_COLORS structure
        colors = dict(DEFAULT_COLORS)
        for name, ranges in cal.items():
            if name in colors:
                colors[name]["ranges"] = ranges
        return colors
    print("[COLORS] Using default HSV ranges — run --calibrate to tune for your site")
    return DEFAULT_COLORS


def save_calibration(site, calibrated_ranges):
    cal_path = get_cal_path(site)
    with open(cal_path, "w") as f:
        json.dump(calibrated_ranges, f, indent=2)
    print(f"[COLORS] Calibration saved to {cal_path}")


def setup_camera(width, height):
    cam = Picamera2()
    config = cam.create_video_configuration(
        main={"size": (width, height), "format": "RGB888"},
        controls={"AeEnable": True, "AwbEnable": True}
    )
    cam.configure(config)
    cam.start()
    time.sleep(3)
    print(f"[CAMERA] Started at {width}x{height}")
    return cam


def detect_color_dots(frame, colors):
    """
    Detect paint dots in frame.
    Returns list of (color_name, center_x, center_y, area, contour)
    """
    hsv      = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    detected = []

    for color_name, color_data in colors.items():
        combined_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)

        for lower, upper in color_data["ranges"]:
            lower_np = np.array(lower, dtype=np.uint8)
            upper_np = np.array(upper, dtype=np.uint8)
            mask     = cv2.inRange(hsv, lower_np, upper_np)
            combined_mask = cv2.bitwise_or(combined_mask, mask)

        # Clean up mask
        kernel        = np.ones((3, 3), np.uint8)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN,  kernel)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

        # Find contours
        contours, _ = cv2.findContours(
            combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if MIN_DOT_AREA <= area <= MAX_DOT_AREA:
                M  = cv2.moments(cnt)
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    detected.append((color_name, cx, cy, area, cnt))

    return detected


def get_csv_path(site):
    return os.path.join(DATA_DIR, f"site_{site}_color_detections.csv")


def init_csv(csv_path, site):
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["color_group", "site", "date", "time",
                             "timestamp_unix", "dot_area_px",
                             "frame_width", "frame_height"])
        print(f"[INIT] Created log: {csv_path}")
    else:
        print(f"[INIT] Appending to: {csv_path}")


def log_detection(csv_path, color_name, site, area):
    now = datetime.now()
    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            color_name, site,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            now.timestamp(),
            f"{area:.0f}",
            FRAME_WIDTH, FRAME_HEIGHT
        ])
    print(f"  ✓ LOGGED → {color_name:10} | Site {site} | {now.strftime('%H:%M:%S')}")


def save_verification_image(frame, contour, color_name, site):
    verify_dir = os.path.join(DATA_DIR, "verification_images", "color", f"site_{site}")
    os.makedirs(verify_dir, exist_ok=True)
    try:
        x, y, w, h = cv2.boundingRect(contour)
        pad  = 40
        x1   = max(0, x - pad)
        y1   = max(0, y - pad)
        x2   = min(FRAME_WIDTH,  x + w + pad)
        y2   = min(FRAME_HEIGHT, y + h + pad)
        crop = frame[y1:y2, x1:x2].copy()
        # Draw colored rectangle around dot area
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(verify_dir, f"{color_name}_{ts}.jpg")
        cv2.imwrite(path, cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    except Exception as e:
        print(f"  [WARN] Could not save image: {e}")


# ─────────────────────────────────────────────
# CALIBRATION MODE
# Hold each Posca pen cap in front of camera
# when prompted. Script samples the center of
# frame and learns the HSV range for that color.
# ─────────────────────────────────────────────

def run_calibration(site):
    print(f"\n[CALIBRATION] Site {site}")
    print("Hold each Posca pen cap in front of the camera when prompted.")
    print("Keep the cap centered and close (~15cm). Press Enter when ready.\n")

    cam             = setup_camera(FRAME_WIDTH, FRAME_HEIGHT)
    calibrated      = {}
    color_order     = ["Red", "Orange", "Pink", "Blue", "LightBlue", "Green"]

    for color_name in color_order:
        input(f"  → Hold {color_name} Posca cap in front of camera, then press Enter...")

        # Sample 10 frames and average the HSV values
        samples = []
        for _ in range(10):
            frame = cam.capture_array()
            hsv   = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
            # Sample center region of frame
            cy, cx = FRAME_HEIGHT // 2, FRAME_WIDTH // 2
            region = hsv[cy-30:cy+30, cx-30:cx+30]
            samples.append(region.reshape(-1, 3))

        all_samples = np.vstack(samples)
        h_min = max(0,   int(np.percentile(all_samples[:, 0], 5))  - 10)
        h_max = min(179, int(np.percentile(all_samples[:, 0], 95)) + 10)
        s_min = max(0,   int(np.percentile(all_samples[:, 1], 5))  - 20)
        s_max = min(255, int(np.percentile(all_samples[:, 1], 95)) + 20)
        v_min = max(0,   int(np.percentile(all_samples[:, 2], 5))  - 20)
        v_max = min(255, int(np.percentile(all_samples[:, 2], 95)) + 20)

        # Handle red hue wrap-around
        if color_name == "Red" and h_max > 160:
            calibrated[color_name] = [
                [0, s_min, v_min], [10, s_max, v_max],
                [165, s_min, v_min], [179, s_max, v_max]
            ]
        else:
            calibrated[color_name] = [
                [h_min, s_min, v_min], [h_max, s_max, v_max]
            ]

        print(f"    ✓ {color_name}: H={h_min}-{h_max} S={s_min}-{s_max} V={v_min}-{v_max}")

    cam.stop()

    # Save as pairs of [lower, upper] per color
    save_data = {}
    for color_name, vals in calibrated.items():
        if color_name == "Red":
            save_data[color_name] = [
                [vals[0], vals[1]],
                [vals[2], vals[3]]
            ]
        else:
            save_data[color_name] = [[vals[0], vals[1]]]

    save_calibration(site, save_data)
    print(f"\n[CALIBRATION] Complete. Run detection normally now.")


# ─────────────────────────────────────────────
# DETECTION MODE
# ─────────────────────────────────────────────

def run_detection(site):
    colors   = load_colors(site)
    csv_path = get_csv_path(site)
    os.makedirs(DATA_DIR, exist_ok=True)
    init_csv(csv_path, site)

    cam       = setup_camera(FRAME_WIDTH, FRAME_HEIGHT)
    last_seen = {}  # {color_name: last_logged_unix}

    print(f"\n[RUNNING] Color detection active — Site {site}. Press Ctrl+C to stop.\n")

    frame_count = 0
    try:
        while True:
            frame = cam.capture_array()
            frame_count += 1
            if frame_count % (FRAME_SKIP + 1) != 0:
                continue

            detections = detect_color_dots(frame, colors)

            for color_name, cx, cy, area, contour in detections:
                now_unix = time.time()

                # Cooldown check
                if color_name in last_seen:
                    if now_unix - last_seen[color_name] < COOLDOWN_SECONDS:
                        continue

                last_seen[color_name] = now_unix
                print(f"  → {color_name:10} detected (area: {area:.0f}px² at {cx},{cy})")
                log_detection(csv_path, color_name, site, area)
                save_verification_image(frame, contour, color_name, site)

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Color detection ended. Data saved to {csv_path}")
    finally:
        cam.stop()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bee Color Group Detector")
    parser.add_argument("--site",      required=True, help="Site identifier e.g. A, B ...")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration mode to tune HSV ranges for your site lighting")
    args = parser.parse_args()

    if args.calibrate:
        run_calibration(args.site)
    else:
        run_detection(args.site)
