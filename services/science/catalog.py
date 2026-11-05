"""Pinned Gaia DR3 & Hipparcos bright-star catalog and coordinate transformation math."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Sequence

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


def icrs_to_altaz(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    obs_utc_iso: str,
) -> tuple[float, float]:
    """Convert ICRS (ra_deg, dec_deg) to geometric horizontal (alt_deg, az_deg) at the observer and moment.

    The direction is precessed (IAU 1976) to the mean equator and equinox of date and turned to the horizon
    with mean sidereal time. Left out: nutation and annual aberration (together at most 37 arcsec against
    ERFA-checked transforms over 2017 to 2050, tests/integration/test_sky_transforms.py), UT1 - UTC (under
    0.9 s of rotation, at most 14 arcsec) and refraction: the altitude is airless, not what a camera sees
    near the horizon.
    """
    jd = _iso_to_julian_date(obs_utc_iso)
    ra_deg, dec_deg = unit_vector_to_ra_dec(
        precession_matrix_iau1976(jd) @ np.asarray(ra_dec_to_unit_vector(ra_deg, dec_deg))
    )
    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t_ut1**2)
        - (t_ut1**3) / 38710000.0
    ) % 360.0
    lst_deg = (gmst_deg + lon_deg) % 360.0
    ha_rad = math.radians((lst_deg - ra_deg) % 360.0)

    dec_rad = math.radians(dec_deg)
    lat_rad = math.radians(lat_deg)

    sin_alt = math.sin(dec_rad) * math.sin(lat_rad) + math.cos(dec_rad) * math.cos(
        lat_rad
    ) * math.cos(ha_rad)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)

    y = -math.sin(ha_rad) * math.cos(dec_rad)
    x = math.sin(dec_rad) * math.cos(lat_rad) - math.cos(dec_rad) * math.sin(
        lat_rad
    ) * math.cos(ha_rad)
    az_deg = math.degrees(math.atan2(y, x)) % 360.0
    alt_deg = math.degrees(alt_rad)
    return (alt_deg, az_deg)


def altaz_to_icrs(
    alt_deg: float,
    az_deg: float,
    lat_deg: float,
    lon_deg: float,
    obs_utc_iso: str,
) -> tuple[float, float]:
    """Convert geometric horizontal (alt_deg, az_deg) to ICRS (ra_deg, dec_deg): the inverse of `icrs_to_altaz`,
    with the same omissions."""
    jd = _iso_to_julian_date(obs_utc_iso)
    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * (t_ut1**2)
        - (t_ut1**3) / 38710000.0
    ) % 360.0
    lst_deg = (gmst_deg + lon_deg) % 360.0

    alt_rad = math.radians(alt_deg)
    az_rad = math.radians(az_deg)
    lat_rad = math.radians(lat_deg)

    sin_dec = math.sin(alt_rad) * math.sin(lat_rad) + math.cos(alt_rad) * math.cos(
        lat_rad
    ) * math.cos(az_rad)
    sin_dec = max(-1.0, min(1.0, sin_dec))
    dec_rad = math.asin(sin_dec)

    y = -math.sin(az_rad) * math.cos(alt_rad)
    x = math.sin(alt_rad) * math.cos(lat_rad) - math.cos(alt_rad) * math.sin(
        lat_rad
    ) * math.cos(az_rad)
    ha_deg = math.degrees(math.atan2(y, x)) % 360.0
    ra_of_date_deg = (lst_deg - ha_deg) % 360.0
    dec_of_date_deg = math.degrees(dec_rad)
    # Back from the mean equator and equinox of date to ICRS: the transpose of the precession matrix
    return unit_vector_to_ra_dec(
        precession_matrix_iau1976(jd).T @ np.asarray(ra_dec_to_unit_vector(ra_of_date_deg, dec_of_date_deg))
    )


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
    """Propagate a CatalogStar from its reference epoch to `target_year_ce` using full 3D space velocity."""
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

    pos0_pc = r_hat * d0_pc
    vel_pc_yr = (
        (d0_pc * mu_ra_rad_yr) * e_ra
        + (d0_pc * mu_dec_rad_yr) * e_dec
        + (float(star.rv_km_s) * km_s_to_pc_yr) * r_hat
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
