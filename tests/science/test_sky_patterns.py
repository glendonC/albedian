"""Four-star sky patterns after Brown and Stubis (2017), and their catalogue from the shipped star tiles.

The key, the canonical labelling and the lookup are checked against a brute-force reading of the definition over
all 24 labellings of four stars; the catalogue against its caps; the lookup at the edges of its tolerance; and the
catalogue built from the real Gaia DR3 and Hipparcos tiles against a made-up camera's view of it.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from hypothesis import assume, example, given, settings, strategies as st

from services.science import sky_patterns as sp

PERMUTATIONS = np.array(list(itertools.permutations(range(4))), dtype=np.int64)


# ------------------------------------------------------------------------------------------------
# A reading of the definition, independent of the module
# ------------------------------------------------------------------------------------------------


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _plane(dirs: np.ndarray) -> np.ndarray:
    """Gnomonic points of four directions about their mean direction, in any fixed basis of its tangent plane."""
    c = _unit(dirs.sum(axis=0))
    helper = np.array([0.0, 0.0, 1.0]) if abs(c[2]) < 0.5 else np.array([1.0, 0.0, 0.0])
    e1 = _unit(np.cross(helper, c))
    e2 = np.cross(c, e1)
    return np.array([[d @ e1 / (d @ c), d @ e2 / (d @ c)] for d in dirs])


def _labelled(points: np.ndarray, order) -> tuple[np.ndarray, float, float]:
    """(x'_k, y'_k, x'_l, y'_l), the axis length, and the longest edge, for the labelling (a, b, k, l)."""
    a, b, k, l = (points[i] for i in order)
    axis = b - a
    length = float(np.hypot(*axis))
    u = axis / length
    w = np.array([-u[1], u[0]])
    mid = 0.5 * (a + b)
    key = np.array([(k - mid) @ u, (k - mid) @ w, (l - mid) @ u, (l - mid) @ w]) / length
    longest = max(float(np.hypot(*(points[i] - points[j]))) for i, j in itertools.combinations(range(4), 2))
    return key, length, longest


def _canonical_reference(dirs: np.ndarray) -> tuple[tuple[int, ...], np.ndarray, float]:
    points = _plane(dirs)
    found = []
    for order in PERMUTATIONS:
        key, length, longest = _labelled(points, order)
        if length == longest and key[0] <= key[2] and key[0] + key[2] >= 0.0:
            found.append((tuple(int(i) for i in order), key, length))
    assert len(found) == 1, f"the reference found {len(found)} canonical labellings"
    return found[0]


def _admissible_reference(dirs: np.ndarray, tau: float) -> set[tuple[int, ...]]:
    points = _plane(dirs)
    out = set()
    for order in PERMUTATIONS:
        key, length, longest = _labelled(points, order)
        if key[0] - key[2] <= 2.0 * tau and -(key[0] + key[2]) <= 2.0 * tau and longest <= (1.0 + 3.0 * tau) * length:
            out.add(tuple(int(i) for i in order))
    return out


def _rotation(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def _margins(dirs: np.ndarray) -> float:
    """How far the pattern is from any tie of the canonical rule (0 at a tie)."""
    points = _plane(dirs)
    edges = sorted(float(np.hypot(*(points[i] - points[j]))) for i, j in itertools.combinations(range(4), 2))
    gap = 1.0 - edges[-2] / edges[-1]
    best = min(
        (abs(_labelled(points, o)[0][2] - _labelled(points, o)[0][0]), abs(_labelled(points, o)[0][0] + _labelled(points, o)[0][2]))
        for o in PERMUTATIONS
        if _labelled(points, o)[1] == edges[-1]
    )
    return min(gap, *best)


coordinate = st.floats(min_value=-0.35, max_value=0.35, allow_nan=False, allow_infinity=False)
quaternion = st.tuples(*(st.floats(min_value=-1.0, max_value=1.0, allow_nan=False) for _ in range(4))).filter(
    lambda q: sum(c * c for c in q) > 1e-3
)


@st.composite
def patterns(draw) -> np.ndarray:
    """Four directions: four tangent-plane points about +z (up to 0.35 rad out), turned by a random rotation."""
    xy = np.array([[draw(coordinate), draw(coordinate)] for _ in range(4)])
    pairwise = [float(np.hypot(*(xy[i] - xy[j]))) for i, j in itertools.combinations(range(4), 2)]
    assume(min(pairwise) > 2e-3)
    dirs = _unit(np.column_stack([xy, np.ones(4)]))
    return dirs @ _rotation(np.array(draw(quaternion))).T


# ------------------------------------------------------------------------------------------------
# Field classes
# ------------------------------------------------------------------------------------------------


def test_the_field_classes_are_the_six_of_the_preparation_with_ten_stars_per_cap() -> None:
    assert [fc.diagonal_fov_deg for fc in sp.FIELD_CLASSES] == [20.0, 30.0, 48.5, 63.4, 84.0, 120.0]
    assert sp.STARS_PER_CAP == 10
    assert sp.MAX_KEY_TOLERANCE == 0.04
    assert sp.MIN_CAP_RADIUS_DEG == 1.0
    for fc in sp.FIELD_CLASSES:
        r_in = math.degrees(math.atan(math.tan(math.radians(fc.diagonal_fov_deg) / 2.0) * 0.6))
        assert fc.inscribed_radius_deg == pytest.approx(r_in, rel=1e-12)
        assert fc.cap_radius_deg == pytest.approx(r_in / 2.0, rel=1e-12)


# ------------------------------------------------------------------------------------------------
# Keys
# ------------------------------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(dirs=patterns())
def test_the_key_is_the_papers_coordinates_in_its_one_canonical_labelling(dirs: np.ndarray) -> None:
    assume(_margins(dirs) > 1e-6)
    order, key, length = _canonical_reference(dirs)
    got = sp.pattern_keys(dirs[None])
    assert tuple(int(i) for i in got.order[0]) == order
    np.testing.assert_allclose(got.shape[0], key, rtol=0.0, atol=1e-9)
    assert got.axis_rad[0] == pytest.approx(length, rel=1e-9)


@settings(max_examples=150, deadline=None)
@given(dirs=patterns(), turn=quaternion, perm=st.permutations(range(4)))
def test_the_key_does_not_change_when_the_sky_turns_or_the_stars_are_listed_in_another_order(
    dirs: np.ndarray, turn: tuple, perm: list
) -> None:
    assume(_margins(dirs) > 1e-6)
    base = sp.pattern_keys(dirs[None])
    moved = sp.pattern_keys((dirs[list(perm)] @ _rotation(np.array(turn)).T)[None])
    np.testing.assert_allclose(moved.shape[0], base.shape[0], rtol=0.0, atol=1e-9)
    assert moved.axis_rad[0] == pytest.approx(base.axis_rad[0], rel=1e-9)
    # The same stars in the same roles, wherever the list put them
    assert [perm[i] for i in moved.order[0]] == list(base.order[0])


@settings(max_examples=100, deadline=None)
@given(dirs=patterns())
def test_a_mirrored_pattern_has_the_mirrored_key_so_a_mirror_image_of_the_sky_is_not_the_sky(dirs: np.ndarray) -> None:
    assume(_margins(dirs) > 1e-6)
    base = sp.pattern_keys(dirs[None])
    mirrored = sp.pattern_keys((dirs * np.array([1.0, 1.0, -1.0]))[None])
    flipped = base.shape[0] * np.array([1.0, -1.0, 1.0, -1.0])
    np.testing.assert_allclose(mirrored.shape[0], flipped, rtol=0.0, atol=1e-9)
    if max(abs(base.shape[0][1]), abs(base.shape[0][3])) > 1e-3:
        assert np.abs(mirrored.shape[0] - base.shape[0]).max() > 1e-3


def test_keys_are_computed_for_many_patterns_at_once_as_for_each_alone() -> None:
    rng = np.random.default_rng(3)
    many = np.stack([_unit(np.column_stack([rng.uniform(-0.2, 0.2, (4, 2)), np.ones(4)])) for _ in range(50)])
    together = sp.pattern_keys(many)
    for i in range(50):
        alone = sp.pattern_keys(many[i : i + 1])
        np.testing.assert_allclose(alone.shape[0], together.shape[i], rtol=0.0, atol=1e-12)
        np.testing.assert_array_equal(alone.order[0], together.order[i])
        assert alone.axis_rad[0] == pytest.approx(together.axis_rad[i], rel=1e-12)


# ------------------------------------------------------------------------------------------------
# Query keys: every labelling that noise inside the tolerance could have made canonical
# ------------------------------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(dirs=patterns(), tau=st.floats(min_value=1e-6, max_value=0.04))
@example(  # a square: every rule ties
    dirs=_unit(np.array([[0.1, 0.1, 1.0], [-0.1, 0.1, 1.0], [-0.1, -0.1, 1.0], [0.1, -0.1, 1.0]])), tau=0.01
)
def test_the_query_labellings_are_exactly_those_the_slack_rules_admit(dirs: np.ndarray, tau: float) -> None:
    got = sp.query_keys(dirs[None], tau)
    assert set(got.pattern.tolist()) == {0}
    orders = {tuple(int(i) for i in o) for o in got.order}
    assert orders == _admissible_reference(dirs, tau)
    points = _plane(dirs)
    for o, key, edge in zip(got.order, got.shape, got.axis_rad):
        ref_key, ref_len, _ = _labelled(points, o)
        np.testing.assert_allclose(key, ref_key, rtol=0.0, atol=1e-9)
        assert edge == pytest.approx(ref_len, rel=1e-9)


@settings(max_examples=150, deadline=None)
@given(dirs=patterns(), noise=st.floats(min_value=1e-7, max_value=4e-3), seed=st.integers(0, 2**32 - 1))
def test_noise_inside_the_tolerance_always_leaves_the_stored_labelling_among_the_query_keys(
    dirs: np.ndarray, noise: float, seed: int
) -> None:
    assume(_margins(dirs) > 1e-9)
    stored = sp.pattern_keys(dirs[None])
    rng = np.random.default_rng(seed)
    seen = _unit(dirs + rng.normal(0.0, noise, dirs.shape))
    true_order = stored.order[0]
    seen_key, _, _ = _labelled(_plane(seen), true_order)
    tau = float(np.abs(seen_key - stored.shape[0]).max()) * (1.0 + 1e-9) + 1e-12
    assume(tau <= sp.MAX_KEY_TOLERANCE)
    got = sp.query_keys(seen[None], tau)
    hits = [i for i, o in enumerate(got.order) if list(o) == list(true_order)]
    assert hits, "the stored labelling is not among the query labellings"
    assert np.abs(got.shape[hits[0]] - stored.shape[0]).max() <= tau


def test_a_pattern_far_from_every_tie_is_looked_up_once() -> None:
    dirs = _unit(np.array([[0.0, 0.0, 1.0], [0.3, 0.0, 1.0], [0.21, 0.07, 1.0], [0.05, -0.04, 1.0]]))
    got = sp.query_keys(dirs[None], 1e-4)
    assert len(got.pattern) == 1
    np.testing.assert_array_equal(got.order[0], sp.pattern_keys(dirs[None]).order[0])


# ------------------------------------------------------------------------------------------------
# Refusals and the tolerance's edges
# ------------------------------------------------------------------------------------------------

_GOOD = _unit(np.array([[0.0, 0.0, 1.0], [0.3, 0.0, 1.0], [0.21, 0.07, 1.0], [0.05, -0.04, 1.0]]))

TOLERANCE_EDGES = [
    (5e-324, True),
    (1e-12, True),
    (0.04, True),
    (float(np.nextafter(0.04, 1.0)), False),
    (0.0, False),
    (-0.0, False),
    (-1e-12, False),
    (float("nan"), False),
    (float("inf"), False),
]


@pytest.mark.parametrize(("tau", "accepted"), TOLERANCE_EDGES)
def test_the_tolerance_is_taken_above_zero_and_up_to_the_cap_only(tau: float, accepted: bool) -> None:
    catalogue = sp.build_catalogue(_unit(np.random.default_rng(5).normal(size=(400, 3))), 20.0)
    if accepted:
        sp.query_keys(_GOOD[None], tau)
        catalogue.lookup(catalogue.shape[:1], tau)
    else:
        with pytest.raises(ValueError, match="key tolerance"):
            sp.query_keys(_GOOD[None], tau)
        with pytest.raises(ValueError, match="key tolerance"):
            catalogue.lookup(catalogue.shape[:1], tau)


@pytest.mark.parametrize(
    ("dirs", "match"),
    [
        (_GOOD[:3], "Patterns must be"),  # three stars
        (np.concatenate([_GOOD, _GOOD[:1]]), "Patterns must be"),  # five
        (_GOOD[:, :2], "Patterns must be"),  # not three components
        (_GOOD[:, :, None], "Patterns must be"),  # one dimension too many
        (np.where(np.arange(12).reshape(4, 3) == 4, np.nan, _GOOD), "not finite"),  # a NaN
        (np.where(np.arange(12).reshape(4, 3) == 4, np.inf, _GOOD), "not finite"),  # an infinity
        (np.concatenate([_GOOD[:3], _GOOD[:1]]), "two stars in one place"),
        (np.array([[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]), "within 90 deg"),  # at 90
        (np.array([[0.0, 0.0, 1.0], [0.0, 0.1, -1.0], [0.0, 1.0, 0.3], [1.0, 0.0, 0.3]]), "within 90 deg"),  # past 90
        (np.zeros((4, 3)), "zero vector"),  # no direction
        (np.array([[1.0, 1.0, 1.0], [1.0, -1.0, -1.0], [-1.0, 1.0, -1.0], [-1.0, -1.0, 1.0]]), "no mean direction"),
    ],
)
def test_a_set_of_directions_that_is_not_one_four_star_pattern_on_one_side_of_the_sky_is_refused(dirs, match) -> None:
    with pytest.raises(ValueError, match=match):
        sp.pattern_keys(np.asarray(dirs)[None])
    with pytest.raises(ValueError, match=match):
        sp.query_keys(np.asarray(dirs)[None], 0.01)


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_a_pattern_centred_on_any_axis_has_the_key_it_has_anywhere_else(axis: int, sign: float) -> None:
    """The tangent plane's basis is built from a helper vector; at either pole of each axis it must still exist."""
    base = sp.pattern_keys(_GOOD[None])
    centre = _unit(_GOOD.sum(axis=0))
    target = np.zeros(3)
    target[axis] = sign
    # The rotation that takes the pattern's mean direction exactly onto the axis (Rodrigues about their cross)
    v = np.cross(centre, target)
    s, c = np.linalg.norm(v), float(centre @ target)
    vx = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    turn = np.eye(3) + vx + vx @ vx * ((1.0 - c) / s**2)
    moved = _GOOD @ turn.T
    np.testing.assert_allclose(_unit(moved.sum(axis=0)), target, atol=1e-12)
    got = sp.pattern_keys(moved[None])
    assert np.all(np.isfinite(got.shape))
    np.testing.assert_allclose(got.shape, base.shape, rtol=0.0, atol=1e-9)


