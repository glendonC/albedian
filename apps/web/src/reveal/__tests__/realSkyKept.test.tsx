// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { REAL_SKY_PANORAMA_URL, RealSkyLayer, prepareRealSky } from '../RealSkyLayer.js';
import { forgetKeptSkyPictures } from '../../spatial/sky/skyPicture.js';
import { forgetPanoramaRegistration } from '../../spatial/sky/skyRegistration.js';
import { FLIGHT_DOME_FRAGMENT, FLIGHT_MILKY_WAY_URL } from '../../spatial/sky/flightMilkyWay.js';
import { elementGl, installSkyFakes, named } from '../../spatial/sky/__tests__/skyPictureFakes.js';

// realsky-upload-one (LEAD ANSWER 18:27): Real sky's panorama is decoded off the main thread and uploaded once, ahead of
// any motion where it can be, and kept across the layer's mounts (Real sky, There, the end of steering); no draw waits
// for the GPU.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../public/textures/eso0932a_registration.json'), 'utf8');
const session = poseSession(sample('plate_calibrated') as ObservationSolution);

let container: HTMLDivElement;
let root: Root;
let transfers: unknown[][];
beforeEach(() => {
  forgetKeptSkyPictures();
  forgetPanoramaRegistration();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  transfers = [];
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

const layer = () => createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 390, viewportHeightPx: 844, phone: true });
const state = () => container.querySelector('[data-testid="real-sky"]')?.getAttribute('data-state');

function stubCanvases(fakes: ReturnType<typeof installSkyFakes>) {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
    if (type === 'bitmaprenderer') return { transferFromImageBitmap: (b: unknown) => transfers.push([this, b]) } as unknown as RenderingContext;
    if (type === 'webgl2') return elementGl(fakes, this) as unknown as RenderingContext;
    return null;
  } as typeof HTMLCanvasElement.prototype.getContext);
}

/** Frames until the photograph's canvas is handed a frame from a new kept context (after a loss). */
async function untilDrawnAgain(fakes: ReturnType<typeof installSkyFakes>) {
  const before = transfers.length;
  for (let i = 0; i < 40 && transfers.length === before; i++) await act(async () => await fakes.frame());
}

async function untilDrawn(fakes: ReturnType<typeof installSkyFakes>) {
  for (let i = 0; i < 40 && state() !== 'drawn'; i++) await act(async () => await fakes.frame());
}

