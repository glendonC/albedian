import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { ObservationSolutionSchema, VOICE_COMMAND_GRAMMAR_TABLE } from '@albedian/contracts';
import type { ObservationSession } from '@albedian/contracts';
import { buildActionContext, HANDLED_RULES, parseUtteranceToAction } from '../voiceParser.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';
import { contractActionTypes, fieldsFor, loadVoiceGrammar, ruleSays, VOICE_GRAMMAR, VoiceGrammarError } from '../voiceGrammar.js';

/**
 * The voice command grammar as the app's parser loads it, judged on the cases the backend's loader is judged on
 * (tests/fixtures/voice_grammar_cases.json, written by tests/integration/test_voice_grammar.py): every faulty table
 * is refused with the same code, the real one is taken, every request is said by the same rules with the same
 * fields, and both sides list the contract's same actions.
 */
const ROOT = path.resolve(__dirname, '../../../..');
interface Change {
  at: (string | number)[];
  set?: unknown;
  drop?: boolean;
}
const CASES = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_grammar_cases.json'), 'utf8')) as {
  actions: string[];
  tables: { what: string; code: string | null; changes: Change[] }[];
  requests: { request: string; says: string[]; fields: Record<string, Record<string, unknown>> }[];
};

/** The real table with the stored changes made in order, as apply_changes makes it in the backend's test. */
function applyChanges(changes: Change[]): unknown {
  let table: unknown = structuredClone(VOICE_COMMAND_GRAMMAR_TABLE);
  for (const change of changes) {
    if (change.at.length === 0) {
      table = change.set;
      continue;
    }
    let parent = table as Record<string | number, unknown>;
    for (const step of change.at.slice(0, -1)) parent = parent[step] as Record<string | number, unknown>;
    const last = change.at[change.at.length - 1]!;
    if (change.drop) delete parent[last];
    else parent[last] = change.set;
  }
  return table;
}

function codeOf(table: unknown): string | null {
  try {
    loadVoiceGrammar(table);
    return null;
  } catch (error) {
    if (error instanceof VoiceGrammarError) return error.code;
    throw error;
  }
}

