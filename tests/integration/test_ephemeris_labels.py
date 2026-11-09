"""The ephemeris says what it computes and how far it can be off.

The planets come from the approximate Keplerian elements of Standish and Williams (1992) that JPL's Solar System
Dynamics group publishes for 1800 to 2050, with nominal heliocentric errors per planet; the Moon from Meeus
(1998) chapter 47 cut to its 6 largest longitude, 4 latitude and 4 distance terms, seen from the Earth's centre.
No JPL Horizons request and no DE440 file stands behind either, so no output may say so, and no fixed
magnitude may pass for the one at the moment.

Covers:
- No ephemeris output names JPL Horizons or DE440, carries a catalog reference, or states a magnitude
- PBT: a planet's stated uncertainty covers the published heliocentric error carried to the geocentre plus the
  light time the model leaves out, and its distance bounds hold the published distance error
- PBT: the Moon's stated uncertainty and distance bounds cover its error against the benchmark Moon, and it
  says it is geocentric with that moment's parallax shift
- The horizon solver claims no QuickTime source for a typed moment, places predicted stars at their position
  of the date, and its pose warning says the Moon it uses is geocentric
- The sky-context route sends no fixed magnitude
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import numpy as np

from research.benchmarks.phone_photos import astrometry as am
from research.benchmarks.phone_photos import scene as sc
from services.api import app
from services.science import ephemeris
from services.science.catalog import get_all_sky_catalog_stars, propagate_star_epoch, ra_dec_to_unit_vector
from services.science.horizon_solver import solve_horizon_and_beacon_pose

START = datetime(2017, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2050, 1, 1, tzinfo=timezone.utc) - START).total_seconds()
PLANETS = ("Venus", "Mars", "Jupiter", "Saturn", "Uranus")
# Heliocentric nominal errors for 1800 to 2050 (arcsec in longitude, arcsec in latitude, 1000 km in distance),
# JPL SSD "Approximate Positions of the Planets" (Standish and Williams 1992)
PUBLISHED = {"Venus": (20, 1, 4), "Mars": (40, 2, 25), "Jupiter": (400, 10, 600), "Saturn": (600, 25, 1500), "Uranus": (50, 2, 1000)}
EARTH_MOON_BARYCENTRE = (20, 8, 6)  # the same table's row for the point the planets are seen from
BARYCENTRE_TO_GEOCENTRE_KM = 5000.0  # up to 406,700 km / 82.3 (Moon's greatest distance over 1 + Earth/Moon mass ratio)
AU_KM = 149597870.7
C_AU_PER_DAY = 173.1446326846693
# A synthetic moment and place that point at nobody (as in test_no_default_moment): the Moon at 33 degrees there
MOON_MOMENT = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}

instant = st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s))


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sep_arcsec(u, v) -> float:
    u, v = np.ravel(u) / np.linalg.norm(u), np.ravel(v) / np.linalg.norm(v)
    return math.degrees(math.atan2(np.linalg.norm(np.cross(u, v)), float(u @ v))) * 3600.0


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


@settings(max_examples=100, deadline=None)
@given(when=instant, body=st.sampled_from(PLANETS))
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc), body="Jupiter")
@example(when=datetime(2026, 10, 1, tzinfo=timezone.utc), body="Venus")
def test_pbt_planet_uncertainty_covers_the_published_error_and_the_omitted_light_time(when: datetime, body: str) -> None:
    (item,) = ephemeris.compute_solar_system_ephemerides(_iso(when), 0.0, 0.0, bodies=(body,))
    t = (am.julian_date(when) - 2451545.0) / 36525.0
    earth = np.array(ephemeris._heliocentric_ecliptic_xyz_au(ephemeris.PLANET_ELEMENTS["Earth"], t))
    planet = np.array(ephemeris._heliocentric_ecliptic_xyz_au(ephemeris.PLANET_ELEMENTS[body], t))
    geo = planet - earth
    delta = float(np.linalg.norm(geo))
    tau_centuries = delta / C_AU_PER_DAY / 36525.0
    earlier = np.array(ephemeris._heliocentric_ecliptic_xyz_au(ephemeris.PLANET_ELEMENTS[body], t - tau_centuries))
    light_time_arcsec = _sep_arcsec(geo, earlier - earth)
    lon, lat, dist = PUBLISHED[body]
    e_lon, e_lat, e_dist = EARTH_MOON_BARYCENTRE
    # Each displacement the published errors allow, at its own distance, as if all fell across the line of sight
    displacement_au = (
        float(np.linalg.norm(planet)) * math.radians(math.hypot(lon, lat) / 3600.0)
        + dist * 1000.0 / AU_KM
        + float(np.linalg.norm(earth)) * math.radians(math.hypot(e_lon, e_lat) / 3600.0)
        + e_dist * 1000.0 / AU_KM
        + BARYCENTRE_TO_GEOCENTRE_KM / AU_KM
        + ephemeris.EARTH_EQUATORIAL_RADIUS_KM / AU_KM
    )
    published_arcsec = math.degrees(displacement_au / delta) * 3600.0
    assert item.uncertainty_arcsec >= published_arcsec + light_time_arcsec - 1e-9
    assert item.distance_lower_au <= item.distance_au - dist * 1000.0 / AU_KM
    assert item.distance_upper_au >= item.distance_au + dist * 1000.0 / AU_KM
    payload = item.to_contract_dict(source_sha256=None)
    assert payload["skyPosition"]["uncertaintyArcsec"] == item.uncertainty_arcsec
    assert "Standish and Williams (1992)" in payload["evidence"]["verificationMethod"]
    limitations_text = " ".join(payload["evidence"]["limitations"])
    assert "5,000 km" in limitations_text and "6,378.137 km" in limitations_text


@settings(max_examples=100, deadline=None)
@given(when=instant)
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc))
def test_pbt_moon_bounds_cover_its_error_and_it_says_it_is_geocentric(when: datetime) -> None:
    (moon,) = ephemeris.compute_solar_system_ephemerides(_iso(when), 30.0, -40.0, bodies=("Moon",))
    jd_utc = am.julian_date(when)
    reference = sc.moon_geocentric_icrs_km(jd_utc + am.tt_minus_utc_seconds(jd_utc) / 86400.0)
    assert _sep_arcsec(np.array(ra_dec_to_unit_vector(moon.ra_deg, moon.dec_deg)), reference) <= moon.uncertainty_arcsec
    assert moon.distance_lower_au * AU_KM <= float(np.linalg.norm(reference)) <= moon.distance_upper_au * AU_KM
    payload = moon.to_contract_dict(source_sha256=None)
    assert "Meeus (1998) chapter 47" in payload["evidence"]["verificationMethod"]
    # _compute_moon_geocentric_icrs keeps 6 periodic longitude, 4 latitude and 4 distance terms
    assert "6 largest longitude, 4 latitude and 4 distance terms" in payload["evidence"]["verificationMethod"]
    shift = math.degrees(math.asin(6378.137 / (moon.distance_au * AU_KM))) * math.cos(math.radians(moon.alt_deg)) * 3600
    geocentric = [text for text in payload["evidence"]["limitations"] if "geocentric" in text.lower()]
    assert geocentric and f"{shift:,.0f} arcsec" in geocentric[0]


def _night_frame(height: int, width: int) -> np.ndarray:
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    frame[118:126, 930:938] = 240.0
    return frame


def test_horizon_solver_claims_no_quicktime_source_and_places_stars_at_the_date() -> None:
    frame = _night_frame(1792, 1344)
    sol = solve_horizon_and_beacon_pose(
        frame, **MOON_MOMENT, source_asset_id="synthetic-night", source_sha256=hashlib.sha256(frame.tobytes()).hexdigest()
    )
    assert sol.status == "approximate"
    assert not any("QuickTime" in limitation for f in sol.findings for limitation in f.limitations)
    assert any("geocentric" in warning.lower() for warning in sol.warnings)
    # Every predicted star sits where its proper motion has carried it by the date, not at its J2000 position
    year = 2031.0 + (datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc) - datetime(2031, 1, 1, tzinfo=timezone.utc)).days / 365.25
    by_id = {f.id: f for f in sol.findings}
    checked, largest_motion = 0, 0.0
    for star in get_all_sky_catalog_stars():
        finding = by_id.get(f"finding-predicted-{star.id}")
        if finding is None:
            continue
        moved = propagate_star_epoch(star, year)
        position = np.array(ra_dec_to_unit_vector(finding.ra_deg, finding.dec_deg))
        assert _sep_arcsec(position, np.array(ra_dec_to_unit_vector(moved.ra_deg, moved.dec_deg))) < 0.5, star.common_name
        largest_motion = max(largest_motion, _sep_arcsec(position, np.array(ra_dec_to_unit_vector(star.ra_deg, star.dec_deg))))
        checked += 1
    assert checked >= 10 and largest_motion > 10.0


def test_sky_context_sends_no_fixed_magnitude() -> None:
    response = TestClient(app).post(
        "/api/science/sky-context", json={"observedAtUTC": "2031-08-30T03:01:00Z", "latDeg": 30.0, "lonDeg": -40.0}
    )
    assert response.status_code == 200
    assert all("apparentMagnitude" not in body for body in response.json()["ephemerides"])
