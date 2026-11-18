"""The Moon's phase (services/science/ephemeris.py `moon_phase`, `phase_name`), and radiometry.py's phase on it.

Covers:
- Against JPL Horizons' stored rows (tests/fixtures/horizons_phase_rts.json.gz: the Sun-target-observer angle and the
  illuminated fraction, from four open-ocean places and the Earth's centre at synthetic instants), within the stated
  bound plus what Horizons says its angle carries (down-leg aberration, at most the aberration constant) and its
  printing
- PBT: the stated bound holds when the Moon's and the Sun's vectors move by their stated displacements; the
  topocentric phase angle differs from the geocentric by at most the angle the place's offset subtends at the Moon
- The phase names' sector edges from one table; a place is both coordinates or neither; the ephemeris' span
- radiometry.py's phase is moon_phase's, geocentric, with its keys as before
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import json
import math
from pathlib import Path

import erfa
from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from services.science import ephemeris
from services.science.catalog import RIGID_ABERRATION_MAX_ARCSEC, unit_vector_to_ra_dec
from services.science.ephemeris import (
    BODY_BY_NAME,
    MOON_PHASE_NAMES,
    PHASE_METHOD,
    PHASE_SECTOR_DEG,
    EphemerisOutOfRange,
    moon_phase,
    phase_name,
)
from services.science.radiometry import compute_lunar_phase_and_illumination

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "horizons_phase_rts.json.gz"
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
# Horizons prints the angle to 4 decimals of a degree and the illuminated fraction to 5 decimals of a percent
STO_PRINT_DEG = 0.5e-4
ILLU_PRINT = 0.5e-5 / 100.0
MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0


def _iso(text: str) -> str:
    date, clock = text.strip().split()
    year, month, day = date.split("-")
    return f"{year}-{MONTHS[month]:02d}-{day}T{clock[:8]}Z"


def _rows(request: dict) -> list[list[str]]:
    result = json.loads(request["response"])["result"]
    table = result[result.index("$$SOE") + 5: result.index("$$EOE")]
    return [[cell.strip() for cell in line.split(",")] for line in table.strip().splitlines()]


def _requests(kind_prefix: str, body: str) -> list[dict]:
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
    return [r for r in data["requests"] if r["kind"].startswith(kind_prefix) and r["body"] == body]


def test_the_phase_matches_horizons_within_its_bound() -> None:
    """Every stored Moon row: the phase angle within the stated bound plus the down-leg aberration Horizons' angle
    carries (its manual: 'slightly different at the few arcsecond level in that it includes stellar aberration'; at
    most the aberration constant) plus its printing; the illuminated fraction within the bound that angle gives."""
    checked = 0
    for request in _requests("phase", "Moon"):
        lat, lon = request["latDeg"], request["lonDeg"]
        for row in _rows(request):
            moment = _iso(row[0])
            phase = moon_phase(moment, lat, lon) if lat is not None else moon_phase(moment)
            assert phase.frame == ("topocentric" if lat is not None else "geocentric")
            allowed = phase.phase_angle_bound_deg + RIGID_ABERRATION_MAX_ARCSEC / 3600.0 + STO_PRINT_DEG
            assert abs(phase.phase_angle_deg - float(row[7])) <= allowed, (moment, phase.phase_angle_deg, row[7])
            a, d = math.radians(phase.phase_angle_deg), math.radians(allowed)
            fraction_allowed = (abs(math.sin(a)) * d + d * d / 2.0) / 2.0 + ILLU_PRINT
            assert abs(phase.illuminated_fraction - float(row[6]) / 100.0) <= fraction_allowed, (moment, row[6])
            checked += 1
    assert checked == 5 * 32


def _displace(vector: np.ndarray, across_km: float, along_km: float, angle: float) -> np.ndarray:
    """`vector` (au) moved `across_km` across its line at position angle `angle` and `along_km` along it."""
    unit = vector / float(np.linalg.norm(vector))
    side = np.cross(unit, [0.0, 0.0, 1.0])
    side /= float(np.linalg.norm(side))
    up = np.cross(unit, side)
    across = (math.cos(angle) * side + math.sin(angle) * up) * across_km
    return vector + (across + unit * along_km) / ephemeris.AU_KM


moment_offsets = st.floats(min_value=0.0, max_value=250.0 * 365.25 * 86400.0 - 86400.0)


@settings(max_examples=60, deadline=None)
@given(offset=moment_offsets, lat=st.floats(-89.0, 89.0), lon=st.floats(-180.0, 180.0),
       moon_angle=st.floats(0.0, 2.0 * math.pi), sun_angle=st.floats(0.0, 2.0 * math.pi),
       moon_sign=st.sampled_from([-1.0, 1.0]), sun_sign=st.sampled_from([-1.0, 1.0]))
@example(offset=7.3e9, lat=30.0, lon=-40.0, moon_angle=0.0, sun_angle=math.pi, moon_sign=1.0,
         sun_sign=-1.0)
@example(offset=6845460933.0, lat=84.3319920364251, lon=73.44833123882569, moon_angle=0.0, sun_angle=math.pi,
         moon_sign=-1.0, sun_sign=-1.0)
def test_pbt_the_phase_bound_holds_when_both_vectors_move_by_their_stated_displacements(
    offset: float, lat: float, lon: float, moon_angle: float, sun_angle: float, moon_sign: float, sun_sign: float,
) -> None:
    moment = (datetime(1800, 1, 2, tzinfo=timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    phase = moon_phase(moment, lat, lon)
    scales, geocentre, observer, _ = ephemeris._observer(moment, lat, lon)
    moon, _ = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, observer)
    sun, _ = ephemeris._astrometric_au(BODY_BY_NAME["Sun"], scales.tt, observer)
    moon_geo = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, geocentre)[0]
    m_across, m_along = ephemeris._vector_bound(BODY_BY_NAME["Moon"], scales, moon, None, moon_geo, observer, True)
    s_across, s_along = ephemeris._vector_bound(BODY_BY_NAME["Sun"], scales, sun, None, sun, observer, True)
    moved_moon = _displace(moon, m_across, moon_sign * m_along, moon_angle)
    moved_sun = _displace(sun, s_across, sun_sign * s_along, sun_angle)
    moved = ephemeris._angle_deg(moved_sun - moved_moon, -moved_moon)
    assert abs(moved - phase.phase_angle_deg) <= phase.phase_angle_bound_deg, (moved, phase.phase_angle_deg)
    moved_fraction = (1.0 + math.cos(math.radians(moved))) / 2.0
    assert abs(moved_fraction - phase.illuminated_fraction) <= phase.illuminated_fraction_bound
    lon_m, _ = erfa.eqec06(*scales.tt, *(math.radians(v) for v in unit_vector_to_ra_dec(moved_moon)))
    lon_s, _ = erfa.eqec06(*scales.tt, *(math.radians(v) for v in unit_vector_to_ra_dec(moved_sun)))
    moved_synodic = (math.degrees(float(lon_m)) - math.degrees(float(lon_s))) % 360.0
    synodic_diff = min(
        (moved_synodic - phase.synodic_angle_deg) % 360.0,
        (phase.synodic_angle_deg - moved_synodic) % 360.0,
    )
    assert synodic_diff <= phase.synodic_angle_bound_deg, (synodic_diff, phase.synodic_angle_bound_deg)


def test_the_worst_ecliptic_displacement_stays_within_the_synodic_bound() -> None:
    """When the Moon and the Sun both step toward the observer by their distance bounds and across their lines along
    opposite directions of ecliptic longitude, the synodic angle's shift stays within `synodic_angle_bound_deg`."""
    moment, lat, lon = "2016-12-13T09:22:33Z", 84.3319920364251, 73.44833123882569
    phase = moon_phase(moment, lat, lon)
    scales, geocentre, observer, _ = ephemeris._observer(moment, lat, lon)
    moon, _ = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, observer)
    sun, _ = ephemeris._astrometric_au(BODY_BY_NAME["Sun"], scales.tt, observer)
    moon_geo = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, geocentre)[0]
    m_across, m_along = ephemeris._vector_bound(BODY_BY_NAME["Moon"], scales, moon, None, moon_geo, observer, True)
    s_across, s_along = ephemeris._vector_bound(BODY_BY_NAME["Sun"], scales, sun, None, sun, observer, True)

    def ecl_lon(vec: np.ndarray) -> float:
        ra_deg, dec_deg = unit_vector_to_ra_dec(vec)
        lon_rad, _ = erfa.eqec06(*scales.tt, math.radians(ra_deg), math.radians(dec_deg))
        return math.degrees(float(lon_rad))

    base_m, base_s = ecl_lon(moon), ecl_lon(sun)
    best_m = max(
        abs(((ecl_lon(_displace(moon, m_across, -m_along, float(a))) - base_m + 180.0) % 360.0) - 180.0)
        for a in np.linspace(0.0, 2.0 * math.pi, 360, endpoint=False)
    )
    best_s = max(
        abs(((ecl_lon(_displace(sun, s_across, -s_al, float(a))) - base_s + 180.0) % 360.0) - 180.0)
        for s_al in (s_along,)
        for a in np.linspace(0.0, 2.0 * math.pi, 360, endpoint=False)
    )
    assert best_m + best_s <= phase.synodic_angle_bound_deg