# Four directions of norm 9 whose unit vectors sum exactly to a multiple of +x in floating point (y and z cancel
# term by term in this order), so the mean direction is exactly the axis
_ON_X = np.array([[8.0, 4.0, 1.0], [4.0, 8.0, -1.0], [7.0, -4.0, 4.0], [1.0, -8.0, -4.0]])


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_a_pattern_whose_mean_is_exactly_an_axis_has_a_finite_key_equal_to_its_turned_copies(axis, sign) -> None:
    turned = np.roll(_ON_X, axis, axis=1)  # a cyclic swap of the axes is a rotation
    if sign < 0.0:
        turned = turned * np.roll(np.array([-1.0, -1.0, 1.0]), axis)  # half a turn about the third axis
    unit_rows = turned / np.linalg.norm(turned, axis=-1, keepdims=True)
    total = unit_rows[None].sum(axis=1)[0]
    assert np.count_nonzero(total) == 1 and np.sign(total[axis]) == sign
    got = sp.pattern_keys(turned[None])
    assert np.all(np.isfinite(got.shape))
    np.testing.assert_allclose(got.shape, sp.pattern_keys(_ON_X[None]).shape, rtol=0.0, atol=1e-12)
    np.testing.assert_array_equal(got.order, sp.pattern_keys(_ON_X[None]).order)


