import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { ObservationSolutionSchema, REFUSAL_KINDS, refusalSentence, type VoiceRefusal } from '@albedian/contracts';
import type { ObservationSession } from '@albedian/contracts';
import { isTableName } from '@albedian/contracts';
import { CATALOG_STAR_NAMES } from '../generated/catalogStarNames.js';
import { buildActionContext, parseUtteranceToAction, PREDICTED_BODY_NAMES, UNBAKED_DEEP_SKY_ROWS } from '../voiceParser.js';
import { fold, VOICE_GRAMMAR } from '../voiceGrammar.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * The app's parser against the backend's readings of the same 682 sentences in the same three views
 * (tests/fixtures/voice_refusal_parity.json, written by tests/integration/test_voice_refusal_parity.py):
 * - every refusal the app gives is typed, and its sentence is the contract's for its kind and this recording;
 * - a refusal the backend can know is given by both parsers alike: the same kind, said, row and sentence;
 * - where the two differ, the app's refusal is one only the photo's sky can give (the backend holds no photo).
 */
const ROOT = path.resolve(__dirname, '../../../..');
type Refusal = VoiceRefusal;
interface Reading {
  status: 'proposed' | 'needs_input' | 'rejected';
  reason?: string;
  explanation?: string;
  refusal?: Refusal;
  action?: string;
  target?: string;
}
const FIXTURE = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_refusal_parity.json'), 'utf8')) as {
  sentences: string[];
  views: { sample: string; selected: string | null; readings: Reading[] }[];
};
const SOLUTIONS = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, unknown>;
const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();

function sessionOf(sample: string, selected: string | null): ObservationSession {
  const solution = ObservationSolutionSchema.parse(SOLUTIONS[sample]);
  const calibration =
    solution.status === 'pending' ? 'not_requested' : solution.status === 'approximate' ? 'predicted_only' : solution.status;
  return { ...base, machines: { ...base.machines, calibration }, observationSolution: solution, selectedFindingId: selected, focusedFindingId: null };
}

const backendKind = (kind: string) => REFUSAL_KINDS[kind as keyof typeof REFUSAL_KINDS].backend;

// Where one parser refuses and the other forms a question or a command: tests/fixtures/voice_refusal_one_sided.json,
// written by this test with the variable below set to 1 and read by tests/integration/test_voice_refusal_parity.py
const ONE_SIDED_PATH = path.join(ROOT, 'tests/fixtures/voice_refusal_one_sided.json');
const ONE_SIDED_UPDATE_ENV = 'ALBEDIAN_UPDATE_VOICE_ONE_SIDED';
interface AppReading {
  status: 'proposed' | 'rejected';
  action?: string;
  target?: string;
  reason?: string;
  refusal?: Refusal;
}
interface OneSided {
  sample: string;
  selected: string | null;
  sentence: string;
  refuses: 'app' | 'backend';
  app: AppReading;
  backend: Reading;
}

/** What the app made of a sentence, in the shape of the backend's readings: the action and its target, or the refusal. */
function appReading(parsed: ReturnType<typeof parseUtteranceToAction>): AppReading {
  if (parsed.action) {
    const reading: AppReading = { status: 'proposed', action: parsed.action.type };
    if ('findingId' in parsed.action && typeof parsed.action.findingId === 'string') reading.target = parsed.action.findingId;
    return reading;
  }
  const reading: AppReading = { status: 'rejected' };
  if (parsed.reason) reading.reason = parsed.reason;
  if (parsed.refusal) reading.refusal = parsed.refusal;
  return reading;
}

