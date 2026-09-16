from tkinter import ttk

from poe2bot.models import MAX_GROUP_NESTING_DEPTH, ConditionGroup


class DragDropMixin:
    """Drag-and-drop reordering and reparenting in the step Treeview: a step
    into/out of/between Condition Groups, and (since groups may now nest,
    see poe2bot/models.py's ConditionGroup) a Condition Group into/out of/
    between other groups. Mixed into App (see poe2bot/gui/app.py) -- calls
    into StepEditorMixin (self.editing_steps, self._parse_tree_iid,
    self._steps_list_for, self._location_iid, self._refresh_steps_tree,
    self._apply_pending_step_edits from App itself) freely.

    Every dragged/hovered row is addressed by StepEditorMixin's
    (group_path, step_idx, cond_idx) location tuple -- a group header
    (step_idx is None), a step (group_path names whichever list -- the top
    level, or some nested group's own entries -- it currently lives in), or
    one of a step's own conditions. A drag set is always one uniform kind
    (see _is_valid_drag_set): any number of Condition Groups sharing the
    same current parent list, several steps sharing the same current
    parent list (all top-level, or all nested in the same group -- letting
    a group's worth of steps be dropped somewhere with a *different*
    parent than they started with, which is what makes cross-group moves
    possible), or several conditions of the same step.

    Nesting a group happens the same way a step already nests into a group
    today: drop it onto another group's own header row -- at any depth,
    not just among current siblings -- and it becomes that group's first
    child. Two guards keep this sane: a group can never be dropped into
    itself or one of its own current descendants (which would create a
    cycle), and dropping is refused past MAX_GROUP_NESTING_DEPTH.

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
        """Parses every iid into a (group_path, step_idx, cond_idx) location,
        sorted with None treated as less than any int for step_idx/cond_idx
        (group_path is always a tuple of ints -- itself already directly
        comparable/sortable) -- a raw multi-selection can freely mix rows at
        different nesting depths before _is_valid_drag_set has had a chance
        to reject an invalid mix."""
        locations = [p for p in (self._parse_tree_iid(iid) for iid in iids) if p is not None]
        return sorted(locations, key=lambda p: (p[0], -1 if p[1] is None else p[1], -1 if p[2] is None else p[2]))

    def _sibling_iid(self, container_path, local_index: int) -> str:
        """The tree iid of the entry at `local_index` within
        self._steps_list_for(container_path) -- the generalized counterpart
        to indexing self.editing_steps directly, used by _resolve_sibling_target
        to build a highlight/bbox-lookup row for whichever list a drag is
        currently being resolved against."""
        entry = self._steps_list_for(container_path)[local_index]
        return (self._location_iid(container_path + (local_index,), None) if isinstance(entry, ConditionGroup)
                else self._location_iid(container_path, local_index))

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
            return len({gp[:-1] for gp, _s, _c in group_entries}) == 1  # all groups sharing the same current parent
        if cond_entries:
            return len({(gp, s) for gp, s, _c in candidate}) == 1  # all conditions of the same step
        return len({gp for gp, _s, _c in candidate}) == 1  # all steps sharing the same current parent

    @staticmethod
    def _path_within(path, ancestor_candidates) -> bool:
        """True if `path` is exactly, or nested inside, any path in
        `ancestor_candidates` -- used to stop a dragged group from being
        dropped into itself or one of its own current descendants, which
        would otherwise create a cycle."""
        return any(len(a) <= len(path) and path[:len(a)] == a for a in ancestor_candidates)

    @classmethod
    def _group_subtree_depth(cls, group) -> int:
        """1 (just `group` itself, no ConditionGroup nested inside it) plus
        the deepest chain of ConditionGroups nested inside it -- used so a
        drop is refused not just when the dragged group ITSELF would land
        past MAX_GROUP_NESTING_DEPTH, but also when some group nested
        inside it would."""
        nested = [e for e in group.entries if isinstance(e, ConditionGroup)]
        return 1 + max((cls._group_subtree_depth(e) for e in nested), default=0)

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
        _highlight_iid, dest_container_path, target_index, after = target
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
            parent_path = candidate[0][0][:-1]
            source_list = self._steps_list_for(parent_path)
            dest_list = self._steps_list_for(dest_container_path)
            dragged_indices = sorted(gp[-1] for gp, _s, _c in candidate)
            start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(dest_container_path + (start + k,), None)
                                       for k in range(len(dragged_indices))))
        else:
            source_group = candidate[0][0]
            source_list = self._steps_list_for(source_group)
            dest_list = self._steps_list_for(dest_container_path)
            dragged_indices = sorted(s for _g, s, _c in candidate)
            start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(dest_container_path, start + k)
                                       for k in range(len(dragged_indices))))
        self._autosave()

    def _resolve_drop_target(self, event, candidate):
        """Returns (highlight_iid, dest_container_path, target_index, after)
        for the given drag candidate (a list of (group_path, step_idx,
        cond_idx) locations, all the same kind per _is_valid_drag_set), or
        None if there's no valid drop here. target_index is an index into
        the destination list *before* removing the dragged items --
        self.editing_steps for a drop landing at the top level, some
        group's own .entries for a drop landing inside/within a group, or
        the owning step's .conditions for a condition drop. dest_container_path
        is only meaningful for a group or step drag (which list, identified
        by its own group_path, the drop lands in); ignored by callers for a
        condition drag, which can only ever land in its own one fixed
        list."""
        dragging_conditions = candidate[0][2] is not None
        dragging_groups = candidate[0][1] is None
        target_row = self.tree.identify_row(event.y)
        parsed = self._parse_tree_iid(target_row) if target_row else None

        if dragging_conditions:
            return self._resolve_condition_drop_target(event, candidate, parsed, target_row)

        if dragging_groups:
            return self._resolve_group_drop_target(event, candidate, parsed, target_row)

        # Dragging steps.
        if parsed is not None and parsed[1] is None:
            # Hovering a group's own header row -- drop INTO it, at the front,
            # mirroring "hovering the step's own row targets index 0 of its
            # conditions" one level up (see _resolve_condition_drop_target).
            # Unlike that case, the target group can genuinely be empty right
            # now (dropping the first step ever into it) -- there's no
            # existing child row yet to highlight, so fall back to
            # highlighting the group's own row instead of a nonexistent one.
            dest_group_path = parsed[0]
            # _sibling_iid (not a bare _location_iid(dest_group_path, 0)) --
            # whatever's CURRENTLY at index 0 of this group's entries might
            # itself be a nested ConditionGroup rather than a Step, now that
            # groups can nest, so the highlighted row's iid must be built
            # according to what that entry actually is.
            highlight_iid = (self._sibling_iid(dest_group_path, 0)
                              if self._steps_list_for(dest_group_path) else target_row)
            return highlight_iid, dest_group_path, 0, False
        if parsed is not None and parsed[2] is not None:
            # Hovered a condition row -- treat a step and its conditions as one block.
            target_row, parsed = self._location_iid(parsed[0], parsed[1]), (parsed[0], parsed[1], None)
        if parsed is not None:
            dest_group_path = parsed[0]
            if (dest_group_path, parsed[1]) in {(g, s) for g, s, _c in candidate}:
                return None  # dropped on one of the dragged steps itself
            bbox = self.tree.bbox(target_row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return target_row, dest_group_path, parsed[1], after
        source_group = candidate[0][0]
        exclude = {s for _g, s, _c in candidate}
        result = self._resolve_sibling_target(event, None, exclude, source_group)
        return (result[0], source_group, result[1], result[2]) if result is not None else None

    def _resolve_group_drop_target(self, event, candidate, parsed, target_row):
        """Resolves a drop target while dragging one or more Condition
        Groups (candidate[*][1] is None), which all share the same current
        parent list (see _is_valid_drag_set). Hovering a DIFFERENT group's
        own header row -- one that isn't among the dragged groups and isn't
        nested inside one of them, and wouldn't push nesting past
        MAX_GROUP_NESTING_DEPTH -- nests the dragged group(s) as that
        group's first child(ren), exactly mirroring how a step dragged onto
        a group's header row already nests into it (see the step-dragging
        branch above) -- this is true even if the hovered group is a
        current sibling of the dragged one(s), same as a step dragged onto
        a sibling group's row already nests rather than just reordering.
        Hovering the dragged groups' own current parent's header row (or
        anywhere else that doesn't resolve to a valid nest target) instead
        reorders the dragged group(s) among their own current siblings."""
        dragged_paths = {gp for gp, _s, _c in candidate}
        parent_path = next(iter(dragged_paths))[:-1]

        if parsed is not None and parsed[0] and parsed[1] is not None:
            # Hovering a step or one of its conditions that itself lives
            # inside some group -- redirect to that OWNING group's header
            # row (whatever depth it's at), mirroring how a condition row
            # redirects to its owning step's row in
            # _resolve_condition_drop_target. A TOP-LEVEL step/condition
            # (parsed[0] == ()) has no owning group to redirect to -- left
            # as-is, it flows through to sibling-target resolution below,
            # used as a plain reorder anchor within the top-level list,
            # exactly like hovering a plain step already works for a step
            # drag.
            parsed = (parsed[0], None, None)

        if parsed is not None and parsed[1] is None:
            target_path = parsed[0]
            if target_path in dragged_paths or self._path_within(target_path, dragged_paths):
                return None  # dropped on one of the dragged groups itself, or one of their own descendants
            # No special-case for target_path == parent_path (hovering the
            # dragged group(s)' own current parent's row): that just falls
            # out of the same nest-at-front logic below as "reorder to the
            # front of the list they're already in," mirroring exactly how
            # a step dragged onto its own current group's header row
            # already just moves it to the front of that same list.
            max_dragged_depth = max(self._group_subtree_depth(self._group_at(gp)) for gp in dragged_paths)
            if len(target_path) + max_dragged_depth <= MAX_GROUP_NESTING_DEPTH:
                dest_entries = self._steps_list_for(target_path)
                highlight_iid = self._sibling_iid(target_path, 0) if dest_entries else target_row
                return highlight_iid, target_path, 0, False

        exclude = {gp[-1] for gp in dragged_paths}
        result = self._resolve_sibling_target(event, parsed, exclude, parent_path)
        return (result[0], parent_path, result[1], result[2]) if result is not None else None

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

    def _resolve_sibling_target(self, event, parsed, exclude_indices, container_path):
        """(highlight_iid, target_index, after) for a drop landing among
        self._steps_list_for(container_path)'s own entries -- used by a
        group drag (reordering among its current siblings) and, for the
        blank-space case, a step drag targeting its own current group (or
        the top level, when container_path is ()). `parsed` is whatever
        _parse_tree_iid returned for the currently-hovered row (or None for
        blank space above/below every row in this list); if it resolves to
        a row that ISN'T a direct child of container_path, that's treated
        the same as no row at all -- the caller is responsible for
        redirecting a nested row to its owning entry's own row first, same
        as _resolve_condition_drop_target/_resolve_group_drop_target
        already do before calling this. `exclude_indices` are local indices
        (within this list) that are themselves being dragged -- never a
        valid target."""
        if parsed is not None:
            group_path, step_idx, _cond_idx = parsed
            if step_idx is None and len(group_path) == len(container_path) + 1 and group_path[:-1] == container_path:
                local_index = group_path[-1]      # a group among these siblings
            elif step_idx is not None and group_path == container_path:
                local_index = step_idx             # a step among these siblings
            else:
                local_index = None                 # belongs to a different list entirely
            if local_index is None or local_index in exclude_indices:
                return None
            row = self._sibling_iid(container_path, local_index)
            bbox = self.tree.bbox(row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return row, local_index, after
        entries = self._steps_list_for(container_path)
        if not entries:
            return None
        last = len(entries) - 1
        first_row, last_row = self._sibling_iid(container_path, 0), self._sibling_iid(container_path, last)
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
