"""A still above the line is searched binned and its solution restated in the stored pixels (bin-detect):
services/science/binning.py. The figures are the two public photos the line was drawn from (bin-research)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from services.science.binning import (
    BIN_ABOVE_PX,
    BIN_MAX_PX,
    bin_factor,
    bin_frame,
    restate_pixels,
    stored_position,
)
from services.science.wcs_models import WCSModel


@pytest.mark.parametrize(
    ("width", "height", "k"),
    [
        (6144, 8160, 3),  # rp-b-10, 50.1 MP: solves binned 3 x 3
        (6944, 9280, 3),  # rp-b-14, 64.4 MP: 2 x 2 leaves 16.1 MP, which did not solve
        (8256, 5504, 1),  # rp-d-01, 45.4 MP, the largest of the sixty that solves as stored
        (8000, 6000, 1),  # exactly at the line
        (8001, 6000, 3),  # just above it: 2 x 2 would leave 12 MP
        (8064, 6048, 3),  # an iPhone photo taken at 48 MP
        (4032, 3024, 1),  # an iPhone photo at its default 12 MP
        (20000, 20000, 8),  # 400 MP: 7 x 7 leaves 8.16 MP, 8 x 8 leaves 6.25 MP
    ],
)
def test_a_still_is_binned_by_the_smallest_factor_that_brings_it_under_the_line(width: int, height: int, k: int) -> None:
    assert bin_factor(width, height) == k
    if k > 1:
        assert (width // k) * (height // k) <= BIN_MAX_PX
        assert k == 2 or (width // (k - 1)) * (height // (k - 1)) > BIN_MAX_PX
        assert width * height > BIN_ABOVE_PX


def test_a_still_with_no_pixels_is_refused() -> None:
    with pytest.raises(ValueError):
        bin_factor(0, 10)


def test_a_binned_frame_holds_each_whole_blocks_mean_rounded_in_the_frames_own_dtype() -> None:
    rgb = np.arange(7 * 5 * 3, dtype=np.uint8).reshape(7, 5, 3)
    binned = bin_frame(rgb, 2)
    assert binned.dtype == np.uint8 and binned.shape == (3, 2, 3)
    for r in range(3):
        for c in range(2):
            block = rgb[2 * r:2 * r + 2, 2 * c:2 * c + 2].astype(np.float64)
            assert np.array_equal(binned[r, c], np.round(block.mean(axis=(0, 1))).astype(np.uint8))
    luma = np.array([[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]])
    assert np.array_equal(bin_frame(luma, 2), np.array([[2.0]]))  # (0 + 1 + 3 + 4) / 4 = 2, the last column left out
    assert bin_frame(rgb, 1) is rgb


def test_a_frame_of_repeated_blocks_bins_back_to_itself_exactly() -> None:
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 256, size=(9, 11, 3), dtype=np.uint8)
    stored = np.repeat(np.repeat(frame, 3, axis=0), 3, axis=1)
    assert np.array_equal(bin_frame(stored, 3), frame)


def test_a_binned_pixel_stands_for_the_centre_of_its_block() -> None:
    assert stored_position(0.0, 3) == 1.0  # the block of stored 0, 1 and 2
    assert stored_position(-0.5, 3) == -0.5  # the frame's edge stays its edge
    assert stored_position(10.25, 2) == 21.0


@pytest.mark.parametrize(
    ("binned", "stored"),
    [
        ("Measured at (10.5, 20.0) px · SNR 7.1", "Measured at (32.5, 61.0) px · SNR 7.1"),
        ("Unmatched sensor detection at (0.0, 3.3) px", "Unmatched sensor detection at (1.0, 10.9) px"),
        ("Paired with Rigel at 1.23 px residual, outside the 2.00 px inlier gate.",
         "Paired with Rigel at 3.69 px residual, outside the 6.00 px inlier gate."),
        ("No star of the 9110-star catalog sits within 15 px of where the WCS puts one.",
         "No star of the 9110-star catalog sits within 45 px of where the WCS puts one."),
        ("Skipped by catalog matching as a hot-pixel impulse (FWHM 0.90 px, peak-to-total flux ratio 0.71).",
         "Skipped by catalog matching as a hot-pixel impulse (FWHM 2.70 px, peak-to-total flux ratio 0.71)."),
        ("3 unmatched sensor detection(s): 2 paired within 15 px but outside the 2.00 px inlier gate, 1 with no star "
         "of the 9110-star catalog within 15 px.",
         "3 unmatched sensor detection(s): 2 paired within 45 px but outside the 6.00 px inlier gate, 1 with no star "
         "of the 9110-star catalog within 45 px."),
        ("Distance is Gaia DR3 parallax, not measured from this image.",
         "Distance is Gaia DR3 parallax, not measured from this image."),
        ("The search stopped at its 180 s limit.", "The search stopped at its 180 s limit."),
    ],
)
def test_every_pixel_figure_in_the_chains_words_is_restated_in_the_stored_pixels(binned: str, stored: str) -> None:
    assert restate_pixels(binned, 3) == stored
    assert restate_pixels(binned, 1) == binned


def test_the_stored_wcs_puts_every_sky_point_at_the_centre_of_the_block_the_binned_one_puts_it_in() -> None:
    binned = WCSModel(83.8, -5.4, 1023.7, 1359.2, 0.0167, rotation_deg=31.0, k1=-0.04, k2=0.01, parity=-1,
                      width=2048, height=2720)
    k = 3
    stored = WCSModel(83.8, -5.4, stored_position(1023.7, k), stored_position(1359.2, k), 0.0167 / k,
                      rotation_deg=31.0, k1=-0.04, k2=0.01, parity=-1, width=6144, height=8160)
    for x, y in [(0.0, 0.0), (1023.7, 1359.2), (2047.0, 2719.0), (300.25, 2200.75), (1900.5, 40.0)]:
        ra, dec = binned.pixel_to_world(x, y)
        sx, sy = stored.world_to_pixel(ra, dec)
        assert math.isclose(sx, stored_position(x, k), abs_tol=1e-6)
        assert math.isclose(sy, stored_position(y, k), abs_tol=1e-6)
        assert stored.pixel_to_world(stored_position(x, k), stored_position(y, k)) == pytest.approx((ra, dec), abs=1e-9)


@pytest.mark.parametrize("kind", ["tan", "phone"])
def test_a_restated_wcs_of_either_kind_projects_every_sky_point_to_the_centre_of_its_block(kind: str) -> None:
    """The phone solver's camera WCS projects through its own focal length, not the TAN scale: both are restated."""
    from scipy.spatial.transform import Rotation

    from services.science.binning import wcs_in_stored_pixels
    from services.science.phone_photo_solver import PhoneCameraWCS

    k, width, height = 3, 400, 300
    if kind == "tan":
        binned = WCSModel(83.8, -5.4, 199.5, 149.5, 0.15, rotation_deg=31.0, k1=-0.04, parity=-1, width=width,
                          height=height)
    else:
        binned = PhoneCameraWCS.from_rotation_and_intrinsics(
            rot_matrix=Rotation.from_euler("zyx", [40.0, -20.0, 10.0], degrees=True).as_matrix(),
            fov_diagonal_deg=60.0, width=width, height=height, k1=-0.05, k2=0.01, p1=0.001, p2=-0.002,
        )
    stored = wcs_in_stored_pixels(binned, k, width * k, height * k)
    assert type(stored) is type(binned)
    for x, y in [(0.0, 0.0), (199.5, 149.5), (399.0, 299.0), (35.25, 260.75), (380.5, 12.0)]:
        ra, dec = binned.pixel_to_world(x, y)
        sx, sy = stored.world_to_pixel(ra, dec)
        assert math.isclose(sx, stored_position(x, k), abs_tol=1e-6), (x, sx)
        assert math.isclose(sy, stored_position(y, k), abs_tol=1e-6), (y, sy)


