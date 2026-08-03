import ctypes
import sys


def _enable_dpi_awareness():
    """Must run before poe2bot.gui (and therefore pyautogui) is imported, and before
    any Tk window exists. pyautogui calls SetProcessDPIAware() as an import side
    effect on Windows, which changes how Win32 reports screen/mouse coordinates for
    the whole process from that point on -- if that fires at some other, accidental
    time relative to Tk's own window creation, Tk's winfo_screenwidth()/event.x_root
    and pyautogui's screenshot()/locateOnScreen() can end up disagreeing about
    physical vs. logical pixels on any display running above 100% scaling. Doing
    this ourselves, first, with the more capable per-monitor API removes that
    dependency on import ordering."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except AttributeError:
            pass  # very old Windows -- nothing to do


_enable_dpi_awareness()

from tkinter import messagebox

from poe2bot.gui import App
from poe2bot.log_setup import get_logger


def _check_platform_prerequisites() -> bool:
    """True if it's safe to proceed. On Linux, verifies an X11/XWayland display is
    reachable and the window manager supports the EWMH property focus detection
    needs -- fails fast with a clear message instead of a rotation that silently
    never detects game focus. No-op on Windows (nothing to check upfront there)."""
    if sys.platform == "win32":
        return True
    from poe2bot.focus import check_x11_available, X11Unavailable
    try:
        check_x11_available()
        return True
    except X11Unavailable as e:
        messagebox.showerror("Failed to start", str(e))
        return False


def main():
    log = get_logger()
    log.info("Starting POE2 Rotation Bot")
    if not _check_platform_prerequisites():
        return
    try:
        app = App()
    except OSError as e:
        if sys.platform == "win32":
            hint = (
                "If Path of Exile 2 (or its launcher) runs elevated, this app needs to "
                "run as Administrator too -- Windows blocks a lower-privilege process "
                "from sending input to an elevated window.")
        else:
            hint = (
                "This usually means the keyboard/mouse libraries couldn't access "
                "/dev/uinput. Make sure the `uinput` kernel module is loaded and your "
                "user is in the `input` group (or has an equivalent udev rule), then "
                "log out and back in for group membership to take effect.")
        messagebox.showerror(
            "Failed to start",
            f"Could not register global hotkeys.\n\n{hint}\n\nDetails: {e}")
        log.error(f"Failed to start: {e}")
        return
    app.mainloop()


if __name__ == "__main__":
    main()
