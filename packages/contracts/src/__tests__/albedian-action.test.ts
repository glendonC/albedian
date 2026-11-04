import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  ActionOutcomeSchema,
  AlbedianActionSchema,
  LandingSceneSchema,
  ScaleRegimeSchema,
} from '../index.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;

const BASE = { requestId: 'req-test', expectedViewRevision: 4 };
const REGIMES = ScaleRegimeSchema.options;
const SCENES = LandingSceneSchema.options;

// Zod drops unknown keys, so an accepted action must also come back field for field
function roundTrips(action: unknown): boolean {
  const parsed = AlbedianActionSchema.safeParse(action);
  expect(parsed.error?.issues ?? []).toEqual([]);
  expect(parsed.data).toEqual(action);
  return true;
}

function rejects(action: unknown): boolean {
  return !AlbedianActionSchema.safeParse(action).success;
}

describe('@albedian/contracts: voice actions for regimes, scenes, audio and questions', () => {
  it('names four regimes and leaves out planetary_surface', () => {
    expect([...REGIMES]).toEqual(['earth_capture', 'cislunar', 'solar_neighborhood', 'deep_sky']);
    expect([...SCENES]).toEqual(['cislunar', 'black_hole', 'supergiant', 'saturn_horizon']);
  });

  it('PBT: space.regime parses for each regime and for no other value', () => {
    fc.assert(
      fc.property(fc.constantFrom(...REGIMES), (regime) => roundTrips({ ...BASE, type: 'space.regime', regime })),
      { seed: SEED, numRuns: NUM_RUNS }
    );
    fc.assert(
      fc.property(
        fc.string().filter((value) => !(REGIMES as readonly string[]).includes(value)),
        (regime) => rejects({ ...BASE, type: 'space.regime', regime })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [['planetary_surface'], [''], ['Cislunar']] }
    );
  });

  it('PBT: scene.show parses for each carousel scene and for no other value', () => {
    fc.assert(
      fc.property(fc.constantFrom(...SCENES), (scene) => roundTrips({ ...BASE, type: 'scene.show', scene })),
      { seed: SEED, numRuns: NUM_RUNS }
    );
    fc.assert(
      fc.property(
        fc.string().filter((value) => !(SCENES as readonly string[]).includes(value)),
        (scene) => rejects({ ...BASE, type: 'scene.show', scene })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [['deep_sky'], ['black hole'], ['']] }
    );
  });

  it('PBT: audio.set parses with muted, gain in [0, 1], or both, and never with neither', () => {
    const gain = fc.double({ min: 0, max: 1, noNaN: true });
    const fields = fc
      .record({ muted: fc.boolean(), gain }, { requiredKeys: [] })
      .filter((value) => value.muted !== undefined || value.gain !== undefined);
    fc.assert(
      fc.property(fields, (value) => roundTrips({ ...BASE, type: 'audio.set', ...value })),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [[{ gain: 0 }], [{ gain: 1 }], [{ muted: true }], [{ muted: false, gain: 0.5 }]],
      }
    );
    expect(rejects({ ...BASE, type: 'audio.set' })).toBe(true);
    fc.assert(
      fc.property(
        fc.oneof(fc.double({ max: -Number.MIN_VALUE }), fc.double({ min: 1 + Number.EPSILON }), fc.constant(Number.NaN)),
        (outside) => rejects({ ...BASE, type: 'audio.set', gain: outside })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[-0.001], [1.001], [Number.POSITIVE_INFINITY]] }
    );
  });

  it('PBT: question.ask takes 1 to 500 characters with at least one that is not a space', () => {
    const question = fc
      .string({ minLength: 1, maxLength: 500 })
      .filter((value) => /\S/.test(value));
    fc.assert(
      fc.property(question, fc.option(fc.string({ minLength: 1 }), { nil: undefined }), (text, findingId) =>
        roundTrips({ ...BASE, type: 'question.ask', question: text, ...(findingId ? { findingId } : {}) })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [['a'.repeat(500), undefined], ['what is that bright one?', 'finding-obs-1']] }
    );
    fc.assert(
      fc.property(
        fc.oneof(fc.string({ minLength: 501, maxLength: 900 }), fc.stringOf(fc.constantFrom(' ', '\t', '\n'), { maxLength: 40 })),
        (text) => rejects({ ...BASE, type: 'question.ask', question: text })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [['a'.repeat(501)], [''], ['   ']] }
    );
  });

  it('PBT: every new action still needs a request id and a non-negative integer revision', () => {
    const action = fc.oneof(
      fc.constantFrom(...REGIMES).map((regime) => ({ type: 'space.regime', regime })),
      fc.constantFrom(...SCENES).map((scene) => ({ type: 'scene.show', scene })),
      fc.constant({ type: 'audio.set', muted: true }),
      fc.constant({ type: 'question.ask', question: 'how far is it?' })
    );
    const badBase = fc.oneof(
      fc.constant({ expectedViewRevision: 1 }),
      fc.constant({ requestId: '', expectedViewRevision: 1 }),
      fc.integer({ max: -1 }).map((rev) => ({ requestId: 'req', expectedViewRevision: rev })),
      fc.double({ min: 0.1, max: 0.9, noNaN: true }).map((rev) => ({ requestId: 'req', expectedViewRevision: rev }))
    );
    fc.assert(
      fc.property(action, badBase, (body, base) => rejects({ ...base, ...body })),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });
});

