"""The triangle plate solver on dense star fields, with the tile catalog's stars in its pool (solver-10k).

Covers:
- A dense simulated Milky Way frame (thousands of rendered Gaia DR3 and Hipparcos 2007 tile stars, none of the 18
  pinned stars in the field) solves correctly within a stated time bound, through the tile pool above the horizon.
- A southern field with none of the 18 pinned stars (Crux, at a synthetic moment and open-ocean place) solves with
  the tile pool.
- Random dots, and a real star field whose stars are below the horizon at the given moment and place, make no claim.
- A tile-pass claim names only stars inside the frame's field (their angle from the tangent point at most the frame
  corners' angle plus 1 deg), on the frames where folded radial terms once paired stars 62 to 80 deg out: the Crux
  frame 12 h later, the Cygnus frame flipped top to bottom and left to right, and the benchmark's pos-002 and pos-024;
  the tile pass verifies and counts only the field's stars, and refuses a fit whose radial terms fold inside the field.
- PBT: the fold guard refuses exactly the WCS whose r (1 + k1 r^2 + k2 r^4) stops increasing before the frame
  corners' angle plus 1 deg; the field's cone holds exactly the stars within that angle of the tangent point.
- Each finding names its own star (solver-10k v4): on the benchmark's pos-089, pos-124, pos-136, pos-142, pos-017 and
  pos-131 and the auditor's adversarial bounds frame, there is no claim or every catalog match's detection lies within
  3 px of its star's true pixel and nearer it than any other star at V 6.5 or brighter.
- v4's refusals: PBT, the bound rule refuses exactly the fits within 1 % of the 0.24 bound, a fit pushed past the bound
  stops at it; PBT, a pair is ambiguous exactly when another direction projects within three inlier gates, a second
  row at the same place is the same star, an ambiguous pair is neither an inlier nor a match; PBT, coverage refuses
  exactly when the misses beat a 5 % rate at the consensus chance, over the frame or in a cell, and counts a miss only
  on a clear, visible, held-out star inside the frame a magnitude above its depth; the dual-path grid reaches the
  frame's edges and corners; the tile pass claims nothing when a refusal refuses every fit, and a claim passed each on
  its own fit.
- PBT: the ratio lookup returns exactly the catalog triangles within the 0.045 tolerance on both ratios.
- PBT: `verify_held_out` pairs every detection as the one-by-one rule does (nearest unused star, 8 px).
- PBT: the tile pool helpers return stars above the altitude cut with valid distances, brightest first, each V
  limit a prefix of a deeper one.
- PBT: the V limit is the faintest floor-or-deeper step whose expected count in a field-sized cone reaches the
  detections used, never above the cap.
- The bucketed catalog triangles keep today's side bounds and vertex order; the consensus rule; the tile pool's
  gate (the held-out gate, then the coincidence check with its dual-path agreement) on hand-built inputs, and a
  solve that claims nothing when that check fails; no detection a fit trained on is held out; the pinned search
  still scores every PSF-shaped detection; a given catalog is searched alone; the pool for each kind of moment;
  the solver identity names the pool.

Every frame here is simulated: rendered from catalog positions by `render_synthetic_starfield` or by the phone photo
benchmark's renderer, never a recording. The moments and places are synthetic: the fixtures' open North Atlantic
place (30 N, 40 W) and its mirror in the open South Atlantic (30 S, 40 W), and the benchmark frames' own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import itertools
import math
import time

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from scipy.spatial import cKDTree

from research.benchmarks.phone_photos.stars import TileCatalog
from services.science import phone_photo_solver as pps
from services.science import solver as sv
from services.science.catalog import angular_separation_deg, get_all_sky_catalog_stars, get_catalog_stars, icrs_to_altaz
from services.science.catalog_model import CatalogStar
from services.science.phone_photo_solver import tile_stars_above_horizon, tile_stars_all_sky
from services.science.solver import (
    HeldOutVerification,
    SourceDetection,
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    triangle_solver_identity,
    verify_held_out,
)

# Cygnus at 57 degrees altitude (the fixtures' Moon moment), and the same place when Cygnus is 13 degrees below the
# horizon (the fixtures' Orion moment); Crux at 38 degrees from the open South Atlantic
CYGNUS_UP = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
CYGNUS_DOWN = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
CRUX_UP = {"obs_utc_iso": "2031-02-17T09:41:00Z", "observer_lat": -30.0, "observer_lon": -40.0}
MOMENTS = (CYGNUS_UP, CYGNUS_DOWN, CRUX_UP)
# The same place 12 h after CRUX_UP: the Crux frame's centre is 14.8 deg up and one corner 17 deg below the horizon
CRUX_LATE = {"obs_utc_iso": "2031-02-17T21:41:00Z", "observer_lat": -30.0, "observer_lon": -40.0}
# A catalog match lies inside the frame's field when its star's angle from the claimed WCS's tangent point is at most
# the frame corners' angle plus this margin (the solver's FIELD_MARGIN_DEG)
FIELD_MARGIN_DEG = 1.0

# Solve-time bounds in seconds. Measured on an 18-core laptop shared with two other agents' test runs (load average
# 25 to 60): the dense frame's solve took 1.5 to 2.3 s (2.0 to 3.8 s for its whole test), the negatives' solves
# 2.2 to 4.0 s; before solver-10k the random dots took 39 to 41 s. The bounds are about four and five times the
# slowest measured solve [tested]
DENSE_SOLVE_BOUND_S = 15.0
NEGATIVE_SOLVE_BOUND_S = 20.0
# A claim is correct when the median field error on a grid is at most 3 px, the benchmark's bound
# (research/benchmarks/phone_photos/score.py CORRECT_MEDIAN_PX)
CORRECT_MEDIAN_PX = 3.0


@dataclass(frozen=True)
class _RenderedStar:
    """The three values `render_synthetic_starfield` reads from a star: its position and magnitude."""

    ra_deg: float
    dec_deg: float
    mag: float


def _epoch_jyear(obs_utc_iso: str) -> float:
    from datetime import datetime

    when = datetime.fromisoformat(obs_utc_iso.replace("Z", "+00:00"))
    return 2000.0 + (when.timestamp() / 86400.0 + 2440587.5 - 2451545.0) / 365.25


@lru_cache(maxsize=4)
def _scene(
    ra_deg: float, dec_deg: float, width: int, height: int, scale: float, rotation: float, mag_limit: float,
    obs_utc_iso: str, seed: int,
) -> tuple[np.ndarray, WCSModel, int]:
    """A simulated frame of every tile star brighter than `mag_limit` (V) in the field, read through the
    benchmark's own tile reader (not the code under test), rendered with a known TAN and radial distortion WCS."""
    truth = WCSModel(
        crval_ra_deg=ra_deg, crval_dec_deg=dec_deg, crpix_x=width / 2.0, crpix_y=height / 2.0,
        scale_deg_per_px=scale, rotation_deg=rotation, k1=-0.02, k2=0.004, parity=-1, width=width, height=height,
    )
    radius = math.degrees(math.atan(math.radians(0.5 * math.hypot(width, height) * scale))) + 2.0
    field = TileCatalog().cone(ra_deg, dec_deg, radius, mag_limit=mag_limit + 1.0, epoch_jyear=_epoch_jyear(obs_utc_iso))
    stars = [
        _RenderedStar(float(r), float(d), float(v))
        for r, d, v in zip(field.ra_deg, field.dec_deg, field.v_mag)
        if math.isfinite(v) and v <= mag_limit
    ]
    frame, _, projected = render_synthetic_starfield(
        width=width, height=height, wcs=truth, catalog_stars=stars, read_noise_sigma=2.0, flux_scale=950.0, seed=seed,
    )
    return frame, truth, len(projected)


def _cygnus() -> tuple[np.ndarray, WCSModel, int]:
    return _scene(305.0, 40.0, 1600, 1200, 0.04, 12.0, 8.5, CYGNUS_UP["obs_utc_iso"], 5)


def _crux() -> tuple[np.ndarray, WCSModel, int]:
    return _scene(187.0, -60.0, 1200, 900, 0.05, -20.0, 7.0, CRUX_UP["obs_utc_iso"], 3)


def _sources(frame: np.ndarray, tag: str) -> dict[str, str]:
    # The frame's float bytes differ by platform, so the id hashes the recipe, not the pixels
    digest = hashlib.sha256(tag.encode()).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def _field_error_px(truth: WCSModel, solved: WCSModel) -> tuple[float, float]:
    """Median and 95th percentile of where the solved WCS puts the truth's sky on a 9 x 7 grid of pixels."""
    errors = []
    for x in np.linspace(0.1 * truth.width, 0.9 * truth.width, 9):
        for y in np.linspace(0.1 * truth.height, 0.9 * truth.height, 7):
            ra, dec = truth.pixel_to_world(float(x), float(y))
            sx, sy = solved.world_to_pixel(ra, dec)
            errors.append(math.hypot(sx - x, sy - y))
    return float(np.median(errors)), float(np.percentile(errors, 95.0))


def _corner_angle_deg(wcs: WCSModel) -> float:
    """The largest angle from `wcs`'s tangent point of the sky its `pixel_to_world` puts at the frame's corners."""
    corners = ((0.0, 0.0), (wcs.width, 0.0), (0.0, wcs.height), (wcs.width, wcs.height))
    return max(angular_separation_deg(*wcs.pixel_to_world(x, y), wcs.crval_ra_deg, wcs.crval_dec_deg) for x, y in corners)


def _assert_matches_inside_the_field(sol) -> None:
    """Every catalog match names a star inside the frame's field: within the corners' angle plus FIELD_MARGIN_DEG."""
    corner_deg = _corner_angle_deg(sol.wcs)
    for f in sol.findings:
        if f.evidence_class == "catalog_matched":
            sep = angular_separation_deg(f.catalog_star.ra_deg, f.catalog_star.dec_deg, sol.wcs.crval_ra_deg, sol.wcs.crval_dec_deg)
            assert sep <= corner_deg + FIELD_MARGIN_DEG, f"{f.title}: {sep:.1f} deg out, corners {corner_deg:.1f} deg"


def _assert_correct_claim(sol, truth: WCSModel) -> None:
    assert sol.status == "calibrated", (sol.status, sol.warnings, sol.quality)
    median_px, p95_px = _field_error_px(truth, sol.wcs)
    assert median_px <= CORRECT_MEDIAN_PX, (median_px, p95_px)
    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 6
    _assert_matches_inside_the_field(sol)
    # Every match is the right star: the truth puts its catalog position within 4 px of its detection, inside the
    # benchmark's match tolerance of max(4 px, 2 FWHM) (score.py), 5.4 px for the 2.7 px FWHM rendered here
    for f in matched:
        tx, ty = truth.world_to_pixel(f.catalog_star.ra_deg, f.catalog_star.dec_deg)
        assert math.hypot(tx - f.source_detection.x_px, ty - f.source_detection.y_px) <= 4.0, f.title


