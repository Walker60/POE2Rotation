"""Small, generic composite widgets shared across the GUI package."""
import tkinter as tk
from tkinter import ttk

from poe2bot.gui import theme

_COLLAPSED_MARK = "▸"  # >
_EXPANDED_MARK = "▾"   # v


class CollapsibleSection(ttk.Frame):
    """A ttk.LabelFrame-like group whose body can be shown/hidden by
    clicking its header. Pack/grid children into `.body`, exactly like
    packing into a LabelFrame's own frame -- this is meant as a drop-in
    replacement at call sites that used to do
    `group = ttk.LabelFrame(parent, text=title); group.pack(...)` and then
    packed children into `group` directly.

    `subtitle_var`, if given, shows a muted summary next to the title,
    visible even while collapsed (e.g. "Cooldown Check -- Image: 64x64"),
    so collapsing a section doesn't hide *whether* it's configured.

    `on_toggle(collapsed: bool)`, if given, fires only on a user click (not
    on a programmatic set_collapsed call) -- used by callers that want to
    remember collapse state themselves (e.g. per selected step).
    """

    def __init__(self, parent, title: str, *, padding=6, start_collapsed=False,
                 subtitle_var=None, on_toggle=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._collapsed = start_collapsed

        header = ttk.Frame(self, cursor="hand2")
        header.pack(fill="x")
        self._indicator_var = tk.StringVar()
        indicator = ttk.Label(header, textvariable=self._indicator_var, width=2, cursor="hand2")
        indicator.pack(side="left")
        title_label = ttk.Label(header, text=title, font=theme.heading_font(parent), cursor="hand2")
        title_label.pack(side="left")
        clickable = [header, indicator, title_label]
        if subtitle_var is not None:
            subtitle_label = ttk.Label(header, textvariable=subtitle_var, foreground="gray", cursor="hand2")
            subtitle_label.pack(side="left", padx=(8, 0))
            clickable.append(subtitle_label)
        for widget in clickable:
            widget.bind("<Button-1>", lambda _e: self._on_click())

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(2, 0))

        self.body = ttk.Frame(self, padding=padding)
        self._apply_state()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._apply_state()

    def _on_click(self) -> None:
        self.set_collapsed(not self._collapsed)
        if self._on_toggle is not None:
            self._on_toggle(self._collapsed)

    def _apply_state(self) -> None:
        if self._collapsed:
            self._indicator_var.set(_COLLAPSED_MARK)
            self.body.pack_forget()
        else:
            self._indicator_var.set(_EXPANDED_MARK)
            self.body.pack(fill="x")