def _rule_pattern(points: list[list[float]]) -> np.ndarray:
    return _unit(np.column_stack([np.array(points), np.ones(4)]))


def _edge_tau(holds) -> float:
    """The smallest tolerance at which `holds(tau)` is true, found to the last float from a first guess."""
    lo, hi = 0.0, sp.MAX_KEY_TOLERANCE
    assert holds(hi) and not holds(5e-324)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid in (lo, hi):
            break
        lo, hi = (lo, mid) if holds(mid) else (mid, hi)
    tau = hi
    while holds(float(np.nextafter(tau, 0.0))):
        tau = float(np.nextafter(tau, 0.0))
    return tau


@pytest.mark.parametrize(
    ("rule", "points", "labelling"),
    [
        # The order rule: the canonical labelling with k and l swapped (x'_k - x'_l = 0.02 > 0)
        ("order", [[-0.05, 0.0], [0.05, 0.0], [0.001, 0.02], [0.003, -0.015]], (0, 1, 3, 2)),
        # The half-turn rule: the canonical labelling turned half a turn (x'_k + x'_l = -0.01 < 0)
        ("half turn", [[-0.05, 0.0], [0.05, 0.0], [-0.002, 0.02], [0.003, -0.015]], (1, 0, 3, 2)),
        # The longest-edge rule: the axis on the second longest edge, star 0 to star 3 (0.0949 against 0.1)
        ("longest edge", [[-0.05, 0.0], [0.05, 0.0], [0.0, 0.02], [0.04, -0.03]], None),
    ],
)
def test_each_slack_rule_admits_a_labelling_at_its_edge_and_not_one_float_before(rule, points, labelling) -> None:
    dirs = _rule_pattern(points)
    canonical = tuple(int(i) for i in sp.pattern_keys(dirs[None]).order[0])
    assert canonical == (0, 1, 2, 3)
    if labelling is None:
        plane = _plane(dirs)
        labelling = next(
            tuple(int(i) for i in o)
            for o in PERMUTATIONS
            if {int(o[0]), int(o[1])} == {0, 3} and _labelled(plane, o)[0][0] < _labelled(plane, o)[0][2]
            and _labelled(plane, o)[0][0] + _labelled(plane, o)[0][2] > 0.0
        )

    def holds(tau: float) -> bool:
        return labelling in {tuple(int(i) for i in o) for o in sp.query_keys(dirs[None], tau).order}

    tau = _edge_tau(holds)
    assert 1e-4 < tau < sp.MAX_KEY_TOLERANCE
    assert holds(tau) and not holds(float(np.nextafter(tau, 0.0)))
    # The edge is where the rule's own number says it is
    plane = _plane(dirs)
    key, length, longest = _labelled(plane, labelling)
    expected = {
        "order": (key[0] - key[2]) / 2.0,
        "half turn": -(key[0] + key[2]) / 2.0,
        "longest edge": (longest / length - 1.0) / 3.0,
    }[rule]
    assert tau == pytest.approx(expected, rel=1e-9)
    # At a tolerance where the rule's two sides are equal in floating point, the labelling is admitted: the rule
    # holds with its ends included. The numbers are the module's own (its key and edges for this labelling).
    query = sp.query_keys(dirs[None], sp.MAX_KEY_TOLERANCE)
    row = next(i for i, o in enumerate(query.order) if tuple(int(v) for v in o) == labelling)
    own = query.shape[row]
    if rule == "order":
        exact = [float((own[0] - own[2]) / 2.0)]
    elif rule == "half turn":
        exact = [float(-(own[0] + own[2]) / 2.0)]
    else:
        length = float(query.axis_rad[row])
        longest = float(sp.pattern_keys(dirs[None]).axis_rad[0])
        t = float(np.nextafter(tau, 0.0))
        for _ in range(4000):
            t = float(np.nextafter(t, 0.0))
        exact = []
        for _ in range(8000):
            if (1.0 + 3.0 * t) * length == longest:
                exact.append(t)
            t = float(np.nextafter(t, 1.0))
        assert exact, "no tolerance makes the two sides equal; choose another pattern"
    for t in exact:
        assert holds(t)


