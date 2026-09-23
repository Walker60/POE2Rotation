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

Passthrough mode (set_passthrough_enabled()): a game generally only reads
ONE physical/virtual controller at a time (confirmed on a real Steam Deck:
PoE2 defaults to whichever one existed first -- Steam Input's own synthesized
pad for the Deck's built-in controller -- and simply never reads a second,
separately-created one, no matter how correctly identified). So a player who
wants to keep using their REAL controller to actually play, while poe2bot
ALSO presses buttons for them, can't just point the game at poe2bot's pad
instead -- that would give up manual control entirely. Passthrough solves
this by making poe2bot's own virtual pad the ONLY thing the game needs to
read: it continuously mirrors the real controller's entire state (via
poe2bot/controller_input.py's ControllerReader.snapshot() -- buttons,
triggers, both sticks) onto the virtual one, merging in whatever a running
rotation additionally wants pressed (see press()/release()) -- OR'd per
button/trigger, so a rotation's own press never fights a real, simultaneous
one. Analog sticks are pure passthrough; nothing in this app ever drives
them programmatically (a Step's `key` is always a button/trigger name, never
a stick axis), so there's no merge question there at all.
"""
import importlib.util
import platform
import subprocess
import sys
import threading
from pathlib import Path

from poe2bot import controller_input
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

# ---- passthrough mode (see module docstring) --------------------------------
_bot_wants = {}                       # name -> True, only present while a running rotation
                                       # currently wants that button/trigger pressed
_passthrough_enabled = False
_passthrough_thread = None
_passthrough_stop = threading.Event()
_PASSTHROUGH_POLL_S = 0.015           # matches controller_input.POLL_INTERVAL_S -- no point
                                       # polling faster than the real controller's own source data changes


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


_linux_uinput_identity_patched = False


def _patch_linux_vgamepad_identity():
    """vgamepad's Linux backend (vgamepad/lin/virtual_gamepad.py) creates its
    uinput device via a bare libevdev.Device() and never sets a vendor,
    product, or bus type of its own -- unlike its Windows counterpart (a
    real ViGEmBus-emulated Xbox 360 pad, VID 0x045E/PID 0x028E over
    BUS_USB), so the Linux virtual pad ends up with whatever meaningless
    default libevdev/uinput assigns it. This is a known, documented SDL2
    limitation, not a poe2bot- or vgamepad-specific one: SDL's
    gamecontroller layer -- what most modern games (Proton/Wine builds
    included) use to recognize a device as an Xbox-360-shaped controller
    and map its buttons at all -- matches primarily against a vendor/
    product ID database (gamecontrollerdb.txt); a device with no
    recognized ID is invisible to it even though it's perfectly readable
    at the raw evdev/joystick level (evtest/jstest see it fine -- this is
    exactly why that check alone can't rule this out). See
    https://discourse.libsdl.org/t/uinput-controller/27972, which documents
    this same class of problem and its standard fix: present the virtual
    device with a real, recognized controller's identity instead of a bare/
    virtual one.

    Patches libevdev.Device.create_uinput_device (there's no hook point
    inside vgamepad itself for this -- VX360Gamepad.__init__ calls it
    directly, with no vendor/product of its own set yet) to set a real
    Xbox 360 controller's identity -- BUS_USB, VID 0x045E, PID 0x028E,
    matching what Linux's own "xpad" driver reports for a genuine one --
    on any Device that doesn't already have one of its own (so a
    hypothetical future vgamepad release that sets a real ID itself, or
    some other libevdev consumer entirely, is left alone). Idempotent --
    safe to call more than once, only patches once."""
    global _linux_uinput_identity_patched
    if _linux_uinput_identity_patched:
        return
    import libevdev

    original_create_uinput_device = libevdev.Device.create_uinput_device

    def _create_uinput_device_with_xbox_identity(self, *args, **kwargs):
        current = self.id
        # Handles both a dict-shaped and an attribute-shaped `.id` return --
        # this has changed across libevdev releases (vgamepad itself assumes
        # the older attribute-style shape in its own get_vid()/get_pid()).
        get = current.get if isinstance(current, dict) else (lambda k, d=0: getattr(current, k, d))
        if not (get("vendor") or get("product") or get("bustype")):
            self.id = {"bustype": 0x03, "vendor": 0x045E, "product": 0x028E}
        return original_create_uinput_device(self, *args, **kwargs)

    libevdev.Device.create_uinput_device = _create_uinput_device_with_xbox_identity
    _linux_uinput_identity_patched = True


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
            if sys.platform != "win32":
                _patch_linux_vgamepad_identity()
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
    """Best-effort, silent early creation of the virtual controller -- see
    gui/app.py's own call site for exactly when this gets called on each
    platform, and why.

    On the Steam Deck, PoE2 (via Proton) and Steam Input typically
    enumerate joysticks once, at their own startup, and treat anything that
    appears afterward as a hot-plug event they may never notice -- so a pad
    that isn't created until a rotation's first controller-encoded press
    can go completely unseen by the game even though poe2bot fired it
    correctly. Creating it here instead gives it a chance to already be
    present by the time PoE2/Steam Input go looking for controllers.

    Windows/XInput hot-plug detection is normally far more reliable than
    that, but the exact same class of problem reappears whenever Steam
    Input is active for the game on desktop Windows too -- Steam Input
    itself only detects controllers present at ITS OWN startup, the same
    as Proton/Steam Input on the Deck, and a virtual pad created after the
    game (and Steam) already launched can go unseen the same way. Unlike
    Linux, this is never called unconditionally on Windows, though -- app.py
    only calls it there once the user has actually switched Active Device
    to Controller, so a keyboard-only Windows user is never handed an
    unasked-for virtual Xbox controller device (and, if ViGEmBus isn't
    installed yet, an unasked-for driver dependency) just for starting the
    app."""
    try:
        _get_pad()
    except Exception as e:
        log.info("Controller warm-up skipped, will retry on first real press: %s", e)


def press(name: str):
    """Press and flush a controller button/trigger. `name` is the bare
    button name (e.g. "a", "lt"), not the "controller:"-prefixed form.
    When passthrough is enabled (see set_passthrough_enabled), this only
    records that the bot wants `name` pressed -- the actual pad write is
    OR-merged with whatever the real controller is doing at that instant
    (a simultaneous real press must not get clobbered) and flushed
    immediately after, right here, not deferred to the passthrough loop's
    own next tick -- so a rotation's timing isn't held hostage to that
    loop's poll interval."""
    _get_pad()
    passthrough = False
    with _pad_lock:
        _bot_wants[name] = True
        passthrough = _passthrough_enabled
        if not passthrough:
            _write_bot_only_locked(name, True)
    if passthrough:
        _flush_combined_state()


