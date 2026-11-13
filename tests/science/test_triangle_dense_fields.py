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
- Values no record holds. A detection or a WCS refuses, when it is built, what the observation solution contract
  refuses (a position that is not finite or below 0, a flux or an SNR not above 0, a WCS angle that is no number, a
  scale of 0), and POST /api/science/solve answers such a detection with 422 and the record's sentence, with the
  one-by-one rules as with the fast ones. Handed to the pairings past the record's check, as raw values (`_raw`),
  they pair as the one-by-one rules do, never with an exception the loops did not raise: a detection with a NaN or
  infinite position, NaN and infinite ratios (PBT), and stars, detections and projections with NaN or infinite
  values in both pairings (PBT).
- Every float: the index limit is sqrt(largest float) / 4, the largest magnitude at which no squared distance between
  two points overflows; a KD-tree holds every point within it and overflows past it. A detection at each boundary
  (the limit, the next float past it, subnormals, 1e200, the largest float, both signs, both axes, faintest and
  brightest) pairs as the one-by-one rule pairs it; one the record takes gives the one-by-one solution, but for
  1e200 and the largest float, where the solver's verified WCS gives the detection no sky position and the finding
  record refuses it (held by name: the solver's own to change); stars projected past the limit pair as the loops
  pair them; positions, ratios, fluxes and snrs drawn from every float give the one-by-one pairs and lookups and,
  where the record takes them, whole solutions with and without a hint (PBT), and the same answers through POST
  /api/science/solve (PBT, and huge values as JSON text, a 200-digit integer and numbers).
- What the search hands the fast pieces is main's: every catalog triangle with its own ratios, the detections
  without hot pixels, the whole catalog with its positions and an 18 px radius (the one-by-one path takes these
  from the same calls, so they are pinned here).

Every frame here is simulated: rendered from catalog positions by `render_synthetic_starfield` or by the phone photo
benchmark's renderer, never a recording. The moments and places are synthetic: the fixtures' open North Atlantic
place (30 N, 40 W), and the benchmark frame's own.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, fields, replace
from functools import lru_cache
import hashlib
import json
import math
import sys
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
from services.science.wcs_models import ContractRefusal

# Cygnus at 57 degrees altitude (the fixtures' Moon moment)
CYGNUS_UP = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}
PINNED_RELEASE = "18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)"

# The random dots' solve-time bound in seconds, solve only (detection excluded). Before landing (parent dcdbd65d,
# 2026-11-11) the lane timed it on an 18-core laptop shared with other agents' runs (load average 29 to 43): the
# search before solver-10k-a took 34.1 to 37.6 s (6 runs), this one 0.64 to 0.91 s (20 runs) [inference: that timing
# script is not in the repository]. The bound is 11 times the slowest fast solve and under a third of the fastest
# solve before; the test below asserts the solve stays within it [tested]
RANDOM_DOTS_SOLVE_BOUND_S = 10.0


@dataclass(frozen=True)
class _RenderedStar:
    """The three values `render_synthetic_starfield` reads from a star: its position and magnitude."""

    ra_deg: float
    dec_deg: float
    mag: float


def _raw(record: Any, **values: Any) -> Any:
    """`record` copied with `values` set as they are, past the record's own check.

    A detection or a WCS refuses, when it is built, a value the observation solution contract refuses
    (`ContractRefusal`), so no solution holds one. What the search does with such a value is still its own to get
    right, and the tests of that hand it the value in a record that never checked it. The copy keeps the record's
    exact type, which `_project` asks for before it uses its vectorized formula."""
    raw = copy.copy(record)
    for name, value in values.items():
        object.__setattr__(raw, name, value)
    return raw


_A_DETECTION = SourceDetection(x_px=1.0, y_px=1.0, flux=1.0, snr=1.0, fwhm_px=2.0, centroid_uncertainty_px=0.1)


def _raw_detection(**values: Any) -> SourceDetection:
    """A detection 2 px wide with a centroid uncertainty of 0.1 px, holding `values` as they are (`_raw`)."""
    return _raw(_A_DETECTION, **values)


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


# (frame, catalog): the status the search before solver-10k-a gave, so each case reaches the part it checks. The
# test's one-by-one run (that search's loops, written out above) asserts each [tested]. Before landing, on 2026-11-11,
# the lane also ran that search itself, `git show dcdbd65d:services/science/solver.py` loaded as a module, on these
# frames: 8 of 8 [inference: that loader is not in the repository]
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
    """Detections at the seeded in-frame stars' pixels moved by `offsets`, plus detections at `extra` pixels. A star
    near the frame's top or left edge, moved up or left, lands below 0, which the detection record refuses: the moved
    ones are handed to the pairings as raw values (`_raw_detection`)."""
    rng = np.random.default_rng(seed)
    in_frame = []
    for star in stars:
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if not math.isnan(x) and 0 <= x < 960 and 0 <= y < 540:
            in_frame.append((x, y))
    detections = []
    for k, (dx, dy, hot) in enumerate(offsets):
        x, y = in_frame[int(rng.integers(len(in_frame)))] if in_frame else (480.0, 270.0)
        detections.append(_raw_detection(x_px=x + dx, y_px=y + dy, flux=100.0 + k, snr=20.0, fwhm_px=2.0 if not hot else 0.6,
                                         peak_to_total_ratio=0.2 if not hot else 0.9))
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


