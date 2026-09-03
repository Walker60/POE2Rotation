"""Central theming: ttk style names the rest of the GUI styles buttons with,
persisted dark/light theme load/apply/toggle, and a shared heading font.

Dark mode is a from-scratch "dracula" ttk theme (built on the themable "clam"
base -- see _ensure_dracula_theme), using the standard Dracula palette
(https://draculatheme.com/contribute -- background #282a36, current-line
#44475a, foreground #f8f8f2, comment #6272a4, purple #bd93f9, red #ff5555,
etc.). Light mode stays the existing Sun Valley ttk theme (`sv-ttk`) --
Dracula doesn't have a canonical light counterpart, so light mode is
deliberately left alone rather than inventing one.

Both themes get the same two custom button styles layered on top:
Accent.TButton (primary actions) and Danger.TButton (destructive ones).
sv_ttk ships its own Accent.TButton already; dracula's is defined here.
Danger.TButton doesn't exist natively in either, so this module registers it
for both.

Verified empirically (a live Tk session, not just read from source): a
custom style registered via ttk.Style().configure(...) is scoped to whichever
theme was active when it was registered -- switching themes and switching
back does NOT restore it automatically. configure_custom_styles() must
therefore run again after every theme change, not just once at startup.

Also verified (empirically): sv_ttk's own color setup -- the thing that makes
plain-tk widgets' ttk.Style().lookup("TFrame", "background") queries return
real colors instead of '' -- lives in a Tcl proc (`configure_colors`) that
sv_ttk binds to the <<ThemeChanged>> virtual event. In this Tk/sv_ttk build,
neither Tk's own automatic firing of that event on a ttk theme switch, nor
generating it manually, actually invokes that handler -- calling
`configure_colors` directly is the one thing that reliably worked in
testing, so apply_theme() does that after every sv_ttk.set_theme(...). The
dracula theme below doesn't have this problem at all -- it configures "."/
"TFrame" directly via the normal ttk Style API, which takes effect
immediately with no separate "apply colors" step.
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import sv_ttk

ACCENT_BUTTON_STYLE = "Accent.TButton"   # built into sv_ttk; registered below for dracula
DANGER_BUTTON_STYLE = "Danger.TButton"   # registered below for both

DANGER_COLOR = "#ff5555"   # Dracula red -- used app-wide for error text/borders regardless of theme

DRACULA = {
    "bg": "#282a36",
    "bg_alt": "#21222c",        # slightly darker -- input fields, popup listboxes
    "current_line": "#44475a",  # selections, hovers, borders
    "fg": "#f8f8f2",
    "comment": "#6272a4",       # muted/disabled text
    "cyan": "#8be9fd",
    "green": "#50fa7b",
    "orange": "#ffb86c",
    "pink": "#ff79c6",
    "purple": "#bd93f9",
    "red": "#ff5555",
    "yellow": "#f1fa8c",
}

_heading_font = None
_dracula_built = False


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


def _ensure_dracula_theme(style: ttk.Style) -> None:
    """Registers the "dracula" ttk theme once (ttk.Style().theme_create
    raises if called twice for the same name) -- built on "clam" since it's
    the stock theme most amenable to full recoloring via plain .configure()
    calls, unlike the OS-native themes. Every widget class this app actually
    uses gets covered explicitly (TFrame/TLabel/TButton/TEntry/TCombobox/
    TCheckbutton/TRadiobutton/TLabelframe/TScrollbar/TSeparator/Treeview) --
    this app never uses TNotebook/TProgressbar/TScale/TPanedwindow, so those
    are left at "clam"'s own (unthemed) defaults rather than styled blind."""
    global _dracula_built
    if _dracula_built:
        return
    d = DRACULA
    style.theme_create("dracula", parent="clam", settings={
        ".": {"configure": {
            "background": d["bg"], "foreground": d["fg"],
            "fieldbackground": d["bg_alt"], "troughcolor": d["bg"],
            "bordercolor": d["current_line"], "lightcolor": d["bg"], "darkcolor": d["bg"],
            "selectbackground": d["current_line"], "selectforeground": d["fg"],
            "insertcolor": d["fg"], "focuscolor": d["purple"],
        }},
        "TFrame": {"configure": {"background": d["bg"]}},
        "TLabel": {"configure": {"background": d["bg"], "foreground": d["fg"]}},
        "TLabelframe": {"configure": {"background": d["bg"], "bordercolor": d["current_line"]}},
        "TLabelframe.Label": {"configure": {"background": d["bg"], "foreground": d["fg"]}},
        "TButton": {
            "configure": {"background": d["current_line"], "foreground": d["fg"],
                          "bordercolor": d["current_line"], "padding": (10, 5), "relief": "flat"},
            "map": {"background": [("disabled", d["bg_alt"]), ("pressed", "#383a4c"), ("active", "#565973")],
                    "foreground": [("disabled", d["comment"])]},
        },
        "Accent.TButton": {
            "configure": {"background": d["purple"], "foreground": d["bg"],
                          "bordercolor": d["purple"], "padding": (10, 5), "relief": "flat"},
            "map": {"background": [("disabled", d["current_line"]), ("pressed", "#a67ef0"), ("active", "#cba6fb")],
                    "foreground": [("disabled", d["comment"])]},
        },
        "TEntry": {
            "configure": {"fieldbackground": d["bg_alt"], "foreground": d["fg"],
                          "insertcolor": d["fg"], "bordercolor": d["current_line"],
                          "lightcolor": d["bg_alt"], "darkcolor": d["bg_alt"], "padding": 4},
            "map": {"bordercolor": [("invalid", d["red"]), ("focus", d["purple"])],
                    "fieldbackground": [("invalid", "#3a2530"), ("disabled", d["bg"])]},
        },
        "TCombobox": {
            "configure": {"fieldbackground": d["bg_alt"], "background": d["current_line"],
                          "foreground": d["fg"], "arrowcolor": d["fg"], "bordercolor": d["current_line"],
                          "padding": 4},
            "map": {"fieldbackground": [("readonly", d["bg_alt"]), ("disabled", d["bg"])],
                    "background": [("active", "#565973")],
                    "bordercolor": [("focus", d["purple"])]},
        },
        "TCheckbutton": {
            "configure": {"background": d["bg"], "foreground": d["fg"], "focuscolor": "",
                          "indicatorcolor": d["current_line"]},
            "map": {"indicatorcolor": [("selected", d["purple"])]},
        },
        "TRadiobutton": {
            "configure": {"background": d["bg"], "foreground": d["fg"], "focuscolor": "",
                          "indicatorcolor": d["current_line"]},
            "map": {"indicatorcolor": [("selected", d["purple"])]},
        },
        "TScrollbar": {
            "configure": {"background": d["current_line"], "troughcolor": d["bg"],
                          "bordercolor": d["bg"], "arrowcolor": d["fg"], "relief": "flat"},
            "map": {"background": [("active", "#565973")]},
        },
        "TSeparator": {"configure": {"background": d["current_line"]}},
        "Treeview": {
            "configure": {"background": d["bg"], "fieldbackground": d["bg"], "foreground": d["fg"],
                          "bordercolor": d["current_line"], "rowheight": 22},
            "map": {"background": [("selected", d["current_line"])],
                    "foreground": [("selected", d["fg"])]},
        },
        "Treeview.Heading": {
            "configure": {"background": d["current_line"], "foreground": d["fg"],
                          "relief": "flat", "padding": (6, 4)},
            "map": {"background": [("active", "#565973")]},
        },
    })
    _dracula_built = True


