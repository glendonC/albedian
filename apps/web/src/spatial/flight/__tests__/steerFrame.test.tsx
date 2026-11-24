// @vitest-environment jsdom
import { Profiler, act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { changeSpeed, followRows, raDecToUnitVector, steerCamera } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { FlightStarsLayer, type OpenFlightTiles } from '../FlightStarsLayer.js';
import { flightCameraOf } from '../flightFrame.js';
import { starViewOfSteer, steerStartOf } from '../starView.js';
import { arrivalShiftOf, arrivedFlightCamera } from '../arrival.js';
import { RING_RADIUS_PX, flightRings } from '../flightRings.js';
import { STEER_START_SPEED_PC_S, SteerHands } from '../steering.js';

// steer-frame-one (DECIDED-BY-LEAD 24 Nov 15:59): while the person steers, each frame's camera is held in a ref and drawn
// by the one frame loop, with no React render per frame; the page is told only when the readout's numbers change; the
// frames drawn are the ones the state-per-frame loop drew (the picture unchanged)

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

/** A WebGL 2 context recording each frame's camera offset (uEye) as the star pass sets it. */
function recordingGl() {
  const eyes: number[][] = [];
  const looks: number[][] = [];
  let n = 0;
  const gl: Record<string, unknown> = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5, STATIC_DRAW: 6, FLOAT: 7, COLOR_BUFFER_BIT: 8, BLEND: 9, ONE: 10, POINTS: 11, TRIANGLE_STRIP: 12,
    createShader: () => ({ id: ++n }), shaderSource: () => {}, compileShader: () => {}, getShaderParameter: () => true,
    createProgram: () => ({ id: ++n }), attachShader: () => {}, linkProgram: () => {}, getProgramParameter: () => true,
    getUniformLocation: (_p: unknown, name: string) => ({ name }), getAttribLocation: () => 0,
    createVertexArray: () => ({ id: ++n }), bindVertexArray: () => {}, deleteVertexArray: () => {},
    createBuffer: () => ({ id: ++n }), bindBuffer: () => {}, deleteBuffer: () => {}, bufferData: () => {},
    enableVertexAttribArray: () => {}, vertexAttribPointer: () => {},
    viewport: () => {}, clearColor: () => {}, clear: () => {}, useProgram: () => {}, enable: () => {}, blendFunc: () => {},
    uniform3fv: (loc: { name: string }, v: number[]) => {
      if (loc.name === 'uEye') eyes.push(Array.from(v));
      if (loc.name === 'uRowZ') looks.push(Array.from(v));
    },
    uniform2fv: () => {}, uniform1f: () => {}, uniform2f: () => {}, uniform3f: () => {}, deleteProgram: () => {}, drawArrays: () => {},
  };
  return { gl, eyes, looks };
}

