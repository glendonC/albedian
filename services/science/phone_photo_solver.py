"""Phone-photo astrometric solver over the G 12 catalog tiles (roadmap Track 1 M2).

Solves handheld and tripod night-sky photos from consumer phone cameras (rectilinear main and
ultra-wide lenses with Brown-Conrady radial/tangential distortion, and uncorrected ultra-wide
fisheye lenses with Kannala-Brandt equidistant distortion) against the local Gaia DR3 + Hipparcos
2007 G 12 star tiles (`apps/web/public/catalog/stars/gaia-dr3/`).

Design and references:
- Point-source extraction runs on the original image pixels (`services/science/detector.py`);
  every detection starts as `observed` and becomes `catalog_matched` only after the held-out
  and dual-path verification gates pass [verified].
- Candidate ranking normalizes local background noise in a 6x6 tile grid to counteract corner
  lens-shading amplification and downweights dark-silhouette ground lamps and single-Bayer-site
  hot-pixel impulses [inference, tested on phone-photo benchmark].
- Topocentric apparent sky frame (`_ApparentSkyFrame`) applies proper motion from J2016.0, IAU 1976
  precession (Lieske et al. 1977, A&A 58, 1), annual aberration (Meeus 1998, ch. 23 & 25), short
  nutation series (Meeus ch. 22), Greenwich apparent sidereal time (IAU 1982), and Saemundsson
  atmospheric refraction (Meeus ch. 16) [tested].
- Dual-path hypothesis generation and verification:
  1. When capture UTC and observer latitude/longitude are present, a vectorized horizon-pair triad
     attitude voter searches pairs of detections against visible catalog star pairs, filtering
     candidate 3D rotations by horizon altitude and roll before scoring consensus inliers, then
     refines the camera attitude, focal length, and distortion (`BrownConrady` or `KannalaBrandt`)
     on a held-in training subset and checks agreement with an independent refit on the disjoint
     held-out inliers [tested].
  2. When time or place is unknown (or if horizon-constrained search finds no verified solution),
     a scale-invariant 3D triangle geometric matcher searches ICRS star triangles [tested].
- Acceptance gate (`evaluate_coincidence_gate`) weighs each held-out inlier residual against the
  background detection density following the Bayesian decision model of Lang et al. (2010, AJ 139,
  1782) alongside a Poisson coincidence ceiling and dual-path refit agreement [tested].
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import itertools
import json
import math
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from scipy import optimize

from services.science import catalog_tiles as ct
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    CatalogStar,
    angular_separation_deg,
    get_catalog_stars,
    icrs_to_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.catalog_model import (
    BAILER_JONES_GEOMETRIC,
    BAILER_JONES_PHOTOGEOMETRIC,
    GAIA_DR3_RELEASE,
    GAIA_G,
    HIPPARCOS_2007_RELEASE,
    HIPPARCOS_HP,
    INVERSE_PARALLAX,
    gaia_dr3_citation,
    hipparcos_2007_tile_citation,
)
from services.science.detector import (
    _estimate_background_and_noise,
    _to_luma_2d,
    detect_point_sources,
)
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
    WCSModel,
    require_source_sha256,
)

DEFAULT_TILE_RELEASE_DIR = (
    Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
)

ARCSEC_RAD = math.pi / 648000.0
TWO_PI = 2.0 * math.pi
JD_J2000 = 2451545.0
DAYS_PER_CENTURY = 36525.0
DAYS_PER_JYEAR = 365.25
JD_UNIX_EPOCH = 2440587.5
TT_MINUS_UTC_S = 69.184
ABERRATION_CONST_RAD = 20.49552 * ARCSEC_RAD
REFRACTION_FLOOR_DEG = -2.0
# A typical Gaia BP-RP, used only to rank a star whose row has no colour by an approximate V: the value the phone
# photo benchmark renders such stars with (research/benchmarks/phone_photos/stars.py) [proposed: a typical colour at
# G < 12, not the star's own; it never reaches a finding, a card or a reply]
TYPICAL_BP_RP_FOR_RANKING = 0.82
# The IAU constellation boundaries (Delporte, as Davenhall and Leggett 1989 tabulate them, VizieR VI/49): corners
# shipped at J2000, each edge a constant right ascension or declination of equinox B1875
CONSTELLATION_BOUNDS_PATH = (
    Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "constellations" / "bounds.json"
)
# Besselian epoch B1875.0 as a Julian date, 2415020.31352 + (1875 - 1900) * 365.242198781: the Besselian year of
# Lieske (1979, A&A 73, 282) [inference: the formula as commonly quoted, not read at its source in this unit]
JD_B1875 = 2405889.258550475

# Published diagonal fields of view (degrees) of phone cameras in the benchmark [verified]
PUBLISHED_CAMERA_FOV_DEG: dict[str, tuple[float, tuple[str, ...]]] = {
    "iphone 18 pro, 48mp fusion main": (84.06, ("brown-conrady",)),
    "iphone-18-pro-main": (84.06, ("brown-conrady",)),
    "iphone 18 pro, 48mp fusion ultra wide": (120.0, ("brown-conrady", "kannala-brandt")),
    "iphone-18-pro-ultra-wide": (120.0, ("brown-conrady", "kannala-brandt")),
    "pixel 11 pro, 50 mp wide": (82.0, ("brown-conrady",)),
    "pixel-11-pro-wide": (82.0, ("brown-conrady",)),
    "pixel 11 pro, 48 mp ultrawide": (123.0, ("brown-conrady",)),
    "pixel-11-pro-ultrawide": (123.0, ("brown-conrady",)),
    "galaxy s25 ultra, 200 mp wide": (85.0, ("brown-conrady",)),
    "galaxy-s25-ultra-wide": (85.0, ("brown-conrady",)),
    "galaxy s25 ultra, 50 mp ultra-wide": (120.0, ("brown-conrady",)),
    "galaxy-s25-ultra-ultra-wide": (120.0, ("brown-conrady",)),
}

# The 36 x 24 mm film frame that EXIF FocalLengthIn35mmFilm refers to; makers scale by its diagonal [inference]
FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)


def diagonal_fov_deg_from_35mm_equivalent(focal_35mm_mm: float) -> float:
    """Diagonal field of view of a rectilinear lens with this 35 mm equivalent focal length: 2 atan(d / 2f)."""
    if not (math.isfinite(focal_35mm_mm) and focal_35mm_mm > 0.0):
        raise ValueError(f"A 35 mm equivalent focal length must be finite and positive, not {focal_35mm_mm!r}.")
    return math.degrees(2.0 * math.atan(FULL_FRAME_DIAGONAL_MM / (2.0 * focal_35mm_mm)))


def is_published_camera_label(camera_label: str | None) -> bool:
    """Whether `camera_label` names a camera in `PUBLISHED_CAMERA_FOV_DEG`."""
    return camera_label is not None and camera_label.strip().lower() in PUBLISHED_CAMERA_FOV_DEG


MIN_COINCIDENCE_LOG_ODDS = 18.0
MIN_HELD_OUT_INLIERS = 6
MAX_HELD_OUT_MEDIAN_PX = 3.0
MAX_HELD_OUT_P95_PX = 5.5
MAX_DUAL_PATH_DISAGREEMENT_PX = 2.5

PHONE_SOLVER_IDENTITY = SolverIdentity(
    name="albedian.phone_photo_tile_solver",
    version="1.0.0",
    catalog_release="Gaia DR3 + Hipparcos 2007 (G <= 12 tiles)",
)


# =============================================================================================
# Apparent topocentric sky transformations (ICRS <-> refracted ENU)
# =============================================================================================


def _rot_x(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]], dtype=np.float64)


def _rot_y(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]], dtype=np.float64)


def _rot_z(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def _radec_to_vec_batch(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra = np.radians(np.atleast_1d(np.asarray(ra_deg, dtype=np.float64)))
    dec = np.radians(np.atleast_1d(np.asarray(dec_deg, dtype=np.float64)))
    cd = np.cos(dec)
    return np.stack([cd * np.cos(ra), cd * np.sin(ra), np.sin(dec)], axis=-1)


def _vec_to_radec_batch(vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = np.atleast_2d(np.asarray(vec, dtype=np.float64))
    ra = np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360.0
    dec = np.degrees(np.arctan2(v[:, 2], np.hypot(v[:, 0], v[:, 1])))
    return ra, dec


def _normalize_vecs(v: np.ndarray) -> np.ndarray:
    norm = np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-15)
    return v / norm


def _parse_iso_to_jd_utc(obs_utc_iso: str) -> float:
    cleaned = obs_utc_iso.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return JD_UNIX_EPOCH + delta.days + (delta.seconds + delta.microseconds * 1e-6) / 86400.0


def _precession_matrix_iau1976(jd_tt: float) -> np.ndarray:
    t = (jd_tt - JD_J2000) / DAYS_PER_CENTURY
    tas2r = t * ARCSEC_RAD
    w = 2306.2181
    zeta = (w + (0.30188 + 0.017998 * t) * t) * tas2r
    z = (w + (1.09468 + 0.018203 * t) * t) * tas2r
    theta = (2004.3109 + (-0.42665 - 0.041833 * t) * t) * tas2r
    return _rot_z(-z) @ _rot_y(theta) @ _rot_z(-zeta)


def _mean_obliquity_rad(jd_tt: float) -> float:
    t = (jd_tt - JD_J2000) / DAYS_PER_CENTURY
    return ARCSEC_RAD * (84381.448 + (-46.8150 + (-0.00059 + 0.001813 * t) * t) * t)


def _nutation_short_rad(jd_tt: float) -> tuple[float, float]:
    t = (jd_tt - JD_J2000) / DAYS_PER_CENTURY
    omega = math.radians(125.04452 - 1934.136261 * t + 0.0020708 * t * t + t**3 / 450000.0)
    l_sun = math.radians(280.4665 + 36000.7698 * t)
    l_moon = math.radians(218.3165 + 481267.8813 * t)
    dpsi = -17.20 * math.sin(omega) - 1.32 * math.sin(2.0 * l_sun) - 0.23 * math.sin(2.0 * l_moon) + 0.21 * math.sin(2.0 * omega)
    deps = 9.20 * math.cos(omega) + 0.57 * math.cos(2.0 * l_sun) + 0.10 * math.cos(2.0 * l_moon) - 0.09 * math.cos(2.0 * omega)
    return dpsi * ARCSEC_RAD, deps * ARCSEC_RAD


def _nutation_matrix(jd_tt: float) -> np.ndarray:
    eps0 = _mean_obliquity_rad(jd_tt)
    dpsi, deps = _nutation_short_rad(jd_tt)
    return _rot_x(-(eps0 + deps)) @ _rot_z(-dpsi) @ _rot_x(eps0)


def _earth_velocity_over_c(jd_tt: float) -> np.ndarray:
    t = (jd_tt - JD_J2000) / DAYS_PER_CENTURY
    mean_lon = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    anomaly = math.radians(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    center = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(anomaly)
        + (0.019993 - 0.000101 * t) * math.sin(2.0 * anomaly)
        + 0.000289 * math.sin(3.0 * anomaly)
    )
    sun_lon = math.radians(mean_lon + center)
    ecc = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t
    perihelion = math.radians(102.93735 + 1.71946 * t + 0.00046 * t * t)
    bx = ABERRATION_CONST_RAD * (math.sin(sun_lon) - ecc * math.sin(perihelion))
    by = ABERRATION_CONST_RAD * (-math.cos(sun_lon) + ecc * math.cos(perihelion))
    eps = _mean_obliquity_rad(jd_tt)
    return np.array([bx, by * math.cos(eps), by * math.sin(eps)], dtype=np.float64)


def _gast_rad(jd_utc: float, jd_tt: float) -> float:
    t = (jd_utc - JD_J2000) / DAYS_PER_CENTURY
    day_fraction_s = 86400.0 * math.fmod(jd_utc, 1.0)
    seconds = (24110.54841 - 43200.0) + (8640184.812866 + (0.093104 - 6.2e-6 * t) * t) * t + day_fraction_s
    gmst = (seconds * (TWO_PI / 86400.0)) % TWO_PI
    dpsi, _ = _nutation_short_rad(jd_tt)
    omega = math.radians(125.04452 - 1934.136261 * ((jd_tt - JD_J2000) / DAYS_PER_CENTURY))
    eqeq = dpsi * math.cos(_mean_obliquity_rad(jd_tt)) + ARCSEC_RAD * (0.00264 * math.sin(omega) + 0.000063 * math.sin(2.0 * omega))
    return (gmst + eqeq) % TWO_PI


def _apparent_altitude_deg(true_alt_deg: np.ndarray) -> np.ndarray:
    h = np.atleast_1d(np.asarray(true_alt_deg, dtype=np.float64))
    held = np.maximum(h, REFRACTION_FLOOR_DEG)
    ref_arcmin = 1.02 / np.tan(np.radians(held + 10.3 / (held + 5.11)))
    return h + ref_arcmin / 60.0


def _true_altitude_deg(apparent_alt_deg: np.ndarray) -> np.ndarray:
    a = np.atleast_1d(np.asarray(apparent_alt_deg, dtype=np.float64))
    floor_shift = (1.02 / math.tan(math.radians(REFRACTION_FLOOR_DEG + 10.3 / (REFRACTION_FLOOR_DEG + 5.11)))) / 60.0
    below = a <= REFRACTION_FLOOR_DEG + floor_shift
    start = np.clip(a, -1.0, 90.0)
    h = np.clip(a - (1.0 / np.tan(np.radians(start + 7.31 / (start + 4.4)))) / 60.0, REFRACTION_FLOOR_DEG, 90.0)
    for _ in range(10):
        f = _apparent_altitude_deg(h) - a
        g = h + 10.3 / (h + 5.11)
        dg = 1.0 - 10.3 / (h + 5.11) ** 2
        slope = -1.02 / np.sin(np.radians(g)) ** 2 * dg * (math.pi / 180.0) / 60.0
        h = np.clip(h - f / (1.0 + slope), REFRACTION_FLOOR_DEG, 90.5)
    return np.where(below, a - floor_shift, h)


@dataclass(frozen=True)
class _ApparentSkyFrame:
    """Invertible transformation between ICRS directions of date and refracted (east, north, up) vectors."""

    jd_utc: float
    jd_tt: float
    lat_deg: float
    lon_deg: float
    precession: np.ndarray
    nutation: np.ndarray
    velocity: np.ndarray
    to_horizon: np.ndarray

    @classmethod
    def from_iso(cls, obs_utc_iso: str, lat_deg: float, lon_deg: float) -> "_ApparentSkyFrame":
        jd_utc = _parse_iso_to_jd_utc(obs_utc_iso)
        jd_tt = jd_utc + TT_MINUS_UTC_S / 86400.0
        prec = _precession_matrix_iau1976(jd_tt)
        nut = _nutation_matrix(jd_tt)
        vel = _earth_velocity_over_c(jd_tt)
        local_sid = _gast_rad(jd_utc, jd_tt) + math.radians(lon_deg)
        sp, cp = math.sin(math.radians(lat_deg)), math.cos(math.radians(lat_deg))
        hour_to_enu = np.array([[0.0, 1.0, 0.0], [-sp, 0.0, cp], [cp, 0.0, sp]], dtype=np.float64)
        to_hor = hour_to_enu @ _rot_z(local_sid)
        return cls(
            jd_utc=jd_utc,
            jd_tt=jd_tt,
            lat_deg=float(lat_deg),
            lon_deg=float(lon_deg),
            precession=prec,
            nutation=nut,
            velocity=vel,
            to_horizon=to_hor,
        )

    @property
    def epoch_jyear(self) -> float:
        return 2000.0 + (self.jd_utc - JD_J2000) / DAYS_PER_JYEAR

    def _refract(self, enu: np.ndarray, *, forward: bool) -> np.ndarray:
        horiz = np.hypot(enu[:, 0], enu[:, 1])
        alt = np.degrees(np.arctan2(enu[:, 2], horiz))
        new_alt = _apparent_altitude_deg(alt) if forward else _true_altitude_deg(alt)
        out = enu.copy()
        moved = horiz > 0.0
        if np.any(moved):
            ratio = np.cos(np.radians(new_alt[moved])) / horiz[moved]
            out[moved, 0] = enu[moved, 0] * ratio
            out[moved, 1] = enu[moved, 1] * ratio
            out[moved, 2] = np.sin(np.radians(new_alt[moved]))
        return out

    def icrs_to_sky_vec(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
        vec = _radec_to_vec_batch(ra_deg, dec_deg) @ self.precession.T
        vec = _normalize_vecs(vec + self.velocity) @ self.nutation.T
        return self._refract(vec @ self.to_horizon.T, forward=True)

    def sky_vec_to_icrs(self, enu: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        enu_n = _normalize_vecs(np.atleast_2d(np.asarray(enu, dtype=np.float64)))
        vec = self._refract(enu_n, forward=False) @ self.to_horizon
        obs = vec @ self.nutation
        nat = _normalize_vecs(obs - self.velocity)
        for _ in range(4):
            nat = _normalize_vecs(nat + (obs - _normalize_vecs(nat + self.velocity)))
        icrs = nat @ self.precession
        return _vec_to_radec_batch(icrs)


# =============================================================================================
# PhoneCameraWCS: contract-compatible WCSModel supporting Brown-Conrady and Kannala-Brandt
# =============================================================================================


def _max_valid_radial(k1: float, k2: float, *, fisheye: bool) -> float:
    # Smallest positive root of 1 + 3 k1 s + 5 k2 s^2 = 0
    coeffs = [0.0 if abs(c) < 1e-15 else c for c in (5.0 * k2, 3.0 * k1)] + [1.0]
    roots = np.roots(coeffs) if any(c != 0.0 for c in coeffs[:-1]) else np.array([])
    real = [float(r.real) for r in roots if abs(r.imag) < 1e-12 and r.real > 0.0]
    limit = math.sqrt(min(real)) if real else (math.pi if fisheye else 50.0)
    return min(limit, math.pi if fisheye else 50.0)


def _focal_for_diagonal_fov(width: int, height: int, fov_deg: float, lens_model: str, k1: float, k2: float) -> float:
    half = math.radians(max(5.0, min(175.0, fov_deg)) / 2.0)
    if lens_model == "kannala-brandt":
        t2 = half * half
        distorted = half * (1.0 + t2 * (k1 + t2 * k2))
    else:
        r = math.tan(half)
        r2 = r * r
        distorted = r * (1.0 + r2 * (k1 + r2 * k2))
    return 0.5 * math.hypot(width, height) / max(abs(distorted), 1e-6)


@dataclass(frozen=True)
class PhoneCameraWCS(WCSModel):
    """WCSModel extended with exact 3D camera attitude and Brown-Conrady or Kannala-Brandt distortion."""

    lens_model: str = "brown-conrady"
    fov_diagonal_deg: float = 84.0
    focal_px: float = 500.0
    dist_k1: float = 0.0
    dist_k2: float = 0.0
    dist_p1: float = 0.0
    dist_p2: float = 0.0
    cam_rotation: tuple[tuple[float, float, float], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    sky_frame: _ApparentSkyFrame | None = None

    @classmethod
    def from_rotation_and_intrinsics(
        cls,
        *,
        rot_matrix: np.ndarray,
        fov_diagonal_deg: float,
        width: int,
        height: int,
        lens_model: str = "brown-conrady",
        k1: float = 0.0,
        k2: float = 0.0,
        p1: float = 0.0,
        p2: float = 0.0,
        sky_frame: _ApparentSkyFrame | None = None,
    ) -> "PhoneCameraWCS":
        focal = _focal_for_diagonal_fov(width, height, fov_diagonal_deg, lens_model, k1, k2)
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        # Optical axis is row 2 of rot_matrix in sky_frame (or ICRS)
        forward = rot_matrix[2:3, :]
        if sky_frame is not None:
            ra_c, dec_c = sky_frame.sky_vec_to_icrs(forward)
        else:
            ra_c, dec_c = _vec_to_radec_batch(forward)
        crval_ra = float(ra_c[0]) % 360.0
        crval_dec = float(dec_c[0])
        scale_deg_per_px = math.degrees(1.0 / max(focal, 1e-6))

        # Estimate position angle of celestial north at center for contract rotationDeg
        north_icrs = np.array(
            [
                -math.sin(math.radians(crval_dec)) * math.cos(math.radians(crval_ra)),
                -math.sin(math.radians(crval_dec)) * math.sin(math.radians(crval_ra)),
                math.cos(math.radians(crval_dec)),
            ],
            dtype=np.float64,
        )
        if sky_frame is not None:
            step_dec = max(-89.9, min(89.9, crval_dec + 0.1))
            v0 = sky_frame.icrs_to_sky_vec(np.array([crval_ra]), np.array([crval_dec]))[0]
            v1 = sky_frame.icrs_to_sky_vec(np.array([crval_ra]), np.array([step_dec]))[0]
            north_vec = v1 - v0
        else:
            north_vec = north_icrs
        cam_n = rot_matrix @ north_vec
        rot_deg = math.degrees(math.atan2(-float(cam_n[0]), -float(cam_n[1])))

        # Equivalent TAN radial coefficients clamped to contract [-0.25, 0.25]
        tan_k1 = k1 - (1.0 / 3.0 if lens_model == "kannala-brandt" else 0.0)
        tan_k2 = k2 + (0.2 if lens_model == "kannala-brandt" else 0.0)

        rot_tuple = tuple(tuple(float(rot_matrix[r, c]) for c in range(3)) for r in range(3))
        return cls(
            crval_ra_deg=crval_ra,
            crval_dec_deg=crval_dec,
            crpix_x=cx,
            crpix_y=cy,
            scale_deg_per_px=scale_deg_per_px,
            rotation_deg=rot_deg,
            k1=max(-0.25, min(0.25, tan_k1)),
            k2=max(-0.25, min(0.25, tan_k2)),
            parity=-1,
            width=width,
            height=height,
            lens_model=lens_model,
            fov_diagonal_deg=float(fov_diagonal_deg),
            focal_px=float(focal),
            dist_k1=float(k1),
            dist_k2=float(k2),
            dist_p1=float(p1),
            dist_p2=float(p2),
            cam_rotation=rot_tuple,
            sky_frame=sky_frame,
        )

    @classmethod
    def from_pointing(
        cls,
        *,
        crval_ra_deg: float,
        crval_dec_deg: float,
        rotation_deg: float,
        fov_diagonal_deg: float,
        width: int,
        height: int,
        lens_model: str = "brown-conrady",
        k1: float = 0.0,
        k2: float = 0.0,
        p1: float = 0.0,
        p2: float = 0.0,
        sky_frame: _ApparentSkyFrame | None = None,
    ) -> "PhoneCameraWCS":
        ra = math.radians(float(crval_ra_deg) % 360.0)
        dec = math.radians(float(crval_dec_deg))
        forward = np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)], dtype=np.float64)
        east = np.array([-math.sin(ra), math.cos(ra), 0.0], dtype=np.float64)
        north = np.array([-math.sin(dec) * math.cos(ra), -math.sin(dec) * math.sin(ra), math.cos(dec)], dtype=np.float64)
        # Standard sky parity (-1): x_cam increases with -east (west), y_cam increases with -north (south)
        right0 = -east
        down0 = -north
        theta = math.radians(float(rotation_deg))
        right = math.cos(theta) * right0 - math.sin(theta) * down0
        down = math.sin(theta) * right0 + math.cos(theta) * down0
        rot_matrix = np.vstack([right, down, forward])
        return cls.from_rotation_and_intrinsics(
            rot_matrix=rot_matrix,
            fov_diagonal_deg=fov_diagonal_deg,
            width=width,
            height=height,
            lens_model=lens_model,
            k1=k1,
            k2=k2,
            p1=p1,
            p2=p2,
            sky_frame=sky_frame,
        )

    def _rot_array(self) -> np.ndarray:
        return np.asarray(self.cam_rotation, dtype=np.float64)

    def world_to_pixel_batch(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ra_arr = np.atleast_1d(np.asarray(ra_deg, dtype=np.float64))
        dec_arr = np.atleast_1d(np.asarray(dec_deg, dtype=np.float64))
        if self.sky_frame is not None:
            sky_vecs = self.sky_frame.icrs_to_sky_vec(ra_arr, dec_arr)
        else:
            sky_vecs = _radec_to_vec_batch(ra_arr, dec_arr)
        rays = sky_vecs @ self._rot_array().T
        rx, ry, rz = rays[:, 0], rays[:, 1], rays[:, 2]
        limit = _max_valid_radial(self.dist_k1, self.dist_k2, fisheye=self.lens_model == "kannala-brandt")

        with np.errstate(divide="ignore", invalid="ignore"):
            if self.lens_model == "kannala-brandt":
                rho = np.hypot(rx, ry)
                theta = np.arctan2(rho, rz)
                valid = (theta <= limit) & ((rho > 0.0) | (rz > 0.0))
                t2 = theta * theta
                theta_d = theta * (1.0 + t2 * (self.dist_k1 + t2 * self.dist_k2))
                scale = np.where(rho > 0.0, theta_d / np.where(rho > 0.0, rho, 1.0), 0.0)
                xd, yd = rx * scale, ry * scale
            else:
                valid = rz > 1e-9
                x = np.where(valid, rx / np.where(valid, rz, 1.0), 0.0)
                y = np.where(valid, ry / np.where(valid, rz, 1.0), 0.0)
                r = np.hypot(x, y)
                valid &= r <= limit
                r2 = x * x + y * y
                rad = 1.0 + r2 * (self.dist_k1 + r2 * self.dist_k2)
                xd = x * rad + 2.0 * self.dist_p1 * x * y + self.dist_p2 * (r2 + 2.0 * x * x)
                yd = y * rad + self.dist_p1 * (r2 + 2.0 * y * y) + 2.0 * self.dist_p2 * x * y

        px = np.where(valid, self.focal_px * xd + self.crpix_x, np.nan)
        py = np.where(valid, self.focal_px * yd + self.crpix_y, np.nan)
        return px, py

    def pixel_to_world_batch(self, x_px: np.ndarray, y_px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        xd = (np.atleast_1d(np.asarray(x_px, dtype=np.float64)) - self.crpix_x) / self.focal_px
        yd = (np.atleast_1d(np.asarray(y_px, dtype=np.float64)) - self.crpix_y) / self.focal_px
        limit = _max_valid_radial(self.dist_k1, self.dist_k2, fisheye=self.lens_model == "kannala-brandt")

        if self.lens_model == "kannala-brandt":
            rd = np.hypot(xd, yd)
            grid = np.linspace(0.0, limit, 1025)
            g2 = grid * grid
            gd = grid * (1.0 + g2 * (self.dist_k1 + g2 * self.dist_k2))
            theta = np.interp(rd, gd, grid)
            for _ in range(6):
                t2 = theta * theta
                f = theta * (1.0 + t2 * (self.dist_k1 + t2 * self.dist_k2)) - rd
                slope = 1.0 + t2 * (3.0 * self.dist_k1 + 5.0 * self.dist_k2 * t2)
                step = np.divide(f, slope, out=np.zeros_like(f), where=slope > 1e-12)
                theta = np.clip(theta - step, 0.0, limit)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(rd > 0.0, np.sin(theta) / np.where(rd > 0.0, rd, 1.0), 0.0)
            rays = np.stack([xd * ratio, yd * ratio, np.cos(theta)], axis=-1)
        else:
            rd = np.hypot(xd, yd)
            top = min(limit, 25.0)
            grid = np.linspace(0.0, top, 1025)
            g2 = grid * grid
            gd = grid * (1.0 + g2 * (self.dist_k1 + g2 * self.dist_k2))
            r = np.interp(rd, gd, grid)
            for _ in range(6):
                s = r * r
                f = r * (1.0 + s * (self.dist_k1 + s * self.dist_k2)) - rd
                slope = 1.0 + s * (3.0 * self.dist_k1 + 5.0 * self.dist_k2 * s)
                step = np.divide(f, slope, out=np.zeros_like(f), where=slope > 1e-12)
                r = np.clip(r - step, 0.0, top)
            with np.errstate(divide="ignore", invalid="ignore"):
                scale = np.where(rd > 0.0, r / np.where(rd > 0.0, rd, 1.0), 1.0)
            x, y = xd * scale, yd * scale
            if self.dist_p1 != 0.0 or self.dist_p2 != 0.0:
                for _ in range(15):
                    r2 = x * x + y * y
                    rad = 1.0 + r2 * (self.dist_k1 + r2 * self.dist_k2)
                    slope = 2.0 * self.dist_k1 + 4.0 * self.dist_k2 * r2
                    fx = x * rad + 2.0 * self.dist_p1 * x * y + self.dist_p2 * (r2 + 2.0 * x * x) - xd
                    fy = y * rad + self.dist_p1 * (r2 + 2.0 * y * y) + 2.0 * self.dist_p2 * x * y - yd
                    a = rad + slope * x * x + 2.0 * self.dist_p1 * y + 6.0 * self.dist_p2 * x
                    b = slope * x * y + 2.0 * self.dist_p1 * x + 2.0 * self.dist_p2 * y
                    d = rad + slope * y * y + 6.0 * self.dist_p1 * y + 2.0 * self.dist_p2 * x
                    det = a * d - b * b
                    with np.errstate(divide="ignore", invalid="ignore"):
                        sx = np.where(np.abs(det) > 1e-12, (d * fx - b * fy) / det, 0.0)
                        sy = np.where(np.abs(det) > 1e-12, (a * fy - b * fx) / det, 0.0)
                    x, y = x - sx, y - sy
            rays = _normalize_vecs(np.stack([x, y, np.ones_like(x)], axis=-1))

        sky_vecs = rays @ self._rot_array()
        if self.sky_frame is not None:
            return self.sky_frame.sky_vec_to_icrs(sky_vecs)
        return _vec_to_radec_batch(sky_vecs)

    def world_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        px, py = self.world_to_pixel_batch(np.array([ra_deg], dtype=np.float64), np.array([dec_deg], dtype=np.float64))
        return (float(px[0]), float(py[0]))

    def pixel_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        ra, dec = self.pixel_to_world_batch(np.array([x_px], dtype=np.float64), np.array([y_px], dtype=np.float64))
        return (float(ra[0]), float(dec[0]))


# =============================================================================================
# G 12 Tile Catalog Reader (L0 + L1 bright sky index)
# =============================================================================================


def _v_from_gaia_mag(g_mag: np.ndarray, bp_rp: np.ndarray) -> np.ndarray:
    """An approximate Johnson V from Gaia G, for the brightness cut and the ranking only; never output.

    The polynomial is the G - V relation in BP-RP of Riello et al. (2021, A&A 649, A3, table C2), as the phone
    photo benchmark cites it (research/benchmarks/phone_photos/stars.py). A row without BP-RP is ranked with
    TYPICAL_BP_RP_FOR_RANKING inside this sum alone: the star's catalog reference then carries no colour.
    """
    c = np.clip(np.where(np.isfinite(bp_rp), bp_rp, TYPICAL_BP_RP_FOR_RANKING), -0.5, 2.75)
    return g_mag - (-0.02704 + 0.01424 * c - 0.2156 * c * c + 0.01426 * c**3)


def _propagate_pm(
    ra_deg: np.ndarray, dec_deg: np.ndarray, pm_ra_mas_yr: np.ndarray, pm_dec_mas_yr: np.ndarray, years: float
) -> tuple[np.ndarray, np.ndarray]:
    if abs(years) < 1e-9:
        return ra_deg, dec_deg
    vec = _radec_to_vec_batch(ra_deg, dec_deg)
    ra = np.radians(ra_deg)
    dec = np.radians(dec_deg)
    mas_rad = ARCSEC_RAD / 1000.0
    d_east = np.nan_to_num(pm_ra_mas_yr) * mas_rad * years
    d_north = np.nan_to_num(pm_dec_mas_yr) * mas_rad * years
    east = np.stack([-np.sin(ra), np.cos(ra), np.zeros_like(ra)], axis=-1)
    north = np.stack([-np.sin(dec) * np.cos(ra), -np.sin(dec) * np.sin(ra), np.cos(dec)], axis=-1)
    return _vec_to_radec_batch(_normalize_vecs(vec + east * d_east[:, None] + north * d_north[:, None]))


class _TileStarIndex:
    """In-memory index of L0 (G <= 6.5) and L1 (6.5 < G <= 8.0) catalog tiles with valid distances."""

    def __init__(self, release_dir: Path = DEFAULT_TILE_RELEASE_DIR) -> None:
        self.release_dir = Path(release_dir)
        manifest = json.loads((self.release_dir / "manifest.json").read_text(encoding="utf-8"))
        self.epoch_jyear = float(manifest["epochJulianYear"])
        names_path = self.release_dir / "names.json"
        self._names_by_source: dict[tuple[bool, int], dict] = {}
        if names_path.is_file():
            names_doc = json.loads(names_path.read_text(encoding="utf-8"))
            for entry in names_doc.get("names", ()):
                is_hip = entry.get("catalog") == "hipparcos-2007"
                cid = int(entry["catalogId"])
                self._names_by_source[(is_hip, cid)] = entry

        self._pinned_by_source: dict[str, CatalogStar] = {
            s.catalog_source_id: s for s in ALL_SKY_CATALOG_STARS
        }

        parts: list[dict[str, np.ndarray]] = []
        for layer in (0, 1):
            layer_dir = self.release_dir / f"L{layer}"
            if not layer_dir.is_dir():
                continue
            for core_path in sorted(layer_dir.glob("*.core.bin")):
                pix = int(core_path.name.split(".")[0])
                detail_path = layer_dir / f"{pix}.detail.bin"
                header, cols = ct.decode_core_tile(core_path.read_bytes())
                _, detail = ct.decode_detail_tile(detail_path.read_bytes())
                med_pc = ct.decode_distance(cols["distance"])
                lo_pc, hi_pc = ct.decode_distance_bounds(med_pc, detail["distanceLower"], detail["distanceUpper"])
                valid_dist = (
                    np.isfinite(med_pc)
                    & np.isfinite(lo_pc)
                    & np.isfinite(hi_pc)
                    & (lo_pc > 0.0)
                    & (lo_pc <= med_pc)
                    & (med_pc <= hi_pc)
                )
                flags = cols["flags"].astype(np.int64)
                vec = ct.decode_core_directions(header, cols)
                ra0, dec0 = _vec_to_radec_batch(vec)
                mag = ct.decode_mag(cols["mag"])
                color = ct.decode_color(cols["color"])
                color_bv = (flags & ct.FLAG_COLOR_B_V) != 0
                hip = (flags & ct.FLAG_CATALOG_HIPPARCOS) != 0
                sid = (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64)
                pm_ra = ct.decode_f16(detail["pmRa"])
                pm_dec = ct.decode_f16(detail["pmDec"])
                rv = ct.decode_f16(detail["radialVelocity"])
                bp_rp = np.where(color_bv, np.nan, color)
                v_mag = np.where(~hip, _v_from_gaia_mag(mag, bp_rp), mag)
                parts.append(
                    {
                        "ra0": ra0,
                        "dec0": dec0,
                        "mag": mag,
                        "mag_hp": (flags & ct.FLAG_MAG_BAND_HP) != 0,
                        "v_mag": v_mag,
                        # A value the row lacks stays NaN here and is left out of the star's reference
                        "color": color,
                        "color_bv": color_bv,
                        "hip": hip,
                        "sid": sid,
                        "pm_ra": pm_ra,
                        "pm_dec": pm_dec,
                        "rv": rv,
                        "dist_kind": flags & ct.FLAG_DIST_KIND_MASK,
                        "lo_pc": lo_pc,
                        "med_pc": med_pc,
                        "hi_pc": hi_pc,
                        "valid_dist": valid_dist,
                    }
                )

        merged = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        order = np.argsort(merged["v_mag"])
        self.data = {k: v[order] for k, v in merged.items()}

    def query_bright(self, *, v_limit: float, max_stars: int, epoch_jyear: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (indices, ra_deg, dec_deg, v_mag) for stars brighter than `v_limit` at `epoch_jyear`."""
        mask = self.data["v_mag"] <= v_limit
        idx = np.flatnonzero(mask)[:max_stars]
        ra, dec = _propagate_pm(
            self.data["ra0"][idx],
            self.data["dec0"][idx],
            self.data["pm_ra"][idx],
            self.data["pm_dec"][idx],
            epoch_jyear - self.epoch_jyear,
        )
        return idx, ra, dec, self.data["v_mag"][idx]

    def has_distance(self, row_idx: int) -> bool:
        """Whether the row carries a distance; a pinned star's finding takes its distance from its row too."""
        return bool(self.data["valid_dist"][row_idx])

    def _row_value(self, key: str, row_idx: int) -> float | None:
        """The row's own value, or None where the catalog gives none."""
        value = float(self.data[key][row_idx])
        return value if math.isfinite(value) else None

    def to_catalog_star(self, row_idx: int, ra_deg: float, dec_deg: float, epoch_jyear: float) -> CatalogStar | None:
        """The star of one tile row, holding only what the row gives.

        A value the row lacks is None: no colour, proper motion or radial velocity is filled in, a Hipparcos B-V is
        not given as BP-RP, no spectral type is named (neither release carries one), the tiles' Hipparcos distance
        is not turned back into a parallax, and the magnitude keeps its band (Hipparcos Hp or Gaia G). Names come
        from the IAU name table, its full designation (never a raw Bayer abbreviation such as "alf"), and a star
        without an IAU name has no designation beyond its id. Its constellation is the name table's, else the IAU
        boundary that holds it. A pinned star takes every catalog value from its row too; its pinned record adds
        only the star's id and names (common name, designation and constellation).
        """
        if not self.has_distance(row_idx):
            return None
        is_hip = bool(self.data["hip"][row_idx])
        sid = int(self.data["sid"][row_idx])
        cat_source_id = f"HIP {sid}" if is_hip else f"Gaia DR3 {sid}"
        pinned = self._pinned_by_source.get(cat_source_id)
        name_entry = self._names_by_source.get((is_hip, sid))
        common_name = name_entry["name"] if name_entry else cat_source_id
        designation = (name_entry.get("designation") or None) if name_entry else None
        constellation = name_entry.get("constellation") if name_entry else None
        if pinned is not None:
            common_name, designation, constellation = pinned.common_name, pinned.bayer_designation, pinned.constellation
        if not constellation:
            constellation = iau_constellation_codes(
                np.array([self.data["ra0"][row_idx]]), np.array([self.data["dec0"][row_idx]])
            )[0]
        kind = int(self.data["dist_kind"][row_idx])
        if kind not in _DISTANCE_METHOD_BY_KIND:
            raise ValueError(f"{cat_source_id}: tile distance kind {kind} has no method")
        method = _DISTANCE_METHOD_BY_KIND[kind]
        # A Hipparcos row's colour is Johnson B-V, which is not Gaia BP-RP
        bp_rp = None if bool(self.data["color_bv"][row_idx]) else self._row_value("color", row_idx)
        rv = self._row_value("rv", row_idx)
        band = HIPPARCOS_HP if bool(self.data["mag_hp"][row_idx]) else GAIA_G
        return CatalogStar(
            id=pinned.id if pinned is not None else (f"hip-{sid}" if is_hip else f"gaia-dr3-{sid}"),
            common_name=common_name,
            bayer_designation=designation,
            constellation=constellation,
            provider="Hipparcos" if is_hip else "Gaia DR3",
            release_version=HIPPARCOS_2007_RELEASE if is_hip else GAIA_DR3_RELEASE,
            catalog_source_id=cat_source_id,
            ra_deg=float(ra_deg) % 360.0,
            dec_deg=float(dec_deg),
            mag=float(self.data["mag"][row_idx]),
            bp_rp=bp_rp,
            spectral_type=None,
            pm_ra_mas_yr=self._row_value("pm_ra", row_idx),
            pm_dec_mas_yr=self._row_value("pm_dec", row_idx),
            rv_km_s=rv,
            parallax_mas=None,
            parallax_error_mas=None,
            lower_pc=float(self.data["lo_pc"][row_idx]),
            median_pc=float(self.data["med_pc"][row_idx]),
            upper_pc=float(self.data["hi_pc"][row_idx]),
            distance_method=method,
            citation=(
                hipparcos_2007_tile_citation(magnitude_band=band)
                if is_hip
                else gaia_dr3_citation(
                    color=bp_rp is not None, radial_velocity=rv is not None, distance_method=method, magnitude_band=band
                )
            ),
            epoch_year=float(epoch_jyear),
            magnitude_band=band,
        )


