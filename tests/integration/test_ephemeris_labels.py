"""The ephemeris says what it computes and how far it can be off.

The Moon comes from ERFA's eraMoon98 (Meeus 1998, chapter 47 in full), the planets from eraPlan94 (Simon et al.
1994), the Earth and the Sun from eraEpv00, each seen from the place (topocentric) unless asked for from the
Earth's centre. Each stated uncertainty is built from the documented worst errors in ERFA's own notes, which these tests read
from the installed library, plus the budget for what is assumed (UT1 - UTC, the place's height, polar motion, the
time scale); tests/integration/test_ephemeris_horizons.py checks the result against JPL Horizons. No output may name
Horizons or DE440 (no Horizons answer stands behind a finding at run time), and no fixed magnitude may pass for the
one at the moment.

Covers:
- No ephemeris output names JPL Horizons or DE440, carries a catalog reference, or states a magnitude
- The documented accuracies the module uses are the ones ERFA's notes give (eraPlan94's two tables, the larger of each;
  eraMoon98's worst case; eraEpv00's worst heliocentric error)
- The body table: the Sun, the Moon, Mercury to Neptune, each with its Horizons id; an unknown body is refused
- PBT: a planet's stated uncertainty holds its documented heliocentric error carried to the place, the Earth's, UT1 -
  UTC, the place's height and polar motion, and its distance bounds the documented distance errors
- PBT: the Moon's stated uncertainty and distance bounds hold its documented error at its distance from the place and
  the same budget; it says it is topocentric, and from the Earth's centre it says it is geocentric with that moment's
  parallax shift
- The horizon solver claims no QuickTime source for a typed moment, places predicted stars at their position of the
  date, and its pose warning says the Moon it uses is topocentric
- The Sun's apparent declination of date is 0 at USNO's equinoxes and the true obliquity at its solstices, within its
  stated uncertainty; PBT: that uncertainty holds the Earth's documented heliocentric error and the same budget; the
  default bodies leave out the Sun, Mercury and Neptune
- The sky-context route sends no fixed magnitude
- Each era of the time scales (before 1800, 1800 to 1900, 1900 to 1960, 1960 to mid 2027, later) states the bound that
  was applied to that moment: the rotation part and the TT window in the words beside their numbers
- No horizon pose and no stored contract sample names the methods the ephemeris left (Standish and Williams, the
  six-term Moon) or calls its Moon geocentric
- Each stated uncertainty held from above: at fixed cases (every body, from a place and from the Earth's centre, four
  eras, the day eraEpv00's documented span begins among them) each part equals its formula written here, and the
  record's total and distance bounds their sum
- Every record's second limitation names where it is seen from (the place; the Earth's centre, with the Moon's shift or
  the place's parallax held)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import json
import math
import re
import warnings

import erfa
from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from services.api import app
from services.science import ephemeris
from services.science.catalog import (
    earth_position_velocity,
    get_all_sky_catalog_stars,
    observer_astrom,
    propagate_star_epoch,
    ra_dec_to_unit_vector,
    time_scales,
)
from services.science.horizon_solver import solve_horizon_and_beacon_pose

START = datetime(1800, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2051, 1, 1, tzinfo=timezone.utc) - START).total_seconds() - 1.0
PLANETS = ("Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")
AU_KM = 149597870.7
ARCSEC_PER_RAD = 180.0 * 3600.0 / math.pi
# The parts of the budget every body carries, as the module states them: UT1 - UTC within 0.9 s turns the sky by
# 0.9 s of the Earth's rotation (15.04 arcsec per second, eraEra00's rate) and moves the place; a place up to 9 km
# above sea level; 1 arcsec for polar motion
ROTATION_ARCSEC = 0.9 * 1296000.0 * 1.00273781191135448 / 86400.0
EARTH_RADIUS_KM = 6378.137
ROTATION_RAD_PER_S = 2.0 * math.pi * 1.00273781191135448 / 86400.0
HEIGHT_KM = 9.0
POLAR_MOTION_ARCSEC = 1.0
# A synthetic moment and place that point at nobody (as in test_no_default_moment): the Moon at 33 degrees there
MOON_MOMENT = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}

instant = st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s))
place = st.tuples(st.floats(min_value=-80.0, max_value=80.0), st.floats(min_value=-180.0, max_value=180.0))


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sep_arcsec(u, v) -> float:
    u, v = np.ravel(u) / np.linalg.norm(u), np.ravel(v) / np.linalg.norm(v)
    return math.degrees(math.atan2(np.linalg.norm(np.cross(u, v)), float(u @ v))) * 3600.0


def _plan94_documented() -> dict[str, tuple[float, float, float]]:
    """eraPlan94's two accuracy tables (note 5, as the installed pyerfa ships it), the larger value of each."""
    largest: dict[str, tuple[float, float, float]] = {}
    for line in erfa.plan94.__doc__.splitlines():
        row = re.match(r"\s+(Mercury|Venus|EMB|Mars|Jupiter|Saturn|Uranus|Neptune)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", line)
        if row:
            values = tuple(float(v) for v in row.groups()[1:])
            previous = largest.get(row.group(1), (0.0, 0.0, 0.0))
            largest[row.group(1)] = tuple(max(a, b) for a, b in zip(previous, values))
    return largest


