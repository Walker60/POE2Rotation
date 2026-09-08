"""Rescales a calibrated point/region from the screen size it was captured
at to whatever size the game is actually rendering at now, so a rotation
calibrated on one monitor/computer still lines up on another one at a
different resolution -- including a different aspect ratio -- without
needing to be recalibrated there.

The model: exactly the "anchor" concept every game-UI framework (Unity,
Unreal, CSS) uses to make a HUD element behave sensibly when its canvas
resizes. Each axis of a calibrated point is classified as anchored to the
"start" (left/top), "center", or "end" (right/bottom) of that axis,
based on which third of the calibration-time screen it fell in, then
repositioned to keep the same *margin from that anchor*, scaled by how
much that axis actually changed -- not by treating every point as if it
were pinned to the top-left corner. A plain proportional scale (the
common case: same aspect ratio, just a different resolution) falls out of
this as a special case automatically, since scale_x == scale_y then makes
the anchor choice mathematically irrelevant. It's only when the aspect
ratio actually changes that the anchor choice matters -- and ARPG HUDs
(health orb bottom-left, flask bar bottom-center, minimap top-right, etc.)
are built almost universally on exactly this kind of edge/corner-anchored
layout, which is why this is a reasonable default rather than a guess
specific to any one game.

Not a guarantee -- a HUD that does something unusual (e.g. reflowing
entirely rather than anchoring) will still need recalibrating, the same
as today. Use ConditionsMixin's/ConditionGroupsMixin's Test Match button to
verify a rescaled condition actually still matches after moving to a new
screen.
"""
from typing import Tuple

_START, _CENTER, _END = "start", "center", "end"


def _anchor_for(value: int, calib_size: int) -> str:
    """Which third of the calibration-time axis `value` fell in -- the
    anchor a game HUD element built on an edge/corner-anchored layout would
    most plausibly be pinned to."""
    if calib_size <= 0:
        return _CENTER
    third = calib_size / 3
    if value < third:
        return _START
    if value > 2 * third:
        return _END
    return _CENTER


def _scale_axis(value: float, calib_size: int, current_size: int, anchor: str) -> float:
    """Repositions `value` (a coordinate on one axis) from a `calib_size`-
    wide/tall screen to a `current_size` one, preserving its margin from
    whichever edge `anchor` names, scaled by this axis's own ratio -- not
    the other axis's, since an aspect-ratio change means the two can
    differ. `anchor` == "center" preserves the margin from the axis's
    midpoint instead of an edge, for a horizontally/vertically centered
    element (e.g. a flask bar) that should stay centered rather than
    drift toward whichever edge it happened to be closer to."""
    scale = current_size / calib_size if calib_size > 0 else 1.0
    if anchor == _START:
        return value * scale
    if anchor == _END:
        margin = calib_size - value
        return current_size - margin * scale
    margin = value - calib_size / 2
    return current_size / 2 + margin * scale


def scale_point(x: int, y: int, calib_w: int, calib_h: int,
                current_w: int, current_h: int) -> Tuple[int, int]:
    """Rescales a single calibrated point (e.g. a pixel-match condition's
    pixel_pos) from a calib_w x calib_h screen to a current_w x current_h
    one. A no-op (returns (x, y) unchanged) when the size hasn't actually
    changed -- the overwhelmingly common case, so callers on the hot
    polling path should still skip calling this at all when they can
    cheaply tell sizes match, rather than relying on this short-circuit
    alone."""
    if calib_w == current_w and calib_h == current_h:
        return x, y
    new_x = _scale_axis(x, calib_w, current_w, _anchor_for(x, calib_w))
    new_y = _scale_axis(y, calib_h, current_h, _anchor_for(y, calib_h))
    return round(new_x), round(new_y)


def scale_region(region: Tuple[int, int, int, int], calib_w: int, calib_h: int,
                  current_w: int, current_h: int) -> Tuple[int, int, int, int]:
    """Rescales a calibrated (left, top, width, height) region the same
    way scale_point does a single point -- anchored by its OWN CENTER's
    position (not its top-left corner), since that's what stays fixed
    under a center anchor; width/height always scale by their axis's
    ratio regardless of anchor, since a HUD element's size, unlike its
    position, isn't anchor-sensitive -- it just scales with the overall
    UI. A no-op when the size hasn't actually changed."""
    if calib_w == current_w and calib_h == current_h:
        return region
    left, top, width, height = region
    cx, cy = left + width / 2, top + height / 2
    scale_x = current_w / calib_w if calib_w > 0 else 1.0
    scale_y = current_h / calib_h if calib_h > 0 else 1.0
    new_width, new_height = width * scale_x, height * scale_y
    new_cx = _scale_axis(cx, calib_w, current_w, _anchor_for(cx, calib_w))
    new_cy = _scale_axis(cy, calib_h, current_h, _anchor_for(cy, calib_h))
    new_left = round(new_cx - new_width / 2)
    new_top = round(new_cy - new_height / 2)
    return new_left, new_top, round(new_width), round(new_height)
