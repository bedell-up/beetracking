"""
Bee Tracker - Google Sheets Sync Script
========================================
Pushes detection data from this station's CSV to a shared Google Sheet.
Each station gets its own tab named by site letter (Site A, Site B, etc.)

Usage:
    python3 scripts/sync_to_sheets.py --site A

Run automatically via cron at end of each day:
    0 20 * * * python3 /home/pi/beetracking/scripts/sync_to_sheets.py --site A >> /home/pi/beetracking/data/sync_log.txt 2>&1

Requirements:
    pip3 install gspread google-auth --break-system-packages

Setup:
    - credentials.json must be in /home/pi/beetracking/
    - SHEET_ID below must match your Google Sheet ID
"""

import gspread
from google.oauth2.service_account import Credentials
import csv
import os
import argparse
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

SHEET_ID       = "1nQi7VJWRyPay2Q2LW8T417pInBBWQboesp8L4W4NgoE"
CREDENTIALS    = "/home/pi/beetracking/credentials.json"
SCOPES         = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "..", "data")


def get_csv_path(site):
    return os.path.join(DATA_DIR, f"site_{site}_detections.csv")


def connect_to_sheets():
    """Authenticate and return the Google Sheets client."""
    creds  = Credentials.from_service_account_file(CREDENTIALS, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client


def get_or_create_worksheet(spreadsheet, site):
    """Get the worksheet for this site, creating it if it doesn't exist."""
    tab_name = f"Site {site}"
    try:
        worksheet = spreadsheet.worksheet(tab_name)
        print(f"[SHEETS] Found existing tab: {tab_name}")
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_name, rows=10000, cols=10)
        print(f"[SHEETS] Created new tab: {tab_name}")
        # Add header row
        worksheet.append_row([
            "tag_id", "site", "date", "time",
            "timestamp_unix", "confidence",
            "frame_width", "frame_height"
        ])
    return worksheet


def get_existing_timestamps(worksheet):
    """Get all timestamp_unix values already in the sheet to avoid duplicates."""
    try:
        records = worksheet.get_all_values()
        if len(records) <= 1:
            return set()
        # timestamp_unix is column 5 (index 4)
        return set(row[4] for row in records[1:] if len(row) > 4 and row[4])
    except Exception as e:
        print(f"[WARN] Could not fetch existing timestamps: {e}")
        return set()


def read_csv(csv_path):
    """Read the local detection CSV and return rows."""
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV not found: {csv_path}")
        return []
    rows = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            if row:
                rows.append(row)
    return rows


def sync(site):
    csv_path = get_csv_path(site)
    print(f"\n[SYNC] Starting sync for Site {site}")
    print(f"[SYNC] Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[SYNC] CSV: {csv_path}")

    # Read local data
    rows = read_csv(csv_path)
    if not rows:
        print("[SYNC] No detection data to sync.")
        return

    print(f"[SYNC] Local detections: {len(rows)}")

    # Connect to Google Sheets
    try:
        client      = connect_to_sheets()
        spreadsheet = client.open_by_key(SHEET_ID)
        worksheet   = get_or_create_worksheet(spreadsheet, site)
    except Exception as e:
        print(f"[ERROR] Could not connect to Google Sheets: {e}")
        return

    # Get existing timestamps to avoid duplicates
    existing = get_existing_timestamps(worksheet)
    print(f"[SYNC] Rows already in sheet: {len(existing)}")

    # Find new rows
    new_rows = [row for row in rows if len(row) > 4 and row[4] not in existing]
    print(f"[SYNC] New rows to upload: {len(new_rows)}")

    if not new_rows:
        print("[SYNC] Sheet is already up to date.")
        return

    # Upload in batches of 100
    batch_size = 100
    uploaded   = 0
    for i in range(0, len(new_rows), batch_size):
        batch = new_rows[i:i + batch_size]
        worksheet.append_rows(batch, value_input_option="RAW")
        uploaded += len(batch)
        print(f"[SYNC] Uploaded {uploaded}/{len(new_rows)} rows...")

    print(f"[SYNC] ✓ Sync complete — {uploaded} new detections added to Site {site} tab")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync bee detections to Google Sheets")
    parser.add_argument("--site", required=True, help="Site identifier e.g. A, B, C...")
    args = parser.parse_args()
    sync(args.site)
