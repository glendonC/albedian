// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightCraftLayer } from '../FlightCraftLayer.js';
import { CRAFTS } from '../crafts.js';
import { flightCameraOf } from '../flightFrame.js';

// craft-compile-one (DECIDED-BY-LEAD 22:31): a craft's first load never stops the page: what a new model needs on the
// GPU is made before it is shown, its programs compiled by the renderer's own asynchronous compile and its textures
// uploaded one a frame, and only then is it framed in.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

/** A model with two textured meshes. */
function texturedModel() {
  const model = new THREE.Group();
  const textures = [new THREE.Texture(), new THREE.Texture(), new THREE.Texture()];
  model.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ map: textures[0], normalMap: textures[1] })));
  model.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), new THREE.MeshStandardMaterial({ map: textures[2] })));
  return { model, textures };
}

let container: HTMLDivElement;
let root: Root;
let frames: (() => void)[];
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => (frames.push(fn), frames.length));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const runFrame = async () => {
  const run = frames;
  frames = [];
  await act(async () => {
    for (const fn of run) fn();
    await Promise.resolve();
  });
};

function readyRenderer() {
  let resolveCompile: () => void = () => undefined;
  const r = {
    setPixelRatio: vi.fn(),
    setSize: vi.fn(),
    setClearColor: vi.fn(),
    dispose: vi.fn(),
    clear: vi.fn(),
    render: vi.fn(),
    compileAsync: vi.fn(() => new Promise<void>((ok) => (resolveCompile = ok))),
    initTexture: vi.fn(),
  };
  return { r, compiled: () => resolveCompile() };
}

describe('FlightCraftLayer: a new craft made ready before it is shown', () => {
  it('compiles the model against the flight scene, uploads its textures one a frame, and only then draws and frames it in', async () => {
    const { r, compiled } = readyRenderer();
    const { model, textures } = texturedModel();
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: async () => model, makeRenderer: () => r })));
    const layer = () => container.querySelector('[data-testid="flight-craft"]')!;
    // Asked to compile the model itself, against the scene its lights and environment are in
    expect(r.compileAsync).toHaveBeenCalledTimes(1);
    const [compiledWhat, , target] = r.compileAsync.mock.calls[0] as unknown as [THREE.Object3D, THREE.Camera, THREE.Scene];
    expect(compiledWhat).toBe(model);
    expect(target).toBeInstanceOf(THREE.Scene);
    expect(r.render).not.toHaveBeenCalled();
    expect(layer().getAttribute('data-state')).toBe('loading');
    // Frames pass while the compile runs: nothing goes up and nothing is drawn until it is done
    await runFrame();
    await runFrame();
    expect(r.initTexture).not.toHaveBeenCalled();
    expect(r.render).not.toHaveBeenCalled();
    await act(async () => compiled());
    // One texture a frame, nothing drawn meanwhile
    for (let i = 0; i < textures.length; i++) {
      await runFrame();
      expect(r.initTexture).toHaveBeenCalledTimes(i + 1);
      expect(r.render).not.toHaveBeenCalled();
    }
    expect(new Set(r.initTexture.mock.calls.map(([t]) => t))).toEqual(new Set(textures));
    await runFrame();
    expect(r.render).toHaveBeenCalled();
    expect(layer().getAttribute('data-state')).toBe('drawn');
  });

  it('draws a model it already holds again at once, with nothing to make ready', async () => {
    const { r, compiled } = readyRenderer();
    const { model } = texturedModel();
    const other = texturedModel().model;
    const camera = flightCameraOf(there(), 1440, 900)!;
    const load = vi.fn(async (url: string) => (url.includes('emu') ? model : other));
    // One renderer maker for the whole flight, as the page gives it
    const make = () => r;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer: make })));
    await act(async () => compiled());
    for (let i = 0; i < 6; i++) await runFrame();
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer: make })));
    await act(async () => compiled());
    for (let i = 0; i < 6; i++) await runFrame();
    const asked = r.compileAsync.mock.calls.length;
    const uploads = r.initTexture.mock.calls.length;
    const framesBefore = frames.length;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer: make })));
    // No animation frame is run: only the promises settle
    await act(async () => await new Promise((done) => setTimeout(done, 0)));
    expect(frames.length).toBe(framesBefore);
    expect(container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-state')).toBe('drawn');
    expect(r.compileAsync.mock.calls.length).toBe(asked);
    expect(r.initTexture.mock.calls.length).toBe(uploads);
  });

  it('shows the model at once with a renderer that has no asynchronous compile (a stand-in)', async () => {
    const r = { setPixelRatio: vi.fn(), setSize: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(), clear: vi.fn(), render: vi.fn() };
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: async () => texturedModel().model, makeRenderer: () => r })));
    expect(container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-state')).toBe('drawn');
  });
});
