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
import { CATALOG_STAR_NAMES, EVERYDAY_WORD_NAMES, NAME_CUE_VERBS } from './generated/catalogStarNames.js';

/**
 * ============================================================================
 * @albedian/observation: Context-Bound Voice Utterance Parser & Deictic Resolver
 * ============================================================================
 *
 * The parser says only what the request and the view hold. A request is about what it names: a target comes
 * from a name, matched as a whole word, and from what is selected, focused or pointed at only when the request
 * points at it or names nothing at all. Every thing it names must be in this sky: a name this sky does not hold,
 * or a word the parser did not understand, stops the request, whatever else it names. Years count from the clock.
 * A request no rule matches is a question, and every refusal carries its own reason and says only what the
 * parser can know.
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

/**
 * The bodies the backend predicts for a recording's time and place (PREDICTED_BODIES in
 * services/science/predicted_sky.py; tests/integration/test_voice_named_targets.py keeps the two equal), each by
 * the name a refusal writes for it. A solution holds one as finding-predicted-<body>.
 */
const PREDICTED_BODY_NAMES: Readonly<Record<string, string>> = {
  sun: 'the Sun',
  moon: 'the Moon',
  venus: 'Venus',
  mars: 'Mars',
  jupiter: 'Jupiter',
  saturn: 'Saturn',
  uranus: 'Uranus',
};

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const EVERYDAY_WORDS = new Set(EVERYDAY_WORD_NAMES);
const NAME_CUES = NAME_CUE_VERBS.map(escapeRegExp).join('|');

/**
 * How a request names a star, as services/voice/names.py name_pattern does. A plain name is a whole word, with the
 * name first in the pattern so the regex engine skips straight to where it occurs. A name that is also an everyday
 * word (ran, fang, atlas) names a star only after a naming verb, then "the" and "star" if said, or with "star" right
 * before or after it, so "zoom in, I ran out of room" names no star.
 */
function starNamePattern(name: string): RegExp {
  const lower = name.trim().toLowerCase();
  const literal = escapeRegExp(lower);
  if (!EVERYDAY_WORDS.has(lower)) return new RegExp(`${literal}(?<![a-z0-9]${literal})(?![a-z0-9])`, 'g');
  return new RegExp(
    `(?:(?<![a-z0-9])(?:${NAME_CUES})\\s+(?:the\\s+)?(?:star\\s+)?|(?<![a-z0-9])star\\s+)${literal}(?![a-z0-9])` +
      `|(?<![a-z0-9])${literal}\\s+star(?![a-z0-9])`,
    'g'
  );
}

/**
 * ---- Words that name nothing ----
 * A request is about what it names. Once its names and its command are cut out, each word left is one of three
 * kinds, and any other word is one the parser did not understand. It may be a name no table holds, so the request
 * is then refused, whatever else it names: it is never read as being about what happens to be selected, or about
 * a name said beside it. services/voice/names.py holds the same three lists
 * (tests/integration/test_voice_named_targets.py keeps them equal), so both parsers read a sentence the same way.
 */

/** Words that point at what is selected, focused or pointed at. */
const POINTING_WORDS = [
  'that', 'this', 'it', 'its', 'there', 'here', 'these', 'those', 'them', 'selected', 'selection', 'current',
];

/**
 * Words that say something about a thing without naming it. They stand only beside a pointing word ("that bright
 * one", "how far is it in light years"); without one, the request does not say which thing it means.
 */
