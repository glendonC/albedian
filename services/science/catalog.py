"""Pinned Gaia DR3 & Hipparcos bright-star catalog and coordinate transformation math."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import math
import threading
from typing import Sequence
import warnings

import erfa
import numpy as np

from services.science.catalog_all_sky import ALL_SKY_EXTRA_STARS, CONSTELLATION_EDGES
from services.science.catalog_model import CatalogStar
from services.science.catalog_orion import PINNED_CATALOG_STARS

ALL_SKY_CATALOG_STARS: tuple[CatalogStar, ...] = PINNED_CATALOG_STARS + ALL_SKY_EXTRA_STARS

STAR_BY_NAME: dict[str, CatalogStar] = {
    star.common_name.lower(): star for star in ALL_SKY_CATALOG_STARS
}
STAR_BY_ID: dict[str, CatalogStar] = {star.id: star for star in ALL_SKY_CATALOG_STARS}


def get_catalog_stars() -> list[CatalogStar]:
    """Return a copy of the pinned 18-star Orion/Taurus benchmark catalog."""
    return list(PINNED_CATALOG_STARS)


def get_all_sky_catalog_stars() -> list[CatalogStar]:
    """Return the combined all-sky bright navigational star catalog (71 stars)."""
    return list(ALL_SKY_CATALOG_STARS)


def get_star_by_name(name: str) -> CatalogStar:
    """Lookup a catalog star by common name (case-insensitive)."""
    key = name.strip().lower()
    if key not in STAR_BY_NAME:
        raise KeyError(f"Unknown catalog star: {name}")
    return STAR_BY_NAME[key]


def ra_dec_to_unit_vector(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
    """Convert ICRS right ascension and declination (degrees) to a 3D unit vector."""
    ra_rad = math.radians(float(ra_deg) % 360.0)
    dec_rad = math.radians(max(-90.0, min(90.0, float(dec_deg))))
    cos_dec = math.cos(dec_rad)
    x = cos_dec * math.cos(ra_rad)
    y = cos_dec * math.sin(ra_rad)
    z = math.sin(dec_rad)
    norm = math.sqrt(x * x + y * y + z * z)
    return (x / norm, y / norm, z / norm)


def unit_vector_to_ra_dec(vec: Sequence[float] | np.ndarray) -> tuple[float, float]:
    """Convert a 3D Cartesian vector to ICRS (ra_deg in [0, 360), dec_deg in [-90, 90])."""
    x, y, z = float(vec[0]), float(vec[1]), float(vec[2])
    norm = math.sqrt(x * x + y * y + z * z)
    if norm <= 0.0:
        raise ValueError("Zero-length vector cannot be converted to RA/Dec.")
    nx, ny, nz = x / norm, y / norm, max(-1.0, min(1.0, z / norm))
    dec_rad = math.asin(nz)
    if abs(nx) < 1e-15 and abs(ny) < 1e-15:
        ra_deg = 0.0
    else:
        ra_deg = math.degrees(math.atan2(ny, nx)) % 360.0
        if ra_deg >= 360.0 or math.isclose(ra_deg, 360.0, abs_tol=1e-12):
            ra_deg = 0.0
    dec_deg = math.degrees(dec_rad)
    return (ra_deg, dec_deg)


def angular_separation_deg(
    ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float
) -> float:
    """Compute exact great-circle angular separation (Vincenty formula) in degrees."""
    ra1 = math.radians(ra1_deg)
    dec1 = math.radians(dec1_deg)
    ra2 = math.radians(ra2_deg)
    dec2 = math.radians(dec2_deg)
    dra = ra2 - ra1

    num1 = math.cos(dec2) * math.sin(dra)
    num2 = math.cos(dec1) * math.sin(dec2) - math.sin(dec1) * math.cos(dec2) * math.cos(dra)
    numerator = math.hypot(num1, num2)
    denominator = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(dra)
    return math.degrees(math.atan2(numerator, denominator))


def _iso_to_julian_date(obs_utc_iso: str) -> float:
    """Convert an ISO-8601 UTC timestamp string to Julian Date."""
    cleaned = obs_utc_iso.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    unix_sec = dt.timestamp()
    return unix_sec / 86400.0 + 2440587.5


ARCSEC_RAD = math.pi / (180.0 * 3600.0)


def _rot_y(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def _rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])


def precession_matrix_iau1976(jd: float) -> np.ndarray:
    """IAU 1976 precession (Lieske et al. 1977, A&A 58, 1): the matrix taking J2000 mean equatorial vectors to
    the mean equator and equinox of date `jd` (ERFA's eraPmat76; axes rotated, as ERFA does).

    The date is meant in TT; passing a UTC Julian date, as this module does, shifts it by about 69 s, which
    moves the result by under 0.001 arcsec.
    """
    t = (jd - 2451545.0) / 36525.0
    zeta = (2306.2181 + (0.30188 + 0.017998 * t) * t) * t * ARCSEC_RAD
    z = (2306.2181 + (1.09468 + 0.018203 * t) * t) * t * ARCSEC_RAD
    theta = (2004.3109 + (-0.42665 - 0.041833 * t) * t) * t * ARCSEC_RAD
    return _rot_z(-z) @ _rot_y(theta) @ _rot_z(-zeta)


def mean_obliquity_iau1980_deg(jd: float) -> float:
    """Mean obliquity of the ecliptic of date in degrees (Meeus 22.2; ERFA's eraObl80)."""
    t = (jd - 2451545.0) / 36525.0
    return (84381.448 + (-46.8150 + (-0.00059 + 0.001813 * t) * t) * t) / 3600.0


# --- Time scales and the sky transforms, on ERFA (pyerfa; docs/architecture/THIRD_PARTY.md) ---------------------
#
# The moment is given in UTC. The positions of the Sun, the Moon and the planets run on TT (ERFA's ephemerides take
# TDB, and say TT serves, as TDB - TT stays under 2 ms); the Earth's rotation runs on UT1. ERFA's own rules decide
# how each comes from the given moment (the notes of eraDat and eraUtctai, as pyerfa 2.0.1.5 ships them):
# - From 1960, the moment is UTC: TT from ERFA's leap-second table; UT1 is taken as UTC.
# - Before 1960 there was no UTC, and ERFA refuses to treat a moment as one; the moment is then taken as UT1 (the
#   mean solar time GMT was), and TT - UT1 comes from Espenak and Meeus' polynomials below.
# - ERFA calls a year five or more after its release (2023) "dubious": leap seconds cannot be predicted.
#
# UT1 - UTC is not known here and is taken as 0: the IERS keeps it within 0.9 s by leap seconds from 1972, as JPL
# Horizons' documentation says ("UTC is kept within 0.9 seconds of UT1 using integer leap-seconds for 1972 and
# later years", ssd.jpl.nasa.gov/horizons/manual.html, read at source 2026-11-11), and before 1972 UTC followed UT2
# within 0.1 s [inference]. The 0.9 s is assumed for the future too, which holds only while the IERS keeps
# inserting leap seconds [open].
UT1_MINUS_UTC_BOUND_S = 0.9
# The leap-second table holds at least until this instant: IERS Bulletin C 72 (Paris, 6 July 2026, read at source
# 2026-11-11) says no leap second at the end of December 2026 and UTC - TAI = -37 s from 2017 January 1 "until
# further notice"; the next possible step is at the end of June 2027
LEAP_SECONDS_KNOWN_UNTIL_UTC = "2027-07-01T00:00:00Z"
# After that, at most one leap second at each end of June and of December, the two dates Bulletin C names ("Leap
# seconds can be introduced in UTC at the end of the months of December or June") [proposed: a bound, not a
# prediction; none has been introduced since 2017]
LEAP_SECOND_STEPS_PER_YEAR = 2
# Standard errors of TT - UT1 before 1960. From -500 to 1800, Morrison and Stephenson's (2004) own, as NASA's Table 1
# gives them at its tabulated years ("Values of Delta T Derived from Historical Records",
# eclipse.gsfc.nasa.gov/SEhelp/deltat2004.html, read at source 2026-11-11); 1 s at 1800 and 0.1 s at 1900 (NASA,
# "Uncertainty in Delta T", eclipse.gsfc.nasa.gov/SEhelp/uncertainty2004.html, Table 2, read at source 2026-11-11);
# before -500, Morrison and Stephenson's 0.8 u^2 s, u = (year - 1820) / 100, from the same page. The bound is three
# of them, the larger of a span's two ends [proposed]
DELTA_T_BOUND_S_FROM_1900 = 0.3
DELTA_T_BOUND_S_FROM_1800 = 3.0
DELTA_T_TABLE1_STANDARD_ERRORS_S: tuple[tuple[int, float], ...] = (
    (-500, 430.0), (-400, 390.0), (-300, 360.0), (-200, 330.0), (-100, 290.0), (0, 260.0), (100, 240.0),
    (200, 210.0), (300, 180.0), (400, 160.0), (500, 140.0), (600, 120.0), (700, 100.0), (800, 80.0), (900, 70.0),
    (1000, 55.0), (1100, 40.0), (1200, 30.0), (1300, 20.0), (1400, 20.0), (1500, 20.0), (1600, 20.0), (1700, 5.0),
    (1750, 2.0), (1800, 1.0),
)


def delta_t_bound_s(year: float) -> float:
    """The most TT - UT1 from `espenak_meeus_delta_t_s` can be off for a moment before 1960, in seconds: three
    standard errors, by the spans above."""
    if year >= 1900.0:
        return DELTA_T_BOUND_S_FROM_1900
    if year >= 1800.0:
        return DELTA_T_BOUND_S_FROM_1800
    table = DELTA_T_TABLE1_STANDARD_ERRORS_S
    if year < table[0][0]:
        u = (year - 1820.0) / 100.0
        return 3.0 * 0.8 * u * u
    for (start, start_error), (end, end_error) in zip(table, table[1:]):
        if start <= year < end:
            return 3.0 * max(start_error, end_error)
    raise AssertionError("unreachable: the table runs to 1800")


# Refraction, where an altitude is shown or compared with a horizon: Saemundsson's formula (Sky and Telescope 72, 70,
# 1986) for a true (airless) altitude h in degrees, R = 1.02 cot(h + 10.3 / (h + 5.11)) arcminutes, within 0.1
# arcminute of Bennett's (1982) formula from the zenith to the horizon, at 101.0 kPa and 10 degrees C (Wikipedia,
# "Atmospheric refraction", citing Saemundsson 1986 and Meeus 1991, read at source 2026-11-11). JPL Horizons assumes
# the same 1010 hPa and 10 degrees C and calls its own refraction good to under 10 arcsec, "but errors may be much
# larger near the horizon (+- 0.3 deg) or fluctuate unpredictably with local weather" (Horizons manual, read at
# source 2026-11-11). ERFA's own A tan z + B tan^3 z model is not used: its notes put it only "better than 20
# arcmin" at the horizon, and it gives about 11 arcmin there where Horizons gives 32 to 39 [tested]. The pressure and
# temperature are the formula's own, not inputs: no other weather is taken.
# Below an airless altitude of -1 degree the formula runs toward its pole (h = -5.11): the refraction is held at its
# value there, as Horizons' refracted answers are (2,327.8 arcsec at every stored elevation below -1) [tested]
REFRACTION_FLOOR_ALT_DEG = -1.0


def refraction_deg(airless_alt_deg: float) -> float:
    """How far refraction lifts a body at airless altitude `airless_alt_deg`, in degrees: Saemundsson's formula at
    1010 hPa and 10 degrees C (above), held at its -1 degree value below that, and never below 0 (the formula dips
    to -0.1 arcsec at the zenith)."""
    h = max(float(airless_alt_deg), REFRACTION_FLOOR_ALT_DEG)
    return max(0.0, 1.02 / math.tan(math.radians(h + 10.3 / (h + 5.11))) / 60.0)


def airless_altitude_deg(refracted_alt_deg: float) -> float:
    """The airless altitude that `refraction_deg` lifts to `refracted_alt_deg`: the inverse, by fixed-point
    iteration (the lift changes by under 0.1 degree per degree of altitude, so each step gains a factor of ten)."""
    h = float(refracted_alt_deg)
    for _ in range(60):
        h, previous = float(refracted_alt_deg) - refraction_deg(h), h
        if abs(h - previous) < 1e-13:
            break
    return h


def espenak_meeus_delta_t_s(year: float) -> float:
    """TT - UT1 in seconds for any moment before 1961, from the expressions of Espenak and Meeus (2006), Five Millennium
    Canon of Solar Eclipses (NASA TP-2006-214141), as NASA's "Polynomial Expressions for Delta T"
    (eclipse.gsfc.nasa.gov/SEhelp/deltatpoly2004.html, read at source 2026-11-11) gives them: polynomials fitted to
    Morrison and Stephenson's (2004) values from -500 to 1800, then to the record since, and before -500 Morrison and
    Stephenson's long-term parabola -20 + 32 u^2, u = (year - 1820) / 100. `year` is the decimal year; the bound for
    each span is `delta_t_bound_s`."""
    if year > 1961.0:
        raise ValueError("These Delta T expressions are used before 1961 only; from 1960 the moment is UTC.")
    if year < -500:
        u = (year - 1820) / 100
        return -20 + 32 * u**2
    if year < 500:
        u = year / 100
        return (10583.6 - 1014.41 * u + 33.78311 * u**2 - 5.952053 * u**3 - 0.1798452 * u**4 + 0.022174192 * u**5
                + 0.0090316521 * u**6)
    if year < 1600:
        u = (year - 1000) / 100
        return (1574.2 - 556.01 * u + 71.23472 * u**2 + 0.319781 * u**3 - 0.8503463 * u**4 - 0.005050998 * u**5
                + 0.0083572073 * u**6)
    if year < 1700:
        t = year - 1600
        return 120 - 0.9808 * t - 0.01532 * t**2 + t**3 / 7129
    if year < 1800:
        t = year - 1700
        return 8.83 + 0.1603 * t - 0.0059285 * t**2 + 0.00013336 * t**3 - t**4 / 1174000
    if year < 1860:
        t = year - 1800
        return (13.72 - 0.332447 * t + 0.0068612 * t**2 + 0.0041116 * t**3 - 0.00037436 * t**4
                + 0.0000121272 * t**5 - 0.0000001699 * t**6 + 0.000000000875 * t**7)
    if year < 1900:
        t = year - 1860
        return 7.62 + 0.5737 * t - 0.251754 * t**2 + 0.01680668 * t**3 - 0.0004473624 * t**4 + t**5 / 233174
    if year < 1920:
        t = year - 1900
        return -2.79 + 1.494119 * t - 0.0598939 * t**2 + 0.0061966 * t**3 - 0.000197 * t**4
    if year < 1941:
        t = year - 1920
        return 21.20 + 0.84493 * t - 0.076100 * t**2 + 0.0020936 * t**3
    t = year - 1950
    return 29.07 + 0.407 * t - t**2 / 233 + t**3 / 2547


@dataclass(frozen=True)
class TimeScales:
    """A UTC moment on the time scales the sky needs, as ERFA two-part Julian dates, with how well each is known.

    `tt` drives the positions of the Sun, the Moon and the planets, `ut1` the Earth's rotation. `tt_bound_s` and
    `ut1_bound_s` are the most each can be off, in seconds, under the rules above; `basis` says in a sentence how the
    moment was read."""

    tt: tuple[float, float]
    ut1: tuple[float, float]
    tt_bound_s: float
    ut1_bound_s: float
    basis: str


def _utc_datetime(obs_utc_iso: str) -> datetime:
    cleaned = obs_utc_iso.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    return (moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment).astimezone(timezone.utc)


# warnings.catch_warnings changes process-wide state and is not thread-safe; the API runs routes in threads
_ERFA_WARNINGS = threading.Lock()


def _erfa_quiet(call, *args):
    """An ERFA call, with the warnings it raised: ERFA flags a dubious year or a date outside a theory's span by a
    warning status, and the caller decides what that means. One call at a time (see the lock above)."""
    with _ERFA_WARNINGS, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", erfa.ErfaWarning)
        result = call(*args)
    return result, [str(w.message) for w in caught if issubclass(w.category, erfa.ErfaWarning)]


@lru_cache(maxsize=4096)
def time_scales(obs_utc_iso: str) -> TimeScales:
    """The moment `obs_utc_iso` (ISO 8601, UTC) on TT and UT1, by the rules above."""
    moment = _utc_datetime(obs_utc_iso)
    fields = (moment.year, moment.month, moment.day, moment.hour, moment.minute,
              moment.second + moment.microsecond / 1e6)
    if moment.year < 1960:
        ut1 = erfa.dtf2d("", *fields)
        year = moment.year + (moment.timetuple().tm_yday - 0.5) / 365.25
        delta_t = espenak_meeus_delta_t_s(year)
        tt = (ut1[0], ut1[1] + delta_t / 86400.0)
        bound = delta_t_bound_s(year)
        return TimeScales(
            tt=tt, ut1=ut1, tt_bound_s=bound, ut1_bound_s=0.0,
            basis=(
                "A moment before 1960, when there was no UTC, is taken as UT1, and TT - UT1 comes from Espenak and "
                f"Meeus (2006), within {bound:g} s"
                + (", a wider bound than later: the Earth's rotation is less well known before 1800." if year < 1800
                   else ".")
            ),
        )
    utc, _ = _erfa_quiet(erfa.dtf2d, "UTC", *fields)
    tai, flags = _erfa_quiet(erfa.utctai, *utc)
    tt = erfa.taitt(*tai)
    ut1, _ = _erfa_quiet(erfa.utcut1, *utc, 0.0)
    known_until = _utc_datetime(LEAP_SECONDS_KNOWN_UNTIL_UTC)
    if moment < known_until:
        tt_bound = 0.0
        basis = (
            "The moment is UTC; TT comes from ERFA's leap-second table, and UT1 is taken as UTC, within "
            f"{UT1_MINUS_UTC_BOUND_S:g} s."
        )
    else:
        years = (moment - known_until).total_seconds() / (365.25 * 86400.0)
        tt_bound = float(math.floor(years * LEAP_SECOND_STEPS_PER_YEAR) + 1)
        basis = (
            "The moment is UTC, later than the leap seconds announced so far (to mid 2027), so TT may be off by up "
            f"to {tt_bound:g} s of leap seconds not yet known"
            + (" (ERFA calls the year dubious)" if flags else "")
            + f"; UT1 is taken as UTC, within {UT1_MINUS_UTC_BOUND_S:g} s, if the IERS keeps it so."
        )
    return TimeScales(tt=tuple(tt), ut1=tuple(ut1), tt_bound_s=tt_bound, ut1_bound_s=UT1_MINUS_UTC_BOUND_S, basis=basis)


def earth_position_velocity(tt: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """ERFA's eraEpv00 at TT: the Earth's heliocentric and barycentric position and velocity (au, au/day, BCRS), as
    two structured arrays with fields `p` and `v`. Outside 1900 to 2100 ERFA warns, and its errors are about double
    by 1800 (its notes); the caller states that."""
    (pvh, pvb), _ = _erfa_quiet(erfa.epv00, *tt)
    return pvh, pvb


class PlaceOutOfRange(ValueError):
    """A latitude outside -90 to 90 degrees or a longitude outside -180 to 180, or either not finite: no place on the
    Earth is named so. A latitude past a pole would answer for the point on the far meridian, its azimuth half a turn
    off (the sixth version's audit), so it is refused, not folded."""


def require_place(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """The door of every function that takes a place: the latitude and longitude as floats when the latitude lies in
    [-90, 90] degrees and the longitude in [-180, 180], both finite, and PlaceOutOfRange otherwise. One rule for both,
    as the API's sky-context request has it; the message names the rule, never the value."""
    lat, lon = float(lat_deg), float(lon_deg)
    if not (math.isfinite(lat) and -90.0 <= lat <= 90.0):
        raise PlaceOutOfRange("A latitude is a finite angle from -90 to 90 degrees.")
    if not (math.isfinite(lon) and -180.0 <= lon <= 180.0):
        raise PlaceOutOfRange("A longitude is a finite angle from -180 to 180 degrees.")
    return lat, lon


@lru_cache(maxsize=1024)
def observer_astrom(obs_utc_iso: str, lat_deg: float, lon_deg: float) -> np.ndarray:
    """ERFA's star-independent astrometry parameters (eraApco) for an observer at sea level at geodetic (`lat_deg`,
    `lon_deg`, WGS84, east positive) and the moment: the Earth from eraEpv00, IAU 2006/2000A precession and nutation
    (eraPnm06a), the CIO locator, the Earth rotation angle at UT1, no polar motion and no refraction (see
    `refraction_deg`). The same steps as ERFA's eraApco13, with TT and UT1 from `time_scales`. Treat the result as
    read only: it is cached."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    scales = time_scales(obs_utc_iso)
    pvh, pvb = earth_position_velocity(scales.tt)
    x, y = erfa.bpn2xy(erfa.pnm06a(*scales.tt))
    s = erfa.s06(*scales.tt, x, y)
    theta = erfa.era00(*scales.ut1)
    sp = erfa.sp00(*scales.tt)
    return erfa.apco(*scales.tt, pvb, pvh["p"], x, y, s, theta, math.radians(float(lon_deg)),
                     math.radians(float(lat_deg)), 0.0, 0.0, 0.0, sp, 0.0, 0.0)


def _solar_deflection_arcsec(direction: np.ndarray, astrom: np.ndarray) -> float:
    unit = np.asarray(direction, dtype=np.float64) / float(np.linalg.norm(direction))
    bent = erfa.ldsun(unit, astrom["eh"], astrom["em"])
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(unit, bent))), float(unit @ bent))) * 3600.0


def solar_deflection_arcsec(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str) -> float:
    """How far `icrs_to_altaz` moves the ICRS direction (ra_deg, dec_deg) for light deflection by the Sun, in arcsec:
    ERFA's eraLdsun, as for a source at infinity.

    ERFA restrains it near the Sun: "The deflection is restrained when the angle between the star and the center of
    the Sun is less than a threshold value, falling to zero deflection for zero separation. The chosen threshold value
    is within the solar limb for all solar-system applications, and is about 5 arcminutes for the case of a
    terrestrial observer" (eraLdsun, note 2; eraLd, note 4: "the deflection is artificially reduced, reaching zero for
    phi = 0"), as pyerfa 2.0.1.5 ships them, read 2026-11-11. So it is 0 at the Sun's centre, peaks at about 5.8
    arcsec near that threshold (5.78 at 292 arcsec from the centre, measured at one moment), is about 1.75 at the limb
    and falls off beyond [tested: tests/integration/test_sky_transforms.py]."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    return _solar_deflection_arcsec(np.asarray(ra_dec_to_unit_vector(ra_deg, dec_deg)), astrom)


def _observed_altaz(ri_rad: float, di_rad: float, astrom: np.ndarray) -> tuple[float, float]:
    azimuth, zenith_distance, _, _, _ = erfa.atioq(ri_rad, di_rad, astrom)
    return (90.0 - math.degrees(float(zenith_distance)), math.degrees(float(azimuth)) % 360.0)


def icrs_to_altaz(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    obs_utc_iso: str,
    *,
    refracted: bool = False,
) -> tuple[float, float]:
    """Convert an ICRS (astrometric) direction (ra_deg, dec_deg) to the horizontal (alt_deg, az_deg, azimuth from
    north through east) an observer at sea level at (`lat_deg`, `lon_deg`) sees at the moment, on ERFA (eraAtciq,
    eraAtioq): light deflection by the Sun, annual and diurnal aberration, IAU 2006/2000A precession and nutation, and
    the Earth's rotation at UT1.

    This is the apparent place: everything that states where a body or a star is seen uses it. It is not a rotation
    (aberration and deflection move directions unequally); a caller that needs one, such as the pose's algebra, uses
    `rigid_horizon_rotation` (docs/science/SKY_TRANSFORMS.md).

    Every direction is transformed, one near or inside the Sun's disk too (where no star is seen): the deflection
    there is ERFA's restrained one, at most about 5.8 arcsec, and `solar_deflection_arcsec` gives it for the direction.

    The altitude is airless unless `refracted`, which adds `refraction_deg` (Saemundsson, 1010 hPa and 10 degrees C;
    the real air differs most near the horizon). The direction is taken as seen from the Earth: a star's parallax
    (under 1 arcsec) is left out, and a near body must come as seen from the place (services/science/ephemeris.py).
    Left out: polar motion (under 1 arcsec [inference]) and UT1 - UTC (taken as 0, within 0.9 s: up to 13.5 arcsec of
    rotation; see `time_scales`).

    It answers for any moment an ISO 8601 string holds (years 1 to 9999), as it always did; `time_scales` states the
    wider bounds far from today. Only the bodies (services/science/ephemeris.py) keep a range of 1800 to 2050.
    """
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    ri, di = erfa.atciq(math.radians(float(ra_deg)), math.radians(float(dec_deg)), 0.0, 0.0, 0.0, 0.0, astrom)
    alt_deg, az_deg = _observed_altaz(ri, di, astrom)
    return (alt_deg + refraction_deg(alt_deg), az_deg) if refracted else (alt_deg, az_deg)


def altaz_to_icrs(
    alt_deg: float,
    az_deg: float,
    lat_deg: float,
    lon_deg: float,
    obs_utc_iso: str,
    *,
    refracted: bool = False,
) -> tuple[float, float]:
    """Convert a horizontal (alt_deg, az_deg) to the ICRS (astrometric) direction (ra_deg, dec_deg) that appears
    there: the inverse of `icrs_to_altaz` (ERFA's eraAtoiq and eraAticq), with the same steps, conditions and
    omissions. The altitude is read as airless unless `refracted` (then `airless_altitude_deg` undoes the
    refraction first)."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    airless = airless_altitude_deg(alt_deg) if refracted else float(alt_deg)
    ri, di = erfa.atoiq("A", math.radians(float(az_deg)), math.radians(90.0 - airless), astrom)
    rc, dc = erfa.aticq(ri, di, astrom)
    return (math.degrees(float(rc)) % 360.0, math.degrees(float(dc)))


# The rigid frame rotation: what it leaves out of the apparent place, at most, for moments from 1800 to 2050, each part
# from ERFA [tested: tests/integration/test_sky_transforms.py holds every direction it draws within the total]:
# - aberration, the observer's barycentric speed over ERFA_CMPS (eraApco puts the Earth's rotation into astrom `v` and
#   sets `diurab` to 0): the Earth's greatest barycentric speed from eraEpv00 over the span, 30.3158 km/s, plus the
#   rotation at the equator at sea level, 0.4651 km/s, is 21.178 arcsec (the annual part alone 20.858)
# - light deflection by the Sun: ERFA's restrained eraLdsun at its peak, at the Earth's least distance from the Sun in
#   the span (0.983166 au), 5.857 arcsec (from ERFA_SRS; 1.75 at the limb, under 0.01 a degree from the Sun's centre)
# Together 27.035, held as 27.1. Measured by the ephemeris lane on 2026-11-11: at most 26.62 arcsec over 6,000 random
# moments, places and directions, half of them within 20 arcmin of the Sun
RIGID_ABERRATION_MAX_ARCSEC = 21.178
RIGID_DEFLECTION_MAX_ARCSEC = 5.857
RIGID_FRAME_OMISSION_ARCSEC = 27.1


@lru_cache(maxsize=1024)
def _rigid_rotation(obs_utc_iso: str, lat_deg: float, lon_deg: float) -> np.ndarray:
    astrom = observer_astrom(obs_utc_iso, lat_deg, lon_deg).copy()
    # eraApco already sets it to 0 (the rotation's velocity is in `v`); set here so the steps below are a rotation
    astrom["diurab"] = 0.0
    columns = []
    for axis in np.eye(3):
        ri, di = erfa.c2s(astrom["bpn"] @ axis)
        alt_deg, az_deg = _observed_altaz(erfa.anp(ri), di, astrom)
        a, z = math.radians(alt_deg), math.radians(az_deg)
        columns.append((math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)))
    rotation = np.array(columns, dtype=np.float64).T
    rotation.setflags(write=False)
    return rotation


def rigid_horizon_rotation(lat_deg: float, lon_deg: float, obs_utc_iso: str) -> np.ndarray:
    """The rigid frame rotation: the 3 x 3 matrix that turns an ICRS unit vector into (east, north, up) at sea level
    at (`lat_deg`, `lon_deg`) at the moment, through ERFA's steps only: the bias-precession-nutation matrix (IAU
    2006/2000A), the Earth rotation angle at UT1 and the place's horizon (eraAtioq), as in `icrs_to_altaz` but without
    light deflection or aberration.

    It is a rotation, so every angle between two directions is kept, which the pose's algebra needs
    (services/science/pose_estimate.py fits a gnomonic camera to ICRS directions). It never states where anything is
    seen: it leaves out up to RIGID_FRAME_OMISSION_ARCSEC (27.1 arcsec) of the apparent place, aberration up to
    RIGID_ABERRATION_MAX_ARCSEC (21.2) and light deflection up to RIGID_DEFLECTION_MAX_ARCSEC (5.9), unequally across
    the sky. Positions drawn through a pose leave out up to that much, and the pose's bound counts it. Refraction, polar
    motion and UT1 - UTC are left out as in `icrs_to_altaz`. Treat the result as read only: it is cached."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    return _rigid_rotation(obs_utc_iso, float(lat_deg), float(lon_deg))


def icrs_to_rigid_altaz(
    ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str
) -> tuple[float, float]:
    """An ICRS direction turned by `rigid_horizon_rotation` to (alt_deg, az_deg): the pose's frame, not where the
    direction is seen (that is `icrs_to_altaz`)."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    east, north, up = rigid_horizon_rotation(lat_deg, lon_deg, obs_utc_iso) @ np.asarray(
        ra_dec_to_unit_vector(ra_deg, dec_deg), dtype=np.float64
    )
    return (math.degrees(math.atan2(up, math.hypot(east, north))), math.degrees(math.atan2(east, north)) % 360.0)


def rigid_altaz_to_icrs(
    alt_deg: float, az_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str
) -> tuple[float, float]:
    """The inverse of `icrs_to_rigid_altaz`: (alt_deg, az_deg) in the pose's frame turned back to ICRS by the
    rotation's transpose."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    a, z = math.radians(float(alt_deg)), math.radians(float(az_deg))
    horizontal = np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])
    return unit_vector_to_ra_dec(rigid_horizon_rotation(lat_deg, lon_deg, obs_utc_iso).T @ horizontal)


# Aberration's second order, (v / c)^2 of a radian at most (about 0.002 arcsec), held as 0.01 arcsec
SECOND_ORDER_ABERRATION_ARCSEC = 0.01
# ERFA's restrained deflection peaks about 292 arcsec from the Sun's centre and falls beyond it: a direction within
# 0.1 degree of the centre is given the peak, RIGID_DEFLECTION_MAX_ARCSEC
DEFLECTION_PEAK_WITHIN_DEG = 0.1


def _horizontal(alt_deg: float, az_deg: float) -> np.ndarray:
    a, z = math.radians(float(alt_deg)), math.radians(float(az_deg))
    return np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])


