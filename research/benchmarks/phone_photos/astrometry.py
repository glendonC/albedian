"""From ICRS catalog directions to the observer's horizon, for the benchmark's ground truth.

A catalog direction, already moved to the date by its proper motion, goes through:

1. IAU 1976 precession (Lieske et al. 1977) to the mean equator and equinox of date;
2. annual aberration from Earth's orbital velocity (Meeus 1998, chapters 23 and 25);
3. nutation to the true equator of date, by Meeus's short series (chapter 22), which he states
   is good to 0.5 arcsec in longitude and 0.1 arcsec in obliquity;
4. Greenwich apparent sidereal time, the IAU 1982 mean sidereal time plus the equation of the
   equinoxes, then the local hour angle;
5. the local horizon (east, north, up) and refraction by Saemundsson's formula (Meeus chapter
   16), scaled to the frame's pressure and temperature.

Every step has an exact inverse here, so a pixel can be traced back to ICRS.

Simplifications [inference: each stays far below the tens of arcseconds a phone pixel spans]:
frame bias between ICRS and the J2000 mean equator (0.02 arcsec), polar motion (under 0.5 arcsec),
diurnal aberration (under 0.32 arcsec), stellar parallax (under 0.8 arcsec) and light deflection
are ignored; the geodetic latitude stands in for the astronomical one. UT1 is taken equal to UTC:
the real difference stays under 0.9 s, which turns the sky by at most 13.5 arcsec about the pole.
That is a convention of the benchmark, not a property of real photos.

References: Lieske et al. 1977, A&A 58, 1; Meeus, Astronomical Algorithms, 2nd ed., 1998;
the ERFA library (github.com/liberfa/erfa), whose test values the tests use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property

import numpy as np

ARCSEC = math.pi / 648000.0
TWO_PI = 2.0 * math.pi
JD_J2000 = 2451545.0
DAYS_PER_JULIAN_CENTURY = 36525.0
DAYS_PER_JULIAN_YEAR = 365.25
JD_UNIX_EPOCH = 2440587.5
# TT - UTC = 32.184 s + (TAI - UTC); TAI - UTC has been 37 s since 2017-01-01 (IERS Bulletin C)
JD_2017_UTC = 2457754.5
TT_MINUS_UTC_SINCE_2017_S = 69.184
ABERRATION_CONSTANT = 20.49552 * ARCSEC  # Meeus chapter 23
STANDARD_PRESSURE_HPA = 1010.0
STANDARD_TEMPERATURE_C = 10.0
# Saemundsson's formula peaks near -1.9 deg true altitude; holding it constant below -2 deg keeps
# true to apparent altitude strictly increasing, so the horizon mapping stays invertible
REFRACTION_FLOOR_DEG = -2.0


def _scalar_or_array(value: np.ndarray):
    return float(value) if np.ndim(value) == 0 else value


# =============================================================================================
# Time
# =============================================================================================


def julian_date(utc: datetime) -> float:
    """Julian date of an aware datetime, on the UTC scale."""
    if utc.tzinfo is None:
        raise ValueError("Pass an aware datetime; the benchmark never guesses a time zone.")
    delta = utc.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return JD_UNIX_EPOCH + delta.days + (delta.seconds + delta.microseconds * 1e-6) / 86400.0


def tt_minus_utc_seconds(jd_utc: float) -> float:
    """TT - UTC in seconds for dates since 2017-01-01, the last leap second to date."""
    if jd_utc < JD_2017_UTC:
        raise ValueError(
            "TT - UTC is tabulated here only from 2017-01-01 (TAI - UTC = 37 s); earlier dates need "
            "the leap-second table."
        )
    return TT_MINUS_UTC_SINCE_2017_S


def _centuries(jd: float) -> float:
    return (jd - JD_J2000) / DAYS_PER_JULIAN_CENTURY


# =============================================================================================
# Rotations (ERFA's convention: rotating the axes, so a positive angle turns vectors backwards)
# =============================================================================================


def _rot_x(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]])


def _rot_y(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def _rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


def radec_to_vec(ra_deg, dec_deg) -> np.ndarray:
    """Unit vectors (n, 3) for right ascensions and declinations in degrees."""
    ra = np.radians(np.atleast_1d(np.asarray(ra_deg, dtype=np.float64)))
    dec = np.radians(np.atleast_1d(np.asarray(dec_deg, dtype=np.float64)))
    cos_dec = np.cos(dec)
    return np.stack([cos_dec * np.cos(ra), cos_dec * np.sin(ra), np.sin(dec)], axis=-1)


def vec_to_radec(vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Right ascension in [0, 360) and declination in degrees of vectors (n, 3)."""
    vec = np.atleast_2d(vec)
    ra = np.degrees(np.arctan2(vec[:, 1], vec[:, 0])) % 360.0
    dec = np.degrees(np.arctan2(vec[:, 2], np.hypot(vec[:, 0], vec[:, 1])))
    return ra, dec