const DESCRIBING_WORDS = [
  'one', 'ones', 'star', 'stars', 'thing', 'things', 'object', 'objects', 'body', 'bodies', 'dot', 'dots', 'point',
  'points', 'light', 'lights', 'spot', 'spots', 'patch', 'patches', 'blob', 'blobs', 'speck', 'smudge', 'streak',
  'glow', 'marker', 'markers', 'planet', 'planets', 'galaxy', 'galaxies', 'nebula', 'nebulae', 'nebulas', 'cluster',
  'clusters', 'constellation', 'constellations', 'comet', 'comets', 'asteroid', 'asteroids', 'satellite',
  'satellites', 'meteor', 'meteors', 'plane', 'planes', 'airplane', 'aeroplane', 'bright', 'brighter', 'dim',
  'dimmer', 'faint', 'fainter', 'big', 'bigger', 'large', 'larger', 'small', 'smaller', 'little', 'tiny', 'huge',
  'red', 'reddish', 'orange', 'yellow', 'yellowish', 'white', 'whitish', 'blue', 'bluish', 'green', 'greenish',
  'fuzzy', 'blurry', 'hazy', 'glowing', 'shiny', 'twinkling', 'flickering', 'blinking', 'moving', 'left', 'top',
  'bottom', 'middle', 'center', 'centre', 'upper', 'lower', 'corner', 'edge', 'above', 'below', 'beside', 'next',
  'nearest', 'closest', 'other', 'another', 'same', 'first', 'second', 'last', 'something', 'anything', 'else',
  'distance', 'year', 'years', 'parsec', 'parsecs', 'kilometre', 'kilometres', 'kilometer', 'kilometers', 'km',
  'mile', 'miles', 'au', 'ly', 'pc', 'photo', 'photos', 'picture', 'pictures', 'image', 'images', 'video', 'videos',
  'clip', 'recording', 'shot', 'frame', 'sky', 'night', 'screen', 'view', 'app', 'solve', 'solved', 'space', '3d',
  'earth', 'home', 'orbit', 'viewpoint', 'angle', 'side', 'sightline', 'different', 'distant',
];

/**
 * Words that can be neither the thing asked about nor a description of it: grammar, politeness and the verbs a
 * request is wrapped in.
 */
const FUNCTION_WORDS = [
  'a', 'an', 'the', 'my', 'our', 'your', 'any', 'some', 'all', 'every', 'each', 'both', 'i', 'me', 'mine', 'we', 'us',
  'you', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am', 'do', 'does', 'did', 'done', 'doing', 'can',
  'could', 'will', 'would', 'shall', 'should', 'may', 'might', 'must', 'have', 'has', 'had', 'having', 'get', 'gets',
  'got', 'getting', 'let', 'lets', 's', 't', 'd', 'm', 'll', 're', 've', 'don', 'doesn', 'didn', 'isn', 'aren',
  'wasn', 'weren', 'won', 'couldn', 'wouldn', 'shouldn', 'im', 'whats', 'wheres', 'and', 'or', 'but', 'so', 'then',
  'if', 'as', 'than', 'because', 'while', 'when', 'after', 'before', 'to', 'of', 'off', 'in', 'into', 'on', 'onto',
  'at', 'from', 'for', 'with', 'without', 'by', 'through', 'across', 'around', 'about', 'near', 'between', 'like',
  'over', 'under', 'up', 'down', 'out', 'away', 'back', 'toward', 'towards', 'behind', 'beyond', 'past', 'far',
  'close', 'what', 'which', 'who', 'whose', 'where', 'why', 'how', 'please', 'pls', 'plz', 'now', 'today', 'tonight',
  'again', 'already', 'still', 'yet', 'soon', 'later', 'quickly', 'fast', 'slowly', 'straight', 'directly', 'just',
  'only', 'even', 'ever', 'really', 'very', 'quite', 'actually', 'exactly', 'roughly', 'approximately', 'nearly',
  'almost', 'maybe', 'perhaps', 'probably', 'right', 'sorry', 'ok', 'okay', 'alright', 'sure', 'yes', 'yeah', 'yep',
  'no', 'nope', 'not', 'never', 'thanks', 'thank', 'cheers', 'hey', 'hi', 'hello', 'um', 'uh', 'hmm', 'ah', 'oh',
  'wow', 'cool', 'nice', 'great', 'well', 'also', 'too', 'much', 'many', 'more', 'less', 'most', 'closer', 'nearer',
  'further', 'farther', 'deeper', 'want', 'wants', 'wanted', 'wanna', 'need', 'needs', 'love', 'think', 'thought',
  'guess', 'know', 'wonder', 'wondering', 'curious', 'mean', 'meant', 'try', 'trying', 'help', 'see', 'show', 'find',
  'locate', 'look', 'looking', 'watch', 'go', 'going', 'take', 'bring', 'travel', 'zoom', 'move', 'jump', 'head',
  'come', 'tell', 'say', 'said', 'give', 'explain', 'describe', 'identify', 'select', 'choose', 'pick', 'focus',
  'inspect', 'lock', 'open', 'enter', 'explore', 'visit', 'make', 'looks', 'seems', 'appears', 'called', 'named',
  'name', 'idea', 'info', 'information', 'details', 'mode', 'fly', 'lies', 'along', 'translate', 'shift', 'albedian',
];

