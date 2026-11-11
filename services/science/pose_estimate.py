"""An approximate camera pose for a head-worn clip from the Moon, the camera profile's field of view and a level
head (layer 2 of spec predicted-sky-for-every-upload, REQ-SKY-3; design component 4).

No star is matched and none is held out: the pose rests on one bright body and stated assumptions, so it is never
`calibrated` and never makes a catalog match. One frame at a time:
1. The Moon test, by the contour at half the Moon's peak height above the sky: the frame's brightest point after a
   light smoothing, its peak at least `MIN_PEAK_LUMA` and at least `MIN_PEAK_SNR` times the noise above the sky; its
   region, the 8-connected pixels around it above the half-peak level, of an equivalent diameter between
   `MIN_DIAMETER_FACTOR` times the Moon's predicted diameter at the profile's widest field of view and
   `MAX_DIAMETER_FACTOR` times it at the narrowest; and every other region above that level under half its area.
   Anything else is "not confirmed as the Moon".
2. The Moon as seen from the place (topocentric): its ephemeris position lowered by its parallax shift.
3. For each field of view (the profile's limits and middle) and roll (`SWEPT_ROLLS_DEG`): a TAN WCS at that fixed
   scale (parity -1, no distortion, crpix at the frame centre) whose centre and rotation are fitted so the Moon lands
   on the blob's centroid and the horizon has that roll in the upright frame (the frame as displayed). A fit whose
   largest residual exceeds `MAX_FIT_RESIDUAL_PX` refuses the pose.
4. The nominal WCS is the middle field of view at roll 0; the pose estimate keeps all nine, the largest angle between
   the nominal frame centre and a swept one, and the Moon's predicted radius as the centroid's own uncertainty.
Refraction is left out for the Moon and the stars alike (the ephemeris and the catalog transform are airless), so it
cancels to first order. docs/science/PREDICTED_SKY.md describes the layer in plain words.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import math
from typing import Any

import numpy as np
from scipy import ndimage, optimize

from services.science.camera_profiles import CameraProfile
from services.science.catalog import altaz_to_icrs, angular_separation_deg, icrs_to_altaz
from services.science.detector import _effective_sigma
from services.science.ephemeris import (
    AU_KM,
    EARTH_EQUATORIAL_RADIUS_KM,
    compute_solar_system_ephemerides,
    moon_parallax_shift_arcsec,
)
from services.science.wcs_models import PoseEstimate, WCSModel

POSE_METHOD = "moon_camera_profile_level_head"
# The Moon is found by the contour at half its peak height above the sky, not by saturation [proposed: the lead's
# decision of 2026-11-11]. The Moon is a resolved disk of a known angular size (its predicted diameter from the
# ephemeris distance), and the contour at half its peak follows the disk's edge whatever the exposure or tone curve;
# an unresolved light's region at half its peak is only its blur, a few pixels. A saturation rule cannot see every
# Moon: the first real head-worn clip tried records its Moon as a peak (frame maxima 241 to 244 of 255) whose spot
# within 3 codes of the top is 1 to 4 px but 146 px at 8 codes down [tested, counts only].
# The candidate is the frame's brightest point after the Gaussian of 2.5 px that horizon_solver.py finds its Moon's
# peak with, so one hot pixel or a compression speck is not taken for it; its height and region are then measured on
# the frame's own pixels [proposed: smoothing lowers an unresolved light's peak and widens it toward the Moon's size]
PEAK_SMOOTHING_SIGMA_PX = 2.5
# The peak at least 200 of 255 [proposed: a night exposure records the Moon near its top code; a dimmer peak is a light
# or noise]
MIN_PEAK_LUMA = 200.0
# and at least 10 times the noise above the sky background: the frame's median, and 1.4826 times its median absolute
# deviation floored at 1e-4, as horizon_solver.py measures them (its bright source test asks the same 10 of its Moon)
# [proposed]. The spot's reported SNR keeps the floor it had, the detector's rounding noise of the frame's own code
# step, so a frame whose MAD is 0 does not report a near-infinite SNR
MIN_PEAK_SNR = 10.0
SKY_NOISE_FLOOR = 1e-4
# The region: the pixels around the peak above the sky plus half the peak's height over it
HALF_PEAK = 0.5
# The Moon's volumetric mean radius (NASA, Moon Fact Sheet, nssdc.gsfc.nasa.gov/planetary/factsheet/moonfact.html,
# read at source 2026-11-11)
MOON_RADIUS_KM = 1737.4
# The region's equivalent diameter: at least 0.8 times the Moon's predicted diameter at the widest field of view
# (REQ-SKY-3.3, so a distant point light is refused) and at most 8 times it at the narrowest [proposed: wide enough
# for an overexposed Moon's bloom, narrow enough to refuse a lit cloud or a twilight band]
MIN_DIAMETER_FACTOR = 0.8
MAX_DIAMETER_FACTOR = 8.0
# Every other region above the same half-peak level under half the Moon's area (REQ-SKY-3.3)
UNIQUE_AREA_FACTOR = 2.0
# The centroid is measured over the region grown by two pixels, so the light just outside the half-peak contour (the
# disk's soft edge, an overexposed Moon's bloom) counts (design)
BLOOM_DILATION_PX = 2
EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)
# The predicted Moon must be more than 5 degrees up (REQ-SKY-3.1)
MIN_MOON_ALT_DEG = 5.0
# A level head within 10 degrees of roll either way [proposed: the lead's decision of 2026-11-10, open to the owner]
ROLL_LIMIT_DEG = 10.0
SWEPT_ROLLS_DEG = (-ROLL_LIMIT_DEG, 0.0, ROLL_LIMIT_DEG)
# The level constraint's two points: at the frame centre's altitude, 10 degrees either side of it in azimuth (design)
LEVEL_AZ_OFFSET_DEG = 10.0
# Three residuals fit three unknowns exactly, so a fit that cannot reach half a pixel did not converge (design)
MAX_FIT_RESIDUAL_PX = 0.5

_NOT_LINED_UP = "The sky is not lined up over the picture: "
NO_PROFILE = _NOT_LINED_UP + "the camera has no head-worn camera profile, so its field of view is not known."
MIRRORED = (
    _NOT_LINED_UP + "the frame is stored mirrored, and the head-worn pose needs a frame stored as the camera saw it."
)
NO_FRAMES = _NOT_LINED_UP + "no frame could be read to look for the Moon."
MOON_NOT_UP = _NOT_LINED_UP + f"the Moon was not more than {MIN_MOON_ALT_DEG:g} degrees above the horizon."


@dataclass(frozen=True)
class MoonBlob:
    """The brightest spot that passed the Moon test, measured in the frame's pixels: its flux-weighted centroid, the
    area of its region above half its peak and that region's equivalent diameter, the radius of the region its
    centroid was measured over (grown by `BLOOM_DILATION_PX`), and its flux, SNR and FWHM. Flux and SNR are lower
    bounds where its brightest pixels are clipped."""

    x_px: float
    y_px: float
    area_px: int
    equivalent_diameter_px: float
    radius_px: float
    flux: float
    snr: float
    fwhm_px: float
    centroid_uncertainty_px: float
    peak_to_total_ratio: float


@dataclass(frozen=True)
class Refusal:
    """Why no pose was found: `kind` is one of no_profile, mirrored, no_frames, moon_not_up, not_moon or fit, and
    `reason` says it in plain words, with no capture time, place or file name."""

    kind: str
    reason: str
    blob: MoonBlob | None = None
    frame_time_sec: float | None = None


@dataclass(frozen=True)
class TopocentricMoon:
    """The Moon as seen from the place at the moment: the ephemeris (geocentric) position lowered by its parallax
    shift, with the ephemeris altitude it was lowered from, the shift, and the predicted angular radius."""

    ra_deg: float
    dec_deg: float
    alt_deg: float
    az_deg: float
    predicted_alt_deg: float
    shift_arcsec: float
    distance_km: float
    radius_deg: float


@dataclass(frozen=True)
class PoseResult:
    """A pose lined up from the Moon in one frame: the nominal WCS, the pose estimate with its sweep, the blob, the
    Moon it was lined up to, the frame's time and moment, and the camera profile it assumed."""

    wcs: WCSModel
    pose_estimate: PoseEstimate
    blob: MoonBlob
    moon: TopocentricMoon
    frame_time_sec: float
    moment: str
    profile: CameraProfile


