from poe2bot.gui import dialogs as messagebox
from poe2bot.gui.action_labels import (
    GROUP_CONDITION_ACTION_LABELS, SKILL_GROUP_ACTION_LABELS, STEP_CONDITION_ACTION_LABELS,
)

# Every kind of thing the unified detail editor below can be showing, and the
# Action label set/reverse-lookup each one uses -- see GateEditorMixin's own
# docstring for what each kind actually is.
_LABELS_BY_KIND = {
    "condition": STEP_CONDITION_ACTION_LABELS,
    "skill_group": SKILL_GROUP_ACTION_LABELS,
    "rotation_group": GROUP_CONDITION_ACTION_LABELS,
}
_ACTION_BY_LABEL_BY_KIND = {
    kind: {label: action for action, label in labels.items()} for kind, labels in _LABELS_BY_KIND.items()
}

_KIND_CAPTIONS = {
    None: "Nothing selected -- select a condition, skill condition group, or condition group's own "
          "row in Skill Steps, or use one of the Add buttons above.",
    "condition": "Selected: Skill Condition",
    "skill_group": "Selected: Skill Condition Group",
    "rotation_group": "Selected: Rotation Condition Group",
}


class GateEditorMixin:
    """The single "Conditions" section: three separate Add-button rows (Add
    Image/Pixel/Timer Condition + Copy/Paste Conditions for a step's own
    plain Conditions; Add Skill Condition Group for grouping several of
    them with an All/Any rule -- see poe2bot/models.py's SkillConditionGroup;
    Add Condition Group (Image)/(Pixel) for a rotation-level ConditionGroup
    that gates a whole block of steps -- see that class's own docstring),
    each a genuinely different creation flow kept in its own actual
    ADD-button row, but sharing ONE detail editor below them (Name/Action/
    Negate-or-Match-Logic/Timeout-or-Hold+Delay/match-summary) for whichever
    single Condition/SkillConditionGroup/ConditionGroup is currently
    selected -- replacing what used to be three separate, near-identical
    editors (poe2bot/gui/conditions.py, skill_condition_groups.py,
    condition_groups.py), each built into its own always-visible section
    even though at most one of them ever had anything relevant loaded into
    it at a time.

    Mixed into App (see poe2bot/gui/app.py). The three Add-button rows'
    own click handlers (ConditionsMixin/SkillConditionGroupsMixin/
    ConditionGroupsMixin) are unaffected by this merge -- they already
    resolve what to do entirely from the current tree selection, same as
    always. Only the populate/apply/visibility side (what used to be
    ConditionsMixin._populate_condition_form/_apply_pending_condition_edits/
    _refresh_condition_extra_visibility and its two siblings) is unified
    here, via `kind` -- None (nothing editable selected), "condition" (a
    Condition, standalone or nested one level inside a SkillConditionGroup),
    "skill_group" (a SkillConditionGroup), or "rotation_group" (a
    ConditionGroup)."""

    def _set_step_panels_visible(self, visible: bool):
        """Shows "Selected Step" (Key/Delay/Hold/Repeat/etc., nested inside
        the Skill Steps section's own body) only when a step (or nothing) is
        selected; hides it when a rotation-level condition group's own row
        is selected instead, since a group has no Key/Delay/Hold/Repeat of
        its own. The unified Conditions section (see _build_gate_editor_section,
        poe2bot/gui/app.py) stays visible regardless of what's selected --
        its three Add-button rows don't depend on any particular selection,
        only the detail editor beneath them does (blanked via
        _populate_gate_editor(None, None) when nothing editable is
        selected)."""
        if visible:
            self.step_fields_group.pack(fill="x", pady=(0, 6))
        else:
            self.step_fields_group.pack_forget()

    # ---- Test Match (dispatches to whichever kind is currently selected) ----

    def _on_gate_test_match_clicked(self):
        """Live "does it match right now" preview, dispatched to whichever
        of the three kinds is currently selected -- see
        CalibrationMixin._show_test_match_result. Meaningless for a Timer
        condition (there's no "since this step's last fire" to measure
        outside a running rotation) or for a Skill Condition Group's own
        header row (it has several children, not one single match) --
        both get a plain explanatory message instead, same idea."""
        location = self._selected_condition_location()
        if location is not None:
            condition = self._condition_at(*location)
            if condition.match_type == "timer":
                self._show_gate_info(
                    "Can't test a Timer condition",
                    "A Timer condition matches based on seconds since this step's own last fire -- "
                    "there's no rotation running right now to measure that against. Use Test Run "
                    "and watch the Activity window instead.")
                return
            self._show_test_match_result(condition)
            return
        group_path = self._selected_group_location()
        if group_path is not None:
            self._show_test_match_result(self._group_at(group_path).condition)
            return
        if self._selected_skill_group_location() is not None:
            self._show_gate_info(
                "Can't test a Skill Condition Group",
                "A Skill Condition Group combines several of its own child conditions -- select one "
                "of them to test individually, or use Test Run and watch the Activity window.")
            return
        self._show_gate_info(
            "No condition selected", "Select a condition or condition group in the Skill Steps list first.")

    @staticmethod
    def _show_gate_info(title: str, message: str):
        messagebox.showinfo(title, message)

    # ---- populate (selection -> form) ------------------------------------------

    def _populate_gate_editor(self, kind, obj, *, nested_in_skill_group: bool = False):
        """Fills the unified detail editor from whichever single Condition
        ("condition", `obj` a Condition)/SkillConditionGroup ("skill_group")/
        ConditionGroup's own condition ("rotation_group") is currently
        selected, or blanks it to defaults if `kind` is None (a step's own
        row, nothing, or a multi-selection). `nested_in_skill_group` (only
        meaningful for kind == "condition") shows a one-line hint that this
        condition's own Action/Timeout/Hold/Delay are ignored -- the owning
        Skill Condition Group's own apply instead. Wrapped in
        _autosave_suppressed() -- this is code populating the form, not the
        user editing it, so it must never misfire an autosave onto whatever
        was previously loaded (see AutosaveMixin, poe2bot/gui/autosave.py)."""
        with self._autosave_suppressed():
            self._gate_editor_kind = kind
            self._gate_editor_nested = nested_in_skill_group
            self.gate_kind_var.set(_KIND_CAPTIONS[kind])
            labels = _LABELS_BY_KIND.get(kind, STEP_CONDITION_ACTION_LABELS)
            self.gate_action_combo.config(values=list(labels.values()))

            if kind == "rotation_group":
                condition = obj.condition
                self.gate_name_var.set(condition.name)
                self.gate_action_var.set(labels.get(condition.action, labels["fire"]))
                self.gate_negate_var.set(condition.negate)
                self.gate_match_summary_var.set(self._condition_summary(condition))
            elif kind == "skill_group":
                self.gate_name_var.set(obj.name)
                self.gate_action_var.set(labels.get(obj.action, labels["fire"]))
                self.gate_match_logic_var.set("All" if obj.match_logic == "all" else "Any")
                self.gate_timeout_var.set(str(obj.timeout_ms))
                self.gate_hold_var.set("" if obj.hold_ms is None else str(obj.hold_ms))
                self.gate_delay_var.set("" if obj.delay_ms is None else str(obj.delay_ms))
            elif kind == "condition":
                self.gate_name_var.set(obj.name)
                self.gate_action_var.set(labels.get(obj.action, labels["fire"]))
                self.gate_negate_var.set(obj.negate)
                self.gate_timeout_var.set(str(obj.timeout_ms))
                self.gate_hold_var.set("" if obj.hold_ms is None else str(obj.hold_ms))
                self.gate_delay_var.set("" if obj.delay_ms is None else str(obj.delay_ms))
            else:
                self.gate_name_var.set("")
                self.gate_action_var.set(labels["fire"])
                self.gate_negate_var.set(False)
                self.gate_match_logic_var.set("All")
                self.gate_timeout_var.set("0")
                self.gate_hold_var.set("")
                self.gate_delay_var.set("")
                self.gate_match_summary_var.set("(no condition group selected)")
            self._clear_gate_form_error()
            self._refresh_gate_extra_visibility()

    def _refresh_gate_extra_visibility(self):
        """Shows only the fields relevant to the currently-displayed `kind`
        and its selected Action -- Negate for "condition"/"rotation_group"
        (never "skill_group": a group's own Negate would be ambiguous over
        several children, each of which already has its own); Match Logic
        for "skill_group" only; Wait Timeout ("fire")/Hold+Delay override
        ("hold") for "condition"/"skill_group" only (a rotation-level
        group's condition never uses either); the match summary + a
        recalibrate hint for "rotation_group" only; the "nested in a Skill
        Condition Group" hint only for a nested "condition"."""
        kind = self._gate_editor_kind
        action = _ACTION_BY_LABEL_BY_KIND.get(kind, {}).get(self.gate_action_var.get(), "fire")

        self.gate_negate_check.pack_forget()
        self.gate_match_logic_frame.pack_forget()
        self.gate_timeout_frame.pack_forget()
        self.gate_hold_frame.pack_forget()
        self.gate_summary_row.pack_forget()
        self.gate_nested_hint_label.pack_forget()

        if kind in ("condition", "rotation_group"):
            self.gate_negate_check.pack(side="left")
        if kind == "skill_group":
            self.gate_match_logic_frame.pack(side="left")
        if kind in ("condition", "skill_group"):
            if action == "fire":
                self.gate_timeout_frame.pack(side="left")
            elif action == "hold":
                self.gate_hold_frame.pack(side="left")
        if kind == "rotation_group":
            self.gate_summary_row.pack(fill="x", pady=(6, 0))
        if kind == "condition" and self._gate_editor_nested:
            self.gate_nested_hint_label.pack(anchor="w", pady=(4, 0))

    def _on_gate_action_changed(self, _event=None):
        self._refresh_gate_extra_visibility()

    # ---- apply (form -> selection) ----------------------------------------------

    def _apply_pending_gate_edits(self) -> bool:
        """The single, non-interactive entry point that used to be three
        separate apply methods (ConditionsMixin._apply_pending_condition_edits,
        SkillConditionGroupsMixin._apply_pending_skill_group_edits,
        ConditionGroupsMixin._apply_pending_group_edits) -- applies the
        unified editor's fields onto whichever single Condition/
        SkillConditionGroup/ConditionGroup's own condition is CURRENTLY
        selected, re-derived fresh from the tree (via the same
        _selected_*_location helpers the three Add-button flows already
        use) rather than trusted from _gate_editor_kind, which only tracks
        what's currently DISPLAYED. Called on every field change by
        AutosaveMixin._autosave -- nothing (or the wrong thing) selected is
        just "nothing pending to apply" (return True), not something to
        interrupt anyone about; a parse/range failure shows inline via
        gate_form_error_label instead of a popup, since this can now run
        mid-keystroke."""
        location = self._selected_condition_location()
        if location is not None:
            return self._apply_pending_condition_fields(location)
        group_path = self._selected_group_location()
        if group_path is not None:
            return self._apply_pending_rotation_group_fields(group_path)
        skill_group_location = self._selected_skill_group_location()
        if skill_group_location is not None:
            return self._apply_pending_skill_group_fields(skill_group_location)
        return True

    def _apply_gate_numeric_fields(self):
        """Parses/validates the shared Wait Timeout / Hold override / Delay
        override fields -- (timeout_ms, hold_ms, delay_ms) on success, or
        None (having already shown the inline error) on a parse/range
        failure. Shared by the two kinds that use these three fields at all
        ("condition"/"skill_group") -- a rotation-level group's condition
        never does, see _apply_pending_rotation_group_fields."""
        try:
            timeout_ms = int(self.gate_timeout_var.get() or 0)
            hold_text = self.gate_hold_var.get().strip()
            delay_text = self.gate_delay_var.get().strip()
            hold_ms = int(hold_text) if hold_text else None
            delay_ms = int(delay_text) if delay_text else None
        except ValueError:
            self._show_gate_form_error(
                "Wait timeout, Hold override, and Delay override must be whole numbers "
                "(Hold/Delay may be left blank).")
            return None
        if timeout_ms < 0 or (hold_ms is not None and hold_ms < 0) or (delay_ms is not None and delay_ms < 0):
            self._show_gate_form_error("Wait timeout, Hold override, and Delay override cannot be negative.")
            return None
        self._clear_gate_form_error()
        return timeout_ms, hold_ms, delay_ms

    def _apply_pending_condition_fields(self, location) -> bool:
        parsed = self._apply_gate_numeric_fields()
        if parsed is None:
            return False
        timeout_ms, hold_ms, delay_ms = parsed
        condition = self._condition_at(*location)
        condition.name = self.gate_name_var.get().strip()
        condition.negate = self.gate_negate_var.get()
        condition.action = _ACTION_BY_LABEL_BY_KIND["condition"].get(self.gate_action_var.get(), "fire")
        condition.timeout_ms = timeout_ms
        condition.hold_ms = hold_ms
        condition.delay_ms = delay_ms
        self._update_condition_row(*location)
        return True

    def _apply_pending_skill_group_fields(self, location) -> bool:
        parsed = self._apply_gate_numeric_fields()
        if parsed is None:
            return False
        timeout_ms, hold_ms, delay_ms = parsed
        group_path, step_idx, cond_idx = location
        group = self._skill_group_at(group_path, step_idx, cond_idx)
        group.name = self.gate_name_var.get().strip()
        group.action = _ACTION_BY_LABEL_BY_KIND["skill_group"].get(self.gate_action_var.get(), "fire")
        group.match_logic = "all" if self.gate_match_logic_var.get() == "All" else "any"
        group.timeout_ms = timeout_ms
        group.hold_ms = hold_ms
        group.delay_ms = delay_ms
        self._update_skill_group_row(group_path, step_idx, cond_idx)
        return True

    def _apply_pending_rotation_group_fields(self, group_path) -> bool:
        """No numeric field to parse here -- Name/Action/Negate can't be
        invalid -- so unlike the other two, this never returns False."""
        condition = self._group_at(group_path).condition
        condition.name = self.gate_name_var.get().strip()
        condition.negate = self.gate_negate_var.get()
        condition.action = _ACTION_BY_LABEL_BY_KIND["rotation_group"].get(self.gate_action_var.get(), "fire")
        self._update_group_row(group_path)
        return True

    def _show_gate_form_error(self, message: str):
        self.gate_form_error_var.set(message)
        self.gate_form_error_label.pack(anchor="w", pady=(4, 0))

    def _clear_gate_form_error(self):
        self.gate_form_error_var.set("")
        self.gate_form_error_label.pack_forget()
