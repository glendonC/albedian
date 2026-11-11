import { z } from 'zod';
import LETTERS_AND_DIGITS from './data/letters-and-digits.json';
import { EvidenceClassSchema, type EvidenceClass } from './primitives.js';

/**
 * A typed answer to a question about a person's own photo (Python mirror: services/contracts/voice.py, the
 * "answers" section; samples both sides judge: tests/fixtures/voice_answer_contract.json).
 *
 * An answer is either `answered`, one short plain sentence a person reads or hears plus the statements behind it,
 * or `unanswerable`, a plain sentence saying why this photo's data cannot answer, with no statements. Each
 * statement says what it states (`quantity`) about what (`subject`), its value in the unit its source stores, its
 * evidence class and its source, so a client never parses a fact out of the sentence.
 *
 * A statement's class is never the caller's choice: the source decides it. Each source a statement may cite is a
 * row of a table here (SOLUTION_FIELD_SOURCES, CATALOG_SOURCE_QUANTITIES, EPHEMERIS_METHODS) that names the
 * quantity it holds, its stored unit and its class rule; the rule (CLASS_RULES) gives the one class a statement
 * from that source has about that subject, or none, and a statement with any other class is refused:
 * - a field measured from the photo's pixels (a detection's own fields) is observed and nothing else;
 * - a catalog field, cited from the catalog or from its copy in the solution, is never observed: it is
 *   catalog_matched on a catalog_matched finding, predicted on a predicted one, simulated on a simulated one;
 * - a finding's sky position carries the finding's own class;
 * - an ephemeris value is predicted, or simulated about a simulated finding;
 * - a statement about the whole solution states only the solution's own quantities: its counts, residuals and
 *   status (what the solver measured, observed) and its pose offset (computed from assumptions, predicted).
 * One answer gives each finding one class, and an id of the predicted form is only ever predicted.
 *
 * Every number lies in its quantity's range (QUANTITY_SPECS), intervals included; every text is bounded in UTF-16
 * units with no lone surrogate and no control character; every integer is safe. These are the lead's wire rules
 * (.lead/briefs/WIRE_RULES.md), implemented here until the shared wire module lands on main.
 *
 * This file holds the shapes only. Nothing computes or sends an answer yet. [proposed] answers-3 attaches it to
 * the voice reply.
 */

/** [proposed] One spoken sentence of at most 90 characters: the bound the lead approved for the language lane's
 * spoken reply (.lead/STATE.md, 2026-11-09), so a caption stays short on a phone. Counted in UTF-16 code units. */
export const ANSWER_SENTENCE_MAX_UTF16 = 90;
/** [proposed] The most UTF-16 units of any other text: a name, a release, a row id, an id. */
export const ANSWER_TEXT_MAX_UTF16 = 128;
/** [proposed] The most statements one answer carries, and the highest finding index a source path may name. */
export const ANSWER_STATEMENTS_MAX = 32;
export const FINDING_INDEX_MAX = 99_999;
/** The deepest source path: findings, an index, a field, its member and that member's member. */
export const SOURCE_PATH_MAX_DEPTH = 5;
/** The largest integer both JSON readers hold exactly: 2^53 - 1. */
export const SAFE_INTEGER_MAX = Number.MAX_SAFE_INTEGER;

/** The ids the backend mints: `finding-obs-<n>`, `finding-<catalog star id>`, `finding-predicted-<body or star id>`,
 * `finding-observed-moon`, `finding-openngc-<OpenNGC name>`; `sol-<name>` and `sol_<16 hex>`. */
