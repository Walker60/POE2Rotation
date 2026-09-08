import contextlib
import threading

import keyboard
import mouse

from poe2bot import config
from poe2bot.controller import controller_button_of, encode_controller_key, is_controller_key
from poe2bot.controller_input import get_controller_reader
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


CONTROLLER_DISPLAY_NAMES = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "lb": "Left Bumper", "rb": "Right Bumper", "lt": "Left Trigger", "rt": "Right Trigger",
    "back": "Back", "start": "Start", "ls": "Left Stick", "rs": "Right Stick",
    "dpad_up": "D-Pad Up", "dpad_down": "D-Pad Down", "dpad_left": "D-Pad Left", "dpad_right": "D-Pad Right",
}


def classify(hotkey) -> str:
    """Which input device `hotkey` refers to: "mouse", "controller", or
    "keyboard" (the fallback for any plain key name). Single source of
    truth for the mouse/controller/keyboard triage that used to be
    reimplemented independently in executor.py, models.py, and here."""
    if is_mouse_hotkey(hotkey):
        return "mouse"
    if is_controller_key(hotkey):
        return "controller"
    return "keyboard"


def display_name(hotkey) -> str:
    """Human-friendly label for a hotkey string (a keyboard key name,
    'mouse:<button>', or 'controller:<button>'), for use anywhere the GUI
    shows a hotkey to the user."""
    if not hotkey:
        return "(unbound)"
    kind = classify(hotkey)
    if kind == "mouse":
        button = mouse_button_of(hotkey)
        return MOUSE_DISPLAY_NAMES.get(button, f"Mouse {button}")
    if kind == "controller":
        button = controller_button_of(hotkey)
        return f"Controller: {CONTROLLER_DISPLAY_NAMES.get(button, button)}"
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
        self._capture_lock = threading.Lock()  # serializes capture_next_key() -- see its docstring
        self._enabled = False
        self._register_panic_key()
        self._enabled = True

    @property
    def panic_key(self) -> str:
        return self._panic_key

    def set_panic_key(self, new_panic_key: str):
        """Changes the reserved panic/stop-all key at runtime (e.g. from
        Settings) -- unregisters the old one and registers the new one, if
        hotkeys are currently enabled; otherwise just records it for the
        next enable_all(). keyboard.unhook_all_hotkeys() is safe here for
        the same reason disable_all() already uses it: the panic key is the
        ONLY keyboard.add_hotkey()-based registration in this app -- every
        rotation-scoped key uses keyboard.hook()/mouse.on_button() instead
        (see _register_action_key), so this can't accidentally drop one of
        those."""
        if self._enabled:
            keyboard.unhook_all_hotkeys()
        self._panic_key = new_panic_key
        if self._enabled:
            self._register_panic_key()

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
        self._set_action_key(self._cancel_keys, self._cancel_handlers,
                              self._rotation_manager.cancel, rotation_name, cancel_key)

    def reset_key_for(self, rotation_name: str):
        return self._reset_keys.get(rotation_name)

    def set_reset_key(self, rotation_name: str, reset_key):
        """Configure (or clear, if reset_key is falsy) the key that immediately
        restarts `rotation_name` from its first step if it's running. Same
        sharing rules as the cancel key: not exclusive, multiple rotations may
        use the same reset key."""
        self._set_action_key(self._reset_keys, self._reset_handlers,
                              self._rotation_manager.reset, rotation_name, reset_key)

    def pause_key_for(self, rotation_name: str):
        return self._pause_keys.get(rotation_name)

    def set_pause_key(self, rotation_name: str, pause_key):
        """Configure (or clear, if pause_key is falsy) the key that immediately
        freezes `rotation_name` in place if it's running. Same sharing rules as
        the cancel/reset keys: not exclusive, multiple rotations may use the
        same pause key."""
        self._set_action_key(self._pause_keys, self._pause_handlers,
                              self._rotation_manager.pause, rotation_name, pause_key)

    def _set_action_key(self, keys: dict, handlers: dict, action_fn, rotation_name: str, action_key):
        """Shared body behind set_cancel_key()/set_reset_key()/set_pause_key():
        unregister whatever this rotation was previously bound to in `keys`/
        `handlers`, record the new key, and (if non-empty and hotkeys are
        currently enabled) register it against `action_fn(rotation_name)`."""
        self._unregister_action_key(handlers, rotation_name)
        keys[rotation_name] = action_key
        if action_key and self._enabled:
            self._register_action_key(
                handlers, rotation_name, action_key,
                lambda n=rotation_name: action_fn(n))

    def _register_action_key(self, handlers: dict, rotation_name: str, action_key: str, callback):
        """Shared machinery behind bind()/set_cancel_key()/set_reset_key()/
        set_pause_key(): all need a many-rotations-to-one-key registration, so
        all wrap keyboard.hook()/mouse.on_button() directly rather than
        keyboard.add_hotkey(), which assumes one callback per key combo."""
        if is_mouse_hotkey(action_key):
            button = mouse_button_of(action_key)
            handler = mouse.on_button(callback, buttons=(button,), types=(mouse.DOWN,))
            handlers[rotation_name] = ("mouse", handler)
        elif is_controller_key(action_key):
            button = controller_button_of(action_key)

            def on_controller_button(cb=callback):
                cb()
            get_controller_reader().on_button_down(button, on_controller_button)
            handlers[rotation_name] = ("controller", (button, on_controller_button))
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
        elif kind == "controller":
            button, callback = handler
            get_controller_reader().off_button_down(button, callback)
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

    def _action_key_specs(self):
        """(keys dict, handlers dict, action_fn) for every rotation-scoped
        hotkey action -- trigger, cancel, reset, pause -- in the order
        enable_all() has always registered them. Shared by enable_all() and
        _suspend_all_action_keys() so both stay in sync by construction
        instead of by separately-maintained loops."""
        return (
            (self._trigger_keys, self._trigger_handlers, self._rotation_manager.trigger),
            (self._cancel_keys, self._cancel_handlers, self._rotation_manager.cancel),
            (self._reset_keys, self._reset_handlers, self._rotation_manager.reset),
            (self._pause_keys, self._pause_handlers, self._rotation_manager.pause),
        )

    def enable_all(self):
        if self._enabled:
            return
        self._register_panic_key()
        for keys, handlers, action_fn in self._action_key_specs():
            for rotation_name, key in keys.items():
                if key:
                    self._register_action_key(
                        handlers, rotation_name, key,
                        lambda n=rotation_name, fn=action_fn: fn(n))
        self._enabled = True

    def disable_all(self):
        if not self._enabled:
            return
        # Releases the panic key (an add_hotkey()-based registration, untouched by
        # the four registries below). Each registry's own entries are unhooked
        # individually via _unregister_action_key, which already dispatches
        # correctly by kind (keyboard/mouse/controller) -- no bulk mouse.unhook_all()
        # or per-kind patch-up loop needed on top of that.
        keyboard.unhook_all_hotkeys()
        for handlers in (self._trigger_handlers, self._cancel_handlers,
                          self._reset_handlers, self._pause_handlers):
            for rotation_name in list(handlers.keys()):
                self._unregister_action_key(handlers, rotation_name)
        self._enabled = False

    @contextlib.contextmanager
    def _suspend_all_action_keys(self):
        """Temporarily unregisters every currently-bound trigger/cancel/reset/
        pause handler (every rotation, all three kinds) so a capture flow's
        physical input doesn't also fire whatever action currently owns it,
        then restores them all on exit. Shared by capture_next_key() and
        capture_next_controller_button() -- both mutate/restore the same
        registries, so callers must hold self._capture_lock around this too,
        not just each other."""
        if self._enabled:
            for keys, handlers, _ in self._action_key_specs():
                for rotation_name in list(keys.keys()):
                    self._unregister_action_key(handlers, rotation_name)
        try:
            yield
        finally:
            if self._enabled:
                for keys, handlers, action_fn in self._action_key_specs():
                    for rotation_name, key in keys.items():
                        if key:
                            self._register_action_key(
                                handlers, rotation_name, key,
                                lambda n=rotation_name, fn=action_fn: fn(n))

    def _attach_keyboard_capture(self, on_value):
        """Register a one-shot keyboard key-down listener that reports the
        key name to `on_value`; return the matching detach callable. Shared
        capture-source helper -- see _capture()'s docstring."""
        def on_key_event(event):
            if event.event_type == keyboard.KEY_DOWN:
                on_value(event.name)
        keyboard.hook(on_key_event)
        return lambda: keyboard.unhook(on_key_event)

    def _attach_mouse_capture(self, on_value):
        """Register a one-shot mouse button-down listener that reports the
        encoded "mouse:<button>" hotkey to `on_value`; return the matching
        detach callable. Shared capture-source helper -- see _capture()'s
        docstring."""
        def on_mouse_event(event):
            if isinstance(event, mouse.ButtonEvent) and event.event_type == mouse.DOWN:
                on_value(encode_mouse_hotkey(event.button))
        mouse.hook(on_mouse_event)
        return lambda: mouse.unhook(on_mouse_event)

    def _attach_controller_capture(self, on_value):
        """Register a one-shot controller button-down listener that reports
        the encoded "controller:<button>" hotkey to `on_value`; return the
        matching detach callable. Shared capture-source helper -- see
        _capture()'s docstring."""
        reader = get_controller_reader()
        def on_controller_event(button_name):
            on_value(encode_controller_key(button_name))
        reader.on_any_button_down(on_controller_event)
        return lambda: reader.off_any_button_down(on_controller_event)

    def _capture(self, attach_fns) -> str:
        """BLOCKING -- call from a background thread only, never the Tk main
        thread. Shared implementation behind capture_next_key(),
        capture_next_controller_button(), and capture_next_mouse_button():
        temporarily suspends every bound rotation hotkey -- trigger, cancel,
        reset, and pause alike (not the panic key) -- so the input being
        pressed to bind doesn't also fire whatever action currently owns it,
        then waits for the first of `attach_fns` (each one of the
        _attach_*_capture methods above) to report a value, and returns it.

        Guarded by self._capture_lock so two overlapping calls (e.g. the GUI
        lets a user click a second "Bind ..." button before the first capture
        resolves) can't both unregister-then-restore concurrently -- without
        this, both callers' restore step would re-register every action key,
        leaving a duplicate, permanently orphaned hook that nothing could ever
        unhook again short of restarting the process. All three public
        methods share this lock (via _suspend_all_action_keys()), since they
        all mutate/restore the same registries and so must be mutually
        exclusive with each other, not just with themselves.
        """
        with self._capture_lock, self._suspend_all_action_keys():
            result = {}
            done = threading.Event()

            def on_value(value):
                if "value" not in result:
                    result["value"] = value
                    done.set()

            detachers = [attach(on_value) for attach in attach_fns]
            try:
                done.wait()
                return result["value"]
            finally:
                for detach in detachers:
                    detach()

    def capture_next_key(self) -> str:
        """BLOCKING -- waits for the next physical keyboard key-down, mouse
        button-down, or controller button-down, whichever comes first, and
        returns it -- a plain key name for a keyboard press, "mouse:<button>"
        for a mouse click, or "controller:<button>" for a controller press.
        See _capture() for the suspend/restore/locking discipline."""
        return self._capture((self._attach_keyboard_capture, self._attach_mouse_capture,
                               self._attach_controller_capture))

    def capture_next_controller_button(self) -> str:
        """BLOCKING -- same as capture_next_key(), but listens ONLY to the
        controller reader -- for the step editor's controller-only capture,
        where a stray keyboard/mouse event must not be able to win the race
        and silently write the wrong kind of value into a field meant to
        hold a controller button."""
        return self._capture((self._attach_controller_capture,))

    def capture_next_mouse_button(self) -> str:
        """BLOCKING -- same as capture_next_key(), but listens ONLY to the
        mouse -- for the step editor's mouse-only capture, where a stray
        keyboard/controller event must not be able to win the race and
        silently write the wrong kind of value into a field meant to hold a
        mouse button."""
        return self._capture((self._attach_mouse_capture,))
