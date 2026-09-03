STATUS_LABELS = {
    "idle": "",
    "running": " (running)",
    "waiting_focus": " (waiting for game focus)",
    "paused": " (paused)",
    "resetting": " (resetting)",
}

# Per-status accent colors, shared by the Activity window's pane indicators
# and the rotation list's row tinting -- None means "no tint" (idle). Matches
# the Dracula palette (poe2bot/gui/theme.py's DRACULA dict) used for dark
# mode, kept as plain hex here rather than importing theme.py so this stays
# a tiny, dependency-free module.
STATUS_COLORS = {
    "idle": None,
    "running": "#50fa7b",         # Dracula green
    "waiting_focus": "#ffb86c",   # Dracula orange
    "paused": "#8be9fd",          # Dracula cyan
    "resetting": "#bd93f9",       # Dracula purple
}
