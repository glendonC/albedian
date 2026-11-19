"""The Sun as the beacon by day, its first pieces (sky-6a; spec predicted-sky-for-every-upload REQ-SKY-4.3): the Sun's
place seen from the photo's place, the Sun's core in a day frame, and the clock check that comes before any Sun pose.

Frames are synthetic day skies [simulated]: a vertical gradient, a clipped Sun with a bloom several times its disk, and
glitter specks on water below a horizon, as the two public daytime photos measured them (sky-6-research.md). The
moment and place are open ocean and point at nobody.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from services.science import sun_pose as sp
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.pose_estimate import Refusal

MOMENT, LAT, LON = "2031-06-21T15:00:00Z", 30.0, -40.0
H, W = 1200, 900


def _day(sun_x: float, sun_y: float, bloom_px: float, *, horizon: int = 800, specks: int = 300, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    frame = 140.0 + 50.0 * yy / H + 60.0 * np.exp(-np.hypot(xx - sun_x, yy - sun_y) / 200.0)
    frame[np.hypot(xx - sun_x, yy - sun_y) <= bloom_px / 2.0] = 255.0
    frame[horizon:] = 110.0
    # Glitter on the water: single clipped specks
    for _ in range(specks):
        frame[rng.integers(horizon + 5, H), rng.integers(0, W)] = 255.0
    return np.clip(frame + 2.0 * rng.standard_normal((H, W)), 0, 255)


def test_the_sun_is_placed_where_the_ephemeris_puts_it_with_its_own_radius() -> None:
    sun = sp.topocentric_sun(MOMENT, LAT, LON)
    (record,) = compute_solar_system_ephemerides(MOMENT, LAT, LON, bodies=("Sun",), topocentric=True)
    assert (sun.ra_deg, sun.dec_deg, sun.alt_deg, sun.az_deg) == (record.ra_deg, record.dec_deg, record.alt_deg, record.az_deg)
    # The Sun's angular radius from the place: 0.262 to 0.272 degrees over the year
    assert 0.262 < sun.radius_deg < 0.272
    assert sun.uncertainty_deg == record.uncertainty_arcsec / 3600.0


@pytest.mark.parametrize(("x", "y", "bloom"), [(300.0, 250.0, 120.0), (610.5, 400.25, 60.0), (450.0, 120.0, 200.0)])
def test_the_sun_core_is_the_largest_clipped_region_above_the_glitter(x: float, y: float, bloom: float) -> None:
    core = sp.find_sun_core(_day(x, y, bloom), max_diameter_px=4.0 * bloom)
    assert isinstance(core, sp.SunCore), core
    assert math.hypot(core.x_px - x, core.y_px - y) < 0.6
    assert core.diameter_px == pytest.approx(bloom, rel=0.05)


def test_a_frame_with_no_clipped_region_larger_than_a_speck_has_no_sun_core() -> None:
    frame = _day(300.0, 250.0, 120.0)
    yy, xx = np.mgrid[0:H, 0:W]
    frame[np.hypot(xx - 300.0, yy - 250.0) <= 60.0] = 180.0
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    assert isinstance(core, Refusal) and core.kind == "no_sun"


def test_a_sun_core_wider_than_its_limit_or_on_the_frame_edge_is_refused() -> None:
    wide = sp.find_sun_core(_day(300.0, 250.0, 300.0), max_diameter_px=200.0)
    assert isinstance(wide, Refusal) and wide.kind == "sun_bloom"
    edge = sp.find_sun_core(_day(30.0, 250.0, 120.0), max_diameter_px=500.0)
    assert isinstance(edge, Refusal) and edge.kind == "sun_edge"


def test_the_sun_core_level_follows_the_frame_s_own_top() -> None:
    # A Sun that is not clipped (Svalbard's peaked near 240): the core is taken within SUN_CORE_CODES of the top
    frame = _day(300.0, 250.0, 120.0) * (240.0 / 255.0)
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    assert isinstance(core, sp.SunCore) and math.hypot(core.x_px - 300.0, core.y_px - 250.0) < 0.6


@pytest.mark.parametrize(("measured", "predicted", "tolerance", "passes"), [
    (25.43, 25.87, 1.0, True),   # Lake Michigan, measured on the photo
    (14.1, 9.9, 1.0, False),     # Svalbard: the camera's clock is hours off
    (10.0, 9.0, 1.0, True),
    (10.0, 8.99, 1.0, False),
    (-3.0, 4.0, 2.0, False),
])
def test_the_clock_check_refuses_a_sun_whose_height_disagrees_with_the_photo_s_time(
    measured: float, predicted: float, tolerance: float, passes: bool
) -> None:
    verdict = sp.clock_check(measured, predicted, tolerance_deg=tolerance)
    if passes:
        assert verdict is None
    else:
        assert isinstance(verdict, Refusal) and verdict.kind == "clock"
        assert chr(0x2014) not in verdict.reason and str(LAT) not in verdict.reason
        assert f"{measured:.1f}" in verdict.reason and f"{predicted:.1f}" in verdict.reason


def test_the_clock_check_needs_a_positive_tolerance() -> None:
    with pytest.raises(ValueError):
        sp.clock_check(10.0, 10.0, tolerance_deg=0.0)