def _not_confirmed(detail: str) -> Refusal:
    return Refusal("not_moon", f"The frame's bright spots were not confirmed as the Moon: {detail}.")


def find_moon_blob(luma: np.ndarray, *, min_diameter_px: float, max_diameter_px: float) -> MoonBlob | Refusal:
    """The Moon test on one gray frame (0 to 255), by the contour at half the peak's height above the sky.

    The candidate is the brightest point of the frame smoothed by a Gaussian of PEAK_SMOOTHING_SIGMA_PX; its peak is
    that pixel's own code, which must be at least MIN_PEAK_LUMA and at least MIN_PEAK_SNR times the noise above the
    sky (the frame's median, and 1.4826 times its median absolute deviation floored at SKY_NOISE_FLOOR, as
    horizon_solver.py measures them). Its region is the 8-connected pixels
    about it above the sky plus HALF_PEAK of the peak's height; the region's equivalent diameter must lie inside the
    limits, and every other region above that same level must be under 1 / UNIQUE_AREA_FACTOR of its area. Then the
    spot is measured: the background-subtracted, flux-weighted centroid over the region grown by BLOOM_DILATION_PX,
    its FWHM from second moments. Else the refusal, saying which condition failed."""
    luma = np.asarray(luma, dtype=np.float64)
    background = float(np.median(luma))
    mad_sigma = 1.4826 * float(np.median(np.abs(luma - background)))
    smoothed = ndimage.gaussian_filter(luma, sigma=PEAK_SMOOTHING_SIGMA_PX)
    peak_y, peak_x = np.unravel_index(int(np.argmax(smoothed)), smoothed.shape)
    peak = float(luma[peak_y, peak_x])
    if not peak >= MIN_PEAK_LUMA:
        return _not_confirmed(f"the brightest spot peaks at {peak:g} of 255, under {MIN_PEAK_LUMA:g}")
    height = peak - background
    peak_snr = height / max(mad_sigma, SKY_NOISE_FLOOR)
    if not peak_snr >= MIN_PEAK_SNR:
        return _not_confirmed(
            f"the brightest spot peaks {peak_snr:.1f} times the noise above the sky, under {MIN_PEAK_SNR:g}"
        )
    labels, _ = ndimage.label(luma > background + HALF_PEAK * height, structure=EIGHT_CONNECTED)
    label = int(labels[peak_y, peak_x])
    areas = np.bincount(labels.ravel())
    area = int(areas[label])
    diameter = 2.0 * math.sqrt(area / math.pi)
    if not diameter >= min_diameter_px:
        return _not_confirmed(
            f"the brightest spot is {diameter:.1f} px across at half its peak, under the {min_diameter_px:.1f} px the "
            "Moon needs"
        )
    if not diameter <= max_diameter_px:
        return _not_confirmed(
            f"the brightest spot is {diameter:.1f} px across at half its peak, over the {max_diameter_px:.1f} px the "
            "Moon allows"
        )
    others = np.delete(areas[1:], label - 1)
    if others.size and not float(area) > UNIQUE_AREA_FACTOR * float(others.max()):
        return _not_confirmed("another spot rises above half the brightest one's peak with half its area or more")

    # Measured in a window around the region, grown by the bloom margin: the same numbers as over the whole frame
    rows, cols = ndimage.find_objects(labels)[label - 1]
    pad = BLOOM_DILATION_PX + 1
    y0, y1 = max(0, rows.start - pad), min(luma.shape[0], rows.stop + pad)
    x0, x1 = max(0, cols.start - pad), min(luma.shape[1], cols.stop + pad)
    region = labels[y0:y1, x0:x1] == label
    grown = ndimage.binary_dilation(region, structure=EIGHT_CONNECTED, iterations=BLOOM_DILATION_PX)
    # Positive: the peak's own pixel is in the region, `height` above the sky
    weights = np.where(grown, np.clip(luma[y0:y1, x0:x1] - background, 0.0, None), 0.0)
    total = float(weights.sum())
    yy, xx = np.mgrid[y0:y1, x0:x1]
    x_px = float((xx * weights).sum() / total)
    y_px = float((yy * weights).sum() / total)
    # Width from the spot's second moments (2.355 sigma is a Gaussian's FWHM), as horizon_solver.py measures its Moon
    sigma_sq = float((((xx - x_px) ** 2 + (yy - y_px) ** 2) * weights).sum() / (2.0 * total))
    fwhm_px = 2.355 * math.sqrt(max(sigma_sq, 0.0))
    snr = height / _effective_sigma(mad_sigma, luma)
    return MoonBlob(
        x_px=x_px,
        y_px=y_px,
        area_px=area,
        equivalent_diameter_px=diameter,
        radius_px=math.sqrt(float(grown.sum()) / math.pi),
        flux=total,
        snr=snr,
        fwhm_px=fwhm_px,
        centroid_uncertainty_px=fwhm_px / (2.355 * snr),
        peak_to_total_ratio=height / total,
    )


