"""A capture time whose stated offset gives an instant the calendar cannot hold (unit capture-time-2).

A UTC instant is written from year 1 to year 9999. A file's own offset (EXIF `OffsetTimeOriginal`, or the zone of a
clip's `com.apple.quicktime.creationdate`) of up to 14 hours can carry a local time within 14 hours of either end past
it. Such an offset is not applied and is no crash: the upload is taken, the capture time stays local, and its record
names the cause, `offsetNotApplied: instant_outside_the_calendar`; every other hint is kept, the asset gets no capture
instant, and no sentence states a time for it.

Synthetic files only: JPEGs built with Pillow and clips with ffmpeg (CI installs it), made-up tags, the open-ocean
place the plate-solver fixtures use.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import ExifTags, Image, TiffImagePlugin
from pydantic import ValidationError
import pytest

from services.api import app
from services.contracts.ingest import CaptureTimeHint, IngestReceipt, format_utc
from services.ingest import router as ingest_router
from services.ingest.metadata import _format_local
from services.ingest.store import IngestStore

Rat = TiffImagePlugin.IFDRational
OUTSIDE = "instant_outside_the_calendar"
OCEAN_GPS = {"GPSLatitudeRef": "N", "GPSLatitude": (Rat(30, 1), Rat(0, 1), Rat(0, 1)),
             "GPSLongitudeRef": "W", "GPSLongitude": (Rat(40, 1), Rat(0, 1), Rat(0, 1))}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def photo(local: str, offset: str | None, *, gps: bool = False, millis: str | None = None) -> bytes:
    """A JPEG whose EXIF holds `local` (YYYY-MM-DDTHH:MM:SS) as DateTimeOriginal and `offset` (+HH:MM) as
    OffsetTimeOriginal."""
    exif = Image.Exif()
    exif[0x0110] = "Synthetic camera"
    ifd = exif.get_ifd(ExifTags.IFD.Exif)
    ifd[ExifTags.Base.DateTimeOriginal] = local.replace("-", ":").replace("T", " ")
    if millis is not None:
        ifd[ExifTags.Base.SubsecTimeOriginal] = millis
    if offset is not None:
        ifd[ExifTags.Base.OffsetTimeOriginal] = offset
    if gps:
        for name, value in OCEAN_GPS.items():
            exif.get_ifd(ExifTags.IFD.GPSInfo)[getattr(ExifTags.GPS, name)] = value
    out = io.BytesIO()
    Image.new("L", (32, 24), 40).save(out, "JPEG", exif=exif)
    return out.getvalue()


# Every child process a test here starts has its own bound, and a test that waits on the API waits within one too: on
# this laptop each ffmpeg call below takes well under a second (child_times in the report), so 60 s is generous and
# still ends a hung child long before CI's 20 minute job limit [proposed]
CHILD_BOUND_SEC = 60


def run_bounded(command: list[str], what: str) -> None:
    """Run `command` to its end within CHILD_BOUND_SEC, or fail by an assertion that names what it waited for."""
    assert shutil.which(command[0]), f"{command[0]} is required (CI installs it)"
    try:
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=CHILD_BOUND_SEC)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{what} did not finish within {CHILD_BOUND_SEC} s")


def clip(folder: Path, local: str, offset: str, *, millis: str | None = None) -> bytes:
    """A clip whose com.apple.quicktime.creationdate is `local` with `offset` written as +HHMM, and a place."""
    stamp = local + (f".{millis}" if millis else "") + offset.replace(":", "")
    path = folder / "synthetic.mov"
    run_bounded(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x48:d=1:r=25", "-c:v", "mpeg4",
                 "-movflags", "use_metadata_tags", "-metadata", f"com.apple.quicktime.creationdate={stamp}",
                 "-metadata", "com.apple.quicktime.location.ISO6709=+30.0000-040.0000/", str(path)], "ffmpeg making a clip")
    return path.read_bytes()


def upload(client: TestClient, body: bytes, content_type: str) -> dict[str, Any]:
    response = client.post("/api/ingest", content=body, headers={"content-type": content_type})
    assert response.status_code == 201, response.text
    receipt = response.json()
    IngestReceipt.model_validate(receipt)
    return receipt


# One table over both ends of the calendar, for the photo and the clip reader: the local time, the file's offset, and
# the UTC instant it gives, or None where that instant lies outside years 1 to 9999. The smallest and the largest
# offsets (+03:00 and -03:00, +14:00 and -14:00, and none at all), each a second inside and a second outside
EDGES = [
    ("0001-01-01T03:00:00", "+03:00", None, "0001-01-01T00:00:00Z"),
    ("0001-01-01T02:59:59", "+03:00", None, None),
    ("0001-01-01T14:00:00", "+14:00", None, "0001-01-01T00:00:00Z"),
    ("0001-01-01T13:59:59", "+14:00", None, None),
    ("0001-01-01T00:00:00", "+00:00", None, "0001-01-01T00:00:00Z"),
    ("9999-12-31T20:59:59", "-03:00", None, "9999-12-31T23:59:59Z"),
    ("9999-12-31T21:00:00", "-03:00", None, None),
    ("9999-12-31T09:59:59", "-14:00", None, "9999-12-31T23:59:59Z"),
    ("9999-12-31T10:00:00", "-14:00", None, None),
    ("9999-12-31T23:59:59", "+00:00", None, "9999-12-31T23:59:59Z"),
    ("9999-12-31T20:59:59", "-03:00", "999", "9999-12-31T23:59:59.999Z"),
    ("0001-01-01T02:59:59", "+03:00", "999", None),
]
READERS = ["photo", "clip"]


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize(("local", "offset", "millis", "utc"), EDGES)
def test_each_end_of_the_calendar_through_the_upload_route(
        client: TestClient, tmp_path: Path, reader: str, local: str, offset: str, millis: str | None, utc: str | None) -> None:
    body = photo(local, offset, gps=True, millis=millis) if reader == "photo" else clip(tmp_path, local, offset, millis=millis)
    receipt = upload(client, body, "image/jpeg" if reader == "photo" else "video/quicktime")
    hint, asset = receipt["hints"]["captureTime"], receipt["sourceAsset"]
    offset_tag = "OffsetTimeOriginal" if reader == "photo" else "com.apple.quicktime.creationdate"
    assert hint["localDateTime"] == local + (f".{millis}" if millis else "")
    assert offset_tag in hint["source"]["tags"]
    # Every other hint is kept: the place, and the photo's camera
    assert receipt["hints"]["location"]["latDeg"] == pytest.approx(30.0) and asset["observerLocation"]["lonDeg"] == pytest.approx(-40.0)
    if reader == "photo":
        assert receipt["hints"]["camera"]["model"] == "Synthetic camera"
    if utc is not None:
        assert (hint["utc"], asset["capturedAtUTC"]) == (utc, utc)
        assert "offsetNotApplied" not in hint
    else:
        assert (hint["utcOffsetMinutes"], hint["utc"]) == (None, None)
        assert hint["offsetNotApplied"] == OUTSIDE
        assert "capturedAtUTC" not in asset and "capturedAtUTC" not in asset["metadataSources"]
        # The file's own offset is not applied, and none is worked out in its place: no GPS check, no basis
        assert "basis" not in hint["source"] and not {"GPSDateStamp", "GPSTimeStamp"} & set(hint["source"]["tags"])


@pytest.mark.parametrize("local", ["0001-01-01T00:00:00", "9999-12-31T23:59:59"])
def test_an_untagged_photo_at_either_end_keeps_its_local_time_and_names_no_cause(client: TestClient, local: str) -> None:
    hint = upload(client, photo(local, None), "image/jpeg")["hints"]["captureTime"]
    assert hint == {"localDateTime": local, "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
                    "source": {"container": "exif", "tags": ["DateTimeOriginal"]}}


TIME_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|\b0001\b|\b9999\b")


@pytest.mark.parametrize(("reader", "local", "offset"), [("photo", "0001-01-01T02:59:59", "+03:00"),
                                                         ("photo", "9999-12-31T21:00:00", "-03:00"),
                                                         ("clip", "9999-12-31T10:00:00", "-14:00")])
def test_no_sentence_states_a_time_for_an_offset_not_applied(
        client: TestClient, tmp_path: Path, reader: str, local: str, offset: str) -> None:
    from services.science.predicted_sky import NO_TIME

    body = photo(local, offset, gps=True) if reader == "photo" else clip(tmp_path, local, offset)
    receipt = upload(client, body, "image/jpeg" if reader == "photo" else "video/quicktime")
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    assert job["state"] == "succeeded", job
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert "observedAtUTC" not in solution and solution["warnings"][-1] == NO_TIME
    sentences = [*solution["warnings"], receipt["sourceAsset"]["title"],
                 *(text for finding in solution["findings"] for text in (finding["title"], finding.get("subtitle", "")))]
    assert not any(TIME_LIKE.search(sentence) for sentence in sentences), sentences


def test_the_first_years_are_written_with_four_digits() -> None:
    # CI's Linux writes %Y without its leading zeros below year 1000; the contract's patterns need four digits
    assert format_utc(datetime(1, 1, 1, 0, 0, tzinfo=timezone.utc)) == "0001-01-01T00:00:00Z"
    assert format_utc(datetime(999, 7, 1, 2, 3, 4, 5000, tzinfo=timezone.utc)) == "0999-07-01T02:03:04.005Z"
    assert _format_local(datetime(1, 1, 1, 0, 0)) == "0001-01-01T00:00:00"
    assert _format_local(datetime(999, 7, 1, 2, 3, 4, 5000)) == "0999-07-01T02:03:04.005"


# ---- The contract's term, by the wire rules ----

NOT_APPLIED = {"localDateTime": "9999-12-31T21:00:00", "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
               "source": {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}, "offsetNotApplied": OUTSIDE}
CLIP_NOT_APPLIED = {**NOT_APPLIED, "localDateTime": "0001-01-01T02:59:59",
                    "source": {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]}}


def changed(base: dict, **fields: Any) -> dict:
    out = {**base, "source": dict(base["source"])}
    for key, value in fields.items():
        if key in ("container", "tags", "basis"):
            out["source"][key] = value
        elif value is _DROP:
            out.pop(key, None)
        else:
            out[key] = value
    return out


_DROP = object()
CONTRACT_EDGES = [
    (NOT_APPLIED, True, "an EXIF time whose offset tag gives an instant past the calendar's end"),
    (CLIP_NOT_APPLIED, True, "a clip's creation date whose instant falls before the calendar's start"),
    # The local time must lie within the largest offset, 14 hours, of an end: each inside end and a millisecond out
    (changed(NOT_APPLIED, localDateTime="0001-01-01T13:59:59.999"), True, "the last local time an offset can carry before year 1"),
    (changed(NOT_APPLIED, localDateTime="0001-01-01T14:00:00"), False, "no offset carries this local time before year 1"),
    (changed(NOT_APPLIED, localDateTime="9999-12-31T10:00:00"), True, "the first local time an offset can carry past year 9999"),
    (changed(NOT_APPLIED, localDateTime="9999-12-31T09:59:59.999"), False, "no offset carries this local time past year 9999"),
    (changed(NOT_APPLIED, offsetNotApplied="instant_past_the_calendar"), False, "a cause not in the closed list"),
    (changed(NOT_APPLIED, offsetNotApplied=None), False, "null where the field may only be absent"),
    (changed(NOT_APPLIED, offsetNotApplied=_DROP, offset_not_applied=OUTSIDE), False, "a Python field name on the wire"),
    (changed(NOT_APPLIED, offsetNotApplied=_DROP, offsetnotapplied=OUTSIDE), False, "an unknown key"),
    (changed(NOT_APPLIED, utcOffsetMinutes=-180, utc="9999-12-31T23:59:59Z"), False, "a cause with an offset"),
    # Decided by the "no offset" rule alone (audit J): the offset applied with its true UTC, and the cause beside it;
    # the neighbours, the same records without the offset or without the cause, are taken
    (changed(NOT_APPLIED, utcOffsetMinutes=180, utc="9999-12-31T18:00:00Z"), False, "a cause beside an applied offset, past the end"),
    (changed(CLIP_NOT_APPLIED, utcOffsetMinutes=-180, utc="0001-01-01T05:59:59Z"), False, "a cause beside an applied offset, before the start"),
    (changed(NOT_APPLIED, offsetNotApplied=_DROP, utcOffsetMinutes=180, utc="9999-12-31T18:00:00Z"), True, "the applied offset with no cause"),
    (changed(CLIP_NOT_APPLIED, offsetNotApplied=_DROP, utcOffsetMinutes=-180, utc="0001-01-01T05:59:59Z"), True,
     "the applied offset with no cause, before the start"),
    (changed(NOT_APPLIED, tags=["DateTimeOriginal"]), False, "an EXIF time names the offset tag it did not apply"),
    (changed(CLIP_NOT_APPLIED, tags=["creation_time"]), False, "a clip names the creation date it did not apply"),
    (changed(NOT_APPLIED, container="heif"), False, "only an EXIF tag or a clip's creation date states such an offset"),
    (changed(NOT_APPLIED, marks="file_written"), False, "a file's own written time states no offset"),
    (changed(NOT_APPLIED, localDateTime=None, utc="9999-12-31T23:59:59Z"), False, "a cause needs the local time it kept"),
    (changed(NOT_APPLIED, basis={"placeTimeZone": "unchecked", "gpsTimeStamp": "no_usable_time_stamp"}), False,
     "a cause leaves nothing for the GPS sources to check"),
    # A record that claims an instant past the calendar is refused, not a crash
    ({**changed(NOT_APPLIED, offsetNotApplied=_DROP), "utcOffsetMinutes": -180, "utc": "9999-12-31T23:00:00Z"}, False,
     "utc must be the local time minus the offset"),
    ({**changed(NOT_APPLIED, offsetNotApplied=_DROP, localDateTime="0001-01-01T01:00:00"), "utcOffsetMinutes": 180,
      "utc": "0001-01-01T00:00:00Z"}, False, "utc must be the local time minus the offset, before year 1"),
]


@pytest.mark.parametrize(("record", "ok", "reason"), CONTRACT_EDGES, ids=[edge[2] for edge in CONTRACT_EDGES])
def test_the_contract_judges_the_cause_by_the_wire_rules(record: dict, ok: bool, reason: str) -> None:
    if ok:
        dumped = CaptureTimeHint.model_validate(record).model_dump(by_alias=True, exclude_none=True)
        assert dumped.get("offsetNotApplied") == record.get("offsetNotApplied")
        assert dumped.get("utc") == record.get("utc")
    else:
        with pytest.raises(ValidationError):
            CaptureTimeHint.model_validate(record)


# ---- Media discovery's creation date (capture-time-2 v2: audit J's note, the same defect one file over) ----

# A clip's creationdate as discovery reads it, its local time and zone kept apart (joined when used), the capture moment
# discovery reports (None where the instant lies outside years 1 to 9999), and whether the item says why no prediction
# rests on the frame it decodes, a second after the start. Each calendar end a second inside and a second outside, with
# the smallest offsets and with Z; the ephemeris' last second, whose frame lies past it, and the second before
DISCOVERY_EDGES = [
    ("9999-12-31T21:00:00", "-0300", None, False),
    ("9999-12-31T20:59:59", "-0300", "9999-12-31T23:59:59Z", True),
    ("9999-12-31T23:59:59", "Z", "9999-12-31T23:59:59Z", True),
    ("0001-01-01T02:59:59", "+0300", None, False),
    ("0001-01-01T03:00:00", "+0300", "0001-01-01T00:00:00Z", True),
    ("0001-01-01T00:00:00", "Z", "0001-01-01T00:00:00Z", True),
    ("0999-12-31T02:03:04", "Z", "0999-12-31T02:03:04Z", True),
    ("2050-12-31T23:59:59", "Z", "2050-12-31T23:59:59Z", True),
    ("2050-12-31T23:59:58", "Z", "2050-12-31T23:59:58Z", False),
    # A fraction of a second: kept to the millisecond, the most the contract's instant holds, cut below it, as ingest's
    # reader of the same tag keeps it (AUDIT L's finding 2)
    ("2030-06-01T12:00:00.5", "Z", "2030-06-01T12:00:00.500Z", False),
    ("2030-06-01T12:00:00.0005", "Z", "2030-06-01T12:00:00Z", False),
    ("2030-06-01T12:00:00.999999", "+0300", "2030-06-01T09:00:00.999Z", False),
]


@pytest.mark.parametrize(("local", "zone", "utc", "error"), DISCOVERY_EDGES)
def test_discovery_reads_a_creation_date_at_either_end_of_the_calendar(local: str, zone: str, utc: str | None, error: bool) -> None:
    from services.media.discovery import normalize_creation_utc

    assert normalize_creation_utc(local + zone) == utc


@pytest.mark.parametrize(("local", "zone", "utc", "error"), DISCOVERY_EDGES)
def test_discovery_and_ingest_read_the_same_creation_date_alike(local: str, zone: str, utc: str | None, error: bool) -> None:
    # The two readers of one clip's com.apple.quicktime.creationdate give it the same instant, or both none
    from services.ingest.metadata import quicktime_hints
    from services.media.discovery import normalize_creation_utc

    assert quicktime_hints({"com.apple.quicktime.creationdate": local + zone})["captureTime"]["utc"] == normalize_creation_utc(local + zone)


def beacon_clip(path: Path, creationdate: str) -> None:
    """A 2 s clip at 10 fps: a dark sky over a lit foreground, with a compact bright source high in the sky only from
    1.0 to 1.9 s, so of the frames discovery decodes at 1 fps it takes the one ffmpeg times at 1 s (as
    test_discovery_reports_what_it_read.py's clip with a beacon in its fourth second does)."""
    run_bounded(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=2", "-vf",
         "drawbox=x=0:y=100:w=320:h=80:color=gray:t=fill,"
         "drawbox=x=150:y=10:w=6:h=6:color=white:t=fill:enable='between(t,1,1.9)'",
         "-c:v", "mpeg4", "-q:v", "2", "-movflags", "use_metadata_tags", "-metadata", f"com.apple.quicktime.creationdate={creationdate}",
         "-metadata", "com.apple.quicktime.location.ISO6709=+30.0000-040.0000/", str(path)],
        "ffmpeg making a beacon clip",
    )


