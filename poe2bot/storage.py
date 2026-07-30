import json
import os
import re

from poe2bot import config
from poe2bot.models import Rotation


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "rotation"


def _path_for(name: str) -> str:
    return os.path.join(config.ROTATIONS_DIR, f"{_slugify(name)}.json")


def list_rotations() -> list:
    os.makedirs(config.ROTATIONS_DIR, exist_ok=True)
    names = []
    for filename in sorted(os.listdir(config.ROTATIONS_DIR)):
        if not filename.endswith(".json"):
            continue
        try:
            names.append(load_rotation_from_file(os.path.join(config.ROTATIONS_DIR, filename)).name)
        except (OSError, ValueError, KeyError):
            continue
    return names


def load_rotation_from_file(path: str) -> Rotation:
    with open(path, "r", encoding="utf-8") as f:
        return Rotation.from_dict(json.load(f))


def load_rotation(name: str) -> Rotation:
    return load_rotation_from_file(_path_for(name))


def load_all_rotations() -> dict:
    os.makedirs(config.ROTATIONS_DIR, exist_ok=True)
    rotations = {}
    for filename in sorted(os.listdir(config.ROTATIONS_DIR)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(config.ROTATIONS_DIR, filename)
        try:
            rotation = load_rotation_from_file(path)
        except (OSError, ValueError, KeyError):
            continue
        rotations[rotation.name] = rotation
    return rotations


def save_rotation(rotation: Rotation) -> None:
    os.makedirs(config.ROTATIONS_DIR, exist_ok=True)
    path = _path_for(rotation.name)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(rotation.to_dict(), f, indent=2)
    os.replace(tmp_path, path)


def delete_rotation(name: str) -> None:
    path = _path_for(name)
    if os.path.exists(path):
        os.remove(path)
