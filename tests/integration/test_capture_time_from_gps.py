"""The time zone of a photo's local capture time, from two independent sources that agree (unit capture-time-1).

A photo's EXIF `DateTimeOriginal` is local wall-clock time. With `OffsetTimeOriginal` it is an instant. Without it,
the zone is worked out only when two sources agree: the time zone at the photo's own GPS position (tzfpy's boundary
data, its offset at the local time from `zoneinfo`), which must offer exactly one offset, and the GPS time stamp
(the local time minus the fix's instant minus that offset within `GPS_TIME_STAMP_LEFTOVER_LIMIT_SEC`). Where the place
offers more than one offset (two zones at a border, or a wall time a clock change repeats or skips), nothing is
derived: a fix as old as their difference would pick the other one. The GPS instant is never the capture time, and
the GPS stamps alone never give an offset: a fix an hour old would look like the next zone. An offset tag always
wins; each GPS source then only labels it. The capture time's source says what each source said (`basis`), and its
uncertainty is the whole second a tagged photo's EXIF time counts plus the leftover, rounded up to the whole second:
the GPS time stamp cannot tell a slow camera clock from an old fix, so it never states less than a tagged photo's.

Synthetic files only: the tests build JPEGs with Pillow and made-up tags. Their positions are open-ocean points, the
published coordinates of observatories and well-known towns, and made-up grid points (audit E's), each chosen for
the zones it lies in.
"""

from __future__ import annotations

import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
from PIL import ExifTags, Image, TiffImagePlugin
import pytest

from services.contracts.ingest import GPS_TIME_STAMP_LEFTOVER_LIMIT_SEC as R
from services.contracts.ingest import CaptureTimeHint, IngestReceipt
from services.ingest import metadata
from services.ingest.metadata import exif_hints, place_offsets, place_time_zones

Rat = TiffImagePlugin.IFDRational
GPS_TAGS = ["GPSDateStamp", "GPSTimeStamp"]
ZONED = "place_time_zone_agrees_with_gps_time_stamp"
# Positions, each with the zone the boundary data puts it in
OCEAN = (30.0, -40.0)  # the plate-solver fixtures' open-ocean place: the nautical zone Etc/GMT+3, UTC-03:00
OCEAN_PACIFIC = (0.0, -140.0)  # Etc/GMT+9, UTC-09:00
HANLE = (32.7794, 78.9642)  # the Indian Astronomical Observatory: Asia/Kolkata, UTC+05:30
KATHMANDU = (27.7172, 85.3240)  # Asia/Kathmandu, UTC+05:45
WAITANGI = (-43.9535, -176.5597)  # the Chatham Islands: Pacific/Chatham, UTC+12:45 in July
KIRITIMATI = (1.8721, -157.4278)  # Pacific/Kiritimati, UTC+14:00
PAGO_PAGO = (-14.2756, -170.7020)  # Pacific/Pago_Pago, UTC-11:00
GREENWICH = (51.4769, -0.0005)  # the Royal Observatory: Europe/London
# Made-up points of a 0.2 degree grid (audit E of the second version), each with the zones the boundary data holds there
OVERLAP_2H = (34.5, 78.9)  # Asia/Shanghai and Asia/Urumqi, UTC+08:00 and UTC+06:00
OVERLAP_1H = (42.5, 41.3)  # Asia/Tbilisi and Europe/Moscow, UTC+04:00 and UTC+03:00
OVERLAP_15 = (30.3, 80.9)  # Asia/Kathmandu and Asia/Kolkata, UTC+05:45 and UTC+05:30
INLAND_DST = (50.3, 10.7)  # Europe/Berlin, UTC+01:00 and UTC+02:00 in summer
SINGLE_530 = (21.0, 78.0)  # Asia/Kolkata alone
DATE_LINE_W = (0.0, 179.99)  # the nautical zone Etc/GMT-12 just west of the date line
DATE_LINE = (0.0, 180.0)  # Etc/GMT-12 on it
POLE_N = (90.0, 0.0)  # Etc/GMT
POLE_S = (-90.0, 0.0)  # Antarctica/McMurdo
LOCAL = datetime(2031, 7, 1, 4, 37, 52)