export const FINDING_ID_PATTERN = /^finding-[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$/;
export const PREDICTED_FINDING_ID_PATTERN = /^finding-predicted-/;
export const SOLUTION_ID_PATTERN = /^sol[-_][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$/;

/** The questions this repository's data can answer, from the survey of 2026-11-11 (section 3). */
export const ANSWERABLE_QUESTION_KINDS = [
  'identity',
  'distance',
  'brightness',
  'colour_temperature',
  'constellation',
  'sky_position',
  'moon_phase',
  'photo_contents',
  'confidence',
] as const;
/** A question no data of this photo answers ("what is a light year?", "is that a plane?"). */
export const UNANSWERABLE_QUESTION_KIND = 'unanswerable';
export const AnswerableQuestionKindSchema = z.enum(ANSWERABLE_QUESTION_KINDS);
export const QuestionKindSchema = z.enum([...ANSWERABLE_QUESTION_KINDS, UNANSWERABLE_QUESTION_KIND]);
export type QuestionKind = z.infer<typeof QuestionKindSchema>;

/** Why this photo's data cannot answer. `outside_the_data` is for the unanswerable question kind, and only it. */
export const UNANSWERABLE_REASONS = [
  // The named thing is not in this photo's sky
  'not_found_in_this_sky',
  // The photo has no solution that places anything on the sky (pending or unresolved)
  'photo_not_solved',
  // The answer needs the capture time and place, and the photo has neither or not both
  'no_time_or_place',
  // The solution, catalog or ephemeris holds no value for it (a star with no parallax, no temperature row)
  'not_in_the_data',
  // No data of this photo answers the question at all
  'outside_the_data',
] as const;
export const UnanswerableReasonSchema = z.enum(UNANSWERABLE_REASONS);

/** The units sources store. `count` is a whole number of things, `fraction` lies in 0 to 1, `mag` is a magnitude
 * (also a colour index such as BP minus RP), `ratio` a dimensionless ratio such as a signal to noise. A converted
 * unit (light years, kilometres) is never stated: a sentence may say one, from a statement in its stored unit. */
export const ANSWER_UNITS = ['pc', 'au', 'mag', 'K', 'deg', 'arcsec', 'px', 'fraction', 'count', 'ratio'] as const;
export const AnswerUnitSchema = z.enum(ANSWER_UNITS);
export type AnswerUnit = z.infer<typeof AnswerUnitSchema>;

/** The closed lists a text value may come from: the solution contract's object types and statuses
 * (astronomy.ts, primitives.ts) and the Moon's phase names (services/science/radiometry.py). */
export const OBJECT_TYPES = ['star', 'planet', 'moon', 'deep_sky', 'satellite_candidate'] as const;
export const SOLUTION_STATUSES = ['pending', 'calibrated', 'approximate', 'predicted_only', 'unresolved', 'ambiguous'] as const;
export const MOON_PHASE_NAMES = [
  'New Moon',
  'Waxing Crescent',
  'First Quarter',
  'Waxing Gibbous',
  'Full Moon',
  'Waning Gibbous',
  'Last Quarter',
  'Waning Crescent',
] as const;

/** A number's range: null is no bound beyond being finite; `open` excludes that end. */
type Range = { min: number | null; minOpen: boolean; max: number | null; maxOpen: boolean };
type QuantitySpec = { value: 'text'; values?: readonly string[] } | { value: 'number'; range: Range };
const DEGREES_AROUND: Range = { min: 0, minOpen: false, max: 360, maxOpen: true };
const DEGREES_UP: Range = { min: -90, minOpen: false, max: 90, maxOpen: false };
const ABOVE_ZERO: Range = { min: 0, minOpen: true, max: null, maxOpen: false };
const ZERO_OR_MORE: Range = { min: 0, minOpen: false, max: null, maxOpen: false };
const COUNT: Range = { min: 0, minOpen: false, max: SAFE_INTEGER_MAX, maxOpen: false };
// A magnitude and a colour index have no physical bound: only finite
const ANY_FINITE: Range = { min: null, minOpen: false, max: null, maxOpen: false };

/** What a statement states: the kind of value it takes and, for a number, its range (both ends of an interval
 * too). A count is also whole. */
export const QUANTITY_SPECS = {
  name: { value: 'text' },
  object_type: { value: 'text', values: OBJECT_TYPES },
  distance: { value: 'number', range: ABOVE_ZERO },
  apparent_magnitude: { value: 'number', range: ANY_FINITE },
  detection_snr: { value: 'number', range: ABOVE_ZERO },
  colour_index_bp_rp: { value: 'number', range: ANY_FINITE },
  effective_temperature: { value: 'number', range: ABOVE_ZERO },
  spectral_type: { value: 'text' },
  constellation: { value: 'text' },
  altitude: { value: 'number', range: DEGREES_UP },
  azimuth: { value: 'number', range: DEGREES_AROUND },
  right_ascension: { value: 'number', range: DEGREES_AROUND },
  declination: { value: 'number', range: DEGREES_UP },
  position_uncertainty: { value: 'number', range: ZERO_OR_MORE },
  illuminated_fraction: { value: 'number', range: { min: 0, minOpen: false, max: 1, maxOpen: false } },
  phase_angle: { value: 'number', range: { min: 0, minOpen: false, max: 180, maxOpen: false } },
  moon_phase_name: { value: 'text', values: MOON_PHASE_NAMES },
  solution_status: { value: 'text', values: SOLUTION_STATUSES },
  detected_source_count: { value: 'number', range: COUNT },
  matched_star_count: { value: 'number', range: COUNT },
  held_out_pair_count: { value: 'number', range: COUNT },
  held_out_median_residual: { value: 'number', range: ZERO_OR_MORE },
  pose_max_offset: { value: 'number', range: { min: 0, minOpen: false, max: 180, maxOpen: false } },
} as const satisfies Record<string, QuantitySpec>;
export type AnswerQuantity = keyof typeof QUANTITY_SPECS;
export const AnswerQuantitySchema = z.enum(Object.keys(QUANTITY_SPECS) as [AnswerQuantity, ...AnswerQuantity[]]);

/** An answer to a question kind has at least one statement of one of these quantities. */
export const QUESTION_QUANTITIES = {
  identity: ['name', 'object_type'],
  distance: ['distance'],
  brightness: ['apparent_magnitude', 'detection_snr'],
  colour_temperature: ['colour_index_bp_rp', 'effective_temperature', 'spectral_type'],
  constellation: ['constellation'],
  sky_position: ['altitude', 'azimuth', 'right_ascension', 'declination'],
  moon_phase: ['illuminated_fraction', 'phase_angle', 'moon_phase_name'],
  photo_contents: ['name', 'detected_source_count', 'matched_star_count'],
  confidence: [
    'solution_status',
    'matched_star_count',
    'held_out_pair_count',
    'held_out_median_residual',
    'pose_max_offset',
    'position_uncertainty',
  ],
} as const satisfies Record<(typeof ANSWERABLE_QUESTION_KINDS)[number], readonly AnswerQuantity[]>;

/** The one class a statement from a source of each rule has about each subject (a finding by its own class, an
 * ephemeris body, the solution). A subject the rule does not list cannot be stated from that source. */
export const CLASS_RULES = {
  // Measured from the photo's pixels: a detection's own fields
  measured: { observed: 'observed', catalog_matched: 'observed' },
  // The finding's own sky position
  own_class: { observed: 'observed', catalog_matched: 'catalog_matched', predicted: 'predicted', simulated: 'simulated' },
  // A catalog value, from the catalog or from its copy in the solution
  catalog: { catalog_matched: 'catalog_matched', predicted: 'predicted', simulated: 'simulated' },
  // Computed by the ephemeris for the capture time and place
  ephemeris: { observed: 'predicted', catalog_matched: 'predicted', predicted: 'predicted', simulated: 'simulated', ephemeris_body: 'predicted' },
  // What the solver measured of the photo as a whole
  solve: { solution: 'observed' },
  // How far an approximate pose may be off, computed from stated assumptions, never checked against stars
  pose: { solution: 'predicted' },
} as const satisfies Record<string, Partial<Record<EvidenceClass | 'ephemeris_body' | 'solution', EvidenceClass>>>;
export type ClassRule = keyof typeof CLASS_RULES;

type SourceRow = { quantity: AnswerQuantity; unit: AnswerUnit | null; rule: ClassRule };

/** The fields of the stored solution (astronomy.ts ObservationSolutionSchema) a statement may cite, each as its path
 * with `*` for a finding's index. A text quantity has no unit. */
export const SOLUTION_FIELD_SOURCES = [
  { path: ['status'], quantity: 'solution_status', unit: null, rule: 'solve' },
  { path: ['quality', 'detectedCandidateCount'], quantity: 'detected_source_count', unit: 'count', rule: 'solve' },
  { path: ['quality', 'matchedInlierCount'], quantity: 'matched_star_count', unit: 'count', rule: 'solve' },
  { path: ['quality', 'heldOutPairCount'], quantity: 'held_out_pair_count', unit: 'count', rule: 'solve' },
  { path: ['quality', 'medianResidualPx'], quantity: 'held_out_median_residual', unit: 'px', rule: 'solve' },
  { path: ['quality', 'medianResidualArcsec'], quantity: 'held_out_median_residual', unit: 'arcsec', rule: 'solve' },
  { path: ['poseEstimate', 'maxCenterOffsetDeg'], quantity: 'pose_max_offset', unit: 'deg', rule: 'pose' },
  { path: ['findings', '*', 'sourceDetection', 'snr'], quantity: 'detection_snr', unit: 'ratio', rule: 'measured' },
  { path: ['findings', '*', 'skyPosition', 'raDeg'], quantity: 'right_ascension', unit: 'deg', rule: 'own_class' },
  { path: ['findings', '*', 'skyPosition', 'decDeg'], quantity: 'declination', unit: 'deg', rule: 'own_class' },
  { path: ['findings', '*', 'skyPosition', 'altDeg'], quantity: 'altitude', unit: 'deg', rule: 'own_class' },
  { path: ['findings', '*', 'skyPosition', 'azDeg'], quantity: 'azimuth', unit: 'deg', rule: 'own_class' },
  { path: ['findings', '*', 'skyPosition', 'uncertaintyArcsec'], quantity: 'position_uncertainty', unit: 'arcsec', rule: 'own_class' },
  { path: ['findings', '*', 'catalogRef', 'commonName'], quantity: 'name', unit: null, rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'objectType'], quantity: 'object_type', unit: null, rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'constellation'], quantity: 'constellation', unit: null, rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'spectralType'], quantity: 'spectral_type', unit: null, rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'apparentMagnitude'], quantity: 'apparent_magnitude', unit: 'mag', rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'colorIndexBpRp'], quantity: 'colour_index_bp_rp', unit: 'mag', rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'raDeg'], quantity: 'right_ascension', unit: 'deg', rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'decDeg'], quantity: 'declination', unit: 'deg', rule: 'catalog' },
  { path: ['findings', '*', 'catalogRef', 'bailerJonesDistancePc', 'median'], quantity: 'distance', unit: 'pc', rule: 'catalog' },
  { path: ['findings', '*', 'inferredDistancePc', 'median'], quantity: 'distance', unit: 'pc', rule: 'catalog' },
] as const satisfies readonly (SourceRow & { path: readonly string[] })[];

