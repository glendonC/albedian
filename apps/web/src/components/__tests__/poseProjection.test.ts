import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  ObservationSolutionSchema,
  orientedSize,
  type CelestialFinding,
  type DisplayOrientation,
  type ObservationSession,
  type ObservationSolution,
  type WCSCalibrationModel,
} from '@albedian/contracts';
import { pixelToWorldWCS, worldToPixelWCS } from '@albedian/camera-rig';
import { projectThroughPose } from '../poseProjection';
import { projectFindingToViewport } from '../CelestialMarkerOverlay';
import { sourcePhotoDisplayRect, sourcePixelToViewport } from '../sourcePhotoRect';
import { FRAME, POSE, poseSession, predictedOf as predicted, sample, starsOf as stars } from './poseSessions';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const RAD = Math.PI / 180;

/** The point `distDeg` from (raDeg, decDeg) on the sky toward `bearingDeg` (east of north): the destination formula. */
function offsetOnSky(raDeg: number, decDeg: number, distDeg: number, bearingDeg: number): { raDeg: number; decDeg: number } {
  const [ra, dec, d, b] = [raDeg * RAD, decDeg * RAD, distDeg * RAD, bearingDeg * RAD];
  const dec2 = Math.asin(Math.sin(dec) * Math.cos(d) + Math.cos(dec) * Math.sin(d) * Math.cos(b));
  const ra2 = ra + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(dec), Math.cos(d) - Math.sin(dec) * Math.sin(dec2));
  return { raDeg: ((((ra2 / RAD) % 360) + 360) % 360), decDeg: dec2 / RAD };
}

/**
 * The centroid term at a finding's own place, worked out here: the farthest, under the nominal WCS, of the points
 * `distDeg` away from it on the sky in `directions` bearings evenly spread, from its own nominal projection.
 */
function centroidAtPlacePx(finding: CelestialFinding, wcs: WCSCalibrationModel, distDeg: number, directions = 360): number {
  const sky = finding.skyPosition!;
  const nominal = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
  let farthest = 0;
  for (let k = 0; k < directions; k++) {
    const p = offsetOnSky(sky.raDeg, sky.decDeg, distDeg, (360 * k) / directions);
    const q = worldToPixelWCS(p.raDeg, p.decDeg, wcs);
    farthest = Math.max(farthest, Math.hypot(q.uPx - nominal.uPx, q.vPx - nominal.vPx));
  }
  return farthest;
}

/** The largest distance from the nominal projection to the projections under the swept WCS, worked out here. */
function largestSweptDistancePx(finding: CelestialFinding, solution: ObservationSolution): number {
  const sky = finding.skyPosition!;
  const nominal = worldToPixelWCS(sky.raDeg, sky.decDeg, solution.wcs!);
  return Math.max(
    ...solution.poseEstimate!.sweptWcs.map((w) => {
      const p = worldToPixelWCS(sky.raDeg, sky.decDeg, w);
      return Math.hypot(p.uPx - nominal.uPx, p.vPx - nominal.vPx);
    })
  );
}

