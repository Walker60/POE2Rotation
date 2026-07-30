import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROTATIONS_DIR = os.path.join(BASE_DIR, "rotations")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Verify the exact executable name via Task Manager > Details while POE2 is running --
# it may be PathOfExileSteam.exe / PathOfExile_x64.exe / PathOfExile_KG.exe depending on
# the storefront/build. Override without editing source via the env var, which also
# doubles as the mechanism for pointing the focus guard at Notepad during testing.
GAME_PROCESS_NAME = os.environ.get("POE2BOT_TARGET_PROCESS", "PathOfExile.exe")

# Reserved global hotkey that instantly stops every running rotation. Cannot be bound
# to a rotation.
PANIC_KEY = os.environ.get("POE2BOT_PANIC_KEY", "f12")
