// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { poseWeights } from '../craftPoses.js';
import { BRAKE_BEFORE_HOVER_S } from '../craftAlive.js';
import { HOVER_WITHIN_FLOORS } from '../nearStar.js';
import { STEER_START_SPEED_PC_S } from '../steering.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

// craft-alive-three version two (DECIDED-BY-LEAD 27 Nov 11:59, MOTION's approach-curve-numbers.md item 2): on the paced
// approach to a star, the phone's and the laptop's, the suit's poses go by time to the hover, as the owner's reference
// leaves cruise: cruise held whole until three quarters of a second before the hover, the brake from there on its own
// smoothing, hover from the floor on; W held near a star raises cruise as S raises brake; Reduce Motion holds hover.
// The weights read here are the ones the craft layer draws (poseWeights of the layer's own signals, data-alive).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(reduced = false): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, reducedMotion: reduced, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const gone = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
  return { ...gone, spatialView: { ...gone.spatialView, transitionPhase: 'at_space', transitionProgress: 1 } } as ObservationSession;
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

const elsewhere: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(200, -40)),
  magnitude: new Float32Array([3]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([50]), flags: new Uint8Array([1]), byteLength: 0,
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

const layer = () => container.querySelector('[data-testid="flight-stars"]')!;
interface Sample { t: number; d: number; floor: number; hover: number; cruise: number; brake: number }
let clock = 0;
function sample(): Sample {
  const [, speed, brake] = layer().getAttribute('data-alive')!.split(' ').map(Number);
  const [d, floor] = layer().getAttribute('data-near')!.split(' ').map(Number);
  const w = poseWeights({ bank: 0, speed: speed!, brake: brake!, roll: 0 });
  return { t: clock, d: d!, floor: floor!, ...w };
}

/** `rounds` frames of `ms` each, a sample taken after every one. */
async function fly(rounds: number, ms: number, out: Sample[]) {
  for (let r = 0; r < rounds; r++) {
    now += ms;
    clock += ms / 1000;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
    if (layer().getAttribute('data-alive')) out.push(sample());
  }
}

async function steer(w: number, h: number, phone: boolean, reduced = false) {
  clock = 0;
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(elsewhere);
    return { setView: () => {}, dispose: () => {} };
  };
  const session = there(reduced);
  const tree = (steering: boolean) => createElement(FlightStarsLayer, { session, viewportWidthPx: w, viewportHeightPx: h, phone, openTiles, steering, onPressSky: () => {} });
  await act(async () => root.render(tree(false)));
  await fly(4, 1000 / 60, []);
  await act(async () => root.render(tree(true)));
}

const atFloor = (s: Sample) => s.d / s.floor <= 1.000001;
const atHover = (s: Sample) => s.d / s.floor <= HOVER_WITHIN_FLOORS;

/**
 * The poses against the time to the hover, from the run's own hover frame: cruising whole just before the brake is due,
 * the brake first seen within a frame or two of three quarters of a second before the hover and near whole by its last
 * tenth, hover from a second after on. The first `from` seconds of samples (S's own brake letting go) are not read for the
 * brake's start.
 */
function expectByTime(seen: Sample[], hovered: number, from = 0) {
  const tHover = seen[hovered]!.t;
  const near = (offset: number) => seen.reduce((best, s) => (Math.abs(s.t - (tHover + offset)) < Math.abs(best.t - (tHover + offset)) ? s : best));
  const due = tHover - BRAKE_BEFORE_HOVER_S;
  const cruising = near(-BRAKE_BEFORE_HOVER_S - 0.05);
  expect(cruising.cruise).toBeGreaterThan(0.95);
  expect(cruising.brake).toBeLessThan(0.01);
  const firstBrake = seen.find((s) => s.t >= seen[0]!.t + from && s.brake > 0.05)!;
  expect(firstBrake.t - due).toBeGreaterThanOrEqual(0);
  expect(firstBrake.t - due).toBeLessThan(0.06);
  expect(near(-0.1).brake).toBeGreaterThan(0.9);
  const held = seen.filter((s) => s.t >= tHover + 1);
  expect(held.length).toBeGreaterThan(30);
  for (const s of held) expect(s.hover).toBeGreaterThan(0.95);
}
const key = (type: 'keydown' | 'keyup', code: string) => act(async () => void window.dispatchEvent(new KeyboardEvent(type, { code, key: code === 'KeyW' ? 'w' : code })));

describe('the suit\'s poses through a paced approach (craft-alive-three)', { timeout: 60_000 }, () => {
  for (const [w, h, phone] of [[390, 844, true], [1440, 900, false]] as const) {
    const size = phone ? 'the phone' : 'the laptop';

    it(`${size}: cruise whole until three quarters of a second before the hover, the brake through them, hover from the floor on`, async () => {
      await steer(w, h, phone);
      const seen: Sample[] = [];
      // Forty seconds at sixty frames a second, the hands still: the hand-over, the paced approach, the arrival, the floor
      await fly(2400, 1000 / 60, seen);
      const hovered = seen.findIndex(atHover);
      expect(hovered).toBeGreaterThan(60);
      expectByTime(seen, hovered);
    });
  }

  it('the laptop: W held at the floor raises cruise past one half, and hover comes back once it is let go', async () => {
    await steer(1440, 900, false);
    const seen: Sample[] = [];
    await fly(1800, 1000 / 60, seen);
    expect(atFloor(seen.at(-1)!)).toBe(true);
    const held: Sample[] = [];
    await key('keydown', 'KeyW');
    await fly(90, 1000 / 60, held);
    await key('keyup', 'KeyW');
    expect(held.at(-1)!.cruise).toBeGreaterThan(0.5);
    const after: Sample[] = [];
    await fly(180, 1000 / 60, after);
    expect(after.at(-1)!.hover).toBeGreaterThan(0.95);
  });

  it('the laptop: after S has lowered the set speed, the brake still comes three quarters of a second before the hover', async () => {
    await steer(1440, 900, false);
    await fly(60, 1000 / 60, []);
    await key('keydown', 'KeyS');
    await fly(90, 1000 / 60, []);
    await key('keyup', 'KeyS');
    const set = Number(layer().getAttribute('data-near')!.split(' ')[3]);
    expect(set).toBeLessThan(STEER_START_SPEED_PC_S * 0.9);
    // S's own brake let go on its smoothing first, then the approach at the lowered speed to the hover
    const seen: Sample[] = [];
    await fly(3000, 1000 / 60, seen);
    const hovered = seen.findIndex(atHover);
    expect(hovered).toBeGreaterThan(120);
    expectByTime(seen, hovered, 1);
  });

  it('Reduce Motion holds hover the whole approach, on the phone', async () => {
    await steer(390, 844, true, true);
    const seen: Sample[] = [];
    await fly(1800, 1000 / 60, seen);
    expect(seen.length).toBeGreaterThan(1000);
    for (const s of seen) expect(s.hover).toBe(1);
  });
});
