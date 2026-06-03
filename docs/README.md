# Bombus vosnesenskii AprilTag Tracker
### University of Portland — SLUG / Pollinator Research
**Inter-site movement study: Green Space A ↔ Green Space B**

---

## System Overview

Two Raspberry Pi 4 camera stations monitor flower patches at each site.
Tagged bees are detected automatically. Data logs to CSV. Analysis
produces publication-ready movement statistics.

```
[Site A]                        [Site B]
Pi 4 + Camera                   Pi 4 + Camera
    ↓                               ↓
site_A_detections.csv       site_B_detections.csv
         ↓               ↓
         analyze.py  (run on either Pi or laptop)
              ↓
      analysis_results.csv
      movement_summary.txt  ← draft publication text
```

---

## Hardware Shopping List (~$270 total for 2 stations)

| Item | Qty | Est. Cost | Notes |
|---|---|---|---|
| Raspberry Pi 4 (2GB) | 2 | $45 each | Vilros or CanaKit kits include PSU |
| Raspberry Pi Camera Module 3 Wide | 2 | $35 each | Wide FOV for flower patches |
| 64GB Samsung Endurance MicroSD | 2 | $12 each | Endurance rated for continuous write |
| 20,000mAh USB-C power bank | 2 | $25 each | Anker 737 or similar — ~8hr runtime |
| Weatherproof project box (IP55) | 2 | $10 each | Amazon/electronics surplus |
| Flexible gooseneck clamp mount | 2 | $8 each | For camera positioning over flower patch |
| Posca paint pens (4-color set) | 1 | $12 | Backup visual ID system |

**Total: ~$270**
Budget remaining from $1,000: **~$730** (for additional sensors, travel, or contingency)

---

## Setup Instructions

### 1. Flash Raspberry Pi OS
- Download Raspberry Pi Imager: https://www.raspberrypi.com/software/
- Flash **Raspberry Pi OS 64-bit (Bookworm)** to each MicroSD
- Enable SSH and set hostname (`site-a` / `site-b`) in Imager settings

### 2. Run Setup Script (on each Pi)
```bash
bash setup.sh
```

### 3. Generate and Print Tags
```bash
python3 scripts/generate_tags.py --count 64 --size 5
```
- Print `tags/apriltags_5mm.pdf` at **exactly 100% scale**
- Verify with ruler: each tag = 5mm × 5mm
- Tags are printed with white border for easy cutting

### 4. Deploy Camera Stations
- Mount camera 30–50cm above highest-traffic flower cluster
- Angle slightly downward (~30°)
- Ensure bees land in frame center (watch live preview first)
- Power with USB-C power bank

### 5. Tag Bees in Field
1. Net a bee over a flower
2. Place in small container with ice pack for ~90 seconds (chills bee)
3. Apply tiny superglue dot to dorsal thorax
4. Press tag down, hold 10 seconds
5. Record tag ID in `tags/tag_manifest.csv`
6. Release at capture site, observe for 5 min

### 6. Run Detector
```bash
# On Site A Pi:
python3 scripts/detect.py --site A

# On Site B Pi:
python3 scripts/detect.py --site B
```
Ctrl+C to stop. Data saves automatically to `data/site_X_detections.csv`

### 7. Analyze Results
Copy both CSV files to one computer, then:
```bash
python3 scripts/analyze.py
```
Output: `data/analysis_results.csv` and `data/movement_summary.txt`

---

## CSV Data Format

**Detection log** (`site_A_detections.csv`):
```
tag_id, site, date, time, timestamp_unix, confidence, frame_width, frame_height
42, A, 2025-07-14, 10:23:45, 1752488625.3, 0.712, 1920, 1080
```

**Tag manifest** (`tag_manifest.csv`) — fill in field:
```
tag_id, capture_site, capture_date, capture_time, bee_sex, bee_size_estimate, notes
42, A, 2025-07-14, 09:15, F, large, foraging on phacelia
```

---

## Auto-Start on Boot (Optional)
To have the detector start automatically when Pi powers on in the field:

```bash
crontab -e
# Add this line:
@reboot sleep 15 && python3 /home/pi/bee_tracker/scripts/detect.py --site A >> /home/pi/bee_tracker/data/boot_log.txt 2>&1
```

---

## Field Notes

- **Best tagging time:** Morning (9–11am) when bees are most active and temperatures allow safe chilling
- **Tag survivorship:** AprilTags typically persist 3–7 days on *Bombus* workers (glue + weather dependent)
- **Detection range:** Reliable within ~5–8cm of camera at 5mm tag size — camera should be close to flower patch
- **Sunny conditions:** Pi camera auto-exposure handles cloud cover. Direct backlit situations (sun behind bee) reduce contrast — orient camera to face away from sun
- **Data backup:** Copy CSVs to USB drive daily — power banks occasionally run out

---

## Citation Basis

AprilTag tracking of bumblebees is consistent with methods in:
- Tautz et al. (2004) — harmonic radar tracking of *Bombus*
- Couvillon et al. (2012) — tag weight effects on *Bombus terrestris* flight
- BeesBook Project (Wario et al., 2015) — automated 2D barcode tracking in honeybees

The 36h11 AprilTag family is documented in:
> Olson et al. (2011). AprilTag: A robust and flexible visual fiducial system.
> *Proc. ICRA 2011.*

---

*Generated for University of Portland SLUG Pollinator Research*
*Contact: bedell@up.edu*