def _normalize(vec: np.ndarray) -> np.ndarray:
    return vec / np.linalg.norm(vec, axis=-1, keepdims=True)


# =============================================================================================
# Precession, obliquity, nutation, sidereal time
# =============================================================================================


def precession_angles_iau1976(jd_tt_from: float, jd_tt_to: float) -> tuple[float, float, float]:
    """Lieske et al. (1977) Euler angles zeta, z, theta (radians) between two TT dates (eraPrec76)."""
    t0 = _centuries(jd_tt_from)
    t = (jd_tt_to - jd_tt_from) / DAYS_PER_JULIAN_CENTURY
    tas2r = t * ARCSEC
    w = 2306.2181 + (1.39656 - 0.000139 * t0) * t0
    zeta = (w + ((0.30188 - 0.000344 * t0) + 0.017998 * t) * t) * tas2r
    z = (w + ((1.09468 + 0.000066 * t0) + 0.018203 * t) * t) * tas2r
    theta = (
        (2004.3109 + (-0.85330 - 0.000217 * t0) * t0) + ((-0.42665 - 0.000217 * t0) - 0.041833 * t) * t
    ) * tas2r
    return zeta, z, theta


def precession_matrix_iau1976(jd_tt: float) -> np.ndarray:
    """Matrix taking J2000 mean equatorial vectors to the mean equator and equinox of date (eraPmat76)."""
    zeta, z, theta = precession_angles_iau1976(JD_J2000, jd_tt)
    return _rot_z(-z) @ _rot_y(theta) @ _rot_z(-zeta)


def mean_obliquity_iau1980(jd_tt: float) -> float:
    """Mean obliquity of the ecliptic in radians (eraObl80, Meeus 22.2)."""
    t = _centuries(jd_tt)
    return ARCSEC * (84381.448 + (-46.8150 + (-0.00059 + 0.001813 * t) * t) * t)


def _moon_node_deg(t: float) -> float:
    return 125.04452 - 1934.136261 * t + 0.0020708 * t * t + t**3 / 450000.0


def nutation_short(jd_tt: float) -> tuple[float, float]:
    """Nutation in longitude and obliquity (radians) by Meeus's short series (chapter 22).

    Meeus states it is good to 0.5 arcsec in longitude and 0.1 arcsec in obliquity.
    """
    t = _centuries(jd_tt)
    omega = math.radians(_moon_node_deg(t))
    l_sun = math.radians(280.4665 + 36000.7698 * t)
    l_moon = math.radians(218.3165 + 481267.8813 * t)
    dpsi = -17.20 * math.sin(omega) - 1.32 * math.sin(2 * l_sun) - 0.23 * math.sin(2 * l_moon) + 0.21 * math.sin(2 * omega)
    deps = 9.20 * math.cos(omega) + 0.57 * math.cos(2 * l_sun) + 0.10 * math.cos(2 * l_moon) - 0.09 * math.cos(2 * omega)
    return dpsi * ARCSEC, deps * ARCSEC


def nutation_matrix(jd_tt: float) -> np.ndarray:
    """Matrix taking mean-of-date vectors to the true equator and equinox of date (eraNumat)."""
    eps0 = mean_obliquity_iau1980(jd_tt)
    dpsi, deps = nutation_short(jd_tt)
    return _rot_x(-(eps0 + deps)) @ _rot_z(-dpsi) @ _rot_x(eps0)


def equation_of_equinoxes(jd_tt: float) -> float:
    """Apparent minus mean sidereal time in radians (eraEqeq94, with the short nutation series)."""
    dpsi, _ = nutation_short(jd_tt)
    omega = math.radians(_moon_node_deg(_centuries(jd_tt)))
    return dpsi * math.cos(mean_obliquity_iau1980(jd_tt)) + ARCSEC * (0.00264 * math.sin(omega) + 0.000063 * math.sin(2 * omega))


def gmst_iau1982(jd_ut1: float) -> float:
    """Greenwich mean sidereal time in radians, IAU 1982 (eraGmst82; Meeus 12.4)."""
    t = _centuries(jd_ut1)
    day_fraction_s = 86400.0 * math.fmod(jd_ut1, 1.0)
    # The constant drops 12 h because a Julian date starts at noon
    seconds = (24110.54841 - 43200.0) + (8640184.812866 + (0.093104 - 6.2e-6 * t) * t) * t + day_fraction_s
    return (seconds * (TWO_PI / 86400.0)) % TWO_PI


