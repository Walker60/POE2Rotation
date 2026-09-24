import copy

from poe2bot import storage, templates
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui.action_labels import STEP_CONDITION_ACTION_LABELS as CONDITION_ACTION_LABELS
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition, iter_conditions

log = get_logger()

# Human labels for Condition.action, and back -- shared by the Action combobox
# (app.py), _populate_condition_form, and _apply_pending_condition_edits below.
_CONDITION_ACTION_BY_LABEL = {label: action for action, label in CONDITION_ACTION_LABELS.items()}


class ConditionsMixin:
    """Per-step Conditions: add/update/recalibrate, plus tracking which
    calibrated template files are still referenced (so the periodic sweep
    doesn't delete ones still in use). A Condition is the single mechanism
    behind what used to be three separate concepts (Cooldown Check, Buff
    Check, plain Condition) -- see Condition.action in models.py. Mixed into
    App (see poe2bot/gui/app.py) -- Add/recalibrate Condition reuse
    CalibrationMixin's _start_image_capture/_start_pixel_capture via the
    on_use callback. See poe2bot/gui/condition_groups.py for the parallel
    rotation-level Condition Group concept (one condition gating a whole
    block of steps rather than one step's own conditions), and
    poe2bot/gui/skill_condition_groups.py for the parallel Skill Condition
    Group concept (several of one step's own conditions, combined via an
    All/Any rule) -- a Condition living inside one of THOSE is still added/
    edited/recalibrated through this same mixin, just addressed one level
    deeper (see StepEditorMixin's own docstring for the location tuple)."""

    def _selected_condition_location(self):
        """(group_path, step_idx, cond_idx, subcond_idx) if exactly one
        CONDITION row (never a Skill Condition Group's own header row -- it
        has no single match of its own) is currently selected, else None (a
        step's own row, a condition group's own row, a skill group's own
        header row, nothing, or a multi-selection). Used by the Add Image/
        Pixel/Timer Condition buttons to decide whether they're adding a
        brand new condition or recalibrating/replacing the one that's
        already selected -- see _resolve_condition_add_target."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return None
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[2] is None:
            return None
        group_path, step_idx, cond_idx, subcond_idx = parsed
        if subcond_idx is None and self._is_skill_group_entry(
                self._steps_list_for(group_path)[step_idx].conditions[cond_idx]):
            return None  # a Skill Condition Group's own header row -- no match of its own
        return parsed

    def _on_test_match_clicked(self):
        """Live "does it match right now" preview for whichever condition is
        currently selected -- see CalibrationMixin._show_test_match_result.
        Meaningless for a timer condition (there's no "since this step's
        last fire" to measure outside a running rotation), so that case
        gets a plain explanatory message instead."""
        location = self._selected_condition_location()
        if location is None:
            messagebox.showinfo("No condition selected", "Select a condition in the Skill Steps list first.")
            return
        group_path, step_idx, cond_idx, subcond_idx = location
        condition = self._condition_at(group_path, step_idx, cond_idx, subcond_idx)
        if condition.match_type == "timer":
            messagebox.showinfo(
                "Can't test a Timer condition",
                "A Timer condition matches based on seconds since this step's own last fire -- "
                "there's no rotation running right now to measure that against. Use Test Run "
                "and watch the Activity window instead.")
            return
        self._show_test_match_result(condition)

    def _on_add_image_condition_clicked(self):
        self._add_or_recalibrate_condition("image")

    def _on_add_pixel_condition_clicked(self):
        self._add_or_recalibrate_condition("pixel")

    def _resolve_condition_add_target(self):
        """(apply, old_condition) for whichever Add/recalibrate action is in
        scope: replace the selected Condition in place (leaf, top-level or
        nested -- old_condition is that Condition), append to the selected
        Skill Condition Group's own children (old_condition is None), or
        append to the owning step's own top-level .conditions (old_condition
        is None). (None, None) if nothing valid is in scope (a popup has
        already been shown by _selected_owning_step_location in that case).
        Shared by _add_or_recalibrate_condition/_on_add_timer_condition_clicked."""
        selected = self._selected_condition_location()
        if selected is not None:
            group_path, step_idx, cond_idx, subcond_idx = selected
            old_condition = self._condition_at(group_path, step_idx, cond_idx, subcond_idx)
            return self._replacing_condition_applier(
                group_path, step_idx, cond_idx, subcond_idx, old_condition), old_condition
        skill_group_location = self._selected_skill_group_location()
        if skill_group_location is not None:
            group_path, step_idx, cond_idx = skill_group_location
            return (lambda new_condition: self._add_condition_to_skill_group(
                group_path, step_idx, cond_idx, new_condition)), None
        location = self._selected_owning_step_location()
        if location is None:
            return None, None
        group_path, step_idx = location
        return (lambda new_condition: self._add_condition(group_path, step_idx, new_condition)), None

    def _add_or_recalibrate_condition(self, match_type: str):
        """Shared by "Add Image Condition..."/"Add Pixel Condition...": with
        a condition already selected, this recalibrates it in place as
        `match_type` -- switching type if that's not what it already was --
        carrying over its Name/Action/Negate/Timeout/Hold/Delay unchanged,
        exactly like double-clicking a condition row (_on_tree_double_click)
        except the new match type comes from whichever button was clicked
        rather than always matching what the condition already had, so this
        also doubles as how an existing condition gets converted from one
        match type to the other. With a Skill Condition Group's own row
        selected instead, this adds a brand new condition as ITS child. With
        nothing (or a step's own row, or a rotation-level condition group's
        row) selected, this instead adds a brand new condition to whichever
        step is in scope, exactly as before."""
        apply, old_condition = self._resolve_condition_add_target()
        if apply is None:
            return
        default_confidence = old_condition.confidence if old_condition is not None else 0.9

        if match_type == "pixel":
            self._start_pixel_capture(
                on_use=lambda point, color, confidence: apply(
                    Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence,
                              **self._calib_size_kwargs())),
                default_confidence=default_confidence)
        else:
            self._start_image_capture(
                on_use=lambda filename, region, confidence, search_mode, search_region: apply(
                    Condition(match_type="image", template=filename, region=region, confidence=confidence,
                              search_mode=search_mode, search_region=search_region, **self._calib_size_kwargs())),
                default_confidence=default_confidence)

    def _on_add_timer_condition_clicked(self):
        # No screen capture needed -- unlike image/pixel, the value is just
        # typed in directly, so this skips CalibrationMixin's overlay flow
        # entirely.
        apply, old_condition = self._resolve_condition_add_target()
        if apply is None:
            return
        title, initial = ("Edit Timer Condition", old_condition.timer_seconds or 5.0) if old_condition is not None \
            else ("Add Timer Condition", 5.0)
        seconds = messagebox.askfloat(
            title, "Minimum seconds since this step's last use:",
            initialvalue=initial, minvalue=0.1, parent=self)
        if seconds is None:
            return
        apply(Condition(match_type="timer", timer_seconds=seconds))

    def _replacing_condition_applier(self, group_path, step_idx: int, cond_idx: int, subcond_idx,
                                      old_condition: Condition):
        """Returns a function that carries old_condition's Name/Action/
        Negate/Timeout/Hold/Delay onto whichever new Condition it's called
        with, then replaces (group_path, step_idx, cond_idx, subcond_idx)
        with it, reselects that same row (the tree's own full-rebuild
        refresh below would otherwise leave it looking deselected), and
        autosaves. Shared by _resolve_condition_add_target (an existing
        condition selected) and _on_tree_double_click (below)."""
        def apply(new_condition: Condition):
            new_condition.name = old_condition.name
            new_condition.negate = old_condition.negate
            new_condition.action = old_condition.action
            new_condition.timeout_ms = old_condition.timeout_ms
            new_condition.hold_ms = old_condition.hold_ms
            new_condition.delay_ms = old_condition.delay_ms
            owner = (self._skill_group_children_for(group_path, step_idx, cond_idx) if subcond_idx is not None
                     else self._steps_list_for(group_path)[step_idx].conditions)
            owner[subcond_idx if subcond_idx is not None else cond_idx] = new_condition
            self._refresh_steps_tree()
            self.tree.selection_set(self._location_iid(group_path, step_idx, cond_idx, subcond_idx))
            self._populate_condition_form(new_condition, nested_in_skill_group=(subcond_idx is not None))
            self._autosave()
        return apply

    def _add_condition(self, group_path, step_idx: int, condition: Condition):
        """Appends `condition` (default action="fire", exactly today's plain
        gating behavior) and selects its new row, so the Action/Timeout/
        Hold/Delay editor is immediately showing it -- picking Block or
        Change Key Hold Amount, or a wait timeout, is a follow-up step now
        that those are no longer separate calibration flows of their own."""
        conditions = self._steps_list_for(group_path)[step_idx].conditions
        conditions.append(condition)
        self._refresh_steps_tree()
        self.tree.selection_set(self._location_iid(group_path, step_idx, len(conditions) - 1))
        self._autosave()

    def _add_condition_to_skill_group(self, group_path, step_idx: int, cond_idx: int, condition: Condition):
        """Appends `condition` to the Skill Condition Group AT (group_path,
        step_idx, cond_idx) -- the group-scoped counterpart to
        _add_condition, one level deeper."""
        children = self._skill_group_children_for(group_path, step_idx, cond_idx)
        children.append(condition)
        self._refresh_steps_tree()
        self.tree.selection_set(self._location_iid(group_path, step_idx, cond_idx, len(children) - 1))
        self._autosave()

    # ---- the per-condition editor (Name/Action/Negate/Timeout/Hold/Delay) ----

    def _populate_condition_form(self, condition, *, nested_in_skill_group: bool = False):
        """Fills the Conditions section's editor from `condition`, or blanks
        it to defaults if None (a step row, not a condition row, is
        selected, or nothing is). Shared by StepEditorMixin._on_select_step
        and this mixin's own add/update handlers, so the form always
        reflects exactly what's selected. `nested_in_skill_group` shows a
        one-line hint that this condition's own Action/Timeout/Hold/Delay
        are ignored (the owning Skill Condition Group's own apply instead)
        -- the fields themselves stay visible/editable rather than being
        hidden outright, to avoid restructuring this form for a case that's
        otherwise identical to a standalone condition's. Wrapped in
        _autosave_suppressed() -- this is code populating the form, not the
        user editing it, so it must never misfire an autosave onto whatever
        condition was previously loaded (see AutosaveMixin, poe2bot/gui/
        autosave.py)."""
        with self._autosave_suppressed():
            self._condition_form_nested = nested_in_skill_group
            if condition is None:
                self.condition_name_var.set("")
                self.condition_action_var.set(CONDITION_ACTION_LABELS["fire"])
                self.condition_negate_var.set(False)
                self.condition_timeout_var.set("0")
                self.condition_hold_var.set("")
                self.condition_delay_var.set("")
            else:
                self.condition_name_var.set(condition.name)
                self.condition_action_var.set(CONDITION_ACTION_LABELS.get(condition.action, CONDITION_ACTION_LABELS["fire"]))
                self.condition_negate_var.set(condition.negate)
                self.condition_timeout_var.set(str(condition.timeout_ms))
                self.condition_hold_var.set("" if condition.hold_ms is None else str(condition.hold_ms))
                self.condition_delay_var.set("" if condition.delay_ms is None else str(condition.delay_ms))
            self._refresh_condition_extra_visibility()

    def _refresh_condition_extra_visibility(self):
        """Shows only the field group relevant to the currently-selected
        Action -- Wait Timeout for "fire", Hold/Delay override for "hold",
        neither for "block" -- so the editor doesn't reintroduce the same
        always-everything-visible clutter this whole redesign was meant to
        remove. Also shows/hides the "this condition is nested in a Skill
        Condition Group" hint label set by _populate_condition_form."""
        action = _CONDITION_ACTION_BY_LABEL.get(self.condition_action_var.get(), "fire")
        self.condition_timeout_frame.pack_forget()
        self.condition_hold_frame.pack_forget()
        if action == "fire":
            self.condition_timeout_frame.pack(side="left")
        elif action == "hold":
            self.condition_hold_frame.pack(side="left")
        if getattr(self, "_condition_form_nested", False):
            self.condition_nested_hint_label.pack(anchor="w", pady=(4, 0))
        else:
            self.condition_nested_hint_label.pack_forget()

    def _on_condition_action_changed(self, _event=None):
        self._refresh_condition_extra_visibility()

    def _apply_pending_condition_edits(self) -> bool:
        """Applies the editor's Name/Action/Negate/Timeout/Hold/Delay fields
        onto whichever single condition is currently selected -- everything
        about a condition except its match itself (recalibrate via double-
        click for that, see _on_tree_double_click). The non-interactive core
        of what used to be the "Update Selected Condition" button, called on
        every field change by AutosaveMixin._autosave -- so unlike that
        button, nothing/the wrong thing being selected is just "nothing
        pending to apply" (return True), not something to interrupt anyone
        about, and a parse/range failure shows inline via
        condition_form_error_label instead of a popup, since this can now
        run mid-keystroke."""
        selection = self.tree.selection()
        if len(selection) != 1:
            return True
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None or parsed[2] is None:
            return True
        group_path, step_idx, cond_idx, subcond_idx = parsed
        if subcond_idx is None and self._is_skill_group_entry(
                self._steps_list_for(group_path)[step_idx].conditions[cond_idx]):
            return True  # a Skill Condition Group's own header row -- nothing here to apply
        action = _CONDITION_ACTION_BY_LABEL.get(self.condition_action_var.get(), "fire")
        try:
            timeout_ms = int(self.condition_timeout_var.get() or 0)
            hold_text = self.condition_hold_var.get().strip()
            delay_text = self.condition_delay_var.get().strip()
            hold_ms = int(hold_text) if hold_text else None
            delay_ms = int(delay_text) if delay_text else None
        except ValueError:
            self.condition_form_error_var.set(
                "Wait timeout, Hold override, and Delay override must be whole numbers "
                "(Hold/Delay may be left blank).")
            self.condition_form_error_label.pack(anchor="w", pady=(4, 0))
            return False
        if timeout_ms < 0 or (hold_ms is not None and hold_ms < 0) or (delay_ms is not None and delay_ms < 0):
            self.condition_form_error_var.set("Wait timeout, Hold override, and Delay override cannot be negative.")
            self.condition_form_error_label.pack(anchor="w", pady=(4, 0))
            return False
        self.condition_form_error_var.set("")
        self.condition_form_error_label.pack_forget()
        condition = self._condition_at(group_path, step_idx, cond_idx, subcond_idx)
        condition.name = self.condition_name_var.get().strip()
        condition.negate = self.condition_negate_var.get()
        condition.action = action
        condition.timeout_ms = timeout_ms
        condition.hold_ms = hold_ms
        condition.delay_ms = delay_ms
        self._update_condition_row(group_path, step_idx, cond_idx, subcond_idx)
        return True

    def _on_tree_double_click(self, _event):
        """Double-clicking a condition row recalibrates its match in place
        (same capture flow as adding one, but replacing rather than
        appending) -- everything else about it (name, action, negate,
        timeout, hold/delay overrides) carries over unchanged, since
        recalibrating is only meant to fix *what's being matched*, not
        *what happens when it matches*. Double-clicking a step row is a
        no-op -- steps are recalibrated via a condition's own row, there's
        no equivalent step-level check anymore. Double-clicking a condition
        GROUP's own row dispatches to its own recalibrate flow instead (see
        ConditionGroupsMixin._on_group_row_double_click) -- a group has a
        single condition living directly on it, not a list of child rows.
        Double-clicking a SKILL Condition Group's own row is also a no-op --
        it has no single match of its own either, just a list of child
        conditions, each recalibrated via its own row -- Tk's native
        double-click still toggles that row's expand/collapse afterward,
        exactly like a plain step row today."""
        selection = self.tree.selection()
        if not selection:
            return
        parsed = self._parse_tree_iid(selection[0])
        if parsed is None:
            return
        group_path, step_idx, cond_idx, subcond_idx = parsed
        if step_idx is None:
            self._on_group_row_double_click(group_path)
            return
        if cond_idx is None:
            return
        if subcond_idx is None and self._is_skill_group_entry(
                self._steps_list_for(group_path)[step_idx].conditions[cond_idx]):
            return
        condition = self._condition_at(group_path, step_idx, cond_idx, subcond_idx)
        replace = self._replacing_condition_applier(group_path, step_idx, cond_idx, subcond_idx, condition)

        if condition.match_type == "timer":
            seconds = messagebox.askfloat(
                "Edit Timer Condition", "Minimum seconds since this step's last use:",
                initialvalue=condition.timer_seconds, minvalue=0.1, parent=self)
            if seconds is None:
                return
            replace(Condition(match_type="timer", timer_seconds=seconds))
        elif condition.match_type == "pixel":
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

    # ---- copy/paste conditions between steps ---------------------------------

    def _on_copy_conditions_clicked(self):
        """Copies every condition belonging to whichever step is in scope
        (the step row itself or one of its condition rows -- same resolution
        StepEditorMixin's Add Condition buttons already use) into an
        in-memory clipboard that lives on the App, so it survives switching
        rotations (enables cross-rotation paste), mirroring
        StepEditorMixin._on_copy_clicked's whole-step clipboard."""
        location = self._selected_owning_step_location()
        if location is None:
            return
        group_path, step_idx = location
        conditions = self._steps_list_for(group_path)[step_idx].conditions
        if not conditions:
            messagebox.showinfo("No conditions to copy", "This step has no conditions to copy.")
            return
        self._condition_clipboard = copy.deepcopy(conditions)

    def _on_paste_conditions_clicked(self):
        """Appends a fresh copy of the clipboard's conditions onto every
        selected step (deduped from the selection the same way
        StepEditorMixin._on_copy_clicked collects multiple source steps) --
        each target gets its own independent Condition objects, so
        recalibrating one afterward never affects another. Purely additive:
        a target step's own existing conditions are left as they are."""
        if not self._condition_clipboard:
            messagebox.showinfo("Clipboard is empty", "Copy conditions from a step first.")
            return
        selection = self.tree.selection()
        step_locations = {(p[0], p[1]) for p in (self._parse_tree_iid(iid) for iid in selection)
                           if p is not None and p[1] is not None}
        if not step_locations:
            messagebox.showinfo("No step selected", "Select at least one step to paste conditions onto.")
            return
        for group_path, step_idx in step_locations:
            self._steps_list_for(group_path)[step_idx].conditions.extend(copy.deepcopy(self._condition_clipboard))
        self._refresh_steps_tree()
        self._autosave()

    # ---- template lifecycle ---------------------------------------------------

    def _referenced_templates(self) -> set:
        keep = set()
        for rotation in self.rotations.values():
            keep.update(c.template for c in iter_conditions(rotation.steps) if c.template)
        keep.update(c.template for c in iter_conditions(self.editing_steps) if c.template)
        # Whatever "Restore Last Deleted" could still bring back also counts as
        # referenced -- otherwise trashing a rotation, then merely opening the
        # app again (or editing an unrelated rotation) before restoring it,
        # would sweep away the templates it needs and leave it uncalibrated.
        keep.update(storage.trashed_rotation_templates())
        return keep

    def _sweep_templates(self):
        # A rotation file that currently fails to load contributes nothing to
        # _referenced_templates() (self.rotations only holds successfully-
        # loaded ones), so its calibration images would otherwise look
        # orphaned and get deleted -- abstain from the whole sweep rather
        # than risk destroying something a merely-temporarily-broken (not
        # actually abandoned) rotation still needs.
        if storage.has_unparseable_rotations():
            log.warning("Skipping template cleanup: at least one rotation file failed to load "
                        "(its templates might still be in use) -- fix or remove it, and cleanup "
                        "will resume normally.")
            return
        templates.sweep_unreferenced(self._referenced_templates())
