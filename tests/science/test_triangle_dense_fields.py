"""The triangle plate solver's search made fast on dense star fields (solver-10k-a).

Covers:
- The fast search gives the solution its one-by-one rules give: with the ratio lookup, the consensus pairing and the
  held-out pairing replaced by the loops over every catalog triangle, star and detection that the search ran before
  solver-10k-a (written out here), the whole solution (status, solver identity, WCS, quality, every finding,
  warnings) is the same on a ci frame of the phone photo benchmark, a pinned-star field, a dense wide distorted field
  and a dense Milky Way frame (thousands of rendered tile stars, none of the 18 pinned stars in it), each with the 18
  pinned stars and with the 71-star list.
- Random dots (2,500 sources, no sky), with a time and place and blind, finish within a stated bound and make no
  claim, naming the 18 pinned stars.
- PBT: the ratio lookup returns exactly the catalog triangles within the 0.045 tolerance on both ratios.
- PBT: the vectorized projection equals `WCSModel.world_to_pixel` star by star, NaN where it is NaN.
- PBT: a candidate's consensus pairs are the one-by-one rule's (nearest unused star closer than the radius).
- PBT: `verify_held_out` pairs every detection as the one-by-one rule does (nearest unused star within 8 px).
- A WCS subclass that projects otherwise is paired through its own `world_to_pixel`.
- The consensus radius is 18 px: a field whose solution differs at 17, 18 and 19 px gets the 18 px one.
- Pairing stays exact when the vectorized projection is off by up to 0.0007 px on each axis (another platform's
  trigonometry), in both pairings (PBT, with detections a hair inside 8 px and 18 px) and in whole solutions.
- Non-finite values give the solution of the one-by-one rules, never an exception the loops did not raise: a detection
  with a NaN or infinite position (through `solve_and_verify_field` and through POST /api/science/solve), NaN and
  infinite x, y, flux and snr in random detections (PBT, whole solutions), NaN and infinite ratios (PBT), and stars
  and projections with NaN or infinite values in both pairings (PBT).

Every frame here is simulated: rendered from catalog positions by `render_synthetic_starfield` or by the phone photo
benchmark's renderer, never a recording. The moments and places are synthetic: the fixtures' open North Atlantic
place (30 N, 40 W), and the benchmark frame's own.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from functools import lru_cache
import hashlib
import json
import math
import time
from typing import Any, Callable

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos.stars import TileCatalog
from services.science import solver as sv
from services.science.catalog import angular_separation_deg, get_all_sky_catalog_stars, get_catalog_stars
from services.science.catalog_model import CatalogStar
from services.science.solver import (
    HeldOutVerification,
    SourceDetection,
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    verify_held_out,
)

# Cygnus at 57 degrees altitude (the fixtures' Moon moment)
CYGNUS_UP = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
PINNED_RELEASE = "18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)"

# The random dots' solve-time bound in seconds, solve only (detection excluded), measured on an 18-core laptop shared
# with other agents' runs (load average 29 to 43): before solver-10k-a the solve took 34.1 to 37.6 s (6 runs), with
# it 0.64 to 0.91 s (20 runs) [tested, 2026-11-11]. The bound is 11 times the slowest fast solve and under a third
# of the fastest solve before
RANDOM_DOTS_SOLVE_BOUND_S = 10.0


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


@lru_cache(maxsize=2)
def _cygnus() -> tuple[np.ndarray, WCSModel, int]:
    """A simulated frame of every tile star brighter than V 8.5 in a Cygnus field, read through the benchmark's own
    tile reader (not the code under test), rendered with a known TAN and radial distortion WCS."""
    width, height, scale = 1600, 1200, 0.04
    truth = WCSModel(
        crval_ra_deg=305.0, crval_dec_deg=40.0, crpix_x=width / 2.0, crpix_y=height / 2.0,
        scale_deg_per_px=scale, rotation_deg=12.0, k1=-0.02, k2=0.004, parity=-1, width=width, height=height,
    )
    radius = math.degrees(math.atan(math.radians(0.5 * math.hypot(width, height) * scale))) + 2.0
    field = TileCatalog().cone(305.0, 40.0, radius, mag_limit=9.5, epoch_jyear=_epoch_jyear(CYGNUS_UP["obs_utc_iso"]))
    stars = [
        _RenderedStar(float(r), float(d), float(v))
        for r, d, v in zip(field.ra_deg, field.dec_deg, field.v_mag)
        if math.isfinite(v) and v <= 8.5
    ]
    frame, _, projected = render_synthetic_starfield(
        width=width, height=height, wcs=truth, catalog_stars=stars, read_noise_sigma=2.0, flux_scale=950.0, seed=5,
    )
    return frame, truth, len(projected)


@lru_cache(maxsize=1)
def _cygnus_detections() -> tuple[SourceDetection, ...]:
    return tuple(detect_point_sources(_cygnus()[0], snr_threshold=4.0))


def _sources(tag: str) -> dict[str, str]:
    # The frame's float bytes differ by platform, so the id hashes the recipe, not the pixels
    digest = hashlib.sha256(tag.encode()).hexdigest()
    return {"source_asset_id": f"synthetic-{digest[:12]}", "source_sha256": digest}


def _assert_no_claim(sol) -> None:
    assert sol.status != "calibrated", (sol.solver, sol.quality)
    assert sol.wcs is None
    assert all(f.evidence_class == "observed" and not f.has_sky_position for f in sol.findings)


def _pinned_in_frame(truth: WCSModel) -> list[str]:
    names = []
    for star in get_catalog_stars():
        x, y = truth.world_to_pixel(star.ra_deg, star.dec_deg)
        if not math.isnan(x) and 0 <= x < truth.width and 0 <= y < truth.height:
            names.append(star.common_name)
    return names


def _random_dots(width: int = 1600, height: int = 1200, sources: int = 2500, seed: int = 11) -> np.ndarray:
    """Many random point sources on a dark noisy field and no sky behind them [simulated]."""
    rng = np.random.default_rng(seed)
    luma = np.full((height, width), 8.0) + rng.normal(0.0, 1.5, (height, width))
    yy, xx = np.mgrid[0:9, 0:9]
    for x, y, amp in zip(rng.uniform(6, width - 6, sources), rng.uniform(6, height - 6, sources), rng.uniform(60, 240, sources)):
        x0, y0 = int(x) - 4, int(y) - 4
        luma[y0 : y0 + 9, x0 : x0 + 9] += amp * np.exp(-((xx + x0 - x) ** 2 + (yy + y0 - y) ** 2) / (2 * 1.3**2))
    return np.clip(luma, 0, 255)


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


# =============================================================================================
# The search before solver-10k-a, one triangle, star and detection at a time
# =============================================================================================


def _one_by_one_lookup(index, dr1: float, dr2: float) -> np.ndarray:
    """Every catalog triangle in order, kept unless a ratio lies more than 0.045 off (the loop before solver-10k-a)."""
    return np.array(
        [t for t, (r1, r2) in enumerate(index.ratios.tolist()) if not (abs(dr1 - r1) > 0.045 or abs(dr2 - r2) > 0.045)],
        dtype=np.int64,
    )


def _one_by_one_consensus_pairs(wcs, detections, stars, ra_deg=None, dec_deg=None, radius_px=18.0):
    """Each detection in turn takes the nearest star not yet taken, measured with `world_to_pixel` one star at a
    time, when it lies closer than `radius_px` (the loop before solver-10k-a)."""
    pairs = []
    used: set[str] = set()
    for det in detections:
        best_star, best_dist = None, radius_px
        for star in stars:
            if star.id in used:
                continue
            px, py = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
            if math.isnan(px):
                continue
            dist = math.hypot(px - det.x_px, py - det.y_px)
            if dist < best_dist:
                best_dist, best_star = dist, star
        if best_star is not None:
            used.add(best_star.id)
            pairs.append((det, best_star, best_dist))
    return pairs


def _brute_force_pairs(wcs, detections, catalog):
    """`verify_held_out`'s pairing written one detection and one star at a time, as it was before solver-10k-a."""
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