/**
 * The plain words that ask. A request that asks, names nothing and points at nothing is a question about no one
 * thing ("what is an orbit?"), not a command for what is in view.
 */
const ASKING_WORDS = ['what', 'which', 'who', 'whose', 'where', 'why', 'how'];

const POINTING = new Set<string>(POINTING_WORDS);
const ASKING = new Set<string>(ASKING_WORDS);
// An everyday-word name said without its cue names no star ("I ran the solve again"): it only describes
const DESCRIBING = new Set<string>([...DESCRIBING_WORDS, ...EVERYDAY_WORD_NAMES]);
const FUNCTION = new Set<string>(FUNCTION_WORDS);
/** "there is", "are there", "there's": something exists, and nothing is pointed at. */
const EXISTS = new Set(['is', 'are', 'was', 'were']);
/**
 * The most words, and the most characters, of a request quoted back in a refusal [proposed: enough for any name,
 * short enough to read on a phone].
 */
const MAX_QUOTED_WORDS = 6;
const MAX_QUOTED_CHARACTERS = 80;

/**
 * The words of a text: runs of letters and digits of any script, as Python's [^\W_] finds them, so a name written in
 * another alphabet is a word no list holds, never nothing at all.
 */
function wordsOf(text: string): string[] {
  return text.match(/[\p{L}\p{N}]+/gu) ?? [];
}

/** `text` with the characters from `start` to `end` turned to spaces, so what is cut out no longer reads as words. */
function blank(text: string, start: number, end: number): string {
  return text.slice(0, start) + ' '.repeat(end - start) + text.slice(end);
}

/** True when a word of the text points at what is in view. "There" beside "is" or "are" points at nothing. */
function pointsAtTheView(text: string): boolean {
  const words = wordsOf(text);
  return words.some((word, index) => {
    if (!POINTING.has(word)) return false;
    if (word !== 'there') return true;
    const before = words[index - 1] ?? '';
    const after = words[index + 1] ?? '';
    return !(EXISTS.has(before) || EXISTS.has(after) || after === 's');
  });
}

/**
 * How the words of a request read once its names and its command are cut out. `points`: a word points at what is
 * in view. `describes`: a word describes a thing without naming it. `asks`: a word outside the command asks.
 * `unknown`: the first words that are none of these and not plain, as said: the words the parser did not
 * understand, which a refusal quotes; empty when there are none.
 */
interface Wording {
  points: boolean;
  describes: boolean;
  asks: boolean;
  unknown: string;
}

/**
 * Reads the words left of a lower-cased request, as services/voice/names.py read_wording does. A pointing word
 * counts wherever it stands, the command's own words included ("take me there"); the other words are judged with
 * the command cut out too, and with it the word that asks for a ranking, which the ranking rule reads.
 */