def gast(jd_ut1: float, jd_tt: float) -> float:
    """Greenwich apparent sidereal time in radians."""
    return (gmst_iau1982(jd_ut1) + equation_of_equinoxes(jd_tt)) % TWO_PI


# =============================================================================================
# Aberration
# =============================================================================================


def earth_velocity_over_c(jd_tt: float) -> np.ndarray:
    """Earth's orbital velocity over c on the mean equator and equinox of date (Meeus 23.3, 25.2-25.4).

    The velocity points 90 degrees behind the Sun's true longitude, with the eccentricity term.
    """
    t = _centuries(jd_tt)
    mean_lon = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    anomaly = math.radians(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    center = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(anomaly)
        + (0.019993 - 0.000101 * t) * math.sin(2 * anomaly)
        + 0.000289 * math.sin(3 * anomaly)
    )
    sun_lon = math.radians(mean_lon + center)
    ecc = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t
    perihelion = math.radians(102.93735 + 1.71946 * t + 0.00046 * t * t)
    bx = ABERRATION_CONSTANT * (math.sin(sun_lon) - ecc * math.sin(perihelion))
    by = ABERRATION_CONSTANT * (-math.cos(sun_lon) + ecc * math.cos(perihelion))
    eps = mean_obliquity_iau1980(jd_tt)
    return np.array([bx, by * math.cos(eps), by * math.sin(eps)])


def apply_aberration(direction: np.ndarray, velocity_over_c: np.ndarray) -> np.ndarray:
    """Shift natural directions toward the observer's velocity to first order, renormalized."""
    return _normalize(np.asarray(direction, dtype=np.float64) + np.asarray(velocity_over_c, dtype=np.float64))


def remove_aberration(observed: np.ndarray, velocity_over_c: np.ndarray) -> np.ndarray:
    """Exact inverse of `apply_aberration` by fixed-point iteration."""
    observed = np.asarray(observed, dtype=np.float64)
    natural = _normalize(observed - velocity_over_c)
    for _ in range(4):
        natural = _normalize(natural + (observed - apply_aberration(natural, velocity_over_c)))
    return natural


# =============================================================================================
# Horizon and refraction
# =============================================================================================


def hour_angle_to_horizon(hour_angle, dec, lat):
    """Azimuth (from north through east, [0, 2 pi)) and altitude in radians (eraHd2ae)."""
    hour_angle, dec = np.asarray(hour_angle, dtype=np.float64), np.asarray(dec, dtype=np.float64)
    sh, ch = np.sin(hour_angle), np.cos(hour_angle)
    sd, cd = np.sin(dec), np.cos(dec)
    sp, cp = math.sin(lat), math.cos(lat)
    x = -ch * cd * sp + sd * cp
    y = -sh * cd
    z = ch * cd * cp + sd * sp
    r = np.hypot(x, y)
    az = np.where(r != 0.0, np.arctan2(y, x), 0.0)
    az = np.where(az < 0.0, az + TWO_PI, az)
    return _scalar_or_array(az), _scalar_or_array(np.arctan2(z, r))


def _refraction_scale(pressure_hpa: float, temperature_c: float) -> float:
    return (pressure_hpa / STANDARD_PRESSURE_HPA) * (283.0 / (273.0 + temperature_c))


def refraction_saemundsson_arcmin(true_alt_deg, pressure_hpa: float = STANDARD_PRESSURE_HPA, temperature_c: float = STANDARD_TEMPERATURE_C):
    """Refraction in arcminutes for a true (airless) altitude in degrees (Meeus 16.4)."""
    h = np.asarray(true_alt_deg, dtype=np.float64)
    r = _refraction_scale(pressure_hpa, temperature_c) * 1.02 / np.tan(np.radians(h + 10.3 / (h + 5.11)))
    return _scalar_or_array(r)


def refraction_bennett_arcmin(apparent_alt_deg, pressure_hpa: float = STANDARD_PRESSURE_HPA, temperature_c: float = STANDARD_TEMPERATURE_C):
    """Refraction in arcminutes for an apparent (observed) altitude in degrees (Meeus 16.3)."""
    h0 = np.asarray(apparent_alt_deg, dtype=np.float64)
    r = _refraction_scale(pressure_hpa, temperature_c) / np.tan(np.radians(h0 + 7.31 / (h0 + 4.4)))
    return _scalar_or_array(r)


