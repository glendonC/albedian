/**
 * The moment of a visit the soundtrack follows (soundtrack.ts), read from what the app already
 * holds: the landing page, the session's navigation machine and flight phase, the selected finding
 * and whether a photo has been solved. First match wins:
 *
 * - arrive: the landing page is up;
 * - travel: the flight to a star and the 3D neighbourhood it lands in (navigation transitioning or
 *   space, or a flight phase);
 * - back_to_moment: the view has come back to the source from a departed state (Back to Moment, Esc
 *   or voice), until the next navigation or selection change;
 * - star_card: a finding is selected, or being inspected;
 * - reveal: a solved photo is on screen, nothing selected;
 * - look_around: everything else after Launch.
 *
 * The renderer's own Go There and regime flights do not change the session's navigation, so they do
 * not count as travel yet; the engine's `setMusicMoment` is ready for them.
 *
 * The viewport's hook puts the moment in `appSoundtrackMoment`, which the app's engine follows, so
 * the viewport never calls the engine for it.
 */

import { useEffect, useRef } from 'react';
import type { NavigationMachineState, ObservationSession, SpatialViewState } from '@albedian/contracts';
import type { SoundtrackMoment } from './soundtrack';

export interface MomentInputs {
  readonly landing: boolean;
  readonly navigation: NavigationMachineState;
  readonly transitionPhase: SpatialViewState['transitionPhase'] | null;
  readonly selectedFindingId: string | null;
  readonly solvedPhoto: boolean;
}

export interface MomentTrack {
  readonly moment: SoundtrackMoment;
  readonly inputs: MomentInputs;
}

/** The states that leave the source recording, from which coming back is Back to Moment. */
const DEPARTED: ReadonlySet<NavigationMachineState> = new Set(['sky_registered', 'transitioning', 'space', 'inspecting', 'returning']);

const FLIGHT_PHASES: ReadonlySet<string> = new Set(['phase_a_photo_to_sky', 'phase_b_sky_to_space', 'at_space']);

/** The moment for these inputs, given the previous track (null on the first call). */
export function nextMoment(previous: MomentTrack | null, inputs: MomentInputs): MomentTrack {
  return { moment: momentOf(previous, inputs), inputs };
}

function momentOf(previous: MomentTrack | null, inputs: MomentInputs): SoundtrackMoment {
  if (inputs.landing) return 'arrive';
  const phase = inputs.transitionPhase ?? '';
  if (inputs.navigation === 'transitioning' || inputs.navigation === 'space' || FLIGHT_PHASES.has(phase)) return 'travel';
  if (inputs.navigation === 'returning' || phase === 'returning_to_source') return 'back_to_moment';
  if (inputs.navigation === 'source' && previous && !previous.inputs.landing) {
    const before = previous.inputs;
    // Just came back, or came back and nothing has changed since
    if (DEPARTED.has(before.navigation)) return 'back_to_moment';
    if (previous.moment === 'back_to_moment' && before.navigation === 'source' && before.selectedFindingId === inputs.selectedFindingId) {
      return 'back_to_moment';
    }
  }
  if (inputs.selectedFindingId !== null || inputs.navigation === 'inspecting') return 'star_card';
  if (inputs.solvedPhoto) return 'reveal';
  return 'look_around';
}

/** What the moment reads from the viewport's state. */
export function momentInputs(landing: boolean, session: ObservationSession): MomentInputs {
  return {
    landing,
    navigation: session.machines?.navigation ?? 'source',
    transitionPhase: session.spatialView?.transitionPhase ?? null,
    selectedFindingId: session.selectedFindingId ?? null,
    solvedPhoto: (session.observationSolution?.findings?.length ?? 0) > 0,
  };
}

const sameInputs = (a: MomentInputs, b: MomentInputs) =>
  a.landing === b.landing &&
  a.navigation === b.navigation &&
  a.transitionPhase === b.transitionPhase &&
  a.selectedFindingId === b.selectedFindingId &&
  a.solvedPhoto === b.solvedPhoto;

/** The page's soundtrack moment, which the app's engine follows. */
export class MomentStore {
  private moment: SoundtrackMoment;
  private readonly listeners = new Set<() => void>();

  constructor(initial: SoundtrackMoment) {
    this.moment = initial;
  }

  readonly get = (): SoundtrackMoment => this.moment;

  readonly set = (moment: SoundtrackMoment): void => {
    if (moment === this.moment) return;
    this.moment = moment;
    for (const listener of [...this.listeners]) listener();
  };

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

/** This page's moment: the viewport sets it, the engine plays to it. */
export const appSoundtrackMoment = new MomentStore('arrive');

/**
 * The soundtrack moment of the viewport, put in `store` for the engine. It moves on only when an
 * input changes, so rendering twice with the same state gives the same moment.
 */
export function useSoundtrackMoment(landing: boolean, session: ObservationSession, store: MomentStore = appSoundtrackMoment): SoundtrackMoment {
  const track = useRef<MomentTrack | null>(null);
  const inputs = momentInputs(landing, session);
  if (!track.current || !sameInputs(track.current.inputs, inputs)) track.current = nextMoment(track.current, inputs);
  const moment = track.current.moment;
  useEffect(() => store.set(moment), [store, moment]);
  return moment;
}
