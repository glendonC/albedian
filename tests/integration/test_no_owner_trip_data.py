"""Guard: no tracked file holds a capture place or time from the owner's own clips.

The owner's night clips in `data/uploads/` carry a GPS position and a capture time. The guard reads them from
the clips with ffprobe at test time, so the values are never written into the repository, and fails when a
tracked text file holds one of these:
- A coordinate pair within 50 km of a clip, read in two ways:
  - any two numbers on lines at most 6 apart, which covers tuples, keyword and positional arguments and JSON
    fields across lines, and ISO 6709 strings and hemisphere letters;
  - a far pair: two numbers written with a fraction anywhere in one file, more than 6 lines apart in either order.
    Integers pair only within 6 lines, so counts, sizes and versions across a file are not read as places.
  A number with an exponent (2.5E+3) is read as one number, never as its mantissa and its power.
- A time within 48 hours of one, in any of these forms:
  - an ISO 8601 date-time, epoch seconds or milliseconds, or a Julian date;
  - a Python `datetime(y, m, d, ...)` argument list;
  - a JavaScript `Date.UTC(y, m, ...)` or `new Date(y, m, ...)` argument list (months from 0, read as UTC);
  - a month, day and time of day with no year (`-05-14T09:30`), compared with the clip's in every year.
- A date within 2 days of one: a bare ISO date, or a date in words (30 August 2031, August 30, 2031, Aug 30 2031).
Lockfiles are exempt: their digits are package hashes, versions and upload times. A failure names only file:line
(for a far pair, both lines) and the kind of match, never a value, a distance or a gap.

Reviewed coincidences are accepted only by digest (ACCEPTED_COINCIDENCES below), so git never says which lines
they are.

`data/uploads/` is not in git, so the guard runs where the clips are and is skipped, with its reason, everywhere
else (CI included). The scanner itself is tested on synthetic places and times, which run everywhere. Photos'
EXIF metadata is not read: today the folder holds only video clips. Not read [open]: degrees, minutes and
seconds; numeric day/month dates such as 01/11/2030; a time of day with no date.

Print the digest of one reviewed line, or of a far pair's latitude and longitude lines, where the clips are:
    uv run python -m tests.integration.test_no_owner_trip_data <path>:<line>
    uv run python -m tests.integration.test_no_owner_trip_data <path>:<latitude line>+<longitude line>
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from hypothesis import example, given, settings, strategies as st
import pytest

from services.media.discovery import normalize_creation_utc, parse_iso6709_location

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_MEDIA = REPO_ROOT / "data" / "uploads"
VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v"}
RADIUS_M = 50_000.0
WINDOW = timedelta(hours=48)
DATE_DAYS = 2
PAIR_LINES = 6
# Package managers write package hashes, versions and upload times into lockfiles; none is a place or a capture time
LOCKFILES = {"uv.lock", "pnpm-lock.yaml"}

# Reviewed coincidences: lines whose numbers or dates fall near a clip by chance. Each entry is an HMAC-SHA256 of
# the file's path and the line's text (for a far pair, both lines' text), keyed by a hash of the clips' own places
# and times, so only someone holding the clips can tell which lines these are; their files, lines and reasons are in
# the lead's record, not in git. A digest stops matching when its lines or the clips change, and the guard then
# flags them again.
ACCEPTED_COINCIDENCES: frozenset[str] = frozenset(
    {
        "2b346d37c1bd47816642fcd278819b0e7aeec84caa7a4eb4db8ae120c2ff32a9",
        "44303f726b55ec64097c597b787bbad2fb68d929b66d939bb0c6ebeb7affb4f7",
        "453186839c7ed7ae5152ef96990870c20e77a83f9cbaa947fe9c429ba257db31",
        "564720015d0591763a5bf4e14f71f7418a1b1c8c2c20203d4d1ac801de714c8b",
        "573bfe9ccc5af67b83f9ae4cdc7de49f9611d7f86b04b0021cfd40f1238f498b",
        "5dad2218afe0c892ef8171adb3592fe16ce7312d0da2299a7558f996f39d1c2c",
        "61b94b54b7d1e8d8463ec19a84fc2dd0c60749dfc7000f96881412a33e74543c",
        "7ce1c662a275f7b6606cd89aff38aa417200ca50c858bf7cfab6add26db7ff8f",
        "93b61176e114d407a1b634a9a3a39c42d0a6ba00c796379f2e949bb9559a1b66",
        "b179ebe61718cca2691321a9f9fd940fef0f26a361f743644f5a7ba3b85f1751",
        "b3ba075e841c07c5aa13857277d98a90463b5860873eb9e6068660815abb62a6",
        "d38b64cd593743c8d54986934e9dc1b3d1124214696a8ddc9836de5481902687",
        "f704dd659c92a0c6b7b42d06f860fe8ae9f578fa345b0dbbf1cc14c069252703",
        "fa2f6f02fc0f6239574f23534a336462727f92667b7e9892d9df36131b585307",
    }
)

# An exponent belongs to its number (2.5E+3 is one value, never 2.5 east and 3), unless a letter or a dot follows it,
# as in a hex digest
EXPONENT = r"(?:[eE][+-]?\d+(?![\w.]))?"
NUMBER = re.compile(
    r"(?P<hemi>\d+(?:\.\d+)?)\s*°?\s*(?P<side>[NSEW])(?![A-Za-z]|(?<=E)[+-]?\d)"
    rf"|(?P<signed>[+-]\d+(?:\.\d+)?{EXPONENT})"
    rf"|(?<![\d.+-])(?P<plain>\d+(?:\.\d+)?{EXPONENT})"
)
FRACTION = re.compile(r"\.\d")
ISO_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?"
)
EPOCH = re.compile(r"(?<![\d.])(1\d{9})(?:\d{3})?(?:\.\d+)?(?!\d)")
JULIAN_DATE = re.compile(r"(?<![\d.])(24\d{5}\.\d+)(?!\d)")
PY_DATETIME = re.compile(
    r"\bdatetime\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})(?:\s*,\s*(\d{1,2}))?(?:\s*,\s*(\d{1,2}))?(?:\s*,\s*(\d{1,2}))?"
)
JS_DATE = re.compile(
    r"(?:\bDate\.UTC|\bnew\s+Date)\(\s*(\d{4})\s*,\s*(\d{1,2})(?:\s*,\s*(\d{1,2}))?(?:\s*,\s*(\d{1,2}))?"
    r"(?:\s*,\s*(\d{1,2}))?(?:\s*,\s*(\d{1,2}))?"
)
YEARLESS = re.compile(r"(?<![\w-])-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
BARE_DATE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])(?![T ]\d{2}:\d{2})")
MONTH = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
DAY_FIRST = re.compile(rf"(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH},?\s+(?P<year>\d{{4}})(?!\d)", re.I)
MONTH_FIRST = re.compile(rf"(?<![A-Za-z]){MONTH}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})(?!\d)", re.I)
MONTH_NUMBERS = {name: number for number, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
J2000_JULIAN_DATE = 2451545.0
EARTH_RADIUS_M = 6_371_000.0
METRES_PER_DEGREE = math.pi * EARTH_RADIUS_M / 180.0
KINDS = {
    "place": "coordinate pair within 50 km of a clip position",
    "time": "time within 48 h of a clip time",
    "date": "date within 2 days of a clip date",
}


@dataclass(frozen=True)
class Capture:
    place: tuple[float, float] | None
    time: datetime | None


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _numbers_on(line: str) -> list[tuple[float, bool]]:
    """Each number on the line that could be a coordinate, and whether it is written with a fraction."""
    values = []
    for match in NUMBER.finditer(line):
        if match.group("hemi") is not None:
            value = float(match.group("hemi")) * (-1.0 if match.group("side") in "SW" else 1.0)
        else:
            value = float(match.group("signed") or match.group("plain"))
        if abs(value) <= 180.0:
            values.append((value, FRACTION.search(match.group(0)) is not None))
    return values


def numbers_on(line: str) -> list[float]:
    """Every decimal on the line that could be a coordinate, with hemisphere letters read as signs."""
    return [value for value, _ in _numbers_on(line)]


def _tolerances(places: list[tuple[float, float]]) -> tuple[float, list[float]]:
    """How far a number may be from a clip's latitude, and from each clip's longitude, and still be within the radius."""
    lat_tolerance = RADIUS_M / METRES_PER_DEGREE * 1.01
    return lat_tolerance, [lat_tolerance / max(math.cos(math.radians(p[0])), 0.01) for p in places]


def _utc(*parts: int) -> datetime | None:
    try:
        return datetime(*parts, tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


def times_on(line: str) -> list[datetime]:
    """ISO 8601 date-times (no zone read as UTC), epoch seconds or milliseconds, Julian dates, and Python datetime(...)
    and JavaScript Date.UTC(...) or new Date(...) argument lists (JavaScript months count from 0)."""
    found = []
    for match in ISO_DATETIME.finditer(line):
        year, month, day, hour, minute, second, zone = match.groups()
        offset = timedelta(0)
        if zone and zone != "Z":
            digits = zone[1:].replace(":", "")
            offset = (1 if zone[0] == "+" else -1) * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        local = _utc(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
        if local is not None:
            found.append(local - offset)
    found += [datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc) for m in EPOCH.finditer(line)]
    found += [J2000 + timedelta(days=float(m.group(1)) - J2000_JULIAN_DATE) for m in JULIAN_DATE.finditer(line)]
    for m in PY_DATETIME.finditer(line):
        found.append(_utc(*(int(part or 0) for part in m.groups())))
    for m in JS_DATE.finditer(line):
        year, month, day, hour, minute, second = (int(part) if part else None for part in m.groups())
        found.append(_utc(year, month + 1, day or 1, hour or 0, minute or 0, second or 0))
    return [moment for moment in found if moment is not None]


def yearless_times_on(line: str) -> list[tuple[int, int, int, int]]:
    """(month, day, hour, minute) for each month, day and time of day written without a year, as in -05-14T09:30."""
    return [tuple(int(part) for part in m.groups()) for m in YEARLESS.finditer(line)]


def dates_on(line: str) -> list[date]:
    """Bare ISO dates (YYYY-MM-DD with no time after) and dates in words, day first or month first."""
    found = []
    for m in BARE_DATE.finditer(line):
        try:
            found.append(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            pass
    for pattern in (DAY_FIRST, MONTH_FIRST):
        for m in pattern.finditer(line):
            try:
                found.append(date(int(m["year"]), MONTH_NUMBERS[m["month"].lower()[:3]], int(m["day"])))
            except ValueError:
                pass
    return found


def _yearless_gap(moment: tuple[int, int, int, int], capture: datetime) -> timedelta | None:
    """The shortest gap between a yearless moment, placed in any year, and the capture time."""
    month, day, hour, minute = moment
    gaps = [
        abs(candidate - capture)
        for year in (capture.year - 1, capture.year, capture.year + 1)
        if (candidate := _utc(year, month, day, hour, minute)) is not None
    ]
    return min(gaps, default=None)


def scan_text(
    text: str, captures: list[Capture], parsed: list[list[tuple[float, bool]]] | None = None
) -> list[tuple[int, str, float]]:
    """(line number, kind, how close) for each line that holds a capture place ('place', metres), time ('time',
    hours) or date ('date', days). Only the tests read how close; the guard reports line and kind. `parsed` is
    each line's _numbers_on, when the caller has read them already."""
    lines = text.splitlines()
    places = [c.place for c in captures if c.place is not None]
    times = [c.time for c in captures if c.time is not None]
    closest: dict[tuple[int, str], float] = {}

    def hit(line: int, kind: str, measure: float) -> None:
        closest[(line, kind)] = min(measure, closest.get((line, kind), math.inf))

    if places:
        # Keep only numbers within the radius of some clip in one axis, so pairing stays cheap on long lines
        lat_tolerance, lon_tolerance = _tolerances(places)
        rows = parsed if parsed is not None else [_numbers_on(line) for line in lines]
        numbers = [[value for value, _ in row] for row in rows]
        near_lat = [[v for v in row if any(abs(v - p[0]) <= lat_tolerance for p in places)] for row in numbers]
        near_lon = [
            [v for v in row if any(abs(v - p[1]) <= tol for p, tol in zip(places, lon_tolerance))] for row in numbers
        ]
        for i, lats in enumerate(near_lat):
            for j in range(max(0, i - PAIR_LINES), min(len(lines), i + PAIR_LINES + 1)):
                for lat in lats:
                    for lon in near_lon[j]:
                        metres = min(haversine_m((lat, lon), place) for place in places)
                        if metres <= RADIUS_M:
                            hit(i + 1, "place", metres)

    if times:
        capture_dates = {t.date() for t in times}
        for i, line in enumerate(lines):
            for moment in times_on(line):
                gap = min(abs(moment - t) for t in times)
                if gap <= WINDOW:
                    hit(i + 1, "time", gap / timedelta(hours=1))
            for moment in yearless_times_on(line):
                gaps = [g for t in times if (g := _yearless_gap(moment, t)) is not None]
                if gaps and min(gaps) <= WINDOW:
                    hit(i + 1, "time", min(gaps) / timedelta(hours=1))
            for day in dates_on(line):
                days = min(abs((day - d).days) for d in capture_dates)
                if days <= DATE_DAYS:
                    hit(i + 1, "date", float(days))
    return sorted((line, kind, measure) for (line, kind), measure in closest.items())


