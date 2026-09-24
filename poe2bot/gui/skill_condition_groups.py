from poe2bot.models import SkillConditionGroup


class SkillConditionGroupsMixin:
    """Skill Condition Groups: an explicitly user-created group of a step's
    own plain Conditions, living inline in that SAME step.conditions list
    (see poe2bot/models.py's SkillConditionGroup) -- distinct from the
    rotation-level ConditionGroup (poe2bot/gui/condition_groups.py), which
    gates a whole block of STEPS. Covers "Add Skill Condition Group" (always
    appends an empty group to whichever step is in scope -- there's no
    recalibrate-in-place mode, since a group has no match of its own).
    Mixed into App (see poe2bot/gui/app.py). A Condition living inside one
    of these groups is still added/recalibrated through ConditionsMixin
    (poe2bot/gui/conditions.py), just addressed one level deeper -- see
    StepEditorMixin's own docstring for the location tuple. The selected
    group's own Name/Action/Match Logic/Timeout-or-Hold+Delay fields are
    edited through the unified detail editor instead -- see
    poe2bot/gui/gate_editor.py's GateEditorMixin, which this mixin's
    _selected_skill_group_location is also shared with."""

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
