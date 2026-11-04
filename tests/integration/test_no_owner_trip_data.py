"""Guard: no tracked file holds a capture place or time from the owner's own clips.

The owner's night clips in `data/uploads/` carry a GPS position and a capture time. The guard reads
them from the clips with ffprobe at test time, so the values are never written into the repository,
and fails when a tracked text file holds a coordinate pair within 1 km of a clip or a timestamp within
6 hours of one. A failure names only file:line, the kind of match and its distance, never a value.

`data/uploads/` is not in git, so the guard runs where the clips are and is skipped, with its reason,
everywhere else (CI included). The scanner itself is tested on synthetic places and times, which run
everywhere. Photos' EXIF metadata is not read: today the folder holds only video clips.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
RADIUS_M = 1000.0
WINDOW = timedelta(hours=6)
PAIR_LINES = 6
# Package managers write package upload times into lockfiles; they cannot carry a capture time
TIMESTAMP_EXEMPT = {"uv.lock", "pnpm-lock.yaml"}

NUMBER = re.compile(
    r"(?P<hemi>\d+(?:\.\d+)?)\s*°?\s*(?P<side>[NSEW])(?![A-Za-z])"
    r"|(?P<signed>[+-]\d+(?:\.\d+)?)"
    r"|(?<![\d.+-])(?P<plain>\d+(?:\.\d+)?)"
)
ISO_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?"
)
EPOCH = re.compile(r"(?<![\d.])(1\d{9})(?:\d{3})?(?:\.\d+)?(?!\d)")
JULIAN_DATE = re.compile(r"(?<![\d.])(24\d{5}\.\d+)(?!\d)")
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
J2000_JULIAN_DATE = 2451545.0
EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Capture:
    place: tuple[float, float] | None
    time: datetime | None


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def numbers_on(line: str) -> list[float]:
    """Every decimal on the line that could be a coordinate, with hemisphere letters read as signs."""
    values = []
    for match in NUMBER.finditer(line):
        if match.group("hemi") is not None:
            value = float(match.group("hemi")) * (-1.0 if match.group("side") in "SW" else 1.0)
        else:
            value = float(match.group("signed") or match.group("plain"))
        if abs(value) <= 180.0:
            values.append(value)
    return values


def times_on(line: str) -> list[datetime]:
    """ISO 8601 date-times (no zone read as UTC), epoch seconds or milliseconds, and Julian dates."""
    found = []
    for match in ISO_DATETIME.finditer(line):
        year, month, day, hour, minute, second, zone = match.groups()
        offset = timedelta(0)
        if zone and zone != "Z":
            digits = zone[1:].replace(":", "")
            offset = (1 if zone[0] == "+" else -1) * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        try:
            local = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))
        except ValueError:
            continue
        found.append((local - offset).replace(tzinfo=timezone.utc))
    found += [datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc) for m in EPOCH.finditer(line)]
    found += [J2000 + timedelta(days=float(m.group(1)) - J2000_JULIAN_DATE) for m in JULIAN_DATE.finditer(line)]
    return found


def scan_text(text: str, captures: list[Capture], check_times: bool = True) -> list[tuple[int, str]]:
    """(line number, what matched and how close) for each line that holds a capture place or time."""
    lines = text.splitlines()
    places = [c.place for c in captures if c.place is not None]
    times = [c.time for c in captures if c.time is not None]
    hits: set[tuple[int, str]] = set()

    # Keep only numbers within about 2 km of some clip in one axis, so pairing stays cheap on long lines
    numbers = [numbers_on(line) for line in lines] if places else []
    lon_tolerance = [0.02 / max(math.cos(math.radians(p[0])), 0.05) for p in places]
    near_lat = [[v for v in row if any(abs(v - p[0]) < 0.02 for p in places)] for row in numbers]
    near_lon = [
        [v for v in row if any(abs(v - p[1]) < tol for p, tol in zip(places, lon_tolerance))] for row in numbers
    ]
    for i, lats in enumerate(near_lat):
        for j in range(max(0, i - PAIR_LINES), min(len(lines), i + PAIR_LINES + 1)):
            for lat in lats:
                for lon in near_lon[j]:
                    metres = min(haversine_m((lat, lon), place) for place in places)
                    if metres <= RADIUS_M:
                        hits.add((i + 1, f"coordinate {metres:.0f} m from a clip position"))

    if check_times and times:
        for i, line in enumerate(lines):
            for moment in times_on(line):
                gap = min(abs(moment - t) for t in times)
                if gap <= WINDOW:
                    hits.add((i + 1, f"timestamp {gap / timedelta(hours=1):.2f} h from a clip time"))
    return sorted(hits)


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
SYNTHETIC = [Capture(place=(30.0, -40.0), time=datetime(2030, 11, 1, 22, 32, tzinfo=timezone.utc))]


def destination(place: tuple[float, float], metres: float, bearing_deg: float) -> tuple[float, float]:
    """The point `metres` from `place` along `bearing_deg` on a spherical Earth."""
    lat1, lon1, bearing = math.radians(place[0]), math.radians(place[1]), math.radians(bearing_deg)
    angle = metres / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(angle) + math.cos(lat1) * math.sin(angle) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angle) * math.cos(lat1), math.cos(angle) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


def test_a_planted_place_is_reported_by_line_and_distance_only() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 400.0, 45.0)
    text = f'{{\n  "title": "x",\n  "latDeg": {lat:.6f},\n  "lonDeg": {lon:.6f}\n}}'
    hits = scan_text(text, SYNTHETIC)
    assert [line for line, _ in hits] == [3]
    assert hits[0][1] == "coordinate 400 m from a clip position"
    assert all(f"{v:.2f}" not in what for v in (lat, lon) for _, what in hits)


@pytest.mark.parametrize(
    "line",
    ["+30.0036-040.0021/", "30.0036N, 40.0021W", "30.0036° N 40.0021° W", "lat=30.0036 lon=-40.0021"],
)
def test_iso6709_hemisphere_and_signed_forms_are_read(line: str) -> None:
    expected = f"coordinate {haversine_m((30.0036, -40.0021), SYNTHETIC[0].place):.0f} m from a clip position"
    assert scan_text(line, SYNTHETIC) == [(1, expected)]


def test_a_place_more_than_a_kilometre_away_or_split_too_far_is_not_reported() -> None:
    lat, lon = destination(SYNTHETIC[0].place, 1500.0, 200.0)
    assert scan_text(f"{lat:.6f}, {lon:.6f}", SYNTHETIC) == []
    near_lat, near_lon = destination(SYNTHETIC[0].place, 100.0, 10.0)
    far_apart = "\n".join([f"{near_lat:.6f}", *["filler"] * PAIR_LINES, f"{near_lon:.6f}"])
    assert scan_text(far_apart, SYNTHETIC) == []
    assert scan_text("\n".join([f"{near_lat:.6f}", "lone latitude without a longitude"]), SYNTHETIC) == []


@pytest.mark.parametrize(
    "line",
    [
        "2030-11-01T20:00:00Z",
        "2030-11-01T21:00:00+0100",
        "2030-11-01 20:00",
        "1919793600",
        "1919793600000",
        "2462807.33333",
    ],
)
def test_iso_epoch_and_julian_timestamps_within_six_hours_are_reported(line: str) -> None:
    # Each spelling is 2030-11-01T20:00Z, 2 h 32 min before the synthetic capture time
    assert scan_text(line, SYNTHETIC) == [(1, "timestamp 2.53 h from a clip time")]


def test_times_beyond_six_hours_bare_dates_and_exempt_files_are_not_reported() -> None:
    assert scan_text("2030-11-02T05:00:00Z", SYNTHETIC) == []
    assert scan_text("released 2030-11-01", SYNTHETIC) == []
    assert scan_text("2030-11-01T22:32:00Z", SYNTHETIC, check_times=False) == []
    assert all((REPO_ROOT / name).is_file() for name in TIMESTAMP_EXEMPT)


@settings(deadline=None)
@given(metres=st.floats(0.0, 3000.0), bearing=st.floats(0.0, 360.0), lines_apart=st.integers(0, 10))
@example(metres=999.0, bearing=90.0, lines_apart=0)
@example(metres=1001.0, bearing=90.0, lines_apart=0)
@example(metres=10.0, bearing=0.0, lines_apart=PAIR_LINES)
@example(metres=10.0, bearing=0.0, lines_apart=PAIR_LINES + 1)
def test_pbt_a_place_is_reported_exactly_when_within_a_kilometre_and_paired(
    metres: float, bearing: float, lines_apart: int
) -> None:
    lat, lon = destination(SYNTHETIC[0].place, metres, bearing)
    text = "\n".join([f"lat {lat:.6f}", *["-"] * (lines_apart - 1), f"lon {lon:.6f}"]) if lines_apart else f"{lat:.6f} {lon:.6f}"
    planted = haversine_m((round(lat, 6), round(lon, 6)), SYNTHETIC[0].place)
    reported = scan_text(text, SYNTHETIC) != []
    assert reported == (planted <= RADIUS_M and lines_apart <= PAIR_LINES)


@settings(deadline=None)
@given(minutes=st.integers(-12 * 60, 12 * 60))
@example(minutes=6 * 60)
@example(minutes=6 * 60 + 1)
@example(minutes=-6 * 60 - 1)
def test_pbt_a_time_is_reported_exactly_when_within_six_hours(minutes: int) -> None:
    moment = SYNTHETIC[0].time + timedelta(minutes=minutes)
    reported = scan_text(moment.strftime("%Y-%m-%dT%H:%M:%SZ"), SYNTHETIC) != []
    assert reported == (abs(minutes) <= 6 * 60)


def test_owner_media_marker_skips_with_its_reason_only_where_a_folder_is_absent(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "data" / "uploads").mkdir(parents=True)
    (tmp_path / "tests" / "test_marked.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.owner_media\n"
        "def test_present():\n    pass\n\n"
        "@pytest.mark.owner_media('data/artifacts')\n"
        "def test_absent():\n    pass\n"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-2000:]
    assert "1 passed, 1 skipped" in run.stdout
    assert "needs the owner's media in data/artifacts, which is not in git" in run.stdout


def test_no_unexpected_skips_fails_every_skip_outside_the_owner_media_marker(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "tests" / "test_kinds.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.owner_media('data/artifacts')\n"
        "def test_owner_media_absent():\n    pass\n\n"
        "def test_skips_itself():\n    pytest.skip('not ready')\n\n"
        "@pytest.mark.skipif(True, reason='switched off')\n"
        "def test_skipif():\n    pass\n\n"
        "@pytest.mark.xfail(reason='a known gap')\n"
        "def test_xfail():\n    assert False\n"
    )
    (tmp_path / "tests" / "test_module_skip.py").write_text(
        "import pytest\n\npytest.skip('the whole module', allow_module_level=True)\n"
    )

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-rs", "-p", "no:cacheprovider", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    relaxed = run("tests")
    assert relaxed.returncode == 0, relaxed.stdout[-2000:]
    assert "4 skipped, 1 xfailed" in relaxed.stdout

    # A skip in the test body fails; a skipif fires during setup, so pytest reports it as an error
    strict = run("--no-unexpected-skips", "tests/test_kinds.py")
    assert strict.returncode == 1, strict.stdout[-2000:]
    assert "1 failed, 1 skipped, 1 xfailed, 1 error" in strict.stdout
    assert strict.stdout.count("only tests marked owner_media may skip") >= 2

    strict_module = run("--no-unexpected-skips", "tests/test_module_skip.py")
    assert strict_module.returncode != 0, strict_module.stdout[-2000:]
    assert "1 error" in strict_module.stdout
    assert "skipped at collection" in strict_module.stdout


@pytest.mark.owner_media
def test_no_tracked_file_holds_a_capture_place_or_time_from_the_owners_clips() -> None:
    if shutil.which("ffprobe") is None:
        pytest.fail("ffprobe is needed to read the clips' GPS positions and capture times")
    captures = read_captures(OWNER_MEDIA)
    if not any(c.place or c.time for c in captures):
        pytest.fail(f"no clip in data/uploads carries a GPS position or a capture time ({len(captures)} read)")
    findings = [
        f"{rel}:{line}: {what}"
        for rel, text in tracked_text_files()
        for line, what in scan_text(text, captures, check_times=rel not in TIMESTAMP_EXEMPT)
    ]
    assert not findings, (
        f"{len(findings)} tracked line(s) hold a place or time from the owner's clips; "
        "replace them with synthetic values:\n" + "\n".join(findings)
    )