describe('projectThroughPose on the synthetic pose sample (P-SKY-8, REQ-SKY-6.2)', () => {
  it('puts the predicted Moon on the observed Moon under the nominal and every swept WCS, as the Python fit placed it', () => {
    // The sample lines the WCS up so the predicted Moon lands on the hand-placed Moon at (700, 160) px; the same
    // point here shows camera-rig projects with the Python WCS conventions (services/science/wcs_models.py)
    const observed = POSE.findings.find((f) => f.id === 'finding-observed-moon')!.sourceDetection!;
    const moon = POSE.findings.find((f) => f.id === 'finding-predicted-moon')!.skyPosition!;
    for (const wcs of [POSE.wcs!, ...POSE.poseEstimate!.sweptWcs]) {
      const p = worldToPixelWCS(moon.raDeg, moon.decDeg, wcs);
      expect(Math.hypot(p.uPx - observed.xPx, p.vPx - observed.yPx)).toBeLessThan(0.5);
    }
    const lined = projectThroughPose(POSE.findings.find((f) => f.id === 'finding-predicted-moon')!, POSE)!;
    expect(lined.xPx).toBeCloseTo(observed.xPx, 0);
    expect(lined.yPx).toBeCloseTo(observed.yPx, 0);
  });

  it('places the six predicted stars inside the frame, each ring the largest swept distance plus the centroid term', () => {
    const centroidDeg = POSE.poseEstimate!.beaconCentroidUncertaintyDeg;
    const placed = stars(POSE).map((f) => [f, projectThroughPose(f, POSE, FRAME)] as const).filter(([, p]) => p !== null);
    expect(placed.length).toBe(6);
    for (const [finding, lined] of placed) {
      const nominal = worldToPixelWCS(finding.skyPosition!.raDeg, finding.skyPosition!.decDeg, POSE.wcs!);
      expect(lined!.xPx).toBeCloseTo(nominal.uPx, 9);
      expect(lined!.yPx).toBeCloseTo(nominal.vPx, 9);
      const swept = largestSweptDistancePx(finding, POSE);
      // The sweep moves every star away from the Moon by a nonzero amount
      expect(swept).toBeGreaterThan(1);
      // The centroid term measured at the star's own place, in 360 bearings
      const centroid = centroidAtPlacePx(finding, POSE.wcs!, centroidDeg);
      expect(lined!.ringPx).toBeGreaterThanOrEqual(swept + centroid - 1e-9);
      expect(lined!.ringPx).toBeLessThanOrEqual(swept + centroid + 1e-4);
    }
  });

  it('takes the frame from the asset, not from twice crpix, when the two differ (verdict finding 3)', () => {
    // The sample's crpix is (480, 270): twice it is 960 x 540
    const wcs = POSE.wcs!;
    const at = (xPx: number, yPx: number): CelestialFinding => {
      const sky = pixelToWorldWCS(xPx, yPx, wcs);
      return { ...stars(POSE)[0]!, skyPosition: { raDeg: sky.raDeg, decDeg: sky.decDeg, uncertaintyArcsec: 55 } };
    };
    expect(projectThroughPose(at(1100, 270), POSE, { widthPx: 1200, heightPx: 540 })).not.toBeNull();
    expect(projectThroughPose(at(900, 270), POSE, { widthPx: 880, heightPx: 540 })).toBeNull();
    expect(projectThroughPose(at(480, 600), POSE, { widthPx: 960, heightPx: 700 })).not.toBeNull();
    expect(projectThroughPose(at(480, 500), POSE, { widthPx: 960, heightPx: 480 })).toBeNull();
    // The overlay passes the session's own asset size
    const base = poseSession();
    const sized = (width: number, height: number): ObservationSession => ({
      ...base,
      sourceAsset: { ...base.sourceAsset!, width, height },
    });
    expect(projectFindingToViewport(at(1100, 270), sized(1200, 540), 1440, 900).visible).toBe(true);
    expect(projectFindingToViewport(at(900, 270), sized(880, 540), 1440, 900).visible).toBe(false);
    expect(projectFindingToViewport(at(480, 600), sized(960, 700), 1440, 900).visible).toBe(true);
    expect(projectFindingToViewport(at(480, 500), sized(960, 480), 1440, 900).visible).toBe(false);
  });

  it('places nothing behind the camera, outside the frame, without a pose estimate or for a finding that is not predicted', () => {
    // Uranus and Saturn are above the horizon but far behind this frame
    for (const id of ['finding-predicted-uranus', 'finding-predicted-saturn']) {
      expect(projectThroughPose(POSE.findings.find((f) => f.id === id)!, POSE, FRAME)).toBeNull();
    }
    const noPose: ObservationSolution = { ...POSE, poseEstimate: undefined };
    for (const f of POSE.findings) expect(projectThroughPose(f, noPose, FRAME)).toBeNull();
    const observedMoon = POSE.findings.find((f) => f.id === 'finding-observed-moon')!;
    expect(projectThroughPose(observedMoon, POSE, FRAME)).toBeNull();
    // A simulated finding at a star's place is drawn by the app too, but never lined up over the photo
    const star = stars(POSE).find((f) => projectThroughPose(f, POSE, FRAME) !== null)!;
    const simulated = { ...star, evidenceClass: 'simulated', evidence: { ...star.evidence, evidenceClass: 'simulated' } } as CelestialFinding;
    expect(projectThroughPose(simulated, POSE, FRAME)).toBeNull();
    // A predicted_only solution has no WCS and no pose
    for (const f of predicted(sample('horizon_predicted_only'))) {
      expect(projectThroughPose(f, sample('horizon_predicted_only'), FRAME)).toBeNull();
    }
  });

  it('takes the frame edges at the outer pixel edges, half a pixel beyond the first and last pixel centres', () => {
    const wcs = POSE.wcs!;
    const at = (xPx: number, yPx: number): CelestialFinding => {
      const sky = pixelToWorldWCS(xPx, yPx, wcs);
      return { ...stars(POSE)[0]!, skyPosition: { raDeg: sky.raDeg, decDeg: sky.decDeg, uncertaintyArcsec: 55 } };
    };
    const inside = (x: number, y: number) => projectThroughPose(at(x, y), POSE, FRAME) !== null;
    expect(inside(-0.49, 270)).toBe(true);
    expect(inside(-0.51, 270)).toBe(false);
    expect(inside(959.49, 270)).toBe(true);
    expect(inside(959.51, 270)).toBe(false);
    expect(inside(480, -0.49)).toBe(true);
    expect(inside(480, -0.51)).toBe(false);
    expect(inside(480, 539.49)).toBe(true);
    expect(inside(480, 539.51)).toBe(false);
    // Without a frame given, the frame is the one the WCS centres on (crpix at its middle, horizon_solver.py)
    expect(projectThroughPose(at(959.49, 270), POSE) !== null).toBe(true);
    expect(projectThroughPose(at(959.51, 270), POSE)).toBeNull();
  });

  it('refuses a point behind the nominal camera even where its projection would land in the frame', () => {
    // Exactly opposite the frame centre, a TAN projection's numbers land on crpix; the swept WCS both look at the
    // point, and a centroid term of 100 deg puts every point it measures 80 deg from the frame centre, in front of
    // the camera, so only the nominal camera's own check refuses it
    const wcs = POSE.wcs!;
    const opposite = { raDeg: (wcs.crvalRaDeg + 180) % 360, decDeg: -wcs.crvalDecDeg, uncertaintyArcsec: 55 };
    const finding: CelestialFinding = { ...stars(POSE)[0]!, skyPosition: opposite };
    const facing = { ...wcs, crvalRaDeg: opposite.raDeg, crvalDecDeg: opposite.decDeg };
    for (const beaconCentroidUncertaintyDeg of [POSE.poseEstimate!.beaconCentroidUncertaintyDeg, 100]) {
      const solution: ObservationSolution = {
        ...POSE,
        poseEstimate: { ...POSE.poseEstimate!, sweptWcs: [facing, facing], beaconCentroidUncertaintyDeg },
      };
      expect(projectThroughPose(finding, solution, FRAME)).toBeNull();
    }
  });

  it('places nothing through a pose estimate on a solution that is not approximate, which the contract refuses', () => {
    // A session built without parsing (the discovered-media builder) could carry one; it is refused here too
    for (const status of ['predicted_only', 'calibrated', 'unresolved'] as const) {
      const wrong = { ...POSE, status } as ObservationSolution;
      for (const f of stars(POSE)) expect(projectThroughPose(f, wrong, FRAME)).toBeNull();
    }
  });

  it('refuses a point in front of the nominal WCS but behind the camera under one swept WCS', () => {
    const finding = stars(POSE).find((f) => projectThroughPose(f, POSE, FRAME) !== null)!;
    const sky = finding.skyPosition!;
    // One swept WCS turned to look the other way: the star is behind it
    const away: WCSCalibrationModel = {
      ...POSE.poseEstimate!.sweptWcs[0]!,
      crvalRaDeg: (sky.raDeg + 180) % 360,
      crvalDecDeg: -sky.decDeg,
    };
    const turned: ObservationSolution = {
      ...POSE,
      poseEstimate: { ...POSE.poseEstimate!, sweptWcs: [...POSE.poseEstimate!.sweptWcs.slice(1), away] },
    };
    expect(projectThroughPose(finding, turned, FRAME)).toBeNull();
  });
});

