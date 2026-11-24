import { z } from 'zod';
import { SHA256_HEX_PATTERN } from './media.js';
import { EvidenceClass, EvidenceClassSchema, SolutionStatusSchema } from './primitives.js';
import { BAYER_DESIGNATION_PATTERN, FLAMSTEED_NUMBER_MAX, FLAMSTEED_NUMBER_MIN } from './designation.js';
import { isRealCalendarDay, wireInt, wireNumber } from './wire.js';

/** A recording's SHA-256 as 64 lowercase hex digits. 64 zeros is a placeholder for a hash nobody computed, not a hash. */
export const SourceSha256Schema = z
  .string()
  .regex(new RegExp(SHA256_HEX_PATTERN))
  .refine((value) => !/^0+$/.test(value), { message: 'A SHA-256 of 64 zeros is a placeholder, not a hash.' });

/**
 * Astronomical Domain Entities: CatalogReference, ScientificEvidence,
 * SourceDetection, CelestialFinding, WCSCalibrationModel, PoseEstimate, and ObservationSolution.
 *
 * Every number here is finite: JSON carries no Infinity, and the Python mirrors refuse one. The Python parser of the
 * solution family is services/contracts/astronomy.py; tests/fixtures/observation_solution_contract.json holds the
 * samples and the table of field rules both sides are held to.
 */

/**
 * The held-out gate that `passedIndependentVerification` stands for: at least this many held-out inliers, and a
 * median held-out residual of at most this many pixels. The plate solver defines the two numbers
 * (MIN_HELD_OUT_INLIERS and MAX_HELD_OUT_MEDIAN_RESIDUAL_PX in services/science/solver.py), the Python parser reads
 * them there, and the contract fixture carries them here to be compared.
 */
export const MIN_HELD_OUT_INLIERS = 6;
export const MAX_HELD_OUT_MEDIAN_RESIDUAL_PX = 3.0;

/**
 * Rounding room, in degrees, when a swept WCS's field of view is compared with the nominal one's [proposed: a
 * billionth of a degree covers the last digits of an arctangent; one pixel more of image width moves a field of view
 * by about the pixel scale, millions of times more].
 */
export const SWEPT_FOV_TOLERANCE_DEG = 1e-9;

/**
 * What the detector measured on the frame's own pixels (`quality.detection`; services/science/detector.py fills it).
 * The star profile's bounds are literals, the same in the Python parser, and the detector's fill imports them, so a
 * width the fit leaves exactly at a bound is taken on every machine: the narrowest width 1 / sqrt(2 ln 20) px written
 * as its double, a Gaussian whose four nearest pixels hold 5 % of its peak; the widest the fit's own bound; the fewest
 * and most stars the profile is measured from; and the angle's bound, pi / 2, written as its double (the detector
 * folds the angle into above -pi / 2 and at most pi / 2).
 */
export const PROFILE_MIN_SIGMA_PX = 0.4085389826536349;
export const PROFILE_MAX_SIGMA_PX = 8.0;
export const PROFILE_MIN_STARS = 10;
export const PROFILE_MAX_STARS = 400;
export const PROFILE_ANGLE_LIMIT_RAD = 1.5707963267948966;
/** What every detection's snr counts. */
export const DETECTION_STATISTICS = ['matched', 'whitened'] as const;
/**
 * The measured conditions under which the detector's false rate is not controlled, in the one order a list of them is
 * written in. "quantised" left with realsky-4's second version (measured to have no cause once the background is on a
 * grid) and "noise_tail" stays out until its meaning is settled: both are refused.
 */
export const NOISE_LIMITS = ['near_black_clip'] as const;

/**
 * Whether `limits` lists each value at most once and in `order`'s order (true for none at all); every value is one of
 * `order`'s. The detection's noise limits are held to it with NOISE_LIMITS, so a later limit enters by that list alone;
 * the fixture's noiseLimitOrderCases hold it, on both sides, to an order of two values.
 */
export function listsEachOnceInOrder(limits: readonly string[], order: readonly string[] = NOISE_LIMITS): boolean {
  const places = limits.map((limit) => order.indexOf(limit));
  return places.every((place, index) => place >= 0 && (index === 0 || places[index - 1]! < place));
}

/**
 * The frame's measured star: an elliptical Gaussian fitted to the median of the frame's bright, isolated, unsaturated
 * stars. Widths in pixels, the angle of the wider axis from +x toward +y in radians (it means nothing when the widths
 * are equal), and how many stars it was measured from. A closed record: it refuses a key it does not name. That the
 * narrower width is at most the wider is the detection's rule.
 */
export const StarProfileSchema = z
  .object({
    sigmaMajorPx: z.number().min(PROFILE_MIN_SIGMA_PX).max(PROFILE_MAX_SIGMA_PX),
    sigmaMinorPx: z.number().min(PROFILE_MIN_SIGMA_PX).max(PROFILE_MAX_SIGMA_PX),
    angleRad: wireNumber().gt(-PROFILE_ANGLE_LIMIT_RAD).max(PROFILE_ANGLE_LIMIT_RAD),
    stars: z.number().int().min(PROFILE_MIN_STARS).max(PROFILE_MAX_STARS),
  })
  .strict();