def _assert_no_claim(sol) -> None:
    assert sol.status != "calibrated", (sol.solver, sol.quality)
    assert sol.wcs is None
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)


# =============================================================================================
# Solves on simulated frames
# =============================================================================================


def _pinned_in_frame(truth: WCSModel) -> list[str]:
    names = []
    for star in get_catalog_stars():
        x, y = truth.world_to_pixel(star.ra_deg, star.dec_deg)
        if not math.isnan(x) and 0 <= x < truth.width and 0 <= y < truth.height:
            names.append(star.common_name)
    return names


def test_a_dense_simulated_milky_way_frame_solves_correctly_within_the_time_bound() -> None:
    frame, truth, rendered = _cygnus()
    assert rendered > 3000 and _pinned_in_frame(truth) == []
    detections = detect_point_sources(frame, snr_threshold=4.0)
    assert len(detections) > 2000
    started = time.monotonic()
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CYGNUS_UP, **_sources(frame, "cygnus-dense"))
    elapsed = time.monotonic() - started
    assert elapsed < DENSE_SOLVE_BOUND_S, f"took {elapsed:.1f} s"
    _assert_correct_claim(sol, truth)
    assert "tile stars above the horizon" in sol.solver.catalog_release
    assert sol.quality.held_out_inliers >= 6 and sol.quality.passed_independent_verification


def test_a_crux_field_with_none_of_the_pinned_stars_solves_with_the_tile_pool() -> None:
    frame, truth, rendered = _crux()
    assert _pinned_in_frame(truth) == [] and rendered > 100
    detections = detect_point_sources(frame, snr_threshold=4.0)
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux"))
    _assert_correct_claim(sol, truth)
    assert "tile stars above the horizon" in sol.solver.catalog_release
    # Alt and az come with the moment and place
    assert all(f.alt_deg is not None and f.alt_deg >= 9.0 for f in sol.findings if f.evidence_class == "catalog_matched")


def _random_dots(width: int = 1600, height: int = 1200, sources: int = 2500, seed: int = 11) -> np.ndarray:
    """Many random point sources on a dark noisy field and no sky behind them [simulated]."""
    rng = np.random.default_rng(seed)
    luma = np.full((height, width), 8.0) + rng.normal(0.0, 1.5, (height, width))
    yy, xx = np.mgrid[0:9, 0:9]
    for x, y, amp in zip(rng.uniform(6, width - 6, sources), rng.uniform(6, height - 6, sources), rng.uniform(60, 240, sources)):
        x0, y0 = int(x) - 4, int(y) - 4
        luma[y0 : y0 + 9, x0 : x0 + 9] += amp * np.exp(-((xx + x0 - x) ** 2 + (yy + y0 - y) ** 2) / (2 * 1.3**2))
    return np.clip(luma, 0, 255)


@pytest.mark.parametrize("moment", [CYGNUS_UP, None], ids=["with-time-and-place", "blind"])
def test_random_dots_make_no_claim(moment: dict | None, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _random_dots()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    assert len(detections) > 1500
    # Every candidate the first score lets through has passed the consensus threshold
    scored: list[tuple[float, int]] = []
    score = sv._score_tile_candidates

    def spy(*args):
        out = score(*args)
        scored.extend((tail, matches) for tail, matches, _, _ in out)
        return out

    monkeypatch.setattr(sv, "_score_tile_candidates", spy)
    started = time.monotonic()
    sol = solve_and_verify_field(detections, 1600, 1200, **(moment or {}), **_sources(frame, "random-dots"))
    elapsed = time.monotonic() - started
    _assert_no_claim(sol)
    assert elapsed < NEGATIVE_SOLVE_BOUND_S, f"took {elapsed:.1f} s"
    assert scored and all(m >= sv.CONSENSUS_MIN_MATCHES and tail <= sv.CONSENSUS_MAX_CHANCE for tail, m in scored)


def test_a_real_field_below_the_horizon_makes_no_claim() -> None:
    frame, truth, _ = _cygnus()
    alt, _ = icrs_to_altaz(truth.crval_ra_deg, truth.crval_dec_deg, CYGNUS_DOWN["observer_lat"], CYGNUS_DOWN["observer_lon"], CYGNUS_DOWN["obs_utc_iso"])
    assert alt < -10.0
    detections = detect_point_sources(frame, snr_threshold=4.0)
    started = time.monotonic()
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CYGNUS_DOWN, **_sources(frame, "cygnus-down"))
    elapsed = time.monotonic() - started
    _assert_no_claim(sol)
    assert elapsed < NEGATIVE_SOLVE_BOUND_S, f"took {elapsed:.1f} s"


# =============================================================================================
# Only stars inside the field: frames where folded radial terms once paired stars far outside it
# =============================================================================================


def _flipped_truth(truth: WCSModel, rows: bool) -> WCSModel:
    """The truth WCS of the frame flipped top to bottom (`rows`) or left to right: the other parity, the rotation
    taken to 180 deg minus it (or to minus it), and the reference pixel moved to its mirror pixel."""
    if rows:
        return replace(truth, parity=-truth.parity, rotation_deg=180.0 - truth.rotation_deg, crpix_y=truth.height - 1 - truth.crpix_y)
    return replace(truth, parity=-truth.parity, rotation_deg=-truth.rotation_deg, crpix_x=truth.width - 1 - truth.crpix_x)


def _fold_case(case: str):
    """(frame, truth WCS, moment) of the unit's own simulated frames in the auditor's reproductions."""
    if case == "crux-12h":
        frame, truth, _ = _crux()
        return frame, truth, CRUX_LATE
    frame, truth, _ = _cygnus()
    rows = case == "cygnus-flipped-top-to-bottom"
    flipped = np.ascontiguousarray(frame[::-1, :] if rows else frame[:, ::-1])
    return flipped, _flipped_truth(truth, rows), CYGNUS_UP


@pytest.mark.parametrize("case", ["crux-12h", "cygnus-flipped-top-to-bottom", "cygnus-flipped-left-to-right"])
def test_a_tile_pass_claim_names_only_stars_inside_the_field(case: str) -> None:
    frame, truth, moment = _fold_case(case)
    detections = detect_point_sources(frame, snr_threshold=4.0)
    sol = solve_and_verify_field(detections, truth.width, truth.height, **moment, **_sources(frame, case))
    assert "tile stars above the horizon" in sol.solver.catalog_release
    # Still the right field, every match the right star and inside the field
    _assert_correct_claim(sol, truth)


@lru_cache(maxsize=1)
def _benchmark_specs() -> dict:
    from research.benchmarks.phone_photos import suite

    return {spec.frame_id: spec for spec in suite.build_suite("full")}


class _VectorWcs:
    """A claimed WCS seen through the benchmark scorer's interface, one direction at a time."""

    def __init__(self, wcs: WCSModel) -> None:
        self.wcs = wcs

    def world_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        out = np.array([self.wcs.world_to_pixel(float(r), float(d)) for r, d in zip(ra_deg, dec_deg)], dtype=np.float64).reshape(-1, 2)
        return out[:, 0], out[:, 1]


def _solve_benchmark_frame(frame_id: str):
    """(rendered frame, solution) of a phone photo benchmark frame as its today-api adapter runs it (solvers.py): luma,
    default detection, the frame's own time and place."""
    from research.benchmarks.phone_photos import render, solvers

    frame = render.render_frame(_benchmark_specs()[frame_id], TileCatalog())
    luma = solvers._luma(frame.rgb)
    height, width = luma.shape
    meta = frame.truth.metadata
    sol = solve_and_verify_field(
        detect_point_sources(luma), width, height, obs_utc_iso=meta["utc"], observer_lat=meta["latDeg"],
        observer_lon=meta["lonDeg"], **_sources(frame.rgb, f"benchmark-{frame_id}"),
    )
    return frame, sol


@pytest.mark.parametrize("frame_id", ["pos-002", "pos-024"])
def test_a_benchmark_frame_claim_names_only_stars_inside_the_field(frame_id: str) -> None:
    from research.benchmarks.phone_photos import score, solvers

    frame, sol = _solve_benchmark_frame(frame_id)
    assert sol.status == "calibrated" and "tile stars above the horizon" in sol.solver.catalog_release
    # Still correct by the benchmark's own score: median field error at most 3 px
    errors = solvers.field_errors_px(frame.truth.camera_truth, _VectorWcs(sol.wcs), frame.truth.sky_mask)
    assert float(np.median(errors)) <= score.CORRECT_MEDIAN_PX
    _assert_matches_inside_the_field(sol)
    # Every match is the right star by the scorer's tolerance, max(4 px, 2 FWHM, 1.5 trail lengths)
    tolerance = max(4.0, 2.0 * frame.truth.psf_fwhm_px, 1.5 * frame.truth.trail_px)
    for f in sol.findings:
        if f.evidence_class == "catalog_matched":
            tx, ty = frame.truth.camera_truth.icrs_to_pixel(np.array([f.catalog_star.ra_deg]), np.array([f.catalog_star.dec_deg]))
            assert math.hypot(float(tx[0]) - f.source_detection.x_px, float(ty[0]) - f.source_detection.y_px) <= tolerance, f.title


# =============================================================================================
# Each finding names its own star: frames where a calibrated tile-pass claim once named, inside the field, a star 3 to
# 42 px from its true pixel, often a neighbour whose own detection sat elsewhere
# =============================================================================================

# A catalog_matched finding names its own star when its detection lies within OWN_STAR_MAX_PX of that star's true
# pixel and nearer to it than to the true pixel of any other star the tile pass could name: a rendered star at
# NAMEABLE_V_LIMIT or brighter (TILE_V_CAP, the deepest pool) whose true pixel lies at least one PSF FWHM from the named
# star's (closer, the two make one image). Fainter rendered stars are left out: the benchmark renders to V 12, about 0.07
# stars per square pixel, so one lies within a pixel of any detection by chance
OWN_STAR_MAX_PX = 3.0
NAMEABLE_V_LIMIT = 6.5
# The FWHM of `render_synthetic_starfield`'s Gaussian PSF (sigma 1.15 px)
SYNTHETIC_FWHM_PX = 2.0 * math.sqrt(2.0 * math.log(2.0)) * 1.15


