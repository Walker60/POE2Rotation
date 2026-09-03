"""Monitor-aware window sizing, shared by every top-level window in the app
(previously private to App -- see git history) so a new Toplevel doesn't
have to hardcode a pixel geometry or re-derive multi-monitor handling."""
import ctypes
import sys
from ctypes import wintypes


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def monitor_work_area(hwnd):
    """The usable desktop area, in pixels, of whichever monitor `hwnd` is
    currently on -- excludes the taskbar, unlike winfo_screenwidth()/
    winfo_screenheight(), which report that monitor's full native
    resolution. On a smaller display the taskbar strip is a much bigger
    fraction of the screen, so a window sized right up to the raw
    resolution can end up with its bottom edge rendered behind the taskbar.
    Also correctly follows whichever monitor the window is actually on in a
    multi-monitor setup, rather than always assuming the primary display.
    Returns None on non-Windows, or if anything about the Win32 call fails,
    so callers can fall back to winfo_screenwidth()/winfo_screenheight()."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
        monitor_default_to_nearest = 2
        monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), monitor_default_to_nearest)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.right - work.left, work.bottom - work.top
    except (OSError, AttributeError, ValueError, TypeError, ctypes.ArgumentError):
        return None


def size_window_to_contents(window, *, min_width: int = 0, min_height: int = 0) -> None:
    """Open `window` large enough to show its current contents without the
    user having to resize on every launch, clamped to its monitor's usable
    work area so it never opens larger than what's actually visible.
    `min_width`/`min_height` set a floor (e.g. a window that starts with
    little/no content yet, like the Activity window before any pane
    exists) -- 0 means no floor beyond the natural requested size.

    Deliberately not paired with minsize()/maxsize(), so the window stays
    freely click-and-drag resizable (larger or smaller) afterward."""
    window.update_idletasks()
    work_area = monitor_work_area(window.winfo_id())
    screen_width, screen_height = work_area or (window.winfo_screenwidth(), window.winfo_screenheight())
    width = min(max(window.winfo_reqwidth(), min_width), screen_width)
    height = min(max(window.winfo_reqheight(), min_height), screen_height)
    window.geometry(f"{width}x{height}")
