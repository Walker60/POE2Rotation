from tkinter import ttk


class DragDropMixin:
    """Drag-and-drop reordering of steps and conditions in the step Treeview.
    Mixed into App (see poe2bot/gui/app.py) -- calls into StepEditorMixin
    (self.editing_steps, self._parse_tree_iid, self._refresh_steps_tree,
    self._apply_pending_step_edits from App itself) freely.

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

    @staticmethod
    def _is_valid_drag_set(candidate) -> bool:
        if not candidate:
            return False
        cond_entries = [c for s, c in candidate if c is not None]
        step_entries = [s for s, c in candidate if c is None]
        if cond_entries and step_entries:
            return False  # mixed steps + conditions
        if cond_entries:
            return len({s for s, c in candidate}) == 1  # all conditions of the same step
        return True  # all steps

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
            candidate = sorted(p for p in (self._parse_tree_iid(iid) for iid in current_selection)
                                if p is not None)
            if self._is_valid_drag_set(candidate):
                self._drag_candidate = candidate
                self.tree.focus_set()
                self.tree.focus(row)
                return "break"
            self._drag_candidate = None
            return
        parsed = self._parse_tree_iid(row)
        self._drag_candidate = [parsed] if parsed is not None else None

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
                self.tree.item(self._drop_target_iid, tags=())
            if new_target_iid is not None:
                self.tree.item(new_target_iid, tags=("drop_target",))
            self._drop_target_iid = new_target_iid

    def _on_tree_release(self, event):
        if self._drop_target_iid is not None:
            self.tree.item(self._drop_target_iid, tags=())
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
        expected_iids = {f"step-{s}" if c is None else f"step-{s}-cond-{c}" for s, c in candidate}
        if expected_iids == set(self.tree.selection()) and not self._apply_pending_step_edits():
            return
        target = self._resolve_drop_target(event, candidate)
        if target is None:
            return
        _highlight_iid, target_index, after = target
        dragging_conditions = candidate[0][1] is not None
        if dragging_conditions:
            owning_step = candidate[0][0]
            dragged_indices = sorted(c for s, c in candidate)
            start = self._reorder(self.editing_steps[owning_step].conditions, dragged_indices, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(f"step-{owning_step}-cond-{start + k}" for k in range(len(dragged_indices))))
        else:
            dragged_indices = sorted(s for s, c in candidate)
            start = self._reorder(self.editing_steps, dragged_indices, target_index, after)
            self._refresh_steps_tree()
            self.tree.selection_set(*(f"step-{start + k}" for k in range(len(dragged_indices))))

    def _resolve_drop_target(self, event, candidate):
        """Returns (highlight_iid, target_index, after) for the given drag
        candidate (list[(step_idx, cond_idx_or_None)]), or None if there's no
        valid drop here. target_index is an index into whichever list is
        being dragged (self.editing_steps for a step drag, or the owning
        step's .conditions for a condition drag), referring to a position in
        that list as it stood *before* removing the dragged items."""
        dragging_conditions = candidate[0][1] is not None
        target_row = self.tree.identify_row(event.y)
        parsed = self._parse_tree_iid(target_row) if target_row else None

        if dragging_conditions:
            owning_step = candidate[0][0]
            conditions = self.editing_steps[owning_step].conditions
            if not conditions:
                return None
            if parsed is not None and parsed[0] == owning_step and parsed[1] is not None:
                if (owning_step, parsed[1]) in candidate:
                    return None  # dropped on one of the dragged rows itself
                bbox = self.tree.bbox(target_row)
                after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
                return target_row, parsed[1], after
            if parsed is not None and parsed[0] == owning_step and parsed[1] is None:
                # Hovering the step's own row -- it sits above its conditions.
                return f"step-{owning_step}-cond-0", 0, False
            # Anywhere else is only valid if it's clearly beyond this step's own
            # condition block (above the first / below the last of *that* step).
            first_iid, last_iid = f"step-{owning_step}-cond-0", f"step-{owning_step}-cond-{len(conditions) - 1}"
            first_bbox, last_bbox = self.tree.bbox(first_iid), self.tree.bbox(last_iid)
            if first_bbox and event.y < first_bbox[1]:
                return first_iid, 0, False
            if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
                return last_iid, len(conditions) - 1, True
            return None

        if not self.editing_steps:
            return None
        if parsed is not None and parsed[1] is not None:
            # Hovered a condition row -- treat a step and its conditions as one block.
            target_row, parsed = f"step-{parsed[0]}", (parsed[0], None)
        if parsed is not None:
            if parsed[0] in {s for s, c in candidate}:
                return None  # dropped on one of the dragged steps itself
            bbox = self.tree.bbox(target_row)
            after = bool(bbox) and event.y >= bbox[1] + bbox[3] / 2
            return target_row, parsed[0], after
        last = len(self.editing_steps) - 1
        first_bbox, last_bbox = self.tree.bbox("step-0"), self.tree.bbox(f"step-{last}")
        if first_bbox and event.y < first_bbox[1]:
            return "step-0", 0, False
        if last_bbox and event.y >= last_bbox[1] + last_bbox[3]:
            return f"step-{last}", last, True
        return None

    @staticmethod
    def _reorder(items: list, dragged_indices, target_index: int, after: bool) -> int:
        """Moves items at dragged_indices (sorted ascending) to just before/
        after target_index (an index into items *before* removal), preserving
        their relative order. Mutates items in place; returns the index the
        first moved item ends up at, so callers can reselect the moved block."""
        moved = [items[i] for i in dragged_indices]
        drop_pos = target_index + (1 if after else 0)
        removed_before_drop = sum(1 for i in dragged_indices if i < drop_pos)
        adjusted_drop_pos = drop_pos - removed_before_drop
        for i in sorted(dragged_indices, reverse=True):
            del items[i]
        for offset, item in enumerate(moved):
            items.insert(adjusted_drop_pos + offset, item)
        return adjusted_drop_pos
