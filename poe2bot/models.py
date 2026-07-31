import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

import keyboard

from poe2bot import templates

VALID_MODES = ("once", "loop")
VALID_PAUSE_MODES = ("duration", "toggle")
VALID_READY_MATCH_TYPES = ("image", "pixel")


@dataclass
class Step:
    key: str
    name: str = ""   # optional display label (e.g. "Fireball"); falls back to `key` in the GUI if blank
    delay_ms: int = 100
    jitter_ms: int = 0
    hold_ms: int = 0
    hold_jitter_ms: int = 0   # uniform random +/- jitter applied to hold_ms each time (only when hold_ms > 0)
    ready_match_type: str = "image"                             # "image" or "pixel" -- which method below is active
    ready_template: Optional[str] = None                       # filename only, resolved via templates.template_path()
    ready_region: Optional[Tuple[int, int, int, int]] = None   # (left, top, width, height), absolute screen px -- image mode
    ready_pixel_pos: Optional[Tuple[int, int]] = None           # (x, y) absolute screen px -- pixel mode
    ready_pixel_color: Optional[Tuple[int, int, int]] = None    # expected (r, g, b) when "ready" -- pixel mode
    ready_confidence: float = 0.9
    ready_timeout_ms: int = 300

    def has_ready_check(self) -> bool:
        """True if this step has a cooldown check configured, via whichever
        method ready_match_type currently points at."""
        if self.ready_match_type == "pixel":
            return self.ready_pixel_color is not None
        return bool(self.ready_template)

    @staticmethod
    def from_dict(data: dict) -> "Step":
        region = data.get("ready_region")
        pixel_pos = data.get("ready_pixel_pos")
        pixel_color = data.get("ready_pixel_color")
        return Step(
            key=data["key"],
            name=data.get("name", ""),
            delay_ms=int(data.get("delay_ms", 100)),
            jitter_ms=int(data.get("jitter_ms", 0)),
            hold_ms=int(data.get("hold_ms", 0)),
            hold_jitter_ms=int(data.get("hold_jitter_ms", 0)),
            ready_match_type=data.get("ready_match_type", "image"),
            ready_template=data.get("ready_template"),
            ready_region=tuple(region) if region is not None else None,  # JSON round-trips tuples as lists
            ready_pixel_pos=tuple(pixel_pos) if pixel_pos is not None else None,
            ready_pixel_color=tuple(pixel_color) if pixel_color is not None else None,
            ready_confidence=float(data.get("ready_confidence", 0.9)),
            ready_timeout_ms=int(data.get("ready_timeout_ms", 300)),
        )


@dataclass
class Rotation:
    name: str
    mode: str = "once"
    hotkey: Optional[str] = None
    cancel_key: Optional[str] = None   # e.g. the dodge key -- immediately stops this rotation if running
    reset_key: Optional[str] = None    # immediately restarts this rotation from its first step if running
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
            pause_key=data.get("pause_key"),
            pause_mode=data.get("pause_mode", "duration"),
            pause_duration_ms=int(data.get("pause_duration_ms", 1000)),
            steps=[Step.from_dict(step) for step in data.get("steps", [])],
        )


def folder_path_problem(folder: str) -> Optional[str]:
    """None if `folder` is a valid '/'-separated group path, else a human-readable
    reason it isn't. Shared by rotation validation and the GUI's rename/move-to-folder
    dialogs, so both reject the same things the same way."""
    if not folder:
        return None
    for part in folder.split("/"):
        part = part.strip()
        if not part:
            return "Folder path cannot have empty segments (leading/trailing/double slash)."
        if part in (".", ".."):
            return "Folder path cannot contain '.' or '..' segments."
    return None


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

    for i, step in enumerate(rotation.steps, start=1):
        # A blank key means this step is a sleep/pause: no key to press, it just
        # waits out delay_ms (+/- jitter_ms) like any other step's post-fire wait.
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

    return problems
