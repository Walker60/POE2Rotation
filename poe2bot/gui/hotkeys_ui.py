import threading

from poe2bot import storage
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

    def _on_hotkey_captured(self, key: str):
        # Saves immediately (like _on_unbind_clicked below) so binding a key
        # takes effect as a single click/press instead of
        # capture-then-remember-to-Save -- if the rotation isn't valid yet
        # (e.g. a brand new one with no steps), _save_rotation shows its usual
        # error and the capture is simply left pending until it is.
        self.pending_hotkey = key
        self.hotkey_label_var.set(display_name(key))
        self.bind_hotkey_btn.config(text="Bind Hotkey...")
        self._set_bind_buttons_enabled(True)
        self._save_rotation()

    def _on_unbind_clicked(self):
        # Clears and immediately saves, so freeing this hotkey up for another
        # rotation is a single click instead of unbind-then-remember-to-Save.
        self.pending_hotkey = None
        self.hotkey_label_var.set(display_name(None))
        self._save_rotation()

    def _unbind_all_rotations(self):
        bound = [r for r in self.rotations.values() if r.hotkey]
        if not bound:
            messagebox.showinfo("Unbind all", "No rotations currently have a hotkey bound.")
            return
        if not messagebox.askyesno(
                "Unbind all rotations",
                f"Remove the hotkey binding from all {len(bound)} bound rotation(s)? "
                "Each will be saved immediately.", danger=True):
            return
        for rotation in bound:
            self.hotkey_manager.unbind(rotation.name)
            rotation.hotkey = None
            storage.save_rotation(rotation)
        if self.editing_original_name in self.rotations:
            self.pending_hotkey = None
            self.hotkey_label_var.set(display_name(None))
        self._refresh_rotation_tree()

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
        self._save_rotation()

    def _on_clear_cancel_key(self):
        self.pending_cancel_key = None
        self.cancel_key_label_var.set(display_name(None))
        self._save_rotation()

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
        self._save_rotation()

    def _on_clear_reset_key(self):
        self.pending_reset_key = None
        self.reset_key_label_var.set(display_name(None))
        self._save_rotation()

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
        self._save_rotation()

    def _on_clear_pause_key(self):
        self.pending_pause_key = None
        self.pause_key_label_var.set(display_name(None))
        self._save_rotation()
