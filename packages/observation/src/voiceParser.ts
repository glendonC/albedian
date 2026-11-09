import {
  ActionContext,
  ActionContextSchema,
  ActionOutcome,
  ActionOutcomeSchema,
  AlbedianAction,
  AlbedianActionSchema,
  CelestialFinding,
  ObservationSession,
  VoiceAction,
  VoiceActionSchema,
  deterministicHexDigest,
} from '@albedian/contracts';
import { dispatchAlbedianAction } from './reducer.js';
import { EMPTY_SOURCE_ASSET_ID } from './emptySession.js';

/**
 * ============================================================================
 * @albedian/observation: Context-Bound Voice Utterance Parser & Deictic Resolver
 * ============================================================================
 *
 * The parser says only what the request and the view hold. A target comes from a name, matched as a
 * whole word, or from what is selected, focused or pointed at; nothing else stands in for it. Years count
 * from the clock. A request no rule matches is a question, and every refusal carries its own reason.
 */

export const MAX_POINTER_RECENCY_MS = 10_000;

type RejectionReason = Extract<ActionOutcome, { status: 'rejected' }>['reason'];

/** The contract's bounds on a space.epoch year, which is historical: 500 BCE is -500, and there is no year 0. */
const EPOCH_MIN_YEAR_CE = -10000;
const EPOCH_MAX_YEAR_CE = 12000;
/** The contract's longest question, in UTF-16 code units, as a JavaScript string's length counts them. */
const QUESTION_MAX_UTF16 = 500;

export interface VoiceParseOptions {
  /** Stands in for the clock, whose UTC year "N years ago" counts back from. */
  now?: Date;
}

export interface VoiceParseResult {
  action?: AlbedianAction;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  /** Why no action was formed, as the contract's rejection reason; set whenever `error` is. */
  reason?: RejectionReason;
  /** Said before the reducer's own banner when the action completes: how its target was chosen. */
  note?: string;
}

/**
 * epochISO for a historical year, as ISO 8601 writes it: January 1 at 00:00 UTC, in astronomical
 * numbering (1 BCE is year 0, 500 BCE is -499), with the signed six-digit expanded year outside 0000 to
 * 9999, the only form JavaScript's Date reads there. A yearCE of 0, which historical counting lacks, maps
 * to astronomical year 0.
 */
export function epochIsoForYear(yearCE: number): string {
  const astronomical = yearCE < 0 ? yearCE + 1 : yearCE;
  const digits = String(Math.abs(astronomical));
  const year =
    astronomical >= 0 && astronomical <= 9999
      ? digits.padStart(4, '0')
      : `${astronomical < 0 ? '-' : '+'}${digits.padStart(6, '0')}`;
  return `${year}-01-01T00:00:00Z`;
}

/** The historical year of an astronomical one: astronomical 0 is 1 BCE (-1), and -1 is 2 BCE (-2). */
function historicalYear(astronomicalYear: number): number {
  return astronomicalYear <= 0 ? astronomicalYear - 1 : astronomicalYear;
}

// The backend reads years the same way (services/voice/intent.py), so both parsers read the same phrases.
// A number may not continue another: no digit, and no digit and separator, before it or after it.
const NUMBER = String.raw`(?<!\d)(?<!\d[,.])(\d{1,3}(?:,\d{3})+|\d+)`;
const NOT_CONTINUED = String.raw`(?!\d|[,.]\d)`;
const YEARS_AGO = new RegExp(String.raw`${NUMBER}\s+years?\s+ago\b`);
const A_THOUSAND_YEARS_AGO = /(?<!half\s)\ba\s+thousand\s+years?\s+ago\b/;
const YEARS_FROM_NOW = new RegExp(String.raw`${NUMBER}\s+years?\s+from\s+now\b`);
const YEAR_THEN_ERA = new RegExp(String.raw`${NUMBER}\s*(bce|bc|ce|ad)\b`);
const ERA_THEN_YEAR = new RegExp(String.raw`\b(?:ad|ce)\s+${NUMBER}${NOT_CONTINUED}`);
const YEAR_WORD = new RegExp(String.raw`\b(?:year|epoch)\s+(-?)${NUMBER}${NOT_CONTINUED}`);
/**
 * Words that count years. Only "a thousand years ago" is read from words; any other count right before
 * "years ago" or "years from now" is refused, never misread. "how many years ago..." asks, it does not count.
 * The digit branch starts only where a run of digits and separators starts, so a long run is searched in
 * linear time: a branch that could start at every digit of the run would retry it from each one.
 */
