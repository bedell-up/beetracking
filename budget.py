import json, os, uuid

_FILE = os.path.join(os.path.dirname(__file__), "budget_data.json")

def load():
    with open(_FILE) as f:
        return json.load(f)

def save(data):
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)

def new_id():
    return uuid.uuid4().hex[:8]