def far_pairs(
    text: str, captures: list[Capture], parsed: list[list[tuple[float, bool]]] | None = None
) -> list[tuple[int, int, float]]:
    """(latitude line, longitude line, metres) for each far pair: a number written with a fraction near a clip's
    latitude and another near its longitude, more than PAIR_LINES lines apart in either order, together within the
    radius. Pairs within PAIR_LINES, integers included, are scan_text's. Only the tests read the metres. `parsed`
    is each line's _numbers_on, when the caller has read them already."""
    places = [c.place for c in captures if c.place is not None]
    if not places:
        return []
    lat_tolerance, lon_tolerance = _tolerances(places)
    # A number with a fraction has a dot, so a line without one needs no parse
    rows = parsed if parsed is not None else [_numbers_on(line) if "." in line else [] for line in text.splitlines()]
    lats: list[tuple[int, float]] = []
    lons: list[tuple[int, float]] = []
    for i, row in enumerate(rows):
        for value, fraction in row:
            if not fraction:
                continue
            if any(abs(value - p[0]) <= lat_tolerance for p in places):
                lats.append((i + 1, value))
            if any(abs(value - p[1]) <= tol for p, tol in zip(places, lon_tolerance)):
                lons.append((i + 1, value))
    closest: dict[tuple[int, int], float] = {}
    for lat_line, lat in lats:
        for lon_line, lon in lons:
            if abs(lat_line - lon_line) <= PAIR_LINES:
                continue
            metres = min(haversine_m((lat, lon), place) for place in places)
            if metres <= RADIUS_M:
                closest[(lat_line, lon_line)] = min(metres, closest.get((lat_line, lon_line), math.inf))
    return sorted((lat_line, lon_line, metres) for (lat_line, lon_line), metres in closest.items())


