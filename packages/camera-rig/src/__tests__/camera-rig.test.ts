import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CatalogReference,
  CelestialFinding,
  QuaternionTuple,
  WCSCalibrationModel,
} from '@albedian/contracts';
import {
  angularSeparationDeg,
  computeAnchorLockedPhaseAState,
  interpolateLogTanFov,
  lookAtDirectionQuaternion,
  pixelToWorldWCS,
  projectWorldPointWithCamera,
  propagateCatalogPositionToYear,
  querySightlineCone,
  raDecToUnitVector,
  rebaseFloatingOrigin,
  resolveFindingParsecPosition,
  slerpQuaternion,
  unitVectorToRaDec,
  worldToPixelWCS,
} from '../index.js';

describe('@albedian/camera-rig — Property-Based & Unit Verification', () => {
  it('PBT-COORD-1: raDecToUnitVector and unitVectorToRaDec preserve unit norm and round-trip', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 359.999999, noNaN: true }),
        fc.double({ min: -89.999, max: 89.999, noNaN: true }),
        (raDeg, decDeg) => {
          const vec = raDecToUnitVector(raDeg, decDeg);
          const norm = Math.hypot(vec[0], vec[1], vec[2]);
          expect(Math.abs(norm - 1)).toBeLessThan(1e-14);

          const recovered = unitVectorToRaDec(vec);
          const sepArcsec =
            angularSeparationDeg(raDeg, decDeg, recovered.raDeg, recovered.decDeg) *
            3600;
          expect(sepArcsec).toBeLessThan(1e-6);
        }
      ),
      { numRuns: 200 }
    );
  });

  it('PBT-CAM-1: pixelToWorldWCS and worldToPixelWCS round-trip within < 1e-4 px and < 1e-3 arcsec', () => {
    const wcsArb: fc.Arbitrary<WCSCalibrationModel> = fc.record({
      projection: fc.constant('TAN' as const),
      crvalRaDeg: fc.double({ min: 0, max: 360, noNaN: true }),
      crvalDecDeg: fc.double({ min: -85, max: 85, noNaN: true }),
      crpixX: fc.double({ min: 640, max: 1920, noNaN: true }),
      crpixY: fc.double({ min: 360, max: 1080, noNaN: true }),
      scaleDegPerPx: fc.double({ min: 0.005, max: 0.04, noNaN: true }),
      rotationDeg: fc.double({ min: -180, max: 180, noNaN: true }),
      radialDistortionK1: fc.double({ min: -0.08, max: 0.08, noNaN: true }),
      radialDistortionK2: fc.double({ min: -0.08, max: 0.08, noNaN: true }),
      parity: fc.constantFrom(1 as const, -1 as const),
      fovHorizontalDeg: fc.constant(50),
      fovVerticalDeg: fc.constant(30),
    });

    fc.assert(
      fc.property(
        wcsArb,
        fc.double({ min: -600, max: 600, noNaN: true }),
        fc.double({ min: -400, max: 400, noNaN: true }),
        (wcs, duPx, dvPx) => {
          const uPx = wcs.crpixX + duPx;
          const vPx = wcs.crpixY + dvPx;

          // 1. Pixel -> World -> Pixel
          const sky = pixelToWorldWCS(uPx, vPx, wcs);
          const reprojected = worldToPixelWCS(sky.raDeg, sky.decDeg, wcs);
          expect(reprojected.visible).toBe(true);

          const pixelErr = Math.hypot(reprojected.uPx - uPx, reprojected.vPx - vPx);
          expect(pixelErr).toBeLessThan(1e-6);

          // 2. World -> Pixel -> World
          const sky2 = pixelToWorldWCS(reprojected.uPx, reprojected.vPx, wcs);
          const angularErrArcsec =
            angularSeparationDeg(sky.raDeg, sky.decDeg, sky2.raDeg, sky2.decDeg) *
            3600;
          expect(angularErrArcsec).toBeLessThan(1e-3);
        }
      ),
      { numRuns: 250 }
    );
  });

  it('PBT-CAM-2: Off-axis anchor constraint keeps selected star drift < 0.01 px across Phase A [0, 1]', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 359.9, noNaN: true }),
        fc.double({ min: -75, max: 75, noNaN: true }),
        fc.double({ min: -12, max: 12, noNaN: true }),
        fc.double({ min: -8, max: 8, noNaN: true }),
        fc.double({ min: -0.75, max: 0.75, noNaN: true }),
        fc.double({ min: -0.75, max: 0.75, noNaN: true }),
        fc.double({ min: 28, max: 68, noNaN: true }),
        fc.double({ min: 18, max: 52, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (
          centerRa,
          centerDec,
          dRa,
          dDec,
          targetNdcX,
          targetNdcY,
          fovStartDeg,
          fovEndDeg,
          progress
        ) => {
          const anchorRaDeg = ((centerRa + dRa) % 360 + 360) % 360;
          const anchorDecDeg = Math.max(-85, Math.min(85, centerDec + dDec));

          const qStart = lookAtDirectionQuaternion(
            raDecToUnitVector(centerRa, centerDec)
          );
          const qEnd = lookAtDirectionQuaternion(
            raDecToUnitVector(anchorRaDeg, anchorDecDeg)
          );

          const state = computeAnchorLockedPhaseAState({
            anchorRaDeg,
            anchorDecDeg,
            anchorTargetNdc: [targetNdcX, targetNdcY],
            qStart,
            qEnd,
            fovStartDeg,
            fovEndDeg,
            aspect: 16 / 9,
            progress,
            viewportWidthPx: 1920,
            viewportHeightPx: 1080,
          });

          expect(state.anchorDriftPx).toBeLessThan(1e-5);
          expect(state.anchorDriftPx).toBeLessThan(0.01);
          expect(Math.abs(state.projectedAnchorNdc[0] - targetNdcX)).toBeLessThan(
            1e-12
          );
          expect(Math.abs(state.projectedAnchorNdc[1] - targetNdcY)).toBeLessThan(
            1e-12
          );
        }
      ),
      { numRuns: 200 }
    );
  });

  it('PBT-CAM-3: Floating-origin rebasing preserves local star coordinates within < 1e-4 pc up to 1000 pc', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -1000, max: 1000, noNaN: true }),
        fc.double({ min: -1000, max: 1000, noNaN: true }),
        fc.double({ min: -1000, max: 1000, noNaN: true }),
        fc.double({ min: -50, max: 50, noNaN: true }),
        fc.double({ min: -50, max: 50, noNaN: true }),
        fc.double({ min: -50, max: 50, noNaN: true }),
        (ox, oy, oz, lx, ly, lz) => {
          const catalogPositions = new Float64Array([ox + lx, oy + ly, oz + lz]);
          const rebased = rebaseFloatingOrigin(catalogPositions, [ox, oy, oz]);
          const errPc = Math.hypot(
            (rebased[0] ?? 0) - lx,
            (rebased[1] ?? 0) - ly,
            (rebased[2] ?? 0) - lz
          );
          expect(errPc).toBeLessThan(1e-4);
        }
      ),
      { numRuns: 250 }
    );
  });

  it('verifies slerpQuaternion shortest-arc hemisphere flip and log-tan-FOV uniformity', () => {
    const q0: QuaternionTuple = [0, 0, 0, 1];
    // Negated identity represents the exact same 3D orientation
    const q1Negated: QuaternionTuple = [0, 0, 0, -1];
    const mid = slerpQuaternion(q0, q1Negated, 0.5);
    expect(Math.hypot(mid[0], mid[1], mid[2], mid[3])).toBeCloseTo(1, 12);
    expect(Math.abs(mid[3])).toBeCloseTo(1, 12);

    // Log-tan-half-FOV midpoint satisfies tan(fovMid/2) = sqrt(tan(fov0/2) * tan(fov1/2))
    const fov0 = 60;
    const fov1 = 20;
    const fovMid = interpolateLogTanFov(fov0, fov1, 0.5);
    const tan0 = Math.tan((fov0 * Math.PI) / 360);
    const tan1 = Math.tan((fov1 * Math.PI) / 360);
    const tanMid = Math.tan((fovMid * Math.PI) / 360);
    expect(tanMid).toBeCloseTo(Math.sqrt(tan0 * tan1), 12);
  });

  it('PBT-COORD-1-SOUTH: raDecToUnitVector / unitVectorToRaDec round-trip at southern circumpolar declinations (decDeg ∈ [-90, -75])', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 359.999999, noNaN: true }),
        fc.double({ min: -89.999, max: -75.0, noNaN: true }),
        (raDeg, decDeg) => {
          const vec = raDecToUnitVector(raDeg, decDeg);
          const norm = Math.hypot(vec[0], vec[1], vec[2]);
          expect(Math.abs(norm - 1)).toBeLessThan(1e-14);

          const recovered = unitVectorToRaDec(vec);
          const sepArcsec =
            angularSeparationDeg(raDeg, decDeg, recovered.raDeg, recovered.decDeg) *
            3600;
          // Near the south pole RA wraps freely; verify via angular separation only
          expect(sepArcsec).toBeLessThan(1e-6);
        }
      ),
      { numRuns: 200 }
    );
  });

  it('PBT-CAM-2-SOUTH: Off-axis anchor constraint holds < 0.01 px drift at southern circumpolar declinations (decDeg ≈ -89.5)', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 359.9, noNaN: true }),
        // anchor dec pinned near south celestial pole
        fc.double({ min: -89.9, max: -75.0, noNaN: true }),
        fc.double({ min: -0.5, max: 0.5, noNaN: true }),
        fc.double({ min: -0.5, max: 0.5, noNaN: true }),
        fc.double({ min: 28, max: 68, noNaN: true }),
        fc.double({ min: 18, max: 52, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (anchorRaDeg, anchorDecDeg, targetNdcX, targetNdcY, fovStartDeg, fovEndDeg, progress) => {
          // Center the camera a few degrees away so the anchor is genuinely off-center
          const centerDecDeg = Math.min(-70, anchorDecDeg + 3);
          const qStart = lookAtDirectionQuaternion(
            raDecToUnitVector(anchorRaDeg, centerDecDeg)
          );
          const qEnd = lookAtDirectionQuaternion(
            raDecToUnitVector(anchorRaDeg, anchorDecDeg)
          );

          const state = computeAnchorLockedPhaseAState({
            anchorRaDeg,
            anchorDecDeg,
            anchorTargetNdc: [targetNdcX, targetNdcY],
            qStart,
            qEnd,
            fovStartDeg,
            fovEndDeg,
            aspect: 16 / 9,
            progress,
            viewportWidthPx: 1920,
            viewportHeightPx: 1080,
          });

          expect(state.anchorDriftPx).toBeLessThan(0.01);
        }
      ),
      { numRuns: 150 }
    );
  });

  it('propagates catalog proper motion and radial velocity across epochs and queries sightline cones', () => {
    const siriusRef: CatalogReference = {
      id: 'cat-sirius',
      provider: 'Hipparcos',
      releaseVersion: 'HIP-2',
      catalogSourceId: 'HIP 32349',
      commonName: 'Sirius',
      bayerDesignation: 'Alpha CMa',
      constellation: 'Canis Major',
      objectType: 'star',
      coordinateEpoch: 'J2026.0',
      raDeg: 101.287,
      decDeg: -16.716,
      apparentMagnitude: -1.46,
      properMotionRaMasYr: -546.01,
      properMotionDecMasYr: -1223.07,
      radialVelocityKmS: -5.5,
      bailerJonesDistancePc: {
        lower: 2.63,
        median: 2.64,
        upper: 2.65,
        method: 'geometric_parallax',
      },
      citation: 'Hipparcos New Reduction (van Leeuwen 2007)',
    };

    const atRef = propagateCatalogPositionToYear(siriusRef, 2026, 2026);
    expect(atRef.raDeg).toBeCloseTo(siriusRef.raDeg, 8);
    expect(atRef.decDeg).toBeCloseTo(siriusRef.decDeg, 8);

    const future = propagateCatalogPositionToYear(siriusRef, 5026, 2026);
    const sepDeg = angularSeparationDeg(
      siriusRef.raDeg,
      siriusRef.decDeg,
      future.raDeg,
      future.decDeg
    );
    // ~1.34 arcsec/yr * 3000 yr ≈ 4020 arcsec ≈ 1.116 deg
    expect(sepDeg).toBeGreaterThan(1.0);
    expect(sepDeg).toBeLessThan(1.25);

    const makeFinding = (
      id: string,
      raDeg: number,
      decDeg: number,
      distPc: number
    ): CelestialFinding => ({
      id,
      observationSolutionId: 'sol-1',
      evidenceClass: 'catalog_matched',
      title: id,
      subtitle: 'Star',
      skyPosition: { raDeg, decDeg, uncertaintyArcsec: 1.2 },
      inferredDistancePc: {
        lower: distPc * 0.9,
        median: distPc,
        upper: distPc * 1.1,
        method: 'photogeometric_bailer_jones_edr3',
      },
      cartesianParsecICRS: (() => {
        const u = raDecToUnitVector(raDeg, decDeg);
        return [u[0] * distPc, u[1] * distPc, u[2] * distPc] as [
          number,
          number,
          number,
        ];
      })(),
      evidence: {
        id: `ev-${id}`,
        evidenceClass: 'catalog_matched',
        verificationMethod: 'wcs_holdout',
        sourceAssetSha256: '0123456789abcdef'.repeat(4),
        limitations: ['Catalog distance estimate'],
        verifiedByIndependentHoldout: true,
      },
    });

    const target = makeFinding('Bellatrix', 81.28, 6.35, 77);
    const behind1 = makeFinding('Mintaka', 83.0, -0.3, 380);
    const behind2 = makeFinding('Alnilam', 84.05, -1.2, 412);
    const farAwayAngle = makeFinding('Vega', 279.23, 38.78, 7.68);

    const cone = querySightlineCone(
      [behind2, farAwayAngle, target, behind1],
      [0, 0, 0],
      target,
      18
    ) ?? [];

    expect(cone.map((c) => c.finding.id)).toEqual([
      'Bellatrix',
      'Mintaka',
      'Alnilam',
    ]);
    expect(cone[0]!.distanceFromObserverPc).toBeLessThan(
      cone[1]!.distanceFromObserverPc
    );
    expect(cone[1]!.distanceFromObserverPc).toBeLessThan(
      cone[2]!.distanceFromObserverPc
    );
  });
});