export type StarProfile = z.infer<typeof StarProfileSchema>;

/**
 * What the detector measured on the pixels the solution's detections were measured on (the source frame, or a clip's
 * stacked composite): the statistic every detection's snr counts, the noise limits it measured (an empty list when it
 * measured and none was raised), and the star profile a whitened statistic was built for. Every value is measured on
 * those pixels and on nothing else, and the record says so itself. A closed record: it refuses a key it does not name.
 */
export const DetectionQualitySchema = z
  .object({
    evidenceClass: z.literal('observed'),
    statistic: z.enum(DETECTION_STATISTICS),
    noiseLimits: z.array(z.enum(NOISE_LIMITS)),
    starProfile: StarProfileSchema.optional(),
  })
  .strict()
  .superRefine((detection, ctx) => {
    if (detection.statistic === 'whitened' && detection.starProfile === undefined) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['starProfile'],
        message: 'A detection scored by the whitened statistic holds the star profile it was built for.',
      });
    }
    if (!listsEachOnceInOrder(detection.noiseLimits)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['noiseLimits'],
        message: "A detection lists each noise limit at most once, in the contract's order.",
      });
    }
    const profile = detection.starProfile;
    if (profile !== undefined && profile.sigmaMinorPx > profile.sigmaMajorPx) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['starProfile', 'sigmaMinorPx'],
        message: "A star profile's narrower width is at most its wider one.",
      });
    }
  });
export type DetectionQuality = z.infer<typeof DetectionQualitySchema>;

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
    // The long form the repository ships ("Alpha Orionis"), only from the designation tables (./designation.ts)
    bayerDesignation: z.string().regex(new RegExp(BAYER_DESIGNATION_PATTERN)).optional(),
    // A whole number within the range the baked designations hold
    flamsteedNumber: wireInt().min(FLAMSTEED_NUMBER_MIN).max(FLAMSTEED_NUMBER_MAX).optional(),
    constellation: z.string().min(1),
    objectType: z.enum(['star', 'planet', 'moon', 'deep_sky', 'satellite_candidate']),
    coordinateEpoch: z.string().min(1),
    raDeg: z.number().min(0).max(360),
    decDeg: z.number().min(-90).max(90),
    apparentMagnitude: z.number().finite().optional(),
    colorIndexBpRp: z.number().finite().optional(),
    spectralType: z.string().optional(),
    properMotionRaMasYr: z.number().finite().optional(),
    properMotionDecMasYr: z.number().finite().optional(),
    radialVelocityKmS: z.number().finite().optional(),
    parallaxMas: z.number().finite().optional(),
    parallaxErrorMas: z.number().nonnegative().finite().optional(),
    bailerJonesDistancePc: z
      .object({
        lower: z.number().positive().finite(),
        median: z.number().positive().finite(),
        upper: z.number().positive().finite(),
        method: z.enum([
          'photogeometric_bailer_jones_edr3',
          'geometric_bailer_jones_edr3',
          'geometric_parallax',
          'ephemeris_au',
        ]),
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
  // The two residuals: on a catalog match, its detection's distance from its catalog star under the WCS, a held-out
  // residual only where verifiedByIndependentHoldout says so; on an observed finding, only in an approximate
  // solution, the pose fit's own residual, which checks nothing (ObservationSolutionSchema). Neither is agreement
  // unless that flag is true.
  angularResidualArcsec: z.number().nonnegative().finite().optional(),
  pixelResidualPx: z.number().nonnegative().finite().optional(),
  distanceUncertaintyNote: z.string().optional(),
  limitations: z.array(z.string()).min(1),
  verifiedByIndependentHoldout: z.boolean(),
});
export type ScientificEvidence = z.infer<typeof ScientificEvidenceSchema>;

export const SourceDetectionSchema = z.object({
  assetId: z.string().min(1),
  frameTimeSec: z.number().nonnegative().finite(),
  xPx: z.number().nonnegative().finite(),
  yPx: z.number().nonnegative().finite(),
  flux: z.number().positive().finite(),
  snr: z.number().positive().finite(),
  fwhmPx: z.number().nonnegative().finite(),
  centroidUncertaintyPx: z.number().positive().finite(),
});
export type SourceDetection = z.infer<typeof SourceDetectionSchema>;

export const SkyPositionSchema = z.object({
  raDeg: z.number().min(0).max(360),
  decDeg: z.number().min(-90).max(90),
  uncertaintyArcsec: z.number().nonnegative().finite(),
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
        lower: z.number().positive().finite(),
        median: z.number().positive().finite(),
        upper: z.number().positive().finite(),
        method: z.string().min(1),
      })
      .optional(),
    cartesianParsecICRS: z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]).optional(),
    evidence: ScientificEvidenceSchema,
  });
}

