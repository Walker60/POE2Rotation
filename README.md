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
(`sv-ttk`) for a modern, Windows-11-like dark/light look, plus its own themed
replacements for every confirm/error/input dialog so those match too. Open
**Settings...** (bottom-right) and click **Switch to Light/Dark Mode** to
switch — your choice is remembered across restarts. The Windows title bar
itself doesn't switch color on its own; that's a Windows limitation, not a bug.

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

### Controller output

A step can press a button on a virtual Xbox 360 controller instead of a keyboard key
(see "Controller-encoded steps" below). This works via `vgamepad`, which — as a side
effect of `pip install -r requirements.txt` — installs the ViGEmBus driver (a real
Windows kernel driver, not just a Python package). Expect one UAC/admin prompt during
that install; it's one-time and unrelated to whether the bot itself needs to run
elevated (see the keyboard-hook note above — that's a separate concern). The driver is
properly signed and works fine with Secure Boot and driver-signature enforcement both
enabled — no settings need to be changed for it. (If you go looking for "ViGEmBus"
online, note the upstream project renamed in 2023; functionally unaffected either way.)

## Configuration

- `POE2BOT_TARGET_PROCESS` — the game executable name the focus guard checks for
  (default `PathOfExileSteam.exe`). Verify the exact name via Task Manager > Details
  while POE2 is running — it may differ by storefront (Steam/EGS/standalone). Set
  this to `notepad.exe` to test the bot against Notepad instead of the game.
- `POE2BOT_PANIC_KEY` — reserved global hotkey that instantly stops every running
  rotation (default `f12`). Cannot be bound to a rotation.
- `POE2BOT_CONTROLLER_MIN_TAP_MS` — minimum press duration (ms) for a controller-encoded
  step with no Hold configured (default `40`). A virtual controller has no input queue
  the way a keyboard tap does, so an instant press+release risks the game's next input
  poll never seeing it; raise this if a controller step still doesn't reliably register.

## Skill Conditions (optional, per step)

Everything that gates whether a step fires, or changes how it fires, is a
**Condition** — a step can have any number of them. Each one is an
image/pixel/timer match plus an **Action** deciding what happens once it
matches:

- **Execute Step** — this step only fires while the condition matches.
  Every "Execute Step" condition on a step must currently match for it to
  fire at all (they're AND'd together).
- **Skip Step** — a veto: while this condition matches, the step is
  skipped this pass, regardless of any "Execute Step" conditions passing.
  Handy for "don't cast this while stunned/silenced" style checks.
- **Override Hold Time** — doesn't affect whether the step fires at all;
  while this condition matches, it overrides the step's Hold and/or Delay
  with its own **Hold override (ms)**/**Delay override (ms)** values instead
  (leaving either blank means that particular value is never overridden). For
  skills whose animation time changes while a buff is up (faster/slower
  attack or cast speed) without needing to touch the step's own normal
  timing. Read once per cast and used for both that cast's hold *and* its
  following delay, so if the underlying match stops holding partway through
  the post-cast delay, that delay still finishes out at the overridden value
  rather than switching mid-wait.

A condition can additionally be **Negate**d, which inverts whichever match it
uses (e.g. a "Fire" condition with Negate fires only while its image/pixel is
*absent*) — this applies uniformly regardless of Action.

Three ways to calibrate what a condition actually matches against:

- **Add Image Condition...** — the window hides, drag a small rectangle
  tightly around an icon on screen, then confirm the capture. Best when the
  icon's whole appearance (shape, highlight, etc.) changes between its two
  states.
- **Add Pixel Condition...** — the window hides, click exactly on a single
  pixel that's a distinct, reliable color in one state (e.g. a bright border
  pixel on an icon), then confirm the sampled color. Cheaper than an image
  match and handy when a whole-icon capture isn't needed.
- **Add Timer Condition...** — no screen capture at all, just a number of
  seconds since this step's own last actual fire. Useful for a plain cooldown
  gate that has no reliable on-screen indicator to check instead.

A newly added condition defaults to **Execute Step** with no wait — exactly
an always-instant gate. Select it (it's auto-selected right after adding) to
change its Action, Name, Negate, or the fields below, then click **Update
Selected Condition** to apply.

**An "Execute Step" condition can optionally wait instead of failing
instantly.** Set **Wait up to (ms)** above 0 and, if the condition doesn't
match yet, the bot polls it for up to that long (checking a few times a
second) before giving up on this pass and skipping the cast — this is what
used to be a separate "Cooldown Check" concept, now just any Execute Step
condition with a wait configured. Leaving it at 0 (the default) is an
instant, one-shot check with no waiting, suited to things like "is this buff
currently active" rather than "wait for this skill to come off cooldown."
The bot blocks the *entire* rotation for up to the wait time on that one
step, so a large value makes a fast rotation feel like it stalls on
whichever skill is still cooling down — it's not hung, it's waiting out the
timeout before moving on. "Skip Step" and "Override Hold Time" conditions
are always instant, one-shot checks; only "Execute Step" ever waits.

**Image matching defaults to a direct comparison against exactly the
calibrated spot, not a search.** In this default "exact" mode, a check takes a
screenshot of precisely the region you drew during calibration and compares it
directly against the saved template (mean pixel difference, scaled by the
Confidence field — higher Confidence demands a closer match). A pixel-match
check reads the single calibrated pixel and compares its color to the saved
color (Euclidean RGB distance, scaled by the same Confidence field), which is
cheaper still since there's no image to decode or diff. Neither mode searches
elsewhere on screen for the icon by default — this is deliberately cheap: no
sliding-window matching, so check speed doesn't scale with region size, and a
single check can no longer blow past a very low wait timeout on its own. The
trade-off is that both assume the icon/pixel is still exactly where it was
when calibrated — if the game window moves, resizes, or the UI scale changes
afterward, the check will stop matching (never crash, it'll just always read
as "not matching") and that condition needs recalibrating.

**Image matching can optionally search a larger area instead.** After
confirming the tight icon capture, check "Search a larger area" and click-drag
a second, larger rectangle — the check then searches anywhere within that
rectangle for the icon (via OpenCV template matching) rather than comparing
only the one exact spot. Use this for an icon that can visibly shift position
slightly (e.g. a UI panel that reflows when other buffs appear/disappear)
instead of recalibrating every time it moves. This costs more per check than
exact mode, scaling with how large the search area is, so keep it as tight as
you reasonably can — don't reach for it as a default. It's also a different
matching algorithm than exact mode (normalized cross-correlation, not mean
pixel difference), so a Confidence value tuned for exact mode is only a
starting point after switching to area mode; expect to retune it.

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
matching `templates/*.png` file(s) too, or that condition will simply log an
error and read as not-matching (never crash) until recalibrated. Files no
longer referenced by any saved or in-progress rotation are cleaned up
automatically on save, delete, and app startup. Pixel-match and timer
calibrations don't need any of this — their values are just numbers stored
directly in the rotation's JSON, so they're already portable with no matching
file to copy.

Known limitation: calibration only supports the primary monitor.

The step list shows conditions as indented rows nested under their step, with
an expand/collapse arrow — a step with any conditions starts expanded so
you'll always see one right after adding it, though a manual collapse doesn't
persist across further edits to the step list.

- **Double-click** an existing condition to recalibrate its match (region or
  pixel, matching whichever type it already is) — its Confidence field starts
  pre-filled with its current value, and its Name/Action/Negate/Wait/Hold/
  Delay are all carried over unchanged; recalibrating only ever changes
  *what's being matched*, never what happens when it matches.
- Select a condition and click **Remove Selected** to delete just that
  condition, leaving the step and its other conditions untouched.
- Select a condition, type into the **Name** field, and click **Update
  Selected Condition** to give it a label (e.g. "Bleeding") — it's shown in
  the list instead of the auto-generated "Pixel RGB(...)"/"Image WxH"
  description, purely cosmetic, and survives recalibration.
- Select a condition and click **Move Up**/**Move Down** (in Skill Steps), or
  just drag it, to reorder it within its own step. Reordering "Execute
  Step"/"Skip Step" conditions is cosmetic only (they're still combined the
  same way regardless of order); with more than one "Override Hold Time"
  condition on the same step, the first one (in list order) that currently
  matches is the one whose override applies.

