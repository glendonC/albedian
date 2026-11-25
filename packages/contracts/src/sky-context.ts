import { z } from 'zod';
import { wireNumber, wireUtcInstant } from './wire.js';

/**
 * The sky for a time and place: what `POST /api/science/sky-context` takes and answers. Mirrored by
 * services/contracts/sky_context.py; both are judged by tests/fixtures/sky_context_contract.json, which
 * tests/integration/test_sky_context_contract.py writes and the iPhone app's reader is tested against too.
 *
 * Everything in the reply is `predicted`: computed from catalog rows and an ephemeris for the time and place sent,
 * never measured in any image. The positions follow one convention, stated once in the reply (`convention`):
 * topocentric from the place at sea level, azimuth from true north through east, altitude apparent and airless (no
 * refraction applied), as services/science/catalog.py `icrs_to_altaz` computes them.
 *
 * The request travels in the body, never in the URL. `magLimit` keeps only the stars whose `mag` is at most it (a body
 * has no magnitude in this reply and is kept); `aboveHorizonOnly` keeps only the stars and bodies whose altitude is 0
 * or more, and then only the edges whose two stars are kept. The reply states both as `filters`, so a reader knows
 * what was left out.
 *
 * The Sun (`sun`, skycontext-3) is its own block, never one of `ephemerides`, and no filter leaves it out: below the
 * horizon is when it says how dark the sky is. A reply made before it was added holds none, so a reader takes its
 * absence as no Sun to tell of.
 *
 * Strict on both sides by the wire rules: unknown keys and Python field names are refused at every level.
 */
export const SKY_CONTEXT_FORMAT = 'albedian.sky-context';
export const SKY_CONTEXT_FORMAT_VERSION = 1;
/** The ephemeris's span: from the first instant of 1800 up to, not including, the first of 2051. */
export const SKY_CONTEXT_EPHEMERIS_FROM = '1800-01-01T00:00:00Z';
export const SKY_CONTEXT_EPHEMERIS_UNTIL = '2051-01-01T00:00:00Z';
/** [proposed] A magnitude, sent or held, lies in -30 to 30: the Sun is -26.7, and no star of these catalogs is near 30. */
export const SKY_CONTEXT_MAG_MIN = -30;
export const SKY_CONTEXT_MAG_MAX = 30;
/** [proposed] A name or an id is at most 64 UTF-16 units; a sentence (a method, a limitation, a citation) at most 1024. */
export const SKY_CONTEXT_NAME_MAX_UTF16 = 64;
export const SKY_CONTEXT_TEXT_MAX_UTF16 = 1024;
/** [proposed] At most this many of each: the route sends 71 stars, 6 bodies and 72 edges today. */
export const SKY_CONTEXT_STARS_MAX = 512;
export const SKY_CONTEXT_BODIES_MAX = 16;
export const SKY_CONTEXT_EDGES_MAX = 1024;
export const SKY_CONTEXT_NOTES_MAX = 16;
/** [proposed] A proper motion at most 20,000 mas/yr in size, a radial velocity at most 2,000 km/s, BP-RP from -2 to 10. */
export const SKY_CONTEXT_PROPER_MOTION_MAX = 20_000;
export const SKY_CONTEXT_RADIAL_VELOCITY_MAX = 2_000;
export const SKY_CONTEXT_BP_RP_MIN = -2;
export const SKY_CONTEXT_BP_RP_MAX = 10;

export const SKY_CONTEXT_CONVENTION = {
  frame: 'topocentric_sea_level',
  azimuth: 'true_north_through_east',
  altitude: 'apparent_airless',
} as const;
export const SKY_CONTEXT_PROVIDERS = { Hipparcos: 'Hipparcos 2007', 'Gaia DR3': 'Gaia DR3' } as const;
export const SKY_CONTEXT_SOURCE_ID_PATTERNS = {
  Hipparcos: '^HIP [1-9][0-9]{0,5}$',
  'Gaia DR3': '^Gaia DR3 [1-9][0-9]{0,19}$',
} as const;
export const SKY_CONTEXT_STAR_DISTANCE_METHODS = [
  'geometric_parallax',
  'photogeometric_bailer_jones_edr3',
  'geometric_bailer_jones_edr3',
] as const;
export const SKY_CONTEXT_INTERVALS = ['16th to 84th percentile', 'span of the two percentile intervals'] as const;
export const SKY_CONTEXT_MOON_PHASES = [
  'New Moon',
  'Waxing Crescent',
  'First Quarter',
  'Waxing Gibbous',
  'Full Moon',
  'Waning Gibbous',
  'Last Quarter',
  'Waning Crescent',
] as const;
export const SKY_CONTEXT_ID_PATTERN = '^[a-z0-9]+(?:-[a-z0-9]+)*$';
/** A body's name: one capital and lower-case ASCII letters (Moon, Jupiter), so its lower case is the same everywhere. */
export const SKY_CONTEXT_BODY_NAME_PATTERN = '^[A-Z][a-z]{1,31}$';
/**
 * The Sun's USNO bands of its centre's altitude, brightest first, as services/science/predicted_sky.py `sky_phase`
 * names them, and where each begins: day above sunrise and sunset's -50 arcminutes, each twilight down to and including
 * its floor, night below the last.
 */
