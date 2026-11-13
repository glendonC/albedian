"""The sky transforms: ICRS to the horizon and back, on ERFA, and refraction.

`icrs_to_altaz` takes an ICRS direction through ERFA's eraAtciq and eraAtioq (light deflection by the Sun, annual and
diurnal aberration, IAU 2006/2000A precession and nutation, the Earth's rotation), and `altaz_to_icrs` undoes it with
eraAtoiq and eraAticq. The reference is the solver lane's benchmark astrometry (research/benchmarks/phone_photos),
an independent chain that tests/science checks against ERFA's test values: its airless observed frame states what it
leaves out, and the two agree to within those. The benchmark's TT table starts in 2017, so the tests do. The
ephemeris Moon's frame is checked against JPL Horizons' ICRF positions in tests/integration/test_ephemeris_horizons.py:
the benchmark's 20-term Moon, the reference here before, is coarser than the Moon's stated uncertainty now.

Covers:
- The precession matrix equals ERFA's eraPmat76 test value
- PBT: `icrs_to_altaz` lands within the benchmark's stated omissions (0.84 arcsec) plus the light deflection it applies
  (`solar_deflection_arcsec`) of the benchmark's airless observed direction, a direction inside the Sun's disk included
- The deflection is ERFA's restrained one near the Sun: 0 at its centre, at most about 5.8 arcsec near ERFA's 5 arcmin
  threshold, about 1.75 at the limb, and it is what `icrs_to_altaz` applies
- PBT: `altaz_to_icrs` inverts `icrs_to_altaz`, airless and refracted
- A plate solve answers for every moment main answered for: POST /api/science/solve calibrates the synthetic field
  (seed 42, flux 1100, as test_unregistered_detections) at 1790, 1500 and the calendar's ends (years 1 and 9999), as
  it does in 2030, with the same catalog matches, each with an altitude
- PBT: from year 1 to 9999, the range main took, `icrs_to_altaz` and `altaz_to_icrs` return finite values, invert each
  other, and the moment's time scales state a positive bound before 1960 and after mid 2027
- `refraction_deg` is Saemundsson's formula: 0 at the zenith, about 29 arcmin at a true altitude of 0 and about
  34 arcmin where the body appears on the horizon, held below -1 degree; `airless_altitude_deg` undoes it
- PBT, 1800 to 2050, seeded with a direction near the Sun: the rigid frame rotation (`rigid_horizon_rotation`) is a
  rotation (orthonormal, determinant one), turns a direction as ERFA's bias-precession-nutation matrix and eraAtioq do,
  keeps every angle, round-trips within 1e-9 arcsec, and misses the apparent place by no more than its stated
  aberration and deflection, so by no more than RIGID_FRAME_OMISSION_ARCSEC; the apparent place is not rigid
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math

from hypothesis import assume, example, given, settings, strategies as st
import numpy as np
import pytest

import erfa
from fastapi.testclient import TestClient

from research.benchmarks.phone_photos import astrometry as am
from services.api import app
from services.science.catalog import (
    RIGID_ABERRATION_MAX_ARCSEC,
    DEFLECTION_PEAK_WITHIN_DEG,
    RIGID_DEFLECTION_MAX_ARCSEC,
    SECOND_ORDER_ABERRATION_ARCSEC,
    solar_deflection_at_separation_arcsec,
    RIGID_FRAME_OMISSION_ARCSEC,
    airless_altitude_deg,
    altaz_to_icrs,
    anchored_horizon_rotation,
    anchored_rotation_omission_arcsec,
    icrs_to_altaz,
    icrs_to_frame_altaz,
    icrs_to_rigid_altaz,
    precession_matrix_iau1976,
    ra_dec_to_unit_vector,
    observer_astrom,
    refraction_deg,
    rigid_altaz_to_icrs,
    rigid_horizon_rotation,
    solar_deflection_arcsec,
    time_scales,
    unit_vector_to_ra_dec,
)
from services.science.solver import detect_point_sources, render_synthetic_starfield

START = datetime(2017, 1, 1, tzinfo=timezone.utc)
SPAN_S = (datetime(2050, 1, 1, tzinfo=timezone.utc) - START).total_seconds()
# What the benchmark leaves out and the backend applies, as its docstring states them: frame bias (0.02 arcsec),
# diurnal aberration (under 0.32 arcsec), nutation by Meeus's short series (good to 0.5 arcsec in longitude); both take
# UT1 as UTC. The light deflection by the Sun, which the benchmark also leaves out, is added for each direction as
# ERFA applies it (`solar_deflection_arcsec`: about 5.8 arcsec at most, near the Sun)
BENCHMARK_OMISSIONS_ARCSEC = 0.02 + 0.32 + 0.5

instant = st.floats(min_value=0.0, max_value=SPAN_S).map(lambda s: START + timedelta(seconds=s))


def _iso(when: datetime) -> str:
    # The year written with four digits by hand: strftime's %Y does not pad years below 1000 on every platform
    return f"{when.year:04d}{when:-%m-%dT%H:%M:%S.%f}Z"


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
# The second audit's case: a direction 142 arcsec from the Sun's centre, inside its disk, at a March equinox
@example(when=datetime(2036, 3, 20, 14, 2, 40, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0, ra_deg=0.0, sin_dec=0.0)
def test_pbt_icrs_to_altaz_matches_the_benchmarks_airless_observed_frame(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    frame = am.ObservationFrame(utc=when, lat_deg=lat_deg, lon_deg=lon_deg, pressure_hpa=0.0)
    az_ref, alt_ref = frame.altaz(ra_deg, dec_deg)
    assume(float(alt_ref[0]) > 0.0)
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when))
    tolerance = BENCHMARK_OMISSIONS_ARCSEC + solar_deflection_arcsec(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when))
    assert _sep_arcsec(_enu(alt_deg, az_deg), _enu(float(alt_ref[0]), float(az_ref[0]))) <= tolerance


@settings(max_examples=200, deadline=None)
@given(
    when=instant,
    lat_deg=st.floats(min_value=-80.0, max_value=80.0),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    ra_deg=st.floats(min_value=0.0, max_value=359.999),
    sin_dec=st.floats(min_value=-0.999, max_value=0.999),
)
@example(when=datetime(2036, 3, 20, 14, 2, 40, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0, ra_deg=0.0, sin_dec=0.0)
def test_pbt_altaz_to_icrs_inverts_icrs_to_altaz(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when))
    ra_back, dec_back = altaz_to_icrs(alt_deg, az_deg, lat_deg, lon_deg, _iso(when))
    back = np.array(ra_dec_to_unit_vector(ra_back, dec_back))
    assert _sep_arcsec(back, np.array(ra_dec_to_unit_vector(ra_deg, dec_deg))) < 1e-6


@settings(max_examples=200, deadline=None)
@given(
    when=instant,
    lat_deg=st.floats(min_value=-80.0, max_value=80.0),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    ra_deg=st.floats(min_value=0.0, max_value=359.999),
    sin_dec=st.floats(min_value=-0.999, max_value=0.999),
)
@example(when=datetime(2036, 3, 20, 14, 2, 40, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0, ra_deg=0.0, sin_dec=0.0)
def test_pbt_altaz_to_icrs_inverts_the_refracted_icrs_to_altaz(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, _iso(when), refracted=True)
    ra_back, dec_back = altaz_to_icrs(alt_deg, az_deg, lat_deg, lon_deg, _iso(when), refracted=True)
    back = np.array(ra_dec_to_unit_vector(ra_back, dec_back))
    assert _sep_arcsec(back, np.array(ra_dec_to_unit_vector(ra_deg, dec_deg))) < 1e-6


def test_refraction_is_saemundssons_formula_and_its_inverse_undoes_it() -> None:
    # R = 1.02 cot(h + 10.3 / (h + 5.11)) arcminutes for a true altitude h in degrees
    for h in (-0.5, 0.0, 2.0, 10.0, 45.0):
        expected = 1.02 / math.tan(math.radians(h + 10.3 / (h + 5.11))) / 60.0
        assert refraction_deg(h) == pytest.approx(max(0.0, expected), abs=1e-12)
    # At h = 0: 1.02 cot(2.0157 degrees) = 28.98 arcminutes; a body that appears on the horizon is 34.4 arcminutes
    # below it
    assert refraction_deg(0.0) * 60.0 == pytest.approx(28.98, abs=0.01)
    assert airless_altitude_deg(0.0) * 60.0 == pytest.approx(-34.4, abs=0.1)
    assert refraction_deg(90.0) == 0.0
    # Held below -1 degree
    assert refraction_deg(-3.0) == refraction_deg(-1.0)
    for h in (-2.0, -1.0, 0.0, 0.3, 5.0, 30.0, 89.0):
        assert airless_altitude_deg(h + refraction_deg(h)) == pytest.approx(h, abs=1e-11)


def _solve(moment: str) -> dict:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    detections = detect_point_sources(frame, snr_threshold=4.0)
    digest = hashlib.sha256(frame.tobytes()).hexdigest()
    response = TestClient(app).post(
        "/api/science/solve",
        json={
            "width": 960, "height": 540, "observedAtUTC": moment, "latDeg": 30.0, "lonDeg": -40.0,
            "sourceAssetId": f"src_{digest[:16]}", "sourceSha256": digest,
            "detections": [
                {"x_px": d.x_px, "y_px": d.y_px, "flux": d.flux, "snr": d.snr, "fwhm_px": d.fwhm_px,
                 "centroid_uncertainty_px": d.centroid_uncertainty_px}
                for d in detections
            ],
        },
    )
    assert response.status_code == 200, (moment, response.status_code)
    return response.json()


@pytest.mark.parametrize("moment", ["1790-01-01T02:00:00Z", "1500-06-01T02:00:00Z", "0001-01-01T00:00:00Z",
                                    "9999-12-31T23:59:59Z"])
def test_a_plate_solve_answers_before_1800_and_at_the_ends_of_the_calendar(moment: str) -> None:
    def matched(solution: dict) -> list[tuple[str, float]]:
        return sorted(
            (f["id"], f["skyPosition"]["altDeg"]) for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"
        )

    reference = matched(_solve("2030-01-01T02:00:00Z"))
    solution = _solve(moment)
    assert solution["status"] == "calibrated"
    stars = matched(solution)
    assert [star for star, _ in stars] == [star for star, _ in reference] and len(stars) >= 10
    assert all(math.isfinite(alt) for _, alt in stars)


@settings(max_examples=200, deadline=None)
@given(
    when=st.datetimes(min_value=datetime(1, 1, 2), max_value=datetime(9999, 12, 30), timezones=st.just(timezone.utc)),
    lat_deg=st.floats(min_value=-80.0, max_value=80.0),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    ra_deg=st.floats(min_value=0.0, max_value=359.999),
    sin_dec=st.floats(min_value=-0.999, max_value=0.999),
)
@example(when=datetime(1790, 1, 1, 2, tzinfo=timezone.utc), lat_deg=30.0, lon_deg=-40.0, ra_deg=88.79, sin_dec=0.129)
@example(when=datetime(1, 1, 2, tzinfo=timezone.utc), lat_deg=30.0, lon_deg=-40.0, ra_deg=101.29, sin_dec=-0.288)
@example(when=datetime(9999, 12, 30, tzinfo=timezone.utc), lat_deg=-60.0, lon_deg=0.0, ra_deg=10.0, sin_dec=0.5)
def test_pbt_the_transforms_answer_for_every_moment_main_accepted(
    when: datetime, lat_deg: float, lon_deg: float, ra_deg: float, sin_dec: float
) -> None:
    dec_deg = math.degrees(math.asin(sin_dec))
    iso = _iso(when)
    alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, iso)
    assert math.isfinite(alt_deg) and math.isfinite(az_deg)
    ra_back, dec_back = altaz_to_icrs(alt_deg, az_deg, lat_deg, lon_deg, iso)
    back = np.array(ra_dec_to_unit_vector(ra_back, dec_back))
    assert _sep_arcsec(back, np.array(ra_dec_to_unit_vector(ra_deg, dec_deg))) < 1e-6
    scales = time_scales(iso)
    if when.year < 1960 or when >= datetime(2027, 7, 1, tzinfo=timezone.utc):
        assert math.isfinite(scales.tt_bound_s) and scales.tt_bound_s > 0.0


def test_the_light_deflection_is_erfas_restrained_value_near_the_sun_and_what_icrs_to_altaz_applies() -> None:
    # The second audit's moment and place; directions stepped away from the Sun's centre along one great circle
    iso, lat, lon = "2036-03-20T14:02:40.000000Z", 0.0, 1.0
    astrom = observer_astrom(iso, lat, lon)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    side = np.cross(sun, [0.0, 0.0, 1.0])
    side /= np.linalg.norm(side)

    def direction(arcsec: float) -> tuple[float, float]:
        angle = math.radians(arcsec / 3600.0)
        return unit_vector_to_ra_dec(math.cos(angle) * sun + math.sin(angle) * side)

    deflection = {s: solar_deflection_arcsec(*direction(s), lat, lon, iso) for s in (0.0, 10.0, 142.0, 292.0, 964.0, 3600.0)}
    # ERFA's notes: "falling to zero deflection for zero separation", restrained below a threshold of "about 5
    # arcminutes" for an observer on the Earth, then the familiar 1.75 arcsec at the limb, less beyond
    assert deflection[0.0] < 1e-6
    assert deflection[10.0] < deflection[142.0] < deflection[292.0]
    assert deflection[292.0] > deflection[964.0] > deflection[3600.0]
    assert 1.70 <= deflection[964.0] <= 1.80
    # At most the docstring's "about 5.8 arcsec", with the 1.7 percent the Earth's distance from the Sun moves it
    assert max(solar_deflection_arcsec(*direction(s), lat, lon, iso) for s in range(100, 600, 2)) <= 5.8 * 1.034
    # It is the deflection icrs_to_altaz applies: ERFA's chain with it is icrs_to_altaz, and without it misses by it
    for arcsec in (142.0, 292.0, 3600.0):
        ra, dec = direction(arcsec)
        p = np.array(ra_dec_to_unit_vector(ra, dec))
        for deflect, expected in ((True, 0.0), (False, deflection[arcsec])):
            q = erfa.ldsun(p, astrom["eh"], astrom["em"]) if deflect else p
            ri, di = erfa.c2s(astrom["bpn"] @ erfa.ab(q, astrom["v"], astrom["em"], astrom["bm1"]))
            azimuth, zenith_distance, _, _, _ = erfa.atioq(ri, di, astrom)
            manual = _enu(90.0 - math.degrees(float(zenith_distance)), math.degrees(float(azimuth)))
            assert _sep_arcsec(_enu(*icrs_to_altaz(ra, dec, lat, lon, iso)), manual) == pytest.approx(expected, rel=0.01, abs=1e-6)


# The rigid frame rotation is stated for 1800 to 2050, the span the predicted sky and the pose take
RIGID_START = datetime(1800, 1, 1, tzinfo=timezone.utc)
rigid_instant = st.floats(
    min_value=0.0, max_value=(datetime(2050, 12, 31, tzinfo=timezone.utc) - RIGID_START).total_seconds()
).map(lambda s: RIGID_START + timedelta(seconds=s))
# A direction anywhere, or one `offset` arcsec from the Sun's centre at a position angle (near ERFA's 5 arcmin
# threshold the deflection peaks, about 5.9 arcsec)
DIRECTIONS = st.one_of(
    st.tuples(st.just("sky"), st.floats(min_value=0.0, max_value=359.999), st.floats(min_value=-0.999, max_value=0.999)),
    st.tuples(st.just("sun"), st.floats(min_value=0.0, max_value=1200.0), st.floats(min_value=0.0, max_value=359.999)),
)


def _direction(choice: tuple[str, float, float], lat: float, lon: float, iso: str) -> np.ndarray:
    kind, a, b = choice
    if kind == "sky":
        return np.array(ra_dec_to_unit_vector(a, math.degrees(math.asin(b))))
    # The Sun as seen from the place is opposite the astrom's heliocentric direction `eh`; move from it by `a` arcsec
    sun = -np.asarray(observer_astrom(iso, lat, lon)["eh"], dtype=np.float64)
    pole = np.array([0.0, 0.0, 1.0])
    east = np.cross(pole, sun)
    east = east / float(np.linalg.norm(east))
    north = np.cross(sun, east)
    offset, angle = math.radians(a / 3600.0), math.radians(b)
    return math.cos(offset) * sun + math.sin(offset) * (math.cos(angle) * north + math.sin(angle) * east)


@settings(max_examples=200, deadline=None)
@given(
    when=rigid_instant,
    lat_deg=st.floats(min_value=-89.9, max_value=89.9),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    first_choice=DIRECTIONS,
    second_choice=DIRECTIONS,
)
# Near the deflection's peak, and inside the Sun's disk (the case the second audit drew)
@example(when=datetime(2031, 1, 3, 12, 0, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0, first_choice=("sun", 292.0, 0.0),
         second_choice=("sky", 90.0, 0.5))
@example(when=datetime(2036, 3, 20, 14, 2, 40, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0,
         first_choice=("sun", 142.0, 90.0), second_choice=("sun", 1200.0, 270.0))
def test_pbt_the_rigid_frame_rotation_is_a_rotation_and_misses_the_apparent_place_by_at_most_its_stated_term(
    when: datetime, lat_deg: float, lon_deg: float, first_choice: tuple[str, float, float],
    second_choice: tuple[str, float, float],
) -> None:
    iso = _iso(when)
    rotation = rigid_horizon_rotation(lat_deg, lon_deg, iso)
    assert np.max(np.abs(rotation @ rotation.T - np.eye(3))) < 1e-12
    assert abs(float(np.linalg.det(rotation)) - 1.0) < 1e-12
    astrom = observer_astrom(iso, lat_deg, lon_deg)
    first, second = _direction(first_choice, lat_deg, lon_deg, iso), _direction(second_choice, lat_deg, lon_deg, iso)
    # ERFA's own steps without aberration and deflection: the bias-precession-nutation matrix, then eraAtioq
    ri, di = erfa.c2s(astrom["bpn"] @ first)
    azimuth, zenith_distance, _, _, _ = erfa.atioq(erfa.anp(ri), di, astrom)
    assert _sep_arcsec(rotation @ first, _enu(90.0 - math.degrees(float(zenith_distance)),
                                              math.degrees(float(azimuth)))) < 1e-9
    ra, dec = unit_vector_to_ra_dec(first)
    alt, az = icrs_to_rigid_altaz(ra, dec, lat_deg, lon_deg, iso)
    assert _sep_arcsec(np.array(ra_dec_to_unit_vector(*rigid_altaz_to_icrs(alt, az, lat_deg, lon_deg, iso))), first) < 1e-9
    # Every angle is kept
    other_alt, other_az = icrs_to_rigid_altaz(*unit_vector_to_ra_dec(second), lat_deg, lon_deg, iso)
    assert abs(_sep_arcsec(_enu(alt, az), _enu(other_alt, other_az)) - _sep_arcsec(first, second)) < 1e-6
    # What it leaves out of the apparent place: the aberration by the observer's speed and ERFA's deflection there
    aberration = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    deflection = solar_deflection_arcsec(ra, dec, lat_deg, lon_deg, iso)
    assert float(astrom["diurab"]) == 0.0
    assert aberration <= RIGID_ABERRATION_MAX_ARCSEC and deflection <= RIGID_DEFLECTION_MAX_ARCSEC
    missed = _sep_arcsec(_enu(*icrs_to_altaz(ra, dec, lat_deg, lon_deg, iso)), _enu(alt, az))
    assert missed <= aberration + deflection + 0.01
    assert missed <= RIGID_FRAME_OMISSION_ARCSEC
    assert RIGID_ABERRATION_MAX_ARCSEC + RIGID_DEFLECTION_MAX_ARCSEC <= RIGID_FRAME_OMISSION_ARCSEC


def test_the_apparent_place_is_not_rigid_so_a_gnomonic_camera_needs_the_rigid_frame_rotation() -> None:
    """Two directions 55 degrees apart (the corner of the head-worn profile's widest field), one toward the observer's
    motion: aberration moves them by different amounts, so their angle at the horizon differs from the ICRS angle by
    several arcsec in the apparent place, while the rigid frame rotation keeps it."""
    iso, lat, lon = "2031-08-30T03:01:00Z", 30.0, -40.0
    astrom = observer_astrom(iso, lat, lon)
    apex_ra, apex_dec = (math.degrees(v) for v in erfa.c2s(np.asarray(astrom["v"], dtype=np.float64)))
    # 55 degrees from the apex along its meridian (over the pole if need be)
    other = unit_vector_to_ra_dec(np.asarray(erfa.s2c(math.radians(apex_ra), math.radians(apex_dec + 55.0))))
    on_sky = _sep_arcsec(np.array(ra_dec_to_unit_vector(apex_ra % 360.0, apex_dec)),
                         np.array(ra_dec_to_unit_vector(*other)))
    assert on_sky == pytest.approx(55.0 * 3600.0, abs=1e-6)
    apparent = abs(_sep_arcsec(_enu(*icrs_to_altaz(apex_ra % 360.0, apex_dec, lat, lon, iso)),
                               _enu(*icrs_to_altaz(*other, lat, lon, iso))) - on_sky)
    rigid = abs(_sep_arcsec(_enu(*icrs_to_rigid_altaz(apex_ra % 360.0, apex_dec, lat, lon, iso)),
                            _enu(*icrs_to_rigid_altaz(*other, lat, lon, iso))) - on_sky)
    assert apparent > 5.0 and rigid < 1e-6, (apparent, rigid)




def _offset(centre: np.ndarray, angle_deg: float, bearing_deg: float) -> np.ndarray:
    """The direction `angle_deg` from `centre` along `bearing_deg` (from the pole's side)."""
    pole = np.array([0.0, 0.0, 1.0]) if abs(centre[2]) < 0.99 else np.array([1.0, 0.0, 0.0])
    east = np.cross(pole, centre)
    east = east / float(np.linalg.norm(east))
    north = np.cross(centre, east)
    a, b = math.radians(angle_deg), math.radians(bearing_deg)
    return math.cos(a) * centre + math.sin(a) * (math.cos(b) * north + math.sin(b) * east)


@settings(max_examples=200, deadline=None)
@given(
    when=rigid_instant,
    lat_deg=st.floats(min_value=-89.9, max_value=89.9),
    lon_deg=st.floats(min_value=-180.0, max_value=180.0),
    beacon=DIRECTIONS,
    centre_angle_deg=st.floats(min_value=0.0, max_value=60.0),
    centre_bearing_deg=st.floats(min_value=0.0, max_value=359.999),
    radius_deg=st.floats(min_value=1.0, max_value=55.0),
    edge=st.floats(min_value=0.0, max_value=1.0),
    bearing_deg=st.floats(min_value=0.0, max_value=359.999),
)
# A beacon near the deflection's peak; a field reaching its widest, the beacon at its far edge (the corners of the
# head-worn profile's widest frame lie 55 degrees from its centre)
@example(when=datetime(2031, 1, 3, 12, 0, tzinfo=timezone.utc), lat_deg=0.0, lon_deg=1.0, beacon=("sun", 292.0, 0.0),
         centre_angle_deg=0.0, centre_bearing_deg=0.0, radius_deg=10.0, edge=1.0, bearing_deg=90.0)
@example(when=datetime(2031, 8, 30, 3, 1, tzinfo=timezone.utc), lat_deg=30.0, lon_deg=-40.0, beacon=("sky", 10.0, 0.3),
         centre_angle_deg=55.0, centre_bearing_deg=45.0, radius_deg=55.0, edge=1.0, bearing_deg=225.0)
# A beacon inside the Sun's disk, outside its field and nearer the Sun than any of it (a random seed found it on
# 2026-11-12: the beacon's own deflection is larger than the field's)
@example(when=RIGID_START, lat_deg=0.0, lon_deg=0.0, beacon=("sun", 104.0, 0.0), centre_angle_deg=3.0,
         centre_bearing_deg=0.0, radius_deg=1.0, edge=1.0, bearing_deg=0.0)
def test_pbt_the_rotation_anchored_on_a_beacon_misses_every_direction_of_its_field_by_at_most_the_stated_term(
    when: datetime, lat_deg: float, lon_deg: float, beacon: tuple[str, float, float], centre_angle_deg: float,
    centre_bearing_deg: float, radius_deg: float, edge: float, bearing_deg: float,
) -> None:
    """`anchored_horizon_rotation` is a rotation that puts the beacon exactly where it is seen, and turns a direction
    of a field (a centre and a radius around it) to within `anchored_rotation_omission_arcsec` of its apparent place,
    a direction at the field's edge included; the field's centre lies anywhere within 60 degrees of the beacon."""
    iso = _iso(when)
    m = _direction(beacon, lat_deg, lon_deg, iso)
    centre = _offset(m, centre_angle_deg, centre_bearing_deg)
    n = _offset(centre, edge * radius_deg, bearing_deg)
    beacon_ra, beacon_dec = unit_vector_to_ra_dec(m)
    rotation = anchored_horizon_rotation(beacon_ra, beacon_dec, lat_deg, lon_deg, iso)
    assert np.max(np.abs(rotation @ rotation.T - np.eye(3))) < 1e-12 and abs(float(np.linalg.det(rotation)) - 1.0) < 1e-12
    assert _sep_arcsec(_enu(*icrs_to_frame_altaz(rotation, beacon_ra, beacon_dec)),
                       _enu(*icrs_to_altaz(beacon_ra, beacon_dec, lat_deg, lon_deg, iso))) < 1e-6
    term = anchored_rotation_omission_arcsec(beacon_ra, beacon_dec, [(*unit_vector_to_ra_dec(centre), radius_deg)],
                                             lat_deg, lon_deg, iso)
    ra, dec = unit_vector_to_ra_dec(n)
    missed = _sep_arcsec(_enu(*icrs_to_frame_altaz(rotation, ra, dec)), _enu(*icrs_to_altaz(ra, dec, lat_deg, lon_deg, iso)))
    assert missed <= term, (missed, term)


def _rotation_taking(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The least rotation taking the unit vector a to the unit vector b."""
    axis = np.cross(a, b)
    s, c = float(np.linalg.norm(axis)), float(a @ b)
    k = axis / s
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + s * kx + (1.0 - c) * (kx @ kx)


def test_the_frame_terms_chord_is_reached_where_the_motion_lies_worst() -> None:
    """The chord is the bound, not a loose one: with the observer's motion u along the worst direction for a beacon m
    and a direction n 70 degrees from it (the top singular vector of u -> u (1 - m . n) + (u . n) (m - n)), what the
    anchored rotation leaves out at n reaches at least 0.99 of k 2 sin(35 deg), and stays within the stated term."""
    iso, lat, lon = "2031-08-30T03:01:00Z", 30.0, -40.0
    theta = math.radians(70.0)
    c, s = math.cos(theta), math.sin(theta)
    a_matrix = np.array([[(1 - c) * (1 + c), (1 - c) * s, 0.0], [-c * s, -c * (1 - c), 0.0], [0.0, 0.0, 1 - c]])
    _, singular, rows = np.linalg.svd(a_matrix)
    assert singular[0] == pytest.approx(2.0 * math.sin(theta / 2.0), rel=1e-12)
    astrom = observer_astrom(iso, lat, lon)
    # A copy: the parameters are cached and read only
    apex = np.array(astrom["v"], dtype=np.float64)
    k = float(np.linalg.norm(apex)) * 180.0 / math.pi * 3600.0
    apex = apex / np.linalg.norm(apex)
    # The right-handed basis (m, e, f) in which the apex has the worst coordinates: the rotation taking them to it
    frame = _rotation_taking(rows[0] / np.linalg.norm(rows[0]), apex)
    m, e = frame[:, 0], frame[:, 1]
    n = math.cos(theta) * m + math.sin(theta) * e
    m_ra, m_dec = unit_vector_to_ra_dec(m)
    rotation = anchored_horizon_rotation(m_ra, m_dec, lat, lon, iso)
    ra, dec = unit_vector_to_ra_dec(n)
    missed = _sep_arcsec(_enu(*icrs_to_frame_altaz(rotation, ra, dec)), _enu(*icrs_to_altaz(ra, dec, lat, lon, iso)))
    term = anchored_rotation_omission_arcsec(m_ra, m_dec, [(m_ra, m_dec, 70.0)], lat, lon, iso)
    assert 0.99 * k * 2.0 * math.sin(theta / 2.0) <= missed <= term, (missed, k, term)


def test_the_frame_term_counts_the_deflection_twice() -> None:
    """A beacon and a direction on either side of the Sun's centre near the deflection's peak (292 arcsec out): the
    deflection pushes them apart, and the rotation anchored on the beacon carries the beacon's push to the direction,
    so what is left there is close to the two deflections together; the term holds it by counting the peak twice."""
    iso, lat, lon = "2031-01-03T12:00:00Z", 0.0, 1.0
    astrom = observer_astrom(iso, lat, lon)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    m, n = _offset(sun, 292.0 / 3600.0, 0.0), _offset(sun, 292.0 / 3600.0, 180.0)
    m_ra, m_dec = unit_vector_to_ra_dec(m)
    rotation = anchored_horizon_rotation(m_ra, m_dec, lat, lon, iso)
    ra, dec = unit_vector_to_ra_dec(n)
    missed = _sep_arcsec(_enu(*icrs_to_frame_altaz(rotation, ra, dec)), _enu(*icrs_to_altaz(ra, dec, lat, lon, iso)))
    deflections = solar_deflection_arcsec(m_ra, m_dec, lat, lon, iso) + solar_deflection_arcsec(ra, dec, lat, lon, iso)
    term = anchored_rotation_omission_arcsec(m_ra, m_dec, [(*unit_vector_to_ra_dec(sun), 0.1)], lat, lon, iso)
    assert 0.95 * deflections <= missed <= term, (missed, deflections, term)
    assert missed > RIGID_DEFLECTION_MAX_ARCSEC + 1.0


# The bounded numbers of the frame term, each at an inside end and just outside, from one table:
# (what, the value, what is expected)
EDGE_MOMENT, EDGE_LAT, EDGE_LON = "2031-08-30T03:01:00Z", 30.0, -40.0
STEP = 1e-9
SEPARATION_EDGES = (
    ("separation at 0", 0.0, "peak"),
    ("separation at the peak band's end", DEFLECTION_PEAK_WITHIN_DEG, "peak"),
    ("separation just past the peak band", DEFLECTION_PEAK_WITHIN_DEG + STEP, "computed"),
    ("separation at 180, opposite the Sun", 180.0, "zero"),
    ("separation just past 180", 180.0 + STEP, "as at 180"),
    ("separation well past 180", 270.0, "as at 180"),
    ("separation just under 0", -STEP, "refused"),
    ("separation not a number", float("nan"), "refused"),
    ("separation infinite", float("inf"), "refused"),
)
RADIUS_EDGES = (
    ("radius at 0", 0.0, "computed"),
    ("radius at 180", 180.0, "the whole sphere"),
    ("radius just under 0", -STEP, "refused"),
    ("radius just past 180", 180.0 + STEP, "refused"),
    ("radius not a number", float("nan"), "refused"),
)


@pytest.mark.parametrize(("what", "value", "expected"), SEPARATION_EDGES, ids=[row[0] for row in SEPARATION_EDGES])
def test_the_deflection_at_a_separation_at_each_edge(what: str, value: float, expected: str) -> None:
    def at(separation: float) -> float:
        return solar_deflection_at_separation_arcsec(separation, EDGE_LAT, EDGE_LON, EDGE_MOMENT)

    if expected == "refused":
        with pytest.raises(ValueError):
            at(value)
        return
    got = at(value)
    if expected == "peak":
        assert got == RIGID_DEFLECTION_MAX_ARCSEC
    elif expected == "zero":
        assert got == 0.0
    elif expected == "as at 180":
        assert got == at(180.0)
    else:
        # ERFA's own value there: below the peak, and above zero
        assert 0.0 < got < RIGID_DEFLECTION_MAX_ARCSEC, got


@pytest.mark.parametrize(("what", "value", "expected"), RADIUS_EDGES, ids=[row[0] for row in RADIUS_EDGES])
def test_the_frame_term_at_each_edge_of_a_fields_radius(what: str, value: float, expected: str) -> None:
    astrom = observer_astrom(EDGE_MOMENT, EDGE_LAT, EDGE_LON)
    # A beacon a quarter of the sky from the Sun, the field centred on it
    beacon = _offset(-np.asarray(astrom["eh"], dtype=np.float64), 90.0, 0.0)
    ra, dec = unit_vector_to_ra_dec(beacon)

    def term(radius: float) -> float:
        return anchored_rotation_omission_arcsec(ra, dec, [(ra, dec, radius)], EDGE_LAT, EDGE_LON, EDGE_MOMENT)

    if expected == "refused":
        with pytest.raises(ValueError):
            term(value)
        return
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    if expected == "the whole sphere":
        # The field holds the Sun and the point opposite the beacon: the chord is 2 and the deflection the peak
        assert term(value) == pytest.approx(2.0 * k + 2.0 * RIGID_DEFLECTION_MAX_ARCSEC + SECOND_ORDER_ABERRATION_ARCSEC,
                                            rel=1e-12)
    else:
        # The beacon alone: no chord, and twice the deflection a quarter of the sky from the Sun
        deflection = solar_deflection_at_separation_arcsec(90.0, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
        assert term(value) == pytest.approx(2.0 * deflection + SECOND_ORDER_ABERRATION_ARCSEC, rel=1e-9)


def test_the_frame_term_needs_a_field_and_caps_its_farthest_angle_at_180_degrees() -> None:
    astrom = observer_astrom(EDGE_MOMENT, EDGE_LAT, EDGE_LON)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    beacon = _offset(sun, 90.0, 0.0)
    ra, dec = unit_vector_to_ra_dec(beacon)
    with pytest.raises(ValueError):
        anchored_rotation_omission_arcsec(ra, dec, [], EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    # A field opposite the beacon, 10 degrees wide: farthest 190 degrees, capped at 180, so the chord is 2; it lies a
    # quarter of the sky from the Sun, less its radius
    opposite = unit_vector_to_ra_dec(-beacon)
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    deflection = solar_deflection_at_separation_arcsec(80.0, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    got = anchored_rotation_omission_arcsec(ra, dec, [(*opposite, 10.0)], EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    assert got == pytest.approx(2.0 * k + 2.0 * deflection + SECOND_ORDER_ABERRATION_ARCSEC, rel=1e-9)


def test_the_least_rotation_is_the_identity_for_one_direction_and_refused_for_opposite_ones() -> None:
    from services.science.catalog import _least_rotation

    v = np.asarray(ra_dec_to_unit_vector(58.48, -64.64), dtype=np.float64)
    assert np.array_equal(_least_rotation(v, v), np.eye(3))
    with pytest.raises(ValueError):
        _least_rotation(v, -v)


def test_the_cached_rotations_are_read_only() -> None:
    """Both rotations are cached: a caller that wrote into one would change every later answer, so neither can be."""
    rigid = rigid_horizon_rotation(EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    anchored = anchored_horizon_rotation(58.48, -64.64, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    for rotation in (rigid, anchored):
        with pytest.raises(ValueError):
            rotation[0, 0] = 0.0


def test_the_frame_term_near_the_point_opposite_the_sun() -> None:
    """A field around the point opposite the Sun: its nearest approach to the Sun is 180 degrees less its radius, and
    the deflection there is ERFA's own, not the peak's or one from a nearer place."""
    astrom = observer_astrom(EDGE_MOMENT, EDGE_LAT, EDGE_LON)
    opposite = np.asarray(astrom["eh"], dtype=np.float64)
    ra, dec = unit_vector_to_ra_dec(opposite)
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    deflection = solar_deflection_at_separation_arcsec(175.0, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    term = anchored_rotation_omission_arcsec(ra, dec, [(ra, dec, 5.0)], EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    assert term == pytest.approx(k * 2.0 * math.sin(math.radians(5.0) / 2.0) + 2.0 * deflection
                                 + SECOND_ORDER_ABERRATION_ARCSEC, rel=1e-9)


def _unit_vectors_longer_by(monkeypatch: pytest.MonkeyPatch, excess: float) -> None:
    """Make catalog.py's unit vectors `excess` longer, so a direction dotted with itself, with the Sun's direction or
    with the point opposite lies past one on any machine: a rounded product can fall past one (by 2e-16 here at the
    synthetic moment below), but its last bits differ between machines, so no test waits for them."""
    from services.science import catalog as catalog_module

    real = catalog_module.ra_dec_to_unit_vector
    monkeypatch.setattr(catalog_module, "ra_dec_to_unit_vector",
                        lambda ra_deg, dec_deg: tuple(c * (1.0 + excess) for c in real(ra_deg, dec_deg)))


def test_the_frame_term_holds_where_rounding_puts_a_dot_product_past_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direction dotted with itself, with its antipode, with the Sun's direction or with the point opposite can lie
    past one; with the unit vectors 1e-14 longer it does on any machine. The angles are still 0 and 180 degrees, not
    an error."""
    lat, lon, moment = EDGE_LAT, EDGE_LON, "2031-01-03T02:14:00Z"
    k = float(np.linalg.norm(observer_astrom(moment, lat, lon)["v"])) * 180.0 / math.pi * 3600.0
    sun = -np.asarray(observer_astrom(moment, lat, lon)["eh"], dtype=np.float64)
    sun_ra, sun_dec = unit_vector_to_ra_dec(sun)
    beacon = (58.48, -64.64)
    _unit_vectors_longer_by(monkeypatch, 1e-14)
    for centre, radius, chord, nearest in (
        (beacon, 1.0, 2.0 * math.sin(math.radians(1.0) / 2.0), None),
        (((beacon[0] + 180.0) % 360.0, -beacon[1]), 0.0, 2.0, None),
        ((sun_ra, sun_dec), 1.0, None, 0.0),
        (((sun_ra + 180.0) % 360.0, -sun_dec), 1.0, None, 179.0),
    ):
        term = anchored_rotation_omission_arcsec(*beacon, [(*centre, radius)], lat, lon, moment)
        if chord is not None:
            assert term >= k * chord * (1.0 - 1e-12), (centre, term)
        if nearest is not None:
            assert term >= 2.0 * solar_deflection_at_separation_arcsec(nearest, lat, lon, moment)


def test_the_deflection_band_holds_the_peak_and_the_deflection_falls_beyond_it() -> None:
    """DEFLECTION_PEAK_WITHIN_DEG must hold the place of ERFA's peak, so that beyond it the deflection only falls and
    the nearest direction to the Sun gets the most: the peak lies inside it, and the stated peak holds every value."""
    astrom = observer_astrom(EDGE_MOMENT, EDGE_LAT, EDGE_LON)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    scan = [(arcsec, solar_deflection_arcsec(*unit_vector_to_ra_dec(_offset(sun, arcsec / 3600.0, 30.0)),
                                             EDGE_LAT, EDGE_LON, EDGE_MOMENT)) for arcsec in range(200, 400)]
    peak_at, peak = max(scan, key=lambda row: row[1])
    assert peak_at / 3600.0 < DEFLECTION_PEAK_WITHIN_DEG and peak <= RIGID_DEFLECTION_MAX_ARCSEC
    beyond = [solar_deflection_at_separation_arcsec(DEFLECTION_PEAK_WITHIN_DEG + step, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
              for step in (1e-9, 0.01, 0.1, 1.0, 10.0, 90.0)]
    assert all(a >= b for a, b in zip(beyond, beyond[1:])), beyond


def test_the_deflection_band_ends_at_the_stated_tenth_of_a_degree() -> None:
    """catalog.py states the band as 0.1 degree from the Sun's centre. The edge table above reads the constant, so it
    moves with it; these two sit at the stated value: the peak at 0.1 degree, ERFA's own lower value just past it."""
    def at(separation: float) -> float:
        return solar_deflection_at_separation_arcsec(separation, EDGE_LAT, EDGE_LON, EDGE_MOMENT)

    assert at(0.1) == RIGID_DEFLECTION_MAX_ARCSEC
    assert 0.0 < at(0.1 + 1e-6) < RIGID_DEFLECTION_MAX_ARCSEC


@pytest.mark.parametrize("separation_deg", [0.2, 5.0, 45.0, 120.0, 179.0])
def test_the_deflection_at_a_separation_is_the_same_on_every_side_of_the_sun(separation_deg: float) -> None:
    """ERFA's deflection of a direction depends only on its angle from the Sun (eraLd's p x (e x p) has length
    sin of that angle), so the side `solar_deflection_at_separation_arcsec` steps toward does not matter: ERFA's own
    value at five bearings around the Sun is the function's."""
    astrom = observer_astrom(EDGE_MOMENT, EDGE_LAT, EDGE_LON)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    want = solar_deflection_at_separation_arcsec(separation_deg, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
    for bearing in (0.0, 33.0, 90.0, 180.0, 270.0):
        ra, dec = unit_vector_to_ra_dec(_offset(sun, separation_deg, bearing))
        got = solar_deflection_arcsec(ra, dec, EDGE_LAT, EDGE_LON, EDGE_MOMENT)
        assert got == pytest.approx(want, rel=1e-9, abs=1e-9), (bearing, got, want)


def test_the_frame_term_holds_a_beacon_nearer_the_sun_than_its_field() -> None:
    """The beacon need not lie in a field: one at the deflection's peak, 292 arcsec from the Sun's centre, and a field
    5 to 7 degrees out on the far side. The anchoring carries the beacon's own deflection (about 5.8 arcsec) to the
    field, so the term counts the larger of the beacon's and the field's deflection twice, not the field's alone."""
    iso, lat, lon = "2031-01-03T12:00:00Z", 0.0, 1.0
    astrom = observer_astrom(iso, lat, lon)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    m = _offset(sun, 292.0 / 3600.0, 0.0)
    centre = _offset(sun, 6.0, 180.0)
    m_ra, m_dec = unit_vector_to_ra_dec(m)
    rotation = anchored_horizon_rotation(m_ra, m_dec, lat, lon, iso)
    term = anchored_rotation_omission_arcsec(m_ra, m_dec, [(*unit_vector_to_ra_dec(centre), 1.0)], lat, lon, iso)
    beacon_deflection = solar_deflection_arcsec(m_ra, m_dec, lat, lon, iso)
    worst = 0.0
    for bearing in range(0, 360, 30):
        for edge in (0.0, 0.5, 1.0):
            ra, dec = unit_vector_to_ra_dec(_offset(centre, edge, float(bearing)))
            missed = _sep_arcsec(_enu(*icrs_to_frame_altaz(rotation, ra, dec)),
                                 _enu(*icrs_to_altaz(ra, dec, lat, lon, iso)))
            worst = max(worst, missed)
    assert worst > beacon_deflection, (worst, beacon_deflection)
    assert worst <= term, (worst, term)


def test_the_frame_term_takes_the_fields_deflection_when_the_field_is_nearer_the_sun() -> None:
    """The mirror of the case above: a beacon 2 degrees from the Sun's centre and a small field on the Sun itself. The
    field's nearest point lies within the peak band, so D is the peak, not the beacon's smaller deflection."""
    iso, lat, lon = "2031-01-03T12:00:00Z", 0.0, 1.0
    astrom = observer_astrom(iso, lat, lon)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    m_ra, m_dec = unit_vector_to_ra_dec(_offset(sun, 2.0, 0.0))
    sun_ra, sun_dec = unit_vector_to_ra_dec(sun)
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    assert solar_deflection_arcsec(m_ra, m_dec, lat, lon, iso) < RIGID_DEFLECTION_MAX_ARCSEC / 2.0
    term = anchored_rotation_omission_arcsec(m_ra, m_dec, [(sun_ra, sun_dec, 0.05)], lat, lon, iso)
    # The farthest field point lies the beacon's 2 degrees from the field's centre plus its radius from the beacon
    assert term == pytest.approx(k * 2.0 * math.sin(math.radians(2.05) / 2.0) + 2.0 * RIGID_DEFLECTION_MAX_ARCSEC
                                 + SECOND_ORDER_ABERRATION_ARCSEC, rel=1e-9)


def test_the_frame_term_holds_a_beacon_on_the_sun_or_opposite_it_where_rounding_passes_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A beacon at the Sun's own RA and Dec (the sun_horizon method's beacon) and one at the point opposite, with the
    unit vectors 1e-14 longer, so each dotted with the Sun's direction lies past one on any machine. The term is still
    the deflection at 0 and at 180 degrees, not an error."""
    from services.science import catalog as catalog_module

    lat, lon, moment = EDGE_LAT, EDGE_LON, "2031-01-03T02:14:00Z"
    astrom = observer_astrom(moment, lat, lon)
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    sun_ra, sun_dec = unit_vector_to_ra_dec(sun)
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    _unit_vectors_longer_by(monkeypatch, 1e-14)
    for beacon, at_sun in (((sun_ra, sun_dec), True), (((sun_ra + 180.0) % 360.0, -sun_dec), False)):
        assert abs(float(sun @ np.asarray(catalog_module.ra_dec_to_unit_vector(*beacon), dtype=np.float64))) > 1.0
        term = anchored_rotation_omission_arcsec(*beacon, [(*beacon, 1.0)], lat, lon, moment)
        deflection = RIGID_DEFLECTION_MAX_ARCSEC if at_sun else solar_deflection_at_separation_arcsec(179.0, lat, lon, moment)
        assert term == pytest.approx(k * 2.0 * math.sin(math.radians(1.0) / 2.0) + 2.0 * deflection
                                     + SECOND_ORDER_ABERRATION_ARCSEC, rel=1e-9), (at_sun, term)


def test_the_rigid_frame_terms_equal_their_erfa_formulas() -> None:
    """Held from above (the properties hold them from below): the aberration term is the Earth's greatest barycentric
    speed over 1800 to 2050 (eraEpv00, daily) plus the rotation at the equator, over the speed of light; the deflection
    term is ERFA's restrained eraLdsun at its peak at the Earth's least distance from the Sun in the span; each stated
    to the 0.001 arcsec it is written to, and the total is their sum held up to the next tenth. The second-order term
    is held as 0.01 above (v/c)^2 of a radian, about 0.002."""
    import warnings

    import erfa

    start, stop = erfa.dtf2d("TT", 1800, 1, 1, 0, 0, 0.0), erfa.dtf2d("TT", 2051, 1, 1, 0, 0, 0.0)
    days = np.arange(start[0] + start[1], stop[0] + stop[1], 1.0)
    with warnings.catch_warnings():
        # eraEpv00 warns outside 1900 to 2100, where its notes double its error; the speed is all that is read here
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        pvh, pvb = erfa.epv00(np.full_like(days, 0.0), days)
    speed_au_day = float(np.max(np.linalg.norm(pvb["v"], axis=1)))
    least_au = float(np.min(np.linalg.norm(pvh["p"], axis=1)))
    c_au_day = erfa.CMPS * 86400.0 / erfa.DAU
    rotation_au_day = 2.0 * math.pi * 1.00273781191135448 * 6378.137 / (erfa.DAU / 1000.0)
    aberration = (speed_au_day + rotation_au_day) / c_au_day * 180.0 / math.pi * 3600.0
    assert abs(RIGID_ABERRATION_MAX_ARCSEC - aberration) <= 0.0005, aberration
    sun = np.array([1.0, 0.0, 0.0])
    peak = 0.0
    # The restrained deflection peaks at a kink (about 291.7 arcsec out), so the steps are fine near it
    for arcsec in np.arange(280.0, 300.0, 0.01):
        a = math.radians(arcsec / 3600.0)
        p = np.array([math.cos(a), math.sin(a), 0.0])
        bent = erfa.ldsun(p, -sun, least_au)
        peak = max(peak, math.degrees(math.atan2(float(np.linalg.norm(np.cross(p, bent))), float(p @ bent))) * 3600.0)
    assert abs(RIGID_DEFLECTION_MAX_ARCSEC - peak) <= 0.0005, peak
    assert RIGID_FRAME_OMISSION_ARCSEC == math.ceil((RIGID_ABERRATION_MAX_ARCSEC + RIGID_DEFLECTION_MAX_ARCSEC) * 10.0) / 10.0
    second_order = ((speed_au_day + rotation_au_day) / c_au_day) ** 2 * 180.0 / math.pi * 3600.0
    assert 0.001 < second_order < SECOND_ORDER_ABERRATION_ARCSEC == 0.01, second_order
