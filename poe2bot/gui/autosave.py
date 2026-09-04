import copy
import os
from contextlib import contextmanager

from poe2bot import storage
from poe2bot.hotkeys import display_name
from poe2bot.models import Rotation, validate_rotation


class AutosaveMixin:
    """Every field/structural change reaches disk immediately -- no Save/
    Update buttons anywhere. `_autosave_on_change(var)` wires a Tk variable
    so any change to it (typing, a Radiobutton/Combobox selection, a
    Checkbutton toggle) calls `_autosave()`, unless the change is happening
    while `_autosave_suppressed()` is active -- used by every place that
    *programmatically* repopulates a field from a Step/Condition/
    ConditionGroup/Rotation object (switching the selected step, loading a
    different rotation, blanking the form) rather than the user actually
    editing something, so that repopulating never misfires an autosave onto
    whatever was selected a moment ago. Mixed into App (see
    poe2bot/gui/app.py) -- freely calls into StepEditorMixin/
    ConditionsMixin/ConditionGroupsMixin/RotationListMixin/HotkeysMixin
    methods, the same cross-mixin pattern every other mixin here already
    uses."""

    @contextmanager
    def _autosave_suppressed(self):
        self._autosave_suppress_depth += 1
        try:
            yield
        finally:
            self._autosave_suppress_depth -= 1

    def _autosave_on_change(self, var):
        def on_write(*_args):
            if not self._autosave_suppress_depth:
                self._autosave()
        var.trace_add("write", on_write)

    def _autosave(self):
        """The single entry point every tracked field's trace calls, and
        every structural mutation (Add/Remove/Move/drag/paste/recalibrate)
        calls directly. Always attempts all three "apply the form onto
        whatever's selected" steps -- at most one is ever relevant to
        whatever's currently selected in the tree, the other two no-op via
        their own guard clauses -- then persists the whole rotation. An
        apply that fails (e.g. a currently-non-numeric Delay field, mid-
        edit) leaves editing_steps holding the last *valid* state of that
        object -- exactly what still gets persisted below, so the invalid
        text stays visible in its own widget with an inline error, but
        nothing invalid ever reaches disk."""
        self._apply_pending_step_edits()
        self._apply_pending_condition_edits()
        self._apply_pending_group_edits()
        self._persist_rotation_to_disk()

    def _persist_rotation_to_disk(self) -> bool:
        """Validates the rotation currently on screen and writes it to disk
        if valid -- the non-interactive core of what used to be the "Save
        Rotation" button (App._save_rotation). Any problem (invalid
        fields, a name collision, the trigger hotkey being the panic key)
        is shown inline via rotation_form_error_label instead of a blocking
        dialog, since this now runs on every keystroke -- a popup on every
        character typed would make editing unusable. The one genuinely
        interactive decision that used to live here (this hotkey is already
        bound elsewhere -- share it?) has moved to
        HotkeysMixin._confirm_hotkey_share_if_needed, resolved once at the
        moment a hotkey is actually bound, not re-asked here on every
        unrelated field change."""
        name = self.name_var.get().strip()
        try:
            pause_duration_ms = int(self.pause_duration_var.get())
            reset_delay_ms = int(self.reset_delay_var.get())
        except ValueError:
            self._show_rotation_form_error("Pause duration and reset delay must be whole numbers.")
            return False
        # Looked up early (not just later for the rename/move check) so the
        # alt_* fields below -- which have no editing UI of their own, only
        # ever touched by the Active Device toggle -- get preserved rather
        # than silently reset to None by this fresh Rotation(...) call.
        old_rotation = self.rotations.get(self.editing_original_name) if self.editing_original_name else None
        rotation = Rotation(
            name=name,
            mode=self.mode_var.get(),
            hotkey=self.pending_hotkey,
            alt_hotkey=old_rotation.alt_hotkey if old_rotation else None,
            cancel_key=self.pending_cancel_key,
            alt_cancel_key=old_rotation.alt_cancel_key if old_rotation else None,
            reset_key=self.pending_reset_key,
            alt_reset_key=old_rotation.alt_reset_key if old_rotation else None,
            reset_delay_ms=reset_delay_ms,
            pause_key=self.pending_pause_key,
            alt_pause_key=old_rotation.alt_pause_key if old_rotation else None,
            pause_mode=self.pause_mode_var.get(),
            pause_duration_ms=pause_duration_ms,
            folder=self.folder_var.get().strip(),
            steps=copy.deepcopy(self.editing_steps),
        )
        problems = validate_rotation(rotation)
        if name != self.editing_original_name and name in self.rotations:
            problems.append(f"A rotation named '{name}' already exists.")
        else:
            # Two different display names can still sanitize to the same filename
            # (storage._slugify folds case/punctuation, e.g. "Fire Ball" and
            # "Fire-Ball" both become fire_ball.json) -- saving would silently
            # overwrite whichever rotation got there first, with no warning, so
            # this is checked by comparing actual on-disk paths, not just names.
            new_path = os.path.normcase(os.path.normpath(storage.path_for(name, rotation.folder)))
            for other_name, other_rotation in self.rotations.items():
                if other_name == self.editing_original_name:
                    continue
                other_path = os.path.normcase(os.path.normpath(
                    storage.path_for(other_name, other_rotation.folder)))
                if other_path == new_path:
                    problems.append(
                        f"'{name}' would save to the same file as existing rotation '{other_name}' "
                        f"(both simplify to the same filename) -- choose a more distinct name.")
                    break
        if rotation.hotkey and rotation.hotkey == self.hotkey_manager.panic_key:
            problems.append(f"'{display_name(rotation.hotkey)}' is reserved as the panic/stop-all key.")
        if rotation.alt_hotkey and rotation.alt_hotkey == self.hotkey_manager.panic_key:
            problems.append(f"'{display_name(rotation.alt_hotkey)}' (alt) is reserved as the panic/stop-all key.")
        if problems:
            self._show_rotation_form_error(" ".join(problems))
            return False

        new_in_scope = self._folder_in_scope(rotation.folder)

        # Rebind hotkey first (release whatever this rotation held before, bind the new
        # choice) -- before any destructive rename/move step, so a conflict here (shouldn't
        # happen -- sharing was already confirmed at bind time, see
        # HotkeysMixin._confirm_hotkey_share_if_needed -- but kept as a defensive guard)
        # can't leave the old file deleted with nothing saved in its place. Skipped
        # entirely when this rotation isn't in the Active Folder's scope -- it has
        # nothing live to conflict with.
        if new_in_scope:
            try:
                self.hotkey_manager.rebind(rotation.hotkey, rotation.name)
            except ValueError as e:
                self._show_rotation_form_error(str(e))
                return False

        self._clear_rotation_form_error()

        # A rotation's file path depends on both its name and its folder, so either
        # changing means the old file needs to go, not just a plain rename.
        renamed = old_rotation is not None and old_rotation.name != rotation.name
        moved = old_rotation is not None and (renamed or old_rotation.folder != rotation.folder)
        if renamed:
            # Only a genuine rename needs the OLD name's live bindings released -- a
            # folder-only move keeps the same name, and the (possibly skipped) rebind
            # above already replaced whatever was live under it; clearing here too in
            # that case would immediately undo it, since old_rotation.name ==
            # rotation.name then.
            self._clear_rotation_hotkeys(old_rotation.name)
        if moved:
            # move_rotation writes the new file before removing the old one, so a
            # crash in between leaves a recoverable stray duplicate rather than
            # losing the rotation outright.
            storage.move_rotation(rotation, old_rotation.name, old_rotation.folder)
            self.rotation_manager.unload(old_rotation.name)
            del self.rotations[old_rotation.name]
        else:
            storage.save_rotation(rotation)

        self.rotation_manager.load(rotation)
        self.rotations[rotation.name] = rotation
        if new_in_scope:
            self.hotkey_manager.set_cancel_key(rotation.name, rotation.cancel_key)
            self.hotkey_manager.set_reset_key(rotation.name, rotation.reset_key)
            self.hotkey_manager.set_pause_key(rotation.name, rotation.pause_key)
        else:
            # Covers the case where this rotation had live bindings from before this
            # edit (e.g. it used to be in-scope) and is only now moving out of scope.
            self._clear_rotation_hotkeys(rotation.name)

        # No _load_rotation_into_form(rotation) here -- rotation was just built FROM
        # the live form/editing_steps, so there's nothing to reload; doing so would
        # also wipe the current tree selection and any mid-edit state on every single
        # keystroke (this runs that often now). Just keep the bookkeeping current.
        self.editing_original_name = rotation.name
        self._update_title()
        self._refresh_rotation_tree()
        return True

    def _show_rotation_form_error(self, message: str):
        self.rotation_form_error_var.set(message)
        self.rotation_form_error_label.pack(anchor="w", pady=(4, 0))

    def _clear_rotation_form_error(self):
        self.rotation_form_error_var.set("")
        self.rotation_form_error_label.pack_forget()
