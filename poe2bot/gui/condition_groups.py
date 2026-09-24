from poe2bot.models import Condition, ConditionGroup


class ConditionGroupsMixin:
    """Rotation-level Condition Groups: creating one (Add Condition Group
    (Image)/(Pixel), always a top-level append), and recalibrating a
    group's match by double-clicking its row. A group may itself be nested
    inside another group (up to models.MAX_GROUP_NESTING_DEPTH) -- nesting
    is created only via drag-and-drop (see poe2bot/gui/drag_drop.py), never
    by these Add Condition Group buttons, which always append at the top
    level exactly as before. Mixed into App (see poe2bot/gui/app.py) --
    reuses CalibrationMixin's _start_image_capture/_start_pixel_capture via
    the on_use callback, exactly like ConditionsMixin does for a step's own
    conditions. The selected group's own Name/Action/Negate fields are
    edited through the unified detail editor instead -- see
    poe2bot/gui/gate_editor.py's GateEditorMixin, which this mixin's
    _selected_group_location is also shared with."""

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
            self._populate_gate_editor("rotation_group", group)
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