describe('@albedian/observation voice parser: refusals alike with the backend', () => {
  it('holds 682 sentences in three views', () => {
    expect(FIXTURE.sentences.length).toBe(682);
    expect(new Set(FIXTURE.sentences).size).toBe(682);
    expect(FIXTURE.views.length).toBe(3);
  });

  it('gives the same kind and sentence as the backend for every refusal the backend can know', () => {
    const counts = { same: 0, appOnly: 0, appRefusals: 0, backendRefusals: 0, appRefusesBackendActs: 0, backendRefusesAppActs: 0 };
    const differences: string[] = [];
    const oneSided: string[] = [];
    const divergent: OneSided[] = [];
    for (const view of FIXTURE.views) {
      const session = sessionOf(view.sample, view.selected);
      const media = session.sourceAsset?.mediaKind === 'video' ? 'video' : 'photo';
      FIXTURE.sentences.forEach((text, index) => {
        const backend = view.readings[index]!;
        const app = parseUtteranceToAction(text, buildActionContext(session), session, { now: NOW });
        if (app.reason === 'ambiguous_target' || app.reason === 'not_one_request') {
          expect(app.refusal, text).toBeDefined();
        }
        if (app.refusal) {
          counts.appRefusals += 1;
          expect(app.error, text).toBe(refusalSentence(app.refusal, media));
          expect(app.reason && (REFUSAL_KINDS[app.refusal.kind].reasons as readonly string[]).includes(app.reason), text).toBe(true);
        }
        if (backend.refusal) counts.backendRefusals += 1;
        if (app.refusal && backendKind(app.refusal.kind)) {
          if (JSON.stringify(backend.refusal) === JSON.stringify(app.refusal) && backend.explanation === app.error) counts.same += 1;
          else if (backend.refusal || backend.status === "needs_input") differences.push(`${view.sample}/${view.selected} ${JSON.stringify(text)}: app ${JSON.stringify(app.refusal)}, backend ${JSON.stringify(backend.refusal ?? backend)}`);
          else {
            counts.appRefusesBackendActs += 1;
            oneSided.push(`app refuses, backend ${backend.action ?? backend.reason}: ${JSON.stringify(text)}`);
            divergent.push({ sample: view.sample, selected: view.selected, sentence: text, refuses: 'app', app: appReading(app), backend });
          }
        } else if (backend.refusal) {
          if (app.refusal && !backendKind(app.refusal.kind)) counts.appOnly += 1;
          else if (app.refusal || app.disambiguation) differences.push(`${view.sample}/${view.selected} ${JSON.stringify(text)}: backend ${JSON.stringify(backend.refusal)}, app ${JSON.stringify(app.refusal ?? app.disambiguation)}`);
          else {
            counts.backendRefusesAppActs += 1;
            oneSided.push(`backend refuses, app ${app.action?.type ?? app.reason}: ${JSON.stringify(text)}`);
            divergent.push({ sample: view.sample, selected: view.selected, sentence: text, refuses: 'backend', app: appReading(app), backend });
          }
        }
      });
    }
    console.log("refusal parity", JSON.stringify(counts), "differences", differences.length);
    expect(differences, differences.slice(0, 20).join("\n")).toEqual([]);
    // Both parsers read one command grammar (packages/contracts/src/data/voice-command-grammar.json), so no sentence is
    // refused by one and acted on by the other; the refusals only the app gives are those that need the photo's sky,
    // among them the six showpieces the backend says voice cannot reach and the app, holding the sky, says are not in
    // it (deepsky-answer-1, C33)
    expect(counts, oneSided.join("\n")).toEqual({ same: 487, appOnly: 49, appRefusals: 602, backendRefusals: 536, appRefusesBackendActs: 0, backendRefusesAppActs: 0 });
    for (const line of oneSided) expect(line, line).toMatch(/(backend no_grounded_answer|app question\.ask|app media\.compare|app navigation\.cancel|app ui\.overlay):/);
    // Every one-sided reading, held exactly in a tracked fixture that the backend's test also reads, so the list
    // changes only on purpose (answers-3b gives both parsers one command table)
    const text = `${JSON.stringify({ count: divergent.length, readings: divergent }, null, 1)}\n`;
    if (process.env[ONE_SIDED_UPDATE_ENV] === '1') fs.writeFileSync(ONE_SIDED_PATH, text, 'utf8');
    expect(fs.readFileSync(ONE_SIDED_PATH, 'utf8'), `regenerate with ${ONE_SIDED_UPDATE_ENV}=1`).toBe(text);
    expect(divergent.map((d) => `${d.refuses} refuses: ${d.sentence}`)).toEqual([]);
  });
});