@pytest.mark.parametrize("field", ["x_px", "y_px"])
@pytest.mark.parametrize("value", [NAN, INF, -INF], ids=["nan", "inf", "-inf"])
# Faintest: only the held-out pairing meets it; brightest: it is in the 14 the triangles are drawn from too
@pytest.mark.parametrize("snr", [5.0, 500.0], ids=["faintest", "brightest"])
def test_a_detection_at_a_non_finite_position_gives_the_one_by_one_solution(field: str, value: float, snr: float) -> None:
    """The record refuses a position that is no finite number, so no whole solution holds one; below the record, the
    held-out pairing meets the raw value as the one-by-one rule does."""
    wcs, pinned = _pinned_true_pixels()
    assert len(pinned) == 17
    values = {"x_px": 100.0, "y_px": 10.0, "flux": 5.0, "snr": snr, field: value}
    with pytest.raises(ContractRefusal, match=f"A source detection's {field} is outside the contract: this number is not finite."):
        SourceDetection(**values, fwhm_px=2.0, centroid_uncertainty_px=0.1)
    bad = _raw_detection(**values)
    detections = list(pinned) + [bad]
    got = verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    want = _one_by_one_verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    assert len(got.associations) == 17


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_the_solve_endpoint_answers_the_one_by_one_solution_for_a_non_finite_detection(value: str) -> None:
    """The route refuses the detection with 422 and the record's sentence, with the fast rules and with the
    one-by-one rules alike: written as text, where a number goes, and written as the number itself."""
    from fastapi.testclient import TestClient

    from services.api import app

    _, pinned = _pinned_true_pixels()
    sha = hashlib.sha256(b"non-finite").hexdigest()
    body = {
        "width": 1600, "height": 1200, **{"observedAtUTC": ORION_MOMENT["obs_utc_iso"], "latDeg": 30.0, "lonDeg": -40.0},
        **{"sourceAssetId": f"src_{sha[:16]}", "sourceSha256": sha},
        "detections": [
            {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px, "centroidUncertaintyPx": 0.1}
            for d in pinned
        ] + [{"xPx": value, "yPx": 10.0, "flux": 5.0, "snr": 5.0, "fwhmPx": 2.0, "centroidUncertaintyPx": 0.1}],
    }
    client = TestClient(app)
    for sent, said in (
        (value, "A source detection's x_px is outside the contract: this value is not a number."),
        (float(value), "A source detection's x_px is outside the contract: this number is not finite."),
    ):
        body["detections"][-1]["xPx"] = sent
        # Python's JSON writer writes NaN and Infinity as the tokens the route's reader takes for those numbers
        text = json.dumps(body)
        fast = client.post("/api/science/solve", content=text, headers={"content-type": "application/json"})
        with pytest.MonkeyPatch.context() as mp:
            _use_one_by_one_rules(mp)
            slow = client.post("/api/science/solve", content=text, headers={"content-type": "application/json"})
        assert fast.status_code == slow.status_code == 422, fast.text[:200]
        assert fast.json() == slow.json() == {"detail": said}
    # Without it, the 17 pinned detections solve
    body["detections"].pop()
    solved = client.post("/api/science/solve", json=body)
    assert solved.status_code == 200 and solved.json()["status"] == "calibrated"
    assert sum(1 for f in solved.json()["findings"] if f["evidenceClass"] == "catalog_matched") == 17


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
    """No whole solution holds a detection with a value that is no finite number: the record refuses each one when it
    is built, whichever detection and whichever of its values it is."""
    _, pinned = _pinned_true_pixels()
    detections = [replace(pinned[i], x_px=pinned[i].x_px + jitter[0], y_px=pinned[i].y_px + jitter[1]) for i in sorted(keep)]
    detections += [SourceDetection(x_px=x, y_px=y, flux=10.0 * snr, snr=snr, fwhm_px=2.0, centroid_uncertainty_px=0.1) for x, y, snr in extra]
    if not detections:
        return
    if order_seed:
        detections = [detections[i] for i in np.random.default_rng(order_seed).permutation(len(detections))]
    for index, name, value in bad:
        k = index % len(detections)
        with pytest.raises(ContractRefusal, match=f"A source detection's {name} is outside the contract: this number is not finite."):
            replace(detections[k], **{name: value})


odd_ratio = st.one_of(NON_FINITE, ratio)


