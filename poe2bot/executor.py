import random
import threading
import time

import keyboard
import mss
from PIL import Image, ImageChops, ImageStat

from poe2bot import templates
from poe2bot.focus import is_game_focused
from poe2bot.log_setup import get_logger
from poe2bot.models import Rotation, Step

log = get_logger()

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_WAITING_FOCUS = "waiting_focus"

_template_image_cache = {}   # filename -> grayscale PIL.Image.Image, decoded once and reused
_thread_local = threading.local()


def _load_template_image(filename: str) -> Image.Image:
    """Load and cache a calibration template as grayscale, so repeated ready-checks
    never re-read/re-decode the PNG from disk or re-convert it to grayscale."""
    image = _template_image_cache.get(filename)
    if image is None:
        image = Image.open(templates.template_path(filename)).convert("L")
        image.load()  # force-read pixel data now, so later use never touches the file again
        _template_image_cache[filename] = image
    return image


def _screen_capture():
    """A thread-local mss capture instance. mss is not safe to share across
    threads, and each RotationRunner polls readiness from its own dedicated
    thread, so this caches one instance per thread rather than per process."""
    sct = getattr(_thread_local, "sct", None)
    if sct is None:
        sct = mss.mss()
        _thread_local.sct = sct
    return sct


def _close_screen_capture():
    """Release the current thread's mss capture resources, if any were ever
    created. Called when a RotationRunner's thread finishes so GDI handles
    don't accumulate across many start/stop cycles over a long session."""
    sct = getattr(_thread_local, "sct", None)
    if sct is not None:
        sct.close()
        _thread_local.sct = None


def _capture_region(region) -> Image.Image:
    """Screenshot exactly `region` (left, top, width, height) -- and only that
    region. pyautogui/Pillow's own screen grab always captures the *entire*
    screen on Windows and crops afterward regardless of region size; mss
    captures just the requested rectangle directly via BitBlt, which is what
    actually matters for a check that runs in a tight polling loop."""
    left, top, width, height = region
    monitor = {"left": left, "top": top, "width": width, "height": height}
    shot = _screen_capture().grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.rgb)


def _check_ready(step: Step) -> bool:
    """True if step's calibrated 'ready' template currently matches on screen.

    Compares a screenshot of exactly the calibrated region directly against the
    cached template (mean pixel difference) instead of searching for the
    template's position with OpenCV. Calibration already tells us precisely
    where the icon is, so there's nothing to search for -- skipping the
    sliding-window correlation search entirely is what actually makes this
    fast, not just a lower confidence threshold. Trade-off: this assumes the
    icon hasn't moved since calibration (e.g. the game window resizing or UI
    scale changing) -- if it has, recalibrate rather than expecting this to
    still find it elsewhere in the region.

    `ready_confidence` (0-1, higher = stricter) is mapped onto an equivalent
    max-allowed mean pixel difference so existing calibrated steps keep behaving
    the same way without needing to be retuned for this simpler match: 0.9
    (the default) allows a mean difference of about 10% of the 0-255 range.

    Broadly defensive: a rotation loaded from a hand-edited or stale JSON file
    can reach here without ever passing through validate_rotation, so any
    failure to read/match the template (missing file, corrupt PNG, a captured
    region that no longer matches the template's size, etc.) is logged and
    treated as not-ready rather than propagating out of the thread.
    """
    if not step.ready_template:
        return False
    try:
        template = _load_template_image(step.ready_template)
        screenshot = _capture_region(step.ready_region).convert("L")
        if screenshot.size != template.size:
            log.error(
                f"ready-check for '{step.key}': captured region {screenshot.size} doesn't "
                f"match calibrated template {template.size} -- recalibrate this step")
            return False
        mean_diff = ImageStat.Stat(ImageChops.difference(screenshot, template)).mean[0]
        max_allowed_diff = (1 - step.ready_confidence) * 255
        return mean_diff <= max_allowed_diff
    except Exception as e:
        log.error(f"ready-check for '{step.key}' failed ({type(e).__name__}: {e}); treating as not ready")
        return False


