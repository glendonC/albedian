import { AlbedianActionSchema, isLetterOrDigit, VOICE_COMMAND_GRAMMAR_TABLE } from '@albedian/contracts';

/**
 * The voice command grammar both parsers read: packages/contracts/src/data/voice-command-grammar.json, loaded here
 * for the app's parser (voiceParser.ts) and by services/voice/grammar.py for the backend's, by the same rules and
 * with the same error codes, so a table one side takes the other takes too (tests/fixtures/voice_grammar_faults.json
 * holds the faulty tables both are judged on). The checks run in the order written here, and the first fault found
 * is the one reported.
 */

export const GRAMMAR_FORMAT = 'albedian.voice-command-grammar';
export const GRAMMAR_MATCHES = ['words', 'start', 'whole', 'year', 'seconds', 'name', 'rest'] as const;
export const GRAMMAR_TARGETS = ['view', 'which', 'about', 'named'] as const;
/** [proposed] Bounds of the table, each well above what it holds (28 rules, 26 phrases in the longest rule). */
export const GRAMMAR_LIMITS = { rules: 64, phrases: 64, phraseUtf16: 64, modifiers: 8, modifierWords: 16, aboutUtf16: 2000, idUtf16: 32, unchangingGroups: 16, whyUtf16: 200, folds: 16 } as const;
/** The match kinds code reads, each with the one action it forms; every other kind is read from phrases. */
const CODE_MATCH_ACTION: Readonly<Record<string, string>> = { year: 'space.epoch', seconds: 'media.seek', name: 'finding.select', rest: 'question.ask' };
const PHRASE_MATCHES = new Set(['words', 'start', 'whole', 'seconds']);
/** The actions on one finding, which say how their target is found. */
const TARGET_ACTIONS = new Set(['finding.select', 'finding.describe', 'space.enter', 'space.viewpoint']);
/** The fields a rule may fix for each action, and the rest of a sample action they are checked in by the contract. */
const WITH_FIELDS: Readonly<Record<string, { keys: readonly string[]; sample: Record<string, unknown> }>> = {
  'history.back': { keys: ['mode', 'fraction'], sample: { mode: 'checkpoint' } },
  'ui.overlay': { keys: ['overlay', 'enabled'], sample: { overlay: 'observedMarkers', enabled: true } },
  'finding.describe': { keys: ['fields'], sample: { findingId: 'finding-sample', fields: ['distance'] } },
  'space.viewpoint': { keys: ['trajectory'], sample: { targetFindingId: 'finding-sample', units: 'pc', offsetParsec: [0, 0, 0], trajectory: 'orbit' } },
  'media.restore': { keys: ['profile'], sample: { sourceAssetId: 'src_sample', profile: 'natural', constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true } } },
  'sky.show': { keys: ['mode'], sample: { mode: 'measured' } },
};
const TOP_KEYS = new Set(['format', 'formatVersion', 'about', 'namesRefusedBefore', 'folds', 'unchanging', 'describingActions', 'rules']);
const UNCHANGING_KEYS = new Set(['words', 'why']);
const RULE_KEYS = new Set(['id', 'action', 'match', 'phrases', 'target', 'with', 'modifiers']);
const MODIFIER_KEYS = new Set(['words', 'with']);
const ID_PATTERN = /^[a-z][a-z0-9_]*$/;
const GAP = '...';
const JOINERS = new Set(["'", '-', '/']);

