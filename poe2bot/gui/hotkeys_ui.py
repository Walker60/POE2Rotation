import threading

from poe2bot.gui import dialogs as messagebox
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
    """

    # ---- hotkey binding ----------------------------------------------------

    def _set_bind_buttons_enabled(self, enabled: bool):
        """capture_next_key()/capture_next_controller_button()/
        capture_next_mouse_button() are blocking calls sharing one lock
        (HotkeyManager._capture_lock) and only one can meaningfully be in
        flight at a time -- disabling all six buttons, not just the one
        clicked, makes that exclusivity visible rather than letting a second
        click silently queue up behind the first with no feedback. The four
        _KEY_BIND_SPECS buttons are looped rather than named individually so
        adding a new bindable key only means adding a spec entry, not also
        remembering to list its button here."""
        state = "normal" if enabled else "disabled"
        for spec in self._KEY_BIND_SPECS.values():
            getattr(self, spec["button_attr"]).config(state=state)
        self.capture_step_key_btn.config(state=state)
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
        "hotkey": dict(button_attr="bind_hotkey_btn", label_var_attr="hotkey_label_var",
                       pending_attr="pending_hotkey", sentinel="__capture__",
                       bound_label="Bind Hotkey..."),
        "cancel": dict(button_attr="bind_cancel_btn", label_var_attr="cancel_key_label_var",
                       pending_attr="pending_cancel_key", sentinel="__cancel_capture__",
                       bound_label="Bind Cancel Key..."),
        "reset": dict(button_attr="bind_reset_btn", label_var_attr="reset_key_label_var",
                      pending_attr="pending_reset_key", sentinel="__reset_capture__",
                      bound_label="Bind Reset Key..."),
        "pause": dict(button_attr="bind_pause_btn", label_var_attr="pause_key_label_var",
                      pending_attr="pending_pause_key", sentinel="__pause_capture__",
                      bound_label="Bind Pause Key..."),
    }

    def _on_bind_key_clicked(self, kind: str):
        getattr(self, self._KEY_BIND_SPECS[kind]["button_attr"]).config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_key_worker, args=(kind,), daemon=True).start()

    def _capture_key_worker(self, kind: str):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put((self._KEY_BIND_SPECS[kind]["sentinel"], key))

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
