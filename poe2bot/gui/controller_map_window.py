import tkinter as tk
from tkinter import ttk

from poe2bot.controller import encode_controller_key
from poe2bot.gui import geometry
from poe2bot.gui.controller_layouts import CONTROLLER_LAYOUTS, CONTROLLER_TYPE_LABELS


class ControllerMapWindow(tk.Toplevel):
    """Lets the user pick a controller button by clicking its on-screen
    label instead of physically pressing it -- currently the only reliable
    way to bind a Steam Deck's own built-in controls (they're normally
    owned by Steam Input and never reach evdev unless poe2bot itself is
    launched through Steam; see README's Steam Deck section), and a handy
    shortcut on Windows too when the real controller isn't within reach.

    Shows whichever layout/labels match Settings > Controller Type (see
    poe2bot/gui/controller_layouts.py), but every button still encodes the
    same "controller:<name>" value controller.py's virtual Xbox 360 pad
    understands -- Xbox and Steam Deck differ here only in what each button
    is CALLED, never in which sixteen buttons exist. `on_select` is called
    with the encoded value the moment any button is clicked, and the window
    closes itself immediately after -- there's nothing else to configure
    here, unlike a capture flow there's no "waiting for input" state to
    show. Used both by the step editor's own Map Controller Button (a
    step's Key field) and by each rotation-level hotkey/cancel/reset/pause
    row's "Map..." button (poe2bot/gui/hotkeys_ui.py) -- callers who
    disabled other UI before opening this (the hotkeys rows do, to keep a
    physical Bind... and this mutually exclusive-looking; the step editor
    doesn't need to) pass `on_close`, called when the window goes away for
    ANY reason -- a pick (after `on_select`) or Cancel/the window's own
    close button alike -- so that re-enabling isn't skipped just because
    the user backed out instead of picking something.
    """

    def __init__(self, master, controller_type: str, on_select, on_close=None):
        super().__init__(master)
        self._on_select = on_select
        self._on_close = on_close
        self.title(f"Map Controller Button — {CONTROLLER_TYPE_LABELS[controller_type]}")
        self.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)

        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Click the button to use.",
                  foreground="gray").pack(anchor="w", pady=(0, 8))

        groups_row = ttk.Frame(container)
        groups_row.pack()
        for title, buttons in CONTROLLER_LAYOUTS[controller_type]:
            group = ttk.LabelFrame(groups_row, text=title, padding=8)
            group.pack(side="left", padx=(0, 8), fill="y")
            for name, label, row, col in buttons:
                ttk.Button(group, text=label, width=9,
                           command=lambda n=name: self._choose(n)).grid(
                    row=row, column=col, padx=2, pady=2)

        ttk.Button(container, text="Cancel", command=self._cancel).pack(pady=(10, 0))
        geometry.size_window_to_contents(self)
        # Modal -- this is a one-shot picker, not a window meant to sit open
        # alongside the rest of the editor the way Settings/Activity/Hotkey
        # Map do.
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.focus_force()

    def _choose(self, name: str):
        self._on_select(encode_controller_key(name))
        self.destroy()
        if self._on_close:
            self._on_close()

    def _cancel(self):
        self.destroy()
        if self._on_close:
            self._on_close()
