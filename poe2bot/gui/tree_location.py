from typing import NamedTuple, Optional, Tuple


class Location(NamedTuple):
    """Where one row of the Skill Steps tree lives: (group_path, step_idx,
    cond_idx, subcond_idx) -- see StepEditorMixin's own docstring
    (poe2bot/gui/step_editor.py) for exactly what each slot being None vs
    set means. A plain NamedTuple rather than a bare tuple purely so new
    code can write `loc.step_idx` instead of a positional `loc[1]` -- it
    still unpacks/indexes exactly like a 4-tuple (loc[0]/loc[1]/..., or
    `group_path, step_idx, cond_idx, subcond_idx = loc`), so every existing
    call site built around that stays valid unchanged."""
    group_path: Tuple[int, ...]
    step_idx: Optional[int] = None
    cond_idx: Optional[int] = None
    subcond_idx: Optional[int] = None

    @property
    def is_group_header(self) -> bool:
        """True for a ConditionGroup's own header row (group_path names it,
        step_idx/cond_idx/subcond_idx all None)."""
        return self.step_idx is None

    @property
    def is_step(self) -> bool:
        """True for a Step's own row (not one of its conditions)."""
        return self.step_idx is not None and self.cond_idx is None

    @property
    def is_condition_entry(self) -> bool:
        """True for a top-level entry in a step's own .conditions -- either
        a plain Condition's row, or a SkillConditionGroup's own header row
        (the shape alone can't tell those apart, see _is_skill_group_entry)."""
        return self.cond_idx is not None and self.subcond_idx is None

    @property
    def is_nested_condition(self) -> bool:
        """True for a plain Condition nested inside a SkillConditionGroup."""
        return self.subcond_idx is not None
