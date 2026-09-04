from tkinter import ttk

from poe2bot.models import ConditionGroup


class DragDropMixin:
    """Drag-and-drop reordering (and, for steps, reparenting into/out of a
    Condition Group) in the step Treeview. Mixed into App (see
    poe2bot/gui/app.py) -- calls into StepEditorMixin (self.editing_steps,
    self._parse_tree_iid, self._steps_list_for, self._location_iid,
    self._refresh_steps_tree, self._apply_pending_step_edits from App
    itself) freely.

    Every dragged/hovered row is addressed by StepEditorMixin's
    (group_idx, step_idx, cond_idx) location tuple -- a group header
    (step_idx is None), a step (top-level when group_idx is None, else
    nested in that group), or one of a step's own conditions. A drag set
    is always one uniform kind (see _is_valid_drag_set): any number of
    top-level groups, several steps sharing the same current parent
    (all top-level, or all nested in the same group -- letting a group's
    worth of steps be dropped somewhere with a *different* parent than
    they started with, which is what makes cross-group moves possible), or
    several conditions of the same step.

    A plain click (Button-1) on ttk.Treeview unconditionally collapses multi-
    selection to the single clicked row, synchronously, before any B1-Motion
    can fire -- so "what to drag" must be captured in the press handler
    itself (which runs before that collapse, since instance bindings fire
    before class bindings), not lazily on first motion. If the pressed row
    is already part of the current selection, the press handler suppresses
    the collapse (returns "break") so the whole multi-selection can be
    dragged as a group; the release handler replicates the collapse itself
    if it turns out no drag actually happened (a plain click, not a drag).

    The tree is never rebuilt (_refresh_steps_tree) while a drag is in
    progress -- only at release, once the reorder is fully resolved -- since
    rebuilding would invalidate iids captured earlier in the drag.
    """

    def _drop_target_color(self) -> str:
        color = ttk.Style().lookup("Treeview", "background", ("selected",))
        return color or "#4a6984"

    def _sorted_locations(self, iids):
        """Parses every iid into a (group_idx, step_idx, cond_idx) location,
        sorted with None treated as less than any int -- a raw multi-
        selection can freely mix rows whose locations have None in
        different positions (e.g. a top-level step next to one nested in a
        group) before _is_valid_drag_set has had a chance to reject that
        mix, and comparing None to an int directly would raise."""
        locations = [p for p in (self._parse_tree_iid(iid) for iid in iids) if p is not None]
        return sorted(locations, key=lambda p: tuple(-1 if x is None else x for x in p))

    def _top_level_iid(self, index: int) -> str:
        entry = self.editing_steps[index]
        return self._location_iid(index, None) if isinstance(entry, ConditionGroup) else self._location_iid(None, index)

    @staticmethod
    def _is_valid_drag_set(candidate) -> bool:
        if not candidate:
            return False
        group_entries = [c for c in candidate if c[1] is None]
        cond_entries = [c for c in candidate if c[2] is not None]
        step_entries = [c for c in candidate if c[1] is not None and c[2] is None]
        if sum(bool(kind) for kind in (group_entries, cond_entries, step_entries)) > 1:
            return False  # mixed groups + steps + conditions
        if group_entries:
            return True  # any number of top-level groups, always reorder-only
        if cond_entries:
            return len({(g, s) for g, s, _c in candidate}) == 1  # all conditions of the same step
        return len({g for g, _s, _c in candidate}) == 1  # all steps sharing the same current parent

    def _on_tree_press(self, event):
        if event.state & 0x0005:  # Shift (0x1) or Control (0x4) held -- leave extend/toggle select alone
            self._drag_candidate = None
            return
        self._drag_start_xy = (event.x, event.y)
        self._drag_active = False
        self._drop_target_iid = None
        row = self.tree.identify_row(event.y)
        if not row:
            self._drag_candidate = None
            return
        current_selection = self.tree.selection()
        if row in current_selection:
            candidate = self._sorted_locations(current_selection)
            if self._is_valid_drag_set(candidate):
                self._drag_candidate = candidate
                self.tree.focus_set()
                self.tree.focus(row)
                return "break"
            self._drag_candidate = None
            return
        parsed = self._parse_tree_iid(row)
        self._drag_candidate = [parsed] if parsed is not None else None

    def _set_drop_target_tag(self, iid, present: bool):
        """Adds/removes just the "drop_target" tag on `iid`, preserving any
        other tag it already has (e.g. "step_disabled", see
        StepEditorMixin._step_row_tags) -- setting `tags=` outright would
        otherwise silently wipe those out for the duration of a drag (and,
        via the `tags=()` clear, permanently once the drag moves past that
        row)."""
        tags = set(self.tree.item(iid, "tags"))
        if present:
            tags.add("drop_target")
        else:
            tags.discard("drop_target")
        self.tree.item(iid, tags=tuple(tags))

    def _on_tree_motion(self, event):
        if not self._drag_candidate:
            return
        if not self._drag_active:
            if abs(event.x - self._drag_start_xy[0]) < 4 and abs(event.y - self._drag_start_xy[1]) < 4:
                return
            self._drag_active = True
        target = self._resolve_drop_target(event, self._drag_candidate)
        new_target_iid = target[0] if target is not None else None
        if new_target_iid != self._drop_target_iid:
            if self._drop_target_iid is not None:
                self._set_drop_target_tag(self._drop_target_iid, False)
            if new_target_iid is not None:
                self._set_drop_target_tag(new_target_iid, True)
            self._drop_target_iid = new_target_iid

    def _on_tree_release(self, event):
        if self._drop_target_iid is not None:
            self._set_drop_target_tag(self._drop_target_iid, False)
            self._drop_target_iid = None
        candidate = self._drag_candidate
        self._drag_candidate = None
        if not candidate:
            return
        if not self._drag_active:
            # No real drag happened -- replicate Tk's own click-to-select (which
            # the press handler suppressed) so a plain click still behaves normally.
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
                self.tree.focus(row)
            return
        self._drag_active = False
        # If the row(s) being dragged are exactly what's currently selected (the
        # common case -- you select a step, then drag that same step), apply any
        # pending form edit first so reordering it doesn't silently discard an
        # edit still sitting in the form. Skipped when they don't match (e.g.
        # dragging a row that isn't the current selection) -- there's no single
        # unambiguous step to apply the form to in that case, so leave it as-is
        # rather than risk applying it to the wrong step.
        expected_iids = {self._location_iid(g, s, c) for g, s, c in candidate}
        if expected_iids == set(self.tree.selection()) and not self._apply_pending_step_edits():
            return
        target = self._resolve_drop_target(event, candidate)
        if target is None:
            return
        _highlight_iid, dest_group_idx, target_index, after = target
        dragging_conditions = candidate[0][2] is not None
        dragging_groups = candidate[0][1] is None
        if dragging_conditions:
            owning_group, owning_step = candidate[0][0], candidate[0][1]
            conditions = self._steps_list_for(owning_group)[owning_step].conditions
            dragged_indices = sorted(c for _g, _s, c in candidate)
            start = self._move_items(conditions, dragged_indices, conditions, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(owning_group, owning_step, start + k)
                                       for k in range(len(dragged_indices))))
        elif dragging_groups:
            dragged_indices = sorted(g for g, _s, _c in candidate)
            start = self._move_items(self.editing_steps, dragged_indices, self.editing_steps, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(start + k, None) for k in range(len(dragged_indices))))
        else:
            source_group = candidate[0][0]
            source_list = self._steps_list_for(source_group)
            dest_list = self._steps_list_for(dest_group_idx)
            dragged_indices = sorted(s for _g, s, _c in candidate)
            start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(dest_group_idx, start + k)
                                       for k in range(len(dragged_indices))))
        self._autosave()

    def _resolve_drop_target(self, event, candidate):
        """Returns (highlight_iid, dest_group_idx, target_index, after) for
        the given drag candidate (a list of (group_idx, step_idx, cond_idx)
        locations, all the same kind per _is_valid_drag_set), or None if
        there's no valid drop here. target_index is an index into the
        destination list *before* removing the dragged items --
        self.editing_steps for a group drop or a step drop landing at the
        top level, some group's own .steps for a step drop landing
        inside/within a group, or the owning step's .conditions for a
        condition drop. dest_group_idx is only meaningful for a step drag
        (which group, if any, the drop lands in); ignored by callers for a
        group or condition drag, which can only ever land in their own one
        fixed list."""
        dragging_conditions = candidate[0][2] is not None
        dragging_groups = candidate[0][1] is None
        target_row = self.tree.identify_row(event.y)
        parsed = self._parse_tree_iid(target_row) if target_row else None

        if dragging_conditions:
            return self._resolve_condition_drop_target(event, candidate, parsed, target_row)

        if dragging_groups:
            # Groups never nest -- redirect a hover over a nested step (or one
            # of its conditions) to that step's own top-level group, mirroring
            # how a condition row redirects to its owning step's row below.
            if parsed is not None and parsed[0] is not None and parsed[1] is not None:
                parsed = (parsed[0], None, None)
            exclude = {g for g, _s, _c in candidate}
            result = self._resolve_top_level_target(event, parsed, exclude)
            return (result[0], None, result[1], result[2]) if result is not None else None

        # Dragging steps.
        if parsed is not None and parsed[1] is None:
            # Hovering a group's own header row -- drop INTO it, at the front,
            # mirroring "hovering the step's own row targets index 0 of its
            # conditions" one level up (see _resolve_condition_drop_target).
            # Unlike that case, the target group can genuinely be empty right
            # now (dropping the first step ever into it) -- there's no
            # existing child row yet to highlight, so fall back to
            # highlighting the group's own row instead of a nonexistent one.
            dest_group_idx = parsed[0]
            highlight_iid = (self._location_iid(dest_group_idx, 0)
                              if self._steps_list_for(dest_group_idx) else target_row)
            return highlight_iid, dest_group_idx, 0, False
        if parsed is not None and parsed[2] is not None:
            # Hovered a condition row -- treat a step and its conditions as one block.
            target_row, parsed = self._location_iid(parsed[0], parsed[1]), (parsed[0], parsed[1], None)
        if parsed is not None:
            dest_group_idx = parsed[0]
            if (dest_group_idx, parsed[1]) in {(g, s) for g, s, _c in candidate}:
                return None  # dropped on one of the dragged steps itself
            bbox = self.tree.bbox(target_row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return target_row, dest_group_idx, parsed[1], after
        exclude = {s for g, s, _c in candidate if g is None}
        result = self._resolve_top_level_target(event, None, exclude)
        return (result[0], None, result[1], result[2]) if result is not None else None

    def _resolve_condition_drop_target(self, event, candidate, parsed, target_row):
        owning_group, owning_step = candidate[0][0], candidate[0][1]
        conditions = self._steps_list_for(owning_group)[owning_step].conditions
        if not conditions:
            return None
        if parsed is not None and parsed[0] == owning_group and parsed[1] == owning_step and parsed[2] is not None:
            if (owning_group, owning_step, parsed[2]) in candidate:
                return None  # dropped on one of the dragged rows itself
            bbox = self.tree.bbox(target_row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return target_row, None, parsed[2], after
        if parsed is not None and parsed[0] == owning_group and parsed[1] == owning_step and parsed[2] is None:
            # Hovering the step's own row -- it sits above its conditions.
            return self._location_iid(owning_group, owning_step, 0), None, 0, False
        # Anywhere else is only valid if it's clearly beyond this step's own
        # condition block (above the first / below the last of *that* step).
        first_iid = self._location_iid(owning_group, owning_step, 0)
        last_iid = self._location_iid(owning_group, owning_step, len(conditions) - 1)
        first_bbox, last_bbox = self.tree.bbox(first_iid), self.tree.bbox(last_iid)
        if first_bbox and event.y < first_bbox[1]:
            return first_iid, None, 0, False
        if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
            return last_iid, None, len(conditions) - 1, True
        return None

    def _resolve_top_level_target(self, event, parsed, exclude_indices):
        """(highlight_iid, target_index, after) for a drop landing among
        self.editing_steps' own top-level entries -- used by a group drag
        (which can only ever reorder among top-level entries) and, for the
        blank-space case, a step drag targeting the top level. `parsed` is
        whatever _parse_tree_iid returned for the currently-hovered row (or
        None for blank space above/below every row); `exclude_indices` are
        top-level indices that are themselves being dragged (never a valid
        target)."""
        if parsed is not None:
            if parsed[1] is None:
                top_index = parsed[0]      # a group header -- its own top-level index
            elif parsed[0] is None:
                top_index = parsed[1]      # a top-level step -- its own top-level index
            else:
                top_index = None           # a nested row -- caller must redirect before calling this
            if top_index is None or top_index in exclude_indices:
                return None
            row = self._top_level_iid(top_index)
            bbox = self.tree.bbox(row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return row, top_index, after
        if not self.editing_steps:
            return None
        last = len(self.editing_steps) - 1
        first_row, last_row = self._top_level_iid(0), self._top_level_iid(last)
        first_bbox, last_bbox = self.tree.bbox(first_row), self.tree.bbox(last_row)
        if first_bbox and event.y < first_bbox[1]:
            return first_row, 0, False
        if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
            return last_row, last, True
        return None

    @staticmethod
    def _move_items(source_list: list, dragged_indices, dest_list: list, target_index: int, after: bool) -> int:
        """Moves the items at dragged_indices (sorted ascending, indices
        into source_list) to just before/after target_index (an index into
        dest_list *before* removal) -- removing them from source_list and
        inserting into dest_list, preserving their relative order.
        Identical math/behavior to a same-list reorder when dest_list is
        source_list (the removal-index adjustment below only matters in
        that case -- when they're different lists, removing from one never
        shifts positions in the other). Mutates both lists in place;
        returns the index the first moved item ends up at in dest_list, so
        callers can reselect the moved block."""
        moved = [source_list[i] for i in dragged_indices]
        for i in sorted(dragged_indices, reverse=True):
            del source_list[i]
        drop_pos = target_index + (1 if after else 0)
        if dest_list is source_list:
            removed_before_drop = sum(1 for i in dragged_indices if i < drop_pos)
            drop_pos -= removed_before_drop
        for offset, item in enumerate(moved):
            dest_list.insert(drop_pos + offset, item)
        return drop_pos
