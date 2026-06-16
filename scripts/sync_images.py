"""
Bee Tracker - Verification Image Sync to Google Drive
======================================================
Copies verification images to Google Drive and deletes
them from the SD card to prevent storage filling up.

Runs daily at 5pm via cron:
    0 17 * * * python3 /home/pi/beetracking/scripts/sync_images.py --site A >> /home/pi/beetracking/data/image_sync_log.txt 2>&1

Requirements:
    pip3 install google-auth google-auth-httplib2 google-api-python-client --break-system-packages

Setup:
    - credentials.json must be in /home/pi/bee/
    - Share the BeeTracker Images folder with the service account:
      beetracker-sync@beetracker-498519.iam.gserviceaccount.com
"""

import os
import argparse
import glob
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

CREDENTIALS     = "/home/pi/beetracking/credentials.json"
SCOPES          = ["https://www.googleapis.com/auth/drive"]

# Google Drive folder ID for BeeTracker Images
# From: https://drive.google.com/drive/folders/1frIV4BzoUYKyYqDe2r9NNqPnV4rxNGp8
DRIVE_FOLDER_ID = "1frIV4BzoUYKyYqDe2r9NNqPnV4rxNGp8"

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR        = os.path.join(BASE_DIR, "..", "data")


def get_verify_dir(site):
    return os.path.join(DATA_DIR, "verification_images", f"site_{site}")


def connect_to_drive():
    creds   = Credentials.from_service_account_file(CREDENTIALS, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds)
    return service


def get_or_create_folder(service, folder_name, parent_id):
    """Find a folder by name under a parent, create if not found."""
    query   = (f"name='{folder_name}' and "
               f"mimeType='application/vnd.google-apps.folder' and "
               f"'{parent_id}' in parents and trashed=false")
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files   = results.get("files", [])

    if files:
        return files[0]["id"]

    metadata = {
        "name":     folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents":  [parent_id]
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    print(f"[DRIVE] Created folder: {folder_name}")
    return folder["id"]


def file_exists_in_drive(service, filename, parent_id):
    """Check if a file already exists in Drive to avoid re-uploading."""
    query   = f"name='{filename}' and '{parent_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id)").execute()
    return len(results.get("files", [])) > 0


def upload_image(service, filepath, filename, parent_id):
    """Upload a single image file to Google Drive."""
    metadata = {"name": filename, "parents": [parent_id]}
    media    = MediaFileUpload(filepath, mimetype="image/jpeg", resumable=False)
    service.files().create(body=metadata, media_body=media, fields="id").execute()


def sync_images(site):
    verify_dir = get_verify_dir(site)
    today      = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[IMAGE SYNC] Starting — Site {site}")
    print(f"[IMAGE SYNC] Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[IMAGE SYNC] Source: {verify_dir}")

    if not os.path.exists(verify_dir):
        print(f"[IMAGE SYNC] No verification image directory found. Nothing to sync.")
        return

    images = sorted(glob.glob(os.path.join(verify_dir, "*.jpg")))
    if not images:
        print(f"[IMAGE SYNC] No images to sync.")
        return

    print(f"[IMAGE SYNC] Found {len(images)} images to upload")

    try:
        service = connect_to_drive()
    except Exception as e:
        print(f"[ERROR] Could not connect to Google Drive: {e}")
        return

    # Folder structure: BeeTracker Images / Site A / 2026-06-16 /
    try:
        site_id = get_or_create_folder(service, f"Site {site}", parent_id=DRIVE_FOLDER_ID)
        date_id = get_or_create_folder(service, today, parent_id=site_id)
    except Exception as e:
        print(f"[ERROR] Could not create Drive folder structure: {e}")
        return

    uploaded = 0
    skipped  = 0
    failed   = 0
    deleted  = 0

    for filepath in images:
        filename = os.path.basename(filepath)
        try:
            if file_exists_in_drive(service, filename, date_id):
                skipped += 1
                os.remove(filepath)
                deleted += 1
                continue

            upload_image(service, filepath, filename, date_id)
            uploaded += 1
            os.remove(filepath)
            deleted += 1

        except Exception as e:
            print(f"  [WARN] Failed to upload {filename}: {e}")
            failed += 1

    print(f"\n[IMAGE SYNC] Complete")
    print(f"  Uploaded : {uploaded}")
    print(f"  Skipped  : {skipped} (already in Drive)")
    print(f"  Deleted  : {deleted} from SD card")
    if failed:
        print(f"  Failed   : {failed} (left on SD card for retry)")
    print(f"  Drive path: BeeTracker Images / Site {site} / {today}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync verification images to Google Drive")
    parser.add_argument("--site", required=True, help="Site identifier e.g. A, B, C...")
    args = parser.parse_args()
    sync_images(args.site)
