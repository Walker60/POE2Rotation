import threading

from poe2bot import controller
from poe2bot.gui import dialogs as messagebox


class ControllerDriverMixin:
    """The Settings window's Windows-only "Install ViGEmBus Driver..."
    button -- see controller.install_vigembus's docstring for why the
    packaged Windows build needs this at all (unlike a from-source run,
    where `pip install -r requirements.txt` already does it automatically).
    Same "Settings button -> background thread -> status_queue -> result
    dialog" shape as UpdaterMixin (poe2bot/gui/updater_ui.py), deliberately
    modeled after it."""

    def _on_install_vigembus_clicked(self):
        if not messagebox.askyesno(
                "Install ViGEmBus Driver",
                "This installs ViGEmBus, the virtual-controller driver a controller-encoded "
                "step's button press needs -- a one-time install, needing your approval via a "
                "Windows administrator prompt. Continue?",
                parent=self._vigembus_dialog_parent()):
            return
        self._set_vigembus_button_state("disabled", "Installing...")
        threading.Thread(target=self._install_vigembus_worker, daemon=True).start()

    def _install_vigembus_worker(self):
        try:
            controller.install_vigembus()
        except Exception as e:
            self.status_queue.put(("__vigembus_install_failed__", str(e)))
            return
        self.status_queue.put(("__vigembus_install_done__", None))

    def _on_vigembus_install_failed(self, message: str):
        self._set_vigembus_button_state("normal", "Install ViGEmBus Driver...")
        messagebox.showerror(
            "Install failed", f"Could not install ViGEmBus:\n{message}",
            parent=self._vigembus_dialog_parent())

    def _on_vigembus_install_done(self, _payload):
        self._set_vigembus_button_state("normal", "Install ViGEmBus Driver...")
        messagebox.showinfo(
            "Installed",
            "ViGEmBus installed successfully. Controller-encoded steps should work now -- no "
            "restart needed.",
            parent=self._vigembus_dialog_parent())

    def _set_vigembus_button_state(self, state: str, text: str):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.set_vigembus_button_state(state, text)

    def _vigembus_dialog_parent(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            return self.settings_window
        return None
