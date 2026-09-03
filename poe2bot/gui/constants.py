STATUS_LABELS = {
    "idle": "",
    "running": " (running)",
    "waiting_focus": " (waiting for game focus)",
    "paused": " (paused)",
    "resetting": " (resetting)",
}

# Per-status accent colors, shared by the Activity window's pane indicators
# and the rotation list's row tinting -- None means "no tint" (idle).
STATUS_COLORS = {
    "idle": None,
    "running": "#3fb950",
    "waiting_focus": "#d29922",
    "paused": "#58a6ff",
    "resetting": "#a371f7",
}