def capture_key(captures: list[Capture]) -> bytes:
    """A key only the clips can give: a hash of their places and times, never written anywhere."""
    canonical = sorted(
        (f"{c.place[0]:.6f},{c.place[1]:.6f}" if c.place else "-") + "|" + (c.time.isoformat() if c.time else "-")
        for c in captures
    )
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).digest()


def line_digest(key: bytes, rel: str, line: str) -> str:
    return hmac.new(key, f"{rel}\n{line.strip()}".encode("utf-8"), hashlib.sha256).hexdigest()


def pair_digest(key: bytes, rel: str, lat_line: str, lon_line: str) -> str:
    """A far pair's digest: both lines' text, latitude first, tagged so that it never equals a line's digest."""
    payload = f"far pair\n{rel}\n{lat_line.strip()}\n{lon_line.strip()}"
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def findings(rel: str, text: str, captures: list[Capture], accepted: frozenset[str] = ACCEPTED_COINCIDENCES) -> list[str]:
    """'path:line: kind' for each line that holds a capture place, time or date, and 'path:line: kind, with line
    N' for each far pair, unless lockfile or accepted."""
    if Path(rel).name in LOCKFILES:
        return []
    key = capture_key(captures)
    lines = text.splitlines()
    # Both place rules read the same numbers, so each line is parsed once
    parsed = [_numbers_on(line) for line in lines] if any(c.place for c in captures) else None
    near = [
        f"{rel}:{line}: a {KINDS[kind]}"
        for line, kind, _ in scan_text(text, captures, parsed)
        if line_digest(key, rel, lines[line - 1]) not in accepted
    ]
    far = [
        f"{rel}:{lat_line}: a {KINDS['place']}, with line {lon_line}"
        for lat_line, lon_line, _ in far_pairs(text, captures, parsed)
        if pair_digest(key, rel, lines[lat_line - 1], lines[lon_line - 1]) not in accepted
    ]
    return near + far


