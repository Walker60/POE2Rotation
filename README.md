# POE2 Rotation Bot

A small Tkinter tool for defining skill rotations, saving them, and binding each one to
its own global hotkey. When a bound hotkey is pressed, the rotation's key sequence is
sent to whichever window currently has focus (intended to be Path of Exile 2).

## Setup

```
pip install -r requirements.txt
```

`tkinter` ships with the standard python.org Windows installer — verify with
`python -m tkinter` (should open a small test window). It is not a pip package.

## Running

```
python main.py
```

The `keyboard` library installs a low-level system-wide keyboard hook. This usually
works fine unrelated to admin rights, **except**: Windows blocks a lower-privilege
process from sending input to a higher-privilege (elevated) window (UIPI). If POE2
(or its launcher) runs elevated, this bot must also run elevated — as Administrator —
or its keystrokes will silently not reach the game. If hotkeys or keystrokes don't
seem to work at all, try running from an elevated terminal first to rule this out.

## Configuration

- `POE2BOT_TARGET_PROCESS` — the game executable name the focus guard checks for
  (default `PathOfExileSteam.exe`). Verify the exact name via Task Manager > Details
  while POE2 is running — it may differ by storefront (Steam/EGS/standalone). Set
  this to `notepad.exe` to test the bot against Notepad instead of the game.
- `POE2BOT_PANIC_KEY` — reserved global hotkey that instantly stops every running
  rotation (default `f12`). Cannot be bound to a rotation.

## Cooldown-gated steps (optional, per step)

Any step can optionally wait for a calibrated "ready" icon to appear on screen
before firing its key, instead of firing blind on a fixed delay. In the step
editor, click **Calibrate...**: the window hides, drag a small rectangle tightly
around the skill's icon while it's off cooldown, then confirm the capture. If the
icon doesn't reappear within the step's Timeout, that cast is skipped (logged as a
warning) and the rotation moves on — it never fires blind and never hangs.

**Timeout is a quick "is it ready right now?" check, not a wait-for-cooldown
timer.** The bot blocks the *entire* rotation for up to Timeout milliseconds on
every step that has a cooldown check, so a large value (the old default was
5000ms) makes a fast rotation feel like it stalls or hangs on any skill that's
still cooling down — it's not actually hung, it's just sitting there waiting out
the full timeout before giving up and moving on. The default is now 300ms, which
is enough time to absorb normal check jitter without noticeably delaying the rest
of the rotation; only raise it for a specific step if you'd genuinely rather wait
a bit than skip that particular cast. Keep the calibrated region small and tight
around just the icon, too — matching (`confidence`-based, via OpenCV) gets more
expensive the larger the region is, and if a *single* check takes longer than
your configured Timeout, the timeout can't actually be honored no matter how low
you set it — you'll see it stall well past 50ms even with Timeout set to 50.
If a step still feels slow after lowering Timeout, recalibrate it with a
noticeably smaller box before assuming something else is wrong.

Calibration screenshots are stored as individual PNGs under `templates/`, named
by a random ID rather than the skill name, so rotations stay portable if you
rename or reorder things. Rotation JSON files reference these by filename only —
if you copy a `rotations/*.json` file to another machine or folder, copy its
matching `templates/*.png` file(s) too, or that step's cooldown check will simply
log an error and skip firing (never crash) until recalibrated. Files no longer
referenced by any saved or in-progress rotation are cleaned up automatically on
save, delete, and app startup.

Cooldown-gated steps require `opencv-python` (installed via `requirements.txt`) —
the `confidence` matching parameter raises an error at runtime if OpenCV isn't
importable.

Known limitation: calibration only supports the primary monitor.

## Usage

1. Click **New**, give the rotation a name, add steps (optional display name,
   key, delay in ms, optional jitter, optional hold duration, optional hold
   jitter), and choose **Once** (single pass) or **Loop** (repeats until
   re-triggered or the panic key is pressed). Jitter randomizes the delay ± that
   many ms each time; Hold Jitter does the same for how long the key is held
   down — both make timing look less like a perfectly repeating macro. Leave
   either at 0 for exact, fixed timing. The step's Name (e.g. "Fireball") is just
   a label for the steps list — it doesn't affect what gets sent, that's the Key
   field. If you need the same skill more than once in a rotation, select it and
   click **Copy Selected** rather than re-entering it (and recalibrating its
   cooldown check, if it has one) from scratch — the copy is inserted right after
   the original and can be tweaked independently from there.
2. Click **Bind Hotkey...** and either press a keyboard key or click a mouse button
   to trigger this rotation, then **Save Rotation**. Left/middle/right click and the
   two extra side buttons (mouse 4/5) are all supported.

   **Caution:** binding left or right click makes that button trigger the rotation
   *everywhere*, not just in-game — every left-click in Windows Explorer, every
   right-click context menu, etc. Middle-click or a side button (mouse 4/5) is
   almost always the safer choice unless you're certain you want that trade-off.

   **Unbind** clears this rotation's hotkey and saves immediately — use it when you
   want to move a hotkey to a different rotation: Unbind it here first to free the
   key up, then Bind Hotkey it on the other rotation. **Unbind All** does the same
   for every saved rotation at once (with a confirmation prompt first), for
   starting your key bindings over from scratch.
3. Selecting a rotation in the list and clicking **Copy** duplicates it (steps,
   mode, and all) as a new unsaved rotation named "*name* (copy)" — the hotkey is
   left unbound since it can't share the original's, and template-based cooldown
   checks are carried over by reference (no recalibration needed). Rename it,
   assign a hotkey, and **Save Rotation** when ready.
3. Rotations only fire keystrokes while the configured game process has OS focus —
   switching away pauses a running rotation; switching back resumes it automatically.
4. **Stop Bot** stops any running rotation and disables all hotkeys (including the
   panic key) at once; **Start Bot** re-enables them. While stopped, nothing can be
   triggered until you press Start Bot again.

Rotations are saved as one JSON file per rotation under `rotations/`.
