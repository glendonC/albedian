"""The plate search's consensus pairing reads its reach from one KD-tree of the search's detections (pair-reach-tree-1).

Main paired every candidate WCS through a dense distance matrix: every detection against every catalog star, made
again for each candidate, which was nearly all of a plate solve on the public photos. The reach (the stars each
detection measures with `world_to_pixel`) now comes from a KD-tree of the detections made once with their arrays
(`_PairingDetections`), asked a millionth of a pixel past the reach; the pairs it finds are measured with main's own
expression and kept by main's own test, so every reach and every pair is main's.

Covers:
- The reach equals main's dense reach (main's lines written out here) for stars at every float from eight units in
  the last place inside the reach to eight outside it, on sixteen directions from a detection at the origin and from
  one in the frame, at the reaches the search and the held-out check use and at the tree's own limit.
- PBT: the reach equals main's dense reach with stars a few units in the last place either side of the reach, at 0,
  subnormals, NaN and the infinities, at and past the index limit and at the largest float, on duplicates, with
  detections the record refuses as raw values (an int past the float range, a float32, a string, None) and with
  reaches from below 0 to past the tree's limit, NaN and infinite.
- PBT: a candidate's pairs equal main's dense pairing (written out here) on random candidates over a dense made-up
  field with twin stars and duplicate detections, at radii of 18, 8 and 3 px, through the search's arrays and
  through a plain list.
- The search makes one KD-tree of its detections and no detections-by-stars matrix, and its whole solution is the
  one main's dense pairing gives.

Every field here is made up: positions drawn by a seeded generator, the renderer's default WCS, and the pinned
stars rendered by `render_synthetic_starfield` (simulated, never a recording). The moment and place are the
fixtures' synthetic open North Atlantic ones.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import sys
from typing import Any

from hypothesis import HealthCheck, example, given, settings, strategies as st
import numpy as np
import pytest

from services.science import solver as sv
from services.science.catalog import get_all_sky_catalog_stars
from services.science.solver import solve_and_verify_field
from tests.science.test_triangle_dense_fields import _orion_wcs_and_stars, _outcome, _pinned_field, _sources

LIMIT = sv._INDEX_LIMIT
SPECIAL = [0.0, -0.0, 5e-324, -5e-324, 1e-300, math.nan, math.inf, -math.inf, LIMIT, -LIMIT,
           float(np.nextafter(LIMIT, math.inf)), float(np.nextafter(-LIMIT, -math.inf)), 1e200, -1e200,
           sys.float_info.max, -sys.float_info.max, 1e6, 1e154]
REFUSED: list[Any] = [10**400, -(10**400), np.float32(3.5), "3.5", None]
# The largest reach the tree is asked with (`_PAIR_TREE_REACH_LIMIT_PX`); past it the pairing is main's dense one
TREE_REACH_LIMIT_PX = 1e6
REACHES = [18.0 + sv._PREFILTER_MARGIN_PX, sv.ASSOCIATION_RADIUS_PX + sv._PREFILTER_MARGIN_PX, 3.0 + sv._PREFILTER_MARGIN_PX,
           sv._PREFILTER_MARGIN_PX, 0.0, -1.0, 1e-300, TREE_REACH_LIMIT_PX,
           float(np.nextafter(TREE_REACH_LIMIT_PX, math.inf)), 1e7, 1e12, math.inf, math.nan]


@dataclass(frozen=True)
class _At:
    """A detection's position and nothing else: all the pairing's arrays read from a detection."""

    x_px: Any
    y_px: Any


def _main_reach(detections: sv._PairingDetections, px: np.ndarray, py: np.ndarray, reach_px: float) -> list[tuple[int, list[int]]]:
    """Main's reach (`_greedy_pairs` before pair-reach-tree-1): the dense detections-by-stars distances, a pair in
    reach when its distance is finite and below the reach, every star in reach of a detection the record refuses;
    per detection with any star in reach, in order, its stars in order."""
    with np.errstate(invalid="ignore", over="ignore"):
        dist = np.hypot(px[None, :] - detections.x[:, None], py[None, :] - detections.y[:, None])
    close = np.isfinite(dist) & (dist < reach_px)
    close[detections.x_refused | detections.y_refused, :] = True
    return [(int(i), np.flatnonzero(close[i]).tolist()) for i in np.flatnonzero(close.any(axis=1))]


def _reach(detections: sv._PairingDetections, px: np.ndarray, py: np.ndarray, reach_px: float) -> list[tuple[int, list[int]]]:
    return [(int(i), [int(j) for j in js]) for i, js in sv._pairing_reach(detections, px, py, reach_px)]


def _main_greedy_pairs(wcs, detections, stars, ra_deg, dec_deg, radius_px):
    """Main's `_greedy_pairs` before pair-reach-tree-1, line for line: the dense reach, then each detection in turn
    takes the nearest star not yet taken that `world_to_pixel` puts closer than the radius."""
    if not detections or len(stars) == 0:
        return []
    px, py = sv._project(wcs, ra_deg, dec_deg)
    if isinstance(detections, sv._PairingDetections):
        dx, x_refused, dy, y_refused = detections.x, detections.x_refused, detections.y, detections.y_refused
    else:
        dx, x_refused = sv._coordinates([d.x_px for d in detections])
        dy, y_refused = sv._coordinates([d.y_px for d in detections])
    with np.errstate(invalid="ignore", over="ignore"):
        dist = np.hypot(px[None, :] - dx[:, None], py[None, :] - dy[:, None])
    close = np.isfinite(dist) & (dist < radius_px + sv._PREFILTER_MARGIN_PX)
    close[x_refused | y_refused, :] = True
    ids = [s.id for s in stars]
    used: set[str] = set()
    exact: dict[int, tuple[float, float]] = {}
    pairs = []
    for i in np.flatnonzero(close.any(axis=1)):
        det = detections[i]
        best_j, best_d = -1, radius_px
        for j in np.flatnonzero(close[i]):
            if ids[j] in used:
                continue
            if j not in exact:
                exact[j] = wcs.world_to_pixel(stars[j].ra_deg, stars[j].dec_deg)
            sx, sy = exact[j]
            if math.isnan(sx):
                continue
            d = math.hypot(sx - det.x_px, sy - det.y_px)
            if d < best_d:
                best_j, best_d = int(j), d
        if best_j >= 0:
            used.add(ids[best_j])
            pairs.append((det, stars[best_j], best_d))
    return pairs


def _ulps(value: float, steps: int) -> float:
    """`value` moved `steps` floats up (or down, when negative)."""
    toward = math.inf if steps > 0 else -math.inf
    with np.errstate(over="ignore"):
        for _ in range(abs(steps)):
            value = float(np.nextafter(value, toward))
    return value


@pytest.mark.parametrize("reach_px", [18.0 + sv._PREFILTER_MARGIN_PX, sv.ASSOCIATION_RADIUS_PX + sv._PREFILTER_MARGIN_PX,
                                      3.0 + sv._PREFILTER_MARGIN_PX, TREE_REACH_LIMIT_PX])
@pytest.mark.parametrize("origin", [(0.0, 0.0), (1000.25, 700.5)])
def test_stars_from_eight_floats_inside_the_reach_to_eight_outside_are_in_main_s_reach(reach_px: float, origin) -> None:
    # Sixteen directions; on each, the star's x and y each moved from eight floats down to eight up: 4,624 stars a
    # detection, many of them one float either side of main's test
    x0, y0 = origin
    px, py = [], []
    for k in range(16):
        t = 2.0 * math.pi * k / 16.0
        for sx in range(-8, 9):
            for sy in range(-8, 9):
                px.append(_ulps(x0 + reach_px * math.cos(t), sx))
                py.append(_ulps(y0 + reach_px * math.sin(t), sy))
    detections = sv._PairingDetections([_At(x0, y0), _At(x0, y0)])
    px_a, py_a = np.array(px), np.array(py)
    want = _main_reach(detections, px_a, py_a, reach_px)
    assert want and 0 < len(want[0][1]) < len(px)
    assert _reach(detections, px_a, py_a, reach_px) == want


_COORD = st.one_of(st.floats(-50.0, 2050.0), st.sampled_from(SPECIAL), st.floats(allow_nan=True, allow_infinity=True))
_DETECTION = st.one_of(st.tuples(_COORD, _COORD), st.tuples(st.sampled_from(REFUSED), _COORD), st.tuples(_COORD, st.sampled_from(REFUSED)))


@st.composite
def _search(draw) -> tuple[list[_At], np.ndarray, np.ndarray, float]:
    """Detections, star projections and a reach. Some stars sit a few floats either side of the reach from a drawn
    detection (`edge`), some are copies of other stars or of a detection's own position, the rest any float."""
    reach_px = draw(st.one_of(st.sampled_from(REACHES), st.floats(0.0, 100.0)))
    positions = draw(st.lists(_DETECTION, min_size=1, max_size=30))
    positions += draw(st.lists(st.sampled_from(positions), max_size=4))
    px, py = [], []
    for k, t, sx, sy in draw(st.lists(st.tuples(st.integers(0, len(positions) - 1), st.floats(0.0, 2.0 * math.pi),
                                                st.integers(-4, 4), st.integers(-4, 4)), max_size=30)):
        x0, y0 = positions[k]
        if isinstance(x0, float) and isinstance(y0, float) and math.isfinite(reach_px):
            px.append(_ulps(x0 + reach_px * math.cos(t), sx))
            py.append(_ulps(y0 + reach_px * math.sin(t), sy))
    for x, y in draw(st.lists(st.tuples(_COORD, _COORD), max_size=20)):
        px.append(x)
        py.append(y)
    for k in draw(st.lists(st.integers(0, 10**6), max_size=4)):
        if px:
            px.append(px[k % len(px)])
            py.append(py[k % len(py)])
    for k in draw(st.lists(st.integers(0, len(positions) - 1), max_size=3)):
        x0, y0 = positions[k]
        if isinstance(x0, float) and isinstance(y0, float):
            px.append(x0)
            py.append(y0)
    if not px:
        px, py = [0.0], [0.0]
    return [_At(x, y) for x, y in positions], np.array(px, dtype=np.float64), np.array(py, dtype=np.float64), reach_px


