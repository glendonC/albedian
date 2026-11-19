import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { steerCamera } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { flightCameraOf } from '../flightFrame.js';
import { starViewOfFlight, starViewOfSteer, steerStartOf } from '../starView.js';

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

describe('starViewOfSteer: the flight camera handed to the person', () => {
  it('is the flight\'s own view at the handover, and keeps its lens and frame as it turns and moves', () => {
    const camera = flightCameraOf(there(), 1440, 900)!;
    const start = steerStartOf(camera);
    expect(starViewOfSteer(camera, start)).toEqual(starViewOfFlight(camera));
    const moved = steerCamera({ ...start, speedPcPerS: 1 }, { yawRadPerS: 0.5, pitchRadPerS: -0.2 }, 0.1);
    const view = starViewOfSteer(camera, moved);
    expect(view.observerPc).toEqual(moved.positionPc);
    expect(view.rows).toEqual(moved.rows);
    const flight = starViewOfFlight(camera);
    for (const k of ['crpixX', 'crpixY', 'scaleRadPerPx', 'k1', 'k2', 'foldRad', 'storedToScreen', 'referencePc'] as const) expect(view[k]).toEqual(flight[k]);
  });
});
