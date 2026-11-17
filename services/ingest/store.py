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

from pathlib import Path as _Path

from services.ingest.store_fs import (
    _fsync_dir,
    close_existing_root as _close_existing_root,
    commit_received as _commit_received,
    discard_partial as _discard_partial,
    ensure_private_folder as _ensure_private_folder,
    find_stored_original as _find_stored_original,
    hash_folder as _hash_folder,
    read_stored_record as _read_stored_record,
    sweep_stale_partials as _sweep_stale_partials,
    write_stored_record as _write_stored_record,
)
from services.ingest.store_sniff import (
    UnsupportedIsoBrand as _UnsupportedIsoBrand,
    classify_head as _classify_head,
    is_declarable_still as _is_declarable_still,
    normalize_content_type as _normalize_content_type,
)
from services.ingest.store_stream import stream_upload_chunks as _stream_upload_chunks
from services.ingest.store_types import (
    DECLARABLE,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_STILL_BYTES,
    DEFAULT_ROOT,
    FileType,
    HEIF_GENERIC_BRANDS,
    HEIF_STILL_BRANDS,
    MAX_BYTES_ENV,
    MAX_STILL_BYTES_ENV,
    MP4_BRANDS,
    PRIVATE_FILE_MODE,
    PRIVATE_FOLDER_MODE,
    QUICKTIME_BRANDS,
    ROOT_ENV,
    Received,
    Refused,
    SHA256_PATTERN,
    SNIFF_BYTES,
    STALE_PARTIAL_SEC,
    STILL_FAMILIES,
    read_store_environment as _read_store_environment,
)


def sniff(head: "bytes") -> "FileType":
    """The file's type from its first bytes, or `Refused` (415) naming what is not supported."""
    result = _classify_head(head)
    if isinstance(result, FileType):
        return result
    if isinstance(result, _UnsupportedIsoBrand):
        raise Refused(415, f"an ISO base media file with brand {result.brand!r}, which is not supported")
    raise Refused(415, "not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file")


def check_declared(file_type: "FileType", declared: "Optional[str]") -> "None":
    """The client's Content-Type must name the same kind of file as its bytes."""
    if not declared:
        raise Refused(415, "the upload must declare its Content-Type")
    name = _normalize_content_type(declared)
    if name not in DECLARABLE[file_type.family]:
        raise Refused(415, f"declared as {name}, but the bytes are {file_type.mime_type}")


def _too_large(limit: "int", kind: "Optional[str]") -> "Refused":
    return Refused(413, f"larger than the {limit}-byte limit" + (f" for a {kind}" if kind else ""))


def _sniff_and_check(head: "bytes", declared: "Optional[str]") -> "FileType":
    file_type = sniff(head)
    check_declared(file_type, declared)
    return file_type


class IngestStore:
    """The write-once store under `root`: clips up to `max_bytes`, stills up to `max_still_bytes`."""

    def __init__(
        self,
        root: "Path",
        max_bytes: "int" = DEFAULT_MAX_BYTES,
        max_still_bytes: "int" = DEFAULT_MAX_STILL_BYTES,
    ) -> "None":
        if max_bytes <= 0 or max_still_bytes <= 0:
            raise ValueError("the size limits must be positive")
        self.root = _Path(root)
        self.max_bytes = max_bytes
        self.max_still_bytes = min(max_still_bytes, max_bytes)
        _close_existing_root(self.root)
        self.sweep_partials()

    @classmethod
    def from_environment(cls) -> "'IngestStore'":
        root, max_bytes, max_still_bytes = _read_store_environment()
        return cls(root, max_bytes, max_still_bytes)

    def limit_for(self, media_kind: "str") -> "int":
        return self.max_still_bytes if media_kind == "image" else self.max_bytes

    def sweep_partials(self, now: "Optional[float]" = None) -> "int":
        """Remove partial files older than a day, which an interrupted upload left behind; returns how many."""
        return _sweep_stale_partials(self.root, now)

    def folder(self, sha256: "str") -> "Path":
        """Where a file with this hash lives; only a 64-character lowercase hex hash names a folder."""
        return _hash_folder(self.root, sha256)

    def original(self, sha256: "str") -> "Optional[Path]":
        """The stored original for this hash, if there is one."""
        return _find_stored_original(self.root, sha256)

    async def receive(
        self,
        chunks: "AsyncIterator[bytes]",
        declared: "Optional[str]",
        declared_length: "Optional[int]" = None,
    ) -> "Received":
        """Stream an upload into a partial file, hashing and counting it; refuse it as soon as it fails a rule."""
        if declared_length is not None:
            still = _is_declarable_still(declared)
            limit = self.max_still_bytes if still else self.max_bytes
            if declared_length > limit:
                raise _too_large(limit, "still" if still else None)
        return await _stream_upload_chunks(
            root=self.root,
            chunks=chunks,
            max_bytes=self.max_bytes,
            limit_for_kind=self.limit_for,
            inspect_head=lambda head: _sniff_and_check(head, declared),
            on_over_limit=_too_large,
            on_empty=lambda: Refused(422, "the upload is empty"),
        )

    def discard(self, received: "Received") -> "None":
        """Remove an upload's partial file; the store keeps nothing of it."""
        _discard_partial(received)

    def commit(self, received: "Received") -> "tuple[Path, bool]":
        """Link a checked upload into place, never over an existing file."""
        return _commit_received(self.root, received)

    def _private_folder(self, folder: "Path") -> "None":
        """Make `folder` and each folder between it and the root, every one open to the owner alone."""
        _ensure_private_folder(self.root, folder)

    def read_record(self, sha256: "str") -> "Optional[dict]":
        """The stored record (asset and hints) for this hash, if there is one."""
        return _read_stored_record(self.root, sha256)

    def write_record(self, sha256: "str", record: "dict") -> "dict":
        """Write the record once; when one exists already, it stands and is returned."""
        return _write_stored_record(self.root, sha256, record)


__all__ = [
    "DECLARABLE",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_STILL_BYTES",
    "DEFAULT_ROOT",
    "FileType",
    "HEIF_GENERIC_BRANDS",
    "HEIF_STILL_BRANDS",
    "IngestStore",
    "MAX_BYTES_ENV",
    "MAX_STILL_BYTES_ENV",
    "MP4_BRANDS",
    "PRIVATE_FILE_MODE",
    "PRIVATE_FOLDER_MODE",
    "QUICKTIME_BRANDS",
    "ROOT_ENV",
    "Received",
    "Refused",
    "SHA256_PATTERN",
    "SNIFF_BYTES",
    "STALE_PARTIAL_SEC",
    "STILL_FAMILIES",
    "check_declared",
    "sniff",
]
