import copy
import os
import queue
import time
import tkinter as tk
from tkinter import ttk

import keyboard

from poe2bot import app_state, controller, storage
from poe2bot.executor import RotationManager, STATUS_RUNNING
from poe2bot.hotkeys import HotkeyManager, display_name
from poe2bot.log_setup import get_logger
from poe2bot.models import Rotation, validate_rotation, replace_step_fields, folder_in_scope

from poe2bot.gui import dialogs as messagebox
from poe2bot.gui import geometry, theme
from poe2bot.gui.activity_window import ActivityWindow
from poe2bot.gui.settings_window import SettingsWindow
from poe2bot.gui.rotation_list import RotationListMixin
from poe2bot.gui.step_editor import StepEditorMixin
from poe2bot.gui.drag_drop import DragDropMixin
from poe2bot.gui.calibration import CalibrationMixin
from poe2bot.gui.conditions import ConditionsMixin, CONDITION_ACTION_LABELS
from poe2bot.gui.hotkeys_ui import HotkeysMixin
from poe2bot.gui.widgets import CollapsibleSection
from poe2bot.gui.constants import STATUS_COLORS

log = get_logger()


class App(tk.Tk, RotationListMixin, StepEditorMixin, DragDropMixin,
          CalibrationMixin, ConditionsMixin, HotkeysMixin):
    def __init__(self):
        super().__init__()
        self.title("POE2 Rotation Bot")

        state = app_state.load_state()
        self._theme = state["theme"]
        theme.apply_theme(self._theme, root=self)
        self._sync_root_background()

        self.status_queue = queue.Queue()
        self.activity_queue = queue.Queue()
        self.activity_window = None  # ActivityWindow, created lazily on first STATUS_RUNNING
        self.settings_window = None  # SettingsWindow, created lazily on first "Settings..." click
        self.rotation_manager = RotationManager(
            on_status_change=self._queue_status, on_activity=self._queue_activity)
        self.hotkey_manager = HotkeyManager(self.rotation_manager)
        self.bot_enabled = True

        self.active_folder = state["active_folder"]  # None = "(All Folders)" -- no scoping restriction
        self.active_device = state["active_device"]  # "keyboard" or "controller"

        self.rotations = {}          # name -> Rotation, mirrors what's on disk
        self.editing_original_name = None    # name of rotation being edited, or None if new/unsaved
        self.editing_steps = []              # working list[Step] for the form
        self.pending_hotkey = None            # hotkey chosen in this edit session (may be unchanged)
        self.pending_cancel_key = None        # cancel key chosen in this edit session (may be unchanged)
        self.pending_reset_key = None         # reset key chosen in this edit session (may be unchanged)
        self.pending_pause_key = None         # pause key chosen in this edit session (may be unchanged)
        # Snapshot of the form's core fields as of the last _on_select_step -- lets
        # Add Step/Add Sleep tell whether the user has touched them since selecting
        # (see _discard_selected_step_edits), and the auto-commit-on-navigate /
        # dirty-indicator logic tell whether there's an uncommitted edit pending.
        self._selected_step_core_snapshot = None
        self._steps_tree_render_order = []    # editing_steps order as of the last _refresh_steps_tree,
                                               # so a manual row collapse/expand can be re-attached to the
                                               # right Step object (by identity) even after a reorder
        self._step_clipboard = []             # list[Step], set by Copy -- lives on the App, so it
                                               # survives switching rotations (enables cross-rotation paste)
        self._condition_clipboard = []        # list[Condition], set by Copy Conditions -- same
                                               # cross-rotation-paste lifetime as _step_clipboard above
        self._drag_candidate = None    # list[(step_idx, cond_idx_or_None)] being dragged, or None
        self._drag_active = False      # True once the mouse has moved past the drag threshold
        self._drag_start_xy = (0, 0)
        self._drop_target_iid = None   # currently tag-highlighted row during a drag, if any
        self._selected_step_ref = None        # the actual Step object currently loaded into the
                                               # Selected Step form, or None -- tracked by identity
                                               # (not index) so navigating to a different tree row
                                               # can auto-commit an in-progress edit onto the right
                                               # object even after a reorder (see _on_select_step)
        self._suppress_commit_on_select = False  # True only while _discard_selected_step_edits is
                                                  # clearing the tree's selection itself, so that
                                                  # doesn't get misread as "user navigated away" and
                                                  # trigger an unwanted auto-commit (see there)
        self._saved_steps_snapshot = None     # deepcopy of editing_steps as of the last load/save,
                                               # for the unsaved-changes indicator (see
                                               # _rotation_is_dirty / _capture_saved_snapshot)
        self._saved_core_snapshot = None      # same idea, for the rotation-level fields
        self._section_collapse_overrides = {}  # id(step) -> {"cooldown"/"buff"/"conditions": bool},
                                                # in-session only (not persisted) manual overrides of
                                                # each CollapsibleSection's smart per-step default
        self.rotation_filter_var = tk.StringVar()  # substring filter for the rotation list
        self._calibration_hint_shown = False  # show the "here's how calibration works" popup
                                               # at most once per session -- see CalibrationMixin

        self._build_widgets()
        self._load_rotations_from_disk()
        self._sweep_templates()
        self._refresh_rotation_tree()
        self._new_rotation()
        geometry.size_window_to_contents(self)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._poll_status_queue)

    # ---- startup -----------------------------------------------------

    def _load_rotations_from_disk(self):
        for name, rotation in storage.load_all_rotations().items():
            self.rotations[name] = rotation
            self.rotation_manager.load(rotation)
            if self._folder_in_scope(rotation.folder):
                self._bind_rotation_hotkeys(rotation)

    # ---- Active Folder / Active Device scoping --------------------------------

    def _folder_in_scope(self, folder: str) -> bool:
        return folder_in_scope(folder, self.active_folder)

    def _known_folder_prefixes(self) -> list:
        """Every folder path AND all of its ancestor prefixes, across every
        loaded rotation -- so a character organized as "Warrior/Boss" +
        "Warrior/Farm" with nothing directly in "Warrior" can still be
        picked as one combined Active Folder scope."""
        prefixes = set()
        for rotation in self.rotations.values():
            if not rotation.folder:
                continue
            parts = rotation.folder.split("/")
            for i in range(1, len(parts) + 1):
                prefixes.add("/".join(parts[:i]))
        return sorted(prefixes)

    def _bind_rotation_hotkeys(self, rotation: Rotation):
        """Applies rotation's own already-saved hotkey/cancel/reset/pause
        fields to HotkeyManager -- used at load time and whenever a
        rotation newly enters the active folder's scope. bind() (unlike
        set_*_key) isn't falsy-safe -- an unguarded bind(None, name) leaks
        a permanent, never-matching keyboard hook -- so the trigger key
        alone needs the truthiness guard."""
        if rotation.hotkey:
            try:
                self.hotkey_manager.bind(rotation.hotkey, rotation.name)
            except ValueError as e:
                messagebox.showwarning("Hotkey conflict", str(e))
        if rotation.cancel_key:
            self.hotkey_manager.set_cancel_key(rotation.name, rotation.cancel_key)
        if rotation.reset_key:
            self.hotkey_manager.set_reset_key(rotation.name, rotation.reset_key)
        if rotation.pause_key:
            self.hotkey_manager.set_pause_key(rotation.name, rotation.pause_key)

    def _clear_rotation_hotkeys(self, name: str):
        """Releases every live HotkeyManager registration for `name` --
        trigger, cancel, reset, and pause alike -- without touching the
        Rotation object's own saved fields (those stay exactly as
        persisted; this only affects what's currently enforced). Safe to
        call even if nothing is currently bound."""
        self.hotkey_manager.unbind(name)
        self.hotkey_manager.set_cancel_key(name, None)
        self.hotkey_manager.set_reset_key(name, None)
        self.hotkey_manager.set_pause_key(name, None)

    def _reconcile_hotkey_scope(self, rotation: Rotation, was_in_scope: bool):
        """Call after rotation.folder changes, or after self.active_folder
        changes, to bring HotkeyManager's live registrations in line with
        the new scope. Newly out-of-scope rotations get stopped (if
        running) and unbound; newly in-scope rotations get (re-)bound from
        their own already-saved fields -- no retyping needed either way."""
        now_in_scope = self._folder_in_scope(rotation.folder)
        if was_in_scope and not now_in_scope:
            self.rotation_manager.cancel(rotation.name)
            self._clear_rotation_hotkeys(rotation.name)
        elif now_in_scope and not was_in_scope:
            self._bind_rotation_hotkeys(rotation)

    def _on_active_folder_changed(self, _event=None):
        selected = self.active_folder_var.get()
        new_active = None if selected == "(All Folders)" else selected
        if new_active == self.active_folder:
            return
        old_active = self.active_folder
        self.active_folder = new_active
        for rotation in self.rotations.values():
            self._reconcile_hotkey_scope(rotation, folder_in_scope(rotation.folder, old_active))
        app_state.save_state(self.active_folder, self.active_device, self._theme)
        self._refresh_rotation_tree()

    def _on_active_device_changed(self):
        new_device = self.active_device_var.get()
        if new_device == self.active_device:
            return
        self.active_device = new_device
        # Avoid swapping a step's key (or a rotation's hotkey) out from under
        # a RotationRunner mid-fire -- the object mutated below is the exact
        # same one a running thread reads.
        self.rotation_manager.stop_all()
        for rotation in self.rotations.values():
            rotation.hotkey, rotation.alt_hotkey = rotation.alt_hotkey, rotation.hotkey
            rotation.cancel_key, rotation.alt_cancel_key = rotation.alt_cancel_key, rotation.cancel_key
            rotation.reset_key, rotation.alt_reset_key = rotation.alt_reset_key, rotation.reset_key
            rotation.pause_key, rotation.alt_pause_key = rotation.alt_pause_key, rotation.pause_key
            for step in rotation.steps:
                step.key, step.alt_key = step.alt_key, step.key
            storage.save_rotation(rotation)
            if self._folder_in_scope(rotation.folder):
                self._clear_rotation_hotkeys(rotation.name)
                self._bind_rotation_hotkeys(rotation)
        if self.editing_original_name in self.rotations:
            self._load_rotation_into_form(self.rotations[self.editing_original_name])
        app_state.save_state(self.active_folder, self.active_device, self._theme)
        self._refresh_rotation_tree()

    # ---- widget layout -------------------------------------------------

    def _build_widgets(self):
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")

        folder_scope_row = ttk.Frame(left)
        folder_scope_row.pack(fill="x", pady=(0, 4))
        ttk.Label(folder_scope_row, text="Active Folder:").pack(side="left")
        self.active_folder_var = tk.StringVar(value=self.active_folder or "(All Folders)")
        self.active_folder_combo = ttk.Combobox(
            folder_scope_row, textvariable=self.active_folder_var, state="readonly", width=16)
        self.active_folder_combo.pack(side="left", padx=(4, 0))
        self.active_folder_combo.bind("<<ComboboxSelected>>", self._on_active_folder_changed)

        ttk.Label(left, text="Rotations").pack(anchor="w")
        filter_row = ttk.Frame(left)
        filter_row.pack(fill="x", pady=(2, 4))
        ttk.Label(filter_row, text="Filter:").pack(side="left")
        filter_entry = ttk.Entry(filter_row, textvariable=self.rotation_filter_var)
        filter_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        filter_entry.bind("<KeyRelease>", lambda _e: self._refresh_rotation_tree())

        self.rotation_tree = ttk.Treeview(
            left, columns=("status",), show="tree headings", height=20, selectmode="extended")
        self.rotation_tree.heading("#0", text="Name")
        self.rotation_tree.column("#0", width=180)
        self.rotation_tree.heading("status", text="Status")
        self.rotation_tree.column("status", width=110, anchor="w")
        self.rotation_tree.pack(fill="both", expand=True)
        self.rotation_tree.bind("<<TreeviewSelect>>", self._on_select_rotation)
        self.rotation_tree.bind("<Button-3>", self._on_rotation_tree_right_click)
        for status, color in STATUS_COLORS.items():
            if color:
                self.rotation_tree.tag_configure(status, foreground=color)
        self._folder_nodes = {}   # folder path -> tree item id, rebuilt each _refresh_rotation_tree()

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="New", command=self._new_rotation).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Copy", command=self._copy_rotation).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Delete", style=theme.DANGER_BUTTON_STYLE,
                   command=self._delete_rotation).pack(side="left", expand=True, fill="x")

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

        # ---- key bindings: one compact grid instead of 4 near-duplicate rows ----
        self.hotkey_label_var = tk.StringVar(value="(unbound)")
        self.cancel_key_label_var = tk.StringVar(value="(unbound)")
        self.reset_key_label_var = tk.StringVar(value="(unbound)")
        self.pause_key_label_var = tk.StringVar(value="(unbound)")
        keys_frame = ttk.Frame(right)
        keys_frame.pack(fill="x", pady=(6, 4))
        key_bind_rows = (
            ("Hotkey:", self.hotkey_label_var, "bind_hotkey_btn",
             self._on_bind_hotkey_clicked, "Unbind", self._on_unbind_clicked),
            ("Cancel Key:", self.cancel_key_label_var, "bind_cancel_btn",
             self._on_bind_cancel_clicked, "Clear", self._on_clear_cancel_key),
            ("Reset Key:", self.reset_key_label_var, "bind_reset_btn",
             self._on_bind_reset_clicked, "Clear", self._on_clear_reset_key),
            ("Pause Key:", self.pause_key_label_var, "bind_pause_btn",
             self._on_bind_pause_clicked, "Clear", self._on_clear_pause_key),
        )
        for row_i, (label, var, btn_attr, bind_cmd, clear_text, clear_cmd) in enumerate(key_bind_rows):
            ttk.Label(keys_frame, text=label).grid(row=row_i, column=0, sticky="w", pady=2)
            ttk.Label(keys_frame, textvariable=var, width=14).grid(row=row_i, column=1, padx=(4, 8), sticky="w")
            btn = ttk.Button(keys_frame, text="Bind...", width=10, command=bind_cmd)
            btn.grid(row=row_i, column=2, padx=(0, 4))
            setattr(self, btn_attr, btn)
            ttk.Button(keys_frame, text=clear_text, style=theme.DANGER_BUTTON_STYLE,
                       command=clear_cmd).grid(row=row_i, column=3, padx=(0, 4), sticky="w")
        ttk.Button(keys_frame, text="Unbind All", style=theme.DANGER_BUTTON_STYLE,
                   command=self._unbind_all_rotations).grid(row=0, column=4, padx=(12, 0), sticky="w")
        reset_extra = ttk.Frame(keys_frame)
        reset_extra.grid(row=2, column=4, padx=(12, 0), sticky="w")
        ttk.Label(reset_extra, text="Delay (ms)").pack(side="left", padx=(0, 2))
        self.reset_delay_var = tk.StringVar(value="0")
        ttk.Entry(reset_extra, textvariable=self.reset_delay_var, width=6).pack(side="left")
        ttk.Label(keys_frame,
                  text="Cancel = e.g. your dodge key, stops instantly. Reset restarts from step 1. "
                       "Pause freezes in place.",
                  foreground="gray").grid(row=4, column=0, columnspan=5, sticky="w", pady=(4, 0))

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

        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True, pady=(8, 4))
        self.tree = ttk.Treeview(
            tree_frame, columns=("key", "delay", "jitter", "hold", "hold_jitter", "repeat"),
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
        # A plain Treeview clips silently once its rows outgrow either its own
        # `height` or a manually shrunk window (see _size_to_fit_contents's
        # free-resize note above) -- a scrollbar makes the overflow reachable
        # again instead of just invisible.
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
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
        for text, cmd, style in (
            ("Add Step", self._add_step, None), ("Add Sleep", self._add_sleep_step, None),
            ("Copy", self._on_copy_clicked, None), ("Paste", self._on_paste_clicked, None),
            ("Remove Selected", self._remove_selected_step, theme.DANGER_BUTTON_STYLE),
            ("Move Up", self._move_step_up, None), ("Move Down", self._move_step_down, None),
        ):
            kwargs = {"style": style} if style else {}
            ttk.Button(step_btns, text=text, command=cmd, **kwargs).pack(side="left", padx=(0, 4))

        step_fields_group = ttk.LabelFrame(right, text="Selected Step", padding=6)
        step_fields_group.pack(fill="x", pady=(0, 6))
        self.step_name_var = tk.StringVar(value="Skill")
        self.step_key_var = tk.StringVar()
        self.step_delay_var = tk.StringVar(value="20")
        self.step_jitter_var = tk.StringVar(value="10")
        self.step_hold_var = tk.StringVar(value="30")
        self.step_hold_jitter_var = tk.StringVar(value="10")
        self.step_repeat_var = tk.StringVar(value="1")

        identity_row = ttk.Frame(step_fields_group)
        identity_row.pack(fill="x")
        ttk.Label(identity_row, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(identity_row, textvariable=self.step_name_var, width=14).grid(
            row=0, column=1, padx=(4, 12), sticky="w")
        ttk.Label(identity_row, text="Key").grid(row=0, column=2, sticky="w")
        ttk.Entry(identity_row, textvariable=self.step_key_var, width=10).grid(
            row=0, column=3, padx=(4, 8), sticky="w")
        self.capture_step_key_btn = ttk.Button(
            identity_row, text="Capture Controller Button", command=self._on_capture_step_key_clicked)
        self.capture_step_key_btn.grid(row=0, column=4, padx=(0, 4))
        self.capture_step_mouse_btn = ttk.Button(
            identity_row, text="Capture Mouse Button", command=self._on_capture_step_mouse_clicked)
        self.capture_step_mouse_btn.grid(row=0, column=5, padx=(0, 4))

        def add_timing_field(parent, col, label, var, width):
            ttk.Label(parent, text=label).grid(row=0, column=col * 2, sticky="w", padx=(0 if col == 0 else 10, 2))
            entry = ttk.Entry(parent, textvariable=var, width=width)
            entry.grid(row=0, column=col * 2 + 1, sticky="w")
            return entry

        timing_row = ttk.Frame(step_fields_group)
        timing_row.pack(fill="x", pady=(6, 0))
        self.step_delay_entry = add_timing_field(timing_row, 0, "Delay", self.step_delay_var, 6)
        self.step_jitter_entry = add_timing_field(timing_row, 1, "Jitter", self.step_jitter_var, 6)
        self.step_hold_entry = add_timing_field(timing_row, 2, "Hold", self.step_hold_var, 6)
        self.step_hold_jitter_entry = add_timing_field(timing_row, 3, "Hold Jitter", self.step_hold_jitter_var, 6)
        self.step_repeat_entry = add_timing_field(timing_row, 4, "Repeat", self.step_repeat_var, 4)

        repeat_row = ttk.Frame(step_fields_group)
        repeat_row.pack(fill="x", pady=(6, 0))
        self.step_repeat_combine_hold_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(repeat_row, text="Combine Hold",
                         variable=self.step_repeat_combine_hold_var).pack(side="left")
        ttk.Label(repeat_row,
                  text="(hold the key once for Hold × Repeat ms, then a single Delay, instead of "
                       "repeating press/release + Delay Repeat times)",
                  foreground="gray").pack(side="left", padx=(8, 0))

        self.step_form_error_var = tk.StringVar(value="")
        self.step_form_error_label = ttk.Label(
            step_fields_group, textvariable=self.step_form_error_var, foreground=theme.DANGER_COLOR)
        # Not packed here -- only shown while there's an actual error, see _read_step_form.

        self.conditions_section = CollapsibleSection(
            right, title="Conditions", padding=6, start_collapsed=True,
            on_toggle=lambda collapsed: self._on_section_toggled("conditions", collapsed))
        self.conditions_section.pack(fill="x", pady=(0, 6))
        condition_btns = ttk.Frame(self.conditions_section.body)
        condition_btns.pack(fill="x")
        ttk.Button(condition_btns, text="Add Image Condition...",
                   command=self._on_add_image_condition_clicked).pack(side="left", padx=(0, 4))
        ttk.Button(condition_btns, text="Add Pixel Condition...",
                   command=self._on_add_pixel_condition_clicked).pack(side="left", padx=(0, 4))
        ttk.Button(condition_btns, text="Add Timer Condition...",
                   command=self._on_add_timer_condition_clicked).pack(side="left", padx=(0, 4))
        ttk.Button(condition_btns, text="Copy Conditions",
                   command=self._on_copy_conditions_clicked).pack(side="left", padx=(8, 4))
        ttk.Button(condition_btns, text="Paste Conditions",
                   command=self._on_paste_conditions_clicked).pack(side="left", padx=(0, 4))
        ttk.Label(condition_btns,
                  text="(double-click a condition in the list to recalibrate its match;"
                       " use Move Up/Move Down in Step Actions to reorder it)",
                  foreground="gray").pack(side="left", padx=(8, 0))

        condition_name_row = ttk.Frame(self.conditions_section.body)
        condition_name_row.pack(fill="x", pady=(6, 0))
        ttk.Label(condition_name_row, text="Name:").pack(side="left")
        self.condition_name_var = tk.StringVar()
        ttk.Entry(condition_name_row, textvariable=self.condition_name_var, width=16).pack(
            side="left", padx=(2, 12))
        ttk.Label(condition_name_row, text="Action:").pack(side="left")
        self.condition_action_var = tk.StringVar(value=CONDITION_ACTION_LABELS["fire"])
        condition_action_combo = ttk.Combobox(
            condition_name_row, textvariable=self.condition_action_var,
            values=list(CONDITION_ACTION_LABELS.values()), state="readonly", width=22)
        condition_action_combo.pack(side="left", padx=(2, 12))
        condition_action_combo.bind("<<ComboboxSelected>>", self._on_condition_action_changed)
        self.condition_negate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(condition_name_row, text="Negate (invert the match)",
                        variable=self.condition_negate_var).pack(side="left")

        condition_extra_row = ttk.Frame(self.conditions_section.body)
        condition_extra_row.pack(fill="x", pady=(4, 0))
        self.condition_timeout_frame = ttk.Frame(condition_extra_row)
        ttk.Label(self.condition_timeout_frame, text="Wait up to (ms)").pack(side="left")
        self.condition_timeout_var = tk.StringVar(value="0")
        ttk.Entry(self.condition_timeout_frame, textvariable=self.condition_timeout_var, width=6).pack(
            side="left", padx=(2, 0))
        self.condition_hold_frame = ttk.Frame(condition_extra_row)
        ttk.Label(self.condition_hold_frame, text="Hold override (ms)").pack(side="left")
        self.condition_hold_var = tk.StringVar(value="")
        ttk.Entry(self.condition_hold_frame, textvariable=self.condition_hold_var, width=6).pack(
            side="left", padx=(2, 8))
        ttk.Label(self.condition_hold_frame, text="Delay override (ms)").pack(side="left")
        self.condition_delay_var = tk.StringVar(value="")
        ttk.Entry(self.condition_hold_frame, textvariable=self.condition_delay_var, width=6).pack(
            side="left", padx=(2, 0))
        # Neither frame packed yet -- _refresh_condition_extra_visibility shows
        # whichever one is relevant to the currently-selected Action.

        condition_apply_row = ttk.Frame(self.conditions_section.body)
        condition_apply_row.pack(fill="x", pady=(4, 0))
        ttk.Button(condition_apply_row, text="Update Selected Condition",
                   command=self._on_update_condition_clicked).pack(side="left")

        save_row = ttk.Frame(right)
        save_row.pack(fill="x", pady=(4, 0))
        ttk.Button(save_row, text="Revert to Saved", style=theme.DANGER_BUTTON_STYLE,
                   command=self._on_revert_clicked).pack(side="right", padx=(4, 0))
        self.save_rotation_btn = ttk.Button(save_row, text="Save Rotation", command=self._save_rotation)
        self.save_rotation_btn.pack(side="right")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(side="bottom", fill="x")
        self.toggle_btn = ttk.Button(bottom, text="Stop Bot", command=self._toggle_bot)
        self.toggle_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Bot running. Hotkeys are live.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", padx=8)
        # Bound to the "Keyboard"/"Controller" radio buttons in SettingsWindow --
        # created here, unconditionally, since _on_active_device_changed reads it
        # regardless of whether Settings has ever been opened this session.
        self.active_device_var = tk.StringVar(value=self.active_device)
        ttk.Button(bottom, text="Settings...", command=self._on_show_settings_clicked).pack(side="right")

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
        self._theme = theme.toggle_theme(self._theme)
        theme.apply_theme(self._theme, root=self)
        self._sync_root_background()
        self.tree.tag_configure("drop_target", background=self._drop_target_color())
        app_state.save_state(self.active_folder, self.active_device, self._theme)
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.refresh_theme_label()
        if self.activity_window is not None and self.activity_window.winfo_exists():
            self.activity_window.refresh_theme()

    # ---- unsaved-changes indicator --------------------------------------------

    def _capture_saved_snapshot(self):
        """Baseline to compare against for the unsaved-changes indicator --
        call whenever the form starts exactly matching what's considered
        "saved" (after loading a rotation, after a successful save, or for a
        brand new/duplicated rotation that has nothing to lose yet)."""
        self._saved_steps_snapshot = copy.deepcopy(self.editing_steps)
        self._saved_core_snapshot = self._rotation_core_field_snapshot()

    def _rotation_core_field_snapshot(self) -> tuple:
        return (
            self.name_var.get(), self.folder_var.get(), self.mode_var.get(),
            self.hotkey_label_var.get(), self.cancel_key_label_var.get(),
            self.reset_key_label_var.get(), self.reset_delay_var.get(),
            self.pause_key_label_var.get(), self.pause_mode_var.get(), self.pause_duration_var.get(),
        )

    def _rotation_is_dirty(self) -> bool:
        """True if anything currently on screen -- including an in-progress
        Selected Step edit not yet committed to editing_steps -- differs from
        the last-loaded/last-saved baseline. Recomputed cheaply on every
        status-queue poll tick rather than tracked imperatively at each
        mutation site, so no call site can forget to flag it."""
        if self._saved_steps_snapshot is None:
            return True
        # Only meaningful while a step is actually selected -- _selected_step_ref
        # is explicitly cleared to None whenever the form is blanked (see
        # StepEditorMixin._reset_step_core_fields), so this can't compare the
        # live form against a stale snapshot left over from a step that belongs
        # to a different rotation than the one now open.
        if (self._selected_step_ref is not None
                and self._core_step_form_snapshot() != self._selected_step_core_snapshot):
            return True
        return (self._rotation_core_field_snapshot() != self._saved_core_snapshot
                or self.editing_steps != self._saved_steps_snapshot)

    def _refresh_dirty_indicator(self):
        dirty = self._rotation_is_dirty()
        shown_name = self.editing_original_name or "New Rotation"
        self.title(f"POE2 Rotation Bot -- {shown_name}{' *' if dirty else ''}")
        self.save_rotation_btn.config(style=theme.ACCENT_BUTTON_STYLE if dirty else "TButton")

    # ---- save ---------------------------------------------------------------

    def _apply_pending_step_edits(self) -> bool:
        """Writes the step-editing form's current contents back into whichever
        step is currently selected (or owns the selected condition) -- so a
        mutating action (Save/Paste/Move/drag-drop) always persists what's
        currently on screen instead of silently discarding it. This is a
        different code path from the auto-commit-on-navigate in
        StepEditorMixin._commit_previous_step_edits_if_changed: that one
        fires when the selection is about to move to a *different* step (and
        resolves the target by identity, since the tree selection has
        already moved on by then); this one fires while the *same* step is
        still selected. Returns False (leaving the inline field error up
        from _read_step_form) if the form doesn't parse; True if there was
        nothing to apply (no step, or more than one row, selected -- with
        several selected there's no single clear target to apply the form
        to) or the apply succeeded."""
        selection = self.tree.selection()
        if not selection or len(selection) > 1:
            return True
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return True
        step_idx = parsed[0]
        step = self._read_step_form(
            conditions=self.editing_steps[step_idx].conditions,
            is_new_step=False, original_key=self.editing_steps[step_idx].key,
            alt_key=self.editing_steps[step_idx].alt_key)
        if step is None:
            return False
        # In place, preserving identity -- so a manually collapsed/expanded tree
        # row or CollapsibleSection override (both tracked by id(step)) doesn't
        # spring back to its default state on every apply, even one with no
        # actual field changes.
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
        # Looked up early (not just later for the rename/move check) so the
        # alt_* fields below -- which have no editing UI of their own, only
        # ever touched by the Active Device toggle -- get preserved rather
        # than silently reset to None by this fresh Rotation(...) call.
        old_rotation = self.rotations.get(self.editing_original_name) if self.editing_original_name else None
        rotation = Rotation(
            name=name,
            mode=self.mode_var.get(),
            hotkey=self.pending_hotkey,
            alt_hotkey=old_rotation.alt_hotkey if old_rotation else None,
            cancel_key=self.pending_cancel_key,
            alt_cancel_key=old_rotation.alt_cancel_key if old_rotation else None,
            reset_key=self.pending_reset_key,
            alt_reset_key=old_rotation.alt_reset_key if old_rotation else None,
            reset_delay_ms=reset_delay_ms,
            pause_key=self.pending_pause_key,
            alt_pause_key=old_rotation.alt_pause_key if old_rotation else None,
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
        if rotation.alt_hotkey and rotation.alt_hotkey == self.hotkey_manager.panic_key:
            problems.append(f"'{display_name(rotation.alt_hotkey)}' (alt) is reserved as the panic/stop-all key.")
        if problems:
            messagebox.showerror("Cannot save rotation", "\n".join(problems))
            return

        new_in_scope = self._folder_in_scope(rotation.folder)

        if rotation.hotkey:
            # bound_to() only reflects currently-live (in-scope) bindings -- also warn
            # about another rotation in the SAME folder sharing this hotkey even if
            # that folder isn't the active one right now, since it's just as real a
            # conflict the moment either rotation's folder becomes active.
            same_folder_conflicts = [
                r.name for r in self.rotations.values()
                if r.name != self.editing_original_name and r.folder == rotation.folder
                and r.hotkey == rotation.hotkey]
            sharing_with = list(dict.fromkeys(
                [n for n in self.hotkey_manager.bound_to(rotation.hotkey) if n != self.editing_original_name]
                + same_folder_conflicts))
            if sharing_with and not messagebox.askyesno(
                    "Hotkey already in use",
                    f"'{display_name(rotation.hotkey)}' is already bound to "
                    f"{', '.join(sharing_with)}. Also bind it to this rotation?"):
                return

        # Rebind hotkey first (release whatever this rotation held before, bind the new
        # choice) -- before any destructive rename/move step, so a conflict here (shouldn't
        # happen given the pre-checks above, but kept as a defensive guard) can't leave
        # the old file deleted with nothing saved in its place. Skipped entirely when this
        # rotation isn't in the Active Folder's scope -- it has nothing live to conflict with.
        if new_in_scope:
            try:
                self.hotkey_manager.rebind(rotation.hotkey, rotation.name)
            except ValueError as e:
                messagebox.showerror("Hotkey conflict", str(e))
                return

        # A rotation's file path depends on both its name and its folder, so either
        # changing means the old file needs to go, not just a plain rename.
        renamed = old_rotation is not None and old_rotation.name != rotation.name
        moved = old_rotation is not None and (renamed or old_rotation.folder != rotation.folder)
        if renamed:
            # Only a genuine rename needs the OLD name's live bindings released -- a
            # folder-only move keeps the same name, and the (possibly skipped) rebind
            # above already replaced whatever was live under it; clearing here too in
            # that case would immediately undo it, since old_rotation.name ==
            # rotation.name then.
            self._clear_rotation_hotkeys(old_rotation.name)
        if moved:
            # move_rotation writes the new file before removing the old one, so a
            # crash in between leaves a recoverable stray duplicate rather than
            # losing the rotation outright.
            storage.move_rotation(rotation, old_rotation.name, old_rotation.folder)
            self.rotation_manager.unload(old_rotation.name)
            del self.rotations[old_rotation.name]
        else:
            storage.save_rotation(rotation)

        self.rotation_manager.load(rotation)
        self.rotations[rotation.name] = rotation
        if new_in_scope:
            self.hotkey_manager.set_cancel_key(rotation.name, rotation.cancel_key)
            self.hotkey_manager.set_reset_key(rotation.name, rotation.reset_key)
            self.hotkey_manager.set_pause_key(rotation.name, rotation.pause_key)
        else:
            # Covers the case where this rotation had live bindings from before this
            # edit (e.g. it used to be in-scope) and is only now moving out of scope.
            self._clear_rotation_hotkeys(rotation.name)

        self._load_rotation_into_form(rotation)
        self._refresh_rotation_tree()
        self._sweep_templates()

    # ---- global start/stop --------------------------------------------------

    def _toggle_bot(self):
        if self.bot_enabled:
            self.rotation_manager.stop_all()
            self.hotkey_manager.disable_all()
            self.bot_enabled = False
            self.toggle_btn.config(text="Start Bot", style=theme.ACCENT_BUTTON_STYLE)
            self.status_var.set("Bot stopped. Hotkeys are inactive.")
        else:
            self.hotkey_manager.enable_all()
            self.bot_enabled = True
            self.toggle_btn.config(text="Stop Bot", style="TButton")
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

    def _ensure_settings_window(self):
        if self.settings_window is None or not self.settings_window.winfo_exists():
            self.settings_window = SettingsWindow(self)

    def _on_show_settings_clicked(self):
        # Recreated if closed, or just raised if merely hidden behind another
        # window -- same convention as _on_show_activity_window_clicked below.
        self._ensure_settings_window()
        self.settings_window.deiconify()
        self.settings_window.lift()
        self.settings_window.focus_force()

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
                elif name == "__step_mouse_capture__":
                    self._on_step_mouse_captured(payload)
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
        self._refresh_dirty_indicator()
        self.after(200, self._poll_status_queue)

    # ---- shutdown -------------------------------------------------------------

    def _on_close(self):
        self.rotation_manager.stop_all()
        keyboard.unhook_all()
        controller.release_all()
        self.destroy()
