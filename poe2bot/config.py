import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROTATIONS_DIR = os.path.join(BASE_DIR, "rotations")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Verify the exact executable name via Task Manager > Details while POE2 is running --
# it may be PathOfExileSteam.exe / PathOfExile_x64.exe / PathOfExile_KG.exe depending on
# the storefront/build. Override without editing source via the env var, which also
# doubles as the mechanism for pointing the focus guard at Notepad during testing.
GAME_PROCESS_NAME = os.environ.get("POE2BOT_TARGET_PROCESS", "PathOfExileSteam.exe")

# Reserved global hotkey that instantly stops every running rotation. Cannot be bound
# to a rotation.
PANIC_KEY = os.environ.get("POE2BOT_PANIC_KEY", "f12")

# A virtual controller's button report has no OS-level input queue the way a real
# keyboard tap's discrete KEYDOWN/KEYUP messages do -- a true zero-duration press+
# release risks the game's next input poll never observing the transition at all.
# A controller-encoded step's tap is floored to this duration instead of firing
# instantly; override if the game needs longer/shorter to reliably register it.
CONTROLLER_MIN_TAP_MS = int(os.environ.get("POE2BOT_CONTROLLER_MIN_TAP_MS", "40"))

# Which XInput slot (0-3) to read as the real, physically-held controller for
# hotkey/capture purposes. ViGEmBus's virtual output controller deliberately
# reports the same VID/PID as a genuine Xbox 360 controller (that's the point
# of driver-level emulation), so there's no reliable way to tell them apart
# by querying Windows -- this assumes the real controller is already plugged
# in and enumerated (claiming slot 0) before the bot creates its virtual one.
# Override if that assumption doesn't hold for your setup.
CONTROLLER_INDEX = int(os.environ.get("POE2BOT_CONTROLLER_INDEX", "0"))
