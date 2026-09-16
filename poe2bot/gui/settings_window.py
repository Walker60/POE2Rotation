import sys
import tkinter as tk
from tkinter import ttk

from poe2bot import updater
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui import geometry
from poe2bot.gui.controller_layouts import CONTROLLER_TYPE_LABELS


class SettingsWindow(tk.Toplevel):
    """App-wide settings that don't belong to any one rotation: which input
    device is active, the light/dark theme, the four env-var-backed values
    poe2bot/config.py otherwise only reads at startup, and shortcuts to the
    Activity/Hotkey Map windows and the logs folder. `master` is the App
    instance -- every control here just drives an existing App method/
    variable rather than owning any state of its own, so closing and
    reopening this window loses nothing."""

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

        controller_frame = ttk.LabelFrame(container, text="Controller", padding=8)
        controller_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(controller_frame, text="Controller type:").pack(anchor="w")
        controller_type_combo = ttk.Combobox(
            controller_frame, textvariable=master.controller_type_var,
            values=list(CONTROLLER_TYPE_LABELS.values()), state="readonly")
        controller_type_combo.pack(fill="x", pady=(2, 0))
        controller_type_combo.bind("<<ComboboxSelected>>", master._on_controller_type_changed)
        ttk.Label(controller_frame, foreground="gray", wraplength=320, justify="left",
                  text="Which labels/layout \"Map Controller Button\" (in the step editor) shows -- "
                       "doesn't change which controller is actually read.").pack(anchor="w", pady=(4, 0))

        appearance_frame = ttk.LabelFrame(container, text="Appearance", padding=8)
        appearance_frame.pack(fill="x", pady=(8, 0))
        self._theme_btn = ttk.Button(appearance_frame, command=master._toggle_theme)
        self._theme_btn.pack(fill="x")
        self.refresh_theme_label()

        window_frame = ttk.LabelFrame(container, text="Windows", padding=8)
        window_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(window_frame, text="Show Activity Window",
                   command=master._on_show_activity_window_clicked).pack(fill="x")
        ttk.Button(window_frame, text="Show Hotkey Map...",
                   command=master._on_show_hotkey_map_clicked).pack(fill="x", pady=(4, 0))
        ttk.Button(window_frame, text="Open Logs Folder",
                   command=master._on_view_logs_clicked).pack(fill="x", pady=(4, 0))

        if updater.IS_SUPPORTED:
            self._build_updates_section(container)

        self._build_advanced_section(container)

        geometry.size_window_to_contents(self)

    def _build_updates_section(self, container):
        """Linux/Steam Deck only (see updater.IS_SUPPORTED) -- Windows has
        no update mechanism yet, so this section doesn't exist there at
        all, rather than showing a button that can't do anything."""
        updates_frame = ttk.LabelFrame(container, text="Updates", padding=8)
        updates_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(updates_frame, text=f"Current version: {updater.current_version()}",
                  foreground="gray").pack(anchor="w")
        self._update_btn = ttk.Button(
            updates_frame, text="Check for Updates...", command=self._master._on_check_for_updates_clicked)
        self._update_btn.pack(fill="x", pady=(4, 0))

    def set_update_button_state(self, state: str, text: str):
        """Called from UpdaterMixin (poe2bot/gui/updater_ui.py) as a check/
        download progresses -- kept as a method (like refresh_theme_label)
        rather than having that mixin reach into self._update_btn directly,
        since this window is created once and reused via deiconify(), so
        whatever's mid-flight when it's hidden needs a stable handle to
        keep updating."""
        self._update_btn.config(state=state, text=text)

    def _build_advanced_section(self, container):
        """The four settings poe2bot/config.py otherwise only reads from
        environment variables at startup -- editable here instead, applied
        live and persisted to app_state.json the moment Save succeeds."""
        advanced = ttk.LabelFrame(container, text="Advanced", padding=8)
        advanced.pack(fill="x", pady=(8, 0))

        self._process_var = tk.StringVar(value=self._master.game_process_name)
        self._panic_key_var = tk.StringVar(value=self._master.panic_key)
        self._min_tap_var = tk.StringVar(value=str(self._master.controller_min_tap_ms))
        self._controller_index_var = tk.StringVar(value=str(self._master.controller_index))

        rows = (
            ("Target process:", self._process_var,
             "Exact .exe name from Task Manager > Details -- e.g. PathOfExileSteam.exe"),
            ("Panic key:", self._panic_key_var,
             "Instantly stops every running rotation -- a keyboard key name, e.g. f12"),
            ("Controller min tap (ms):", self._min_tap_var,
             "Floor for an instant controller-encoded tap with no Hold configured"),
            ("Controller index:", self._controller_index_var,
             "Which controller is your real, physically-held one -- an XInput slot (0-3) on "
             "Windows, or a position in the list of detected gamepads on Linux"),
        )
        for label, var, hint in rows:
            row = ttk.Frame(advanced)
            row.pack(fill="x", pady=(0, 6))
            ttk.Label(row, text=label, width=22, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=var, width=20).pack(side="left")
            ttk.Label(advanced, text=hint, foreground="gray").pack(anchor="w", pady=(0, 6))

        self._advanced_error_var = tk.StringVar(value="")
        self._advanced_error_label = ttk.Label(
            advanced, textvariable=self._advanced_error_var, foreground="#ff5555", wraplength=320, justify="left")
        # Not packed here -- only shown while there's an actual validation problem.

        ttk.Button(advanced, text="Save", command=self._on_save_advanced_clicked).pack(fill="x", pady=(4, 0))

    def _on_save_advanced_clicked(self):
        process_name = self._process_var.get().strip()
        panic_key = self._panic_key_var.get().strip()
        problems = []
        if not process_name:
            problems.append("Target process cannot be blank.")
        if not panic_key:
            problems.append("Panic key cannot be blank.")
        try:
            min_tap_ms = int(self._min_tap_var.get())
            if min_tap_ms < 0:
                problems.append("Controller min tap (ms) cannot be negative.")
        except ValueError:
            problems.append("Controller min tap (ms) must be a whole number.")
            min_tap_ms = None
        try:
            controller_index = int(self._controller_index_var.get())
            # 0-3 on Windows (XInput supports exactly 4 slots); Linux has no
            # fixed ceiling (an index into however many gamepads evdev finds),
            # so only reject a negative index there.
            max_index = 3 if sys.platform == "win32" else None
            if controller_index < 0 or (max_index is not None and controller_index > max_index):
                problems.append(
                    "Controller index must be between 0 and 3 (XInput supports exactly 4 slots)."
                    if max_index is not None else "Controller index cannot be negative.")
        except ValueError:
            problems.append("Controller index must be a whole number.")
            controller_index = None

        if problems:
            self._advanced_error_var.set(" ".join(problems))
            self._advanced_error_label.pack(anchor="w", pady=(0, 6))
            return
        self._advanced_error_var.set("")
        self._advanced_error_label.pack_forget()

        self._master._on_advanced_settings_changed(process_name, panic_key, min_tap_ms, controller_index)
        messagebox.showinfo("Settings saved", "Advanced settings applied.", parent=self)

    def refresh_theme_label(self):
        """Reflects the *current* theme rather than a static "Toggle..."
        label -- called once at construction and again from
        App._toggle_theme() (this window is created once and reused via
        deiconify(), so a label baked in only at construction would go
        stale after the first toggle)."""
        other = "Light" if self._master._theme == "dark" else "Dark"
        self._theme_btn.configure(text=f"Switch to {other} Mode")
