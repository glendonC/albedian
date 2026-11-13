"""Domain data models for point-source detections, Gnomonic TAN + radial distortion WCS,
solution quality metrics, epistemic findings, and video calibration stacking.

Each record refuses, when it is built, a value the observation solution contract refuses (a number that is not
finite or lies outside its bounds, an unknown status, a hash that is not one): the rule it checks with is the very
object the contract's parser checks the field with (services/contracts/astronomy.py). `ObservationSolution`'s
`to_contract_dict` then passes its output through that parser, so no writer stores or serves a solution the
TypeScript schema would refuse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import numbers
import re
from typing import Any

import numpy as np

from services.contracts import astronomy as contract
from services.contracts.astronomy import (
    EVIDENCE_CLASSES,
    MAX_SWEPT_WCS,
    MIN_SWEPT_WCS,
    POSE_BEACONS,
    POSE_METHOD_BEACONS,
    POSE_METHODS,
)
from services.contracts.ingest import SHA256_HEX_PATTERN
from services.science.catalog import CatalogStar, catalog_cartesian_pc

_SHA256_HEX = re.compile(SHA256_HEX_PATTERN)


class ContractRefusal(ValueError):
    """A record of this module refusing what the observation solution contract refuses, when it is built or
    written. A `ValueError`, so every caller that caught one still does; its own type, so a caller can tell a value
    the contract refuses from any other fault. A refusal of one value (`require_contract_value`,
    `require_source_sha256`) names the field and the rule and never holds the value, so it can be shown to whoever
    sent the value."""


def require_source_sha256(sha256: str) -> str:
    """The source asset's SHA-256 as 64 lowercase hex digits. The all-zero string is refused: it is a placeholder
    for a hash nobody computed, not a hash."""
    if not isinstance(sha256, str) or not _SHA256_HEX.fullmatch(sha256):
        raise ContractRefusal("source_sha256 must be the source asset's SHA-256 as 64 lowercase hex digits.")
    if set(sha256) == {"0"}:
        raise ContractRefusal("source_sha256 of 64 zeros is a placeholder, not a hash: pass the source asset's SHA-256.")
    return sha256


def contract_utc_instant(moment: str) -> str:
    """`moment` in the contract's form for a capture time: a UTC instant ending in Z. A time already in that form
    is kept as written. One written with an offset (+00:00, as Python's isoformat writes UTC, or a clip's own
    offset) names the same instant and is rewritten in UTC, to the microsecond. A time with no zone names no
    instant, and anything else is not a time: both are returned as they are, for the contract's parser to refuse."""
    if contract.UTC_INSTANT.problem(moment) is None:
        return moment
    try:
        parsed = datetime.fromisoformat(moment)
        if parsed.tzinfo is None:
            return moment
        utc = parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return moment
    fraction = f".{utc.microsecond:06d}".rstrip("0") if utc.microsecond else ""
    return f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}{fraction}Z"


def _contract_rules(schema: str, **wire_names: str) -> tuple[tuple[str, Any], ...]:
    """(attribute, rule) for each attribute named, the rule being the one the contract's parser checks the field
    `wire_names[attribute]` of `schema` with."""
    return tuple((attribute, contract.field_rule(schema, wire_name)) for attribute, wire_name in wire_names.items())


def require_contract_value(value: object, rule: Any, named: str) -> None:
    """Refuse `value` when it breaks `rule`, one of the contract's own field rules (`contract.field_rule`), in a
    sentence that calls it `named` and states the rule, never the value, so no place or capture time reaches a log
    or an answer. The records below check each of their fields through this, and a caller that takes a value from
    outside can hold it to the rule it will meet here before any work is done on it."""
    # A solver computes with NumPy, whose numbers a JSON document never holds: they are judged by their value
    problem = rule.problem(value, any_real=True) if isinstance(rule, contract.NumberRule) else rule.problem(value)
    if problem is not None:
        raise ContractRefusal(f"{named} is outside the contract: {problem[0].lower()}{problem[1:]}")


def _require_contract(record: object, what: str, rules: tuple[tuple[str, Any], ...], *, optional: bool = False) -> None:
    """Refuse `record` when one of its attributes breaks its contract rule; with `optional`, None is left out."""
    for attribute, rule in rules:
        value = getattr(record, attribute)
        if optional and value is None:
            continue
        require_contract_value(value, rule, f"{what}'s {attribute}")


