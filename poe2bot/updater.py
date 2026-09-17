"""Self-update support: check GitHub for a newer build than the one
currently running, download it, and swap it into place. Works the same way
on both the Linux/Steam Deck build and the Windows build -- IS_SUPPORTED
gates the "Updates" section in Settings entirely off for anything that isn't
a frozen (PyInstaller) build, since a plain `python main.py` run has no
build artifact to replace itself with.

What "update" means here: the CI workflow for each platform
(.github/workflows/build-steamdeck.yml, .github/workflows/build-windows.yml)
publishes its own single ROLLING GitHub Release (tag `_RELEASE_TAG`,
platform-specific -- see _PLATFORM below), replacing its one attached bundle
on every push -- not a versioned release history. There's no semantic
version number; "version" is just the git commit short SHA the running
build was made from (baked into a VERSION file next to the executable),
compared against whatever SHA that release's own body currently records.
This is meant for fast iteration while developing these ports, not a
polished release channel.
"""
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import certifi

from poe2bot.log_setup import get_logger

log = get_logger()

IS_SUPPORTED = getattr(sys, "frozen", False)

_PLATFORM = "win32" if sys.platform == "win32" else "linux"

# A PyInstaller onedir bundle carries its own OpenSSL shared libraries,
# built against whatever CA certificate PATHS happened to exist on the CI
# runner that built it -- those paths (or the certs at them) may not exist
# at all on the actual target machine, so ssl.create_default_context()'s
# normal "ask the OS" behavior can fail with "unable to get local issuer
# certificate" even though the connection itself is fine. Pointing
# explicitly at certifi's own bundled CA file sidesteps the target
# system's cert layout entirely -- this is the standard fix for exactly
# this class of "frozen app can't verify HTTPS on a machine it wasn't
# built on" problem.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_REPO = "Walker60/POE2Rotation"
# Each platform publishes to its own rolling release tag/bundle format, built
# by its own CI workflow -- a Windows build has no business being offered to
# a Linux install or vice versa, so which one this running build even asks
# about is picked once, here, from the platform it's actually running on.
_RELEASE_TAG = "windows-latest" if _PLATFORM == "win32" else "steamdeck-latest"
_ASSET_SUFFIX = ".zip" if _PLATFORM == "win32" else ".tar.gz"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/tags/{_RELEASE_TAG}"
_REQUEST_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "poe2bot-updater"}
_CHECK_TIMEOUT_S = 15
_DOWNLOAD_TIMEOUT_S = 120
# Only ever download from GitHub's own asset hosts -- a basic sanity check
# against the API ever handing back something unexpected, not a defense
# against a compromised repo (which could just as easily change _REPO above).
_ALLOWED_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com")

# Everything that lives alongside the executable (see poe2bot/config.py's
# BASE_DIR comment for why) that's the USER's own data -- rotations,
# settings, calibration templates, the undo-slot trash, logs -- as opposed
# to the executable and its bundled libraries, which always come from the
# freshly downloaded build and must never be carried over. Neither
# packaging/linux.spec nor packaging/windows.spec ever bundles any of these
# names as `datas` (windows.spec's own datas are vgamepad's DLLs, an
# unrelated library dependency), so a fresh extract never collides with
# what's copied in below.
# Named directly (not imported from config.py) since this always operates
# on the OLD install's own directory, whatever that happens to be, rather
# than on config.BASE_DIR specifically.
_USER_DATA_ENTRIES = ("rotations", "templates", "trash", "logs", "app_state.json")


class UpdateCheckFailed(Exception):
    """Raised by check_for_update()/download_and_install() for any network,
    parsing, or content problem -- always with a human-readable reason,
    since "no update available" and "couldn't check" must never look the
    same to whoever's showing this to a user."""


class UpdateInfo:
    def __init__(self, version: str, download_url: str, published_at: str):
        self.version = version
        self.download_url = download_url
        self.published_at = published_at


