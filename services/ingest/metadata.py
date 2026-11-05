"""Capture metadata read from an upload as hints, each naming the container and the tags it came from.

Missing stays missing: a hint is absent when the file does not hold it, and a value that is malformed
or out of range is dropped, never repaired. A capture time written without a zone stays local. For a
clip, Apple's `com.apple.quicktime.creationdate` (when the clip was recorded) wins; QuickTime's
`creation_time` is used only when it is absent, and is marked `file_written`, since it may be when the
file was written or exported rather than recorded.

Stills: EXIF through Pillow (JPEG, PNG, TIFF), and for HEIC through `read_heif`, a small reader of the
ISO base media boxes that finds the primary image's size and its EXIF item. The reader works on slices
of the bytes it is given, bounds every count, offset and length by the input, and refuses anything else
with `HeifError` and its reason.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from PIL import Image

from services.contracts.ingest import UTC_OFFSET_LIMIT_MINUTES, format_utc

# ---------------------------------------------------------------------------------------------
# HEIF (ISO base media) reader
# ---------------------------------------------------------------------------------------------

MAX_DEPTH = 8
MAX_BOXES = 4096
MAX_ITEMS = 1024
MAX_EXTENTS = 4096
MAX_EXIF_BYTES = 4 * 1024 * 1024
TIFF_HEADERS = (b"II*\x00", b"MM\x00*")


class HeifError(ValueError):
    """A HEIF file this reader will not read, with the reason."""


@dataclass(frozen=True)
class HeifInfo:
    width: int
    height: int
    # The EXIF block from its TIFF header on, or None when the file has no EXIF item
    exif: Optional[bytes]


class _Cursor:
    """Reads big-endian fields from a slice, refusing any read past its end."""

    def __init__(self, view: memoryview, what: str) -> None:
        self.view = view
        self.pos = 0
        self.what = what

    def uint(self, size: int) -> int:
        if size == 0:
            return 0
        if size not in (1, 2, 4, 8):
            raise HeifError(f"{self.what}: a field of {size} bytes")
        if self.pos + size > len(self.view):
            raise HeifError(f"{self.what} ends early")
        value = int.from_bytes(self.view[self.pos:self.pos + size], "big")
        self.pos += size
        return value

    def raw(self, size: int) -> bytes:
        if self.pos + size > len(self.view):
            raise HeifError(f"{self.what} ends early")
        value = bytes(self.view[self.pos:self.pos + size])
        self.pos += size
        return value


def _children(view: memoryview, depth: int, budget: list[int]) -> list[tuple[bytes, memoryview]]:
    """The boxes in `view`, each as its type and body; refuses a box that runs past its parent."""
    if depth > MAX_DEPTH:
        raise HeifError(f"boxes nested deeper than {MAX_DEPTH}")
    boxes = []
    pos = 0
    end = len(view)
    while pos < end:
        if end - pos < 8:
            raise HeifError("a box header runs past its parent")
        size = int.from_bytes(view[pos:pos + 4], "big")
        kind = bytes(view[pos + 4:pos + 8])
        header = 8
        if size == 1:
            if end - pos < 16:
                raise HeifError("a large box header runs past its parent")
            size = int.from_bytes(view[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or size > end - pos:
            raise HeifError(f"box {kind!r} runs past its parent")
        budget[0] -= 1
        if budget[0] < 0:
            raise HeifError(f"more than {MAX_BOXES} boxes")
        boxes.append((kind, view[pos + header:pos + size]))
        pos += size
    return boxes


def _full_box(body: memoryview, what: str) -> tuple[int, int, memoryview]:
    if len(body) < 4:
        raise HeifError(f"{what} ends early")
    return body[0], int.from_bytes(body[1:4], "big"), body[4:]


def _one(boxes: list[tuple[bytes, memoryview]], kind: bytes, where: str) -> memoryview:
    found = [body for k, body in boxes if k == kind]
    if len(found) != 1:
        raise HeifError(f"{where} holds {len(found)} {kind.decode('latin-1')!r} boxes, not one")
    return found[0]


def read_heif(data: bytes) -> HeifInfo:
    """The primary image's size and EXIF block of a HEIF still, or `HeifError` with the reason."""
    view = memoryview(data)
    budget = [MAX_BOXES]
    top = _children(view, 0, budget)
    if not top or top[0][0] != b"ftyp":
        raise HeifError("the file does not start with an ftyp box")
    meta_version, _, meta_body = _full_box(_one(top, b"meta", "the file"), "meta")
    if meta_version != 0:
        raise HeifError(f"meta version {meta_version}")
    meta = _children(meta_body, 1, budget)

    _, _, hdlr = _full_box(_one(meta, b"hdlr", "meta"), "hdlr")
    if bytes(hdlr[4:8]) != b"pict":
        raise HeifError("meta does not describe a picture")

    pitm_version, _, pitm = _full_box(_one(meta, b"pitm", "meta"), "pitm")
    primary = _Cursor(pitm, "pitm").uint(2 if pitm_version == 0 else 4)

    items = _item_types(_one(meta, b"iinf", "meta"), budget)
    if primary not in items:
        raise HeifError("the primary item is not listed")
    locations = _item_locations(_one(meta, b"iloc", "meta"))
    width, height = _primary_size(_one(meta, b"iprp", "meta"), primary, budget)

    exif_ids = sorted(item for item, kind in items.items() if kind == b"Exif")
    exif = None
    if exif_ids:
        idat = [body for kind, body in meta if kind == b"idat"]
        payload = _item_bytes(view, idat[0] if len(idat) == 1 else None, locations.get(exif_ids[0]))
        if len(payload) < 4:
            raise HeifError("the EXIF item is shorter than its header")
        start = 4 + int.from_bytes(payload[0:4], "big")
        if start + 8 > len(payload):
            raise HeifError("the EXIF item's TIFF header lies past its end")
        exif = payload[start:]
        if not exif.startswith(TIFF_HEADERS):
            raise HeifError("the EXIF item has no TIFF header where it says")
    return HeifInfo(width, height, exif)


