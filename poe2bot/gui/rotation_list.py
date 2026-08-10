import copy

import tkinter as tk
from tkinter import messagebox, simpledialog

from poe2bot import storage
from poe2bot.hotkeys import display_name
from poe2bot.models import Rotation, folder_path_problem
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
            item_id = self.rotation_tree.insert(
                parent_id, tk.END, iid=f"folder:{folder_path}",
                text=f"\U0001F4C1 {label}", open=folder_path in previously_open)
            self._folder_nodes[folder_path] = item_id
            return item_id

        # Folders first, then ungrouped rotations, each group alphabetical -- avoids
        # ungrouped rotations (folder == "") sorting before every folder name.
        for name in sorted(self.rotations, key=lambda n: (
                self.rotations[n].folder == "", self.rotations[n].folder.lower(), n.lower())):
            rotation = self.rotations[name]
            parent_id = ensure_folder_node(rotation.folder)
            shared_suffix = ""
            if rotation.hotkey and len(self.hotkey_manager.bound_to(rotation.hotkey)) > 1:
                shared_suffix = f" (shared {display_name(rotation.hotkey)})"
            suffix = STATUS_LABELS.get(self.rotation_manager.status(name), "")
            self.rotation_tree.insert(
                parent_id, tk.END, iid=f"rotation:{name}", text=f"{name}{shared_suffix}{suffix}")

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
        if name is not None and name in self.rotations:
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

    def _rename_folder(self, folder_path: str):
        """Renames/moves folder_path to a new path, taking every rotation in it
        (and any nested subfolders) along -- a bulk operation, unlike editing a
        single rotation's Folder field one at a time."""
        new_path = simpledialog.askstring(
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
        for rotation in affected:
            old_folder = rotation.folder
            new_folder = new_path + old_folder[len(folder_path):]
            storage.delete_rotation(rotation.name, old_folder)
            rotation.folder = new_folder
            storage.save_rotation(rotation)
            if rotation.name == self.editing_original_name:
                self.folder_var.set(new_folder)
        self._refresh_rotation_tree()

    def _move_selected_to_folder(self):
        """Moves every currently-selected rotation to one destination folder in
        a single action, instead of opening each one to edit its Folder field."""
        names = self._selected_rotation_names()
        if not names:
            messagebox.showinfo("No rotations selected", "Select one or more rotations in the list first.")
            return
        current_folder = self.rotations[names[0]].folder
        new_path = simpledialog.askstring(
            "Move to Folder", "Destination folder (blank = ungrouped):",
            initialvalue=current_folder, parent=self)
        if new_path is None:
            return
        new_path = new_path.strip().strip("/")
        problem = folder_path_problem(new_path)
        if problem:
            messagebox.showerror("Invalid folder", problem)
            return
        for name in names:
            rotation = self.rotations[name]
            if rotation.folder == new_path:
                continue
            storage.delete_rotation(rotation.name, rotation.folder)
            rotation.folder = new_path
            storage.save_rotation(rotation)
            if name == self.editing_original_name:
                self.folder_var.set(new_path)
        self._refresh_rotation_tree()

    def _load_rotation_into_form(self, rotation: Rotation):
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
        self._reset_ready_form()
        self._reset_buff_form()
        self._refresh_steps_tree()

    def _new_rotation(self):
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
        self._reset_ready_form()
        self._reset_buff_form()
        self._refresh_steps_tree()
        self.rotation_tree.selection_remove(*self.rotation_tree.selection())

    def _on_revert_clicked(self):
        """Discards unsaved edits to whichever rotation is currently open --
        a safety net now that drag-and-drop/multi-select make it easier to
        mess one up by accident."""
        if self.editing_original_name and self.editing_original_name in self.rotations:
            if not messagebox.askyesno(
                    "Discard unsaved changes",
                    f"Discard unsaved changes to '{self.editing_original_name}'?"):
                return
            self._load_rotation_into_form(self.rotations[self.editing_original_name])
        else:
            if not messagebox.askyesno(
                    "Discard unsaved changes", "Discard this new, unsaved rotation?"):
                return
            self._new_rotation()

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
            cancel_key=original.cancel_key,  # cancel/reset/pause keys CAN be shared, so these carry over as-is
            reset_key=original.reset_key,
            reset_delay_ms=original.reset_delay_ms,
            pause_key=original.pause_key,
            pause_mode=original.pause_mode,
            pause_duration_ms=original.pause_duration_ms,
            folder=original.folder,
            steps=copy.deepcopy(original.steps),
        )
        self._load_rotation_into_form(duplicate)
        self.rotation_tree.selection_remove(*self.rotation_tree.selection())

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
        if not messagebox.askyesno("Delete rotation", f"Delete '{name}'?"):
            return
        rotation = self.rotations.pop(name)
        self.hotkey_manager.unbind(name)
        self.hotkey_manager.set_cancel_key(name, None)
        self.hotkey_manager.set_reset_key(name, None)
        self.hotkey_manager.set_pause_key(name, None)
        self.rotation_manager.unload(name)
        storage.delete_rotation(name, rotation.folder)
        self._refresh_rotation_tree()
        self._new_rotation()
        self._sweep_templates()
