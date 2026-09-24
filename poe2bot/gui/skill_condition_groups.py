from poe2bot.gui.action_labels import SKILL_GROUP_ACTION_LABELS
from poe2bot.models import SkillConditionGroup

# Human labels for a SkillConditionGroup's own action, and back -- shared by
# the Action combobox (app.py), _populate_skill_group_form, and
# _apply_pending_skill_group_edits below.
_SKILL_GROUP_ACTION_BY_LABEL = {label: action for action, label in SKILL_GROUP_ACTION_LABELS.items()}


class SkillConditionGroupsMixin:
    """Skill Condition Groups: an explicitly user-created group of a step's
    own plain Conditions, living inline in that SAME step.conditions list
    (see poe2bot/models.py's SkillConditionGroup) -- distinct from the
    rotation-level ConditionGroup (poe2bot/gui/condition_groups.py), which
    gates a whole block of STEPS. Covers "Add Skill Condition Group" (always
    appends an empty group to whichever step is in scope -- there's no
    recalibrate-in-place mode, since a group has no match of its own) and
    the "Skill Condition Groups" section's Name/Action/Match Logic/Timeout-
    or-Hold+Delay fields for whichever group is selected. Mixed into App
    (see poe2bot/gui/app.py). A Condition living inside one of these groups
    is still added/edited/recalibrated through ConditionsMixin (poe2bot/gui/
    conditions.py), just addressed one level deeper -- see StepEditorMixin's
    own docstring for the location tuple."""

    def _selected_skill_group_location(self):
        """(group_path, step_idx, cond_idx) if a Skill Condition Group's own
        header row is currently selected, else None (a plain condition row,
        a step's own row, a rotation-level condition group's row, nothing,
        or a multi-selection). Used by the Add Skill Condition Group button
        (recalibrate is N/A -- a group has no match of its own) and by
        ConditionsMixin._resolve_condition_add_target to decide whether a
        new condition should be appended to this group's own children."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None or parsed[2] is None or parsed[3] is not None:
            return None
        group_path, step_idx, cond_idx, _subcond_idx = parsed
        entry = self._steps_list_for(group_path)[step_idx].conditions[cond_idx]
        return (group_path, step_idx, cond_idx) if self._is_skill_group_entry(entry) else None

    def _on_add_skill_condition_group_clicked(self):
        """Appends a new, empty SkillConditionGroup (default action="fire",
        match_logic="all" -- no effect until the user adds children) to
        whichever step is in scope -- same resolution as ConditionsMixin's
        Add Condition buttons -- and selects its new row so the Skill
        Condition Groups editor immediately shows it."""
        location = self._selected_owning_step_location()
        if location is None:
            return
        group_path, step_idx = location
        conditions = self._steps_list_for(group_path)[step_idx].conditions
        conditions.append(SkillConditionGroup())
        self._refresh_steps_tree()
        self.tree.selection_set(self._location_iid(group_path, step_idx, len(conditions) - 1))
        self._autosave()

    # ---- the Skill Condition Groups section's Name/Action/Match Logic/Timeout-or-Hold+Delay fields ----

    def _populate_skill_group_form(self, group):
        """Fills the Skill Condition Groups section's editor from `group`,
        or blanks it to defaults if None (a condition row, a step row, or
        nothing is selected). Mirrors ConditionsMixin._populate_condition_form's
        None-handling. Wrapped in _autosave_suppressed() for the same reason
        that one is -- this is code populating the form, not the user
        editing it."""
        with self._autosave_suppressed():
            if group is None:
                self.skill_group_name_var.set("")
                self.skill_group_action_var.set(SKILL_GROUP_ACTION_LABELS["fire"])
                self.skill_group_match_logic_var.set("All")
                self.skill_group_timeout_var.set("0")
                self.skill_group_hold_var.set("")
                self.skill_group_delay_var.set("")
            else:
                self.skill_group_name_var.set(group.name)
                self.skill_group_action_var.set(
                    SKILL_GROUP_ACTION_LABELS.get(group.action, SKILL_GROUP_ACTION_LABELS["fire"]))
                self.skill_group_match_logic_var.set("All" if group.match_logic == "all" else "Any")
                self.skill_group_timeout_var.set(str(group.timeout_ms))
                self.skill_group_hold_var.set("" if group.hold_ms is None else str(group.hold_ms))
                self.skill_group_delay_var.set("" if group.delay_ms is None else str(group.delay_ms))
            self._refresh_skill_group_extra_visibility()

    def _refresh_skill_group_extra_visibility(self):
        """Shows only the field group relevant to the currently-selected
        Action -- Wait Timeout for "fire", Hold/Delay override for "hold",
        neither for "block" -- mirrors ConditionsMixin's own
        _refresh_condition_extra_visibility."""
        action = _SKILL_GROUP_ACTION_BY_LABEL.get(self.skill_group_action_var.get(), "fire")
        self.skill_group_timeout_frame.pack_forget()
        self.skill_group_hold_frame.pack_forget()
        if action == "fire":
            self.skill_group_timeout_frame.pack(side="left")
        elif action == "hold":
            self.skill_group_hold_frame.pack(side="left")

    def _on_skill_group_action_changed(self, _event=None):
        self._refresh_skill_group_extra_visibility()

    def _apply_pending_skill_group_edits(self) -> bool:
        """Applies the panel's Name/Action/Match Logic/Timeout/Hold/Delay
        fields onto whichever Skill Condition Group is currently selected --
        called on every field change by AutosaveMixin._autosave, mirroring
        ConditionsMixin._apply_pending_condition_edits. Nothing/the wrong
        thing being selected is just "nothing pending to apply" (return
        True), not something to interrupt anyone about; a parse/range
        failure shows inline via skill_group_form_error_label instead of a
        popup, since this can run mid-keystroke."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return True
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[2] is None or parsed[3] is not None:
            return True
        group_path, step_idx, cond_idx, _subcond_idx = parsed
        entry = self._steps_list_for(group_path)[step_idx].conditions[cond_idx]
        if not self._is_skill_group_entry(entry):
            return True
        action = _SKILL_GROUP_ACTION_BY_LABEL.get(self.skill_group_action_var.get(), "fire")
        try:
            timeout_ms = int(self.skill_group_timeout_var.get() or 0)
            hold_text = self.skill_group_hold_var.get().strip()
            delay_text = self.skill_group_delay_var.get().strip()
            hold_ms = int(hold_text) if hold_text else None
            delay_ms = int(delay_text) if delay_text else None
        except ValueError:
            self.skill_group_form_error_var.set(
                "Wait timeout, Hold override, and Delay override must be whole numbers "
                "(Hold/Delay may be left blank).")
            self.skill_group_form_error_label.pack(anchor="w", pady=(4, 0))
            return False
        if timeout_ms < 0 or (hold_ms is not None and hold_ms < 0) or (delay_ms is not None and delay_ms < 0):
            self.skill_group_form_error_var.set("Wait timeout, Hold override, and Delay override cannot be negative.")
            self.skill_group_form_error_label.pack(anchor="w", pady=(4, 0))
            return False
        self.skill_group_form_error_var.set("")
        self.skill_group_form_error_label.pack_forget()
        entry.name = self.skill_group_name_var.get().strip()
        entry.action = action
        entry.match_logic = "all" if self.skill_group_match_logic_var.get() == "All" else "any"
        entry.timeout_ms = timeout_ms
        entry.hold_ms = hold_ms
        entry.delay_ms = delay_ms
        self._update_skill_group_row(group_path, step_idx, cond_idx)
        return True