const COUNT_WORDS = [
  'a', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
  'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty', 'thirty',
  'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety', 'hundred', 'hundreds', 'thousand', 'thousands',
  'million', 'millions', 'billion', 'billions', 'few', 'several', 'couple', 'dozen', 'dozens', 'some', 'of',
];
const COUNTED_YEARS = new RegExp(
  String.raw`(?:(?<![\d.,])[.,]*\d[\d.,]*|\b(?:${COUNT_WORDS.join('|')}|(?<!how\s)many))\s+years?\s+(?:ago|from\s+now)\b`
);
/** JavaScript's numbers hold every integer of up to 15 digits exactly: a longer number is never read or quoted. */
const MAX_YEAR_DIGITS = 15;
const NO_YEAR_ZERO = 'There is no year 0: 1 BCE is followed by 1 CE.';
const NOT_IN_DIGITS = 'The number of years must be a whole number in digits, as in 2000 years ago.';

/** What a request says of a year: the historical year, or why it names one it cannot give. */
type SaidYear = number | 'year_zero' | 'too_long' | 'not_in_digits';

/** The number `digits` says, or undefined past 15 significant digits. */
function wholeNumber(digits: string): number | undefined {
  const significant = digits.replace(/,/g, '').replace(/^0+/, '');
  return significant.length > MAX_YEAR_DIGITS ? undefined : Number.parseInt(significant || '0', 10);
}

function signed(year: number | undefined, negative: boolean): SaidYear {
  if (year === undefined) return 'too_long';
  if (year === 0) return 'year_zero';
  return negative ? -year : year;
}

/**
 * The historical year a lower-cased request names, a marker for one it names but cannot give, or
 * undefined when it names none. "N years ago" counts back from `currentYear`, across 1 BCE to 1 CE. Era
 * words count only as whole words, so the "ce" in "space" or "since" names no year. The markers: year 0,
 * a number past 15 digits, and years counted in anything but one whole number in digits.
 */
function saidYear(text: string, currentYear: number): SaidYear | undefined {
  let match = text.match(YEARS_AGO);
  if (match?.[1]) {
    const years = wholeNumber(match[1]);
    return years === undefined ? 'too_long' : historicalYear(currentYear - years);
  }
  if (A_THOUSAND_YEARS_AGO.test(text)) return historicalYear(currentYear - 1000);
  match = text.match(YEARS_FROM_NOW);
  if (match?.[1]) {
    const years = wholeNumber(match[1]);
    return years === undefined ? 'too_long' : currentYear + years;
  }
  match = text.match(YEAR_THEN_ERA);
  if (match?.[1] && match[2]) return signed(wholeNumber(match[1]), match[2].startsWith('bc'));
  match = text.match(ERA_THEN_YEAR);
  if (match?.[1]) return signed(wholeNumber(match[1]), false);
  match = text.match(YEAR_WORD);
  if (match?.[2]) return signed(wholeNumber(match[2]), match[1] === '-');
  if (COUNTED_YEARS.test(text)) return 'not_in_digits';
  return undefined;
}

function describeYear(yearCE: number): string {
  return yearCE < 0 ? `${-yearCE} BCE` : `${yearCE} CE`;
}

/** Why a year is outside the contract's range; a year past 15 digits is not quoted. */
function outOfRange(yearCE: number | undefined): string {
  const range = `${describeYear(EPOCH_MIN_YEAR_CE)} to ${describeYear(EPOCH_MAX_YEAR_CE)}`;
  if (yearCE === undefined || Math.abs(yearCE) >= 10 ** MAX_YEAR_DIGITS) {
    return `The year is outside the range of a space.epoch year, ${range}.`;
  }
  return `Year ${describeYear(yearCE)} is outside the range of a space.epoch year, ${range}.`;
}

/** The space.epoch year a said year gives, or the parser's words for why it gives none. */
function epochYear(said: SaidYear): { yearCE: number } | { refusal: string } {
  if (said === 'year_zero') return { refusal: NO_YEAR_ZERO };
  if (said === 'not_in_digits') return { refusal: NOT_IN_DIGITS };
  if (said === 'too_long') return { refusal: outOfRange(undefined) };
  if (said < EPOCH_MIN_YEAR_CE || said > EPOCH_MAX_YEAR_CE) return { refusal: outOfRange(said) };
  return { yearCE: said };
}