@settings(max_examples=300, deadline=None)
@given(search=_search())
# A detection at the index limit and a star on it; one past the limit and a star 1 px from it (both compared as main
# compared them); a star past the limit beside detections in the frame; and a refused detection, which reaches all
@example(search=([_At(LIMIT, LIMIT), _At(10.0, 10.0)], np.array([LIMIT, 10.0 + 18.0005]), np.array([LIMIT, 10.0]), 18.001))
@example(search=([_At(1e200, -1e200), _At(np.float32(3.5), 1.0)], np.array([1e200 + 1e184, 3.5]), np.array([-1e200, 1.0]), 18.001))
@example(search=([_At(5.0, 5.0), _At(6.0, 5.0)], np.array([1e200, 5.0, math.nan]), np.array([5.0, 23.0, 5.0]), 18.001))
@example(search=([_At(0.0, 0.0)], np.array([18.001, _ulps(18.001, -1), 0.0]), np.array([0.0, 0.0, 18.001]), 18.001))
def test_pbt_the_reach_is_main_s_dense_reach(search) -> None:
    positions, px, py, reach_px = search
    detections = sv._PairingDetections(positions)
    assert _reach(detections, px, py, reach_px) == _main_reach(detections, px, py, reach_px)


def _dense_made_up_field(seed: int, extra: int, copies: int) -> tuple[Any, list, list]:
    """The renderer's default WCS, 330 made-up stars (every tenth with a twin at its place), detections at the
    in-frame stars' pixels moved by up to 25 px, `extra` detections anywhere in the 960 x 540 frame and `copies`
    detections repeated at another's place."""
    wcs, stars = _orion_wcs_and_stars()
    rng = np.random.default_rng(seed)
    near = []
    for star in stars:
        x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        if not math.isnan(x) and 0 <= x < 960 and 0 <= y < 540:
            near.append(_At(float(x + rng.uniform(-25.0, 25.0)), float(y + rng.uniform(-25.0, 25.0))))
    detections = near + [_At(float(x), float(y)) for x, y in zip(rng.uniform(0, 960, extra), rng.uniform(0, 540, extra))]
    detections += [detections[int(k)] for k in rng.integers(0, len(detections), copies)]
    return wcs, stars, [detections[int(k)] for k in rng.permutation(len(detections))]


