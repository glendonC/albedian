"""Domain data models for point-source detections, Gnomonic TAN + radial distortion WCS,
solution quality metrics, epistemic findings, and video calibration stacking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import re
from typing import Any

import numpy as np

from services.science.catalog import CatalogStar, catalog_cartesian_pc

EVIDENCE_CLASSES = ("observed", "catalog_matched", "predicted", "simulated")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def require_source_sha256(sha256: str) -> str:
    """The source asset's SHA-256 as 64 lowercase hex digits. The all-zero string is refused: it is a placeholder
    for a hash nobody computed, not a hash."""
    if not _SHA256_HEX.fullmatch(sha256):
        raise ValueError("source_sha256 must be the source asset's SHA-256 as 64 lowercase hex digits.")
    if set(sha256) == {"0"}:
        raise ValueError("source_sha256 of 64 zeros is a placeholder, not a hash: pass the source asset's SHA-256.")
    return sha256


# What each class of finding rests on, as its evidence record names it
VERIFICATION_METHODS = {
    "observed": "Measured in the image's pixels",
    "catalog_matched": "Triangle-matched WCS with held-out residual verification",
    "predicted": "Predicted for the given time and place; not measured in the image",
    "simulated": "Simulated; not measured in the image",
}


@dataclass(frozen=True)
class SourceDetection:
    """Point source measured directly from original sensor pixels (`observed` class)."""

    x_px: float
    y_px: float
    flux: float
    snr: float
    fwhm_px: float
    centroid_uncertainty_px: float
    # The recording it was measured in, when the caller named it: no id is made up for it
    asset_id: str | None = None
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

    def to_contract_dict(self, *, asset_id: str | None = None) -> dict:
        """The contract's SourceDetection under the recording it was measured in: its own `asset_id`, or else the
        one given, its solution's. With neither, it is refused rather than given an id."""
        recording = self.asset_id if self.asset_id is not None else asset_id
        if recording is None:
            raise ValueError(
                f"The detection at ({self.x_px:.1f}, {self.y_px:.1f}) px names no recording and none was given: "
                "it is serialized under the id of the recording it was measured in."
            )
        return {
            "assetId": recording,
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
    width: int = field(kw_only=True)
    height: int = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"WCSModel width and height must be positive integers (got {self.width} x {self.height}).")
        if abs(self.k1) > 0.25 or abs(self.k2) > 0.25:
            raise ValueError(
                f"WCSModel radial distortion coefficients k1 and k2 must lie in [-0.25, 0.25] (got k1={self.k1}, k2={self.k2})."
            )

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
            "radialDistortionK1": float(self.k1),
            "radialDistortionK2": float(self.k2),
            "parity": -1 if self.parity < 0 else 1,
            "fovHorizontalDeg": float(self.fov_horizontal_deg),
            "fovVerticalDeg": float(self.fov_vertical_deg),
        }


