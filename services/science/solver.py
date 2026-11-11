"""Deterministic astrometric geometric plate solver with held-out WCS verification,
and public re-exports for point-source extraction, video calibration stacking, and horizon solving.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
import sys
from typing import Callable, Sequence

import numpy as np
from scipy import optimize
from scipy.spatial import cKDTree

from services.science.catalog import (
    CatalogStar,
    angular_separation_deg,
    get_catalog_stars,
    icrs_to_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.detector import (
    InjectionRecoveryBin,
    InjectionRecoveryReport,
    _build_gaussian_psf_kernel,
    _estimate_background_and_noise,
    _estimate_subpixel_shift_phase_corr,
    _extract_registration_signal,
    _to_luma_2d,
    detect_point_sources,
    evaluate_injection_recovery,
    render_synthetic_starfield,
    stack_video_frames_for_calibration,
    verify_candidates_on_original_pixels,
)
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.phone_photo_solver import (
    PhoneCameraWCS,
    evaluate_coincidence_gate,
    solve_phone_photo,
)
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SolverIdentity,
    SourceDetection,
    VideoStackCalibrationResult,
    WCSModel,
    require_source_sha256,
)


def triangle_solver_identity(catalog: Sequence[CatalogStar]) -> SolverIdentity:
    """The plate solver's name, and the catalog it matched against, counted from the stars it was given."""
    releases = " and ".join(sorted({star.release_version for star in catalog}))
    return SolverIdentity(
        name="AlbedianTriangleSolver",
        version="1.0.0",
        catalog_release=f"{len(catalog)} pinned bright stars ({releases} rows)",
    )

# The held-out gate, with the thresholds of `evaluateWCSVerificationGate` in packages/evidence. Under a WCS
# fitted on training stars only, each detection pairs with its nearest unused catalog star within
# ASSOCIATION_RADIUS_PX; a pair inside the per-star inlier gate (1.8 to 3 px, from the spread of all residuals)
# is an inlier. The fit is calibrated when at least MIN_HELD_OUT_INLIERS held-out detections are inliers and
# the residuals of every held-out pair, inlier or not, have a median of at most MAX_HELD_OUT_MEDIAN_RESIDUAL_PX
# and a 95th percentile of at most MAX_HELD_OUT_P95_RESIDUAL_PX.
ASSOCIATION_RADIUS_PX = 8.0
INLIER_GATE_MIN_PX = 1.8
INLIER_GATE_MAX_PX = 3.0
MIN_HELD_OUT_INLIERS = 6
MAX_HELD_OUT_MEDIAN_RESIDUAL_PX = 3.0
MAX_HELD_OUT_P95_RESIDUAL_PX = 6.0
# The held-out pairing above, and the triangle search's consensus pairing, measure only the stars a vectorized
# projection (`_project`) puts within their radius plus this margin of a detection, each with
# `WCSModel.world_to_pixel` as before, so they pair exactly as measuring every star does. The margin is for pixels
# within 10,000 px of the frame and nothing wider: there the two projections agree to 1e-6 px
# (`test_pbt_the_vectorized_projection_is_world_to_pixel_star_by_star`, 80 random WCS of 205 stars a run) and the
# pairing stays exact with the projection off by up to 7e-4 px
# (`test_pbt_pairing_is_exact_when_the_projection_is_off_by_up_to_0_0007_px`), both in
# tests/science/test_triangle_dense_fields.py [tested]. Before landing, against
# dcdbd65d on 2026-11-11, the lane also found 0 px between them over 199,731 projections from 3,000 random WCS on
# macOS arm64 [inference: that script is not in the repository]. Another platform's trigonometry may differ by a few
# units in the last place [inference], far below this margin within that range; past about 1e13 px one unit in the
# last place is larger than the margin, so there the pairs equal main's only where the two projections agree exactly
_PREFILTER_MARGIN_PX = 1e-3
# A detector triangle matches a catalog triangle when both side ratios lie within this of the catalog's
TRIANGLE_RATIO_TOLERANCE = 0.045
# The largest coordinate magnitude a KD-tree here holds or is asked with, from the float format alone: on two axes
# within [-L, L] a difference is at most 2L = sqrt(max) / 2, its square at most max / 4 and a squared distance at most
# max / 2, so every distance cKDTree sums stays finite. cKDTree raises "Encountering floating point overflow" when one
# does not [tested, scipy 1.18.1, `test_a_kd_tree_holds_every_point_within_the_limit_and_overflows_past_it` in
# tests/science/test_triangle_dense_fields.py: a point at the origin and a query at (v, v) raise from the float
# after v = sqrt(max / 2) on]
_INDEX_LIMIT = math.sqrt(sys.float_info.max) / 4.0


