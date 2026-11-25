// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { HANDOVER_SECONDS, NEAR_STAR_TAU_S, READOUT_EVERY_MS, nearStarFloorPc } from '../nearStar.js';
import { flightCameraOf } from '../flightFrame.js';
import { STEER_START_SPEED_PC_S } from '../steering.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

// near-star-flight-one (DECIDED-BY-LEAD 25 Nov 07:28): on the real star layer, the steered flight heads at the star at
// the hand-over, comes in on its distance over 2.5 s, stops at the star's floor, and tells the page of its numbers at
// most ten times a second.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(reducedMotion = false): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, reducedMotion, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

function quietGl(): unknown {
  let n = 0;
  return new Proxy({}, {
    get: (_t, key) => {
      if (key === 'getShaderParameter' || key === 'getProgramParameter') return () => true;
      if (key === 'isContextLost') return () => false;
      if (key === 'getUniformLocation') return () => ({ id: ++n });
      if (key === 'getAttribLocation') return () => 0;
      if (typeof key === 'string' && /^create/.test(key)) return () => ({ id: ++n });
      if (typeof key === 'string' && /^[A-Z_0-9]+$/.test(key)) return 1;
      return () => {};
    },
  });
}

const tile: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(83, 4)),
  magnitude: new Float32Array([2]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([120]), flags: new Uint8Array([1]), byteLength: 0,
};

let container: HTMLDivElement;
let root: Root;
let frames: FrameRequestCallback[];
let now = 10_000;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  frames = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    frames.push(cb);
    return frames.length;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(quietGl() as never);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

/** Runs the queued frames `rounds` times, `ms` apart. */
async function tick(rounds: number, ms = 1000 / 60) {
  for (let r = 0; r < rounds; r++) {
    now += ms;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
  }
}

/** The layer's own record of the pacing: distance to the star, its floor, the paced speed and the set speed. */
const near = () => container.querySelector('[data-testid="flight-stars"]')!.getAttribute('data-near')!.split(' ').map(Number);

/**
 * A tile whose one star is the star flown to as its own catalog row would place it: a hundredth farther than the place
 * flown to and a ten-thousandth of a radian off it (within arrival.ts's match), magnitude 2.
 */
function rowAtThePlace(): { tile: CatalogStarCoreTile; positionPc: [number, number, number] } {
  const ref = flightCameraOf(there(false), 1440, 900)!.referencePc;
  const d = Math.hypot(ref[0], ref[1], ref[2]);
  const u = [ref[0] / d, ref[1] / d, ref[2] / d];
  // Off the place by 1e-4 rad, about a perpendicular axis
  const p = Math.abs(u[2]!) < 0.9 ? [-u[1]!, u[0]!, 0] : [0, -u[2]!, u[1]!];
  const pn = Math.hypot(p[0]!, p[1]!, p[2]!);
  const dir = [u[0]! + (1e-4 * p[0]!) / pn, u[1]! + (1e-4 * p[1]!) / pn, u[2]! + (1e-4 * p[2]!) / pn];
  const directions = new Float32Array(dir);
  const distancePc = new Float32Array([d * 1.01]);
  const dn = Math.hypot(directions[0]!, directions[1]!, directions[2]!);
  const positionPc: [number, number, number] = [(directions[0]! / dn) * distancePc[0]!, (directions[1]! / dn) * distancePc[0]!, (directions[2]! / dn) * distancePc[0]!];
  return { tile: { ...tile, directions, distancePc, magnitude: new Float32Array([2]) }, positionPc };
}

async function steerFrom(reducedMotion: boolean, onSteer = vi.fn(), theTile: CatalogStarCoreTile = tile) {
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(theTile);
    return { setView: () => {}, dispose: () => {} };
  };
  const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: there(reducedMotion), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering, onSteer });
  await act(async () => root.render(tree(false)));
  await tick(4);
  await act(async () => root.render(tree(true)));
  return onSteer;
}