function readWording(withoutNames: string, command: RegExp | undefined): Wording {
  const cutOut = (cut: string) => ' '.repeat(cut.length);
  const rest = (command ? withoutNames.replace(new RegExp(command.source, 'gi'), cutOut) : withoutNames).replace(
    RANKING_WORD,
    cutOut
  );
  const words = wordsOf(rest);
  const kinds = words.map((word) =>
    FUNCTION.has(word) || POINTING.has(word) ? 'plain' : DESCRIBING.has(word) ? 'describes' : 'unknown'
  );
  let unknown = '';
  const at = kinds.indexOf('unknown');
  if (at >= 0) {
    // Only the words that stopped the reading: the first unknown word and the unknown words right after it, so
    // "the andromeda galaxy" quotes "andromeda", and "the milky way" quotes "milky way"
    let last = at;
    while (last + 1 < kinds.length && kinds[last + 1] === 'unknown') last += 1;
    const phrase = words.slice(at, last + 1);
    // Counted in characters, as Python counts them, never in UTF-16 units: no letter is cut in half
    const quoted = Array.from(phrase.slice(0, MAX_QUOTED_WORDS).join(' '));
    const cut = phrase.length > MAX_QUOTED_WORDS || quoted.length > MAX_QUOTED_CHARACTERS;
    unknown = quoted.slice(0, MAX_QUOTED_CHARACTERS).join('') + (cut ? ' ...' : '');
  }
  return {
    points: pointsAtTheView(withoutNames),
    describes: kinds.includes('describes'),
    asks: words.some((word) => ASKING.has(word)),
    unknown,
  };
}

/** One name a request can say for something in this sky solution, or for a star or body it does not hold. */
interface SpokenName {
  /** The name in lower case: a request that does not hold these letters does not say it. */
  spoken: string;
  /** How a request says the name: a whole word, or for an everyday word only when cued. */
  pattern: RegExp;
  /** The findings of this solution the name stands for; none when the solution does not hold the star or body. */
  ids: string[];
  /** The name a refusal writes for a star or body the solution does not hold, as its table writes it. */
  absent?: string;
}

/** How a request says each star name the backend knows (generated/catalogStarNames.ts, from services/voice/names.py). */
const CATALOG_PATTERNS = new Map(CATALOG_STAR_NAMES.map(([spoken]) => [spoken, starNamePattern(spoken)]));
/** How a request says a star name that is also an everyday word where it needs no cue: as a whole word. */
const EVERYDAY_BARE = new Map(
  EVERYDAY_WORD_NAMES.map((spoken) => [spoken, new RegExp(`(?<![a-z0-9])${escapeRegExp(spoken)}(?![a-z0-9])`, 'g')])
);
const NO_FINDINGS: readonly CelestialFinding[] = [];
const SPOKEN_NAMES = new WeakMap<readonly CelestialFinding[], SpokenName[]>();
/**
 * The ids the backend gives what it predicts for a recording's time and place: finding-predicted-<body>
 * (services/science/ephemeris.py) and finding-predicted-star-<name> (services/science/predicted_sky.py).
 */
const PREDICTED_ID = /^finding-predicted-(?:star-)?(.+)$/;

/**
 * Every name a request can say for a finding of this solution, the longest first so "Deneb Algedi" is read before
 * "Deneb" and "Algedi", with the catalog stars and the predicted bodies the solution does not hold.
 *
 * A name stands first for the record the backend itself gives that name: the solved star of the generated name
 * table, a spoken alias of its IAU name, or the body or star a finding-predicted id spells out. Only a name no
 * record carries that way is read from what a finding's title and catalog row say: the whole title, the
 * title up to its bracket ("Moon" of "Moon (Predicted Ephemeris)"), the id after "finding_", and a Bayer designation
 * or catalogue number of two words or more. So "the Moon" is the predicted Moon, and the Moon's measured beacon,
 * whose title only begins with the word, is reached by pointing at it or by its whole title. A one-word
 * designation ("bet", "pi", "51": the solver's abbreviation for a named star outside the pinned catalog) is not a
 * name a person says, and is never read as one.
 */
