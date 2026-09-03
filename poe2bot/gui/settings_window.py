import tkinter as tk
from tkinter import ttk

from poe2bot.gui import geometry


class SettingsWindow(tk.Toplevel):
    """App-wide settings that don't belong to any one rotation: which input
    device is active, the light/dark theme, and a shortcut to the Activity
    window. `master` is the App instance -- every control here just drives
    an existing App method/variable rather than owning any state of its own,
    so closing and reopening this window loses nothing."""

    def __init__(self, master):
        super().__init__(master)
        self._master = master
        self.title("Settings")
        self.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)

        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        device_frame = ttk.LabelFrame(container, text="Active Device", padding=8)
        device_frame.pack(fill="x")
        ttk.Radiobutton(device_frame, text="Keyboard", variable=master.active_device_var,
                        value="keyboard", command=master._on_active_device_changed).pack(anchor="w")
        ttk.Radiobutton(device_frame, text="Controller", variable=master.active_device_var,
                        value="controller", command=master._on_active_device_changed).pack(anchor="w")

        appearance_frame = ttk.LabelFrame(container, text="Appearance", padding=8)
        appearance_frame.pack(fill="x", pady=(8, 0))
        self._theme_btn = ttk.Button(appearance_frame, command=master._toggle_theme)
        self._theme_btn.pack(fill="x")
        self.refresh_theme_label()

        window_frame = ttk.LabelFrame(container, text="Windows", padding=8)
        window_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(window_frame, text="Show Activity Window",
                   command=master._on_show_activity_window_clicked).pack(fill="x")

        geometry.size_window_to_contents(self)

    def refresh_theme_label(self):
        """Reflects the *current* theme rather than a static "Toggle..."
        label -- called once at construction and again from
        App._toggle_theme() (this window is created once and reused via
        deiconify(), so a label baked in only at construction would go
        stale after the first toggle)."""
        other = "Light" if self._master._theme == "dark" else "Dark"
        self._theme_btn.configure(text=f"Switch to {other} Mode")
