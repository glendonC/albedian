"""Deterministic astrometric geometric plate solver with held-out WCS verification,
and public re-exports for point-source extraction, video calibration stacking, and horizon solving.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import itertools
import math
from typing import Callable, Sequence

import numpy as np
from scipy import optimize
from scipy.spatial import cKDTree
from scipy.special import bdtrc

from services.science import catalog_tiles as ct
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
    _max_valid_radial,
    evaluate_coincidence_gate,
    solve_phone_photo,
    tile_stars_above_horizon,
    tile_stars_all_sky,
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


def triangle_solver_identity(catalog: Sequence[CatalogStar], pool_description: str | None = None) -> SolverIdentity:
    """The plate solver's name, and the catalog it matched against, counted from the stars it was given.

    Without `pool_description` the stars are the pinned bright stars; with it they are a pool of the G 12 tiles that
    the description names (`_pool_description`).
    """
    releases = " and ".join(sorted({star.release_version for star in catalog}))
    if pool_description is None:
        release = f"{len(catalog)} pinned bright stars ({releases} rows)"
    else:
        release = f"{len(catalog)} {pool_description} ({releases} rows of the G 12 tiles)"
    return SolverIdentity(name="AlbedianTriangleSolver", version="1.0.0", catalog_release=release)

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
# The pairing above runs on candidate stars within this margin of the 8 px radius by a vectorized projection, then
# measures each candidate with `WCSModel.world_to_pixel` as before; the two projections agree to about 1e-9 px
_PREFILTER_MARGIN_PX = 1e-3

# The triangle search's values before solver-10k, kept for both pools [verified: this file at db972af7]
TRIANGLE_RATIO_TOLERANCE = 0.045
TRIANGLE_MIN_SIDE_DEG = 2.5
TRIANGLE_MAX_SIDE_DEG = 62.0
DETECTOR_POOL_SIZE = 14
DETECTOR_MIN_SIDE_PX = 25.0
PINNED_POOL_SIZE = 18
PINNED_MATCH_RADIUS_PX = 18.0

# solver-10k: the tile catalog's pool, searched when the pinned stars do not calibrate.
# Consensus is scored on the brightest detections only [proposed, the lead's plan: 150]
SCORING_DETECTION_COUNT = 150
# Stars below this apparent altitude stay out of the pool [proposed, the lead's plan]
TILE_MIN_ALTITUDE_DEG = 10.0
# The V limit rule: the shallowest step from the floor whose expected count in a field-sized cone reaches the
# detections used (`_tile_v_limit`); floors and step from the lead's plan [proposed], the cap is the depth the
# phone_photo_solver helpers keep (TILE_POOL_CACHED_V_LIMIT) [proposed]
TILE_V_FLOOR_ABOVE_HORIZON = 4.5
TILE_V_FLOOR_ALL_SKY = 3.5
TILE_V_CAP = 6.5
TILE_V_STEP = 0.1
# A field-sized cone: half the longest catalog triangle side, so a cone holds any triangle the search keeps
# [proposed]; the sky cells are HEALPix nside 4 (192 cells) [proposed, the lead's plan]
FIELD_CONE_RADIUS_DEG = 31.0
TILE_CELL_ORDER = 2
# Triangles come from the brightest stars of each cell's field-sized cone [proposed, the lead's plan: 8 to 10]
TILE_TRIANGLE_STARS_PER_CELL = 10
# The first consensus score projects the brightest stars within this cone of the sky cell nearest the candidate's
# field centre [proposed: a 64 x 48 deg field's corners lie 40 deg from its centre]
TILE_SCORING_STARS = 40
TILE_SCORING_CONE_DEG = 45.0
# Matches count within today's 18 px on a 960 x 540 frame, scaled with the frame's diagonal [proposed]
TILE_MATCH_RADIUS_MIN_PX = 6.0
# A candidate goes on to full verification when at least CONSENSUS_MIN_MATCHES of its projected stars (not its own
# three) land on a detection and that many or more would land by chance with probability at most
# CONSENSUS_MAX_CHANCE (binomial, with the chance of one landing from the scored detections' density) [proposed]
CONSENSUS_MIN_MATCHES = 4
CONSENSUS_MAX_CHANCE = 1e-3
# At most this many candidates are verified, best first [proposed: bounds the time on frames with no sky]
TILE_MAX_VERIFIED = 40
# A tile-pool fit holds only over the frame's field: the cone of the frame corners' largest angle from its tangent
# point plus FIELD_MARGIN_DEG. Its held-out verification and the coincidence check's count of in-frame stars take
# only the pool's stars in that cone, and the fit is refused when r (1 + k1 r^2 + k2 r^4), r the tangent-plane radius,
# stops increasing before the cone's edge (`_folds_inside_field`): past that turn two directions share a pixel. Before
# this check, stars 62 to 80 deg out landed on detections in frames whose corners were 33.5 to 42.1 deg out [tested,
# simulated].
# The margin is the one the cone the consensus pairs come from has past the candidate's half diagonal [proposed: so
# every star verification can pair lies where the fit is one to one]
FIELD_MARGIN_DEG = 1.0
# Training pairs for a tile-pool fit, at most, split as phone_photo_solver.py splits its pairs (which takes up to
# 8) [proposed: on 7 simulated frames (this unit's Cygnus and Crux, Crux 12 h later, Cygnus flipped either way, the
# benchmark's pos-002 and pos-024) the median field error was 0.10 to 0.67 px with 16 and 0.05 to 1.20 px with 8,
# lower with 16 on 6 of the 7 and with 8 on Crux alone, 0.05 against 0.36 px; tested, 2026-11-11]
TILE_MAX_TRAINING_PAIRS = 16


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
    """
    train_ids = {id(det) for det in training_detections}
    stars = list(catalog)
    active = [det for det in detections if not det.is_hot_pixel_impulse]
    associations: list[tuple[SourceDetection, CatalogStar, float, bool]] = []
    used_catalog_ids: set[str] = set()
    if stars and active:
        px, py = _project(wcs, np.array([s.ra_deg for s in stars]), np.array([s.dec_deg for s in stars]))
        finite = np.flatnonzero(np.isfinite(px) & np.isfinite(py))
        near: list[list[int]] = [[] for _ in active]
        if finite.size:
            tree = cKDTree(np.column_stack([px[finite], py[finite]]))
            found = tree.query_ball_point(
                np.array([[det.x_px, det.y_px] for det in active], dtype=np.float64),
                r=ASSOCIATION_RADIUS_PX + _PREFILTER_MARGIN_PX,
            )
            near = [sorted(int(finite[k]) for k in hits) for hits in found]
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
    """`wcs.world_to_pixel` over arrays: NaN where it returns NaN. A plain `WCSModel` is projected with its own
    formula in numpy; any other model uses its batch projection or, failing that, one star at a time."""
    ra_deg = np.asarray(ra_deg, dtype=np.float64)
    dec_deg = np.asarray(dec_deg, dtype=np.float64)
    if type(wcs) is not WCSModel:
        if hasattr(wcs, "world_to_pixel_batch"):
            x, y = wcs.world_to_pixel_batch(ra_deg, dec_deg)
            return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        pairs = np.array([wcs.world_to_pixel(float(r), float(d)) for r, d in zip(ra_deg, dec_deg)], dtype=np.float64)
        return pairs.reshape(-1, 2)[:, 0], pairs.reshape(-1, 2)[:, 1]
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