class RotationRunner:
    """Drives a single rotation's execution on its own daemon thread.

    Cancellation is cooperative via a threading.Event: every wait in the
    run loop is a bounded Event.wait() rather than time.sleep(), so stop()
    wakes the thread within milliseconds instead of waiting out a full sleep.
    """

    def __init__(self, rotation: Rotation, on_status_change=None):
        self.rotation = rotation
        self.on_status_change = on_status_change
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"rotation-{self.rotation.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _notify(self, status: str):
        if self.on_status_change:
            self.on_status_change(self.rotation.name, status)

    def _run(self):
        log.info(f"[{self.rotation.name}] starting ({self.rotation.mode})")
        self._notify(STATUS_RUNNING)
        try:
            if self.rotation.mode == "loop":
                while not self._stop_event.is_set():
                    if not self._run_once():
                        break
            else:
                self._run_once()
        finally:
            _close_screen_capture()
            log.info(f"[{self.rotation.name}] stopped")
            self._notify(STATUS_IDLE)

    def _run_once(self) -> bool:
        for step in self.rotation.steps:
            if self._stop_event.is_set():
                return False
            if not self._wait_for_focus_or_stop():
                return False
            if self._wait_until_ready(step):
                self._fire_step(step)
                # Only pay the post-cast delay/jitter after an actual fire -- there's no
                # cast animation to wait out for a step that was skipped, so a skipped
                # cast falls straight through to the next step instead of also eating
                # this step's full delay on top of the ready-check time.
                if not self._sleep_delay(step):
                    return False
            elif not self._stop_event.is_set():
                log.warning(f"[{self.rotation.name}] '{step.key}' not ready after "
                            f"{step.ready_timeout_ms}ms; skipping this cast")
        return True

    def _wait_for_focus_or_stop(self) -> bool:
        notified_waiting = False
        while not is_game_focused():
            if not notified_waiting:
                self._notify(STATUS_WAITING_FOCUS)
                notified_waiting = True
            if self._stop_event.wait(timeout=0.1):
                return False
        if notified_waiting:
            self._notify(STATUS_RUNNING)
        return True

    def _wait_until_ready(self, step: Step) -> bool:
        """True immediately if step has no cooldown check configured (no behavior
        change). Otherwise polls _check_ready until it matches or ready_timeout_ms
        elapses, sleeping cooperatively (stop/panic returns immediately) between
        polls -- same cancellation idiom as _wait_for_focus_or_stop/_sleep_delay."""
        if not step.ready_template:
            return True
        deadline = time.perf_counter() + step.ready_timeout_ms / 1000
        while not _check_ready(step):
            if time.perf_counter() >= deadline:
                return False
            if self._stop_event.wait(timeout=0.05):
                return False
        return True

    def _fire_step(self, step: Step):
        if step.hold_ms > 0:
            hold = step.hold_ms
            if step.hold_jitter_ms:
                hold += random.uniform(-step.hold_jitter_ms, step.hold_jitter_ms)
            hold = max(0, hold)
            log.debug(f"[{self.rotation.name}] key={step.key} hold_ms={hold:.0f}")
            try:
                keyboard.press(step.key)
                self._stop_event.wait(timeout=hold / 1000)
            finally:
                keyboard.release(step.key)
        else:
            log.debug(f"[{self.rotation.name}] key={step.key} (tap)")
            keyboard.send(step.key)

    def _sleep_delay(self, step: Step) -> bool:
        delay = step.delay_ms
        if step.jitter_ms:
            delay += random.uniform(-step.jitter_ms, step.jitter_ms)
        return not self._stop_event.wait(timeout=max(0, delay) / 1000)


class RotationManager:
    """Owns one RotationRunner per loaded rotation; the single object the
    GUI and hotkey manager both talk to."""

    def __init__(self, on_status_change=None):
        self._external_callback = on_status_change
        self._runners = {}
        self._status = {}

    def _handle_status(self, name: str, status: str):
        self._status[name] = status
        if self._external_callback:
            self._external_callback(name, status)

    def load(self, rotation: Rotation):
        self.unload(rotation.name)
        self._runners[rotation.name] = RotationRunner(rotation, on_status_change=self._handle_status)
        self._status[rotation.name] = STATUS_IDLE

    def unload(self, name: str):
        runner = self._runners.pop(name, None)
        if runner:
            runner.stop()
        self._status.pop(name, None)

    def trigger(self, name: str):
        runner = self._runners.get(name)
        if runner is None:
            log.warning(f"trigger() called for unknown rotation '{name}'")
            return
        if not runner.is_running:
            runner.start()
        elif runner.rotation.mode == "loop":
            runner.stop()
        # running + mode == "once": ignore, no overlapping duplicate runs

    def cancel(self, name: str):
        """Immediately stop `name` if it's running. Unlike trigger(), never
        starts it -- for an interrupt key (e.g. dodge) that should only ever
        cut a rotation short, never toggle it on."""
        runner = self._runners.get(name)
        if runner is not None:
            runner.stop()

    def stop_all(self):
        for runner in self._runners.values():
            runner.stop()

    def status(self, name: str) -> str:
        return self._status.get(name, STATUS_IDLE)
