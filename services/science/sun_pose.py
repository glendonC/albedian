"""The Sun as the beacon by day, its first pieces (sky-6a; spec predicted-sky-for-every-upload REQ-SKY-4.3): the Sun's
place seen from the photo's place, the Sun's core in a day frame, and the clock check that comes before any Sun pose;
and the skyline of a day frame (sky-6b), the step below the Sun in the columns about it; and the Sun plus horizon
pose (sky-6c), lined up through pose_estimate.py's own fit, after the clock check.

Measured on two public daytime photos (.lead/kiro/reports/SCIENCE-INGEST/sky-6-research.md) [tested]: the Sun's
clipped core is 4.5 to 6.2 times its disk across (its bloom), thousands of single clipped specks (glitter on water)
stand beside it, and one photo's camera clock is hours off, which would line the sky up wrong without a check. The
skyline of a day frame and the fits come in the next units; nothing here draws a sky.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from scipy import ndimage

from services.science.catalog import require_place
from services.science.detector import _effective_sigma
from services.science.ephemeris import AU_KM, compute_solar_system_ephemerides
from services.science.catalog import icrs_to_frame_altaz
from services.science.pose_estimate import (
    MAX_DIAMETER_FACTOR,
    SKYLINE_ALTS_DEG,
    SKYLINE_NOISE_FLOOR,
    MoonBlob,
    Refusal,
    Skyline,
    TopocentricMoon,
    _orientation,
    fit_horizon_pose,
    fov_diagonal_deg,
    frame_term_arcsec,
    pose_frame,
    to_stored,
)
from services.science.wcs_models import PoseEstimate, WCSModel

# The nominal solar radius, 695,700 km [verified: IAU 2015 Resolution B3]
SUN_RADIUS_KM = 695_700.0
# [proposed, measured on two photos] The Sun's core: the pixels within this many codes of the frame's own brightest,
# so a Sun that is not clipped (one photo peaked below 250) is found as a clipped one is
SUN_CORE_CODES = 10.0
# [proposed] A core smaller than this many pixels across is a speck (glitter, a star of light), never the Sun
MIN_SUN_CORE_DIAMETER_PX = 6.0
# The 8-bit full scale of the gray frame `find_sun_core` reads [verified: services/science/detector.py FULL_SCALES]
SUN_CLIPPED_LUMA = 255.0

# sky-6b, the day skyline [proposed, measured on two public photos: sky-6-research.md]. The columns a sixth of the
# frame's width either side of the Sun's; each row's median over them, smoothed by a Gaussian of this many rows
DAY_SKYLINE_HALF_BAND = 1.0 / 6.0
DAY_SKYLINE_SMOOTHING_ROWS = 6.0
# A step is the smoothed profile's change over this many rows either side of a row
DAY_SKYLINE_REACH_ROWS = 12
# Its noise: each row's median absolute deviation over the band, their median over this many rows above the step
DAY_SKYLINE_NOISE_ROWS = 240
# Rows tried, every so many, from three reaches below the Sun's glare
DAY_SKYLINE_ROW_STEP = 4
# The highest step stands at least this many times its noise, and this many times any step more than
# DAY_SKYLINE_APART_ROWS from it (the two photos: 12.5 and 6.7 against next bests of 2.2 and 1.4)
DAY_SKYLINE_MIN_SNR = 5.0
DAY_SKYLINE_MIN_RATIO = 2.5
DAY_SKYLINE_APART_ROWS = 60

# The solver identity's name the Sun pose's solutions carry (predicted_sky.SUN_POSE_SOLVER)
SUN_POSE_SOLVER_NAME = "AlbedianSunHorizonPose"
# sky-6c. The Sun is the beacon only when it is more than this far up (REQ-SKY-4.3)
MIN_SUN_ALT_DEG = 5.0
# [proposed] How far the Sun's measured height above the skyline may lie from its altitude at the photo's time before
# the clock is taken to be wrong: Michigan's photo lay 0.45 deg off, Svalbard's 3.9
CLOCK_TOLERANCE_DEG = 1.5
# [proposed, measured on one photo] How far the core's centre may sit from the Sun's in its glare: Svalbard's sunstar
# put it 0.45 deg off; the beacon term takes this much beside the Sun's radius
SUN_CORE_OFFSET_DEG = 0.5
# The 35 mm frame's diagonal, for a field of view from a 35 mm equivalent focal length
FRAME_35MM_DIAGONAL_MM = math.hypot(36.0, 24.0)

# The words a person reads are the design lane's C23 (.lead/uiux/UIUX-sunwords-READY.md, decided 2026-11-19 17:08):
# the Moon poses' family prefix, the reason first (shown) and its numbers in a second sentence (kept in the record)
FAMILY_PREFIX = "The sky is not lined up over the picture: "
SUN_LOW = (
    FAMILY_PREFIX + "the Sun was too low at the photo's time to line it up with. "
    f"It was not more than {MIN_SUN_ALT_DEG:g} degrees above the horizon."
)
SUN_MIRRORED = FAMILY_PREFIX + "the photo is stored mirrored, so the Sun cannot be lined up as the camera saw it."
NO_FOCAL = FAMILY_PREFIX + "the photo's file gives no 35 mm focal length, so its time cannot be checked against the Sun."
NO_SUN = (
    FAMILY_PREFIX + "no Sun was found in the frame. No bright region larger than a speck stands at the frame's "
    "brightest level."
)
SUN_BLOOM = FAMILY_PREFIX + "the brightest glare is too wide to be the Sun's."
SUN_EDGE = FAMILY_PREFIX + "the Sun's glare touches the edge of the frame, so its centre cannot be measured."
NO_DAY_SKYLINE = (
    FAMILY_PREFIX + "no skyline was found below the Sun to line it up with. No single line stands out where the sky "
    "meets the ground or the water."
)


def sun_bloom_reason(focal_35mm_mm: float) -> str:
    """SUN_BLOOM with its limit through the file's own lens (C23)."""
    return f"{SUN_BLOOM} It is wider than {MAX_DIAMETER_FACTOR:g} times the Sun's disk through the file's own {focal_35mm_mm:g} mm lens."