REAL_POPEN = subprocess.Popen


def stop_own_child(child: subprocess.Popen) -> None:
    """Stop a child this test started in a session of its own, with its group, while it is still unreaped: its id, and
    so its group's, cannot belong to anyone else until this test waits for it. Nothing is stopped by name."""
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGKILL)


def discover_within_bound(folders: Any) -> Any:
    """GET /api/media/discover, waited for within CHILD_BOUND_SEC by a thread of its own: a route that never answers
    fails the test by an assertion instead of holding it, and leaves nothing running.

    When the wait runs out, the helper refuses any further child (subprocess.Popen, which subprocess.run also uses, is
    wrapped while the route runs), tells every guard discovery has running to end its group
    (discovery.abandon_running_children, which closes the pipe each guard watches, so no id is signalled), waits up to
    CHILD_BOUND_SEC more for the thread to end, and fails. The thread is a daemon, so even one that never ends does not
    hold the process at its exit."""
    from services.api import create_app
    from services.media import discovery

    lock = threading.Lock()
    gave_up = threading.Event()
    real_popen = subprocess.Popen

    class Refusing(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            with lock:
                if gave_up.is_set():
                    raise RuntimeError("the test stopped waiting for the route: no child is started")
                super().__init__(*args, **kwargs)

    outcome: dict[str, Any] = {}

    def ask() -> None:
        try:
            outcome["response"] = TestClient(create_app(media_folders=folders)).get("/api/media/discover")
        except BaseException as exc:  # noqa: BLE001  handed to the test's own thread below
            outcome["error"] = exc

    worker = threading.Thread(target=ask, name="discover_within_bound", daemon=True)
    subprocess.Popen = Refusing  # type: ignore[misc]
    try:
        worker.start()
        worker.join(CHILD_BOUND_SEC)
        if worker.is_alive():
            with lock:
                gave_up.set()
            ended_calls = discovery.abandon_running_children()
            worker.join(CHILD_BOUND_SEC)
            ended = "" if not worker.is_alive() else f"; its thread had not ended {CHILD_BOUND_SEC} s after that"
            pytest.fail(f"GET /api/media/discover did not answer within {CHILD_BOUND_SEC} s; the test ended the "
                        f"{ended_calls} call(s) discovery had running{ended}")
    finally:
        subprocess.Popen = real_popen  # type: ignore[misc]
    if "error" in outcome:
        raise outcome["error"]
    return outcome["response"]


def test_the_discover_route_lists_clips_from_both_ends_of_the_calendar(tmp_path: Path) -> None:
    """The frame discovery decodes lies a second after the start, so a clip recorded in the calendar's last second has
    no frame moment the calendar holds, and none is computed for a start outside 1800 to 2050; one recorded in 2050's
    last second has a frame past the ephemeris, which the item says. Every clip is listed and the route answers."""
    from services.media.router import MediaFolders

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for index, (local, zone, _, _) in enumerate(DISCOVERY_EDGES):
        beacon_clip(uploads / f"clip{index}.mov", local + zone)
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    response = discover_within_bound(folders)
    assert response.status_code == 200, response.text
    items = {item["filename"]: item for item in response.json()["items"]}
    assert len(items) == len(DISCOVERY_EDGES)
    for index, (_, _, utc, error) in enumerate(DISCOVERY_EDGES):
        item = items[f"clip{index}.mov"]
        assert item["capturedAtUTC"] == utc
        assert ("ephemerisError" in item) == error
        # No prediction rests on a moment outside the ephemeris' years; the one inside them gets its ephemerides
        assert (item["ephemerides"] == []) == (utc is None or error)


# ---- Discovery's own child processes are bounded (AUDIT L's finding 1) and leave nothing running (AUDIT M) ----

ROOT = Path(__file__).resolve().parents[2]


def test_discovery_waits_120_s_on_a_child() -> None:
    """The value itself: the stand-in tests below set the time limit to 2 s, so only this test sees it. The margin a
    stopped call waits for its group is the shared caller's (test_guarded_call.py)."""
    from services.media import discovery

    assert discovery.CHILD_TIME_LIMIT_SEC == 120.0


# Which call each stand-in stands for: discovery's probe, its 1 fps frame decode, and its native decode of the frame
CALL_SITES = {
    "the probe": ("ffprobe", "True"),
    "the frame decode": ("ffmpeg", '"gray" in args and "gray16le" not in args'),
    "the native decode": ("ffmpeg", '"gray16le" in args'),
}


def site_of(command: list[str]) -> str:
    """Which of discovery's three call sites a command comes from, told apart by what it runs, as CALL_SITES does."""
    tool = Path(command[0]).name
    if tool == "ffprobe":
        return "the probe"
    if tool == "ffmpeg" and "gray16le" in command:
        return "the native decode"
    if tool == "ffmpeg" and "gray" in command:
        return "the frame decode"
    return "no call site: " + " ".join(command)


# A time limit no call site would write as a number of its own
ODD_LIMIT_SEC = 97.25


def test_each_call_site_starts_its_child_through_the_one_seam_with_the_time_limit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery starts every ffprobe and ffmpeg child through one seam, _run_child, whose time limit is an argument
    with no default. With the limit set to an odd value, the test notes what each of the three call sites passes for
    one clip: a site with no limit, a limit of its own, or a child started some other way fails here at once, by an
    assertion that names the site. Afterwards discovery holds no child as running."""
    from services.media import discovery
    from services.media.router import MediaFolders

    monkeypatch.setattr(discovery, "CHILD_TIME_LIMIT_SEC", ODD_LIMIT_SEC)
    monkeypatch.setattr(discovery, "_DISCOVERY_CACHE", {})
    real_seam = discovery._run_child
    seen: list[tuple[str, Any]] = []

    def noted(command: list[str], *args: Any, **kwargs: Any) -> Any:
        seen.append((site_of(command), f"a positional {args}" if args else kwargs.get("timeout", "no time limit")))
        return real_seam(command, *args, **kwargs)

    monkeypatch.setattr(discovery, "_run_child", noted)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "one.mov", "2030-06-01T12:00:00Z")
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    try:
        answered: Any = discover_within_bound(folders)
    except Exception as exc:  # noqa: BLE001  each site is judged first, by name
        answered = exc
    for site in sorted(CALL_SITES):
        limits = [limit for where, limit in seen if where == site]
        assert limits == [ODD_LIMIT_SEC], f"{site} started its child with {limits or 'no call through the seam'}"
    assert sorted(where for where, _ in seen) == sorted(CALL_SITES)
    assert not isinstance(answered, Exception), repr(answered)
    assert answered.status_code == 200, answered.text
    [item] = answered.json()["items"]
    assert item["filename"] == "one.mov" and "decodeError" not in item
    assert discovery._RUNNING == {}
    assert subprocess.Popen is REAL_POPEN  # the helper put back what it wrapped


EMPTY_INPUT = """
import os
import sys

print("empty" if os.path.samestat(os.fstat(0), os.stat(os.devnull)) else "not empty", flush=True)
"""


def test_a_child_of_discovery_reads_an_empty_standard_input(tmp_path: Path) -> None:
    """The seam gives each child the null device as its standard input, so no child reads the terminal or the test
    runner's own input. For the call this process's own input is a pipe, so a child that took it would not see the
    null device, whatever input the test run itself was given."""
    from services.media import discovery

    script = tmp_path / "empty_input.py"
    script.write_text(EMPTY_INPUT)
    read_end, write_end = os.pipe()
    own_input = os.dup(0)
    try:
        os.dup2(read_end, 0)
        done = discovery._run_child([sys.executable, "-I", str(script)], timeout=float(CHILD_BOUND_SEC))
    finally:
        os.dup2(own_input, 0)
        for end in (own_input, read_end, write_end):
            os.close(end)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"empty\n", b"")


# A stand-in for ffprobe or ffmpeg, put first on PATH. It logs each call it gets, by which file and its process id;
# for a file whose name holds "hang", at the one call it stands for ("met"), it starts a child of its own (logged as
# "kid") and then acts as BEHAVIOURS says; every other call it hands to the real tool unchanged. A stand-in and its
# child end on their own after STANDIN_LIFETIME_SEC, so a test that finds one left running needs to stop nothing
# (no test here signals a process it did not start and has not yet waited for)
STANDIN_LIFETIME_SEC = 120
STANDIN = """#!{python}
import os, signal, subprocess, sys, time
args = sys.argv[1:]
named = "hang" in " ".join(args)
met = named and {when}


def log(kind, pid):
    with open({calls!r}, "a") as calls:
        calls.write(kind + " " + str(pid) + "\\n")


log("met" if met else "hang" if named else "plain", os.getpid())
if met:
{behave}
os.execv({real!r}, [{real!r}, *args])
"""
KID = f"sleep {STANDIN_LIFETIME_SEC}"
BEHAVIOURS = {
    # Never answers, ignores SIGTERM, SIGINT and SIGHUP (and so does its child, which inherits that), and its child
    # keeps the pipes discovery reads open
    "never answers": ("    for deaf in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):\n        signal.signal(deaf, signal.SIG_IGN)\n"
                      f"    log('kid', subprocess.Popen({KID.split()!r}).pid)\n    time.sleep({STANDIN_LIFETIME_SEC})"),
    # Its child lets go of the pipes, and it fails at once: discovery reads its output to the end, and its exit
    # At its call, the first time only, it is ended by SIGKILL; every later call goes to the real tool
    "ends by a signal once": ("    once = os.path.join(os.path.dirname(sys.argv[0]), 'signalled_once')\n"
                              "    if not os.path.exists(once):\n        open(once, 'w').close()\n"
                              "        os.kill(os.getpid(), signal.SIGKILL)"),
    "fails": (f"    log('kid', subprocess.Popen({KID.split()!r}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)\n"
              "    sys.exit(1)"),
}


def make_standin(folder: Path, tool: str, when: str, behaviour: str, calls: Path) -> Path:
    folder.mkdir(exist_ok=True)
    standin = folder / tool
    standin.write_text(STANDIN.format(python=sys.executable, when=when, calls=str(calls), behave=BEHAVIOURS[behaviour],
                                      real=shutil.which(tool)))
    standin.chmod(0o755)
    return standin


def logged(calls: Path, kind: str) -> list[int]:
    lines = [line.split() for line in calls.read_text().splitlines()] if calls.exists() else []
    return [int(pid) for what, pid in lines if what == kind]


def shown(pid: int) -> tuple[str, str] | None:
    """The state letter and command line of the process with this id, or None when there is none: from /proc where the
    system has it (Linux, where ps is not in every image), else from ps (macOS)."""
    if Path("/proc/self/stat").exists():
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text()
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            return None
        return stat.rsplit(")", 1)[1].split()[0], command.strip()
    found = subprocess.run(["ps", "-o", "stat=,command=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=CHILD_BOUND_SEC).stdout.split(None, 1)
    return (found[0], found[1].strip()) if len(found) == 2 else None


def running(pid: int, holds: str) -> bool:
    """A process has this id, has not ended, and is the one meant: its command line holds `holds`, so an id the
    system has since given to another process is never taken for it. A zombie (ended, not yet reaped by its parent,
    which for an orphan is the system's first process) is not running: its state is Z."""
    found = shown(pid)
    return found is not None and not found[0].startswith("Z") and holds in found[1]


# How long a process sent SIGKILL may take to be seen ended: it ends when it next runs, which under a heavy load can
# be a while; a process the guard did not end is still running at this margin's end
LEFT_MARGIN_SEC = 5.0


def left_running(calls: Path, within: float = LEFT_MARGIN_SEC) -> list[str]:
    """Each stand-in (its path lies in the log's folder) and child of one (KID) the log names that is still running
    `within` seconds from now, as "<kind> <id>"; an empty list says none is. Nothing is stopped here."""
    deadline = time.monotonic() + within
    while True:
        left = [f"{kind} {pid}" for kind, holds in (("met", str(calls.parent)), ("kid", KID))
                for pid in logged(calls, kind) if running(pid, holds)]
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.05)


def test_the_check_for_what_is_left_sees_a_running_child_a_zombie_and_an_end(tmp_path: Path) -> None:
    """left_running on this system: a KID the log names is seen running; once stopped and before it is reaped (a
    zombie) it is not running; once reaped it is gone. So an empty list from it means something on both systems."""
    calls = tmp_path / "calls.txt"
    child = subprocess.Popen(KID.split(), stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        calls.write_text(f"kid {child.pid}\n")
        assert running(child.pid, KID) and not running(child.pid, "sleep 600")
        assert left_running(calls, within=0.0) == [f"kid {child.pid}"]
        stop_own_child(child)
        deadline = time.monotonic() + CHILD_BOUND_SEC
        while not (shown(child.pid) or ("",))[0].startswith("Z") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (shown(child.pid) or ("",))[0].startswith("Z")
        assert not running(child.pid, KID) and left_running(calls, within=0.0) == []
    finally:
        stop_own_child(child)
        child.wait()
    assert left_running(calls, within=0.0) == []


@pytest.mark.parametrize("site", sorted(CALL_SITES))
def test_a_child_of_discovery_that_never_answers_is_ended_with_its_group_and_the_route_answers(
        site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery waits on each ffprobe and ffmpeg call no longer than its bound (here 2 s), then has the call's guard
    end the whole group, so the tool's own child is gone too; it lists the file with a plain reason that it could not
    be read in time, still lists the clip beside it, and keeps no record of the timed-out read, so the next discovery
    tries again, while the clip it read in time is kept and not read again."""
    from services.media import discovery
    from services.media.router import MediaFolders

    tool, when = CALL_SITES[site]
    calls = tmp_path / "calls.txt"
    standins = tmp_path / "standins"
    make_standin(standins, tool, when, "never answers", calls)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "hang.mov", "2030-06-01T12:00:00Z")
    beacon_clip(uploads / "plain.mov", "2030-06-01T12:00:01Z")
    monkeypatch.setenv("PATH", f"{standins}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(discovery, "CHILD_TIME_LIMIT_SEC", 2.0)
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    for attempt in (1, 2):
        response = discover_within_bound(folders)
        assert response.status_code == 200, response.text
        body = response.json()
        items = {item["filename"]: item for item in body["items"]}
        assert items["plain.mov"]["capturedAtUTC"] == "2030-06-01T12:00:01Z" and "decodeError" not in items["plain.mov"]
        if site == "the probe":
            assert "hang.mov" not in items
            assert body["skipped"] == [{"filename": "hang.mov", "reason": "ffprobe did not finish reading the file within 2 s"}]
        else:
            assert items["hang.mov"]["decodeError"] == "ffmpeg did not decode a frame within 2 s"
        # Each try met the stand-in, which had a child of its own running: both were ended, nothing is left
        assert len(logged(calls, "met")) == attempt
        assert len(logged(calls, "kid")) == attempt
        assert left_running(calls) == []
        # The plain clip was read on the first try only: its item was kept, the timed-out one was not
        plain = len(logged(calls, "plain"))
        if attempt == 1:
            first_plain = plain
            assert plain >= 1
        else:
            assert plain == first_plain


@pytest.mark.parametrize("site", sorted(CALL_SITES))
def test_a_child_of_discovery_that_fails_leaves_nothing_of_its_group_running(
        site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that starts a child of its own, which lets go of the pipes, and fails at once: discovery reads its end,
    says the file could not be read, lists the clip beside it, and the guard ends what is left of the group, so the
    tool's own child does not outlive the call. A failed decode is kept (ffmpeg gave its answer for this file: the
    next discovery does not ask again); a failed probe is not kept (each discovery probes again)."""
    from services.media import discovery
    from services.media.router import MediaFolders

    tool, when = CALL_SITES[site]
    calls = tmp_path / "calls.txt"
    standins = tmp_path / "standins"
    make_standin(standins, tool, when, "fails", calls)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "hang.mov", "2030-06-01T12:00:00Z")
    beacon_clip(uploads / "plain.mov", "2030-06-01T12:00:01Z")
    monkeypatch.setenv("PATH", f"{standins}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(discovery, "_DISCOVERY_CACHE", {})
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    for attempt in (1, 2):
        response = discover_within_bound(folders)
        assert response.status_code == 200, response.text
        body = response.json()
        items = {item["filename"]: item for item in body["items"]}
        assert items["plain.mov"]["capturedAtUTC"] == "2030-06-01T12:00:01Z" and "decodeError" not in items["plain.mov"]
        if site == "the probe":
            assert body["skipped"] == [{"filename": "hang.mov", "reason": "ffprobe could not read the file"}]
        else:
            assert items["hang.mov"]["decodeError"]
        assert len(logged(calls, "met")) == (attempt if site == "the probe" else 1)
        assert left_running(calls) == []
        assert discovery._RUNNING == {}


# ---- What discovery keeps, and what each reason names (AUDIT G's finding 2 on the fifth version) ----

def run_ending(end: Any, stdout: bytes = b"out", stderr: bytes = b"err") -> Callable[..., Any]:
    """guarded_call.run standing in for a call that ended as `end` says."""
    from services.media import guarded_call

    return lambda command, **_: guarded_call.Finished(end, stdout, stderr)


def test_discovery_returns_only_the_tool_s_own_exit_and_names_every_other_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_child reads the typed end of the shared caller: an exit, with its status (0 or not), is the tool's own
    answer and comes back; every other end raises with a reason that names it, so no call site can take it for an
    answer about the file [proposed: the wording is for the UIUX work]."""
    from services.media import discovery, guarded_call
    from services.media.guarded_call import Bound, NotStartedCause

    command = ["ffmpeg", "-i", "x"]
    for status in (0, 3):
        monkeypatch.setattr(guarded_call, "run", run_ending(guarded_call.Exited(status)))
        done = discovery._run_child(command, timeout=2.0)
        assert (done.args, done.returncode, done.stdout, done.stderr) == (command, status, b"out", b"err")
    monkeypatch.setattr(guarded_call, "run", run_ending(guarded_call.BoundPassed(Bound.CALL, 2.0)))
    with pytest.raises(subprocess.TimeoutExpired) as timed_out:
        discovery._run_child(command, timeout=2.0)
    assert (timed_out.value.timeout, timed_out.value.output, timed_out.value.stderr) == (2.0, b"out", b"err")
    raised = {
        guarded_call.NotStarted(NotStartedCause.NOT_FOUND): (discovery.ToolNotStarted, "ffmpeg was not found on this machine"),
        guarded_call.NotStarted(NotStartedCause.NOT_RUNNABLE): (discovery.ToolNotStarted, "ffmpeg could not be started"),
        guarded_call.Signalled(9): (discovery.ToolDidNotFinish, "ffmpeg was ended by SIGKILL before it answered"),
        guarded_call.Signalled(15): (discovery.ToolDidNotFinish, "ffmpeg was ended by SIGTERM before it answered"),
        guarded_call.Signalled(250): (discovery.ToolDidNotFinish, "ffmpeg was ended by signal 250 before it answered"),
        guarded_call.GuardNotStarted(): (discovery.ToolDidNotFinish, "an internal error stopped the ffmpeg call before it ran"),
        guarded_call.Cut(by_caller=True): (discovery.ToolDidNotFinish, "ffmpeg did not finish: the call was ended before it did"),
        guarded_call.Cut(by_caller=False): (discovery.ToolDidNotFinish, "ffmpeg did not finish: the call was ended before it did"),
    }
    for end, (kind, reason) in raised.items():
        monkeypatch.setattr(guarded_call, "run", run_ending(end))
        with pytest.raises(kind) as got:
            discovery._run_child(command, timeout=2.0)
        assert (type(got.value), str(got.value)) == (kind, reason), end


@pytest.mark.parametrize("site", sorted(CALL_SITES))
def test_a_tool_ended_by_a_signal_is_not_kept_as_its_answer_and_the_reason_says_so(
        site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUDIT G's probe made a test: the tool at one call site is ended by SIGKILL on its first call only (as the
    out-of-memory killer or an operator would end it), and a second try passes. The first discovery says the tool was
    ended by a signal, never that the file could not be read or decoded; nothing of it is kept, so the second discovery
    asks again and reads the clip whole."""
    from services.media import discovery
    from services.media.router import MediaFolders

    tool, when = CALL_SITES[site]
    calls = tmp_path / "calls.txt"
    standins = tmp_path / "standins"
    make_standin(standins, tool, when, "ends by a signal once", calls)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "hang.mov", "2030-06-01T12:00:00Z")
    beacon_clip(uploads / "plain.mov", "2030-06-01T12:00:01Z")
    monkeypatch.setenv("PATH", f"{standins}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(discovery, "_DISCOVERY_CACHE", {})
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    reason = f"{tool} was ended by SIGKILL before it answered"
    body = discover_within_bound(folders).json()
    items = {item["filename"]: item for item in body["items"]}
    assert items["plain.mov"]["capturedAtUTC"] == "2030-06-01T12:00:01Z" and "decodeError" not in items["plain.mov"]
    if site == "the probe":
        assert "hang.mov" not in items and body["skipped"] == [{"filename": "hang.mov", "reason": reason}]
    else:
        assert items["hang.mov"]["decodeError"] == reason
    body = discover_within_bound(folders).json()
    items = {item["filename"]: item for item in body["items"]}
    assert items["hang.mov"]["capturedAtUTC"] == "2030-06-01T12:00:00Z" and "decodeError" not in items["hang.mov"]
    assert body["skipped"] == []
    assert len(logged(calls, "met")) == 2
    assert left_running(calls) == []
    assert discovery._RUNNING == {}


def test_a_guard_that_cannot_run_is_an_internal_error_and_is_not_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's file gone: no tool is blamed and the file is not said to be unreadable; the reason is an internal
    error, nothing is kept, and once the guard is back the next discovery reads each clip whole."""
    from services.media import discovery, guarded_call
    from services.media.router import MediaFolders

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "one.mov", "2030-06-01T12:00:00Z")
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    monkeypatch.setattr(discovery, "_DISCOVERY_CACHE", {})
    with monkeypatch.context() as patch:
        patch.setattr(guarded_call, "GUARD", tmp_path / "no-such-guard.py")
        body = discover_within_bound(folders).json()
    assert body["items"] == []
    assert body["skipped"] == [{"filename": "one.mov", "reason": "an internal error stopped the ffprobe call before it ran"}]
    [item] = discover_within_bound(folders).json()["items"]
    assert item["capturedAtUTC"] == "2030-06-01T12:00:00Z" and "decodeError" not in item
    assert discovery._RUNNING == {}


def tools_only(folder: Path, *tools: str) -> str:
    """A PATH holding only links to these tools, as on a machine where the others are not installed."""
    folder.mkdir()
    for tool in tools:
        (folder / tool).symlink_to(shutil.which(tool))
    return str(folder)


def test_a_tool_that_is_not_installed_leaves_each_clip_its_row_with_a_plain_reason(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ffprobe or ffmpeg not on the path: the route still answers, each clip keeps its row, and the reason names the
    tool as not found [proposed: the sentence a person reads is for the UIUX work]. Nothing of it is kept: once the
    tool is there, the next discovery reads each clip whole."""
    from services.media import discovery
    from services.media.router import MediaFolders

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    beacon_clip(uploads / "one.mov", "2030-06-01T12:00:00Z")
    beacon_clip(uploads / "two.mov", "2030-06-01T12:00:01Z")
    folders = MediaFolders(uploads=uploads, public=tmp_path / "public", artifacts=tmp_path / "artifacts")
    monkeypatch.setattr(discovery, "_DISCOVERY_CACHE", {})
    with monkeypatch.context() as patch:
        patch.setenv("PATH", tools_only(tmp_path / "no-ffprobe", "ffmpeg"))
        response = discover_within_bound(folders)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert sorted(response.json()["skipped"], key=lambda row: row["filename"]) == [
        {"filename": name, "reason": "ffprobe was not found on this machine"} for name in ("one.mov", "two.mov")]
    with monkeypatch.context() as patch:
        patch.setenv("PATH", tools_only(tmp_path / "no-ffmpeg", "ffprobe"))
        response = discover_within_bound(folders)
    assert response.status_code == 200, response.text
    items = {item["filename"]: item for item in response.json()["items"]}
    assert sorted(items) == ["one.mov", "two.mov"]
    for item in items.values():
        assert item["decodeError"] == "ffmpeg was not found on this machine"
        assert item["capturedAtUTC"] is not None  # the probe read the clip
    items = {item["filename"]: item for item in discover_within_bound(folders).json()["items"]}
    assert sorted(items) == ["one.mov", "two.mov"] and all("decodeError" not in item for item in items.values())


def test_a_tool_on_the_path_that_cannot_be_started_says_so(tmp_path: Path) -> None:
    """A tool the path names but the system cannot run (here a script whose interpreter does not exist): the guard
    reports that it did not start the tool, and the call raises ToolNotStarted with the plain reason, never kept."""
    from services.media import discovery

    broken = tmp_path / "bin" / "ffprobe"
    broken.parent.mkdir()
    broken.write_text("#!/no/such/interpreter\n")
    broken.chmod(0o755)
    with pytest.raises(discovery.ToolNotStarted, match="^ffprobe could not be started$"):
        discovery._run_child([str(broken), "-version"], timeout=float(CHILD_BOUND_SEC))
    assert discovery._RUNNING == {}


GIVE_UP = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import tests.integration.test_capture_time_outside_the_calendar as unit
from services.media import discovery
from services.media.router import MediaFolders

work, case = Path(sys.argv[2]), sys.argv[3]
# In this interpreter only: discovery's time limit taken away, or its probe stuck in its own code, and the helper's
# wait cut to 3 s
if case == "a child that never answers":
    discovery.CHILD_TIME_LIMIT_SEC = None
else:
    discovery._probe_media_metadata = lambda path: time.sleep(10 ** 6)
unit.CHILD_BOUND_SEC = 3
try:
    unit.discover_within_bound(MediaFolders(uploads=work / "uploads", public=work / "public", artifacts=work / "artifacts"))
    print("the route answered", flush=True)
except BaseException as exc:
    print(type(exc).__name__ + ": " + str(exc), flush=True)
print("script end reached", flush=True)
"""
# How soon after its last line a child interpreter of a test here must have ended
INTERPRETER_END_SEC = 10.0


def run_interpreter(script: Path, *args: str, env: dict[str, str]) -> tuple[list[str], float]:
    """Run `script` in a child interpreter of this test's own, in a group of its own, and return the lines it printed
    and how many seconds after its last line, "script end reached", it ended. One still running CHILD_BOUND_SEC after
    it started is stopped with its group while it is still unreaped (stop_own_child), and the test fails."""
    child = subprocess.Popen([sys.executable, "-I", str(script), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)
    lines: list[tuple[float, str]] = []

    def read() -> None:
        for line in child.stdout:  # type: ignore[union-attr]
            lines.append((time.monotonic(), line.rstrip("\n")))

    reader = threading.Thread(target=read, name="run_interpreter", daemon=True)
    reader.start()
    try:
        child.wait(timeout=CHILD_BOUND_SEC)
    except subprocess.TimeoutExpired:
        stop_own_child(child)
        child.wait()
        pytest.fail(f"the child interpreter was still running {CHILD_BOUND_SEC} s after it started (stopped with its "
                    f"group); it printed {[line for _, line in lines]}")
    ended = time.monotonic()
    reader.join(INTERPRETER_END_SEC)
    child.stdout.close()  # type: ignore[union-attr]
    end = [at for at, line in lines if line == "script end reached"]
    assert end, [line for _, line in lines]
    return [line for _, line in lines], ended - end[-1]


GAVE_UP = "Failed: GET /api/media/discover did not answer within 3 s; the test ended the "
GIVE_UP_CASES = {
    # Two hung files: once the helper gives up, the route starts no child for the second
    "a child that never answers": (["hang1.mov", "hang2.mov"], GAVE_UP + "1 call(s) discovery had running"),
    # No call to end, and a thread that does not end: a daemon, so the process ends all the same
    "the route's own code": (["hang1.mov"], GAVE_UP + "0 call(s) discovery had running; its thread had not ended 3 s after that"),
}


@pytest.mark.parametrize("case", sorted(GIVE_UP_CASES))
def test_a_test_that_gives_up_on_the_route_ends_what_it_started_and_its_process_ends(case: str, tmp_path: Path) -> None:
    """discover_within_bound in a child interpreter of this test's own, where discovery's time limit is taken away and
    ffprobe never answers (and has a child of its own), or its probe is stuck in its own code: the helper fails by its
    assertion after its 3 s wait, having had each running call's guard end its group and refused any other child; the
    interpreter ends within INTERPRETER_END_SEC of its last line, and nothing it started is running."""
    files, said = GIVE_UP_CASES[case]
    calls = tmp_path / "calls.txt"
    standins = tmp_path / "standins"
    make_standin(standins, "ffprobe", "True", "never answers", calls)
    uploads = tmp_path / "work" / "uploads"
    uploads.mkdir(parents=True)
    for second, name in enumerate(files):
        beacon_clip(uploads / name, f"2030-06-01T12:00:0{second}Z")
    script = tmp_path / "give_up.py"
    script.write_text(GIVE_UP)
    printed, after_end = run_interpreter(script, str(ROOT), str(tmp_path / "work"), case,
                                         env={**os.environ, "PATH": f"{standins}{os.pathsep}{os.environ['PATH']}"})
    assert printed[-2:] == [said, "script end reached"], printed
    assert after_end < INTERPRETER_END_SEC
    assert len(logged(calls, "met")) == (case == "a child that never answers")
    assert len(logged(calls, "kid")) == (case == "a child that never answers")
    assert left_running(calls) == []