/** True when `name` appears in the lower-cased utterance as a whole word, so the Moon is not in "honeymoon". */
function namesWholeWord(normalizedUtterance: string, name: string): boolean {
  const needle = name.trim().toLowerCase();
  if (!needle) return false;
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![a-z0-9])${escaped}(?![a-z0-9])`).test(normalizedUtterance);
}

/**
 * Builds a schema-validated `ActionContext` from the current `ObservationSession`
 * and optional contemporaneous pointer telemetry.
 */
export function buildActionContext(
  session: ObservationSession,
  recentPointer?: ActionContext['recentPointer']
): ActionContext {
  return ActionContextSchema.parse({
    sessionId: session.id,
    sourceAssetId: session.sourceAsset?.id ?? EMPTY_SOURCE_ASSET_ID,
    sourceTimeSec: session.currentSourceTimeSec,
    navigationState: session.machines.navigation,
    calibrationState: session.machines.calibration,
    selectedFindingId: session.selectedFindingId,
    focusedFindingId: session.focusedFindingId,
    recentPointer,
    simulationEpochISO: session.spatialView.simulationEpochISO,
    viewRevision: session.viewRevision,
    reducedMotion: session.reducedMotion,
  });
}

export function isPointerFreshAndMatchingRevision(
  pointer: NonNullable<ActionContext['recentPointer']> | undefined,
  context: ActionContext
): pointer is NonNullable<ActionContext['recentPointer']> {
  if (!pointer || pointer.viewRevision !== context.viewRevision) return false;
  if (!Number.isFinite(pointer.atTimestampMs) || pointer.atTimestampMs < 0) return false;
  if (pointer.atTimestampMs >= 1_000_000_000_000) {
    const ageMs = Date.now() - pointer.atTimestampMs;
    return ageMs <= MAX_POINTER_RECENCY_MS && ageMs >= -60_000;
  }
  return context.sourceTimeSec * 1000 - pointer.atTimestampMs <= MAX_POINTER_RECENCY_MS;
}

/**
 * Names a stargazer says for a star the IAU star-name table names otherwise, each the IAU name a solved
 * finding carries as its title. Kept equal to SPOKEN_ALIASES in services/voice/names.py by a test there.
 */
export const SPOKEN_STAR_ALIASES: Readonly<Record<string, string>> = {
  'alpha centauri': 'Rigil Kentaurus',
  'beta centauri': 'Hadar',
  agena: 'Hadar',
  'alpha crucis': 'Acrux',
  'beta crucis': 'Mimosa',
  'gamma crucis': 'Gacrux',
  'delta crucis': 'Imai',
  'epsilon crucis': 'Ginan',
  'alpha carinae': 'Canopus',
  'beta carinae': 'Miaplacidus',
  'epsilon carinae': 'Avior',
  'alpha eridani': 'Achernar',
  'alpha pavonis': 'Peacock',
  'alpha gruis': 'Alnair',
  'alpha trianguli australis': 'Atria',
};

/** Alpha, beta, gamma, delta and epsilon Crucis, by their IAU names. */
export const SOUTHERN_CROSS_STARS = ['Acrux', 'Mimosa', 'Gacrux', 'Imai', 'Ginan'] as const;
const SOUTHERN_CROSS_NAMES = ['southern cross', 'crux'];
const SOUTHERN_CROSS_QUESTION = `The Southern Cross is a constellation, not one star. Which of its stars did you mean: ${SOUTHERN_CROSS_STARS.join(', ')}?`;

/**
 * Southern showpieces voice cannot reach yet (deep-sky rows, or no named row), by the names people say. Kept equal to
 * UNBAKED_OBJECTS in services/voice/names.py by a test there.
 */
export const UNBAKED_OBJECTS: Readonly<Record<string, readonly string[]>> = {
  'the Large Magellanic Cloud': ['large magellanic cloud', 'lmc'],
  'the Small Magellanic Cloud': ['small magellanic cloud', 'smc'],
  'the Magellanic Clouds': ['magellanic clouds'],
  'Omega Centauri': ['omega centauri', 'omega cen'],
  '47 Tucanae': ['47 tucanae', '47 tuc'],
  'the Carina Nebula': ['carina nebula', 'eta carinae nebula'],
  'Eta Carinae': ['eta carinae', 'eta car'],
  'the Jewel Box': ['jewel box', 'kappa crucis cluster'],
  'the Coalsack': ['coalsack', 'coal sack'],
};

/** IAU names of the bright southern stars the parser knows even when the sky solution does not hold them. */
const SOUTHERN_STAR_NAMES = [
  ...new Set([...Object.values(SPOKEN_STAR_ALIASES), ...SOUTHERN_CROSS_STARS, 'Rigil Kentaurus', 'Toliman', 'Hadar']),
];

function unbakedObjectIn(normalizedUtterance: string): string | undefined {
  return Object.entries(UNBAKED_OBJECTS).find(([, spoken]) =>
    spoken.some((name) => namesWholeWord(normalizedUtterance, name))
  )?.[0];
}

/**
 * The row of the baked OpenNGC table (apps/web/public/catalog/deepsky/openngc.json) that holds each object above,
 * or null when no baked table holds it by name; kept equal to UNBAKED_DEEP_SKY_ROWS in services/voice/names.py.
 */
export const UNBAKED_DEEP_SKY_ROWS: Readonly<Record<string, string | null>> = {
  'the Large Magellanic Cloud': 'ESO056-115',
  'the Small Magellanic Cloud': 'NGC0292',
  'the Magellanic Clouds': null,
  'Omega Centauri': 'NGC5139',
  '47 Tucanae': 'NGC0104',
  'the Carina Nebula': 'NGC3372',
  'Eta Carinae': null,
  'the Jewel Box': 'NGC4755',
  'the Coalsack': 'C099',
};

/** Why voice cannot act on this object: where the baked data holds it, or that no table holds it by name. */
export function unbakedRefusal(label: string): string {
  const subject = `${label.charAt(0).toUpperCase()}${label.slice(1)}`;
  const row = UNBAKED_DEEP_SKY_ROWS[label];
  if (row) {
    return `${subject} is in the app's deep-sky catalogue (OpenNGC ${row}), but voice does not reach deep-sky objects yet, so it cannot be selected, described or flown to.`;
  }
  return `${subject} has no named row in the app's catalogues yet, so it cannot be selected, described or flown to by name.`;
}