@settings(max_examples=60, deadline=None)
@given(ratios=st.lists(st.tuples(odd_ratio, odd_ratio), min_size=0, max_size=40), query=st.tuples(odd_ratio, odd_ratio))
# A NaN ratio keeps every triangle on its axis (`abs(nan - r) > 0.045` is False); an infinite one keeps only a NaN
@example(ratios=[(0.5, 0.5), (0.9, 0.1), (0.5, 0.9)], query=(NAN, 0.5))
@example(ratios=[(0.5, 0.5), (0.9, 0.1)], query=(NAN, NAN))
@example(ratios=[(0.5, 0.5), (NAN, 0.5), (INF, 0.5)], query=(INF, 0.5))
@example(ratios=[(NAN, 0.5), (0.5, NAN), (-INF, 0.5), (0.52, 0.48)], query=(0.5, 0.5))
# A finite first ratio with a non-finite second: the second alone must send the query past the tree
@example(ratios=[(0.5, 0.5), (0.52, 0.9), (0.9, 0.5), (NAN, 0.1)], query=(0.5, NAN))
# An index with no row the tree can hold, asked with a finite ratio: the rows kept aside are still compared
@example(ratios=[(NAN, 0.5), (0.5, INF), (0.5, NAN)], query=(0.5, 0.5))
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
    # The WCS record refuses each of these values when it is built, so no fit and no solution holds one. Handed one
    # past that check, both pairings still end as the loops did
    (name,) = change
    with pytest.raises(ContractRefusal, match=f"A WCS's {name} is outside the contract"):
        replace(base, **change)
    wcs = _raw(base, **change)
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
            # A position the record refuses, handed to the pairings as a raw value
            detections[k] = _raw(detections[k], **{name: value})
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


# =============================================================================================
# Every float: what a KD-tree can hold, and the one-by-one rules' solution for every other value
# =============================================================================================


FLOAT_MAX = sys.float_info.max
# Derived here from the float format as the solver derives it: on two axes within [-L, L] a squared distance is at
# most 2 * (2L)**2 = max / 2
INDEX_LIMIT = math.sqrt(FLOAT_MAX) / 4.0
PAST_LIMIT = math.nextafter(INDEX_LIMIT, INF)
SUBNORMAL = 5e-324
# The boundaries of what the index holds, both signs: the limit and the next float past it, subnormals, the
# auditor's 1e200, the largest float, and where cKDTree's own overflow starts for a point at the origin
BOUNDARY_VALUES = [INDEX_LIMIT, PAST_LIMIT, -INDEX_LIMIT, -PAST_LIMIT, SUBNORMAL, -SUBNORMAL, 1.35e154, 1e200, -1e200,
                   FLOAT_MAX, -FLOAT_MAX]
ANY_FLOAT = st.floats()  # every float: subnormals, both signs, NaN and both infinities
# Positions the detection record takes that lie so far off the frame that the solver's verified WCS gives them a sky
# position that is no number. The finding record refuses that position, inside the solve (services/science/solver.py,
# the observed finding under a verified WCS), where the search before the records held their values wrote it out.
# Held here by name as it is today: the solver's own to change, by refusing such a detection before it solves
BEYOND_THE_VERIFIED_WCS = (1e200, FLOAT_MAX)
NO_SKY_POSITION_FROM_THE_VERIFIED_WCS = (
    "raised ContractRefusal: A finding's ra_deg is outside the contract: this number is not finite."
)


def test_the_index_limit_is_derived_from_the_float_format() -> None:
    assert sv._INDEX_LIMIT == INDEX_LIMIT
    assert sv._indexable(np.array([[INDEX_LIMIT, -INDEX_LIMIT], [-INDEX_LIMIT, INDEX_LIMIT], [SUBNORMAL, 0.0]])).all()
    for bad in [(PAST_LIMIT, 0.0), (0.0, -PAST_LIMIT), (NAN, 0.0), (0.0, INF), (-INF, 0.0), (FLOAT_MAX, FLOAT_MAX)]:
        assert not sv._indexable(np.array([bad]))[0], bad


def test_a_kd_tree_holds_every_point_within_the_limit_and_overflows_past_it() -> None:
    from scipy.spatial import cKDTree

    corners = [(-INDEX_LIMIT, -INDEX_LIMIT), (INDEX_LIMIT, INDEX_LIMIT), (-INDEX_LIMIT, INDEX_LIMIT), (INDEX_LIMIT, -INDEX_LIMIT)]
    for p in (2, INF):
        tree = cKDTree(np.array(corners))
        for q in corners:
            assert tree.query_ball_point(np.array(q), r=18.001, p=p) == [corners.index(q)]
    # The case the index must keep out: a squared distance past the largest float (scipy 1.18.1). Against a point at
    # the origin a query at (v, v) holds at v = sqrt(max / 2) and overflows from the next float on
    edge = math.sqrt(FLOAT_MAX / 2.0)
    assert cKDTree(np.array([[0.0, 0.0]])).query_ball_point(np.array([edge, edge]), r=18.001) == []
    with pytest.raises(ValueError, match="overflow"):
        after = math.nextafter(edge, INF)
        cKDTree(np.array([[0.0, 0.0]])).query_ball_point(np.array([after, after]), r=18.001)
    with pytest.raises(ValueError, match="overflow"):
        cKDTree(np.array([[0.0, 0.0]])).query_ball_point(np.array([1e200, 0.0]), r=18.001)
    with pytest.raises(ValueError, match="overflow"):
        cKDTree(np.array([[-FLOAT_MAX, 0.0]])).query_ball_point(np.array([FLOAT_MAX, 0.0]), r=0.045, p=INF)


