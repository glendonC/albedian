// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { CATALOG_STAR_TILE_ENCODING_V1 as ENC } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { flightCameraOf } from '../flightFrame.js';
import { phoneCraftFraming } from '../craftScene.js';
import { starViewOfFlight } from '../starView.js';
import { ARRIVAL_GLARE_AT_FLOOR, cappedBloomDiameterPx, discAngularRadiusRad, discTintOf, magnitudeAt, STAR_SPRITE_PX, temperatureOfBMinusV } from '../arrival.js';
import { catalogStarTint } from '../../catalogStars/catalogStarTint.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

/**
 * near-star-light-one, in the layer: W held all the way in to the star's floor (near-star-flight-one's pacing), the
 * bloom's quad is drawn with the grown skirt, the white-hot centre held at arrival-star-1's size, and the modelled disc
 * where it passes the star's own sprite: a cool star's at its floor, a hot star's never.
 */

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
}

/** A GL that keeps the last value set for each uniform by its name. */
function recordingGl(uniforms: Map<string, number[]>): unknown {
  let n = 0;
  return new Proxy({}, {
    get: (_t, key) => {
      if (key === 'getShaderParameter' || key === 'getProgramParameter') return () => true;
      if (key === 'isContextLost') return () => false;
      if (key === 'getUniformLocation') return (_p: unknown, name: string) => ({ name });
      if (key === 'getAttribLocation') return () => 0;
      if (key === 'uniform1f') return (loc: { name: string }, x: number) => uniforms.set(loc.name, [x]);
      if (key === 'uniform3f') return (loc: { name: string }, x: number, y: number, z: number) => uniforms.set(loc.name, [x, y, z]);
      if (typeof key === 'string' && /^create/.test(key)) return () => ({ id: ++n });
      if (typeof key === 'string' && /^[A-Z_0-9]+$/.test(key)) return 1;
      return () => {};
    },
  });
}

/** The star flown to as its own catalog row (Hipparcos B-V `bMinusV`, magnitude 2), at the place flown to. Typed test data. */
function rowAtThePlace(bMinusV: number): { tile: CatalogStarCoreTile; earthPc: number } {
  const ref = flightCameraOf(there(), 1440, 900)!.referencePc;
  const d = Math.hypot(ref[0], ref[1], ref[2]);
  return {
    tile: {
      layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array([ref[0] / d, ref[1] / d, ref[2] / d]),
      magnitude: new Float32Array([2]), colorIndex: new Float32Array([bMinusV]), distancePc: new Float32Array([d]), flags: new Uint8Array([1 | ENC.flags.colorBMinusV]), byteLength: 0,
    },
    earthPc: Math.fround(d),
  };
}

let container: HTMLDivElement;
let root: Root;
let frames: FrameRequestCallback[];
let now = 10_000;
let uniforms: Map<string, number[]>;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  frames = [];
  uniforms = new Map();
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    frames.push(cb);
    return frames.length;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(recordingGl(uniforms) as never);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function tick(rounds: number, ms = 1000 / 60) {
  for (let r = 0; r < rounds; r++) {
    now += ms;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
  }
}

/** The layer at rest at the place flown to, then steered with W held for a minute: at the star's floor. */
async function flyIn(theTile: CatalogStarCoreTile): Promise<{ restCore: number[] | undefined; restDisc: number[] | undefined; restGlare: number[] | undefined; distancePc: number }> {
  const openTiles: OpenFlightTiles = async (onTile) => {
    onTile(theTile);
    return { setView: () => {}, dispose: () => {} };
  };
  const tree = (steering: boolean) => createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering });
  await act(async () => root.render(tree(false)));
  await tick(4);
  const restCore = uniforms.get('uCore');
  const restDisc = uniforms.get('uDisc');
  const restGlare = uniforms.get('uGlare');
  await act(async () => root.render(tree(true)));
  await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW', key: 'w' })));
  await tick(600, 100);
  const [distancePc] = container.querySelector('[data-testid="flight-stars"]')!.getAttribute('data-near')!.split(' ').map(Number);
  return { restCore, restDisc, restGlare, distancePc: distancePc! };
}

describe('the star flown to at its floor, in the layer (near-star-light-one)', () => {
  it('draws a cool star\'s disc at its modelled size, its white-hot centre held as the skirt fills the screen', async () => {
    const red = rowAtThePlace(1.85);
    const { restCore, restDisc, restGlare, distancePc } = await flyIn(red.tile);
    // At rest a light year out: arrival-star-1's bloom as it was, no disc
    expect(restCore).toEqual([expect.closeTo(1, 6)]);
    expect(restDisc).toEqual([0]);
    expect(restGlare![0]).toBeCloseTo(0, 6);
    // At the floor: the skirt across the screen's longer side, the centre at arrival-star-1's size
    const m = magnitudeAt(2, red.earthPc, distancePc);
    expect(uniforms.get('uRadius')![0]).toBeGreaterThan(0.99 * 720);
    expect(uniforms.get('uRadius')![0]).toBeLessThanOrEqual(720 + 1e-6);
    expect(uniforms.get('uCore')![0]).toBeCloseTo(cappedBloomDiameterPx(m, 1440, 900) / (2 * uniforms.get('uRadius')![0]!), 6);
    expect(uniforms.get('uCore')![0]).toBeLessThan(0.35);
    // The wide glare at its fill (the floor's Sun is 4.83 here and 4.81 on the curve: within a fiftieth)
    expect(uniforms.get('uGlare')![0]).toBeGreaterThan(0.98 * ARRIVAL_GLARE_AT_FLOOR);
    expect(uniforms.get('uGlare')![0]).toBeLessThanOrEqual(ARRIVAL_GLARE_AT_FLOOR + 1e-9);
    // The disc, from its magnitude and colour where the camera is, through the camera's scale within 5 percent
    const view = starViewOfFlight(flightCameraOf(there(), 1440, 900)!);
    const pxPerRad = Math.hypot(view.storedToScreen[0], view.storedToScreen[3]) / view.scaleRadPerPx;
    const expected = 2 * discAngularRadiusRad(m, temperatureOfBMinusV(Math.fround(1.85))) * pxPerRad;
    expect(expected).toBeGreaterThan(STAR_SPRITE_PX);
    const discPx = uniforms.get('uDisc')![0]! * 2 * uniforms.get('uRadius')![0]!;
    expect(Math.abs(discPx / expected - 1)).toBeLessThan(0.05);
    expect(uniforms.get('uDiscTint')).toEqual(discTintOf(catalogStarTint(Math.fround(1.85), true)).map((c) => expect.closeTo(c, 6)));
  });

  it('tells the bloom where the phone\'s words stand, from the readout\'s top, and none on the laptop', async () => {
    const blue = rowAtThePlace(-0.03);
    await flyIn(blue.tile);
    expect(uniforms.get('uYieldY')).toEqual([-1]);
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(blue.tile);
      return { setView: () => {}, dispose: () => {} };
    };
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 390, viewportHeightPx: 844, openTiles, phone: true, craftFraming: phoneCraftFraming(390, 612) })));
    await tick(4);
    expect(uniforms.get('uYieldY')).toEqual([612]);
  });

  it('draws no disc for a hot star even at its floor: it stays inside the star\'s own sprite', async () => {
    const blue = rowAtThePlace(-0.03);
    await flyIn(blue.tile);
    expect(uniforms.get('uRadius')![0]).toBeGreaterThan(0.99 * 720);
    expect(uniforms.get('uDisc')).toEqual([0]);
  });
});
