import { describe, expect, it } from 'vitest';
import type { ObservationSession, SkyView } from '@albedian/contracts';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { realSkyGrid, skyAtScreen } from '../../../reveal/realSkyGrid.js';
import { wcsOfSkyView } from '../skyViewWcs.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parsePanoramaRegistration } from '../../sky/skyRegistration.js';

// go-there-predicted-glow-one (DECIDED-BY-LEAD 25 Nov 04:47): the Milky Way's glow in a flight from the predicted sky,
// placed by the sky view's own projection (skyViewWcs.ts), as the flight's stars are; the photo's Real sky never.

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


const here = path.dirname(fileURLToPath(import.meta.url));
const flat = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8')));

describe('the glow in a flight from the predicted sky (go-there-predicted-glow-one)', () => {
  const W = 390;
  const H = 844;

  it('reads the sky at a window point through the kept sky view\'s WCS, for the glow alone', () => {
    const flown = fly(predictedSky(), VIEW);
    const wcs = wcsOfSkyView(VIEW, W, H);
    for (const [x, y] of [[W / 2, H / 2], [20, 30], [W - 15, H - 40]] as const) {
      const got = skyAtScreen(flown, x, y, W, H, true)!;
      const want = pixelToWorldWCS(x, y, wcs);
      expect(got.raDeg).toBeCloseTo(want.raDeg, 9);
      expect(got.decDeg).toBeCloseTo(want.decDeg, 9);
    }
    // The photo's Real sky (no pose flag) has no sky there: the predicted sky has no solve to draw ESO's photograph by
    expect(skyAtScreen(flown, W / 2, H / 2, W, H, false)).toBeNull();
  });

  it('gives the glow a grid over the window from that view, and none without a kept view', () => {
    const flown = fly(predictedSky(), VIEW);
    const grid = realSkyGrid(flown, W, H, flat, 64, undefined, true);
    expect(grid).not.toBeNull();
    expect(grid!.positions.length).toBeGreaterThan(0);
    expect(realSkyGrid(flown, W, H, flat, 64, undefined, false)).toBeNull();
    // A flight from the predicted sky that carried no view, and the sky itself at rest: no grid to place a glow by
    expect(realSkyGrid(fly(predictedSky()), W, H, flat, 64, undefined, true)).toBeNull();
    expect(realSkyGrid(predictedSky(), W, H, flat, 64, undefined, true)).toBeNull();
  });
});
