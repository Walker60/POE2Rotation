"""Virtual Xbox 360 controller output, via vgamepad/ViGEmBus.

Mirrors poe2bot/hotkeys.py's "mouse:<button>" string-prefix convention: a
Step (or, on the input side, a Rotation hotkey) can hold a plain string
like "a" for a keyboard key, or "controller:a" for a controller button --
CONTROLLER_PREFIX/is_controller_key/controller_button_of/encode_controller_key
are the single place that encoding is defined, shared by models.py
(validation), executor.py (dispatch), and the GUI's capture flow.

vgamepad itself must NEVER be imported at module scope here (or anywhere
else in this codebase) -- merely importing it connects to the ViGEmBus
driver and raises if that driver isn't installed/running, which would
break every keyboard-only user who has never touched a controller-encoded
step. _get_pad() defers the import to first real use.

On Windows, `pip install -r requirements.txt` (a from-source run) installs
the ViGEmBus driver itself as a side effect, via vgamepad's own post-install
hook (see README's Controller output section) -- but the packaged Windows
.exe build never goes through `pip install` on the end user's machine at
all, so that never happens there. find_vigembus_installer()/install_vigembus()
below exist so the app can offer the same one-time driver install itself
(see gui/updater_ui.py's sibling UpdaterMixin for the analogous "Settings
button -> background thread -> result dialog" pattern this follows).
"""
import importlib.util
import platform
import subprocess
import sys
import threading
from pathlib import Path

from poe2bot.log_setup import get_logger

log = get_logger()

CONTROLLER_PREFIX = "controller:"

# The two analog triggers aren't part of vgamepad's digital XUSB_BUTTON
# bitmask -- they're separate press_button-shaped calls (left_trigger/
# right_trigger), but from a rotation's perspective they behave like any
# other button: press() = full depth (255), release() = 0. Everything else
# maps directly to a confirmed XUSB_BUTTON member name.
_DIGITAL_BUTTONS = {
    "a": "XUSB_GAMEPAD_A",
    "b": "XUSB_GAMEPAD_B",
    "x": "XUSB_GAMEPAD_X",
    "y": "XUSB_GAMEPAD_Y",
    "lb": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "rb": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "back": "XUSB_GAMEPAD_BACK",
    "start": "XUSB_GAMEPAD_START",
    "ls": "XUSB_GAMEPAD_LEFT_THUMB",
    "rs": "XUSB_GAMEPAD_RIGHT_THUMB",
    "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
    "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
    "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
    "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
}
_TRIGGERS = ("lt", "rt")
VALID_BUTTON_NAMES = frozenset(_DIGITAL_BUTTONS) | frozenset(_TRIGGERS)


def is_controller_key(key) -> bool:
    return bool(key) and key.startswith(CONTROLLER_PREFIX)


def controller_button_of(key: str) -> str:
    return key[len(CONTROLLER_PREFIX):]


def encode_controller_key(name: str) -> str:
    return f"{CONTROLLER_PREFIX}{name}"


class ControllerUnavailable(RuntimeError):
    """Raised when the virtual controller can't be created -- vgamepad
    isn't installed, or its ViGEmBus driver isn't installed/running."""


_pad_lock = threading.Lock()
_init_lock = threading.Lock()
_pad = None
_vg = None  # the imported vgamepad module, stashed once import succeeds


def find_vigembus_installer() -> "Path | None":
    """Locates the ViGEmBus driver installer .msi vgamepad ships alongside
    itself (win/vigem/install/<x64|x86>/ViGEmBusSetup_<arch>.msi), without
    ever importing vgamepad -- importing it is exactly the thing that raises
    when the driver isn't installed (see _get_pad() above), so it can't be
    used to locate its own fix. None on non-Windows (there's no separate
    driver to install there -- vgamepad's Linux backend talks to the kernel's
    uinput directly), or if it can't be found either way below.

    Two distinct places to look, since vgamepad's code and its data files
    (this .msi included) don't necessarily live under the same root:
    - Frozen (the packaged Windows build): packaging/windows.spec's
      collect_data_files("vgamepad") preserves vgamepad's own internal
      layout under a "vgamepad/" prefix, rooted at sys._MEIPASS -- the
      directory PyInstaller actually extracts/exposes bundled data files
      under at runtime, regardless of its onedir layout version.
    - From source: vgamepad's own pip-installed package directory, found via
      importlib.util.find_spec (which only locates a module on disk --
      unlike an actual `import`, it never executes the package's code)."""
    if sys.platform != "win32":
        return None
    arch = "x64" if platform.architecture()[0] == "64bit" else "x86"
    tail = Path("win") / "vigem" / "install" / arch / f"ViGEmBusSetup_{arch}.msi"

    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "vgamepad" / tail)
    else:
        spec = importlib.util.find_spec("vgamepad")
        if spec and spec.submodule_search_locations:
            candidates.append(Path(list(spec.submodule_search_locations)[0]) / tail)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def install_vigembus():
    """Runs vgamepad's bundled ViGEmBus installer -- the same .msi a
    from-source `pip install -r requirements.txt` already runs automatically
    as a post-install step (see README's Controller output section), for
    when that never happened (the packaged Windows build, which has no pip
    step on the end user's machine at all -- see gui/updater_ui.py's sibling
    UpdaterMixin for how Settings surfaces this).

    BLOCKING -- msiexec doesn't return until the whole install finishes,
    including the UAC consent prompt Windows Installer shows on its own for
    a driver package (the exact one-time prompt README's Controller output
    section already documents for the from-source path); always call this
    from a background thread, never the Tk thread. Safe to re-run even if
    ViGEmBus is already installed -- Windows Installer just detects the
    existing install and no-ops (or offers a repair).

    Raises RuntimeError (never lets a raw subprocess/OSError escape) if the
    installer can't be located at all, or if msiexec itself reports failure
    -- 3010 ("success, reboot required") counts as success, everything else
    non-zero doesn't."""
    installer = find_vigembus_installer()
    if installer is None:
        raise RuntimeError(
            "Could not find vgamepad's bundled ViGEmBus installer. Installing it manually from "
            "https://github.com/ViGEm/ViGEmBus/releases (or reinstalling/redownloading poe2bot) "
            "should fix this.")
    try:
        result = subprocess.run(["msiexec", "/i", str(installer), "/qn"], capture_output=True, text=True)
    except OSError as e:
        raise RuntimeError(f"Could not launch msiexec: {e}") from e
    if result.returncode not in (0, 3010):
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"msiexec exited with code {result.returncode}" + (f": {detail}" if detail else ""))