/** The catalogs a row comes from (astronomy.ts CatalogReferenceSchema's providers), and the quantities a row holds. */
export const CATALOG_PROVIDERS = ['Gaia DR3', 'Hipparcos', 'JPL Horizons', 'SIMBAD', 'OpenNGC'] as const;
export const CATALOG_SOURCE_QUANTITIES = [
  { quantity: 'name', unit: null, rule: 'catalog' },
  { quantity: 'object_type', unit: null, rule: 'catalog' },
  { quantity: 'constellation', unit: null, rule: 'catalog' },
  { quantity: 'spectral_type', unit: null, rule: 'catalog' },
  { quantity: 'apparent_magnitude', unit: 'mag', rule: 'catalog' },
  { quantity: 'colour_index_bp_rp', unit: 'mag', rule: 'catalog' },
  { quantity: 'effective_temperature', unit: 'K', rule: 'catalog' },
  { quantity: 'right_ascension', unit: 'deg', rule: 'catalog' },
  { quantity: 'declination', unit: 'deg', rule: 'catalog' },
  { quantity: 'distance', unit: 'pc', rule: 'catalog' },
] as const satisfies readonly SourceRow[];

/** The bodies the ephemeris computes (services/science/ephemeris.py), and its methods: the module's PLANET_METHOD,
 * MOON_METHOD and SUN_METHOD, and radiometry.py's compute_lunar_phase_and_illumination. Each names the bodies it
 * computes and the quantities it gives, in the unit it gives them. */
