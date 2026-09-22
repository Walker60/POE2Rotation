"""Reads a REAL, physically-connected gamepad, for use as a hotkey trigger
source (alongside keyboard/mouse in poe2bot/hotkeys.py) -- e.g. binding a
rotation's trigger/cancel/reset/pause key to a controller press via
HotkeyManager.capture_next_key(). The step editor's own Key field instead
uses poe2bot/gui/controller_map_window.py's click-to-choose picker, which
needs no real hardware at all -- see its docstring for why.

Two platform backends, selected once at import time via _IS_WINDOWS:

- Windows: the Windows XInput API, via raw ctypes -- matches
  poe2bot/focus.py's convention of calling Win32 APIs directly for
  something ctypes already covers well, rather than pulling in pygame/
  inputs/PYXInput for a single DLL export.
- Linux (Steam Deck, see README's Steam Deck section): `evdev`, reading
  /dev/input/event* nodes directly -- the closest Linux analogue of
  polling an XInput slot, but event-driven (blocking reads) rather than
  poll-a-snapshot-struct, since evdev has no XInput-style "give me the
  whole current state" call. UNVERIFIED against real Deck hardware -- see
  README's Steam Deck section for what to check first, especially the
  BTN_SOUTH/EAST/NORTH/WEST -> a/b/y/x mapping and each axis's actual
  min/max range, both of which vary by controller/driver.

This is the READING half; poe2bot/controller.py (a separate module, separate
lifecycle) is the WRITING half -- emulating a virtual controller via
vgamepad (ViGEmBus on Windows, uinput/evdev on Linux). The two are never
assumed to be the same device/slot on purpose -- see poe2bot/config.py's
CONTROLLER_INDEX.
"""
import select
import sys
import threading
import time

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()

_IS_WINDOWS = sys.platform == "win32"

TRIGGER_THRESHOLD = 30  # of 0-255 -- matches the output side's binary (full-on/full-off) treatment
POLL_INTERVAL_S = 0.015  # ~66Hz -- a cheap syscall, negligible CPU; plenty responsive for a hotkey trigger

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

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

    _BUTTON_BITS = {
        "dpad_up": 0x0001, "dpad_down": 0x0002, "dpad_left": 0x0004, "dpad_right": 0x0008,
        "start": 0x0010, "back": 0x0020, "ls": 0x0040, "rs": 0x0080,
        "lb": 0x0100, "rb": 0x0200, "a": 0x1000, "b": 0x2000, "x": 0x4000, "y": 0x8000,
    }
else:
    # Soft/lazy, mirroring poe2bot/focus.py's python-xlib handling: evdev is a
    # Linux-only extra (see requirements.txt), never importable on Windows,
    # and this module must still import cleanly even if it's missing on
    # Linux too -- get_controller_reader() degrades to "no controller found"
    # (via a one-time warning) rather than crashing the app at startup.
    try:
        import evdev
        from evdev import ecodes
    except ImportError as e:
        evdev = None
        ecodes = None
        _evdev_import_error = e
    else:
        _evdev_import_error = None

    _RESCAN_INTERVAL_S = 1.0  # how often to retry finding a device when none is connected
    _EVDEV_SELECT_TIMEOUT_S = 0.25  # upper bound on how long set_index() takes to be noticed
                                     # while the current device sits idle -- see _poll_loop_evdev

    # evdev's BTN_SOUTH/EAST/NORTH/WEST naming is by PHYSICAL POSITION on an
    # Xbox-style pad, not by letter -- SOUTH (bottom) = A, EAST (right) = B,
    # NORTH (top) = Y, WEST (left) = X. Easy to get backwards; verify against
    # a real device with `evtest` if button captures come out mismatched.
    _KEY_CODE_TO_BUTTON = {
        ecodes.BTN_SOUTH: "a", ecodes.BTN_EAST: "b",
        ecodes.BTN_NORTH: "y", ecodes.BTN_WEST: "x",
        ecodes.BTN_TL: "lb", ecodes.BTN_TR: "rb",
        ecodes.BTN_SELECT: "back", ecodes.BTN_START: "start",
        ecodes.BTN_THUMBL: "ls", ecodes.BTN_THUMBR: "rs",
        # Present on some controllers/drivers as discrete buttons; others only
        # ever report the d-pad via the ABS_HAT0X/Y axes below -- both are
        # handled, whichever a given device actually sends.
        ecodes.BTN_DPAD_UP: "dpad_up", ecodes.BTN_DPAD_DOWN: "dpad_down",
        ecodes.BTN_DPAD_LEFT: "dpad_left", ecodes.BTN_DPAD_RIGHT: "dpad_right",
    } if evdev is not None else {}

    _HAT_AXIS_TO_BUTTONS = {
        ecodes.ABS_HAT0X: {-1: "dpad_left", 1: "dpad_right"},
        ecodes.ABS_HAT0Y: {-1: "dpad_up", 1: "dpad_down"},
    } if evdev is not None else {}

    _TRIGGER_AXES = {ecodes.ABS_Z: "lt", ecodes.ABS_RZ: "rt"} if evdev is not None else {}

    # A device only needs to report ONE of these to be treated as
    # "gamepad-like" by _candidate_gamepad_paths -- BTN_SOUTH alone (the
    # original, narrower check) would miss a real controller/virtual pad
    # that happens to report a different subset of these.
    _GAMEPAD_INDICATOR_CODES = {
        ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
        ecodes.BTN_GAMEPAD, ecodes.BTN_THUMBL, ecodes.BTN_THUMBR, ecodes.BTN_TRIGGER,
    } if evdev is not None else set()