Copying a step (Copy/Paste, or copying a whole rotation) carries its
conditions along with it. Conditions with an image-match template participate
in the same template-file portability/cleanup rules described above.

## Condition Groups (optional, rotation-level)

A **Condition Group** is the rotation-level counterpart to a step's own
Conditions above: instead of gating one step, it gates a whole block of
steps nested under it at once. Handy for a burst combo that should only run
in its entirety while some buff/debuff is up, without adding the same
condition to every step in that combo individually.

A group holds exactly one condition (an image or pixel match — never a
Timer condition, and never an AND'd list the way a step's Conditions can be)
and an **Action**:

- **Execute Group** — every step nested in the group only runs while the
  condition matches; while it doesn't, the whole group is skipped this pass
  (no fire, no delay, for any step nested in it) and the rotation moves on
  to whatever comes after the group.
- **Skip Group** — a veto: while the condition matches, the whole group is
  skipped this pass, regardless of Execute Group. Handy for "don't run this
  combo while stunned/silenced."

There's no Override Hold Time option and no Wait-up-to-ms polling at the
group level (both stay per-step, on a step's own Conditions) — a group's
condition is always a single, instant check. Groups never nest inside each
other; a group holds steps directly.

**Add Condition Group (Image)...**/**Add Condition Group (Pixel)...** (in
the **Rotation Conditions** section) calibrate the group's condition
exactly like adding a step's own Image/Pixel Condition does, then add a
new, empty group to the end of the step list — select it afterward to set
its Name/Action/Negate right there in Rotation Conditions and click
**Update Selected Condition Group** to apply. Unlike Selected Step/Skill
Conditions (which hide while a group's own row is selected, since a group
has no Key/Delay/Hold/Repeat/per-step Conditions of its own), Rotation
Conditions stays visible no matter what's selected — its Name/Action/Negate
fields just blank out until a group is actually selected. Double-click a
group's row to recalibrate its match, the same as double-clicking a step's
condition.

Steps end up nested under a group two ways: select the group (or one of its
own nested steps/conditions) and click **Add Step**/**Add Sleep**, which
appends into that group instead of the top level; or drag an existing step
onto the group's row to move it in — dragging a nested step out to the top
level, or into a different group, works the same way in reverse. Move
Up/Move Down and a plain drag reorder a nested step within its own group,
or a group itself among the rotation's other top-level steps/groups, the
same way Move Up/Move Down and dragging already work for a plain step.
Removing a non-empty group (Remove Selected) asks for confirmation first,
since it deletes every step nested inside it along with the group.

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

Repeat only fires once through the step's own Conditions — they aren't
re-checked between reps, including any "Override Hold Time" condition,
which is captured once (before the first rep) and used for all of them.

## Multi-select, drag-and-drop, and clipboard

The step list supports multi-select (ctrl/shift-click, same as the rotation
list on the left) and drag-and-drop, on top of the buttons described above:

- **Drag** one or more selected rows to reorder them — drag a step (or
  several multi-selected steps sharing the same current group, or lack of
  one) to reposition it, including onto a Condition Group's row (or a step
  already nested in one) to move it into/out of/between groups; drag a
  Condition Group (or several) to reposition it among the rotation's other
  top-level steps/groups — groups never nest, so a group drag only ever
  lands at the top level; drag a condition (or several, multi-selected) to
  reposition it within its own step. A highlighted row shows where it'll
  land as you drag. Dragging a mix of groups/steps/conditions together, or
  conditions from more than one step at once, isn't supported — nothing
  happens rather than doing something surprising. Move Up/Move Down still
  work as a click-based alternative, moving a step within its own group (or
  a group within the top level) the same way dragging does.
