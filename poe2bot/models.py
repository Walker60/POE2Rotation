import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

import keyboard

from poe2bot import templates

VALID_MODES = ("once", "loop")


@dataclass
class Step:
    key: str
    name: str = ""   # optional display label (e.g. "Fireball"); falls back to `key` in the GUI if blank
    delay_ms: int = 100
    jitter_ms: int = 0
    hold_ms: int = 0
    hold_jitter_ms: int = 0   # uniform random +/- jitter applied to hold_ms each time (only when hold_ms > 0)
    ready_template: Optional[str] = None                       # filename only, resolved via templates.template_path()
    ready_region: Optional[Tuple[int, int, int, int]] = None   # (left, top, width, height), absolute screen px
    ready_confidence: float = 0.9
    ready_timeout_ms: int = 300

    @staticmethod
    def from_dict(data: dict) -> "Step":
        region = data.get("ready_region")
        return Step(
            key=data["key"],
            name=data.get("name", ""),
            delay_ms=int(data.get("delay_ms", 100)),
            jitter_ms=int(data.get("jitter_ms", 0)),
            hold_ms=int(data.get("hold_ms", 0)),
            hold_jitter_ms=int(data.get("hold_jitter_ms", 0)),
            ready_template=data.get("ready_template"),
            ready_region=tuple(region) if region is not None else None,  # JSON round-trips tuples as lists
            ready_confidence=float(data.get("ready_confidence", 0.9)),
            ready_timeout_ms=int(data.get("ready_timeout_ms", 300)),
        )


@dataclass
class Rotation:
    name: str
    mode: str = "once"
    hotkey: Optional[str] = None
    cancel_key: Optional[str] = None   # e.g. the dodge key -- immediately stops this rotation if running
    steps: List[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "hotkey": self.hotkey,
            "cancel_key": self.cancel_key,
            "steps": [asdict(step) for step in self.steps],
        }

    @staticmethod
    def from_dict(data: dict) -> "Rotation":
        return Rotation(
            name=data["name"],
            mode=data.get("mode", "once"),
            hotkey=data.get("hotkey"),
            cancel_key=data.get("cancel_key"),
            steps=[Step.from_dict(step) for step in data.get("steps", [])],
        )


def validate_rotation(rotation: Rotation) -> List[str]:
    """Return a list of human-readable problems with `rotation`. Empty list == valid."""
    problems = []

    if not rotation.name or not rotation.name.strip():
        problems.append("Name cannot be empty.")

    if rotation.mode not in VALID_MODES:
        problems.append(f"Mode must be one of {VALID_MODES}, got '{rotation.mode}'.")

    if rotation.cancel_key and rotation.cancel_key == rotation.hotkey:
        problems.append("Cancel key cannot be the same as this rotation's own trigger hotkey.")

    if not rotation.steps:
        problems.append("Rotation must have at least one step.")

    for i, step in enumerate(rotation.steps, start=1):
        if not step.key or not step.key.strip():
            problems.append(f"Step {i}: key cannot be empty.")
        else:
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

        if step.ready_template:
            if not (isinstance(step.ready_region, tuple) and len(step.ready_region) == 4):
                problems.append(f"Step {i}: has a cooldown check but no calibrated region (recalibrate).")
            if not os.path.isfile(templates.template_path(step.ready_template)):
                problems.append(f"Step {i}: calibrated template image is missing on disk (recalibrate).")
            if not (0 < step.ready_confidence <= 1):
                problems.append(f"Step {i}: confidence must be greater than 0 and at most 1.")
            if step.ready_timeout_ms < 0:
                problems.append(f"Step {i}: ready_timeout_ms cannot be negative.")

    return problems
