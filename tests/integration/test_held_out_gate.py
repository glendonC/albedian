"""The plate solver's held-out gate: what it measures, and that each of its conditions can fail.

A WCS fitted on four training stars is `calibrated` only when at least six held-out detections land
inside the per-star inlier gate and the residuals of every held-out detection paired with a catalog
star, inliers or not, have a median of at most 3 px and a 95th percentile of at most 6 px (the
thresholds of `evaluateWCSVerificationGate` in packages/evidence). Residuals are measured out to the
8 px association radius, so a fit that misses held-out stars fails on its residuals, not only on its count.

Covers:
- A fit that predicts six held-out stars exactly and misses eight by 5 px fails on its median alone
- A fit that misses six of fourteen held-out stars by 7 px fails on its 95th percentile alone
- The same fit with its misses at 5 px calibrates, and reports the misses in its residuals
- A fit with nothing held out (four stars and two detections far from every star) reports no residual
- PBT: `verify_held_out` reports the residual of every held-out pair, leaves the training stars out,
  counts the inliers inside the inlier gate, and passes exactly when all three conditions hold
- Real clips (owner media): the decoded frames reach the gate only on chance triangle matches, do not
  calibrate, and report the held-out residuals they measured
"""

from __future__ import annotations

import math
import hashlib
from pathlib import Path

from hypothesis import example, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest

from services.science.catalog import get_catalog_stars
from services.science.solver import (
    SourceDetection,
    WCSModel,
    detect_point_sources,
    solve_and_verify_field,
    verify_held_out,
)

ARTIFACTS = Path(__file__).resolve().parents[2] / "data" / "artifacts"
# A 1920 x 1080 frame wide enough to hold all 18 pinned stars, at least 30 px apart
WIDTH, HEIGHT = 1920, 1080
WIDE_WCS = WCSModel(
    crval_ra_deg=92.0, crval_dec_deg=18.0, crpix_x=WIDTH / 2.0, crpix_y=HEIGHT / 2.0, scale_deg_per_px=0.0475,
    rotation_deg=90.0, k1=-0.02, k2=0.004, parity=-1, width=WIDTH, height=HEIGHT,
)
# A synthetic moment that points at nobody; the blind solve does not use it to match
MOMENT = {"obs_utc_iso": "2001-01-01T00:00:00Z", "observer_lat": 12.5, "observer_lon": 45.25}
STARS = get_catalog_stars()
NAMES = tuple(star.common_name for star in STARS)
OUTLYING = ("Sirius", "Procyon", "Pollux", "Castor", "Capella", "Elnath", "Aldebaran", "Tabit")
# In these fields the solver trains on these four Orion stars, its consensus matches farthest from the centre
ORION_TRAINING = ("Rigel", "Saiph", "Hatsya", "Alnitak")
HELD_OUT = tuple(name for name in NAMES if name not in ORION_TRAINING)


def _field(misses: dict[str, tuple[float, float]]) -> list[SourceDetection]:
    """A detection for every pinned star where `WIDE_WCS` puts it, the stars in `misses` moved by (px, angle)."""
    detections = []
    for star in STARS:
        x_px, y_px = WIDE_WCS.world_to_pixel(star.ra_deg, star.dec_deg)
        distance, angle = misses.get(star.common_name, (0.0, 0.0))
        snr = 30.0 * 10.0 ** (-0.2 * (star.mag - 3.5))
        detections.append(
            SourceDetection(
                x_px=x_px + distance * math.cos(angle), y_px=y_px + distance * math.sin(angle),
                flux=100.0 * snr, snr=snr, fwhm_px=2.6, centroid_uncertainty_px=0.1,
            )
        )
    return detections


def _miss(names: tuple[str, ...], distance_px: float) -> dict[str, tuple[float, float]]:
    return {name: (distance_px, 2.0 * math.pi * k / len(names) + 0.3) for k, name in enumerate(names)}


def _sources(data: object) -> dict[str, str]:
    """The id and SHA-256 a recording would carry, here of the synthetic input the test builds."""
    raw = np.ascontiguousarray(data).tobytes() if isinstance(data, np.ndarray) else repr(data).encode()
    digest = hashlib.sha256(raw).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def test_a_fit_that_misses_most_held_out_stars_fails_on_its_median() -> None:
    detections = _field(_miss(OUTLYING, 5.0))
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(detections))
    # Six held-out Orion stars sit inside the inlier gate and the p95 is 5 px, so only the median decides
    assert sol.quality.median_residual_px == pytest.approx(5.0, abs=0.01)
    assert sol.quality.p95_residual_px == pytest.approx(5.0, abs=0.01)
    # All fourteen held-out stars pair within 8 px; six sit inside the inlier gate
    assert (sol.quality.held_out_pair_count, sol.quality.held_out_inliers) == (14, 6)
    assert sol.status == "ambiguous"
    assert sol.quality.passed_independent_verification is False
    assert sol.wcs is None
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)


