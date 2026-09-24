"""Everything about deciding whether a Condition/SkillConditionGroup/
ConditionGroup currently matches: template loading/caching, screen capture,
rescaling a calibrated point/region to the game window's current size/
position, and the actual image/pixel/timer comparisons -- split out of
executor.py (which now holds just the RotationRunner/RotationManager side
of things) purely for navigability, since this half has no dependency on
threading/rotation-execution state at all, only on a Condition/step's own
fields."""
import math
import threading
import time
from typing import Optional, Union

import cv2
import mss
import numpy as np
from PIL import Image, ImageChops, ImageStat

from poe2bot import templates
from poe2bot.focus import game_window_client_rect
from poe2bot.log_setup import get_logger
from poe2bot.models import Condition, ConditionGroup, SkillConditionGroup, Step
from poe2bot.scaling import scale_point, scale_region

log = get_logger()

_MAX_COLOR_DISTANCE = math.sqrt(3 * 255 ** 2)  # largest possible Euclidean distance between two RGB colors

_template_image_cache = {}   # filename -> grayscale PIL.Image.Image, decoded once and reused
_template_array_cache = {}   # filename -> grayscale np.ndarray, decoded once -- area search only
_resized_template_image_cache = {}  # (filename, target_size) -> grayscale PIL.Image.Image, resized once
_resized_template_array_cache = {}  # (filename, target_size) -> grayscale np.ndarray, resized once
_thread_local = threading.local()

_cached_game_window_rect = None
_cached_game_window_rect_at = 0.0
_GAME_WINDOW_SIZE_CACHE_S = 1.0  # avoid a Win32 EnumWindows call on every single poll of a tight loop


def _load_template_image(filename: str) -> Image.Image:
    """Load and cache a calibration template as grayscale, so repeated ready-checks
    never re-read/re-decode the PNG from disk or re-convert it to grayscale."""
    image = _template_image_cache.get(filename)
    if image is None:
        image = Image.open(templates.template_path(filename)).convert("L")
        image.load()  # force-read pixel data now, so later use never touches the file again
        _template_image_cache[filename] = image
    return image


def _load_template_array(filename: str) -> np.ndarray:
    """Same caching as _load_template_image, but as a numpy array for
    cv2.matchTemplate -- used only by area-search matching, kept separate so
    the far more common exact-mode path never pays for a numpy conversion."""
    array = _template_array_cache.get(filename)
    if array is None:
        array = np.array(_load_template_image(filename))
        _template_array_cache[filename] = array
    return array


def _load_template_image_resized(filename: str, target_size: tuple) -> Image.Image:
    """_load_template_image(filename), resized to target_size if that
    differs from the template's native size -- used when a condition's
    calibrated region has been rescaled to a different screen (see
    poe2bot/scaling.py) than the template was originally captured at, so
    _image_matches_exact still compares two same-size images. Cached per
    (filename, target_size) pair for the same reason _load_template_image
    itself is cached -- resizing isn't free, and repeated polls at a
    stable (unchanged) screen size need not pay for it more than once."""
    base = _load_template_image(filename)
    if base.size == target_size:
        return base
    key = (filename, target_size)
    resized = _resized_template_image_cache.get(key)
    if resized is None:
        resized = base.resize(target_size, Image.LANCZOS)
        _resized_template_image_cache[key] = resized
    return resized


def _load_template_array_resized(filename: str, target_size: tuple) -> np.ndarray:
    """Same idea as _load_template_image_resized, but as a numpy array for
    cv2.matchTemplate -- used only by area-search matching."""
    base_image = _load_template_image(filename)
    if base_image.size == target_size:
        return _load_template_array(filename)
    key = (filename, target_size)
    array = _resized_template_array_cache.get(key)
    if array is None:
        array = np.array(base_image.resize(target_size, Image.LANCZOS))
        _resized_template_array_cache[key] = array
    return array


