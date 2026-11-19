import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { FLIGHT_DRAW_LIMIT_MAG, flightCameraOf, photoStarsInFlight } from '../flightFrame.js';
import { RING_RADIUS_PX, RING_RADIUS_PX_PHONE, flightRings, noDistanceStarsOnScreen, noDistanceSentence } from '../flightRings.js';
import { starViewOfFlight } from '../starView.js';

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

describe('flightRings: a ring on every star the photo matched, while on screen (D27 item 1)', () => {
  it('rings each matched star on screen at its 3D place, never the picked one, never under the words or the bar', () => {
    const session = there();
    const stars = photoStarsInFlight(session, W, H);
    const onScreen = stars.filter((s) => s.screen && s.findingId !== session.selectedFindingId && s.screen.xPx >= 0 && s.screen.xPx <= W && s.screen.yPx >= 0 && s.screen.yPx <= H);
    const view = starViewOfFlight(flightCameraOf(session, W, H)!);
    const all = flightRings(session, view, []);
    expect(all.map((r) => r.findingId).sort()).toEqual(onScreen.map((s) => s.findingId).sort());
    for (const r of all) {
      const s = stars.find((x) => x.findingId === r.findingId)!;
      expect(r.xPx).toBe(s.screen!.xPx);
      expect(r.yPx).toBe(s.screen!.yPx);
    }
    expect(all.some((r) => r.findingId === session.selectedFindingId)).toBe(false);
    if (all.length > 0) {
      const first = all[0]!;
      // A box over the first ring takes it away, and one that only touches its edge does too
      const covered = flightRings(session, view, [{ left: first.xPx - 2, top: first.yPx - 2, right: first.xPx + 2, bottom: first.yPx + 2 }]);
      expect(covered.some((r) => r.findingId === first.findingId)).toBe(false);
      const touching = flightRings(session, view, [{ left: first.xPx + RING_RADIUS_PX - 0.5, top: first.yPx - 1, right: first.xPx + 40, bottom: first.yPx + 1 }]);
      expect(touching.some((r) => r.findingId === first.findingId)).toBe(false);
      const clear = flightRings(session, view, [{ left: first.xPx + RING_RADIUS_PX + 2, top: first.yPx - 1, right: first.xPx + 40, bottom: first.yPx + 1 }]);
      expect(clear.some((r) => r.findingId === first.findingId)).toBe(true);
      // The phone's smaller ring clears a box the laptop's touches
      const nearBox = [{ left: first.xPx + 6.5, top: first.yPx - 1, right: first.xPx + 40, bottom: first.yPx + 1 }];
      expect(flightRings(session, view, nearBox).some((r) => r.findingId === first.findingId)).toBe(false);
      expect(flightRings(session, view, nearBox, RING_RADIUS_PX_PHONE).some((r) => r.findingId === first.findingId)).toBe(true);
    }
  });
});

/** A core tile written by hand (typed test data, not read from any catalog release). */
function tile(stars: Array<{ ra: number; dec: number; mag: number; distancePc: number; kind: number }>): CatalogStarCoreTile {
  const n = stars.length;
  const directions = new Float32Array(3 * n);
  const magnitude = new Float32Array(n);
  const distancePc = new Float32Array(n);
  const flags = new Uint8Array(n);
  stars.forEach((s, i) => {
    directions.set(raDecToUnitVector(s.ra, s.dec), 3 * i);
    magnitude[i] = s.mag;
    distancePc[i] = s.distancePc;
    flags[i] = s.kind;
  });
  return { layer: 0, order: 0, pix: 1, count: n, positionBits: 32, directions, magnitude, colorIndex: new Float32Array(n), distancePc, flags, byteLength: 0 };
}

describe('noDistanceStarsOnScreen: the stars kept on the sky sphere that the window shows (D27 item 2)', () => {
  it('counts stars with no usable distance, bright enough to be drawn, inside the window', () => {
    const session = there();
    const view = starViewOfFlight(flightCameraOf(session, W, H)!);
    const t = tile([
      // On screen near the photo's centre, no distance, bright: counted
      { ra: 83.8, dec: 4.0, mag: 3, distancePc: Number.NaN, kind: 0 },
      // Same place with a distance: not counted
      { ra: 83.9, dec: 4.1, mag: 3, distancePc: 120, kind: 1 },
      // No distance but fainter than the draw limit: not drawn, not counted
      { ra: 84.0, dec: 4.2, mag: FLIGHT_DRAW_LIMIT_MAG + 0.5, distancePc: Number.NaN, kind: 0 },
      // No distance, bright, on the far side of the sky: not on screen
      { ra: 263.8, dec: -4.0, mag: 2, distancePc: Number.NaN, kind: 0 },
      // No distance, bright, in front but 40 degrees off the photo's centre, past the window's edge: not on screen
      { ra: 123.8, dec: 4.0, mag: 2, distancePc: Number.NaN, kind: 0 },
    ]);
    expect(noDistanceStarsOnScreen(t, view)).toBe(1);
  });

  it('words the count as the record does', () => {
    expect(noDistanceSentence(0)).toBeNull();
    expect(noDistanceSentence(1)).toBe('1 star has no measured distance, so it stays where it appears from Earth.');
    expect(noDistanceSentence(2)).toBe('2 stars have no measured distance, so they stay where they appear from Earth.');
    expect(noDistanceSentence(12)).toBe('12 stars have no measured distance, so they stay where they appear from Earth.');
    expect(noDistanceSentence(1200)).toBe('1,200 stars have no measured distance, so they stay where they appear from Earth.');
  });
});
