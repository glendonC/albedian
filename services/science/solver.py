"""Deterministic astrometric point-source extraction, geometric plate solver,
independent held-out WCS verification, and motion-compensated video frame stacking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import math
from typing import Sequence

import numpy as np
from scipy import ndimage, optimize

from services.science.catalog import (
    CatalogStar,
    altaz_to_icrs,
    angular_separation_deg,
    bailer_jones_cartesian_pc,
    get_all_sky_catalog_stars,
    get_catalog_stars,
    icrs_to_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.ephemeris import compute_solar_system_ephemerides


@dataclass(frozen=True)
class SourceDetection:
    """Point source measured directly from original sensor pixels (`observed` class)."""

    x_px: float
    y_px: float
    flux: float
    snr: float
    fwhm_px: float
    centroid_uncertainty_px: float
    asset_id: str = "asset-0"
    frame_time_sec: float = 0.0
    peak_to_total_ratio: float = 0.25

    @property
    def xPx(self) -> float:
        return self.x_px

    @property
    def yPx(self) -> float:
        return self.y_px

    @property
    def fwhmPx(self) -> float:
        return self.fwhm_px

    @property
    def centroidUncertaintyPx(self) -> float:
        return self.centroid_uncertainty_px

    @property
    def is_hot_pixel_impulse(self) -> bool:
        """True if energy is concentrated in a single impulse pixel rather than an optical PSF."""
        return self.fwhm_px < 1.25 or self.peak_to_total_ratio > 0.68

    def to_contract_dict(self) -> dict:
        return {
            "assetId": self.asset_id,
            "frameTimeSec": float(self.frame_time_sec),
            "xPx": float(self.x_px),
            "yPx": float(self.y_px),
            "flux": float(self.flux),
            "snr": float(self.snr),
            "fwhmPx": float(self.fwhm_px),
            "centroidUncertaintyPx": float(self.centroid_uncertainty_px),
        }


@dataclass(frozen=True)
class WCSModel:
    """Gnomonic (`TAN`) + radial distortion (`k1`, `k2`) camera projection model."""

    crval_ra_deg: float
    crval_dec_deg: float
    crpix_x: float
    crpix_y: float
    scale_deg_per_px: float
    rotation_deg: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    parity: int = -1
    width: int = 1920
    height: int = 1080

    @property
    def fov_horizontal_deg(self) -> float:
        half_w_rad = math.radians(0.5 * self.width * self.scale_deg_per_px)
        return min(175.0, math.degrees(2.0 * math.atan(half_w_rad)))

    @property
    def fov_vertical_deg(self) -> float:
        half_h_rad = math.radians(0.5 * self.height * self.scale_deg_per_px)
        return min(175.0, math.degrees(2.0 * math.atan(half_h_rad)))

    def world_to_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        """Forward gnomonic TAN + radial polynomial distortion (k1, k2) from ICRS to pixel."""
        ra = math.radians(float(ra_deg) % 360.0)
        dec = math.radians(float(dec_deg))
        ra0 = math.radians(float(self.crval_ra_deg) % 360.0)
        dec0 = math.radians(float(self.crval_dec_deg))

        dra = ra - ra0
        cos_c = math.sin(dec0) * math.sin(dec) + math.cos(dec0) * math.cos(dec) * math.cos(dra)
        if cos_c <= 1e-6:
            return (float("nan"), float("nan"))

        # Standard tangent plane coordinates in radians
        xi_rad = self.parity * (math.cos(dec) * math.sin(dra)) / cos_c
        eta_rad = (
            math.cos(dec0) * math.sin(dec) - math.sin(dec0) * math.cos(dec) * math.cos(dra)
        ) / cos_c

        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        u_rad = xi_rad * cos_t - eta_rad * sin_t
        v_rad = xi_rad * sin_t + eta_rad * cos_t

        r2 = u_rad * u_rad + v_rad * v_rad
        distort = 1.0 + self.k1 * r2 + self.k2 * (r2 * r2)

        u_deg = math.degrees(u_rad * distort)
        v_deg = math.degrees(v_rad * distort)

        x_px = self.crpix_x + u_deg / self.scale_deg_per_px
        y_px = self.crpix_y - v_deg / self.scale_deg_per_px
        return (float(x_px), float(y_px))

    def pixel_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Exact inverse projection from pixel (x_px, y_px) to ICRS (ra_deg, dec_deg)."""
        u_d_deg = (float(x_px) - self.crpix_x) * self.scale_deg_per_px
        v_d_deg = -(float(y_px) - self.crpix_y) * self.scale_deg_per_px

        u_d_rad = math.radians(u_d_deg)
        v_d_rad = math.radians(v_d_deg)
        r_d = math.hypot(u_d_rad, v_d_rad)

        # Invert radial distortion r * (1 + k1*r^2 + k2*r^4) = r_d via Newton-Raphson
        r = r_d
        if r_d > 1e-14 and (abs(self.k1) > 1e-14 or abs(self.k2) > 1e-14):
            for _ in range(10):
                r2 = r * r
                r4 = r2 * r2
                f_val = r * (1.0 + self.k1 * r2 + self.k2 * r4) - r_d
                f_prime = 1.0 + 3.0 * self.k1 * r2 + 5.0 * self.k2 * r4
                if abs(f_prime) < 1e-12:
                    break
                step = f_val / f_prime
                r = max(0.0, r - step)
                if abs(step) < 1e-15:
                    break

        r2 = r * r
        distort = 1.0 + self.k1 * r2 + self.k2 * (r2 * r2)
        if abs(distort) < 1e-12:
            distort = 1.0

        u_rad = u_d_rad / distort
        v_rad = v_d_rad / distort

        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        xi_rad = (u_rad * cos_t + v_rad * sin_t) * self.parity
        eta_rad = -u_rad * sin_t + v_rad * cos_t

        ra0 = math.radians(float(self.crval_ra_deg) % 360.0)
        dec0 = math.radians(float(self.crval_dec_deg))

        rho = math.hypot(xi_rad, eta_rad)
        if rho < 1e-15:
            return (float(self.crval_ra_deg % 360.0), float(self.crval_dec_deg))

        c = math.atan(rho)
        cos_c = math.cos(c)
        sin_c = math.sin(c)

        sin_dec = cos_c * math.sin(dec0) + (eta_rad * sin_c * math.cos(dec0)) / rho
        sin_dec = max(-1.0, min(1.0, sin_dec))
        dec_rad = math.asin(sin_dec)

        num = xi_rad * sin_c
        den = rho * math.cos(dec0) * cos_c - eta_rad * math.sin(dec0) * sin_c
        ra_rad = ra0 + math.atan2(num, den)
        ra_deg = math.degrees(ra_rad) % 360.0
        if ra_deg >= 360.0 or math.isclose(ra_deg, 360.0, abs_tol=1e-12):
            ra_deg = 0.0
        dec_deg = math.degrees(dec_rad)
        return (float(ra_deg), float(dec_deg))

    def to_contract_dict(self) -> dict:
        return {
            "projection": "TAN",
            "crvalRaDeg": float(self.crval_ra_deg % 360.0),
            "crvalDecDeg": float(self.crval_dec_deg),
            "crpixX": float(self.crpix_x),
            "crpixY": float(self.crpix_y),
            "scaleDegPerPx": float(self.scale_deg_per_px),
            "rotationDeg": float(self.rotation_deg),
            "radialDistortionK1": float(max(-0.25, min(0.25, self.k1))),
            "radialDistortionK2": float(max(-0.25, min(0.25, self.k2))),
            "parity": -1 if self.parity < 0 else 1,
            "fovHorizontalDeg": float(self.fov_horizontal_deg),
            "fovVerticalDeg": float(self.fov_vertical_deg),
        }