@dataclass(frozen=True)
class HeldOutVerification:
    """Held-out verification of a WCS fitted on training stars only.

    `associations` holds (detection, catalog star, residual px, trained the fit) for every detection that
    pairs with a catalog star within `ASSOCIATION_RADIUS_PX`. The held-out residual statistics cover every
    held-out pair, not only the inliers, so a fit that misses held-out stars shows it in its median and p95.
    The held-out detections still take part in choosing which catalog pattern the field matches; the
    least-squares fit sees only the training stars.
    """

    associations: tuple[tuple[SourceDetection, CatalogStar, float, bool], ...]
    inlier_gate_px: float

    @property
    def held_out_residuals_px(self) -> tuple[float, ...]:
        return tuple(res_px for _, _, res_px, is_train in self.associations if not is_train)

    @property
    def held_out_pair_count(self) -> int:
        return len(self.held_out_residuals_px)

    @property
    def held_out_inliers(self) -> int:
        return sum(res_px <= self.inlier_gate_px for res_px in self.held_out_residuals_px)

    @property
    def median_residual_px(self) -> float | None:
        residuals = self.held_out_residuals_px
        return float(np.median(residuals)) if residuals else None

    @property
    def p95_residual_px(self) -> float | None:
        residuals = self.held_out_residuals_px
        return float(np.percentile(residuals, 95.0)) if residuals else None

    @property
    def inliers(self) -> dict[int, tuple[CatalogStar, float, bool]]:
        """Pairs inside the inlier gate, keyed by detection identity: (star, residual px, trained the fit)."""
        return {
            id(det): (star, res_px, is_train)
            for det, star, res_px, is_train in self.associations
            if res_px <= self.inlier_gate_px
        }

    @property
    def passed(self) -> bool:
        median, p95 = self.median_residual_px, self.p95_residual_px
        return (
            self.held_out_inliers >= MIN_HELD_OUT_INLIERS
            and median is not None
            and median <= MAX_HELD_OUT_MEDIAN_RESIDUAL_PX
            and p95 is not None
            and p95 <= MAX_HELD_OUT_P95_RESIDUAL_PX
        )


def verify_held_out(
    wcs: WCSModel,
    detections: Sequence[SourceDetection],
    catalog: Sequence[CatalogStar],
    training_detections: Sequence[SourceDetection],
) -> HeldOutVerification:
    """Pair every detection with a catalog star under `wcs`, which was fitted on `training_detections` (the
    same objects as in `detections`) and nothing else, and measure the held-out gate.

    Each detection, in order, takes the nearest catalog star not yet taken (the first in catalog order on a tie)
    when it lies within `ASSOCIATION_RADIUS_PX`. Only stars a vectorized projection puts within that radius plus
    `_PREFILTER_MARGIN_PX` are measured, each with `wcs.world_to_pixel` as one at a time would: the same pairs and
    residuals as measuring every star, in time that does not grow with detections times stars.

    A detection at a NaN or infinite position, or a star projected to one, lies at a NaN or infinite distance, which
    measuring every star never took as closer: it pairs with nothing, so it is left out. A finite position too large
    for the KD-tree (`_PointIndex`) can pair, and is compared with every star or detection as measuring every star did.
    So is a position that is not a Python float, such as an int past the float range or a float32 (`_coordinates`),
    in the same expression as measuring every star, so it raises where that raised.
    """
    train_ids = {id(det) for det in training_detections}
    stars = list(catalog)
    active = [det for det in detections if not det.is_hot_pixel_impulse]
    associations: list[tuple[SourceDetection, CatalogStar, float, bool]] = []
    used_catalog_ids: set[str] = set()
    if stars and active:
        px, py = _project(
            wcs,
            np.array([s.ra_deg for s in stars], dtype=np.float64),
            np.array([s.dec_deg for s in stars], dtype=np.float64),
        )
        finite = np.flatnonzero(np.isfinite(px) & np.isfinite(py))
        xs, x_refused = _coordinates([det.x_px for det in active])
        ys, y_refused = _coordinates([det.y_px for det in active])
        positions = np.column_stack([xs, ys])
        placed = np.flatnonzero(np.isfinite(positions).all(axis=1))
        near: list[list[int]] = [[] for _ in active]
        if finite.size and placed.size:
            index = _PointIndex(np.column_stack([px[finite], py[finite]]))
            found = index.near(positions[placed], ASSOCIATION_RADIUS_PX + _PREFILTER_MARGIN_PX, 2.0)
            for i, hits in zip(placed, found):
                near[i] = finite[hits].tolist()
        for i in np.flatnonzero(x_refused | y_refused):
            near[i] = list(range(len(stars)))
        exact: dict[int, tuple[float, float]] = {}
        for det, candidates in zip(active, near):
            best_st: CatalogStar | None = None
            best_res = float("inf")
            for j in candidates:
                st = stars[j]
                if st.id in used_catalog_ids:
                    continue
                if j not in exact:
                    exact[j] = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
                sx, sy = exact[j]
                if math.isnan(sx):
                    continue
                res_px = math.hypot(sx - det.x_px, sy - det.y_px)
                if res_px < best_res:
                    best_res = res_px
                    best_st = st
            if best_st is not None and best_res <= ASSOCIATION_RADIUS_PX:
                associations.append((det, best_st, best_res, id(det) in train_ids))
                used_catalog_ids.add(best_st.id)

    raw_residuals = np.array([res_px for _, _, res_px, _ in associations], dtype=np.float64)
    if raw_residuals.size > 0:
        med_r = float(np.median(raw_residuals))
        mad_r = float(np.median(np.abs(raw_residuals - med_r)))
        gate_px = min(INLIER_GATE_MAX_PX, max(INLIER_GATE_MIN_PX, med_r + 3.0 * max(0.35, 1.4826 * mad_r)))
    else:
        gate_px = INLIER_GATE_MAX_PX
    return HeldOutVerification(associations=tuple(associations), inlier_gate_px=gate_px)