@pytest.mark.parametrize("field", ["x_px", "y_px"])
@pytest.mark.parametrize("value", BOUNDARY_VALUES, ids=repr)
@pytest.mark.parametrize("snr", [5.0, 500.0], ids=["faintest", "brightest"])
def test_a_detection_at_any_boundary_float_gives_the_one_by_one_solution(field: str, value: float, snr: float) -> None:
    """Every boundary value pairs as the one-by-one rule pairs it. A position below 0 is one the record refuses, so
    no whole solution holds it: it reaches the pairing as a raw value. One the record takes gives the one-by-one
    solution, but for the two so far off the frame that the solver's verified WCS gives them no sky position."""
    wcs, pinned = _pinned_true_pixels()
    values = {"x_px": 100.0, "y_px": 10.0, "flux": 5.0, "snr": snr, field: value}
    if value < 0:
        with pytest.raises(ContractRefusal, match=f"A source detection's {field} is outside the contract: this number is below 0."):
            SourceDetection(**values, fwhm_px=2.0, centroid_uncertainty_px=0.1)
        bad = _raw_detection(**values)
    else:
        bad = SourceDetection(**values, fwhm_px=2.0, centroid_uncertainty_px=0.1)
    detections = list(pinned) + [bad]
    got = verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    want = _one_by_one_verify_held_out(wcs, detections, get_catalog_stars(), detections[:4])
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    if value < 0:
        return
    fast, slow = _solve_both_ways(detections, 1600, 1200, **ORION_MOMENT, **_sources("boundary-float-position"))
    assert fast == slow
    if value in BEYOND_THE_VERIFIED_WCS:
        assert fast[0] == NO_SKY_POSITION_FROM_THE_VERIFIED_WCS
    else:
        assert not fast[0].startswith("raised"), fast[0][:200]


# Outside the rule, which is written for Python floats: an int float() refuses, None, a string, a float32. The loops
# raise on the first three (OverflowError for the int, TypeError for the others) when a star is left to measure them
# against, and return when every star was taken before them
HUGE_INT = 10**400
NOT_PYTHON_FLOATS = [HUGE_INT, -HUGE_INT, None, "10.0"]


def _raised_or(call: Callable[[], Any], shown: Callable[[Any], Any]) -> Any:
    try:
        return shown(call())
    except Exception as exc:  # noqa: BLE001 (compared with the other run's, never hidden)
        return f"raised {type(exc).__name__}: {exc}"


@pytest.mark.parametrize("value", NOT_PYTHON_FLOATS, ids=["int-past-max", "int-past-minus-max", "none", "string"])
@pytest.mark.parametrize("field", ["x_px", "y_px"])
@pytest.mark.parametrize("place", ["first", "last"])
def test_a_value_that_is_not_a_python_float_pairs_or_raises_as_the_one_by_one_rules(value, field: str, place: str) -> None:
    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=0.05,
                   rotation_deg=10.0, k1=0.0, k2=0.0, parity=-1, width=1600, height=1200)
    stars = list(get_catalog_stars())
    pixels = [wcs.world_to_pixel(s.ra_deg, s.dec_deg) for s in stars]
    # A detection on every star in front of the camera, so the last detection finds every star taken. A star off the
    # frame's top or left lies below 0, and the odd value is no number at all: the record refuses both, so they are
    # handed to the pairings as raw values
    on_stars = [_raw_detection(x_px=x, y_px=y, flux=100.0, snr=50.0 - k) for k, (x, y) in enumerate(pixels) if math.isfinite(x)]
    odd = _raw_detection(**{"x_px": 10.0, "y_px": 10.0, "flux": 100.0, "snr": 1.0, field: value})
    detections = [odd] + on_stars if place == "first" else on_stars + [odd]
    held = lambda v: ([(id(d), s.id, r, t) for d, s, r, t in v.associations], v.inlier_gate_px)  # noqa: E731
    got = _raised_or(lambda: verify_held_out(wcs, detections, stars, on_stars[:2]), held)
    want = _raised_or(lambda: _one_by_one_verify_held_out(wcs, detections, stars, on_stars[:2]), held)
    assert got == want
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    pairs = lambda ps: [(id(d), s.id, r) for d, s, r in ps]  # noqa: E731
    got_pairs = _raised_or(lambda: sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0), pairs)
    want_pairs = _raised_or(lambda: _one_by_one_consensus_pairs(wcs, detections, stars, radius_px=18.0), pairs)
    assert got_pairs == want_pairs
    error = "raised OverflowError" if isinstance(value, int) else "raised TypeError"
    if place == "first":
        assert isinstance(want, str) and want.startswith(error), want
        assert isinstance(want_pairs, str) and want_pairs.startswith(error), want_pairs
    else:
        assert len(want[0]) == len(on_stars) and len(want_pairs) == len(on_stars)


