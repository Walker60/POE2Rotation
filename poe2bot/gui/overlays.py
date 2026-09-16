import sys
import threading
import tkinter as tk

import keyboard
from PIL import Image, ImageTk

_IS_WINDOWS = sys.platform == "win32"
_ESCAPE_POLL_MS = 50  # how often the Tk thread checks for a background-thread Escape request


class _FullscreenPickerOverlay(tk.Toplevel):
    """Shared fullscreen, borderless picker chrome behind RegionCaptureOverlay
    (click-drag-release rectangle) and PointCaptureOverlay (single-click
    point): window/canvas setup, the optional persistent hint-text banner,
    Escape-to-cancel, and the destroy-then-callback finish sequence.
    Subclasses only add their own canvas bindings and `_finish(...)` payload.

    Displayed as an actual captured-screen-plus-gray-tint IMAGE (`bounds`/
    `captured_image`, both supplied by the caller -- see
    CalibrationMixin._capture_full_screen_for_overlay), not a semi-
    transparent WINDOW: an earlier version of this used a real
    -alpha-attributed window instead, relying on the window manager/
    compositor to blend it with whatever's underneath live. That turned out
    to be unreliable on at least one real Linux compositor (KDE Plasma/
    KWin, as used by SteamOS Desktop Mode) -- the overlay rendered fully
    opaque no matter what per-window transparency hint was set, seemingly
    because compositors commonly skip alpha blending entirely for a window
    whose geometry exactly matches a screen's resolution (a performance
    optimization aimed at fullscreen games), and neither asking to opt out
    of that (_NET_WM_BYPASS_COMPOSITOR) nor making the window 1px smaller
    than the screen actually fixed it in practice. Baking the tint into a
    static image instead works identically regardless of compositor
    behavior, or even with no compositor running at all -- and as a
    bonus, this is also what lets calibration crop/sample the exact same
    pixels the user saw on screen directly from `captured_image`,
    rather than needing a SECOND, separately-timed screenshot after this
    overlay closes (which is what used to produce a black/stale read on
    that same Linux setup -- see CalibrationMixin, no longer applicable).

    Spans the full virtual desktop (every connected monitor's combined
    bounds -- see geometry.virtual_screen_bounds), not just the primary
    monitor's own resolution at +0+0, so a skill icon on a secondary
    monitor (including one positioned above/left of the primary, at
    negative virtual coordinates) can be calibrated too.
    """

    _HINT_FONT = ("Segoe UI", 12, "bold")
    _HINT_PAD = 8
    _TINT_WEIGHT = 0.25  # how much the gray tint contributes to the blended image -- matches the old -alpha value

    def __init__(self, master, on_done, bounds, captured_image, hint_text=None):
        super().__init__(master)
        self.on_done = on_done
        self._finished = False
        left, top, width, height = bounds
        self._overlay_width = width

        tint = Image.new("RGB", (width, height), "gray")
        tinted = Image.blend(captured_image, tint, self._TINT_WEIGHT)
        self._tk_image = ImageTk.PhotoImage(tinted)

        self.overrideredirect(True)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.attributes("-topmost", True)

        self.canvas = tk.Canvas(self, cursor="cross", highlightthickness=0, width=width, height=height)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)
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
        if _IS_WINDOWS:
            self.grab_set()
        else:
            # A plain grab_set() is a *local* grab -- it only arbitrates
            # between this application's own windows, it can't take pointer/
            # keyboard events away from a DIFFERENT X11 client. Path of
            # Exile 2 (like many games, for camera-look/click-to-move)
            # commonly holds its own active X11 pointer grab, which keeps
            # routing every click to the game even after this overlay
            # becomes the topmost, focused-looking window -- observed on
            # Steam Deck as "the overlay appears but clicking it does
            # nothing." grab_set_global() performs a real global grab,
            # which X11 allows a new requester to take over from whichever
            # client held it before, exactly what's needed here. Not done
            # on Windows: the local grab already works fine there (this
            # class of persistent OS-level exclusive input grab isn't a
            # thing on Windows the same way), and a global grab is more
            # disruptive than necessary to reach for unless it's actually
            # needed.
            self.grab_set_global()
        self.focus_force()

        # Belt-and-suspenders escape hatch, Linux only: if grab_set_global()
        # above still doesn't win back keyboard focus from the game (e.g. a
        # raw evdev-level input grab the game holds for itself, entirely
        # below X11), the plain <Escape> binding above would never fire
        # either, leaving this fullscreen, click-through-nothing overlay with
        # no way to cancel it short of killing the process -- reported on
        # Steam Deck as "stuck there with no way of continuing or
        # cancelling it." keyboard.hook() reads raw input events itself
        # (evdev on Linux) independent of which window X11 thinks has
        # focus, so Escape can always dismiss this overlay regardless of
        # what the game has grabbed. Runs on keyboard's own hook thread, not
        # Tk's -- it only ever sets a flag; _poll_escape_requested (driven by
        # self.after, so it always runs on the Tk thread) is what actually
        # acts on it.
        self._escape_requested = threading.Event()
        self._escape_poll_id = None
        self._global_escape_hook = None
        if not _IS_WINDOWS:
            def on_key_event(event):
                if event.event_type == keyboard.KEY_DOWN and event.name == "esc":
                    self._escape_requested.set()
            keyboard.hook(on_key_event)
            self._global_escape_hook = on_key_event
            self._poll_escape_requested()

    def _poll_escape_requested(self):
        if self._escape_requested.is_set():
            self._on_cancel(None)
            return
        self._escape_poll_id = self.after(_ESCAPE_POLL_MS, self._poll_escape_requested)

    def _draw_hint(self, hint_text):
        """A persistent on-canvas reminder of what to do, so the one-time
        instructional popup (shown at most once per app session -- see
        CalibrationMixin) doesn't need repeating before every capture. A
        dark backing rectangle keeps light text legible over whatever the
        captured screen image happens to show behind it. Centered on this
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
        # Guards against a double-finish: a real click/Escape landing right
        # as _poll_escape_requested's own after()-scheduled check fires (or
        # vice versa) would otherwise call self.destroy() on an
        # already-destroyed widget and invoke on_done twice.
        if self._finished:
            return
        self._finished = True
        if self._global_escape_hook is not None:
            keyboard.unhook(self._global_escape_hook)
        if self._escape_poll_id is not None:
            self.after_cancel(self._escape_poll_id)
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

    def __init__(self, master, on_done, bounds, captured_image, hint_text=None):
        super().__init__(master, on_done, bounds, captured_image, hint_text)
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

    def __init__(self, master, on_done, bounds, captured_image, hint_text=None):
        super().__init__(master, on_done, bounds, captured_image, hint_text)
        self.canvas.bind("<ButtonRelease-1>", self._on_click)

    def _on_click(self, event):
        self._finish((event.x_root, event.y_root))