@pytest.fixture
def monkeypatch_project():
    """Sets the vectorized projection off by (dx, dy) px for the rest of one example, then puts it back."""
    real = sv._project
    with pytest.MonkeyPatch.context() as mp:
        def shifted(shift):
            mp.setattr(sv, "_project", lambda *args: tuple(a + d for a, d in zip(real(*args), shift)))
        yield shifted


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    seed=st.integers(0, 2**16),
    extra=st.integers(0, 400),
    copies=st.integers(0, 20),
    radius=st.sampled_from([18.0, 8.0, 3.0]),
    candidates=st.lists(st.tuples(st.floats(-60.0, 60.0), st.floats(-60.0, 60.0), st.floats(-10.0, 10.0), st.floats(0.8, 1.25)),
                        min_size=1, max_size=4),
    shift=st.one_of(st.just((0.0, 0.0)), st.tuples(st.floats(-0.0007, 0.0007), st.floats(-0.0007, 0.0007))),
)
@example(seed=0, extra=300, copies=10, radius=18.0, candidates=[(0.0, 0.0, 0.0, 1.0), (5.0, -3.0, 2.0, 1.1)], shift=(0.0, 0.0))
@example(seed=0, extra=300, copies=10, radius=18.0, candidates=[(0.0, 0.0, 0.0, 1.0)], shift=(0.0007, -0.0007))
def test_pbt_a_candidate_s_pairs_are_main_s_dense_pairs(seed, extra, copies, radius, candidates, shift, monkeypatch_project) -> None:
    # With `shift`, the vectorized projection lands up to 0.0007 px off `world_to_pixel` on each axis (another
    # platform's trigonometry, within the prefilter's margin), in both pairings alike
    monkeypatch_project(shift)
    wcs, stars, detections = _dense_made_up_field(seed, extra, copies)
    ra = np.array([s.ra_deg for s in stars])
    dec = np.array([s.dec_deg for s in stars])
    once = sv._PairingDetections(detections)
    for dx, dy, turn, zoom in candidates:
        candidate = replace(wcs, crpix_x=wcs.crpix_x + dx, crpix_y=wcs.crpix_y + dy, rotation_deg=wcs.rotation_deg + turn,
                            scale_deg_per_px=wcs.scale_deg_per_px * zoom)
        want = [(id(d), s.id, dist) for d, s, dist in _main_greedy_pairs(candidate, detections, stars, ra, dec, radius)]
        assert [(id(d), s.id, dist) for d, s, dist in sv._greedy_pairs(candidate, once, stars, ra, dec, radius)] == want
        assert [(id(d), s.id, dist) for d, s, dist in sv._greedy_pairs(candidate, list(detections), stars, ra, dec, radius)] == want