def _get_pad():
    """Lazily creates the one shared virtual controller for this process.
    Deliberately never cached as a permanent failure -- a user who installs
    or starts ViGEmBus mid-session should have the very next fire succeed
    without restarting the app."""
    global _pad, _vg
    if _pad is not None:
        return _pad
    with _init_lock:
        if _pad is None:
            try:
                import vgamepad as vg
            except Exception as e:
                # The single most common cause by far (vgamepad's own package
                # is always present -- it's a hard requirements.txt dependency,
                # bundled into the frozen build too) is a missing/not-running
                # ViGEmBus driver: importing vgamepad on Windows eagerly
                # connects to it (see vgamepad's own VBus.__init__), so a
                # missing driver surfaces as an import failure here, not at
                # VX360Gamepad() construction below, however unintuitive that
                # split reads. install_vigembus() above is the fix on Windows;
                # a genuinely missing/broken vgamepad package is the only
                # other realistic cause, on any platform.
                fix = ("Windows > Settings > Controller > Install ViGEmBus Driver... "
                       "should fix this (or run it manually from "
                       "https://github.com/ViGEm/ViGEmBus/releases)." if sys.platform == "win32" else
                       "Make sure it's installed (pip install vgamepad).")
                raise ControllerUnavailable(
                    f"vgamepad could not be imported -- {fix} Details: {e}") from e
            try:
                pad = vg.VX360Gamepad()
            except Exception as e:
                raise ControllerUnavailable(
                    "Could not create a virtual Xbox 360 controller -- ViGEmBus may not be "
                    "installed or running. Reinstalling the vgamepad pip package re-runs its "
                    f"driver installer. Details: {e}") from e
            _vg, _pad = vg, pad
    return _pad


def warm_up():
    """Best-effort, silent early creation of the virtual controller --
    call once at app startup, Linux only. On the Steam Deck, PoE2 (via
    Proton) and Steam Input typically enumerate joysticks once, at their
    own startup, and treat anything that appears afterward as a hot-plug
    event they may never notice -- so a pad that isn't created until a
    rotation's first controller-encoded press can go completely unseen by
    the game even though poe2bot fired it correctly. Creating it here
    instead gives it a chance to already be present by the time PoE2/Steam
    Input go looking for controllers. Never called on Windows: ViGEmBus
    has no such enumeration-order issue, and unlike Linux this would show
    every keyboard-only user a driver-missing failure on every single
    startup instead of only if/when they ever press a controller-encoded
    step."""
    if sys.platform == "win32":
        return
    try:
        _get_pad()
    except Exception as e:
        log.info("Controller warm-up skipped, will retry on first real press: %s", e)


def press(name: str):
    """Press and flush a controller button/trigger. `name` is the bare
    button name (e.g. "a", "lt"), not the "controller:"-prefixed form."""
    pad = _get_pad()
    with _pad_lock:
        if name in _TRIGGERS:
            (pad.left_trigger if name == "lt" else pad.right_trigger)(value=255)
        else:
            pad.press_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        pad.update()


def release(name: str):
    """Release and flush a controller button/trigger."""
    pad = _get_pad()
    with _pad_lock:
        if name in _TRIGGERS:
            (pad.left_trigger if name == "lt" else pad.right_trigger)(value=0)
        else:
            pad.release_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        pad.update()


def release_all():
    """Releases every known button/trigger and flushes once -- called on app
    shutdown. A no-op if no virtual controller was ever created. Built only
    from the confirmed press_button/release_button/left_trigger/right_trigger/
    update API surface, rather than assuming a convenience reset() method
    exists on the installed vgamepad version."""
    global _pad
    if _pad is None:
        return
    with _pad_lock:
        for name in _DIGITAL_BUTTONS:
            _pad.release_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        _pad.left_trigger(value=0)
        _pad.right_trigger(value=0)
        _pad.update()