def _assert_each_finding_names_its_own_star(sol, true_pixel, others_xy: np.ndarray, fwhm_px: float) -> None:
    """No claim, or a claim in which every catalog_matched finding's detection lies within OWN_STAR_MAX_PX of its own
    star's true pixel (`true_pixel(ra, dec)`, NaN where the truth puts it nowhere) and nearer to it than to the true
    pixel of any star of `others_xy` lying at least `fwhm_px` from it."""
    if sol.status != "calibrated":
        _assert_no_claim(sol)
        return
    wrong = []
    for f in sol.findings:
        if f.evidence_class != "catalog_matched":
            continue
        tx, ty = true_pixel(f.catalog_star.ra_deg, f.catalog_star.dec_deg)
        dx, dy = f.source_detection.x_px, f.source_detection.y_px
        own = math.hypot(tx - dx, ty - dy)
        apart = np.hypot(others_xy[:, 0] - tx, others_xy[:, 1] - ty) if math.isfinite(own) else np.full(len(others_xy), np.inf)
        other = np.hypot(others_xy[:, 0] - dx, others_xy[:, 1] - dy)[apart >= fwhm_px]
        nearest_other = float(other.min()) if other.size else math.inf
        if not (own <= OWN_STAR_MAX_PX and own < nearest_other):
            kind = "held out" if f.verified_by_holdout else "trained the fit"
            wrong.append(f"{f.title} ({kind}): {own:.2f} px from its true pixel, another star's {nearest_other:.2f} px")
    assert not wrong, wrong


# pos-089, pos-136 and pos-142 named a neighbour or a star 6 to 42 px off on fits whose k2 sat at or near the 0.24
# bound; pos-124 named stars the truth puts off the frame; pos-017 named a training pair 6.5 px off and a blend's fainter
# star; pos-131 named the fainter of two stars 3.7 px apart [tested, 2026-11-11, on 5f8d1e43]
@pytest.mark.parametrize("frame_id", ["pos-089", "pos-124", "pos-136", "pos-142", "pos-017", "pos-131"])
def test_a_benchmark_frame_claim_names_each_star_at_its_own_true_pixel(frame_id: str) -> None:
    from research.benchmarks.phone_photos import score, solvers

    frame, sol = _solve_benchmark_frame(frame_id)
    truth = frame.truth
    bright = np.asarray(truth.star_v_mag) <= NAMEABLE_V_LIMIT

    def true_pixel(ra: float, dec: float) -> tuple[float, float]:
        tx, ty = truth.camera_truth.icrs_to_pixel(np.array([ra]), np.array([dec]))
        return float(tx[0]), float(ty[0])

    _assert_each_finding_names_its_own_star(sol, true_pixel, np.column_stack([truth.star_x[bright], truth.star_y[bright]]), truth.psf_fwhm_px)
    if sol.status == "calibrated":
        # And correct by the benchmark's own score (pos-089's claim was imprecise, a false claim there)
        errors = solvers.field_errors_px(truth.camera_truth, _VectorWcs(sol.wcs), truth.sky_mask)
        assert float(np.median(errors)) <= score.CORRECT_MEDIAN_PX


@lru_cache(maxsize=1)
def _adversarial_bounds_frame() -> tuple[np.ndarray, WCSModel, float, list]:
    """The auditor's adversarial bounds frame (adv.py, ADV_ONLY="k2 -0.12 diag 70.0 N"), simulated: 1600 x 1200 px,
    70 deg diagonal, barrel terms k1 = k2 = -0.12, rotated 30 deg, centred 55 deg up at azimuth 200 deg at CYGNUS_UP;
    the tile stars to V 7.5 within the corners' angle plus 1 deg that are above the horizon. Returns the frame, the
    truth, that reach and the rendered stars with their true pixels."""
    from services.science.catalog import altaz_to_icrs

    width, height, diag_deg = 1600, 1200, 70.0
    when, lat, lon = CYGNUS_UP["obs_utc_iso"], CYGNUS_UP["observer_lat"], CYGNUS_UP["observer_lon"]
    ra, dec = altaz_to_icrs(55.0, 200.0, lat, lon, when)
    scale = math.degrees(math.tan(math.radians(diag_deg / 2.0))) / math.hypot(width / 2.0, height / 2.0)
    truth = WCSModel(crval_ra_deg=ra % 360.0, crval_dec_deg=dec, crpix_x=width / 2.0, crpix_y=height / 2.0, scale_deg_per_px=scale,
                     rotation_deg=30.0, k1=-0.12, k2=-0.12, parity=-1, width=width, height=height)
    reach = _corner_angle_deg(truth) + 1.0
    field = TileCatalog().cone(truth.crval_ra_deg, truth.crval_dec_deg, reach, mag_limit=8.5, epoch_jyear=_epoch_jyear(when))
    stars = []
    for r, d, v in zip(field.ra_deg, field.dec_deg, field.v_mag):
        if math.isfinite(v) and v <= 7.5 and icrs_to_altaz(float(r), float(d), lat, lon, when)[0] >= 0.0:
            stars.append(_RenderedStar(float(r), float(d), float(v)))
    frame, _, projected = render_synthetic_starfield(width=width, height=height, wcs=truth, catalog_stars=stars, read_noise_sigma=2.0,
                                                     flux_scale=950.0, seed=2)
    return frame, truth, reach, projected


def test_the_adversarial_bounds_frame_claim_names_each_star_at_its_own_true_pixel() -> None:
    # Its claim on 5f8d1e43 fitted k2 0.240 (the truth's is -0.12) and named a held-out star 22.5 px off
    frame, truth, reach, projected = _adversarial_bounds_frame()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CYGNUS_UP, **_sources(frame, "adversarial-bounds-k-0.12"))

    def true_pixel(ra: float, dec: float) -> tuple[float, float]:
        if angular_separation_deg(ra, dec, truth.crval_ra_deg, truth.crval_dec_deg) > reach:
            return math.nan, math.nan
        return truth.world_to_pixel(ra, dec)

    others = np.array([(x, y) for star, x, y in projected if star.mag <= NAMEABLE_V_LIMIT], dtype=np.float64).reshape(-1, 2)
    _assert_each_finding_names_its_own_star(sol, true_pixel, others, SYNTHETIC_FWHM_PX)
    if sol.status == "calibrated":
        assert _field_error_px(truth, sol.wcs)[0] <= CORRECT_MEDIAN_PX


def test_the_tile_pass_verifies_and_counts_only_the_stars_inside_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, moment = _fold_case("crux-12h")
    pools: list[list[CatalogStar]] = []
    verified: list[tuple[WCSModel, list[CatalogStar]]] = []
    counts: list[int] = []
    pool_for, verify, coincidence = sv._tile_pool_for, sv.verify_held_out, sv.evaluate_coincidence_gate

    def spy_pool(*args):
        out = pool_for(*args)
        pools.append(out[0])
        return out

    def spy_verify(wcs, dets, catalog, training):
        verified.append((wcs, list(catalog)))
        return verify(wcs, dets, catalog, training)

    def spy_coincidence(**kwargs):
        counts.append(kwargs["projected_catalog_count"])
        return coincidence(**kwargs)

    monkeypatch.setattr(sv, "_tile_pool_for", spy_pool)
    monkeypatch.setattr(sv, "verify_held_out", spy_verify)
    monkeypatch.setattr(sv, "evaluate_coincidence_gate", spy_coincidence)
    sol = solve_and_verify_field(detect_point_sources(frame, snr_threshold=4.0), truth.width, truth.height, **moment, **_sources(frame, "crux-12h"))
    assert sol.status == "calibrated" and "tile stars" in sol.solver.catalog_release
    wcs, catalog = verified[-1]
    assert wcs == sol.wcs
    # The claim's verification saw exactly the pool's stars within the corners' angle plus the margin, in pool order
    limit = _corner_angle_deg(wcs) + FIELD_MARGIN_DEG
    seps = {s.id: angular_separation_deg(s.ra_deg, s.dec_deg, wcs.crval_ra_deg, wcs.crval_dec_deg) for s in pools[-1]}
    clear = [s for s in pools[-1] if abs(seps[s.id] - limit) > 1e-6]
    inside = [s.id for s in clear if seps[s.id] <= limit]
    assert [s.id for s in catalog if abs(seps[s.id] - limit) > 1e-6] == inside
    assert len(inside) < len(pools[-1])
    # And the coincidence check counted those of them the claim's WCS puts inside the frame, no other
    in_frame = 0
    for star in catalog:
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        in_frame += bool(not math.isnan(x) and 0.0 <= x < truth.width and 0.0 <= y < truth.height)
    assert counts[-1] == in_frame