@dataclass(frozen=True)
class SunCore:
    """The Sun's core in a frame: the centroid of its brightest region in pixels, its area and equivalent diameter,
    and whether its own brightest pixels reach the 8-bit clip (with how many do)."""

    x_px: float
    y_px: float
    area_px: int
    diameter_px: float
    clipped: bool = False
    clipped_area_px: int = 0


def topocentric_sun(moment: str, lat: float, lon: float) -> TopocentricMoon:
    """The Sun seen from (`lat`, `lon`) at `moment`, as the beacon record the poses take (the Moon's dataclass): the
    ephemeris record as it is, asked for topocentric, its radius asin(SUN_RADIUS_KM / distance) and its uncertainty
    the one the record states."""
    lat, lon = require_place(lat, lon)
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",), topocentric=True)
    distance_km = sun.distance_au * AU_KM
    return TopocentricMoon(
        ra_deg=sun.ra_deg,
        dec_deg=sun.dec_deg,
        alt_deg=sun.alt_deg,
        az_deg=sun.az_deg,
        distance_km=distance_km,
        radius_deg=math.degrees(math.asin(SUN_RADIUS_KM / distance_km)),
        uncertainty_deg=sun.uncertainty_arcsec / 3600.0,
    )


def _find_core_region(luma: np.ndarray, *, max_diameter_px: float) -> tuple[SunCore, np.ndarray] | Refusal:
    """`find_sun_core` together with the core's boolean mask in `luma`."""
    luma = np.asarray(luma, dtype=np.float64)
    top = float(np.max(luma))
    mask = luma >= top - SUN_CORE_CODES
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    if count == 0:
        return Refusal("no_sun", NO_SUN)
    sizes = np.bincount(labels.ravel())[1:]
    largest = int(np.argmax(sizes)) + 1
    area = int(sizes[largest - 1])
    diameter = 2.0 * math.sqrt(area / math.pi)
    if diameter < MIN_SUN_CORE_DIAMETER_PX:
        return Refusal("no_sun", NO_SUN)
    if diameter > max_diameter_px:
        return Refusal("sun_bloom", SUN_BLOOM)
    core_mask = labels == largest
    rows, cols = np.nonzero(core_mask)
    height, width = luma.shape
    if rows.min() == 0 or cols.min() == 0 or rows.max() == height - 1 or cols.max() == width - 1:
        return Refusal("sun_edge", SUN_EDGE)
    at_clip = luma[core_mask] >= SUN_CLIPPED_LUMA * (1.0 - 1e-9)
    clipped = bool(np.any(at_clip))
    core = SunCore(
        x_px=float(cols.mean()),
        y_px=float(rows.mean()),
        area_px=area,
        diameter_px=diameter,
        clipped=clipped,
        clipped_area_px=int(np.sum(at_clip)) if clipped else 0,
    )
    return core, core_mask


