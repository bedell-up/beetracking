"""
Gspread data layer with in-memory cache.
All public functions return plain dicts/lists safe to json.dumps().
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import gspread
from google.oauth2.service_account import Credentials

import groups as _groups

from config import (
    ACTIVE_WINDOW_HOURS,
    ALL_STATIONS,
    CACHE_TTL,
    COL_DATE,
    COL_STATION,
    COL_TAG_ID,
    COL_TIME,
    COL_UNIX,
    CREDENTIALS_PATH,
    SPREADSHEET_NAME,
)

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ── in-memory cache ──────────────────────────────────────────────────────────
_lock = threading.Lock()
_cache: dict = {"rows": [], "fetched_at": None, "error": None}


def _open_spreadsheet():
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=_SCOPES)
    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME)


def _parse_rows(raw: list[dict]) -> list[dict]:
    """Normalise and type-cast every row from the sheet."""
    rows = []
    for r in raw:
        # ── timestamp: prefer unix epoch, fall back to date+time strings ──
        ts = None
        unix_raw = str(r.get(COL_UNIX, "")).strip()
        if unix_raw:
            try:
                unix_val = float(unix_raw)
                # handle milliseconds (values > year 2100 in seconds)
                if unix_val > 4_102_444_800:
                    unix_val /= 1000
                ts = datetime.fromtimestamp(unix_val, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        if ts is None:
            date_raw = str(r.get(COL_DATE, "")).strip()
            time_raw = str(r.get(COL_TIME, "")).strip()
            try:
                ts = datetime.fromisoformat(f"{date_raw} {time_raw}")
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue  # skip unparse-able rows

        station = str(r.get(COL_STATION, "")).strip().upper()
        if station not in ALL_STATIONS:
            continue

        tag_id = str(r.get(COL_TAG_ID, "")).strip()
        if not tag_id:
            continue

        rows.append(
            {
                "ts": ts,
                "ts_iso": ts.isoformat(),
                "tag_id": tag_id,
                "station": station,
            }
        )
    # sort ascending so slices are chronological
    rows.sort(key=lambda x: x["ts"])
    return rows


def refresh() -> str:
    """Pull fresh data from all worksheet tabs. Returns 'ok' or an error string."""
    try:
        spreadsheet = _open_spreadsheet()
        raw = []
        for ws in spreadsheet.worksheets():
            raw.extend(ws.get_all_records())
        rows = _parse_rows(raw)
        with _lock:
            _cache["rows"] = rows
            _cache["fetched_at"] = datetime.now(timezone.utc)
            _cache["error"] = None
        log.info("Sheet refreshed: %d rows across %d tabs", len(rows), len(spreadsheet.worksheets()))
        return "ok"
    except Exception as exc:
        msg = str(exc)
        log.error("Sheet refresh failed: %s", msg)
        with _lock:
            _cache["error"] = msg
        return msg


def _get_rows(start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    with _lock:
        fetched_at = _cache["fetched_at"]
        rows = list(_cache["rows"])

    if fetched_at is None or (datetime.now(timezone.utc) - fetched_at).total_seconds() > CACHE_TTL:
        refresh()
        with _lock:
            rows = list(_cache["rows"])

    if start:
        rows = [r for r in rows if r["ts"] >= start]
    if end:
        rows = [r for r in rows if r["ts"] <= end]
    return rows


# ── public query functions ───────────────────────────────────────────────────

def station_counts(start: datetime | None = None, end: datetime | None = None) -> dict:
    rows = _get_rows(start, end)
    counts = {s: 0 for s in ALL_STATIONS}
    for r in rows:
        counts[r["station"]] += 1
    return counts


def time_series(
    start: datetime | None = None,
    end: datetime | None = None,
    granularity: str = "day",   # "hour" | "day"
) -> list[dict]:
    """Return [{label, count}, …] bucketed by day or hour."""
    rows = _get_rows(start, end)
    buckets: dict[str, int] = {}
    for r in rows:
        if granularity == "hour":
            key = r["ts"].strftime("%Y-%m-%d %H:00")
        else:
            key = r["ts"].strftime("%Y-%m-%d")
        buckets[key] = buckets.get(key, 0) + 1
    return [{"label": k, "count": v} for k, v in sorted(buckets.items())]


def cross_campus_movements(
    start: datetime | None = None, end: datetime | None = None
) -> list[dict]:
    """Find every tag_id seen in BOTH configured groups."""
    group1, group2 = _groups.load()
    rows = _get_rows(start, end)
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag_id"], []).append(r)

    results = []
    for tag_id, events in by_tag.items():
        stations_seen = {e["station"] for e in events}
        in_g1 = stations_seen & set(group1)
        in_g2 = stations_seen & set(group2)
        if not in_g1 or not in_g2:
            continue

        sorted_events = sorted(events, key=lambda x: x["ts"])
        # build a compact movement trail
        trail = [
            {"ts_iso": e["ts_iso"], "station": e["station"]}
            for e in sorted_events
        ]
        first_ts = sorted_events[0]["ts_iso"]
        last_ts  = sorted_events[-1]["ts_iso"]

        results.append(
            {
                "tag_id": tag_id,
                "total_detections": len(events),
                "group1_stations": sorted(in_g1),
                "group2_stations": sorted(in_g2),
                "first_seen": first_ts,
                "last_seen": last_ts,
                "trail": trail,
            }
        )

    results.sort(key=lambda x: x["total_detections"], reverse=True)
    return results


def recent_detections(n: int = 50) -> list[dict]:
    with _lock:
        rows = list(_cache["rows"])
    return [
        {"ts_iso": r["ts_iso"], "tag_id": r["tag_id"], "station": r["station"]}
        for r in rows[-n:][::-1]
    ]


def station_status() -> dict:
    """Return {station: "active"|"inactive"} based on ACTIVE_WINDOW_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ACTIVE_WINDOW_HOURS)
    with _lock:
        rows = list(_cache["rows"])
    latest: dict[str, datetime] = {}
    for r in rows:
        if r["station"] not in latest or r["ts"] > latest[r["station"]]:
            latest[r["station"]] = r["ts"]
    status = {}
    for s in ALL_STATIONS:
        last = latest.get(s)
        status[s] = {
            "state": "active" if (last and last >= cutoff) else "inactive",
            "last_seen": last.isoformat() if last else None,
        }
    return status


def cache_info() -> dict:
    with _lock:
        return {
            "row_count": len(_cache["rows"]),
            "fetched_at": _cache["fetched_at"].isoformat() if _cache["fetched_at"] else None,
            "error": _cache["error"],
        }