def current_version() -> str:
    """The running build's own git short SHA, read from the VERSION file
    the CI workflow writes next to the executable. "dev" if not running
    from a frozen (PyInstaller) build, or that file is missing/unreadable
    for any reason -- never an error, since this is also called from
    non-Linux/non-frozen contexts just to display in Settings."""
    if not getattr(sys, "frozen", False):
        return "dev"
    version_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def check_for_update() -> "UpdateInfo | None":
    """Queries the rolling release; None if its version matches
    current_version() (nothing to do), else an UpdateInfo describing what's
    available. Raises UpdateCheckFailed on any network/parsing/content
    problem -- callers must show that to the user rather than treating it
    as "no update," which would be actively misleading."""
    try:
        request = urllib.request.Request(_API_URL, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=_CHECK_TIMEOUT_S, context=_SSL_CONTEXT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateCheckFailed(
                "No build has been published yet for this platform (the "
                f"'{_RELEASE_TAG}' release doesn't exist).")
        raise UpdateCheckFailed(f"GitHub returned an error: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise UpdateCheckFailed(f"Could not reach GitHub: {e.reason}")
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise UpdateCheckFailed(f"Unexpected response from GitHub: {e}")

    remote_version = (data.get("body") or "").strip()
    if not remote_version:
        raise UpdateCheckFailed("Latest release has no version recorded in its body -- can't compare.")

    assets = data.get("assets") or []
    asset = next((a for a in assets if a.get("name", "").endswith(_ASSET_SUFFIX)), None)
    if not asset or not asset.get("browser_download_url"):
        raise UpdateCheckFailed("Latest release has no downloadable bundle attached.")
    download_url = asset["browser_download_url"]
    host = urllib.parse.urlparse(download_url).netloc
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_DOWNLOAD_HOSTS):
        raise UpdateCheckFailed(f"Refusing to download from an unexpected host: {host!r}")

    if remote_version == current_version():
        return None
    return UpdateInfo(version=remote_version, download_url=download_url, published_at=data.get("published_at", ""))


def _copy_user_data(old_dir: str, new_dir: str) -> None:
    """Copies (never moves) every _USER_DATA_ENTRIES path that exists in
    old_dir into new_dir, so the freshly extracted build -- which never
    ships any of them, see _USER_DATA_ENTRIES's comment -- ends up with the
    user's actual rotations/settings/templates instead of starting empty.
    Deliberately a COPY, not a move: old_dir (still the live installation
    at the point this runs, before the rename swap below) is left fully
    intact, so a crash between this and the final cleanup can never lose
    the only copy of the user's data -- it's only ever removed once
    everything else has already succeeded."""
    for name in _USER_DATA_ENTRIES:
        src = os.path.join(old_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(new_dir, name), dirs_exist_ok=True)
        elif os.path.isfile(src):
            shutil.copy2(src, os.path.join(new_dir, name))


def download_and_install(update_info: "UpdateInfo", progress_callback=None) -> None:
    """Downloads update_info's bundle and extracts it to a fresh sibling
    directory (poe2bot.new), ready to be swapped in for the currently
    running installation. progress_callback(str), if given, is called from
    THIS (calling) thread at each stage -- callers running this on a
    background thread are responsible for hopping back to their own UI
    thread themselves. Raises UpdateCheckFailed/OSError on any failure,
    leaving install_dir completely untouched either way -- nothing about
    the running installation itself changes here on EITHER platform.

    What happens after staging differs by platform, entirely inside
    restart_into_new_version():

    - On Linux, swapping poe2bot.new in for the running install_dir is a
      plain directory RENAME, safe to do to a still-running process's own
      directory because open file descriptors/mappings are keyed by inode,
      not path -- so restart_into_new_version() does the rename-swap itself,
      in-process, then execs straight into the result.
    - On Windows, a directory a running exe's OWN files live in (not just
      the exe, but every DLL it has loaded out of that same folder --
      python3XX.dll, Tcl/Tk, etc.) routinely can't be renamed or deleted
      out from under the still-running process -- unlike the exe file
      alone, which Windows' loader deliberately allows renaming (the
      well-known "rename the running exe" self-update trick), the
      *directory* rename this needs isn't reliably possible while anything
      in it is still open. So on Windows the actual swap is handed off to a
      short-lived detached helper script that waits for THIS process to
      fully exit first -- see restart_into_new_version()'s win32 branch.

    Either way, the user's own data (rotations/settings/templates/etc. --
    see _USER_DATA_ENTRIES) is copied into the new build here, while
    install_dir is still the live installation, so that whichever swap
    mechanism runs later never has to touch it -- a freshly extracted build
    never contains any of it on its own."""
    install_dir = os.path.dirname(os.path.abspath(sys.executable))  # .../poe2bot/ (the running bundle's own folder)
    parent_dir = os.path.dirname(install_dir)
    new_dir = os.path.join(parent_dir, "poe2bot.new")
    old_dir = os.path.join(parent_dir, "poe2bot.old")
    for stale_dir in (new_dir, old_dir):
        if os.path.isdir(stale_dir):
            shutil.rmtree(stale_dir, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="poe2bot-update-") as tmp_dir:
        archive_path = os.path.join(tmp_dir, "update" + _ASSET_SUFFIX)
        if progress_callback:
            progress_callback("Downloading...")
        request = urllib.request.Request(update_info.download_url, headers=_REQUEST_HEADERS)
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S, context=_SSL_CONTEXT) as response, \
                open(archive_path, "wb") as out_file:
            shutil.copyfileobj(response, out_file)

        if progress_callback:
            progress_callback("Extracting...")
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        if _ASSET_SUFFIX == ".zip":
            with zipfile.ZipFile(archive_path) as zf:
                _safe_extract_zip(zf, extract_dir)
        else:
            with tarfile.open(archive_path) as tar:
                _safe_extract_tar(tar, extract_dir)

        extracted_app_dir = os.path.join(extract_dir, "poe2bot")
        if not os.path.isdir(extracted_app_dir):
            raise UpdateCheckFailed(
                "Downloaded bundle doesn't look like a poe2bot build (no poe2bot/ folder inside it).")

        if progress_callback:
            progress_callback("Installing...")
        # shutil.move (not os.rename): the temp dir and parent_dir may be on
        # different filesystems, which a bare os.rename can't cross --
        # shutil.move falls back to copy+delete in that case.
        shutil.move(extracted_app_dir, new_dir)
        _copy_user_data(install_dir, new_dir)

    if _PLATFORM != "win32":
        # Linux can swap right now -- see this function's own docstring for
        # why that's safe. Done here (rather than deferred to
        # restart_into_new_version(), like Windows) so the update has
        # already fully taken effect on disk even if the user never clicks
        # "restart now" on the dialog that follows -- the NEXT time they
        # launch the app at all, by any means, it's already the new version.
        os.rename(install_dir, old_dir)
        os.rename(new_dir, install_dir)
        # Safe immediately: new_dir (now living at install_dir) already has
        # its own copy of everything _USER_DATA_ENTRIES names, so old_dir
        # holds nothing that isn't also preserved above -- ignore_errors=True
        # matches the stale-leftover cleanup above, since a failed delete
        # here means only "still there next launch," not a failed update.
        shutil.rmtree(old_dir, ignore_errors=True)


