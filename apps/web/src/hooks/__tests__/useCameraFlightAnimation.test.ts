// @vitest-environment jsdom
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import type { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { dispatchAlbedianAction, executeVoiceOrTextCommand, returnCheckpointOf } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlightAndRestoreHud } from '../../components/FlightAndRestoreHud.js';
import { FLIGHT_STANDOFF_PC, distancePc } from '@albedian/observation';
import { MOTION_TOKENS } from '@albedian/design-system';
import { FLIGHT_HALF_MS, flightEase, useCameraFlightAnimation } from '../useCameraFlightAnimation.js';

/**
 * Go There ends one light year short of the star, on the line from where the camera started
 * (render-14). The old flight stopped at 0.45 of the star's distance from the Sun and said it had
 * arrived: Tabit, 8.02 pc away, read 11.78 ly from Earth with 14.39 ly still to go.
 */

type Update = ObservationSession | ((prev: ObservationSession) => ObservationSession);

/**
 * The clock the flight starts from, held still: the hook reads performance.now() once when a flight starts and each
 * frame is called at this start plus its offset, so a frame is exactly that far into the flight however busy the
 * machine is. Read again after the hook, the clock had moved on under load, and a progress compared to three decimals
 * came out 0.0006 off.
 */
const FLIGHT_START_MS = 1_000;
function holdClock(): number {
  vi.spyOn(performance, 'now').mockReturnValue(FLIGHT_START_MS);
  return FLIGHT_START_MS;
}

/**
 * Flies to a finding and returns the session after each update the flight made, in order. The
 * hook flies from the session it rendered with; its updates apply to `appliedTo`, the state the
 * app holds when the flight starts (by default the same session).
 */
function flyThrough(
  session: ObservationSession,
  findingId: string,
  frames: FrameRequestCallback[],
  appliedTo: ObservationSession = session,
  offsets: number[] = [300, 1200, 60_000]
): ObservationSession[] {
  const updates: Update[] = [];
  const setSession = (u: Update) => updates.push(u);
  let fly: ((id: string) => void) | null = null;
  const Probe = () => {
    fly = useCameraFlightAnimation(session, setSession as React.Dispatch<React.SetStateAction<ObservationSession>>)
      .animatePhaseAToPhaseBSpaceFlight;
    return null;
  };
  renderToString(createElement(Probe));
  const start = holdClock();
  fly!(findingId);
  // Run the queued animation frames: one in each phase, then past the flight's end
  const times = offsets.map((ms) => start + ms);
  while (frames.length > 0) frames.shift()!(times.shift() ?? start + 60_000);
  const states: ObservationSession[] = [];
  updates.reduce<ObservationSession>((s, u) => {
    const next = typeof u === 'function' ? u(s) : u;
    states.push(next);
    return next;
  }, appliedTo);
  return states;
}

/** What the flight panel shows for a session: the readouts by test id. */
function panel(session: ObservationSession): Record<string, string> {
  const host = document.createElement('div');
  host.innerHTML = renderToString(createElement(FlightAndRestoreHud, { session, setSession: () => {} }));
  const text = (id: string) => host.querySelector(`[data-testid="${id}"]`)?.textContent ?? '';
  return {
    earth: text('flight-distance-readout'),
    target: text('flight-target-distance-readout'),
    phase: text('flight-transition-phase'),
  };
}

/** The session when the flight is over. */
const flyTo = (session: ObservationSession, findingId: string, frames: FrameRequestCallback[]) =>
  flyThrough(session, findingId, frames).at(-1)!;

function queueFrames(): FrameRequestCallback[] {
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => (frames.push(cb), frames.length));
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  return frames;
}

afterEach(() => vi.restoreAllMocks());

const base = createSyntheticCalibratedSession();
const target = base.observationSolution!.findings.find((f) => resolveFindingParsecPosition(f) !== null)!;
const targetPc = resolveFindingParsecPosition(target)!;

