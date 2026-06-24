"""
Bee Tracker — Color Diagnostic Mode
=====================================
Paint Posca dots on a white card, place under camera at field distance (10-15cm),
and run this script. It samples every detected color region and reports the actual
HSV values the camera sees under real field conditions.

Use the output to tune your calibration file ranges.

Usage:
    python3 scripts/diagnose.py --site H

Output:
    - Terminal: HSV readings for every detected region
    - Saved images: data/diagnostic/site_H/TIMESTAMP.jpg with HSV overlay
    - Summary file: data/diagnostic/site_H_hsv_readings.csv

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

FRAME_WIDTH  = 1920
FRAME_HEIGHT = 1080
FRAME_SKIP   = 3

# Minimum contour area to analyze
MIN_AREA = 200
MAX_AREA = 5000

# How often to sample (seconds)
SAMPLE_INTERVAL = 5

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, "..", "data")


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


def find_distinct_regions(frame):
    """
    Find regions that stand out from the background.
    Uses edge detection and saturation to find paint dots.
    """
    hsv  = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

    # Find high-saturation regions (paint dots)
    sat_mask = cv2.inRange(hsv[:,:,1], 80, 255)

    # Also find very dark regions (black paint)
    dark_mask = cv2.inRange(hsv[:,:,2], 0, 60)

    # Also find very bright low-saturation regions (silver/white paint)
    silver_mask = cv2.bitwise_and(
        cv2.inRange(hsv[:,:,1], 0, 40),
        cv2.inRange(hsv[:,:,2], 160, 255)
    )

    # Combine all masks
    combined = cv2.bitwise_or(sat_mask, dark_mask)
    combined = cv2.bitwise_or(combined, silver_mask)

    # Clean up
    kernel   = np.ones((5, 5), np.uint8)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN,  kernel)
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)

    # Find contours
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if MIN_AREA <= area <= MAX_AREA:
            M = cv2.moments(cnt)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                regions.append((cx, cy, area, cnt))

    return regions


def sample_hsv(hsv_frame, cx, cy, radius=15):
    """Sample HSV values in a circle around the detection center."""
    y1 = max(0, cy - radius)
    y2 = min(hsv_frame.shape[0], cy + radius)
    x1 = max(0, cx - radius)
    x2 = min(hsv_frame.shape[1], cx + radius)
    region = hsv_frame[y1:y2, x1:x2].reshape(-1, 3)

    h_mean = int(np.mean(region[:, 0]))
    s_mean = int(np.mean(region[:, 1]))
    v_mean = int(np.mean(region[:, 2]))
    h_std  = int(np.std(region[:, 0]))
    s_std  = int(np.std(region[:, 1]))
    v_std  = int(np.std(region[:, 2]))

    return h_mean, s_mean, v_mean, h_std, s_std, v_std


def save_diagnostic_image(frame, regions, hsv_data, site, diag_dir):
    """Save annotated frame showing all detected regions with HSV values."""
    annotated = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR).copy()

    for i, (cx, cy, area, cnt, hsv_vals) in enumerate(zip(
            [r[0] for r in regions],
            [r[1] for r in regions],
            [r[2] for r in regions],
            [r[3] for r in regions],
            hsv_data)):

        h, s, v, hs, ss, vs = hsv_vals

        # Draw contour
        cv2.drawContours(annotated, [cnt], -1, (0, 255, 0), 2)

        # Draw center point
        cv2.circle(annotated, (cx, cy), 5, (0, 0, 255), -1)

        # Add HSV label
        label1 = f"#{i+1} H:{h}+/-{hs}"
        label2 = f"S:{s}+/-{ss} V:{v}+/-{vs}"
        label3 = f"Area:{area:.0f}px2"

        y_off = cy - 10
        cv2.putText(annotated, label1, (cx+8, y_off),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        cv2.putText(annotated, label2, (cx+8, y_off+14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        cv2.putText(annotated, label3, (cx+8, y_off+28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(diag_dir, f"diag_{ts}.jpg")
    cv2.imwrite(path, annotated)
    return path


def run_diagnostic(site):
    diag_dir = os.path.join(DATA_DIR, "diagnostic", f"site_{site}")
    os.makedirs(diag_dir, exist_ok=True)

    csv_path = os.path.join(DATA_DIR, f"diagnostic", f"site_{site}_hsv_readings.csv")
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerow([
            "timestamp", "region_num", "center_x", "center_y", "area",
            "h_mean", "s_mean", "v_mean", "h_std", "s_std", "v_std",
            "suggested_lower", "suggested_upper"
        ])

    cam = setup_camera()

    print(f"\n[DIAGNOSTIC] Site {site} — Color HSV Sampler")
    print(f"[DIAGNOSTIC] Place your painted card under the camera at field distance.")
    print(f"[DIAGNOSTIC] Sampling every {SAMPLE_INTERVAL} seconds. Press Ctrl+C to stop.\n")

    frame_count  = 0
    last_sample  = 0

    try:
        while True:
            frame = cam.capture_array()
            frame_count += 1

            if frame_count % (FRAME_SKIP + 1) != 0:
                continue

            now = time.time()
            if now - last_sample < SAMPLE_INTERVAL:
                continue

            last_sample = now
            regions     = find_distinct_regions(frame)

            if not regions:
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] No distinct regions found — check card position")
                continue

            hsv_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
            hsv_data  = []

            print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] Found {len(regions)} region(s):")
            print(f"  {'#':<4} {'Pos':^12} {'Area':>6}  {'H':>4} {'S':>4} {'V':>4}  {'Suggested range'}")
            print(f"  {'-'*70}")

            for i, (cx, cy, area, cnt) in enumerate(regions):
                h, s, v, hs, ss, vs = sample_hsv(hsv_frame, cx, cy)
                hsv_data.append((h, s, v, hs, ss, vs))

                # Suggest calibration range with padding
                h_lo = max(0,   h - max(hs*2, 10))
                h_hi = min(179, h + max(hs*2, 10))
                s_lo = max(0,   s - max(ss*2, 20))
                s_hi = min(255, s + max(ss*2, 20))
                v_lo = max(0,   v - max(vs*2, 20))
                v_hi = min(255, v + max(vs*2, 20))

                suggested_lower = f"[{h_lo},{s_lo},{v_lo}]"
                suggested_upper = f"[{h_hi},{s_hi},{v_hi}]"

                print(f"  {i+1:<4} ({cx:>4},{cy:>4}) {area:>6.0f}  "
                      f"{h:>4} {s:>4} {v:>4}  "
                      f"{suggested_lower} → {suggested_upper}")

                # Log to CSV
                with open(csv_path, "a", newline="") as f:
                    csv.writer(f).writerow([
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        i+1, cx, cy, f"{area:.0f}",
                        h, s, v, hs, ss, vs,
                        suggested_lower, suggested_upper
                    ])

            # Save annotated image
            img_path = save_diagnostic_image(frame, regions, hsv_data, site, diag_dir)
            print(f"  Saved: {os.path.basename(img_path)}")

    except KeyboardInterrupt:
        print(f"\n[DIAGNOSTIC] Complete.")
        print(f"  Images saved to: {diag_dir}")
        print(f"  CSV saved to:    {csv_path}")
        print(f"\n  Use the 'Suggested range' values above to update your calibration file:")
        print(f"  nano ~/beetracking/data/calibration/site_{site}_colors.json")
    finally:
        cam.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Color HSV Diagnostic Tool")
    parser.add_argument("--site", required=True, help="Site identifier e.g. A, B ...")
    args = parser.parse_args()
    run_diagnostic(args.site)
