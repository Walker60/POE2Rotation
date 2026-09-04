import copy
import os

import tkinter as tk

from poe2bot import app_state, storage
from poe2bot.hotkeys import display_name
from poe2bot.models import Rotation, folder_path_problem, folder_in_scope
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui.constants import STATUS_LABELS


class RotationListMixin:
    """The left-hand rotation list/folder tree, and loading/creating/copying/
    deleting a rotation into the step-editing form. Mixed into App (see
    poe2bot/gui/app.py) -- methods here freely reference self.xxx attributes
    defined in App.__init__ or other mixins (e.g. self.rotation_tree,
    self.rotations, self._refresh_steps_tree from StepEditorMixin), which is
    the whole point of splitting a big class across files this way."""

    def _refresh_rotation_tree(self):
        selected = self.editing_original_name
        previously_open = {
            path for path, item_id in self._folder_nodes.items()
            if self.rotation_tree.exists(item_id) and self.rotation_tree.item(item_id, "open")
        }
        self.rotation_tree.delete(*self.rotation_tree.get_children())
        self._folder_nodes = {}

        filter_text = self.rotation_filter_var.get().strip().lower()
        matching_names = {name for name in self.rotations if not filter_text or filter_text in name.lower()}

        def ensure_folder_node(folder_path: str) -> str:
            """Return the tree item id for folder_path (the root tree item id,
            "", for an ungrouped rotation), creating it -- and any missing
            parent folders -- on first use."""
            if not folder_path:
                return ""
            if folder_path in self._folder_nodes:
                return self._folder_nodes[folder_path]
            parent_path, _, label = folder_path.rpartition("/")
            parent_id = ensure_folder_node(parent_path)
            # While filtering, force every visible folder open (it must contain
            # a match, or it wouldn't be inserted below) rather than respecting
            # its remembered state, so a match is never hidden behind a
            # collapsed folder.
            is_open = folder_path in previously_open or bool(filter_text)
            item_id = self.rotation_tree.insert(
                parent_id, tk.END, iid=f"folder:{folder_path}", text=label, open=is_open)
            self._folder_nodes[folder_path] = item_id
            return item_id

        # Folders first, then ungrouped rotations, each group alphabetical -- avoids
        # ungrouped rotations (folder == "") sorting before every folder name.
        for name in sorted(self.rotations, key=lambda n: (
                self.rotations[n].folder == "", self.rotations[n].folder.lower(), n.lower())):
            if name not in matching_names:
                continue
            rotation = self.rotations[name]
            parent_id = ensure_folder_node(rotation.folder)
            shared_suffix = ""
            if rotation.hotkey and len(self.hotkey_manager.bound_to(rotation.hotkey)) > 1:
                shared_suffix = f" (shared {display_name(rotation.hotkey)})"
            status = self.rotation_manager.status(name)
            status_text = STATUS_LABELS.get(status, "").strip(" ()").capitalize()
            self.rotation_tree.insert(
                parent_id, tk.END, iid=f"rotation:{name}", text=f"{name}{shared_suffix}",
                values=(status_text,), tags=(status,))

        # Keeps the Active Folder combobox's options current with whatever folders
        # actually exist -- called after every rotation-set mutation (add/delete/
        # move/rename/copy), so this is the one place that needs to stay in sync.
        self.active_folder_combo["values"] = ("(All Folders)",) + tuple(self._known_folder_prefixes())

        if selected:
            item_id = f"rotation:{selected}"
            if self.rotation_tree.exists(item_id):
                self.rotation_tree.see(item_id)
                self.rotation_tree.selection_set(item_id)

    def _selected_rotation_name(self):
        """Name of the single currently-selected rotation, or None if nothing,
        a folder, or more than one item is selected."""
        selection = self.rotation_tree.selection()
        if len(selection) != 1 or not selection[0].startswith("rotation:"):
            return None
        return selection[0][len("rotation:"):]

    def _selected_rotation_names(self) -> list:
        """Names of every currently-selected rotation (folders in the
        selection are ignored), for actions that support multi-select."""
        return [item_id[len("rotation:"):] for item_id in self.rotation_tree.selection()
                if item_id.startswith("rotation:")]

    def _on_select_rotation(self, _event):
        name = self._selected_rotation_name()
        # The `!= self.editing_original_name` guard also matters for autosave:
        # AutosaveMixin._persist_rotation_to_disk re-selects the current rotation
        # at the end of every _refresh_rotation_tree() call it makes (which runs
        # on every keystroke), and selection_set() re-fires <<TreeviewSelect>> --
        # without this guard, that would reload the whole form (wiping the
        # current tree selection/mid-edit state) on every single keystroke.
        if name is not None and name in self.rotations and name != self.editing_original_name:
            self._load_rotation_into_form(self.rotations[name])

    def _on_rotation_tree_right_click(self, event):
        item_id = self.rotation_tree.identify_row(event.y)
        if not item_id:
            return
        if item_id.startswith("folder:"):
            self.rotation_tree.selection_set(item_id)
            folder_path = item_id[len("folder:"):]
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Rename Folder...", command=lambda: self._rename_folder(folder_path))
            menu.tk_popup(event.x_root, event.y_root)
        elif item_id.startswith("rotation:"):
            if item_id not in self.rotation_tree.selection():
                self.rotation_tree.selection_set(item_id)
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Move to Folder...", command=self._move_selected_to_folder)
            menu.tk_popup(event.x_root, event.y_root)

    def _folder_move_collisions(self, planned_folders: dict) -> list:
        """planned_folders: rotation name -> new folder, for a batch of
        rotations about to move/rename. Returns a list of (name_a, name_b)
        pairs that would resolve to the same on-disk file once these changes
        are applied -- checked against every currently-known rotation
        (substituting in each mover's *new* folder), so this catches both
        "two rotations being moved together collide with each other" and
        "a moved rotation collides with one that's staying put" -- the same
        kind of check AutosaveMixin._persist_rotation_to_disk already does for
        a single save."""
        paths = {}
        collisions = []
        for name, rotation in self.rotations.items():
            folder = planned_folders.get(name, rotation.folder)
            path = os.path.normcase(os.path.normpath(storage.path_for(name, folder)))
            if path in paths:
                collisions.append((paths[path], name))
            else:
                paths[path] = name
        return collisions

    def _rename_folder(self, folder_path: str):
        """Renames/moves folder_path to a new path, taking every rotation in it
        (and any nested subfolders) along -- a bulk operation, unlike editing a
        single rotation's Folder field one at a time."""
        new_path = messagebox.askstring(
            "Rename Folder", "New folder path:", initialvalue=folder_path, parent=self)
        if new_path is None:
            return
        new_path = new_path.strip().strip("/")
        problem = folder_path_problem(new_path)
        if problem:
            messagebox.showerror("Invalid folder", problem)
            return
        if not new_path or new_path == folder_path:
            return
        affected = [r for r in self.rotations.values()
                    if r.folder == folder_path or r.folder.startswith(folder_path + "/")]
        planned = {r.name: new_path + r.folder[len(folder_path):] for r in affected}
        collisions = self._folder_move_collisions(planned)
        if collisions:
            a, b = collisions[0]
            messagebox.showerror(
                "Cannot rename folder",
                f"'{a}' and '{b}' would both save to the same file after this rename "
                "-- rename or move one of them out of the way first.")
            return
        # Captured before any mutation: if the renamed folder IS (or contains) the
        # Active Folder, that scope needs to follow the rename too, but each
        # rotation's "was it in scope before" must still be judged against this
        # pre-rename value, not the already-updated one.
        old_active_folder = self.active_folder
        if old_active_folder is not None and (
                old_active_folder == folder_path or old_active_folder.startswith(folder_path + "/")):
            self.active_folder = new_path + old_active_folder[len(folder_path):]
            self.active_folder_var.set(self.active_folder or "(All Folders)")
        for rotation in affected:
            old_folder = rotation.folder
            was_in_scope = folder_in_scope(old_folder, old_active_folder)
            new_folder = planned[rotation.name]
            rotation.folder = new_folder
            storage.move_rotation(rotation, rotation.name, old_folder)
            if rotation.name == self.editing_original_name:
                self.folder_var.set(new_folder)
            self._reconcile_hotkey_scope(rotation, was_in_scope)
        app_state.save_state(self.active_folder, self.active_device, self._theme)
        self._refresh_rotation_tree()

    def _move_selected_to_folder(self):
        """Moves every currently-selected rotation to one destination folder in
        a single action, instead of opening each one to edit its Folder field."""
        names = self._selected_rotation_names()
        if not names:
            messagebox.showinfo("No rotations selected", "Select one or more rotations in the list first.")
            return
        current_folder = self.rotations[names[0]].folder
        new_path = messagebox.askstring(
            "Move to Folder", "Destination folder (blank = ungrouped):",
            initialvalue=current_folder, parent=self)
        if new_path is None:
            return
        new_path = new_path.strip().strip("/")
        problem = folder_path_problem(new_path)
        if problem:
            messagebox.showerror("Invalid folder", problem)
            return
        planned = {name: new_path for name in names}
        collisions = self._folder_move_collisions(planned)
        if collisions:
            a, b = collisions[0]
            messagebox.showerror(
                "Cannot move",
                f"'{a}' and '{b}' would both save to the same file in the destination folder "
                "-- rename one of them or choose a different destination.")
            return
        for name in names:
            rotation = self.rotations[name]
            if rotation.folder == new_path:
                continue
            old_folder = rotation.folder
            was_in_scope = self._folder_in_scope(old_folder)
            rotation.folder = new_path
            storage.move_rotation(rotation, name, old_folder)
            if name == self.editing_original_name:
                self.folder_var.set(new_path)
            self._reconcile_hotkey_scope(rotation, was_in_scope)
        self._refresh_rotation_tree()

    def _load_rotation_into_form(self, rotation: Rotation):
        # Wrapped in _autosave_suppressed() -- every .set() below is code
        # populating the form from `rotation`, not the user editing it, and
        # must never misfire an autosave onto whatever rotation was open a
        # moment ago (see AutosaveMixin, poe2bot/gui/autosave.py).
        with self._autosave_suppressed():
            self.editing_original_name = rotation.name
            self.pending_hotkey = rotation.hotkey
            self.pending_cancel_key = rotation.cancel_key
            self.pending_reset_key = rotation.reset_key
            self.pending_pause_key = rotation.pause_key
            self.editing_steps = copy.deepcopy(rotation.steps)
            self.name_var.set(rotation.name)
            self.folder_var.set(rotation.folder)
            self.mode_var.set(rotation.mode)
            self.hotkey_label_var.set(display_name(rotation.hotkey))
            self.cancel_key_label_var.set(display_name(rotation.cancel_key))
            self.reset_key_label_var.set(display_name(rotation.reset_key))
            self.reset_delay_var.set(str(rotation.reset_delay_ms))
            self.pause_key_label_var.set(display_name(rotation.pause_key))
            self.pause_mode_var.set(rotation.pause_mode)
            self.pause_duration_var.set(str(rotation.pause_duration_ms))
            self._reset_step_core_fields()
            self._refresh_steps_tree()
        self._update_title()

    def _new_rotation(self):
        with self._autosave_suppressed():
            self.editing_original_name = None
            self.pending_hotkey = None
            self.pending_cancel_key = None
            self.pending_reset_key = None
            self.pending_pause_key = None
            self.editing_steps = []
            self.name_var.set("New Rotation")
            self.folder_var.set("")
            self.mode_var.set("once")
            self.hotkey_label_var.set("(unbound)")
            self.cancel_key_label_var.set("(unbound)")
            self.reset_key_label_var.set("(unbound)")
            self.reset_delay_var.set("0")
            self.pause_key_label_var.set("(unbound)")
            self.pause_mode_var.set("duration")
            self.pause_duration_var.set("1000")
            self._reset_step_core_fields()
            self._refresh_steps_tree()
            self.rotation_tree.selection_remove(*self.rotation_tree.selection())
        self._update_title()

    def _copy_rotation(self):
        name = self._selected_rotation_name()
        if name is None:
            messagebox.showinfo("No rotation selected", "Select a rotation in the list first.")
            return
        original = self.rotations[name]
        duplicate = Rotation(
            name=self._unique_rotation_name(f"{original.name} (copy)"),
            mode=original.mode,
            hotkey=None,  # can't share the original's hotkey -- bind a new one before saving
            alt_hotkey=None,  # same reasoning as hotkey=None above
            cancel_key=original.cancel_key,  # cancel/reset/pause keys CAN be shared, so these carry over as-is
            alt_cancel_key=original.alt_cancel_key,
            reset_key=original.reset_key,
            alt_reset_key=original.alt_reset_key,
            reset_delay_ms=original.reset_delay_ms,
            pause_key=original.pause_key,
            alt_pause_key=original.alt_pause_key,
            pause_mode=original.pause_mode,
            pause_duration_ms=original.pause_duration_ms,
            folder=original.folder,
            steps=copy.deepcopy(original.steps),
        )
        self._load_rotation_into_form(duplicate)
        self.rotation_tree.selection_remove(*self.rotation_tree.selection())
        # A duplicate should exist on disk immediately, same as everything else --
        # not stay phantom until some later field edit happens to trigger a save.
        self._autosave()

    def _unique_rotation_name(self, base_name: str) -> str:
        if base_name not in self.rotations:
            return base_name
        n = 2
        while f"{base_name} {n}" in self.rotations:
            n += 1
        return f"{base_name} {n}"

    def _delete_rotation(self):
        name = self._selected_rotation_name()
        if name is None:
            return
        if not messagebox.askyesno("Delete rotation", f"Delete '{name}'?", danger=True):
            return
        rotation = self.rotations.pop(name)
        self._clear_rotation_hotkeys(name)
        self.rotation_manager.unload(name)
        storage.delete_rotation(name, rotation.folder)
        self._refresh_rotation_tree()
        self._new_rotation()
        self._sweep_templates()
