import json, os
from config import GROUP_1, GROUP_2

_FILE = os.path.join(os.path.dirname(__file__), "groups_config.json")

def load():
    """Return (group1, group2) lists from file, falling back to config defaults."""
    if os.path.exists(_FILE):
        try:
            with open(_FILE) as f:
                d = json.load(f)
            return list(d.get("group1", [])), list(d.get("group2", []))
        except Exception:
            pass
    return list(GROUP_1), list(GROUP_2)

def save(group1: list, group2: list):
    with open(_FILE, "w") as f:
        json.dump({"group1": sorted(group1), "group2": sorted(group2)}, f, indent=2)
