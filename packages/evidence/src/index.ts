import {
  CatalogReference,
  CelestialFinding,
  EvidenceClass,
  SolutionStatus,
  SourceDetection,
} from '@albedian/contracts';

/**
 * ============================================================================
 * @albedian/evidence
 * Epistemic Integrity Engine, WCS Verification Gate & Uncertainty Formatting
 * ============================================================================
 */

export interface AuthorizedCalibrationProof {
  readonly kind: 'AuthorizedCalibrationProof';
  readonly wcsSolutionId: string;
  readonly sourceDetection: SourceDetection;
  readonly catalogRef: CatalogReference;
  readonly heldOutInliers: number;
  readonly medianResidualPx: number;
  readonly angularResidualArcsec: number;
  readonly pixelResidualPx: number;
}

export interface WCSGateMetrics {
  detectedCandidateCount: number;
  matchedInlierCount: number;
  heldOutInliers: number;
  medianResidualPx: number;
  p95ResidualPx: number;
  hasTimestampAndLocation: boolean;
}

export const MIN_HELD_OUT_INLIERS = 6;
export const MAX_MEDIAN_RESIDUAL_PX = 3.0;
export const MAX_P95_RESIDUAL_PX = 6.0;
export const MAX_MATCH_RESIDUAL_PX = 4.5;
export const MIN_SOURCE_DETECTION_SNR = 3.5;

/**
 * Evaluates whether an astrometric calibration attempt passes Albedian's
 * independent held-out verification gate.
 *
 * A solver returning a candidate match is NOT sufficient:
 * - Requires >= 6 spatially distributed held-out inliers
 * - Requires held-out median residual <= 3.0 px and p95 <= 6.0 px
 */
export function evaluateWCSVerificationGate(metrics: WCSGateMetrics): {
  status: SolutionStatus;
  passedIndependentVerification: boolean;
  reason: string;
} {
  if (
    metrics.heldOutInliers >= MIN_HELD_OUT_INLIERS &&
    metrics.matchedInlierCount >= MIN_HELD_OUT_INLIERS &&
    metrics.medianResidualPx <= MAX_MEDIAN_RESIDUAL_PX &&
    metrics.p95ResidualPx <= MAX_P95_RESIDUAL_PX
  ) {
    return {
      status: 'calibrated',
      passedIndependentVerification: true,
      reason: `Calibrated (${metrics.matchedInlierCount} matches, ${metrics.heldOutInliers} held-out inliers, median residual ${metrics.medianResidualPx.toFixed(2)} px).`,
    };
  }

  if (metrics.matchedInlierCount >= 4 && metrics.heldOutInliers < MIN_HELD_OUT_INLIERS) {
    return {
      status: 'ambiguous',
      passedIndependentVerification: false,
      reason: `Ambiguous pattern (${metrics.heldOutInliers}/${MIN_HELD_OUT_INLIERS} required held-out inliers verified). Direct pixel overlay disabled.`,
    };
  }

  if (
    metrics.matchedInlierCount >= MIN_HELD_OUT_INLIERS &&
    (metrics.medianResidualPx > MAX_MEDIAN_RESIDUAL_PX ||
      metrics.p95ResidualPx > MAX_P95_RESIDUAL_PX)
  ) {
    return {
      status: 'ambiguous',
      passedIndependentVerification: false,
      reason: `High reprojection residual (median ${metrics.medianResidualPx.toFixed(2)} px > ${MAX_MEDIAN_RESIDUAL_PX.toFixed(1)} px threshold).`,
    };
  }

  if (metrics.hasTimestampAndLocation) {
    return {
      status: 'predicted_only',
      passedIndependentVerification: false,
      reason: `${metrics.detectedCandidateCount} point detections insufficient for blind plate solve. Sky predicted separately from recorded timestamp and location.`,
    };
  }

  return {
    status: 'unresolved',
    passedIndependentVerification: false,
    reason: `Unresolved sky (${metrics.detectedCandidateCount} candidate detections; minimum pattern and location metadata unavailable).`,
  };
}

/**
 * Enforces the Epistemic Promotion Invariant (PBT Suite #3):
 * - `predicted` and `simulated` findings can NEVER become `observed` or `catalog_matched`
 *   unless accompanied by an explicit `AuthorizedCalibrationProof` with a valid
 *   pixel-level `SourceDetection` (`snr >= 3.5`) and verified WCS solution.
 */
