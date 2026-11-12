"""Four-star sky patterns and their catalogue, for a whole-sky search of a photo with no time and no place.

After J. Brown and K. Stubis, "TETRA: Star Identification with Hash Tables", 31st Annual AIAA/USU Conference on Small
Satellites, SSC17-VIII-3, 2017 [verified, read at source]:

- A pattern is four stars. Its key puts the largest edge (length L) from (-L/2, 0) to (L/2, 0) and gives the other
  two stars their coordinates divided by L, (x', y') (p. 5). Here the coordinates are taken on the plane tangent to
  the sky at the four stars' mean direction (a gnomonic projection), so a pattern has the same key wherever it sits
  in a wide frame [proposed: the paper treats the field as flat, p. 8].
- The paper fixes the half turn by the sign of the largest x' bin and orders the two stars by their bins, and stores a
  catalog pattern again under each labelling an error could produce (p. 5). Here the canonical labelling is the one
  where the axis is the longest edge, x'_k <= x'_l and x'_k + x'_l >= 0, which is the same rule written without bins;
  the catalogue keeps one labelling, and the query side asks under every labelling that an error within the
  tolerance could have made canonical (`query_keys`).
- The paper looks a key up in one directly addressed bin (pp. 4 to 7). Here a KD-tree box query takes the tolerance
  each lookup needs, which on a phone depends on the pattern's size, the field and the lens [proposed, measured in
  the solver-12-prep report].

The catalogue [proposed]: for each field class, a Fibonacci lattice of sky centres close enough that every direction
lies within the class's cap radius of one; each cap's `STARS_PER_CAP` brightest stars give all their four-star sets,
each set kept once. The paper does not say how it chooses catalog patterns.

A key is geometry only: it says nothing about which stars a photo shows until a candidate passes verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import itertools
import math

import numpy as np
from scipy.spatial import cKDTree

# Diagonal fields of view of the classes [proposed, solver-12-prep section 2.2: the corpus spans a 5x zoom near 20 deg
# to ultra-wides past 95 deg; below 20 deg the caps need stars past the L1 tiles' G 8]
FIELD_CLASS_DIAGONALS_DEG = (20.0, 30.0, 48.5, 63.4, 84.0, 120.0)
# Stars per cap that make the patterns [proposed, solver-12-prep: 1.1 million patterns over the six classes, 40 MB]
STARS_PER_CAP = 10
# Largest lookup tolerance on a key coordinate [proposed, solver-12-prep section 2.1: past it chance matches grow to
# about 75 per lookup at 20 deg]
MAX_KEY_TOLERANCE = 0.04
# The short side of a 4:3 frame over its diagonal
_SHORT_SIDE_OVER_DIAGONAL = 0.6
# Fibonacci lattice points per (4 pi / cap radius^2): the solver-12-prep measurement needed 0.66 to 0.67 for a covering
# radius of 0.89 to 0.94 cap radii; 0.8 leaves room [proposed], and the tests probe every class
_LATTICE_DENSITY = 0.8
# Smallest cap radius a lattice is made for [proposed: the smallest class's cap is 3.02 deg, and a lattice for 1 deg
# already has 33,003 centres]
MIN_CAP_RADIUS_DEG = 1.0
# Slack of the canonical rule's tests under a key error of tau per coordinate [derived: a difference or a sum of two
# coordinates moves by at most 2 tau; an edge in units of the axis by at most 2 sqrt(2) tau, under 3 tau]
_ORDER_SLACK = 2.0
_EDGE_SLACK = 3.0
# Patterns keyed at once while a catalogue is built, since all 24 labellings of a chunk are held together [proposed:
# building the six tile catalogues peaked at 6,189 MB of RSS with each class keyed at once and at 932 MB in chunks of
# 65,536, solver-12b report]
_KEY_CHUNK = 65_536

_PERMUTATIONS = np.array(list(itertools.permutations(range(4))), dtype=np.int64)
_PAIRS = np.array(list(itertools.combinations(range(4), 2)), dtype=np.int64)


@dataclass(frozen=True)
class FieldClass:
    """A class of photos by diagonal field of view, on a 4:3 rectilinear frame."""

    diagonal_fov_deg: float

    @property
    def inscribed_radius_deg(self) -> float:
        """Angular radius of the circle inscribed in the frame (half its short side)."""
        half = math.radians(self.diagonal_fov_deg) / 2.0
        return math.degrees(math.atan(math.tan(half) * _SHORT_SIDE_OVER_DIAGONAL))

    @property
    def cap_radius_deg(self) -> float:
        """Half the inscribed radius: a cap this wide about the lattice centre nearest the frame's centre lies inside
        the frame."""
        return self.inscribed_radius_deg / 2.0


FIELD_CLASSES = tuple(FieldClass(d) for d in FIELD_CLASS_DIAGONALS_DEG)


@dataclass(frozen=True)
class PatternKeys:
    """Per pattern: the key (x'_k, y'_k, x'_l, y'_l); the length of the labelling's axis on the tangent plane, in
    radians (the canonical axis is the pattern's longest edge, the L the paper keeps); and the labelling as indices
    into the pattern's four stars (axis end a, axis end b, star k, star l)."""

    shape: np.ndarray
    axis_rad: np.ndarray
    order: np.ndarray


@dataclass(frozen=True)
class QueryKeys:
    """Every labelling of each pattern that a key error within the tolerance could have made canonical: `pattern`
    names the input pattern each row belongs to; `axis_rad` is that labelling's axis length, which is the pattern's
    longest edge only where the axis is."""

    pattern: np.ndarray
    shape: np.ndarray
    axis_rad: np.ndarray
    order: np.ndarray


def _require_tolerance(tau: float) -> float:
    tau = float(tau)
    if not (math.isfinite(tau) and 0.0 < tau <= MAX_KEY_TOLERANCE):
        raise ValueError(f"A key tolerance must be above 0 and at most {MAX_KEY_TOLERANCE}; got {tau!r}.")
    return tau


def _tangent_points(dirs: np.ndarray) -> np.ndarray:
    """(M, 4, 3) directions to (M, 4, 2) gnomonic points about each pattern's mean direction, refusing what is not
    four distinct directions on one side of the sky."""
    dirs = np.asarray(dirs, dtype=np.float64)
    if dirs.ndim != 3 or dirs.shape[1:] != (4, 3):
        raise ValueError(f"Patterns must be an (M, 4, 3) array of directions; got shape {dirs.shape}.")
    if not np.all(np.isfinite(dirs)):
        raise ValueError("A pattern holds a direction that is not finite.")
    norms = np.linalg.norm(dirs, axis=-1, keepdims=True)
    if np.any(norms == 0.0):
        raise ValueError("A pattern holds a zero vector, which is no direction.")
    dirs = dirs / norms
    total = dirs.sum(axis=1)
    centre_norm = np.linalg.norm(total, axis=-1, keepdims=True)
    if np.any(centre_norm == 0.0):
        raise ValueError("A pattern's directions have no mean direction.")
    centre = total / centre_norm
    dot = np.einsum("mkj,mj->mk", dirs, centre)
    # A star 90 deg or more from the mean has no point on the tangent plane
    if np.any(dot <= 0.0):
        raise ValueError("A pattern's stars must lie within 90 deg of their mean direction.")
    helper = np.where(np.abs(centre[:, 2:3]) < 0.5, np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]))
    e1 = np.cross(helper, centre)
    e1 /= np.linalg.norm(e1, axis=-1, keepdims=True)
    e2 = np.cross(centre, e1)
    points = np.stack(
        [np.einsum("mkj,mj->mk", dirs, e1) / dot, np.einsum("mkj,mj->mk", dirs, e2) / dot], axis=-1
    )
    edges = np.linalg.norm(points[:, _PAIRS[:, 0]] - points[:, _PAIRS[:, 1]], axis=-1)
    if np.any(edges == 0.0):
        raise ValueError("A pattern holds two stars in one place.")
    return points