def _project(wcs: WCSModel, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`wcs.world_to_pixel` over arrays, NaN where it returns NaN: `WCSModel`'s own formula in numpy. Any subclass
    (which may project otherwise) is projected one star at a time with its own `world_to_pixel`, and so are the
    cases where `WCSModel.world_to_pixel` raises for some stars only (a star at an infinite declination, which
    `math.sin` refuses, or an infinite rotation or a zero scale, refused for the stars in front), so they raise where
    measuring every star did. An infinite tangent declination raises here at `math.sin(dec0)` as it does there."""
    ra_deg = np.asarray(ra_deg, dtype=np.float64)
    dec_deg = np.asarray(dec_deg, dtype=np.float64)
    if (
        type(wcs) is not WCSModel
        or wcs.scale_deg_per_px == 0
        or math.isinf(wcs.rotation_deg)
        or np.isinf(dec_deg).any()
    ):
        pairs = np.array([wcs.world_to_pixel(float(r), float(d)) for r, d in zip(ra_deg, dec_deg)], dtype=np.float64)
        return pairs.reshape(-1, 2)[:, 0], pairs.reshape(-1, 2)[:, 1]
    # A NaN or infinite RA, a NaN declination or a NaN in the WCS gives NaN here, as `world_to_pixel` returns it, with
    # numpy's warnings for it off
    with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
        ra = np.radians(np.mod(ra_deg, 360.0))
        dec = np.radians(dec_deg)
        ra0 = math.radians(float(wcs.crval_ra_deg) % 360.0)
        dec0 = math.radians(float(wcs.crval_dec_deg))
        dra = ra - ra0
        cos_c = math.sin(dec0) * np.sin(dec) + math.cos(dec0) * np.cos(dec) * np.cos(dra)
        front = cos_c > 1e-6
        safe = np.where(front, cos_c, 1.0)
        xi = wcs.parity * (np.cos(dec) * np.sin(dra)) / safe
        eta = (math.cos(dec0) * np.sin(dec) - math.sin(dec0) * np.cos(dec) * np.cos(dra)) / safe
        theta = math.radians(wcs.rotation_deg)
        u = xi * math.cos(theta) - eta * math.sin(theta)
        v = xi * math.sin(theta) + eta * math.cos(theta)
        r2 = u * u + v * v
        distort = 1.0 + wcs.k1 * r2 + wcs.k2 * (r2 * r2)
        x = wcs.crpix_x + np.degrees(u * distort) / wcs.scale_deg_per_px
        y = wcs.crpix_y - np.degrees(v * distort) / wcs.scale_deg_per_px
    return np.where(front, x, np.nan), np.where(front, y, np.nan)


def _indexable(points: np.ndarray) -> np.ndarray:
    """Per row of the 2-D `points`, whether a KD-tree may hold it or be asked with it: both coordinates within
    `_INDEX_LIMIT` of 0. This is the one rule for both indexes here. NaN and the infinities fail the comparison, so
    it keeps out every value cKDTree refuses (not finite) and every value whose squared distance could overflow.

    Why the solutions stay main's: `_PointIndex` returns a row it keeps out to every query, and every row to a query
    it keeps out, for the caller's exact comparison, which is main's loop. `verify_held_out` leaves out only
    positions that are not finite, whose distance in main's loop is NaN or infinite and so never closer. A finite
    position past the limit can pair in main's loop (a WCS with a scale near 1e-160 deg/px puts stars past it), so it
    is compared, not dropped.

    The rule is written for Python floats, which every caller passes (the detector, the solve route, the horizon
    solver). A Python int past the float range, a float32 and any other value that is not a Python float lie outside
    it: through the vectorized projection's margin, which is float64, a float32 that main's loop measures in float32
    could be left out, and the int cannot be converted at all. `_coordinates` marks such a value, and both pairings
    compare its detection with every star in main's own expression, so it pairs, raises or returns as main's loop did."""
    with np.errstate(invalid="ignore"):
        return (np.abs(np.asarray(points, dtype=np.float64).reshape(-1, 2)) <= _INDEX_LIMIT).all(axis=1)


def _coordinates(values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    """`values` as float64, and per value whether it lies outside the rule: anything but a Python float (or a float
    subclass) or a Python int within the float range, such as an int past it, a float32, a string or None. Such a
    value stands as NaN in the array; the caller compares its detection with every star in main's own expression,
    `math.hypot(sx - det.x_px, sy - det.y_px)` on the value itself, so it pairs, raises or is left as main's loop did.
    A Python int within the float range enters that expression as `float(value)`, the value given here."""
    out = np.empty(len(values), dtype=np.float64)
    refused = np.zeros(len(values), dtype=bool)
    for k, value in enumerate(values):
        try:
            if not isinstance(value, (float, int)):
                raise TypeError(type(value).__name__)
            out[k] = float(value)
        except (OverflowError, TypeError):
            out[k] = math.nan
            refused[k] = True
    return out, refused


class _PointIndex:
    """2-D points found by distance without a loop over every point. The rows `_indexable` lets in sit in a KD-tree;
    every other row is returned by every query, and a query `_indexable` keeps out returns every row. A caller then
    compares what it gets exactly as the loop over every point did, so it keeps what that loop kept: the tree leaves
    out only rows farther than the radius, and every value the tree cannot hold reaches the exact comparison."""

    def __init__(self, points: np.ndarray) -> None:
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        inside = _indexable(points)
        self.size = points.shape[0]
        self.tree_rows = np.flatnonzero(inside)
        self.other_rows = np.flatnonzero(~inside)
        self.tree = cKDTree(points[self.tree_rows]) if self.tree_rows.size else None

    def near(self, queries: np.ndarray, r: float, p: float) -> list[np.ndarray]:
        """Per row of `queries`, the ascending indices of the points within `r` (Minkowski `p`) and of the rows the
        tree does not hold; every index for a query the tree cannot be asked with."""
        queries = np.asarray(queries, dtype=np.float64).reshape(-1, 2)
        out = [np.arange(self.size, dtype=np.int64) for _ in range(queries.shape[0])]
        asked = np.flatnonzero(_indexable(queries))
        if asked.size == 0:
            return out
        found = self.tree.query_ball_point(queries[asked], r=r, p=p) if self.tree is not None else [[] for _ in asked]
        for i, hits in zip(asked, found):
            in_tree = self.tree_rows[np.array(sorted(hits), dtype=np.int64)]
            out[i] = np.union1d(in_tree, self.other_rows) if self.other_rows.size else in_tree
        return out


class _RatioIndex:
    """The catalog triangles' two side ratios in a `_PointIndex`, so a detector triangle finds its matches without a
    loop over every catalog triangle."""

    def __init__(self, ratios: Sequence[tuple[float, float]]) -> None:
        self.ratios = np.asarray(ratios, dtype=np.float64).reshape(-1, 2)
        self.index = _PointIndex(self.ratios)

    def lookup(self, dr1: float, dr2: float) -> np.ndarray:
        """Indices, ascending, of the triangles whose two ratios are each within `TRIANGLE_RATIO_TOLERANCE` of
        (dr1, dr2): a box query widened by 1e-9, then the comparison the loop over every triangle made.

        That loop skipped a triangle when `abs(dr - r) > TRIANGLE_RATIO_TOLERANCE`, which is False when the difference
        is NaN: a NaN ratio kept every triangle on its axis, and an infinite one kept a NaN or the same infinity. Such
        a ratio, and any too large for the tree, is compared with every triangle (`_PointIndex`)."""
        hits = self.index.near(np.array([dr1, dr2], dtype=np.float64), TRIANGLE_RATIO_TOLERANCE + 1e-9, np.inf)[0]
        return self._kept(hits, dr1, dr2)

    def _kept(self, hits: np.ndarray, dr1: float, dr2: float) -> np.ndarray:
        """`hits` without the triangles the loop's comparison skipped, in their order."""
        if hits.size == 0:
            return hits
        with np.errstate(invalid="ignore", over="ignore"):
            outside = (np.abs(dr1 - self.ratios[hits, 0]) > TRIANGLE_RATIO_TOLERANCE) | (
                np.abs(dr2 - self.ratios[hits, 1]) > TRIANGLE_RATIO_TOLERANCE
            )
        return hits[~outside]


def _greedy_pairs(
    wcs: WCSModel,
    detections: Sequence[SourceDetection],
    stars: Sequence[CatalogStar],
    ra_deg: np.ndarray,
    dec_deg: np.ndarray,
    radius_px: float,
) -> list[tuple[SourceDetection, CatalogStar, float]]:
    """A candidate's consensus pairs: each detection in turn takes the nearest star not yet taken (the first in
    order on a tie) when it lies closer than `radius_px` under `wcs.world_to_pixel`. `ra_deg` and `dec_deg` are the
    stars' positions: all stars are projected once per candidate (`_project`), and only those it puts within
    `radius_px` plus `_PREFILTER_MARGIN_PX` of a detection are measured with `wcs.world_to_pixel`, as `verify_held_out`
    measures them. The pairs and distances are the ones measuring every star with `wcs.world_to_pixel` gives."""
    if not detections or len(stars) == 0:
        return []
    px, py = _project(wcs, ra_deg, dec_deg)
    dx, x_refused = _coordinates([d.x_px for d in detections])
    dy, y_refused = _coordinates([d.y_px for d in detections])
    # A NaN or infinite detection or projection gives a NaN or infinite distance, never closer than the radius
    with np.errstate(invalid="ignore", over="ignore"):
        dist = np.hypot(px[None, :] - dx[:, None], py[None, :] - dy[:, None])
    close = np.isfinite(dist) & (dist < radius_px + _PREFILTER_MARGIN_PX)
    close[x_refused | y_refused, :] = True
    ids = [s.id for s in stars]
    used: set[str] = set()
    exact: dict[int, tuple[float, float]] = {}
    pairs: list[tuple[SourceDetection, CatalogStar, float]] = []
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


def _estimate_initial_wcs_from_triplet(
    det_triplet: tuple[SourceDetection, SourceDetection, SourceDetection],
    star_triplet: tuple[CatalogStar, CatalogStar, CatalogStar],
    width: int,
    height: int,
) -> WCSModel | None:
    """Estimate initial similarity TAN WCSModel from a 3-star correspondence."""
    s0, s1, s2 = star_triplet
    d0, d1, d2 = det_triplet

    v0 = np.array(ra_dec_to_unit_vector(s0.ra_deg, s0.dec_deg))
    v1 = np.array(ra_dec_to_unit_vector(s1.ra_deg, s1.dec_deg))
    v2 = np.array(ra_dec_to_unit_vector(s2.ra_deg, s2.dec_deg))
    v_mean = v0 + v1 + v2
    if np.linalg.norm(v_mean) < 1e-6:
        return None
    ra_c, dec_c = unit_vector_to_ra_dec(v_mean)

    ang01 = angular_separation_deg(s0.ra_deg, s0.dec_deg, s1.ra_deg, s1.dec_deg)
    px01 = math.hypot(d1.x_px - d0.x_px, d1.y_px - d0.y_px)
    if px01 < 10.0 or ang01 < 0.5:
        return None
    scale = ang01 / px01
    if not (0.002 <= scale <= 0.25):
        return None

    best_wcs: WCSModel | None = None
    best_err = float("inf")
    crpix_x, crpix_y = width / 2.0, height / 2.0

    for parity in (-1, 1):
        temp_wcs = WCSModel(
            crval_ra_deg=ra_c, crval_dec_deg=dec_c, crpix_x=0.0, crpix_y=0.0,
            scale_deg_per_px=1.0, rotation_deg=0.0, parity=parity, width=width, height=height,
        )
        t_pts = []
        valid = True
        for st in star_triplet:
            tx, ty = temp_wcs.world_to_pixel(st.ra_deg, st.dec_deg)
            if math.isnan(tx) or math.isnan(ty):
                valid = False
                break
            t_pts.append((tx, -ty))
        if not valid:
            continue

        i_pts = [((d.x_px - crpix_x) * scale, -(d.y_px - crpix_y) * scale) for d in det_triplet]
        tc_x = sum(p[0] for p in t_pts) / 3.0
        tc_y = sum(p[1] for p in t_pts) / 3.0
        ic_x = sum(p[0] for p in i_pts) / 3.0
        ic_y = sum(p[1] for p in i_pts) / 3.0

        cs_sum, sn_sum = 0.0, 0.0
        for (tx, ty), (ix, iy) in zip(t_pts, i_pts):
            dx_t, dy_t = tx - tc_x, ty - tc_y
            dx_i, dy_i = ix - ic_x, iy - ic_y
            cs_sum += dx_t * dx_i + dy_t * dy_i
            sn_sum += dx_t * dy_i - dy_t * dx_i

        rot_rad = math.atan2(sn_sum, cs_sum)
        rot_deg = math.degrees(rot_rad)
        cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)
        shift_u = (tc_x * cos_r - tc_y * sin_r) - ic_x
        shift_v = (tc_x * sin_r + tc_y * cos_r) - ic_y
        center_x_tan = shift_u * cos_r + shift_v * sin_r
        center_y_tan = -shift_u * sin_r + shift_v * cos_r
        crval_ra, crval_dec = temp_wcs.pixel_to_world(center_x_tan, -center_y_tan)

        cand_wcs = WCSModel(
            crval_ra_deg=crval_ra, crval_dec_deg=crval_dec, crpix_x=crpix_x, crpix_y=crpix_y,
            scale_deg_per_px=scale, rotation_deg=rot_deg, k1=0.0, k2=0.0,
            parity=parity, width=width, height=height,
        )
        err = 0.0
        for d, st in zip(det_triplet, star_triplet):
            px, py = cand_wcs.world_to_pixel(st.ra_deg, st.dec_deg)
            if math.isnan(px):
                err = 1e9
                break
            err += math.hypot(px - d.x_px, py - d.y_px)

        if err < best_err:
            best_err = err
            best_wcs = cand_wcs

    return best_wcs