describe("the ring's centroid term at the finding's own place (the verdict's note on the ring)", () => {
  // A 1920 x 1080 frame 110 deg wide: its half width is tan(55 deg) in the TAN plane
  const HALF_W = 960;
  const HALF_H = 540;
  const SCALE = (Math.tan(55 * RAD) / HALF_W) / RAD;
  const fov = (halfPx: number) => (2 * Math.atan(halfPx * SCALE * RAD)) / RAD;
  const WIDE: WCSCalibrationModel = {
    ...POSE.wcs!,
    crpixX: HALF_W,
    crpixY: HALF_H,
    scaleDegPerPx: SCALE,
    fovHorizontalDeg: fov(HALF_W),
    fovVerticalDeg: fov(HALF_H),
  };
  const WIDE_FRAME = { widthPx: 2 * HALF_W, heightPx: 2 * HALF_H };
  // Both swept WCS are the nominal one, so the sweep adds nothing and the ring is the centroid term alone
  const lineUp = (wcs: WCSCalibrationModel, centroidDeg: number): ObservationSolution =>
    ObservationSolutionSchema.parse({
      ...POSE,
      wcs,
      poseEstimate: { ...POSE.poseEstimate!, sweptWcs: [wcs, wcs], beaconCentroidUncertaintyDeg: centroidDeg },
    });
  const at = (wcs: WCSCalibrationModel, xPx: number, yPx: number): CelestialFinding => {
    const sky = pixelToWorldWCS(xPx, yPx, wcs);
    return { ...stars(POSE)[0]!, skyPosition: { raDeg: ((sky.raDeg % 360) + 360) % 360, decDeg: sky.decDeg, uncertaintyArcsec: 55 } };
  };

  it('is a 110 deg frame', () => {
    expect(fov(HALF_W)).toBeCloseTo(110, 9);
  });

  it('covers every point 1 deg away from a finding near a corner, in 360 bearings', () => {
    const solution = lineUp(WIDE, 1);
    for (const [x, y] of [[40, 40], [1880, 40], [40, 1040], [1880, 1040]] as const) {
      const finding = at(WIDE, x, y);
      const lined = projectThroughPose(finding, solution, WIDE_FRAME)!;
      expect(lined).not.toBeNull();
      const farthest = centroidAtPlacePx(finding, WIDE, 1);
      expect(lined.ringPx).toBeGreaterThanOrEqual(farthest);
      // Measured, not padded: within a part in a thousand of the farthest point
      expect(lined.ringPx).toBeLessThan(farthest * 1.001);
      // The centre's scale gives 1 / SCALE px, under half of what this corner needs
      expect(farthest).toBeGreaterThan(2 / SCALE);
    }
  });

  it("is the centre's scale at the frame centre, where the TAN plane is not stretched", () => {
    const finding = at(WIDE, HALF_W, HALF_H);
    const lined = projectThroughPose(finding, lineUp(WIDE, 1), WIDE_FRAME)!;
    // tan(1 deg) over the scale in radians, a part in ten thousand above 1 / SCALE
    expect(lined.ringPx).toBeCloseTo(Math.tan(RAD) / (SCALE * RAD), 6);
    // Every bearing stretches alike here, so the 360 agree with the 8 up to float rounding
    expect(lined.ringPx).toBeGreaterThanOrEqual(centroidAtPlacePx(finding, WIDE, 1) - 1e-9);
  });

  it('covers the points square to the line to the centre, where barrel distortion stretches the sky most', () => {
    // k1 = -0.25, the contract's limit: 42 deg off axis (tan 0.9) the TAN plane stretches the sky 1.81 times toward
    // the centre and 1.35 times across, and the distortion shrinks the first to 0.39 of it and the second to 0.80
    const barrel: WCSCalibrationModel = { ...WIDE, radialDistortionK1: -0.25 };
    const rDist = 0.9 * (1 - 0.25 * 0.81);
    const finding = at(barrel, HALF_W + rDist / (SCALE * RAD), HALF_H);
    const lined = projectThroughPose(finding, lineUp(barrel, 1), WIDE_FRAME)!;
    expect(lined).not.toBeNull();
    const farthest = centroidAtPlacePx(finding, barrel, 1);
    // Within the 0.08 % the 8 bearings were measured to fall short by with distortion (poseProjection.ts)
    expect(lined.ringPx).toBeGreaterThanOrEqual(farthest * (1 - 8e-4));
    expect(lined.ringPx).toBeLessThan(farthest * 1.001);
    // Toward and away from the centre alone fall well short here
    const sky = finding.skyPosition!;
    const nominal = worldToPixelWCS(sky.raDeg, sky.decDeg, barrel);
    const radial = Math.max(
      ...[0, 180].map((bearingOffset) => {
        // The bearing toward the centre, from the destination formula's inverse
        const c = { raDeg: barrel.crvalRaDeg * RAD, decDeg: barrel.crvalDecDeg * RAD };
        const [ra, dec] = [sky.raDeg * RAD, sky.decDeg * RAD];
        const toward =
          Math.atan2(
            Math.sin(c.raDeg - ra) * Math.cos(c.decDeg),
            Math.cos(dec) * Math.sin(c.decDeg) - Math.sin(dec) * Math.cos(c.decDeg) * Math.cos(c.raDeg - ra)
          ) / RAD;
        const p = offsetOnSky(sky.raDeg, sky.decDeg, 1, toward + bearingOffset);
        const q = worldToPixelWCS(p.raDeg, p.decDeg, barrel);
        return Math.hypot(q.uPx - nominal.uPx, q.vPx - nominal.vPx);
      })
    );
    expect(radial).toBeLessThan(0.8 * farthest);
  });

  it('places nothing when a point the centroid term away lies behind the camera', () => {
    // A 170.6 deg frame at 1 deg per pixel, 1400 px wide: a finding 85 deg off axis lies inside it
    const wcs: WCSCalibrationModel = { ...WIDE, crpixX: 700, crpixY: 700, scaleDegPerPx: 1, fovHorizontalDeg: 170.6, fovVerticalDeg: 170.6 };
    const frame = { widthPx: 1400, heightPx: 1400 };
    const finding = at(wcs, 700 + Math.tan(85 * RAD) / RAD, 700);
    // 4 deg further out is 89 deg off axis, still in front; 6 deg further is behind the camera
    expect(projectThroughPose(finding, lineUp(wcs, 4), frame)).not.toBeNull();
    expect(projectThroughPose(finding, lineUp(wcs, 6), frame)).toBeNull();
  });
});

