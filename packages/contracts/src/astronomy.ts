import { z } from 'zod';
import { SHA256_HEX_PATTERN } from './media.js';
import { EvidenceClass, EvidenceClassSchema, SolutionStatusSchema } from './primitives.js';

/** A recording's SHA-256 as 64 lowercase hex digits. 64 zeros is a placeholder for a hash nobody computed, not a hash. */
export const SourceSha256Schema = z
  .string()
  .regex(new RegExp(SHA256_HEX_PATTERN))
  .refine((value) => !/^0+$/.test(value), { message: 'A SHA-256 of 64 zeros is a placeholder, not a hash.' });

/**
 * Astronomical Domain Entities: CatalogReference, ScientificEvidence,
 * SourceDetection, CelestialFinding, WCSCalibrationModel, and ObservationSolution.
 */

/**
 * The catalog row a finding stands on. Its Python mirror is services/contracts/catalog_reference.py, and
 * tests/fixtures/catalog_reference_contract.json holds samples both sides judge alike.
 *
 * `apparentMagnitude` is required except for a deep-sky object: OpenNGC gives none for many, and none is filled
 * in. An OpenNGC reference is a deep-sky object.
 */
export const CatalogReferenceSchema = z
  .object({
    id: z.string().min(1),
    provider: z.enum(['Gaia DR3', 'Hipparcos', 'JPL Horizons', 'SIMBAD', 'OpenNGC']),
    releaseVersion: z.string().min(1),
    catalogSourceId: z.string().min(1),
    commonName: z.string().min(1),
    bayerDesignation: z.string().optional(),
    constellation: z.string().min(1),
    objectType: z.enum(['star', 'planet', 'moon', 'deep_sky', 'satellite_candidate']),
    coordinateEpoch: z.string().min(1),
    raDeg: z.number().min(0).max(360),
    decDeg: z.number().min(-90).max(90),
    apparentMagnitude: z.number().optional(),
    colorIndexBpRp: z.number().optional(),
    spectralType: z.string().optional(),
    properMotionRaMasYr: z.number().optional(),
    properMotionDecMasYr: z.number().optional(),
    radialVelocityKmS: z.number().optional(),
    parallaxMas: z.number().optional(),
    parallaxErrorMas: z.number().nonnegative().optional(),
    bailerJonesDistancePc: z
      .object({
        lower: z.number().positive(),
        median: z.number().positive(),
        upper: z.number().positive(),
        method: z.enum(['photogeometric_bailer_jones_edr3', 'geometric_parallax', 'ephemeris_au']),
      })
      .optional(),
    citation: z.string().min(1),
  })
  .superRefine((reference, ctx) => {
    if (reference.apparentMagnitude === undefined && reference.objectType !== 'deep_sky') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['apparentMagnitude'],
        message: "Only a deep-sky object's reference may leave the apparent magnitude out.",
      });
    }
    if (reference.provider === 'OpenNGC' && reference.objectType !== 'deep_sky') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['objectType'],
        message: 'An OpenNGC reference is a deep-sky object.',
      });
    }
  });
export type CatalogReference = z.infer<typeof CatalogReferenceSchema>;

export const ScientificEvidenceSchema = z.object({
  id: z.string().min(1),
  evidenceClass: EvidenceClassSchema,
  verificationMethod: z.string().min(1),
  // The recording the evidence rests on. Absent only for a prediction made for a time and place with no
  // recording behind it; observed and catalog-matched findings must name theirs (CelestialFindingSchema).
  sourceAssetSha256: SourceSha256Schema.optional(),
  wcsSolutionId: z.string().optional(),
  catalogReferenceId: z.string().optional(),
  angularResidualArcsec: z.number().nonnegative().optional(),
  pixelResidualPx: z.number().nonnegative().optional(),
  distanceUncertaintyNote: z.string().optional(),
  limitations: z.array(z.string()).min(1),
  verifiedByIndependentHoldout: z.boolean(),
});
export type ScientificEvidence = z.infer<typeof ScientificEvidenceSchema>;

export const SourceDetectionSchema = z.object({
  assetId: z.string().min(1),
  frameTimeSec: z.number().nonnegative(),
  xPx: z.number().nonnegative(),
  yPx: z.number().nonnegative(),
  flux: z.number().positive(),
  snr: z.number().positive(),
  fwhmPx: z.number().nonnegative(),
  centroidUncertaintyPx: z.number().positive(),
});
export type SourceDetection = z.infer<typeof SourceDetectionSchema>;

export const SkyPositionSchema = z.object({
  raDeg: z.number().min(0).max(360),
  decDeg: z.number().min(-90).max(90),
  uncertaintyArcsec: z.number().nonnegative(),
  altDeg: z.number().min(-90).max(90).optional(),
  azDeg: z.number().min(0).max(360).optional(),
});
export type SkyPosition = z.infer<typeof SkyPositionSchema>;