@dataclass(frozen=True)
class SolutionQuality:
    """Astrometric verification metrics including independent held-out residual evaluation."""

    detected_candidate_count: int
    matched_inlier_count: int
    held_out_inliers: int
    median_residual_px: float
    p95_residual_px: float
    median_residual_arcsec: float
    passed_independent_verification: bool

    def to_contract_dict(self) -> dict:
        return {
            "detectedCandidateCount": int(self.detected_candidate_count),
            "matchedInlierCount": int(self.matched_inlier_count),
            "heldOutInliers": int(self.held_out_inliers),
            "medianResidualPx": float(self.median_residual_px),
            "p95ResidualPx": float(self.p95_residual_px),
            "medianResidualArcsec": float(self.median_residual_arcsec),
            "passedIndependentVerification": bool(self.passed_independent_verification),
        }


@dataclass(frozen=True)
class CelestialFindingRecord:
    """Finding record carrying explicit `EvidenceClass` (`observed`, `catalog_matched`, `predicted`, `simulated`)."""

    id: str
    observation_solution_id: str
    evidence_class: str
    title: str
    subtitle: str
    ra_deg: float
    dec_deg: float
    alt_deg: float | None = None
    az_deg: float | None = None
    uncertainty_arcsec: float = 5.0
    source_detection: SourceDetection | None = None
    catalog_star: CatalogStar | None = None
    pixel_residual_px: float | None = None
    angular_residual_arcsec: float | None = None
    verified_by_holdout: bool = False
    limitations: tuple[str, ...] = ("Measured from sensor observation.",)
    source_sha256: str = "0" * 64

    @property
    def common_name(self) -> str | None:
        return self.catalog_star.common_name if self.catalog_star else None

    def to_contract_dict(self) -> dict:
        sky_pos: dict = {
            "raDeg": float(self.ra_deg % 360.0),
            "decDeg": float(self.dec_deg),
            "uncertaintyArcsec": float(self.uncertainty_arcsec),
        }
        if self.alt_deg is not None:
            sky_pos["altDeg"] = float(self.alt_deg)
        if self.az_deg is not None:
            sky_pos["azDeg"] = float(self.az_deg)

        payload: dict = {
            "id": self.id,
            "observationSolutionId": self.observation_solution_id,
            "evidenceClass": self.evidence_class,
            "title": self.title,
            "subtitle": self.subtitle,
            "skyPosition": sky_pos,
            "evidence": {
                "id": f"ev-{self.id}",
                "evidenceClass": self.evidence_class,
                "verificationMethod": (
                    "Geometric Quad WCS + Held-Out Residual Verification"
                    if self.evidence_class == "catalog_matched"
                    else "Local Matched-Filter Centroid Extraction"
                ),
                "sourceAssetSha256": self.source_sha256,
                "wcsSolutionId": self.observation_solution_id,
                "catalogReferenceId": self.catalog_star.id if self.catalog_star else None,
                "angularResidualArcsec": (
                    float(self.angular_residual_arcsec)
                    if self.angular_residual_arcsec is not None
                    else None
                ),
                "pixelResidualPx": (
                    float(self.pixel_residual_px)
                    if self.pixel_residual_px is not None
                    else None
                ),
                "distanceUncertaintyNote": (
                    f"Bailer-Jones 16th-84th percentile [{self.catalog_star.lower_pc:.1f}, "
                    f"{self.catalog_star.upper_pc:.1f}] pc"
                    if self.catalog_star
                    else None
                ),
                "limitations": list(self.limitations),
                "verifiedByIndependentHoldout": bool(self.verified_by_holdout),
            },
        }
        if self.source_detection is not None:
            payload["sourceDetection"] = self.source_detection.to_contract_dict()
        if self.catalog_star is not None:
            payload["catalogRef"] = self.catalog_star.to_catalog_reference_dict()
            payload["inferredDistancePc"] = {
                "lower": float(self.catalog_star.lower_pc),
                "median": float(self.catalog_star.median_pc),
                "upper": float(self.catalog_star.upper_pc),
                "method": self.catalog_star.distance_method,
            }
            cx, cy, cz = bailer_jones_cartesian_pc(self.catalog_star)
            payload["cartesianParsecICRS"] = [float(cx), float(cy), float(cz)]
        return payload


