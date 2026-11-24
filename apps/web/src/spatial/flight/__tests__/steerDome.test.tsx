// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';

// milkyway-flight-one on steer-frame-one: while the person steers, the starless glow's dome is drawn in every frame the
// one frame loop draws, before the stars and through that frame's camera, so the Milky Way turns with the camera.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8');

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

/** A WebGL 2 stand-in: says yes to every step and records, in order, each dome draw's camera look and each star pass. */
function recordingGl() {
  const events: Array<{ kind: 'dome'; look: number[] } | { kind: 'stars' }> = [];
  let domeNext = false;
  let n = 0;
  const gl = new Proxy({} as Record<string, unknown>, {
    get: (_t, p) => {
      if (p === 'getUniformLocation') return (_program: unknown, name: string) => ({ name });
      if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
      if (p === 'getError') return () => 0;
      if (p === 'NO_ERROR') return 0;
      // The dome sets its panorama rows, then the camera's rows; the star pass sets its limit
      if (p === 'uniformMatrix3fv') return (loc: { name: string }) => { if (loc.name === 'uIcrsToPanorama') domeNext = true; };
      if (p === 'uniform3fv') return (loc: { name: string }, v: ArrayLike<number>) => {
        if (domeNext && loc.name === 'uRowZ') {
          events.push({ kind: 'dome', look: Array.from(v) });
          domeNext = false;
        }
      };
      if (p === 'uniform1f') return (loc: { name: string }) => { if (loc.name === 'uLimit') events.push({ kind: 'stars' }); };
      if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
      if (typeof p === 'string' && p.startsWith('create')) return () => ({ id: ++n });
      return () => {};
    },
  });
  return { gl, events };
}

/** A core tile written by hand (typed test data, not read from any catalog release). */
const tile: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(83, 4)),
  magnitude: new Float32Array([2]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([120]), flags: new Uint8Array([1]), byteLength: 0,
};

class LoadingImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = '';
  set src(_url: string) {
    setTimeout(() => this.onload?.(), 0);
  }
}

let container: HTMLDivElement;
let root: Root;
let frames: FrameRequestCallback[];
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
  vi.stubGlobal('fetch', vi.fn(async () => new Response(registration, { status: 200 })));
  vi.stubGlobal('Image', LoadingImage);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the Milky Way while steering, drawn by the one frame loop', () => {
  it('draws the dome before the stars in every steering frame, turned with that frame camera', async () => {
    const { gl, events } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const onSkyDome = vi.fn();
    const session = there();
    const t0 = 10_000;
    const clock = vi.spyOn(performance, 'now').mockReturnValue(t0);
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering: true, onSkyDome })));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(tile));
    // The glow and the registration load, the dome is made and the page is told it is drawn
    await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(onSkyDome).toHaveBeenLastCalledWith(true);
    const before = events.length;
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
    const ticks = 30;
    for (let i = 1; i <= ticks; i++) {
      const cb = frames.shift()!;
      await act(async () => cb(t0 + i * (1000 / 60)));
    }
    clock.mockRestore();
    const drawn = events.slice(before);
    // One dome and then one star pass a frame
    expect(drawn.map((e) => e.kind)).toEqual(Array.from({ length: ticks }, () => ['dome', 'stars']).flat());
    // The dome's look follows the turning camera
    const looks = drawn.flatMap((e) => (e.kind === 'dome' ? [e.look] : []));
    expect(Math.hypot(...[0, 1, 2].map((k) => looks.at(-1)![k]! - looks[0]![k]!))).toBeGreaterThan(1e-3);
  });

  it('draws no dome once steering stops', async () => {
    const { gl, events } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const onSkyDome = vi.fn();
    const session = there();
    const tree = (steering: boolean) => createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering, onSkyDome });
    await act(async () => root.render(tree(true)));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(tile));
    await act(async () => await new Promise((r) => setTimeout(r, 20)));
    expect(onSkyDome).toHaveBeenLastCalledWith(true);
    await act(async () => root.render(tree(false)));
    expect(onSkyDome).toHaveBeenLastCalledWith(false);
    const after = events.length;
    // A frame drawn for a new size, and one for a tile that arrives later: neither draws the dome
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1400, viewportHeightPx: 900, openTiles, steering: false, onSkyDome })));
    await act(async () => deliver!({ ...tile, pix: 2 }));
    const drawn = events.slice(after);
    expect(drawn.filter((e) => e.kind === 'stars').length).toBeGreaterThanOrEqual(2);
    expect(drawn.some((e) => e.kind === 'stars')).toBe(true);
    expect(drawn.some((e) => e.kind === 'dome')).toBe(false);
  });
});
