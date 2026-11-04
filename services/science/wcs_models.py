"""Domain data models for point-source detections, Gnomonic TAN + radial distortion WCS,
solution quality metrics, epistemic findings, and video calibration stacking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from services.science.catalog import CatalogStar, bailer_jones_cartesian_pc

EVIDENCE_CLASSES = ("observed", "catalog_matched", "predicted", "simulated")


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
    """Finding record carrying explicit `EvidenceClass` (`observed`, `catalog_matched`, `predicted`, `simulated`).

    `catalog_matched`, `predicted` and `simulated` findings always carry a sky position. An `observed`
    finding is a pixel measurement: it carries one only when a verified WCS maps its pixels to the sky
    (checked by `ObservationSolution`), and without one it carries the detection that locates it.
    """

    id: str
    observation_solution_id: str
    evidence_class: str
    title: str
    subtitle: str
    ra_deg: float | None = None
    dec_deg: float | None = None
    alt_deg: float | None = None
    az_deg: float | None = None
    uncertainty_arcsec: float | None = None
    source_detection: SourceDetection | None = None
    catalog_star: CatalogStar | None = None
    pixel_residual_px: float | None = None
    angular_residual_arcsec: float | None = None
    verified_by_holdout: bool = False
    limitations: tuple[str, ...] = ("Measured from sensor observation.",)
    source_sha256: str = "0" * 64

    def __post_init__(self) -> None:
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f"Finding {self.id} has unknown evidence class {self.evidence_class!r}.")
        sky = (self.ra_deg, self.dec_deg, self.uncertainty_arcsec)
        if None in sky and any(v is not None for v in (*sky, self.alt_deg, self.az_deg)):
            raise ValueError(
                f"Finding {self.id} has a partial sky position: RA, Dec and uncertainty come together, "
                "and Alt/Az need them."
            )
        if not self.has_sky_position and self.evidence_class != "observed":
            raise ValueError(f"A {self.evidence_class} finding needs a sky position ({self.id}).")
        if not self.has_sky_position and self.source_detection is None:
            raise ValueError(
                f"An observed finding without a sky position needs the detection that locates it in pixels ({self.id})."
            )

    @property
    def has_sky_position(self) -> bool:
        return self.ra_deg is not None

    @property
    def common_name(self) -> str | None:
        return self.catalog_star.common_name if self.catalog_star else None

    def to_contract_dict(self) -> dict:
        payload: dict = {
            "id": self.id,
            "observationSolutionId": self.observation_solution_id,
            "evidenceClass": self.evidence_class,
            "title": self.title,
            "subtitle": self.subtitle,
        }
        if self.has_sky_position:
            sky_pos: dict = {
                "raDeg": float(self.ra_deg % 360.0),
                "decDeg": float(self.dec_deg),
                "uncertaintyArcsec": float(self.uncertainty_arcsec),
            }
            if self.alt_deg is not None:
                sky_pos["altDeg"] = float(self.alt_deg)
            if self.az_deg is not None:
                sky_pos["azDeg"] = float(self.az_deg)
            payload["skyPosition"] = sky_pos

        evidence: dict = {
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
        }
        # The contract's optional evidence fields are left out when unknown, never sent as null
        payload["evidence"] = {key: value for key, value in evidence.items() if value is not None}
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

    def __post_init__(self) -> None:
        # Pixels reach the sky and the catalog only through a verified WCS: an observed finding has a
        # sky position exactly when there is one, and a catalog match needs one
        verified = self.has_verified_wcs
        for finding in self.findings:
            if finding.evidence_class == "catalog_matched" and not verified:
                raise ValueError(
                    f"Catalog-matched finding {finding.id} needs a verified WCS: "
                    "catalog association follows held-out verification."
                )
            if finding.evidence_class == "observed" and finding.has_sky_position != verified:
                raise ValueError(
                    f"Observed finding {finding.id} "
                    + (
                        "lacks the sky position the verified WCS gives it."
                        if verified
                        else "has a sky position without a verified WCS."
                    )
                )

    @property
    def has_verified_wcs(self) -> bool:
        """Whether the WCS passed independent held-out verification, the only way this frame's pixels
        are placed on the sky."""
        return self.wcs is not None and self.quality.passed_independent_verification

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
