"""Tests for the phone-photo astrometric solver against the G 12 catalog tiles (Track 1 M2).

Covers:
- Star detections measured on original pixels (`observed` for centroids/flux/SNR, `catalog_matched` only after
  the coincidence-weighed held-out + dual-path WCS verification gate passes)
- Solving positive phone photos across main and ultra-wide lenses (both Brown-Conrady and Kannala-Brandt
  fisheye) on the phone-photo benchmark (`outcome == "correct"`, median field error <= 3 px), with each CI
  positive's child CPU seconds guarded at three times its machine's measured set and written to the warnings
  summary
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
import platform
import sys
import warnings

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos import render, score, solvers, stars, suite
from services.ingest.jobs import SOLVE_TIME_LIMIT_SEC
from services.science import detector
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
    # Each solve stops at the product's own bound, so a busy machine cannot turn a right solve into a timeout; the
    # speed is a measured number in the benchmark's report, not a pass or fail here
    report = score.run_frames(ci_frames, "phone-photo", timeout_s=SOLVE_TIME_LIMIT_SEC)
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


# The three CI phone positives `phone-photo` solves (`ci-03` is the 1/30 s `head-worn-video` frame).
CI_POSITIVE_IDS = ("ci-00", "ci-01", "ci-02")
# Each CI positive's phone-photo solve in CPU seconds of the bounded child that runs it (`time.process_time`, all its
# threads), keyed by `platform.machine()` [tested, measured on 2026-11-18 at b1ea12f4, solver unchanged at c6d4dbab]:
# - `arm64`: the largest of five runs on the project's 18-core arm64 laptop (loads 25.43 to 32.50);
# - `x86_64`: the largest of five runs of `py312-distro` and five runs of `py314-pinned` in the laptop's emulated
#   `linux/amd64` container (loads 17.75 to 33.21); because on `x86_64` the numbers come from an emulated container, on
#   a real x86-64 runner the guard is looser and still a guard, and the warning summary below shows the measured CPU
#   seconds on a pass as well so that after the first CI run the `x86_64` set can be taken from CI's own runner in a
#   later unit.
CI_POSITIVE_CPU_S_MEASURED: dict[str, dict[str, float]] = {
    "arm64": {"ci-00": 2.52, "ci-01": 3.54, "ci-02": 3.00},
    "x86_64": {"ci-00": 10.32, "ci-01": 13.70, "ci-02": 12.62},
}
# How many times its machine's measured CPU seconds each CI positive solve may take: a guard against a solve getting
# much slower unseen, not a product target. A unit that must raise a number says why in its report.
CI_POSITIVE_CPU_GUARD = 3.0


def _judge_ci_positive_cpu_seconds(machine: str, spent: dict[str, float]) -> None:
    if machine not in CI_POSITIVE_CPU_S_MEASURED:
        pytest.fail(
            f"No measured CI positive CPU-second set for machine {machine!r}; measure ci-00, ci-01 and ci-02 on "
            f"{machine!r} and add its entry to CI_POSITIVE_CPU_S_MEASURED."
        )
    measured = CI_POSITIVE_CPU_S_MEASURED[machine]
    non_positive = {fid: spent[fid] for fid in CI_POSITIVE_IDS if not (spent[fid] > 0.0)}
    assert not non_positive, f"non-positive child CPU seconds recorded: {non_positive}"
    slower = {
        fid: spent[fid]
        for fid in CI_POSITIVE_IDS
        if spent[fid] > CI_POSITIVE_CPU_GUARD * measured[fid]
    }
    assert not slower, (
        f"CI positive solves on {machine} over {CI_POSITIVE_CPU_GUARD:g} times their measured CPU seconds: {slower}; "
        f"measured {measured}"
    )
    formatted = ", ".join(f"{fid}={spent[fid]:.2f}s" for fid in CI_POSITIVE_IDS)
    warnings.warn(f"CI positive child CPU seconds on {machine}: {formatted}", stacklevel=2)


def test_no_ci_positive_solve_has_got_much_slower_unseen(ci_frames: list[render.RenderedFrame]) -> None:
    """Guard against a solve getting much slower unseen, not a product target: each CI positive's solve in the bounded
    child's CPU seconds is at most three times its machine's measured number (`arm64` from this laptop; on `x86_64` the
    numbers come from an emulated container, so on a real x86-64 runner the guard is looser and still a guard), and the
    measured CPU seconds are emitted into the warnings summary so CI's log shows them on a pass as well."""
    by_id = {frame.spec.frame_id: frame for frame in ci_frames}
    spent: dict[str, float] = {}
    for fid in CI_POSITIVE_IDS:
        frame = by_id[fid]
        outcome = solvers.run_solver("phone-photo", solvers.SolverInput(frame.rgb, frame.truth.metadata), SOLVE_TIME_LIMIT_SEC)
        assert outcome.status == "calibrated" and outcome.claimed, (fid, outcome.status, outcome.error)
        spent[fid] = float(outcome.extra["cpuS"])
    _judge_ci_positive_cpu_seconds(platform.machine(), spent)