describe('@albedian/observation voice parser: the refusals only the photo can give', () => {
  // A photo or a video of the backend's own solved sky, and an unresolved one
  const asMedia = (session: ObservationSession, mediaKind: 'photo' | 'video'): ObservationSession =>
    ({ ...session, sourceAsset: session.sourceAsset ? { ...session.sourceAsset, mediaKind } : session.sourceAsset }) as ObservationSession;
  const pending = sessionOf('pending_unsolved', null);
  const unresolved = sessionOf('plate_too_few_sources', null);
  const solved = sessionOf('plate_calibrated', null);
  const held = new Set((solved.observationSolution?.findings ?? []).map((f) => f.title));

  it.each(['photo', 'video'] as const)('names each kind with its sentence in a %s', (media) => {
    const cases: [ObservationSession, string, VoiceRefusal, string][] = [
      [solved, 'select the southern cross', { kind: 'southern_cross_not_in_this_sky' }, 'ambiguous_target'],
      [pending, 'select Vega', { kind: 'sky_not_worked_out' }, 'ambiguous_target'],
      [unresolved, 'select Vega', { kind: 'sky_not_worked_out' }, 'unresolved_sky'],
      [unresolved, 'take me there', { kind: 'nothing_in_view_sky_not_worked_out' }, 'unresolved_sky'],
    ];
    for (const name of ['Vega', 'Deneb', 'Polaris', 'Sirius']) {
      if (!held.has(name)) cases.push([solved, `select ${name}`, { kind: 'not_in_this_sky', said: name.toLowerCase(), name }, 'ambiguous_target']);
    }
    expect(cases.length).toBeGreaterThanOrEqual(4);
    for (const [session, text, refusal, reason] of cases) {
      const s = asMedia(session, media);
      const parsed = parseUtteranceToAction(text, buildActionContext(s), s, { now: NOW });
      expect(parsed.refusal, text).toEqual(refusal);
      expect(parsed.reason, text).toBe(reason);
      expect(parsed.error, text).toBe(refusalSentence(refusal, media));
    }
  });
});

describe('@albedian/observation voice parser: every name a refusal can show', () => {
  it('is a name as the tables write it, so its sentence is made by the ASCII rule', () => {
    const held = new Set([...CATALOG_STAR_NAMES.map(([, , shown]) => shown), ...Object.values(PREDICTED_BODY_NAMES), ...Object.keys(UNBAKED_DEEP_SKY_ROWS)]);
    expect(held.size).toBeGreaterThan(400);
    expect([...held].filter((name) => !isTableName(name))).toEqual([]);
  });
});

// The sentences that must change nothing and the two requests (answers-3b v4), as tests/integration/test_voice_refusal_parity.py
// holds the backend to them: no action that changes the scene in any view, and two requests refused as two
describe('@albedian/observation voice parser: a sentence that is not one request does nothing', () => {
  it('changes the scene for none of the lead\'s and the reader\'s sentences, and refuses two requests as two', () => {
    const rows = (JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_not_one_request.json'), 'utf8')) as {
      rows: { group: string; sentence: string }[];
    }).rows;
    expect(rows.length).toBe(49);
    for (const view of FIXTURE.views) {
      const session = sessionOf(view.sample, view.selected);
      for (const row of rows) {
        const parsed = parseUtteranceToAction(row.sentence, buildActionContext(session), session, { now: NOW });
        const type = parsed.action?.type;
        expect([row.sentence, type === undefined || type === 'question.ask' || type === 'finding.describe']).toEqual([row.sentence, true]);
        // Two requests are refused as two, but for a name this sky does not hold, which the app refuses first, as it
        // does any name it lacks (a refusal only the photo's sky can give)
        if (row.group === 'two requests') {
          const kind = parsed.refusal?.kind;
          expect([row.sentence, kind === 'two_requests' || (kind !== undefined && !REFUSAL_KINDS[kind].backend)]).toEqual([row.sentence, true]);
        }
      }
    }
  });
});

describe('@albedian/observation voice parser: a typographic apostrophe is the plain one', () => {
  it('reads every parity sentence with an apostrophe typed any way the table folds as the plain sentence', () => {
    // Folded on the way in, in one place (the table's folds), as the backend folds it (answers-3b v4)
    expect(fold(VOICE_GRAMMAR, '“don’t”')).toBe('"don\'t"');
    const said = FIXTURE.sentences.filter((text) => text.includes("'"));
    expect(said.length).toBeGreaterThan(40);
    for (const typed of ['’', '‘', 'ʼ']) {
      for (const view of FIXTURE.views) {
        const session = sessionOf(view.sample, view.selected);
        for (const text of said) {
          const plain = parseUtteranceToAction(text, buildActionContext(session), session, { now: NOW });
          const got = parseUtteranceToAction(text.replaceAll("'", typed), buildActionContext(session), session, { now: NOW });
          expect([typed, text, got.action?.type, got.refusal, got.error]).toEqual([typed, text, plain.action?.type, plain.refusal, plain.error]);
        }
      }
    }
    const s = sessionOf('plate_calibrated', 'finding-star-rigel');
    const asked = parseUtteranceToAction('“what does ephemeris mean”', buildActionContext(s), s, { now: NOW });
    expect(asked.action).toMatchObject({ type: 'question.ask', question: '"what does ephemeris mean"' });
  });
});