describe('Real sky keeps its panorama across mounts', () => {
  it('uploads the panorama once for every mount, in slices, and draws each mount from it with no wait for the GPU', async () => {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, pictureRows: 600 });
    stubCanvases(fakes);
    await act(async () => root.render(layer()));
    await untilDrawn(fakes);
    expect(state()).toBe('drawn');
    const first = container.querySelector('canvas.real-sky__canvas');
    expect(transfers.map(([c]) => c)).toEqual([first]);

    // Leaving and coming back (Real sky off and on, There, steering's end): drawn again from the kept texture
    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(layer()));
    await untilDrawn(fakes);
    expect(state()).toBe('drawn');
    const second = container.querySelector('canvas.real-sky__canvas');
    expect(second).not.toBe(first);
    expect(transfers.map(([c]) => c)).toEqual([first, second]);

    expect(fakes.fetched.filter((u) => u === REAL_SKY_PANORAMA_URL)).toHaveLength(1);
    expect(named(fakes.calls, 'texStorage2D')).toHaveLength(1);
    expect(named(fakes.calls, 'texSubImage2D')).toHaveLength(3);
    expect(named(fakes.calls, 'texImage2D')).toHaveLength(0);
    expect(named(fakes.calls, 'getError')).toHaveLength(0);
    expect(named(fakes.calls, 'drawElements')).toHaveLength(2);
    // One context for the page; the layer's own canvases only show its frames
    expect(fakes.contexts).toHaveLength(1);
    // Each draw lets its grid's buffers go, so the kept context gathers none across mounts
    expect(named(fakes.calls, 'createBuffer')).toHaveLength(6);
    expect(named(fakes.calls, 'deleteBuffer')).toHaveLength(6);
  });

  it('paints only the newest draw when the window changes while the panorama is still going up', async () => {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, pictureRows: 600 });
    stubCanvases(fakes);
    await act(async () => root.render(layer()));
    await act(async () => await fakes.frame());
    await act(async () => root.render(createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 400, viewportHeightPx: 860, phone: true })));
    await untilDrawn(fakes);
    expect(state()).toBe('drawn');
    expect(transfers).toHaveLength(1);
    expect(named(fakes.calls, 'drawElements')).toHaveLength(1);
    // The newer window's size (jsdom's device pixel ratio is 1)
    expect(named(fakes.calls, 'viewport')[0]!.args).toEqual([0, 0, 400, 860]);
  });

  it('starts the upload when prepared while the reveal is at rest, so the first mount draws with nothing left to upload', async () => {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, pictureRows: 600 });
    stubCanvases(fakes);
    prepareRealSky();
    prepareRealSky();
    await act(async () => await fakes.frame(8));
    // Its programs, and the steering dome's, linked once a page in a context of their own besides the kept one
    expect(fakes.contexts).toHaveLength(2);
    expect(named(fakes.calls, 'shaderSource').some((c) => c.args[1] === FLIGHT_DOME_FRAGMENT)).toBe(true);
    // The photograph and the flight's starless glow, each once
    expect(fakes.fetched.filter((u) => u.startsWith('/textures/eso_milkyway'))).toEqual([REAL_SKY_PANORAMA_URL, FLIGHT_MILKY_WAY_URL]);
    expect(named(fakes.calls, 'generateMipmap')).toHaveLength(2);
    const uploads = fakes.calls.filter((c) => c.name === 'texSubImage2D' || c.name === 'texStorage2D').length;

    await act(async () => root.render(layer()));
    // No animation frame run: the draw needs none
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(state()).toBe('drawn');
    expect(fakes.calls.filter((c) => c.name === 'texSubImage2D' || c.name === 'texStorage2D')).toHaveLength(uploads);
    expect(named(fakes.calls, 'getError')).toHaveLength(0);
  });

  it('says it failed when the panorama cannot be had, and draws nothing', async () => {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, failFetch: true });
    stubCanvases(fakes);
    const onDrawn = vi.fn();
    await act(async () => root.render(createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 390, viewportHeightPx: 844, onDrawn })));
    await act(async () => await fakes.frame(2));
    expect(state()).toBe('failed');
    expect(onDrawn).not.toHaveBeenCalledWith(true);
    expect(transfers).toHaveLength(0);
  });
});