export const SKY_CONTEXT_SUN_BANDS = ['day', 'civil_twilight', 'nautical_twilight', 'astronomical_twilight', 'night'] as const;
export type SkyContextSunBand = (typeof SKY_CONTEXT_SUN_BANDS)[number];
export const SKY_CONTEXT_SUN_BAND_EDGES = {
  dayAboveDeg: -50 / 60,
  civilTwilightFromDeg: -6,
  nauticalTwilightFromDeg: -12,
  astronomicalTwilightFromDeg: -18,
} as const;

/** The contract's own rules, worded the same on both sides. */
export const SKY_CONTEXT_RULES = {
  insideEphemerisSpan: 'The moment lies from 1800 to 2050, the only years the sky can be worked out for.',
  aboveHorizonIsAltitude: 'aboveHorizon is true exactly when altDeg is 0 or more.',
  aboveHorizonOnlyHeld: 'With aboveHorizonOnly, every star and body has an altDeg of 0 or more.',
  magLimitHeld: "With a magLimit, every star's mag is at most the magLimit.",
  idsOnce: "Each star's id and each body's id is held once.",
  edgeJoinsHeldStars: 'Each constellation edge joins two different stars the reply holds.',
  releaseOfProvider: "A star's releaseVersion is its provider's: Hipparcos 2007 for Hipparcos, Gaia DR3 for Gaia DR3.",
  sourceIdOfProvider: "A star's catalogSourceId is in its provider's form: HIP and a number, or Gaia DR3 and a number.",
  distanceInOrder: "A distance's lower is at most its median, and its median at most its upper.",
  lightTravelOfDistance: "lightTravel.distancePc is the item's own median distance.",
  astrophysicsOfGaia: 'Only a Gaia DR3 star has astrophysics, under its own catalogSourceId.',
  astrophysicsHoldsAValue: 'Astrophysics holds a temperature or a luminosity.',
  estimateInterval:
    "An estimate's lower, upper and interval come together, with lower at most its value and its value at most upper.",
  radiusFromBoth: 'A radius is given only with the temperature and the luminosity it is derived from.',
  positionIsSkyPosition: "A body's raDeg, decDeg, altDeg and azDeg are its skyPosition's.",
  distanceIsInferred: "A body's distancePc is its inferredDistancePc median.",
  idsOfName: "A body's id and its evidence's id are made from its name.",
  titleOrder: "A star's title is its commonName where that is not its catalogSourceId, else its bayerDesignation where it has one.",
  sunBandOfAltitude:
    "The Sun's band is the USNO band of its altDeg: day above -50 arcminutes, civil twilight down to -6 degrees, nautical down to -12, astronomical down to -18, night below.",
} as const;
type Rule = keyof typeof SKY_CONTEXT_RULES;

// The fixed ranges, never a runtime's Unicode data: U+D800 to U+DFFF unpaired, and C0, DEL and C1
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
/** Text of 1 to `max` UTF-16 units with no lone surrogate and no control character. */
const boundedText = (max: number) =>
  z
    .string()
    .min(1)
    .max(max)
    .refine((s) => !LONE_SURROGATE.test(s), 'A text holds no lone surrogate.')
    .refine((s) => !CONTROL.test(s), 'A text holds no control character.');
const name = () => boundedText(SKY_CONTEXT_NAME_MAX_UTF16);
const text = () => boundedText(SKY_CONTEXT_TEXT_MAX_UTF16);
const id = () =>
  z
    .string()
    .min(1)
    .max(SKY_CONTEXT_NAME_MAX_UTF16)
    .regex(new RegExp(SKY_CONTEXT_ID_PATTERN));