def read_captures(folder: Path) -> list[Capture]:
    """Each clip's QuickTime GPS position and capture time, read with ffprobe."""
    captures = []
    for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            continue
        data = json.loads(probe.stdout or b"{}")
        tags = dict(data.get("format", {}).get("tags", {}))
        for stream in data.get("streams", []):
            for key, value in stream.get("tags", {}).items():
                tags.setdefault(key, value)
        location = parse_iso6709_location(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location"))
        captured = normalize_creation_utc(tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time"))
        captures.append(
            Capture(
                place=(location[0], location[1]) if location else None,
                time=datetime.strptime(captured, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if captured else None,
            )
        )
    return captures


def tracked_text_files() -> Iterator[tuple[str, str]]:
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True)
    for rel in listed.stdout.decode("utf-8").split("\0"):
        path = REPO_ROOT / rel
        if not rel or not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        yield rel, raw.decode("utf-8", errors="replace")


# Synthetic, pointing at nobody: the open North Atlantic, about 1,321 km from Flores in the Azores
SYNTHETIC = [Capture(place=(30.0, -40.0), time=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc))]
OTHER_SYNTHETIC = [Capture(place=(-20.0, 60.0), time=datetime(2031, 3, 5, 4, 10, tzinfo=timezone.utc))]


def destination(place: tuple[float, float], metres: float, bearing_deg: float) -> tuple[float, float]:
    """The point `metres` from `place` along `bearing_deg` on a spherical Earth."""
    lat1, lon1, bearing = math.radians(place[0]), math.radians(place[1]), math.radians(bearing_deg)
    angle = metres / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(angle) + math.cos(lat1) * math.sin(angle) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angle) * math.cos(lat1), math.cos(angle) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


