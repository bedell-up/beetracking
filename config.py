import os

# --- Google Sheets ---
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "beetracker_credentials.json")
SPREADSHEET_NAME = "BeeTracker Data"   # exact name of your Google Sheet
WORKSHEET_INDEX  = 0               # 0 = first sheet tab

# --- Column names in your sheet (case-sensitive) ---
COL_TAG_ID        = "tag_id"         # AprilTag identifier
COL_STATION       = "site"           # A–H
COL_UNIX          = "timestamp_unix" # seconds since epoch (primary time source)
COL_DATE          = "date"           # fallback date string
COL_TIME          = "time"           # fallback time string
COL_CONFIDENCE    = "confidence"     # detection confidence score

# --- Station groups ---
GROUP_1 = ["A", "B", "C", "D"]   # one side of campus
GROUP_2 = ["E", "F", "G", "H"]   # other side of campus
ALL_STATIONS = GROUP_1 + GROUP_2

# --- Data cache (seconds) — sheet syncs every 6 h, cache slightly less ---
CACHE_TTL = 21_000  # ~5 h 50 min

# --- "Active" = station seen a detection within this many hours ---
ACTIVE_WINDOW_HOURS = 24

# --- Flask ---
PORT = 5843
DEBUG = False
