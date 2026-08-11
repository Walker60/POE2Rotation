import os
from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional, Tuple

import keyboard

from poe2bot import templates

VALID_MODES = ("once", "loop")
VALID_PAUSE_MODES = ("duration", "toggle")
VALID_READY_MATCH_TYPES = ("image", "pixel")           # Step.ready_match_type, buff_check.match_type -- visual-only
VALID_CONDITION_MATCH_TYPES = ("image", "pixel", "timer")  # a plain step Condition can also be a pure time gate
VALID_SEARCH_MODES = ("exact", "area")
MAX_REPEAT_COUNT = 50


def _int_or(data: dict, key: str, default: int) -> int:
    """int(data.get(key, default)), but also falls back to `default` when the
    key is present with an explicit JSON null -- dict.get's own default only
    kicks in when the key is absent entirely, so a hand-edited/stale rotation
    file with e.g. "delay_ms": null would otherwise raise int(None) -> TypeError,
    a type storage.py's loaders don't catch, crashing the whole app on launch
    instead of just skipping that one bad file."""
    value = data.get(key)
    return default if value is None else int(value)


def _float_or(data: dict, key: str, default: float) -> float:
    """Same null-safety as _int_or, for float fields (e.g. confidence)."""
    value = data.get(key)
    return default if value is None else float(value)


def _int_tuple(values) -> Optional[tuple]:
    """Coerce a JSON-decoded region/point/color into a tuple of ints, or None
    if `values` itself is None. Element-wise int() (rather than a bare
    tuple()) fixes numerically-valid-but-string-typed hand-edited JSON (e.g.
    "100" instead of 100), and turns genuinely bad data (non-numeric strings,
    wrong element types) into a clean ValueError/TypeError here at load time
    -- caught by storage.py and treated as "skip this bad file" -- instead of
    a confusing failure deep inside a match function much later at runtime."""
    if values is None:
        return None
    return tuple(int(v) for v in values)


@dataclass
class Condition:
    """An extra gate on a Step: it only fires if every one of its conditions
    currently matches, checked once right before firing (no polling/timeout,
    unlike the step's own cooldown check -- a condition is either true right
    now or the cast is skipped this pass)."""
    match_type: str = "image"                                    # "image", "pixel", or "timer"
    name: str = ""   # optional display label (e.g. "Bleeding"); falls back to an auto description in the GUI if blank
    template: Optional[str] = None                              # filename only, resolved via templates.template_path()
    region: Optional[Tuple[int, int, int, int]] = None          # (left, top, width, height), absolute screen px -- image mode
    search_mode: str = "exact"                                   # "exact" (compare `region` directly) or "area" (search
                                                                  # for the template anywhere within `search_region`) -- image mode only
    search_region: Optional[Tuple[int, int, int, int]] = None   # (left, top, width, height), absolute screen px -- only used
                                                                  # when search_mode == "area"; must be >= `region` in both dimensions
    pixel_pos: Optional[Tuple[int, int]] = None                  # (x, y) absolute screen px -- pixel mode
    pixel_color: Optional[Tuple[int, int, int]] = None           # expected (r, g, b) -- pixel mode
    confidence: float = 0.9                                      # image/pixel mode only, unused for timer mode
    timer_seconds: Optional[float] = None                        # timer mode only -- minimum seconds since the owning
                                                                  # step's own last actual fire (see RotationRunner)
    negate: bool = False                                         # invert the match result -- e.g. an image condition
                                                                  # with negate=True gates firing on the image being
                                                                  # ABSENT rather than present. Applies uniformly to
                                                                  # whichever match_type this condition uses; doesn't
                                                                  # affect has_check() -- an uncalibrated negated
                                                                  # condition is still "not configured", not an error.

    def has_check(self) -> bool:
        if self.match_type == "timer":
            return self.timer_seconds is not None and self.timer_seconds > 0
        if self.match_type == "pixel":
            return self.pixel_color is not None
        return bool(self.template)

    @staticmethod
    def from_dict(data: dict) -> "Condition":
        return Condition(
            match_type=data.get("match_type", "image"),
            name=data.get("name", ""),
            template=data.get("template"),
            region=_int_tuple(data.get("region")),
            search_mode=data.get("search_mode", "exact"),
            search_region=_int_tuple(data.get("search_region")),
            pixel_pos=_int_tuple(data.get("pixel_pos")),
            pixel_color=_int_tuple(data.get("pixel_color")),
            confidence=_float_or(data, "confidence", 0.9),
            timer_seconds=_float_or(data, "timer_seconds", None),
            negate=bool(data.get("negate", False)),
        )


