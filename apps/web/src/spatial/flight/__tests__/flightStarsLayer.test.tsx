// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { pixelToWorldWCS, raDecToUnitVector, resolveFindingParsecPosition } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { FLIGHT_LOAD_LIMIT_MAG, flightCameraOf } from '../flightFrame.js';
import { noDistanceStarsOnScreen } from '../flightRings.js';
import { starViewOfFlight } from '../starView.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const out = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' });
  return out.session;
}

/** A core tile written by hand (typed test data, not read from any catalog release). */
function tile(pix: number): CatalogStarCoreTile {
  const directions = new Float32Array([...raDecToUnitVector(83, 4), ...raDecToUnitVector(84, 5)]);
  return {
    layer: 0, order: 0, pix, count: 2, positionBits: 32, directions,
    magnitude: new Float32Array([2, 5]), colorIndex: new Float32Array([0.5, Number.NaN]),
    distancePc: new Float32Array([120, Number.NaN]), flags: new Uint8Array([1, 0]), byteLength: 0,
  };
}

/** A WebGL 2 context that records what the layer asks of it. */
function recordingGl() {
  const calls = { bufferData: [] as Float32Array[], drawArrays: [] as number[], uniform3fv: new Map<string, number[]>(), deleted: 0 };
  let n = 0;
  const gl: Record<string, unknown> = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5, STATIC_DRAW: 6, FLOAT: 7, COLOR_BUFFER_BIT: 8, BLEND: 9, ONE: 10, POINTS: 11,
    createShader: () => ({ id: ++n }), shaderSource: () => {}, compileShader: () => {}, getShaderParameter: () => true,
    createProgram: () => ({ id: ++n }), attachShader: () => {}, linkProgram: () => {}, getProgramParameter: () => true,
    getUniformLocation: (_p: unknown, name: string) => ({ name }), getAttribLocation: () => 0,
    createVertexArray: () => ({ id: ++n }), bindVertexArray: () => {}, deleteVertexArray: () => {},
    createBuffer: () => ({ id: ++n }), bindBuffer: () => {}, deleteBuffer: () => { calls.deleted += 1; },
    bufferData: (_t: number, data: Float32Array) => calls.bufferData.push(data),
    enableVertexAttribArray: () => {}, vertexAttribPointer: () => {},
    viewport: () => {}, clearColor: () => {}, clear: () => {}, useProgram: () => {}, enable: () => {}, blendFunc: () => {},
    uniform3fv: (loc: { name: string }, v: number[]) => calls.uniform3fv.set(loc.name, Array.from(v)),
    uniform2fv: () => {}, uniform1f: () => {}, deleteProgram: () => {},
    drawArrays: (_mode: number, _first: number, count: number) => calls.drawArrays.push(count),
  };
  return { gl, calls };
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

const layer = () => container.querySelector('[data-testid="flight-stars"]')!;

