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
  type VoiceRefusal,
  deterministicHexDigest,
  isLetterOrDigit,
  lowerCase,
  refusalSentence,
} from '@albedian/contracts';
import { dispatchAlbedianAction } from './reducer.js';
import { EMPTY_SOURCE_ASSET_ID } from './emptySession.js';
import { CATALOG_STAR_NAMES, EVERYDAY_WORD_NAMES, NAME_CUE_VERBS } from './generated/catalogStarNames.js';
import {
  anotherRequest,
  blanked,
  changesTheScene,
  conflictingModifier,
  fieldsFor,
  fold,
  ownWordsCut,
  ruleSays,
  VOICE_GRAMMAR,
  type GrammarRule,
  type GrammarTarget,
} from './voiceGrammar.js';

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
  /** Why the request has no target, as the contract's VoiceRefusal: then `error` is its sentence. */
  refusal?: VoiceRefusal;
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
  const needle = lowerCase(name.trim());
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
export const PREDICTED_BODY_NAMES: Readonly<Record<string, string>> = {
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
  const lower = lowerCase(name.trim());
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
/** The words that describe a thing itself (what it is, its kind, how it looks, where it is), which a rule that changes the
 * scene and has a target accounts for (answers-3b v4); services/voice/names.py DESCRIBING_A_THING. */
const DESCRIBING_A_THING = [
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
];
const DESCRIBING_WORDS = [
  ...DESCRIBING_A_THING,
  'distance', 'year', 'years', 'parsec', 'parsecs', 'kilometre', 'kilometres', 'kilometer', 'kilometers', 'km',
  'mile', 'miles', 'au', 'ly', 'pc', 'photo', 'photos', 'picture', 'pictures', 'image', 'images', 'video', 'videos',
  'clip', 'recording', 'shot', 'frame', 'sky', 'night', 'screen', 'view', 'app', 'solve', 'solved', 'space', '3d',
  'earth', 'home', 'orbit', 'viewpoint', 'angle', 'side', 'sightline', 'different', 'distant',
  'magnitude', 'brightness', 'dimmest', 'faintest',
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
  'wait', 'yo', 'mate', 'beautiful', 'gorgeous', 'pretty', 'amazing', 'lovely', 'stunning', 'awesome', 'wonderful', 'incredible', 'instead', 'camping', 'saw',
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
 * The words of a text: runs of letters and digits of the tracked letters table, of any script, so a name written in
 * another alphabet is a word no list holds, never nothing at all (services/voice/names.py words_of reads the same).
 */
function wordsOf(text: string): string[] {
  const words: string[] = [];
  let word = '';
  for (const char of text) {
    if (isLetterOrDigit(char.codePointAt(0)!)) word += char;
    else if (word) {
      words.push(word);
      word = '';
    }
  }
  if (word) words.push(word);
  return words;
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
    // "It" after "as" points at nothing ("show me the sky as it was")
    if (word === 'it') return words[index - 1] !== 'as';
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
  // The words that change nothing (er, the photo, can you) are plain to the target's reading too (answers-3b v4)
  const rest = blanked(
    VOICE_GRAMMAR.unchangingPattern,
    (command ? withoutNames.replace(new RegExp(command.source, 'gi'), cutOut) : withoutNames).replace(RANKING_WORD, cutOut)
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
    const spoken = name === undefined ? undefined : lowerCase(name.trim());
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
  absent: Array<{ at: number; shown: string; spoken: string }>;
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
      else if (name.absent) read.absent.push({ at: start, shown: name.absent, spoken: name.spoken });
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
  return unbakedObjectSaid(normalizedUtterance)?.[0];
}

/** The southern object voice cannot reach yet that the request names, and the spelling it says. */
function unbakedObjectSaid(normalizedUtterance: string): [string, string] | undefined {
  for (const [label, spoken] of Object.entries(UNBAKED_OBJECTS)) {
    const said = spoken.find((name) => namesWholeWord(normalizedUtterance, name));
    if (said !== undefined) return [label, said];
  }
  return undefined;
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
/** The contract's refusal for this object: the deep-sky row that holds it, or that no table holds it by name. */
export function unbakedRefusalRecord(label: string, said: string): VoiceRefusal {
  const row = UNBAKED_DEEP_SKY_ROWS[label];
  return row
    ? { kind: 'deep_sky_out_of_reach', said, name: label, catalogRow: row }
    : { kind: 'no_named_row', said, name: label };
}

/** Why voice cannot act on this object, as the contract's sentence for its refusal. */
export function unbakedRefusal(label: string): string {
  return refusalSentence(unbakedRefusalRecord(label, label));
}

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  reason?: RejectionReason;
  refusal?: VoiceRefusal;
}

/** A refusal, its contract reason, and its sentence in this recording's media word. */
function refused(refusal: VoiceRefusal, reason: RejectionReason, session: ObservationSession): TargetResolutionResult {
  return { error: refusalSentence(refusal, mediaWord(session)), reason, refusal };
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
    // Ties by id in code unit order, never the runtime's collation
    .sort((a, b) => b.sourceDetection.flux - a.sourceDetection.flux || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
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
  const next = BRIGHTEST_NEXT_WORD.exec(normalizedUtterance)?.[1];
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
    return refused({ kind: 'nothing_in_view_sky_not_worked_out' }, 'unresolved_sky', session);
  }
  return refused({ kind: 'nothing_in_view' }, 'ambiguous_target', session);
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
  const unbaked = unbakedObjectSaid(normalizedUtterance);
  if (unbaked) return refused(unbakedRefusalRecord(...unbaked), 'no_grounded_answer', session);
  if (SOUTHERN_CROSS_NAMES.some((name) => namesWholeWord(normalizedUtterance, name))) {
    const findings = session.observationSolution?.findings ?? [];
    const choices = SOUTHERN_CROSS_STARS.flatMap((name) => {
      const finding = findings.find((f) => f.title === name);
      return finding ? [{ id: finding.id, label: name }] : [];
    });
    if (choices.length > 0) return { disambiguation: { question: SOUTHERN_CROSS_QUESTION, choices } };
    return refused({ kind: 'southern_cross_not_in_this_sky' }, 'ambiguous_target', session);
  }
  const missing = said.absent[0];
  if (missing) {
    // Only a sky that was worked out can lack a name: of any other, all that is known is that nothing is placed
    // yet. The reason is the unresolved sky where the view marks it so, else the one a missing name always had
    if (!skyWasWorkedOut(context, session)) {
      const reason = context.calibrationState === 'unresolved' ? 'unresolved_sky' : 'ambiguous_target';
      return refused({ kind: 'sky_not_worked_out' }, reason, session);
    }
    return refused({ kind: 'not_in_this_sky', said: missing.spoken, name: missing.shown }, 'ambiguous_target', session);
  }
  if (unknown) return refused({ kind: 'not_understood', said: unknown }, 'ambiguous_target', session);
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
  alone: GrammarTarget
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
  // A rule that is about a named thing (about, named) says nothing of a thing only described: the next rule reads it
  if (alone === 'about' || alone === 'named') return undefined;
  if (wording.describes) return refused({ kind: 'which_one' }, 'ambiguous_target', session);
  if (wording.asks) return undefined;
  return alone === 'view' ? targetInView(context, session) : refused({ kind: 'which_one' }, 'ambiguous_target', session);
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
  // The one way in: each typographic apostrophe or quote is read as its plain one before anything reads the words
  utterance = fold(VOICE_GRAMMAR, utterance);
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

  const norm = lowerCase(trimmed);
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

  // The commands, tried in the order of the voice command grammar (packages/contracts/src/data/voice-command-grammar.json),
  // which the backend's parser reads too
  const named = readNames(norm, session);
  const howMany = /\bhow\s+many\b/i.test(norm);
  const base = { requestId, expectedViewRevision: context.viewRevision };
  // The first refusal of a sentence a rule acted on but did not account for (answers-3b v4)
  let notARequest: VoiceParseResult | undefined;
  for (const rule of VOICE_GRAMMAR.rules) {
    if (rule.id === VOICE_GRAMMAR.namesRefusedBefore) {
      // As the backend does: a southern object, the Southern Cross, or a star or body this sky does not hold is
      // answered here, beside a name that is here or not, before a later rule could read the request as a question,
      // take the selection, or act on the name that is here
      if (
        unbakedObjectIn(norm) !== undefined ||
        SOUTHERN_CROSS_NAMES.some((name) => namesWholeWord(norm, name)) ||
        // a "how many" question naming a star stays a question, as the name rule and the backend read it
        (named.absent.length > 0 && !howMany)
      ) {
        const resolved = resolveTargetFinding(norm, context, session, named, undefined, 'which');
        if (resolved?.disambiguation) return { disambiguation: resolved.disambiguation };
        if (resolved && !resolved.findingId) return { error: resolved.error, reason: resolved.reason, refusal: resolved.refusal };
      }
    }
    const read = readRule(rule, norm, trimmed, context, session, named, howMany, base, currentYear);
    // A rule that acted but left a word unaccounted passes the sentence to the later rules, as the backend does: its
    // refusal is given only when no later rule accounts for it, and two requests are refused at once
    if (read?.refusal?.kind === 'not_a_request') {
      // The name rule's own refusal is never kept: a sentence that is more than a name is the later rules' to read
      if (rule.match !== 'name') notARequest ??= read;
      continue;
    }
    if (read && rule.match === 'rest' && notARequest) return notARequest;
    if (read) return read;
  }
  throw new Error('the voice command grammar ends with its rest rule, which always reads the request');
}

type RuleBase = { requestId: string; expectedViewRevision: number };

/** What one rule makes of the request: a result, or undefined when the rule does not ask for it. */
function readRule(
  rule: GrammarRule,
  norm: string,
  trimmed: string,
  context: ActionContext,
  session: ObservationSession,
  named: NamesRead,
  howMany: boolean,
  base: RuleBase,
  currentYear: number
): VoiceParseResult | undefined {
  const handler = RULE_HANDLERS[rule.action];
  if (!handler) throw new Error(`no handler for the voice command ${rule.action}`);
  let read: VoiceParseResult | undefined;
  if (rule.match === 'year' || rule.match === 'rest' || rule.match === 'name' || rule.match === 'seconds') {
    read = handler({ rule, norm, trimmed, context, session, named, howMany, base, currentYear, fields: rule.with });
  } else if (ruleSays(rule, norm)) {
    read = handler({ rule, norm, trimmed, context, session, named, howMany, base, currentYear, fields: fieldsFor(rule, norm) });
  }
  // A sentence that changes the scene acts only when every word of it is accounted for (answers-3b v4); the caller
  // passes a not_a_request to the later rules
  if (read?.action && changesTheScene(VOICE_GRAMMAR, rule)) return notOneRequest(rule, norm, named, session) ?? read;
  return read;
}

// The words a rule that changes the scene accounts for beside its own: the pointing words (that, it, there), and for a
// rule with a target, the words that describe the thing itself (bright, red, star) and the star names that are also
// everyday words (ran, fang), as services/voice/intent.py has them
const TARGET_ACCOUNTED = new Set<string>([...POINTING, ...DESCRIBING_A_THING, ...EVERYDAY_WORD_NAMES]);
// What the code of a year rule reads: the first of these its reading takes
const YEAR_READS = [YEARS_AGO, A_THOUSAND_YEARS_AGO, YEARS_FROM_NOW, YEAR_THEN_ERA, ERA_THEN_YEAR, YEAR_WORD];

/** A seek phrase and the seconds said after it, as the seek rule reads them. */
function secondsSaid(rule: GrammarRule, norm: string): RegExpMatchArray | null {
  return norm.match(new RegExp(`(?:${rule.pattern!.source})\\s+(?:to\\s+)?(\\d+(?:\\.\\d+)?)\\s*(?:s|sec|seconds)?\\b`));
}

/** `rest` with what a code-read rule's own code read of `norm` turned to spaces: the seconds after a seek phrase, or the year. */
function codeRead(rule: GrammarRule, norm: string, rest: string): string {
  let said: RegExpMatchArray | null | undefined;
  if (rule.match === 'seconds') said = secondsSaid(rule, norm);
  else if (rule.match === 'year') said = YEAR_READS.map((pattern) => norm.match(pattern)).find((match) => match !== null);
  if (!said || said.index === undefined) return rest;
  return rest.slice(0, said.index) + ' '.repeat(said[0].length) + rest.slice(said.index + said[0].length);
}

/**
 * The refusal of a sentence the rule's action came from that is not one request, or undefined when it is one
 * (services/voice/intent.py _not_one_request): a second request that changes the scene in it (two_requests), else the
 * first word nothing accounts for (not_a_request): not the rule's own words, a name, a pointing word, for a rule with a
 * target a describing word, what its code read, or a word that changes nothing (the table's unchanging words).
 */
function notOneRequest(rule: GrammarRule, norm: string, named: NamesRead, session: ObservationSession): VoiceParseResult | undefined {
  const own = ownWordsCut(rule, named.without);
  const media = mediaWord(session);
  // The name rule has no words of its own, so a rule's words beside a name are that rule's to read, not a second request
  if (rule.match !== 'name' && anotherRequest(VOICE_GRAMMAR, rule, own, named.without)) {
    const refusal: VoiceRefusal = { kind: 'two_requests' };
    return { error: refusalSentence(refusal, media), reason: 'not_one_request', refusal };
  }
  const conflict = conflictingModifier(rule, named.without);
  if (conflict !== undefined) {
    const refusal: VoiceRefusal = { kind: 'not_a_request', said: Array.from(wordsOf(conflict)[0]!).slice(0, MAX_QUOTED_CHARACTERS).join('') };
    return { error: refusalSentence(refusal, media), reason: 'not_one_request', refusal };
  }
  let rest = codeRead(rule, norm, blanked(VOICE_GRAMMAR.unchangingPattern, own));
  // The ranking word is the app's own reading of a target by measured flux ("the brightest star"); the backend refuses
  // it before any rule (services/voice/intent.py BRIGHTEST), since it holds no fluxes
  if (rule.target !== undefined) rest = blanked(RANKING_WORD, rest);
  const accounted = rule.target !== undefined ? TARGET_ACCOUNTED : POINTING;
  const word = wordsOf(rest).find((w) => !accounted.has(w));
  if (word === undefined) return undefined;
  // At most 80 characters, counted in code points as Python counts them
  const refusal: VoiceRefusal = { kind: 'not_a_request', said: Array.from(word).slice(0, MAX_QUOTED_CHARACTERS).join('') };
  return { error: refusalSentence(refusal, media), reason: 'not_one_request', refusal };
}

interface RuleInput {
  rule: GrammarRule;
  norm: string;
  trimmed: string;
  context: ActionContext;
  session: ObservationSession;
  named: NamesRead;
  howMany: boolean;
  base: RuleBase;
  currentYear: number;
  fields: Readonly<Record<string, unknown>>;
}

/** The action on the target a rule found, the question between several, or the refusal that says why there is none. */
function onTarget(input: RuleInput, act: (findingId: string) => Record<string, unknown>): VoiceParseResult | undefined {
  const { rule, norm, context, session, named } = input;
  // A rule read from phrases cuts its own words before the rest is read; the name rule has none
  const resolved = resolveTargetFinding(norm, context, session, named, rule.pattern, rule.target!);
  if (!resolved) return undefined;
  if (rule.target === 'named' && !resolved.findingId && !resolved.disambiguation) return undefined;
  if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
  if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason, refusal: resolved.refusal };
  return { action: AlbedianActionSchema.parse(act(resolved.findingId)) };
}

const acted = (action: Record<string, unknown>): VoiceParseResult => ({ action: AlbedianActionSchema.parse(action) });

/** One handler per contract action the grammar names; each reads the rule's fixed fields. */
const RULE_HANDLERS: Readonly<Record<string, (input: RuleInput) => VoiceParseResult | undefined>> = {
  'navigation.cancel': ({ base, session }) =>
    acted({ type: 'navigation.cancel', ...base, transitionId: session.spatialView.activeTransitionId }),
  'history.back': ({ base, fields }) => acted({ type: 'history.back', ...base, ...fields }),
  // No checkpoint named: the reducer returns to the moment you left, as the button does
  'source.return': ({ base }) => acted({ type: 'source.return', ...base, restoreAudio: true }),
  'space.epoch': ({ norm, base, currentYear }) => {
    const said = saidYear(norm, currentYear);
    if (said === undefined) return undefined;
    const year = epochYear(said);
    if ('refusal' in year) return { error: year.refusal, reason: 'invalid_epoch' };
    return acted({ type: 'space.epoch', ...base, epochISO: epochIsoForYear(year.yearCE), yearCE: year.yearCE });
  },
  'ui.overlay': ({ base, fields }) => acted({ type: 'ui.overlay', ...base, ...fields }),
  'media.compare': ({ base, session }) => {
    const originalVariant = session.mediaVariants[0]?.id ?? 'variant_original';
    const restoredVariant = session.activeVariantId !== originalVariant ? session.activeVariantId : (session.mediaVariants[1]?.id ?? originalVariant);
    return acted({ type: 'media.compare', ...base, variantAId: originalVariant, variantBId: restoredVariant, preserveTime: true });
  },
  'media.restore': ({ base, fields, session }) =>
    acted({
      type: 'media.restore',
      ...base,
      sourceAssetId: session.sourceAsset?.id ?? EMPTY_SOURCE_ASSET_ID,
      ...fields,
      constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
    }),
  'media.seek': ({ rule, norm, base }) => {
    const seekMatch = secondsSaid(rule, norm);
    if (!seekMatch?.[1]) return undefined;
    return acted({ type: 'media.seek', ...base, sourceTimeSec: Number.parseFloat(seekMatch[1]), preserveAudioSync: true });
  },
  'sky.show': ({ base, fields, context, session }) =>
    acted({
      type: 'sky.show',
      ...base,
      mode: context.calibrationState === 'calibrated' ? 'measured' : 'predicted',
      ...fields,
      ...(session.observationSolution ? { observationSolutionId: session.observationSolution.id } : {}),
    }),
  'space.viewpoint': (input) =>
    onTarget(input, (findingId) => ({
      type: 'space.viewpoint',
      ...input.base,
      targetFindingId: findingId,
      units: 'pc',
      offsetParsec: [12, 8, -5],
      ...input.fields,
    })),
  'finding.describe': (input) => onTarget(input, (findingId) => ({ type: 'finding.describe', ...input.base, findingId, ...input.fields })),
  'space.enter': (input) =>
    onTarget(input, (findingId) => ({ type: 'space.enter', ...input.base, findingId, distancePolicy: 'measured_or_estimated' })),
  'finding.select': (input) => {
    // The name rule: a thing the request names, or a ranking by measured brightness, is what it is about
    if (input.rule.match === 'name' && !BRIGHTEST_PATTERN.test(input.norm) && (input.howMany || input.named.present.length === 0)) return undefined;
    return onTarget(input, (findingId) => ({ type: 'finding.select', ...input.base, findingId }));
  },
  // No rule matches: a question, which the reducer refuses until the M3 tools can answer it from the user's data
  'question.ask': ({ trimmed, base }) => {
    if (trimmed.length > QUESTION_MAX_UTF16) {
      return { error: 'No command matches the request, and it is longer than a question can hold.', reason: 'no_grounded_answer' };
    }
    return acted({ type: 'question.ask', ...base, question: trimmed });
  },
};

/** The rules of the voice command grammar this parser reads: every one whose action has a handler here. */
export const HANDLED_RULES: ReadonlySet<string> = new Set(VOICE_GRAMMAR.rules.filter((rule) => rule.action in RULE_HANDLERS).map((rule) => rule.id));

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