def _least_rotation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The rotation about a x b that takes the unit vector a to the unit vector b (Rodrigues): the identity when they
    are the same direction; two opposite directions have no least rotation (any axis across them serves), so they
    raise ValueError."""
    axis = np.cross(a, b)
    s, c = float(np.linalg.norm(axis)), float(a @ b)
    if s == 0.0:
        if c < 0.0:
            raise ValueError("Two opposite directions have no least rotation between them.")
        return np.eye(3)
    k = axis / s
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + s * kx + (1.0 - c) * (kx @ kx)


@lru_cache(maxsize=1024)
def _anchored_rotation(
    beacon_ra_deg: float, beacon_dec_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str
) -> np.ndarray:
    rigid = rigid_horizon_rotation(lat_deg, lon_deg, obs_utc_iso)
    beacon = np.asarray(ra_dec_to_unit_vector(beacon_ra_deg, beacon_dec_deg), dtype=np.float64)
    seen = _horizontal(*icrs_to_altaz(beacon_ra_deg, beacon_dec_deg, lat_deg, lon_deg, obs_utc_iso))
    rotation = _least_rotation(rigid @ beacon, seen) @ rigid
    rotation.setflags(write=False)
    return rotation


def anchored_horizon_rotation(
    beacon_ra_deg: float, beacon_dec_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str
) -> np.ndarray:
    """The rigid frame rotation anchored on a beacon: `rigid_horizon_rotation`, then the least rotation that takes
    the beacon's ICRS direction (`beacon_ra_deg`, `beacon_dec_deg`) to where the beacon is seen, its apparent place
    (`icrs_to_altaz`). It is a rotation, so a gnomonic camera fitted through it is one TAN WCS over ICRS directions;
    the beacon lands exactly where it is seen, and every other direction lies within
    `anchored_rotation_omission_arcsec` of its apparent place. Treat the result as read only: it is cached."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    return _anchored_rotation(float(beacon_ra_deg), float(beacon_dec_deg), float(lat_deg), float(lon_deg), obs_utc_iso)