describe('@albedian/observation voice grammar: the table both parsers read', () => {
  it('lists the contract actions the backend lists', () => {
    expect([...contractActionTypes()].sort()).toEqual(CASES.actions);
  });

  it('judges every table as the backend does', () => {
    expect(CASES.tables.length).toBeGreaterThan(80);
    const judged = CASES.tables.map((c) => ({ what: c.what, code: codeOf(applyChanges(c.changes)) }));
    expect(judged).toEqual(CASES.tables.map((c) => ({ what: c.what, code: c.code })));
    expect(
      codeOf({
        ...(VOICE_COMMAND_GRAMMAR_TABLE as Record<string, unknown>),
        folds: Object.assign([], { '\u2019': "'" }),
      })
    ).toBe('folds');
  });

  it('reads every request with the rules and fields the backend reads', () => {
    expect(CASES.requests.length).toBeGreaterThan(1000);
    const read = CASES.requests.map(({ request }) => {
      const says = VOICE_GRAMMAR.rules.filter((rule) => ruleSays(rule, request));
      return {
        request,
        says: says.map((rule) => rule.id),
        fields: Object.fromEntries(says.filter((rule) => rule.modifiers.length > 0).map((rule) => [rule.id, fieldsFor(rule, request)])),
      };
    });
    expect(read).toEqual(CASES.requests);
  });

  it("is the app parser's grammar: it reads every rule of the table", () => {
    expect([...HANDLED_RULES].sort()).toEqual(VOICE_GRAMMAR.rules.map((rule) => rule.id).sort());
  });

  it('cuts a longer phrase whole, whatever order the table lists it in', () => {
    const table = structuredClone(VOICE_COMMAND_GRAMMAR_TABLE) as { rules: { id: string; phrases?: string[] }[] };
    table.rules.find((rule) => rule.id === 'compare')!.phrases = ['zq', 'zq zr'];
    const rule = loadVoiceGrammar(table).rules.find((r) => r.id === 'compare')!;
    expect(rule.pattern!.exec('so zq zr now')?.[0]).toBe('zq zr');
  });

  // One request per rule of the table and what the app forms of it with a finding selected in a calibrated sky
  const base = createSyntheticCalibratedSession();
  const selected = base.observationSolution!.findings[0]!.id;
  const session = { ...base, selectedFindingId: selected };
  const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
  it.each([
    ['stop', 'navigation.cancel', {}],
    ['pause transition', 'navigation.cancel', {}],
    ['back a little', 'history.back', { mode: 'partial', fraction: 0.35 }],
    ['go back halfway', 'history.back', { mode: 'partial', fraction: 0.5 }],
    ['the last checkpoint', 'history.back', { mode: 'checkpoint' }],
    ['back', 'source.return', { restoreAudio: true }],
    ['go back', 'source.return', { restoreAudio: true }],
    ['step back', 'history.back', { mode: 'checkpoint' }],
    ['show constellation lines', 'ui.overlay', { overlay: 'constellationLines', enabled: true }],
    ['disable constellation lines', 'ui.overlay', { overlay: 'constellationLines', enabled: false }],
    ['denoise the photo of omega centauri', 'rejected', { kind: 'not_in_this_sky', said: 'omega centauri', name: 'Omega Centauri' }],
    ['hide the predicted sky', 'rejected', { kind: 'not_a_request', said: 'hide' }],
    ['turn off the predicted sky', 'rejected', { kind: 'not_a_request', said: 'turn' }],
    ['what does predicted mean', 'question.ask', {}],
    ["what's the difference between measured and predicted", 'question.ask', {}],
    ['wait what is that', 'finding.describe', {}],
    ['is that a planet?', 'finding.describe', { findingId: selected, fields: ['type', 'evidence'] }],
    ['show the planets', 'sky.show', { mode: 'predicted' }],
    ['stop.', 'navigation.cancel', {}],
    ["don't change the people", 'media.restore', { profile: 'star_protected' }],
    ['no, select that one', 'rejected', { kind: 'not_a_request', said: 'no' }],
    ['hide the residual vectors', 'ui.overlay', { overlay: 'residualVectors', enabled: false }],
    ['compare before and after', 'media.compare', { preserveTime: true }],
    ['denoise', 'media.restore', { profile: 'star_protected' }],
    ['temporal stack', 'media.restore', { profile: 'temporal' }],
    ['restore with a natural tone', 'media.restore', { profile: 'natural' }],
    ['seek to 7.5 seconds', 'media.seek', { sourceTimeSec: 7.5, preserveAudioSync: true }],
    ['jump 3 s', 'media.seek', { sourceTimeSec: 3 }],
    ['show the sky', 'sky.show', { mode: 'measured' }],
    ['orbit it', 'space.viewpoint', { targetFindingId: selected, trajectory: 'orbit' }],
    ['shift viewpoint around it', 'space.viewpoint', { trajectory: 'translate' }],
    ['how far is it', 'finding.describe', { findingId: selected, fields: ['distance', 'evidence'] }],
    ['what is behind it', 'finding.describe', { fields: ['behind_sightline'] }],
    ['fly there', 'space.enter', { findingId: selected }],
    ['select that one', 'finding.select', { findingId: selected }],
    ['describe it', 'finding.describe', { fields: ['distance', 'type', 'motion', 'evidence'] }],
    ['where is it', 'finding.describe', { findingId: selected }],
    ['focus on it', 'finding.select', { findingId: selected }],
    ['show me it', 'finding.select', { findingId: selected }],
    ['show the predicted planets', 'sky.show', { mode: 'predicted' }],
    ['show the constellations', 'sky.show', { mode: 'measured' }],
    // answers-3b v3: the readings the lead's request names, one view (Rigel selected in a solved sky)
    ["let's not fly there", 'rejected', { kind: 'not_a_request', said: 'not' }],
    ["I didn't say fly there", 'rejected', { kind: 'not_a_request', said: 'i' }],
    ['I would not restore it', 'rejected', { kind: 'not_a_request', said: 'i' }],
    ["you shouldn't restore it", 'rejected', { kind: 'not_a_request', said: 'you' }],
    ['can you not show the planets', 'rejected', { kind: 'not_a_request', said: 'not' }],
    ['not the predicted sky', 'rejected', { kind: 'not_a_request', said: 'not' }],
    ['not back to the moment', 'rejected', { kind: 'not_a_request', said: 'not' }],
    ['not Rigel', 'question.ask', {}],
    ['anything but Rigel', 'question.ask', {}],
    ['not that one', 'rejected', { kind: 'not_a_request', said: 'not' }],
    ["don't hide the constellation lines", 'rejected', { kind: 'not_a_request', said: 'don' }],
    ["I don't want to remove the constellation lines", 'rejected', { kind: 'not_a_request', said: 'i' }],
    ['get rid of the predicted sky', 'rejected', { kind: 'not_a_request', said: 'get' }],
    ['take away the predicted sky', 'rejected', { kind: 'not_a_request', said: 'take' }],
    ['close the predicted sky', 'rejected', { kind: 'not_a_request', said: 'close' }],
    ['undo the restore', 'rejected', { kind: 'not_a_request', said: 'undo' }],
    ['remove the restore', 'rejected', { kind: 'not_a_request', said: 'remove' }],
    ['did you restore it', 'rejected', { kind: 'not_a_request', said: 'did' }],
    ['should I restore it', 'rejected', { kind: 'not_a_request', said: 'should' }],
    ['what happens if I restore it', 'rejected', { kind: 'not_a_request', said: 'what' }],
    ['is the predicted sky on', 'rejected', { kind: 'not_a_request', said: 'is' }],
    ['why is the predicted sky showing', 'rejected', { kind: 'not_a_request', said: 'why' }],
    ['are the constellation lines on', 'rejected', { kind: 'not_a_request', said: 'are' }],
    ['how do I go back', 'rejected', { kind: 'not_a_request', said: 'how' }],
    ['why did you select Rigel', 'rejected', { kind: 'not_a_request', said: 'why' }],
    ["won't you show the planets", 'rejected', { kind: 'not_a_request', said: 'won' }],
    ["can't you clean this up", 'rejected', { kind: 'not_a_request', said: 'can' }],
    ["why don't you show the planets", 'rejected', { kind: 'not_a_request', said: 'why' }],
    ['how do I show the planets', 'rejected', { kind: 'not_a_request', said: 'how' }],
    ["don't stop", 'question.ask', {}],
    ['do not cancel', 'question.ask', {}],
    ['how do you know which star that is', 'rejected', { kind: 'not_a_request', said: 'how' }],
    ['show me the sky as it was', 'question.ask', {}],
    ['select Betelgeuse, not Zorblax', 'rejected', { kind: 'not_understood', said: 'zorblax' }],
    ["I don't want the constellation lines", 'rejected', { kind: 'not_a_request', said: 'i' }],
    ['no constellation lines', 'rejected', { kind: 'not_a_request', said: 'no' }],
    ['remove the constellation lines', 'ui.overlay', { overlay: 'constellationLines', enabled: false }],
    ['kill the constellation lines', 'ui.overlay', { overlay: 'constellationLines', enabled: false }],
    ['no earth direction', 'rejected', { kind: 'not_a_request', said: 'no' }],
    ['stop the flight', 'navigation.cancel', {}],
    ['stop flying', 'navigation.cancel', {}],
    ['stop moving', 'navigation.cancel', {}],
    ['stop the transition', 'navigation.cancel', {}],
    ['cancel the flight', 'navigation.cancel', {}],
    ['show me it from a different side', 'space.viewpoint', { targetFindingId: selected }],
    // An opener at the start of a clause, after a comma or a stop
    ['ok, did you restore it', 'rejected', { kind: 'not_a_request', said: 'did' }],
    ['select it, why is the predicted sky showing', 'rejected', { kind: 'not_understood', said: 'predicted' }],
    ['fine. should I restore it', 'rejected', { kind: 'not_a_request', said: 'fine' }],
    ["don't compare before and after", 'rejected', { kind: 'not_a_request', said: 'don' }],
    ['what is an a/b wipe', 'rejected', { kind: 'not_understood', said: 'b wipe' }],
    ['denoise the planet', 'rejected', { kind: 'not_a_request', said: 'planet' }],
    ['ran restore it', 'rejected', { kind: 'not_a_request', said: 'ran' }],
    ['why is the sky dark at night', 'question.ask', {}],
  ] as const)('forms "%s" as %s', (utterance, type, fields) => {
    const parsed = parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
    if (type === 'rejected') {
      // No action (answers-3b v4): the refusal, its kind and its quote, as the backend gives them
      expect(parsed.action, utterance).toBeUndefined();
      expect(parsed.refusal, utterance).toEqual(fields);
      return;
    }
    expect(parsed.action?.type, `${parsed.error}`).toBe(type);
    const action = parsed.action as unknown as Record<string, unknown>;
    expect(Object.fromEntries(Object.keys(fields).map((key) => [key, action[key]]))).toEqual(fields);
  });

  it('compiles every pattern of the table with no Unicode or case flag, as the backend compiles them ASCII', () => {
    const patterns = VOICE_GRAMMAR.rules.flatMap((rule) => (rule.pattern ? [rule.pattern] : []));
    expect(patterns.length).toBe(VOICE_GRAMMAR.rules.filter((rule) => ['words', 'start', 'seconds'].includes(rule.match)).length);
    const cuts = VOICE_GRAMMAR.rules.flatMap((rule) => (rule.cut ? [rule.cut] : []));
    expect(cuts.length).toBe(patterns.length + 1);
    const modPatterns = VOICE_GRAMMAR.rules.flatMap((rule) => rule.modifiers.flatMap((m) => [m.pattern, m.cut]));
    // The unchanging words' pattern is global (it blanks every one), as Python's sub does
    expect([...patterns, ...cuts, ...modPatterns].map((p) => p.flags)).toEqual(
      Array(patterns.length + cuts.length + modPatterns.length).fill('')
    );
    expect(VOICE_GRAMMAR.unchangingPattern.flags).toBe('g');
  });

  // Every word accounted for, held by a property generated from one fixture (answers-3b v4), as services/voice reads it
  it('stops every acting sentence with a word no rule holds, and lets no unchanging word change one', () => {
    const PROPERTY = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_class_property.json'), 'utf8')) as {
      unknown: string;
      gapFill: string;
      secondsAfter: string;
      codeRead: Record<string, string>;
      counts: { backend: [number, number, number]; app: [number, number, number] };
      actsWithAnUnknownWord: { sentence: string; reason: string }[];
      changedByAnUnchangingWord: { sentence: string; reason: string }[];
    };
    type Raw = { action: string; match: string; phrases?: string[] };
    const table = VOICE_COMMAND_GRAMMAR_TABLE as unknown as { rules: Raw[]; unchanging: { words: string[] }[]; describingActions: string[] };
    // The parity test's view: Rigel selected in the backend's own solved sky (tests/fixtures/python_contract_solutions.json)
    const solutions = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, unknown>;
    const rigel: ObservationSession = {
      ...base,
      machines: { ...base.machines, calibration: 'calibrated' },
      observationSolution: ObservationSolutionSchema.parse(solutions.plate_calibrated),
      selectedFindingId: 'finding-star-rigel',
      focusedFindingId: null,
    };
    // An action that changes the scene, as its type and target; undefined for no action, a question or a description
    const sceneChange = (text: string): string | undefined => {
      const action = parseUtteranceToAction(text, buildActionContext(rigel), rigel, { now: NOW }).action as Record<string, unknown> | undefined;
      if (!action || action.type === 'question.ask' || table.describingActions.includes(action.type as string)) return undefined;
      return JSON.stringify([action.type, action.findingId ?? action.targetFindingId ?? null]);
    };
    const bases: [Raw, string][] = [];
    for (const rule of table.rules) {
      if (rule.action === 'question.ask' || table.describingActions.includes(rule.action) || rule.match === 'rest') continue;
      let phrases = PROPERTY.codeRead[rule.match] !== undefined
        ? [PROPERTY.codeRead[rule.match]!]
        : rule.phrases!.map((phrase) => phrase.replace(' ... ', ` ${PROPERTY.gapFill} `));
      if (rule.match === 'seconds') phrases = phrases.map((phrase) => phrase + PROPERTY.secondsAfter);
      for (const phrase of phrases) if (sceneChange(phrase) !== undefined) bases.push([rule, phrase]);
    }
    const unchanging = table.unchanging.flatMap((group) => group.words);
    const unknown: [string, string | undefined][] = [];
    const alike: [string, string | undefined][] = [];
    for (const [rule, base] of bases) {
      const reading = sceneChange(base);
      const words = base.split(' ');
      for (let i = 0; i <= words.length; i += 1) unknown.push([[...words.slice(0, i), PROPERTY.unknown, ...words.slice(i)].join(' '), reading]);
      if (rule.match !== 'whole') for (const word of unchanging) alike.push([`${word} ${base}`, reading], [`${base} ${word}`, reading]);
    }
    expect([bases.length, unknown.length, alike.length]).toEqual(PROPERTY.counts.app);
    const acted = [...new Set(unknown.filter(([text]) => sceneChange(text) !== undefined).map(([text]) => text))].sort();
    const changed = [...new Set(alike.filter(([text, reading]) => sceneChange(text) !== reading).map(([text]) => text))].sort();
    // The kept cases are exactly the fixture's lists, each with its reason
    expect(acted).toEqual(PROPERTY.actsWithAnUnknownWord.map((c) => c.sentence).sort());
    expect(changed).toEqual(PROPERTY.changedByAnUnchangingWord.map((c) => c.sentence).sort());
    expect([...PROPERTY.actsWithAnUnknownWord, ...PROPERTY.changedByAnUnchangingWord].every((c) => c.reason.length > 0)).toBe(true);
  }, 120_000);

  // A second request is read only across a joining word, and only when it asks for something else; the words that
  // speak of the recording or the app describe no thing (answers-3b v4), as services/voice reads them
  it('reads two requests only across a joining word and as something else asked, and no recording word as a thing', () => {
    const solutions = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, unknown>;
    const rigel: ObservationSession = {
      ...base,
      machines: { ...base.machines, calibration: 'calibrated' },
      observationSolution: ObservationSolutionSchema.parse(solutions.plate_calibrated),
      selectedFindingId: 'finding-star-rigel',
      focusedFindingId: null,
    };
    const read = (text: string) => {
      const parsed = parseUtteranceToAction(text, buildActionContext(rigel), rigel, { now: NOW });
      const action = parsed.action as { type: string; findingId?: string } | undefined;
      return [action ? [action.type, action.findingId ?? null] : null, parsed.refusal ?? null];
    };
    expect(read('turn on the constellation lines the residuals')).toEqual([null, { kind: 'not_a_request', said: 'residuals' }]);
    expect(read('turn on the constellation lines, turn off the residuals')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('select that one, lock on it')).toEqual([['finding.select', 'finding-star-rigel'], null]);
    // "point" describes the thing here and is a phrase of the other select rule: one request, the same thing asked
    expect(read('select that one, the bright point')).toEqual([['finding.select', 'finding-star-rigel'], null]);
    expect(read('select that one in view')).toEqual([null, { kind: 'not_a_request', said: 'view' }]);
    expect(read('compare before and after and fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it; fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it then fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it also fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it plus fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it or fly there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('show constellation lines restore')).toEqual([null, { kind: 'not_a_request', said: 'restore' }]);
    expect(read('go back, how far')).toEqual([['finding.describe', 'finding-star-rigel'], null]);
    expect(read('previous checkpoint, step back')).toEqual([null, { kind: 'not_a_request', said: 'step' }]);
    expect(read('back a little, step back')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('fly to it, that one')).toEqual([['space.enter', 'finding-star-rigel'], null]);
    expect(read('fly to it, this one')).toEqual([['space.enter', 'finding-star-rigel'], null]);
    expect(read('fly to rigel and rigel')).toEqual([null, { kind: 'not_a_request', said: 'and' }]);
    expect(read('stop the flight and take me back')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it and take me to Rigel')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it and tell me about Rigel')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore it and take me there')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('turn on the constellation lines, turn off the constellation lines')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('show the constellation lines, hide the constellation lines')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('restore with natural tone, temporal stack')).toEqual([null, { kind: 'two_requests' }]);
    expect(read('show hide the constellation lines')).toEqual([null, { kind: 'not_a_request', said: 'show' }]);
    expect(read('restore with natural temporal stack')).toEqual([null, { kind: 'not_a_request', said: 'temporal' }]);
  });

  it('PBT: any word outside the grammar stops an acting sentence at any place, and any unchanging word keeps it', () => {
    const seed = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
    const property = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_class_property.json'), 'utf8')) as {
      gapFill: string;
      secondsAfter: string;
      codeRead: Record<string, string>;
    };
    type Raw = { action: string; match: string; phrases?: string[] };
    const table = VOICE_COMMAND_GRAMMAR_TABLE as unknown as {
      rules: Raw[];
      unchanging: { words: string[] }[];
      describingActions: string[];
    };
    const solutions = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, unknown>;
    const rigel: ObservationSession = {
      ...base,
      machines: { ...base.machines, calibration: 'calibrated' },
      observationSolution: ObservationSolutionSchema.parse(solutions.plate_calibrated),
      selectedFindingId: 'finding-star-rigel',
      focusedFindingId: null,
    };
    const ctx = buildActionContext(rigel);
    const sceneChange = (text: string): string | undefined => {
      const action = parseUtteranceToAction(text, ctx, rigel, { now: NOW }).action as Record<string, unknown> | undefined;
      if (!action || action.type === 'question.ask' || table.describingActions.includes(action.type as string)) return undefined;
      return JSON.stringify([action.type, action.findingId ?? action.targetFindingId ?? null]);
    };

    const wholePhrases = new Set(
      table.rules.filter((r) => r.match === 'whole').flatMap((r) => r.phrases ?? [])
    );
    const basePhrases: string[] = [];
    for (const rule of table.rules) {
      if (rule.action === 'question.ask' || table.describingActions.includes(rule.action) || rule.match === 'rest') continue;
      let phrases =
        property.codeRead[rule.match] !== undefined
          ? [property.codeRead[rule.match]!]
          : rule.phrases!.map((phrase) => phrase.replace(' ... ', ` ${property.gapFill} `));
      if (rule.match === 'seconds') phrases = phrases.map((phrase) => phrase + property.secondsAfter);
      basePhrases.push(...phrases);
    }
    const paritySentences = (
      JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_refusal_parity.json'), 'utf8')) as { sentences: string[] }
    ).sentences;
    const candidates = [...new Set([...basePhrases, ...paritySentences])];
    const actingPool: [string, string][] = [];
    const nonWholePool: [string, string][] = [];
    for (const text of candidates) {
      const sc = sceneChange(text);
      if (sc !== undefined) {
        actingPool.push([text, sc]);
        if (!wholePhrases.has(text.toLowerCase().replace(/^[ .!?,]+|[ .!?,]+$/g, ''))) {
          nonWholePool.push([text, sc]);
        }
      }
    }
    const unchangingPool = table.unchanging.flatMap((group) => group.words);
    const heldWords = new Set<string>();
    for (const rel of [
      'packages/contracts/src/data/voice-command-grammar.json',
      'packages/observation/src/generated/catalogStarNames.ts',
      'packages/observation/src/voiceParser.ts',
      'tests/fixtures/python_contract_solutions.json',
    ]) {
      const raw = fs.readFileSync(path.join(ROOT, rel), 'utf8').toLowerCase();
      for (const m of raw.matchAll(/[a-z0-9]+/g)) heldWords.add(m[0]);
    }
    const outsideEnglish = [
      'did', 'should', 'never', 'dont', 'shant', 'oughtnt', 'nobody',
      'why', 'was', 'were', 'if', 'undo', 'forget', 'skip', 'explain', 'define', 'leave', 'exit',
    ];
    const alphaWord = fc
      .array(fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz'), { minLength: 2, maxLength: 10 })
      .map((chars) => chars.join(''))
      .filter((w) => !heldWords.has(w));
    const outsideArb = fc.oneof(fc.constantFrom(...outsideEnglish), alphaWord);

    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: actingPool.length - 1 }),
        fc.integer({ min: 0, max: nonWholePool.length - 1 }),
        outsideArb,
        fc.constantFrom(...unchangingPool),
        fc.integer({ min: 0, max: 1000 }),
        fc.boolean(),
        (actingIdx, nonWholeIdx, outsideWord, unchangingWord, placeSeed, before) => {
          const [baseText] = actingPool[actingIdx]!;
          const words = baseText.split(' ');
          const at = placeSeed % (words.length + 1);
          const withOutside = [...words.slice(0, at), outsideWord, ...words.slice(at)].join(' ');
          expect(sceneChange(withOutside), withOutside).toBeUndefined();

          const [keptBase, keptReading] = nonWholePool[nonWholeIdx]!;
          const withUnchanging = before ? `${unchangingWord} ${keptBase}` : `${keptBase} ${unchangingWord}`;
          expect(sceneChange(withUnchanging), withUnchanging).toBe(keptReading);
        }
      ),
      { seed, numRuns: 300 }
    );
  });
});