export const EPHEMERIS_BODIES = ['Sun', 'Moon', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus'] as const;
const POSITION_ROWS = [
  { quantity: 'altitude', unit: 'deg', rule: 'ephemeris' },
  { quantity: 'azimuth', unit: 'deg', rule: 'ephemeris' },
  { quantity: 'right_ascension', unit: 'deg', rule: 'ephemeris' },
  { quantity: 'declination', unit: 'deg', rule: 'ephemeris' },
  { quantity: 'distance', unit: 'au', rule: 'ephemeris' },
] as const satisfies readonly SourceRow[];
export const EPHEMERIS_METHODS = {
  planet: { bodies: ['Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus'], rows: POSITION_ROWS },
  moon: { bodies: ['Moon'], rows: POSITION_ROWS },
  sun: { bodies: ['Sun'], rows: POSITION_ROWS },
  lunar_phase: {
    bodies: ['Moon'],
    rows: [
      { quantity: 'illuminated_fraction', unit: 'fraction', rule: 'ephemeris' },
      { quantity: 'phase_angle', unit: 'deg', rule: 'ephemeris' },
      { quantity: 'moon_phase_name', unit: null, rule: 'ephemeris' },
    ],
  },
} as const satisfies Record<string, { bodies: readonly (typeof EPHEMERIS_BODIES)[number][]; rows: readonly SourceRow[] }>;
export type EphemerisMethod = keyof typeof EPHEMERIS_METHODS;

/** What an uncertainty means, as its source states it: Bailer-Jones and GSP-Phot give the 16th and 84th
 * percentiles, the ephemeris an error bound, a measurement one standard deviation. */
export const UNCERTAINTY_MEANINGS = ['percentile_16_84', 'error_bound', 'one_sigma'] as const;

// The fixed ranges, never a runtime's Unicode data: U+D800 to U+DFFF unpaired, and C0, DEL and C1
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
/** The Unicode version of the letters-and-digits table (data/letters-and-digits.json, written by
 * scripts/generate_letters_and_digits.py, shipped with its notice on apps/web/public/notices/unicode/). A letter
 * or digit is a code point of its ranges: categories L and N less Default_Ignorable_Code_Point, so the Hangul
 * fillers that never show are not. The table decides, never the Unicode data of the runtime, so every runtime
 * judges a sentence alike; services/contracts/voice.py reads the same file. */
export const LETTERS_AND_DIGITS_UNICODE_VERSION: string = LETTERS_AND_DIGITS.unicodeVersion;
const LETTER_RANGES: readonly (readonly number[])[] = LETTERS_AND_DIGITS.ranges;

/** Whether the table counts this code point as a letter or a digit. */
export function isLetterOrDigit(codePoint: number): boolean {
  let low = 0;
  let high = LETTER_RANGES.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const [first, last] = LETTER_RANGES[middle]!;
    if (codePoint < first!) high = middle - 1;
    else if (codePoint > last!) low = middle + 1;
    else return true;
  }
  return false;
}

