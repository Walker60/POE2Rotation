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

The app uses the [Sun Valley ttk theme](https://github.com/rdbende/Sun-Valley-ttk-theme)
(`sv-ttk`) for a modern, Windows-11-like dark/light look. It starts in dark mode;
click **Toggle Light/Dark** (bottom-right) to switch. This restyles standard ttk
widgets (buttons, entries, the rotation/step trees) but can't reach a couple of
things Tkinter itself doesn't theme: native dialogs (the message boxes and the
folder rename/move prompts) keep the OS's own light appearance regardless of
the app's theme, and the Windows title bar doesn't switch color on its own —
both are Tkinter/Windows limitations, not bugs.

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
a bit than skip that particular cast.

**Matching is a direct comparison against exactly the calibrated region, not a
search.** A ready-check takes a screenshot of precisely the region you drew
during calibration and compares it directly against the saved template (mean
pixel difference, scaled by the Confidence field — higher Confidence demands a
closer match) — it does not search elsewhere on screen for the icon. This is
deliberately cheap: no sliding-window matching, so check speed doesn't scale
with region size the way it used to, and a single check can no longer blow past
a very low Timeout on its own. The trade-off is that it assumes the icon is
still exactly where it was when calibrated — if the game window moves, resizes,
or the UI scale changes afterward, the check will stop matching (never crash,
it'll just always read as "not ready") and that step needs recalibrating.

**Screenshots go through `mss`, not `pyautogui`.** On Windows, `pyautogui`
(and even Pillow's own `ImageGrab.grab()` used directly) always captures the
*entire* screen internally and crops it down afterward, no matter how small
the requested region is — for a check running many times a second, that's a
real, avoidable cost that scales with your monitor's resolution. `mss`
captures only the requested rectangle directly, so this is what actually makes
repeated checks fast rather than just a smaller region or a cheaper threshold.

Calibration screenshots are stored as individual PNGs under `templates/`, named
by a random ID rather than the skill name, so rotations stay portable if you
rename or reorder things. Rotation JSON files reference these by filename only —
if you copy a `rotations/*.json` file to another machine or folder, copy its
matching `templates/*.png` file(s) too, or that step's cooldown check will simply
log an error and skip firing (never crash) until recalibrated. Files no longer
referenced by any saved or in-progress rotation are cleaned up automatically on
save, delete, and app startup.

Known limitation: calibration only supports the primary monitor.

## Organizing rotations into folders

The rotation list is a folder tree, not a flat list — set a rotation's **Folder**
field (e.g. `Bosses/HardMode` for nesting) to group it under a collapsible folder
node instead of leaving it at the root. Folders exist purely because rotations
reference them: there's no separate "create an empty folder" step, and a folder
disappears from the tree once nothing is in it anymore (its now-empty directory
is removed automatically). A folder is just where the rotation's JSON file
physically lives on disk — it isn't duplicated inside the file itself, so there's
no way for the two to drift out of sync.

Two dedicated actions for reorganizing without editing rotations one at a time:
- **Right-click a folder → Rename Folder...** renames/moves it, taking every
  rotation inside it (and any nested subfolders) along in one action.
- **Right-click one or more selected rotations → Move to Folder...** moves all
  of them to a destination folder at once. Ctrl/Shift-click to select several
  rotations first.

## Usage

1. Click **New**, give the rotation a name and, optionally, a Folder to group it
   under, add steps (optional display name,
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
   the original and can be tweaked independently from there. **Add Sleep** adds a
   step with no key at all — just a deliberate pause (Delay ± Jitter, same fields
   as any other step) with nothing pressed, for spacing out a rotation without
   tying the wait to any particular skill. It shows up in the list as "Sleep"
   unless you give it its own Name. You can also turn any existing step into a
   sleep by clearing its Key field and clicking Update Selected, or turn a sleep
   back into a real step by typing a key into it.
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
3. **Cancel Key** (optional) immediately stops this rotation if it's currently
   running — bind it the same way as the trigger hotkey, e.g. to your dodge key,
   so rolling away instantly cuts off whatever the rotation was doing instead of
   fighting your input. Unlike the trigger hotkey, a cancel key is never
   exclusive — multiple rotations can share the exact same one (space cancels
   *whichever* of them happens to be running), since it only ever stops, never
   starts or toggles. The one restriction: a rotation's cancel key can't be the
   same as its own trigger hotkey, since that would race a single keypress
   against itself (start and immediately self-cancel).
4. Selecting a rotation in the list and clicking **Copy** duplicates it (steps,
   mode, and all) as a new unsaved rotation named "*name* (copy)" — the hotkey is
   left unbound since it can't share the original's, the cancel key (if any) is
   carried over as-is since sharing one is fine, and template-based cooldown
   checks are carried over by reference (no recalibration needed). Rename it,
   assign a hotkey, and **Save Rotation** when ready.
5. Rotations only fire keystrokes while the configured game process has OS focus —
   switching away pauses a running rotation; switching back resumes it automatically.
6. **Stop Bot** stops any running rotation and disables all hotkeys (including the
   panic key and any cancel keys) at once; **Start Bot** re-enables them. While
   stopped, nothing can be triggered until you press Start Bot again.

Rotations are saved as one JSON file per rotation under `rotations/`, in whatever
subfolder structure their Folder field puts them in.
