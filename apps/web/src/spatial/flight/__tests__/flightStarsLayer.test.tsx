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
import { PHONE_CONE_CENTRE_Y_PX } from '../coneView.js';
import { FLIGHT_LOAD_LIMIT_MAG, flightCameraOf, flightViewCone } from '../flightFrame.js';
import { HANGAR_CRAFTS } from '../crafts.js';
import { arrivalBloomOf, arrivalStarOfTile, arrivalViewCone, arrivalShiftOf } from '../arrival.js';
import { catalogStarTint } from '../../catalogStars/catalogStarTint.js';
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
    // arrival-star-1's bloom quad
    TRIANGLE_STRIP: 12,
    uniform2f: (loc: { name: string }, x: number, y: number) => calls.uniform3fv.set(loc.name, [x, y]),
    uniform3f: (loc: { name: string }, x: number, y: number, z: number) => calls.uniform3fv.set(loc.name, [x, y, z]),
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

  it('says its stars have drawn in the very task that draws them, never when the catalog is unavailable, and no longer as it leaves (realsky-points-one)', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const told: Array<[boolean, number]> = [];
    const onStarsDrawn = (drawn: boolean) => told.push([drawn, calls.drawArrays.length]);
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, onStarsDrawn })));
    await act(async () => await Promise.resolve());
    expect(told).toEqual([]);
    await act(async () => deliver!(tile(1)));
    // Told after the draw, in the same task: Real sky's stars leave in the render that paints the flight's
    expect(told).toEqual([[true, 1]]);
    act(() => root.unmount());
    root = createRoot(container);
    expect(told.at(-1)![0]).toBe(false);
    const failed: boolean[] = [];
    await act(async () => root.render(createElement(FlightStarsLayer, { session: there(), viewportWidthPx: 1440, viewportHeightPx: 900, openTiles: () => Promise.reject(new Error('no catalog')), onStarsDrawn: (d: boolean) => failed.push(d) })));
    await act(async () => await Promise.resolve());
    expect(failed).not.toContain(true);
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

  it('recomputes the cone view camera when phone toggles at fixed viewport dimensions', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 390, viewportHeightPx: 844, cone: true, phone: false, openTiles })));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(tile(1)));
    expect(calls.uniform3fv.get('uPix')![1]).toBe(422);
    const laptopRowX = [...calls.uniform3fv.get('uRowX')!];
    const laptopRowY = [...calls.uniform3fv.get('uRowY')!];
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 390, viewportHeightPx: 844, cone: true, phone: true, openTiles })));
    expect(calls.uniform3fv.get('uPix')![1]).toBe(PHONE_CONE_CENTRE_Y_PX);
    // Phone cone view rolls a quarter turn (y becomes the unrolled x, x becomes -y)
    expect(calls.uniform3fv.get('uRowY')!).not.toEqual(laptopRowY);
    expect(calls.uniform3fv.get('uRowX')!).not.toEqual(laptopRowX);
  });

  it('draws the star flown to with its bloom from its own row, after every star, and asks once for the whole move\'s sky (arrival-star-1)', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const setView = vi.fn();
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView, dispose: () => {} };
    };
    const session = { ...there(), spatialView: { ...there().spatialView, transitionPhase: 'at_space', transitionProgress: 1 } } as ObservationSession;
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const ref = resolveFindingParsecPosition(picked)!;
    const d = Math.hypot(ref[0], ref[1], ref[2]);
    // The picked star's own row: at the place flown to, Gaia BP-RP 1.0
    const own: CatalogStarCoreTile = {
      layer: 0, order: 0, pix: 9, count: 1, positionBits: 32, directions: new Float32Array([ref[0] / d, ref[1] / d, ref[2] / d]),
      magnitude: new Float32Array([1.5]), colorIndex: new Float32Array([1.0]), distancePc: new Float32Array([d]), flags: new Uint8Array([1]), byteLength: 0,
    };
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles })));
    await act(async () => await Promise.resolve());
    // The loader is asked for the sky of the whole move: the photo's cone widened to hold the window at rest
    const photoCamera = flightCameraOf(session, 1440, 900)!;
    const shift = arrivalShiftOf(session, 1440, 900, false)!;
    expect(setView.mock.calls.at(-1)![0]).toEqual(arrivalViewCone(photoCamera, shift.fullPx));
    expect(setView.mock.calls.at(-1)![0].radiusRad).toBeGreaterThan(flightViewCone(photoCamera).radiusRad);
    await act(async () => deliver!(tile(1)));
    expect(layer().getAttribute('data-bloom-px')).toBeNull();
    const drawsBefore = calls.drawArrays.length;
    await act(async () => deliver!(own));
    // The stars, then the bloom's quad (4 corners) last
    expect(calls.drawArrays.slice(drawsBefore).at(-1)).toBe(4);
    const star = arrivalStarOfTile(own, ref)!;
    const view = { ...starViewOfFlight(photoCamera), storedToScreen: [photoCamera.storedToScreen[0], photoCamera.storedToScreen[1], photoCamera.storedToScreen[2] + shift.nowPx[0], photoCamera.storedToScreen[3], photoCamera.storedToScreen[4], photoCamera.storedToScreen[5] + shift.nowPx[1]] as const };
    const bloom = arrivalBloomOf(star, view)!;
    expect(Number(layer().getAttribute('data-bloom-px'))).toBeCloseTo(bloom.diameterPx, 1);
    const [x, y] = layer().getAttribute('data-bloom-at')!.split(',').map(Number);
    // At rest the star stands at the resting place, its bloom around it
    expect(x).toBeCloseTo(0.536 * 1440, 0);
    expect(y).toBeCloseTo(0.476 * 900, 0);
    expect(calls.uniform3fv.get('uCenter')).toEqual([expect.closeTo(bloom.xPx, 3), expect.closeTo(bloom.yPx, 3)]);
    expect(calls.uniform3fv.get('uTint')).toEqual(catalogStarTint(1.0, false).map((c) => expect.closeTo(c, 6)));
    // A new window: the loader is asked again for the whole move's sky at that size
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1280, viewportHeightPx: 800, openTiles })));
    const smaller = flightCameraOf(session, 1280, 800)!;
    expect(setView.mock.calls.at(-1)![0]).toEqual(arrivalViewCone(smaller, arrivalShiftOf(session, 1280, 800, false)!.fullPx));
  });

  it('draws no bloom in the cone view, and none for a star whose row the tiles do not hold', async () => {
    const { gl, calls } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    const picked = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!;
    const ref = resolveFindingParsecPosition(picked)!;
    const d = Math.hypot(ref[0], ref[1], ref[2]);
    const own: CatalogStarCoreTile = {
      layer: 0, order: 0, pix: 9, count: 1, positionBits: 32, directions: new Float32Array([ref[0] / d, ref[1] / d, ref[2] / d]),
      magnitude: new Float32Array([1.5]), colorIndex: new Float32Array([1.0]), distancePc: new Float32Array([d]), flags: new Uint8Array([1]), byteLength: 0,
    };
    await act(async () => root.render(createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, cone: true })));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(own));
    expect(layer().getAttribute('data-bloom-px')).toBeNull();
    expect(calls.drawArrays).not.toContain(4);
  });

  it('frames the craft at its arrival place below and left of the star at rest, at D26\'s point before the move (15:50)', async () => {
    const { gl } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    const openTiles: OpenFlightTiles = async () => ({ setView: () => {}, dispose: () => {} });
    const craft = HANGAR_CRAFTS.find((c) => c.id === 'astronaut-emu')!;
    const atRest = { ...there(), spatialView: { ...there().spatialView, transitionPhase: 'at_space', transitionProgress: 1 } } as ObservationSession;
    await act(async () => root.render(createElement(FlightStarsLayer, { session: atRest, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft })));
    const framed = () => container.querySelector('[data-testid="flight-craft"]')!.getAttribute('data-framing');
    expect(framed()).toBe(`${(0.361 * 1440).toFixed(1)},${(0.711 * 900).toFixed(1)}`);
    const before = { ...atRest, spatialView: { ...atRest.spatialView, transitionPhase: 'phase_a_photo_to_sky', transitionProgress: 0.5 } } as ObservationSession;
    await act(async () => root.render(createElement(FlightStarsLayer, { session: before, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, craft })));
    expect(framed()).toBe(`${(0.5 * 1440).toFixed(1)},${(0.62 * 900).toFixed(1)}`);
  });
});