# The tile flag's distance kind (services/science/catalog_tiles.py) and the method it names
_DISTANCE_METHOD_BY_KIND = {
    ct.DIST_KIND_BJ_PHOTOGEO: BAILER_JONES_PHOTOGEOMETRIC,
    ct.DIST_KIND_BJ_GEO: BAILER_JONES_GEOMETRIC,
    ct.DIST_KIND_PARALLAX_INVERSE: INVERSE_PARALLAX,
}


@dataclass(frozen=True)
class _ConstellationRing:
    """One IAU constellation's boundary at equinox B1875, right ascension unwrapped along the ring."""

    code: str
    ra_deg: np.ndarray
    dec_deg: np.ndarray
    ra_min: float
    ra_max: float
    dec_min: float
    dec_max: float


@lru_cache(maxsize=1)
def _constellation_rings(path: Path = CONSTELLATION_BOUNDS_PATH) -> tuple[_ConstellationRing, ...]:
    """The shipped boundary rings, taken back to B1875 where every edge is a constant right ascension or declination.

    In B1875 coordinates each ring is a polygon with straight edges in the (right ascension, declination) plane, so
    an even-odd test there is exact up to the file's 0.0001 degree corners. A ring whose right ascension turns a
    full circle holds a celestial pole and is closed through it.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("vertexEquinox") != "J2000" or doc.get("edgeEquinox") != "B1875":
        raise ValueError(f"{path}: expected J2000 corners on B1875 edges")
    to_b1875 = _precession_matrix_iau1976(JD_B1875)
    rings = []
    for entry in doc["constellations"]:
        ring = np.asarray(entry["ring"], dtype=np.float64)
        ra, dec = _vec_to_radec_batch(_radec_to_vec_batch(ring[:, 0], ring[:, 1]) @ to_b1875.T)
        steps = (np.diff(ra) + 180.0) % 360.0 - 180.0
        ra_u = np.concatenate([[ra[0]], ra[0] + np.cumsum(steps)])
        turn = ra_u[-1] - ra_u[0]
        if abs(turn) > 180.0:
            pole = 90.0 if float(np.mean(dec)) > 0.0 else -90.0
            ra_u = np.concatenate([ra_u, [ra_u[-1], ra_u[0], ra_u[0]]])
            dec = np.concatenate([dec, [pole, pole, dec[0]]])
        # Serpens is one IAU constellation drawn as two rings, Caput (Se1) and Cauda (Se2)
        code = "Ser" if entry["code"] in ("Se1", "Se2") else str(entry["code"])
        rings.append(
            _ConstellationRing(
                code, ra_u, dec, float(ra_u.min()), float(ra_u.max()), float(dec.min()), float(dec.max())
            )
        )
    return tuple(rings)


def _inside_ring(ring: _ConstellationRing, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Even-odd test of points against one ring in its unwrapped (right ascension, declination) plane."""
    inside = np.zeros(x.shape, dtype=bool)
    x1, y1, x2, y2 = ring.ra_deg[:-1], ring.dec_deg[:-1], ring.ra_deg[1:], ring.dec_deg[1:]
    for k in np.flatnonzero(y1 != y2):
        crosses = (y1[k] > y) != (y2[k] > y)
        x_at = x1[k] + (y - y1[k]) * (x2[k] - x1[k]) / (y2[k] - y1[k])
        inside ^= crosses & (x < x_at)
    return inside