def _one_by_one_verify_held_out(wcs, detections, catalog, training_detections) -> HeldOutVerification:
    """`verify_held_out` before solver-10k-a: the one-by-one pairing, then the inlier gate from all residuals."""
    train_ids = {id(det) for det in training_detections}
    pairs = _brute_force_pairs(wcs, detections, catalog)
    raw = np.array([res for _, _, res in pairs], dtype=np.float64)
    if raw.size:
        med = float(np.median(raw))
        mad = float(np.median(np.abs(raw - med)))
        gate = min(sv.INLIER_GATE_MAX_PX, max(sv.INLIER_GATE_MIN_PX, med + 3.0 * max(0.35, 1.4826 * mad)))
    else:
        gate = sv.INLIER_GATE_MAX_PX
    return HeldOutVerification(associations=tuple((d, s, r, id(d) in train_ids) for d, s, r in pairs), inlier_gate_px=gate)


def _main_consensus_pairs(wcs, detections, stars, ra_deg=None, dec_deg=None, radius_px=None):
    """The search's consensus pairing before solver-10k-a, at its own 18 px: the radius the caller passes is ignored,
    so a changed radius at the call shows as a different solution."""
    return _one_by_one_consensus_pairs(wcs, detections, stars, radius_px=18.0)


def _use_one_by_one_rules(mp: pytest.MonkeyPatch) -> None:
    """Swap the ratio lookup, the consensus pairing and the held-out pairing for the loops of the search before
    solver-10k-a, so `solve_and_verify_field` then gives that search's solution."""
    mp.setattr(sv._RatioIndex, "lookup", _one_by_one_lookup)
    mp.setattr(sv, "_greedy_pairs", _main_consensus_pairs)
    mp.setattr(sv, "verify_held_out", _one_by_one_verify_held_out)


def _outcome(call: Callable[[], Any]) -> str:
    """A solve's result as text: its contract dict as sorted JSON (a NaN written as NaN, so two NaN compare equal),
    or the error it raised, so a fast and a one-by-one run can be compared whatever they end in."""
    try:
        solution = call()
    except Exception as exc:  # noqa: BLE001 (compared with the other run's, never hidden)
        return f"raised {type(exc).__name__}: {exc}"
    return json.dumps(solution.to_contract_dict(), sort_keys=True)


def _solve_both_ways(detections, width: int, height: int, **kwargs) -> tuple[tuple[str, list], tuple[str, list]]:
    """(fast, one by one): each run's `_outcome` and its on_candidate sequence."""

    def run() -> tuple[str, list]:
        events: list[tuple[int, int]] = []
        out = _outcome(lambda: solve_and_verify_field(
            list(detections), width, height, on_candidate=lambda done, total: events.append((done, total)), **kwargs
        ))
        return out, events

    fast = run()
    with pytest.MonkeyPatch.context() as mp:
        _use_one_by_one_rules(mp)
        slow = run()
    return fast, slow


def _ci_frame(index: int):
    """(detections, width, height, moment) of the phone photo benchmark's ci frame, as its today-api adapter runs it
    (solvers.py): luma, default detection, the frame's own time and place."""
    from research.benchmarks.phone_photos import render, solvers, suite

    frame = render.render_frame(suite.build_suite("ci")[index], TileCatalog())
    luma = solvers._luma(frame.rgb)
    meta = frame.truth.metadata
    moment = {"obs_utc_iso": meta["utc"], "observer_lat": meta["latDeg"], "observer_lon": meta["lonDeg"]}
    return detect_point_sources(luma), luma.shape[1], luma.shape[0], moment


