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
import { flightCameraOf } from '../flightFrame.js';
import * as THREE from 'three';
import type { CraftRenderer } from '../FlightCraftLayer.js';
import { CRAFTS } from '../crafts.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

// phone-star-open-one: on the real star layer, at rest and through the steering's hand-over, the star flown to stands
// at the arrival record's resting place (the open part of the frame, above the phone's readout), never where the
// solve's reference pixel falls; under Reduce Motion from the first steered frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(reducedMotion = false): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, reducedMotion, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const gone = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
  // At rest at the star, the view turned all the way to the resting place
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

/** A tile whose one star is the star flown to as its own catalog row places it: a hundredth farther, 1e-4 rad off. */
function rowAtThePlace(w: number, h: number): CatalogStarCoreTile {
  const ref = flightCameraOf(there(false), w, h)!.referencePc;
  const d = Math.hypot(ref[0], ref[1], ref[2]);
  const u = [ref[0] / d, ref[1] / d, ref[2] / d];
  const p = Math.abs(u[2]!) < 0.9 ? [-u[1]!, u[0]!, 0] : [0, -u[2]!, u[1]!];
  const pn = Math.hypot(p[0]!, p[1]!, p[2]!);
  const directions = new Float32Array([u[0]! + (1e-4 * p[0]!) / pn, u[1]! + (1e-4 * p[1]!) / pn, u[2]! + (1e-4 * p[2]!) / pn]);
  return {
    layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions,
    magnitude: new Float32Array([2]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([d * 1.01]), flags: new Uint8Array([1]), byteLength: 0,
  };
}

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

/** Where the layer drew the star flown to on its last steered frame, CSS pixels. */
const starPx = () => {
  const v = container.querySelector('[data-testid="flight-stars"]')!.getAttribute('data-star-px');
  return v ? v.split(',').map(Number) : null;
};

/** A tile with one star far from the place flown to: the star's own row is not found, so the place is the star. */
const elsewhere: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(200, -40)),
  magnitude: new Float32Array([3]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([50]), flags: new Uint8Array([1]), byteLength: 0,
};

async function steer(w: number, h: number, phone: boolean, reducedMotion: boolean, tile: CatalogStarCoreTile) {
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(tile);
    return { setView: () => {}, dispose: () => {} };
  };
  const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: there(reducedMotion), viewportWidthPx: w, viewportHeightPx: h, phone, openTiles, steering });
  await act(async () => root.render(tree(false)));
  await tick(4);
  await act(async () => root.render(tree(true)));
}

const off = (p: number[], rest: readonly number[]) => Math.hypot(p[0]! - rest[0]!, p[1]! - rest[1]!);