def _refraction_slope(true_alt_deg: np.ndarray, scale: float) -> np.ndarray:
    """d(refraction in degrees) / d(true altitude in degrees) of Saemundsson's formula."""
    g = true_alt_deg + 10.3 / (true_alt_deg + 5.11)
    dg = 1.0 - 10.3 / (true_alt_deg + 5.11) ** 2
    return -scale * 1.02 / np.sin(np.radians(g)) ** 2 * dg * (math.pi / 180.0) / 60.0


def apparent_altitude_deg(true_alt_deg, pressure_hpa: float = STANDARD_PRESSURE_HPA, temperature_c: float = STANDARD_TEMPERATURE_C) -> np.ndarray:
    """Observed altitude for a true altitude, both in degrees; strictly increasing everywhere."""
    h = np.atleast_1d(np.asarray(true_alt_deg, dtype=np.float64))
    held = np.maximum(h, REFRACTION_FLOOR_DEG)
    return h + np.asarray(refraction_saemundsson_arcmin(held, pressure_hpa, temperature_c)) / 60.0


def true_altitude_deg(apparent_alt_deg, pressure_hpa: float = STANDARD_PRESSURE_HPA, temperature_c: float = STANDARD_TEMPERATURE_C) -> np.ndarray:
    """Inverse of `apparent_altitude_deg` by Newton iteration."""
    a = np.atleast_1d(np.asarray(apparent_alt_deg, dtype=np.float64))
    scale = _refraction_scale(pressure_hpa, temperature_c)
    floor_shift = float(refraction_saemundsson_arcmin(REFRACTION_FLOOR_DEG, pressure_hpa, temperature_c)) / 60.0
    below = a <= REFRACTION_FLOOR_DEG + floor_shift
    start = np.clip(a, -1.0, 90.0)
    h = np.clip(a - np.asarray(refraction_bennett_arcmin(start, pressure_hpa, temperature_c)) / 60.0, REFRACTION_FLOOR_DEG, 90.0)
    for _ in range(12):
        f = apparent_altitude_deg(h, pressure_hpa, temperature_c) - a
        step = f / (1.0 + _refraction_slope(h, scale))
        h = np.clip(h - step, REFRACTION_FLOOR_DEG, 90.5)
    return np.where(below, a - floor_shift, h)


# =============================================================================================
# Places of date
# =============================================================================================


def propagate_proper_motion(ra_deg, dec_deg, pm_ra_cosdec_mas_yr, pm_dec_mas_yr, years: float) -> tuple[np.ndarray, np.ndarray]:
    """Move directions along the tangent plane by proper motion (right ascension rate times cos dec)."""
    vec = radec_to_vec(ra_deg, dec_deg)
    ra = np.radians(np.atleast_1d(np.asarray(ra_deg, dtype=np.float64)))
    dec = np.radians(np.atleast_1d(np.asarray(dec_deg, dtype=np.float64)))
    mas = ARCSEC / 1000.0
    d_east = np.nan_to_num(np.atleast_1d(np.asarray(pm_ra_cosdec_mas_yr, dtype=np.float64))) * mas * years
    d_north = np.nan_to_num(np.atleast_1d(np.asarray(pm_dec_mas_yr, dtype=np.float64))) * mas * years
    east = np.stack([-np.sin(ra), np.cos(ra), np.zeros_like(ra)], axis=-1)
    north = np.stack([-np.sin(dec) * np.cos(ra), -np.sin(dec) * np.sin(ra), np.cos(dec)], axis=-1)
    return vec_to_radec(_normalize(vec + east * d_east[:, None] + north * d_north[:, None]))


def julian_year(jd: float) -> float:
    return 2000.0 + (jd - JD_J2000) / DAYS_PER_JULIAN_YEAR


def apparent_place(
    ra_deg: float,
    dec_deg: float,
    *,
    jd_tt: float,
    pm_ra_cosdec_mas_yr: float = 0.0,
    pm_dec_mas_yr: float = 0.0,
    catalog_epoch_jyear: float = 2000.0,
) -> tuple[float, float]:
    """Geocentric apparent right ascension and declination of date, in degrees (Meeus chapter 23)."""
    ra, dec = propagate_proper_motion(ra_deg, dec_deg, pm_ra_cosdec_mas_yr, pm_dec_mas_yr, julian_year(jd_tt) - catalog_epoch_jyear)
    vec = radec_to_vec(ra, dec) @ precession_matrix_iau1976(jd_tt).T
    vec = apply_aberration(vec, earth_velocity_over_c(jd_tt)) @ nutation_matrix(jd_tt).T
    ra_out, dec_out = vec_to_radec(vec)
    return float(ra_out[0]), float(dec_out[0])