def test_directions_need_not_be_unit_length() -> None:
    np.testing.assert_allclose(
        sp.pattern_keys((_GOOD * np.array([[2.0], [0.5], [3.0], [1.0]]))[None]).shape, sp.pattern_keys(_GOOD[None]).shape,
        rtol=0.0, atol=1e-12,
    )


# ------------------------------------------------------------------------------------------------
# The sky lattice and the catalogue
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fc", sp.FIELD_CLASSES, ids=lambda fc: f"{fc.diagonal_fov_deg}")
def test_every_direction_lies_within_the_cap_radius_of_a_lattice_centre(fc) -> None:
    lattice = sp.sky_lattice(fc.cap_radius_deg)
    np.testing.assert_allclose(np.linalg.norm(lattice, axis=1), 1.0, atol=1e-12)
    np.testing.assert_array_equal(lattice, sp.sky_lattice(fc.cap_radius_deg))
    probes = _unit(np.random.default_rng(int(fc.diagonal_fov_deg * 10)).normal(size=(200_000, 3)))
    nearest = np.max(probes @ lattice.T, axis=1)
    worst = math.degrees(math.acos(float(np.clip(nearest.min(), -1.0, 1.0))))
    assert worst <= fc.cap_radius_deg


LATTICE_RADIUS_EDGES = [
    (sp.MIN_CAP_RADIUS_DEG, True),
    (float(np.nextafter(sp.MIN_CAP_RADIUS_DEG, 0.0)), False),
    (float(np.nextafter(90.0, 0.0)), True),
    (90.0, False),
    (0.0, False),
    (-1.0, False),
    (float("nan"), False),
    (float("inf"), False),
]


