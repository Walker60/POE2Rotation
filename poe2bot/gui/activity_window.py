import time
import tkinter as tk
from tkinter import ttk

from poe2bot.gui import geometry
from poe2bot.gui.constants import STATUS_COLORS, STATUS_LABELS

MAX_LINES_PER_PANE = 3000
PANE_MIN_WIDTH = 260
_FALLBACK_BG = "#1c1c1c"
_FALLBACK_FG = "#fafafa"
_FALLBACK_SELECT_BG = "#2f60d8"
_FALLBACK_SELECT_FG = "#ffffff"


def _theme_colors():
    """(bg, fg, select_bg, select_fg), read live from the active ttk theme
    with a fallback for anything not yet themed -- same lookup-with-fallback
    idiom as drag_drop.py's _drop_target_color, needed here because
    tk.PanedWindow/tk.Text are plain tk widgets sv_ttk never restyles."""
    style = ttk.Style()
    bg = style.lookup("TFrame", "background") or _FALLBACK_BG
    fg = style.lookup("TLabel", "foreground") or _FALLBACK_FG
    select_bg = style.lookup("Treeview", "background", ("selected",)) or _FALLBACK_SELECT_BG
    select_fg = _FALLBACK_SELECT_FG
    return bg, fg, select_bg, select_fg


class ActivityWindow(tk.Toplevel):
    """Live, one-pane-per-rotation view of what each running rotation is
    doing. Fed exclusively from App._poll_status_queue on the Tk thread --
    RotationRunner worker threads only ever reach this indirectly via
    App._queue_activity's queue.put (see poe2bot/gui/app.py), the same
    thread-marshaling pattern already used for status_queue. Panes are added
    as rotations start running and are never removed for the life of one
    ActivityWindow instance -- a stopped rotation's pane just gets its
    header suffix updated, so its recent history stays visible.

    Uses classic tk.PanedWindow (not ttk.PanedWindow) because ttk's themed
    paned window has no per-pane minsize option -- with several rotations
    sharing one hotkey, an unenforced floor would let a pane get dragged
    down to a sliver.
    """

    def __init__(self, master):
        super().__init__(master)
        self.title("Rotation Activity")
        self._sized_once = False  # size_window_to_contents runs once the first pane exists,
                                  # not here -- an empty window would size to near-nothing
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)
        self._paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6)
        self._paned.pack(fill="both", expand=True)
        self._panes = {}  # rotation name -> (LabelFrame, Text, dot Label, name Label)
        self.refresh_theme()

    def refresh_theme(self):
        """Re-applies theme-derived colors to every plain-tk widget this
        window owns -- called once at construction and again from
        App._toggle_theme(), since sv_ttk never restyles tk.PanedWindow/
        tk.Text on its own."""
        bg, fg, select_bg, select_fg = _theme_colors()
        self._paned.configure(bg=bg)
        for _frame, text, _dot, _name_label in self._panes.values():
            text.configure(bg=bg, fg=fg, insertbackground=fg,
                            selectbackground=select_bg, selectforeground=select_fg)

    def ensure_pane(self, name: str):
        if name in self._panes:
            return
        frame = ttk.LabelFrame(self._paned, padding=4)
        header = ttk.Frame(frame)
        dot_label = ttk.Label(header, text="", width=1)
        dot_label.pack(side="left")
        name_label = ttk.Label(header, text=name)
        name_label.pack(side="left", padx=(2, 0))
        frame.configure(labelwidget=header)
        text = tk.Text(frame, width=42, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self._paned.add(frame, minsize=PANE_MIN_WIDTH)
        self._panes[name] = (frame, text, dot_label, name_label)
        bg, fg, select_bg, select_fg = _theme_colors()
        text.configure(bg=bg, fg=fg, insertbackground=fg,
                        selectbackground=select_bg, selectforeground=select_fg)
        if not self._sized_once:
            self._sized_once = True
            geometry.size_window_to_contents(self, min_width=900, min_height=400)

    def set_pane_state(self, name: str, status: str):
        self.ensure_pane(name)
        _frame, _text, dot_label, name_label = self._panes[name]
        color = STATUS_COLORS.get(status)
        dot_label.configure(text="●" if color else "", foreground=color or "")
        name_label.configure(text=f"{name}{STATUS_LABELS.get(status, '')}")

    def append(self, name: str, message: str, ts: float):
        self.ensure_pane(name)
        _frame, text, _dot, _name_label = self._panes[name]
        text.configure(state="normal")
        text.insert("end", f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {message}\n")
        overflow = int(text.index("end-1c").split(".")[0]) - MAX_LINES_PER_PANE
        if overflow > 0:
            text.delete("1.0", f"{overflow}.0")
        text.see("end")
        text.configure(state="disabled")