function spokenNamesOf(findings: readonly CelestialFinding[]): SpokenName[] {
  const cached = SPOKEN_NAMES.get(findings);
  if (cached) return cached;
  const held = new Map(findings.map((finding, index) => [finding.id, index]));
  const own = new Map<string, Set<string>>();
  const said = new Map<string, Set<string>>();
  const absent = new Map<string, string>();
  const add = (names: Map<string, Set<string>>, name: string | undefined, id: string) => {
    const spoken = name?.trim().toLowerCase();
    if (!spoken) return;
    if (!names.has(spoken)) names.set(spoken, new Set());
    names.get(spoken)!.add(id);
  };

  for (const [spoken, id, shown] of CATALOG_STAR_NAMES) {
    if (held.has(id)) add(own, spoken, id);
    else absent.set(spoken, shown);
  }
  // A body this solution holds is named below by its own record or its title; one it does not hold is missing
  for (const [spoken, shown] of Object.entries(PREDICTED_BODY_NAMES)) absent.set(spoken, shown);
  for (const finding of findings) {
    for (const [spoken, iauName] of Object.entries(SPOKEN_STAR_ALIASES)) {
      if (finding.title === iauName) add(own, spoken, finding.id);
    }
    add(own, PREDICTED_ID.exec(finding.id)?.[1]?.replace(/-/g, ' '), finding.id);
    add(said, finding.title, finding.id);
    add(said, finding.title.split(' (')[0], finding.id);
    const idShort = finding.id.replace(/^finding_/, '');
    if (idShort.length >= 3) add(said, idShort, finding.id);
    for (const designation of [finding.catalogRef?.bayerDesignation, finding.catalogRef?.catalogSourceId]) {
      if (designation && /\S\s+\S/.test(designation)) add(said, designation, finding.id);
    }
  }

  const names = [...new Set([...own.keys(), ...said.keys(), ...absent.keys()])]
    .sort((a, b) => b.length - a.length || (a < b ? -1 : 1))
    .map((spoken): SpokenName => {
      const ids = [...(own.get(spoken) ?? said.get(spoken) ?? [])].sort((a, b) => held.get(a)! - held.get(b)!);
      return {
        spoken,
        pattern: CATALOG_PATTERNS.get(spoken) ?? starNamePattern(spoken),
        ids,
        ...(ids.length === 0 ? { absent: absent.get(spoken) } : {}),
      };
    });
  SPOKEN_NAMES.set(findings, names);
  return names;
}

/** What a request names: findings of the solution, stars and bodies it does not hold, and the text with both cut out. */
interface NamesRead {
  /** Each name of a finding, in the order said, with the findings it stands for. */
  present: Array<{ at: number; ids: string[] }>;
  /** Each star or body named that the solution does not hold, in the order said, by the name to show. */
  absent: Array<{ at: number; shown: string }>;
  without: string;
}

/**
 * Finds every name the request says. A name found is cut out before a shorter one is looked for. A request that
 * names something is then read for every name it may say: beside another name, a star name that is also an
 * everyday word is a name without its cue ("how far is Rigel from Ran?"), so it is never dropped, as a word that
 * only describes, for the name beside it. With no other name it stays an everyday word.
 */
function readNames(normalizedUtterance: string, session: ObservationSession): NamesRead {
  const read: NamesRead = { present: [], absent: [], without: normalizedUtterance };
  const names = spokenNamesOf(session.observationSolution?.findings ?? NO_FINDINGS);
  const take = (name: SpokenName, pattern: RegExp) => {
    // A pattern finds its name only where the name's own letters stand: most names are passed over here
    if (!read.without.includes(name.spoken)) return;
    const spans = [...read.without.matchAll(pattern)].map((match) => [match.index, match.index + match[0].length] as const);
    for (const [start, end] of spans) {
      if (name.ids.length > 0) read.present.push({ at: start, ids: name.ids });
      else if (name.absent) read.absent.push({ at: start, shown: name.absent });
      read.without = blank(read.without, start, end);
    }
  };
  for (const name of names) take(name, name.pattern);
  if (read.present.length + read.absent.length > 0) {
    for (const name of names) {
      const bare = EVERYDAY_BARE.get(name.spoken);
      if (bare) take(name, bare);
    }
  }
  read.present.sort((a, b) => a.at - b.at);
  read.absent.sort((a, b) => a.at - b.at);
  return read;
}

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

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  reason?: RejectionReason;
}