@pytest.mark.parametrize(("radius", "accepted"), LATTICE_RADIUS_EDGES)
def test_a_lattice_is_made_for_a_cap_radius_from_its_floor_to_under_90_deg_only(radius: float, accepted: bool) -> None:
    if accepted:
        assert len(sp.sky_lattice(radius)) == math.ceil(0.8 * 4.0 * math.pi / math.radians(radius) ** 2)
    else:
        with pytest.raises(ValueError, match="cap radius"):
            sp.sky_lattice(radius)


@pytest.mark.parametrize("radius", [1.0, 30.0, 45.0, 60.0, 75.0, float(np.nextafter(90.0, 0.0))])
def test_the_lattice_covers_the_sky_at_every_radius_it_takes(radius: float) -> None:
    lattice = sp.sky_lattice(radius)
    probes = _unit(np.random.default_rng(int(radius)).normal(size=(50_000, 3)))
    nearest = np.max(probes @ lattice.T, axis=1) if len(lattice) < 5000 else None
    if nearest is None:
        from scipy.spatial import cKDTree

        chord, _ = cKDTree(lattice).query(probes)
        worst = math.degrees(2.0 * math.asin(float(chord.max()) / 2.0))
    else:
        worst = math.degrees(math.acos(float(np.clip(nearest.min(), -1.0, 1.0))))
    assert worst <= radius


def _stars(n: int, seed: int) -> np.ndarray:
    return _unit(np.random.default_rng(seed).normal(size=(n, 3)))