@dataclass
class Step:
    key: Optional[str] = None   # None = no keybind assigned yet -- skipped entirely at runtime (no fire,
                                 # no delay); "" = a deliberate sleep/pause (waits out delay_ms, presses
                                 # nothing); anything else = the actual key to press
    name: str = ""   # optional display label (e.g. "Fireball"); falls back to `key` in the GUI if blank
    delay_ms: int = 100
    jitter_ms: int = 0
    hold_ms: int = 0
    hold_jitter_ms: int = 0   # uniform random +/- jitter applied to hold_ms each time (only when hold_ms > 0)
    ready_match_type: str = "image"                             # "image" or "pixel" -- which method below is active
    ready_template: Optional[str] = None                       # filename only, resolved via templates.template_path()
    ready_region: Optional[Tuple[int, int, int, int]] = None   # (left, top, width, height), absolute screen px -- image mode
    ready_search_mode: str = "exact"                            # "exact" (compare `ready_region` directly) or "area" (search
                                                                 # for the template anywhere within `ready_search_region`) -- image mode only
    ready_search_region: Optional[Tuple[int, int, int, int]] = None   # (left, top, width, height), absolute screen px -- only
                                                                 # used when ready_search_mode == "area"; must be >= `ready_region` in both dimensions
    ready_pixel_pos: Optional[Tuple[int, int]] = None           # (x, y) absolute screen px -- pixel mode
    ready_pixel_color: Optional[Tuple[int, int, int]] = None    # expected (r, g, b) when "ready" -- pixel mode
    ready_confidence: float = 0.9
    ready_timeout_ms: int = 300
    conditions: List[Condition] = field(default_factory=list)   # extra gates, checked once, instantly, before firing
    buff_check: Optional[Condition] = None   # same instant image/pixel check as a Condition, but its result swaps
                                              # in buff_hold_ms/buff_delay_ms below instead of gating whether this
                                              # step fires at all -- for animation-speed buffs that aren't always up
    buff_hold_ms: Optional[int] = None       # used instead of hold_ms while buff_check matches; None = no override
    buff_delay_ms: Optional[int] = None      # used instead of delay_ms while buff_check matches; None = no override
    repeat_count: int = 1                    # fire this step this many times per pass; 1 = today's behavior
    repeat_combine_hold: bool = False        # if the key has a hold > 0, hold once for hold_ms * repeat_count and
                                              # delay once afterward, instead of repeat_count independent hold+delay cycles

    def has_ready_check(self) -> bool:
        """True if this step has a cooldown check configured, via whichever
        method ready_match_type currently points at."""
        if self.ready_match_type == "pixel":
            return self.ready_pixel_color is not None
        return bool(self.ready_template)

    @staticmethod
    def from_dict(data: dict) -> "Step":
        # A whitespace-only key (e.g. a hand-edited "key": "   ") is truthy, so it
        # would otherwise slip past both the sleep-step check (`not step.key`) and
        # validate_rotation's `step.key.strip()` guard as a third, unintended
        # pseudo-state -- normalizing it to "" here (a real, deliberate sleep step)
        # or leaving None alone means those two checks never need to know about it.
        raw_key = data.get("key")
        key = raw_key.strip() if isinstance(raw_key, str) else raw_key
        return Step(
            key=key,
            name=data.get("name", ""),
            delay_ms=_int_or(data, "delay_ms", 100),
            jitter_ms=_int_or(data, "jitter_ms", 0),
            hold_ms=_int_or(data, "hold_ms", 0),
            hold_jitter_ms=_int_or(data, "hold_jitter_ms", 0),
            ready_match_type=data.get("ready_match_type", "image"),
            ready_template=data.get("ready_template"),
            ready_region=_int_tuple(data.get("ready_region")),  # JSON round-trips tuples as lists
            ready_search_mode=data.get("ready_search_mode", "exact"),
            ready_search_region=_int_tuple(data.get("ready_search_region")),
            ready_pixel_pos=_int_tuple(data.get("ready_pixel_pos")),
            ready_pixel_color=_int_tuple(data.get("ready_pixel_color")),
            ready_confidence=_float_or(data, "ready_confidence", 0.9),
            ready_timeout_ms=_int_or(data, "ready_timeout_ms", 300),
            conditions=[Condition.from_dict(c) for c in data.get("conditions", [])],
            buff_check=Condition.from_dict(data["buff_check"]) if data.get("buff_check") else None,
            buff_hold_ms=int(data["buff_hold_ms"]) if data.get("buff_hold_ms") is not None else None,
            buff_delay_ms=int(data["buff_delay_ms"]) if data.get("buff_delay_ms") is not None else None,
            repeat_count=_int_or(data, "repeat_count", 1),
            repeat_combine_hold=bool(data.get("repeat_combine_hold", False)),
        )


