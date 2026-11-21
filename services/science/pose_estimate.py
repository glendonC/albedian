"""An approximate camera pose for a head-worn clip from a bright spot taken to be the Moon, the camera profile's field
of view and a level head (layer 2 of spec predicted-sky-for-every-upload, REQ-SKY-3; design component 4).

No star is matched and none is held out: the pose rests on one bright spot taken to be the Moon and on stated
assumptions, so it is never `calibrated` and never makes a catalog match. Nothing in one frame proves the spot is the
Moon: a lamp or another light of similar size would be taken the same way. One frame at a time:
1. By day, the Sun above USNO's sunrise and sunset altitude at the frame's moment, no pose is tried: the likeliest
   bright spot is the Sun.
2. The Moon test, by the contour at half the spot's peak height above the sky: the frame's brightest point after a
   light smoothing, its peak at least `MIN_PEAK_LUMA` and at least `MIN_PEAK_SNR` times the noise above the sky; its
   region, the 8-connected pixels around it above the half-peak level, of an equivalent diameter between
   `MIN_DIAMETER_FACTOR` times the Moon's predicted diameter at the profile's widest field of view and
   `MAX_DIAMETER_FACTOR` times it at the narrowest, and whole inside the frame; and no other spot in the frame that
   passes the same test on its own. Anything else is "not confirmed as the Moon".
3. The Moon as seen from the place (topocentric): the ephemeris record as it is, its parallax already applied there.
4. The level-head poses that see the Moon at the spot's centroid, in closed form (the verdict's finding 1), at each
   swept field of view (the profile's limits and middle) and roll (`SWEPT_ROLLS_DEG`): a level camera pitched to a0
   sees the Moon at tangent offsets (X right, Y up) when sin(a0) + Y cos(a0) = sin(Moon) sqrt(1 + X^2 + Y^2), so
   a0 + atan(Y) is asin(q) or 180 - asin(q), with q = sin(Moon) sqrt(1 + X^2 / (1 + Y^2)): none, one or two poses.
   Two at any swept value refuse the pose (which way the camera faced is not known); so does none (at one or more of
   the stated fields of view and rolls, no level head puts the Moon on the spot).
5. Each of the nine is then a TAN WCS at that fixed scale (parity -1, no distortion, crpix at the frame centre), its
   centre and rotation polished by least squares from the closed form so the Moon lands on the centroid and the
   horizon has that roll in the upright frame (the frame as displayed). A fit whose largest residual exceeds
   `MAX_FIT_RESIDUAL_PX`, that shows the sky upside down, or that leaves the closed form's pose refuses the pose.
6. The nominal WCS is the middle field of view at roll 0. The pose estimate keeps all nine, the largest angle between
   the nominal frame centre and a swept one, and the beacon term: the Moon's predicted radius (where the spot's light
   sits on the disk) plus the ephemeris record's own stated uncertainty of the Moon's position, read at run time,
   plus the frame term: what the one rotation the WCS draws catalog positions with leaves out across the frame.
Refraction is left out for the Moon and the stars alike (the ephemeris and the catalog transform are airless).
The two frames (docs/science/SKY_TRANSFORMS.md): the camera is level in the true horizon and sees each direction at
its apparent place, so the closed form takes the Moon's apparent altitude and azimuth (the record's, as they are) and
gives the camera's pointing in the apparent horizon exactly; that pointing is the centre the pose states. The one
approximation is the WCS: a single rotation for every catalog position, catalog.py `anchored_horizon_rotation` (the
rigid frame rotation turned so the Moon lands exactly where it is seen), through which the level line, the fit's start
and its check run. A drawn position then misses where it is seen by aberration and deflection that differ from the
Moon's, at most catalog.py `anchored_rotation_omission_arcsec` of the frame's farthest angle from the Moon (from the
field of view and where the Moon sits), and the beacon term carries it into every ring.
docs/science/PREDICTED_SKY.md describes the layer in plain words.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import math
from typing import Any

import numpy as np
from scipy import ndimage, optimize

from services.science.camera_profiles import CameraProfile
from services.science.catalog import (
    anchored_horizon_rotation,
    anchored_rotation_omission_arcsec,
    angular_separation_deg,
    frame_altaz_to_icrs,
    icrs_to_frame_altaz,
    require_place,
)
from services.science.detector import _effective_sigma
from services.science.ephemeris import AU_KM, compute_solar_system_ephemerides
from services.science.wcs_models import PoseEstimate, WCSModel

POSE_METHOD = "moon_camera_profile_level_head"
# The Sun's centre altitude at sunrise and sunset, from USNO's "Rise, Set, and Twilight Definitions"
# (aa.usno.navy.mil/faq/RST_defs): a geometric zenith distance of 90.8333 degrees, the centre 50 arcminutes below the
# horizon (16 for the Sun's radius and 34 for refraction). Above it is day, the limit predicted_sky.py's sky phase
# sentence uses too; by day no bright spot is taken to be the Moon, since the likeliest one is the Sun
SUNRISE_SUNSET_ALT_DEG = -50.0 / 60.0
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
# The centroid is measured over the region grown by two pixels, so the light just outside the half-peak contour (the
# disk's soft edge, an overexposed Moon's bloom) counts (design). A region that grown so reaches the frame's edge may
# be cut by it, its centroid moved off its centre: the spot is refused (the lead's decision of 2026-11-11)
BLOOM_DILATION_PX = 2
EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)
# The predicted Moon must be more than 5 degrees up (REQ-SKY-3.1)
MIN_MOON_ALT_DEG = 5.0
# A level head within 10 degrees of roll either way [proposed: the lead's decision of 2026-11-10, open to the owner]
ROLL_LIMIT_DEG = 10.0
SWEPT_ROLLS_DEG = (-ROLL_LIMIT_DEG, 0.0, ROLL_LIMIT_DEG)
# The level constraint's two points: at the frame centre's altitude, 10 degrees either side of it in azimuth (design)
LEVEL_AZ_OFFSET_DEG = 10.0
# Three residuals fit three unknowns exactly, from a start that already meets them (the closed form), so a fit that
# cannot reach half a pixel did not converge (design)
MAX_FIT_RESIDUAL_PX = 0.5
# The polished fit stays on the closed form's pose: within 0.001 degree of its centre, about a seventieth of a pixel
# at the profile's widest field of view [proposed: polishing moves it by far less; more means the fit left the pose]
SAME_POSE_DEG = 1e-3

_NOT_LINED_UP = "The sky is not lined up over the picture: "
NO_PROFILE = _NOT_LINED_UP + "the camera has no head-worn camera profile, so its field of view is not known."
MIRRORED = (
    _NOT_LINED_UP + "the frame is stored mirrored, and the head-worn pose needs a frame stored as the camera saw it."
)
NO_FRAMES = _NOT_LINED_UP + "no frame could be read to look for the Moon."
MOON_NOT_UP = _NOT_LINED_UP + f"the Moon was not more than {MIN_MOON_ALT_DEG:g} degrees above the horizon."
DAYTIME = (
    _NOT_LINED_UP + "the Sun was above the horizon at the frame's moment, and by day a bright spot may be the Sun."
)
# The kinds of refusal reached once a spot passed the Moon test: these say most of a frame that gave no pose
POSE_REFUSALS = ("two_poses", "ranges", "fit")


def _spot(x_px: float, y_px: float, where: str) -> str:
    return f"({x_px:.0f}, {y_px:.0f}) px {where}"


# C32's two sentences (.lead/uiux/UIUX-moonposewords-READY.md, decided 2026-11-21), C21's shape: the reason in plain
# words with no number but a count of frames, which the warnings list shows, then the spot, the rows, the degrees and
# the thresholds in a second sentence kept in the record (C5's rule)
def two_poses_reason(x_px: float, y_px: float, where: str) -> str:
    return (
        _NOT_LINED_UP + "the bright spot taken to be the Moon fits two different directions of view, so which way the "
        f"camera faced is not known. The spot is at {_spot(x_px, y_px, where)}; both directions keep a level head inside "
        "the stated field of view and roll."
    )


def ranges_reason(x_px: float, y_px: float, where: str, profile: CameraProfile) -> str:
    low, high = profile.fov_diagonal_deg_range
    return (
        # C32 as amended (the lead, 13:20 on 21 November): true whether one swept value or all hold no level pose
        _NOT_LINED_UP + "at one or more of the fields of view and head rolls the camera is stated to have, no level head "
        f"puts the Moon on the bright spot. The spot is at {_spot(x_px, y_px, where)}; the field of view was {low:g} to "
        f"{high:g} degrees across the diagonal and the roll within {ROLL_LIMIT_DEG:g} degrees either way."
    )


def level_ranges_reason(x_px: float, y_px: float, where: str, fov_diagonal_deg: float, roll_deg: float) -> str:
    """No level head at one field of view and roll (`fit_level_pose`'s own refusal)."""
    return (
        _NOT_LINED_UP + "no level head puts the Moon on the bright spot at this field of view and roll. The spot is at "
        f"{_spot(x_px, y_px, where)}; the field of view was {fov_diagonal_deg:g} degrees across the diagonal and the "
        f"roll {roll_deg:g} degrees."
    )


def fit_reason(x_px: float, y_px: float, where: str) -> str:
    return (
        _NOT_LINED_UP + "the fit could not put the Moon on the bright spot with a level head. The spot is at "
        f"{_spot(x_px, y_px, where)}; the fit allows {MAX_FIT_RESIDUAL_PX:g} px."
    )


def not_confirmed_reason(where: str) -> str:
    return (
        _NOT_LINED_UP + f"what was seen {where} was not confirmed as the Moon. The Moon test needs a spot whose peak is "
        f"at {MIN_PEAK_LUMA:g} of 255 or more and at least {MIN_PEAK_SNR:g} times the noise above the sky, that is whole "
        f"inside the frame, that measures at half its peak between {MIN_DIAMETER_FACTOR:g} times the Moon's predicted "
        f"diameter at the widest field of view and {MAX_DIAMETER_FACTOR:g} times it at the narrowest, and that is the "
        "only spot in the frame passing this test."
    )


@dataclass(frozen=True)
class MoonBlob:
    """The brightest spot that passed the Moon test, taken to be the Moon, measured in the frame's pixels: its
    flux-weighted centroid, the area of its region above half its peak and that region's equivalent diameter, the
    radius of the region its centroid was measured over (grown by `BLOOM_DILATION_PX`), and its flux, SNR and FWHM.
    Flux and SNR are lower bounds where its brightest pixels are clipped."""

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
    """Why no pose was found: `kind` is one of no_profile, mirrored, no_frames, moon_not_up, daytime, not_moon,
    two_poses, ranges or fit, or for the Moon plus horizon pose no_skyline, fov or size, and `reason` says it in plain
    words, with no capture time, place or file name."""

    kind: str
    reason: str
    blob: MoonBlob | None = None
    frame_time_sec: float | None = None


@dataclass(frozen=True)
class TopocentricMoon:
    """The Moon as seen from the place at the moment: the ephemeris record's topocentric position, apparent altitude
    and azimuth, and distance as it states them, the predicted angular radius, and the record's own stated uncertainty
    of the Moon's position."""

    ra_deg: float
    dec_deg: float
    alt_deg: float
    az_deg: float
    distance_km: float
    radius_deg: float
    uncertainty_deg: float


@dataclass(frozen=True)
class PoseResult:
    """A pose lined up from one frame's bright spot taken to be the Moon: the nominal WCS, the pose estimate with its
    sweep, the spot (in stored pixels), the Moon it was lined up to, the frame's time and moment, and either the
    camera profile it assumed (head-worn) or the skyline it was lined up to (Moon plus horizon)."""

    wcs: WCSModel
    pose_estimate: PoseEstimate
    blob: MoonBlob
    moon: TopocentricMoon
    frame_time_sec: float
    moment: str
    profile: CameraProfile | None
    skyline: Skyline | None = None


@dataclass(frozen=True)
class Skyline:
    """The skyline the Moon plus horizon pose lines up to, in the upright frame: the row where the frame turns
    brighter below, the half width of that rise (its measured uncertainty, in rows), and the rise in noise units."""

    row: float
    half_width_px: float
    step_snr: float


def _not_confirmed(detail: str) -> Refusal:
    return Refusal("not_moon", f"The frame's bright spots were not confirmed as the Moon: {detail}.")


def find_moon_blob(
    luma: np.ndarray, *, min_diameter_px: float, max_diameter_px: float, bottom: str = "the edge of the frame"
) -> MoonBlob | Refusal:
    """The Moon test on one gray frame (0 to 255), by the contour at half a spot's peak height above the sky; when
    the frame is the sky above a skyline, `bottom` names what its lower edge is ("the skyline").

    A spot is judged at a point: its peak is that pixel's own code, which must be at least MIN_PEAK_LUMA and at least
    MIN_PEAK_SNR times the noise above the sky (the frame's median, and 1.4826 times its median absolute deviation
    floored at SKY_NOISE_FLOOR, as horizon_solver.py measures them); its region is the 8-connected pixels about it
    above the sky plus HALF_PEAK of the peak's height, whose equivalent diameter must lie inside the limits.
    The candidate is the brightest point of the frame smoothed by a Gaussian of PEAK_SMOOTHING_SIGMA_PX; its region,
    grown by BLOOM_DILATION_PX, must also stay off the frame's edge. Every other spot (each connected group of pixels
    at MIN_PEAK_LUMA or more outside the candidate's region, judged at its own brightest point after smoothing) is
    judged by the same test on its own, the edge aside: one that passes refuses the frame, whatever its area beside
    the candidate's. Then the candidate is measured: the background-subtracted, flux-weighted centroid over its region
    grown by BLOOM_DILATION_PX, its FWHM from second moments. Else the refusal, saying which condition failed."""
    luma = np.asarray(luma, dtype=np.float64)
    height_px, width_px = luma.shape
    background = float(np.median(luma))
    mad_sigma = 1.4826 * float(np.median(np.abs(luma - background)))
    noise = max(mad_sigma, SKY_NOISE_FLOOR)
    smoothed = ndimage.gaussian_filter(luma, sigma=PEAK_SMOOTHING_SIGMA_PX)
    # The regions above one level, their areas and bounding boxes, labelled once per level
    by_level: dict[float, tuple[np.ndarray, np.ndarray, list]] = {}

    def regions_above(peak: float) -> tuple[np.ndarray, np.ndarray, list]:
        level = background + HALF_PEAK * (peak - background)
        if level not in by_level:
            labels, _ = ndimage.label(luma > level, structure=EIGHT_CONNECTED)
            by_level[level] = (labels, np.bincount(labels.ravel()), ndimage.find_objects(labels))
        return by_level[level]

    def judged(y: int, x: int, *, whole: bool) -> str | None:
        """Why the spot at (x, y) fails the Moon test on its own, or None when it passes."""
        peak = float(luma[y, x])
        if not peak >= MIN_PEAK_LUMA:
            return f"the brightest spot peaks at {peak:g} of 255, under {MIN_PEAK_LUMA:g}"
        peak_snr = (peak - background) / noise
        if not peak_snr >= MIN_PEAK_SNR:
            return f"the brightest spot peaks {peak_snr:.1f} times the noise above the sky, under {MIN_PEAK_SNR:g}"
        labels, areas, boxes = regions_above(peak)
        label = int(labels[y, x])
        diameter = 2.0 * math.sqrt(float(areas[label]) / math.pi)
        if not diameter <= max_diameter_px:
            return (
                f"the brightest spot is {diameter:.1f} px across at half its peak, over the {max_diameter_px:.1f} px "
                "the Moon allows"
            )
        if whole:
            rows, cols = boxes[label - 1]
            if (
                rows.start <= BLOOM_DILATION_PX
                or cols.start <= BLOOM_DILATION_PX
                or cols.stop >= width_px - BLOOM_DILATION_PX
            ):
                return "the brightest spot is cut by the edge of the frame"
            if rows.stop >= height_px - BLOOM_DILATION_PX:
                return f"the brightest spot is cut by {bottom}"
        if not diameter >= min_diameter_px:
            return (
                f"the brightest spot is {diameter:.1f} px across at half its peak, under the {min_diameter_px:.1f} px "
                "the Moon needs"
            )
        return None

    peak_y, peak_x = (int(v) for v in np.unravel_index(int(np.argmax(smoothed)), smoothed.shape))
    failed = judged(peak_y, peak_x, whole=True)
    if failed is not None:
        return _not_confirmed(failed)
    labels, _, boxes = regions_above(float(luma[peak_y, peak_x]))
    label = int(labels[peak_y, peak_x])

    # Every other spot that could pass on its own has a pixel at MIN_PEAK_LUMA or more outside the candidate's region
    candidate = labels == label
    others, count = ndimage.label((luma >= MIN_PEAK_LUMA) & ~candidate, structure=EIGHT_CONNECTED)
    if count:
        seen: set[tuple[float, int]] = set()
        for y, x in ndimage.maximum_position(smoothed, others, index=np.arange(1, count + 1)):
            y, x = int(y), int(x)
            rival_labels, _, _ = regions_above(float(luma[y, x]))
            rival = int(rival_labels[y, x])
            # A region that holds the candidate's own peak is the candidate seen at a lower level, not another spot
            key = (background + HALF_PEAK * (float(luma[y, x]) - background), rival)
            if rival_labels[peak_y, peak_x] == rival or key in seen:
                continue
            seen.add(key)
            if judged(y, x, whole=False) is None:
                return _not_confirmed("more than one spot passes the Moon test on its own")

    # Measured in a window around the region, grown by the bloom margin: the same numbers as over the whole frame
    rows, cols = boxes[label - 1]
    pad = BLOOM_DILATION_PX + 1
    y0, y1 = max(0, rows.start - pad), min(height_px, rows.stop + pad)
    x0, x1 = max(0, cols.start - pad), min(width_px, cols.stop + pad)
    region = labels[y0:y1, x0:x1] == label
    grown = ndimage.binary_dilation(region, structure=EIGHT_CONNECTED, iterations=BLOOM_DILATION_PX)
    height = float(luma[peak_y, peak_x]) - background
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
    # A spot of no width is no beacon (the lead's decision of 2026-11-11): with its light in one pixel there is no
    # width to measure, and a centroid uncertainty that is zero or no finite number is no measurement, which no
    # detection record takes. The count is of pixels, since rounding alone can give one pixel a width of 1e-15 px
    if np.count_nonzero(weights) < 2:
        return _not_confirmed("the brightest spot has no width, all of its light in one pixel")
    if not 0.0 < fwhm_px / (2.355 * snr) < math.inf:
        return _not_confirmed("the brightest spot's width came out as zero or as no finite number")
    area = int(region.sum())
    return MoonBlob(
        x_px=x_px,
        y_px=y_px,
        area_px=area,
        equivalent_diameter_px=2.0 * math.sqrt(area / math.pi),
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
    """The Moon seen from (`lat`, `lon`) at `moment`: the ephemeris record as it is, asked for topocentric, so its
    position, altitude, azimuth and distance are already from the place (its parallax applied by ERFA's chain) and
    nothing is shifted here. Its radius is asin(MOON_RADIUS_KM / distance) at the record's distance from the place;
    its uncertainty is the one the record states for it at the moment."""
    lat, lon = require_place(lat, lon)
    (moon,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",), topocentric=True)
    distance_km = moon.distance_au * AU_KM
    return TopocentricMoon(
        ra_deg=moon.ra_deg,
        dec_deg=moon.dec_deg,
        alt_deg=moon.alt_deg,
        az_deg=moon.az_deg,
        distance_km=distance_km,
        radius_deg=math.degrees(math.asin(MOON_RADIUS_KM / distance_km)),
        uncertainty_deg=moon.uncertainty_arcsec / 3600.0,
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
    return moon.alt_deg > MIN_MOON_ALT_DEG


def moon_is_up(moment: str, lat: float, lon: float) -> bool:
    """Whether the predicted Moon (the ephemeris altitude, as its predicted finding carries it) is more than
    MIN_MOON_ALT_DEG above the horizon at `moment`."""
    lat, lon = require_place(lat, lon)
    return _up(topocentric_moon(moment, lat, lon))


def sun_is_up(moment: str, lat: float, lon: float) -> bool:
    """Whether the Sun's centre is above SUNRISE_SUNSET_ALT_DEG at `moment` from the place: day, by USNO's limit."""
    lat, lon = require_place(lat, lon)
    (sun,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))
    return sun.alt_deg > SUNRISE_SUNSET_ALT_DEG


def upright_vector(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """A stored-pixel vector (x right, y down) as the upright frame shows it after `turns` clockwise quarter turns
    (packages/contracts/src/media.ts DisplayOrientationSchema)."""
    for _ in range(turns):
        dx, dy = -dy, dx
    return dx, dy


def level_centres(
    moon_alt_deg: float,
    moon_az_deg: float,
    x_px: float,
    y_px: float,
    *,
    width: int,
    height: int,
    fov_diagonal_deg: float,
    roll_deg: float,
    turns: int,
) -> tuple[tuple[float, float], ...]:
    """Every level-head pointing, as the frame centre's (altitude, azimuth), lowest first, that sees the direction
    (`moon_alt_deg`, `moon_az_deg`) at the stored pixel at this diagonal field of view and roll, in closed form.

    The pixel's upright offset from the frame centre, the roll undone (a positive roll turns the picture clockwise)
    and times the scale in radians, gives the Moon's tangent offsets (X right, Y up) in a level camera. A level camera
    pitched to a0 sees it there when sin(a0) + Y cos(a0) = sin(Moon) sqrt(1 + X^2 + Y^2), so a0 + atan(Y) is asin(q)
    or 180 degrees - asin(q), q = sin(Moon) sqrt(1 + X^2 / (1 + Y^2)); each a0 strictly between -90 and 90 degrees (the
    camera's top toward the zenith) is a pose, with the azimuth that puts the Moon at X. None when q > 1, and none for
    a Moon at the zenith, which a level camera sees only on its middle column, at any azimuth."""
    ux, uy = upright_vector(x_px - width / 2.0, y_px - height / 2.0, turns)
    rho = math.radians(roll_deg)
    dx, dy = ux * math.cos(rho) + uy * math.sin(rho), -ux * math.sin(rho) + uy * math.cos(rho)
    s = math.radians(scale_deg_per_px(fov_diagonal_deg, width, height))
    big_x, big_y = dx * s, -dy * s
    moon = math.radians(moon_alt_deg)
    if math.cos(moon) < 1e-12:
        return ()
    norm = math.sqrt(1.0 + big_x * big_x + big_y * big_y)
    q = math.sin(moon) * norm / math.sqrt(1.0 + big_y * big_y)
    if q > 1.0:
        return ()
    t, phi = math.asin(q), math.atan(big_y)
    centres = []
    for a0 in sorted({t - phi, math.pi - t - phi}):
        if not -math.pi / 2.0 < a0 < math.pi / 2.0:
            continue
        sin_d = big_x / (norm * math.cos(moon))
        cos_d = (1.0 / norm - math.sin(moon) * math.sin(a0)) / (math.cos(moon) * math.cos(a0))
        centres.append((math.degrees(a0), (moon_az_deg - math.degrees(math.atan2(sin_d, cos_d))) % 360.0))
    return tuple(centres)


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

    The pointing is the one level pose `level_centres` finds for the Moon's apparent altitude and azimuth, in the
    apparent horizon; two or none refuse (kinds two_poses and ranges). Unknowns: crval's RA and Dec and the rotation,
    started at that pose. Residuals: the Moon's projection minus the pixel (two), and the level constraint (one): two
    points at the frame centre's altitude, `LEVEL_AZ_OFFSET_DEG` either side in azimuth, mapped to the upright frame,
    have a row difference (right minus left) equal to tan(roll) times their column difference, so a positive roll puts
    the horizon's right side lower. The level line, the start and the check turn ICRS through `pose_frame` (the rigid
    frame rotation anchored on the Moon), which takes the Moon's ICRS direction to its apparent one, so the WCS is the
    camera seen through that one rotation. Levenberg-Marquardt polishes the start; the WCS returned is refused when its
    largest residual exceeds MAX_FIT_RESIDUAL_PX, its upright frame shows the sky upside down, or its centre left the
    pose by more than SAME_POSE_DEG."""
    lat, lon = require_place(lat, lon)
    turns, mirrored = _orientation(orientation)
    if mirrored:
        return Refusal("mirrored", MIRRORED)
    centres = level_centres(
        moon.alt_deg, moon.az_deg, x_px, y_px, width=width, height=height, fov_diagonal_deg=fov_diagonal_deg,
        roll_deg=roll_deg, turns=turns,
    )
    if len(centres) == 2:
        return Refusal("two_poses", two_poses_reason(x_px, y_px, "in the frame"))
    if not centres:
        return Refusal("ranges", level_ranges_reason(x_px, y_px, "in the frame", fov_diagonal_deg, roll_deg))
    ((centre_alt, centre_az),) = centres
    frame = pose_frame(moon, lat, lon, moment)
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
        alt, az = icrs_to_frame_altaz(frame, wcs.crval_ra_deg, wcs.crval_dec_deg)
        lx, ly = wcs.world_to_pixel(*frame_altaz_to_icrs(frame, alt, az - LEVEL_AZ_OFFSET_DEG))
        rx, ry = wcs.world_to_pixel(*frame_altaz_to_icrs(frame, alt, az + LEVEL_AZ_OFFSET_DEG))
        return upright_vector(rx - lx, ry - ly, turns)

    def residuals_of(wcs: WCSModel) -> np.ndarray:
        mx, my = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
        dx, dy = level_line(wcs)
        return np.array([mx - x_px, my - y_px, dy - tan_roll * dx], dtype=np.float64)

    def residuals(params: np.ndarray) -> np.ndarray:
        res = residuals_of(wcs_for(params))
        return res if np.all(np.isfinite(res)) else np.full(3, 1e4, dtype=np.float64)

    # The closed form's centre, with the rotation that gives the horizon the roll there
    start_ra, start_dec = frame_altaz_to_icrs(frame, centre_alt, centre_az)
    dx0, dy0 = level_line(wcs_for(np.array([start_ra, start_dec, 0.0])))
    start = np.array([start_ra, start_dec, math.degrees(math.atan2(dy0, dx0)) - roll_deg])
    fit = optimize.least_squares(residuals, start, method="lm", xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=400)
    wcs = _normalized(wcs_for(fit.x))
    fitted_alt, fitted_az = icrs_to_frame_altaz(frame, wcs.crval_ra_deg, wcs.crval_dec_deg)
    # Measured on the WCS that is returned: its constraints met, the sky the right way up, and still the closed form's
    # pose
    if (
        _fit_accepted(residuals_of(wcs))
        and level_line(wcs)[0] > 0.0
        and angular_separation_deg(fitted_az, fitted_alt, centre_az, centre_alt) <= SAME_POSE_DEG
    ):
        return wcs
    return Refusal("fit", fit_reason(x_px, y_px, "in the frame"))


def pose_frame(moon: TopocentricMoon, lat: float, lon: float, moment: str) -> np.ndarray:
    """The one rotation the pose's WCS draws ICRS with: the rigid frame rotation anchored on the Moon (catalog.py
    `anchored_horizon_rotation`), which takes the Moon's ICRS direction to where it is seen."""
    lat, lon = require_place(lat, lon)
    return anchored_horizon_rotation(moon.ra_deg, moon.dec_deg, lat, lon, moment)


def frame_term_arcsec(
    moon: TopocentricMoon, swept: Sequence[WCSModel], fovs_deg: Sequence[float], lat: float, lon: float, moment: str
) -> float:
    """The frame term: at most how far the WCS's one rotation puts a direction in the frame from where it is seen,
    catalog.py `anchored_rotation_omission_arcsec` over the frames of the sweep, each its crval and half its
    diagonal field of view (the farthest a frame point lies from the centre)."""
    lat, lon = require_place(lat, lon)
    fields = [(w.crval_ra_deg, w.crval_dec_deg, fov / 2.0) for w, fov in zip(swept, fovs_deg, strict=True)]
    return anchored_rotation_omission_arcsec(moon.ra_deg, moon.dec_deg, fields, lat, lon, moment)


def _assumptions(profile: CameraProfile, moon: TopocentricMoon, term_arcsec: float) -> tuple[str, ...]:
    low, high = profile.fov_diagonal_deg_range
    return (
        f"The camera's diagonal field of view is {low:g} to {high:g} degrees, from its camera profile "
        f"({profile.fov_label}); the sweep takes {low:g}, {profile.fov_diagonal_deg_mid:g} and {high:g} degrees.",
        "The camera was not zoomed: the camera profile's field of view is for no zoom, the pose reads nothing in the "
        "file that would show a zoom, and a zoomed frame sees a narrower field than the sweep takes.",
        f"The head was level: the horizon's roll in the upright frame is within {ROLL_LIMIT_DEG:g} degrees either way; "
        f"the sweep takes {SWEPT_ROLLS_DEG[0]:g}, {SWEPT_ROLLS_DEG[1]:g} and +{SWEPT_ROLLS_DEG[2]:g} degrees, and a "
        "second level pose or none at any of them refuses the pose.",
        "The lens is rectilinear (gnomonic), with no distortion, and centred on the frame.",
        "The bright spot is taken to be the Moon: nothing in one frame proves it, and a lamp or another light of "
        "similar size, as bright and the only spot that passes the Moon test, would be taken the same way.",
        "The spot's light centroid is taken to lie on the Moon's disk, and the Moon where its ephemeris puts it: the "
        f"beacon term, the Moon's predicted radius of {moon.radius_deg:.2f} degrees plus the ephemeris's stated "
        f"uncertainty of {moon.uncertainty_deg * 3600.0:,.0f} arcsec plus {term_arcsec:,.0f} arcsec for drawing every "
        "position through one rotation, is taken to cover overexposure, which spreads the light, a partial phase, "
        "which moves it toward the lit edge, the ephemeris's own error, and the aberration and light deflection that "
        "differ across the frame from the Moon's, which one rotation cannot follow.",
        "The Moon is placed where it is seen from the place (topocentric): the ephemeris record as it is, its parallax "
        "already applied there; refraction is left out for the Moon and the stars alike, which is taken to cancel to "
        "first order near the Moon.",
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
    """The pose from one frame recorded at `moment`, `frame_time_sec` into the recording: the Moon up and the Sun
    down at that moment, the Moon test on this frame alone, exactly one level pose at each of the nine swept values,
    then the nine fits; any refused fit refuses the pose, since its uncertainty could not be bounded."""
    lat, lon = require_place(lat, lon)
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    moon = topocentric_moon(moment, lat, lon)
    if not _up(moon):
        return Refusal("moon_not_up", MOON_NOT_UP)
    if sun_is_up(moment, lat, lon):
        return Refusal("daytime", DAYTIME, frame_time_sec=frame_time_sec)
    height, width = np.shape(luma)
    min_d, max_d = moon_diameter_limits_px(moon, profile.fov_diagonal_deg_range, width, height)
    blob = find_moon_blob(luma, min_diameter_px=min_d, max_diameter_px=max_d)
    if isinstance(blob, Refusal):
        return replace(blob, frame_time_sec=frame_time_sec)
    turns = _orientation(orientation)[0]
    low, high = profile.fov_diagonal_deg_range
    middle = profile.fov_diagonal_deg_mid
    values = [(fov, roll) for fov in (low, middle, high) for roll in SWEPT_ROLLS_DEG]
    counts = [
        len(level_centres(moon.alt_deg, moon.az_deg, blob.x_px, blob.y_px, width=width, height=height,
                          fov_diagonal_deg=fov, roll_deg=roll, turns=turns))
        for fov, roll in values
    ]
    if any(count >= 2 for count in counts):
        return Refusal("two_poses", two_poses_reason(blob.x_px, blob.y_px, "in the frame"), blob, frame_time_sec)
    if any(count == 0 for count in counts):
        return Refusal("ranges", ranges_reason(blob.x_px, blob.y_px, "in the frame", profile), blob, frame_time_sec)
    swept: list[WCSModel] = []
    nominal: WCSModel | None = None
    for fov, roll in values:
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
    # Where the camera's centre is seen: its pointing in the apparent horizon, which the frame rotation gives for
    # the nominal crval (the closed form's centre, within SAME_POSE_DEG)
    frame = pose_frame(moon, lat, lon, moment)
    centre_alt, centre_az = icrs_to_frame_altaz(frame, nominal.crval_ra_deg, nominal.crval_dec_deg)
    term_arcsec = frame_term_arcsec(moon, swept, [fov for fov, _ in values], lat, lon, moment)
    estimate = PoseEstimate(
        method=POSE_METHOD,
        beacon="moon",
        center_alt_deg=centre_alt,
        center_az_deg=centre_az,
        roll_deg=0.0,
        fov_diagonal_deg=middle,
        fov_source=f"The camera profile for {profile.model}, {profile.fov_label}: {profile.fov_source}",
        assumptions=_assumptions(profile, moon, term_arcsec),
        swept_wcs=tuple(swept),
        max_center_offset_deg=max(
            angular_separation_deg(nominal.crval_ra_deg, nominal.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg)
            for w in swept
        ),
        beacon_centroid_uncertainty_deg=moon.radius_deg + moon.uncertainty_deg + term_arcsec / 3600.0,
    )
    return PoseResult(nominal, estimate, blob, moon, frame_time_sec, moment, profile)


def _where(frame_time_sec: float | None, is_clip: bool) -> str:
    return f"in the frame {frame_time_sec:.2f} s into the clip" if is_clip else "in the photo"


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
    refusal that says most: a spot that passed the Moon test but gave no pose (two level poses, the ranges not all
    fitted, the fit refused), else not confirmed as the Moon (counting the frames searched), else the Moon not up in
    every frame, else by day."""
    lat, lon = require_place(lat, lon)
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
    posed = next((r for r in refusals if r.kind in POSE_REFUSALS and r.blob is not None), None)
    if posed is not None:
        x, y, where = posed.blob.x_px, posed.blob.y_px, _where(posed.frame_time_sec, is_clip)
        reason = {
            "two_poses": two_poses_reason(x, y, where),
            "ranges": ranges_reason(x, y, where, profile),
            "fit": fit_reason(x, y, where),
        }[posed.kind]
        return Refusal(posed.kind, reason, posed.blob, posed.frame_time_sec)
    searched = sum(1 for r in refusals if r.kind == "not_moon")
    if not searched:
        if all(r.kind == "moon_not_up" for r in refusals):
            return Refusal("moon_not_up", MOON_NOT_UP)
        return Refusal("daytime", DAYTIME)
    if not is_clip:
        where = "in the photo"
    else:
        where = "in the frame searched" if searched == 1 else f"in the {searched} frames searched"
    return Refusal("not_moon", not_confirmed_reason(where))


# --- The Moon plus horizon pose (spec REQ-SKY-4.1, 4.2, 4.4; design component 5) ---------------------------------
# For an upload or a laptop clip whose camera has no head-worn profile, or whose head-worn pose found none: the frame's
# bright spot above a skyline taken to be the Moon, the skyline taken to be level ground, the field of view fitted
# from the two. The Moon test is sky-3's, on the sky above the skyline; the algebra takes the Moon's apparent altitude
# and azimuth and runs through the same one frame rotation as the head-worn pose (`pose_frame`), and the beacon term is
# built the same way, its frame term over the sweep's own frames.

HORIZON_POSE_METHOD = "moon_horizon"
# The skyline's true altitude, swept: level ground from the true horizon to 3 degrees above it [proposed in the
# spec, REQ-SKY-4.2], the nominal at the middle
SKYLINE_ALTS_DEG = (0.0, 1.5, 3.0)
# Where the skyline is looked for, and how: each row's 85th percentile smoothed by a Gaussian of 12 rows, its
# steepest rise between 0.38 and 0.72 of the upright frame's height (the horizon solver's own finder, kept)
# [proposed: a skyline in the middle of the picture, the sky darker than the ground; sky-6 takes either sign]
SKYLINE_BAND = (0.38, 0.72)
SKYLINE_ROW_PERCENTILE = 85.0
SKYLINE_SMOOTHING_ROWS = 12.0
# The rise measured from two smoothing widths above the row to two below, against the sky's noise above it
# [proposed: the Moon test's own 10 noise units, so a sky's own noise is never a skyline]
SKYLINE_STEP_REACH = 2.0
SKYLINE_MIN_STEP_SNR = 10.0
# The sky's noise is taken as at least one code: a dark 8-bit sky can hold most of its pixels on one code, so its
# median absolute deviation reads 0, and a rise of a fraction of a code would pass [proposed: a code is the finest step
# an 8-bit frame records]
SKYLINE_NOISE_FLOOR = 1.0
# The field of view a spot and a skyline may fit to, across the diagonal [proposed: wider than every camera the phone
# benchmark lists, 82 to 123 degrees (research/benchmarks/phone_photos/cameras.py), and down to about four times the
# main camera's zoom; a fit outside it is taken as a wrong spot or a wrong skyline]
HORIZON_FOV_DIAGONAL_RANGE_DEG = (20.0, 130.0)
# The sky above the skyline is searched down to the skyline's row less its half width, so a spot touching that
# edge is cut by the skyline; a band thinner than this many rows is no sky to search
MIN_SKY_ROWS = 16

# C21's two sentences (.lead/uiux/UIUX-skylinewords-READY.md, decided 2026-11-19 13:20): the reason in plain words,
# which the warnings list shows, then the band and the step looked for, kept in the record (C5's rule)
NO_SKYLINE = (
    _NOT_LINED_UP + "no skyline was found to line the Moon up with. "
    f"A skyline here is a row {SKYLINE_BAND[0] * 100:.0f} to {SKYLINE_BAND[1] * 100:.0f} percent of the way down below "
    f"which the picture turns brighter by at least {SKYLINE_MIN_STEP_SNR:g} times the sky's noise."
)
HORIZON_REFUSALS = ("fit", "fov", "size")


# C32's two sentences for the skyline's refusals, as for the level head's above
def horizon_fit_reason(x_px: float, y_px: float, where: str, row: float) -> str:
    return (
        _NOT_LINED_UP + "the fit found no camera that puts the Moon on the bright spot with the skyline level. The spot "
        f"is at {_spot(x_px, y_px, where)} and the skyline at row {row:.0f}; the fit allows {MAX_FIT_RESIDUAL_PX:g} px."
    )


def horizon_start_reason(x_px: float, y_px: float, where: str, row: float) -> str:
    return (
        _NOT_LINED_UP + "the bright spot is not above the skyline, or the Moon is not above the skyline's altitude, so no "
        f"fit could start. The spot is at {_spot(x_px, y_px, where)} and the skyline at row {row:.0f}."
    )


def horizon_upside_down_reason(x_px: float, y_px: float, where: str, row: float) -> str:
    return (
        _NOT_LINED_UP + "the only camera the fit found shows the sky upside down. The spot is at "
        f"{_spot(x_px, y_px, where)} and the skyline at row {row:.0f}."
    )


def horizon_fov_reason(x_px: float, y_px: float, where: str, row: float, fov_deg: float) -> str:
    low, high = HORIZON_FOV_DIAGONAL_RANGE_DEG
    return (
        _NOT_LINED_UP + "with the bright spot taken to be the Moon and the skyline taken to be level ground, the field "
        f"of view they give is outside what a camera is taken to have. The spot is at {_spot(x_px, y_px, where)} and the "
        f"skyline at row {row:.0f}; they give {fov_deg:.0f} degrees across the diagonal, outside {low:g} to {high:g}."
    )


def horizon_size_reason(x_px: float, y_px: float, where: str, diameter_px: float, ratio: float) -> str:
    return (
        _NOT_LINED_UP + "the bright spot is not the Moon's size at the field of view the skyline gives. It measures "
        f"{diameter_px:.1f} px across at half its peak, {ratio:.1f} times the Moon's predicted diameter, outside "
        f"{MIN_DIAMETER_FACTOR:g} to {MAX_DIAMETER_FACTOR:g} times; it is at {_spot(x_px, y_px, where)}."
    )


def not_confirmed_above_skyline_reason(where: str) -> str:
    low, high = HORIZON_FOV_DIAGONAL_RANGE_DEG
    return (
        _NOT_LINED_UP + f"what was seen above the skyline {where} was not confirmed as the Moon. The Moon test needs a "
        f"spot there whose peak is at {MIN_PEAK_LUMA:g} of 255 or more and at least {MIN_PEAK_SNR:g} times the noise "
        "above the sky, that is whole inside the frame and clear of the skyline, that measures at half its peak between "
        f"{MIN_DIAMETER_FACTOR:g} times the Moon's predicted diameter at {high:g} degrees across the diagonal and "
        f"{MAX_DIAMETER_FACTOR:g} times it at {low:g}, and that is the only spot above the skyline passing this test."
    )


def to_upright(x: float, y: float, turns: int, width: int, height: int) -> tuple[float, float]:
    """A stored pixel position (index coordinates) in the upright frame `np.rot90(stored, -turns)`: one clockwise
    quarter turn takes (x, y) of a frame `height` rows high to ((height - 1) - y, x)."""
    for _ in range(turns % 4):
        x, y, width, height = (height - 1) - y, x, height, width
    return x, y


def to_stored(u: float, v: float, turns: int, up_width: int, up_height: int) -> tuple[float, float]:
    """The inverse of `to_upright`, from the upright frame's own size."""
    for _ in range(turns % 4):
        u, v, up_width, up_height = v, (up_width - 1) - u, up_height, up_width
    return u, v


def fov_diagonal_deg(scale_deg_per_px_value: float, width: int, height: int) -> float:
    """The diagonal field of view of a rectilinear frame at this scale: the inverse of `scale_deg_per_px`."""
    return math.degrees(2.0 * math.atan(math.radians(scale_deg_per_px_value) * math.hypot(width, height) / 2.0))


def find_skyline(upright: np.ndarray) -> Skyline | None:
    """The skyline of an upright gray frame: the row between SKYLINE_BAND's fractions of its height where each row's
    SKYLINE_ROW_PERCENTILE, smoothed by a Gaussian of SKYLINE_SMOOTHING_ROWS, rises most, placed to a fraction of a
    row by a parabola through the rise at it and its two neighbours; its half width the rows about it where the rise
    is at least half its peak, halved. None when no row in the band rises, or when the smoothed profile's rise from
    SKYLINE_STEP_REACH smoothing widths above the row to as far below is under SKYLINE_MIN_STEP_SNR times the noise of
    the sky above (1.4826 times its median absolute deviation about its median, as the Moon test measures it, but at
    least SKYLINE_NOISE_FLOOR codes)."""
    upright = np.asarray(upright, dtype=np.float64)
    height = upright.shape[0]
    lo, hi = int(height * SKYLINE_BAND[0]), int(height * SKYLINE_BAND[1])
    if hi <= lo:
        return None
    profile = np.percentile(upright, SKYLINE_ROW_PERCENTILE, axis=1)
    smooth = ndimage.gaussian_filter1d(profile, SKYLINE_SMOOTHING_ROWS)
    rise = np.gradient(smooth)
    row = lo + int(np.argmax(rise[lo:hi]))
    peak = float(rise[row])
    if not peak > 0.0:
        return None
    reach = int(round(SKYLINE_STEP_REACH * SKYLINE_SMOOTHING_ROWS))
    above, below = max(0, row - reach), min(height - 1, row + reach)
    sky = upright[: max(above, 1)]
    background = float(np.median(sky))
    noise = max(1.4826 * float(np.median(np.abs(sky - background))), SKYLINE_NOISE_FLOOR)
    step_snr = float(smooth[below] - smooth[above]) / noise
    if not step_snr >= SKYLINE_MIN_STEP_SNR:
        return None
    first, last = row, row
    while first > 0 and rise[first - 1] >= peak / 2.0:
        first -= 1
    while last < height - 1 and rise[last + 1] >= peak / 2.0:
        last += 1
    offset = 0.0
    if 0 < row < height - 1:
        curve = float(rise[row - 1] - 2.0 * rise[row] + rise[row + 1])
        if curve < 0.0:
            offset = 0.5 * float(rise[row - 1] - rise[row + 1]) / curve
    return Skyline(row=float(row) + offset, half_width_px=(last - first + 1) / 2.0, step_snr=step_snr)


def fit_horizon_pose(
    moon: TopocentricMoon,
    blob: MoonBlob,
    *,
    width: int,
    height: int,
    skyline_alt_deg: float,
    skyline_row: float,
    turns: int,
    moment: str,
    lat: float,
    lon: float,
    where: str = "in the frame",
) -> WCSModel | Refusal:
    """The TAN WCS (parity -1, k1 = k2 = 0, crpix at the frame centre) that puts the topocentric Moon on the spot
    (`blob`, stored pixels) and the skyline, taken at `skyline_alt_deg`, level on `skyline_row` of the upright frame.

    Unknowns: crval's RA and Dec, the scale and the rotation. Residuals: the Moon's projection minus the spot (two),
    and the upright rows of two points at the skyline's altitude, LEVEL_AZ_OFFSET_DEG either side of the centre's
    azimuth, minus the skyline's row (two): four constraints for four unknowns, so a small residual says the fit
    met them, not that it is right. The skyline's points and the start turn through `pose_frame`, as the head-worn
    pose's level line does. Started from the scale the Moon's apparent altitude above the skyline gives over the rows
    between them, polished by Levenberg-Marquardt, `_normalized`. Refused (kind fit) when a residual exceeds
    MAX_FIT_RESIDUAL_PX or the sky is upside down; (fov) when its field of view lies outside
    HORIZON_FOV_DIAGONAL_RANGE_DEG; (size) when the spot's diameter at half its peak is not MIN_DIAMETER_FACTOR to
    MAX_DIAMETER_FACTOR times the Moon's predicted diameter at the fitted scale."""
    lat, lon = require_place(lat, lon)
    up_w, up_h = (height, width) if turns % 2 else (width, height)
    ux, uy = to_upright(blob.x_px, blob.y_px, turns, width, height)
    refused = Refusal("fit", horizon_fit_reason(blob.x_px, blob.y_px, where, skyline_row))
    between = skyline_row - uy
    # The spot is searched for above the skyline and the Moon is more than 5 degrees up, so this guards a caller only
    if not between > 0.0 or not moon.alt_deg > skyline_alt_deg:
        return Refusal("fit", horizon_start_reason(blob.x_px, blob.y_px, where, skyline_row))
    scale0 = (moon.alt_deg - skyline_alt_deg) / between
    centre_alt0 = skyline_alt_deg + (skyline_row - (up_h - 1) / 2.0) * scale0
    centre_az0 = moon.az_deg - (ux - (up_w - 1) / 2.0) * scale0 / max(0.25, math.cos(math.radians(moon.alt_deg)))
    crpix_x, crpix_y = width / 2.0, height / 2.0
    frame = pose_frame(moon, lat, lon, moment)

    def wcs_for(params: np.ndarray) -> WCSModel:
        return WCSModel(
            crval_ra_deg=float(params[0]),
            crval_dec_deg=float(params[1]),
            crpix_x=crpix_x,
            crpix_y=crpix_y,
            scale_deg_per_px=float(params[2]),
            rotation_deg=float(params[3]),
            k1=0.0,
            k2=0.0,
            parity=-1,
            width=width,
            height=height,
        )

    def skyline_points(wcs: WCSModel) -> tuple[tuple[float, float], tuple[float, float]]:
        """The upright positions of the skyline's two points, left then right of the centre's azimuth."""
        _, az = icrs_to_frame_altaz(frame, wcs.crval_ra_deg, wcs.crval_dec_deg)
        points = []
        for side in (-LEVEL_AZ_OFFSET_DEG, LEVEL_AZ_OFFSET_DEG):
            px, py = wcs.world_to_pixel(*frame_altaz_to_icrs(frame, skyline_alt_deg, az + side))
            points.append(to_upright(px, py, turns, width, height))
        return points[0], points[1]

    def residuals_of(wcs: WCSModel) -> np.ndarray:
        mx, my = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
        (_, left_v), (_, right_v) = skyline_points(wcs)
        return np.array([mx - blob.x_px, my - blob.y_px, left_v - skyline_row, right_v - skyline_row], dtype=np.float64)

    def residuals(params: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(params)) or not params[2] > 0.0:
            return np.full(4, 1e4, dtype=np.float64)
        res = residuals_of(wcs_for(params))
        return res if np.all(np.isfinite(res)) else np.full(4, 1e4, dtype=np.float64)

    start_ra, start_dec = frame_altaz_to_icrs(frame, centre_alt0, centre_az0)
    (lu, lv), (ru, rv) = skyline_points(wcs_for(np.array([start_ra, start_dec, scale0, 0.0])))
    start = np.array([start_ra, start_dec, scale0, math.degrees(math.atan2(rv - lv, ru - lu))])
    fit = optimize.least_squares(residuals, start, method="lm", xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=400)
    if not np.all(np.isfinite(fit.x)) or not fit.x[2] > 0.0:
        return refused
    wcs = _normalized(wcs_for(fit.x))
    (lu, _), (ru, _) = skyline_points(wcs)
    if not _fit_accepted(residuals_of(wcs)):
        return refused
    if not ru - lu > 0.0:
        return Refusal("fit", horizon_upside_down_reason(blob.x_px, blob.y_px, where, skyline_row))
    fov = fov_diagonal_deg(wcs.scale_deg_per_px, width, height)
    low, high = HORIZON_FOV_DIAGONAL_RANGE_DEG
    if not low <= fov <= high:
        return Refusal("fov", horizon_fov_reason(blob.x_px, blob.y_px, where, skyline_row, fov))
    ratio = blob.equivalent_diameter_px / (2.0 * moon.radius_deg / wcs.scale_deg_per_px)
    if not MIN_DIAMETER_FACTOR <= ratio <= MAX_DIAMETER_FACTOR:
        return Refusal("size", horizon_size_reason(blob.x_px, blob.y_px, where, blob.equivalent_diameter_px, ratio))
    return wcs


def _horizon_assumptions(moon: TopocentricMoon, skyline: Skyline, term_arcsec: float) -> tuple[str, ...]:
    low, high = HORIZON_FOV_DIAGONAL_RANGE_DEG
    bottom, middle, top = SKYLINE_ALTS_DEG
    return (
        "The bright spot is taken to be the Moon: nothing in one frame proves it, and a lamp or another light of "
        "similar size above the skyline, as bright and the only spot there that passes the Moon test, would be taken "
        "the same way.",
        f"The skyline at row {skyline.row:.0f} of the upright frame is taken to be level ground {bottom:g} to {top:g} "
        f"degrees above the true horizon (hills, buildings and refraction lift it); the sweep takes {bottom:g}, "
        f"{middle:g} and {top:g} degrees. A sea horizon seen from a height lies below the true horizon by its dip, "
        "which the sweep does not cover.",
        f"The skyline's row is measured to within {skyline.half_width_px:.0f} px, the half width of the rise in "
        f"brightness there; the sweep takes the row and {skyline.half_width_px:.0f} px either side.",
        "The skyline is level in the upright frame, so the camera's roll is taken to be 0: a sloping skyline tilts "
        "the pose.",
        "The lens is rectilinear (gnomonic), with no distortion, and centred on the frame; its field of view is "
        f"fitted from the spot and the skyline, and a fit outside {low:g} to {high:g} degrees across the diagonal is "
        "refused.",
        "The spot's light centroid is taken to lie on the Moon's disk, and the Moon where its ephemeris puts it: the "
        f"beacon term, the Moon's predicted radius of {moon.radius_deg:.2f} degrees plus the ephemeris's stated "
        f"uncertainty of {moon.uncertainty_deg * 3600.0:,.0f} arcsec plus {term_arcsec:,.0f} arcsec for drawing every "
        "position through one rotation, is taken to cover overexposure, a partial phase, the ephemeris's own error, "
        "and the aberration and light deflection that differ across the frame from the Moon's, which one rotation "
        "cannot follow.",
        "The Moon is placed where it is seen from the place (topocentric): the ephemeris record as it is, its parallax "
        "already applied there; refraction is left out for the Moon, the skyline and the stars alike, and near the "
        "horizon it lifts what is seen by about half a degree, which is taken to fall inside the skyline's swept "
        "altitudes.",
    )


def horizon_moon_pose(
    luma: np.ndarray,
    *,
    frame_time_sec: float,
    moment: str,
    lat: float,
    lon: float,
    orientation: Mapping[str, Any] | None,
    where: str = "in the frame",
) -> PoseResult | Refusal:
    """The Moon plus horizon pose from one stored frame recorded at `moment`: a frame not mirrored, the Moon up and
    the Sun down at that moment, a skyline in the upright frame (`find_skyline`), sky-3's Moon test on the sky above
    it (down to its row less its half width, that edge named the skyline), then nine fits (`fit_horizon_pose`): the
    skyline at each of SKYLINE_ALTS_DEG on its row less, at and plus its half width. Any refused fit refuses the pose.
    The nominal WCS is the middle altitude on the row found; the pose estimate keeps all nine, the largest angle
    between the nominal centre and a swept one, and the beacon term of the head-worn pose."""
    lat, lon = require_place(lat, lon)
    turns, mirrored = _orientation(orientation)
    if mirrored:
        return Refusal("mirrored", MIRRORED)
    moon = topocentric_moon(moment, lat, lon)
    if not _up(moon):
        return Refusal("moon_not_up", MOON_NOT_UP, frame_time_sec=frame_time_sec)
    if sun_is_up(moment, lat, lon):
        return Refusal("daytime", DAYTIME, frame_time_sec=frame_time_sec)
    luma = np.asarray(luma, dtype=np.float64)
    height, width = luma.shape
    upright = np.rot90(luma, -turns)
    up_h, up_w = upright.shape
    skyline = find_skyline(upright)
    if skyline is None:
        return Refusal("no_skyline", NO_SKYLINE, frame_time_sec=frame_time_sec)
    top = int(math.floor(skyline.row - skyline.half_width_px))
    if top < MIN_SKY_ROWS:
        return Refusal("not_moon", not_confirmed_above_skyline_reason(where), frame_time_sec=frame_time_sec)
    min_d, max_d = moon_diameter_limits_px(moon, HORIZON_FOV_DIAGONAL_RANGE_DEG, up_w, up_h)
    found = find_moon_blob(upright[:top], min_diameter_px=min_d, max_diameter_px=max_d, bottom="the skyline")
    if isinstance(found, Refusal):
        return replace(found, frame_time_sec=frame_time_sec)
    x_px, y_px = to_stored(found.x_px, found.y_px, turns, up_w, up_h)
    blob = replace(found, x_px=x_px, y_px=y_px)
    rows = (skyline.row - skyline.half_width_px, skyline.row, skyline.row + skyline.half_width_px)
    swept: list[WCSModel] = []
    nominal: WCSModel | None = None
    for skyline_alt in SKYLINE_ALTS_DEG:
        for row in rows:
            wcs = fit_horizon_pose(
                moon, blob, width=width, height=height, skyline_alt_deg=skyline_alt, skyline_row=row, turns=turns,
                moment=moment, lat=lat, lon=lon, where=where,
            )
            if isinstance(wcs, Refusal):
                return replace(wcs, blob=blob, frame_time_sec=frame_time_sec)
            swept.append(wcs)
            if skyline_alt == SKYLINE_ALTS_DEG[1] and row == skyline.row:
                nominal = wcs
    assert nominal is not None
    # Where the camera's centre is seen: its pointing in the apparent horizon, which the frame rotation gives for
    # the nominal crval, as the head-worn pose states it
    frame = pose_frame(moon, lat, lon, moment)
    centre_alt, centre_az = icrs_to_frame_altaz(frame, nominal.crval_ra_deg, nominal.crval_dec_deg)
    term_arcsec = frame_term_arcsec(
        moon, swept, [fov_diagonal_deg(w.scale_deg_per_px, width, height) for w in swept], lat, lon, moment
    )
    estimate = PoseEstimate(
        method=HORIZON_POSE_METHOD,
        beacon="moon",
        center_alt_deg=centre_alt,
        center_az_deg=centre_az,
        roll_deg=0.0,
        fov_diagonal_deg=fov_diagonal_deg(nominal.scale_deg_per_px, width, height),
        fov_source=(
            "Fitted, not assumed: from the angle between the bright spot taken to be the Moon and the skyline taken "
            "to be level ground, over the rows between them in the frame, for a rectilinear lens."
        ),
        assumptions=_horizon_assumptions(moon, skyline, term_arcsec),
        swept_wcs=tuple(swept),
        max_center_offset_deg=max(
            angular_separation_deg(nominal.crval_ra_deg, nominal.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg)
            for w in swept
        ),
        beacon_centroid_uncertainty_deg=moon.radius_deg + moon.uncertainty_deg + term_arcsec / 3600.0,
    )
    return PoseResult(nominal, estimate, blob, moon, frame_time_sec, moment, None, skyline)


def horizon_pose_from_frames(
    frames: Sequence[tuple[float, str, np.ndarray]],
    *,
    lat: float,
    lon: float,
    orientation: Mapping[str, Any] | None,
    is_clip: bool = True,
) -> PoseResult | Refusal:
    """The first frame, in time order, that gives a Moon plus horizon pose; each frame (its time, its moment, its
    gray pixels) is judged and used alone, never stacked. With none, the refusal that says most: a spot above a
    skyline that gave no pose (the fit, its field of view or its size refused), else not confirmed as the Moon
    (counting the frames searched), else no skyline, else the Moon not up in every frame, else by day."""
    lat, lon = require_place(lat, lon)
    if is_mirrored(orientation):
        return Refusal("mirrored", MIRRORED)
    ordered = sorted(frames, key=lambda frame: frame[0])
    if not ordered:
        return Refusal("no_frames", NO_FRAMES)
    refusals: list[Refusal] = []
    for frame_time_sec, moment, luma in ordered:
        result = horizon_moon_pose(
            luma, frame_time_sec=frame_time_sec, moment=moment, lat=lat, lon=lon, orientation=orientation,
            where=_where(frame_time_sec, is_clip),
        )
        if isinstance(result, PoseResult):
            return result
        refusals.append(result)
    posed = next((r for r in refusals if r.kind in HORIZON_REFUSALS and r.blob is not None), None)
    if posed is not None:
        return posed
    searched = sum(1 for r in refusals if r.kind == "not_moon")
    if searched:
        if not is_clip:
            where = "in the photo"
        else:
            where = "in the frame searched" if searched == 1 else f"in the {searched} frames searched"
        return Refusal("not_moon", not_confirmed_above_skyline_reason(where))
    if any(r.kind == "no_skyline" for r in refusals):
        return Refusal("no_skyline", NO_SKYLINE)
    if all(r.kind == "moon_not_up" for r in refusals):
        return Refusal("moon_not_up", MOON_NOT_UP)
    return Refusal("daytime", DAYTIME)