def _estimate_initial_wcs_from_triplet(
    det_triplet: tuple[SourceDetection, SourceDetection, SourceDetection],
    star_triplet: tuple[CatalogStar, CatalogStar, CatalogStar],
    width: int,
    height: int,
    parities: Sequence[int] = (-1, 1),
) -> WCSModel | None:
    """Estimate initial similarity TAN WCSModel from a 3-star correspondence, trying each of `parities`."""
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

    for parity in parities:
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
    fit_distortion: bool = True,
) -> WCSModel:
    """Refine WCS parameters on the training subset ONLY using robust nonlinear least squares. Without
    `fit_distortion` only the tangent point, scale and rotation move; k1 and k2 stay the initial WCS's."""
    if len(train_pairs) < 3:
        return initial_wcs
    n_params = 6 if fit_distortion else 4

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
        ra0, dec0, scale, rot, k1, k2 = params if len(params) == 6 else (*params, initial_wcs.k1, initial_wcs.k2)
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
        residuals_fn, x0[:n_params], bounds=(bounds_lower[:n_params], bounds_upper[:n_params]),
        loss="soft_l1", f_scale=1.5, max_nfev=250,
    )
    ra0, dec0, scale, rot, k1, k2 = opt.x if n_params == 6 else (*opt.x, initial_wcs.k1, initial_wcs.k2)
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


def _detector_triangles(
    det_pool: Sequence[SourceDetection],
) -> list[tuple[tuple[SourceDetection, SourceDetection, SourceDetection], float, float] | None]:
    """Every triplet of `det_pool` in combination order: its detections ordered as the catalog triangles are (the
    vertex shared by the two longest sides, the other end of the longest, the last) and its two side ratios, or
    None when its shortest side is under `DETECTOR_MIN_SIDE_PX`."""
    out: list[tuple[tuple[SourceDetection, SourceDetection, SourceDetection], float, float] | None] = []
    for a, b, c in itertools.combinations(range(len(det_pool)), 3):
        da, db, dc = det_pool[a], det_pool[b], det_pool[c]
        p_ab = math.hypot(da.x_px - db.x_px, da.y_px - db.y_px)
        p_bc = math.hypot(db.x_px - dc.x_px, db.y_px - dc.y_px)
        p_ac = math.hypot(da.x_px - dc.x_px, da.y_px - dc.y_px)
        px_pairs = [((a, b), p_ab), ((b, c), p_bc), ((a, c), p_ac)]
        px_pairs.sort(key=lambda p: p[1], reverse=True)
        if px_pairs[2][1] < DETECTOR_MIN_SIDE_PX:
            out.append(None)
            continue
        longest_set, second_set = set(px_pairs[0][0]), set(px_pairs[1][0])
        u0 = (longest_set & second_set).pop()
        u1 = (longest_set - {u0}).pop()
        u2 = ({a, b, c} - {u0, u1}).pop()
        out.append(((det_pool[u0], det_pool[u1], det_pool[u2]), px_pairs[1][1] / px_pairs[0][1], px_pairs[2][1] / px_pairs[0][1]))
    return out


@dataclass(frozen=True)
class _TriangleIndex:
    """Catalog triangles (vertex indices into their pool, ordered as `_detector_triangles` orders detections) with a
    KD-tree on their two side ratios. `cell` is the sky cell a tile triangle was built from (-1 for the pinned)."""

    v0: np.ndarray
    v1: np.ndarray
    v2: np.ndarray
    ratios: np.ndarray
    cell: np.ndarray
    tree: cKDTree | None

    @classmethod
    def from_ratios(
        cls,
        ratios: np.ndarray,
        v0: np.ndarray | None = None,
        v1: np.ndarray | None = None,
        v2: np.ndarray | None = None,
        cell: np.ndarray | None = None,
    ) -> "_TriangleIndex":
        ratios = np.asarray(ratios, dtype=np.float64).reshape(-1, 2)
        n = ratios.shape[0]
        empty = np.zeros(n, dtype=np.int64)
        return cls(
            v0=empty if v0 is None else np.asarray(v0, dtype=np.int64),
            v1=empty if v1 is None else np.asarray(v1, dtype=np.int64),
            v2=empty if v2 is None else np.asarray(v2, dtype=np.int64),
            ratios=ratios,
            cell=np.full(n, -1, dtype=np.int64) if cell is None else np.asarray(cell, dtype=np.int64),
            tree=cKDTree(ratios) if n else None,
        )

    def lookup(self, dr1: float, dr2: float) -> np.ndarray:
        """Indices, ascending, of the triangles whose two ratios are each within `TRIANGLE_RATIO_TOLERANCE` of
        (dr1, dr2): the box query, widened by 1e-9, then the same comparison the loop before solver-10k made."""
        if self.tree is None:
            return np.zeros(0, dtype=np.int64)
        found = self.tree.query_ball_point([dr1, dr2], r=TRIANGLE_RATIO_TOLERANCE + 1e-9, p=np.inf)
        hits = np.array(sorted(found), dtype=np.int64)
        if hits.size == 0:
            return hits
        outside = (np.abs(dr1 - self.ratios[hits, 0]) > TRIANGLE_RATIO_TOLERANCE) | (
            np.abs(dr2 - self.ratios[hits, 1]) > TRIANGLE_RATIO_TOLERANCE
        )
        return hits[~outside]