def replace_step_fields(target: Step, source: Step) -> None:
    """Copy every field of `source` onto `target` in place, preserving
    `target`'s identity. The step-editing form always builds a brand-new Step
    via _read_step_form, even when the user changed nothing -- applying it
    with a plain `editing_steps[i] = new_step` would silently break anything
    that tracks a step by id() across an update, such as the GUI's manual
    row collapse/expand state (see StepEditorMixin._refresh_steps_tree)."""
    for f in fields(Step):
        setattr(target, f.name, getattr(source, f.name))


@dataclass
class Rotation:
    name: str
    mode: str = "once"
    hotkey: Optional[str] = None
    cancel_key: Optional[str] = None   # e.g. the dodge key -- immediately stops this rotation if running
    reset_key: Optional[str] = None    # immediately restarts this rotation from its first step if running
    reset_delay_ms: int = 0            # wait this long after a reset before actually firing step 1 again (0 = instant)
    pause_key: Optional[str] = None    # immediately freezes this rotation in place if running (see pause_mode)
    pause_mode: str = "duration"        # "duration" = auto-resume after pause_duration_ms; "toggle" = press again to resume
    pause_duration_ms: int = 1000       # only used when pause_mode == "duration"
    folder: str = ""   # "/"-separated group path (e.g. "Bosses/HardMode"); "" = ungrouped. NOT persisted
                        # in the JSON -- it's derived from where the file actually lives on disk each time
                        # it's loaded (see storage.py), so there's no way for it to drift out of sync.
    steps: List[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "hotkey": self.hotkey,
            "cancel_key": self.cancel_key,
            "reset_key": self.reset_key,
            "reset_delay_ms": self.reset_delay_ms,
            "pause_key": self.pause_key,
            "pause_mode": self.pause_mode,
            "pause_duration_ms": self.pause_duration_ms,
            "steps": [asdict(step) for step in self.steps],
        }

    @staticmethod
    def from_dict(data: dict) -> "Rotation":
        return Rotation(
            name=data["name"],
            mode=data.get("mode", "once"),
            hotkey=data.get("hotkey"),
            cancel_key=data.get("cancel_key"),
            reset_key=data.get("reset_key"),
            reset_delay_ms=_int_or(data, "reset_delay_ms", 0),
            pause_key=data.get("pause_key"),
            pause_mode=data.get("pause_mode", "duration"),
            pause_duration_ms=_int_or(data, "pause_duration_ms", 1000),
            steps=[Step.from_dict(step) for step in data.get("steps", [])],
        )


