"""Tiny shared helper for the "log a warning once, then stay quiet until it
clears" pattern -- focus.py's three module-level warning flags and
controller_input.py's _warned_disconnected each reimplemented this
independently as a plain bool with its own if/set/clear."""


class WarnOnce:
    def __init__(self):
        self.already_warned = False

    def warn_once(self, log_fn) -> None:
        """Calls log_fn() (no arguments) only the first time this is called
        since the last clear()."""
        if not self.already_warned:
            log_fn()
            self.already_warned = True

    def clear(self) -> None:
        self.already_warned = False