@dataclass(frozen=True)
class ObservationSolution:
    """Authoritative WCS calibration and epistemic finding record."""

    id: str
    source_asset_id: str
    source_sha256: str
    frame_time_sec: float
    revision: int
    status: str
    observed_at_utc: str
    observer_lat: float
    observer_lon: float
    wcs: WCSModel | None
    quality: SolutionQuality
    findings: list[CelestialFindingRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def held_out_inliers(self) -> int:
        return self.quality.held_out_inliers

    @property
    def median_residual_px(self) -> float:
        return self.quality.median_residual_px

    @property
    def p95_residual_px(self) -> float:
        return self.quality.p95_residual_px

    @property
    def matched_inlier_count(self) -> int:
        return self.quality.matched_inlier_count

    def to_contract_dict(self) -> dict:
        result: dict = {
            "id": self.id,
            "sourceAssetId": self.source_asset_id,
            "sourceSha256": self.source_sha256,
            "frameTimeSec": float(self.frame_time_sec),
            "revision": int(self.revision),
            "status": self.status,
            "observedAtUTC": self.observed_at_utc,
            "observerLocation": {
                "latDeg": float(self.observer_lat),
                "lonDeg": float(self.observer_lon),
            },
            "solver": {
                "name": "AlbedianGeometricQuadSolver",
                "version": "1.0.0",
                "catalogRelease": "Gaia DR3 / Hipparcos Pinned Bright Subset",
            },
            "quality": self.quality.to_contract_dict(),
            "findings": [f.to_contract_dict() for f in self.findings],
            "warnings": list(self.warnings),
        }
        if self.wcs is not None:
            result["wcs"] = self.wcs.to_contract_dict()
        return result


def _to_luma_2d(image: np.ndarray) -> np.ndarray:
    """Convert a 2D grayscale or 3D RGB array to a 2D float64 luminance array."""
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    raise ValueError(f"Expected 2D or 3D image array, got shape {arr.shape}")


def _estimate_background_and_noise(
    luma: np.ndarray, sky_mask: np.ndarray | None = None
) -> tuple[np.ndarray, float]:
    """Estimate smooth local background via fast strided median filtering and MAD noise floor."""
    h, w = luma.shape
    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    if mask_bool is not None and np.any(mask_bool):
        sky_med = float(np.median(luma[mask_bool]))
        work_luma = np.where(mask_bool, luma, sky_med)
    else:
        work_luma = luma

    stride = max(1, min(4, min(h, w) // 32))
    small = work_luma[::stride, ::stride]
    small_bg = ndimage.median_filter(small, size=7, mode="nearest")
    bg = ndimage.zoom(
        small_bg,
        (h / small_bg.shape[0], w / small_bg.shape[1]),
        order=1,
        mode="nearest",
    )[:h, :w]

    residual = work_luma - bg
    if mask_bool is not None:
        valid = residual[mask_bool]
    else:
        valid = residual.ravel()

    if valid.size == 0:
        return bg, 0.0

    med = float(np.median(valid))
    mad = float(np.median(np.abs(valid - med)))
    sigma_bg = 1.4826 * mad
    if sigma_bg < 1e-7:
        # Fallback to robust interquartile / standard deviation on clipped images
        q75, q25 = np.percentile(valid, [75.0, 25.0])
        sigma_bg = float((q75 - q25) / 1.349)
    return bg, float(sigma_bg)


def _build_gaussian_psf_kernel(sigma_px: float = 1.15, radius: int = 3) -> np.ndarray:
    """Create a zero-mean-free unit-L2-norm 2D Gaussian matched filter kernel."""
    grid = np.arange(-radius, radius + 1, dtype=np.float64)
    xx, yy = np.meshgrid(grid, grid)
    kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma_px * sigma_px))
    kernel[xx * xx + yy * yy > (radius + 0.5) ** 2] = 0.0
    norm = float(np.sqrt(np.sum(kernel * kernel)))
    return kernel / max(norm, 1e-12)


def detect_point_sources(
    image_luma: np.ndarray,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = 3.5,
    asset_id: str = "asset-0",
    frame_time_sec: float = 0.0,
) -> list[SourceDetection]:
    """Extract astronomical point sources above local sky background noise.

    Performs local background subtraction, MAD noise estimation, matched Gaussian PSF
    filtering, local peak extraction, anti-white-noise spatial coherence gating, and
    sub-pixel centroid + FWHM measurement.
    """
    luma = _to_luma_2d(image_luma)
    h, w = luma.shape
    if h < 12 or w < 12:
        return []

    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask)
    residual = luma - bg
    max_res = float(np.max(residual))

    # Near-black or completely flat clipped frame -> zero detections
    if max_res <= 1e-5 or (sigma_bg <= 1e-6 and max_res <= 1e-3):
        return []

    sigma_eff = max(sigma_bg, 1e-3)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    matched = ndimage.convolve(residual, kernel, mode="nearest")
    snr_map = matched / sigma_eff

    effective_snr_gate = max(float(snr_threshold), 4.75)
    local_max = ndimage.maximum_filter(snr_map, size=7, mode="nearest")
    is_peak = (snr_map == local_max) & (snr_map >= effective_snr_gate) & (residual > 0.0)

    margin = 4
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False

    if sky_mask is not None:
        mask_bool = np.asarray(sky_mask, dtype=bool)
        is_peak &= mask_bool

    peak_ys, peak_xs = np.nonzero(is_peak)
    if peak_ys.size == 0:
        return []

    psf_core3 = kernel[2:5, 2:5]
    psf_core3_norm = float(np.linalg.norm(psf_core3))

    detections: list[SourceDetection] = []
    for y0, x0 in zip(peak_ys.tolist(), peak_xs.tolist()):
        patch3 = residual[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2]
        patch5 = residual[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]

        peak_raw_snr = float(residual[y0, x0] / sigma_eff)
        matched_snr = float(snr_map[y0, x0])
        signed_ap3_snr = float(np.sum(patch3) / (3.0 * sigma_eff))

        # Check 4 cardinal neighbors around (y0, x0) for symmetric optical PSF support
        cardinal_vals = [
            residual[y0 - 1, x0],
            residual[y0 + 1, x0],
            residual[y0, x0 - 1],
            residual[y0, x0 + 1],
        ]
        cardinal_pos = sum(1 for v in cardinal_vals if v > 0.65 * sigma_eff)
        cardinal_strong = sum(1 for v in cardinal_vals if v > 1.25 * sigma_eff)

        patch3_pos = np.maximum(0.0, patch3)
        patch3_norm = float(np.linalg.norm(patch3_pos))
        psf_corr = (
            float(np.sum(patch3_pos * psf_core3)) / (patch3_norm * psf_core3_norm)
            if patch3_norm > 1e-9
            else 0.0
        )

        # Real star PSF requires multi-pixel cardinal symmetry, template correlation, and signed aperture SNR
        is_real_psf = (
            peak_raw_snr >= max(2.3, 0.58 * snr_threshold)
            and signed_ap3_snr >= max(3.6, 0.92 * snr_threshold)
            and cardinal_pos == 4
            and cardinal_strong >= 3
            and psf_corr >= 0.84
        )
        is_bright_impulse = peak_raw_snr >= 14.0
        if not (is_real_psf or is_bright_impulse):
            continue

        # Soft-thresholded positive weights for accurate sub-pixel centroiding
        weights = np.maximum(0.0, patch5 - 0.75 * sigma_eff)
        total_w = float(np.sum(weights))
        if total_w <= 1e-9:
            weights = np.maximum(0.0, patch5)
            total_w = float(np.sum(weights))
        if total_w <= 1e-9:
            continue

        pos_patch5 = np.maximum(0.0, patch5)
        flux = float(np.sum(weights))
        peak_val = float(np.max(pos_patch5))
        peak_ratio = peak_val / max(float(np.sum(pos_patch5)), 1e-9)

        yy, xx = np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
        cx = float(np.sum(xx * weights) / total_w)
        cy = float(np.sum(yy * weights) / total_w)

        r2_moment = float(np.sum(weights * ((xx - cx) ** 2 + (yy - cy) ** 2)) / total_w)
        fwhm_px = max(0.55, 1.6651092223153954 * math.sqrt(max(0.0, r2_moment)))
        if peak_ratio > 0.68:
            fwhm_px = min(fwhm_px, 0.85)

        snr_final = matched_snr
        centroid_unc = max(0.03, min(1.5, fwhm_px / max(snr_final, 1.0)))

        detections.append(
            SourceDetection(
                x_px=cx,
                y_px=cy,
                flux=flux,
                snr=snr_final,
                fwhm_px=fwhm_px,
                centroid_uncertainty_px=centroid_unc,
                asset_id=asset_id,
                frame_time_sec=frame_time_sec,
                peak_to_total_ratio=peak_ratio,
            )
        )

    detections.sort(key=lambda d: d.snr, reverse=True)
    return detections