def test_phone_photo_adapter_records_child_cpu_seconds_and_guard_handles_machines_and_edges(
    ci00_frame: render.RenderedFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.science.phone_photo_solver as pps_mod

    cpu_now = 10.0
    wall_now = 100.0
    real_solve = pps_mod.solve_phone_photo
    fake_sol = real_solve(
        np.zeros((32, 32, 3), dtype=np.uint8),
        detections=[],
        source_asset_id="src_fake",
        source_sha256=hashlib.sha256(b"fake").hexdigest(),
    )

    def fake_solve_phone_photo(*args: object, **kwargs: object) -> object:
        nonlocal cpu_now, wall_now
        cpu_now = 12.75
        wall_now = 105.5
        return fake_sol

    monkeypatch.setattr(pps_mod, "solve_phone_photo", fake_solve_phone_photo)
    monkeypatch.setattr(solvers.time, "process_time", lambda: cpu_now)
    monkeypatch.setattr(solvers.time, "perf_counter", lambda: wall_now)
    outcome = solvers._phone_photo(solvers.SolverInput(ci00_frame.rgb, ci00_frame.truth.metadata))
    assert outcome.extra["cpuS"] == 12.75 - 10.0
    assert outcome.elapsed_s == 105.5 - 100.0

    with pytest.raises(pytest.fail.Exception, match="measure ci-00, ci-01 and ci-02 on 'riscv64'"):
        _judge_ci_positive_cpu_seconds("riscv64", {"ci-00": 1.0, "ci-01": 1.0, "ci-02": 1.0})

    assert CI_POSITIVE_IDS == ("ci-00", "ci-01", "ci-02")
    assert CI_POSITIVE_CPU_GUARD == 3.0
    assert CI_POSITIVE_CPU_S_MEASURED == {
        "arm64": {"ci-00": 2.52, "ci-01": 3.54, "ci-02": 3.00},
        "x86_64": {"ci-00": 10.32, "ci-01": 13.70, "ci-02": 12.62},
    }
    for machine in ("arm64", "x86_64"):
        measured = CI_POSITIVE_CPU_S_MEASURED[machine]
        at_bound = {fid: 3.0 * measured[fid] for fid in ("ci-00", "ci-01", "ci-02")}
        expected_warning = (
            f"CI positive child CPU seconds on {machine}: "
            f"ci-00={at_bound['ci-00']:.2f}s, ci-01={at_bound['ci-01']:.2f}s, ci-02={at_bound['ci-02']:.2f}s"
        )
        with pytest.warns(UserWarning) as caught:
            _judge_ci_positive_cpu_seconds(machine, at_bound)
        assert [str(w.message) for w in caught] == [expected_warning]
        for fid in ("ci-00", "ci-01", "ci-02"):
            for bad in (0.0, -0.1, math.nan):
                with pytest.raises(AssertionError, match="non-positive"):
                    _judge_ci_positive_cpu_seconds(machine, {**at_bound, fid: bad})
            for over in (float(np.nextafter(at_bound[fid], math.inf)), math.inf):
                with pytest.raises(AssertionError, match=fid):
                    _judge_ci_positive_cpu_seconds(machine, {**at_bound, fid: over})

    this_mod = sys.modules[__name__]
    with monkeypatch.context() as mp:
        mp.setattr(this_mod, "CI_POSITIVE_CPU_GUARD", 2.0)
        two_x = {fid: 2.0 * CI_POSITIVE_CPU_S_MEASURED["arm64"][fid] for fid in ("ci-00", "ci-01", "ci-02")}
        with pytest.warns(UserWarning):
            _judge_ci_positive_cpu_seconds("arm64", two_x)
        with pytest.raises(AssertionError, match="over 2 times"):
            _judge_ci_positive_cpu_seconds(
                "arm64", {**two_x, "ci-01": float(np.nextafter(two_x["ci-01"], math.inf))}
            )

    synthetic_ci = [replace(ci00_frame, spec=replace(ci00_frame.spec, frame_id=fid)) for fid in ("ci-00", "ci-01", "ci-02")]
    seen_calls: list[tuple[str, float]] = []
    fake_outcomes: dict[str, solvers.SolverOutcome] = {}
    rgb_by_fid = {}
    for idx, fid in enumerate(("ci-00", "ci-01", "ci-02")):
        tagged = np.full((4, 4, 3), idx + 1, dtype=np.uint8)
        synthetic_ci[idx] = replace(synthetic_ci[idx], rgb=tagged)
        rgb_by_fid[idx + 1] = fid
        fake_outcomes[fid] = solvers.SolverOutcome(
            status="calibrated",
            claimed=True,
            wcs=None,
            matches=[],
            detections=10,
            held_out_inliers=6,
            self_median_residual_px=0.5,
            elapsed_s=1.0,
            extra={"cpuS": 1.0 + idx},
        )

    def fake_run_solver(name: str, inp: solvers.SolverInput, timeout_s: float) -> solvers.SolverOutcome:
        fid = rgb_by_fid[int(inp.rgb[0, 0, 0])]
        seen_calls.append((f"{name}:{fid}", timeout_s))
        return fake_outcomes[fid]

    with monkeypatch.context() as mp:
        mp.setattr(solvers, "run_solver", fake_run_solver)
        mp.setattr(platform, "machine", lambda: "x86_64")
        mp.setattr(this_mod, "SOLVE_TIME_LIMIT_SEC", 42.5)
        with pytest.warns(UserWarning) as caught_full:
            test_no_ci_positive_solve_has_got_much_slower_unseen(synthetic_ci)
        assert seen_calls == [("phone-photo:ci-00", 42.5), ("phone-photo:ci-01", 42.5), ("phone-photo:ci-02", 42.5)]
        assert [str(w.message) for w in caught_full] == [
            "CI positive child CPU seconds on x86_64: ci-00=1.00s, ci-01=2.00s, ci-02=3.00s"
        ]
        fake_outcomes["ci-02"] = replace(fake_outcomes["ci-02"], extra={"cpuS": 50.0})
        with pytest.raises(AssertionError, match="ci-02"):
            test_no_ci_positive_solve_has_got_much_slower_unseen(synthetic_ci)
        fake_outcomes["ci-02"] = replace(fake_outcomes["ci-02"], claimed=False, extra={"cpuS": 1.0})
        with pytest.raises(AssertionError, match="ci-02"):
            test_no_ci_positive_solve_has_got_much_slower_unseen(synthetic_ci)
        fake_outcomes["ci-02"] = replace(fake_outcomes["ci-02"], status="unresolved", claimed=True, extra={"cpuS": 1.0})
        with pytest.raises(AssertionError, match="ci-02"):
            test_no_ci_positive_solve_has_got_much_slower_unseen(synthetic_ci)


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
    assert contract["observedAtUTC"] == meta["utc"].replace("+00:00", "Z")
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


def test_a_calibrated_phone_solution_says_its_frames_worded_flags_after_its_own_warnings(
    ci00_frame: render.RenderedFrame,
) -> None:
    """The phone solver's own last path, which research/benchmarks/phone_photos/solvers.py reaches without the solve job
    (the job adds a frame's worded flags only where a solution lacks them). ci-00 calibrates and raises no worded flag;
    its own detections, each standing in for a flagged frame's (its FrameMeasures carrying both worded flags too), are
    solved the same way and the solution ends with both flags' words."""
    frame = ci00_frame
    meta = frame.truth.metadata
    raw = detector._to_luma_2d(frame.rgb)  # the luma solve_phone_photo detects on
    luma = raw / 255.0 if (frame.rgb.dtype == np.uint8 or float(np.max(raw)) > 1.5) else raw
    detections = detector.detect_point_sources(luma, asset_id="src_ci00")
    assert detections and all(d.frame is not None for d in detections)
    assert not any(flag in detector.NOISE_LIMIT_WORDS for d in detections for flag in d.noise_limits)
    flags = tuple(detector.NOISE_LIMIT_WORDS)
    flagged = [replace(d, frame=replace(d.frame, noise_limits=(*flags, *d.noise_limits))) for d in detections]

    def solve(dets: list[SourceDetection]):
        return solve_phone_photo(
            frame.rgb, obs_utc_iso=meta["utc"], observer_lat=meta["latDeg"], observer_lon=meta["lonDeg"],
            camera_label=meta["camera"], source_asset_id="src_ci00",
            source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(), detections=dets,
        )

    plain, said = solve(detections), solve(flagged)
    assert plain.status == said.status == "calibrated"
    assert not any(words in plain.warnings for words in detector.NOISE_LIMIT_WORDS.values())
    assert said.warnings == [*plain.warnings, *(detector.NOISE_LIMIT_WORDS[flag] for flag in flags)]


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


def test_too_few_sources_for_a_solution_still_says_the_frames_worded_flags(ci00_frame: render.RenderedFrame) -> None:
    """The phone solver's first unresolved path (under 6 detections) gives the frame's flags in words too (AUDIT M's
    mutant P2, that path's words taken away, lived): five of ci-00's detections, each standing in for a flagged frame's."""
    frame = ci00_frame
    meta = frame.truth.metadata
    raw = detector._to_luma_2d(frame.rgb)
    luma = raw / 255.0 if (frame.rgb.dtype == np.uint8 or float(np.max(raw)) > 1.5) else raw
    flags = tuple(detector.NOISE_LIMIT_WORDS)
    detections = [replace(d, frame=replace(d.frame, noise_limits=(*flags, *d.noise_limits)))
                  for d in detector.detect_point_sources(luma, asset_id="src_ci00")[:5]]
    solution = solve_phone_photo(
        frame.rgb, obs_utc_iso=meta["utc"], observer_lat=meta["latDeg"], observer_lon=meta["lonDeg"],
        camera_label=meta["camera"], source_asset_id="src_ci00",
        source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(), detections=detections,
    )
    assert solution.status == "unresolved"
    assert all(solution.warnings.count(detector.NOISE_LIMIT_WORDS[flag]) == 1 for flag in flags), solution.warnings


def test_stellar_pool_ranks_by_flux_over_local_noise_not_tile_snr() -> None:
    """_rank_stellar_candidates orders the pool by flux over local pixel noise, not by tile-divided snr (since realsky-3
    each tile's snr divides by its own matched noise, and clipped cores cap theirs)."""
    from services.science.phone_photo_solver import _rank_stellar_candidates

    rng = np.random.default_rng(8101)
    h, w = 240, 360
    luma = 0.20 + rng.normal(0.0, 0.002, (h, w))
    # Top-right 6 x 6 tile (rows 0..40, cols 300..360) has 4x the pixel MAD, so noise_scale is ~4 there and 1 elsewhere
    luma[:40, 300:] = 0.20 + rng.normal(0.0, 0.008, (40, 60))
    bg = np.full_like(luma, 0.20)
    bright_low_snr = SourceDetection(x_px=30.3, y_px=20.3, flux=0.90, snr=6.0, fwhm_px=2.4, centroid_uncertainty_px=0.1)
    same_flux_noisy_tile = SourceDetection(x_px=330.3, y_px=20.3, flux=0.90, snr=6.0, fwhm_px=2.4, centroid_uncertainty_px=0.1)
    faint_high_snr = SourceDetection(x_px=90.3, y_px=20.3, flux=0.10, snr=30.0, fwhm_px=2.4, centroid_uncertainty_px=0.1)
    ranked, _ = _rank_stellar_candidates(luma, luma, bg, [faint_high_snr, same_flux_noisy_tile, bright_low_snr], max_pool=10)
    assert [(d.x_px, d.flux) for d in ranked] == [(30.3, 0.90), (330.3, 0.90), (90.3, 0.10)]


# ------------------------------------------------------------------------------------------------
# Naming refusals on both solve paths: clearance, zero-point brightness limit and checked hull (solver-12a)
# ------------------------------------------------------------------------------------------------

from services.science import phone_photo_solver as pps  # noqa: E402

GATE = 4.0
EDGE = pps.NAMING_CLEARANCE_GATES * GATE  # the clearance's edge, computed as the rule computes it


@pytest.fixture
def without_brightness(monkeypatch):
    """The tests of the other clauses name one detection, which has no other match to measure a zero point by: they
    set the brightness clause aside, which its own tests below judge."""
    monkeypatch.setattr(pps, "_brightness_refusals", lambda inst, v: [None] * len(inst))

CLEARANCE_CASES = [
    # (named star found, other stars' distances, whether each counts, conditions refused)
    (True, [], [], []),
    (False, [], [], ["found"]),
    (True, [1.0], [True], ["clear"]),
    (True, [1.0], [False], []),  # a star giving under a tenth of the light does not count, however near
    (True, [EDGE], [True], ["clear"]),  # at the edge: within
    (True, [float(np.nextafter(EDGE, 0.0))], [True], ["clear"]),
    (True, [float(np.nextafter(EDGE, np.inf))], [True], []),  # one float past
    (True, [60.0, EDGE, 90.0], [True, True, True], ["clear"]),
    (True, [60.0, EDGE, 90.0], [True, False, True], []),
    (False, [EDGE], [True], ["found", "clear"]),
]


@pytest.mark.parametrize(("found", "others", "live", "refused"), CLEARANCE_CASES)
def test_the_first_two_naming_conditions_on_numbers_equal_by_construction(found, others, live, refused) -> None:
    assert pps._clearance_refusals(found, others, live, GATE) == refused


def _threshold(m_named: float, m_other: float) -> float:
    return pps.NAMING_LIGHT_SHARE_MAG + m_named + m_other  # as `_counts_against` adds it


COUNTS_CASES = [
    # (named V, named margin, other V, other margin, counts): a named star at V 0 makes "fainter by" the other's V exactly
    (0.0, 0.1, _threshold(0.1, 0.1), 0.1, False),  # exactly a tenth of the light plus both margins fainter: does not count
    (0.0, 0.1, float(np.nextafter(_threshold(0.1, 0.1), 0.0)), 0.1, True),  # one float brighter: counts
    (0.0, 0.1, 0.0, 0.1, True),  # as bright
    (0.0, 0.1, -3.0, 0.1, True),  # brighter
    # Across bands: a Hipparcos star named (Hp read as V, margin 0.5) and a Gaia star with colour (margin 0.1)
    (0.0, pps.NAMING_MARGIN_HP_MAG, 2.9, pps.NAMING_MARGIN_COLOUR_MAG, True),
    (0.0, pps.NAMING_MARGIN_HP_MAG, _threshold(pps.NAMING_MARGIN_HP_MAG, pps.NAMING_MARGIN_COLOUR_MAG),
     pps.NAMING_MARGIN_COLOUR_MAG, False),
    # A Gaia star without colour, whose V estimate can be off by over a magnitude
    (0.0, pps.NAMING_MARGIN_COLOUR_MAG, 3.5, pps.NAMING_MARGIN_NO_COLOUR_MAG, True),
]


@pytest.mark.parametrize(("v_named", "m_named", "v_other", "m_other", "counts"), COUNTS_CASES)
def test_another_star_counts_unless_it_gives_under_a_tenth_of_the_named_stars_light_leaning_to_refusal(
    v_named, m_named, v_other, m_other, counts
) -> None:
    assert bool(pps._counts_against(v_named, m_named, np.array([v_other]), np.array([m_other]))[0]) is counts


def test_the_naming_constants_are_the_ruled_ones() -> None:
    assert pps.NAMING_LAYERS == (0, 1, 2)
    assert pps.NAMING_CLEARANCE_GATES == 2.0
    assert pps.NAMING_LIGHT_SHARE_MAG == 2.5
    assert pps.NAMING_MARGIN_COLOUR_MAG == 0.1
    assert pps.NAMING_MARGIN_HP_MAG == 0.5
    # The polynomial's largest change between the assumed colour, 0.82, and either end of its clip
    assert pps.NAMING_MARGIN_NO_COLOUR_MAG == pytest.approx(1.169, abs=1e-3)


def test_the_v_estimate_keeps_its_polynomial() -> None:
    g = np.array([5.0, 5.0, 5.0, 5.0])
    c = np.array([-1.0, 0.82, 2.0, np.nan])
    want = g - np.array([pps._g_minus_v(-0.5), pps._g_minus_v(0.82), pps._g_minus_v(2.0), pps._g_minus_v(0.82)])
    np.testing.assert_allclose(pps._v_from_gaia_mag(g, c), want, rtol=0.0, atol=1e-12)
    assert pps._g_minus_v(0.0) == pytest.approx(-0.02704, abs=1e-12)


SQUARE = np.array([[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]])


@pytest.mark.parametrize(
    ("point", "inside"),
    [((2.0, 2.0), True), ((0.0, 0.0), True), ((2.0, 0.0), True), ((4.0, 4.0), True), ((5.0, 2.0), False),
     ((2.0, -1e-6), False), ((-1e-6, 2.0), False), ((2.0, 4.000001), False)],
)
def test_the_checked_region_is_the_held_out_inliers_hull_with_its_edge(point, inside) -> None:
    assert pps._inside_checked_region(*point, SQUARE) is inside


@pytest.mark.parametrize(
    "held", [np.zeros((0, 2)), SQUARE[:2], np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])],
    ids=["none", "two", "on-one-line"],
)
def test_fewer_than_three_held_out_inliers_or_all_on_a_line_check_no_region(held) -> None:
    assert pps._inside_checked_region(1.0, 1.0, held) is False


