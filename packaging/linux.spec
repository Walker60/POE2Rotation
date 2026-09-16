# PyInstaller spec for a self-contained Linux build (Steam Deck / SteamOS
# Desktop Mode -- see README's Steam Deck section). Produces a "onedir"
# bundle: a folder containing a full Python + Tcl/Tk + every dependency,
# runnable on the target with nothing else installed.
#
# MUST be built on Linux x86_64 -- PyInstaller bundles the actual shared
# libraries (including Tcl/Tk) present on the machine that runs it, so this
# cannot be cross-built from Windows. See .github/workflows/build-steamdeck.yml,
# which runs this automatically on an Ubuntu runner and publishes the result
# as a downloadable artifact -- that's the intended way to produce this, not
# running PyInstaller by hand, though `pyinstaller packaging/linux.spec` from
# a repo checkout works identically if you ever need to build it yourself
# (e.g. inside the Distrobox container from README's Steam Deck section).
#
# Untested against a real onedir bundle actually running on Steam Deck
# hardware -- see README's Steam Deck section for what to validate first.
import os
import sys

if not sys.platform.startswith("linux"):
    raise SystemExit(
        "packaging/linux.spec must be built on Linux (it bundles the build "
        "machine's own Tcl/Tk and shared libraries) -- see this file's own "
        "header comment, and .github/workflows/build-steamdeck.yml for the "
        "automated way to produce this build.")

# SPECPATH (injected by PyInstaller into this file's exec namespace) is
# this .spec file's own directory, regardless of the current working
# directory `pyinstaller` was invoked from -- the workflow invokes it as
# `pyinstaller packaging/linux.spec` from the repo root, so a plain
# relative "../main.py" would resolve against the wrong base and fail.
main_script = os.path.join(SPECPATH, "..", "main.py")

a = Analysis(
    [main_script],
    pathex=[],
    binaries=[],
    datas=[],
    # PIL._tkinter_finder: a small internal Pillow module PIL.ImageTk (used
    # by poe2bot/gui/overlays.py to display the calibration overlay's
    # captured-screen background) imports to locate the exact _tkinter
    # binary matching the running Tcl/Tk -- PyInstaller's own Pillow hook
    # doesn't pick this up on its own, a well-known gap for anything using
    # ImageTk specifically (plain PIL.Image alone doesn't need it).
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="poe2bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a terminal window -- this app logs to it and to logs/, and
                   # there's no console-hiding equivalent of Windows' windowed mode
                   # worth the complexity for a first Linux build
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="poe2bot",
)
