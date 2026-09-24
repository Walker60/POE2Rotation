import threading
import tkinter as tk
from tkinter import ttk

from poe2bot import focus, templates
from poe2bot.executor import calibration_scale_note, capture_region, check_condition_now, rescaled_pixel_pos
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui import geometry, theme
from poe2bot.gui.controller_map_window import ControllerMapWindow
from poe2bot.gui.overlays import RegionCaptureOverlay, PointCaptureOverlay
from poe2bot.hotkeys import display_name
from poe2bot.log_setup import get_logger

log = get_logger()

_MATCH_COLOR = "#50fa7b"    # Dracula green -- reads as "good" regardless of theme
_NO_MATCH_COLOR = "#ff5555"  # Dracula red == theme.DANGER_COLOR

_DEFAULT_CONFIDENCE = 0.90
_HIDE_WINDOW_DELAY_MS = 150   # lets self.withdraw() actually finish hiding before an overlay opens


def _crop_from_captured(captured_image, bounds, region):
    """The (left, top, width, height) `region` (absolute screen pixels),
    cropped out of `captured_image` -- which itself covers `bounds`
    (also absolute screen pixels, not necessarily starting at the
    screen's own (0, 0) -- see geometry.virtual_screen_bounds), so a
    region's coordinates need shifting to be relative to the image's own
    (0, 0) before cropping."""
    bounds_left, bounds_top, _bounds_width, _bounds_height = bounds
    left, top, width, height = region
    rel_left, rel_top = left - bounds_left, top - bounds_top
    return captured_image.crop((rel_left, rel_top, rel_left + width, rel_top + height))