@dataclass(frozen=True)
class SolutionQuality:
    """Astrometric verification metrics including independent held-out residual evaluation.

    `held_out_pair_count` counts the held-out detections paired with a catalog star within the association radius;
    `held_out_inliers` are those of them inside the per-star inlier gate, and the residuals cover all of them. The
    residuals are None exactly when no held-out star paired: the contract then leaves them out rather than carry
    a stand-in number. The contract's rules on these fields are checked here too, so no producer can break them.
    """

    detected_candidate_count: int
    matched_inlier_count: int
    held_out_inliers: int
    median_residual_px: float | None
    p95_residual_px: float | None
    median_residual_arcsec: float | None
    passed_independent_verification: bool
    held_out_pair_count: int = field(kw_only=True)

    def __post_init__(self) -> None:
        residuals = (self.median_residual_px, self.p95_residual_px, self.median_residual_arcsec)
        if any((value is not None) != (self.held_out_pair_count > 0) for value in residuals):
            raise ValueError("Held-out residuals are reported exactly when a held-out star was paired.")
        if self.held_out_inliers > self.held_out_pair_count:
            raise ValueError("Held-out inliers are held-out pairs inside the gate: there cannot be more of them.")
        if self.passed_independent_verification and self.median_residual_px is None:
            raise ValueError("Held-out verification passes only with the held-out residuals it was judged on.")

    def to_contract_dict(self) -> dict:
        payload: dict = {
            "detectedCandidateCount": int(self.detected_candidate_count),
            "matchedInlierCount": int(self.matched_inlier_count),
            "heldOutPairCount": int(self.held_out_pair_count),
            "heldOutInliers": int(self.held_out_inliers),
            "passedIndependentVerification": bool(self.passed_independent_verification),
        }
        for key, value in (
            ("medianResidualPx", self.median_residual_px),
            ("p95ResidualPx", self.p95_residual_px),
            ("medianResidualArcsec", self.median_residual_arcsec),
        ):
            if value is not None:
                payload[key] = float(value)
        return payload


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
    # A deep-sky object's contract CatalogReference (services/science/deep_sky_findings.py), in place of a
    # catalog star; left out of the hash, since a dict cannot be hashed
    deep_sky_reference: Mapping[str, Any] | None = field(default=None, hash=False)
    # How this finding was reached, when the evidence class's general wording does not say it
    verification_method: str | None = None
    pixel_residual_px: float | None = None
    angular_residual_arcsec: float | None = None
    verified_by_holdout: bool = False
    limitations: tuple[str, ...] = field(kw_only=True)
    # The SHA-256 of the recording this finding belongs to: required, since no record can know it by default
    source_sha256: str = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f"Finding {self.id} has unknown evidence class {self.evidence_class!r}.")
        if self.catalog_star is not None and self.deep_sky_reference is not None:
            raise ValueError(f"Finding {self.id} stands on one catalog row: a star or a deep-sky object, not both.")
        if len(self.limitations) == 0 or any(not isinstance(lim, str) or not lim.strip() for lim in self.limitations):
            raise ValueError(f"Finding {self.id} requires at least one non-empty limitation string in limitations.")
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
        if self.catalog_star is not None:
            return self.catalog_star.common_name
        return str(self.deep_sky_reference["commonName"]) if self.deep_sky_reference is not None else None

    def to_contract_dict(self, *, source_asset_id: str | None = None) -> dict:
        """The contract's CelestialFinding. `source_asset_id` is the recording of the solution it belongs to: a
        detection that names no recording of its own was measured there and is serialized under it."""
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
            "verificationMethod": self.verification_method or VERIFICATION_METHODS[self.evidence_class],
            "sourceAssetSha256": self.source_sha256,
            "wcsSolutionId": self.observation_solution_id,
            "catalogReferenceId": (
                self.catalog_star.id
                if self.catalog_star
                else (str(self.deep_sky_reference["id"]) if self.deep_sky_reference is not None else None)
            ),
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
                f"Distance interval [{self.catalog_star.lower_pc:.1f}, {self.catalog_star.upper_pc:.1f}] pc: "
                f"{self.catalog_star.distance_source}"
                if self.catalog_star
                else None
            ),
            "limitations": list(self.limitations),
            "verifiedByIndependentHoldout": bool(self.verified_by_holdout),
        }
        # The contract's optional evidence fields are left out when unknown, never sent as null
        payload["evidence"] = {key: value for key, value in evidence.items() if value is not None}
        if self.source_detection is not None:
            payload["sourceDetection"] = self.source_detection.to_contract_dict(asset_id=source_asset_id)
        if self.catalog_star is not None:
            payload["catalogRef"] = self.catalog_star.to_catalog_reference_dict()
            payload["inferredDistancePc"] = {
                "lower": float(self.catalog_star.lower_pc),
                "median": float(self.catalog_star.median_pc),
                "upper": float(self.catalog_star.upper_pc),
                "method": self.catalog_star.distance_method,
            }
            cx, cy, cz = catalog_cartesian_pc(self.catalog_star)
            payload["cartesianParsecICRS"] = [float(cx), float(cy), float(cz)]
        elif self.deep_sky_reference is not None:
            payload["catalogRef"] = dict(self.deep_sky_reference)
        return payload


@dataclass(frozen=True)
class SolverIdentity:
    """Which solver produced a solution, and against which catalog or model."""

    name: str
    version: str
    catalog_release: str

    def to_contract_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "catalogRelease": self.catalog_release}


