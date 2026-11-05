import {
  CatalogReference,
  CelestialFinding,
  EvidenceClass,
  EvidenceKind,
  EvidenceRecord,
  ObservationSolution,
  OperationalInstrumentStatus,
  SolutionStatus,
  SourceAsset,
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
  /** Held-out residuals; absent when no held-out star paired with the catalog. */
  medianResidualPx?: number;
  p95ResidualPx?: number;
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
  const median = metrics.medianResidualPx;
  const p95 = metrics.p95ResidualPx;
  if (
    metrics.heldOutInliers >= MIN_HELD_OUT_INLIERS &&
    metrics.matchedInlierCount >= MIN_HELD_OUT_INLIERS &&
    median !== undefined &&
    p95 !== undefined &&
    median <= MAX_MEDIAN_RESIDUAL_PX &&
    p95 <= MAX_P95_RESIDUAL_PX
  ) {
    return {
      status: 'calibrated',
      passedIndependentVerification: true,
      reason: `Calibrated (${metrics.matchedInlierCount} matches, ${metrics.heldOutInliers} held-out inliers, median residual ${median.toFixed(2)} px).`,
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
    median !== undefined &&
    p95 !== undefined &&
    (median > MAX_MEDIAN_RESIDUAL_PX || p95 > MAX_P95_RESIDUAL_PX)
  ) {
    return {
      status: 'ambiguous',
      passedIndependentVerification: false,
      reason: `High reprojection residual (median ${median.toFixed(2)} px > ${MAX_MEDIAN_RESIDUAL_PX.toFixed(1)} px threshold).`,
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

/** A distance's half-width in words, named for the method that gave its bounds; any other method gets no
 * percentage, only the bounds as given. */
function describeDistanceSpread(halfSpanPc: number, method: string): string {
  const spread = `±${halfSpanPc.toFixed(1)} pc`;
  switch (method) {
    case 'photogeometric_bailer_jones_edr3':
      return `${spread}: half the 16th to 84th percentile range of the Bailer-Jones et al. (2021) photogeometric distance`;
    case 'geometric_parallax':
      return `${spread}: half the range from the parallax error`;
    case 'ephemeris_au':
      return `${spread}: half the range of the model's error bounds`;
    default:
      return `${spread}: half the range of the bounds as given, method ${method}`;
  }
}

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
    parsecText: `${distancePc.median.toFixed(1)} pc (${distancePc.lower.toFixed(1)} to ${distancePc.upper.toFixed(1)} pc)`,
    lightYearText: `${lyMedian.toFixed(0)} ly (${lyLower.toFixed(0)} to ${lyUpper.toFixed(0)} ly)`,
    uncertaintyText: describeDistanceSpread(halfSpanPc, distancePc.method),
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
        label: 'Simulated',
        shortCode: 'SIM',
        markerShape: 'bracket_reticle',
        disclosure: 'Drawn or computed by the app; not measured in the source recording.',
      };
  }
}

/**
 * Maps `SolutionStatus` and an optional `CelestialFinding` to the 6-state
 * `OperationalInstrumentStatus` taxonomy (Track 7 & Track 14 Master Brief).
 */
export function deriveOperationalInstrumentStatus(
  solutionStatus: SolutionStatus,
  finding?: CelestialFinding | null
): OperationalInstrumentStatus {
  if (solutionStatus === 'pending') {
    return 'REGISTERING';
  }
  if (solutionStatus === 'unresolved') {
    return 'SOURCE UNAVAILABLE';
  }

  if (finding) {
    if (finding.evidenceClass === 'predicted') {
      return 'PROPAGATED';
    }
    if (finding.evidenceClass === 'simulated') {
      return 'RECONSTRUCTED';
    }
    if (finding.evidenceClass === 'catalog_matched') {
      if (solutionStatus === 'calibrated' || finding.evidence.verifiedByIndependentHoldout) {
        return 'MATCHED';
      }
      if (solutionStatus === 'approximate') {
        return 'RECONSTRUCTED';
      }
      return 'NO COVERAGE';
    }
    if (finding.evidenceClass === 'observed') {
      if (!finding.sourceDetection) {
        return 'SOURCE UNAVAILABLE';
      }
      if (solutionStatus === 'approximate') {
        return 'RECONSTRUCTED';
      }
      if (solutionStatus === 'calibrated') {
        return finding.catalogRef ? 'MATCHED' : 'NO COVERAGE';
      }
      return 'NO COVERAGE';
    }
  }

  switch (solutionStatus) {
    case 'calibrated':
      return 'MATCHED';
    case 'approximate':
      return 'RECONSTRUCTED';
    case 'predicted_only':
      return 'PROPAGATED';
    case 'ambiguous':
      return 'NO COVERAGE';
  }
}

/**
 * Maps a `CelestialFinding` and `SolutionStatus` to the granular 8-tier `EvidenceKind`
 * provenance taxonomy (`captured` | `registered` | `catalog-derived` | `observed-elsewhere`
 * | `propagated` | `reconstructed` | `simulated` | `illustrative`).
 */
export function deriveEvidenceKind(
  finding: CelestialFinding,
  solutionStatus: SolutionStatus = 'calibrated'
): EvidenceKind {
  switch (finding.evidenceClass) {
    case 'observed': {
      const methodLower = finding.evidence.verificationMethod.toLowerCase();
      if (methodLower.includes('coherent') || methodLower.includes('stack')) {
        return 'reconstructed';
      }
      return 'captured';
    }
    case 'catalog_matched': {
      if (
        finding.sourceDetection &&
        (solutionStatus === 'calibrated' || finding.evidence.verifiedByIndependentHoldout)
      ) {
        return 'registered';
      }
      if (solutionStatus === 'approximate') {
        return 'reconstructed';
      }
      return 'catalog-derived';
    }
    case 'predicted': {
      if (finding.catalogRef?.objectType === 'deep_sky') {
        return 'observed-elsewhere';
      }
      if (solutionStatus === 'approximate') {
        return 'reconstructed';
      }
      return 'propagated';
    }
    case 'simulated': {
      if (finding.inferredDistancePc || finding.cartesianParsecICRS) {
        return 'simulated';
      }
      return 'illustrative';
    }
  }
}

/** The held-out residuals a solution measured, or that it measured none. */
function describeHeldOutResiduals(quality: ObservationSolution['quality']): string {
  const { medianResidualPx, p95ResidualPx, medianResidualArcsec, heldOutInliers } = quality;
  if (medianResidualPx === undefined || p95ResidualPx === undefined) {
    return 'No held-out star paired with the catalog, so no held-out residual';
  }
  const arcsec = medianResidualArcsec !== undefined ? ` (±${medianResidualArcsec.toFixed(1)} arcsec)` : '';
  return `Held-out median residual ±${medianResidualPx.toFixed(2)} px${arcsec}, p95 ±${p95ResidualPx.toFixed(2)} px across ${heldOutInliers} held-out inliers`;
}

/**
 * The licence of each catalog provider's data. Gaia DR3 and Hipparcos come from the ESA archive, whose terms
 * (docs/science/CATALOG_STAR_TILES.md) are CC BY-NC 3.0 IGO. No finding uses JPL Horizons data: only the landing
 * scene's baked Sun and Moon positions come from it (scripts/plan_landing_moment.py writes
 * apps/web/src/spatial/webgpu/cislunar/landingMoment.json). Albedian makes no SIMBAD query.
 */
function resolveCatalogLicense(provider: CatalogReference['provider']): string {
  switch (provider) {
    case 'Gaia DR3':
      return 'CC BY-NC 3.0 IGO (ESA archive terms); credit ESA/Gaia/DPAC';
    case 'Hipparcos':
      return 'CC BY-NC 3.0 IGO (ESA archive terms); credit ESA';
    case 'JPL Horizons':
      return "No finding uses JPL Horizons data; only the landing scene's baked Sun and Moon positions come from it";
    case 'SIMBAD':
      return 'Not used: Albedian makes no SIMBAD query';
  }
}

type DistanceEstimate = NonNullable<CatalogReference['bailerJonesDistancePc']>;

/** A catalog distance in words, named for the method that gave it (as services/science/catalog_model.py does). */
function describeCatalogDistance({ lower, median, upper, method }: DistanceEstimate): string {
  const range = `${lower.toFixed(1)} to ${upper.toFixed(1)} pc`;
  switch (method) {
    case 'photogeometric_bailer_jones_edr3':
      return `Bailer-Jones et al. (2021) photogeometric distance ${median.toFixed(1)} pc (16th to 84th percentile: ${range})`;
    case 'geometric_parallax':
      return `Inverse-parallax distance ${median.toFixed(1)} pc (${range} from the parallax error)`;
    case 'ephemeris_au':
      return `Ephemeris distance ${median.toFixed(1)} pc (${range} from the model's error bounds)`;
  }
}

/**
 * Builds the structured provenance chain (`EvidenceRecord[]`) for a `CelestialFinding`:
 * 1. Sensor Capture Record (`captured`)
 * 2. WCS Sky Registration Record (`registered` | `reconstructed` | `propagated`)
 * 3. Catalog / Ephemeris / Survey Record (`catalog-derived` | `propagated` | `observed-elsewhere` | `simulated` | `captured`)
 */
export function buildFindingEvidenceRecords(
  sourceAsset: SourceAsset,
  solution: ObservationSolution,
  finding: CelestialFinding
): EvidenceRecord[] {
  const captureTimeUtc = sourceAsset.capturedAtUTC ?? solution.observedAtUTC;
  const det = finding.sourceDetection;

  const captureRecord: EvidenceRecord = {
    id: `${finding.id}:capture:${sourceAsset.id}`,
    kind: 'captured',
    title: det
      ? `Sensor Pixel Centroid (${det.xPx.toFixed(1)}, ${det.yPx.toFixed(1)}) px`
      : `Source Frame Capture (${sourceAsset.title})`,
    sourceUrl: sourceAsset.uri,
    sourceIdentifier: `sha256:${sourceAsset.sha256}`,
    license:
      sourceAsset.privacyClass === 'public_sample'
        ? // SourceAsset has no licence field, so no sample's record can name one
          'No licence recorded: the asset record names none'
        : 'Observer First-Party Recording',
    observationTimeUtc: captureTimeUtc,
    modelEpochUtc: solution.observedAtUTC,
    coordinateFrame: 'Sensor Pixel Grid (u, v) Origin Top-Left',
    coordinateSpace: 'astronomical',
    units: {
      xPx: 'px',
      yPx: 'px',
      flux: 'ADU',
      snr: 'sigma',
    },
    uncertaintyDescription: det
      ? `Centroid ±${det.centroidUncertaintyPx.toFixed(2)} px · PSF FWHM ${det.fwhmPx.toFixed(2)} px · SNR ${det.snr.toFixed(1)}`
      : 'No direct single-frame point-source centroid above SNR threshold.',
    processingDescription: `Immutable source asset (${sourceAsset.width}×${sourceAsset.height} ${sourceAsset.videoCodec}); ${finding.evidence.verificationMethod}`,
    coverageDescription: sourceAsset.observerLocation
      ? `Observer site ${sourceAsset.observerLocation.latDeg.toFixed(4)}°, ${sourceAsset.observerLocation.lonDeg.toFixed(4)}°${sourceAsset.observerLocation.placeName ? ` (${sourceAsset.observerLocation.placeName})` : ''}`
      : `Sensor frame ${sourceAsset.width}×${sourceAsset.height} px at t=${(det?.frameTimeSec ?? solution.frameTimeSec).toFixed(2)}s`,
  };

  const wcsKind: EvidenceKind =
    solution.status === 'calibrated' && solution.quality.passedIndependentVerification
      ? 'registered'
      : solution.status === 'approximate'
        ? 'reconstructed'
        : 'propagated';

  const sky = finding.skyPosition;
  const registrationCoverage = solution.wcs
    ? `FOV ${solution.wcs.fovHorizontalDeg.toFixed(1)}°×${solution.wcs.fovVerticalDeg.toFixed(1)}° centered at RA ${solution.wcs.crvalRaDeg.toFixed(4)}°, Dec ${solution.wcs.crvalDecDeg.toFixed(4)}° (${solution.wcs.scaleDegPerPx.toFixed(4)}°/px)`
    : sky
      ? `Sky position RA ${sky.raDeg.toFixed(4)}°, Dec ${sky.decDeg.toFixed(4)}° (±${sky.uncertaintyArcsec.toFixed(1)} arcsec)`
      : undefined;

  // A detection with neither a WCS nor a sky position was never registered, so no record claims it was
  const records: EvidenceRecord[] = [captureRecord];
  if (registrationCoverage) {
    records.push({
      id: `${finding.id}:wcs:${solution.id}`,
      kind: wcsKind,
      title: `WCS Sky Registration (${solution.solver.name} ${solution.solver.version})`,
      sourceIdentifier: `${solution.id}@rev${solution.revision}`,
      license: 'Albedian Deterministic Astrometry Pipeline',
      observationTimeUtc: solution.observedAtUTC,
      modelEpochUtc: solution.observedAtUTC,
      coordinateFrame: solution.wcs
        ? `ICRS / J2000 ${solution.wcs.projection} Tangent Projection`
        : 'Topocentric Horizon / Uncalibrated Frame',
      coordinateSpace: 'astronomical',
      units: {
        raDeg: 'deg',
        decDeg: 'deg',
        residualPx: 'px',
        residualArcsec: 'arcsec',
      },
      uncertaintyDescription: `${describeHeldOutResiduals(solution.quality)}${finding.evidence.pixelResidualPx !== undefined ? `; target residual ±${finding.evidence.pixelResidualPx.toFixed(2)} px` : ''}`,
      processingDescription: `Solution status: ${solution.status}; independent holdout verification: ${solution.quality.passedIndependentVerification ? 'PASSED' : 'NOT PASSED'}`,
      coverageDescription: registrationCoverage,
    });
  }

  const cat = finding.catalogRef;
  if (cat) {
    const isEphemeris =
      cat.provider === 'JPL Horizons' ||
      cat.objectType === 'planet' ||
      cat.objectType === 'moon' ||
      cat.objectType === 'satellite_candidate';
    const isDeepSky = cat.objectType === 'deep_sky';
    const catalogKind: EvidenceKind =
      finding.evidenceClass === 'simulated'
        ? 'simulated'
        : isEphemeris
          ? 'propagated'
          : isDeepSky
            ? 'observed-elsewhere'
            : 'catalog-derived';

    const dist = cat.bailerJonesDistancePc;
    const catalogRecord: EvidenceRecord = {
      id: `${finding.id}:catalog:${cat.id}`,
      kind: catalogKind,
      title: `${cat.provider} (${cat.releaseVersion}): ${cat.commonName} (${cat.catalogSourceId})`,
      sourceIdentifier: `${cat.provider}:${cat.catalogSourceId}`,
      license: resolveCatalogLicense(cat.provider),
      observationTimeUtc: solution.observedAtUTC,
      modelEpochUtc: cat.coordinateEpoch,
      coordinateFrame: 'ICRS (Barycentric / Heliocentric Parsec & Equatorial J2000)',
      coordinateSpace: 'astronomical',
      units: {
        raDeg: 'deg',
        decDeg: 'deg',
        apparentMagnitude: 'mag',
        distancePc: 'pc',
      },
      uncertaintyDescription: dist
        ? `${describeCatalogDistance(dist)}${cat.parallaxMas !== undefined && cat.parallaxErrorMas !== undefined ? `; parallax ${cat.parallaxMas.toFixed(2)} ± ${cat.parallaxErrorMas.toFixed(2)} mas` : ''}`
        : finding.evidence.distanceUncertaintyNote ??
          (sky
            ? `Angular uncertainty ±${sky.uncertaintyArcsec.toFixed(1)} arcsec`
            : 'No sky position, so no angular uncertainty'),
      processingDescription: `Citation: ${cat.citation}`,
      coverageDescription: `${cat.constellation} (${cat.objectType}, m=${cat.apparentMagnitude.toFixed(2)})`,
    };

    return [...records, catalogRecord];
  }

  if (finding.evidenceClass === 'observed') {
    // Only a detection placed on the sky was compared with the catalog
    if (finding.skyPosition) {
      records.push({
        id: `${finding.id}:catalog:uncataloged`,
        kind: 'captured',
        title: `Uncataloged Sensor Locus (${finding.title})`,
        sourceIdentifier: `uncataloged:${finding.id}`,
        license: 'Observer First-Party Telemetry',
        observationTimeUtc: solution.observedAtUTC,
        modelEpochUtc: solution.observedAtUTC,
        coordinateFrame: 'ICRS J2000 (WCS Back-Projected)',
        coordinateSpace: 'astronomical',
        units: {
          raDeg: 'deg',
          decDeg: 'deg',
          uncertaintyArcsec: 'arcsec',
        },
        uncertaintyDescription: `Positional uncertainty ±${finding.skyPosition.uncertaintyArcsec.toFixed(1)} arcsec; no static reference entry in ${solution.solver.catalogRelease} within match radius.`,
        processingDescription:
          'Differential photometry candidate (Observed ≠ Static Catalog Baseline)',
        coverageDescription: `RA ${finding.skyPosition.raDeg.toFixed(4)}°, Dec ${finding.skyPosition.decDeg.toFixed(4)}° outside bright-star baseline threshold`,
      });
    }
  } else {
    records.push({
      id: `${finding.id}:model:${solution.id}`,
      kind: deriveEvidenceKind(finding, solution.status),
      title: `Topocentric Ephemeris / Sky Model (${finding.title})`,
      sourceIdentifier: `${solution.solver.catalogRelease}:${finding.id}`,
      // Every prediction without a catalog record comes from services/science/ephemeris.py's two models
      license:
        'Computed in Albedian from the Standish and Williams (1992) elements (JPL Solar System Dynamics) and Meeus (1998), with no JPL Horizons request',
      observationTimeUtc: solution.observedAtUTC,
      modelEpochUtc: solution.observedAtUTC,
      coordinateFrame: 'Topocentric / ICRS J2000',
      coordinateSpace: 'astronomical',
      units: {
        raDeg: 'deg',
        decDeg: 'deg',
        uncertaintyArcsec: 'arcsec',
      },
      uncertaintyDescription: `Model uncertainty ±${finding.skyPosition.uncertaintyArcsec.toFixed(1)} arcsec`,
      processingDescription: finding.evidence.verificationMethod,
      coverageDescription: `RA ${finding.skyPosition.raDeg.toFixed(4)}°, Dec ${finding.skyPosition.decDeg.toFixed(4)}°`,
    });
  }

  return records;
}

