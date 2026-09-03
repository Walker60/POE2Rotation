"""Themed drop-in replacements for tkinter.messagebox/simpledialog, so
confirmation/error/input popups match the app's current dark/light theme
instead of always rendering in native (always-light) OS dialog chrome.

Call signatures deliberately mirror the stdlib functions they replace (same
positional args, same return value shapes) so existing call sites only need
an import swap (`from tkinter import messagebox` -> `from poe2bot.gui import
dialogs as messagebox`, or call `dialogs.xxx` directly), not a rewrite.
"""
import tkinter as tk
from tkinter import ttk

from poe2bot.gui import theme

_FALLBACK_BG = "#1c1c1c"
_FALLBACK_FG = "#fafafa"


def _theme_colors():
    style = ttk.Style()
    bg = style.lookup("TFrame", "background") or _FALLBACK_BG
    fg = style.lookup("TLabel", "foreground") or _FALLBACK_FG
    return bg, fg


def _build_dialog(title, parent):
    parent = parent or tk._default_root
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    bg, _ = _theme_colors()
    dialog.configure(bg=bg)
    return dialog, parent


def _center_over_parent(dialog, parent):
    dialog.update_idletasks()
    if parent is not None:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
    else:
        px, py = 0, 0
        pw, ph = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
    dw, dh = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    x = px + max(0, (pw - dw) // 2)
    y = py + max(0, (ph - dh) // 2)
    dialog.geometry(f"+{x}+{y}")


def _run_modal(dialog, parent):
    _center_over_parent(dialog, parent)
    dialog.grab_set()
    dialog.focus_force()
    dialog.wait_window(dialog)


def _message_dialog(title, message, parent, button_style):
    dialog, parent = _build_dialog(title, parent)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.bind("<Return>", lambda _e: dialog.destroy())

    ttk.Label(dialog, text=message, wraplength=360, justify="left",
              padding=(16, 16, 16, 8)).pack()
    btns = ttk.Frame(dialog, padding=(0, 0, 16, 16))
    btns.pack()
    ok_btn = ttk.Button(btns, text="OK", style=button_style, command=dialog.destroy)
    ok_btn.pack()
    ok_btn.focus_set()

    _run_modal(dialog, parent)


def showinfo(title, message, parent=None) -> None:
    _message_dialog(title, message, parent, theme.ACCENT_BUTTON_STYLE)


def showerror(title, message, parent=None) -> None:
    _message_dialog(title, message, parent, theme.ACCENT_BUTTON_STYLE)


def showwarning(title, message, parent=None) -> None:
    _message_dialog(title, message, parent, theme.ACCENT_BUTTON_STYLE)


def askyesno(title, message, parent=None, danger: bool = False) -> bool:
    dialog, parent = _build_dialog(title, parent)
    result = {"value": False}

    def respond(value):
        result["value"] = value
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", lambda: respond(False))
    dialog.bind("<Escape>", lambda _e: respond(False))

    ttk.Label(dialog, text=message, wraplength=360, justify="left",
              padding=(16, 16, 16, 8)).pack()
    btns = ttk.Frame(dialog, padding=(0, 0, 16, 16))
    btns.pack()
    yes_style = theme.DANGER_BUTTON_STYLE if danger else theme.ACCENT_BUTTON_STYLE
    yes_btn = ttk.Button(btns, text="Yes", style=yes_style, command=lambda: respond(True))
    yes_btn.pack(side="left", padx=(0, 6))
    no_btn = ttk.Button(btns, text="No", command=lambda: respond(False))
    no_btn.pack(side="left")
    # A destructive Yes never becomes the Enter-key default, so an accidental
    # Enter press can't confirm a delete.
    dialog.bind("<Return>", (lambda _e: respond(False)) if danger else (lambda _e: respond(True)))
    (no_btn if danger else yes_btn).focus_set()

    _run_modal(dialog, parent)
    return result["value"]


def askstring(title, prompt, initialvalue=None, parent=None):
    dialog, parent = _build_dialog(title, parent)
    result = {"value": None}

    ttk.Label(dialog, text=prompt, wraplength=360, justify="left",
              padding=(16, 16, 16, 4)).pack(anchor="w")
    var = tk.StringVar(value=initialvalue or "")
    entry = ttk.Entry(dialog, textvariable=var, width=32)
    entry.pack(padx=16, pady=(0, 8), fill="x")
    entry.select_range(0, "end")
    entry.focus_set()

    def submit():
        result["value"] = var.get()
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.bind("<Return>", lambda _e: submit())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())

    btns = ttk.Frame(dialog, padding=(0, 0, 0, 16))
    btns.pack()
    ttk.Button(btns, text="OK", style=theme.ACCENT_BUTTON_STYLE, command=submit).pack(side="left", padx=(0, 6))
    ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left")

    _run_modal(dialog, parent)
    return result["value"]


def askfloat(title, prompt, initialvalue=None, minvalue=None, maxvalue=None, parent=None):
    dialog, parent = _build_dialog(title, parent)
    result = {"value": None}

    ttk.Label(dialog, text=prompt, wraplength=360, justify="left",
              padding=(16, 16, 16, 4)).pack(anchor="w")
    var = tk.StringVar(value="" if initialvalue is None else f"{initialvalue:g}")
    entry = ttk.Entry(dialog, textvariable=var, width=12)
    entry.pack(padx=16, anchor="w")
    entry.select_range(0, "end")
    entry.focus_set()
    error_var = tk.StringVar(value="")
    ttk.Label(dialog, textvariable=error_var, foreground=theme.DANGER_COLOR,
              padding=(16, 4, 16, 0)).pack(anchor="w")

    def submit():
        text = var.get().strip()
        try:
            value = float(text)
        except ValueError:
            error_var.set("Enter a number.")
            return
        if minvalue is not None and value < minvalue:
            error_var.set(f"Must be at least {minvalue:g}.")
            return
        if maxvalue is not None and value > maxvalue:
            error_var.set(f"Must be at most {maxvalue:g}.")
            return
        result["value"] = value
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.bind("<Return>", lambda _e: submit())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())

    btns = ttk.Frame(dialog, padding=16)
    btns.pack()
    ttk.Button(btns, text="OK", style=theme.ACCENT_BUTTON_STYLE, command=submit).pack(side="left", padx=(0, 6))
    ttk.Button(btns, text="Cancel", command=dialog.destroy).pack(side="left")

    _run_modal(dialog, parent)
    return result["value"]
