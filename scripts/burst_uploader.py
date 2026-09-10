#!/usr/bin/env python3
"""
Bee Tracker — Burst Uploader (Pi-side)
=========================================
Runs continuously on a bee-cam Pi, independently of capture.py. Watches
data/bursts/site_X/<timestamp>/ for frames capture.py has saved and
uploads every one of them, full quality, to the central gallery on the
VPS (bee_gallery.py's /api/upload/full endpoint) — unlike the old
uploader.py, there's no thumbnail-first/human-requests-full-res flow here,
because the server needs every frame to run AprilTag detection on, not
just the ones a person happened to click on in a gallery UI.

Frame filenames are namespaced with their burst folder (e.g.
"20260910_221530__frame_00.png") before upload, since capture.py reuses
frame_00.png, frame_01.png, ... in every burst and the server indexes
images by (station, filename) — without the burst timestamp prefix,
frames from different bursts would collide and overwrite each other.

Once a frame is confirmed uploaded, it's deleted locally (and the burst
folder removed if it's now empty) — the Pi is not meant to be long-term
storage, and this is what actually prevents the SD card from filling up
over a season of motion-triggered bursts.

State (what's been uploaded, retry counts) lives in a local sqlite file
in the data dir, so a Pi reboot or network drop just picks up where it
left off — nothing is re-uploaded or lost.

Usage:
    pip install requests
    python3 scripts/burst_uploader.py --config config.ini

Reuses the same config.ini as the old uploader.py (station_id, api_key,
server_url — image_dir is ignored, the burst directory is always
data/bursts/site_<station_id>).

Intended to run under systemd (see beecam-burst-uploader.service) so it
restarts automatically and runs forever, not as a cron job.
"""

import argparse
import configparser
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("burst_uploader")

EXTS = {".png", ".dng", ".jpg", ".jpeg"}
POLL_INTERVAL = 15            # seconds between scan cycles — bursts should go out promptly
REQUEST_TIMEOUT = 60          # raw/PNG full-res files are bigger than the old thumbnail uploads
MAX_BACKOFF = 3600            # cap retry backoff at 1 hour

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")


def backoff_seconds(attempts: int) -> int:
    return min(60 * (2 ** attempts), MAX_BACKOFF)


class State:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                relpath TEXT PRIMARY KEY,
                mtime REAL,
                uploaded INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                last_attempt REAL DEFAULT 0
            )
        """)
        self.conn.commit()

    def track_new(self, relpath: str, mtime: float):
        self.conn.execute(
            "INSERT OR IGNORE INTO files (relpath, mtime) VALUES (?, ?)",
            (relpath, mtime),
        )
        self.conn.commit()

    def pending(self):
        now = time.time()
        rows = self.conn.execute(
            "SELECT relpath, attempts, last_attempt FROM files WHERE uploaded = 0"
        ).fetchall()
        return [r for r in rows if now - r[2] >= backoff_seconds(r[1])]

    def mark_success(self, relpath):
        self.conn.execute("UPDATE files SET uploaded = 1 WHERE relpath = ?", (relpath,))
        self.conn.commit()

    def mark_failure(self, relpath):
        self.conn.execute(
            "UPDATE files SET attempts = attempts + 1, last_attempt = ? WHERE relpath = ?",
            (time.time(), relpath),
        )
        self.conn.commit()

    def forget(self, relpath):
        """Drop a row once the file is deleted from disk, so a future file
        that happens to reuse the same relpath isn't skipped as 'already done'."""
        self.conn.execute("DELETE FROM files WHERE relpath = ?", (relpath,))
        self.conn.commit()


def upload_filename_for(relpath: Path) -> str:
    """<burst_folder>__<frame_file>, e.g. 20260910_221530__frame_00.png —
    keeps frames from different bursts unique on the server."""
    burst_folder = relpath.parent.name
    return f"{burst_folder}__{relpath.name}"


def upload(session, url, api_key, station, filename, mtime, file_bytes):
    resp = session.post(
        url,
        headers={"X-API-Key": api_key},
        data={"station": station, "filename": filename, "timestamp": str(mtime)},
        files={"image": (filename, file_bytes)},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp


def run(cfg):
    station = cfg["station_id"]
    api_key = cfg["api_key"]
    burst_root = Path(DATA_DIR) / "bursts" / f"site_{station}"
    burst_root.mkdir(parents=True, exist_ok=True)

    state = State(Path(DATA_DIR) / f".burst_uploader_state_{station}.db")
    session = requests.Session()
    full_url = cfg["server_url"].rstrip("/") + "/api/upload/full"

    log.info("Starting burst uploader for station %s -> %s (watching %s)", station, cfg["server_url"], burst_root)

    while True:
        try:
            # 1. Discover new frames (recursive: data/bursts/site_X/<ts>/frame_NN.ext)
            for p in burst_root.glob("*/*"):
                if p.suffix.lower() in EXTS:
                    relpath = str(p.relative_to(burst_root))
                    state.track_new(relpath, p.stat().st_mtime)

            # 2. Upload everything pending, full quality, no throttling —
            #    the server needs every frame to run detection on.
            for relpath, _, _ in state.pending():
                fpath = burst_root / relpath
                if not fpath.exists():
                    # Already gone (manually cleared, etc.) — stop tracking it.
                    state.forget(relpath)
                    continue
                try:
                    upload_name = upload_filename_for(Path(relpath))
                    file_bytes = fpath.read_bytes()
                    upload(session, full_url, api_key, station, upload_name,
                           fpath.stat().st_mtime, file_bytes)
                    state.mark_success(relpath)
                    log.info("Uploaded: %s", relpath)

                    fpath.unlink()
                    state.forget(relpath)
                    burst_folder = fpath.parent
                    if burst_folder.is_dir() and not any(burst_folder.iterdir()):
                        burst_folder.rmdir()

                except Exception as e:
                    state.mark_failure(relpath)
                    log.warning("Upload failed for %s: %s", relpath, e)

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
