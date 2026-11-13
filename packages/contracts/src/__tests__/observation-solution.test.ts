import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CelestialFindingSchema,
  EvidenceClass,
  ObservationSolutionSchema,
  PoseEstimateSchema,
  SolutionStatus,
  SpatialViewStateSchema,
  hasVerifiedWcs,
} from '../index.js';

// ALBEDIAN_FC_SEED=1..20 reruns the properties that read it under fixed seeds
const FC_SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
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
// A real catalog row (Rigel, Hipparcos 2007) as the Python solver serializes it, for the catalog-backed findings that
// production predicted stars are
const CATALOG_REF = (
  JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as Record<string, { findings: { id: string; catalogRef?: unknown }[] }>
)['plate_calibrated']!.findings.find((f) => f.id === 'finding-star-rigel')!.catalogRef;

function finding(id: string, evidenceClass: EvidenceClass, parts: { sky?: typeof SKY; detection?: boolean; catalog?: boolean }) {
  // A catalog match always holds the entry it was matched to, the id naming it and both residuals (the contract's
  // FINDING_CLASS_EVIDENCE); any finding that holds an entry names it by that id
  const catalog = parts.catalog || evidenceClass === 'catalog_matched';
  return {
    id,
    observationSolutionId: 'sol_01',
    evidenceClass,
    title: `Finding ${id}`,
    subtitle: 'Generated',
    ...(parts.detection ? { sourceDetection: DETECTION } : {}),
    ...(catalog ? { catalogRef: CATALOG_REF } : {}),
    ...(parts.sky ? { skyPosition: parts.sky } : {}),
    evidence: {
      id: `ev_${id}`, evidenceClass, verificationMethod: 'test', sourceAssetSha256: SHA,
      ...(catalog ? { catalogReferenceId: (CATALOG_REF as { id: string }).id } : {}),
      ...(evidenceClass === 'catalog_matched' ? { pixelResidualPx: 0.4, angularResidualArcsec: 50.4 } : {}),
      limitations: ['Generated for contract tests.'], verifiedByIndependentHoldout: false,
    },
  };
}

