import math
import random
import threading
import time
from typing import Optional

import keyboard
import mss
from PIL import Image, ImageChops, ImageStat

from poe2bot import templates
from poe2bot.focus import is_game_focused
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition, Rotation, Step

log = get_logger()

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_WAITING_FOCUS = "waiting_focus"
STATUS_PAUSED = "paused"
STATUS_RESETTING = "resetting"

_MAX_COLOR_DISTANCE = math.sqrt(3 * 255 ** 2)  # largest possible Euclidean distance between two RGB colors

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
    """True if step's calibrated 'ready' state currently matches on screen,
    dispatching to whichever matching method (image or pixel color) this step
    is configured to use."""
    if step.ready_match_type == "pixel":
        return _pixel_matches(step.ready_pixel_pos, step.ready_pixel_color, step.ready_confidence, step.key)
    return _image_matches(step.ready_template, step.ready_region, step.ready_confidence, step.key)


def _check_condition(condition: Condition, label: str) -> bool:
    """True if `condition` currently matches on screen -- same dispatch/matching
    code as _check_ready, just parameterized on a Condition instead of a Step
    (a Condition is an extra, instantly-checked gate on a step; see
    RotationRunner._run_once)."""
    if condition.match_type == "pixel":
        return _pixel_matches(condition.pixel_pos, condition.pixel_color, condition.confidence, label)
    return _image_matches(condition.template, condition.region, condition.confidence, label)


def _check_buff_active(step: Step) -> bool:
    """True if step's calibrated buff_check currently matches on screen --
    reuses _check_condition since a buff check is shaped exactly like a
    Condition, but its result picks an alternate hold/delay in _fire_step/
    _sleep_delay instead of gating whether the step fires at all."""
    if step.buff_check is None or not step.buff_check.has_check():
        return False
    return _check_condition(step.buff_check, step.key or "buff")


def _image_matches(template_filename, region, confidence: float, label: str) -> bool:
    """True if a screenshot of exactly `region` currently matches the cached
    `template_filename` (mean pixel difference) -- shared by both a step's own
    cooldown check and any image-match Condition.

    Compares directly against the calibrated region instead of searching for
    the template's position with OpenCV. Calibration already tells us precisely
    where the icon is, so there's nothing to search for -- skipping the
    sliding-window correlation search entirely is what actually makes this
    fast, not just a lower confidence threshold. Trade-off: this assumes the
    icon hasn't moved since calibration (e.g. the game window resizing or UI
    scale changing) -- if it has, recalibrate rather than expecting this to
    still find it elsewhere in the region.

    `confidence` (0-1, higher = stricter) is mapped onto an equivalent
    max-allowed mean pixel difference so existing calibrated steps keep behaving
    the same way without needing to be retuned for this simpler match: 0.9
    (the default) allows a mean difference of about 10% of the 0-255 range.

    Broadly defensive: a rotation loaded from a hand-edited or stale JSON file
    can reach here without ever passing through validate_rotation, so any
    failure to read/match the template (missing file, corrupt PNG, a captured
    region that no longer matches the template's size, etc.) is logged and
    treated as not-ready rather than propagating out of the thread.
    """
    if not template_filename:
        return False
    try:
        template = _load_template_image(template_filename)
        screenshot = _capture_region(region).convert("L")
        if screenshot.size != template.size:
            log.error(
                f"match check for '{label}': captured region {screenshot.size} doesn't "
                f"match calibrated template {template.size} -- recalibrate this step")
            return False
        mean_diff = ImageStat.Stat(ImageChops.difference(screenshot, template)).mean[0]
        max_allowed_diff = (1 - confidence) * 255
        return mean_diff <= max_allowed_diff
    except Exception as e:
        log.error(f"match check for '{label}' failed ({type(e).__name__}: {e}); treating as not matching")
        return False


