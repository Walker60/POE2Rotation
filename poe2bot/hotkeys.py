import keyboard

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()


class HotkeyManager:
    """Maps global hotkeys to rotation names and dispatches into a RotationManager.

    Wraps `keyboard`'s own dynamic add_hotkey/remove_hotkey rather than a manual
    read_event() dispatch loop, since bindings are added/changed/removed at
    runtime from the GUI.
    """

    def __init__(self, rotation_manager, panic_key: str = config.PANIC_KEY):
        self._rotation_manager = rotation_manager
        self._panic_key = panic_key
        self._bound = {}  # hotkey -> rotation name
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
        keyboard.add_hotkey(hotkey, lambda n=rotation_name: self._rotation_manager.trigger(n))

    def _register_panic_key(self):
        keyboard.add_hotkey(self._panic_key, self._rotation_manager.stop_all)

    def bind(self, hotkey: str, rotation_name: str):
        if hotkey == self._panic_key:
            raise ValueError(f"'{hotkey}' is reserved as the panic/stop-all key")
        existing = self._bound.get(hotkey)
        if existing is not None and existing != rotation_name:
            raise ValueError(f"'{hotkey}' is already bound to '{existing}'")
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
            keyboard.remove_hotkey(hotkey)
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
        self._enabled = False

    def capture_next_key(self) -> str:
        """BLOCKING -- call from a background thread only, never the Tk main thread.

        Temporarily suspends every bound rotation hotkey (not the panic key) so the
        key being pressed to bind doesn't also fire whatever rotation currently owns
        it, waits for the next physical key-down, then restores every binding.
        """
        if self._enabled:
            for hotkey in list(self._bound.keys()):
                keyboard.remove_hotkey(hotkey)
        try:
            while True:
                event = keyboard.read_event(suppress=False)
                if event.event_type == keyboard.KEY_DOWN:
                    return event.name
        finally:
            if self._enabled:
                for hotkey, name in self._bound.items():
                    self._register_hotkey(hotkey, name)
