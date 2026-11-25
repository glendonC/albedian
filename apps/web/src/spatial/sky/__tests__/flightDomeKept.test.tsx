// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Vec3 } from '@albedian/camera-rig';
import { FlightDomeLayer, type DomeView } from '../FlightDomeLayer.js';
import { FLIGHT_DOME_FRAGMENT, FLIGHT_MILKY_WAY_URL } from '../flightMilkyWay.js';
import { forgetKeptSkyPictures, prepareSkyPicture } from '../skyPicture.js';
import { elementGl, installSkyFakes, named } from './skyPictureFakes.js';

// realsky-upload-one (LEAD ANSWER 19:21): where the page keeps sky pictures, the steering dome is drawn from the kept glow
// through the kept context: the first steer decodes, uploads and links nothing, and the dome's program is made once a
// page however often the person steers.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8');

const view = (z: Vec3): DomeView => ({ rows: { x: [1, 0, 0], y: [0, 1, 0], z }, crpixX: 195, crpixY: 422, scaleRadPerPx: 0.0005, k1: 0, k2: 0, storedToScreen: [1, 0, 0, 0, 1, 0], viewportWidthPx: 390, viewportHeightPx: 844 });

let container: HTMLDivElement;
let root: Root;
let transfers: unknown[];
let images: number;
beforeEach(() => {
  forgetKeptSkyPictures();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  transfers = [];
  images = 0;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
    if (type === 'bitmaprenderer') return { transferFromImageBitmap: (b: unknown) => transfers.push(b) } as unknown as RenderingContext;
    return null;
  } as typeof HTMLCanvasElement.prototype.getContext);
  vi.stubGlobal('Image', class { set src(_u: string) { images += 1; } });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

const state = () => container.querySelector('[data-testid="flight-dome"]')?.getAttribute('data-state');

describe('the steering dome on the kept glow', () => {
  it('draws from the glow kept at rest, with no image, no upload and its program made once across steers', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration, pictureRows: 300 });
    prepareSkyPicture(FLIGHT_MILKY_WAY_URL);
    await act(async () => await fakes.frame(5));
    const uploads = named(fakes.calls, 'texSubImage2D').length;
    expect(uploads).toBe(2);

    const drawRef: { current: ((v: DomeView) => void) | null } = { current: null };
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844, drawRef })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(state()).toBe('drawn');
    expect(transfers).toHaveLength(1);
    // The frame loop's draws each hand a frame over
    act(() => drawRef.current!(view([0, 1, 0])));
    act(() => drawRef.current!(view([1, 0, 0])));
    expect(transfers).toHaveLength(3);

    // A second steer: the same kept dome
    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844 })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(state()).toBe('drawn');
    expect(transfers).toHaveLength(4);

    expect(images).toBe(0);
    expect(named(fakes.calls, 'texSubImage2D')).toHaveLength(uploads);
    expect(fakes.calls.filter((c) => c.name === 'texImage2D' && c.args[2] === 'RGB')).toHaveLength(0);
    expect(named(fakes.calls, 'shaderSource').filter((c) => c.args[1] === FLIGHT_DOME_FRAGMENT)).toHaveLength(1);
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(4);
    // The kept dome is the page's: nothing of it is freed as a steer ends
    expect(named(fakes.calls, 'deleteProgram')).toHaveLength(0);
    expect(named(fakes.calls, 'deleteTexture')).toHaveLength(0);
  });

  it('where pictures cannot be kept, uploads the glow into its own context from the image, flipped, as before', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration });
    vi.stubGlobal('OffscreenCanvas', undefined);
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(function (this: HTMLCanvasElement, type: string) {
      return (type === 'webgl2' ? elementGl(fakes, this) : null) as unknown as RenderingContext;
    } as typeof HTMLCanvasElement.prototype.getContext);
    vi.stubGlobal('Image', class { onload: (() => void) | null = null; set src(_u: string) { images += 1; setTimeout(() => this.onload?.(), 0); } });
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844 })));
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    expect(state()).toBe('drawn');
    expect(images).toBe(1);
    const glow = fakes.calls.filter((c) => c.name === 'texImage2D' && c.args[2] === 'RGB');
    expect(glow).toHaveLength(1);
    const flips = named(fakes.calls, 'pixelStorei').filter((c) => c.args[0] === 'UNPACK_FLIP_Y_WEBGL');
    expect(flips.map((c) => c.args[1])).toEqual([true, false]);
    expect(named(fakes.calls, 'generateMipmap')).toHaveLength(1);
    // Into a texture of its own, not the offset table's
    const boundBefore = (name: string, format: string) => {
      const at = fakes.calls.findIndex((c) => c.name === name && c.args[2] === format);
      return fakes.calls.slice(0, at).filter((c) => c.name === 'bindTexture').at(-1)!.args[1];
    };
    expect(boundBefore('texImage2D', 'RGB')).not.toBe(boundBefore('texImage2D', 'RG8'));
    expect(transfers).toHaveLength(0);
  });

  it('says it failed when its canvas refuses the kept frame', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration, pictureRows: 200 });
    prepareSkyPicture(FLIGHT_MILKY_WAY_URL);
    await act(async () => await fakes.frame(4));
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation((() => null) as typeof HTMLCanvasElement.prototype.getContext);
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844 })));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(state()).toBe('failed');
  });

  it('uploads the glow again into a new context, and draws, when the kept one was lost before the steer', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration, pictureRows: 200 });
    prepareSkyPicture(FLIGHT_MILKY_WAY_URL);
    await act(async () => await fakes.frame(4));
    fakes.contexts[0]!.lost = true;
    await act(async () => root.render(createElement(FlightDomeLayer, { view: view([0, 0, 1]), viewportWidthPx: 390, viewportHeightPx: 844 })));
    for (let i = 0; i < 10 && state() !== 'drawn'; i++) await act(async () => await fakes.frame());
    expect(state()).toBe('drawn');
    expect(transfers).toHaveLength(1);
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(2);
  });
});