def kinds(text: str, captures: list[Capture] = SYNTHETIC) -> list[tuple[int, str]]:
    return [(line, kind) for line, kind, _ in scan_text(text, captures)]


# =============================================================================================
# Places
# =============================================================================================

PLACE_FORMS = {
    "tuple": lambda lat, lon: f"MOMENT = ({lat:.4f}, {lon:.4f})",
    "keyword arguments": lambda lat, lon: f"observe(lat_deg={lat:.4f}, lon_deg={lon:.4f})",
    "positional arguments": lambda lat, lon: f"Capture(place=({lat:.4f}, {lon:.4f}), time=None)",
    "json across lines": lambda lat, lon: f'{{\n  "title": "x",\n  "latDeg": {lat:.4f},\n  "lonDeg": {lon:.4f}\n}}',
    "iso 6709": lambda lat, lon: f"{lat:+08.4f}{lon:+09.4f}/",
    "hemisphere letters": lambda lat, lon: f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}",
}


@pytest.mark.parametrize("form", sorted(PLACE_FORMS))
def test_a_planted_place_within_50_km_is_reported_in_every_form_and_one_beyond_is_not(form: str) -> None:
    near = destination(SYNTHETIC[0].place, 30_000.0, 120.0)
    far = destination(SYNTHETIC[0].place, 70_000.0, 120.0)
    assert [kind for _, kind in kinds(PLACE_FORMS[form](*near))] == ["place"]
    assert kinds(PLACE_FORMS[form](*far)) == []


def test_a_place_is_reported_by_line_and_kind_and_the_value_never_appears() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 400.0, 45.0)
    text = PLACE_FORMS["json across lines"](lat, lon)
    assert findings("fixture.json", text, SYNTHETIC) == [
        "fixture.json:3: a coordinate pair within 50 km of a clip position"
    ]
    assert scan_text(text, SYNTHETIC)[0][2] == pytest.approx(400.0, abs=15.0)


def test_numbers_with_a_fraction_anywhere_in_one_file_are_a_pair_in_either_order() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 1000.0, 10.0)
    for gap in (PAIR_LINES + 1, 100, 3000):
        filler = ["filler"] * (gap - 1)
        text = "\n".join([f"lat = {lat:.4f}", *filler, f"lon = {lon:.4f}"])
        assert findings("fixture.py", text, SYNTHETIC) == [
            f"fixture.py:1: a coordinate pair within 50 km of a clip position, with line {gap + 1}"
        ]
        swapped = "\n".join([f"lon = {lon:.4f}", *filler, f"lat = {lat:.4f}"])
        assert findings("fixture.py", swapped, SYNTHETIC) == [
            f"fixture.py:{gap + 1}: a coordinate pair within 50 km of a clip position, with line 1"
        ]


def test_integers_more_than_six_lines_apart_or_a_lone_latitude_are_not_a_pair() -> None:
    lat, lon = SYNTHETIC[0].place
    integers = "\n".join([f"{lat:.0f}", *["filler"] * PAIR_LINES, f"{lon:.0f}"])
    assert findings("fixture.py", integers, SYNTHETIC) == []
    near_lat, _ = destination(SYNTHETIC[0].place, 1000.0, 10.0)
    lone = "\n".join([f"{near_lat:.6f}", "lone latitude without a longitude", *["filler"] * 50])
    assert findings("fixture.py", lone, SYNTHETIC) == []


def test_a_number_with_an_exponent_is_read_as_one_number() -> None:
    assert numbers_on("tolerance = 2e-3") == [pytest.approx(2e-3)]
    assert numbers_on("scale: -2.5E-1,") == [pytest.approx(-0.25)]
    assert numbers_on("2.5E+3 metres") == []
    # Read as two numbers, the exponent's mantissa and power would pair with the bound above it
    lat, lon = SYNTHETIC[0].place
    assert findings("fixture.py", f"row < {lat:.0f}\nassert err < 1e{lon:.0f}", SYNTHETIC) == []


