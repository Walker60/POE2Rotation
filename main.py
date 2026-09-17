import ctypes
import sys


def _enable_dpi_awareness():
    """Must run before poe2bot.gui is imported, and before any Tk window
    exists. Setting this affects how Win32 reports screen/mouse coordinates
    for the whole process from that point on -- if it happened at some
    other, accidental time relative to Tk's own window creation instead
    (e.g. as an import-time side effect of some other dependency calling
    the older, coarser SetProcessDPIAware() itself), Tk's
    winfo_screenwidth()/event.x_root could end up disagreeing with
    anything doing its own raw screen-pixel math on any display running
    above 100% scaling. Doing this ourselves, first, with the more capable
    per-monitor API removes any dependency on import ordering."""
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

from poe2bot import updater
from poe2bot.gui import App
from poe2bot.log_setup import get_logger


def main():
    # Best-effort backstop for a poe2bot.old directory a previous update's
    # own cleanup didn't finish removing -- see updater.cleanup_stale_update()'s
    # docstring. A no-op on every ordinary startup (not running frozen at
    # all, or nothing left over), so it's safe to call unconditionally
    # before the app itself starts.
    updater.cleanup_stale_update()
    try:
        log = get_logger()
    except OSError as e:
        messagebox.showerror(
            "Failed to start",
            "Could not set up logging (the logs folder or log file may be unwritable, "
            "locked by another running copy of this app, or the disk may be full).\n\n"
            f"Details: {e}")
        return
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