def _budget_arcsec(distance_au: float) -> float:
    """UT1 - UTC, the place's height and polar motion, for a body at `distance_au` from the place."""
    distance_km = distance_au * AU_KM
    return (
        ROTATION_ARCSEC
        + EARTH_RADIUS_KM * ROTATION_RAD_PER_S * 0.9 / distance_km * ARCSEC_PER_RAD
        + HEIGHT_KM / distance_km * ARCSEC_PER_RAD
        + POLAR_MOTION_ARCSEC
    )


def _deflection_arcsec(item, lat: float, lon: float) -> float:
    """The light deflection by the Sun that `icrs_to_altaz` applies to the finding's direction (ERFA eraLdsun, as for a
    star at infinity), which a nearer body does not get in full: the most it can be wrong by."""
    astrom = observer_astrom(item.obs_utc_iso, float(lat), float(lon))
    direction = np.array(ra_dec_to_unit_vector(item.ra_deg, item.dec_deg))
    return _sep_arcsec(direction, erfa.ldsun(direction, astrom["eh"], astrom["em"]))


def _moved_arcsec(body: str, when: datetime, lat: float, lon: float) -> float:
    """How far the body's direction from the Earth's centre moves over the moment's TT bound, either way: the most a TT
    off by that bound can move it."""
    bound = time_scales(_iso(when)).tt_bound_s
    if bound == 0.0:
        return 0.0
    (here,) = ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=(body,), topocentric=False)
    moved = 0.0
    for sign in (-1.0, 1.0):
        # Each side that stays inside 1800 to 2050 (at the range's edges only one does): a lower bound either way
        shifted = when + timedelta(seconds=sign * bound)
        if not START <= shifted < datetime(2051, 1, 1, tzinfo=timezone.utc):
            continue
        (there,) = ephemeris.compute_solar_system_ephemerides(
            _iso(shifted), lat, lon, bodies=(body,), topocentric=False
        )
        moved = max(moved, _sep_arcsec(ra_dec_to_unit_vector(here.ra_deg, here.dec_deg),
                                       ra_dec_to_unit_vector(there.ra_deg, there.dec_deg)))
    return moved


def _budget_before_1960_arcsec(distance_au: float) -> float:
    """Before 1960 the moment is taken as UT1, so no UT1 - UTC part."""
    distance_km = distance_au * AU_KM
    return HEIGHT_KM / distance_km * ARCSEC_PER_RAD + POLAR_MOTION_ARCSEC


def test_no_output_names_horizons_or_de440_or_states_a_magnitude() -> None:
    items = ephemeris.compute_solar_system_ephemerides("2031-08-30T03:01:00Z", 30.0, -40.0, bodies=(*PLANETS, "Moon"))
    payloads = [item.to_contract_dict(source_sha256=None) for item in items]
    text = json.dumps(payloads)
    assert "Horizons" not in text and "DE440" not in text and "JPL-" not in text and "sol-ephemeris" not in text
    assert all(
        "catalogRef" not in p and "observationSolutionId" not in p and "apparentMagnitude" not in json.dumps(p)
        for p in payloads
    )
    response = TestClient(app).post(
        "/api/science/ephemeris", json={"observedAtUTC": "2031-08-30T03:01:00Z", "latDeg": 30.0, "lonDeg": -40.0}
    )
    assert response.status_code == 200
    assert (
        "Horizons" not in response.text
        and "DE440" not in response.text
        and "apparentMagnitude" not in response.text
        and "sol-ephemeris" not in response.text
    )


def test_the_documented_accuracies_are_the_ones_erfas_notes_give() -> None:
    documented = _plan94_documented()
    numbers = {"Mercury": 1, "Venus": 2, "Mars": 4, "Jupiter": 5, "Saturn": 6, "Uranus": 7, "Neptune": 8}
    assert ephemeris.PLAN94_MAXIMUM_ERRORS == {numbers[name]: documented[name] for name in numbers}
    moon_note = " ".join(erfa.moon98.__doc__.split())
    assert (
        f"worst case errors were {ephemeris.MOON98_DIRECTION_ARCSEC} arcsec in geocentric direction, "
        f"{ephemeris.MOON98_POSITION_KM} km in position" in moon_note
    )
    assert "over the interval 1950-2100" in moon_note
    heliocentric = re.search(r"Heliocentric:\s+position error\s+[\d.]+\s+([\d.]+)\s+km", erfa.epv00.__doc__)
    assert heliocentric is not None and float(heliocentric.group(1)) == ephemeris.EPV00_HELIOCENTRIC_KM


