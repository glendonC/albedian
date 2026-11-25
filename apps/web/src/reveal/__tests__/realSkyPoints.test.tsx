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
import type { CatalogStarCoreTile } from '../../spatial/catalogStars/tileCodec.js';
import type { OpenRealSkyTiles } from '../../spatial/sky/RealSkyStars.js';
import { elementGl, installSkyFakes } from '../../spatial/sky/__tests__/skyPictureFakes.js';

// realsky-points-one (DECIDED-BY-LEAD 24 Nov 20:35, B, and its answer to LOOK's question): at rest Real sky draws ESO's
// photograph with its own stars removed and the catalog's stars at their places, shown once both have drawn; the
// photograph only where either cannot be, so no star is drawn twice; in flight Real sky's stars leave on the render
// that says the flight's own have drawn, and come back with Back to Moment.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = fs.readFileSync(path.resolve(here, '../../../public/textures/eso0932a_registration.json'), 'utf8');
const session = poseSession(sample('plate_calibrated') as ObservationSolution);

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  forgetKeptSkyPictures();
  forgetPanoramaRegistration();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

async function prepared() {
  const fakes = installSkyFakes({ pictureUrl: REAL_SKY_PANORAMA_URL, otherBody: registration, pictureRows: 400 });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
    if (type === 'bitmaprenderer') return { transferFromImageBitmap: () => undefined } as unknown as RenderingContext;
    if (type === 'webgl2') return elementGl(fakes, this) as unknown as RenderingContext;
    return null;
  } as typeof HTMLCanvasElement.prototype.getContext);
  prepareRealSky();
  await act(async () => await fakes.frame(8));
  return fakes;
}

/** One star written by hand (typed test data, not read from any catalog release). */
const TILE: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32,
  directions: new Float32Array([0, 0, 1]), magnitude: new Float32Array([5]), colorIndex: new Float32Array([0.5]),
  distancePc: new Float32Array([Number.NaN]), flags: new Uint8Array([0]), byteLength: 0,
};

/** A tile source that hands over its one tile and is done; or one that cannot be had. */
const oneTile: OpenRealSkyTiles = async (onTile) => {
  setTimeout(() => onTile(TILE), 0);
  return { setView: () => undefined, dispose: () => undefined, getStats: () => ({ tilesWanted: 1, tilesInFlight: 0, tilesLoaded: 1, failures: 0 }) };
};
const noCatalog: OpenRealSkyTiles = () => Promise.reject(new Error('no catalog'));

const at = (props: Record<string, unknown> = {}) => createElement(RealSkyLayer, { session, rect: null, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, makeStarFinish: () => null, ...props });
const layerEl = () => container.querySelector('.real-sky')!;
const stars = () => container.querySelector<HTMLCanvasElement>('canvas.real-sky__stars');
const glow = () => container.querySelector<HTMLCanvasElement>('canvas.real-sky__glow');

async function untilPoints(fakes: ReturnType<typeof installSkyFakes>) {
  for (let i = 0; i < 20 && !layerEl().classList.contains('is-points'); i++) await act(async () => await fakes.frame());
}

