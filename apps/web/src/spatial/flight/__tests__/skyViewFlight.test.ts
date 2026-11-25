import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import type { ObservationSession, SkyView } from '@albedian/contracts';
import { raDecToUnitVector, worldToPixelWCS } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { flightCameraOf, placeFinding, screenOfPlacement } from '../flightFrame.js';
import { wcsOfSkyView } from '../skyViewWcs.js';

// go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): a flight from the predicted sky takes its camera from the
// sky view kept in the checkpoint it left from, so the picked star starts where its ring was drawn.

const VIEW: SkyView = { cameraQuaternionIcrs: [0.5, -0.5, 0.5, 0.5], fovVerticalDeg: 45 };

function predictedSky(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const solution = base.observationSolution!;
  const { wcs: _wcs, ...rest } = solution;
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 },
    machines: { ...base.machines, calibration: 'predicted_only' },
    observationSolution: { ...rest, status: 'predicted_only', findings: solution.findings.map((f) => ({ ...f, evidenceClass: f.evidenceClass === 'catalog_matched' ? 'predicted' : f.evidenceClass })) },
  } as ObservationSession;
}
const starOf = (s: ObservationSession) => s.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted' && f.inferredDistancePc && f.catalogRef)!;
const fly = (s: ObservationSession, skyView?: SkyView) =>
  dispatchAlbedianAction(s, { type: 'space.enter', requestId: 'go', expectedViewRevision: s.viewRevision, findingId: starOf(s).id, distancePolicy: 'measured_or_estimated', ...(skyView ? { skyView } : {}) }).session;

describe('the flight from the predicted sky', () => {
  it('takes its camera from the departing sky view, and the picked star starts where the view drew it', () => {
    const s = predictedSky();
    const star = starOf(s);
    // The predicted sky looking a little off the star, north up, as a person would have left it
    const look = new THREE.PerspectiveCamera(45, 390 / 844);
    look.up.set(0, 0, 1);
    const toward = raDecToUnitVector(star.skyPosition!.raDeg + 3, star.skyPosition!.decDeg - 2);
    look.lookAt(new THREE.Vector3(...toward));
    const view: SkyView = { cameraQuaternionIcrs: look.quaternion.toArray() as [number, number, number, number], fovVerticalDeg: 45 };
    const flown = fly(s, view);
    const camera = flightCameraOf({ ...flown, spatialView: { ...flown.spatialView, observerOriginParsec: [0, 0, 0] } }, 390, 844)!;
    expect(camera).not.toBeNull();
    expect(camera.wcs).toEqual(wcsOfSkyView(view, 390, 844));
    expect(camera.storedToScreen).toEqual([1, 0, 0, 0, 1, 0]);
    // From Earth the star is drawn along its sky position: where the sky view's projection puts its ring
    const ring = worldToPixelWCS(star.skyPosition!.raDeg, star.skyPosition!.decDeg, camera.wcs);
    expect(ring.visible).toBe(true);
    expect(ring.uPx > 0 && ring.uPx < 390 && ring.vPx > 0 && ring.vPx < 844).toBe(true);
    const at = screenOfPlacement(camera, placeFinding(star))!;
    expect(Math.hypot(at.xPx - ring.uPx, at.yPx - ring.vPx)).toBeLessThan(1);
  });

  it('has no camera when the flight carried no sky view and the sky has no WCS or pose, as before', () => {
    expect(flightCameraOf(fly(predictedSky()), 390, 844)).toBeNull();
  });

  it('keeps a verified solve\'s own WCS', () => {
    const base = createSyntheticCalibratedSession();
    const solved = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 } } as ObservationSession;
    const star = solved.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
    const flown = dispatchAlbedianAction(solved, { type: 'space.enter', requestId: 'go', expectedViewRevision: solved.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated', skyView: VIEW }).session;
    expect(flightCameraOf(flown, 1440, 900)!.wcs).toBe(solved.observationSolution!.wcs);
  });
});