def test_the_body_table_holds_the_sun_the_moon_and_mercury_to_neptune() -> None:
    assert ephemeris.BODY_NAMES == ("Sun", "Moon", *PLANETS)
    assert {body.name: body.horizons_id for body in ephemeris.BODIES} == {
        "Sun": "10", "Moon": "301", "Mercury": "199", "Venus": "299", "Mars": "499", "Jupiter": "599",
        "Saturn": "699", "Uranus": "799", "Neptune": "899",
    }
    assert set(ephemeris.DEFAULT_BODIES) <= set(ephemeris.BODY_NAMES)
    with pytest.raises(KeyError, match="Unsupported Solar System body: Pluto"):
        ephemeris.compute_solar_system_ephemerides("2031-08-30T03:01:00Z", 30.0, -40.0, bodies=("Pluto",))


@settings(max_examples=100, deadline=None)
@given(when=instant, body=st.sampled_from(PLANETS), where=place)
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc), body="Jupiter", where=(30.0, -40.0))
@example(when=datetime(2026, 10, 1, tzinfo=timezone.utc), body="Venus", where=(30.0, -40.0))
@example(when=datetime(1850, 3, 14, 18, tzinfo=timezone.utc), body="Uranus", where=(-45.0, -130.0))
def test_pbt_a_planets_uncertainty_holds_its_documented_error_and_the_budget(
    when: datetime, body: str, where: tuple[float, float]
) -> None:
    lat, lon = where
    (item,) = ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=(body,), topocentric=True)
    lon_err, lat_err, radius_km = _plan94_documented()[body]
    # The planet's heliocentric place when its light left it
    tt = time_scales(_iso(when)).tt
    number = ephemeris.BODY_BY_NAME[body].plan94_number
    helio = erfa.plan94(tt[0], tt[1] - item.distance_au / (erfa.CMPS * 86400.0 / erfa.DAU), number)["p"]
    line_of_sight = np.array(ra_dec_to_unit_vector(item.ra_deg, item.dec_deg))
    sin_phase = float(np.linalg.norm(np.cross(helio / np.linalg.norm(helio), line_of_sight)))
    across_km = float(np.linalg.norm(helio)) * AU_KM * math.hypot(lon_err, lat_err) / ARCSEC_PER_RAD
    earth_km = 11.2 * (3.0 if sum(time_scales(_iso(when)).tt) < 2415020.5 else 1.0)
    theory = (across_km + radius_km * sin_phase + earth_km) / (item.distance_au * AU_KM) * ARCSEC_PER_RAD
    budget = _budget_arcsec(item.distance_au) if when.year >= 1960 else _budget_before_1960_arcsec(item.distance_au)
    assert item.uncertainty_arcsec >= (theory + budget + _deflection_arcsec(item, lat, lon)) * (1.0 - 1e-6)
    assert item.distance_upper_au - item.distance_au >= (radius_km + across_km + earth_km + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    assert item.distance_au - item.distance_lower_au >= (radius_km + across_km + earth_km + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    payload = item.to_contract_dict(source_sha256=None)
    assert payload["skyPosition"]["uncertaintyArcsec"] == item.uncertainty_arcsec
    assert "eraPlan94 (Simon et al. 1994)" in payload["evidence"]["verificationMethod"]
    limitations_text = " ".join(payload["evidence"]["limitations"])
    rotation_words = "UT1 - UTC taken as 0 (within 0.9 s)" if when.year >= 1960 else "with the moment taken as UT1"
    assert "topocentric" in limitations_text and rotation_words in limitations_text and "9 km" in limitations_text


@settings(max_examples=100, deadline=None)
@given(when=instant, where=place)
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc), where=(30.0, -40.0))
@example(when=datetime(1833, 3, 3, 15, tzinfo=timezone.utc), where=(-60.0, 0.0))
@example(when=datetime(2044, 5, 27, 19, 3, 7, tzinfo=timezone.utc), where=(-20.0, 75.0))
def test_pbt_the_moons_bounds_hold_its_documented_error_and_it_says_where_it_is_seen_from(
    when: datetime, where: tuple[float, float]
) -> None:
    lat, lon = where
    (moon,) = ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=("Moon",))
    (geocentric,) = ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=("Moon",), topocentric=False)
    # Given a place, the default is that place: the record says so, and is the topocentric one
    assert ephemeris.TOPOCENTRIC_BY_DEFAULT is True
    assert moon == ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=("Moon",), topocentric=True)[0]
    budget = _budget_arcsec(moon.distance_au) if when.year >= 1960 else _budget_before_1960_arcsec(moon.distance_au)
    theory = 18.3 * geocentric.distance_au / moon.distance_au
    # The Moon moves about half an arcsecond a second: a TT bound of a few seconds shows. The place turns with UT1,
    # not TT, so the motion from the Earth's centre is the part a TT error brings, within 5 percent
    moved = _moved_arcsec("Moon", when, lat, lon)
    assert moon.uncertainty_arcsec >= (theory + budget + _deflection_arcsec(moon, lat, lon) + 0.95 * moved) * (1 - 1e-6)
    assert moon.distance_upper_au - moon.distance_au >= (31.7 + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    assert moon.distance_au - moon.distance_lower_au >= (31.7 + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    # The place's parallax is applied: the two directions differ by about the parallax shift
    assert moon.frame == "topocentric" and geocentric.frame == "geocentric"
    parallax_arcsec, _ = ephemeris.moon_parallax_shift_arcsec(geocentric.distance_au, geocentric.alt_deg)
    apart = _sep_arcsec(ra_dec_to_unit_vector(moon.ra_deg, moon.dec_deg), ra_dec_to_unit_vector(geocentric.ra_deg, geocentric.dec_deg))
    assert apart <= parallax_arcsec * 1.01
    payload = moon.to_contract_dict(source_sha256=None)
    assert payload["evidence"]["verificationMethod"].startswith("Meeus (1998) chapter 47 in full")
    assert any("(topocentric): its parallax is applied" in text for text in payload["evidence"]["limitations"])
    # From the Earth's centre it says so, with that moment's parallax shift, and its uncertainty holds the shift
    shift = parallax_arcsec * math.cos(math.radians(geocentric.alt_deg))
    said = [text for text in geocentric.limitations if "geocentric" in text.lower()]
    assert said and f"{shift:,.0f} arcsec" in said[0] and "its uncertainty holds that shift" in said[0]
    assert geocentric.uncertainty_arcsec >= parallax_arcsec + theory
    assert geocentric.distance_upper_au - geocentric.distance_au >= (31.7 + EARTH_RADIUS_KM) / AU_KM


def _night_frame(height: int, width: int) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    frame[115:129, 927:941] = 240.0
    return frame


def test_horizon_solver_claims_no_quicktime_source_and_places_stars_at_the_date() -> None:
    frame = _night_frame(1792, 1344)
    sol = solve_horizon_and_beacon_pose(
        frame, **MOON_MOMENT, source_asset_id="synthetic-night", source_sha256=hashlib.sha256(frame.tobytes()).hexdigest()
    )
    assert sol.status == "approximate"
    assert not any("QuickTime" in limitation for f in sol.findings for limitation in f.limitations)
    # Since sky-4 the pose's own assumptions say which Moon it lined up: the record as it is, topocentric
    assert any(
        "The Moon is placed where it is seen from the place (topocentric): the ephemeris record as it is" in assumption
        for assumption in sol.pose_estimate.assumptions
    )
    # Every predicted star sits where its proper motion has carried its J2016.0 catalog tile row by the date, not at
    # its catalog epoch; the 71 pinned J2000 records sit within 3 arcsec of their tile rows (test_pinned_catalog_matches_tiles.py)
    from services.science.predicted_sky import _sky_rows

    year = 2031.0 + (datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc) - datetime(2031, 1, 1, tzinfo=timezone.utc)).days / 365.25
    by_id = {f.id: f for f in sol.findings}
    tile_by_id = {s.id: s for s in _sky_rows().stars}
    checked, largest_motion = 0, 0.0
    for star in get_all_sky_catalog_stars():
        finding = by_id.get(f"finding-predicted-{star.id}")
        if finding is None:
            continue
        moved = propagate_star_epoch(star, year)
        tile_star = tile_by_id[star.id]
        tile_moved = propagate_star_epoch(tile_star, year)
        position = np.array(ra_dec_to_unit_vector(finding.ra_deg, finding.dec_deg))
        assert _sep_arcsec(position, np.array(ra_dec_to_unit_vector(tile_moved.ra_deg, tile_moved.dec_deg))) < 0.1, star.common_name
        assert _sep_arcsec(position, np.array(ra_dec_to_unit_vector(moved.ra_deg, moved.dec_deg))) < 3.0, star.common_name
        largest_motion = max(largest_motion, _sep_arcsec(position, np.array(ra_dec_to_unit_vector(star.ra_deg, star.dec_deg))))
        checked += 1
    assert checked >= 10 and largest_motion > 10.0


# USNO's "Earth's Seasons" data service (aa.usno.navy.mil/data/Earth_Seasons), read at source through the API it
# names (aa.usno.navy.mil/api/seasons?year=2030 and ?year=1850): the moments in UT, to the minute. At an equinox the
# Sun crosses the celestial equator (declination 0); at a solstice it stands at its greatest declination north or
# south, the obliquity of the ecliptic, which USNO's "The Seasons and the Earth's Orbit"
# (aa.usno.navy.mil/faq/seasons_orbit) gives as 23.4 degrees.
USNO_SEASONS = (
    ("2030-03-20T13:52:00Z", 0),
    ("2030-06-21T07:31:00Z", 1),
    ("2030-09-22T23:27:00Z", 0),
    ("2030-12-21T20:09:00Z", -1),
    ("1850-03-20T23:02:00Z", 0),
    ("1850-06-21T19:59:00Z", 1),
    ("1850-12-22T03:37:00Z", -1),
)


@pytest.mark.parametrize(("moment", "side"), USNO_SEASONS)
def test_the_sun_stands_where_usno_puts_it_at_the_equinoxes_and_solstices(moment: str, side: int) -> None:
    # The seasons are the Sun's apparent place from the Earth's centre: its astrometric direction (geocentric) with
    # annual aberration (ERFA eraAb, the Earth's barycentric velocity), on the true equator of date (eraPnm06a), and
    # at a solstice the true obliquity (eraObl06 plus the nutation in obliquity, eraNut06a)
    (sun,) = ephemeris.compute_solar_system_ephemerides(moment, 30.0, -40.0, bodies=("Sun",), topocentric=False)
    tt = time_scales(moment).tt
    with warnings.catch_warnings():
        # ERFA warns for 1850, outside eraEpv00's 1900 to 2100; its velocity there is still far better than this needs
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        pvh, pvb = erfa.epv00(*tt)
    velocity = pvb["v"] / (erfa.CMPS * 86400.0 / erfa.DAU)
    apparent = erfa.ab(
        np.array(ra_dec_to_unit_vector(sun.ra_deg, sun.dec_deg)), velocity, float(np.linalg.norm(pvh["p"])),
        math.sqrt(1.0 - float(velocity @ velocity)),
    )
    declination = math.degrees(math.asin(float((erfa.pnm06a(*tt) @ apparent)[2])))
    obliquity = math.degrees(erfa.obl06(*tt) + erfa.nut06a(*tt)[1])
    assert abs(obliquity - 23.4) < 0.1
    expected = side * obliquity
    assert abs(declination - expected) * 3600.0 <= sun.uncertainty_arcsec, (declination - expected) * 3600.0
    # Within its stated uncertainty, which here is under a minute of arc
    assert sun.uncertainty_arcsec < 60.0


@settings(max_examples=100, deadline=None)
@given(when=instant, where=place)
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc), where=(30.0, -40.0))
def test_pbt_the_suns_uncertainty_holds_the_earths_documented_error_and_the_budget(
    when: datetime, where: tuple[float, float]
) -> None:
    lat, lon = where
    (sun,) = ephemeris.compute_solar_system_ephemerides(_iso(when), lat, lon, bodies=("Sun",), topocentric=True)
    earth_km = 11.2 * (3.0 if sum(time_scales(_iso(when)).tt) < 2415020.5 else 1.0)
    budget = _budget_arcsec(sun.distance_au) if when.year >= 1960 else _budget_before_1960_arcsec(sun.distance_au)
    assert sun.uncertainty_arcsec >= (earth_km / (sun.distance_au * AU_KM) * ARCSEC_PER_RAD + budget) * (1 - 1e-6)
    assert sun.distance_upper_au - sun.distance_au >= (earth_km + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    assert sun.distance_au - sun.distance_lower_au >= (earth_km + HEIGHT_KM) / AU_KM * (1 - 1e-6)
    assert 0.98 < sun.distance_au < 1.02
    payload = sun.to_contract_dict(source_sha256=None)
    assert payload["id"] == "finding-predicted-sun" and sun.object_type == "sun"
    assert payload["skyPosition"]["uncertaintyArcsec"] == sun.uncertainty_arcsec
    assert "eraEpv00 (simplified VSOP2000)" in payload["evidence"]["verificationMethod"]
    assert "Horizons" not in json.dumps(payload) and "apparentMagnitude" not in json.dumps(payload)


def test_the_default_bodies_leave_out_the_sun_mercury_and_neptune() -> None:
    names = [item.name for item in ephemeris.compute_solar_system_ephemerides(MOON_MOMENT["obs_utc_iso"], 30.0, -40.0)]
    assert names == ["Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"]


def test_sky_context_sends_no_fixed_magnitude() -> None:
    response = TestClient(app).post(
        "/api/science/sky-context", json={"observedAtUTC": "2031-08-30T03:01:00Z", "latDeg": 30.0, "lonDeg": -40.0}
    )
    assert response.status_code == 200
    assert all("apparentMagnitude" not in body for body in response.json()["ephemerides"])


# One moment in each era of the time scales, at the open North Atlantic place: (moment, UT1 bound, TT bound)
ERAS = (
    ("1850-03-14T18:00:00Z", 0.0, 3.0),
    ("1930-06-01T04:00:00Z", 0.0, 0.3),
    ("2000-06-01T04:00:00Z", 0.9, 0.0),
    ("2040-01-01T02:00:00Z", 0.9, 26.0),
)
# The rotation part, in either era's words, and the time-scale part
ROTATION = re.compile(
    r"(?P<number>[\d,.]+) for (?P<words>UT1 - UTC taken as 0 \(within [\d.]+ s\)|the Earth's rotation, with the "
    r"moment taken as UT1)"
)
TIME_SCALE = re.compile(r"(?P<number>[\d,.]+) for the time scale\.")


@pytest.mark.parametrize(("moment", "ut1_bound_s", "tt_bound_s"), ERAS)
def test_each_eras_limitation_states_the_bound_applied_to_that_moment(
    moment: str, ut1_bound_s: float, tt_bound_s: float
) -> None:
    scales = time_scales(moment)
    assert (scales.ut1_bound_s, scales.tt_bound_s) == (ut1_bound_s, tt_bound_s)
    (moon,) = ephemeris.compute_solar_system_ephemerides(moment, 30.0, -40.0, bodies=("Moon",))
    sentence = next(text for text in moon.limitations if text.startswith("Its uncertainty"))
    rotation_part, time_part = ROTATION.search(sentence), TIME_SCALE.search(sentence)
    assert rotation_part is not None and time_part is not None, sentence
    rotation = float(rotation_part.group("number").replace(",", ""))
    if ut1_bound_s == 0.0:
        # The moment is taken as UT1: no rotation part, and no window named for it
        assert (rotation, rotation_part.group("words")) == (0.0, "the Earth's rotation, with the moment taken as UT1")
    else:
        assert rotation_part.group("words") == f"UT1 - UTC taken as 0 (within {ut1_bound_s:g} s)"
        assert rotation >= ut1_bound_s * ROTATION_ARCSEC / 0.9 - 0.05
    # The TT window the basis sentence names is the one applied (its first window; with none, TT is the leap-second
    # table's and only UT1's 0.9 s is named), and the time-scale part is there exactly when it is
    basis = moon.limitations[-1]
    windows = [float(w) for w in re.findall(r"(?:within|up to) ([\d.]+) s", basis)]
    if tt_bound_s > 0.0:
        assert windows and windows[0] == tt_bound_s, basis
    else:
        assert "TT comes from ERFA's leap-second table" in basis and windows == [ut1_bound_s], basis
    assert (float(time_part.group("number").replace(",", "")) > 0.0) == (tt_bound_s > 0.0)


def test_before_1800_the_time_scales_state_their_own_wider_bound() -> None:
    scales = time_scales("1790-01-01T02:00:00Z")
    assert scales.ut1_bound_s == 0.0 and scales.tt_bound_s > 3.0
    windows = [float(w) for w in re.findall(r"within ([\d.]+) s", scales.basis)]
    assert windows == [scales.tt_bound_s], scales.basis
    assert "a wider bound than later" in scales.basis and "a wider bound" not in time_scales("1850-03-14T18:00:00Z").basis
    # The sentence goes with the bound: on the last day of 1799 it is said, from the first day of 1800 it is not
    assert "a wider bound than later" in time_scales("1799-12-31T12:00:00Z").basis
    assert "a wider bound" not in time_scales("1800-01-01T12:00:00Z").basis


def test_no_horizon_pose_or_contract_sample_names_the_methods_the_ephemeris_left() -> None:
    frame = _night_frame(1792, 1344)
    sol = solve_horizon_and_beacon_pose(
        frame, **MOON_MOMENT, source_asset_id="synthetic-night", source_sha256=hashlib.sha256(frame.tobytes()).hexdigest()
    )
    assert "ERFA" in sol.solver.catalog_release
    stored = (Path(__file__).resolve().parents[1] / "fixtures" / "python_contract_solutions.json").read_text()
    for old in ("Standish", "low-precision ephemeris", "Meeus 1998 chapter 47)", "which is geocentric"):
        assert old not in sol.solver.catalog_release and old not in stored, old


def test_the_head_worn_pose_and_the_predicted_stars_state_the_frame_and_the_transform_they_use() -> None:
    """The head-worn pose names the Moon it uses as the topocentric ERFA record, lowered by nothing; a predicted star
    does not claim that nutation or annual aberration are left out, since the transform holds them."""
    from services.science import pose_estimate
    from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL, camera_profile
    from services.science.predicted_sky import POSE_SOLVER, STAR_POSITION_LIMITATION

    release = POSE_SOLVER.catalog_release
    assert "ERFA" in release and "topocentric" in release, release
    for old in ("low-precision", "chapter 47)", "lowered by its parallax"):
        assert old not in release, old
    assert "nutation" not in STAR_POSITION_LIMITATION and "aberration" not in STAR_POSITION_LIMITATION
    moon = pose_estimate.topocentric_moon(MOON_MOMENT["obs_utc_iso"], MOON_MOMENT["observer_lat"],
                                          MOON_MOMENT["observer_lon"])
    words = " ".join(pose_estimate._assumptions(camera_profile(META_RAY_BAN_DISPLAY_MODEL), moon, 24.6))
    assert "the ephemeris record as it is, its parallax already applied there" in words
    assert "lowered by" not in words and f"uncertainty of {moon.uncertainty_deg * 3600.0:,.0f} arcsec" in words
    assert "plus 25 arcsec for drawing every position through one rotation" in words


# Each stated uncertainty held from above: at fixed cases (an era with a UT1 bound and a TT bound, one before 1960 with
# only a TT bound, one with only a UT1 bound; from a place and from the Earth's centre; every body) each part of the
# bound equals its formula, written here from the documented accuracies and the geometry, and the record's total and
# distance bounds equal their sum. The properties above hold each from below.
# Noon on 1900-01-01 is the day eraEpv00's documented span begins: its TT pair is (2415020.5, about 0.5), past the
# boundary only when its two parts are added
UPPER_CASES = [("2031-08-30T03:01:00Z", True), ("2031-08-30T03:01:00Z", False), ("1850-03-14T18:00:00Z", True),
               ("1850-03-14T18:00:00Z", False), ("1995-05-01T12:00:00Z", True), ("1995-05-01T12:00:00Z", False),
               ("1900-01-01T12:00:00Z", True), ("1900-01-01T12:00:00Z", False)]


@pytest.mark.parametrize("body_name", [body.name for body in ephemeris.BODIES])
@pytest.mark.parametrize(("moment", "topocentric"), UPPER_CASES)
def test_every_part_of_each_stated_uncertainty_equals_its_formula_at_fixed_cases(
    body_name: str, moment: str, topocentric: bool,
) -> None:
    lat, lon = 30.0, -40.0
    body = ephemeris.BODY_BY_NAME[body_name]
    scales = time_scales(moment)
    astrom = observer_astrom(moment, lat, lon)
    geocentre = np.asarray(earth_position_velocity(scales.tt)[1]["p"], dtype=np.float64)
    observer = np.asarray(astrom["eb"], dtype=np.float64) if topocentric else geocentre
    vector, helio = ephemeris._astrometric_au(body, scales.tt, observer)
    geocentric = ephemeris._astrometric_au(body, scales.tt, geocentre)[0]
    d_km = float(np.linalg.norm(vector)) * AU_KM
    earth_km = 11.2 * (3.0 if sum(scales.tt) < 2415020.5 else 1.0)
    if body.object_type == "moon":
        theory, theory_km = 18.3 * float(np.linalg.norm(geocentric)) / float(np.linalg.norm(vector)), 31.7
    elif body.object_type == "sun":
        theory, theory_km = earth_km / d_km * ARCSEC_PER_RAD, earth_km
    else:
        lon_e, lat_e, radius_km = ephemeris.PLAN94_MAXIMUM_ERRORS[body.plan94_number]
        r_au = float(np.linalg.norm(helio))
        across_km = r_au * AU_KM * math.hypot(lon_e, lat_e) / ARCSEC_PER_RAD
        sin_phase = float(np.linalg.norm(np.cross(helio, vector))) / (r_au * float(np.linalg.norm(vector)))
        theory = (across_km + radius_km * sin_phase + earth_km) / d_km * ARCSEC_PER_RAD
        theory_km = across_km + radius_km + earth_km
    unit = vector / float(np.linalg.norm(vector))
    bent = erfa.ldsun(unit, astrom["eh"], astrom["em"])
    deflection = math.degrees(math.atan2(float(np.linalg.norm(np.cross(unit, bent))), float(unit @ bent))) * 3600.0
    rotation = scales.ut1_bound_s * 1296000.0 * 1.00273781191135448 / 86400.0
    if topocentric:
        rotation += EARTH_RADIUS_KM * ROTATION_RAD_PER_S * scales.ut1_bound_s / d_km * ARCSEC_PER_RAD
        height, place_km = math.asin(HEIGHT_KM / d_km) * ARCSEC_PER_RAD, HEIGHT_KM
    else:
        height = math.asin((EARTH_RADIUS_KM + HEIGHT_KM) / d_km) * ARCSEC_PER_RAD
        place_km = EARTH_RADIUS_KM + HEIGHT_KM
    time_scale = 0.0
    for sign in ((-1.0, 1.0) if scales.tt_bound_s > 0.0 else ()):
        shifted_tt = (scales.tt[0], scales.tt[1] + sign * scales.tt_bound_s / 86400.0)
        offset = observer - geocentre
        shifted_observer = np.asarray(earth_position_velocity(shifted_tt)[1]["p"], dtype=np.float64) + offset
        shifted, _ = ephemeris._astrometric_au(body, shifted_tt, shifted_observer)
        moved = math.atan2(float(np.linalg.norm(np.cross(unit, shifted))), float(unit @ shifted))
        time_scale = max(time_scale, math.degrees(moved) * 3600.0)
    bound = ephemeris._bound(body, scales.tt, vector, helio, geocentric, observer, astrom, topocentric,
                             scales.tt_bound_s, scales.ut1_bound_s)
    parts = {"theory": (bound.theory_arcsec, theory), "deflection": (bound.deflection_arcsec, deflection),
             "rotation": (bound.rotation_arcsec, rotation), "height": (bound.height_arcsec, height),
             "polar motion": (bound.polar_motion_arcsec, POLAR_MOTION_ARCSEC),
             "time scale": (bound.time_scale_arcsec, time_scale), "distance": (bound.distance_km, theory_km + place_km)}
    for name, (got, formula) in parts.items():
        # 1e-8 arcsec absolute: the time-scale part is the angle between two nearly equal vectors, which differs here by
        # under 1e-10 from its formula (one rounding of a unit vector is 4.6e-11 arcsec)
        assert got == pytest.approx(formula, rel=1e-9, abs=1e-8), (body_name, moment, topocentric, name)
    (record,) = ephemeris.compute_solar_system_ephemerides(moment, lat, lon, bodies=(body_name,), topocentric=topocentric)
    total = theory + deflection + rotation + height + POLAR_MOTION_ARCSEC + time_scale
    assert record.uncertainty_arcsec == pytest.approx(total, rel=1e-9)
    assert record.distance_upper_au - record.distance_au == pytest.approx((theory_km + place_km) / AU_KM, rel=1e-9)
    if scales.tt_bound_s > 0.0:
        assert time_scale > 0.0


def test_the_earths_documented_error_holds_from_the_first_instant_of_its_span_and_the_bound_is_read_only() -> None:
    """eraEpv00's documented span starts at 1900-01-01 TT, JD 2415020.5: from that instant on (however the TT pair is
    split) the Earth's error is the documented 11.2 km, and one step before it three times that. The bound's parts are
    read only once made."""
    moment = "1900-01-01T12:00:00Z"
    sun = ephemeris.BODY_BY_NAME["Sun"]
    scales = time_scales(moment)
    astrom = observer_astrom(moment, 30.0, -40.0)
    observer = np.asarray(astrom["eb"], dtype=np.float64)
    vector, helio = ephemeris._astrometric_au(sun, scales.tt, observer)
    for tt, earth_km in (((2415020.5, 0.0), 11.2), ((2415020.0, 0.5), 11.2), ((2415020.5, -1e-9), 33.6)):
        bound = ephemeris._bound(sun, tt, vector, helio, vector, observer, astrom, True, 0.0, 0.0)
        assert bound.distance_km == pytest.approx(earth_km + HEIGHT_KM, rel=1e-12), tt
    with pytest.raises(AttributeError):
        bound.theory_arcsec = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("topocentric", [True, False])
def test_each_record_says_where_it_is_seen_from_in_its_own_words(topocentric: bool) -> None:
    """The second limitation of every body's record names its frame: from the place; from the Earth's centre, the
    Moon with its parallax shift and every other body with the place's parallax held in its uncertainty."""
    records = ephemeris.compute_solar_system_ephemerides(
        "2031-08-30T03:01:00Z", 30.0, -40.0, bodies=tuple(body.name for body in ephemeris.BODIES),
        topocentric=topocentric)
    for record in records:
        seen_from = record.limitations[1]
        if topocentric:
            assert seen_from.startswith("Seen from the given place at sea level (topocentric)"), record.name
        elif record.object_type == "moon":
            assert seen_from.startswith("Geocentric: from this place the Moon appears about "), record.name
        else:
            assert seen_from.startswith("Seen from the Earth's centre: its uncertainty holds the place's parallax"), (
                record.name)
        assert ("Moon" in seen_from) == (record.object_type == "moon" and not topocentric), record.name
