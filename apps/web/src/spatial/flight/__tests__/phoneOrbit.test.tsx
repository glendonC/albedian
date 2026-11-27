// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { HANDOVER_SECONDS } from '../nearStar.js';
import { restPointPx } from '../heldStar.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import * as THREE from 'three';
import type { CraftRenderer } from '../FlightCraftLayer.js';
import { CRAFTS } from '../crafts.js';

// phone-orbit-one (MOTION's page, DECIDED-BY-LEAD 27 Nov 03:19 and 03:29): in a flight to a star every drag, the
// finger's on the phone and the mouse's on the laptop, goes round the star with the look held on it: the star keeps its
// place, the craft its distance and floor, the sky swings with the hand; the keys still turn; free flight keeps turning.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
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

async function tick(rounds: number, ms = 1000 / 60) {
  for (let r = 0; r < rounds; r++) {
    now += ms;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
  }
}

const layer = () => container.querySelector('[data-testid="flight-stars"]')!;
const starPx = () => layer().getAttribute('data-star-px')!.split(',').map(Number);
const near = () => layer().getAttribute('data-near')!.split(' ').map(Number);
const rings = () => [...container.querySelectorAll('svg circle[data-finding-id]')].map((c) => [Number(c.getAttribute('cx')), Number(c.getAttribute('cy'))]);
const off = (p: number[], q: readonly number[]) => Math.hypot(p[0]! - q[0]!, p[1]! - q[1]!);

async function steer(w: number, h: number, phone: boolean, freeFlight = false) {
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(elsewhere);
    return { setView: () => {}, dispose: () => {} };
  };
  // As the page passes it: a press on the sky is always taken, so the sky is there before the steering starts
  const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: there(), viewportWidthPx: w, viewportHeightPx: h, phone, openTiles, steering, freeFlight, onPressSky: () => {} });
  await act(async () => root.render(tree(false)));
  await tick(4);
  await act(async () => root.render(tree(true)));
}

/** A drag across the sky, `dx` px over `n` frames, one move a frame, then the hand lifts. */
async function drag(dx: number, dy: number, n: number, each?: () => void) {
  const sky = container.querySelector('[data-testid="flight-sky"]')!;
  const ev = (type: string, x: number, y: number) => act(() => void sky.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y })));
  let x = 150;
  let y = 300;
  await ev('pointerdown', x, y);
  for (let i = 0; i < n; i++) {
    x += dx / n;
    y += dy / n;
    await ev('pointermove', x, y);
    await tick(1);
    each?.();
  }
  await ev('pointerup', x, y);
}

describe('a drag in a flight to a star goes round the star (phone-orbit-one)', () => {
  for (const [w, h, phone] of [[390, 844, true], [1440, 900, false]] as const) {
    const size = phone ? 'the phone\'s finger' : 'the laptop\'s mouse';
    const rest = restPointPx(w, h, phone);

    it(`${size}: the star keeps its place through the drag and its carry, while the sky swings with the hand`, async () => {
      await steer(w, h, phone);
      await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
      expect(off(starPx(), rest)).toBeLessThan(0.05);
      const before = rings();
      expect(before.length).toBeGreaterThan(0);
      let far = 0;
      await drag(-120, 40, 36, () => {
        far = Math.max(far, off(starPx(), rest));
      });
      await tick(60);
      far = Math.max(far, off(starPx(), rest));
      // The star where it stood the whole way; the matched stars' rings swung across the window
      expect(far).toBeLessThan(0.05);
      const after = rings();
      const moved = Math.max(0, ...after.map((p, i) => (before[i] ? off(p, before[i]!) : 0)));
      expect(moved).toBeGreaterThan(30);
    });

    it(`${size}: at the floor a drag circles at the floor's distance and never slides off it`, async () => {
      await steer(w, h, phone);
      await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', key: 'w' })));
      await tick(600, 100);
      await act(async () => window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW', key: 'w' })));
      const [d0, floor] = near();
      expect(d0! / floor!).toBeCloseTo(1, 6);
      await drag(-150, 0, 40);
      // Seven seconds after: still at the floor, the star still dead ahead
      await tick(70, 100);
      const [d] = near();
      expect(d! / floor!).toBeCloseTo(1, 6);
      expect(off(starPx(), rest)).toBeLessThan(0.05);
    });
  }

  it('turns the chase view with the orbit (MOTION\'s item six): the craft keeps its place on the window through the drag', async () => {
    const boxes: string[] = [];
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(elsewhere);
      return { setView: () => {}, dispose: () => {} };
    };
    const makeCraftRenderer = (): CraftRenderer => ({ setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {}, render: () => {} });
    const craftLoad = async () => {
      const g = new THREE.Group();
      g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 0.5), new THREE.MeshStandardMaterial()));
      return g;
    };
    const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, steering, onPressSky: () => {}, craft: CRAFTS['astronaut-emu']!, craftLoad, makeCraftRenderer });
    await act(async () => root.render(tree(false)));
    await tick(4);
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    await act(async () => root.render(tree(true)));
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    const centre = () => {
      const [x0, y0, x1, y1] = container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-box')!.split(',').map(Number);
      return [(x0! + x1!) / 2, (y0! + y1!) / 2];
    };
    const stood = centre();
    let far = 0;
    await drag(-120, 0, 30, () => {
      boxes.push(centre().join(','));
      far = Math.max(far, off(centre(), stood));
    });
    // The camera's roll with the bank (craft-alive, as ruled) moves it about 12 px; without the chase turned with the
    // orbit the look lags the orbit by its 0.12 s and the craft leads off about 55 px (measured on that mutant)
    expect(boxes.length).toBe(30);
    expect(far).toBeLessThan(25);
  });

  it('the keys still turn in a flight to a star: an arrow held takes the star away from its place', async () => {
    const rest = restPointPx(1440, 900, false);
    await steer(1440, 900, false);
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    await tick(30);
    expect(off(starPx(), rest)).toBeGreaterThan(20);
  });

  it('free flight, with no star, keeps turning by the drag', async () => {
    await steer(1440, 900, false, true);
    await tick(10);
    const before = rings();
    await drag(-120, 0, 30);
    await tick(10);
    expect(Number.isNaN(near()[0]!)).toBe(true);
    const after = rings();
    expect(Math.max(0, ...after.map((p, i) => (before[i] ? off(p, before[i]!) : 0)))).toBeGreaterThan(30);
  });
});