def _item_types(iinf: memoryview, budget: list[int]) -> dict[int, bytes]:
    version, _, body = _full_box(iinf, "iinf")
    cursor = _Cursor(body, "iinf")
    count = cursor.uint(2 if version == 0 else 4)
    if count > MAX_ITEMS:
        raise HeifError(f"more than {MAX_ITEMS} items")
    entries = _children(body[cursor.pos:], 2, budget)
    types: dict[int, bytes] = {}
    for kind, entry in entries:
        if kind != b"infe":
            continue
        infe_version, _, infe = _full_box(entry, "infe")
        if infe_version < 2:
            raise HeifError(f"infe version {infe_version}")
        reader = _Cursor(infe, "infe")
        item = reader.uint(2 if infe_version == 2 else 4)
        reader.uint(2)
        types[item] = reader.raw(4)
    if len(types) != count:
        raise HeifError(f"iinf lists {count} items but describes {len(types)}")
    return types


def _item_locations(iloc: memoryview) -> dict[int, tuple[int, list[tuple[int, int]]]]:
    version, _, body = _full_box(iloc, "iloc")
    if version > 2:
        raise HeifError(f"iloc version {version}")
    cursor = _Cursor(body, "iloc")
    sizes = cursor.uint(1)
    offset_size, length_size = sizes >> 4, sizes & 15
    sizes = cursor.uint(1)
    base_size, index_size = sizes >> 4, (sizes & 15 if version in (1, 2) else 0)
    count = cursor.uint(2 if version < 2 else 4)
    if count > MAX_ITEMS:
        raise HeifError(f"more than {MAX_ITEMS} item locations")
    locations: dict[int, tuple[int, list[tuple[int, int]]]] = {}
    extents_left = MAX_EXTENTS
    for _ in range(count):
        item = cursor.uint(2 if version < 2 else 4)
        method = cursor.uint(2) & 15 if version in (1, 2) else 0
        cursor.uint(2)
        base = cursor.uint(base_size)
        extent_count = cursor.uint(2)
        extents_left -= extent_count
        if extents_left < 0:
            raise HeifError(f"more than {MAX_EXTENTS} extents")
        extents = []
        for _ in range(extent_count):
            cursor.uint(index_size)
            extents.append((base + cursor.uint(offset_size), cursor.uint(length_size)))
        locations[item] = (method, extents)
    return locations


