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

_is_window_visible = ctypes.windll.user32.IsWindowVisible
_is_window_visible.restype = wintypes.BOOL
_is_window_visible.argtypes = [wintypes.HWND]

_get_client_rect = ctypes.windll.user32.GetClientRect
_get_client_rect.restype = wintypes.BOOL
_get_client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_enum_windows = ctypes.windll.user32.EnumWindows
_enum_windows.restype = wintypes.BOOL
_enum_windows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]

_cached_pid = None
_warned_no_process = False


def reset_process_cache():
    """Forces the next is_game_focused() call to re-search for the
    configured game process by name, instead of trusting a cached pid --
    call this after changing config.GAME_PROCESS_NAME at runtime (e.g. from
    Settings), since the cache otherwise only invalidates once the
    previously-found pid stops existing."""
    global _cached_pid
    _cached_pid = None


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


def game_window_client_size():
    """(width, height) of the configured game process's main window client
    area (i.e. its actual rendering surface, excluding the title bar/
    borders) -- the reference frame poe2bot/scaling.py rescales a
    calibrated condition against so it still lines up after moving to a
    different monitor/computer at a different resolution or aspect ratio.
    None if the game process can't be found, or has no suitably-sized
    visible top-level window (e.g. it's still loading).

    Deliberately independent of is_game_focused() -- unlike that check,
    this doesn't require the game to currently have OS foreground focus,
    since calibration hides the bot's own window (and briefly shows a
    capture overlay) rather than necessarily leaving the game focused at
    the exact instant a screenshot is taken. Finds the game's window by
    enumerating every top-level window and keeping whichever visible one
    (owned by the game's pid) has the largest client area -- a simple, but
    effective, heuristic for "the main window" that also tolerates a
    process owning multiple windows (e.g. a small launcher/overlay
    window)."""
    pid = _game_pid()
    if not pid:
        return None
    best = None

    def callback(hwnd, _lparam):
        nonlocal best
        if not _is_window_visible(hwnd):
            return True
        owner_pid = wintypes.DWORD(0)
        _get_window_thread_process_id(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != pid:
            return True
        rect = wintypes.RECT()
        if not _get_client_rect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return True
        if best is None or width * height > best[0] * best[1]:
            best = (width, height)
        return True

    _enum_windows(_WNDENUMPROC(callback), 0)
    return best
