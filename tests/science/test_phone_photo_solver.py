"""Tests for the phone-photo astrometric solver against the G 12 catalog tiles (Track 1 M2).

Covers:
- Star detections measured on original pixels (`observed` for centroids/flux/SNR, `catalog_matched` only after
  the coincidence-weighed held-out + dual-path WCS verification gate passes)
- Solving positive phone photos across main and ultra-wide lenses (both Brown-Conrady and Kannala-Brandt
  fisheye) on the phone-photo benchmark (`outcome == "correct"`, median field error <= 3 px)
- Blind timeless/placeless solving (`obs_utc_iso=None, observer_lat=None, observer_lon=None`), half-location
  rejection, and real candidate denominator progress reporting (`on_candidate`)
- Zero false solves (`falseSolves == 0`) on hard negatives (overcast, moon in cloud, screen, random dots, dark)
- PBT: coincidence-weighed held-out gate rejects random dot detections across densities, and `PhoneCameraWCS`
  inverts `world_to_pixel` / `pixel_to_world` across rectilinear and fisheye lenses
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos import render, score, solvers, stars, suite
from services.science.phone_photo_solver import (
    PhoneCameraWCS,
    evaluate_coincidence_gate,
    solve_phone_photo,
)
from services.science.wcs_models import SourceDetection

CATALOG = stars.TileCatalog()


@pytest.fixture(scope="module")
def ci_specs() -> list[suite.FrameSpec]:
    return suite.build_suite("ci")


@pytest.fixture(scope="module")
def ci00_frame(ci_specs: list[suite.FrameSpec]) -> render.RenderedFrame:
    return render.render_frame(ci_specs[0], CATALOG)


@pytest.fixture(scope="module")
def ci_frames(ci_specs: list[suite.FrameSpec], ci00_frame: render.RenderedFrame) -> list[render.RenderedFrame]:
    return [ci00_frame, *[render.render_frame(spec, CATALOG) for spec in ci_specs[1:]]]


def test_phone_photo_solver_adapter_solves_ci_phone_positives_with_zero_false_solves(
    ci_frames: list[render.RenderedFrame],
) -> None:
    assert "phone-photo" in solvers.SOLVERS
    report = score.run_frames(ci_frames, "phone-photo", timeout_s=15.0)
    assert report.summary["evidenceClass"] == "simulated"
    assert report.summary["negatives"]["falseSolves"] == 0
    assert report.summary["positives"]["falseClaims"] == 0

    by_id = {f.frame_id: f for f in report.frames}
    # ci-00 (iPhone 18 Pro main, Orion), ci-01 (Pixel 11 Pro ultrawide, 123 deg), ci-02 (Galaxy S25 Ultra wide, Cygnus)
    for fid in ("ci-00", "ci-01", "ci-02"):
        assert by_id[fid].outcome == "correct", f"{fid}: {by_id[fid]}"
        assert by_id[fid].field_median_px is not None and by_id[fid].field_median_px <= 3.0
        assert by_id[fid].match_precision is not None and by_id[fid].match_precision >= 0.75

    # All 4 hard negatives (ci-04..ci-07) must be unresolved with zero false solves
    for fid in ("ci-04", "ci-05", "ci-06", "ci-07"):
        assert not by_id[fid].claimed, f"{fid} falsely claimed: {by_id[fid]}"
        assert not by_id[fid].false_solve


def test_solve_phone_photo_preserves_original_pixel_detections_and_contract_invariants(
    ci00_frame: render.RenderedFrame,
) -> None:
    from services.science import solver as solver_mod

    assert solver_mod.PhoneCameraWCS is PhoneCameraWCS
    assert solver_mod.evaluate_coincidence_gate is evaluate_coincidence_gate
    assert solver_mod.solve_phone_photo is solve_phone_photo
    assert "phone-photo" in solvers.SOLVERS

    frame = ci00_frame  # ci-00
    sha = hashlib.sha256(frame.jpeg_bytes).hexdigest()
    events: list[tuple[int, int]] = []
    meta = frame.truth.metadata

    with pytest.raises(ValueError, match="source_sha256"):
        solve_phone_photo(
            frame.rgb,
            source_asset_id="src_ci00",
            source_sha256="not-a-sha256",
        )

    outcome = solvers.SOLVERS["phone-photo"](solvers.SolverInput(frame.rgb, meta))
    scored = score.score_frame(frame.truth, outcome, camera=frame.spec.camera)
    assert scored.outcome == "correct"
    assert scored.field_median_px is not None and scored.field_median_px <= 3.0

    sol = solve_phone_photo(
        frame.rgb,
        obs_utc_iso=meta["utc"],
        observer_lat=meta["latDeg"],
        observer_lon=meta["lonDeg"],
        camera_label=meta["camera"],
        source_asset_id="src_ci00",
        source_sha256=sha,
        solution_id="sol_ci00",
        on_candidate=lambda done, total: events.append((done, total)),
    )
    assert sol.status == "calibrated"
    assert sol.has_verified_wcs
    assert sol.wcs is not None
    assert sol.quality.held_out_inliers >= 6
    assert sol.quality.median_residual_px is not None and sol.quality.median_residual_px <= 3.0

    contract = sol.to_contract_dict()
    assert contract["status"] == "calibrated"
    assert contract["observedAtUTC"] == meta["utc"]
    assert contract["observerLocation"] == {"latDeg": meta["latDeg"], "lonDeg": meta["lonDeg"]}

    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    observed = [f for f in sol.findings if f.evidence_class == "observed"]
    assert len(matched) >= 8
    assert all(f.source_detection is not None and f.catalog_star is not None for f in matched)
    assert all(f.alt_deg is not None and f.az_deg is not None for f in matched)
    assert all(f.has_sky_position for f in observed)

    assert events and events[0][0] == 0 and events[0][1] >= 1500
    assert events[-1][0] == events[-1][1]
    assert all(0 <= done <= total for done, total in events)


def test_solve_phone_photo_solves_kannala_brandt_fisheye_ultrawide(
    ci_specs: list[suite.FrameSpec],
) -> None:
    base = ci_specs[0]
    fisheye_spec = replace(
        base,
        frame_id="test-fisheye",
        camera="iphone-18-pro-ultra-wide",
        fov_deg=120.0,
        distortion=("kannala-brandt", (-0.02, 0.006, 0.0, 0.0)),
    )
    frame = render.render_frame(fisheye_spec, CATALOG)
    outcome = solvers.SOLVERS["phone-photo"](solvers.SolverInput(frame.rgb, frame.truth.metadata))
    scored = score.score_frame(frame.truth, outcome, camera=fisheye_spec.camera)
    assert scored.outcome == "correct", f"Fisheye solve failed: {scored}"
    assert scored.field_median_px is not None and scored.field_median_px <= 3.0


def test_solve_phone_photo_blind_timeless_and_half_location_validation(
    ci00_frame: render.RenderedFrame,
) -> None:
    frame = ci00_frame  # ci-00 (Orion)
    sha = hashlib.sha256(frame.jpeg_bytes).hexdigest()

    bad_events: list[tuple[int, int]] = []
    with pytest.raises(ValueError, match="observer_lat and observer_lon"):
        solve_phone_photo(
            frame.rgb,
            observer_lat=30.0,
            observer_lon=None,
            source_asset_id="src_blind",
            source_sha256=sha,
            on_candidate=lambda done, total: bad_events.append((done, total)),
        )
    assert bad_events == []

    sol = solve_phone_photo(
        frame.rgb,
        obs_utc_iso=None,
        observer_lat=None,
        observer_lon=None,
        camera_label=frame.truth.metadata["camera"],
        source_asset_id="src_blind",
        source_sha256=sha,
    )
    assert sol.status == "calibrated"
    assert sol.observed_at_utc is None
    assert sol.observer_lat is None and sol.observer_lon is None
    contract = sol.to_contract_dict()
    assert "observedAtUTC" not in contract and "observerLocation" not in contract


@settings(max_examples=20, deadline=None)
@given(
    n_dots=st.integers(min_value=0, max_value=120),
    seed=st.integers(min_value=1, max_value=100_000),
)
@example(n_dots=0, seed=1)
@example(n_dots=12, seed=42)
@example(n_dots=80, seed=99)
def test_pbt_coincidence_gate_and_solver_reject_random_detections(n_dots: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    w, h = 480, 360
    dets = [
        SourceDetection(
            x_px=float(rng.uniform(8.0, w - 8.0)),
            y_px=float(rng.uniform(8.0, h - 8.0)),
            flux=float(rng.uniform(50.0, 500.0)),
            snr=float(rng.uniform(5.0, 35.0)),
            fwhm_px=1.6,
            centroid_uncertainty_px=0.15,
        )
        for _ in range(n_dots)
    ]
    # Even if 6 held-out pairs occurred by chance in a dense field, coincidence gate weighs against density
    passed_dense, log_odds_dense = evaluate_coincidence_gate(
        held_out_residuals_px=(2.4, 2.6, 2.7, 2.8, 2.9, 2.95),
        active_detection_count=4500,
        active_sky_area_px2=float(w * h),
        projected_catalog_count=60,
        inlier_gate_px=3.2,
    )
    assert not passed_dense
    assert log_odds_dense < 18.0

    passed_disagree, _ = evaluate_coincidence_gate(
        held_out_residuals_px=(0.3, 0.4, 0.35, 0.5, 0.45, 0.4, 0.3, 0.5),
        active_detection_count=30,
        active_sky_area_px2=float(w * h),
        projected_catalog_count=35,
        inlier_gate_px=2.5,
        dual_path_disagreement_px=4.0,
    )
    assert not passed_disagree

    sha = hashlib.sha256(f"rand-{n_dots}-{seed}".encode()).hexdigest()
    sol = solve_phone_photo(
        np.zeros((h, w, 3), dtype=np.uint8),
        detections=dets,
        source_asset_id="src_rand",
        source_sha256=sha,
    )
    assert sol.status == "unresolved"
    assert not sol.has_verified_wcs
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)


@settings(max_examples=25, deadline=None)
@given(
    ra0=st.floats(min_value=0.0, max_value=359.9, allow_nan=False, allow_infinity=False),
    dec0=st.floats(min_value=-75.0, max_value=75.0, allow_nan=False, allow_infinity=False),
    rot_deg=st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False),
    fov_deg=st.floats(min_value=55.0, max_value=123.0, allow_nan=False, allow_infinity=False),
    fisheye=st.booleans(),
    k1=st.floats(min_value=-0.03, max_value=0.02, allow_nan=False, allow_infinity=False),
)
@example(ra0=83.8, dec0=-1.2, rot_deg=0.0, fov_deg=84.06, fisheye=False, k1=0.007)
@example(ra0=359.5, dec0=60.0, rot_deg=-15.0, fov_deg=120.0, fisheye=True, k1=-0.02)
def test_pbt_phone_camera_wcs_round_trip_invertibility(
    ra0: float, dec0: float, rot_deg: float, fov_deg: float, fisheye: bool, k1: float
) -> None:
    w, h = 480, 360
    wcs = PhoneCameraWCS.from_pointing(
        crval_ra_deg=ra0,
        crval_dec_deg=dec0,
        rotation_deg=rot_deg,
        fov_diagonal_deg=fov_deg,
        width=w,
        height=h,
        lens_model="kannala-brandt" if fisheye else "brown-conrady",
        k1=k1,
        k2=0.0,
    )
    for x_px, y_px in ((240.0, 180.0), (120.0, 95.0), (370.0, 265.0), (60.0, 300.0)):
        ra, dec = wcs.pixel_to_world(x_px, y_px)
        assert math.isfinite(ra) and math.isfinite(dec)
        rx, ry = wcs.world_to_pixel(ra, dec)
        assert math.hypot(rx - x_px, ry - y_px) < 1e-3
