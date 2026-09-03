import copy

from poe2bot import storage, templates
from poe2bot.gui import dialogs as messagebox
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition

log = get_logger()

# Human labels for Condition.action, and back -- shared by the Action combobox
# (app.py), _populate_condition_form, and _on_update_condition_clicked below.
CONDITION_ACTION_LABELS = {
    "fire": "Fire the skill",
    "block": "Not fire the skill",
    "hold": "Change key hold amount",
}
_CONDITION_ACTION_BY_LABEL = {label: action for action, label in CONDITION_ACTION_LABELS.items()}


class ConditionsMixin:
    """Per-step Conditions: add/update/recalibrate, plus tracking which
    calibrated template files are still referenced (so the periodic sweep
    doesn't delete ones still in use). A Condition is the single mechanism
    behind what used to be three separate concepts (Cooldown Check, Buff
    Check, plain Condition) -- see Condition.action in models.py. Mixed into
    App (see poe2bot/gui/app.py) -- Add/recalibrate Condition reuse
    CalibrationMixin's _start_image_capture/_start_pixel_capture via the
    on_use callback."""

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
        seconds = messagebox.askfloat(
            "Add Timer Condition", "Minimum seconds since this step's last use:",
            initialvalue=5.0, minvalue=0.1, parent=self)
        if seconds is None:
            return
        self._add_condition(step_idx, Condition(match_type="timer", timer_seconds=seconds))

    def _add_condition(self, step_idx: int, condition: Condition):
        """Appends `condition` (default action="fire", exactly today's plain
        gating behavior) and selects its new row, so the Action/Timeout/
        Hold/Delay editor is immediately showing it -- picking Block or
        Change Key Hold Amount, or a wait timeout, is a follow-up step now
        that those are no longer separate calibration flows of their own."""
        self.editing_steps[step_idx].conditions.append(condition)
        self._refresh_steps_tree()
        cond_idx = len(self.editing_steps[step_idx].conditions) - 1
        self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx}")

    # ---- the per-condition editor (Name/Action/Negate/Timeout/Hold/Delay) ----

    def _populate_condition_form(self, condition):
        """Fills the Conditions section's editor from `condition`, or blanks
        it to defaults if None (a step row, not a condition row, is
        selected, or nothing is). Shared by StepEditorMixin._on_select_step
        and this mixin's own add/update handlers, so the form always
        reflects exactly what's selected."""
        if condition is None:
            self.condition_name_var.set("")
            self.condition_action_var.set(CONDITION_ACTION_LABELS["fire"])
            self.condition_negate_var.set(False)
            self.condition_timeout_var.set("0")
            self.condition_hold_var.set("")
            self.condition_delay_var.set("")
        else:
            self.condition_name_var.set(condition.name)
            self.condition_action_var.set(CONDITION_ACTION_LABELS.get(condition.action, CONDITION_ACTION_LABELS["fire"]))
            self.condition_negate_var.set(condition.negate)
            self.condition_timeout_var.set(str(condition.timeout_ms))
            self.condition_hold_var.set("" if condition.hold_ms is None else str(condition.hold_ms))
            self.condition_delay_var.set("" if condition.delay_ms is None else str(condition.delay_ms))
        self._refresh_condition_extra_visibility()

    def _refresh_condition_extra_visibility(self):
        """Shows only the field group relevant to the currently-selected
        Action -- Wait Timeout for "fire", Hold/Delay override for "hold",
        neither for "block" -- so the editor doesn't reintroduce the same
        always-everything-visible clutter this whole redesign was meant to
        remove."""
        action = _CONDITION_ACTION_BY_LABEL.get(self.condition_action_var.get(), "fire")
        self.condition_timeout_frame.pack_forget()
        self.condition_hold_frame.pack_forget()
        if action == "fire":
            self.condition_timeout_frame.pack(side="left")
        elif action == "hold":
            self.condition_hold_frame.pack(side="left")

    def _on_condition_action_changed(self, _event=None):
        self._refresh_condition_extra_visibility()

    def _on_update_condition_clicked(self):
        """Applies the editor's Name/Action/Negate/Timeout/Hold/Delay fields
        onto whichever single condition is selected -- everything about a
        condition except its match itself (recalibrate via double-click for
        that, see _on_tree_double_click)."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No condition selected", "Select a condition in the list first.")
            return
        if len(selection) > 1:
            messagebox.showinfo("Select one condition", "Select exactly one condition to update.")
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            messagebox.showinfo("Select a condition", "Select a condition (not a step) to update.")
            return
        step_idx, cond_idx = parsed
        action = _CONDITION_ACTION_BY_LABEL.get(self.condition_action_var.get(), "fire")
        try:
            timeout_ms = int(self.condition_timeout_var.get() or 0)
            hold_text = self.condition_hold_var.get().strip()
            delay_text = self.condition_delay_var.get().strip()
            hold_ms = int(hold_text) if hold_text else None
            delay_ms = int(delay_text) if delay_text else None
        except ValueError:
            messagebox.showerror(
                "Invalid condition", "Wait timeout, Hold override, and Delay override must be whole "
                "numbers (Hold/Delay may be left blank).")
            return
        if timeout_ms < 0 or (hold_ms is not None and hold_ms < 0) or (delay_ms is not None and delay_ms < 0):
            messagebox.showerror("Invalid condition", "Wait timeout, Hold override, and Delay override cannot be negative.")
            return
        condition = self.editing_steps[step_idx].conditions[cond_idx]
        condition.name = self.condition_name_var.get().strip()
        condition.negate = self.condition_negate_var.get()
        condition.action = action
        condition.timeout_ms = timeout_ms
        condition.hold_ms = hold_ms
        condition.delay_ms = delay_ms
        self._refresh_steps_tree()
        self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx}")

    def _on_tree_double_click(self, _event):
        """Double-clicking a condition row recalibrates its match in place
        (same capture flow as adding one, but replacing rather than
        appending) -- everything else about it (name, action, negate,
        timeout, hold/delay overrides) carries over unchanged, since
        recalibrating is only meant to fix *what's being matched*, not
        *what happens when it matches*. Double-clicking a step row is a
        no-op -- steps are recalibrated via a condition's own row, there's
        no equivalent step-level check anymore."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            return
        step_idx, cond_idx = parsed
        condition = self.editing_steps[step_idx].conditions[cond_idx]

        def replace(new_condition: Condition):
            new_condition.name = condition.name
            new_condition.negate = condition.negate
            new_condition.action = condition.action
            new_condition.timeout_ms = condition.timeout_ms
            new_condition.hold_ms = condition.hold_ms
            new_condition.delay_ms = condition.delay_ms
            self.editing_steps[step_idx].conditions[cond_idx] = new_condition
            self._refresh_steps_tree()
            self._populate_condition_form(new_condition)

        if condition.match_type == "timer":
            seconds = messagebox.askfloat(
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

    # ---- copy/paste conditions between steps ---------------------------------

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

    # ---- template lifecycle ---------------------------------------------------

    def _referenced_templates(self) -> set:
        keep = set()
        for rotation in self.rotations.values():
            for step in rotation.steps:
                for condition in step.conditions:
                    if condition.template:
                        keep.add(condition.template)
        for step in self.editing_steps:
            for condition in step.conditions:
                if condition.template:
                    keep.add(condition.template)
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