/** Text of 1 to `max` UTF-16 units with no lone surrogate and no control character. */
function boundedText(max: number, pattern?: RegExp, message?: string) {
  const base = z.string().min(1).max(max);
  return (pattern === undefined ? base : base.regex(pattern, message))
    .refine((s) => !LONE_SURROGATE.test(s), 'A text holds no lone surrogate.')
    .refine((s) => !CONTROL.test(s), 'A text holds no control character.');
}
const text = boundedText(ANSWER_TEXT_MAX_UTF16);
const finite = z.number().finite();

/** What a statement is about: a finding of the solution (with that finding's own evidence class), a body the
 * ephemeris computed that has no finding (one below the horizon, say), or the solution as a whole. */
export const AnswerSubjectSchema = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('finding'),
      findingId: boundedText(ANSWER_TEXT_MAX_UTF16, FINDING_ID_PATTERN, 'A finding id has the form the backend mints.'),
      evidenceClass: EvidenceClassSchema,
    })
    .strict(),
  z.object({ kind: z.literal('ephemeris_body'), body: z.enum(EPHEMERIS_BODIES) }).strict(),
  z.object({ kind: z.literal('solution') }).strict(),
]);
export type AnswerSubject = z.infer<typeof AnswerSubjectSchema>;

/** Where a statement's value came from: a field of the answer's stored solution (its path, matching a row of
 * SOLUTION_FIELD_SOURCES), a catalog row (the catalog, its release and the row's own id), or an ephemeris method run
 * for the solution's capture time and place. */
export const AnswerSourceSchema = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('solution_field'),
      path: z
        .array(z.union([boundedText(64), z.number().int().min(0).max(FINDING_INDEX_MAX)]))
        .min(1)
        .max(SOURCE_PATH_MAX_DEPTH),
    })
    .strict(),
  z.object({ kind: z.literal('catalog'), catalog: z.enum(CATALOG_PROVIDERS), release: text, rowId: text }).strict(),
  z
    .object({
      kind: z.literal('ephemeris'),
      method: z.enum(Object.keys(EPHEMERIS_METHODS) as [EphemerisMethod, ...EphemerisMethod[]]),
    })
    .strict(),
]);
export type AnswerSource = z.infer<typeof AnswerSourceSchema>;