/** The brightest family: services/voice/intent.py refuses the same words, since it never sees the fluxes. */
export const BRIGHTEST_PATTERN = /\bbrightest\b/i;
/** Every "brightest" of a request: the ranking rule's own word, cut out before the other words are judged. */
const RANKING_WORD = new RegExp(BRIGHTEST_PATTERN.source, 'gi');
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

/**
 * Why a request has no target, in words a person who is not an astronomer reads: no id, class or contract term.
 * services/voice/intent.py says the same sentences for the same cases, bar the one about what this sky lacks: the
 * backend holds no photo.
 */
const WHICH_ONE = 'That does not say which one. Say its name, or select it and say "that one".';
const NOTHING_IN_VIEW = 'Nothing is selected or pointed at. Select a star or planet first, or say its name.';

/** A word the parser does not know is no evidence about the sky: it says only that it did not understand the word. */
function notUnderstood(said: string): string {
  return `I did not understand "${said}". Say a star's or a planet's name, or select one and say "that one".`;
}

/**
 * True when this session holds a sky that was worked out, from its stars or from the recording's time and place,
 * and the view has not marked it unresolved. Only such a sky can be said to lack a name: a solve that has not run,
 * or did not come out, looked for nothing.
 */
function skyWasWorkedOut(context: ActionContext, session: ObservationSession): boolean {
  const status = session.observationSolution?.status;
  return (
    context.calibrationState !== 'unresolved' &&
    (status === 'calibrated' || status === 'approximate' || status === 'predicted_only')
  );
}

/** An unresolved sky places nothing, so nothing in it has a name; a point of light can still be selected. */
function unresolvedSky(session: ObservationSession, what: 'no name' | 'nothing in view'): string {
  const sky = `The sky in this ${mediaWord(session)} has not been worked out yet`;
  return what === 'no name' ? `${sky}, so nothing in it can be found by name.` : `${sky}, and nothing is selected or pointed at.`;
}

/** What is pointed at, then selected, then focused: the ids a pointing word can mean, the nearest first. */
function idsInView(context: ActionContext): string[] {
  const pointer = isPointerFreshAndMatchingRevision(context.recentPointer, context) ? context.recentPointer : undefined;
  const pointed =
    pointer?.nearestFindingId ??
    (pointer?.overlappingCandidateIds?.length === 1 ? pointer.overlappingCandidateIds[0] : undefined);
  return [pointed, context.selectedFindingId, context.focusedFindingId].filter((id): id is string => Boolean(id));
}

/** What is in view: the pointer's finding, or a question between the findings it overlaps, else the selection. */
function targetInView(context: ActionContext, session: ObservationSession): TargetResolutionResult {
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

  const inView = idsInView(context)[0];
  if (inView) return { findingId: inView };

  // Nothing else stands in: not the solution's only finding, and not any default star
  if (context.calibrationState === 'unresolved') {
    return { error: unresolvedSky(session, 'nothing in view'), reason: 'unresolved_sky' };
  }
  return { error: NOTHING_IN_VIEW, reason: 'ambiguous_target' };
}

/**
 * The finding the request's names mean. One name is that finding; a name two findings share is the one of them in
 * view, when one is; two different things named are a question, never a guess.
 */
function namedTarget(present: NamesRead['present'], context: ActionContext, session: ObservationSession): TargetResolutionResult {
  const inView = idsInView(context);
  const meant: string[] = [];
  for (const { ids } of present) {
    const one = ids.length === 1 ? ids[0] : inView.find((id) => ids.includes(id));
    for (const id of one ? [one] : ids) if (!meant.includes(id)) meant.push(id);
  }
  if (meant.length === 1) return { findingId: meant[0] };
  const titles = new Map((session.observationSolution?.findings ?? []).map((f) => [f.id, f.title]));
  const choices = meant.map((id) => ({ id, label: titles.get(id) ?? id }));
  return {
    disambiguation: { question: `Which one did you mean: ${choices.map((choice) => choice.label).join(' or ')}?`, choices },
  };
}