function celestialFindingVariant<C extends EvidenceClass, P extends z.ZodTypeAny>(
  evidenceClass: C,
  skyPosition: P
) {
  return z.object({
    id: z.string().min(1),
    // Absent only for a standalone prediction or simulation made for a time and place with no ObservationSolution;
    // observed and catalog-matched findings, and every finding inside an ObservationSolution, name theirs.
    observationSolutionId: z.string().min(1).optional(),
    evidenceClass: z.literal(evidenceClass),
    title: z.string().min(1),
    subtitle: z.string().min(1),
    sourceDetection: SourceDetectionSchema.optional(),
    catalogRef: CatalogReferenceSchema.optional(),
    skyPosition,
    inferredDistancePc: z
      .object({
        lower: z.number().positive(),
        median: z.number().positive(),
        upper: z.number().positive(),
        method: z.string().min(1),
      })
      .optional(),
    cartesianParsecICRS: z.tuple([z.number(), z.number(), z.number()]).optional(),
    evidence: ScientificEvidenceSchema,
  });
}

/**
 * `catalog_matched`, `predicted` and `simulated` findings always carry a sky position.
 * An `observed` finding is a pixel measurement: it carries one only when a verified WCS
 * maps its pixels to the sky (checked by `ObservationSolutionSchema`), and without one it
 * carries the `sourceDetection` that locates it in the frame.
 */
export const CelestialFindingSchema = z
  .discriminatedUnion('evidenceClass', [
    celestialFindingVariant('observed', SkyPositionSchema.optional()),
    celestialFindingVariant('catalog_matched', SkyPositionSchema),
    celestialFindingVariant('predicted', SkyPositionSchema),
    celestialFindingVariant('simulated', SkyPositionSchema),
  ])
  .superRefine((finding, ctx) => {
    if (finding.evidenceClass === 'observed' && !finding.skyPosition && !finding.sourceDetection) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['sourceDetection'],
        message: 'An observed finding without a sky position needs the source detection that locates it in pixels.',
      });
    }
    if ((finding.evidenceClass === 'observed' || finding.evidenceClass === 'catalog_matched') && !finding.observationSolutionId) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['observationSolutionId'],
        message: 'Observed and catalog-matched findings belong to an ObservationSolution and name its id.',
      });
    }
    if ((finding.evidenceClass === 'observed' || finding.evidenceClass === 'catalog_matched') && !finding.evidence.sourceAssetSha256) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['evidence', 'sourceAssetSha256'],
        message: 'Observed and catalog-matched evidence rests on a recording and names its SHA-256.',
      });
    }
  });
export type CelestialFinding = z.infer<typeof CelestialFindingSchema>;

export const WCSCalibrationModelSchema = z.object({
  projection: z.literal('TAN'),
  crvalRaDeg: z.number().min(0).max(360),
  crvalDecDeg: z.number().min(-90).max(90),
  crpixX: z.number(),
  crpixY: z.number(),
  scaleDegPerPx: z.number().positive(),
  rotationDeg: z.number(),
  radialDistortionK1: z.number().min(-0.25).max(0.25),
  radialDistortionK2: z.number().min(-0.25).max(0.25),
  parity: z.union([z.literal(1), z.literal(-1)]),
  fovHorizontalDeg: z.number().positive().max(175),
  fovVerticalDeg: z.number().positive().max(175),
});
export type WCSCalibrationModel = z.infer<typeof WCSCalibrationModelSchema>;

/**
 * Whether a solution's WCS passed independent held-out verification, the only way pixels
 * in its source frame are placed on the sky.
 */
export function hasVerifiedWcs(solution: {
  wcs?: WCSCalibrationModel;
  quality?: { passedIndependentVerification: boolean };
}): boolean {
  return solution.wcs !== undefined && solution.quality?.passedIndependentVerification === true;
}

