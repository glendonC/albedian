import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CelestialFindingSchema,
  EvidenceClass,
  ObservationSolutionSchema,
  hasVerifiedWcs,
} from '../index.js';

const SHA = '0123456789abcdef0123456789abcdef';
const DETECTION = {
  assetId: 'src_01', frameTimeSec: 0, xPx: 412.5, yPx: 300.2, flux: 5000, snr: 25, fwhmPx: 2.5, centroidUncertaintyPx: 0.1,
};
const SKY = { raDeg: 83.8, decDeg: 4.0, uncertaintyArcsec: 12.0 };
const WCS = {
  projection: 'TAN' as const, crvalRaDeg: 83.8, crvalDecDeg: 4.0, crpixX: 480, crpixY: 270, scaleDegPerPx: 0.035,
  rotationDeg: 0, radialDistortionK1: 0, radialDistortionK2: 0, parity: -1 as const, fovHorizontalDeg: 33.6, fovVerticalDeg: 18.9,
};
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json');

function finding(id: string, evidenceClass: EvidenceClass, parts: { sky?: typeof SKY; detection?: boolean }) {
  return {
    id,
    observationSolutionId: 'sol_01',
    evidenceClass,
    title: `Finding ${id}`,
    subtitle: 'Generated',
    ...(parts.detection ? { sourceDetection: DETECTION } : {}),
    ...(parts.sky ? { skyPosition: parts.sky } : {}),
    evidence: {
      id: `ev_${id}`, evidenceClass, verificationMethod: 'test', sourceAssetSha256: SHA,
      limitations: ['Generated for contract tests.'], verifiedByIndependentHoldout: false,
    },
  };
}

function solution(parts: { wcs: boolean; passed: boolean; findings: unknown[] }) {
  return {
    id: 'sol_01', sourceAssetId: 'src_01', sourceSha256: SHA, frameTimeSec: 0, revision: 1,
    status: parts.wcs && parts.passed ? 'calibrated' : 'unresolved', observedAtUTC: '2030-01-01T02:00:00Z',
    solver: { name: 'test', version: '1', catalogRelease: 'test' },
    ...(parts.wcs ? { wcs: WCS } : {}),
    quality: {
      detectedCandidateCount: parts.findings.length, matchedInlierCount: 0, heldOutInliers: 0,
      medianResidualPx: 0, p95ResidualPx: 0, medianResidualArcsec: 0, passedIndependentVerification: parts.passed,
    },
    findings: parts.findings,
    warnings: [],
  };
}

describe('@albedian/contracts: CelestialFinding sky positions', () => {
  it('accepts an observed point source located by its pixels alone', () => {
    const parsed = CelestialFindingSchema.parse(finding('obs', 'observed', { detection: true }));
    expect(parsed.skyPosition).toBeUndefined();
    expect(parsed.sourceDetection?.xPx).toBe(412.5);
  });

  it('rejects an observed finding located neither in pixels nor on the sky', () => {
    expect(CelestialFindingSchema.safeParse(finding('obs', 'observed', {})).success).toBe(false);
  });

  it.each(['catalog_matched', 'predicted', 'simulated'] as const)(
    'still requires a sky position for %s findings',
    (evidenceClass) => {
      expect(CelestialFindingSchema.safeParse(finding('f', evidenceClass, { detection: true })).success).toBe(false);
      expect(CelestialFindingSchema.safeParse(finding('f', evidenceClass, { sky: SKY })).success).toBe(true);
    }
  );

  it('PBT-SKY-1: without a verified WCS no detection carries sky coordinates or a catalog match', () => {
    const findingSpecArb = fc.record({
      evidenceClass: fc.constantFrom<EvidenceClass>('observed', 'catalog_matched', 'predicted', 'simulated'),
      sky: fc.option(
        fc.record({
          raDeg: fc.double({ min: 0, max: 360, noNaN: true }),
          decDeg: fc.double({ min: -90, max: 90, noNaN: true }),
          uncertaintyArcsec: fc.double({ min: 0, max: 3600, noNaN: true }),
        }),
        { nil: undefined }
      ),
      detection: fc.boolean(),
    });
    const solutionSpecArb = fc.record({
      wcs: fc.boolean(),
      passed: fc.boolean(),
      findings: fc.array(findingSpecArb, { maxLength: 4 }),
    });

    let acceptedUnverifiedWithDetections = 0;
    fc.assert(
      fc.property(solutionSpecArb, (spec) => {
        const verified = spec.wcs && spec.passed;
        // Model-based oracle: catalog, predicted and simulated positions are required, and a catalog
        // match needs a verified WCS; an observed detection is on the sky exactly when a verified WCS
        // puts it there, and otherwise in pixels
        const obeysContract = spec.findings.every((f) =>
          f.evidenceClass === 'observed'
            ? (f.sky !== undefined) === verified && (f.sky !== undefined || f.detection)
            : f.sky !== undefined && (f.evidenceClass !== 'catalog_matched' || verified)
        );
        const parsed = ObservationSolutionSchema.safeParse(
          solution({ ...spec, findings: spec.findings.map((f, i) => finding(`f${i}`, f.evidenceClass, f)) })
        );
        expect(parsed.success).toBe(obeysContract);
        if (parsed.success && !hasVerifiedWcs(parsed.data)) {
          expect(parsed.data.findings.some((f) => f.evidenceClass === 'catalog_matched')).toBe(false);
          const observed = parsed.data.findings.filter((f) => f.evidenceClass === 'observed');
          expect(observed.every((f) => f.skyPosition === undefined && f.sourceDetection !== undefined)).toBe(true);
          if (observed.length > 0) acceptedUnverifiedWithDetections += 1;
        }
      }),
      { numRuns: 400 }
    );
    expect(acceptedUnverifiedWithDetections).toBeGreaterThan(0);
  });

  it('treats a published but unverified WCS as no registration', () => {
    const placed = finding('obs', 'observed', { sky: SKY, detection: true });
    const pixelOnly = finding('obs', 'observed', { detection: true });
    const matched = finding('cat', 'catalog_matched', { sky: SKY, detection: true });
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: true, passed: false, findings: [placed] })).success).toBe(false);
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: true, passed: false, findings: [pixelOnly] })).success).toBe(true);
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: true, passed: true, findings: [pixelOnly] })).success).toBe(false);
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: true, passed: false, findings: [matched] })).success).toBe(false);
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: false, passed: true, findings: [matched] })).success).toBe(false);
    expect(ObservationSolutionSchema.safeParse(solution({ wcs: true, passed: true, findings: [matched] })).success).toBe(true);
  });
});

describe('@albedian/contracts: Python contract parity', () => {
  const samples = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as Record<string, unknown>;

  it.each(Object.keys(samples))('parses the Python %s solution', (name) => {
    const parsed = ObservationSolutionSchema.safeParse(samples[name]);
    expect(parsed.error?.issues ?? []).toEqual([]);
  });

  it('receives detections without a sky position from every Python solution without a verified WCS', () => {
    const solutions = Object.values(samples).map((sample) => ObservationSolutionSchema.parse(sample));
    const unplaced = solutions
      .filter((s) => !hasVerifiedWcs(s))
      .flatMap((s) => s.findings.filter((f) => f.evidenceClass === 'observed'));
    expect(unplaced.length).toBeGreaterThan(0);
    expect(unplaced.every((f) => f.skyPosition === undefined && f.sourceDetection !== undefined)).toBe(true);
  });
});
