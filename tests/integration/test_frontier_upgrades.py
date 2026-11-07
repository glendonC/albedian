"""Integration and property tests for Frontier Upgrades 1 & 2 and Deep Scientific Engines:
- `services/media/silhouette.py`: `compute_2d_sky_silhouette_mask` & `deband_shadow_macroblocks`
- `services/science/optical_flow.py`: `compute_video_pose_trajectory` (`beacon_lock` & `silhouette_flow`)
- `services/science/radiometry.py`: Krisciunas & Schaefer (1991) lunar sky brightness at a stated target
- `services/science/coherent_stack.py`: `subtract_lunar_scattering_veil`, `integrate_wcs_coherent_star_trajectories`
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import numpy as np
import pytest
from scipy import ndimage

from services.api import app
from services.media.silhouette import (
    compute_2d_sky_silhouette_mask,
    deband_shadow_macroblocks,
)
from services.science.coherent_stack import (
    integrate_wcs_coherent_star_trajectories,
    subtract_lunar_scattering_veil,
)
from services.science.optical_flow import compute_video_pose_trajectory
from services.science.radiometry import (
    compute_krisciunas_schaefer_sky_brightness,
    compute_lunar_phase_and_illumination,
)
from services.science.solver import render_synthetic_starfield


def test_frontier_2d_sky_silhouette_mask_traces_protruding_structures() -> None:
    """Verify `compute_2d_sky_silhouette_mask` traces a non-flat 2D horizon contour
    (including a protruding tent/palm silhouette) and produces a feathered `[0, 1]` sky mask.
    """
    h, w = 180, 240
    frame = np.full((h, w), 0.03, dtype=np.float64)

    true_contour = np.full(w, 112.0, dtype=np.float64)
    for x in range(75, 130):
        bump = 44.0 * np.exp(-0.5 * ((x - 102.0) / 11.0) ** 2)
        true_contour[x] = 112.0 - bump

    yy = np.arange(h, dtype=np.float64)[:, None]
    is_fg = yy >= true_contour[None, :]
    frame[is_fg] = 0.42

    frame[28, 45] = 0.85
    frame[34, 190] = 0.78

    sky_mask_2d, contour_y = compute_2d_sky_silhouette_mask(frame, feather_px=12.0)

    assert sky_mask_2d.shape == (h, w)
    assert contour_y.shape == (w,)
    assert sky_mask_2d.dtype == np.float64
    assert float(np.min(sky_mask_2d)) >= 0.0
    assert float(np.max(sky_mask_2d)) <= 1.0

    assert abs(float(np.median(contour_y[:50])) - 112.0) <= 6.0
    assert abs(float(np.median(contour_y[160:])) - 112.0) <= 6.0
    assert float(contour_y[102]) <= 82.0

    assert float(np.mean(sky_mask_2d[:20, :])) > 0.99
    assert float(np.mean(sky_mask_2d[145:, :])) < 0.01


def test_frontier_deband_shadow_macroblocks_preserves_stars_and_smooths_terraces() -> None:
    """Verify `deband_shadow_macroblocks` suppresses 8-bit quantization staircase steps in
    ultra-dark sky regions (`luma < 0.12`) while preserving bright point sources (`> 2.5 sigma`)
    and foreground regions (`sky_mask <= 0.5`).
    """
    h, w = 120, 160
    yy, _ = np.mgrid[0:h, 0:w]
    continuous_ramp = 0.02 + 0.07 * (yy / float(h))
    quantized_sky = np.round(continuous_ramp * 64.0) / 64.0

    sky_mask = np.ones((h, w), dtype=np.float64)
    sky_mask[90:, :] = 0.0
    quantized_sky[90:, :] = 0.35

    star_y, star_x = 35, 80
    quantized_sky[star_y - 1 : star_y + 2, star_x - 1 : star_x + 2] += np.array(
        [[0.08, 0.18, 0.08], [0.18, 0.55, 0.18], [0.08, 0.18, 0.08]], dtype=np.float64
    )
    peak_before = float(quantized_sky[star_y, star_x])
    fg_before = quantized_sky[95:, :].copy()

    debanded = deband_shadow_macroblocks(quantized_sky, sky_mask, seed=42)

    assert abs(float(debanded[star_y, star_x]) - peak_before) < 1e-9
    np.testing.assert_allclose(debanded[95:, :], fg_before, atol=1e-12)

    patch_slice = (slice(10, 28), slice(10, 60))
    err_quantized = float(np.sqrt(np.mean((quantized_sky[patch_slice] - continuous_ramp[patch_slice]) ** 2)))
    err_debanded = float(np.sqrt(np.mean((debanded[patch_slice] - continuous_ramp[patch_slice]) ** 2)))
    assert err_debanded < err_quantized


def test_frontier_optical_flow_beacon_lock_and_silhouette_flow() -> None:
    """Verify `compute_video_pose_trajectory` accurately tracks `(duPx, dvPx, drollDeg)`
    in both `beacon_lock` mode and `silhouette_flow` mode.
    """
    h, w = 160, 240
    yy, xx = np.mgrid[0:h, 0:w]

    contour_0 = 96.0 - 14.0 * np.exp(-0.5 * ((np.arange(w) - 110.0) / 14.0) ** 2)
    base_moonless = np.where(yy >= contour_0[None, :], 85.0, 12.0).astype(np.float64)
    base_moonless = ndimage.gaussian_filter(base_moonless, sigma=1.2)

    beacon_psf = 210.0 * np.exp(-((xx - 170.0) ** 2 + (yy - 36.0) ** 2) / (2.0 * 2.2**2))
    base_with_beacon = base_moonless + beacon_psf

    true_shifts = [(0.0, 0.0), (4.25, -2.5), (-3.0, 1.75)]
    beacon_frames = [
        ndimage.shift(base_with_beacon, shift=(dv, du), order=3, mode="nearest")
        for du, dv in true_shifts
    ]
    traj_beacon = compute_video_pose_trajectory(beacon_frames, base_horizon_y=96.0, fps=1.0)
    assert len(traj_beacon) == 3
    for idx, (du_true, dv_true) in enumerate(true_shifts):
        step = traj_beacon[idx]
        assert step["frameIndex"] == idx
        assert step["trackingMode"] == "beacon_lock"
        assert abs(float(step["duPx"]) - du_true) <= 0.45
        assert abs(float(step["dvPx"]) - dv_true) <= 0.45

    moonless_frames = [
        ndimage.shift(base_moonless, shift=(dv, du), order=3, mode="nearest")
        for du, dv in true_shifts
    ]
    traj_sil = compute_video_pose_trajectory(moonless_frames, base_horizon_y=96.0, fps=1.0)
    assert len(traj_sil) == 3
    for idx, (du_true, dv_true) in enumerate(true_shifts):
        step = traj_sil[idx]
        assert step["frameIndex"] == idx
        assert step["trackingMode"] == "silhouette_flow"
        assert abs(float(step["duPx"]) - du_true) <= 0.65
        assert abs(float(step["dvPx"]) - dv_true) <= 0.65


@pytest.mark.owner_media
def test_frontier_discovery_gives_each_clip_a_pose_trajectory_radiometry_and_deep_sky() -> None:
    """GET /api/media/discover over the owner's clips in data/uploads, which are not in git."""
    client = TestClient(app)
    resp = client.get("/api/media/discover")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Flags with fixed messages where a failing assert would print the clips' names, places or capture times
    count = len(items)
    assert count >= 3, "fewer than three real clips were discovered"
    for item in items:
        has_trajectory = "poseTrajectory" in item
        assert has_trajectory, "a real clip has no pose trajectory"
        assert len(item["poseTrajectory"]) >= 1
        assert item["predictedSkyBrightness"]["evidenceClass"] == "predicted"
        lists_deep_sky = "deepSkyObjects" in item
        assert not lists_deep_sky, "a real clip lists deep-sky objects"


