import ctypes
from ctypes import wintypes

import psutil

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()

# GetForegroundWindow/GetWindowThreadProcessId MUST have explicit restype/argtypes.
# Without them, ctypes assumes a plain 32-bit signed `int` return, but HWND is a
# pointer-sized handle -- on 64-bit Windows, any handle whose value has its high bit
# set gets misread as a *negative* Python int, which then corrupts the PID lookup in
# GetWindowThreadProcessId and makes is_game_focused() intermittently return False
# even while the target window genuinely is in the foreground.
_get_foreground_window = ctypes.windll.user32.GetForegroundWindow
_get_foreground_window.restype = wintypes.HWND
_get_foreground_window.argtypes = []

_get_window_thread_process_id = ctypes.windll.user32.GetWindowThreadProcessId
_get_window_thread_process_id.restype = wintypes.DWORD
_get_window_thread_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

_cached_pid = None
_warned_no_process = False


def _game_pid():
    global _cached_pid
    if _cached_pid is None or not psutil.pid_exists(_cached_pid):
        _cached_pid = None
        for proc in psutil.process_iter(["pid", "name"]):
            if (proc.info["name"] or "").lower() == config.GAME_PROCESS_NAME.lower():
                _cached_pid = proc.info["pid"]
                log.debug(f"found game process '{config.GAME_PROCESS_NAME}' at pid={_cached_pid}")
                break
    return _cached_pid


def is_game_focused() -> bool:
    """True if the configured game process currently has OS foreground focus."""
    global _warned_no_process
    pid = _game_pid()
    if not pid:
        if not _warned_no_process:
            log.warning(
                f"game process '{config.GAME_PROCESS_NAME}' not found -- check Task Manager > "
                f"Details for the real executable name and set POE2BOT_TARGET_PROCESS if it "
                f"differs (this is a common mismatch across storefronts/versions)")
            _warned_no_process = True
        return False
    _warned_no_process = False

    hwnd = _get_foreground_window()
    fg_pid = wintypes.DWORD(0)
    _get_window_thread_process_id(hwnd, ctypes.byref(fg_pid))
    focused = fg_pid.value == pid
    if not focused:
        log.debug(f"foreground window belongs to pid={fg_pid.value}, game pid={pid} -- not focused")
    return focused
