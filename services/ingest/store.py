"""Content-addressed, write-once storage for uploads (roadmap Track 1 M1).

An upload streams into a partial file inside the store root (never the system temp folder), hashed and
counted as it arrives, under a size cap: 256 MiB for a still, 2 GiB for a clip. Its type is read from
its first bytes and must agree with the type the client declared. Once the caller has read the whole
file (`probe`, `metadata`), `commit` links it to `<root>/<aa>/<sha256>/original.<ext>` without
overwriting: `os.link` fails when the name exists, so a race cannot replace a stored file. The stored
file is made read-only. Every path comes from the hash, never from anything the client sent. Partial
files an interrupted upload left behind are removed after a day, when the store is next opened.
Uploads are the owner's own media: they stay on this machine, in a folder git ignores (`data/ingest/`
by default).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

ROOT_ENV = "ALBEDIAN_INGEST_DIR"
MAX_BYTES_ENV = "ALBEDIAN_INGEST_MAX_BYTES"
MAX_STILL_BYTES_ENV = "ALBEDIAN_INGEST_MAX_STILL_BYTES"
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "ingest"
# The largest clip; a still is capped lower, since its whole file is read into memory to be checked
DEFAULT_MAX_BYTES = 2 * 1024**3
DEFAULT_MAX_STILL_BYTES = 256 * 1024**2
# A partial file older than this belongs to no upload still running
STALE_PARTIAL_SEC = 24 * 3600
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
# Enough for an ISO base media `ftyp` box with its first compatible brands
SNIFF_BYTES = 64
# An original and its record can hold a capture time and a position: only the owner's account reads a stored
# file, and only it lists or enters a store folder
PRIVATE_FILE_MODE = 0o400
PRIVATE_FOLDER_MODE = 0o700

# ISO base media brands, by what the file holds
HEIF_STILL_BRANDS = {b"heic", b"heix", b"heim", b"heis"}
HEIF_GENERIC_BRANDS = {b"mif1"}
QUICKTIME_BRANDS = {b"qt  "}
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"mmp4"}


class Refused(Exception):
    """An upload the store will not keep: the HTTP status to answer with and the reason, in plain words."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class FileType:
    """What the first bytes say the file is."""

    mime_type: str
    extension: str
    family: Literal["jpeg", "png", "tiff", "heif", "isobmff-video"]
    media_kind: Literal["image", "video"]


# The types a client may declare for each family; the stored type is always the one read from the bytes
DECLARABLE = {
    "jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    "png": {"image/png"},
    "tiff": {"image/tiff", "image/tif"},
    "heif": {"image/heic", "image/heif"},
    "isobmff-video": {"video/quicktime", "video/mp4", "video/x-m4v"},
}


def sniff(head: bytes) -> FileType:
    """The file's type from its first bytes, or `Refused` (415) naming what is not supported."""
    if head.startswith(b"\xff\xd8\xff"):
        return FileType("image/jpeg", "jpg", "jpeg", "image")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileType("image/png", "png", "png", "image")
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return FileType("image/tiff", "tif", "tiff", "image")
    if len(head) >= 12 and head[4:8] == b"ftyp":
        major = head[8:12]
        box_end = min(int.from_bytes(head[0:4], "big"), len(head))
        compatible = {head[i:i + 4] for i in range(16, box_end - 3, 4)}
        if major in HEIF_STILL_BRANDS or (major in HEIF_GENERIC_BRANDS and b"heic" in compatible):
            return FileType("image/heic", "heic", "heif", "image")
        if major in QUICKTIME_BRANDS:
            return FileType("video/quicktime", "mov", "isobmff-video", "video")
        if major in MP4_BRANDS:
            return FileType("video/mp4", "mp4", "isobmff-video", "video")
        raise Refused(415, f"an ISO base media file with brand {major.decode('latin-1')!r}, which is not supported")
    raise Refused(415, "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file")


def check_declared(file_type: FileType, declared: Optional[str]) -> None:
    """The client's Content-Type must name the same kind of file as its bytes."""
    if not declared:
        raise Refused(415, "the upload must declare its Content-Type")
    name = declared.split(";", 1)[0].strip().lower()
    if name not in DECLARABLE[file_type.family]:
        raise Refused(415, f"declared as {name}, but the bytes are {file_type.mime_type}")


@dataclass(frozen=True)
class Received:
    """A whole upload in its partial file, before it is checked and committed."""

    partial: Path
    sha256: str
    byte_size: int
    file_type: FileType


STILL_FAMILIES = ("jpeg", "png", "tiff", "heif")


def _too_large(limit: int, kind: Optional[str]) -> Refused:
    return Refused(413, f"larger than the {limit}-byte limit" + (f" for a {kind}" if kind else ""))


