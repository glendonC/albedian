import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { apparentMagnitudeFrom, pixelToWorldWCS, placeStar, raDecToUnitVector, type StarPlacement, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { buildFlightTileArrays, flightCameraOf, screenOfPlacement, type FlightCamera } from '../flightFrame.js';
import { ARRIVAL_BLOOM_FRAGMENT, ARRIVAL_BLOOM_VERTEX, FLIGHT_STAR_FRAGMENT, FLIGHT_STAR_VERTEX, flightStarUniforms } from '../flightStarShader.js';
import { arrivalGlow, arrivalGlowYielded } from '../arrival.js';
import { starViewOfFlight } from '../starView.js';
import { runVertexMain, type GlslInputs } from './glslSubset.js';

// The flight's vertex shader, run from its own text (glslSubset.ts) against the flight's float64 projection: a star
// with a usable distance moves as the camera moves; one with none stays on the sky sphere, at the same pixel from
// anywhere, never at an invented distance (the verdict's note on stack 1: no test ran the sky-sphere branch).

const W = 1440;
const H = 900;

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

/** The sky direction the camera's photo shows at a window point. */
function directionAt(camera: FlightCamera, x: number, y: number): Vec3 {
  const m = camera.screenToStored;
  const at = pixelToWorldWCS(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5], camera.wcs);
  return raDecToUnitVector(at.raDeg, at.decDeg);
}

/** Two stars written by hand (typed test data, not read from any catalog release): one at 120 pc, one with none. */
function tileOf(placedDir: Vec3, skyDir: Vec3): CatalogStarCoreTile {
  return {
    layer: 0, order: 0, pix: 1, count: 2, positionBits: 32,
    directions: new Float32Array([...placedDir, ...skyDir]),
    magnitude: new Float32Array([2, 5]), colorIndex: new Float32Array([0.5, 0.5]),
    distancePc: new Float32Array([120, Number.NaN]), flags: new Uint8Array([1, 0]), byteLength: 0,
  };
}

/** The shader's attributes for star i of the tile, as the layer uploads them, and its uniforms for the camera. */
function inputsOf(camera: FlightCamera, tile: CatalogStarCoreTile, i: number): GlslInputs {
  const a = buildFlightTileArrays(tile, camera.referencePc);
  const u = flightStarUniforms(starViewOfFlight(camera));
  return {
    aPos: [a.positions[3 * i]!, a.positions[3 * i + 1]!, a.positions[3 * i + 2]!],
    aPlaced: a.placed[i]!,
    aEarthPc: a.earthDistancePc[i]!,
    aMag: a.magnitude[i]!,
    aTint: [1, 1, 1],
    ...u,
    uLimit: 6.5,
    uPixelRatio: 1,
    uGain: 1,
  };
}

/** Where the shader puts the star in the window, in CSS pixels; none when it hides it. */
function drawnAt(camera: FlightCamera, tile: CatalogStarCoreTile, i: number): { xPx: number; yPx: number } | null {
  const out = runVertexMain(FLIGHT_STAR_VERTEX, inputsOf(camera, tile, i));
  if (out.gl_PointSize === 0) return null;
  const [x, y] = out.gl_Position as number[];
  return { xPx: ((x! + 1) / 2) * W, yPx: ((1 - y!) / 2) * H };
}

function moved(camera: FlightCamera, byPc: Vec3): FlightCamera {
  const o = camera.observerPc;
  return { ...camera, observerPc: [o[0] + byPc[0], o[1] + byPc[1], o[2] + byPc[2]] };
}

