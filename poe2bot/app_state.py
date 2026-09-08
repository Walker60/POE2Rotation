"""Small persisted app-level preferences -- the Active Folder (which
rotations' hotkeys are currently live), Active Device (keyboard or
controller), Theme (dark/light), and optional GUI-editable overrides for the
four settings poe2bot/config.py otherwise only reads from environment
variables at startup (target process name, panic key, controller min tap
ms, controller index) -- so any of them survives closing and reopening the
bot instead of resetting every launch. Deliberately separate from
poe2bot/storage.py's per-rotation JSON files -- this is one small file of
app-wide state, not rotation data."""
import json
import os

from poe2bot import config

STATE_PATH = os.path.join(config.BASE_DIR, "app_state.json")

_VALID_DEVICES = ("keyboard", "controller")
_VALID_THEMES = ("dark", "light")
_DEFAULT_STATE = {
    "active_folder": None,
    "active_device": "keyboard",
    "theme": "dark",
    # None for any of these four means "no override saved -- keep using
    # config.py's own env-var-or-builtin default." Only ever non-None once
    # the user has actually changed the corresponding Settings field.
    "game_process_name": None,
    "panic_key": None,
    "controller_min_tap_ms": None,
    "controller_index": None,
}


def load_state() -> dict:
    """Always returns a dict with all keys present and valid, defaulting
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
    theme = data.get("theme")
    if theme not in _VALID_THEMES:
        theme = "dark"
    game_process_name = data.get("game_process_name")
    if not isinstance(game_process_name, str) or not game_process_name.strip():
        game_process_name = None
    panic_key = data.get("panic_key")
    if not isinstance(panic_key, str) or not panic_key.strip():
        panic_key = None
    controller_min_tap_ms = data.get("controller_min_tap_ms")
    if not isinstance(controller_min_tap_ms, int) or isinstance(controller_min_tap_ms, bool):
        controller_min_tap_ms = None
    controller_index = data.get("controller_index")
    if not isinstance(controller_index, int) or isinstance(controller_index, bool):
        controller_index = None
    return {
        "active_folder": active_folder, "active_device": active_device, "theme": theme,
        "game_process_name": game_process_name, "panic_key": panic_key,
        "controller_min_tap_ms": controller_min_tap_ms, "controller_index": controller_index,
    }


def save_state(active_folder, active_device: str, theme: str, game_process_name=None,
                panic_key=None, controller_min_tap_ms=None, controller_index=None) -> None:
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({
            "active_folder": active_folder, "active_device": active_device, "theme": theme,
            "game_process_name": game_process_name, "panic_key": panic_key,
            "controller_min_tap_ms": controller_min_tap_ms, "controller_index": controller_index,
        }, f, indent=2)
    os.replace(tmp_path, STATE_PATH)