def folder_path_problem(folder: str) -> Optional[str]:
    """None if `folder` is a valid '/'-separated group path, else a human-readable
    reason it isn't. Shared by rotation validation and the GUI's rename/move-to-folder
    dialogs, so both reject the same things the same way.

    Backslashes are rejected outright (not just split on) because '/' is the
    only documented separator -- a stray backslash (e.g. a pasted Windows path
    like "..\\..\\Desktop") would otherwise pass through as a single segment
    here, undetected as a '..' traversal attempt, and only get sanitized (not
    rejected) by storage._folder_parts, which is meant as defense-in-depth,
    not the primary check a user actually sees an error message from."""
    if not folder:
        return None
    if "\\" in folder:
        return "Folder path cannot contain backslashes -- use '/' to separate subfolders."
    for part in folder.split("/"):
        part = part.strip()
        if not part:
            return "Folder path cannot have empty segments (leading/trailing/double slash)."
        if part in (".", ".."):
            return "Folder path cannot contain '.' or '..' segments."
    return None


def _search_area_problems(label: str, subject: str, search_mode: str, search_region, region) -> List[str]:
    """Shared by the three image-mode validation blocks below (step's own ready
    check, each condition, buff_check) -- all three calibrate search_mode/
    search_region the same way, so this avoids repeating the same checks three
    times. `subject` is an optional noun phrase (e.g. "buff check ") inserted
    right after `label`, matching how the existing messages around each call
    site are worded."""
    problems = []
    if search_mode not in VALID_SEARCH_MODES:
        problems.append(f"{label}: {subject}search mode must be one of {VALID_SEARCH_MODES}.")
    elif search_mode == "area":
        region_ok = isinstance(region, tuple) and len(region) == 4
        if not (isinstance(search_region, tuple) and len(search_region) == 4):
            problems.append(f"{label}: {subject}search mode is 'area' but no valid search area is calibrated (recalibrate).")
        elif region_ok and (search_region[2] < region[2] or search_region[3] < region[3]):
            problems.append(
                f"{label}: {subject}search area ({search_region[2]}x{search_region[3]}) must be at least as "
                f"large as the calibrated icon ({region[2]}x{region[3]}).")
    return problems