@settings(deadline=None, max_examples=150)
@given(
    metres=st.floats(500.0, 120_000.0),
    bearing=st.floats(0.0, 360.0),
    gap=st.integers(PAIR_LINES + 1, 3000),
)
@example(metres=49_900.0, bearing=90.0, gap=PAIR_LINES + 1)
@example(metres=50_100.0, bearing=90.0, gap=PAIR_LINES + 1)
@example(metres=49_900.0, bearing=0.0, gap=3000)
@example(metres=50_100.0, bearing=180.0, gap=3000)
def test_pbt_a_far_pair_is_reported_exactly_when_within_50_km(metres: float, bearing: float, gap: int) -> None:
    lat, lon = destination(SYNTHETIC[0].place, metres, bearing)
    text = "\n".join([f"{lat:.4f}", *["filler"] * (gap - 1), f"{lon:.4f}"])
    planted = haversine_m((round(lat, 4), round(lon, 4)), SYNTHETIC[0].place)
    assert bool(findings("fixture.py", text, SYNTHETIC)) == (planted <= RADIUS_M)


def test_a_pair_near_any_clip_is_reported_not_only_near_the_first() -> None:
    # Two clips, the pairs planted near the second, so a guard that checks only the first clip fails here
    two_clips = [*SYNTHETIC, *OTHER_SYNTHETIC]
    lat, lon = destination(OTHER_SYNTHETIC[0].place, 1000.0, 200.0)
    far = "\n".join([f"lat = {lat:.4f}", *["filler"] * 20, f"lon = {lon:.4f}"])
    assert findings("fixture.py", far, two_clips) == [
        "fixture.py:1: a coordinate pair within 50 km of a clip position, with line 22"
    ]
    near = f"MOMENT = ({lat:.4f}, {lon:.4f})"
    assert findings("fixture.py", near, two_clips) == ["fixture.py:1: a coordinate pair within 50 km of a clip position"]


@settings(deadline=None, max_examples=150)
@given(
    metres=st.floats(500.0, 120_000.0),
    bearing=st.floats(0.0, 360.0),
    form=st.sampled_from(sorted(PLACE_FORMS)),
)
@example(metres=49_900.0, bearing=90.0, form="tuple")
@example(metres=50_100.0, bearing=90.0, form="tuple")
@example(metres=49_900.0, bearing=0.0, form="json across lines")
@example(metres=50_100.0, bearing=180.0, form="iso 6709")
def test_pbt_a_place_is_reported_exactly_when_within_50_km(metres: float, bearing: float, form: str) -> None:
    lat, lon = destination(SYNTHETIC[0].place, metres, bearing)
    text = PLACE_FORMS[form](lat, lon)
    # The forms write four decimals, so measure the place as written
    planted = haversine_m((round(lat, 4), round(lon, 4)), SYNTHETIC[0].place)
    assert ("place" in [kind for _, kind in kinds(text)]) == (planted <= RADIUS_M)


# =============================================================================================
# Times and dates
# =============================================================================================