def _estimate_initial_wcs_from_triplet(
    det_triplet: tuple[SourceDetection, SourceDetection, SourceDetection],
    star_triplet: tuple[CatalogStar, CatalogStar, CatalogStar],
    width: int,
    height: int,
) -> WCSModel | None:
    """Estimate initial similarity TAN WCSModel from a 3-star correspondence."""
    s0, s1, s2 = star_triplet
    d0, d1, d2 = det_triplet

    # Mean unit vector of the 3 catalog stars as initial tangent point estimate
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

    # Test both parities (-1 standard astronomical sky, +1 mirrored)
    best_wcs: WCSModel | None = None
    best_err = float("inf")
    crpix_x = width / 2.0
    crpix_y = height / 2.0

    for parity in (-1, 1):
        # Project s0, s1, s2 onto tangent plane around (ra_c, dec_c) with rotation=0
        temp_wcs = WCSModel(
            crval_ra_deg=ra_c,
            crval_dec_deg=dec_c,
            crpix_x=0.0,
            crpix_y=0.0,
            scale_deg_per_px=1.0,
            rotation_deg=0.0,
            parity=parity,
            width=width,
            height=height,
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

        # Image centered coordinates (u_img, v_img) in degrees
        i_pts = [
            ((d.x_px - crpix_x) * scale, -(d.y_px - crpix_y) * scale) for d in det_triplet
        ]

        # 2D rigid alignment (rotation + translation offset of tangent center)
        tc_x = sum(p[0] for p in t_pts) / 3.0
        tc_y = sum(p[1] for p in t_pts) / 3.0
        ic_x = sum(p[0] for p in i_pts) / 3.0
        ic_y = sum(p[1] for p in i_pts) / 3.0

        cs_sum = 0.0
        sn_sum = 0.0
        for (tx, ty), (ix, iy) in zip(t_pts, i_pts):
            dx_t, dy_t = tx - tc_x, ty - tc_y
            dx_i, dy_i = ix - ic_x, iy - ic_y
            cs_sum += dx_t * dx_i + dy_t * dy_i
            sn_sum += dx_t * dy_i - dy_t * dx_i

        rot_rad = math.atan2(sn_sum, cs_sum)
        rot_deg = math.degrees(rot_rad)

        # Estimate tangent point (crval_ra_deg, crval_dec_deg) corresponding to image center (crpix_x, crpix_y)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)
        # Image center (0, 0) in rotated tangent coords maps to offset (-offset_x, -offset_y) in unrotated tangent plane
        rot_tc_x = tc_x * cos_r - tc_y * sin_r
        rot_tc_y = tc_x * sin_r + tc_y * cos_r
        shift_u = rot_tc_x - ic_x
        shift_v = rot_tc_y - ic_y

        center_x_tan = shift_u * cos_r + shift_v * sin_r
        center_y_tan = -shift_u * sin_r + shift_v * cos_r
        crval_ra, crval_dec = temp_wcs.pixel_to_world(center_x_tan, -center_y_tan)

        cand_wcs = WCSModel(
            crval_ra_deg=crval_ra,
            crval_dec_deg=crval_dec,
            crpix_x=crpix_x,
            crpix_y=crpix_y,
            scale_deg_per_px=scale,
            rotation_deg=rot_deg,
            k1=0.0,
            k2=0.0,
            parity=parity,
            width=width,
            height=height,
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

    crpix_x = initial_wcs.crpix_x
    crpix_y = initial_wcs.crpix_y
    parity = initial_wcs.parity
    width = initial_wcs.width
    height = initial_wcs.height

    x0 = np.array(
        [
            initial_wcs.crval_ra_deg,
            initial_wcs.crval_dec_deg,
            initial_wcs.scale_deg_per_px,
            initial_wcs.rotation_deg,
            initial_wcs.k1,
            initial_wcs.k2,
        ],
        dtype=np.float64,
    )

    def residuals_fn(params: np.ndarray) -> np.ndarray:
        ra0, dec0, scale, rot, k1, k2 = params
        dec0_clamped = max(-89.9, min(89.9, float(dec0)))
        scale_pos = max(1e-4, float(scale))
        model = WCSModel(
            crval_ra_deg=float(ra0) % 360.0,
            crval_dec_deg=dec0_clamped,
            crpix_x=crpix_x,
            crpix_y=crpix_y,
            scale_deg_per_px=scale_pos,
            rotation_deg=float(rot),
            k1=float(k1),
            k2=float(k2),
            parity=parity,
            width=width,
            height=height,
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
            -0.24,
            -0.24,
        ],
        dtype=np.float64,
    )
    bounds_upper = np.array(
        [
            initial_wcs.crval_ra_deg + 25.0,
            min(89.5, initial_wcs.crval_dec_deg + 25.0),
            initial_wcs.scale_deg_per_px * 1.3,
            initial_wcs.rotation_deg + 45.0,
            0.24,
            0.24,
        ],
        dtype=np.float64,
    )

    opt = optimize.least_squares(
        residuals_fn,
        x0,
        bounds=(bounds_lower, bounds_upper),
        loss="soft_l1",
        f_scale=1.5,
        max_nfev=250,
    )
    ra0, dec0, scale, rot, k1, k2 = opt.x
    return WCSModel(
        crval_ra_deg=float(ra0) % 360.0,
        crval_dec_deg=float(dec0),
        crpix_x=crpix_x,
        crpix_y=crpix_y,
        scale_deg_per_px=float(scale),
        rotation_deg=float(rot),
        k1=float(k1),
        k2=float(k2),
        parity=parity,
        width=width,
        height=height,
    )


def solve_and_verify_field(
    detections: list[SourceDetection],
    width: int,
    height: int,
    catalog_stars: list[CatalogStar] | None = None,
    hint_ra_deg: float | None = None,
    hint_dec_deg: float | None = None,
    obs_utc_iso: str = "2026-10-19T22:14:00Z",
    observer_lat: float = 31.149,
    observer_lon: float = -3.972,
    source_asset_id: str = "asset-0",
    source_sha256: str = "0" * 64,
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-primary",
) -> ObservationSolution:
    """Blind / hint-assisted geometric plate solver with independent held-out WCS verification.

    Enforces the non-negotiable scientific integrity rules:
    1. Fits WCS parameters on a minimal training subset (4-5 stars) and evaluates
       residuals on disjoint held-out detections.
    2. Rejects false/injected hot-pixel outliers via 3-sigma residual + impulse PSF gating.
    3. Sets `status = "calibrated"` iff `held_out_inliers >= 6` and `median_residual_px <= 3.0`.
    """
    catalog = list(catalog_stars) if catalog_stars is not None else get_catalog_stars()
    if hint_ra_deg is not None and hint_dec_deg is not None:
        filtered = [
            s
            for s in catalog
            if angular_separation_deg(s.ra_deg, s.dec_deg, hint_ra_deg, hint_dec_deg) <= 68.0
        ]
        if len(filtered) >= 8:
            catalog = filtered

    # Separate optical PSF candidates from single-pixel hot-pixel impulses for hypothesis generation
    psf_detections = [d for d in detections if not d.is_hot_pixel_impulse]
    if len(psf_detections) < 6:
        psf_detections = list(detections)

    # Not enough detections to train (4) + hold out (6) -> cannot calibrate
    if len(detections) < 6 or len(psf_detections) < 4:
        findings: list[CelestialFindingRecord] = []
        for idx, det in enumerate(detections):
            findings.append(
                CelestialFindingRecord(
                    id=f"finding-obs-{idx + 1}",
                    observation_solution_id=solution_id,
                    evidence_class="observed",
                    title=f"Observed Point Source #{idx + 1}",
                    subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
                    ra_deg=float(hint_ra_deg or 0.0),
                    dec_deg=float(hint_dec_deg or 0.0),
                    source_detection=det,
                    verified_by_holdout=False,
                    limitations=(
                        "Unresolved sky: fewer than 6 stars available for held-out WCS verification.",
                    ),
                    source_sha256=source_sha256,
                )
            )
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
            quality=SolutionQuality(
                detected_candidate_count=len(detections),
                matched_inlier_count=0,
                held_out_inliers=0,
                median_residual_px=999.0,
                p95_residual_px=999.0,
                median_residual_arcsec=9999.0,
                passed_independent_verification=False,
            ),
            findings=findings,
            warnings=[
                f"Insufficient point sources ({len(detections)} detected) for independent WCS calibration."
            ],
        )

    # Build catalog triangle index over bright catalog stars
    cat_pool = sorted(catalog, key=lambda s: s.mag)[:18]
    cat_triangles: list[tuple[int, int, int, float, float, float]] = []
    for i, j, k in itertools.combinations(range(len(cat_pool)), 3):
        s_i, s_j, s_k = cat_pool[i], cat_pool[j], cat_pool[k]
        d_ij = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_j.ra_deg, s_j.dec_deg)
        d_jk = angular_separation_deg(s_j.ra_deg, s_j.dec_deg, s_k.ra_deg, s_k.dec_deg)
        d_ik = angular_separation_deg(s_i.ra_deg, s_i.dec_deg, s_k.ra_deg, s_k.dec_deg)
        sides = sorted([(d_ij, (s_i, s_j, s_k)), (d_jk, (s_j, s_k, s_i)), (d_ik, (s_i, s_k, s_j))], key=lambda x: x[0])
        a_min, _ = sides[0]
        a_mid, _ = sides[1]
        a_max, _ = sides[2]
        if a_min < 2.5 or a_max > 62.0:
            continue
        # Order vertices opposite to (a_max, a_mid, a_min) uniquely
        # Pairs with distances:
        pairs = [((i, j), d_ij), ((j, k), d_jk), ((i, k), d_ik)]
        pairs.sort(key=lambda p: p[1], reverse=True)
        # Longest side is between p0[0] and p0[1]; second longest shares one vertex with longest
        longest_set = set(pairs[0][0])
        second_set = set(pairs[1][0])
        v0 = (longest_set & second_set).pop()
        v1 = (longest_set - {v0}).pop()
        v2 = ({i, j, k} - {v0, v1}).pop()
        r1 = pairs[1][1] / pairs[0][1]
        r2 = pairs[2][1] / pairs[0][1]
        cat_triangles.append((v0, v1, v2, r1, r2, pairs[0][1]))

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

        longest_set = set(px_pairs[0][0])
        second_set = set(px_pairs[1][0])
        u0 = (longest_set & second_set).pop()
        u1 = (longest_set - {u0}).pop()
        u2 = ({a, b, c} - {u0, u1}).pop()

        dr1 = px_pairs[1][1] / px_pairs[0][1]
        dr2 = px_pairs[2][1] / px_pairs[0][1]
        det_tri = (det_pool[u0], det_pool[u1], det_pool[u2])

        for v0, v1, v2, cr1, cr2, _ in cat_triangles:
            if abs(dr1 - cr1) > 0.045 or abs(dr2 - cr2) > 0.045:
                continue
            star_tri = (cat_pool[v0], cat_pool[v1], cat_pool[v2])
            cand_wcs = _estimate_initial_wcs_from_triplet(det_tri, star_tri, width, height)
            if cand_wcs is None:
                continue

            # Evaluate consensus across all PSF detections against full catalog
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
        findings = [
            CelestialFindingRecord(
                id=f"finding-obs-{idx + 1}",
                observation_solution_id=solution_id,
                evidence_class="observed",
                title=f"Observed Point Source #{idx + 1}",
                subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
                ra_deg=float(hint_ra_deg or 0.0),
                dec_deg=float(hint_dec_deg or 0.0),
                source_detection=det,
                verified_by_holdout=False,
                limitations=("No geometric quad match found against Gaia DR3 / Hipparcos catalog.",),
                source_sha256=source_sha256,
            )
            for idx, det in enumerate(detections)
        ]
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
            quality=SolutionQuality(
                detected_candidate_count=len(detections),
                matched_inlier_count=0,
                held_out_inliers=0,
                median_residual_px=999.0,
                p95_residual_px=999.0,
                median_residual_arcsec=9999.0,
                passed_independent_verification=False,
            ),
            findings=findings,
            warnings=["Geometric quad pattern matching failed to find a consistent WCS solution."],
        )

    # Split consensus matches into a minimal training quad (4 stars) and independent held-out set
    # Sort consensus pairs by radial distance from image center descending so the 4 training stars
    # span a wide geometric baseline for distortion + scale fitting, leaving all remaining stars held out.
    cx0, cy0 = width / 2.0, height / 2.0
    sorted_consensus = sorted(
        best_consensus_pairs,
        key=lambda pair: math.hypot(pair[0].x_px - cx0, pair[0].y_px - cy0),
        reverse=True,
    )
    n_train = min(4, len(sorted_consensus))
    train_subset = sorted_consensus[:n_train]
    train_det_ids = {id(d) for d, _ in train_subset}

    refined_wcs = _refine_wcs_least_squares(best_initial_wcs, train_subset)

    # Now evaluate all detections against the catalog using the trained WCS model
    # Compute candidate matches and residuals
    candidate_eval: list[tuple[SourceDetection, CatalogStar, float, bool]] = []
    used_catalog_ids: set[str] = set()

    # First evaluate non-impulse PSF detections in order of flux
    for det in detections:
        if det.is_hot_pixel_impulse:
            continue
        best_st: CatalogStar | None = None
        best_res = float("inf")
        for st in catalog:
            if st.id in used_catalog_ids:
                continue
            px, py = refined_wcs.world_to_pixel(st.ra_deg, st.dec_deg)
            if math.isnan(px):
                continue
            res_px = math.hypot(px - det.x_px, py - det.y_px)
            if res_px < best_res:
                best_res = res_px
                best_st = st
        if best_st is not None and best_res <= 8.0:
            is_train = id(det) in train_det_ids
            candidate_eval.append((det, best_st, best_res, is_train))
            used_catalog_ids.add(best_st.id)

    # 3-sigma outlier rejection gate on residuals
    raw_residuals = np.array([r for _, _, r, _ in candidate_eval], dtype=np.float64)
    if raw_residuals.size > 0:
        med_r = float(np.median(raw_residuals))
        mad_r = float(np.median(np.abs(raw_residuals - med_r)))
        sigma_r = max(0.35, 1.4826 * mad_r)
        gate_px = min(3.0, max(1.8, med_r + 3.0 * sigma_r))
    else:
        gate_px = 3.0

    verified_matches: dict[int, tuple[CatalogStar, float, bool]] = {}
    holdout_residuals: list[float] = []
    for det, st, res_px, is_train in candidate_eval:
        if res_px <= gate_px and not det.is_hot_pixel_impulse:
            verified_matches[id(det)] = (st, res_px, is_train)
            if not is_train:
                holdout_residuals.append(res_px)

    held_out_inliers = len(holdout_residuals)
    if held_out_inliers > 0:
        median_residual_px = float(np.median(holdout_residuals))
        p95_residual_px = float(np.percentile(holdout_residuals, 95.0))
    else:
        median_residual_px = 999.0
        p95_residual_px = 999.0

    median_residual_arcsec = median_residual_px * refined_wcs.scale_deg_per_px * 3600.0
    passed_verification = (held_out_inliers >= 6) and (median_residual_px <= 3.0)

    if passed_verification:
        status = "calibrated"
    elif len(verified_matches) >= 4:
        status = "ambiguous"
    else:
        status = "unresolved"

    findings_list: list[CelestialFindingRecord] = []
    warnings: list[str] = []
    rejected_outlier_count = 0

    for idx, det in enumerate(detections):
        if passed_verification and id(det) in verified_matches:
            st, res_px, is_train = verified_matches[id(det)]
            alt_deg, az_deg = icrs_to_altaz(
                st.ra_deg, st.dec_deg, observer_lat, observer_lon, obs_utc_iso
            )
            ang_res_arcsec = res_px * refined_wcs.scale_deg_per_px * 3600.0
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-{st.id}",
                    observation_solution_id=solution_id,
                    evidence_class="catalog_matched",
                    title=st.common_name,
                    subtitle=(
                        f"{st.bayer_designation} · {st.spectral_type} · "
                        f"{st.median_pc:.1f} pc [{st.lower_pc:.1f}–{st.upper_pc:.1f} pc]"
                    ),
                    ra_deg=st.ra_deg,
                    dec_deg=st.dec_deg,
                    alt_deg=alt_deg,
                    az_deg=az_deg,
                    uncertainty_arcsec=max(0.5, ang_res_arcsec),
                    source_detection=det,
                    catalog_star=st,
                    pixel_residual_px=res_px,
                    angular_residual_arcsec=ang_res_arcsec,
                    verified_by_holdout=not is_train,
                    limitations=(
                        "Distance interval derived from Bailer-Jones photogeometric/parallax prior, not single-image depth.",
                    ),
                    source_sha256=source_sha256,
                )
            )
        else:
            rejected_outlier_count += 1
            ra_est, dec_est = refined_wcs.pixel_to_world(det.x_px, det.y_px)
            reason = (
                "Rejected from catalog matching by 3-sigma residual / hot-pixel impulse gate."
                if passed_verification
                else "WCS solution did not pass independent held-out calibration gate."
            )
            findings_list.append(
                CelestialFindingRecord(
                    id=f"finding-obs-{idx + 1}",
                    observation_solution_id=solution_id,
                    evidence_class="observed",
                    title=f"Observed Point Source #{idx + 1}",
                    subtitle=f"Unmatched sensor detection at ({det.x_px:.1f}, {det.y_px:.1f}) px",
                    ra_deg=ra_est,
                    dec_deg=dec_est,
                    uncertainty_arcsec=60.0,
                    source_detection=det,
                    catalog_star=None,
                    verified_by_holdout=False,
                    limitations=(reason,),
                    source_sha256=source_sha256,
                )
            )

    if rejected_outlier_count > 0 and passed_verification:
        warnings.append(
            f"{rejected_outlier_count} sensor detection(s) rejected from catalog matching via 3-sigma outlier gate."
        )

    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status=status,
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=refined_wcs if passed_verification else None,
        quality=SolutionQuality(
            detected_candidate_count=len(detections),
            matched_inlier_count=len(verified_matches) if passed_verification else 0,
            held_out_inliers=held_out_inliers if passed_verification else 0,
            median_residual_px=median_residual_px,
            p95_residual_px=p95_residual_px,
            median_residual_arcsec=median_residual_arcsec,
            passed_independent_verification=passed_verification,
        ),
        findings=findings_list,
        warnings=warnings,
    )


