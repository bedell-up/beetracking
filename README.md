# BeeTracker

Tracking yellow-faced bumblebees (*Bombus vosnesenskii*) across the University of Portland campus with small printed tags and Raspberry Pi cameras.

University of Portland · SURE 2026 · PI: Dr. Tara Prestholdt
Questions: Charles Bedell (bedell@up.edu)

---

## What this project does

We wanted to know whether bumblebees living along the restored riverbank at the Franz River Campus (part of the Portland Harbor Superfund cleanup) stay there, or travel up the hill to the SLUG community garden, and vice versa.

To find out, we glued a tiny printed barcode (an [AprilTag](https://april.eecs.umich.edu/software/apriltag)) to the back of each bee we caught, then set up eight camera stations at flower patches on both sides of campus. When a tagged bee lands in front of a camera, the station reads the tag number and logs it. If the same tag shows up at a river station and a garden station, that bee made the trip.

## The stations

There were 8 stations in two groups, about a quarter mile and 180 feet of elevation apart:

| Group | Location | Stations |
|---|---|---|
| 1 | Franz River Campus (riverbank) | A, B, C, D |
| 2 | SLUG Garden (upper campus) | E, F, G, H |

## How it worked in summer 2026

```
Camera station (x8)                 Google Sheet               Dashboard
-------------------                 ------------               ---------
detect.py watches the camera   -->  "BeeTracker Data"     -->  bees.pscapps.cloud
and logs each tag it sees           one tab per station        charts, station status,
to a CSV file on the Pi             (Site A ... Site H)        cross-campus movements
         |
         +-- sync_to_sheets.py uploads new rows every hour, 10am-6pm
```

**On each station (Raspberry Pi Zero 2 W):**

1. `scripts/detect.py` starts automatically when the Pi boots. It watches the camera at full resolution (3280 x 2464) and looks for AprilTags.
2. When it finds one, it adds a row to `data/site_X_detections.csv` and saves two photos to `data/verification_images/` (a close-up of the tag and the full frame) so we can double-check the detection later.
3. The same tag isn't logged again for 10 seconds, so a bee sitting still doesn't create hundreds of rows.
4. Every hour from 10am to 6pm, `scripts/sync_to_sheets.py` uploads any new rows to that station's tab in the shared Google Sheet.

**On the server:**

The dashboard (`app.py`) reads the Google Sheet and shows detections over time, which stations are active, and which bees have been seen on both sides of campus. It's live at https://bees.pscapps.cloud. The dashboard also has pages for the parts list, the budget, and a project overview.

**Day to day:**

Students checked on the stations remotely through [Pi Connect](https://connect.raspberrypi.com). The step-by-step instructions are in `BeeTracker_Field_Manual.docx`, including the morning check, forcing a sync, restarting the detector and troubleshooting.

## Cost

Each station cost **$148**, so the 8-station network came to **$1,184**.

| Part | Cost per station | 8 stations |
|---|---:|---:|
| Raspberry Pi Zero 2 W | $30 | $240 |
| Camera module and cable | $35 | $280 |
| Cooler | $12 | $96 |
| MicroSD card (endurance rated) | $30 | $240 |
| Weatherproof enclosure and hardware | $25 | $200 |
| Gooseneck camera mount | $16 | $128 |
| **Total** | **$148** | **$1,184** |

Not included in the total:

- **Power banks.** We used Anker PowerCore 10K packs we already owned.
- **PVC pipe stakes.** These came from existing stock.
- **3D-printed camera housings.** The only cost was a few grams of filament.
- **Field supplies,** such as superglue gel and paint pens for backup marking.
- **Pi Connect remote access.** This costs about $0.50 per station per month.

All of the software is free and open source.

## Setting up a station

These steps are for a fresh Pi Zero 2 W. Replace `A` with the station's letter.

1. **Install the operating system.** Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to put Raspberry Pi OS Lite (64-bit) on the SD card. In the Imager settings, set the hostname (for example `station-a`), the username `pi`, and turn on SSH.

2. **Get it online.** We registered each Pi on the UP IoT network by its MAC address. Then set up [Pi Connect](https://connect.raspberrypi.com) so it can be reached from a browser.

3. **Install the software:**
   ```bash
   sudo apt update && sudo apt install -y python3-picamera2 git
   pip3 install opencv-contrib-python numpy gspread google-auth --break-system-packages
   git clone https://github.com/bedell-up/beetracking.git ~/beetracking
   ```

4. **Add the Google credentials.** Copy the service account key to `/home/pi/beetracking/credentials.json`. This file is not in the repo; get it from Charles.

5. **Test the camera and detector:**
   ```bash
   cd ~/beetracking
   python3 scripts/detect.py --site A
   ```
   Hold up a printed tag. You should see it logged. Press Ctrl+C to stop.

6. **Make it run on its own.** Run `crontab -e` and add:
   ```
   @reboot sleep 20 && python3 /home/pi/beetracking/scripts/detect.py --site A >> /home/pi/beetracking/data/log.txt 2>&1
   0 10-18 * * * python3 /home/pi/beetracking/scripts/sync_to_sheets.py --site A >> /home/pi/beetracking/data/sync_log.txt 2>&1
   ```

7. **Mount it.** Put the camera about 10-15 cm above the busiest part of the flower patch, angled slightly down and facing away from the sun.

## Tags and tagging

- Tags are from the AprilTag 36h11 family. Make a printable sheet with `python3 scripts/generate_tags.py --count 64 --size 6`, which gives 64 tags at 6 mm, and print it at exactly 100% scale.
- Bees are caught by net, chilled on an ice pack for about 90 seconds, and the tag is glued to the top of the thorax.
- Record each tag number, capture site, date and time, then watch the bee for 5 minutes after release.
- Tags usually last 3-7 days. A paint-pen dot is added as a backup ID.

## Data format

Each row in a station's CSV, and in its Google Sheet tab, looks like this:

```
tag_id, site, date,       time,     timestamp_unix, confidence, frame_width, frame_height
42,     A,    2026-07-14, 10:23:45, 1784049825.3,   0.712,      3280,        2464
```

## What's in this repo

| Path | What it is |
|---|---|
| `scripts/detect.py` | Runs on each station. Reads tags from the camera and logs them. |
| `scripts/sync_to_sheets.py` | Runs on each station. Uploads new detections to the Google Sheet. |
| `scripts/generate_tags.py` | Makes a printable PDF of tags. |
| `scripts/analyze.py` | Early analysis script from the two-station pilot (Site A and B only). |
| `app.py`, `data.py`, `templates/`, `static/` | The dashboard website. |
| `Dockerfile`, `wsgi.py` | How the dashboard runs on the server. |
| `BeeTracker_Field_Manual.docx` | Instructions for students running the stations. |

## Next version

We're moving to a lighter setup for next season. Instead of reading tags on the Pi, the station will only take a quick burst of photos when something moves and upload them. The server will then find the tags. This saves battery on the Pi and lets us re-check every photo later. That code is already here (`scripts/capture.py`, `scripts/burst_uploader.py`, `detect_tags.py` and `SETUP.md`) but it was **not** used in the summer 2026 field season.
