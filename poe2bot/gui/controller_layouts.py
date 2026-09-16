"""Per-controller-type button labels/layout for gui/controller_map_window.py's
click-to-choose button picker -- the alternative to physically pressing a
real controller, which is the only reliable way to bind a Steam Deck's own
built-in controls today (they're normally owned by Steam Input and never
reach evdev unless poe2bot itself is launched through Steam -- see README's
Steam Deck section).

Both layouts below encode the exact same sixteen names controller.py's
virtual Xbox 360 pad understands (poe2bot.controller.VALID_BUTTON_NAMES) --
Xbox and Steam Deck differ only in what each physical button is CALLED (e.g.
the Deck's shoulder buttons read L1/R1/L2/R2 and its two menu buttons read
View/Menu, where an Xbox controller reads LB/RB/LT/RT and Back/Start), never
in which sixteen buttons exist or what a chosen button actually does.
"""

CONTROLLER_TYPE_LABELS = {
    "xbox": "Xbox Controller",
    "steam_deck": "Steam Deck",
}


def controller_type_from_label(label: str) -> str:
    for key, value in CONTROLLER_TYPE_LABELS.items():
        if value == label:
            return key
    return "xbox"


# Each group is (title, buttons), where buttons is a tuple of
# (button_name, display_label, grid_row, grid_col) -- grid_row/col position
# the button within its own LabelFrame so buttons belonging to the same
# physical part of the controller (the D-Pad's plus shape, the face buttons'
# diamond) are laid out that way here too, rather than in an arbitrary row.
_DPAD = ("D-Pad", (
    ("dpad_up", "↑", 0, 1),
    ("dpad_left", "←", 1, 0),
    ("dpad_right", "→", 1, 2),
    ("dpad_down", "↓", 2, 1),
))

_FACE_BUTTONS = ("Face Buttons", (
    ("y", "Y", 0, 1),
    ("x", "X", 1, 0),
    ("b", "B", 1, 2),
    ("a", "A", 2, 1),
))

_STICKS = ("Sticks", (
    ("ls", "Left Stick", 0, 0),
    ("rs", "Right Stick", 0, 1),
))

CONTROLLER_LAYOUTS = {
    "xbox": (
        _DPAD,
        _FACE_BUTTONS,
        ("Bumpers & Triggers", (
            ("lb", "LB", 0, 0),
            ("rb", "RB", 0, 1),
            ("lt", "LT", 1, 0),
            ("rt", "RT", 1, 1),
        )),
        _STICKS,
        ("Menu", (
            ("back", "Back", 0, 0),
            ("start", "Start", 0, 1),
        )),
    ),
    "steam_deck": (
        _DPAD,
        _FACE_BUTTONS,
        ("Bumpers & Triggers", (
            ("lb", "L1", 0, 0),
            ("rb", "R1", 0, 1),
            ("lt", "L2", 1, 0),
            ("rt", "R2", 1, 1),
        )),
        _STICKS,
        ("Menu", (
            ("back", "View", 0, 0),
            ("start", "Menu", 0, 1),
        )),
    ),
}