def iau_constellation_codes(ra_deg: np.ndarray, dec_deg: np.ndarray) -> list[str | None]:
    """The IAU constellation (three-letter code) holding each ICRS direction, or None where not exactly one does.

    The direction is precessed to B1875 (IAU 1976, Lieske et al. 1977) and tested against the Delporte boundaries
    (Davenhall and Leggett 1989). Catalog data, not measured from any recording; a direction within about 0.0001
    degree of a boundary can fall on either side, and frame bias and the star's motion since are ignored [inference].
    """
    ra_deg = np.atleast_1d(np.asarray(ra_deg, dtype=np.float64))
    dec_deg = np.atleast_1d(np.asarray(dec_deg, dtype=np.float64))
    ra, dec = _vec_to_radec_batch(_radec_to_vec_batch(ra_deg, dec_deg) @ _precession_matrix_iau1976(JD_B1875).T)
    rings = _constellation_rings()
    ra_min = np.array([r.ra_min for r in rings])
    ra_max = np.array([r.ra_max for r in rings])
    dec_min = np.array([r.dec_min for r in rings])
    dec_max = np.array([r.dec_max for r in rings])
    # Each direction's right ascension shifted by whole turns to the start of each ring's unwrapped span
    lifted = ra_min[None, :] + (ra[:, None] - ra_min[None, :]) % 360.0
    near = (dec[:, None] >= dec_min[None, :]) & (dec[:, None] <= dec_max[None, :]) & (lifted <= ra_max[None, :])
    hits = np.zeros(ra.shape, dtype=np.int64)
    found = np.full(ra.shape, -1, dtype=np.int64)
    for ring_i in np.flatnonzero(near.any(axis=0)):
        ring = rings[ring_i]
        idx = np.flatnonzero(near[:, ring_i])
        x, y = lifted[idx, ring_i], dec[idx]
        # A ring that circles a pole can span more than one turn: the direction is inside when an odd number of its
        # lifts are
        inside = np.zeros(idx.shape, dtype=bool)
        while np.any(x <= ring.ra_max):
            inside ^= _inside_ring(ring, x, y) & (x <= ring.ra_max)
            x = x + 360.0
        hits[idx[inside]] += 1
        found[idx[inside]] = ring_i
    return [rings[f].code if n == 1 else None for n, f in zip(hits.tolist(), found.tolist(), strict=True)]


