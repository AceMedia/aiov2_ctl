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


def _private_new_file(folder, stem, suffix=".json", tries=1000):
    """Open a new file in `folder` that no one else can read (0600), never one that already exists: the first
    free name of stem+suffix, stem-1+suffix, ... Returns (path, file descriptor)."""
    for n in range(tries):
        path = os.path.join(folder, f"{stem}-{n}{suffix}" if n else f"{stem}{suffix}")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.fchmod(fd, 0o600)
        return path, fd
    raise FileExistsError(folder)


def export_telemetry_snapshot(snapshot, local_position=None, folder=None):
    """Write the mesh snapshot to a new JSON file, private to this user, and return its path (None on failure).
    The folder is made 0700 if it is created here; an existing folder is left as it is."""
    if not isinstance(snapshot, dict):
        return None

    payload = {
        "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "local_position": local_position if isinstance(local_position, dict) else None,
        "telemetry": snapshot,
    }
    folder = folder or EXPORTS_DIR
    stem = f"mesh-telemetry-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"

    try:
        if not os.path.isdir(folder):
            os.makedirs(folder, mode=0o700, exist_ok=True)
            os.chmod(folder, 0o700)
        path, fd = _private_new_file(folder, stem)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        return None

    return path