def _current_game_window_rect():
    """(left, top, width, height) of the configured game process's window
    client area, cached for _GAME_WINDOW_SIZE_CACHE_S at a time -- match
    checks that need this (see _rescaled_point/_rescaled_region) run far
    more often than a window/monitor change could plausibly happen, so
    this caps the cost of asking Windows to at most once a second instead
    of once per poll."""
    global _cached_game_window_rect, _cached_game_window_rect_at
    now = time.perf_counter()
    if now - _cached_game_window_rect_at >= _GAME_WINDOW_SIZE_CACHE_S:
        _cached_game_window_rect = game_window_client_rect()
        _cached_game_window_rect_at = now
    return _cached_game_window_rect


def _invalidate_game_window_size_cache():
    """Forces the next _current_game_window_rect() call to do a fresh
    lookup instead of trusting the cache -- called when the game regains
    OS focus (see RotationRunner._wait_for_focus_or_stop), since that's
    exactly when a resize/move that happened while the game was unfocused
    (e.g. the user alt-tabbed away, resized or dragged it, and came back)
    would otherwise take up to _GAME_WINDOW_SIZE_CACHE_S to be picked up."""
    global _cached_game_window_rect_at
    _cached_game_window_rect_at = 0.0


def _rescaled_point(pixel_pos, calib_width: Optional[int], calib_height: Optional[int],
                     calib_left: Optional[int] = None, calib_top: Optional[int] = None):
    """pixel_pos rescaled from a (calib_width, calib_height) reference
    screen to the game window's CURRENT client size, if there's actually a
    reference recorded and a current size to rescale it to (see
    poe2bot/scaling.py) -- otherwise pixel_pos unchanged, which also covers
    the by-far-most-common case where the screen hasn't changed at all.

    pixel_pos is stored in absolute virtual-desktop pixels (see
    overlays.py), but poe2bot/scaling.py's anchor model only makes sense
    relative to the client area it was calibrated against -- so this
    subtracts off the calibration-time client origin (calib_left/
    calib_top) before rescaling, then adds back the CURRENT client
    origin afterward. calib_left/calib_top default to 0 for a condition
    calibrated before these were recorded, matching that condition's
    pre-existing behavior of implicitly assuming the client area started
    at the desktop's own (0, 0)."""
    if pixel_pos is None or calib_width is None or calib_height is None:
        return pixel_pos
    current = _current_game_window_rect()
    if current is None:
        return pixel_pos
    current_left, current_top, current_width, current_height = current
    rel_x = pixel_pos[0] - (calib_left or 0)
    rel_y = pixel_pos[1] - (calib_top or 0)
    new_x, new_y = scale_point(rel_x, rel_y, calib_width, calib_height, current_width, current_height)
    return new_x + current_left, new_y + current_top


def _rescaled_region(region, calib_width: Optional[int], calib_height: Optional[int],
                      calib_left: Optional[int] = None, calib_top: Optional[int] = None):
    """Same idea as _rescaled_point, for a (left, top, width, height)
    region."""
    if region is None or calib_width is None or calib_height is None:
        return region
    current = _current_game_window_rect()
    if current is None:
        return region
    current_left, current_top, current_width, current_height = current
    left, top, width, height = region
    rel_region = (left - (calib_left or 0), top - (calib_top or 0), width, height)
    new_left, new_top, new_width, new_height = scale_region(
        rel_region, calib_width, calib_height, current_width, current_height)
    return new_left + current_left, new_top + current_top, new_width, new_height


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


def capture_region(region) -> Image.Image:
    """Screenshot exactly `region` (left, top, width, height) -- and only that
    region -- in absolute virtual-desktop pixels. pyautogui/Pillow's own
    screen grab always captures the *entire* screen on Windows and crops
    afterward regardless of region size; mss captures just the requested
    rectangle directly via BitBlt (X11's XGetImage on Linux -- no shelling
    out to an external screenshot tool the way Pillow's own Linux
    ImageGrab backend does), which is what actually matters for a check
    that runs in a tight polling loop. No virtual-desktop-origin
    correction needed here (contrast poe2bot/gui/calibration.py's old
    pyautogui-based capture, before it switched to this same function) --
    mss's own monitor dict already takes absolute coordinates directly,
    multi-monitor included, with no separate "capture every screen" mode
    to opt into. Public (no leading underscore): also used directly by
    poe2bot/gui/calibration.py's calibration screenshots, not just this
    module's own runtime match-checking."""
    left, top, width, height = region
    monitor = {"left": left, "top": top, "width": width, "height": height}
    shot = _screen_capture().grab(monitor)
    return Image.frombytes("RGB", shot.size, shot.rgb)