def release(name: str):
    """Release and flush a controller button/trigger -- see press()'s
    docstring for how this interacts with passthrough mode."""
    _get_pad()
    passthrough = False
    with _pad_lock:
        _bot_wants.pop(name, None)
        passthrough = _passthrough_enabled
        if not passthrough:
            _write_bot_only_locked(name, False)
    if passthrough:
        _flush_combined_state()


def _write_bot_only_locked(name: str, is_press: bool):
    """press()/release()'s pre-passthrough behavior: write directly to the
    pad and flush immediately. Only used while passthrough is OFF -- once
    it's on, _flush_combined_state() is the single place that ever writes
    to the pad, so the bot's own wishes and the real controller's state can
    never race each other into an inconsistent write. Caller must already
    hold _pad_lock."""
    pad = _get_pad()
    if name in _TRIGGERS:
        (pad.left_trigger if name == "lt" else pad.right_trigger)(value=255 if is_press else 0)
    else:
        (pad.press_button if is_press else pad.release_button)(
            button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
    pad.update()


def set_passthrough_enabled(enabled: bool):
    """Enables/disables continuous mirroring of the real controller (see
    module docstring) -- app.py calls this whenever Active Device becomes
    (or stops being) Controller, both at startup and from the Settings
    toggle/rotation's own device switch. Starts (or stops) a dedicated
    background thread; safe to call repeatedly with the same value (a
    no-op past the first time)."""
    global _passthrough_enabled, _passthrough_thread
    with _pad_lock:
        if enabled == _passthrough_enabled:
            return
        _passthrough_enabled = enabled
        if enabled:
            _passthrough_stop.clear()
            _passthrough_thread = threading.Thread(
                target=_passthrough_loop, name="controller-passthrough", daemon=True)
            _passthrough_thread.start()
        else:
            _passthrough_stop.set()
            _passthrough_thread = None


def _passthrough_loop():
    while not _passthrough_stop.wait(timeout=_PASSTHROUGH_POLL_S):
        _flush_combined_state()


def _flush_combined_state():
    """Recomputes and writes the CURRENT combined (real controller + bot)
    state to the virtual pad. Called both periodically by the passthrough
    loop (so stick movement and real button changes stay fresh even when
    the bot isn't doing anything) and immediately by press()/release()
    (so the bot's own timing never waits on the loop's own poll interval).
    Silently skipped if the pad isn't available yet (e.g. ViGEmBus not
    installed) -- matches warm_up()'s own best-effort philosophy; the very
    next tick (or press()) retries."""
    try:
        pad = _get_pad()
    except ControllerUnavailable:
        return
    snapshot = controller_input.get_controller_reader().snapshot()
    with _pad_lock:
        if not _passthrough_enabled:
            return  # disabled in the gap between this call being scheduled and acquiring the lock
        for name in _DIGITAL_BUTTONS:
            pressed = _bot_wants.get(name, False) or snapshot.buttons.get(name, False)
            (pad.press_button if pressed else pad.release_button)(
                button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        for name, real_value in (("lt", snapshot.lt), ("rt", snapshot.rt)):
            bot_value = 255 if _bot_wants.get(name, False) else 0
            (pad.left_trigger if name == "lt" else pad.right_trigger)(value=max(bot_value, real_value))
        lx, ly = snapshot.left_stick
        rx, ry = snapshot.right_stick
        pad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        pad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        pad.update()


def release_all():
    """Releases every known button/trigger and flushes once -- called on app
    shutdown. A no-op if no virtual controller was ever created. Built only
    from the confirmed press_button/release_button/left_trigger/right_trigger/
    update API surface, rather than assuming a convenience reset() method
    exists on the installed vgamepad version. Also clears _bot_wants (so a
    stale entry can't linger into some future press) and stops passthrough,
    if it was running."""
    global _pad
    if _pad is None:
        return
    set_passthrough_enabled(False)
    with _pad_lock:
        _bot_wants.clear()
        for name in _DIGITAL_BUTTONS:
            _pad.release_button(button=getattr(_vg.XUSB_BUTTON, _DIGITAL_BUTTONS[name]))
        _pad.left_trigger(value=0)
        _pad.right_trigger(value=0)
        _pad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        _pad.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
        _pad.update()
