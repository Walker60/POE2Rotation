import threading

import keyboard

from poe2bot import controller, updater
from poe2bot.gui import dialogs as messagebox


class UpdaterMixin:
    """The Settings window's "Updates" section (frozen/PyInstaller builds
    only -- see poe2bot/updater.py's IS_SUPPORTED, and SettingsWindow, which
    hides this section entirely when that's False -- works the same way on
    both the Linux/Steam Deck build and the Windows build): checking for a
    newer build, downloading and installing it, and restarting into it.
    Mixed into App (see poe2bot/gui/app.py) -- the actual check/download
    runs on a background thread, hopping results back to the Tk thread via
    self.status_queue exactly the way HotkeysMixin's/StepEditorMixin's own
    capture flows already do (see app.py's _CAPTURE_SENTINEL_HANDLERS)."""

    def _on_check_for_updates_clicked(self):
        self._set_update_button_state("disabled", "Checking...")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self):
        try:
            info = updater.check_for_update()
        except updater.UpdateCheckFailed as e:
            self.status_queue.put(("__update_check_failed__", str(e)))
            return
        self.status_queue.put(("__update_check_done__", info))

    def _on_update_check_failed(self, message: str):
        self._set_update_button_state("normal", "Check for Updates...")
        messagebox.showerror("Update check failed", message, parent=self._update_dialog_parent())

    def _on_update_check_done(self, info):
        self._set_update_button_state("normal", "Check for Updates...")
        if info is None:
            messagebox.showinfo(
                "Up to date", f"You're running the latest version ({updater.current_version()}).",
                parent=self._update_dialog_parent())
            return
        if not messagebox.askyesno(
                "Update available",
                f"Version {info.version} is available (you have {updater.current_version()}).\n\n"
                f"Download and install it now? The app will need to restart afterward.",
                parent=self._update_dialog_parent()):
            return
        self._set_update_button_state("disabled", "Downloading...")
        threading.Thread(target=self._install_update_worker, args=(info,), daemon=True).start()

    def _install_update_worker(self, info):
        def progress(message, fraction=None):
            self.status_queue.put(("__update_install_progress__", (message, fraction)))
        try:
            updater.download_and_install(info, progress_callback=progress)
        except Exception as e:
            self.status_queue.put(("__update_install_failed__", str(e)))
            return
        self.status_queue.put(("__update_install_done__", info.version))

    def _on_update_install_progress(self, payload):
        message, fraction = payload
        self._set_update_button_state("disabled", message)
        self._set_update_progress(fraction)

    def _on_update_install_failed(self, message: str):
        self._set_update_button_state("normal", "Check for Updates...")
        self._set_update_progress(None)
        messagebox.showerror("Update failed", f"Could not install the update:\n{message}",
                              parent=self._update_dialog_parent())

    def _on_update_install_done(self, version: str):
        self._set_update_button_state("normal", "Check for Updates...")
        self._set_update_progress(None)
        if not messagebox.askyesno(
                "Update installed", f"Updated to {version}. Restart now to use the new version?",
                parent=self._update_dialog_parent()):
            return
        # The same cleanup _on_close() does, minus destroying the Tk root --
        # a successful exec() below replaces this whole process, so there'd
        # be nothing left to have destroyed anyway; left undestroyed
        # specifically so a (very unlikely) exec failure still has a live
        # window to report the error in, rather than the app just vanishing.
        self.rotation_manager.stop_all()
        keyboard.unhook_all()
        controller.release_all()
        try:
            updater.restart_into_new_version()
        except OSError as e:
            messagebox.showerror(
                "Restart failed",
                f"The update installed, but couldn't restart automatically:\n{e}\n\n"
                f"Please close and reopen the app yourself.",
                parent=self._update_dialog_parent())

    def _set_update_button_state(self, state: str, text: str):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.set_update_button_state(state, text)

    def _set_update_progress(self, fraction: "float | None"):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.set_update_progress(fraction)

    def _update_dialog_parent(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            return self.settings_window
        return None