def test_the_tile_pass_refuses_a_fit_whose_radial_terms_fold_inside_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, _ = _crux()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    checked: list[WCSModel] = []
    guard = sv._folds_inside_field

    def spy(wcs):
        checked.append(wcs)
        return guard(wcs)

    monkeypatch.setattr(sv, "_folds_inside_field", spy)
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux"))
    # The claimed fit, with its radial terms, is the last one the guard passed
    assert sol.status == "calibrated" and checked and checked[-1] == sol.wcs and not guard(sol.wcs)
    # Were every fit to fold inside the field, the tile pass would claim nothing
    monkeypatch.setattr(sv, "_folds_inside_field", lambda wcs: True)
    _assert_no_claim(solve_and_verify_field(detections, truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux")))


def _folds_by_scan(k1: float, k2: float, corner_radius: float) -> tuple[bool | None, bool | None]:
    """(whether r (1 + k1 r^2 + k2 r^4) stops increasing before the frame corners' angle plus FIELD_MARGIN_DEG,
    whether the corners lie past that turn), read off a scan of 200,001 tangent-plane radii r from 0 to 50 (88.9 deg,
    where the phone solver's rule stops looking): the corners sit at the first scanned r whose distorted radius reaches
    `corner_radius`, and lie past the turn when none does before the scan stops increasing. None for an answer within
    0.01 deg (or a relative 1e-6 of the distorted radius) of the scan's turning point."""
    r = np.linspace(0.0, 50.0, 200_001)
    f = r * (1.0 + k1 * r**2 + k2 * r**4)
    falls = np.flatnonzero(np.diff(f) <= 0.0)
    stop = int(falls[0]) if falls.size else r.size - 1
    turn_deg = math.degrees(math.atan(r[stop]))
    reached = np.flatnonzero(f[: stop + 1] >= corner_radius)
    if reached.size == 0:
        return (None, None) if f[stop] >= corner_radius * (1.0 - 1e-6) else (True, True)
    past = None if f[stop] <= corner_radius * (1.0 + 1e-6) else False
    reach_deg = math.degrees(math.atan(r[reached[0]])) + FIELD_MARGIN_DEG
    if abs(reach_deg - turn_deg) < 0.01:
        return None, past
    return reach_deg >= turn_deg, past


def _frame_wcs(k1: float, k2: float, width: int, height: int, corner_deg: float, ra: float = 30.0, dec: float = -20.0) -> WCSModel:
    """A WCS centred on its frame whose corners lie `corner_deg` from the tangent point before the radial terms."""
    scale = math.degrees(math.tan(math.radians(corner_deg))) / math.hypot(width / 2.0, height / 2.0)
    return WCSModel(crval_ra_deg=ra, crval_dec_deg=dec, crpix_x=width / 2.0, crpix_y=height / 2.0, scale_deg_per_px=scale,
                    rotation_deg=0.0, k1=k1, k2=k2, parity=-1, width=width, height=height)


@settings(max_examples=200, deadline=None)
@given(
    k1=st.floats(min_value=-0.25, max_value=0.25),
    k2=st.floats(min_value=-0.25, max_value=0.25),
    width=st.integers(min_value=64, max_value=4032),
    height=st.integers(min_value=64, max_value=4032),
    corner_deg=st.floats(min_value=2.0, max_value=75.0),
)
# The crux-12h fit (turns 54 deg out, corners 33.5 deg): kept
@example(k1=0.0047, k2=-0.0574, width=1200, height=900, corner_deg=33.0)
# The strongest fold the bounds allow turns 38.7 deg out: corners past it, then corners 0.5 deg inside it (so within
# the margin), then 3.1 deg inside it
@example(k1=-0.25, k2=-0.25, width=1600, height=1200, corner_deg=36.0)
@example(k1=-0.25, k2=-0.25, width=1600, height=1200, corner_deg=30.53)
@example(k1=-0.25, k2=-0.25, width=1600, height=1200, corner_deg=30.0)
# The corners' angle plus the margin 0.03 deg past the turn (refused) and 0.03 deg short of it (kept), for that fold
# (turn 38.73 deg) and the crux-12h fit's (turn 53.98 deg)
@example(k1=-0.25, k2=-0.25, width=1600, height=1200, corner_deg=30.4854)
@example(k1=-0.25, k2=-0.25, width=1600, height=1200, corner_deg=30.478)
@example(k1=0.0047, k2=-0.0574, width=1200, height=900, corner_deg=47.7742)
@example(k1=0.0047, k2=-0.0574, width=1200, height=900, corner_deg=47.7639)
# Two turning points (k1 < 0 < k2): the first one counts
@example(k1=-0.25, k2=0.02, width=4032, height=3024, corner_deg=40.0)
# No turning point
@example(k1=0.25, k2=0.25, width=4032, height=3024, corner_deg=75.0)
@example(k1=0.0, k2=0.0, width=64, height=64, corner_deg=2.0)
def test_pbt_the_fold_guard_refuses_exactly_the_wcs_that_fold_inside_the_field(k1, k2, width, height, corner_deg) -> None:
    wcs = _frame_wcs(k1, k2, width, height, corner_deg)
    expected, past = _folds_by_scan(k1, k2, math.radians(wcs.scale_deg_per_px * math.hypot(width / 2.0, height / 2.0)))
    # The corners have an angle on the rising part exactly when they lie before the turn
    if past is not None:
        assert (sv._field_corner_angle_deg(wcs) is None) is past
    if expected is not None:
        assert sv._folds_inside_field(wcs) is expected


@settings(max_examples=80, deadline=None)
@given(
    k1=st.floats(min_value=-0.25, max_value=0.25),
    k2=st.floats(min_value=-0.25, max_value=0.25),
    width=st.integers(min_value=64, max_value=4032),
    height=st.integers(min_value=64, max_value=4032),
    corner_deg=st.floats(min_value=2.0, max_value=60.0),
    dec=st.floats(min_value=-89.0, max_value=89.0),
    seed=st.integers(0, 2**16),
)
@example(k1=0.0047, k2=-0.0574, width=1200, height=900, corner_deg=33.0, dec=-60.0, seed=1)
@example(k1=-0.25, k2=0.25, width=1600, height=1200, corner_deg=50.0, dec=89.0, seed=2)
def test_pbt_the_field_cone_holds_exactly_the_stars_within_the_corners_angle_plus_the_margin(k1, k2, width, height, corner_deg, dec, seed) -> None:
    wcs = _frame_wcs(k1, k2, width, height, corner_deg, ra=200.0, dec=dec)
    if sv._folds_inside_field(wcs):
        return
    rng = np.random.default_rng(seed)
    # 400 directions over the sky around the field, a quarter of them close to the cone's edge
    limit = _corner_angle_deg(wcs) + FIELD_MARGIN_DEG
    centre = np.array([math.cos(math.radians(dec)) * math.cos(math.radians(200.0)), math.cos(math.radians(dec)) * math.sin(math.radians(200.0)), math.sin(math.radians(dec))])
    vec = rng.normal(size=(400, 3))
    vec /= np.linalg.norm(vec, axis=1, keepdims=True)
    edge = rng.normal(size=(100, 3))
    edge -= np.outer(edge @ centre, centre)
    edge /= np.linalg.norm(edge, axis=1, keepdims=True)
    angle = np.radians(limit + rng.uniform(-0.5, 0.5, 100))
    vec[:100] = np.cos(angle)[:, None] * centre + np.sin(angle)[:, None] * edge
    ra = np.degrees(np.arctan2(vec[:, 1], vec[:, 0])) % 360.0
    de = np.degrees(np.arcsin(np.clip(vec[:, 2], -1.0, 1.0)))
    got = sv._field_cone_members(wcs, cKDTree(vec))
    seps = [angular_separation_deg(float(r), float(d), wcs.crval_ra_deg, wcs.crval_dec_deg) for r, d in zip(ra, de)]
    clear = [i for i, sep in enumerate(seps) if abs(sep - limit) > 1e-6]
    assert [i for i in got if i in set(clear)] == [i for i in clear if seps[i] <= limit]
    assert got == sorted(got)


# =============================================================================================
# The pieces: ratio lookup, held-out pairing, tile pool, V limit, triangles, consensus, gate, identity
# =============================================================================================


ratio = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@settings(max_examples=60, deadline=None)
@given(
    ratios=st.lists(st.tuples(ratio, ratio), min_size=0, max_size=60),
    query=st.tuples(ratio, ratio),
)
@example(ratios=[(0.5, 0.5), (0.545, 0.455), (0.5451, 0.5), (0.5, 0.4549)], query=(0.5, 0.5))
# 0.04 off on either ratio is inside; 5e-10 past the tolerance on either is outside, though the box query reaches it
@example(ratios=[(0.54, 0.5), (0.5, 0.46), (0.5450000005, 0.5), (0.5, 0.4549999995)], query=(0.5, 0.5))
# 0.0449 off on one ratio or both, just inside the tolerance: the box query must reach the whole 0.045
@example(ratios=[(0.5449, 0.5), (0.5, 0.4551), (0.4551, 0.5449)], query=(0.5, 0.5))
def test_pbt_the_ratio_lookup_returns_exactly_the_triangles_within_the_tolerance(
    ratios: list[tuple[float, float]], query: tuple[float, float]
) -> None:
    index = sv._TriangleIndex.from_ratios(np.array(ratios, dtype=np.float64).reshape(-1, 2))
    dr1, dr2 = query
    expected = [i for i, (r1, r2) in enumerate(ratios) if not (abs(dr1 - r1) > 0.045 or abs(dr2 - r2) > 0.045)]
    assert index.lookup(dr1, dr2).tolist() == expected


def _brute_force_pairs(wcs, detections, catalog):
    """`verify_held_out`'s pairing written one detection and one star at a time, as it was before solver-10k."""
    out = []
    used: set[str] = set()
    for det in detections:
        if det.is_hot_pixel_impulse:
            continue
        best, best_res = None, float("inf")
        for star in catalog:
            if star.id in used:
                continue
            px, py = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
            if math.isnan(px):
                continue
            res = math.hypot(px - det.x_px, py - det.y_px)
            if res < best_res:
                best, best_res = star, res
        if best is not None and best_res <= sv.ASSOCIATION_RADIUS_PX:
            out.append((det, best, best_res))
            used.add(best.id)
    return out


@lru_cache(maxsize=1)
def _orion_wcs_and_stars() -> tuple[WCSModel, list[CatalogStar]]:
    """The renderer's default WCS and a made-up dense catalog: 300 copies of a pinned row at seeded positions over
    the field and around it (made-up positions, used only to compare two pairing rules)."""
    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=480.0, crpix_y=270.0, scale_deg_per_px=0.062,
                   rotation_deg=4.5, k1=-0.035, k2=0.008, parity=-1, width=960, height=540)
    rng = np.random.default_rng(2)
    base = get_catalog_stars()[0]
    stars = [
        replace(base, id=f"made-up-{k}", ra_deg=float(ra), dec_deg=float(dec))
        for k, (ra, dec) in enumerate(zip(rng.uniform(50.0, 118.0, 300), rng.uniform(-18.0, 30.0, 300)))
    ]
    # Every tenth star has a twin right after it at the same place, so a tie must go to the first in catalog order
    with_twins = []
    for k, star in enumerate(stars):
        with_twins.append(star)
        if k % 10 == 0:
            with_twins.append(replace(star, id=f"made-up-twin-{k}"))
    return wcs, with_twins


@settings(max_examples=30, deadline=None)
@given(
    offsets=st.lists(
        st.tuples(st.floats(-12.0, 12.0), st.floats(-12.0, 12.0), st.booleans()), min_size=0, max_size=40
    ),
    extra=st.lists(st.tuples(st.floats(0.0, 959.0), st.floats(0.0, 539.0)), max_size=20),
    seed=st.integers(0, 2**16),
)
@example(offsets=[(0.0, 0.0, False), (0.0, 0.0, False), (8.0, 0.0, False), (8.0001, 0.0, False)], extra=[], seed=0)
def test_pbt_verify_held_out_pairs_as_the_one_by_one_rule(offsets, extra, seed) -> None:
    wcs, stars = _orion_wcs_and_stars()
    rng = np.random.default_rng(seed)
    in_frame = []
    for star in stars:
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if not math.isnan(x) and 0 <= x < 960 and 0 <= y < 540:
            in_frame.append((x, y))
    detections = []
    for k, (dx, dy, hot) in enumerate(offsets):
        x, y = in_frame[int(rng.integers(len(in_frame)))] if in_frame else (480.0, 270.0)
        detections.append(SourceDetection(x_px=x + dx, y_px=y + dy, flux=100.0 + k, snr=20.0, fwhm_px=2.0 if not hot else 0.6,
                                          centroid_uncertainty_px=0.1, peak_to_total_ratio=0.2 if not hot else 0.9))
    detections += [SourceDetection(x_px=x, y_px=y, flux=50.0, snr=8.0, fwhm_px=2.0, centroid_uncertainty_px=0.1) for x, y in extra]
    training = detections[::3]
    got = verify_held_out(wcs, detections, stars, training)
    want = _brute_force_pairs(wcs, detections, stars)
    assert [(id(d), s.id) for d, s, _, _ in got.associations] == [(id(d), s.id) for d, s, _ in want]
    assert all(math.isclose(a[2], b[2], abs_tol=1e-9) for a, b in zip(got.associations, want))
    trained_ids = {id(d) for d in training}
    assert [is_train for _, _, _, is_train in got.associations] == [id(d) in trained_ids for d, _, _ in want]


@settings(max_examples=12, deadline=None)
@given(
    moment=st.sampled_from(MOMENTS),
    v_limit=st.floats(min_value=2.0, max_value=6.5),
    min_alt=st.floats(min_value=5.0, max_value=40.0),
)
@example(moment=CRUX_UP, v_limit=4.5, min_alt=10.0)
def test_pbt_the_tile_pool_holds_stars_above_the_cut_with_valid_distances_brightest_first(moment, v_limit, min_alt) -> None:
    when, lat, lon = moment["obs_utc_iso"], moment["observer_lat"], moment["observer_lon"]
    pool = tile_stars_above_horizon(when, lat, lon, v_limit, min_alt)
    deeper = tile_stars_above_horizon(when, lat, lon, v_limit + 0.5, min_alt)
    # A V limit is a prefix of a deeper one: the pool is in V order
    assert [s.id for s in deeper[: len(pool)]] == [s.id for s in pool] and len(deeper) >= len(pool)
    assert len({s.id for s in pool}) == len(pool)
    for star in pool:
        assert 0.0 < star.lower_pc <= star.median_pc <= star.upper_pc
        # The cut is on the refracted altitude; at 5 degrees and above the airless altitude is at most 0.16 degrees
        # lower (Saemundsson), and icrs_to_altaz leaves out up to 37 arcsec of nutation and aberration
        alt, _ = icrs_to_altaz(star.ra_deg, star.dec_deg, lat, lon, when)
        assert alt >= min_alt - 0.2, (star.id, alt)
    everywhere = tile_stars_all_sky(v_limit, when)
    assert {s.id for s in pool} <= {s.id for s in everywhere}


def test_the_all_sky_pool_without_a_time_is_at_the_tiles_epoch_and_brightest_first() -> None:
    pool = tile_stars_all_sky(3.5)
    assert len(pool) > 200
    # Exactly the index's rows at V 3.5 or brighter that carry a valid distance
    index = pps._get_tile_index()
    assert len(pool) == sum(index.has_distance(int(r)) for r in np.flatnonzero(index.data["v_mag"] <= 3.5))
    assert {s.epoch_year for s in pool} == {2016.0}
    assert [s.id for s in tile_stars_all_sky(4.0)[: len(pool)]] == [s.id for s in pool]
    with pytest.raises(ValueError):
        tile_stars_all_sky(float("nan"))
    with pytest.raises(ValueError):
        tile_stars_above_horizon(CRUX_UP["obs_utc_iso"], -30.0, -40.0, 4.5, 91.0)


@settings(max_examples=60, deadline=None)
@given(n_used=st.integers(min_value=0, max_value=150), all_sky=st.booleans(), density=st.floats(min_value=1.0, max_value=4000.0))
@example(n_used=150, all_sky=False, density=1.0)
@example(n_used=0, all_sky=True, density=100.0)
@example(n_used=40, all_sky=False, density=650.0)
def test_pbt_the_v_limit_is_the_shallowest_step_whose_cone_holds_the_detections_used(n_used, all_sky, density) -> None:
    # A made-up pool whose count grows by `density` stars per magnitude past V 2
    def count_at(v: float) -> int:
        return int(density * max(0.0, v - 2.0) ** 2)

    region = 4.0 * math.pi if all_sky else 2.0 * math.pi * (1.0 - math.sin(math.radians(sv.TILE_MIN_ALTITUDE_DEG)))
    floor = sv.TILE_V_FLOOR_ALL_SKY if all_sky else sv.TILE_V_FLOOR_ABOVE_HORIZON
    v = sv._tile_v_limit(n_used, count_at, region, floor)
    share = 2.0 * math.pi * (1.0 - math.cos(math.radians(sv.FIELD_CONE_RADIUS_DEG))) / region
    assert floor <= v <= sv.TILE_V_CAP
    assert math.isclose(round((v - floor) / sv.TILE_V_STEP), (v - floor) / sv.TILE_V_STEP, abs_tol=1e-6)
    if v < sv.TILE_V_CAP:
        assert count_at(v) * share >= n_used
    if v > floor:
        assert count_at(round(v - sv.TILE_V_STEP, 6)) * share < n_used


def test_the_v_limit_reaches_no_further_than_needed_and_stops_at_the_cap() -> None:
    region = 4.0 * math.pi
    # No detection to match keeps the floor even when the pool is empty there
    assert sv._tile_v_limit(0, lambda v: 0, region, 4.5) == 4.5
    # A pool that never fills a cone stops at the cap
    assert sv._tile_v_limit(150, lambda v: 10, region, 3.5) == sv.TILE_V_CAP


def test_the_plan_s_values_are_the_ones_in_use() -> None:
    # The values the lead's plan and the search before solver-10k fix (see the constants' comments in solver.py)
    assert (sv.TRIANGLE_RATIO_TOLERANCE, sv.TRIANGLE_MIN_SIDE_DEG, sv.TRIANGLE_MAX_SIDE_DEG) == (0.045, 2.5, 62.0)
    assert (sv.DETECTOR_POOL_SIZE, sv.DETECTOR_MIN_SIDE_PX, sv.PINNED_POOL_SIZE, sv.PINNED_MATCH_RADIUS_PX) == (14, 25.0, 18, 18.0)
    assert (sv.TILE_MIN_ALTITUDE_DEG, sv.TILE_V_FLOOR_ABOVE_HORIZON, sv.TILE_V_FLOOR_ALL_SKY, sv.TILE_V_CAP) == (10.0, 4.5, 3.5, 6.5)
    assert (sv.SCORING_DETECTION_COUNT, sv.TILE_CELL_ORDER, sv.FIELD_CONE_RADIUS_DEG) == (150, 2, 31.0)
    assert (sv.CONSENSUS_MIN_MATCHES, sv.CONSENSUS_MAX_CHANCE, sv.TILE_MAX_VERIFIED) == (4, 1e-3, 40)
    assert (sv.MIN_HELD_OUT_INLIERS, sv.MAX_HELD_OUT_MEDIAN_RESIDUAL_PX, sv.MAX_HELD_OUT_P95_RESIDUAL_PX) == (6, 3.0, 6.0)
    # The field's margin past the frame corners, as these tests state it
    assert sv.FIELD_MARGIN_DEG == FIELD_MARGIN_DEG
    # solver-10k v4's refusals: three inlier gates, 1 % of the 0.24 bound, a 5 % miss rate weighed at the consensus
    # chance over a 3 x 3 grid, a magnitude above the frame's depth, 1 arcsec for one star
    assert (sv.AMBIGUITY_GATE_MULTIPLE, sv.RADIAL_COEFFICIENT_BOUND, sv.RADIAL_BOUND_FRACTION) == (3.0, 0.24, 0.01)
    assert (sv.COVERAGE_MISS_RATE, sv.COVERAGE_GRID, sv.COVERAGE_V_MARGIN, sv.SAME_STAR_ARCSEC) == (0.05, 3, 1.0, 1.0)


def test_the_v_limit_rule_on_the_real_tile_pool() -> None:
    when, lat, lon = CYGNUS_UP["obs_utc_iso"], 30.0, -40.0
    region = 2.0 * math.pi * (1.0 - math.sin(math.radians(10.0)))
    count_at = lambda v: len(tile_stars_above_horizon(when, lat, lon, v, 10.0))  # noqa: E731
    # A sparse frame keeps the floor; the 150 brightest of a dense frame need a deeper pool
    assert sv._tile_v_limit(20, count_at, region, 4.5) == 4.5
    deep = sv._tile_v_limit(150, count_at, region, 4.5)
    assert 4.5 < deep < 6.5
    share = 2.0 * math.pi * (1.0 - math.cos(math.radians(31.0))) / region
    assert count_at(deep) * share >= 150 > count_at(round(deep - 0.1, 6)) * share


def test_the_bucketed_triangles_keep_the_side_bounds_and_the_vertex_order() -> None:
    pool = tile_stars_above_horizon(CRUX_UP["obs_utc_iso"], -30.0, -40.0, 4.5, 10.0)
    vec = np.array([[math.cos(math.radians(s.dec_deg)) * math.cos(math.radians(s.ra_deg)),
                     math.cos(math.radians(s.dec_deg)) * math.sin(math.radians(s.ra_deg)),
                     math.sin(math.radians(s.dec_deg))] for s in pool])
    index = sv._tile_triangle_index(vec)
    triples = {tuple(sorted(t)) for t in zip(index.v0.tolist(), index.v1.tolist(), index.v2.tolist())}
    assert len(triples) == index.v0.size > 500
    centres = sv._cell_centres()
    assert centres.shape == (192, 3)
    assert np.allclose(np.linalg.norm(centres, axis=1), 1.0, atol=1e-12)
    cos_cone = math.cos(math.radians(sv.FIELD_CONE_RADIUS_DEG))
    # The pool is in V order, so the first members within a cell's cone are its brightest
    brightest_by_cell = [set(np.flatnonzero(vec @ centre >= cos_cone)[: sv.TILE_TRIANGLE_STARS_PER_CELL].tolist()) for centre in centres]

    def side(i: int, j: int) -> float:
        return math.degrees(math.acos(max(-1.0, min(1.0, float(vec[i] @ vec[j])))))

    for h in range(index.v0.size):
        a, b, c = int(index.v0[h]), int(index.v1[h]), int(index.v2[h])
        longest, second, shortest = side(a, b), side(a, c), side(b, c)
        assert sv.TRIANGLE_MIN_SIDE_DEG <= shortest <= second <= longest <= sv.TRIANGLE_MAX_SIDE_DEG
        assert math.isclose(index.ratios[h, 0], second / longest, abs_tol=1e-9)
        assert math.isclose(index.ratios[h, 1], shortest / longest, abs_tol=1e-9)
        assert any({a, b, c} <= cell for cell in brightest_by_cell), h
    # And every such triangle of every cell is there
    expected = set()
    for cell in brightest_by_cell:
        for t in itertools.combinations(sorted(cell), 3):
            sides = sorted(side(i, j) for i, j in itertools.combinations(t, 2))
            if sides[0] >= sv.TRIANGLE_MIN_SIDE_DEG and sides[2] <= sv.TRIANGLE_MAX_SIDE_DEG:
                expected.add(t)
    assert triples == expected


def test_the_consensus_rule_weighs_matches_against_chance() -> None:
    passes = sv._consensus_passes
    # 4 of 4 in frame matched where a chance match is 1 in 20: about 6e-6 by chance, passes
    assert passes(np.array([4]), np.array([4]), 0.05).tolist() == [True]
    # 3 matches never pass, however unlikely
    assert passes(np.array([3]), np.array([3]), 0.001).tolist() == [False]
    # 6 of 20 where a chance match is 1 in 5: 0.196 by chance, fails
    assert passes(np.array([6]), np.array([20]), 0.2).tolist() == [False]
    # 11 of 20 at 1 in 5: 5.6e-4 by chance, passes; 10 of 20: 2.6e-3, fails (exact binomial tails)
    assert passes(np.array([11, 10]), np.array([20, 20]), 0.2).tolist() == [True, False]
    assert sv._chance_of_at_least(np.array([11, 0, 21]), np.array([20, 20, 20]), 0.2) == pytest.approx([5.634e-4, 1.0, 0.0], rel=1e-3)


def _verification(residuals: list[float], train: int = 4) -> HeldOutVerification:
    """Pairs of made-up stars spread over the 960 x 540 frame with detections where its WCS puts them, moved by each
    residual (the first `train` by 0.5 px, as training pairs)."""
    wcs, stars = _orion_wcs_and_stars()
    placed = [(s, *wcs.world_to_pixel(s.ra_deg, s.dec_deg)) for s in stars]
    in_frame = [(s, x, y) for s, x, y in placed if not math.isnan(x) and 40 <= x < 920 and 40 <= y < 500]
    rng = np.random.default_rng(6)
    chosen = [in_frame[k] for k in rng.choice(len(in_frame), size=train + len(residuals), replace=False)]
    assoc = []
    for k, ((star, x, y), r) in enumerate(zip(chosen, [0.5] * train + residuals)):
        det = SourceDetection(x_px=x + r, y_px=y, flux=1.0, snr=10.0, fwhm_px=2.0, centroid_uncertainty_px=0.1)
        assoc.append((det, star, r, k < train))
    return HeldOutVerification(associations=tuple(assoc), inlier_gate_px=3.0)


def _gate_inputs(n_inside: int, n_detections: int, n_impulses: int = 0, n_outside: int = 0):
    """The 960 x 540 WCS; `n_inside` made-up catalog positions inside its frame, `n_outside` beside it (in front of
    the camera, so they project, but off the frame) and one behind it; and the detections (one PSF-shaped and one
    hot-pixel impulse, repeated: the gate only counts them)."""
    wcs, _ = _orion_wcs_and_stars()
    rng = np.random.default_rng(4)
    pixels = np.vstack([rng.uniform([0.0, 0.0], [959.0, 539.0], (n_inside, 2)), rng.uniform([1000.0, 0.0], [1900.0, 539.0], (n_outside, 2))])
    radec = np.array([wcs.pixel_to_world(float(x), float(y)) for x, y in pixels]).reshape(-1, 2)
    radec = np.vstack([radec, [[(wcs.crval_ra_deg + 180.0) % 360.0, -wcs.crval_dec_deg]]])
    star_like = SourceDetection(x_px=1.0, y_px=1.0, flux=1.0, snr=10.0, fwhm_px=2.0, centroid_uncertainty_px=0.1)
    impulse = SourceDetection(x_px=1.0, y_px=1.0, flux=1.0, snr=10.0, fwhm_px=0.6, centroid_uncertainty_px=0.1, peak_to_total_ratio=0.9)
    return wcs, [star_like] * n_detections + [impulse] * n_impulses, radec[:, 0], radec[:, 1]


def test_the_tile_pool_gate_needs_the_held_out_gate_and_the_coincidence_check() -> None:
    gate = sv._tile_pool_gate
    good = _verification([0.4, 0.6, 0.5, 0.7, 0.3, 0.5, 0.6, 0.4, 0.5, 0.6])
    assert good.passed
    # A sparse frame: 40 detections, 20 catalog stars in frame, so 10 held-out inliers are far beyond chance
    held, passed, log_odds = gate(good, *_gate_inputs(20, 40), 960, 540)
    assert (held, passed) == (True, True) and log_odds >= 18.0
    # Hot-pixel impulses do not count towards the density the residuals are weighed against
    assert gate(good, *_gate_inputs(20, 40, n_impulses=200_000), 960, 540)[:2] == (True, True)
    # The same residuals among 200,000 detections and 900 projected stars could be chance: no claim
    held, passed, _ = gate(good, *_gate_inputs(900, 200_000), 960, 540)
    assert (held, passed) == (True, False)
    # 600 catalog stars inside the frame make those 10 inliers too few; 580 of them beside the frame do not count
    assert gate(good, *_gate_inputs(600, 40), 960, 540)[:2] == (True, False)
    assert gate(good, *_gate_inputs(20, 40, n_outside=580), 960, 540)[:2] == (True, True)
    # Five held-out inliers fail the held-out gate whatever the density
    held, passed, _ = gate(_verification([0.4, 0.6, 0.5, 0.7, 0.3]), *_gate_inputs(20, 40), 960, 540)
    assert held is False and passed is False


def test_the_tile_pool_gate_needs_the_fit_and_its_held_out_refit_to_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _verification([0.4, 0.6, 0.5, 0.7, 0.3, 0.5, 0.6, 0.4, 0.5, 0.6])
    wcs = _orion_wcs_and_stars()[0]
    # Detections 0.3 to 0.7 px beside where the WCS puts their stars refit to about the same WCS: under a pixel apart
    assert sv._dual_path_disagreement_px(wcs, good, 960, 540) < 1.0
    assert sv._dual_path_disagreement_px(wcs, _verification([0.4, 0.6, 0.5, 0.7, 0.3]), 960, 540) == 99.0
    # The gate takes the disagreement: up to 2.5 px passes, more does not (MAX_DUAL_PATH_DISAGREEMENT_PX)
    for disagreement, expected in ((2.4, True), (2.6, False)):
        monkeypatch.setattr(sv, "_dual_path_disagreement_px", lambda *args, d=disagreement: d)
        assert sv._tile_pool_gate(good, *_gate_inputs(20, 40), 960, 540)[1] is expected


@lru_cache(maxsize=1)
def _cygnus_detections() -> tuple[SourceDetection, ...]:
    return tuple(detect_point_sources(_cygnus()[0], snr_threshold=4.0))


def test_the_tile_pool_claims_nothing_when_the_coincidence_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, _ = _cygnus()
    monkeypatch.setattr(sv, "evaluate_coincidence_gate", lambda **kwargs: (False, 0.0))
    sol = solve_and_verify_field(list(_cygnus_detections()), truth.width, truth.height, **CYGNUS_UP, **_sources(frame, "cygnus-dense"))
    _assert_no_claim(sol)


def test_no_detection_a_tile_pool_fit_trained_on_is_held_out(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, _ = _crux()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    fitted: list[set[int]] = []
    held_out_runs: list[set[int]] = []
    refine, verify = sv._refine_wcs_least_squares, sv.verify_held_out

    def spy_refine(initial, pairs, fit_distortion=True):
        fitted.append({id(d) for d, _ in pairs})
        return refine(initial, pairs, fit_distortion)

    def spy_verify(wcs, dets, catalog, training):
        held_out_runs.append({id(d) for d in training})
        return verify(wcs, dets, catalog, training)

    monkeypatch.setattr(sv, "_refine_wcs_least_squares", spy_refine)
    monkeypatch.setattr(sv, "verify_held_out", spy_verify)
    sol = solve_and_verify_field(detections, truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux"))
    assert sol.status == "calibrated" and "tile stars" in sol.solver.catalog_release
    # The claim's fits are the last three: the TAN fit, the fit with radial terms, then the dual-path refit. The last
    # verification is the claim's, and it holds out nothing either of the first two trained on
    assert fitted[-3] and fitted[-2] and fitted[-3] | fitted[-2] <= held_out_runs[-1]
    assert not (fitted[-1] & held_out_runs[-1]), "the dual-path refit trains on held-out inliers only"


def test_the_pinned_search_scores_every_psf_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    detections = list(_cygnus_detections())
    psf = [d for d in detections if not d.is_hot_pixel_impulse]
    assert len(psf) > sv.SCORING_DETECTION_COUNT
    seen: list[int] = []
    search = sv._pinned_search

    def spy(det_triangles, cat_pool, catalog, score_dets, *args):
        seen.append(len(score_dets))
        return search(det_triangles, cat_pool, catalog, score_dets, *args)

    monkeypatch.setattr(sv, "_pinned_search", spy)
    solve_and_verify_field(detections, 1600, 1200, **CYGNUS_UP, **_sources(_cygnus()[0], "cygnus-dense"))
    assert seen == [len(psf)]


def test_tile_training_pairs_take_at_most_half_of_the_tightest_and_at_most_sixteen() -> None:
    wcs, stars = _orion_wcs_and_stars()
    placed = [(s, *wcs.world_to_pixel(s.ra_deg, s.dec_deg)) for s in stars]
    pairs = [
        (SourceDetection(x_px=x, y_px=y, flux=1.0, snr=10.0, fwhm_px=2.0, centroid_uncertainty_px=0.1), s, 0.1 * k)
        for k, (s, x, y) in enumerate(p for p in placed if not math.isnan(p[1]) and 0 <= p[1] < 960 and 0 <= p[2] < 540)
    ]
    # The tightest 85 % (at least 6), then max(3, min(16, half of those, all of those but 6))
    for n, expected in ((4, 3), (10, 3), (14, 5), (20, 8), (60, 16)):
        chosen = sv._tile_training_pairs(pairs[:n], 960, 540)
        assert len(chosen) == expected, n
        assert len({id(d) for d, _, _ in chosen}) == expected


def test_a_given_catalog_is_searched_alone() -> None:
    frame, truth, _ = _crux()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    events: list[int] = []
    sol = solve_and_verify_field(
        detections, truth.width, truth.height, **CRUX_UP, catalog_stars=get_all_sky_catalog_stars(),
        on_candidate=lambda done, total: events.append(done), **_sources(frame, "crux"),
    )
    assert events.count(0) == 1
    assert sol.solver.catalog_release.startswith("71 pinned bright stars")


def test_the_pool_is_the_sky_above_the_cut_with_a_time_and_place_and_the_whole_sky_without() -> None:
    when, lat, lon = CRUX_UP["obs_utc_iso"], CRUX_UP["observer_lat"], CRUX_UP["observer_lon"]
    pool, v_limit, above = sv._tile_pool_for(10, when, lat, lon)
    assert above and v_limit == 4.5
    assert [s.id for s in pool] == [s.id for s in tile_stars_above_horizon(when, lat, lon, 4.5, 10.0)]
    pool, v_limit, above = sv._tile_pool_for(10, None, None, None)
    assert not above and v_limit == 3.5 and [s.id for s in pool] == [s.id for s in tile_stars_all_sky(3.5)]
    # A time without a place: the whole sky, moved to the time's epoch
    pool, v_limit, above = sv._tile_pool_for(10, when, None, None)
    assert not above and v_limit == 3.5 and {round(s.epoch_year, 3) for s in pool} == {round(_epoch_jyear(when), 3)}
    # A place without a time: the whole sky at the tiles' epoch
    pool, v_limit, above = sv._tile_pool_for(10, None, lat, lon)
    assert not above and {s.epoch_year for s in pool} == {2016.0}
    # 150 detections need a deeper pool, by the rule
    pool, v_limit, above = sv._tile_pool_for(150, when, lat, lon)
    region = 2.0 * math.pi * (1.0 - math.sin(math.radians(10.0)))
    assert v_limit == sv._tile_v_limit(150, lambda v: len(tile_stars_above_horizon(when, lat, lon, v, 10.0)), region, 4.5) > 4.5
    assert len(pool) == len(tile_stars_above_horizon(when, lat, lon, v_limit, 10.0))


def test_the_solver_identity_names_the_pool() -> None:
    pinned = triangle_solver_identity(get_catalog_stars())
    assert pinned.catalog_release == "18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)"
    pool = tile_stars_above_horizon(CRUX_UP["obs_utc_iso"], -30.0, -40.0, 4.5, 10.0)
    tile = triangle_solver_identity(pool, sv._pool_description(above_horizon=True, v_limit=4.5))
    assert tile.name == pinned.name
    assert tile.catalog_release == (
        f"{len(pool)} tile stars above the horizon, apparent altitude at least 10 deg and V at most 4.5 "
        "(Gaia DR3 and Hipparcos 2007 rows of the G 12 tiles)"
    )
    everywhere = tile_stars_all_sky(3.5)
    blind = triangle_solver_identity(everywhere, sv._pool_description(above_horizon=False, v_limit=3.5))
    assert blind.catalog_release == (
        f"{len(everywhere)} tile stars across the whole sky, V at most 3.5 (Gaia DR3 and Hipparcos 2007 rows of the G 12 tiles)"
    )


# =============================================================================================
# solver-10k v4's refusals: the radial bound, ambiguous pairs, coverage, the dual path to the corners
# =============================================================================================


@settings(max_examples=120, deadline=None)
@given(k1=st.floats(min_value=-0.24, max_value=0.24), k2=st.floats(min_value=-0.24, max_value=0.24))
# 99 % of the 0.24 bound is 0.2376: refused on either coefficient and either sign; 0.23759 is kept
@example(k1=0.2376, k2=0.0)
@example(k1=0.23759, k2=0.0)
@example(k1=0.0, k2=-0.2376)
@example(k1=0.0, k2=-0.23759)
@example(k1=-0.23759, k2=0.23759)
@example(k1=-0.24, k2=0.0)
def test_pbt_the_bound_rule_refuses_exactly_the_fits_within_one_percent_of_the_bound(k1: float, k2: float) -> None:
    wcs = replace(_orion_wcs_and_stars()[0], k1=k1, k2=k2)
    assert sv._radial_term_at_bound(wcs) is (abs(k1) >= 0.2376 or abs(k2) >= 0.2376)


def test_a_fit_its_pairs_push_past_the_bound_stops_at_the_bound_and_the_rule_names_it() -> None:
    # Pairs drawn through k2 = 0.6, past the bound (WCSModel itself takes at most 0.25, so the radial factor is applied
    # here by hand about the reference pixel): least squares stops at RADIAL_COEFFICIENT_BOUND
    wcs, stars = _orion_wcs_and_stars()
    tan = replace(wcs, k1=0.0, k2=0.0)
    pairs = []
    for star in stars[::3]:
        x0, y0 = tan.world_to_pixel(star.ra_deg, star.dec_deg)
        if math.isnan(x0):
            continue
        r = math.radians(math.hypot(x0 - tan.crpix_x, y0 - tan.crpix_y) * tan.scale_deg_per_px)
        factor = 1.0 + 0.6 * r**4
        x, y = tan.crpix_x + (x0 - tan.crpix_x) * factor, tan.crpix_y + (y0 - tan.crpix_y) * factor
        if 0 <= x < 960 and 0 <= y < 540:
            pairs.append((SourceDetection(x_px=x, y_px=y, flux=1.0, snr=10.0, fwhm_px=2.0, centroid_uncertainty_px=0.1), star))
    refined = sv._refine_wcs_least_squares(tan, _spread_pairs(pairs, 16))
    assert max(abs(refined.k1), abs(refined.k2)) <= sv.RADIAL_COEFFICIENT_BOUND + 1e-12
    assert sv._radial_term_at_bound(refined), (refined.k1, refined.k2)


def _spread_pairs(pairs: list, count: int) -> list:
    """`count` of `pairs` spread from the 960 x 540 frame's edge in, as the tile pass picks training pairs."""
    spread = sv._spread_by_radius([(d, s, 0.0) for d, s in pairs], 960, 540, count)
    return [(d, s) for d, s, _ in spread]


def _ambiguity_oracle(wcs: WCSModel, verification: HeldOutVerification, field: list[tuple[float, float]]) -> frozenset[int]:
    """The detections with a direction of `field` more than 1 arcsec from their own star that `wcs.world_to_pixel`
    puts within three inlier gates of them, one direction at a time."""
    radius = 3.0 * verification.inlier_gate_px
    out = set()
    for det, star, _, _ in verification.associations:
        for ra, dec in field:
            if angular_separation_deg(ra, dec, star.ra_deg, star.dec_deg) * 3600.0 <= 1.0:
                continue
            x, y = wcs.world_to_pixel(ra, dec)
            if not math.isnan(x) and math.hypot(x - det.x_px, y - det.y_px) <= radius:
                out.add(id(det))
                break
    return frozenset(out)


@settings(max_examples=40, deadline=None)
@given(
    picks=st.lists(st.tuples(st.integers(0, 10_000), st.floats(-2.0, 2.0), st.floats(-2.0, 2.0)), min_size=1, max_size=20),
    extra=st.lists(st.tuples(st.integers(0, 10_000), st.floats(-12.0, 12.0), st.floats(-12.0, 12.0)), max_size=20),
)
# One detection on its star (gate 1.8 px, so three gates are 5.4 px): a second direction 5.3 px off makes it ambiguous,
# one 5.5 px off does not
@example(picks=[(0, 0.0, 0.0)], extra=[(0, 5.3, 0.0)])
@example(picks=[(0, 0.0, 0.0)], extra=[(0, 5.5, 0.0)])
@example(picks=[(0, 0.0, 0.0), (10, 0.5, 0.5), (11, -1.0, 0.0)], extra=[])
def test_pbt_a_pair_is_ambiguous_exactly_when_another_star_projects_within_three_gates(picks, extra) -> None:
    wcs, stars = _orion_wcs_and_stars()
    placed = [(s, *wcs.world_to_pixel(s.ra_deg, s.dec_deg)) for s in stars]
    in_frame = [(s, x, y) for s, x, y in placed if not math.isnan(x) and 20 <= x < 940 and 20 <= y < 520]
    detections = []
    for k, (pick, dx, dy) in enumerate(picks):
        _, x, y = in_frame[pick % len(in_frame)]
        detections.append(SourceDetection(x_px=x + dx, y_px=y + dy, flux=10.0 + k, snr=20.0, fwhm_px=2.0, centroid_uncertainty_px=0.1))
    verification = verify_held_out(wcs, detections, [s for s, _, _ in in_frame], [])
    field = [(s.ra_deg, s.dec_deg) for s, _, _ in in_frame]
    for pick, dx, dy in extra:
        det = detections[pick % len(detections)]
        field.append(wcs.pixel_to_world(det.x_px + dx, det.y_px + dy))
    got = sv._with_ambiguous_pairs(wcs, verification, np.array([r for r, _ in field]), np.array([d for _, d in field]))
    assert got.ambiguous == _ambiguity_oracle(wcs, verification, field)
    assert got.associations == verification.associations and got.inlier_gate_px == verification.inlier_gate_px
    assert got.ambiguity_radius_px == pytest.approx(3.0 * verification.inlier_gate_px)


def test_a_second_row_at_the_same_place_is_the_same_star_but_one_2_arcsec_off_is_another() -> None:
    wcs, stars = _orion_wcs_and_stars()
    # The made-up catalog's first twin in the frame: two rows at one place
    star, twin = next(
        (s, t) for s, t in zip(stars, stars[1:])
        if t.id.startswith("made-up-twin") and 20 <= wcs.world_to_pixel(s.ra_deg, s.dec_deg)[0] < 940
        and 20 <= wcs.world_to_pixel(s.ra_deg, s.dec_deg)[1] < 520
    )
    verification = verify_held_out(wcs, [_detection_at(*wcs.world_to_pixel(star.ra_deg, star.dec_deg))], [star, twin], [])
    assert len(verification.associations) == 1
    same = sv._with_ambiguous_pairs(wcs, verification, np.array([star.ra_deg, twin.ra_deg]), np.array([star.dec_deg, twin.dec_deg]))
    assert same.ambiguous == frozenset()
    # 2 arcsec north of it is another star, though a hundredth of a pixel away
    other = sv._with_ambiguous_pairs(wcs, verification, np.array([star.ra_deg, star.ra_deg]), np.array([star.dec_deg, star.dec_deg + 2.0 / 3600.0]))
    assert other.ambiguous == frozenset({id(verification.associations[0][0])})


def test_an_ambiguous_pair_is_neither_an_inlier_nor_a_catalog_match_but_keeps_its_residual() -> None:
    good = _verification([0.4, 0.6, 0.5, 0.7, 0.3, 0.5, 0.6, 0.4, 0.5, 0.6])
    held = [det for det, _, _, is_train in good.associations if not is_train]
    marked = replace(good, ambiguous=frozenset(id(d) for d in held[:5]), ambiguity_radius_px=9.0)
    assert (good.held_out_inliers, marked.held_out_inliers) == (10, 5)
    assert marked.held_out_residuals_px == good.held_out_residuals_px
    assert (marked.median_residual_px, marked.p95_residual_px) == (good.median_residual_px, good.p95_residual_px)
    assert set(marked.inliers) == set(good.inliers) - {id(d) for d in held[:5]}
    # Five unambiguous held-out inliers are too few for the gate
    assert good.passed and not marked.passed
    # In a solution, each ambiguous detection is an observed finding that says why
    wcs, stars = _orion_wcs_and_stars()
    detections = [det for det, _, _, _ in good.associations]
    sol = sv._solution_from_fit(
        detections, wcs, marked, True, len(stars), triangle_solver_identity(stars), solution_id="sol-ambiguous",
        frame_time_sec=0.0, obs_utc_iso=None, observer_lat=None, observer_lon=None, **_sources(np.zeros(1), "ambiguous"),
    )
    by_detection = {id(f.source_detection): f for f in sol.findings}
    for det in held[:5]:
        finding = by_detection[id(det)]
        assert finding.evidence_class == "observed" and finding.catalog_star is None
        assert "another catalog star sits within 9.00 px" in finding.limitations[0]
    assert sum(f.evidence_class == "catalog_matched" for f in sol.findings) == len(detections) - 5
    assert sol.quality.held_out_inliers == 5 and sol.quality.matched_inlier_count == len(detections) - 5
    assert any("5 paired but with another catalog star within 9.00 px, so ambiguous" in w for w in sol.warnings)


def _binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X binomial over n at p, summed exactly."""
    if k <= 0:
        return 1.0
    return float(sum(math.comb(n, j) * p**j * (1.0 - p) ** (n - j) for j in range(k, n + 1)))


@settings(max_examples=150, deadline=None)
@given(cells=st.lists(st.tuples(st.integers(0, 40), st.integers(0, 40)), min_size=9, max_size=9))
# One cell missing 5 of 11 (chance 1.1e-4): refused, though the frame's 5 of 171 are not
@example(cells=[(11, 5)] + [(20, 0)] * 8)
# 4 of 14 (chance 4.2e-3): kept
@example(cells=[(14, 4)] + [(20, 0)] * 8)
# 4 of 30 in every cell (chance 0.061 each): kept cell by cell, but 36 of 270 over the frame are refused
@example(cells=[(30, 4)] * 9)
# All three missed (1.25e-4): refused; both of two (2.5e-3): kept
@example(cells=[(3, 3)] + [(0, 0)] * 8)
@example(cells=[(2, 2)] + [(0, 0)] * 8)
@example(cells=[(0, 0)] * 9)
def test_pbt_coverage_refuses_exactly_when_the_misses_beat_a_five_percent_rate_by_chance(cells) -> None:
    expected = np.array([n for n, _ in cells], dtype=np.int64).reshape(3, 3)
    missed = np.array([min(m, n) for n, m in cells], dtype=np.int64).reshape(3, 3)
    tails = [_binomial_tail(int(missed.sum()), int(expected.sum()), 0.05)]
    tails += [_binomial_tail(int(m), int(n), 0.05) for n, m in zip(expected.ravel(), missed.ravel())]
    assert sv._coverage_refuses(expected, missed) is (min(tails) <= 1e-3)


def _made_up_star(wcs: WCSModel, x: float, y: float, tag: str, mag: float) -> CatalogStar:
    ra, dec = wcs.pixel_to_world(x, y)
    return replace(get_catalog_stars()[0], id=f"made-up-{tag}", ra_deg=ra, dec_deg=dec, mag=mag)


def _detection_at(x: float, y: float) -> SourceDetection:
    return SourceDetection(x_px=x, y_px=y, flux=10.0, snr=20.0, fwhm_px=2.0, centroid_uncertainty_px=0.1)


def test_coverage_counts_a_miss_only_on_a_clear_visible_held_out_star_inside_the_frame() -> None:
    # The 960 x 540 WCS, where the tile match radius (the visibility radius) is 18 px
    wcs = _orion_wcs_and_stars()[0]
    places = {
        "paired": (100.0, 100.0), "missed": (500.0, 270.0), "missed-corner": (900.0, 500.0), "edge": (4.0, 270.0),
        "trained": (800.0, 400.0), "blended": (300.0, 400.0), "unseen": (700.0, 100.0),
    }
    stars = {name: _made_up_star(wcs, x, y, name, 3.0) for name, (x, y) in places.items()}
    # Detections on the paired, trained, blended and edge stars, 12 px from the two missed ones (visible, but past the
    # 8 px association radius), none within 18 px of the unseen one; and ten more along the bottom left, so that the
    # PSF detections outnumber the stars and every V counts
    dets = {name: _detection_at(*places[name]) for name in ("paired", "trained", "blended", "edge")}
    dets["near-missed"] = _detection_at(512.0, 270.0)
    dets["near-corner"] = _detection_at(900.0, 488.0)
    extra = [_detection_at(40.0 + 40.0 * k, 525.0) for k in range(10)]
    detections = [*dets.values(), *extra]
    verification = verify_held_out(wcs, detections, list(stars.values()), [dets["trained"]])
    # A second star 2 px from the blended one (in the field, not in the pool)
    companion = wcs.pixel_to_world(302.0, 400.0)
    field = [(s.ra_deg, s.dec_deg) for s in stars.values()] + [companion]
    expected, missed = sv._coverage_cells(
        wcs, sv._with_ambiguous_pairs(wcs, verification, np.array([r for r, _ in field]), np.array([d for _, d in field])),
        list(stars.values()), detections, 960, 540, np.array([r for r, _ in field]), np.array([d for _, d in field]),
    )
    # Rows from the top: the paired star in the top left cell, the missed one in the centre, the other in the corner
    assert expected.tolist() == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert missed.tolist() == [[0, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_coverage_counts_only_stars_a_magnitude_above_the_frame_s_depth() -> None:
    wcs = _orion_wcs_and_stars()[0]
    stars = [
        _made_up_star(wcs, 100.0, 100.0, "v3", 3.0), _made_up_star(wcs, 130.0, 100.0, "v4", 4.0),
        _made_up_star(wcs, 400.0, 300.0, "v5", 5.0), _made_up_star(wcs, 430.0, 300.0, "v6", 6.0),
    ]
    # Two PSF detections, each 17 px from two stars (visible, unpaired): the frame's depth is the second brightest
    # star's V, 4.0, so only the V 3.0 star, a magnitude above it, is expected
    detections = [_detection_at(115.0, 108.0), _detection_at(415.0, 308.0)]
    verification = verify_held_out(wcs, detections, stars, [])
    assert verification.associations == ()
    ra, dec = np.array([s.ra_deg for s in stars]), np.array([s.dec_deg for s in stars])
    expected, missed = sv._coverage_cells(wcs, verification, stars, detections, 960, 540, ra, dec)
    assert expected.tolist() == [[1, 0, 0], [0, 0, 0], [0, 0, 0]] and missed.tolist() == expected.tolist()


def test_the_dual_path_grid_reaches_the_frame_s_edges_and_corners(monkeypatch: pytest.MonkeyPatch) -> None:
    wcs, _ = _orion_wcs_and_stars()
    good = _verification([0.4, 0.6, 0.5, 0.7, 0.3, 0.5, 0.6, 0.4, 0.5, 0.6])
    # A held-out refit that differs from the fit by its k2 term alone: least at the centre, most at the corners
    refit = replace(wcs, k2=wcs.k2 + 0.05)
    trained_on: list[set[int]] = []

    def fake_refit(initial, pairs, fit_distortion=True):
        trained_on.append({id(d) for d, _ in pairs})
        return refit

    monkeypatch.setattr(sv, "_refine_wcs_least_squares", fake_refit)

    def apart(xs, ys) -> np.ndarray:
        out = []
        for y in ys:
            for x in xs:
                ra, dec = wcs.pixel_to_world(float(x), float(y))
                hx, hy = refit.world_to_pixel(ra, dec)
                out.append(math.hypot(hx - x, hy - y))
        return np.array(out).reshape(len(ys), len(xs))

    grid = apart(np.linspace(0.0, 960.0, 5), np.linspace(0.0, 540.0, 5))
    got = sv._dual_path_disagreement_px(wcs, good, 960, 540)
    assert got == pytest.approx(float(np.median(grid)), abs=1e-6)
    # The phone solver's grid, 15 to 85 % of the width by 15 to 75 % of the height, sees less of the difference
    assert float(np.median(apart(np.linspace(144.0, 816.0, 5), np.linspace(81.0, 405.0, 5)))) < got
    # The refit never trains on an ambiguous held-out inlier
    held = [det for det, _, _, is_train in good.associations if not is_train]
    marked = replace(good, ambiguous=frozenset(id(d) for d in held[:3]), ambiguity_radius_px=9.0)
    sv._dual_path_disagreement_px(wcs, marked, 960, 540)
    assert trained_on[-1] and not (trained_on[-1] & marked.ambiguous)


@pytest.mark.parametrize("refusal", ["_radial_term_at_bound", "_coverage_refuses", "_with_ambiguous_pairs"])
def test_the_tile_pass_claims_nothing_when_a_v4_refusal_refuses_every_fit(refusal: str, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, _ = _crux()
    detections = detect_point_sources(frame, snr_threshold=4.0)
    if refusal == "_with_ambiguous_pairs":
        # Every pair ambiguous: no held-out inlier is left
        monkeypatch.setattr(sv, refusal, lambda wcs, ver, ra, dec: replace(ver, ambiguous=frozenset(id(d) for d, _, _, _ in ver.associations), ambiguity_radius_px=9.0))
    else:
        monkeypatch.setattr(sv, refusal, lambda *args: True)
    _assert_no_claim(solve_and_verify_field(detections, truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux")))


def test_a_tile_pass_claim_passed_each_v4_refusal_on_its_own_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, truth, _ = _crux()
    bound, ambiguity, cells, verify = sv._radial_term_at_bound, sv._with_ambiguous_pairs, sv._coverage_cells, sv.verify_held_out
    seen: dict[str, list] = {"bound": [], "ambiguity": [], "coverage": [], "verify": []}

    def spy_bound(wcs):
        seen["bound"].append(wcs)
        return bound(wcs)

    def spy_ambiguity(wcs, verification, ra, dec):
        out = ambiguity(wcs, verification, ra, dec)
        seen["ambiguity"].append((wcs, verification, out, len(ra)))
        return out

    def spy_cells(wcs, verification, *args):
        out = cells(wcs, verification, *args)
        seen["coverage"].append((wcs, verification, out))
        return out

    def spy_verify(wcs, dets, catalog, training):
        out = verify(wcs, dets, catalog, training)
        seen["verify"].append((wcs, len(catalog), out))
        return out

    monkeypatch.setattr(sv, "_radial_term_at_bound", spy_bound)
    monkeypatch.setattr(sv, "_with_ambiguous_pairs", spy_ambiguity)
    monkeypatch.setattr(sv, "_coverage_cells", spy_cells)
    monkeypatch.setattr(sv, "verify_held_out", spy_verify)
    sol = solve_and_verify_field(detect_point_sources(frame, snr_threshold=4.0), truth.width, truth.height, **CRUX_UP, **_sources(frame, "crux"))
    assert sol.status == "calibrated" and "tile stars" in sol.solver.catalog_release
    # The claimed fit is the last the bound rule saw, and not at the bound
    assert seen["bound"][-1] == sol.wcs and not bound(sol.wcs)
    # Its verification was marked for ambiguity over every tile row of the field (more than the pool's field stars it
    # paired), and no catalog match is an ambiguous detection; each ambiguous one is an observed finding
    wcs, raw, marked, rows = seen["ambiguity"][-1]
    assert wcs == sol.wcs and raw is seen["verify"][-1][2] and rows > seen["verify"][-1][1]
    assert marked.ambiguous, "the Crux frame has neighbours within three gates"
    matched = {id(f.source_detection) for f in sol.findings if f.evidence_class == "catalog_matched"}
    assert matched and not (matched & marked.ambiguous)
    flagged = [f for f in sol.findings if f.evidence_class == "observed" and "which star it shows is open" in f.limitations[0]]
    assert len(flagged) == len(marked.ambiguous)
    assert sol.quality.held_out_inliers == marked.held_out_inliers
    # And its coverage, on the marked verification, has expected stars and does not refuse
    wcs, verification, (expected, missed) = seen["coverage"][-1]
    assert wcs == sol.wcs and verification is marked and expected.sum() > 0 and not sv._coverage_refuses(expected, missed)
