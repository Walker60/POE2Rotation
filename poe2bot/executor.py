import random
import threading
import time
from typing import List, Optional, Tuple, Union

import keyboard
import mouse

from poe2bot import config, controller, hotkeys
from poe2bot.focus import is_game_focused
from poe2bot.log_setup import get_logger
from poe2bot.matching import (
    _close_screen_capture, _condition_override, _fire_gate_passes, _group_gate_passes,
    _hold_override, _invalidate_game_window_size_cache, _max_fire_timeout_ms,
)
from poe2bot.models import Condition, ConditionGroup, Rotation, SkillConditionGroup, Step, iter_steps

log = get_logger()

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_WAITING_FOCUS = "waiting_focus"
STATUS_PAUSED = "paused"
STATUS_RESETTING = "resetting"

_INTERRUPT_POLL_S = 0.1   # how often pause/reset/focus waits recheck for a stop/reset/pause request
_FIRE_GATE_POLL_S = 0.05  # how often _wait_for_fire_gate rechecks a "fire" condition with timeout_ms set


class RotationRunner:
    """Drives a single rotation's execution on its own daemon thread.

    Cancellation is cooperative via a threading.Event: every wait in the
    run loop is a bounded Event.wait() rather than time.sleep(), so stop()
    wakes the thread within milliseconds instead of waiting out a full sleep.
    """

    def __init__(self, rotation: Rotation, on_status_change=None, on_activity=None):
        self.rotation = rotation
        self.on_status_change = on_status_change
        self.on_activity = on_activity
        self._stop_event = threading.Event()
        # These four are written only by stop()/reset()/pause() (another thread) and
        # read/cleared only by _run's own thread, except _paused which pause()
        # also reads/clears directly (see pause() for why that one's safe).
        # _stop_requested exists separately from _stop_event because stop()/reset()/
        # pause() all set the same _stop_event to wake any in-flight cooperative wait
        # instantly -- without a flag recording *which* of them was actually asked
        # for, a reset()/pause() that lands around the same instant as a stop() would
        # look identical to _run() afterward, and a real stop() could be silently
        # swallowed (see stop()/pause()/_do_pause()/_wait_reset_delay() below).
        self._stop_requested = False
        self._reset_requested = False
        self._pause_requested = False
        self._paused = threading.Event()
        self._current_path: Tuple[int, ...] = ()   # path of indices from rotation.steps down to whatever
                                                     # entry is currently executing (through any nested
                                                     # ConditionGroups) -- () means "not started yet."
                                                     # Touched only by _run_once/_run_entries' own thread.
        # id(step) -> time.perf_counter() of that step's own last actual fire, for
        # any "timer" Condition gating it (see _check_condition). Survives a Loop
        # wraparound and a pause/resume -- a real cooldown keeps ticking regardless
        # -- and is cleared only on reset() (restart from step 1) or whenever a run
        # truly ends (see _run's reset branch and finally block). Touched only by
        # _run/_run_once/_fire_repeats, all on this runner's own thread.
        self._step_last_fired = {}
        self._thread = None
        # Loop-mode-only lap stats -- logged to Activity on each full pass so a
        # rotation's actual cycle time is visible without instrumenting it by hand.
        # Meaningless (never touched) for "once" mode.
        self._lap_count = 0
        self._lap_started_at = None
        # True from start() until end_hold() (see its docstring) -- a "once" mode
        # rotation whose trigger is still physically held reads this as license to
        # keep looping past its normal single pass, exactly like a "loop" rotation
        # does unconditionally. Meaningless for "loop" mode, which never consults it.
        self._hold_active = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._stop_requested = False
        self._reset_requested = False
        self._pause_requested = False
        self._paused.clear()
        self._current_path = ()
        self._hold_active = True
        self._thread = threading.Thread(
            target=self._run, name=f"rotation-{self.rotation.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_requested = True
        self._stop_event.set()

    def end_hold(self):
        """Marks this rotation's trigger hotkey as physically released -- see
        _run's own loop-continuation check and trigger_released's docstring.
        A "once" mode rotation currently mid-hold finishes whatever pass is
        already in progress (never interrupted mid-step just because the key
        came up -- there's nothing to cooperatively wake, unlike stop()/
        reset()/pause()) and then stops at its next lap-boundary check, same
        as a plain tap-and-release always has. Harmless no-op if called while
        not running, and never consulted at all by a "loop" mode rotation."""
        self._hold_active = False

    def reset(self):
        """Immediately abandon whatever step is currently in progress, then
        restart this rotation's step sequence from the beginning -- after
        rotation.reset_delay_ms if set (0 = instant, the default; see
        _wait_reset_delay). No-op if not running.

        Implemented by (ab)using the same stop_event every cooperative wait in
        _run_once already watches for instant wakeup -- no new polling needed --
        with _reset_requested marking that this particular wakeup should restart
        the step sequence rather than actually stop the thread. If stop() also
        lands around the same instant, stop() wins (see _run/_do_pause/
        _wait_reset_delay's _stop_requested checks) -- a reset can't override
        an explicit stop."""
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
        just a no-op; it's already counting down to auto-resume). Same
        stop()-wins guarantee as reset() if a stop() lands around the same
        instant.

        The `_pause_requested` check below (distinct from `_paused.is_set()`)
        guards a narrow but real race: `_paused` isn't actually set until
        `_do_pause()` runs a couple of lines into its own body, so a second
        pause() (e.g. an OS key-repeat firing the pause hotkey twice) landing
        after the first request but before `_do_pause` has started would
        otherwise fall through to the same "start a new pause" branch again --
        re-setting `_stop_event` a second time *after* `_run`'s dispatch loop
        already cleared it to enter `_do_pause`, which that method's own wait
        loops never clear again on their own, spinning them continuously."""
        if not self.is_running:
            return
        if self._paused.is_set():
            if self.rotation.pause_mode == "toggle":
                self._paused.clear()
            return
        if self._pause_requested:
            return  # a pause is already pending -- let it resolve, don't queue a second one
        self._pause_requested = True
        self._stop_event.set()

    def _notify(self, status: str):
        if self.on_status_change:
            self.on_status_change(self.rotation.name, status)

    def _notify_activity(self, message: str):
        if self.on_activity:
            self.on_activity(self.rotation.name, message)

    def _is_fireless(self) -> bool:
        """True if every step in this rotation is either keyless or disabled
        -- meaning a full pass produces no wait of any kind and would repeat
        with zero delay if allowed to loop. Shared by _run's startup guard
        (for "loop" mode) and its hold-continuation check (for a "once" mode
        rotation still being held past its first pass, see end_hold's
        docstring) -- both need to refuse spinning a CPU core with no delay
        anywhere, whichever reason it's about to repeat for."""
        return (not self.rotation.steps
                or all(step.key is None or not step.enabled for step in iter_steps(self.rotation.steps)))

    def _run(self):
        if self.rotation.mode == "loop" and self._is_fireless():
            # Same condition validate_rotation now rejects at save time -- kept
            # here too since a rotation can reach the runtime without ever
            # passing through it (a pre-existing file saved before that check
            # existed, or a hand-edited one). Without this, a Loop rotation
            # with no step that ever fires or sleeps would spin one full pass
            # after another with no delay anywhere, pegging a CPU core forever.
            log.error(f"[{self.rotation.name}] refusing to start: a Loop rotation needs at least "
                      f"one step with a key assigned (or a Sleep step), or it would spin with no delay")
            self._notify_activity(
                "Refused to start: no step has a key assigned -- a Loop rotation would spin with no delay")
            self._notify(STATUS_IDLE)
            return
        log.info(f"[{self.rotation.name}] starting ({self.rotation.mode})")
        self._notify(STATUS_RUNNING)
        self._notify_activity(f"Rotation started ({self.rotation.mode} mode)")
        self._lap_count = 0
        self._lap_started_at = time.perf_counter()
        resume_index: Tuple[int, ...] = ()
        try:
            while True:
                if self._stop_requested:
                    break
                completed = self._run_once(resume_index)
                # A genuine stop() always wins over a reset()/pause() that happened to
                # arrive around the same instant -- all three share the same stop_event
                # to wake any in-flight cooperative wait instantly, so without this
                # check first, an unluckily-timed reset/pause hotkey could silently
                # swallow the user's stop request and leave the rotation running.
                if self._stop_requested:
                    break
                if self._reset_requested:
                    log.info(f"[{self.rotation.name}] reset to start")
                    self._notify_activity("Reset to step 1")
                    self._reset_requested = False
                    self._stop_event.clear()
                    resume_index = ()
                    self._step_last_fired.clear()
                    self._lap_count = 0
                    self._lap_started_at = time.perf_counter()
                    if self.rotation.reset_delay_ms > 0 and not self._wait_reset_delay():
                        break  # a genuine stop() arrived during the reset delay
                    continue
                if self._pause_requested:
                    self._pause_requested = False
                    resume_index = self._current_path
                    self._stop_event.clear()
                    if self._do_pause():
                        continue
                    break  # a genuine stop() arrived while paused
                # A "loop" rotation always continues; a "once" rotation only continues
                # while its trigger is still being held down (see end_hold()) -- so
                # holding a "once" rotation's hotkey makes it repeat exactly like a
                # "loop" one, until release lets this check fall through and stop it.
                # _is_fireless() guards the hold case the same way the startup guard
                # above already guards "loop" mode -- a fireless rotation just runs
                # its one normal pass and stops, hold or not, rather than spinning.
                should_repeat = self.rotation.mode == "loop" or (self._hold_active and not self._is_fireless())
                if not completed or not should_repeat:
                    break
                self._lap_count += 1
                lap_seconds = time.perf_counter() - self._lap_started_at
                self._lap_started_at = time.perf_counter()
                self._notify_activity(f"Lap {self._lap_count} complete ({lap_seconds:.1f}s)")
                resume_index = ()
        except Exception as e:
            # Anything escaping here (e.g. an unrecognized key name reaching
            # keyboard.press/send -- validate_rotation only runs at GUI save
            # time, never at load or trigger time, so a stale/hand-edited
            # rotation can still reach this point) would otherwise kill this
            # thread silently: Python's default threading.excepthook only
            # prints to stderr, invisible for a windowed launch, and the
            # finally block below still reports a normal "stopped" -- making a
            # crash indistinguishable from the user pressing Stop.
            log.exception(f"[{self.rotation.name}] crashed: {e}")
            self._notify_activity(f"Rotation crashed: {type(e).__name__}: {e}")
        finally:
            _close_screen_capture()
            log.info(f"[{self.rotation.name}] stopped")
            self._notify_activity("Rotation stopped")
            self._notify(STATUS_IDLE)
            self._step_last_fired.clear()

    def _do_pause(self) -> bool:
        """Block until this pause ends (duration elapsed, or toggled off), or
        reset()/stop() interrupts it. Returns True if _run's main loop should
        continue -- either to resume normally, or because a reset() arrived
        while paused and needs _run's own reset-handling to take over -- and
        False only if a genuine stop() was requested, even if a reset/pause
        also raced in alongside it (a real stop always wins)."""
        log.info(f"[{self.rotation.name}] paused ({self.rotation.pause_mode})")
        self._notify_activity(f"Paused ({self.rotation.pause_mode})")
        self._paused.set()
        self._notify(STATUS_PAUSED)
        try:
            if self.rotation.pause_mode == "toggle":
                while self._paused.is_set() and not self._reset_requested and not self._stop_requested:
                    self._stop_event.wait(timeout=_INTERRUPT_POLL_S)
            else:
                deadline = time.perf_counter() + self.rotation.pause_duration_ms / 1000
                while (time.perf_counter() < deadline
                       and not self._reset_requested and not self._stop_requested):
                    remaining = max(0.0, deadline - time.perf_counter())
                    self._stop_event.wait(timeout=min(_INTERRUPT_POLL_S, remaining))
            return not self._stop_requested
        finally:
            self._paused.clear()
            self._notify(STATUS_RUNNING)
            self._notify_activity("Resumed")

    def _wait_reset_delay(self) -> bool:
        """Blocks for rotation.reset_delay_ms before actually restarting from
        step 1, cooperatively -- stop()/reset()/pause() all wake it instantly
        via the shared stop_event. Returns True to let _run's own dispatch
        proceed -- either the delay elapsed normally, or another reset/pause
        arrived and will be handled the usual way on the next loop iteration
        -- and False only if a genuine stop() was requested, even if a
        reset/pause also raced in alongside it (a real stop always wins)."""
        self._notify(STATUS_RESETTING)
        try:
            deadline = time.perf_counter() + self.rotation.reset_delay_ms / 1000
            while (time.perf_counter() < deadline
                   and not self._reset_requested and not self._pause_requested
                   and not self._stop_requested):
                remaining = max(0.0, deadline - time.perf_counter())
                self._stop_event.wait(timeout=min(_INTERRUPT_POLL_S, remaining))
            return not self._stop_requested
        finally:
            self._notify(STATUS_RUNNING)

    def _run_once(self, resume_path: Tuple[int, ...] = ()) -> bool:
        """Runs one full pass over self.rotation.steps (a Step | ConditionGroup
        list, possibly with ConditionGroups nested arbitrarily deep inside one
        another). `resume_path` is () for a fresh pass, or the exact
        self._current_path a previous pass left behind when interrupted
        (pause/reset) -- see _run_entries for how it's consumed level by
        level. Kept as a thin entry point so callers never need to know
        _run_entries' extra bookkeeping parameters."""
        return self._run_entries(self.rotation.steps, "", resume_path, ())

    def _run_entries(self, entries: List[Union[Step, ConditionGroup]], group_path_label: str,
                      resume_path: Tuple[int, ...], path_prefix: Tuple[int, ...]) -> bool:
        """Runs one full pass over `entries` -- a Step | ConditionGroup list at
        any nesting depth (self.rotation.steps itself, or one ConditionGroup's
        own `entries`) -- starting at the position `resume_path` describes
        relative to THIS level ((): start fresh at index 0). `path_prefix` is
        this level's own location within the overall self._current_path ((:
        at the top level). `group_path_label` is this level's ancestor-label
        chain, joined the same way validate_rotation's _validate_entries does
        (" > " between nested group labels, ", " right before a leaf Step's
        own "Step N") -- "" only at the true top level.

        Only the entry index actually being resumed into at this level carries
        resume info down to its own children (`child_resume` below) -- every
        other entry visited in this same pass is a fresh visit, exactly like
        _run_once's previous single-level `gi == start_top` check. A nested
        ConditionGroup's own gate is rechecked exactly when resuming lands at
        (or a fresh pass reaches) its own first child -- decided purely by
        this level's own next path element, applied independently at every
        depth, never influenced by an ancestor's decision.

        Returns False the instant stop_event fires (mirrors _run_step's own
        contract) -- every enclosing call, all the way back up to _run_once,
        unwinds immediately without doing anything else."""
        start_index = resume_path[0] if resume_path else 0
        for i in range(start_index, len(entries)):
            if self._stop_event.is_set():
                return False
            entry = entries[i]
            my_path = path_prefix + (i,)
            child_resume = resume_path[1:] if i == start_index else ()
            if isinstance(entry, ConditionGroup):
                this_group_label = (f"{group_path_label} > Condition Group {i + 1}"
                                     if group_path_label else f"Condition Group {i + 1}")
                if not child_resume or child_resume[0] == 0:
                    self._current_path = my_path
                    if not self._wait_for_focus_or_stop():
                        return False
                    if not _group_gate_passes(entry):
                        if not self._stop_event.is_set():
                            log.info(f"[{self.rotation.name}] {this_group_label.lower()}'s condition not met; skipping")
                            self._notify_activity(f"{this_group_label}: condition not met, skipping")
                        continue  # next sibling at THIS level, not the rotation's top level
                if not self._run_entries(entry.entries, this_group_label, child_resume, my_path):
                    return False
            else:
                self._current_path = my_path
                label = f"{group_path_label}, Step {i + 1}" if group_path_label else f"Step {i + 1}"
                if not self._run_step(entry, label):
                    return False
        return True

    def _run_step(self, step: Step, label: str) -> bool:
        """Runs exactly one Step -- its own fire/block gate, hold override,
        and repeat_count firing -- regardless of whether it's a top-level
        step or nested inside a ConditionGroup (the caller has already
        decided the group it belongs to, if any, currently allows it to
        run). Returns False the moment stop_event fires, mirroring
        _fire_repeats' own contract, so _run_once can bail out immediately."""
        if not self._wait_for_focus_or_stop():
            return False
        if self._skip_if_disabled_or_unbound(step, label):
            return True
        if not step.key:
            return self._run_sleep_step(step, label)
        return self._run_keyed_step(step, label)

    def _skip_if_disabled_or_unbound(self, step: Step, label: str) -> bool:
        """True (having already logged/notified) if `step` must be skipped
        outright -- no fire, no delay, conditions never even checked --
        either disabled via the GUI's Disable Step toggle, or with no
        keybind assigned yet. False means the caller should keep going,
        including for a "" (sleep) step, which still runs."""
        if not step.enabled:
            log.info(f"[{self.rotation.name}] {label} is disabled; skipping")
            self._notify_activity(f"{label}: disabled, skipping")
            return True
        if step.key is None:
            # No keybind assigned yet -- skip this step entirely, the same as a
            # not-ready/conditions-not-met skip below: no fire, no delay, straight
            # to the next step. Distinct from a "" (sleep) step, which still waits.
            log.info(f"[{self.rotation.name}] {label} has no keybind assigned; skipping")
            self._notify_activity(f"{label}: no key assigned, skipping")
            return True
        return False

    def _run_sleep_step(self, step: Step, label: str) -> bool:
        """Runs a "" (sleep/pause) step: no key to fire, just pause for
        delay_ms (+/- jitter_ms) repeat_count times, exactly like any other
        step's post-fire wait -- see Step.key's docstring for the
        None/""/string distinction. Conditions still gate it exactly like a
        normal step's do -- always an instant check here, never polled (a
        sleep step has nothing to wait to become "ready", only to gate
        on)."""
        if not _fire_gate_passes(step, self._seconds_since_fired(step)):
            if not self._stop_event.is_set():
                log.info(f"[{self.rotation.name}] sleep step's conditions not met; skipping")
                self._notify_activity(f"{label}: sleep conditions not met, skipping")
            return True
        hold_condition = _hold_override(step, self._seconds_since_fired(step))
        log.debug(f"[{self.rotation.name}] sleep {step.delay_ms}ms"
                  + (f" x{step.repeat_count}" if step.repeat_count > 1 else ""))
        self._notify_activity(
            f"{label}: sleeping {step.delay_ms}ms"
            + (f" x{step.repeat_count}" if step.repeat_count > 1 else ""))
        return self._fire_repeats(step, hold_condition)

    def _run_keyed_step(self, step: Step, label: str) -> bool:
        """Runs a step with an actual key to press: waits for its fire/block
        gate (an instant check, or polled up to timeout_ms -- see
        _wait_for_fire_gate), then either fires it repeat_count times or
        logs/notifies why this pass's cast was skipped."""
        gate_passed = self._wait_for_fire_gate(step)
        if gate_passed:
            hold_condition = _hold_override(step, self._seconds_since_fired(step))
            self._notify_activity(
                f"{label} ('{step.key}'): casting" + (" (hold override)" if hold_condition else ""))
            # Only pay the post-cast delay/jitter after an actual fire -- there's no
            # cast animation to wait out for a step that was skipped, so a skipped
            # cast falls straight through to the next step instead of also eating
            # this step's full delay on top of the condition-check time.
            return self._fire_repeats(step, hold_condition)
        elif not self._stop_event.is_set():
            timeout_ms = _max_fire_timeout_ms(step)
            if timeout_ms > 0:
                log.warning(f"[{self.rotation.name}] '{step.key}' conditions not met after "
                            f"{timeout_ms}ms; skipping this cast")
                self._notify_activity(
                    f"{label} ('{step.key}'): conditions not met after {timeout_ms}ms, skipping")
            else:
                log.info(f"[{self.rotation.name}] '{step.key}' conditions not met; skipping this cast")
                self._notify_activity(f"{label} ('{step.key}'): conditions not met, skipping")
        return True

    def _wait_for_focus_or_stop(self) -> bool:
        if not config.REQUIRE_GAME_FOCUS:
            return True
        notified_waiting = False
        while not is_game_focused():
            if not notified_waiting:
                self._notify(STATUS_WAITING_FOCUS)
                self._notify_activity("Waiting for game focus...")
                notified_waiting = True
            if self._stop_event.wait(timeout=_INTERRUPT_POLL_S):
                return False
        if notified_waiting:
            # A resize that happened while unfocused (alt-tabbed away, resized,
            # tabbed back) should be picked up right away, not wait out however
            # much of _GAME_WINDOW_SIZE_CACHE_S happens to be left.
            _invalidate_game_window_size_cache()
            self._notify(STATUS_RUNNING)
            self._notify_activity("Game focus regained, resuming")
        return True

    def _seconds_since_fired(self, step: Step) -> Optional[float]:
        last_fired = self._step_last_fired.get(id(step))
        return (time.perf_counter() - last_fired) if last_fired is not None else None

    def _wait_for_fire_gate(self, step: Step) -> bool:
        """True once step's fire/block conditions allow it to fire (or
        immediately if it has none). A single instant check if none of its
        "fire" conditions have a timeout_ms configured -- exactly a plain
        Condition's traditional behavior. Otherwise polls cooperatively
        (stop/panic returns immediately) up to the longest such timeout --
        this is what used to be a step's own separate, always-polling
        Cooldown Check, generalized to any "fire" condition."""
        deadline_ms = _max_fire_timeout_ms(step)
        if deadline_ms <= 0:
            return _fire_gate_passes(step, self._seconds_since_fired(step))
        deadline = time.perf_counter() + deadline_ms / 1000
        while not _fire_gate_passes(step, self._seconds_since_fired(step)):
            if time.perf_counter() >= deadline:
                return False
            if self._stop_event.wait(timeout=_FIRE_GATE_POLL_S):
                return False
        return True

    def _fire_step(self, step: Step, hold_condition: Optional[Union[Condition, SkillConditionGroup]] = None,
                    hold_override: Optional[int] = None):
        condition_hold = _condition_override(hold_condition, "hold_ms")
        base_hold = hold_override if hold_override is not None else (
            condition_hold if condition_hold is not None else step.hold_ms)
        key_kind = hotkeys.classify(step.key)
        is_controller = key_kind == "controller"
        is_mouse = key_kind == "mouse"
        button = controller.controller_button_of(step.key) if is_controller else None
        mouse_button = hotkeys.mouse_button_of(step.key) if is_mouse else None
        # A virtual controller report has no OS-level input queue the way keyboard.send()'s
        # discrete KEYDOWN/KEYUP messages do -- an instant press+release risks the game's
        # next input poll never observing it. Force a controller tap through the hold
        # branch below with a small floor duration instead of a true instantaneous tap.
        # A synthesized mouse click is a real discrete OS message like a keyboard tap, so
        # it needs no such floor and is left free to take the instant-tap branch below.
        if is_controller and base_hold <= 0:
            base_hold = config.CONTROLLER_MIN_TAP_MS
        if base_hold > 0:
            hold = base_hold
            if step.hold_jitter_ms:
                hold += random.uniform(-step.hold_jitter_ms, step.hold_jitter_ms)
            hold = max(0, hold)
            log.debug(f"[{self.rotation.name}] key={step.key} hold_ms={hold:.0f}"
                      + (" (hold override)" if condition_hold is not None else ""))
            try:
                if is_controller:
                    controller.press(button)
                elif is_mouse:
                    mouse.press(button=mouse_button)
                else:
                    keyboard.press(step.key)
                self._stop_event.wait(timeout=hold / 1000)
            finally:
                if is_controller:
                    controller.release(button)
                elif is_mouse:
                    mouse.release(button=mouse_button)
                else:
                    keyboard.release(step.key)
            self._notify_activity(
                f"Held '{step.key}' {hold:.0f}ms"
                + (" (hold override)" if condition_hold is not None else ""))
        else:
            # is_controller is always False here -- forced into the branch above otherwise.
            log.debug(f"[{self.rotation.name}] key={step.key} (tap)")
            if is_mouse:
                mouse.click(button=mouse_button)
            else:
                keyboard.send(step.key)
            self._notify_activity(f"Tapped '{step.key}'")

    def _sleep_delay(self, step: Step, hold_condition: Optional[Union[Condition, SkillConditionGroup]] = None) -> bool:
        condition_delay = _condition_override(hold_condition, "delay_ms")
        delay = condition_delay if condition_delay is not None else step.delay_ms
        if step.jitter_ms:
            delay += random.uniform(-step.jitter_ms, step.jitter_ms)
        return not self._stop_event.wait(timeout=max(0, delay) / 1000)

    def _fire_repeats(self, step: Step, hold_condition: Optional[Union[Condition, SkillConditionGroup]]) -> bool:
        """Fires `step` step.repeat_count times, then applies the post-fire
        delay -- either as repeat_count independent press/hold/release +
        delay cycles, or, when repeat_combine_hold is set and there's an
        actual hold to combine, as one continuous hold for the combined
        duration followed by a single delay (see README for the worked
        example). The step's own fire/block conditions are only ever
        evaluated once, by the caller, before this runs -- reps never
        re-check either (a "hold" condition's match is likewise captured
        once, in `hold_condition`, before this is called). Returns False the
        moment stop_event fires, mirroring _sleep_delay's contract, so
        _run_once can bail out immediately."""
        if self._stop_event.is_set():
            return False
        # Recorded here, not at _run_once's call sites, so a pause() landing in
        # the gap between the condition check passing and this method being
        # entered can never falsely mark a step as "just fired" -- _stop_event
        # is shared by stop()/reset()/pause(), and the guard just above already
        # bails out before this line for exactly that race.
        self._step_last_fired[id(step)] = time.perf_counter()
        repeat = max(1, step.repeat_count)
        condition_hold = _condition_override(hold_condition, "hold_ms")
        base_hold = condition_hold if condition_hold is not None else step.hold_ms
        if step.key and step.repeat_combine_hold and base_hold > 0 and repeat > 1:
            self._notify_activity(
                f"Combining {repeat} reps into one {base_hold * repeat}ms hold on '{step.key}'")
            self._fire_step(step, hold_condition, hold_override=base_hold * repeat)
            return self._sleep_delay(step, hold_condition)
        for _ in range(repeat):
            if self._stop_event.is_set():
                return False
            if step.key:
                self._fire_step(step, hold_condition)
            if not self._sleep_delay(step, hold_condition):
                return False
        return True


class RotationManager:
    """Owns one RotationRunner per loaded rotation; the single object the
    GUI and hotkey manager both talk to."""

    def __init__(self, on_status_change=None, on_activity=None):
        self._external_callback = on_status_change
        self._activity_callback = on_activity
        self._runners = {}
        self._status = {}

    def _handle_status(self, name: str, status: str):
        self._status[name] = status
        if self._external_callback:
            self._external_callback(name, status)

    def _handle_activity(self, name: str, message: str):
        if self._activity_callback:
            self._activity_callback(name, message)

    def load(self, rotation: Rotation):
        self.unload(rotation.name)
        self._runners[rotation.name] = RotationRunner(
            rotation, on_status_change=self._handle_status, on_activity=self._handle_activity)
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
        # running + mode == "once": ignore, no overlapping duplicate runs -- but note
        # this is also where an OS key-repeat's extra key-down events land while the
        # trigger hotkey is held, which is exactly what keeps a hold in progress (see
        # trigger_released below) from being mistaken for a fresh press.

    def trigger_released(self, name: str):
        """Called when a rotation's trigger hotkey is physically released --
        the counterpart to trigger()'s key-down. A "once" mode rotation that's
        still running treats a *held* trigger like a "loop" rotation, repeating
        for as long as the key stays down (see RotationRunner.end_hold's
        docstring); this is what tells it the hold has ended, so it finishes
        whatever pass is already in progress and then stops, same as it always
        has for a normal tap-and-release. No-op if nothing is running, or for
        a "loop" mode rotation (which starts/stops purely by repeated presses,
        never by being held)."""
        runner = self._runners.get(name)
        if runner is not None:
            runner.end_hold()

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
