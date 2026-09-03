import copy
import threading

import tkinter as tk

from poe2bot.gui import dialogs as messagebox
from poe2bot.models import Step, replace_step_fields

# (StringVar attr, Entry attr, parser, allow_blank, Step field name, display label) for
# every numeric field on the Selected Step form (including Cooldown/Buff Check's own
# numeric fields) -- driving both _read_step_form's per-field validation and the section
# auto-expand-on-error in _read_step_form below.
_NUMERIC_FIELDS = (
    ("step_delay_var", "step_delay_entry", int, False, "delay_ms", "Delay"),
    ("step_jitter_var", "step_jitter_entry", int, False, "jitter_ms", "Jitter"),
    ("step_hold_var", "step_hold_entry", int, False, "hold_ms", "Hold"),
    ("step_hold_jitter_var", "step_hold_jitter_entry", int, False, "hold_jitter_ms", "Hold Jitter"),
    ("step_repeat_var", "step_repeat_entry", int, False, "repeat_count", "Repeat"),
    ("step_ready_timeout_var", "step_ready_timeout_entry", int, False, "ready_timeout_ms", "Timeout"),
    ("step_ready_confidence_var", "step_ready_confidence_entry", float, False, "ready_confidence", "Confidence"),
    ("step_buff_hold_var", "step_buff_hold_entry", int, True, "buff_hold_ms", "Buff Hold"),
    ("step_buff_delay_var", "step_buff_delay_entry", int, True, "buff_delay_ms", "Buff Delay"),
)
# Which CollapsibleSection a field lives in, for auto-expanding it when that field is
# the one that failed to parse -- fields absent here live in the always-visible
# Selected Step group, which needs no expanding.
_SECTION_BY_FIELD = {
    "ready_timeout_ms": "cooldown", "ready_confidence": "cooldown",
    "buff_hold_ms": "buff", "buff_delay_ms": "buff",
}


