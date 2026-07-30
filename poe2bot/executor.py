import random
import threading
import time

import keyboard
import pyautogui

from poe2bot import templates
from poe2bot.focus import is_game_focused
from poe2bot.log_setup import get_logger
from poe2bot.models import Rotation, Step

log = get_logger()

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_WAITING_FOCUS = "waiting_focus"


def _check_ready(step: Step) -> bool:
    """True if step's calibrated 'ready' template currently matches on screen.

    Broadly defensive: a rotation loaded from a hand-edited or stale JSON file can
    reach here without ever passing through validate_rotation, so any failure to
    read/match the template (missing file, corrupt PNG, malformed region, etc.) is
    logged and treated as not-ready rather than propagating out of the thread.
    """
    path = templates.template_path(step.ready_template)
    try:
        pyautogui.locateOnScreen(
            path, region=step.ready_region, confidence=step.ready_confidence, grayscale=True)
        return True
    except pyautogui.ImageNotFoundException:
        return False
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
            elif not self._stop_event.is_set():
                log.warning(f"[{self.rotation.name}] '{step.key}' not ready after "
                            f"{step.ready_timeout_ms}ms; skipping this cast")
            if not self._sleep_delay(step):
                return False
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

    def stop_all(self):
        for runner in self._runners.values():
            runner.stop()

    def status(self, name: str) -> str:
        return self._status.get(name, STATUS_IDLE)