function solution(parts: { wcs: boolean; passed: boolean; findings: unknown[] }) {
  // observedAtUTC and observerLocation are a synthetic moment and open-ocean place that point at nobody
  return {
    id: 'sol_01', sourceAssetId: 'src_01', sourceSha256: SHA, frameTimeSec: 0, revision: 1,
    status: parts.wcs && parts.passed ? 'calibrated' : 'unresolved', observedAtUTC: '2030-01-01T02:00:00Z',
    observerLocation: { latDeg: 30.0, lonDeg: -40.0 },
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

/** The solution under another status; a pending one has no solver and no quality, since nothing has run. */
function asStatus<T extends ReturnType<typeof solution>>(sol: T, status: SolutionStatus) {
  if (status !== 'pending') return { ...sol, status };
  const { quality: _noQuality, solver: _noSolver, ...rest } = sol;
  return { ...rest, status };
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
      // A catalog match is a detection matched to the catalog, so it holds its detection besides its sky position
      const matched = evidenceClass === 'catalog_matched';
      expect(CelestialFindingSchema.safeParse(finding('f', evidenceClass, { detection: true })).success).toBe(false);
      expect(CelestialFindingSchema.safeParse(finding('f', evidenceClass, { sky: SKY, detection: matched })).success).toBe(true);
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
      // A catalog row behind the finding, as production predicted stars carry: it changes no rule
      catalog: fc.boolean(),
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
        // puts it there, and otherwise in pixels; a predicted or simulated finding carries no detection
        // (REQ-SKY-5.2)
        // ... and held-out verification passes only with a WCS (the helper always carries the residuals)
        // ... and the class table: an observed finding and a catalog match hold the detection they were measured
        // from, and an observed finding holds no catalog entry (the helper gives a match its entry, id and residuals)
        const obeysContract =
          (!spec.passed || spec.wcs) &&
          spec.findings.every((f) =>
            f.evidenceClass === 'observed'
              ? (f.sky !== undefined) === verified && f.detection && !f.catalog
              : f.sky !== undefined &&
                (f.evidenceClass !== 'catalog_matched' || (verified && f.detection)) &&
                (f.evidenceClass === 'catalog_matched' || !f.detection)
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
        seed: FC_SEED,
        numRuns: 400,
        // Random specs hit this case only about 1 in 120 runs, so seed it every time: an
        // unverified solution holding a pixel-only observed detection, with and without a WCS
        examples: [
          [{ wcs: false, passed: false, findings: [{ evidenceClass: 'observed', sky: undefined, detection: true, catalog: false }] }],
          [{ wcs: true, passed: false, findings: [{ evidenceClass: 'observed', sky: undefined, detection: true, catalog: false }] }],
          // A predicted or simulated finding with a detection, refused whatever the WCS, with or without a catalog row
          [{ wcs: true, passed: true, findings: [{ evidenceClass: 'predicted', sky: SKY, detection: true, catalog: false }] }],
          [{ wcs: false, passed: false, findings: [{ evidenceClass: 'simulated', sky: SKY, detection: true, catalog: false }] }],
          [{ wcs: true, passed: true, findings: [{ evidenceClass: 'predicted', sky: SKY, detection: true, catalog: true }] }],
          [{ wcs: false, passed: false, findings: [{ evidenceClass: 'simulated', sky: SKY, detection: true, catalog: true }] }],
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

  it('parses the Python plate_calibrated_timeless solution with no capture time or observer location', () => {
    const timeless = ObservationSolutionSchema.parse(samples['plate_calibrated_timeless']);
    expect(timeless.status).toBe('calibrated');
    expect(timeless.observedAtUTC).toBeUndefined();
    expect(timeless.observerLocation).toBeUndefined();
    expect(hasVerifiedWcs(timeless)).toBe(true);
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
  const withoutSolutionId = (f: Record<string, unknown>) => {
    const { observationSolutionId: _omitted, ...rest } = f;
    return rest;
  };

  it('accepts a prediction for a time and place that no recording stands behind', () => {
    const parsed = CelestialFindingSchema.safeParse(withoutHash(finding('moon', 'predicted', { sky: SKY })));
    expect(parsed.error?.issues ?? []).toEqual([]);
  });

  it('accepts a standalone predicted or simulated finding that names no ObservationSolution and requires observationSolutionId on observed and catalog_matched findings and inside a solution', () => {
    const standalonePred = CelestialFindingSchema.safeParse(
      withoutSolutionId(withoutHash(finding('moon', 'predicted', { sky: SKY })))
    );
    const standaloneSim = CelestialFindingSchema.safeParse(
      withoutSolutionId(withoutHash(finding('sim', 'simulated', { sky: SKY })))
    );
    expect(standalonePred.error?.issues ?? []).toEqual([]);
    expect(standaloneSim.error?.issues ?? []).toEqual([]);

    const observed = CelestialFindingSchema.safeParse(
      withoutSolutionId(finding('obs', 'observed', { detection: true }))
    );
    const matched = CelestialFindingSchema.safeParse(
      withoutSolutionId(finding('rigel', 'catalog_matched', { sky: SKY, detection: true }))
    );
    expect(observed.error?.issues.map((i) => i.path.join('.'))).toEqual(['observationSolutionId']);
    expect(matched.error?.issues.map((i) => i.path.join('.'))).toEqual(['observationSolutionId']);

    const pred = finding('moon', 'predicted', { sky: SKY });
    expect(CelestialFindingSchema.safeParse({ ...pred, observationSolutionId: null }).success).toBe(false);
    expect(CelestialFindingSchema.safeParse({ ...pred, observationSolutionId: '' }).success).toBe(false);

    // A single-pixel impulse detection with zero second moment has fwhmPx 0; negative fwhmPx is rejected
    const obsWithZeroFwhm = finding('obs0', 'observed', { detection: true });
    expect(
      CelestialFindingSchema.safeParse({
        ...obsWithZeroFwhm,
        sourceDetection: { ...obsWithZeroFwhm.sourceDetection!, fwhmPx: 0 },
      }).success
    ).toBe(true);
    expect(
      CelestialFindingSchema.safeParse({
        ...obsWithZeroFwhm,
        sourceDetection: { ...obsWithZeroFwhm.sourceDetection!, fwhmPx: -0.01 },
      }).success
    ).toBe(false);

    // Inside an ObservationSolution, every finding must carry observationSolutionId
    const solWithoutFindingSolId = ObservationSolutionSchema.safeParse({
      ...solution({ wcs: false, passed: false, findings: [withoutSolutionId(pred) as ReturnType<typeof finding>] }),
      status: 'predicted_only',
      observedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: { latDeg: 30.0, lonDeg: -40.0 },
    });
    expect(solWithoutFindingSolId.error?.issues.map((i) => i.path.join('.'))).toContain(
      'findings.0.observationSolutionId'
    );
  });

  it('rejects observed and catalog-matched evidence that names no recording', () => {
    const observed = CelestialFindingSchema.safeParse(withoutHash(finding('obs', 'observed', { detection: true })));
    const matched = CelestialFindingSchema.safeParse(withoutHash(finding('rigel', 'catalog_matched', { sky: SKY, detection: true })));
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

  it('passes held-out verification only on a calibrated solution (integrity-27)', () => {
    const verified = solution({ wcs: true, passed: true, findings: [] });
    expect(verified.status).toBe('calibrated');
    for (const status of ['approximate', 'ambiguous', 'unresolved', 'predicted_only'] as const) {
      const marked = ObservationSolutionSchema.safeParse(asStatus(verified, status));
      expect(issuePaths(marked), status).toContain('quality.passedIndependentVerification');
    }
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
    const { sourceSha256: _none, ...pending } = asStatus(solution({ wcs: false, passed: false, findings: [] }), 'pending');
    expect(issuePaths(ObservationSolutionSchema.safeParse(pending))).toEqual([]);
    expect(issuePaths(ObservationSolutionSchema.safeParse({ ...asStatus(solution({ wcs: false, passed: false, findings: [] }), 'unresolved'), sourceSha256: undefined }))).toContain('sourceSha256');
    const withFinding = { ...pending, findings: [finding('obs', 'observed', { detection: true })] };
    expect(issuePaths(ObservationSolutionSchema.safeParse(withFinding))).toContain('sourceSha256');
  });
});

describe('@albedian/contracts: solution and spatial view time and place', () => {
  // Synthetic moment and open-ocean coordinates that point at nobody
  const TIME = '2030-01-01T02:00:00Z';
  const PLACE = { latDeg: 30.0, lonDeg: -40.0 };
  const samples = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as Record<string, unknown>;

  function buildSolution(opts: {
    status: SolutionStatus;
    observedAtUTC?: string | null;
    observerLocation?: typeof PLACE | null;
    findings?: unknown[];
  }) {
    const verified = opts.status === 'calibrated';
    const base = asStatus(solution({ wcs: verified, passed: verified, findings: opts.findings ?? [] }), opts.status);
    const { observedAtUTC: _droppedTime, observerLocation: _droppedPlace, ...withoutTimeAndPlace } = base;
    return {
      ...withoutTimeAndPlace,
      status: opts.status,
      ...(opts.observedAtUTC !== undefined ? { observedAtUTC: opts.observedAtUTC } : {}),
      ...(opts.observerLocation !== undefined ? { observerLocation: opts.observerLocation } : {}),
    };
  }

  it('accepts a calibrated, unresolved, ambiguous or pending solution without a capture time or observer location', () => {
    for (const status of ['pending', 'calibrated', 'unresolved', 'ambiguous'] as const) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status })))).toEqual([]);
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status, observedAtUTC: TIME })))).toEqual([]);
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status, observerLocation: PLACE })))).toEqual([]);
    }
    const timeless = ObservationSolutionSchema.parse(samples['plate_calibrated_timeless']);
    expect(timeless.status).toBe('calibrated');
    expect(timeless.observedAtUTC).toBeUndefined();
    expect(timeless.observerLocation).toBeUndefined();
  });

  it('rejects observedAtUTC or observerLocation sent as null: unknown time and place are left out, never null', () => {
    expect(ObservationSolutionSchema.safeParse(buildSolution({ status: 'calibrated', observedAtUTC: null })).success).toBe(false);
    expect(ObservationSolutionSchema.safeParse(buildSolution({ status: 'calibrated', observerLocation: null })).success).toBe(false);
  });

  it('requires both observedAtUTC and observerLocation for approximate and predicted_only solutions', () => {
    for (const status of ['approximate', 'predicted_only'] as const) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status })))).toEqual([
        'observedAtUTC',
        'observerLocation',
      ]);
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status, observedAtUTC: TIME })))).toEqual([
        'observerLocation',
      ]);
      expect(issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status, observerLocation: PLACE })))).toEqual([
        'observedAtUTC',
      ]);
      expect(
        issuePaths(
          ObservationSolutionSchema.safeParse(buildSolution({ status, observedAtUTC: TIME, observerLocation: PLACE }))
        )
      ).toEqual([]);
    }
  });

  it('requires both observedAtUTC and observerLocation whenever a solution carries a predicted finding, whatever its status', () => {
    const predictedFinding = finding('moon', 'predicted', { sky: SKY });
    for (const status of ['calibrated', 'unresolved', 'ambiguous', 'pending'] as const) {
      expect(
        issuePaths(ObservationSolutionSchema.safeParse(buildSolution({ status, findings: [predictedFinding] })))
      ).toEqual(['observedAtUTC', 'observerLocation']);
      expect(
        issuePaths(
          ObservationSolutionSchema.safeParse(
            buildSolution({ status, observedAtUTC: TIME, findings: [predictedFinding] })
          )
        )
      ).toEqual(['observerLocation']);
      expect(
        issuePaths(
          ObservationSolutionSchema.safeParse(
            buildSolution({ status, observerLocation: PLACE, findings: [predictedFinding] })
          )
        )
      ).toEqual(['observedAtUTC']);
      expect(
        issuePaths(
          ObservationSolutionSchema.safeParse(
            buildSolution({ status, observedAtUTC: TIME, observerLocation: PLACE, findings: [predictedFinding] })
          )
        )
      ).toEqual([]);
    }
  });

  it('PBT-TIME-1: solution status and predicted findings govern when observedAtUTC and observerLocation are required', () => {
    const specArb = fc.record({
      status: fc.constantFrom<SolutionStatus>(
        'pending',
        'calibrated',
        'approximate',
        'predicted_only',
        'unresolved',
        'ambiguous'
      ),
      hasTime: fc.boolean(),
      hasPlace: fc.boolean(),
      hasPredictedFinding: fc.boolean(),
    });

    for (let seed = 1; seed <= 20; seed += 1) {
      fc.assert(
        fc.property(specArb, (spec) => {
          const verified = spec.status === 'calibrated';
          const findings = [
            finding('f0', verified ? 'catalog_matched' : 'observed', verified ? { sky: SKY, detection: true } : { detection: true }),
            ...(spec.hasPredictedFinding ? [finding('f1', 'predicted', { sky: SKY })] : []),
          ];
          const candidate = buildSolution({
            status: spec.status,
            ...(spec.hasTime ? { observedAtUTC: TIME } : {}),
            ...(spec.hasPlace ? { observerLocation: PLACE } : {}),
            findings,
          });
          const needsTimeAndPlace =
            spec.status === 'approximate' || spec.status === 'predicted_only' || spec.hasPredictedFinding;
          const expectedValid = !needsTimeAndPlace || (spec.hasTime && spec.hasPlace);
          const parsed = ObservationSolutionSchema.safeParse(candidate);
          expect(parsed.success).toBe(expectedValid);
          if (!parsed.success) {
            const paths = issuePaths(parsed);
            if (!spec.hasTime) expect(paths).toContain('observedAtUTC');
            if (!spec.hasPlace) expect(paths).toContain('observerLocation');
          }
        }),
        {
          seed,
          numRuns: 40,
          examples: [
            [{ status: 'calibrated', hasTime: false, hasPlace: false, hasPredictedFinding: false }],
            [{ status: 'approximate', hasTime: true, hasPlace: false, hasPredictedFinding: false }],
            [{ status: 'predicted_only', hasTime: false, hasPlace: true, hasPredictedFinding: false }],
            [{ status: 'calibrated', hasTime: false, hasPlace: true, hasPredictedFinding: true }],
            [{ status: 'unresolved', hasTime: true, hasPlace: false, hasPredictedFinding: true }],
          ],
        }
      );
    }
  });

  it('lets SpatialViewState omit observationEpochISO when the capture time is unknown while still requiring simulationEpochISO', () => {
    const baseView = {
      mode: 'source_2d' as const,
      transitionPhase: 'at_source' as const,
      transitionProgress: 0,
      observerOriginParsec: [0, 0, 0] as [number, number, number],
      cameraQuaternion: [0, 0, 0, 1] as [number, number, number, number],
      fovVerticalDeg: 60,
      frustumShiftNdc: [0, 0] as [number, number],
      simulationEpochISO: TIME,
      sceneScalePolicy: 'physical_euclidean' as const,
      activeOverlays: {
        observedMarkers: false,
        catalogMatchedLabels: false,
        predictedEphemerides: false,
        constellationLines: false,
        earthDirectionVector: false,
        residualVectors: false,
        sightlineCone: false,
      },
    };
    const withoutObsEpoch = SpatialViewStateSchema.parse(baseView);
    expect(withoutObsEpoch.observationEpochISO).toBeUndefined();
    expect(withoutObsEpoch.simulationEpochISO).toBe(TIME);

    const withObsEpoch = SpatialViewStateSchema.parse({ ...baseView, observationEpochISO: TIME });
    expect(withObsEpoch.observationEpochISO).toBe(TIME);

    expect(SpatialViewStateSchema.safeParse({ ...baseView, observationEpochISO: null }).success).toBe(false);
    const { simulationEpochISO: _dropped, ...withoutSimEpoch } = baseView;
    expect(SpatialViewStateSchema.safeParse(withoutSimEpoch).success).toBe(false);
  });
});

