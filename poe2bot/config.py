import os
import sys

# When frozen (PyInstaller -- see packaging/linux.spec), __file__ points
# somewhere inside the bundle's own extracted/embedded copy of this package,
# not a location that should hold the user's actual rotations/logs/templates
# -- those need to live alongside the executable itself instead, so they
# persist across replacing the bundle with a newer build. sys.executable is
# the frozen app's own binary in that case; getattr(sys, "frozen", False) is
# the flag PyInstaller sets on the sys module at runtime for exactly this
# check (unset, so falsy, under a normal `python main.py` run either way).
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROTATIONS_DIR = os.path.join(BASE_DIR, "rotations")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
TRASH_DIR = os.path.join(BASE_DIR, "trash")   # single-slot "undo last delete" -- see storage.trash_rotation

# Verify the exact executable name via Task Manager > Details while POE2 is running --
# it may be PathOfExileSteam.exe / PathOfExile_x64.exe / PathOfExile_KG.exe depending on
# the storefront/build. Override without editing source via the env var, which also
# doubles as the mechanism for pointing the focus guard at Notepad during testing.
GAME_PROCESS_NAME = os.environ.get("POE2BOT_TARGET_PROCESS", "PathOfExileSteam.exe")

# Reserved global hotkey that instantly stops every running rotation. Cannot be bound
# to a rotation.
PANIC_KEY = os.environ.get("POE2BOT_PANIC_KEY", "f12")

# Whether a rotation must wait for the game window to have OS focus before firing
# (see poe2bot/executor.py's RotationRunner._wait_for_focus_or_stop) -- the default,
# safe behavior, so casts never fire into whatever window happens to be focused
# instead of the game. Turning this off is for testing against a target that never
# takes OS focus itself (e.g. driving another always-on-top tool), or a setup where
# the focus check itself is unreliable -- see focus.py's Windows/X11 backends.
REQUIRE_GAME_FOCUS = os.environ.get("POE2BOT_REQUIRE_GAME_FOCUS", "1") != "0"

# A virtual controller's button report has no OS-level input queue the way a real
# keyboard tap's discrete KEYDOWN/KEYUP messages do -- a true zero-duration press+
# release risks the game's next input poll never observing the transition at all.
# A controller-encoded step's tap is floored to this duration instead of firing
# instantly; override if the game needs longer/shorter to reliably register it.
CONTROLLER_MIN_TAP_MS = int(os.environ.get("POE2BOT_CONTROLLER_MIN_TAP_MS", "40"))

# Which controller to read as the real, physically-held one for hotkey/capture
# purposes -- an XInput slot (0-3) on Windows, or an index into the sorted list
# of gamepad-capable /dev/input/event* devices on Linux (see
# controller_input.py's _candidate_gamepad_paths). Either way, the virtual
# output controller (vgamepad -- ViGEmBus on Windows, uinput on Linux)
# deliberately looks just like a genuine one, so there's no reliable way to
# tell them apart by querying the OS -- this assumes the real controller is
# already connected and enumerated first (claiming index/slot 0) before the
# bot creates its virtual one. Override if that assumption doesn't hold for
# your setup.
CONTROLLER_INDEX = int(os.environ.get("POE2BOT_CONTROLLER_INDEX", "0"))