const NAMES_ITS_SOLUTION = 'Observed and catalog-matched findings belong to an ObservationSolution and name its id.';
const NAMES_ITS_RECORDING = 'Observed and catalog-matched evidence rests on a recording and names its SHA-256.';
const OBSERVED_IS_MEASURED = 'An observed finding is a measurement in the pixels and carries the source detection that measured it.';
const OBSERVED_NAMES_NO_ENTRY = 'An observed finding names no catalog entry: a finding matched to the catalog is catalog_matched.';
const OBSERVED_HAS_NO_DISTANCE = 'An observed finding holds no catalog distance: a point measured in the pixels has none.';
const A_MATCH_IS_OF_A_DETECTION = 'A catalog_matched finding carries the source detection that was matched to the catalog.';
const A_MATCH_NAMES_ITS_ENTRY = 'A catalog_matched finding carries the catalog entry it was matched to and names it in its evidence.';
const A_MATCH_HOLDS_ITS_RESIDUALS =
  "A catalog_matched finding's evidence holds the match's residual in pixels and in arcseconds: the class stands on a held-out residual check.";
const NOT_MEASURED = 'A predicted or simulated finding was not measured in the pixels, so it carries no source detection.';
const ONLY_A_MATCH_HAS_A_RESIDUAL = 'Only a catalog_matched finding holds a residual: a residual is a measured difference between a detection and a catalog star.';
const ONLY_A_MATCH_IS_HELD_OUT = 'Only a catalog_matched finding is verified by independent held-out stars.';
const OBSERVED_RESIDUAL_OF_A_POSE = "An observed finding holds a residual only in an approximate solution, where it is the pose fit's residual.";

/**
 * The fields of a finding that the class table places, each by its path in the finding. A finding "holds" a field
 * when the field is there, and holds `evidence.verifiedByIndependentHoldout` when it is true (the flag is always
 * there). The sky position is not among them: it is each class's own shape (`celestialFindingVariant`), required of
 * every class but observed.
 */
export const FINDING_FIELDS = [
  'observationSolutionId',
  'sourceDetection',
  'catalogRef',
  'inferredDistancePc',
  'cartesianParsecICRS',
  'evidence.sourceAssetSha256',
  'evidence.wcsSolutionId',
  'evidence.catalogReferenceId',
  'evidence.pixelResidualPx',
  'evidence.angularResidualArcsec',
  'evidence.distanceUncertaintyNote',
  'evidence.verifiedByIndependentHoldout',
] as const;
export type FindingField = (typeof FINDING_FIELDS)[number];

interface ClassEvidence {
  /** The sentences of AGENTS.md section 1, of this contract's field docs and of the lead's rulings the row rests on. */
  source: readonly string[];
  /** What a finding of the class must hold, each with the sentence its absence is refused in. */
  must: Readonly<Partial<Record<FindingField, string>>>;
  /** What it must not hold, each with the sentence its presence is refused in. */
  mustNot: Readonly<Partial<Record<FindingField, string>>>;
  /** What it may hold or leave out. Each field is in exactly one of the three. */
  may: readonly FindingField[];
}

/**
 * What a finding of each evidence class must hold, may hold and must not hold: the one table both validators read
 * (`services/contracts/astronomy.py` holds the same, and the shared fixture carries it,
 * tests/fixtures/observation_solution_contract.json), written from AGENTS.md section 1 and this contract's field
 * docs, and where those leave a cell unclear from the lead's rulings of 2026-11-12, which each row cites.
 */