/** An uncertainty in the value's own unit, as its source states it, with what the source says it means. */
export const AnswerUncertaintySchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('interval'), lower: finite, upper: finite, meaning: z.enum(UNCERTAINTY_MEANINGS) }).strict(),
  z.object({ kind: z.literal('plus_minus'), halfWidth: finite.nonnegative(), meaning: z.enum(UNCERTAINTY_MEANINGS) }).strict(),
]);
export type AnswerUncertainty = z.infer<typeof AnswerUncertaintySchema>;

export const AnswerValueSchema = z.discriminatedUnion('kind', [
  z
    .object({
      kind: z.literal('number'),
      amount: finite,
      unit: AnswerUnitSchema,
      uncertainty: AnswerUncertaintySchema.optional(),
    })
    .strict(),
  z.object({ kind: z.literal('text'), text }).strict(),
]);
export type AnswerValue = z.infer<typeof AnswerValueSchema>;

function issue(ctx: z.RefinementCtx, path: (string | number)[], message: string): void {
  ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
}

/** The table row a source names for a quantity, or why there is none. */
export function sourceRow(source: AnswerSource, quantity: AnswerQuantity): SourceRow | string {
  if (source.kind === 'solution_field') {
    const row = SOLUTION_FIELD_SOURCES.find(
      (candidate) =>
        candidate.path.length === source.path.length &&
        candidate.path.every((step, index) =>
          step === '*' ? typeof source.path[index] === 'number' : step === source.path[index]
        )
    );
    if (row === undefined) return 'A source path names a field of the solution a statement may cite.';
    return row.quantity === quantity ? row : `The field ${row.path.join('/')} holds a ${row.quantity}, not a ${quantity}.`;
  }
  const rows: readonly SourceRow[] =
    source.kind === 'catalog' ? CATALOG_SOURCE_QUANTITIES : EPHEMERIS_METHODS[source.method].rows;
  const row = rows.find((candidate) => candidate.quantity === quantity);
  return row ?? `A ${source.kind} source gives no ${quantity}.`;
}

/** Whether `amount` lies in `range`. */
export function inRange(amount: number, range: Range): boolean {
  if (range.min !== null && (range.minOpen ? !(amount > range.min) : !(amount >= range.min))) return false;
  if (range.max !== null && (range.maxOpen ? !(amount < range.max) : !(amount <= range.max))) return false;
  return true;
}

export const AnswerStatementSchema = z
  .object({
    subject: AnswerSubjectSchema,
    quantity: AnswerQuantitySchema,
    value: AnswerValueSchema,
    evidenceClass: EvidenceClassSchema,
    source: AnswerSourceSchema,
  })
  .strict()
  .superRefine((statement, ctx) => {
    const { subject, value, source, evidenceClass, quantity } = statement;
    if (subject.kind === 'finding' && PREDICTED_FINDING_ID_PATTERN.test(subject.findingId) && subject.evidenceClass !== 'predicted') {
      issue(ctx, ['subject', 'evidenceClass'], 'A finding with an id of the predicted form is predicted.');
    }
    const spec: QuantitySpec = QUANTITY_SPECS[quantity];
    if (spec.value !== value.kind) {
      issue(ctx, ['value', 'kind'], `A ${quantity} statement takes a ${spec.value} value.`);
      return;
    }
    if (value.kind === 'text' && spec.value === 'text' && spec.values !== undefined && !spec.values.includes(value.text)) {
      issue(ctx, ['value', 'text'], `A ${quantity} is one of ${spec.values.join(', ')}.`);
    }
    if (value.kind === 'number' && spec.value === 'number') {
      if (!inRange(value.amount, spec.range)) {
        issue(ctx, ['value', 'amount'], `A ${quantity} lies in its range.`);
      }
      if (value.unit === 'count' && !Number.isInteger(value.amount)) {
        issue(ctx, ['value', 'amount'], 'A count is a whole number.');
      }
      const u = value.uncertainty;
      if (u !== undefined && value.unit === 'count') {
        issue(ctx, ['value', 'uncertainty'], 'A count is exact: it has no uncertainty.');
      }
      if (u?.kind === 'interval') {
        if (!(u.lower <= value.amount && value.amount <= u.upper)) {
          issue(ctx, ['value', 'uncertainty'], 'An interval holds its value: lower <= amount <= upper.');
        }
        if (!inRange(u.lower, spec.range) || !inRange(u.upper, spec.range)) {
          issue(ctx, ['value', 'uncertainty'], `Both ends of an interval lie in a ${quantity}'s range.`);
        }
      }
    }
    const row = sourceRow(source, quantity);
    if (typeof row === 'string') {
      issue(ctx, ['source'], row);
      return;
    }
    const unit = value.kind === 'number' ? value.unit : null;
    if (unit !== row.unit) {
      issue(ctx, ['value', 'unit'], `This source stores a ${quantity} in ${row.unit ?? 'text'}.`);
    }
    if (source.kind === 'ephemeris' && subject.kind === 'ephemeris_body') {
      const bodies: readonly string[] = EPHEMERIS_METHODS[source.method].bodies;
      if (!bodies.includes(subject.body)) {
        issue(ctx, ['source', 'method'], `The ${source.method} method computes ${bodies.join(', ')}, not ${subject.body}.`);
      }
    }
    const subjectKey = subject.kind === 'finding' ? subject.evidenceClass : subject.kind;
    const classes: Partial<Record<string, EvidenceClass>> = CLASS_RULES[row.rule];
    const decided = classes[subjectKey];
    if (decided === undefined) {
      issue(ctx, ['subject'], `A ${row.rule} source states nothing about a ${subjectKey} subject.`);
    } else if (decided !== evidenceClass) {
      issue(ctx, ['evidenceClass'], `This statement is ${decided}: its source decides, not the caller.`);
    }
  });
