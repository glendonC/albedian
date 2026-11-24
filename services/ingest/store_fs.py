"""Content-addressed write-once filesystem persistence and permission guards for uploads."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any

from services.ingest.store_types import (
    PRIVATE_FILE_MODE,
    PRIVATE_FOLDER_MODE,
    Received,
    SHA256_PATTERN,
    STALE_PARTIAL_SEC,
)

PARTIAL_DIRNAME = ".partial"
RECORD_FILENAME = "record.json"
_RECORD_SCRATCH_TOKEN_BYTES = 8


def _fsync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def close_existing_root(root: Path) -> None:
    """Close an already-existing store root folder to other accounts on open."""
    if root.is_dir():
        os.chmod(root, PRIVATE_FOLDER_MODE)


def ensure_private_folder(root: Path, folder: Path) -> None:
    """Make `folder` and each folder between it and `root`, every one open to the owner alone.

    `mkdir` leaves newly created parent directories at the default mode cut by `umask`, so every level
    from `root` down to `folder` is explicitly set to `PRIVATE_FOLDER_MODE`.
    """
    level = root
    level.mkdir(parents=True, exist_ok=True)
    os.chmod(level, PRIVATE_FOLDER_MODE)
    for part in folder.relative_to(root).parts:
        level = level / part
        level.mkdir(exist_ok=True)
        os.chmod(level, PRIVATE_FOLDER_MODE)


def hash_folder(root: Path, sha256: str) -> Path:
    """Return `<root>/<aa>/<sha256>` after validating a 64-character lowercase hex SHA-256."""
    if not SHA256_PATTERN.fullmatch(sha256):
        raise ValueError("not a SHA-256 hash")
    return root / sha256[:2] / sha256


def _real_hash_folder(root: Path, sha256: str) -> Path | None:
    """`<root>/<aa>/<sha256>` when it and `<aa>` are folders and not links, else None. The root itself is
    configuration and may be a link (a linked data folder); nothing below it is followed."""
    folder = hash_folder(root, sha256)
    for level in (folder.parent, folder):
        try:
            if not stat.S_ISDIR(os.lstat(level).st_mode):
                return None
        except (FileNotFoundError, NotADirectoryError):
            return None
    return folder


def find_stored_original(root: Path, sha256: str) -> Path | None:
    """Return the stored `original.<ext>` path for `sha256`, or `None` when absent. A link, a folder or a FIFO of that
    name, or a linked hash folder, is no original."""
    folder = _real_hash_folder(root, sha256)
    if folder is None:
        return None
    with os.scandir(folder) as entries:
        found = sorted(
            entry.name for entry in entries
            if entry.name.startswith("original.") and entry.is_file(follow_symlinks=False)
        )
    return folder / found[0] if found else None


def sweep_stale_partials(root: Path, now: float | None = None) -> int:
    """Remove `.part` files older than `STALE_PARTIAL_SEC` left by interrupted uploads."""
    partial_dir = root / PARTIAL_DIRNAME
    if not partial_dir.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - STALE_PARTIAL_SEC
    removed = 0
    for entry in os.scandir(partial_dir):
        try:
            if (
                entry.name.endswith(".part")
                and entry.is_file(follow_symlinks=False)
                and entry.stat(follow_symlinks=False).st_mtime < cutoff
            ):
                os.unlink(entry.path)
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def discard_partial(received: Received) -> None:
    """Remove an uncommitted upload's partial file."""
    received.partial.unlink(missing_ok=True)


def commit_received(root: Path, received: Received) -> tuple[Path, bool]:
    """Hardlink a verified partial upload into `<root>/<aa>/<sha256>/original.<ext>` without overwriting."""
    existing = find_stored_original(root, received.sha256)
    if existing is not None:
        discard_partial(received)
        return existing, True
    folder = hash_folder(root, received.sha256)
    ensure_private_folder(root, folder)
    target = folder / f"original.{received.file_type.extension}"
    try:
        os.link(received.partial, target)
    except FileExistsError:
        discard_partial(received)
        return target, True
    os.chmod(target, PRIVATE_FILE_MODE)
    discard_partial(received)
    _fsync_dir(folder)
    return target, False


def _read_record_path(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_stored_record(root: Path, sha256: str) -> dict[str, Any] | None:
    """Read `<root>/<aa>/<sha256>/record.json` if present, else `None`. It is opened without following a link and
    read only when it is a regular file: a link, a folder, a FIFO or a linked hash folder there is no record."""
    folder = _real_hash_folder(root, sha256)
    if folder is None:
        return None
    try:
        # O_NONBLOCK: a FIFO there opens at once (and is refused below) instead of waiting for a writer
        fd = os.open(folder / RECORD_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        # A link there: O_NOFOLLOW refuses it (a folder or a FIFO opens and is refused just below)
        if error.errno == errno.ELOOP:
            return None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return json.loads(b"".join(chunks).decode("utf-8"))


def write_stored_record(root: Path, sha256: str, record: dict[str, Any]) -> dict[str, Any]:
    """Write `record.json` once via an exclusive scratch file and hardlink; return the standing record."""
    folder = hash_folder(root, sha256)
    ensure_private_folder(root, folder)
    path = folder / RECORD_FILENAME
    scratch = folder / f".record-{secrets.token_hex(_RECORD_SCRATCH_TOKEN_BYTES)}.tmp"
    with open(scratch, "x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(scratch, path)
        os.chmod(path, PRIVATE_FILE_MODE)
    except FileExistsError:
        pass
    finally:
        scratch.unlink(missing_ok=True)
    _fsync_dir(folder)
    return _read_record_path(path)


__all__ = [
    "PARTIAL_DIRNAME",
    "RECORD_FILENAME",
    "_fsync_dir",
    "close_existing_root",
    "commit_received",
    "discard_partial",
    "ensure_private_folder",
    "find_stored_original",
    "hash_folder",
    "read_stored_record",
    "sweep_stale_partials",
    "write_stored_record",
]
