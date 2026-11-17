"""Streaming partial-file writer, incremental SHA-256 hashing, and byte-cap enforcement."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import hashlib
import os
from pathlib import Path
import secrets

from services.ingest.store_fs import PARTIAL_DIRNAME, ensure_private_folder
from services.ingest.store_types import FileType, Received, SNIFF_BYTES

_PARTIAL_TOKEN_BYTES = 16
_PARTIAL_OPEN_MODE = 0o600


async def stream_upload_chunks(
    root: Path,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    limit_for_kind: Callable[[str], int],
    inspect_head: Callable[[bytes], FileType],
    on_over_limit: Callable[[int, str | None], BaseException],
    on_empty: Callable[[], BaseException],
) -> Received:
    """Stream `chunks` into an exclusive `.part` file under `root/.partial`, cleaning up on any failure."""
    partial_dir = root / PARTIAL_DIRNAME
    ensure_private_folder(root, partial_dir)
    partial = partial_dir / f"{secrets.token_hex(_PARTIAL_TOKEN_BYTES)}.part"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PARTIAL_OPEN_MODE)
    digest = hashlib.sha256()
    size = 0
    head = b""
    file_type: FileType | None = None
    # Until the first bytes say what the file is, only the largest limit applies
    limit: int = max_bytes
    kind: str | None = None
    try:
        async for chunk in chunks:
            if not chunk:
                continue
            size += len(chunk)
            if file_type is None:
                head += chunk[: SNIFF_BYTES - len(head)]
                if len(head) >= SNIFF_BYTES:
                    file_type = inspect_head(head)
                    kind = "still" if file_type.media_kind == "image" else "clip"
                    limit = limit_for_kind(file_type.media_kind)
            if size > limit:
                raise on_over_limit(limit, kind)
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        if size == 0:
            raise on_empty()
        if file_type is None:
            file_type = inspect_head(head)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        partial.unlink(missing_ok=True)
        raise
    os.close(fd)
    return Received(partial, digest.hexdigest(), size, file_type)


__all__ = [
    "stream_upload_chunks",
]