// DECIDED-BY-LEAD 19:45: no painted frame of Go there or Back to Moment without a sky
describe('Real sky keeps a sky on every frame', () => {
  async function prepared() {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, pictureRows: 400 });
    stubCanvases(fakes);
    prepareRealSky();
    await act(async () => await fakes.frame(8));
    return fakes;
  }
  const at = (props: Record<string, unknown> = {}) => createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, ...props });

  it('draws again in the same commit when a new size clears the canvas (the arrival\'s move at Go there and Back to Moment)', async () => {
    const fakes = await prepared();
    act(() => root.render(at()));
    expect(state()).toBe('drawn');
    const photo = container.querySelector<HTMLCanvasElement>('canvas.real-sky__canvas')!;
    const before = transfers.length;
    const frames = fakes.frames();
    // Go there: the move's area is larger, so the canvas is resized, which clears it
    act(() => root.render(at({ there: true, shift: { fullPx: [40, -20], nowPx: [0, 0] } })));
    expect(photo.width).toBe(430);
    expect(transfers.filter(([c]) => c === photo).length).toBe(transfers.slice(0, before).filter(([c]) => c === photo).length + 1);
    // Back to Moment: the window again
    act(() => root.render(at()));
    expect(photo.width).toBe(390);
    expect(transfers.filter(([c]) => c === photo)).toHaveLength(3);
    // No frame, timer or promise came between
    expect(fakes.frames()).toBe(frames);
  });

  it('draws in the first commit after the reveal prepared, the registration read at rest with the pictures', async () => {
    const fakes = await prepared();
    expect(fakes.fetched.filter((u) => u.endsWith('eso0932a_registration.json'))).toHaveLength(1);
    act(() => root.render(at()));
    expect(state()).toBe('drawn');
  });

  it('says it failed when the canvas refuses the frame drawn at once', async () => {
    await prepared();
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation((() => null) as typeof HTMLCanvasElement.prototype.getContext);
    act(() => root.render(at()));
    expect(state()).toBe('failed');
  });

  it('uploads again and draws, rather than failing, when the kept context was lost before a new size', async () => {
    const fakes = await prepared();
    act(() => root.render(at()));
    // The kept context is the one that listens for its loss
    fakes.lose(fakes.contexts.find((c) => ((c.canvas as unknown as { listeners: Record<string, unknown[]> }).listeners.webglcontextlost ?? []).length > 0)!);
    act(() => root.render(at({ there: true, shift: { fullPx: [40, -20], nowPx: [0, 0] } })));
    const uploadsBefore = named(fakes.calls, 'texStorage2D').length;
    await untilDrawnAgain(fakes);
    expect(state()).toBe('drawn');
    // The photograph and the glow went up again into a new context, and the layer did not say it failed on the way
    expect(named(fakes.calls, 'texStorage2D').length).toBe(uploadsBefore + 2);
    expect(fakes.contexts.filter((c) => ((c.canvas as unknown as { listeners: Record<string, unknown[]> }).listeners.webglcontextlost ?? []).length > 0)).toHaveLength(2);
  });

  it('uploads again into a new context when the kept one is lost but its loss event has not come yet', async () => {
    const fakes = await prepared();
    act(() => root.render(at()));
    const keptGl = fakes.contexts.find((c) => ((c.canvas as unknown as { listeners: Record<string, unknown[]> }).listeners.webglcontextlost ?? []).length > 0)!;
    keptGl.lost = true;
    const uploadsBefore = named(fakes.calls, 'texStorage2D').length;
    act(() => root.render(at({ shift: { fullPx: [40, -20], nowPx: [0, 0] } })));
    await untilDrawnAgain(fakes);
    expect(state()).toBe('drawn');
    expect(named(fakes.calls, 'texStorage2D').length).toBe(uploadsBefore + 1);
  });

  it('draws the glow in the commit that mounts it at Go there, and keeps the photograph under it until then', async () => {
    await prepared();
    act(() => root.render(at()));
    act(() => root.render(at({ there: true })));
    const layer = container.querySelector('.real-sky')!;
    const glow = container.querySelector<HTMLCanvasElement>('canvas.real-sky__glow')!;
    expect(glow.getAttribute('data-state')).toBe('drawn');
    expect(transfers.some(([c]) => c === glow)).toBe(true);
    expect(layer.classList.contains('is-glow-drawn')).toBe(true);
    expect(layer.classList.contains('is-glow-failed')).toBe(false);
  });

  it('says the glow failed, so the photograph gives way as ruled, when the glow cannot be had', async () => {
    const fakes = installSkyFakes({ pictureUrl: FLIGHT_MILKY_WAY_URL, otherBody: registration, failFetch: true });
    stubCanvases(fakes);
    const onGlowFailed = vi.fn();
    await act(async () => root.render(at({ there: true, onGlowFailed })));
    await act(async () => await fakes.frame(3));
    const layer = container.querySelector('.real-sky')!;
    expect(layer.classList.contains('is-glow-failed')).toBe(true);
    expect(layer.classList.contains('is-glow-drawn')).toBe(false);
    // flight-sky-line-state-one: the reveal is told, so the photograph's line goes with it
    expect(onGlowFailed).toHaveBeenLastCalledWith(true);
  });
});

describe('where the browser cannot keep sky pictures', () => {
  it('draws from the image element in its own context, as before, with no wait for the GPU', async () => {
    const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration });
    vi.stubGlobal('OffscreenCanvas', undefined);
    stubCanvases(fakes);
    class LoadingImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      decoding = '';
      set src(_url: string) {
        setTimeout(() => this.onload?.(), 0);
      }
    }
    vi.stubGlobal('Image', LoadingImage);
    await act(async () => root.render(layer()));
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    expect(state()).toBe('drawn');
    const uploads = named(fakes.calls, 'texImage2D');
    expect(uploads).toHaveLength(1);
    expect(uploads[0]!.args.slice(0, 5)).toEqual(['TEXTURE_2D', 0, 'RGB', 'RGB', 'UNSIGNED_BYTE']);
    expect(named(fakes.calls, 'pixelStorei')[0]!.args).toEqual(['UNPACK_FLIP_Y_WEBGL', true]);
    expect(named(fakes.calls, 'drawElements')).toHaveLength(1);
    expect(named(fakes.calls, 'getError')).toHaveLength(0);
    expect(transfers).toHaveLength(0);
  });
});
