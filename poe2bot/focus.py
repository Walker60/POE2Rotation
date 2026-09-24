"""Finds/queries the target game window: which process it is, whether it
currently has OS focus, and its client-area rect in absolute screen pixels.

Two platform backends live side by side in this one module (selected once,
at import time, via _IS_WINDOWS) rather than as separate files, so every
caller (executor.py, gui/calibration.py) keeps importing exactly the same
three functions (reset_process_cache/is_game_focused/game_window_client_rect)
regardless of platform:

- Windows: raw ctypes.windll.user32 calls -- unchanged from before Linux
  support existed, see the Win32 section below for the original rationale
  comments.
- Linux (Steam Deck, X11 session only -- see README's Steam Deck section):
  python-xlib + EWMH conventions (_NET_ACTIVE_WINDOW/_NET_CLIENT_LIST/
  _NET_WM_PID/_NET_WM_STATE), which is the direct X11 analogue of
  EnumWindows+GetWindowThreadProcessId+GetClientRect+ClientToScreen. This
  is UNVERIFIED against a real Proton-hosted game window -- see the Steam
  Deck section of README.md for what to check first.
"""
import sys
import threading

import psutil

from poe2bot import config
from poe2bot.log_setup import get_logger
from poe2bot.warn_once import WarnOnce

log = get_logger()

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

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

    # GetClientRect alone only gives the client area's SIZE, always relative to its own
    # (0, 0) -- ClientToScreen is what maps that origin to an absolute virtual-desktop
    # position, which is what a calibrated pixel_pos/region is actually expressed in (see
    # game_window_client_rect). Needed so a calibration done on a secondary monitor (or
    # any window not sitting at the desktop's own (0, 0)) rescales correctly instead of
    # silently assuming the window's client area starts at the desktop origin.
    _client_to_screen = ctypes.windll.user32.ClientToScreen
    _client_to_screen.restype = wintypes.BOOL
    _client_to_screen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _enum_windows = ctypes.windll.user32.EnumWindows
    _enum_windows.restype = wintypes.BOOL
    _enum_windows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
else:
    # Soft/lazy: python-xlib is a Linux-only extra (see requirements.txt),
    # never installed/importable on Windows, and this whole module must
    # still import cleanly even if it's missing on Linux too -- callers
    # degrade to "not focused"/None (via _x11_available()'s one-time
    # warning) rather than crashing the app at startup.
    try:
        from Xlib import X, display
        from Xlib.error import XError
    except ImportError as e:
        X = None
        display = None
        XError = Exception
        _xlib_import_error = e
    else:
        _xlib_import_error = None

    # One Xlib Display connection per thread, created lazily -- Xlib connections
    # aren't safe to share across threads without extra locking, and this module
    # is polled concurrently from the GUI thread and from every running
    # RotationRunner's own thread (see executor.py) -- mirrors executor.py's
    # _screen_capture()'s "one mss instance per thread" pattern.
    _thread_local = threading.local()

_cached_pid = None
_no_process_warning = WarnOnce()
_no_window_warning = WarnOnce()
_no_xlib_warning = WarnOnce()


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
    pid = _game_pid()
    if not pid:
        _no_process_warning.warn_once(lambda: log.warning(
            f"game process '{config.GAME_PROCESS_NAME}' not found -- check Task Manager > "
            f"Details for the real executable name and set POE2BOT_TARGET_PROCESS if it "
            f"differs (this is a common mismatch across storefronts/versions)"))
        return False
    _no_process_warning.clear()

    return _is_game_focused_win32(pid) if _IS_WINDOWS else _is_game_focused_x11(pid)


def _is_game_focused_win32(pid) -> bool:
    hwnd = _get_foreground_window()
    fg_pid = wintypes.DWORD(0)
    _get_window_thread_process_id(hwnd, ctypes.byref(fg_pid))
    focused = fg_pid.value == pid
    if not focused:
        log.debug(f"foreground window belongs to pid={fg_pid.value}, game pid={pid} -- not focused")
    return focused