class StepEditorMixin:
    """The step Treeview and the "Selected Step" editing form: reading/writing
    it, the reset helpers that blank it for a fresh step (also called from
    RotationListMixin when switching rotations), Add/Update/Remove/Copy/
    Paste/Move, and capturing a controller button press directly into the
    Key field. Mixed into App (see poe2bot/gui/app.py)."""

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
        _new_rotation/_load_rotation_into_form (switching rotations), which
        for the same reason also call _reset_ready_form/_reset_buff_form."""
        self.step_name_var.set("Skill")
        self.step_key_var.set("")
        self.step_delay_var.set("20")
        self.step_jitter_var.set("10")
        self.step_hold_var.set("30")
        self.step_hold_jitter_var.set("10")
        self.step_repeat_var.set("1")
        self.step_repeat_combine_hold_var.set(False)
        self.condition_name_var.set("")
        self.condition_negate_var.set(False)
        self.conditions_section.set_collapsed(True)
        self._clear_form_errors()
        # Blanking the form always means nothing is meaningfully "selected" for
        # auto-commit/dirty-check purposes, even for call sites (_new_rotation,
        # _load_rotation_into_form) that never touch self.tree's own selection
        # and so would otherwise leave this pointing at a step from a rotation
        # that isn't even open anymore.
        self._selected_step_ref = None

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

    def _reset_ready_form(self):
        self.step_ready_match_type = "image"
        self.step_ready_template = None
        self.step_ready_region = None
        self.step_ready_search_mode = "exact"
        self.step_ready_search_region = None
        self.step_ready_pixel_pos = None
        self.step_ready_pixel_color = None
        self.step_ready_timeout_var.set("0")
        self.step_ready_confidence_var.set("0.90")
        self._refresh_ready_status()
        self.cooldown_section.set_collapsed(True)

    def _refresh_ready_status(self):
        if self.step_ready_match_type == "pixel" and self.step_ready_pixel_color:
            r, g, b = self.step_ready_pixel_color
            self.step_ready_status_var.set(f"Pixel: RGB({r},{g},{b})")
        elif self.step_ready_match_type == "image" and self.step_ready_template and self.step_ready_region:
            w, h = self.step_ready_region[2], self.step_ready_region[3]
            if self.step_ready_search_mode == "area" and self.step_ready_search_region:
                sw, sh = self.step_ready_search_region[2], self.step_ready_search_region[3]
                self.step_ready_status_var.set(f"Image: {w}x{h} (searching {sw}x{sh} area)")
            else:
                self.step_ready_status_var.set(f"Image: {w}x{h}")
        else:
            self.step_ready_status_var.set("No cooldown check")

    def _reset_buff_form(self):
        self.step_buff_check = None
        self.step_buff_hold_var.set("")
        self.step_buff_delay_var.set("")
        self._refresh_buff_status()
        self.buff_section.set_collapsed(True)

    def _refresh_buff_status(self):
        self.step_buff_status_var.set(
            self._condition_summary(self.step_buff_check) if self.step_buff_check else "No buff check")

    # ---- step editing ----------------------------------------------------

    def _refresh_steps_tree(self):
        # Steps don't have stable ids of their own, only a position that shifts on
        # every add/remove/reorder -- so "was this step's row open?" is tracked by
        # the Step object's identity (stable across a reorder/move, since those
        # just relocate the same objects) rather than by index, otherwise a manual
        # collapse/expand would silently jump to whatever step next lands at that
        # same row number instead of following the step the user actually toggled.
        # The tree's current rows still reflect whatever order self.editing_steps
        # was in on the *previous* call (cached below), not its current order --
        # e.g. right after a reorder, "step-0" on screen right now is still the
        # step that used to be first, so that previous order (not today's) is
        # what must be used to look up each row's current open/closed state.
        previously_open = set()
        previously_closed = set()
        for i, step in enumerate(getattr(self, "_steps_tree_render_order", [])):
            iid = f"step-{i}"
            if self.tree.exists(iid):
                (previously_open if self.tree.item(iid, "open") else previously_closed).add(id(step))
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.editing_steps):
            label, key_col = self._step_row_text_and_key(step)
            step_iid = f"step-{i}"
            if id(step) in previously_closed:
                is_open = False
            elif id(step) in previously_open:
                is_open = True
            else:
                is_open = bool(step.conditions)  # a step never shown before -- today's default
            self.tree.insert("", tk.END, iid=step_iid, text=label,
                              values=(key_col, step.delay_ms,
                                      step.jitter_ms, step.hold_ms, step.hold_jitter_ms,
                                      step.repeat_count),
                              open=is_open)
            for j, condition in enumerate(step.conditions):
                self.tree.insert(step_iid, tk.END, iid=f"{step_iid}-cond-{j}",
                                  text=self._condition_summary(condition),
                                  values=("", "", "", "", "", ""))
        self._steps_tree_render_order = list(self.editing_steps)

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

    def _update_step_row(self, step_idx: int):
        """Patches one step's row in place (text + column values only)
        instead of the full delete-and-reinsert _refresh_steps_tree does --
        used after an auto-committed edit (_commit_previous_step_edits_if_changed)
        specifically because that full rebuild does not preserve the tree's
        selection, which would otherwise silently swallow whatever row the
        user was actually navigating to when the commit fired. Doesn't touch
        this step's condition child rows -- nothing about a numeric-field
        edit ever changes those."""
        step = self.editing_steps[step_idx]
        iid = f"step-{step_idx}"
        if not self.tree.exists(iid):
            return
        label, key_col = self._step_row_text_and_key(step)
        self.tree.item(iid, text=label, values=(
            key_col, step.delay_ms, step.jitter_ms, step.hold_ms, step.hold_jitter_ms, step.repeat_count))

    @staticmethod
    def _condition_summary(condition) -> str:
        not_marker = "NOT " if condition.negate else ""
        if condition.name:
            return f"Condition: {not_marker}{condition.name}"
        if condition.match_type == "timer" and condition.timer_seconds:
            return f"Condition: {not_marker}{condition.timer_seconds:g}s since last use"
        if condition.match_type == "pixel" and condition.pixel_color:
            r, g, b = condition.pixel_color
            return f"Condition: {not_marker}Pixel RGB({r},{g},{b})"
        if condition.match_type == "image" and condition.region:
            w, h = condition.region[2], condition.region[3]
            if condition.search_mode == "area" and condition.search_region:
                sw, sh = condition.search_region[2], condition.search_region[3]
                return f"Condition: {not_marker}Image {w}x{h} (searching {sw}x{sh} area)"
            return f"Condition: {not_marker}Image {w}x{h}"
        return f"Condition: {not_marker}(not calibrated)"

    @staticmethod
    def _parse_tree_iid(iid: str):
        """("step-N", None) for a step row's index, (N, M) for the M'th
        condition of step N, or None if `iid` doesn't match either scheme."""
        parts = iid.split("-")
        if len(parts) == 2 and parts[0] == "step":
            return int(parts[1]), None
        if len(parts) == 4 and parts[0] == "step" and parts[2] == "cond":
            return int(parts[1]), int(parts[3])
        return None

    def _selected_owning_step_index(self):
        """For actions that attach to a step regardless of whether the step
        itself or one of its conditions is selected (Add Condition)."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No step selected", "Select a step (or one of its conditions) first.")
            return None
        if len(selection) > 1:
            messagebox.showinfo("Select one step", "Select exactly one step (or one of its conditions) for this action.")
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return None
        return parsed[0]

    def _on_select_step(self, _event):
        if not self._suppress_commit_on_select:
            self._commit_previous_step_edits_if_changed()
        selection = self.tree.selection()
        if not selection:
            self._selected_step_ref = None
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        step = self.editing_steps[parsed[0]]
        self._clear_form_errors()
        self.condition_name_var.set(step.conditions[parsed[1]].name if parsed[1] is not None else "")
        self.condition_negate_var.set(step.conditions[parsed[1]].negate if parsed[1] is not None else False)
        self.step_name_var.set(step.name)
        self.step_key_var.set(step.key or "")
        self.step_delay_var.set(str(step.delay_ms))
        self.step_jitter_var.set(str(step.jitter_ms))
        self.step_hold_var.set(str(step.hold_ms))
        self.step_hold_jitter_var.set(str(step.hold_jitter_ms))
        self.step_repeat_var.set(str(step.repeat_count))
        self.step_repeat_combine_hold_var.set(step.repeat_combine_hold)
        self.step_ready_match_type = step.ready_match_type
        self.step_ready_template = step.ready_template
        self.step_ready_region = step.ready_region
        self.step_ready_search_mode = step.ready_search_mode
        self.step_ready_search_region = step.ready_search_region
        self.step_ready_pixel_pos = step.ready_pixel_pos
        self.step_ready_pixel_color = step.ready_pixel_color
        self.step_ready_timeout_var.set(str(step.ready_timeout_ms))
        self.step_ready_confidence_var.set(f"{step.ready_confidence:.2f}")
        self._refresh_ready_status()
        self.step_buff_check = step.buff_check
        self.step_buff_hold_var.set("" if step.buff_hold_ms is None else str(step.buff_hold_ms))
        self.step_buff_delay_var.set("" if step.buff_delay_ms is None else str(step.buff_delay_ms))
        self._refresh_buff_status()
        self.cooldown_section.set_collapsed(
            self._section_collapse_state(step, "cooldown", not step.has_ready_check()))
        self.buff_section.set_collapsed(
            self._section_collapse_state(step, "buff", step.buff_check is None))
        self.conditions_section.set_collapsed(
            self._section_collapse_state(step, "conditions", not step.conditions))
        self._selected_step_ref = step
        self._selected_step_core_snapshot = self._core_step_form_snapshot()
        self._selected_step_ready_snapshot = self._ready_check_form_snapshot()
        self._selected_step_buff_snapshot = self._buff_check_form_snapshot()

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
        if parsed is None:
            return
        step = self.editing_steps[parsed[0]]
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
        if step is None or not any(s is step for s in self.editing_steps):
            return
        if (self._core_step_form_snapshot() == self._selected_step_core_snapshot
                and self._ready_check_form_snapshot() == self._selected_step_ready_snapshot
                and self._buff_check_form_snapshot() == self._selected_step_buff_snapshot):
            return
        parsed = self._read_step_form(conditions=step.conditions, is_new_step=False,
                                       original_key=step.key, alt_key=step.alt_key)
        if parsed is None:
            self.status_var.set(f"Discarded an unsaved, invalid edit to '{step.name or step.key or 'a step'}'.")
            return
        replace_step_fields(step, parsed)
        # _update_step_row, NOT _refresh_steps_tree -- this runs from _on_select_step,
        # partway through processing a selection change (the row the user is
        # navigating TO). _refresh_steps_tree's full delete-and-reinsert clears the
        # tree's selection as a side effect, which would silently swallow that
        # navigation; patching this one row's displayed values in place does not.
        self._update_step_row(next(i for i, s in enumerate(self.editing_steps) if s is step))

    def _core_step_form_snapshot(self) -> tuple:
        """A cheap, side-effect-free snapshot of the Name/Key/Delay/.../Repeat
        fields' raw values (deliberately raw strings, not parsed ints -- this
        must never risk popping the "Invalid step" dialog just from being
        computed). See _discard_selected_step_edits for how this, plus the
        ready-check/buff-check snapshots below, are used."""
        return (
            self.step_name_var.get(), self.step_key_var.get(),
            self.step_delay_var.get(), self.step_jitter_var.get(),
            self.step_hold_var.get(), self.step_hold_jitter_var.get(),
            self.step_repeat_var.get(), self.step_repeat_combine_hold_var.get(),
        )

    def _ready_check_form_snapshot(self) -> tuple:
        """Same idea as _core_step_form_snapshot, for the Cooldown Check group."""
        return (
            self.step_ready_match_type, self.step_ready_template, self.step_ready_region,
            self.step_ready_search_mode, self.step_ready_search_region,
            self.step_ready_pixel_pos, self.step_ready_pixel_color,
            self.step_ready_timeout_var.get(), self.step_ready_confidence_var.get(),
        )

    def _buff_check_form_snapshot(self) -> tuple:
        """Same idea as _core_step_form_snapshot, for the Buff Check group."""
        return self.step_buff_check, self.step_buff_hold_var.get(), self.step_buff_delay_var.get()

    def _read_step_form(self, conditions=None, is_new_step=True, original_key=None, alt_key=None):
        """Builds a Step from the form's current contents, or None if any
        numeric field fails to parse. Each field is validated independently
        (rather than one try/except around all of them), so a bad field gets
        its own red border via ttk's native "invalid" Entry state, and the
        shared error label names exactly which field(s) are wrong instead of
        a single generic message covering every possible field. A bad field
        that lives inside a currently-collapsed Cooldown/Buff Check section
        also expands that section, so the red border is actually visible.

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
        bad_sections = set()
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
                if key in _SECTION_BY_FIELD:
                    bad_sections.add(_SECTION_BY_FIELD[key])
        if bad_labels:
            self.step_form_error_var.set(
                f"Invalid: {', '.join(bad_labels)} -- Confidence needs a decimal (e.g. 0.9), "
                "the rest need whole numbers.")
            self.step_form_error_label.pack(anchor="w", pady=(4, 0))
            if "cooldown" in bad_sections:
                self.cooldown_section.set_collapsed(False)
            if "buff" in bad_sections:
                self.buff_section.set_collapsed(False)
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
            ready_match_type=self.step_ready_match_type,
            ready_template=self.step_ready_template,
            ready_region=self.step_ready_region,
            ready_search_mode=self.step_ready_search_mode,
            ready_search_region=self.step_ready_search_region,
            ready_pixel_pos=self.step_ready_pixel_pos,
            ready_pixel_color=self.step_ready_pixel_color,
            ready_confidence=values["ready_confidence"],
            ready_timeout_ms=values["ready_timeout_ms"],
            buff_check=self.step_buff_check,
            buff_hold_ms=values["buff_hold_ms"],
            buff_delay_ms=values["buff_delay_ms"],
            repeat_count=values["repeat_count"],
            repeat_combine_hold=self.step_repeat_combine_hold_var.get(),
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
        whatever's highlighted. Checked independently per section (core
        fields / Cooldown Check / Buff Check), not as one all-or-nothing
        snapshot: changing just the Key to build a similar-but-new step must
        still discard a stale, untouched Cooldown Check from the step that
        was selected, rather than silently carrying someone else's calibrated
        icon over onto what's meant to be an unrelated new step. Any section
        the user *did* touch since selecting (recalibrated a check, edited a
        core field, etc.) is left exactly as they left it -- only untouched
        sections reset to blank. No-op entirely if nothing is selected, so
        typing values and clicking Add Step repeatedly to add several
        similarly-timed steps still works."""
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
        if self._ready_check_form_snapshot() == self._selected_step_ready_snapshot:
            self._reset_ready_form()
        if self._buff_check_form_snapshot() == self._selected_step_buff_snapshot:
            self._reset_buff_form()

    def _add_step(self):
        self._discard_selected_step_edits()
        # No original_key -- this is a brand new step, so a blank Key field means
        # "not yet assigned" (None, skipped at runtime), never a sleep step. Use
        # Add Sleep for a deliberate keyless pause.
        step = self._read_step_form()
        if step is None:
            return
        self.editing_steps.append(step)
        self._refresh_steps_tree()
        self._reset_ready_form()
        self._reset_buff_form()

    def _add_sleep_step(self):
        """A sleep step has no key -- it's just a pause of delay_ms (+/- jitter_ms)
        with nothing pressed, for a deliberate wait that isn't tied to any skill.
        Reuses the same form fields as Add Step; whatever's in Key is ignored."""
        self._discard_selected_step_edits()
        step = self._read_step_form()
        if step is None:
            return
        step.key = ""
        self.editing_steps.append(step)
        self._refresh_steps_tree()
        self._reset_ready_form()
        self._reset_buff_form()

    def _on_copy_clicked(self):
        """Copies every currently-selected step (condition-only selections are
        ignored -- conditions aren't independently copy/pasteable) to an
        in-memory clipboard that lives on the App itself, so it survives
        switching to a different rotation -- that's what makes pasting into a
        different rotation than the one you copied from work."""
        # Captured before _apply_pending_step_edits, which -- if it actually applies
        # an edit -- calls _refresh_steps_tree and clears the tree's selection (see
        # _on_paste_clicked below for the same reason); editing_steps' order/indices
        # are unaffected by the apply, so reading them now and using them after is safe.
        selection = self.tree.selection()
        step_indices = sorted({p[0] for p in (self._parse_tree_iid(iid) for iid in selection)
                                if p is not None and p[1] is None})
        if not step_indices:
            messagebox.showinfo("No step selected", "Select at least one step to copy.")
            return
        # Applied here (like Paste/Move/Save/drag-drop already do) so Copy can't
        # silently copy stale, pre-edit data if the form has an untouched change.
        if not self._apply_pending_step_edits():
            return
        self._step_clipboard = copy.deepcopy([self.editing_steps[i] for i in step_indices])

    def _on_paste_clicked(self):
        if not self._step_clipboard:
            messagebox.showinfo("Clipboard is empty", "Copy a step first.")
            return
        # Captured before _apply_pending_step_edits, which -- if it actually applies
        # an edit -- calls _refresh_steps_tree and clears the tree's selection, so
        # re-reading it afterward would silently paste at the end instead of after
        # whatever was selected.
        selection = self.tree.selection()
        parsed = self._parse_tree_iid(selection[0]) if selection else None
        if not self._apply_pending_step_edits():
            return
        insert_at = parsed[0] + 1 if parsed is not None else len(self.editing_steps)
        pasted = copy.deepcopy(self._step_clipboard)  # independent objects each time, so repeated pastes don't share state
        self.editing_steps[insert_at:insert_at] = pasted
        self._refresh_steps_tree()
        self.tree.selection_set(*(f"step-{insert_at + offset}" for offset in range(len(pasted))))

    def _remove_selected_step(self):
        selection = self.tree.selection()
        if not selection:
            return
        parsed_list = [p for p in (self._parse_tree_iid(iid) for iid in selection) if p is not None]
        step_indices = {s for s, c in parsed_list if c is None}
        # Conditions belonging to a step that's also being fully removed are
        # already handled by removing that step -- skip them to avoid deleting
        # from a conditions list that's about to disappear anyway.
        condition_deletions = [(s, c) for s, c in parsed_list if c is not None and s not in step_indices]
        conditions_by_step = {}
        for s, c in condition_deletions:
            conditions_by_step.setdefault(s, []).append(c)
        for s, cond_indices in conditions_by_step.items():
            for c in sorted(cond_indices, reverse=True):
                del self.editing_steps[s].conditions[c]
        for s in sorted(step_indices, reverse=True):
            del self.editing_steps[s]
        self._refresh_steps_tree()

    def _move_step_up(self):
        """Moves the selected step, or -- if a condition is selected instead --
        that condition up within its own step's conditions list."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        # Apply whatever's pending in the step form first (e.g. an edit the user
        # was about to click Update Selected for) so moving the step doesn't
        # silently discard it -- same protection Save Rotation already has.
        if not self._apply_pending_step_edits():
            return
        step_idx, cond_idx = parsed
        if cond_idx is None:
            if step_idx == 0:
                return
            self.editing_steps[step_idx - 1], self.editing_steps[step_idx] = \
                self.editing_steps[step_idx], self.editing_steps[step_idx - 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx - 1}")
        else:
            if cond_idx == 0:
                return
            conditions = self.editing_steps[step_idx].conditions
            conditions[cond_idx - 1], conditions[cond_idx] = conditions[cond_idx], conditions[cond_idx - 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx - 1}")

    def _move_step_down(self):
        """Moves the selected step, or -- if a condition is selected instead --
        that condition down within its own step's conditions list."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        # See _move_step_up: apply any pending form edit before moving, so it
        # isn't silently discarded.
        if not self._apply_pending_step_edits():
            return
        step_idx, cond_idx = parsed
        if cond_idx is None:
            if step_idx >= len(self.editing_steps) - 1:
                return
            self.editing_steps[step_idx + 1], self.editing_steps[step_idx] = \
                self.editing_steps[step_idx], self.editing_steps[step_idx + 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx + 1}")
        else:
            conditions = self.editing_steps[step_idx].conditions
            if cond_idx >= len(conditions) - 1:
                return
            conditions[cond_idx + 1], conditions[cond_idx] = conditions[cond_idx], conditions[cond_idx + 1]
            self._refresh_steps_tree()
            self.tree.selection_set(f"step-{step_idx}-cond-{cond_idx + 1}")