/**
 * Why a request stops at something it names that this sky does not hold, or undefined when it names no such thing.
 * In order: a southern object voice cannot reach; the Southern Cross, which is no one star; a star or body the name
 * tables know that this sky lacks; and a word the parser did not understand (`unknown`), which may be a name no
 * table holds. Each stops the request whatever else it names: nothing is done about a name that is here while
 * another thing said is not.
 */
function whatIsNotHeld(
  normalizedUtterance: string,
  context: ActionContext,
  session: ObservationSession,
  said: NamesRead,
  unknown: string
): TargetResolutionResult | undefined {
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
  const missing = said.absent[0];
  if (missing) {
    // Only a sky that was worked out can lack a name: of any other, all that is known is that nothing is placed
    // yet. The reason is the unresolved sky where the view marks it so, else the one a missing name always had
    if (!skyWasWorkedOut(context, session)) {
      const reason = context.calibrationState === 'unresolved' ? 'unresolved_sky' : 'ambiguous_target';
      return { error: unresolvedSky(session, 'no name'), reason };
    }
    const shown = `${missing.shown.charAt(0).toUpperCase()}${missing.shown.slice(1)}`;
    return { error: `${shown} was not found in this ${mediaWord(session)}'s sky.`, reason: 'ambiguous_target' };
  }
  if (unknown) return { error: notUnderstood(unknown), reason: 'ambiguous_target' };
  return undefined;
}

/**
 * What a command is about, read from the request's own words (services/voice/intent.py reads it the same way).
 *
 * Every thing the request names must be in this sky, and every word of it must be read: a name this sky does not
 * hold, or a word the parser did not understand, stops it (`whatIsNotHeld`), whether or not it also names
 * something that is here or points at the view. Then a name is that finding, and two different names are a
 * question. With no name, what is pointed at, selected or focused is taken only when a word points at it, or when
 * the request describes and asks nothing at all and the command, said alone, acts on what is in view (`alone` is
 * 'view': "explore", "how far?"). A command that needs to be told which one says so when said alone (`alone` is
 * 'which': "select"). Undefined means the words say nothing about a target: a question about no one thing ("what
 * is an orbit?"), left to the rules after this one.
 */
