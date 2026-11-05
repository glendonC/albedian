"""Hints read from uploads (roadmap Track 1 M1): EXIF from JPEG, PNG, TIFF and HEIC, and QuickTime tags.

Synthetic files only: every value is invented (2031 dates, invented places and cameras), the HEIC is the
fixture described in tests/fixtures/ingest/README.md, and clips are made here by ffmpeg, which CI installs.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import math
import shutil
import subprocess
import tracemalloc
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from PIL import ExifTags, Image, ImageOps, TiffImagePlugin

from services.api import app
from services.contracts.ingest import EXIF_ORIENTATIONS, IngestReceipt
from services.ingest import router as ingest_router
from services.ingest.metadata import (
    MAX_BOXES,
    MAX_EXIF_BYTES,
    HeifError,
    HeifInfo,
    display_matrix_hint,
    exif_hints,
    quicktime_hints,
    read_heif,
)
from services.ingest.probe import probe_media
from services.ingest.store import SNIFF_BYTES, IngestStore, Received, sniff

ROOT = Path(__file__).resolve().parents[2]
HEIC = (ROOT / "tests" / "fixtures" / "ingest" / "synthetic-exif.heic").read_bytes()
R = TiffImagePlugin.IFDRational
HEIF_IROT = {"container": "heif", "tags": ["irot"]}
EXIF_ORIENTATION = {"container": "exif", "tags": ["Orientation"]}
TKHD = {"container": "quicktime", "tags": ["tkhd"]}


def exif_of(tags: dict[str, Any] | None = None, exif_ifd: dict[str, Any] | None = None, gps: dict[str, Any] | None = None) -> Image.Exif:
    """An EXIF block written by Pillow into a JPEG and read back, as an upload would carry it."""
    exif = Image.Exif()
    for name, value in (tags or {}).items():
        exif[getattr(ExifTags.Base, name)] = value
    if exif_ifd:
        ifd = exif.get_ifd(ExifTags.IFD.Exif)
        for name, value in exif_ifd.items():
            ifd[getattr(ExifTags.Base, name)] = value
    if gps:
        ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        for name, value in gps.items():
            ifd[getattr(ExifTags.GPS, name)] = value
    out = io.BytesIO()
    Image.new("L", (32, 24), 40).save(out, "JPEG", exif=exif)
    return Image.open(io.BytesIO(out.getvalue())).getexif()


ZONED = {"DateTimeOriginal": "2031:06:01 04:37:52", "SubsecTimeOriginal": "120", "OffsetTimeOriginal": "+02:00"}
PLACE = {"GPSLatitudeRef": "N", "GPSLatitude": (46.0, 31.0, 10.92), "GPSLongitudeRef": "E", "GPSLongitude": (7.0, 59.0, 9.96)}


def test_a_zoned_exif_time_has_its_offset_and_utc() -> None:
    hint = exif_hints(exif_of(exif_ifd=ZONED))["captureTime"]
    assert hint == {
        "localDateTime": "2031-06-01T04:37:52.120",
        "utcOffsetMinutes": 120,
        "utc": "2031-06-01T02:37:52.120Z",
        "marks": "recorded",
        "source": {"container": "exif", "tags": ["DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"]},
    }


def test_a_zoneless_exif_time_stays_local_and_no_time_stays_missing() -> None:
    hint = exif_hints(exif_of(exif_ifd={"DateTimeOriginal": "2031:06:01 04:37:52"}))["captureTime"]
    assert hint == {"localDateTime": "2031-06-01T04:37:52", "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
                    "source": {"container": "exif", "tags": ["DateTimeOriginal"]}}
    assert exif_hints(exif_of(tags={"Make": "Synthetic"})).get("captureTime") is None
    assert exif_hints(Image.Exif()) == {}


@pytest.mark.parametrize(("lat_ref", "lon_ref", "sign"), [("N", "E", (1, 1)), ("N", "W", (1, -1)), ("S", "E", (-1, 1)), ("S", "W", (-1, -1))])
def test_gps_reads_all_four_quadrants(lat_ref: str, lon_ref: str, sign: tuple[int, int]) -> None:
    hint = exif_hints(exif_of(gps={**PLACE, "GPSLatitudeRef": lat_ref, "GPSLongitudeRef": lon_ref,
                                   "GPSAltitudeRef": b"\x01", "GPSAltitude": 12.5, "GPSHPositioningError": 5.0}))["location"]
    assert hint["latDeg"] == pytest.approx(sign[0] * (46 + 31 / 60 + 10.92 / 3600))
    assert hint["lonDeg"] == pytest.approx(sign[1] * (7 + 59 / 60 + 9.96 / 3600))
    assert hint["altitudeM"] == -12.5 and hint["horizontalAccuracyM"] == 5.0
    assert hint["source"] == {"container": "exif", "tags": [
        "GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef", "GPSAltitude", "GPSAltitudeRef", "GPSHPositioningError"]}


@pytest.mark.parametrize(
    "gps",
    [
        {},
        {**PLACE, "GPSLatitude": (0.0, 0.0, 0.0), "GPSLongitude": (0.0, 0.0, 0.0)},
        {**PLACE, "GPSStatus": "V"},
        {key: value for key, value in PLACE.items() if key != "GPSLatitudeRef"},
        {**PLACE, "GPSLatitude": (95.0, 0.0, 0.0)},
        {**PLACE, "GPSLongitude": (7.0, 61.0, 0.0)},
        {**PLACE, "GPSLongitudeRef": "X"},
    ],
)
def test_a_place_without_a_valid_fix_stays_missing(gps: dict[str, Any]) -> None:
    assert exif_hints(exif_of(gps=gps)).get("location") is None


def test_camera_values_and_the_tags_they_came_from() -> None:
    hint = exif_hints(exif_of(
        tags={"Make": "Synthetic", "Model": "Synthetic Test Camera"},
        exif_ifd={"LensModel": "Synthetic 24 mm", "FocalLength": R(42, 10), "FocalLengthIn35mmFilm": 24,
                  "ExposureTime": R(10, 1), "FNumber": R(18, 10), "ISOSpeedRatings": 1600},
    ))["camera"]
    assert hint == {
        "make": "Synthetic", "model": "Synthetic Test Camera", "lensModel": "Synthetic 24 mm", "focalLengthMm": 4.2,
        "focalLength35mmMm": 24.0, "exposureTimeSec": 10.0, "fNumber": 1.8, "iso": 1600.0,
        "source": {"container": "exif", "tags": ["Make", "Model", "LensModel", "FocalLength", "FocalLengthIn35mmFilm",
                                                 "ExposureTime", "FNumber", "PhotographicSensitivity"]},
    }


def test_a_malformed_value_is_dropped_not_repaired() -> None:
    assert exif_hints(exif_of(exif_ifd={"DateTimeOriginal": "2031:13:45 25:61:00"})).get("captureTime") is None
    beyond = exif_hints(exif_of(exif_ifd={**ZONED, "OffsetTimeOriginal": "+15:00"}))["captureTime"]
    assert (beyond["utcOffsetMinutes"], beyond["utc"]) == (None, None), "an offset past 14 hours is dropped, not clamped"
    camera = exif_hints(exif_of(tags={"Make": "Synth\x07etic", "Model": "Synthetic"}, exif_ifd={"FNumber": R(0, 1), "ISOSpeedRatings": 65535}))["camera"]
    assert camera == {"model": "Synthetic", "iso": 65535.0, "source": {"container": "exif", "tags": ["Model", "PhotographicSensitivity"]}}
    assert exif_hints(exif_of(exif_ifd={"FocalLength": R(30000, 1)})).get("camera") is None


@pytest.mark.parametrize("fmt", ["PNG", "TIFF"])
def test_png_and_tiff_carry_their_exif_too(fmt: str) -> None:
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "Synthetic"
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = "2031:06:01 04:37:52"
    out = io.BytesIO()
    Image.new("L", (32, 24), 40).save(out, fmt, exif=exif)
    hints = exif_hints(Image.open(io.BytesIO(out.getvalue())).getexif())
    assert hints["camera"]["make"] == "Synthetic"
    if fmt == "PNG":
        # Pillow's TIFF writer keeps IFD0 tags but writes no Exif sub-IFD, so only the PNG carries the time
        assert hints["captureTime"]["localDateTime"] == "2031-06-01T04:37:52"


def test_the_heic_fixture_reads_through_the_box_reader() -> None:
    info = read_heif(HEIC)
    assert (info.width, info.height) == (640, 480)
    assert info.exif is not None and info.exif.startswith((b"II*\x00", b"MM\x00*"))
    exif = Image.Exif()
    exif.load(b"Exif\x00\x00" + info.exif)
    hints = exif_hints(exif)
    assert hints["captureTime"]["utc"] == "2031-06-01T02:37:52.120Z"
    assert (round(hints["location"]["latDeg"], 4), round(hints["location"]["lonDeg"], 4)) == (46.5197, 7.9861)
    assert hints["location"]["altitudeM"] == 3466.0 and hints["location"]["horizontalAccuracyM"] == 5.0
    assert hints["camera"]["model"] == "Synthetic Test Camera" and hints["camera"]["fNumber"] == 1.8


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def test_an_uploaded_heic_carries_its_hints_and_size(client: TestClient) -> None:
    response = client.post("/api/ingest", content=HEIC, headers={"content-type": "image/heic"})
    assert response.status_code == 201, response.text
    receipt = response.json()
    IngestReceipt.model_validate(receipt)
    asset = receipt["sourceAsset"]
    assert (asset["width"], asset["height"], asset["videoCodec"], asset["mimeType"]) == (640, 480, "hevc", "image/heic")
    assert asset["capturedAtUTC"] == "2031-06-01T02:37:52.120Z"
    assert asset["observerLocation"]["elevationM"] == 3466.0 and asset["observerLocation"]["uncertaintyMeters"] == 5.0
    assert asset["metadataSources"]["capturedAtUTC"]["container"] == "exif"
    # The fixture's irot 0 states the pixels are shown as stored
    assert asset["displayOrientation"] == {"quarterTurnsClockwise": 0, "mirrored": False}
    assert asset["metadataSources"]["displayOrientation"] == receipt["hints"]["orientation"]["source"] == HEIF_IROT


@pytest.mark.parametrize(("tag", "kind", "value", "reason"), [
    (50706, 1, b"\x01\x04\x00\x00", "camera raw"),  # DNGVersion
    (254, 4, 1, "reduced copy"),  # NewSubfileType: the first image is a reduced copy
])
def test_a_camera_raw_built_on_tiff_is_refused_not_read_as_its_preview(
    client: TestClient, tag: int, kind: int, value: Any, reason: str
) -> None:
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[tag] = value
    info.tagtype[tag] = kind
    out = io.BytesIO()
    Image.new("L", (32, 24), 40).save(out, "TIFF", tiffinfo=info)
    response = client.post("/api/ingest", content=out.getvalue(), headers={"content-type": "image/tiff"})
    assert response.status_code == 422 and reason in response.json()["detail"]


def make_clip(folder: Path, name: str, *metadata: str, audio: bool = False) -> Path:
    assert shutil.which("ffmpeg"), "ffmpeg is required (CI installs it)"
    path = folder / name
    command = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x48:d=1:r=25"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000", "-c:a", "aac", "-shortest"]
    command += ["-c:v", "mpeg4", "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    subprocess.run(command + [str(path)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path


def upload_clip(client: TestClient, path: Path) -> dict[str, Any]:
    response = client.post("/api/ingest", content=path.read_bytes(), headers={"content-type": "video/quicktime"})
    assert response.status_code == 201, response.text
    IngestReceipt.model_validate(response.json())
    return response.json()


def test_apple_creationdate_wins_over_creation_time(client: TestClient, tmp_path: Path) -> None:
    clip = make_clip(tmp_path, "both.mov", "com.apple.quicktime.creationdate=2031-06-01T04:37:52+0200",
                     "creation_time=2031-06-12T08:00:00Z", "com.apple.quicktime.location.ISO6709=+46.5197+007.9861+3466.000/")
    receipt = upload_clip(client, clip)
    assert receipt["hints"]["captureTime"] == {
        "localDateTime": "2031-06-01T04:37:52", "utcOffsetMinutes": 120, "utc": "2031-06-01T02:37:52Z", "marks": "recorded",
        "source": {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]},
    }
    asset = receipt["sourceAsset"]
    assert asset["capturedAtUTC"] == "2031-06-01T02:37:52Z"
    assert (asset["observerLocation"]["latDeg"], asset["observerLocation"]["lonDeg"]) == (46.5197, 7.9861)
    assert (asset["width"], asset["height"], asset["fpsNumerator"], asset["fpsDenominator"]) == (64, 48, 25, 1)
    assert asset["durationMs"] == pytest.approx(1000, abs=50)


def test_creation_time_alone_is_a_labelled_file_time_not_a_capture_time(client: TestClient, tmp_path: Path) -> None:
    receipt = upload_clip(client, make_clip(tmp_path, "file-time.mov", "creation_time=2031-06-12T08:00:00Z"))
    assert receipt["hints"]["captureTime"] == {
        "localDateTime": None, "utcOffsetMinutes": None, "utc": "2031-06-12T08:00:00Z", "marks": "file_written",
        "source": {"container": "quicktime", "tags": ["creation_time"]},
    }
    assert "capturedAtUTC" not in receipt["sourceAsset"]
    assert "metadataSources" not in receipt["sourceAsset"]


@pytest.mark.parametrize("metadata", [(), ("creation_time=1970-01-01T00:00:00Z",)])
def test_a_clip_without_a_time_has_none(client: TestClient, tmp_path: Path, metadata: tuple[str, ...]) -> None:
    receipt = upload_clip(client, make_clip(tmp_path, "no-time.mov", *metadata))
    assert "captureTime" not in receipt["hints"] and "capturedAtUTC" not in receipt["sourceAsset"]


def test_a_clip_lists_its_audio_stream_with_the_hash_of_its_packets(client: TestClient, tmp_path: Path) -> None:
    clip = make_clip(tmp_path, "audio.mov", audio=True)
    streams = upload_clip(client, clip)["sourceAsset"]["audioStreams"]
    assert len(streams) == 1
    stream = streams[0]
    assert (stream["codec"], stream["sampleRateHz"], stream["channels"]) == ("aac", 48000, 1)
    packets = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-map", f"0:{stream['index']}", "-c", "copy", "-f", "data", "-"],
                             check=True, capture_output=True, stdin=subprocess.DEVNULL).stdout
    assert stream["sha256"] == hashlib.sha256(packets).hexdigest()


def test_quicktime_tags_are_read_strictly() -> None:
    apple = {"com.apple.quicktime.creationdate": "2031-06-01T04:37:52Z"}
    assert quicktime_hints(apple)["captureTime"]["utcOffsetMinutes"] == 0
    malformed = {"com.apple.quicktime.creationdate": "2031-06-01 04:37", "creation_time": "2031-06-12T08:00:00.000000Z"}
    assert quicktime_hints(malformed)["captureTime"]["marks"] == "file_written"
    assert "location" not in quicktime_hints({"location": "+95.0000+007.9861/"})
    assert "location" not in quicktime_hints({"location": "+00.0000+000.0000/"})
    place = quicktime_hints({"location": "-33.8568+151.2153/", "com.apple.quicktime.location.accuracy.horizontal": "35.0"})["location"]
    assert place == {"latDeg": -33.8568, "lonDeg": 151.2153, "horizontalAccuracyM": 35.0,
                     "source": {"container": "quicktime", "tags": ["location", "com.apple.quicktime.location.accuracy.horizontal"]}}
    camera = quicktime_hints({"com.apple.quicktime.make": "Synthetic", "model": "Synthetic Test Camera"})["camera"]
    assert camera["source"]["tags"] == ["com.apple.quicktime.make", "model"]


def test_the_box_reader_caps_the_boxes_it_will_walk() -> None:
    many = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00\x00\x00\x08free" * (MAX_BOXES + 1)
    with pytest.raises(HeifError, match=f"more than {MAX_BOXES} boxes"):
        read_heif(many)
    with pytest.raises(HeifError, match="does not start with an ftyp box"):
        read_heif(b"\x00\x00\x00\x08free")


def exif_extent(data: bytes) -> bytes:
    """The fixture's EXIF item location as its iloc box writes it: a four-byte offset and a four-byte length.

    The item is the four-byte TIFF offset (6), "Exif\\0\\0", then the block `read_heif` returns."""
    exif = read_heif(data).exif
    assert exif is not None
    start = data.find(b"\x00\x00\x00\x06Exif\x00\x00")
    assert start > 0 and data[start + 10:start + 10 + len(exif)] == exif
    entry = start.to_bytes(4, "big") + (10 + len(exif)).to_bytes(4, "big")
    assert data.count(entry) == 1
    return entry


def test_a_truncated_or_overreaching_heic_is_refused_not_half_read() -> None:
    with pytest.raises(HeifError, match="runs past its parent"):
        read_heif(HEIC[:-100])
    entry = exif_extent(HEIC)
    overreaching = HEIC.replace(entry, entry[:4] + (int.from_bytes(entry[4:], "big") + 10_000).to_bytes(4, "big"))
    with pytest.raises(HeifError, match="outside the file"):
        read_heif(overreaching)


def test_an_exif_item_over_the_cap_is_refused() -> None:
    # A free box after the file holds room for an EXIF item one byte over the cap, inside the file
    room = MAX_EXIF_BYTES + 64
    padded = HEIC + (8 + room).to_bytes(4, "big") + b"free" + bytes(room)
    oversized = padded.replace(exif_extent(HEIC), (len(HEIC) + 8).to_bytes(4, "big") + (MAX_EXIF_BYTES + 1).to_bytes(4, "big"))
    with pytest.raises(HeifError, match=f"larger than {MAX_EXIF_BYTES} bytes"):
        read_heif(oversized)
    # The same room holding an item at the cap reads
    at_cap = padded.replace(exif_extent(HEIC), (len(HEIC) + 8).to_bytes(4, "big") + MAX_EXIF_BYTES.to_bytes(4, "big"))
    with pytest.raises(HeifError, match="no TIFF header"):
        read_heif(at_cap)


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _full_box(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return _box(kind, bytes([version, 0, 0, 0]) + payload)


def heif_with(properties: list[tuple[bytes, bytes]], exif: Optional[Image.Exif] = None) -> bytes:
    """A small HEIF still built box by box: one hvc1 item 64 x 48, its ispe and then `properties` associated in that
    order, and an Exif item when `exif` is given. It holds no picture; the reader needs none."""
    ispe = _full_box(b"ispe", (64).to_bytes(4, "big") + (48).to_bytes(4, "big"))
    ipco = _box(b"ipco", ispe + b"".join(_box(kind, body) for kind, body in properties))
    count = 1 + len(properties)
    ipma = _full_box(b"ipma", (1).to_bytes(4, "big") + (1).to_bytes(2, "big") + bytes([count]) + bytes(range(1, count + 1)))
    infe = [_full_box(b"infe", (1).to_bytes(2, "big") + bytes(2) + b"hvc1" + b"\x00", version=2)]
    payload = b""
    if exif is not None:
        infe.append(_full_box(b"infe", (2).to_bytes(2, "big") + bytes(2) + b"Exif" + b"\x00", version=2))
        tiff = exif.tobytes()
        tiff = tiff[6:] if tiff.startswith(b"Exif\x00\x00") else tiff
        payload = (6).to_bytes(4, "big") + b"Exif\x00\x00" + tiff
    ftyp = _box(b"ftyp", b"heic" + bytes(4) + b"mif1heic")

    def meta(exif_offset: int) -> bytes:
        items = [(1).to_bytes(2, "big") + bytes(2) + (1).to_bytes(2, "big") + bytes(8)]
        if payload:
            items.append((2).to_bytes(2, "big") + bytes(2) + (1).to_bytes(2, "big")
                         + exif_offset.to_bytes(4, "big") + len(payload).to_bytes(4, "big"))
        iloc = _full_box(b"iloc", bytes([0x44, 0x00]) + len(items).to_bytes(2, "big") + b"".join(items))
        return _full_box(b"meta", _full_box(b"hdlr", bytes(4) + b"pict" + bytes(12) + b"\x00")
                         + _full_box(b"pitm", (1).to_bytes(2, "big"))
                         + _full_box(b"iinf", len(infe).to_bytes(2, "big") + b"".join(infe))
                         + iloc + _box(b"iprp", ipco + ipma))

    # The Exif item lies in mdat, after ftyp, meta and mdat's own header; offsets are four bytes wide either way
    offset = len(ftyp) + len(meta(0)) + 8
    return ftyp + meta(offset) + _box(b"mdat", payload)


ORIENTED = heif_with([(b"irot", b"\x01"), (b"imir", b"\x01")], exif=exif_of({"Orientation": 6}))


def _mutate(data: bytes, edits: list[tuple[int, int]]) -> bytes:
    out = bytearray(data)
    for position, value in edits:
        out[position % len(out)] = value
    return bytes(out)


def _read_within_bounds(data: bytes) -> HeifInfo | None:
    """Read `data`, allowing only a result or a `HeifError`, and only a bounded allocation."""
    tracemalloc.start()
    try:
        try:
            return read_heif(data)
        except HeifError as exc:
            assert str(exc), "a refusal says why"
            return None
        finally:
            _, peak = tracemalloc.get_traced_memory()
            assert peak <= 2 * len(data) + 256 * 1024, f"peak {peak} bytes for {len(data)} bytes of input"
    finally:
        tracemalloc.stop()


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    data=st.one_of(
        st.binary(max_size=4096),
        st.integers(min_value=0, max_value=len(HEIC) - 1).map(lambda cut: HEIC[:cut]),
        st.lists(st.tuples(st.integers(0, len(HEIC) - 1), st.integers(0, 255)), min_size=1, max_size=6).map(lambda e: _mutate(HEIC, e)),
        st.binary(max_size=64).map(lambda junk: HEIC[:300] + junk),
        # A HEIF with irot, imir and EXIF, its bytes mutated or cut
        st.lists(st.tuples(st.integers(0, len(ORIENTED) - 1), st.integers(0, 255)), min_size=1, max_size=6).map(lambda e: _mutate(ORIENTED, e)),
        st.integers(min_value=0, max_value=len(ORIENTED) - 1).map(lambda cut: ORIENTED[:cut]),
    )
)
@example(data=HEIC)
@example(data=HEIC[:36])
@example(data=b"\x00\x00\x00\x01ftyp" + b"\xff" * 8)
@example(data=b"\x00\x00\x00\x00ftypheic")
@example(data=_mutate(HEIC, [(4987, 255), (4988, 255)]))
@example(data=ORIENTED)
def test_pbt_ingest_heif_1_the_box_reader_reads_or_refuses_cleanly_within_bounds(data: bytes) -> None:
    info = _read_within_bounds(data)
    if info is not None:
        assert info.width > 0 and info.height > 0
        assert info.exif is None or (len(info.exif) <= len(data) and info.exif.startswith((b"II*\x00", b"MM\x00*")))
        orientation = info.orientation
        assert orientation is None or (
            orientation["quarterTurnsClockwise"] in range(4) and isinstance(orientation["mirrored"], bool)
            and orientation["source"]["container"] == "heif" and set(orientation["source"]["tags"]) <= {"irot", "imir"}
        )


# ---------------------------------------------------------------------------------------------
# Display orientation (solver-6c): EXIF Orientation, a HEIF's irot and imir, a clip's display matrix.
# Each reading is checked against an independent oracle: Pillow's exif_transpose, the HEIF text applied to a pixel
# grid, and ffmpeg's own autorotate decode.
# ---------------------------------------------------------------------------------------------

FORMS = [{"quarterTurnsClockwise": q, "mirrored": m} for m in (False, True) for q in range(4)]
GRID = np.arange(12, dtype=np.uint8).reshape(3, 4)


def show(form: dict, pixels: np.ndarray) -> np.ndarray:
    """The canonical form applied to stored pixels: mirror left to right if asked, then turn clockwise."""
    mirrored = np.fliplr(pixels) if form["mirrored"] else pixels
    return np.rot90(mirrored, k=-form["quarterTurnsClockwise"])


def form_of(hint: dict) -> dict:
    return {"quarterTurnsClockwise": hint["quarterTurnsClockwise"], "mirrored": hint["mirrored"]}


@pytest.mark.parametrize("value", range(1, 9))
def test_exif_orientation_reads_to_the_form_pillow_shows_it_in(value: int) -> None:
    out = io.BytesIO()
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = value
    Image.fromarray(np.arange(48, dtype=np.uint8).reshape(6, 8) * 5, "L").save(out, "JPEG", exif=exif)
    image = Image.open(io.BytesIO(out.getvalue()))
    hint = exif_hints(image.getexif())["orientation"]
    assert hint == {**EXIF_ORIENTATIONS[value], "source": EXIF_ORIENTATION}
    decoded = np.asarray(image)
    assert np.array_equal(show(form_of(hint), decoded), np.asarray(ImageOps.exif_transpose(image)))


@pytest.mark.parametrize("value", [0, 9, 65535])
def test_an_exif_orientation_outside_one_to_eight_is_dropped(value: int) -> None:
    assert "orientation" not in exif_hints(exif_of({"Orientation": value}))
    assert "orientation" not in exif_hints(exif_of())


@pytest.mark.parametrize("value", ["6", 6.0, True, (6, 6)], ids=["text", "float", "bool", "two-values"])
def test_an_exif_orientation_that_is_no_integer_is_dropped(value: Any) -> None:
    # Pillow writes Orientation only as an integer, so these are set on a block in memory
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = value
    assert "orientation" not in exif_hints(exif)


def by_the_heif_text(pixels: np.ndarray, properties: list[tuple[bytes, bytes]]) -> np.ndarray:
    """ISO/IEC 23008-12 as libavif's avif.h states it: irot turns anticlockwise by angle x 90 degrees, imir axis 0
    exchanges top and bottom and axis 1 left and right, each applied in the order the item lists it."""
    out = pixels
    for kind, body in properties:
        if kind == b"irot":
            out = np.rot90(out, k=body[0] & 3)
        elif kind == b"imir":
            out = np.flipud(out) if body[0] & 1 == 0 else np.fliplr(out)
    return out


HEIF_CASES = [[(b"irot", bytes([angle]))] for angle in range(4)] + [[(b"imir", bytes([axis]))] for axis in (0, 1)] + [
    order for angle, axis in itertools.product(range(4), (0, 1))
    for order in ([(b"irot", bytes([angle])), (b"imir", bytes([axis]))], [(b"imir", bytes([axis])), (b"irot", bytes([angle]))])
]


@pytest.mark.parametrize("properties", HEIF_CASES, ids=lambda p: "-".join(f"{k.decode()}{b[0]}" for k, b in p))
def test_heif_irot_and_imir_read_to_the_form_the_heif_text_gives(properties: list[tuple[bytes, bytes]]) -> None:
    orientation = read_heif(heif_with(properties)).orientation
    assert orientation is not None
    assert orientation["source"] == {"container": "heif", "tags": [kind.decode() for kind, _ in properties]}
    assert np.array_equal(show(form_of(orientation), GRID), by_the_heif_text(GRID, properties))


def test_heif_transforms_keep_only_their_low_bits_and_need_their_byte() -> None:
    # The reserved bits above irot's two and imir's one are not part of the value
    assert form_of(read_heif(heif_with([(b"irot", b"\xfd")])).orientation) == {"quarterTurnsClockwise": 3, "mirrored": False}
    assert form_of(read_heif(heif_with([(b"imir", b"\xfe")])).orientation) == {"quarterTurnsClockwise": 2, "mirrored": True}
    assert read_heif(heif_with([])).orientation is None
    for kind in (b"irot", b"imir"):
        with pytest.raises(HeifError, match=f"{kind.decode()} ends early"):
            read_heif(heif_with([(kind, b"")]))


def test_the_heic_fixture_states_its_pixels_are_shown_as_stored() -> None:
    assert read_heif(HEIC).orientation == {"quarterTurnsClockwise": 0, "mirrored": False, "source": HEIF_IROT}


def upload_still(client: TestClient, data: bytes, content_type: str) -> dict[str, Any]:
    response = client.post("/api/ingest", content=data, headers={"content-type": content_type})
    assert response.status_code == 201, response.text
    IngestReceipt.model_validate(response.json())
    return response.json()


def test_a_heics_irot_and_imir_decide_and_its_exif_orientation_counts_only_without_them(client: TestClient) -> None:
    # irot 1 is three quarter turns clockwise; the EXIF block says 6, one turn: the container's transforms decide
    turned = upload_still(client, heif_with([(b"irot", b"\x01")], exif=exif_of({"Orientation": 6})), "image/heic")
    assert turned["sourceAsset"]["displayOrientation"] == {"quarterTurnsClockwise": 3, "mirrored": False}
    assert turned["sourceAsset"]["metadataSources"]["displayOrientation"] == HEIF_IROT
    # With neither irot nor imir, the EXIF Orientation is what the file says
    plain = upload_still(client, heif_with([], exif=exif_of({"Orientation": 6})), "image/heic")
    assert plain["sourceAsset"]["displayOrientation"] == {"quarterTurnsClockwise": 1, "mirrored": False}
    assert plain["sourceAsset"]["metadataSources"]["displayOrientation"] == EXIF_ORIENTATION
    # And with neither, nothing is said
    bare = upload_still(client, heif_with([], exif=exif_of()), "image/heic")
    assert "displayOrientation" not in bare["sourceAsset"] and "orientation" not in bare["hints"]
    # A HEIC with no EXIF item at all still carries its irot
    unexifed = upload_still(client, heif_with([(b"irot", b"\x01")]), "image/heic")
    assert unexifed["sourceAsset"]["displayOrientation"] == {"quarterTurnsClockwise": 3, "mirrored": False}
    assert unexifed["hints"] == {"orientation": {"quarterTurnsClockwise": 3, "mirrored": False, "source": HEIF_IROT}}


FIXED_ONE = 1 << 16
FIXED_W = 1 << 30
# The eight 2 x 2 signed permutations (a, b, c, d): every quarter turn, with and without a mirror
RIGHT_ANGLES = [m for m in itertools.product((-FIXED_ONE, 0, FIXED_ONE), repeat=4)
                if (m[1] == m[2] == 0 and m[0] and m[3]) or (m[0] == m[3] == 0 and m[1] and m[2])]
SIDE = 32


def coordinate_clip(folder: Path, name: str, linear: Optional[tuple[int, int, int, int]]) -> Path:
    """A one-frame lossless clip whose pixels name their own stored column (red) and row (green), with its track
    header's display matrix set to `linear` (a, b, c, d) and no translation, or left as ffmpeg wrote it."""
    rows, columns = np.mgrid[0:SIDE, 0:SIDE]
    frame = np.stack([columns * 8, rows * 8, np.full_like(rows, 128)], axis=-1).astype(np.uint8)
    raw = folder / f"{name}.rgb"
    raw.write_bytes(frame.tobytes())
    path = folder / f"{name}.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{SIDE}x{SIDE}", "-r", "25",
                    "-i", str(raw), "-frames:v", "1", "-c:v", "png", str(path)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    if linear is not None:
        data = bytearray(path.read_bytes())
        at = data.find(b"tkhd")
        assert at > 0 and data.count(b"tkhd") == 1, "one video track header"
        # After the box type: version and flags, the times, track id and duration (20 bytes, or 32 in version 1),
        # 8 reserved bytes, then layer, group, volume and 2 reserved bytes: the matrix follows
        start = at + 4 + 4 + (32 if data[at + 4] == 1 else 20) + 8 + 8
        a, b, c, d = linear
        data[start:start + 36] = b"".join(v.to_bytes(4, "big", signed=True) for v in (a, b, 0, c, d, 0, 0, 0, FIXED_W))
        path.write_bytes(bytes(data))
    return path


