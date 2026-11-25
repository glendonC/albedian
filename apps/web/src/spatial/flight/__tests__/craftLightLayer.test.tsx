// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import type { Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, type CraftRenderer } from '../FlightCraftLayer.js';
import { CRAFT_SKY_SIZE, CRAFT_SKY_URL, craftLighting, keyLight, type LightCandidate } from '../craftLight.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// craft-light-one on craft-cache-one and steer-frame-one: each frame the one renderer draws, at a star or while steering,
// lights the craft by the brightest real source where the camera is, in its catalog tint, tone maps with ACES and
// exposes for the side the camera sees (craftLight.ts); a renderer that is not three's has no sky, so no fill and, since
// craft-light-two, no second light (the Milky Way's brightest sky), which stands beside the key at 0 until a sky comes.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}

interface Drawn {
  color: [number, number, number];
  toKey: Vec3;
  craftAt: Vec3;
  exposure: number | undefined;
  toneMapping: THREE.ToneMapping | undefined;
  environment: number;
  /** craft-light-two: the second light's intensity (the Milky Way's brightest sky). */
  galaxy: number;
}

/** A renderer stand-in that records, at each render, the key light, the exposure, the tone mapping and the fill. */
function recordingRenderer() {
  const drawn: Drawn[] = [];
  const makeRenderer = (): CraftRenderer => {
    const r: CraftRenderer = {
      setPixelRatio: () => {},
      setSize: () => {},
      setClearColor: () => {},
      clear: () => {},
      dispose: () => {},
      render: (scene: THREE.Scene) => {
        const lights = scene.children.filter((c) => (c as THREE.DirectionalLight).isDirectionalLight) as THREE.DirectionalLight[];
        const key = lights[0]!;
        const d = key.position.clone().sub(key.target.position).normalize();
        drawn.push({
          color: [key.color.r, key.color.g, key.color.b],
          toKey: [d.x, d.y, d.z],
          craftAt: [key.target.position.x, key.target.position.y, key.target.position.z],
          exposure: r.toneMappingExposure,
          toneMapping: r.toneMapping,
          environment: scene.environmentIntensity,
          galaxy: lights[1]?.intensity ?? Number.NaN,
        });
      },
    };
    return r;
  };
  return { drawn, makeRenderer };
}

const model = async () => {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.BoxGeometry(2, 1, 1), new THREE.MeshStandardMaterial()));
  return g;
};

function expectLitBy(d: Drawn, cameraPc: Vec3, candidates: readonly LightCandidate[]): void {
  const key = keyLight(cameraPc, cameraPc, candidates);
  d.color.forEach((c, k) => expect(c).toBeCloseTo(key.tint[k]!, 6));
  d.toKey.forEach((c, k) => expect(c).toBeCloseTo(key.direction[k]!, 6));
  expect(d.toneMapping).toBe(THREE.ACESFilmicToneMapping);
  // craft-light-two: with no sky (a renderer that is not three's) the meter counts the key and the ruled fill, and the second light is out
  expect(d.exposure).toBeCloseTo(craftLighting({ craftFromCameraM: d.craftAt, keyDirection: key.direction, sky: null }).exposure, 9);
  expect(d.galaxy).toBe(0);
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('the craft lit by the brightest real source where the camera is', () => {
  const camera = flightCameraOf(there(), 1440, 900)!;
  const at = camera.observerPc;
  // A red star a parsec from the camera (typed test data): far brighter there than the Sun
  const star: LightCandidate = { positionPc: [at[0] + 1, at[1], at[2]], earthMag: 1, earthDistancePc: 100, colorIndex: 2, isBMinusV: false };

  it('at the star: its tint and direction, ACES, the meter on the seen side, no fill without three\'s renderer', async () => {
    const { drawn, makeRenderer } = recordingRenderer();
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: model, makeRenderer, keyCandidates: [star] })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-state')).toBe('drawn');
    const last = drawn.at(-1)!;
    expect(keyLight(at, at, [star]).source).toBe('star');
    expectLitBy(last, at, [star]);
    expect(last.color[0]).toBeGreaterThan(last.color[2]);
    expect(last.environment).toBe(0);
  });

  it('with no star given: the Sun, white, from where it is', async () => {
    const { drawn, makeRenderer } = recordingRenderer();
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: model, makeRenderer })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expectLitBy(drawn.at(-1)!, at, []);
    expect(drawn.at(-1)!.color).toEqual([1, 1, 1]);
  });

  it('draws again when the star that may light it changes, with the camera where it was', async () => {
    const { drawn, makeRenderer } = recordingRenderer();
    const tree = (keyCandidates: readonly LightCandidate[]) => createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: model, makeRenderer, keyCandidates });
    await act(async () => root.render(tree([star])));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expectLitBy(drawn.at(-1)!, at, [star]);
    await act(async () => root.render(tree([])));
    expectLitBy(drawn.at(-1)!, at, []);
  });

  it('in each steering frame the loop draws: lit from where the steering camera stands, by the same star', async () => {
    const { drawn, makeRenderer } = recordingRenderer();
    const drawRef: { current: ((s: { positionPc: Vec3; rows: ReturnType<typeof steerStartOf>['rows'] }) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: model, makeRenderer, keyCandidates: [star], drawRef })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    const rows = steerStartOf(camera).rows;
    // Steered to the far side of the star: the key now comes from the other way
    const steered: Vec3 = [at[0] + 2, at[1], at[2]];
    const before = drawn.length;
    act(() => drawRef.current!({ positionPc: steered, rows }));
    expect(drawn.length).toBe(before + 1);
    expectLitBy(drawn.at(-1)!, steered, [star]);
    expect(drawn.at(-1)!.toKey[0]).toBeLessThan(0);
  });
});

