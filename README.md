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
  (default `PathOfExile.exe`). Verify the exact name via Task Manager > Details while
  POE2 is running — it may differ by storefront (Steam/EGS/standalone). Set this to
  `notepad.exe` to test the bot against Notepad instead of the game.
- `POE2BOT_PANIC_KEY` — reserved global hotkey that instantly stops every running
  rotation (default `f12`). Cannot be bound to a rotation.

## Cooldown-gated steps (optional, per step)

Any step can optionally wait for a calibrated "ready" icon to appear on screen
before firing its key, instead of firing blind on a fixed delay. In the step
editor, click **Calibrate...**: the window hides, drag a small rectangle tightly
around the skill's icon while it's off cooldown, then confirm the capture. If the
icon doesn't reappear within the step's Timeout, that cast is skipped (logged as a
warning) and the rotation moves on — it never fires blind and never hangs.

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

1. Click **New**, give the rotation a name, add steps (key, delay in ms, optional
   jitter, optional hold duration), and choose **Once** (single pass) or **Loop**
   (repeats until re-triggered or the panic key is pressed).
2. Click **Bind Hotkey...** and press the key you want to trigger this rotation, then
   **Save Rotation**.
3. Rotations only fire keystrokes while the configured game process has OS focus —
   switching away pauses a running rotation; switching back resumes it automatically.
4. **Stop Bot** stops any running rotation and disables all hotkeys (including the
   panic key) at once; **Start Bot** re-enables them. While stopped, nothing can be
   triggered until you press Start Bot again.

Rotations are saved as one JSON file per rotation under `rotations/`.