export const FINDING_CLASS_EVIDENCE = {
  observed: {
    source: [
      'AGENTS.md section 1: "observed: Measured directly from original pixels (centroid, flux, SNR, frame/time)."',
      'CelestialFindingSchema, as this unit writes it (stricter than the doc on main, which asks the sourceDetection only of an observed finding without a verified WCS): "catalog_matched, predicted and simulated findings always carry a sky position. An observed finding is a pixel measurement: it carries one only when a verified WCS maps its pixels to the sky (checked by ObservationSolutionSchema), and always the sourceDetection that measured it."',
      'catalog_matched, the class that holds a catalog entry: "Associated with an external astronomical catalog (Gaia DR3, HIP, JPL) only after an independently verified WCS calibration and held-out residual check."',
      'ScientificEvidenceSchema, of sourceAssetSha256: "Absent only for a prediction made for a time and place with no recording behind it; observed and catalog-matched findings must name theirs (CelestialFindingSchema)." celestialFindingVariant, of observationSolutionId: "Absent only for a standalone prediction or simulation made for a time and place with no ObservationSolution; observed and catalog-matched findings, and every finding inside an ObservationSolution, name theirs."',
      "lead's ruling, 2026-11-12: an observed finding holds no catalog distance and no distance note (a point measured in the pixels has no distance), and is never verified by held-out stars (only a catalog match is).",
      "lead's ruling, 2026-11-12, the observed Moon: its residuals are may here and a rule of the solution: an observed finding holds one only in an approximate solution, the status of a pose estimate, where it is the pose fit's residual (services/science/horizon_solver.py:224-225); the pose record holds no fit residual and no solution names its beacon finding, so both belong in the pose record, an open gap for its unit.",
    ],
    must: {
      observationSolutionId: NAMES_ITS_SOLUTION,
      sourceDetection: OBSERVED_IS_MEASURED,
      'evidence.sourceAssetSha256': NAMES_ITS_RECORDING,
    },
    mustNot: {
      catalogRef: OBSERVED_NAMES_NO_ENTRY,
      inferredDistancePc: OBSERVED_HAS_NO_DISTANCE,
      cartesianParsecICRS: OBSERVED_HAS_NO_DISTANCE,
      'evidence.catalogReferenceId': OBSERVED_NAMES_NO_ENTRY,
      'evidence.distanceUncertaintyNote': OBSERVED_HAS_NO_DISTANCE,
      'evidence.verifiedByIndependentHoldout': ONLY_A_MATCH_IS_HELD_OUT,
    },
    may: ['evidence.wcsSolutionId', 'evidence.pixelResidualPx', 'evidence.angularResidualArcsec'],
  },
  catalog_matched: {
    source: [
      'AGENTS.md section 1: "catalog_matched: Associated with an external astronomical catalog (Gaia DR3, HIP, JPL) only after an independently verified WCS calibration and held-out residual check."',
      'ObservationSolutionSchema: "A catalog_matched finding needs a verified WCS: catalog association follows held-out verification."',
      'ScientificEvidenceSchema, of sourceAssetSha256: "Absent only for a prediction made for a time and place with no recording behind it; observed and catalog-matched findings must name theirs (CelestialFindingSchema)." celestialFindingVariant, of observationSolutionId: "Absent only for a standalone prediction or simulation made for a time and place with no ObservationSolution; observed and catalog-matched findings, and every finding inside an ObservationSolution, name theirs."',
      "lead's ruling, 2026-11-12: a catalog match holds both residuals, in pixels and in arcseconds: the class exists only after a held-out residual check, and a per-finding claim needs its own numbers.",
    ],
    must: {
      observationSolutionId: NAMES_ITS_SOLUTION,
      sourceDetection: A_MATCH_IS_OF_A_DETECTION,
      catalogRef: A_MATCH_NAMES_ITS_ENTRY,
      'evidence.sourceAssetSha256': NAMES_ITS_RECORDING,
      'evidence.catalogReferenceId': A_MATCH_NAMES_ITS_ENTRY,
      'evidence.pixelResidualPx': A_MATCH_HOLDS_ITS_RESIDUALS,
      'evidence.angularResidualArcsec': A_MATCH_HOLDS_ITS_RESIDUALS,
    },
    mustNot: {},
    may: [
      'inferredDistancePc',
      'cartesianParsecICRS',
      'evidence.wcsSolutionId',
      'evidence.distanceUncertaintyNote',
      'evidence.verifiedByIndependentHoldout',
    ],
  },
  predicted: {
    source: [
      'AGENTS.md section 1: "predicted: Computed from ephemeris/catalog + time/location/pose assumptions; never rendered as if recovered from camera pixels."',
      'CelestialFindingSchema: "A predicted or simulated finding was not measured in the pixels, so it carries no sourceDetection."',
      'ObservationSolutionSchema: a solution with a predicted finding needs observedAtUTC and observerLocation.',
      "lead's ruling, 2026-11-12: a prediction holds no residual (a residual is a measured difference between a detection and a catalog star) and is never verified by held-out stars.",
      "lead's ruling, 2026-11-12: what a prediction was computed from (a catalog entry, or the ephemeris with its edition) has no structured field yet, so its catalog entry and a distance with no entry stay may: an open gap for a later unit.",
    ],
    must: {},
    mustNot: {
      sourceDetection: NOT_MEASURED,
      'evidence.pixelResidualPx': ONLY_A_MATCH_HAS_A_RESIDUAL,
      'evidence.angularResidualArcsec': ONLY_A_MATCH_HAS_A_RESIDUAL,
      'evidence.verifiedByIndependentHoldout': ONLY_A_MATCH_IS_HELD_OUT,
    },
    may: [
      'observationSolutionId',
      'catalogRef',
      'inferredDistancePc',
      'cartesianParsecICRS',
      'evidence.sourceAssetSha256',
      'evidence.wcsSolutionId',
      'evidence.catalogReferenceId',
      'evidence.distanceUncertaintyNote',
    ],
  },
  simulated: {
    source: [
      'AGENTS.md section 1: "simulated: Catalog-geometry 3D navigation, modeled bodies and scenes, alternative viewpoints, or historical epoch models; must display explicit disclosure that the layer or view is drawn or computed by the app and not measured in the source recording, with each layer naming its own source and limits."',
      'CelestialFindingSchema: "A predicted or simulated finding was not measured in the pixels, so it carries no sourceDetection."',
      "lead's ruling, 2026-11-12: a simulated finding needs no new field (the disclosure a person reads is the screen's fixed sentence); it holds no detection, no residual and no held-out verification, and the limitation every class holds.",
    ],
    must: {},
    mustNot: {
      sourceDetection: NOT_MEASURED,
      'evidence.pixelResidualPx': ONLY_A_MATCH_HAS_A_RESIDUAL,
      'evidence.angularResidualArcsec': ONLY_A_MATCH_HAS_A_RESIDUAL,
      'evidence.verifiedByIndependentHoldout': ONLY_A_MATCH_IS_HELD_OUT,
    },
    may: [
      'observationSolutionId',
      'catalogRef',
      'inferredDistancePc',
      'cartesianParsecICRS',
      'evidence.sourceAssetSha256',
      'evidence.wcsSolutionId',
      'evidence.catalogReferenceId',
      'evidence.distanceUncertaintyNote',
    ],
  },
} as const satisfies Record<EvidenceClass, ClassEvidence>;

