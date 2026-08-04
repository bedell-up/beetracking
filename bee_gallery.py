"""
bee_gallery: central image gallery blueprint for bee.pscapps.com

Register this into the existing Flask app:

    from bee_gallery import bee_gallery_bp, init_gallery
    init_gallery(app, storage_root="/var/www/bee_images",
                 stations_file="/etc/beecam/stations.json")
    app.register_blueprint(bee_gallery_bp)

Storage layout on disk:
    <storage_root>/<station>/thumbs/<date>/<filename>.jpg
    <storage_root>/<station>/full/<date>/<filename>

stations.json maps station id -> API key, e.g.:
    {"A": "...", "B": "...", ...}
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, Flask, abort, current_app, g, jsonify,
    redirect, render_template, request, send_file, url_for
)
from PIL import Image

bee_gallery_bp = Blueprint(
    "bee_gallery", __name__,
    template_folder="templates",
    url_prefix="/gallery",
)

PER_PAGE = 60


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def init_gallery(app: Flask, storage_root: str, stations_file: str):
    app.config["BEE_STORAGE_ROOT"] = Path(storage_root)
    app.config["BEE_STORAGE_ROOT"].mkdir(parents=True, exist_ok=True)

    with open(stations_file) as f:
        app.config["BEE_STATIONS"] = json.load(f)  # {station_id: api_key}

    db_path = app.config["BEE_STORAGE_ROOT"] / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station TEXT NOT NULL,
            filename TEXT NOT NULL,
            date TEXT NOT NULL,
            captured_at REAL,
            has_thumb INTEGER DEFAULT 0,
            has_full INTEGER DEFAULT 0,
            full_requested INTEGER DEFAULT 0,
            updated_at REAL,
            UNIQUE(station, filename)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_station_date ON images(station, date)")
    try:
        conn.execute("ALTER TABLE images ADD COLUMN full_requested INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already present (existing DB from before this column existed)
    conn.commit()
    conn.close()
    app.config["BEE_DB_PATH"] = db_path


def get_db():
    if "bee_db" not in g:
        g.bee_db = sqlite3.connect(str(current_app.config["BEE_DB_PATH"]))
        g.bee_db.row_factory = sqlite3.Row
    return g.bee_db


@bee_gallery_bp.teardown_app_request
def close_db(exc):
    db = g.pop("bee_db", None)
    if db is not None:
        db.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def check_auth(station):
    stations = current_app.config["BEE_STATIONS"]
    api_key = request.headers.get("X-API-Key")
    if station not in stations or api_key != stations[station]:
        abort(401)


def date_dir_for(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def upsert_image(station, filename, date_str, captured_at, has_thumb=None, has_full=None):
    db = get_db()
    row = db.execute(
        "SELECT has_thumb, has_full FROM images WHERE station=? AND filename=?",
        (station, filename),
    ).fetchone()
    new_thumb = has_thumb if has_thumb is not None else (row["has_thumb"] if row else 0)
    new_full = has_full if has_full is not None else (row["has_full"] if row else 0)
    db.execute("""
        INSERT INTO images (station, filename, date, captured_at, has_thumb, has_full, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(station, filename) DO UPDATE SET
            has_thumb = excluded.has_thumb,
            has_full = excluded.has_full,
            updated_at = excluded.updated_at
    """, (station, filename, date_str, captured_at, new_thumb, new_full, time.time()))
    db.commit()


# --------------------------------------------------------------------------
# Upload endpoints (called by the Pi uploaders)
# --------------------------------------------------------------------------

@bee_gallery_bp.route("/api/upload/thumb", methods=["POST"])
def upload_thumb():
    station = request.form.get("station", "")
    filename = request.form.get("filename", "")
    ts = float(request.form.get("timestamp", time.time()))
    check_auth(station)

    if "image" not in request.files:
        abort(400)

    date_str = date_dir_for(ts)
    root = current_app.config["BEE_STORAGE_ROOT"]
    thumb_dir = root / station / "thumbs" / date_str
    thumb_dir.mkdir(parents=True, exist_ok=True)
    dest = thumb_dir / (Path(filename).stem + ".jpg")
    request.files["image"].save(dest)

    upsert_image(station, filename, date_str, ts, has_thumb=1)
    return jsonify({"ok": True})


@bee_gallery_bp.route("/api/upload/full", methods=["POST"])
def upload_full():
    station = request.form.get("station", "")
    filename = request.form.get("filename", "")
    ts = float(request.form.get("timestamp", time.time()))
    check_auth(station)

    if "image" not in request.files:
        abort(400)

    date_str = date_dir_for(ts)
    root = current_app.config["BEE_STORAGE_ROOT"]
    full_dir = root / station / "full" / date_str
    full_dir.mkdir(parents=True, exist_ok=True)
    dest = full_dir / filename
    request.files["image"].save(dest)

    # Regenerate a higher-quality thumbnail from the full-res original now that we have it
    try:
        thumb_dir = root / station / "thumbs" / date_str
        thumb_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(dest) as im:
            im = im.convert("RGB")
            im.thumbnail((320, 320))
            im.save(thumb_dir / (Path(filename).stem + ".jpg"), "JPEG", quality=85)
        has_thumb = 1
    except Exception:
        has_thumb = None  # leave whatever thumb state was already there

    upsert_image(station, filename, date_str, ts, has_thumb=has_thumb, has_full=1)
    return jsonify({"ok": True})


@bee_gallery_bp.route("/api/requests/<station>")
def list_requests(station):
    """Called by the Pi uploader to find out which images to upload full-res for."""
    check_auth(station)
    db = get_db()
    rows = db.execute(
        "SELECT filename FROM images WHERE station = ? AND full_requested = 1 AND has_full = 0",
        (station,),
    ).fetchall()
    return jsonify({"filenames": [r["filename"] for r in rows]})


# --------------------------------------------------------------------------
# Gallery views
# --------------------------------------------------------------------------

@bee_gallery_bp.route("/")
def gallery():
    station_filter = request.args.get("station") or None
    page = max(1, request.args.get("page", 1, type=int))
    db = get_db()

    where = "WHERE station = ?" if station_filter else ""
    params = (station_filter,) if station_filter else ()

    total = db.execute(f"SELECT COUNT(*) c FROM images {where}", params).fetchone()["c"]
    rows = db.execute(f"""
        SELECT * FROM images {where}
        ORDER BY captured_at DESC
        LIMIT ? OFFSET ?
    """, params + (PER_PAGE, (page - 1) * PER_PAGE)).fetchall()

    all_stations = [r["station"] for r in db.execute(
        "SELECT DISTINCT station FROM images ORDER BY station"
    ).fetchall()]

    items = []
    for r in rows:
        items.append({
            "station": r["station"],
            "filename": r["filename"],
            "date": r["date"],
            "timestamp": datetime.fromtimestamp(r["captured_at"]).strftime("%Y-%m-%d %H:%M:%S"),
            "has_full": bool(r["has_full"]),
            "full_requested": bool(r["full_requested"]),
            "thumb_url": url_for("bee_gallery.thumb", station=r["station"], date=r["date"], filename=Path(r["filename"]).stem + ".jpg"),
            "full_url": url_for("bee_gallery.full", station=r["station"], date=r["date"], filename=r["filename"]) if r["has_full"] else None,
            "request_full_url": url_for("bee_gallery.request_full", station=r["station"], filename=r["filename"]),
        })

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    return render_template(
        "bee_gallery.html",
        items=items, page=page, total_pages=total_pages, total=total,
        stations=all_stations, active_station=station_filter,
    )


@bee_gallery_bp.route("/api/request-full/<station>/<path:filename>", methods=["POST"])
def request_full(station, filename):
    """Called from the gallery UI when a human marks a thumb-only image for full-res pickup."""
    db = get_db()
    db.execute(
        "UPDATE images SET full_requested = 1 WHERE station = ? AND filename = ?",
        (station, filename),
    )
    db.commit()
    return redirect(request.referrer or url_for("bee_gallery.gallery"))


@bee_gallery_bp.route("/thumb/<station>/<date>/<path:filename>")
def thumb(station, date, filename):
    root = current_app.config["BEE_STORAGE_ROOT"]
    path = root / station / "thumbs" / date / filename
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg")


@bee_gallery_bp.route("/full/<station>/<date>/<path:filename>")
def full(station, date, filename):
    root = current_app.config["BEE_STORAGE_ROOT"]
    path = root / station / "full" / date / filename
    if not path.is_file():
        abort(404)
    return send_file(path)