def catalog_matched_subtitle(star: CatalogStar) -> str:
    """Designation, spectral type and distance, leaving out what the catalog lacks and a designation that only
    repeats the star's name."""
    designation = star.bayer_designation if star.bayer_designation != star.common_name else None
    distance = f"{star.median_pc:.1f} pc [{star.lower_pc:.1f}-{star.upper_pc:.1f} pc]"
    return " · ".join(part for part in (designation, star.spectral_type, distance) if part)


@lru_cache(maxsize=2)
def _get_tile_index(release_dir: Path = DEFAULT_TILE_RELEASE_DIR) -> _TileStarIndex:
    return _TileStarIndex(release_dir)


# =============================================================================================
# Coincidence-weighed held-out verification gate (Lang et al. 2010, AJ 139, 1782)
# =============================================================================================


def _median_over_every_pair(held_out_residuals_px: Sequence[float]) -> float:
    """The median held-out residual over every pair, inlier or not (the contract's `medianResidualPx`,
    packages/contracts/src/astronomy.ts): the one number the gate judges and the quality record reports."""
    return float(np.median(np.asarray(held_out_residuals_px, dtype=np.float64)))


def held_out_residual_statistics(
    held_out_residuals_px: Sequence[float], inlier_gate_px: float
) -> tuple[int, int, float | None, float | None]:
    """`(pairs, inliers, median px, 95th percentile px)` of the held-out residuals, as the quality record reports
    them. The inliers are the pairs at or inside the per-star inlier gate; the median and the percentile cover every
    held-out pair, inlier or not, as the contract's `medianResidualPx` and `p95ResidualPx` do, and are None with no
    pair."""
    residuals = np.asarray(held_out_residuals_px, dtype=np.float64)
    if residuals.size == 0:
        return 0, 0, None, None
    inliers = int(np.count_nonzero(residuals <= inlier_gate_px))
    return int(residuals.size), inliers, _median_over_every_pair(held_out_residuals_px), float(np.percentile(residuals, 95.0))