def _pinned_triangle_index(cat_pool: Sequence[CatalogStar]) -> _TriangleIndex:
    """Every triangle of the pinned pool with sides of 2.5 to 62 deg, built as before solver-10k."""
    v0s: list[int] = []
    v1s: list[int] = []
    v2s: list[int] = []
    ratios: list[tuple[float, float]] = []
    for i, j, k in itertools.combinations(range(len(cat_pool)), 3):
        s_i, s_j, s_k = cat_pool[i], cat_pool[j], cat_pool[k]
        d_ij = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_j.ra_deg, s_j.dec_deg)
        d_jk = angular_separation_deg(s_j.ra_deg, s_j.dec_deg, s_k.ra_deg, s_k.dec_deg)
        d_ik = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_k.ra_deg, s_k.dec_deg)
        sides = sorted([d_ij, d_jk, d_ik])
        if sides[0] < TRIANGLE_MIN_SIDE_DEG or sides[2] > TRIANGLE_MAX_SIDE_DEG:
            continue
        pairs = [((i, j), d_ij), ((j, k), d_jk), ((i, k), d_ik)]
        pairs.sort(key=lambda p: p[1], reverse=True)
        longest_set, second_set = set(pairs[0][0]), set(pairs[1][0])
        v0 = (longest_set & second_set).pop()
        v1 = (longest_set - {v0}).pop()
        v2 = ({i, j, k} - {v0, v1}).pop()
        v0s.append(v0)
        v1s.append(v1)
        v2s.append(v2)
        ratios.append((pairs[1][1] / pairs[0][1], pairs[2][1] / pairs[0][1]))
    return _TriangleIndex.from_ratios(np.array(ratios, dtype=np.float64).reshape(-1, 2), v0s, v1s, v2s)


def _greedy_pairs(
    wcs: WCSModel,
    detections: Sequence[SourceDetection],
    stars: Sequence[CatalogStar],
    ra_deg: np.ndarray,
    dec_deg: np.ndarray,
    radius_px: float,
) -> list[tuple[SourceDetection, CatalogStar, float]]:
    """The consensus pairing: each detection in turn takes the nearest star not yet taken (the first in order on a
    tie) when it projects closer than `radius_px`, as the search did before solver-10k, with one projection of all
    stars per candidate instead of one per detection and star."""
    if not detections or len(stars) == 0:
        return []
    px, py = _project(wcs, ra_deg, dec_deg)
    dx = np.array([d.x_px for d in detections], dtype=np.float64)
    dy = np.array([d.y_px for d in detections], dtype=np.float64)
    with np.errstate(invalid="ignore"):
        dist = np.hypot(px[None, :] - dx[:, None], py[None, :] - dy[:, None])
    close = np.isfinite(dist) & (dist < radius_px)
    ids = [s.id for s in stars]
    used: set[str] = set()
    pairs: list[tuple[SourceDetection, CatalogStar, float]] = []
    for i in np.flatnonzero(close.any(axis=1)):
        best_j, best_d = -1, radius_px
        for j in np.flatnonzero(close[i]):
            if ids[j] in used:
                continue
            if dist[i, j] < best_d:
                best_j, best_d = int(j), float(dist[i, j])
        if best_j >= 0:
            used.add(ids[best_j])
            pairs.append((detections[i], stars[best_j], best_d))
    return pairs


def _scoring_detections(psf_detections: Sequence[SourceDetection]) -> list[SourceDetection]:
    """The `SCORING_DETECTION_COUNT` highest-SNR detections, in their given order (all of them when fewer)."""
    if len(psf_detections) <= SCORING_DETECTION_COUNT:
        return list(psf_detections)
    brightest = sorted(range(len(psf_detections)), key=lambda i: psf_detections[i].snr, reverse=True)
    return [psf_detections[i] for i in sorted(brightest[:SCORING_DETECTION_COUNT])]


def _pinned_search(
    det_triangles: Sequence,
    cat_pool: Sequence[CatalogStar],
    catalog: Sequence[CatalogStar],
    score_dets: Sequence[SourceDetection],
    width: int,
    height: int,
    on_candidate: Callable[[int, int], None] | None,
) -> tuple[list[tuple[SourceDetection, CatalogStar]], WCSModel | None]:
    """The search over the pinned pool, as before solver-10k: the candidate whose WCS pairs the most of
    `score_dets` (every PSF-shaped detection) with catalog stars within 18 px wins, the first on a tie, and the
    search stops once one pairs at least max(11, pool size - 3). Only the speed changed: triangles are looked up by
    ratio and each candidate projects the catalog once. (Scoring only the 150 brightest detections here turned two
    unresolved simulated ultra-wide frames into false claims, so it is left to the tile pool, whose gate is
    stricter.)"""
    index = _pinned_triangle_index(cat_pool)
    cat_ra = np.array([s.ra_deg for s in catalog], dtype=np.float64)
    cat_dec = np.array([s.dec_deg for s in catalog], dtype=np.float64)
    best_pairs: list[tuple[SourceDetection, CatalogStar]] = []
    best_wcs: WCSModel | None = None
    total = len(det_triangles)
    if on_candidate is not None:
        on_candidate(0, total)
    for evaluated, triangle in enumerate(det_triangles, start=1):
        if triangle is None:
            if on_candidate is not None:
                on_candidate(evaluated, total)
            continue
        det_tri, dr1, dr2 = triangle
        for h in index.lookup(dr1, dr2):
            cand_wcs = _estimate_initial_wcs_from_triplet(
                det_tri, (cat_pool[index.v0[h]], cat_pool[index.v1[h]], cat_pool[index.v2[h]]), width, height
            )
            if cand_wcs is None:
                continue
            matched = _greedy_pairs(cand_wcs, score_dets, catalog, cat_ra, cat_dec, PINNED_MATCH_RADIUS_PX)
            if len(matched) > len(best_pairs):
                best_pairs = [(d, s) for d, s, _ in matched]
                best_wcs = cand_wcs
        if on_candidate is not None:
            on_candidate(evaluated, total)
        if len(best_pairs) >= max(11, len(cat_pool) - 3):
            break
    return best_pairs, best_wcs


