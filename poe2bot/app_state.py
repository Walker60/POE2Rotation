"""Small persisted app-level preferences -- currently just the Active Folder
(which rotations' hotkeys are currently live) and Active Device (keyboard
or controller) selections, so switching either survives closing and
reopening the bot instead of resetting every launch. Deliberately separate
from poe2bot/storage.py's per-rotation JSON files -- this is one small file
of app-wide state, not rotation data."""
import json
import os

from poe2bot import config

STATE_PATH = os.path.join(config.BASE_DIR, "app_state.json")

_VALID_DEVICES = ("keyboard", "controller")
_DEFAULT_STATE = {"active_folder": None, "active_device": "keyboard"}


def load_state() -> dict:
    """Always returns a dict with both keys present and valid, defaulting
    safely on a missing or corrupt file rather than raising -- this runs
    during App.__init__, before there's any error-dialog machinery set up
    for it, so a bad state file must never block startup."""
    if not os.path.isfile(STATE_PATH):
        return dict(_DEFAULT_STATE)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(_DEFAULT_STATE)
    active_folder = data.get("active_folder")
    if not isinstance(active_folder, str):
        active_folder = None
    active_device = data.get("active_device")
    if active_device not in _VALID_DEVICES:
        active_device = "keyboard"
    return {"active_folder": active_folder, "active_device": active_device}


def save_state(active_folder, active_device: str) -> None:
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"active_folder": active_folder, "active_device": active_device}, f, indent=2)
    os.replace(tmp_path, STATE_PATH)