class IngestStore:
    """The write-once store under `root`: clips up to `max_bytes`, stills up to `max_still_bytes`."""

    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_BYTES, max_still_bytes: int = DEFAULT_MAX_STILL_BYTES) -> None:
        if max_bytes <= 0 or max_still_bytes <= 0:
            raise ValueError("the size limits must be positive")
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.max_still_bytes = min(max_still_bytes, max_bytes)
        # A root that already holds files from before is closed to other accounts as soon as the store opens it
        if self.root.is_dir():
            os.chmod(self.root, PRIVATE_FOLDER_MODE)
        self.sweep_partials()

    @classmethod
    def from_environment(cls) -> "IngestStore":
        root = Path(os.environ.get(ROOT_ENV) or DEFAULT_ROOT)
        max_bytes = int(os.environ.get(MAX_BYTES_ENV) or DEFAULT_MAX_BYTES)
        max_still_bytes = int(os.environ.get(MAX_STILL_BYTES_ENV) or DEFAULT_MAX_STILL_BYTES)
        return cls(root, max_bytes, max_still_bytes)

    def limit_for(self, media_kind: str) -> int:
        return self.max_still_bytes if media_kind == "image" else self.max_bytes

    def sweep_partials(self, now: Optional[float] = None) -> int:
        """Remove partial files older than a day, which an interrupted upload left behind; returns how many."""
        partial_dir = self.root / ".partial"
        if not partial_dir.is_dir():
            return 0
        cutoff = (time.time() if now is None else now) - STALE_PARTIAL_SEC
        removed = 0
        for entry in os.scandir(partial_dir):
            try:
                if (entry.name.endswith(".part") and entry.is_file(follow_symlinks=False)
                        and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                    os.unlink(entry.path)
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def folder(self, sha256: str) -> Path:
        """Where a file with this hash lives; only a 64-character lowercase hex hash names a folder."""
        if not SHA256_PATTERN.fullmatch(sha256):
            raise ValueError("not a SHA-256 hash")
        return self.root / sha256[:2] / sha256

    def original(self, sha256: str) -> Optional[Path]:
        """The stored original for this hash, if there is one."""
        folder = self.folder(sha256)
        if not folder.is_dir():
            return None
        found = sorted(folder.glob("original.*"))
        return found[0] if found else None

    async def receive(self, chunks: AsyncIterator[bytes], declared: Optional[str], declared_length: Optional[int] = None) -> Received:
        """Stream an upload into a partial file, hashing and counting it; refuse it as soon as it fails a rule."""
        if declared_length is not None:
            name = (declared or "").split(";", 1)[0].strip().lower()
            still = any(name in DECLARABLE[family] for family in STILL_FAMILIES)
            limit = self.max_still_bytes if still else self.max_bytes
            if declared_length > limit:
                raise _too_large(limit, "still" if still else None)
        partial_dir = self.root / ".partial"
        self._private_folder(partial_dir)
        partial = partial_dir / f"{secrets.token_hex(16)}.part"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest = hashlib.sha256()
        size = 0
        head = b""
        file_type: Optional[FileType] = None
        # Until the first bytes say what the file is, only the largest limit applies
        limit, kind = self.max_bytes, None
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if file_type is None:
                    head += chunk[: SNIFF_BYTES - len(head)]
                    if len(head) >= SNIFF_BYTES:
                        file_type = sniff(head)
                        check_declared(file_type, declared)
                        kind = "still" if file_type.media_kind == "image" else "clip"
                        limit = self.limit_for(file_type.media_kind)
                if size > limit:
                    raise _too_large(limit, kind)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            if size == 0:
                raise Refused(422, "the upload is empty")
            if file_type is None:
                file_type = sniff(head)
                check_declared(file_type, declared)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            partial.unlink(missing_ok=True)
            raise
        os.close(fd)
        return Received(partial, digest.hexdigest(), size, file_type)

    def discard(self, received: Received) -> None:
        """Remove an upload's partial file; the store keeps nothing of it."""
        received.partial.unlink(missing_ok=True)

    def commit(self, received: Received) -> tuple[Path, bool]:
        """Link a checked upload into place, never over an existing file. Returns its path and whether it was
        already stored."""
        existing = self.original(received.sha256)
        if existing is not None:
            self.discard(received)
            return existing, True
        folder = self.folder(received.sha256)
        self._private_folder(folder)
        target = folder / f"original.{received.file_type.extension}"
        try:
            os.link(received.partial, target)
        except FileExistsError:
            self.discard(received)
            return target, True
        os.chmod(target, PRIVATE_FILE_MODE)
        self.discard(received)
        _fsync_dir(folder)
        return target, False

    def _private_folder(self, folder: Path) -> None:
        """Make `folder` and each folder between it and the root, every one open to the owner alone. mkdir's own
        mode leaves the parents it makes at the default and is cut by the umask, so each level is set here."""
        level = self.root
        level.mkdir(parents=True, exist_ok=True)
        os.chmod(level, PRIVATE_FOLDER_MODE)
        for part in folder.relative_to(self.root).parts:
            level = level / part
            level.mkdir(exist_ok=True)
            os.chmod(level, PRIVATE_FOLDER_MODE)

    def read_record(self, sha256: str) -> Optional[dict]:
        """The stored record (asset and hints) for this hash, if there is one."""
        path = self.folder(sha256) / "record.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_record(self, sha256: str, record: dict) -> dict:
        """Write the record once; when one exists already, it stands and is returned."""
        folder = self.folder(sha256)
        self._private_folder(folder)
        path = folder / "record.json"
        scratch = folder / f".record-{secrets.token_hex(8)}.tmp"
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
        return json.loads(path.read_text(encoding="utf-8"))


def _fsync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