@lru_cache(maxsize=1)
def _cell_centres() -> np.ndarray:
    """Unit vectors of the 192 HEALPix nside 4 cell centres (nested order)."""
    nside = 1 << TILE_CELL_ORDER
    face, ix, iy = ct.nest_to_face_ixiy(TILE_CELL_ORDER, np.arange(12 * nside * nside, dtype=np.int64))
    vec = ct.face_xy_to_vec(face, (ix + 0.5) / nside, (iy + 0.5) / nside)
    return vec / np.linalg.norm(vec, axis=1, keepdims=True)


def _chord(angle_deg: float | np.ndarray) -> float | np.ndarray:
    return 2.0 * np.sin(np.radians(np.minimum(angle_deg, 180.0)) / 2.0)


def _unit_vectors(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def _tile_triangle_index(vec: np.ndarray) -> _TriangleIndex:
    """Triangles of a pool in V order (`vec` its unit vectors): for each sky cell, every triangle of the
    `TILE_TRIANGLE_STARS_PER_CELL` brightest stars within `FIELD_CONE_RADIUS_DEG` of its centre, with sides of 2.5 to
    62 deg, each triangle once, recorded with the first cell that built it."""
    vec = np.asarray(vec, dtype=np.float64).reshape(-1, 3)
    if vec.shape[0] < 3:
        return _TriangleIndex.from_ratios(np.zeros((0, 2)))
    members = cKDTree(vec).query_ball_point(_cell_centres(), float(_chord(FIELD_CONE_RADIUS_DEG)), return_sorted=True)
    first_cell: dict[tuple[int, int, int], int] = {}
    for cell, stars_in_cone in enumerate(members):
        for triple in itertools.combinations(list(stars_in_cone)[:TILE_TRIANGLE_STARS_PER_CELL], 3):
            first_cell.setdefault(triple, cell)
    if not first_cell:
        return _TriangleIndex.from_ratios(np.zeros((0, 2)))
    tri = np.array(list(first_cell.keys()), dtype=np.int64)
    cells = np.array(list(first_cell.values()), dtype=np.int64)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]

    def side(i: np.ndarray, j: np.ndarray) -> np.ndarray:
        return np.degrees(np.arccos(np.clip(np.sum(vec[i] * vec[j], axis=1), -1.0, 1.0)))

    sides = np.stack([side(a, b), side(b, c), side(a, c)], axis=1)
    opposite = np.stack([c, a, b], axis=1)
    order = np.argsort(-sides, axis=1, kind="stable")
    rows = np.arange(tri.shape[0])
    longest, second, shortest = (sides[rows, order[:, k]] for k in range(3))
    # The longest side joins v0 and v1, the second v0 and v2, the shortest v1 and v2
    v2, v1, v0 = (opposite[rows, order[:, k]] for k in range(3))
    keep = (shortest >= TRIANGLE_MIN_SIDE_DEG) & (longest <= TRIANGLE_MAX_SIDE_DEG)
    ratios = np.stack([second / longest, shortest / longest], axis=1)
    return _TriangleIndex.from_ratios(ratios[keep], v0[keep], v1[keep], v2[keep], cells[keep])


def _tile_v_limit(n_used: int, count_at: Callable[[float], int], region_sr: float, floor: float) -> float:
    """The V limit of the tile pool, chosen from the frame: the shallowest step of `TILE_V_STEP` from `floor` at
    which the pool's expected count in a field-sized cone, `count_at(v)` times the cone's share of the region the
    pool covers (`region_sr`, the sky above the altitude cut or the whole sky), reaches `n_used`, the detections
    the consensus scores; `TILE_V_CAP` when no step does."""
    share = 2.0 * math.pi * (1.0 - math.cos(math.radians(FIELD_CONE_RADIUS_DEG))) / region_sr
    steps = int(round((TILE_V_CAP - floor) / TILE_V_STEP))
    for k in range(steps + 1):
        v = round(floor + k * TILE_V_STEP, 6)
        if count_at(v) * share >= n_used:
            return v
    return TILE_V_CAP


def _pool_description(*, above_horizon: bool, v_limit: float) -> str:
    if above_horizon:
        return f"tile stars above the horizon, apparent altitude at least {TILE_MIN_ALTITUDE_DEG:g} deg and V at most {v_limit:.1f}"
    return f"tile stars across the whole sky, V at most {v_limit:.1f}"


def _chance_of_at_least(matches: np.ndarray, in_frame: np.ndarray, chance: float) -> np.ndarray:
    """P(X >= matches) for X binomial over `in_frame` stars, each landing on a detection with probability `chance`."""
    k, n = np.broadcast_arrays(np.asarray(matches, dtype=np.int64), np.asarray(in_frame, dtype=np.int64))
    out = np.where(k > n, 0.0, 1.0)
    ok = (k >= 1) & (k <= n)
    if np.any(ok):
        out[ok] = bdtrc(k[ok] - 1, n[ok], float(chance))
    return out


def _consensus_passes(matches: np.ndarray, in_frame: np.ndarray, chance: float) -> np.ndarray:
    """The consensus threshold: at least `CONSENSUS_MIN_MATCHES` matches, as many or more being at most
    `CONSENSUS_MAX_CHANCE` likely by chance."""
    k = np.asarray(matches)
    return (k >= CONSENSUS_MIN_MATCHES) & (_chance_of_at_least(k, in_frame, chance) <= CONSENSUS_MAX_CHANCE)