def _pinned_field():
    """The 71 pinned all-sky stars rendered under the Orion WCS (960 x 540)."""
    wcs = _orion_wcs_and_stars()[0]
    frame, _, _ = render_synthetic_starfield(width=960, height=540, wcs=wcs, catalog_stars=get_all_sky_catalog_stars(),
                                             read_noise_sigma=2.0, flux_scale=950.0, seed=0)
    return detect_point_sources(frame), 960, 540, {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _wide_field():
    """A dense 100 deg diagonal field with pincushion distortion (2000 x 1500, k1 0.10) south of Orion, rendered from
    the tile stars to V 6.5 and the 71 pinned all-sky stars inside its corners plus 1 deg."""
    width, height = 2000, 1500
    scale = math.degrees(math.tan(math.radians(50.0))) / math.hypot(width / 2.0, height / 2.0)
    wcs = WCSModel(crval_ra_deg=90.0, crval_dec_deg=-5.0, crpix_x=width / 2.0, crpix_y=height / 2.0, scale_deg_per_px=scale,
                   rotation_deg=-60.0, k1=0.10, k2=0.0, parity=-1, width=width, height=height)
    corners = ((0.0, 0.0), (width, 0.0), (0.0, height), (width, height))
    reach = max(angular_separation_deg(*wcs.pixel_to_world(x, y), 90.0, -5.0) for x, y in corners) + 1.0
    field = TileCatalog().cone(90.0, -5.0, reach, mag_limit=7.5, epoch_jyear=2030.0)
    stars = [_RenderedStar(float(r), float(d), float(v)) for r, d, v in zip(field.ra_deg, field.dec_deg, field.v_mag) if math.isfinite(v) and v <= 6.5]
    stars += [s for s in get_all_sky_catalog_stars() if angular_separation_deg(s.ra_deg, s.dec_deg, 90.0, -5.0) <= reach]
    frame, _, _ = render_synthetic_starfield(width=width, height=height, wcs=wcs, catalog_stars=stars, read_noise_sigma=2.0,
                                             flux_scale=950.0, seed=56)
    return detect_point_sources(frame), width, height, {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _dense_cygnus():
    _, truth, rendered = _cygnus()
    assert rendered > 3000 and _pinned_in_frame(truth) == []
    return list(_cygnus_detections()), 1600, 1200, CYGNUS_UP


# (frame, catalog): the status the search before solver-10k-a gave, so each case reaches the part it checks
# [tested, 2026-11-11: .lanetools/kiro10k-a/parity_main.jsonl]
CHARACTERIZATION_CASES = {
    ("ci-02", "pinned"): "ambiguous",
    ("ci-02", "71-star"): "ambiguous",
    ("pinned-field", "pinned"): "calibrated",
    ("pinned-field", "71-star"): "calibrated",
    ("wide-field", "pinned"): "calibrated",
    ("wide-field", "71-star"): "calibrated",
    ("cygnus-dense", "pinned"): "unresolved",
    ("cygnus-dense", "71-star"): "unresolved",
}
_FRAMES = {"ci-02": lambda: _ci_frame(2), "pinned-field": _pinned_field, "wide-field": _wide_field, "cygnus-dense": _dense_cygnus}


@pytest.mark.parametrize(("frame_name", "catalog"), list(CHARACTERIZATION_CASES), ids=lambda v: str(v))
def test_the_fast_search_gives_the_solution_of_the_one_by_one_rules(frame_name: str, catalog: str, monkeypatch: pytest.MonkeyPatch) -> None:
    detections, width, height, moment = _FRAMES[frame_name]()
    kwargs = dict(**moment, catalog_stars=get_all_sky_catalog_stars() if catalog == "71-star" else None, **_sources(frame_name))
    fast = solve_and_verify_field(list(detections), width, height, **kwargs)
    _use_one_by_one_rules(monkeypatch)
    slow = solve_and_verify_field(list(detections), width, height, **kwargs)
    assert slow.status == CHARACTERIZATION_CASES[(frame_name, catalog)]
    assert fast.to_contract_dict() == slow.to_contract_dict()


# =============================================================================================
# Dense frames: within the time bound, no claim
# =============================================================================================


@pytest.mark.parametrize("moment", [CYGNUS_UP, None], ids=["with-time-and-place", "blind"])
def test_random_dots_finish_within_the_bound_and_make_no_claim(moment: dict | None) -> None:
    detections = detect_point_sources(_random_dots(), snr_threshold=4.0)
    assert len(detections) > 1500
    started = time.monotonic()
    sol = solve_and_verify_field(detections, 1600, 1200, **(moment or {}), **_sources("random-dots"))
    elapsed = time.monotonic() - started
    _assert_no_claim(sol)
    assert sol.solver.catalog_release == PINNED_RELEASE
    assert elapsed < RANDOM_DOTS_SOLVE_BOUND_S, f"took {elapsed:.2f} s"


# =============================================================================================
# The pieces: ratio lookup, projection, consensus pairing, held-out pairing
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
# Exactly 0.045 off in floats, on either ratio and on both: inside, as `> 0.045` leaves it
@example(ratios=[(0.045, 0.0), (0.0, 0.045), (0.045, 0.045)], query=(0.0, 0.0))
def test_pbt_the_ratio_lookup_returns_exactly_the_triangles_within_the_tolerance(
    ratios: list[tuple[float, float]], query: tuple[float, float]
) -> None:
    index = sv._RatioIndex(ratios)
    dr1, dr2 = query
    expected = [i for i, (r1, r2) in enumerate(ratios) if not (abs(dr1 - r1) > 0.045 or abs(dr2 - r2) > 0.045)]
    assert index.lookup(dr1, dr2).tolist() == expected


def _at_cos_c(ra0: float, dec0: float, cos_c: float) -> tuple[float, float]:
    """The sky position at angular distance acos(`cos_c`) from (ra0, dec0), toward the north celestial pole."""
    r0, d0 = math.radians(ra0), math.radians(dec0)
    u = (math.cos(d0) * math.cos(r0), math.cos(d0) * math.sin(r0), math.sin(d0))
    w = (-math.sin(d0) * math.cos(r0), -math.sin(d0) * math.sin(r0), math.cos(d0))
    s = math.sqrt(1.0 - cos_c * cos_c)
    p = [cos_c * a + s * b for a, b in zip(u, w)]
    return math.degrees(math.atan2(p[1], p[0])) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, p[2]))))