TIME_FORMS = {
    "iso utc": lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "iso with offset": lambda t: (t + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+01:00"),
    "iso with a space": lambda t: t.strftime("%Y-%m-%d %H:%M"),
    "epoch seconds": lambda t: str(int(t.timestamp())),
    "epoch milliseconds": lambda t: str(int(t.timestamp()) * 1000),
    "julian date": lambda t: f"{J2000_JULIAN_DATE + (t - J2000) / timedelta(days=1):.5f}",
    "python datetime": lambda t: f"datetime({t.year}, {t.month}, {t.day}, {t.hour}, {t.minute}, tzinfo=timezone.utc)",
    "js Date.UTC": lambda t: f"Date.UTC({t.year}, {t.month - 1}, {t.day}, {t.hour}, {t.minute})",
    "js new Date": lambda t: f"new Date({t.year}, {t.month - 1}, {t.day}, {t.hour}, {t.minute})",
    "no year": lambda t: f'f"{{year}}-{t.month:02d}-{t.day:02d}T{t.hour:02d}:{t.minute:02d}:00Z"',
}
DATE_FORMS = {
    "bare iso": lambda d: f"released {d.isoformat()}",
    "day month year": lambda d: f"observed on {d.day} {d.strftime('%B')} {d.year}",
    "month day, year": lambda d: f"observed on {d.strftime('%B')} {d.day}, {d.year}",
    "short month": lambda d: f"{d.strftime('%b')} {d.day} {d.year}",
    "ordinal": lambda d: f"the {d.day}th of {d.strftime('%B')} {d.year}",
}


@pytest.mark.parametrize("form", sorted(TIME_FORMS))
def test_a_planted_time_within_48_hours_is_reported_in_every_form_and_one_beyond_is_not(form: str) -> None:
    near = SYNTHETIC[0].time - timedelta(hours=30)
    far = SYNTHETIC[0].time + timedelta(hours=60)
    assert [kind for _, kind in kinds(TIME_FORMS[form](near))] == ["time"]
    assert kinds(TIME_FORMS[form](far)) == []


@pytest.mark.parametrize("form", sorted(DATE_FORMS))
def test_a_planted_date_within_two_days_is_reported_in_every_form_and_one_beyond_is_not(form: str) -> None:
    capture_day = SYNTHETIC[0].time.date()
    assert [kind for _, kind in kinds(DATE_FORMS[form](capture_day + timedelta(days=2)))] == ["date"]
    assert kinds(DATE_FORMS[form](capture_day - timedelta(days=3))) == []


def test_a_time_with_no_year_is_compared_in_every_year_and_across_new_year() -> None:
    assert [kind for _, kind in kinds('"-08-31T10:00:00Z"')] == ["time"]
    assert kinds('"-09-03T10:00:00Z"') == []
    late = [Capture(place=None, time=datetime(2030, 12, 31, 23, 0, tzinfo=timezone.utc))]
    assert [kind for _, kind in kinds('"-01-01T10:00:00Z"', late)] == ["time"]
    # A full ISO date-time is read once, as a time, not again as a time with no year
    assert len(scan_text(SYNTHETIC[0].time.strftime("%Y-%m-%dT%H:%M:%SZ"), SYNTHETIC)) == 1


def test_month_and_year_alone_and_lockfiles_are_not_reported() -> None:
    assert kinds("released in August 2031") == []
    assert kinds("2031-08") == []
    lat, lon = destination(SYNTHETIC[0].place, 100.0, 0.0)
    planted = f"{lat:.4f}, {lon:.4f} {SYNTHETIC[0].time.isoformat()}"
    assert findings("uv.lock", planted, SYNTHETIC) == []
    assert findings("apps/web/pnpm-lock.yaml", planted, SYNTHETIC) == []
    assert all((REPO_ROOT / name).is_file() for name in LOCKFILES)


@settings(deadline=None, max_examples=150)
@given(minutes=st.integers(2 * 60, 90 * 60), sign=st.sampled_from([-1, 1]), form=st.sampled_from(sorted(TIME_FORMS)))
@example(minutes=48 * 60, sign=1, form="iso utc")
@example(minutes=48 * 60 + 1, sign=1, form="iso utc")
@example(minutes=48 * 60, sign=-1, form="js Date.UTC")
@example(minutes=48 * 60 + 1, sign=-1, form="no year")
def test_pbt_a_time_is_reported_exactly_when_within_48_hours(minutes: int, sign: int, form: str) -> None:
    gap = timedelta(minutes=minutes)
    written = TIME_FORMS[form](SYNTHETIC[0].time + sign * gap)
    if form == "julian date" and gap == WINDOW:
        return  # five decimals of a day round within a second either side of the boundary
    assert ("time" in [kind for _, kind in kinds(written)]) == (gap <= WINDOW)


@settings(deadline=None)
@given(days=st.integers(-6, 6), form=st.sampled_from(sorted(DATE_FORMS)))
@example(days=2, form="bare iso")
@example(days=3, form="bare iso")
@example(days=-2, form="ordinal")
@example(days=-3, form="month day, year")
def test_pbt_a_date_is_reported_exactly_when_within_two_days(days: int, form: str) -> None:
    written = DATE_FORMS[form](SYNTHETIC[0].time.date() + timedelta(days=days))
    assert ("date" in [kind for _, kind in kinds(written)]) == (abs(days) <= DATE_DAYS)


# =============================================================================================
# Accepted coincidences
# =============================================================================================


def test_an_accepted_line_is_skipped_only_under_the_clips_it_was_accepted_with() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 20_000.0, 300.0)
    line = f"    {lat:.2f}, {lon:.2f},"
    text = f"rows = [\n{line}\n]"
    assert findings("catalog.py", text, SYNTHETIC) == ["catalog.py:2: a coordinate pair within 50 km of a clip position"]
    accepted = frozenset({line_digest(capture_key(SYNTHETIC), "catalog.py", line)})
    assert findings("catalog.py", text, SYNTHETIC, accepted) == []
    # The same digest under other clips, under another path or for an edited line accepts nothing
    moved = [Capture(place=SYNTHETIC[0].place, time=SYNTHETIC[0].time + timedelta(seconds=1))]
    assert findings("catalog.py", text, moved, accepted) != []
    assert findings("other.py", text, SYNTHETIC, accepted) != []
    edited = text.replace(f"{lat:.2f}", f"{lat + 0.01:.2f}")
    assert findings("catalog.py", edited, SYNTHETIC, accepted) != []


