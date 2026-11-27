// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';

// phone-orbit-one: the star layer goes round the view's up as each drag begins (orbitUpNext), not the up the steering
// began with, which the hand-over has turned since.

const ups = vi.hoisted(() => ({ seen: [] as Vec3[] }));
vi.mock('../orbit.js', async (importOriginal) => {
  const real = await importOriginal<typeof import('../orbit.js')>();
  return { ...real, orbitStep: (...args: Parameters<typeof real.orbitStep>) => (ups.seen.push(args[2]), real.orbitStep(...args)) };
});
const { FlightStarsLayer } = await import('../FlightStarsLayer.js');
const { HANDOVER_SECONDS } = await import('../nearStar.js');
const { flightCameraOf } = await import('../flightFrame.js');
const { steerStartOf } = await import('../starView.js');
type OpenFlightTiles = import('../FlightStarsLayer.js').OpenFlightTiles;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function there(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  const gone = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
  return { ...gone, spatialView: { ...gone.spatialView, transitionPhase: 'at_space', transitionProgress: 1 } } as ObservationSession;
}

const elsewhere: CatalogStarCoreTile = {
  layer: 0, order: 0, pix: 1, count: 1, positionBits: 32, directions: new Float32Array(raDecToUnitVector(200, -40)),
  magnitude: new Float32Array([3]), colorIndex: new Float32Array([0.5]), distancePc: new Float32Array([50]), flags: new Uint8Array([1]), byteLength: 0,
};

let container: HTMLDivElement;
let root: Root;
let frames: FrameRequestCallback[];
let now = 10_000;
beforeEach(() => {
  ups.seen = [];
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  frames = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    frames.push(cb);
    return frames.length;
  });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

async function tick(rounds: number) {
  for (let r = 0; r < rounds; r++) {
    now += 1000 / 60;
    const due = frames.splice(0);
    await act(async () => {
      for (const cb of due) cb(now);
    });
  }
}

describe('the up the star layer orbits round (phone-orbit-one)', () => {
  it('is the view\'s up as the drag begins, after the hand-over turned it, held through the drag', async () => {
    const session = there();
    const openTiles: OpenFlightTiles = async (onTile) => {
      onTile(elsewhere);
      return { setView: () => {}, dispose: () => {} };
    };
    const tree = (steering: boolean) => createElement(FlightStarsLayer, { session, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, openTiles, steering, onPressSky: () => {} });
    await act(async () => root.render(tree(false)));
    await tick(4);
    await act(async () => root.render(tree(true)));
    await tick(Math.round(HANDOVER_SECONDS * 60) + 30);
    const sky = container.querySelector('[data-testid="flight-sky"]')!;
    const ev = (type: string, x: number, y = 300) => act(() => void sky.dispatchEvent(new MouseEvent(type, { clientX: x, clientY: y })));
    // Across and down together: going over turns the view's own up, which the orbit must not follow within the drag
    let x = 150;
    let y = 300;
    await ev('pointerdown', x, y);
    for (let i = 0; i < 20; i++) {
      x -= 4;
      y += 8;
      await ev('pointermove', x, y);
      await tick(1);
    }
    await ev('pointerup', x, y);
    expect(ups.seen.length).toBeGreaterThan(5);
    // One up through the drag
    const first = ups.seen[0]!;
    for (const u of ups.seen) u.forEach((v, k) => expect(v).toBeCloseTo(first[k]!, 12));
    // and not the up the steering began with: the hand-over has turned the view toward the star since
    const startUp = steerStartOf(flightCameraOf(session, 390, 844)!).rows.y;
    expect(Math.hypot(first[0] - startUp[0], first[1] - startUp[1], first[2] - startUp[2])).toBeGreaterThan(1e-3);
  });
});