describe('FLIGHT_STAR_VERTEX, run from its own text', () => {
  const session = there();
  // The flight from its start, on Earth (after Go there the session stands at its end, beside the star)
  const camera: FlightCamera = { ...flightCameraOf(session, W, H)!, observerPc: [0, 0, 0] };
  const placedDir = directionAt(camera, 0.42 * W, 0.47 * H);
  const skyDir = directionAt(camera, 0.61 * W, 0.38 * H);
  const tile = tileOf(placedDir, skyDir);
  const placed: StarPlacement = { kind: 'at_distance', positionPc: [placedDir[0] * 120, placedDir[1] * 120, placedDir[2] * 120], distancePc: 120 };
  const onSky = placeStar(skyDir, null);
  // Forty parsecs toward the star flown to: the placed star at 120 pc shifts across the window, the sky sphere does not
  const ref = camera.referencePc;
  const len = Math.hypot(ref[0], ref[1], ref[2]);
  const farther = moved(camera, [(ref[0] / len) * 40, (ref[1] / len) * 40, (ref[2] / len) * 40]);

  it('draws a star with a usable distance where the flight\'s float64 projection puts it, from two camera places', () => {
    for (const cam of [camera, farther]) {
      const want = screenOfPlacement(cam, placed)!;
      const got = drawnAt(cam, tile, 0)!;
      expect(Math.abs(got.xPx - want.xPx)).toBeLessThan(0.05);
      expect(Math.abs(got.yPx - want.yPx)).toBeLessThan(0.05);
    }
    const a = drawnAt(camera, tile, 0)!;
    const b = drawnAt(farther, tile, 0)!;
    expect(Math.hypot(a.xPx - b.xPx, a.yPx - b.yPx)).toBeGreaterThan(5);
  });

  it('keeps a star with no usable distance on the sky sphere: the same pixel from both places, where its direction falls', () => {
    const a = drawnAt(camera, tile, 1)!;
    const b = drawnAt(farther, tile, 1)!;
    const want = screenOfPlacement(camera, onSky)!;
    expect(Math.abs(a.xPx - want.xPx)).toBeLessThan(0.05);
    expect(Math.abs(a.yPx - want.yPx)).toBeLessThan(0.05);
    expect(Math.hypot(a.xPx - b.xPx, a.yPx - b.yPx)).toBeLessThan(0.01);
    expect(Math.abs(a.xPx - 0.61 * W)).toBeLessThan(0.05);
    expect(Math.abs(a.yPx - 0.38 * H)).toBeLessThan(0.05);
  });

  it('dims a placed star by the inverse square law from the camera, and leaves a sky-sphere star as bright as from Earth', () => {
    for (const cam of [camera, farther]) {
      const m = runVertexMain(FLIGHT_STAR_VERTEX, inputsOf(cam, tile, 0)).m as number;
      expect(m).toBeCloseTo(apparentMagnitudeFrom(2, placed, cam.observerPc), 4);
      expect(runVertexMain(FLIGHT_STAR_VERTEX, inputsOf(cam, tile, 1)).m).toBe(5);
    }
  });

  it('bends both kinds of star through the solve\'s radial lens model as the float64 projection does, near a corner', () => {
    const lensed: FlightCamera = { ...camera, wcs: { ...camera.wcs, radialDistortionK1: -0.03, radialDistortionK2: 0.004 } };
    const flat: FlightCamera = { ...camera, wcs: { ...camera.wcs, radialDistortionK1: 0, radialDistortionK2: 0 } };
    const corner = tileOf(directionAt(lensed, 0.08 * W, 0.1 * H), directionAt(lensed, 0.93 * W, 0.88 * H));
    const at = (i: number): Vec3 => [corner.directions[3 * i]!, corner.directions[3 * i + 1]!, corner.directions[3 * i + 2]!];
    const kinds: StarPlacement[] = [{ kind: 'at_distance', positionPc: [at(0)[0] * 120, at(0)[1] * 120, at(0)[2] * 120], distancePc: 120 }, placeStar(at(1), null)];
    for (const [i, placement] of kinds.entries()) {
      const want = screenOfPlacement(lensed, placement)!;
      const got = drawnAt(lensed, corner, i)!;
      expect(Math.abs(got.xPx - want.xPx)).toBeLessThan(0.05);
      expect(Math.abs(got.yPx - want.yPx)).toBeLessThan(0.05);
      const unbent = screenOfPlacement(flat, placement)!;
      expect(Math.hypot(want.xPx - unbent.xPx, want.yPx - unbent.yPx)).toBeGreaterThan(1);
    }
  });

  it('refuses shader text outside its subset rather than skipping it (the fragment shader has an if block)', () => {
    expect(() => runVertexMain(FLIGHT_STAR_FRAGMENT, {})).toThrow(/outside the subset/);
    expect(() => runVertexMain(FLIGHT_STAR_VERTEX, {})).toThrow(/has no value/);
  });

  it('hides a star behind the camera, and one past the draw limit', () => {
    const behind = tileOf([-placedDir[0], -placedDir[1], -placedDir[2]], [-skyDir[0], -skyDir[1], -skyDir[2]]);
    expect(drawnAt(camera, behind, 0)).toBeNull();
    expect(drawnAt(camera, behind, 1)).toBeNull();
    const faint = { ...tile, magnitude: new Float32Array([2, 6.9]) };
    expect(drawnAt(camera, faint, 1)).toBeNull();
    expect(drawnAt(camera, faint, 0)).not.toBeNull();
  });
});

