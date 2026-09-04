from poe2bot.gui import dialogs as messagebox
from poe2bot.models import Condition, ConditionGroup

# Human labels for a ConditionGroup's own condition.action, and back -- a
# narrower set than a Step's own CONDITION_ACTION_LABELS (poe2bot/gui/
# conditions.py): a group never uses "hold" (there's no single step's
# hold_ms/delay_ms to override) or "timer" (see validate_rotation).
GROUP_CONDITION_ACTION_LABELS = {"fire": "Execute Group", "block": "Skip Group"}
_GROUP_CONDITION_ACTION_BY_LABEL = {label: action for action, label in GROUP_CONDITION_ACTION_LABELS.items()}


class ConditionGroupsMixin:
    """Rotation-level Condition Groups: creating one (Add Condition Group
    (Image)/(Pixel), always a top-level append -- groups never nest), the
    "Rotation Conditions" section's Name/Action/Negate fields for whichever
    group is currently selected (blanked when a step or nothing is selected
    -- see StepEditorMixin._on_select_step), and recalibrating a group's
    match by double-clicking its row. Mixed into App (see
    poe2bot/gui/app.py) -- reuses CalibrationMixin's
    _start_image_capture/_start_pixel_capture via the on_use callback,
    exactly like ConditionsMixin does for a step's own conditions."""

    def _on_add_image_condition_group_clicked(self):
        self._start_image_capture(on_use=lambda filename, region, confidence, search_mode, search_region: self._append_condition_group(
            Condition(match_type="image", template=filename, region=region, confidence=confidence,
                      search_mode=search_mode, search_region=search_region)))

    def _on_add_pixel_condition_group_clicked(self):
        self._start_pixel_capture(on_use=lambda point, color, confidence: self._append_condition_group(
            Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)))

    def _append_condition_group(self, condition: Condition):
        """Appends a new ConditionGroup (default action="fire", exactly
        today's plain gating behavior) to the TOP LEVEL of self.editing_steps
        -- groups never nest, so unlike Add Step/Add Sleep this ignores the
        current tree selection entirely -- and selects its new row so the
        Rotation Conditions section immediately shows it."""
        group_idx = len(self.editing_steps)
        self.editing_steps.append(ConditionGroup(condition=condition))
        self._refresh_steps_tree()
        self.tree.selection_set(f"group-{group_idx}")

    # ---- the Rotation Conditions section's Name/Action/Negate fields --------

    def _set_step_panels_visible(self, visible: bool):
        """Shows "Selected Step" + Skill Conditions when a step (or
        nothing) is selected; hides them when a condition group's own row
        is selected instead, since a group has no Key/Delay/Hold/Repeat/
        per-step Conditions of its own. Unlike those two, the Rotation
        Conditions section (built in app.py) is always visible regardless
        of what's selected -- its Add Condition Group buttons don't depend
        on any particular selection -- so it's re-packed here too every
        time, last, to guarantee it always ends up positioned after
        whichever of the other two are currently showing rather than
        drifting out of place after repeated toggles."""
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
        ConditionsMixin._populate_condition_form's None-handling."""
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

    def _on_update_condition_group_clicked(self):
        """Applies the panel's Name/Action/Negate fields onto whichever
        group is currently selected -- everything about its condition
        except its match itself (recalibrate by double-clicking the
        group's own row, see _on_group_row_double_click)."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No condition group selected", "Select a condition group in the list first.")
            return
        if len(selection) > 1:
            messagebox.showinfo("Select one condition group", "Select exactly one condition group to update.")
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is not None:
            messagebox.showinfo("Select a condition group", "Select a condition group's own row (not a step) to update.")
            return
        group_idx = parsed[0]
        condition = self.editing_steps[group_idx].condition
        condition.name = self.group_name_var.get().strip()
        condition.negate = self.group_negate_var.get()
        condition.action = _GROUP_CONDITION_ACTION_BY_LABEL.get(self.group_action_var.get(), "fire")
        self._refresh_steps_tree()
        self.tree.selection_set(f"group-{group_idx}")

    # ---- recalibrating a group's match (double-click its row) ---------------

    def _on_group_row_double_click(self, group_idx: int):
        """Double-clicking a condition group's own row recalibrates its
        match in place -- same capture flow as creating one, but replacing
        rather than appending -- exactly like recalibrating a step's
        condition does (see ConditionsMixin._on_tree_double_click, which
        dispatches here for a group row). Name/Action/Negate carry over
        unchanged; a group's condition is never match_type "timer", so
        there's no timer branch to handle here."""
        group = self.editing_steps[group_idx]
        condition = group.condition

        def replace(new_condition: Condition):
            new_condition.name = condition.name
            new_condition.negate = condition.negate
            new_condition.action = condition.action
            group.condition = new_condition
            self._refresh_steps_tree()
            self._populate_group_condition_form(group)

        if condition.match_type == "pixel":
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
