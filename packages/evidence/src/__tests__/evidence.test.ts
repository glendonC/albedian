import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  AuthorizedCalibrationProof,
  buildFindingEvidenceRecords,
  deriveEvidenceKind,
  deriveOperationalInstrumentStatus,
  evaluateWCSVerificationGate,
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
  promoteFindingToCatalogMatched,
  validateEvidenceTransition,
} from '../index.js';
import {
  CelestialFinding,
  CelestialFindingSchema,
  EvidenceClass,
  EvidenceRecordSchema,
  ObservationSolution,
  SolutionStatus,
  SourceAsset,
  WCSCalibrationModel,
} from '@albedian/contracts';

describe('@albedian/evidence: PBT Suite #3 (Epistemic Integrity) & EXP-12', () => {
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

  it('never calibrates when a held-out residual is missing', () => {
    const cases: Array<{ medianResidualPx?: number; p95ResidualPx?: number }> = [{}, { medianResidualPx: 0.5 }, { p95ResidualPx: 0.5 }];
    for (const residuals of cases) {
      const gate = evaluateWCSVerificationGate({
        detectedCandidateCount: 20, matchedInlierCount: 12, heldOutInliers: 8, hasTimestampAndLocation: true, ...residuals,
      });
      expect(gate.status).not.toBe('calibrated');
      expect(gate.passedIndependentVerification).toBe(false);
      expect(gate.reason).not.toMatch(/NaN|undefined/);
    }
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
    expect(formatted.parsecText).toBe('264.5 pc (198.0 to 342.0 pc)');
    expect(formatted.lightYearText).toBe('863 ly (646 to 1115 ly)');
    expect(formatted.uncertaintyText).toBe(
      '±72.0 pc: half the 16th to 84th percentile range of the Bailer-Jones et al. (2021) photogeometric distance'
    );
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
        sourceAssetSha256: '0123456789abcdef'.repeat(4),
        limitations: ['Pixel detection only prior to WCS verification'],
        verifiedByIndependentHoldout: false,
      },
    };

    const promoted = promoteFindingToCatalogMatched(baseFinding, {
      kind: 'AuthorizedCalibrationProof',
      wcsSolutionId: 'wcs_synthetic_01',
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

  it('derives OperationalInstrumentStatus, EvidenceKind, and structured EvidenceRecord provenance chain', () => {
    expect(deriveOperationalInstrumentStatus('pending')).toBe('REGISTERING');
    expect(deriveOperationalInstrumentStatus('calibrated')).toBe('MATCHED');
    expect(deriveOperationalInstrumentStatus('approximate')).toBe('RECONSTRUCTED');
    expect(deriveOperationalInstrumentStatus('predicted_only')).toBe('PROPAGATED');
    expect(deriveOperationalInstrumentStatus('ambiguous')).toBe('NO COVERAGE');
    expect(deriveOperationalInstrumentStatus('unresolved')).toBe('SOURCE UNAVAILABLE');

    const sourceAsset: SourceAsset = {
      id: 'asset_01',
      title: 'High-Altitude Night Field Recording',
      mediaKind: 'video',
      sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
      uri: '/data/uploads/night_field.mp4',
      width: 1920,
      height: 1080,
      durationMs: 12000,
      fpsNumerator: 30,
      fpsDenominator: 1,
      videoCodec: 'hevc',
      audioStreams: [],
      // A synthetic moment and place that point at nobody: the open North Atlantic
      capturedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: {
        latDeg: 30.0,
        lonDeg: -40.0,
        elevationM: 0,
        placeName: 'Synthetic place, open North Atlantic',
      },
      privacyClass: 'public_sample',
      immutable: true,
    };

    const catalogMatchedFinding: CelestialFinding = {
      id: 'finding_rigel',
      observationSolutionId: 'sol_01',
      evidenceClass: 'catalog_matched',
      title: 'Rigel',
      subtitle: 'Beta Orionis · Orion',
      sourceDetection: {
        assetId: 'asset_01',
        frameTimeSec: 2.5,
        xPx: 642.1,
        yPx: 318.4,
        flux: 4820,
        snr: 18.4,
        fwhmPx: 2.2,
        centroidUncertaintyPx: 0.15,
      },
      catalogRef: {
        id: 'cat_rigel',
        provider: 'Gaia DR3',
        releaseVersion: 'DR3-2022',
        catalogSourceId: 'HIP 24436',
        commonName: 'Rigel',
        bayerDesignation: 'Beta Orionis',
        constellation: 'Orion',
        objectType: 'star',
        coordinateEpoch: 'J2016.0',
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
      skyPosition: {
        raDeg: 78.6345,
        decDeg: -8.2016,
        uncertaintyArcsec: 14.2,
      },
      inferredDistancePc: {
        lower: 240.0,
        median: 264.5,
        upper: 290.0,
        method: 'photogeometric_bailer_jones_edr3',
      },
      evidence: {
        id: 'ev_rigel',
        evidenceClass: 'catalog_matched',
        verificationMethod: 'Triangle-matched WCS with held-out residual verification',
        sourceAssetSha256: sourceAsset.sha256,
        wcsSolutionId: 'wcs_01',
        catalogReferenceId: 'cat_rigel',
        angularResidualArcsec: 14.2,
        pixelResidualPx: 0.39,
        limitations: ['68% Bailer-Jones photogeometric interval'],
        verifiedByIndependentHoldout: true,
      },
    };

    const uncatalogedObservedFinding: CelestialFinding = {
      id: 'finding-field-star-101',
      observationSolutionId: 'sol_01',
      evidenceClass: 'observed',
      title: 'Uncataloged Sensor Detection #101',
      subtitle: 'SNR 6.8 · (412.0, 210.5)',
      sourceDetection: {
        assetId: 'asset_01',
        frameTimeSec: 2.5,
        xPx: 412.0,
        yPx: 210.5,
        flux: 520,
        snr: 6.8,
        fwhmPx: 2.1,
        centroidUncertaintyPx: 0.31,
      },
      skyPosition: {
        raDeg: 81.12,
        decDeg: -4.85,
        uncertaintyArcsec: 18.0,
      },
      evidence: {
        id: 'ev_101',
        evidenceClass: 'observed',
        verificationMethod: 'DAOStarFinder local peak SNR >= 3.5',
        sourceAssetSha256: sourceAsset.sha256,
        limitations: ['No static catalog reference entry within match radius'],
        verifiedByIndependentHoldout: false,
      },
    };

    const solution: ObservationSolution = {
      id: 'sol_01',
      sourceAssetId: sourceAsset.id,
      sourceSha256: sourceAsset.sha256,
      frameTimeSec: 2.5,
      revision: 1,
      status: 'calibrated',
      observedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: {
        latDeg: 30.0,
        lonDeg: -40.0,
        placeName: 'Synthetic place, open North Atlantic',
      },
      solver: {
        name: 'AlbedianTriangleSolver',
        version: '1.0.0',
        catalogRelease: '18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)',
      },
      wcs: {
        projection: 'TAN',
        crvalRaDeg: 83.82,
        crvalDecDeg: -5.39,
        crpixX: 960,
        crpixY: 540,
        scaleDegPerPx: 0.032,
        rotationDeg: 1.4,
        radialDistortionK1: -0.002,
        radialDistortionK2: 0.0001,
        parity: 1,
        fovHorizontalDeg: 61.4,
        fovVerticalDeg: 34.5,
      },
      quality: {
        detectedCandidateCount: 16,
        matchedInlierCount: 12,
        heldOutPairCount: 9,
        heldOutInliers: 8,
        medianResidualPx: 0.42,
        p95ResidualPx: 0.91,
        medianResidualArcsec: 15.1,
        passedIndependentVerification: true,
      },
      findings: [catalogMatchedFinding, uncatalogedObservedFinding],
      warnings: [],
    };

    expect(deriveEvidenceKind(catalogMatchedFinding, 'calibrated')).toBe('registered');
    expect(deriveEvidenceKind(uncatalogedObservedFinding, 'calibrated')).toBe('captured');
    expect(deriveOperationalInstrumentStatus('calibrated', catalogMatchedFinding)).toBe('MATCHED');
    expect(deriveOperationalInstrumentStatus('calibrated', uncatalogedObservedFinding)).toBe(
      'NO COVERAGE'
    );

    const records = buildFindingEvidenceRecords(sourceAsset, solution, catalogMatchedFinding);
    expect(records).toHaveLength(3);
    for (const rec of records) {
      expect(() => EvidenceRecordSchema.parse(rec)).not.toThrow();
      expect(rec.sourceIdentifier).toBeTruthy();
      expect(rec.observationTimeUtc).toBe('2030-01-01T02:00:00Z');
      expect(rec.modelEpochUtc).toBeTruthy();
      expect(rec.coordinateFrame).toBeTruthy();
      expect(rec.uncertaintyDescription).toBeTruthy();
      expect(rec.coverageDescription).toBeTruthy();
    }
    expect(records[0]?.kind).toBe('captured');
    expect(records[1]?.kind).toBe('registered');
    expect(records[2]?.kind).toBe('catalog-derived');
    expect(records[2]?.sourceIdentifier).toBe('Gaia DR3:HIP 24436');

    const uncatalogedRecords = buildFindingEvidenceRecords(
      sourceAsset,
      solution,
      uncatalogedObservedFinding
    );
    expect(uncatalogedRecords).toHaveLength(3);
    expect(uncatalogedRecords[2]?.kind).toBe('captured');
    expect(uncatalogedRecords[2]?.sourceIdentifier).toBe('uncataloged:finding-field-star-101');
  });
});

describe('@albedian/evidence: detections without a sky position', () => {
  const sourceAsset: SourceAsset = {
    id: 'asset_02', title: 'Hazy Field Recording', mediaKind: 'image', sha256: 'b1c2d3e4f5a60718293a4b5c6d7e8f90b1c2d3e4f5a60718293a4b5c6d7e8f90',
    uri: '/data/uploads/hazy_field.jpg', width: 960, height: 540, durationMs: 0, fpsNumerator: 1, fpsDenominator: 1,
    videoCodec: 'none', audioStreams: [], privacyClass: 'private', immutable: true,
  };
  const pixelOnly = (xPx = 733.0, yPx = 120.4): CelestialFinding => ({
    id: 'finding-obs-3', observationSolutionId: 'sol_02', evidenceClass: 'observed',
    title: 'Observed Point Source #3', subtitle: `Measured at (${xPx.toFixed(1)}, ${yPx.toFixed(1)}) px · SNR 25.0`,
    sourceDetection: { assetId: sourceAsset.id, frameTimeSec: 0, xPx, yPx, flux: 5000, snr: 25, fwhmPx: 2.5, centroidUncertaintyPx: 0.1 },
    evidence: {
      id: 'ev-finding-obs-3', evidenceClass: 'observed', verificationMethod: 'Local Matched-Filter Centroid Extraction',
      sourceAssetSha256: sourceAsset.sha256, limitations: ['Pixel position only: no WCS places it on the sky.'],
      verifiedByIndependentHoldout: false,
    },
  });
  const approximateWcs: WCSCalibrationModel = {
    projection: 'TAN', crvalRaDeg: 222.4, crvalDecDeg: -14.2, crpixX: 480, crpixY: 270, scaleDegPerPx: 0.04,
    rotationDeg: 3.1, radialDistortionK1: 0, radialDistortionK2: 0, parity: -1, fovHorizontalDeg: 37.9, fovVerticalDeg: 21.5,
  };
  // observedAtUTC is a synthetic moment that points at nobody
  const solutionFor = (finding: CelestialFinding, status: SolutionStatus, wcs?: WCSCalibrationModel): ObservationSolution => ({
    id: 'sol_02', sourceAssetId: sourceAsset.id, sourceSha256: sourceAsset.sha256, frameTimeSec: 0, revision: 1, status,
    observedAtUTC: '2030-11-01T22:32:00Z', solver: { name: 'AlbedianTriangleSolver', version: '1.0.0', catalogRelease: '18 pinned bright stars (Gaia DR3 and Hipparcos 2007 rows)' },
    wcs,
    // No held-out star paired with the catalog, so no held-out residual
    quality: { detectedCandidateCount: 8, matchedInlierCount: 0, heldOutPairCount: 0, heldOutInliers: 0, passedIndependentVerification: false },
    findings: [finding], warnings: [],
  });

  it('PBT-EVID-2: a detection with no WCS keeps only its capture record and is given no sky coordinates', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 959, noNaN: true }),
        fc.double({ min: 0, max: 539, noNaN: true }),
        fc.constantFrom<SolutionStatus>('pending', 'unresolved', 'ambiguous', 'predicted_only'),
        (xPx, yPx, status) => {
          const finding = pixelOnly(xPx, yPx);
          const records = buildFindingEvidenceRecords(sourceAsset, solutionFor(finding, status), finding);
          expect(records.map((r) => r.kind)).toEqual(['captured']);
          expect(records[0]?.title).toBe(`Sensor Pixel Centroid (${xPx.toFixed(1)}, ${yPx.toFixed(1)}) px`);
          expect(JSON.stringify(records)).not.toMatch(/RA |Dec |raDeg/);
        }
      ),
      { numRuns: 100 }
    );
  });

  it('records the unverified pose a detection belongs to without placing the detection itself', () => {
    const finding = pixelOnly();
    const records = buildFindingEvidenceRecords(sourceAsset, solutionFor(finding, 'approximate', approximateWcs), finding);
    expect(records.map((r) => r.kind)).toEqual(['captured', 'reconstructed']);
    expect(records[1]?.coverageDescription).toContain('FOV 37.9°×21.5°');
    expect(records.some((r) => r.title.startsWith('Uncataloged Sensor Locus'))).toBe(false);
  });

  it('describes a pose with no held-out residual without inventing one', () => {
    const finding = pixelOnly();
    // The pose paired no held-out star, so its quality carries no held-out residual
    const solution = solutionFor(finding, 'approximate', approximateWcs);
    expect(solution.quality.medianResidualPx).toBeUndefined();
    const records = buildFindingEvidenceRecords(sourceAsset, solution, finding);
    const description = records[1]?.uncertaintyDescription ?? '';
    expect(description).toContain('No held-out star paired with the catalog, so no held-out residual');
    expect(description).not.toMatch(/NaN|undefined|999/);
  });

  it('promotes a detection without a sky position to the position of its verified catalog match', () => {
    const finding = pixelOnly();
    const promoted = promoteFindingToCatalogMatched(finding, {
      kind: 'AuthorizedCalibrationProof', wcsSolutionId: 'sol_02', sourceDetection: finding.sourceDetection!,
      catalogRef: {
        id: 'cat_betelgeuse', provider: 'Gaia DR3', releaseVersion: 'DR3-2022', catalogSourceId: 'HIP 27989',
        commonName: 'Betelgeuse', constellation: 'Orion', objectType: 'star', coordinateEpoch: 'J2016.0',
        raDeg: 88.7929, decDeg: 7.4071, apparentMagnitude: 0.42, citation: 'ESA Gaia DR3 / Hipparcos HIP 27989',
      },
      heldOutInliers: 8, medianResidualPx: 0.6, angularResidualArcsec: 9.5, pixelResidualPx: 0.4,
    });
    expect(promoted.skyPosition).toEqual({ raDeg: 88.7929, decDeg: 7.4071, uncertaintyArcsec: 9.5 });
    expect(CelestialFindingSchema.safeParse(promoted).success).toBe(true);
  });
});