describe('Real sky at rest: the glow and the catalog\'s stars', () => {
  it('shows the glow with the stars once both have drawn, never the photograph first, and says so', async () => {
    const fakes = await prepared();
    const onDrawn = vi.fn();
    const onPointsShown = vi.fn();
    act(() => root.render(at({ openStarTiles: oneTile, onDrawn, onPointsShown })));
    // The photograph drawn at once from the kept picture, but the layer waits for the stars
    expect(container.querySelector('[data-testid="real-sky"]')!.getAttribute('data-state')).toBe('drawn');
    expect(layerEl().classList.contains('is-shown')).toBe(false);
    expect(glow()).toBeNull();
    expect(onDrawn).not.toHaveBeenCalledWith(true);
    await untilPoints(fakes);
    expect(layerEl().classList.contains('is-points')).toBe(true);
    expect(layerEl().classList.contains('is-shown')).toBe(true);
    expect(glow()!.getAttribute('data-state')).toBe('drawn');
    expect(stars()!.classList.contains('is-shown')).toBe(true);
    expect(onPointsShown).toHaveBeenLastCalledWith(true);
    expect(onDrawn).toHaveBeenLastCalledWith(true);
    // The photograph's canvas gives way to them (reveal.css: .real-sky.is-points:not(.is-there) .real-sky__canvas)
    expect(layerEl().classList.contains('is-there')).toBe(false);
  });

  it('shows the photograph, alone and as published, where the catalog cannot be had: no glow asked, no stars', async () => {
    const fakes = await prepared();
    const onPointsShown = vi.fn();
    const onDrawn = vi.fn();
    act(() => root.render(at({ openStarTiles: noCatalog, onPointsShown, onDrawn })));
    for (let i = 0; i < 4; i++) await act(async () => await fakes.frame());
    expect(layerEl().classList.contains('is-shown')).toBe(true);
    expect(layerEl().classList.contains('is-points')).toBe(false);
    expect(glow()).toBeNull();
    expect(stars()!.getAttribute('data-state')).toBe('failed');
    expect(stars()!.classList.contains('is-shown')).toBe(false);
    expect(onPointsShown).not.toHaveBeenCalledWith(true);
    expect(onDrawn).toHaveBeenLastCalledWith(true);
  });

  it('shows the photograph where the glow cannot be had, the stars hidden with it', async () => {
    const fakes = installSkyFakes({ pictureUrl: '/textures/eso_milkyway_diffuse_6k.webp', otherBody: registration, failFetch: true });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
      if (type === 'bitmaprenderer') return { transferFromImageBitmap: () => undefined } as unknown as RenderingContext;
      if (type === 'webgl2') return elementGl(fakes, this) as unknown as RenderingContext;
      return null;
    } as typeof HTMLCanvasElement.prototype.getContext);
    const onPointsShown = vi.fn();
    act(() => root.render(at({ openStarTiles: oneTile, onPointsShown })));
    for (let i = 0; i < 20 && glow()?.getAttribute('data-state') !== 'failed'; i++) await act(async () => await fakes.frame());
    for (let i = 0; i < 20 && !layerEl().classList.contains('is-shown'); i++) await act(async () => await fakes.frame());
    expect(glow()!.getAttribute('data-state')).toBe('failed');
    expect(layerEl().classList.contains('is-points')).toBe(false);
    expect(layerEl().classList.contains('is-shown')).toBe(true);
    expect(stars()!.classList.contains('is-shown')).toBe(false);
    expect(onPointsShown).not.toHaveBeenCalledWith(true);
  });
});

describe('Real sky\'s stars in flight', () => {
  it('leave on the render that says the flight\'s own stars have drawn, and come back with Back to Moment', async () => {
    const fakes = await prepared();
    act(() => root.render(at({ openStarTiles: oneTile })));
    await untilPoints(fakes);
    act(() => root.render(at({ openStarTiles: oneTile, there: true, flightStarsDrawn: false })));
    expect(stars()!.classList.contains('is-shown')).toBe(true);
    act(() => root.render(at({ openStarTiles: oneTile, there: true, flightStarsDrawn: true })));
    expect(stars()!.classList.contains('is-shown')).toBe(false);
    // The glow stays: it is the flight's sky too
    expect(glow()!.classList.contains('is-drawn')).toBe(true);
    act(() => root.render(at({ openStarTiles: oneTile, there: false, flightStarsDrawn: false })));
    expect(stars()!.classList.contains('is-shown')).toBe(true);
  });

  it('are left to the flight by a layer Go there mounts first, so nothing loads twice during the move', async () => {
    await prepared();
    const open = vi.fn(oneTile);
    act(() => root.render(at({ openStarTiles: open, there: true })));
    expect(stars()).toBeNull();
    expect(open).not.toHaveBeenCalled();
    expect(glow()).not.toBeNull();
  });
});
