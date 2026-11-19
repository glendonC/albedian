"""The Sun as the beacon by day, its first pieces (sky-6a; spec predicted-sky-for-every-upload REQ-SKY-4.3): the Sun's
place seen from the photo's place, the Sun's core in a day frame, and the clock check that comes before any Sun pose.

Frames are synthetic day skies [simulated]: a vertical gradient, a clipped Sun with a bloom several times its disk, and
glitter specks on water below a horizon, as the two public daytime photos measured them (sky-6-research.md). The
moment and place are open ocean and point at nobody.
"""

from __future__ import annotations

import math
from typing import Any

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


# sky-6b: the skyline of a day frame, the highest step below the Sun in the columns about it


def _day_with(horizon: int | None, *, water: float = 110.0, seed: int = 0, sun: tuple[float, float] = (450.0, 250.0)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    frame = 140.0 + 50.0 * yy / H + 60.0 * np.exp(-np.hypot(xx - sun[0], yy - sun[1]) / 200.0)
    frame[np.hypot(xx - sun[0], yy - sun[1]) <= 50.0] = 255.0
    if horizon is not None:
        frame[horizon:] = water + 3.0 * rng.standard_normal((H - horizon, W))
        for _ in range(300):
            frame[rng.integers(horizon + 5, H), rng.integers(0, W)] = 255.0
    return np.clip(frame + 2.0 * rng.standard_normal((H, W)), 0, 255)


@pytest.mark.parametrize(("horizon", "water"), [(700, 110.0), (950, 110.0), (520, 120.0), (800, 235.0)])
def test_the_day_skyline_is_the_step_below_the_sun_of_either_sign(horizon: int, water: float) -> None:
    frame = _day_with(horizon, water=water)
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    assert isinstance(core, sp.SunCore)
    sky = sp.find_day_skyline(frame, core)
    assert isinstance(sky, sp.Skyline), sky
    assert abs(sky.row - horizon) <= 2.0
    assert sky.step_snr >= sp.DAY_SKYLINE_MIN_SNR


def test_a_day_frame_with_no_skyline_gives_none_over_twenty_seeds() -> None:
    for seed in range(20):
        frame = _day_with(None, seed=seed)
        core = sp.find_sun_core(frame, max_diameter_px=500.0)
        assert isinstance(core, sp.SunCore)
        sky = sp.find_day_skyline(frame, core)
        assert isinstance(sky, Refusal) and sky.kind == "no_skyline", (seed, sky)


def test_a_single_faint_step_below_the_sun_is_no_skyline() -> None:
    # The only step below the Sun, 8 codes against a sky noise of 2: unique, but under DAY_SKYLINE_MIN_SNR
    rng = np.random.default_rng(4)
    frame = _day_with(None, seed=4)
    frame[800:] = np.clip(frame[800:] - 8.0 + 0.5 * rng.standard_normal((H - 800, W)), 0, 255)
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    assert isinstance(core, sp.SunCore)
    sky = sp.find_day_skyline(frame, core)
    assert isinstance(sky, Refusal) and sky.kind == "no_skyline", sky


def test_two_skylines_as_strong_as_each_other_give_none() -> None:
    frame = _day_with(700)
    frame[950:] = np.clip(frame[950:] - 60.0, 0, 255)
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    sky = sp.find_day_skyline(frame, core)
    assert isinstance(sky, Refusal) and sky.kind == "no_skyline"


# sky-6c: the Sun plus horizon pose, with the clock check before it

POSE_MOMENT = "2031-06-21T10:30:00Z"
FOCAL35 = 24.0


def _level_camera_frame(centre_alt: float, sun_alt: float, *, bloom: float = 40.0) -> np.ndarray:
    """A portrait frame (900 x 1200) from a level camera whose axis looks along the Sun's azimuth at `centre_alt`:
    on the middle column a point at altitude a sits f tan(a - centre_alt) above the centre, and the true horizon, a
    great circle, is the straight row f tan(centre_alt) below it; water below that row, the Sun's bloom on the column."""
    f = math.hypot(W, H) / 2.0 / (math.hypot(36.0, 24.0) / 2.0 / FOCAL35)
    cx, cy = (W - 1) / 2.0, (H - 1) / 2.0
    sun_row = cy - f * math.tan(math.radians(sun_alt - centre_alt))
    horizon = int(round(cy + f * math.tan(math.radians(centre_alt))))
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    frame = 140.0 + 50.0 * yy / H + 60.0 * np.exp(-np.hypot(xx - cx, yy - sun_row) / 200.0)
    frame[np.hypot(xx - cx, yy - sun_row) <= bloom / 2.0] = 255.0
    frame[horizon:] = 110.0 + 3.0 * rng.standard_normal((H - horizon, W))
    return np.clip(frame + 2.0 * rng.standard_normal((H, W)), 0, 255)


def test_the_sun_pose_lines_up_a_level_camera_within_its_stated_bound() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    assert 15.0 < sun.alt_deg < 45.0
    centre_alt = sun.alt_deg - 15.0
    pose = sp.sun_horizon_pose(
        _level_camera_frame(centre_alt, sun.alt_deg), moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None,
        focal_35mm_mm=FOCAL35,
    )
    assert isinstance(pose, sp.SunPose), pose
    estimate = pose.estimate
    assert (estimate.method, estimate.beacon) == ("sun_horizon", "sun")
    assert len(estimate.swept_wcs) == 9
    # The skyline is taken 0 to 3 degrees above the true horizon (1.5 nominal); this one is the true horizon itself
    bound = estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg
    gap = math.hypot(estimate.center_alt_deg - centre_alt, (estimate.center_az_deg - sun.az_deg) * math.cos(math.radians(centre_alt)))
    assert gap <= bound
    # Taking the true horizon to be 1.5 degrees up both lifts the centre and narrows the fitted field of view; the
    # centre stays within the sweep's own spread of the truth
    assert abs(estimate.center_alt_deg - centre_alt) <= estimate.max_center_offset_deg
    # A 24 mm equivalent lens is 84.1 degrees across; the fit, from a Sun 34 degrees above a skyline taken 1.5 up
    assert estimate.fov_diagonal_deg == pytest.approx(84.1, abs=5.0)
    assert pose.measured_height_deg == pytest.approx(sun.alt_deg, abs=0.2)
    # The beacon term: the Sun's radius, its ephemeris's stated uncertainty and the core's offset in its glare
    assert estimate.beacon_centroid_uncertainty_deg >= sun.radius_deg + sun.uncertainty_deg + sp.SUN_CORE_OFFSET_DEG
    assert estimate.beacon_centroid_uncertainty_deg < sun.radius_deg + sun.uncertainty_deg + sp.SUN_CORE_OFFSET_DEG + 0.01


def test_no_sun_pose_when_the_camera_clock_disagrees_with_the_sun() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    # Drawn for the Sun 4 degrees higher than the photo's time puts it
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg + 4.0)
    pose = sp.sun_horizon_pose(frame, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(pose, Refusal) and pose.kind == "clock"


@pytest.mark.parametrize(("moment", "orientation", "focal", "kind"), [
    ("2031-06-21T08:00:00Z", None, FOCAL35, "sun_low"),
    (POSE_MOMENT, {"quarterTurnsClockwise": 0, "mirrored": True}, FOCAL35, "mirrored"),
    (POSE_MOMENT, None, None, "no_focal"),
])
def test_no_sun_pose_without_what_it_needs(moment: str, orientation: Any, focal: float | None, kind: str) -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    pose = sp.sun_horizon_pose(frame, moment=moment, lat=LAT, lon=LON, orientation=orientation, focal_35mm_mm=focal)
    assert isinstance(pose, Refusal) and pose.kind == kind, pose
    assert chr(0x2014) not in pose.reason
