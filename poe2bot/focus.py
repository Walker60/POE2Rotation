import sys

# is_game_focused() has no portable stdlib equivalent -- "which process owns the
# focused window" is answered completely differently per OS. Windows uses ctypes
# calls into user32.dll; Linux uses X11 EWMH properties (covers X11-native desktops
# and Wayland-via-XWayland, which is what Wine/Proton -- and Tk itself -- default to;
# see _focus_x11.py's module docstring for why native Wayland isn't in scope).
if sys.platform == "win32":
    from poe2bot._focus_win32 import is_game_focused  # noqa: F401
else:
    from poe2bot._focus_x11 import is_game_focused, check_x11_available, X11Unavailable  # noqa: F401