def test_the_catalogue_holds_every_four_of_each_caps_brightest_once_in_canonical_order() -> None:
    stars = _stars(3000, 7)  # brightest first: the order is the brightness
    cap = 14.0
    catalogue = sp.build_catalogue(stars, cap, stars_per_cap=6)
    lattice = sp.sky_lattice(cap)
    expected: set[tuple[int, ...]] = set()
    cos_cap = math.cos(math.radians(cap))
    for centre in lattice:
        members = np.flatnonzero(stars @ centre >= cos_cap)[:6]
        expected.update(tuple(sorted(c)) for c in itertools.combinations(members.tolist(), 4))
    got = [tuple(sorted(q)) for q in catalogue.quads.tolist()]
    assert len(got) == len(set(got))
    assert set(got) == expected
    keys = sp.pattern_keys(stars[catalogue.quads])
    np.testing.assert_array_equal(keys.order, np.tile(np.arange(4), (len(catalogue.quads), 1)))
    np.testing.assert_allclose(keys.shape, catalogue.shape, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(keys.axis_rad, catalogue.axis_rad, rtol=1e-12, atol=0.0)


def test_a_cap_with_fewer_than_four_stars_gives_no_pattern_and_one_with_few_gives_all_it_has() -> None:
    stars = _unit(np.array([[0.0, 0.0, 1.0], [0.01, 0.0, 1.0], [0.0, 0.012, 1.0], [0.013, 0.011, 1.0], [0.0, 0.0, -1.0]]))
    catalogue = sp.build_catalogue(stars, 5.0, stars_per_cap=10)
    assert sorted(tuple(sorted(q)) for q in catalogue.quads.tolist()) == [(0, 1, 2, 3)]
    assert len(sp.build_catalogue(stars[[0, 1, 2, 4]], 5.0).quads) == 0


@pytest.mark.parametrize("chunk", [1, 7, 64, 10**6])
def test_keys_made_in_chunks_are_the_keys_made_at_once(chunk: int) -> None:
    stars = _unit(np.column_stack([np.random.default_rng(23).uniform(-0.3, 0.3, (12, 2)), np.ones(12)]))
    quads = np.array(list(itertools.combinations(range(12), 4)), dtype=np.int64)  # 495 patterns
    at_once = sp.pattern_keys(stars[quads])
    chunked = sp._keys_in_chunks(stars, quads, chunk)
    np.testing.assert_array_equal(chunked.order, at_once.order)
    np.testing.assert_allclose(chunked.shape, at_once.shape, rtol=0.0, atol=1e-12)
    np.testing.assert_allclose(chunked.axis_rad, at_once.axis_rad, rtol=1e-12, atol=0.0)


@pytest.mark.parametrize("chunk", [0, -1])
def test_a_chunk_of_no_patterns_is_refused(chunk: int) -> None:
    with pytest.raises(ValueError, match="at least one pattern"):
        sp._keys_in_chunks(_stars(10, 1), np.zeros((4, 4), dtype=np.int64), chunk)


def test_the_catalogue_keys_its_patterns_in_chunks_of_the_stated_size(monkeypatch) -> None:
    assert sp._KEY_CHUNK == 65_536
    seen = []
    real = sp._keys_in_chunks

    def spy(stars, quads, chunk):
        seen.append((len(quads), chunk))
        return real(stars, quads, chunk)

    monkeypatch.setattr(sp, "_keys_in_chunks", spy)
    catalogue = sp.build_catalogue(_stars(3000, 7), 14.0, stars_per_cap=6)
    assert seen == [(len(catalogue.quads), sp._KEY_CHUNK)]


def test_the_same_stars_give_the_same_catalogue() -> None:
    stars = _stars(2000, 11)
    first, second = sp.build_catalogue(stars, 10.0), sp.build_catalogue(stars.copy(), 10.0)
    assert first.quads.dtype.kind == "i"
    np.testing.assert_array_equal(first.quads, second.quads)


@pytest.mark.parametrize("per_cap", [3, 0, -1, 4.5])
def test_fewer_than_four_stars_per_cap_or_a_part_of_a_star_is_refused(per_cap) -> None:
    with pytest.raises(ValueError, match="at least four stars"):
        sp.build_catalogue(_stars(100, 1), 20.0, stars_per_cap=per_cap)


@pytest.mark.parametrize(("share", "inside"), [(1.0 - 1e-10, True), (1.0 + 1e-10, False)])
def test_a_star_just_inside_a_caps_edge_joins_its_pattern_and_one_just_past_it_does_not(share, inside) -> None:
    cap = 10.0
    lattice = sp.sky_lattice(cap)
    c0 = lattice[len(lattice) // 2]
    others = np.degrees(np.arccos(np.clip(np.delete(lattice, len(lattice) // 2, axis=0) @ c0, -1.0, 1.0)))
    assert others.min() > cap + 0.2  # no other cap reaches the three stars near this centre
    e1 = _unit(np.cross([0.0, 0.0, 1.0], c0))
    e2 = np.cross(c0, e1)

    def at(angle_deg: float, azimuth_deg: float) -> np.ndarray:
        a, b = math.radians(angle_deg), math.radians(azimuth_deg)
        return math.cos(a) * c0 + math.sin(a) * (math.cos(b) * e1 + math.sin(b) * e2)

    stars = np.array([at(0.1, 0.0), at(0.1, 120.0), at(0.1, 240.0), at(cap * share, 45.0)])
    found = sp.build_catalogue(stars, cap, stars_per_cap=4).quads
    assert len(found) == (1 if inside else 0)


def test_exactly_four_stars_in_one_cap_make_one_pattern() -> None:
    stars = _unit(np.array([[0.0, 0.0, 1.0], [0.01, 0.0, 1.0], [0.0, 0.012, 1.0], [0.013, 0.011, 1.0]]))
    assert len(sp.build_catalogue(stars, 5.0, stars_per_cap=4).quads) == 1
    assert len(sp.build_catalogue(stars, 5.0, stars_per_cap=4.0).quads) == 1


@pytest.mark.parametrize(
    "stars",
    [
        np.zeros((10, 2)),
        np.zeros(30),
        np.where(np.arange(30).reshape(10, 3) == 7, np.nan, 1.0),
        np.full((10, 3), np.inf),
        np.where(np.arange(30).reshape(10, 3) < 3, 0.0, 1.0),  # a zero vector
    ],
)
def test_stars_that_are_not_finite_directions_are_refused(stars) -> None:
    with pytest.raises(ValueError, match="Stars must be|A star is a zero vector"):
        sp.build_catalogue(stars, 20.0)


def test_an_empty_catalogue_finds_nothing() -> None:
    empty = sp.build_catalogue(_stars(3, 2), 20.0)
    assert len(empty.quads) == 0
    hits = empty.lookup(np.zeros((3, 4)), 0.01)
    assert len(hits) == 3 and all(h.size == 0 for h in hits)


# ------------------------------------------------------------------------------------------------
# Lookup
# ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def made_up_catalogue():
    return sp.build_catalogue(_stars(4000, 13), 10.0)


def test_every_stored_pattern_is_found_from_its_own_key_moved_inside_the_tolerance(made_up_catalogue) -> None:
    rng = np.random.default_rng(17)
    picks = rng.choice(len(made_up_catalogue.quads), 300, replace=False)
    tau = 0.01
    queries = made_up_catalogue.shape[picks] + rng.uniform(-tau, tau, (300, 4))
    for pick, hits in zip(picks, made_up_catalogue.lookup(queries, tau)):
        assert pick in hits
        assert np.all(np.diff(hits) > 0)
        within = np.abs(made_up_catalogue.shape - queries[np.flatnonzero(picks == pick)[0]]).max(axis=1) <= tau
        np.testing.assert_array_equal(hits, np.flatnonzero(within))


@pytest.mark.parametrize("axis", range(4))
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_the_tolerance_includes_its_edge_and_nothing_past_it(made_up_catalogue, axis: int, sign: float) -> None:
    tau = 0.02
    for pick in (0, 7, 123):
        key = made_up_catalogue.shape[pick]
        at = key.copy()
        at[axis] = key[axis] + sign * tau
        past = key.copy()
        past[axis] = key[axis] + sign * tau * (1.0 + 1e-9)
        found_at = made_up_catalogue.lookup(at[None], tau)[0]
        found_past = made_up_catalogue.lookup(past[None], tau)[0]
        assert (pick in found_at) == (abs(at[axis] - key[axis]) <= tau)
        assert pick not in found_past


@pytest.mark.parametrize(
    ("axis", "sign"),
    # x'_l is never negative in a canonical key (x'_l >= x'_k and x'_k + x'_l >= 0), so it has no negative case
    [(a, s) for a in range(4) for s in (1.0, -1.0) if (a, s) != (2, -1.0)],
)
def test_a_key_exactly_the_tolerance_away_is_found(made_up_catalogue, axis: int, sign: float) -> None:
    """tau = 2**-6 added to a coordinate between 0.25 and 0.48 in size is exact, so the difference is exactly tau."""
    tau = 2.0**-6
    size = sign * made_up_catalogue.shape[:, axis]
    pick = int(np.flatnonzero((size >= 0.25) & (size <= 0.48))[0])
    key = made_up_catalogue.shape[pick]
    at = key.copy()
    at[axis] = key[axis] + sign * tau
    assert abs(at[axis] - key[axis]) == tau
    assert pick in made_up_catalogue.lookup(at[None], tau)[0]


def test_the_largest_axis_range_keeps_its_ends_and_nothing_past_them(made_up_catalogue) -> None:
    pick = 42
    key = made_up_catalogue.shape[pick : pick + 1]
    edge = float(made_up_catalogue.axis_rad[pick])
    assert pick in made_up_catalogue.lookup(key, 1e-9, axis_range=(edge, edge))[0]
    assert pick not in made_up_catalogue.lookup(key, 1e-9, axis_range=(float(np.nextafter(edge, np.inf)), np.inf))[0]
    assert pick not in made_up_catalogue.lookup(key, 1e-9, axis_range=(0.0, float(np.nextafter(edge, 0.0))))[0]
    with pytest.raises(ValueError, match="axis range"):
        made_up_catalogue.lookup(key, 1e-9, axis_range=(edge, 0.5 * edge))
    with pytest.raises(ValueError, match="axis range"):
        made_up_catalogue.lookup(key, 1e-9, axis_range=(float("nan"), edge))


@pytest.mark.parametrize("bad", [np.zeros((2, 3)), np.zeros(4), np.full((1, 4), np.nan), np.full((1, 4), np.inf)])
def test_a_key_that_is_not_four_finite_numbers_is_refused(made_up_catalogue, bad) -> None:
    with pytest.raises(ValueError, match="Keys must be"):
        made_up_catalogue.lookup(bad, 0.01)


# ------------------------------------------------------------------------------------------------
# The real tiles, seen through a made-up camera
# ------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiles_84():
    return sp.tile_catalogue(sp.FIELD_CLASSES[4])


def test_the_tile_catalogue_is_built_once_and_holds_tile_stars(tiles_84) -> None:
    assert sp.tile_catalogue(sp.FIELD_CLASSES[4]) is tiles_84
    assert len(tiles_84.quads) > 1000
    assert tiles_84.quads.min() >= 0 and tiles_84.quads.max() < len(tiles_84.star_dirs)
    np.testing.assert_allclose(np.linalg.norm(tiles_84.star_dirs, axis=1), 1.0, atol=1e-12)
    # Every pattern lies inside one cap: no edge longer than the cap's diameter
    assert tiles_84.axis_rad.max() <= 2.0 * math.tan(math.radians(sp.FIELD_CLASSES[4].cap_radius_deg)) * 1.01


def test_the_tile_catalogue_stars_are_the_tile_index_rows_at_their_catalog_places(tiles_84) -> None:
    from services.science.phone_photo_solver import _get_tile_index

    data = _get_tile_index().data
    assert len(tiles_84.star_dirs) == len(data["ra0"])
    ra, dec = np.radians(data["ra0"]), np.radians(data["dec0"])
    expected = np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    np.testing.assert_allclose(tiles_84.star_dirs, expected, rtol=0.0, atol=1e-12)
    # The rows run brightest first, so each cap's patterns come from its brightest stars
    assert np.all(np.diff(data["v_mag"]) >= 0.0)
    # Every class is built from the same rows at its own cap radius
    assert all(sp.tile_catalogue(fc).star_dirs is not None for fc in sp.FIELD_CLASSES[4:])


def test_tile_patterns_seen_by_a_camera_with_centroid_noise_are_found(tiles_84) -> None:
    """A 4032 x 3024 pinhole at 84 deg diagonal, pointed at each sampled pattern's centre, 1 px of centroid noise;
    the pixels go back to directions through the same focal. Each pattern is looked up with the tolerance its own
    noise needs (the change of its key in its stored labelling), and must be found."""
    rng = np.random.default_rng(19)
    width, height = 4032, 3024
    focal = 0.5 * math.hypot(width, height) / math.tan(math.radians(84.0) / 2.0)
    picks = rng.choice(len(tiles_84.quads), 200, replace=False)
    found = 0
    for pick in picks:
        dirs = tiles_84.star_dirs[tiles_84.quads[pick]]
        axis_z = _unit(dirs.sum(axis=0))
        turn = _rotation(rng.normal(size=4))
        e1 = _unit(np.cross(turn[0], axis_z))
        e2 = np.cross(axis_z, e1)
        cam = np.stack([e1, e2, axis_z])  # rows: camera x, y, optical axis
        local = dirs @ cam.T
        px = focal * local[:, 0] / local[:, 2] + rng.normal(0.0, 1.0, 4)
        py = focal * local[:, 1] / local[:, 2] + rng.normal(0.0, 1.0, 4)
        seen = _unit(np.column_stack([px / focal, py / focal, np.ones(4)])) @ cam
        seen_key, _, _ = _labelled(_plane(seen), np.arange(4))
        tau = float(np.abs(seen_key - tiles_84.shape[pick]).max()) * (1.0 + 1e-9) + 1e-12
        if tau > sp.MAX_KEY_TOLERANCE:
            continue
        found += 1
        query = sp.query_keys(seen[None], tau)
        hits = tiles_84.lookup(query.shape, tau)
        assert any(pick in h and list(o) == [0, 1, 2, 3] for h, o in zip(hits, query.order))
    assert found >= 150