def find_sun_core(luma: np.ndarray, *, max_diameter_px: float) -> SunCore | Refusal:
    """The Sun's core in one gray frame (0 to 255): the largest 8-connected region within SUN_CORE_CODES of the
    frame's brightest code, its centroid unweighted (the core is clipped or nearly so, so its shape, not its light,
    places it). Refused (no_sun) when that region is under MIN_SUN_CORE_DIAMETER_PX across, (sun_bloom) when it is
    wider than `max_diameter_px`, and (sun_edge) when it touches the frame's edge."""
    found = _find_core_region(luma, max_diameter_px=max_diameter_px)
    return found if isinstance(found, Refusal) else found[0]


def clock_check(measured_height_deg: float, predicted_alt_deg: float, *, tolerance_deg: float) -> Refusal | None:
    """Before any Sun pose: the Sun's height above the skyline as measured on the frame against its altitude at the
    photo's recorded time; more than `tolerance_deg` apart, no pose, since the camera's clock (or its zone) would put
    the sky somewhere else (Svalbard's photo: 14.1 measured, 9.9 predicted). None when they agree."""
    if not tolerance_deg > 0.0:
        raise ValueError("The clock check's tolerance is a positive angle.")
    if abs(measured_height_deg - predicted_alt_deg) <= tolerance_deg:
        return None
    return Refusal(
        "clock",
        FAMILY_PREFIX + "the camera's clock or its time zone looks wrong. The Sun stands about "
        f"{measured_height_deg:.1f} degrees above the skyline in the picture, but was {predicted_alt_deg:.1f} degrees "
        "up at the recorded time.",
    )


def find_day_skyline(luma: np.ndarray, core: SunCore) -> Skyline | Refusal:
    """The skyline of an upright day frame below the Sun's core: in the columns DAY_SKYLINE_HALF_BAND of the width
    either side of the Sun's, each row's median, smoothed; every DAY_SKYLINE_ROW_STEP rows from three reaches below
    the glare, the step over DAY_SKYLINE_REACH_ROWS either side against the noise of the rows above (each row's
    median absolute deviation over the band, 1.4826 times their median over DAY_SKYLINE_NOISE_ROWS, at least
    SKYLINE_NOISE_FLOOR), of either sign, so water darker or brighter than the sky is found alike. The highest such
    step is the skyline when it reaches DAY_SKYLINE_MIN_SNR and DAY_SKYLINE_MIN_RATIO times every step more than
    DAY_SKYLINE_APART_ROWS from it; its row is the steepest within its reach, to a fraction by a parabola, its half
    width half the rows about it where the slope is at least half its peak. Else none (no_skyline)."""
    luma = np.asarray(luma, dtype=np.float64)
    height, width = luma.shape
    half = max(1, int(width * DAY_SKYLINE_HALF_BAND))
    centre = int(round(core.x_px))
    band = luma[:, max(0, centre - half) : min(width, centre + half)]
    medians = np.median(band, axis=1)
    deviation = np.median(np.abs(band - medians[:, None]), axis=1)
    profile = ndimage.gaussian_filter1d(medians, DAY_SKYLINE_SMOOTHING_ROWS)
    reach = DAY_SKYLINE_REACH_ROWS
    start = int(math.ceil(core.y_px + core.diameter_px))
    scored: list[tuple[float, int]] = []
    for row in range(start + 3 * reach, height - reach, DAY_SKYLINE_ROW_STEP):
        above = deviation[max(start, row - DAY_SKYLINE_NOISE_ROWS) : row - reach]
        noise = max(1.4826 * float(np.median(above)), SKYLINE_NOISE_FLOOR)
        scored.append((abs(float(profile[row + reach] - profile[row - reach])) / noise, row))
    if not scored:
        return Refusal("no_skyline", NO_DAY_SKYLINE)
    snr, row = max(scored)
    rivals = [s for s, r in scored if abs(r - row) > DAY_SKYLINE_APART_ROWS]
    if snr < DAY_SKYLINE_MIN_SNR or (rivals and max(rivals) * DAY_SKYLINE_MIN_RATIO > snr):
        return Refusal("no_skyline", NO_DAY_SKYLINE)
    slope = np.abs(np.gradient(profile))
    lo = max(1, row - reach)
    peak = lo + int(np.argmax(slope[lo : min(height - 1, row + reach + 1)]))
    offset = 0.0
    curve = float(slope[peak - 1] - 2.0 * slope[peak] + slope[peak + 1]) if peak + 1 < height else 0.0
    if curve < 0.0:
        offset = 0.5 * float(slope[peak - 1] - slope[peak + 1]) / curve
    first = last = peak
    while first > 0 and slope[first - 1] >= slope[peak] / 2.0:
        first -= 1
    while last < height - 1 and slope[last + 1] >= slope[peak] / 2.0:
        last += 1
    return Skyline(row=float(peak) + offset, half_width_px=(last - first + 1) / 2.0, step_snr=snr)