def _configure_combobox_popup_colors(root) -> None:
    """A ttk.Combobox's dropdown list isn't a ttk widget at all -- it's a
    raw Tk Listbox Tk builds internally -- so it can't be styled through
    ttk.Style(); the classic `option add` database is the only way to color
    it. Idempotent (option add just overwrites), so safe to call every time
    dark mode is (re-)applied."""
    d = DRACULA
    root.option_add("*TCombobox*Listbox.background", d["bg_alt"])
    root.option_add("*TCombobox*Listbox.foreground", d["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", d["current_line"])
    root.option_add("*TCombobox*Listbox.selectForeground", d["fg"])


def configure_custom_styles(root=None) -> None:
    """(Re-)registers every custom style this app defines against whichever
    ttk theme is currently active. Must be called once right after the
    first apply_theme(...) and again after every subsequent one -- see
    module docstring. `root` is optional only so this can be called before
    any window exists (it just skips the heading-font tweak in that case);
    every real call site has one."""
    style = ttk.Style()
    disabled_fg = style.lookup("TButton", "foreground", ("disabled",)) or "#888888"
    style.configure(DANGER_BUTTON_STYLE, foreground=DANGER_COLOR)
    style.map(DANGER_BUTTON_STYLE, foreground=[("disabled", disabled_fg)])
    if root is not None:
        style.configure("TLabelframe.Label", font=heading_font(root))


def _refresh_sv_ttk_colors(root) -> None:
    """Directly invokes sv_ttk's own <<ThemeChanged>>-bound color setup --
    see module docstring for why this is necessary instead of just firing
    that event. Only relevant for light mode (sv_ttk) -- the dracula theme
    configures its own colors directly and needs no such workaround. Falls
    back to (harmlessly) firing the event anyway if a future sv_ttk version
    renames/removes this internal proc, in case a different Tk build
    handles it correctly."""
    try:
        root.tk.call("configure_colors")
    except tk.TclError:
        root.event_generate("<<ThemeChanged>>", when="now")


def apply_theme(theme_name: str, root) -> None:
    """Dark mode -> the from-scratch "dracula" ttk theme; light mode -> the
    existing Sun Valley ttk theme. Either way, (re-)applies this app's own
    custom styles (Accent/Danger button styles, heading font) on top."""
    style = ttk.Style(root)
    if theme_name == "dark":
        _ensure_dracula_theme(style)
        style.theme_use("dracula")
        _configure_combobox_popup_colors(root)
    else:
        sv_ttk.set_theme(theme_name)
        _refresh_sv_ttk_colors(root)
    configure_custom_styles(root)


def toggle_theme(current_theme: str) -> str:
    return "light" if current_theme == "dark" else "dark"