def _calibrated_binned_solution(median_px: float, *, solver_kind: str = "phone") -> tuple[object, list[object]]:
    from services.science.catalog import get_catalog_stars
    from services.science.phone_photo_solver import PHONE_SOLVER_IDENTITY
    from services.science.solver import triangle_solver_identity
    from services.science.wcs_models import (
        CelestialFindingRecord,
        ObservationSolution,
        SolutionQuality,
        SourceDetection,
    )

    sha = "b" * 64
    star = get_catalog_stars()[0]
    wcs = WCSModel(star.ra_deg, star.dec_deg, 1024.0, 1360.0, 0.0167, rotation_deg=0.0, parity=-1, width=2048, height=2720)
    sx, sy = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
    d1 = SourceDetection(sx, sy, flux=400.0, snr=18.2, fwhm_px=2.1, centroid_uncertainty_px=0.08, asset_id="src_bin")
    d2 = SourceDetection(10.0, 20.0, flux=120.0, snr=6.4, fwhm_px=1.9, centroid_uncertainty_px=0.15, asset_id="src_bin")
    u_ra, u_dec = wcs.pixel_to_world(d2.x_px, d2.y_px)
    identity = PHONE_SOLVER_IDENTITY if solver_kind == "phone" else triangle_solver_identity(get_catalog_stars())
    sol = ObservationSolution(
        id="sol-binned",
        source_asset_id="src_bin",
        source_sha256=sha,
        frame_time_sec=0.0,
        revision=1,
        status="calibrated",
        observed_at_utc="2030-10-05T04:00:00Z",
        observer_lat=35.0,
        observer_lon=-110.0,
        wcs=wcs,
        solver=identity,
        quality=SolutionQuality(
            detected_candidate_count=2,
            matched_inlier_count=1,
            held_out_pair_count=28,
            held_out_inliers=24,
            median_residual_px=median_px,
            p95_residual_px=4.57,
            median_residual_arcsec=median_px * wcs.scale_deg_per_px * 3600.0,
            passed_independent_verification=True,
        ),
        findings=[
            CelestialFindingRecord(
                id=f"finding-{star.id}",
                observation_solution_id="sol-binned",
                evidence_class="catalog_matched",
                title=star.common_name,
                subtitle="Matched Star",
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                uncertainty_arcsec=1.2,
                source_detection=d1,
                catalog_star=star,
                pixel_residual_px=0.5,
                angular_residual_arcsec=30.0,
                verified_by_holdout=True,
                limitations=("Distance is Hipparcos parallax, not measured from this image.",),
                source_sha256=sha,
            ),
            CelestialFindingRecord(
                id="finding-obs-2",
                observation_solution_id="sol-binned",
                evidence_class="observed",
                title="Observed Point Source #2",
                subtitle="Unmatched sensor detection at (10.0, 20.0) px",
                ra_deg=u_ra,
                dec_deg=u_dec,
                uncertainty_arcsec=10.0,
                source_detection=d2,
                verified_by_holdout=False,
                limitations=("No star of the 71-star catalog sits within 8 px of where the WCS puts one.",),
                source_sha256=sha,
            ),
        ],
        warnings=[
            "1 unmatched sensor detection(s): 1 with no star of the 71-star catalog within 8 px.",
            "The stars alone lined this photo up; what is predicted on it is drawn for the time your iPhone recorded, "
            "which the stars do not confirm. A search at that moment matched nothing, so it may be off; the stars were "
            "matched without it.",
        ],
    )
    return sol, [d1, d2]