def _check_condition(condition: Condition, label: str, seconds_since_fired: Optional[float] = None) -> bool:
    """True if `condition` currently matches -- for "image"/"pixel" that means
    on screen; for "timer" it means at least `condition.timer_seconds` have
    passed since the owning step's own last fire -- `seconds_since_fired` is
    computed by the caller (RotationRunner, from its own _step_last_fired
    tracking), since this function stays a plain stateless dispatcher.

    `condition.negate` inverts the result uniformly, applied last, regardless
    of match_type or action or why the underlying check came out the way it
    did (e.g. "block while this debuff icon is NOT present" is an image
    condition with action="block", negate=True; an unconfigured negated
    condition still ends up matching every time, since "unconfigured" itself
    already reads as "doesn't match"). What a match *does* -- gate firing,
    veto firing, or override hold/delay -- is entirely up to the caller,
    based on condition.action (see _fire_gate_passes / _hold_override below);
    this function only ever answers "does it match right now," never "what
    should happen.\""""
    if condition.match_type == "timer":
        if condition.timer_seconds is None or condition.timer_seconds <= 0:
            matched = False  # unconfigured -- treated as "doesn't match", same as a missing template/pixel below
        elif seconds_since_fired is None:
            matched = True  # never fired yet this run -- nothing to wait out, available immediately
        else:
            matched = seconds_since_fired >= condition.timer_seconds
    elif condition.match_type == "pixel":
        matched = _pixel_matches(condition.pixel_pos, condition.pixel_color, condition.confidence, label,
                                  condition.calib_width, condition.calib_height,
                                  condition.calib_left, condition.calib_top)
    else:
        matched = _image_matches(condition.template, condition.region, condition.confidence, label,
                                  condition.search_mode, condition.search_region,
                                  condition.calib_width, condition.calib_height,
                                  condition.calib_left, condition.calib_top)
    return (not matched) if condition.negate else matched


def check_condition_now(condition: Condition) -> bool:
    """Public entry point for a live "does this match right now" check
    against a single Condition, with no owning step/RotationRunner context
    needed -- used by the GUI's "Test Match" button (see
    poe2bot/gui/calibration.py's CalibrationMixin._show_test_match_result)
    to preview a pixel/image condition's current match without running the
    whole rotation. Meaningless for match_type == "timer" -- there's no
    "seconds since this step's last fire" to measure outside a running
    rotation, so callers should check for that themselves and skip calling
    this at all rather than getting a misleading always-"available" True."""
    return _check_condition(condition, "test match", seconds_since_fired=None)


def check_group_now(group: SkillConditionGroup) -> bool:
    """Public entry point for a live "does this whole group currently match"
    check, no owning Step/RotationRunner context needed -- the group-level
    counterpart to check_condition_now(), for the GUI's own Test Match
    preview on a SkillConditionGroup as a whole. Same timer-mode caveat as
    check_condition_now applies to any "timer" child (see its docstring)."""
    return _group_matches(group, "test match", seconds_since_fired=None)


def rescaled_pixel_pos(condition: Condition):
    """Public accessor for _rescaled_point, using `condition`'s own
    calib_width/calib_height/calib_left/calib_top -- for the GUI's Test
    Match preview, so the live swatch it screenshots is the same point the
    match check itself actually used, even when a resolution/aspect-ratio
    change (or the window simply having moved) means that's no longer
    condition.pixel_pos verbatim (see poe2bot/scaling.py)."""
    return _rescaled_point(condition.pixel_pos, condition.calib_width, condition.calib_height,
                            condition.calib_left, condition.calib_top)


