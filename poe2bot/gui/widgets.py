"""Small, generic composite widgets shared across the GUI package."""
import tkinter as tk
from tkinter import ttk

from poe2bot.gui import theme

_COLLAPSED_MARK = "▸"  # >
_EXPANDED_MARK = "▾"   # v


def make_scrollable_area(parent, *, horizontal: bool = False, padding: int = 0):
    """Wraps a Canvas + Scrollbar(s) around a fresh body frame the caller
    packs its own content into -- the standard Tk way to make an arbitrary
    stack of widgets scrollable, since ttk has no native scrollable frame.
    Returns (canvas, body): body is what callers pack their content into;
    canvas is returned too, since a caller may need it for its own sizing
    (e.g. SettingsWindow auto-sizing itself to its content).

    `horizontal`, if set, also adds a horizontal scrollbar and never shrinks
    body below its own natural required width -- for a caller with rows
    that can genuinely be wider than the window (e.g. the step editor's
    Skill Steps columns on a narrow screen), so the excess is reachable via
    horizontal scroll instead of being squeezed and clipped; otherwise body
    is always stretched/shrunk to exactly the canvas's own width, like a
    plain vertically-scrolling list. `padding`, if set, applies to body
    itself (e.g. an outer content margin) rather than needing a second
    nested Frame just for that.

    Mouse-wheel scrolling is bound only while the pointer is actually over
    the canvas (not for the whole window's lifetime), so scrolling over
    some OTHER scrollable widget nested inside (a Treeview's own scrollbar,
    the rotation list, ...) is never hijacked by this canvas -- covers both
    Windows/macOS's <MouseWheel> and X11's <Button-4>/<Button-5> (Linux has
    no <MouseWheel> event of its own)."""
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True)
    canvas = tk.Canvas(container, highlightthickness=0, bd=0, bg=ttk.Style().lookup("TFrame", "background"))
    vscroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vscroll.set)
    if horizontal:
        # Packed before the canvas (claiming its own strip of the cavity first)
        # so it lands flush against the bottom edge instead of leaving a gap.
        hscroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hscroll.set)
        hscroll.pack(side="bottom", fill="x")
    vscroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    body = ttk.Frame(canvas, padding=padding)
    window_id = canvas.create_window((0, 0), window=body, anchor="nw")

    def _on_body_configure(_event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    body.bind("<Configure>", _on_body_configure)

    def _on_canvas_configure(event):
        width = max(event.width, body.winfo_reqwidth()) if horizontal else event.width
        canvas.itemconfigure(window_id, width=width)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(event):
        canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

    def _on_shift_mousewheel(event):
        canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_wheel(_event):
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel_linux)
        canvas.bind_all("<Button-5>", _on_mousewheel_linux)
        if horizontal:
            canvas.bind_all("<Shift-MouseWheel>", _on_shift_mousewheel)

    def _unbind_wheel(_event):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")
        if horizontal:
            canvas.unbind_all("<Shift-MouseWheel>")
    canvas.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)
    return canvas, body


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
