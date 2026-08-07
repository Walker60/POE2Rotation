import copy
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import keyboard
import pyautogui
import sv_ttk

from poe2bot import storage, templates
from poe2bot.executor import RotationManager
from poe2bot.hotkeys import HotkeyManager, display_name
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition, Rotation, Step, folder_path_problem, validate_rotation

log = get_logger()

STATUS_LABELS = {
    "idle": "",
    "running": " (running)",
    "waiting_focus": " (waiting for game focus)",
    "paused": " (paused)",
    "resetting": " (resetting)",
}


class RegionCaptureOverlay(tk.Toplevel):
    """Fullscreen, borderless, semi-transparent click-drag-release rectangle picker.

    Calls on_done(region) where region is (left, top, width, height) in absolute
    screen pixels, or on_done(None) if cancelled (Escape, or a release without a
    meaningfully-sized drag). Primary monitor only -- geometry is sized/positioned
    from winfo_screenwidth()/winfo_screenheight() at +0+0, so multi-monitor setups
    where the primary isn't at the origin, or where a skill icon sits on a secondary
    monitor, aren't supported by this overlay (known limitation).
    """

    def __init__(self, master, on_done):
        super().__init__(master)
        self.on_done = on_done
        self._start = None
        self._rect_id = None

        self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._on_cancel)

        self.grab_set()
        self.focus_force()

    def _on_press(self, event):
        self._start = (event.x_root, event.y_root)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2)

    def _on_drag(self, event):
        if self._start is None:
            return
        x0, y0 = self._start
        # This rectangle is purely cosmetic drag feedback in canvas-local coordinates;
        # the authoritative region below is computed entirely from x_root/y_root,
        # never from an assumed canvas-local == screen equivalence.
        self.canvas.coords(
            self._rect_id,
            x0 - self.winfo_rootx(), y0 - self.winfo_rooty(),
            event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())

    def _on_release(self, event):
        if self._start is None:
            self._finish(None)
            return
        x0, y0 = self._start
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 4 or height < 4:
            self._finish(None)
            return
        self._finish((left, top, width, height))

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, region):
        callback = self.on_done
        self.destroy()
        callback(region)