@pytest.mark.parametrize("seed", range(10))
def test_float32_positions_far_from_the_origin_pair_as_the_one_by_one_rules(seed: int) -> None:
    # The loops measure a float32 position in float32 (`sx - det.x_px`); near 3e5 px one float32 step is 0.03 px, past
    # the vectorized projection's 1e-3 px margin, so detections 7.99 to 8 px from their star would be left out there
    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=3e5, crpix_y=3e5, scale_deg_per_px=0.05,
                   rotation_deg=10.0, k1=0.0, k2=0.0, parity=-1, width=1600, height=1200)
    stars = list(get_catalog_stars())
    rng = np.random.default_rng(seed)
    detections = []
    for k, star in enumerate(stars):
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if math.isfinite(x):
            angle, r = rng.uniform(0.0, 2.0 * math.pi), rng.uniform(7.99, 8.0)
            detections.append(SourceDetection(x_px=np.float32(x + r * math.cos(angle)), y_px=np.float32(y + r * math.sin(angle)),
                                              flux=100.0, snr=50.0 - k, fwhm_px=2.0, centroid_uncertainty_px=0.1))
    got = verify_held_out(wcs, detections, stars, detections[:2])
    want = _one_by_one_verify_held_out(wcs, detections, stars, detections[:2])
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    assert got.inlier_gate_px == want.inlier_gate_px
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    got_pairs = sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0)
    want_pairs = _one_by_one_consensus_pairs(wcs, detections, stars, radius_px=18.0)
    assert [(id(d), s.id, r) for d, s, r in got_pairs] == [(id(d), s.id, r) for d, s, r in want_pairs]


@pytest.mark.parametrize("field", ["x_px", "y_px"])
@pytest.mark.parametrize("snr", [5.0, 500.0], ids=["faintest", "brightest"])
def test_a_python_int_past_the_float_range_gives_the_one_by_one_solution(field: str, snr: float) -> None:
    """An int past the float range is no position a detection holds: the record refuses it, so no whole solution has
    one. The float32 positions the record takes give the one-by-one solution."""
    wcs, pinned = _pinned_true_pixels()
    on_frame = SourceDetection(x_px=100.0, y_px=10.0, flux=5.0, snr=snr, fwhm_px=2.0, centroid_uncertainty_px=0.1)
    with pytest.raises(ContractRefusal, match=f"A source detection's {field} is outside the contract: this number is beyond the largest a float holds."):
        replace(on_frame, **{field: HUGE_INT})
    whole_32 = [replace(d, x_px=np.float32(d.x_px), y_px=np.float32(d.y_px)) for d in list(pinned) + [on_frame]]
    fast, slow = _solve_both_ways(whole_32, 1600, 1200, **ORION_MOMENT, **_sources("float32-position"))
    assert fast == slow


def _tiny_scale_field(scale: float) -> tuple[WCSModel, list[CatalogStar], list[tuple[float, float]]]:
    """A WCS whose scale puts the pinned stars' pixels near 1e160 or beyond, and the stars' exact pixels."""
    wcs = WCSModel(crval_ra_deg=84.0, crval_dec_deg=6.0, crpix_x=800.0, crpix_y=600.0, scale_deg_per_px=scale,
                   rotation_deg=10.0, k1=0.0, k2=0.0, parity=-1, width=1600, height=1200)
    stars = list(get_catalog_stars())
    return wcs, stars, [wcs.world_to_pixel(s.ra_deg, s.dec_deg) for s in stars]


def test_a_star_projected_past_the_limit_pairs_as_the_one_by_one_rules_pair_it() -> None:
    # The loops pair a detection and a star beyond what a KD-tree holds when they are close: such values are compared
    # one by one, not dropped
    wcs, stars, pixels = _tiny_scale_field(1e-160)
    front = [k for k, (x, _) in enumerate(pixels) if math.isfinite(x) and abs(x) > INDEX_LIMIT]
    assert len(front) >= 6
    # Some of these pixels lie below 0, where the record refuses a position: all are handed over as raw values
    detections = [_raw_detection(x_px=pixels[k][0], y_px=pixels[k][1], flux=100.0 - n, snr=50.0 - n) for n, k in enumerate(front)]
    got = verify_held_out(wcs, detections, stars, detections[:2])
    want = _one_by_one_verify_held_out(wcs, detections, stars, detections[:2])
    assert [(id(d), s.id, r, t) for d, s, r, t in got.associations] == [(id(d), s.id, r, t) for d, s, r, t in want.associations]
    assert len(want.associations) == len(front)
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    got_pairs = [(id(d), s.id, r) for d, s, r in sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0)]
    assert got_pairs == [(id(d), s.id, r) for d, s, r in _one_by_one_consensus_pairs(wcs, detections, stars)]


