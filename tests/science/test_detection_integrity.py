"""Tests for detection integrity and forced photometry on original pixels (Track 1 M6).

Covers:
- Candidates proposed by a denoised or enhanced `MediaVariant` must pass forced PSF + aperture photometry on
  the unmodified original pixels (`verify_candidates_on_original_pixels`)
- A source seen only in a variant is rejected and never becomes `observed`
- Surviving detections take their centroid, flux, FWHM, and SNR strictly from the original pixels, never from
  the variant
- `evaluate_injection_recovery` publishes completeness per SNR bin, false-positive rate, and centroid error on
  synthetic frames and real clip frames (`owner_media`)
- PBT: variant-only sources across arbitrary amplification factors are rejected on original noise, and verified
  fluxes of real stars are invariant to how strongly the variant scaled them
"""

from __future__ import annotations

import math

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos import real_clips
from services.science import detector as detector_mod, solver as solver_mod
from services.science.detector import (
    InjectionRecoveryBin,
    InjectionRecoveryReport,
    detect_point_sources,
    evaluate_injection_recovery,
    render_synthetic_starfield,
    verify_candidates_on_original_pixels,
)
from services.science.wcs_models import SourceDetection


def _add_gaussian_star(image: np.ndarray, x_px: float, y_px: float, flux: float, sigma_px: float = 1.15) -> None:
    h, w = image.shape
    x0, y0 = int(round(x_px)), int(round(y_px))
    r_win = 6
    y_slice = slice(max(0, y0 - r_win), min(h, y0 + r_win + 1))
    x_slice = slice(max(0, x0 - r_win), min(w, x0 + r_win + 1))
    yy, xx = np.mgrid[y_slice, x_slice]
    psf = np.exp(-((xx - x_px) ** 2 + (yy - y_px) ** 2) / (2.0 * sigma_px * sigma_px))
    psf /= max(float(np.sum(psf)), 1e-12)
    image[y_slice, x_slice] += flux * psf


def test_solver_reexports_detection_integrity_symbols() -> None:
    assert solver_mod.verify_candidates_on_original_pixels is verify_candidates_on_original_pixels
    assert solver_mod.evaluate_injection_recovery is evaluate_injection_recovery
    assert solver_mod.InjectionRecoveryReport is InjectionRecoveryReport
    assert solver_mod.InjectionRecoveryBin is InjectionRecoveryBin
    assert detector_mod.verify_candidates_on_original_pixels is verify_candidates_on_original_pixels


def test_variant_only_candidates_are_rejected_and_photometry_is_measured_on_original_pixels() -> None:
    orig_frame, _, truth_stars = render_synthetic_starfield(
        width=320, height=240, background_level=25.0, read_noise_sigma=1.5, flux_scale=1200.0, seed=101
    )
    assert len(truth_stars) >= 6

    # Build a synthetic denoised/boosted variant that amplifies pixel intensities by 3.5x
    # and hallucinates 10 bright PSF sparkles where the original image has only background noise.
    variant_frame = (orig_frame - 25.0) * 3.5 + 25.0
    hallucinated_coords: list[tuple[float, float]] = []
    for idx in range(10):
        hx = 25.0 + idx * 26.0
        hy = 22.0 if idx % 2 == 0 else 215.0
        # Ensure hallucinated coordinate is far from any real truth star
        if all(math.hypot(hx - tx, hy - ty) > 12.0 for _, tx, ty in truth_stars):
            hallucinated_coords.append((hx, hy))
            _add_gaussian_star(variant_frame, hx, hy, flux=850.0, sigma_px=1.15)
    assert len(hallucinated_coords) >= 6

    variant_detections = detect_point_sources(variant_frame, snr_threshold=3.5, asset_id="variant_denoised")
    # Confirm the variant detector actually picked up the hallucinated sparkles
    hallucinated_in_variant = [
        d for d in variant_detections if any(math.hypot(d.x_px - hx, d.y_px - hy) <= 2.0 for hx, hy in hallucinated_coords)
    ]
    assert len(hallucinated_in_variant) == len(hallucinated_coords)

    # Now verify all variant candidates on the unmodified original pixels (including duplicate proposals)
    verified = verify_candidates_on_original_pixels(
        orig_frame,
        [*variant_detections, *variant_detections[:3]],
        snr_threshold=3.5,
        asset_id="src_original",
        frame_time_sec=1.25,
    )
    assert len(verified) >= 6
    assert len({(round(d.x_px, 4), round(d.y_px, 4)) for d in verified}) == len(verified)
    for det in verified:
        assert det.asset_id == "src_original"
        assert det.frame_time_sec == 1.25
        # Zero variant-only hallucinations may survive forced photometry on the original pixels
        for hx, hy in hallucinated_coords:
            assert math.hypot(det.x_px - hx, det.y_px - hy) > 4.0

    # Compare against direct detection on original pixels: flux and SNR must match original pixels, not 3.5x variant
    direct_orig = detect_point_sources(orig_frame, snr_threshold=3.5, asset_id="src_original", frame_time_sec=1.25)
    for v_det in verified:
        nearest_orig = min(direct_orig, key=lambda d: math.hypot(d.x_px - v_det.x_px, d.y_px - v_det.y_px))
        assert math.hypot(nearest_orig.x_px - v_det.x_px, nearest_orig.y_px - v_det.y_px) < 1e-6
        assert abs(nearest_orig.flux - v_det.flux) < 1e-6
        assert abs(nearest_orig.snr - v_det.snr) < 1e-6
        assert abs(nearest_orig.fwhm_px - v_det.fwhm_px) < 1e-6

        nearest_var = min(variant_detections, key=lambda d: math.hypot(d.x_px - v_det.x_px, d.y_px - v_det.y_px))
        assert v_det.flux < nearest_var.flux * 0.45

    # Verify sky_mask is respected during forced photometry on original pixels
    half_mask = np.zeros_like(orig_frame, dtype=bool)
    half_mask[:120, :] = True
    masked_verified = verify_candidates_on_original_pixels(
        orig_frame, variant_detections, sky_mask=half_mask, snr_threshold=3.5, asset_id="src_original"
    )
    assert len(masked_verified) < len(verified)
    assert all(d.y_px < 121.0 for d in masked_verified)


