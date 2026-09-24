#!/bin/sh
# Fallback launcher for when the non-sudo setup (packaging/
# setup-steamdeck-permissions.sh + the ensure_root() patch in
# poe2bot/hotkeys.py) still isn't enough on your particular setup --
# just runs poe2bot under sudo instead, prompting for your password every
# time. Right-click this file in Dolphin and choose "Run in Konsole" (it
# needs a real terminal to show the password prompt in -- a plain
# double-click with no terminal attached fails with "no tty present").
#
# DISPLAY/XAUTHORITY are captured from your own (non-root) session before
# sudo switches to root, and handed to poe2bot explicitly -- otherwise it
# can't connect to your X11 session at all as root (a bare `sudo poe2bot`
# looks for a root-owned Xauthority that doesn't exist, and fails to even
# open a window).

DIR="$(cd "$(dirname "$0")" && pwd)"
XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
cd "$DIR" || exit 1
exec sudo env DISPLAY="$DISPLAY" XAUTHORITY="$XAUTH" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" "$DIR/poe2bot" "$@"
