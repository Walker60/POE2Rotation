import threading

import keyboard
import mouse

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()

MOUSE_PREFIX = "mouse:"
MOUSE_DISPLAY_NAMES = {
    "left": "Left Click",
    "right": "Right Click",
    "middle": "Middle Click",
    "x": "Mouse Button 4",
    "x2": "Mouse Button 5",
}


def is_mouse_hotkey(hotkey) -> bool:
    return bool(hotkey) and hotkey.startswith(MOUSE_PREFIX)


def mouse_button_of(hotkey: str) -> str:
    return hotkey[len(MOUSE_PREFIX):]


def encode_mouse_hotkey(button: str) -> str:
    return f"{MOUSE_PREFIX}{button}"


def display_name(hotkey) -> str:
    """Human-friendly label for a hotkey string (a keyboard key name, or
    'mouse:<button>'), for use anywhere the GUI shows a hotkey to the user."""
    if not hotkey:
        return "(unbound)"
    if is_mouse_hotkey(hotkey):
        button = mouse_button_of(hotkey)
        return MOUSE_DISPLAY_NAMES.get(button, f"Mouse {button}")
    return hotkey


class HotkeyManager:
    """Maps global hotkeys (keyboard keys or mouse buttons) to rotation names and
    dispatches into a RotationManager.

    Keyboard hotkeys are plain key name strings (e.g. "f6"), wrapping `keyboard`'s
    own dynamic add_hotkey/remove_hotkey. Mouse hotkeys are encoded as
    "mouse:<button>" (e.g. "mouse:right") and wrap the `mouse` library's
    on_button/unhook, since `keyboard` has no concept of mouse buttons at all.
    Both dispatch through the same bind/unbind/rebind/enable_all/disable_all API
    since bindings are added/changed/removed at runtime from the GUI.
    """

    def __init__(self, rotation_manager, panic_key: str = config.PANIC_KEY):
        self._rotation_manager = rotation_manager
        self._panic_key = panic_key
        self._bound = {}           # hotkey -> rotation name
        self._mouse_handlers = {}  # hotkey -> handler object returned by mouse.on_button, for unhook()
        self._enabled = False
        self._register_panic_key()
        self._enabled = True

    @property
    def panic_key(self) -> str:
        return self._panic_key

    def bound_to(self, hotkey: str):
        """Name of the rotation currently owning `hotkey`, or None if unbound.
        Read-only pre-check -- does not touch the OS hook."""
        return self._bound.get(hotkey)

    def _register_hotkey(self, hotkey: str, rotation_name: str):
        handler = lambda n=rotation_name: self._rotation_manager.trigger(n)
        if is_mouse_hotkey(hotkey):
            button = mouse_button_of(hotkey)
            self._mouse_handlers[hotkey] = mouse.on_button(handler, buttons=(button,), types=(mouse.DOWN,))
        else:
            keyboard.add_hotkey(hotkey, handler)

    def _unregister_hotkey(self, hotkey: str):
        if is_mouse_hotkey(hotkey):
            registered = self._mouse_handlers.pop(hotkey, None)
            if registered is not None:
                mouse.unhook(registered)
        else:
            keyboard.remove_hotkey(hotkey)

    def _register_panic_key(self):
        # Always a keyboard key (config.PANIC_KEY, default 'f12') -- kept keyboard-only
        # since it's a fixed reserved key, not something the user rebinds to a mouse button.
        keyboard.add_hotkey(self._panic_key, self._rotation_manager.stop_all)

    def bind(self, hotkey: str, rotation_name: str):
        if hotkey == self._panic_key:
            raise ValueError(f"'{display_name(hotkey)}' is reserved as the panic/stop-all key")
        existing = self._bound.get(hotkey)
        if existing is not None and existing != rotation_name:
            raise ValueError(f"'{display_name(hotkey)}' is already bound to '{existing}'")
        if existing == rotation_name:
            return  # already bound to this rotation, nothing to do
        if self._enabled:
            self._register_hotkey(hotkey, rotation_name)
        self._bound[hotkey] = rotation_name
        log.info(f"bound '{hotkey}' -> '{rotation_name}'")

    def unbind(self, hotkey: str):
        if hotkey not in self._bound:
            return
        if self._enabled:
            self._unregister_hotkey(hotkey)
        name = self._bound.pop(hotkey)
        log.info(f"unbound '{hotkey}' (was '{name}')")

    def rebind(self, old_hotkey, new_hotkey, rotation_name: str):
        if old_hotkey:
            self.unbind(old_hotkey)
        if new_hotkey:
            self.bind(new_hotkey, rotation_name)

    def enable_all(self):
        if self._enabled:
            return
        self._register_panic_key()
        for hotkey, name in self._bound.items():
            self._register_hotkey(hotkey, name)
        self._enabled = True

    def disable_all(self):
        if not self._enabled:
            return
        keyboard.unhook_all_hotkeys()
        mouse.unhook_all()
        self._mouse_handlers.clear()
        self._enabled = False

    def capture_next_key(self) -> str:
        """BLOCKING -- call from a background thread only, never the Tk main thread.

        Temporarily suspends every bound rotation hotkey (not the panic key) so the
        input being pressed to bind doesn't also fire whatever rotation currently
        owns it, then waits for the next physical keyboard key-down OR mouse
        button-down, whichever comes first, and returns it -- a plain key name for
        a keyboard press, or "mouse:<button>" for a mouse click.
        """
        if self._enabled:
            for hotkey in list(self._bound.keys()):
                self._unregister_hotkey(hotkey)

        result = {}
        done = threading.Event()

        def on_key_event(event):
            if event.event_type == keyboard.KEY_DOWN and "value" not in result:
                result["value"] = event.name
                done.set()

        def on_mouse_event(event):
            if (isinstance(event, mouse.ButtonEvent) and event.event_type == mouse.DOWN
                    and "value" not in result):
                result["value"] = encode_mouse_hotkey(event.button)
                done.set()

        keyboard.hook(on_key_event)
        mouse.hook(on_mouse_event)
        try:
            done.wait()
            return result["value"]
        finally:
            keyboard.unhook(on_key_event)
            mouse.unhook(on_mouse_event)
            if self._enabled:
                for hotkey, name in self._bound.items():
                    self._register_hotkey(hotkey, name)