const notes = () => z.array(text()).min(1).max(SKY_CONTEXT_NOTES_MAX);
const latitude = () => wireNumber().min(-90).max(90);
const longitude = () => wireNumber().min(-180).max(180);
const angle360 = () => wireNumber().min(0).max(360);
const magnitude = () => wireNumber().min(SKY_CONTEXT_MAG_MIN).max(SKY_CONTEXT_MAG_MAX);
const positive = () => wireNumber().gt(0);
const nonNegative = () => wireNumber().min(0);
const cartesian = () => z.array(wireNumber()).length(3);
const properMotion = () => wireNumber().min(-SKY_CONTEXT_PROPER_MOTION_MAX).max(SKY_CONTEXT_PROPER_MOTION_MAX);

function broken(ctx: z.RefinementCtx, rule: Rule): void {
  ctx.addIssue({ code: z.ZodIssueCode.custom, message: SKY_CONTEXT_RULES[rule] });
}

/** The USNO band of the Sun's centre altitude, by SKY_CONTEXT_SUN_BAND_EDGES. */
export function sunBandOf(altDeg: number): SkyContextSunBand {
  const edges = SKY_CONTEXT_SUN_BAND_EDGES;
  if (altDeg > edges.dayAboveDeg) return 'day';
  if (altDeg >= edges.civilTwilightFromDeg) return 'civil_twilight';
  if (altDeg >= edges.nauticalTwilightFromDeg) return 'nautical_twilight';
  if (altDeg >= edges.astronomicalTwilightFromDeg) return 'astronomical_twilight';
  return 'night';
}

/** Whether a wire UTC instant lies in the ephemeris's span: the form is fixed width, so text order is time order. */
function inSpan(moment: string): boolean {
  const second = `${moment.slice(0, 19)}Z`;
  return SKY_CONTEXT_EPHEMERIS_FROM <= second && second < SKY_CONTEXT_EPHEMERIS_UNTIL;
}

export const SkyContextRequestSchema = z
  .object({
    observedAtUTC: wireUtcInstant(),
    latDeg: latitude(),
    lonDeg: longitude(),
    magLimit: magnitude().optional(),
    aboveHorizonOnly: z.boolean().optional(),
  })
  .strict()
  .superRefine((request, ctx) => {
    if (!inSpan(request.observedAtUTC)) broken(ctx, 'insideEphemerisSpan');
  });
export type SkyContextRequest = z.infer<typeof SkyContextRequestSchema>;

export const SkyContextConventionSchema = z
  .object({
    frame: z.literal(SKY_CONTEXT_CONVENTION.frame),
    azimuth: z.literal(SKY_CONTEXT_CONVENTION.azimuth),
    altitude: z.literal(SKY_CONTEXT_CONVENTION.altitude),
  })
  .strict();

export const SkyContextFiltersSchema = z
  .object({ magLimit: magnitude().optional(), aboveHorizonOnly: z.boolean() })
  .strict();

const distanceInterval = <M extends z.ZodTypeAny>(method: M) =>
  z
    .object({ lower: positive(), median: positive(), upper: positive(), method })
    .strict()
    .superRefine((d, ctx) => {
      if (!(d.lower <= d.median && d.median <= d.upper)) broken(ctx, 'distanceInOrder');
    });

export const LightTravelSchema = z
  .object({
    distancePc: positive(),
    lightYears: positive(),
    lightMinutes: positive(),
    lightSeconds: positive(),
    description: name(),
    formatted: name(),
  })
  .strict();

const estimateShape = {
  value: positive(),
  method: text(),
  lower: positive().optional(),
  upper: positive().optional(),
  interval: z.enum(SKY_CONTEXT_INTERVALS).optional(),
};
type EstimateShape = { value: number; lower?: number; upper?: number; interval?: string };
function checkEstimate(e: EstimateShape, ctx: z.RefinementCtx): void {
  const held = [e.lower !== undefined, e.upper !== undefined, e.interval !== undefined];
  if (held.some(Boolean) && !held.every(Boolean)) broken(ctx, 'estimateInterval');
  else if (held.every(Boolean) && !(e.lower! <= e.value && e.value <= e.upper!)) broken(ctx, 'estimateInterval');
}
export const EstimateSchema = z.object(estimateShape).strict().superRefine(checkEstimate);
export const DerivedEstimateSchema = z
  .object({ ...estimateShape, derived: z.literal(true) })
  .strict()
  .superRefine(checkEstimate);

export const AstrophysicsSchema = z
  .object({
    catalogSourceId: name(),
    limitations: notes(),
    citations: notes(),
    teffK: EstimateSchema.optional(),
    luminositySolar: EstimateSchema.optional(),
    radiusSolar: DerivedEstimateSchema.optional(),
  })
  .strict()
  .superRefine((a, ctx) => {
    if (a.teffK === undefined && a.luminositySolar === undefined) broken(ctx, 'astrophysicsHoldsAValue');
    else if (a.radiusSolar !== undefined && (a.teffK === undefined || a.luminositySolar === undefined))
      broken(ctx, 'radiusFromBoth');
  });