/**
 * The rules between a finding's fields that hold for every class, with their sentences (the same on both sides). A
 * catalog reference id names the entry the finding carries: one that names another entry, or names one where the
 * finding carries none, is refused on every class (lead's ruling, 2026-11-12: an id that names an entry the finding
 * does not carry points at nothing).
 */
export const FINDING_EVIDENCE_RULES = {
  ownClass: "A finding's class is the class its evidence names.",
  sameEntry: "A finding's catalog reference id names the catalog entry the finding carries.",
} as const;


export interface FindingIssue {
  path: string[];
  message: string;
}

function fieldAt(finding: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((value, key) => (value as Record<string, unknown> | undefined)?.[key], finding);
}

/** Whether a finding holds a field of FINDING_FIELDS: it is there, and for the held-out flag, it is true. */
function holds(finding: unknown, path: string): boolean {
  const value = fieldAt(finding, path);
  return path === 'evidence.verifiedByIndependentHoldout' ? value === true : value !== undefined;
}

/**
 * What a finding claims and has no evidence for, by `table` (FINDING_CLASS_EVIDENCE, or another table a test makes):
 * each `must` field it does not hold, each `mustNot` field it holds, its evidence naming another class, and a catalog
 * reference id that names another entry than the one it carries, or names one where it carries none. In this order
 * on both sides.
 */
export function findingClassIssues(
  table: Record<EvidenceClass, Pick<ClassEvidence, 'must' | 'mustNot'>>,
  finding: { evidenceClass: EvidenceClass; evidence: { evidenceClass: unknown } }
): FindingIssue[] {
  const own = table[finding.evidenceClass];
  const issues: FindingIssue[] = [];
  for (const [path, message] of Object.entries(own.must) as [string, string][]) {
    if (!holds(finding, path)) issues.push({ path: path.split('.'), message });
  }
  for (const [path, message] of Object.entries(own.mustNot) as [string, string][]) {
    if (holds(finding, path)) issues.push({ path: path.split('.'), message });
  }
  if (finding.evidence.evidenceClass !== finding.evidenceClass) {
    issues.push({ path: ['evidence', 'evidenceClass'], message: FINDING_EVIDENCE_RULES.ownClass });
  }
  const named = fieldAt(finding, 'evidence.catalogReferenceId');
  if (named !== undefined && named !== fieldAt(finding, 'catalogRef.id')) {
    issues.push({ path: ['evidence', 'catalogReferenceId'], message: FINDING_EVIDENCE_RULES.sameEntry });
  }
  return issues;
}

/**
 * `catalog_matched`, `predicted` and `simulated` findings always carry a sky position.
 * An `observed` finding is a pixel measurement: it carries one only when a verified WCS
 * maps its pixels to the sky (checked by `ObservationSolutionSchema`), and always the
 * `sourceDetection` that measured it. A `predicted` or `simulated` finding was not measured in the
 * pixels, so it carries no `sourceDetection`. What else each class must, may and must not hold is
 * `FINDING_CLASS_EVIDENCE`.
 */
export const CelestialFindingSchema = z
  .discriminatedUnion('evidenceClass', [
    celestialFindingVariant('observed', SkyPositionSchema.optional()),
    celestialFindingVariant('catalog_matched', SkyPositionSchema),
    celestialFindingVariant('predicted', SkyPositionSchema),
    celestialFindingVariant('simulated', SkyPositionSchema),
  ])
  .superRefine((finding, ctx) => {
    for (const { path, message } of findingClassIssues(FINDING_CLASS_EVIDENCE, finding)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    }
  });
export type CelestialFinding = z.infer<typeof CelestialFindingSchema>;

export const WCSCalibrationModelSchema = z.object({
  projection: z.literal('TAN'),
  crvalRaDeg: z.number().min(0).max(360),
  crvalDecDeg: z.number().min(-90).max(90),
  crpixX: z.number().finite(),
  crpixY: z.number().finite(),
  scaleDegPerPx: z.number().positive().finite(),
  rotationDeg: z.number().finite(),
  radialDistortionK1: z.number().min(-0.25).max(0.25),
  radialDistortionK2: z.number().min(-0.25).max(0.25),
  parity: z.union([z.literal(1), z.literal(-1)]),
  fovHorizontalDeg: z.number().positive().max(175),
  fovVerticalDeg: z.number().positive().max(175),
});
export type WCSCalibrationModel = z.infer<typeof WCSCalibrationModelSchema>;