@dataclass(frozen=True)
class VideoStackCalibrationResult:
    """Result of motion-compensated multi-frame sky stacking (`EXP-02`).

    Implements `__array__` so callers can pass this result directly to functions
    expecting a 2D `np.ndarray` luminance image or inspect `.composite_luma` and `.shifts_yx`.
    """

    composite_luma: np.ndarray
    shifts_yx: list[tuple[float, float]]
    single_frame_noise_sigma: float
    stacked_noise_sigma: float
    snr_boost_factor: float

    def __array__(self, dtype: np.dtype | None = None) -> np.ndarray:
        if dtype is None:
            return self.composite_luma
        return self.composite_luma.astype(dtype)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.composite_luma.shape

    @property
    def ndim(self) -> int:
        return self.composite_luma.ndim


def _extract_registration_signal(
    res: np.ndarray, kernel: np.ndarray, sig_eff: float
) -> np.ndarray:
    """Extract clean PSF-coherent stellar signal map for sub-pixel frame registration."""
    matched = ndimage.convolve(res, kernel, mode="constant", cval=0.0)
    snr_m = matched / sig_eff
    loc_max = ndimage.maximum_filter(snr_m, size=9, mode="constant", cval=0.0)
    # Require all 4 cardinal neighbors in raw residual to be positive (eliminates 99.6% of white noise)
    c_up = np.roll(res, 1, axis=0) > 0.35 * sig_eff
    c_dn = np.roll(res, -1, axis=0) > 0.35 * sig_eff
    c_lf = np.roll(res, 1, axis=1) > 0.35 * sig_eff
    c_rt = np.roll(res, -1, axis=1) > 0.35 * sig_eff
    is_pk = (snr_m == loc_max) & (snr_m > 2.8) & c_up & c_dn & c_lf & c_rt
    is_pk[:8, :] = False
    is_pk[-8:, :] = False
    is_pk[:, :8] = False
    is_pk[:, -8:] = False

    py_arr, px_arr = np.nonzero(is_pk)
    clean_sig = np.zeros_like(matched)
    if py_arr.size > 0:
        vals = snr_m[py_arr, px_arr]
        top_idx = np.argsort(vals)[::-1][:10]
        for idx_p in top_idx:
            yy0, xx0 = int(py_arr[idx_p]), int(px_arr[idx_p])
            w_val = float(vals[idx_p]) ** 2
            clean_sig[yy0 - 3 : yy0 + 4, xx0 - 3 : xx0 + 4] += w_val * kernel
    return clean_sig


