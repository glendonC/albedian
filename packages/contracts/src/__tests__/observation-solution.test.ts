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

const SHA = '0123456789abcdef'.repeat(4);
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
  // observedAtUTC is a synthetic moment that points at nobody
  return {
    id: 'sol_01', sourceAssetId: 'src_01', sourceSha256: SHA, frameTimeSec: 0, revision: 1,
    status: parts.wcs && parts.passed ? 'calibrated' : 'unresolved', observedAtUTC: '2030-01-01T02:00:00Z',
    solver: { name: 'test', version: '1', catalogRelease: 'test' },
    ...(parts.wcs ? { wcs: WCS } : {}),
    // Eight held-out detections paired with the catalog and measured; inliers only when the fit passed
    quality: {
      detectedCandidateCount: parts.findings.length, matchedInlierCount: 0, heldOutPairCount: 8,
      heldOutInliers: parts.passed ? 8 : 0, medianResidualPx: 0.4, p95ResidualPx: 0.9, medianResidualArcsec: 50,
      passedIndependentVerification: parts.passed,
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
        // ... and held-out verification passes only with a WCS (the helper always carries the residuals)
        const obeysContract =
          (!spec.passed || spec.wcs) &&
          spec.findings.every((f) =>
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
      {
        numRuns: 400,
        // Random specs hit this case only about 1 in 120 runs, so seed it every time: an
        // unverified solution holding a pixel-only observed detection, with and without a WCS
        examples: [
          [{ wcs: false, passed: false, findings: [{ evidenceClass: 'observed', sky: undefined, detection: true }] }],
          [{ wcs: true, passed: false, findings: [{ evidenceClass: 'observed', sky: undefined, detection: true }] }],
        ],
      }
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

describe('@albedian/contracts: solution quality residuals', () => {
  const counts = {
    detectedCandidateCount: 8, matchedInlierCount: 0, heldOutPairCount: 0, heldOutInliers: 0, passedIndependentVerification: false,
  };

  it('accepts a solution without held-out residuals, as when no held-out star paired with the catalog', () => {
    const parsed = ObservationSolutionSchema.safeParse({
      ...solution({ wcs: false, passed: false, findings: [] }),
      quality: counts,
    });
    expect(parsed.error?.issues ?? []).toEqual([]);
  });

  it('rejects a residual sent as null: a missing residual is left out, never null', () => {
    const parsed = ObservationSolutionSchema.safeParse({
      ...solution({ wcs: false, passed: false, findings: [] }),
      quality: { ...counts, medianResidualPx: null, p95ResidualPx: null, medianResidualArcsec: null },
    });
    expect(parsed.success).toBe(false);
  });
});

describe('@albedian/contracts: the recording behind the evidence', () => {
  const withoutHash = (f: ReturnType<typeof finding>) => {
    const { sourceAssetSha256: _omitted, ...evidence } = f.evidence;
    return { ...f, evidence };
  };

  it('accepts a prediction for a time and place that no recording stands behind', () => {
    const parsed = CelestialFindingSchema.safeParse(withoutHash(finding('moon', 'predicted', { sky: SKY })));
    expect(parsed.error?.issues ?? []).toEqual([]);
  });

  it('rejects observed and catalog-matched evidence that names no recording', () => {
    const observed = CelestialFindingSchema.safeParse(withoutHash(finding('obs', 'observed', { detection: true })));
    const matched = CelestialFindingSchema.safeParse(withoutHash(finding('rigel', 'catalog_matched', { sky: SKY })));
    expect(observed.error?.issues.map((i) => i.path.join('.'))).toEqual(['evidence.sourceAssetSha256']);
    expect(matched.error?.issues.map((i) => i.path.join('.'))).toEqual(['evidence.sourceAssetSha256']);
  });

  it('rejects a hash sent as null: a missing hash is left out, never null', () => {
    const f = finding('moon', 'predicted', { sky: SKY });
    expect(CelestialFindingSchema.safeParse({ ...f, evidence: { ...f.evidence, sourceAssetSha256: null } }).success).toBe(false);
  });
});

const issuePaths = (result: { error?: { issues: { path: (string | number)[] }[] } }) =>
  (result.error?.issues ?? []).map((issue) => issue.path.join('.'));

describe('@albedian/contracts: held-out pairs, residuals and verification', () => {
  const unverified = solution({ wcs: false, passed: false, findings: [] });
  const withQuality = (quality: Record<string, unknown>) =>
    ObservationSolutionSchema.safeParse({
      ...unverified,
      quality: { detectedCandidateCount: 8, matchedInlierCount: 0, passedIndependentVerification: false, ...quality },
    });
  const residuals = { medianResidualPx: 4.2, p95ResidualPx: 6.8, medianResidualArcsec: 520 };

  it('accepts residuals exactly when held-out stars were paired, inliers or not', () => {
    expect(issuePaths(withQuality({ heldOutPairCount: 0, heldOutInliers: 0 }))).toEqual([]);
    // Every held-out pair beyond the inlier gate but within the association radius: residuals and no inlier
    expect(issuePaths(withQuality({ heldOutPairCount: 5, heldOutInliers: 0, ...residuals }))).toEqual([]);
  });

  it('rejects residuals without a held-out pair, and a held-out pair without its residuals', () => {
    const sentinels = { medianResidualPx: 999, p95ResidualPx: 999, medianResidualArcsec: 9999 };
    expect(issuePaths(withQuality({ heldOutPairCount: 0, heldOutInliers: 0, ...sentinels }))).toContain('quality.medianResidualPx');
    expect(issuePaths(withQuality({ heldOutPairCount: 5, heldOutInliers: 0 }))).toContain('quality.medianResidualPx');
  });

  it('never counts more held-out inliers than held-out pairs', () => {
    expect(issuePaths(withQuality({ heldOutPairCount: 4, heldOutInliers: 6, ...residuals }))).toContain('quality.heldOutInliers');
  });

  it('passes held-out verification only with a WCS and the residuals it was judged on', () => {
    const verified = solution({ wcs: true, passed: true, findings: [] });
    expect(issuePaths(ObservationSolutionSchema.safeParse(verified))).toEqual([]);
    const { wcs: _dropped, ...withoutWcs } = verified;
    expect(issuePaths(ObservationSolutionSchema.safeParse(withoutWcs))).toContain('quality.passedIndependentVerification');
    const withoutResiduals = {
      ...verified,
      quality: { detectedCandidateCount: 8, matchedInlierCount: 8, heldOutPairCount: 0, heldOutInliers: 0, passedIndependentVerification: true },
    };
    expect(issuePaths(ObservationSolutionSchema.safeParse(withoutResiduals))).toContain('quality.passedIndependentVerification');
  });
});

describe('@albedian/contracts: the hashes a solution and its evidence name', () => {
  it('take only a SHA-256 as 64 lowercase hex, never 64 zeros', () => {
    for (const notAHash of ['0123456789abcdef0123456789abcdef', 'AB'.repeat(32), '0'.repeat(64), 'ab'.repeat(31) + 'zz']) {
      const sol = { ...solution({ wcs: false, passed: false, findings: [] }), sourceSha256: notAHash };
      expect(ObservationSolutionSchema.safeParse(sol).success, notAHash).toBe(false);
      const f = finding('moon', 'predicted', { sky: SKY });
      expect(CelestialFindingSchema.safeParse({ ...f, evidence: { ...f.evidence, sourceAssetSha256: notAHash } }).success, notAHash).toBe(false);
    }
  });

  it('let only a pending solution with no findings name no source hash', () => {
    const { sourceSha256: _none, ...pending } = { ...solution({ wcs: false, passed: false, findings: [] }), status: 'pending' };
    expect(issuePaths(ObservationSolutionSchema.safeParse(pending))).toEqual([]);
    expect(issuePaths(ObservationSolutionSchema.safeParse({ ...pending, status: 'unresolved' }))).toContain('sourceSha256');
    const withFinding = { ...pending, findings: [finding('obs', 'observed', { detection: true })] };
    expect(issuePaths(ObservationSolutionSchema.safeParse(withFinding))).toContain('sourceSha256');
  });
});
