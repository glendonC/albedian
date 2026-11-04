import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { AlbedianActionSchema, LandingSceneSchema, ScaleRegimeSchema } from '@albedian/contracts';
import type { AlbedianAction } from '@albedian/contracts';
import { createEmptyObservationSession } from '../emptySession.js';
import { dispatchAlbedianAction } from '../reducer.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 200;

// A made-up creation time that points at no real moment
const session = createEmptyObservationSession('2001-01-01T00:00:00Z');
const base = { requestId: 'req-new-action', expectedViewRevision: session.viewRevision };

const regimeAction = fc
  .constantFrom(...ScaleRegimeSchema.options)
  .map((regime) => ({ ...base, type: 'space.regime' as const, regime }));
const sceneAction = fc
  .constantFrom(...LandingSceneSchema.options)
  .map((scene) => ({ ...base, type: 'scene.show' as const, scene }));
const audioAction = fc
  .record({ muted: fc.boolean(), gain: fc.double({ min: 0, max: 1, noNaN: true }) }, { requiredKeys: [] })
  .filter((fields) => fields.muted !== undefined || fields.gain !== undefined)
  .map((fields) => ({ ...base, type: 'audio.set' as const, ...fields }));
const notConnected = fc.oneof(regimeAction, sceneAction, audioAction);
const questionAction = fc
  .record({
    question: fc.string({ minLength: 1, maxLength: 500 }).filter((text) => /\S/.test(text)),
    findingId: fc.option(fc.string({ minLength: 1 }), { nil: undefined }),
  })
  .map(({ question, findingId }) => ({
    ...base,
    type: 'question.ask' as const,
    question,
    ...(findingId === undefined ? {} : { findingId }),
  }));

function dispatch(action: AlbedianAction) {
  expect(AlbedianActionSchema.safeParse(action).success).toBe(true);
  return dispatchAlbedianAction(session, action);
}

describe('@albedian/observation: voice actions the app cannot carry out yet', () => {
  it('PBT: space.regime, scene.show and audio.set are rejected as not_connected and change nothing', () => {
    fc.assert(
      fc.property(notConnected, (action) => {
        const result = dispatch(action);
        expect(result.outcome).toEqual({
          status: 'rejected',
          actionId: action.requestId,
          reason: 'not_connected',
          explanation: 'Voice cannot do this in the app yet.',
          nextActions: [],
        });
        expect(result.session).toBe(session);
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          [{ ...base, type: 'audio.set', gain: 0 }],
          [{ ...base, type: 'audio.set', gain: 1 }],
          [{ ...base, type: 'audio.set', muted: true }],
          [{ ...base, type: 'space.regime', regime: 'deep_sky' }],
          [{ ...base, type: 'scene.show', scene: 'black_hole' }],
        ],
      }
    );
  });

  it('leaves the session gain alone, since nothing plays at that gain yet', () => {
    const result = dispatch({ ...base, type: 'audio.set', gain: 0.25 });
    expect(result.session.audioGain).toBe(session.audioGain);
    expect(result.outcome.status).toBe('rejected');
  });

  it('PBT: question.ask is rejected with no_grounded_answer and changes nothing', () => {
    fc.assert(
      fc.property(questionAction, (action) => {
        const result = dispatch(action);
        expect(result.outcome).toEqual({
          status: 'rejected',
          actionId: action.requestId,
          reason: 'no_grounded_answer',
          explanation: 'No tool can answer questions from your data yet.',
          nextActions: [],
        });
        expect(result.session).toBe(session);
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [[{ ...base, type: 'question.ask', question: 'what is that bright one?', findingId: 'finding-obs-1' }]],
      }
    );
  });

  it('PBT: a stale revision is still reported before anything else', () => {
    fc.assert(
      fc.property(fc.oneof(notConnected, questionAction), fc.integer({ min: 1, max: 1000 }), (action, ahead) => {
        const result = dispatch({ ...action, expectedViewRevision: session.viewRevision + ahead });
        expect(result.outcome.status).toBe('rejected');
        expect(result.outcome.status === 'rejected' && result.outcome.reason).toBe('stale_revision');
        expect(result.session).toBe(session);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });
});
