import tkinter as tk


class RegionCaptureOverlay(tk.Toplevel):
    """Fullscreen, borderless, semi-transparent click-drag-release rectangle picker.

    Calls on_done(region) where region is (left, top, width, height) in absolute
    screen pixels, or on_done(None) if cancelled (Escape, or a release without a
    meaningfully-sized drag). Primary monitor only -- geometry is sized/positioned
    from winfo_screenwidth()/winfo_screenheight() at +0+0, so multi-monitor setups
    where the primary isn't at the origin, or where a skill icon sits on a secondary
    monitor, aren't supported by this overlay (known limitation).
    """

    def __init__(self, master, on_done):
        super().__init__(master)
        self.on_done = on_done
        self._start = None
        self._rect_id = None

        self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", self._on_cancel)

        self.grab_set()
        self.focus_force()

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

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, region):
        callback = self.on_done
        self.destroy()
        callback(region)


class PointCaptureOverlay(tk.Toplevel):
    """Fullscreen, borderless, semi-transparent single-click point picker.

    Calls on_done(point) where point is (x, y) in absolute screen pixels, or
    on_done(None) if cancelled (Escape). Primary monitor only, same limitation
    as RegionCaptureOverlay.
    """

    def __init__(self, master, on_done):
        super().__init__(master)
        self.on_done = on_done

        self.overrideredirect(True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.attributes("-alpha", 0.25)
        self.attributes("-topmost", True)
        self.configure(bg="gray")

        self.canvas = tk.Canvas(self, cursor="cross", bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<Escape>", self._on_cancel)

        self.grab_set()
        self.focus_force()

    def _on_click(self, event):
        self._finish((event.x_root, event.y_root))

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, point):
        callback = self.on_done
        self.destroy()
        callback(point)