def _naming_setup(others: list[tuple[float, float, float]], named_id: str = "HIP 1", v_named: float = 2.0):
    """A 4032 x 3024 phone WCS at 60 deg (ICRS, no sky frame), a detection near the frame's centre, its named star
    there (V `v_named`), and other deep stars (dx px, dy px, V) around it, all Gaia stars with colour, at the tiles'
    epoch with no proper motion."""
    wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3), fov_diagonal_deg=60.0, width=4032, height=3024, sky_frame=None
    )
    det = SourceDetection(x_px=2000.0, y_px=1500.0, flux=100.0, snr=30.0, fwhm_px=3.0, centroid_uncertainty_px=0.3)
    places = [wcs.pixel_to_world(det.x_px, det.y_px)] + [wcs.pixel_to_world(det.x_px + dx, det.y_px + dy) for dx, dy, _ in others]
    ids = [named_id] + [f"Gaia DR3 {k + 2}" for k in range(len(others))]
    n = len(places)
    deep = pps._DeepStarIndex(np.array([p[0] for p in places]), np.array([p[1] for p in places]), np.zeros(n), np.zeros(n),
                              ids, 2016.0, np.array([v_named] + [v for _, _, v in others]),
                              np.full(n, pps.NAMING_MARGIN_COLOUR_MAG))
    return wcs, det, deep


@pytest.fixture(scope="module")
def named_star():
    """A real tile star's record, given the source id "HIP 1" that the made-up deep stars name it by."""
    base = pps._get_tile_index()
    star = base.to_catalog_star(0, float(base.data["ra0"][0]), float(base.data["dec0"][0]), base.epoch_jyear)
    return replace(star, catalog_source_id="HIP 1")


AROUND = [(1000.0, 1000.0), (3000.0, 1000.0), (3000.0, 2000.0), (1000.0, 2000.0)]
BESIDE = [(2500.0, 1000.0), (3500.0, 1000.0), (3500.0, 2000.0), (2500.0, 2000.0)]


@pytest.mark.parametrize(
    ("others", "named_id", "region", "refused"),
    [
        ([], "HIP 1", AROUND, []),
        ([(0.5 * EDGE, 0.0, 3.0)], "HIP 1", AROUND, ["clear"]),  # a star a magnitude fainter inside the clearance
        ([(0.5 * EDGE, 0.0, 5.0)], "HIP 1", AROUND, []),  # three magnitudes fainter: under a tenth of the light
        ([(0.0, 1.5 * EDGE, 2.0)], "HIP 1", AROUND, []),  # as bright, but past the clearance
        ([(0.1, 0.0, 1.0)], "HIP 1", AROUND, ["clear"]),  # a brighter star right beside it
        ([], "HIP 999", AROUND, ["found"]),  # another star where the named one should be: no clearance to judge
        ([], "HIP 1", BESIDE, ["checked"]),
        ([(0.25 * EDGE, 0.0, 2.5)], "HIP 999", BESIDE, ["found", "checked"]),
    ],
    ids=["alone", "fainter-inside", "much-fainter-inside", "as-bright-past", "brighter-beside", "named-star-missing",
         "outside-the-region", "missing-and-outside"],
)
@pytest.mark.usefixtures("without_brightness")
def test_a_name_stands_only_where_the_named_star_gives_the_detections_light(named_star, others, named_id, region, refused) -> None:
    wcs, det, deep = _naming_setup(others, named_id)
    got = pps.naming_refusals(wcs, [(det, named_star)], GATE, region, 2016.0, deep)[0]
    assert got == (" ".join(pps.NAMING_REFUSALS[k] for k in refused) if refused else None)