@dataclass(frozen=True)
class SunPose:
    """A pose lined up from the Sun and a skyline: the nominal WCS, its pose estimate, the Sun's core and the skyline
    (in the upright frame), the Sun it was lined up to, the Sun's measured height above the skyline, and the glare
    spot measured in stored pixels (`spot`: its centre in stored pixels, its area and equivalent width as FWHM, its
    background-subtracted sum over the core and its level over the sky's noise as lower bounds, and SUN_CORE_OFFSET_DEG
    in nominal pixels as its centroid uncertainty)."""

    wcs: WCSModel
    estimate: PoseEstimate
    core: SunCore
    skyline: Skyline
    sun: TopocentricMoon
    measured_height_deg: float
    spot: MoonBlob


def _focal_px(up_width: int, up_height: int, focal_35mm_mm: float) -> float:
    """The focal length in pixels of a frame of this size through a lens of this 35 mm equivalent focal length."""
    return math.hypot(up_width, up_height) / 2.0 / (FRAME_35MM_DIAGONAL_MM / 2.0 / float(focal_35mm_mm))


def _max_core_diameter_px(focal_px: float, sun: TopocentricMoon) -> float:
    """The widest core taken to be the Sun's: MAX_DIAMETER_FACTOR times its disk through that lens."""
    return MAX_DIAMETER_FACTOR * 2.0 * focal_px * math.radians(sun.radius_deg)


def sun_core_spot(
    luma: np.ndarray, *, orientation: Mapping[str, Any] | None, focal_35mm_mm: float, sun: TopocentricMoon,
) -> MoonBlob | Refusal:
    """The Sun's core in a frame by the pose's own test (`find_sun_core` in the upright frame, no wider than the pose
    allows through the same lens), as the fit reads it (`_fit_spot`: its centre in stored pixels, its radius), or the
    test's refusal. For pixels the pose did not line up but the solve's detections were measured on: a clip's stack of
    its first frames, whose own core is where the glare sits in them (AUDIT's sun-core verdict, note a)."""
    turns, _ = _orientation(orientation)
    upright = np.rot90(np.asarray(luma, dtype=np.float64), -turns)
    up_h, up_w = upright.shape
    core = find_sun_core(upright, max_diameter_px=_max_core_diameter_px(_focal_px(up_w, up_h, focal_35mm_mm), sun))
    if isinstance(core, Refusal):
        return core
    return _fit_spot(core, turns, up_w, up_h)