@settings(max_examples=60, deadline=None)
@given(offset=moment_offsets, lat=st.floats(-89.0, 89.0), lon=st.floats(-180.0, 180.0))
def test_pbt_the_topocentric_phase_differs_from_the_geocentric_by_at_most_the_places_offset_seen_from_the_moon(
    offset: float, lat: float, lon: float,
) -> None:
    """The observer's move from the Earth's centre to the place turns the Moon-to-observer direction by the angle that
    move subtends at the Moon, and the Moon-to-Sun direction by far less: the phase angles differ by at most that,
    each record states its frame, and the difference is not zero (computed, not assumed)."""
    moment = (datetime(1800, 1, 2, tzinfo=timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    topocentric, geocentric = moon_phase(moment, lat, lon), moon_phase(moment)
    assert (topocentric.frame, geocentric.frame) == ("topocentric", "geocentric")
    scales, geocentre, observer, _ = ephemeris._observer(moment, lat, lon)
    moon_geo = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, geocentre)[0]
    sun_geo = ephemeris._astrometric_au(BODY_BY_NAME["Sun"], scales.tt, geocentre)[0]
    offset_au = observer - geocentre
    subtended = ephemeris._angle_deg(moon_geo, moon_geo - offset_au)
    sun_turn = math.degrees(float(np.linalg.norm(offset_au)) / float(np.linalg.norm(sun_geo - moon_geo)))
    difference = abs(topocentric.phase_angle_deg - geocentric.phase_angle_deg)
    assert 0.0 < difference <= subtended + 2.0 * sun_turn, (difference, subtended)


def test_the_topocentric_phase_at_the_fixtures_moment_differs_by_the_computed_amount() -> None:
    topocentric, geocentric = moon_phase(MOMENT, LAT, LON), moon_phase(MOMENT)
    assert abs(topocentric.phase_angle_deg - geocentric.phase_angle_deg) == pytest.approx(0.604238, abs=1e-6)


# The sector edges, from one table: (synodic angle, bound, expected name or None). Every number is exact in binary,
# so each sum and remainder is exact and the inclusive edge is tested by construction, not by rounding
EDGE = PHASE_SECTOR_DEG / 2.0
NAME_EDGES = [
    (0.0, 0.5, "New Moon"),
    (EDGE - 0.75, 0.5, "New Moon"),
    (EDGE - 0.5, 0.5, None),
    (EDGE, 0.0, None),
    (EDGE + 0.5, 0.5, None),
    (EDGE + 0.75, 0.5, "Waxing Crescent"),
    (90.0, 0.5, "First Quarter"),
    (180.0, 0.5, "Full Moon"),
    (270.0, 0.5, "Last Quarter"),
    (360.0 - EDGE - 0.75, 0.5, "Waning Crescent"),
    (360.0 - EDGE - 0.5, 0.5, None),
    (360.0 - EDGE + 0.75, 0.5, "New Moon"),
    (359.75, 0.0, "New Moon"),
]


@pytest.mark.parametrize(("synodic", "bound", "expected"), NAME_EDGES)
def test_the_phase_names_at_each_sector_edge(synodic: float, bound: float, expected: str | None) -> None:
    assert phase_name(synodic, bound) == expected


def test_every_sector_has_its_name_in_order() -> None:
    assert [phase_name(45.0 * k, 0.0) for k in range(8)] == list(MOON_PHASE_NAMES)


SPAN_EDGES = [
    ("0001-01-01T00:00:00Z", False),
    ("1799-12-31T23:59:59Z", False),
    ("1800-01-01T00:00:00Z", True),
    ("2050-12-31T23:59:59Z", True),
    ("2051-01-01T00:00:00Z", False),
    ("9999-12-31T23:59:59Z", False),
]


@pytest.mark.parametrize(("moment", "accepted"), SPAN_EDGES)
def test_a_place_is_both_coordinates_or_neither_and_the_span_holds_at_each_edge(
    moment: str, accepted: bool,
) -> None:
    if not accepted:
        with pytest.raises(EphemerisOutOfRange):
            moon_phase(moment, LAT, LON)
        with pytest.raises(EphemerisOutOfRange):
            compute_lunar_phase_and_illumination(moment)
        return
    assert moon_phase(moment, LAT, LON).obs_utc_iso == moment
    assert compute_lunar_phase_and_illumination(moment)["observedAtUTC"] == moment


def test_a_single_coordinate_is_refused() -> None:
    with pytest.raises(ValueError, match="together, or neither"):
        moon_phase(MOMENT, LAT, None)
    with pytest.raises(ValueError, match="together, or neither"):
        moon_phase(MOMENT, None, LON)


@pytest.mark.parametrize("moment", [
    "1850-06-15T12:00:00Z",
    "1950-06-15T12:00:00Z",
    "2020-06-15T12:00:00Z",
    MOMENT,
])
@pytest.mark.parametrize("topocentric", [True, False])
def test_every_part_of_the_phase_bounds_equals_its_formula_at_fixed_cases(
    moment: str, topocentric: bool,
) -> None:
    """Held from above as well as below across eras and frames: `_place_bound_km`, `_vector_bound`, and each bound and
    limitation on `MoonPhase` equal their formulas, so none can be inflated silently."""
    got = moon_phase(moment, LAT, LON) if topocentric else moon_phase(moment)
    scales, geocentre, observer, is_topo = ephemeris._observer(
        moment, LAT if topocentric else None, LON if topocentric else None
    )
    assert is_topo is topocentric
    expected_place_km = (
        0.0
        if not topocentric
        else ephemeris.MAX_OBSERVER_HEIGHT_KM
        + ephemeris.EARTH_EQUATORIAL_RADIUS_KM
        * (ephemeris.EARTH_ROTATION_RAD_PER_S * scales.ut1_bound_s + ephemeris.POLAR_MOTION_BOUND_ARCSEC / ephemeris.ARCSEC_PER_RAD)
    )
    assert ephemeris._place_bound_km(scales, topocentric) == pytest.approx(expected_place_km, rel=1e-12, abs=1e-15)

    moon_body, sun_body = BODY_BY_NAME["Moon"], BODY_BY_NAME["Sun"]
    moon, _ = ephemeris._astrometric_au(moon_body, scales.tt, observer)
    sun, _ = ephemeris._astrometric_au(sun_body, scales.tt, observer)
    moon_geo = moon if not topocentric else ephemeris._astrometric_au(moon_body, scales.tt, geocentre)[0]

    for body, vec, geo in ((moon_body, moon, moon_geo), (sun_body, sun, sun)):
        theory_arcsec, theory_km = ephemeris._theory_bound(body, scales.tt, vec, None, geo)
        time_arcsec = ephemeris._time_scale_arcsec(body, scales.tt, vec, observer, scales.tt_bound_s)
        dist_km = float(np.linalg.norm(vec)) * ephemeris.AU_KM
        ac, al = ephemeris._vector_bound(body, scales, vec, None, geo, observer, topocentric)
        assert ac == pytest.approx((theory_arcsec + time_arcsec) / ephemeris.ARCSEC_PER_RAD * dist_km + expected_place_km, rel=1e-12)
        assert al == pytest.approx(theory_km + expected_place_km, rel=1e-12)

    m_ac, m_al = ephemeris._vector_bound(moon_body, scales, moon, None, moon_geo, observer, topocentric)
    s_ac, s_al = ephemeris._vector_bound(sun_body, scales, sun, None, sun, observer, topocentric)
    m_shift = math.hypot(m_ac, m_al) / ephemeris.AU_KM
    s_shift = math.hypot(s_ac, s_al) / ephemeris.AU_KM
    to_sun = sun - moon
    expected_phase = ephemeris._angle_deg(to_sun, -moon)
    expected_phase_bound = math.degrees(
        math.asin(m_shift / float(np.linalg.norm(moon))) + math.asin((s_shift + m_shift) / float(np.linalg.norm(to_sun)))
    )
    p_rad, b_rad = math.radians(expected_phase), math.radians(expected_phase_bound)
    expected_fraction = (1.0 + math.cos(p_rad)) / 2.0
    expected_fraction_bound = (abs(math.sin(p_rad)) * b_rad + b_rad * b_rad / 2.0) / 2.0

    _, lat_m = erfa.eqec06(*scales.tt, *(math.radians(v) for v in unit_vector_to_ra_dec(moon)))
    _, lat_s = erfa.eqec06(*scales.tt, *(math.radians(v) for v in unit_vector_to_ra_dec(sun)))
    m_km = float(np.linalg.norm(moon)) * ephemeris.AU_KM
    s_km = float(np.linalg.norm(sun)) * ephemeris.AU_KM
    sin_m = m_ac / math.hypot(m_ac, m_km - m_al)
    sin_s = s_ac / math.hypot(s_ac, s_km - s_al)
    expected_synodic_bound = math.degrees(
        math.asin(sin_m / math.cos(float(lat_m))) + math.asin(sin_s / math.cos(float(lat_s)))
    )

    assert got.phase_angle_deg == pytest.approx(expected_phase, rel=1e-12)
    assert got.phase_angle_bound_deg == pytest.approx(expected_phase_bound, rel=1e-12)
    assert got.illuminated_fraction == pytest.approx(expected_fraction, rel=1e-12)
    assert got.illuminated_fraction_bound == pytest.approx(expected_fraction_bound, rel=1e-12)
    assert got.synodic_angle_bound_deg == pytest.approx(expected_synodic_bound, rel=1e-12)
    assert got.elongation_deg == pytest.approx(ephemeris._angle_deg(moon, sun), rel=1e-12)
    assert got.method == PHASE_METHOD
    if topocentric:
        assert "given time and place with ERFA" in got.limitations[0]
        assert got.limitations[1] == "Seen from the given place at sea level (topocentric)."
    else:
        assert "given time with ERFA" in got.limitations[0]
        assert got.limitations[1] == "Seen from the Earth's centre (geocentric): no place was given."
    assert f"{got.phase_angle_bound_deg * 3600.0:,.1f} arcsec" in got.limitations[2]
    assert f"{got.illuminated_fraction_bound:.2g}" in got.limitations[2]


def test_synodic_bound_divides_both_moon_and_sun_terms_by_cosine_of_ecliptic_latitude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In ecliptic coordinates of date the Sun's latitude is under an arcsecond, so `cos(lat)` is within 1e-11 of 1;
    tilting the returned ecliptic latitude verifies that both terms divide by `cos(lat)` rather than multiplying."""
    tilt_rad = 0.4
    monkeypatch.setattr(ephemeris.erfa, "eqec06", lambda j1, j2, ra_rad, dec_rad: (ra_rad, tilt_rad))
    got = moon_phase(MOMENT)
    scales, geocentre, observer, _ = ephemeris._observer(MOMENT, None, None)
    moon, _ = ephemeris._astrometric_au(BODY_BY_NAME["Moon"], scales.tt, observer)
    sun, _ = ephemeris._astrometric_au(BODY_BY_NAME["Sun"], scales.tt, observer)
    m_ac, m_al = ephemeris._vector_bound(BODY_BY_NAME["Moon"], scales, moon, None, moon, observer, False)
    s_ac, s_al = ephemeris._vector_bound(BODY_BY_NAME["Sun"], scales, sun, None, sun, observer, False)
    sin_m = m_ac / math.hypot(m_ac, float(np.linalg.norm(moon)) * ephemeris.AU_KM - m_al)
    sin_s = s_ac / math.hypot(s_ac, float(np.linalg.norm(sun)) * ephemeris.AU_KM - s_al)
    expected = math.degrees(math.asin(sin_m / math.cos(tilt_rad)) + math.asin(sin_s / math.cos(tilt_rad)))
    assert got.synodic_angle_bound_deg == pytest.approx(expected, rel=1e-12)


def test_the_phase_record_states_its_bounds_and_its_limits() -> None:
    phase = moon_phase(MOMENT, LAT, LON)
    with pytest.raises(FrozenInstanceError):
        phase.frame = "geocentric"  # type: ignore[misc]
    assert 0.0 < phase.phase_angle_bound_deg < 0.05 and 0.0 < phase.illuminated_fraction_bound < 1e-3
    assert phase.illuminated_fraction == pytest.approx((1.0 + math.cos(math.radians(phase.phase_angle_deg))) / 2.0)
    assert phase.phase_name == phase_name(phase.synodic_angle_deg, phase.synodic_angle_bound_deg)
    assert any("topocentric" in text for text in phase.limitations)
    assert any("not measured" in text for text in phase.limitations)
    assert any("convention" in text for text in phase.limitations)


def test_radiometry_takes_its_phase_from_the_geocentric_moon_phase() -> None:
    """radiometry.py keeps its name and keys (router.py and the answer contract name it) and reads moon_phase from
    the Earth's centre; its lunar magnitude is Krisciunas and Schaefer's on that phase angle, and across the 32 stored
    geocentric Horizons rows its rounded phase angle stays within 0.016 degree of Horizons."""
    for moment in ("1903-03-15T00:00:00Z", MOMENT, "2047-12-20T12:00:00Z"):
        old_style = compute_lunar_phase_and_illumination(moment)
        phase = moon_phase(moment)
        assert old_style["observedAtUTC"] == moment
        assert old_style["alphaDeg"] == round(phase.phase_angle_deg, 2) == old_style["alpha_deg"]
        assert (
            old_style["illuminationFraction"]
            == round(phase.illuminated_fraction, 4)
            == old_style["illumination_fraction"]
        )
        assert old_style["synodicAngleDeg"] == round(phase.synodic_angle_deg, 2)
        assert old_style["elongationDeg"] == round(phase.elongation_deg, 2) == old_style["elongation_deg"]
        assert old_style["phaseName"] == phase.phase_name == old_style["phase_name"]
        a = phase.phase_angle_deg
        expected_mag = round(-12.73 + 0.026 * abs(a) + 4.0e-9 * a**4, 2)
        assert old_style["lunarVisualMag"] == expected_mag == old_style["lunar_visual_mag"]
    (geo_moon,) = [r for r in _requests("phase-geocentric", "Moon")]
    worst_deg = max(
        abs(compute_lunar_phase_and_illumination(_iso(row[0]))["alphaDeg"] - float(row[7]))
        for row in _rows(geo_moon)
    )
    assert worst_deg <= 0.016


def _script():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts" / "fetch_horizons_ephemeris_checks.py"
    spec = importlib.util.spec_from_file_location("fetch_horizons_ephemeris_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_phase_fixture_is_what_the_script_plans_with_its_terms_and_checksums() -> None:
    script = _script()
    data = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
    assert data["format"] == "albedian.horizons-phase-rts-checks" and data["source"] == script.API
    assert data["script"] == "scripts/fetch_horizons_ephemeris_checks.py --set phase"
    assert "Permission to reproduce may be required" in data["terms"]
    assert [r["url"] for r in data["requests"]] == [r["url"] for r in script.plan_phase()]
    kinds = [r["kind"] for r in data["requests"]]
    assert kinds.count("rts") == 4 * 4 * 3 and kinds.count("phase-geocentric") == 8
    for request in data["requests"]:
        assert hashlib.sha256(request["response"].encode("utf-8")).hexdigest() == request["responseSha256"]
        assert "Albedian" not in request["url"] and request["url"].startswith(script.API + "?")


@pytest.mark.parametrize("which", ["positions", "phase"])
def test_the_script_rebuilds_each_fixture_exactly_from_its_stored_answers(
    which: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Offline: a cache made from the fixture's own answers, and the script's --verify for that set, which rebuilds the
    fixture from the cache alone and fails if one byte differs."""
    script = _script()
    out = script.PHASE_OUT if which == "phase" else script.OUT
    for request in json.loads(gzip.decompress(out.read_bytes()).decode("utf-8"))["requests"]:
        entry = {"url": request["url"], "retrievedUtc": request["retrievedUtc"], "text": request["response"]}
        (tmp_path / f"{hashlib.sha256(request['url'].encode()).hexdigest()[:24]}.json").write_text(json.dumps(entry))
    # The set's own default cache is the one made here; the other set's is an empty folder, so a swap fails
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(script, "PHASE_CACHE", tmp_path if which == "phase" else empty)
    monkeypatch.setattr(script, "DEFAULT_CACHE", empty if which == "phase" else tmp_path)
    monkeypatch.setattr("sys.argv", ["fetch", "--verify"] + (["--set", "phase"] if which == "phase" else []))
    script.main()
    assert "matches the cache" in capsys.readouterr().out


def test_the_script_writes_the_phase_fixture_from_cache_and_refuses_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --verify, `--set phase` plans afresh, takes every answer from `PHASE_CACHE`, and writes the stored
    fixture text with mtime 0; with `--verify`, a single changed byte in the fixture is refused."""
    script = _script()
    cache = tmp_path / "phase-cache"
    cache.mkdir()
    for request in json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))["requests"]:
        entry = {"url": request["url"], "retrievedUtc": request["retrievedUtc"], "text": request["response"]}
        (cache / f"{hashlib.sha256(request['url'].encode()).hexdigest()[:24]}.json").write_text(json.dumps(entry))
    out = tmp_path / "tests" / "fixtures" / "horizons_phase_rts.json.gz"
    out.parent.mkdir(parents=True)
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "PHASE_OUT", out)
    monkeypatch.setattr(script, "PHASE_CACHE", cache)
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("a request was made"))
    monkeypatch.setattr("sys.argv", ["fetch", "--set", "phase"])
    script.main()
    assert "wrote tests/fixtures/horizons_phase_rts.json.gz: 67 requests" in capsys.readouterr().out
    written = out.read_bytes()
    assert written[4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(written) == gzip.decompress(FIXTURE.read_bytes())

    altered = json.loads(gzip.decompress(written).decode("utf-8"))
    altered["description"] += "!"
    out.write_bytes(gzip.compress((json.dumps(altered, indent=1, ensure_ascii=False) + "\n").encode("utf-8"), mtime=0))
    monkeypatch.setattr("sys.argv", ["fetch", "--verify", "--set", "phase"])
    with pytest.raises(SystemExit, match="differs from the fixture"):
        script.main()

    assert script.build([], cache, offline=True)["format"] == script.FORMAT
    one_cache = tmp_path / "one-cache"
    one_cache.mkdir()
    first = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))["requests"][0]
    (one_cache / f"{hashlib.sha256(first['url'].encode()).hexdigest()[:24]}.json").write_text(
        json.dumps({"url": first["url"], "retrievedUtc": first["retrievedUtc"], "text": first["response"]})
    )
    subset = script.build([first], one_cache, offline=True, phase=True)
    out.write_bytes(gzip.compress((json.dumps(subset, indent=1, ensure_ascii=False) + "\n").encode("utf-8"), mtime=0))
    monkeypatch.setattr(script, "PHASE_CACHE", one_cache)
    monkeypatch.setattr("sys.argv", ["fetch", "--verify", "--set", "phase"])
    try:
        script.main()
    except SystemExit as exc:  # pragma: no cover - fails if --verify replans all 67 instead of reading the fixture
        pytest.fail(f"--verify did not read the committed requests from the fixture: {exc}")
    assert "matches the cache: 1 requests" in capsys.readouterr().out