def test_injection_recovery_publishes_completeness_and_rejects_variant_artifacts() -> None:
    rng = np.random.default_rng(77)
    base_frame = 20.0 + rng.normal(0.0, 1.8, size=(260, 360))
    # Add 2 pre-existing real stars to base_frame so baseline subtraction is exercised
    _add_gaussian_star(base_frame, 55.0, 50.0, flux=550.0, sigma_px=1.15)
    _add_gaussian_star(base_frame, 295.0, 50.0, flux=620.0, sigma_px=1.15)
    variant_frame = base_frame.copy()
    # Add 6 variant-only sparkles to variant_frame
    for idx in range(6):
        _add_gaussian_star(variant_frame, 30.0 + idx * 45.0, 230.0, flux=600.0, sigma_px=1.15)

    report = evaluate_injection_recovery(
        base_frame,
        variant_image=variant_frame,
        injected_snrs=(2.0, 5.5, 9.0, 15.0),
        n_per_snr=8,
        snr_threshold=3.5,
        seed=77,
    )
    assert isinstance(report, InjectionRecoveryReport)
    assert report.baseline_detection_count == 2
    assert report.injected_count == 32
    assert report.spurious_count == 0
    assert report.false_positive_rate == 0.0
    assert report.median_centroid_error_px is not None and report.median_centroid_error_px <= 0.45

    by_snr = {round(b.snr_min, 1): b for b in report.bins}
    # Sub-threshold SNR=2.0 bin should have 0 completeness; bright SNR=9.0 and 15.0 bins should have high completeness
    assert by_snr[2.0].completeness == 0.0
    assert by_snr[9.0].completeness >= 0.75
    assert by_snr[15.0].completeness >= 0.875


