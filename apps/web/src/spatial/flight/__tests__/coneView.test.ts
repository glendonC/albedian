// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { insidePhotoCone, pinholeScreenOf, unitVectorToRaDec, wcsCameraRows, worldToPixelWCS, type Vec3 } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { placeFinding } from '../flightFrame.js';
import { pinholeOfView } from '../starView.js';
import { CONE_LENGTH_WITHOUT_DISTANCES_PC, CONE_REACH, coneLengthPc, coneSceneOf, coneStarView, dragOrbit, initialOrbit, isConeViewOn, zoomOrbit } from '../coneView.js';

const W = 1440;
const H = 900;

function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
  } as ObservationSession;
}
function there(): ObservationSession {
  const session = photo();
  const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
  return dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' }).session;
}

beforeEach(() => {
  (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
});
afterEach(() => {
  delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
});

describe('isConeViewOn: the cone view after Go there, by page address until a control exists', () => {
  it('is on only there and only when asked', () => {
    expect(isConeViewOn(there(), '?view=cone')).toBe(true);
    expect(isConeViewOn(there(), '')).toBe(false);
    expect(isConeViewOn(there(), '?view=flight')).toBe(false);
    expect(isConeViewOn(photo(), '?view=cone')).toBe(false);
  });
});

describe('coneLengthPc: the cone reaches past its farthest matched star', () => {
  it('is the farthest placed matched star times the reach, or a drawing length when none has a distance', () => {
    const session = there();
    const farthest = Math.max(
      ...session.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched').map((f) => placeFinding(f)).map((p) => (p.kind === 'at_distance' ? p.distancePc : 0))
    );
    expect(coneLengthPc(session)).toBeCloseTo(farthest * CONE_REACH, 9);
    const findings = session.observationSolution!.findings.map((f) => ({ ...f, inferredDistancePc: undefined, cartesianParsecICRS: undefined, catalogRef: f.catalogRef ? { ...f.catalogRef, bailerJonesDistancePc: undefined } : undefined }));
    expect(coneLengthPc({ ...session, observationSolution: { ...session.observationSolution!, findings } } as ObservationSession)).toBe(CONE_LENGTH_WITHOUT_DISTANCES_PC);
  });
});

describe('coneStarView and coneSceneOf: looking back at the photo\'s cone', () => {
  it('keeps the cone\'s middle at the window\'s centre and Earth, the base and the photo in front of the camera', () => {
    const session = there();
    const state = initialOrbit(coneLengthPc(session));
    const view = coneStarView(session, state, W, H)!;
    const pin = pinholeOfView(view);
    const axis = wcsCameraRows(session.observationSolution!.wcs!).z;
    const half = coneLengthPc(session) / 2;
    const middle: Vec3 = [axis[0] * half - view.observerPc[0], axis[1] * half - view.observerPc[1], axis[2] * half - view.observerPc[2]];
    const s = pinholeScreenOf(pin, middle)!;
    expect(s.xPx).toBeCloseTo(W / 2, 6);
    expect(s.yPx).toBeCloseTo(H / 2, 6);
    const scene = coneSceneOf(session, view, coneLengthPc(session))!;
    expect(pinholeScreenOf(pin, scene.apexFromCamera)).not.toBeNull();
    for (const c of scene.cornersFromCamera) expect(pinholeScreenOf(pin, c)).not.toBeNull();
    // The base's corners on the photo's corner rays at the cone's length along its axis
    const camera = view.observerPc;
    for (const c of scene.cornersFromCamera) {
      const p: Vec3 = [c[0] + camera[0], c[1] + camera[1], c[2] + camera[2]];
      expect(p[0] * axis[0] + p[1] * axis[1] + p[2] * axis[2]).toBeCloseTo(coneLengthPc(session), 6);
    }
  });

  it('holds a matched star inside the cone exactly when the solve puts it on the photo', () => {
    const session = there();
    const { wcs, findings } = session.observationSolution!;
    const { width, height } = session.sourceAsset!;
    let checked = 0;
    for (const f of findings.filter((x) => x.evidenceClass === 'catalog_matched')) {
      const p = placeFinding(f);
      if (p.kind !== 'at_distance') continue;
      const { raDeg, decDeg } = unitVectorToRaDec(p.positionPc);
      const px = worldToPixelWCS(raDeg, decDeg, wcs!);
      const onPhoto = px.visible && px.uPx >= -0.5 && px.uPx <= width - 0.5 && px.vPx >= -0.5 && px.vPx <= height - 0.5;
      expect(insidePhotoCone(p.positionPc, wcs!, width, height)).toBe(onPhoto);
      checked += 1;
    }
    expect(checked).toBeGreaterThan(0);
  });

  it('maps the photo upright on the base as the page shows it', () => {
    const session = there();
    const turned = { ...session, sourceAsset: { ...session.sourceAsset!, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    const view = coneStarView(turned, initialOrbit(coneLengthPc(turned)), W, H)!;
    const scene = coneSceneOf(turned, view, coneLengthPc(turned), 4)!;
    // The stored top left corner turned a quarter clockwise is the upright image's top right
    expect(scene.plane.uvs[0]).toBeCloseTo(1, 6);
    expect(scene.plane.uvs[1]).toBeCloseTo(1, 6);
  });
});

describe('dragOrbit and zoomOrbit: following the hand', () => {
  it('turns with a drag, never over the top, and zooms by a factor within limits', () => {
    const s = initialOrbit(100);
    expect(dragOrbit(s, 40, 0).azimuthDeg).toBeCloseTo(s.azimuthDeg - 10, 12);
    expect(dragOrbit(s, 0, 10000).elevationDeg).toBe(85);
    expect(dragOrbit(s, 0, -10000).elevationDeg).toBe(-85);
    expect(zoomOrbit(s, 100, 100).distancePc / s.distancePc).toBeCloseTo(Math.exp(0.1), 12);
    expect(zoomOrbit(s, 1e6, 100).distancePc).toBe(2000);
    expect(zoomOrbit(s, -1e6, 100).distancePc).toBe(5);
  });
});