def _fit_spot(core: SunCore, turns: int, up_width: int, up_height: int) -> MoonBlob:
    """The core as fit_horizon_pose reads a spot: its position in stored pixels and its size; the fit reads nothing
    else, and a clipped core has no honest flux, SNR or FWHM, so those are NaN."""
    x, y = to_stored(core.x_px, core.y_px, turns, up_width, up_height)
    nan = float("nan")
    return MoonBlob(
        x_px=x, y_px=y, area_px=core.area_px, equivalent_diameter_px=core.diameter_px,
        radius_px=core.diameter_px / 2.0, flux=nan, snr=nan, fwhm_px=nan, centroid_uncertainty_px=nan,
        peak_to_total_ratio=nan,
    )


def _glare_spot(
    upright: np.ndarray,
    core: SunCore,
    core_mask: np.ndarray,
    skyline: Skyline,
    turns: int,
    scale_deg_per_px: float,
) -> MoonBlob | Refusal:
    """The Sun's glare measured in the sky above the skyline (sky-6 (a)): the core's position in stored pixels, its
    background-subtracted sum above the sky's median as flux (a lower bound), its brightest level over the sky's
    row-detrended noise as SNR (a lower bound), its equivalent diameter as FWHM, and SUN_CORE_OFFSET_DEG at the
    nominal WCS scale as centroid uncertainty. Refused (no_sun) when the core does not stand above the sky's median."""
    up_h, up_w = upright.shape
    sky_end = max(1, int(math.floor(skyline.row - skyline.half_width_px)))
    sky_rows = upright[:sky_end]
    sky_mask = ~core_mask[:sky_end]
    background = float(np.median(sky_rows[sky_mask]))
    core_pixels = upright[core_mask]
    level = float(np.max(core_pixels)) - background
    flux = float(np.clip(core_pixels - background, 0.0, None).sum())
    if not (level > 0.0 and flux > 0.0):
        return Refusal("no_sun", NO_SUN)
    masked = np.where(sky_mask, sky_rows, np.nan)
    row_mad = 1.4826 * float(np.nanmedian(np.abs(masked - np.nanmedian(masked, axis=1, keepdims=True))))
    snr = level / _effective_sigma(row_mad, upright)
    x, y = to_stored(core.x_px, core.y_px, turns, up_w, up_h)
    return MoonBlob(
        x_px=x,
        y_px=y,
        area_px=core.area_px,
        equivalent_diameter_px=core.diameter_px,
        radius_px=core.diameter_px / 2.0,
        flux=flux,
        snr=snr,
        fwhm_px=core.diameter_px,
        centroid_uncertainty_px=SUN_CORE_OFFSET_DEG / scale_deg_per_px,
        peak_to_total_ratio=level / flux,
    )