// AUDIT-D and the lead (18:18): the craft's sky costs Go there no 6000 px decode: the glow is fetched, decoded and
// shrunk to the size a fill needs off the main thread, and summed from that small copy
describe('the craft\'s sky, at the size a fill needs', () => {
  it('is shrunk to 720 by 360 by the browser off the main thread, never decoded at full size here', async () => {
    const fetched: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      fetched.push(url);
      return new Response(new Blob([new Uint8Array([1, 2, 3])]), { status: 200 });
    }));
    const asked: unknown[] = [];
    const bitmap = { width: 720, height: 360, close: vi.fn() };
    vi.stubGlobal('createImageBitmap', vi.fn(async (_blob: Blob, options: unknown) => {
      asked.push(options);
      return bitmap;
    }));
    const made: unknown[] = [];
    vi.stubGlobal('Image', class { constructor() { made.push(this); } });
    const { smallCraftSky } = await import('../FlightCraftLayer.js');
    expect(await smallCraftSky()).toBe(bitmap);
    expect(fetched).toEqual([CRAFT_SKY_URL]);
    expect(asked).toEqual([{ resizeWidth: CRAFT_SKY_SIZE.width, resizeHeight: CRAFT_SKY_SIZE.height, resizeQuality: 'high' }]);
    expect(made).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it('where the browser cannot shrink it, is decoded off the main thread (decode) and shrunk by the canvas', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob([new Uint8Array([1])]), { status: 200 })));
    vi.stubGlobal('createImageBitmap', vi.fn(async () => {
      throw new Error('no resize options');
    }));
    const decoded = vi.fn(async () => {});
    vi.stubGlobal('Image', class { src = ''; decode = decoded; });
    // jsdom has no object URLs: lent for this test and taken back
    const created = vi.fn(() => 'blob:sky');
    const revoked = vi.fn();
    const lent = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    const had = { createObjectURL: lent.createObjectURL, revokeObjectURL: lent.revokeObjectURL };
    lent.createObjectURL = created;
    lent.revokeObjectURL = revoked;
    try {
      const { smallCraftSky } = await import('../FlightCraftLayer.js');
      const img = (await smallCraftSky()) as unknown as { src: string };
      expect(img.src).toBe('blob:sky');
      expect(decoded).toHaveBeenCalledTimes(1);
      expect(created).toHaveBeenCalledTimes(1);
      expect(revoked).toHaveBeenCalledWith('blob:sky');
    } finally {
      lent.createObjectURL = had.createObjectURL;
      lent.revokeObjectURL = had.revokeObjectURL;
      vi.unstubAllGlobals();
    }
  });

  it('fails as the craft\'s sky when the glow does not come', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 404 })));
    const { smallCraftSky } = await import('../FlightCraftLayer.js');
    await expect(smallCraftSky()).rejects.toThrow('craft sky');
    vi.unstubAllGlobals();
  });
});