export function validateEvidenceTransition(
  fromClass: EvidenceClass,
  toClass: EvidenceClass,
  proof?: AuthorizedCalibrationProof
): { allowed: boolean; reason: string } {
  if (fromClass === toClass) {
    return { allowed: true, reason: 'Identity evidence class preserved.' };
  }

  // Promoting observed -> catalog_matched requires verified WCS + residual proof
  if (fromClass === 'observed' && toClass === 'catalog_matched') {
    if (!proof) {
      return {
        allowed: false,
        reason: 'Cannot promote observed detection to catalog_matched without AuthorizedCalibrationProof.',
      };
    }
    if (proof.sourceDetection.snr < MIN_SOURCE_DETECTION_SNR) {
      return {
        allowed: false,
        reason: `Source detection SNR (${proof.sourceDetection.snr.toFixed(2)}) is below minimum threshold (${MIN_SOURCE_DETECTION_SNR}).`,
      };
    }
    if (
      proof.heldOutInliers < MIN_HELD_OUT_INLIERS ||
      proof.medianResidualPx > MAX_MEDIAN_RESIDUAL_PX ||
      proof.pixelResidualPx > MAX_MATCH_RESIDUAL_PX
    ) {
      return {
        allowed: false,
        reason: 'WCS solution or individual star match failed held-out residual verification.',
      };
    }
    return {
      allowed: true,
      reason: 'Observed source detection verified against catalog via held-out WCS.',
    };
  }

  // Deriving a simulated 3D representation from a catalog_matched finding is allowed
  // only when the finding is explicitly labeled `simulated`
  if (fromClass === 'catalog_matched' && toClass === 'simulated') {
    return {
      allowed: true,
      reason: 'Catalog-matched object projected into explicitly labeled simulated 3D space.',
    };
  }

  // Predicted or simulated can NEVER become observed or catalog_matched without real pixel detection proof
  if (
    (fromClass === 'predicted' || fromClass === 'simulated') &&
    (toClass === 'observed' || toClass === 'catalog_matched')
  ) {
    if (
      !proof ||
      proof.sourceDetection.snr < MIN_SOURCE_DETECTION_SNR ||
      proof.heldOutInliers < MIN_HELD_OUT_INLIERS ||
      proof.medianResidualPx > MAX_MEDIAN_RESIDUAL_PX ||
      proof.pixelResidualPx > MAX_MATCH_RESIDUAL_PX
    ) {
      return {
        allowed: false,
        reason: `Forbidden epistemic promotion: ${fromClass} -> ${toClass} requires independent source-pixel detection and verified WCS calibration.`,
      };
    }
    if (toClass === 'observed') {
      return {
        allowed: false,
        reason: 'Predicted or simulated entities cannot be retroactively rewritten as raw observed detections.',
      };
    }
    return {
      allowed: true,
      reason: 'Authorized via independent source pixel detection and verified WCS.',
    };
  }

  return {
    allowed: false,
    reason: `Disallowed epistemic state transition: ${fromClass} -> ${toClass}.`,
  };
}

/**
 * Promotes an `observed` finding to `catalog_matched` only if the proof passes validation.
 * Throws an error if the promotion violates scientific integrity invariants.
 */