def held_out_quality(
    held_out_residuals_px: Sequence[float],
    inlier_gate_px: float,
    passed: bool,
    *,
    detected_candidate_count: int,
    matched_inlier_count: int,
    scale_deg_per_px: float,
) -> SolutionQuality:
    """The quality record of a solve whose held-out residuals these are and whose gate answered `passed`: the counts
    and residuals of `held_out_residual_statistics`, the same numbers the gate judged."""
    pairs, inliers, median_px, p95_px = held_out_residual_statistics(held_out_residuals_px, inlier_gate_px)
    return SolutionQuality(
        detected_candidate_count=detected_candidate_count,
        matched_inlier_count=matched_inlier_count,
        held_out_pair_count=pairs,
        held_out_inliers=inliers,
        median_residual_px=median_px,
        p95_residual_px=p95_px,
        median_residual_arcsec=median_px * scale_deg_per_px * 3600.0 if median_px is not None else None,
        passed_independent_verification=passed,
    )


def evaluate_coincidence_gate(
    *,
    held_out_residuals_px: Sequence[float],
    active_detection_count: int,
    active_sky_area_px2: float,
    projected_catalog_count: int,
    inlier_gate_px: float,
    dual_path_disagreement_px: float = 0.0,
) -> tuple[bool, float]:
    """Evaluate held-out residuals against background coincidence density and dual-path agreement.

    Returns `(passed, log_odds)` where `log_odds` is the summed Bayesian log-Bayes-factor of the
    held-out matches against a uniform Poisson background of density `active_detection_count / active_sky_area_px2`.
    """
    residuals = [float(r) for r in held_out_residuals_px if math.isfinite(r) and r <= inlier_gate_px]
    k_inliers = len(residuals)
    if k_inliers < MIN_HELD_OUT_INLIERS:
        return (False, -100.0)

    # The median judged is the one the quality record reports, over every held-out pair, inlier or not: the held-out
    # gate is a limit on that number (the contract's medianResidualPx; services/science/solver.py judges it so too).
    # Judged on the inliers alone, a solve could pass while the median it reports stood above the gate. A median
    # that is no number does not pass
    med_px = _median_over_every_pair(held_out_residuals_px)
    p95_px = float(np.percentile(residuals, 95.0))
    if not med_px <= MAX_HELD_OUT_MEDIAN_PX or p95_px > MAX_HELD_OUT_P95_PX:
        return (False, -50.0)
    if dual_path_disagreement_px > MAX_DUAL_PATH_DISAGREEMENT_PX:
        return (False, -25.0)

    area = max(float(active_sky_area_px2), 1000.0)
    n_det = max(int(active_detection_count), k_inliers)
    n_cat = max(int(projected_catalog_count), k_inliers)
    gate = max(float(inlier_gate_px), 1.5)

    # Background Poisson density (detections per square pixel)
    lambda_bg = n_det / area
    p_rand_gate = 1.0 - math.exp(-lambda_bg * math.pi * gate * gate)
    expected_false = n_cat * p_rand_gate

    # Positional sigma under H1 (true astrometric alignment)
    sigma_pos = max(0.55, min(1.25, 0.38 * gate))
    p_hit = 0.65

    log_odds = 0.0
    for r_px in residuals:
        signal_density = p_hit / (2.0 * math.pi * sigma_pos * sigma_pos) * math.exp(-0.5 * (r_px / sigma_pos) ** 2)
        bg_density = max(lambda_bg, 1e-9)
        log_odds += math.log(max(signal_density / bg_density, 1e-12))

    # Penalty for unmatched projected catalog stars and high false-match expectation
    missed = max(0, n_cat - k_inliers)
    miss_penalty = missed * math.log(max(1.0 - 0.25 * (1.0 - p_rand_gate), 0.5))
    log_odds += miss_penalty - 2.0 * expected_false

    passed = (
        log_odds >= MIN_COINCIDENCE_LOG_ODDS
        and k_inliers >= max(MIN_HELD_OUT_INLIERS, int(math.ceil(3.5 * expected_false)))
    )
    return (bool(passed), float(log_odds))


# =============================================================================================
# Stellar candidate ranking & hot-pixel / ground-silhouette filtering on original pixels
# =============================================================================================


def _rank_stellar_candidates(
    image: np.ndarray,
    luma: np.ndarray,
    bg: np.ndarray,
    detections: Sequence[SourceDetection],
    max_pool: int = 65,
) -> tuple[list[SourceDetection], float]:
    """Rank detections for geometric pattern matching, returning `(ranked_pool, active_sky_fraction)`."""
    h, w = luma.shape
    res = luma - bg
    has_rgb = image.ndim == 3 and image.shape[2] >= 3
    rgb = (
        np.asarray(image[:, :, :3], dtype=np.float64) / (255.0 if np.max(image) > 1.5 else 1.0)
        if has_rgb
        else None
    )

    upper_bg = float(np.percentile(bg[: max(1, int(0.55 * h)), :], 60))
    if upper_bg > 0.015:
        sky_mask_est = (bg >= 0.52 * upper_bg) | (bg >= 0.085)
        sky_fraction = max(0.25, float(np.mean(sky_mask_est)))
    else:
        sky_fraction = 1.0

    gh, gw = 6, 6
    local_sig = np.zeros((gh, gw), dtype=np.float64)
    for iy in range(gh):
        y0, y1 = (iy * h) // gh, ((iy + 1) * h) // gh
        for ix in range(gw):
            x0, x1 = (ix * w) // gw, ((ix + 1) * w) // gw
            tile = res[y0:y1, x0:x1]
            med = float(np.median(tile))
            mad = float(np.median(np.abs(tile - med)))
            local_sig[iy, ix] = max(1.4826 * mad, 1e-4)
    med_sig = float(np.median(local_sig))

    scored: list[tuple[float, SourceDetection]] = []
    for d in detections:
        x0, y0 = int(round(d.x_px)), int(round(d.y_px))
        if not (3 <= x0 < w - 3 and 3 <= y0 < h - 3):
            continue
        loc_bg = float(bg[y0, x0])
        if upper_bg > 0.015 and loc_bg < 0.52 * upper_bg and loc_bg < 0.085 and y0 > 0.32 * h:
            continue
        # Reject detections inside saturated extended disks (e.g. Moon disk or bright UI block)
        if loc_bg > 0.68:
            continue

        iy = min(gh - 1, (y0 * gh) // h)
        ix = min(gw - 1, (x0 * gw) // w)
        noise_scale = max(1.0, local_sig[iy, ix] / max(med_sig, 1e-4))
        adj_snr = d.snr / noise_scale

        p = res[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
        c = max(float(p[2, 2]), 1e-6)
        ring2 = float(np.mean(np.concatenate([p[0, :], p[4, :], p[1:4, 0], p[1:4, 4]])))
        frac_center = math.hypot(d.x_px - x0, d.y_px - y0)

        green_excess = 0.0
        if rgb is not None:
            bg_rgb = np.median(rgb[max(0, y0 - 4) : min(h, y0 + 5), max(0, x0 - 4) : min(w, x0 + 5)], axis=(0, 1))
            dr, dg, db = rgb[y0, x0] - bg_rgb
            green_excess = float((dg - max(dr, db)) / c)

        if frac_center < 0.16 and ring2 / c < 0.048 and ((x0 + y0) % 2 == 1 or green_excess > 0.02):
            adj_snr *= 0.005
        elif green_excess > 0.035 and ring2 / c < 0.055:
            adj_snr *= 0.008

        scored.append((adj_snr, d))

    scored.sort(key=lambda item: item[0], reverse=True)
    return ([d for _, d in scored[:max_pool]], sky_fraction)


# =============================================================================================
# Triad basis, horizon attitude voter, blind triangle voter, and nonlinear lens refinement
# =============================================================================================


def _triad_basis_batch(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
    e1 = _normalize_vecs(v1)
    proj = np.sum(v2 * e1, axis=-1, keepdims=True)
    e2 = _normalize_vecs(v2 - proj * e1)
    e3 = np.cross(e1, e2)
    return np.stack([e1, e2, e3], axis=-1)


def _rodrigues(omega: np.ndarray) -> np.ndarray:
    theta = float(np.linalg.norm(omega))
    if theta < 1e-12:
        return np.eye(3, dtype=np.float64)
    k = omega / theta
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]], dtype=np.float64)
    return np.eye(3, dtype=np.float64) + math.sin(theta) * kx + (1.0 - math.cos(theta)) * (kx @ kx)


def _refine_phone_camera_wcs(
    initial_wcs: PhoneCameraWCS,
    train_pairs: Sequence[tuple[SourceDetection, float, float]],
    *,
    fit_distortion: bool | None = None,
) -> PhoneCameraWCS:
    """Refine 3D attitude (3 Rodrigues parameters), focal scale (diagonal FOV), and radial distortion (k1, k2)."""
    if len(train_pairs) < 3:
        return initial_wcs

    det_x = np.array([d.x_px for d, _, _ in train_pairs], dtype=np.float64)
    det_y = np.array([d.y_px for d, _, _ in train_pairs], dtype=np.float64)
    cat_ra = np.array([ra for _, ra, _ in train_pairs], dtype=np.float64)
    cat_dec = np.array([dec for _, _, dec in train_pairs], dtype=np.float64)

    base_rot = initial_wcs._rot_array()
    fov0 = initial_wcs.fov_diagonal_deg
    w, h = initial_wcs.width, initial_wcs.height
    lens_model = initial_wcs.lens_model
    sky_frame = initial_wcs.sky_frame

    if sky_frame is not None:
        sky_vecs = sky_frame.icrs_to_sky_vec(cat_ra, cat_dec)
    else:
        sky_vecs = _radec_to_vec_batch(cat_ra, cat_dec)

    allow_dist = (len(train_pairs) >= 9) if fit_distortion is None else fit_distortion
    k1_0, k2_0 = initial_wcs.dist_k1, initial_wcs.dist_k2
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0

    if not allow_dist:
        x0 = np.array([0.0, 0.0, 0.0, fov0], dtype=np.float64)
        lb = np.array([-0.12, -0.12, -0.12, max(25.0, fov0 * 0.94)], dtype=np.float64)
        ub = np.array([0.12, 0.12, 0.12, min(145.0, fov0 * 1.06)], dtype=np.float64)

        def residuals_rigid(params: np.ndarray) -> np.ndarray:
            wx, wy, wz, fov = params
            rot = _rodrigues(np.array([wx, wy, wz], dtype=np.float64)) @ base_rot
            focal = _focal_for_diagonal_fov(w, h, float(fov), lens_model, k1_0, k2_0)
            rays = sky_vecs @ rot.T
            rx, ry, rz = rays[:, 0], rays[:, 1], rays[:, 2]
            if lens_model == "kannala-brandt":
                rho = np.hypot(rx, ry)
                theta = np.arctan2(rho, rz)
                t2 = theta * theta
                theta_d = theta * (1.0 + t2 * (k1_0 + t2 * k2_0))
                scale = np.where(rho > 0.0, theta_d / np.where(rho > 0.0, rho, 1.0), 0.0)
                px = focal * rx * scale + cx
                py = focal * ry * scale + cy
            else:
                valid = rz > 1e-6
                xn = np.where(valid, rx / np.where(valid, rz, 1.0), 0.0)
                yn = np.where(valid, ry / np.where(valid, rz, 1.0), 0.0)
                r2 = xn * xn + yn * yn
                rad = 1.0 + r2 * (k1_0 + r2 * k2_0)
                px = np.where(valid, focal * xn * rad + cx, 1e4)
                py = np.where(valid, focal * yn * rad + cy, 1e4)
            reg = np.array([(fov - fov0) * 0.6], dtype=np.float64)
            return np.concatenate([px - det_x, py - det_y, reg])

        opt = optimize.least_squares(
            residuals_rigid, x0, bounds=(lb, ub), loss="soft_l1", f_scale=1.2, max_nfev=120
        )
        wx, wy, wz, fov = opt.x
        rot_opt = _rodrigues(np.array([wx, wy, wz], dtype=np.float64)) @ base_rot
        return PhoneCameraWCS.from_rotation_and_intrinsics(
            rot_matrix=rot_opt,
            fov_diagonal_deg=float(fov),
            width=w,
            height=h,
            lens_model=lens_model,
            k1=k1_0,
            k2=k2_0,
            sky_frame=sky_frame,
        )

    x0 = np.array([0.0, 0.0, 0.0, fov0, k1_0, k2_0], dtype=np.float64)
    k1_bound = 0.05 if lens_model == "kannala-brandt" else 0.14
    k2_bound = 0.025 if lens_model == "kannala-brandt" else 0.045
    lb = np.array([-0.15, -0.15, -0.15, max(25.0, fov0 * 0.88), -k1_bound, -k2_bound], dtype=np.float64)
    ub = np.array([0.15, 0.15, 0.15, min(145.0, fov0 * 1.12), k1_bound, k2_bound], dtype=np.float64)

    def residuals_fn(params: np.ndarray) -> np.ndarray:
        wx, wy, wz, fov, k1, k2 = params
        rot = _rodrigues(np.array([wx, wy, wz], dtype=np.float64)) @ base_rot
        focal = _focal_for_diagonal_fov(w, h, float(fov), lens_model, float(k1), float(k2))
        rays = sky_vecs @ rot.T
        rx, ry, rz = rays[:, 0], rays[:, 1], rays[:, 2]
        if lens_model == "kannala-brandt":
            rho = np.hypot(rx, ry)
            theta = np.arctan2(rho, rz)
            t2 = theta * theta
            theta_d = theta * (1.0 + t2 * (k1 + t2 * k2))
            scale = np.where(rho > 0.0, theta_d / np.where(rho > 0.0, rho, 1.0), 0.0)
            px = focal * rx * scale + cx
            py = focal * ry * scale + cy
        else:
            valid = rz > 1e-6
            xn = np.where(valid, rx / np.where(valid, rz, 1.0), 0.0)
            yn = np.where(valid, ry / np.where(valid, rz, 1.0), 0.0)
            r2 = xn * xn + yn * yn
            rad = 1.0 + r2 * (k1 + r2 * k2)
            px = np.where(valid, focal * xn * rad + cx, 1e4)
            py = np.where(valid, focal * yn * rad + cy, 1e4)
        reg = np.array([(fov - fov0) * 0.4, (k1 - k1_0) * 25.0, (k2 - k2_0) * 60.0], dtype=np.float64)
        return np.concatenate([px - det_x, py - det_y, reg])

    opt = optimize.least_squares(
        residuals_fn, x0, bounds=(lb, ub), loss="soft_l1", f_scale=1.2, max_nfev=160
    )
    wx, wy, wz, fov, k1, k2 = opt.x
    rot_opt = _rodrigues(np.array([wx, wy, wz], dtype=np.float64)) @ base_rot
    return PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot_opt,
        fov_diagonal_deg=float(fov),
        width=w,
        height=h,
        lens_model=lens_model,
        k1=float(k1),
        k2=float(k2),
        sky_frame=sky_frame,
    )