/** A core tile written by hand (typed test data, not read from any catalog release). */
const tile: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(83, 4)),
  magnitude: new Float32Array([2]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([120]), flags: new Uint8Array([1]), byteLength: 0,
};

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
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('steering drawn by one frame loop (steer-frame-one)', () => {
  it('draws every frame from the ref with no React render, the same frames as before, the page told only as its numbers change', async () => {
    const { gl, eyes, looks } = recordingGl();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(gl as never);
    let deliver: ((t: CatalogStarCoreTile) => void) | null = null;
    const openTiles: OpenFlightTiles = async (onTile) => {
      deliver = onTile;
      return { setView: () => {}, dispose: () => {} };
    };
    const session = there();
    const onSteer = vi.fn();
    let commits = 0;
    const tree = (steering: boolean) =>
      createElement(Profiler, { id: 'flight', onRender: () => { commits += 1; } }, createElement(FlightStarsLayer, { session, viewportWidthPx: 1440, viewportHeightPx: 900, openTiles, steering, onSteer }));
    // The loop's clock starts at a known time, so the frames can be followed exactly
    const t0 = 10_000;
    const clock = vi.spyOn(performance, 'now').mockReturnValue(t0);
    await act(async () => root.render(tree(true)));
    await act(async () => await Promise.resolve());
    await act(async () => deliver!(tile));
    // At mount nothing steers yet (null), then the hand-over's first report
    const reportsBefore = onSteer.mock.calls.filter(([r]) => r !== null).length;
    expect(reportsBefore).toBe(1);
    // The frames the state-per-frame loop drew: the same steering rule from the same start, no input
    const camera = flightCameraOf(session, 1440, 900)!;
    const start = steerStartOf(camera);
    let want = { ...start, speedPcPerS: STEER_START_SPEED_PC_S, chase: start.rows };
    const hands = new SteerHands(() => false);
    const before = commits;
    const drawsBefore = eyes.length;
    const ticks = 60;
    for (let i = 1; i <= ticks; i++) {
      // For the last quarter second the person turns left, so the chase camera's look lags the craft's (MOTION k13)
      if (i === 45) {
        await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { code: 'ArrowLeft', key: 'ArrowLeft' })));
        hands.keyDown('ArrowLeft');
      }
      const cb = frames.shift()!;
      const now = t0 + i * (1000 / 60);
      await act(async () => cb(now));
      const dt = 1 / 60;
      const { input, speedDirection } = hands.frame(dt);
      const next = steerCamera(want, input, dt);
      want = { ...next, speedPcPerS: changeSpeed(want.speedPcPerS, speedDirection, dt), chase: followRows(want.chase, next.rows, dt, { reduced: false }) };
    }
    clock.mockRestore();
    // No React render while the loop draws
    expect(commits - before).toBe(0);
    // A frame drawn by each tick, the last one where the steering rule puts the camera
    expect(eyes.length - drawsBefore).toBe(ticks);
    const ref = camera.referencePc;
    expect(eyes.at(-1)).toEqual([0, 1, 2].map((k) => expect.closeTo(want.positionPc[k]! - ref[k]!, 3)));
    // drawn through the chase camera's look, which lags the craft's own in the turn
    expect(looks.at(-1)).toEqual([0, 1, 2].map((k) => expect.closeTo(want.chase.z[k]!, 5)));
    expect(Math.hypot(...[0, 1, 2].map((k) => want.chase.z[k]! - want.rows.z[k]!))).toBeGreaterThan(1e-3);
    // The layer's distance follows each frame; the page heard only of the changes it shows
    const fromEarth = Math.hypot(want.positionPc[0], want.positionPc[1], want.positionPc[2]);
    expect(Number(container.querySelector('[data-testid="flight-stars"]')!.getAttribute('data-from-earth-pc'))).toBeCloseTo(fromEarth, 3);
    const reports = onSteer.mock.calls.map(([r]) => r).filter(Boolean);
    expect(reports.length - reportsBefore).toBeGreaterThan(0);
    expect(reports.length - reportsBefore).toBeLessThan(ticks);
    expect(reports.at(-1)!.fromEarthPc).toBeLessThanOrEqual(fromEarth + 1e-9);
    // The rings while steering: drawn by the loop where the steering camera sees the matched stars
    const shift = arrivalShiftOf(session, 1440, 900, false);
    const cam = shift ? arrivedFlightCamera(camera, shift.nowPx) : camera;
    const wantRings = flightRings(session, starViewOfSteer(cam, { positionPc: want.positionPc, rows: want.chase }), [], RING_RADIUS_PX);
    expect(wantRings.length).toBeGreaterThan(0);
    const circles = [...container.querySelectorAll('[data-testid="flight-rings"] circle')];
    expect(circles.map((c) => c.getAttribute('data-finding-id'))).toEqual(wantRings.map((r) => r.findingId));
    circles.forEach((c, k) => expect(Number(c.getAttribute('cx'))).toBeCloseTo(wantRings[k]!.xPx, 3));
    // Handing the camera back: the page is told, the loop stops
    await act(async () => root.render(tree(false)));
    expect(onSteer).toHaveBeenLastCalledWith(null);
  });
});