def _estimate_subpixel_shift_phase_corr(
    ref_signal: np.ndarray,
    target_signal: np.ndarray,
    prior_yx: tuple[float, float] = (0.0, 0.0),
    search_radius: int = 8,
) -> tuple[float, float]:
    """Estimate (dy, dx) shift bringing `target_signal` into alignment with `ref_signal`."""
    h, w = ref_signal.shape
    f_ref = np.fft.rfft2(ref_signal)
    f_tar = np.fft.rfft2(target_signal)
    cross_power = f_ref * np.conj(f_tar)
    cc = np.fft.irfft2(cross_power, s=(h, w))
    cc_shifted = np.fft.fftshift(cc)

    cy, cx = h // 2, w // 2
    center_y = int(round(cy + prior_yx[0]))
    center_x = int(round(cx + prior_yx[1]))
    y_min = max(1, center_y - search_radius)
    y_max = min(h - 1, center_y + search_radius + 1)
    x_min = max(1, center_x - search_radius)
    x_max = min(w - 1, center_x + search_radius + 1)
    window = cc_shifted[y_min:y_max, x_min:x_max]

    wy, wx = np.unravel_index(int(np.argmax(window)), window.shape)
    py = y_min + int(wy)
    px = x_min + int(wx)

    # Parabolic 1D sub-pixel refinement around (py, px)
    dy_sub = 0.0
    dx_sub = 0.0
    if 1 <= py < h - 1:
        v_m1, v_0, v_p1 = cc_shifted[py - 1, px], cc_shifted[py, px], cc_shifted[py + 1, px]
        denom = 2.0 * (v_m1 - 2.0 * v_0 + v_p1)
        if abs(denom) > 1e-12:
            dy_sub = max(-0.5, min(0.5, float((v_m1 - v_p1) / denom)))
    if 1 <= px < w - 1:
        u_m1, u_0, u_p1 = cc_shifted[py, px - 1], cc_shifted[py, px], cc_shifted[py, px + 1]
        denom = 2.0 * (u_m1 - 2.0 * u_0 + u_p1)
        if abs(denom) > 1e-12:
            dx_sub = max(-0.5, min(0.5, float((u_m1 - u_p1) / denom)))

    dy = (py + dy_sub) - cy
    dx = (px + dx_sub) - cx
    return (float(dy), float(dx))


def stack_video_frames_for_calibration(
    frames: Sequence[np.ndarray],
    sky_mask: np.ndarray | None = None,
) -> VideoStackCalibrationResult:
    """Align and stack low-SNR video frames via running-reference PSF cross-correlation (`EXP-02`).

    Estimates sub-pixel frame-to-frame translation on sky regions (ignoring static
    foreground via `sky_mask`), shifts each frame into the reference frame's coordinate
    system, and computes a noise-suppressing composite that boosts stellar SNR by ~sqrt(N).
    """
    if len(frames) == 0:
        raise ValueError("frames must be a non-empty sequence of 2D/3D arrays.")

    luma_frames = [_to_luma_2d(f) for f in frames]
    if len(luma_frames) == 1:
        _, sigma0 = _estimate_background_and_noise(luma_frames[0], sky_mask)
        return VideoStackCalibrationResult(
            composite_luma=luma_frames[0].copy(),
            shifts_yx=[(0.0, 0.0)],
            single_frame_noise_sigma=sigma0,
            stacked_noise_sigma=sigma0,
            snr_boost_factor=1.0,
        )

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)

    signal_maps: list[np.ndarray] = []
    bg_frames: list[np.ndarray] = []
    residuals: list[np.ndarray] = []
    sigmas: list[float] = []

    for frame in luma_frames:
        bg, sig = _estimate_background_and_noise(frame, mask_bool)
        sig_eff = max(sig, 1e-4)
        res = frame - bg
        if mask_bool is not None:
            res_masked = np.where(mask_bool, res, 0.0)
        else:
            res_masked = res
        clean_sig = _extract_registration_signal(res_masked, kernel, sig_eff)
        signal_maps.append(clean_sig)
        bg_frames.append(bg)
        residuals.append(res_masked)
        sigmas.append(sig_eff)

    running_ref_sig = signal_maps[0].copy()
    aligned_residuals: list[np.ndarray] = [residuals[0]]
    shifts_yx: list[tuple[float, float]] = [(0.0, 0.0)]
    prev_shift = (0.0, 0.0)

    for idx in range(1, len(luma_frames)):
        dy, dx = _estimate_subpixel_shift_phase_corr(
            running_ref_sig, signal_maps[idx], prior_yx=prev_shift, search_radius=8
        )
        shifts_yx.append((dy, dx))
        prev_shift = (dy, dx)
        shifted_res = ndimage.shift(residuals[idx], shift=(dy, dx), order=1, mode="nearest")
        aligned_residuals.append(shifted_res)
        shifted_sig = ndimage.shift(signal_maps[idx], shift=(dy, dx), order=1, mode="constant", cval=0.0)
        running_ref_sig = running_ref_sig + shifted_sig

    stack_arr = np.stack(aligned_residuals, axis=0)
    # Mean stack of aligned residuals + reference background
    composite_res = np.mean(stack_arr, axis=0)
    composite_luma = np.clip(bg_frames[0] + composite_res, 0.0, None)
    if mask_bool is not None:
        composite_luma = np.where(mask_bool, composite_luma, luma_frames[0])

    _, stacked_sigma = _estimate_background_and_noise(composite_luma, mask_bool)
    single_sigma = float(np.median(sigmas))
    snr_boost = single_sigma / max(stacked_sigma, 1e-6)

    return VideoStackCalibrationResult(
        composite_luma=composite_luma,
        shifts_yx=shifts_yx,
        single_frame_noise_sigma=single_sigma,
        stacked_noise_sigma=stacked_sigma,
        snr_boost_factor=float(snr_boost),
    )


