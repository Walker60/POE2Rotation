#!/bin/sh
# Wrapper so Steam's "Add a Non-Steam Game" file picker has something to
# point at: it only offers a few known executable extensions (.exe, .sh,
# .AppImage, .application, ...), and this repo's own PyInstaller-built
# `poe2bot` binary is a plain, extensionless ELF executable (the normal
# Linux convention) -- it doesn't show up in that picker at all. See
# README's Steam Deck section for why launching through Steam matters in
# the first place (Steam Input only exposes the Deck's own built-in
# controls as a generic gamepad to a game/app it's actively running).
#
# `exec` (not a plain call) replaces this shell process with poe2bot's own,
# so Steam tracks the right PID and considers the game "closed" the moment
# poe2bot itself exits, rather than leaving this wrapper as an orphaned
# parent process.
cd "$(dirname "$0")" || exit 1
exec ./poe2bot "$@"