def decoded(path: Path, autorotate: bool) -> np.ndarray:
    command = ["ffmpeg", "-v", "error"] + ([] if autorotate else ["-noautorotate"])
    command += ["-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    out = subprocess.run(command, check=True, capture_output=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(SIDE, SIDE, 3)


def facts_of(path: Path) -> Any:
    with path.open("rb") as handle:
        return probe_media(path, sniff(handle.read(SNIFF_BYTES)))


@pytest.mark.parametrize("linear", RIGHT_ANGLES, ids=lambda m: "".join("+-0"[(v < 0) + 2 * (v == 0)] for v in m))
def test_a_clips_display_matrix_reads_to_the_form_ffmpeg_autorotates_it_by(tmp_path: Path, linear: tuple[int, int, int, int]) -> None:
    clip = coordinate_clip(tmp_path, "turned", linear)
    hint = display_matrix_hint(facts_of(clip).display_matrix)
    if linear == (FIXED_ONE, 0, 0, FIXED_ONE):
        # ffprobe reports no display matrix for the identity, so the file says nothing to read and nothing is turned
        assert hint is None and np.array_equal(decoded(clip, autorotate=True), decoded(clip, autorotate=False))
        return
    assert hint is not None and hint["source"] == TKHD
    shown = show(form_of(hint), decoded(clip, autorotate=False)).astype(int)
    assert np.abs(shown - decoded(clip, autorotate=True).astype(int)).max() <= 2


@pytest.mark.parametrize("linear", [
    (round(math.cos(math.radians(30)) * FIXED_ONE), round(math.sin(math.radians(30)) * FIXED_ONE),
     -round(math.sin(math.radians(30)) * FIXED_ONE), round(math.cos(math.radians(30)) * FIXED_ONE)),
    (2 * FIXED_ONE, 0, 0, 2 * FIXED_ONE),
], ids=["thirty-degrees", "twice-the-size"])
def test_a_display_matrix_that_is_no_right_angle_turn_gives_no_hint(tmp_path: Path, linear: tuple[int, int, int, int]) -> None:
    matrix = facts_of(coordinate_clip(tmp_path, "odd", linear)).display_matrix
    assert matrix is not None, "ffprobe reports the matrix"
    assert display_matrix_hint(matrix) is None


def test_display_matrix_hint_is_strict_about_its_shape() -> None:
    turn = (0, FIXED_ONE, 0, -FIXED_ONE, 0, 0, 0, 0, FIXED_W)
    assert form_of(display_matrix_hint(turn)) == {"quarterTurnsClockwise": 1, "mirrored": False}
    assert display_matrix_hint(turn[:8]) is None
    assert display_matrix_hint(None) is None
    assert display_matrix_hint(turn[:8] + (FIXED_W // 2,)) is None
    assert display_matrix_hint((0, FIXED_ONE, 1, -FIXED_ONE, 0, 0, 0, 0, FIXED_W)) is None
    assert display_matrix_hint((FIXED_ONE, FIXED_ONE, 0, 0, 0, 0, 0, 0, FIXED_W)) is None
    # Nearly a turn is not one: nothing is rounded to the nearest form
    assert display_matrix_hint((FIXED_ONE + 1, 0, 0, 0, FIXED_ONE, 0, 0, 0, FIXED_W)) is None
    assert display_matrix_hint((0, FIXED_ONE - 1, 0, -FIXED_ONE, 0, 0, 0, 0, FIXED_W)) is None


def test_an_uploaded_clip_carries_the_orientation_its_matrix_states(client: TestClient, tmp_path: Path) -> None:
    # A quarter turn clockwise, as a portrait phone clip's track header writes it
    receipt = upload_clip(client, coordinate_clip(tmp_path, "portrait", (0, FIXED_ONE, -FIXED_ONE, 0)))
    assert receipt["hints"]["orientation"] == {"quarterTurnsClockwise": 1, "mirrored": False, "source": TKHD}
    assert receipt["sourceAsset"]["displayOrientation"] == {"quarterTurnsClockwise": 1, "mirrored": False}
    assert receipt["sourceAsset"]["metadataSources"]["displayOrientation"] == TKHD
    plain = upload_clip(client, coordinate_clip(tmp_path, "plain", None))
    assert "orientation" not in plain["hints"] and "displayOrientation" not in plain["sourceAsset"]


def _gray_frame(path: Path, at_sec: float, autorotate: bool) -> Optional[np.ndarray]:
    command = ["ffmpeg", "-v", "error", "-nostdin"] + ([] if autorotate else ["-noautorotate"])
    command += ["-ss", f"{at_sec:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json"]
    try:
        out = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        if out.returncode != 0:
            return None
        if autorotate:
            return np.frombuffer(out.stdout, dtype=np.uint8)
        stream = json.loads(subprocess.run(probe + [str(path)], capture_output=True, text=True, timeout=60).stdout)["streams"][0]
        return np.frombuffer(out.stdout, dtype=np.uint8).reshape(int(stream["height"]), int(stream["width"]))
    except Exception:
        return None


@pytest.mark.owner_media("data/uploads")
def test_real_clips_carry_their_probed_rate_and_the_orientation_ffmpeg_shows_them_in() -> None:
    # Read only: the facts, hints and asset the store would build, from each clip where it lies; nothing is copied.
    # Every check is a flag with a fixed message: a failing assert must never print a clip's name, place, time or pixels
    from research.benchmarks.phone_photos import real_clips

    clips = real_clips.find_clips()
    assert clips, "no real clip was found"
    for clip in clips:
        try:
            with clip.path.open("rb") as handle:
                file_type = sniff(handle.read(SNIFF_BYTES))
            facts = probe_media(clip.path, file_type)
            received = Received(clip.path, clip.sha256, clip.path.stat().st_size, file_type)
            hints = ingest_router._hints(received, facts)
            asset = ingest_router._asset(received, "Real clip", facts, hints)
            IngestReceipt.model_validate({"sourceAsset": asset, "hints": hints, "alreadyStored": False})
            valid = True
        except Exception:
            valid = False
        assert valid, "the receipt the store would build for a real clip is not valid"
        try:
            stream = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "json",
                 str(clip.path)], capture_output=True, text=True, timeout=60).stdout)["streams"][0]
            same_rate = Fraction(asset["fpsNumerator"], asset["fpsDenominator"]) == Fraction(stream["avg_frame_rate"])
        except Exception:
            same_rate = False
        assert same_rate, "a real clip's asset does not carry the frame rate ffprobe reads"
        form = form_of(asset["displayOrientation"]) if "displayOrientation" in asset else FORMS[0]
        stored = _gray_frame(clip.path, clip.duration_s / 2.0, autorotate=False)
        shown = _gray_frame(clip.path, clip.duration_s / 2.0, autorotate=True)
        as_ffmpeg_shows_it = False
        if stored is not None and shown is not None:
            ours = show(form, stored)
            if shown.size == ours.size:
                shown = shown.reshape(ours.shape)
                as_ffmpeg_shows_it = float(np.abs(ours.astype(int) - shown.astype(int)).mean()) < 1.0
        assert as_ffmpeg_shows_it, "a real clip's display orientation does not show it as ffmpeg's autorotate does"


def test_the_probe_reads_a_display_matrix_only_as_three_rows_of_three_integers() -> None:
    from services.ingest.probe import _display_matrix

    text = "\n00000000:            0       65536           0\n00000001:       -65536           0           0\n00000002:            0           0  1073741824\n"
    entry = {"side_data_type": "Display Matrix", "displaymatrix": text, "rotation": -90}
    assert _display_matrix({"side_data_list": [entry]}) == (0, 65536, 0, -65536, 0, 0, 0, 0, 1073741824)
    assert _display_matrix({}) is None
    assert _display_matrix({"side_data_list": [entry, entry]}) is None, "two matrices are one too many"
    assert _display_matrix({"side_data_list": [{**entry, "displaymatrix": text.replace("00000002:", "00000003:")}]}) is None
    assert _display_matrix({"side_data_list": [{**entry, "displaymatrix": text.rsplit("\n00000002", 1)[0]}]}) is None
    assert _display_matrix({"side_data_list": [{**entry, "displaymatrix": text.replace("65536", "1.0", 1)}]}) is None
    assert _display_matrix({"side_data_list": [{**entry, "displaymatrix": None}]}) is None
    assert _display_matrix({"side_data_list": [{"side_data_type": "Stereo 3D"}]}) is None