def calibration_scale_note(condition: Condition) -> Optional[str]:
    """A short human-readable note describing whether `condition`'s
    region/pixel_pos is currently being rescaled from its calibration-time
    screen size to a different one right now, or None if it isn't --
    either because no reference size was recorded (a condition calibrated
    before this existed), the game window can't be found, or the current
    client rect matches the calibration-time one exactly. Used by the
    GUI's Test Match preview so a match/no-match result after moving to a
    new screen is visibly explained rather than looking identical to an
    ordinary check.

    Compares the full (left, top, width, height) rect, not just size --
    a window that moved without resizing (e.g. dragged to another
    monitor) still needs its calibrated point/region shifted to follow
    it, so that case should be called out here too, not just an actual
    resolution/aspect-ratio change."""
    if condition.calib_width is None or condition.calib_height is None:
        return None
    current = _current_game_window_rect()
    if current is None:
        return None
    current_left, current_top, current_width, current_height = current
    calib_left, calib_top = condition.calib_left or 0, condition.calib_top or 0
    size_changed = (current_width, current_height) != (condition.calib_width, condition.calib_height)
    moved = (current_left, current_top) != (calib_left, calib_top)
    if not size_changed and not moved:
        return None
    if size_changed:
        note = f"Rescaled from {condition.calib_width}x{condition.calib_height} to {current_width}x{current_height}"
        return note + " (window also moved)" if moved else note
    return f"Repositioned: game window moved from ({calib_left}, {calib_top}) to ({current_left}, {current_top})"


def _group_matches(group: SkillConditionGroup, label: str, seconds_since_fired: Optional[float]) -> bool:
    """True if `group`'s combined result currently holds: every child
    Condition must currently match if group.match_logic == "all", or at
    least one must if "any" -- each child's own match (subject to its own
    `negate`) computed exactly like a standalone Condition's, via
    _check_condition; a child's own `action`/timeout_ms/hold_ms/delay_ms are
    never consulted here (see SkillConditionGroup's own docstring). `label`
    is passed straight through to _check_condition for logging, same as a
    plain Condition's own label -- callers pass step.key or "step" (see
    _fire_gate_passes/_hold_override), or "test match" for check_group_now's
    standalone preview.

    An empty group.conditions is a deliberate vacuous-truth edge case, NOT
    handled specially: all() of an empty iterable is True (an empty "all"
    group always matches), any() of an empty iterable is False (an empty
    "any" group never does) -- exactly Python's own built-in behavior, left
    as-is intentionally rather than special-cased."""
    results = (_check_condition(c, label, seconds_since_fired) for c in group.conditions)
    return all(results) if group.match_logic == "all" else any(results)


def _fire_gate_passes(step: Step, seconds_since_fired: Optional[float]) -> bool:
    """True if step is currently allowed to fire: every "fire" entry in
    step.conditions currently matches AND no "block" entry does -- the
    AND+veto combination every step's conditions use together, generalized
    from "condition" to "entry": an entry is now either a plain Condition
    (matched via _check_condition directly) or a SkillConditionGroup
    (matched via _group_matches, using the GROUP's own action -- a child
    Condition's own action field is unused once nested in a group). "hold"
    entries (plain or group) never affect this (see _hold_override).
    Short-circuits on the first entry that already decides the answer, so a
    later expensive check -- including every remaining child of a
    still-unevaluated group -- is skipped once the outcome is known."""
    for entry in step.conditions:
        if isinstance(entry, SkillConditionGroup):
            if entry.action not in ("fire", "block"):
                continue  # "hold" (or bad data) has no effect here
            matched = _group_matches(entry, step.key or "step", seconds_since_fired)
            if entry.action == "block" and matched:
                return False
            if entry.action == "fire" and not matched:
                return False
        elif entry.action == "block":
            if _check_condition(entry, step.key or "step", seconds_since_fired):
                return False
        elif entry.action == "fire":
            if not _check_condition(entry, step.key or "step", seconds_since_fired):
                return False
    return True


def _group_gate_passes(group: ConditionGroup) -> bool:
    """Mirrors _fire_gate_passes but for a ConditionGroup's own single
    condition: "fire" requires it to currently match for the group's nested
    steps to run this pass, "block" vetoes running while it matches. Always
    an instant, one-shot check -- unlike a step's own Fire condition, a
    group's condition never polls/waits (timeout_ms is meaningless here;
    validate_rotation never lets a group's condition use "hold" or
    "timer")."""
    matched = _check_condition(group.condition, "condition group")
    return (not matched) if group.condition.action == "block" else matched


