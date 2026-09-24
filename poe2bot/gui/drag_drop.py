from tkinter import ttk

from poe2bot.models import MAX_GROUP_NESTING_DEPTH, ConditionGroup


class DragDropMixin:
    """Drag-and-drop reordering and reparenting in the step Treeview: a step
    into/out of/between Condition Groups, and (since groups may now nest,
    see poe2bot/models.py's ConditionGroup) a Condition Group into/out of/
    between other groups -- plus, one level deeper, a plain condition
    into/out of/between a step's own Skill Condition Groups (see
    poe2bot/models.py's SkillConditionGroup), and a Skill Condition Group
    itself reordered among a step's other conditions/groups. Mixed into App
    (see poe2bot/gui/app.py) -- calls into StepEditorMixin (self.editing_steps,
    self._parse_tree_iid, self._steps_list_for, self._location_iid,
    self._skill_group_children_for, self._is_skill_group_entry,
    self._refresh_steps_tree, self._apply_pending_step_edits from App
    itself) freely.

    Every dragged/hovered row is addressed by StepEditorMixin's
    (group_path, step_idx, cond_idx, subcond_idx) location tuple -- a group
    header (step_idx is None), a step (group_path names whichever list --
    the top level, or some nested group's own entries -- it currently lives
    in), one of a step's own top-level conditions/Skill Condition Groups
    (cond_idx set, subcond_idx None), or a condition nested inside one of
    those Skill Condition Groups (subcond_idx set). A Skill Condition
    Group's own children never nest further -- there's no analogous fifth
    level. A drag set is always one uniform kind (see _is_valid_drag_set):
    any number of Condition Groups sharing the same current parent list,
    several steps sharing the same current parent list (all top-level, or
    all nested in the same group -- letting a group's worth of steps be
    dropped somewhere with a *different* parent than they started with,
    which is what makes cross-group moves possible), several Skill
    Condition Group header rows sharing the same owning step (reorderable
    only within that step's own .conditions -- never nested into another
    such group, never moved to a different step), or several plain
    conditions sharing the same immediate list (a step's own top-level
    .conditions, or one specific Skill Condition Group's own children).

    Nesting a group happens the same way a step already nests into a group
    today: drop it onto the MIDDLE of another group's own header row -- at
    any depth, not just among current siblings -- and it becomes that
    group's first child. The TOP/BOTTOM edge of a group's header row means
    something different (see _hover_zone): reposition the dragged item as a
    plain sibling of that group instead, in its own parent list -- which is
    what lets something be dragged back out to a shallower level, including
    all the way to the top level, even when the only row available to hover
    is a single group with nothing else alongside it to drop next to.
    Dropping a nested group onto a plain STEP row (anywhere on it, no
    edge/middle distinction needed since a step can't be nested into)
    reorders/inserts it as a sibling within whatever list that step lives
    in, the same promotion idea. Two guards keep group-nesting sane: a
    group can never be dropped into itself or one of its own current
    descendants (which would create a cycle), and dropping is refused past
    MAX_GROUP_NESTING_DEPTH (see _resolve_group_drop_target).

    A plain condition dropped onto a Skill Condition Group's own header row
    works the same way, one level down and without any depth cap (a group's
    own children never nest further): the middle zone nests it inside as a
    new child, the top/bottom edge instead reorders/inserts it as a plain
    sibling at the STEP'S OWN top level (a Skill Condition Group's own
    header row always lives there, never inside another such group) -- see
    _resolve_condition_drop_target. A condition already nested inside a
    group is dragged back out the same way any condition is repositioned:
    drop it anywhere outside that group's own child rows and it lands at
    the step's own top level instead.

    A plain click (Button-1) on ttk.Treeview unconditionally collapses multi-
    selection to the single clicked row, synchronously, before any B1-Motion
    can fire -- so "what to drag" must be captured in the press handler
    itself (which runs before that collapse, since instance bindings fire
    before class bindings), not lazily on first motion. If the pressed row
    is already part of the current selection, the press handler suppresses
    the collapse (returns "break") so the whole multi-selection can be
    dragged as a group; the release handler replicates the collapse itself
    if it turns out no drag actually happened (a plain click, not a drag).
    A press that lands on a row's own expand/collapse arrow is left
    completely alone (no "break", no drag candidate) so Tk's native
    click-to-toggle keeps working there regardless of what's selected --
    see _on_tree_press.

    The tree is never rebuilt (_refresh_steps_tree) while a drag is in
    progress -- only at release, once the reorder is fully resolved -- since
    rebuilding would invalidate iids captured earlier in the drag.
    """

    def _drop_target_color(self) -> str:
        color = ttk.Style().lookup("Treeview", "background", ("selected",))
        return color or "#4a6984"

    def _sorted_locations(self, iids):
        """Parses every iid into a (group_path, step_idx, cond_idx,
        subcond_idx) location, sorted with None treated as less than any int
        for step_idx/cond_idx/subcond_idx (group_path is always a tuple of
        ints -- itself already directly comparable/sortable) -- a raw
        multi-selection can freely mix rows at different nesting depths
        before _is_valid_drag_set has had a chance to reject an invalid
        mix."""
        locations = [p for p in (self._parse_tree_iid(iid) for iid in iids) if p is not None]
        return sorted(locations, key=lambda p: (
            p[0], -1 if p[1] is None else p[1], -1 if p[2] is None else p[2], -1 if p[3] is None else p[3]))

    def _sibling_iid(self, container_path, local_index: int) -> str:
        """The tree iid of the entry at `local_index` within
        self._steps_list_for(container_path) -- the generalized counterpart
        to indexing self.editing_steps directly, used by _resolve_sibling_target
        to build a highlight/bbox-lookup row for whichever list a drag is
        currently being resolved against."""
        entry = self._steps_list_for(container_path)[local_index]
        return (self._location_iid(container_path + (local_index,), None) if isinstance(entry, ConditionGroup)
                else self._location_iid(container_path, local_index))

    def _is_valid_drag_set(self, candidate) -> bool:
        if not candidate:
            return False
        group_entries = [c for c in candidate if c[1] is None]
        cond_entries = [c for c in candidate if c[2] is not None]
        step_entries = [c for c in candidate if c[1] is not None and c[2] is None]
        if sum(bool(kind) for kind in (group_entries, cond_entries, step_entries)) > 1:
            return False  # mixed groups + steps + conditions
        if group_entries:
            return len({gp[:-1] for gp, _s, _c, _sc in group_entries}) == 1  # all groups sharing the same current parent
        if step_entries:
            return len({gp for gp, _s, _c, _sc in candidate}) == 1  # all steps sharing the same current parent
        # cond_entries: split into Skill Condition Group header rows vs.
        # plain Condition rows (top-level or nested one level) -- these two
        # kinds can never be dragged together as one set.
        skill_group_rows = [c for c in cond_entries if c[3] is None
                             and self._is_skill_group_entry(self._condition_entry_at(c[0], c[1], c[2]))]
        leaf_rows = [c for c in cond_entries if c not in skill_group_rows]
        if skill_group_rows and leaf_rows:
            return False
        if skill_group_rows:
            return len({(gp, s) for gp, s, _c, _sc in skill_group_rows}) == 1  # same owning step

        def container_key(gp, s, c, sc):
            return (gp, s, None) if sc is None else (gp, s, c)
        return len({container_key(*loc) for loc in leaf_rows}) == 1  # same immediate list

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

    @staticmethod
    def _fits_nesting_cap(container_path, max_dragged_depth: int) -> bool:
        """True if landing the dragged group(s) in `container_path` (their
        own new parent list, whatever depth it's at) keeps every entry in
        their combined subtree -- not just the dragged group(s) themselves,
        see _group_subtree_depth -- at or under MAX_GROUP_NESTING_DEPTH."""
        return len(container_path) + max_dragged_depth <= MAX_GROUP_NESTING_DEPTH

    def _row_is_after(self, row_iid: str, event_y: int) -> bool:
        """True if `event_y` sits at or past the vertical midpoint of
        `row_iid` -- the shared "dropped on the bottom half of this row, so
        land AFTER it rather than before" test every _resolve_*_drop_target's
        own sibling-row case uses; fails safe to False with no bbox (e.g. a
        scrolled-out-of-view row), exactly like each did inline before this
        was extracted."""
        bbox = self.tree.bbox(row_iid)
        return bool(bbox) and event_y >= bbox[1] + bbox[3] / 2

    def _blank_space_edge_target(self, event_y: int, count: int, iid_at):
        """(edge_iid, local_index, after) for a drop landing above the
        first or below the last of `count` sibling rows (addressed via
        iid_at(local_index)), when nothing else in that list is directly
        hovered -- shared by every _resolve_*_drop_target's own "blank
        space above/below this specific list" fallback. None if `count` is
        0, or the hover is neither clearly above the first row nor clearly
        below the last (e.g. it's ambiguously between two other lists'
        rows) -- callers embed the 3-tuple into their own richer target
        shape (see each one's own return statement)."""
        if not count:
            return None
        first_iid, last_iid = iid_at(0), iid_at(count - 1)
        first_bbox, last_bbox = self.tree.bbox(first_iid), self.tree.bbox(last_iid)
        if first_bbox and event_y < first_bbox[1]:
            return first_iid, 0, False
        if last_bbox and event_y >= last_bbox[1] + last_bbox[3]:
            return last_iid, count - 1, True
        return None

    def _hover_zone(self, row_iid: str, event_y: int) -> str:
        """Splits a hovered GROUP header row into three vertical zones, so a
        group can always be repositioned as a plain sibling of another group
        -- including all the way out to the top level -- even when that
        other group is the only row available to hover (no separate
        top-level row of its own to drop next to instead): the top and
        bottom quarters mean "insert as a sibling before/after this row" (in
        ITS OWN parent list), the middle half means "nest inside this row,
        as its first child" -- the existing, more discoverable behavior for
        a plain click-drag onto the middle of a row. Returns "before",
        "into", or "after"; "into" if the row has no bbox (shouldn't happen
        for a real hovered row, but keeps this total)."""
        bbox = self.tree.bbox(row_iid)
        if not bbox:
            return "into"
        _left, top, _width, height = bbox
        offset = event_y - top
        if offset < height * 0.25:
            return "before"
        if offset > height * 0.75:
            return "after"
        return "into"

    def _on_tree_press(self, event):
        if self.tree.identify_element(event.x, event.y) == "Treeitem.indicator":
            # The expand/collapse arrow -- never treat this as a drag, and
            # don't return "break" below (which a row already in the current
            # selection otherwise would): this binding runs before Tk's own
            # class-level bindings, so "break" would silently swallow the
            # native toggle for any row that happened to already be
            # selected, which is most of the time you'd actually want to
            # collapse/expand something. Leaving this click alone entirely
            # lets Tk's own indicator handling run exactly as it would with
            # no drag-and-drop bindings installed at all.
            self._drag_candidate = None
            return
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
        expected_iids = {self._location_iid(g, s, c, sc) for g, s, c, sc in candidate}
        if expected_iids == set(self.tree.selection()) and not self._apply_pending_step_edits():
            return
        target = self._resolve_drop_target(event, candidate)
        if target is None:
            return
        dragging_cond_slot = candidate[0][2] is not None
        dragging_groups = candidate[0][1] is None
        if dragging_cond_slot:
            is_skill_group_drag = candidate[0][3] is None and self._is_skill_group_entry(
                self._condition_entry_at(candidate[0][0], candidate[0][1], candidate[0][2]))
            if is_skill_group_drag:
                self._apply_skill_group_reorder(candidate, target)
            else:
                self._apply_condition_move(candidate, target)
        elif dragging_groups:
            _highlight_iid, dest_container_path, target_index, after = target
            parent_path = candidate[0][0][:-1]
            source_list = self._steps_list_for(parent_path)
            dest_list = self._steps_list_for(dest_container_path)
            dragged_indices = sorted(gp[-1] for gp, _s, _c, _sc in candidate)
            start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(dest_container_path + (start + k,), None)
                                       for k in range(len(dragged_indices))))
        else:
            _highlight_iid, dest_container_path, target_index, after = target
            source_group = candidate[0][0]
            source_list = self._steps_list_for(source_group)
            dest_list = self._steps_list_for(dest_container_path)
            dragged_indices = sorted(s for _g, s, _c, _sc in candidate)
            start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(self._location_iid(dest_container_path, start + k)
                                       for k in range(len(dragged_indices))))
        self._autosave()

    def _apply_skill_group_reorder(self, candidate, target):
        """Applies a drop for one or more Skill Condition Group header rows
        -- always reordered within their own owning step's .conditions (see
        _resolve_skill_group_drop_target)."""
        _highlight_iid, _dest, target_index, after = target
        group_path, step_idx = candidate[0][0], candidate[0][1]
        conditions = self._steps_list_for(group_path)[step_idx].conditions
        dragged_indices = sorted(c for _g, _s, c, _sc in candidate)
        start = self._move_items(conditions, dragged_indices, conditions, target_index, after)
        self._refresh_steps_tree()
        self.tree.selection_set(*(self._location_iid(group_path, step_idx, start + k)
                                   for k in range(len(dragged_indices))))

    def _apply_condition_move(self, candidate, target):
        """Applies a drop for one or more plain Conditions (top-level, or
        nested one level inside a Skill Condition Group) -- may land back in
        their own current list (reorder) or the OTHER list at the same step
        (reparenting into/out of a group), per _resolve_condition_drop_target's
        `dest_cond_idx` (that function repurposes this module's usual
        "dest_container_path" slot to mean: None = the step's own top level,
        an int = inside the Skill Condition Group at that cond_idx)."""
        _highlight_iid, dest_cond_idx, target_index, after = target
        group_path, step_idx = candidate[0][0], candidate[0][1]
        src_cond_idx, src_subcond_idx = candidate[0][2], candidate[0][3]
        source_list = (self._skill_group_children_for(group_path, step_idx, src_cond_idx)
                       if src_subcond_idx is not None
                       else self._steps_list_for(group_path)[step_idx].conditions)
        dest_list = (self._skill_group_children_for(group_path, step_idx, dest_cond_idx)
                     if dest_cond_idx is not None
                     else self._steps_list_for(group_path)[step_idx].conditions)
        dragged_indices = sorted((sc if src_subcond_idx is not None else c) for _g, _s, c, sc in candidate)
        start = self._move_items(source_list, dragged_indices, dest_list, target_index, after)
        self._refresh_steps_tree()
        if dest_cond_idx is not None:
            locs = [self._location_iid(group_path, step_idx, dest_cond_idx, start + k)
                    for k in range(len(dragged_indices))]
        else:
            locs = [self._location_iid(group_path, step_idx, start + k) for k in range(len(dragged_indices))]
        self.tree.selection_set(*locs)

    def _resolve_drop_target(self, event, candidate):
        """Returns (highlight_iid, dest_container_path, target_index, after)
        for the given drag candidate (a list of (group_path, step_idx,
        cond_idx, subcond_idx) locations, all the same kind per
        _is_valid_drag_set), or None if there's no valid drop here.
        target_index is an index into the destination list *before*
        removing the dragged items -- self.editing_steps for a drop landing
        at the top level, some group's own .entries for a drop landing
        inside/within a group, the owning step's .conditions for a
        top-level condition/Skill Condition Group drop, or a Skill
        Condition Group's own .conditions for a drop landing inside one.
        dest_container_path is only meaningful for a group or step drag
        (which list, identified by its own group_path, the drop lands in);
        for a condition drag it's repurposed by _resolve_condition_drop_target
        to mean dest_cond_idx instead (see _apply_condition_move); ignored
        entirely for a Skill Condition Group drag, which can only ever land
        in its one fixed owning-step list."""
        dragging_cond_slot = candidate[0][2] is not None
        dragging_groups = candidate[0][1] is None
        target_row = self.tree.identify_row(event.y)
        parsed = self._parse_tree_iid(target_row) if target_row else None

        if dragging_cond_slot:
            is_skill_group_drag = candidate[0][3] is None and self._is_skill_group_entry(
                self._condition_entry_at(candidate[0][0], candidate[0][1], candidate[0][2]))
            if is_skill_group_drag:
                return self._resolve_skill_group_drop_target(event, candidate, parsed, target_row)
            return self._resolve_condition_drop_target(event, candidate, parsed, target_row)

        if dragging_groups:
            return self._resolve_group_drop_target(event, candidate, parsed, target_row)

        # Dragging steps.
        if parsed is not None and parsed[1] is None:
            dest_group_path = parsed[0]
            zone = self._hover_zone(target_row, event.y)
            if zone == "into":
                # Drop INTO the group, at the front, mirroring "hovering the
                # step's own row targets index 0 of its conditions" one level
                # up (see _resolve_condition_drop_target). Unlike that case,
                # the target group can genuinely be empty right now (dropping
                # the first step ever into it) -- there's no existing child
                # row yet to highlight, so fall back to highlighting the
                # group's own row instead of a nonexistent one.
                # _sibling_iid (not a bare _location_iid(dest_group_path, 0))
                # -- whatever's CURRENTLY at index 0 of this group's entries
                # might itself be a nested ConditionGroup rather than a Step,
                # now that groups can nest, so the highlighted row's iid must
                # be built according to what that entry actually is.
                highlight_iid = (self._sibling_iid(dest_group_path, 0)
                                  if self._steps_list_for(dest_group_path) else target_row)
                return highlight_iid, dest_group_path, 0, False
            # "before"/"after" -- reposition as a plain sibling of the
            # hovered group itself, within ITS OWN parent list. This is what
            # lets a step be promoted out to the top level (or any shallower
            # group) even when the only row available to hover is a single
            # group with nothing alongside it -- hover that group's top or
            # bottom edge instead of its middle.
            return target_row, dest_group_path[:-1], dest_group_path[-1], zone == "after"
        if parsed is not None and parsed[2] is not None:
            # Hovered a condition row (or a Skill Condition Group's own
            # header row, or a condition nested inside one) -- treat a step
            # and everything under it as one block.
            target_row, parsed = self._location_iid(parsed[0], parsed[1]), (parsed[0], parsed[1], None, None)
        if parsed is not None:
            dest_group_path = parsed[0]
            if (dest_group_path, parsed[1]) in {(g, s) for g, s, _c, _sc in candidate}:
                return None  # dropped on one of the dragged steps itself
            return target_row, dest_group_path, parsed[1], self._row_is_after(target_row, event.y)
        source_group = candidate[0][0]
        exclude = {s for _g, s, _c, _sc in candidate}
        result = self._resolve_sibling_target(event, None, exclude, source_group)
        return (result[0], source_group, result[1], result[2]) if result is not None else None

    def _resolve_group_drop_target(self, event, candidate, parsed, target_row):
        """Resolves a drop target while dragging one or more Condition
        Groups (candidate[*][1] is None), which all share the same current
        parent list (see _is_valid_drag_set).

        Hovering a group's own header row -- any group, at any depth,
        whether or not it's a current sibling of the dragged one(s) -- means
        one of two things depending on WHERE on that row (see _hover_zone):
        the middle nests the dragged group(s) as that group's first
        child(ren), exactly mirroring how a step dropped on a group's row
        already nests into it; the top/bottom edge instead repositions the
        dragged group(s) as a plain SIBLING of the hovered group, in ITS OWN
        parent list. That sibling placement is what lets a nested group be
        dragged back out to a shallower level -- including all the way to
        the top level -- even when the only row available to hover is a
        single group with nothing alongside it to drop next to instead.
        Either way, refused (no valid drop) if the hovered group is one of
        the dragged ones itself, one of their own descendants (that would
        create a cycle), or landing there would push past
        MAX_GROUP_NESTING_DEPTH.

        Hovering a plain step (or one of its conditions, treated as hovering
        that step's own row) reorders/inserts the dragged group(s) as a
        sibling within WHATEVER list that step itself currently lives in --
        the top level, the dragged groups' own current parent, or any other
        group entirely -- the same promote-by-hovering-an-existing-entry
        idea as the sibling-edge case above. Blank space (nothing hovered)
        instead stays within the dragged group(s)' own current parent, same
        as before."""
        dragged_paths = {gp for gp, _s, _c, _sc in candidate}
        parent_path = next(iter(dragged_paths))[:-1]
        max_dragged_depth = max(self._group_subtree_depth(self._group_at(gp)) for gp in dragged_paths)

        if parsed is not None and parsed[2] is not None:
            # Hovered a condition row -- treat it as hovering its owning step's own row.
            parsed = (parsed[0], parsed[1], None, None)

        if parsed is not None and parsed[1] is None:
            target_path = parsed[0]
            if target_path in dragged_paths or self._path_within(target_path, dragged_paths):
                return None  # dropped on one of the dragged groups itself, or one of their own descendants
            zone = self._hover_zone(target_row, event.y)
            if zone == "into":
                # No special-case for target_path == parent_path (hovering
                # the dragged group(s)' own current parent's row): that just
                # falls out of this same nest-at-front logic as "reorder to
                # the front of the list they're already in," mirroring
                # exactly how a step dragged onto its own current group's
                # header row already just moves it to the front of that same
                # list.
                if not self._fits_nesting_cap(target_path, max_dragged_depth):
                    return None  # would exceed the nesting cap
                dest_entries = self._steps_list_for(target_path)
                highlight_iid = self._sibling_iid(target_path, 0) if dest_entries else target_row
                return highlight_iid, target_path, 0, False
            # "before"/"after" -- reposition as a plain sibling of the
            # hovered group, within ITS OWN parent list (same depth the
            # hovered group itself is already at, so the cap check uses that
            # group's own parent path, not one level deeper).
            sibling_container = target_path[:-1]
            if self._path_within(sibling_container, dragged_paths):
                return None  # would nest a dragged group inside its own current subtree
            if not self._fits_nesting_cap(sibling_container, max_dragged_depth):
                return None
            return target_row, sibling_container, target_path[-1], zone == "after"

        # Hovering a plain step, or blank space -- reorder/insert as a
        # sibling within whichever list is actually in scope: that step's
        # own current list if one is hovered, else the dragged group(s)'
        # own current parent for blank space.
        container_path = parsed[0] if parsed is not None else parent_path
        if self._path_within(container_path, dragged_paths):
            return None  # would require nesting a dragged group inside its own current subtree
        if not self._fits_nesting_cap(container_path, max_dragged_depth):
            return None
        exclude = {gp[-1] for gp in dragged_paths} if container_path == parent_path else set()
        result = self._resolve_sibling_target(event, parsed, exclude, container_path)
        return (result[0], container_path, result[1], result[2]) if result is not None else None

    def _resolve_skill_group_drop_target(self, event, candidate, parsed, target_row):
        """Resolves a drop while dragging one or more Skill Condition Group
        header rows -- always reordered within their own owning step's
        .conditions (never nested into another such group, never moved to a
        different step -- see this module's own class docstring). Unlike
        _resolve_group_drop_target there is no INTO zone, no nesting-depth
        cap, and the returned "dest" slot is unused (always None) -- every
        valid drop lands in this one fixed list."""
        group_path, step_idx = candidate[0][0], candidate[0][1]
        conditions = self._steps_list_for(group_path)[step_idx].conditions
        dragged_indices = {c for _g, _s, c, _sc in candidate}

        if parsed is not None and parsed[0] == group_path and parsed[1] == step_idx and parsed[2] is not None:
            hovered_idx = parsed[2]
            if hovered_idx in dragged_indices:
                return None
            # Hovering one of this group's own nested condition rows counts
            # as hovering the group's own header row -- there's no sibling
            # ordering to resolve one level deeper here.
            row = target_row if parsed[3] is None else self._location_iid(group_path, step_idx, hovered_idx)
            return row, None, hovered_idx, self._row_is_after(row, event.y)

        if parsed is not None and parsed[0] == group_path and parsed[1] == step_idx and parsed[2] is None:
            # Hovering the step's own row -- it sits above its conditions.
            return self._location_iid(group_path, step_idx, 0), None, 0, False

        result = self._blank_space_edge_target(
            event.y, len(conditions), lambda i: self._location_iid(group_path, step_idx, i))
        return (result[0], None, result[1], result[2]) if result is not None else None

    def _resolve_condition_drop_target(self, event, candidate, parsed, target_row):
        """Resolves a drop while dragging one or more plain Conditions
        (never a Skill Condition Group's own header row -- see
        _resolve_skill_group_drop_target for that) -- always within the
        SAME owning step, but potentially a DIFFERENT immediate list within
        it: the step's own top-level .conditions, or one specific Skill
        Condition Group's own children. Hovering a Skill Condition Group's
        own header row means one of two things depending on WHERE on that
        row (see _hover_zone): the middle nests the dragged condition(s)
        inside it as new children, exactly mirroring how a step dropped on
        a rotation-level group's row already nests into it; the top/bottom
        edge instead repositions them as a plain sibling of that group, at
        the STEP'S OWN top level (a group's own header row always lives
        there, never inside another such group). Hovering a condition
        that's already nested inside SOME group (any group, not just the
        dragged item(s)' own current one) reorders/inserts as its sibling,
        within THAT group's own children -- the same promote-or-move idea a
        plain leaf-to-leaf drop already has. Repurposes this module's usual
        "dest_container_path" return slot to instead mean `dest_cond_idx`:
        None = the step's own top level, an int = inside the Skill
        Condition Group at that cond_idx of the step's own .conditions (see
        DragDropMixin._apply_condition_move)."""
        owning_group, owning_step = candidate[0][0], candidate[0][1]
        src_cond_idx, src_subcond_idx = candidate[0][2], candidate[0][3]
        top_level = self._steps_list_for(owning_group)[owning_step].conditions
        if not top_level:
            return None
        dragged_source_container = None if src_subcond_idx is None else src_cond_idx
        dragged_positions = {(sc if src_subcond_idx is not None else c) for _g, _s, c, sc in candidate}

        def is_dragged(dest_cond_idx, local_index) -> bool:
            return dest_cond_idx == dragged_source_container and local_index in dragged_positions

        if parsed is not None and parsed[0] == owning_group and parsed[1] == owning_step and parsed[2] is not None:
            hovered_cond_idx, hovered_subcond_idx = parsed[2], parsed[3]
            if hovered_subcond_idx is not None:
                # Hovered a condition nested inside SOME Skill Condition
                # Group -- reorder/insert as its sibling, within THAT
                # group's own children.
                if is_dragged(hovered_cond_idx, hovered_subcond_idx):
                    return None
                return target_row, hovered_cond_idx, hovered_subcond_idx, self._row_is_after(target_row, event.y)
            hovered_entry = top_level[hovered_cond_idx]
            if self._is_skill_group_entry(hovered_entry):
                zone = self._hover_zone(target_row, event.y)
                if zone == "into":
                    children = hovered_entry.conditions
                    highlight_iid = (self._location_iid(owning_group, owning_step, hovered_cond_idx, 0)
                                      if children else target_row)
                    return highlight_iid, hovered_cond_idx, 0, False
                # "before"/"after" -- plain sibling at the STEP'S OWN TOP
                # LEVEL (a group's own header row always lives there).
                return target_row, None, hovered_cond_idx, zone == "after"
            if is_dragged(None, hovered_cond_idx):
                return None  # dropped on one of the dragged rows itself
            return target_row, None, hovered_cond_idx, self._row_is_after(target_row, event.y)
        if parsed is not None and parsed[0] == owning_group and parsed[1] == owning_step and parsed[2] is None:
            # Hovering the step's own row -- it sits above its conditions.
            return self._location_iid(owning_group, owning_step, 0), None, 0, False
        # Anywhere else is only valid if it's clearly beyond this step's own
        # condition block (above the first / below the last of *that* step).
        # Always lands at the step's own TOP LEVEL -- there's no unambiguous
        # "which group" for a blank-space drop.
        result = self._blank_space_edge_target(
            event.y, len(top_level), lambda i: self._location_iid(owning_group, owning_step, i))
        return (result[0], None, result[1], result[2]) if result is not None else None

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
            group_path, step_idx, _cond_idx, _subcond_idx = parsed
            if step_idx is None and len(group_path) == len(container_path) + 1 and group_path[:-1] == container_path:
                local_index = group_path[-1]      # a group among these siblings
            elif step_idx is not None and group_path == container_path:
                local_index = step_idx             # a step among these siblings
            else:
                local_index = None                 # belongs to a different list entirely
            if local_index is None or local_index in exclude_indices:
                return None
            row = self._sibling_iid(container_path, local_index)
            return row, local_index, self._row_is_after(row, event.y)
        entries = self._steps_list_for(container_path)
        return self._blank_space_edge_target(event.y, len(entries), lambda i: self._sibling_iid(container_path, i))

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
