// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync } from 'node:fs';
import { dirname, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
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
import { steerStartOf } from '../starView.js';

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
    // Each render as it was drawn: the models in the scene's holder and its matrix at that moment
    const drawn: Array<{ children: THREE.Object3D[]; matrix: number[] }> = [];
    const made: Array<{ render: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn>; clear: ReturnType<typeof vi.fn> }> = [];
    const makeRenderer = vi.fn(() => {
      const r = {
        setPixelRatio: vi.fn(), setSize: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(), clear: vi.fn(),
        render: vi.fn((scene: THREE.Scene) => {
          const holder = scene.children[0] as THREE.Group;
          drawn.push({ children: [...holder.children], matrix: Array.from(holder.matrix.elements) });
        }),
      };
      made.push(r);
      return r;
    });
    return { made, makeRenderer, drawn };
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

  it('keeps drawing the outgoing craft, as itself, while the next model loads (section eight: into its own canvas)', async () => {
    const { made, makeRenderer, drawn } = rendererStub();
    const emu = modelOf(sizes['astronaut-emu']!);
    let release: (g: THREE.Group) => void = () => {};
    const load = vi.fn((url: string) => (url === CRAFTS['astronaut-emu']!.modelUrl ? Promise.resolve(emu.model) : new Promise<THREE.Group>((r) => { release = r; })));
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    const before = drawn.at(-1)!.matrix;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(layer().getAttribute('data-state')).toBe('loading');
    // A new frame of the flight while Webb loads: the suit, still at its own attitude
    const moved = { ...camera, observerPc: [camera.observerPc[0] * 0.9, camera.observerPc[1] * 0.9, camera.observerPc[2] * 0.9] as typeof camera.observerPc };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera: moved, craft: CRAFTS.jwst!, load, makeRenderer })));
    const last = drawn.at(-1)!;
    expect(last.children).toEqual([emu.model]);
    // and the canvas of the craft shown is cleared, nothing drawn there until Webb's model is ready
    expect(made[0]!.clear).toHaveBeenCalled();
    const holder = { matrix: { elements: last.matrix } };
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

  it('never frees the model on screen: three switches inside one model load leave the shown one kept (craft-cache-pin-one)', async () => {
    const { made, makeRenderer, drawn } = rendererStub();
    const suit = modelOf(sizes['astronaut-emu']!);
    const later: Record<string, (g: THREE.Group) => void> = {};
    const load = vi.fn((url: string) =>
      url === CRAFTS['astronaut-emu']!.modelUrl ? Promise.resolve(suit.model) : new Promise<THREE.Group>((r) => { later[url] = r; })
    );
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    // Webb, Voyager and Hubble asked for while none of their models has come: the suit is still the one on screen
    for (const id of ['jwst', 'voyager-1', 'hubble']) {
      await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS[id]!, load, makeRenderer })));
    }
    expect(suit.disposed()).toEqual({ geometry: false, material: false, texture: false });
    // and it is still drawn as itself on the next frame
    const moved = { ...camera, observerPc: [camera.observerPc[0] * 0.9, camera.observerPc[1] * 0.9, camera.observerPc[2] * 0.9] as typeof camera.observerPc };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera: moved, craft: CRAFTS.hubble!, load, makeRenderer })));
    expect(drawn.at(-1)!.children).toEqual([suit.model]);
    // Webb, the oldest not on screen, went instead (the cache keeps its size). Off screen now (Hubble drawn, the suit's
    // frame-out ended), the suit is the oldest again: the next new model frees it, while Hubble, framing out, is kept
    const hubble = modelOf(sizes.hubble!);
    await act(async () => later[CRAFTS.hubble!.modelUrl]!(hubble.model));
    const out = container.querySelector('[data-testid="flight-craft-out"]');
    if (out) await act(async () => out.dispatchEvent(new Event('animationend', { bubbles: true })));
    expect(suit.disposed()).toEqual({ geometry: false, material: false, texture: false });
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.iss!, load, makeRenderer })));
    await act(async () => await Promise.resolve());
    expect(suit.disposed()).toEqual({ geometry: true, material: true, texture: true });
    expect(hubble.disposed()).toEqual({ geometry: false, material: false, texture: false });
    expect(made[0]!.dispose).not.toHaveBeenCalled();
  });
});

