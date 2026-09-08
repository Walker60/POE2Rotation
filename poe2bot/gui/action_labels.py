"""Canonical Condition.action ("fire"/"block"/"hold" -- see
poe2bot/models.py) codes -> human labels, and the per-context label sets
derived from them.

A step's own Conditions (poe2bot/gui/conditions.py + its tree-row summary in
poe2bot/gui/step_editor.py) and a ConditionGroup's single gating condition
(poe2bot/gui/condition_groups.py) each need a slightly different display
string for the same three codes -- this is the one place a renamed/added
action code needs updating; everything below derives from ACTION_LABELS.
"""

ACTION_LABELS = {"fire": "Execute", "block": "Skip", "hold": "Override"}

# A step's own Condition action combobox (poe2bot/gui/conditions.py) and its
# tree-row summary (poe2bot/gui/step_editor.py) use these full labels.
STEP_CONDITION_ACTION_LABELS = {
    "fire": f"{ACTION_LABELS['fire']} Step",
    "block": f"{ACTION_LABELS['block']} Step",
    "hold": "Override Hold Time",  # not just "<ACTION_LABELS['hold']> Step" -- names what it overrides
}

# A ConditionGroup's single gating condition action combobox
# (poe2bot/gui/condition_groups.py) -- never "hold", a group has no single
# step's hold_ms/delay_ms to override (see validate_rotation).
GROUP_CONDITION_ACTION_LABELS = {
    "fire": f"{ACTION_LABELS['fire']} Group",
    "block": f"{ACTION_LABELS['block']} Group",
}