@pytest.mark.usefixtures("without_brightness")
def test_a_deep_star_moved_into_the_clearance_by_its_proper_motion_refuses_the_name(named_star) -> None:
    """A star as bright as the named one, 1.5 clearances away at the tiles' epoch, whose proper motion carries it onto
    the detection by 2056."""
    wcs, det, _ = _naming_setup([])
    ra0, dec0 = wcs.pixel_to_world(det.x_px, det.y_px)
    ra_far, dec_far = wcs.pixel_to_world(det.x_px, det.y_px + 1.5 * EDGE)
    years = 40.0
    far, here = pps._radec_to_vec_batch(np.array([ra_far, ra0]), np.array([dec_far, dec0]))
    r, d = np.radians(ra_far), np.radians(dec_far)
    east = np.array([-np.sin(r), np.cos(r), 0.0])
    north = np.array([-np.sin(d) * np.cos(r), -np.sin(d) * np.sin(r), np.cos(d)])
    to_mas_yr = 3600.0e3 * 180.0 / np.pi / years
    pm_ra, pm_dec = float((here - far) @ east) * to_mas_yr, float((here - far) @ north) * to_mas_yr
    deep = pps._DeepStarIndex(np.array([ra0, ra_far]), np.array([dec0, dec_far]), np.array([0.0, pm_ra]),
                              np.array([0.0, pm_dec]), ["HIP 1", "Gaia DR3 2"], 2016.0, np.array([2.0, 2.0]),
                              np.full(2, pps.NAMING_MARGIN_COLOUR_MAG))
    assert pps.naming_refusals(wcs, [(det, named_star)], GATE, AROUND, 2016.0, deep)[0] is None
    assert pps.naming_refusals(wcs, [(det, named_star)], GATE, AROUND, 2016.0 + years, deep)[0] == pps.NAMING_REFUSALS["clear"]


def test_the_deep_stars_are_every_tile_star_to_g_9_5_with_the_solvers_own_v_estimate() -> None:
    """Every star of the L0, L1 and L2 tiles, as the manifest counts them, each with its catalog source id; the V
    estimate of each L0 and L1 star is the one the phone solver ranks it by."""
    import json

    deep = pps._get_deep_star_index()
    manifest = json.loads((pps.DEFAULT_TILE_RELEASE_DIR / "manifest.json").read_text(encoding="utf-8"))
    counts = {layer["layer"]: layer["stars"] for layer in manifest["layers"]}
    assert len(deep.source_ids) == counts[0] + counts[1] + counts[2] == 294_318
    assert len(set(deep.source_ids.tolist())) == len(deep.source_ids)
    assert all(s.startswith(("HIP ", "Gaia DR3 ")) for s in deep.source_ids.tolist())
    base = pps._get_tile_index().data
    solver_v = {(f"HIP {s}" if h else f"Gaia DR3 {s}"): v for s, h, v in zip(base["sid"].tolist(), base["hip"].tolist(), base["v_mag"].tolist())}
    rows = [k for k, sid in enumerate(deep.source_ids.tolist()) if sid in solver_v]
    assert len(rows) == len(solver_v)
    np.testing.assert_allclose(deep.v_mag[rows], [solver_v[deep.source_ids[k]] for k in rows], rtol=0.0, atol=1e-12)
    assert set(np.unique(deep.margin_mag).tolist()) <= {
        pps.NAMING_MARGIN_COLOUR_MAG, pps.NAMING_MARGIN_NO_COLOUR_MAG, pps.NAMING_MARGIN_HP_MAG}


@pytest.mark.usefixtures("without_brightness")
def test_mizar_is_not_named_on_a_wide_phone_frame_because_mizar_b_gives_a_fifth_of_its_light_14_arcseconds_away() -> None:
    """A close double a phone shows as one detection [real tiles]: Mizar (HIP 65378) and, 14.4 arcseconds away,
    Gaia DR3 1563590510627624064 (Mizar B), under 2.5 magnitudes fainter. On a 60 deg, 4032 px wide frame (about 54
    arcseconds a pixel) Mizar B lies within twice a 4 px gate of Mizar, so a detection there is not named."""
    deep = pps._get_deep_star_index()
    base = pps._get_tile_index()
    rows = [i for i in range(len(base.data["sid"])) if base.data["hip"][i] and int(base.data["sid"][i]) == 65378]
    assert len(rows) == 1
    ra, dec = float(base.data["ra0"][rows[0]]), float(base.data["dec0"][rows[0]])
    near = deep.near(ra, dec, 30.0 / 3600.0, deep.epoch_jyear)
    ids = deep.source_ids[near].tolist()
    assert "Gaia DR3 1563590510627624064" in ids
    b = near[ids.index("Gaia DR3 1563590510627624064")]
    a = near[ids.index("HIP 65378")]
    assert deep.v_mag[b] - deep.v_mag[a] < pps.NAMING_LIGHT_SHARE_MAG
    star = base.to_catalog_star(rows[0], ra, dec, base.epoch_jyear)
    axis = pps._radec_to_vec_batch(np.array([ra]), np.array([dec]))[0]
    x_axis = np.cross([0.0, 0.0, 1.0], axis)
    x_axis /= np.linalg.norm(x_axis)
    wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.stack([x_axis, np.cross(axis, x_axis), axis]), fov_diagonal_deg=60.0, width=4032, height=3024,
        sky_frame=None,
    )
    x, y = wcs.world_to_pixel(ra, dec)
    det = SourceDetection(x_px=float(x), y_px=float(y), flux=1000.0, snr=200.0, fwhm_px=3.0, centroid_uncertainty_px=0.2)
    region = [(x - 1000.0, y - 1000.0), (x + 1000.0, y - 1000.0), (x + 1000.0, y + 1000.0), (x - 1000.0, y + 1000.0)]
    got = pps.naming_refusals(wcs, [(det, star)], GATE, region, base.epoch_jyear, deep)[0]
    assert got == pps.NAMING_REFUSALS["clear"]


def _with_a_star_beside(deep, ra: float, dec: float, v: float):
    """The deep stars and one more, a Gaia star with colour at (ra, dec) and V `v`, at the tiles' epoch."""
    return pps._DeepStarIndex(
        np.append(deep.ra0, ra), np.append(deep.dec0, dec), np.append(deep.pm_ra, 0.0), np.append(deep.pm_dec, 0.0),
        [*deep.source_ids.tolist(), "Gaia DR3 4242424242424242424"], deep.epoch_jyear, np.append(deep.v_mag, v),
        np.append(deep.margin_mag, pps.NAMING_MARGIN_COLOUR_MAG),
    )


def _named_and_refused(solution):
    named = [f for f in solution.findings if f.evidence_class == "catalog_matched"]
    refused = [f for f in solution.findings if f.evidence_class == "observed" and any(l.startswith("Not named:") for l in f.limitations)]
    return named, refused


def _ci00_solve(frame):
    sha = hashlib.sha256(b"ci-00 naming").hexdigest()
    return solve_phone_photo(frame.rgb, obs_utc_iso=frame.truth.metadata.get("utc"),
                             observer_lat=frame.truth.metadata.get("latDeg"), observer_lon=frame.truth.metadata.get("lonDeg"),
                             camera_label=frame.truth.metadata.get("camera"), source_asset_id=f"src_{sha[:16]}",
                             source_sha256=sha)


def test_the_phone_solver_names_only_what_the_rule_lets_stand_and_counts_only_those(ci00_frame, monkeypatch) -> None:
    """ci-00 [simulated]: every match the quality record counts is a name written; then one more tile star, as bright
    as a named star, placed one pixel from its detection takes that name away and nothing else."""
    first = _ci00_solve(ci00_frame)
    assert first.status == "calibrated"
    named, refused = _named_and_refused(first)
    assert named and first.quality.matched_inlier_count == len(named)
    target = named[0]
    ra, dec = first.wcs.pixel_to_world(target.source_detection.x_px + 1.0, target.source_detection.y_px)
    deep = pps._get_deep_star_index()
    v_target = float(deep.v_mag[list(deep.source_ids).index(target.catalog_star.catalog_source_id)])
    monkeypatch.setattr(pps, "_get_deep_star_index", lambda *a: _with_a_star_beside(deep, ra, dec, v_target))
    second = _ci00_solve(ci00_frame)
    named2, refused2 = _named_and_refused(second)
    assert [f.catalog_star.id for f in named2] == [f.catalog_star.id for f in named if f is not target]
    assert second.quality.matched_inlier_count == len(named2) == len(named) - 1
    moved = [f for f in refused2 if f.source_detection.x_px == target.source_detection.x_px]
    assert len(moved) == 1 and moved[0].limitations == (pps.NAMING_REFUSALS["clear"],)