def test_a_fit_with_a_few_far_held_out_misses_fails_on_its_p95() -> None:
    detections = _field(_miss(OUTLYING[:6], 7.0))
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(detections))
    # Eight held-out inliers and a median of 0 px, so only the 95th percentile decides
    assert sol.quality.p95_residual_px == pytest.approx(7.0, abs=0.01)
    assert sol.quality.median_residual_px == pytest.approx(0.0, abs=0.01)
    assert (sol.quality.held_out_pair_count, sol.quality.held_out_inliers) == (14, 8)
    assert sol.status == "ambiguous"
    assert sol.wcs is None


def test_the_same_fit_calibrates_when_its_misses_stay_within_bounds() -> None:
    misses = OUTLYING[:6]
    detections = _field(_miss(misses, 5.0))
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(detections))
    # The six misses are measured and reported, not dropped from the statistics
    assert sol.quality.p95_residual_px == pytest.approx(5.0, abs=0.01)
    assert sol.quality.median_residual_px == pytest.approx(0.0, abs=0.01)
    assert (sol.quality.held_out_pair_count, sol.quality.held_out_inliers) == (14, 8)
    assert sol.status == "calibrated"
    matched = {f.common_name for f in sol.findings if f.evidence_class == "catalog_matched"}
    assert len(matched) == 12 and matched.isdisjoint(misses)


def test_calibrated_solve_names_each_unmatched_detections_own_reason() -> None:
    # Start from a field where Sirius is displaced by 5.0 px (paired within 8 px, outside the inlier gate),
    # add one optical PSF detection at (60, 60) px far from every catalog star, and one hot-pixel impulse at (90, 90) px.
    base_dets = _field({"Sirius": (5.0, 0.3)})
    far_det = SourceDetection(
        x_px=60.0, y_px=60.0, flux=850.0, snr=8.5, fwhm_px=2.6, centroid_uncertainty_px=0.12, peak_to_total_ratio=0.25
    )
    impulse_det = SourceDetection(
        x_px=90.0, y_px=90.0, flux=920.0, snr=9.2, fwhm_px=0.85, centroid_uncertainty_px=0.10, peak_to_total_ratio=0.82
    )
    detections = base_dets + [far_det, impulse_det]
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(detections))
    assert sol.status == "calibrated"

    unmatched = [f for f in sol.findings if f.evidence_class == "observed"]
    assert len(unmatched) == 3
    by_det = {id(f.source_detection): f.limitations[0] for f in unmatched}

    sirius_det = base_dets[NAMES.index("Sirius")]
    assert "Paired with Sirius at 5.00 px residual, outside the " in by_det[id(sirius_det)]
    assert " px inlier gate." in by_det[id(sirius_det)]
    assert by_det[id(far_det)] == "No star of the 18-star catalog sits within 8 px of where the WCS puts one."
    assert by_det[id(impulse_det)] == (
        "Skipped by catalog matching as a hot-pixel impulse (FWHM 0.85 px, peak-to-total flux ratio 0.82)."
    )

    assert len(sol.warnings) == 1
    warning = sol.warnings[0]
    assert warning.startswith("3 unmatched sensor detection(s): ")
    assert "1 skipped as hot-pixel impulse(s)" in warning
    assert "1 paired within 8 px but outside the " in warning
    assert "1 with no star of the 18-star catalog within 8 px" in warning


def test_a_fit_with_nothing_held_out_reports_no_residual() -> None:
    # Four stars to fit and two detections at least 260 px from every pinned star, so nothing is held out
    full = _field({})
    detections = [full[NAMES.index(name)] for name in ("Betelgeuse", "Rigel", "Bellatrix", "Procyon")] + [
        SourceDetection(x_px=x, y_px=y, flux=900.0, snr=9.0, fwhm_px=2.6, centroid_uncertainty_px=0.1)
        for x, y in ((60.0, 60.0), (1860.0, 1020.0))
    ]
    sol = solve_and_verify_field(detections, WIDTH, HEIGHT, **MOMENT, **_sources(detections))
    assert "WCS solution did not pass independent held-out calibration gate." in sol.warnings
    assert (sol.quality.held_out_pair_count, sol.quality.held_out_inliers) == (0, 0) and sol.status != "calibrated"
    quality = sol.quality
    assert (quality.median_residual_px, quality.p95_residual_px, quality.median_residual_arcsec) == (None, None, None)
    assert not {"medianResidualPx", "p95ResidualPx", "medianResidualArcsec"} & sol.to_contract_dict()["quality"].keys()