export const SkyContextStarSchema = z
  .object({
    id: id(),
    evidenceClass: z.literal('predicted'),
    commonName: name(),
    // The title a card shows, by the title order (designations.star_title): the IAU name, else the Bayer designation,
    // else the Flamsteed designation, else the catalog id (sky-context-title-zero)
    title: name(),
    // Absent for a star the baked designations do not give one (catalog-1-v2); never null
    bayerDesignation: name().optional(),
    constellation: name(),
    provider: z.enum(['Hipparcos', 'Gaia DR3']),
    releaseVersion: z.enum(['Hipparcos 2007', 'Gaia DR3']),
    catalogSourceId: name(),
    raDeg: angle360(),
    decDeg: latitude(),
    altDeg: latitude(),
    azDeg: angle360(),
    aboveHorizon: z.boolean(),
    mag: magnitude(),
    properMotionRaMasYr: properMotion(),
    properMotionDecMasYr: properMotion(),
    distancePc: distanceInterval(z.enum(SKY_CONTEXT_STAR_DISTANCE_METHODS)),
    cartesianParsecICRS: cartesian(),
    lightTravel: LightTravelSchema,
    bpRp: wireNumber().min(SKY_CONTEXT_BP_RP_MIN).max(SKY_CONTEXT_BP_RP_MAX).optional(),
    radialVelocityKmS: wireNumber().min(-SKY_CONTEXT_RADIAL_VELOCITY_MAX).max(SKY_CONTEXT_RADIAL_VELOCITY_MAX).optional(),
    astrophysics: AstrophysicsSchema.optional(),
  })
  .strict()
  .superRefine((s, ctx) => {
    if (s.aboveHorizon !== s.altDeg >= 0) return broken(ctx, 'aboveHorizonIsAltitude');
    if (SKY_CONTEXT_PROVIDERS[s.provider] !== s.releaseVersion) return broken(ctx, 'releaseOfProvider');
    if (!new RegExp(SKY_CONTEXT_SOURCE_ID_PATTERNS[s.provider]).test(s.catalogSourceId))
      return broken(ctx, 'sourceIdOfProvider');
    if (s.lightTravel.distancePc !== s.distancePc.median)
      return broken(ctx, 'lightTravelOfDistance');
    if (s.astrophysics !== undefined && (s.provider !== 'Gaia DR3' || s.astrophysics.catalogSourceId !== s.catalogSourceId))
      return broken(ctx, 'astrophysicsOfGaia');
    // A Flamsteed designation or the catalog id, where neither name is held, is the route's to give
    if (s.commonName !== s.catalogSourceId ? s.title !== s.commonName : s.bayerDesignation !== undefined && s.title !== s.bayerDesignation)
      return broken(ctx, 'titleOrder');
  });
export type SkyContextStar = z.infer<typeof SkyContextStarSchema>;

export const BodySkyPositionSchema = z
  .object({ raDeg: angle360(), decDeg: latitude(), uncertaintyArcsec: nonNegative(), altDeg: latitude(), azDeg: angle360() })
  .strict();

export const BodyEvidenceSchema = z
  .object({
    id: id(),
    evidenceClass: z.literal('predicted'),
    verificationMethod: text(),
    distanceUncertaintyNote: text(),
    limitations: notes(),
    verifiedByIndependentHoldout: z.literal(false),
  })
  .strict();

export const SkyContextBodySchema = z
  .object({
    id: id(),
    evidenceClass: z.literal('predicted'),
    name: z.string().regex(new RegExp(SKY_CONTEXT_BODY_NAME_PATTERN)),
    title: name(),
    subtitle: name(),
    skyPosition: BodySkyPositionSchema,
    inferredDistancePc: distanceInterval(z.literal('ephemeris_au')),
    cartesianParsecICRS: cartesian(),
    evidence: BodyEvidenceSchema,
    raDeg: angle360(),
    decDeg: latitude(),
    altDeg: latitude(),
    azDeg: angle360(),
    aboveHorizon: z.boolean(),
    distanceAu: positive(),
    distancePc: positive(),
    lightTravel: LightTravelSchema,
  })
  .strict()
  .superRefine((b, ctx) => {
    const p = b.skyPosition;
    if (b.raDeg !== p.raDeg || b.decDeg !== p.decDeg || b.altDeg !== p.altDeg || b.azDeg !== p.azDeg)
      return broken(ctx, 'positionIsSkyPosition');
    if (b.aboveHorizon !== b.altDeg >= 0) return broken(ctx, 'aboveHorizonIsAltitude');
    if (b.distancePc !== b.inferredDistancePc.median) return broken(ctx, 'distanceIsInferred');
    if (b.lightTravel.distancePc !== b.distancePc) return broken(ctx, 'lightTravelOfDistance');
    const stem = b.name.toLowerCase();
    if (b.id !== `finding-predicted-${stem}` || b.evidence.id !== `ev-ephem-${stem}`) return broken(ctx, 'idsOfName');
  });