@settings(max_examples=80, deadline=None)
@given(
    ra0=st.floats(0.0, 360.0),
    dec0=st.floats(-89.9, 89.9),
    scale=st.floats(0.002, 0.25),
    rotation=st.floats(-180.0, 180.0),
    k1=st.floats(-0.25, 0.25),
    k2=st.floats(-0.25, 0.25),
    parity=st.sampled_from([-1, 1]),
    seed=st.integers(0, 2**16),
)
# Across the RA wrap; at the pole; strong radial terms of both signs on a wide frame
@example(ra0=359.95, dec0=10.0, scale=0.05, rotation=30.0, k1=-0.02, k2=0.004, parity=-1, seed=1)
@example(ra0=12.0, dec0=89.9, scale=0.1, rotation=-120.0, k1=0.0, k2=0.0, parity=1, seed=2)
@example(ra0=200.0, dec0=-40.0, scale=0.25, rotation=0.0, k1=-0.25, k2=0.25, parity=-1, seed=3)
def test_pbt_the_vectorized_projection_is_world_to_pixel_star_by_star(ra0, dec0, scale, rotation, k1, k2, parity, seed) -> None:
    wcs = WCSModel(crval_ra_deg=ra0, crval_dec_deg=dec0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=scale,
                   rotation_deg=rotation, k1=k1, k2=k2, parity=parity, width=1600, height=1200)
    rng = np.random.default_rng(seed)
    # Stars over the whole sky, stars on the frame, the tangent point, its antipode (behind the camera) and RA past 360
    ra = list(rng.uniform(0.0, 360.0, 150))
    dec = list(np.degrees(np.arcsin(rng.uniform(-1.0, 1.0, 150))))
    for x, y in rng.uniform([0.0, 0.0], [1600.0, 1200.0], (50, 2)):
        r, d = wcs.pixel_to_world(float(x), float(y))
        ra.append(r)
        dec.append(d)
    ra += [ra0, ra0 + 180.0, ra0 + 360.0]
    dec += [dec0, -dec0, dec0]
    # RA 2**44 turns past 360, which `% 360` wraps exactly as world_to_pixel does; and a star whose cos_c is 5e-7,
    # positive but behind the 1e-6 limit, so NaN in both
    ra.append(ra0 + 360.0 * 2.0**44)
    dec.append(dec0)
    edge_ra, edge_dec = _at_cos_c(ra0, dec0, 5e-7)
    ra.append(edge_ra)
    dec.append(edge_dec)
    px, py = sv._project(wcs, np.array(ra), np.array(dec))
    d0 = math.radians(dec0)
    for k, (r, d) in enumerate(zip(ra, dec)):
        dr, dd = math.radians(r % 360.0) - math.radians(ra0 % 360.0), math.radians(d)
        cos_c = math.sin(d0) * math.sin(dd) + math.cos(d0) * math.cos(dd) * math.cos(dr)
        if abs(cos_c - 1e-6) < 1e-12:
            continue  # on the edge of the half sky the formula projects, where the two may round apart
        ex, ey = wcs.world_to_pixel(r, d)
        assert math.isnan(ex) == math.isnan(px[k]) and math.isnan(ey) == math.isnan(py[k]), (r, d, ex, px[k])
        if not math.isnan(ex) and -1e4 <= ex <= 1600 + 1e4 and -1e4 <= ey <= 1200 + 1e4:
            assert abs(ex - px[k]) <= 1e-6 and abs(ey - py[k]) <= 1e-6, (r, d, ex - px[k], ey - py[k])


def _detections_near_stars(wcs, stars, offsets, extra, seed) -> list[SourceDetection]:
    """Detections at the seeded in-frame stars' pixels moved by `offsets`, plus detections at `extra` pixels."""
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
    return detections


@settings(max_examples=40, deadline=None)
@given(
    offsets=st.lists(st.tuples(st.floats(-25.0, 25.0), st.floats(-25.0, 25.0), st.just(False)), min_size=0, max_size=40),
    extra=st.lists(st.tuples(st.floats(0.0, 959.0), st.floats(0.0, 539.0)), max_size=20),
    seed=st.integers(0, 2**16),
    radius=st.sampled_from([18.0, 8.0, 3.0]),
)
# Two detections on one star (the second takes the next nearest), and detections just inside, at and just past 18 px
@example(offsets=[(0.0, 0.0, False), (0.0, 0.0, False), (17.9995, 0.0, False), (18.0, 0.0, False), (0.0, -18.0005, False)],
         extra=[], seed=0, radius=18.0)
def test_pbt_the_consensus_pairs_are_the_one_by_one_rule_s(offsets, extra, seed, radius) -> None:
    wcs, stars = _orion_wcs_and_stars()
    detections = _detections_near_stars(wcs, stars, offsets, extra, seed)
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    got = sv._greedy_pairs(wcs, detections, stars, ra, dec, radius)
    want = _one_by_one_consensus_pairs(wcs, detections, stars, radius_px=radius)
    assert [(id(d), s.id, dist) for d, s, dist in got] == [(id(d), s.id, dist) for d, s, dist in want]