describe('Go There flies to one light year short of the star', () => {
  for (const reducedMotion of [true, false]) {
    it(`ends one light year short, from Earth, ${reducedMotion ? 'at once with reduced motion' : 'after the animation'}`, () => {
      const end = flyTo({ ...base, reducedMotion }, target.id, queueFrames()).spatialView.observerOriginParsec;
      expect(Math.abs(distancePc(end, targetPc) - FLIGHT_STANDOFF_PC)).toBeLessThan(1e-9);
      expect(Math.abs(distancePc([0, 0, 0], end) + FLIGHT_STANDOFF_PC - distancePc([0, 0, 0], targetPc))).toBeLessThan(1e-9);
    });
  }

  it('ends on the line from wherever the camera already was', () => {
    const start: [number, number, number] = [3, -2, 1.5];
    const from = { ...base, reducedMotion: true, spatialView: { ...base.spatialView, observerOriginParsec: start } };
    const end = flyTo(from, target.id, queueFrames()).spatialView.observerOriginParsec;
    expect(Math.abs(distancePc(end, targetPc) - FLIGHT_STANDOFF_PC)).toBeLessThan(1e-9);
    expect(Math.abs(distancePc(start, end) + distancePc(end, targetPc) - distancePc(start, targetPc))).toBeLessThan(1e-9);
  });

  it('travels as a session navigation change, which the soundtrack listens for', () => {
    expect(base.machines.navigation).not.toBe('space');
    for (const reducedMotion of [true, false]) {
      const after = flyTo({ ...base, reducedMotion }, target.id, queueFrames());
      expect(after.machines.navigation).toBe('space');
      expect(after.spatialView.mode).toBe('catalog_3d');
    }
  });

  it('says which phase it is in and where it arrives, never a made-up percentage', () => {
    for (const reducedMotion of [true, false]) {
      const states = flyThrough({ ...base, reducedMotion }, target.id, queueFrames());
      if (!reducedMotion) expect(new Set(states.map((s) => s.spatialView.transitionPhase)).size).toBeGreaterThanOrEqual(3);
      for (const state of states) {
        expect(state.lastStatusBanner).not.toMatch(/%/);
        expect(state.lastStatusBanner).not.toMatch(/0\.5 px/);
      }
      expect(states.at(-1)!.lastStatusBanner).toContain(`one light year from ${target.title}`);
    }
  });

  it('never shows the camera at the star on the way, from the command to the arrival', () => {
    // The target card's "Take me to <star>" runs this command; the reducer's space.enter puts the
    // camera at the flight's end, one light year short of the star, then the app starts the flight
    // from the session it rendered with
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
    const starPc = resolveFindingParsecPosition(star)!;
    for (const reducedMotion of [false, true]) {
      const before = { ...base, reducedMotion };
      const command = executeVoiceOrTextCommand(before, `Take me to ${star.title}`, 'pointer');
      expect(command.outcome.status).toBe('completed');
      const states = flyThrough(before, star.id, queueFrames(), command.session);
      expect(states.length).toBeGreaterThanOrEqual(reducedMotion ? 1 : 4);
      states.forEach((state, n) => {
        const last = n === states.length - 1;
        const shown = panel(state);
        const toStar = distancePc(state.spatialView.observerOriginParsec, starPc);
        // Never at the star: from the first update the camera is where it started, or on its way
        expect(toStar, `${reducedMotion} update ${n}`).toBeGreaterThan(FLIGHT_STANDOFF_PC * 0.999);
        expect(shown.target, `${reducedMotion} update ${n}`).not.toMatch(/^0\.0+ ly/);
        if (!last) expect(shown.phase, `update ${n}`).not.toMatch(/Arrived/);
      });
      expect(panel(states.at(-1)!).target).toMatch(new RegExp(`^1\\.00 ly from ${star.title}`));
    }
  });

  it('says how far it is when the camera is already within a light year, rather than one light year', () => {
    const halfLy = FLIGHT_STANDOFF_PC / 2;
    const near: [number, number, number] = [targetPc[0] + halfLy, targetPc[1], targetPc[2]];
    const from = { ...base, reducedMotion: true, spatialView: { ...base.spatialView, observerOriginParsec: near } };
    const after = flyTo(from, target.id, queueFrames());
    expect(after.spatialView.observerOriginParsec).toEqual(near);
    expect(after.lastStatusBanner).not.toMatch(/one light year/);
    expect(after.lastStatusBanner).toContain(`0.500 ly from ${target.title}`);
  });
});