def _spread_by_radius(
    pairs: Sequence[tuple[SourceDetection, CatalogStar, float]], width: int, height: int, count: int
) -> list[tuple[SourceDetection, CatalogStar, float]]:
    """`count` of `pairs` spread from the edge of the frame in: ordered by distance from the centre, outermost
    first, every other one, then the rest (phone_photo_solver.py's interleaved split)."""
    cx0, cy0 = width / 2.0, height / 2.0
    by_radius = sorted(pairs, key=lambda item: math.hypot(item[0].x_px - cx0, item[0].y_px - cy0), reverse=True)
    return [*by_radius[::2], *by_radius[1::2]][:count]


def _dual_path_disagreement_px(wcs: WCSModel, verification: HeldOutVerification, width: int, height: int) -> float:
    """The phone solver's dual-path check, for a tile-pool fit: the fit refitted on held-out inliers alone (at most
    `TILE_MAX_TRAINING_PAIRS` of them, spread from the edge in), and the median distance in pixels between where the
    two put the sky of a 5 x 5 grid over 15 to 85 % of the width and 15 to 75 % of the height (the phone solver's
    grid). 99 when fewer than `MIN_HELD_OUT_INLIERS` are held out. A fit that holds only where its stars are
    disagrees with its own held-out refit away from them."""
    held = [(d, s, r) for d, s, r, is_train in verification.associations if not is_train and r <= verification.inlier_gate_px]
    if len(held) < MIN_HELD_OUT_INLIERS:
        return 99.0
    pairs = _spread_by_radius(held, width, height, TILE_MAX_TRAINING_PAIRS)
    held_wcs = _refine_wcs_least_squares(wcs, [(d, s) for d, s, _ in pairs])
    gx, gy = np.meshgrid(np.linspace(0.15 * width, 0.85 * width, 5), np.linspace(0.15 * height, 0.75 * height, 5))
    sky = np.array([wcs.pixel_to_world(float(x), float(y)) for x, y in zip(gx.ravel(), gy.ravel())])
    hx, hy = _project(held_wcs, sky[:, 0], sky[:, 1])
    diffs = np.hypot(hx - gx.ravel(), hy - gy.ravel())
    diffs = diffs[np.isfinite(diffs)]
    return float(np.median(diffs)) if diffs.size else 99.0


def _tile_pool_gate(
    verification: HeldOutVerification,
    wcs: WCSModel,
    detections: Sequence[SourceDetection],
    pool_ra: np.ndarray,
    pool_dec: np.ndarray,
    width: int,
    height: int,
) -> tuple[bool, bool, float]:
    """(held-out gate passed, the tile pool's whole gate passed, log odds) for a fit on the tile pool: the held-out
    gate unchanged, and then the phone solver's coincidence check (`evaluate_coincidence_gate`, Lang et al. 2010):
    the held-out residuals weighed against the PSF-shaped detections over the frame's area and the stars of
    `pool_ra` and `pool_dec` (the tile pass gives the field's cone only) that `wcs` puts inside the frame, with its
    dual-path agreement (`_dual_path_disagreement_px`, at most 2.5 px)."""
    held = bool(verification.passed)
    px, py = _project(wcs, pool_ra, pool_dec)
    projected = int(np.count_nonzero(np.isfinite(px) & np.isfinite(py) & (px >= 0.0) & (px < width) & (py >= 0.0) & (py < height)))
    active = sum(not d.is_hot_pixel_impulse for d in detections)
    coincident, log_odds = evaluate_coincidence_gate(
        held_out_residuals_px=verification.held_out_residuals_px,
        active_detection_count=active,
        active_sky_area_px2=float(width * height),
        projected_catalog_count=projected,
        inlier_gate_px=verification.inlier_gate_px,
        dual_path_disagreement_px=_dual_path_disagreement_px(wcs, verification, width, height) if held else 99.0,
    )
    return held, bool(held and coincident), float(log_odds)


def _tile_match_radius_px(width: int, height: int) -> float:
    return max(TILE_MATCH_RADIUS_MIN_PX, PINNED_MATCH_RADIUS_PX * math.hypot(width, height) / math.hypot(960.0, 540.0))


def _distorted_radius(wcs: WCSModel, r: float) -> float:
    """r (1 + k1 r^2 + k2 r^4): where `wcs`'s radial terms take tangent-plane radius r (radians, the tangent of the
    angle from the tangent point)."""
    r2 = r * r
    return r * (1.0 + wcs.k1 * r2 + wcs.k2 * r2 * r2)


def _field_corner_angle_deg(wcs: WCSModel) -> float | None:
    """The largest angle from `wcs`'s tangent point of the frame's four corners, found on the rising part of
    r (1 + k1 r^2 + k2 r^4) below its first turn (the phone solver's rule, `_max_valid_radial`: the smallest positive
    root of 1 + 3 k1 r^2 + 5 k2 r^4, at most r = 50, 88.9 deg). None when the corners lie past that turn."""
    corner_px = max(math.hypot(x - wcs.crpix_x, y - wcs.crpix_y) for x in (0.0, float(wcs.width)) for y in (0.0, float(wcs.height)))
    target = math.radians(corner_px * wcs.scale_deg_per_px)
    turn = _max_valid_radial(wcs.k1, wcs.k2, fisheye=False)
    if not target < _distorted_radius(wcs, turn):
        return None
    lo, hi = 0.0, turn
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if _distorted_radius(wcs, mid) < target:
            lo = mid
        else:
            hi = mid
    return math.degrees(math.atan(hi))


def _folds_inside_field(wcs: WCSModel) -> bool:
    """Whether `wcs`'s radial terms fold over inside the field: r (1 + k1 r^2 + k2 r^4) stops increasing before the
    frame corners' angle from the tangent point plus `FIELD_MARGIN_DEG`, or before the corners themselves."""
    corner_deg = _field_corner_angle_deg(wcs)
    if corner_deg is None:
        return True
    turn_deg = math.degrees(math.atan(_max_valid_radial(wcs.k1, wcs.k2, fisheye=False)))
    return corner_deg + FIELD_MARGIN_DEG >= turn_deg


