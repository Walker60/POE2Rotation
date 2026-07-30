import json
import os
import re

from poe2bot import config
from poe2bot.models import Rotation

_ILLEGAL_FOLDER_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "rotation"


def _folder_parts(folder: str) -> list:
    """Split a '/'-separated folder path into sanitized directory-name segments,
    dropping empty/'.'/'..' segments so a stray value can't escape ROTATIONS_DIR
    or collide with illegal Windows path characters. Folder names are used
    close to as-typed (not slugified) since, unlike rotation names, they're
    already used directly as real directory names."""
    parts = []
    for raw in (folder or "").split("/"):
        part = _ILLEGAL_FOLDER_CHARS.sub("_", raw.strip())
        if not part or part in (".", ".."):
            continue
        parts.append(part)
    return parts


def _path_for(name: str, folder: str = "") -> str:
    return os.path.join(config.ROTATIONS_DIR, *_folder_parts(folder), f"{_slugify(name)}.json")


def _iter_rotation_files():
    """Yield (path, folder) for every rotation JSON file under ROTATIONS_DIR,
    at any depth. `folder` is its location relative to ROTATIONS_DIR joined
    with '/' (e.g. "Bosses/HardMode"), or "" for a file directly in the root."""
    os.makedirs(config.ROTATIONS_DIR, exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(config.ROTATIONS_DIR):
        rel_dir = os.path.relpath(dirpath, config.ROTATIONS_DIR)
        folder = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for filename in sorted(filenames):
            if filename.endswith(".json"):
                yield os.path.join(dirpath, filename), folder


def list_rotations() -> list:
    names = []
    for path, folder in _iter_rotation_files():
        try:
            rotation = load_rotation_from_file(path)
        except (OSError, ValueError, KeyError):
            continue
        names.append(rotation.name)
    return names


def load_rotation_from_file(path: str) -> Rotation:
    with open(path, "r", encoding="utf-8") as f:
        return Rotation.from_dict(json.load(f))


def load_rotation(name: str, folder: str = "") -> Rotation:
    rotation = load_rotation_from_file(_path_for(name, folder))
    rotation.folder = folder
    return rotation


def load_all_rotations() -> dict:
    rotations = {}
    for path, folder in _iter_rotation_files():
        try:
            rotation = load_rotation_from_file(path)
        except (OSError, ValueError, KeyError):
            continue
        rotation.folder = folder
        rotations[rotation.name] = rotation
    return rotations


def save_rotation(rotation: Rotation) -> None:
    path = _path_for(rotation.name, rotation.folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(rotation.to_dict(), f, indent=2)
    os.replace(tmp_path, path)


def delete_rotation(name: str, folder: str = "") -> None:
    path = _path_for(name, folder)
    if os.path.exists(path):
        os.remove(path)
    _prune_empty_dirs(folder)


def _prune_empty_dirs(folder: str) -> None:
    """After removing a rotation from `folder`, remove that folder -- and any
    now-empty ancestor folders below ROTATIONS_DIR -- so moving/renaming
    rotations out of a folder doesn't leave empty directories behind."""
    parts = _folder_parts(folder)
    while parts:
        dir_path = os.path.join(config.ROTATIONS_DIR, *parts)
        try:
            if os.path.isdir(dir_path) and not os.listdir(dir_path):
                os.rmdir(dir_path)
            else:
                break
        except OSError:
            break
        parts.pop()
