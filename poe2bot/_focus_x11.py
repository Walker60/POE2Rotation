"""Linux/X11 equivalent of _focus_win32.py's is_game_focused(), using EWMH
properties via python-xlib -- the same _NET_ACTIVE_WINDOW/_NET_WM_PID pattern
`wmctrl`/`xdotool` use under the hood. Same contract as the Windows version:
never raise from is_game_focused() itself, when in doubt return False and log
(throttled, not on every ~100ms poll).

Covers X11-native Linux desktops and Wayland desktops running the game via
XWayland (the common case for Wine/Proton, which has no mainstream native-
Wayland backend either) -- not a hypothetical native-Wayland-only game build,
since there's no portable "focused window" API for that at all.
"""

import os
import threading

from Xlib import X, display, error
import psutil

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()

_lock = threading.Lock()  # is_game_focused() is only ever called from one polling
                           # thread today; this is cheap insurance if that changes.
_display = None
_active_window_atom = None
_wm_pid_atom = None
_client_leader_atom = None

_cached_pid = None
_warned_no_process = False
_warned_no_active_window = False


class X11Unavailable(Exception):
    """Raised only by check_x11_available(), for a startup, fail-fast check --
    never raised from is_game_focused() itself."""


def check_x11_available() -> None:
    """Call once at app startup (main thread), before the rotation engine is
    allowed to run. Raises X11Unavailable with an actionable message instead
    of letting the polling thread silently never detect focus."""
    if not os.environ.get("DISPLAY"):
        raise X11Unavailable(
            "No DISPLAY environment variable set. This looks like a pure "
            "Wayland session with no XWayland running -- this app's focus "
            "detection needs an X11 or XWayland display to talk to.")
    try:
        d = display.Display()
    except Exception as exc:
        raise X11Unavailable(
            f"Could not connect to the X server (DISPLAY="
            f"{os.environ.get('DISPLAY')!r}): {exc}") from exc
    try:
        active_atom = d.get_atom("_NET_ACTIVE_WINDOW")
        supported = d.screen().root.get_full_property(
            d.get_atom("_NET_SUPPORTED"), X.AnyPropertyType)
        if supported is None or active_atom not in supported.value:
            raise X11Unavailable(
                "This window manager doesn't advertise EWMH _NET_ACTIVE_WINDOW "
                "support -- focus detection cannot work, so this app would "
                "never auto-pause on alt-tab. A modern desktop WM (GNOME/"
                "Mutter, KDE/KWin, XFCE, i3, sway+XWayland, etc.) is required.")
    finally:
        d.close()


def _get_display():
    """Opens the shared Display once and reuses it -- each Display() call is a
    fresh socket handshake with the X server, wasteful at the ~10Hz this is
    polled. Only ever call this from the one background polling thread."""
    global _display, _active_window_atom, _wm_pid_atom, _client_leader_atom
    if _display is None:
        _display = display.Display()
        _active_window_atom = _display.get_atom("_NET_ACTIVE_WINDOW")
        _wm_pid_atom = _display.get_atom("_NET_WM_PID")
        _client_leader_atom = _display.get_atom("WM_CLIENT_LEADER")
    return _display


def _reset_display():
    global _display
    if _display is not None:
        try:
            _display.close()
        except Exception:
            pass
    _display = None


def _get_active_window():
    d = _get_display()
    prop = d.screen().root.get_full_property(_active_window_atom, X.AnyPropertyType)
    if prop is None or prop.format != 32 or not prop.value:
        return None
    wid = prop.value[0]
    if not wid:  # WM reports "no window focused" as XID 0
        return None
    return d.create_resource_object("window", wid)


def _read_pid_property(window, atom):
    prop = window.get_full_property(atom, X.AnyPropertyType)
    if prop is None or prop.format != 32 or not prop.value:
        return None
    return int(prop.value[0])


def _get_pid_for_active_window(window):
    """_NET_WM_PID is normally set directly on the active top-level window
    (this is what Wine's winex11.drv does, which is what matters for a
    Proton-run game). Falls back to WM_CLIENT_LEADER for the rare client
    (some older Java/SWT-style apps) that only sets it there."""
    pid = _read_pid_property(window, _wm_pid_atom)
    if pid:
        return pid
    leader_prop = window.get_full_property(_client_leader_atom, X.AnyPropertyType)
    if leader_prop is not None and leader_prop.format == 32 and leader_prop.value:
        leader = _get_display().create_resource_object("window", leader_prop.value[0])
        return _read_pid_property(leader, _wm_pid_atom)
    return None


def _game_pid():
    # Identical approach to the Windows version -- psutil.process_iter is OS-agnostic.
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
    """True if the configured game process currently owns the EWMH active window."""
    global _warned_no_process, _warned_no_active_window

    pid = _game_pid()
    if not pid:
        if not _warned_no_process:
            log.warning(
                f"game process '{config.GAME_PROCESS_NAME}' not found -- check "
                f"`ps aux`/`pgrep` for the real process name and set "
                f"POE2BOT_TARGET_PROCESS if it differs")
            _warned_no_process = True
        return False
    _warned_no_process = False

    with _lock:
        try:
            window = _get_active_window()
            fg_pid = _get_pid_for_active_window(window) if window is not None else None
        except (error.XError, OSError) as exc:
            # XError = protocol-level error (e.g. BadWindow -- window closed mid-query,
            # a real race given this is a poll loop). OSError covers a broken-socket
            # connection. Either way: drop the connection, retry fresh next poll.
            log.warning(f"X11 focus check failed, will reconnect next poll: {exc}")
            _reset_display()
            return False
        except Exception as exc:
            log.warning(f"unexpected error in X11 focus check: {exc}")
            _reset_display()
            return False

    if window is None:
        if not _warned_no_active_window:
            log.debug("no _NET_ACTIVE_WINDOW reported (nothing focused, or WM lacks EWMH)")
            _warned_no_active_window = True
        return False
    _warned_no_active_window = False

    focused = fg_pid == pid
    if not focused:
        log.debug(f"active window belongs to pid={fg_pid}, game pid={pid} -- not focused")
    return focused