class CalibrationMixin:
    """Image/pixel/search-area capture-overlay machinery, shared by every
    Condition calibration flow (ConditionsMixin's Add/recalibrate Condition).
    Mixed into App (see poe2bot/gui/app.py)."""

    def _show_calibration_hint_once(self, title: str, message: str):
        """The full instructional popup (what a capture involves, how to
        cancel) is only genuinely needed the first time a user does this in
        a session -- every subsequent capture reuses the same mechanic, and
        the overlay itself now carries a persistent on-canvas reminder (see
        overlays.py's `hint_text`), so repeating the popup every single time
        is pure friction, not information."""
        if self._calibration_hint_shown:
            return
        self._calibration_hint_shown = True
        messagebox.showinfo(title, message)

    def _capture_full_screen_for_overlay(self):
        """(bounds, captured_image): bounds is (left, top, width, height)
        covering the full virtual desktop (see geometry.virtual_screen_bounds,
        falling back to just the primary monitor if that Win32-only query
        isn't available); captured_image is that exact region screenshotted
        right now, via capture_region (mss). Every capture-overlay flow needs
        this same pairing: an image to display as the overlay's own
        background (see overlays.py's _FullscreenPickerOverlay for why this
        no longer relies on the window manager's own transparency), sized
        and positioned to exactly match where the overlay itself will sit --
        and, once the user clicks, the same image is what calibration crops/
        samples the final result from (see _crop_from_captured), rather than
        needing a second, separately-timed screenshot after the overlay
        closes."""
        bounds = geometry.virtual_screen_bounds()
        if bounds:
            left, top, width, height = bounds
        else:
            left, top = 0, 0
            width, height = self.winfo_screenwidth(), self.winfo_screenheight()
        bounds = (left, top, width, height)
        image = capture_region(bounds)
        # Diagnostic for the "overlay renders as a flat gray/black" reports:
        # getextrema() gives (min, max) per channel -- a real screenshot has
        # a wide spread; (0, 0) on every channel means capture_region itself
        # got back solid black (e.g. mss/X11 unable to see a directly-scanned-out
        # exclusive-fullscreen game surface, or a GPU-composited one), which
        # would explain the flat-color overlay independently of anything this
        # module or overlays.py does with the image afterward.
        log.info(f"calibration capture: bounds={bounds} size={image.size} "
                  f"extrema(min,max)/channel={image.getextrema()}")
        return bounds, image

    def _capture_intro_line(self, again: bool = False) -> str:
        """The first line of every calibration hint popup -- what happens
        right after the user clicks OK. Differs depending on whether a
        Screen Grab Hotkey is configured (Settings): with one set, nothing
        happens until it's physically pressed (possibly much later, from
        inside the game); without one, the capture is immediate, exactly as
        before. Shared by _start_image_capture/_start_pixel_capture/
        _start_search_area_capture's hint text."""
        hotkey = self.hotkey_manager.screen_grab_hotkey
        if hotkey:
            return (f"When you're ready, press {display_name(hotkey)} -- from anywhere, even "
                     "with another window focused -- to grab the screen.")
        return f"After you click OK, the bot window will hide{' again' if again else ''}."

    def _start_image_capture(self, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        """Runs the region-capture-overlay flow, ending in an image-match
        preview. "Use This" calls on_use(filename, region, confidence,
        search_mode, search_region) -- every Condition (Add or recalibrate)
        goes through this same callback, there's no other consumer.
        search_mode/search_region come from the optional second click-drag
        pass offered in the preview dialog -- see _show_image_match_preview
        and _start_search_area_capture. If a Screen Grab Hotkey is
        configured, the actual capture waits for it instead of running
        immediately -- see _await_grab_hotkey_or_capture_now."""
        self._show_calibration_hint_once(
            "Calibrate image match",
            f"{self._capture_intro_line()}\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click-drag a small rectangle tightly around just that icon and "
            "release the mouse button.\n\nPress Escape at any time to cancel.")

        def capture_fn():
            self.withdraw()
            self.after(_HIDE_WINDOW_DELAY_MS, lambda: self._open_region_capture_overlay(on_use, default_confidence))
        self._await_grab_hotkey_or_capture_now(capture_fn)

    def _open_region_capture_overlay(self, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        bounds, captured_image = self._capture_full_screen_for_overlay()
        RegionCaptureOverlay(
            self, bounds=bounds, captured_image=captured_image,
            on_done=lambda region: self._on_image_region_captured(
                region, bounds, captured_image, on_use, default_confidence),
            hint_text="Click-drag tightly around the icon  ·  Esc to cancel")

    def _on_image_region_captured(self, region, bounds, captured_image, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        self.deiconify()
        if region is None:
            return
        self._take_image_match_screenshot(region, bounds, captured_image, on_use, default_confidence)

    def _take_image_match_screenshot(self, region, bounds, captured_image, on_use,
                                      default_confidence=_DEFAULT_CONFIDENCE):
        filename = templates.new_template_filename()
        path = templates.template_path(filename)
        try:
            templates.ensure_dir()
            _crop_from_captured(captured_image, bounds, region).save(path)
        except Exception as e:
            messagebox.showerror("Calibration failed", f"Could not capture the region:\n{e}")
            return
        self._last_calib_rect = focus.game_window_client_rect()
        self._show_image_match_preview(filename, region, on_use, default_confidence)

    def _calib_size_kwargs(self) -> dict:
        """calib_width/calib_height/calib_left/calib_top kwargs for a
        freshly-constructed Condition, from whatever _last_calib_rect was
        captured at the most recent screenshot
        (_take_image_match_screenshot/_sample_pixel_color) -- shared by
        every Condition(...) construction site in ConditionsMixin/
        ConditionGroupsMixin so they don't each need to unpack the
        None-or-(l, t, w, h) tuple themselves. All None (no rescaling
        reference recorded) if the game window couldn't be found at
        calibration time -- matches this condition's behavior before
        poe2bot/scaling.py existed."""
        left, top, width, height = self._last_calib_rect or (None, None, None, None)
        return {"calib_width": width, "calib_height": height, "calib_left": left, "calib_top": top}

    def _build_preview_dialog(self, title: str) -> tk.Toplevel:
        """Shared Toplevel setup behind _show_image_match_preview() and
        _show_pixel_match_preview(): themed background, modal/transient to
        the main window, no resize."""
        preview = tk.Toplevel(self)
        preview.title(title)
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            preview.configure(bg=bg)
        return preview

    def _build_confidence_row(self, parent, default_confidence, pady=(4, 0)) -> tk.StringVar:
        """Shared "Confidence [___]" row behind both preview dialogs;
        returns the StringVar its entry is bound to."""
        conf_row = ttk.Frame(parent)
        conf_row.pack(pady=pady)
        ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
        confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
        ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")
        return confidence_var

    def _build_use_retry_cancel_buttons(self, parent, use_this, retry, cancel, pady=(4, 0)):
        """Shared "Use This / Retry / Cancel" button row behind both preview
        dialogs."""
        btns = ttk.Frame(parent, padding=8)
        btns.pack(pady=pady)
        ttk.Button(btns, text="Use This", style=theme.ACCENT_BUTTON_STYLE, command=use_this).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", style=theme.DANGER_BUTTON_STYLE, command=cancel).pack(
            side="left", padx=4)

    def _show_image_match_preview(self, filename, region, on_use, default_confidence=_DEFAULT_CONFIDENCE,
                                   inline_error=None):
        """inline_error, when set, shows a warning line above the "search a
        larger area" checkbox instead of a separate popup-then-reopen round
        trip -- used when re-showing this preview after a search-area
        capture was cancelled or came back too small (see
        _on_search_area_captured). The checkbox starts pre-checked in that
        case too, since the user has already shown they want a search area."""
        preview = self._build_preview_dialog("Confirm image match")

        image = tk.PhotoImage(file=templates.template_path(filename))
        preview.image = image  # keep a reference -- Tk drops it otherwise
        ttk.Label(preview, image=image).pack(padx=8, pady=8)
        ttk.Label(preview, text=f"{region[2]} x {region[3]} px at ({region[0]}, {region[1]})").pack()

        if inline_error:
            ttk.Label(preview, text=inline_error, foreground=theme.DANGER_COLOR,
                      wraplength=280, justify="left").pack(padx=8, pady=(6, 0))

        confidence_var = self._build_confidence_row(preview, default_confidence)

        area_var = tk.BooleanVar(value=bool(inline_error))
        ttk.Checkbutton(preview, variable=area_var,
                        text="Search a larger area (icon may shift slightly)").pack(pady=(4, 0))

        def use_this():
            try:
                confidence = float(confidence_var.get())
            except ValueError:
                messagebox.showerror("Invalid confidence", "Confidence must be a decimal (e.g. 0.9).")
                return
            if area_var.get():
                # NOT deleting the template file -- it's confirmed, it just still
                # needs a search area before this calibration can be finalized.
                preview.destroy()
                self._start_search_area_capture(filename, region, confidence, on_use)
                return
            preview.destroy()
            on_use(filename, region, confidence, "exact", None)

        def retry():
            templates.delete_template(filename)  # safe: nothing has ever referenced it
            preview.destroy()
            self._start_image_capture(on_use, default_confidence)

        def cancel():
            templates.delete_template(filename)  # safe: same as above
            preview.destroy()

        self._build_use_retry_cancel_buttons(preview, use_this, retry, cancel)

    def _start_search_area_capture(self, filename, region, confidence, on_use):
        """Second calibration pass, run only when the "search a larger area"
        checkbox was ticked in _show_image_match_preview: click-drag a larger
        rectangle to search within instead of comparing only the exact
        calibrated spot. filename/region/confidence/on_use are the
        already-confirmed values from that first pass, carried through
        unchanged so they can be finalized once this second rectangle is
        captured, or so the confirmation dialog can be redisplayed unchanged
        (with `confidence` as its new default) if this pass is cancelled or
        the rectangle turns out too small."""
        self._show_calibration_hint_once(
            "Calibrate search area",
            f"{self._capture_intro_line(again=True)}\n\n"
            "Click-drag a LARGER rectangle that comfortably contains the icon, "
            "giving it room to be found even if it shifts slightly. It must be "
            "at least as large as the icon you just captured.\n\n"
            "Press Escape to cancel and return to the previous confirmation.")

        def capture_fn():
            self.withdraw()
            self.after(_HIDE_WINDOW_DELAY_MS,
                       lambda: self._open_search_area_overlay(filename, region, confidence, on_use))
        self._await_grab_hotkey_or_capture_now(capture_fn)

    def _open_search_area_overlay(self, filename, region, confidence, on_use):
        bounds, captured_image = self._capture_full_screen_for_overlay()
        RegionCaptureOverlay(
            self, bounds=bounds, captured_image=captured_image,
            on_done=lambda search_region: self._on_search_area_captured(
                search_region, filename, region, confidence, on_use),
            hint_text="Drag a LARGER rectangle around the icon  ·  Esc to cancel")

    def _on_search_area_captured(self, search_region, filename, region, confidence, on_use):
        self.deiconify()
        if search_region is None:
            self._show_image_match_preview(
                filename, region, on_use, confidence,
                inline_error="Search area capture cancelled -- the icon itself is still confirmed. "
                             "Check the box and click \"Use This\" again to retry, or leave it "
                             "unchecked to use exact mode instead.")
            return
        if search_region[2] < region[2] or search_region[3] < region[3]:
            self._show_image_match_preview(
                filename, region, on_use, confidence,
                inline_error=f"Search area ({search_region[2]}x{search_region[3]}) must be at least "
                             f"as large as the icon ({region[2]}x{region[3]}) in both dimensions -- "
                             f"try again.")
            return
        on_use(filename, region, confidence, "area", search_region)

    def _start_pixel_capture(self, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        """Same shape as _start_image_capture, for the pixel-match flow."""
        self._show_calibration_hint_once(
            "Calibrate pixel match",
            f"{self._capture_intro_line()}\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click exactly on the pixel you want to check.\n\n"
            "Press Escape at any time to cancel.")

        def capture_fn():
            self.withdraw()
            self.after(_HIDE_WINDOW_DELAY_MS, lambda: self._open_point_capture_overlay(on_use, default_confidence))
        self._await_grab_hotkey_or_capture_now(capture_fn)

    def _open_point_capture_overlay(self, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        bounds, captured_image = self._capture_full_screen_for_overlay()
        PointCaptureOverlay(
            self, bounds=bounds, captured_image=captured_image,
            on_done=lambda point: self._on_point_captured(point, bounds, captured_image, on_use, default_confidence),
            hint_text="Click exactly on the pixel  ·  Esc to cancel")

    def _on_point_captured(self, point, bounds, captured_image, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        self.deiconify()
        if point is None:
            return
        self._sample_pixel_color(point, bounds, captured_image, on_use, default_confidence)

    def _sample_pixel_color(self, point, bounds, captured_image, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        try:
            bounds_left, bounds_top, _bounds_width, _bounds_height = bounds
            color = captured_image.getpixel((point[0] - bounds_left, point[1] - bounds_top))
        except Exception as e:
            messagebox.showerror("Calibration failed", f"Could not sample the pixel:\n{e}")
            return
        self._last_calib_rect = focus.game_window_client_rect()
        self._show_pixel_match_preview(point, color, on_use, default_confidence)

    def _show_pixel_match_preview(self, point, color, on_use, default_confidence=_DEFAULT_CONFIDENCE):
        preview = self._build_preview_dialog("Confirm pixel match")

        swatch = tk.Canvas(preview, width=60, height=60, highlightthickness=1)
        swatch.pack(padx=8, pady=8)
        swatch.create_rectangle(1, 1, 59, 59, fill="#%02x%02x%02x" % color, outline="")
        ttk.Label(preview, text=f"RGB {color} at ({point[0]}, {point[1]})").pack(pady=(0, 8))

        confidence_var = self._build_confidence_row(preview, default_confidence, pady=(0, 4))

        def use_this():
            try:
                confidence = float(confidence_var.get())
            except ValueError:
                messagebox.showerror("Invalid confidence", "Confidence must be a decimal (e.g. 0.9).")
                return
            preview.destroy()
            on_use(point, color, confidence)

        def retry():
            preview.destroy()
            self._start_pixel_capture(on_use, default_confidence)

        self._build_use_retry_cancel_buttons(preview, use_this, retry, preview.destroy, pady=0)

    def _show_test_match_result(self, condition):
        """Live "does this match right now" check for `condition`'s Test
        Match button (ConditionsMixin's Skill Conditions, or
        ConditionGroupsMixin's Rotation Conditions) -- shows a big MATCH/NO
        MATCH verdict next to whatever's actually useful to eyeball: the
        saved template thumbnail for an image condition, or a live-vs-saved
        color swatch pair for a pixel one (cheap enough to screenshot just
        for display, unlike a whole template image). Never re-runs the
        calibration overlay -- this only ever reads the current screen, it
        never changes what's calibrated. Timer conditions have no
        meaningful "right now" outside a running rotation, so callers
        should route those to a plain message instead of calling this."""
        matched = check_condition_now(condition)
        preview = self._build_preview_dialog("Test Match")

        if condition.match_type == "pixel":
            try:
                live_x, live_y = rescaled_pixel_pos(condition)
                live_color = capture_region((live_x, live_y, 1, 1)).getpixel((0, 0))
            except Exception:
                live_color = None
            row = ttk.Frame(preview)
            row.pack(padx=8, pady=8)
            for label, color in (("Saved", condition.pixel_color), ("Live", live_color)):
                col = ttk.Frame(row)
                col.pack(side="left", padx=8)
                swatch = tk.Canvas(col, width=50, height=50, highlightthickness=1)
                swatch.pack()
                if color is not None:
                    swatch.create_rectangle(1, 1, 49, 49, fill="#%02x%02x%02x" % color, outline="")
                ttk.Label(col, text=f"{label}: {color if color is not None else '?'}").pack(pady=(2, 0))
        else:
            try:
                image = tk.PhotoImage(file=templates.template_path(condition.template))
                preview.image = image  # keep a reference -- Tk drops it otherwise
                ttk.Label(preview, image=image).pack(padx=8, pady=8)
                ttk.Label(preview, text="(saved template shown above)", foreground="gray").pack()
            except Exception as e:
                ttk.Label(preview, text=f"Could not load saved template: {e}",
                          foreground=theme.DANGER_COLOR, wraplength=280, justify="left").pack(padx=8, pady=8)

        ttk.Label(preview, text="MATCH" if matched else "NO MATCH",
                  foreground=(_MATCH_COLOR if matched else _NO_MATCH_COLOR),
                  font=("Segoe UI", 14, "bold")).pack(pady=(4, 0))
        scale_note = calibration_scale_note(condition)
        if scale_note:
            ttk.Label(preview, text=scale_note, foreground="gray").pack()
        if condition.negate:
            ttk.Label(preview, text="(Negate is on -- this already reflects the inverted result)",
                      foreground="gray").pack(pady=(0, 8))
        else:
            ttk.Frame(preview, height=8).pack()
        ttk.Button(preview, text="OK", style=theme.ACCENT_BUTTON_STYLE, command=preview.destroy).pack(pady=(0, 8))

    # ---- Screen Grab Hotkey: arm-and-wait instead of capturing immediately -----
    # Lets a user start an Image/Pixel Condition capture, alt-tab into the game,
    # and fire the actual screenshot with a global hotkey once whatever they want
    # to capture (a transient buff icon, a telegraph, etc.) is actually on screen
    # -- instead of having to already be looking at it before switching to poe2bot
    # and clicking Add. Purely additive: with no Screen Grab Hotkey configured
    # (Settings), _await_grab_hotkey_or_capture_now falls straight through to
    # capture_fn(), i.e. today's immediate-capture-on-click behavior.

    def _await_grab_hotkey_or_capture_now(self, capture_fn):
        """Runs `capture_fn` (the withdraw-then-screenshot tail of
        _start_image_capture/_start_pixel_capture/_start_search_area_capture)
        right away if no Screen Grab Hotkey is configured, or defers it to
        _on_screen_grab_hotkey_pressed via a "Waiting for Screen Grab"
        dialog if one is."""
        hotkey = self.hotkey_manager.screen_grab_hotkey
        if not hotkey:
            capture_fn()
            return
        self._show_grab_waiting_dialog(hotkey, capture_fn)

    def _show_grab_waiting_dialog(self, hotkey: str, capture_fn):
        """A small dialog that just says what to press and how to back out
        -- deliberately NOT withdrawing the main window (unlike capture_fn
        itself): the whole point is the user is free to leave poe2bot
        running in the background and go do something else (alt-tab to the
        game) while this waits. transient()+grab_set() (same as
        _build_preview_dialog) keeps it from being interacted "around" by
        other poe2bot windows without blocking alt-tabbing to a different
        application entirely -- grab_set() is local to this Tk app."""
        self._pending_grab_capture_fn = capture_fn
        dialog = tk.Toplevel(self)
        dialog.title("Waiting for Screen Grab")
        dialog.transient(self)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            dialog.configure(bg=bg)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack()
        ttk.Label(frame, justify="center",
                  text=f"Press {display_name(hotkey)} any time -- even with another\n"
                       "window focused -- to grab the screen.").pack(pady=(0, 8))
        ttk.Label(frame, text="Waiting...", foreground="gray").pack()
        ttk.Button(frame, text="Cancel", style=theme.DANGER_BUTTON_STYLE,
                   command=self._cancel_grab_wait).pack(pady=(12, 0))
        dialog.protocol("WM_DELETE_WINDOW", self._cancel_grab_wait)
        dialog.bind("<Escape>", lambda _e: self._cancel_grab_wait())
        self._grab_waiting_dialog = dialog
        dialog.grab_set()
        armed = self.hotkey_manager.arm_screen_grab(
            lambda: self.status_queue.put(("__screen_grab_fired__", None)))
        if not armed:
            # Shouldn't happen -- _await_grab_hotkey_or_capture_now already checked
            # `hotkey` was truthy -- but stay safe rather than leave the user staring
            # at a dialog that can never fire.
            self._pending_grab_capture_fn = None
            self._grab_waiting_dialog = None
            dialog.destroy()
            capture_fn()

    def _cancel_grab_wait(self):
        self.hotkey_manager.disarm_screen_grab()
        self._pending_grab_capture_fn = None
        dialog = self._grab_waiting_dialog
        self._grab_waiting_dialog = None
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()

    def _on_screen_grab_hotkey_pressed(self, _payload):
        """The configured Screen Grab Hotkey was physically pressed while a
        "Waiting for Screen Grab" dialog was up (dispatched here via
        App._CAPTURE_SENTINEL_HANDLERS/_poll_status_queue, from whichever
        thread the keyboard/mouse/controller library called back on -- see
        HotkeyManager.arm_screen_grab). Runs the deferred capture_fn FIRST
        (it calls self.withdraw() synchronously, hiding the main window)
        and only then destroys the waiting dialog -- that ordering avoids a
        brief focus flash from destroying the dialog while its still-visible
        owner briefly regains focus."""
        self.hotkey_manager.disarm_screen_grab()
        capture_fn = self._pending_grab_capture_fn
        self._pending_grab_capture_fn = None
        dialog = self._grab_waiting_dialog
        self._grab_waiting_dialog = None
        if capture_fn is not None:
            capture_fn()
        if dialog is not None and dialog.winfo_exists():
            dialog.destroy()

    # ---- Screen Grab Hotkey: configuring which key/button it is (Settings) -----

    def _set_screen_grab_settings_buttons_enabled(self, enabled: bool):
        """No-op if Settings has never been opened this session (it's
        created lazily -- see self.settings_window's own comment in
        __init__) -- there's nothing to disable if it doesn't exist yet."""
        if self.settings_window is not None:
            self.settings_window.set_screen_grab_buttons_enabled(enabled)

    def _refresh_screen_grab_settings_label(self):
        if self.settings_window is not None:
            self.settings_window.refresh_screen_grab_label()

    def _on_bind_screen_grab_hotkey_clicked(self):
        self._maybe_show_no_gamepad_hint()
        self._set_screen_grab_settings_buttons_enabled(False)
        threading.Thread(target=self._capture_screen_grab_hotkey_worker, daemon=True).start()

    def _capture_screen_grab_hotkey_worker(self):
        key = self.hotkey_manager.capture_next_key()
        self.status_queue.put(("__screen_grab_bind_captured__", key))

    def _on_screen_grab_hotkey_bind_captured(self, key: str):
        self.screen_grab_hotkey = key
        self.hotkey_manager.set_screen_grab_hotkey(key)
        self._persist_app_state()
        self._set_screen_grab_settings_buttons_enabled(True)
        self._refresh_screen_grab_settings_label()

    def _on_map_screen_grab_hotkey_clicked(self):
        self._set_screen_grab_settings_buttons_enabled(False)
        ControllerMapWindow(
            self, self.controller_type, self._on_screen_grab_hotkey_bind_captured,
            on_close=lambda: self._set_screen_grab_settings_buttons_enabled(True))

    def _on_unbind_screen_grab_hotkey_clicked(self):
        self.screen_grab_hotkey = None
        self.hotkey_manager.set_screen_grab_hotkey(None)
        self._persist_app_state()
        self._refresh_screen_grab_settings_label()
