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
from services.science.pose_estimate import MIN_PEAK_LUMA, Refusal

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


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_the_pose_states_the_suns_core_where_the_stored_frame_holds_it(turns: int) -> None:
    # The core is found in the upright frame; the pose states it in stored pixels too (`spot`), where the solve's
    # detections are measured, with the core's own radius (AUDIT's sweep of 21 Nov, finding one)
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    upright = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    # Shown after `turns` clockwise quarter turns, so stored as the upright frame turned back that many times
    stored = np.rot90(upright, turns)
    pose = sp.sun_horizon_pose(
        stored, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation={"quarterTurnsClockwise": turns, "mirrored": False},
        focal_35mm_mm=FOCAL35,
    )
    assert isinstance(pose, sp.SunPose), pose
    level = float(np.max(upright)) - sp.SUN_CORE_CODES
    assert upright[int(round(pose.core.y_px)), int(round(pose.core.x_px))] >= level
    assert stored[int(round(pose.spot.y_px)), int(round(pose.spot.x_px))] >= level
    assert pose.spot.radius_px == pytest.approx(pose.core.diameter_px / 2.0)
    # The same core by the helper a clip's stack is read with: the pose's own test, the same place and radius
    alone = sp.sun_core_spot(stored, orientation={"quarterTurnsClockwise": turns, "mirrored": False},
                             focal_35mm_mm=FOCAL35, sun=pose.sun)
    assert not isinstance(alone, Refusal), alone
    assert (alone.x_px, alone.y_px, alone.radius_px) == pytest.approx((pose.spot.x_px, pose.spot.y_px, pose.spot.radius_px))
    if turns == 0:
        assert (pose.spot.x_px, pose.spot.y_px) == pytest.approx((pose.core.x_px, pose.core.y_px))


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


# sky-6d: the design lane's C23 words (.lead/uiux/UIUX-sunwords-READY.md, decided 17:08)

FAMILY = "The sky is not lined up over the picture: "


def test_the_sun_pose_s_refusals_are_c23_s() -> None:
    assert sp.NO_SUN == FAMILY + "no Sun was found in the frame. No bright region larger than a speck stands at the frame's brightest level."
    assert sp.SUN_EDGE == FAMILY + "the Sun's glare touches the edge of the frame, so its centre cannot be measured."
    assert sp.NO_DAY_SKYLINE == FAMILY + "no skyline was found below the Sun to line it up with. No single line stands out where the sky meets the ground or the water."
    assert sp.SUN_LOW == FAMILY + "the Sun was too low at the photo's time to line it up with. It was not more than 5 degrees above the horizon."
    assert sp.SUN_MIRRORED == FAMILY + "the photo is stored mirrored, so the Sun cannot be lined up as the camera saw it."
    assert sp.NO_FOCAL == FAMILY + "the photo's file gives no 35 mm focal length, so its time cannot be checked against the Sun."
    assert sp.sun_bloom_reason(24.0) == FAMILY + "the brightest glare is too wide to be the Sun's. It is wider than 8 times the Sun's disk through the file's own 24 mm lens."
    assert sp.clock_check(13.84, 9.9, tolerance_deg=1.5).reason == (
        FAMILY + "the camera's clock or its time zone looks wrong. The Sun stands about 13.8 degrees above the skyline "
        "in the picture, but was 9.9 degrees up at the recorded time."
    )


def test_a_too_wide_glare_names_the_file_s_lens() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg, bloom=400.0)
    pose = sp.sun_horizon_pose(frame, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(pose, Refusal) and pose.kind == "sun_bloom" and pose.reason == sp.sun_bloom_reason(FOCAL35)


def test_the_sun_pose_s_estimate_words_are_c23_s() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    pose = sp.sun_horizon_pose(
        _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg), moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None,
        focal_35mm_mm=FOCAL35,
    )
    assert isinstance(pose, sp.SunPose)
    assert pose.estimate.fov_source == "Fitted from the Sun and the skyline, and checked against the file's 35 mm focal length."
    assert pose.estimate.assumptions == (
        "The glare's centre is taken to be within 0.5 degrees of the Sun's.",
        "The skyline is taken to be level and 0 to 3 degrees above the true horizon; the fit tried it at 0, 1.5 and 3 degrees.",
        "The camera's clock is taken to be right: the Sun's height on the picture agreed with its height at the recorded time, through the file's 24 mm lens.",
        "The lens is taken to be a plain one with no distortion, centred on the frame.",
    )


# sky-6 (a): the Sun's core measured in the sky above the skyline, clipped or unclipped


