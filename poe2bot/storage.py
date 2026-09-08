import json
import os
import re
import zipfile
from typing import Optional

from poe2bot import config, templates
from poe2bot.models import Rotation, iter_conditions

_ILLEGAL_FOLDER_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')


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


def path_for(name: str, folder: str = "") -> str:
    """Where `name` in `folder` actually lives on disk. Public (not `_path_for`)
    because callers outside this module need it too -- e.g. the GUI's save
    validation compares this across every existing rotation to catch two
    different display names that sanitize to the same file (see _slugify:
    it folds case and punctuation, so "Fire Ball" and "Fire-Ball" collide
    here even though they're clearly different names to a human)."""
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


def _try_load_rotation(path: str) -> Optional[Rotation]:
    """load_rotation_from_file(path), or None if it fails to parse -- shared
    failure handling for list_rotations()/load_all_rotations()/
    has_unparseable_rotations(), all of which must tolerate one bad rotation
    file without crashing or silently corrupting output for the rest.
    TypeError included alongside the obvious parse-failure types because
    dict.get(key, default) only substitutes `default` when `key` is *absent*
    -- an explicit JSON null (e.g. a hand-edited "delay_ms": null) makes
    int(None)/tuple(None-ish) raise TypeError instead."""
    try:
        return load_rotation_from_file(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _iter_loaded_rotations():
    """Yield (rotation, folder) for every rotation file under ROTATIONS_DIR
    that parses successfully, silently skipping any that don't -- shared by
    list_rotations() and load_all_rotations()."""
    for path, folder in _iter_rotation_files():
        rotation = _try_load_rotation(path)
        if rotation is not None:
            yield rotation, folder


def list_rotations() -> list:
    return [rotation.name for rotation, _folder in _iter_loaded_rotations()]


def load_rotation_from_file(path: str) -> Rotation:
    with open(path, "r", encoding="utf-8") as f:
        return Rotation.from_dict(json.load(f))


def load_rotation(name: str, folder: str = "") -> Rotation:
    rotation = load_rotation_from_file(path_for(name, folder))
    rotation.folder = folder
    return rotation


def load_all_rotations() -> dict:
    rotations = {}
    for rotation, folder in _iter_loaded_rotations():
        rotation.folder = folder
        rotations[rotation.name] = rotation
    return rotations


def save_rotation(rotation: Rotation) -> None:
    path = path_for(rotation.name, rotation.folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(rotation.to_dict(), f, indent=2)
    os.replace(tmp_path, path)


def delete_rotation(name: str, folder: str = "") -> None:
    path = path_for(name, folder)
    if os.path.exists(path):
        os.remove(path)
    _prune_empty_dirs(folder)


_TRASH_PATH = os.path.join(config.TRASH_DIR, "last_deleted.json")


def trash_rotation(name: str, folder: str) -> None:
    """Moves name/folder's rotation file into a single-slot trash instead
    of deleting it outright -- a lightweight "undo my last delete" safety
    net for RotationListMixin._delete_rotation, since there's no undo
    anywhere else in this app and autosave means every edit already
    reaches disk instantly. Only the MOST RECENT deletion is recoverable --
    trashing a second rotation permanently discards whatever was in the
    slot before it; this is a quick "did I mean to click that?" undo, not a
    full recycle bin. The rotation's original folder (never itself part of
    the normal JSON shape -- see Rotation.folder's own docstring) is
    stashed under an extra "_trashed_from_folder" key that Rotation.from_dict
    simply ignores, so restore_last_trashed() can put it back exactly where
    it came from."""
    src_path = path_for(name, folder)
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["_trashed_from_folder"] = folder
    os.makedirs(config.TRASH_DIR, exist_ok=True)
    tmp_path = _TRASH_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, _TRASH_PATH)
    os.remove(src_path)
    _prune_empty_dirs(folder)


def peek_trashed_rotation() -> Optional[Rotation]:
    """The rotation currently sitting in the trash slot, with .folder
    restored to wherever it was trashed from -- or None if the slot is
    empty or its file fails to parse. Used both to populate "Restore Last
    Deleted" and, via trashed_rotation_templates() below, to keep the
    template GC from deleting anything it still references."""
    if not os.path.isfile(_TRASH_PATH):
        return None
    try:
        with open(_TRASH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        rotation = Rotation.from_dict(data)
        rotation.folder = data.get("_trashed_from_folder", "")
        return rotation
    except (OSError, ValueError, KeyError, TypeError):
        return None


def restore_last_trashed() -> Optional[Rotation]:
    """Moves whatever's in the trash slot back into ROTATIONS_DIR at its
    original folder, and clears the slot. None (no-op) if the slot is
    empty or its file fails to parse -- callers should check
    peek_trashed_rotation() first (and warn about a name/folder collision
    with something the user kept in the meantime) rather than assuming this
    always succeeds."""
    rotation = peek_trashed_rotation()
    if rotation is None:
        return None
    save_rotation(rotation)
    os.remove(_TRASH_PATH)
    return rotation


def trashed_rotation_templates() -> set:
    """Every image-match template filename referenced by whatever's
    currently in the trash slot, or an empty set if it's empty/unreadable
    -- folded into ConditionsMixin._referenced_templates() so the periodic
    sweep never deletes a template "Restore Last Deleted" would still
    need."""
    rotation = peek_trashed_rotation()
    if rotation is None:
        return set()
    return {c.template for c in iter_conditions(rotation.steps) if c.template}


def move_rotation(rotation: Rotation, old_name: str, old_folder: str) -> None:
    """Rename/move a rotation from (old_name, old_folder) to rotation's
    current name/folder. Writes the new file *before* removing the old one --
    the reverse of a naive delete-then-save -- so a crash in between leaves
    the rotation recoverable (present at both the old and new paths) rather
    than lost entirely (present at neither). Not fully atomic (that would
    need a journal/two-phase commit, overkill here), but this ordering turns
    "permanent silent data loss" into "a stray duplicate file to notice and
    clean up," which is the tradeoff that matters for a crash mid-move."""
    old_path = os.path.normcase(os.path.normpath(path_for(old_name, old_folder)))
    new_path = os.path.normcase(os.path.normpath(path_for(rotation.name, rotation.folder)))
    save_rotation(rotation)
    if old_path != new_path:
        delete_rotation(old_name, old_folder)


def export_rotation_bundle(rotation: Rotation, dest_path: str) -> None:
    """Writes `rotation` (as the exact same JSON shape as its own on-disk
    file) plus every image-match template it references into one zip file
    at dest_path -- for sharing a rotation with someone else without also
    needing to separately locate and copy its matching templates/*.png
    files by hand (a pixel/timer condition needs no such file, so this is
    a no-op for those). See import_rotation_bundle for the other half."""
    template_filenames = {c.template for c in iter_conditions(rotation.steps) if c.template}
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rotation.json", json.dumps(rotation.to_dict(), indent=2))
        for filename in template_filenames:
            path = templates.template_path(filename)
            if os.path.isfile(path):
                zf.write(path, arcname=f"templates/{filename}")


def import_rotation_bundle(src_path: str) -> Rotation:
    """Reads a bundle written by export_rotation_bundle(): copies every
    template PNG it contains into the real templates/ directory under a
    FRESH random filename (never reusing the one it had in the zip -- see
    templates.import_template_bytes), rewriting the returned Rotation's own
    condition.template references to match. The rotation's hotkey/cancel/
    reset/pause keys and folder are the caller's decision, not this
    function's -- see RotationListMixin._on_import_rotation_clicked, which
    clears the former (another person's keybinds mean nothing on this
    machine) and leaves the latter at Rotation's own default (ungrouped).

    Raises the same exceptions a corrupt rotation JSON file already would
    (BadZipFile/KeyError/ValueError/etc. from json.loads()/Rotation.from_dict())
    for a file that isn't actually one of these bundles -- callers should
    catch broadly, same as any other "load something a user handed us" path."""
    with zipfile.ZipFile(src_path, "r") as zf:
        data = json.loads(zf.read("rotation.json").decode("utf-8"))
        rotation = Rotation.from_dict(data)
        rename_map = {}
        for name in zf.namelist():
            if name.startswith("templates/") and not name.endswith("/"):
                old_filename = name[len("templates/"):]
                rename_map[old_filename] = templates.import_template_bytes(zf.read(name))
    for condition in iter_conditions(rotation.steps):
        if condition.template in rename_map:
            condition.template = rename_map[condition.template]
    return rotation


def has_unparseable_rotations() -> bool:
    """True if any rotation JSON file under ROTATIONS_DIR currently fails to
    load. Used to make the template GC abstain rather than risk deleting a
    calibration image that a merely-temporarily-broken (not genuinely gone)
    rotation still references -- a broken file's own template references
    never make it into the "still referenced" set the sweep uses, since
    list_rotations()/load_all_rotations() silently skip it."""
    return any(_try_load_rotation(path) is None for path, _folder in _iter_rotation_files())


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