export const ObservationSolutionSchema = z
  .object({
    id: z.string().min(1),
    sourceAssetId: z.string().min(1),
    // Absent only for a pending solution with no findings: nothing has been solved, so no recording is named
    sourceSha256: SourceSha256Schema.optional(),
    frameTimeSec: z.number().nonnegative(),
    revision: z.number().int().positive(),
    status: SolutionStatusSchema,
    // Present when the recording's capture time is known; required whenever a pose or finding is predicted from time and place
    observedAtUTC: z.string().datetime().optional(),
    observerLocation: z
      .object({
        latDeg: z.number().min(-90).max(90),
        lonDeg: z.number().min(-180).max(180),
        placeName: z.string().optional(),
      })
      .optional(),
    // Who solved it: absent on a pending solution, which no solver has run on, and on an unresolved one
    // whose attempt failed before any solver ran
    solver: z
      .object({
        name: z.string().min(1),
        version: z.string().min(1),
        catalogRelease: z.string().min(1),
      })
      .optional(),
    wcs: WCSCalibrationModelSchema.optional(),
    // What the solver measured: required on a calibrated or approximate solution and absent on a pending
    // one, so no row of zeros ever stands in for a solve that did not happen
    quality: z.object({
      detectedCandidateCount: z.number().int().nonnegative(),
      matchedInlierCount: z.number().int().nonnegative(),
      // Held-out detections paired with a catalog star under the fitted WCS (within the association radius), and
      // those of them inside the per-star inlier gate
      heldOutPairCount: z.number().int().nonnegative(),
      heldOutInliers: z.number().int().nonnegative(),
      // Held-out residuals, over every held-out pair: present exactly when there is one, never a stand-in number
      medianResidualPx: z.number().nonnegative().optional(),
      p95ResidualPx: z.number().nonnegative().optional(),
      medianResidualArcsec: z.number().nonnegative().optional(),
      passedIndependentVerification: z.boolean(),
    }).optional(),
    findings: z.array(CelestialFindingSchema),
    warnings: z.array(z.string()),
  })
  .superRefine((solution, ctx) => {
    const quality = solution.quality;
    if (solution.status === 'pending') {
      if (quality !== undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality'],
          message: 'A pending solution has no quality: no solver has run, so nothing was counted.',
        });
      }
      if (solution.solver !== undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['solver'],
          message: 'A pending solution names no solver: none has run.',
        });
      }
    } else {
      // An unresolved solution may come from an attempt that failed before any solver ran (a file that
      // would not decode); every other outcome was reached by a solver, which it names
      if (solution.solver === undefined && solution.status !== 'unresolved') {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['solver'],
          message: 'Only a pending solution, or an unresolved one no solver reached, names no solver.',
        });
      }
      if (quality === undefined && (solution.status === 'calibrated' || solution.status === 'approximate')) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality'],
          message: 'A calibrated or approximate solution reports the quality it was judged on.',
        });
      }
    }
    if (quality !== undefined) {
      (['medianResidualPx', 'p95ResidualPx', 'medianResidualArcsec'] as const).forEach((key) => {
        if ((quality[key] !== undefined) !== quality.heldOutPairCount > 0) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['quality', key],
            message:
              quality.heldOutPairCount > 0
                ? 'Held-out stars were paired, so their residual is reported.'
                : 'No held-out star was paired, so there is no residual to report.',
          });
        }
      });
      if (quality.heldOutInliers > quality.heldOutPairCount) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality', 'heldOutInliers'],
          message: 'Held-out inliers are held-out pairs inside the inlier gate: there cannot be more of them than pairs.',
        });
      }
      if (quality.passedIndependentVerification && (solution.wcs === undefined || quality.medianResidualPx === undefined)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality', 'passedIndependentVerification'],
          message: 'Held-out verification passes only with a WCS and the held-out residuals it was judged on.',
        });
      }
      // A passed verification places catalog matches and deep-sky findings through the WCS: only a calibrated
      // solution passed it, never an approximate, ambiguous, unresolved or predicted-only one
      if (quality.passedIndependentVerification && solution.status !== 'calibrated') {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality', 'passedIndependentVerification'],
          message: `Only a calibrated solution passes held-out verification, not a ${solution.status} one.`,
        });
      }
    }
    if (solution.sourceSha256 === undefined && !(solution.status === 'pending' && solution.findings.length === 0)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['sourceSha256'],
        message: 'Only a pending solution with no findings names no recording.',
      });
    }
    // Ephemeris predictions and horizon/beacon poses are computed from capture time and observer location,
    // so approximate and predicted_only solutions and any solution carrying a predicted finding need both
    const needsTimeAndPlace =
      solution.status === 'approximate' ||
      solution.status === 'predicted_only' ||
      solution.findings.some((finding) => finding.evidenceClass === 'predicted');
    if (needsTimeAndPlace && solution.observedAtUTC === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['observedAtUTC'],
        message: 'An approximate or predicted_only solution, or any solution with a predicted finding, needs observedAtUTC.',
      });
    }
    if (needsTimeAndPlace && solution.observerLocation === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['observerLocation'],
        message: 'An approximate or predicted_only solution, or any solution with a predicted finding, needs observerLocation.',
      });
    }
    // Pixels reach the sky and the catalog only through a verified WCS: an observed finding has a
    // sky position exactly when there is one, and a catalog match needs one
    const verified = hasVerifiedWcs(solution);
    solution.findings.forEach((finding, index) => {
      if (!finding.observationSolutionId) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['findings', index, 'observationSolutionId'],
          message: 'A finding inside an ObservationSolution must name its ObservationSolution id.',
        });
      }
      if (finding.evidenceClass === 'catalog_matched' && !verified) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['findings', index, 'evidenceClass'],
          message: 'A catalog_matched finding needs a verified WCS: catalog association follows held-out verification.',
        });
      }
      if (finding.evidenceClass === 'observed' && (finding.skyPosition !== undefined) !== verified) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['findings', index, 'skyPosition'],
          message: verified
            ? 'An observed finding in a solution with a verified WCS needs the sky position that WCS gives it.'
            : 'An observed finding has no sky position without a verified WCS.',
        });
      }
    });
  });
export type ObservationSolution = z.infer<typeof ObservationSolutionSchema>;