def test_the_plate_solver_names_only_what_the_rule_lets_stand(monkeypatch) -> None:
    """The plate solver on 17 detections at the pinned stars' pixels of a 1600 x 1200 Orion WCS [simulated]: each name
    it writes passes the rule, each it refuses says why, and one more tile star beside a named one takes that name."""
    from services.science.catalog import get_catalog_stars
    from services.science.solver import solve_and_verify_field
    from services.science.wcs_models import WCSModel

    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=0.05,
                   rotation_deg=10.0, k1=-0.03, k2=0.005, parity=-1, width=1600, height=1200)
    dets = []
    for k, star in enumerate(get_catalog_stars()):
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if math.isfinite(x) and 0 <= x < 1600 and 0 <= y < 1200:
            dets.append(SourceDetection(x_px=x + 0.2, y_px=y - 0.1, flux=1000.0 - k, snr=100.0 - k, fwhm_px=2.0, centroid_uncertainty_px=0.1))
    sha = hashlib.sha256(b"plate naming").hexdigest()
    moment = {"obs_utc_iso": "2030-01-15T03:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}

    def solve():
        return solve_and_verify_field(dets, 1600, 1200, source_asset_id=f"src_{sha[:16]}", source_sha256=sha, **moment)

    first = solve()
    assert first.status == "calibrated"
    named, refused = _named_and_refused(first)
    assert named and refused and first.quality.matched_inlier_count == len(named)
    assert {l for f in refused for l in f.limitations} <= set(pps.NAMING_REFUSALS.values())
    target = named[0]
    ra, dec = first.wcs.pixel_to_world(target.source_detection.x_px + 1.0, target.source_detection.y_px)
    deep = pps._get_deep_star_index()
    v_target = float(deep.v_mag[list(deep.source_ids).index(target.catalog_star.catalog_source_id)])
    monkeypatch.setattr(pps, "_get_deep_star_index", lambda *a: _with_a_star_beside(deep, ra, dec, v_target))
    second = solve()
    named2, _ = _named_and_refused(second)
    assert [f.catalog_star.id for f in named2] == [f.catalog_star.id for f in named if f is not target]
    assert second.quality.matched_inlier_count == len(named2)


def test_each_deep_star_carries_its_bands_margin() -> None:
    """Real tile rows: Mizar is a Hipparcos row (Hp read as V), Mizar B a Gaia row with BP-RP, and Gaia DR3
    98209860357332864 a Gaia row without one."""
    deep = pps._get_deep_star_index()
    ids = deep.source_ids.tolist()
    assert deep.margin_mag[ids.index("HIP 65378")] == pps.NAMING_MARGIN_HP_MAG
    assert deep.margin_mag[ids.index("Gaia DR3 1563590510627624064")] == pps.NAMING_MARGIN_COLOUR_MAG
    assert deep.margin_mag[ids.index("Gaia DR3 98209860357332864")] == pps.NAMING_MARGIN_NO_COLOUR_MAG


def test_a_three_star_region_is_a_region() -> None:
    triangle = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 4.0]])
    assert pps._inside_checked_region(1.0, 1.0, triangle) is True
    assert pps._inside_checked_region(3.0, 3.0, triangle) is False


def test_an_empty_catalog_finds_no_named_star(named_star) -> None:
    wcs, det, _ = _naming_setup([])
    empty = pps._DeepStarIndex(np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0), [], 2016.0, np.zeros(0), np.zeros(0))
    assert pps.naming_refusals(wcs, [(det, named_star)], GATE, AROUND, 2016.0, empty)[0] == pps.NAMING_REFUSALS["found"]


class _NoSky:
    """A WCS that places the detection nowhere on the sky."""

    scale_deg_per_px = 0.01

    def pixel_to_world(self, x, y):
        return float("nan"), float("nan")

    def world_to_pixel(self, ra, dec):
        return float("nan"), float("nan")


def test_a_detection_the_wcs_puts_nowhere_on_the_sky_is_not_named(named_star) -> None:
    _, det, deep = _naming_setup([])
    assert pps.naming_refusals(_NoSky(), [(det, named_star)], GATE, AROUND, 2016.0, deep)[0] == pps.NAMING_REFUSALS["found"]


@pytest.mark.usefixtures("without_brightness")
def test_a_star_just_inside_the_clearance_is_found_however_the_search_is_cut(named_star) -> None:
    """A star as bright as the named one at 0.99 of the clearance: found and counted."""
    wcs, det, deep = _naming_setup([(0.99 * EDGE, 0.0, 2.0)])
    assert pps.naming_refusals(wcs, [(det, named_star)], GATE, AROUND, 2016.0, deep)[0] == pps.NAMING_REFUSALS["clear"]


@pytest.mark.usefixtures("without_brightness")
def test_a_deep_star_moved_in_from_far_outside_the_search_by_its_proper_motion_refuses_the_name(named_star) -> None:
    """A star as bright as the named one, six clearances away at the tiles' epoch (outside the first search's angle),
    whose proper motion carries it onto the detection by 2056: the search widens by the largest proper motion."""
    wcs, det, _ = _naming_setup([])
    ra0, dec0 = wcs.pixel_to_world(det.x_px, det.y_px)
    ra_far, dec_far = wcs.pixel_to_world(det.x_px, det.y_px + 6.0 * EDGE)
    years = 40.0
    far, here = pps._radec_to_vec_batch(np.array([ra_far, ra0]), np.array([dec_far, dec0]))
    r, d = np.radians(ra_far), np.radians(dec_far)
    east = np.array([-np.sin(r), np.cos(r), 0.0])
    north = np.array([-np.sin(d) * np.cos(r), -np.sin(d) * np.sin(r), np.cos(d)])
    to_mas_yr = 3600.0e3 * 180.0 / np.pi / years
    pm_ra, pm_dec = float((here - far) @ east) * to_mas_yr, float((here - far) @ north) * to_mas_yr
    deep = pps._DeepStarIndex(np.array([ra0, ra_far]), np.array([dec0, dec_far]), np.array([0.0, pm_ra]),
                              np.array([0.0, pm_dec]), ["HIP 1", "Gaia DR3 2"], 2016.0, np.array([2.0, 2.0]),
                              np.full(2, pps.NAMING_MARGIN_COLOUR_MAG))
    assert pps.naming_refusals(wcs, [(det, named_star)], GATE, AROUND, 2016.0 + years, deep)[0] == pps.NAMING_REFUSALS["clear"]


def _capturing(monkeypatch):
    seen = []
    real = pps.naming_refusals

    def spy(wcs, named, gate_px, held_out_xy, epoch_jyear, deep=None):
        seen.append((list(held_out_xy), float(epoch_jyear)))
        return real(wcs, named, gate_px, held_out_xy, epoch_jyear, deep)

    from services.science import solver as plate

    monkeypatch.setattr(pps, "naming_refusals", spy)
    monkeypatch.setattr(plate, "naming_refusals", spy)  # the plate solver holds its own name for it
    return seen


def test_the_phone_solver_checks_names_in_the_held_out_region_at_the_capture_epoch(ci00_frame, monkeypatch) -> None:
    seen = _capturing(monkeypatch)
    sol = _ci00_solve(ci00_frame)
    assert sol.status == "calibrated" and len(seen) == 1
    held, epoch = seen[0]
    # The region is drawn around every star the solution was fitted on or checked against: the held-out inliers and
    # the fit stars, of which the phone solver takes 3 to 8 (`solve_phone_photo`'s training count)
    assert 3 <= len(held) - sol.quality.held_out_inliers <= 8
    jd = pps._parse_iso_to_jd_utc(ci00_frame.truth.metadata["utc"])
    assert epoch == pytest.approx(2000.0 + (jd - pps.JD_J2000) / pps.DAYS_PER_JYEAR, abs=1e-9)
    # A named fit star says its position made the solution; a named held-out star does not
    named = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert any(not f.verified_by_holdout for f in named) and any(f.verified_by_holdout for f in named)
    for f in named:
        assert (pps.NAMING_FIT_STAR_NOTE in f.limitations) is (not f.verified_by_holdout)