describe('@albedian/camera-rig: detections without a sky position', () => {
  const evidence = (id: string, evidenceClass: 'observed' | 'catalog_matched') => ({
    id: `ev-${id}`, evidenceClass, verificationMethod: 'test', sourceAssetSha256: '0123456789abcdef'.repeat(4),
    limitations: ['Generated for camera rig tests.'], verifiedByIndependentHoldout: evidenceClass === 'catalog_matched',
  });
  const pixelOnly: CelestialFinding = {
    id: 'finding-obs-1', observationSolutionId: 'sol-1', evidenceClass: 'observed', title: 'Observed Point Source #1',
    subtitle: 'Measured at (412.5, 300.2) px',
    sourceDetection: { assetId: 'src-1', frameTimeSec: 0, xPx: 412.5, yPx: 300.2, flux: 5000, snr: 25, fwhmPx: 2.5, centroidUncertaintyPx: 0.1 },
    evidence: evidence('finding-obs-1', 'observed'),
  };
  const bellatrix: CelestialFinding = {
    id: 'Bellatrix', observationSolutionId: 'sol-1', evidenceClass: 'catalog_matched', title: 'Bellatrix', subtitle: 'Star',
    skyPosition: { raDeg: 81.28, decDeg: 6.35, uncertaintyArcsec: 1.2 },
    inferredDistancePc: { lower: 69.3, median: 77, upper: 84.7, method: 'photogeometric_bailer_jones_edr3' },
    evidence: evidence('Bellatrix', 'catalog_matched'),
  };

  it('gives a pixel-only detection no 3D position and no sightline, and leaves it out of other cones', () => {
    expect(resolveFindingParsecPosition(pixelOnly)).toBeNull();
    expect(resolveFindingParsecPosition({ ...pixelOnly, inferredDistancePc: bellatrix.inferredDistancePc })).toBeNull();
    expect(querySightlineCone([pixelOnly, bellatrix], [0, 0, 0], pixelOnly)).toBeNull();
    expect(querySightlineCone([pixelOnly, bellatrix], [0, 0, 0], bellatrix)?.map((c) => c.finding.id)).toEqual(['Bellatrix']);
  });
});