def test_an_accepted_far_pair_is_skipped_only_while_its_path_both_lines_and_the_clips_are_unchanged() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 20_000.0, 300.0)
    lat_line, lon_line = f"    lat = {lat:.4f},", f"    lon = {lon:.4f},"
    text = "\n".join(["rows = [", lat_line, *["    filler,"] * 10, lon_line, "]"])
    assert findings("catalog.py", text, SYNTHETIC) == [
        "catalog.py:2: a coordinate pair within 50 km of a clip position, with line 13"
    ]
    key = capture_key(SYNTHETIC)
    accepted = frozenset({pair_digest(key, "catalog.py", lat_line, lon_line)})
    assert findings("catalog.py", text, SYNTHETIC, accepted) == []
    moved = [Capture(place=SYNTHETIC[0].place, time=SYNTHETIC[0].time + timedelta(seconds=1))]
    assert findings("catalog.py", text, moved, accepted) != []
    assert findings("other.py", text, SYNTHETIC, accepted) != []
    for edited in (text.replace(f"{lat:.4f}", f"{lat + 0.0001:.4f}"), text.replace(f"{lon:.4f}", f"{lon + 0.0001:.4f}")):
        assert findings("catalog.py", edited, SYNTHETIC, accepted) != []
    # A digest of either line alone accepts nothing
    either_line = frozenset({line_digest(key, "catalog.py", lat_line), line_digest(key, "catalog.py", lon_line)})
    assert findings("catalog.py", text, SYNTHETIC, either_line) != []


def test_a_digest_reveals_neither_the_line_nor_the_clips() -> None:
    key = capture_key(SYNTHETIC)
    assert capture_key(OTHER_SYNTHETIC) != key
    digest = line_digest(key, "catalog.py", "30.10, -40.20,")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert "30.1" not in digest and "catalog" not in digest
    pair = pair_digest(key, "catalog.py", "30.10,", "-40.20,")
    assert re.fullmatch(r"[0-9a-f]{64}", pair) and pair != line_digest(key, "catalog.py", "30.10,")
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry) for entry in ACCEPTED_COINCIDENCES)


def test_the_helper_prints_a_far_pairs_digest_and_a_lines_digest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Synthetic clips stand in for data/uploads, so this runs everywhere
    monkeypatch.setitem(globals(), "read_captures", lambda folder: SYNTHETIC)
    rel = "tests/integration/test_no_owner_trip_data.py"
    lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
    assert main([f"{rel}:1+20", f"{rel}:3"]) == 0
    key = capture_key(SYNTHETIC)
    assert capsys.readouterr().out.splitlines() == [
        f"{rel}:1+20: {pair_digest(key, rel, lines[0], lines[19])}",
        f"{rel}:3: {line_digest(key, rel, lines[2])}",
    ]


# =============================================================================================
# The guard itself
# =============================================================================================


@pytest.mark.owner_media
def test_no_tracked_file_holds_a_capture_place_or_time_from_the_owners_clips() -> None:
    if shutil.which("ffprobe") is None:
        pytest.fail("ffprobe is needed to read the clips' GPS positions and capture times")
    captures = read_captures(OWNER_MEDIA)
    if not any(c.place or c.time for c in captures):
        pytest.fail(f"no clip in data/uploads carries a GPS position or a capture time ({len(captures)} read)")
    found = [finding for rel, text in tracked_text_files() for finding in findings(rel, text, captures)]
    assert not found, (
        f"{len(found)} tracked line(s) hold a place or time near the owner's clips. Replace them with synthetic values; "
        "for a reviewed coincidence, add the digest that `uv run python -m tests.integration.test_no_owner_trip_data "
        "<path>:<line>` (for a far pair, <path>:<line>+<line>) prints to ACCEPTED_COINCIDENCES:\n" + "\n".join(found)
    )


def main(specs: list[str]) -> int:
    """Print the digest of each named line, or of each far pair (path:latitude line+longitude line), keyed by the
    clips in data/uploads; never a value."""
    captures = read_captures(OWNER_MEDIA)
    if not any(c.place or c.time for c in captures):
        print("no clip in data/uploads carries a GPS position or a capture time", file=sys.stderr)
        return 1
    key = capture_key(captures)
    for spec in specs:
        rel, _, numbers = spec.rpartition(":")
        lines = (REPO_ROOT / rel).read_bytes().decode("utf-8", errors="replace").splitlines()
        if "+" in numbers:
            lat_line, lon_line = (int(number) for number in numbers.split("+"))
            digest = pair_digest(key, rel, lines[lat_line - 1], lines[lon_line - 1])
        else:
            digest = line_digest(key, rel, lines[int(numbers) - 1])
        print(f"{spec}: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