export type GrammarMatch = (typeof GRAMMAR_MATCHES)[number];
export type GrammarTarget = (typeof GRAMMAR_TARGETS)[number];
export interface GrammarModifier {
  words: readonly string[];
  with: Readonly<Record<string, unknown>>;
  pattern: RegExp;
  cut: RegExp;
}
export interface GrammarRule {
  id: string;
  action: string;
  match: GrammarMatch;
  phrases: readonly string[];
  target?: GrammarTarget;
  with: Readonly<Record<string, unknown>>;
  modifiers: readonly GrammarModifier[];
  /** Whole words of any phrase (a gap phrase as its two parts in order); never set for year, name or rest. */
  pattern?: RegExp;
  /**
   * The same phrases with each gap a named group (g0, g1, ...), and how far into a match each gap starts: the rule
   * accounts for its phrase less the gap, which holds the request's own words.
   */
  cut?: RegExp;
  gapStarts?: Readonly<Record<string, number>>;
}
export interface VoiceGrammar {
  namesRefusedBefore: string;
  /** Each typographic apostrophe or quote and the plain one it is read as, before anything reads a request. */
  folds: Readonly<Record<string, string>>;
  /**
   * The words that change nothing about a request (a greeting, "please", an article, the app's name), each group with
   * why it is there: with a rule's own words, the only words a sentence that changes the scene may hold.
   */
  unchanging: readonly string[];
  unchangingPattern: RegExp;
  /** The actions that only describe: a sentence read by one keeps its answer, and never changes the scene. */
  describing: ReadonlySet<string>;
  rules: readonly GrammarRule[];
}

/** A table the loader refuses, with the code of the first fault (the same code services/voice/grammar.py gives). */
export class VoiceGrammarError extends Error {
  constructor(
    readonly code: string,
    detail: string
  ) {
    super(`${code}: ${detail}`);
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const fault = (code: string, detail: string): never => {
  throw new VoiceGrammarError(code, detail);
};

/** The contract's own closed list of action types, read from AlbedianActionSchema. */
export function contractActionTypes(): string[] {
  return AlbedianActionSchema._def.schema.options.map((option) => option.shape.type.value);
}

/** Text of 1 to `max` UTF-16 units with no lone surrogate and no control character. */
function isBoundedText(value: unknown, max: number): value is string {
  return (
    typeof value === 'string' &&
    value.length >= 1 &&
    value.length <= max &&
    !/[\uD800-\uDFFF]/.test(value.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, '')) &&
    !/[\u0000-\u001F\u007F-\u009F]/.test(value)
  );
}

/** A word: letters and digits of the letters table, with one ', - or / between two runs of them. No ASCII capital. */
function isWord(word: string): boolean {
  const chars = [...word];
  if (chars.length === 0) return false;
  return chars.every((char, index) => {
    if (JOINERS.has(char)) return index > 0 && index < chars.length - 1 && !JOINERS.has(chars[index - 1]!) && !JOINERS.has(chars[index + 1]!);
    if (char >= 'A' && char <= 'Z') return false;
    return isLetterOrDigit(char.codePointAt(0)!);
  });
}

/** A phrase: words parted by one space, and in a words rule at most one ' ... ' gap between two parts. */
function isPhrase(phrase: unknown, gapAllowed: boolean, max: number): phrase is string {
  if (typeof phrase !== 'string' || phrase.length < 1 || phrase.length > max) return false;
  const words = phrase.split(' ');
  const gaps = words.filter((word) => word === GAP).length;
  if (gaps > (gapAllowed ? 1 : 0)) return false;
  if (words[0] === GAP || words[words.length - 1] === GAP) return false;
  return words.every((word) => word === GAP || isWord(word));
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\/-]/g, '\\$&');
/** One phrase as a pattern of whole words; a gap asks for the second part later in the same sentence. */
function phraseSource(phrase: string): string {
  const [first, second] = phrase.split(` ${GAP} `);
  return second === undefined ? `\\b${escape(first!)}\\b` : `\\b${escape(first!)}\\b[^.?!]*?\\b${escape(second)}\\b`;
}
/** Longest phrase first, so a phrase inside another never cuts the longer one short. */
function longestFirst(phrases: readonly string[]): string[] {
  return [...phrases].sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0));
}
function alternation(phrases: readonly string[]): string {
  return longestFirst(phrases).map(phraseSource).join('|');
}
/** The alternation with each gap a named group, and where each gap starts in its phrase's match. */
function cutAlternation(phrases: readonly string[]): { source: string; starts: Record<string, number> } {
  const starts: Record<string, number> = {};
  const sources = longestFirst(phrases).map((phrase) => {
    const [first, second] = phrase.split(` ${GAP} `);
    if (second === undefined) return phraseSource(phrase);
    const name = `g${Object.keys(starts).length}`;
    starts[name] = first!.length;
    return `\\b${escape(first!)}\\b(?<${name}>[^.?!]*?)\\b${escape(second)}\\b`;
  });
  return { source: sources.join('|'), starts };
}