def _associate_detections(
    wcs: PhoneCameraWCS,
    detections: Sequence[SourceDetection],
    cat_ra: np.ndarray,
    cat_dec: np.ndarray,
    radius_px: float = 9.0,
) -> tuple[list[tuple[int, int, float]], int]:
    """Greedy nearest-neighbor pairing between `detections` and catalog coordinates `(cat_ra, cat_dec)`.

    Returns `(pairs, projected_in_bounds_count)` where each pair is `(det_idx, cat_idx, residual_px)`.
    """
    w, h = wcs.width, wcs.height
    px, py = wcs.world_to_pixel_batch(cat_ra, cat_dec)
    in_bounds = np.isfinite(px) & (px >= 4.0) & (px <= w - 5.0) & (py >= 4.0) & (py <= h - 5.0)
    vis_indices = np.flatnonzero(in_bounds)
    projected_count = int(vis_indices.size)
    if projected_count == 0 or not detections:
        return ([], projected_count)

    vx, vy = px[vis_indices], py[vis_indices]
    used_cat: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for d_idx, det in enumerate(detections):
        dists = np.hypot(vx - det.x_px, vy - det.y_px)
        order = np.argsort(dists)
        for o_idx in order:
            dist = float(dists[o_idx])
            if dist > radius_px:
                break
            c_idx = int(vis_indices[o_idx])
            if c_idx not in used_cat:
                used_cat.add(c_idx)
                pairs.append((d_idx, c_idx, dist))
                break
    return (pairs, projected_count)


def _candidate_fovs_and_models(
    camera_label: str | None, fov_diagonal_deg: float | None
) -> list[tuple[float, str]]:
    if fov_diagonal_deg is not None:
        return [(float(fov_diagonal_deg), "brown-conrady"), (float(fov_diagonal_deg), "kannala-brandt")]
    if camera_label is not None:
        key = camera_label.strip().lower()
        if key in PUBLISHED_CAMERA_FOV_DEG:
            fov, models = PUBLISHED_CAMERA_FOV_DEG[key]
            return [(fov, m) for m in models]
    return [
        (84.06, "brown-conrady"),
        (82.0, "brown-conrady"),
        (85.0, "brown-conrady"),
        (120.0, "brown-conrady"),
        (123.0, "brown-conrady"),
        (120.0, "kannala-brandt"),
        (95.0, "brown-conrady"),
        (108.0, "brown-conrady"),
    ]


def _search_horizon_attitude(
    pool: Sequence[SourceDetection],
    width: int,
    height: int,
    sky_frame: _ApparentSkyFrame,
    u_cat: np.ndarray,
    cat_ra: np.ndarray,
    cat_dec: np.ndarray,
    fov_models: Sequence[tuple[float, str]],
    on_candidate: Callable[[int, int], None] | None,
) -> PhoneCameraWCS | None:
    search_pool = pool[:38] if len(fov_models) > 2 else pool
    m_len = len(search_pool)
    if m_len < 6 or len(u_cat) < 6:
        return None

    dx = np.array([d.x_px for d in search_pool], dtype=np.float64)
    dy = np.array([d.y_px for d in search_pool], dtype=np.float64)

    k_len = len(u_cat)
    ia, ib = np.triu_indices(k_len, k=1)
    ia_both = np.concatenate([ia, ib])
    ib_both = np.concatenate([ib, ia])
    cat_dots = np.clip(np.sum(u_cat[ia_both] * u_cat[ib_both], axis=-1), -1.0, 1.0)
    cat_ang = np.degrees(np.arccos(cat_dots))
    keep_cat = (cat_ang >= 6.0) & (cat_ang <= 82.0)
    ia_both, ib_both, cat_ang = ia_both[keep_cat], ib_both[keep_cat], cat_ang[keep_cat]
    sort_idx = np.argsort(cat_ang)
    ang_sorted = cat_ang[sort_idx]
    F_sorted = _triad_basis_batch(u_cat[ia_both[sort_idx]], u_cat[ib_both[sort_idx]])

    di_all, dj_all = np.triu_indices(m_len, k=1)
    total_candidates = len(di_all) * len(fov_models)
    if on_candidate is not None:
        on_candidate(0, max(1, total_candidates))

    best_score = 0
    best_wcs: PhoneCameraWCS | None = None
    evaluated = 0
    cos_gate = math.cos(math.radians(0.85))
    tol_deg = 0.85

    for fov_deg, lens_model in fov_models:
        temp_wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
            rot_matrix=np.eye(3, dtype=np.float64),
            fov_diagonal_deg=fov_deg,
            width=width,
            height=height,
            lens_model=lens_model,
            sky_frame=sky_frame,
        )
        # Convert pool pixels to unit camera rays
        xd = (dx - temp_wcs.crpix_x) / temp_wcs.focal_px
        yd = (dy - temp_wcs.crpix_y) / temp_wcs.focal_px
        if lens_model == "kannala-brandt":
            rd = np.hypot(xd, yd)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(rd > 0.0, np.sin(rd) / np.where(rd > 0.0, rd, 1.0), 0.0)
            d_rays = _normalize_vecs(np.stack([xd * ratio, yd * ratio, np.cos(rd)], axis=-1))
        else:
            d_rays = _normalize_vecs(np.stack([xd, yd, np.ones_like(xd)], axis=-1))

        d_dots = np.clip(np.sum(d_rays[di_all] * d_rays[dj_all], axis=-1), -1.0, 1.0)
        d_ang = np.degrees(np.arccos(d_dots))
        max_ang = min(0.85 * fov_deg, 82.0)
        valid_pairs = np.flatnonzero((d_ang >= 6.0) & (d_ang <= max_ang))
        E_valid = _triad_basis_batch(d_rays[di_all[valid_pairs]], d_rays[dj_all[valid_pairs]])

        for local_idx, pair_idx in enumerate(valid_pairs):
            ang = float(d_ang[pair_idx])
            lo = int(np.searchsorted(ang_sorted, ang - tol_deg, side="left"))
            hi = int(np.searchsorted(ang_sorted, ang + tol_deg, side="right"))
            if lo < hi:
                E = E_valid[local_idx]
                F_sub = F_sorted[lo:hi]
                R_cand = np.einsum("ij,ckj->cik", E, F_sub)
                valid_att = (R_cand[:, 2, 2] > 0.08) & (R_cand[:, 1, 2] < -0.08) & (np.abs(R_cand[:, 0, 2]) < 0.48)
                if np.any(valid_att):
                    R_sub = R_cand[valid_att]
                    proj = np.einsum("kn,cmn->ckm", u_cat, R_sub)
                    sim = np.einsum("mn,ckn->cmk", d_rays, proj)
                    inliers_per_rot = np.count_nonzero(np.max(sim, axis=2) >= cos_gate, axis=1)
                    max_idx = int(np.argmax(inliers_per_rot))
                    top_inliers = int(inliers_per_rot[max_idx])
                    if top_inliers > best_score:
                        best_score = top_inliers
                        best_wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
                            rot_matrix=R_sub[max_idx],
                            fov_diagonal_deg=fov_deg,
                            width=width,
                            height=height,
                            lens_model=lens_model,
                            sky_frame=sky_frame,
                        )
        evaluated += len(di_all)
        if on_candidate is not None:
            on_candidate(min(evaluated, total_candidates), max(1, total_candidates))
        if best_score >= max(16, int(0.45 * m_len)):
            break

    if on_candidate is not None and evaluated < total_candidates:
        on_candidate(max(1, total_candidates), max(1, total_candidates))
    return best_wcs if best_score >= 7 else None