// The beacon each pose method lines up from: the Moon for the two Moon methods, the Sun for sun_horizon
/** A pose from the phone's pointing record names no beacon (ios-capture-companion design component 6). */
export const DEVICE_ATTITUDE = 'device_attitude';
const POSE_BEACON_TERMS =
  "A pose estimate lined up from a beacon names the beacon and its centroid's uncertainty; one from the device attitude names neither.";
const POSE_METHOD_BEACONS = {
  moon_camera_profile_level_head: 'moon',
  moon_horizon: 'moon',
  sun_horizon: 'sun',
} as const;

/**
 * An approximate pointing and its uncertainty, lined up from a beacon (the Moon or the Sun) and stated assumptions,
 * never checked against stars: the frame centre's altitude and azimuth, the roll and the diagonal field of view of
 * the solution's nominal WCS, where the field of view comes from, and the WCS swept over the assumptions' ranges.
 * The method names its beacon, and the two uncertainties are finite. `ObservationSolutionSchema` lets it ride only
 * on an approximate solution with a WCS, every swept WCS on that WCS's crpix and of its image size. Its Python
 * mirror is `PoseEstimate` in services/science/wcs_models.py.
 */
export const PoseEstimateSchema = z
  .object({
    method: z.enum(['moon_camera_profile_level_head', 'moon_horizon', 'sun_horizon', DEVICE_ATTITUDE]),
    // Present exactly for the methods that line up from a beacon, as is the beacon's uncertainty below
    beacon: z.enum(['moon', 'sun']).optional(),
    centerAltDeg: z.number().min(-90).max(90),
    centerAzDeg: z.number().min(0).max(360),
    rollDeg: z.number().min(-180).max(180),
    fovDiagonalDeg: z.number().positive().max(175),
    fovSource: z.string().min(1),
    assumptions: z.array(z.string().min(1)).min(1),
    sweptWcs: z.array(WCSCalibrationModelSchema).min(2).max(27),
    maxCenterOffsetDeg: z.number().nonnegative().finite(),
    // The beacon term, one number: the beacon centroid's own uncertainty (for the head-worn Moon pose, the Moon's
    // predicted radius plus its ephemeris's stated uncertainty) plus the frame term, at most what drawing every
    // position through the pose's one rotation leaves out
    beaconCentroidUncertaintyDeg: z.number().nonnegative().finite().optional(),
  })
  .superRefine((pose, ctx) => {
    const linesUpFrom = pose.method === DEVICE_ATTITUDE ? undefined : POSE_METHOD_BEACONS[pose.method];
    (['beacon', 'beaconCentroidUncertaintyDeg'] as const).forEach((key) => {
      if ((pose[key] === undefined) !== (linesUpFrom === undefined)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: [key], message: POSE_BEACON_TERMS });
      }
    });
    if (linesUpFrom !== undefined && pose.beacon !== undefined && pose.beacon !== linesUpFrom) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['beacon'],
        message: "A pose estimate's beacon is the one its method lines up from: the moon for moon_camera_profile_level_head and moon_horizon, the sun for sun_horizon.",
      });
    }
  });
export type PoseEstimate = z.infer<typeof PoseEstimateSchema>;

/** How a solve used the phone's pointing record, in this order, and where a solution's time and place came from. */
export const POINTING_USES = ['search_hint', 'pose_estimate', 'moment'] as const;
export const MOMENT_SOURCES = ['file', 'record'] as const;
/** Where a pointing hint came from: the phone's pointing record, or a person's alignment of the predicted sky (align-1b). */
export const POINTING_HINT_SOURCES = ['record', 'person'] as const;
const HINT_RECORDING = "A pointing hint names the recording the solution solved: its sourceAssetSha256 is the solution's sourceSha256.";
const HINT_POSE =
  "A pointing hint is used as the pose estimate exactly when the solution's pose estimate is lined up from the device attitude.";
const HINT_MOMENT_USE = "A pointing hint says moment exactly when the solution's time or its place came from the record.";
const HINT_OFFSET =
  "A pointing hint gives the verified centre's offset from its hinted centre exactly on a calibrated solution whose hint gives one.";
const HINT_PERSON_POSE = 'A pointing hint gives the roll and the field of view exactly when a person aligned it.';
const HINT_PERSON_USE =
  "A person's alignment is used only as a search hint and lends no time or place of its own: it lists moment exactly when the photo's pointing record lent the time or the place.";
/**
 * What a person's alignment is used as: a search hint, and with it the moment exactly when the photo's pointing record
 * lent the time or the place of the sky the person lined up (lentalign-0).
 */
const PERSON_POINTING_USES = 'search_hint';
const PERSON_POINTING_USES_LENT = 'search_hint,moment';
const HINT_MOMENT =
  'A pointing hint is lined up in the sky of a time and a place, so a solution with one has observedAtUTC and observerLocation.';

/**
 * How a solve used the phone's pointing record (ios-capture-companion design component 6): the record's photo, what
 * it was used as (a search hint, the pose, and the moment: its time or place standing in for the file's), the hinted frame centre in altitude and azimuth with its stated uncertainty, the verified centre's
 * offset from it on a calibrated solution (REQ-IOS-2.2), and where the solution's time and place came from: the
 * file's own metadata or the record. Never a measurement of the sky. `ObservationSolutionSchema` ties it to the
 * solution's photo, status, pose and moment. Its Python mirror is `PointingHint` in services/science/wcs_models.py.
 */
