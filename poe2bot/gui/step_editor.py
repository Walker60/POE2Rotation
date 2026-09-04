import copy
import threading

import tkinter as tk

from poe2bot.gui import dialogs as messagebox
from poe2bot.models import ConditionGroup, Step, replace_step_fields

# (StringVar attr, Entry attr, parser, allow_blank, Step field name, display label) for
# every numeric field on the Selected Step form -- drives _read_step_form's per-field
# validation below. Conditions (including what used to be a separate Cooldown/Buff
# Check) have their own, independent numeric fields -- see ConditionsMixin.
_NUMERIC_FIELDS = (
    ("step_delay_var", "step_delay_entry", int, False, "delay_ms", "Delay"),
    ("step_jitter_var", "step_jitter_entry", int, False, "jitter_ms", "Jitter"),
    ("step_hold_var", "step_hold_entry", int, False, "hold_ms", "Hold"),
    ("step_hold_jitter_var", "step_hold_jitter_entry", int, False, "hold_jitter_ms", "Hold Jitter"),
    ("step_repeat_var", "step_repeat_entry", int, False, "repeat_count", "Repeat"),
)


class StepEditorMixin:
    """The step Treeview and the "Selected Step" editing form: reading/writing
    it, the reset helpers that blank it for a fresh step (also called from
    RotationListMixin when switching rotations), Add/Update/Remove/Copy/
    Paste/Move, and capturing a controller button press directly into the
    Key field. Mixed into App (see poe2bot/gui/app.py).

    self.editing_steps is a heterogeneous list of Step | ConditionGroup (see
    poe2bot/models.py). Every tree row is addressed by a 3-tuple
    (group_idx, step_idx, cond_idx), each part either an int or None:
      (g, None, None)  -- a ConditionGroup's own header row (top-level index g)
      (None, s, None)  -- a top-level Step
      (None, s, c)     -- a top-level Step's c'th condition
      (g, s, None)     -- a Step nested inside group g
      (g, s, c)        -- that nested Step's c'th condition
    `_parse_tree_iid` is the single place that turns a tree iid string into
    this tuple; `_steps_list_for(group_idx)` turns `group_idx` into the
    actual list a `step_idx` indexes into (self.editing_steps, or that
    group's own .steps). See poe2bot/gui/condition_groups.py for the
    parallel per-group condition editor and creation flow, and
    poe2bot/gui/drag_drop.py for reordering/reparenting across these lists.
    """

    # ---- controller-button capture for the Key field -----------------------

    def _on_capture_step_key_clicked(self):
        self.capture_step_key_btn.config(text="Press a controller button...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_step_key_worker, daemon=True).start()

    def _capture_step_key_worker(self):
        key = self.hotkey_manager.capture_next_controller_button()
        self.status_queue.put(("__step_key_capture__", key))

    def _on_step_key_captured(self, key: str):
        # No display_name() indirection needed here, unlike the four rotation-
        # level captures -- the Key field is a plain editable Entry that
        # already shows raw text (e.g. "controller:a"), not a read-only label.
        self.step_key_var.set(key)
        self.capture_step_key_btn.config(text="Capture Controller Button")
        self._set_bind_buttons_enabled(True)

    # ---- mouse-button capture for the Key field -----------------------------

    def _on_capture_step_mouse_clicked(self):
        self.capture_step_mouse_btn.config(text="Click a mouse button...")
        self._set_bind_buttons_enabled(False)
        threading.Thread(target=self._capture_step_mouse_worker, daemon=True).start()

    def _capture_step_mouse_worker(self):
        key = self.hotkey_manager.capture_next_mouse_button()
        self.status_queue.put(("__step_mouse_capture__", key))

    def _on_step_mouse_captured(self, key: str):
        # Same raw-text Entry as _on_step_key_captured -- e.g. "mouse:left".
        self.step_key_var.set(key)
        self.capture_step_mouse_btn.config(text="Capture Mouse Button")
        self._set_bind_buttons_enabled(True)

    def _reset_step_core_fields(self):
        """Blank defaults for a step not yet filled in. Shared by every place
        that must guarantee the step-editing form isn't showing stale data
        left over from whatever step or rotation was previously open --
        _discard_selected_step_edits (an untouched selection), and
        _new_rotation/_load_rotation_into_form (switching rotations). Every
        .set() below is a programmatic reset, not a user edit -- wrapped in
        _autosave_suppressed() so it never misfires an autosave onto
        whatever step/condition/group happened to be selected before this
        ran (see AutosaveMixin, poe2bot/gui/autosave.py)."""
        with self._autosave_suppressed():
            self.step_name_var.set("Skill")
            self.step_key_var.set("")
            self.step_delay_var.set("20")
            self.step_jitter_var.set("10")
            self.step_hold_var.set("30")
            self.step_hold_jitter_var.set("10")
            self.step_repeat_var.set("1")
            self.step_repeat_combine_hold_var.set(False)
            self._populate_condition_form(None)
            self.conditions_section.set_collapsed(True)
            self._clear_form_errors()
            self._set_step_panels_visible(True)
            self._populate_group_condition_form(None)
            self._update_toggle_step_enabled_button()
        # Blanking the form always means nothing is meaningfully "selected" for
        # auto-commit/dirty-check purposes, even for call sites (_new_rotation,
        # _load_rotation_into_form) that never touch self.tree's own selection
        # and so would otherwise leave this pointing at a step from a rotation
        # that isn't even open anymore.
        self._selected_step_ref = None
        self._selected_group_ref = None

    def _clear_form_errors(self):
        """Resets every numeric Entry's invalid-state highlight and hides the
        shared error label -- called whenever the form is about to show a
        different step (or a blank one), so a discarded error left over from
        whichever step was previously loaded can never visually leak onto
        this one."""
        for _var_attr, entry_attr, _parser, _allow_blank, _key, _label in _NUMERIC_FIELDS:
            getattr(self, entry_attr).state(["!invalid"])
        self.step_form_error_var.set("")
        self.step_form_error_label.pack_forget()

    # ---- addressing: tree iid <-> (group_idx, step_idx, cond_idx) -------------

    def _steps_list_for(self, group_idx):
        """The actual list a `step_idx` indexes into: self.editing_steps
        (top-level) when `group_idx` is None, else that group's own nested
        .steps list."""
        return self.editing_steps if group_idx is None else self.editing_steps[group_idx].steps

    @staticmethod
    def _parse_tree_iid(iid: str):
        """Turns a tree iid string into a (group_idx, step_idx, cond_idx)
        location -- see this class's own docstring for the five shapes this
        recognizes -- or None if `iid` matches none of them."""
        parts = iid.split("-")
        if len(parts) == 2 and parts[0] == "group":
            return int(parts[1]), None, None
        if len(parts) == 2 and parts[0] == "step":
            return None, int(parts[1]), None
        if len(parts) == 4 and parts[0] == "step" and parts[2] == "cond":
            return None, int(parts[1]), int(parts[3])
        if len(parts) == 4 and parts[0] == "group" and parts[2] == "step":
            return int(parts[1]), int(parts[3]), None
        if len(parts) == 6 and parts[0] == "group" and parts[2] == "step" and parts[4] == "cond":
            return int(parts[1]), int(parts[3]), int(parts[5])
        return None

    @staticmethod
    def _location_iid(group_idx, step_idx, cond_idx=None) -> str:
        """The inverse of _parse_tree_iid -- builds the iid string for a
        given location, so callers that just computed/moved to a location
        (reselecting after a reorder, a paste, etc.) don't have to know the
        string format themselves."""
        if step_idx is None:
            return f"group-{group_idx}"
        base = f"step-{step_idx}" if group_idx is None else f"group-{group_idx}-step-{step_idx}"
        return base if cond_idx is None else f"{base}-cond-{cond_idx}"

    def _find_step_location(self, step) -> tuple:
        """(group_idx, step_idx) locating `step` (by identity) in
        self.editing_steps, wherever it currently lives -- top-level or
        nested inside a ConditionGroup -- or None if it's no longer present
        at all (e.g. removed since being selected)."""
        for i, entry in enumerate(self.editing_steps):
            if entry is step:
                return None, i
        for gi, entry in enumerate(self.editing_steps):
            if isinstance(entry, ConditionGroup):
                for si, s in enumerate(entry.steps):
                    if s is step:
                        return gi, si
        return None

    def _all_step_locations_in_order(self):
        """Every (group_idx, step_idx) Step location in self.editing_steps,
        in top-to-bottom tree order -- a top-level step or group by its own
        position, each group's nested steps immediately after it in their
        own order. Used to give a multi-selection spanning several groups
        (or a mix of top-level and nested) a stable, visually-sensible
        order for Copy."""
        locations = []
        for i, entry in enumerate(self.editing_steps):
            if isinstance(entry, ConditionGroup):
                locations.extend((i, si) for si in range(len(entry.steps)))
            else:
                locations.append((None, i))
        return locations

    def _current_group_scope(self):
        """Which group (if any) a newly-added step (Add Step/Add Sleep)
        should land in: the group currently in scope via the tree selection
        -- its own header row, or one of its nested steps/conditions -- or
        None for top-level. Multi-selection or no selection both mean
        top-level, same as today's "append to the end" default."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return None
        parsed = self._parse_tree_iid(selection[0])
        return parsed[0] if parsed is not None else None

    def _selected_owning_step_location(self):
        """(group_idx, step_idx) for whichever step is in scope for an
        action that attaches to a step regardless of whether the step
        itself or one of its conditions is selected (Add Condition) --
        rejects a group header row (step_idx is None), which has its own
        single condition edited via the separate Rotation Conditions
        section, not a list of per-step Conditions."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step (or one of its conditions) first.")
            return None
        if len(selection) > 1:
            messagebox.showinfo("Select one step", "Select exactly one step (or one of its conditions) for this action.")
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            messagebox.showinfo("Select a step", "Select a step (not a condition group) for this action.")
            return None
        return parsed[0], parsed[1]

    def _current_toggle_target(self):
        """(group_idx, step_idx) for whichever single step is in scope for
        the Disable/Enable Step button -- same resolution as
        _selected_owning_step_location (a step's own row or one of its
        conditions), but silent (None, no popup) when nothing/the wrong
        thing is selected, since this is also polled reactively on every
        selection change (see _update_toggle_step_enabled_button) rather
        than only in response to a click."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            return None
        return parsed[0], parsed[1]

    def _update_toggle_step_enabled_button(self):
        """Refreshes the Skill Steps section's Disable/Enable Step button to
        reflect whichever step is currently in scope -- labeled for the
        action it will perform, and disabled entirely when there's no
        single step to toggle (nothing selected, a condition group's own
        row, or a multi-selection)."""
        location = self._current_toggle_target()
        if location is None:
            self.toggle_step_enabled_btn.config(text="Disable Step", state="disabled")
            return
        group_idx, step_idx = location
        step = self._steps_list_for(group_idx)[step_idx]
        self.toggle_step_enabled_btn.config(
            text="Enable Step" if not step.enabled else "Disable Step", state="normal")

    def _on_toggle_step_enabled_clicked(self):
        location = self._current_toggle_target()
        if location is None:
            return  # the button is disabled in this state -- shouldn't be reachable
        group_idx, step_idx = location
        step = self._steps_list_for(group_idx)[step_idx]
        step.enabled = not step.enabled
        self._update_step_row((group_idx, step_idx))
        self._update_toggle_step_enabled_button()
        self._autosave()

    # ---- step editing ----------------------------------------------------

    def _refresh_steps_tree(self):
        # Steps/groups don't have stable ids of their own, only a position that
        # shifts on every add/remove/reorder -- so "was this row open?" is
        # tracked by object identity (stable across a reorder/move, since those
        # just relocate the same objects) rather than by index, otherwise a
        # manual collapse/expand would silently jump to whatever step/group
        # next lands at that same row number instead of following the one the
        # user actually toggled. The tree's current rows still reflect whatever
        # order self.editing_steps was in on the *previous* call (cached
        # below), not its current order -- e.g. right after a reorder, "step-0"
        # on screen right now is still the entry that used to be first, so
        # that previous order (not today's) is what must be used to look up
        # each row's current open/closed state.
        previously_open = set()
        previously_closed = set()
        for i, entry in enumerate(getattr(self, "_steps_tree_render_order", [])):
            iid = f"group-{i}" if isinstance(entry, ConditionGroup) else f"step-{i}"
            if self.tree.exists(iid):
                (previously_open if self.tree.item(iid, "open") else previously_closed).add(id(entry))

        def open_state(entry, default_open: bool) -> bool:
            if id(entry) in previously_closed:
                return False
            if id(entry) in previously_open:
                return True
            return default_open

        self.tree.delete(*self.tree.get_children())
        for i, entry in enumerate(self.editing_steps):
            if isinstance(entry, ConditionGroup):
                group_iid = f"group-{i}"
                self.tree.insert("", tk.END, iid=group_iid, text=self._condition_summary(entry.condition),
                                  values=("", "", "", "", "", ""), open=open_state(entry, bool(entry.steps)))
                for k, step in enumerate(entry.steps):
                    self._insert_step_row(group_iid, f"{group_iid}-step-{k}", step, open_state)
            else:
                self._insert_step_row("", f"step-{i}", entry, open_state)
        self._steps_tree_render_order = list(self.editing_steps)

    def _insert_step_row(self, parent_iid: str, step_iid: str, step, open_state):
        label, key_col = self._step_row_text_and_key(step)
        self.tree.insert(parent_iid, tk.END, iid=step_iid, text=label,
                          values=(key_col, step.delay_ms, step.jitter_ms, step.hold_ms,
                                  step.hold_jitter_ms, step.repeat_count),
                          open=open_state(step, bool(step.conditions)), tags=self._step_row_tags(step))
        for j, condition in enumerate(step.conditions):
            self.tree.insert(step_iid, tk.END, iid=f"{step_iid}-cond-{j}",
                              text=self._condition_summary(condition), values=("", "", "", "", "", ""))

    @staticmethod
    def _step_row_tags(step) -> tuple:
        """The Treeview tags for one step's own row -- just "step_disabled"
        (grays out the whole row, see app.py's tag_configure) while the
        step is disabled, otherwise none."""
        return () if step.enabled else ("step_disabled",)

    @staticmethod
    def _step_row_text_and_key(step) -> tuple:
        """(display label, Key column text) for one step's tree row --
        shared by _refresh_steps_tree (building every row) and
        _update_step_row (patching one row in place)."""
        if step.key is None:
            key_col, fallback_label = "(no key)", "No Key"
        elif step.key == "":
            key_col, fallback_label = "(sleep)", "Sleep"
        else:
            key_col, fallback_label = step.key, step.key
        return step.name or fallback_label, key_col

    def _update_step_row(self, location: tuple):
        """Patches one step's row in place (text + column values only)
        instead of the full delete-and-reinsert _refresh_steps_tree does --
        used after an auto-committed edit (_commit_previous_step_edits_if_changed)
        specifically because that full rebuild does not preserve the tree's
        selection, which would otherwise silently swallow whatever row the
        user was actually navigating to when the commit fired. Doesn't touch
        this step's condition child rows -- nothing about a numeric-field
        edit ever changes those."""
        group_idx, step_idx = location
        step = self._steps_list_for(group_idx)[step_idx]
        iid = self._location_iid(group_idx, step_idx)
        if not self.tree.exists(iid):
            return
        label, key_col = self._step_row_text_and_key(step)
        # Merges "step_disabled" in/out of whatever tags this row already has
        # (rather than replacing them outright) so an in-flight drag's
        # "drop_target" tag -- set directly via tree.item(iid, tags=...), see
        # DragDropMixin -- can never be silently wiped out by an autosave-
        # triggered field edit landing at the same moment.
        tags = (set(self.tree.item(iid, "tags")) - {"step_disabled"}) | set(self._step_row_tags(step))
        self.tree.item(iid, text=label, values=(
            key_col, step.delay_ms, step.jitter_ms, step.hold_ms, step.hold_jitter_ms, step.repeat_count),
            tags=tuple(tags))

    def _update_condition_row(self, group_idx, step_idx, cond_idx):
        """Patches one condition's row label in place -- same
        preserve-the-tree-selection rationale as _update_step_row, used by
        ConditionsMixin._apply_pending_condition_edits (called on every
        keystroke via AutosaveMixin._autosave, so a full _refresh_steps_tree
        rebuild here would drop the current selection after the very first
        keystroke)."""
        iid = self._location_iid(group_idx, step_idx, cond_idx)
        if not self.tree.exists(iid):
            return
        condition = self._steps_list_for(group_idx)[step_idx].conditions[cond_idx]
        self.tree.item(iid, text=self._condition_summary(condition))

    def _update_group_row(self, group_idx):
        """Patches a condition group's own header row label in place -- same
        rationale as _update_condition_row, used by
        ConditionGroupsMixin._apply_pending_group_edits."""
        iid = f"group-{group_idx}"
        if not self.tree.exists(iid):
            return
        self.tree.item(iid, text=self._condition_summary(self.editing_steps[group_idx].condition))

    _ACTION_SUMMARY_LABELS = {"fire": "Execute", "block": "Skip", "hold": "Override"}

    @classmethod
    def _condition_summary(cls, condition) -> str:
        action_label = cls._ACTION_SUMMARY_LABELS.get(condition.action, condition.action)
        not_marker = "NOT " if condition.negate else ""
        if condition.name:
            base = condition.name
        elif condition.match_type == "timer" and condition.timer_seconds:
            base = f"{condition.timer_seconds:g}s since last use"
        elif condition.match_type == "pixel" and condition.pixel_color:
            r, g, b = condition.pixel_color
            base = f"Pixel RGB({r},{g},{b})"
        elif condition.match_type == "image" and condition.region:
            w, h = condition.region[2], condition.region[3]
            if condition.search_mode == "area" and condition.search_region:
                sw, sh = condition.search_region[2], condition.search_region[3]
                base = f"Image {w}x{h} (searching {sw}x{sh} area)"
            else:
                base = f"Image {w}x{h}"
        else:
            base = "(not calibrated)"
        extra = ""
        if condition.action == "hold":
            parts = []
            if condition.hold_ms is not None:
                parts.append(f"hold {condition.hold_ms}ms")
            if condition.delay_ms is not None:
                parts.append(f"delay {condition.delay_ms}ms")
            if parts:
                extra = f" -> {', '.join(parts)}"
        elif condition.action == "fire" and condition.timeout_ms > 0:
            extra = f" (wait up to {condition.timeout_ms}ms)"
        return f"[{action_label}] {not_marker}{base}{extra}"

    def _on_select_step(self, _event):
        if not self._suppress_commit_on_select:
            self._commit_previous_step_edits_if_changed()
        # Everything below is the code populating the form from whatever's
        # newly selected, not the user typing/clicking -- suppressed so it
        # can never misfire an autosave onto the PREVIOUSLY selected step/
        # condition/group mid-populate (see AutosaveMixin, poe2bot/gui/autosave.py).
        with self._autosave_suppressed():
            selection = self.tree.selection()
            if not selection:
                self._selected_step_ref = None
                self._selected_group_ref = None
                self._populate_condition_form(None)
                self._set_step_panels_visible(True)
                self._populate_group_condition_form(None)
                self._update_toggle_step_enabled_button()
                return
            parsed = self._parse_tree_iid(selection[0])
            if parsed is None:
                return
            group_idx, step_idx, cond_idx = parsed
            if step_idx is None:
                # A condition group's own header row is selected -- it has no Key/
                # Delay/Hold/Repeat of its own, just the one condition gating it.
                group = self.editing_steps[group_idx]
                self._selected_step_ref = None
                self._selected_group_ref = group
                self._set_step_panels_visible(False)
                self._populate_group_condition_form(group)
                self._update_toggle_step_enabled_button()
                return
            self._selected_group_ref = None
            self._set_step_panels_visible(True)
            self._populate_group_condition_form(None)
            step = self._steps_list_for(group_idx)[step_idx]
            self._clear_form_errors()
            self._populate_condition_form(step.conditions[cond_idx] if cond_idx is not None else None)
            self.step_name_var.set(step.name)
            self.step_key_var.set(step.key or "")
            self.step_delay_var.set(str(step.delay_ms))
            self.step_jitter_var.set(str(step.jitter_ms))
            self.step_hold_var.set(str(step.hold_ms))
            self.step_hold_jitter_var.set(str(step.hold_jitter_ms))
            self.step_repeat_var.set(str(step.repeat_count))
            self.step_repeat_combine_hold_var.set(step.repeat_combine_hold)
            if cond_idx is not None:
                # A condition row is selected -- always show its editor, regardless of
                # this step's smart per-step default/override below, since the user is
                # clearly here to look at (or update) that specific condition.
                self.conditions_section.set_collapsed(False)
            else:
                self.conditions_section.set_collapsed(
                    self._section_collapse_state(step, "conditions", not step.conditions))
            self._selected_step_ref = step
            self._selected_step_core_snapshot = self._core_step_form_snapshot()
            self._update_toggle_step_enabled_button()

    def _section_collapse_state(self, step, key: str, default_collapsed: bool) -> bool:
        return self._section_collapse_overrides.get(id(step), {}).get(key, default_collapsed)

    def _on_section_toggled(self, key: str, collapsed: bool):
        """The on_toggle callback wired to each CollapsibleSection -- fires
        only on a user click, so it never overrides the smart per-step
        default in _on_select_step above except when the user actually
        chose to override it for this specific step."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[1] is None:
            return
        group_idx, step_idx, _cond_idx = parsed
        step = self._steps_list_for(group_idx)[step_idx]
        self._section_collapse_overrides.setdefault(id(step), {})[key] = collapsed

    def _commit_previous_step_edits_if_changed(self):
        """Writes the Selected Step form back onto whichever step it was
        loaded from (by identity, not index -- stable across reorders), if
        anything in it actually changed since _on_select_step last populated
        it. Called right before _on_select_step loads a *different* row, so
        navigating away from a step auto-saves an in-progress edit -- there
        is no separate "Update Selected" step anymore. An edit that doesn't
        parse is silently discarded rather than blocked, since this fires at
        a navigation point (the user has already moved on), not a keystroke,
        where a popup would be jarring."""
        step = self._selected_step_ref
        if step is None:
            return
        location = self._find_step_location(step)
        if location is None:
            return
        if self._core_step_form_snapshot() == self._selected_step_core_snapshot:
            return
        parsed = self._read_step_form(conditions=step.conditions, is_new_step=False,
                                       original_key=step.key, alt_key=step.alt_key, enabled=step.enabled)
        if parsed is None:
            self.status_var.set(f"Discarded an unsaved, invalid edit to '{step.name or step.key or 'a step'}'.")
            return
        replace_step_fields(step, parsed)
        self._update_step_row(location)

    def _core_step_form_snapshot(self) -> tuple:
        """A cheap, side-effect-free snapshot of the Name/Key/Delay/.../Repeat
        fields' raw values (deliberately raw strings, not parsed ints -- this
        must never risk tripping the per-field validation just from being
        computed). See _discard_selected_step_edits for how this is used."""
        return (
            self.step_name_var.get(), self.step_key_var.get(),
            self.step_delay_var.get(), self.step_jitter_var.get(),
            self.step_hold_var.get(), self.step_hold_jitter_var.get(),
            self.step_repeat_var.get(), self.step_repeat_combine_hold_var.get(),
        )

    def _read_step_form(self, conditions=None, is_new_step=True, original_key=None, alt_key=None, enabled=True):
        """Builds a Step from the form's current contents, or None if any
        numeric field fails to parse. Each field is validated independently
        (rather than one try/except around all of them), so a bad field gets
        its own red border via ttk's native "invalid" Entry state, and the
        shared error label names exactly which field(s) are wrong instead of
        a single generic message covering every possible field.

        `enabled` (like `conditions`/`alt_key`) has no field of its own on
        this form -- it's toggled separately via the Disable/Enable Step
        button -- so an existing step's current value must be passed through
        explicitly by the caller, or it would silently reset to the default
        (True) on every autosave-triggered rebuild of this step.

        `original_key` (the key the step being *replaced* already had;
        ignored when is_new_step) resolves what a blank Key field means,
        since the Entry shows the same blank text for both "not yet
        assigned" (None) and "a deliberate sleep step" (""). A brand new
        step (is_new_step, e.g. Add Step) left blank means "not yet
        assigned" (None). An *existing* step left blank means: still
        unassigned if it already was (original_key is None) -- editing other
        fields shouldn't accidentally turn an unassigned step into a sleep
        step -- otherwise a deliberate sleep step (""), preserving the
        documented "clear Key, navigate away" conversion for a step that had
        a real key before."""
        values = {}
        bad_labels = []
        for var_attr, entry_attr, parser, allow_blank, key, label in _NUMERIC_FIELDS:
            entry = getattr(self, entry_attr)
            text = getattr(self, var_attr).get().strip()
            if not text and allow_blank:
                values[key] = None
                entry.state(["!invalid"])
                continue
            try:
                values[key] = parser(text)
                entry.state(["!invalid"])
            except ValueError:
                entry.state(["invalid"])
                bad_labels.append(label)
        if bad_labels:
            self.step_form_error_var.set(f"Invalid: {', '.join(bad_labels)} -- must be whole numbers.")
            self.step_form_error_label.pack(anchor="w", pady=(4, 0))
            return None
        self.step_form_error_var.set("")
        self.step_form_error_label.pack_forget()

        key_text = self.step_key_var.get().strip()
        if key_text:
            key = key_text
        elif is_new_step:
            key = None   # a brand new step (Add Step) with no Key typed -- not yet assigned
        else:
            key = original_key if original_key is None else ""
        step = Step(
            key=key,
            alt_key=alt_key,
            name=self.step_name_var.get().strip(),
            delay_ms=values["delay_ms"],
            jitter_ms=values["jitter_ms"],
            hold_ms=values["hold_ms"],
            hold_jitter_ms=values["hold_jitter_ms"],
            repeat_count=values["repeat_count"],
            repeat_combine_hold=self.step_repeat_combine_hold_var.get(),
            enabled=enabled,
        )
        if conditions is not None:
            # The form has no fields of its own for conditions -- an existing
            # step's own conditions must be carried over explicitly, or they'd
            # silently be wiped out by this fresh Step (whose conditions default
            # to an empty list).
            step.conditions = conditions
        return step

    def _discard_selected_step_edits(self):
        """If a tree row is currently selected, Add Step/Add Sleep must not
        silently read whatever _on_select_step loaded into the form as if it
        were a fresh step -- that's how "Add Step" used to end up cloning
        whatever's highlighted. Only reset if the core fields are untouched
        since selecting -- if the user *did* edit them, that's left exactly
        as they left it, carried forward into the new step, rather than
        silently discarded. No-op entirely if nothing is selected, so typing
        values and clicking Add Step repeatedly to add several
        similarly-timed steps still works. (Conditions have no staged form
        state to discard here -- they live directly on Step objects, and
        clearing the selection below already blanks the condition editor via
        the normal _on_select_step path.)"""
        if not self.tree.selection():
            return
        # Clearing the selection fires <<TreeviewSelect>> like any other selection
        # change, which would otherwise make _on_select_step auto-commit the form
        # onto the step being navigated away from -- exactly what this method's own
        # docstring says must NOT happen (values the user touched are meant to carry
        # forward into a new step, not get written back onto the old one).
        self._suppress_commit_on_select = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._suppress_commit_on_select = False
        if self._core_step_form_snapshot() == self._selected_step_core_snapshot:
            self._reset_step_core_fields()

    def _add_step(self):
        scope = self._current_group_scope()
        self._discard_selected_step_edits()
        # No original_key -- this is a brand new step, so a blank Key field means
        # "not yet assigned" (None, skipped at runtime), never a sleep step. Use
        # Add Sleep for a deliberate keyless pause.
        step = self._read_step_form()
        if step is None:
            return
        self._steps_list_for(scope).append(step)
        self._refresh_steps_tree()
        self._autosave()

    def _add_sleep_step(self):
        """A sleep step has no key -- it's just a pause of delay_ms (+/- jitter_ms)
        with nothing pressed, for a deliberate wait that isn't tied to any skill.
        Reuses the same form fields as Add Step; whatever's in Key is ignored."""
        scope = self._current_group_scope()
        self._discard_selected_step_edits()
        step = self._read_step_form()
        if step is None:
            return
        step.key = ""
        self._steps_list_for(scope).append(step)
        self._refresh_steps_tree()
        self._autosave()

    def _on_copy_clicked(self):
        """Copies every currently-selected step (group header and
        condition-only selections are ignored -- neither is independently
        copy/pasteable) to an in-memory clipboard that lives on the App
        itself, so it survives switching to a different rotation -- that's
        what makes pasting into a different rotation than the one you
        copied from work. A selection spanning multiple groups (or a mix of
        top-level and nested steps) is copied in top-to-bottom tree order,
        not selection order."""
        # editing_steps' order/indices are unaffected by _apply_pending_step_edits
        # below (it patches one row's tree label in place, see _update_step_row --
        # it doesn't touch editing_steps' structure or the tree's selection), so
        # reading the selection now and using it after is safe either way.
        selection = self.tree.selection()
        selected_locations = {(g, s) for g, s, c in (self._parse_tree_iid(iid) for iid in selection)
                               if s is not None and c is None} if selection else set()
        # (the generator above never yields None -- selection is only ever real iids)
        if not selected_locations:
            messagebox.showinfo("No step selected", "Select at least one step to copy.")
            return
        # Applied here (like Paste/Move/drag-drop already do) so Copy can't
        # silently copy stale, pre-edit data if the form has an untouched change.
        if not self._apply_pending_step_edits():
            return
        ordered = [loc for loc in self._all_step_locations_in_order() if loc in selected_locations]
        self._step_clipboard = copy.deepcopy([self._steps_list_for(g)[s] for g, s in ordered])

    def _on_paste_clicked(self):
        if not self._step_clipboard:
            messagebox.showinfo("Clipboard is empty", "Copy a step first.")
            return
        selection = self.tree.selection()
        parsed = self._parse_tree_iid(selection[0]) if selection else None
        if not self._apply_pending_step_edits():
            return
        group_idx, step_idx = (parsed[0], parsed[1]) if parsed is not None else (None, None)
        target_list = self._steps_list_for(group_idx)
        # step_idx is None when nothing (or a group's own header row) is selected --
        # both mean "append to the end of whichever list is in scope" instead of
        # "insert right after a specific step," exactly like Add Step's own default.
        insert_at = step_idx + 1 if step_idx is not None else len(target_list)
        pasted = copy.deepcopy(self._step_clipboard)  # independent objects each time, so repeated pastes don't share state
        target_list[insert_at:insert_at] = pasted
        self._refresh_steps_tree()
        self.tree.selection_set(*(self._location_iid(group_idx, insert_at + offset) for offset in range(len(pasted))))
        self._autosave()

    def _remove_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            return
        parsed_list = [p for p in (self._parse_tree_iid(iid) for iid in selection) if p is not None]
        group_removals = {g for g, s, c in parsed_list if s is None}
        if group_removals:
            total_nested_steps = sum(len(self.editing_steps[g].steps) for g in group_removals)
            if total_nested_steps and not messagebox.askyesno(
                    "Remove Condition Group",
                    f"Remove {len(group_removals)} condition group(s) and the {total_nested_steps} "
                    f"step(s) nested inside them?", danger=True):
                return
        # Conditions/steps whose owner is itself also being fully removed are
        # skipped -- removing the group (or the step, for a condition) already
        # takes care of them, same idea one level deeper than before.
        step_deletions = {(g, s) for g, s, c in parsed_list if s is not None and c is None and g not in group_removals}
        condition_deletions = [(g, s, c) for g, s, c in parsed_list
                                if c is not None and (g, s) not in step_deletions and g not in group_removals]
        conditions_by_owner = {}
        for g, s, c in condition_deletions:
            conditions_by_owner.setdefault((g, s), []).append(c)
        for (g, s), cond_indices in conditions_by_owner.items():
            owning_conditions = self._steps_list_for(g)[s].conditions
            for c in sorted(cond_indices, reverse=True):
                del owning_conditions[c]
        # Nested-step deletions (from a group that's surviving) mutate that
        # group's own .steps list directly, which never shifts anything in
        # self.editing_steps itself -- safe to do in any order relative to the
        # combined top-level pass below.
        steps_by_owner = {}
        for g, s in step_deletions:
            steps_by_owner.setdefault(g, []).append(s)
        for g, indices in steps_by_owner.items():
            if g is None:
                continue  # top-level step removals are handled below, together with group removals
            owning_list = self._steps_list_for(g)
            for s in sorted(indices, reverse=True):
                del owning_list[s]
        # Top-level group removals and top-level step removals both index into
        # the SAME self.editing_steps list, so they must be combined into one
        # descending-index pass -- deleting them separately would shift indices
        # out from under whichever pass ran second.
        top_level_removals = group_removals | set(steps_by_owner.get(None, []))
        for i in sorted(top_level_removals, reverse=True):
            del self.editing_steps[i]
        self._refresh_steps_tree()
        self._autosave()

    def _move_step_up(self):
        self._move_selected(-1)

    def _move_step_down(self):
        self._move_selected(1)

    def _move_selected(self, direction: int):
        """Moves the selected row `direction` (-1 = up, 1 = down) within
        whichever list it belongs to: a condition moves within its own
        step's conditions; a step (top-level or nested) moves within its
        own owning list; a group header moves within the top-level list,
        exactly like a top-level step does."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        # Apply whatever's pending in the step form first so moving the step
        # doesn't silently discard an edit the autosave trace hasn't caught yet
        # (e.g. a field whose value is currently invalid mid-edit).
        if not self._apply_pending_step_edits():
            return
        group_idx, step_idx, cond_idx = parsed
        if cond_idx is not None:
            items, idx = self._steps_list_for(group_idx)[step_idx].conditions, cond_idx
            new_iid = lambda new_idx: self._location_iid(group_idx, step_idx, new_idx)  # noqa: E731
        elif step_idx is not None:
            items, idx = self._steps_list_for(group_idx), step_idx
            new_iid = lambda new_idx: self._location_iid(group_idx, new_idx)  # noqa: E731
        else:
            items, idx = self.editing_steps, group_idx
            new_iid = lambda new_idx: self._location_iid(new_idx, None)  # noqa: E731
        new_idx = idx + direction
        if not (0 <= new_idx < len(items)):
            return
        items[idx], items[new_idx] = items[new_idx], items[idx]
        self._refresh_steps_tree()
        self.tree.selection_set(new_iid(new_idx))
        self._autosave()
