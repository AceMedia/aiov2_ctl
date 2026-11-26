import json
import os
from datetime import datetime


PREFERENCES_DIR = os.path.expanduser("~/.config/ops-agent")
PREFERENCES_PATH = os.path.join(PREFERENCES_DIR, "preferences.json")
EXPORTS_DIR = os.path.join(PREFERENCES_DIR, "exports")


def load_preferences():
    try:
        with open(PREFERENCES_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_preferences(data):
    if not isinstance(data, dict):
        return False
    try:
        os.makedirs(PREFERENCES_DIR, exist_ok=True)
        with open(PREFERENCES_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return True
    except OSError:
        return False


def export_telemetry_snapshot(snapshot, local_position=None):
    if not isinstance(snapshot, dict):
        return None

    payload = {
        "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "local_position": local_position if isinstance(local_position, dict) else None,
        "telemetry": snapshot,
    }
    filename = f"mesh-telemetry-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path = os.path.join(EXPORTS_DIR, filename)

    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        return None

    return path
