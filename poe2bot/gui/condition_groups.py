from poe2bot.gui import dialogs as messagebox
from poe2bot.gui.action_labels import GROUP_CONDITION_ACTION_LABELS
from poe2bot.models import Condition, ConditionGroup

# Human labels for a ConditionGroup's own condition.action, and back -- a
# narrower set than a Step's own CONDITION_ACTION_LABELS (poe2bot/gui/
# conditions.py): a group never uses "hold" (there's no single step's
# hold_ms/delay_ms to override) or "timer" (see validate_rotation).
_GROUP_CONDITION_ACTION_BY_LABEL = {label: action for action, label in GROUP_CONDITION_ACTION_LABELS.items()}


class ConditionGroupsMixin:
    """Rotation-level Condition Groups: creating one (Add Condition Group
    (Image)/(Pixel), always a top-level append), the "Rotation Conditions"
    section's Name/Action/Negate fields for whichever group is currently
    selected (blanked when a step or nothing is selected -- see
    StepEditorMixin._on_select_step), and recalibrating a group's match by
    double-clicking its row. A group may itself be nested inside another
    group (up to models.MAX_GROUP_NESTING_DEPTH) -- nesting is created only
    via drag-and-drop (see poe2bot/gui/drag_drop.py), never by these Add
    Condition Group buttons, which always append at the top level exactly
    as before. Mixed into App (see poe2bot/gui/app.py) -- reuses
    CalibrationMixin's _start_image_capture/_start_pixel_capture via the
    on_use callback, exactly like ConditionsMixin does for a step's own
    conditions."""

    def _selected_group_location(self):
        """The group_path of the condition group whose own header row is
        currently selected, or None (a step/condition row, nothing, or a
        multi-selection). Used by the Add Condition Group (Image)/(Pixel)
        buttons to decide whether they're adding a brand new top-level
        group or recalibrating the one that's already selected -- see
        _on_add_image_condition_group_clicked below."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is not None:
            return None
        return parsed[0]

    def _on_test_match_group_clicked(self):
        """Live "does it match right now" preview for whichever condition
        group's own row is currently selected -- see
        CalibrationMixin._show_test_match_result. A group's condition is
        never match_type "timer" (see validate_rotation), so unlike
        ConditionsMixin's equivalent this never needs to reject that case."""
        group_path = self._selected_group_location()
        if group_path is None:
            messagebox.showinfo(
                "No condition group selected", "Select a condition group's own row in the Skill Steps list first.")
            return
        self._show_test_match_result(self._group_at(group_path).condition)

    def _on_add_image_condition_group_clicked(self):
        group_path = self._selected_group_location()
        if group_path is not None:
            self._recalibrate_group(group_path, "image")
            return
        self._start_image_capture(on_use=lambda filename, region, confidence, search_mode, search_region: self._append_condition_group(
            Condition(match_type="image", template=filename, region=region, confidence=confidence,
                      search_mode=search_mode, search_region=search_region, **self._calib_size_kwargs())))

    def _on_add_pixel_condition_group_clicked(self):
        group_path = self._selected_group_location()
        if group_path is not None:
            self._recalibrate_group(group_path, "pixel")
            return
        self._start_pixel_capture(on_use=lambda point, color, confidence: self._append_condition_group(
            Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence,
                      **self._calib_size_kwargs())))

    def _append_condition_group(self, condition: Condition):
        """Appends a new ConditionGroup (default action="fire", exactly
        today's plain gating behavior) to the TOP LEVEL of self.editing_steps
        -- unlike Add Step/Add Sleep this ignores the current tree selection
        entirely, and always lands at the top level regardless of what's
        selected; nesting a group inside another is done only by dragging it
        onto that group's own row afterward (see poe2bot/gui/drag_drop.py)
        -- and selects its new row so the Rotation Conditions section
        immediately shows it."""
        group_path = (len(self.editing_steps),)
        self.editing_steps.append(ConditionGroup(condition=condition))
        self._refresh_steps_tree()
        self.tree.selection_set(self._location_iid(group_path, None))
        self._autosave()

    # ---- the Rotation Conditions section's Name/Action/Negate fields --------

    def _set_step_panels_visible(self, visible: bool):
        """Shows "Selected Step" (nested inside the Skill Steps section's
        own body, alongside its button row) + Skill Conditions when a step
        (or nothing) is selected; hides them when a condition group's own
        row is selected instead, since a group has no Key/Delay/Hold/
        Repeat/per-step Conditions of its own. Unlike Skill Conditions, the
        Rotation Conditions section (built in app.py) is always visible
        regardless of what's selected -- its Add Condition Group buttons
        don't depend on any particular selection -- so it's re-packed here
        too every time, last, to guarantee it always ends up positioned
        after Skill Conditions rather than drifting out of place after
        repeated toggles (Selected Step's own position is unaffected by
        that ordering -- it lives in a different parent, Skill Steps' body,
        alongside only its own button row)."""
        self.step_fields_group.pack_forget()
        self.conditions_section.pack_forget()
        self.rotation_conditions_section.pack_forget()
        if visible:
            self.step_fields_group.pack(fill="x", pady=(0, 6))
            self.conditions_section.pack(fill="x", pady=(0, 6))
        self.rotation_conditions_section.pack(fill="x", pady=(0, 6))

    def _populate_group_condition_form(self, group):
        """Fills the Rotation Conditions section's Name/Action/Negate/match-
        summary fields from `group`, or blanks them to defaults if None (a
        step row, not a group row, is selected, or nothing is) -- mirrors
        ConditionsMixin._populate_condition_form's None-handling. Wrapped in
        _autosave_suppressed() for the same reason that one is -- this is
        code populating the form, not the user editing it."""
        with self._autosave_suppressed():
            if group is None:
                self.group_name_var.set("")
                self.group_action_var.set(GROUP_CONDITION_ACTION_LABELS["fire"])
                self.group_negate_var.set(False)
                self.group_match_summary_var.set("(no condition group selected)")
                return
            condition = group.condition
            self.group_name_var.set(condition.name)
            self.group_action_var.set(
                GROUP_CONDITION_ACTION_LABELS.get(condition.action, GROUP_CONDITION_ACTION_LABELS["fire"]))
            self.group_negate_var.set(condition.negate)
            # Reuses the same [Execute]/[Skip] + match-description formatting the
            # tree row itself uses -- a live preview of exactly what's calibrated,
            # not a separate description to keep in sync.
            self.group_match_summary_var.set(self._condition_summary(condition))

    def _apply_pending_group_edits(self) -> bool:
        """Applies the panel's Name/Action/Negate fields onto whichever
        group is currently selected -- everything about its condition
        except its match itself (recalibrate by double-clicking the
        group's own row, see _on_group_row_double_click). The non-
        interactive core of what used to be the "Update Selected Condition
        Group" button, called on every field change by
        AutosaveMixin._autosave -- nothing/the wrong thing being selected is
        just "nothing pending to apply" (return True), not something to
        interrupt anyone about. Unlike ConditionsMixin's equivalent, there's
        no numeric field here that could fail to parse -- Name/Action/
        Negate can't be invalid -- so this never returns False."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return True
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is not None:
            return True
        group_path = parsed[0]
        condition = self._group_at(group_path).condition
        condition.name = self.group_name_var.get().strip()
        condition.negate = self.group_negate_var.get()
        condition.action = _GROUP_CONDITION_ACTION_BY_LABEL.get(self.group_action_var.get(), "fire")
        self._update_group_row(group_path)
        return True

    # ---- recalibrating a group's match (double-click its row, or the Add ----
    # ---- Condition Group buttons while that group is already selected) ------

    def _on_group_row_double_click(self, group_path):
        """Double-clicking a condition group's own row recalibrates its
        match in place, always using whichever match_type it already has --
        exactly like recalibrating a step's condition does (see
        ConditionsMixin._on_tree_double_click, which dispatches here for a
        group row). The Add Condition Group (Image)/(Pixel) buttons share
        the same underlying recalibrate (_recalibrate_group) when a group
        is already selected, but let the clicked button's type override
        this, which is how an existing group gets converted from one match
        type to the other."""
        group = self._group_at(group_path)
        self._recalibrate_group(group_path, group.condition.match_type)

    def _recalibrate_group(self, group_path, match_type: str):
        """Recalibrates the condition of the group AT group_path as
        `match_type` -- switching type first if that's not what it already
        had -- carrying over its Name/Action/Negate unchanged, since
        recalibrating (like a step's own condition) is only ever meant to
        fix *what's being matched*, not *what happens when it matches*. A
        group's condition is never match_type "timer", so there's no timer
        branch to handle here."""
        group = self._group_at(group_path)
        condition = group.condition

        def replace(new_condition: Condition):
            new_condition.name = condition.name
            new_condition.negate = condition.negate
            new_condition.action = condition.action
            group.condition = new_condition
            self._refresh_steps_tree()
            self.tree.selection_set(self._location_iid(group_path, None))
            self._populate_group_condition_form(group)
            self._autosave()

        if match_type == "pixel":
            self._start_pixel_capture(
                on_use=lambda point, color, confidence: replace(
                    Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence,
                              **self._calib_size_kwargs())),
                default_confidence=condition.confidence)
        else:
            self._start_image_capture(
                on_use=lambda filename, region, confidence, search_mode, search_region: replace(
                    Condition(match_type="image", template=filename, region=region, confidence=confidence,
                              search_mode=search_mode, search_region=search_region, **self._calib_size_kwargs())),
                default_confidence=condition.confidence)