def test_the_search_pairs_through_one_tree_of_its_detections_and_no_dense_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Main measured every detection against every catalog star for each candidate; the search now asks one KD-tree
    # of its detections, made once, and its solution is the one main's dense pairing gives, to the byte
    detections, width, height, moment = _pinned_field()
    psf = [d for d in detections if not d.is_hot_pixel_impulse]
    catalog = get_all_sky_catalog_stars()
    want_points = np.column_stack([[d.x_px for d in psf], [d.y_px for d in psf]])

    def solve() -> str:
        return _outcome(lambda: solve_and_verify_field(list(detections), width, height, catalog_stars=catalog, **moment,
                                                       **_sources("pair-reach-tree")))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sv, "_greedy_pairs", _main_greedy_pairs)
        want = solve()

    trees, measured, pairing = [], [], []
    real_tree, real_hypot, real_pairs = sv.cKDTree, np.hypot, sv._greedy_pairs

    def spy_tree(data, *args, **kwargs):
        data = np.asarray(data)
        trees.append(data.shape == want_points.shape and bool((data == want_points).all()))
        return real_tree(data, *args, **kwargs)

    def spy_hypot(*args, **kwargs):
        out = real_hypot(*args, **kwargs)
        if pairing:
            measured.append(np.size(out))
        return out

    def spy_pairs(*args):
        pairing.append(1)
        try:
            return real_pairs(*args)
        finally:
            pairing.pop()

    monkeypatch.setattr(sv, "cKDTree", spy_tree)
    monkeypatch.setattr(np, "hypot", spy_hypot)
    monkeypatch.setattr(sv, "_greedy_pairs", spy_pairs)
    got = solve()
    assert got == want
    assert trees.count(True) == 1
    assert measured and max(measured) * 4 < len(psf) * len(catalog), (max(measured), len(psf), len(catalog))


@pytest.mark.parametrize("radius", [18.0, 8.0, 3.0])
def test_a_star_a_hair_inside_the_radius_pairs_when_the_projection_lands_a_hair_past_it(radius: float, monkeypatch_project) -> None:
    # The tree is asked at the radius plus the prefilter's margin: a star `world_to_pixel` puts 0.0004 px inside the
    # radius, whose vectorized projection lands 0.0007 px farther (another platform's trigonometry), pairs as in main
    wcs, stars = _orion_wcs_and_stars()
    star = next(s for s in stars if 100 < wcs.world_to_pixel(s.ra_deg, s.dec_deg)[0] < 800 and 100 < wcs.world_to_pixel(s.ra_deg, s.dec_deg)[1] < 440)
    x, y = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
    detections = [_At(x + radius - 0.0004, y)]
    monkeypatch_project((-0.0007, 0.0))
    ra, dec = np.array([star.ra_deg]), np.array([star.dec_deg])
    got = sv._greedy_pairs(wcs, sv._PairingDetections(detections), [star], ra, dec, radius)
    assert [(s.id, d) for _, s, d in got] == [(s.id, d) for _, s, d in _main_greedy_pairs(wcs, detections, [star], ra, dec, radius)]
    assert len(got) == 1 and got[0][2] < radius