@settings(max_examples=30, deadline=None)
@given(
    offsets=st.lists(
        st.tuples(st.floats(-12.0, 12.0), st.floats(-12.0, 12.0), st.booleans()), min_size=0, max_size=40
    ),
    extra=st.lists(st.tuples(st.floats(0.0, 959.0), st.floats(0.0, 539.0)), max_size=20),
    seed=st.integers(0, 2**16),
)
# Two detections on one star, and detections at and just past the 8 px association radius
@example(offsets=[(0.0, 0.0, False), (0.0, 0.0, False), (8.0, 0.0, False), (8.0001, 0.0, False)], extra=[], seed=0)
def test_pbt_verify_held_out_pairs_as_the_one_by_one_rule(offsets, extra, seed) -> None:
    wcs, stars = _orion_wcs_and_stars()
    detections = _detections_near_stars(wcs, stars, offsets, extra, seed)
    training = detections[::3]
    got = verify_held_out(wcs, detections, stars, training)
    want = _one_by_one_verify_held_out(wcs, detections, stars, training)
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    assert got.inlier_gate_px == want.inlier_gate_px


class _ShiftedWcs(WCSModel):
    """A WCS subclass whose `world_to_pixel` lands 20 px right of `WCSModel`'s formula."""

    def world_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        x, y = super().world_to_pixel(ra_deg, dec_deg)
        return (x + 20.0, y)


def test_a_wcs_subclass_is_paired_through_its_own_projection() -> None:
    base, stars = _orion_wcs_and_stars()
    shifted = _ShiftedWcs(**{f.name: getattr(base, f.name) for f in fields(base)})
    detections = _detections_near_stars(shifted, stars, [(0.3, -0.2, False)] * 12, [], 5)
    # Each detection is 0.36 px from where the subclass puts a star, and 20 px from where WCSModel's own formula does
    want = _brute_force_pairs(shifted, detections, stars)
    assert len(want) >= 6 and all(r < 1.0 for _, _, r in want)
    assert len(_brute_force_pairs(base, detections, stars)) < len(want) / 2
    got = verify_held_out(shifted, detections, stars, [])
    assert [(id(d), s.id, r) for d, s, r, _ in got.associations] == [(id(d), s.id, r) for d, s, r in want]
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    pairs = sv._greedy_pairs(shifted, detections, stars, ra, dec, 18.0)
    assert [(id(d), s.id) for d, s, _ in pairs] == [(id(d), s.id) for d, s, _ in _one_by_one_consensus_pairs(shifted, detections, stars)]
    assert len(pairs) >= 6


# =============================================================================================
# The consensus radius, and pairing when the vectorized projection is a little off
# =============================================================================================


def _radius_field() -> tuple[WCSModel, list[CatalogStar], list[SourceDetection]]:
    """Eight made-up stars on a 1600 x 1200 TAN frame (0.02 deg per px), one detection each: three on their stars'
    pixels, three 17.6 px and two 18.4 px right of theirs, the 18.4 px pair in the far corners. Under the frame's own
    WCS a consensus radius of 17, 18 or 19 px pairs 3, 6 or 8 of them (made-up positions and magnitudes)."""
    wcs = WCSModel(crval_ra_deg=150.0, crval_dec_deg=20.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=0.02,
                   rotation_deg=0.0, k1=0.0, k2=0.0, parity=-1, width=1600, height=1200)
    layout = [  # (x px, y px, magnitude, detection's offset in px)
        (400.0, 300.0, 1.0, 0.0), (1200.0, 350.0, 1.1, 0.0), (800.0, 900.0, 1.2, 0.0),
        (300.0, 1000.0, 2.0, 17.6), (1350.0, 1000.0, 2.1, 17.6), (1500.0, 150.0, 2.2, 17.6),
        (60.0, 60.0, 3.0, 18.4), (1520.0, 1150.0, 3.1, 18.4),
    ]
    base = get_catalog_stars()[0]
    stars, detections = [], []
    for k, (x, y, mag, offset) in enumerate(layout):
        ra, dec = wcs.pixel_to_world(x, y)
        stars.append(replace(base, id=f"made-up-{k}", ra_deg=ra, dec_deg=dec, mag=mag))
        detections.append(SourceDetection(x_px=x + offset, y_px=y, flux=1000.0 - 100.0 * k, snr=100.0 - 10.0 * k,
                                          fwhm_px=2.0, centroid_uncertainty_px=0.1))
    return wcs, stars, detections


def test_the_consensus_pairs_stars_closer_than_18_px(monkeypatch: pytest.MonkeyPatch) -> None:
    wcs, stars, detections = _radius_field()
    # Every matched triangle gives the frame's own WCS, so a pair's distance is its offset above
    monkeypatch.setattr(sv, "_estimate_initial_wcs_from_triplet", lambda *_: wcs)
    kwargs = dict(catalog_stars=stars, **_sources("consensus-radius"))
    fast = _outcome(lambda: solve_and_verify_field(list(detections), 1600, 1200, **kwargs))
    by_radius = {}
    for radius in (17.0, 18.0, 19.0):
        with pytest.MonkeyPatch.context() as mp:
            _use_one_by_one_rules(mp)
            mp.setattr(sv, "_greedy_pairs", lambda w, d, s, *_, r=radius: _one_by_one_consensus_pairs(w, d, s, radius_px=r))
            by_radius[radius] = _outcome(lambda: solve_and_verify_field(list(detections), 1600, 1200, **kwargs))
    # The field's solution differs at 17, 18 and 19 px, so it pins the radius
    assert len(set(by_radius.values())) == 3
    assert "no consistent WCS" in by_radius[17.0]
    assert fast == by_radius[18.0]


