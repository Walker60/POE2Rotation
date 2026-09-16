import sys
import threading
import tkinter as tk

import keyboard
import mouse
from PIL import Image, ImageTk

from poe2bot.log_setup import get_logger

log = get_logger()

_IS_WINDOWS = sys.platform == "win32"
_INPUT_POLL_MS = 33  # ~30Hz -- Linux-only Escape/mouse polling, see _poll_linux_input


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

        # Belt-and-suspenders escape hatch, Linux only -- installed BEFORE
        # any grab attempt below, deliberately: if grab_set_global() (or
        # even wait_visibility()/focus_force()) ever raises -- a real
        # possibility, since this overlay's geometry exactly matches the
        # screen resolution, which KWin's own fullscreen-window heuristics
        # (see this class's docstring) can make transiently un-viewable --
        # an exception here would abort the rest of __init__, meaning this
        # hook (and even the subclass's own click bindings, registered
        # after super().__init__() returns) would never get installed at
        # all. That exactly reproduces "overlay stuck, nothing responds,
        # not even Escape," which is worse than not having a grab.
        # keyboard.hook() reads raw input events itself (evdev on Linux)
        # independent of which window X11 thinks has focus, so Escape can
        # always dismiss this overlay regardless of what the game has
        # grabbed. Runs on keyboard's own hook thread, not Tk's -- it only
        # ever sets a flag; _poll_linux_input (driven by self.after, so it
        # always runs on the Tk thread) is what actually acts on it.
        #
        # The same poll loop also drives raw mouse-button tracking on
        # Linux (see _poll_linux_input/_on_raw_mouse_*) -- it turned out
        # grab_set_global() can't actually be relied on either: X11 active
        # grabs cannot be preempted by a new client's grab request at all
        # ("grab failed: another application has grab" -- confirmed on
        # real Steam Deck hardware while Path of Exile 2 itself holds its
        # own pointer grab for camera-look/click-to-move). With no way to
        # win the grab back, X11 simply never delivers ButtonPress/Motion/
        # ButtonRelease events to this window's canvas at all while the
        # game holds it -- no Tk-level fix can change that, since Tk is
        # just as subject to X11's event routing as anything else. mouse.
        # is_pressed()/get_position() (like keyboard.hook() above) read
        # raw evdev state directly, the same way this app's own mouse-
        # button rotation triggers already work while the game has focus
        # (see hotkeys.py) -- entirely independent of X11 focus/grabs.
        self._escape_requested = threading.Event()
        self._input_poll_id = None
        self._global_escape_hook = None
        self._raw_mouse_was_pressed = False
        if not _IS_WINDOWS:
            def on_key_event(event):
                if event.event_type == keyboard.KEY_DOWN and event.name == "esc":
                    self._escape_requested.set()
            keyboard.hook(on_key_event)
            self._global_escape_hook = on_key_event
            self._poll_linux_input()

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
        #
        # Both grab_set()/grab_set_global() and focus_force() are wrapped
        # in try/except: any of them failing (e.g. TclError: grab failed:
        # window not viewable) must not prevent the escape hatch installed
        # above from working, or the click bindings the subclass registers
        # right after this constructor returns.
        self.wait_visibility()
        try:
            if _IS_WINDOWS:
                self.grab_set()
            else:
                # NOTE: grab_set_global() does NOT reliably win this away
                # from a game holding its own active X11 pointer grab (as
                # Path of Exile 2 commonly does, for camera-look/
                # click-to-move) -- confirmed on real Steam Deck hardware,
                # X11 refuses a new active grab outright while another
                # client already holds one ("grab failed: another
                # application has grab"), it does not hand it over. This
                # call is kept anyway since it's harmless (wrapped below)
                # and still helps when nothing else is grabbing (e.g.
                # calibrating against the bare desktop) -- but the actual
                # fix for the game-is-grabbing case is the raw mouse.
                # is_pressed()/get_position() polling below
                # (_poll_linux_input/_on_raw_mouse_*), which reads input
                # independent of X11 entirely, same idea as the Escape
                # hatch above. Not done on Windows: the local grab already
                # works fine there, and this class of persistent OS-level
                # exclusive grab isn't a thing on Windows the same way.
                self.grab_set_global()
        except tk.TclError as e:
            log.warning(f"calibration overlay: grab_set{'_global' if not _IS_WINDOWS else ''}() failed: {e}")
        try:
            self.focus_force()
        except tk.TclError as e:
            log.warning(f"calibration overlay: focus_force() failed: {e}")

    def _poll_linux_input(self):
        """Linux-only self.after() loop: acts on a pending Escape request
        (see keyboard.hook() above), and edge-detects raw left-mouse-button
        press/drag/release via mouse.is_pressed()/get_position() -- reading
        evdev state directly rather than waiting for X11 to deliver Button
        events to this window's canvas, which it never will while another
        client (e.g. the game) holds an active pointer grab (see the
        grab_set_global() comment above). Feeds the exact same _on_press/
        _on_drag/_on_release/_on_click methods the Tk canvas bindings use
        (see each subclass) -- both input paths are always active
        simultaneously on Linux, not just as a fallback, since there's no
        reliable way to know in advance whether X11 delivery will actually
        work for a given capture (e.g. it does when calibrating against the
        bare desktop with no grabbing game running). _finish() is
        idempotent and _on_press() ignores a press while one is already in
        progress, so both paths racing to report the same physical click is
        harmless.

        The mouse-reading portion is wrapped in its own try/except so a
        transient failure there (unlike the Escape check above it) can
        never stop this loop from rescheduling itself -- silently losing
        the Escape hatch too would be worse than a single missed mouse
        poll."""
        if self._escape_requested.is_set():
            self._on_cancel(None)
            return
        try:
            pressed = mouse.is_pressed("left")
            x, y = mouse.get_position()
            was_pressed = self._raw_mouse_was_pressed
            self._raw_mouse_was_pressed = pressed
            if pressed and not was_pressed:
                self._on_raw_mouse_press(x, y)
            elif pressed and was_pressed:
                self._on_raw_mouse_drag(x, y)
            elif not pressed and was_pressed:
                self._on_raw_mouse_release(x, y)
        except Exception as e:
            log.warning(f"calibration overlay: raw mouse poll failed: {e}")
        self._input_poll_id = self.after(_INPUT_POLL_MS, self._poll_linux_input)

    def _on_raw_mouse_press(self, x, y):
        """Overridden by RegionCaptureOverlay; a single-click subclass
        (PointCaptureOverlay) has nothing to do on press."""

    def _on_raw_mouse_drag(self, x, y):
        """Overridden by RegionCaptureOverlay."""

    def _on_raw_mouse_release(self, x, y):
        """Overridden by both subclasses."""

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
        # as _poll_linux_input's own after()-scheduled check fires (or a
        # click reported through both the Tk canvas binding and the raw
        # mouse poll -- see _poll_linux_input) would otherwise call
        # self.destroy() on an already-destroyed widget and invoke on_done
        # twice.
        if self._finished:
            return
        self._finished = True
        if self._global_escape_hook is not None:
            keyboard.unhook(self._global_escape_hook)
        if self._input_poll_id is not None:
            self.after_cancel(self._input_poll_id)
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
        self.canvas.bind("<ButtonPress-1>", lambda e: self._on_press(e.x_root, e.y_root))
        self.canvas.bind("<B1-Motion>", lambda e: self._on_drag(e.x_root, e.y_root))
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._on_release(e.x_root, e.y_root))

    # _on_raw_mouse_*(x, y): fed by _poll_linux_input's raw evdev-based
    # mouse.is_pressed()/get_position() polling (Linux only) -- the actual
    # fix for a game holding an X11 pointer grab, which stops the Tk canvas
    # bindings above from ever firing at all (see _poll_linux_input's
    # docstring). Both input paths call the exact same _on_press/_on_drag/
    # _on_release below.
    def _on_raw_mouse_press(self, x, y):
        self._on_press(x, y)

    def _on_raw_mouse_drag(self, x, y):
        self._on_drag(x, y)

    def _on_raw_mouse_release(self, x, y):
        self._on_release(x, y)

    def _on_press(self, x_root, y_root):
        if self._start is not None:
            return  # already tracking a press -- the other input path got here first
        self._start = (x_root, y_root)
        x_local, y_local = x_root - self.winfo_rootx(), y_root - self.winfo_rooty()
        self._rect_id = self.canvas.create_rectangle(
            x_local, y_local, x_local, y_local, outline="red", width=2)

    def _on_drag(self, x_root, y_root):
        if self._start is None:
            return
        x0, y0 = self._start
        # This rectangle is purely cosmetic drag feedback in canvas-local coordinates;
        # the authoritative region below is computed entirely from root coordinates,
        # never from an assumed canvas-local == screen equivalence.
        self.canvas.coords(
            self._rect_id,
            x0 - self.winfo_rootx(), y0 - self.winfo_rooty(),
            x_root - self.winfo_rootx(), y_root - self.winfo_rooty())

    def _on_release(self, x_root, y_root):
        if self._start is None:
            self._finish(None)
            return
        x0, y0 = self._start
        left, top = min(x0, x_root), min(y0, y_root)
        width, height = abs(x_root - x0), abs(y_root - y0)
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
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._on_click(e.x_root, e.y_root))

    # See RegionCaptureOverlay's _on_raw_mouse_* -- same reasoning, fed by
    # _poll_linux_input's raw evdev-based polling (Linux only).
    def _on_raw_mouse_release(self, x, y):
        self._on_click(x, y)

    def _on_click(self, x_root, y_root):
        self._finish((x_root, y_root))