describe('@albedian/evidence: licence and source strings', () => {
  type CatalogRef = NonNullable<CelestialFinding['catalogRef']>;
  type DistanceMethod = NonNullable<CatalogRef['bailerJonesDistancePc']>['method'];
  // A synthetic recording and moment that point at nobody
  const sourceAsset: SourceAsset = {
    id: 'asset_03', title: 'Synthetic Sky Recording', mediaKind: 'video', sha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
    uri: '/data/uploads/synthetic_sky.mp4', width: 1920, height: 1080, durationMs: 12000, fpsNumerator: 30, fpsDenominator: 1,
    videoCodec: 'hevc', audioStreams: [], privacyClass: 'private', immutable: true,
  };
  const solution: ObservationSolution = {
    id: 'sol_03', sourceAssetId: sourceAsset.id, sourceSha256: sourceAsset.sha256, frameTimeSec: 0, revision: 1, status: 'predicted_only',
    observedAtUTC: '2030-01-01T02:00:00Z', solver: { name: 'AlbedianGeometricQuadSolver', version: '1.0.0', catalogRelease: 'Gaia DR3 / Hipparcos Pinned Bright Subset' },
    quality: { detectedCandidateCount: 0, matchedInlierCount: 0, heldOutPairCount: 0, heldOutInliers: 0, passedIndependentVerification: false },
    findings: [], warnings: [],
  };
  const rigel = (provider: CatalogRef['provider'], method?: DistanceMethod): CatalogRef => ({
    id: 'cat_rigel', provider, releaseVersion: 'DR3-2022', catalogSourceId: 'HIP 24436', commonName: 'Rigel',
    constellation: 'Orion', objectType: 'star', coordinateEpoch: 'J2016.0', raDeg: 78.6345, decDeg: -8.2016, apparentMagnitude: 0.13,
    ...(method ? { bailerJonesDistancePc: { lower: 240.0, median: 264.5, upper: 290.0, method } } : {}),
    citation: 'ESA Gaia DR3 / Hipparcos HIP 24436',
  });
  // A prediction for the solution's moment, from a catalog record or, with none, from the ephemeris models
  const predicted = (catalogRef?: CatalogRef): CelestialFinding => ({
    id: 'finding-rigel', observationSolutionId: 'sol_03', evidenceClass: 'predicted', title: 'Rigel', subtitle: 'Beta Orionis · Orion',
    ...(catalogRef ? { catalogRef } : {}),
    skyPosition: { raDeg: 78.6345, decDeg: -8.2016, uncertaintyArcsec: 14.2 },
    evidence: {
      id: 'ev-rigel', evidenceClass: 'predicted', verificationMethod: 'Position predicted for the given time',
      limitations: ['Predicted for the given time and place; not measured in the image'], verifiedByIndependentHoldout: false,
    },
  });
  const lastRecord = (finding: CelestialFinding) => buildFindingEvidenceRecords(sourceAsset, solution, finding).at(-1);

  it('gives each catalog provider the licence its archive states, or says what Albedian uses it for', () => {
    expect(lastRecord(predicted(rigel('Gaia DR3')))?.license).toBe('CC BY-NC 3.0 IGO (ESA archive terms); credit ESA/Gaia/DPAC');
    expect(lastRecord(predicted(rigel('Hipparcos')))?.license).toBe('CC BY-NC 3.0 IGO (ESA archive terms); credit ESA');
    expect(lastRecord(predicted(rigel('JPL Horizons')))?.license).toBe(
      "No finding uses JPL Horizons data; only the landing scene's baked Sun and Moon positions come from it"
    );
    expect(lastRecord(predicted(rigel('SIMBAD')))?.license).toBe('Not used: Albedian makes no SIMBAD query');
  });

  it('titles a catalog record by provider, release, name and catalog id', () => {
    expect(lastRecord(predicted(rigel('Gaia DR3')))?.title).toBe('Gaia DR3 (DR3-2022): Rigel (HIP 24436)');
  });

  it('says that no licence is recorded for a public sample, whose record cannot name one', () => {
    const publicSample: SourceAsset = { ...sourceAsset, privacyClass: 'public_sample' };
    expect(buildFindingEvidenceRecords(publicSample, solution, predicted())[0]?.license).toBe(
      'No licence recorded: the asset record names none'
    );
  });

  it("names a private or a consented recording as the observer's own", () => {
    const privateRecording: SourceAsset = { ...sourceAsset, privacyClass: 'private' };
    const consentedRecording: SourceAsset = { ...sourceAsset, privacyClass: 'consented_demo' };
    expect(buildFindingEvidenceRecords(privateRecording, solution, predicted())[0]?.license).toBe('Observer First-Party Recording');
    expect(buildFindingEvidenceRecords(consentedRecording, solution, predicted())[0]?.license).toBe('Observer First-Party Recording');
  });

  it("describes a distance's spread by the method that gave its bounds, with no percentage for an unknown method", () => {
    const spread = (method: string) =>
      formatDistanceWithUncertainty({ lower: 240.0, median: 264.5, upper: 290.0, method }).uncertaintyText;
    expect(spread('geometric_parallax')).toBe('±25.0 pc: half the range from the parallax error');
    expect(spread('ephemeris_au')).toBe("±25.0 pc: half the range of the model's error bounds");
    expect(spread('stellar_model')).toBe('±25.0 pc: half the range of the bounds as given, method stellar_model');
  });

  it('names the models behind a prediction that has no catalog record', () => {
    expect(lastRecord(predicted())?.license).toBe(
      'Computed in Albedian from the Standish and Williams (1992) elements (JPL Solar System Dynamics) and Meeus (1998), with no JPL Horizons request'
    );
  });

  it('describes a catalog distance by the method that gave it', () => {
    expect(lastRecord(predicted(rigel('Gaia DR3', 'photogeometric_bailer_jones_edr3')))?.uncertaintyDescription).toBe(
      'Bailer-Jones et al. (2021) photogeometric distance 264.5 pc (16th to 84th percentile: 240.0 to 290.0 pc)'
    );
    expect(lastRecord(predicted(rigel('Hipparcos', 'geometric_parallax')))?.uncertaintyDescription).toBe(
      'Inverse-parallax distance 264.5 pc (240.0 to 290.0 pc from the parallax error)'
    );
    expect(lastRecord(predicted(rigel('JPL Horizons', 'ephemeris_au')))?.uncertaintyDescription).toBe(
      "Ephemeris distance 264.5 pc (240.0 to 290.0 pc from the model's error bounds)"
    );
  });
});
