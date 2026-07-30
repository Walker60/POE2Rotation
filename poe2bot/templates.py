import os
import uuid

from poe2bot import config
from poe2bot.log_setup import get_logger

log = get_logger()


def ensure_dir() -> None:
    os.makedirs(config.TEMPLATES_DIR, exist_ok=True)


def new_template_filename() -> str:
    """A collision-proof filename for a freshly captured calibration screenshot."""
    return f"{uuid.uuid4().hex}.png"


def template_path(filename: str) -> str:
    return os.path.join(config.TEMPLATES_DIR, filename)


def delete_template(filename) -> None:
    """No-op if filename is falsy or the file is already gone."""
    if not filename:
        return
    path = template_path(filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        log.warning(f"could not delete template '{filename}': {e}")


def sweep_unreferenced(keep: set) -> int:
    """Delete every file under TEMPLATES_DIR whose filename is not in `keep`.

    Template files are created eagerly at calibration time, before a rotation (or
    even a single step) is committed/saved, so this is the cleanup mechanism for
    abandoned calibrations. A single file's removal failing (e.g. Windows file-in-use)
    only logs a warning -- it doesn't abort the rest of the sweep, and that file will
    be reconsidered next time this runs.
    """
    ensure_dir()
    deleted = 0
    for filename in os.listdir(config.TEMPLATES_DIR):
        path = template_path(filename)
        if not os.path.isfile(path) or filename in keep:
            continue
        try:
            os.remove(path)
            deleted += 1
        except OSError as e:
            log.warning(f"could not remove orphaned template '{filename}': {e}")
    if deleted:
        log.info(f"template sweep removed {deleted} orphaned file(s)")
    return deleted
