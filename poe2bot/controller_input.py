"""Reads a REAL, physically-connected Xbox-compatible controller via the
Windows XInput API, for use as a hotkey trigger source (alongside keyboard/
mouse in poe2bot/hotkeys.py) and for the step editor's "capture a controller
button" flow.

Deliberately a raw ctypes wrapper, not a third-party library -- matches
poe2bot/focus.py's existing convention of calling Win32 APIs directly for
something ctypes already covers well, rather than pulling in pygame/inputs/
PYXInput for a single DLL export.

This is the READING half; poe2bot/controller.py (a separate module, separate
lifecycle) is the WRITING half -- emulating a virtual controller via
vgamepad/ViGEmBus. The two are never assumed to be the same XInput slot on
purpose -- see poe2bot/config.py's CONTROLLER_INDEX.
"""
import ctypes
import threading
import time
from ctypes import wintypes

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", _XINPUT_GAMEPAD),
    ]


# XInputGetState always returns a plain DWORD error code (0 == success), never
# a handle, so there's no sign-bit misread risk the way focus.py's HWND case
# has -- but restype/argtypes are still set explicitly, for the same reason
# focus.py sets them: never let ctypes guess a signature for a Win32 call.
_xinput_get_state = ctypes.windll.xinput1_4.XInputGetState
_xinput_get_state.restype = wintypes.DWORD
_xinput_get_state.argtypes = [wintypes.DWORD, ctypes.POINTER(_XINPUT_STATE)]

_ERROR_SUCCESS = 0
TRIGGER_THRESHOLD = 30  # of 0-255 -- matches the output side's binary (full-on/full-off) treatment
POLL_INTERVAL_S = 0.015  # ~66Hz -- a cheap syscall, negligible CPU; plenty responsive for a hotkey trigger

_BUTTON_BITS = {
    "dpad_up": 0x0001, "dpad_down": 0x0002, "dpad_left": 0x0004, "dpad_right": 0x0008,
    "start": 0x0010, "back": 0x0020, "ls": 0x0040, "rs": 0x0080,
    "lb": 0x0100, "rb": 0x0200, "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000,
}


class ControllerReader:
    """Polls one XInput slot on its own daemon thread (XInput has no push/
    hook API -- callers must poll and diff consecutive snapshots themselves),
    dispatching a callback once per button-down *transition*, never once per
    poll -- so a held button behaves like keyboard.KEY_DOWN, firing once per
    physical press rather than repeatedly while held."""

    def __init__(self, index=None):
        self._index = config.CONTROLLER_INDEX if index is None else index
        self._lock = threading.Lock()
        self._down_subscribers = {}   # button_name -> list[callback]
        self._any_subscribers = []    # list[callback(button_name)] -- for capture flows
        self._prev_mask = 0
        self._prev_trigger = {"lt": False, "rt": False}
        self._warned_disconnected = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def on_button_down(self, button_name: str, callback):
        with self._lock:
            self._down_subscribers.setdefault(button_name, []).append(callback)

    def off_button_down(self, button_name: str, callback):
        with self._lock:
            subs = self._down_subscribers.get(button_name)
            if subs and callback in subs:
                subs.remove(callback)

    def on_any_button_down(self, callback):
        with self._lock:
            self._any_subscribers.append(callback)

    def off_any_button_down(self, callback):
        with self._lock:
            if callback in self._any_subscribers:
                self._any_subscribers.remove(callback)

    def _poll_loop(self):
        state = _XINPUT_STATE()
        while True:
            time.sleep(POLL_INTERVAL_S)
            if _xinput_get_state(self._index, ctypes.byref(state)) != _ERROR_SUCCESS:
                self._handle_disconnected()
                continue
            self._warned_disconnected = False
            self._process_snapshot(state.Gamepad.wButtons, state.Gamepad.bLeftTrigger,
                                    state.Gamepad.bRightTrigger)

    def _handle_disconnected(self):
        if self._prev_mask or any(self._prev_trigger.values()):
            self._prev_mask = 0
            self._prev_trigger = {"lt": False, "rt": False}
        if not self._warned_disconnected:
            log.warning(f"controller index {self._index} not connected -- set "
                        f"POE2BOT_CONTROLLER_INDEX if your real controller is on a different slot")
            self._warned_disconnected = True

    def _process_snapshot(self, mask: int, left_trigger: int, right_trigger: int):
        """The actual edge-detection logic, isolated from the ctypes polling
        mechanics above so it can be exercised directly with synthetic
        values in tests -- no real XInput/hardware needed to verify it."""
        newly_pressed = mask & ~self._prev_mask
        self._prev_mask = mask
        for name, bit in _BUTTON_BITS.items():
            if newly_pressed & bit:
                self._dispatch(name)
        for name, raw in (("lt", left_trigger), ("rt", right_trigger)):
            pressed = raw >= TRIGGER_THRESHOLD
            if pressed and not self._prev_trigger[name]:
                self._dispatch(name)
            self._prev_trigger[name] = pressed

    def _dispatch(self, button_name: str):
        with self._lock:
            callbacks = list(self._down_subscribers.get(button_name, ()))
            any_callbacks = list(self._any_subscribers)
        for cb in callbacks:
            cb()
        for cb in any_callbacks:
            cb(button_name)


_singleton = None
_singleton_lock = threading.Lock()


def get_controller_reader() -> ControllerReader:
    """Lazy module-level singleton -- the polling thread only starts once
    something actually needs controller input (a rotation binds a controller
    hotkey, or a capture flow starts), not for every keyboard-only user."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ControllerReader()
    return _singleton