@settings(max_examples=40, deadline=None)
@given(
    log_scale=st.floats(-323.0, 0.0),
    near=st.lists(st.tuples(st.integers(0, 17), st.floats(-20.0, 20.0), st.floats(-20.0, 20.0)), max_size=20),
    loose=st.lists(st.tuples(ANY_FLOAT, ANY_FLOAT), max_size=8),
)
# The stars' pixels on both sides of the limit at once, and a scale that sends them to infinity
@example(log_scale=-153.3, near=[(k, 0.5, -0.5) for k in range(18)], loose=[(PAST_LIMIT, INDEX_LIMIT)])
@example(log_scale=-323.0, near=[(k, 0.0, 0.0) for k in range(18)], loose=[(INF, INF), (FLOAT_MAX, -FLOAT_MAX)])
@example(log_scale=-2.0, near=[(k, 1.0, 1.0) for k in range(18)], loose=[(SUBNORMAL, -SUBNORMAL), (NAN, 3.0)])
# Both coordinates under sqrt(max) but a squared distance past it: a limit of sqrt(max) would let it in
@example(log_scale=-2.0, near=[(k, 0.5, 0.5) for k in range(18)], loose=[(1.2e154, 1.2e154)])
def test_pbt_detections_and_projections_at_any_float_pair_as_the_one_by_one_rules(log_scale, near, loose) -> None:
    wcs, stars, pixels = _tiny_scale_field(10.0**log_scale)
    # Positions at any float, most of which the record refuses (below 0, or no finite number): raw values
    detections = [_raw_detection(x_px=pixels[k][0] + dx, y_px=pixels[k][1] + dy, flux=10.0, snr=10.0) for k, dx, dy in near]
    detections += [_raw_detection(x_px=x, y_px=y, flux=10.0, snr=10.0) for x, y in loose]
    training = detections[::3]

    def held(fn):
        def call():
            v = fn(wcs, detections, stars, training)
            return [(id(d), s.id, r, t) for d, s, r, t in v.associations], v.inlier_gate_px

        return call

    assert _pairing_outcome(held(verify_held_out)) == _pairing_outcome(held(_one_by_one_verify_held_out))
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    got = _pairing_outcome(lambda: [(id(d), s.id, r) for d, s, r in sv._greedy_pairs(wcs, detections, stars, ra, dec, 18.0)])
    want = _pairing_outcome(lambda: [(id(d), s.id, r) for d, s, r in _one_by_one_consensus_pairs(wcs, detections, stars)])
    assert got == want


@settings(max_examples=80, deadline=None)
@given(ratios=st.lists(st.tuples(ANY_FLOAT, ANY_FLOAT), min_size=0, max_size=40), query=st.tuples(ANY_FLOAT, ANY_FLOAT))
# A finite first ratio with a non-finite second, and the other way round
@example(ratios=[(0.5, 0.5), (0.52, 0.9), (0.9, 0.5), (NAN, 0.1)], query=(0.5, NAN))
@example(ratios=[(0.5, 0.5), (0.52, 0.9), (0.9, 0.5), (0.1, NAN)], query=(0.5, INF))
@example(ratios=[(0.5, 0.5), (0.52, 0.9), (0.9, 0.5)], query=(NAN, 0.5))
# An index with no row the tree can hold, asked with a ratio it can
@example(ratios=[(NAN, 0.5), (0.5, INF), (PAST_LIMIT, 0.5), (0.5, -FLOAT_MAX)], query=(0.5, 0.5))
# Rows and queries at the limit and the next float past it, on each axis, and the largest floats of both signs
@example(ratios=[(INDEX_LIMIT, 0.5), (PAST_LIMIT, 0.5), (0.5, -INDEX_LIMIT), (0.5, -PAST_LIMIT), (0.5, 0.5)], query=(INDEX_LIMIT, 0.5))
@example(ratios=[(INDEX_LIMIT, 0.5), (PAST_LIMIT, 0.5), (0.5, -INDEX_LIMIT), (0.5, -PAST_LIMIT), (0.5, 0.5)], query=(PAST_LIMIT, 0.5))
@example(ratios=[(INDEX_LIMIT, 0.5), (PAST_LIMIT, 0.5), (0.5, -INDEX_LIMIT), (0.5, -PAST_LIMIT), (0.5, 0.5)], query=(0.5, -PAST_LIMIT))
@example(ratios=[(-FLOAT_MAX, 0.5), (0.5, 0.5), (SUBNORMAL, -SUBNORMAL)], query=(FLOAT_MAX, 0.5))
@example(ratios=[(-FLOAT_MAX, 0.5), (0.5, 0.5), (SUBNORMAL, -SUBNORMAL)], query=(0.0, 0.0))
def test_pbt_a_ratio_at_any_float_keeps_what_the_loop_kept(ratios, query) -> None:
    index = sv._RatioIndex(ratios)
    dr1, dr2 = query
    expected = [i for i, (r1, r2) in enumerate(ratios) if not (abs(dr1 - r1) > 0.045 or abs(dr2 - r2) > 0.045)]
    assert index.lookup(dr1, dr2).tolist() == expected


DETECTION_FIELDS = st.sampled_from(["x_px", "y_px", "flux", "snr"])


