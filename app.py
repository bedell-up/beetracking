from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request
import logging

import data
import groups as _groups
import budget as _budget
import overview as _overview
from config import PORT, DEBUG, ALL_STATIONS
from bee_gallery import bee_gallery_bp, init_gallery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)

init_gallery(
    app,
    storage_root="/var/www/bee_images",
    stations_file="/etc/beecam/stations.json",
)
app.register_blueprint(bee_gallery_bp)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ── pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    g1, g2 = _groups.load()
    return render_template(
        "dashboard.html",
        group1=g1,
        group2=g2,
        all_stations=ALL_STATIONS,
    )


@app.route("/parts")
def parts():
    return render_template("parts.html")


@app.route("/budget")
def budget():
    return render_template("budget.html")


@app.route("/overview")
def overview():
    return render_template("overview.html", data=_overview.load())


@app.route("/api/overview", methods=["GET", "POST"])
def api_overview():
    if request.method == "POST":
        _overview.save(request.get_json(force=True))
        return jsonify({"ok": True})
    return jsonify(_overview.load())


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify(data.cache_info())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    result = data.refresh()
    return jsonify({"result": result, **data.cache_info()})


@app.route("/api/stations")
def api_stations():
    start = _parse_dt(request.args.get("start"))
    end   = _parse_dt(request.args.get("end"))
    g1, g2 = _groups.load()
    return jsonify({
        "counts": data.station_counts(start, end),
        "status": data.station_status(),
        "group1": g1,
        "group2": g2,
    })


@app.route("/api/groups", methods=["GET"])
def api_groups_get():
    g1, g2 = _groups.load()
    assigned = set(g1) | set(g2)
    unassigned = [s for s in ALL_STATIONS if s not in assigned]
    return jsonify({"group1": g1, "group2": g2, "unassigned": unassigned, "all_stations": list(ALL_STATIONS)})


@app.route("/api/groups", methods=["POST"])
def api_groups_save():
    body = request.get_json(force=True)
    valid = set(ALL_STATIONS)
    g1 = sorted({s.upper() for s in body.get("group1", []) if s.upper() in valid})
    g2 = sorted({s.upper() for s in body.get("group2", []) if s.upper() in valid})
    _groups.save(g1, g2)
    return jsonify({"ok": True, "group1": g1, "group2": g2})


@app.route("/api/budget", methods=["GET"])
def api_budget_get():
    return jsonify(_budget.load())


@app.route("/api/budget", methods=["POST"])
def api_budget_save():
    body = request.get_json(force=True)
    _budget.save(body)
    return jsonify({"ok": True})


@app.route("/api/timeseries")
def api_timeseries():
    start       = _parse_dt(request.args.get("start"))
    end         = _parse_dt(request.args.get("end"))
    granularity = request.args.get("granularity", "day")
    if granularity not in ("hour", "day"):
        granularity = "day"
    return jsonify(data.time_series(start, end, granularity))


@app.route("/api/movements")
def api_movements():
    start = _parse_dt(request.args.get("start"))
    end   = _parse_dt(request.args.get("end"))
    return jsonify(data.cross_campus_movements(start, end))


@app.route("/api/recent")
def api_recent():
    n = min(int(request.args.get("n", 50)), 200)
    return jsonify(data.recent_detections(n))


if __name__ == "__main__":
    data.refresh()   # warm the cache on startup
    app.run(host="127.0.0.1", port=PORT, debug=DEBUG)
