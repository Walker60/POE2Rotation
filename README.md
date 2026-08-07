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

Any step can optionally wait for a calibrated "ready" signal on screen before
firing its key, instead of firing blind on a fixed delay. There are two ways to
calibrate this, and a step uses exactly one at a time:

- **Image Match...** — the window hides, drag a small rectangle tightly around
  the skill's icon while it's off cooldown, then confirm the capture. Best when
  the icon's whole appearance (shape, highlight, etc.) changes between ready and
  on-cooldown.
- **Pixel Match...** — the window hides, click exactly on a single pixel that's
  a distinct, reliable color when the skill is ready (e.g. a bright border pixel
  on the icon), then confirm the sampled color. Cheaper than an image match and
  handy when a whole-icon capture isn't needed — one pixel read and compare is
  enough to tell ready from not-ready.

Whichever you calibrate last is the one that's active for that step; switching
from one to the other replaces the previous calibration rather than combining
them. If the ready signal doesn't reappear within the step's Timeout, that cast
is skipped (logged as a warning) and the rotation moves on — it never fires
blind and never hangs.

**Timeout is a quick "is it ready right now?" check, not a wait-for-cooldown
timer.** The bot blocks the *entire* rotation for up to Timeout milliseconds on
every step that has a cooldown check, so a large value (the old default was
5000ms) makes a fast rotation feel like it stalls or hangs on any skill that's
still cooling down — it's not actually hung, it's just sitting there waiting out
the full timeout before giving up and moving on. The default is now 300ms, which
is enough time to absorb normal check jitter without noticeably delaying the rest
of the rotation; only raise it for a specific step if you'd genuinely rather wait
a bit than skip that particular cast.

