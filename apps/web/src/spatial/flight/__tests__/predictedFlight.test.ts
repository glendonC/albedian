import { describe, expect, it } from 'vitest';
import { flightRefusal, type ObservationSolution } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../../components/__tests__/poseSessions.js';
import { projectFindingToViewport } from '../../../components/CelestialMarkerOverlay.js';
import { flightCameraOf, placeFinding, screenOfPlacement } from '../flightFrame.js';

// go-there-predicted-1 (LEAD ANSWER 12:55, UIUX-predflight-READY.md): a predicted star the flight rule allows is flown
// to from a photo with a pose estimate, and the flight leaves from the star's predicted ring as drawn through that
// pose, never from a claimed pixel: the flight's camera is the pose's own nominal WCS.

// The fixtures' pose estimates with flyable predicted stars (horizon_approximate's predicted findings carry no catalog
// distance, so it has none)
const POSED: Array<[string, ObservationSolution]> = [['pose_moon_level_head', POSE]];

describe('the flight from a pose estimate', () => {
  for (const [name, solution] of POSED) {
    it(`starts each flyable predicted star on its ring, to the pixel (${name})`, () => {
      const base = poseSession(solution);
      const flyable = solution.findings.filter((f) => f.evidenceClass === 'predicted' && flightRefusal(f) === null);
      expect(flyable.length).toBeGreaterThan(0);
      let checked = 0;
      for (const finding of flyable) {
        const session = { ...base, selectedFindingId: finding.id };
        const ring = projectFindingToViewport(finding, session, 1440, 900);
        if (!ring.visible) continue;
        const camera = flightCameraOf(session, 1440, 900);
        expect(camera).not.toBeNull();
        const at = screenOfPlacement(camera!, placeFinding(finding));
        expect(at).not.toBeNull();
        // The ring is placed to the whole pixel (CelestialMarkerOverlay rounds it)
        expect(Math.hypot(at!.xPx - ring.xPx, at!.yPx - ring.yPx)).toBeLessThanOrEqual(1);
        checked += 1;
      }
      expect(checked).toBeGreaterThan(0);
    });
  }

  it('has no camera for a sky predicted with no pose, nor for a solve that failed its check', () => {
    expect(flightCameraOf(poseSession(sample('horizon_predicted_only')), 1440, 900)).toBeNull();
    expect(flightCameraOf(poseSession(sample('plate_wcs_failed_verification')), 1440, 900)).toBeNull();
  });

  it('still takes a verified solve as before', () => {
    expect(flightCameraOf(poseSession(sample('plate_calibrated')), 1440, 900)).not.toBeNull();
  });
});