def _refine_wcs_least_squares(
    initial_wcs: WCSModel,
    train_pairs: list[tuple[SourceDetection, CatalogStar]],
) -> WCSModel:
    """Refine WCS parameters on the training subset ONLY using robust nonlinear least squares."""
    if len(train_pairs) < 3:
        return initial_wcs

    crpix_x, crpix_y = initial_wcs.crpix_x, initial_wcs.crpix_y
    parity, width, height = initial_wcs.parity, initial_wcs.width, initial_wcs.height
    x0 = np.array(
        [
            initial_wcs.crval_ra_deg, initial_wcs.crval_dec_deg,
            initial_wcs.scale_deg_per_px, initial_wcs.rotation_deg,
            initial_wcs.k1, initial_wcs.k2,
        ],
        dtype=np.float64,
    )

    def residuals_fn(params: np.ndarray) -> np.ndarray:
        ra0, dec0, scale, rot, k1, k2 = params
        model = WCSModel(
            crval_ra_deg=float(ra0) % 360.0,
            crval_dec_deg=max(-89.9, min(89.9, float(dec0))),
            crpix_x=crpix_x, crpix_y=crpix_y,
            scale_deg_per_px=max(1e-4, float(scale)),
            rotation_deg=float(rot), k1=float(k1), k2=float(k2),
            parity=parity, width=width, height=height,
        )
        res = []
        for det, star in train_pairs:
            px, py = model.world_to_pixel(star.ra_deg, star.dec_deg)
            if math.isnan(px) or math.isnan(py):
                res.extend([500.0, 500.0])
            else:
                res.extend([px - det.x_px, py - det.y_px])
        return np.asarray(res, dtype=np.float64)

    bounds_lower = np.array(
        [
            initial_wcs.crval_ra_deg - 25.0,
            max(-89.5, initial_wcs.crval_dec_deg - 25.0),
            initial_wcs.scale_deg_per_px * 0.7,
            initial_wcs.rotation_deg - 45.0,
            -0.24, -0.24,
        ],
        dtype=np.float64,
    )
    bounds_upper = np.array(
        [
            initial_wcs.crval_ra_deg + 25.0,
            min(89.5, initial_wcs.crval_dec_deg + 25.0),
            initial_wcs.scale_deg_per_px * 1.3,
            initial_wcs.rotation_deg + 45.0,
            0.24, 0.24,
        ],
        dtype=np.float64,
    )
    opt = optimize.least_squares(
        residuals_fn, x0, bounds=(bounds_lower, bounds_upper),
        loss="soft_l1", f_scale=1.5, max_nfev=250,
    )
    ra0, dec0, scale, rot, k1, k2 = opt.x
    return WCSModel(
        crval_ra_deg=float(ra0) % 360.0, crval_dec_deg=float(dec0),
        crpix_x=crpix_x, crpix_y=crpix_y, scale_deg_per_px=float(scale),
        rotation_deg=float(rot), k1=float(k1), k2=float(k2),
        parity=parity, width=width, height=height,
    )