@pytest.mark.owner_media("data/artifacts")
def test_real_clip_frames_do_not_calibrate_and_report_their_held_out_residuals() -> None:
    # The frame each real clip's experiment run decoded and saved before any processing
    frames = sorted(ARTIFACTS.glob("*/00_original_source.png"))
    assert frames, "data/artifacts holds no decoded original frame"
    for path in frames:
        rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
        luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        height, width = luma.shape
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sol = solve_and_verify_field(
            detect_point_sources(luma), width, height, **MOMENT, source_asset_id=f"frame-{digest[:12]}", source_sha256=digest
        )
        assert sol.status != "calibrated" and sol.wcs is None
        assert sol.quality.held_out_inliers < 6
        # Thousands of detections let the triangle search settle on a chance alignment whose held-out pairs
        # scatter across the association radius: the reported residuals are theirs, not ones capped under 3 px
        assert sol.quality.median_residual_px > 3.0
        assert sol.quality.p95_residual_px > 6.0


def _case(at: dict[str, float]) -> list[tuple[float, float] | None]:
    """Offsets for `HELD_OUT` in order: the named stars moved by their distance, the rest undetected."""
    return [(at[name], 1.0) if name in at else None for name in HELD_OUT]


def _at(*groups: tuple[int, float]) -> dict[str, float]:
    """The next `count` held-out stars at each distance, in `HELD_OUT` order."""
    at: dict[str, float] = {}
    for count, distance in groups:
        start = len(at)
        at.update(dict.fromkeys(HELD_OUT[start : start + count], distance))
    return at


# Each held-out star is undetected (None) or detected (distance px, angle rad) from where the WCS puts it
OFFSET = st.one_of(
    st.none(), st.tuples(st.floats(min_value=0.0, max_value=7.5), st.floats(min_value=0.0, max_value=2.0 * math.pi))
)
ORION = list(ORION_TRAINING)


@settings(max_examples=150, deadline=None)
@given(
    training=st.lists(st.sampled_from(NAMES), min_size=4, max_size=4, unique=True),
    offsets=st.lists(OFFSET, min_size=len(HELD_OUT), max_size=len(HELD_OUT)),
)
@example(training=ORION, offsets=_case(_at((6, 0.0), (8, 5.0))))  # the median alone rejects
@example(training=ORION, offsets=_case(_at((8, 0.0), (6, 7.0))))  # the p95 alone rejects
@example(training=ORION, offsets=_case(_at((8, 0.0), (6, 5.0))))  # all three conditions hold
@example(training=ORION, offsets=_case(_at((6, 0.5), (6, 5.3))))  # median 2.9 px passes
@example(training=ORION, offsets=_case(_at((6, 0.5), (6, 5.7))))  # median 3.1 px fails
@example(training=ORION, offsets=_case(_at((12, 0.0), (2, 5.9))))  # p95 5.9 px passes
@example(training=ORION, offsets=_case(_at((12, 0.0), (2, 6.1))))  # p95 6.1 px fails
@example(training=ORION, offsets=_case(_at((6, 0.0))))  # six inliers alone pass
@example(training=ORION, offsets=_case(_at((5, 0.0), (2, 5.0))))  # five inliers fail, with seven held-out pairs
def test_pbt_held_out_gate_measures_every_held_out_pair(
    training: list[str], offsets: list[tuple[float, float] | None]
) -> None:
    held_out = [name for name in NAMES if name not in training]
    misses = {name: offset for name, offset in zip(held_out, offsets) if offset is not None}
    by_name = dict(zip(NAMES, _field(misses)))
    detections = [by_name[name] for name in NAMES if name in training or name in misses]

    verification = verify_held_out(WIDE_WCS, detections, STARS, [by_name[name] for name in training])

    # Every held-out detection pairs with its own star at the distance it was moved; no training star counts
    residuals = sorted(verification.held_out_residuals_px)
    assert residuals == pytest.approx(sorted(distance for distance, _ in misses.values()), abs=1e-6)
    assert [res < 1e-6 for _, _, res, is_train in verification.associations if is_train] == [True] * 4
    assert 1.8 <= verification.inlier_gate_px <= 3.0
    assert verification.held_out_inliers == sum(res <= verification.inlier_gate_px for res in residuals)
    if not residuals:
        assert verification.median_residual_px is None and verification.p95_residual_px is None
        assert not verification.passed
        return
    median, p95 = float(np.median(residuals)), float(np.percentile(residuals, 95.0))
    assert verification.median_residual_px == pytest.approx(median, abs=1e-12)
    assert verification.p95_residual_px == pytest.approx(p95, abs=1e-12)
    # The documented gate: at least 6 held-out inliers, a held-out median of at most 3 px and a p95 of at most 6 px
    assert verification.passed == (verification.held_out_inliers >= 6 and median <= 3.0 and p95 <= 6.0)