def _pixel_matches(pixel_pos, pixel_color, confidence: float, label: str) -> bool:
    """True if the current color at `pixel_pos` is within tolerance of the
    expected `pixel_color` -- shared by both a step's own cooldown check and
    any pixel-match Condition. Much cheaper than image matching (a single 1x1
    capture, no template file, no per-pixel convolution) for the common case
    where readiness shows as a stable color (a border, a glow, a swatch)
    rather than needing a whole icon comparison.

    `confidence` uses the same 0-1, higher-is-stricter meaning as image
    matching, mapped onto an equivalent max-allowed Euclidean RGB distance
    (0.9 default allows roughly 10% of the largest possible color distance).
    """
    if not pixel_pos or not pixel_color:
        return False
    try:
        x, y = pixel_pos
        monitor = {"left": x, "top": y, "width": 1, "height": 1}
        current = tuple(_screen_capture().grab(monitor).rgb)
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(current, pixel_color)))
        max_allowed_distance = (1 - confidence) * _MAX_COLOR_DISTANCE
        return distance <= max_allowed_distance
    except Exception as e:
        log.error(f"pixel match check for '{label}' failed ({type(e).__name__}: {e}); treating as not matching")
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
        # These three are written only by reset()/pause() (another thread) and
        # read/cleared only by _run's own thread, except _paused which pause()
        # also reads/clears directly (see pause() for why that one's safe).
        self._reset_requested = False
        self._pause_requested = False
        self._paused = threading.Event()
        self._current_step_index = 0   # touched only by _run_once's own thread
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._reset_requested = False
        self._pause_requested = False
        self._paused.clear()
        self._current_step_index = 0
        self._thread = threading.Thread(
            target=self._run, name=f"rotation-{self.rotation.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def reset(self):
        """Immediately abandon whatever step is currently in progress, then
        restart this rotation's step sequence from the beginning -- after
        rotation.reset_delay_ms if set (0 = instant, the default; see
        _wait_reset_delay). No-op if not running.

        Implemented by (ab)using the same stop_event every cooperative wait in
        _run_once already watches for instant wakeup -- no new polling needed --
        with _reset_requested marking that this particular wakeup should restart
        the step sequence rather than actually stop the thread."""
        if self.is_running:
            self._reset_requested = True
            self._stop_event.set()

    def pause(self):
        """Immediately freeze this rotation in place if it's running -- same
        interrupt mechanism as reset(), but resumes from the *same* step it was
        on (re-attempting that step's ready-check/fire from scratch) rather
        than restarting from step 1. No-op if not running.

        In "duration" mode, resumes automatically after rotation.pause_duration_ms.
        In "toggle" mode, stays frozen until pause() is called again -- that
        second call is what this method's `if self._paused.is_set()` branch
        handles, distinguishing "start a pause" from "end one already in
        progress" (for "duration" mode a second press while already paused is
        just a no-op; it's already counting down to auto-resume)."""
        if not self.is_running:
            return
        if self._paused.is_set():
            if self.rotation.pause_mode == "toggle":
                self._paused.clear()
            return
        self._pause_requested = True
        self._stop_event.set()

    def _notify(self, status: str):
        if self.on_status_change:
            self.on_status_change(self.rotation.name, status)

    def _run(self):
        log.info(f"[{self.rotation.name}] starting ({self.rotation.mode})")
        self._notify(STATUS_RUNNING)
        resume_index = 0
        try:
            while True:
                if self._stop_event.is_set() and not self._reset_requested and not self._pause_requested:
                    break
                completed = self._run_once(resume_index)
                if self._reset_requested:
                    log.info(f"[{self.rotation.name}] reset to start")
                    self._reset_requested = False
                    self._stop_event.clear()
                    resume_index = 0
                    if self.rotation.reset_delay_ms > 0 and not self._wait_reset_delay():
                        break  # a genuine stop() arrived during the reset delay
                    continue
                if self._pause_requested:
                    self._pause_requested = False
                    resume_index = self._current_step_index
                    self._stop_event.clear()
                    if self._do_pause():
                        continue
                    break  # a genuine stop() arrived while paused
                if not completed or self.rotation.mode != "loop":
                    break
                resume_index = 0
        finally:
            _close_screen_capture()
            log.info(f"[{self.rotation.name}] stopped")
            self._notify(STATUS_IDLE)

    def _do_pause(self) -> bool:
        """Block until this pause ends (duration elapsed, or toggled off), or
        reset()/stop() interrupts it. Returns True if _run's main loop should
        continue -- either to resume normally, or because a reset() arrived
        while paused and needs _run's own reset-handling to take over -- and
        False only for a genuine, non-reset stop() (reset() also sets
        stop_event to wake this wait instantly, so it must be told apart from
        a real stop rather than treated as one)."""
        log.info(f"[{self.rotation.name}] paused ({self.rotation.pause_mode})")
        self._paused.set()
        self._notify(STATUS_PAUSED)
        try:
            if self.rotation.pause_mode == "toggle":
                while self._paused.is_set() and not self._reset_requested:
                    if self._stop_event.wait(timeout=0.1) and not self._reset_requested:
                        return False
            else:
                deadline = time.perf_counter() + self.rotation.pause_duration_ms / 1000
                while time.perf_counter() < deadline and not self._reset_requested:
                    remaining = max(0.0, deadline - time.perf_counter())
                    if self._stop_event.wait(timeout=min(0.1, remaining)) and not self._reset_requested:
                        return False
            return True
        finally:
            self._paused.clear()
            self._notify(STATUS_RUNNING)

    def _wait_reset_delay(self) -> bool:
        """Blocks for rotation.reset_delay_ms before actually restarting from
        step 1, cooperatively -- stop()/reset()/pause() all wake it instantly
        via the shared stop_event. Returns True to let _run's own dispatch
        proceed -- either the delay elapsed normally, or another reset/pause
        arrived and will be handled the usual way on the next loop iteration
        -- and False only for a genuine stop() with nothing else pending."""
        self._notify(STATUS_RESETTING)
        try:
            deadline = time.perf_counter() + self.rotation.reset_delay_ms / 1000
            while (time.perf_counter() < deadline
                   and not self._reset_requested and not self._pause_requested):
                remaining = max(0.0, deadline - time.perf_counter())
                if self._stop_event.wait(timeout=min(0.1, remaining)):
                    break
            return self._reset_requested or self._pause_requested or not self._stop_event.is_set()
        finally:
            self._notify(STATUS_RUNNING)

    def _run_once(self, start_index: int = 0) -> bool:
        for i in range(start_index, len(self.rotation.steps)):
            step = self.rotation.steps[i]
            self._current_step_index = i
            if self._stop_event.is_set():
                return False
            if not self._wait_for_focus_or_stop():
                return False
            if not step.key:
                # Sleep step: no key to check readiness for or fire, just pause for
                # delay_ms (+/- jitter_ms) like any other step's post-fire wait,
                # repeat_count times if set.
                log.debug(f"[{self.rotation.name}] sleep {step.delay_ms}ms"
                          + (f" x{step.repeat_count}" if step.repeat_count > 1 else ""))
                if not self._fire_repeats(step, _check_buff_active(step)):
                    return False
                continue
            ready = self._wait_until_ready(step)
            if ready and all(_check_condition(c, step.key) for c in step.conditions):
                buff_active = _check_buff_active(step)
                # Only pay the post-cast delay/jitter after an actual fire -- there's no
                # cast animation to wait out for a step that was skipped, so a skipped
                # cast falls straight through to the next step instead of also eating
                # this step's full delay on top of the ready-check/condition-check time.
                if not self._fire_repeats(step, buff_active):
                    return False
            elif not self._stop_event.is_set():
                if not ready:
                    log.warning(f"[{self.rotation.name}] '{step.key}' not ready after "
                                f"{step.ready_timeout_ms}ms; skipping this cast")
                else:
                    log.info(f"[{self.rotation.name}] '{step.key}' conditions not met; skipping this cast")
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
        if not step.has_ready_check():
            return True
        deadline = time.perf_counter() + step.ready_timeout_ms / 1000
        while not _check_ready(step):
            if time.perf_counter() >= deadline:
                return False
            if self._stop_event.wait(timeout=0.05):
                return False
        return True

    def _fire_step(self, step: Step, buff_active: bool = False, hold_override: Optional[int] = None):
        base_hold = hold_override if hold_override is not None else (
            step.buff_hold_ms if (buff_active and step.buff_hold_ms is not None) else step.hold_ms)
        if base_hold > 0:
            hold = base_hold
            if step.hold_jitter_ms:
                hold += random.uniform(-step.hold_jitter_ms, step.hold_jitter_ms)
            hold = max(0, hold)
            log.debug(f"[{self.rotation.name}] key={step.key} hold_ms={hold:.0f}"
                      + (" (buff)" if buff_active and step.buff_hold_ms is not None else ""))
            try:
                keyboard.press(step.key)
                self._stop_event.wait(timeout=hold / 1000)
            finally:
                keyboard.release(step.key)
        else:
            log.debug(f"[{self.rotation.name}] key={step.key} (tap)")
            keyboard.send(step.key)

    def _sleep_delay(self, step: Step, buff_active: bool = False) -> bool:
        delay = step.buff_delay_ms if (buff_active and step.buff_delay_ms is not None) else step.delay_ms
        if step.jitter_ms:
            delay += random.uniform(-step.jitter_ms, step.jitter_ms)
        return not self._stop_event.wait(timeout=max(0, delay) / 1000)

    def _fire_repeats(self, step: Step, buff_active: bool) -> bool:
        """Fires `step` step.repeat_count times, then applies the post-fire
        delay -- either as repeat_count independent press/hold/release +
        delay cycles, or, when repeat_combine_hold is set and there's an
        actual hold to combine, as one continuous hold for the combined
        duration followed by a single delay (see README for the worked
        example). The step's own cooldown check and Conditions are only
        ever evaluated once, by the caller, before this runs -- reps never
        re-check either. Returns False the moment stop_event fires, mirroring
        _sleep_delay's contract, so _run_once can bail out immediately."""
        if self._stop_event.is_set():
            return False
        repeat = max(1, step.repeat_count)
        base_hold = step.buff_hold_ms if (buff_active and step.buff_hold_ms is not None) else step.hold_ms
        if step.key and step.repeat_combine_hold and base_hold > 0 and repeat > 1:
            self._fire_step(step, buff_active, hold_override=base_hold * repeat)
            return self._sleep_delay(step, buff_active)
        for _ in range(repeat):
            if self._stop_event.is_set():
                return False
            if step.key:
                self._fire_step(step, buff_active)
            if not self._sleep_delay(step, buff_active):
                return False
        return True


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

    def reset(self, name: str):
        """Immediately restart `name` from its first step if it's running.
        No-op if it's not running -- there's nothing to reset back to the
        start of, and unlike trigger()/cancel() this never changes whether
        the rotation is running at all, only where in its sequence it is."""
        runner = self._runners.get(name)
        if runner is not None:
            runner.reset()

    def pause(self, name: str):
        """Immediately freeze `name` in place if it's running, per its own
        pause_mode/pause_duration_ms. No-op if it's not running."""
        runner = self._runners.get(name)
        if runner is not None:
            runner.pause()

    def stop_all(self):
        for runner in self._runners.values():
            runner.stop()

    def status(self, name: str) -> str:
        return self._status.get(name, STATUS_IDLE)
