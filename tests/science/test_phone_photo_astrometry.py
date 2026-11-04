"""Ground-truth astrometry of the phone-photo benchmark, checked against published reference values.

Reference values come from two independent sources:

- the ERFA test suite, `src/t_erfa_c.c` in github.com/liberfa/erfa (read 2026-11-04), for the
  IAU 1976 precession, IAU 1980 obliquity and nutation, IAU 1982 sidereal time, aberration and the
  hour angle to horizon conversion;
- Meeus, Astronomical Algorithms (2nd edition, 1998), worked examples 12.a, 12.b, 13.b, 16.a, 22.a
  and 23.a, as printed by the example tests of the soniakeys/meeus Go port (read 2026-11-04).

The module uses Meeus's short nutation series, which he states is good to 0.5 arcsec in longitude
and 0.1 arcsec in obliquity. Every check that passes through nutation allows exactly that error,
propagated to the quantity checked.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from research.benchmarks.phone_photos import astrometry as am

ARCSEC = math.pi / 648000.0
MJD_ZERO = 2400000.5
# Meeus's stated accuracy of the short nutation series (chapter 22)
SHORT_NUTATION_DPSI = 0.5 * ARCSEC
SHORT_NUTATION_DEPS = 0.1 * ARCSEC


def _hours(h: float, m: float, s: float) -> float:
    """Right ascension in degrees from hours, minutes and seconds of time."""
    return 15.0 * (h + m / 60.0 + s / 3600.0)


def _degrees(sign: int, d: float, m: float, s: float) -> float:
    return sign * (d + m / 60.0 + s / 3600.0)


def _time_seconds(angle_rad: float) -> float:
    """An angle as seconds of time within the day."""
    return (math.degrees(angle_rad) % 360.0) / 15.0 * 3600.0


def _separation_rad(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    a = np.array([math.cos(math.radians(dec1_deg)) * math.cos(math.radians(ra1_deg)),
                  math.cos(math.radians(dec1_deg)) * math.sin(math.radians(ra1_deg)),
                  math.sin(math.radians(dec1_deg))])
    b = np.array([math.cos(math.radians(dec2_deg)) * math.cos(math.radians(ra2_deg)),
                  math.cos(math.radians(dec2_deg)) * math.sin(math.radians(ra2_deg)),
                  math.sin(math.radians(dec2_deg))])
    return math.atan2(float(np.linalg.norm(np.cross(a, b))), float(np.dot(a, b)))


# =============================================================================================
# ERFA reference values
# =============================================================================================


def test_precession_angles_match_erfa_prec76() -> None:
    zeta, z, theta = am.precession_angles_iau1976(MJD_ZERO + 33282.0, MJD_ZERO + 51544.0)
    assert zeta == pytest.approx(0.5588961642000161243e-2, abs=1e-12)
    assert z == pytest.approx(0.5589922365870680624e-2, abs=1e-12)
    assert theta == pytest.approx(0.4858945471687296760e-2, abs=1e-12)


def test_precession_matrix_matches_erfa_pmat76() -> None:
    expected = np.array(
        [
            [0.9999995504328350733, 0.8696632209480960785e-3, 0.3779153474959888345e-3],
            [-0.8696632209485112192e-3, 0.9999996218428560614, -0.1643284776111886407e-6],
            [-0.3779153474950335077e-3, -0.1643306746147366896e-6, 0.9999999285899790119],
        ]
    )
    np.testing.assert_allclose(am.precession_matrix_iau1976(MJD_ZERO + 50123.9999), expected, rtol=0, atol=1e-12)


def test_mean_obliquity_matches_erfa_obl80() -> None:
    assert am.mean_obliquity_iau1980(MJD_ZERO + 54388.0) == pytest.approx(0.4090751347643816218, abs=1e-14)


def test_gmst_matches_erfa_gmst82() -> None:
    assert am.gmst_iau1982(MJD_ZERO + 53736.0) == pytest.approx(1.754174981860675096, abs=1e-12)


def test_short_nutation_is_within_its_stated_accuracy_of_erfa_nut80() -> None:
    dpsi, deps = am.nutation_short(MJD_ZERO + 53736.0)
    assert abs(dpsi - (-0.9643658353226563966e-5)) <= SHORT_NUTATION_DPSI
    assert abs(deps - 0.4060051006879713322e-4) <= SHORT_NUTATION_DEPS


def test_equation_of_equinoxes_is_within_short_series_accuracy_of_erfa_eqeq94() -> None:
    # The equation of the equinoxes is dpsi cos(eps) plus terms of a few milliarcseconds
    assert abs(am.equation_of_equinoxes(MJD_ZERO + 41234.0) - 0.5357758254609256894e-4) <= SHORT_NUTATION_DPSI


def test_hour_angle_to_horizon_matches_erfa_hd2ae() -> None:
    az, alt = am.hour_angle_to_horizon(1.1, 1.2, 0.3)
    assert az == pytest.approx(5.916889243730066194, abs=1e-13)
    assert alt == pytest.approx(0.4472186304990486228, abs=1e-14)


def test_aberration_matches_erfa_ab_to_second_order() -> None:
    # eraAb is relativistic; the first-order shift renormalized here differs by |v|^2, about 1e-8
    natural = np.array([-0.76321968546737951, -0.60869453983060384, -0.21676408580639883])
    velocity_over_c = np.array([2.1044018893653786e-5, -8.9108923304429319e-5, -3.8633714797716569e-5])
    proper = am.apply_aberration(natural, velocity_over_c)
    np.testing.assert_allclose(
        proper,
        [-0.7631631094219556269, -0.6087553082505590832, -0.2167926269368471279],
        rtol=0,
        atol=1e-8,
    )
    assert np.linalg.norm(proper) == pytest.approx(1.0, abs=1e-15)


# =============================================================================================
# Meeus worked examples
# =============================================================================================


def test_meeus_22a_nutation_and_obliquity() -> None:
    jd_tt = 2446895.5  # 1987 April 10, 0h TD
    dpsi, deps = am.nutation_short(jd_tt)
    assert abs(dpsi - (-3.788 * ARCSEC)) <= SHORT_NUTATION_DPSI
    assert abs(deps - 9.443 * ARCSEC) <= SHORT_NUTATION_DEPS
    eps0 = am.mean_obliquity_iau1980(jd_tt)
    assert eps0 == pytest.approx(math.radians(_degrees(1, 23, 26, 27.407)), abs=0.0005 * ARCSEC)
    assert abs(eps0 + deps - math.radians(_degrees(1, 23, 26, 36.850))) <= SHORT_NUTATION_DEPS


def test_meeus_12a_mean_and_apparent_sidereal_time() -> None:
    jd = 2446895.5  # 1987 April 10, 0h UT
    assert _time_seconds(am.gmst_iau1982(jd)) == pytest.approx(13 * 3600 + 10 * 60 + 46.3668, abs=1e-4)
    # Apparent minus mean is the equation of the equinoxes; its error is the series error times cos(eps)
    apparent = am.gast(jd, jd)
    assert abs(_time_seconds(apparent) - (13 * 3600 + 10 * 60 + 46.1351)) <= SHORT_NUTATION_DPSI / ARCSEC / 15.0


def test_meeus_12b_mean_sidereal_time() -> None:
    jd = am.julian_date(datetime(1987, 4, 10, 19, 21, 0, tzinfo=timezone.utc))
    assert _time_seconds(am.gmst_iau1982(jd)) == pytest.approx(8 * 3600 + 34 * 60 + 57.0896, abs=1e-4)


def test_meeus_13b_venus_horizontal_coordinates() -> None:
    # Meeus measures azimuth westward from the south and longitude positive west; this module
    # measures azimuth from the north through the east and longitude positive east
    jd = am.julian_date(datetime(1987, 4, 10, 19, 21, 0, tzinfo=timezone.utc))
    ra = math.radians(_hours(23, 9, 16.641))
    dec = math.radians(_degrees(-1, 6, 43, 11.61))
    lat = math.radians(_degrees(1, 38, 55, 17))
    lon_east = -math.radians(_degrees(1, 77, 3, 56))
    hour_angle = am.gast(jd, jd) + lon_east - ra
    az, alt = am.hour_angle_to_horizon(hour_angle, dec, lat)
    assert math.degrees(az) == pytest.approx(68.0337 + 180.0, abs=3e-4)
    assert math.degrees(alt) == pytest.approx(15.1249, abs=3e-4)


def test_meeus_16a_refraction() -> None:
    assert am.refraction_bennett_arcmin(0.5) == pytest.approx(28.754, abs=5e-4)
    assert am.refraction_saemundsson_arcmin(33.246 / 60.0) == pytest.approx(24.618, abs=5e-4)


def test_meeus_23a_apparent_place_of_theta_persei() -> None:
    dec0 = _degrees(1, 49, 13, 42.48)
    ra, dec = am.apparent_place(
        _hours(2, 44, 11.986),
        dec0,
        jd_tt=2462088.69,  # 2028 November 13.19 TD
        pm_ra_cosdec_mas_yr=0.03425 * 15000.0 * math.cos(math.radians(dec0)),
        pm_dec_mas_yr=-89.5,
        catalog_epoch_jyear=2000.0,
    )
    expected_ra, expected_dec = _hours(2, 46, 14.390), _degrees(1, 49, 21, 7.45)
    # The short series errs by at most 0.5" in dpsi and 0.1" in deps; at this position that moves the
    # star by at most 0.51" on the sky (dpsi along the ecliptic, deps perpendicular to it)
    assert _separation_rad(ra, dec, expected_ra, expected_dec) <= 0.6 * ARCSEC


# =============================================================================================
# Time scales, refraction and the observation frame
# =============================================================================================


def test_tt_minus_utc_since_2017_and_refuses_earlier_dates() -> None:
    assert am.tt_minus_utc_seconds(am.julian_date(datetime(2026, 11, 4, tzinfo=timezone.utc))) == 69.184
    assert am.tt_minus_utc_seconds(am.julian_date(datetime(2017, 1, 1, tzinfo=timezone.utc))) == 69.184
    with pytest.raises(ValueError, match="2017"):
        am.tt_minus_utc_seconds(am.julian_date(datetime(2016, 12, 31, 23, 59, tzinfo=timezone.utc)))


def test_refraction_is_monotonic_and_invertible_down_below_the_horizon() -> None:
    true_alt = np.linspace(-10.0, 90.0, 20001)
    apparent = am.apparent_altitude_deg(true_alt)
    assert np.all(np.diff(apparent) > 0.0)
    np.testing.assert_allclose(am.true_altitude_deg(apparent), true_alt, rtol=0, atol=1e-10)
    # Standard conditions give no refraction change; pressure and temperature scale it
    scaled = am.apparent_altitude_deg(np.array([10.0]), pressure_hpa=505.0, temperature_c=10.0)
    assert float(scaled[0]) - 10.0 == pytest.approx((float(am.apparent_altitude_deg(np.array([10.0]))[0]) - 10.0) / 2.0)


@pytest.mark.parametrize(
    ("lat", "lon", "hour"),
    [(45.0, 10.0, 22), (-33.9, 151.2, 11), (64.1, -21.9, 3), (0.0, 179.9, 17)],
)
def test_observation_frame_agrees_with_the_reference_checked_pieces(lat: float, lon: float, hour: int) -> None:
    # The frame composes its matrices itself; rebuild the same direction from the pieces the ERFA
    # and Meeus tests pin (apparent place, apparent sidereal time with longitude positive east,
    # eraHd2ae, Saemundsson) so a sign or ordering slip in the frame cannot pass unseen
    utc = datetime(2026, 3, 15, hour, 7, 30, tzinfo=timezone.utc)
    frame = am.ObservationFrame(utc=utc, lat_deg=lat, lon_deg=lon)
    jd_utc = am.julian_date(utc)
    jd_tt = jd_utc + am.tt_minus_utc_seconds(jd_utc) / 86400.0
    # Directions near Betelgeuse, Vega and Canopus, used only as directions spread over the sky
    for ra_icrs, dec_icrs in [(88.79293899, 7.40703634), (279.23473479, 38.78368896), (95.98795782, -52.69566138)]:
        ra, dec = am.apparent_place(ra_icrs, dec_icrs, jd_tt=jd_tt, catalog_epoch_jyear=am.julian_year(jd_tt))
        hour_angle = am.gast(jd_utc, jd_tt) + math.radians(lon) - math.radians(ra)
        az, alt = am.hour_angle_to_horizon(hour_angle, math.radians(dec), math.radians(lat))
        expected_alt = float(am.apparent_altitude_deg(math.degrees(alt))[0])
        got_az, got_alt = frame.altaz(ra_icrs, dec_icrs)
        assert float(got_alt[0]) == pytest.approx(expected_alt, abs=1e-9)
        assert (float(got_az[0]) - math.degrees(az) + 180.0) % 360.0 - 180.0 == pytest.approx(0.0, abs=1e-9)


def test_the_true_pole_of_date_stands_at_the_latitude() -> None:
    frame = am.ObservationFrame(utc=datetime(2026, 3, 20, 3, 0, tzinfo=timezone.utc), lat_deg=45.0, lon_deg=10.0)
    pole_icrs = frame.true_pole_of_date_icrs()
    az, alt = frame.altaz(*pole_icrs)
    refraction_deg = am.refraction_saemundsson_arcmin(45.0) / 60.0
    # Annual aberration moves any star by at most 20.5 arcsec
    assert float(alt[0]) == pytest.approx(45.0 + refraction_deg, abs=21.0 / 3600.0)
    assert min(float(az[0]), 360.0 - float(az[0])) < 0.02


@settings(max_examples=150, deadline=None)
@given(
    ra=st.floats(0.0, 360.0, exclude_max=True),
    dec=st.floats(-89.9, 89.9),
    lat=st.floats(-70.0, 70.0),
    lon=st.floats(-180.0, 180.0),
    seconds=st.floats(0.0, 5 * 365.25 * 86400.0),
)
@example(ra=0.0, dec=0.0, lat=0.0, lon=0.0, seconds=0.0)
@example(ra=359.9999, dec=89.9, lat=70.0, lon=180.0, seconds=1.0)
@example(ra=180.0, dec=-89.9, lat=-70.0, lon=-180.0, seconds=86400.0)
def test_observation_frame_round_trips_every_direction(ra: float, dec: float, lat: float, lon: float, seconds: float) -> None:
    frame = am.ObservationFrame(
        utc=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds), lat_deg=lat, lon_deg=lon
    )
    enu = frame.icrs_to_enu(np.array([ra]), np.array([dec]))
    assert np.linalg.norm(enu[0]) == pytest.approx(1.0, abs=1e-14)
    ra2, dec2 = frame.enu_to_icrs(enu)
    assert _separation_rad(ra, dec, float(ra2[0]), float(dec2[0])) < 1e-10