def test_the_sun_core_records_whether_its_own_peak_is_clipped() -> None:
    clipped = sp.find_sun_core(_day(300.0, 250.0, 120.0), max_diameter_px=500.0)
    assert isinstance(clipped, sp.SunCore)
    assert clipped.clipped is True and clipped.clipped_area_px > 0
    unclipped = sp.find_sun_core(_day(300.0, 250.0, 120.0) * (240.0 / 255.0), max_diameter_px=500.0)
    assert isinstance(unclipped, sp.SunCore)
    assert unclipped.clipped is False and unclipped.clipped_area_px == 0
    # Svalbard's case: single-pixel glitter specks on the water reach 255 while the Sun's own core peaks below 255
    glitter_frame = np.minimum(_day(300.0, 250.0, 120.0, specks=0), 248.0)
    glitter_frame[900, 100] = 255.0
    glitter_frame[920, 400] = 255.0
    svalbard_like = sp.find_sun_core(glitter_frame, max_diameter_px=500.0)
    assert isinstance(svalbard_like, sp.SunCore)
    assert svalbard_like.clipped is False and svalbard_like.clipped_area_px == 0


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_the_pose_measures_the_glare_in_the_sky_above_the_skyline(turns: int) -> None:
    from scipy import ndimage  # noqa: PLC0415
    from services.science.detector import _effective_sigma  # noqa: PLC0415

    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    upright = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    # Dark water covering the lower part of the frame: ground or water below the skyline is not sky background or noise
    horizon = int(round((H - 1) / 2.0 + (math.hypot(W, H) / 2.0 / (math.hypot(36.0, 24.0) / 2.0 / FOCAL35)) * math.tan(math.radians(sun.alt_deg - 15.0))))
    upright[horizon + 20 :] = 15.0
    stored = np.rot90(upright, turns)
    pose = sp.sun_horizon_pose(
        stored, moment=POSE_MOMENT, lat=LAT, lon=LON,
        orientation={"quarterTurnsClockwise": turns, "mirrored": False}, focal_35mm_mm=FOCAL35,
    )
    assert isinstance(pose, sp.SunPose), pose
    labels, _ = ndimage.label(upright >= float(np.max(upright)) - sp.SUN_CORE_CODES, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())[1:]
    core_mask = labels == (int(np.argmax(sizes)) + 1)
    sky_end = int(math.floor(pose.skyline.row - pose.skyline.half_width_px))
    sky_rows = upright[:sky_end]
    sky_mask = ~core_mask[:sky_end]
    bg = float(np.median(sky_rows[sky_mask]))
    masked = np.where(sky_mask, sky_rows, np.nan)
    row_mad = 1.4826 * float(np.nanmedian(np.abs(masked - np.nanmedian(masked, axis=1, keepdims=True))))
    noise = _effective_sigma(row_mad, upright)
    peak = float(np.max(upright[core_mask]))
    expected_flux = float(np.clip(upright[core_mask] - bg, 0.0, None).sum())
    spot = pose.spot
    assert spot.flux == pytest.approx(expected_flux)
    assert spot.snr == pytest.approx((peak - bg) / noise)
    assert spot.fwhm_px == pytest.approx(pose.core.diameter_px)
    assert spot.centroid_uncertainty_px == pytest.approx(sp.SUN_CORE_OFFSET_DEG / pose.wcs.scale_deg_per_px)
    assert spot.peak_to_total_ratio == pytest.approx((peak - bg) / expected_flux)


def test_the_sun_glare_spot_holds_contract_bounds_across_twenty_seeds() -> None:
    from hypothesis import given, settings, strategies as st  # noqa: PLC0415
    from services.science.wcs_models import SourceDetection  # noqa: PLC0415

    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)

    @settings(max_examples=6, deadline=None)
    @given(
        pitch_offset=st.floats(10.0, 20.0, allow_nan=False, allow_infinity=False),
        bloom=st.floats(18.0, 48.0, allow_nan=False, allow_infinity=False),
        clipped=st.booleans(),
        unclipped_scale=st.floats(0.88, 0.97, allow_nan=False, allow_infinity=False),
        turns=st.integers(0, 3),
    )
    def prop(pitch_offset: float, bloom: float, clipped: bool, unclipped_scale: float, turns: int) -> None:
        scale = 1.0 if clipped else unclipped_scale
        upright = _level_camera_frame(sun.alt_deg - pitch_offset, sun.alt_deg, bloom=bloom) * scale
        stored = np.rot90(upright, turns)
        pose = sp.sun_horizon_pose(
            stored, moment=POSE_MOMENT, lat=LAT, lon=LON,
            orientation={"quarterTurnsClockwise": turns, "mirrored": False}, focal_35mm_mm=FOCAL35,
        )
        assert isinstance(pose, sp.SunPose), pose
        spot = pose.spot
        det = SourceDetection(
            x_px=spot.x_px, y_px=spot.y_px, flux=spot.flux, snr=spot.snr, fwhm_px=spot.fwhm_px,
            centroid_uncertainty_px=spot.centroid_uncertainty_px, asset_id="src_seed",
            peak_to_total_ratio=spot.peak_to_total_ratio,
            saturated=pose.core.clipped, saturated_area_px=pose.core.clipped_area_px,
        )
        assert det.flux > 0.0 and det.snr > 0.0 and det.fwhm_px >= sp.MIN_SUN_CORE_DIAMETER_PX
        assert pose.core.clipped is clipped

    prop()


