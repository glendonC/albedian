// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer, MODEL_CACHE_SIZE } from '../FlightCraftLayer.js';
import { craftFrameRadiusM, craftSceneOf } from '../craftScene.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';

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

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('FlightCraftLayer', () => {
  it('says it failed, with no words, where the browser cannot draw it', async () => {
    const load = vi.fn(async () => new THREE.Group());
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load })));
    const layer = container.querySelector('[data-testid="flight-craft"]')!;
    expect(layer.getAttribute('data-state')).toBe('failed');
    expect(layer.getAttribute('data-craft')).toBe('voyager-1');
    expect(layer.textContent).toBe('');
    expect(layer.getAttribute('aria-hidden')).toBe('true');
    expect(load).not.toHaveBeenCalled();
  });
});

describe('FlightStarsLayer with a craft', () => {
  const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });

  it('draws the craft only when one is asked for', async () => {
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: null })));
    expect(container.querySelector('[data-testid="flight-craft"]')).toBeNull();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: CRAFTS['astronaut-emu']! })));
    expect(container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-craft')).toBe('astronaut-emu');
  });
});

describe('FlightCraftLayer: one renderer and one loader for the flight, models kept by address (craft-cache-one)', () => {
  /** A model written by hand: one box of the given size with a textured material (typed test data). */
  function modelOf(size: [number, number, number]) {
    const texture = new THREE.Texture();
    const material = new THREE.MeshBasicMaterial({ map: texture });
    const geometry = new THREE.BoxGeometry(...size);
    const model = new THREE.Group();
    model.add(new THREE.Mesh(geometry, material));
    return {
      model,
      disposed: () => ({ geometry: geometrySpy.mock.contexts.includes(geometry), material: materialSpy.mock.contexts.includes(material), texture: textureSpy.mock.contexts.includes(texture) }),
    };
  }
  let geometrySpy: ReturnType<typeof vi.spyOn>;
  let materialSpy: ReturnType<typeof vi.spyOn>;
  let textureSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    geometrySpy = vi.spyOn(THREE.BufferGeometry.prototype, 'dispose');
    materialSpy = vi.spyOn(THREE.Material.prototype, 'dispose');
    textureSpy = vi.spyOn(THREE.Texture.prototype, 'dispose');
  });
  const rendererStub = () => {
    const made: Array<{ render: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> }> = [];
    const makeRenderer = vi.fn(() => {
      const r = { setPixelRatio: vi.fn(), setSize: vi.fn(), setClearColor: vi.fn(), render: vi.fn(), dispose: vi.fn() };
      made.push(r);
      return r;
    });
    return { made, makeRenderer };
  };
  const ids = ['astronaut-emu', 'jwst', 'voyager-1', 'hubble', 'iss'];
  const sizes: Record<string, [number, number, number]> = { 'astronaut-emu': [1, 2, 0.7], jwst: [14, 12, 24], 'voyager-1': [16, 7, 11], hubble: [4, 13, 4], iss: [100, 30, 70] };
  const layer = () => container.querySelector('[data-testid="flight-craft"]')!;

  it('makes one renderer for the whole flight and loads each model once, a switch back drawing from the kept model', async () => {
    const { made, makeRenderer } = rendererStub();
    const models = Object.fromEntries(ids.map((id) => [id, modelOf(sizes[id]!)]));
    const load = vi.fn(async (url: string) => models[ids.find((id) => url === CRAFTS[id]!.modelUrl)!]!.model);
    const camera = flightCameraOf(there(), 1440, 900)!;
    for (const id of ['astronaut-emu', 'jwst', 'astronaut-emu']) {
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load, makeRenderer })));
      expect(layer().getAttribute('data-state')).toBe('drawn');
    }
    expect(makeRenderer).toHaveBeenCalledTimes(1);
    expect(load.mock.calls.map(([u]) => u)).toEqual([CRAFTS['astronaut-emu']!.modelUrl, CRAFTS.jwst!.modelUrl]);
    // The scene holds the suit alone again, drawn as it was the first time
    const scene = made[0]!.render.mock.calls.at(-1)![0] as THREE.Scene;
    const holder = scene.children[0] as THREE.Group;
    expect(holder.children).toEqual([models['astronaut-emu']!.model]);
    expect(made[0]!.dispose).not.toHaveBeenCalled();
  });

  it('draws a craft exactly as before: its own box, attitude and framing (the picture unchanged)', async () => {
    const { made, makeRenderer } = rendererStub();
    const m = modelOf(sizes['voyager-1']!);
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['voyager-1']!, load: async () => m.model, makeRenderer })));
    const scene = made[0]!.render.mock.calls.at(-1)![0] as THREE.Scene;
    const box = new THREE.Box3().setFromObject(m.model);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const size = box.getSize(new THREE.Vector3());
    const want = craftSceneOf(camera, CRAFTS['voyager-1']!, [sphere.center.x, sphere.center.y, sphere.center.z], sphere.radius, undefined, undefined, craftFrameRadiusM(CRAFTS['voyager-1']!, [size.x, size.y, size.z], sphere.radius))!;
    expect(Array.from((scene.children[0] as THREE.Group).matrix.elements)).toEqual(want.modelMatrix.map((x) => expect.closeTo(x, 9)));
  });

  it('keeps drawing the craft shown, as itself, while the next model loads', async () => {
    const { made, makeRenderer } = rendererStub();
    const emu = modelOf(sizes['astronaut-emu']!);
    let release: (g: THREE.Group) => void = () => {};
    const load = vi.fn((url: string) => (url === CRAFTS['astronaut-emu']!.modelUrl ? Promise.resolve(emu.model) : new Promise<THREE.Group>((r) => { release = r; })));
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    const before = Array.from(((made[0]!.render.mock.calls.at(-1)![0] as THREE.Scene).children[0] as THREE.Group).matrix.elements);
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(layer().getAttribute('data-state')).toBe('loading');
    // A new frame of the flight while Webb loads: the suit, still at its own attitude
    const moved = { ...camera, observerPc: [camera.observerPc[0] * 0.9, camera.observerPc[1] * 0.9, camera.observerPc[2] * 0.9] as typeof camera.observerPc };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera: moved, craft: CRAFTS.jwst!, load, makeRenderer })));
    const holder = (made[0]!.render.mock.calls.at(-1)![0] as THREE.Scene).children[0] as THREE.Group;
    expect(holder.children).toEqual([emu.model]);
    const box = new THREE.Box3().setFromObject(emu.model);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const size = box.getSize(new THREE.Vector3());
    const asSuit = craftSceneOf(moved, CRAFTS['astronaut-emu']!, [sphere.center.x, sphere.center.y, sphere.center.z], sphere.radius, undefined, undefined, craftFrameRadiusM(CRAFTS['astronaut-emu']!, [size.x, size.y, size.z], sphere.radius))!;
    expect(Array.from(holder.matrix.elements)).toEqual(asSuit.modelMatrix.map((x) => expect.closeTo(x, 9)));
    expect(before).not.toEqual(Array.from(holder.matrix.elements));
    await act(async () => release(modelOf(sizes.jwst!).model));
    expect(layer().getAttribute('data-state')).toBe('drawn');
  });

  it('frees the oldest model beyond the three it keeps, and every model, the loader and the renderer on leaving', async () => {
    expect(MODEL_CACHE_SIZE).toBe(3);
    const { made, makeRenderer } = rendererStub();
    const models = Object.fromEntries(ids.map((id) => [id, modelOf(sizes[id]!)]));
    const load = vi.fn(async (url: string) => models[ids.find((id) => url === CRAFTS[id]!.modelUrl)!]!.model);
    const camera = flightCameraOf(there(), 1440, 900)!;
    for (const id of ['astronaut-emu', 'jwst', 'voyager-1', 'hubble']) {
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load, makeRenderer })));
    }
    expect(models['astronaut-emu']!.disposed()).toEqual({ geometry: true, material: true, texture: true });
    for (const id of ['jwst', 'voyager-1', 'hubble']) expect(models[id]!.disposed()).toEqual({ geometry: false, material: false, texture: false });
    await act(async () => root.render(createElement('div')));
    await act(async () => await Promise.resolve());
    for (const id of ['jwst', 'voyager-1', 'hubble']) expect(models[id]!.disposed()).toEqual({ geometry: true, material: true, texture: true });
    expect(made[0]!.dispose).toHaveBeenCalledTimes(1);
    expect(models.iss!.disposed()).toEqual({ geometry: false, material: false, texture: false });
  });

  it('counts a switch back as the latest use: the model left longest goes first', async () => {
    const { makeRenderer } = rendererStub();
    const models = Object.fromEntries(ids.map((id) => [id, modelOf(sizes[id]!)]));
    const load = vi.fn(async (url: string) => models[ids.find((id) => url === CRAFTS[id]!.modelUrl)!]!.model);
    const camera = flightCameraOf(there(), 1440, 900)!;
    for (const id of ['astronaut-emu', 'jwst', 'astronaut-emu', 'voyager-1', 'hubble']) {
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load, makeRenderer })));
    }
    expect(models.jwst!.disposed()).toEqual({ geometry: true, material: true, texture: true });
    expect(models['astronaut-emu']!.disposed()).toEqual({ geometry: false, material: false, texture: false });
    expect(load).toHaveBeenCalledTimes(4);
  });
});

