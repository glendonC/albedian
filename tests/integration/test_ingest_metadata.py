"""Hints read from uploads (roadmap Track 1 M1): EXIF from JPEG, PNG, TIFF and HEIC, and QuickTime tags.

Synthetic files only: every value is invented (2031 dates, invented places and cameras), the HEIC is the
fixture described in tests/fixtures/ingest/README.md, and clips are made here by ffmpeg, which CI installs.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tracemalloc
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from PIL import ExifTags, Image, TiffImagePlugin

from services.api import app
from services.contracts.ingest import IngestReceipt
from services.ingest import router as ingest_router
from services.ingest.metadata import (
    MAX_BOXES,
    MAX_EXIF_BYTES,
    HeifError,
    HeifInfo,
    exif_hints,
    quicktime_hints,
    read_heif,
)
from services.ingest.store import IngestStore

ROOT = Path(__file__).resolve().parents[2]
HEIC = (ROOT / "tests" / "fixtures" / "ingest" / "synthetic-exif.heic").read_bytes()
R = TiffImagePlugin.IFDRational


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
    )
)
@example(data=HEIC)
@example(data=HEIC[:36])
@example(data=b"\x00\x00\x00\x01ftyp" + b"\xff" * 8)
@example(data=b"\x00\x00\x00\x00ftypheic")
@example(data=_mutate(HEIC, [(4987, 255), (4988, 255)]))
def test_pbt_ingest_heif_1_the_box_reader_reads_or_refuses_cleanly_within_bounds(data: bytes) -> None:
    info = _read_within_bounds(data)
    if info is not None:
        assert info.width > 0 and info.height > 0
        assert info.exif is None or (len(info.exif) <= len(data) and info.exif.startswith((b"II*\x00", b"MM\x00*")))