function checkWith(action: string, value: unknown, base: Record<string, unknown>, ruleId: string, code: string): Record<string, unknown> {
  const spec = WITH_FIELDS[action];
  if (!spec || !isRecord(value) || Object.keys(value).length === 0) return fault(code, `${ruleId}: no fields to fix for ${action}`);
  for (const key of Object.keys(value)) if (!spec.keys.includes(key)) fault(code, `${ruleId}: ${action} takes no fixed ${key}`);
  const merged = { ...base, ...value };
  const sample = { type: action, requestId: 'req_sample', expectedViewRevision: 0, ...spec.sample, ...merged };
  if (!AlbedianActionSchema.safeParse(sample).success) fault(code, `${ruleId}: the contract refuses ${action} with ${JSON.stringify(merged)}`);
  return value;
}

/** The grammar in `table`, checked whole; throws VoiceGrammarError with the first fault. */
export function loadVoiceGrammar(table: unknown): VoiceGrammar {
  if (!isRecord(table)) return fault('not_object', 'the table is not an object');
  for (const key of Object.keys(table)) if (!TOP_KEYS.has(key)) fault('unknown_key', `the table holds ${key}`);
  if (table.format !== GRAMMAR_FORMAT || table.formatVersion !== 1) fault('format', 'not format 1 of the voice command grammar');
  if (!isBoundedText(table.about, GRAMMAR_LIMITS.aboutUtf16)) fault('about', 'about is no text of 1 to 2000 units');
  const rawRules = table.rules;
  if (!Array.isArray(rawRules) || rawRules.length < 1 || rawRules.length > GRAMMAR_LIMITS.rules) return fault('rules', 'rules is no list of 1 to 64');
  const actions = new Set(contractActionTypes());
  const ids = new Set<string>();
  const phrasesSeen = new Set<string>();
  const codeMatches = new Set<string>();
  const rules: GrammarRule[] = [];
  for (const raw of rawRules) {
    if (!isRecord(raw)) return fault('rule_not_object', 'a rule is not an object');
    for (const key of Object.keys(raw)) if (!RULE_KEYS.has(key)) fault('unknown_key', `a rule holds ${key}`);
    const id = raw.id;
    if (typeof id !== 'string' || id.length > GRAMMAR_LIMITS.idUtf16 || !ID_PATTERN.test(id)) return fault('id', `${String(id)} is no rule id`);
    if (ids.has(id)) fault('id_repeated', `${id} is named twice`);
    ids.add(id);
    const action = raw.action;
    if (typeof action !== 'string' || !actions.has(action)) return fault('action', `${id}: ${String(action)} is no contract action`);
    const match = raw.match;
    if (typeof match !== 'string' || !(GRAMMAR_MATCHES as readonly string[]).includes(match)) return fault('match', `${id}: ${String(match)} is no match`);
    let phrases: string[] = [];
    // A year rule may hold the words that frame a year (show the sky in ...), which it accounts for and never matches
    // by: its code reads the year
    if (PHRASE_MATCHES.has(match) || (match === 'year' && raw.phrases !== undefined)) {
      const list = raw.phrases;
      if (!Array.isArray(list) || list.length < 1 || list.length > GRAMMAR_LIMITS.phrases) return fault('phrases', `${id}: phrases is no list of 1 to 64`);
      for (const phrase of list) {
        if (!isPhrase(phrase, match === 'words', GRAMMAR_LIMITS.phraseUtf16)) fault('phrase', `${id}: ${JSON.stringify(phrase)} is no phrase`);
        if (phrasesSeen.has(phrase as string)) fault('phrase_repeated', `${id}: ${JSON.stringify(phrase)} is in another rule`);
        phrasesSeen.add(phrase as string);
      }
      phrases = list as string[];
    } else if (raw.phrases !== undefined) fault('phrases', `${id}: a ${match} rule is read by code and holds no phrases`);
    if (match in CODE_MATCH_ACTION) {
      if (CODE_MATCH_ACTION[match] !== action) fault('match', `${id}: a ${match} rule forms ${CODE_MATCH_ACTION[match]}`);
      if (codeMatches.has(match)) fault('match', `${id}: a second ${match} rule`);
      codeMatches.add(match);
    }
    const target = raw.target;
    if (TARGET_ACTIONS.has(action)) {
      if (typeof target !== 'string' || !(GRAMMAR_TARGETS as readonly string[]).includes(target)) fault('target', `${id}: ${action} says how its target is found`);
      if (match === 'name' && target !== 'about') fault('target', `${id}: a name rule keeps what the words refuse`);
    } else if (target !== undefined) fault('target', `${id}: ${action} takes no target`);
    const fixed = raw.with === undefined ? {} : checkWith(action, raw.with, {}, id, 'with');
    let modifiers: GrammarModifier[] = [];
    if (raw.modifiers !== undefined) {
      const list = raw.modifiers;
      if (!Array.isArray(list) || list.length < 1 || list.length > GRAMMAR_LIMITS.modifiers) return fault('modifiers', `${id}: modifiers is no list of 1 to 8`);
      modifiers = list.map((modifier) => {
        if (!isRecord(modifier)) return fault('modifiers', `${id}: a modifier is not an object`);
        for (const key of Object.keys(modifier)) if (!MODIFIER_KEYS.has(key)) fault('unknown_key', `a modifier holds ${key}`);
        const words = modifier.words;
        if (!Array.isArray(words) || words.length < 1 || words.length > GRAMMAR_LIMITS.modifierWords) return fault('modifiers', `${id}: words is no list of 1 to 16`);
        for (const word of words) if (!isPhrase(word, false, GRAMMAR_LIMITS.phraseUtf16)) fault('modifiers', `${id}: ${JSON.stringify(word)} is no phrase`);
        const ownWords = [...new Set((words as string[]).flatMap((phrase) => phrase.split(' ')))];
        return {
          words: words as string[],
          with: checkWith(action, modifier.with, fixed, id, 'modifiers'),
          pattern: new RegExp(alternation(words as string[])),
          cut: new RegExp(alternation(ownWords)),
        };
      });
    }
    const rule: GrammarRule = { id, action, match: match as GrammarMatch, phrases, with: fixed, modifiers };
    if (target !== undefined) rule.target = target as GrammarTarget;
    if (match === 'words' || match === 'start' || match === 'seconds') {
      rule.pattern = new RegExp(match === 'start' ? `^(?:${alternation(phrases)})` : alternation(phrases));
    }
    if (match === 'words' || match === 'start' || match === 'seconds' || (match === 'year' && phrases.length > 0)) {
      const { source, starts } = cutAlternation(phrases);
      rule.cut = new RegExp(match === 'start' ? `^(?:${source})` : source);
      rule.gapStarts = starts;
    }
    rules.push(rule);
  }
  if (rules.filter((rule) => rule.match === 'rest').length !== 1 || rules[rules.length - 1]!.match !== 'rest') fault('rest', 'the last rule, and only it, is the rest');
  const before = table.namesRefusedBefore;
  if (typeof before !== 'string' || !ids.has(before)) fault('names_refused_before', `${String(before)} names no rule`);
  const folds = table.folds;
  if (typeof folds !== 'object' || folds === null || Array.isArray(folds) || Object.keys(folds).length < 1 || Object.keys(folds).length > GRAMMAR_LIMITS.folds) {
    fault('folds', 'folds is no object of 1 to 16');
  }
  for (const [typed, plain] of Object.entries(folds as Record<string, unknown>)) {
    // One code point outside ASCII and not a surrogate, as Python counts one character
    const points = Array.from(typed);
    if (points.length !== 1 || typed.codePointAt(0)! < 0x80 || /[\uD800-\uDFFF]/u.test(typed)) fault('folds', `${JSON.stringify(typed)} is no one character outside ASCII`);
    if (typeof plain !== 'string' || plain.length !== 1 || !(plain.charCodeAt(0) > 0x20 && plain.charCodeAt(0) < 0x7f)) {
      fault('folds', `${JSON.stringify(typed)} folds to no one printable ASCII character`);
    }
  }
  const groups = table.unchanging;
  if (!Array.isArray(groups) || groups.length < 1 || groups.length > GRAMMAR_LIMITS.unchangingGroups) fault('unchanging', 'unchanging is no list of 1 to 16 groups');
  const unchanging: string[] = [];
  for (const group of groups as unknown[]) {
    if (typeof group !== 'object' || group === null || Array.isArray(group) || Object.keys(group).some((key) => !UNCHANGING_KEYS.has(key))) {
      fault('unchanging', 'a group of unchanging words is no object of words and why');
    }
    const { words, why } = group as { words?: unknown; why?: unknown };
    if (!Array.isArray(words) || words.length < 1 || words.length > GRAMMAR_LIMITS.modifierWords) fault('unchanging', "a group's words is no list of 1 to 16");
    for (const word of words as unknown[]) {
      if (!isPhrase(word, false, GRAMMAR_LIMITS.phraseUtf16)) fault('unchanging', `${JSON.stringify(word)} is no phrase`);
      if (unchanging.includes(word as string)) fault('unchanging', `${JSON.stringify(word)} is listed twice`);
      unchanging.push(word as string);
    }
    if (!isBoundedText(why, GRAMMAR_LIMITS.whyUtf16)) fault('unchanging', 'a group says no why of 1 to 200 units');
  }
  const known = new Set<string>(contractActionTypes());
  const describing = table.describingActions;
  if (!Array.isArray(describing) || describing.length < 1 || describing.length > known.size || new Set(describing.map((d) => JSON.stringify(d))).size !== describing.length) {
    fault('describing_actions', 'describingActions is no list of 1 or more different actions');
  }
  for (const action of describing as unknown[]) {
    if (typeof action !== 'string' || !known.has(action) || action === 'navigation.cancel' || action === 'question.ask') {
      fault('describing_actions', `${JSON.stringify(action)} is no action of the contract that only describes`);
    }
  }
  return {
    namesRefusedBefore: before as string,
    folds: { ...(folds as Record<string, string>) },
    unchanging,
    unchangingPattern: new RegExp(alternation(unchanging), 'g'),
    describing: new Set(describing as string[]),
    rules,
  };
}