def cleanup_stale_update() -> None:
    """Removes a leftover poe2bot.old directory, if one somehow still exists
    at startup -- meant to be called once, early, every time the app starts.
    Normally a no-op: on Linux, download_and_install() deletes old_dir
    itself right after swapping; on Windows, restart_into_new_version()'s
    detached helper script deletes it right after ITS swap, once this
    process (the one it was waiting on) has actually exited -- either way,
    old_dir shouldn't still be there by the next launch. This exists purely
    as a backstop for the rare case that cleanup step didn't finish (e.g.
    antivirus scanning the directory at that exact moment on Windows), so a
    failed update doesn't silently leave a full extra copy of the previous
    build's bundle (Python + Tcl/Tk + every dependency) sitting on disk
    forever. ignore_errors=True: this is a best-effort cleanup, not
    something a startup should ever fail over -- worst case it just tries
    again next launch."""
    if not getattr(sys, "frozen", False):
        return
    install_dir = os.path.dirname(os.path.abspath(sys.executable))
    old_dir = os.path.join(os.path.dirname(install_dir), "poe2bot.old")
    if os.path.isdir(old_dir):
        log.info(f"Removing leftover update directory from a previous install: {old_dir}")
        shutil.rmtree(old_dir, ignore_errors=True)
        if os.path.isdir(old_dir):
            log.warning(f"Could not fully remove leftover update directory (still in use?): {old_dir}")


def _safe_extract_tar(tar: "tarfile.TarFile", dest_dir: str) -> None:
    """tarfile.extractall, but refusing any member whose path would land
    outside dest_dir (a path-traversal guard, since this extracts a
    downloaded archive rather than a trusted local file)."""
    dest_dir = os.path.realpath(dest_dir)
    for member in tar.getmembers():
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if member_path != dest_dir and not member_path.startswith(dest_dir + os.sep):
            raise UpdateCheckFailed(f"Refusing to extract a file outside the target directory: {member.name!r}")
    tar.extractall(dest_dir)


def _safe_extract_zip(zf: "zipfile.ZipFile", dest_dir: str) -> None:
    """zipfile.extractall, but refusing any member whose path would land
    outside dest_dir -- the same path-traversal guard as _safe_extract_tar,
    for the Windows (.zip) build."""
    dest_dir = os.path.realpath(dest_dir)
    for name in zf.namelist():
        member_path = os.path.realpath(os.path.join(dest_dir, name))
        if member_path != dest_dir and not member_path.startswith(dest_dir + os.sep):
            raise UpdateCheckFailed(f"Refusing to extract a file outside the target directory: {name!r}")
    zf.extractall(dest_dir)