**Matching is a direct comparison against exactly the calibrated spot, not a
search.** An image-match check takes a screenshot of precisely the region you
drew during calibration and compares it directly against the saved template
(mean pixel difference, scaled by the Confidence field — higher Confidence
demands a closer match). A pixel-match check reads the single calibrated pixel
and compares its color to the saved color (Euclidean RGB distance, scaled by
the same Confidence field), which is cheaper still since there's no image to
decode or diff. Neither mode searches elsewhere on screen for the icon — this
is deliberately cheap: no sliding-window matching, so check speed doesn't scale
with region size the way it used to, and a single check can no longer blow past
a very low Timeout on its own. The trade-off is that both assume the icon/pixel
is still exactly where it was when calibrated — if the game window moves,
resizes, or the UI scale changes afterward, the check will stop matching (never
crash, it'll just always read as "not ready") and that step needs recalibrating.

**Screenshots go through `mss`, not `pyautogui`.** On Windows, `pyautogui`
(and even Pillow's own `ImageGrab.grab()` used directly) always captures the
*entire* screen internally and crops it down afterward, no matter how small
the requested region is — for a check running many times a second, that's a
real, avoidable cost that scales with your monitor's resolution. `mss`
captures only the requested rectangle directly, so this is what actually makes
repeated checks fast rather than just a smaller region or a cheaper threshold.

Image-match calibrations are stored as individual PNGs under `templates/`, named
by a random ID rather than the skill name, so rotations stay portable if you
rename or reorder things. Rotation JSON files reference these by filename only —
if you copy a `rotations/*.json` file to another machine or folder, copy its
matching `templates/*.png` file(s) too, or that step's cooldown check will simply
log an error and skip firing (never crash) until recalibrated. Files no longer
referenced by any saved or in-progress rotation are cleaned up automatically on
save, delete, and app startup. Pixel-match calibrations don't need any of this —
the point and color are just numbers stored directly in the rotation's JSON, so
they're already portable with no matching file to copy.

Known limitation: calibration only supports the primary monitor.

## Buff-based hold/delay override (optional, per step)

Some skills' animation time changes while a particular buff is up (faster or
slower attack/cast speed, etc.), and that buff isn't always active. A step can
have one **Buff Check** — the same Image Match/Pixel Match calibration flow as
the cooldown check above — plus a **Hold (ms)** and/or **Delay (ms)** value to
use *instead of* the step's normal Hold/Delay whenever that check currently
matches on screen.

- Calibrate it the same way as a cooldown check or condition: click **Image
  Match...** or **Pixel Match...** in the Buff Check group, capture the
  buff's icon or a distinct pixel, and confirm.
- Fill in **Hold (ms)** and/or **Delay (ms)** with the values to use while the
  buff is active. Leaving either blank means that particular value is never
  overridden — the step keeps its normal Hold or Delay even while the buff
  check matches. A buff check with both left blank would never have any
  effect, so saving is blocked until at least one is set.
- Unlike the cooldown check, this is read **once, instantly**, right before
  firing — same as a Condition — not polled or waited on.
- The buff state is read once per cast and used for both that cast's hold
  *and* its following delay, so if the buff expires partway through the
  post-cast delay, that delay still finishes out at the buffed value rather
  than switching mid-wait.

## Repeat and Combine Hold (optional, per step)

A step can fire more than once per pass with **Repeat** — set it above 1 and
the step fires that many times before the rotation moves on. With **Combine
Hold** off (the default), Repeat is just N independent copies of the step
back to back: press/hold/release, then Delay, then press/hold/release again,
and so on, N times. This works for a tap (Hold = 0) too — it just presses the
key N times with Delay between each.

**Combine Hold** changes this for a step that actually has a Hold: instead of
N separate hold+release+Delay cycles, the key is pressed once and held
continuously for Hold × Repeat, then Delay applies exactly once at the end.
For example, a step with a 50ms Delay, a 500ms Hold, and Repeat set to 3 —
with Combine Hold on — holds the key for 1500ms straight, then waits 50ms,
instead of holding-releasing-waiting three separate times. This is for skills
where holding longer does more (a charge-up attack, for instance) rather than
skills that need a fresh, discrete press each time. Combine Hold has no effect
(and isn't needed) on a tap, or when Repeat is 1.

Repeat only fires once through the step's own cooldown check and any
Conditions — they aren't re-checked between reps, the same "checked once,
right before firing" rule Conditions and the Buff Check already follow.

## Conditions (optional, per step)

A step can also have any number of **conditions** — extra image/pixel-match
gates layered on top of its own cooldown check. A step only fires if its
cooldown check (if any) is ready *and* every one of its conditions currently
matches; if any single condition doesn't match, that cast is skipped (logged)
the same way a not-ready cooldown check is, and the rotation moves on to the
next step without eating that step's delay.

Conditions are a different kind of check than the cooldown check above:
they're read **once, instantly**, right before firing — there's no Timeout
and no waiting for one to become true. This suits things like "only cast this
if I still have a buff active" or "only cast this if the target isn't already
below a health threshold" — a quick yes/no read of something elsewhere on
screen, not a cooldown icon you'd expect to wait out.

The step list shows conditions as indented rows nested under their step, with
an expand/collapse arrow — a step with any conditions starts expanded so
you'll always see one right after adding it, though a manual collapse doesn't
persist across further edits to the step list.

- Select a step (or one of its existing conditions) and click **Add Image
  Condition...** or **Add Pixel Condition...** to attach a new one — same
  calibration flow as Image Match/Pixel Match above, plus a Confidence field
  in the confirmation dialog (each condition has its own).
- **Double-click** an existing condition to recalibrate it in place (region or
  pixel, matching whichever type it already is) — its Confidence field starts
  pre-filled with its current value. Double-clicking a step itself does
  nothing; steps are still recalibrated via Image Match/Pixel Match.
- Select a condition and click **Remove Selected** to delete just that
  condition, leaving the step and its other conditions untouched.
- Select a condition, type into the **Name** field, and click **Rename
  Selected Condition** to give it a label (e.g. "Bleeding") — it's shown in
  the list instead of the auto-generated "Pixel RGB(...)"/"Image WxH"
  description, purely cosmetic, and survives recalibration.
- Select a condition and click **Move Up**/**Move Down** (in Step Actions), or
  just drag it, to reorder it within its own step. Reordering conditions is
  cosmetic only (all of a step's conditions are still AND'd together
  regardless of order).

Copying a step (Copy/Paste, or copying a whole rotation) carries its
conditions along with it, along with its buff check and hold/delay overrides
if any. Conditions and buff checks with an image-match template participate
in the same template-file portability/cleanup rules described above.

## Multi-select, drag-and-drop, and clipboard

The step list supports multi-select (ctrl/shift-click, same as the rotation
list on the left) and drag-and-drop, on top of the buttons described above:

- **Drag** one or more selected rows to reorder them — drag a step (or
  several multi-selected steps) to reposition it in the rotation; drag a
  condition (or several, multi-selected) to reposition it within its own
  step. A highlighted row shows where it'll land as you drag. Dragging a mix
  of steps and conditions together, or conditions from more than one step at
  once, isn't supported — nothing happens rather than doing something
  surprising. Move Up/Move Down still work as a click-based alternative.
- **Copy** copies every currently-selected step (with its conditions) to an
  in-memory clipboard; **Paste** inserts a copy of the clipboard's contents
  after whichever step/condition is selected (or at the end, if nothing is).
  The clipboard isn't tied to the rotation you copied from — copy some steps,
  switch to a *different* rotation in the left-hand list, and Paste to bring
  them over, cooldown checks and conditions included (image-match templates
  are shared by reference, same as everywhere else in this app). Duplicating
  a step within the same rotation is now Copy then Paste rather than one
  click on a single "Copy Selected" button.
- **Remove Selected** deletes every currently-selected step and/or condition
  at once, not just the first one.
- Keyboard shortcuts, active whenever the step list has focus: **Ctrl+C**
  (Copy), **Ctrl+V** (Paste), **Delete** (Remove Selected). These don't
  interfere with normal copy/paste/delete while typing in the Name/Key/etc.
  fields — they only fire while the list itself is focused, not the whole
  window.
- **Revert to Saved** discards every unsaved edit to whichever rotation is
  currently open in the form (steps, hotkeys, name, folder — everything),
  reloading it exactly as it was last saved (or resetting to blank for a
  rotation you haven't saved yet), after a confirmation prompt. A safety net
  now that drag-and-drop and multi-select make it easier to mess up a
  rotation by accident.

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
   jitter, optional repeat count — see "Repeat and Combine Hold" below), and
   choose **Once** (single pass) or **Loop** (repeats until
   re-triggered or the panic key is pressed). Jitter randomizes the delay ± that
   many ms each time; Hold Jitter does the same for how long the key is held
   down — both make timing look less like a perfectly repeating macro. Leave
   either at 0 for exact, fixed timing. The step's Name (e.g. "Fireball") is just
   a label for the steps list — it doesn't affect what gets sent, that's the Key
   field. If you need the same skill more than once in a rotation, select it and
   click **Copy** then **Paste** rather than re-entering it (and recalibrating
   its cooldown check, if it has one) from scratch — the copy is inserted right
   after the original and can be tweaked independently from there (this also
   works for copying several steps at once, and for pasting into a different
   rotation entirely — see "Multi-select, drag-and-drop, and clipboard" below).
   **Add Sleep** adds a
   step with no key at all — just a deliberate pause (Delay ± Jitter, same fields
   as any other step) with nothing pressed, for spacing out a rotation without
   tying the wait to any particular skill. It shows up in the list as "Sleep"
   unless you give it its own Name. You can also turn any existing step into a
   sleep by clearing its Key field and clicking Update Selected, or turn a sleep
   back into a real step by typing a key into it.

   **Add Step** with an empty Key field is different from a sleep step: it
   creates a step with no keybind *assigned yet*, shown as "(no key)" in the
   list (Name defaults to "Skill" so it's easy to find and rename). A step
   with no keybind assigned is skipped entirely when the rotation runs — no
   key pressed, no Delay waited out, straight to the next step — rather than
   pausing like a sleep step does. Type a key into it whenever you're ready
   and it behaves like any other step from then on. This only applies to
   **Add Step**; **Add Sleep** always creates a real, deliberate pause
   regardless of what's in the Key field.

   **Save Rotation** always
   applies whatever's currently in the step form to the selected step first, as
   if Update Selected had just been clicked — so editing a step's fields (or
   recalibrating its cooldown check) and going straight to Save Rotation is
   enough; Update Selected is only for applying an edit without saving yet.
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
4. **Reset Key** (optional) restarts this rotation from its first step if it's
   currently running — for when a fight phase changes, you misjudged a cast,
   or anything else where you want to bail back to the top of the sequence
   rather than either letting it continue from the middle or stopping it
   outright. Whatever step it was on, and however far into that step's timing
   it was, is abandoned immediately; if the rotation is in Loop mode it just
   starts the current lap over, and even in Once mode a reset restarts it
   rather than ending it. Same sharing rule as the cancel key (multiple
   rotations can use the same reset key), and the same restriction: it can't
   be the same as this rotation's own trigger hotkey or its own cancel key,
   since either would race a single keypress against itself.

   **Delay (ms)** next to it optionally waits that long *after* a reset before
   actually firing step 1 again (0, the default, restarts instantly, exactly
   as before) — for a brief "recovery" beat (e.g. matching a dodge-roll's
   animation) before the rotation picks back up. The wait is interruptible
   the same way everything else in this app is: Stop ends the rotation
   outright instead of restarting, Pause pauses through it (resuming, once
   unpaused, back at step 1), and pressing Reset again during the delay just
   restarts the countdown rather than stacking up.
5. **Pause Key** (optional) immediately freezes this rotation in place if it's
   currently running, then automatically resumes it — from the *same* step it
   was on (re-attempting that step's ready-check/fire from scratch, not
   skipping ahead or restarting the whole sequence). Choose **For [N] ms** to
   auto-resume after a fixed delay, or **Until pressed again** to stay frozen
   until you press the same key a second time. Same sharing rule as the
   cancel/reset keys, and the same restriction: it can't be the same as this
   rotation's own trigger hotkey, cancel key, or reset key.
6. Selecting a rotation in the list and clicking **Copy** duplicates it (steps,
   mode, and all) as a new unsaved rotation named "*name* (copy)" — the hotkey is
   left unbound since it can't share the original's, the cancel/reset/pause keys
   (if any) are carried over as-is since sharing those is fine, and
   template-based cooldown checks are carried over by reference (no
   recalibration needed). Rename it, assign a hotkey, and **Save Rotation**
   when ready.
7. Rotations only fire keystrokes while the configured game process has OS focus —
   switching away pauses a running rotation; switching back resumes it automatically.
8. **Stop Bot** stops any running rotation and disables all hotkeys (including the
   panic key and any cancel/reset/pause keys) at once; **Start Bot** re-enables
   them. While stopped, nothing can be triggered until you press Start Bot again.

Rotations are saved as one JSON file per rotation under `rotations/`, in whatever
subfolder structure their Folder field puts them in.
