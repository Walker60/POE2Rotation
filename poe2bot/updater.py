"""Self-update support for the Linux/Steam Deck build: check GitHub for a
newer build than the one currently running, download it, and swap it into
place. Windows has no equivalent yet (see README's Steam Deck section) --
IS_SUPPORTED gates the "Updates" section in Settings entirely off there.

What "update" means here: the CI workflow (.github/workflows/build-steamdeck.yml)
publishes a single ROLLING GitHub Release (tag `_RELEASE_TAG`), replacing its
one attached bundle on every push -- not a versioned release history. There's
no semantic version number; "version" is just the git commit short SHA the
running build was made from (baked into a VERSION file next to the
executable), compared against whatever SHA that release's own body currently
records. This is meant for fast iteration while developing the Linux port,
not a polished release channel.
"""
import json
import os
import shutil
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import certifi

IS_SUPPORTED = sys.platform != "win32" and getattr(sys, "frozen", False)

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
_RELEASE_TAG = "steamdeck-latest"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/tags/{_RELEASE_TAG}"
_REQUEST_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "poe2bot-updater"}
_CHECK_TIMEOUT_S = 15
_DOWNLOAD_TIMEOUT_S = 120
# Only ever download from GitHub's own asset hosts -- a basic sanity check
# against the API ever handing back something unexpected, not a defense
# against a compromised repo (which could just as easily change _REPO above).
_ALLOWED_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com")


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
                "No Steam Deck build has been published yet (the "
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
    asset = next((a for a in assets if a.get("name", "").endswith(".tar.gz")), None)
    if not asset or not asset.get("browser_download_url"):
        raise UpdateCheckFailed("Latest release has no downloadable bundle attached.")
    download_url = asset["browser_download_url"]
    host = urllib.parse.urlparse(download_url).netloc
    if not any(host == h or host.endswith("." + h) for h in _ALLOWED_DOWNLOAD_HOSTS):
        raise UpdateCheckFailed(f"Refusing to download from an unexpected host: {host!r}")

    if remote_version == current_version():
        return None
    return UpdateInfo(version=remote_version, download_url=download_url, published_at=data.get("published_at", ""))


def download_and_install(update_info: "UpdateInfo", progress_callback=None) -> None:
    """Downloads update_info's bundle and swaps it in for the currently
    running installation, by directory RENAME rather than overwriting files
    in place: renaming install_dir out from under this still-running
    process is safe on Linux (open file descriptors/mappings are keyed by
    inode, not path) -- unlike trying to open-and-write over the
    currently-executing binary file itself, which the kernel refuses
    (ETXTBSY, "text file busy"). progress_callback(str), if given, is
    called from THIS (calling) thread at each stage -- callers running this
    on a background thread are responsible for hopping back to their own
    UI thread themselves. Raises UpdateCheckFailed/OSError on any failure;
    the installation is left as either fully the old version or fully the
    new one, never a half-swapped mix, since the only cross-directory
    operation (the sibling-directory rename pair below) is two single
    os.rename calls, each atomic on its own."""
    install_dir = os.path.dirname(os.path.abspath(sys.executable))  # .../poe2bot/ (the running bundle's own folder)
    parent_dir = os.path.dirname(install_dir)
    new_dir = os.path.join(parent_dir, "poe2bot.new")
    old_dir = os.path.join(parent_dir, "poe2bot.old")
    for stale_dir in (new_dir, old_dir):
        if os.path.isdir(stale_dir):
            shutil.rmtree(stale_dir, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="poe2bot-update-") as tmp_dir:
        archive_path = os.path.join(tmp_dir, "update.tar.gz")
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
        with tarfile.open(archive_path) as tar:
            _safe_extract(tar, extract_dir)

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

    os.rename(install_dir, old_dir)
    os.rename(new_dir, install_dir)


def _safe_extract(tar: "tarfile.TarFile", dest_dir: str) -> None:
    """tarfile.extractall, but refusing any member whose path would land
    outside dest_dir (a path-traversal guard, since this extracts a
    downloaded archive rather than a trusted local file)."""
    dest_dir = os.path.realpath(dest_dir)
    for member in tar.getmembers():
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if member_path != dest_dir and not member_path.startswith(dest_dir + os.sep):
            raise UpdateCheckFailed(f"Refusing to extract a file outside the target directory: {member.name!r}")
    tar.extractall(dest_dir)


def restart_into_new_version() -> None:
    """Replaces this running process with whatever's now at sys.executable
    -- download_and_install() just swapped that path to point at the new
    version's directory, so this is a genuine in-place restart into it, not
    a separate subprocess/relaunch. Never returns on success; raises OSError
    if exec itself fails (callers should treat that as "please restart the
    app yourself," not retry)."""
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
