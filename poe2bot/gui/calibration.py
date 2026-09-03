import tkinter as tk
from tkinter import ttk

import pyautogui

from poe2bot import templates
from poe2bot.gui import dialogs as messagebox
from poe2bot.gui import theme
from poe2bot.gui.overlays import RegionCaptureOverlay, PointCaptureOverlay


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

    def _start_image_capture(self, on_use, default_confidence=0.90):
        """Runs the region-capture-overlay flow, ending in an image-match
        preview. "Use This" calls on_use(filename, region, confidence,
        search_mode, search_region) -- every Condition (Add or recalibrate)
        goes through this same callback, there's no other consumer.
        search_mode/search_region come from the optional second click-drag
        pass offered in the preview dialog -- see _show_image_match_preview
        and _start_search_area_capture."""
        self._show_calibration_hint_once(
            "Calibrate image match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click-drag a small rectangle tightly around just that icon and "
            "release the mouse button.\n\nPress Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_region_capture_overlay(on_use, default_confidence))

    def _open_region_capture_overlay(self, on_use, default_confidence=0.90):
        RegionCaptureOverlay(
            self, on_done=lambda region: self._on_image_region_captured(region, on_use, default_confidence),
            hint_text="Click-drag tightly around the icon  ·  Esc to cancel")

    def _on_image_region_captured(self, region, on_use, default_confidence=0.90):
        if region is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # captured template isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._take_image_match_screenshot(region, on_use, default_confidence))

    def _take_image_match_screenshot(self, region, on_use, default_confidence=0.90):
        filename = templates.new_template_filename()
        path = templates.template_path(filename)
        try:
            templates.ensure_dir()
            pyautogui.screenshot(region=region).save(path)
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Calibration failed", f"Could not capture the region:\n{e}")
            return
        self.deiconify()
        self._show_image_match_preview(filename, region, on_use, default_confidence)

    def _show_image_match_preview(self, filename, region, on_use, default_confidence=0.90,
                                   inline_error=None):
        """inline_error, when set, shows a warning line above the "search a
        larger area" checkbox instead of a separate popup-then-reopen round
        trip -- used when re-showing this preview after a search-area
        capture was cancelled or came back too small (see
        _on_search_area_captured). The checkbox starts pre-checked in that
        case too, since the user has already shown they want a search area."""
        preview = tk.Toplevel(self)
        preview.title("Confirm image match")
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            preview.configure(bg=bg)

        image = tk.PhotoImage(file=templates.template_path(filename))
        preview.image = image  # keep a reference -- Tk drops it otherwise
        ttk.Label(preview, image=image).pack(padx=8, pady=8)
        ttk.Label(preview, text=f"{region[2]} x {region[3]} px at ({region[0]}, {region[1]})").pack()

        if inline_error:
            ttk.Label(preview, text=inline_error, foreground=theme.DANGER_COLOR,
                      wraplength=280, justify="left").pack(padx=8, pady=(6, 0))

        conf_row = ttk.Frame(preview)
        conf_row.pack(pady=(4, 0))
        ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
        confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
        ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        area_var = tk.BooleanVar(value=bool(inline_error))
        ttk.Checkbutton(preview, variable=area_var,
                        text="Search a larger area (icon may shift slightly)").pack(pady=(4, 0))

        btns = ttk.Frame(preview, padding=8)
        btns.pack(pady=(4, 0))

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

        ttk.Button(btns, text="Use This", style=theme.ACCENT_BUTTON_STYLE, command=use_this).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", style=theme.DANGER_BUTTON_STYLE, command=cancel).pack(
            side="left", padx=4)

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
            "After you click OK, the bot window will hide again.\n\n"
            "Click-drag a LARGER rectangle that comfortably contains the icon, "
            "giving it room to be found even if it shifts slightly. It must be "
            "at least as large as the icon you just captured.\n\n"
            "Press Escape to cancel and return to the previous confirmation.")
        self.withdraw()
        self.after(150, lambda: self._open_search_area_overlay(filename, region, confidence, on_use))

    def _open_search_area_overlay(self, filename, region, confidence, on_use):
        RegionCaptureOverlay(
            self, on_done=lambda search_region: self._on_search_area_captured(
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

    def _start_pixel_capture(self, on_use, default_confidence=0.90):
        """Same shape as _start_image_capture, for the pixel-match flow."""
        self._show_calibration_hint_once(
            "Calibrate pixel match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click exactly on the pixel you want to check.\n\n"
            "Press Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_point_capture_overlay(on_use, default_confidence))

    def _open_point_capture_overlay(self, on_use, default_confidence=0.90):
        PointCaptureOverlay(
            self, on_done=lambda point: self._on_point_captured(point, on_use, default_confidence),
            hint_text="Click exactly on the pixel  ·  Esc to cancel")

    def _on_point_captured(self, point, on_use, default_confidence=0.90):
        if point is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # sampled color isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._sample_pixel_color(point, on_use, default_confidence))

    def _sample_pixel_color(self, point, on_use, default_confidence=0.90):
        try:
            color = pyautogui.screenshot(region=(point[0], point[1], 1, 1)).getpixel((0, 0))
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Calibration failed", f"Could not sample the pixel:\n{e}")
            return
        self.deiconify()
        self._show_pixel_match_preview(point, color, on_use, default_confidence)

    def _show_pixel_match_preview(self, point, color, on_use, default_confidence=0.90):
        preview = tk.Toplevel(self)
        preview.title("Confirm pixel match")
        preview.transient(self)
        preview.grab_set()
        preview.resizable(False, False)
        bg = ttk.Style().lookup("TFrame", "background")
        if bg:
            preview.configure(bg=bg)

        swatch = tk.Canvas(preview, width=60, height=60, highlightthickness=1)
        swatch.pack(padx=8, pady=8)
        swatch.create_rectangle(1, 1, 59, 59, fill="#%02x%02x%02x" % color, outline="")
        ttk.Label(preview, text=f"RGB {color} at ({point[0]}, {point[1]})").pack(pady=(0, 8))

        conf_row = ttk.Frame(preview)
        conf_row.pack(pady=(0, 4))
        ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
        confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
        ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        btns = ttk.Frame(preview, padding=8)
        btns.pack()

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

        ttk.Button(btns, text="Use This", style=theme.ACCENT_BUTTON_STYLE, command=use_this).pack(
            side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", style=theme.DANGER_BUTTON_STYLE, command=preview.destroy).pack(
            side="left", padx=4)
