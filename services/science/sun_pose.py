"""The Sun as the beacon by day, its first pieces (sky-6a; spec predicted-sky-for-every-upload REQ-SKY-4.3): the Sun's
place seen from the photo's place, the Sun's core in a day frame, and the clock check that comes before any Sun pose;
and the skyline of a day frame (sky-6b), the step below the Sun in the columns about it.

Measured on two public daytime photos (.lead/kiro/reports/SCIENCE-INGEST/sky-6-research.md) [tested]: the Sun's
clipped core is 4.5 to 6.2 times its disk across (its bloom), thousands of single clipped specks (glitter on water)
stand beside it, and one photo's camera clock is hours off, which would line the sky up wrong without a check. The
skyline of a day frame and the fits come in the next units; nothing here draws a sky.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy import ndimage

from services.science.catalog import require_place
from services.science.ephemeris import AU_KM, compute_solar_system_ephemerides
from services.science.pose_estimate import SKYLINE_NOISE_FLOOR, Refusal, Skyline, TopocentricMoon

# The nominal solar radius, 695,700 km [verified: IAU 2015 Resolution B3]
SUN_RADIUS_KM = 695_700.0
# [proposed, measured on two photos] The Sun's core: the pixels within this many codes of the frame's own brightest,
# so a Sun that is not clipped (one photo peaked below 250) is found as a clipped one is
SUN_CORE_CODES = 10.0
# [proposed] A core smaller than this many pixels across is a speck (glitter, a star of light), never the Sun
MIN_SUN_CORE_DIAMETER_PX = 6.0

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

_NO_SUN = "The Sun was not found in the frame: "
NO_SUN = _NO_SUN + "no bright region larger than a speck stands at the frame's brightest level."
SUN_BLOOM = _NO_SUN + "its brightest region is wider than the Sun's glare could be at the camera's widest field of view."
SUN_EDGE = _NO_SUN + "its brightest region touches the edge of the frame, so its centre cannot be measured."
NO_DAY_SKYLINE = (
    "The sky is not lined up over the picture: no single skyline stands out below the Sun, where the sky meets the "
    "ground or the water."
)


@dataclass(frozen=True)
class SunCore:
    """The Sun's core in a frame: the centroid of its brightest region in pixels, its area and equivalent diameter."""

    x_px: float
    y_px: float
    area_px: int
    diameter_px: float


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


def find_sun_core(luma: np.ndarray, *, max_diameter_px: float) -> SunCore | Refusal:
    """The Sun's core in one gray frame (0 to 255): the largest 8-connected region within SUN_CORE_CODES of the
    frame's brightest code, its centroid unweighted (the core is clipped or nearly so, so its shape, not its light,
    places it). Refused (no_sun) when that region is under MIN_SUN_CORE_DIAMETER_PX across, (sun_bloom) when it is
    wider than `max_diameter_px`, and (sun_edge) when it touches the frame's edge."""
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
    rows, cols = np.nonzero(labels == largest)
    height, width = luma.shape
    if rows.min() == 0 or cols.min() == 0 or rows.max() == height - 1 or cols.max() == width - 1:
        return Refusal("sun_edge", SUN_EDGE)
    return SunCore(x_px=float(cols.mean()), y_px=float(rows.mean()), area_px=area, diameter_px=diameter)


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
        f"The sky is not lined up over the picture: the Sun stands about {measured_height_deg:.1f} degrees above the "
        f"skyline in the picture, but {predicted_alt_deg:.1f} degrees at the photo's recorded time, so the camera's "
        "clock or its time zone looks wrong.",
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
