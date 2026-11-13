"""Every body, against JPL Horizons: at every stored instant the error is at most the uncertainty the finding states.

tests/fixtures/horizons_ephemeris.json.gz (one JSON document, gzip-compressed) holds JPL Horizons observer tables
(DE441), fetched once by
scripts/fetch_horizons_ephemeris_checks.py, with each request's URL, retrieval time, the SHA-256 of the answer's
text and the text itself: astrometric RA and Dec, airless apparent azimuth and elevation and range at four
open-ocean places at sea level, refracted azimuth and elevation at the first, and astrometric RA and Dec and range
from the Earth's centre, at synthetic instants from 1800 to 2050 (an even spread, instants low over each place's
horizon, and for the geocentric Moon the eight instants where the six-term Moon used before was furthest off).

Covers:
- Every stored answer is the text received (its SHA-256), from the Horizons API, for its body, place and frame; every
  body of the ephemeris has its four places, its refracted table and its geocentric table, and every requested
  instant has its row
- For every body and every stored instant: the angle between the finding's RA and Dec and Horizons' astrometric RA and
  Dec, and (from a place) between its altitude and azimuth and Horizons' airless apparent ones, is at most the
  finding's `uncertainty_arcsec`, and Horizons' range lies inside its distance bounds (the property of the lead's
  decision 2). From a place, the default, the Moon of main fails it at its first stored instant (it was off by up to
  4,603 arcsec against the 1,300 it stated); from the Earth's centre, at the first of the instants where its six-term
  Moon was furthest off
- The guard against a second parallax shift: for an observer on the ground, the Moon's altitude and azimuth from the
  default call (its record's frame "topocentric") are Horizons' observer-site values within the stated uncertainty,
  and the same record lowered once more by `moon_parallax_shift_arcsec` misses them by more than it, at every stored
  instant (by about 0.9 degrees)
- Asked for from the Earth's centre, every body's stated uncertainty also holds the place's parallax: its altitude and
  azimuth are within it of Horizons' from each place, and Horizons' range from the place is inside its distance
  bounds (the Sun's and the planets' bounds always held the observer's offset; the Moon's 1,300 arcsec did not hold
  its parallax, up to 4,603 arcsec on main)
- Refraction: the lift the finding states (Saemundsson at 1010 hPa and 10 degrees C) is within 10 arcsec of Horizons'
  own, at every stored instant from 1800 to 2050, horizon included; Horizons calls its refraction good to under 10
  arcsec (ssd.jpl.nasa.gov/horizons/manual.html, read at source 2026-11-11)
- The fetch script, with no request leaving the tests: its plan is the stored requests; --verify rebuilds the fixture
  from a cache of its own answers, refuses an empty cache and finds a changed character; without --verify it writes
  the stored text from a full cache; an answer is kept as received (in a cache folder it makes) and one with no table
  refused; a cache file is taken only when it holds that URL's whole answer; no URL passes 1,800 characters; its
  search for low instants ends, refused, when its candidates run out; run as a program, it checks the fixture
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import urllib.parse

import numpy as np
import pytest

from services.science import ephemeris
from services.science.catalog import icrs_to_altaz

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "horizons_ephemeris.json.gz"
STORED = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
REQUESTS = STORED["requests"]
# The open-ocean places the script asks for (latitude, east longitude), at sea level
SITES = {
    "north-atlantic": (30.0, -40.0),
    "south-pacific": (-45.0, -130.0),
    "indian-ocean": (-20.0, 75.0),
    "southern-ocean": (-60.0, 0.0),
}
# Horizons: refracted coordinates "should be accurate to < 10 arcsec" (manual, read at source 2026-11-11)
HORIZONS_REFRACTION_ARCSEC = 10.0
MONTHS = {name: number for number, name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _moment(cell: str) -> str:
    date, clock = cell.strip().split(" ")
    year, month, day = date.split("-")
    hour, minute, second = clock.split(":")
    when = datetime(int(year), MONTHS[month], int(day), int(hour), int(minute), int(float(second)), tzinfo=timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(request: dict) -> list[dict]:
    """The rows of a stored observer table: the moment and the columns by kind (ra, dec, az, el, delta)."""
    result = json.loads(request["response"])["result"]
    head, rest = result.split("$$SOE", 1)
    header = [line for line in head.splitlines() if line.strip() and not line.startswith("*")][-1]
    names = [cell.strip() for cell in header.split(",")]
    rows = []
    for line in rest.split("$$EOE", 1)[0].strip().splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        row: dict = {"moment": _moment(cells[0])}
        for name, value in zip(names, cells):
            key = ("ra" if name.startswith("R.A.") else "dec" if name.startswith("DEC") else "az" if name.startswith("Azi")
                   else "el" if name.startswith("Elev") else "delta" if name == "delta" else None)
            if key is not None:
                row[key] = float(value)
        rows.append(row)
    return rows


def _query(request: dict) -> dict[str, str]:
    return {key: values[0] for key, values in urllib.parse.parse_qs(urllib.parse.urlparse(request["url"]).query).items()}


def _unit(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])


def _enu(alt_deg: float, az_deg: float) -> np.ndarray:
    alt, az = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(alt) * math.sin(az), math.cos(alt) * math.cos(az), math.sin(alt)])


def _sep_arcsec(u: np.ndarray, v: np.ndarray) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(u, v))), float(u @ v))) * 3600.0


BY_BODY: dict[str, list[dict]] = defaultdict(list)
for _request in REQUESTS:
    BY_BODY[_request["body"]].append(_request)


def test_every_stored_answer_is_the_text_received_for_its_body_place_and_frame() -> None:
    assert STORED["format"] == "albedian.horizons-ephemeris-checks"
    assert STORED["script"] == "scripts/fetch_horizons_ephemeris_checks.py"
    assert (FIXTURE.parents[2] / STORED["script"]).is_file()
    ids = {body.name: body.horizons_id for body in ephemeris.BODIES}
    assert sorted(BY_BODY) == sorted(ids)
    assert ephemeris.TOPOCENTRIC_BY_DEFAULT is True
    for request in REQUESTS:
        assert hashlib.sha256(request["response"].encode("utf-8")).hexdigest() == request["responseSha256"]
        assert request["url"].startswith("https://ssd.jpl.nasa.gov/api/horizons.api?")
        datetime.strptime(request["retrievedUtc"], "%Y-%m-%dT%H:%M:%SZ")
        query = _query(request)
        assert query["COMMAND"] == f"'{ids[request['body']]}'" and query["EPHEM_TYPE"] == "'OBSERVER'"
        assert query["TIME_TYPE"] == "'UT'" and query["REF_SYSTEM"] == "'ICRF'"
        if request["kind"] == "geocentric":
            assert query["CENTER"] == "'500@399'" and request["site"] is None
        else:
            lat, lon = SITES[request["site"]]
            assert (request["latDeg"], request["lonDeg"]) == (lat, lon)
            assert query["CENTER"] == "'coord@399'" and query["COORD_TYPE"] == "'GEODETIC'"
            assert query["SITE_COORD"] == f"'{lon:g},{lat:g},0'"
            assert query["APPARENT"] == ("'REFRACTED'" if request["kind"] == "topocentric-refracted" else "'AIRLESS'")
        # Every requested instant came back
        asked = sorted(m.strip("'").replace(" ", "T") + "Z" for m in query["TLIST"].split("' '"))
        assert [row["moment"] for row in _rows(request)] == asked
    for body, requests in BY_BODY.items():
        kinds = sorted((r["kind"], r["site"] or "") for r in requests)
        assert kinds == sorted(
            [("topocentric", site) for site in SITES] + [("topocentric-refracted", "north-atlantic"), ("geocentric", "")]
        ), body


@pytest.mark.parametrize("body", sorted(BY_BODY))
def test_every_body_is_within_its_stated_uncertainty_at_every_stored_instant(body: str) -> None:
    checked = 0
    # From a place first: the default frame (TOPOCENTRIC_BY_DEFAULT, checked above), called as every caller that gives
    # a place and no frame calls it
    for request in sorted(BY_BODY[body], key=lambda r: r["kind"] != "topocentric"):
        if request["kind"] == "topocentric-refracted":
            continue
        topocentric = request["kind"] == "topocentric"
        lat, lon = SITES[request["site"]] if topocentric else SITES["north-atlantic"]
        for row in _rows(request):
            moment = row["moment"]
            if topocentric:
                (finding,) = ephemeris.compute_solar_system_ephemerides(moment, lat, lon, bodies=(body,))
            else:
                (finding,) = ephemeris.compute_solar_system_ephemerides(
                    moment, lat, lon, bodies=(body,), topocentric=False
                )
            direction = _sep_arcsec(_unit(finding.ra_deg, finding.dec_deg), _unit(row["ra"], row["dec"]))
            assert direction <= finding.uncertainty_arcsec, (request["kind"], request["site"], moment, direction)
            if topocentric:
                altaz = _sep_arcsec(_enu(finding.alt_deg, finding.az_deg), _enu(row["el"], row["az"]))
                assert altaz <= finding.uncertainty_arcsec, (request["site"], moment, altaz, finding.uncertainty_arcsec)
            assert finding.distance_lower_au <= row["delta"] <= finding.distance_upper_au, (request["kind"], moment)
            checked += 1
    assert checked == sum(len(_rows(r)) for r in BY_BODY[body] if r["kind"] != "topocentric-refracted")


def test_the_moon_from_a_place_is_horizons_observer_site_moon_and_a_second_shift_would_miss_it() -> None:
    checked = 0
    for request in BY_BODY["Moon"]:
        if request["kind"] != "topocentric":
            continue
        lat, lon = SITES[request["site"]]
        for row in _rows(request):
            (moon,) = ephemeris.compute_solar_system_ephemerides(row["moment"], lat, lon, bodies=("Moon",))
            horizons = _enu(row["el"], row["az"])
            altaz = _sep_arcsec(_enu(moon.alt_deg, moon.az_deg), horizons)
            assert altaz <= moon.uncertainty_arcsec, (request["site"], row["moment"], altaz)
            assert moon.frame == "topocentric"
            assert any("(topocentric): its parallax is applied" in text for text in moon.limitations)
            # What a caller that lowers the record again would get
            shift_deg = ephemeris.moon_parallax_shift_arcsec(moon.distance_au, moon.alt_deg)[1] / 3600.0
            twice = _sep_arcsec(_enu(moon.alt_deg - shift_deg, moon.az_deg), horizons)
            assert twice > moon.uncertainty_arcsec, (request["site"], row["moment"], twice)
            checked += 1
    assert checked == 4 * 38


@pytest.mark.parametrize("body", sorted(BY_BODY))
def test_from_the_earths_centre_every_body_holds_for_an_observer_at_the_place(body: str) -> None:
    checked = 0
    for request in BY_BODY[body]:
        if request["kind"] != "topocentric":
            continue
        lat, lon = SITES[request["site"]]
        for row in _rows(request):
            (finding,) = ephemeris.compute_solar_system_ephemerides(
                row["moment"], lat, lon, bodies=(body,), topocentric=False
            )
            altaz = _sep_arcsec(_enu(finding.alt_deg, finding.az_deg), _enu(row["el"], row["az"]))
            assert altaz <= finding.uncertainty_arcsec, (request["site"], row["moment"], altaz)
            assert finding.distance_lower_au <= row["delta"] <= finding.distance_upper_au, (request["site"], row["moment"])
            assert finding.frame == "geocentric"
            checked += 1
    assert checked == sum(len(_rows(r)) for r in BY_BODY[body] if r["kind"] == "topocentric")


def test_the_stated_refraction_is_within_horizons_own_at_every_stored_instant() -> None:
    from services.science.catalog import refraction_deg

    checked = 0
    for request in REQUESTS:
        if request["kind"] != "topocentric-refracted":
            continue
        (airless,) = [r for r in BY_BODY[request["body"]] if r["kind"] == "topocentric" and r["site"] == request["site"]]
        airless_el = {row["moment"]: row["el"] for row in _rows(airless)}
        lat, lon = SITES[request["site"]]
        for row in _rows(request):
            (finding,) = ephemeris.compute_solar_system_ephemerides(row["moment"], lat, lon, bodies=(request["body"],))
            ours = (finding.refracted_alt_deg - finding.alt_deg) * 3600.0
            theirs = (row["el"] - airless_el[row["moment"]]) * 3600.0
            assert abs(ours - theirs) <= HORIZONS_REFRACTION_ARCSEC, (request["body"], row["moment"], ours, theirs)
            # The transform callers use lifts the same direction by the same amount
            refracted, _ = icrs_to_altaz(finding.ra_deg, finding.dec_deg, lat, lon, row["moment"], refracted=True)
            assert refracted == pytest.approx(finding.alt_deg + refraction_deg(finding.alt_deg), abs=1e-9)
            assert finding.refracted_alt_deg == pytest.approx(refracted, abs=1e-9)
            checked += 1
    assert checked == 9 * 38


def _script():
    import importlib.util

    path = FIXTURE.parents[2] / "scripts" / "fetch_horizons_ephemeris_checks.py"
    spec = importlib.util.spec_from_file_location("fetch_horizons_ephemeris_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cache_from_fixture(folder: Path, change: int | None = None) -> None:
    """A cache like the script's, made from the fixture's own answers (one answer's text changed if asked)."""
    for index, request in enumerate(REQUESTS):
        text = request["response"] + (" " if index == change else "")
        entry = {"url": request["url"], "retrievedUtc": request["retrievedUtc"], "text": text}
        (folder / f"{hashlib.sha256(request['url'].encode()).hexdigest()[:24]}.json").write_text(json.dumps(entry))


def test_the_fetch_script_plans_exactly_the_stored_requests() -> None:
    """The script's plan today (its low instants found with this ephemeris) is the stored requests, in order."""
    assert [r["url"] for r in _script().plan()] == [r["url"] for r in REQUESTS]


def test_the_fetch_script_rebuilds_the_fixture_offline_from_its_stored_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """--verify with the default cache made from the fixture's own answers rebuilds the fixture byte for byte; with an
    empty cache it refuses (it makes no request); with one answer changed by a character it finds the difference."""
    script = _script()
    full, empty, changed = tmp_path / "full", tmp_path / "empty", tmp_path / "changed"
    for folder in (full, empty, changed):
        folder.mkdir()
    _cache_from_fixture(full)
    _cache_from_fixture(changed, change=7)
    monkeypatch.setattr(script, "DEFAULT_CACHE", full)
    monkeypatch.setattr("sys.argv", ["fetch", "--verify"])
    script.main()
    assert "matches the cache: 54 requests" in capsys.readouterr().out
    for folder, words in ((empty, "not cached"), (changed, "differs from the fixture")):
        monkeypatch.setattr("sys.argv", ["fetch", "--verify", "--cache", str(folder)])
        with pytest.raises(SystemExit, match=words):
            script.main()


class _Answer:
    """What urllib's opener hands back, holding a fixed text: no request leaves the test."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.text.encode("utf-8")


def test_the_fetch_script_keeps_an_answer_as_received_and_refuses_one_with_no_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL not cached is asked for once (through a stand-in opener), its answer kept and cached exactly as received
    with its retrieval time in a cache folder it makes, then read from the cache; an answer with no table is refused
    and not cached."""
    script = _script()
    cache = tmp_path / "not" / "made"
    answers = {"table": json.dumps({"result": "header\n$$SOE\n a row\n$$EOE\n"}),
               "none": json.dumps({"result": "No ephemeris for this target"})}
    asked: list[str] = []
    pauses: list[float] = []

    def opener(request: object, timeout: float) -> _Answer:
        asked.append(request.full_url)
        assert timeout == 120 and request.get_header("User-agent") == script.USER_AGENT
        return _Answer(answers[request.full_url.rsplit("=", 1)[-1]])

    monkeypatch.setattr(script.urllib.request, "urlopen", opener)
    monkeypatch.setattr(script.time, "sleep", pauses.append)
    table_url, none_url = script.query_url({"WHICH": "table"}), script.query_url({"WHICH": "none"})
    entry = script.fetch(table_url, cache, offline=False)
    assert entry["url"] == table_url and entry["text"] == answers["table"]
    datetime.strptime(entry["retrievedUtc"], "%Y-%m-%dT%H:%M:%SZ")
    assert asked == [table_url] and pauses == [script.PAUSE_S]
    assert script.fetch(table_url, cache, offline=True) == entry
    assert asked == [table_url]
    with pytest.raises(SystemExit, match="no table"):
        script.fetch(none_url, cache, offline=False)
    with pytest.raises(SystemExit, match="not cached"):
        script.fetch(none_url, cache, offline=True)


def test_the_fetch_script_takes_from_its_cache_only_that_urls_whole_answer(tmp_path: Path) -> None:
    """A cache file under a URL's name is taken only when it holds that URL, a retrieval time and a text: one that holds
    another URL's answer, or lacks either field, is not, and offline that URL is refused as not cached."""
    script = _script()
    url = script.query_url({"WHICH": "table"})
    path = tmp_path / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.json"
    whole = {"url": url, "retrievedUtc": "2026-11-11T00:00:00Z", "text": "{}"}
    path.write_text(json.dumps(whole))
    assert script.fetch(url, tmp_path, offline=True) == whole
    for broken in ({**whole, "url": script.query_url({"WHICH": "other"})},
                   {key: value for key, value in whole.items() if key != "retrievedUtc"},
                   {key: value for key, value in whole.items() if key != "text"}):
        path.write_text(json.dumps(broken))
        with pytest.raises(SystemExit, match="not cached"):
            script.fetch(url, tmp_path, offline=True)


def test_the_fetch_script_asks_for_no_url_longer_than_the_api_answered() -> None:
    """A request URL of exactly 1,800 characters is made and one of 1,801 refused (the API answered 502 to 1,900 and
    200 to 1,825); every stored URL is within it."""
    script = _script()
    head = len(script.query_url({"X": ""}))
    assert len(script.query_url({"X": "a" * (1800 - head)})) == 1800
    with pytest.raises(SystemExit, match="of 1801 characters"):
        script.query_url({"X": "a" * (1801 - head)})
    assert max(len(request["url"]) for request in REQUESTS) <= 1800


def test_the_fetch_script_writes_the_stored_fixture_from_a_full_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --verify the script plans afresh, takes every answer from the cache (a request fails the test) and writes
    the fixture: the stored document's text exactly, gzip with its header's time 0."""
    script = _script()
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache_from_fixture(cache)
    out = tmp_path / "tests" / "fixtures" / "horizons_ephemeris.json.gz"
    out.parent.mkdir(parents=True)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "OUT", out)
    monkeypatch.setattr(script, "DEFAULT_CACHE", cache)
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("a request was made"))
    monkeypatch.setattr("sys.argv", ["fetch"])
    script.main()
    assert "wrote tests/fixtures/horizons_ephemeris.json.gz: 54 requests" in capsys.readouterr().out
    written = out.read_bytes()
    assert written[4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(written) == gzip.decompress(FIXTURE.read_bytes())


def test_the_fetch_script_run_as_a_program_checks_the_fixture(tmp_path: Path) -> None:
    """`python scripts/fetch_horizons_ephemeris_checks.py --verify --cache <a cache of the fixture's own answers>`, a
    child process bounded at 120 seconds, finds the fixture whole and exits 0."""
    _cache_from_fixture(tmp_path)
    script = FIXTURE.parents[2] / "scripts" / "fetch_horizons_ephemeris_checks.py"
    run = subprocess.run([sys.executable, str(script), "--verify", "--cache", str(tmp_path)], capture_output=True,
                         text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:]
    assert "matches the cache: 54 requests" in run.stdout


def test_the_fetch_scripts_search_for_low_instants_always_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no altitude in its window the search for low instants is refused after its candidates, never run on; the
    plan test above holds that the script's own 999 are enough."""
    script = _script()
    monkeypatch.setattr(script, "LOW_ALT_DEG", (90.5, 91.0))
    monkeypatch.setattr(script, "LOW_CANDIDATES", 12)
    with pytest.raises(SystemExit, match="Moon: fewer than 6 low instants among 12 candidates"):
        script.low_instants("Moon", 0, 30.0, -40.0)