def _off_by_a_little(shift: tuple[float, float] | None, seed: int):
    """`sv._project` with every pixel moved by `shift`, or, when None, each by its own amount up to 0.0007 px on each
    axis: what another platform's last-place trigonometry could do, at most 0.00099 px in distance, under the 1e-3 px
    margin."""
    real = sv._project
    rng = np.random.default_rng(seed)

    def project(wcs, ra_deg, dec_deg):
        x, y = real(wcs, ra_deg, dec_deg)
        if shift is None:
            return x + rng.uniform(-7e-4, 7e-4, x.shape), y + rng.uniform(-7e-4, 7e-4, y.shape)
        return x + shift[0], y + shift[1]

    return project


@settings(max_examples=40, deadline=None)
@given(
    offsets=st.lists(st.tuples(st.floats(-20.0, 20.0), st.floats(-20.0, 20.0), st.booleans()), min_size=0, max_size=40),
    extra=st.lists(st.tuples(st.floats(0.0, 959.0), st.floats(0.0, 539.0)), max_size=20),
    seed=st.integers(0, 2**16),
    shift=st.one_of(st.none(), st.tuples(st.floats(-7e-4, 7e-4), st.floats(-7e-4, 7e-4))),
)
# Detections a hair inside 8 px and inside 18 px left of a star, its projection moved 0.0007 px right and down: the
# star stays in reach of the prefilter only through its margin
@example(offsets=[(-(8.0 - 1e-7), 0.0, False), (-(18.0 - 1e-7), 0.0, False)], extra=[], seed=0, shift=(7e-4, 7e-4))
@example(offsets=[(-(8.0 - 1e-7), 0.0, False), (-(18.0 - 1e-7), 0.0, False)], extra=[], seed=3, shift=(7e-4, 7e-4))
@example(offsets=[(0.0, 8.0 - 1e-7, False), (0.0, 18.0 - 1e-7, False)], extra=[], seed=5, shift=(-7e-4, -7e-4))
def test_pbt_pairing_is_exact_when_the_projection_is_off_by_up_to_0_0007_px(offsets, extra, seed, shift) -> None:
    wcs, stars = _orion_wcs_and_stars()
    detections = _detections_near_stars(wcs, stars, offsets, extra, seed)
    training = detections[::3]
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    want_held = _one_by_one_verify_held_out(wcs, detections, stars, training)
    want_pairs = _one_by_one_consensus_pairs(wcs, detections, stars, radius_px=18.0)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sv, "_project", _off_by_a_little(shift, seed))
        got_held = verify_held_out(wcs, detections, stars, training)
        got_pairs = sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0)
    assert [(id(d), s.id, r, t) for d, s, r, t in got_held.associations] == [(id(d), s.id, r, t) for d, s, r, t in want_held.associations]
    assert got_held.inlier_gate_px == want_held.inlier_gate_px
    assert [(id(d), s.id, dist) for d, s, dist in got_pairs] == [(id(d), s.id, dist) for d, s, dist in want_pairs]


