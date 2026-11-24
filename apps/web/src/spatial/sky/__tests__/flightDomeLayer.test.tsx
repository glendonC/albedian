// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Vec3 } from '@albedian/camera-rig';
import { FlightDomeLayer, type DomeView } from '../FlightDomeLayer.js';
import { FLIGHT_MILKY_WAY_URL } from '../flightMilkyWay.js';

// The steering dome in a canvas of its own: the glow loaded once into this canvas's context, drawn through each camera
// it is given (by the page or by the star layer's frame loop), its state told, everything freed as it leaves.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8');

/** A WebGL 2 stand-in: says yes to every step, records each dome draw's camera look and what is freed. */
function recordingGl() {
  const looks: number[][] = [];
  const freed: string[] = [];
  let n = 0;
  const gl = new Proxy({} as Record<string, unknown>, {
    get: (_t, p) => {
      if (p === 'getUniformLocation') return (_program: unknown, name: string) => ({ name });
      if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
      if (p === 'uniform3fv') return (loc: { name: string }, v: ArrayLike<number>) => { if (loc.name === 'uRowZ') looks.push(Array.from(v)); };
      if (p === 'deleteProgram' || p === 'deleteTexture' || p === 'deleteVertexArray') return () => freed.push(String(p));
      if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
      if (typeof p === 'string' && p.startsWith('create')) return () => ({ id: ++n });
      return () => {};
    },
  });
  return { gl, looks, freed };
}

const asked: string[] = [];
class LoadingImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = '';
  set src(url: string) {
    asked.push(url);
    setTimeout(() => this.onload?.(), 0);
  }
}

function viewLooking(z: Vec3): DomeView {
  return { rows: { x: [1, 0, 0], y: [0, 1, 0], z }, crpixX: 960, crpixY: 540, scaleRadPerPx: 0.0005, k1: 0, k2: 0, storedToScreen: [0.75, 0, 0, 0, 0.75, 0], viewportWidthPx: 1440, viewportHeightPx: 900 };
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  asked.length = 0;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(registration, { status: 200 })));
  vi.stubGlobal('Image', LoadingImage);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('FlightDomeLayer', () => {
  it('loads the glow into its own canvas and draws it through the camera it is given, then through each frame\'s', async () => {
    const { gl, looks } = recordingGl();
    const contexts: HTMLCanvasElement[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      contexts.push(this);
      return gl as never;
    } as never);
    const onDrawn = vi.fn();
    const drawRef: { current: ((v: DomeView) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightDomeLayer, { view: viewLooking([0, 0, 1]), viewportWidthPx: 1440, viewportHeightPx: 900, drawRef, onDrawn })));
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    const el = container.querySelector<HTMLCanvasElement>('[data-testid="flight-dome"]')!;
    expect(contexts).toEqual([el]);
    expect(asked).toEqual([FLIGHT_MILKY_WAY_URL]);
    expect(el.getAttribute('data-state')).toBe('drawn');
    expect(el.getAttribute('aria-hidden')).toBe('true');
    expect(onDrawn).toHaveBeenLastCalledWith(true);
    expect(looks.at(-1)).toEqual([0, 0, 1]);
    // The frame loop's draw, through that frame's camera
    act(() => drawRef.current!(viewLooking([0, 1, 0])));
    expect(looks.at(-1)).toEqual([0, 1, 0]);
    // A camera from the page draws too
    await act(async () => root.render(createElement(FlightDomeLayer, { view: viewLooking([1, 0, 0]), viewportWidthPx: 1440, viewportHeightPx: 900, drawRef, onDrawn })));
    expect(looks.at(-1)).toEqual([1, 0, 0]);
  });

  it('frees its program, its two textures and its triangle, and says it is gone, as it leaves', async () => {
    const { gl, freed } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    const onDrawn = vi.fn();
    const drawRef: { current: ((v: DomeView) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightDomeLayer, { view: viewLooking([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844, drawRef, onDrawn })));
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    await act(async () => root.render(createElement('div')));
    expect(freed.sort()).toEqual(['deleteProgram', 'deleteTexture', 'deleteTexture', 'deleteVertexArray']);
    expect(onDrawn).toHaveBeenLastCalledWith(false);
    expect(drawRef.current).toBeNull();
  });

  it('says it failed, drawing nothing, where WebGL 2 is not there', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const onDrawn = vi.fn();
    await act(async () => root.render(createElement(FlightDomeLayer, { view: viewLooking([0, 0, 1]), viewportWidthPx: 1440, viewportHeightPx: 900, onDrawn })));
    expect(container.querySelector('[data-testid="flight-dome"]')!.getAttribute('data-state')).toBe('failed');
    expect(asked).toEqual([]);
    expect(onDrawn).not.toHaveBeenCalledWith(true);
  });
});