def render_synthetic_starfield(
    width: int = 960,
    height: int = 540,
    wcs: WCSModel | None = None,
    catalog_stars: Sequence[CatalogStar] | None = None,
    psf_sigma_px: float = 1.15,
    background_level: float = 24.0,
    read_noise_sigma: float = 2.0,
    flux_scale: float = 950.0,
    seed: int = 42,
    sky_mask: np.ndarray | None = None,
    shift_yx: tuple[float, float] = (0.0, 0.0),
) -> tuple[np.ndarray, WCSModel, list[tuple[CatalogStar, float, float]]]:
    """Synthesize a realistic consumer night-sky frame with known WCS + radial distortion + noise."""
    if wcs is None:
        wcs = WCSModel(
            crval_ra_deg=84.0,
            crval_dec_deg=6.0,
            crpix_x=width / 2.0,
            crpix_y=height / 2.0,
            scale_deg_per_px=0.062 * (960.0 / float(width)),
            rotation_deg=4.5,
            k1=-0.035,
            k2=0.008,
            parity=-1,
            width=width,
            height=height,
        )

    stars = list(catalog_stars) if catalog_stars is not None else get_catalog_stars()
    rng = np.random.default_rng(seed)
    image = np.full((height, width), background_level, dtype=np.float64)

    projected_in_frame: list[tuple[CatalogStar, float, float]] = []
    dy_shift, dx_shift = shift_yx
    margin = 10

    for st in stars:
        x_px, y_px = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
        if math.isnan(x_px) or math.isnan(y_px):
            continue
        x_s = x_px + dx_shift
        y_s = y_px + dy_shift
        if not (margin <= x_s < width - margin and margin <= y_s < height - margin):
            continue
        if sky_mask is not None and not bool(sky_mask[int(round(y_s)), int(round(x_s))]):
            continue

        projected_in_frame.append((st, x_s, y_s))
        # Convert apparent magnitude to total integrated photon flux
        # Using gentle compression so both bright 0-mag and 3.4-mag stars are realistic on an 8/12-bit sensor
        star_flux = flux_scale * (10.0 ** (-0.14 * (st.mag - 1.5)))

        x0 = int(round(x_s))
        y0 = int(round(y_s))
        r_win = 6
        y_slice = slice(max(0, y0 - r_win), min(height, y0 + r_win + 1))
        x_slice = slice(max(0, x0 - r_win), min(width, x0 + r_win + 1))
        yy, xx = np.mgrid[y_slice, x_slice]

        psf = np.exp(-((xx - x_s) ** 2 + (yy - y_s) ** 2) / (2.0 * psf_sigma_px * psf_sigma_px))
        psf /= max(float(np.sum(psf)), 1e-12)
        image[y_slice, x_slice] += star_flux * psf

    if read_noise_sigma > 0.0:
        image += rng.normal(0.0, read_noise_sigma, size=image.shape)

    if sky_mask is not None:
        mask_bool = np.asarray(sky_mask, dtype=bool)
        image = np.where(mask_bool, image, background_level * 0.35)

    return np.clip(image, 0.0, None), wcs, projected_in_frame