def _max_fire_timeout_ms(step: Step) -> int:
    """The longest timeout_ms configured on any of step's "fire" entries (a
    plain Condition or a SkillConditionGroup -- both expose timeout_ms the
    same way, so no isinstance check is needed here; 0 if none have one) --
    this is what RotationRunner._wait_for_fire_gate polls up to before
    giving up on a pass; a plain instant check (today's ordinary Condition
    behavior) is exactly the timeout_ms == 0 case."""
    return max((entry.timeout_ms for entry in step.conditions
                if entry.action == "fire" and entry.timeout_ms > 0), default=0)


def _hold_override(step: Step, seconds_since_fired: Optional[float]) -> Optional[Union[Condition, SkillConditionGroup]]:
    """The first currently-matching "hold" entry (a plain Condition or a
    SkillConditionGroup) on `step`, or None if none match (or none are
    configured) -- its hold_ms/delay_ms (whichever is set) replaces the
    step's own for this fire, in _fire_step/_sleep_delay (see
    _condition_override). First-in-list wins if more than one matches at
    once, exactly as before, now across a mixed list -- a group counts as
    "matching" when its OWN combined match_logic result over its children
    currently holds (via _group_matches); a group's own hold_ms/delay_ms
    then apply uniformly, never a child's."""
    for entry in step.conditions:
        if entry.action != "hold":
            continue
        matched = (_group_matches(entry, step.key or "step", seconds_since_fired)
                   if isinstance(entry, SkillConditionGroup)
                   else _check_condition(entry, step.key or "step", seconds_since_fired))
        if matched:
            return entry
    return None


def _condition_override(hold_condition: Optional[Union[Condition, SkillConditionGroup]], attr: str) -> Optional[int]:
    """getattr(hold_condition, attr) ("hold_ms" or "delay_ms") if a matching
    "hold" entry (a plain Condition or a SkillConditionGroup) was found,
    else None -- a value of None either way (no hold_condition, or its
    override field itself unset) means "no override, use the step's own
    value." Shared by _fire_step/_sleep_delay/_fire_repeats, which each need
    this same lookup. SkillConditionGroup exposes hold_ms/delay_ms as plain
    fields of its own, identically named to Condition's, so this generic
    getattr() needs no isinstance branching at all."""
    return getattr(hold_condition, attr) if hold_condition is not None else None


def _image_matches(template_filename, region, confidence: float, label: str,
                    search_mode: str = "exact", search_region=None,
                    calib_width: Optional[int] = None, calib_height: Optional[int] = None,
                    calib_left: Optional[int] = None, calib_top: Optional[int] = None) -> bool:
    """Dispatches to the fast exact-region compare (default, unchanged) or, when
    search_mode == "area", a sliding-window search over a larger calibrated
    search_region. Shared by both a step's own cooldown check and any
    image-match Condition.

    `region`/`search_region` are rescaled from (calib_width, calib_height)
    -- the screen size they were actually calibrated at, at whatever
    position (calib_left, calib_top) the client area was in then -- to the
    game window's current size/position first, if that's known and
    actually different (see _rescaled_region/poe2bot/scaling.py); a no-op
    in the ordinary case where the screen hasn't changed since
    calibration."""
    if not template_filename:
        return False
    region = _rescaled_region(region, calib_width, calib_height, calib_left, calib_top)
    if search_mode == "area":
        if not (isinstance(search_region, tuple) and len(search_region) == 4):
            log.error(f"match check for '{label}': search mode is 'area' but no valid "
                      f"search region is calibrated -- recalibrate this step")
            return False
        search_region = _rescaled_region(search_region, calib_width, calib_height, calib_left, calib_top)
        return _image_matches_area(template_filename, search_region, confidence, label, region[2:])
    return _image_matches_exact(template_filename, region, confidence, label)


