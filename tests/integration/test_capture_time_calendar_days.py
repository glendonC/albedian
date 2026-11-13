"""A capture time names a real calendar day, and a named offset tag with no offset says why (unit capture-time-4).

Wire rule 4: a time is on a real calendar day (leap years by the full rule) and never in year zero. The ingest
contract's local time and its own UTC instants took any day the pattern allows (30 February, year 0000); both now take
only a real day, judged the same way in Zod and Pydantic (the wire module's check). And a recorded capture time that
names the tag its offset came from (EXIF OffsetTimeOriginal, a clip's creationdate) but gives no offset and no instant
must say why: `offsetNotApplied` (capture-time-2) is the one reason the readers give.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest

from services.contracts.ingest import NAMED_OFFSET_TAG_MESSAGE, CaptureTimeHint, SolveJob, SourceAsset

# One table of days for both a local time and a UTC instant: each inside end, and one step outside it
CALENDAR_DAYS = [
    ("0001-01-01", True), ("0000-12-31", False), ("9999-12-31", True),
    ("2031-01-31", True), ("2031-02-28", True), ("2031-02-29", False), ("2032-02-29", True), ("2032-02-30", False),
    ("2000-02-29", True), ("1900-02-29", False), ("2031-04-30", True), ("2031-04-31", False), ("2031-06-31", False),
    ("2031-12-31", True), ("2031-11-31", False),
]
LOCAL = {"utcOffsetMinutes": None, "utc": None, "marks": "recorded", "source": {"container": "exif", "tags": ["DateTimeOriginal"]}}
WRITTEN = {"localDateTime": None, "utcOffsetMinutes": None, "marks": "file_written",
           "source": {"container": "quicktime", "tags": ["creation_time"]}}


def judged(model: Any, record: dict) -> bool:
    try:
        model.model_validate(record)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(("day", "real"), CALENDAR_DAYS)
def test_a_local_capture_time_is_on_a_real_calendar_day(day: str, real: bool) -> None:
    assert judged(CaptureTimeHint, {**LOCAL, "localDateTime": f"{day}T12:00:00"}) == real
    assert judged(CaptureTimeHint, {**LOCAL, "localDateTime": f"{day}T23:59:59.999"}) == real


@pytest.mark.parametrize(("day", "real"), CALENDAR_DAYS)
def test_a_utc_capture_time_is_on_a_real_calendar_day(day: str, real: bool) -> None:
    assert judged(CaptureTimeHint, {**WRITTEN, "utc": f"{day}T12:00:00Z"}) == real


@pytest.mark.parametrize(("day", "real"), CALENDAR_DAYS)
def test_an_asset_s_capture_instant_is_on_a_real_calendar_day(day: str, real: bool) -> None:
    """The asset's capturedAtUTC on the same table (main took year 0000 there from 5d6dac19 to capture-time-4 v3)."""
    from services.contracts.ingest import build_ingest_samples

    photo = next(asset for asset in build_ingest_samples()["valid"]["SourceAsset"] if asset.get("capturedAtUTC"))
    assert judged(SourceAsset, photo)
    assert judged(SourceAsset, {**photo, "capturedAtUTC": f"{day}T12:00:00Z"}) == real


# The converse of capture-time-2's rule: a named offset tag with no offset and no instant names why
NAMED_NO_OFFSET = [
    ({"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}, None, False, "an EXIF offset tag, no offset, no cause"),
    ({"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}, "instant_outside_the_calendar", True, "the same with its cause"),
    ({"container": "exif", "tags": ["DateTimeOriginal"]}, None, True, "no offset tag named: a local time with no zone"),
    ({"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]}, None, False, "a clip's creation date, no offset, no cause"),
    ({"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]}, "instant_outside_the_calendar", True, "the same with its cause"),
    ({"container": "heif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}, None, True, "a container whose offset tag the contract does not name"),
]


@pytest.mark.parametrize(("source", "cause", "taken", "why"), NAMED_NO_OFFSET, ids=[row[3] for row in NAMED_NO_OFFSET])
def test_a_named_offset_tag_with_no_offset_says_why(source: dict, cause: str | None, taken: bool, why: str) -> None:
    record = {**LOCAL, "localDateTime": "9999-12-31T21:00:00", "source": source}
    if cause is not None:
        record["offsetNotApplied"] = cause
    assert judged(CaptureTimeHint, record) == taken, why


def test_a_file_written_time_that_names_its_offset_tag_is_refused_too() -> None:
    """The rule holds for any capture time that names the tag. A file-written one can give no cause (a cause belongs
    to a recorded local time), so with no offset and no instant it is refused; one that names no offset tag is not."""
    written = {**LOCAL, "marks": "file_written", "localDateTime": "2031-05-02T05:42:18"}
    for source in ({"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]},
                   {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]}):
        assert not judged(CaptureTimeHint, {**written, "source": source})
        assert not judged(CaptureTimeHint, {**written, "source": source, "offsetNotApplied": "instant_outside_the_calendar"})
    assert judged(CaptureTimeHint, {**written, "source": {"container": "quicktime", "tags": ["creation_time"]}})


def test_a_named_offset_tag_with_no_offset_is_told_the_one_sentence_both_sides_write() -> None:
    assert NAMED_OFFSET_TAG_MESSAGE == "a capture time that names its offset tag gives the offset, or says why it does not"
    record = {**LOCAL, "localDateTime": "9999-12-31T21:00:00",
              "source": {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}}
    with pytest.raises(ValidationError) as refused:
        CaptureTimeHint.model_validate(record)
    assert [error["msg"] for error in refused.value.errors()] == [f"Value error, {NAMED_OFFSET_TAG_MESSAGE}"]


@pytest.mark.parametrize("field", ["createdAtUTC", "startedAtUTC", "finishedAtUTC"])
def test_a_solve_job_s_instants_are_on_a_real_calendar_day(field: str) -> None:
    from services.contracts.ingest import build_ingest_samples

    job = {**build_ingest_samples()["valid"]["SolveJob"][2], "createdAtUTC": "2032-02-29T19:20:00Z",
           "startedAtUTC": "2032-02-29T19:20:01.250Z", "finishedAtUTC": "2032-02-29T19:20:41Z"}
    assert judged(SolveJob, job)
    with pytest.raises(ValidationError, match="not a real calendar day"):
        SolveJob.model_validate({**job, field: job[field].replace("2032-02-29", "2032-02-30")})


def test_a_named_offset_tag_with_its_offset_needs_no_cause() -> None:
    applied = {"localDateTime": "2031-05-02T05:42:18", "utcOffsetMinutes": 120, "utc": "2031-05-02T03:42:18Z",
               "marks": "recorded", "source": {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}}
    assert judged(CaptureTimeHint, applied)
