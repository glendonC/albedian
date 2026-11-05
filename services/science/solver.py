"""Deterministic astrometric geometric plate solver with held-out WCS verification,
and public re-exports for point-source extraction, video calibration stacking, and horizon solving.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Sequence

import numpy as np
from scipy import optimize

from services.science.catalog import (
    CatalogStar,
    angular_separation_deg,
    get_catalog_stars,
    icrs_to_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.detector import (
    _build_gaussian_psf_kernel,
    _estimate_background_and_noise,
    _estimate_subpixel_shift_phase_corr,
    _extract_registration_signal,
    _to_luma_2d,
    detect_point_sources,
    render_synthetic_starfield,
    stack_video_frames_for_calibration,
)
from services.science.horizon_solver import solve_horizon_and_beacon_pose
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
    same objects as in `detections`) and nothing else, and measure the held-out gate."""
    train_ids = {id(det) for det in training_detections}
    associations: list[tuple[SourceDetection, CatalogStar, float, bool]] = []
    used_catalog_ids: set[str] = set()
    for det in detections:
        if det.is_hot_pixel_impulse:
            continue
        best_st: CatalogStar | None = None
        best_res = float("inf")
        for st in catalog:
            if st.id in used_catalog_ids:
                continue
            px, py = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
            if math.isnan(px):
                continue
            res_px = math.hypot(px - det.x_px, py - det.y_px)
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
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
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
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
    catalog_stars: list[CatalogStar] | None = None,
    hint_ra_deg: float | None = None,
    hint_dec_deg: float | None = None,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-primary",
) -> ObservationSolution:
    """Blind / hint-assisted geometric plate solver with held-out WCS verification (`verify_held_out`).

    `source_asset_id` and `source_sha256` name the recording the detections were measured in; there is no
    default, because the solver cannot know them.
    """
    require_source_sha256(source_sha256)
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

    det_pool = sorted(psf_detections, key=lambda d: d.snr, reverse=True)[:14]
    best_consensus_pairs: list[tuple[SourceDetection, CatalogStar]] = []
    best_initial_wcs: WCSModel | None = None

    for a, b, c in itertools.combinations(range(len(det_pool)), 3):
        da, db, dc = det_pool[a], det_pool[b], det_pool[c]
        p_ab = math.hypot(da.x_px - db.x_px, da.y_px - db.y_px)
        p_bc = math.hypot(db.x_px - dc.x_px, db.y_px - dc.y_px)
        p_ac = math.hypot(da.x_px - dc.x_px, da.y_px - dc.y_px)
        px_pairs = [((a, b), p_ab), ((b, c), p_bc), ((a, c), p_ac)]
        px_pairs.sort(key=lambda p: p[1], reverse=True)
        if px_pairs[2][1] < 25.0:
            continue

        longest_set, second_set = set(px_pairs[0][0]), set(px_pairs[1][0])
        u0 = (longest_set & second_set).pop()
        u1 = (longest_set - {u0}).pop()
        u2 = ({a, b, c} - {u0, u1}).pop()

        dr1 = px_pairs[1][1] / px_pairs[0][1]
        dr2 = px_pairs[2][1] / px_pairs[0][1]
        det_tri = (det_pool[u0], det_pool[u1], det_pool[u2])

        for v0, v1, v2, cr1, cr2, _ in cat_triangles:
            if abs(dr1 - cr1) > 0.045 or abs(dr2 - cr2) > 0.045:
                continue
            cand_wcs = _estimate_initial_wcs_from_triplet(
                det_tri, (cat_pool[v0], cat_pool[v1], cat_pool[v2]), width, height
            )
            if cand_wcs is None:
                continue

            matched_pairs: list[tuple[SourceDetection, CatalogStar, float]] = []
            used_stars: set[str] = set()
            for det in psf_detections:
                best_star: CatalogStar | None = None
                best_dist = 18.0
                for st in catalog:
                    if st.id in used_stars:
                        continue
                    px, py = cand_wcs.world_to_pixel(st.ra_deg, st.dec_deg)
                    if math.isnan(px):
                        continue
                    dist = math.hypot(px - det.x_px, py - det.y_px)
                    if dist < best_dist:
                        best_dist = dist
                        best_star = st
                if best_star is not None:
                    used_stars.add(best_star.id)
                    matched_pairs.append((det, best_star, best_dist))

            if len(matched_pairs) > len(best_consensus_pairs):
                best_consensus_pairs = [(d, s) for d, s, _ in matched_pairs]
                best_initial_wcs = cand_wcs

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
    rejected_outlier_count = 0

    for idx, det in enumerate(detections):
        if passed_verification and id(det) in verified_matches:
            st, res_px, is_train = verified_matches[id(det)]
            alt_deg, az_deg = icrs_to_altaz(st.ra_deg, st.dec_deg, observer_lat, observer_lon, obs_utc_iso)
            ang_res_arcsec = res_px * refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-{st.id}",
                    observation_solution_id=solution_id,
                    evidence_class="catalog_matched",
                    title=st.common_name,
                    subtitle=f"{st.bayer_designation} · {st.spectral_type} · {st.median_pc:.1f} pc [{st.lower_pc:.1f}–{st.upper_pc:.1f} pc]",
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
            rejected_outlier_count += 1
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
                    limitations=("Rejected from catalog matching by 3-sigma residual / hot-pixel impulse gate.",),
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

    if rejected_outlier_count > 0 and passed_verification:
        warnings.append(
            f"{rejected_outlier_count} sensor detection(s) rejected from catalog matching via 3-sigma outlier gate."
        )
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
    "CelestialFindingRecord", "HeldOutVerification", "ObservationSolution", "SolutionQuality",
    "SourceDetection", "VideoStackCalibrationResult", "WCSModel",
    "_build_gaussian_psf_kernel", "_estimate_background_and_noise",
    "_estimate_initial_wcs_from_triplet", "_estimate_subpixel_shift_phase_corr",
    "_extract_registration_signal", "_refine_wcs_least_squares", "_to_luma_2d",
    "detect_point_sources", "render_synthetic_starfield", "solve_and_verify_field",
    "solve_horizon_and_beacon_pose", "stack_video_frames_for_calibration", "verify_held_out",
]