/** The IAU name of a known southern star the utterance names, by that name or a spoken alias. */
function southernStarNamedIn(normalizedUtterance: string): string | undefined {
  const alias = Object.entries(SPOKEN_STAR_ALIASES).find(([spoken]) => namesWholeWord(normalizedUtterance, spoken));
  if (alias) return alias[1];
  return SOUTHERN_STAR_NAMES.find((name) => namesWholeWord(normalizedUtterance, name));
}

function findNamedFindingInUtterance(
  normalizedUtterance: string,
  session: ObservationSession
): CelestialFinding | undefined {
  const aliased = Object.entries(SPOKEN_STAR_ALIASES).find(([spoken]) => namesWholeWord(normalizedUtterance, spoken));
  if (aliased) {
    const finding = (session.observationSolution?.findings ?? []).find((f) => f.title === aliased[1]);
    if (finding) return finding;
  }
  for (const finding of session.observationSolution?.findings ?? []) {
    if (namesWholeWord(normalizedUtterance, finding.title)) return finding;
    const idShort = finding.id.replace(/^finding_/, '');
    if (idShort.length >= 3 && namesWholeWord(normalizedUtterance, idShort)) return finding;
    if (finding.catalogRef?.bayerDesignation && namesWholeWord(normalizedUtterance, finding.catalogRef.bayerDesignation)) {
      return finding;
    }
    if (finding.catalogRef?.catalogSourceId && namesWholeWord(normalizedUtterance, finding.catalogRef.catalogSourceId)) {
      return finding;
    }
  }
  return undefined;
}

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  reason?: RejectionReason;
}

/** The brightest family: services/voice/intent.py refuses the same words, since it never sees the fluxes. */
export const BRIGHTEST_PATTERN = /\bbrightest\b/i;
/** A rank past the first: only the single brightest is named. */
const NTH_BRIGHTEST = /\b(?:second|third|fourth|fifth|next|\d+(?:st|nd|rd|th))[\s-]+brightest\b/i;
/**
 * The first place word after "brightest" names somewhere other than the photo itself ("in Orion", "in the
 * sky", "of the Southern Cross", "on the left"): the parser ranks only what was measured in the photo.
 */
const BRIGHTEST_ELSEWHERE =
  /\bbrightest\b(?:(?!\b(?:in|of|among|within|on|near|at)\b).)*\b(?:in|of|among|within|on|near|at)\s+(?!(?:(?:my|the|this|your)\s+)?(?:photo|picture|image|frame|shot|video|recording|view|screen|capture|it|all|them|these|those)\b)/i;
/**
 * Kinds of body the photo's measured brightness does not rank: the ranking covers stars and points of light only.
 * services/voice/intent.py refuses the same words with the same reason (a test keeps the lists equal).
 */