export type SkyContextBody = z.infer<typeof SkyContextBodySchema>;

export const ConstellationEdgeSchema = z
  .object({ constellation: name(), fromStarId: id(), toStarId: id() })
  .strict();

export const PredictedSkyBrightnessSchema = z
  .object({
    skyBrightnessMagArcsec2: wireNumber(),
    darkSkyMagArcsec2: wireNumber(),
    lunarVeilDeltaMag: nonNegative(),
    skyBrightnessNanoLamberts: nonNegative(),
    lunarScatteredNanoLamberts: nonNegative(),
    airmass: wireNumber().min(1),
    illuminationFraction: wireNumber().min(0).max(1),
    // Null within the geocentric synodic angle's stated bound of a sector's edge, where either neighbouring name could be
    // true (services/science/ephemeris.py `phase_name`): about ten minutes of each lunar month, everywhere at once
    lunarPhaseName: z.enum(SKY_CONTEXT_MOON_PHASES).nullable(),
    sepFromMoonDeg: wireNumber().min(0).max(180),
    target: z.literal('zenith'),
    targetAltDeg: latitude(),
    moonAltDeg: latitude(),
    evidenceClass: z.literal('predicted'),
    method: text(),
    limitations: notes(),
  })
  .strict();

/** The Sun's centre altitude from the place, in the reply's convention (apparent and airless), and its USNO band. */
export const SkyContextSunSchema = z
  .object({
    altDeg: latitude(),
    band: z.enum(SKY_CONTEXT_SUN_BANDS),
    evidenceClass: z.literal('predicted'),
    method: text(),
    limitations: notes(),
  })
  .strict()
  .superRefine((s, ctx) => {
    if (s.band !== sunBandOf(s.altDeg)) broken(ctx, 'sunBandOfAltitude');
  });
export type SkyContextSun = z.infer<typeof SkyContextSunSchema>;

export const SkyContextReplySchema = z
  .object({
    format: z.literal(SKY_CONTEXT_FORMAT),
    formatVersion: z.literal(SKY_CONTEXT_FORMAT_VERSION),
    observedAtUTC: wireUtcInstant(),
    observerLocation: z.object({ latDeg: latitude(), lonDeg: longitude() }).strict(),
    filters: SkyContextFiltersSchema,
    convention: SkyContextConventionSchema,
    stars: z.array(SkyContextStarSchema).max(SKY_CONTEXT_STARS_MAX),
    ephemerides: z.array(SkyContextBodySchema).max(SKY_CONTEXT_BODIES_MAX),
    constellationEdges: z.array(ConstellationEdgeSchema).max(SKY_CONTEXT_EDGES_MAX),
    predictedSkyBrightness: PredictedSkyBrightnessSchema,
    // Absent from a reply made before the Sun was added (skycontext-3); never null
    sun: SkyContextSunSchema.optional(),
  })
  .strict()
  .superRefine((r, ctx) => {
    if (!inSpan(r.observedAtUTC)) return broken(ctx, 'insideEphemerisSpan');
    const starIds = r.stars.map((s) => s.id);
    const bodyIds = r.ephemerides.map((b) => b.id);
    if (new Set(starIds).size !== starIds.length || new Set(bodyIds).size !== bodyIds.length) return broken(ctx, 'idsOnce');
    const held = new Set(starIds);
    if (r.constellationEdges.some((e) => e.fromStarId === e.toStarId || !held.has(e.fromStarId) || !held.has(e.toStarId)))
      return broken(ctx, 'edgeJoinsHeldStars');
    if (r.filters.aboveHorizonOnly && [...r.stars, ...r.ephemerides].some((item) => item.altDeg < 0))
      return broken(ctx, 'aboveHorizonOnlyHeld');
    const limit = r.filters.magLimit;
    if (limit !== undefined && r.stars.some((s) => s.mag > limit)) return broken(ctx, 'magLimitHeld');
  });
export type SkyContextReply = z.infer<typeof SkyContextReplySchema>;
