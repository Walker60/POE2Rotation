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