describe('FlightCraftLayer: switching craft (craft-switch-motion-1, MOTION-READY-web.md section eight)', () => {
  const rendererStub = () => {
    const drawn: Array<{ children: THREE.Object3D[] }> = [];
    const makeRenderer = vi.fn(() => ({
      setPixelRatio: vi.fn(), setSize: vi.fn(), setClearColor: vi.fn(), dispose: vi.fn(), clear: vi.fn(),
      render: vi.fn((scene: THREE.Scene) => drawn.push({ children: [...(scene.children[0] as THREE.Group).children] })),
    }));
    return { makeRenderer, drawn };
  };
  const box = (s: number) => {
    const g = new THREE.Group();
    g.add(new THREE.Mesh(new THREE.BoxGeometry(s, s, s), new THREE.MeshBasicMaterial()));
    return g;
  };
  const layer = () => container.querySelector('[data-testid="flight-craft"]')!;
  const out = () => container.querySelector('[data-testid="flight-craft-out"]');
  const inFrame = () => container.querySelector('.flight-craft__in')!;

  it('frames the outgoing craft out from the press, still drawn from the camera, the incoming in from its first drawn frame, overlapping', async () => {
    const { makeRenderer, drawn } = rendererStub();
    const suit = box(2);
    const webb = box(20);
    let release: (g: THREE.Group) => void = () => {};
    const load = vi.fn((url: string) => (url === CRAFTS['astronaut-emu']!.modelUrl ? Promise.resolve(suit) : new Promise<THREE.Group>((r) => { release = r; })));
    const copies: unknown[] = [];
    const ctx2d = { clearRect: vi.fn(), drawImage: vi.fn((src: unknown) => copies.push(src)) };
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(((kind: string) => (kind === '2d' ? ctx2d : null)) as never);
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    // The first craft frames in from its first drawn frame too
    expect(inFrame().classList.contains('is-entering')).toBe(true);
    expect(out()).toBeNull();
    // The press: the suit frames out at once, before Webb's model has come
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(out()!.className).toBe('flight-craft__out');
    expect(layer().getAttribute('data-state')).toBe('loading');
    // Drawn from the camera each frame while it goes, copied out of the craft canvas
    const frames = drawn.length;
    const moved = { ...camera, observerPc: [camera.observerPc[0] * 0.9, camera.observerPc[1] * 0.9, camera.observerPc[2] * 0.9] as typeof camera.observerPc };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera: moved, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(drawn.length).toBeGreaterThan(frames);
    expect(drawn.at(-1)!.children).toEqual([suit]);
    expect(copies.at(-1)).toBe(container.querySelector('.flight-craft__in canvas'));
    // Webb arrives: it frames in from that frame while the suit is still going (neither waits)
    inFrame().classList.remove('is-entering');
    await act(async () => release(webb));
    expect(layer().getAttribute('data-state')).toBe('drawn');
    expect(inFrame().classList.contains('is-entering')).toBe(true);
    expect(out()).not.toBeNull();
    expect(drawn.at(-1)!.children).toEqual([webb]);
    // The suit's frame-out ends: its canvas goes and it is drawn no more
    await act(async () => out()!.dispatchEvent(new Event('animationend', { bubbles: true })));
    expect(out()).toBeNull();
    // The next frame of the flight draws Webb alone
    const before = drawn.length;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load, makeRenderer })));
    expect(drawn.slice(before).map((d) => d.children)).toEqual([[webb]]);
  });

  it('frames the craft out when Your view is picked, loading nothing', async () => {
    const { makeRenderer } = rendererStub();
    const load = vi.fn(async () => box(2));
    const camera = flightCameraOf(there(), 1440, 900)!;
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load, makeRenderer })));
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: null, load, makeRenderer })));
    expect(out()).not.toBeNull();
    expect(layer().getAttribute('data-craft')).toBe('none');
    expect(load).toHaveBeenCalledTimes(1);
    // Picked while the next model was still on its way: Your view has nothing to load, so nothing waits
    const slow = vi.fn((url: string) => (url === CRAFTS['astronaut-emu']!.modelUrl ? Promise.resolve(box(2)) : new Promise<THREE.Group>(() => {})));
    await act(async () => root.render(createElement('div')));
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: slow, makeRenderer })));
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS.jwst!, load: slow, makeRenderer })));
    expect(layer().getAttribute('data-state')).toBe('loading');
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: null, load: slow, makeRenderer })));
    expect(layer().getAttribute('data-state')).toBe('drawn');
  });

  it('keeps the craft layer in the flight after a craft was shown, so Your view frames it out', async () => {
    const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: CRAFTS['astronaut-emu']! })));
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft: null })));
    expect(layer().getAttribute('data-craft')).toBe('none');
  });

  it('times both by the record\'s own tokens: frame out and frame in, no rise and no scale, the dissolve under Reduce Motion', () => {
    const css = readFileSync(resolvePath(dirname(fileURLToPath(import.meta.url)), '../../../styles/motion.css'), 'utf8');
    const rule = (selector: string) => css.slice(css.indexOf(`${selector} {`), css.indexOf('}', css.indexOf(`${selector} {`)));
    expect(rule('.flight-craft__out')).toContain('animation: look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) both;');
    expect(rule('.flight-craft__in.is-entering')).toContain('animation: look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both;');
    // The picker: the laptop list as a card, the phone sheet as a card's sheet
    expect(rule('.craft-picker__panel')).toContain('animation: look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both;');
    expect(rule('.craft-picker__panel .craft-picker__line')).toContain('look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger) * var(--i, 0))');
    expect(rule('.craft-picker__panel.is-leaving')).toContain('look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) calc(var(--look-mo-stagger) * var(--n, 0) + var(--look-mo-dur-words-out))');
    expect(rule('.craft-picker__panel.is-leaving .craft-picker__line')).toContain('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * var(--o, 0))');
    expect(rule('.craft-picker__sheet')).toContain('animation: look-mo-sheet-in var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both;');
    expect(rule('.craft-picker__sheet.is-leaving')).toContain('animation: look-mo-sheet-out var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both;');
    // Reduce Motion: the list's words and panel together (the stagger token, nought there); only the sheet's fill and hairline dissolve
    const reduced = css.slice(css.indexOf('.craft-picker__panel.is-leaving {\n    animation-delay: var(--look-mo-stagger);'));
    expect(reduced.startsWith('.craft-picker__panel.is-leaving {\n    animation-delay: var(--look-mo-stagger);')).toBe(true);
    expect(reduced).toContain('.craft-picker__sheet {\n    animation: reveal-sheet-fill-in var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both;');
    expect(reduced).toContain('.craft-picker__sheet.is-leaving {\n    animation: reveal-sheet-fill-out var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both;');
  });

  it('draws a steering frame when the star layer\'s loop asks, through its draw hook (steer-frame-one)', async () => {
    const { makeRenderer, drawn } = rendererStub();
    const suit = box(2);
    const camera = flightCameraOf(there(), 1440, 900)!;
    const drawRef: { current: ((s: { positionPc: [number, number, number]; rows: ReturnType<typeof steerStartOf>['rows'] }) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightCraftLayer, { camera, craft: CRAFTS['astronaut-emu']!, load: async () => suit, makeRenderer, drawRef })));
    const before = drawn.length;
    const start = steerStartOf(camera);
    drawRef.current!({ positionPc: start.positionPc as [number, number, number], rows: start.rows });
    expect(drawn.length).toBe(before + 1);
    expect(drawn.at(-1)!.children).toEqual([suit]);
    await act(async () => root.render(createElement('div')));
    expect(drawRef.current).toBeNull();
  });
});

