"""Pure magic-byte signature and Content-Type header classification for uploads."""

from __future__ import annotations

from dataclasses import dataclass

from services.ingest.store_types import (
    DECLARABLE,
    FileType,
    HEIF_GENERIC_BRANDS,
    HEIF_STILL_BRANDS,
    MP4_BRANDS,
    QUICKTIME_BRANDS,
    STILL_FAMILIES,
)

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
_FTYP_MIN_HEADER_BYTES = 12
_FTYP_BOX_TYPE = b"ftyp"
_FTYP_COMPAT_START = 16
_FTYP_BRAND_BYTES = 4


@dataclass(frozen=True)
class UnsupportedIsoBrand:
    """An ISO base media `ftyp` header whose major/compatible brands are not supported."""

    brand: str


@dataclass(frozen=True)
class UnknownSignature:
    """Bytes that match none of the supported container or image signatures."""


def classify_head(head: bytes) -> FileType | UnsupportedIsoBrand | UnknownSignature:
    """Classify the leading bytes of an upload without raising HTTP transport exceptions."""
    if head.startswith(_JPEG_MAGIC):
        return FileType("image/jpeg", "jpg", "jpeg", "image")
    if head.startswith(_PNG_MAGIC):
        return FileType("image/png", "png", "png", "image")
    if head.startswith(_TIFF_MAGICS):
        return FileType("image/tiff", "tif", "tiff", "image")
    if len(head) >= _FTYP_MIN_HEADER_BYTES and head[4:8] == _FTYP_BOX_TYPE:
        major = head[8:12]
        box_end = min(int.from_bytes(head[0:4], "big"), len(head))
        compatible = {
            head[i : i + _FTYP_BRAND_BYTES]
            for i in range(_FTYP_COMPAT_START, box_end - (_FTYP_BRAND_BYTES - 1), _FTYP_BRAND_BYTES)
        }
        if major in HEIF_STILL_BRANDS or (major in HEIF_GENERIC_BRANDS and b"heic" in compatible):
            return FileType("image/heic", "heic", "heif", "image")
        if major in QUICKTIME_BRANDS:
            return FileType("video/quicktime", "mov", "isobmff-video", "video")
        if major in MP4_BRANDS:
            return FileType("video/mp4", "mp4", "isobmff-video", "video")
        return UnsupportedIsoBrand(major.decode("latin-1"))
    return UnknownSignature()


def normalize_content_type(declared: str | None) -> str:
    """Extract the lowercase media-type token before any `;` parameters."""
    return (declared or "").split(";", 1)[0].strip().lower()


def is_declarable_still(declared: str | None) -> bool:
    """True when `declared` names one of the supported still-image MIME aliases."""
    name = normalize_content_type(declared)
    return any(name in DECLARABLE[family] for family in STILL_FAMILIES)


__all__ = [
    "UnknownSignature",
    "UnsupportedIsoBrand",
    "classify_head",
    "is_declarable_still",
    "normalize_content_type",
]
