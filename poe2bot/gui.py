import copy
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import keyboard
import pyautogui

from poe2bot import storage, templates
from poe2bot.executor import RotationManager
from poe2bot.hotkeys import HotkeyManager, display_name
from poe2bot.log_setup import get_logger
from poe2bot.models import Rotation, Step, validate_rotation

log = get_logger()

STATUS_LABELS = {
    "idle": "",
    "running": " (running)",
    "waiting_focus": " (waiting for game focus)",
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("POE2 Rotation Bot")
        self.geometry("760x480")

        self.status_queue = queue.Queue()
        self.rotation_manager = RotationManager(on_status_change=self._queue_status)
        self.hotkey_manager = HotkeyManager(self.rotation_manager)
        self.bot_enabled = True

        self.rotations = {}          # name -> Rotation, mirrors what's on disk
        self.editing_original_name = None    # name of rotation being edited, or None if new/unsaved
        self.editing_original_hotkey = None  # hotkey it had when editing started
        self.editing_steps = []              # working list[Step] for the form
        self.pending_hotkey = None            # hotkey chosen in this edit session (may be unchanged)
        self.step_ready_template = None       # cooldown-check filename pending for the step being edited
        self.step_ready_region = None         # (left, top, width, height) absolute screen coords, or None

        self._build_widgets()
        self._load_rotations_from_disk()
        self._sweep_templates()
        self._refresh_listbox()
        self._new_rotation()

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

    # ---- widget layout -------------------------------------------------

    def _build_widgets(self):
        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="Rotations").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=28, height=20, exportselection=False)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select_rotation)

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

        self.tree = ttk.Treeview(
            right, columns=("name", "key", "delay", "jitter", "hold", "hold_jitter"),
            show="headings", height=10)
        for col, label, width in (
            ("name", "Name", 110), ("key", "Key", 60), ("delay", "Delay (ms)", 90),
            ("jitter", "Jitter (ms)", 90), ("hold", "Hold (ms)", 90),
            ("hold_jitter", "Hold Jitter (ms)", 100),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(8, 4))
        self.tree.bind("<<TreeviewSelect>>", self._on_select_step)

        edit_row = ttk.Frame(right)
        edit_row.pack(fill="x")
        self.step_name_var = tk.StringVar()
        self.step_key_var = tk.StringVar()
        self.step_delay_var = tk.StringVar(value="100")
        self.step_jitter_var = tk.StringVar(value="0")
        self.step_hold_var = tk.StringVar(value="0")
        self.step_hold_jitter_var = tk.StringVar(value="0")
        for label, var, width in (
            ("Name", self.step_name_var, 12), ("Key", self.step_key_var, 8),
            ("Delay", self.step_delay_var, 6),
            ("Jitter", self.step_jitter_var, 6), ("Hold", self.step_hold_var, 6),
            ("Hold Jitter", self.step_hold_jitter_var, 6),
        ):
            ttk.Label(edit_row, text=label).pack(side="left")
            ttk.Entry(edit_row, textvariable=var, width=width).pack(side="left", padx=(2, 8))

        self.step_ready_timeout_var = tk.StringVar(value="300")
        self.step_ready_confidence_var = tk.StringVar(value="0.90")
        self.step_ready_status_var = tk.StringVar(value="No cooldown check")

        ready_row = ttk.Frame(right)
        ready_row.pack(fill="x", pady=(0, 4))
        ttk.Label(ready_row, textvariable=self.step_ready_status_var, width=24).pack(side="left")
        ttk.Label(ready_row, text="Timeout (ms)").pack(side="left", padx=(8, 2))
        ttk.Entry(ready_row, textvariable=self.step_ready_timeout_var, width=6).pack(side="left")
        ttk.Label(ready_row, text="Confidence").pack(side="left", padx=(8, 2))
        ttk.Entry(ready_row, textvariable=self.step_ready_confidence_var, width=5).pack(side="left")
        ttk.Button(ready_row, text="Calibrate...", command=self._on_calibrate_clicked).pack(side="left", padx=(8, 4))
        ttk.Button(ready_row, text="Clear", command=self._clear_ready_check).pack(side="left")

        step_btns = ttk.Frame(right)
        step_btns.pack(fill="x", pady=4)
        for text, cmd in (
            ("Add Step", self._add_step), ("Copy Selected", self._copy_selected_step),
            ("Update Selected", self._update_selected_step),
            ("Remove Selected", self._remove_selected_step),
            ("Move Up", self._move_step_up), ("Move Down", self._move_step_down),
        ):
            ttk.Button(step_btns, text=text, command=cmd).pack(side="left", padx=(0, 4))

        ttk.Button(right, text="Save Rotation", command=self._save_rotation).pack(anchor="e", pady=(8, 0))

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(side="bottom", fill="x")
        self.toggle_btn = ttk.Button(bottom, text="Stop Bot", command=self._toggle_bot)
        self.toggle_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Bot running. Hotkeys are live.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", padx=8)

    # ---- rotation list / selection --------------------------------------

    def _refresh_listbox(self):
        selected = self.editing_original_name
        self.listbox.delete(0, tk.END)
        for name in sorted(self.rotations.keys()):
            suffix = STATUS_LABELS.get(self.rotation_manager.status(name), "")
            self.listbox.insert(tk.END, f"{name}{suffix}")
        if selected in self.rotations:
            names = sorted(self.rotations.keys())
            self.listbox.selection_set(names.index(selected))

    def _on_select_rotation(self, _event):
        selection = self.listbox.curselection()
        if not selection:
            return
        name = sorted(self.rotations.keys())[selection[0]]
        self._load_rotation_into_form(self.rotations[name])

    def _load_rotation_into_form(self, rotation: Rotation):
        self.editing_original_name = rotation.name
        self.editing_original_hotkey = rotation.hotkey
        self.pending_hotkey = rotation.hotkey
        self.editing_steps = copy.deepcopy(rotation.steps)
        self.name_var.set(rotation.name)
        self.mode_var.set(rotation.mode)
        self.hotkey_label_var.set(display_name(rotation.hotkey))
        self._reset_ready_form()
        self._refresh_steps_tree()

    def _new_rotation(self):
        self.editing_original_name = None
        self.editing_original_hotkey = None
        self.pending_hotkey = None
        self.editing_steps = []
        self.name_var.set("New Rotation")
        self.mode_var.set("once")
        self.hotkey_label_var.set("(unbound)")
        self._reset_ready_form()
        self._refresh_steps_tree()
        self.listbox.selection_clear(0, tk.END)

    def _copy_rotation(self):
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo("No rotation selected", "Select a rotation in the list first.")
            return
        name = sorted(self.rotations.keys())[selection[0]]
        original = self.rotations[name]
        duplicate = Rotation(
            name=self._unique_rotation_name(f"{original.name} (copy)"),
            mode=original.mode,
            hotkey=None,  # can't share the original's hotkey -- bind a new one before saving
            steps=copy.deepcopy(original.steps),
        )
        self._load_rotation_into_form(duplicate)
        self.listbox.selection_clear(0, tk.END)

    def _unique_rotation_name(self, base_name: str) -> str:
        if base_name not in self.rotations:
            return base_name
        n = 2
        while f"{base_name} {n}" in self.rotations:
            n += 1
        return f"{base_name} {n}"

    def _reset_ready_form(self):
        self.step_ready_template = None
        self.step_ready_region = None
        self.step_ready_timeout_var.set("300")
        self.step_ready_confidence_var.set("0.90")
        self._refresh_ready_status()

    def _refresh_ready_status(self):
        if self.step_ready_template and self.step_ready_region:
            w, h = self.step_ready_region[2], self.step_ready_region[3]
            self.step_ready_status_var.set(f"Calibrated ({w}x{h})")
        else:
            self.step_ready_status_var.set("No cooldown check")

    def _delete_rotation(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        name = sorted(self.rotations.keys())[selection[0]]
        if not messagebox.askyesno("Delete rotation", f"Delete '{name}'?"):
            return
        rotation = self.rotations.pop(name)
        if rotation.hotkey:
            self.hotkey_manager.unbind(rotation.hotkey)
        self.rotation_manager.unload(name)
        storage.delete_rotation(name)
        self._refresh_listbox()
        self._new_rotation()
        self._sweep_templates()

    # ---- step editing ----------------------------------------------------

    def _refresh_steps_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.editing_steps):
            self.tree.insert("", tk.END, iid=str(i),
                              values=(step.name or step.key, step.key, step.delay_ms,
                                      step.jitter_ms, step.hold_ms, step.hold_jitter_ms))

    def _on_select_step(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
        step = self.editing_steps[int(selection[0])]
        self.step_name_var.set(step.name)
        self.step_key_var.set(step.key)
        self.step_delay_var.set(str(step.delay_ms))
        self.step_jitter_var.set(str(step.jitter_ms))
        self.step_hold_var.set(str(step.hold_ms))
        self.step_hold_jitter_var.set(str(step.hold_jitter_ms))
        self.step_ready_template = step.ready_template
        self.step_ready_region = step.ready_region
        self.step_ready_timeout_var.set(str(step.ready_timeout_ms))
        self.step_ready_confidence_var.set(f"{step.ready_confidence:.2f}")
        self._refresh_ready_status()

    def _read_step_form(self):
        try:
            delay = int(self.step_delay_var.get())
            jitter = int(self.step_jitter_var.get())
            hold = int(self.step_hold_var.get())
            hold_jitter = int(self.step_hold_jitter_var.get())
            timeout = int(self.step_ready_timeout_var.get())
            confidence = float(self.step_ready_confidence_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid step",
                "Delay/jitter/hold/hold jitter/timeout must be whole numbers, and "
                "confidence must be a decimal (e.g. 0.9).")
            return None
        return Step(
            key=self.step_key_var.get().strip(),
            name=self.step_name_var.get().strip(),
            delay_ms=delay,
            jitter_ms=jitter,
            hold_ms=hold,
            hold_jitter_ms=hold_jitter,
            ready_template=self.step_ready_template,
            ready_region=self.step_ready_region,
            ready_confidence=confidence,
            ready_timeout_ms=timeout,
        )

    def _add_step(self):
        step = self._read_step_form()
        if step is None:
            return
        self.editing_steps.append(step)
        self._refresh_steps_tree()
        self._reset_ready_form()

    def _copy_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step in the list first.")
            return
        i = int(selection[0])
        duplicate = copy.deepcopy(self.editing_steps[i])
        self.editing_steps.insert(i + 1, duplicate)
        self._refresh_steps_tree()
        self.tree.selection_set(str(i + 1))

    def _update_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step in the list first.")
            return
        step = self._read_step_form()
        if step is None:
            return
        self.editing_steps[int(selection[0])] = step
        self._refresh_steps_tree()

    def _remove_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            return
        del self.editing_steps[int(selection[0])]
        self._refresh_steps_tree()

    def _move_step_up(self):
        selection = self.tree.selection()
        if not selection:
            return
        i = int(selection[0])
        if i == 0:
            return
        self.editing_steps[i - 1], self.editing_steps[i] = self.editing_steps[i], self.editing_steps[i - 1]
        self._refresh_steps_tree()
        self.tree.selection_set(str(i - 1))

    def _move_step_down(self):
        selection = self.tree.selection()
        if not selection:
            return
        i = int(selection[0])
        if i >= len(self.editing_steps) - 1:
            return
        self.editing_steps[i + 1], self.editing_steps[i] = self.editing_steps[i], self.editing_steps[i + 1]
        self._refresh_steps_tree()
        self.tree.selection_set(str(i + 1))

    # ---- cooldown-check calibration -----------------------------------------

    def _clear_ready_check(self):
        self.step_ready_template = None
        self.step_ready_region = None
        self._refresh_ready_status()

    def _on_calibrate_clicked(self):
        messagebox.showinfo(
            "Calibrate cooldown check",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click-drag a small rectangle tightly around just that icon and "
            "release the mouse button.\n\nPress Escape at any time to cancel.")
        self.withdraw()
        self.after(150, self._open_capture_overlay)

    def _open_capture_overlay(self):
        RegionCaptureOverlay(self, on_done=self._on_region_captured)

    def _on_region_captured(self, region):
        if region is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # captured template isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._take_calibration_screenshot(region))

    def _take_calibration_screenshot(self, region):
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
        self._show_calibration_preview(filename, region)

    def _show_calibration_preview(self, filename, region):
        preview = tk.Toplevel(self)
        preview.title("Confirm calibration")
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)

        image = tk.PhotoImage(file=templates.template_path(filename))
        preview.image = image  # keep a reference -- Tk drops it otherwise
        ttk.Label(preview, image=image).pack(padx=8, pady=8)
        ttk.Label(preview, text=f"{region[2]} x {region[3]} px at ({region[0]}, {region[1]})").pack()

        btns = ttk.Frame(preview, padding=8)
        btns.pack()

        def use_this():
            # Deliberately does NOT delete any previously-calibrated file for this
            # step -- that file may still be referenced by a committed Step until
            # "Update Selected"/"Save Rotation" runs. Orphans are cleaned up by the
            # periodic sweep instead (see _sweep_templates).
            self.step_ready_template = filename
            self.step_ready_region = region
            self._refresh_ready_status()
            preview.destroy()

        def retry():
            templates.delete_template(filename)  # safe: nothing has ever referenced it
            preview.destroy()
            self._on_calibrate_clicked()

        def cancel():
            templates.delete_template(filename)  # safe: same as above
            preview.destroy()

        ttk.Button(btns, text="Use This", command=use_this).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left", padx=4)

    def _referenced_templates(self) -> set:
        keep = set()
        for rotation in self.rotations.values():
            for step in rotation.steps:
                if step.ready_template:
                    keep.add(step.ready_template)
        for step in self.editing_steps:
            if step.ready_template:
                keep.add(step.ready_template)
        if self.step_ready_template:
            keep.add(self.step_ready_template)
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
        self._refresh_listbox()

    # ---- save ---------------------------------------------------------------

    def _save_rotation(self):
        name = self.name_var.get().strip()
        rotation = Rotation(
            name=name,
            mode=self.mode_var.get(),
            hotkey=self.pending_hotkey,
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
        # choice) -- before any destructive rename step, so a conflict here (shouldn't
        # happen given the pre-check above, but kept as a defensive guard) can't leave
        # the old file deleted with nothing saved in its place.
        try:
            self.hotkey_manager.rebind(self.editing_original_hotkey, rotation.hotkey, rotation.name)
        except ValueError as e:
            messagebox.showerror("Hotkey conflict", str(e))
            return

        renamed = self.editing_original_name is not None and self.editing_original_name != name
        if renamed:
            storage.delete_rotation(self.editing_original_name)
            self.rotation_manager.unload(self.editing_original_name)
            del self.rotations[self.editing_original_name]

        storage.save_rotation(rotation)
        self.rotation_manager.load(rotation)
        self.rotations[rotation.name] = rotation

        self._load_rotation_into_form(rotation)
        self._refresh_listbox()
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
                else:
                    self._refresh_listbox()
        except queue.Empty:
            pass
        self.after(200, self._poll_status_queue)

    # ---- shutdown -------------------------------------------------------------

    def _on_close(self):
        self.rotation_manager.stop_all()
        keyboard.unhook_all()
        self.destroy()