export type AnswerStatement = z.infer<typeof AnswerStatementSchema>;

/** The one sentence a person reads or hears: at most ANSWER_SENTENCE_MAX_UTF16 units, holding a letter or digit. */
export const AnswerSentenceSchema = boundedText(ANSWER_SENTENCE_MAX_UTF16).refine(
  (s) => [...s].some((char) => isLetterOrDigit(char.codePointAt(0)!)),
  'A sentence says something: it holds a letter or a digit.'
);

const solutionId = boundedText(ANSWER_TEXT_MAX_UTF16, SOLUTION_ID_PATTERN, 'A solution id has the form the backend mints.');

export const AnsweredSchema = z
  .object({
    status: z.literal('answered'),
    question: AnswerableQuestionKindSchema,
    observationSolutionId: solutionId,
    sentence: AnswerSentenceSchema,
    statements: z.array(AnswerStatementSchema).min(1).max(ANSWER_STATEMENTS_MAX),
  })
  .strict();

/** A refusal is a typed answer too: what was asked, why it cannot be answered, in one plain sentence, and no
 * statements. The solution is named when there is one. */
export const UnanswerableSchema = z
  .object({
    status: z.literal('unanswerable'),
    question: QuestionKindSchema,
    reason: UnanswerableReasonSchema,
    sentence: AnswerSentenceSchema,
    observationSolutionId: solutionId.optional(),
  })
  .strict();

export const VoiceAnswerSchema = z
  .discriminatedUnion('status', [AnsweredSchema, UnanswerableSchema])
  .superRefine((answer, ctx) => {
    if (answer.status === 'unanswerable') {
      if ((answer.question === UNANSWERABLE_QUESTION_KIND) !== (answer.reason === 'outside_the_data')) {
        issue(ctx, ['reason'], 'A question no data answers is outside_the_data, and only such a question is.');
      }
      return;
    }
    const answering: readonly AnswerQuantity[] = QUESTION_QUANTITIES[answer.question];
    if (!answer.statements.some((statement) => answering.includes(statement.quantity))) {
      issue(ctx, ['statements'], `A ${answer.question} answer states at least one of ${answering.join(', ')}.`);
    }
    const classOf = new Map<string, EvidenceClass>();
    answer.statements.forEach((statement, index) => {
      if (statement.subject.kind !== 'finding') return;
      const seen = classOf.get(statement.subject.findingId);
      if (seen !== undefined && seen !== statement.subject.evidenceClass) {
        issue(ctx, ['statements', index, 'subject', 'evidenceClass'], 'One answer gives a finding one class.');
      }
      classOf.set(statement.subject.findingId, seen ?? statement.subject.evidenceClass);
    });
  });
export type VoiceAnswer = z.infer<typeof VoiceAnswerSchema>;