/** What a whole-sentence rule leaves off the end of a request: the sentence's own stop and spaces. */
const WHOLE_ENDING = /[\s.!?,]+$/;

/** `text` with each typographic apostrophe or quote of the table read as its plain one, one unit for one. */
export function fold(grammar: VoiceGrammar, text: string): string {
  return Array.from(text, (char) => grammar.folds[char] ?? char).join('');
}

/** `text` with every match of `pattern` turned to spaces of its own length, so places stay where they were. */
export function blanked(pattern: RegExp, text: string): string {
  return text.replace(new RegExp(pattern.source, 'g'), (match) => ' '.repeat(match.length));
}

/**
 * `text` with the rule's own phrase and modifier words turned to spaces, but for the gap of a gap phrase, which holds the
 * request's own words: what is left is what the rule does not account for. A whole-sentence rule accounts for all of
 * it, since the request is its phrase (services/voice/grammar.py own_words_cut).
 */
export function ownWordsCut(rule: GrammarRule, text: string): string {
  if (rule.match === 'whole') return ' '.repeat(text.length);
  const chosen = rule.modifiers.find((m) => m.pattern.test(text));
  let rest = rule.cut ? text.replace(new RegExp(rule.cut.source, 'g'), (...args) => ownWordsCutOne(rule, args)) : text;
  if (chosen !== undefined) rest = blanked(chosen.cut, rest);
  return rest;
}