describe('@albedian/contracts: what a solver measured (experience-9f)', () => {
  const STATUSES: SolutionStatus[] = ['pending', 'calibrated', 'approximate', 'predicted_only', 'unresolved', 'ambiguous'];
  // A solution under a status, with or without its quality and solver; calibrated ones carry a verified WCS
  const build = (status: SolutionStatus, parts: { quality: boolean; solver: boolean }) => {
    const verified = status === 'calibrated';
    const { quality, solver, ...rest } = solution({ wcs: verified, passed: verified, findings: [] });
    return { ...rest, status, ...(parts.quality ? { quality } : {}), ...(parts.solver ? { solver } : {}) };
  };

  it('accepts a pending solution with no quality and no solver: nothing has run, so nothing was counted', () => {
    expect(issuePaths(ObservationSolutionSchema.safeParse(build('pending', { quality: false, solver: false })))).toEqual([]);
  });

  it('refuses counts or a solver on a pending solution, zeros included', () => {
    const zeros = {
      detectedCandidateCount: 0, matchedInlierCount: 0, heldOutPairCount: 0, heldOutInliers: 0, passedIndependentVerification: false,
    };
    const withZeros = { ...build('pending', { quality: false, solver: false }), quality: zeros };
    expect(issuePaths(ObservationSolutionSchema.safeParse(withZeros))).toContain('quality');
    const standIn = { ...build('pending', { quality: false, solver: false }), solver: { name: 'none', version: '0', catalogRelease: 'none' } };
    expect(issuePaths(ObservationSolutionSchema.safeParse(standIn))).toContain('solver');
  });

  it('requires the quality a calibrated or approximate solution was judged on', () => {
    for (const status of ['calibrated', 'approximate'] as const) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(build(status, { quality: false, solver: true }))), status).toContain('quality');
      expect(issuePaths(ObservationSolutionSchema.safeParse(build(status, { quality: true, solver: true }))), status).toEqual([]);
    }
  });

  it('lets an unresolved, ambiguous or predicted_only solution leave quality out, and only an unresolved one its solver', () => {
    for (const status of ['unresolved', 'ambiguous', 'predicted_only'] as const) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(build(status, { quality: false, solver: true }))), status).toEqual([]);
    }
    for (const status of ['calibrated', 'approximate', 'ambiguous', 'predicted_only'] as const) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(build(status, { quality: true, solver: false }))), status).toContain('solver');
    }
    // A solve job that failed before any solver ran (the file would not decode) names none
    expect(issuePaths(ObservationSolutionSchema.safeParse(build('unresolved', { quality: false, solver: false })))).toEqual([]);
  });

  it('places nothing on the sky through a WCS whose verification quality is absent', () => {
    const { quality: _none, ...noQuality } = solution({ wcs: true, passed: true, findings: [] });
    expect(hasVerifiedWcs(noQuality)).toBe(false);
    expect(hasVerifiedWcs(solution({ wcs: true, passed: true, findings: [] }))).toBe(true);
  });

  it('PBT-QUALITY-1: status alone decides whether quality and solver must, may or must not be present', () => {
    const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
    fc.assert(
      fc.property(fc.constantFrom(...STATUSES), fc.boolean(), fc.boolean(), (status, hasQuality, hasSolver) => {
        const expectedValid =
          status === 'pending'
            ? !hasQuality && !hasSolver
            : (hasSolver || status === 'unresolved') && (hasQuality || (status !== 'calibrated' && status !== 'approximate'));
        const parsed = ObservationSolutionSchema.safeParse(build(status, { quality: hasQuality, solver: hasSolver }));
        expect(parsed.success).toBe(expectedValid);
      }),
      {
        seed: SEED,
        numRuns: 100,
        examples: [
          ['pending', false, false],
          ['pending', true, false],
          ['calibrated', false, true],
          ['approximate', true, false],
          ['predicted_only', false, true],
        ],
      }
    );
  });
});

