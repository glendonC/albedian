import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { lowerCase, ObservationSolutionSchema, REFUSAL_KINDS, type ObservationSession, type VoiceRefusal } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * The app parser reads a request as the backend does on every runtime: words from the tracked letters table, lower
 * case from the tracked lower-case table, never the runtime's own Unicode tables. The sentences and the backend's
 * readings are tests/fixtures/voice_unicode_readings.json, written by tests/integration/test_voice_unicode_readings.py:
 * each code point whose properties change between the Unicode versions the runtimes carry, in three sentences, and
 * the lower case of each.
 */
const ROOT = path.resolve(__dirname, '../../../..');
interface Reading {
  status: 'proposed' | 'needs_input' | 'rejected';
  action?: string;
  target?: string;
  choices?: string[];
  reason?: string;
  refusal?: VoiceRefusal;
}
const FIXTURE = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/voice_unicode_readings.json'), 'utf8')) as {
  codePoints: number[];
  lower: { text: string; lower: string }[];
  readings: { text: string; reading: Reading }[];
};
const SOLUTIONS = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, unknown>;
const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
// The backend's context: a calibrated sky (the plate_calibrated solution) with Rigel selected
const base = createSyntheticCalibratedSession();
const session: ObservationSession = {
  ...base,
  observationSolution: ObservationSolutionSchema.parse(SOLUTIONS.plate_calibrated),
  selectedFindingId: 'finding-star-rigel',
  focusedFindingId: null,
};

function reading(text: string): Reading {
  const parsed = parseUtteranceToAction(text, buildActionContext(session), session, { now: NOW });
  if (parsed.action) {
    const action = parsed.action as unknown as Record<string, unknown>;
    return { status: 'proposed', action: parsed.action.type, ...(typeof action.findingId === 'string' ? { target: action.findingId } : {}) };
  }
  if (parsed.disambiguation) return { status: 'needs_input', choices: parsed.disambiguation.choices.map((c) => c.id) };
  return { status: 'rejected', reason: parsed.reason, ...(parsed.refusal ? { refusal: parsed.refusal } : {}) };
}

describe('@albedian/observation voice parser: one reading on every runtime', () => {
  it('holds the reader example and every code point the versions read otherwise', () => {
    expect(FIXTURE.codePoints).toContain(0x1c89);
    expect(FIXTURE.codePoints.length).toBe(187);
    // The three sentences of each code point, and the six requests of AUDIT G's live-path probe
    expect(FIXTURE.readings.length).toBe(6 + 187 * 3);
    expect(FIXTURE.readings.slice(0, 4).map((r) => r.text)).toEqual(['select vega\u1c8a', 'fly to vega\u1c8a', 'select vega\ua7cf', 'select \u1c89']);
  });

  it('lowers every sample as the backend does', () => {
    expect(FIXTURE.lower.map(({ text }) => lowerCase(text))).toEqual(FIXTURE.lower.map(({ lower }) => lower));
  });

  it('reads every sentence as the backend does, bar a refusal only the photo can give', () => {
    // The app holds the photo's sky, which lacks Vega: there it says so where the backend, which holds no photo,
    // acts on Vega. The words were read alike (said "vega"); everything else is equal entry for entry
    const photoOnly: string[] = [];
    const app = FIXTURE.readings.map(({ text, reading: backend }) => {
      const own = reading(text);
      const kind = own.refusal?.kind;
      if (kind !== undefined && !REFUSAL_KINDS[kind].backend && backend.status === 'proposed') {
        photoOnly.push(text);
        expect(own.refusal).toEqual({ kind: 'not_in_this_sky', said: 'vega', name: 'Vega' });
        expect(backend.target).toBe('finding-star-vega');
        return { text, reading: backend };
      }
      return { text, reading: own };
    });
    expect(app).toEqual(FIXTURE.readings);
    expect(photoOnly).toEqual(['select vega\u1c8a', 'fly to vega\u1c8a', 'select vega\ua7cf', 'select vega']);
  });

  it('calls no case mapping and no Unicode property of the runtime', () => {
    for (const file of ['voiceParser.ts', 'voiceGrammar.ts']) {
      const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
      expect(source.match(/toUpperCase|toLowerCase|toLocale[A-Za-z]*Case|localeCompare|Intl\.|\.normalize\(|\\[pP]\{|\/[a-z]*(?:iu|ui)[a-z]*[.;,)]/g), file).toBeNull();
    }
  });
});