export const BRIGHTEST_UNRANKED_KINDS = [
  'planet', 'planets', 'moon', 'moons', 'galaxy', 'galaxies', 'nebula', 'nebulae', 'nebulas', 'cluster', 'clusters',
  'deep', 'deep-sky', 'comet', 'comets', 'asteroid', 'asteroids', 'satellite', 'satellites', 'constellation', 'constellations',
];
/** Words that may follow "brightest" for the parser to rank: a star or an unqualified word, or where to look. */
const BRIGHTEST_RANKED_WORDS = new Set([
  'star', 'stars', 'object', 'objects', 'point', 'points', 'light', 'lights', 'thing', 'things', 'one', 'ones', 'source', 'sources',
  'in', 'on', 'of', 'among', 'within', 'near', 'at', 'here', 'there', 'now',
]);
/** The word right after "brightest", if any. */
const BRIGHTEST_NEXT_WORD = /\bbrightest\b[\s-]*([a-z][a-z-]*)?/i;

function mediaWord(session: ObservationSession): 'photo' | 'video' {
  return session.sourceAsset?.mediaKind === 'video' ? 'video' : 'photo';
}

function joinedNames(names: string[]): string {
  return names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/**
 * The finding measured brightest in the source's own pixels: the largest sourceDetection flux among observed
 * and catalog-matched findings measured on the source asset, in the frame nearest the current time. Catalog
 * magnitudes and the selection play no part. When others agree with the top flux within the combined
 * measurement uncertainty (each flux / its SNR), the parser asks which one rather than choosing.
 */
function brightestMeasuredFinding(session: ObservationSession): TargetResolutionResult {
  const kind = mediaWord(session);
  const sourceId = session.sourceAsset?.id;
  const measured = (session.observationSolution?.findings ?? []).filter(
    (f): f is CelestialFinding & { sourceDetection: NonNullable<CelestialFinding['sourceDetection']> } =>
      (f.evidenceClass === 'observed' || f.evidenceClass === 'catalog_matched') &&
      f.sourceDetection !== undefined &&
      f.sourceDetection.assetId === sourceId
  );
  if (sourceId === undefined || measured.length === 0) {
    return {
      error: `No finding in this sky solution carries a brightness measured from the ${kind}'s pixels, so none can be named the brightest.`,
      reason: 'no_grounded_answer',
    };
  }
  const now = session.currentSourceTimeSec;
  const frame = measured
    .map((f) => f.sourceDetection.frameTimeSec)
    .reduce((best, t) => {
      const gap = Math.abs(t - now);
      const bestGap = Math.abs(best - now);
      return gap < bestGap || (gap === bestGap && t < best) ? t : best;
    });
  const ranked = measured
    .filter((f) => f.sourceDetection.frameTimeSec === frame)
    .sort((a, b) => b.sourceDetection.flux - a.sourceDetection.flux || a.id.localeCompare(b.id));
  const top = ranked[0]!;
  const sigma = (f: (typeof ranked)[number]) => f.sourceDetection.flux / f.sourceDetection.snr;
  const level = ranked.filter((f) => top.sourceDetection.flux - f.sourceDetection.flux <= Math.hypot(sigma(top), sigma(f)));
  if (level.length > 1) {
    return {
      disambiguation: {
        question: `${joinedNames(level.map((f) => f.title))} are equally bright in this ${kind} within their measurement uncertainty. Which did you mean?`,
        choices: level.map((f) => ({ id: f.id, label: f.title })),
      },
    };
  }
  return { findingId: top.id };
}

function brightestTarget(normalizedUtterance: string, session: ObservationSession): TargetResolutionResult {
  const kind = mediaWord(session);
  const next = BRIGHTEST_NEXT_WORD.exec(normalizedUtterance)?.[1]?.toLowerCase();
  if (next !== undefined && BRIGHTEST_UNRANKED_KINDS.includes(next)) {
    return {
      error: `Brightness measured in this ${kind} ranks stars and points of light only, so a brightest planet, moon, galaxy, nebula or cluster is not named.`,
      reason: 'no_grounded_answer',
    };
  }
  if (next !== undefined && !BRIGHTEST_RANKED_WORDS.has(next)) {
    return {
      error: `Only the brightest star measured in this ${kind} can be named, by measured brightness alone, so "brightest ${next}" is not answered.`,
      reason: 'no_grounded_answer',
    };
  }
  if (NTH_BRIGHTEST.test(normalizedUtterance)) {
    return {
      error: `Only the single brightest star measured in this ${kind} can be named, not the second or a later one.`,
      reason: 'no_grounded_answer',
    };
  }
  if (BRIGHTEST_ELSEWHERE.test(normalizedUtterance)) {
    return {
      error: `Only the brightest star measured in this ${kind} can be named; a brightest in the sky, a constellation or a part of the frame is not ranked.`,
      reason: 'no_grounded_answer',
    };
  }
  return brightestMeasuredFinding(session);
}

function resolveTargetFinding(
  normalizedUtterance: string,
  context: ActionContext,
  session: ObservationSession
): TargetResolutionResult {
  // "Brightest" asks for a ranking, so it is answered from the measurements even when a star is named
  if (BRIGHTEST_PATTERN.test(normalizedUtterance)) return brightestTarget(normalizedUtterance, session);

  const named = findNamedFindingInUtterance(normalizedUtterance, session);
  if (named) return { findingId: named.id };

  // A southern object or star the request names, but the sky solution does not hold, never falls through
  // to the pointer or the selection
  const unbaked = unbakedObjectIn(normalizedUtterance);
  if (unbaked) return { error: unbakedRefusal(unbaked), reason: 'no_grounded_answer' };
  if (SOUTHERN_CROSS_NAMES.some((name) => namesWholeWord(normalizedUtterance, name))) {
    const findings = session.observationSolution?.findings ?? [];
    const choices = SOUTHERN_CROSS_STARS.flatMap((name) => {
      const finding = findings.find((f) => f.title === name);
      return finding ? [{ id: finding.id, label: name }] : [];
    });
    if (choices.length > 0) return { disambiguation: { question: SOUTHERN_CROSS_QUESTION, choices } };
    return {
      error: `The Southern Cross is a constellation, not one star, and none of its stars (${SOUTHERN_CROSS_STARS.join(', ')}) is in the sky solution.`,
      reason: 'ambiguous_target',
    };
  }
  const southernStar = southernStarNamedIn(normalizedUtterance);
  if (southernStar) {
    return { error: `${southernStar} is not among the findings in this sky solution.`, reason: 'ambiguous_target' };
  }

  const pointerFresh = isPointerFreshAndMatchingRevision(context.recentPointer, context);

  if (pointerFresh && context.recentPointer?.overlappingCandidateIds && context.recentPointer.overlappingCandidateIds.length > 1) {
    const findingMap = new Map((session.observationSolution?.findings ?? []).map((f) => [f.id, f]));
    const choices = context.recentPointer.overlappingCandidateIds.map((id) => ({
      id,
      label: findingMap.get(id)?.title ?? id,
    }));
    return {
      disambiguation: {
        question: `Multiple celestial objects overlap near your pointer. Did you mean ${choices.map((c) => c.label).join(' or ')}?`,
        choices,
      },
    };
  }

  if (pointerFresh && context.recentPointer) {
    if (context.recentPointer.nearestFindingId) return { findingId: context.recentPointer.nearestFindingId };
    if (context.recentPointer.overlappingCandidateIds?.length === 1) return { findingId: context.recentPointer.overlappingCandidateIds[0] };
  }

  if (context.selectedFindingId) return { findingId: context.selectedFindingId };
  if (context.focusedFindingId) return { findingId: context.focusedFindingId };

  // Nothing else stands in: not the solution's only finding, and not any default star
  if (context.calibrationState === 'unresolved') {
    return {
      error: 'The sky is unresolved, so no finding in it can be named, selected or pointed at yet.',
      reason: 'unresolved_sky',
    };
  }
  return {
    error: 'No finding in the sky solution is named in the request, and none is selected, focused or pointed at.',
    reason: 'ambiguous_target',
  };
}

/**
 * Translates natural-language voice or text utterances into schema-validated `AlbedianAction`
 * payloads bound to `context.viewRevision`, or returns a structured `disambiguation`, or an `error`
 * with its contract `reason`. A request no rule matches becomes `question.ask`.
 */
export function parseUtteranceToAction(
  utterance: string,
  context: ActionContext,
  session: ObservationSession,
  options: VoiceParseOptions = {}
): VoiceParseResult {
  const parsed = parseWithRules(utterance, context, session, options);
  // An action on the brightest says that its target was ranked by measured flux
  const action = parsed.action;
  if (action && 'findingId' in action && BRIGHTEST_PATTERN.test(utterance)) {
    if (brightestMeasuredFinding(session).findingId === action.findingId) {
      return { ...parsed, note: `Brightest by measured flux in this ${mediaWord(session)}:` };
    }
  }
  return parsed;
}

function parseWithRules(
  utterance: string,
  context: ActionContext,
  session: ObservationSession,
  options: VoiceParseOptions
): VoiceParseResult {
  const trimmed = utterance.trim();
  if (!trimmed) return { error: 'Empty voice or text command.', reason: 'no_grounded_answer' };

  const norm = trimmed.toLowerCase();
  const requestId = `req_${context.viewRevision}_${deterministicHexDigest(`${trimmed}:${context.viewRevision}`).slice(0, 8)}`;
  const currentYear = (options.now ?? new Date()).getUTCFullYear();

  if (/\b(fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b/i.test(norm)) {
    return {
      error: 'Unsafe synthetic edit rejected: Albedian preserves authentic observations and never fabricates sky content.',
      reason: 'unsafe_synthetic_edit',
    };
  }

  if (/^(stop|cancel|wait|halt|abort|freeze)\b/i.test(norm) || /\b(stop|cancel)\s+(navigation|transition|flight)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({ type: 'navigation.cancel', requestId, expectedViewRevision: context.viewRevision, transitionId: session.spatialView.activeTransitionId }),
    };
  }

  if (/\b(back a little|back a bit|step back slightly|slightly back|partial back)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({ type: 'history.back', requestId, expectedViewRevision: context.viewRevision, mode: 'partial', fraction: 0.35 }),
    };
  }

  if (/\b(take me back|back to moment|back to the moment|return to source|back to source|back to recording|back to photo|back to video|go back|return home)\b/i.test(norm) || norm === 'back') {
    if (/\b(previous checkpoint|last checkpoint|checkpoint back)\b/i.test(norm)) {
      return { action: AlbedianActionSchema.parse({ type: 'history.back', requestId, expectedViewRevision: context.viewRevision, mode: 'checkpoint' }) };
    }
    return {
      action: AlbedianActionSchema.parse({ type: 'source.return', requestId, expectedViewRevision: context.viewRevision, checkpointId: session.navigationHistory.anchorMomentCheckpoint.id, restoreAudio: true }),
    };
  }

  const said = saidYear(norm, currentYear);
  if (said !== undefined) {
    const year = epochYear(said);
    if ('refusal' in year) return { error: year.refusal, reason: 'invalid_epoch' };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.epoch',
        requestId,
        expectedViewRevision: context.viewRevision,
        epochISO: epochIsoForYear(year.yearCE),
        yearCE: year.yearCE,
      }),
    };
  }

  if (/\b(where is earth|earth direction|point to earth|direction to earth|home vector)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'earthDirectionVector', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(constellation lines|constellations overlay|star lines)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'constellationLines', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(residual vectors|residuals)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'residualVectors', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(compare|show original|original recording|before and after|a\/b wipe|split view)\b/i.test(norm)) {
    const originalVariant = session.mediaVariants[0]?.id ?? 'variant_original';
    const restoredVariant = session.activeVariantId !== originalVariant ? session.activeVariantId : (session.mediaVariants[1]?.id ?? originalVariant);
    return { action: AlbedianActionSchema.parse({ type: 'media.compare', requestId, expectedViewRevision: context.viewRevision, variantAId: originalVariant, variantBId: restoredVariant, preserveTime: true }) };
  }

  if (/\b(make the sky clearer|sky clearer|don't change the people|do not change the people|preserve the people|temporal stack|motion-compensated|denoise|clean up noise|restore|bring out faint stars)\b/i.test(norm)) {
    const profile: 'natural' | 'temporal' | 'star_protected' = /\b(temporal|stack|faint)\b/i.test(norm) ? 'temporal' : 'star_protected';
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.restore',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceAssetId: session.sourceAsset?.id ?? EMPTY_SOURCE_ASSET_ID,
        profile,
        constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
      }),
    };
  }

  // As the backend does: a southern object, the Southern Cross, or a southern star the sky solution lacks
  // is answered here, before a later rule could read the request as a question or take the selection
  if (
    !findNamedFindingInUtterance(norm, session) &&
    (unbakedObjectIn(norm) !== undefined ||
      SOUTHERN_CROSS_NAMES.some((name) => namesWholeWord(norm, name)) ||
      southernStarNamedIn(norm) !== undefined)
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
  }

  const seekMatch = norm.match(/\b(?:seek|jump|scrub)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b/i);
  if (seekMatch?.[1]) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.seek',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceTimeSec: Number.parseFloat(seekMatch[1]),
        preserveAudioSync: true,
      }),
    };
  }

  if (/\b(what were we looking at|what was overhead|show me what was overhead|identify the stars|calibrate|register the sky|show sky|what is in the sky|match the sky)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'sky.show',
        requestId,
        expectedViewRevision: context.viewRevision,
        mode: context.calibrationState === 'calibrated' ? 'measured' : 'predicted',
        ...(session.observationSolution ? { observationSolutionId: session.observationSolution.id } : {}),
      }),
    };
  }

  if (/\b(another viewpoint|different viewpoint|orbit|another angle|from the side|translate viewpoint|shift viewpoint)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.viewpoint',
        requestId,
        expectedViewRevision: context.viewRevision,
        targetFindingId: resolved.findingId,
        units: 'pc',
        offsetParsec: [12, 8, -5],
        trajectory: /\b(translate|shift)\b/i.test(norm) ? 'translate' : 'orbit',
      }),
    };
  }

  if (/\b(how far|distance|light years|parsecs|how distant)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['distance', 'evidence'],
      }),
    };
  }

  if (/\b(what's behind|what is behind|what lies beyond|behind it|beyond it|behind|beyond|behind sightline|along this sightline)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['behind_sightline'],
      }),
    };
  }

  if (/\b(take me there|fly there|go there|go to|explore|fly to|travel to|enter space|enter 3d|zoom into space|take me to)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.enter',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        distancePolicy: 'measured_or_estimated',
      }),
    };
  }

  if (
    /\b(what's that|what is that|what star is that|which star|that one|this one|select|focus on|inspect|point at|look at|tell me about)\b/i.test(norm) ||
    BRIGHTEST_PATTERN.test(norm) ||
    (!/\bhow\s+many\b/i.test(norm) && Boolean(findNamedFindingInUtterance(norm, session)))
  ) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.select',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
      }),
    };
  }

  // No rule matches: a question, which the reducer refuses until the M3 tools can answer it from the user's data
  if (trimmed.length > QUESTION_MAX_UTF16) {
    return {
      error: 'No command matches the request, and it is longer than a question can hold.',
      reason: 'no_grounded_answer',
    };
  }
  return {
    action: AlbedianActionSchema.parse({
      type: 'question.ask',
      requestId,
      expectedViewRevision: context.viewRevision,
      question: trimmed,
    }),
  };
}