function resolveTargetFinding(
  normalizedUtterance: string,
  context: ActionContext,
  session: ObservationSession,
  said: NamesRead,
  command: RegExp | undefined,
  alone: 'view' | 'which'
): TargetResolutionResult | undefined {
  // "Brightest" asks for a ranking, answered from the measurements even when a star of this sky is named beside
  // it. The ranking's own refusals come first: they say why no brightest is named
  const ranked = BRIGHTEST_PATTERN.test(normalizedUtterance) ? brightestTarget(normalizedUtterance, session) : undefined;
  if (ranked?.error !== undefined) return ranked;

  const wording = readWording(said.without, command);
  const stopped = whatIsNotHeld(normalizedUtterance, context, session, said, wording.unknown);
  if (stopped) return stopped;
  if (ranked) return ranked;
  if (said.present.length > 0) return namedTarget(said.present, context, session);

  if (wording.points) return targetInView(context, session);
  if (wording.describes) return { error: WHICH_ONE, reason: 'ambiguous_target' };
  if (wording.asks) return undefined;
  return alone === 'view' ? targetInView(context, session) : { error: WHICH_ONE, reason: 'ambiguous_target' };
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

/** The commands that act on one finding, by the words that ask for each. */
const VIEWPOINT_COMMAND = /\b(another viewpoint|different viewpoint|orbit|another angle|from the side|translate viewpoint|shift viewpoint)\b/i;
const DISTANCE_COMMAND = /\b(how far|distance|light years|parsecs|how distant)\b/i;
const SIGHTLINE_COMMAND = /\b(what's behind|what is behind|what lies beyond|behind it|beyond it|behind|beyond|behind sightline|along this sightline)\b/i;
const FLIGHT_COMMAND = /\b(take me there|fly there|go there|go to|explore|fly to|travel to|enter space|enter 3d|zoom into space|take me to)\b/i;
const SELECT_COMMAND = /\b(what's that|what is that|what star is that|which star|that one|this one|select|focus on|inspect|point at|look at|tell me about)\b/i;

function parseWithRules(
  utterance: string,
  context: ActionContext,
  session: ObservationSession,
  options: VoiceParseOptions
): VoiceParseResult {
  const trimmed = utterance.trim();
  if (!trimmed) return { error: 'Empty voice or text command.', reason: 'no_grounded_answer' };

  const norm = trimmed.toLowerCase();
  // Each request gets its own id: the view's revision, the count of requests this session has heard, and the
  // words. A request repeated after a refusal, which leaves the revision as it was, is still another request
  const heard = session.voiceActions.length + 1;
  const requestId = `req_${context.viewRevision}_${heard}_${deterministicHexDigest(`${trimmed}:${context.viewRevision}`).slice(0, 8)}`;
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
      // No checkpoint named: the reducer returns to the moment you left, as the button does
      action: AlbedianActionSchema.parse({ type: 'source.return', requestId, expectedViewRevision: context.viewRevision, restoreAudio: true }),
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

  // What the request names, read once: findings of this sky, and the stars and bodies it does not hold
  const named = readNames(norm, session);
  const howMany = /\bhow\s+many\b/i.test(norm);
  // As the backend does: a southern object, the Southern Cross, or a star or body this sky does not hold is
  // answered here, beside a name that is here or not, before a later rule could read the request as a question,
  // take the selection, or act on the name that is here
  if (
    unbakedObjectIn(norm) !== undefined ||
    SOUTHERN_CROSS_NAMES.some((name) => namesWholeWord(norm, name)) ||
    // a "how many" question naming a star stays a question, as the select rule and the backend read it
    (named.absent.length > 0 && !howMany)
  ) {
    const resolved = resolveTargetFinding(norm, context, session, named, undefined, 'which');
    if (resolved?.disambiguation) return { disambiguation: resolved.disambiguation };
    if (resolved && !resolved.findingId) return { error: resolved.error, reason: resolved.reason };
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

  const aroundWhat = VIEWPOINT_COMMAND.test(norm) ? resolveTargetFinding(norm, context, session, named, VIEWPOINT_COMMAND, 'view') : undefined;
  if (aroundWhat) {
    const resolved = aroundWhat;
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

  const howFarWhat = DISTANCE_COMMAND.test(norm) ? resolveTargetFinding(norm, context, session, named, DISTANCE_COMMAND, 'view') : undefined;
  if (howFarWhat) {
    const resolved = howFarWhat;
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

  const behindWhat = SIGHTLINE_COMMAND.test(norm) ? resolveTargetFinding(norm, context, session, named, SIGHTLINE_COMMAND, 'view') : undefined;
  if (behindWhat) {
    const resolved = behindWhat;
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

  const toWhat = FLIGHT_COMMAND.test(norm) ? resolveTargetFinding(norm, context, session, named, FLIGHT_COMMAND, 'view') : undefined;
  if (toWhat) {
    const resolved = toWhat;
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

  // Selecting needs a name or a pointing word: on its own it does not say which one
  const selectWhat =
    SELECT_COMMAND.test(norm) || BRIGHTEST_PATTERN.test(norm) || (!howMany && named.present.length > 0)
      ? resolveTargetFinding(norm, context, session, named, SELECT_COMMAND, 'which')
      : undefined;
  if (selectWhat) {
    const resolved = selectWhat;
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