def restart_into_new_version() -> None:
    """Finishes installing the update download_and_install() staged at
    poe2bot.new and restarts into it. Never returns on success (see each
    branch below for exactly how); raises OSError if it can't even get that
    far (callers should treat that as "please restart the app yourself,"
    not retry)."""
    if _PLATFORM == "win32":
        _swap_and_restart_windows()
        return
    # Linux: download_and_install() already did the actual swap (see its own
    # docstring for why that's safe here) -- sys.executable now names a path
    # whose contents are the NEW build, so this is a genuine in-place restart
    # into it, not a separate subprocess/relaunch. os.execv is implemented on
    # Windows too (via the C runtime's own process-overlay support, not
    # emulated by spawning-and-waiting) but isn't used there -- see
    # _swap_and_restart_windows() for why Windows needs a different approach.
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])


def _swap_and_restart_windows() -> None:
    """Windows counterpart to the Linux branch above. The swap
    download_and_install() already performed in-process on Linux can't
    happen here while this process is still running out of install_dir --
    see download_and_install()'s docstring -- so instead this hands the
    swap off to a small, detached helper .bat script and then exits
    immediately, via os._exit() (skips atexit/cleanup the same way
    os.execv() does on Linux -- UpdaterMixin's caller has already done its
    own graceful shutdown, stopping rotations/hotkeys/the controller, before
    ever calling restart_into_new_version() at all). The script:

    1. Retries renaming install_dir to old_dir in a loop until it succeeds
       -- which only becomes possible once every DLL this process has
       loaded out of install_dir is actually unloaded, i.e. once this
       process has fully exited. This polls the rename itself rather than
       this process's PID, so it needs no extra tool (tasklist's output is
       locale-dependent; a failed `ren`'s errorlevel isn't).
    2. Renames new_dir into install_dir's place and deletes old_dir --
       trivially safe by this point, since nothing has either open anymore.
    3. Relaunches the exe now sitting at install_dir, then deletes itself
       (the classic self-deleting-batch-file trick: cmd.exe holds its own
       script file open with share-delete while running it, so a script's
       last line can delete the very file being executed).

    Left running completely undetached, this would still work but would
    keep a visible console window open after this process exits (with
    nothing left to own it) -- DETACHED_PROCESS/CREATE_NEW_PROCESS_GROUP
    avoid that."""
    install_dir = os.path.dirname(os.path.abspath(sys.executable))
    parent_dir = os.path.dirname(install_dir)
    new_dir = os.path.join(parent_dir, "poe2bot.new")
    old_dir = os.path.join(parent_dir, "poe2bot.old")
    install_name = os.path.basename(install_dir)
    old_name = os.path.basename(old_dir)
    exe_path = sys.executable
    extra_args = " ".join(f'"{a}"' for a in sys.argv[1:])

    script_path = os.path.join(tempfile.gettempdir(), f"poe2bot-update-{os.getpid()}.bat")
    # %ERRORLEVEL% after `ren`/`rmdir` reliably reflects success/failure on
    # every supported Windows version -- `ping -n 2 127.0.0.1 >nul` is the
    # traditional console-independent ~1s delay (NOT `timeout`, which
    # refuses to run at all without a real console, and this script runs
    # fully detached with none). `enabledelayedexpansion` + `!RETRIES!`
    # (not `%RETRIES%`) inside the retry block: cmd.exe substitutes a bare
    # %VAR% for an ENTIRE parenthesized block in one pass before running any
    # of it, so a same-block `if %RETRIES%...` right after `set /a
    # RETRIES-=1` would still see the PRE-decrement value every time --
    # delayed expansion (!VAR!) is the standard fix for reading a value a
    # block just set, within that same block.
    #
    # If every retry is exhausted, :relaunch is reached WITHOUT the
    # install_dir/new_dir swap ever having happened -- exe_path then still
    # names the untouched OLD build, so `start` below just brings the app
    # back as it was rather than leaving it closed with nothing to show for
    # it; the update itself is simply abandoned for this session (retrying
    # "Check for Updates" later tries the whole thing fresh).
    script_lines = [
        "@echo off",
        "setlocal enabledelayedexpansion",
        "set RETRIES=30",
        ":retry_rename",
        f'ren "{install_dir}" "{old_name}"',
        "if errorlevel 1 (",
        "    set /a RETRIES-=1",
        "    if !RETRIES! leq 0 goto relaunch",
        "    ping -n 2 127.0.0.1 >nul",
        "    goto retry_rename",
        ")",
        f'ren "{new_dir}" "{install_name}"',
        f'rmdir /s /q "{old_dir}" 2>nul',
        ":relaunch",
        f'start "" "{exe_path}" {extra_args}',
        f'del "{script_path}"',
    ]
    with open(script_path, "w", newline="\r\n") as f:
        f.write("\n".join(script_lines) + "\n")

    subprocess.Popen(
        ["cmd", "/c", script_path],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    os._exit(0)
