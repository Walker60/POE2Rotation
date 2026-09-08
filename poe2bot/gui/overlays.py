import tkinter as tk

from poe2bot.gui import geometry


class _FullscreenPickerOverlay(tk.Toplevel):
    """Shared fullscreen, borderless, semi-transparent picker chrome behind
    RegionCaptureOverlay (click-drag-release rectangle) and
    PointCaptureOverlay (single-click point): window/canvas setup, the
    optional persistent hint-text banner, Escape-to-cancel, and the
    destroy-then-callback finish sequence. Subclasses only add their own
    canvas bindings and `_finish(...)` payload.

    Spans the full virtual desktop (every connected monitor's combined
    bounds -- see geometry.virtual_screen_bounds), not just the primary
    monitor's own resolution at +0+0, so a skill icon on a secondary
    monitor (including one positioned above/left of the primary, at
    negative virtual coordinates) can be calibrated too. Falls back to
    primary-monitor-only geometry if that Win32 query ever fails.
    """

    _HINT_FONT = ("Segoe UI", 12, "bold")
    _HINT_PAD = 8

    def __init__(self, master, on_done, hint_text=None):
        super().__init__(master)
        self.on_done = on_done

        bounds = geometry.virtual_screen_bounds()
        if bounds:
            left, top, width, height = bounds
        else:
            left, top = 0, 0
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()
        self._overlay_width = width

        self.overrideredirect(True)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        if hint_text:
            self._draw_hint(hint_text)

        self.bind("<Escape>", self._on_cancel)
        self.grab_set()
        self.focus_force()

    def _draw_hint(self, hint_text):
        """A persistent on-canvas reminder of what to do, so the one-time
        instructional popup (shown at most once per app session -- see
        CalibrationMixin) doesn't need repeating before every capture. A
        dark backing rectangle keeps light text legible over whatever is
        actually behind this semi-transparent overlay. Centered on this
        overlay's OWN width (the full virtual desktop), not
        winfo_screenwidth() (always just the primary monitor's), so the
        hint lands in the middle of whichever monitor setup this is."""
        cx = self._overlay_width // 2
        text_id = self.canvas.create_text(cx, 24, text=hint_text, fill="white", font=self._HINT_FONT)
        bbox = self.canvas.bbox(text_id)
        if bbox:
            pad = self._HINT_PAD
            self.canvas.create_rectangle(
                bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad,
                fill="black", outline="", stipple="gray50")
            self.canvas.tag_raise(text_id)

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, value):
        callback = self.on_done
        self.destroy()
        callback(value)


class RegionCaptureOverlay(_FullscreenPickerOverlay):
    """Fullscreen click-drag-release rectangle picker (see
    _FullscreenPickerOverlay for the shared chrome/limitations).

    Calls on_done(region) where region is (left, top, width, height) in
    absolute screen pixels, or on_done(None) if cancelled (Escape, or a
    release without a meaningfully-sized drag).
    """

    def __init__(self, master, on_done, hint_text=None):
        super().__init__(master, on_done, hint_text)
        self._start = None
        self._rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event):
        self._start = (event.x_root, event.y_root)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2)

    def _on_drag(self, event):
        if self._start is None:
            return
        x0, y0 = self._start
        # This rectangle is purely cosmetic drag feedback in canvas-local coordinates;
        # the authoritative region below is computed entirely from x_root/y_root,
        # never from an assumed canvas-local == screen equivalence.
        self.canvas.coords(
            self._rect_id,
            x0 - self.winfo_rootx(), y0 - self.winfo_rooty(),
            event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())

    def _on_release(self, event):
        if self._start is None:
            self._finish(None)
            return
        x0, y0 = self._start
        x1, y1 = event.x_root, event.y_root
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 4 or height < 4:
            self._finish(None)
            return
        self._finish((left, top, width, height))


class PointCaptureOverlay(_FullscreenPickerOverlay):
    """Fullscreen single-click point picker (see _FullscreenPickerOverlay
    for the shared chrome/limitations).

    Calls on_done(point) where point is (x, y) in absolute screen pixels, or
    on_done(None) if cancelled (Escape).
    """

    def __init__(self, master, on_done, hint_text=None):
        super().__init__(master, on_done, hint_text)
        self.canvas.bind("<ButtonRelease-1>", self._on_click)

    def _on_click(self, event):
        self._finish((event.x_root, event.y_root))
