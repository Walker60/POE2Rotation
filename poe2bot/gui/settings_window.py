import sys
import tkinter as tk
from tkinter import ttk

from poe2bot import updater
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui import geometry
from poe2bot.gui.controller_layouts import CONTROLLER_TYPE_LABELS
from poe2bot.hotkeys import display_name


class SettingsWindow(tk.Toplevel):
    """App-wide settings that don't belong to any one rotation: which input
    device is active, whether the game needs OS focus to fire at all, the
    controller-type label preference, the light/dark theme, the four
    env-var-backed values poe2bot/config.py otherwise only reads at
    startup, and shortcuts to the Activity/Hotkey Map windows and the logs
    folder. `master` is the App instance -- every control here just drives
    an existing App method/variable rather than owning any state of its
    own, so closing and reopening this window loses nothing."""

    def __init__(self, master):
        super().__init__(master)
        self._master = master
        self.title("Settings")
        self._canvas = None  # set below -- a plain tk.Canvas, kept in sync with the
                              # theme by hand the same way App._sync_root_background does
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)

        container = self._build_scroll_area()

        device_frame = ttk.LabelFrame(container, text="Active Device", padding=8)
        device_frame.pack(fill="x")
        ttk.Radiobutton(device_frame, text="Keyboard", variable=master.active_device_var,
                        value="keyboard", command=master._on_active_device_changed).pack(anchor="w")
        ttk.Radiobutton(device_frame, text="Controller", variable=master.active_device_var,
                        value="controller", command=master._on_active_device_changed).pack(anchor="w")

        safety_frame = ttk.LabelFrame(container, text="Safety", padding=8)
        safety_frame.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(safety_frame, text="Require game window focus to fire",
                         variable=master.require_game_focus_var,
                         command=master._on_require_game_focus_changed).pack(anchor="w")
        ttk.Label(safety_frame, foreground="gray", wraplength=320, justify="left",
                  text="Off lets every rotation fire no matter what window is focused -- only turn "
                       "this off for testing against a target that never takes OS focus itself, or "
                       "if the focus check is unreliable on your setup.").pack(anchor="w", pady=(4, 0))

        controller_frame = ttk.LabelFrame(container, text="Controller", padding=8)
        controller_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(controller_frame, text="Controller type:").pack(anchor="w")
        controller_type_combo = ttk.Combobox(
            controller_frame, textvariable=master.controller_type_var,
            values=list(CONTROLLER_TYPE_LABELS.values()), state="readonly")
        controller_type_combo.pack(fill="x", pady=(2, 0))
        controller_type_combo.bind("<<ComboboxSelected>>", master._on_controller_type_changed)
        ttk.Label(controller_frame, foreground="gray", wraplength=320, justify="left",
                  text="Which labels/layout every controller button map (the step editor's \"Map "
                       "Controller Button\" and each hotkey row's \"Map...\") shows -- doesn't change "
                       "which controller is actually read.").pack(anchor="w", pady=(4, 0))
        if sys.platform == "win32":
            self._vigembus_btn = ttk.Button(
                controller_frame, text="Install ViGEmBus Driver...",
                command=master._on_install_vigembus_clicked)
            self._vigembus_btn.pack(fill="x", pady=(8, 0))
            ttk.Label(controller_frame, foreground="gray", wraplength=320, justify="left",
                      text="Needed for a step to press a virtual controller button -- a "
                           "from-source run already installs this as a side effect of `pip install "
                           "-r requirements.txt`; this button covers the packaged .exe build, which "
                           "has no such install step of its own. Safe to click even if it's already "
                           "installed.").pack(anchor="w", pady=(4, 0))

        appearance_frame = ttk.LabelFrame(container, text="Appearance", padding=8)
        appearance_frame.pack(fill="x", pady=(8, 0))
        self._theme_btn = ttk.Button(appearance_frame, command=master._toggle_theme)
        self._theme_btn.pack(fill="x")
        self.refresh_theme()

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

        self._build_screen_grab_section(container)
        self._build_advanced_section(container)

        # A bare Canvas's own natural size has nothing to do with the size of
        # the item drawn inside it -- without this, size_window_to_contents
        # below would have nothing meaningful to measure and always fall back
        # to min_width/min_height, opening far smaller than the actual content
        # even on a big monitor with plenty of room to show all of it at once.
        container.update_idletasks()
        self._canvas.configure(width=container.winfo_reqwidth(), height=container.winfo_reqheight())
        geometry.size_window_to_contents(self, min_width=380, min_height=300)

    def _build_scroll_area(self) -> ttk.Frame:
        """Wraps every section below in a Canvas + Scrollbar (the standard Tk
        way to make an arbitrary stack of widgets scrollable, since ttk has
        no native scrollable frame -- same pattern as App's own step-editor
        scroll area, poe2bot/gui/app.py's _build_editor_scroll_area) so a
        window now resizable can be shrunk below its natural content height
        -- e.g. to fit the Steam Deck's small display -- without clipping
        anything. Returns the frame every section packs into, same as the
        plain `container` this replaced."""
        bg = ttk.Style().lookup("TFrame", "background")
        scroll_frame = ttk.Frame(self)
        scroll_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_frame, highlightthickness=0, bd=0, bg=bg)
        self._canvas = canvas
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        container = ttk.Frame(canvas, padding=12)
        scroll_window = canvas.create_window((0, 0), window=container, anchor="nw")

        def _on_container_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        container.bind("<Configure>", _on_container_configure)

        def _on_canvas_configure(event):
            # Keeps container (and everything packed fill="x" inside it) the
            # same width as the visible canvas, instead of shrink-wrapping to
            # its widest child.
            canvas.itemconfigure(scroll_window, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_mousewheel_linux(event):
            canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        def _bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel_linux)
            canvas.bind_all("<Button-5>", _on_mousewheel_linux)

        def _unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        # Bound/unbound on hover (not for the window's lifetime) so scrolling over
        # some other scrollable widget inside here isn't hijacked by this canvas.
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        return container

    def _build_updates_section(self, container):
        """Frozen/PyInstaller builds only (see updater.IS_SUPPORTED) -- a
        plain `python main.py` run has no build artifact to replace itself
        with, so this section doesn't exist there at all, rather than
        showing a button that can't do anything."""
        updates_frame = ttk.LabelFrame(container, text="Updates", padding=8)
        updates_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(updates_frame, text=f"Current version: {updater.current_version()}",
                  foreground="gray").pack(anchor="w")
        self._update_btn = ttk.Button(
            updates_frame, text="Check for Updates...", command=self._master._on_check_for_updates_clicked)
        self._update_btn.pack(fill="x", pady=(4, 0))
        # Determinate download progress bar -- created up front but never
        # packed here, so it takes up no space until set_update_progress()
        # first shows it (only while a download with a known size is
        # actually in flight; see that method).
        self._update_progress = ttk.Progressbar(updates_frame, orient="horizontal", mode="determinate", maximum=100)

    def set_update_button_state(self, state: str, text: str):
        """Called from UpdaterMixin (poe2bot/gui/updater_ui.py) as a check/
        download progresses -- kept as a method (like refresh_theme)
        rather than having that mixin reach into self._update_btn directly,
        since this window is created once and reused via deiconify(), so
        whatever's mid-flight when it's hidden needs a stable handle to
        keep updating."""
        self._update_btn.config(state=state, text=text)

    def set_update_progress(self, fraction: "float | None"):
        """Shows/updates/hides the download progress bar below the Updates
        button. Called from UpdaterMixin with a 0..1 fraction while the
        update download is in progress and the server reported a size, and
        with None the rest of the time (checking, extracting, installing,
        done, or failed) -- so the bar is only ever visible during an actual
        download, per its own docstring in updater.download_and_install()."""
        if fraction is None:
            self._update_progress.pack_forget()
            return
        if not self._update_progress.winfo_ismapped():
            self._update_progress.pack(fill="x", pady=(4, 0))
        self._update_progress.config(value=max(0.0, min(1.0, fraction)) * 100)

    def set_vigembus_button_state(self, state: str, text: str):
        """Same idea as set_update_button_state above, for ControllerDriverMixin
        (poe2bot/gui/controller_driver_ui.py)'s "Install ViGEmBus Driver..."
        button -- which only exists at all on Windows, see this window's own
        Controller section."""
        if sys.platform == "win32":
            self._vigembus_btn.config(state=state, text=text)

    def _build_screen_grab_section(self, container):
        """The Screen Grab Hotkey: arms a screenshot capture that a user can
        trigger from anywhere (even with the game focused) while adding or
        recalibrating an Image/Pixel condition, instead of the capture
        happening immediately when they click Add -- see
        CalibrationMixin._await_grab_hotkey_or_capture_now. Physical-press
        Bind.../controller-button Map... buttons, same pattern as the
        per-rotation Hotkey/Cancel/Reset/Pause rows (poe2bot/gui/app.py's
        _build_hotkeys_section) -- but this is one app-wide value, like the
        Panic Key below, not a per-rotation one, so it lives here instead."""
        grab_frame = ttk.LabelFrame(container, text="Screen Grab Hotkey", padding=8)
        grab_frame.pack(fill="x", pady=(8, 0))

        self._screen_grab_label_var = tk.StringVar(value=display_name(self._master.screen_grab_hotkey))
        row = ttk.Frame(grab_frame)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self._screen_grab_label_var, width=14, anchor="w").pack(side="left")
        self._screen_grab_bind_btn = ttk.Button(
            row, text="Bind...", command=self._master._on_bind_screen_grab_hotkey_clicked)
        self._screen_grab_bind_btn.pack(side="left", padx=(4, 0))
        self._screen_grab_map_btn = ttk.Button(
            row, text="Map...", command=self._master._on_map_screen_grab_hotkey_clicked)
        self._screen_grab_map_btn.pack(side="left", padx=(4, 0))
        self._screen_grab_unbind_btn = ttk.Button(
            row, text="Unbind", command=self._master._on_unbind_screen_grab_hotkey_clicked)
        self._screen_grab_unbind_btn.pack(side="left", padx=(4, 0))
        ttk.Label(grab_frame, foreground="gray", wraplength=320, justify="left",
                  text="Used when adding or recalibrating an Image or Pixel condition -- arms a "
                       "screen grab you can trigger from anywhere, instead of capturing immediately "
                       "when you click Add.").pack(anchor="w", pady=(4, 0))

    def set_screen_grab_buttons_enabled(self, enabled: bool):
        """Called from CalibrationMixin while a physical-press/controller-
        button capture for this row is in flight, same idea as
        set_update_button_state/set_vigembus_button_state above."""
        state = "normal" if enabled else "disabled"
        self._screen_grab_bind_btn.config(state=state)
        self._screen_grab_map_btn.config(state=state)
        self._screen_grab_unbind_btn.config(state=state)

    def refresh_screen_grab_label(self):
        self._screen_grab_label_var.set(display_name(self._master.screen_grab_hotkey))

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

    def refresh_theme(self):
        """Reflects the *current* theme -- both the "Switch to .../Dark Mode"
        button label and the plain tk.Canvas backing the scroll area (sv_ttk
        never restyles plain tk widgets on its own, same idea as App's own
        _sync_root_background) -- called once at construction and again from
        App._toggle_theme() (this window is created once and reused via
        deiconify(), so state baked in only at construction would go stale
        after the first toggle)."""
        other = "Light" if self._master._theme == "dark" else "Dark"
        self._theme_btn.configure(text=f"Switch to {other} Mode")
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)
            if self._canvas is not None:
                self._canvas.configure(bg=bg)