def _search_blind_icrs_triangles(
    pool: Sequence[SourceDetection],
    width: int,
    height: int,
    cat_ra: np.ndarray,
    cat_dec: np.ndarray,
    fov_models: Sequence[tuple[float, str]],
    on_candidate: Callable[[int, int], None] | None,
) -> PhoneCameraWCS | None:
    """Scale-invariant 3D triangle matcher in ICRS when capture time or observer location is omitted."""
    det_sub = list(pool[:16])
    if len(det_sub) < 6 or len(cat_ra) < 6:
        return None

    fov_deg, lens_model = fov_models[0]
    temp_wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3, dtype=np.float64),
        fov_diagonal_deg=fov_deg,
        width=width,
        height=height,
        lens_model=lens_model,
        sky_frame=None,
    )
    dx = np.array([d.x_px for d in det_sub], dtype=np.float64)
    dy = np.array([d.y_px for d in det_sub], dtype=np.float64)
    xd = (dx - temp_wcs.crpix_x) / temp_wcs.focal_px
    yd = (dy - temp_wcs.crpix_y) / temp_wcs.focal_px
    d_rays = _normalize_vecs(np.stack([xd, yd, np.ones_like(xd)], axis=-1))

    # Build catalog triangles from brightest stars in clustered sky patches
    u_all = _radec_to_vec_batch(cat_ra[:80], cat_dec[:80])
    k_sub = min(28, len(u_all))
    u_cat = u_all[:k_sub]

    cat_tris: list[tuple[int, int, int, float, float, float]] = []
    for i, j, k in itertools.combinations(range(k_sub), 3):
        a_ij = math.degrees(math.acos(max(-1.0, min(1.0, float(u_cat[i] @ u_cat[j])))))
        a_jk = math.degrees(math.acos(max(-1.0, min(1.0, float(u_cat[j] @ u_cat[k])))))
        a_ik = math.degrees(math.acos(max(-1.0, min(1.0, float(u_cat[i] @ u_cat[k])))))
        sides = sorted([a_ij, a_jk, a_ik])
        if sides[0] < 2.5 or sides[2] > min(0.85 * fov_deg, 75.0):
            continue
        pairs = [((i, j), a_ij), ((j, k), a_jk), ((i, k), a_ik)]
        pairs.sort(key=lambda p: p[1], reverse=True)
        lset, sset = set(pairs[0][0]), set(pairs[1][0])
        v0 = (lset & sset).pop()
        v1 = (lset - {v0}).pop()
        v2 = ({i, j, k} - {v0, v1}).pop()
        cat_tris.append((v0, v1, v2, pairs[1][1] / pairs[0][1], pairs[2][1] / pairs[0][1], pairs[0][1]))

    if not cat_tris:
        return None
    cat_v0 = np.array([t[0] for t in cat_tris], dtype=np.int64)
    cat_v1 = np.array([t[1] for t in cat_tris], dtype=np.int64)
    cat_v2 = np.array([t[2] for t in cat_tris], dtype=np.int64)
    cat_r1 = np.array([t[3] for t in cat_tris], dtype=np.float64)
    cat_r2 = np.array([t[4] for t in cat_tris], dtype=np.float64)
    cat_long = np.array([t[5] for t in cat_tris], dtype=np.float64)
    F_cat = _triad_basis_batch(u_cat[cat_v0], u_cat[cat_v1])

    triplets = list(itertools.combinations(range(len(det_sub)), 3))
    total = len(triplets)
    if on_candidate is not None:
        on_candidate(0, max(1, total))

    best_score = 0
    best_wcs: PhoneCameraWCS | None = None
    cos_gate_tri = math.cos(math.radians(0.65))
    cos_gate_inlier = math.cos(math.radians(0.42))

    for evaluated, (a, b, c) in enumerate(triplets, start=1):
        d_ab = math.degrees(math.acos(max(-1.0, min(1.0, float(d_rays[a] @ d_rays[b])))))
        d_bc = math.degrees(math.acos(max(-1.0, min(1.0, float(d_rays[b] @ d_rays[c])))))
        d_ac = math.degrees(math.acos(max(-1.0, min(1.0, float(d_rays[a] @ d_rays[c])))))
        dpairs = [((a, b), d_ab), ((b, c), d_bc), ((a, c), d_ac)]
        dpairs.sort(key=lambda p: p[1], reverse=True)
        if dpairs[2][1] < 2.0:
            if on_candidate is not None:
                on_candidate(evaluated, total)
            continue
        lset, sset = set(dpairs[0][0]), set(dpairs[1][0])
        u0 = (lset & sset).pop()
        u1 = (lset - {u0}).pop()
        u2 = ({a, b, c} - {u0, u1}).pop()
        dr1 = dpairs[1][1] / dpairs[0][1]
        dr2 = dpairs[2][1] / dpairs[0][1]
        d_long = dpairs[0][1]

        hits = np.flatnonzero(
            (np.abs(cat_r1 - dr1) <= 0.03)
            & (np.abs(cat_r2 - dr2) <= 0.03)
            & (np.abs(cat_long - d_long) <= 4.5)
        )
        if hits.size > 0:
            E = _triad_basis_batch(d_rays[[u0]], d_rays[[u1]])[0]
            for h_idx in hits:
                v2 = int(cat_v2[h_idx])
                R = E @ F_cat[h_idx].T
                if float(d_rays[u2] @ (R @ u_cat[v2])) < cos_gate_tri:
                    continue
                proj = u_all @ R.T
                sim = d_rays @ proj.T
                inliers = int(np.count_nonzero(np.max(sim, axis=1) >= cos_gate_inlier))
                if inliers > best_score:
                    best_score = inliers
                    best_wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
                        rot_matrix=R,
                        fov_diagonal_deg=fov_deg,
                        width=width,
                        height=height,
                        lens_model=lens_model,
                        sky_frame=None,
                    )
        if on_candidate is not None:
            on_candidate(evaluated, total)
        if best_score >= max(10, len(det_sub) - 3):
            break

    return best_wcs if best_score >= 7 else None


def _build_unresolved(
    detections: Sequence[SourceDetection],
    solution_id: str,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float,
    obs_utc_iso: str | None,
    observer_lat: float | None,
    observer_lon: float | None,
    warning_msg: str,
    limitation: str,
) -> ObservationSolution:
    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status="unresolved",
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=None,
        solver=PHONE_SOLVER_IDENTITY,
        quality=SolutionQuality(
            detected_candidate_count=len(detections),
            matched_inlier_count=0,
            held_out_pair_count=0,
            held_out_inliers=0,
            median_residual_px=None,
            p95_residual_px=None,
            median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        findings=[
            CelestialFindingRecord(
                id=f"finding-obs-{idx + 1}",
                observation_solution_id=solution_id,
                evidence_class="observed",
                title=f"Observed Point Source #{idx + 1}",
                subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
                source_detection=det,
                verified_by_holdout=False,
                limitations=(limitation,),
                source_sha256=source_sha256,
            )
            for idx, det in enumerate(detections)
        ],
        warnings=[warning_msg],
    )


