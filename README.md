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

## Pre-built Windows executable and auto-update

Besides running from source (`python main.py` above), every push to this repo's
`main`/`steam-deck-linux-support` branches also runs
[`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml), which
builds a self-contained Windows bundle (a full Python + Tcl/Tk + every dependency, via
PyInstaller — see `packaging/windows.spec`) needing nothing else installed on the
target machine. Grab it once from the workflow's Actions artifact
(`poe2bot-windows-x86_64`) or the `windows-latest` GitHub Release, extract it anywhere,
and run the `poe2bot.exe` inside it directly.

After that, use **Settings → Check for Updates** inside the app instead of downloading
by hand again: it checks the same rolling release, and if the git commit SHA it was
built from differs from the one currently running, downloads and installs the new
build in place, then offers to restart into it. This mirrors the Steam Deck build's own
self-update mechanism below, with one Windows-specific wrinkle: a running .exe's own
folder can't reliably be renamed/replaced out from under itself the way it can on
Linux, so clicking "restart now" hands the actual swap off to a short-lived helper
script that waits for the app to fully close first, rather than swapping immediately
the way the Linux build does — functionally the same end result, just deferred a few
seconds until after the app exits.

This is a first build pipeline (and this Windows self-update mechanism specifically is
newer than the Linux one it's modeled on), not a polished release channel — see the
Steam Deck section right below for the fuller story on what "rolling release" and
"version" mean here, both of which apply identically to the Windows build.

## Steam Deck / Linux (experimental)

The app also has a Linux backend (window detection via X11/EWMH, real-controller
input via `evdev`) intended for running natively on a Steam Deck in **Desktop
Mode**, alongside Path of Exile 2 running as usual via Proton — the bot itself
does **not** run under Wine/Proton, just as an ordinary Linux process. This is
new and **unverified against real hardware** — treat it as a first draft to
debug on an actual Deck, not a finished, tested feature. If something doesn't
work, check the specific caveats called out in `poe2bot/focus.py` and
`poe2bot/controller_input.py`'s module docstrings first.

### Getting it onto the Deck

**Option A (recommended): the pre-built bundle.** Every push to this repo's
`main`/`steam-deck-linux-support` branches runs
[`.github/workflows/build-steamdeck.yml`](.github/workflows/build-steamdeck.yml),
which builds a self-contained folder (a full Python + Tcl/Tk + every
dependency, via PyInstaller — see `packaging/linux.spec`) on a Linux runner.
**The very first time**, download it from the workflow's Actions artifact
(`poe2bot-steamdeck-linux-x86_64.tar.gz` — requires being logged into GitHub
in a browser, since Actions artifacts have no public download link), extract
it anywhere on the Deck, and run the `poe2bot` executable inside it directly
— no `pip`, `pacman`, or Distrobox needed just to get Python/the dependencies
in place. **After that**, use **Settings → Check for Updates** inside the
app itself instead of repeating this by hand: it checks a rolling GitHub
Release the same workflow publishes (which, unlike a plain Actions artifact,
has a permanent public download link) and, if the SHA it was built from
differs from the one currently running, downloads and installs the new
build in place, then offers to restart into it — no browser, no manual
download/extract needed for every iteration. This is a first build pipeline
(and a first self-update mechanism), not yet confirmed to actually run
correctly on real Deck hardware — see the rest of this section for what
still has to be true regardless of how you got the app onto the Deck, and
report back what breaks.

**Option B: install everything yourself**, e.g. inside
[Distrobox](https://github.com/89luca89/distrobox) (a container that doesn't
require Developer Mode's permanent read-write unlock, recommended since
SteamOS's own root filesystem is read-only) — install Python 3 + `tk`
inside it, then `pip install -r requirements.txt` (`python-xlib` and
`evdev`, the two Linux-only additions, are pulled in automatically via
environment markers; they're never installed on Windows).

### What has to be true either way

- **An X11 session**, not Wayland: `steamos-session-select plasma-x11-persistent`.
  `keyboard`/`mouse`/`mss` (hotkey capture, input injection, screen/pixel capture)
  have no Wayland support at all, on any platform, so this isn't optional.
  (SteamOS has moved to Wayland-by-default for Desktop Mode; the X11 session is
  still selectable as of this writing, but Valve's long-term direction is Wayland
  — if a future SteamOS update drops the X11 desktop session, this whole approach
  needs revisiting.)
- **Permission to read/write `/dev/uinput` (virtual controller output) and
  `/dev/input/*` (real controller input) without root** — add your user to the
  `input`/`uinput` groups (or an equivalent udev rule); `vgamepad`'s own Linux
  docs describe the exact steps. If running inside a container (Option B, or
  if you choose to run Option A's bundle inside one too), it also needs device
  passthrough for both of those.

**Virtual-controller output** (a step that presses a button on the emulated
controller — see "Controller output" above): `vgamepad`, the same library
`poe2bot/controller.py` already uses on Windows, ships its own native Linux
backend built on the kernel's `uinput`/`evdev` subsystem — no ViGEmBus, no Wine
involved, no code changes needed here. This is the single biggest open
question in the whole Linux port: confirm a virtual pad created this way
actually shows up as a controller to Proton-hosted PoE2 (not just to the host
OS) before relying on it — there are reports of Proton not always recognizing
a `uinput`-created pad. If it doesn't work with stock Proton, a community
"GE" Proton build (which carries extra controller-support patches) may help.

Recalibrate every rotation's image/pixel conditions fresh on the Deck's own
display — a template captured on a Windows PC's screen won't match the Deck's
rendering, the same as moving between any two different screens today.

**The Deck's own built-in controls (buttons/sticks/trackpads) won't show up
for a physical-press capture (e.g. a rotation's "Bind Hotkey...") out of the
box.** They're normally owned by **Steam Input**, which only creates a
generic virtual gamepad for a game Steam is actively running/targeting — a
plain terminal-launched app in Desktop Mode never gets one, so `evdev`
genuinely has nothing gamepad-like to find (this isn't a bug; if it happens,
poe2bot's log will list every `/dev/input` device it actually saw, none of
them recognized as a gamepad — check `logs/poe2bot.log` for that dump if you
want to confirm this is what's happening on your setup before trying the fix
below). This also means the Deck's own controls can't actually *trigger* a
rotation while playing until one of the two fixes below is in place, even
after a hotkey is bound to one of their buttons. Two ways to make the Deck's
own controls visible as a real evdev gamepad:

- **Add poe2bot as a non-Steam shortcut and launch it through Steam** (Desktop
  Mode → Steam → Games → "Add a Non-Steam Game" → point it at the `poe2bot`
  executable) — launching it this way, rather than directly from a terminal,
  is what gets Steam Input to engage and expose a virtual Xbox 360 controller
  the same way it does for an actual game; `evdev` should then find it like
  any other gamepad. Unconfirmed against real hardware yet — if Steam Input
  doesn't engage for a plain utility app this way, or the virtual pad it
  creates isn't visible system-wide to a process Steam didn't itself launch,
  this needs another approach.
- **[Handheld Daemon (HHD)](https://github.com/hhd-dev/hhd)**, a community
  project that exposes the Deck's built-in controls as a normal evdev gamepad
  independent of Steam/Steam Input entirely — the more robust option if the
  above doesn't pan out, at the cost of installing another piece of software.

Either way, a real USB/Bluetooth controller plugged into the Deck doesn't
have this problem at all — it shows up as a normal gamepad to `evdev`
regardless of Steam.

Both **choosing which button a step should press** (the step editor's **Map
Controller Button**) and **entering which button should trigger/cancel/
reset/pause a rotation** (each hotkey row's own **Map...**, next to its
"Bind...") work no matter what `evdev` can currently see — click a button's
name from a list instead of physically pressing it. Pick **Settings...
> Controller > Controller type: Steam Deck** first so that list shows
Deck-style labels (View/Menu, L1/R1/L2/R2) instead of Xbox ones (Back/Start,
LB/RB/LT/RT) — purely cosmetic, since every button still presses (or is
compared against) the same spot either way.

For a step's Key field, that's the whole story — nothing else needs to see
the Deck's own controls at all, since it's the bot's own *virtual* controller
being pressed. For a hotkey/cancel/reset/pause bind, Map... only fills in
the field's *value*; the Deck's own buttons still can't *trigger* it while
you're actually playing until `evdev` can see them for real, i.e. until one
of the two fixes above is in place — Map... just means you no longer need a
working physical capture merely to set the binding up ahead of time.

## Configuration

Every setting below can also be viewed and changed from **Settings... > Advanced**
without restarting the bot — the environment variable only supplies the very first
default, before you've ever changed it in Settings. Once you click Save there, the
new value is remembered in `app_state.json` and takes effect immediately.

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
- `POE2BOT_CONTROLLER_INDEX` — which XInput slot (0-3) is your real, physically-held
  controller (default `0`). Change this if a real controller plugged in alongside the
  bot's own virtual one ends up enumerated on a different slot.
- `POE2BOT_REQUIRE_GAME_FOCUS` — whether a rotation must wait for the game window to
  have OS focus before it'll fire at all (default `1`/on). Set to `0` to fire
  regardless of what's focused — only for testing against a target that never takes
  OS focus itself, or if the focus check itself is unreliable on your setup; leaving
  this on is what stops a cast from firing into whatever window you're actually
  looking at instead of the game. Unlike the four settings above, this one lives at
  **Settings... > Safety** (a plain checkbox, applied immediately), not **Advanced**.

**Settings... > Windows** also has **Show Hotkey Map...** (a snapshot table of every
rotation's Hotkey/Cancel/Reset/Pause keys across every folder, flagging any key used
more than once anywhere — same-folder sharing is already flagged when you bind a new
hotkey, but this catches a conflict introduced by editing a Folder field, or two
rotations in different folders independently choosing the same key) and **Open Logs
Folder** (the rotating `poe2bot.log`/`poe2bot.log.1`/etc. files, for reviewing a past
session after closing the app).

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

With no condition selected (or a step's own row selected instead), all three
add a brand new condition. **With an existing condition selected, these same
three buttons instead recalibrate *that* condition's match** — exactly like
double-clicking it — rather than adding another one; clicking a different one
of the three (e.g. Add Image Condition on a currently pixel-based condition)
converts it to that type. Either way, Name/Action/Negate/Wait/Hold/Delay
carry over unchanged, since recalibrating (by button or double-click) only
ever changes *what's being matched*, never what happens when it matches.

A newly added condition defaults to **Execute Step** with no wait — exactly
an always-instant gate. Select it (it's auto-selected right after adding) to
change its Action, Name, Negate, or the fields below — there's no separate
apply step, every change saves as you make it.

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
when calibrated — see "Moving a rotation to a different screen" below for what
happens (and what doesn't need recalibrating) when the game window's own size
changes.

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

**Screenshots go through `mss`, not `pyautogui`/Pillow's `ImageGrab`** — for
both runtime condition checks and the calibration flow itself. Pillow's own
`ImageGrab.grab()` (what `pyautogui.screenshot()` uses underneath) always
captures the *entire* screen internally and crops it down afterward, no
matter how small the requested region is — for a check running many times a
second, that's a real, avoidable cost that scales with your monitor's
resolution; `mss` captures only the requested rectangle directly, so this is
what actually makes repeated checks fast rather than just a smaller region or
a cheaper threshold. It's also the only one of the two that works everywhere
this app runs at all: Pillow's Linux `ImageGrab` backend shells out to an
external `gnome-screenshot` binary, which doesn't exist on KDE Plasma (Steam
Deck Desktop Mode) — `mss` needs nothing beyond the X11 connection it already
requires.

## Moving a rotation to a different screen

A rotation calibrated on one monitor/computer keeps working after moving to a
different one at a different resolution — including a different aspect ratio
(e.g. 16:9 to ultrawide) — with no separate rotation to maintain and no manual
step. Every image/pixel condition quietly records the game window's client
rect (size *and* on-screen position) at the moment it's calibrated; if a later
check finds the game window is a different size, in a different position (e.g.
it's now on a different monitor), or both, it rescales that condition's
calibrated point/region (and the saved template's own size, for an image
condition) from the rect it was calibrated at to the current one before
checking, live, every time. Two conditions calibrated on different screens at
different times each rescale from their own reference rect correctly — this
is per-condition, not a single rotation-wide setting.

The rescale isn't a blind stretch from the top-left corner: each point keeps
its position relative to whichever screen edge (or the center) it was closest
to when calibrated — the same "anchor" model most game HUDs are actually built
on (health orb anchored to the bottom-left, flask bar anchored to bottom-*center*,
minimap to the top-right, and so on), and the same one UI frameworks like
Unity/Unreal use for exactly this "what should a UI element do when its canvas
resizes" problem. A resolution change with the *same* aspect ratio (1080p to
1440p to 4K) reduces to a plain proportional scale under this model, so that
common case works exactly as you'd expect; a resolution change that also
changes the aspect ratio additionally keeps each element anchored to its own
edge/corner rather than stretching it across whatever space the new aspect
ratio added or removed.

This is a best-effort transform, not a guarantee — it depends on the game's
HUD actually being built on that same anchor-per-element assumption, which
holds for the vast majority of ARPG UIs but isn't verified against POE2
specifically inside this tool. If a condition doesn't behave as expected after
moving to a new screen, **Test Match** shows exactly what it's comparing right
now, including a "Rescaled from WxH to WxH" (or, if just the window's position
changed, "Repositioned: game window moved from (...) to (...)") note whenever
a rescale actually applied, so you can tell at a glance whether the transform
or something else (e.g. a UI scale setting that also changed) is the actual
problem — recalibrate that one condition if so, the same as always.

A condition calibrated before this existed (or one only ever edited by hand)
has no recorded reference size, so nothing about it is rescaled — exactly its
pre-existing behavior. Recalibrating it (or double-clicking to redo its match)
records one going forward.

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

Calibration spans every connected monitor, not just the primary one -- the
capture overlay covers the full virtual desktop, so an icon on a secondary
display (including one positioned above/left of the primary) can be captured
the same way as one on the primary monitor.

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
- Select a condition and click **Test Match** to check, right now, whether it
  currently matches — a small popup shows the saved template (image conditions)
  or a saved-vs-live color swatch pair (pixel conditions) next to a MATCH/NO MATCH
  verdict (already reflecting Negate, if it's on). Useful for confirming a
  calibration is still good without running the whole rotation — e.g. after the
  game window moves/resizes or the UI scale changes, which is exactly when a
  match can silently start failing (see the exact/area-mode notes above). Not
  meaningful for a Timer condition (there's no "since this step's last fire" to
  measure without a rotation actually running) — Test Match tells you so instead
  of guessing.

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
condition is always a single, instant check. A group can itself hold another
group nested inside it (up to 5 levels deep) — each level's own gate must
pass for anything inside it to run, so a group nested inside another
effectively ANDs its own condition with every ancestor group's condition
above it, without re-entering the same check on every inner step.

**Add Condition Group (Image)...**/**Add Condition Group (Pixel)...** (in
the **Rotation Conditions** section), with no group selected, calibrate a
new condition exactly like adding a step's own Image/Pixel Condition does
and add a new, empty group to the end of the step list — select it
afterward to set its Name/Action/Negate right there in Rotation Conditions,
which saves as you type, no separate apply step. **With an existing group's
own row already selected, these same two buttons instead recalibrate that
group's match** — converting it to Image or Pixel as needed — rather than
adding another group; Name/Action/Negate carry over unchanged. Unlike
Selected Step/Skill Conditions (which hide while a group's own row is
selected, since a group has no Key/Delay/Hold/Repeat/per-step Conditions of
its own), Rotation Conditions stays visible no matter what's selected — its
Name/Action/Negate fields just blank out until a group is actually
selected. Double-clicking a group's row also recalibrates it, the same as
double-clicking a step's condition, always keeping its current match type.
**Test Match** (next to the Add Condition Group buttons) checks a selected group's
condition the same way it does for a step's own condition — see Skill Conditions
above.

Steps end up nested under a group two ways: select the group (or one of its
own nested steps/conditions) and click **Add Step**/**Add Sleep**, which
appends into that group instead of the top level; or drag an existing step
onto the group's row to move it in — dragging a nested step onto a plain
step's row instead repositions it as a sibling of that step, wherever it
lives, which is how a nested step gets pulled back out to the top level or
into a different group. A group itself is nested inside another group only
by dragging it onto the *middle* of that other group's own row — there's
no "Add Nested Group" button; the Add Condition Group buttons always add a
brand-new group at the top level, exactly as above. Dragging a group onto
that same row's top or bottom edge instead repositions it as a plain
sibling of that group, which is how a nested group gets pulled back out to
a shallower level (including the top level) — handy since hovering a
group's row otherwise always means nesting into it, even when it's the
only row there is to drop next to. Move Up/Move Down and a plain drag
reorder a nested step within its own group, or a group itself among
whichever list it currently lives in (the top level, or another group's
own nested entries), the same way Move
Up/Move Down and dragging already work for a plain step. Removing a
non-empty group (Remove Selected) asks for confirmation first, since it
deletes everything nested inside it — steps and any further-nested groups,
at any depth — along with the group.

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
  one) onto a Condition Group's row to move it into that group, or onto a
  plain step's row to reposition it as a sibling of that step (wherever it
  lives — the top level, or any group); drag a Condition Group (or several
  sharing the same current parent) onto the *middle* of another group's row
  to nest it inside that group instead, or onto that same row's top/bottom
  edge to reposition it as a plain sibling of that group instead — this
  sibling-edge drop is what lets a group (or a step inside one) be dragged
  back out to a shallower level, all the way to the top level if you want,
  even when the only row available to hover is a single group with nothing
  else next to it to drop beside. Dropping a group onto itself or one of
  its own current descendants is refused (it would create a cycle), and
  dropping past 5 levels of nesting is refused too. Drag a condition (or
  several, multi-selected) to reposition it within its own step. A
  highlighted row shows where it'll land as you drag. Dragging a mix of
  groups/steps/conditions together, or conditions from more than one
  step at once, isn't supported — nothing happens rather than doing
  something surprising. Move Up/Move Down still work as a click-based
  alternative, moving a step or a group within whichever list it currently
  lives in, the same way dragging does.
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

## Exporting, importing, and recovering rotations

**Export...**/**Import...** (below New/Copy/Delete) share a rotation as a single
`.zip` file, without you having to separately hunt down and copy its matching
image-match template PNGs by hand — Export bundles the rotation's JSON together
with every template it actually references (nothing to do for a rotation with only
pixel/timer conditions, or none at all); Import reads that bundle back, writing each
template under a brand-new filename so it can never collide with (or silently
overwrite) anything already in your `templates/` folder. An imported rotation always
lands ungrouped, with a unique name (`" 2"`, `" 3"`, ... appended if there's already
one with that name) and its Hotkey/Cancel/Reset/Pause keys all cleared — another
person's keybinds mean nothing on your machine, and reusing them by accident risks
silently colliding with one of your own rotations. Bind its keys the same way as any
new rotation once it's imported.

**Right-click anywhere in the rotation list → Restore Last Deleted...** undoes the
most recent **Delete** — there's no Save button anywhere in this app (see below), so
this is the one safety net against a fat-fingered delete. It only remembers a single
step back: deleting a second rotation permanently discards whatever was in that slot
before it, so restore it before you delete anything else if you want it back. The
menu item is disabled whenever there's nothing to restore.

## Usage

1. Click **New**, give the rotation a name and, optionally, a Folder to group it
   under, add steps (optional display name,
   key, delay in ms, optional jitter, optional hold duration, optional hold
   jitter, optional repeat count — see "Repeat and Combine Hold" below), and
   choose **Once** (single pass) or **Loop** (repeats until
   re-triggered or the panic key is pressed) — a Loop rotation logs a "Lap N complete
   (X.Xs)" line to the Activity window at the end of every full pass, so its actual
   cycle time is visible without timing it by hand. Jitter randomizes the delay ± that
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

   The **Enabled** checkbox (next to Mode) is the same idea one level up: unchecking
   it releases this whole rotation's Hotkey/Cancel/Reset/Pause keys (freeing them up
   for another rotation to use) without unbinding them — check it again and they all
   come back exactly as they were, no retyping. Unlike Active Folder scoping, this is
   per-rotation and has nothing to do with which folder is active.

   **Test Run** (top-right of the form) fires this rotation immediately, the same way
   pressing its real trigger hotkey would — including toggling a running Loop
   rotation off again on a second click — without needing to bind a hotkey first, and
   regardless of Active Folder scope or the Enabled checkbox above. It autosaves
   first, so what's on screen right now is exactly what runs; if the form doesn't
   currently save cleanly, the usual inline error explains why instead of firing
   something stale. Opens the Activity window automatically so you can watch it go.

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
