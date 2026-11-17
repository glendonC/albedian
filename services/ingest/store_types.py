"""Domain constants, upload classification records, and refusal errors for the ingest store."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Literal

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

# The types a client may declare for each family; the stored type is always the one read from the bytes
DECLARABLE = {
    "jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    "png": {"image/png"},
    "tiff": {"image/tiff", "image/tif"},
    "heif": {"image/heic", "image/heif"},
    "isobmff-video": {"video/quicktime", "video/mp4", "video/x-m4v"},
}

STILL_FAMILIES = ("jpeg", "png", "tiff", "heif")


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


@dataclass(frozen=True)
class Received:
    """A whole upload in its partial file, before it is checked and committed."""

    partial: Path
    sha256: str
    byte_size: int
    file_type: FileType


def read_store_environment() -> tuple[Path, int, int]:
    """Read `(root, max_bytes, max_still_bytes)` from environment variables with defaults."""
    root = Path(os.environ.get(ROOT_ENV) or DEFAULT_ROOT)
    max_bytes = int(os.environ.get(MAX_BYTES_ENV) or DEFAULT_MAX_BYTES)
    max_still_bytes = int(os.environ.get(MAX_STILL_BYTES_ENV) or DEFAULT_MAX_STILL_BYTES)
    return root, max_bytes, max_still_bytes


__all__ = [
    "DECLARABLE",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_STILL_BYTES",
    "DEFAULT_ROOT",
    "FileType",
    "HEIF_GENERIC_BRANDS",
    "HEIF_STILL_BRANDS",
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
    "read_store_environment",
]