def _pixel_only_finding(
    idx: int,
    det: SourceDetection,
    solution_id: str,
    source_sha256: str,
    limitation: str,
) -> CelestialFindingRecord:
    """An `observed` finding located by its pixels alone: without a verified WCS it has no sky position."""
    return CelestialFindingRecord(
        id=f"finding-obs-{idx + 1}",
        observation_solution_id=solution_id,
        evidence_class="observed",
        title=f"Observed Point Source #{idx + 1}",
        subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
        source_detection=det,
        verified_by_holdout=False,
        limitations=(limitation,),
        source_sha256=source_sha256,
    )


def _build_unresolved_solution(
    detections: list[SourceDetection],
    solution_id: str,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float,
    obs_utc_iso: str | None,
    observer_lat: float | None,
    observer_lon: float | None,
    warning_msg: str,
    limitation: str,
    solver: SolverIdentity,
) -> ObservationSolution:
    """Construct an honest `unresolved` ObservationSolution: every detection is an `observed`
    finding at its measured pixel position, with no sky position because there is no WCS."""
    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status="unresolved",
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=None,
        solver=solver,
        quality=SolutionQuality(
            detected_candidate_count=len(detections),
            matched_inlier_count=0,
            held_out_pair_count=0,
            held_out_inliers=0,
            median_residual_px=None,
            p95_residual_px=None,
            median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        findings=[
            _pixel_only_finding(idx, det, solution_id, source_sha256, limitation)
            for idx, det in enumerate(detections)
        ],
        warnings=[warning_msg],
    )