@pytest.mark.parametrize("frame_name", ["ci-02", "pinned-field"])
def test_the_solution_is_the_one_by_one_rules_when_the_projection_is_off_by_up_to_0_0007_px(
    frame_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    detections, width, height, moment = _FRAMES[frame_name]()
    kwargs = dict(**moment, **_sources(frame_name))
    monkeypatch.setattr(sv, "_project", _off_by_a_little(None, 7))
    (fast, fast_events), (slow, slow_events) = _solve_both_ways(detections, width, height, **kwargs)
    assert json.loads(slow)["status"] == CHARACTERIZATION_CASES[(frame_name, "pinned")]
    assert fast == slow and fast_events == slow_events


# =============================================================================================
# Non-finite values: the one-by-one rules' solution, never an exception they did not raise
# =============================================================================================


NAN, INF = float("nan"), float("inf")
NON_FINITE = st.sampled_from([NAN, INF, -INF])
ORION_MOMENT = {"obs_utc_iso": "2030-01-01T02:00:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


@lru_cache(maxsize=1)
def _pinned_true_pixels() -> tuple[WCSModel, tuple[SourceDetection, ...]]:
    """The auditor's reproduction: a detection 0.2 px right of and 0.1 px above the pixel of each of the 18 pinned
    stars that a 1600 x 1200 Orion WCS puts on the frame, brightest first [simulated]."""
    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=0.05,
                   rotation_deg=10.0, k1=-0.03, k2=0.005, parity=-1, width=1600, height=1200)
    detections = []
    for k, star in enumerate(get_catalog_stars()):
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if math.isfinite(x) and 0 <= x < 1600 and 0 <= y < 1200:
            detections.append(SourceDetection(x_px=x + 0.2, y_px=y - 0.1, flux=1000.0 - k, snr=100.0 - k, fwhm_px=2.0,
                                              centroid_uncertainty_px=0.1))
    return wcs, tuple(detections)


def _catalog_matches(outcome: str) -> tuple[str, int]:
    solution = json.loads(outcome)
    return solution["status"], sum(1 for f in solution["findings"] if f["evidenceClass"] == "catalog_matched")


@pytest.mark.parametrize("field", ["x_px", "y_px"])
@pytest.mark.parametrize("value", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
# Faintest: only the held-out pairing meets it; brightest: it is in the 14 the triangles are drawn from too
@pytest.mark.parametrize("snr", [5.0, 500.0], ids=["faintest", "brightest"])
def test_a_detection_at_a_non_finite_position_gives_the_one_by_one_solution(field: str, value: float, snr: float) -> None:
    wcs, pinned = _pinned_true_pixels()
    assert len(pinned) == 17
    bad = replace(SourceDetection(x_px=100.0, y_px=10.0, flux=5.0, snr=snr, fwhm_px=2.0, centroid_uncertainty_px=0.1), **{field: value})
    detections = list(pinned) + [bad]
    got = verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    want = _one_by_one_verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    assert len(got.associations) == 17
    fast, slow = _solve_both_ways(detections, 1600, 1200, **ORION_MOMENT, **_sources("non-finite-position"))
    assert fast == slow
    # What the search before solver-10k-a gave [tested, 2026-11-11, cd10d044: .lanetools/kiro10k-a2/probe_main_nonfinite.py]
    assert _catalog_matches(fast[0]) == ("calibrated", 17)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_the_solve_endpoint_answers_the_one_by_one_solution_for_a_non_finite_detection(value: str) -> None:
    from fastapi.testclient import TestClient

    from services.api import app

    _, pinned = _pinned_true_pixels()
    body = {
        "width": 1600, "height": 1200, **{"observedAtUTC": ORION_MOMENT["obs_utc_iso"], "latDeg": 30.0, "lonDeg": -40.0},
        **{"sourceAssetId": "synthetic-non-finite", "sourceSha256": hashlib.sha256(b"non-finite").hexdigest()},
        "detections": [
            {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px, "centroidUncertaintyPx": 0.1}
            for d in pinned
        ] + [{"xPx": value, "yPx": 10.0, "flux": 5.0, "snr": 5.0, "fwhmPx": 2.0, "centroidUncertaintyPx": 0.1}],
    }
    client = TestClient(app)
    fast = client.post("/api/science/solve", json=body)
    with pytest.MonkeyPatch.context() as mp:
        _use_one_by_one_rules(mp)
        slow = client.post("/api/science/solve", json=body)
    assert fast.status_code == slow.status_code == 200, fast.text[:200]
    assert fast.json() == slow.json()
    assert fast.json()["status"] == "calibrated"
    assert sum(1 for f in fast.json()["findings"] if f["evidenceClass"] == "catalog_matched") == 17


@settings(max_examples=20, deadline=None)
@given(
    # At least 6 kept, so every example reaches the triangle search
    keep=st.lists(st.integers(0, 16), min_size=6, max_size=17, unique=True),
    jitter=st.tuples(st.floats(-0.5, 0.5), st.floats(-0.5, 0.5)),
    extra=st.lists(st.tuples(st.floats(0.0, 1599.0), st.floats(0.0, 1199.0), st.floats(3.0, 300.0)), max_size=12),
    bad=st.lists(st.tuples(st.integers(0, 40), st.sampled_from(["x_px", "y_px", "flux", "snr"]), NON_FINITE), min_size=1, max_size=6),
    order_seed=st.integers(0, 2**16),
)
# The brightest detection at a NaN x, so the triangles drawn from it have NaN ratios
@example(keep=list(range(17)), jitter=(0.0, 0.0), extra=[], bad=[(0, "x_px", NAN)], order_seed=0)
# One of each: NaN snr, infinite x, minus infinite y, NaN flux, on the brightest four
@example(keep=list(range(17)), jitter=(0.1, -0.1), extra=[], bad=[(0, "snr", NAN), (1, "x_px", INF), (2, "y_px", -INF), (3, "flux", NAN)], order_seed=1)
# Both positions infinite on the brightest, and an infinite snr on a random dot
@example(keep=list(range(10)), jitter=(0.0, 0.0), extra=[(300.0, 200.0, 50.0)], bad=[(0, "x_px", INF), (0, "y_px", INF), (10, "snr", INF)], order_seed=2)
# Two bright detections at one place and one at a NaN y: the side ratios of their triangle divide by a zero side,
# and the search before solver-10k-a raised ZeroDivisionError there (the same line runs in both)
@example(keep=list(range(6)), jitter=(0.0, 0.0), extra=[(500.0, 500.0, 300.0), (500.0, 500.0, 290.0)], bad=[(0, "y_px", NAN)], order_seed=0)
def test_pbt_non_finite_detections_give_the_one_by_one_whole_solution(keep, jitter, extra, bad, order_seed) -> None:
    _, pinned = _pinned_true_pixels()
    detections = [replace(pinned[i], x_px=pinned[i].x_px + jitter[0], y_px=pinned[i].y_px + jitter[1]) for i in sorted(keep)]
    detections += [SourceDetection(x_px=x, y_px=y, flux=10.0 * snr, snr=snr, fwhm_px=2.0, centroid_uncertainty_px=0.1) for x, y, snr in extra]
    if not detections:
        return
    if order_seed:
        detections = [detections[i] for i in np.random.default_rng(order_seed).permutation(len(detections))]
    for index, name, value in bad:
        k = index % len(detections)
        detections[k] = replace(detections[k], **{name: value})
    fast, slow = _solve_both_ways(detections, 1600, 1200, **ORION_MOMENT, **_sources("non-finite-detections"))
    assert fast == slow


odd_ratio = st.one_of(NON_FINITE, ratio)


@settings(max_examples=60, deadline=None)
@given(ratios=st.lists(st.tuples(odd_ratio, odd_ratio), min_size=0, max_size=40), query=st.tuples(odd_ratio, odd_ratio))
# A NaN ratio keeps every triangle on its axis (`abs(nan - r) > 0.045` is False); an infinite one keeps only a NaN
@example(ratios=[(0.5, 0.5), (0.9, 0.1), (0.5, 0.9)], query=(NAN, 0.5))
@example(ratios=[(0.5, 0.5), (0.9, 0.1)], query=(NAN, NAN))
@example(ratios=[(0.5, 0.5), (NAN, 0.5), (INF, 0.5)], query=(INF, 0.5))
@example(ratios=[(NAN, 0.5), (0.5, NAN), (-INF, 0.5), (0.52, 0.48)], query=(0.5, 0.5))
def test_pbt_a_non_finite_ratio_keeps_what_the_loop_kept(ratios, query) -> None:
    index = sv._RatioIndex(ratios)
    dr1, dr2 = query
    expected = [i for i, (r1, r2) in enumerate(ratios) if not (abs(dr1 - r1) > 0.045 or abs(dr2 - r2) > 0.045)]
    assert index.lookup(dr1, dr2).tolist() == expected


def _pairing_outcome(call: Callable[[], Any]) -> tuple:
    try:
        return ("pairs", call())
    except Exception as exc:  # noqa: BLE001 (compared with the one-by-one run's, never hidden)
        return ("raised", type(exc).__name__, str(exc))


@pytest.mark.parametrize(
    ("change", "behind"),
    [({"rotation_deg": INF}, False), ({"rotation_deg": INF}, True), ({"crval_dec_deg": -INF}, False),
     ({"scale_deg_per_px": 0.0}, False), ({"scale_deg_per_px": 0.0}, True), ({"crval_ra_deg": NAN}, False)],
    ids=["rotation-inf", "rotation-inf-stars-behind", "dec0-minus-inf", "scale-0", "scale-0-stars-behind", "ra0-nan"],
)
def test_a_wcs_value_world_to_pixel_refuses_ends_both_pairings_as_the_loops_did(change: dict, behind: bool) -> None:
    base, clean = _orion_wcs_and_stars()
    detections = _detections_near_stars(base, clean, [(0.5, 0.5, False)] * 8, [], 1)
    # `world_to_pixel` refuses an infinite angle or a zero scale only for a star in front of the camera
    stars = [replace(s, ra_deg=(s.ra_deg + 180.0) % 360.0, dec_deg=-s.dec_deg) for s in clean] if behind else list(clean)
    wcs = replace(base, **change)
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    for got, want in [
        (lambda: verify_held_out(wcs, detections, stars, detections[:2]).associations,
         lambda: _one_by_one_verify_held_out(wcs, detections, stars, detections[:2]).associations),
        (lambda: sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0),
         lambda: _one_by_one_consensus_pairs(wcs, detections, stars)),
    ]:
        assert _pairing_outcome(got) == _pairing_outcome(want)


@settings(max_examples=40, deadline=None)
@given(
    offsets=st.lists(st.tuples(st.floats(-12.0, 12.0), st.floats(-12.0, 12.0), st.booleans()), min_size=0, max_size=30),
    extra=st.lists(st.tuples(st.floats(0.0, 959.0), st.floats(0.0, 539.0)), max_size=10),
    seed=st.integers(0, 2**16),
    bad_detections=st.lists(st.tuples(st.integers(0, 60), st.sampled_from(["x_px", "y_px"]), NON_FINITE), max_size=6),
    bad_stars=st.lists(st.tuples(st.integers(0, 329), st.sampled_from(["ra_deg", "dec_deg"]), NON_FINITE), max_size=3),
    odd_pixels=st.lists(st.tuples(st.integers(0, 329), st.integers(0, 1), NON_FINITE), max_size=8),
)
# A projection that gives some stars a NaN or infinite y (or x) with a finite other coordinate
@example(offsets=[(0.5, 0.5, False)] * 12, extra=[], seed=1, bad_detections=[], bad_stars=[],
         odd_pixels=[(k, 1, NAN) for k in range(0, 330, 3)] + [(k, 0, INF) for k in range(1, 330, 3)])
# A detection at an infinite x and a star at a NaN RA; a star at an infinite declination, which the loop's
# `math.sin` refuses
@example(offsets=[(0.5, 0.5, False)] * 6, extra=[], seed=2, bad_detections=[(0, "x_px", INF), (1, "y_px", NAN)],
         bad_stars=[(4, "ra_deg", NAN)], odd_pixels=[])
@example(offsets=[(0.5, 0.5, False)] * 6, extra=[], seed=2, bad_detections=[], bad_stars=[(7, "dec_deg", -INF)], odd_pixels=[])
def test_pbt_non_finite_stars_detections_and_projections_pair_as_the_one_by_one_rules(
    offsets, extra, seed, bad_detections, bad_stars, odd_pixels
) -> None:
    base, clean = _orion_wcs_and_stars()
    detections = _detections_near_stars(base, clean, offsets, extra, seed)
    for index, name, value in bad_detections:
        if detections:
            k = index % len(detections)
            detections[k] = replace(detections[k], **{name: value})
    stars = list(clean)
    for index, name, value in bad_stars:
        stars[index % len(stars)] = replace(stars[index % len(stars)], **{name: value})
    odd = {}
    for index, axis, value in odd_pixels:
        star = clean[index % len(clean)]
        odd[(star.ra_deg, star.dec_deg)] = (axis, value)

    class _OddPixels(WCSModel):
        """`WCSModel`'s projection with one coordinate made NaN or infinite for the chosen stars."""

        def world_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
            x, y = super().world_to_pixel(ra_deg, dec_deg)
            axis, value = odd.get((ra_deg, dec_deg), (None, None))
            return (value, y) if axis == 0 else (x, value) if axis == 1 else (x, y)

    wcs = _OddPixels(**{f.name: getattr(base, f.name) for f in fields(base)}) if odd else base
    training = detections[::3]
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])

    def held(fn):
        def call():
            v = fn(wcs, detections, stars, training)
            return [(id(d), s.id, r, t) for d, s, r, t in v.associations], v.inlier_gate_px

        return call

    assert _pairing_outcome(held(verify_held_out)) == _pairing_outcome(held(_one_by_one_verify_held_out))
    got = _pairing_outcome(lambda: [(id(d), s.id, r) for d, s, r in sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0)])
    want = _pairing_outcome(lambda: [(id(d), s.id, r) for d, s, r in _one_by_one_consensus_pairs(wcs, detections, stars)])
    assert got == want