def _primary_size(iprp: memoryview, primary: int, budget: list[int]) -> tuple[int, int]:
    parts = _children(iprp, 2, budget)
    properties = _children(_one(parts, b"ipco", "iprp"), 3, budget)
    ipma_version, ipma_flags, ipma = _full_box(_one(parts, b"ipma", "iprp"), "ipma")
    cursor = _Cursor(ipma, "ipma")
    count = cursor.uint(4)
    if count > MAX_ITEMS:
        raise HeifError(f"more than {MAX_ITEMS} property associations")
    associated: list[int] = []
    for _ in range(count):
        item = cursor.uint(2 if ipma_version < 1 else 4)
        links = cursor.uint(1)
        for _ in range(links):
            value = cursor.uint(2 if ipma_flags & 1 else 1)
            index = value & (0x7FFF if ipma_flags & 1 else 0x7F)
            if item == primary and index:
                associated.append(index)
    for index in associated:
        if index > len(properties):
            raise HeifError("a property association points past the property list")
        kind, body = properties[index - 1]
        if kind == b"ispe":
            _, _, ispe = _full_box(body, "ispe")
            reader = _Cursor(ispe, "ispe")
            width, height = reader.uint(4), reader.uint(4)
            if width <= 0 or height <= 0:
                raise HeifError("the primary image has no size")
            return width, height
    raise HeifError("the primary image has no ispe size")


def _item_bytes(view: memoryview, idat: Optional[memoryview], location: Optional[tuple[int, list[tuple[int, int]]]]) -> bytes:
    if location is None:
        raise HeifError("the EXIF item has no location")
    method, extents = location
    if method == 0:
        source = view
    elif method == 1 and idat is not None:
        source = idat
    else:
        raise HeifError(f"an item stored with construction method {method}")
    total = 0
    pieces = []
    for offset, length in extents:
        if length == 0:
            length = len(source) - offset
        if offset < 0 or length < 0 or offset + length > len(source):
            raise HeifError("an item extent lies outside the file")
        total += length
        if total > MAX_EXIF_BYTES:
            raise HeifError(f"an EXIF item larger than {MAX_EXIF_BYTES} bytes")
        pieces.append(source[offset:offset + length])
    return b"".join(pieces)


# ---------------------------------------------------------------------------------------------
# EXIF hints (stills)
# ---------------------------------------------------------------------------------------------

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
EXIF_DATE = re.compile(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")
EXIF_OFFSET = re.compile(r"([+-])(\d{2}):(\d{2})")
CAMERA_LIMITS = {
    "focalLengthMm": 2000,
    "focalLength35mmMm": 5000,
    "exposureTimeSec": 3600,
    "fNumber": 64,
    "iso": 10_000_000,
}


def _text(value: Any) -> Optional[str]:
    """A tag's text, trimmed; None unless it is 1 to 128 printable characters."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="strict") if value.isascii() else None
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", " ").strip()
    if not value or len(value) > 128 or not value.isprintable():
        return None
    return value


def _number(value: Any) -> Optional[float]:
    """A tag's positive finite number (a rational, int or float; the first of a list); else None."""
    if isinstance(value, (tuple, list)):
        if not value:
            return None
        value = value[0]
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _local_time(year: int, month: int, day: int, hour: int, minute: int, second: int, millis: int) -> Optional[datetime]:
    try:
        return datetime(year, month, day, hour, minute, second, millis * 1000)
    except ValueError:
        return None


def _format_local(moment: datetime) -> str:
    millis = moment.microsecond // 1000
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + (f".{millis:03d}" if millis else "")


def _capture_time(local: datetime, offset_minutes: Optional[int], marks: str, source: dict) -> dict:
    utc = None
    if offset_minutes is not None:
        utc = format_utc((local - timedelta(minutes=offset_minutes)).replace(tzinfo=timezone.utc))
    return {
        "localDateTime": _format_local(local),
        "utcOffsetMinutes": offset_minutes,
        "utc": utc,
        "marks": marks,
        "source": source,
    }


def _exif_time(exif_ifd: Any) -> Optional[dict]:
    match = EXIF_DATE.fullmatch(_text(exif_ifd.get(0x9003)) or "")
    if not match:
        return None
    tags = ["DateTimeOriginal"]
    millis = 0
    subsec = _text(exif_ifd.get(0x9291))
    if subsec is not None and subsec.isdigit():
        millis = int((subsec + "000")[:3])
        tags.append("SubSecTimeOriginal")
    local = _local_time(*(int(part) for part in match.groups()), millis)
    if local is None:
        return None
    offset = None
    offset_match = EXIF_OFFSET.fullmatch(_text(exif_ifd.get(0x9011)) or "")
    if offset_match:
        sign, hours, minutes = offset_match.groups()
        value = (int(hours) * 60 + int(minutes)) * (-1 if sign == "-" else 1)
        if int(minutes) < 60 and abs(value) <= UTC_OFFSET_LIMIT_MINUTES:
            offset = value
            tags.append("OffsetTimeOriginal")
    return _capture_time(local, offset, "recorded", {"container": "exif", "tags": tags})


def _degrees(parts: Any, limit: float) -> Optional[float]:
    if not isinstance(parts, (tuple, list)) or len(parts) != 3:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in parts)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if not all(math.isfinite(x) and x >= 0 for x in (degrees, minutes, seconds)) or minutes >= 60 or seconds >= 60:
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return value if value <= limit else None