export function promoteFindingToCatalogMatched(
  finding: CelestialFinding,
  proof: AuthorizedCalibrationProof
): CelestialFinding {
  const check = validateEvidenceTransition(
    finding.evidenceClass,
    'catalog_matched',
    proof
  );
  if (!check.allowed) {
    throw new Error(check.reason);
  }

  const dist = proof.catalogRef.bailerJonesDistancePc;
  const raRad = (proof.catalogRef.raDeg * Math.PI) / 180;
  const decRad = (proof.catalogRef.decDeg * Math.PI) / 180;
  const cartesianParsecICRS: [number, number, number] | undefined = dist
    ? [
        dist.median * Math.cos(decRad) * Math.cos(raRad),
        dist.median * Math.cos(decRad) * Math.sin(raRad),
        dist.median * Math.sin(decRad),
      ]
    : undefined;

  return {
    ...finding,
    evidenceClass: 'catalog_matched',
    title: proof.catalogRef.commonName,
    subtitle: `${proof.catalogRef.bayerDesignation ?? proof.catalogRef.catalogSourceId} · ${proof.catalogRef.constellation}`,
    sourceDetection: proof.sourceDetection,
    catalogRef: proof.catalogRef,
    skyPosition: {
      ...finding.skyPosition,
      raDeg: proof.catalogRef.raDeg,
      decDeg: proof.catalogRef.decDeg,
      uncertaintyArcsec: proof.angularResidualArcsec,
    },
    inferredDistancePc: dist
      ? {
          lower: dist.lower,
          median: dist.median,
          upper: dist.upper,
          method: dist.method,
        }
      : undefined,
    cartesianParsecICRS,
    evidence: {
      ...finding.evidence,
      evidenceClass: 'catalog_matched',
      wcsSolutionId: proof.wcsSolutionId,
      catalogReferenceId: proof.catalogRef.id,
      angularResidualArcsec: proof.angularResidualArcsec,
      pixelResidualPx: proof.pixelResidualPx,
      verifiedByIndependentHoldout: true,
    },
  };
}

export const PARSEC_TO_LIGHT_YEAR = 3.261563777;

export function formatDistanceWithUncertainty(
  distancePc?: {
    lower: number;
    median: number;
    upper: number;
    method: string;
  }
): {
  parsecText: string;
  lightYearText: string;
  uncertaintyText: string;
  canEnter3DSpace: boolean;
} {
  if (
    !distancePc ||
    !Number.isFinite(distancePc.median) ||
    distancePc.median <= 0 ||
    distancePc.lower <= 0 ||
    distancePc.upper < distancePc.median
  ) {
    return {
      parsecText: 'Distance Unconstrained',
      lightYearText: 'Distance Unconstrained',
      uncertaintyText: 'No reliable parallax or Bailer-Jones distance prior available.',
      canEnter3DSpace: false,
    };
  }

  const lyMedian = distancePc.median * PARSEC_TO_LIGHT_YEAR;
  const lyLower = distancePc.lower * PARSEC_TO_LIGHT_YEAR;
  const lyUpper = distancePc.upper * PARSEC_TO_LIGHT_YEAR;
  const halfSpanPc = (distancePc.upper - distancePc.lower) / 2;

  return {
    parsecText: `${distancePc.median.toFixed(1)} pc (${distancePc.lower.toFixed(1)}–${distancePc.upper.toFixed(1)} pc)`,
    lightYearText: `${lyMedian.toFixed(0)} ly (${lyLower.toFixed(0)}–${lyUpper.toFixed(0)} ly)`,
    uncertaintyText: `±${halfSpanPc.toFixed(1)} pc (68% CI · ${distancePc.method})`,
    canEnter3DSpace: true,
  };
}

export function getEpistemicBadgeDescriptor(evidenceClass: EvidenceClass): {
  label: string;
  shortCode: 'OBS' | 'CAT' | 'PRD' | 'SIM';
  markerShape: 'solid_dot_tick' | 'concentric_ring_elbow' | 'dashed_circle' | 'bracket_reticle';
  disclosure: string;
} {
  switch (evidenceClass) {
    case 'observed':
      return {
        label: 'Observed in Source Pixels',
        shortCode: 'OBS',
        markerShape: 'solid_dot_tick',
        disclosure: 'Measured directly from immutable source recording pixels.',
      };
    case 'catalog_matched':
      return {
        label: 'Catalog Matched (Verified WCS)',
        shortCode: 'CAT',
        markerShape: 'concentric_ring_elbow',
        disclosure: 'Source pixel detection matched to catalog via independently verified WCS.',
      };
    case 'predicted':
      return {
        label: 'Predicted (Ephemeris / Model)',
        shortCode: 'PRD',
        markerShape: 'dashed_circle',
        disclosure: 'Computed from recording time and location; not claimed as recovered camera pixels.',
      };
    case 'simulated':
      return {
        label: 'Simulated (Catalog Geometry)',
        shortCode: 'SIM',
        markerShape: 'bracket_reticle',
        disclosure: 'Catalog Geometry · Distance Estimates & Modeled Epoch.',
      };
  }
}
