import tkinter as tk
from tkinter import messagebox, ttk

import pyautogui

from poe2bot import templates
from poe2bot.models import Condition
from poe2bot.gui.overlays import RegionCaptureOverlay, PointCaptureOverlay


class CalibrationMixin:
    """Cooldown-check and Buff-check calibration, plus the shared image/pixel/
    search-area capture-overlay machinery both of those (and ConditionsMixin's
    Add/recalibrate Condition flows) reuse. Mixed into App (see
    poe2bot/gui/app.py)."""

    # ---- cooldown-check calibration -----------------------------------------

    def _clear_ready_check(self):
        self.step_ready_match_type = "image"
        self.step_ready_template = None
        self.step_ready_region = None
        self.step_ready_search_mode = "exact"
        self.step_ready_search_region = None
        self.step_ready_pixel_pos = None
        self.step_ready_pixel_color = None
        self._refresh_ready_status()

    def _on_image_match_clicked(self):
        self._start_image_capture()

    # ---- buff-check calibration ----------------------------------------------

    def _clear_buff_check(self):
        self.step_buff_check = None
        self._refresh_buff_status()

    def _on_buff_image_match_clicked(self):
        default_confidence = self.step_buff_check.confidence if self.step_buff_check else 0.90
        self._start_image_capture(on_use=self._set_buff_image_match, default_confidence=default_confidence)

    def _on_buff_pixel_match_clicked(self):
        default_confidence = self.step_buff_check.confidence if self.step_buff_check else 0.90
        self._start_pixel_capture(on_use=self._set_buff_pixel_match, default_confidence=default_confidence)

    def _set_buff_image_match(self, filename, region, confidence, search_mode="exact", search_region=None):
        self.step_buff_check = Condition(match_type="image", template=filename, region=region, confidence=confidence,
                                          search_mode=search_mode, search_region=search_region)
        self._refresh_buff_status()

    def _set_buff_pixel_match(self, point, color, confidence):
        self.step_buff_check = Condition(match_type="pixel", pixel_pos=point, pixel_color=color, confidence=confidence)
        self._refresh_buff_status()

    def _start_image_capture(self, on_use=None, default_confidence=0.90):
        """Runs the region-capture-overlay flow, ending in an image-match preview.
        With `on_use` set, "Use This" calls on_use(filename, region, confidence,
        search_mode, search_region) and shows a Confidence field (pre-filled from
        `default_confidence`) instead of the default behavior of staging the
        step's own cooldown check into self.step_ready_* (used for Add/
        Recalibrate Condition). search_mode/search_region come from the optional
        second click-drag pass offered in the preview dialog -- see
        _show_image_match_preview and _start_search_area_capture."""
        messagebox.showinfo(
            "Calibrate image match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click-drag a small rectangle tightly around just that icon and "
            "release the mouse button.\n\nPress Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_region_capture_overlay(on_use, default_confidence))

    def _open_region_capture_overlay(self, on_use=None, default_confidence=0.90):
        RegionCaptureOverlay(
            self, on_done=lambda region: self._on_image_region_captured(region, on_use, default_confidence))

    def _on_image_region_captured(self, region, on_use=None, default_confidence=0.90):
        if region is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # captured template isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._take_image_match_screenshot(region, on_use, default_confidence))

    def _take_image_match_screenshot(self, region, on_use=None, default_confidence=0.90):
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

    def _show_image_match_preview(self, filename, region, on_use=None, default_confidence=0.90):
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

        confidence_var = None
        if on_use is not None:
            conf_row = ttk.Frame(preview)
            conf_row.pack(pady=(4, 0))
            ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
            confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
            ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        area_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(preview, variable=area_var,
                        text="Search a larger area (icon may shift slightly)").pack(pady=(4, 0))

        btns = ttk.Frame(preview, padding=8)
        btns.pack(pady=(4, 0))

        def use_this():
            confidence = default_confidence
            if on_use is not None:
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
            if on_use is not None:
                on_use(filename, region, confidence, "exact", None)
                return
            self._apply_image_match_to_ready_form(filename, region, "exact", None)

        def retry():
            templates.delete_template(filename)  # safe: nothing has ever referenced it
            preview.destroy()
            self._start_image_capture(on_use, default_confidence)

        def cancel():
            templates.delete_template(filename)  # safe: same as above
            preview.destroy()

        ttk.Button(btns, text="Use This", command=use_this).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side="left", padx=4)

    def _apply_image_match_to_ready_form(self, filename, region, search_mode, search_region):
        """Finalizes an image-match calibration into the step's own cooldown
        check (the on_use-is-None path through _show_image_match_preview /
        _on_search_area_captured). Deliberately does NOT delete any
        previously-calibrated file for this step -- that file may still be
        referenced by a committed Step until "Update Selected"/"Save Rotation"
        runs. Orphans are cleaned up by the periodic sweep instead (see
        _sweep_templates)."""
        self.step_ready_match_type = "image"
        self.step_ready_template = filename
        self.step_ready_region = region
        self.step_ready_search_mode = search_mode
        self.step_ready_search_region = search_region
        self.step_ready_pixel_pos = None
        self.step_ready_pixel_color = None
        self._refresh_ready_status()

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
        messagebox.showinfo(
            "Calibrate search area",
            "After you click OK, the bot window will hide again.\n\n"
            "Click-drag a LARGER rectangle that comfortably contains the icon, "
            "giving it room to be found even if it shifts slightly. It must be "
            "at least as large as the icon you just captured.\n\n"
            "Press Escape to cancel and return to the previous confirmation.")
        self.withdraw()
        self.after(150, lambda: self._open_search_area_overlay(filename, region, confidence, on_use))

    def _open_search_area_overlay(self, filename, region, confidence, on_use):
        RegionCaptureOverlay(self, on_done=lambda search_region: self._on_search_area_captured(
            search_region, filename, region, confidence, on_use))

    def _on_search_area_captured(self, search_region, filename, region, confidence, on_use):
        self.deiconify()
        if search_region is None:
            messagebox.showinfo(
                "Search area cancelled",
                "Search area capture was cancelled. The icon capture from before is "
                "still confirmed -- check the box and click \"Use This\" again to "
                "retry the search area, or leave it unchecked to use exact mode instead.")
            self._show_image_match_preview(filename, region, on_use, confidence)
            return
        if search_region[2] < region[2] or search_region[3] < region[3]:
            messagebox.showerror(
                "Search area too small",
                f"The search area ({search_region[2]}x{search_region[3]}) must be at least as "
                f"large as the icon ({region[2]}x{region[3]}) in both dimensions. Try again.")
            self._show_image_match_preview(filename, region, on_use, confidence)
            return
        if on_use is not None:
            on_use(filename, region, confidence, "area", search_region)
            return
        self._apply_image_match_to_ready_form(filename, region, "area", search_region)

    def _on_pixel_match_clicked(self):
        self._start_pixel_capture()

    def _start_pixel_capture(self, on_use=None, default_confidence=0.90):
        """Same shape as _start_image_capture, for the pixel-match flow."""
        messagebox.showinfo(
            "Calibrate pixel match",
            "After you click OK, the bot window will hide.\n\n"
            "Make sure the skill's icon is visible and OFF cooldown (ready to cast), "
            "then click exactly on the pixel you want to check.\n\n"
            "Press Escape at any time to cancel.")
        self.withdraw()
        self.after(150, lambda: self._open_point_capture_overlay(on_use, default_confidence))

    def _open_point_capture_overlay(self, on_use=None, default_confidence=0.90):
        PointCaptureOverlay(
            self, on_done=lambda point: self._on_point_captured(point, on_use, default_confidence))

    def _on_point_captured(self, point, on_use=None, default_confidence=0.90):
        if point is None:
            self.deiconify()
            return
        # Let the overlay's own window fully disappear/repaint first, so the
        # sampled color isn't tinted by our own semi-transparent gray overlay.
        self.after(200, lambda: self._sample_pixel_color(point, on_use, default_confidence))

    def _sample_pixel_color(self, point, on_use=None, default_confidence=0.90):
        try:
            color = pyautogui.screenshot(region=(point[0], point[1], 1, 1)).getpixel((0, 0))
        except Exception as e:
            self.deiconify()
            messagebox.showerror("Calibration failed", f"Could not sample the pixel:\n{e}")
            return
        self.deiconify()
        self._show_pixel_match_preview(point, color, on_use, default_confidence)

    def _show_pixel_match_preview(self, point, color, on_use=None, default_confidence=0.90):
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

        confidence_var = None
        if on_use is not None:
            conf_row = ttk.Frame(preview)
            conf_row.pack(pady=(0, 4))
            ttk.Label(conf_row, text="Confidence").pack(side="left", padx=(0, 4))
            confidence_var = tk.StringVar(value=f"{default_confidence:.2f}")
            ttk.Entry(conf_row, textvariable=confidence_var, width=5).pack(side="left")

        btns = ttk.Frame(preview, padding=8)
        btns.pack()

        def use_this():
            if on_use is not None:
                try:
                    confidence = float(confidence_var.get())
                except ValueError:
                    messagebox.showerror("Invalid confidence", "Confidence must be a decimal (e.g. 0.9).")
                    return
                preview.destroy()
                on_use(point, color, confidence)
                return
            self.step_ready_match_type = "pixel"
            self.step_ready_pixel_pos = point
            self.step_ready_pixel_color = color
            self.step_ready_template = None
            self.step_ready_region = None
            self._refresh_ready_status()
            preview.destroy()

        def retry():
            preview.destroy()
            self._start_pixel_capture(on_use, default_confidence)

        ttk.Button(btns, text="Use This", command=use_this).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry", command=retry).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=preview.destroy).pack(side="left", padx=4)