- **Copy** copies every currently-selected step (with its conditions) to an
  in-memory clipboard; **Paste** inserts a copy of the clipboard's contents
  after whichever step/condition is selected (or at the end, if nothing is).
  The clipboard isn't tied to the rotation you copied from — copy some steps,
  switch to a *different* rotation in the left-hand list, and Paste to bring
  them over, conditions included (image-match templates are shared by
  reference, same as everywhere else in this app). Duplicating
  a step within the same rotation is now Copy then Paste rather than one
  click on a single "Copy Selected" button.
- **Remove Selected** deletes every currently-selected step and/or condition
  at once, not just the first one.
- Keyboard shortcuts, active whenever the step list has focus: **Ctrl+C**
  (Copy), **Ctrl+V** (Paste), **Delete** (Remove Selected). These don't
  interfere with normal copy/paste/delete while typing in the Name/Key/etc.
  fields — they only fire while the list itself is focused, not the whole
  window.

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
   its conditions, if it has any) from scratch — the copy is inserted right
   after the original and can be tweaked independently from there (this also
   works for copying several steps at once, and for pasting into a different
   rotation entirely — see "Multi-select, drag-and-drop, and clipboard" below).
   **Add Sleep** adds a
   step with no key at all — just a deliberate pause (Delay ± Jitter, same fields
   as any other step) with nothing pressed, for spacing out a rotation without
   tying the wait to any particular skill. It shows up in the list as "Sleep"
   unless you give it its own Name. You can also turn any existing step into a
   sleep by clearing its Key field and selecting a different step (or letting
   it autosave, see below) to commit the change, or turn a sleep back into a
   real step by typing a key into it.

   **Disable Step**/**Enable Step** (in Skill Steps, next to Remove Selected)
   toggles whether the currently-selected step ever runs at all — a disabled
   step always shows grayed out in the list and is unconditionally skipped
   when the rotation runs (no fire, no delay, its own Conditions aren't even
   checked), regardless of its Key or anything else about it. Handy for
   temporarily pulling a step out of a rotation without deleting it and
   losing its calibrated conditions. The button is only enabled while
   exactly one step (or one of its conditions) is selected — nothing to
   toggle with a condition group's own row selected, or with nothing/several
   things selected.

   **Add Step** with an empty Key field is different from a sleep step: it
   creates a step with no keybind *assigned yet*, shown as "(no key)" in the
   list (Name defaults to "Skill" so it's easy to find and rename). A step
   with no keybind assigned is skipped entirely when the rotation runs — no
   key pressed, no Delay waited out, straight to the next step — rather than
   pausing like a sleep step does. Type a key into it whenever you're ready
   and it behaves like any other step from then on. This only applies to
   **Add Step**; **Add Sleep** always creates a real, deliberate pause
   regardless of what's in the Key field.

   There's no Save button anywhere in this app — every edit (a field, adding/
   removing/reordering a step, a condition, a hotkey binding, renaming the
   rotation, all of it) is written to disk the instant you make it. A field
   that doesn't currently parse (e.g. a non-numeric Delay, or a blank one)
   just isn't saved yet — the field gets a red border and the exact problem
   is named right there in the form — and resumes saving normally the moment
   it's valid again; nothing invalid ever reaches disk.
2. Click **Bind Hotkey...** and either press a keyboard key or click a mouse button
   to trigger this rotation — it saves the instant you press/click it. Left/
   middle/right click and the two extra side buttons (mouse 4/5) are all supported.

   **Caution:** binding left or right click makes that button trigger the rotation
   *everywhere*, not just in-game — every left-click in Windows Explorer, every
   right-click context menu, etc. Middle-click or a side button (mouse 4/5) is
   almost always the safer choice unless you're certain you want that trade-off.

   **Unbind** clears this rotation's hotkey and saves immediately — use it when you
   want to move a hotkey to a different rotation: Unbind it here first to free the
   key up, then Bind Hotkey it on the other rotation.
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
   mode, and all) as a new rotation named "*name* (copy)", saved to disk
   immediately — the hotkey is left unbound since it can't share the
   original's, the cancel/reset/pause keys (if any) are carried over as-is
   since sharing those is fine, and template-based conditions are carried
   over by reference (no recalibration needed). Rename it and assign a
   hotkey whenever you're ready.
7. Rotations only fire keystrokes while the configured game process has OS focus —
   switching away pauses a running rotation; switching back resumes it automatically.
8. **Stop Bot** stops any running rotation and disables all hotkeys (including the
   panic key and any cancel/reset/pause keys) at once; **Start Bot** re-enables
   them. While stopped, nothing can be triggered until you press Start Bot again.

Rotations are saved as one JSON file per rotation under `rotations/`, in whatever
subfolder structure their Folder field puts them in.
