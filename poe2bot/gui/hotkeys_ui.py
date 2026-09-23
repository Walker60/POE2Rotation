import threading

from poe2bot import controller_input
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui.controller_map_window import ControllerMapWindow
from poe2bot.hotkeys import display_name


class HotkeysMixin:
    """Binding UI for a rotation's trigger hotkey, cancel key, reset key, and
    pause key -- four near-identical bindings, parameterized by
    _KEY_BIND_SPECS below. Mixed into App (see poe2bot/gui/app.py).

    Each "Bind ... " button starts a background thread (_capture_key_worker)
    that makes a *blocking* call to self.hotkey_manager.capture_next_key(),
    then hops back to the Tk thread by pushing a sentinel tuple onto
    self.status_queue -- the same queue RotationManager's status callback
    uses. App's _poll_status_queue (in app.py) special-cases each spec's
    sentinel ("__capture__"/"__cancel_capture__"/"__reset_capture__"/
    "__pause_capture__") and dispatches to the corresponding
    _on_*_captured method below. That dispatch table lives in app.py, not
    here, so a change to these sentinel names must be kept in sync there.

    Each row's "Map..." button is the same click-to-choose controller
    button picker the step editor's Map Controller Button uses (see
    poe2bot/gui/controller_map_window.py and _on_map_key_clicked below) --
    an alternative to the "Bind..." physical-press capture above, for
    exactly the same reason the step editor needed one: a Steam Deck's own
    built-in controls can't be physically captured unless poe2bot is
    launched through Steam (see README's Steam Deck section).
    """

    # ---- hotkey binding ----------------------------------------------------

    def _set_bind_buttons_enabled(self, enabled: bool):
        """capture_next_key()/capture_next_mouse_button() are blocking calls
        sharing one lock (HotkeyManager._capture_lock) and only one can
        meaningfully be in flight at a time -- disabling all nine buttons,
        not just the one clicked, makes that exclusivity visible rather than
        letting a second click silently queue up behind the first with no
        feedback. The four _KEY_BIND_SPECS buttons (bind AND map) are looped
        rather than named individually so adding a new bindable key only
        means adding a spec entry, not also remembering to list its buttons
        here. (The step editor's Map Controller Button isn't included -- it
        opens its own modal window instead of contending for this same
        lock; see StepEditorMixin._on_map_step_key_clicked. Each row's own
        "Map..." button here is included even though it's the same kind of
        modal picker, purely so the row reads as a single, consistently
        disabled unit while any capture is in flight -- see
        _on_map_key_clicked.)"""
        state = "normal" if enabled else "disabled"
        for spec in self._KEY_BIND_SPECS.values():
            getattr(self, spec["button_attr"]).config(state=state)
            getattr(self, spec["map_button_attr"]).config(state=state)
        self.capture_step_mouse_btn.config(state=state)

    def _on_bind_hotkey_clicked(self):
        self._on_bind_key_clicked("hotkey")

    def _confirm_hotkey_share_if_needed(self, hotkey) -> bool:
        """True if it's fine to proceed with `hotkey` as this rotation's
        trigger -- either nothing else currently uses it, or the user just
        confirmed sharing it. This is the one interactive confirmation that
        survives from the old "Save Rotation" button, kept right here at the
        moment a NEW hotkey is actually being chosen -- asking it from the
        generic autosave path instead (AutosaveMixin._persist_rotation_to_disk)
        would re-prompt on every single keystroke of an unrelated field for
        as long as the hotkey stays shared, since nothing about a Name/Delay
        edit would ever change the answer."""
        if not hotkey:
            return True
        folder = self.folder_var.get().strip()
        # bound_to() only reflects currently-live (in-scope) bindings -- also warn
        # about another rotation in the SAME folder sharing this hotkey even if
        # that folder isn't the active one right now, since it's just as real a
        # conflict the moment either rotation's folder becomes active.
        same_folder_conflicts = [
            r.name for r in self.rotations.values()
            if r.name != self.editing_original_name and r.folder == folder and r.hotkey == hotkey]
        sharing_with = list(dict.fromkeys(
            [n for n in self.hotkey_manager.bound_to(hotkey) if n != self.editing_original_name]
            + same_folder_conflicts))
        if not sharing_with:
            return True
        return messagebox.askyesno(
            "Hotkey already in use",
            f"'{display_name(hotkey)}' is already bound to {', '.join(sharing_with)}. "
            "Also bind it to this rotation?")

    def _on_hotkey_captured(self, key: str):
        spec = self._KEY_BIND_SPECS["hotkey"]
        getattr(self, spec["button_attr"]).config(text=spec["bound_label"])
        self._set_bind_buttons_enabled(True)
        if not self._confirm_hotkey_share_if_needed(key):
            return  # declined -- leave the old hotkey in place, nothing to save
        # Saves immediately (like _on_unbind_clicked below) so binding a key
        # takes effect as a single click/press -- if the rotation isn't valid yet
        # (e.g. a brand new one with no steps), _autosave shows its usual inline
        # error and the capture is simply left pending until it is.
        self.pending_hotkey = key
        self.hotkey_label_var.set(display_name(key))
        self._autosave()

    def _on_unbind_clicked(self):
        # Clears and immediately saves, so freeing this hotkey up for another
        # rotation is a single click instead of unbind-then-remember-to-save.
        self._on_clear_key("hotkey")

    # ---- cancel/reset/pause keys --------------------------------------------
    # These three are otherwise identical -- shared machinery lives in
    # _on_bind_key_clicked/_capture_key_worker/_on_key_captured/_on_clear_key
    # below, parameterized by _KEY_BIND_SPECS. The hotkey binding above reuses
    # the bind/capture half too, but keeps its own _on_hotkey_captured/
    # _on_unbind_clicked since binding a trigger hotkey also needs the
    # sharing-confirmation dialog above.

    _KEY_BIND_SPECS = {
        "hotkey": dict(button_attr="bind_hotkey_btn", map_button_attr="map_hotkey_btn",
                       label_var_attr="hotkey_label_var", pending_attr="pending_hotkey",
                       sentinel="__capture__", bound_label="Bind Hotkey..."),
        "cancel": dict(button_attr="bind_cancel_btn", map_button_attr="map_cancel_btn",
                       label_var_attr="cancel_key_label_var", pending_attr="pending_cancel_key",
                       sentinel="__cancel_capture__", bound_label="Bind Cancel Key..."),
        "reset": dict(button_attr="bind_reset_btn", map_button_attr="map_reset_btn",
                      label_var_attr="reset_key_label_var", pending_attr="pending_reset_key",
                      sentinel="__reset_capture__", bound_label="Bind Reset Key..."),
        "pause": dict(button_attr="bind_pause_btn", map_button_attr="map_pause_btn",
                      label_var_attr="pause_key_label_var", pending_attr="pending_pause_key",
                      sentinel="__pause_capture__", bound_label="Bind Pause Key..."),
    }

    # kind -> name of the method that applies a chosen value to that kind's own
    # pending_attr/label/autosave -- reused as-is by _on_map_key_clicked below, so
    # a controller button picked via the map window goes through exactly the same
    # path a physical capture's sentinel dispatches to in app.py (including
    # "hotkey"'s own sharing-conflict confirmation, which the other three don't
    # need -- see _on_hotkey_captured).
    _MAP_CAPTURE_HANDLERS = {
        "hotkey": "_on_hotkey_captured",
        "cancel": "_on_cancel_key_captured",
        "reset": "_on_reset_key_captured",
        "pause": "_on_pause_key_captured",
    }

    def _on_bind_key_clicked(self, kind: str):
        # At most once per session (like CalibrationMixin's own _calibration_hint_shown) --
        # this is a heads-up, not something to nag about on every single "Bind..." click,
        # e.g. while trying it for several different rows in a row. Only ever true on
        # Linux; a no-op check everywhere else (see linux_no_gamepad_visible's docstring).
        if not self._no_gamepad_hint_shown and controller_input.linux_no_gamepad_visible():
            self._no_gamepad_hint_shown = True
            messagebox.showinfo(
                "No gamepad-like device found",
                "evdev doesn't currently see any gamepad-like /dev/input device on this "
                "system. If you're trying to physically press one of the Steam Deck's own "
                "built-in buttons, this is expected unless poe2bot is launched through Steam "
                "(or Handheld Daemon is running) -- see README's Steam Deck section. A real "
                "USB/Bluetooth controller doesn't have this problem. In the meantime, click "
                "\"Map...\" instead to choose a controller button from a list, or just press "
                "a keyboard key/mouse button here.",
                parent=self)
        getattr(self, self._KEY_BIND_SPECS[kind]["button_attr"]).config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_key_worker, args=(kind,), daemon=True).start()

    def _capture_key_worker(self, kind: str):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put((self._KEY_BIND_SPECS[kind]["sentinel"], key))

    def _on_map_key_clicked(self, kind: str):
        """The "Map..." button next to each row's "Bind...": opens the same
        click-to-choose controller button picker the step editor's Map
        Controller Button uses, applying whatever's picked through this
        kind's own *_captured handler (see _MAP_CAPTURE_HANDLERS) exactly as
        if it had been physically captured. Unlike _on_bind_key_clicked,
        this doesn't touch HotkeyManager._capture_lock at all -- the map
        window is its own modal (grab_set), needing no worker thread or
        status_queue hop -- but the row is still disabled first for the
        same one-capture-at-a-time clarity as a physical Bind (see
        _set_bind_buttons_enabled), in case one is already in flight, and
        re-enabled via on_close whether the user picked a button or backed
        out -- the picked-a-button path also gets re-enabled by its own
        *_captured handler, so on_close's call is redundant (not harmful)
        in that case, but it's the only one that fires at all on Cancel."""
        self._set_bind_buttons_enabled(False)
        handler = getattr(self, self._MAP_CAPTURE_HANDLERS[kind])
        ControllerMapWindow(self, self.controller_type, handler,
                             on_close=lambda: self._set_bind_buttons_enabled(True))

    def _on_key_captured(self, kind: str, key: str):
        spec = self._KEY_BIND_SPECS[kind]
        setattr(self, spec["pending_attr"], key)
        getattr(self, spec["label_var_attr"]).set(display_name(key))
        getattr(self, spec["button_attr"]).config(text=spec["bound_label"])
        self._set_bind_buttons_enabled(True)
        self._autosave()

    def _on_clear_key(self, kind: str):
        spec = self._KEY_BIND_SPECS[kind]
        setattr(self, spec["pending_attr"], None)
        getattr(self, spec["label_var_attr"]).set(display_name(None))
        self._autosave()

    def _on_bind_cancel_clicked(self):
        self._on_bind_key_clicked("cancel")

    def _on_cancel_key_captured(self, key: str):
        self._on_key_captured("cancel", key)

    def _on_clear_cancel_key(self):
        self._on_clear_key("cancel")

    def _on_bind_reset_clicked(self):
        self._on_bind_key_clicked("reset")

    def _on_reset_key_captured(self, key: str):
        self._on_key_captured("reset", key)

    def _on_clear_reset_key(self):
        self._on_clear_key("reset")

    def _on_bind_pause_clicked(self):
        self._on_bind_key_clicked("pause")

    def _on_pause_key_captured(self, key: str):
        self._on_key_captured("pause", key)

    def _on_clear_pause_key(self):
        self._on_clear_key("pause")