def _image_matches_exact(template_filename, region, confidence: float, label: str) -> bool:
    """True if a screenshot of exactly `region` currently matches the cached
    `template_filename` (mean pixel difference).

    Compares directly against the calibrated region instead of searching for
    the template's position with OpenCV. Calibration already tells us precisely
    where the icon is, so there's nothing to search for -- skipping the
    sliding-window correlation search entirely is what actually makes this
    fast, not just a lower confidence threshold. Trade-off: this assumes the
    icon hasn't moved since calibration (e.g. the game window resizing or UI
    scale changing) -- if it has, recalibrate (or switch to area-search mode,
    see _image_matches_area) rather than expecting this to still find it
    elsewhere in the region.

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
    try:
        template = _load_template_image_resized(template_filename, (region[2], region[3]))
        screenshot = capture_region(region).convert("L")
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


def _image_matches_area(template_filename, search_region, confidence: float, label: str,
                         template_size: Optional[tuple] = None) -> bool:
    """True if `template_filename` is found anywhere within a screenshot of the
    larger `search_region`, via OpenCV's cv2.matchTemplate -- used only when a
    step/condition is calibrated in area-search mode. Unlike _image_matches_exact
    this pays for an actual sliding-window correlation search, so it costs more
    per check the larger search_region is; use it for icons that can visibly
    shift position slightly (e.g. a UI panel that reflows), not as a default.

    `template_size`, when given, resizes the template to that (width,
    height) before searching -- the icon's own calibrated size rescaled to
    the current screen (see _image_matches's `region[2:]`), since a
    resolution/aspect-ratio change means the icon itself now renders at a
    different size, not just a different position.

    TM_CCOEFF_NORMED is used because it's mean-normalized (robust to minor
    brightness shifts between calibration time and runtime) and its best-match
    score is bounded to roughly -1..1 (1 = perfect correlation), so the existing
    0-1 "confidence, higher = stricter" meaning maps directly onto it: a match is
    found if the best correlation anywhere in the search region is >= confidence.
    This is a different distance metric than _image_matches_exact's mean pixel
    difference, so a confidence value tuned for exact mode is only a starting
    point after switching a step to area mode -- expect to retune it.
    """
    try:
        template_array = (_load_template_array_resized(template_filename, tuple(template_size))
                           if template_size else _load_template_array(template_filename))
        screenshot = capture_region(search_region).convert("L")
        template_h, template_w = template_array.shape
        if screenshot.width < template_w or screenshot.height < template_h:
            log.error(
                f"match check for '{label}': search area {screenshot.size} is smaller than "
                f"calibrated template {(template_w, template_h)} -- recalibrate this step")
            return False
        screenshot_array = np.array(screenshot)
        result = cv2.matchTemplate(screenshot_array, template_array, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return max_val >= confidence
    except Exception as e:
        log.error(f"area match check for '{label}' failed ({type(e).__name__}: {e}); treating as not matching")
        return False


def _pixel_matches(pixel_pos, pixel_color, confidence: float, label: str,
                    calib_width: Optional[int] = None, calib_height: Optional[int] = None,
                    calib_left: Optional[int] = None, calib_top: Optional[int] = None) -> bool:
    """True if the current color at `pixel_pos` is within tolerance of the
    expected `pixel_color` -- shared by both a step's own cooldown check and
    any pixel-match Condition. Much cheaper than image matching (a single 1x1
    capture, no template file, no per-pixel convolution) for the common case
    where readiness shows as a stable color (a border, a glow, a swatch)
    rather than needing a whole icon comparison.

    `confidence` uses the same 0-1, higher-is-stricter meaning as image
    matching, mapped onto an equivalent max-allowed Euclidean RGB distance
    (0.9 default allows roughly 10% of the largest possible color distance).

    `pixel_pos` is rescaled from (calib_width, calib_height) at
    (calib_left, calib_top) to the game window's current size/position
    first, same as _image_matches -- see _rescaled_point/poe2bot/scaling.py.
    """
    if not pixel_pos or not pixel_color:
        return False
    try:
        x, y = _rescaled_point(pixel_pos, calib_width, calib_height, calib_left, calib_top)
        monitor = {"left": x, "top": y, "width": 1, "height": 1}
        current = tuple(_screen_capture().grab(monitor).rgb)
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(current, pixel_color)))
        max_allowed_distance = (1 - confidence) * _MAX_COLOR_DISTANCE
        return distance <= max_allowed_distance
    except Exception as e:
        log.error(f"pixel match check for '{label}' failed ({type(e).__name__}: {e}); treating as not matching")
        return False
