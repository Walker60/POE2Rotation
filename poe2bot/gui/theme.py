"""Central theming: ttk style names the rest of the GUI styles buttons with,
persisted dark/light theme load/apply/toggle, and a shared heading font.

sv_ttk already ships an "Accent.TButton" style in both its dark and light
theme files -- no registration needed, it just works once sv_ttk.set_theme()
has run. There's no built-in destructive/danger style, so this module adds
one (Danger.TButton) the same way.

Verified empirically (a live Tk session, not just read from source): a
custom style registered via ttk.Style().configure(...) is scoped to whichever
theme was active when it was registered -- switching sv_ttk themes and
switching back does NOT restore it automatically. configure_custom_styles()
must therefore run again after every theme change, not just once at startup.

Also verified (empirically, against actual behavior -- not just read from
source): sv_ttk's own color setup -- the thing that makes plain-tk widgets'
ttk.Style().lookup("TFrame", "background") queries return real colors
instead of '' -- lives in a Tcl proc (`configure_colors`) that sv_ttk binds
to the <<ThemeChanged>> virtual event. In this Tk/sv_ttk build, neither
Tk's own automatic firing of that event on a ttk theme switch, nor
generating it manually (tried both via Tkinter's event_generate and Tcl's
own `event generate ... -when now`), actually invokes that handler --
calling `configure_colors` directly is the one thing that reliably worked
in testing. apply_theme() does that after every sv_ttk.set_theme(...) so
every existing bg-sync call site across the app starts actually working.
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import sv_ttk

ACCENT_BUTTON_STYLE = "Accent.TButton"   # built into sv_ttk itself
DANGER_BUTTON_STYLE = "Danger.TButton"   # registered below

DANGER_COLOR = "#e5484d"

_heading_font = None


def heading_font(root):
    """A bold, +1pt variant of the platform default font, lazily created
    (tkinter.font.Font needs a live Tk instance to attach to) and cached --
    there's only ever one such font needed across the whole app."""
    global _heading_font
    if _heading_font is None:
        base = tkfont.nametofont("TkDefaultFont", root=root)
        _heading_font = tkfont.Font(
            root=root, family=base.cget("family"), size=base.cget("size") + 1, weight="bold")
    return _heading_font


def configure_custom_styles(root=None) -> None:
    """(Re-)registers every custom style this app defines against whichever
    ttk theme is currently active. Must be called once right after the
    first sv_ttk.set_theme(...) and again after every subsequent one -- see
    module docstring. `root` is optional only so this can be called before
    any window exists (it just skips the heading-font tweak in that case);
    every real call site has one."""
    style = ttk.Style()
    disabled_fg = style.lookup("TButton", "foreground", ("disabled",)) or "#888888"
    style.configure(DANGER_BUTTON_STYLE, foreground=DANGER_COLOR)
    style.map(DANGER_BUTTON_STYLE, foreground=[("disabled", disabled_fg)])
    if root is not None:
        style.configure("TLabelframe.Label", font=heading_font(root))


def _refresh_theme_colors(root) -> None:
    """Directly invokes sv_ttk's own <<ThemeChanged>>-bound color setup --
    see module docstring for why this is necessary instead of just firing
    that event. Falls back to (harmlessly) firing the event anyway if a
    future sv_ttk version renames/removes this internal proc, in case a
    different Tk build handles it correctly."""
    try:
        root.tk.call("configure_colors")
    except tk.TclError:
        root.event_generate("<<ThemeChanged>>", when="now")


def apply_theme(theme_name: str, root) -> None:
    """Sets the sv_ttk theme, works around the <<ThemeChanged>> gap (see
    module docstring), and (re-)applies this app's own custom styles."""
    sv_ttk.set_theme(theme_name)
    _refresh_theme_colors(root)
    configure_custom_styles(root)


def toggle_theme(current_theme: str) -> str:
    return "light" if current_theme == "dark" else "dark"