def _exif_location(gps: Any) -> Optional[dict]:
    if _text(gps.get(9)) == "V":
        return None  # the receiver marked the fix void
    lat_ref, lon_ref = _text(gps.get(1)), _text(gps.get(3))
    lat, lon = _degrees(gps.get(2), 90), _degrees(gps.get(4), 180)
    if lat is None or lon is None or lat_ref not in ("N", "S") or lon_ref not in ("E", "W"):
        return None
    lat = -lat if lat_ref == "S" else lat
    lon = -lon if lon_ref == "W" else lon
    if lat == 0 and lon == 0:
        return None  # zero, zero is what a camera writes without a fix
    hint: dict[str, Any] = {"latDeg": lat, "lonDeg": lon}
    tags = ["GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef"]
    altitude = gps.get(6)
    ref = gps.get(5)
    ref = ref[0] if isinstance(ref, bytes) and len(ref) == 1 else ref
    try:
        altitude_m = float(altitude) if altitude is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        altitude_m = None
    if altitude_m is not None and math.isfinite(altitude_m) and altitude_m >= 0 and ref in (0, 1):
        hint["altitudeM"] = -altitude_m if ref == 1 else altitude_m
        tags += ["GPSAltitude", "GPSAltitudeRef"]
    accuracy = _number(gps.get(0x1F))
    if accuracy is not None:
        hint["horizontalAccuracyM"] = accuracy
        tags.append("GPSHPositioningError")
    hint["source"] = {"container": "exif", "tags": tags}
    return hint


def _exif_camera(ifd0: Any, exif_ifd: Any) -> Optional[dict]:
    readers = [
        ("make", "Make", _text(ifd0.get(0x010F))),
        ("model", "Model", _text(ifd0.get(0x0110))),
        ("lensModel", "LensModel", _text(exif_ifd.get(0xA434))),
        ("focalLengthMm", "FocalLength", _number(exif_ifd.get(0x920A))),
        ("focalLength35mmMm", "FocalLengthIn35mmFilm", _number(exif_ifd.get(0xA405))),
        ("exposureTimeSec", "ExposureTime", _number(exif_ifd.get(0x829A))),
        ("fNumber", "FNumber", _number(exif_ifd.get(0x829D))),
        ("iso", "PhotographicSensitivity", _number(exif_ifd.get(0x8827))),
    ]
    return _camera_hint("exif", readers)


def _camera_hint(container: str, readers: list[tuple[str, str, Any]]) -> Optional[dict]:
    hint: dict[str, Any] = {}
    tags = []
    for field, tag, value in readers:
        if value is None or (field in CAMERA_LIMITS and value > CAMERA_LIMITS[field]):
            continue
        hint[field] = value
        tags.append(tag)
    if not hint:
        return None
    hint["source"] = {"container": container, "tags": tags}
    return hint