class ControllerReader:
    """Watches one gamepad (an XInput slot on Windows, an evdev device on
    Linux) on its own daemon thread, dispatching a callback once per
    button-down *transition*, never once per poll/event -- so a held button
    behaves like keyboard.KEY_DOWN, firing once per physical press rather
    than repeatedly while held. on_button_up mirrors this for the matching
    release transition (keyboard.KEY_UP's analogue) -- e.g. so a rotation's
    trigger binding can tell a held button apart from a released one."""

    def __init__(self, index=None):
        self._index = config.CONTROLLER_INDEX if index is None else index
        self._lock = threading.Lock()
        self._down_subscribers = {}   # button_name -> list[callback]
        self._up_subscribers = {}     # button_name -> list[callback], fired once per release transition
        self._any_subscribers = []    # list[callback(button_name)] -- for capture flows
        self._warned_disconnected = False
        if _IS_WINDOWS:
            self._prev_mask = 0
            self._prev_trigger = {"lt": False, "rt": False}
        else:
            self._held = set()            # button names currently down
            self._hat_state = {}          # ABS_HAT* code -> last nonzero value seen
            self._axis_max = {}           # ABS_Z/ABS_RZ code -> this device's reported max
            self._device_generation = 0   # bumped by set_index() to force a reopen mid-read_loop
        # Starts polling immediately, for the lifetime of the process -- there's
        # exactly one ControllerReader (see get_controller_reader() below) and
        # nothing ever needs to stop it early, so it has no corresponding stop()/
        # join(); daemon=True is what lets the process exit without joining it.
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def set_index(self, new_index: int):
        """Switches which controller this reader watches, live -- e.g. from
        Settings. Resets whatever "currently held" state is tracked so the
        first read against the new target doesn't misread its actual
        current state as a fresh "just pressed" edge (or silently inherit a
        stale "held" state left over from the old one)."""
        with self._lock:
            self._index = new_index
            if _IS_WINDOWS:
                self._prev_mask = 0
                self._prev_trigger = {"lt": False, "rt": False}
            else:
                self._held = set()
                self._hat_state = {}
                self._device_generation += 1

    def on_button_down(self, button_name: str, callback):
        with self._lock:
            self._down_subscribers.setdefault(button_name, []).append(callback)

    def off_button_down(self, button_name: str, callback):
        with self._lock:
            subs = self._down_subscribers.get(button_name)
            if subs and callback in subs:
                subs.remove(callback)

    def on_button_up(self, button_name: str, callback):
        with self._lock:
            self._up_subscribers.setdefault(button_name, []).append(callback)

    def off_button_up(self, button_name: str, callback):
        with self._lock:
            subs = self._up_subscribers.get(button_name)
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
        if _IS_WINDOWS:
            self._poll_loop_xinput()
        else:
            self._poll_loop_evdev()

    # ---- Windows/XInput backend ---------------------------------------------

    def _poll_loop_xinput(self):
        state = _XINPUT_STATE()
        while True:
            time.sleep(POLL_INTERVAL_S)
            if _xinput_get_state(self._index, ctypes.byref(state)) != _ERROR_SUCCESS:
                self._handle_disconnected()
                continue
            self._warned_disconnected = False
            self._process_snapshot(state.Gamepad.wButtons, state.Gamepad.bLeftTrigger,
                                    state.Gamepad.bRightTrigger)

    def _process_snapshot(self, mask: int, left_trigger: int, right_trigger: int):
        """The actual edge-detection logic, isolated from the ctypes polling
        mechanics above so it can be exercised directly with synthetic
        values in tests -- no real XInput/hardware needed to verify it."""
        newly_pressed = mask & ~self._prev_mask
        newly_released = self._prev_mask & ~mask
        self._prev_mask = mask
        for name, bit in _BUTTON_BITS.items():
            if newly_pressed & bit:
                self._dispatch(name)
            if newly_released & bit:
                self._dispatch_up(name)
        for name, raw in (("lt", left_trigger), ("rt", right_trigger)):
            pressed = raw >= TRIGGER_THRESHOLD
            if pressed and not self._prev_trigger[name]:
                self._dispatch(name)
            elif not pressed and self._prev_trigger[name]:
                self._dispatch_up(name)
            self._prev_trigger[name] = pressed

    # ---- Linux/evdev backend -------------------------------------------------

    def _candidate_gamepad_paths(self):
        """Every /dev/input/event* device that looks like a gamepad (reports
        at least one of _GAMEPAD_INDICATOR_CODES), in a stable sorted-by-path
        order -- the closest Linux equivalent of "XInput slots 0-3," used
        with CONTROLLER_INDEX picking the Nth one. Opens each device only
        briefly to check its capabilities, not to read from it.

        Logs a one-time diagnostic dump of EVERY /dev/input device seen
        (path, name, whether it looks gamepad-like) when nothing qualifies
        -- on a Steam Deck in particular, the built-in controls are normally
        owned by Steam Input and simply don't appear as a generic gamepad to
        an app that isn't currently being run/targeted through Steam, so
        "nothing found" is the expected, common case there, not necessarily
        a bug -- see README's Steam Deck section."""
        paths = []
        seen = []
        for path in sorted(evdev.list_devices()):
            try:
                dev = evdev.InputDevice(path)
            except (OSError, PermissionError):
                seen.append((path, "<unreadable>", False))
                continue
            try:
                key_caps = dev.capabilities().get(ecodes.EV_KEY, [])
                is_gamepad_like = any(code in key_caps for code in _GAMEPAD_INDICATOR_CODES)
                seen.append((path, dev.name, is_gamepad_like))
                if is_gamepad_like:
                    paths.append(path)
            finally:
                dev.close()
        if not paths:
            self._log_device_dump_once(seen)
        return paths

    def _log_device_dump_once(self, seen):
        if self._warned_disconnected:
            return  # already dumped this info on a previous scan -- don't spam it every rescan interval
        if not seen:
            log.info("no /dev/input devices found at all")
            return
        lines = "\n".join(f"  {path}  name={name!r}  gamepad-like={is_gamepad}"
                           for path, name, is_gamepad in seen)
        log.info(
            f"no gamepad-like /dev/input device found; every device currently visible:\n{lines}\n"
            f"On a Steam Deck, the built-in controls are normally owned by Steam Input and only "
            f"exposed to a game Steam is actively running/targeting -- see README's Steam Deck "
            f"section for how to get them recognized here too.")

    def _open_evdev_device(self):
        paths = self._candidate_gamepad_paths()
        if not (0 <= self._index < len(paths)):
            return None
        try:
            device = evdev.InputDevice(paths[self._index])
        except PermissionError:
            log.warning(
                f"found a gamepad at {paths[self._index]} but can't open it -- your user likely "
                f"needs to be in the 'input' group (or an equivalent udev rule) to read it")
            return None
        except OSError:
            return None
        abs_caps = dict(device.capabilities(absinfo=True).get(ecodes.EV_ABS, []))
        self._axis_max = {code: (abs_caps[code].max or 255) for code in _TRIGGER_AXES if code in abs_caps}
        return device

    def _poll_loop_evdev(self):
        if evdev is None:
            if not self._warned_disconnected:
                log.warning(
                    "the `evdev` package is not installed -- real-controller input is disabled. "
                    "Install it with `pip install evdev` (see README's Steam Deck section). "
                    f"Import error: {_evdev_import_error}")
                self._warned_disconnected = True
            return

        device = None
        my_generation = None
        while True:
            if device is None or my_generation != self._device_generation:
                if device is not None:
                    try:
                        device.close()
                    except OSError:
                        pass
                    device = None
                my_generation = self._device_generation
                device = self._open_evdev_device()
                if device is None:
                    self._handle_disconnected()
                    time.sleep(_RESCAN_INTERVAL_S)
                    continue
                self._warned_disconnected = False
            try:
                # select() with a bounded timeout, rather than device.read_loop()'s
                # unbounded blocking read, so set_index() takes effect within one
                # timeout period even while the current device sits idle (no new
                # events at all) -- a blocking read_loop() would otherwise only
                # ever notice a generation change in between actual events,
                # which might never come from a device the user has switched away
                # from.
                ready, _, _ = select.select([device.fd], [], [], _EVDEV_SELECT_TIMEOUT_S)
                if ready:
                    for event in device.read():
                        self._handle_evdev_event(event)
            except (OSError, BlockingIOError):
                # The device disappeared (unplugged, or the kernel dropped it) --
                # drop it and fall back to rescanning on the next iteration.
                device = None
                self._held = set()
                self._hat_state = {}

    def _handle_evdev_event(self, event):
        if event.type == ecodes.EV_KEY:
            button = _KEY_CODE_TO_BUTTON.get(event.code)
            if button is None:
                return
            if event.value == 1:      # key down
                self._note_pressed(button)
            elif event.value == 0:    # key up
                self._note_released(button)
        elif event.type == ecodes.EV_ABS:
            if event.code in _HAT_AXIS_TO_BUTTONS:
                self._handle_hat_axis(event.code, event.value)
            elif event.code in _TRIGGER_AXES:
                self._handle_trigger_axis(event.code, event.value)

    def _handle_hat_axis(self, code, value):
        previous = self._hat_state.get(code, 0)
        if value == previous:
            return  # unchanged reading -- a real device can report the same
                     # value repeatedly while held; must not re-release+re-press
        side_map = _HAT_AXIS_TO_BUTTONS[code]
        if previous != 0:
            self._note_released(side_map.get(previous))
        if value != 0 and value in side_map:
            self._note_pressed(side_map[value])
        self._hat_state[code] = value

    def _handle_trigger_axis(self, code, value):
        name = _TRIGGER_AXES[code]
        axis_max = self._axis_max.get(code, 255)
        pressed = value >= axis_max * (TRIGGER_THRESHOLD / 255)
        if pressed:
            self._note_pressed(name)
        else:
            self._note_released(name)

    def _note_pressed(self, button_name: str):
        if button_name not in self._held:
            self._held.add(button_name)
            self._dispatch(button_name)

    def _note_released(self, button_name):
        if button_name is None:
            return
        if button_name in self._held:
            self._held.discard(button_name)
            self._dispatch_up(button_name)

    # ---- shared ---------------------------------------------------------------

    def _handle_disconnected(self):
        """Also fires an "up" dispatch for anything that was actively held at
        the moment the controller drops out (unplugged, battery died, etc.)
        -- otherwise a rotation held-triggered by one of its buttons would
        have no way to ever learn it was "released" and would loop forever
        with no physical button left to lift."""
        if _IS_WINDOWS:
            if self._prev_mask or any(self._prev_trigger.values()):
                for name, bit in _BUTTON_BITS.items():
                    if self._prev_mask & bit:
                        self._dispatch_up(name)
                for name, pressed in self._prev_trigger.items():
                    if pressed:
                        self._dispatch_up(name)
                self._prev_mask = 0
                self._prev_trigger = {"lt": False, "rt": False}
        else:
            for name in list(self._held):
                self._dispatch_up(name)
            self._held = set()
            self._hat_state = {}
        if not self._warned_disconnected:
            if _IS_WINDOWS:
                log.warning(f"controller index {self._index} not connected -- set "
                            f"POE2BOT_CONTROLLER_INDEX if your real controller is on a different slot")
            else:
                log.warning(
                    f"no gamepad found at index {self._index} -- set POE2BOT_CONTROLLER_INDEX if "
                    f"yours enumerates at a different index, or check `ls /dev/input/event*` and "
                    f"that your user can read it (see the 'input' group note above)")
            self._warned_disconnected = True

    def _dispatch(self, button_name: str):
        with self._lock:
            callbacks = list(self._down_subscribers.get(button_name, ()))
            any_callbacks = list(self._any_subscribers)
        for cb in callbacks:
            cb()
        for cb in any_callbacks:
            cb(button_name)

    def _dispatch_up(self, button_name: str):
        with self._lock:
            callbacks = list(self._up_subscribers.get(button_name, ()))
        for cb in callbacks:
            cb()


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


def peek_controller_reader():
    """The current ControllerReader singleton, or None if nothing has
    triggered its lazy creation yet. For callers that want to update it
    live ONLY if it already exists -- e.g. applying a changed
    CONTROLLER_INDEX from Settings -- without themselves accidentally
    starting its polling thread as a side effect for a keyboard-only user
    who's never touched anything controller-related."""
    return _singleton