def _field_cone_members(wcs: WCSModel, pool_tree: cKDTree) -> list[int]:
    """Indices, ascending, of the pool's stars (`pool_tree` holds their unit vectors) within the frame corners' angle
    plus `FIELD_MARGIN_DEG` of `wcs`'s tangent point. `wcs` must not fold inside the field (`_folds_inside_field`)."""
    corner_deg = _field_corner_angle_deg(wcs)
    if corner_deg is None:
        raise ValueError("the WCS folds over before the frame's corners, so it has no field's cone")
    centre = _unit_vectors(np.array([wcs.crval_ra_deg]), np.array([wcs.crval_dec_deg]))[0]
    return sorted(int(m) for m in pool_tree.query_ball_point(centre, float(_chord(corner_deg + FIELD_MARGIN_DEG))))


def _score_tile_candidates(
    det_tri: tuple[SourceDetection, SourceDetection, SourceDetection],
    hits: np.ndarray,
    index: _TriangleIndex,
    vec: np.ndarray,
    cell_tree: cKDTree,
    cell_stars: np.ndarray,
    det_tree: cKDTree,
    width: int,
    height: int,
    radius_px: float,
    chance: float,
) -> list[tuple[float, int, int, int]]:
    """The first consensus score of every catalog triangle `hits` matched to one detector triangle, all at once.

    Each match gives a similarity from the gnomonic plane at the triangle's centroid to pixels, with the parity
    that fits the three points best (the scale must lie in 0.002 to 0.25 deg/px, as `_estimate_initial_wcs_
    from_triplet` requires). It projects the `TILE_SCORING_STARS` brightest stars around the sky cell nearest its
    field centre, other than its own three, and counts those inside the frame and those within `radius_px` of a
    scored detection. Returns (chance of as many matches, matches, hit, parity) for those passing
    `_consensus_passes`.
    """
    p = np.array([[d.x_px, d.y_px] for d in det_tri], dtype=np.float64)
    zp = p[:, 0] - 1j * p[:, 1]
    s = vec[np.stack([index.v0[hits], index.v1[hits], index.v2[hits]], axis=1)]
    t = s.sum(axis=1)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    east = np.cross(np.array([0.0, 0.0, 1.0]), t)
    polar = np.linalg.norm(east, axis=1) < 1e-9
    east[polar] = np.cross(np.array([1.0, 0.0, 0.0]), t[polar])
    east /= np.linalg.norm(east, axis=1, keepdims=True)
    north = np.cross(t, east)
    depth = np.einsum("hkc,hc->hk", s, t)
    xi = np.einsum("hkc,hc->hk", s, east) / depth
    eta = np.einsum("hkc,hc->hk", s, north) / depth
    fits = []
    for parity in (-1.0, 1.0):
        zt = parity * xi + 1j * eta
        ztc = zt.mean(axis=1, keepdims=True)
        zpc = zp.mean()
        den = np.sum(np.abs(zt - ztc) ** 2, axis=1)
        a = np.sum((zp - zpc)[None, :] * np.conj(zt - ztc), axis=1) / np.where(den > 0.0, den, np.nan)
        b = zpc - a * ztc[:, 0]
        res = np.sum(np.abs(a[:, None] * zt + b[:, None] - zp[None, :]) ** 2, axis=1)
        fits.append((a, b, res))
    use_plus = fits[1][2] < fits[0][2]
    parity = np.where(use_plus, 1.0, -1.0)
    a = np.where(use_plus, fits[1][0], fits[0][0])
    b = np.where(use_plus, fits[1][1], fits[0][1])
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.degrees(1.0) / np.abs(a)
    plausible = np.isfinite(scale) & (scale >= 0.002) & (scale <= 0.25)
    if not np.any(plausible):
        return []
    keep = np.flatnonzero(plausible)
    hits, parity, a, b, t, east, north = hits[keep], parity[keep], a[keep], b[keep], t[keep], east[keep], north[keep]
    # The field centre: the frame's centre pixel back through the similarity onto the sky
    zc = ((width / 2.0) - 1j * (height / 2.0) - b) / a
    centre = t + (parity * zc.real)[:, None] * east + zc.imag[:, None] * north
    centre /= np.linalg.norm(centre, axis=1, keepdims=True)
    _, cell = cell_tree.query(centre)
    idx = cell_stars[cell]
    own = np.stack([index.v0[hits], index.v1[hits], index.v2[hits]], axis=1)
    valid = (idx >= 0) & ~np.any(idx[:, :, None] == own[:, None, :], axis=2)
    sv = vec[np.where(valid, idx, 0)]
    dd = np.einsum("hmc,hc->hm", sv, t)
    front = valid & (dd > 1e-6)
    dd = np.where(front, dd, 1.0)
    z = a[:, None] * (parity[:, None] * np.einsum("hmc,hc->hm", sv, east) / dd + 1j * np.einsum("hmc,hc->hm", sv, north) / dd) + b[:, None]
    x, y = z.real, -z.imag
    inside = front & (x >= 0.0) & (x < width) & (y >= 0.0) & (y < height)
    rows = np.nonzero(inside)[0]
    if rows.size == 0:
        return []
    dist, _ = det_tree.query(np.column_stack([x[inside], y[inside]]), k=1, distance_upper_bound=radius_px)
    in_frame = np.bincount(rows, minlength=hits.size)
    matches = np.bincount(rows[np.isfinite(dist)], minlength=hits.size)
    passing = np.flatnonzero(_consensus_passes(matches, in_frame, chance))
    tails = _chance_of_at_least(matches[passing], in_frame[passing], chance)
    return [(float(tails[k]), int(matches[i]), int(hits[i]), int(parity[i])) for k, i in enumerate(passing)]


