import copy
import os
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk

import keyboard
import sv_ttk

from poe2bot import controller, storage
from poe2bot.executor import RotationManager, STATUS_RUNNING
from poe2bot.hotkeys import HotkeyManager, display_name
from poe2bot.log_setup import get_logger
from poe2bot.models import Rotation, validate_rotation, replace_step_fields

from poe2bot.gui.activity_window import ActivityWindow
from poe2bot.gui.rotation_list import RotationListMixin
from poe2bot.gui.step_editor import StepEditorMixin
from poe2bot.gui.drag_drop import DragDropMixin
from poe2bot.gui.calibration import CalibrationMixin
from poe2bot.gui.conditions import ConditionsMixin
from poe2bot.gui.hotkeys_ui import HotkeysMixin

log = get_logger()


class App(tk.Tk, RotationListMixin, StepEditorMixin, DragDropMixin,
          CalibrationMixin, ConditionsMixin, HotkeysMixin):
    def __init__(self):
        super().__init__()
        self.title("POE2 Rotation Bot")

        sv_ttk.set_theme("dark")
        self._sync_root_background()

        self.status_queue = queue.Queue()
        self.activity_queue = queue.Queue()
        self.activity_window = None  # ActivityWindow, created lazily on first STATUS_RUNNING
        self.rotation_manager = RotationManager(
            on_status_change=self._queue_status, on_activity=self._queue_activity)
        self.hotkey_manager = HotkeyManager(self.rotation_manager)
        self.bot_enabled = True

        self.rotations = {}          # name -> Rotation, mirrors what's on disk
        self.editing_original_name = None    # name of rotation being edited, or None if new/unsaved
        self.editing_steps = []              # working list[Step] for the form
        self.pending_hotkey = None            # hotkey chosen in this edit session (may be unchanged)
        self.pending_cancel_key = None        # cancel key chosen in this edit session (may be unchanged)
        self.pending_reset_key = None         # reset key chosen in this edit session (may be unchanged)
        self.pending_pause_key = None         # pause key chosen in this edit session (may be unchanged)
        self.step_ready_match_type = "image"  # "image" or "pixel" -- which method the step being edited uses
        self.step_ready_template = None       # cooldown-check filename pending for the step being edited
        self.step_ready_region = None         # (left, top, width, height) absolute screen coords, or None
        self.step_ready_search_mode = "exact"  # "exact" or "area" -- which image-match strategy the step being edited uses
        self.step_ready_search_region = None  # (left, top, width, height) absolute screen coords, only when search_mode == "area"
        self.step_ready_pixel_pos = None      # (x, y) absolute screen coords, for pixel-match mode
        self.step_ready_pixel_color = None    # (r, g, b) expected "ready" color, for pixel-match mode
        self.step_buff_check = None           # Condition for the step being edited's buff check, or None
        # Snapshots of the form's core/ready-check/buff-check fields as of the last
        # _on_select_step, kept independently (not one combined snapshot) so Add
        # Step/Add Sleep can tell exactly which *section* of the form the user has
        # touched since selecting -- e.g. changing only the Key must not also carry
        # a stale, untouched Cooldown Check over onto what's meant to be a new step
        # (see _discard_selected_step_edits).
        self._selected_step_core_snapshot = None
        self._selected_step_ready_snapshot = None
        self._selected_step_buff_snapshot = None
        self._steps_tree_render_order = []    # editing_steps order as of the last _refresh_steps_tree,
                                               # so a manual row collapse/expand can be re-attached to the
                                               # right Step object (by identity) even after a reorder
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
        self.step_name_var = tk.StringVar(value="Skill")
        self.step_key_var = tk.StringVar()
        self.step_delay_var = tk.StringVar(value="20")
        self.step_jitter_var = tk.StringVar(value="10")
        self.step_hold_var = tk.StringVar(value="30")
        self.step_hold_jitter_var = tk.StringVar(value="10")
        self.step_repeat_var = tk.StringVar(value="1")
        for label, var, width in (("Name", self.step_name_var, 12),):
            ttk.Label(edit_row, text=label).pack(side="left")
            ttk.Entry(edit_row, textvariable=var, width=width).pack(side="left", padx=(2, 8))
        ttk.Label(edit_row, text="Key").pack(side="left")
        ttk.Entry(edit_row, textvariable=self.step_key_var, width=8).pack(side="left", padx=(2, 4))
        self.capture_step_key_btn = ttk.Button(
            edit_row, text="Capture Controller Button", command=self._on_capture_step_key_clicked)
        self.capture_step_key_btn.pack(side="left", padx=(0, 8))
        for label, var, width in (
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
        ttk.Button(condition_btns, text="Add Timer Condition...",
                   command=self._on_add_timer_condition_clicked).pack(side="left", padx=(0, 4))
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
        self.condition_negate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(condition_name_row, text="Negate (fire if NOT matched)",
                        variable=self.condition_negate_var,
                        command=self._on_toggle_condition_negate).pack(side="left", padx=(12, 0))

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
        ttk.Button(bottom, text="Show Activity Window",
                   command=self._on_show_activity_window_clicked).pack(side="right", padx=(0, 8))

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
        step = self._read_step_form(
            conditions=self.editing_steps[step_idx].conditions,
            is_new_step=False, original_key=self.editing_steps[step_idx].key)
        if step is None:
            return False
        # In place, preserving identity -- see StepEditorMixin._update_selected_step.
        replace_step_fields(self.editing_steps[step_idx], step)
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
        else:
            # Two different display names can still sanitize to the same filename
            # (storage._slugify folds case/punctuation, e.g. "Fire Ball" and
            # "Fire-Ball" both become fire_ball.json) -- saving would silently
            # overwrite whichever rotation got there first, with no warning, so
            # this is checked by comparing actual on-disk paths, not just names.
            new_path = os.path.normcase(os.path.normpath(storage.path_for(name, rotation.folder)))
            for other_name, other_rotation in self.rotations.items():
                if other_name == self.editing_original_name:
                    continue
                other_path = os.path.normcase(os.path.normpath(
                    storage.path_for(other_name, other_rotation.folder)))
                if other_path == new_path:
                    problems.append(
                        f"'{name}' would save to the same file as existing rotation '{other_name}' "
                        f"(both simplify to the same filename) -- choose a more distinct name.")
                    break
        if rotation.hotkey and rotation.hotkey == self.hotkey_manager.panic_key:
            problems.append(f"'{display_name(rotation.hotkey)}' is reserved as the panic/stop-all key.")
        if problems:
            messagebox.showerror("Cannot save rotation", "\n".join(problems))
            return

        if rotation.hotkey:
            sharing_with = [n for n in self.hotkey_manager.bound_to(rotation.hotkey)
                             if n != self.editing_original_name]
            if sharing_with and not messagebox.askyesno(
                    "Hotkey already in use",
                    f"'{display_name(rotation.hotkey)}' is already bound to "
                    f"{', '.join(sharing_with)}. Also bind it to this rotation?"):
                return

        # Rebind hotkey first (release whatever this rotation held before, bind the new
        # choice) -- before any destructive rename/move step, so a conflict here (shouldn't
        # happen given the pre-checks above, but kept as a defensive guard) can't leave
        # the old file deleted with nothing saved in its place.
        try:
            self.hotkey_manager.rebind(rotation.hotkey, rotation.name)
        except ValueError as e:
            messagebox.showerror("Hotkey conflict", str(e))
            return

        # A rotation's file path depends on both its name and its folder, so either
        # changing means the old file needs to go, not just a plain rename.
        old_rotation = self.rotations.get(self.editing_original_name) if self.editing_original_name else None
        moved = old_rotation is not None and (
            old_rotation.name != rotation.name or old_rotation.folder != rotation.folder)
        if moved:
            # move_rotation writes the new file before removing the old one, so a
            # crash in between leaves a recoverable stray duplicate rather than
            # losing the rotation outright.
            storage.move_rotation(rotation, old_rotation.name, old_rotation.folder)
            self.rotation_manager.unload(old_rotation.name)
            self.hotkey_manager.unbind(old_rotation.name)
            self.hotkey_manager.set_cancel_key(old_rotation.name, None)
            self.hotkey_manager.set_reset_key(old_rotation.name, None)
            self.hotkey_manager.set_pause_key(old_rotation.name, None)
            del self.rotations[old_rotation.name]
        else:
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

    def _queue_activity(self, name: str, message: str):
        # Called from a RotationRunner's worker thread -- never touch widgets here.
        self.activity_queue.put((name, message, time.time()))

    def _ensure_activity_window(self):
        if self.activity_window is None or not self.activity_window.winfo_exists():
            self.activity_window = ActivityWindow(self)

    def _on_show_activity_window_clicked(self):
        # The window otherwise only (re)appears on a rotation's next
        # STATUS_RUNNING transition -- for a Loop rotation with no
        # pause/reset/focus-loss in between, that may never happen again for
        # the rest of its run, so closing it had no way back short of
        # stopping and restarting the rotation. This recreates it (fresh) if
        # it was closed, or just raises it if it's merely hidden behind
        # another window.
        self._ensure_activity_window()
        self.activity_window.deiconify()
        self.activity_window.lift()
        self.activity_window.focus_force()

    def _poll_status_queue(self):
        try:
            while True:
                name, payload = self.status_queue.get_nowait()
                # The "__*_capture__" sentinels are pushed by HotkeysMixin's/
                # StepEditorMixin's _capture_*_worker methods (poe2bot/gui/
                # hotkeys_ui.py, poe2bot/gui/step_editor.py) from a
                # background thread -- this is where that hop back to the Tk
                # thread actually gets consumed and dispatched to the matching
                # _on_*_captured method.
                if name == "__capture__":
                    self._on_hotkey_captured(payload)
                elif name == "__cancel_capture__":
                    self._on_cancel_key_captured(payload)
                elif name == "__reset_capture__":
                    self._on_reset_key_captured(payload)
                elif name == "__pause_capture__":
                    self._on_pause_key_captured(payload)
                elif name == "__step_key_capture__":
                    self._on_step_key_captured(payload)
                else:
                    status = payload
                    self._refresh_rotation_tree()
                    if status == STATUS_RUNNING:
                        self._ensure_activity_window()
                        self.activity_window.ensure_pane(name)
                    if self.activity_window is not None and self.activity_window.winfo_exists():
                        self.activity_window.set_pane_state(name, status)
        except queue.Empty:
            pass
        # Drained only after status_queue is fully drained above, so a
        # rotation's pane (created on its STATUS_RUNNING status message) always
        # exists by the time its own activity messages -- necessarily enqueued
        # after that status change -- are processed.
        try:
            while True:
                name, message, ts = self.activity_queue.get_nowait()
                if self.activity_window is not None and self.activity_window.winfo_exists():
                    self.activity_window.append(name, message, ts)
        except queue.Empty:
            pass
        self.after(200, self._poll_status_queue)

    # ---- shutdown -------------------------------------------------------------

    def _on_close(self):
        self.rotation_manager.stop_all()
        keyboard.unhook_all()
        controller.release_all()
        self.destroy()