def scale_deg_per_px(fov_diagonal_deg: float, width: int, height: int) -> float:
    """The scale of a rectilinear (TAN) frame whose diagonal spans `fov_diagonal_deg`: 2 tan(fov / 2) / diagonal."""
    return math.degrees(2.0 * math.tan(math.radians(fov_diagonal_deg) / 2.0) / math.hypot(width, height))


def topocentric_moon(moment: str, lat: float, lon: float) -> TopocentricMoon:
    """The Moon seen from (`lat`, `lon`) at `moment`: the ephemeris position turned to altitude and azimuth, lowered
    by `moon_parallax_shift_arcsec`, turned back to ICRS. Its radius is asin(MOON_RADIUS_KM / distance) at the
    distance from the place (the Earth's equatorial radius toward the zenith)."""
    (moon,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",))
    shift = moon_parallax_shift_arcsec(moon.distance_au, moon.alt_deg)[1]
    alt = moon.alt_deg - shift / 3600.0
    ra, dec = altaz_to_icrs(alt, moon.az_deg, lat, lon, moment)
    geocentric_km = moon.distance_au * AU_KM
    topocentric_km = math.sqrt(
        geocentric_km**2
        + EARTH_EQUATORIAL_RADIUS_KM**2
        - 2.0 * geocentric_km * EARTH_EQUATORIAL_RADIUS_KM * math.sin(math.radians(moon.alt_deg))
    )
    return TopocentricMoon(
        ra_deg=ra,
        dec_deg=dec,
        alt_deg=alt,
        az_deg=moon.az_deg,
        predicted_alt_deg=moon.alt_deg,
        shift_arcsec=shift,
        distance_km=topocentric_km,
        radius_deg=math.degrees(math.asin(MOON_RADIUS_KM / topocentric_km)),
    )


def moon_diameter_limits_px(
    moon: TopocentricMoon, fov_range: tuple[float, float], width: int, height: int
) -> tuple[float, float]:
    """The Moon test's diameter limits in pixels: `MIN_DIAMETER_FACTOR` times its predicted diameter at the widest
    field of view, `MAX_DIAMETER_FACTOR` times it at the narrowest."""
    low_fov, high_fov = fov_range
    diameter_deg = 2.0 * moon.radius_deg
    return (
        MIN_DIAMETER_FACTOR * diameter_deg / scale_deg_per_px(high_fov, width, height),
        MAX_DIAMETER_FACTOR * diameter_deg / scale_deg_per_px(low_fov, width, height),
    )


def _orientation(orientation: Mapping[str, Any] | None) -> tuple[int, bool]:
    """(quarter turns clockwise, mirrored) of a SourceAsset.displayOrientation; none shows the pixels as stored."""
    if not isinstance(orientation, Mapping):
        return 0, False
    turns = orientation.get("quarterTurnsClockwise", 0)
    if isinstance(turns, bool) or not isinstance(turns, int) or not 0 <= turns <= 3:
        raise ValueError("A display orientation turns the frame by 0 to 3 quarter turns.")
    return turns, bool(orientation.get("mirrored", False))


def is_mirrored(orientation: Mapping[str, Any] | None) -> bool:
    """Whether the display orientation mirrors the stored frame: then the stored pixels are not as the camera saw
    them, and a parity -1 WCS would place the sky mirrored."""
    return _orientation(orientation)[1]


def _up(moon: TopocentricMoon) -> bool:
    return moon.predicted_alt_deg > MIN_MOON_ALT_DEG


def moon_is_up(moment: str, lat: float, lon: float) -> bool:
    """Whether the predicted Moon (the ephemeris altitude, as its predicted finding carries it) is more than
    MIN_MOON_ALT_DEG above the horizon at `moment`."""
    return _up(topocentric_moon(moment, lat, lon))


def upright_vector(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """A stored-pixel vector (x right, y down) as the upright frame shows it after `turns` clockwise quarter turns
    (packages/contracts/src/media.ts DisplayOrientationSchema)."""
    for _ in range(turns):
        dx, dy = -dy, dx
    return dx, dy


def _fit_accepted(residuals: np.ndarray) -> bool:
    """Whether a fit reproduced its constraints: every residual finite and none above MAX_FIT_RESIDUAL_PX."""
    return bool(np.all(np.isfinite(residuals))) and float(np.max(np.abs(residuals))) <= MAX_FIT_RESIDUAL_PX


def _normalized(wcs: WCSModel) -> WCSModel:
    """The same projection with crval's declination in [-90, 90] and the rotation in [-180, 180): a centre fitted
    past a pole is the same pointing at RA + 180 and turned by half a turn."""
    ra, dec, rotation = wcs.crval_ra_deg, wcs.crval_dec_deg, wcs.rotation_deg
    if abs(dec) > 90.0:
        dec = math.copysign(180.0, dec) - dec
        ra += 180.0
        rotation += 180.0
    return replace(wcs, crval_ra_deg=ra % 360.0, crval_dec_deg=dec, rotation_deg=(rotation + 180.0) % 360.0 - 180.0)


def fit_level_pose(
    moon: TopocentricMoon,
    x_px: float,
    y_px: float,
    *,
    width: int,
    height: int,
    fov_diagonal_deg: float,
    roll_deg: float,
    orientation: Mapping[str, Any] | None,
    moment: str,
    lat: float,
    lon: float,
) -> WCSModel | Refusal:
    """The TAN WCS at this diagonal field of view (fixed scale, parity -1, k1 = k2 = 0, crpix at the frame centre)
    that puts the topocentric Moon on (`x_px`, `y_px`) and gives the horizon `roll_deg` in the upright frame.

    Unknowns: crval's RA and Dec and the rotation. Residuals: the Moon's projection minus the pixel (two), and the
    level constraint (one): two points at the frame centre's altitude, `LEVEL_AZ_OFFSET_DEG` either side in azimuth,
    mapped to the upright frame, have a row difference (right minus left) equal to tan(roll) times their column
    difference, so a positive roll puts the horizon's right side lower. Levenberg-Marquardt from the design's
    closed-form start, then, when that fit is refused, from a start lined up about the Moon itself (the closed form
    runs off for a Moon near a corner with the frame centre high, tested in P-SKY-5). Refused when the largest
    residual of the returned WCS exceeds MAX_FIT_RESIDUAL_PX or its upright frame shows the sky upside down."""
    turns, mirrored = _orientation(orientation)
    if mirrored:
        return Refusal("mirrored", MIRRORED)
    scale = scale_deg_per_px(fov_diagonal_deg, width, height)
    crpix_x, crpix_y = width / 2.0, height / 2.0
    tan_roll = math.tan(math.radians(roll_deg))

    def wcs_for(params: np.ndarray) -> WCSModel:
        return WCSModel(
            crval_ra_deg=float(params[0]),
            crval_dec_deg=float(params[1]),
            crpix_x=crpix_x,
            crpix_y=crpix_y,
            scale_deg_per_px=scale,
            rotation_deg=float(params[2]),
            k1=0.0,
            k2=0.0,
            parity=-1,
            width=width,
            height=height,
        )

    def level_line(wcs: WCSModel) -> tuple[float, float]:
        """The upright-frame vector from the left level point to the right one."""
        alt, az = icrs_to_altaz(wcs.crval_ra_deg, wcs.crval_dec_deg, lat, lon, moment)
        lx, ly = wcs.world_to_pixel(*altaz_to_icrs(alt, az - LEVEL_AZ_OFFSET_DEG, lat, lon, moment))
        rx, ry = wcs.world_to_pixel(*altaz_to_icrs(alt, az + LEVEL_AZ_OFFSET_DEG, lat, lon, moment))
        return upright_vector(rx - lx, ry - ly, turns)

    def residuals_of(wcs: WCSModel) -> np.ndarray:
        mx, my = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
        dx, dy = level_line(wcs)
        return np.array([mx - x_px, my - y_px, dy - tan_roll * dx], dtype=np.float64)

    def residuals(params: np.ndarray) -> np.ndarray:
        res = residuals_of(wcs_for(params))
        return res if np.all(np.isfinite(res)) else np.full(3, 1e4, dtype=np.float64)

    def levelled(start_ra: float, start_dec: float) -> np.ndarray:
        """A start centre with the rotation that gives the horizon the roll there."""
        dx0, dy0 = level_line(wcs_for(np.array([start_ra, start_dec, 0.0])))
        return np.array([start_ra, start_dec, math.degrees(math.atan2(dy0, dx0)) - roll_deg])

    def first_order_start() -> np.ndarray:
        """The design's closed-form start: the centre's altitude from the Moon's row offset in the upright frame times
        the scale, its azimuth from the column offset over the cosine of the altitude."""
        ux, uy = upright_vector(x_px - crpix_x, y_px - crpix_y, turns)
        alt = min(89.0, max(-89.0, moon.alt_deg + uy * scale))
        az = (moon.az_deg - ux * scale / max(0.1, math.cos(math.radians(alt)))) % 360.0
        return levelled(*altaz_to_icrs(alt, az, lat, lon, moment))

    def tangent_start() -> np.ndarray:
        """A start for a Moon far from the centre with the centre high, where the first-order azimuth runs off: the
        frame centre read through a level TAN projection about the Moon itself at the same scale and roll."""
        about = WCSModel(
            crval_ra_deg=moon.ra_deg, crval_dec_deg=moon.dec_deg, crpix_x=x_px, crpix_y=y_px,
            scale_deg_per_px=scale, width=width, height=height,
        )
        zx, zy = about.world_to_pixel(*altaz_to_icrs(min(89.9, moon.alt_deg + 1.0), moon.az_deg, lat, lon, moment))
        ux, uy = upright_vector(zx - x_px, zy - y_px, turns)
        # Straight up in the upright frame, then turned clockwise by the roll
        rotation = math.degrees(math.atan2(uy, ux)) + 90.0 - roll_deg
        return levelled(*replace(about, rotation_deg=rotation).pixel_to_world(crpix_x, crpix_y))

    for start in (first_order_start, tangent_start):
        fit = optimize.least_squares(
            residuals, start(), method="lm", xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=400
        )
        wcs = _normalized(wcs_for(fit.x))
        # Measured on the WCS that is returned, and the upright frame must show the sky the right way up
        if _fit_accepted(residuals_of(wcs)) and level_line(wcs)[0] > 0.0:
            return wcs
    return Refusal("fit", "The fit did not put the Moon on its pixel with a level head.")


def _assumptions(profile: CameraProfile, moon: TopocentricMoon) -> tuple[str, ...]:
    low, high = profile.fov_diagonal_deg_range
    return (
        f"The camera's diagonal field of view is {low:g} to {high:g} degrees, from its camera profile "
        f"({profile.fov_label}); the sweep takes {low:g}, {profile.fov_diagonal_deg_mid:g} and {high:g} degrees.",
        "The camera was not zoomed: the camera profile's field of view is for no zoom, the pose reads nothing in the "
        "file that would show a zoom, and a zoomed frame sees a narrower field than the sweep takes.",
        f"The head was level: the horizon's roll in the upright frame is within {ROLL_LIMIT_DEG:g} degrees either way; "
        f"the sweep takes {SWEPT_ROLLS_DEG[0]:g}, {SWEPT_ROLLS_DEG[1]:g} and +{SWEPT_ROLLS_DEG[2]:g} degrees.",
        "The lens is rectilinear (gnomonic), with no distortion, and centred on the frame.",
        "The spot is the Moon, and its light centroid lies on the Moon's disk: overexposure spreads it and a partial "
        "phase moves it toward the lit edge, which the centroid term, the Moon's predicted radius of "
        f"{moon.radius_deg:.2f} degrees, covers.",
        "The Moon is placed where it is seen from the place (topocentric): its ephemeris position lowered by its "
        f"parallax, {moon.shift_arcsec:,.0f} arcsec; refraction is left out for the Moon and the stars alike, so it "
        "cancels to first order.",
    )


def head_worn_moon_pose(
    luma: np.ndarray,
    *,
    frame_time_sec: float,
    moment: str,
    lat: float,
    lon: float,
    profile: CameraProfile,
    orientation: Mapping[str, Any] | None,
) -> PoseResult | Refusal:
    """The pose from one frame recorded at `moment`, `frame_time_sec` into the recording: the Moon test on this frame
    alone, then the nine fits of the sweep; any refused fit refuses the pose, since its uncertainty could not be
    bounded."""
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    moon = topocentric_moon(moment, lat, lon)
    if not _up(moon):
        return Refusal("moon_not_up", MOON_NOT_UP)
    height, width = np.shape(luma)
    min_d, max_d = moon_diameter_limits_px(moon, profile.fov_diagonal_deg_range, width, height)
    blob = find_moon_blob(luma, min_diameter_px=min_d, max_diameter_px=max_d)
    if isinstance(blob, Refusal):
        return replace(blob, frame_time_sec=frame_time_sec)
    low, high = profile.fov_diagonal_deg_range
    middle = profile.fov_diagonal_deg_mid
    swept: list[WCSModel] = []
    nominal: WCSModel | None = None
    for fov in (low, middle, high):
        for roll in SWEPT_ROLLS_DEG:
            wcs = fit_level_pose(
                moon, blob.x_px, blob.y_px, width=width, height=height, fov_diagonal_deg=fov, roll_deg=roll,
                orientation=orientation, moment=moment, lat=lat, lon=lon,
            )
            if isinstance(wcs, Refusal):
                return replace(wcs, blob=blob, frame_time_sec=frame_time_sec)
            swept.append(wcs)
            if fov == middle and roll == 0.0:
                nominal = wcs
    assert nominal is not None
    centre_alt, centre_az = icrs_to_altaz(nominal.crval_ra_deg, nominal.crval_dec_deg, lat, lon, moment)
    estimate = PoseEstimate(
        method=POSE_METHOD,
        beacon="moon",
        center_alt_deg=centre_alt,
        center_az_deg=centre_az,
        roll_deg=0.0,
        fov_diagonal_deg=middle,
        fov_source=f"The camera profile for {profile.model}, {profile.fov_label}: {profile.fov_source}",
        assumptions=_assumptions(profile, moon),
        swept_wcs=tuple(swept),
        max_center_offset_deg=max(
            angular_separation_deg(nominal.crval_ra_deg, nominal.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg)
            for w in swept
        ),
        beacon_centroid_uncertainty_deg=moon.radius_deg,
    )
    return PoseResult(nominal, estimate, blob, moon, frame_time_sec, moment, profile)


def pose_from_frames(
    frames: Sequence[tuple[float, str, np.ndarray]],
    *,
    lat: float,
    lon: float,
    profile: CameraProfile,
    orientation: Mapping[str, Any] | None,
    is_clip: bool = True,
) -> PoseResult | Refusal:
    """The first frame, in time order, that passes the Moon test and the fit; each frame is (its time into the
    recording, its moment, its gray pixels) and is judged and used alone, never stacked. With none passing, the
    refusal that says most: a Moon whose fit was refused, else not confirmed as the Moon, else the Moon not up."""
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    ordered = sorted(frames, key=lambda frame: frame[0])
    if not ordered:
        return Refusal("no_frames", NO_FRAMES)
    refusals: list[Refusal] = []
    for frame_time_sec, moment, luma in ordered:
        result = head_worn_moon_pose(
            luma, frame_time_sec=frame_time_sec, moment=moment, lat=lat, lon=lon, profile=profile,
            orientation=orientation,
        )
        if isinstance(result, PoseResult):
            return result
        refusals.append(result)
    fitted = next((r for r in refusals if r.kind == "fit"), None)
    if fitted is not None and fitted.blob is not None:
        where = f"in the frame {fitted.frame_time_sec:.2f} s into the clip" if is_clip else "in the photo"
        return Refusal(
            "fit",
            _NOT_LINED_UP + f"the spot at ({fitted.blob.x_px:.0f}, {fitted.blob.y_px:.0f}) px {where} was confirmed "
            f"as the Moon, but no level-head pose put it there within {MAX_FIT_RESIDUAL_PX:g} px.",
            fitted.blob,
            fitted.frame_time_sec,
        )
    if all(r.kind == "moon_not_up" for r in refusals):
        return Refusal("moon_not_up", MOON_NOT_UP)
    if not is_clip:
        where = "in the photo"
    else:
        where = "in the frame searched" if len(ordered) == 1 else f"in the {len(ordered)} frames searched"
    return Refusal(
        "not_moon",
        _NOT_LINED_UP + f"what was seen {where} was not confirmed as the Moon (that needs a frame whose brightest spot "
        f"peaks at {MIN_PEAK_LUMA:g} of 255 or more and at least {MIN_PEAK_SNR:g} times the noise above the sky, is "
        "about the Moon's size at half its peak, and has no other spot above that level with half its area or more).",
    )
