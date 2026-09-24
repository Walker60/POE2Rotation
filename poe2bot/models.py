import os
from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional, Tuple, TypeVar, Union

import keyboard

from poe2bot import controller, hotkeys, templates

VALID_MODES = ("once", "loop")
VALID_PAUSE_MODES = ("duration", "toggle")
VALID_CONDITION_MATCH_TYPES = ("image", "pixel", "timer")  # a Condition can also be a pure time gate
VALID_CONDITION_ACTIONS = ("fire", "block", "hold")   # what a Condition does once it matches -- see Condition.action
VALID_SEARCH_MODES = ("exact", "area")
VALID_MATCH_LOGIC = ("all", "any")  # how a SkillConditionGroup's own children combine -- see SkillConditionGroup
MAX_REPEAT_COUNT = 50
MAX_GROUP_NESTING_DEPTH = 5  # sanity cap on how deep Rotation Conditions can nest -- see validate_rotation


_T = TypeVar("_T")


def _int_or(data: dict, key: str, default: _T) -> Union[int, _T]:
    """int(data.get(key, default)), but also falls back to `default` when the
    key is present with an explicit JSON null -- dict.get's own default only
    kicks in when the key is absent entirely, so a hand-edited/stale rotation
    file with e.g. "delay_ms": null would otherwise raise int(None) -> TypeError,
    a type storage.py's loaders don't catch, crashing the whole app on launch
    instead of just skipping that one bad file. `default` may itself be None
    (e.g. Condition.hold_ms/delay_ms, where "not set" is a meaningful value) --
    generic over `default`'s type so callers passing a concrete int default
    still get a plain `int` back, not `int | None`."""
    value = data.get(key)
    return default if value is None else int(value)


def _float_or(data: dict, key: str, default: _T) -> Union[float, _T]:
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
    """A rule attached to a Step: when its match currently holds (subject to
    `negate`), `action` decides what happens. Multiple conditions on the same
    step combine as: every "fire" condition must currently match AND no
    "block" condition currently matches (either one skips the step this
    pass, checked once, instantly, unless `timeout_ms` says to wait -- see
    action's own docstring below); "hold" conditions never gate firing, they
    only override hold_ms/delay_ms while they match (the first matching one,
    in list order, wins if more than one does)."""
    match_type: str = "image"                                    # "image", "pixel", or "timer"
    name: str = ""   # optional display label (e.g. "Bleeding"); falls back to an auto description in the GUI if blank
    action: str = "fire"    # "fire" (require this to gate the step firing), "block" (veto firing while this
                             # matches), or "hold" (no effect on whether the step fires -- just overrides
                             # hold_ms/delay_ms below while it matches)
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
                                                                  # with negate=True triggers its action on the image
                                                                  # being ABSENT rather than present. Applies uniformly
                                                                  # to whichever match_type/action this condition uses;
                                                                  # doesn't affect has_check() -- an uncalibrated
                                                                  # negated condition is still "not configured", not an
                                                                  # error.
    timeout_ms: int = 0     # "fire" only: 0 (default) means a single instant check, exactly like a plain
                             # condition always has; >0 polls up to this long for this condition to start
                             # matching before the step is skipped this pass -- this is what used to be a
                             # step's own separate, always-polling Cooldown Check, generalized to any
                             # "fire" condition (see RotationRunner._wait_for_fire_gate). Ignored for
                             # "block"/"hold" -- those are always instant, one-shot checks.
    hold_ms: Optional[int] = None    # "hold" only: replaces the owning step's hold_ms while this condition
                                      # matches; None = don't override hold. Ignored for "fire"/"block".
    delay_ms: Optional[int] = None   # "hold" only: replaces the owning step's delay_ms while this condition
                                      # matches; None = don't override delay. Ignored for "fire"/"block".
    calib_width: Optional[int] = None   # image/pixel mode only: the game window's client size at the moment
    calib_height: Optional[int] = None  # region/pixel_pos/search_region were captured -- None (a rotation
                                         # calibrated before this existed, or one where the game window
                                         # couldn't be found at calibration time) means "no reference size
                                         # recorded," so no auto-rescaling is attempted, same as before this
                                         # existed. When set and the CURRENT game window size differs, every
                                         # runtime match check rescales region/pixel_pos/search_region (and
                                         # resizes the saved template) from this reference size to the
                                         # current one instead -- see poe2bot/scaling.py.
    calib_left: Optional[int] = None    # image/pixel mode only: the game window's client area origin (in
    calib_top: Optional[int] = None     # absolute virtual-desktop pixels) at the same moment as calib_width/
                                         # calib_height. region/pixel_pos/search_region are themselves stored
                                         # in absolute screen pixels, so rescaling needs this to translate them
                                         # into (and back out of) the window-relative space poe2bot/scaling.py's
                                         # anchor model actually operates in -- without it, a window that isn't
                                         # sitting at the desktop's own (0, 0) (a secondary monitor, or windowed
                                         # mode) would rescale against the wrong reference frame. None (a
                                         # condition calibrated before this existed) is treated as (0, 0), i.e.
                                         # the client area's absolute position is assumed unchanged -- matching
                                         # that condition's pre-existing behavior.

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
            action=data.get("action", "fire"),
            template=data.get("template"),
            region=_int_tuple(data.get("region")),
            search_mode=data.get("search_mode", "exact"),
            search_region=_int_tuple(data.get("search_region")),
            pixel_pos=_int_tuple(data.get("pixel_pos")),
            pixel_color=_int_tuple(data.get("pixel_color")),
            confidence=_float_or(data, "confidence", 0.9),
            timer_seconds=_float_or(data, "timer_seconds", None),
            negate=bool(data.get("negate", False)),
            timeout_ms=_int_or(data, "timeout_ms", 0),
            hold_ms=_int_or(data, "hold_ms", None),
            delay_ms=_int_or(data, "delay_ms", None),
            calib_width=_int_or(data, "calib_width", None),
            calib_height=_int_or(data, "calib_height", None),
            calib_left=_int_or(data, "calib_left", None),
            calib_top=_int_or(data, "calib_top", None),
        )


