#!/usr/bin/env python3
"""
beecam pi-uploader

Runs continuously on a bee-cam Pi. Watches a capture directory and
pushes images to the central gallery at bee.pscapps.com:

  - Thumbnails are generated locally and uploaded immediately (small,
    cheap, gets a preview into the gallery fast).
  - Full-resolution originals are uploaded in the background, throttled
    to a few files per cycle so they don't saturate the IoT link.

State (what's been uploaded, retry counts) lives in a local sqlite file
next to the image directory, so a Pi reboot or network drop just picks
up where it left off — nothing is re-uploaded or lost.

Usage:
    pip install requests pillow
    python3 uploader.py --config config.ini

Intended to run under systemd (see beecam-uploader.service) so it
restarts automatically and runs forever, not as a cron job.
"""

import argparse
import configparser
import logging
import sqlite3
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("uploader")

EXTS = {".jpg", ".jpeg", ".png"}
THUMB_SIZE = (320, 320)
MAX_FULL_PER_CYCLE = 3        # throttle: full-res uploads per loop iteration
POLL_INTERVAL = 30            # seconds between scan cycles
REQUEST_TIMEOUT = 30          # seconds
MAX_BACKOFF = 3600            # cap retry backoff at 1 hour


def backoff_seconds(attempts: int) -> int:
    return min(60 * (2 ** attempts), MAX_BACKOFF)


class State:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                filename TEXT PRIMARY KEY,
                mtime REAL,
                thumb_uploaded INTEGER DEFAULT 0,
                full_uploaded INTEGER DEFAULT 0,
                thumb_attempts INTEGER DEFAULT 0,
                full_attempts INTEGER DEFAULT 0,
                last_thumb_attempt REAL DEFAULT 0,
                last_full_attempt REAL DEFAULT 0
            )
        """)
        self.conn.commit()

    def track_new(self, filename: str, mtime: float):
        self.conn.execute(
            "INSERT OR IGNORE INTO files (filename, mtime) VALUES (?, ?)",
            (filename, mtime),
        )
        self.conn.commit()

    def pending_thumbs(self):
        now = time.time()
        rows = self.conn.execute(
            "SELECT filename, thumb_attempts, last_thumb_attempt FROM files WHERE thumb_uploaded = 0"
        ).fetchall()
        return [r for r in rows if now - r[2] >= backoff_seconds(r[1])]

    def pending_full(self, limit=None):
        now = time.time()
        rows = self.conn.execute(
            "SELECT filename, full_attempts, last_full_attempt FROM files WHERE full_uploaded = 0"
        ).fetchall()
        ready = [r for r in rows if now - r[2] >= backoff_seconds(r[1])]
        return ready if limit is None else ready[:limit]

    def mark_thumb_success(self, filename):
        self.conn.execute(
            "UPDATE files SET thumb_uploaded = 1 WHERE filename = ?", (filename,)
        )
        self.conn.commit()

    def mark_thumb_failure(self, filename):
        self.conn.execute(
            "UPDATE files SET thumb_attempts = thumb_attempts + 1, last_thumb_attempt = ? WHERE filename = ?",
            (time.time(), filename),
        )
        self.conn.commit()

    def mark_full_success(self, filename):
        self.conn.execute(
            "UPDATE files SET full_uploaded = 1 WHERE filename = ?", (filename,)
        )
        self.conn.commit()

    def mark_full_failure(self, filename):
        self.conn.execute(
            "UPDATE files SET full_attempts = full_attempts + 1, last_full_attempt = ? WHERE filename = ?",
            (time.time(), filename),
        )
        self.conn.commit()


def make_thumbnail_bytes(img_path: Path) -> bytes:
    with Image.open(img_path) as im:
        im = im.convert("RGB")
        im.thumbnail(THUMB_SIZE)
        buf = BytesIO()
        im.save(buf, "JPEG", quality=80)
        return buf.getvalue()


def fetch_requested(session, cfg):
    """Ask the server which filenames a human has marked for full-res upload."""
    url = cfg["server_url"].rstrip("/") + f"/api/requests/{cfg['station_id']}"
    try:
        resp = session.get(
            url, headers={"X-API-Key": cfg["api_key"]}, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return set(resp.json().get("filenames", []))
    except Exception as e:
        log.warning("Could not fetch full-res request list: %s", e)
        return set()


def upload(session, url, api_key, station, filename, mtime, file_bytes, field_name="image"):
    resp = session.post(
        url,
        headers={"X-API-Key": api_key},
        data={"station": station, "filename": filename, "timestamp": str(mtime)},
        files={field_name: (filename, file_bytes)},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp


def run(cfg):
    image_dir = Path(cfg["image_dir"]).expanduser().resolve()
    state = State(image_dir / ".uploader_state.db")
    session = requests.Session()

    thumb_url = cfg["server_url"].rstrip("/") + "/api/upload/thumb"
    full_url = cfg["server_url"].rstrip("/") + "/api/upload/full"
    station = cfg["station_id"]
    api_key = cfg["api_key"]

    log.info("Starting uploader for station %s -> %s", station, cfg["server_url"])

    while True:
        try:
            # 1. Discover new files
            for p in image_dir.iterdir():
                if p.suffix.lower() in EXTS:
                    state.track_new(p.name, p.stat().st_mtime)

            # 2. Thumbnails: upload all pending, they're cheap
            for filename, _, _ in state.pending_thumbs():
                fpath = image_dir / filename
                if not fpath.exists():
                    continue
                try:
                    thumb_bytes = make_thumbnail_bytes(fpath)
                    upload(session, thumb_url, api_key, station, filename,
                           fpath.stat().st_mtime, thumb_bytes)
                    state.mark_thumb_success(filename)
                    log.info("Thumbnail uploaded: %s", filename)
                except Exception as e:
                    state.mark_thumb_failure(filename)
                    log.warning("Thumbnail upload failed for %s: %s", filename, e)

            # 3. Full-res: only images a human has marked via the gallery, throttled
            requested = fetch_requested(session, cfg)
            if requested:
                candidates = [r for r in state.pending_full() if r[0] in requested]
                for filename, _, _ in candidates[:MAX_FULL_PER_CYCLE]:
                    fpath = image_dir / filename
                    if not fpath.exists():
                        continue
                    try:
                        full_bytes = fpath.read_bytes()
                        upload(session, full_url, api_key, station, filename,
                               fpath.stat().st_mtime, full_bytes)
                        state.mark_full_success(filename)
                        log.info("Full-res uploaded (requested): %s", filename)
                    except Exception as e:
                        state.mark_full_failure(filename)
                        log.warning("Full-res upload failed for %s: %s", filename, e)

        except Exception as e:
            log.error("Cycle error: %s", e)

        time.sleep(POLL_INTERVAL)


def load_config(path):
    parser = configparser.ConfigParser()
    parser.read(path)
    return parser["uploader"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.ini")
    args = ap.parse_args()
    run(load_config(args.config))