@settings(max_examples=25, deadline=None)
@given(
    keep=st.lists(st.integers(0, 16), min_size=6, max_size=17, unique=True),
    bad=st.lists(st.tuples(st.integers(0, 40), DETECTION_FIELDS, ANY_FLOAT), min_size=1, max_size=6),
    hint=st.booleans(),
    order_seed=st.integers(0, 2**16),
)
@example(keep=list(range(17)), bad=[(0, "x_px", INDEX_LIMIT)], hint=False, order_seed=0)
@example(keep=list(range(17)), bad=[(0, "x_px", PAST_LIMIT)], hint=False, order_seed=0)
@example(keep=list(range(17)), bad=[(16, "y_px", -PAST_LIMIT)], hint=True, order_seed=0)
@example(keep=list(range(17)), bad=[(16, "y_px", -INDEX_LIMIT)], hint=True, order_seed=0)
@example(keep=list(range(17)), bad=[(0, "x_px", 1e200), (1, "y_px", -1e200)], hint=False, order_seed=0)
@example(keep=list(range(17)), bad=[(16, "x_px", 1.2e154), (16, "y_px", 1.2e154)], hint=False, order_seed=0)
@example(keep=list(range(17)), bad=[(0, "y_px", FLOAT_MAX), (1, "y_px", -FLOAT_MAX)], hint=True, order_seed=0)
@example(keep=list(range(17)), bad=[(0, "x_px", SUBNORMAL), (1, "y_px", -SUBNORMAL), (2, "snr", SUBNORMAL)], hint=False, order_seed=0)
@example(keep=list(range(17)), bad=[(0, "snr", FLOAT_MAX), (1, "flux", -FLOAT_MAX), (16, "snr", -INF)], hint=True, order_seed=3)
@example(keep=list(range(17)), bad=[(16, "x_px", 1e200), (15, "x_px", NAN), (14, "y_px", INF)], hint=False, order_seed=0)
def test_pbt_detections_at_any_float_give_the_one_by_one_whole_solution(keep, bad, hint, order_seed) -> None:
    """The record takes a position that is finite and not below 0, and a flux and an SNR that are finite and above
    0, and refuses every other float: no whole solution holds one. With the values it takes, the fast search and the
    one-by-one rules give the same whole solution, or raise the same error."""
    # Detections sorted brightest first, so an index under 14 is in the triangles' pool and one above is not
    _, pinned = _pinned_true_pixels()
    detections = [pinned[i] for i in sorted(keep)]
    if order_seed:
        detections = [detections[i] for i in np.random.default_rng(order_seed).permutation(len(detections))]
    refused = False
    for index, name, value in bad:
        k = index % len(detections)
        if math.isfinite(value) and (value >= 0 if name in ("x_px", "y_px") else value > 0):
            detections[k] = replace(detections[k], **{name: value})
        else:
            with pytest.raises(ContractRefusal, match=f"A source detection's {name} is outside the contract"):
                replace(detections[k], **{name: value})
            refused = True
    if refused:
        return
    hint_kwargs = {"hint_ra_deg": 84.0, "hint_dec_deg": 6.0} if hint else {}
    (fast, fast_events), (slow, slow_events) = _solve_both_ways(
        detections, 1600, 1200, **ORION_MOMENT, **hint_kwargs, **_sources("any-float-detections")
    )
    assert fast == slow and fast_events == slow_events


def _as_json_value(value: float) -> float | str:
    """A finite float as a JSON number; NaN and the infinities as text, which the route refuses where a number
    goes."""
    return value if math.isfinite(value) else repr(value)


@settings(max_examples=20, deadline=None)
@given(
    bad=st.lists(st.tuples(st.integers(0, 17), st.sampled_from(["xPx", "yPx", "flux", "snr"]), ANY_FLOAT), min_size=1, max_size=4),
    as_text=st.booleans(),
    hint=st.booleans(),
)
@example(bad=[(17, "xPx", 1e200)], as_text=False, hint=False)
@example(bad=[(17, "xPx", 1e200)], as_text=True, hint=True)
@example(bad=[(0, "yPx", PAST_LIMIT)], as_text=False, hint=True)
@example(bad=[(0, "yPx", -INDEX_LIMIT), (1, "xPx", -PAST_LIMIT)], as_text=False, hint=False)
@example(bad=[(17, "xPx", FLOAT_MAX), (16, "yPx", -SUBNORMAL), (0, "snr", NAN)], as_text=False, hint=False)
def test_pbt_the_solve_endpoint_answers_the_one_by_one_solution_for_any_float(bad, as_text, hint) -> None:
    from fastapi.testclient import TestClient

    from services.api import app

    _, pinned = _pinned_true_pixels()
    rows = [
        {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px, "centroidUncertaintyPx": 0.1}
        for d in pinned
    ] + [{"xPx": 100.0, "yPx": 10.0, "flux": 5.0, "snr": 5.0, "fwhmPx": 2.0, "centroidUncertaintyPx": 0.1}]
    for index, key, value in bad:
        rows[index][key] = repr(value) if as_text else _as_json_value(value)
    sha = hashlib.sha256(b"any-float").hexdigest()
    body = {
        "width": 1600, "height": 1200, "observedAtUTC": ORION_MOMENT["obs_utc_iso"], "latDeg": 30.0, "lonDeg": -40.0,
        "sourceAssetId": f"src_{sha[:16]}", "sourceSha256": sha, "detections": rows,
        **({"hintRaDeg": 84.0, "hintDecDeg": 6.0} if hint else {}),
    }
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    fast = client.post("/api/science/solve", json=body)
    with pytest.MonkeyPatch.context() as mp:
        _use_one_by_one_rules(mp)
        slow = client.post("/api/science/solve", json=body)
    assert (fast.status_code, fast.text) == (slow.status_code, slow.text)


# =============================================================================================
# What the search hands the fast pieces: main's detections, catalog, triangles and radius
# =============================================================================================