@dataclass
class SkillConditionGroup:
    """An explicit, single-skill-scoped grouping of a Step's own plain
    Conditions -- created only via the GUI's "Add Skill Condition Group"
    action, never implicitly. NOT to be confused with the rotation-level
    ConditionGroup below (which gates a whole nested block of Steps with one
    single condition): this instead groups a subset of ONE Step's own
    `conditions` list, combining several child Conditions together.

    `action`/`timeout_ms`/`hold_ms`/`delay_ms` are chosen ONCE here, for the
    whole group -- exactly the same vocabulary/meaning as the matching
    fields on Condition, just applied to the group's own combined result
    rather than a single condition's match. A child Condition's own
    `action`/`timeout_ms`/`hold_ms`/`delay_ms` fields become unused once
    it's nested in a group -- only its match config (match_type, template/
    region/pixel_pos/pixel_color/search_mode/search_region, confidence,
    timer_seconds, negate, calib_*) still applies per child; `negate` in
    particular stays per-child.

    `match_logic` decides how the children combine: "all" (every child must
    currently match) or "any" (at least one must) -- see VALID_MATCH_LOGIC.
    An empty `conditions` list is a deliberate vacuous-truth edge case, not
    specially handled: see executor._group_matches.

    A Step's own `conditions` list can freely mix standalone Conditions
    (today's behavior, each with its own action) alongside these explicit
    groups. No nesting: a group's own `conditions` are always plain
    Conditions, never another SkillConditionGroup -- see iter_conditions/
    validate_rotation/executor._group_matches for how every consumer treats
    a group as exactly one flat pass over its children, never recursing."""
    name: str = ""              # optional display label, same convention as Condition.name/Step.name
    action: str = "fire"        # "fire"/"block"/"hold" -- same vocabulary as Condition.action, but decided
                                 # once for the GROUP's combined result, never per child
    match_logic: str = "all"    # "all" or "any" -- see VALID_MATCH_LOGIC
    conditions: List[Condition] = field(default_factory=list)   # flat, plain Conditions only -- never nested
    timeout_ms: int = 0         # "fire" only -- same semantics as Condition.timeout_ms, applied to the
                                 # group's own combined match
    hold_ms: Optional[int] = None    # "hold" only -- same semantics as Condition.hold_ms
    delay_ms: Optional[int] = None   # "hold" only -- same semantics as Condition.delay_ms

    @staticmethod
    def from_dict(data: dict) -> "SkillConditionGroup":
        return SkillConditionGroup(
            name=data.get("name", ""),
            action=data.get("action", "fire"),
            match_logic=data.get("match_logic", "all"),
            conditions=[Condition.from_dict(c) for c in data.get("conditions", [])],
            timeout_ms=_int_or(data, "timeout_ms", 0),
            hold_ms=_int_or(data, "hold_ms", None),
            delay_ms=_int_or(data, "delay_ms", None),
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
    conditions: List[Union[Condition, SkillConditionGroup]] = field(default_factory=list)   # everything that used
                                                                  # to be a separate Cooldown Check / Buff Check /
                                                                  # plain Condition now lives here uniformly -- see
                                                                  # Condition.action. May also hold explicit
                                                                  # SkillConditionGroups the user created (never
                                                                  # nested) -- see that class's own docstring.
    repeat_count: int = 1                    # fire this step this many times per pass; 1 = today's behavior
    repeat_combine_hold: bool = False        # if the key has a hold > 0, hold once for hold_ms * repeat_count and
                                              # delay once afterward, instead of repeat_count independent hold+delay cycles
    alt_key: Optional[str] = None            # whichever device's key ISN'T currently active -- same None/""/string
                                              # semantics as `key`. Swapped with `key` by App's Active Device toggle;
                                              # never edited directly through its own UI.
    enabled: bool = True    # False = always skipped at runtime (no fire, no delay, conditions never even
                             # checked) regardless of key/conditions -- distinct from key=None ("not yet
                             # assigned") and key="" (a deliberate sleep step, which still waits out its
                             # delay when enabled). Toggled via the Skill Steps section's Disable/Enable
                             # Step button, not a form field of its own -- see StepEditorMixin._read_step_form.

    @staticmethod
    def from_dict(data: dict) -> "Step":
        # A whitespace-only key (e.g. a hand-edited "key": "   ") is truthy, so it
        # would otherwise slip past both the sleep-step check (`not step.key`) and
        # validate_rotation's `step.key.strip()` guard as a third, unintended
        # pseudo-state -- normalizing it to "" here (a real, deliberate sleep step)
        # or leaving None alone means those two checks never need to know about it.
        raw_key = data.get("key")
        key = raw_key.strip() if isinstance(raw_key, str) else raw_key
        raw_alt_key = data.get("alt_key")
        alt_key = raw_alt_key.strip() if isinstance(raw_alt_key, str) else raw_alt_key
        return Step(
            key=key,
            name=data.get("name", ""),
            delay_ms=_int_or(data, "delay_ms", 100),
            jitter_ms=_int_or(data, "jitter_ms", 0),
            hold_ms=_int_or(data, "hold_ms", 0),
            hold_jitter_ms=_int_or(data, "hold_jitter_ms", 0),
            conditions=[_condition_entry_from_dict(c) for c in data.get("conditions", [])],
            repeat_count=_int_or(data, "repeat_count", 1),
            repeat_combine_hold=bool(data.get("repeat_combine_hold", False)),
            alt_key=alt_key,
            enabled=bool(data.get("enabled", True)),
        )

    def to_dict(self) -> dict:
        """Like asdict(self), except `conditions` -- a heterogeneous
        Condition | SkillConditionGroup list -- gets each entry properly
        "type"-tagged via _condition_entry_to_dict instead of asdict()'s own
        untagged recursion, which would otherwise make a SkillConditionGroup
        indistinguishable from a plain Condition dict on reload. See
        _step_entry_to_dict, which calls this instead of a bare
        asdict(step)."""
        data = asdict(self)
        data["conditions"] = [_condition_entry_to_dict(c) for c in self.conditions]
        return data


@dataclass
class ConditionGroup:
    """A rotation-level gate: `condition` (always match_type "image" or
    "pixel", action "fire" or "block" -- never "timer"/"hold", see
    validate_rotation) decides once per pass whether every entry in
    `entries` runs at all, exactly like a Step's own fire/block Conditions
    decide for one step -- just applied to a whole ordered block together.
    `entries` may itself hold nested ConditionGroups (up to
    MAX_GROUP_NESTING_DEPTH deep) -- see iter_steps/iter_conditions/
    validate_rotation for how every consumer recurses to arbitrary depth,
    and RotationRunner._run_entries for how a nested group's own gate
    combines (AND) with every ancestor group's gate above it."""
    condition: Condition = field(default_factory=Condition)
    entries: List[Union[Step, "ConditionGroup"]] = field(default_factory=list)

    @staticmethod
    def from_dict(data: dict) -> "ConditionGroup":
        # "entries" is the current key; a rotation saved before nesting existed
        # used a flat "steps" key instead (plain Step dicts, none of them tagged
        # with a "type" key) -- keep reading that too so it still loads with no
        # migration step, same idea _step_entry_from_dict already relies on for
        # a rotation saved before ConditionGroup existed at all.
        raw_entries = data.get("entries")
        if raw_entries is None:
            raw_entries = data.get("steps", [])
        return ConditionGroup(
            condition=Condition.from_dict(data.get("condition") or {}),
            entries=[_step_entry_from_dict(e) for e in raw_entries])


def _step_entry_to_dict(entry) -> dict:
    """Rotation.steps (and a ConditionGroup's own `entries`) is a
    heterogeneous Step | ConditionGroup list -- this (and
    _step_entry_from_dict below) is where that gets an explicit "type"
    discriminator in the persisted JSON, so a pre-existing rotation file
    (saved before ConditionGroup existed, with no "type" key at all) still
    loads every entry as a plain Step rather than needing a migration.
    Recurses for a ConditionGroup's own `entries` (rather than a flat
    asdict(s) per child) so a nested ConditionGroup gets its own "type"
    tag too, at any depth."""
    if isinstance(entry, ConditionGroup):
        return {"type": "group", "condition": asdict(entry.condition),
                "entries": [_step_entry_to_dict(e) for e in entry.entries]}
    return {"type": "step", **entry.to_dict()}


def _step_entry_from_dict(data: dict):
    return ConditionGroup.from_dict(data) if data.get("type") == "group" else Step.from_dict(data)


def _condition_entry_to_dict(entry) -> dict:
    """Step.conditions (and, before SkillConditionGroup existed, always just
    a flat Condition list) is now a heterogeneous Condition |
    SkillConditionGroup list -- this (and _condition_entry_from_dict below)
    gives each entry an explicit "type" discriminator in the persisted
    JSON, the same idea _step_entry_to_dict/_step_entry_from_dict already
    use for Step vs ConditionGroup. A condition dict with no "type" key at
    all -- every rotation saved before SkillConditionGroup existed -- still
    loads as a plain Condition (see _condition_entry_from_dict), so there's
    no migration step. Unlike _step_entry_to_dict, this never recurses: a
    SkillConditionGroup's own `conditions` are always plain Conditions,
    never another group (see SkillConditionGroup's own docstring), so a
    single asdict() correctly covers a group's children as-is. The "type"
    tag here ("condition_group") is deliberately distinct from
    _step_entry_to_dict's own "group" tag -- the two live in different JSON
    contexts (Step.conditions vs Rotation.steps/ConditionGroup.entries) but
    using different strings avoids any risk of confusing the two concepts
    when reading a raw rotation file."""
    if isinstance(entry, SkillConditionGroup):
        return {"type": "condition_group", **asdict(entry)}
    return {"type": "condition", **asdict(entry)}


def _condition_entry_from_dict(data: dict):
    return SkillConditionGroup.from_dict(data) if data.get("type") == "condition_group" else Condition.from_dict(data)


def iter_steps(entries):
    """Every Step in `entries` (a Rotation.steps-shaped list), including
    ones nested inside a ConditionGroup at any depth -- not the group's own
    condition itself, see iter_conditions for that. Used anywhere something
    needs to see every leaf step regardless of grouping/nesting (e.g. the
    Active Device key/alt_key swap, or a Loop rotation's "at least one step
    with a key" check)."""
    for entry in entries:
        if isinstance(entry, ConditionGroup):
            yield from iter_steps(entry.entries)
        else:
            yield entry


def iter_conditions(entries):
    """Every Condition anywhere in `entries`: each step's own standalone
    conditions, plus each child Condition inside one of a step's own
    SkillConditionGroups (never the group object itself -- unlike a
    rotation-level ConditionGroup's single `condition`, there's no one "the
    group's condition" to yield instead), plus each ConditionGroup's own
    single gating condition (at any nesting depth). Used by the
    calibrated-template GC sweep, which must not delete a template still
    referenced by a group's own condition or by a SkillConditionGroup's
    child."""
    for entry in entries:
        if isinstance(entry, ConditionGroup):
            yield entry.condition
            yield from iter_conditions(entry.entries)
        else:
            for item in entry.conditions:
                if isinstance(item, SkillConditionGroup):
                    yield from item.conditions
                else:
                    yield item


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
    enabled: bool = True   # False = this rotation's hotkeys (trigger/cancel/reset/pause) are never
                            # bound, freeing them up for another rotation to use, the same as being
                            # outside the Active Folder's scope -- but toggled per-rotation regardless
                            # of folder. Doesn't affect the saved rotation itself in any other way.
    hotkey: Optional[str] = None
    alt_hotkey: Optional[str] = None       # the OTHER device's hotkey -- swapped with `hotkey` by the Active
                                            # Device toggle; never edited directly through its own UI
    cancel_key: Optional[str] = None   # e.g. the dodge key -- immediately stops this rotation if running
    alt_cancel_key: Optional[str] = None
    reset_key: Optional[str] = None    # immediately restarts this rotation from its first step if running
    alt_reset_key: Optional[str] = None
    reset_delay_ms: int = 0            # wait this long after a reset before actually firing step 1 again (0 = instant)
    pause_key: Optional[str] = None    # immediately freezes this rotation in place if running (see pause_mode)
    alt_pause_key: Optional[str] = None
    pause_mode: str = "duration"        # "duration" = auto-resume after pause_duration_ms; "toggle" = press again to resume
    pause_duration_ms: int = 1000       # only used when pause_mode == "duration"
    folder: str = ""   # "/"-separated group path (e.g. "Bosses/HardMode"); "" = ungrouped. NOT persisted
                        # in the JSON -- it's derived from where the file actually lives on disk each time
                        # it's loaded (see storage.py), so there's no way for it to drift out of sync.
    steps: List[Union[Step, ConditionGroup]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "enabled": self.enabled,
            "hotkey": self.hotkey,
            "alt_hotkey": self.alt_hotkey,
            "cancel_key": self.cancel_key,
            "alt_cancel_key": self.alt_cancel_key,
            "reset_key": self.reset_key,
            "alt_reset_key": self.alt_reset_key,
            "reset_delay_ms": self.reset_delay_ms,
            "pause_key": self.pause_key,
            "alt_pause_key": self.alt_pause_key,
            "pause_mode": self.pause_mode,
            "pause_duration_ms": self.pause_duration_ms,
            "steps": [_step_entry_to_dict(entry) for entry in self.steps],
        }

    @staticmethod
    def from_dict(data: dict) -> "Rotation":
        return Rotation(
            name=data["name"],
            mode=data.get("mode", "once"),
            enabled=bool(data.get("enabled", True)),
            hotkey=data.get("hotkey"),
            alt_hotkey=data.get("alt_hotkey"),
            cancel_key=data.get("cancel_key"),
            alt_cancel_key=data.get("alt_cancel_key"),
            reset_key=data.get("reset_key"),
            alt_reset_key=data.get("alt_reset_key"),
            reset_delay_ms=_int_or(data, "reset_delay_ms", 0),
            pause_key=data.get("pause_key"),
            alt_pause_key=data.get("alt_pause_key"),
            pause_mode=data.get("pause_mode", "duration"),
            pause_duration_ms=_int_or(data, "pause_duration_ms", 1000),
            steps=[_step_entry_from_dict(entry) for entry in data.get("steps", [])],
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


def folder_in_scope(folder: str, active_folder: Optional[str]) -> bool:
    """True if `folder` is `active_folder` itself, a subfolder of it, or
    `active_folder` is None (no restriction -- every folder is in scope).
    Shared by App's Active Folder feature and RotationListMixin's
    _rename_folder (which folders are affected by a rename), so both treat
    folder-hierarchy containment the same way. The trailing "/" in the
    startswith check is what stops "WarriorX" from false-matching an
    active_folder of "Warrior"."""
    if active_folder is None:
        return True
    return folder == active_folder or folder.startswith(active_folder + "/")


def _search_area_problems(label: str, subject: str, search_mode: str, search_region, region) -> List[str]:
    """Shared by every image-mode Condition validation below -- all calibrate
    search_mode/search_region the same way, so this avoids repeating the same
    checks per condition. `subject` is an optional noun phrase inserted right
    after `label`, matching how the surrounding messages are worded."""
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

    if not any(True for _ in iter_steps(rotation.steps)):
        problems.append("Rotation must have at least one step.")
    elif rotation.mode == "loop" and all(
            step.key is None or not step.enabled for step in iter_steps(rotation.steps)):
        # A step with key=None (unassigned), or a disabled step regardless of its
        # key, has no wait of any kind at runtime -- it's just skipped instantly,
        # unlike a real key or a deliberate "" sleep step. If *every* step in a
        # Loop rotation is like that, the runner would spin one full pass after
        # another with no delay anywhere, pegging a CPU core forever -- catch it
        # here rather than letting it reach the runtime.
        problems.append(
            "A Loop rotation needs at least one step with a key assigned (or a Sleep step) -- "
            "otherwise it would repeat with no delay between passes.")

    _validate_entries(rotation.steps, "", 0, problems)

    return problems


def _validate_entries(entries: List[Union[Step, ConditionGroup]], group_path_label: str,
                       depth: int, problems: List[str]) -> None:
    """Validates one Step | ConditionGroup list -- rotation.steps itself
    (group_path_label="", depth=0) or one ConditionGroup's own `entries`
    (group_path_label=that group's own label, depth=its nesting level) --
    recursing into any nested ConditionGroup and extending `problems` in
    place. group_path_label carries the " > "-joined ancestor-group chain
    (e.g. "Rotation Condition 2 > Rotation Condition 1" -- a ConditionGroup
    is called a "Rotation Condition" in the UI, to distinguish it from a
    SkillConditionGroup); it's "" only at the true top level, which is what
    makes this degrade to the exact "Rotation Condition N"/"Rotation
    Condition N, Step M" labels a single level of nesting always used."""
    for i, entry in enumerate(entries, start=1):
        if isinstance(entry, ConditionGroup):
            this_group_label = (f"{group_path_label} > Rotation Condition {i}"
                                 if group_path_label else f"Rotation Condition {i}")
            if depth >= MAX_GROUP_NESTING_DEPTH:
                problems.append(f"{this_group_label}: rotation conditions cannot nest more than "
                                 f"{MAX_GROUP_NESTING_DEPTH} levels deep.")
                continue  # don't descend further -- avoid a cascade of redundant errors
            problems.extend(_condition_problems(
                this_group_label, entry.condition, ("fire", "block"), allow_timer=False))
            _validate_entries(entry.entries, this_group_label, depth + 1, problems)
        else:
            label = f"{group_path_label}, Step {i}" if group_path_label else f"Step {i}"
            problems.extend(_step_problems(label, entry))


def _step_problems(label: str, step: Step) -> List[str]:
    """Every validation problem with one Step, addressed by `label` (e.g.
    "Step 3" for a top-level step, or "Rotation Condition 2, Step 1" for one
    nested in a group) -- shared by validate_rotation's top-level steps and
    each ConditionGroup's own nested steps."""
    problems = []
    # key is None -- no keybind assigned yet -- means this step is skipped
    # entirely at runtime (not an error; the GUI's Add Step defaults to this).
    # key == "" means this step is a deliberate sleep/pause: no key to press,
    # it just waits out delay_ms (+/- jitter_ms) like any other step's
    # post-fire wait. Both are falsy, so this check is skipped for either.
    # alt_key gets the identical check -- it's just the other device's key,
    # same None/""/string semantics, swapped in by the Active Device toggle.
    for key, key_label in ((step.key, label), (step.alt_key, f"{label} (alt key)")):
        if key and key.strip():
            key_kind = hotkeys.classify(key)
            if key_kind == "controller":
                if controller.controller_button_of(key) not in controller.VALID_BUTTON_NAMES:
                    problems.append(f"{key_label}: '{key}' is not a recognized controller button.")
            elif key_kind == "mouse":
                if hotkeys.mouse_button_of(key) not in hotkeys.MOUSE_DISPLAY_NAMES:
                    problems.append(f"{key_label}: '{key}' is not a recognized mouse button.")
            else:
                try:
                    keyboard.key_to_scan_codes(key)
                except ValueError:
                    problems.append(f"{key_label}: '{key}' is not a recognized key name.")

    if step.delay_ms < 0:
        problems.append(f"{label}: delay_ms cannot be negative.")
    if step.jitter_ms < 0:
        problems.append(f"{label}: jitter_ms cannot be negative.")
    if step.hold_ms < 0:
        problems.append(f"{label}: hold_ms cannot be negative.")
    if step.hold_jitter_ms < 0:
        problems.append(f"{label}: hold_jitter_ms cannot be negative.")

    for j, entry in enumerate(step.conditions, start=1):
        entry_label = f"{label}, condition {j}"
        if isinstance(entry, SkillConditionGroup):
            problems.extend(_condition_group_problems(entry_label, entry))
        else:
            problems.extend(_condition_problems(entry_label, entry, VALID_CONDITION_ACTIONS, allow_timer=True))

    if step.repeat_count < 1:
        problems.append(f"{label}: repeat count must be at least 1.")
    elif step.repeat_count > MAX_REPEAT_COUNT:
        problems.append(f"{label}: repeat count of {step.repeat_count} is over the sanity limit of "
                         f"{MAX_REPEAT_COUNT} -- with Combine Hold this would hold a key down for a "
                         f"very long time. Lower it, or split this into a Loop rotation instead.")
    return problems


def _condition_match_problems(label: str, condition: Condition, allow_timer: bool) -> List[str]:
    """Every validation problem with one Condition's own match configuration
    (match_type + whatever match_type-specific fields it implies, plus
    confidence) -- everything about a Condition EXCEPT whether its `action`
    value is allowed where it lives, and action's own downstream fields
    (timeout_ms / hold_ms / delay_ms), which mean different things (or
    nothing at all) depending on context. See _condition_problems for a
    standalone Condition or a rotation-level ConditionGroup's own gating
    condition (both of which care about `action`), and
    _condition_group_problems for a child Condition nested in a
    SkillConditionGroup (whose OWN action/timeout_ms/hold_ms/delay_ms fields
    are unused -- the group's own apply instead, uniformly, to the whole
    group)."""
    problems = []
    if not condition.has_check():
        # Not yet calibrated -- treated as "this condition is off," not an
        # error. Only reachable via hand-edited JSON; the GUI's own Add
        # Condition flow always produces a fully-calibrated one.
        return problems
    valid_match_types = VALID_CONDITION_MATCH_TYPES if allow_timer else ("image", "pixel")
    if condition.match_type not in valid_match_types:
        problems.append(f"{label}: match_type must be one of {valid_match_types}.")
    if condition.match_type != "timer" and not (0 < condition.confidence <= 1):
        # Confidence is a visual-match fuzziness threshold -- meaningless for a
        # pure time gate, which has no "how close is close enough" to tune.
        problems.append(f"{label}: confidence must be greater than 0 and at most 1.")
    if condition.match_type == "timer":
        pass  # has_check() above already proved timer_seconds is a positive number
    elif condition.match_type == "pixel":
        if not (isinstance(condition.pixel_pos, tuple) and len(condition.pixel_pos) == 2):
            problems.append(f"{label}: has no calibrated point (recalibrate).")
        if not (isinstance(condition.pixel_color, tuple) and len(condition.pixel_color) == 3):
            problems.append(f"{label}: has no calibrated color (recalibrate).")
    else:
        if not (isinstance(condition.region, tuple) and len(condition.region) == 4):
            problems.append(f"{label}: has no calibrated region (recalibrate).")
        if not condition.template or not os.path.isfile(templates.template_path(condition.template)):
            problems.append(f"{label}: calibrated template image is missing on disk (recalibrate).")
        problems.extend(_search_area_problems(
            label, "", condition.search_mode, condition.search_region, condition.region))
    return problems


def _condition_problems(label: str, condition: Condition, allowed_actions, allow_timer: bool) -> List[str]:
    """Every validation problem with one standalone Condition (or a
    rotation-level ConditionGroup's own single gating condition), addressed
    by `label`. Shared by a Step's own ungrouped conditions (`allowed_actions`
    = every action, `allow_timer=True`) and a ConditionGroup's single gating
    condition (`allowed_actions=("fire", "block")`, `allow_timer=False` -- a
    group never uses "hold" or a pure time gate, see ConditionGroup). NOT
    used for a Condition nested inside a SkillConditionGroup -- see
    _condition_group_problems for that, which validates a child's own match
    config the same way (via _condition_match_problems) but never its own
    `action`/timeout_ms/hold_ms/delay_ms, which are unused there."""
    problems = _condition_match_problems(label, condition, allow_timer)
    if not condition.has_check():
        return problems
    if condition.action not in allowed_actions:
        problems.append(f"{label}: action must be one of {allowed_actions}.")
    if condition.action == "fire":
        if condition.timeout_ms < 0:
            problems.append(f"{label}: wait timeout cannot be negative.")
    elif condition.action == "hold":
        if condition.hold_ms is not None and condition.hold_ms < 0:
            problems.append(f"{label}: hold override cannot be negative.")
        if condition.delay_ms is not None and condition.delay_ms < 0:
            problems.append(f"{label}: delay override cannot be negative.")
        if condition.hold_ms is None and condition.delay_ms is None:
            problems.append(
                f"{label}: action is 'Override Hold Time' but neither Hold nor "
                f"Delay override is set, so it would never have any effect.")
    return problems


def _condition_group_problems(label: str, group: SkillConditionGroup) -> List[str]:
    """Every validation problem with one SkillConditionGroup, addressed by
    `label` (e.g. "Step 3, condition 2"). Checks the GROUP's own action/
    match_logic/timeout_ms/hold_ms/delay_ms -- mirroring _condition_problems'
    own action-specific checks (fire: timeout_ms >= 0; hold: at least one of
    hold_ms/delay_ms set), just applied to the group's fields instead of a
    single Condition's. Each child Condition is validated for its own match
    configuration only (via _condition_match_problems, allow_timer=True -- a
    group's children may use a timer condition same as any standalone one)
    -- never a child's own `action`/timeout_ms/hold_ms/delay_ms, which are
    unused once nested (the group's own apply instead, uniformly, to the
    whole group's combined result)."""
    problems = []
    if group.action not in VALID_CONDITION_ACTIONS:
        problems.append(f"{label}: action must be one of {VALID_CONDITION_ACTIONS}.")
    if group.match_logic not in VALID_MATCH_LOGIC:
        problems.append(f"{label}: match logic must be one of {VALID_MATCH_LOGIC}.")
    if group.action == "fire":
        if group.timeout_ms < 0:
            problems.append(f"{label}: wait timeout cannot be negative.")
    elif group.action == "hold":
        if group.hold_ms is not None and group.hold_ms < 0:
            problems.append(f"{label}: hold override cannot be negative.")
        if group.delay_ms is not None and group.delay_ms < 0:
            problems.append(f"{label}: delay override cannot be negative.")
        if group.hold_ms is None and group.delay_ms is None:
            problems.append(
                f"{label}: action is 'Override Hold Time' but neither Hold nor "
                f"Delay override is set, so it would never have any effect.")
    for k, child in enumerate(group.conditions, start=1):
        problems.extend(_condition_match_problems(f"{label}, child {k}", child, allow_timer=True))
    return problems