def sun_horizon_pose(
    luma: np.ndarray,
    *,
    moment: str,
    lat: float,
    lon: float,
    orientation: Mapping[str, Any] | None,
    focal_35mm_mm: float | None,
) -> SunPose | Refusal:
    """The Sun plus horizon pose of one stored frame recorded at `moment` (REQ-SKY-4.3): a frame not mirrored, the
    Sun more than MIN_SUN_ALT_DEG up, a 35 mm focal length to check the clock with, the Sun's core and the day
    skyline in the upright frame, then the clock check (the Sun's height above the skyline through that lens against
    its altitude, within CLOCK_TOLERANCE_DEG), then nine fits through fit_horizon_pose (the skyline at each of
    SKYLINE_ALTS_DEG on its row less, at and plus its half width). Any refused fit refuses the pose. The beacon term is
    the Sun's radius, its ephemeris's stated uncertainty, SUN_CORE_OFFSET_DEG and the frame term."""
    turns, mirrored = _orientation(orientation)
    if mirrored:
        return Refusal("mirrored", SUN_MIRRORED)
    sun = topocentric_sun(moment, lat, lon)
    if not sun.alt_deg > MIN_SUN_ALT_DEG:
        return Refusal("sun_low", SUN_LOW)
    if focal_35mm_mm is None or not focal_35mm_mm > 0.0:
        return Refusal("no_focal", NO_FOCAL)
    luma = np.asarray(luma, dtype=np.float64)
    height, width = luma.shape
    upright = np.rot90(luma, -turns)
    up_h, up_w = upright.shape
    focal_px = _focal_px(up_w, up_h, focal_35mm_mm)
    found_core = _find_core_region(upright, max_diameter_px=_max_core_diameter_px(focal_px, sun))
    if isinstance(found_core, Refusal):
        return Refusal("sun_bloom", sun_bloom_reason(float(focal_35mm_mm))) if found_core.kind == "sun_bloom" else found_core
    core, core_mask = found_core
    skyline = find_day_skyline(upright, core)
    if isinstance(skyline, Refusal):
        return skyline
    cy = (up_h - 1) / 2.0
    measured = math.degrees(math.atan((cy - core.y_px) / focal_px)) - math.degrees(math.atan((cy - skyline.row) / focal_px))
    clock = clock_check(measured, sun.alt_deg, tolerance_deg=CLOCK_TOLERANCE_DEG)
    if clock is not None:
        return clock
    fit_spot = _fit_spot(core, turns, up_w, up_h)
    rows = (skyline.row - skyline.half_width_px, skyline.row, skyline.row + skyline.half_width_px)
    swept: list[WCSModel] = []
    fovs: list[float] = []
    nominal: WCSModel | None = None
    for skyline_alt in SKYLINE_ALTS_DEG:
        for row in rows:
            wcs = fit_horizon_pose(
                sun, fit_spot, width=width, height=height, skyline_alt_deg=skyline_alt, skyline_row=row, turns=turns,
                moment=moment, lat=lat, lon=lon,
            )
            if isinstance(wcs, Refusal):
                return wcs
            swept.append(wcs)
            fovs.append(fov_diagonal_deg(wcs.scale_deg_per_px, width, height))
            if skyline_alt == SKYLINE_ALTS_DEG[1] and row == skyline.row:
                nominal = wcs
    assert nominal is not None
    spot = _glare_spot(upright, core, core_mask, skyline, turns, nominal.scale_deg_per_px)
    if isinstance(spot, Refusal):
        return spot
    frame = pose_frame(sun, lat, lon, moment)
    centre_alt, centre_az = icrs_to_frame_altaz(frame, nominal.crval_ra_deg, nominal.crval_dec_deg)
    term = frame_term_arcsec(sun, swept, fovs, lat, lon, moment)
    nominal_fov = fov_diagonal_deg(nominal.scale_deg_per_px, width, height)
    bottom, middle, top = SKYLINE_ALTS_DEG
    estimate = PoseEstimate(
        method="sun_horizon",
        beacon="sun",
        center_alt_deg=centre_alt,
        center_az_deg=0.0 if centre_az >= 360.0 else centre_az,
        roll_deg=0.0,
        fov_diagonal_deg=nominal_fov,
        fov_source="Fitted from the Sun and the skyline, and checked against the file's 35 mm focal length.",
        # C23's four; the sweep: the skyline at bottom, middle and top altitudes, each on its row and its half width
        # either way
        assumptions=(
            f"The glare's centre is taken to be within {SUN_CORE_OFFSET_DEG:g} degrees of the Sun's.",
            f"The skyline is taken to be level and {bottom:g} to {top:g} degrees above the true horizon; the fit "
            f"tried it at {bottom:g}, {middle:g} and {top:g} degrees.",
            "The camera's clock is taken to be right: the Sun's height on the picture agreed with its height at the "
            f"recorded time, through the file's {focal_35mm_mm:g} mm lens.",
            "The lens is taken to be a plain one with no distortion, centred on the frame.",
        ),
        swept_wcs=tuple(swept),
        max_center_offset_deg=max(
            angular_separation(nominal, w) for w in swept
        ),
        beacon_centroid_uncertainty_deg=sun.radius_deg + sun.uncertainty_deg + SUN_CORE_OFFSET_DEG + term / 3600.0,
    )
    return SunPose(nominal, estimate, core, skyline, sun, measured, spot)


def angular_separation(a: WCSModel, b: WCSModel) -> float:
    """The angle between two WCS's centres, in degrees."""
    from services.science.catalog import angular_separation_deg  # noqa: PLC0415

    return angular_separation_deg(a.crval_ra_deg, a.crval_dec_deg, b.crval_ra_deg, b.crval_dec_deg)