describe('the flight near the star (near-star-flight-one)', () => {
  it('heads at the star over the hand-over, then comes in on its distance over 2.5 s', async () => {
    await steerFrom(false);
    await tick(1);
    const d0 = near()[0]!;
    // The hand-over's frames: the camera turns to the star, so the distance shrinks more slowly than straight in
    await tick(Math.round(HANDOVER_SECONDS * 60) - 1);
    const dHanded = near()[0]!;
    expect(dHanded / d0).toBeGreaterThan((1 - 1 / 60 / NEAR_STAR_TAU_S) ** 44);
    // Then straight at it: each frame a sixtieth of a second takes 1/150 of the way left
    await tick(60);
    expect(near()[0]! / dHanded).toBeCloseTo((1 - 1 / 60 / NEAR_STAR_TAU_S) ** 60, 6);
    // The speed flown is the paced one, under the set speed
    const [d, , paced, set] = near();
    // (paced on the distance at the frame's start, a 150th more than at its end)
    expect(paced).toBeCloseTo(d! / (NEAR_STAR_TAU_S * (1 - 1 / 60 / NEAR_STAR_TAU_S)), 9);
    expect(paced).toBeLessThan(set!);
  });

  it('under Reduce Motion heads at the star at once: straight in from the first frame', async () => {
    await steerFrom(true);
    await tick(1);
    const d1 = near()[0]!;
    await tick(30);
    expect(near()[0]! / d1).toBeCloseTo((1 - 1 / 60 / NEAR_STAR_TAU_S) ** 30, 6);
  });

  it('never comes nearer than the star\'s floor, W held all the way in, and tells the page at most ten times a second', async () => {
    const onSteer = await steerFrom(false);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', key: 'w' })));
    const told = onSteer.mock.calls.length;
    // Sixty seconds in tenths of a second
    await tick(600, 100);
    const [d, floor] = near();
    expect(d! / floor!).toBeCloseTo(1, 6);
    const reports = onSteer.mock.calls.slice(told).map(([r]) => r).filter(Boolean) as { toStarPc: number; speedPcPerS: number }[];
    expect(reports.length).toBeGreaterThan(10);
    expect(reports.length).toBeLessThanOrEqual(600 / (READOUT_EVERY_MS / 100));
    // The page heard the distance come down to the floor and the flight stop there
    expect(reports.at(-1)!.toStarPc / floor!).toBeCloseTo(1, 3);
    expect(reports.at(-1)!.speedPcPerS).toBeLessThan(floor! / 1e3);
    // Never told twice within the interval, at sixty frames a second
    onSteer.mockClear();
    await act(async () => window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW', key: 'w' })));
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    await tick(60);
    expect(onSteer.mock.calls.length).toBeLessThanOrEqual(Math.ceil(1000 / READOUT_EVERY_MS));
  });

  it('goes by the star\'s own row when it is found: aims at it, is paced by it, and reads the distance to it', async () => {
    const row = rowAtThePlace();
    const onSteer = await steerFrom(false, vi.fn(), row.tile);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', key: 'w' })));
    await tick(600, 100);
    const [d, floor] = near();
    // The row's own floor, from its magnitude and distance, not the 1 AU of a place whose light is unknown
    const earthPc = Math.hypot(...row.positionPc);
    expect(floor! / nearStarFloorPc({ earthPc, magnitude: 2 })).toBeCloseTo(1, 6);
    expect(d! / floor!).toBeCloseTo(1, 6);
    // The readout's distance is to the row (at its floor), not to the place flown to (a hundredth of its distance away)
    const last = onSteer.mock.calls.map(([r]) => r).filter(Boolean).at(-1) as { toStarPc: number };
    expect(last.toStarPc / floor!).toBeCloseTo(1, 3);
  });

  it('flies the set speed when S brings it under the paced one', async () => {
    await steerFrom(false);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyS', key: 's' })));
    await tick(240);
    const [, , paced, set] = near();
    // Four seconds of S: a sixteenth of the start speed, under the distance over 2.5 s by then
    expect(set! / (STEER_START_SPEED_PC_S / 16)).toBeCloseTo(1, 6);
    expect(paced).toBe(set);
  });
});