describe('the arrival bloom\'s shaders, run from their own text (arrival-star-1)', () => {
  it('lays its quad on the bloom\'s square: the corners a radius from the star, in the window\'s clip space', () => {
    for (const corner of [[-1, -1], [1, -1], [-1, 1], [1, 1]] as [number, number][]) {
      const out = runVertexMain(ARRIVAL_BLOOM_VERTEX, { aCorner: corner, uCenter: [700, 400], uRadius: 150, uView: [1440, 900] });
      const pos = out.gl_Position as number[];
      expect(pos[0]).toBeCloseTo(((700 + corner[0]! * 150) / 1440) * 2 - 1, 9);
      expect(pos[1]).toBeCloseTo(1 - ((400 + corner[1]! * 150) / 900) * 2, 9);
      expect(out.vCorner).toEqual(corner);
    }
  });

  it('glows as arrivalGlow does, in the star\'s tint, and draws nothing past the bloom\'s edge', () => {
    expect(ARRIVAL_BLOOM_FRAGMENT).toContain('if (q >= 1.0) discard;');
    const body = ARRIVAL_BLOOM_FRAGMENT.replace('if (q >= 1.0) discard;', '');
    for (const [x, y] of [[0, 0], [0.2, 0.1], [0.5, -0.3], [-0.7, 0.6], [0.0, 0.99]] as const) {
      const q = Math.hypot(x, y);
      const out = runVertexMain(body, { vCorner: [x, y], uTint: [1, 0.5, 0.25], uCore: 1, uDisc: 0, uRadius: 150, uDiscTint: [1, 1, 1], uGlare: 0, uCenter: [700, 400], uYieldY: -1 });
      expect(out.glow as number).toBeCloseTo(arrivalGlow(q), 9);
      expect((out.outColor as number[]).slice(0, 3)).toEqual([1, 0.5, 0.25].map((c) => expect.closeTo(c * arrivalGlow(q), 9)));
    }
  });

  it('holds its white-hot centre at the share it is given, as arrivalGlow does (near-star-light-one)', () => {
    const body = ARRIVAL_BLOOM_FRAGMENT.replace('if (q >= 1.0) discard;', '');
    for (const [x, y] of [[0, 0], [0.05, 0.02], [0.1, -0.1], [0.4, 0.3], [0.0, 0.99]] as const) {
      const q = Math.hypot(x, y);
      const out = runVertexMain(body, { vCorner: [x, y], uTint: [1, 0.5, 0.25], uCore: 1 / 3, uDisc: 0, uRadius: 720, uDiscTint: [1, 1, 1], uGlare: 0, uCenter: [700, 400], uYieldY: -1 });
      expect(out.glow as number).toBeCloseTo(arrivalGlow(q, 1 / 3), 9);
      const glared = runVertexMain(body, { vCorner: [x, y], uTint: [1, 0.5, 0.25], uCore: 1 / 3, uDisc: 0, uRadius: 720, uDiscTint: [1, 1, 1], uGlare: 0.35, uCenter: [700, 400], uYieldY: -1 });
      expect(glared.glow as number).toBeCloseTo(arrivalGlow(q, 1 / 3, 0.35), 9);
      expect(out.disc as number).toBe(0);
    }
  });

  it('adds the disc in its white-hot tint inside its radius, with a one-pixel edge, and none when it has none', () => {
    const body = ARRIVAL_BLOOM_FRAGMENT.replace('if (q >= 1.0) discard;', '');
    const run = (q: number, uDisc: number) => runVertexMain(body, { vCorner: [q, 0], uTint: [0.5, 0.3, 0.2], uCore: 1 / 3, uDisc, uRadius: 400, uDiscTint: [1, 0.9, 0.8], uGlare: 0.2, uCenter: [700, 400], uYieldY: -1 });
    // A disc of 40 px radius in a bloom of 400: whole inside 39.5 px, none past 40.5 px, half at 40
    for (const [rPx, disc] of [[0, 1], [20, 1], [39.4, 1], [40, 0.5], [40.6, 0], [100, 0]] as const) {
      const out = run(rPx / 400, 0.1);
      expect(out.disc as number).toBeCloseTo(disc, 9);
      const g = arrivalGlow(rPx / 400, 1 / 3, 0.2);
      expect((out.outColor as number[]).slice(0, 3)).toEqual([0.5 * g + disc, 0.3 * g + 0.9 * disc, 0.2 * g + 0.8 * disc].map((c) => expect.closeTo(c, 9)));
    }
    for (const rPx of [0, 20, 40]) expect(run(rPx / 400, 0).disc as number).toBe(0);
  });

  it('yields under the phone\'s words: below uYieldY the bloom of arrival-star-1, over a 120 px fade above it', () => {
    const body = ARRIVAL_BLOOM_FRAGMENT.replace('if (q >= 1.0) discard;', '');
    // The star at y 450 with a bloom of 400 px radius; the words from y 500 down
    const run = (x: number, y: number) => runVertexMain(body, { vCorner: [x, y], uTint: [1, 1, 1], uCore: 1 / 3, uDisc: 0, uRadius: 400, uDiscTint: [1, 1, 1], uGlare: 0.35, uCenter: [200, 450], uYieldY: 500 });
    for (const [x, yPx, w] of [[0.1, 520, 1], [0.0, 500, 1], [0.05, 440, 0.5], [0.0, 470, 0.75], [0.2, 380, 0], [0.3, 300, 0]] as const) {
      const y = (yPx - 450) / 400;
      const q = Math.hypot(x, y);
      expect(run(x, y).glow as number).toBeCloseTo(arrivalGlowYielded(q, 1 / 3, 0.35, w), 9);
    }
    // No words line: the grown bloom everywhere
    const free = runVertexMain(body, { vCorner: [0, 0.2], uTint: [1, 1, 1], uCore: 1 / 3, uDisc: 0, uRadius: 400, uDiscTint: [1, 1, 1], uGlare: 0.35, uCenter: [200, 450], uYieldY: -1 });
    expect(free.glow as number).toBeCloseTo(arrivalGlow(0.2, 1 / 3, 0.35), 9);
  });
});
