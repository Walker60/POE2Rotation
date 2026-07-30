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


def main():
    log = get_logger()
    log.info("Starting POE2 Rotation Bot")
    try:
        app = App()
    except OSError as e:
        messagebox.showerror(
            "Failed to start",
            "Could not register global hotkeys.\n\n"
            "If Path of Exile 2 (or its launcher) runs elevated, this app needs to "
            "run as Administrator too -- Windows blocks a lower-privilege process "
            "from sending input to an elevated window.\n\n"
            f"Details: {e}")
        log.error(f"Failed to start: {e}")
        return
    app.mainloop()


if __name__ == "__main__":
    main()