/** The match of a second modifier of `rule` that asks for other fields than the first, or undefined. */
export function conflictingModifier(rule: GrammarRule, request: string): string | undefined {
  const chosen = rule.modifiers.find((m) => m.pattern.test(request));
  if (chosen === undefined) return undefined;
  const withoutChosen = blanked(chosen.cut, request);
  for (const other of rule.modifiers) {
    if (other !== chosen && !sameFields(other.with, chosen.with)) {
      const hit = withoutChosen.match(other.pattern);
      if (hit) return hit[0];
    }
  }
  return undefined;
}

/** Whether a rule's action changes the scene: every action but a question and one that only describes. */
export function changesTheScene(grammar: VoiceGrammar, rule: GrammarRule): boolean {
  return rule.action !== 'question.ask' && !grammar.describing.has(rule.action);
}

/** Whether two rules fix the same fields to the same values, whatever the order of their keys (Python's dict equality). */
function sameFields(a: Readonly<Record<string, unknown>>, b: Readonly<Record<string, unknown>>): boolean {
  const sorted = (fields: Readonly<Record<string, unknown>>) => JSON.stringify(Object.keys(fields).sort().map((key) => [key, fields[key]]));
  return sorted(a) === sorted(b);
}

// What joins two requests in one sentence: a second request is read only after one of these
const JOINED = /[,;]|\b(?:and|then|but|also|plus|or)\b/;
// Pointing phrases of the select rule ("that one", "this one"), which point at a target rather than asking a second command
const POINTING_PHRASE = /\b(?:that|this)\s+one\b/;