def exif_hints(exif: Image.Exif) -> dict:
    """Capture time, place and camera from an EXIF block; each absent when the block lacks it."""
    hints: dict[str, Any] = {}
    exif_ifd = exif.get_ifd(EXIF_IFD)
    time = _exif_time(exif_ifd)
    if time is not None:
        hints["captureTime"] = time
    location = _exif_location(exif.get_ifd(GPS_IFD))
    if location is not None:
        hints["location"] = location
    camera = _exif_camera(exif, exif_ifd)
    if camera is not None:
        hints["camera"] = camera
    return hints


# ---------------------------------------------------------------------------------------------
# QuickTime hints (clips)
# ---------------------------------------------------------------------------------------------

APPLE_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})")
UTC_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z")
ISO6709 = re.compile(r"([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?(?:CRS[^/]*)?/?")
# Instants a muxer writes when it has no time: the QuickTime and Unix epochs
EPOCHS = {datetime(1904, 1, 1), datetime(1970, 1, 1)}


def _millis(fraction: Optional[str]) -> int:
    return int((fraction + "000")[:3]) if fraction else 0


def _quicktime_time(tags: dict[str, str]) -> Optional[dict]:
    apple = APPLE_DATE.fullmatch((tags.get("com.apple.quicktime.creationdate") or "").strip())
    if apple:
        *parts, fraction, zone = apple.groups()
        local = _local_time(*(int(part) for part in parts), _millis(fraction))
        offset = 0 if zone == "Z" else (int(zone[1:3]) * 60 + int(zone[-2:])) * (-1 if zone[0] == "-" else 1)
        if local is not None and abs(offset) <= UTC_OFFSET_LIMIT_MINUTES and (zone == "Z" or int(zone[-2:]) < 60):
            return _capture_time(local, offset, "recorded",
                                 {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]})
    written = UTC_DATE.fullmatch((tags.get("creation_time") or "").strip())
    if written:
        *parts, fraction = written.groups()
        moment = _local_time(*(int(part) for part in parts), _millis(fraction))
        if moment is not None and moment not in EPOCHS:
            return {
                "localDateTime": None,
                "utcOffsetMinutes": None,
                "utc": format_utc(moment.replace(tzinfo=timezone.utc)),
                "marks": "file_written",
                "source": {"container": "quicktime", "tags": ["creation_time"]},
            }
    return None


def _quicktime_location(tags: dict[str, str]) -> Optional[dict]:
    for tag in ("com.apple.quicktime.location.ISO6709", "location"):
        match = ISO6709.fullmatch((tags.get(tag) or "").strip())
        if not match:
            continue
        lat, lon = float(match.group(1)), float(match.group(2))
        if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
            return None
        hint: dict[str, Any] = {"latDeg": lat, "lonDeg": lon}
        source_tags = [tag]
        if match.group(3) is not None:
            hint["altitudeM"] = float(match.group(3))
        accuracy = _number(tags.get("com.apple.quicktime.location.accuracy.horizontal"))
        if accuracy is not None:
            hint["horizontalAccuracyM"] = accuracy
            source_tags.append("com.apple.quicktime.location.accuracy.horizontal")
        hint["source"] = {"container": "quicktime", "tags": source_tags}
        return hint
    return None


def _quicktime_camera(tags: dict[str, str]) -> Optional[dict]:
    def first(*names: str) -> tuple[str, Optional[str]]:
        for name in names:
            value = _text(tags.get(name))
            if value is not None:
                return name, value
        return names[0], None

    make_tag, make = first("com.apple.quicktime.make", "make")
    model_tag, model = first("com.apple.quicktime.model", "model")
    lens_tag, lens = first("com.apple.quicktime.camera.lens_model")
    focal_tag = "com.apple.quicktime.camera.focal_length.35mm_equivalent"
    readers = [
        ("make", make_tag, make),
        ("model", model_tag, model),
        ("lensModel", lens_tag, lens),
        ("focalLength35mmMm", focal_tag, _number(tags.get(focal_tag))),
    ]
    return _camera_hint("quicktime", readers)


def quicktime_hints(format_tags: dict[str, str]) -> dict:
    """Capture time, place and camera from a clip's container tags (as ffprobe reports them)."""
    tags = {str(key): str(value) for key, value in format_tags.items()}
    hints: dict[str, Any] = {}
    for name, reader in (("captureTime", _quicktime_time), ("location", _quicktime_location), ("camera", _quicktime_camera)):
        value = reader(tags)
        if value is not None:
            hints[name] = value
    return hints
