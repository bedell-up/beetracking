"""
Bee Tracker — Server-Side AprilTag Detection (v2)
====================================================
Runs on the VPS (bees.pscapps.cloud), NOT on the Pi. The Pi is now a
camera-only unit (scripts/capture.py) that just captures motion-triggered
photo bursts and uploads every frame here via bee_gallery.py's
/api/upload/full endpoint (scripts/burst_uploader.py does that upload).
This module does the actual bee-tag identification — the heavy OpenCV
aruco work that used to run on a battery-powered Pi Zero W2 now runs on
the server instead — and logs matches to the same "BeeTracker Data"
Google Sheet the dashboard already reads from (see data.py/config.py),
so the dashboard itself needs no changes.

Started as a background thread from wsgi.py alongside the Flask app (see
start_background_scanner()) rather than as a separate service, so it
shares the container's already-mounted /var/www/bee_images volume and
/app/beetracker_credentials.json without any extra deployment plumbing.

IMPORTANT: gunicorn MUST stay at 1 worker (see Dockerfile) — this thread
starts once per worker process, so a second worker would double-scan and
double-log every detection to the Sheet. birdbox's dashboard container
has the same constraint for the same reason (in-process state that isn't
shared across workers).

State: keeps its own `tag_scans` table inside the gallery's index.db,
purely additive — bee_gallery.py's `images` table/schema is never
touched — so "what's left to scan" is just:
    images minus (images already in tag_scans)
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import gspread
from google.oauth2.service_account import Credentials

from config import CREDENTIALS_PATH

log = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 30
SCAN_BATCH_LIMIT       = 50
STORAGE_ROOT           = Path("/var/www/bee_images")   # must match init_gallery()'s storage_root in app.py

# Same sheet scripts/sync_to_sheets.py used to write to from the Pi side —
# kept here too so both can point at the exact same document if the old
# script is ever still in use during the v1->v2 transition.
SHEET_ID = "1nQi7VJWRyPay2Q2LW8T417pInBBWQboesp8L4W4NgoE"
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ARUCO_DICT          = cv2.aruco.DICT_APRILTAG_36h11
MIN_CORNER_DISTANCE = 10


# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

def _setup_detector():
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
    log.info("AprilTag 36h11 detector ready")
    return detector


def _ensure_scan_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_scans (
            image_id INTEGER PRIMARY KEY,
            scanned_at REAL,
            tags_found INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def _open_sheet(scopes=_SCOPES):
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


def _get_or_create_worksheet(spreadsheet, site):
    tab_name = f"Site {site}"
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=10000, cols=10)
        ws.append_row([
            "tag_id", "site", "date", "time",
            "timestamp_unix", "confidence",
            "frame_width", "frame_height",
        ])
        log.info("Created new Sheet tab: %s", tab_name)
        return ws


# ─────────────────────────────────────────────
# SCAN
# ─────────────────────────────────────────────

def _scan_image(img_path: Path, station: str, captured_at: float, detector, worksheet_cache, spreadsheet):
    """Runs AprilTag detection on one image and logs any hits. Returns the
    number of tags found (independent of whether the Sheet write actually
    succeeded, so the caller can decide whether it's safe to mark scanned)."""
    frame = cv2.imread(str(img_path))
    if frame is None:
        log.warning("Could not decode image: %s", img_path)
        return 0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return 0

    h, w = frame.shape[:2]
    when = datetime.fromtimestamp(captured_at)
    found = 0

    for i, raw_id in enumerate(ids.flatten()):
        tag_id = int(raw_id)
        tag_corners = corners[i]
        side_length = float(((tag_corners[0][0] - tag_corners[0][1]) ** 2).sum() ** 0.5)
        if side_length < MIN_CORNER_DISTANCE:
            continue
        confidence = min(side_length / 100.0, 1.0)
        found += 1

        if spreadsheet is not None:
            if station not in worksheet_cache:
                worksheet_cache[station] = _get_or_create_worksheet(spreadsheet, station)
            worksheet_cache[station].append_row([
                tag_id, station,
                when.strftime("%Y-%m-%d"), when.strftime("%H:%M:%S"),
                when.timestamp(), f"{confidence:.3f}", w, h,
            ], value_input_option="RAW")
            log.info("Tag %s identified at site %s (%s)", tag_id, station, img_path.name)

    return found


def _scan_cycle(conn, detector):
    _ensure_scan_table(conn)
    rows = conn.execute(f"""
        SELECT id, station, filename, date, captured_at
        FROM images
        WHERE has_full = 1 AND id NOT IN (SELECT image_id FROM tag_scans)
        ORDER BY id ASC
        LIMIT {SCAN_BATCH_LIMIT}
    """).fetchall()

    if not rows:
        return 0

    spreadsheet = None
    try:
        spreadsheet = _open_sheet()
    except Exception as e:
        log.error("Could not reach Google Sheets this cycle (will retry unscanned images next cycle): %s", e)

    worksheet_cache = {}
    total_found = 0

    for image_id, station, filename, date_str, captured_at in rows:
        img_path = STORAGE_ROOT / station / "full" / date_str / filename
        tags_found = 0
        try:
            if img_path.exists():
                tags_found = _scan_image(img_path, station, captured_at, detector, worksheet_cache, spreadsheet)
            else:
                log.warning("Indexed image missing on disk: %s", img_path)
        except Exception as e:
            log.error("Scan failed for %s: %s", img_path, e)

        # Only mark an image scanned once we've actually gotten any tags it
        # has onto the Sheet — if the Sheet was unreachable and tags were
        # found, leave it unscanned so the next cycle retries the upload.
        if spreadsheet is not None or tags_found == 0:
            conn.execute(
                "INSERT OR REPLACE INTO tag_scans (image_id, scanned_at, tags_found) VALUES (?, ?, ?)",
                (image_id, time.time(), tags_found),
            )
            conn.commit()

        total_found += tags_found

    return total_found


def _scan_loop():
    db_path = STORAGE_ROOT / "index.db"
    detector = _setup_detector()
    log.info("Tag-scanner running, checking every %ss", SCAN_INTERVAL_SECONDS)

    while True:
        try:
            conn = sqlite3.connect(str(db_path))
            found = _scan_cycle(conn, detector)
            conn.close()
            if found:
                log.info("Scan cycle: %d tag(s) logged", found)
        except Exception as e:
            log.error("Scan loop error: %s", e)
        time.sleep(SCAN_INTERVAL_SECONDS)


def start_background_scanner():
    t = threading.Thread(target=_scan_loop, daemon=True, name="tag-scanner")
    t.start()
    return t
