import json, os

_FILE = os.path.join(os.path.dirname(__file__), "overview_data.json")

def load():
    if os.path.exists(_FILE):
        try:
            with open(_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save(data: dict):
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)
