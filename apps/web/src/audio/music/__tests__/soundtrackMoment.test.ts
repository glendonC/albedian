// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { NavigationMachineState, ObservationSession } from '@albedian/contracts';
import { SOUNDTRACK, SOUNDTRACK_MOMENTS } from '../soundtrack';
import { MomentStore, momentInputs, nextMoment, type MomentInputs, type MomentTrack } from '../soundtrackMoment';

const AT_SOURCE: MomentInputs = { landing: false, navigation: 'source', transitionPhase: 'at_source', selectedFindingId: null, solvedPhoto: false };

/** The moments for a run of inputs, each step from the one before. */
function moments(steps: readonly Partial<MomentInputs>[]): string[] {
  let track: MomentTrack | null = null;
  return steps.map((step) => {
    track = nextMoment(track, { ...AT_SOURCE, ...step });
    return track.moment;
  });
}

describe('the soundtrack moment of a visit', () => {
  it('arrives on the landing page, whatever else the session holds', () => {
    expect(moments([{ landing: true }])).toEqual(['arrive']);
    expect(moments([{ landing: true, navigation: 'space', transitionPhase: 'at_space', selectedFindingId: 'f1', solvedPhoto: true }])).toEqual(['arrive']);
  });

  it('looks around after Launch, reveals a solved photo, and opens a star card on a selection', () => {
    expect(moments([{ landing: true }, {}])).toEqual(['arrive', 'look_around']);
    expect(moments([{ solvedPhoto: true }])).toEqual(['reveal']);
    expect(moments([{ solvedPhoto: true }, { solvedPhoto: true, selectedFindingId: 'f1' }])).toEqual(['reveal', 'star_card']);
    expect(moments([{ navigation: 'inspecting' }])).toEqual(['star_card']);
  });

  it('travels for the whole flight to a star and in the space it lands in', () => {
    // useCameraFlightAnimation: phase A keeps navigation sky_registered, phase B and arrival are space
    expect(
      moments([
        { solvedPhoto: true, selectedFindingId: 'f1' },
        { solvedPhoto: true, selectedFindingId: 'f1', navigation: 'sky_registered', transitionPhase: 'phase_a_photo_to_sky' },
        { solvedPhoto: true, selectedFindingId: 'f1', navigation: 'space', transitionPhase: 'phase_b_sky_to_space' },
        { solvedPhoto: true, selectedFindingId: 'f1', navigation: 'space', transitionPhase: 'at_space' },
      ])
    ).toEqual(['star_card', 'travel', 'travel', 'travel']);
    expect(moments([{ navigation: 'transitioning' }])).toEqual(['travel']);
  });

  it('comes back to the moment from a departed view, and stays there until the view or selection changes', () => {
    // source.return restores navigation source, at_source and the checkpoint's selection (reducer.ts)
    const space = { solvedPhoto: true, selectedFindingId: 'f1', navigation: 'space', transitionPhase: 'at_space' } as const;
    const back = { solvedPhoto: true, selectedFindingId: 'f1' } as const;
    expect(moments([space, back, back, back])).toEqual(['travel', 'back_to_moment', 'back_to_moment', 'back_to_moment']);
    expect(moments([space, back, { ...back, selectedFindingId: 'f2' }])).toEqual(['travel', 'back_to_moment', 'star_card']);
    expect(moments([space, back, { ...back, selectedFindingId: null }])).toEqual(['travel', 'back_to_moment', 'reveal']);
    expect(moments([space, back, { ...back, navigation: 'comparison' }])).toEqual(['travel', 'back_to_moment', 'star_card']);
    expect(moments([{ navigation: 'returning', transitionPhase: 'returning_to_source' }])).toEqual(['back_to_moment']);
    expect(moments([{ navigation: 'sky_registered', transitionPhase: 'at_registered_sky' }, {}])).toEqual(['look_around', 'back_to_moment']);
  });

  it('does not count a compare wipe or leaving the landing page as coming back', () => {
    expect(moments([{ navigation: 'comparison', solvedPhoto: true }, { solvedPhoto: true }])).toEqual(['reveal', 'reveal']);
    expect(moments([{ landing: true }, {}])).toEqual(['arrive', 'look_around']);
  });

  it('reads the moment inputs from the session, with defaults where the viewport has none', () => {
    const empty = { sourceAsset: { observerLocation: null } } as unknown as ObservationSession;
    expect(momentInputs(true, empty)).toEqual({ landing: true, navigation: 'source', transitionPhase: null, selectedFindingId: null, solvedPhoto: false });
    const solved = {
      machines: { navigation: 'space' },
      spatialView: { transitionPhase: 'at_space' },
      selectedFindingId: 'f9',
      observationSolution: { findings: [{ id: 'f9' }] },
    } as unknown as ObservationSession;
    expect(momentInputs(false, solved)).toEqual({ landing: false, navigation: 'space', transitionPhase: 'at_space', selectedFindingId: 'f9', solvedPhoto: true });
    const unsolved = { observationSolution: { findings: [] } } as unknown as ObservationSession;
    expect(momentInputs(false, unsolved).solvedPhoto).toBe(false);
  });

  it('keeps the page moment for the engine, and tells its listeners only of a change', () => {
    const store = new MomentStore('arrive');
    const heard: string[] = [];
    const unsubscribe = store.subscribe(() => heard.push(store.get()));
    store.set('arrive');
    store.set('travel');
    store.set('travel');
    store.set('back_to_moment');
    expect(heard).toEqual(['travel', 'back_to_moment']);
    unsubscribe();
    store.set('reveal');
    expect(store.get()).toBe('reveal');
    expect(heard).toHaveLength(2);
  });

  it('always names a moment the soundtrack has a piece for, and arrives whenever the landing is up', () => {
    const navigation = fc.constantFrom<NavigationMachineState>('source', 'comparison', 'sky_registered', 'transitioning', 'space', 'inspecting', 'returning');
    const phase = fc.constantFrom('at_source', 'phase_a_photo_to_sky', 'at_registered_sky', 'phase_b_sky_to_space', 'at_space', 'returning_to_source', null);
    const inputs = fc.record({
      landing: fc.boolean(),
      navigation,
      transitionPhase: phase,
      selectedFindingId: fc.constantFrom(null, 'f1', 'f2'),
      solvedPhoto: fc.boolean(),
    }) as fc.Arbitrary<MomentInputs>;
    fc.assert(
      fc.property(fc.array(inputs, { minLength: 1, maxLength: 30 }), (steps) => {
        let track: MomentTrack | null = null;
        for (const step of steps) {
          const next = nextMoment(track, step);
          expect(SOUNDTRACK_MOMENTS).toContain(next.moment);
          expect(SOUNDTRACK.some((piece) => piece.moments.includes(next.moment))).toBe(true);
          if (step.landing) expect(next.moment).toBe('arrive');
          // The same inputs again keep the moment
          expect(nextMoment(next, step).moment).toBe(next.moment);
          track = next;
        }
      }),
      { seed: SEED, examples: [[[{ ...AT_SOURCE, navigation: 'space', transitionPhase: 'at_space' }, AT_SOURCE, AT_SOURCE]]] }
    );
  });
});
