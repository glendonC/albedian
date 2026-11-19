"""The Sun as the beacon by day, its first pieces (sky-6a; spec predicted-sky-for-every-upload REQ-SKY-4.3): the Sun's
place seen from the photo's place, the Sun's core in a day frame, and the clock check that comes before any Sun pose.

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
from services.science.pose_estimate import Refusal, TopocentricMoon

# The nominal solar radius, 695,700 km [verified: IAU 2015 Resolution B3]
SUN_RADIUS_KM = 695_700.0
# [proposed, measured on two photos] The Sun's core: the pixels within this many codes of the frame's own brightest,
# so a Sun that is not clipped (one photo peaked below 250) is found as a clipped one is
SUN_CORE_CODES = 10.0
# [proposed] A core smaller than this many pixels across is a speck (glitter, a star of light), never the Sun
MIN_SUN_CORE_DIAMETER_PX = 6.0

_NO_SUN = "The Sun was not found in the frame: "
NO_SUN = _NO_SUN + "no bright region larger than a speck stands at the frame's brightest level."
SUN_BLOOM = _NO_SUN + "its brightest region is wider than the Sun's glare could be at the camera's widest field of view."
SUN_EDGE = _NO_SUN + "its brightest region touches the edge of the frame, so its centre cannot be measured."


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
