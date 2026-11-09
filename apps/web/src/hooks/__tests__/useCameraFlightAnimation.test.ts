// @vitest-environment jsdom
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import type { ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { executeVoiceOrTextCommand } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlightAndRestoreHud } from '../../components/FlightAndRestoreHud.js';
import { FLIGHT_STANDOFF_PC, distancePc } from '../../spatial/flightTarget.js';
import { useCameraFlightAnimation } from '../useCameraFlightAnimation.js';

/**
 * Go There ends one light year short of the star, on the line from where the camera started
 * (render-14). The old flight stopped at 0.45 of the star's distance from the Sun and said it had
 * arrived: Tabit, 8.02 pc away, read 11.78 ly from Earth with 14.39 ly still to go.
 */

type Update = ObservationSession | ((prev: ObservationSession) => ObservationSession);

/**
 * Flies to a finding and returns the session after each update the flight made, in order. The
 * hook flies from the session it rendered with; its updates apply to `appliedTo`, the state the
 * app holds when the flight starts (by default the same session).
 */
function flyThrough(
  session: ObservationSession,
  findingId: string,
  frames: FrameRequestCallback[],
  appliedTo: ObservationSession = session
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
  fly!(findingId);
  // Run the queued animation frames: one in each phase, then past the flight's end
  const start = performance.now();
  const times = [start + 300, start + 1200, start + 60_000];
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
    // camera on the star, then the app starts the flight from the session it rendered with
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