def test_the_plate_solver_checks_names_in_the_held_out_region_at_the_capture_epoch_and_counts_them(monkeypatch) -> None:
    from services.science.catalog import get_catalog_stars
    from services.science.solver import solve_and_verify_field
    from services.science.wcs_models import WCSModel

    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=0.05,
                   rotation_deg=10.0, k1=-0.03, k2=0.005, parity=-1, width=1600, height=1200)
    dets = []
    for k, star in enumerate(get_catalog_stars()):
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if math.isfinite(x) and 0 <= x < 1600 and 0 <= y < 1200:
            dets.append(SourceDetection(x_px=x + 0.2, y_px=y - 0.1, flux=1000.0 - k, snr=100.0 - k, fwhm_px=2.0, centroid_uncertainty_px=0.1))
    sha = hashlib.sha256(b"plate naming").hexdigest()
    seen = _capturing(monkeypatch)
    sol = solve_and_verify_field(dets, 1600, 1200, source_asset_id=f"src_{sha[:16]}", source_sha256=sha,
                                 obs_utc_iso="2030-01-15T03:00:00Z", observer_lat=30.0, observer_lon=-40.0)
    held, epoch = seen[0]
    assert len(held) == sol.quality.held_out_inliers + 4  # the held-out inliers and the four fit stars
    assert epoch == pytest.approx(2000.0 + (pps._parse_iso_to_jd_utc("2030-01-15T03:00:00Z") - pps.JD_J2000) / pps.DAYS_PER_JYEAR, abs=1e-9)
    refused = sum(1 for f in sol.findings if f.evidence_class == "observed" and any(l.startswith("Not named:") for l in f.limitations))
    assert refused and any(w.startswith(f"{refused} unmatched") or f"{refused} inside the inlier gate but not named" in w for w in sol.warnings)
    # A named fit star says its position made the solution; a named held-out star does not
    named = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert any(not f.verified_by_holdout for f in named)
    for f in named:
        assert (pps.NAMING_FIT_STAR_NOTE in f.limitations) is (not f.verified_by_holdout)


# solver-12a: each row's margin past the colour clip, and the one-sided zero-point brightness limit


def _g_minus_v_written_out(c: float) -> float:
    """Riello et al. (2021, table C2)'s G - V in BP-RP, written out here so the margins are judged by the formula and
    not by the code's own copy of it."""
    return -0.02704 + 0.01424 * c - 0.2156 * c * c + 0.01426 * c * c * c


LOW, HIGH = -0.5, 2.75  # the polynomial's range, as the solver clips to it
COLOUR = pps.NAMING_MARGIN_COLOUR_MAG
MARGIN_CASES = [
    # (Hipparcos row, BP-RP, margin as the named star, margin as another star)
    (True, float("nan"), pps.NAMING_MARGIN_HP_MAG, pps.NAMING_MARGIN_HP_MAG),
    (True, 3.5, pps.NAMING_MARGIN_HP_MAG, pps.NAMING_MARGIN_HP_MAG),  # a Hipparcos row's colour is not BP-RP
    (False, float("nan"), pps.NAMING_MARGIN_NO_COLOUR_MAG, pps.NAMING_MARGIN_NO_COLOUR_MAG),
    (False, float("inf"), pps.NAMING_MARGIN_NO_COLOUR_MAG, pps.NAMING_MARGIN_NO_COLOUR_MAG),
    (False, 1.0, COLOUR, COLOUR),
    (False, LOW, COLOUR, COLOUR),
    (False, LOW + 0.01, COLOUR, COLOUR),
    (False, LOW - 0.01, COLOUR + _g_minus_v_written_out(LOW) - _g_minus_v_written_out(LOW - 0.01), COLOUR),
    (False, HIGH, COLOUR, COLOUR),
    (False, HIGH - 0.01, COLOUR, COLOUR),
    (False, HIGH + 0.01, COLOUR + _g_minus_v_written_out(HIGH) - _g_minus_v_written_out(HIGH + 0.01), COLOUR),
    (False, 3.321, COLOUR + _g_minus_v_written_out(HIGH) - _g_minus_v_written_out(3.321), COLOUR),  # the reader's red star
    (False, -1.2, COLOUR + _g_minus_v_written_out(LOW) - _g_minus_v_written_out(-1.2), COLOUR),
]


@pytest.mark.parametrize(("hip", "bp_rp", "as_named", "as_other"), MARGIN_CASES)
def test_a_rows_margin_is_what_its_v_estimate_can_be_wrong_by_on_the_side_that_refuses(hip, bp_rp, as_named, as_other) -> None:
    named = pps.naming_margin(np.array([hip]), np.array([bp_rp]), as_named=True)[0]
    other = pps.naming_margin(np.array([hip]), np.array([bp_rp]), as_named=False)[0]
    assert named == pytest.approx(as_named, abs=1e-12)
    assert other == pytest.approx(as_other, abs=1e-12)


def test_past_either_end_of_the_colours_the_estimate_is_too_bright_by_the_trend() -> None:
    """G - V peaks near BP-RP 0.03 and falls on both sides, so past either end the polynomial's own trend puts V
    fainter than the clipped estimate: the change is positive, and one float past each end is never below the
    margin inside."""
    for c in (LOW - 0.01, LOW - 1.0, HIGH + 0.01, HIGH + 2.0):
        assert pps._colour_trend_mag(np.array([c]))[0] > 0.0
    for c in (LOW, HIGH, 0.0, float("nan")):
        assert pps._colour_trend_mag(np.array([c]))[0] == 0.0
    past = np.array([float(np.nextafter(LOW, -np.inf)), float(np.nextafter(HIGH, np.inf))])
    assert np.all(pps.naming_margin(np.zeros(2, dtype=bool), past, as_named=True) >= COLOUR)
    assert np.all(pps.naming_margin(np.zeros(2, dtype=bool), past, as_named=False) == COLOUR)


def test_a_colour_whose_estimate_errs_too_faint_widens_its_margin_as_another_star(monkeypatch) -> None:
    """Both roles are held: were the trend to put V brighter than the estimate, the change would go to the row's
    margin as another star (its light hidden), not as the named star."""
    monkeypatch.setattr(pps, "_colour_trend_mag", lambda c: np.full(np.shape(c), -0.3))
    assert pps.naming_margin(np.array([False]), np.array([3.0]), as_named=True)[0] == pytest.approx(COLOUR)
    assert pps.naming_margin(np.array([False]), np.array([3.0]), as_named=False)[0] == pytest.approx(COLOUR + 0.3)


def test_the_deep_index_carries_both_roles_margins_and_the_trend() -> None:
    deep = pps._get_deep_star_index()
    assert deep.margin_named_mag.shape == deep.margin_mag.shape == deep.trend_mag.shape == deep.v_mag.shape
    assert np.all(deep.margin_named_mag >= deep.margin_mag)
    past = deep.margin_named_mag > deep.margin_mag
    assert int(past.sum()) > 10000  # the reader counted 13,698 rows past the clip, 12,010 with a change above 0.1
    assert np.allclose(deep.margin_named_mag[past] - deep.margin_mag[past], deep.trend_mag[past])
    assert np.all(deep.trend_mag[~past] == 0.0)


@pytest.mark.usefixtures("without_brightness")
def test_the_readers_red_star_is_not_named_beside_a_neighbour_its_true_colour_makes_count(named_star) -> None:
    """AUDIT K's case: Gaia DR3 413463586628312448 (BP-RP 3.32, solver V 6.02) named, Gaia DR3 413465304619260160
    (solver V 8.86) 6.24 px away on a built 84 deg 4032 x 3024 frame, gate 4 px: by the polynomial's trend the
    neighbour gives about 0.118 of the named star's light, so the name is refused."""
    deep = pps._get_deep_star_index()
    ids = deep.source_ids.tolist()
    k, j = ids.index("Gaia DR3 413463586628312448"), ids.index("Gaia DR3 413465304619260160")
    assert deep.margin_named_mag[k] == pytest.approx(COLOUR + _g_minus_v_written_out(HIGH) - _g_minus_v_written_out(3.321), abs=0.01)
    ra, dec = deep.places(np.array([k, j]), deep.epoch_jyear)
    wcs = PhoneCameraWCS.from_pointing(crval_ra_deg=float(ra[0]), crval_dec_deg=float(dec[0]), rotation_deg=0.0,
                                       fov_diagonal_deg=84.0, width=4032, height=3024)
    x, y = wcs.world_to_pixel(float(ra[0]), float(dec[0]))
    det = SourceDetection(x_px=float(x), y_px=float(y), flux=1000.0, snr=100.0, fwhm_px=3.0, centroid_uncertainty_px=0.2)
    star = replace(named_star, catalog_source_id="Gaia DR3 413463586628312448")
    region = [(x - 800.0, y - 800.0), (x + 800.0, y - 800.0), (x + 800.0, y + 800.0), (x - 800.0, y + 800.0)]
    assert pps.naming_refusals(wcs, [(det, star)], 4.0, region, deep.epoch_jyear, deep)[0] == pps.NAMING_REFUSALS["clear"]


