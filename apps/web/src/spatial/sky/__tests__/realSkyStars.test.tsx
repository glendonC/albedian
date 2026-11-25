// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { pixelToWorldWCS, raDecToUnitVector, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../../catalogStars/tileLoaderTypes.js';
import { FLIGHT_DRAW_LIMIT_MAG, buildFlightTileArrays, flightCameraOf, flightViewCone } from '../../flight/flightFrame.js';
import { FLIGHT_STAR_VERTEX, flightStarUniforms } from '../../flight/flightStarShader.js';
import { runVertexMain } from '../../flight/__tests__/glslSubset.js';
import { RealSkyStars, realSkyStarView, type RealSkyTileSource } from '../RealSkyStars.js';
import { elementGl, installSkyFakes, named } from './skyPictureFakes.js';

// realsky-points-one (DECIDED-BY-LEAD 24 Nov 20:35, B): Real sky's stars are the catalog's at their places, drawn from
// Earth through the photo's own solve by the flight's own vertex shader, to the one depth the skies share, once the
// view's tiles have come; nothing every frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const W = 1440;
const H = 900;

/** A calibrated photo with a star picked (so the flight's own camera would rebase on it; Real sky's must not). */
function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return { ...session, selectedFindingId: star.id } as ObservationSession;
}

/** The sky direction the photo's solve gives a window point. */
function directionAt(session: ObservationSession, x: number, y: number): Vec3 {
  const camera = flightCameraOf(session, W, H)!;
  const m = camera.screenToStored;
  const at = pixelToWorldWCS(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5], camera.wcs);
  return raDecToUnitVector(at.raDeg, at.decDeg);
}

/** Two stars written by hand (typed test data, not read from any catalog release): one at 120 pc, one with no distance. */
function tileOf(placedDir: Vec3, skyDir: Vec3, pix = 1): CatalogStarCoreTile {
  return {
    layer: 0, order: 0, pix, count: 2, positionBits: 32,
    directions: new Float32Array([...placedDir, ...skyDir]),
    magnitude: new Float32Array([4, 9]), colorIndex: new Float32Array([0.5, 0.5]),
    distancePc: new Float32Array([120, Number.NaN]), flags: new Uint8Array([1, 0]), byteLength: 0,
  };
}

describe('where Real sky draws a catalog star', () => {
  const session = photo();
  const area = { x0: -40, y0: -20, widthPx: W + 40, heightPx: H + 20 };
  const view = realSkyStarView(session, W, H, area)!;
  const tile = tileOf(directionAt(session, 0.42 * W, 0.47 * H), directionAt(session, 0.61 * W, 0.38 * H));
  const drawn = (i: number) => {
    const a = buildFlightTileArrays(tile, view.referencePc);
    const out = runVertexMain(FLIGHT_STAR_VERTEX, {
      aPos: [a.positions[3 * i]!, a.positions[3 * i + 1]!, a.positions[3 * i + 2]!], aPlaced: a.placed[i]!, aEarthPc: a.earthDistancePc[i]!, aMag: a.magnitude[i]!, aTint: [1, 1, 1],
      ...flightStarUniforms(view), uLimit: 12, uPixelRatio: 1, uGain: 1,
    });
    const [x, y] = out.gl_Position as number[];
    return { xPx: ((x! + 1) / 2) * area.widthPx, yPx: ((1 - y!) / 2) * area.heightPx, m: out.m as number };
  };

  it('puts each star at the pixel the photo\'s own solve gives its catalog place, in the area\'s own frame', () => {
    const placed = drawn(0);
    expect(Math.abs(placed.xPx - (0.42 * W - area.x0))).toBeLessThan(0.05);
    expect(Math.abs(placed.yPx - (0.47 * H - area.y0))).toBeLessThan(0.05);
    const onSky = drawn(1);
    expect(Math.abs(onSky.xPx - (0.61 * W - area.x0))).toBeLessThan(0.05);
    expect(Math.abs(onSky.yPx - (0.38 * H - area.y0))).toBeLessThan(0.05);
  });

  it('sees it from Earth, at its catalog magnitude, whatever star is picked', () => {
    expect(view.observerPc).toEqual([0, 0, 0]);
    expect(view.referencePc).toEqual([0, 0, 0]);
    expect(drawn(0).m).toBeCloseTo(4, 5);
    expect(drawn(1).m).toBe(9);
  });

  it('stays on Earth when the session stands at the star flown to', () => {
    const flown = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: session.selectedFindingId!, distancePolicy: 'measured_or_estimated' }).session;
    expect(Math.hypot(...flown.spatialView.observerOriginParsec)).toBeGreaterThan(1);
    expect(realSkyStarView(flown, W, H, area)!.observerPc).toEqual([0, 0, 0]);
  });

  it('has no view without a verified solve', () => {
    const bare = { ...session, observationSolution: undefined } as ObservationSession;
    expect(realSkyStarView(bare, W, H, area)).toBeNull();
  });
});