@pytest.mark.owner_media("data/uploads")
def test_real_clips_forced_photometry_rejects_variant_only_sources_and_measures_injection_recovery() -> None:
    clips = real_clips.find_clips()
    assert clips, "no real clip was found"
    clip = clips[0]
    raw_frame = real_clips.decode_luma(clip, clip.duration_s / 2.0)[0].astype(np.float64)
    # Use a central 240x320 crop of the real clip frame for fast, deterministic evaluation
    h, w = raw_frame.shape
    y0, x0 = max(0, (h - 240) // 2), max(0, (w - 320) // 2)
    crop = raw_frame[y0 : y0 + 240, x0 : x0 + 320].copy()

    # Smooth the crop to form a denoised variant and inject 5 variant-only sparkles in quiet sky locations
    bg, sigma_bg = detector_mod._estimate_background_and_noise(crop)
    res = crop - bg
    variant_crop = crop.copy()
    quiet_coords: list[tuple[float, float]] = []
    for gy in range(30, 210, 35):
        for gx in range(30, 290, 45):
            patch = np.abs(res[gy - 5 : gy + 6, gx - 5 : gx + 6])
            if float(np.max(patch)) < 2.2 * max(sigma_bg, 1.0):
                quiet_coords.append((float(gx), float(gy)))
                _add_gaussian_star(variant_crop, float(gx), float(gy), flux=45.0 * max(sigma_bg, 1.0))
                if len(quiet_coords) == 5:
                    break
        if len(quiet_coords) == 5:
            break
    assert len(quiet_coords) >= 3, "could not find quiet background patches in real clip crop"

    verified = verify_candidates_on_original_pixels(crop, quiet_coords, snr_threshold=3.5, asset_id="src_real_clip")
    assert verified == [], "variant-only sparkles on a real clip frame must never pass forced photometry"

    report = evaluate_injection_recovery(
        crop,
        injected_snrs=(3.0, 8.0, 14.0),
        n_per_snr=6,
        snr_threshold=3.5,
        seed=42,
    )
    assert report.bins[-1].completeness >= 0.65
    assert report.false_positive_rate <= 0.15


@settings(max_examples=12, deadline=None)
@given(
    n_hallucinated=st.integers(min_value=1, max_value=10),
    variant_gain=st.floats(min_value=1.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    seed=st.integers(min_value=1, max_value=500),
)
@example(n_hallucinated=1, variant_gain=2.0, seed=1)
@example(n_hallucinated=6, variant_gain=5.0, seed=42)
@example(n_hallucinated=10, variant_gain=15.0, seed=99)
def test_pbt_variant_gain_and_hallucinations_never_corrupt_original_pixel_photometry(
    n_hallucinated: int,
    variant_gain: float,
    seed: int,
) -> None:
    rng = np.random.default_rng(seed)
    orig = 30.0 + rng.normal(0.0, 1.2, size=(140, 180))
    # Place 2 genuine bright stars in the original image
    real_coords = [(45.2, 42.7), (132.6, 96.4)]
    for rx, ry in real_coords:
        _add_gaussian_star(orig, rx, ry, flux=180.0, sigma_px=1.15)

    variant = (orig - 30.0) * variant_gain + 30.0
    hallucinated: list[tuple[float, float]] = []
    for idx in range(n_hallucinated):
        hx = 20.0 + (idx * 29) % 140
        hy = 18.0 + ((idx * 37) % 100)
        if all(math.hypot(hx - rx, hy - ry) > 12.0 for rx, ry in real_coords):
            hallucinated.append((hx, hy))
            _add_gaussian_star(variant, hx, hy, flux=400.0 * variant_gain, sigma_px=1.15)

    proposals = [*real_coords, *hallucinated]
    verified = verify_candidates_on_original_pixels(orig, proposals, snr_threshold=3.5, asset_id="src_pbt")
    assert len(verified) == 2
    for det in verified:
        assert all(math.hypot(det.x_px - hx, det.y_px - hy) > 3.5 for hx, hy in hallucinated)
        assert 115.0 <= det.flux <= 230.0

    # Raising snr_threshold above the original stars' SNR (~65) must reject them via effective_snr_gate
    assert verify_candidates_on_original_pixels(orig, proposals, snr_threshold=120.0, asset_id="src_pbt") == []

    # Also verify that passing scaled SourceDetection objects gives identical flux to passing raw coordinates
    scaled_det_proposals = [
        SourceDetection(
            x_px=cx,
            y_px=cy,
            flux=99999.0 * variant_gain,
            snr=999.0 * variant_gain,
            fwhm_px=4.5,
            centroid_uncertainty_px=0.01,
            asset_id="variant_id",
        )
        for cx, cy in proposals
    ]
    verified_from_dets = verify_candidates_on_original_pixels(
        orig, scaled_det_proposals, snr_threshold=3.5, asset_id="src_pbt"
    )
    assert len(verified_from_dets) == len(verified)
    for d1, d2 in zip(verified, verified_from_dets):
        assert abs(d1.flux - d2.flux) < 1e-9
        assert abs(d1.snr - d2.snr) < 1e-9
        assert d2.asset_id == "src_pbt"