def test_the_brightness_constants_are_named_by_their_meaning() -> None:
    assert pps.NAMING_BRIGHTNESS_SCATTERS == 3.0
    assert pps.NAMING_BRIGHTNESS_FLOOR_MAG == pytest.approx(0.75257, abs=1e-5)  # half the light
    assert 10 ** (-0.4 * pps.NAMING_BRIGHTNESS_FLOOR_MAG) == pytest.approx(0.5)
    assert pps.NAMING_ZERO_POINT_MIN_STARS == 5


FLOOR = pps.NAMING_BRIGHTNESS_FLOOR_MAG


def _frame(offsets: list[float], candidate_brighter_by: float, v: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Instrumental magnitudes and V of matched stars whose V - instrumental are `offsets` (the zero point's stars,
    each at V 4), then a candidate at V `v` brighter than the median offset makes it by `candidate_brighter_by`."""
    zp = float(np.median(offsets))
    v_all = np.array([4.0] * len(offsets) + [v])
    inst = np.array([4.0 - o for o in offsets] + [v - zp - candidate_brighter_by])
    return inst, v_all


BRIGHTNESS_CASES = [
    # (zero point stars' offsets, candidate brighter by, the candidate's refusal)
    ([10.0] * 5, FLOOR - 1e-9, None),  # no scatter: the floor holds
    ([10.0] * 5, FLOOR + 1e-9, "bright"),
    ([9.8, 9.9, 10.0, 10.1, 10.2], FLOOR - 1e-9, None),  # three scatters (0.445) under the floor: the floor holds
    ([9.8, 9.9, 10.0, 10.1, 10.2], FLOOR + 1e-9, "bright"),
    ([9.0, 9.5, 10.0, 10.5, 11.0], 3 * 1.4826 * 0.5 - 1e-9, None),  # three scatters (2.224) over the floor
    ([9.0, 9.5, 10.0, 10.5, 11.0], 3 * 1.4826 * 0.5 + 1e-9, "bright"),
    ([10.0] * 5, -10.0, None),  # far fainter than the star (it saturates): one-sided, the name stands
    ([10.0] * 5, 6.0, "bright"),  # a planet's light beside a V 5 star
]


@pytest.mark.parametrize(("offsets", "brighter_by", "refusal"), BRIGHTNESS_CASES)
def test_a_detection_far_brighter_than_its_named_star_can_make_it_is_not_named(offsets, brighter_by, refusal) -> None:
    inst, v = _frame(offsets, brighter_by)
    got = pps._brightness_refusals(inst, v)
    assert got[-1] == refusal
    assert got[:-1] == [None] * len(offsets)  # each zero point star agrees with the others' zero point


def test_with_too_few_other_matches_only_the_brightest_matched_detection_is_refused() -> None:
    inst = np.array([1.0, 2.0, 3.0, 4.0, 5.0])  # five matches: each has four others, one fewer than needed
    assert pps._brightness_refusals(inst, np.full(5, 6.0)) == ["brightest", None, None, None, None]
    tied = np.array([1.0, 1.0, 3.0, 4.0, 5.0])
    assert pps._brightness_refusals(tied, np.full(5, 6.0)) == ["brightest", "brightest", None, None, None]
    # a match whose star was not found has no V: it is neither judged nor counted, but it can be the brightest
    inst2 = np.array([0.5, 1.0, 2.0, 3.0, 4.0, 5.0])
    v2 = np.array([np.nan, 6.0, 6.0, 6.0, 6.0, 6.0])
    assert pps._brightness_refusals(inst2, v2) == [None, None, None, None, None, None]
    # exactly NAMING_ZERO_POINT_MIN_STARS others: the zero point is measured and the brightest, which agrees, stands
    inst3, v3 = _frame([10.0] * 5, 0.0)
    assert pps._brightness_refusals(inst3, v3) == [None] * 6


def test_a_planet_brighter_than_a_tile_star_past_the_frames_zero_point_limit_is_not_given_its_name() -> None:
    """When an uncatalogued source or planet exceeds the paired tile star's V past `max(3 * scatter, 0.753 mag)` (here
    five stars on one zero point with zero scatter, and V -1 light 1 px from a V 5.5 tile star, 6.5 mag brighter),
    `"bright"` refuses the star's name while keeping the five."""
    wcs = PhoneCameraWCS.from_rotation_and_intrinsics(rot_matrix=np.eye(3), fov_diagonal_deg=60.0, width=4032,
                                                      height=3024, sky_frame=None)
    zp = 20.0
    spots = [(1200.0, 1100.0, 3.0), (2800.0, 1100.0, 3.5), (1200.0, 1900.0, 4.0), (2800.0, 1900.0, 4.5),
             (2000.0, 1300.0, 2.5), (2000.0, 1700.0, 5.5)]
    places = [wcs.pixel_to_world(x, y) for x, y, _ in spots]
    ids = [f"Gaia DR3 {k + 1}" for k in range(len(spots))]
    deep = pps._DeepStarIndex(np.array([p[0] for p in places]), np.array([p[1] for p in places]), np.zeros(6),
                              np.zeros(6), ids, 2016.0, np.array([v for _, _, v in spots]), np.full(6, COLOUR))
    base = pps._get_tile_index()
    record = base.to_catalog_star(0, float(base.data["ra0"][0]), float(base.data["dec0"][0]), base.epoch_jyear)
    named = []
    for k, (x, y, v) in enumerate(spots):
        light_v = -1.0 if k == 5 else v  # the last detection is the planet's light, 1 px from the V 5.5 star
        det = SourceDetection(x_px=x + (1.0 if k == 5 else 0.0), y_px=y, flux=10 ** (-0.4 * (light_v - zp)), snr=50.0,
                              fwhm_px=3.0, centroid_uncertainty_px=0.3)
        named.append((det, replace(record, catalog_source_id=ids[k])))
    got = pps.naming_refusals(wcs, named, GATE, AROUND, 2016.0, deep)
    assert got == [None] * 5 + [pps.NAMING_REFUSALS["bright"]]


@pytest.mark.parametrize("scatter", [0.32, 0.96])
def test_the_brightness_clause_at_real_frame_scatters_refuses_past_its_limit_and_leaves_closer_planets_open(
    scatter: float,
) -> None:
    """At the measured real-frame scatters (0.32 to 0.96 mag, where `max(3 * scatter, 0.753 mag)` reaches 0.96 to
    2.88 mag), `naming_refusals` refuses a detection brighter than its paired star by `limit + 0.01` mag (`"bright"`)
    and keeps the star's name at `limit - 0.01` mag, even when the star supplies less than half the combined planet
    plus star light (for example Saturn V 0.6 1 px from a V 2.0 star at 0.96 mag scatter, summed V 0.335, 1.665 mag
    brighter, star share 0.22; closer-brightness planet coincidences remain open for the ephemeris planet net)."""
    wcs = PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3), fov_diagonal_deg=60.0, width=4032, height=3024, sky_frame=None
    )
    zp = 20.0
    step = scatter / 1.4826
    pattern = (-2.0 * step, -step, -step, 0.0, step, step, 2.0 * step)
    companions = [(1100.0 + 300.0 * k, 1200.0 if k % 2 == 0 else 1800.0, 3.0 + 0.3 * k) for k in range(7)]
    base = pps._get_tile_index()
    record = base.to_catalog_star(0, float(base.data["ra0"][0]), float(base.data["dec0"][0]), base.epoch_jyear)
    # Written from the rule's own formula (3 scatters, floor 2.5 log10 2), not read from `pps`, so changing the
    # code's constants moves the boundary past these probes and fails here
    limit = max(3.0 * scatter, 2.5 * math.log10(2.0))

    def judge_target(v_star: float, combined_v: float) -> str | None:
        spots = [*companions, (2016.0, 1512.0, v_star)]
        places = [wcs.pixel_to_world(x, y) for x, y, _ in spots]
        ids = [f"Gaia DR3 {k + 1}" for k in range(len(spots))]
        deep = pps._DeepStarIndex(
            np.array([p[0] for p in places]),
            np.array([p[1] for p in places]),
            np.zeros(len(spots)),
            np.zeros(len(spots)),
            ids,
            2016.0,
            np.array([v for _, _, v in spots]),
            np.full(len(spots), COLOUR),
        )
        named = []
        for k, (x, y, v) in enumerate(spots[:-1]):
            inst = v - zp - pattern[k]
            det = SourceDetection(
                x_px=x, y_px=y, flux=10 ** (-0.4 * inst), snr=50.0, fwhm_px=3.0, centroid_uncertainty_px=0.3
            )
            named.append((det, replace(record, catalog_source_id=ids[k])))
        target_det = SourceDetection(
            x_px=2017.0,
            y_px=1512.0,
            flux=10 ** (-0.4 * (combined_v - zp)),
            snr=50.0,
            fwhm_px=3.0,
            centroid_uncertainty_px=0.3,
        )
        named.append((target_det, replace(record, catalog_source_id=ids[-1])))
        got = pps.naming_refusals(wcs, named, GATE, AROUND, 2016.0, deep)
        assert got[:-1] == [None] * 7
        return got[-1]

    assert judge_target(6.0, 6.0 - (limit + 0.01)) == pps.NAMING_REFUSALS["bright"]
    assert judge_target(6.0, 6.0 - (limit - 0.01)) is None

    if scatter == pytest.approx(0.96):
        saturn_v = 0.6
        summed_far = -2.5 * math.log10(10 ** (-0.4 * saturn_v) + 10 ** (-0.4 * 5.5))
        summed_close = -2.5 * math.log10(10 ** (-0.4 * saturn_v) + 10 ** (-0.4 * 2.0))
        star_share_close = (10 ** (-0.4 * 2.0)) / (10 ** (-0.4 * saturn_v) + 10 ** (-0.4 * 2.0))
        assert star_share_close < 0.5
        assert judge_target(5.5, summed_far) == pps.NAMING_REFUSALS["bright"]
        assert judge_target(2.0, summed_close) is None