describe('the Real sky star layer', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fakes: ReturnType<typeof installSkyFakes>;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    fakes = installSkyFakes({ pictureUrl: '/none.jpg' });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, type: string) {
      return type === 'webgl2' ? (elementGl(fakes, this) as unknown as RenderingContext) : null;
    } as typeof HTMLCanvasElement.prototype.getContext);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const session = photo();
  const area = { x0: 0, y0: 0, widthPx: W, heightPx: H };

  /** A tile source the test drives: the views asked for, and how far the tiles have come. */
  function source() {
    const s = { views: [] as CatalogStarView[], stats: { tilesWanted: 2, tilesInFlight: 2, tilesLoaded: 0, failures: 0 }, onTile: null as null | ((t: CatalogStarCoreTile) => void), onError: null as null | (() => void), disposed: false };
    const open = vi.fn(async (onTile: (t: CatalogStarCoreTile) => void, onError: () => void): Promise<RealSkyTileSource> => {
      s.onTile = onTile;
      s.onError = onError;
      return { setView: (v) => void s.views.push(v), dispose: () => void (s.disposed = true), getStats: () => s.stats };
    });
    return { s, open };
  }
  const layer = (props: Record<string, unknown>) => createElement(RealSkyStars, { session, viewportWidthPx: W, viewportHeightPx: H, area, shown: true, makeFinish: () => null, ...props });
  const canvas = () => container.querySelector<HTMLCanvasElement>('canvas[data-testid="real-sky-stars"]')!;
  const tileA = tileOf(directionAt(session, 300, 200), directionAt(session, 700, 500), 1);
  const tileB = tileOf(directionAt(session, 900, 300), directionAt(session, 500, 650), 2);

  it('asks for the window\'s sky to the one depth the skies share', async () => {
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open })));
    const want = flightViewCone(flightCameraOf(session, W, H)!, FLIGHT_DRAW_LIMIT_MAG);
    expect(s.views).toHaveLength(1);
    expect(s.views[0]!.limitingMagnitude).toBe(FLIGHT_DRAW_LIMIT_MAG);
    expect(s.views[0]!.radiusRad).toBeCloseTo(want.radiusRad, 12);
    expect(s.views[0]!.centerIcrs).toEqual(want.centerIcrs);
  });

  it('draws once every tile the view wants has come, every tile at the depth, and says so only then', async () => {
    const { s, open } = source();
    const states: string[] = [];
    await act(async () => root.render(layer({ openTiles: open, onState: (st: string) => states.push(st) })));
    await act(async () => {
      s.onTile!(tileA);
      s.stats = { ...s.stats, tilesInFlight: 1, tilesLoaded: 1 };
      await fakes.frame();
    });
    // One tile still on its way: nothing drawn, nothing shown
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(0);
    expect(canvas().getAttribute('data-state')).toBe('loading');
    await act(async () => {
      s.onTile!(tileB);
      s.stats = { ...s.stats, tilesInFlight: 0, tilesLoaded: 2 };
      await fakes.frame();
    });
    expect(canvas().getAttribute('data-state')).toBe('drawn');
    expect(canvas().getAttribute('data-stars')).toBe('4');
    expect(canvas().className).toBe('real-sky__stars is-shown');
    expect(states.at(-1)).toBe('drawn');
    expect(named(fakes.calls, 'drawArrays').map((c) => c.args)).toEqual([['POINTS', 0, 2], ['POINTS', 0, 2]]);
    const limit = named(fakes.calls, 'uniform1f').find((c) => (c.args[0] as { name: string }).name === 'uLimit');
    expect(limit!.args[1]).toBe(FLIGHT_DRAW_LIMIT_MAG);
    // Light adds
    expect(named(fakes.calls, 'blendFunc')[0]!.args).toEqual(['ONE', 'ONE']);
    // At rest nothing more is drawn, frame after frame
    await act(async () => await fakes.frame(5));
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(2);
  });

  it('draws tiles that come in the same task once, together', async () => {
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open })));
    await act(async () => {
      s.stats = { tilesWanted: 2, tilesInFlight: 0, tilesLoaded: 2, failures: 0 };
      s.onTile!(tileA);
      s.onTile!(tileB);
      await fakes.frame();
    });
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(2);
    expect(named(fakes.calls, 'clear')).toHaveLength(1);
  });

  it('stays hidden while the layer does not show it, drawn all the same', async () => {
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open, shown: false })));
    await act(async () => {
      s.onTile!(tileA);
      s.stats = { tilesWanted: 1, tilesInFlight: 0, tilesLoaded: 1, failures: 0 };
      await fakes.frame();
    });
    expect(canvas().getAttribute('data-state')).toBe('drawn');
    expect(canvas().className).toBe('real-sky__stars');
  });

  it('draws again before the paint when the window changes, the new size cleared', async () => {
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open })));
    await act(async () => {
      s.onTile!(tileA);
      s.stats = { tilesWanted: 1, tilesInFlight: 0, tilesLoaded: 1, failures: 0 };
      await fakes.frame();
    });
    const frames = fakes.frames();
    act(() => root.render(layer({ openTiles: open, viewportWidthPx: 1280, area: { x0: 0, y0: 0, widthPx: 1280, heightPx: H } })));
    expect(named(fakes.calls, 'drawArrays')).toHaveLength(2);
    expect(named(fakes.calls, 'viewport').at(-1)!.args).toEqual([0, 0, 1280, H]);
    expect(fakes.frames()).toBe(frames);
  });

  it('fails, with no context made, when the catalog cannot be had; and fails when every tile it wanted failed', async () => {
    const states: string[] = [];
    await act(async () => root.render(layer({ openTiles: () => Promise.reject(new Error('no catalog')), onState: (st: string) => states.push(st) })));
    expect(canvas().getAttribute('data-state')).toBe('failed');
    expect(fakes.contexts).toHaveLength(0);
    act(() => root.unmount());
    root = createRoot(container);
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open })));
    await act(async () => {
      s.stats = { tilesWanted: 2, tilesInFlight: 0, tilesLoaded: 0, failures: 2 };
      s.onError!();
      await fakes.frame();
    });
    expect(canvas().getAttribute('data-state')).toBe('failed');
  });

  it('lets its tiles and buffers go when it leaves', async () => {
    const { s, open } = source();
    await act(async () => root.render(layer({ openTiles: open })));
    await act(async () => {
      s.onTile!(tileA);
      s.stats = { tilesWanted: 1, tilesInFlight: 0, tilesLoaded: 1, failures: 0 };
      await fakes.frame();
    });
    act(() => root.unmount());
    root = createRoot(container);
    expect(s.disposed).toBe(true);
    expect(named(fakes.calls, 'deleteBuffer')).toHaveLength(named(fakes.calls, 'createBuffer').length);
    expect(named(fakes.calls, 'deleteVertexArray')).toHaveLength(1);
  });
});
