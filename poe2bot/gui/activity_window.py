import time
import tkinter as tk
from tkinter import ttk

from poe2bot.gui.constants import STATUS_LABELS

MAX_LINES_PER_PANE = 3000
PANE_MIN_WIDTH = 260


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
        self.geometry("900x400")
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            self.configure(bg=bg)
        self._paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6)
        self._paned.pack(fill="both", expand=True)
        self._panes = {}  # rotation name -> (LabelFrame, Text)

    def ensure_pane(self, name: str):
        if name in self._panes:
            return
        frame = ttk.LabelFrame(self._paned, text=name, padding=4)
        text = tk.Text(frame, width=42, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self._paned.add(frame, minsize=PANE_MIN_WIDTH)
        self._panes[name] = (frame, text)

    def set_pane_state(self, name: str, status: str):
        self.ensure_pane(name)
        frame, _ = self._panes[name]
        frame.configure(text=f"{name}{STATUS_LABELS.get(status, '')}")

    def append(self, name: str, message: str, ts: float):
        self.ensure_pane(name)
        _, text = self._panes[name]
        text.configure(state="normal")
        text.insert("end", f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {message}\n")
        overflow = int(text.index("end-1c").split(".")[0]) - MAX_LINES_PER_PANE
        if overflow > 0:
            text.delete("1.0", f"{overflow}.0")
        text.see("end")
        text.configure(state="disabled")