def dms(value: float) -> tuple[Rat, Rat, Rat]:
    # In whole thousandths of an arcsecond, so no part rounds up to 60 (50.3 degrees would give 59.99... seconds)
    total = round(abs(value) * 3600 * 1000)
    return (Rat(total // 3600000, 1), Rat((total // 60000) % 60, 1), Rat(total % 60000, 1000))


def position(place: tuple[float, float]) -> dict[str, Any]:
    lat, lon = place
    return {"GPSLatitudeRef": "N" if lat >= 0 else "S", "GPSLatitude": dms(lat),
            "GPSLongitudeRef": "E" if lon >= 0 else "W", "GPSLongitude": dms(lon)}


def gps_stamps(instant: datetime) -> dict[str, Any]:
    seconds = instant.second + instant.microsecond / 1e6
    return {
        "GPSDateStamp": instant.strftime("%Y:%m:%d"),
        "GPSTimeStamp": (Rat(instant.hour, 1), Rat(instant.minute, 1), Rat(round(seconds * 100), 100)),
    }


def jpeg(exif_ifd: dict[str, Any] | None = None, gps: dict[str, Any] | None = None) -> bytes:
    exif = Image.Exif()
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
    return out.getvalue()


def local_tag(moment: datetime) -> str:
    return moment.strftime("%Y:%m:%d %H:%M:%S")


def capture(local: datetime, place: tuple[float, float] | None, fix: datetime | None, tag: str | None = None) -> dict:
    exif_ifd = {"DateTimeOriginal": local_tag(local)}
    if tag is not None:
        exif_ifd["OffsetTimeOriginal"] = tag
    gps = {**(position(place) if place is not None else {}), **(gps_stamps(fix) if fix is not None else {})}
    hint = exif_hints(Image.open(io.BytesIO(jpeg(exif_ifd, gps or None))).getexif())["captureTime"]
    CaptureTimeHint.model_validate(hint)
    return hint


def utc_of(local: datetime, minutes: int) -> datetime:
    return (local - timedelta(minutes=minutes)).replace(tzinfo=timezone.utc)


def zoneless(basis: dict | None, tags: list[str]) -> dict:
    hint = {"localDateTime": LOCAL.strftime("%Y-%m-%dT%H:%M:%S"), "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
            "source": {"container": "exif", "tags": tags}}
    if basis is not None:
        hint["source"]["basis"] = basis
    return hint


# ---- The boundary data and the offsets it gives ----


@pytest.mark.parametrize(
    ("place", "zone"),
    [(OCEAN, "Etc/GMT+3"), (OCEAN_PACIFIC, "Etc/GMT+9"), (HANLE, "Asia/Kolkata"), (KATHMANDU, "Asia/Kathmandu"),
     (WAITANGI, "Pacific/Chatham"), (KIRITIMATI, "Pacific/Kiritimati"), (PAGO_PAGO, "Pacific/Pago_Pago"),
     (GREENWICH, "Europe/London")],
)
def test_each_test_position_lies_in_the_zone_it_was_chosen_for(place: tuple[float, float], zone: str) -> None:
    assert place_time_zones(*place) == (zone,)


def test_a_wall_time_a_clock_change_repeats_has_both_its_offsets() -> None:
    # Europe/London leaves summer time at 01:00 UTC on the last Sunday of October 2031, the 26th
    assert place_offsets(*GREENWICH, datetime(2031, 10, 26, 1, 30)) == (0, 60)
    assert place_offsets(*GREENWICH, datetime(2031, 7, 1, 12, 0)) == (60,)


AUDIT_JULY = datetime(2031, 7, 9, 21, 14, 7)
# Europe/Berlin goes back from 03:00 to 02:00 on the last Sunday of October 2031 and skips from 02:00 to 03:00 on the
# last Sunday of March: 02:30 is repeated on the first day and skipped on the second
REPEATED = datetime(2031, 10, 26, 2, 30, 0)
SKIPPED = datetime(2031, 3, 30, 2, 30, 0)


@pytest.mark.parametrize(
    ("place", "local", "zones", "offsets"),
    [(OVERLAP_2H, AUDIT_JULY, ("Asia/Shanghai", "Asia/Urumqi"), (360, 480)),
     (OVERLAP_1H, AUDIT_JULY, ("Asia/Tbilisi", "Europe/Moscow"), (180, 240)),
     (OVERLAP_15, AUDIT_JULY, ("Asia/Kathmandu", "Asia/Kolkata"), (330, 345)),
     (INLAND_DST, REPEATED, ("Europe/Berlin",), (60, 120)), (INLAND_DST, SKIPPED, ("Europe/Berlin",), (60, 120)),
     (INLAND_DST, AUDIT_JULY, ("Europe/Berlin",), (120,)), (SINGLE_530, AUDIT_JULY, ("Asia/Kolkata",), (330,)),
     (DATE_LINE_W, AUDIT_JULY, ("Etc/GMT-12",), (720,)), (DATE_LINE, AUDIT_JULY, ("Etc/GMT-12",), (720,)),
     (POLE_N, AUDIT_JULY, ("Etc/GMT",), (0,)), (POLE_S, AUDIT_JULY, ("Antarctica/McMurdo",), (720,))],
)
def test_each_audit_point_offers_the_zones_and_offsets_it_was_chosen_for(place: Any, local: datetime, zones: tuple, offsets: tuple) -> None:
    assert place_time_zones(*place) == zones
    assert place_offsets(*place, local) == offsets


# ---- No offset tag: the place's time zone, agreeing with the GPS time stamp ----


def test_the_place_time_zone_agreeing_with_the_gps_time_stamp_gives_the_offset() -> None:
    # The fix is 12 s older than the shutter by the camera's clock, at sea in UTC-03:00
    hint = capture(LOCAL, OCEAN, utc_of(LOCAL, -180) - timedelta(seconds=12))
    assert hint == {
        "localDateTime": "2031-07-01T04:37:52",
        "utcOffsetMinutes": -180,
        "utc": "2031-07-01T07:37:52Z",
        "marks": "recorded",
        "source": {"container": "exif", "tags": ["DateTimeOriginal", *GPS_TAGS],
                   "basis": {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 13}},
    }
    assert type(hint["source"]["basis"]["uncertaintySec"]) is int


@pytest.mark.parametrize("hours", [1, 2, -1])
def test_a_fix_a_whole_number_of_hours_old_is_refused_not_taken_for_the_next_zone(hours: int) -> None:
    hint = capture(LOCAL, OCEAN, utc_of(LOCAL, -180) - timedelta(hours=hours))
    assert hint == zoneless({"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}, ["DateTimeOriginal", *GPS_TAGS])


def test_a_camera_clock_left_on_another_zone_is_refused() -> None:
    # The camera shows UTC+02:00 wall time, the photo was taken at sea in UTC-03:00: the GPS time agrees with
    # the camera's own zone, not with the place's
    hint = capture(LOCAL, OCEAN, utc_of(LOCAL, 120))
    assert hint["utcOffsetMinutes"] is None and hint["source"]["basis"] == {"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}


@pytest.mark.parametrize(
    ("place", "local", "minutes"),
    [(HANLE, LOCAL, 330), (KATHMANDU, LOCAL, 345), (WAITANGI, LOCAL, 765), (OCEAN_PACIFIC, LOCAL, -540)],
)
def test_half_and_three_quarter_hour_zones_are_found(place: tuple[float, float], local: datetime, minutes: int) -> None:
    hint = capture(local, place, utc_of(local, minutes) - timedelta(seconds=7))
    assert hint["utcOffsetMinutes"] == minutes
    assert hint["source"]["basis"] == {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 8}


def test_a_capture_across_the_date_line_takes_the_offset_whose_day_it_is() -> None:
    # Kiritimati is 14 hours ahead: just after its local midnight it is still the day before in UTC
    after_midnight = datetime(2031, 7, 2, 0, 30, 5)
    hint = capture(after_midnight, KIRITIMATI, datetime(2031, 7, 1, 10, 30, 0, tzinfo=timezone.utc))
    assert (hint["utcOffsetMinutes"], hint["utc"]) == (840, "2031-07-01T10:30:05Z")
    # Pago Pago is 11 hours behind: its local afternoon is already the next day in UTC
    afternoon = datetime(2031, 7, 1, 13, 30, 5)
    hint = capture(afternoon, PAGO_PAGO, datetime(2031, 7, 2, 0, 30, 0, tzinfo=timezone.utc))
    assert (hint["utcOffsetMinutes"], hint["utc"]) == (-660, "2031-07-02T00:30:05Z")


MORE_THAN_ONE = {"placeTimeZone": "more_than_one_offset", "gpsTimeStamp": "unchecked"}


def test_a_wall_time_a_clock_change_repeats_derives_nothing_even_from_a_fresh_fix() -> None:
    """Ruling 1 of the third version: the GPS time stamp does not pick between two readings, since a fix an hour old
    would pick the other one. Each fix here is 10 s old by one reading."""
    repeated = datetime(2031, 10, 26, 1, 30, 0)
    for fix in (datetime(2031, 10, 26, 0, 29, 50, tzinfo=timezone.utc), datetime(2031, 10, 26, 1, 29, 50, tzinfo=timezone.utc)):
        hint = capture(repeated, GREENWICH, fix)
        assert hint == zoneless(MORE_THAN_ONE, ["DateTimeOriginal", *GPS_TAGS]) | {"localDateTime": "2031-10-26T01:30:00"}


def test_a_leftover_up_to_the_bound_is_accepted_and_one_second_more_is_not() -> None:
    utc = utc_of(LOCAL, -180)
    for leftover in (R, -R):
        at_bound = capture(LOCAL, OCEAN, utc - timedelta(seconds=leftover))
        assert at_bound["utcOffsetMinutes"] == -180 and at_bound["source"]["basis"]["uncertaintySec"] == R + 1
    for leftover in (R + 1, -(R + 1), R + 0.01, -(R + 0.01)):
        hint = capture(LOCAL, OCEAN, utc - timedelta(seconds=leftover))
        assert hint["utcOffsetMinutes"] is None and hint["source"]["basis"] == {"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}


def test_the_gps_instant_itself_is_never_the_capture_time() -> None:
    fix = utc_of(LOCAL, -180) - timedelta(seconds=150)
    assert capture(LOCAL, OCEAN, fix)["utc"] == "2031-07-01T07:37:52Z" != fix.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize(
    ("leftover_sec", "stated"),
    [(0, 1), (0.01, 2), (0.3, 2), (-0.3, 2), (1, 2), (12, 13), (-12, 13), (13.06, 15), (13.99, 15), (14, 15), (299.99, 301)],
)
def test_the_uncertainty_is_a_second_plus_the_leftover_rounded_up_to_the_whole_second(leftover_sec: float, stated: int) -> None:
    """Ruling 2 of the third version: a tagged photo's EXIF time counts whole seconds, a derived instant never states
    less, and the leftover is added to it, rounded up so that it never states less than the leftover either."""
    utc = utc_of(LOCAL, -180)
    basis = capture(LOCAL, OCEAN, utc - timedelta(seconds=leftover_sec))["source"]["basis"]
    assert basis["uncertaintySec"] == stated and type(basis["uncertaintySec"]) is int


def test_a_slow_camera_clock_an_old_fix_hides_states_what_a_tagged_photo_states_and_claims_no_confirmed_instant() -> None:
    """Audit E's bound case: a camera clock 290 s slow and a fix 290 s old leave nothing over. The GPS time stamp bounds
    only the difference of the two, so the derived instant states the second a tagged photo's time counts, the same as
    the same photo with its offset tag, and the sky's sentence says the zone agrees, not that the instant is confirmed."""
    from services.science.predicted_sky import time_zone_from_gps

    shutter = utc_of(LOCAL, -180)
    camera_local = LOCAL - timedelta(seconds=290)
    fix = shutter - timedelta(seconds=290)
    derived = capture(camera_local, OCEAN, fix)
    tagged = capture(camera_local, OCEAN, fix, tag="-03:00")
    assert derived["utcOffsetMinutes"] == tagged["utcOffsetMinutes"] == -180
    assert derived["source"]["basis"]["uncertaintySec"] == tagged["source"]["basis"]["uncertaintySec"] == 1
    sentence = time_zone_from_gps(derived["source"]["basis"]["uncertaintySec"])
    assert "confirmed" not in sentence and "which agrees with its GPS time stamp" in sentence
    assert "an old GPS fix can hide a wrong camera clock" in sentence


def test_the_uncertainty_counts_a_gps_time_stamps_fraction_of_a_second() -> None:
    # A GPS time stamp with tenths of a millisecond: the leftover of 13.1234 s is stated as 15 s, a second plus it rounded up
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = local_tag(LOCAL)
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    for name, value in position(OCEAN).items():
        gps[getattr(ExifTags.GPS, name)] = value
    gps[ExifTags.GPS.GPSDateStamp] = "2031:07:01"
    gps[ExifTags.GPS.GPSTimeStamp] = (Rat(7, 1), Rat(37, 1), Rat(388766, 10000))
    assert exif_hints(exif)["captureTime"]["source"]["basis"]["uncertaintySec"] == 15


# Leftovers in tenths of a millisecond, each a whole second's neighbours on either side (audit K's case: 12.0004 s
# states 14 s; rounding it to the millisecond first would state 13). Each, in ten-thousandths of a second, and what
# is stated: a second plus the leftover rounded up
SUBMILLI_EDGES = [(0, 1), (4, 2), (119996, 13), (120000, 13), (120004, 14), (120010, 14), (100004, 12), (-120004, 14)]


@pytest.mark.parametrize(("leftover", "stated"), SUBMILLI_EDGES)
def test_the_leftover_is_rounded_up_from_its_tenth_of_a_millisecond(leftover: int, stated: int) -> None:
    # The local time is LOCAL at sea in UTC-03:00, so the fix is 07:37:52 UTC less the leftover
    minutes, rest = divmod((7 * 3600 + 37 * 60 + 52) * 10000 - leftover, 60 * 10000)
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = local_tag(LOCAL)
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    for name, value in position(OCEAN).items():
        gps[getattr(ExifTags.GPS, name)] = value
    gps[ExifTags.GPS.GPSDateStamp] = "2031:07:01"
    gps[ExifTags.GPS.GPSTimeStamp] = (Rat(minutes // 60, 1), Rat(minutes % 60, 1), Rat(rest, 10000))
    basis = exif_hints(exif)["captureTime"]["source"]["basis"]
    assert (basis["offsetSource"], basis["uncertaintySec"]) == (ZONED, stated)


def test_no_label_says_more_than_that_the_sources_agree() -> None:
    """Ruling 2 of the fourth version: the GPS time stamp only shows that the place's time zone agrees with it (a camera
    clock half an hour off with a fix half an hour old agrees and is half an hour wrong), so no label says it confirms,
    verifies or makes exact anything."""
    from services.contracts.ingest import CAPTURE_OFFSET_SOURCES, GPS_TIME_STAMP_CHECKS, PLACE_TIME_ZONE_CHECKS

    assert CAPTURE_OFFSET_SOURCES == ("offset_tag", "place_time_zone_agrees_with_gps_time_stamp")
    for label in (*CAPTURE_OFFSET_SOURCES, *PLACE_TIME_ZONE_CHECKS, *GPS_TIME_STAMP_CHECKS):
        assert not re.search(r"confirm|verif|exact|prove|certain", label), label


def test_either_source_alone_gives_no_offset_and_says_which_is_missing() -> None:
    assert capture(LOCAL, OCEAN, None) == zoneless({"placeTimeZone": "unchecked", "gpsTimeStamp": "no_usable_time_stamp"}, ["DateTimeOriginal"])
    assert capture(LOCAL, None, utc_of(LOCAL, -180)) == zoneless(
        {"placeTimeZone": "no_usable_position", "gpsTimeStamp": "unchecked"}, ["DateTimeOriginal", *GPS_TAGS])
    assert capture(LOCAL, None, None) == zoneless(None, ["DateTimeOriginal"])


def test_a_position_outside_every_zone_gives_no_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "place_time_zones", lambda lat, lon: ())
    assert capture(LOCAL, OCEAN, utc_of(LOCAL, -180))["source"]["basis"] == {
        "placeTimeZone": "outside_every_time_zone", "gpsTimeStamp": "unchecked"}
    assert capture(LOCAL, OCEAN, None)["source"]["basis"] == {"placeTimeZone": "outside_every_time_zone", "gpsTimeStamp": "no_usable_time_stamp"}


FIX = utc_of(LOCAL, -180) - timedelta(seconds=12)
BAD_STAMPS = [
    pytest.param({"GPSDateStamp": None}, id="a time stamp with no date stamp"),
    pytest.param({"GPSTimeStamp": None}, id="a date stamp with no time stamp"),
    pytest.param({"GPSDateStamp": "2031-07-01"}, id="a date stamp written with dashes"),
    pytest.param({"GPSDateStamp": "2031:07:01:00"}, id="a date stamp with more after the day"),
    pytest.param({"GPSDateStamp": "2031:02:30"}, id="no such day"),
    pytest.param({"GPSDateStamp": "0000:07:01"}, id="year zero"),
    pytest.param({"GPSTimeStamp": (Rat(24, 1), Rat(0, 1), Rat(0, 1))}, id="hour 24"),
    pytest.param({"GPSTimeStamp": (Rat(7, 1), Rat(60, 1), Rat(0, 1))}, id="minute 60"),
    pytest.param({"GPSTimeStamp": (Rat(7, 1), Rat(37, 1), Rat(60, 1))}, id="second 60"),
    pytest.param({"GPSTimeStamp": (Rat(7, 1), Rat(37, 1))}, id="two parts"),
    pytest.param({"GPSTimeStamp": (Rat(7, 1), Rat(37, 1), Rat(1, 0))}, id="a zero denominator"),
    pytest.param({"GPSTimeStamp": (Rat(7, 1), Rat(37, 1), Rat(-1, 1))}, id="a negative second"),
    pytest.param({"GPSTimeStamp": "07:37:40"}, id="a time stamp written as text"),
]


@pytest.mark.parametrize("change", BAD_STAMPS)
def test_a_bad_gps_stamp_is_not_used(change: dict[str, Any]) -> None:
    gps = {**position(OCEAN), **gps_stamps(FIX)}
    for name, value in change.items():
        if value is None:
            gps.pop(name)
        else:
            gps[name] = value
    # Built in memory: a JPEG cannot carry a negative or a text value in this rational tag, but a reader must not
    # trust that every file obeys the tag's type
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = local_tag(LOCAL)
    for name, value in gps.items():
        exif.get_ifd(ExifTags.IFD.GPSInfo)[getattr(ExifTags.GPS, name)] = value
    hint = exif_hints(exif)["captureTime"]
    assert hint == zoneless({"placeTimeZone": "unchecked", "gpsTimeStamp": "no_usable_time_stamp"}, ["DateTimeOriginal"])


# Each bound of the GPS stamps the reader reads, from one table: (date stamp, hour, minute, second as a rational's
# numerator and denominator, usable). Each inside end, and one step outside it
STAMP_EDGES = [
    ("2031:07:01", 0, 0, (0, 1), True), ("2031:07:01", 23, 59, (5999, 100), True),
    ("2031:07:01", 24, 0, (0, 1), False), ("2031:07:01", -1, 0, (0, 1), False),
    ("2031:07:01", 7, 60, (0, 1), False), ("2031:07:01", 7, -1, (0, 1), False),
    ("2031:07:01", 7, 37, (6000, 100), False), ("2031:07:01", 7, 37, (-1, 100), False),
    ("2031:01:01", 7, 37, (40, 1), True), ("2031:12:31", 7, 37, (40, 1), True),
    ("2031:00:10", 7, 37, (40, 1), False), ("2031:13:10", 7, 37, (40, 1), False),
    ("2031:07:00", 7, 37, (40, 1), False), ("2031:07:32", 7, 37, (40, 1), False),
    # A fix on the calendar's first or last day is not used: the instant it gives may lie past the calendar's end
    ("0001:01:02", 12, 0, (0, 1), True), ("9999:12:30", 23, 59, (59, 1), True),
    ("0001:01:01", 12, 0, (0, 1), False), ("9999:12:31", 12, 0, (0, 1), False),
    ("9999:12:31", 23, 59, (59, 1), False),  # the second version raised OverflowError here
]


@pytest.mark.parametrize(("date_stamp", "hour", "minute", "second", "usable"), STAMP_EDGES)
def test_each_bound_of_the_gps_stamps_at_its_inside_end_and_one_step_out(
        date_stamp: str, hour: int, minute: int, second: tuple[int, int], usable: bool) -> None:
    # At sea in UTC-03:00, the camera's clock 5 s ahead of the fix by the zone; built in memory, as a reader must not
    # trust that every file obeys the tags' types. An unusable stamp leaves the position unchecked; its local time is
    # the one a usable stamp would agree with, or the module's own when the stamp names no real day
    year, month, day = (int(part) for part in date_stamp.split(":"))
    try:
        fix = datetime(year, month, day, tzinfo=timezone.utc) + timedelta(hours=hour, minutes=minute, seconds=second[0] / second[1])
    except ValueError:
        fix = FIX
    local = (fix - timedelta(hours=3) + timedelta(seconds=5)).replace(tzinfo=None, microsecond=0)
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = local_tag(local)
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    for name, value in position(OCEAN).items():
        gps[getattr(ExifTags.GPS, name)] = value
    gps[ExifTags.GPS.GPSDateStamp] = date_stamp
    gps[ExifTags.GPS.GPSTimeStamp] = (Rat(hour, 1), Rat(minute, 1), Rat(*second))
    hint = exif_hints(exif)["captureTime"]
    CaptureTimeHint.model_validate(hint)
    if usable:
        assert hint["utcOffsetMinutes"] == -180
        assert hint["source"]["basis"]["uncertaintySec"] == 1 + math.ceil(abs((local.replace(tzinfo=timezone.utc) - fix)
                                                                            - timedelta(hours=-3)) / timedelta(seconds=1))
    else:
        assert hint["utcOffsetMinutes"] is None
        assert hint["source"]["basis"] == {"placeTimeZone": "unchecked", "gpsTimeStamp": "no_usable_time_stamp"}


def test_a_fix_the_receiver_marked_void_gives_neither_source() -> None:
    exif = Image.open(io.BytesIO(jpeg({"DateTimeOriginal": local_tag(LOCAL)},
                                      {**position(OCEAN), **gps_stamps(FIX), "GPSStatus": "V"}))).getexif()
    assert exif_hints(exif)["captureTime"] == zoneless(None, ["DateTimeOriginal"])


def test_gps_stamps_without_a_local_time_give_no_capture_time() -> None:
    exif = Image.open(io.BytesIO(jpeg({}, {**position(OCEAN), **gps_stamps(FIX)}))).getexif()
    assert "captureTime" not in exif_hints(exif)


# ---- An offset tag always wins ----


def test_an_offset_tag_that_both_sources_agree_with_is_labelled_so() -> None:
    hint = capture(LOCAL, OCEAN, FIX, tag="-03:00")
    assert (hint["utcOffsetMinutes"], hint["utc"]) == (-180, "2031-07-01T07:37:52Z")
    assert hint["source"] == {
        "container": "exif",
        "tags": ["DateTimeOriginal", "OffsetTimeOriginal", *GPS_TAGS],
        "basis": {"offsetSource": "offset_tag", "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 13},
    }


@pytest.mark.parametrize(("tag", "place_label"), [("+05:30", "agrees"), ("+05:45", "agrees"), ("+05:00", "disagrees")])
def test_an_offset_tag_at_a_place_that_offers_two_offsets_is_labelled_against_both(tag: str, place_label: str) -> None:
    # Two zones a quarter of an hour apart: a tag that is either one agrees with the place, and the tag wins either way
    minutes = int(tag[1:3]) * 60 + int(tag[4:6])
    hint = capture(AUDIT_JULY, OVERLAP_15, utc_of(AUDIT_JULY, minutes) - timedelta(seconds=20), tag=tag)
    assert hint["utcOffsetMinutes"] == minutes
    assert hint["source"]["basis"] == {"offsetSource": "offset_tag", "placeTimeZone": place_label, "gpsTimeStamp": "agrees",
                                       "uncertaintySec": 21}


@pytest.mark.parametrize(
    ("place", "fix", "labels"),
    [
        (OCEAN, FIX - timedelta(hours=1), ("agrees", "disagrees")),
        (HANLE, FIX, ("disagrees", "agrees")),
        (HANLE, FIX - timedelta(hours=1), ("disagrees", "disagrees")),
        (None, FIX - timedelta(hours=1), ("no_usable_position", "disagrees")),
        (HANLE, None, ("disagrees", "no_usable_time_stamp")),
    ],
)
def test_an_offset_tag_a_source_disagrees_with_stays_and_is_labelled(place: Any, fix: Any, labels: tuple[str, str]) -> None:
    hint = capture(LOCAL, place, fix, tag="-03:00")
    assert (hint["utcOffsetMinutes"], hint["utc"]) == (-180, "2031-07-01T07:37:52Z")
    basis = hint["source"]["basis"]
    assert (basis["offsetSource"], basis["placeTimeZone"], basis["gpsTimeStamp"]) == ("offset_tag", *labels)


def test_an_offset_tag_without_any_gps_source_is_as_it_was() -> None:
    hint = capture(LOCAL, None, None, tag="-03:00")
    assert hint["source"] == {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}


# ---- Audit E's 62 built cases of the second version, as its probe_wrong.py built them ----
# `true` is the offset of the camera's own clock in minutes, so the right capture instant is local minus `true`; `age`
# is how much older the GPS fix is than the shutter, in minutes. The second version derived the wrong offset in 9 of
# them; now a case derives `true`, with its stated uncertainty in seconds, or nothing with its labels.

DISAGREES = {"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}


def _audit_cases() -> list:
    cases = []

    def add(label: str, local: datetime, place: Any, true: int, age: float, expected: Any) -> None:
        cases.append(pytest.param(local, place, true, age, expected, id=f"{label}, true {true}, fix {age:g} min old"))

    for age in (0, 4.9, 15, 30, 45, 60, 90, 24 * 60):
        # A fix 4.9 min old leaves 294 s over: a second plus it is stated
        expected = 1 if age == 0 else 295 if age == 4.9 else DISAGREES
        add("one zone, 5:30 inland", AUDIT_JULY, SINGLE_530, 330, age, expected)
        add("open ocean", AUDIT_JULY, OCEAN, -180, age, expected)
    for age in (0, 60, 120, -60, -120):
        for true in (360, 480):
            add("two zones 2 h apart", AUDIT_JULY, OVERLAP_2H, true, age, MORE_THAN_ONE)
    for age in (0, 60, -60):
        for true in (180, 240):
            add("two zones 1 h apart", AUDIT_JULY, OVERLAP_1H, true, age, MORE_THAN_ONE)
    for age in (0, 15, -15, 30):
        for true in (330, 345):
            add("two zones 15 min apart", AUDIT_JULY, OVERLAP_15, true, age, MORE_THAN_ONE)
    for age in (0, 60, -60):
        for true in (60, 120):
            add("a repeated hour, inland", REPEATED, INLAND_DST, true, age, MORE_THAN_ONE)
        add("a skipped hour, the camera clock not moved on", SKIPPED, INLAND_DST, 60, age, MORE_THAN_ONE)
    add("a camera clock on another zone", AUDIT_JULY, SINGLE_530, 120, 0, DISAGREES)
    for place, label, true in ((DATE_LINE_W, "just west of the date line", 720), (DATE_LINE, "on the date line", 720),
                               (POLE_N, "the north pole", 0), (POLE_S, "the south pole", 720)):
        for age in (0, 24 * 60, -24 * 60):
            add(label, AUDIT_JULY, place, true, age, 1 if age == 0 else DISAGREES)
    return cases


AUDIT_CASES = _audit_cases()


def test_the_audit_cases_are_the_62_audit_e_built() -> None:
    assert len(AUDIT_CASES) == 62


@pytest.mark.parametrize(("local", "place", "true", "age", "expected"), AUDIT_CASES)
def test_audit_e_case_derives_the_true_offset_or_nothing(local: datetime, place: Any, true: int, age: float, expected: Any) -> None:
    shutter = utc_of(local, true)
    hint = capture(local, place, shutter - timedelta(minutes=age))
    if isinstance(expected, int):
        assert (hint["utcOffsetMinutes"], hint["utc"]) == (true, shutter.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert hint["source"]["basis"] == {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees",
                                           "uncertaintySec": expected}
    else:
        assert (hint["utcOffsetMinutes"], hint["utc"]) == (None, None)
        assert hint["source"]["basis"] == expected


@pytest.mark.parametrize("place", [OVERLAP_2H, OVERLAP_1H, OVERLAP_15])
def test_a_place_that_offers_two_offsets_and_no_gps_time_stamp_says_so(place: Any) -> None:
    hint = capture(AUDIT_JULY, place, None)
    assert hint["utcOffsetMinutes"] is None
    assert hint["source"]["basis"] == {"placeTimeZone": "more_than_one_offset", "gpsTimeStamp": "no_usable_time_stamp"}


# ---- Properties ----

# Single-zone places, then the made-up border points, whose two zones give two offsets or, at some times since 1980,
# the same one (Asia/Kathmandu kept UTC+05:30 until 1986), and an inland zone with daylight saving
PLACES = [OCEAN, OCEAN_PACIFIC, HANLE, KATHMANDU, WAITANGI, KIRITIMATI, PAGO_PAGO, GREENWICH, OVERLAP_2H, OVERLAP_1H,
          OVERLAP_15, INLAND_DST]
MOMENTS = st.datetimes(min_value=datetime(1980, 1, 2), max_value=datetime(2049, 12, 30))


def true_offsets(place: tuple[float, float], local: datetime) -> list[int]:
    """Every offset, in minutes, the zones at the point give the wall time, both readings, read from zoneinfo here."""
    return sorted({int(ZoneInfo(zone).utcoffset(local.replace(fold=f)).total_seconds()) // 60
                   for zone in place_time_zones(*place) for f in (0, 1)})


def stated(leftover: timedelta) -> int:
    """A second, the whole second a tagged photo's EXIF time counts, plus the leftover rounded up to the whole second."""
    return 1 + math.ceil(abs(leftover) / timedelta(seconds=1))


@settings(max_examples=120, deadline=None)
@given(place=st.sampled_from(PLACES), local=MOMENTS, leftover=st.integers(-R, R), fold=st.integers(0, 1))
@example(place=OCEAN, local=LOCAL, leftover=R, fold=0)
@example(place=OCEAN, local=LOCAL, leftover=-R, fold=0)
@example(place=GREENWICH, local=datetime(2031, 10, 26, 1, 30), leftover=0, fold=1)
@example(place=OVERLAP_2H, local=AUDIT_JULY, leftover=0, fold=0)
@example(place=OVERLAP_15, local=datetime(1985, 7, 1, 12, 0), leftover=-7, fold=0)
def test_property_a_real_offset_within_the_bound_is_found_only_where_the_place_offers_it_alone(
        place: Any, local: datetime, leftover: int, fold: int) -> None:
    local = local.replace(microsecond=0)
    offsets = true_offsets(place, local)
    true = offsets[fold % len(offsets)]
    hint = capture(local, place, utc_of(local, true) - timedelta(seconds=leftover))
    if len(offsets) == 1:
        assert hint["utcOffsetMinutes"] == true
        assert hint["source"]["basis"] == {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees",
                                           "uncertaintySec": 1 + abs(leftover)}
    else:
        assert hint["utcOffsetMinutes"] is None and hint["source"]["basis"] == MORE_THAN_ONE


@settings(max_examples=120, deadline=None)
@given(place=st.sampled_from(PLACES), local=MOMENTS, shift=st.integers(-14 * 3600, 14 * 3600))
@example(place=OCEAN, local=LOCAL, shift=R + 1)
@example(place=OCEAN, local=LOCAL, shift=3600)
@example(place=HANLE, local=LOCAL, shift=-1800)
@example(place=OVERLAP_2H, local=AUDIT_JULY, shift=-7200)
@example(place=OVERLAP_1H, local=AUDIT_JULY, shift=3600)
@example(place=INLAND_DST, local=REPEATED, shift=3600)
def test_property_whatever_the_fix_s_age_only_a_place_s_one_offset_is_ever_derived(place: Any, local: datetime, shift: int) -> None:
    """Every draw is judged, none skipped: a place that offers more than one offset derives nothing, and a place that
    offers one derives it exactly when the leftover is within the bound, with its stated uncertainty."""
    local = local.replace(microsecond=0)
    offsets = true_offsets(place, local)
    fix = utc_of(local, offsets[0]) - timedelta(seconds=shift)
    hint = capture(local, place, fix)
    leftover = local.replace(tzinfo=timezone.utc) - fix - timedelta(minutes=offsets[0])
    if len(offsets) > 1:
        assert hint["utcOffsetMinutes"] is None and hint["source"]["basis"] == MORE_THAN_ONE
    elif abs(leftover) <= timedelta(seconds=R):
        assert hint["utcOffsetMinutes"] == offsets[0] and hint["source"]["basis"]["uncertaintySec"] == stated(leftover)
    else:
        assert hint["utcOffsetMinutes"] is None
        assert hint["source"]["basis"] == {"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}


@settings(max_examples=80, deadline=None)
@given(place=st.sampled_from(PLACES[:8]), local=MOMENTS, centiseconds=st.integers(-100 * R, 100 * R))
@example(place=OCEAN, local=LOCAL, centiseconds=0)
@example(place=OCEAN, local=LOCAL, centiseconds=1)
@example(place=OCEAN, local=LOCAL, centiseconds=-100 * R)
def test_property_a_derived_instant_states_no_less_uncertainty_than_the_same_photo_tagged(
        place: Any, local: datetime, centiseconds: int) -> None:
    local = local.replace(microsecond=0)
    offsets = true_offsets(place, local)
    if len(offsets) > 1:  # a wall time a clock change repeats or skips, which the property before covers
        return
    (true,) = offsets
    sign = "-" if true < 0 else "+"
    tag = f"{sign}{abs(true) // 60:02d}:{abs(true) % 60:02d}"
    leftover = timedelta(milliseconds=10 * centiseconds)
    fix = utc_of(local, true) - leftover
    derived = capture(local, place, fix)["source"]["basis"]["uncertaintySec"]
    tagged = capture(local, place, fix, tag=tag)["source"]["basis"]["uncertaintySec"]
    assert derived == tagged == stated(leftover)
    assert abs(leftover) / timedelta(seconds=1) + 1 <= derived < abs(leftover) / timedelta(seconds=1) + 2


@settings(max_examples=60, deadline=None)
@given(
    tag_minutes=st.integers(-12 * 4, 14 * 4).map(lambda q: q * 15),
    place=st.sampled_from([None, *PLACES]),
    shift=st.one_of(st.none(), st.integers(-20 * 3600, 20 * 3600)),
)
@example(tag_minutes=-180, place=OCEAN, shift=0)
@example(tag_minutes=120, place=OCEAN, shift=-3600)
@example(tag_minutes=345, place=OVERLAP_15, shift=-R)
def test_property_an_offset_tag_is_never_changed(tag_minutes: int, place: Any, shift: int | None) -> None:
    sign = "-" if tag_minutes < 0 else "+"
    tag = f"{sign}{abs(tag_minutes) // 60:02d}:{abs(tag_minutes) % 60:02d}"
    true_utc = utc_of(LOCAL, tag_minutes)
    hint = capture(LOCAL, place, None if shift is None else true_utc + timedelta(seconds=shift), tag=tag)
    assert hint["utcOffsetMinutes"] == tag_minutes
    assert hint["utc"] == true_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    basis = hint["source"].get("basis")
    if place is None and shift is None:
        assert basis is None
        return
    assert basis["offsetSource"] == "offset_tag"
    assert basis["gpsTimeStamp"] == ("no_usable_time_stamp" if shift is None else "agrees" if abs(shift) <= R else "disagrees")
    assert basis.get("uncertaintySec") == (1 + abs(shift) if basis["gpsTimeStamp"] == "agrees" else None)
    if place is not None:
        assert basis["placeTimeZone"] == ("agrees" if tag_minutes in true_offsets(place, LOCAL) else "disagrees")


# ---- Through the upload path ----


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALBEDIAN_INGEST_DIR", str(tmp_path / "ingest"))
    from services.api import create_app

    return TestClient(create_app())


def test_an_upload_whose_place_and_gps_time_agree_gets_a_capture_time_that_says_so(client: TestClient) -> None:
    body = jpeg({"DateTimeOriginal": local_tag(LOCAL)}, {**position(OCEAN), **gps_stamps(FIX)})
    response = client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"})
    assert response.status_code == 201, response.text
    receipt = response.json()
    IngestReceipt.model_validate(receipt)
    asset = receipt["sourceAsset"]
    assert asset["capturedAtUTC"] == "2031-07-01T07:37:52Z"
    assert asset["metadataSources"]["capturedAtUTC"]["basis"]["offsetSource"] == ZONED


def test_an_upload_whose_place_and_gps_time_disagree_has_no_capture_time_and_its_record_says_why(client: TestClient) -> None:
    body = jpeg({"DateTimeOriginal": local_tag(LOCAL)}, {**position(OCEAN), **gps_stamps(FIX - timedelta(hours=1))})
    receipt = client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"}).json()
    assert "capturedAtUTC" not in receipt["sourceAsset"]
    assert receipt["hints"]["captureTime"]["source"]["basis"] == {"placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"}


def test_an_upload_with_an_offset_tag_and_no_gps_source_is_unchanged(client: TestClient) -> None:
    body = jpeg({"DateTimeOriginal": local_tag(LOCAL), "OffsetTimeOriginal": "-03:00"})
    asset = client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"}).json()["sourceAsset"]
    assert asset["metadataSources"]["capturedAtUTC"] == {"container": "exif", "tags": ["DateTimeOriginal", "OffsetTimeOriginal"]}


def test_the_corpus_fact_counts_only_an_offset_the_file_holds(tmp_path: Path) -> None:
    from research.benchmarks.real_photos import corpus as rp

    worked_out = tmp_path / "worked-out.jpg"
    worked_out.write_bytes(jpeg({"DateTimeOriginal": local_tag(LOCAL)}, {**position(OCEAN), **gps_stamps(FIX)}))
    tagged = tmp_path / "tagged.jpg"
    tagged.write_bytes(jpeg({"DateTimeOriginal": local_tag(LOCAL), "OffsetTimeOriginal": "-03:00"}, {**position(OCEAN), **gps_stamps(FIX)}))
    assert exif_hints(Image.open(worked_out).getexif())["captureTime"]["utcOffsetMinutes"] == -180
    assert (rp.read_facts(worked_out)["hasCaptureTime"], rp.read_facts(worked_out)["hasUtcOffset"]) == (True, False)
    assert (rp.read_facts(tagged)["hasCaptureTime"], rp.read_facts(tagged)["hasUtcOffset"]) == (True, True)


# ---- The predicted sky says the time zone was worked out, and widens its uncertainty by the time's ----

SKY_MOMENT = "2030-01-01T02:00:00Z"  # the synthetic moment and open-ocean place the plate-solver fixtures use
SKY_PLACE = {"latDeg": 30.0, "lonDeg": -40.0}
ZONED_BASIS = {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 13}
WIDEST = R + 1  # the largest uncertainty a derived instant states: a second plus a leftover at the bound


def _unresolved():
    from services.science.catalog import get_catalog_stars
    from services.science.solver import _build_unresolved_solution, triangle_solver_identity

    return _build_unresolved_solution(
        [], "sol_synthetic", "src_synthetic", "0" * 64, 0.0, SKY_MOMENT, 30.0, -40.0,
        "Triangle pattern matching found no consistent WCS solution.", "Pixel position only.",
        triangle_solver_identity(get_catalog_stars()),
    )


def _sky_asset(basis: dict | None) -> dict:
    source: dict[str, Any] = {"container": "exif", "tags": ["DateTimeOriginal", *GPS_TAGS]}
    if basis is not None:
        source["basis"] = basis
    return {"id": "src_synthetic", "sha256": "0" * 64, "capturedAtUTC": SKY_MOMENT, "observerLocation": SKY_PLACE,
            "metadataSources": {"capturedAtUTC": source}}


def test_a_worked_out_time_zone_is_said_in_the_sky_and_widens_every_predicted_uncertainty() -> None:
    from services.science.predicted_sky import SKY_TURN_ARCSEC_PER_SEC, complete_sky, time_zone_from_gps

    recorded = complete_sky(_unresolved(), _sky_asset(None))
    worked_out = complete_sky(_unresolved(), _sky_asset(ZONED_BASIS))
    assert SKY_TURN_ARCSEC_PER_SEC == pytest.approx(15.0411, abs=1e-4)
    sentence = time_zone_from_gps(13)
    # The zone agrees with the GPS time stamp; the instant is not said to be confirmed, since an old fix can hide a
    # wrong camera clock (ruling 2 of the third version)
    assert sentence == (
        "The file names no time zone for its capture time: it was taken from the time zone at the photo's GPS position, "
        "which agrees with its GPS time stamp. The capture time is taken to be within 13 s, a second plus how far the "
        "camera's clock and the GPS time stamp differ, which can move the predicted sky by up to 196 arcsec; an old GPS "
        "fix can hide a wrong camera clock."
    )
    assert worked_out.warnings == [*recorded.warnings[:-1], sentence, recorded.warnings[-1]]
    assert len(worked_out.findings) == len(recorded.findings) > 0
    for before, after in zip(recorded.findings, worked_out.findings):
        assert after.uncertainty_arcsec == pytest.approx(before.uncertainty_arcsec + 13 * SKY_TURN_ARCSEC_PER_SEC)
        assert "time zone taken from the GPS position and agreeing with the GPS time stamp" in after.limitations[0]
        assert "confirmed" not in after.limitations[0]
        assert "time zone" not in before.limitations[0] and "the recorded capture time and place" in before.limitations[0]


def test_the_sky_turns_one_degree_in_a_360th_of_a_sidereal_day() -> None:
    # The pose bound's share of the time, in degrees: exact, since whole seconds rarely land near a degree's edge
    from services.science.predicted_sky import sky_turn_deg

    assert sky_turn_deg(86164.0905 / 360) == pytest.approx(1.0, rel=1e-12)
    assert sky_turn_deg(WIDEST) == pytest.approx(WIDEST * 360 / 86164.0905, rel=1e-12)
    assert sky_turn_deg(0) == 0.0


@pytest.mark.parametrize("basis", [
    None,
    {"offsetSource": "offset_tag", "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 6},
    {"offsetSource": "offset_tag", "placeTimeZone": "disagrees", "gpsTimeStamp": "disagrees"},
])
def test_an_offset_from_the_file_leaves_the_sky_as_it_was(basis: dict | None) -> None:
    from services.science.predicted_sky import complete_sky

    plain = {key: value for key, value in _sky_asset(None).items() if key != "metadataSources"}
    assert complete_sky(_unresolved(), _sky_asset(basis)) == complete_sky(_unresolved(), plain)


def test_a_lined_up_pose_counts_the_time_in_its_bound_and_says_where_the_zone_came_from() -> None:
    from services.science.predicted_sky import SKY_TURN_ARCSEC_PER_SEC, complete_sky, time_zone_from_gps
    from tests.science.test_pose_estimate import _asset as head_worn_asset
    from tests.science.test_pose_estimate import _soft_moon, _solver_solution

    luma = _soft_moon(500.3, 700.6)
    plain = complete_sky(_solver_solution(), head_worn_asset(), frames=lambda: [(0.0, luma)])
    asset = head_worn_asset()
    asset["metadataSources"] = {"capturedAtUTC": {"container": "exif", "tags": ["DateTimeOriginal", *GPS_TAGS],
                                                  "basis": {**ZONED_BASIS, "uncertaintySec": WIDEST}}}
    out = complete_sky(_solver_solution(), asset, frames=lambda: [(0.0, luma)])
    assert plain.status == out.status == "approximate"
    estimate = out.pose_estimate
    time_deg = WIDEST * SKY_TURN_ARCSEC_PER_SEC / 3600
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg + time_deg)
    assert within > math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg)
    assert f"within about {within} degrees near the bright spot" in out.warnings[-3]
    assert out.warnings[-2] == time_zone_from_gps(WIDEST) and out.warnings[-1] == plain.warnings[-1]
    predicted = [f for f in out.findings if f.evidence_class == "predicted"]
    before = {f.id: f.uncertainty_arcsec for f in plain.findings if f.evidence_class == "predicted"}
    assert predicted and all(f.uncertainty_arcsec == pytest.approx(before[f.id] + WIDEST * SKY_TURN_ARCSEC_PER_SEC) for f in predicted)


# ---- The real-photo corpus, where its photos are in the shared cache: counts only ----

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "research" / "benchmarks" / "real_photos" / "corpus.json").read_text(encoding="utf-8"))
CORPUS_PATHS = [photo["downloadPath"] for photo in MANIFEST["photos"]]


def _exif_of(path: Path) -> Image.Exif | None:
    if path.suffix.lower() == ".heic":
        info = metadata.read_heif(path.read_bytes())
        if info.exif is None:
            return None
        exif = Image.Exif()
        exif.load(b"Exif\x00\x00" + info.exif)
        return exif
    with Image.open(path) as image:
        return image.getexif()


@pytest.mark.local_dataset(*CORPUS_PATHS)
def test_on_the_corpus_no_photo_is_given_an_offset_other_than_its_tag() -> None:
    labels: dict[str, int] = {}
    without_tag: dict[str, int] = {}
    for relative in CORPUS_PATHS:
        exif = _exif_of(ROOT / relative)
        hint = exif_hints(exif).get("captureTime") if exif is not None else None
        basis = (hint or {}).get("source", {}).get("basis")
        if basis is None:
            continue
        key = f"{basis.get('offsetSource')}/{basis['placeTimeZone']}/{basis['gpsTimeStamp']}"
        labels[key] = labels.get(key, 0) + 1
        if basis.get("offsetSource") == "offset_tag":
            # Rule 4: the same file read again without its tag derives the tag, or nothing
            exif.get_ifd(ExifTags.IFD.Exif).pop(ExifTags.Base.OffsetTimeOriginal, None)
            bare = exif_hints(exif)["captureTime"]["utcOffsetMinutes"]
            outcome = "nothing" if bare is None else "the tag" if bare == hint["utcOffsetMinutes"] else "another offset"
            without_tag[outcome] = without_tag.get(outcome, 0) + 1
    assert without_tag == {"the tag": 14, "nothing": 10}
    assert labels == {
        "offset_tag/agrees/no_usable_time_stamp": 5, "offset_tag/agrees/agrees": 14, "offset_tag/agrees/disagrees": 2,
        "offset_tag/disagrees/no_usable_time_stamp": 2, "offset_tag/disagrees/agrees": 1,
        f"{ZONED}/agrees/agrees": 8, "None/disagrees/disagrees": 1, "None/unchecked/no_usable_time_stamp": 3,
    }


# ---- Edges the rule must hold where the data is rare ----


def test_two_offsets_at_one_place_that_the_gps_time_both_fits_derive_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Offsets this close were in use before 1979; the place offers two, so nothing is derived and the label says so
    monkeypatch.setattr(metadata, "place_offsets", lambda lat, lon, local: (-180, -175))
    hint = capture(LOCAL, OCEAN, utc_of(LOCAL, -180) - timedelta(seconds=150))
    assert hint["utcOffsetMinutes"] is None
    assert hint["source"]["basis"] == MORE_THAN_ONE


def test_a_gps_time_stamp_exactly_at_the_bound_agrees_with_an_offset_tag() -> None:
    utc = utc_of(LOCAL, -180)
    for leftover in (R, -R):
        basis = capture(LOCAL, OCEAN, utc - timedelta(seconds=leftover), tag="-03:00")["source"]["basis"]
        assert (basis["gpsTimeStamp"], basis["uncertaintySec"]) == ("agrees", R + 1)
    for leftover in (R + 1, -(R + 1), R + 0.01):
        basis = capture(LOCAL, OCEAN, utc - timedelta(seconds=leftover), tag="-03:00")["source"]["basis"]
        assert basis["gpsTimeStamp"] == "disagrees" and "uncertaintySec" not in basis


def test_an_offset_that_is_no_whole_number_of_minutes_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    # Liberia kept UTC-00:44:30 until 1972
    monkeypatch.setattr(metadata, "place_time_zones", lambda lat, lon: ("Africa/Monrovia",))
    with pytest.raises(metadata.NoPlaceOffset, match="no_whole_minute_offset"):
        place_offsets(0.0, 0.0, datetime(1970, 6, 1, 12, 0))
    assert place_offsets(0.0, 0.0, datetime(2031, 6, 1, 12, 0)) == (0,)
    hint = capture(datetime(1970, 6, 1, 12, 0), OCEAN, datetime(1970, 6, 1, 12, 44, 30, tzinfo=timezone.utc))
    assert hint["source"]["basis"] == {"placeTimeZone": "no_whole_minute_offset", "gpsTimeStamp": "unchecked"}


def test_a_whole_minute_offset_beside_one_that_is_not_derives_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liberia moved its clocks on from UTC-00:44:30 to UTC at local midnight starting 7 January 1972, so 00:20 that
    day reads as either. The second version kept only the whole-minute reading and took it from a fresh fix, the hole
    ruling 1 closes: the place offers more than one offset, one of them no whole number of minutes."""
    monkeypatch.setattr(metadata, "place_time_zones", lambda lat, lon: ("Africa/Monrovia",))
    skipped = datetime(1972, 1, 7, 0, 20, 0)
    with pytest.raises(metadata.NoPlaceOffset, match="no_whole_minute_offset"):
        place_offsets(0.0, 0.0, skipped)
    hint = capture(skipped, OCEAN, datetime(1972, 1, 7, 0, 19, 55, tzinfo=timezone.utc))
    assert hint["utcOffsetMinutes"] is None
    assert hint["source"]["basis"] == {"placeTimeZone": "no_whole_minute_offset", "gpsTimeStamp": "unchecked"}
    # A second zone at the point with a whole-minute offset the same: the other zone still gives no whole minute
    monkeypatch.setattr(metadata, "place_time_zones", lambda lat, lon: ("Africa/Monrovia", "Etc/GMT"))
    with pytest.raises(metadata.NoPlaceOffset, match="no_whole_minute_offset"):
        place_offsets(0.0, 0.0, datetime(1970, 6, 1, 12, 0))


def test_a_point_on_a_shared_border_offers_every_zone_it_lies_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata.tzfpy, "get_tzs", lambda lon, lat: ["Europe/London", "Asia/Kolkata"])
    assert place_time_zones(0.0, 0.0) == ("Asia/Kolkata", "Europe/London")
    assert place_offsets(0.0, 0.0, LOCAL) == (60, 330)
    # Even from a fresh fix: a fix 4 h 30 min old would agree with the other zone
    assert capture(LOCAL, OCEAN, utc_of(LOCAL, 330))["source"]["basis"] == MORE_THAN_ONE
    assert capture(LOCAL, OCEAN, utc_of(LOCAL, 60))["source"]["basis"] == MORE_THAN_ONE


def test_two_zones_at_a_border_that_give_the_same_offset_derive_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ruling 1 derives nothing unless the offsets are equal: two zones with one offset at the wall time give it
    monkeypatch.setattr(metadata.tzfpy, "get_tzs", lambda lon, lat: ["Asia/Kolkata", "Asia/Colombo"])
    assert place_offsets(0.0, 0.0, LOCAL) == (330,)
    hint = capture(LOCAL, OCEAN, utc_of(LOCAL, 330) - timedelta(seconds=4))
    assert hint["utcOffsetMinutes"] == 330
    assert hint["source"]["basis"] == {"offsetSource": ZONED, "placeTimeZone": "agrees", "gpsTimeStamp": "agrees", "uncertaintySec": 5}


UNCERTAINTY_EDGES = [(1, True), (1.0, True), (R + 1, True), (0, False), (R + 2, False), (12.5, False), (1.5, False),
                     (-1, False), (True, False), ("13", False)]


@pytest.mark.parametrize(("value", "ok"), UNCERTAINTY_EDGES)
def test_the_contract_takes_an_uncertainty_of_whole_seconds_from_one_to_one_past_the_bound(value: Any, ok: bool) -> None:
    hint = capture(LOCAL, OCEAN, FIX)
    hint["source"]["basis"]["uncertaintySec"] = value
    if ok:
        assert CaptureTimeHint.model_validate(hint).source.basis.uncertainty_sec == value
    else:
        with pytest.raises(ValueError):
            CaptureTimeHint.model_validate(hint)


def test_the_second_versions_label_for_two_offsets_that_both_fit_is_retired() -> None:
    # Unreachable now: two offsets that both fit the GPS time are two offsets the place offers, which derive nothing first
    from services.contracts.ingest import GPS_TIME_STAMP_CHECKS, PLACE_TIME_ZONE_CHECKS

    retired = "more_than_one_offset_fits"
    assert retired not in PLACE_TIME_ZONE_CHECKS and retired not in GPS_TIME_STAMP_CHECKS
    hint = zoneless({"placeTimeZone": retired, "gpsTimeStamp": retired}, ["DateTimeOriginal", *GPS_TAGS])
    with pytest.raises(ValueError):
        CaptureTimeHint.model_validate(hint)


def test_without_the_systems_zone_database_the_label_says_the_data_is_unavailable(tmp_path: Path) -> None:
    """zoneinfo reads the system's time zone database (no tzdata package is installed to fall back on). Pointed at an
    empty folder, as on a machine without it, nothing is derived, a tag still wins, and the label names that cause."""
    import zoneinfo

    original = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
            zoneinfo.ZoneInfo("Etc/GMT+3")
        hint = capture(LOCAL, OCEAN, FIX)
        tagged = capture(LOCAL, OCEAN, FIX, tag="-03:00")
        place_only = capture(LOCAL, OCEAN, None)
    finally:
        zoneinfo.reset_tzpath(to=original)
        zoneinfo.ZoneInfo.clear_cache()
    assert hint["utcOffsetMinutes"] is None
    assert hint["source"]["basis"] == {"placeTimeZone": "time_zone_data_unavailable", "gpsTimeStamp": "unchecked"}
    assert place_only["source"]["basis"] == {"placeTimeZone": "time_zone_data_unavailable", "gpsTimeStamp": "no_usable_time_stamp"}
    assert tagged["utcOffsetMinutes"] == -180
    assert tagged["source"]["basis"]["placeTimeZone"] == "time_zone_data_unavailable"
    # Nothing is left behind for the next test
    assert zoneinfo.TZPATH == original and capture(LOCAL, OCEAN, FIX)["utcOffsetMinutes"] == -180


def test_a_position_the_file_holds_but_cannot_be_used_is_labelled_as_no_usable_position() -> None:
    # A latitude past 90 degrees is dropped, as the reader drops every malformed value; the label says no usable one
    gps = {**position(OCEAN), "GPSLatitude": (Rat(95, 1), Rat(0, 1), Rat(0, 1)), **gps_stamps(FIX)}
    exif = Image.open(io.BytesIO(jpeg({"DateTimeOriginal": local_tag(LOCAL)}, gps))).getexif()
    assert exif_hints(exif)["captureTime"]["source"]["basis"] == {"placeTimeZone": "no_usable_position", "gpsTimeStamp": "unchecked"}


def test_a_pose_bound_counts_the_time_to_the_degree_either_side_of_a_whole_number() -> None:
    """The time's share of the bound is its arcseconds over 3,600: of two uncertainties a second apart, the one that
    puts the sum past a whole number of degrees is counted up to the next, and the one a second less is not."""
    from services.science.predicted_sky import SKY_TURN_ARCSEC_PER_SEC, complete_sky
    from tests.science.test_pose_estimate import _asset as head_worn_asset
    from tests.science.test_pose_estimate import _soft_moon, _solver_solution

    luma = _soft_moon(500.3, 700.6)
    plain = complete_sky(_solver_solution(), head_worn_asset(), frames=lambda: [(0.0, luma)])
    base = plain.pose_estimate.max_center_offset_deg + plain.pose_estimate.beacon_centroid_uncertainty_deg
    per_sec = SKY_TURN_ARCSEC_PER_SEC / 3600
    # The first whole number of degrees a whole number of seconds from 2 to WIDEST crosses (WIDEST s turn the sky 1.26 degrees)
    whole = math.floor(base + 2 * per_sec) + 1
    past = math.ceil((whole - base) / per_sec + 1e-9)
    assert 2 <= past <= WIDEST and base + (past - 1) * per_sec <= whole < base + past * per_sec
    for seconds, expected in ((past, whole + 1), (past - 1, whole)):
        asset = head_worn_asset()
        asset["metadataSources"] = {"capturedAtUTC": {"container": "exif", "tags": ["DateTimeOriginal", *GPS_TAGS],
                                                      "basis": {**ZONED_BASIS, "uncertaintySec": seconds}}}
        out = complete_sky(_solver_solution(), asset, frames=lambda: [(0.0, luma)])
        assert f"within about {expected} degrees near the bright spot" in out.warnings[-3]