describe('FlightStarsLayer', () => {
  it('draws nothing and says it failed where the browser has no WebGL 2', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const openTiles = vi.fn<OpenFlightTiles>();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    expect(layer().getAttribute('data-state')).toBe('failed');
    expect(openTiles).not.toHaveBeenCalled();
    expect(layer().textContent).toBe('');
    expect(layer().getAttribute('aria-hidden')).toBe('true');
  });

  it('says the catalog is unavailable, with no stand-in stars, when it cannot be opened', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    const openTiles: OpenFlightTiles = () => Promise.reject(new Error('no catalog'));
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    await act(async () => await Promise.resolve());
    expect(layer().getAttribute('data-state')).toBe('unavailable');
    expect(calls.drawArrays).toEqual([]);
  });

  it('asks for the window\'s sky, uploads each tile rebased on the star flown to, and draws every star', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const setView = vi.fn();
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView, dispose: () => {} };
    };
    const session = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    await act(async () => await Promise.resolve());
    expect(setView).toHaveBeenCalled();
    expect(setView.mock.calls[0]![0].limitingMagnitude).toBe(FLIGHT_LOAD_LIMIT_MAG);
    await act(async () => deliver!(tile(1)));
    expect(layer().getAttribute('data-state')).toBe('drawn');
    expect(layer().getAttribute('data-stars')).toBe('2');
    expect(layer().getAttribute('data-sky-sphere')).toBe('1');
    expect(calls.drawArrays.at(-1)).toBe(2);
    // aPos first: the placed star's offset from the picked star, the other star's direction alone
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const ref = resolveFindingParsecPosition(picked)!;
    const pos = calls.bufferData[0]!;
    const at = raDecToUnitVector(83, 4);
    const dir2 = raDecToUnitVector(84, 5);
    for (let k = 0; k < 3; k++) {
      expect(pos[k]).toBeCloseTo(Math.fround(at[k]!) * 120 - ref[k]!, 3);
      expect(pos[3 + k]).toBeCloseTo(dir2[k]!, 6);
    }
    // The camera's offset from the reference, from float64
    const eye = calls.uniform3fv.get('uEye')!;
    const o = session.spatialView.observerOriginParsec;
    for (let k = 0; k < 3; k++) expect(eye[k]).toBeCloseTo(o[k]! - ref[k]!, 9);
  });

  it('draws again as the camera moves, with no re-upload while the star flown to stays the same', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(tile(1)));
    const uploads = calls.bufferData.length;
    const draws = calls.drawArrays.length;
    const halfway = { ...session, spatialView: { ...session.spatialView, observerOriginParsec: session.spatialView.observerOriginParsec.map((x) => x / 2) as [number, number, number] } };
    await act(async () => root.render(createElement(FlightStarsLayer, { session: halfway, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    expect(calls.drawArrays.length).toBeGreaterThan(draws);
    expect(calls.bufferData.length).toBe(uploads);
    expect(Number(layer().getAttribute('data-from-earth-pc'))).toBeCloseTo(Math.hypot(...halfway.spatialView.observerOriginParsec), 3);
  });

  it('tells the page once when a drag on the sky starts, and not on a tap (D30 item 6, the phone\'s hand-over)', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    const onDragSky = vi.fn();
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 390, viewportHeightPx: 844, openTiles: vi.fn<OpenFlightTiles>(), phone: true, onDragSky })));
    const sky = container.querySelector('[data-testid="flight-sky"]')!;
    const ev = (type: string, x: number, y: number) => act(() => void sky.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y })));
    ev('pointerdown', 200, 300);
    ev('pointerup', 202, 301);
    expect(onDragSky).not.toHaveBeenCalled();
    ev('pointerdown', 200, 300);
    ev('pointermove', 203, 300);
    expect(onDragSky).not.toHaveBeenCalled();
    ev('pointermove', 220, 310);
    ev('pointermove', 240, 320);
    ev('pointerup', 240, 320);
    expect(onDragSky).toHaveBeenCalledTimes(1);
  });

  it('tells the page how many stars with no distance it draws in the window, summed over its tiles (D27 item 2)', async () => {
    const { gl } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    const camera = flightCameraOf(session, 1440, 900)!;
    // A star with no distance where the window's centre looks, one behind the camera, and one with a distance
    const m = camera.screenToStored;
    const centre = pixelToWorldWCS(m[0] * 720 + m[1] * 450 + m[2], m[3] * 720 + m[4] * 450 + m[5], camera.wcs);
    const ahead = raDecToUnitVector(centre.raDeg, centre.decDeg);
    const three = (pix: number): CatalogStarCoreTile => ({
      layer: 0, order: 0, pix, count: 3, positionBits: 32,
      directions: new Float32Array([...ahead, -ahead[0], -ahead[1], -ahead[2], ...ahead]),
      magnitude: new Float32Array([4, 4, 4]), colorIndex: new Float32Array([0.5, 0.5, 0.5]),
      distancePc: new Float32Array([Number.NaN, Number.NaN, 120]), flags: new Uint8Array([0, 0, 1]), byteLength: 0,
    });
    const onNoDistanceCount = vi.fn();
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, onNoDistanceCount })));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(three(1)));
    expect(onNoDistanceCount).toHaveBeenLastCalledWith(1);
    await act(async () => deliver!(three(2)));
    expect(onNoDistanceCount).toHaveBeenLastCalledWith(2);
    expect(layer().getAttribute('data-no-distance-on-screen')).toBe('2');
    expect(noDistanceStarsOnScreen(three(1), starViewOfFlight(camera))).toBe(1);
  });
});