class PointCaptureOverlay(tk.Toplevel):
    """Fullscreen, borderless, semi-transparent single-click point picker.

    Calls on_done(point) where point is (x, y) in absolute screen pixels, or
    on_done(None) if cancelled (Escape). Primary monitor only, same limitation
    as RegionCaptureOverlay.
    """

    def __init__(self, master, on_done):
        super().__init__(master)
        self.on_done = on_done

        self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<Escape>", self._on_cancel)

        self.grab_set()
        self.focus_force()

    def _on_click(self, event):
        self._finish((event.x_root, event.y_root))

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, point):
        callback = self.on_done
        self.destroy()
        callback(point)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("POE2 Rotation Bot")

        sv_ttk.set_theme("dark")
        self._sync_root_background()

        self.status_queue = queue.Queue()
        self.rotation_manager = RotationManager(on_status_change=self._queue_status)
        self.hotkey_manager = HotkeyManager(self.rotation_manager)
        self.bot_enabled = True

        self.rotations = {}          # name -> Rotation, mirrors what's on disk
        self.editing_original_name = None    # name of rotation being edited, or None if new/unsaved
        self.editing_original_hotkey = None  # hotkey it had when editing started
        self.editing_steps = []              # working list[Step] for the form
        self.pending_hotkey = None            # hotkey chosen in this edit session (may be unchanged)
        self.pending_cancel_key = None        # cancel key chosen in this edit session (may be unchanged)
        self.pending_reset_key = None         # reset key chosen in this edit session (may be unchanged)
        self.pending_pause_key = None         # pause key chosen in this edit session (may be unchanged)
        self.step_ready_match_type = "image"  # "image" or "pixel" -- which method the step being edited uses
        self.step_ready_template = None       # cooldown-check filename pending for the step being edited
        self.step_ready_region = None         # (left, top, width, height) absolute screen coords, or None
        self.step_ready_pixel_pos = None      # (x, y) absolute screen coords, for pixel-match mode
        self.step_ready_pixel_color = None    # (r, g, b) expected "ready" color, for pixel-match mode
        self.step_buff_check = None           # Condition for the step being edited's buff check, or None
        self._step_clipboard = []             # list[Step], set by Copy -- lives on the App, so it
                                               # survives switching rotations (enables cross-rotation paste)
        self._drag_candidate = None    # list[(step_idx, cond_idx_or_None)] being dragged, or None
        self._drag_active = False      # True once the mouse has moved past the drag threshold
        self._drag_start_xy = (0, 0)
        self._drop_target_iid = None   # currently tag-highlighted row during a drag, if any

        self._build_widgets()
        self._load_rotations_from_disk()
        self._sweep_templates()
        self._refresh_rotation_tree()
        self._new_rotation()
        self._size_to_fit_contents()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._poll_status_queue)

    # ---- startup -----------------------------------------------------

    def _load_rotations_from_disk(self):
        for name, rotation in storage.load_all_rotations().items():
            self.rotations[name] = rotation
            self.rotation_manager.load(rotation)
            if rotation.hotkey:
                try:
                    self.hotkey_manager.bind(rotation.hotkey, rotation.name)
                except ValueError as e:
                    messagebox.showwarning("Hotkey conflict on startup", str(e))
            if rotation.cancel_key:
                self.hotkey_manager.set_cancel_key(rotation.name, rotation.cancel_key)
            if rotation.reset_key:
                self.hotkey_manager.set_reset_key(rotation.name, rotation.reset_key)
            if rotation.pause_key:
                self.hotkey_manager.set_pause_key(rotation.name, rotation.pause_key)

    def _size_to_fit_contents(self):
        """Open large enough to show every widget without the user having to
        resize on every launch. The step editor has steadily grown new groups
        (cooldown check, buff check, conditions, step actions, ...), so a
        hardcoded pixel size drifts stale each time -- asking Tk for the
        window's actual natural size (after everything is built and laid out)
        stays correct as more UI gets added later. Clamped to the screen so it
        never opens larger than the display, and never smaller than that
        natural size so nothing ends up clipped with no way to scroll to it."""
        self.update_idletasks()
        width = min(self.winfo_reqwidth(), self.winfo_screenwidth())
        height = min(self.winfo_reqheight(), self.winfo_screenheight())
        self.geometry(f"{width}x{height}")
        self.minsize(width, height)

    # ---- widget layout -------------------------------------------------

    def _build_widgets(self):
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="Rotations").pack(anchor="w")
        self.rotation_tree = ttk.Treeview(left, show="tree", height=20, selectmode="extended")
        self.rotation_tree.pack(fill="y", expand=True)
        self.rotation_tree.column("#0", width=220)
        self.rotation_tree.bind("<<TreeviewSelect>>", self._on_select_rotation)
        self.rotation_tree.bind("<Button-3>", self._on_rotation_tree_right_click)
        self._folder_nodes = {}   # folder path -> tree item id, rebuilt each _refresh_rotation_tree()

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="New", command=self._new_rotation).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Copy", command=self._copy_rotation).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Delete", command=self._delete_rotation).pack(side="left", expand=True, fill="x")

        right = ttk.Frame(self, padding=8)
        right.pack(side="left", fill="both", expand=True)

        name_row = ttk.Frame(right)
        name_row.pack(fill="x")
        ttk.Label(name_row, text="Name:").pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var).pack(side="left", fill="x", expand=True, padx=4)

        folder_row = ttk.Frame(right)
        folder_row.pack(fill="x", pady=(4, 0))
        ttk.Label(folder_row, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(folder_row, text="e.g. Bosses/HardMode (blank = ungrouped)",
                  foreground="gray").pack(side="left")

        mode_row = ttk.Frame(right)
        mode_row.pack(fill="x", pady=4)
        ttk.Label(mode_row, text="Mode:").pack(side="left")
        self.mode_var = tk.StringVar(value="once")
        ttk.Radiobutton(mode_row, text="Once", variable=self.mode_var, value="once").pack(side="left")
        ttk.Radiobutton(mode_row, text="Loop", variable=self.mode_var, value="loop").pack(side="left")

        hotkey_row = ttk.Frame(right)
        hotkey_row.pack(fill="x", pady=4)
        ttk.Label(hotkey_row, text="Hotkey:").pack(side="left")
        self.hotkey_label_var = tk.StringVar(value="(unbound)")
        ttk.Label(hotkey_row, textvariable=self.hotkey_label_var, width=12).pack(side="left", padx=4)
        self.bind_hotkey_btn = ttk.Button(hotkey_row, text="Bind Hotkey...", command=self._on_bind_hotkey_clicked)
        self.bind_hotkey_btn.pack(side="left")
        ttk.Button(hotkey_row, text="Unbind", command=self._on_unbind_clicked).pack(side="left", padx=(4, 0))
        ttk.Button(hotkey_row, text="Unbind All", command=self._unbind_all_rotations).pack(side="left", padx=(4, 0))

        cancel_row = ttk.Frame(right)
        cancel_row.pack(fill="x", pady=(0, 4))
        ttk.Label(cancel_row, text="Cancel Key:").pack(side="left")
        self.cancel_key_label_var = tk.StringVar(value="(unbound)")
        ttk.Label(cancel_row, textvariable=self.cancel_key_label_var, width=12).pack(side="left", padx=4)
        self.bind_cancel_btn = ttk.Button(
            cancel_row, text="Bind Cancel Key...", command=self._on_bind_cancel_clicked)
        self.bind_cancel_btn.pack(side="left")
        ttk.Button(cancel_row, text="Clear", command=self._on_clear_cancel_key).pack(side="left", padx=(4, 0))
        ttk.Label(cancel_row, text="e.g. your dodge key -- stops this rotation instantly if pressed",
                  foreground="gray").pack(side="left", padx=(8, 0))

        reset_row = ttk.Frame(right)
        reset_row.pack(fill="x", pady=(0, 4))
        ttk.Label(reset_row, text="Reset Key:").pack(side="left")
        self.reset_key_label_var = tk.StringVar(value="(unbound)")
        ttk.Label(reset_row, textvariable=self.reset_key_label_var, width=12).pack(side="left", padx=4)
        self.bind_reset_btn = ttk.Button(
            reset_row, text="Bind Reset Key...", command=self._on_bind_reset_clicked)
        self.bind_reset_btn.pack(side="left")
        ttk.Button(reset_row, text="Clear", command=self._on_clear_reset_key).pack(side="left", padx=(4, 0))
        ttk.Label(reset_row, text="Delay (ms)").pack(side="left", padx=(8, 2))
        self.reset_delay_var = tk.StringVar(value="0")
        ttk.Entry(reset_row, textvariable=self.reset_delay_var, width=6).pack(side="left")
        ttk.Label(reset_row, text="restarts this rotation from step 1 if pressed, after the delay above",
                  foreground="gray").pack(side="left", padx=(8, 0))

        pause_row = ttk.Frame(right)
        pause_row.pack(fill="x", pady=(0, 4))
        ttk.Label(pause_row, text="Pause Key:").pack(side="left")
        self.pause_key_label_var = tk.StringVar(value="(unbound)")
        ttk.Label(pause_row, textvariable=self.pause_key_label_var, width=12).pack(side="left", padx=4)
        self.bind_pause_btn = ttk.Button(
            pause_row, text="Bind Pause Key...", command=self._on_bind_pause_clicked)
        self.bind_pause_btn.pack(side="left")
        ttk.Button(pause_row, text="Clear", command=self._on_clear_pause_key).pack(side="left", padx=(4, 0))

        pause_mode_row = ttk.Frame(right)
        pause_mode_row.pack(fill="x", pady=(0, 4))
        ttk.Label(pause_mode_row, text="Pause behavior:").pack(side="left")
        self.pause_mode_var = tk.StringVar(value="duration")
        ttk.Radiobutton(pause_mode_row, text="For", variable=self.pause_mode_var, value="duration").pack(side="left")
        self.pause_duration_var = tk.StringVar(value="1000")
        ttk.Entry(pause_mode_row, textvariable=self.pause_duration_var, width=6).pack(side="left")
        ttk.Label(pause_mode_row, text="ms").pack(side="left", padx=(2, 8))
        ttk.Radiobutton(pause_mode_row, text="Until pressed again",
                         variable=self.pause_mode_var, value="toggle").pack(side="left")
        ttk.Label(pause_mode_row, text="freezes this rotation in place, resuming the same step",
                  foreground="gray").pack(side="left", padx=(8, 0))

        self.tree = ttk.Treeview(
            right, columns=("key", "delay", "jitter", "hold", "hold_jitter", "repeat"),
            show="tree headings", height=10)
        self.tree.heading("#0", text="Name")
        self.tree.column("#0", width=140)
        for col, label, width in (
            ("key", "Key", 60), ("delay", "Delay (ms)", 90),
            ("jitter", "Jitter (ms)", 90), ("hold", "Hold (ms)", 90),
            ("hold_jitter", "Hold Jitter (ms)", 100), ("repeat", "Repeat", 60),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(8, 4))
        self.tree.bind("<<TreeviewSelect>>", self._on_select_step)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        # Bound on the tree itself (not the window) so Ctrl+C/Ctrl+V/Delete still behave
        # as normal text editing inside the Name/Key/etc. entries, which is where keyboard
        # focus usually is instead -- these only fire while the tree itself has focus.
        self.tree.bind("<Control-c>", lambda _e: self._on_copy_clicked())
        self.tree.bind("<Control-v>", lambda _e: self._on_paste_clicked())
        self.tree.bind("<Delete>", lambda _e: self._remove_selected_step())
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<B1-Motion>", self._on_tree_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.tag_configure("drop_target", background=self._drop_target_color())

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(0, 8))

        step_actions_group = ttk.LabelFrame(right, text="Step Actions", padding=6)
        step_actions_group.pack(fill="x", pady=(0, 6))
        step_btns = ttk.Frame(step_actions_group)
        step_btns.pack(fill="x")
        for text, cmd in (
            ("Add Step", self._add_step), ("Add Sleep", self._add_sleep_step),
            ("Copy", self._on_copy_clicked), ("Paste", self._on_paste_clicked),
            ("Update Selected", self._update_selected_step),
            ("Remove Selected", self._remove_selected_step),
            ("Move Up", self._move_step_up), ("Move Down", self._move_step_down),
        ):
            ttk.Button(step_btns, text=text, command=cmd).pack(side="left", padx=(0, 4))

        step_fields_group = ttk.LabelFrame(right, text="Selected Step", padding=6)
        step_fields_group.pack(fill="x", pady=(0, 6))
        edit_row = ttk.Frame(step_fields_group)
        edit_row.pack(fill="x")
        self.step_name_var = tk.StringVar()
        self.step_key_var = tk.StringVar()
        self.step_delay_var = tk.StringVar(value="10")
        self.step_jitter_var = tk.StringVar(value="5")
        self.step_hold_var = tk.StringVar(value="30")
        self.step_hold_jitter_var = tk.StringVar(value="10")
        self.step_repeat_var = tk.StringVar(value="1")
        for label, var, width in (
            ("Name", self.step_name_var, 12), ("Key", self.step_key_var, 8),
            ("Delay", self.step_delay_var, 6),
            ("Jitter", self.step_jitter_var, 6), ("Hold", self.step_hold_var, 6),
            ("Hold Jitter", self.step_hold_jitter_var, 6),
            ("Repeat", self.step_repeat_var, 4),
        ):
            ttk.Label(edit_row, text=label).pack(side="left")
            ttk.Entry(edit_row, textvariable=var, width=width).pack(side="left", padx=(2, 8))

        repeat_row = ttk.Frame(step_fields_group)
        repeat_row.pack(fill="x", pady=(4, 0))
        self.step_repeat_combine_hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(repeat_row, text="Combine Hold",
                         variable=self.step_repeat_combine_hold_var).pack(side="left")
        ttk.Label(repeat_row,
                  text="(hold the key once for Hold × Repeat ms, then a single Delay, instead of "
                       "repeating press/release + Delay Repeat times)",
                  foreground="gray").pack(side="left", padx=(8, 0))

        self.step_ready_timeout_var = tk.StringVar(value="0")
        self.step_ready_confidence_var = tk.StringVar(value="0.90")
        self.step_ready_status_var = tk.StringVar(value="No cooldown check")

        cooldown_group = ttk.LabelFrame(right, text="Cooldown Check", padding=6)
        cooldown_group.pack(fill="x", pady=(0, 6))
        ready_row = ttk.Frame(cooldown_group)
        ready_row.pack(fill="x")
        ttk.Label(ready_row, textvariable=self.step_ready_status_var, width=28).pack(side="left")
        ttk.Label(ready_row, text="Timeout (ms)").pack(side="left", padx=(8, 2))
        ttk.Entry(ready_row, textvariable=self.step_ready_timeout_var, width=6).pack(side="left")
        ttk.Label(ready_row, text="Confidence").pack(side="left", padx=(8, 2))
        ttk.Entry(ready_row, textvariable=self.step_ready_confidence_var, width=5).pack(side="left")
        ttk.Button(ready_row, text="Image Match...", command=self._on_image_match_clicked).pack(
            side="left", padx=(8, 4))
        ttk.Button(ready_row, text="Pixel Match...", command=self._on_pixel_match_clicked).pack(
            side="left", padx=(0, 4))
        ttk.Button(ready_row, text="Clear", command=self._clear_ready_check).pack(side="left")

        self.step_buff_status_var = tk.StringVar(value="No buff check")
        buff_group = ttk.LabelFrame(right, text="Buff Check (alternate hold/delay while active)", padding=6)
        buff_group.pack(fill="x", pady=(0, 6))
        buff_row = ttk.Frame(buff_group)
        buff_row.pack(fill="x")
        ttk.Label(buff_row, textvariable=self.step_buff_status_var, width=20).pack(side="left")
        ttk.Button(buff_row, text="Image Match...", command=self._on_buff_image_match_clicked).pack(
            side="left", padx=(8, 4))
        ttk.Button(buff_row, text="Pixel Match...", command=self._on_buff_pixel_match_clicked).pack(
            side="left", padx=(0, 4))
        ttk.Button(buff_row, text="Clear", command=self._clear_buff_check).pack(side="left", padx=(0, 8))
        ttk.Label(buff_row, text="Hold (ms)").pack(side="left", padx=(0, 2))
        self.step_buff_hold_var = tk.StringVar(value="")
        ttk.Entry(buff_row, textvariable=self.step_buff_hold_var, width=6).pack(side="left", padx=(0, 8))
        ttk.Label(buff_row, text="Delay (ms)").pack(side="left", padx=(0, 2))
        self.step_buff_delay_var = tk.StringVar(value="")
        ttk.Entry(buff_row, textvariable=self.step_buff_delay_var, width=6).pack(side="left")
        ttk.Label(buff_group,
                  text="Blank Hold/Delay = keep the normal value even while the buff is active.",
                  foreground="gray").pack(anchor="w", pady=(4, 0))

        conditions_group = ttk.LabelFrame(right, text="Conditions", padding=6)
        conditions_group.pack(fill="x", pady=(0, 6))
        condition_btns = ttk.Frame(conditions_group)
        condition_btns.pack(fill="x")
        ttk.Button(condition_btns, text="Add Image Condition...",
                   command=self._on_add_image_condition_clicked).pack(side="left", padx=(0, 4))
        ttk.Button(condition_btns, text="Add Pixel Condition...",
                   command=self._on_add_pixel_condition_clicked).pack(side="left", padx=(0, 4))
        ttk.Label(condition_btns,
                  text="(double-click a condition in the list to recalibrate it;"
                       " use Move Up/Move Down in Step Actions to reorder it)",
                  foreground="gray").pack(side="left", padx=(8, 0))

        condition_name_row = ttk.Frame(conditions_group)
        condition_name_row.pack(fill="x", pady=(4, 0))
        ttk.Label(condition_name_row, text="Name:").pack(side="left")
        self.condition_name_var = tk.StringVar()
        ttk.Entry(condition_name_row, textvariable=self.condition_name_var, width=20).pack(
            side="left", padx=(2, 8))
        ttk.Button(condition_name_row, text="Rename Selected Condition",
                   command=self._on_rename_condition_clicked).pack(side="left")

        save_row = ttk.Frame(right)
        save_row.pack(fill="x", pady=(4, 0))
        ttk.Button(save_row, text="Revert to Saved", command=self._on_revert_clicked).pack(side="right", padx=(4, 0))
        ttk.Button(save_row, text="Save Rotation", command=self._save_rotation).pack(side="right")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(side="bottom", fill="x")
        self.toggle_btn = ttk.Button(bottom, text="Stop Bot", command=self._toggle_bot)
        self.toggle_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Bot running. Hotkeys are live.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", padx=8)
        ttk.Button(bottom, text="Toggle Light/Dark", command=self._toggle_theme).pack(side="right")

    # ---- appearance -----------------------------------------------------------

    def _sync_root_background(self):
        """Plain tk widgets (the root window itself, and any raw tk.Toplevel
        dialogs) aren't restyled by sv_ttk -- it only touches ttk widgets -- so
        their background is kept in sync with the current theme's own frame
        color here, queried live rather than hardcoded so it can't drift out of
        sync if the theme's palette ever changes."""
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)

    def _toggle_theme(self):
        sv_ttk.set_theme("light" if sv_ttk.get_theme() == "dark" else "dark")
        self._sync_root_background()
        self.tree.tag_configure("drop_target", background=self._drop_target_color())

    # ---- rotation list / selection --------------------------------------

    def _refresh_rotation_tree(self):
        selected = self.editing_original_name
        previously_open = {
            path for path, item_id in self._folder_nodes.items()
            if self.rotation_tree.exists(item_id) and self.rotation_tree.item(item_id, "open")
        }
        self.rotation_tree.delete(*self.rotation_tree.get_children())
        self._folder_nodes = {}

        def ensure_folder_node(folder_path: str) -> str:
            """Return the tree item id for folder_path (the root tree item id,
            "", for an ungrouped rotation), creating it -- and any missing
            parent folders -- on first use."""
            if not folder_path:
                return ""
            if folder_path in self._folder_nodes:
                return self._folder_nodes[folder_path]
            parent_path, _, label = folder_path.rpartition("/")
            parent_id = ensure_folder_node(parent_path)
            item_id = self.rotation_tree.insert(
                parent_id, tk.END, iid=f"folder:{folder_path}",
                text=f"\U0001F4C1 {label}", open=folder_path in previously_open)
            self._folder_nodes[folder_path] = item_id
            return item_id

        # Folders first, then ungrouped rotations, each group alphabetical -- avoids
        # ungrouped rotations (folder == "") sorting before every folder name.
        for name in sorted(self.rotations, key=lambda n: (
                self.rotations[n].folder == "", self.rotations[n].folder.lower(), n.lower())):
            rotation = self.rotations[name]
            parent_id = ensure_folder_node(rotation.folder)
            suffix = STATUS_LABELS.get(self.rotation_manager.status(name), "")
            self.rotation_tree.insert(parent_id, tk.END, iid=f"rotation:{name}", text=f"{name}{suffix}")

        if selected:
            item_id = f"rotation:{selected}"
            if self.rotation_tree.exists(item_id):
                self.rotation_tree.see(item_id)
                self.rotation_tree.selection_set(item_id)

    def _selected_rotation_name(self):
        """Name of the single currently-selected rotation, or None if nothing,
        a folder, or more than one item is selected."""
        selection = self.rotation_tree.selection()
        if len(selection) != 1 or not selection[0].startswith("rotation:"):
            return None
        return selection[0][len("rotation:"):]

    def _selected_rotation_names(self) -> list:
        """Names of every currently-selected rotation (folders in the
        selection are ignored), for actions that support multi-select."""
        return [item_id[len("rotation:"):] for item_id in self.rotation_tree.selection()
                if item_id.startswith("rotation:")]

    def _on_select_rotation(self, _event):
        name = self._selected_rotation_name()
        if name is not None and name in self.rotations:
            self._load_rotation_into_form(self.rotations[name])

    def _on_rotation_tree_right_click(self, event):
        item_id = self.rotation_tree.identify_row(event.y)
        if not item_id:
            return
        if item_id.startswith("folder:"):
            self.rotation_tree.selection_set(item_id)
            folder_path = item_id[len("folder:"):]
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Rename Folder...", command=lambda: self._rename_folder(folder_path))
            menu.tk_popup(event.x_root, event.y_root)
        elif item_id.startswith("rotation:"):
            if item_id not in self.rotation_tree.selection():
                self.rotation_tree.selection_set(item_id)
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Move to Folder...", command=self._move_selected_to_folder)
            menu.tk_popup(event.x_root, event.y_root)

    def _rename_folder(self, folder_path: str):
        """Renames/moves folder_path to a new path, taking every rotation in it
        (and any nested subfolders) along -- a bulk operation, unlike editing a
        single rotation's Folder field one at a time."""
        new_path = simpledialog.askstring(
            "Rename Folder", "New folder path:", initialvalue=folder_path, parent=self)
        if new_path is None:
            return
        new_path = new_path.strip().strip("/")
        problem = folder_path_problem(new_path)
        if problem:
            messagebox.showerror("Invalid folder", problem)
            return
        if not new_path or new_path == folder_path:
            return
        affected = [r for r in self.rotations.values()
                    if r.folder == folder_path or r.folder.startswith(folder_path + "/")]
        for rotation in affected:
            old_folder = rotation.folder
            new_folder = new_path + old_folder[len(folder_path):]
            storage.delete_rotation(rotation.name, old_folder)
            rotation.folder = new_folder
            storage.save_rotation(rotation)
            if rotation.name == self.editing_original_name:
                self.folder_var.set(new_folder)
        self._refresh_rotation_tree()

    def _move_selected_to_folder(self):
        """Moves every currently-selected rotation to one destination folder in
        a single action, instead of opening each one to edit its Folder field."""
        names = self._selected_rotation_names()
        if not names:
            messagebox.showinfo("No rotations selected", "Select one or more rotations in the list first.")
            return
        current_folder = self.rotations[names[0]].folder
        new_path = simpledialog.askstring(
            "Move to Folder", "Destination folder (blank = ungrouped):",
            initialvalue=current_folder, parent=self)
        if new_path is None:
            return
        new_path = new_path.strip().strip("/")
        problem = folder_path_problem(new_path)
        if problem:
            messagebox.showerror("Invalid folder", problem)
            return
        for name in names:
            rotation = self.rotations[name]
            if rotation.folder == new_path:
                continue
            storage.delete_rotation(rotation.name, rotation.folder)
            rotation.folder = new_path
            storage.save_rotation(rotation)
            if name == self.editing_original_name:
                self.folder_var.set(new_path)
        self._refresh_rotation_tree()

    def _load_rotation_into_form(self, rotation: Rotation):
        self.editing_original_name = rotation.name
        self.editing_original_hotkey = rotation.hotkey
        self.pending_hotkey = rotation.hotkey
        self.pending_cancel_key = rotation.cancel_key
        self.pending_reset_key = rotation.reset_key
        self.pending_pause_key = rotation.pause_key
        self.editing_steps = copy.deepcopy(rotation.steps)
        self.name_var.set(rotation.name)
        self.folder_var.set(rotation.folder)
        self.mode_var.set(rotation.mode)
        self.hotkey_label_var.set(display_name(rotation.hotkey))
        self.cancel_key_label_var.set(display_name(rotation.cancel_key))
        self.reset_key_label_var.set(display_name(rotation.reset_key))
        self.reset_delay_var.set(str(rotation.reset_delay_ms))
        self.pause_key_label_var.set(display_name(rotation.pause_key))
        self.pause_mode_var.set(rotation.pause_mode)
        self.pause_duration_var.set(str(rotation.pause_duration_ms))
        self._reset_ready_form()
        self._reset_buff_form()
        self._refresh_steps_tree()

    def _new_rotation(self):
        self.editing_original_name = None
        self.editing_original_hotkey = None
        self.pending_hotkey = None
        self.pending_cancel_key = None
        self.pending_reset_key = None
        self.pending_pause_key = None
        self.editing_steps = []
        self.name_var.set("New Rotation")
        self.folder_var.set("")
        self.mode_var.set("once")
        self.hotkey_label_var.set("(unbound)")
        self.cancel_key_label_var.set("(unbound)")
        self.reset_key_label_var.set("(unbound)")
        self.reset_delay_var.set("0")
        self.pause_key_label_var.set("(unbound)")
        self.pause_mode_var.set("duration")
        self.pause_duration_var.set("1000")
        self._reset_ready_form()
        self._reset_buff_form()
        self._refresh_steps_tree()
        self.rotation_tree.selection_remove(*self.rotation_tree.selection())

    def _on_revert_clicked(self):
        """Discards unsaved edits to whichever rotation is currently open --
        a safety net now that drag-and-drop/multi-select make it easier to
        mess one up by accident."""
        if self.editing_original_name and self.editing_original_name in self.rotations:
            if not messagebox.askyesno(
                    "Discard unsaved changes",
                    f"Discard unsaved changes to '{self.editing_original_name}'?"):
                return
            self._load_rotation_into_form(self.rotations[self.editing_original_name])
        else:
            if not messagebox.askyesno(
                    "Discard unsaved changes", "Discard this new, unsaved rotation?"):
                return
            self._new_rotation()

    def _copy_rotation(self):
        name = self._selected_rotation_name()
        if name is None:
            messagebox.showinfo("No rotation selected", "Select a rotation in the list first.")
            return
        original = self.rotations[name]
        duplicate = Rotation(
            name=self._unique_rotation_name(f"{original.name} (copy)"),
            mode=original.mode,
            hotkey=None,  # can't share the original's hotkey -- bind a new one before saving
            cancel_key=original.cancel_key,  # cancel/reset/pause keys CAN be shared, so these carry over as-is
            reset_key=original.reset_key,
            reset_delay_ms=original.reset_delay_ms,
            pause_key=original.pause_key,
            pause_mode=original.pause_mode,
            pause_duration_ms=original.pause_duration_ms,
            folder=original.folder,
            steps=copy.deepcopy(original.steps),
        )
        self._load_rotation_into_form(duplicate)
        self.rotation_tree.selection_remove(*self.rotation_tree.selection())

    def _unique_rotation_name(self, base_name: str) -> str:
        if base_name not in self.rotations:
            return base_name
        n = 2
        while f"{base_name} {n}" in self.rotations:
            n += 1
        return f"{base_name} {n}"

    def _reset_ready_form(self):
        self.step_ready_match_type = "image"
        self.step_ready_template = None
        self.step_ready_region = None
        self.step_ready_pixel_pos = None
        self.step_ready_pixel_color = None
        self.step_ready_timeout_var.set("0")
        self.step_ready_confidence_var.set("0.90")
        self._refresh_ready_status()

    def _refresh_ready_status(self):
        if self.step_ready_match_type == "pixel" and self.step_ready_pixel_color:
            r, g, b = self.step_ready_pixel_color
            self.step_ready_status_var.set(f"Pixel: RGB({r},{g},{b})")
        elif self.step_ready_match_type == "image" and self.step_ready_template and self.step_ready_region:
            w, h = self.step_ready_region[2], self.step_ready_region[3]
            self.step_ready_status_var.set(f"Image: {w}x{h}")
        else:
            self.step_ready_status_var.set("No cooldown check")

    def _reset_buff_form(self):
        self.step_buff_check = None
        self.step_buff_hold_var.set("")
        self.step_buff_delay_var.set("")
        self._refresh_buff_status()

    def _refresh_buff_status(self):
        self.step_buff_status_var.set(
            self._condition_summary(self.step_buff_check) if self.step_buff_check else "No buff check")

    def _delete_rotation(self):
        name = self._selected_rotation_name()
        if name is None:
            return
        if not messagebox.askyesno("Delete rotation", f"Delete '{name}'?"):
            return
        rotation = self.rotations.pop(name)
        if rotation.hotkey:
            self.hotkey_manager.unbind(rotation.hotkey)
        self.hotkey_manager.set_cancel_key(name, None)
        self.hotkey_manager.set_reset_key(name, None)
        self.hotkey_manager.set_pause_key(name, None)
        self.rotation_manager.unload(name)
        storage.delete_rotation(name, rotation.folder)
        self._refresh_rotation_tree()
        self._new_rotation()
        self._sweep_templates()

    # ---- step editing ----------------------------------------------------

    def _refresh_steps_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.editing_steps):
            is_sleep = not step.key
            label = step.name or ("Sleep" if is_sleep else step.key)
            key_col = "(sleep)" if is_sleep else step.key
            step_iid = f"step-{i}"
            self.tree.insert("", tk.END, iid=step_iid, text=label,
                              values=(key_col, step.delay_ms,
                                      step.jitter_ms, step.hold_ms, step.hold_jitter_ms,
                                      step.repeat_count),
                              open=bool(step.conditions))
            for j, condition in enumerate(step.conditions):
                self.tree.insert(step_iid, tk.END, iid=f"{step_iid}-cond-{j}",
                                  text=self._condition_summary(condition),
                                  values=("", "", "", "", "", ""))

    @staticmethod
    def _condition_summary(condition) -> str:
        if condition.name:
            return f"Condition: {condition.name}"
        if condition.match_type == "pixel" and condition.pixel_color:
            r, g, b = condition.pixel_color
            return f"Condition: Pixel RGB({r},{g},{b})"
        if condition.match_type == "image" and condition.region:
            w, h = condition.region[2], condition.region[3]
            return f"Condition: Image {w}x{h}"
        return "Condition: (not calibrated)"

    @staticmethod
    def _parse_tree_iid(iid: str):
        """("step-N", None) for a step row's index, (N, M) for the M'th
        condition of step N, or None if `iid` doesn't match either scheme."""
        parts = iid.split("-")
        if len(parts) == 2 and parts[0] == "step":
            return int(parts[1]), None
        if len(parts) == 4 and parts[0] == "step" and parts[2] == "cond":
            return int(parts[1]), int(parts[3])
        return None

    def _selected_step_index(self):
        """For actions that only make sense on exactly one whole step
        (Update Selected). Returns None (with an info box explaining why) if
        nothing, a condition, or more than one row is selected -- there's no
        bulk-field-edit feature, so silently picking one of several selected
        steps would be more confusing than asking the user to select just one."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step in the list first.")
            return None
        if len(selection) > 1:
            messagebox.showinfo("Select one step", "Select exactly one step for this action.")
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is not None:
            messagebox.showinfo("Select a step", "Select a step (not a condition) for this action.")
            return None
        return parsed[0]

    def _selected_owning_step_index(self):
        """For actions that attach to a step regardless of whether the step
        itself or one of its conditions is selected (Add Condition)."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step (or one of its conditions) first.")
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return None
        return parsed[0]

    def _on_select_step(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        step = self.editing_steps[parsed[0]]
        self.condition_name_var.set(step.conditions[parsed[1]].name if parsed[1] is not None else "")
        self.step_name_var.set(step.name)
        self.step_key_var.set(step.key)
        self.step_delay_var.set(str(step.delay_ms))
        self.step_jitter_var.set(str(step.jitter_ms))
        self.step_hold_var.set(str(step.hold_ms))
        self.step_hold_jitter_var.set(str(step.hold_jitter_ms))
        self.step_repeat_var.set(str(step.repeat_count))
        self.step_repeat_combine_hold_var.set(step.repeat_combine_hold)
        self.step_ready_match_type = step.ready_match_type
        self.step_ready_template = step.ready_template
        self.step_ready_region = step.ready_region
        self.step_ready_pixel_pos = step.ready_pixel_pos
        self.step_ready_pixel_color = step.ready_pixel_color
        self.step_ready_timeout_var.set(str(step.ready_timeout_ms))
        self.step_ready_confidence_var.set(f"{step.ready_confidence:.2f}")
        self._refresh_ready_status()
        self.step_buff_check = step.buff_check
        self.step_buff_hold_var.set("" if step.buff_hold_ms is None else str(step.buff_hold_ms))
        self.step_buff_delay_var.set("" if step.buff_delay_ms is None else str(step.buff_delay_ms))
        self._refresh_buff_status()

    def _read_step_form(self, conditions=None):
        try:
            delay = int(self.step_delay_var.get())
            jitter = int(self.step_jitter_var.get())
            hold = int(self.step_hold_var.get())
            hold_jitter = int(self.step_hold_jitter_var.get())
            timeout = int(self.step_ready_timeout_var.get())
            confidence = float(self.step_ready_confidence_var.get())
            buff_hold_text = self.step_buff_hold_var.get().strip()
            buff_delay_text = self.step_buff_delay_var.get().strip()
            buff_hold = int(buff_hold_text) if buff_hold_text else None
            buff_delay = int(buff_delay_text) if buff_delay_text else None
            repeat = int(self.step_repeat_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid step",
                "Delay/jitter/hold/hold jitter/timeout/buff hold/buff delay/repeat must be whole "
                "numbers (buff hold/delay may be left blank), and confidence must be a decimal (e.g. 0.9).")
            return None
        step = Step(
            key=self.step_key_var.get().strip(),
            name=self.step_name_var.get().strip(),
            delay_ms=delay,
            jitter_ms=jitter,
            hold_ms=hold,
            hold_jitter_ms=hold_jitter,
            ready_match_type=self.step_ready_match_type,
            ready_template=self.step_ready_template,
            ready_region=self.step_ready_region,
            ready_pixel_pos=self.step_ready_pixel_pos,
            ready_pixel_color=self.step_ready_pixel_color,
            ready_confidence=confidence,
            ready_timeout_ms=timeout,
            buff_check=self.step_buff_check,
            buff_hold_ms=buff_hold,
            buff_delay_ms=buff_delay,
            repeat_count=repeat,
            repeat_combine_hold=self.step_repeat_combine_hold_var.get(),
        )
        if conditions is not None:
            # The form has no fields of its own for conditions -- Update Selected
            # must carry over the step's existing conditions explicitly, or they'd
            # silently be wiped out by this fresh Step (whose conditions default
            # to an empty list).
            step.conditions = conditions
        return step

    def _discard_selected_step_edits(self):
        """If a tree row is currently selected, its data was loaded into the
        step form by _on_select_step for potential Update Selected -- Add
        Step/Add Sleep must not silently read that as if it were a fresh
        step (that's how "Add Step" ends up cloning whatever's highlighted),
        so clear the selection and reset every step field to blank defaults
        first. No-op if nothing is selected, so typing values and clicking
        Add Step repeatedly to add several similarly-timed steps still works."""
        if not self.tree.selection():
            return
        self.tree.selection_remove(*self.tree.selection())
        self.step_name_var.set("")
        self.step_key_var.set("")
        self.step_delay_var.set("10")
        self.step_jitter_var.set("5")
        self.step_hold_var.set("30")
        self.step_hold_jitter_var.set("10")
        self.step_repeat_var.set("1")
        self.step_repeat_combine_hold_var.set(False)
        self._reset_ready_form()
        self._reset_buff_form()

    def _add_step(self):
        self._discard_selected_step_edits()
        step = self._read_step_form()
        if step is None:
            return
        self.editing_steps.append(step)
        self._refresh_steps_tree()
        self._reset_ready_form()
        self._reset_buff_form()

    def _add_sleep_step(self):
        """A sleep step has no key -- it's just a pause of delay_ms (+/- jitter_ms)
        with nothing pressed, for a deliberate wait that isn't tied to any skill.
        Reuses the same form fields as Add Step; whatever's in Key is ignored."""
        self._discard_selected_step_edits()
        step = self._read_step_form()
        if step is None:
            return
        step.key = ""
        self.editing_steps.append(step)
        self._refresh_steps_tree()
        self._reset_ready_form()
        self._reset_buff_form()

    def _on_copy_clicked(self):
        """Copies every currently-selected step (condition-only selections are
        ignored -- conditions aren't independently copy/pasteable) to an
        in-memory clipboard that lives on the App itself, so it survives
        switching to a different rotation -- that's what makes pasting into a
        different rotation than the one you copied from work."""
        selection = self.tree.selection()
        step_indices = sorted({p[0] for p in (self._parse_tree_iid(iid) for iid in selection)
                                if p is not None and p[1] is None})
        if not step_indices:
            messagebox.showinfo("No step selected", "Select at least one step to copy.")
            return
        self._step_clipboard = copy.deepcopy([self.editing_steps[i] for i in step_indices])

    def _on_paste_clicked(self):
        if not self._step_clipboard:
            messagebox.showinfo("Clipboard is empty", "Copy a step first.")
            return
        selection = self.tree.selection()
        if selection:
            parsed = self._parse_tree_iid(selection[0])
            insert_at = parsed[0] + 1 if parsed is not None else len(self.editing_steps)
        else:
            insert_at = len(self.editing_steps)
        pasted = copy.deepcopy(self._step_clipboard)  # independent objects each time, so repeated pastes don't share state
        self.editing_steps[insert_at:insert_at] = pasted
        self._refresh_steps_tree()
        self.tree.selection_set(*(f"step-{insert_at + offset}" for offset in range(len(pasted))))

    def _update_selected_step(self):
        i = self._selected_step_index()
        if i is None:
            return
        step = self._read_step_form(conditions=self.editing_steps[i].conditions)
        if step is None:
            return
        self.editing_steps[i] = step
        self._refresh_steps_tree()

    def _remove_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            return
        parsed_list = [p for p in (self._parse_tree_iid(iid) for iid in selection) if p is not None]
        step_indices = {s for s, c in parsed_list if c is None}
        # Conditions belonging to a step that's also being fully removed are
        # already handled by removing that step -- skip them to avoid deleting
        # from a conditions list that's about to disappear anyway.
        condition_deletions = [(s, c) for s, c in parsed_list if c is not None and s not in step_indices]
        conditions_by_step = {}
        for s, c in condition_deletions:
            conditions_by_step.setdefault(s, []).append(c)
        for s, cond_indices in conditions_by_step.items():
            for c in sorted(cond_indices, reverse=True):
                del self.editing_steps[s].conditions[c]
        for s in sorted(step_indices, reverse=True):
            del self.editing_steps[s]
        self._refresh_steps_tree()

    def _move_step_up(self):
        """Moves the selected step, or -- if a condition is selected instead --
        that condition up within its own step's conditions list."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        step_idx, cond_idx = parsed
        if cond_idx is None:
            if step_idx == 0:
                return
            self.editing_steps[step_idx - 1], self.editing_steps[step_idx] = \
                self.editing_steps[step_idx], self.editing_steps[step_idx - 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx - 1}")
        else:
            if cond_idx == 0:
                return
            conditions = self.editing_steps[step_idx].conditions
            conditions[cond_idx - 1], conditions[cond_idx] = conditions[cond_idx], conditions[cond_idx - 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx - 1}")

    def _move_step_down(self):
        """Moves the selected step, or -- if a condition is selected instead --
        that condition down within its own step's conditions list."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        step_idx, cond_idx = parsed
        if cond_idx is None:
            if step_idx >= len(self.editing_steps) - 1:
                return
            self.editing_steps[step_idx + 1], self.editing_steps[step_idx] = \
                self.editing_steps[step_idx], self.editing_steps[step_idx + 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx + 1}")
        else:
            conditions = self.editing_steps[step_idx].conditions
            if cond_idx >= len(conditions) - 1:
                return
            conditions[cond_idx + 1], conditions[cond_idx] = conditions[cond_idx], conditions[cond_idx + 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx + 1}")

    # ---- drag-and-drop reordering -----------------------------------------
    #
    # A plain click (Button-1) on ttk.Treeview unconditionally collapses multi-
    # selection to the single clicked row, synchronously, before any B1-Motion
    # can fire -- so "what to drag" must be captured in the press handler
    # itself (which runs before that collapse, since instance bindings fire
    # before class bindings), not lazily on first motion. If the pressed row
    # is already part of the current selection, the press handler suppresses
    # the collapse (returns "break") so the whole multi-selection can be
    # dragged as a group; the release handler replicates the collapse itself
    # if it turns out no drag actually happened (a plain click, not a drag).
    #
    # The tree is never rebuilt (_refresh_steps_tree) while a drag is in
    # progress -- only at release, once the reorder is fully resolved -- since
    # rebuilding would invalidate iids captured earlier in the drag.

    def _drop_target_color(self) -> str:
        color = ttk.Style().lookup("Treeview", "background", ("selected",))
        return color or "#4a6984"

    @staticmethod
    def _is_valid_drag_set(candidate) -> bool:
        if not candidate:
            return False
        cond_entries = [c for s, c in candidate if c is not None]
        step_entries = [s for s, c in candidate if c is None]
        if cond_entries and step_entries:
            return False  # mixed steps + conditions
        if cond_entries:
            return len({s for s, c in candidate}) == 1  # all conditions of the same step
        return True  # all steps

    def _on_tree_press(self, event):
        if event.state & 0x0005:  # Shift (0x1) or Control (0x4) held -- leave extend/toggle select alone
            self._drag_candidate = None
            return
        self._drag_start_xy = (event.x, event.y)
        self._drag_active = False
        self._drop_target_iid = None
        row = self.tree.identify_row(event.y)
        if not row:
            self._drag_candidate = None
            return
        current_selection = self.tree.selection()
        if row in current_selection:
            candidate = sorted(p for p in (self._parse_tree_iid(iid) for iid in current_selection)
                                if p is not None)
            if self._is_valid_drag_set(candidate):
                self._drag_candidate = candidate
                self.tree.focus_set()
                self.tree.focus(row)
                return "break"
            self._drag_candidate = None
            return
        parsed = self._parse_tree_iid(row)
        self._drag_candidate = [parsed] if parsed is not None else None

    def _on_tree_motion(self, event):
        if not self._drag_candidate:
            return
        if not self._drag_active:
            if abs(event.x - self._drag_start_xy[0]) < 4 and abs(event.y - self._drag_start_xy[1]) < 4:
                return
            self._drag_active = True
        target = self._resolve_drop_target(event, self._drag_candidate)
        new_target_iid = target[0] if target is not None else None
        if new_target_iid != self._drop_target_iid:
            if self._drop_target_iid is not None:
                self.tree.item(self._drop_target_iid, tags=())
            if new_target_iid is not None:
                self.tree.item(new_target_iid, tags=("drop_target",))
            self._drop_target_iid = new_target_iid

    def _on_tree_release(self, event):
        if self._drop_target_iid is not None:
            self.tree.item(self._drop_target_iid, tags=())
            self._drop_target_iid = None
        candidate = self._drag_candidate
        self._drag_candidate = None
        if not candidate:
            return
        if not self._drag_active:
            # No real drag happened -- replicate Tk's own click-to-select (which
            # the press handler suppressed) so a plain click still behaves normally.
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
                self.tree.focus(row)
            return
        self._drag_active = False
        target = self._resolve_drop_target(event, candidate)
        if target is None:
            return
        _highlight_iid, target_index, after = target
        dragging_conditions = candidate[0][1] is not None
        if dragging_conditions:
            owning_step = candidate[0][0]
            dragged_indices = sorted(c for s, c in candidate)
            start = self._reorder(self.editing_steps[owning_step].conditions, dragged_indices, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(f"step-{owning_step}-cond-{start + k}" for k in range(len(dragged_indices))))
        else:
            dragged_indices = sorted(s for s, c in candidate)
            start = self._reorder(self.editing_steps, dragged_indices, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(f"step-{start + k}" for k in range(len(dragged_indices))))

    def _resolve_drop_target(self, event, candidate):
        """Returns (highlight_iid, target_index, after) for the given drag
        candidate (list[(step_idx, cond_idx_or_None)]), or None if there's no
        valid drop here. target_index is an index into whichever list is
        being dragged (self.editing_steps for a step drag, or the owning
        step's .conditions for a condition drag), referring to a position in
        that list as it stood *before* removing the dragged items."""
        dragging_conditions = candidate[0][1] is not None
        target_row = self.tree.identify_row(event.y)
        parsed = self._parse_tree_iid(target_row) if target_row else None

        if dragging_conditions:
            owning_step = candidate[0][0]
            conditions = self.editing_steps[owning_step].conditions
            if not conditions:
                return None
            if parsed is not None and parsed[0] == owning_step and parsed[1] is not None:
                if (owning_step, parsed[1]) in candidate:
                    return None  # dropped on one of the dragged rows itself
                bbox = self.tree.bbox(target_row)
                after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
                return target_row, parsed[1], after
            if parsed is not None and parsed[0] == owning_step and parsed[1] is None:
                # Hovering the step's own row -- it sits above its conditions.
                return f"step-{owning_step}-cond-0", 0, False
            # Anywhere else is only valid if it's clearly beyond this step's own
            # condition block (above the first / below the last of *that* step).
            first_iid, last_iid = f"step-{owning_step}-cond-0", f"step-{owning_step}-cond-{len(conditions) - 1}"
            first_bbox, last_bbox = self.tree.bbox(first_iid), self.tree.bbox(last_iid)
            if first_bbox and event.y < first_bbox[1]:
                return first_iid, 0, False
            if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
                return last_iid, len(conditions) - 1, True
            return None

        if not self.editing_steps:
            return None
        if parsed is not None and parsed[1] is not None:
            # Hovered a condition row -- treat a step and its conditions as one block.
            target_row, parsed = f"step-{parsed[0]}", (parsed[0], None)
        if parsed is not None:
            if parsed[0] in {s for s, c in candidate}:
                return None  # dropped on one of the dragged steps itself
            bbox = self.tree.bbox(target_row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return target_row, parsed[0], after
        last = len(self.editing_steps) - 1
        first_bbox, last_bbox = self.tree.bbox("step-0"), self.tree.bbox(f"step-{last}")
        if first_bbox and event.y < first_bbox[1]:
            return "step-0", 0, False
        if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
            return f"step-{last}", last, True
        return None

    @staticmethod
    def _reorder(items: list, dragged_indices, target_index: int, after: bool) -> int:
        """Moves items at dragged_indices (sorted ascending) to just before/
        after target_index (an index into items *before* removal), preserving
        their relative order. Mutates items in place; returns the index the
        first moved item ends up at, so callers can reselect the moved block."""
        moved = [items[i] for i in dragged_indices]
        drop_pos = target_index + (1 if after else 0)
        removed_before_drop = sum(1 for i in dragged_indices if i < drop_pos)
        adjusted_drop_pos = drop_pos - removed_before_drop
        for i in sorted(dragged_indices, reverse=True):
            del items[i]
        for offset, item in enumerate(moved):
            items.insert(adjusted_drop_pos + offset, item)
        return adjusted_drop_pos

    # ---- cooldown-check calibration -----------------------------------------

    def _clear_ready_check(self):
        self.step_ready_match_type = "image"
        self.step_ready_template = None
        self.step_ready_region = None
        self.step_ready_pixel_pos = None
        self.step_ready_pixel_color = None
        self._refresh_ready_status()

    def _on_image_match_clicked(self):
        self._start_image_capture()

    # ---- buff-check calibration ----------------------------------------------

    def _clear_buff_check(self):
        self.step_buff_check = None
        self._refresh_buff_status()

    def _on_buff_image_match_clicked(self):
        default_confidence = self.step_buff_check.confidence if self.step_buff_check else 0.90
        self._start_image_capture(on_use=self._set_buff_image_match, default_confidence=default_confidence)

    def _on_buff_pixel_match_clicked(self):
        default_confidence = self.step_buff_check.confidence if self.step_buff_check else 0.90
        self._start_pixel_capture(on_use=self._set_buff_pixel_match, default_confidence=default_confidence)

    def _set_buff_image_match(self, filename, region, confidence):
        self.step_buff_check = Condition(match_type="image", template=filename, region=region, confidence=confidence)
        self._refresh_buff_status()

    def _set_buff_pixel_match(self, point, color, confidence):
        self.step_buff_check = Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)
        self._refresh_buff_status()

    def _start_image_capture(self, on_use=None, default_confidence=0.90):
        """Runs the region-capture-overlay flow, ending in an image-match preview.
        With `on_use` set, "Use This" calls on_use(filename, region, confidence)
        and shows a Confidence field (pre-filled from `default_confidence`)
        instead of the default behavior of staging the step's own cooldown
        check into self.step_ready_* (used for Add/Recalibrate Condition)."""
        messagebox.showinfo(
            "Calibrate image match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click-drag a small rectangle tightly around just that icon and "
            "release the mouse button.\n\nPress Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_region_capture_overlay(on_use, default_confidence))

    def _open_region_capture_overlay(self, on_use=None, default_confidence=0.90):
        RegionCaptureOverlay(
            self, on_done=lambda region: self._on_image_region_captured(region, on_use, default_confidence))

    def _on_image_region_captured(self, region, on_use=None, default_confidence=0.90):
        if region is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # captured template isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._take_image_match_screenshot(region, on_use, default_confidence))

    def _take_image_match_screenshot(self, region, on_use=None, default_confidence=0.90):
        filename = templates.new_template_filename()
        path = templates.template_path(filename)
        try:
            templates.ensure_dir()
            pyautogui.screenshot(region=region).save(path)
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Calibration failed", f"Could not capture the region:\n{e}")
            return
        self.deiconify()
        self._show_image_match_preview(filename, region, on_use, default_confidence)

    def _show_image_match_preview(self, filename, region, on_use=None, default_confidence=0.90):
        preview = tk.Toplevel(self)
        preview.title("Confirm image match")
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            preview.configure(bg=bg)

        image = tk.PhotoImage(file=templates.template_path(filename))
        preview.image = image  # keep a reference -- Tk drops it otherwise
        ttk.Label(preview, image=image).pack(padx=8, pady=8)
        ttk.Label(preview, text=f"{region[2]} x {region[3]} px at ({region[0]}, {region[1]})").pack()

        confidence_var = None
        if on_use is not None:
            conf_row = ttk.Frame(preview)
            conf_row.pack(pady=(4, 0))
            ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
            confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
            ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        btns = ttk.Frame(preview, padding=8)
        btns.pack(pady=(4, 0))

        def use_this():
            if on_use is not None:
                try:
                    confidence = float(confidence_var.get())
                except ValueError:
                    messagebox.showerror("Invalid confidence", "Confidence must be a decimal (e.g. 0.9).")
                    return
                preview.destroy()
                on_use(filename, region, confidence)
                return
            # Deliberately does NOT delete any previously-calibrated file for this
            # step -- that file may still be referenced by a committed Step until
            # "Update Selected"/"Save Rotation" runs. Orphans are cleaned up by the
            # periodic sweep instead (see _sweep_templates).
            self.step_ready_match_type = "image"
            self.step_ready_template = filename
            self.step_ready_region = region
            self.step_ready_pixel_pos = None
            self.step_ready_pixel_color = None
            self._refresh_ready_status()
            preview.destroy()

        def retry():
            templates.delete_template(filename)  # safe: nothing has ever referenced it
            preview.destroy()
            self._start_image_capture(on_use, default_confidence)

        def cancel():
            templates.delete_template(filename)  # safe: same as above
            preview.destroy()

        ttk.Button(btns, text="Use This", command=use_this).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left", padx=4)

    def _on_pixel_match_clicked(self):
        self._start_pixel_capture()

    def _start_pixel_capture(self, on_use=None, default_confidence=0.90):
        """Same shape as _start_image_capture, for the pixel-match flow."""
        messagebox.showinfo(
            "Calibrate pixel match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click exactly on the pixel you want to check.\n\n"
            "Press Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_point_capture_overlay(on_use, default_confidence))

    def _open_point_capture_overlay(self, on_use=None, default_confidence=0.90):
        PointCaptureOverlay(
            self, on_done=lambda point: self._on_point_captured(point, on_use, default_confidence))

    def _on_point_captured(self, point, on_use=None, default_confidence=0.90):
        if point is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # sampled color isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._sample_pixel_color(point, on_use, default_confidence))

    def _sample_pixel_color(self, point, on_use=None, default_confidence=0.90):
        try:
            color = pyautogui.screenshot(region=(point[0], point[1], 1, 1)).getpixel((0, 0))
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Calibration failed", f"Could not sample the pixel:\n{e}")
            return
        self.deiconify()
        self._show_pixel_match_preview(point, color, on_use, default_confidence)

    def _show_pixel_match_preview(self, point, color, on_use=None, default_confidence=0.90):
        preview = tk.Toplevel(self)
        preview.title("Confirm pixel match")
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            preview.configure(bg=bg)

        swatch = tk.Canvas(preview, width=60, height=60, highlightthickness=1)
        swatch.pack(padx=8, pady=8)
        swatch.create_rectangle(1, 1, 59, 59, fill="#%02x%02x%02x" % color, outline="")
        ttk.Label(preview, text=f"RGB {color} at ({point[0]}, {point[1]})").pack(pady=(0, 8))

        confidence_var = None
        if on_use is not None:
            conf_row = ttk.Frame(preview)
            conf_row.pack(pady=(0, 4))
            ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
            confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
            ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        btns = ttk.Frame(preview, padding=8)
        btns.pack()

        def use_this():
            if on_use is not None:
                try:
                    confidence = float(confidence_var.get())
                except ValueError:
                    messagebox.showerror("Invalid confidence", "Confidence must be a decimal (e.g. 0.9).")
                    return
                preview.destroy()
                on_use(point, color, confidence)
                return
            self.step_ready_match_type = "pixel"
            self.step_ready_pixel_pos = point
            self.step_ready_pixel_color = color
            self.step_ready_template = None
            self.step_ready_region = None
            self._refresh_ready_status()
            preview.destroy()

        def retry():
            preview.destroy()
            self._start_pixel_capture(on_use, default_confidence)

        ttk.Button(btns, text="Use This", command=use_this).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=preview.destroy).pack(side="left", padx=4)

    # ---- conditions -----------------------------------------------------

    def _on_add_image_condition_clicked(self):
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        self._start_image_capture(on_use=lambda filename, region, confidence: self._add_condition(
            step_idx, Condition(match_type="image", template=filename, region=region, confidence=confidence)))

    def _on_add_pixel_condition_clicked(self):
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        self._start_pixel_capture(on_use=lambda point, color, confidence: self._add_condition(
            step_idx, Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)))

    def _add_condition(self, step_idx: int, condition: Condition):
        self.editing_steps[step_idx].conditions.append(condition)
        self._refresh_steps_tree()

    def _on_rename_condition_clicked(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No condition selected", "Select a condition in the list first.")
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            messagebox.showinfo("Select a condition", "Select a condition (not a step) to rename.")
            return
        step_idx, cond_idx = parsed
        self.editing_steps[step_idx].conditions[cond_idx].name = self.condition_name_var.get().strip()
        self._refresh_steps_tree()
        self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx}")

    def _on_tree_double_click(self, _event):
        """Double-clicking a condition row recalibrates it in place (same
        capture flow as adding one, but replacing rather than appending).
        Double-clicking a step row is a no-op -- steps are recalibrated via
        the Image Match/Pixel Match buttons instead."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            return
        step_idx, cond_idx = parsed
        condition = self.editing_steps[step_idx].conditions[cond_idx]

        def replace(new_condition: Condition):
            new_condition.name = condition.name  # recalibrating shouldn't clear an existing name
            self.editing_steps[step_idx].conditions[cond_idx] = new_condition
            self._refresh_steps_tree()

        if condition.match_type == "pixel":
            self._start_pixel_capture(
                on_use=lambda point, color, confidence: replace(
                    Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)),
                default_confidence=condition.confidence)
        else:
            self._start_image_capture(
                on_use=lambda filename, region, confidence: replace(
                    Condition(match_type="image", template=filename, region=region, confidence=confidence)),
                default_confidence=condition.confidence)

    def _referenced_templates(self) -> set:
        keep = set()
        for rotation in self.rotations.values():
            for step in rotation.steps:
                if step.ready_template:
                    keep.add(step.ready_template)
                for condition in step.conditions:
                    if condition.template:
                        keep.add(condition.template)
                if step.buff_check and step.buff_check.template:
                    keep.add(step.buff_check.template)
        for step in self.editing_steps:
            if step.ready_template:
                keep.add(step.ready_template)
            for condition in step.conditions:
                if condition.template:
                    keep.add(condition.template)
            if step.buff_check and step.buff_check.template:
                keep.add(step.buff_check.template)
        if self.step_ready_template:
            keep.add(self.step_ready_template)
        if self.step_buff_check and self.step_buff_check.template:
            keep.add(self.step_buff_check.template)
        return keep

    def _sweep_templates(self):
        templates.sweep_unreferenced(self._referenced_templates())

    # ---- hotkey binding ----------------------------------------------------

    def _on_bind_hotkey_clicked(self):
        self.bind_hotkey_btn.config(text="Press a key or click...", state="disabled")
        threading.Thread(target=self._capture_hotkey_worker, daemon=True).start()

    def _capture_hotkey_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__capture__", key))

    def _on_hotkey_captured(self, key: str):
        self.pending_hotkey = key
        self.hotkey_label_var.set(display_name(key))
        self.bind_hotkey_btn.config(text="Bind Hotkey...", state="normal")

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
                "Each will be saved immediately."):
            return
        for rotation in bound:
            self.hotkey_manager.unbind(rotation.hotkey)
            rotation.hotkey = None
            storage.save_rotation(rotation)
        if self.editing_original_name in self.rotations:
            self.pending_hotkey = None
            self.editing_original_hotkey = None
            self.hotkey_label_var.set(display_name(None))
        self._refresh_rotation_tree()

    # ---- cancel key -----------------------------------------------------------

    def _on_bind_cancel_clicked(self):
        self.bind_cancel_btn.config(text="Press a key or click...", state="disabled")
        threading.Thread(target=self._capture_cancel_key_worker, daemon=True).start()

    def _capture_cancel_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__cancel_capture__", key))

    def _on_cancel_key_captured(self, key: str):
        self.pending_cancel_key = key
        self.cancel_key_label_var.set(display_name(key))
        self.bind_cancel_btn.config(text="Bind Cancel Key...", state="normal")

    def _on_clear_cancel_key(self):
        self.pending_cancel_key = None
        self.cancel_key_label_var.set(display_name(None))

    # ---- reset key ------------------------------------------------------------

    def _on_bind_reset_clicked(self):
        self.bind_reset_btn.config(text="Press a key or click...", state="disabled")
        threading.Thread(target=self._capture_reset_key_worker, daemon=True).start()

    def _capture_reset_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__reset_capture__", key))

    def _on_reset_key_captured(self, key: str):
        self.pending_reset_key = key
        self.reset_key_label_var.set(display_name(key))
        self.bind_reset_btn.config(text="Bind Reset Key...", state="normal")

    def _on_clear_reset_key(self):
        self.pending_reset_key = None
        self.reset_key_label_var.set(display_name(None))

    # ---- pause key ------------------------------------------------------------

    def _on_bind_pause_clicked(self):
        self.bind_pause_btn.config(text="Press a key or click...", state="disabled")
        threading.Thread(target=self._capture_pause_key_worker, daemon=True).start()

    def _capture_pause_key_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__pause_capture__", key))

    def _on_pause_key_captured(self, key: str):
        self.pending_pause_key = key
        self.pause_key_label_var.set(display_name(key))
        self.bind_pause_btn.config(text="Bind Pause Key...", state="normal")

    def _on_clear_pause_key(self):
        self.pending_pause_key = None
        self.pause_key_label_var.set(display_name(None))

    # ---- save ---------------------------------------------------------------

    def _apply_pending_step_edits(self) -> bool:
        """Writes the step-editing form's current contents back into whichever
        step is selected (or owns the selected condition), as if Update
        Selected had just been clicked -- so Save Rotation always persists
        what's currently on screen instead of silently discarding it if the
        user forgot to click Update Selected first. Returns False (leaving an
        error box up from _read_step_form) if the form doesn't parse; True if
        there was nothing to apply (no step, or more than one row, selected --
        with several selected there's no single clear target to apply the
        form to) or the apply succeeded."""
        selection = self.tree.selection()
        if not selection or len(selection) > 1:
            return True
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return True
        step_idx = parsed[0]
        step = self._read_step_form(conditions=self.editing_steps[step_idx].conditions)
        if step is None:
            return False
        self.editing_steps[step_idx] = step
        self._refresh_steps_tree()
        return True

    def _save_rotation(self):
        if not self._apply_pending_step_edits():
            return
        name = self.name_var.get().strip()
        try:
            pause_duration_ms = int(self.pause_duration_var.get())
            reset_delay_ms = int(self.reset_delay_var.get())
        except ValueError:
            messagebox.showerror(
                "Cannot save rotation", "Pause duration and reset delay must be whole numbers.")
            return
        rotation = Rotation(
            name=name,
            mode=self.mode_var.get(),
            hotkey=self.pending_hotkey,
            cancel_key=self.pending_cancel_key,
            reset_key=self.pending_reset_key,
            reset_delay_ms=reset_delay_ms,
            pause_key=self.pending_pause_key,
            pause_mode=self.pause_mode_var.get(),
            pause_duration_ms=pause_duration_ms,
            folder=self.folder_var.get().strip(),
            steps=copy.deepcopy(self.editing_steps),
        )
        problems = validate_rotation(rotation)
        if name != self.editing_original_name and name in self.rotations:
            problems.append(f"A rotation named '{name}' already exists.")
        if rotation.hotkey:
            if rotation.hotkey == self.hotkey_manager.panic_key:
                problems.append(f"'{display_name(rotation.hotkey)}' is reserved as the panic/stop-all key.")
            else:
                owner = self.hotkey_manager.bound_to(rotation.hotkey)
                if owner is not None and owner != self.editing_original_name:
                    problems.append(f"'{display_name(rotation.hotkey)}' is already bound to '{owner}'.")
        if problems:
            messagebox.showerror("Cannot save rotation", "\n".join(problems))
            return

        # Rebind hotkey first (release whatever this rotation held before, bind the new
        # choice) -- before any destructive rename/move step, so a conflict here (shouldn't
        # happen given the pre-check above, but kept as a defensive guard) can't leave
        # the old file deleted with nothing saved in its place.
        try:
            self.hotkey_manager.rebind(self.editing_original_hotkey, rotation.hotkey, rotation.name)
        except ValueError as e:
            messagebox.showerror("Hotkey conflict", str(e))
            return

        # A rotation's file path depends on both its name and its folder, so either
        # changing means the old file needs to go, not just a plain rename.
        old_rotation = self.rotations.get(self.editing_original_name) if self.editing_original_name else None
        moved = old_rotation is not None and (
            old_rotation.name != rotation.name or old_rotation.folder != rotation.folder)
        if moved:
            storage.delete_rotation(old_rotation.name, old_rotation.folder)
            self.rotation_manager.unload(old_rotation.name)
            self.hotkey_manager.set_cancel_key(old_rotation.name, None)
            self.hotkey_manager.set_reset_key(old_rotation.name, None)
            self.hotkey_manager.set_pause_key(old_rotation.name, None)
            del self.rotations[old_rotation.name]

        storage.save_rotation(rotation)
        self.rotation_manager.load(rotation)
        self.rotations[rotation.name] = rotation
        self.hotkey_manager.set_cancel_key(rotation.name, rotation.cancel_key)
        self.hotkey_manager.set_reset_key(rotation.name, rotation.reset_key)
        self.hotkey_manager.set_pause_key(rotation.name, rotation.pause_key)

        self._load_rotation_into_form(rotation)
        self._refresh_rotation_tree()
        self._sweep_templates()

    # ---- global start/stop --------------------------------------------------

    def _toggle_bot(self):
        if self.bot_enabled:
            self.rotation_manager.stop_all()
            self.hotkey_manager.disable_all()
            self.bot_enabled = False
            self.toggle_btn.config(text="Start Bot")
            self.status_var.set("Bot stopped. Hotkeys are inactive.")
        else:
            self.hotkey_manager.enable_all()
            self.bot_enabled = True
            self.toggle_btn.config(text="Stop Bot")
            self.status_var.set("Bot running. Hotkeys are live.")

    # ---- status queue / thread bridge ----------------------------------------

    def _queue_status(self, name: str, status: str):
        # Called from a RotationRunner's worker thread -- never touch widgets here.
        self.status_queue.put((name, status))

    def _poll_status_queue(self):
        try:
            while True:
                name, payload = self.status_queue.get_nowait()
                if name == "__capture__":
                    self._on_hotkey_captured(payload)
                elif name == "__cancel_capture__":
                    self._on_cancel_key_captured(payload)
                elif name == "__reset_capture__":
                    self._on_reset_key_captured(payload)
                elif name == "__pause_capture__":
                    self._on_pause_key_captured(payload)
                else:
                    self._refresh_rotation_tree()
        except queue.Empty:
            pass
        self.after(200, self._poll_status_queue)

    # ---- shutdown -------------------------------------------------------------

    def _on_close(self):
        self.rotation_manager.stop_all()
        keyboard.unhook_all()
        self.destroy()