export const resolveVoiceUtterance = parseUtteranceToAction;

/**
 * End-to-end voice/text command executor.
 */
export function executeVoiceOrTextCommand(
  session: ObservationSession,
  utterance: string,
  modality: 'voice' | 'text' | 'pointer' = 'voice',
  recentPointer?: ActionContext['recentPointer'],
  options: VoiceParseOptions = {}
): {
  session: ObservationSession;
  outcome: ActionOutcome;
  voiceActionRecord: VoiceAction;
} {
  const context = buildActionContext(session, recentPointer);
  const parsed = parseUtteranceToAction(utterance, context, session, options);
  const actionRecordId = `voice_act_${session.viewRevision}_${session.voiceActions.length + 1}`;

  const voiceActionRecord: VoiceAction = VoiceActionSchema.parse({
    id: actionRecordId,
    utterance: utterance.trim() || '(empty)',
    modality,
    context,
    proposedAction: parsed.action,
    createdAtUTC: (options.now ?? new Date()).toISOString(),
  });

  if (parsed.disambiguation) {
    return {
      session: {
        ...session,
        machines: { ...session.machines, voice: 'awaiting_tool' },
        voiceActions: [...session.voiceActions, voiceActionRecord],
        lastStatusBanner: parsed.disambiguation.question,
      },
      outcome: ActionOutcomeSchema.parse({
        status: 'needs_input',
        actionId: actionRecordId,
        question: parsed.disambiguation.question,
        choices: parsed.disambiguation.choices,
      }),
      voiceActionRecord,
    };
  }

  if (!parsed.action) {
    const errText = parsed.error ?? 'Could not resolve voice or text command.';
    // The parser's own reason, never one guessed from the wording of its message
    const reason = parsed.reason ?? 'ambiguous_target';
    const nextActions =
      reason === 'invalid_epoch'
        ? [`Choose a year from ${describeYear(EPOCH_MIN_YEAR_CE)} to ${describeYear(EPOCH_MAX_YEAR_CE)}`]
        : ['Select a star on screen, or name one the sky solution found', 'Ask "What were we looking at?"', 'Say "Back to moment"'];

    return {
      session: {
        ...session,
        voiceActions: [...session.voiceActions, voiceActionRecord],
        lastStatusBanner: errText,
      },
      outcome: ActionOutcomeSchema.parse({
        status: 'rejected',
        actionId: actionRecordId,
        reason,
        explanation: errText,
        nextActions,
      }),
      voiceActionRecord,
    };
  }

  const dispatched = dispatchAlbedianAction(session, parsed.action);
  const noted = parsed.note && dispatched.outcome.status === 'completed';
  return {
    session: {
      ...dispatched.session,
      voiceActions: [...dispatched.session.voiceActions, voiceActionRecord],
      ...(noted ? { lastStatusBanner: `${parsed.note} ${dispatched.session.lastStatusBanner}` } : {}),
    },
    outcome: dispatched.outcome,
    voiceActionRecord,
  };
}
