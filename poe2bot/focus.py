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

_is_iconic = ctypes.windll.user32.IsIconic
_is_iconic.restype = wintypes.BOOL
_is_iconic.argtypes = [wintypes.HWND]

_get_client_rect = ctypes.windll.user32.GetClientRect
_get_client_rect.restype = wintypes.BOOL
_get_client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_enum_windows = ctypes.windll.user32.EnumWindows
_enum_windows.restype = wintypes.BOOL
_enum_windows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]

_cached_pid = None
_warned_no_process = False
_warned_no_window = False


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


def _client_size_if_valid(hwnd, pid):
    """(width, height) of hwnd's client area if it's a visible, non-
    minimized top-level window actually owned by `pid` with a sane
    (nonzero) size -- else None. Shared by game_window_client_size()'s
    focused-window fast path and its EnumWindows fallback below, so both
    apply exactly the same "is this actually usable" checks. Minimized is
    excluded because IsWindowVisible alone stays true for a minimized
    window, whose reported client rect isn't a meaningful screen size."""
    if not hwnd or not _is_window_visible(hwnd) or _is_iconic(hwnd):
        return None
    owner_pid = wintypes.DWORD(0)
    _get_window_thread_process_id(hwnd, ctypes.byref(owner_pid))
    if owner_pid.value != pid:
        return None
    rect = wintypes.RECT()
    if not _get_client_rect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return width, height


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
    the exact instant a screenshot is taken.

    Prefers whichever window currently has OS focus, if it's the game's --
    the most direct available signal of "this is the window actually being
    looked at right now," and immune to a same-process launcher/overlay
    window ever being mistaken for the main one the way a pure size-based
    heuristic could be. Only when the game isn't currently focused (as at
    calibration time, per above) does this fall back to enumerating every
    top-level window and keeping whichever visible, non-minimized one
    (owned by the game's pid) has the largest client area."""
    global _warned_no_window
    pid = _game_pid()
    if not pid:
        return None

    focused = _client_size_if_valid(_get_foreground_window(), pid)
    if focused is not None:
        _warned_no_window = False
        return focused

    best = None

    def callback(hwnd, _lparam):
        nonlocal best
        size = _client_size_if_valid(hwnd, pid)
        if size is not None and (best is None or size[0] * size[1] > best[0] * best[1]):
            best = size
        return True

    _enum_windows(_WNDENUMPROC(callback), 0)
    if best is None:
        if not _warned_no_window:
            log.warning(
                f"game process '{config.GAME_PROCESS_NAME}' found (pid={pid}) but no suitably-sized "
                f"visible window -- calibrated conditions won't be rescaled for this screen until "
                f"one is found (expected while the game is still loading, otherwise check it isn't "
                f"minimized)")
            _warned_no_window = True
    else:
        _warned_no_window = False
    return best