const ORIENTATIONS: DisplayOrientation[] = [0, 1, 2, 3].flatMap((quarterTurnsClockwise) =>
  [false, true].map((mirrored) => ({ quarterTurnsClockwise, mirrored }) as DisplayOrientation)
);

describe('P-SKY-8: lined-up markers sit at the nominal projection, ringed by the sweep, and only with a pose', () => {
  it('PBT: for generated approximate solutions, crops and orientations', () => {
    let runs = 0;
    let placed = 0;
    let placedOnScreen = 0;
    const sweptArb = fc.array(
      fc.record({
        dRa: fc.double({ min: -3, max: 3, noNaN: true }),
        dDec: fc.double({ min: -3, max: 3, noNaN: true }),
        scaleRatio: fc.double({ min: 0.85, max: 1.15, noNaN: true }),
        dRot: fc.double({ min: -10, max: 10, noNaN: true }),
      }),
      { minLength: 2, maxLength: 9 }
    );
    fc.assert(
      fc.property(
        fc.record({
          ra: fc.double({ min: 0, max: 359.9, noNaN: true }),
          dec: fc.double({ min: -75, max: 75, noNaN: true }),
          scale: fc.double({ min: 0.01, max: 0.09, noNaN: true }),
          rotation: fc.double({ min: -180, max: 180, noNaN: true }),
          parity: fc.constantFrom<1 | -1>(1, -1),
          halfW: fc.integer({ min: 100, max: 1000 }),
          halfH: fc.integer({ min: 100, max: 1000 }),
        }),
        sweptArb,
        fc.double({ min: 0, max: 1, noNaN: true }),
        // Where the finding is, as a fraction of the frame: some inside, some outside
        // Drawn without fast-check's bias toward tiny values, which would put nearly every finding on the frame corner
        fc.record({
          u: fc.noBias(fc.integer({ min: -150, max: 1150 })).map((k) => k / 1000),
          v: fc.noBias(fc.integer({ min: -150, max: 1150 })).map((k) => k / 1000),
        }),
        fc.record({
          zoom: fc.double({ min: 1, max: 1.6, noNaN: true }),
          panX: fc.double({ min: -0.15, max: 0.15, noNaN: true }),
          panY: fc.double({ min: -0.15, max: 0.15, noNaN: true }),
        }),
        fc.constantFrom(...ORIENTATIONS),
        fc.record({ w: fc.integer({ min: 320, max: 2560 }), h: fc.integer({ min: 320, max: 1600 }) }),
        (n, swept, centroidDeg, at, crop, orientation, view) => {
          runs += 1;
          const widthPx = 2 * n.halfW;
          const heightPx = 2 * n.halfH;
          // As services/science/wcs_models.py WCSModel.fov_horizontal_deg works it out
          const fov = (px: number) => Math.min(175, (2 * Math.atan(((0.5 * px * n.scale) * Math.PI) / 180) * 180) / Math.PI);
          const wcs: WCSCalibrationModel = {
            ...POSE.wcs!,
            crvalRaDeg: n.ra, crvalDecDeg: n.dec, crpixX: n.halfW, crpixY: n.halfH, scaleDegPerPx: n.scale,
            rotationDeg: n.rotation, parity: n.parity, fovHorizontalDeg: fov(widthPx), fovVerticalDeg: fov(heightPx),
          };
          const sweptWcs = swept.map((s) => ({
            ...wcs,
            crvalRaDeg: (((n.ra + s.dRa) % 360) + 360) % 360,
            crvalDecDeg: n.dec + s.dDec,
            scaleDegPerPx: n.scale * s.scaleRatio,
            rotationDeg: n.rotation + s.dRot,
          }));
          const sky = pixelToWorldWCS(at.u * widthPx - 0.5, at.v * heightPx - 0.5, wcs);
          const template = stars(POSE)[0]!;
          const finding: CelestialFinding = {
            ...template,
            skyPosition: { raDeg: ((sky.raDeg % 360) + 360) % 360, decDeg: sky.decDeg, uncertaintyArcsec: 55 },
          };
          const solution = ObservationSolutionSchema.parse({
            ...POSE,
            wcs,
            findings: [...POSE.findings.filter((f) => f.evidenceClass === 'observed'), finding],
            poseEstimate: { ...POSE.poseEstimate!, sweptWcs, beaconCentroidUncertaintyDeg: centroidDeg },
          });
          const base = poseSession(solution);
          const session: ObservationSession = {
            ...base,
            sourceAsset: { ...base.sourceAsset!, width: widthPx, height: heightPx, displayOrientation: orientation },
            cropTransform: crop,
          };
          const lined = projectThroughPose(finding, solution, { widthPx, heightPx });
          const pos = projectFindingToViewport(finding, session, view.w, view.h);
          if (lined === null) {
            expect(pos.visible).toBe(false);
          } else {
            placed += 1;
            // At the nominal projection, mapped through crop and orientation like a detection
            const nominal = worldToPixelWCS(finding.skyPosition!.raDeg, finding.skyPosition!.decDeg, wcs);
            expect(lined.xPx).toBeCloseTo(nominal.uPx, 6);
            expect(lined.yPx).toBeCloseTo(nominal.vPx, 6);
            const expected = sourcePixelToViewport(nominal.uPx, nominal.vPx, widthPx, heightPx, view.w, view.h, crop, orientation);
            const onScreen = expected.xPx >= 0 && expected.xPx <= view.w && expected.yPx >= 0 && expected.yPx <= view.h;
            expect(pos.visible).toBe(onScreen);
            if (onScreen) {
              placedOnScreen += 1;
              expect(pos.xPct).toBeCloseTo((expected.xPx / view.w) * 100, 6);
              expect(pos.yPct).toBeCloseTo((expected.yPx / view.h) * 100, 6);
              // The ring is at least the largest swept distance, and scales with the photo on screen
              const sweptPx = largestSweptDistancePx(finding, solution);
              expect(lined.ringPx).toBeGreaterThanOrEqual(sweptPx);
              // Plus the centroid term at the finding's own place, measured here in 72 bearings: 5 deg apart, so
              // the farthest of them falls short of the farthest point by at most 1 - cos(2.5 deg), under 0.1 %
              const centroidPx = centroidAtPlacePx(finding, wcs, centroidDeg, 72);
              expect(lined.ringPx).toBeGreaterThanOrEqual(sweptPx + centroidPx - 1e-9);
              expect(lined.ringPx).toBeLessThanOrEqual(sweptPx + centroidPx * 1.001 + 1e-9);
              const shown = orientedSize(widthPx, heightPx, orientation);
              const rect = sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, view.w, view.h, crop);
              expect(pos.ringPx).toBeCloseTo((lined.ringPx * rect.widthPx) / shown.widthPx, 6);
            }
          }
          // Without a pose estimate, no predicted marker over the photo (REQ-SKY-6.6)
          const bare = { ...session, observationSolution: { ...solution, poseEstimate: undefined } };
          expect(projectFindingToViewport(finding, bare, view.w, view.h).visible).toBe(false);
        }
      ),
      { seed: SEED, numRuns: 150 }
    );
    // The property placed markers on screen, not only refused them: about half the findings are drawn inside the frame
    expect(placedOnScreen, `${placed} of ${runs} placed, ${placedOnScreen} on screen`).toBeGreaterThan(runs / 10);
  });

  it('places every in-frame star of the fixture at its nominal projection on screen', () => {
    const session = poseSession();
    for (const finding of stars(POSE)) {
      const lined = projectThroughPose(finding, POSE, FRAME);
      const pos = projectFindingToViewport(finding, session, 1440, 900);
      expect(pos.visible).toBe(lined !== null);
      if (lined) {
        const p = sourcePixelToViewport(lined.xPx, lined.yPx, 960, 540, 1440, 900, session.cropTransform);
        expect(pos.xPx).toBe(Math.round(p.xPx));
        expect(pos.yPx).toBe(Math.round(p.yPx));
        // 960 x 540 fills a 1440 x 900 window at 1.5 times
        expect(pos.ringPx).toBeCloseTo(lined.ringPx * 1.5, 9);
      }
    }
  });
});