# =============================================================================================
# The observer's frame
# =============================================================================================


@dataclass(frozen=True)
class ObservationFrame:
    """A moment and a place: maps ICRS directions of that date to the observed horizon and back.

    Horizon vectors are (east, north, up) unit vectors of the observed (refracted) sky. Longitude
    is positive east. Directions passed in are ICRS directions at the observation date, that is,
    after proper motion.
    """

    utc: datetime
    lat_deg: float
    lon_deg: float
    pressure_hpa: float = STANDARD_PRESSURE_HPA
    temperature_c: float = STANDARD_TEMPERATURE_C

    def __post_init__(self) -> None:
        if self.utc.tzinfo is None:
            raise ValueError("ObservationFrame needs an aware UTC datetime.")
        if not -90.0 <= self.lat_deg <= 90.0:
            raise ValueError(f"Latitude {self.lat_deg} is outside [-90, 90].")
        if not -180.0 <= self.lon_deg <= 180.0:
            raise ValueError(f"Longitude {self.lon_deg} is outside [-180, 180].")

    @cached_property
    def jd_utc(self) -> float:
        return julian_date(self.utc)

    @cached_property
    def jd_tt(self) -> float:
        return self.jd_utc + tt_minus_utc_seconds(self.jd_utc) / 86400.0

    @cached_property
    def _precession(self) -> np.ndarray:
        return precession_matrix_iau1976(self.jd_tt)

    @cached_property
    def _nutation(self) -> np.ndarray:
        return nutation_matrix(self.jd_tt)

    @cached_property
    def _velocity(self) -> np.ndarray:
        return earth_velocity_over_c(self.jd_tt)

    @cached_property
    def _to_horizon(self) -> np.ndarray:
        """True-of-date equatorial to (east, north, up): the hour-angle frame, then the latitude."""
        local_sidereal = gast(self.jd_utc, self.jd_tt) + math.radians(self.lon_deg)
        sp, cp = math.sin(math.radians(self.lat_deg)), math.cos(math.radians(self.lat_deg))
        hour_frame_to_enu = np.array([[0.0, 1.0, 0.0], [-sp, 0.0, cp], [cp, 0.0, sp]])
        return hour_frame_to_enu @ _rot_z(local_sidereal)

    def _refract(self, enu: np.ndarray, *, forward: bool) -> np.ndarray:
        horizontal = np.hypot(enu[:, 0], enu[:, 1])
        alt = np.degrees(np.arctan2(enu[:, 2], horizontal))
        if forward:
            new_alt = apparent_altitude_deg(alt, self.pressure_hpa, self.temperature_c)
        else:
            new_alt = true_altitude_deg(alt, self.pressure_hpa, self.temperature_c)
        out = enu.copy()
        moved = horizontal > 0.0
        ratio = np.cos(np.radians(new_alt[moved])) / horizontal[moved]
        out[moved, 0] = enu[moved, 0] * ratio
        out[moved, 1] = enu[moved, 1] * ratio
        out[moved, 2] = np.sin(np.radians(new_alt[moved]))
        return out

    def icrs_to_enu(self, ra_deg, dec_deg) -> np.ndarray:
        """Observed (east, north, up) unit vectors (n, 3) for ICRS directions of date."""
        vec = radec_to_vec(ra_deg, dec_deg) @ self._precession.T
        vec = apply_aberration(vec, self._velocity) @ self._nutation.T
        return self._refract(vec @ self._to_horizon.T, forward=True)

    def enu_to_icrs(self, enu: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """ICRS right ascension and declination (degrees) of observed (east, north, up) vectors."""
        enu = _normalize(np.atleast_2d(np.asarray(enu, dtype=np.float64)))
        vec = self._refract(enu, forward=False) @ self._to_horizon
        vec = remove_aberration(vec @ self._nutation, self._velocity) @ self._precession
        return vec_to_radec(vec)

    def altaz(self, ra_deg, dec_deg) -> tuple[np.ndarray, np.ndarray]:
        """Observed azimuth (from north through east) and altitude in degrees."""
        enu = self.icrs_to_enu(ra_deg, dec_deg)
        az = np.degrees(np.arctan2(enu[:, 0], enu[:, 1])) % 360.0
        alt = np.degrees(np.arctan2(enu[:, 2], np.hypot(enu[:, 0], enu[:, 1])))
        return az, alt

    def true_pole_of_date_icrs(self) -> tuple[np.ndarray, np.ndarray]:
        """ICRS direction of the true celestial pole of date (before aberration)."""
        return vec_to_radec((self._nutation @ self._precession).T @ np.array([0.0, 0.0, 1.0]))
