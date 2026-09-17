# PyInstaller spec for a self-contained Windows build -- what
# poe2bot/updater.py's self-update mechanism downloads and installs, and
# what lets IS_SUPPORTED (and therefore the Settings > Updates section) be
# true on Windows at all: a plain `python main.py` run has no build
# artifact for the updater to replace itself with. See packaging/linux.spec
# for the equivalent Linux/Steam Deck build -- this mirrors its structure
# and its onedir approach (a folder containing a full Python + Tcl/Tk +
# every dependency, needing nothing else pre-installed on the target
# machine) so poe2bot/updater.py's rename-based directory swap has the same
# shape to work with on both platforms.
#
# MUST be built on Windows -- PyInstaller bundles the actual Python DLL,
# Tcl/Tk, and every other shared library present on the machine that runs
# it, so this cannot be cross-built from Linux. See
# .github/workflows/build-windows.yml, which runs this automatically on a
# windows-latest runner and publishes the result as a rolling GitHub
# Release -- that's the intended way to produce this, not running
# PyInstaller by hand, though `pyinstaller packaging/windows.spec` from a
# repo checkout works identically if you ever need to build it yourself.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

if sys.platform != "win32":
    raise SystemExit(
        "packaging/windows.spec must be built on Windows (it bundles the build "
        "machine's own Python/Tcl/Tk DLLs) -- see this file's own header comment, "
        "and .github/workflows/build-windows.yml for the automated way to produce "
        "this build.")

# SPECPATH (injected by PyInstaller into this file's exec namespace) is
# this .spec file's own directory, regardless of the current working
# directory `pyinstaller` was invoked from -- the workflow invokes it as
# `pyinstaller packaging/windows.spec` from the repo root, so a plain
# relative "../main.py" would resolve against the wrong base and fail.
main_script = os.path.join(SPECPATH, "..", "main.py")

a = Analysis(
    [main_script],
    pathex=[],
    binaries=[],
    # vgamepad (poe2bot/controller.py's virtual-controller output -- see
    # requirements.txt) is only ever imported lazily, deep inside a
    # function, specifically so it's never required just to start the app
    # (see controller.py's own comment on that). PyInstaller's static
    # analysis still finds that import fine and bundles the vgamepad
    # PACKAGE's .py files on its own -- what it can't find on its own is
    # vgamepad's bundled ViGEmClient.dll (under vgamepad/win/vigem/client/),
    # which vgamepad loads via a plain filesystem path relative to its own
    # package directory, not through Python's import machinery at all.
    # collect_data_files grabs every non-.py file vgamepad ships (both
    # architectures' DLLs, plus its ViGEmBus driver installer) and places
    # them at the same path relative to this bundle that they'd have in a
    # normal pip install, which is exactly where vgamepad's own runtime
    # path lookups expect to find them.
    datas=collect_data_files("vgamepad"),
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
    # No console window: unlike packaging/linux.spec (which keeps one since
    # there's no equivalent "windowed" convenience worth the complexity for
    # a first Linux build), Windows users double-click an .exe and expect a
    # normal windowed app, not a terminal popping up alongside it. Nothing
    # is lost by hiding it -- this app already logs everything to
    # logs/poe2bot.log (see poe2bot/log_setup.py) regardless of whether a
    # console exists to also echo it to.
    console=False,
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