@pytest.mark.parametrize("solver_kind", ["phone", "triangle"])
def test_a_binned_calibration_whose_restated_median_exceeds_the_held_out_gate_becomes_unresolved(solver_kind: str) -> None:
    """rp-b-10's case under the 22:42 rule: binned 3 x 3 passes at 1.36 px, restated in stored pixels is 4.08 px > 3 px,
    so the restated solution degrades cleanly to unresolved with no WCS and no catalog match rather than raising."""
    from services.contracts.astronomy import parse_observation_solution
    from services.science.binning import in_stored_pixels
    from services.science.solver import GATE_REFUSED_WARNING

    binned, dets = _calibrated_binned_solution(1.36, solver_kind=solver_kind)
    out = in_stored_pixels(binned, 3, 6144, 8160)
    assert out.status == "unresolved"
    assert out.wcs is None and out.pose_estimate is None
    assert out.quality is not None
    assert out.quality.passed_independent_verification is False
    assert out.quality.matched_inlier_count == 0
    assert out.quality.held_out_pair_count == 28 and out.quality.held_out_inliers == 24
    assert out.quality.median_residual_px == pytest.approx(1.36 * 3)
    assert out.quality.p95_residual_px == pytest.approx(4.57 * 3)
    assert len(out.findings) == 2
    for idx, (finding, orig_det) in enumerate(zip(out.findings, dets, strict=True), start=1):
        assert finding.id == f"finding-obs-{idx}"
        assert finding.evidence_class == "observed"
        assert finding.ra_deg is None and finding.dec_deg is None and finding.catalog_star is None
        assert finding.pixel_residual_px is None and finding.angular_residual_arcsec is None
        assert finding.verified_by_holdout is False
        det = finding.source_detection
        assert det is not None
        assert det.x_px == pytest.approx(stored_position(orig_det.x_px, 3))
        assert det.y_px == pytest.approx(stored_position(orig_det.y_px, 3))
        assert det.fwhm_px == pytest.approx(orig_det.fwhm_px * 3)
        assert det.flux == pytest.approx(orig_det.flux * 9)
        assert finding.subtitle == f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}"
    assert out.warnings == [GATE_REFUSED_WARNING]
    parse_observation_solution(out.to_contract_dict())


def test_a_binned_calibration_stays_calibrated_at_3_px_in_stored_pixels_and_degrades_above_it() -> None:
    from services.science.binning import in_stored_pixels

    at_gate, _ = _calibrated_binned_solution(1.0)
    kept = in_stored_pixels(at_gate, 3, 6144, 8160)
    assert kept.status == "calibrated" and kept.wcs is not None
    assert kept.quality is not None and kept.quality.passed_independent_verification is True
    assert kept.quality.median_residual_px == pytest.approx(3.0)

    over_gate, _ = _calibrated_binned_solution(1.001)
    refused = in_stored_pixels(over_gate, 3, 6144, 8160)
    assert refused.status == "unresolved" and refused.wcs is None
    assert refused.quality is not None and refused.quality.passed_independent_verification is False