def _tile_training_pairs(
    pairs: Sequence[tuple[SourceDetection, CatalogStar, float]], width: int, height: int
) -> list[tuple[SourceDetection, CatalogStar, float]]:
    """Training pairs for a tile-pool fit, split as the phone solver splits them: of the tightest 85 % (at least 6),
    up to `TILE_MAX_TRAINING_PAIRS` spread from the edge in, half of them at most and leaving at least 6 to hold
    out."""
    tight = sorted(pairs, key=lambda item: item[2])[: max(6, int(0.85 * len(pairs)))]
    count = max(3, min(TILE_MAX_TRAINING_PAIRS, len(tight) // 2, len(tight) - 6))
    return _spread_by_radius(tight, width, height, count)


def _solution_from_fit(
    detections: list[SourceDetection],
    refined_wcs: WCSModel,
    verification: HeldOutVerification,
    passed_verification: bool,
    catalog_size: int,
    identity: SolverIdentity,
    *,
    solution_id: str,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float,
    obs_utc_iso: str | None,
    observer_lat: float | None,
    observer_lon: float | None,
) -> ObservationSolution:
    """The solution a verified or failed fit gives: catalog matches only when `passed_verification`."""
    verified_matches = verification.inliers
    held_out_inliers = verification.held_out_inliers
    # None when no held-out star paired with a catalog star: there is no residual to report
    median_residual_px = verification.median_residual_px
    p95_residual_px = verification.p95_residual_px
    median_residual_arcsec = (
        median_residual_px * refined_wcs.scale_deg_per_px * 3600.0 if median_residual_px is not None else None
    )
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
                    f"No star of the {catalog_size}-star catalog sits within {ASSOCIATION_RADIUS_PX:g} px "
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
                f"{no_catalog_star_count} with no star of the {catalog_size}-star catalog "
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
        solver=identity,
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


def _tile_pool_for(
    n_used: int, obs_utc_iso: str | None, observer_lat: float | None, observer_lon: float | None
) -> tuple[list[CatalogStar], float, bool]:
    """(pool, V limit, whether it is the sky above the altitude cut) for a frame whose consensus scores `n_used`
    detections. With a time and place: the tile stars at least `TILE_MIN_ALTITUDE_DEG` up, from the V floor
    `TILE_V_FLOOR_ABOVE_HORIZON`. Otherwise the whole sky from `TILE_V_FLOOR_ALL_SKY`, at the time's epoch when there
    is a time. The V limit is `_tile_v_limit`'s, over the region the pool covers."""
    if obs_utc_iso is not None and observer_lat is not None and observer_lon is not None:
        def count_up(v: float) -> int:
            return len(tile_stars_above_horizon(obs_utc_iso, observer_lat, observer_lon, v, TILE_MIN_ALTITUDE_DEG))

        region_sr = 2.0 * math.pi * (1.0 - math.sin(math.radians(TILE_MIN_ALTITUDE_DEG)))
        v_limit = _tile_v_limit(n_used, count_up, region_sr, TILE_V_FLOOR_ABOVE_HORIZON)
        return tile_stars_above_horizon(obs_utc_iso, observer_lat, observer_lon, v_limit, TILE_MIN_ALTITUDE_DEG), v_limit, True

    def count_all(v: float) -> int:
        return len(tile_stars_all_sky(v, obs_utc_iso))

    v_limit = _tile_v_limit(n_used, count_all, 4.0 * math.pi, TILE_V_FLOOR_ALL_SKY)
    return tile_stars_all_sky(v_limit, obs_utc_iso), v_limit, False


def _tile_pool_search(
    det_triangles: Sequence,
    detections: list[SourceDetection],
    score_dets: Sequence[SourceDetection],
    width: int,
    height: int,
    *,
    obs_utc_iso: str | None,
    observer_lat: float | None,
    observer_lon: float | None,
    hint_ra_deg: float | None,
    hint_dec_deg: float | None,
    on_candidate: Callable[[int, int], None] | None,
    solution_kwargs: dict,
) -> ObservationSolution | None:
    """The search over the tile catalog's pool: a calibrated solution, or None when no fit passed.

    The pool: with a time and place, the tile stars at least `TILE_MIN_ALTITUDE_DEG` up, else the whole sky, to the
    V limit `_tile_v_limit` picks for this frame. Every detector triangle is looked up by ratio and each catalog
    triangle it matches is scored at once (`_score_tile_candidates`); those passing the consensus threshold are
    then verified best first (least chance first), at most `TILE_MAX_VERIFIED`: a TAN WCS from the triangle, the
    consensus pairs within the match radius among the stars in a cone around its field centre, a fit on training
    pairs only, refused when its radial terms fold over inside the field (`_folds_inside_field`), and the held-out
    gate on every detection together with the coincidence check (`_tile_pool_gate`), both on the pool's stars in the
    field's cone only (`_field_cone_members`). The first candidate passing both is the solution.
    """
    pool, v_limit, above_horizon = _tile_pool_for(len(score_dets), obs_utc_iso, observer_lat, observer_lon)
    if hint_ra_deg is not None and hint_dec_deg is not None:
        filtered = [s for s in pool if angular_separation_deg(s.ra_deg, s.dec_deg, hint_ra_deg, hint_dec_deg) <= 68.0]
        if len(filtered) >= 8:
            pool = filtered
    if len(pool) < 6:
        return None
    identity = triangle_solver_identity(pool, _pool_description(above_horizon=above_horizon, v_limit=v_limit))
    ra = np.array([s.ra_deg for s in pool], dtype=np.float64)
    dec = np.array([s.dec_deg for s in pool], dtype=np.float64)
    vec = _unit_vectors(ra, dec)
    index = _tile_triangle_index(vec)
    pool_tree = cKDTree(vec)
    centres = _cell_centres()
    cell_stars = np.full((centres.shape[0], TILE_SCORING_STARS), -1, dtype=np.int64)
    for cell, members in enumerate(pool_tree.query_ball_point(centres, float(_chord(TILE_SCORING_CONE_DEG)), return_sorted=True)):
        top = list(members)[:TILE_SCORING_STARS]
        cell_stars[cell, : len(top)] = top
    cell_tree = cKDTree(centres)
    det_tree = cKDTree(np.array([[d.x_px, d.y_px] for d in score_dets], dtype=np.float64))
    radius_px = _tile_match_radius_px(width, height)
    chance = 1.0 - math.exp(-len(score_dets) / float(width * height) * math.pi * radius_px * radius_px)

    candidates: list[tuple[float, int, int, tuple, int, int]] = []
    total = len(det_triangles)
    if on_candidate is not None:
        on_candidate(0, total)
    for evaluated, triangle in enumerate(det_triangles, start=1):
        if triangle is not None and index.tree is not None:
            det_tri, dr1, dr2 = triangle
            hits = index.lookup(dr1, dr2)
            if hits.size:
                for tail, matches, hit, parity in _score_tile_candidates(
                    det_tri, hits, index, vec, cell_tree, cell_stars, det_tree, width, height, radius_px, chance
                ):
                    candidates.append((tail, -matches, len(candidates), det_tri, hit, parity))
        if on_candidate is not None:
            on_candidate(evaluated, total)
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    half_diag_px = 0.5 * math.hypot(width, height)
    for _, _, _, det_tri, hit, parity in candidates[:TILE_MAX_VERIFIED]:
        stars3 = (pool[index.v0[hit]], pool[index.v1[hit]], pool[index.v2[hit]])
        cand_wcs = _estimate_initial_wcs_from_triplet(det_tri, stars3, width, height, parities=(parity,))
        if cand_wcs is None:
            continue
        cone_deg = math.degrees(math.atan(math.radians(half_diag_px * cand_wcs.scale_deg_per_px))) + 1.0
        centre = _unit_vectors(np.array([cand_wcs.crval_ra_deg]), np.array([cand_wcs.crval_dec_deg]))[0]
        members = sorted(pool_tree.query_ball_point(centre, float(_chord(cone_deg))))
        cone = [pool[m] for m in members]
        pairs = _greedy_pairs(cand_wcs, score_dets, cone, ra[members], dec[members], radius_px)
        if len(pairs) < 4:
            continue
        # Two fits, as the phone solver's: a TAN fit first, whose pairs reach further out than the triangle's
        # similarity, then the radial terms on training pairs taken from those
        first = _tile_training_pairs(pairs, width, height)
        tan_wcs = _refine_wcs_least_squares(cand_wcs, [(d, s) for d, s, _ in first], fit_distortion=False)
        pairs = _greedy_pairs(tan_wcs, score_dets, cone, ra[members], dec[members], radius_px)
        if len(pairs) < 4:
            continue
        second = _tile_training_pairs(pairs, width, height)
        refined = _refine_wcs_least_squares(tan_wcs, [(d, s) for d, s, _ in second])
        # A fit whose radial terms fold over inside the field is refused; the rest is verified on the field's stars
        if _folds_inside_field(refined):
            continue
        field = _field_cone_members(refined, pool_tree)
        # Every detection either fit trained on stays out of the held-out set
        trained = {id(d): d for d, _, _ in (*first, *second)}
        verification = verify_held_out(refined, detections, [pool[m] for m in field], list(trained.values()))
        _, passed, _ = _tile_pool_gate(verification, refined, detections, ra[field], dec[field], width, height)
        if passed:
            return _solution_from_fit(detections, refined, verification, True, len(pool), identity, **solution_kwargs)
    return None


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

    The search runs on the 18 brightest pinned stars first, as before solver-10k (or on `catalog_stars` when given,
    and then only on them). When that does not calibrate, it runs again on the tile catalog's pool
    (`_tile_pool_search`), reporting candidate counts for each run. A calibrated tile-pool fit is the solution, and
    its solver identity names that pool; otherwise the pinned run's result stands, with the pinned identity.
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

    solution_kwargs = dict(
        solution_id=solution_id, source_asset_id=source_asset_id, source_sha256=source_sha256,
        frame_time_sec=frame_time_sec, obs_utc_iso=obs_utc_iso, observer_lat=observer_lat, observer_lon=observer_lon,
    )
    cat_pool = sorted(catalog, key=lambda s: s.mag)[:PINNED_POOL_SIZE]
    det_pool = sorted(psf_detections, key=lambda d: d.snr, reverse=True)[:DETECTOR_POOL_SIZE]
    det_triangles = _detector_triangles(det_pool)
    score_dets = _scoring_detections(psf_detections)
    best_consensus_pairs, best_initial_wcs = _pinned_search(
        det_triangles, cat_pool, catalog, psf_detections, width, height, on_candidate
    )

    if best_initial_wcs is None or len(best_consensus_pairs) < 4:
        pinned_solution = _build_unresolved_solution(
            detections, solution_id, source_asset_id, source_sha256, frame_time_sec,
            obs_utc_iso, observer_lat, observer_lon,
            "Triangle pattern matching found no consistent WCS solution.",
            "Pixel position only: no catalog pattern matched, so no WCS places it on the sky.",
            triangle_solver_identity(catalog),
        )
    else:
        cx0, cy0 = width / 2.0, height / 2.0
        sorted_consensus = sorted(
            best_consensus_pairs,
            key=lambda pair: math.hypot(pair[0].x_px - cx0, pair[0].y_px - cy0),
            reverse=True,
        )
        train_subset = sorted_consensus[: min(4, len(sorted_consensus))]
        refined_wcs = _refine_wcs_least_squares(best_initial_wcs, train_subset)
        verification = verify_held_out(refined_wcs, detections, catalog, [d for d, _ in train_subset])
        pinned_solution = _solution_from_fit(
            detections, refined_wcs, verification, verification.passed, len(catalog),
            triangle_solver_identity(catalog), **solution_kwargs,
        )
    if pinned_solution.status == "calibrated" or catalog_stars is not None:
        return pinned_solution

    tile_solution = _tile_pool_search(
        det_triangles, detections, score_dets, width, height,
        obs_utc_iso=obs_utc_iso, observer_lat=observer_lat, observer_lon=observer_lon,
        hint_ra_deg=hint_ra_deg, hint_dec_deg=hint_dec_deg, on_candidate=on_candidate,
        solution_kwargs=solution_kwargs,
    )
    if tile_solution is not None and tile_solution.status == "calibrated":
        return tile_solution
    return pinned_solution


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