describe('the star flown to held at its resting place while steering (phone-star-open-one)', () => {
  for (const [w, h, phone] of [[390, 844, true], [1440, 900, false]] as const) {
    const rest = restPointPx(w, h, phone);
    const size = phone ? 'on the phone' : 'on the laptop';

    it(`${size}: the place flown to stands where it rested on the hand-over's first frame, every frame of the turn, and after`, async () => {
      await steer(w, h, phone, false, elsewhere);
      await tick(1);
      // The arrival measured the place from Earth and Go there left the camera on that line: within a pixel
      expect(off(starPx()!, rest)).toBeLessThan(1);
      let far = 0;
      for (let i = 0; i < Math.round(HANDOVER_SECONDS * 60); i++) {
        await tick(1);
        far = Math.max(far, off(starPx()!, rest));
      }
      expect(far).toBeLessThan(1);
      await tick(30);
      expect(off(starPx()!, rest)).toBeLessThan(0.05);
    });

    it(`${size}: the star's own row starts where it stood, moves only toward its resting place, and holds there`, async () => {
      await steer(w, h, phone, false, rowAtThePlace(w, h));
      await tick(1);
      // Seen from the camera, a row a hundredth farther and a ten-thousandth of a radian off is not quite where the
      // arrival put the place: the first frame keeps it where it stood, so nothing jumps
      const start = off(starPx()!, rest);
      expect(start).toBeGreaterThan(1);
      let last = start;
      for (let i = 0; i < Math.round(HANDOVER_SECONDS * 60); i++) {
        await tick(1);
        const now = off(starPx()!, rest);
        expect(now).toBeLessThanOrEqual(last + 1e-6);
        last = now;
      }
      expect(last).toBeLessThan(0.05);
      await tick(30);
      expect(off(starPx()!, rest)).toBeLessThan(0.05);
    });
  }

  it('brings the star to its resting place when the steering is taken before the arrival has turned the view all the way', async () => {
    const rest = restPointPx(390, 844, true);
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(elsewhere);
      return { setView: () => {}, dispose: () => {} };
    };
    const at = there(false);
    const midway = { ...at, spatialView: { ...at.spatialView, transitionPhase: 'phase_b_sky_to_space', transitionProgress: 0.5 } } as ObservationSession;
    const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: midway, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, steering });
    await act(async () => root.render(tree(false)));
    await tick(4);
    await act(async () => root.render(tree(true)));
    await tick(1);
    // Half-way: the star stands half-way to its resting place, and the first steered frame keeps it there
    expect(off(starPx()!, rest)).toBeGreaterThan(1);
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    expect(off(starPx()!, rest)).toBeLessThan(0.05);
  });

  it('on the phone under Reduce Motion: at its resting place from the first steered frame', async () => {
    const rest = restPointPx(390, 844, true);
    await steer(390, 844, true, true, rowAtThePlace(390, 844));
    await tick(1);
    expect(off(starPx()!, rest)).toBeLessThan(0.05);
  });

  it('keeps holding the star through the hand-over while a turn is already held, the turn moving it only after', async () => {
    const rest = restPointPx(390, 844, true);
    await steer(390, 844, true, false, elsewhere);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    let far = 0;
    for (let i = 0; i < Math.round(HANDOVER_SECONDS * 60) - 1; i++) {
      await tick(1);
      far = Math.max(far, off(starPx()!, rest));
    }
    expect(far).toBeLessThan(1);
  });

  it('draws the craft through the same frame, its camera seeing the star at the resting place too (under Reduce Motion)', async () => {
    const rest = restPointPx(390, 844, true);
    const clips: number[][] = [];
    const makeCraftRenderer = (): CraftRenderer => ({
      setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {},
      render: (_scene: THREE.Scene, cam: THREE.Camera) => {
        clips.push([...(cam as THREE.Camera).projectionMatrix.elements]);
      },
    });
    const craftLoad = async () => {
      const g = new THREE.Group();
      g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 0.5), new THREE.MeshStandardMaterial()));
      return g;
    };
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(elsewhere);
      return { setView: () => {}, dispose: () => {} };
    };
    // Under Reduce Motion the turn is made on the first frame and the camera flies straight at the star after it, so
    // the star's direction from where the camera starts stays its direction all the way
    const session = there(true);
    const cam = flightCameraOf(session, 390, 844)!;
    const tree = (steering: boolean) => createElement(FlightStarsLayer, { session, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, steering, craft: CRAFTS['astronaut-emu']!, craftLoad, makeCraftRenderer });
    await act(async () => root.render(tree(false)));
    await tick(4);
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    await act(async () => root.render(tree(true)));
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    // The star flown to (the place, straight ahead after the turn), through the craft camera's clip matrix
    const to = [cam.referencePc[0] - cam.observerPc[0], cam.referencePc[1] - cam.observerPc[1], cam.referencePc[2] - cam.observerPc[2]];
    const seen = (m: number[]) => {
      const c = [0, 1, 3].map((i) => m[i]! * to[0]! + m[4 + i]! * to[1]! + m[8 + i]! * to[2]!);
      return [((c[0]! / c[2]! + 1) / 2) * 390, ((1 - c[1]! / c[2]!) / 2) * 844];
    };
    expect(off(seen(clips.at(-1)!), rest)).toBeLessThan(0.5);
    // A render of the page mid-flight (the readout's numbers told) draws the craft with the frame too, not without it
    await act(async () => root.render(tree(true)));
    expect(off(seen(clips.at(-1)!), rest), 'on a render').toBeLessThan(0.5);
  });

  it('leaves the frame where it stands at the person\'s first turn once the hand-over is made, so the turn moves the star', async () => {
    const rest = restPointPx(390, 844, true);
    await steer(390, 844, true, false, elsewhere);
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    expect(off(starPx()!, rest)).toBeLessThan(0.05);
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    await tick(30);
    expect(off(starPx()!, rest)).toBeGreaterThan(20);
  });
});
