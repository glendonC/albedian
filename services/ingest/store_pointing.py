"""The photo's pointing record, kept beside its receipt (ios-capture-companion design component 4, pointing-2b).

The record is the exact body bytes the route received, written once to `<root>/<aa>/<sha256>/pointing.json`: through
an exclusive scratch file and `os.link`, which fails when the name exists, so of two records sent at once exactly
one stands and a stored record is never replaced. It is private like the photo (0400 in a 0700 folder). It is read
without following a link, and only as a regular file. Nothing here logs, and no error here holds a byte of a record.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import secrets
import stat

from services.ingest.store_fs import _fsync_dir, hash_folder
from services.ingest.store_types import PRIVATE_FILE_MODE

POINTING_FILENAME = "pointing.json"
_SCRATCH_TOKEN_BYTES = 8


class PointingUnreadable(Exception):
    """Something is at the record's place that is not a regular file the store can read; never its bytes."""


def read_stored_pointing(root: Path, sha256: str) -> bytes | None:
    """The stored record's bytes, None when there is none; `PointingUnreadable` for a link or anything not a file."""
    path = hash_folder(root, sha256) / POINTING_FILENAME
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EISDIR, errno.EACCES, errno.ENOTDIR):
            raise PointingUnreadable("the stored pointing record is not a regular file") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PointingUnreadable("the stored pointing record is not a regular file")
        chunks = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_stored_pointing(root: Path, sha256: str, body: bytes) -> tuple[bytes, bool]:
    """Keep `body` as the photo's record unless one is held; returns the standing bytes and whether this call
    stored them. The photo's folder must exist already (FileNotFoundError otherwise): a record is kept only beside a
    stored photo."""
    folder = hash_folder(root, sha256)
    path = folder / POINTING_FILENAME
    scratch = folder / f".pointing-{secrets.token_hex(_SCRATCH_TOKEN_BYTES)}.tmp"
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(scratch, PRIVATE_FILE_MODE)
        try:
            os.link(scratch, path, follow_symlinks=False)
            stored = True
        except FileExistsError:
            stored = False
    finally:
        scratch.unlink(missing_ok=True)
    _fsync_dir(folder)
    standing = read_stored_pointing(root, sha256)
    if standing is None:
        raise FileNotFoundError(errno.ENOENT, "the pointing record went missing while it was stored")
    return standing, stored


__all__ = ["POINTING_FILENAME", "PointingUnreadable", "read_stored_pointing", "write_stored_pointing"]