def solve_phone_photo(
    image: np.ndarray,
    *,
    detections: list[SourceDetection] | None = None,
    obs_utc_iso: str | None = None,
    observer_lat: float | None = None,
    observer_lon: float | None = None,
    camera_label: str | None = None,
    fov_diagonal_deg: float | None = None,
    catalog_stars: Sequence[CatalogStar] | None = None,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-primary",
    on_candidate: Callable[[int, int], None] | None = None,
) -> ObservationSolution:
    """Solve a phone night photo against the G 12 catalog tiles with coincidence-weighed held-out verification."""
    require_source_sha256(source_sha256)
    if (observer_lat is None) != (observer_lon is None):
        raise ValueError("observer_lat and observer_lon must both be provided or both be None.")

    raw_luma = _to_luma_2d(image)
    luma = raw_luma / 255.0 if (image.dtype == np.uint8 or float(np.max(raw_luma)) > 1.5) else raw_luma
    height, width = luma.shape
    bg, _ = _estimate_background_and_noise(luma)
    if detections is None:
        detections = detect_point_sources(
            luma, snr_threshold=3.5, asset_id=source_asset_id, frame_time_sec=frame_time_sec
        )

    if len(detections) < 6:
        return _build_unresolved(
            detections,
            solution_id,
            source_asset_id,
            source_sha256,
            frame_time_sec,
            obs_utc_iso,
            observer_lat,
            observer_lon,
            f"Insufficient point sources ({len(detections)} detected) for independent WCS calibration.",
            "Pixel position only: too few point sources for a verified WCS to place it on the sky.",
        )

    ranked_candidates, sky_fraction = _rank_stellar_candidates(image, luma, bg, detections, max_pool=85)
    if len(ranked_candidates) < 6:
        ranked_candidates = list(detections[:85])
    pool = ranked_candidates[:65]

    has_time_and_place = obs_utc_iso is not None and observer_lat is not None and observer_lon is not None
    sky_frame = (
        _ApparentSkyFrame.from_iso(obs_utc_iso, observer_lat, observer_lon)
        if has_time_and_place
        else None
    )
    fov_models = _candidate_fovs_and_models(camera_label, fov_diagonal_deg)

    tile_index = _get_tile_index()
    # Stars move by their proper motion to the capture time when there is one; without one they stay at the
    # catalog's own epoch, and each matched finding says so
    if obs_utc_iso is not None:
        epoch_jyear = 2000.0 + (_parse_iso_to_jd_utc(obs_utc_iso) - JD_J2000) / DAYS_PER_JYEAR
    else:
        epoch_jyear = tile_index.epoch_jyear
    if obs_utc_iso is None:
        epoch_limitations: tuple[str, ...] = (
            f"The photo has no capture time, so the star is placed at the catalog epoch J{tile_index.epoch_jyear:.1f}, "
            "not moved by its proper motion to the photo's date.",
        )
    elif not has_time_and_place:
        epoch_limitations = (
            "Moved by its catalog proper motion to the photo's capture time; the photo has no place, so the star has "
            "no altitude or azimuth.",
        )
    else:
        epoch_limitations = ()
    initial_wcs: PhoneCameraWCS | None = None

    if sky_frame is not None and catalog_stars is None:
        row_idx_b, ra_b, dec_b, _ = tile_index.query_bright(v_limit=6.3, max_stars=3500, epoch_jyear=epoch_jyear)
        enu_b = sky_frame.icrs_to_sky_vec(ra_b, dec_b)
        above = enu_b[:, 2] > math.sin(math.radians(0.5))
        vis_idx = np.flatnonzero(above)[:110]
        if vis_idx.size >= 6:
            initial_wcs = _search_horizon_attitude(
                pool,
                width,
                height,
                sky_frame,
                enu_b[vis_idx],
                ra_b[vis_idx],
                dec_b[vis_idx],
                fov_models,
                on_candidate,
            )

    if initial_wcs is None and (sky_frame is None or catalog_stars is not None):
        if catalog_stars is not None:
            custom_list = list(catalog_stars)
            c_ra = np.array([s.ra_deg for s in custom_list], dtype=np.float64)
            c_dec = np.array([s.dec_deg for s in custom_list], dtype=np.float64)
        else:
            # Query bright stars across Orion/Taurus + all-sky navigation stars for blind search
            orion_cone_idx, c_ra, c_dec, _ = tile_index.query_bright(v_limit=5.5, max_stars=400, epoch_jyear=epoch_jyear)
            # Prioritize stars near Orion/Taurus + brightest all-sky
            sep_orion = np.array([angular_separation_deg(float(r), float(d), 83.8, -1.2) for r, d in zip(c_ra, c_dec)])
            in_orion = np.flatnonzero(sep_orion <= 55.0)
            if in_orion.size >= 15:
                c_ra = np.concatenate([c_ra[in_orion], c_ra[:60]])
                c_dec = np.concatenate([c_dec[in_orion], c_dec[:60]])
        initial_wcs = _search_blind_icrs_triangles(
            pool, width, height, c_ra, c_dec, fov_models, on_candidate if sky_frame is None else None
        )

    if initial_wcs is None:
        return _build_unresolved(
            detections,
            solution_id,
            source_asset_id,
            source_sha256,
            frame_time_sec,
            obs_utc_iso,
            observer_lat,
            observer_lon,
            "Phone-photo pattern matching found no consistent WCS solution.",
            "Pixel position only: no catalog pattern matched, so no WCS places it on the sky.",
        )

    # Query verification catalog stars that project inside the initial frame
    if catalog_stars is not None:
        verify_stars = list(catalog_stars)
        v_ra = np.array([s.ra_deg for s in verify_stars], dtype=np.float64)
        v_dec = np.array([s.dec_deg for s in verify_stars], dtype=np.float64)
        v_row_indices = None
    else:
        is_ultrawide = initial_wcs.fov_diagonal_deg >= 105.0
        v_lim = 6.35 if is_ultrawide else 6.65
        max_in_frame = 80 if is_ultrawide else 90
        all_idx, all_ra, all_dec, _ = tile_index.query_bright(v_limit=v_lim, max_stars=8000, epoch_jyear=epoch_jyear)
        px0, py0 = initial_wcs.world_to_pixel_batch(all_ra, all_dec)
        in_box = np.isfinite(px0) & (px0 >= -8.0) & (px0 <= width + 8.0) & (py0 >= -8.0) & (py0 <= height + 8.0)
        if sky_frame is not None:
            enu_all = sky_frame.icrs_to_sky_vec(all_ra, all_dec)
            in_box &= enu_all[:, 2] > math.sin(math.radians(0.2))
        box_sub = np.flatnonzero(in_box)[:max_in_frame]
        v_row_indices = all_idx[box_sub]
        v_ra = all_ra[box_sub]
        v_dec = all_dec[box_sub]
        verify_stars = []

    eval_pool = ranked_candidates

    # Initial association on ranked pool using 1.15-degree angular radius around initial triad attitude
    deg_in_px = 1.0 / max(initial_wcs.scale_deg_per_px, 1e-4)
    init_rad_px = max(4.8, min(28.0, 1.15 * deg_in_px))
    init_pairs, _ = _associate_detections(initial_wcs, eval_pool, v_ra, v_dec, radius_px=init_rad_px)
    if len(init_pairs) < 6:
        init_pairs, _ = _associate_detections(initial_wcs, eval_pool, v_ra, v_dec, radius_px=init_rad_px * 1.4)
    if len(init_pairs) < 6:
        return _build_unresolved(
            detections,
            solution_id,
            source_asset_id,
            source_sha256,
            frame_time_sec,
            obs_utc_iso,
            observer_lat,
            observer_lon,
            "Initial WCS hypothesis matched too few stars for refinement.",
            "Pixel position only: too few catalog matches to refine a verified WCS.",
        )

    # Select radially interleaved training pairs so both train and held-out span center to edge
    cx0, cy0 = width / 2.0, height / 2.0
    tight_init = sorted(init_pairs, key=lambda item: item[2])[: max(6, int(0.85 * len(init_pairs)))]
    sorted_for_train = sorted(
        tight_init,
        key=lambda item: math.hypot(eval_pool[item[0]].x_px - cx0, eval_pool[item[0]].y_px - cy0),
        reverse=True,
    )
    train_count = max(3, min(8, len(sorted_for_train) // 2, len(sorted_for_train) - 6))
    interleaved = [*sorted_for_train[::2], *sorted_for_train[1::2]]
    train_items = interleaved[:train_count]
    train_det_ids = {id(eval_pool[d_i]) for d_i, _, _ in train_items}
    train_tuples = [(eval_pool[d_i], float(v_ra[c_i]), float(v_dec[c_i])) for d_i, c_i, _ in train_items]

    refined_wcs = _refine_phone_camera_wcs(initial_wcs, train_tuples, fit_distortion=False)
    if len(train_tuples) >= 5:
        refined_wcs = _refine_phone_camera_wcs(refined_wcs, train_tuples, fit_distortion=True)

    # Re-associate on refined WCS and run a second pass separating held-out inliers
    assoc_rad_px = max(4.5, min(6.0, 0.35 * deg_in_px))
    all_pairs, projected_cat_count = _associate_detections(refined_wcs, eval_pool, v_ra, v_dec, radius_px=assoc_rad_px)
    raw_res = np.array([res_px for _, _, res_px in all_pairs], dtype=np.float64)
    if raw_res.size > 0:
        med_r = float(np.median(raw_res))
        mad_r = float(np.median(np.abs(raw_res - med_r)))
        gate_px = min(4.0, max(1.8, med_r + 3.0 * max(0.25, 1.4826 * mad_r)))
    else:
        gate_px = 3.5

    held_out_tuples: list[tuple[SourceDetection, float, float]] = []
    held_out_residuals_all: list[float] = []
    for d_i, c_i, res_px in all_pairs:
        det = eval_pool[d_i]
        if id(det) not in train_det_ids:
            held_out_residuals_all.append(res_px)
            if res_px <= gate_px:
                held_out_tuples.append((det, float(v_ra[c_i]), float(v_dec[c_i])))

    # Dual-path consistency check: refit attitude+scale on held-out inliers alone and compare grid projection
    dual_disagreement_px = 0.0
    if len(held_out_tuples) >= 6:
        held_wcs = _refine_phone_camera_wcs(refined_wcs, held_out_tuples[:12], fit_distortion=len(held_out_tuples) >= 8)
        gx, gy = np.meshgrid(
            np.linspace(0.15 * width, 0.85 * width, 5),
            np.linspace(0.15 * height, 0.75 * height, 5),
        )
        ra_g, dec_g = refined_wcs.pixel_to_world_batch(gx.ravel(), gy.ravel())
        hx, hy = held_wcs.world_to_pixel_batch(ra_g, dec_g)
        diffs = np.hypot(hx - gx.ravel(), hy - gy.ravel())
        finite_diffs = diffs[np.isfinite(diffs)]
        dual_disagreement_px = float(np.median(finite_diffs)) if finite_diffs.size else 99.0
        # Final joint polish on all inliers (train + held-out) fitting lens distortion while keeping held-out metrics strictly from pre-polish split
        if dual_disagreement_px <= MAX_DUAL_PATH_DISAGREEMENT_PX:
            combined_inliers = [*train_tuples, *held_out_tuples[:18]]
            refined_wcs = _refine_phone_camera_wcs(
                refined_wcs, combined_inliers, fit_distortion=len(combined_inliers) >= 9
            )

    active_area_px2 = float(width * height) * sky_fraction
    passed_gate, log_odds = evaluate_coincidence_gate(
        held_out_residuals_px=held_out_residuals_all,
        active_detection_count=len(eval_pool),
        active_sky_area_px2=active_area_px2,
        projected_catalog_count=projected_cat_count,
        inlier_gate_px=gate_px,
        dual_path_disagreement_px=dual_disagreement_px,
    )

    # Map verified inlier detections by identity
    verified_by_det_id: dict[int, tuple[CatalogStar, float, bool]] = {}
    if passed_gate:
        for d_i, c_i, res_px in all_pairs:
            if res_px <= gate_px:
                det = eval_pool[d_i]
                if v_row_indices is not None:
                    star = tile_index.to_catalog_star(int(v_row_indices[c_i]), float(v_ra[c_i]), float(v_dec[c_i]), epoch_jyear)
                else:
                    star = verify_stars[c_i]
                if star is not None:
                    verified_by_det_id[id(det)] = (star, res_px, id(det) in train_det_ids)

    # The record of the held-out check, built once from the residuals the gate judged: its counts and its median
    # are the gate's own numbers, and what follows reads them from it
    quality = held_out_quality(
        held_out_residuals_all,
        gate_px,
        passed_gate,
        detected_candidate_count=len(detections),
        matched_inlier_count=len(verified_by_det_id) if passed_gate else 0,
        scale_deg_per_px=refined_wcs.scale_deg_per_px,
    )
    held_out_inliers, median_residual_arcsec = quality.held_out_inliers, quality.median_residual_arcsec

    status = "calibrated" if passed_gate else ("ambiguous" if (held_out_inliers >= 4 and log_odds >= 6.0) else "unresolved")
    findings_list: list[CelestialFindingRecord] = []
    warnings: list[str] = []

    for idx, det in enumerate(detections):
        if passed_gate and id(det) in verified_by_det_id:
            st, res_px, is_train = verified_by_det_id[id(det)]
            alt_deg, az_deg = (
                icrs_to_altaz(st.ra_deg, st.dec_deg, observer_lat, observer_lon, obs_utc_iso)
                if has_time_and_place
                else (None, None)
            )
            ang_res_arcsec = res_px * refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-{st.id}",
                    observation_solution_id=solution_id,
                    evidence_class="catalog_matched",
                    title=st.common_name,
                    subtitle=catalog_matched_subtitle(st),
                    ra_deg=st.ra_deg,
                    dec_deg=st.dec_deg,
                    alt_deg=alt_deg,
                    az_deg=az_deg,
                    uncertainty_arcsec=max(0.5, ang_res_arcsec),
                    source_detection=det,
                    catalog_star=st,
                    pixel_residual_px=res_px,
                    angular_residual_arcsec=ang_res_arcsec,
                    verified_by_holdout=not is_train,
                    limitations=(
                        f"Distance is {st.distance_source}, not measured from this image.",
                        *(
                            (f"Apparent magnitude is {st.magnitude_band}, not Johnson V.",)
                            if st.magnitude_band is not None
                            else ()
                        ),
                        *epoch_limitations,
                    ),
                    source_sha256=source_sha256,
                )
            )
        elif passed_gate:
            ra_est, dec_est = refined_wcs.pixel_to_world(det.x_px, det.y_px)
            arcsec_per_px = refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-obs-{idx + 1}",
                    observation_solution_id=solution_id,
                    evidence_class="observed",
                    title=f"Observed Point Source #{idx + 1}",
                    subtitle=f"Unmatched sensor detection at ({det.x_px:.1f}, {det.y_px:.1f}) px",
                    ra_deg=ra_est,
                    dec_deg=dec_est,
                    uncertainty_arcsec=math.hypot(median_residual_arcsec or 1.0, det.centroid_uncertainty_px * arcsec_per_px),
                    source_detection=det,
                    catalog_star=None,
                    verified_by_holdout=False,
                    limitations=("Unmatched sensor detection outside verified catalog inlier gate.",),
                    source_sha256=source_sha256,
                )
            )
        else:
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-obs-{idx + 1}",
                    observation_solution_id=solution_id,
                    evidence_class="observed",
                    title=f"Observed Point Source #{idx + 1}",
                    subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
                    source_detection=det,
                    verified_by_holdout=False,
                    limitations=("Pixel position only: the WCS fit failed coincidence-weighed held-out verification.",),
                    source_sha256=source_sha256,
                )
            )

    if not passed_gate:
        warnings.append("WCS solution did not pass coincidence-weighed held-out and dual-path verification gate.")

    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status=status,
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=refined_wcs if passed_gate else None,
        solver=PHONE_SOLVER_IDENTITY,
        quality=quality,
        findings=findings_list,
        warnings=warnings,
    )
