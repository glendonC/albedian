"""Tests for refine-2 in `services/science/phone_photo_solver.py`:
- Few-pair distortion shrinkage `(50.0, 120.0)` when `3 <= len(train_pairs) < 5` (`(25.0, 60.0)` unchanged at `>= 5`).
- `remeasure_on_stored_pixels`: pure `observed` pixel-moment remeasurement inside `(5k x 5k)` stored-pixel patches.
- `restate_binned_phone_wcs`: exact block-centre `crpix` preservation (`(b + 0.5) * k - 0.5`), focal length, and
  stored-frame diagonal field of view when `width % k != 0` or `height % k != 0` (Hypothesis PBT).
- `refine_binned_phone_solution`: full-resolution stored-pixel remeasurement, tight-radius (`max(4.8, 3.0 * k)`)
  refinement, unchanged `MAX_HELD_OUT_MEDIAN_PX = 3.0` coincidence gate, and clean `unresolved` degradation.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math

from hypothesis import assume, example, given, settings, strategies as st
import numpy as np
import pytest

from services.science import detector
from services.science import phone_photo_solver as pps
from services.science.catalog import ALL_SKY_CATALOG_STARS, CatalogStar, angular_separation_deg
from services.science.wcs_models import (
    DetectionQualityRecord,
    FrameMeasures,
    ObservationSolution,
    SolutionQuality,
    SourceDetection,
    StarProfileRecord,
    WCSModel,
)


def _synthetic_catalog_patch(ra0_deg: float = 83.0, dec0_deg: float = -5.0) -> list[CatalogStar]:
    """Deterministic grid of synthetic `CatalogStar` entries spanning centre to corners around `(ra0_deg, dec0_deg)`."""
    template = ALL_SKY_CATALOG_STARS[0]
    stars_out: list[CatalogStar] = []
    idx = 0
    for d_dec in np.linspace(-24.0, 24.0, 5):
        for d_ra in np.linspace(-30.0, 30.0, 5):
            idx += 1
            ra = (ra0_deg + float(d_ra)) % 360.0
            dec = max(-85.0, min(85.0, dec0_deg + float(d_dec)))
            stars_out.append(
                replace(
                    template,
                    id=f"syn-star-{idx:02d}",
                    common_name=f"SynStar{idx:02d}",
                    bayer_designation=None,
                    flamsteed_number=None,
                    names_file_designation=None,
                    catalog_source_id=f"HIP {900000 + idx}",
                    ra_deg=ra,
                    dec_deg=dec,
                    mag=2.2 + 0.12 * idx,
                )
            )
    return stars_out


def test_unchanged_held_out_median_gate_and_refine2_constants() -> None:
    assert pps.MAX_HELD_OUT_MEDIAN_PX == 3.0
    assert pps.MIN_HELD_OUT_INLIERS == 6
    assert pps.DISTORTION_REG_WEIGHTS == (25.0, 60.0)
    assert pps.FEW_PAIR_DISTORTION_MIN_PAIRS == 5
    assert pps.FEW_PAIR_DISTORTION_REG_WEIGHTS == (50.0, 120.0)


def test_few_pair_distortion_shrinkage_recovers_distorted_camera_under_3px_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disable deep-tile naming refusal lookup on synthetic star IDs so synthetic catalog_stars test pure solver+gate
    monkeypatch.setattr(pps, "naming_refusals", lambda wcs, named, gate_px, checked_xy, epoch_jyear, deep=None: [None] * len(named))

    width, height = 5392, 3592
    utc = "2031-01-15T04:00:00Z"
    lat, lon = 28.5, -17.8
    sky_frame = pps._ApparentSkyFrame.from_iso(utc, lat, lon)
    # Point camera straight at zenith in ENU so all synthetic stars around zenith lie well above horizon
    rot_true = np.eye(3, dtype=np.float64)  # rows right, down, axis: a right-handed camera at the zenith
    zen_ra, zen_dec = sky_frame.sky_vec_to_icrs(np.array([[0.0, 0.0, 1.0]], dtype=np.float64))
    cat = _synthetic_catalog_patch(float(zen_ra[0]), float(zen_dec[0]))

    true_wcs = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot_true,
        fov_diagonal_deg=81.1,
        width=width,
        height=height,
        lens_model="brown-conrady",
        k1=-0.009,
        k2=0.0005,
        sky_frame=sky_frame,
    )
    # Keep 10 visible stars spanning centre to edge (so tight_init has 10 -> _training_count(10) == 4 < 5, held_out == 6)
    vis_stars: list[CatalogStar] = []
    dets: list[SourceDetection] = []
    for st_item in cat:
        px, py = true_wcs.world_to_pixel(st_item.ra_deg, st_item.dec_deg)
        if math.isfinite(px) and 120.0 <= px <= width - 120.0 and 100.0 <= py <= height - 100.0:
            vis_stars.append(st_item)
            dets.append(
                SourceDetection(
                    x_px=px,
                    y_px=py,
                    flux=500.0 - 15.0 * len(dets),
                    snr=40.0,
                    fwhm_px=3.1,
                    centroid_uncertainty_px=0.08,
                    asset_id="src_syn",
                    frame_time_sec=0.0,
                )
            )
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    order = sorted(range(len(dets)), key=lambda i: math.hypot(dets[i].x_px - cx, dets[i].y_px - cy), reverse=True)
    step_idx = np.linspace(0, len(order) - 1, 12, dtype=int).tolist()
    chosen = [order[j] for j in step_idx]
    dets_10 = [dets[i] for i in chosen]
    stars_10 = [vis_stars[i] for i in chosen]
    assert pps._training_count(int(0.85 * len(dets_10))) == 4

    # 1. End-to-end solve_phone_photo with seed_axes_icrs and 4 training pairs calibrates under unchanged 3.0 px gate
    # (on parent main, len(train_tuples) == 4 < 5 skips distortion and fails the 3.0 px held-out gate):
    seed_axes = [
        true_wcs.pixel_to_world(cx, cy),
        true_wcs.pixel_to_world(cx + 20.0, cy),
        true_wcs.pixel_to_world(cx, cy + 20.0),
    ]
    sky_img = np.full((height, width), 10, dtype=np.uint8)
    fm = FrameMeasures()
    dets_with_fm = [replace(d, frame=fm) for d in dets_10]
    sol = pps.solve_phone_photo(
        sky_img,
        detections=dets_with_fm,
        obs_utc_iso=utc,
        observer_lat=lat,
        observer_lon=lon,
        fov_diagonal_deg=81.1,
        catalog_stars=stars_10,
        source_asset_id="src_syn",
        source_sha256="a" * 64,
        seed_axes_icrs=seed_axes,
    )
    assert sol.status == "calibrated", (sol.status, sol.quality)
    assert sol.quality.held_out_inliers >= 6
    assert sol.quality.median_residual_px is not None and sol.quality.median_residual_px <= 3.0

    # 2. Direct _refine_phone_camera_wcs check on 4 and 5 pairs:
    init_wcs = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot_true,
        fov_diagonal_deg=81.1,
        width=width,
        height=height,
        lens_model="brown-conrady",
        k1=0.0,
        k2=0.0,
        sky_frame=sky_frame,
    )
    interleaved = [*range(0, 10, 2), *range(1, 10, 2)]
    train_idx = interleaved[:4]
    held_idx = [i for i in range(10) if i not in set(train_idx)]
    train_4 = [(dets_10[i], stars_10[i].ra_deg, stars_10[i].dec_deg) for i in train_idx]

    assert pps._refine_phone_camera_wcs(init_wcs, train_4[:2], fit_distortion=True) is init_wcs

    rigid_4 = pps._refine_phone_camera_wcs(init_wcs, train_4, fit_distortion=False)
    shrunk_4 = pps._refine_phone_camera_wcs(rigid_4, train_4, fit_distortion=True)
    loose_4 = pps._refine_phone_camera_wcs(rigid_4, train_4, fit_distortion=True, distortion_regularization=(25.0, 60.0))
    rigid_held_med = float(
        np.median([math.hypot(*(np.subtract(rigid_4.world_to_pixel(stars_10[i].ra_deg, stars_10[i].dec_deg), (dets_10[i].x_px, dets_10[i].y_px)))) for i in held_idx])
    )
    shrunk_held_med = float(
        np.median([math.hypot(*(np.subtract(shrunk_4.world_to_pixel(stars_10[i].ra_deg, stars_10[i].dec_deg), (dets_10[i].x_px, dets_10[i].y_px)))) for i in held_idx])
    )
    assert rigid_held_med > 3.0
    assert shrunk_held_med < 2.0
    assert math.hypot(shrunk_4.dist_k1, shrunk_4.dist_k2) < math.hypot(loose_4.dist_k1, loose_4.dist_k2)

    train_5 = [(dets_10[i], stars_10[i].ra_deg, stars_10[i].dec_deg) for i in interleaved[:5]]
    def_5 = pps._refine_phone_camera_wcs(rigid_4, train_5, fit_distortion=True)
    exp_5 = pps._refine_phone_camera_wcs(rigid_4, train_5, fit_distortion=True, distortion_regularization=(25.0, 60.0))
    assert def_5.dist_k1 == pytest.approx(exp_5.dist_k1, abs=1e-12)
    assert def_5.dist_k2 == pytest.approx(exp_5.dist_k2, abs=1e-12)


def test_three_training_pairs_fit_shrunk_distortion_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11 pairs leave 9 tight ones, so 3 train (`_training_count(9) == 3`) and 8 are held out; main fits no distortion
    # below 5 training pairs, refine-2 from 3
    monkeypatch.setattr(pps, "naming_refusals", lambda wcs, named, gate_px, checked_xy, epoch_jyear, deep=None: [None] * len(named))
    width, height = 5392, 3592
    utc, lat, lon = "2031-01-15T04:00:00Z", 28.5, -17.8
    sky_frame = pps._ApparentSkyFrame.from_iso(utc, lat, lon)
    zen_ra, zen_dec = sky_frame.sky_vec_to_icrs(np.array([[0.0, 0.0, 1.0]], dtype=np.float64))
    cat = _synthetic_catalog_patch(float(zen_ra[0]), float(zen_dec[0]))
    true_wcs = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3, dtype=np.float64), fov_diagonal_deg=81.1, width=width, height=height,
        lens_model="brown-conrady", k1=-0.009, k2=0.0005, sky_frame=sky_frame,
    )
    pairs = []
    for st_item in cat:
        px, py = true_wcs.world_to_pixel(st_item.ra_deg, st_item.dec_deg)
        if math.isfinite(px) and 120.0 <= px <= width - 120.0 and 100.0 <= py <= height - 100.0:
            pairs.append((st_item, px, py))
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    pairs.sort(key=lambda item: math.hypot(item[1] - cx, item[2] - cy), reverse=True)
    chosen = [pairs[j] for j in np.linspace(0, len(pairs) - 1, 11, dtype=int).tolist()]
    assert pps._training_count(max(6, int(0.85 * len(chosen)))) == 3
    dets = [
        SourceDetection(
            x_px=px, y_px=py, flux=500.0 - 15.0 * i, snr=40.0, fwhm_px=3.1, centroid_uncertainty_px=0.08,
            asset_id="src_syn", frame_time_sec=0.0, frame=FrameMeasures(),
        )
        for i, (_, px, py) in enumerate(chosen)
    ]
    seed_axes = [true_wcs.pixel_to_world(cx, cy), true_wcs.pixel_to_world(cx + 20.0, cy), true_wcs.pixel_to_world(cx, cy + 20.0)]
    fits: list[int] = []
    real_refine = pps._refine_phone_camera_wcs

    def counting(initial_wcs, train_pairs, *, fit_distortion=None, distortion_regularization=None):
        if fit_distortion is True:
            fits.append(len(train_pairs))
        return real_refine(initial_wcs, train_pairs, fit_distortion=fit_distortion, distortion_regularization=distortion_regularization)

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", counting)
    sol = pps.solve_phone_photo(
        np.full((height, width), 10, dtype=np.uint8), detections=dets, obs_utc_iso=utc, observer_lat=lat,
        observer_lon=lon, fov_diagonal_deg=81.1, catalog_stars=[s for s, _, _ in chosen], source_asset_id="src_syn",
        source_sha256="b" * 64, seed_axes_icrs=seed_axes,
    )
    assert sol.status == "calibrated", (sol.status, sol.quality)
    assert 3 in fits
    assert sol.quality.median_residual_px is not None and sol.quality.median_residual_px <= 3.0


def test_remeasure_on_stored_pixels_moments_recentering_saturation_and_edges() -> None:
    k = 3
    h, w = 181, 242  # h % 3 == 1, w % 3 == 2
    yy, xx = np.mgrid[0:h, 0:w]
    stored_bg = np.full((h, w), 0.04, dtype=np.float64)
    stored_luma = stored_bg.copy()

    # Star 1: off block-centre at (62.35, 55.70) -> binned block (20, 18) has centre (61.0, 55.0), testing recentering!
    true_x1, true_y1 = 62.35, 55.70
    stored_luma += 0.55 * np.exp(-((xx - true_x1) ** 2 + (yy - true_y1) ** 2) / (2.0 * 2.6**2))

    # Star 2: clipped core (>= 2 pixels at 1.0) at (150.2, 110.4)
    true_x2, true_y2 = 150.2, 110.4
    stored_luma += 1.40 * np.exp(-((xx - true_x2) ** 2 + (yy - true_y2) ** 2) / (2.0 * 3.0**2))

    # Star 3: near the `half = 7` boundary at x0 == 7 with peak slightly inward at x = 6.3 (rx0 == 6 < half), testing
    # the boundary guard when recentering would step outside `half`!
    stored_luma += 0.45 * np.exp(-((xx - 6.3) ** 2 + (yy - 90.0) ** 2) / (2.0 * 1.8**2))

    # Star 4: exactly one pixel at the top code (the detector's clipped core needs CLIPPED_CORE_MIN_PX == 2)
    stored_luma += 0.97 * np.exp(-((xx - 100.0) ** 2 + (yy - 150.0) ** 2) / (2.0 * 2.0**2))

    # Star 5: 4 px from the right edge, inside no full patch (dropped, never measured on a cut patch)
    stored_luma += 0.50 * np.exp(-((xx - 238.0) ** 2 + (yy - 30.0) ** 2) / (2.0 * 2.0**2))

    stored_luma = np.clip(stored_luma, 0.0, 1.0)
    assert int(np.count_nonzero(stored_luma[140:161, 90:111] >= 1.0)) == 1
    fm = FrameMeasures(profile_sigma_major_px=1.3, profile_sigma_minor_px=1.1)

    def bpos(s_px: float) -> float:
        return (s_px + 0.5) / k - 0.5

    binned_dets = [
        SourceDetection(x_px=20.0, y_px=18.0, flux=2.0, snr=18.0, fwhm_px=2.1, centroid_uncertainty_px=0.2, asset_id="src_1", frame=fm),
        SourceDetection(x_px=bpos(true_x2), y_px=bpos(true_y2), flux=5.0, snr=35.0, fwhm_px=2.5, centroid_uncertainty_px=0.1, asset_id="src_1", frame=fm),
        SourceDetection(x_px=bpos(7.0), y_px=bpos(90.0), flux=1.5, snr=12.0, fwhm_px=1.8, centroid_uncertainty_px=0.2, asset_id="src_1", frame=fm),
        # Out-of-margin border detection (dropped)
        SourceDetection(x_px=0.2, y_px=0.2, flux=1.0, snr=8.0, fwhm_px=2.0, centroid_uncertainty_px=0.3, asset_id="src_1", frame=fm),
        # Pure flat background patch with zero positive residual (dropped)
        SourceDetection(x_px=bpos(200.0), y_px=bpos(40.0), flux=1.0, snr=8.0, fwhm_px=2.0, centroid_uncertainty_px=0.3, asset_id="src_1", frame=fm),
        SourceDetection(x_px=bpos(100.0), y_px=bpos(150.0), flux=3.0, snr=30.0, fwhm_px=1.6, centroid_uncertainty_px=0.1, asset_id="src_1", frame=fm),
        SourceDetection(x_px=bpos(238.0), y_px=bpos(30.0), flux=2.0, snr=20.0, fwhm_px=1.6, centroid_uncertainty_px=0.1, asset_id="src_1", frame=fm),
    ]

    rem, kept_idx = pps.remeasure_on_stored_pixels(
        stored_luma, stored_bg, 0.004, binned_dets, k, "src_override", return_indices=True
    )
    assert kept_idx == [0, 1, 2, 5]
    assert len(rem) == 4

    # Star 4's single clipped pixel is not a clipped core
    assert math.hypot(rem[3].x_px - 100.0, rem[3].y_px - 150.0) < 0.1
    assert not rem[3].saturated and rem[3].saturated_area_px == 0

    # Star 1 recovered to < 0.12 px despite starting 1.52 px away at binned integer centre (61.0, 55.0)
    assert math.hypot(rem[0].x_px - true_x1, rem[0].y_px - true_y1) < 0.12
    assert not rem[0].saturated and rem[0].saturated_area_px == 0
    assert rem[0].asset_id == "src_override"
    assert rem[0].frame is not None
    assert rem[0].frame.profile_sigma_major_px == pytest.approx(1.3 * k)
    assert rem[0].frame.profile_sigma_minor_px == pytest.approx(1.1 * k)

    # Star 2 saturated core detected
    assert math.hypot(rem[1].x_px - true_x2, rem[1].y_px - true_y2) < 0.15
    assert rem[1].saturated and rem[1].saturated_area_px >= 2

    # Scale invariance on 0..255 luma/bg/sigma:
    rem_255 = pps.remeasure_on_stored_pixels(stored_luma * 255.0, stored_bg * 255.0, 0.004 * 255.0, binned_dets, k)
    assert len(rem_255) == 4
    assert rem_255[0].x_px == pytest.approx(rem[0].x_px, abs=1e-9)
    assert rem_255[0].y_px == pytest.approx(rem[0].y_px, abs=1e-9)
    assert rem_255[1].saturated is True and rem_255[1].saturated_area_px == rem[1].saturated_area_px

    # k == 1 identity and validation guards
    assert pps.remeasure_on_stored_pixels(stored_luma, stored_bg, 0.004, binned_dets[:2], 1) == binned_dets[:2]
    with pytest.raises(ValueError, match="at least 1"):
        pps.remeasure_on_stored_pixels(stored_luma, stored_bg, 0.004, binned_dets, 0)
    with pytest.raises(ValueError, match="same shape"):
        pps.remeasure_on_stored_pixels(stored_luma, stored_bg[:10, :10], 0.004, binned_dets, 3)


@settings(max_examples=80, deadline=None)
@given(
    width=st.integers(min_value=600, max_value=9500),
    height=st.integers(min_value=600, max_value=9500),
    k=st.integers(min_value=2, max_value=5),
    fov_deg=st.floats(min_value=35.0, max_value=130.0, allow_nan=False, allow_infinity=False),
    lens_model=st.sampled_from(["brown-conrady", "kannala-brandt"]),
    k1=st.floats(min_value=-0.04, max_value=0.04, allow_nan=False, allow_infinity=False),
    k2=st.floats(min_value=-0.015, max_value=0.015, allow_nan=False, allow_infinity=False),
    u_frac=st.floats(min_value=-0.42, max_value=0.42, allow_nan=False, allow_infinity=False),
    v_frac=st.floats(min_value=-0.42, max_value=0.42, allow_nan=False, allow_infinity=False),
)
@example(width=6944, height=9280, k=3, fov_deg=80.168, lens_model="brown-conrady", k1=0.0589, k2=-0.0358, u_frac=0.38, v_frac=-0.38)
def test_pbt_restate_binned_phone_wcs_preserves_exact_block_centre_projection(
    width: int,
    height: int,
    k: int,
    fov_deg: float,
    lens_model: str,
    k1: float,
    k2: float,
    u_frac: float,
    v_frac: float,
) -> None:
    bw, bh = width // k, height // k
    binned_wcs = pps.PhoneCameraWCS.from_pointing(
        crval_ra_deg=120.0,
        crval_dec_deg=25.0,
        rotation_deg=14.0,
        fov_diagonal_deg=fov_deg,
        width=bw,
        height=bh,
        lens_model=lens_model,
        k1=k1,
        k2=k2,
    )
    # The claim holds where the lens model can describe the stored frame's corner; past its valid radius the field
    # is held at the valid edge (test_a_stored_corner_past_the_lens_models_reach_keeps_the_exact_focal)
    assume(_stored_corner_inside_the_model(binned_wcs, k, width, height))
    restated = pps.restate_binned_phone_wcs(binned_wcs, k, width, height)
    assert restated.width == width and restated.height == height
    assert restated.focal_px == pytest.approx(binned_wcs.focal_px * k, rel=1e-12)
    assert restated.scale_deg_per_px == pytest.approx(binned_wcs.scale_deg_per_px / k, rel=1e-12)
    assert restated.crpix_x == pytest.approx((binned_wcs.crpix_x + 0.5) * k - 0.5, abs=1e-12)
    assert restated.crpix_y == pytest.approx((binned_wcs.crpix_y + 0.5) * k - 0.5, abs=1e-12)

    bx = (bw - 1) / 2.0 + u_frac * bw
    by = (bh - 1) / 2.0 + v_frac * bh
    ra, dec = binned_wcs.pixel_to_world(bx, by)
    bx_rt, by_rt = binned_wcs.world_to_pixel(ra, dec)
    assert math.isfinite(bx_rt) and math.isfinite(by_rt)

    sx, sy = restated.world_to_pixel(ra, dec)
    assert sx == pytest.approx((bx_rt + 0.5) * k - 0.5, abs=1e-8)
    assert sy == pytest.approx((by_rt + 0.5) * k - 0.5, abs=1e-8)

    ra_s, dec_s = restated.pixel_to_world(sx, sy)
    assert angular_separation_deg(ra, dec, ra_s, dec_s) < 1e-5

    focal_from_fov = pps._focal_for_diagonal_fov(width, height, restated.fov_diagonal_deg, lens_model, k1, k2)
    assert focal_from_fov == pytest.approx(restated.focal_px, rel=1e-5)


def test_restate_and_refine_binned_phone_solution_end_to_end_and_gate_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pps, "naming_refusals", lambda wcs, named, gate_px, checked_xy, epoch_jyear, deep=None: [None] * len(named))

    # Non-multiple-of-3 stored dimensions: 1802 % 3 == 2, 1351 % 3 == 1
    k = 3
    width, height = 1802, 1351
    utc = "2031-02-10T03:00:00Z"
    lat, lon = 31.0, -25.0
    sky_frame = pps._ApparentSkyFrame.from_iso(utc, lat, lon)
    rot_true = np.eye(3, dtype=np.float64)  # rows right, down, axis: a right-handed camera at the zenith
    zen_ra, zen_dec = sky_frame.sky_vec_to_icrs(np.array([[0.0, 0.0, 1.0]], dtype=np.float64))
    cat = _synthetic_catalog_patch(float(zen_ra[0]), float(zen_dec[0]))

    true_wcs = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot_true,
        fov_diagonal_deg=80.2,
        width=width,
        height=height,
        lens_model="brown-conrady",
        k1=0.032,
        k2=-0.008,
        sky_frame=sky_frame,
    )
    rng = np.random.default_rng(12345)
    stored_img = rng.normal(14.0, 1.5, size=(height, width))
    yy, xx = np.mgrid[0:21, 0:21]
    vis_cat: list[CatalogStar] = []
    for st_item in cat:
        px, py = true_wcs.world_to_pixel(st_item.ra_deg, st_item.dec_deg)
        if math.isfinite(px) and 36.0 <= px <= width - 36.0 and 36.0 <= py <= height - 36.0:
            vis_cat.append(st_item)
            x0, y0 = int(round(px)) - 10, int(round(py)) - 10
            dx, dy = px - x0, py - y0
            amp = 210.0 - 4.0 * len(vis_cat)
            stored_img[y0 : y0 + 21, x0 : x0 + 21] += amp * np.exp(-((xx - dx) ** 2 + (yy - dy) ** 2) / (2.0 * 3.1**2))
    stored_u8 = np.clip(np.round(stored_img), 0, 255).astype(np.uint8)
    stored_rgb = np.repeat(stored_u8[:, :, None], 3, axis=2)

    bh_crop, bw_crop = (height // k) * k, (width // k) * k
    binned_rgb = np.round(stored_rgb[:bh_crop, :bw_crop].reshape(height // k, k, width // k, k, 3).mean(axis=(1, 3))).astype(np.uint8)
    dets_bin = detector.detect_point_sources(detector._to_luma_2d(binned_rgb.astype(np.float64)), asset_id="src_b14_syn")

    cx_b, cy_b = (width // k - 1) / 2.0, (height // k - 1) / 2.0
    binned_true = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot_true,
        fov_diagonal_deg=80.2,
        width=width // k,
        height=height // k,
        lens_model="brown-conrady",
        k1=0.032,
        k2=-0.008,
        sky_frame=sky_frame,
    )
    seed_b = [
        binned_true.pixel_to_world(cx_b, cy_b),
        binned_true.pixel_to_world(cx_b + 15.0, cy_b),
        binned_true.pixel_to_world(cx_b, cy_b + 15.0),
    ]
    sha = hashlib.sha256(stored_rgb.tobytes()).hexdigest()
    bin_sol = pps.solve_phone_photo(
        binned_rgb,
        detections=dets_bin,
        obs_utc_iso=utc,
        observer_lat=lat,
        observer_lon=lon,
        fov_diagonal_deg=80.2,
        catalog_stars=vis_cat,
        source_asset_id="src_b14_syn",
        source_sha256=sha,
        solution_id="sol_b14_syn",
        seed_axes_icrs=seed_b,
    )
    assert bin_sol.status == "calibrated" and isinstance(bin_sol.wcs, pps.PhoneCameraWCS)
    prof = StarProfileRecord(sigma_major_px=1.2, sigma_minor_px=1.0, angle_rad=0.2, stars=12)
    det_q = DetectionQualityRecord(statistic="whitened", noise_limits=(), star_profile=prof)
    bin_sol = replace(bin_sol, quality=replace(bin_sol.quality, detection=det_q))

    # k == 1 returns binned_solution unchanged
    assert pps.refine_binned_phone_solution(stored_rgb, bin_sol, 1) is bin_sol

    refitted = pps.refine_binned_phone_solution(stored_rgb, bin_sol, k, catalog_stars=vis_cat)
    assert refitted.status == "calibrated"
    assert refitted.wcs is not None
    assert refitted.wcs.width == width and refitted.wcs.height == height
    assert refitted.quality.held_out_inliers >= 6
    assert refitted.quality.median_residual_px is not None and refitted.quality.median_residual_px <= 3.0
    assert refitted.quality.detection is not None and refitted.quality.detection.star_profile is not None
    assert refitted.quality.detection.star_profile.sigma_major_px == pytest.approx(3.6)
    assert refitted.quality.detection.star_profile.sigma_minor_px == pytest.approx(3.0)
    contract_dict = refitted.to_contract_dict()
    assert contract_dict["status"] == "calibrated"

    # Negative / gate-refusal test: unrelated random stored sky must degrade cleanly to `unresolved` with no WCS
    noise_rng = np.random.default_rng(999)
    false_stored = noise_rng.integers(8, 16, size=(height, width, 3), dtype=np.uint8)
    for _ in range(40):
        rx, ry = int(noise_rng.integers(30, width - 30)), int(noise_rng.integers(30, height - 30))
        false_stored[ry - 2 : ry + 3, rx - 2 : rx + 3, :] = 220
    degraded = pps.refine_binned_phone_solution(false_stored, bin_sol, k, catalog_stars=vis_cat)
    assert degraded.status == "unresolved"
    assert degraded.wcs is None
    assert all(f.evidence_class == "observed" for f in degraded.findings)

    # The same detections given by the caller, found on this stored image's own binned frame, give the same solution
    own_bin = pps._bin_image_blocks(stored_rgb, k)
    own_dets = detector.detect_point_sources(pps._normalized_luma_2d(own_bin), asset_id="src_b14_syn")
    given = pps.refine_binned_phone_solution(stored_rgb, bin_sol, k, binned_image=own_bin, binned_detections=own_dets, catalog_stars=vis_cat)
    assert given.status == "calibrated"
    assert given.quality.held_out_inliers == refitted.quality.held_out_inliers
    assert given.quality.median_residual_px == pytest.approx(refitted.quality.median_residual_px, abs=1e-9)

    # The handover's first association is within max(4.8, 3.0 * k) stored px, over the binned ranking's candidates
    # remeasured, in the binned ranking's order
    luma_bin = pps._normalized_luma_2d(own_bin)
    bg_bin, _ = pps._estimate_background_and_noise(luma_bin)
    ranked_bin, _ = pps._rank_stellar_candidates(own_bin, luma_bin, bg_bin, own_dets, max_pool=85)
    stored_luma = pps._normalized_luma_2d(stored_rgb)
    stored_bg, stored_sig = pps._estimate_background_and_noise(stored_luma, grouped=True)
    rem_all, kept_all = pps.remeasure_on_stored_pixels(
        stored_luma, stored_bg, pps._effective_sigma(stored_sig, stored_luma), own_dets, k, return_indices=True
    )
    by_bin = {id(own_dets[i]): rem_all[pos] for pos, i in enumerate(kept_all)}
    expected_xy = np.array([(by_bin[id(d)].x_px, by_bin[id(d)].y_px) for d in ranked_bin if id(d) in by_bin])
    assert len(expected_xy) >= 6 and not np.allclose(expected_xy, expected_xy[::-1])
    calls: list[tuple[float, np.ndarray]] = []
    real_assoc = pps._associate_detections

    def recording(wcs, dets, cat_ra, cat_dec, radius_px):
        calls.append((float(radius_px), np.array([(d.x_px, d.y_px) for d in dets])))
        return real_assoc(wcs, dets, cat_ra, cat_dec, radius_px=radius_px)

    monkeypatch.setattr(pps, "_associate_detections", recording)
    pps.refine_binned_phone_solution(stored_rgb, bin_sol, k, catalog_stars=vis_cat)
    monkeypatch.setattr(pps, "_associate_detections", real_assoc)
    assert calls[0][0] == pytest.approx(max(4.8, pps.MAX_HELD_OUT_MEDIAN_PX * k))
    assert calls[0][1].shape == expected_xy.shape and np.allclose(calls[0][1], expected_xy, atol=1e-9)

    # Validation guards
    with pytest.raises(ValueError, match="binned k x k"):
        pps.refine_binned_phone_solution(stored_rgb, bin_sol, k, binned_image=own_bin[:-1], catalog_stars=vis_cat)
    with pytest.raises(ValueError, match="at least 1"):
        pps.refine_binned_phone_solution(stored_rgb, bin_sol, 0)
    with pytest.raises(ValueError, match="calibrated"):
        pps.refine_binned_phone_solution(stored_rgb, degraded, k)
    plain_wcs = WCSModel(
        crval_ra_deg=10.0, crval_dec_deg=20.0, crpix_x=100.0, crpix_y=100.0,
        scale_deg_per_px=0.02, rotation_deg=0.0, k1=0.0, k2=0.0, parity=-1, width=200, height=200,
    )
    with pytest.raises(ValueError, match="PhoneCameraWCS"):
        pps.restate_binned_phone_wcs(plain_wcs, 3, 600, 600)


def _stored_corner_inside_the_model(binned_wcs, k: int, width: int, height: int) -> bool:
    """Whether the stored frame's half diagonal, in the restated focal's units, is one the lens model reaches."""
    fisheye = binned_wcs.lens_model == "kannala-brandt"
    limit = pps._max_valid_radial(binned_wcs.dist_k1, binned_wcs.dist_k2, fisheye=fisheye)
    top = limit if fisheye else min(limit, 25.0)
    reach = top * (1.0 + top * top * (binned_wcs.dist_k1 + top * top * binned_wcs.dist_k2))
    return 0.5 * math.hypot(width, height) / (binned_wcs.focal_px * k) < reach


def test_a_stored_corner_past_the_lens_models_reach_keeps_the_exact_focal() -> None:
    # A property seed's find of 2026-11-24: a 125 deg binned field whose stored frame (one row taller than k times the
    # binned one) puts its corner just past the radius the lens model reaches. The focal stays exactly k times the
    # binned one and crpix exact; the stated field is held at the model's valid edge, finite and inside the bounds
    binned_wcs = pps.PhoneCameraWCS.from_pointing(
        crval_ra_deg=120.0, crval_dec_deg=25.0, rotation_deg=14.0, fov_diagonal_deg=125.0, width=300, height=300,
        lens_model="brown-conrady", k1=0.0, k2=-0.013671875,
    )
    assert not _stored_corner_inside_the_model(binned_wcs, 2, 600, 601)
    restated = pps.restate_binned_phone_wcs(binned_wcs, 2, 600, 601)
    assert restated.focal_px == pytest.approx(binned_wcs.focal_px * 2, rel=1e-12)
    assert restated.crpix_x == pytest.approx((binned_wcs.crpix_x + 0.5) * 2 - 0.5, abs=1e-12)
    assert math.isfinite(restated.fov_diagonal_deg) and binned_wcs.fov_diagonal_deg <= restated.fov_diagonal_deg <= 175.0
