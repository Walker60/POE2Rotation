import copy
from tkinter import messagebox, simpledialog

from poe2bot import storage, templates
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition

log = get_logger()


class ConditionsMixin:
    """Per-step Conditions: add/rename/recalibrate, plus tracking which
    calibrated template files are still referenced (so the periodic sweep
    doesn't delete ones still in use). Mixed into App (see poe2bot/gui/app.py)
    -- Add/recalibrate Condition reuse CalibrationMixin's
    _start_image_capture/_start_pixel_capture via the on_use callback."""

    def _on_add_image_condition_clicked(self):
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        self._start_image_capture(on_use=lambda filename, region, confidence, search_mode, search_region: self._add_condition(
            step_idx, Condition(match_type="image", template=filename, region=region, confidence=confidence,
                                 search_mode=search_mode, search_region=search_region)))

    def _on_add_pixel_condition_clicked(self):
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        self._start_pixel_capture(on_use=lambda point, color, confidence: self._add_condition(
            step_idx, Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)))

    def _on_add_timer_condition_clicked(self):
        # No screen capture needed -- unlike image/pixel, the value is just
        # typed in directly, so this skips CalibrationMixin's overlay flow
        # entirely.
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        seconds = simpledialog.askfloat(
            "Add Timer Condition", "Minimum seconds since this step's last use:",
            initialvalue=5.0, minvalue=0.1, parent=self)
        if seconds is None:
            return
        self._add_condition(step_idx, Condition(match_type="timer", timer_seconds=seconds))

    def _add_condition(self, step_idx: int, condition: Condition):
        self.editing_steps[step_idx].conditions.append(condition)
        self._refresh_steps_tree()

    def _on_copy_conditions_clicked(self):
        """Copies every condition belonging to whichever step is in scope
        (the step row itself or one of its condition rows -- same resolution
        StepEditorMixin's Add Condition buttons already use) into an
        in-memory clipboard that lives on the App, so it survives switching
        rotations (enables cross-rotation paste), mirroring
        StepEditorMixin._on_copy_clicked's whole-step clipboard."""
        step_idx = self._selected_owning_step_index()
        if step_idx is None:
            return
        conditions = self.editing_steps[step_idx].conditions
        if not conditions:
            messagebox.showinfo("No conditions to copy", "This step has no conditions to copy.")
            return
        self._condition_clipboard = copy.deepcopy(conditions)

    def _on_paste_conditions_clicked(self):
        """Appends a fresh copy of the clipboard's conditions onto every
        selected step (deduped from the selection the same way
        StepEditorMixin._on_copy_clicked collects multiple source steps) --
        each target gets its own independent Condition objects, so
        recalibrating one afterward never affects another. Purely additive:
        a target step's own existing conditions are left as they are."""
        if not self._condition_clipboard:
            messagebox.showinfo("Clipboard is empty", "Copy conditions from a step first.")
            return
        selection = self.tree.selection()
        step_indices = sorted({p[0] for p in (self._parse_tree_iid(iid) for iid in selection)
                                if p is not None})
        if not step_indices:
            messagebox.showinfo("No step selected", "Select at least one step to paste conditions onto.")
            return
        for step_idx in step_indices:
            self.editing_steps[step_idx].conditions.extend(copy.deepcopy(self._condition_clipboard))
        self._refresh_steps_tree()

    def _on_rename_condition_clicked(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No condition selected", "Select a condition in the list first.")
            return
        if len(selection) > 1:
            messagebox.showinfo("Select one condition", "Select exactly one condition to rename.")
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            messagebox.showinfo("Select a condition", "Select a condition (not a step) to rename.")
            return
        step_idx, cond_idx = parsed
        self.editing_steps[step_idx].conditions[cond_idx].name = self.condition_name_var.get().strip()
        self._refresh_steps_tree()
        self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx}")

    def _on_toggle_condition_negate(self):
        """The checkbox's variable has already flipped by the time this runs
        (standard Checkbutton behavior -- command fires after the var
        updates), so on any bail-out path below the var is reverted back to
        False rather than left reflecting a toggle that was never actually
        applied to anything."""
        selection = self.tree.selection()
        if not selection:
            self.condition_negate_var.set(False)
            messagebox.showinfo("No condition selected", "Select a condition in the list first.")
            return
        if len(selection) > 1:
            self.condition_negate_var.set(False)
            messagebox.showinfo("Select one condition", "Select exactly one condition to toggle Negate.")
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            self.condition_negate_var.set(False)
            messagebox.showinfo("Select a condition", "Select a condition (not a step) to toggle Negate.")
            return
        step_idx, cond_idx = parsed
        self.editing_steps[step_idx].conditions[cond_idx].negate = self.condition_negate_var.get()
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

        if condition.match_type == "timer":
            seconds = simpledialog.askfloat(
                "Edit Timer Condition", "Minimum seconds since this step's last use:",
                initialvalue=condition.timer_seconds, minvalue=0.1, parent=self)
            if seconds is None:
                return
            replace(Condition(match_type="timer", timer_seconds=seconds))
        elif condition.match_type == "pixel":
            self._start_pixel_capture(
                on_use=lambda point, color, confidence: replace(
                    Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)),
                default_confidence=condition.confidence)
        else:
            self._start_image_capture(
                on_use=lambda filename, region, confidence, search_mode, search_region: replace(
                    Condition(match_type="image", template=filename, region=region, confidence=confidence,
                              search_mode=search_mode, search_region=search_region)),
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
        # A rotation file that currently fails to load contributes nothing to
        # _referenced_templates() (self.rotations only holds successfully-
        # loaded ones), so its calibration images would otherwise look
        # orphaned and get deleted -- abstain from the whole sweep rather
        # than risk destroying something a merely-temporarily-broken (not
        # actually abandoned) rotation still needs.
        if storage.has_unparseable_rotations():
            log.warning("Skipping template cleanup: at least one rotation file failed to load "
                        "(its templates might still be in use) -- fix or remove it, and cleanup "
                        "will resume normally.")
            return
        templates.sweep_unreferenced(self._referenced_templates())
