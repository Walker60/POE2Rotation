import tkinter as tk
from tkinter import ttk

from poe2bot.gui import geometry
from poe2bot.hotkeys import display_name


class HotkeyMapWindow(tk.Toplevel):
    """A read-only snapshot table of every rotation's hotkey/cancel/reset/
    pause keys across every folder -- not just whichever the Active Folder
    currently scopes live. A same-key conflict between two rotations in
    different folders is real (it'll misbehave the moment either becomes
    in-scope) even though HotkeysMixin only ever warns about same-folder
    sharing at the moment a NEW hotkey is bound (see
    HotkeysMixin._confirm_hotkey_share_if_needed) -- this surfaces it
    immediately instead, however it was introduced (e.g. editing a Folder
    field, or two rotations independently choosing the same key from
    different folders). Any key value used more than once anywhere in the
    table (any role, any rotation) is marked, since intentional trigger-key
    sharing looks identical to an accidental Cancel/Reset/Pause collision
    at a glance otherwise."""

    def __init__(self, master):
        super().__init__(master)
        self._master = master
        self.title("Hotkey Map")
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)

        container = ttk.Frame(self, padding=8)
        container.pack(fill="both", expand=True)
        ttk.Label(container, wraplength=560, justify="left",
                  text="⚠ marks a key used more than once anywhere below -- not necessarily a "
                       "mistake (trigger-key sharing is intentional and supported), just worth a "
                       "second look.").pack(anchor="w", pady=(0, 6))

        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=("folder", "hotkey", "cancel", "reset", "pause"),
            show="tree headings", height=16)
        self.tree.heading("#0", text="Rotation")
        self.tree.column("#0", width=160)
        for col, label, width in (
            ("folder", "Folder", 140), ("hotkey", "Hotkey", 110),
            ("cancel", "Cancel", 110), ("reset", "Reset", 110), ("pause", "Pause", 110),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("key_conflict", foreground="#ff5555")
        self.tree.tag_configure("rotation_disabled", foreground="gray")

        ttk.Button(container, text="Refresh", command=self.refresh).pack(pady=(8, 0))
        self.refresh()
        geometry.size_window_to_contents(self, min_width=640, min_height=360)

    def refresh(self):
        """Rebuilds the table from master.rotations as it stands right now
        -- a snapshot, not a live view; call again (or click Refresh) after
        rotations change."""
        self.tree.delete(*self.tree.get_children())
        rotations = self._master.rotations
        key_counts = {}
        for rotation in rotations.values():
            for key in (rotation.hotkey, rotation.cancel_key, rotation.reset_key, rotation.pause_key):
                if key:
                    key_counts[key] = key_counts.get(key, 0) + 1

        def cell(key):
            if not key:
                return ""
            marker = " ⚠" if key_counts.get(key, 0) > 1 else ""
            return display_name(key) + marker

        for name in sorted(rotations, key=lambda n: (rotations[n].folder.lower(), n.lower())):
            rotation = rotations[name]
            keys = (rotation.hotkey, rotation.cancel_key, rotation.reset_key, rotation.pause_key)
            has_conflict = any(key_counts.get(k, 0) > 1 for k in keys if k)
            tags = tuple(t for t in ("key_conflict" if has_conflict else None,
                                      "rotation_disabled" if not rotation.enabled else None) if t)
            self.tree.insert(
                "", tk.END, iid=name, text=name,
                values=(rotation.folder or "(ungrouped)", cell(rotation.hotkey), cell(rotation.cancel_key),
                        cell(rotation.reset_key), cell(rotation.pause_key)),
                tags=tags)
