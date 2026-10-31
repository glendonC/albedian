import { z } from 'zod';
import { EvidenceClassSchema, SolutionStatusSchema } from './primitives.js';

/**
 * Astronomical Domain Entities: CatalogReference, ScientificEvidence,
 * SourceDetection, CelestialFinding, WCSCalibrationModel, and ObservationSolution.
 */

export const CatalogReferenceSchema = z.object({
  id: z.string().min(1),
  provider: z.enum(['Gaia DR3', 'Hipparcos', 'JPL Horizons', 'SIMBAD']),
  releaseVersion: z.string().min(1),
  catalogSourceId: z.string().min(1),
  commonName: z.string().min(1),
  bayerDesignation: z.string().optional(),
  constellation: z.string().min(1),
  objectType: z.enum(['star', 'planet', 'moon', 'deep_sky', 'satellite_candidate']),
  coordinateEpoch: z.string().min(1),
  raDeg: z.number().min(0).max(360),
  decDeg: z.number().min(-90).max(90),
  apparentMagnitude: z.number(),
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
});
export type CatalogReference = z.infer<typeof CatalogReferenceSchema>;

export const ScientificEvidenceSchema = z.object({
  id: z.string().min(1),
  evidenceClass: EvidenceClassSchema,
  verificationMethod: z.string().min(1),
  sourceAssetSha256: z.string().min(16),
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
  fwhmPx: z.number().positive(),
  centroidUncertaintyPx: z.number().positive(),
});
export type SourceDetection = z.infer<typeof SourceDetectionSchema>;

export const CelestialFindingSchema = z.object({
  id: z.string().min(1),
  observationSolutionId: z.string().min(1),
  evidenceClass: EvidenceClassSchema,
  title: z.string().min(1),
  subtitle: z.string().min(1),
  sourceDetection: SourceDetectionSchema.optional(),
  catalogRef: CatalogReferenceSchema.optional(),
  skyPosition: z.object({
    raDeg: z.number().min(0).max(360),
    decDeg: z.number().min(-90).max(90),
    uncertaintyArcsec: z.number().nonnegative(),
    altDeg: z.number().min(-90).max(90).optional(),
    azDeg: z.number().min(0).max(360).optional(),
  }),
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

export const ObservationSolutionSchema = z.object({
  id: z.string().min(1),
  sourceAssetId: z.string().min(1),
  sourceSha256: z.string().min(16),
  frameTimeSec: z.number().nonnegative(),
  revision: z.number().int().positive(),
  status: SolutionStatusSchema,
  observedAtUTC: z.string().datetime(),
  observerLocation: z
    .object({
      latDeg: z.number().min(-90).max(90),
      lonDeg: z.number().min(-180).max(180),
      placeName: z.string().optional(),
    })
    .optional(),
  solver: z.object({
    name: z.string().min(1),
    version: z.string().min(1),
    catalogRelease: z.string().min(1),
  }),
  wcs: WCSCalibrationModelSchema.optional(),
  quality: z.object({
    detectedCandidateCount: z.number().int().nonnegative(),
    matchedInlierCount: z.number().int().nonnegative(),
    heldOutInliers: z.number().int().nonnegative(),
    medianResidualPx: z.number().nonnegative(),
    p95ResidualPx: z.number().nonnegative(),
    medianResidualArcsec: z.number().nonnegative(),
    passedIndependentVerification: z.boolean(),
  }),
  findings: z.array(CelestialFindingSchema),
  warnings: z.array(z.string()),
});
export type ObservationSolution = z.infer<typeof ObservationSolutionSchema>;