export const PointingHintSchema = z
  .object({
    // The phone's pointing record or a person's alignment of the predicted sky (align-1b)
    source: z.enum(POINTING_HINT_SOURCES),
    sourceAssetSha256: SourceSha256Schema,
    usedAs: z.array(z.enum(POINTING_USES)).min(1).max(POINTING_USES.length),
    // Present exactly when the hint was used as a search hint or the pose: only a heading gives a centre
    centerAltDeg: z.number().min(-90).max(90).optional(),
    centerAzDeg: z.number().min(0).max(360).optional(),
    uncertaintyDeg: z.number().positive().max(180).optional(),
    verifiedCenterOffsetDeg: z.number().min(0).max(180).optional(),
    // Present exactly when a person aligned the hint: the roll and the diagonal field of view they left the sky at
    rollDeg: z.number().min(-180).max(180).optional(),
    fovDiagonalDeg: z.number().positive().max(175).optional(),
    timeFrom: z.enum(MOMENT_SOURCES),
    placeFrom: z.enum(MOMENT_SOURCES),
  })
  .superRefine((hint, ctx) => {
    const centred = hint.usedAs.includes('search_hint') || hint.usedAs.includes('pose_estimate');
    (['centerAltDeg', 'centerAzDeg', 'uncertaintyDeg'] as const).forEach((key) => {
      if ((hint[key] !== undefined) !== centred) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: [key],
          message: 'A pointing hint gives the hinted centre and its uncertainty exactly when it was used as a search hint or the pose.',
        });
      }
    });
    const person = hint.source === 'person';
    (['rollDeg', 'fovDiagonalDeg'] as const).forEach((key) => {
      if ((hint[key] !== undefined) !== person) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: [key], message: HINT_PERSON_POSE });
      }
    });
    const lent = hint.timeFrom === 'record' || hint.placeFrom === 'record';
    if (person && hint.usedAs.join() !== (lent ? PERSON_POINTING_USES_LENT : PERSON_POINTING_USES)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['usedAs'], message: HINT_PERSON_USE });
    }
    if (!listsEachOnceInOrder(hint.usedAs, POINTING_USES)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['usedAs'],
        message: 'A pointing hint lists each use at most once, in the order search_hint, pose_estimate, moment.',
      });
    }
  });
export type PointingHint = z.infer<typeof PointingHintSchema>;

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

/**
 * Whether no single image size gives both WCS their fields of view. A WCS here carries no width or height, so this
 * is all the contract can tell: one image at one scale has one field of view, and a larger scale never a smaller
 * one. A swept WCS of another size whose fields of view move the same way as its scale is not caught here; the
 * Python dataclass that writes solutions holds both sizes and compares them (services/science/wcs_models.py).
 */
function isOfAnotherImageSize(swept: WCSCalibrationModel, nominal: WCSCalibrationModel): boolean {
  return (['fovHorizontalDeg', 'fovVerticalDeg'] as const).some((axis) => {
    const widerBy = swept[axis] - nominal[axis];
    if (swept.scaleDegPerPx === nominal.scaleDegPerPx) return Math.abs(widerBy) > SWEPT_FOV_TOLERANCE_DEG;
    if (swept.scaleDegPerPx > nominal.scaleDegPerPx) return widerBy < -SWEPT_FOV_TOLERANCE_DEG;
    return widerBy > SWEPT_FOV_TOLERANCE_DEG;
  });
}

const ZOD_UTC_DATETIME = z.string().datetime();
const isRealDayWhenDatetime = (value: string): boolean => !ZOD_UTC_DATETIME.safeParse(value).success || isRealCalendarDay(value);

