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

    Keyboard hotkeys are plain key name strings (e.g. "f6"); mouse hotkeys are
    encoded as "mouse:<button>" (e.g. "mouse:right"). Multiple rotations may
    share the same trigger hotkey -- pressing it fires every rotation bound to
    it -- so, like the cancel/reset/pause keys below, trigger-key registration
    wraps `keyboard.hook()`/`mouse.on_button()` directly (via
    _register_action_key/_unregister_action_key) rather than
    `keyboard.add_hotkey()`, which only tracks one remover per hotkey string
    and would leak earlier registrations when several rotations share a key.
    """

    def __init__(self, rotation_manager, panic_key: str = config.PANIC_KEY):
        self._rotation_manager = rotation_manager
        self._panic_key = panic_key
        self._trigger_keys = {}     # rotation name -> hotkey (config, survives enable/disable)
        self._trigger_handlers = {} # rotation name -> ("keyboard"|"mouse", live handler), only while enabled
        self._cancel_keys = {}     # rotation name -> cancel key (config, survives enable/disable)
        self._cancel_handlers = {} # rotation name -> ("keyboard"|"mouse", live handler), only while enabled
        self._reset_keys = {}      # rotation name -> reset key (config, survives enable/disable)
        self._reset_handlers = {}  # rotation name -> ("keyboard"|"mouse", live handler), only while enabled
        self._pause_keys = {}      # rotation name -> pause key (config, survives enable/disable)
        self._pause_handlers = {}  # rotation name -> ("keyboard"|"mouse", live handler), only while enabled
        self._enabled = False
        self._register_panic_key()
        self._enabled = True

    @property
    def panic_key(self) -> str:
        return self._panic_key

    def bound_to(self, hotkey: str) -> list:
        """Names of every rotation currently bound to `hotkey` (may be more
        than one, since multiple rotations are allowed to share a trigger
        hotkey). Read-only pre-check -- does not touch the OS hook."""
        return [name for name, key in self._trigger_keys.items() if key == hotkey]

    def cancel_key_for(self, rotation_name: str):
        return self._cancel_keys.get(rotation_name)

    def set_cancel_key(self, rotation_name: str, cancel_key):
        """Configure (or clear, if cancel_key is falsy) the key that immediately
        stops `rotation_name` if it's running -- e.g. the game's dodge key, so any
        rotation can be interrupted mid-cast. Multiple rotations may share the
        same cancel key: pressing it just stops whichever of them happen to be
        running."""
        self._unregister_action_key(self._cancel_handlers, rotation_name)
        self._cancel_keys[rotation_name] = cancel_key
        if cancel_key and self._enabled:
            self._register_action_key(
                self._cancel_handlers, rotation_name, cancel_key,
                lambda n=rotation_name: self._rotation_manager.cancel(n))

    def reset_key_for(self, rotation_name: str):
        return self._reset_keys.get(rotation_name)

    def set_reset_key(self, rotation_name: str, reset_key):
        """Configure (or clear, if reset_key is falsy) the key that immediately
        restarts `rotation_name` from its first step if it's running. Same
        sharing rules as the cancel key: not exclusive, multiple rotations may
        use the same reset key."""
        self._unregister_action_key(self._reset_handlers, rotation_name)
        self._reset_keys[rotation_name] = reset_key
        if reset_key and self._enabled:
            self._register_action_key(
                self._reset_handlers, rotation_name, reset_key,
                lambda n=rotation_name: self._rotation_manager.reset(n))

    def pause_key_for(self, rotation_name: str):
        return self._pause_keys.get(rotation_name)

    def set_pause_key(self, rotation_name: str, pause_key):
        """Configure (or clear, if pause_key is falsy) the key that immediately
        freezes `rotation_name` in place if it's running. Same sharing rules as
        the cancel/reset keys: not exclusive, multiple rotations may use the
        same pause key."""
        self._unregister_action_key(self._pause_handlers, rotation_name)
        self._pause_keys[rotation_name] = pause_key
        if pause_key and self._enabled:
            self._register_action_key(
                self._pause_handlers, rotation_name, pause_key,
                lambda n=rotation_name: self._rotation_manager.pause(n))

    def _register_action_key(self, handlers: dict, rotation_name: str, action_key: str, callback):
        """Shared machinery behind bind()/set_cancel_key()/set_reset_key()/
        set_pause_key(): all need a many-rotations-to-one-key registration, so
        all wrap keyboard.hook()/mouse.on_button() directly rather than
        keyboard.add_hotkey(), which assumes one callback per key combo."""
        if is_mouse_hotkey(action_key):
            button = mouse_button_of(action_key)
            handler = mouse.on_button(callback, buttons=(button,), types=(mouse.DOWN,))
            handlers[rotation_name] = ("mouse", handler)
        else:
            def on_key_event(event, cb=callback, key=action_key):
                if event.event_type == keyboard.KEY_DOWN and event.name == key:
                    cb()
            keyboard.hook(on_key_event)
            handlers[rotation_name] = ("keyboard", on_key_event)

    def _unregister_action_key(self, handlers: dict, rotation_name: str):
        entry = handlers.pop(rotation_name, None)
        if entry is None:
            return
        kind, handler = entry
        if kind == "mouse":
            mouse.unhook(handler)
        else:
            keyboard.unhook(handler)

    def _register_panic_key(self):
        # Always a keyboard key (config.PANIC_KEY, default 'f12') -- kept keyboard-only
        # since it's a fixed reserved key, not something the user rebinds to a mouse button.
        keyboard.add_hotkey(self._panic_key, self._rotation_manager.stop_all)

    def bind(self, hotkey: str, rotation_name: str):
        """Bind `rotation_name` to `hotkey`. Multiple rotations may be bound to
        the same hotkey -- pressing it fires all of them -- so, unlike the old
        exclusive design, this never rejects a hotkey just because another
        rotation already uses it."""
        if hotkey == self._panic_key:
            raise ValueError(f"'{display_name(hotkey)}' is reserved as the panic/stop-all key")
        if self._trigger_keys.get(rotation_name) == hotkey:
            return  # already bound to this hotkey, nothing to do
        self._unregister_action_key(self._trigger_handlers, rotation_name)
        self._trigger_keys[rotation_name] = hotkey
        if self._enabled:
            self._register_action_key(
                self._trigger_handlers, rotation_name, hotkey,
                lambda n=rotation_name: self._rotation_manager.trigger(n))
        log.info(f"bound '{hotkey}' -> '{rotation_name}'")

    def unbind(self, rotation_name: str):
        if rotation_name not in self._trigger_keys:
            return
        hotkey = self._trigger_keys.pop(rotation_name)
        self._unregister_action_key(self._trigger_handlers, rotation_name)
        log.info(f"unbound '{hotkey}' (was '{rotation_name}')")

    def rebind(self, new_hotkey, rotation_name: str):
        self.unbind(rotation_name)
        if new_hotkey:
            self.bind(new_hotkey, rotation_name)

    def enable_all(self):
        if self._enabled:
            return
        self._register_panic_key()
        for rotation_name, hotkey in self._trigger_keys.items():
            if hotkey:
                self._register_action_key(
                    self._trigger_handlers, rotation_name, hotkey,
                    lambda n=rotation_name: self._rotation_manager.trigger(n))
        for rotation_name, cancel_key in self._cancel_keys.items():
            if cancel_key:
                self._register_action_key(
                    self._cancel_handlers, rotation_name, cancel_key,
                    lambda n=rotation_name: self._rotation_manager.cancel(n))
        for rotation_name, reset_key in self._reset_keys.items():
            if reset_key:
                self._register_action_key(
                    self._reset_handlers, rotation_name, reset_key,
                    lambda n=rotation_name: self._rotation_manager.reset(n))
        for rotation_name, pause_key in self._pause_keys.items():
            if pause_key:
                self._register_action_key(
                    self._pause_handlers, rotation_name, pause_key,
                    lambda n=rotation_name: self._rotation_manager.pause(n))
        self._enabled = True

    def disable_all(self):
        if not self._enabled:
            return
        keyboard.unhook_all_hotkeys()
        mouse.unhook_all()
        # mouse-based trigger/cancel/reset/pause-key handlers were already released
        # by mouse.unhook_all() above; keyboard-hook-based ones are a separate
        # registry keyboard doesn't touch, so those still need releasing explicitly.
        for kind, handler in self._trigger_handlers.values():
            if kind == "keyboard":
                keyboard.unhook(handler)
        self._trigger_handlers.clear()
        for kind, handler in self._cancel_handlers.values():
            if kind == "keyboard":
                keyboard.unhook(handler)
        self._cancel_handlers.clear()
        for kind, handler in self._reset_handlers.values():
            if kind == "keyboard":
                keyboard.unhook(handler)
        self._reset_handlers.clear()
        for kind, handler in self._pause_handlers.values():
            if kind == "keyboard":
                keyboard.unhook(handler)
        self._pause_handlers.clear()
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
            for rotation_name in list(self._trigger_keys.keys()):
                self._unregister_action_key(self._trigger_handlers, rotation_name)

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
                for rotation_name, hotkey in self._trigger_keys.items():
                    self._register_action_key(
                        self._trigger_handlers, rotation_name, hotkey,
                        lambda n=rotation_name: self._rotation_manager.trigger(n))