def validate_rotation(rotation: Rotation) -> List[str]:
    """Return a list of human-readable problems with `rotation`. Empty list == valid."""
    problems = []

    if not rotation.name or not rotation.name.strip():
        problems.append("Name cannot be empty.")

    if rotation.mode not in VALID_MODES:
        problems.append(f"Mode must be one of {VALID_MODES}, got '{rotation.mode}'.")

    if rotation.cancel_key and rotation.cancel_key == rotation.hotkey:
        problems.append("Cancel key cannot be the same as this rotation's own trigger hotkey.")

    if rotation.reset_key:
        if rotation.reset_key == rotation.hotkey:
            problems.append("Reset key cannot be the same as this rotation's own trigger hotkey.")
        if rotation.reset_key == rotation.cancel_key:
            problems.append("Reset key cannot be the same as this rotation's own cancel key.")
    if rotation.reset_delay_ms < 0:
        problems.append("Reset delay cannot be negative.")

    if rotation.pause_key:
        if rotation.pause_key == rotation.hotkey:
            problems.append("Pause key cannot be the same as this rotation's own trigger hotkey.")
        if rotation.pause_key == rotation.cancel_key:
            problems.append("Pause key cannot be the same as this rotation's own cancel key.")
        if rotation.pause_key == rotation.reset_key:
            problems.append("Pause key cannot be the same as this rotation's own reset key.")

    if rotation.pause_mode not in VALID_PAUSE_MODES:
        problems.append(f"Pause mode must be one of {VALID_PAUSE_MODES}, got '{rotation.pause_mode}'.")
    if rotation.pause_duration_ms < 0:
        problems.append("Pause duration cannot be negative.")

    folder_problem = folder_path_problem(rotation.folder)
    if folder_problem:
        problems.append(folder_problem)

    if not rotation.steps:
        problems.append("Rotation must have at least one step.")
    elif rotation.mode == "loop" and all(step.key is None for step in rotation.steps):
        # A step with key=None (unassigned) has no wait of any kind at runtime --
        # it's just skipped instantly, unlike a real key or a deliberate ""
        # sleep step. If *every* step in a Loop rotation is like that, the
        # runner would spin one full pass after another with no delay
        # anywhere, pegging a CPU core forever -- catch it here rather than
        # letting it reach the runtime.
        problems.append(
            "A Loop rotation needs at least one step with a key assigned (or a Sleep step) -- "
            "otherwise it would repeat with no delay between passes.")

    for i, step in enumerate(rotation.steps, start=1):
        # key is None -- no keybind assigned yet -- means this step is skipped
        # entirely at runtime (not an error; the GUI's Add Step defaults to this).
        # key == "" means this step is a deliberate sleep/pause: no key to press,
        # it just waits out delay_ms (+/- jitter_ms) like any other step's
        # post-fire wait. Both are falsy, so this check is skipped for either.
        if step.key and step.key.strip():
            try:
                keyboard.key_to_scan_codes(step.key)
            except ValueError:
                problems.append(f"Step {i}: '{step.key}' is not a recognized key name.")

        if step.delay_ms < 0:
            problems.append(f"Step {i}: delay_ms cannot be negative.")
        if step.jitter_ms < 0:
            problems.append(f"Step {i}: jitter_ms cannot be negative.")
        if step.hold_ms < 0:
            problems.append(f"Step {i}: hold_ms cannot be negative.")
        if step.hold_jitter_ms < 0:
            problems.append(f"Step {i}: hold_jitter_ms cannot be negative.")

        if step.ready_match_type not in VALID_READY_MATCH_TYPES:
            problems.append(f"Step {i}: ready_match_type must be one of {VALID_READY_MATCH_TYPES}.")

        if step.has_ready_check():
            if not (0 < step.ready_confidence <= 1):
                problems.append(f"Step {i}: confidence must be greater than 0 and at most 1.")
            if step.ready_timeout_ms < 0:
                problems.append(f"Step {i}: ready_timeout_ms cannot be negative.")
            if step.ready_match_type == "pixel":
                if not (isinstance(step.ready_pixel_pos, tuple) and len(step.ready_pixel_pos) == 2):
                    problems.append(f"Step {i}: has a pixel cooldown check but no calibrated point (recalibrate).")
                if not (isinstance(step.ready_pixel_color, tuple) and len(step.ready_pixel_color) == 3):
                    problems.append(f"Step {i}: has a pixel cooldown check but no calibrated color (recalibrate).")
            else:
                if not (isinstance(step.ready_region, tuple) and len(step.ready_region) == 4):
                    problems.append(f"Step {i}: has an image cooldown check but no calibrated region (recalibrate).")
                if not step.ready_template or not os.path.isfile(templates.template_path(step.ready_template)):
                    problems.append(f"Step {i}: calibrated template image is missing on disk (recalibrate).")
                problems.extend(_search_area_problems(
                    f"Step {i}", "", step.ready_search_mode, step.ready_search_region, step.ready_region))

        for j, condition in enumerate(step.conditions, start=1):
            if not condition.has_check():
                # Not yet calibrated -- treated as "this condition is off," the same
                # as an uncalibrated cooldown check or buff check just above/below,
                # not as an error. Only reachable via hand-edited JSON; the GUI's
                # own Add Condition flow always produces a fully-calibrated one.
                continue
            if condition.match_type not in VALID_CONDITION_MATCH_TYPES:
                problems.append(f"Step {i}, condition {j}: match_type must be one of {VALID_CONDITION_MATCH_TYPES}.")
            if condition.match_type != "timer" and not (0 < condition.confidence <= 1):
                # Confidence is a visual-match fuzziness threshold -- meaningless for a
                # pure time gate, which has no "how close is close enough" to tune.
                problems.append(f"Step {i}, condition {j}: confidence must be greater than 0 and at most 1.")
            if condition.match_type == "timer":
                pass  # has_check() above already proved timer_seconds is a positive number
            elif condition.match_type == "pixel":
                if not (isinstance(condition.pixel_pos, tuple) and len(condition.pixel_pos) == 2):
                    problems.append(f"Step {i}, condition {j}: has no calibrated point (recalibrate).")
                if not (isinstance(condition.pixel_color, tuple) and len(condition.pixel_color) == 3):
                    problems.append(f"Step {i}, condition {j}: has no calibrated color (recalibrate).")
            else:
                if not (isinstance(condition.region, tuple) and len(condition.region) == 4):
                    problems.append(f"Step {i}, condition {j}: has no calibrated region (recalibrate).")
                if not condition.template or not os.path.isfile(templates.template_path(condition.template)):
                    problems.append(f"Step {i}, condition {j}: calibrated template image is missing on disk (recalibrate).")
                problems.extend(_search_area_problems(
                    f"Step {i}, condition {j}", "", condition.search_mode, condition.search_region, condition.region))

        buff_check = step.buff_check
        buff_calibrated = buff_check is not None and buff_check.has_check()
        if buff_calibrated:
            if buff_check.match_type not in VALID_READY_MATCH_TYPES:
                problems.append(f"Step {i}: buff check match_type must be one of {VALID_READY_MATCH_TYPES}.")
            if not (0 < buff_check.confidence <= 1):
                problems.append(f"Step {i}: buff check confidence must be greater than 0 and at most 1.")
            if buff_check.match_type == "pixel":
                if not (isinstance(buff_check.pixel_pos, tuple) and len(buff_check.pixel_pos) == 2):
                    problems.append(f"Step {i}: buff check has no calibrated point (recalibrate).")
                if not (isinstance(buff_check.pixel_color, tuple) and len(buff_check.pixel_color) == 3):
                    problems.append(f"Step {i}: buff check has no calibrated color (recalibrate).")
            else:
                if not (isinstance(buff_check.region, tuple) and len(buff_check.region) == 4):
                    problems.append(f"Step {i}: buff check has no calibrated region (recalibrate).")
                if not buff_check.template or not os.path.isfile(templates.template_path(buff_check.template)):
                    problems.append(f"Step {i}: buff check calibrated template image is missing on disk (recalibrate).")
                problems.extend(_search_area_problems(
                    f"Step {i}", "buff check ", buff_check.search_mode, buff_check.search_region, buff_check.region))
            if step.buff_hold_ms is None and step.buff_delay_ms is None:
                problems.append(f"Step {i}: buff check is calibrated but neither Buff Hold nor Buff Delay is set, "
                                 f"so it would never have any effect.")

        if step.buff_hold_ms is not None and step.buff_hold_ms < 0:
            problems.append(f"Step {i}: buff hold_ms cannot be negative.")
        if step.buff_delay_ms is not None and step.buff_delay_ms < 0:
            problems.append(f"Step {i}: buff delay_ms cannot be negative.")
        if (step.buff_hold_ms is not None or step.buff_delay_ms is not None) and not buff_calibrated:
            problems.append(f"Step {i}: buff hold/delay override is set but no buff check is calibrated "
                             f"(it would never apply).")

        if step.repeat_count < 1:
            problems.append(f"Step {i}: repeat count must be at least 1.")
        elif step.repeat_count > MAX_REPEAT_COUNT:
            problems.append(f"Step {i}: repeat count of {step.repeat_count} is over the sanity limit of "
                             f"{MAX_REPEAT_COUNT} -- with Combine Hold this would hold a key down for a "
                             f"very long time. Lower it, or split this into a Loop rotation instead.")

    return problems
