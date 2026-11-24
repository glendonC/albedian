// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector, resolveFindingParsecPosition } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { FlightStarsLayer, type MakeFinish, type OpenFlightTiles } from '../FlightStarsLayer.js';
import type { FlightFinish } from '../flightFinish.js';

// flight-stars-finish-one (DECIDED-BY-LEAD 24 Nov 16:08 and 18:04): in flight every star but the one flown to is drawn in
// linear light into the finish (the predicted sky's bloom and tone mapping), and then the star flown to alone, in
// display light, with arrival-star-1's bloom quad after it; the cone view and a browser with no finish draw as before.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const arrived = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
  return { ...arrived, spatialView: { ...arrived.spatialView, transitionPhase: 'at_space', transitionProgress: 1 } } as ObservationSession;
}

/** A WebGL 2 stand-in recording, in order, the finish's steps, each draw's range and the tint's light. */
function recordingGl(events: string[]) {
  let n = 0;
  let linear = -1;
  const gl = new Proxy({} as Record<string, unknown>, {
    get: (_t, p) => {
      if (p === 'getUniformLocation') return (_program: unknown, name: string) => ({ name });
      if (p === 'getShaderParameter' || p === 'getProgramParameter') return () => true;
      if (p === 'getAttribLocation') return () => 0;
      if (p === 'uniform1f') return (loc: { name: string }, v: number) => { if (loc.name === 'uLinear') linear = v; };
      if (p === 'POINTS') return 11;
      if (p === 'TRIANGLE_STRIP') return 12;
      if (p === 'clear') return () => events.push('clear');
      if (p === 'drawArrays') return (mode: number, first: number, count: number) => events.push(mode === 12 ? 'bloom' : `draw ${first}+${count}${linear === 1 ? ' linear' : ''}`);
      if (typeof p === 'string' && /^[A-Z_0-9]+$/.test(p)) return 1;
      if (typeof p === 'string' && p.startsWith('create')) return () => ({ id: ++n });
      return () => {};
    },
  });
  return gl;
}

function finishStandIn(events: string[]): MakeFinish {
  return () => {
    const f: FlightFinish = { begin: () => events.push('begin'), end: () => events.push('end'), setSize: (w, h, r) => events.push(`size ${w}x${h}@${r}`), dispose: () => events.push('dispose') };
    return f;
  };
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('the flight\'s stars finished as the predicted sky\'s', () => {
  const session = there();
  const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
  const ref = resolveFindingParsecPosition(picked)!;
  const d = Math.hypot(ref[0], ref[1], ref[2]);
  // Three rows written by hand (typed test data): two stars, and the star flown to in the middle row
  const own: CatalogStarCoreTile = {
    layer: 0, order: 0, pix: 9, count: 3, positionBits: 32,
    directions: new Float32Array([...raDecToUnitVector(10, 10), ref[0] / d, ref[1] / d, ref[2] / d, ...raDecToUnitVector(12, 11)]),
    magnitude: new Float32Array([4, 1.5, 5]), colorIndex: new Float32Array([0.5, 1.0, 0.5]), distancePc: new Float32Array([50, d, 70]), flags: new Uint8Array([1, 1, 1]), byteLength: 0,
  };

  async function mount(props: Record<string, unknown>, events: string[]) {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(recordingGl(events) as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, ...props })));
    await act(async () => await Promise.resolve());
    events.length = 0;
    await act(async () => deliver!(own));
    // The last frame drawn: from its clear, with the finish's begin just before it when finished
    const at = events.lastIndexOf('clear');
    return events.slice(events[at - 1] === 'begin' ? at - 1 : at);
  }

  it('in flight: the stars but the one flown to into the finish in linear light, then it alone, then its bloom', async () => {
    const events: string[] = [];
    const frame = await mount({ makeFinish: finishStandIn(events) }, events);
    expect(frame).toEqual(['begin', 'clear', 'draw 0+1 linear', 'draw 2+1 linear', 'end', 'draw 1+1', 'bloom']);
    // Every frame drawn in flight is finished, the one drawn as the tile arrives among them
    const clears = events.flatMap((e, k) => (e === 'clear' ? [k] : []));
    expect(clears.length).toBeGreaterThan(1);
    for (const k of clears) expect(events[k - 1]).toBe('begin');
  });

  it('in the cone view: every star in one pass, unfinished, as before', async () => {
    const events: string[] = [];
    const frame = await mount({ makeFinish: finishStandIn(events), cone: true }, events);
    expect(frame).toEqual(['clear', 'draw 0+3']);
  });

  it('where there is no finish: every star in one pass and the bloom, as before', async () => {
    const events: string[] = [];
    const frame = await mount({ makeFinish: () => null }, events);
    expect(frame).toEqual(['clear', 'draw 0+3', 'bloom']);
  });

  it('finishes every frame the steering loop draws, the star flown to kept out of it', async () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const events: string[] = [];
    await mount({ makeFinish: finishStandIn(events), steering: true }, events);
    events.length = 0;
    const t0 = performance.now();
    for (let i = 1; i <= 3; i++) {
      const cb = frames.shift()!;
      await act(async () => cb(t0 + i * (1000 / 60)));
    }
    expect(events.filter((e) => e === 'begin')).toHaveLength(3);
    expect(events.filter((e) => e === 'end')).toHaveLength(3);
    const last = events.slice(events.lastIndexOf('begin'));
    expect(last).toEqual(['begin', 'clear', 'draw 0+1 linear', 'draw 2+1 linear', 'end', 'draw 1+1', 'bloom']);
  });

  it('frees the finish as the layer leaves', async () => {
    const events: string[] = [];
    await mount({ makeFinish: finishStandIn(events) }, events);
    await act(async () => root.render(createElement('div')));
    expect(events.at(-1)).toBe('dispose');
  });
});

describe('the finish follows the window', () => {
  it('is sized again when the window changes', async () => {
    const events: string[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(recordingGl(events) as never);
    const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });
    const session = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, makeFinish: finishStandIn(events) })));
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 390, viewportHeightPx: 844, openTiles, makeFinish: finishStandIn(events) })));
    expect(events.filter((e) => e.startsWith('size')).at(-1)).toBe('size 390x844@1');
  });
});