describe('the flight keeps the motion record: two halves of 750 ms on NASA\'s Eyes\' ease-out quart', () => {
  it('takes each half from the motion tokens', () => {
    expect(FLIGHT_HALF_MS).toBe(MOTION_TOKENS.cameraHalfMs);
    expect(FLIGHT_HALF_MS).toBe(750);
  });

  it('eases each half as 1 - (1 - t)^4, from rest to rest', () => {
    expect(flightEase(0)).toBe(0);
    expect(flightEase(1)).toBe(1);
    expect(flightEase(0.5)).toBeCloseTo(0.9375, 10);
    expect(flightEase(0.25)).toBeCloseTo(1 - 0.75 ** 4, 10);
    expect(flightEase(-1)).toBe(0);
    expect(flightEase(2)).toBe(1);
    for (let t = 0; t < 1; t += 0.05) expect(flightEase(t + 0.05)).toBeGreaterThanOrEqual(flightEase(t));
  });

  it('is in the first half until 750 ms and in the second until 1500 ms, then arrives', () => {
    const phases = flyThrough(base, target.id, queueFrames(), base, [700, 800, 1499, 1500]).map((s) => s.spatialView.transitionPhase);
    // the first frame runs at once, then the four frames at 700, 800, 1499 and 1500 ms
    expect(phases).toEqual(['phase_a_photo_to_sky', 'phase_a_photo_to_sky', 'phase_b_sky_to_space', 'phase_b_sky_to_space', 'at_space']);
  });

  it('reports each half\'s progress on that curve', () => {
    const states = flyThrough(base, target.id, queueFrames(), base, [375, 1125, 60_000]);
    expect(states[1]!.spatialView.transitionProgress).toBeCloseTo(flightEase(0.5), 3);
    expect(states[2]!.spatialView.transitionProgress).toBeCloseTo(flightEase(0.5), 3);
  });
});

