// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import type { CraftRenderer } from '../FlightCraftLayer.js';
import { CRAFTS } from '../crafts.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

// craft-alive-held-keys (LEAD ANSWER 06:26): VIDEO's recording showed the suit facing us, upright, while a key was held,
// and from behind only between keys. Each render of the star layer handed the craft layer the hand-over's start with no
// signals, and the craft layer drew that: every readout change while a key is held drew the rest pose. Rendered here
// with a craft, as the mutant that lived asked: the pose drawn last after a render while steering is the chase's.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

/** A WebGL stand-in for the star layer: every call answers and draws nothing. */
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

const makeCraftRenderer = (): CraftRenderer => ({ setPixelRatio: () => {}, setSize: () => {}, setClearColor: () => {}, clear: () => {}, dispose: () => {}, render: () => {} });
const craftLoad = async () => {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 0.5), new THREE.MeshStandardMaterial()));
  return g;
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

/** Runs the queued frames, a sixtieth of a second apart, `rounds` times over. */
async function tick(rounds: number) {
  for (let r = 0; r < rounds; r++) {
    now += 1000 / 60;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
    await act(async () => await Promise.resolve());
  }
}

describe('the craft while a key is held (craft-alive-held-keys)', () => {
  it('keeps the chase\'s pose through every render of the star layer while steering', async () => {
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(tile);
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    const tree = (steering: boolean, onSteer = () => {}) =>
      createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering, onSteer, craft: CRAFTS['astronaut-emu']!, craftLoad, makeCraftRenderer });
    await act(async () => root.render(tree(false)));
    await tick(12);
    const layer = () => container.querySelector('[data-testid="flight-craft"]')!;
    // At rest the craft stands as its picture shows it
    expect(layer().getAttribute('data-pose')).toBe('rest');
    await act(async () => root.render(tree(true)));
    // W held and the left arrow with it, as VIDEO's keys did
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', key: 'w' })));
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    await tick(20);
    expect(layer().getAttribute('data-pose')).toBe('chase');
    // A render of the star layer while the keys are held (the readout's numbers change, the page renders): the craft
    // layer draws again, and it must draw the chase, not the hand-over's start at rest
    await act(async () => root.render(tree(true, () => {})));
    expect(layer().getAttribute('data-pose')).toBe('chase');
    await tick(3);
    await act(async () => root.render(tree(true, () => {})));
    expect(layer().getAttribute('data-pose')).toBe('chase');
  });
});
