FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5843
# Must stay at 1 worker: detect_tags.py's background scanner thread starts
# once per worker process (see wsgi.py) — a second worker would double-scan
# and double-log every detection to the Sheet.
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:5843", "--timeout", "60", "wsgi:app"]