def test_a_core_no_brighter_than_the_sky_median_is_refused() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg, bloom=24.0)
    horizon = int(round((H - 1) / 2.0 + (math.hypot(W, H) / 2.0 / (math.hypot(36.0, 24.0) / 2.0 / FOCAL35)) * math.tan(math.radians(sun.alt_deg - 15.0))))
    # Disconnected 4 x 4 clipped blocks separated by 1 px gridlines fill over half the sky without joining the core
    grid = np.full((horizon - 40, W - 4), 200.0)
    for r in range(2, grid.shape[0] - 4, 5):
        for c in range(2, grid.shape[1] - 4, 5):
            grid[r : r + 4, c : c + 4] = 255.0
    cx = int(round((W - 1) / 2.0))
    sun_row = int(round((H - 1) / 2.0 - (math.hypot(W, H) / 2.0 / (math.hypot(36.0, 24.0) / 2.0 / FOCAL35)) * math.tan(math.radians(15.0))))
    grid[sun_row - 20 : sun_row + 20, cx - 20 : cx + 20] = 200.0
    yy, xx = np.mgrid[0 : horizon - 40, 2 : W - 2]
    grid[np.hypot(xx - cx, yy - sun_row) <= 12.0] = 255.0
    frame[: horizon - 40, 2 : W - 2] = grid
    refused = sp.sun_horizon_pose(frame, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(refused, Refusal) and refused.kind == "no_sun"


@pytest.mark.parametrize("level", [0.0, 128.0, MIN_PEAK_LUMA - 0.5])
@pytest.mark.parametrize("focal", [24.0, 500.0])
def test_a_uniform_frame_under_the_peak_floor_reports_no_sun(level: float, focal: float) -> None:
    flat = np.full((H, W), level, dtype=np.float64)
    core = sp.find_sun_core(flat, max_diameter_px=500.0)
    assert isinstance(core, Refusal) and core.kind == "no_sun" and core.reason == sp.NO_SUN
    pose = sp.sun_horizon_pose(flat, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=focal)
    assert isinstance(pose, Refusal) and pose.kind == "no_sun" and pose.reason == sp.NO_SUN


@pytest.mark.parametrize("level", [MIN_PEAK_LUMA, 255.0])
def test_a_uniform_bright_frame_is_glare_too_wide_or_at_the_edge_never_a_pose(level: float) -> None:
    # A frame bright all over (blown out) is all glare: too wide for the Sun through a short lens, touching the
    # frame's edge through a long one; both sentences hold for it, "no bright region" would not
    flat = np.full((H, W), level, dtype=np.float64)
    core = sp.find_sun_core(flat, max_diameter_px=500.0)
    assert isinstance(core, Refusal) and core.kind == "sun_bloom" and core.reason == sp.SUN_BLOOM
    short = sp.sun_horizon_pose(flat, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=24.0)
    assert isinstance(short, Refusal) and short.kind == "sun_bloom" and short.reason == sp.sun_bloom_reason(24.0)
    long = sp.sun_horizon_pose(flat, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=500.0)
    assert isinstance(long, Refusal) and long.kind == "sun_edge" and long.reason == sp.SUN_EDGE


def test_a_nan_frame_reports_no_sun() -> None:
    frame = np.full((H, W), 255.0, dtype=np.float64)
    frame[0, 0] = math.nan
    core = sp.find_sun_core(frame, max_diameter_px=500.0)
    assert isinstance(core, Refusal) and core.kind == "no_sun" and core.reason == sp.NO_SUN


def test_a_dim_smudge_below_min_peak_luma_is_refused_as_no_sun() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    f_px = math.hypot(W, H) / 2.0 / (math.hypot(36.0, 24.0) / 2.0 / FOCAL35)
    cx, cy = (W - 1) / 2.0, (H - 1) / 2.0
    centre_alt = sun.alt_deg - 15.0
    sun_row = cy - f_px * math.tan(math.radians(sun.alt_deg - centre_alt))
    horizon = int(round(cy + f_px * math.tan(math.radians(centre_alt))))
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    dark = np.full((H, W), 5.5, dtype=np.float64)
    dark[np.hypot(xx - cx, yy - sun_row) <= 15.0] = 16.0
    dark[horizon:] = 0.0
    refused = sp.sun_horizon_pose(dark, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(refused, Refusal) and refused.kind == "no_sun" and refused.reason == sp.NO_SUN


@pytest.mark.parametrize(
    ("moment", "lat", "lon"),
    [
        ("2031-06-21T12:00:00Z", 90.0, 0.0),
        ("2031-06-21T12:00:00Z", 90.0, 180.0),
        ("2031-12-21T12:00:00Z", -90.0, -180.0),
    ],
)
def test_polar_day_sun_pose_holds_at_geographic_poles_and_antimeridian(moment: str, lat: float, lon: float) -> None:
    sun = sp.topocentric_sun(moment, lat, lon)
    assert sun.alt_deg > sp.MIN_SUN_ALT_DEG
    pose = sp.sun_horizon_pose(
        _level_camera_frame(sun.alt_deg - 10.0, sun.alt_deg),
        moment=moment, lat=lat, lon=lon, orientation=None, focal_35mm_mm=FOCAL35,
    )
    assert isinstance(pose, sp.SunPose), pose
    assert 0.0 <= pose.estimate.center_az_deg < 360.0