def _main_catalog_triangle_ratios(catalog: list[CatalogStar]) -> list[tuple[float, float]]:
    """The catalog triangles' side ratios as the search before solver-10k-a built them, written out here: the 18
    brightest stars, sides from 2.5 to 62 deg, sides sorted longest first."""
    pool = sorted(catalog, key=lambda s: s.mag)[:18]
    ratios = []
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            for k in range(j + 1, len(pool)):
                a, b, c = pool[i], pool[j], pool[k]
                sides = [angular_separation_deg(a.ra_deg, a.dec_deg, b.ra_deg, b.dec_deg),
                         angular_separation_deg(b.ra_deg, b.dec_deg, c.ra_deg, c.dec_deg),
                         angular_separation_deg(a.ra_deg, a.dec_deg, c.ra_deg, c.dec_deg)]
                ordered = sorted(sides)
                if ordered[0] < 2.5 or ordered[2] > 62.0:
                    continue
                longest_first = sorted(sides, reverse=True)
                ratios.append((longest_first[1] / longest_first[0], longest_first[2] / longest_first[0]))
    return ratios


@pytest.mark.parametrize("catalog_name", ["pinned", "all-sky"])
def test_the_search_hands_the_fast_pieces_main_s_detections_catalog_triangles_and_radius(
    catalog_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one-by-one path takes what these calls are given, so this pins what they are given: every catalog triangle
    # with its own ratios, the detections without hot pixels, the whole catalog with its positions, 18 px
    _, pinned = _pinned_true_pixels()
    hot = [SourceDetection(x_px=300.0 + 40.0 * k, y_px=200.0, flux=5000.0, snr=900.0, fwhm_px=0.6,
                           centroid_uncertainty_px=0.1, peak_to_total_ratio=0.9) for k in range(3)]
    detections = hot + list(pinned)
    assert all(d.is_hot_pixel_impulse for d in hot) and not any(d.is_hot_pixel_impulse for d in pinned)
    catalog = list(get_catalog_stars()) if catalog_name == "pinned" else list(get_all_sky_catalog_stars())
    built: list[list[tuple[float, float]]] = []
    calls: list[tuple] = []
    real_index, real_pairs = sv._RatioIndex.__init__, sv._greedy_pairs

    def spy_index(self, ratios):
        built.append([tuple(r) for r in ratios])
        real_index(self, ratios)

    def spy_pairs(wcs, dets, stars, ra_deg, dec_deg, radius_px):
        calls.append(([id(d) for d in dets], [s.id for s in stars], np.array(ra_deg).tolist(), np.array(dec_deg).tolist(), radius_px))
        return real_pairs(wcs, dets, stars, ra_deg, dec_deg, radius_px)

    monkeypatch.setattr(sv._RatioIndex, "__init__", spy_index)
    monkeypatch.setattr(sv, "_greedy_pairs", spy_pairs)
    solve_and_verify_field(detections, 1600, 1200, catalog_stars=catalog, **ORION_MOMENT, **_sources("hands-the-pieces"))
    assert built == [_main_catalog_triangle_ratios(catalog)]
    assert calls
    want = ([id(d) for d in pinned], [s.id for s in catalog], [s.ra_deg for s in catalog], [s.dec_deg for s in catalog], 18.0)
    for call in calls:
        assert call == want


# What the route answers each form with. A number written as text is no number to the detection record: 422 and its
# sentence. The 200-digit integer is a number the record takes, far off the frame: the solver's verified WCS gives it
# no sky position, the finding record refuses that, and the route answers 500 for a fault inside the solve. That one
# is held by name as it is today: the solver's own to change, by refusing such a detection before it solves
@pytest.mark.parametrize(
    ("raw", "status"),
    [("1e200", 422), (" 1e200 ", 422), (10**200, 500), ("Infinity", 422), ("-1.7976931348623157e308", 422), (1.2e154, 200)],
    ids=["text-1e200", "text-spaced", "200-digit-integer", "text-Infinity", "text-minus-max", "both-axes-1.2e154"],
)
def test_the_solve_endpoint_answers_the_one_by_one_solution_for_huge_values_in_any_json_form(raw, status: int) -> None:
    from fastapi.testclient import TestClient

    from services.api import app

    _, pinned = _pinned_true_pixels()
    rows = [{"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px, "centroidUncertaintyPx": 0.1}
            for d in pinned]
    rows.append({"xPx": raw, "yPx": raw if isinstance(raw, float) else 10.0, "flux": 5.0, "snr": 5.0, "fwhmPx": 2.0, "centroidUncertaintyPx": 0.1})
    sha = hashlib.sha256(b"huge").hexdigest()
    body = {
        "width": 1600, "height": 1200, "observedAtUTC": ORION_MOMENT["obs_utc_iso"], "latDeg": 30.0, "lonDeg": -40.0,
        "sourceAssetId": f"src_{sha[:16]}", "sourceSha256": sha, "detections": rows,
    }
    client = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    fast = client.post("/api/science/solve", json=body)
    with pytest.MonkeyPatch.context() as mp:
        _use_one_by_one_rules(mp)
        slow = client.post("/api/science/solve", json=body)
    assert (fast.status_code, fast.text) == (slow.status_code, slow.text)
    assert fast.status_code == status
    if status == 422:
        assert fast.json() == {"detail": "A source detection's x_px is outside the contract: this value is not a number."}
    elif status == 200:
        assert fast.json()["status"] == "calibrated"