def _all_labellings(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each pattern and each of the 24 labellings (a, b, k, l): the key (M, 24, 4), the axis length (M, 24), and
    the longest edge of the pattern (M,)."""
    p = points[:, _PERMUTATIONS]  # (M, 24, 4, 2)
    axis = p[:, :, 1] - p[:, :, 0]
    length = np.hypot(axis[..., 0], axis[..., 1])
    u = axis / length[..., None]
    w = np.stack([-u[..., 1], u[..., 0]], axis=-1)
    mid = 0.5 * (p[:, :, 0] + p[:, :, 1])
    rel = p[:, :, 2:] - mid[:, :, None, :]
    x = np.einsum("mpsj,mpj->mps", rel, u) / length[..., None]
    y = np.einsum("mpsj,mpj->mps", rel, w) / length[..., None]
    keys = np.stack([x[..., 0], y[..., 0], x[..., 1], y[..., 1]], axis=-1)
    edges = points[:, _PAIRS[:, 0]] - points[:, _PAIRS[:, 1]]
    longest = np.max(np.hypot(edges[..., 0], edges[..., 1]), axis=1)
    return keys, length, longest


def pattern_keys(dirs: np.ndarray) -> PatternKeys:
    """The canonical key of each (M, 4, 3) pattern of directions (any length, each within 90 deg of the mean).

    Canonical: the axis is the longest edge, x'_k <= x'_l and x'_k + x'_l >= 0. Where floating point leaves no
    labelling or several meeting all three exactly, the one that misses them by least is taken, the first in
    `itertools.permutations` order on a tie."""
    points = _tangent_points(dirs)
    keys, length, longest = _all_labellings(points)
    miss = (
        np.maximum(0.0, keys[..., 0] - keys[..., 2])
        + np.maximum(0.0, -(keys[..., 0] + keys[..., 2]))
        + (longest[:, None] - length) / longest[:, None]
    )
    pick = np.argmin(miss, axis=1)
    rows = np.arange(points.shape[0])
    return PatternKeys(shape=keys[rows, pick], axis_rad=length[rows, pick], order=_PERMUTATIONS[pick])


def query_keys(dirs: np.ndarray, tau: float) -> QueryKeys:
    """Every labelling of each pattern that a key error of at most `tau` per coordinate could have made canonical:
    x'_k - x'_l <= 2 tau, -(x'_k + x'_l) <= 2 tau, and no edge longer than (1 + 3 tau) times the axis. The pattern's
    own canonical labelling is always among them."""
    tau = _require_tolerance(tau)
    points = _tangent_points(dirs)
    keys, length, longest = _all_labellings(points)
    keep = (
        (keys[..., 0] - keys[..., 2] <= _ORDER_SLACK * tau)
        & (-(keys[..., 0] + keys[..., 2]) <= _ORDER_SLACK * tau)
        & (longest[:, None] <= (1.0 + _EDGE_SLACK * tau) * length)
    )
    pattern, perm = np.nonzero(keep)
    return QueryKeys(
        pattern=pattern.astype(np.int64),
        shape=keys[pattern, perm],
        axis_rad=length[pattern, perm],
        order=_PERMUTATIONS[perm],
    )


def sky_lattice(cap_radius_deg: float) -> np.ndarray:
    """(N, 3) unit vectors of a Fibonacci lattice of ceil(`_LATTICE_DENSITY` * 4 pi / r^2) points for a cap radius r
    (radians), so that every direction lies within r of one (probed by the tests for every class and across the
    radii taken)."""
    radius = float(cap_radius_deg)
    if not (math.isfinite(radius) and MIN_CAP_RADIUS_DEG <= radius < 90.0):
        raise ValueError(f"A cap radius must be at least {MIN_CAP_RADIUS_DEG} deg and under 90; got {radius!r}.")
    n = math.ceil(_LATTICE_DENSITY * 4.0 * math.pi / math.radians(radius) ** 2)
    i = np.arange(n, dtype=np.float64) + 0.5
    z = 1.0 - 2.0 * i / n
    r = np.sqrt(np.clip(1.0 - z * z, 0.0, None))
    phi = math.pi * (3.0 - math.sqrt(5.0)) * i
    return np.column_stack([r * np.cos(phi), r * np.sin(phi), z])


@dataclass(frozen=True)
class PatternCatalogue:
    """Four-star patterns of one field class. `quads` holds each pattern's stars as rows of `star_dirs`, in their
    canonical labelling (axis end a, axis end b, star k, star l); `shape` is their keys and `axis_rad` their axis
    lengths in radians on the tangent plane, each the pattern's longest edge."""

    star_dirs: np.ndarray
    quads: np.ndarray
    shape: np.ndarray
    axis_rad: np.ndarray
    _tree: cKDTree | None = field(default=None, repr=False, compare=False)

    def lookup(
        self, shape_keys: np.ndarray, tau: float, axis_range: tuple[float, float] | None = None
    ) -> list[np.ndarray]:
        """For each (K, 4) key, the ascending indices of the patterns whose four coordinates each lie within `tau` of
        it (ends included), and whose axis length lies in `axis_range` (ends included) when one is given."""
        tau = _require_tolerance(tau)
        keys = np.asarray(shape_keys, dtype=np.float64)
        if keys.ndim != 2 or keys.shape[1] != 4 or not np.all(np.isfinite(keys)):
            raise ValueError(f"Keys must be a (K, 4) array of finite numbers; got shape {keys.shape}.")
        if axis_range is not None:
            lo, hi = (float(v) for v in axis_range)
            if math.isnan(lo) or math.isnan(hi) or lo > hi:
                raise ValueError(f"An axis range must run from a low end to a high end; got {axis_range!r}.")
        if self._tree is None:
            return [np.zeros(0, dtype=np.int64) for _ in range(keys.shape[0])]
        # The box query is widened by a hair, then each candidate is held to the exact comparison
        found = self._tree.query_ball_point(keys, tau * (1.0 + 1e-9) + 1e-15, p=np.inf)
        out = []
        for key, hits in zip(keys, found):
            hits = np.asarray(sorted(hits), dtype=np.int64)
            if hits.size:
                hits = hits[np.max(np.abs(self.shape[hits] - key), axis=1) <= tau]
            if axis_range is not None and hits.size:
                axis = self.axis_rad[hits]
                hits = hits[(axis >= lo) & (axis <= hi)]
            out.append(hits)
        return out


def build_catalogue(
    star_dirs: np.ndarray, cap_radius_deg: float, stars_per_cap: int = STARS_PER_CAP
) -> PatternCatalogue:
    """The patterns of the `stars_per_cap` first stars (the caller lists them brightest first) inside each lattice
    cap of radius `cap_radius_deg`; a cap with fewer gives every four-star set it has, one with under four gives none."""
    if int(stars_per_cap) != stars_per_cap or stars_per_cap < 4:
        raise ValueError(f"A cap needs at least four stars to make a pattern; got {stars_per_cap!r}.")
    stars = np.asarray(star_dirs, dtype=np.float64)
    if stars.ndim != 2 or stars.shape[1] != 3 or not np.all(np.isfinite(stars)):
        raise ValueError(f"Stars must be an (S, 3) array of finite directions; got shape {stars.shape}.")
    norms = np.linalg.norm(stars, axis=1, keepdims=True)
    if np.any(norms == 0.0):
        raise ValueError("A star is a zero vector, which is no direction.")
    stars = stars / norms
    lattice = sky_lattice(cap_radius_deg)
    cos_cap = math.cos(math.radians(float(cap_radius_deg)))
    sets: set[tuple[int, ...]] = set()
    if stars.shape[0] >= 4:
        tree = cKDTree(stars)
        chord = 2.0 * math.sin(math.radians(float(cap_radius_deg)) / 2.0)
        for centre, members in zip(lattice, tree.query_ball_point(lattice, chord * (1.0 + 1e-9))):
            members = np.asarray(sorted(members), dtype=np.int64)
            # The chord query is widened by a hair; the cap is the exact angle test
            members = members[stars[members] @ centre >= cos_cap][: int(stars_per_cap)]
            sets.update(itertools.combinations(members.tolist(), 4))
    if not sets:
        empty = np.zeros((0, 4), dtype=np.int64)
        return PatternCatalogue(stars, empty, np.zeros((0, 4)), np.zeros(0), None)
    quads = np.array(sorted(sets), dtype=np.int64)
    keys = _keys_in_chunks(stars, quads, _KEY_CHUNK)
    quads = np.take_along_axis(quads, keys.order, axis=1)
    return PatternCatalogue(stars, quads, keys.shape, keys.axis_rad, cKDTree(keys.shape))


def _keys_in_chunks(stars: np.ndarray, quads: np.ndarray, chunk: int) -> PatternKeys:
    """`pattern_keys(stars[quads])`, `chunk` patterns at a time."""
    if chunk < 1:
        raise ValueError(f"A chunk holds at least one pattern; got {chunk!r}.")
    parts = [pattern_keys(stars[quads[start : start + chunk]]) for start in range(0, len(quads), chunk)]
    return PatternKeys(
        shape=np.concatenate([k.shape for k in parts]),
        axis_rad=np.concatenate([k.axis_rad for k in parts]),
        order=np.concatenate([k.order for k in parts]),
    )


@lru_cache(maxsize=None)
def tile_catalogue(field_class: FieldClass) -> PatternCatalogue:
    """The class's catalogue from the shipped G 12 tiles' L0 and L1 layers (Gaia DR3 and Hipparcos 2007, G to 8) at
    the tiles' epoch, brightest by the phone solver's V first; `star_dirs` rows are the tile index's rows. Built once
    per process."""
    from services.science.phone_photo_solver import _get_tile_index

    data = _get_tile_index().data
    ra = np.radians(data["ra0"])
    dec = np.radians(data["dec0"])
    dirs = np.column_stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    return build_catalogue(dirs, field_class.cap_radius_deg)