@pytest.mark.parametrize(("trend", "brighter_than_estimate", "refusal"), [
    (0.0, FLOOR - 0.3, None),  # no trend: within the floor of its estimate, the name stands
    (0.5, FLOOR - 0.3, "bright"),  # the estimate is too bright by 0.5 by the trend: the star can give less light
    (-0.5, FLOOR + 0.2, "bright"),  # a trend that would make the star brighter is not taken: it would not lean to refusal
])
def test_the_brightness_clause_takes_the_named_stars_v_by_its_colour_trend(trend, brighter_than_estimate, refusal) -> None:
    """Five tile stars whose detections' light agrees with their V by one zero point, and a sixth whose detection is
    brighter than its V estimate makes it by `brighter_than_estimate`: the clause judges it by the estimate plus its
    colour trend where that trend makes the star fainter, and by the estimate alone otherwise."""
    wcs = PhoneCameraWCS.from_rotation_and_intrinsics(rot_matrix=np.eye(3), fov_diagonal_deg=60.0, width=4032,
                                                      height=3024, sky_frame=None)
    zp = 20.0
    spots = [(1200.0, 1100.0, 3.0), (2800.0, 1100.0, 3.5), (1200.0, 1900.0, 4.0), (2800.0, 1900.0, 4.5),
             (2000.0, 1300.0, 2.5), (2000.0, 1700.0, 5.5)]
    places = [wcs.pixel_to_world(x, y) for x, y, _ in spots]
    ids = [f"Gaia DR3 {k + 1}" for k in range(len(spots))]
    deep = pps._DeepStarIndex(np.array([p[0] for p in places]), np.array([p[1] for p in places]), np.zeros(6),
                              np.zeros(6), ids, 2016.0, np.array([v for _, _, v in spots]), np.full(6, COLOUR),
                              np.full(6, COLOUR), np.array([0.0] * 5 + [trend]))
    base = pps._get_tile_index()
    record = base.to_catalog_star(0, float(base.data["ra0"][0]), float(base.data["dec0"][0]), base.epoch_jyear)
    named = []
    for k, (x, y, v) in enumerate(spots):
        light_v = v - (brighter_than_estimate if k == 5 else 0.0)
        det = SourceDetection(x_px=x, y_px=y, flux=10 ** (-0.4 * (light_v - zp)), snr=50.0, fwhm_px=3.0,
                              centroid_uncertainty_px=0.3)
        named.append((det, replace(record, catalog_source_id=ids[k])))
    got = pps.naming_refusals(wcs, named, GATE, AROUND, 2016.0, deep)
    assert got == [None] * 5 + [refusal and pps.NAMING_REFUSALS[refusal]]


def test_a_deep_index_built_without_roles_or_trend_takes_one_margin_and_no_trend() -> None:
    deep = pps._DeepStarIndex(np.array([10.0, 20.0]), np.array([0.0, 5.0]), np.zeros(2), np.zeros(2),
                              ["Gaia DR3 1", "Gaia DR3 2"], 2016.0, np.array([4.0, 5.0]), np.array([0.1, 0.5]))
    np.testing.assert_array_equal(deep.margin_named_mag, deep.margin_mag)
    np.testing.assert_array_equal(deep.trend_mag, np.zeros(2))


def test_the_zero_point_is_the_median_of_the_other_stars_so_one_bright_outlier_does_not_move_it() -> None:
    """Five stars on one zero point, a sixth four magnitudes brighter than its star makes it (a planet), and a
    candidate just past the floor: the median keeps the zero point, so both the sixth and the candidate are refused; a
    mean would have moved it by the sixth and let the candidate stand."""
    inst, v = _frame([10.0] * 5 + [14.0], FLOOR + 0.1)
    assert pps._brightness_refusals(inst, v) == [None] * 5 + ["bright", "bright"]


def test_a_detection_brighter_than_its_star_by_exactly_the_limit_keeps_its_name(monkeypatch) -> None:
    """The clause refuses only by more than its limit: at the limit itself, exactly representable here with a floor
    of one magnitude, the name stands."""
    monkeypatch.setattr(pps, "NAMING_BRIGHTNESS_FLOOR_MAG", 1.0)
    inst = np.array([-6.0] * 5 + [-6.0])  # V 4 stars at zero point 10; the candidate, V 5, brighter by exactly 1.0
    v = np.array([4.0] * 5 + [5.0])
    assert pps._brightness_refusals(inst, v)[-1] is None
    inst[-1] = -6.0 - 2.0 ** -20
    assert pps._brightness_refusals(inst, v)[-1] == "bright"


def test_the_plate_warning_counts_refusals_by_clause() -> None:
    r = pps.NAMING_REFUSALS
    refusals = [r["clear"], f"{r['clear']} {r['bright']}", r["checked"], r["brightest"], f"{r['found']} {r['checked']}"]
    assert pps.naming_refusal_counts(refusals) == (
        "1 whose named star is not among the catalog's stars near it; 2 as another catalog star could be it; "
        "1 as the detection is brighter than the named star can make it; 1 as the brightest match, with too few other "
        "matches to measure the frame's zero point; 2 outside the region of the stars the solution was fitted on and "
        "checked against")
    assert pps.naming_refusal_counts([]) == ""


def test_a_held_out_star_the_reported_solution_was_refined_on_says_so(ci00_frame, monkeypatch) -> None:
    """The phone solver refines its reported solution on the fit stars and up to 18 held-out inliers after the check:
    each named held-out star among those says so, and no other held-out star does. The refinements are spied on: the
    first is the fit on the fit stars, the last the polish after the check."""
    calls: list[list[tuple[float, float]]] = []
    real = pps._refine_phone_camera_wcs

    def spy(wcs, tuples, **kwargs):
        calls.append([(det.x_px, det.y_px) for det, _, _ in tuples])
        return real(wcs, tuples, **kwargs)

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", spy)
    sol = _ci00_solve(ci00_frame)
    assert sol.status == "calibrated"
    fit_on, polished_on = set(calls[0]), calls[-1]
    refined_on = [xy for xy in polished_on if xy not in fit_on]
    assert len(refined_on) == 18  # ci-00 has more held-out inliers than the polish takes, so the bound is in play
    named = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    says = {(f.source_detection.x_px, f.source_detection.y_px) for f in named if pps.NAMING_REFINE_STAR_NOTE in f.limitations}
    assert says and says == {(f.source_detection.x_px, f.source_detection.y_px) for f in named} & set(refined_on)
    assert all(f.verified_by_holdout for f in named if pps.NAMING_REFINE_STAR_NOTE in f.limitations)
    assert not any(pps.NAMING_REFINE_STAR_NOTE in f.limitations and pps.NAMING_FIT_STAR_NOTE in f.limitations for f in named)
