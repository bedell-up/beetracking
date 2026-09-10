from app import app, data
import detect_tags

# warm cache before first request
data.refresh()

# v2: AprilTag identification now happens server-side, not on the Pi —
# see detect_tags.py. Runs once per worker process, which is why the
# Dockerfile pins gunicorn to a single worker.
detect_tags.start_background_scanner()

if __name__ == "__main__":
    app.run()