def solve_and_verify_field(
    detections: list[SourceDetection],
    width: int,
    height: int,
    *,
    obs_utc_iso: str | None = None,
    observer_lat: float | None = None,
    observer_lon: float | None = None,
    catalog_stars: list[CatalogStar] | None = None,
    hint_ra_deg: float | None = None,
    hint_dec_deg: float | None = None,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-primary",
    on_candidate: Callable[[int, int], None] | None = None,
) -> ObservationSolution:
    """Blind / hint-assisted geometric plate solver with held-out WCS verification (`verify_held_out`).

    `source_asset_id` and `source_sha256` name the recording the detections were measured in; there is no
    default, because the solver cannot know them. `obs_utc_iso`, `observer_lat` and `observer_lon` are
    optional: when present, catalog matches also carry topocentric altitude and azimuth.
    """
    require_source_sha256(source_sha256)
    if (observer_lat is None) != (observer_lon is None):
        raise ValueError("observer_lat and observer_lon must both be provided or both be None.")
    catalog = list(catalog_stars) if catalog_stars is not None else get_catalog_stars()
    if hint_ra_deg is not None and hint_dec_deg is not None:
        filtered = [
            s for s in catalog
            if angular_separation_deg(s.ra_deg, s.dec_deg, hint_ra_deg, hint_dec_deg) <= 68.0
        ]
        if len(filtered) >= 8:
            catalog = filtered

    psf_detections = [d for d in detections if not d.is_hot_pixel_impulse]
    if len(psf_detections) < 6:
        psf_detections = list(detections)

    if len(detections) < 6 or len(psf_detections) < 4:
        return _build_unresolved_solution(
            detections, solution_id, source_asset_id, source_sha256, frame_time_sec,
            obs_utc_iso, observer_lat, observer_lon,
            f"Insufficient point sources ({len(detections)} detected) for independent WCS calibration.",
            "Pixel position only: too few point sources for a verified WCS to place it on the sky.",
            triangle_solver_identity(catalog),
        )

    cat_pool = sorted(catalog, key=lambda s: s.mag)[:18]
    cat_triangles: list[tuple[int, int, int, float, float, float]] = []
    for i, j, k in itertools.combinations(range(len(cat_pool)), 3):
        s_i, s_j, s_k = cat_pool[i], cat_pool[j], cat_pool[k]
        d_ij = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_j.ra_deg, s_j.dec_deg)
        d_jk = angular_separation_deg(s_j.ra_deg, s_j.dec_deg, s_k.ra_deg, s_k.dec_deg)
        d_ik = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_k.ra_deg, s_k.dec_deg)
        sides = sorted([d_ij, d_jk, d_ik])
        if sides[0] < 2.5 or sides[2] > 62.0:
            continue
        pairs = [((i, j), d_ij), ((j, k), d_jk), ((i, k), d_ik)]
        pairs.sort(key=lambda p: p[1], reverse=True)
        longest_set, second_set = set(pairs[0][0]), set(pairs[1][0])
        v0 = (longest_set & second_set).pop()
        v1 = (longest_set - {v0}).pop()
        v2 = ({i, j, k} - {v0, v1}).pop()
        cat_triangles.append((v0, v1, v2, pairs[1][1] / pairs[0][1], pairs[2][1] / pairs[0][1], pairs[0][1]))
    ratio_index = _RatioIndex([(cr1, cr2) for _, _, _, cr1, cr2, _ in cat_triangles])
    cat_ra = np.array([s.ra_deg for s in catalog], dtype=np.float64)
    cat_dec = np.array([s.dec_deg for s in catalog], dtype=np.float64)

    det_pool = sorted(psf_detections, key=lambda d: d.snr, reverse=True)[:14]
    best_consensus_pairs: list[tuple[SourceDetection, CatalogStar]] = []
    best_initial_wcs: WCSModel | None = None
    triplets = list(itertools.combinations(range(len(det_pool)), 3))
    candidate_total = len(triplets)
    if on_candidate is not None:
        on_candidate(0, candidate_total)

    for evaluated, (a, b, c) in enumerate(triplets, start=1):
        da, db, dc = det_pool[a], det_pool[b], det_pool[c]
        p_ab = math.hypot(da.x_px - db.x_px, da.y_px - db.y_px)
        p_bc = math.hypot(db.x_px - dc.x_px, db.y_px - dc.y_px)
        p_ac = math.hypot(da.x_px - dc.x_px, da.y_px - dc.y_px)
        px_pairs = [((a, b), p_ab), ((b, c), p_bc), ((a, c), p_ac)]
        px_pairs.sort(key=lambda p: p[1], reverse=True)
        if px_pairs[2][1] < 25.0:
            if on_candidate is not None:
                on_candidate(evaluated, candidate_total)
            continue

        longest_set, second_set = set(px_pairs[0][0]), set(px_pairs[1][0])
        u0 = (longest_set & second_set).pop()
        u1 = (longest_set - {u0}).pop()
        u2 = ({a, b, c} - {u0, u1}).pop()

        dr1 = px_pairs[1][1] / px_pairs[0][1]
        dr2 = px_pairs[2][1] / px_pairs[0][1]
        det_tri = (det_pool[u0], det_pool[u1], det_pool[u2])

        for t in ratio_index.lookup(dr1, dr2):
            v0, v1, v2 = cat_triangles[t][:3]
            cand_wcs = _estimate_initial_wcs_from_triplet(
                det_tri, (cat_pool[v0], cat_pool[v1], cat_pool[v2]), width, height
            )
            if cand_wcs is None:
                continue

            matched_pairs = _greedy_pairs(cand_wcs, psf_detections, catalog, cat_ra, cat_dec, 18.0)
            if len(matched_pairs) > len(best_consensus_pairs):
                best_consensus_pairs = [(d, s) for d, s, _ in matched_pairs]
                best_initial_wcs = cand_wcs

        if on_candidate is not None:
            on_candidate(evaluated, candidate_total)
        if len(best_consensus_pairs) >= max(11, len(cat_pool) - 3):
            break

    if best_initial_wcs is None or len(best_consensus_pairs) < 4:
        return _build_unresolved_solution(
            detections, solution_id, source_asset_id, source_sha256, frame_time_sec,
            obs_utc_iso, observer_lat, observer_lon,
            "Triangle pattern matching found no consistent WCS solution.",
            "Pixel position only: no catalog pattern matched, so no WCS places it on the sky.",
            triangle_solver_identity(catalog),
        )

    cx0, cy0 = width / 2.0, height / 2.0
    sorted_consensus = sorted(
        best_consensus_pairs,
        key=lambda pair: math.hypot(pair[0].x_px - cx0, pair[0].y_px - cy0),
        reverse=True,
    )
    train_subset = sorted_consensus[: min(4, len(sorted_consensus))]
    refined_wcs = _refine_wcs_least_squares(best_initial_wcs, train_subset)

    verification = verify_held_out(refined_wcs, detections, catalog, [d for d, _ in train_subset])
    verified_matches = verification.inliers
    held_out_inliers = verification.held_out_inliers
    # None when no held-out star paired with a catalog star: there is no residual to report
    median_residual_px = verification.median_residual_px
    p95_residual_px = verification.p95_residual_px
    median_residual_arcsec = (
        median_residual_px * refined_wcs.scale_deg_per_px * 3600.0 if median_residual_px is not None else None
    )
    passed_verification = verification.passed
    status = "calibrated" if passed_verification else ("ambiguous" if len(verified_matches) >= 4 else "unresolved")

    findings_list: list[CelestialFindingRecord] = []
    warnings: list[str] = []
    paired_by_id = {id(det): (st, res_px) for det, st, res_px, _ in verification.associations}
    impulse_count = 0
    outside_gate_count = 0
    no_catalog_star_count = 0

    has_time_and_place = (
        obs_utc_iso is not None and observer_lat is not None and observer_lon is not None
    )

    for idx, det in enumerate(detections):
        if passed_verification and id(det) in verified_matches:
            st, res_px, is_train = verified_matches[id(det)]
            alt_deg, az_deg = (
                icrs_to_altaz(st.ra_deg, st.dec_deg, observer_lat, observer_lon, obs_utc_iso)
                if has_time_and_place
                else (None, None)
            )
            ang_res_arcsec = res_px * refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-{st.id}",
                    observation_solution_id=solution_id,
                    evidence_class="catalog_matched",
                    title=st.common_name,
                    subtitle=f"{st.bayer_designation} · {st.median_pc:.1f} pc [{st.lower_pc:.1f} to {st.upper_pc:.1f} pc]",
                    ra_deg=st.ra_deg, dec_deg=st.dec_deg, alt_deg=alt_deg, az_deg=az_deg,
                    uncertainty_arcsec=max(0.5, ang_res_arcsec),
                    source_detection=det, catalog_star=st,
                    pixel_residual_px=res_px, angular_residual_arcsec=ang_res_arcsec,
                    verified_by_holdout=not is_train,
                    limitations=(f"Distance is {st.distance_source}, not measured from this image.",),
                    source_sha256=source_sha256,
                )
            )
        elif passed_verification:
            if det.is_hot_pixel_impulse:
                impulse_count += 1
                unmatched_reason = (
                    f"Skipped by catalog matching as a hot-pixel impulse "
                    f"(FWHM {det.fwhm_px:.2f} px, peak-to-total flux ratio {det.peak_to_total_ratio:.2f})."
                )
            elif id(det) in paired_by_id:
                outside_gate_count += 1
                paired_star, res_px = paired_by_id[id(det)]
                unmatched_reason = (
                    f"Paired with {paired_star.common_name} at {res_px:.2f} px residual, "
                    f"outside the {verification.inlier_gate_px:.2f} px inlier gate."
                )
            else:
                no_catalog_star_count += 1
                unmatched_reason = (
                    f"No star of the {len(catalog)}-star catalog sits within {ASSOCIATION_RADIUS_PX:g} px "
                    f"of where the WCS puts one."
                )
            ra_est, dec_est = refined_wcs.pixel_to_world(det.x_px, det.y_px)
            arcsec_per_px = refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-obs-{idx + 1}",
                    observation_solution_id=solution_id,
                    evidence_class="observed",
                    title=f"Observed Point Source #{idx + 1}",
                    subtitle=f"Unmatched sensor detection at ({det.x_px:.1f}, {det.y_px:.1f}) px",
                    ra_deg=ra_est, dec_deg=dec_est,
                    uncertainty_arcsec=math.hypot(median_residual_arcsec, det.centroid_uncertainty_px * arcsec_per_px),
                    source_detection=det, catalog_star=None, verified_by_holdout=False,
                    limitations=(unmatched_reason,),
                    source_sha256=source_sha256,
                )
            )
        else:
            findings_list.append(
                _pixel_only_finding(
                    idx, det, solution_id, source_sha256,
                    "Pixel position only: the WCS fit failed held-out verification, so it places nothing on the sky.",
                )
            )

    unmatched_total = impulse_count + outside_gate_count + no_catalog_star_count
    if unmatched_total > 0 and passed_verification:
        unmatched_parts: list[str] = []
        if impulse_count > 0:
            unmatched_parts.append(f"{impulse_count} skipped as hot-pixel impulse(s)")
        if outside_gate_count > 0:
            unmatched_parts.append(
                f"{outside_gate_count} paired within {ASSOCIATION_RADIUS_PX:g} px "
                f"but outside the {verification.inlier_gate_px:.2f} px inlier gate"
            )
        if no_catalog_star_count > 0:
            unmatched_parts.append(
                f"{no_catalog_star_count} with no star of the {len(catalog)}-star catalog "
                f"within {ASSOCIATION_RADIUS_PX:g} px"
            )
        warnings.append(f"{unmatched_total} unmatched sensor detection(s): {', '.join(unmatched_parts)}.")
    if not passed_verification:
        warnings.append("WCS solution did not pass independent held-out calibration gate.")

    return ObservationSolution(
        id=solution_id, source_asset_id=source_asset_id, source_sha256=source_sha256,
        frame_time_sec=frame_time_sec, revision=1, status=status,
        observed_at_utc=obs_utc_iso, observer_lat=observer_lat, observer_lon=observer_lon,
        wcs=refined_wcs if passed_verification else None,
        solver=triangle_solver_identity(catalog),
        quality=SolutionQuality(
            detected_candidate_count=len(detections),
            matched_inlier_count=len(verified_matches) if passed_verification else 0,
            held_out_pair_count=verification.held_out_pair_count,
            held_out_inliers=held_out_inliers,
            median_residual_px=median_residual_px, p95_residual_px=p95_residual_px,
            median_residual_arcsec=median_residual_arcsec,
            passed_independent_verification=passed_verification,
        ),
        findings=findings_list, warnings=warnings,
    )


__all__ = [
    "CelestialFindingRecord", "HeldOutVerification", "ObservationSolution", "PhoneCameraWCS",
    "SolutionQuality", "SourceDetection", "VideoStackCalibrationResult", "WCSModel",
    "_build_gaussian_psf_kernel", "_estimate_background_and_noise",
    "_estimate_initial_wcs_from_triplet", "_estimate_subpixel_shift_phase_corr",
    "_extract_registration_signal", "_refine_wcs_least_squares", "_to_luma_2d",
    "detect_point_sources", "evaluate_coincidence_gate", "render_synthetic_starfield",
    "solve_and_verify_field", "solve_horizon_and_beacon_pose", "solve_phone_photo",
    "stack_video_frames_for_calibration", "verify_held_out",
]