# The share of a detection's light in its brightest pixel is not in the contract; it is held to a finite number,
# which every comparison made with it needs
_DETECTION_NUMBERS = (
    *_contract_rules(
        "SourceDetection", x_px="xPx", y_px="yPx", flux="flux", snr="snr", fwhm_px="fwhmPx",
        centroid_uncertainty_px="centroidUncertaintyPx", frame_time_sec="frameTimeSec",
    ),
    ("peak_to_total_ratio", contract.NUMBER),
)
_DETECTION_RECORDING = _contract_rules("SourceDetection", asset_id="assetId")
# crval_ra_deg, crval_dec_deg and the two distortion terms are held to "a finite number" here. The right ascension
# is written modulo 360. A fit works on these records and may carry its centre past a pole, which is the same
# pointing at RA + 180 turned half a turn, and bring it back before it writes: the declination's range is the
# contract's to judge where a solution is written (ObservationSolution.to_contract_dict). The distortion range keeps
# its own message below
_WCS_NUMBERS = (
    ("crval_ra_deg", contract.NUMBER),
    ("crval_dec_deg", contract.NUMBER),
    *_contract_rules(
        "WCSCalibrationModel", crpix_x="crpixX", crpix_y="crpixY", scale_deg_per_px="scaleDegPerPx",
        rotation_deg="rotationDeg",
    ),
    ("k1", contract.NUMBER),
    ("k2", contract.NUMBER),
)
_QUALITY_COUNTS = _contract_rules(
    "SolutionQuality", detected_candidate_count="detectedCandidateCount", matched_inlier_count="matchedInlierCount",
    held_out_pair_count="heldOutPairCount", held_out_inliers="heldOutInliers",
)
_QUALITY_RESIDUALS = _contract_rules(
    "SolutionQuality", median_residual_px="medianResidualPx", p95_residual_px="p95ResidualPx",
    median_residual_arcsec="medianResidualArcsec",
)
_FINDING_TEXT = _contract_rules(
    "CelestialFinding", id="id", observation_solution_id="observationSolutionId", title="title", subtitle="subtitle"
)
_FINDING_SKY = (
    ("ra_deg", contract.NUMBER),
    *_contract_rules("SkyPosition", dec_deg="decDeg", uncertainty_arcsec="uncertaintyArcsec"),
)
_FINDING_OPTIONAL_NUMBERS = (
    *_contract_rules("SkyPosition", alt_deg="altDeg", az_deg="azDeg"),
    *_contract_rules("ScientificEvidence", pixel_residual_px="pixelResidualPx", angular_residual_arcsec="angularResidualArcsec"),
)
_SOLVER_TEXT = _contract_rules("SolverIdentity", name="name", version="version", catalog_release="catalogRelease")
_SOLUTION_REQUIRED = _contract_rules(
    "ObservationSolution", id="id", source_asset_id="sourceAssetId", frame_time_sec="frameTimeSec", revision="revision",
    status="status",
)
_SOLUTION_PLACE = _contract_rules("ObserverLocation", observer_lat="latDeg", observer_lon="lonDeg")


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

    def __post_init__(self) -> None:
        # The contract's bounds on a detection: a position and a width that are not negative, a flux, an SNR and a
        # centroid uncertainty above zero, all finite
        _require_contract(self, "A source detection", _DETECTION_NUMBERS)
        _require_contract(self, "A source detection", _DETECTION_RECORDING, optional=True)

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
            raise ContractRefusal(
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
            raise ContractRefusal(f"WCSModel width and height must be positive integers (got {self.width} x {self.height}).")
        # Finite numbers and a scale above zero, as the contract's WCS: a NaN would pass every comparison below and
        # leave as a number JSON cannot carry
        _require_contract(self, "A WCS", _WCS_NUMBERS)
        if abs(self.k1) > 0.25 or abs(self.k2) > 0.25:
            raise ContractRefusal(
                f"WCSModel radial distortion coefficients k1 and k2 must lie in [-0.25, 0.25] (got k1={self.k1}, k2={self.k2})."
            )
        if isinstance(self.parity, bool) or self.parity not in contract.PARITY.values:
            raise ContractRefusal("A WCS's parity is 1 or -1.")

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
    a stand-in number. The contract's rules on these fields are checked here too, so no producer can break them:
    `passed_independent_verification` is true only at or above the held-out gate it stands for, the plate solver's
    own fewest held-out inliers and largest median held-out residual (`contract.held_out_gate`).
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
        # Whole counts that are not negative, and residuals that are finite and not negative
        _require_contract(self, "A solution's quality", _QUALITY_COUNTS)
        _require_contract(self, "A solution's quality", _QUALITY_RESIDUALS, optional=True)
        residuals = (self.median_residual_px, self.p95_residual_px, self.median_residual_arcsec)
        if any((value is not None) != (self.held_out_pair_count > 0) for value in residuals):
            raise ContractRefusal("Held-out residuals are reported exactly when a held-out star was paired.")
        if self.held_out_inliers > self.held_out_pair_count:
            raise ContractRefusal("Held-out inliers are held-out pairs inside the gate: there cannot be more of them.")
        if self.passed_independent_verification and self.median_residual_px is None:
            raise ContractRefusal("Held-out verification passes only with the held-out residuals it was judged on.")
        if self.passed_independent_verification:
            # The flag stands for the held-out gate: it is never true below the gate's own numbers
            least, most = contract.held_out_gate()
            if self.held_out_inliers < least or self.median_residual_px > most:
                raise ContractRefusal(contract.passes_at_the_gate_only(least, most))

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
    (checked by `ObservationSolution`), and without one it carries the detection that locates it. A
    `predicted` or `simulated` finding was not measured in the pixels, so it carries no detection.
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
            raise ContractRefusal(f"Finding {self.id} has unknown evidence class {self.evidence_class!r}.")
        if self.catalog_star is not None and self.deep_sky_reference is not None:
            raise ContractRefusal(f"Finding {self.id} stands on one catalog row: a star or a deep-sky object, not both.")
        if len(self.limitations) == 0 or any(not isinstance(lim, str) or not lim.strip() for lim in self.limitations):
            raise ContractRefusal(f"Finding {self.id} requires at least one non-empty limitation string in limitations.")
        sky = (self.ra_deg, self.dec_deg, self.uncertainty_arcsec)
        if None in sky and any(v is not None for v in (*sky, self.alt_deg, self.az_deg)):
            raise ContractRefusal(
                f"Finding {self.id} has a partial sky position: RA, Dec and uncertainty come together, "
                "and Alt/Az need them."
            )
        if not self.has_sky_position and self.evidence_class != "observed":
            raise ContractRefusal(f"A {self.evidence_class} finding needs a sky position ({self.id}).")
        if self.evidence_class in ("predicted", "simulated") and self.source_detection is not None:
            raise ContractRefusal(
                f"A {self.evidence_class} finding was not measured in the pixels, so it carries no source detection "
                f"({self.id})."
            )
        # What the contract's class table (contract.FINDING_CLASS_EVIDENCE) asks of an observed finding and of a catalog
        # match among the fields this record holds: the detection each was measured from, the catalog row a match
        # stands on, and none on an observed finding. The rest the writer gives by construction (the evidence's class
        # is the record's own, its catalog reference id the row's)
        catalog_row = self.catalog_star is not None or self.deep_sky_reference is not None
        if self.evidence_class in ("observed", "catalog_matched") and self.source_detection is None:
            table = contract.FINDING_CLASS_EVIDENCE[self.evidence_class]["must"]
            raise ContractRefusal(f"{table['sourceDetection'][:-1]} ({self.id}).")
        if self.evidence_class == "catalog_matched" and not catalog_row:
            raise ContractRefusal(f"{contract.FINDING_CLASS_EVIDENCE['catalog_matched']['must']['catalogRef'][:-1]} ({self.id}).")
        if self.evidence_class == "observed" and catalog_row:
            raise ContractRefusal(f"{contract.FINDING_CLASS_EVIDENCE['observed']['mustNot']['catalogRef'][:-1]} ({self.id}).")
        # The lead's rulings of 2026-11-12 in the same table: a match holds both residuals, a prediction or a simulation
        # neither (an observed finding's stay open: the horizon solver writes its Moon with the pose fit residual), and
        # only a match is verified by held-out stars
        residuals = (self.pixel_residual_px, self.angular_residual_arcsec)
        if self.evidence_class == "catalog_matched" and None in residuals:
            raise ContractRefusal(f"{contract.FINDING_CLASS_EVIDENCE['catalog_matched']['must']['evidence.pixelResidualPx'][:-1]} ({self.id}).")
        if self.evidence_class in ("predicted", "simulated") and residuals != (None, None):
            raise ContractRefusal(f"{contract.FINDING_CLASS_EVIDENCE['predicted']['mustNot']['evidence.pixelResidualPx'][:-1]} ({self.id}).")
        if self.verified_by_holdout and self.evidence_class != "catalog_matched":
            raise ContractRefusal(f"{contract.FINDING_CLASS_EVIDENCE['observed']['mustNot']['evidence.verifiedByIndependentHoldout'][:-1]} ({self.id}).")
        # What the contract holds each of these to: names that are not empty, a sky position on the sphere, residuals
        # that are finite and not negative, and a real hash for the recording
        _require_contract(self, "A finding", _FINDING_TEXT)
        if self.has_sky_position:
            _require_contract(self, "A finding", _FINDING_SKY)
        _require_contract(self, "A finding", _FINDING_OPTIONAL_NUMBERS, optional=True)
        require_source_sha256(self.source_sha256)

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

    def __post_init__(self) -> None:
        _require_contract(self, "A solver identity", _SOLVER_TEXT)

    def to_contract_dict(self) -> dict:
        return {"name": self.name, "version": self.version, "catalogRelease": self.catalog_release}


# How an approximate pose was lined up, its beacon, and how many WCS it sweeps (POSE_METHODS, POSE_BEACONS,
# POSE_METHOD_BEACONS, MIN_SWEPT_WCS and MAX_SWEPT_WCS) are the contract mirror's own, imported above: the bounds of
# PoseEstimateSchema (packages/contracts/src/astronomy.ts; .kiro/specs/predicted-sky-for-every-upload/design.md
# section 7)


def _is_number(value: object) -> bool:
    """A number as the contract's z.number() takes one (a NumPy float too): no boolean, and no NaN, which the bounds
    checks refuse."""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


@dataclass(frozen=True)
class PoseEstimate:
    """An approximate pointing and its uncertainty, as the contract's PoseEstimate: lined up from a beacon (the Moon
    or the Sun) and stated assumptions, never checked against stars. It gives the frame centre's altitude and
    azimuth, the roll and the diagonal field of view of the solution's nominal WCS, where that field of view comes
    from, and the WCS swept over the assumptions' ranges. The method names its beacon, and the two uncertainties are
    finite, so the contract dict is plain JSON. `ObservationSolution` lets it ride only on an approximate solution
    with a WCS, every swept WCS on that WCS's crpix and of its width and height."""

    method: str
    beacon: str
    center_alt_deg: float
    center_az_deg: float
    roll_deg: float
    fov_diagonal_deg: float
    fov_source: str
    assumptions: tuple[str, ...]
    swept_wcs: tuple[WCSModel, ...]
    max_center_offset_deg: float
    beacon_centroid_uncertainty_deg: float

    def __post_init__(self) -> None:
        if self.method not in POSE_METHODS:
            raise ContractRefusal(f"A pose estimate's method is one of {', '.join(POSE_METHODS)} (got {self.method!r}).")
        if self.beacon not in POSE_BEACONS:
            raise ContractRefusal(f"A pose estimate's beacon is the moon or the sun (got {self.beacon!r}).")
        if POSE_METHOD_BEACONS[self.method] != self.beacon:
            raise ContractRefusal(
                "A pose estimate's beacon is the one its method lines up from: the moon for "
                "moon_camera_profile_level_head and moon_horizon, the sun for sun_horizon."
            )
        for name, value, lo, hi in (
            ("center_alt_deg", self.center_alt_deg, -90.0, 90.0),
            ("center_az_deg", self.center_az_deg, 0.0, 360.0),
            ("roll_deg", self.roll_deg, -180.0, 180.0),
        ):
            if not _is_number(value) or not lo <= value <= hi:
                raise ContractRefusal(f"A pose estimate's {name} lies in [{lo:g}, {hi:g}] (got {value!r}).")
        if not _is_number(self.fov_diagonal_deg) or not 0.0 < self.fov_diagonal_deg <= 175.0:
            raise ContractRefusal(
                f"A pose estimate's fov_diagonal_deg is positive and at most 175 (got {self.fov_diagonal_deg!r})."
            )
        for name, value in (
            ("max_center_offset_deg", self.max_center_offset_deg),
            ("beacon_centroid_uncertainty_deg", self.beacon_centroid_uncertainty_deg),
        ):
            # Not negative and finite, as the contract's .nonnegative().finite(): NaN fails both comparisons, and
            # json.dumps would write an infinity as Infinity, which is not JSON
            if not _is_number(value) or not 0.0 <= value < math.inf:
                raise ContractRefusal(f"A pose estimate's {name} is finite and not negative (got {value!r}).")
        if not isinstance(self.fov_source, str) or len(self.fov_source) == 0:
            raise ContractRefusal("A pose estimate names where its field of view comes from.")
        # A list or tuple, as z.array takes: a string, a mapping (whose keys tuple() would take) or a set is refused
        if not isinstance(self.assumptions, (list, tuple)):
            raise ContractRefusal(
                f"A pose estimate's assumptions are a list or tuple of sentences (got {type(self.assumptions).__name__})."
            )
        assumptions = tuple(self.assumptions)
        if len(assumptions) == 0 or any(not isinstance(a, str) or len(a) == 0 for a in assumptions):
            raise ContractRefusal("A pose estimate states at least one assumption, each in words.")
        # A list or tuple, as the assumptions are: None or a number is refused in words, not by tuple() failing
        if not isinstance(self.swept_wcs, (list, tuple)):
            raise ContractRefusal(
                f"A pose estimate's swept WCS are a list or tuple of WCSModel (got {type(self.swept_wcs).__name__})."
            )
        swept = tuple(self.swept_wcs)
        if not MIN_SWEPT_WCS <= len(swept) <= MAX_SWEPT_WCS:
            raise ContractRefusal(
                f"A pose estimate sweeps {MIN_SWEPT_WCS} to {MAX_SWEPT_WCS} WCS (got {len(swept)})."
            )
        if any(not isinstance(wcs, WCSModel) for wcs in swept):
            raise ContractRefusal("Each swept WCS of a pose estimate is a WCSModel.")
        # Kept as tuples, so the frozen record cannot change after it was checked
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "swept_wcs", swept)

    def to_contract_dict(self) -> dict:
        return {
            "method": self.method,
            "beacon": self.beacon,
            "centerAltDeg": float(self.center_alt_deg),
            "centerAzDeg": float(self.center_az_deg),
            "rollDeg": float(self.roll_deg),
            "fovDiagonalDeg": float(self.fov_diagonal_deg),
            "fovSource": self.fov_source,
            "assumptions": list(self.assumptions),
            "sweptWcs": [wcs.to_contract_dict() for wcs in self.swept_wcs],
            "maxCenterOffsetDeg": float(self.max_center_offset_deg),
            "beaconCentroidUncertaintyDeg": float(self.beacon_centroid_uncertainty_deg),
        }


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
    # How far an approximate solution's WCS may be off: present only on an approximate solution with a WCS
    pose_estimate: PoseEstimate | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        # What the contract holds each of these to on its own: ids that are not empty, a real hash for the recording,
        # a frame time that is finite and not negative, a whole revision above zero, a status it names, a place on
        # the globe, and warnings in words
        _require_contract(self, "A solution", _SOLUTION_REQUIRED)
        require_source_sha256(self.source_sha256)
        _require_contract(self, "A solution", _SOLUTION_PLACE, optional=True)
        # A capture time is text here, and to_contract_dict writes it in the contract's form or refuses it: the phone
        # photo benchmark hands the solvers times ending in +00:00 or in a clip's own offset
        # (research/benchmarks/phone_photos), which the solvers read correctly, so they are not refused here
        if self.observed_at_utc is not None:
            require_contract_value(self.observed_at_utc, contract.TEXT, "A solution's observed_at_utc")
        for warning in self.warnings:
            require_contract_value(warning, contract.TEXT, "One of a solution's warnings")
        if self.pose_estimate is not None:
            if self.status != "approximate" or self.wcs is None:
                raise ContractRefusal(
                    "A pose estimate belongs only to an approximate solution with a WCS: "
                    "it states how far that WCS may be off."
                )
            if any(
                swept.crpix_x != self.wcs.crpix_x or swept.crpix_y != self.wcs.crpix_y
                for swept in self.pose_estimate.swept_wcs
            ):
                raise ContractRefusal(
                    "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: "
                    "all of them describe the same frame."
                )
            # The image size itself, which this record holds and the contract's WCS does not (there it can only be
            # read off the fields of view, astronomy.ts and services/contracts/astronomy.py)
            if any(
                swept.width != self.wcs.width or swept.height != self.wcs.height
                for swept in self.pose_estimate.swept_wcs
            ):
                raise ContractRefusal(
                    "Every swept WCS of a pose estimate has the nominal WCS's width and height: "
                    "all of them describe the same frame."
                )
        if self.status == "pending":
            if self.quality is not None:
                raise ContractRefusal("A pending solution has no quality: no solver has run, so nothing was counted.")
            if self.solver is not None:
                raise ContractRefusal("A pending solution names no solver: none has run.")
        else:
            if self.solver is None and self.status != "unresolved":
                raise ContractRefusal("Only a pending solution, or an unresolved one no solver reached, names no solver.")
            if self.quality is None and self.status in ("calibrated", "approximate"):
                raise ContractRefusal("A calibrated or approximate solution reports the quality it was judged on.")
        if (self.observer_lat is None) != (self.observer_lon is None):
            raise ContractRefusal("observer_lat and observer_lon must both be provided or both be None.")
        has_place = self.observer_lat is not None and self.observer_lon is not None
        needs_time_and_place = (
            self.status in ("approximate", "predicted_only")
            or any(f.evidence_class == "predicted" for f in self.findings)
        )
        if needs_time_and_place and (self.observed_at_utc is None or not has_place):
            raise ContractRefusal(
                "An approximate or predicted_only solution, or any solution with a predicted finding, "
                "needs observed_at_utc, observer_lat and observer_lon."
            )
        if self.quality is not None and self.quality.passed_independent_verification and self.wcs is None:
            raise ContractRefusal("Held-out verification passes only with a WCS.")
        # A passed verification places catalog matches and deep-sky findings through the WCS: only a calibrated
        # solution passed it, never an approximate, ambiguous, unresolved or predicted-only one
        if self.quality is not None and self.quality.passed_independent_verification and self.status != "calibrated":
            raise ContractRefusal(
                f"Only a calibrated solution passes held-out verification, not a {self.status} one."
            )
        # Pixels reach the sky and the catalog only through a verified WCS: an observed finding has a
        # sky position exactly when there is one, and a catalog match needs one
        verified = self.has_verified_wcs
        for finding in self.findings:
            # A finding's detection was measured in this solution's recording: one that names another is refused
            measured_in = finding.source_detection.asset_id if finding.source_detection is not None else None
            if measured_in is not None and measured_in != self.source_asset_id:
                raise ContractRefusal(
                    f"Finding {finding.id}'s detection was measured in {measured_in}, "
                    f"not in this solution's recording {self.source_asset_id}."
                )
            if finding.evidence_class == "catalog_matched" and not verified:
                raise ContractRefusal(
                    f"Catalog-matched finding {finding.id} needs a verified WCS: "
                    "catalog association follows held-out verification."
                )
            if finding.evidence_class == "observed" and finding.has_sky_position != verified:
                raise ContractRefusal(
                    f"Observed finding {finding.id} "
                    + (
                        "lacks the sky position the verified WCS gives it."
                        if verified
                        else "has a sky position without a verified WCS."
                    )
                )
            # The pose fit's residual, the only residual an observed finding holds, belongs to an approximate solution
            # (the lead's ruling of 2026-11-12 on the observed Moon; the contract's rule of the solution)
            if (
                finding.evidence_class == "observed"
                and self.status != "approximate"
                and (finding.pixel_residual_px is not None or finding.angular_residual_arcsec is not None)
            ):
                raise ContractRefusal(f"{contract.OBSERVED_RESIDUAL_OF_A_POSE[:-1]} ({finding.id}, a {self.status} solution).")

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
            # Always in the contract's form: a time ending in Z as written, one with an offset as the same instant
            # in UTC (so no clip's local offset, which hints at where it was taken, is stored); a time that names
            # no instant is refused by the parser below
            result["observedAtUTC"] = contract_utc_instant(self.observed_at_utc)
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
        if self.pose_estimate is not None:
            result["poseEstimate"] = self.pose_estimate.to_contract_dict()
        # Judged by the contract's own parser before it leaves: nothing is stored or served that the TypeScript
        # schema would refuse. A refusal names the path and the rule, never the value
        contract.parse_observation_solution(result)
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
