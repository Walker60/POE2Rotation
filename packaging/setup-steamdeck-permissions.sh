#!/bin/sh
# One-time setup so poe2bot never needs sudo again on this Steam Deck.
#
# Without this, /dev/uinput (virtual controller output, via vgamepad) and
# /dev/input/* (reading a real controller, via evdev) are root-only by
# default -- see the "Permission to read/write /dev/uinput... without root"
# note in README's Steam Deck section, and the same "needs to be in the
# 'input' group" warning poe2bot's own log prints if this hasn't been done.
# That's why launching poe2bot at all currently means typing your sudo
# password every time (e.g. `sudo ./poe2bot.sh`).
#
# This installs a udev rule granting the 'input' group read/write access to
# /dev/uinput and adds your user to that group -- the fix vgamepad's own
# Linux docs recommend. Run it ONCE:
#
#   sh setup-steamdeck-permissions.sh
#
# Then log out and back in (or reboot) -- group membership only takes
# effect on your next login -- and launch poe2bot / poe2bot.sh directly
# from then on, with no sudo prompt at all.
#
# SteamOS specifically: its root filesystem (which /etc/udev/rules.d lives
# on) is mounted read-only by default, so even root can't write to it until
# that's temporarily lifted -- this script does that itself via
# `steamos-readonly disable`/`enable` around just the one file write below,
# so you don't need to run those by hand. `steamos-readonly` doesn't exist
# on a non-SteamOS Linux box, so this is skipped there automatically.

set -e

RULE_FILE=/etc/udev/rules.d/99-poe2bot-uinput.rules
RULE_CONTENT='SUBSYSTEM=="misc", KERNEL=="uinput", GROUP="input", MODE="0660"'

echo "This asks for your sudo password once, to install a udev rule and add"
echo "$USER to the 'input' group. After this, poe2bot never needs sudo again."
echo

HAS_READONLY_TOGGLE=0
if command -v steamos-readonly > /dev/null 2>&1; then
    HAS_READONLY_TOGGLE=1
    echo "SteamOS detected -- temporarily disabling the read-only root filesystem..."
    sudo steamos-readonly disable
fi

write_ok=1
echo "$RULE_CONTENT" | sudo tee "$RULE_FILE" > /dev/null || write_ok=0
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG input "$USER"

if [ "$HAS_READONLY_TOGGLE" = "1" ]; then
    echo "Re-enabling the read-only root filesystem..."
    sudo steamos-readonly enable
fi

if [ "$write_ok" != "1" ]; then
    echo
    echo "Writing $RULE_FILE still failed even with the root filesystem"
    echo "writable. Something else is blocking it -- report back the exact"
    echo "error shown above."
    exit 1
fi

echo
echo "--- Verifying ---"
echo "Rule file contents:"
cat "$RULE_FILE" 2>/dev/null || echo "  MISSING"
echo "Your groups (should include 'input' after your next login):"
groups "$USER"
if [ -e /dev/uinput ]; then
    echo "/dev/uinput permissions right now (may still show root:root until"
    echo "poe2bot next creates it, or until reboot -- that's expected):"
    ls -l /dev/uinput
else
    echo "/dev/uinput doesn't exist yet -- poe2bot creates it on demand, and"
    echo "the udev rule above only takes effect the next time it's created."
fi

echo
echo "Done. Log out and back in (or reboot) for the new group membership to"
echo "take effect. After that, launch poe2bot / poe2bot.sh directly -- no"
echo "sudo password needed. If it still asks for one afterward, re-run this"
echo "script and share everything printed under '--- Verifying ---' above."