export const ObservationSolutionSchema = z
  .object({
    id: z.string().min(1),
    sourceAssetId: z.string().min(1),
    // Absent only for a pending solution with no findings: nothing has been solved, so no recording is named
    sourceSha256: SourceSha256Schema.optional(),
    frameTimeSec: z.number().nonnegative().finite(),
    revision: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
    status: SolutionStatusSchema,
    // Present when the recording's capture time is known; required whenever a pose or finding is predicted from time and place
    // Zod's datetime takes year 0000, which is no year to Python's dates: the wire module's real day holds it to 1 to 9999
    observedAtUTC: ZOD_UTC_DATETIME
      .refine(isRealDayWhenDatetime, { message: 'A UTC instant falls on a real calendar day, in a year from 1 to 9999.' })
      .optional(),
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
      detectedCandidateCount: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
      matchedInlierCount: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
      // Held-out detections paired with a catalog star under the fitted WCS (within the association radius), and
      // those of them inside the per-star inlier gate
      heldOutPairCount: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
      heldOutInliers: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
      // Held-out residuals, over every held-out pair: present exactly when there is one, never a stand-in number
      medianResidualPx: z.number().nonnegative().finite().optional(),
      p95ResidualPx: z.number().nonnegative().finite().optional(),
      medianResidualArcsec: z.number().nonnegative().finite().optional(),
      // True only at or above the held-out gate: MIN_HELD_OUT_INLIERS inliers, MAX_HELD_OUT_MEDIAN_RESIDUAL_PX median
      passedIndependentVerification: z.boolean(),
      // What the detector measured on the frame's own pixels: absent when the detector did not run, or on a solution
      // made before the field existed
      detection: DetectionQualitySchema.optional(),
    }).optional(),
    findings: z.array(CelestialFindingSchema),
    warnings: z.array(z.string()),
    // How far an approximate solution's WCS may be off: present only on an approximate solution with a WCS
    poseEstimate: PoseEstimateSchema.optional(),
    // How the solve used the phone's pointing record, when it had one
    pointingHint: PointingHintSchema.optional(),
  })
  .superRefine((solution, ctx) => {
    const quality = solution.quality;
    const pose = solution.poseEstimate;
    // REQ-SKY-5.4: an approximate solution says how far its WCS may be off
    if (pose === undefined && solution.status === 'approximate') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['poseEstimate'],
        message: 'An approximate solution states how far its WCS may be off, so it carries a pose estimate.',
      });
    }
    // The pointing hint's rules, each named where it is: at a field a rule needs when that field is missing
    const hint = solution.pointingHint;
    const devicePose = pose?.method === DEVICE_ATTITUDE;
    const flag = (path: (string | number)[], message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    if (hint === undefined) {
      if (devicePose) flag(['pointingHint'], HINT_POSE);
    } else {
      if (solution.sourceSha256 === undefined) flag(['sourceSha256'], HINT_RECORDING);
      else if (hint.sourceAssetSha256 !== solution.sourceSha256) flag(['pointingHint', 'sourceAssetSha256'], HINT_RECORDING);
      const asPose = hint.usedAs.includes('pose_estimate');
      if (asPose && pose === undefined) flag(['poseEstimate'], HINT_POSE);
      else if (asPose !== devicePose) flag(['pointingHint', 'usedAs'], HINT_POSE);
      if (hint.usedAs.includes('moment') !== (hint.timeFrom === 'record' || hint.placeFrom === 'record')) {
        flag(['pointingHint', 'usedAs'], HINT_MOMENT_USE);
      }
      const centred = hint.usedAs.includes('search_hint') || hint.usedAs.includes('pose_estimate');
      if ((hint.verifiedCenterOffsetDeg !== undefined) !== (solution.status === 'calibrated' && centred)) {
        flag(['pointingHint', 'verifiedCenterOffsetDeg'], HINT_OFFSET);
      }
      if (solution.observedAtUTC === undefined) flag(['observedAtUTC'], HINT_MOMENT);
      if (solution.observerLocation === undefined) flag(['observerLocation'], HINT_MOMENT);
    }
    if (pose !== undefined) {
      if (solution.status !== 'approximate' || solution.wcs === undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['poseEstimate'],
          message: 'A pose estimate belongs only to an approximate solution with a WCS: it states how far that WCS may be off.',
        });
      }
      const nominal = solution.wcs;
      if (nominal !== undefined) {
        pose.sweptWcs.forEach((swept, index) => {
          if (swept.crpixX !== nominal.crpixX || swept.crpixY !== nominal.crpixY) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['poseEstimate', 'sweptWcs', index],
              message: "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: all of them describe the same frame.",
            });
          }
          if (isOfAnotherImageSize(swept, nominal)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['poseEstimate', 'sweptWcs', index],
              message:
                "Every swept WCS of a pose estimate has the nominal WCS's image size: at its scale the same fields of view, at a larger scale none smaller, at a smaller scale none larger.",
            });
          }
        });
      }
    }
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
      // The flag stands for the held-out gate: it is never true below the gate's own numbers
      if (
        quality.passedIndependentVerification &&
        (quality.heldOutInliers < MIN_HELD_OUT_INLIERS ||
          (quality.medianResidualPx !== undefined && quality.medianResidualPx > MAX_HELD_OUT_MEDIAN_RESIDUAL_PX))
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['quality', 'passedIndependentVerification'],
          message: `Held-out verification passes only with at least ${MIN_HELD_OUT_INLIERS} held-out inliers and a median held-out residual of at most ${MAX_HELD_OUT_MEDIAN_RESIDUAL_PX} px.`,
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
      // The lead's ruling of 2026-11-12 on the observed Moon: in an approximate solution, the status a pose estimate
      // has, an observed finding may hold the pose fit's residual (services/science/horizon_solver.py writes the
      // beacon's); in any other, an observed finding holds none. The fit residual and the beacon's identity belong in
      // the pose record: an open gap for that record's unit
      if (finding.evidenceClass === 'observed' && solution.status !== 'approximate') {
        for (const field of ['pixelResidualPx', 'angularResidualArcsec'] as const) {
          if (finding.evidence[field] !== undefined) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['findings', index, 'evidence', field], message: OBSERVED_RESIDUAL_OF_A_POSE });
          }
        }
      }
    });
  });
export type ObservationSolution = z.infer<typeof ObservationSolutionSchema>;
