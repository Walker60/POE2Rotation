import sys
import tkinter as tk

from poe2bot.gui import geometry


def _shrunk_to_dodge_fullscreen_unredirect(width: int, height: int) -> tuple:
    """(width, height) unchanged on Windows; each reduced by 1px on Linux.

    Belt-and-suspenders alongside _disable_compositor_bypass below, for the
    same underlying compositor behavior: on at least some configurations,
    _NET_WM_BYPASS_COMPOSITOR appears NOT to be honored for an
    override-redirect window like this one specifically (this overlay
    remained fully opaque on a real Steam Deck even with that hint set),
    which suggests the compositor's "unredirect fullscreen windows"
    detection for such windows is purely geometric -- does this window's
    rect exactly cover an output -- rather than actually consulting the
    hint. Making the window reliably NOT that exact shape sidesteps the
    detection directly instead of depending on a hint that may or may not
    be respected for this window class on a given compositor."""
    if sys.platform == "win32":
        return width, height
    return max(1, width - 1), max(1, height - 1)


def _disable_compositor_bypass(window: tk.Toplevel):
    """Linux only: explicitly tells the compositor (KWin, as used by
    SteamOS Desktop Mode, included) NOT to skip compositing this window, via
    the _NET_WM_BYPASS_COMPOSITOR EWMH property. Several compositors
    automatically bypass compositing entirely -- direct scanout, no alpha
    blending -- for any window whose geometry exactly matches a screen's
    full resolution, as a performance optimization aimed at fullscreen
    games; this overlay is deliberately that exact shape (see
    _FullscreenPickerOverlay), which defeats its requested -alpha
    transparency completely on those compositors -- it renders fully
    opaque gray instead of see-through, even though the identical code
    renders correctly translucent on Windows (whose equivalent, DWM, has
    no such fullscreen-bypass heuristic).

    Best-effort and silent: does nothing if python-xlib isn't installed, or
    if anything else about this fails, since a fully-opaque overlay is a
    visual regression, not a functional one -- capture/calibration still
    works, it's just harder to see exactly what you're clicking on."""
    if sys.platform == "win32":
        return
    try:
        from Xlib import Xatom, display
        d = display.Display()
        xwindow = d.create_resource_object("window", window.winfo_id())
        xwindow.change_property(d.intern_atom("_NET_WM_BYPASS_COMPOSITOR"), Xatom.CARDINAL, 32, [0])
        d.flush()
    except Exception:
        pass


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

        # On Linux, shave 1px off each dimension (see _shrunk_to_dodge_fullscreen_unredirect):
        # some compositors bypass alpha blending entirely for a window whose
        # geometry EXACTLY matches a screen's resolution, regardless of the
        # _NET_WM_BYPASS_COMPOSITOR hint below -- so this overlay is
        # deliberately never quite that shape there. A 1px-narrower capture
        # area is an imperceptible trade-off for actually being translucent.
        draw_width, draw_height = _shrunk_to_dodge_fullscreen_unredirect(width, height)

        self.overrideredirect(True)
        self.geometry(f"{draw_width}x{draw_height}+{left}+{top}")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")
        _disable_compositor_bypass(self)

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        if hint_text:
            self._draw_hint(hint_text)

        self.bind("<Escape>", self._on_cancel)
        # wait_visibility() BEFORE grab_set()/focus_force() -- not just
        # belt-and-suspenders. On X11 (Steam Deck/Linux Desktop Mode),
        # XSetInputFocus/XGrabPointer require the target window to already
        # be viewable (mapped by the X server) or they silently fail --
        # calling grab_set()/focus_force() right after creating the window,
        # before Tk has actually finished mapping it, wins that race often
        # enough to matter: the overlay still becomes visible (it doesn't
        # need the grab for that), but never actually receives the click,
        # which is exactly "screen goes gray, nothing responds to clicking
        # it" with no error anywhere. Windows' focus/activation model has no
        # equivalent "must already be mapped" requirement, so this was never
        # reachable there. wait_visibility() blocks until the window is
        # actually mapped, so the grab/focus calls that follow always land
        # on a real, viewable window on every platform.
        self.wait_visibility()
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