@dataclass(frozen=True)
class ObservationSolution:
    """Authoritative WCS calibration and epistemic finding record."""

    id: str
    source_asset_id: str
    source_sha256: str
    frame_time_sec: float
    revision: int
    status: str
    wcs: WCSModel | None
    # What the solver measured and who solved it, as in the contract: neither on a pending solution (none has
    # run), quality required on a calibrated or approximate one, and a solver on every outcome but an
    # unresolved attempt that failed before any solver ran
    quality: SolutionQuality | None
    solver: SolverIdentity | None
    observed_at_utc: str | None = None
    observer_lat: float | None = None
    observer_lon: float | None = None
    findings: list[CelestialFindingRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status == "pending":
            if self.quality is not None:
                raise ValueError("A pending solution has no quality: no solver has run, so nothing was counted.")
            if self.solver is not None:
                raise ValueError("A pending solution names no solver: none has run.")
        else:
            if self.solver is None and self.status != "unresolved":
                raise ValueError("Only a pending solution, or an unresolved one no solver reached, names no solver.")
            if self.quality is None and self.status in ("calibrated", "approximate"):
                raise ValueError("A calibrated or approximate solution reports the quality it was judged on.")
        if (self.observer_lat is None) != (self.observer_lon is None):
            raise ValueError("observer_lat and observer_lon must both be provided or both be None.")
        has_place = self.observer_lat is not None and self.observer_lon is not None
        needs_time_and_place = (
            self.status in ("approximate", "predicted_only")
            or any(f.evidence_class == "predicted" for f in self.findings)
        )
        if needs_time_and_place and (self.observed_at_utc is None or not has_place):
            raise ValueError(
                "An approximate or predicted_only solution, or any solution with a predicted finding, "
                "needs observed_at_utc, observer_lat and observer_lon."
            )
        if self.quality is not None and self.quality.passed_independent_verification and self.wcs is None:
            raise ValueError("Held-out verification passes only with a WCS.")
        # Pixels reach the sky and the catalog only through a verified WCS: an observed finding has a
        # sky position exactly when there is one, and a catalog match needs one
        verified = self.has_verified_wcs
        for finding in self.findings:
            # A finding's detection was measured in this solution's recording: one that names another is refused
            measured_in = finding.source_detection.asset_id if finding.source_detection is not None else None
            if measured_in is not None and measured_in != self.source_asset_id:
                raise ValueError(
                    f"Finding {finding.id}'s detection was measured in {measured_in}, "
                    f"not in this solution's recording {self.source_asset_id}."
                )
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
        return self.wcs is not None and self.quality is not None and self.quality.passed_independent_verification

    @property
    def held_out_inliers(self) -> int | None:
        return None if self.quality is None else self.quality.held_out_inliers

    @property
    def median_residual_px(self) -> float | None:
        return None if self.quality is None else self.quality.median_residual_px

    @property
    def p95_residual_px(self) -> float | None:
        return None if self.quality is None else self.quality.p95_residual_px

    @property
    def matched_inlier_count(self) -> int | None:
        return None if self.quality is None else self.quality.matched_inlier_count

    def to_contract_dict(self) -> dict:
        result: dict = {
            "id": self.id,
            "sourceAssetId": self.source_asset_id,
            "sourceSha256": self.source_sha256,
            "frameTimeSec": float(self.frame_time_sec),
            "revision": int(self.revision),
            "status": self.status,
        }
        if self.observed_at_utc is not None:
            result["observedAtUTC"] = self.observed_at_utc
        if self.observer_lat is not None and self.observer_lon is not None:
            result["observerLocation"] = {
                "latDeg": float(self.observer_lat),
                "lonDeg": float(self.observer_lon),
            }
        if self.solver is not None:
            result["solver"] = self.solver.to_contract_dict()
        if self.quality is not None:
            result["quality"] = self.quality.to_contract_dict()
        result.update(
            {
                "findings": [f.to_contract_dict(source_asset_id=self.source_asset_id) for f in self.findings],
                "warnings": list(self.warnings),
            }
        )
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