def _is_game_focused_x11(pid) -> bool:
    if not _x11_available():
        return False
    try:
        d = _x_display()
        active = _x11_active_window(d)
        if active is None:
            return False
        focused_pid = _x11_window_pid(d, active)
        if focused_pid != pid:
            log.debug(f"foreground window belongs to pid={focused_pid}, game pid={pid} -- not focused")
            return False
        return True
    except Exception as e:
        log.debug(f"X11 focus check failed ({type(e).__name__}: {e}); will reconnect on next check")
        _thread_local.display = None
        return False


def _client_rect_if_valid(hwnd, pid):
    """(left, top, width, height) of hwnd's client area, in absolute
    virtual-desktop pixels, if it's a visible, non-minimized top-level
    window actually owned by `pid` with a sane (nonzero) size -- else None.
    Shared by game_window_client_rect()'s focused-window fast path and its
    EnumWindows fallback below, so both apply exactly the same "is this
    actually usable" checks. Minimized is excluded because IsWindowVisible
    alone stays true for a minimized window, whose reported client rect
    isn't a meaningful screen size.

    `left`/`top` come from ClientToScreen, not just GetClientRect (which
    only ever reports a rect relative to its own (0, 0)) -- a calibrated
    pixel_pos/region is stored in absolute screen pixels (see
    overlays.py), so rescaling it needs to know where the client area
    actually sits on screen, not just how big it is. Without this, a
    window that isn't sitting at the desktop's own (0, 0) -- e.g. one on a
    secondary monitor, or in windowed mode -- would rescale against the
    wrong reference frame."""
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
    origin = wintypes.POINT(0, 0)
    if not _client_to_screen(hwnd, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, width, height


def game_window_client_rect():
    """(left, top, width, height) of the configured game process's main
    window client area (i.e. its actual rendering surface, excluding the
    title bar/borders), in absolute virtual-desktop pixels -- the
    reference frame poe2bot/scaling.py rescales a calibrated condition
    against so it still lines up after moving to a different monitor/
    computer at a different resolution or aspect ratio, or after simply
    being repositioned. None if the game process can't be found, or has no
    suitably-sized visible top-level window (e.g. it's still loading).

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
    pid = _game_pid()
    if not pid:
        return None

    rect = _game_window_client_rect_win32(pid) if _IS_WINDOWS else _game_window_client_rect_x11(pid)

    if rect is None:
        _no_window_warning.warn_once(lambda: log.warning(
            f"game process '{config.GAME_PROCESS_NAME}' found (pid={pid}) but no suitably-sized "
            f"visible window -- calibrated conditions won't be rescaled for this screen until "
            f"one is found (expected while the game is still loading, otherwise check it isn't "
            f"minimized)"))
    else:
        _no_window_warning.clear()
    return rect


def _game_window_client_rect_win32(pid):
    focused = _client_rect_if_valid(_get_foreground_window(), pid)
    if focused is not None:
        return focused

    best = None

    def callback(hwnd, _lparam):
        nonlocal best
        rect = _client_rect_if_valid(hwnd, pid)
        if rect is not None and (best is None or rect[2] * rect[3] > best[2] * best[3]):
            best = rect
        return True

    _enum_windows(_WNDENUMPROC(callback), 0)
    return best


# ---- Linux/X11 backend -----------------------------------------------------
# EWMH (Extended Window Manager Hints) equivalents of the Win32 calls above:
# _NET_ACTIVE_WINDOW/_NET_CLIENT_LIST replace GetForegroundWindow/EnumWindows,
# _NET_WM_PID replaces GetWindowThreadProcessId, get_geometry()+
# translate_coords() together replace GetClientRect+ClientToScreen, and
# _NET_WM_STATE's "hidden" bit (plus the raw map_state) replaces IsIconic/
# IsWindowVisible. Requires an EWMH-compliant window manager (KDE Plasma, as
# used by SteamOS Desktop Mode, qualifies) and an actual X11 session --
# there is no Wayland equivalent implemented here (see README's Steam Deck
# section for why: global window queries like this are deliberately
# restricted for unprivileged apps under Wayland).

def _x11_available() -> bool:
    if display is None:
        _no_xlib_warning.warn_once(lambda: log.warning(
            "python-xlib is not installed -- game window detection is disabled. "
            "Install it with `pip install python-xlib` (see README's Steam Deck section). "
            f"Import error: {_xlib_import_error}"))
        return False
    return True


def _x_display():
    d = getattr(_thread_local, "display", None)
    if d is None:
        d = display.Display()
        _thread_local.display = d
    return d


def _x11_get_property(d, window, atom_name):
    try:
        prop = window.get_full_property(d.intern_atom(atom_name), X.AnyPropertyType)
    except XError:
        return None
    return prop.value if prop else None


def _x11_window_pid(d, window):
    value = _x11_get_property(d, window, "_NET_WM_PID")
    return value[0] if value else None


def _x11_window_is_hidden(d, window) -> bool:
    """True if the window manager currently reports this window as
    "hidden" (its _NET_WM_STATE_HIDDEN bit is set) -- the EWMH equivalent
    of IsIconic. Most modern window managers, minimizing a window sets
    this state rather than unmapping it, so map_state alone (see
    _x11_window_is_viewable) wouldn't catch it."""
    value = _x11_get_property(d, window, "_NET_WM_STATE")
    if not value:
        return False
    return d.intern_atom("_NET_WM_STATE_HIDDEN") in value


def _x11_window_is_viewable(window) -> bool:
    try:
        return window.get_attributes().map_state == X.IsViewable
    except XError:
        return False


def _x11_active_window(d):
    root = d.screen().root
    value = _x11_get_property(d, root, "_NET_ACTIVE_WINDOW")
    if not value or not value[0]:
        return None
    return d.create_resource_object("window", value[0])


def _x11_client_list(d):
    root = d.screen().root
    value = _x11_get_property(d, root, "_NET_CLIENT_LIST")
    if not value:
        return []
    return [d.create_resource_object("window", xid) for xid in value]


def _client_rect_if_valid_x11(d, window, pid):
    """X11 counterpart to _client_rect_if_valid -- same contract (None
    unless `window` is a visible, non-minimized, sanely-sized top-level
    window actually owned by `pid`), built from EWMH properties + raw X11
    geometry instead of Win32 calls."""
    if window is None:
        return None
    if not _x11_window_is_viewable(window) or _x11_window_is_hidden(d, window):
        return None
    if _x11_window_pid(d, window) != pid:
        return None
    try:
        geom = window.get_geometry()
    except XError:
        return None
    width, height = geom.width, geom.height
    if width <= 0 or height <= 0:
        return None
    try:
        # translate_coords maps a point in THIS window's own coordinate
        # space into the root window's space -- i.e. exactly this window's
        # absolute on-screen origin, the X11 analogue of ClientToScreen.
        coords = window.translate_coords(d.screen().root, 0, 0)
    except XError:
        return None
    return coords.x, coords.y, width, height


def _game_window_client_rect_x11(pid):
    if not _x11_available():
        return None
    try:
        d = _x_display()
        focused = _client_rect_if_valid_x11(d, _x11_active_window(d), pid)
        if focused is not None:
            return focused

        best = None
        for window in _x11_client_list(d):
            rect = _client_rect_if_valid_x11(d, window, pid)
            if rect is not None and (best is None or rect[2] * rect[3] > best[2] * best[3]):
                best = rect
        return best
    except Exception as e:
        log.debug(f"X11 window-rect lookup failed ({type(e).__name__}: {e}); will reconnect on next check")
        _thread_local.display = None
        return None
