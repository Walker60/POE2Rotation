"""Virtual Xbox 360 controller output, via vgamepad/ViGEmBus.

Mirrors poe2bot/hotkeys.py's "mouse:<button>" string-prefix convention: a
Step (or, on the input side, a Rotation hotkey) can hold a plain string
like "a" for a keyboard key, or "controller:a" for a controller button --
CONTROLLER_PREFIX/is_controller_key/controller_button_of/encode_controller_key
are the single place that encoding is defined, shared by models.py
(validation), executor.py (dispatch), and the GUI's capture flow.

vgamepad itself must NEVER be imported at module scope here (or anywhere
else in this codebase) -- merely importing it connects to the ViGEmBus
driver and raises if that driver isn't installed/running, which would
break every keyboard-only user who has never touched a controller-encoded
step. _get_pad() defers the import to first real use.
"""
import threading

CONTROLLER_PREFIX = "controller:"

# The two analog triggers aren't part of vgamepad's digital XUSB_BUTTON
# bitmask -- they're separate press_button-shaped calls (left_trigger/
# right_trigger), but from a rotation's perspective they behave like any
# other button: press() = full depth (255), release() = 0. Everything else
# maps directly to a confirmed XUSB_BUTTON member name.
_DIGITAL_BUTTONS = {
    "a": "XUSB_GAMEPAD_A",
    "b": "XUSB_GAMEPAD_B",
    "x": "XUSB_GAMEPAD_X",
    "y": "XUSB_GAMEPAD_Y",
    "lb": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "rb": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "back": "XUSB_GAMEPAD_BACK",
    "start": "XUSB_GAMEPAD_START",
    "ls": "XUSB_GAMEPAD_LEFT_THUMB",
    "rs": "XUSB_GAMEPAD_RIGHT_THUMB",
    "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
    "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
    "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
    "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
}
_TRIGGERS = ("lt", "rt")
VALID_BUTTON_NAMES = frozenset(_DIGITAL_BUTTONS) | frozenset(_TRIGGERS)


def is_controller_key(key) -> bool:
    return bool(key) and key.startswith(CONTROLLER_PREFIX)


def controller_button_of(key: str) -> str:
    return key[len(CONTROLLER_PREFIX):]


def encode_controller_key(name: str) -> str:
    return f"{CONTROLLER_PREFIX}{name}"


class ControllerUnavailable(RuntimeError):
    """Raised when the virtual controller can't be created -- vgamepad
    isn't installed, or its ViGEmBus driver isn't installed/running."""


_pad_lock = threading.Lock()
_init_lock = threading.Lock()
_pad = None
_vg = None  # the imported vgamepad module, stashed once import succeeds


def _get_pad():
    """Lazily creates the one shared virtual controller for this process.
    Deliberately never cached as a permanent failure -- a user who installs
    or starts ViGEmBus mid-session should have the very next fire succeed
    without restarting the app."""
    global _pad, _vg
    if _pad is not None:
        return _pad
    with _init_lock:
        if _pad is None:
            try:
                import vgamepad as vg
            except Exception as e:
                raise ControllerUnavailable(
                    "vgamepad could not be imported -- install it (pip install vgamepad) "
                    f"and make sure the ViGEmBus driver it installs is running. Details: {e}") from e
            try:
                pad = vg.VX360Gamepad()
            except Exception as e:
                raise ControllerUnavailable(
                    "Could not create a virtual Xbox 360 controller -- ViGEmBus may not be "
                    "installed or running. Reinstalling the vgamepad pip package re-runs its "
                    f"driver installer. Details: {e}") from e
            _vg, _pad = vg, pad
    return _pad


def press(name: str):
    """Press and flush a controller button/trigger. `name` is the bare
    button name (e.g. "a", "lt"), not the "controller:"-prefixed form."""
    pad = _get_pad()
    with _pad_lock:
        if name in _TRIGGERS:
            (pad.left_trigger if name == "lt" else pad.right_trigger)(value=255)
        else:
            pad.press_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        pad.update()


def release(name: str):
    """Release and flush a controller button/trigger."""
    pad = _get_pad()
    with _pad_lock:
        if name in _TRIGGERS:
            (pad.left_trigger if name == "lt" else pad.right_trigger)(value=0)
        else:
            pad.release_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        pad.update()


def release_all():
    """Releases every known button/trigger and flushes once -- called on app
    shutdown. A no-op if no virtual controller was ever created. Built only
    from the confirmed press_button/release_button/left_trigger/right_trigger/
    update API surface, rather than assuming a convenience reset() method
    exists on the installed vgamepad version."""
    global _pad
    if _pad is None:
        return
    with _pad_lock:
        for name in _DIGITAL_BUTTONS:
            _pad.release_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        _pad.left_trigger(value=0)
        _pad.right_trigger(value=0)
        _pad.update()