describe('@albedian/contracts: who interpreted an action', () => {
  const interpretation = fc.oneof(
    fc.constant({ parser: 'rules' as const }),
    fc.record({
      parser: fc.constant('model' as const),
      requestedModel: fc.string({ minLength: 1 }),
      servedBy: fc.string({ minLength: 1 }),
    })
  );
  const completed = { status: 'completed', actionId: 'req', newStateVersion: 2, visualStatus: 'done' };
  const rejected = {
    status: 'rejected',
    actionId: 'req',
    reason: 'no_grounded_answer',
    explanation: 'no tool can answer yet',
    nextActions: [],
  };
  const outcomes = [
    completed,
    { status: 'needs_input', actionId: 'req', question: 'which one?', choices: [] },
    rejected,
    { status: 'canceled', actionId: 'req', settledStateVersion: 2, visualStatus: 'stopped' },
  ];

  it('PBT: every outcome status carries an optional interpretation', () => {
    fc.assert(
      fc.property(fc.constantFrom(...outcomes), interpretation, (outcome, value) => {
        const parsed = ActionOutcomeSchema.safeParse({ ...outcome, interpretation: value });
        // Zod drops unknown keys, so check the interpretation survived the parse
        expect(parsed.data).toEqual({ ...outcome, interpretation: value });
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [[rejected, { parser: 'model' as const, requestedModel: 'claude-opus-5-5', servedBy: 'claude-opus-4-8' }]],
      }
    );
    for (const outcome of outcomes) expect(ActionOutcomeSchema.safeParse(outcome).success).toBe(true);
  });

  it('accepts the two new rejection reasons and still refuses an unknown one', () => {
    for (const reason of ['no_grounded_answer', 'not_connected']) {
      expect(ActionOutcomeSchema.safeParse({ ...rejected, reason }).success).toBe(true);
    }
    expect(ActionOutcomeSchema.safeParse({ ...rejected, reason: 'not_wired' }).success).toBe(false);
  });

  it('rejects a model interpretation that does not name both models', () => {
    for (const value of [
      { parser: 'model', requestedModel: 'claude-opus-5-5' },
      { parser: 'model', servedBy: 'claude-opus-5-5' },
      { parser: 'model', requestedModel: '', servedBy: 'claude-opus-5-5' },
      { parser: 'guess' },
    ]) {
      expect(ActionOutcomeSchema.safeParse({ ...completed, interpretation: value }).success).toBe(false);
    }
  });
});