describe('@albedian/contracts: the pose estimate of an approximate solution (REQ-SKY-5)', () => {
  const STATUSES: SolutionStatus[] = ['pending', 'calibrated', 'approximate', 'predicted_only', 'unresolved', 'ambiguous'];
  const samples = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as Record<string, unknown>;
  // Synthetic values for contract tests only, describing no real camera: swept WCS i varies the scale by 10 percent
  // and the rotation by 3 deg about the nominal WCS, on its crpix (tests/integration/test_pose_estimate_contract.py
  // builds the same ones)
  const sweptWcs = (count: number) =>
    Array.from({ length: count }, (_, i) => ({
      ...WCS,
      scaleDegPerPx: WCS.scaleDegPerPx * (0.9 + 0.1 * (i % 3)),
      rotationDeg: WCS.rotationDeg + 3 * ((Math.floor(i / 3) % 3) - 1),
    }));
  const poseEstimate = (count = 9, overrides: Record<string, unknown> = {}) => ({
    method: 'moon_camera_profile_level_head',
    beacon: 'moon',
    centerAltDeg: 33,
    centerAzDeg: 224,
    rollDeg: 0,
    fovDiagonalDeg: 38.2,
    fovSource: 'A synthetic camera profile for contract tests',
    assumptions: ['The head is level.'],
    sweptWcs: sweptWcs(count),
    maxCenterOffsetDeg: 0.4,
    beaconCentroidUncertaintyDeg: 0.004,
    ...overrides,
  });
  // An approximate solution by default: a published WCS that did not pass held-out verification
  const posed = (parts: { status?: SolutionStatus; wcs?: boolean; pose?: unknown; findings?: unknown[] } = {}) => ({
    ...asStatus(solution({ wcs: parts.wcs ?? true, passed: false, findings: parts.findings ?? [] }), parts.status ?? 'approximate'),
    ...(parts.pose === undefined ? {} : { poseEstimate: parts.pose }),
  });
  const BEACON_RULE =
    "A pose estimate's beacon is the one its method lines up from: the moon for moon_camera_profile_level_head and moon_horizon, the sun for sun_horizon.";
  // The numeric fields of PoseEstimateSchema (snake_case in the Python test)
  const POSE_NUMBER_FIELDS = ['centerAltDeg', 'centerAzDeg', 'rollDeg', 'fovDiagonalDeg', 'maxCenterOffsetDeg', 'beaconCentroidUncertaintyDeg'];
  // One field outside the bounds of PoseEstimateSchema each; the Python test refuses the same ones
  const POSE_FIELD_FAULTS: Record<string, Record<string, unknown>> = {
    method: { method: 'guessed' },
    beacon: { beacon: 'jupiter' },
    centerAltHigh: { centerAltDeg: 90.5 },
    centerAltLow: { centerAltDeg: -90.5 },
    centerAzNegative: { centerAzDeg: -0.5 },
    centerAzHigh: { centerAzDeg: 360.5 },
    rollHigh: { rollDeg: 180.5 },
    rollLow: { rollDeg: -180.5 },
    fovZero: { fovDiagonalDeg: 0 },
    fovWide: { fovDiagonalDeg: 175.5 },
    fovSourceEmpty: { fovSource: '' },
    assumptionsEmpty: { assumptions: [] },
    assumptionBlank: { assumptions: [''] },
    offsetNegative: { maxCenterOffsetDeg: -0.01 },
    centroidNegative: { beaconCentroidUncertaintyDeg: -0.01 },
    // z.number() takes no NaN, boolean or text, on every numeric field
    ...Object.fromEntries(
      POSE_NUMBER_FIELDS.flatMap((key) => [
        [`${key}NaN`, { [key]: NaN }],
        [`${key}True`, { [key]: true }],
        [`${key}Text`, { [key]: 'x' }],
      ])
    ),
    // Finite, so no producer writes an Infinity that JSON cannot carry
    maxCenterOffsetDegInfinity: { maxCenterOffsetDeg: Infinity },
    beaconCentroidUncertaintyDegInfinity: { beaconCentroidUncertaintyDeg: Infinity },
    // The method lines up from its own beacon
    levelHeadWithTheSun: { beacon: 'sun' },
    moonHorizonWithTheSun: { method: 'moon_horizon', beacon: 'sun' },
    sunHorizonWithTheMoon: { method: 'sun_horizon' },
    // A list of sentences, not a mapping
    assumptionsMapping: { assumptions: { 'The head is level.': 1 } },
  };

  it('accepts an approximate solution whose pose estimate sweeps 2 to 27 WCS on the nominal crpix', () => {
    for (const count of [2, 9, 27]) {
      const parsed = ObservationSolutionSchema.safeParse(posed({ pose: poseEstimate(count) }));
      expect(issuePaths(parsed), String(count)).toEqual([]);
      expect(parsed.data?.poseEstimate?.sweptWcs, String(count)).toHaveLength(count);
    }
    // The pose estimate stays optional: an approximate solution without one is what the solvers send today
    expect(issuePaths(ObservationSolutionSchema.safeParse(posed()))).toEqual([]);
  });

  it('refuses a pose estimate on any solution but an approximate one', () => {
    for (const status of STATUSES.filter((s) => s !== 'approximate')) {
      const parsed = ObservationSolutionSchema.safeParse(posed({ status, pose: poseEstimate() }));
      expect(issuePaths(parsed), status).toEqual(['poseEstimate']);
      expect(parsed.error?.issues[0]?.message, status).toBe(
        'A pose estimate belongs only to an approximate solution with a WCS: it states how far that WCS may be off.'
      );
    }
  });

  it('refuses a pose estimate on an approximate solution without a WCS', () => {
    expect(issuePaths(ObservationSolutionSchema.safeParse(posed({ wcs: false, pose: poseEstimate() })))).toEqual(['poseEstimate']);
  });

  it('refuses fewer than two or more than 27 swept WCS', () => {
    for (const count of [0, 1, 28]) {
      expect(issuePaths(ObservationSolutionSchema.safeParse(posed({ pose: poseEstimate(count) }))), String(count)).toEqual([
        'poseEstimate.sweptWcs',
      ]);
    }
  });

  it('refuses a swept WCS on another crpixX or crpixY than the nominal WCS', () => {
    for (const key of ['crpixX', 'crpixY'] as const) {
      const pose = poseEstimate(9);
      pose.sweptWcs[4] = { ...pose.sweptWcs[4]!, [key]: WCS[key] + 0.5 };
      const parsed = ObservationSolutionSchema.safeParse(posed({ pose }));
      expect(issuePaths(parsed), key).toEqual(['poseEstimate.sweptWcs.4']);
      expect(parsed.error?.issues[0]?.message, key).toBe(
        "Every swept WCS of a pose estimate has the nominal WCS's crpixX and crpixY: all of them describe the same frame."
      );
    }
  });

  it.each(['predicted', 'simulated'] as const)('refuses a source detection on a %s finding, which no pixel measured', (evidenceClass) => {
    // With and without a catalog row behind it: the predicted stars the solvers send carry one
    for (const catalog of [false, true]) {
      const label = catalog ? 'catalog-backed' : 'no catalog row';
      const withDetection = CelestialFindingSchema.safeParse(finding('f', evidenceClass, { sky: SKY, detection: true, catalog }));
      expect(issuePaths(withDetection), label).toEqual(['sourceDetection']);
      expect(withDetection.error?.issues[0]?.message, label).toBe(
        'A predicted or simulated finding was not measured in the pixels, so it carries no source detection.'
      );
      expect(issuePaths(CelestialFindingSchema.safeParse(finding('f', evidenceClass, { sky: SKY, catalog }))), label).toEqual([]);
      // Inside a solution the refusal names the finding
      const inside = ObservationSolutionSchema.safeParse(
        solution({ wcs: false, passed: false, findings: [finding('f', evidenceClass, { sky: SKY, detection: true, catalog })] })
      );
      expect(issuePaths(inside), label).toEqual(['findings.0.sourceDetection']);
    }
  });

  it('ties the method to its beacon: the Moon for the two Moon methods, the Sun for sun_horizon', () => {
    const beacons = { moon_camera_profile_level_head: 'moon', moon_horizon: 'moon', sun_horizon: 'sun' } as const;
    for (const [method, beacon] of Object.entries(beacons)) {
      expect(issuePaths(PoseEstimateSchema.safeParse(poseEstimate(9, { method, beacon }))), method).toEqual([]);
      const refused = PoseEstimateSchema.safeParse(poseEstimate(9, { method, beacon: beacon === 'moon' ? 'sun' : 'moon' }));
      expect(issuePaths(refused), method).toEqual(['beacon']);
      expect(refused.error?.issues[0]?.message, method).toBe(BEACON_RULE);
    }
    // Inside a solution the refusal names the pose estimate's field
    expect(issuePaths(ObservationSolutionSchema.safeParse(posed({ pose: poseEstimate(9, { beacon: 'sun' }) })))).toEqual([
      'poseEstimate.beacon',
    ]);
  });

  it('refuses an infinite centre offset or centroid uncertainty, which JSON cannot carry, and takes the largest finite one', () => {
    for (const key of ['maxCenterOffsetDeg', 'beaconCentroidUncertaintyDeg']) {
      expect(issuePaths(PoseEstimateSchema.safeParse(poseEstimate(9, { [key]: Infinity }))), key).toEqual([key]);
      expect(issuePaths(PoseEstimateSchema.safeParse(poseEstimate(9, { [key]: Number.MAX_VALUE }))), key).toEqual([]);
    }
  });

  it('keeps the source detection of observed and catalog-matched findings', () => {
    expect(issuePaths(CelestialFindingSchema.safeParse(finding('o', 'observed', { detection: true })))).toEqual([]);
    expect(issuePaths(CelestialFindingSchema.safeParse(finding('c', 'catalog_matched', { sky: SKY, detection: true })))).toEqual([]);
  });

  it('refuses a pose estimate with a field outside its bounds, and accepts the bounds themselves', () => {
    for (const [name, fault] of Object.entries(POSE_FIELD_FAULTS)) {
      expect(PoseEstimateSchema.safeParse(poseEstimate(9, fault)).success, name).toBe(false);
    }
    expect(PoseEstimateSchema.safeParse(poseEstimate(9, { sweptWcs: [...sweptWcs(8), { ...WCS, projection: 'SIN' }] })).success).toBe(false);
    const edges = [
      { centerAltDeg: -90 }, { centerAltDeg: 90 }, { centerAzDeg: 0 }, { centerAzDeg: 360 }, { rollDeg: -180 }, { rollDeg: 180 },
      { fovDiagonalDeg: 175 }, { maxCenterOffsetDeg: 0, beaconCentroidUncertaintyDeg: 0 }, { method: 'moon_horizon' },
      { method: 'sun_horizon', beacon: 'sun' },
    ];
    for (const edge of edges) {
      expect(issuePaths(PoseEstimateSchema.safeParse(poseEstimate(9, edge))), JSON.stringify(edge)).toEqual([]);
    }
  });

  it('parses the Python pose_moon_level_head sample: nine swept WCS on the nominal crpix, the Moon in pixels only', () => {
    const sample = ObservationSolutionSchema.parse(samples['pose_moon_level_head']);
    expect(sample.status).toBe('approximate');
    expect(hasVerifiedWcs(sample)).toBe(false);
    const pose = sample.poseEstimate!;
    expect(pose.method).toBe('moon_camera_profile_level_head');
    expect(pose.beacon).toBe('moon');
    expect(pose.sweptWcs).toHaveLength(9);
    expect(pose.sweptWcs.every((w) => w.crpixX === sample.wcs!.crpixX && w.crpixY === sample.wcs!.crpixY)).toBe(true);
    const observed = sample.findings.filter((f) => f.evidenceClass === 'observed');
    expect(observed.map((f) => f.id)).toEqual(['finding-observed-moon']);
    expect(observed[0]!.skyPosition).toBeUndefined();
    expect(observed[0]!.sourceDetection).toBeDefined();
    const predicted = sample.findings.filter((f) => f.evidenceClass === 'predicted');
    expect(predicted.length).toBeGreaterThanOrEqual(2);
    expect(predicted.every((f) => f.sourceDetection === undefined)).toBe(true);
  });

  it('gives the pose sample a sweep a consumer can test rings on: the Moon off crpix, catalog stars, swept crval', () => {
    const sample = ObservationSolutionSchema.parse(samples['pose_moon_level_head']);
    const pose = sample.poseEstimate!;
    const moon = sample.findings.find((f) => f.id === 'finding-observed-moon')!;
    // Nothing in the sample reads as a measurement: the Moon says it is a synthetic fixture
    expect(moon.title).toMatch(/synthetic fixture/);
    expect(moon.evidence.verificationMethod).toMatch(/synthetic fixture/);
    const { xPx, yPx } = moon.sourceDetection!;
    expect(Math.hypot(xPx - sample.wcs!.crpixX, yPx - sample.wcs!.crpixY)).toBeGreaterThan(100);
    // The swept WCS differ in where they point, their scale and their rotation (Python checks the projections)
    expect(new Set(pose.sweptWcs.map((w) => `${w.crvalRaDeg.toFixed(6)},${w.crvalDecDeg.toFixed(6)}`)).size).toBe(9);
    expect(new Set(pose.sweptWcs.map((w) => w.scaleDegPerPx.toFixed(9))).size).toBe(3);
    expect(new Set(pose.sweptWcs.map((w) => w.rotationDeg.toFixed(6))).size).toBe(9);
    expect(pose.maxCenterOffsetDeg).toBeGreaterThan(0);
    // At least three catalog-backed predicted stars, none with a detection
    const stars = sample.findings.filter((f) => f.evidenceClass === 'predicted' && f.catalogRef?.objectType === 'star');
    expect(stars.length).toBeGreaterThanOrEqual(3);
    expect(stars.every((f) => f.sourceDetection === undefined)).toBe(true);
  });

  it('finds a pose estimate in the pose sample of the Python fixture alone', () => {
    const withPose = Object.entries(samples)
      .filter(([, sample]) => 'poseEstimate' in (sample as Record<string, unknown>))
      .map(([name]) => name);
    expect(withPose).toEqual(['pose_moon_level_head']);
  });

  it('P-SKY-7: a pose estimate rides only on an approximate solution with a WCS, sweeps 2 to 27 WCS on its crpix, and no predicted or simulated finding carries a detection', () => {
    const faultNames = Object.keys(POSE_FIELD_FAULTS);
    const poseSpecArb = fc.record({
      count: fc.integer({ min: 0, max: 30 }),
      offIndex: fc.oneof({ arbitrary: fc.constant(undefined), weight: 3 }, { arbitrary: fc.nat({ max: 29 }), weight: 1 }),
      offKey: fc.constantFrom<'crpixX' | 'crpixY'>('crpixX', 'crpixY'),
      offBy: fc.double({ min: 0.001, max: 500, noNaN: true }),
      offSign: fc.constantFrom(1, -1),
      fieldFault: fc.oneof({ arbitrary: fc.constant(undefined), weight: 4 }, { arbitrary: fc.constantFrom(...faultNames), weight: 1 }),
    });
    type PoseSpec = { count: number; offIndex: number | undefined; offKey: 'crpixX' | 'crpixY'; offBy: number; offSign: number; fieldFault: string | undefined };
    const specArb = fc.record({
      status: fc.oneof({ arbitrary: fc.constant<SolutionStatus>('approximate'), weight: 3 }, { arbitrary: fc.constantFrom(...STATUSES), weight: 2 }),
      wcs: fc.boolean(),
      pose: fc.oneof({ arbitrary: fc.constant(undefined), weight: 1 }, { arbitrary: poseSpecArb, weight: 2 }),
      findings: fc.array(
        fc.record({
          evidenceClass: fc.constantFrom<EvidenceClass>('observed', 'predicted', 'simulated'),
          detection: fc.oneof({ arbitrary: fc.constant(false), weight: 3 }, { arbitrary: fc.constant(true), weight: 1 }),
          // A catalog row behind a predicted or simulated finding, as the predicted stars the solvers send carry
          catalog: fc.boolean(),
        }),
        { maxLength: 4 }
      ),
    });
    const REASONS = [
      'accepted with a pose', 'accepted without a pose', 'status', 'no wcs', 'fewer than two', 'more than 27', 'other crpix',
      'field', 'predicted with a detection', 'simulated with a detection', 'catalog-backed predicted with a detection',
      'catalog-backed simulated with a detection',
    ];
    const seen = new Set<string>();
    const okPose: PoseSpec = { count: 9, offIndex: undefined, offKey: 'crpixX', offBy: 1, offSign: 1, fieldFault: undefined };
    fc.assert(
      fc.property(specArb, (spec) => {
        // An observed finding is located by its detection (no verified WCS here); the others carry a sky position
        const findings = spec.findings.map((f, i) =>
          finding(
            `f${i}`,
            f.evidenceClass,
            f.evidenceClass === 'observed' ? { detection: true } : { sky: SKY, detection: f.detection, catalog: f.catalog }
          )
        );
        const p = spec.pose;
        let pose: ReturnType<typeof poseEstimate> | undefined;
        if (p !== undefined) {
          pose = poseEstimate(p.count, p.fieldFault === undefined ? {} : (POSE_FIELD_FAULTS[p.fieldFault] ?? {}));
          if (p.offIndex !== undefined && p.offIndex < p.count) {
            pose.sweptWcs[p.offIndex] = { ...pose.sweptWcs[p.offIndex]!, [p.offKey]: WCS[p.offKey] + p.offSign * p.offBy };
          }
        }
        const crpixMoved = p !== undefined && p.offIndex !== undefined && p.offIndex < p.count;
        const poseOk =
          p === undefined ||
          (spec.status === 'approximate' && spec.wcs && p.count >= 2 && p.count <= 27 && !crpixMoved && p.fieldFault === undefined);
        const detectionOn = spec.findings.map((f) => f.evidenceClass !== 'observed' && f.detection);
        const expected = poseOk && !detectionOn.some(Boolean);

        const parsed = ObservationSolutionSchema.safeParse(posed({ status: spec.status, wcs: spec.wcs, pose, findings }));
        expect(parsed.success).toBe(expected);
        const paths = issuePaths(parsed);
        expect(paths.some((path) => path === 'poseEstimate' || path.startsWith('poseEstimate.'))).toBe(!poseOk);
        detectionOn.forEach((on, i) => expect(paths.includes(`findings.${i}.sourceDetection`)).toBe(on));
        if (parsed.success) expect(parsed.data.poseEstimate).toEqual(pose);

        if (expected) seen.add(p === undefined ? 'accepted without a pose' : 'accepted with a pose');
        if (p !== undefined && spec.status !== 'approximate') seen.add('status');
        if (p !== undefined && !spec.wcs) seen.add('no wcs');
        if (p !== undefined && p.count < 2) seen.add('fewer than two');
        if (p !== undefined && p.count > 27) seen.add('more than 27');
        if (crpixMoved) seen.add('other crpix');
        if (p?.fieldFault !== undefined) seen.add('field');
        spec.findings.forEach((f, i) => {
          if (detectionOn[i]) seen.add(`${f.catalog ? 'catalog-backed ' : ''}${f.evidenceClass} with a detection`);
        });
      }),
      {
        seed: FC_SEED,
        numRuns: 300,
        // Every refusal and both acceptances, seeded so that no run can miss one
        examples: [
          [{ status: 'approximate', wcs: true, pose: okPose, findings: [{ evidenceClass: 'observed', detection: true, catalog: false }, { evidenceClass: 'predicted', detection: false, catalog: true }, { evidenceClass: 'simulated', detection: false, catalog: false }] }],
          [{ status: 'approximate', wcs: true, pose: undefined, findings: [{ evidenceClass: 'predicted', detection: false, catalog: false }] }],
          [{ status: 'calibrated', wcs: true, pose: okPose, findings: [] }],
          [{ status: 'predicted_only', wcs: false, pose: okPose, findings: [] }],
          [{ status: 'approximate', wcs: false, pose: okPose, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, count: 1 }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, count: 0 }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, count: 28 }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, offIndex: 4, offKey: 'crpixY' }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, fieldFault: 'fovZero' }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, fieldFault: 'sunHorizonWithTheMoon' }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: { ...okPose, fieldFault: 'maxCenterOffsetDegInfinity' }, findings: [] }],
          [{ status: 'approximate', wcs: true, pose: okPose, findings: [{ evidenceClass: 'predicted', detection: true, catalog: false }] }],
          [{ status: 'unresolved', wcs: false, pose: undefined, findings: [{ evidenceClass: 'simulated', detection: true, catalog: false }] }],
          [{ status: 'approximate', wcs: true, pose: okPose, findings: [{ evidenceClass: 'predicted', detection: true, catalog: true }] }],
          [{ status: 'unresolved', wcs: false, pose: undefined, findings: [{ evidenceClass: 'simulated', detection: true, catalog: true }] }],
        ],
      }
    );
    expect([...seen].sort()).toEqual([...REASONS].sort());
  });
});
