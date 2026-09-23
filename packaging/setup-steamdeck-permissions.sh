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

set -e

RULE_FILE=/etc/udev/rules.d/99-poe2bot-uinput.rules
RULE_CONTENT='SUBSYSTEM=="misc", KERNEL=="uinput", GROUP="input", MODE="0660"'

echo "This asks for your sudo password once, to install a udev rule and add"
echo "$USER to the 'input' group. After this, poe2bot never needs sudo again."
echo

if ! echo "$RULE_CONTENT" | sudo tee "$RULE_FILE" > /dev/null; then
    echo
    echo "Writing $RULE_FILE failed -- on SteamOS this usually means the"
    echo "root filesystem is still read-only. Try:"
    echo "  sudo steamos-readonly disable"
    echo "  sh $0"
    echo "  sudo steamos-readonly enable"
    exit 1
fi

sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG input "$USER"

echo
echo "Done. Log out and back in (or reboot) for the new group membership to"
echo "take effect. After that, launch poe2bot / poe2bot.sh directly -- no"
echo "sudo password needed, ever again."
