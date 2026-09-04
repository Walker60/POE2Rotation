import threading

from poe2bot.gui import dialogs as messagebox
from poe2bot.hotkeys import display_name


class HotkeysMixin:
    """Binding UI for a rotation's trigger hotkey, cancel key, reset key, and
    pause key -- four near-identical blocks. Mixed into App (see
    poe2bot/gui/app.py).

    Each "Bind ... " button starts a background thread (_capture_*_worker)
    that makes a *blocking* call to self.hotkey_manager.capture_next_key(),
    then hops back to the Tk thread by pushing a sentinel tuple onto
    self.status_queue -- the same queue RotationManager's status callback
    uses. App's _poll_status_queue (in app.py) special-cases the sentinel
    names "__capture__"/"__cancel_capture__"/"__reset_capture__"/
    "__pause_capture__" and dispatches to the corresponding
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
        click silently queue up behind the first with no feedback."""
        state = "normal" if enabled else "disabled"
        self.bind_hotkey_btn.config(state=state)
        self.bind_cancel_btn.config(state=state)
        self.bind_reset_btn.config(state=state)
        self.bind_pause_btn.config(state=state)
        self.capture_step_key_btn.config(state=state)
        self.capture_step_mouse_btn.config(state=state)

    def _on_bind_hotkey_clicked(self):
        self.bind_hotkey_btn.config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_hotkey_worker, daemon=True).start()

    def _capture_hotkey_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__capture__", key))

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
        self.bind_hotkey_btn.config(text="Bind Hotkey...")
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
        self.pending_hotkey = None
        self.hotkey_label_var.set(display_name(None))
        self._autosave()

    # ---- cancel key -----------------------------------------------------------

    def _on_bind_cancel_clicked(self):
        self.bind_cancel_btn.config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_cancel_key_worker, daemon=True).start()

    def _capture_cancel_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__cancel_capture__", key))

    def _on_cancel_key_captured(self, key: str):
        self.pending_cancel_key = key
        self.cancel_key_label_var.set(display_name(key))
        self.bind_cancel_btn.config(text="Bind Cancel Key...")
        self._set_bind_buttons_enabled(True)
        self._autosave()

    def _on_clear_cancel_key(self):
        self.pending_cancel_key = None
        self.cancel_key_label_var.set(display_name(None))
        self._autosave()

    # ---- reset key ------------------------------------------------------------

    def _on_bind_reset_clicked(self):
        self.bind_reset_btn.config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_reset_key_worker, daemon=True).start()

    def _capture_reset_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__reset_capture__", key))

    def _on_reset_key_captured(self, key: str):
        self.pending_reset_key = key
        self.reset_key_label_var.set(display_name(key))
        self.bind_reset_btn.config(text="Bind Reset Key...")
        self._set_bind_buttons_enabled(True)
        self._autosave()

    def _on_clear_reset_key(self):
        self.pending_reset_key = None
        self.reset_key_label_var.set(display_name(None))
        self._autosave()

    # ---- pause key ------------------------------------------------------------

    def _on_bind_pause_clicked(self):
        self.bind_pause_btn.config(text="Press a key or click...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_pause_key_worker, daemon=True).start()

    def _capture_pause_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__pause_capture__", key))

    def _on_pause_key_captured(self, key: str):
        self.pending_pause_key = key
        self.pause_key_label_var.set(display_name(key))
        self.bind_pause_btn.config(text="Bind Pause Key...")
        self._set_bind_buttons_enabled(True)
        self._autosave()

    def _on_clear_pause_key(self):
        self.pending_pause_key = None
        self.pause_key_label_var.set(display_name(None))
        self._autosave()
