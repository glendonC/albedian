import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  AuthorizedCalibrationProof,
  evaluateWCSVerificationGate,
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
  promoteFindingToCatalogMatched,
  validateEvidenceTransition,
} from '../index.js';
import { CelestialFinding, EvidenceClass } from '@albedian/contracts';

describe('@albedian/evidence — PBT Suite #3 (Epistemic Integrity) & EXP-12', () => {
  it('PBT-EVID-1: No Predicted or Simulated finding may ever become Observed, nor become Catalog Matched without valid pixel+WCS proof', () => {
    const evidenceClassArb = fc.constantFrom<EvidenceClass>(
      'observed',
      'catalog_matched',
      'predicted',
      'simulated'
    );

    const invalidProofArb = fc.record({
      snr: fc.double({ min: 0.1, max: 20, noNaN: true }),
      heldOutInliers: fc.integer({ min: 0, max: 20 }),
      medianResidualPx: fc.double({ min: 0.05, max: 15, noNaN: true }),
      pixelResidualPx: fc.double({ min: 0.05, max: 15, noNaN: true }),
    });

    fc.assert(
      fc.property(
        evidenceClassArb,
        evidenceClassArb,
        fc.option(invalidProofArb),
        (fromClass, toClass, rawProof) => {
          const proof: AuthorizedCalibrationProof | undefined = rawProof
            ? {
                kind: 'AuthorizedCalibrationProof',
                wcsSolutionId: 'wcs_test',
                sourceDetection: {
                  assetId: 'src_1',
                  frameTimeSec: 0,
                  xPx: 500,
                  yPx: 400,
                  flux: 1200,
                  snr: rawProof.snr,
                  fwhmPx: 2.4,
                  centroidUncertaintyPx: 0.25,
                },
                catalogRef: {
                  id: 'cat_betelgeuse',
                  provider: 'Gaia DR3',
                  releaseVersion: 'DR3-2022',
                  catalogSourceId: 'HIP 27989',
                  commonName: 'Betelgeuse',
                  bayerDesignation: 'Alpha Orionis',
                  constellation: 'Orion',
                  objectType: 'star',
                  coordinateEpoch: 'J2000',
                  raDeg: 88.7929,
                  decDeg: 7.4071,
                  apparentMagnitude: 0.42,
                  bailerJonesDistancePc: {
                    lower: 152,
                    median: 168,
                    upper: 190,
                    method: 'photogeometric_bailer_jones_edr3',
                  },
                  citation: 'ESA Gaia DR3 / HIP 27989',
                },
                heldOutInliers: rawProof.heldOutInliers,
                medianResidualPx: rawProof.medianResidualPx,
                angularResidualArcsec: rawProof.pixelResidualPx * 30,
                pixelResidualPx: rawProof.pixelResidualPx,
              }
            : undefined;

          const decision = validateEvidenceTransition(fromClass, toClass, proof);

          // Invariant 1: Nothing non-observed can EVER become 'observed'
          if (fromClass !== 'observed' && toClass === 'observed') {
            expect(decision.allowed).toBe(false);
          }

          // Invariant 2: Transition to 'catalog_matched' from any other class requires a valid proof
          if (fromClass !== 'catalog_matched' && toClass === 'catalog_matched') {
            const hasValidProof =
              proof !== undefined &&
              proof.sourceDetection.snr >= 3.5 &&
              proof.heldOutInliers >= 6 &&
              proof.medianResidualPx <= 3.0 &&
              proof.pixelResidualPx <= 4.5 &&
              fromClass !== 'observed'
                ? proof !== undefined &&
                  proof.sourceDetection.snr >= 3.5 &&
                  proof.heldOutInliers >= 6 &&
                  proof.medianResidualPx <= 3.0 &&
                  proof.pixelResidualPx <= 4.5
                : proof !== undefined &&
                  proof.sourceDetection.snr >= 3.5 &&
                  proof.heldOutInliers >= 6 &&
                  proof.medianResidualPx <= 3.0 &&
                  proof.pixelResidualPx <= 4.5;

            if (!hasValidProof) {
              expect(decision.allowed).toBe(false);
            }
          }
        }
      ),
      { numRuns: 200 }
    );
  });

  it('PBT-EVID-2: WCS verification gate only returns calibrated when heldOutInliers >= 6 and residuals are within bounds', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 30 }),
        fc.integer({ min: 0, max: 30 }),
        fc.integer({ min: 0, max: 25 }),
        fc.double({ min: 0.01, max: 12.0, noNaN: true }),
        fc.double({ min: 0.01, max: 15.0, noNaN: true }),
        fc.boolean(),
        (detected, matched, heldOut, medianRes, p95Res, hasMeta) => {
          const gate = evaluateWCSVerificationGate({
            detectedCandidateCount: detected,
            matchedInlierCount: matched,
            heldOutInliers: heldOut,
            medianResidualPx: medianRes,
            p95ResidualPx: p95Res,
            hasTimestampAndLocation: hasMeta,
          });

          if (gate.status === 'calibrated') {
            expect(gate.passedIndependentVerification).toBe(true);
            expect(heldOut).toBeGreaterThanOrEqual(6);
            expect(matched).toBeGreaterThanOrEqual(6);
            expect(medianRes).toBeLessThanOrEqual(3.0);
            expect(p95Res).toBeLessThanOrEqual(6.0);
          } else {
            expect(gate.passedIndependentVerification).toBe(false);
          }
        }
      ),
      { numRuns: 150 }
    );
  });

  it('EXP-12: Distinguishes all four EvidenceClasses by shape, shortCode, and disclosure without relying on color alone', () => {
    const classes: EvidenceClass[] = [
      'observed',
      'catalog_matched',
      'predicted',
      'simulated',
    ];
    const badges = classes.map((c) => getEpistemicBadgeDescriptor(c));

    const shapes = new Set(badges.map((b) => b.markerShape));
    const codes = new Set(badges.map((b) => b.shortCode));
    const labels = new Set(badges.map((b) => b.label));

    expect(shapes.size).toBe(4);
    expect(codes.size).toBe(4);
    expect(labels.size).toBe(4);

    // Verify Bailer-Jones distance formatting includes uncertainty interval
    const formatted = formatDistanceWithUncertainty({
      lower: 198.0,
      median: 264.5,
      upper: 342.0,
      method: 'photogeometric_bailer_jones_edr3',
    });
    expect(formatted.canEnter3DSpace).toBe(true);
    expect(formatted.parsecText).toContain('264.5 pc (198.0–342.0 pc)');
    expect(formatted.uncertaintyText).toContain('photogeometric_bailer_jones_edr3');
  });

  it('promotes an observed finding to catalog_matched and computes 3D ICRS parsec coordinates when proof is valid', () => {
    const baseFinding: CelestialFinding = {
      id: 'find_01',
      observationSolutionId: 'sol_01',
      evidenceClass: 'observed',
      title: 'Detected Point Source #1',
      subtitle: 'SNR 18.4 · (642.1, 318.4)',
      sourceDetection: {
        assetId: 'src_01',
        frameTimeSec: 4.0,
        xPx: 642.1,
        yPx: 318.4,
        flux: 4820,
        snr: 18.4,
        fwhmPx: 2.2,
        centroidUncertaintyPx: 0.15,
      },
      skyPosition: {
        raDeg: 78.6345,
        decDeg: -8.2016,
        uncertaintyArcsec: 12.0,
      },
      evidence: {
        id: 'ev_01',
        evidenceClass: 'observed',
        verificationMethod: 'DAOStarFinder local peak SNR >= 3.5',
        sourceAssetSha256: '0123456789abcdef0123456789abcdef',
        limitations: ['Pixel detection only prior to WCS verification'],
        verifiedByIndependentHoldout: false,
      },
    };

    const promoted = promoteFindingToCatalogMatched(baseFinding, {
      kind: 'AuthorizedCalibrationProof',
      wcsSolutionId: 'wcs_sahara_01',
      sourceDetection: baseFinding.sourceDetection!,
      catalogRef: {
        id: 'cat_rigel',
        provider: 'Gaia DR3',
        releaseVersion: 'DR3-2022',
        catalogSourceId: 'HIP 24436',
        commonName: 'Rigel',
        bayerDesignation: 'Beta Orionis',
        constellation: 'Orion',
        objectType: 'star',
        coordinateEpoch: 'J2000',
        raDeg: 78.6345,
        decDeg: -8.2016,
        apparentMagnitude: 0.13,
        bailerJonesDistancePc: {
          lower: 240.0,
          median: 264.5,
          upper: 290.0,
          method: 'photogeometric_bailer_jones_edr3',
        },
        citation: 'ESA Gaia DR3 / Hipparcos HIP 24436',
      },
      heldOutInliers: 10,
      medianResidualPx: 0.48,
      angularResidualArcsec: 14.2,
      pixelResidualPx: 0.39,
    });

    expect(promoted.evidenceClass).toBe('catalog_matched');
    expect(promoted.title).toBe('Rigel');
    expect(promoted.cartesianParsecICRS).toBeDefined();
    const [x, y, z] = promoted.cartesianParsecICRS!;
    const norm = Math.hypot(x, y, z);
    expect(norm).toBeCloseTo(264.5, 4);
  });
});