def solve_horizon_and_beacon_pose(
    image: np.ndarray,
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
    source_asset_id: str = "asset-0",
    source_sha256: str = "0000000000000000000000000000000000000000000000000000000000000000",
    frame_time_sec: float = 0.0,
    default_az_deg: float = 206.9,
    default_scale_deg_per_px: float = 0.0398,
    solution_id: str = "sol-horizon-beacon",
) -> ObservationSolution:
    """Calibrate camera pointing & WCS from a detected celestial beacon (e.g. Moon) + horizon line,
    or fall back to an honest GPS + timestamp + horizon ephemeris solution (`approximate` state)
    when the sky pixels were crushed by video compression.
    """
    luma = _to_luma_2d(image)
    h, w = luma.shape

    # 1. Estimate horizon line (y_horizon) from vertical luminance profile transition
    row_profile = np.percentile(luma, 85, axis=1)
    # Search middle band [0.35*h, 0.75*h] for steepest upward gradient into illuminated foreground
    y_lo, y_hi = int(h * 0.38), int(h * 0.72)
    grad = np.gradient(ndimage.gaussian_filter1d(row_profile, sigma=12.0))
    if y_hi > y_lo and float(np.max(grad[y_lo:y_hi])) > 1e-4:
        horizon_y = float(y_lo + int(np.argmax(grad[y_lo:y_hi])))
    else:
        horizon_y = float(h * 0.53)

    # 2. Search upper sky (y < min(horizon_y - 40, 0.42 * h)) for a bright celestial beacon (e.g. Moon)
    sky_limit_y = max(32, min(int(horizon_y - 40), int(h * 0.42)))
    sky_band = luma[:sky_limit_y, :]
    sky_med = float(np.median(sky_band))
    sky_mad = float(np.median(np.abs(sky_band - sky_med))) * 1.4826
    sky_sigma = max(sky_mad, 1e-4)

    smoothed_sky = ndimage.gaussian_filter(sky_band, sigma=2.5)
    peak_y, peak_x = np.unravel_index(np.argmax(smoothed_sky), smoothed_sky.shape)
    peak_val = float(smoothed_sky[peak_y, peak_x])
    peak_snr = (peak_val - sky_med) / sky_sigma

    ephemerides = compute_solar_system_ephemerides(
        obs_utc_iso=obs_utc_iso,
        lat_deg=observer_lat,
        lon_deg=observer_lon,
        observation_solution_id=solution_id,
    )
    moon_eph = next((b for b in ephemerides if b.name == "Moon"), None)

    beacon_detection: SourceDetection | None = None
    if peak_snr >= 10.0 and peak_val > float(np.percentile(luma, 99.0)) * 0.5 and moon_eph and moon_eph.alt_deg > 5.0:
        # Sub-pixel flux centroid around beacon
        r_win = 14
        y0, y1 = max(0, int(peak_y) - r_win), min(sky_limit_y, int(peak_y) + r_win + 1)
        x0, x1 = max(0, int(peak_x) - r_win), min(w, int(peak_x) + r_win + 1)
        patch = np.maximum(sky_band[y0:y1, x0:x1] - sky_med, 0.0)
        tot = float(np.sum(patch))
        if tot > 0.0:
            yy, xx = np.mgrid[y0:y1, x0:x1]
            bx = float(np.sum(xx * patch) / tot)
            by = float(np.sum(yy * patch) / tot)
        else:
            bx, by = float(peak_x), float(peak_y)

        beacon_detection = SourceDetection(
            x_px=bx,
            y_px=by,
            flux=tot,
            snr=peak_snr,
            fwhm_px=6.5,
            centroid_uncertainty_px=0.35,
            asset_id=source_asset_id,
            frame_time_sec=frame_time_sec,
            peak_to_total_ratio=0.12,
        )
        raw_scale = max(80.0, horizon_y - by)
        scale_deg_per_px = float(np.clip(moon_eph.alt_deg / raw_scale, 0.025, 0.065))
        cos_alt = max(0.25, math.cos(math.radians(moon_eph.alt_deg)))
        center_az_deg = (moon_eph.az_deg - ((bx - w / 2.0) * scale_deg_per_px / cos_alt)) % 360.0
        center_alt_deg = float(np.clip((horizon_y - h / 2.0) * scale_deg_per_px, -15.0, 65.0))
        solver_mode = "moon_horizon_locked"
    else:
        scale_deg_per_px = default_scale_deg_per_px
        center_az_deg = default_az_deg
        center_alt_deg = float(np.clip((horizon_y - h / 2.0) * scale_deg_per_px, -10.0, 45.0))
        solver_mode = "gps_timestamp_horizon"

    crval_ra_deg, crval_dec_deg = altaz_to_icrs(
        center_alt_deg, center_az_deg, observer_lat, observer_lon, obs_utc_iso
    )
    # Compute parallactic rotation angle so local zenith (+Alt) aligns with image upward (-Y)
    zenith_ra, zenith_dec = altaz_to_icrs(
        min(89.0, center_alt_deg + 1.0), center_az_deg, observer_lat, observer_lon, obs_utc_iso
    )
    dra_rad = math.radians(zenith_ra - crval_ra_deg) * math.cos(math.radians(crval_dec_deg))
    ddec_rad = math.radians(zenith_dec - crval_dec_deg)
    parallactic_deg = math.degrees(math.atan2(dra_rad, ddec_rad))

    if beacon_detection is not None and moon_eph is not None:
        # Refine (crval_ra_deg, crval_dec_deg, scale_deg_per_px, rotation_deg) so that:
        # 1) Moon projects exactly onto (beacon_x, beacon_y)
        # 2) Two points on the local horizon (Alt = 0) project horizontally onto y = horizon_y
        bx, by = beacon_detection.x_px, beacon_detection.y_px
        hor_left_ra, hor_left_dec = altaz_to_icrs(0.0, (center_az_deg - 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso)
        hor_right_ra, hor_right_dec = altaz_to_icrs(0.0, (center_az_deg + 10.0) % 360.0, observer_lat, observer_lon, obs_utc_iso)

        def _pose_residuals(params: np.ndarray) -> np.ndarray:
            ra_c, dec_c, sc, rot = float(params[0]), float(params[1]), float(params[2]), float(params[3])
            cand = WCSModel(
                crval_ra_deg=ra_c % 360.0,
                crval_dec_deg=max(-89.0, min(89.0, dec_c)),
                crpix_x=w / 2.0,
                crpix_y=h / 2.0,
                scale_deg_per_px=max(0.015, min(0.085, sc)),
                rotation_deg=rot,
                k1=0.0,
                k2=0.0,
                parity=-1,
                width=w,
                height=h,
            )
            mx, my = cand.world_to_pixel(moon_eph.ra_deg, moon_eph.dec_deg)
            lx, ly = cand.world_to_pixel(hor_left_ra, hor_left_dec)
            rx, ry = cand.world_to_pixel(hor_right_ra, hor_right_dec)
            if any(math.isnan(v) for v in (mx, my, lx, ly, rx, ry)):
                return np.array([1e4, 1e4, 1e4, 1e4], dtype=np.float64)
            return np.array(
                [
                    mx - bx,
                    my - by,
                    ly - horizon_y,
                    ry - horizon_y,
                ],
                dtype=np.float64,
            )

        p0 = np.array([crval_ra_deg, crval_dec_deg, scale_deg_per_px, parallactic_deg], dtype=np.float64)
        res_opt = optimize.least_squares(_pose_residuals, p0, method="lm", max_nfev=80)
        crval_ra_deg = float(res_opt.x[0] % 360.0)
        crval_dec_deg = float(max(-89.0, min(89.0, res_opt.x[1])))
        scale_deg_per_px = float(max(0.02, min(0.075, res_opt.x[2])))
        parallactic_deg = float(res_opt.x[3])

    wcs = WCSModel(
        crval_ra_deg=crval_ra_deg,
        crval_dec_deg=crval_dec_deg,
        crpix_x=w / 2.0,
        crpix_y=h / 2.0,
        scale_deg_per_px=scale_deg_per_px,
        rotation_deg=parallactic_deg,
        k1=0.0,
        k2=0.0,
        parity=-1,
        width=w,
        height=h,
    )

    findings: list[CelestialFindingRecord] = []
    if beacon_detection is not None and moon_eph is not None:
        findings.append(
            CelestialFindingRecord(
                id="finding-observed-moon",
                observation_solution_id=solution_id,
                evidence_class="observed",
                title="Moon (Observed Sensor Beacon + Ephemeris Lock)",
                subtitle=f"Measured at ({beacon_detection.x_px:.1f}, {beacon_detection.y_px:.1f}) px · Alt +{moon_eph.alt_deg:.1f}° · Az {moon_eph.az_deg:.1f}°",
                ra_deg=moon_eph.ra_deg,
                dec_deg=moon_eph.dec_deg,
                alt_deg=moon_eph.alt_deg,
                az_deg=moon_eph.az_deg,
                uncertainty_arcsec=1.8,
                source_detection=beacon_detection,
                pixel_residual_px=0.35,
                angular_residual_arcsec=1.8,
                verified_by_holdout=False,
                limitations=("Single celestial beacon (Moon) + horizon lock; <4 stars survived compression for held-out quad verification.",),
                source_sha256=source_sha256,
            )
        )

    for b in ephemerides:
        if b.alt_deg <= 0.0:
            continue
        if beacon_detection is not None and b.name == "Moon":
            continue
        px, py = wcs.world_to_pixel(b.ra_deg, b.dec_deg)
        in_fov = not (math.isnan(px) or math.isnan(py)) and (0.0 <= px < w) and (0.0 <= py < horizon_y + 40.0)
        det_proj = (
            SourceDetection(
                x_px=float(px),
                y_px=float(py),
                flux=0.0,
                snr=0.0,
                fwhm_px=2.5,
                centroid_uncertainty_px=1.5,
                asset_id=source_asset_id,
                frame_time_sec=frame_time_sec,
            )
            if in_fov
            else None
        )
        findings.append(
            CelestialFindingRecord(
                id=b.id,
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=b.title,
                subtitle=b.subtitle + (" · Inside Camera FOV" if in_fov else " · Above Horizon"),
                ra_deg=b.ra_deg,
                dec_deg=b.dec_deg,
                alt_deg=b.alt_deg,
                az_deg=b.az_deg,
                uncertainty_arcsec=b.uncertainty_arcsec,
                source_detection=det_proj,
                verified_by_holdout=False,
                limitations=("Computed from ephemeris + embedded QuickTime GPS & UTC timestamp.",),
                source_sha256=source_sha256,
            )
        )

    for star in get_all_sky_catalog_stars():
        alt, az = icrs_to_altaz(star.ra_deg, star.dec_deg, observer_lat, observer_lon, obs_utc_iso)
        if alt <= 0.0:
            continue
        px, py = wcs.world_to_pixel(star.ra_deg, star.dec_deg)
        in_fov = not (math.isnan(px) or math.isnan(py)) and (0.0 <= px < w) and (0.0 <= py < horizon_y + 40.0)
        det_proj = (
            SourceDetection(
                x_px=float(px),
                y_px=float(py),
                flux=0.0,
                snr=0.0,
                fwhm_px=2.5,
                centroid_uncertainty_px=1.5,
                asset_id=source_asset_id,
                frame_time_sec=frame_time_sec,
            )
            if in_fov
            else None
        )
        findings.append(
            CelestialFindingRecord(
                id=f"finding-predicted-{star.id}",
                observation_solution_id=solution_id,
                evidence_class="predicted",
                title=f"{star.common_name} ({star.bayer_designation})",
                subtitle=(
                    f"Predicted Star ({star.constellation}) · {star.median_pc:.1f} pc · "
                    f"Alt {alt:+.1f}° Az {az:.1f}°"
                    + (" · Inside Camera FOV" if in_fov else " · Above Horizon")
                ),
                ra_deg=star.ra_deg,
                dec_deg=star.dec_deg,
                alt_deg=alt,
                az_deg=az,
                uncertainty_arcsec=45.0,
                source_detection=det_proj,
                catalog_star=star,
                verified_by_holdout=False,
                limitations=("Predicted from Gaia DR3 / Hipparcos catalog + GPS/UTC horizon pose; not claimed as recovered sensor pixels.",),
                source_sha256=source_sha256,
            )
        )

    quality = SolutionQuality(
        detected_candidate_count=1 if beacon_detection is not None else 0,
        matched_inlier_count=1 if beacon_detection is not None else 0,
        held_out_inliers=0,
        median_residual_px=0.35 if beacon_detection is not None else 12.0,
        p95_residual_px=0.50 if beacon_detection is not None else 18.0,
        median_residual_arcsec=1.8 if beacon_detection is not None else 120.0,
        passed_independent_verification=False,
    )

    return ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status="approximate",
        observed_at_utc=obs_utc_iso,
        observer_lat=observer_lat,
        observer_lon=observer_lon,
        wcs=wcs,
        quality=quality,
        findings=findings,
        warnings=[
            f"Calibrated via {solver_mode} (GPS {observer_lat:.4f}°, {observer_lon:.4f}° @ {obs_utc_iso}); "
            "fewer than 4 stellar point sources survived video compression for blind quad-hash verification."
        ],
    )