def test_deep_engines_sky_brightness_and_coherent_stack() -> None:
    """Verify Krisciunas & Schaefer (1991) lunar sky brightness, lunar veil
    subtraction, and WCS-coherent trajectory stacking.
    """
    # 1. Radiometry (`services/science/radiometry.py`)
    # A synthetic moment and place that point at nobody (the open North Atlantic, about 1,321 km from
    # Flores in the Azores), with the Moon at 33 degrees altitude by the repo's ephemeris
    obs_utc = "2030-11-01T22:32:00Z"
    obs_lat, obs_lon = 30.0, -40.0
    phase = compute_lunar_phase_and_illumination(obs_utc)
    assert 0.0 <= float(phase["illuminationFraction"]) <= 1.0
    assert phase["phaseName"] in (
        "Waxing Gibbous",
        "First Quarter",
        "Full Moon",
        "Waning Gibbous",
    )

    ks_moonlit = compute_krisciunas_schaefer_sky_brightness(
        sep_from_moon_deg=8.2,
        target_alt_deg=32.0,
        moon_alt_deg=33.0,
        obs_utc_iso=obs_utc,
    )
    ks_moonless = compute_krisciunas_schaefer_sky_brightness(
        sep_from_moon_deg=8.2,
        target_alt_deg=32.0,
        moon_alt_deg=-15.0,
        obs_utc_iso=obs_utc,
    )
    assert ks_moonlit["skyBrightnessMagArcsec2"] < ks_moonless["skyBrightnessMagArcsec2"]
    assert ks_moonlit["lunarVeilDeltaMag"] > 1.5

    # 2. Lunar Scattering Veil Subtraction & WCS Coherent Trajectory Photometry (`services/science/coherent_stack.py`)
    h, w = 180, 240
    yy, xx = np.mgrid[0:h, 0:w]
    moon_x, moon_y = 180.0, 40.0
    rr = np.hypot(xx - moon_x, yy - moon_y)
    sky_mask = np.ones((h, w), dtype=np.float64)
    sky_mask[130:, :] = 0.0

    # Synthetic sky with radial lunar scattering glare + lunar core
    veil_true = 0.35 * np.exp(-rr / 65.0)
    moon_core = 0.90 * np.exp(-(rr**2) / (2.0 * 4.0**2))
    glare_img = np.where(sky_mask > 0.5, 0.04 + veil_true + moon_core, 0.25)

    cleaned_luma, veil_stats = subtract_lunar_scattering_veil(
        glare_img, moon_x_px=moon_x, moon_y_px=moon_y, sky_mask=sky_mask
    )
    assert veil_stats["veilPeakSubtracted"] > 0.05
    assert veil_stats["contrastGainRatio"] > 1.2
    # Lunar core (at moon_x, moon_y) must remain bright
    assert float(cleaned_luma[int(moon_y), int(moon_x)]) > 0.85

    # Test WCS-coherent trajectory integration across 6 shifted frames
    shifts = [(0.0, 0.0), (2.0, -1.0), (4.0, -2.0), (-2.0, 1.5), (-4.0, 3.0), (1.0, -2.5)]
    synth_frames = []
    wcs_model = None
    for idx, (du, dv) in enumerate(shifts):
        f_i, wcs_model, _ = render_synthetic_starfield(
            width=320,
            height=180,
            read_noise_sigma=2.2,
            flux_scale=650.0,
            seed=500 + idx,
            shift_yx=(dv, du),
        )
        synth_frames.append(f_i)

    assert wcs_model is not None
    pose_traj = [
        {"frameIndex": idx, "timeSec": float(idx), "duPx": du, "dvPx": dv, "drollDeg": 0.0}
        for idx, (du, dv) in enumerate(shifts)
    ]
    # The render's own WCS is exact by construction, standing in for a held-out-verified fit, and the frames are
    # the render's original pixels
    coherent_results = integrate_wcs_coherent_star_trajectories(
        synth_frames, wcs=wcs_model, pose_trajectory=pose_traj, wcs_verified=True, original_pixels=True, max_stars=6
    )
    assert len(coherent_results) >= 4
    detected = [r for r in coherent_results if r["status"] == "coherent_signal_detected"]
    assert len(detected) >= 3
    assert all(r["coherentSnr"] >= 3.0 for r in detected)

    # 4. FastAPI endpoint GET /api/science/radiometry-audit
    client = TestClient(app)
    assert client.get("/api/science/radiometry-audit").status_code == 422
    moment = {"obs_utc_iso": obs_utc, "lat_deg": obs_lat, "lon_deg": obs_lon}
    # No target, no prediction: there is no default 8.2 degrees from the Moon
    assert client.get("/api/science/radiometry-audit", params=moment).status_code == 422
    r_rad = client.get(
        "/api/science/radiometry-audit", params={**moment, "target_sep_deg": 8.2, "target_alt_deg": 32.0}
    )
    assert r_rad.status_code == 200
    rad_json = r_rad.json()
    assert "lunarPhase" in rad_json
    assert rad_json["predictedSkyBrightness"]["evidenceClass"] == "predicted"
    assert "sensorAudit" not in rad_json