// The motion record (MOTION-READY-web.md, "Go there (A = 0)", Back to Moment): the second half reversed (750 ms, the
// camera from where it is back to where the flight began), then the first half reversed (750 ms, the registered sky
// back to the photo), each on the flight's own ease-out quart, and only then the reducer's restore of the
// CameraCheckpoint, unchanged; under reduced motion the restore at once, with no travel.
describe('Back to Moment flies the flight backwards, then restores the checkpoint', () => {
  /** There after Go there, through the reducer (space.enter saves the moment first). */
  const thereSession = (reducedMotion: boolean): ObservationSession => {
    const picked = { ...base, reducedMotion, selectedFindingId: target.id } as ObservationSession;
    const out = dispatchAlbedianAction(picked, {
      type: 'space.enter',
      requestId: 'test-go-there',
      expectedViewRevision: picked.viewRevision,
      findingId: target.id,
      distancePolicy: 'measured_or_estimated',
    });
    expect(out.outcome.status).toBe('completed');
    return { ...out.session, spatialView: { ...out.session.spatialView, lockedAnchorNdc: [0.2, -0.1] } } as ObservationSession;
  };

  function returnThrough(session: ObservationSession, frames: FrameRequestCallback[], offsets: number[], calls = 1) {
    const updates: Update[] = [];
    const restored: number[] = [];
    let back: ((restore: () => void) => void) | null = null;
    const Probe = () => {
      back = useCameraFlightAnimation(session, ((u: Update) => updates.push(u)) as React.Dispatch<React.SetStateAction<ObservationSession>>).animateReturnToMoment;
      return null;
    };
    renderToString(createElement(Probe));
    const start = holdClock();
    for (let i = 0; i < calls; i++) back!(() => restored.push(updates.length));
    const seen: Array<{ states: ObservationSession[]; restored: number }> = [];
    const apply = () => {
      const states: ObservationSession[] = [];
      updates.reduce<ObservationSession>((s, u) => {
        const next = typeof u === 'function' ? u(s) : u;
        states.push(next);
        return next;
      }, session);
      return states;
    };
    seen.push({ states: apply(), restored: restored.length });
    for (const ms of offsets) {
      const next = frames.shift();
      if (next) next(start + ms);
      seen.push({ states: apply(), restored: restored.length });
    }
    return { seen, restored, frames };
  }

  it('restores at once under reduced motion, with no travel', () => {
    const frames = queueFrames();
    const { seen, restored } = returnThrough(thereSession(true), frames, []);
    expect(restored).toEqual([0]);
    expect(seen[0]!.states).toHaveLength(0);
    expect(frames).toHaveLength(0);
  });

  it('flies the second half back, then the first, and restores only at the end, once', () => {
    const there = thereSession(false);
    const home = returnCheckpointOf(there)!.spatialView.observerOriginParsec;
    const from = there.spatialView.observerOriginParsec;
    const { seen, restored } = returnThrough(there, queueFrames(), [300, 1200, 1499, 1500]);
    // The first frame at once: the second half at its start, the camera where it is
    const first = seen[0]!.states.at(-1)!;
    expect(first.spatialView.transitionPhase).toBe('phase_b_sky_to_space');
    expect(first.spatialView.transitionProgress).toBe(1);
    expect(first.spatialView.observerOriginParsec).toEqual(from);
    // 300 ms: the camera on its way back, eased
    const b = seen[1]!.states.at(-1)!;
    expect(b.spatialView.transitionPhase).toBe('phase_b_sky_to_space');
    expect(b.machines.navigation).toBe('space');
    expect(b.spatialView.transitionProgress).toBeCloseTo(1 - flightEase(300 / FLIGHT_HALF_MS), 3);
    const share = distancePc(from, b.spatialView.observerOriginParsec) / distancePc(from, home);
    expect(share).toBeCloseTo(flightEase(300 / FLIGHT_HALF_MS), 3);
    // 1200 ms: back at the flight's start, the registered sky going back to the photo
    const a = seen[2]!.states.at(-1)!;
    expect(a.spatialView.transitionPhase).toBe('phase_a_photo_to_sky');
    expect(a.spatialView.mode).toBe('registered_sky');
    expect(a.machines.navigation).toBe('sky_registered');
    expect(a.spatialView.observerOriginParsec).toEqual(home);
    expect(a.spatialView.lockedAnchorNdc).toEqual([0.2, -0.1]);
    expect(a.spatialView.transitionProgress).toBeCloseTo(1 - flightEase((1200 - FLIGHT_HALF_MS) / FLIGHT_HALF_MS), 3);
    // Not restored before both halves; restored once at 1500 ms
    expect(seen.slice(0, 4).map((x) => x.restored)).toEqual([0, 0, 0, 0]);
    expect(seen[4]!.restored).toBe(1);
    expect(restored).toHaveLength(1);
  });

  it('ignores a second press while it flies back', () => {
    const frames = queueFrames();
    const { seen, restored } = returnThrough(thereSession(false), frames, [300, 1200, 1500], 2);
    // The second press made no frame of its own
    expect(seen[0]!.states).toHaveLength(1);
    expect(restored).toHaveLength(1);
  });

  it('stops a flight still on its way there before flying back', () => {
    const frames = queueFrames();
    const cancel = vi.mocked(window.cancelAnimationFrame);
    let fly: ((id: string) => void) | null = null;
    const There = () => {
      fly = useCameraFlightAnimation({ ...base, reducedMotion: false }, (() => {}) as React.Dispatch<React.SetStateAction<ObservationSession>>)
        .animatePhaseAToPhaseBSpaceFlight;
      return null;
    };
    renderToString(createElement(There));
    fly!(target.id);
    const pending = frames.length;
    expect(pending).toBeGreaterThan(0);
    const back = returnThrough(thereSession(false), frames, []);
    expect(cancel).toHaveBeenCalledWith(pending);
    while (back.frames.length > 0) back.frames.shift()!(performance.now() + 60_000);
    expect(back.restored).toHaveLength(1);
  });
});