/**
 * Whether `rest` (the request with `rule`'s own words cut) joins a second request to it: a joining word, and what another
 * rule that changes the scene asks for, or a conflicting modifier of `rule` itself.
 */
export function anotherRequest(grammar: VoiceGrammar, rule: GrammarRule, rest: string, request = ''): boolean {
  const cleaned = blanked(POINTING_PHRASE, rest);
  if (!JOINED.test(cleaned)) return false;
  if (request !== '' && conflictingModifier(rule, request) !== undefined) return true;
  return grammar.rules.some(
    (other) =>
      other !== rule &&
      changesTheScene(grammar, other) &&
      (other.action !== rule.action || !sameFields(other.with, rule.with)) &&
      (other.match === 'words' || other.match === 'start' || other.match === 'seconds') &&
      other.pattern !== undefined &&
      other.pattern.test(cleaned)
  );
}

/** A match of the rule's own phrase turned to spaces, but for the gap of a gap phrase, which the request said. */
function ownWordsCutOne(rule: GrammarRule, args: unknown[]): string {
  const text = args[0] as string;
  const groups = args[args.length - 1] as Record<string, string | undefined> | string;
  if (typeof groups === 'object') {
    for (const [name, start] of Object.entries(rule.gapStarts ?? {})) {
      const gap = groups[name];
      if (gap !== undefined) return ' '.repeat(start) + gap + ' '.repeat(text.length - start - gap.length);
    }
  }
  return ' '.repeat(text.length);
}

/** Whether `rule` asks for this request (lower case, trimmed). Code reads year, seconds, name and rest itself. */
export function ruleSays(rule: GrammarRule, request: string): boolean {
  if (rule.match === 'whole') return rule.phrases.includes(request.replace(WHOLE_ENDING, ''));
  return rule.pattern !== undefined && rule.pattern.test(request);
}

/** The rule's fixed fields, changed by its first modifier whose word the request holds. */
export function fieldsFor(rule: GrammarRule, request: string): Record<string, unknown> {
  const modifier = rule.modifiers.find((m) => m.pattern.test(request));
  return { ...rule.with, ...(modifier?.with ?? {}) };
}

/** The grammar both parsers read. */
export const VOICE_GRAMMAR: VoiceGrammar = loadVoiceGrammar(VOICE_COMMAND_GRAMMAR_TABLE);
export const grammarRule = (id: string): GrammarRule => {
  const rule = VOICE_GRAMMAR.rules.find((r) => r.id === id);
  if (!rule) throw new Error(`no voice grammar rule ${id}`);
  return rule;
};