def icrs_to_frame_altaz(rotation: np.ndarray, ra_deg: float, dec_deg: float) -> tuple[float, float]:
    """An ICRS direction turned by `rotation` (east, north, up) to (alt_deg, az_deg)."""
    east, north, up = rotation @ np.asarray(ra_dec_to_unit_vector(ra_deg, dec_deg), dtype=np.float64)
    return (math.degrees(math.atan2(up, math.hypot(east, north))), math.degrees(math.atan2(east, north)) % 360.0)


def frame_altaz_to_icrs(rotation: np.ndarray, alt_deg: float, az_deg: float) -> tuple[float, float]:
    """The inverse of `icrs_to_frame_altaz`: the rotation's transpose."""
    return unit_vector_to_ra_dec(rotation.T @ _horizontal(alt_deg, az_deg))


def solar_deflection_at_separation_arcsec(
    separation_deg: float, lat_deg: float, lon_deg: float, obs_utc_iso: str
) -> float:
    """The light deflection ERFA applies (eraLdsun, as in `icrs_to_altaz`) to a direction `separation_deg` from the
    Sun's centre as seen from the place at the moment; within DEFLECTION_PEAK_WITHIN_DEG, the peak; past 180 degrees,
    as at 180. A separation that is not a finite angle of 0 or more raises ValueError."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    if not (math.isfinite(separation_deg) and separation_deg >= 0.0):
        raise ValueError(f"A separation from the Sun is a finite angle of 0 degrees or more, not {separation_deg!r}.")
    if separation_deg <= DEFLECTION_PEAK_WITHIN_DEG:
        return RIGID_DEFLECTION_MAX_ARCSEC
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    side = np.cross(sun, np.array([0.0, 0.0, 1.0]) if abs(sun[2]) < 0.9 else np.array([1.0, 0.0, 0.0]))
    side /= float(np.linalg.norm(side))
    psi = math.radians(min(180.0, float(separation_deg)))
    return _solar_deflection_arcsec(math.cos(psi) * sun + math.sin(psi) * side, astrom)


def anchored_rotation_omission_arcsec(
    beacon_ra_deg: float,
    beacon_dec_deg: float,
    fields: Sequence[tuple[float, float, float]],
    lat_deg: float,
    lon_deg: float,
    obs_utc_iso: str,
) -> float:
    """At most how far `anchored_horizon_rotation` puts a direction from its apparent place, over every direction
    inside any of `fields`, each (centre RA, centre Dec, radius), in degrees (a frame: its centre and half its
    diagonal field of view): k 2 sin(theta / 2) + 2 D + SECOND_ORDER_ABERRATION_ARCSEC, in arcsec.

    - theta: the farthest any such direction lies from the beacon, at most the beacon's angle from a field's centre
      plus its radius (the triangle inequality), capped at 180 degrees.
    - k: the observer's barycentric speed over the speed of light at the moment (astrom `v`, which eraApco gives
      the Earth's rotation too), in arcsec. First-order aberration moves a direction p by k (u - (u . p) p), u the
      direction of motion; after the least rotation that makes the beacon m exact, what is left at n is
      k [u (1 - m . n) + (u . n) (m - n)], whose length over every u is at most k |m - n|, the chord 2 sin(theta / 2)
      [inference: the largest eigenvalue of that map at 12 angles to 5 digits; tested against ERFA].
    - D: the larger of the deflection ERFA applies at the nearest any such direction comes to the Sun's centre (the
      Sun's angle from a field's centre less its radius) and the deflection at the beacon's own angle from the Sun,
      counted once for the direction and once for the beacon's own, which the anchoring carries to every direction.
      A beacon inside a field is never nearer the Sun than that field's nearest point, so there the larger is the
      field's (beyond DEFLECTION_PEAK_WITHIN_DEG the deflection only falls, and within it the field's is the peak).
    Refraction, polar motion and UT1 - UTC are left out as in `icrs_to_altaz` (the same for both places). No field,
    or a radius that is not a finite angle from 0 to 180 degrees, raises ValueError."""
    lat_deg, lon_deg = require_place(lat_deg, lon_deg)
    if not fields:
        raise ValueError("The term needs at least one field.")
    for _, _, radius_deg in fields:
        if not (math.isfinite(radius_deg) and 0.0 <= radius_deg <= 180.0):
            raise ValueError(f"A field's radius is a finite angle from 0 to 180 degrees, not {radius_deg!r}.")
    beacon = np.asarray(ra_dec_to_unit_vector(beacon_ra_deg, beacon_dec_deg), dtype=np.float64)
    astrom = observer_astrom(obs_utc_iso, float(lat_deg), float(lon_deg))
    sun = -np.asarray(astrom["eh"], dtype=np.float64)
    farthest, nearest_sun = 0.0, 180.0
    for ra_deg, dec_deg, radius_deg in fields:
        centre = np.asarray(ra_dec_to_unit_vector(ra_deg, dec_deg), dtype=np.float64)
        farthest = max(farthest, math.degrees(math.acos(max(-1.0, min(1.0, float(beacon @ centre))))) + radius_deg)
        sun_from_centre = math.degrees(math.acos(max(-1.0, min(1.0, float(sun @ centre)))))
        nearest_sun = min(nearest_sun, max(0.0, sun_from_centre - radius_deg))
    beacon_sun = math.degrees(math.acos(max(-1.0, min(1.0, float(beacon @ sun)))))
    k = float(np.linalg.norm(astrom["v"])) * 180.0 / math.pi * 3600.0
    chord = 2.0 * math.sin(math.radians(min(180.0, farthest)) / 2.0)
    deflection = max(
        solar_deflection_at_separation_arcsec(nearest_sun, lat_deg, lon_deg, obs_utc_iso),
        solar_deflection_at_separation_arcsec(beacon_sun, lat_deg, lon_deg, obs_utc_iso),
    )
    return k * chord + 2.0 * deflection + SECOND_ORDER_ABERRATION_ARCSEC


def catalog_cartesian_pc(star: CatalogStar) -> tuple[float, float, float]:
    """Compute 3D ICRS Cartesian coordinates in parsecs at the median of the star's own catalog distance.

    That distance is 1000/parallax for a Hipparcos 2007 row and the Bailer-Jones et al. (2021) photogeometric
    estimate for a Gaia DR3 row; `star.distance_source` names which.
    """
    if not (0.0 < star.lower_pc <= star.median_pc <= star.upper_pc):
        raise ValueError(
            f"Invalid distance bounds for {star.common_name} ({star.distance_source}): "
            f"({star.lower_pc}, {star.median_pc}, {star.upper_pc}) pc"
        )
    ux, uy, uz = ra_dec_to_unit_vector(star.ra_deg, star.dec_deg)
    d = float(star.median_pc)
    return (ux * d, uy * d, uz * d)


def propagate_star_epoch(star: CatalogStar, target_year_ce: float) -> CatalogStar:
    """Propagate a CatalogStar from its reference epoch to `target_year_ce` using 3D space velocity.

    When `star.rv_km_s` is `None` (no radial velocity in the star's catalog row), the radial velocity
    term is omitted (`0.0 km/s`); over 1800 to 2050 this changes sky position by at most ~0.1 arcsec
    for the navigational stars, well inside the 55 arcsec bound.
    """
    dt_yr = float(target_year_ce) - float(star.epoch_year)
    if abs(dt_yr) < 1e-12:
        return star

    ra_rad = math.radians(star.ra_deg)
    dec_rad = math.radians(star.dec_deg)

    r_hat = np.array(
        [
            math.cos(dec_rad) * math.cos(ra_rad),
            math.cos(dec_rad) * math.sin(ra_rad),
            math.sin(dec_rad),
        ],
        dtype=np.float64,
    )
    e_ra = np.array([-math.sin(ra_rad), math.cos(ra_rad), 0.0], dtype=np.float64)
    e_dec = np.array(
        [
            -math.sin(dec_rad) * math.cos(ra_rad),
            -math.sin(dec_rad) * math.sin(ra_rad),
            math.cos(dec_rad),
        ],
        dtype=np.float64,
    )

    mas_to_rad = math.pi / (180.0 * 3600.0 * 1000.0)
    mu_ra_rad_yr = float(star.pm_ra_mas_yr) * mas_to_rad
    mu_dec_rad_yr = float(star.pm_dec_mas_yr) * mas_to_rad

    km_s_to_pc_yr = 1.022712165045695e-6
    d0_pc = float(star.median_pc)
    rv_km_s = float(star.rv_km_s) if star.rv_km_s is not None else 0.0

    pos0_pc = r_hat * d0_pc
    vel_pc_yr = (
        (d0_pc * mu_ra_rad_yr) * e_ra
        + (d0_pc * mu_dec_rad_yr) * e_dec
        + (rv_km_s * km_s_to_pc_yr) * r_hat
    )
    pos1_pc = pos0_pc + vel_pc_yr * dt_yr
    new_dist_pc = float(np.linalg.norm(pos1_pc))
    new_ra_deg, new_dec_deg = unit_vector_to_ra_dec(pos1_pc)

    scale = max(1e-6, new_dist_pc / d0_pc)
    return replace(
        star,
        ra_deg=new_ra_deg,
        dec_deg=new_dec_deg,
        lower_pc=star.lower_pc * scale,
        median_pc=star.median_pc * scale,
        upper_pc=star.upper_pc * scale,
        epoch_year=float(target_year_ce),
    )


def compute_light_travel_time_description(distance_pc: float) -> dict[str, float | str]:
    """Compute human-readable light-travel lookback time from distance in parsecs."""
    d_pc = max(1e-12, float(distance_pc))
    light_years = d_pc * 3.261563777
    light_minutes = light_years * 525960.0
    light_seconds = light_minutes * 60.0

    if light_seconds < 90.0:
        desc = f"{light_seconds:.2f} light-seconds"
    elif light_minutes < 600.0:
        desc = f"{light_minutes:.1f} light-minutes"
    elif light_years < 100.0:
        desc = f"{light_years:.1f} light-years"
    else:
        desc = f"{light_years:.0f} light-years"

    return {
        "distancePc": d_pc,
        "lightYears": float(light_years),
        "lightMinutes": float(light_minutes),
        "lightSeconds": float(light_seconds),
        "light_years": float(light_years),
        "light_minutes": float(light_minutes),
        "light_seconds": float(light_seconds),
        "description": desc,
        "formatted": desc,
    }
