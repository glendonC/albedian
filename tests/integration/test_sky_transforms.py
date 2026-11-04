"""The sky transforms: ICRS to the horizon and back, and the frame of the predicted Moon.

`icrs_to_altaz` precesses an ICRS direction (IAU 1976) to the mean equator and equinox of date before turning
it to the horizon, and `altaz_to_icrs` undoes both. The reference is the solver lane's benchmark astrometry
(research/benchmarks/phone_photos), which tests/science checks against ERFA: its observed frame with refraction
off also applies nutation and annual aberration, which the backend leaves out, so the two agree only to within
those (at most 37 arcsec over 2017 to 2050, measured). The benchmark's TT table starts in 2017, so the tests do.

The ephemeris Moon comes from Meeus (1998) chapter 47 on the mean equinox of date and must be precessed to ICRS
like everything else; the reference is the benchmark's Moon, the same chapter with more terms.

Covers:
- The precession matrix equals ERFA's eraPmat76 test value
- PBT: `icrs_to_altaz` lands within 40 arcsec of the benchmark's airless observed direction
- PBT: `altaz_to_icrs` inverts `icrs_to_altaz`
- PBT: the ephemeris Moon is an ICRS direction, within its truncation error of the benchmark Moon
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from hypothesis import assume, example, given, settings, strategies as st
import numpy as np

from research.benchmarks.phone_photos import astrometry as am
from research.benchmarks.phone_photos import scene as sc
from services.science import ephemeris
from services.science.catalog import altaz_to_icrs, icrs_to_altaz, precession_matrix_iau1976, ra_dec_to_unit_vector

START = datetime(2017, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2050, 1, 1, tzinfo=timezone.utc) - START).total_seconds()
# Nutation and annual aberration, which the benchmark applies and the backend leaves out: at most 37 arcsec measured
TRANSFORM_TOLERANCE_ARCSEC = 40.0
# The six-term Meeus Moon against the benchmark's 20-term one: at most 1,216 arcsec over 200,000 instants from
# 2017 to 2050, one every 1.45 h, measured; the benchmark Moon is itself within 0.02 degree of ERFA's
MOON_TOLERANCE_ARCSEC = 1300.0

instant = st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s))


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sep_arcsec(u: np.ndarray, v: np.ndarray) -> float:
    u, v = np.ravel(u) / np.linalg.norm(u), np.ravel(v) / np.linalg.norm(v)
    return math.degrees(math.atan2(np.linalg.norm(np.cross(u, v)), float(u @ v))) * 3600.0


def _enu(alt_deg: float, az_deg: float) -> np.ndarray:
    alt, az = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(alt) * math.sin(az), math.cos(alt) * math.cos(az), math.sin(alt)])


def test_precession_matrix_matches_erfa_pmat76() -> None:
    # ERFA's own test of eraPmat76 (t_erfa_c.c), the same value tests/science pins for the benchmark
    expected = np.array(
        [
            [0.9999995504328350733, 0.8696632209480960785e-3, 0.3779153474959888345e-3],
            [-0.8696632209485112192e-3, 0.9999996218428560614, -0.1643284776111886407e-6],
            [-0.3779153474950335077e-3, -0.1643306746147366896e-6, 0.9999999285899790119],
        ]
    )
    np.testing.assert_allclose(precession_matrix_iau1976(2400000.5 + 50123.9999), expected, rtol=0, atol=1e-12)


@settings(max_examples=200, deadline=None)
@given(
    when=instant,
    lat_deg=st.floats(min_value=-80.0, max_value=80.0),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    ra_deg=st.floats(min_value=0.0, max_value=359.999),
    sin_dec=st.floats(min_value=-1.0, max_value=1.0),
)
@example(when=datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc), lat_deg=12.5, lon_deg=45.25, ra_deg=88.79, sin_dec=0.129)
@example(when=datetime(2049, 6, 1, 0, 0, tzinfo=timezone.utc), lat_deg=-33.0, lon_deg=-70.0, ra_deg=101.29, sin_dec=-0.288)
def test_pbt_icrs_to_altaz_matches_the_erfa_tested_frame_without_nutation_and_aberration(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    frame = am.ObservationFrame(utc=when, lat_deg=lat_deg, lon_deg=lon_deg, pressure_hpa=0.0)
    az_ref, alt_ref = frame.altaz(ra_deg, dec_deg)
    assume(float(alt_ref[0]) > 0.0)
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when))
    assert _sep_arcsec(_enu(alt_deg, az_deg), _enu(float(alt_ref[0]), float(az_ref[0]))) <= TRANSFORM_TOLERANCE_ARCSEC


@settings(max_examples=200, deadline=None)
@given(
    when=instant,
    lat_deg=st.floats(min_value=-80.0, max_value=80.0),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    ra_deg=st.floats(min_value=0.0, max_value=359.999),
    sin_dec=st.floats(min_value=-0.999, max_value=0.999),
)
def test_pbt_altaz_to_icrs_inverts_icrs_to_altaz(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when))
    ra_back, dec_back = altaz_to_icrs(alt_deg, az_deg, lat_deg, lon_deg, _iso(when))
    back = np.array(ra_dec_to_unit_vector(ra_back, dec_back))
    assert _sep_arcsec(back, np.array(ra_dec_to_unit_vector(ra_deg, dec_deg))) < 1e-6


@settings(max_examples=200, deadline=None)
@given(when=instant)
@example(when=datetime(2049, 6, 1, 0, 0, tzinfo=timezone.utc))
def test_pbt_the_ephemeris_moon_is_an_icrs_direction(when: datetime) -> None:
    jd_utc = am.julian_date(when)
    jd_tt = jd_utc + am.tt_minus_utc_seconds(jd_utc) / 86400.0
    moon = next(f for f in ephemeris.compute_solar_system_ephemerides(_iso(when), 0.0, 0.0, bodies=("Moon",)))
    reference = sc.moon_geocentric_icrs_km(jd_tt)
    assert _sep_arcsec(np.array(ra_dec_to_unit_vector(moon.ra_deg, moon.dec_deg)), reference) <= MOON_TOLERANCE_ARCSEC
