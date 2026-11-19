import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { z } from 'zod';
import {
  CaptureTimeHintSchema,
  CatalogReferenceSchema,
  CelestialFindingSchema,
  DETECTION_STATISTICS,
  DetectionQualitySchema,
  EvidenceClassSchema,
  FINDING_CLASS_EVIDENCE,
  FINDING_EVIDENCE_RULES,
  FINDING_FIELDS,
  findingClassIssues,
  listsEachOnceInOrder,
  LocationHintSchema,
  MAX_HELD_OUT_MEDIAN_RESIDUAL_PX,
  MIN_HELD_OUT_INLIERS,
  NOISE_LIMITS,
  ObservationSolutionSchema,
  PROFILE_ANGLE_LIMIT_RAD,
  PROFILE_MAX_SIGMA_PX,
  PROFILE_MAX_STARS,
  PROFILE_MIN_SIGMA_PX,
  PROFILE_MIN_STARS,
  PoseEstimateSchema,
  PointingHintSchema,
  SHA256_HEX_PATTERN,
  SWEPT_FOV_TOLERANCE_DEG,
  ScientificEvidenceSchema,
  SkyPositionSchema,
  SolutionStatusSchema,
  SolveJobSchema,
  SourceDetectionSchema,
  StarProfileSchema,
  WCSCalibrationModelSchema,
} from '../index.js';

/**
 * The observation solution family against the fixture the Python side wrote
 * (tests/integration/test_observation_solution_contract.py, tests/fixtures/observation_solution_contract.json):
 * the Zod schemas here and the parser in services/contracts/astronomy.py must accept and refuse the same samples,
 * at the same paths and in the same words, and follow one table of field rules at every edge.
 */
const FIXTURES = path.resolve(__dirname, '../../../../tests/fixtures');
const EVIDENCE_PACKAGE = path.resolve(__dirname, '../../../evidence/src/index.ts');
const REPOSITORY = path.resolve(__dirname, '../../../..');
// ALBEDIAN_FC_SEED=1..20 reruns the properties under fixed seeds
const FC_SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

type Key = string | number;
type Sample = Record<string, unknown>;
type Step = { op: 'set' | 'remove'; path: Key[]; value?: unknown };
interface Rule {
  type: 'number' | 'text' | 'choice' | 'flag';
  accept: unknown[];
  refuse: unknown[];
  ge?: number;
  gt?: number;
  le?: number;
  integer?: boolean;
  minLength?: number;
  pattern?: string;
  values?: (string | number)[];
}
interface FieldEntry {
  rule?: string;
  schema?: string;
  optional?: boolean;
  array?: boolean;
  minItems?: number;
  maxItems?: number;
}
interface Case {
  name: string;
  base: string;
  patch: Step[];
  reason: string;
  paths: string[];
  messages: string[];
}
interface Fixture {
  patterns: { sha256: string; utcInstant: string };
  heldOutGate: { minHeldOutInliers: number; maxMedianResidualPx: number };
  sweptFovToleranceDeg: number;
  findingClassEvidence: Record<string, { source: string[]; must: Record<string, string>; mustNot: Record<string, string>; may: string[] }>;
  findingEvidenceRules: Record<string, string>;
  findingFields: string[];
  rules: Record<string, Rule>;
  fields: Record<string, Record<string, FieldEntry>>;
  pythonNames: Record<string, Record<string, string>>;
  closedSchemas: string[];
  detection: {
    profileMinSigmaPx: number;
    profileMaxSigmaPx: number;
    profileMinStars: number;
    profileMaxStars: number;
    profileAngleLimitRad: number;
    statistics: string[];
    noiseLimits: string[];
  };
  noiseLimitOrderCases: { order: string[]; cases: { limits: string[]; listed: boolean }[] };
  integerRuleEdgeCases: { name: string; ge?: number; gt?: number; le?: number; entry: Rule }[];
  keyCases: { levels: number; dropped: number; refusedUnknown: number; refusedWhenRenamed: number; leftOutWhenRenamed: number };
  structureRefusals: { object: unknown[]; array: unknown[] };
  homes: Record<string, { sample: string; path: Key[] }>;
  valid: Record<string, Sample>;
  invalid: Case[];
  unnamedKeys: { sample: Sample; parsed: Sample };
  findings: { valid: Record<string, Sample>; invalid: Case[]; taken: { name: string; base: string; patch: Step[] }[] };
  ingest: { valid: { schema: string; sample: unknown }[]; invalid: { schema: string; sample: unknown; reason: string }[] };
}

const fixture = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'observation_solution_contract.json'), 'utf8')) as Fixture;
const pythonSolutions = JSON.parse(fs.readFileSync(path.join(FIXTURES, 'python_contract_solutions.json'), 'utf8')) as Record<string, Sample>;

const NON_FINITE: Record<string, number> = { NaN: Number.NaN, Infinity: Number.POSITIVE_INFINITY, '-Infinity': Number.NEGATIVE_INFINITY };

/** A fixture value as the schema is given it: {"$number": name} becomes the number JSON cannot carry. */
function decode(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(decode);
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value);
    if (entries.length === 1 && entries[0]![0] === '$number') return NON_FINITE[String(entries[0]![1])];
    return Object.fromEntries(entries.map(([key, item]) => [key, decode(item)]));
  }
  return value;
}

function at(value: unknown, where: Key[]): unknown {
  return where.reduce<unknown>((inner, key) => (inner as Record<Key, unknown>)[key], value);
}

/** `sample` after each step: set a value at a path, or remove what is there. */
function patched(sample: unknown, steps: Step[]): Sample {
  const out = structuredClone(sample) as Sample;
  for (const step of steps) {
    const target = at(out, step.path.slice(0, -1)) as Record<Key, unknown>;
    const last = step.path[step.path.length - 1]!;
    if (step.op === 'remove') delete target[last];
    else target[last] = decode(structuredClone(step.value));
  }
  return out;
}

const put = (where: Key[], value: unknown): Step => ({ op: 'set', path: where, value });
const drop = (where: Key[]): Step => ({ op: 'remove', path: where });
const dotted = (where: Key[]) => where.join('.');
const issuePaths = (result: z.SafeParseReturnType<unknown, unknown>) =>
  result.success ? [] : [...new Set(result.error.issues.map((issue) => issue.path.join('.')))].sort();
const issueMessages = (result: z.SafeParseReturnType<unknown, unknown>) =>
  result.success ? [] : result.error.issues.map((issue) => issue.message);

const solutionShape = ObservationSolutionSchema.innerType().shape;
const findingVariants = CelestialFindingSchema.innerType().options;
const observedShape = findingVariants[0]!.shape;
const referenceShape = CatalogReferenceSchema.innerType().shape;
/** Each schema of the family, on its own; the ones the contract writes inline are read out of their parents. */
const SCHEMAS: Record<string, z.ZodTypeAny> = {
  ObservationSolution: ObservationSolutionSchema,
  CelestialFinding: CelestialFindingSchema,
  SourceDetection: SourceDetectionSchema,
  SkyPosition: SkyPositionSchema,
  ScientificEvidence: ScientificEvidenceSchema,
  InferredDistance: observedShape.inferredDistancePc.unwrap(),
  CatalogReference: CatalogReferenceSchema,
  CatalogDistance: referenceShape.bailerJonesDistancePc.unwrap(),
  WCSCalibrationModel: WCSCalibrationModelSchema,
  SolutionQuality: solutionShape.quality.unwrap(),
  DetectionQuality: DetectionQualitySchema,
  StarProfile: StarProfileSchema,
  SolverIdentity: solutionShape.solver.unwrap(),
  ObserverLocation: solutionShape.observerLocation.unwrap(),
  PoseEstimate: PoseEstimateSchema,
  PointingHint: PointingHintSchema,
};
/** The objects whose keys the contract states, by schema: a closed one (z.object().strict()) refuses any other key. */
const OBJECTS: Record<string, z.AnyZodObject> = {
  ObservationSolution: ObservationSolutionSchema.innerType(),
  CelestialFinding: findingVariants[0]!,
  SourceDetection: SourceDetectionSchema,
  SkyPosition: SkyPositionSchema,
  ScientificEvidence: ScientificEvidenceSchema,
  InferredDistance: observedShape.inferredDistancePc.unwrap(),
  CatalogReference: CatalogReferenceSchema.innerType(),
  CatalogDistance: referenceShape.bailerJonesDistancePc.unwrap(),
  WCSCalibrationModel: WCSCalibrationModelSchema,
  SolutionQuality: solutionShape.quality.unwrap(),
  DetectionQuality: DetectionQualitySchema.innerType(),
  StarProfile: StarProfileSchema,
  SolverIdentity: solutionShape.solver.unwrap(),
  ObserverLocation: solutionShape.observerLocation.unwrap(),
  PoseEstimate: PoseEstimateSchema.innerType(),
  PointingHint: PointingHintSchema.innerType(),
};
const SHAPES: Record<string, z.ZodRawShape> = {
  ObservationSolution: solutionShape,
  CelestialFinding: observedShape,
  SourceDetection: SourceDetectionSchema.shape,
  SkyPosition: SkyPositionSchema.shape,
  ScientificEvidence: ScientificEvidenceSchema.shape,
  InferredDistance: observedShape.inferredDistancePc.unwrap().shape,
  CatalogReference: referenceShape,
  CatalogDistance: referenceShape.bailerJonesDistancePc.unwrap().shape,
  WCSCalibrationModel: WCSCalibrationModelSchema.shape,
  SolutionQuality: solutionShape.quality.unwrap().shape,
  DetectionQuality: DetectionQualitySchema.innerType().shape,
  StarProfile: StarProfileSchema.shape,
  SolverIdentity: solutionShape.solver.unwrap().shape,
  ObserverLocation: solutionShape.observerLocation.unwrap().shape,
  PoseEstimate: PoseEstimateSchema.innerType().shape,
  PointingHint: PointingHintSchema.innerType().shape,
};

/** Every object of the family inside `value`, as the schema that judges it and where it is. */
function instances(schema: string, value: Sample, where: Key[] = []): { schema: string; path: Key[] }[] {
  const found = [{ schema, path: where }];
  for (const [name, entry] of Object.entries(fixture.fields[schema]!)) {
    if (entry.schema === undefined || !(name in value)) continue;
    if (entry.array) {
      (value[name] as Sample[]).forEach((item, index) => found.push(...instances(entry.schema!, item, [...where, name, index])));
    } else {
      found.push(...instances(entry.schema, value[name] as Sample, [...where, name]));
    }
  }
  return found;
}

describe('@albedian/contracts: the observation solution fixture both sides judge', () => {
  it('holds the held-out gate numbers the Python solver defines, and the evidence package keeps the same two', () => {
    expect({ minHeldOutInliers: MIN_HELD_OUT_INLIERS, maxMedianResidualPx: MAX_HELD_OUT_MEDIAN_RESIDUAL_PX }).toEqual(fixture.heldOutGate);
    expect(SWEPT_FOV_TOLERANCE_DEG).toBe(fixture.sweptFovToleranceDeg);
    // packages/evidence depends on this package, so it cannot be imported here: its two numbers are read from its
    // source until it imports them from this package
    const evidence = fs.readFileSync(EVIDENCE_PACKAGE, 'utf8');
    expect(Number(/export const MIN_HELD_OUT_INLIERS = ([0-9.]+);/.exec(evidence)?.[1])).toBe(MIN_HELD_OUT_INLIERS);
    expect(Number(/export const MAX_MEDIAN_RESIDUAL_PX = ([0-9.]+);/.exec(evidence)?.[1])).toBe(MAX_HELD_OUT_MEDIAN_RESIDUAL_PX);
  });

  it('shares its patterns and its enums with the Python parser', () => {
    expect(fixture.patterns.sha256).toBe(SHA256_HEX_PATTERN);
    // Zod's own pattern for datetime(), with [0-9] for \d: the Python side runs it where \d takes any Unicode digit
    expect(z.datetimeRegex({ precision: null }).source.replaceAll('\\d', '[0-9]')).toBe(fixture.patterns.utcInstant);
    expect(fixture.rules['solutionStatus']!.values).toEqual(SolutionStatusSchema.options);
    expect(fixture.rules['evidenceClass']!.values).toEqual(EvidenceClassSchema.options);
    expect(fixture.rules['poseMethod']!.values).toEqual(PoseEstimateSchema.innerType().shape.method.options);
    expect(fixture.rules['poseBeacon']!.values).toEqual(PoseEstimateSchema.innerType().shape.beacon.unwrap().options);
    expect(fixture.rules['pointingUse']!.values).toEqual(PointingHintSchema.innerType().shape.usedAs.element.options);
    expect(fixture.rules['momentSource']!.values).toEqual(PointingHintSchema.innerType().shape.timeFrom.options);
    expect(fixture.rules['momentSource']!.values).toEqual(PointingHintSchema.innerType().shape.placeFrom.options);
    expect(fixture.rules['catalogProvider']!.values).toEqual(referenceShape.provider.options);
    expect(fixture.rules['catalogObjectType']!.values).toEqual(referenceShape.objectType.options);
    expect(fixture.rules['catalogDistanceMethod']!.values).toEqual(referenceShape.bailerJonesDistancePc.unwrap().shape.method.options);
    const detectionShape = DetectionQualitySchema.innerType().shape;
    expect(fixture.rules['detectionStatistic']!.values).toEqual(detectionShape.statistic.options);
    expect(fixture.rules['noiseLimit']!.values).toEqual(detectionShape.noiseLimits.element.options);
    expect(fixture.rules['detectionEvidenceClass']!.values).toEqual([detectionShape.evidenceClass.value]);
  });

  it("holds the detector's star profile bounds and lists as the same literals the Python parser holds", () => {
    expect(fixture.detection).toEqual({
      profileMinSigmaPx: PROFILE_MIN_SIGMA_PX,
      profileMaxSigmaPx: PROFILE_MAX_SIGMA_PX,
      profileMinStars: PROFILE_MIN_STARS,
      profileMaxStars: PROFILE_MAX_STARS,
      profileAngleLimitRad: PROFILE_ANGLE_LIMIT_RAD,
      statistics: [...DETECTION_STATISTICS],
      noiseLimits: [...NOISE_LIMITS],
    });
    // Half pi as a literal: Math.PI halved is exact on every machine, so this holds wherever it runs
    expect(PROFILE_ANGLE_LIMIT_RAD).toBe(Math.PI / 2);
    expect(fixture.rules['profileSigmaPx']).toMatchObject({ ge: PROFILE_MIN_SIGMA_PX, le: PROFILE_MAX_SIGMA_PX });
    expect(fixture.rules['profileAngleRad']).toMatchObject({ gt: -PROFILE_ANGLE_LIMIT_RAD, le: PROFILE_ANGLE_LIMIT_RAD });
    expect(fixture.rules['profileStars']).toMatchObject({ ge: PROFILE_MIN_STARS, le: PROFILE_MAX_STARS, integer: true });
  });

  it("holds the noise limits' order rule, as the Python parser does, to an order of two values", () => {
    // With the contract's own list, as the schema uses it, out-of-set values return false cleanly
    expect(listsEachOnceInOrder([])).toBe(true);
    expect(listsEachOnceInOrder(['near_black_clip'])).toBe(true);
    expect(listsEachOnceInOrder(['near_black_clip', 'near_black_clip'])).toBe(false);
    expect(listsEachOnceInOrder(['quantised'])).toBe(false);
    expect(listsEachOnceInOrder(['quantised', 'near_black_clip'])).toBe(false);
    expect(listsEachOnceInOrder(['matched'])).toBe(false);
    // NOISE_LIMITS holds one value today, so the samples alone could not tell the order from a repeat; these cases can
    const { order, cases } = fixture.noiseLimitOrderCases;
    expect(cases.length).toBeGreaterThanOrEqual(14);
    for (const { limits, listed } of cases) expect(listsEachOnceInOrder(limits, order), JSON.stringify(limits)).toBe(listed);
  });

  it('normalizes minus zero to zero on StarProfile.angleRad across StarProfile, DetectionQuality, and ObservationSolution', () => {
    const rawProfile = JSON.parse('{"sigmaMajorPx":2.1,"sigmaMinorPx":1.6,"angleRad":-0.0,"stars":37}');
    const profile = StarProfileSchema.parse(rawProfile);
    expect(Object.is(profile.angleRad, -0)).toBe(false);
    expect(Object.is(profile.angleRad, 0)).toBe(true);

    const rawDq = JSON.parse(
      '{"evidenceClass":"observed","statistic":"whitened","noiseLimits":[],"starProfile":{"sigmaMajorPx":2.1,"sigmaMinorPx":1.6,"angleRad":-0.0,"stars":37}}',
    );
    const dq = DetectionQualitySchema.parse(rawDq);
    expect(Object.is(dq.starProfile?.angleRad, -0)).toBe(false);
    expect(Object.is(dq.starProfile?.angleRad, 0)).toBe(true);

    const solRaw = JSON.stringify(fixture.valid['calibrated_at_the_gate']).replace('"angleRad":0.5', '"angleRad":-0.0');
    const sol = ObservationSolutionSchema.parse(JSON.parse(solRaw));
    expect(Object.is(sol.quality?.detection?.starProfile?.angleRad, -0)).toBe(false);
    expect(Object.is(sol.quality?.detection?.starProfile?.angleRad, 0)).toBe(true);
  });

  it('reports field issues alongside extra keys on closed records and only Invalid datetime when observedAtUTC fails syntax', () => {
    const snakeOnly = StarProfileSchema.safeParse({ sigma_major_px: 2.1, sigma_minor_px: 1.6, angle_rad: 0.5, stars: 37 });
    expect(issuePaths(snakeOnly)).toEqual(['', 'angleRad', 'sigmaMajorPx', 'sigmaMinorPx']);

    const nullAndExtra = DetectionQualitySchema.safeParse({
      evidenceClass: 'observed',
      statistic: 'matched',
      noiseLimits: [],
      starProfile: null,
      extra: 1,
    });
    expect(issuePaths(nullAndExtra)).toEqual(['', 'starProfile']);

    const badClassAndExtra = DetectionQualitySchema.safeParse({
      evidenceClass: 'predicted',
      statistic: 'matched',
      noiseLimits: [],
      extra: 1,
    });
    expect(issuePaths(badClassAndExtra)).toEqual(['', 'evidenceClass']);

    const approx = fixture.valid['approximate']!;
    const realDaySentence = 'A UTC instant falls on a real calendar day, in a year from 1 to 9999.';
    for (const badSyntax of ['not-a-date', '2031-02-29T00:00:00Z', '1900-02-29T00:00:00Z', '0000-02-30T00:00:00Z']) {
      const parsed = ObservationSolutionSchema.safeParse({ ...approx, observedAtUTC: badSyntax });
      expect(issuePaths(parsed), badSyntax).toEqual(['observedAtUTC']);
      expect(issueMessages(parsed), badSyntax).toEqual(['Invalid datetime']);
      expect(issueMessages(parsed), badSyntax).not.toContain(realDaySentence);
    }
    for (const yearZero of ['0000-01-01T00:00:00Z', '0000-02-29T00:00:00Z', '0000-12-31T23:59:59.999Z']) {
      const parsed = ObservationSolutionSchema.safeParse({ ...approx, observedAtUTC: yearZero });
      expect(issuePaths(parsed), yearZero).toEqual(['observedAtUTC']);
      expect(issueMessages(parsed), yearZero).toEqual([realDaySentence]);
    }
  });

  it("holds an integer rule's edge samples on the Zod side too, across upper bounds alone and narrow ranges", () => {
    expect(fixture.integerRuleEdgeCases.length).toBeGreaterThanOrEqual(11);
    for (const { name, entry } of fixture.integerRuleEdgeCases) {
      let schema = z.number().int();
      if (entry.ge !== undefined) schema = schema.min(entry.ge);
      if (entry.gt !== undefined) schema = schema.gt(entry.gt);
      if (entry.le !== undefined) schema = schema.max(entry.le);
      for (const sample of entry.accept) {
        expect(schema.safeParse(decode(sample)).success, `${name} accept ${JSON.stringify(sample)}`).toBe(true);
      }
      for (const sample of entry.refuse) {
        expect(schema.safeParse(decode(sample)).success, `${name} refuse ${JSON.stringify(sample)}`).toBe(false);
      }
    }
  });

  it('closes exactly the records the fixture names as closed, and leaves the older ones to drop a key they do not name', () => {
    expect(Object.keys(OBJECTS).sort()).toEqual(Object.keys(SHAPES).sort());
    for (const [schema, object] of Object.entries(OBJECTS)) {
      expect(Object.keys(object.shape).sort(), schema).toEqual(Object.keys(SHAPES[schema]!).sort());
      expect(object._def.unknownKeys, schema).toBe(fixture.closedSchemas.includes(schema) ? 'strict' : 'strip');
    }
    expect(fixture.closedSchemas).toEqual(['DetectionQuality', 'StarProfile']);
  });

  it.each(Object.keys(fixture.valid))('parses the valid sample %s and returns it unchanged', (name) => {
    const parsed = ObservationSolutionSchema.safeParse(fixture.valid[name]);
    expect(parsed.error?.issues ?? []).toEqual([]);
    expect(parsed.data).toEqual(fixture.valid[name]);
  });

  it.each(fixture.invalid.map((entry) => [entry.name, entry] as const))('refuses %s where and as the contract says', (_name, entry) => {
    const parsed = ObservationSolutionSchema.safeParse(patched(fixture.valid[entry.base], entry.patch));
    expect(parsed.success, entry.reason).toBe(false);
    expect(issuePaths(parsed)).toEqual(entry.paths);
    expect(entry.messages.filter((sentence) => !issueMessages(parsed).includes(sentence))).toEqual([]);
  });

  it('drops a key the contract does not name', () => {
    const parsed = ObservationSolutionSchema.safeParse(fixture.unnamedKeys.sample);
    expect(parsed.error?.issues ?? []).toEqual([]);
    expect(parsed.data).toEqual(fixture.unnamedKeys.parsed);
  });

  it('holds every count and revision to MAX_SAFE_INTEGER, refusing raw JSON integers above 2^53 - 1 that JSON.parse rounds', () => {
    const unresolvedRaw = JSON.stringify(fixture.valid['unresolved_pixels_only']).replace(
      /"heldOutPairCount":\d+,"heldOutInliers":\d+/,
      '"heldOutPairCount":9007199254740995,"heldOutInliers":9007199254740996'
    );
    const pairSplit = ObservationSolutionSchema.safeParse(JSON.parse(unresolvedRaw));
    expect(pairSplit.success).toBe(false);
    expect(issuePaths(pairSplit)).toEqual(['quality.heldOutInliers', 'quality.heldOutPairCount']);

    for (const unsafeLiteral of ['9007199254740992', '9007199254740993']) {
      const revRaw = JSON.stringify(fixture.valid['calibrated']).replace(/"revision":\d+/, `"revision":${unsafeLiteral}`);
      const parsedRev = ObservationSolutionSchema.safeParse(JSON.parse(revRaw));
      expect(parsedRev.success, unsafeLiteral).toBe(false);
      expect(issuePaths(parsedRev), unsafeLiteral).toEqual(['revision']);
    }

    const ceiling = patched(fixture.valid['unresolved_pixels_only'], [
      put(['revision'], Number.MAX_SAFE_INTEGER),
      put(['quality', 'detectedCandidateCount'], Number.MAX_SAFE_INTEGER),
      put(['quality', 'matchedInlierCount'], Number.MAX_SAFE_INTEGER),
      put(['quality', 'heldOutPairCount'], Number.MAX_SAFE_INTEGER),
      put(['quality', 'heldOutInliers'], Number.MAX_SAFE_INTEGER),
    ]);
    const accepted = ObservationSolutionSchema.safeParse(JSON.parse(JSON.stringify(ceiling)));
    expect(accepted.success).toBe(true);
    expect(accepted.data).toEqual(ceiling);
  });

  it('never reads an unknown key or a Python-named key, at any level of any record, as the Python parser never does', () => {
    // Generated from the field table, like key_cases in tests/integration/test_observation_solution_contract.py,
    // which wrote the counts this must match: every record at every level of every valid sample, the findings
    // judged on their own among them
    const levels = [
      ...Object.entries(fixture.valid).flatMap(([name, sample]) =>
        instances('ObservationSolution', sample).map((site) => ({ name, sample, parse: ObservationSolutionSchema as z.ZodTypeAny, ...site })),
      ),
      ...Object.entries(fixture.findings.valid).flatMap(([name, sample]) =>
        instances('CelestialFinding', sample).map((site) => ({ name: `finding ${name}`, sample, parse: CelestialFindingSchema as z.ZodTypeAny, ...site })),
      ),
    ];
    expect(new Set(levels.map((level) => level.schema))).toEqual(new Set(Object.keys(SHAPES)));
    const unknownValues: unknown[] = [1, 'text', null, true, [1, 2], { nested: { deeper: 1 } }];
    const counts = { levels: 0, dropped: 0, refusedUnknown: 0, refusedWhenRenamed: 0, leftOutWhenRenamed: 0 };
    for (const level of levels) {
      const where = `${level.name} ${level.schema} ${dotted(level.path)}`;
      const record = at(level.sample, level.path) as Sample;
      const names = Object.entries(fixture.pythonNames[level.schema]!).filter(([wire]) => wire in record);
      // A key no schema names, whatever its value, and the Python name of every field set beside its wire name,
      // all at once and one at a time: each is dropped, and the record parses to what it was; a closed record
      // refuses them at the record itself, in Zod's words, which the Python parser repeats
      const twins = names.map(([wire, python]) => put([...level.path, python], record[wire]));
      const added = [...unknownValues.map((value) => [put([...level.path, 'unknownToTheContract'], value)]), twins, ...twins.map((twin) => [twin])];
      for (const steps of added) {
        const parsed = level.parse.safeParse(patched(level.sample, steps));
        if (fixture.closedSchemas.includes(level.schema)) {
          expect(issuePaths(parsed), where).toEqual([dotted(level.path)]);
          const keys = steps.map((step) => `'${String(step.path[step.path.length - 1])}'`).join(', ');
          expect(issueMessages(parsed), where).toEqual([`Unrecognized key(s) in object: ${keys}`]);
          counts.refusedUnknown += 1;
          continue;
        }
        expect(parsed.error?.issues ?? [], where).toEqual([]);
        expect(parsed.data, where).toEqual(level.sample);
        counts.dropped += 1;
      }
      // A field carried only under its Python name is not carried: refused where a required one is missing, or at
      // its record when a rule of the record needs it, and left out when it is optional
      for (const [wire, python] of names) {
        const parsed = level.parse.safeParse(patched(level.sample, [put([...level.path, python], record[wire]), drop([...level.path, wire])]));
        if (parsed.success) {
          expect(parsed.data, `${where} ${wire}`).toEqual(patched(level.sample, [drop([...level.path, wire])]));
          counts.leftOutWhenRenamed += 1;
        } else {
          const paths = issuePaths(parsed);
          // Or, for a catalog entry, at the id in the evidence that names it (which then names nothing)
          const tied = level.schema === 'CelestialFinding' && wire === 'catalogRef' && paths.includes(dotted([...level.path, 'evidence', 'catalogReferenceId']));
          expect(paths.includes(dotted([...level.path, wire])) || paths.includes(dotted(level.path)) || tied, `${where} ${wire}`).toBe(true);
          counts.refusedWhenRenamed += 1;
        }
      }
      counts.levels += 1;
    }
    expect(counts).toEqual(fixture.keyCases);
    expect(counts.levels).toBeGreaterThanOrEqual(100);
  });

  it('judges findings on their own as the Python parser does', () => {
    for (const [name, sample] of Object.entries(fixture.findings.valid)) {
      const parsed = CelestialFindingSchema.safeParse(sample);
      expect(parsed.error?.issues ?? [], name).toEqual([]);
      expect(parsed.data, name).toEqual(sample);
    }
    for (const entry of fixture.findings.invalid) {
      const parsed = CelestialFindingSchema.safeParse(patched(fixture.findings.valid[entry.base], entry.patch));
      expect(parsed.success, entry.reason).toBe(false);
      expect(issuePaths(parsed), entry.name).toEqual(entry.paths);
      expect(entry.messages.filter((sentence) => !issueMessages(parsed).includes(sentence)), entry.name).toEqual([]);
    }
    // The neighbours of every refused cell: a field a class may hold, held or left out, is taken
    expect(fixture.findings.taken.length).toBeGreaterThanOrEqual(4 * 5);
    for (const entry of fixture.findings.taken) {
      const sample = patched(fixture.findings.valid[entry.base], entry.patch);
      const parsed = CelestialFindingSchema.safeParse(sample);
      expect(parsed.error?.issues ?? [], entry.name).toEqual([]);
      expect(parsed.data, entry.name).toEqual(sample);
    }
  });

  it('reads the one class table the Python parser reads: every field once per class, each row citing its sentences', () => {
    expect(FINDING_CLASS_EVIDENCE).toEqual(fixture.findingClassEvidence);
    expect(FINDING_EVIDENCE_RULES).toEqual(fixture.findingEvidenceRules);
    expect([...FINDING_FIELDS]).toEqual(fixture.findingFields);
    expect(Object.keys(FINDING_CLASS_EVIDENCE).sort()).toEqual([...EvidenceClassSchema.options].sort());
    for (const [evidenceClass, own] of Object.entries(FINDING_CLASS_EVIDENCE)) {
      const placed = [...Object.keys(own.must), ...Object.keys(own.mustNot), ...own.may];
      expect([...placed].sort(), evidenceClass).toEqual([...FINDING_FIELDS].sort());
      expect(new Set(placed).size, evidenceClass).toBe(placed.length);
      expect(own.source.some((source) => source.startsWith(`AGENTS.md section 1: "${evidenceClass}:`)), evidenceClass).toBe(true);
    }
  });

  it('changes what some sample is told when any cell of the class table is flipped', () => {
    type Table = Parameters<typeof findingClassIssues>[0];
    const bases = new Set(['observed', 'catalog_matched', 'predicted_for_a_time_and_place_alone', 'simulated_alone']);
    const cases = [...fixture.findings.invalid.filter((entry) => bases.has(entry.base)), ...fixture.findings.taken];
    const verdicts = (table: Table) =>
      cases.map((entry) => {
        const sample = patched(fixture.findings.valid[entry.base], entry.patch) as unknown as Parameters<typeof findingClassIssues>[1];
        // Each class issue, its path and its sentence: a field two rules refuse is told so by both
        return [...new Set(findingClassIssues(table, sample).map((issue) => `${issue.path.join('.')}: ${issue.message}`))].sort();
      });
    const asItIs = verdicts(FINDING_CLASS_EVIDENCE);
    let flips = 0;
    for (const [evidenceClass, own] of Object.entries(FINDING_CLASS_EVIDENCE)) {
      for (const field of FINDING_FIELDS) {
        const here = field in own.must ? 'must' : field in own.mustNot ? 'mustNot' : 'may';
        for (const there of (['must', 'mustNot', 'may'] as const).filter((cell) => cell !== here)) {
          const flipped = structuredClone(FINDING_CLASS_EVIDENCE) as unknown as Record<string, { must: Record<string, string>; mustNot: Record<string, string>; may: string[] }>;
          const cell = flipped[evidenceClass]!;
          if (here === 'may') cell.may = cell.may.filter((name) => name !== field);
          else delete cell[here][field];
          if (there === 'may') cell.may.push(field);
          else cell[there][field] = `flipped ${field}`;
          expect(verdicts(flipped as unknown as Table), `${evidenceClass} ${field} ${here} to ${there}`).not.toEqual(asItIs);
          flips += 1;
        }
      }
    }
    expect(flips).toBe(Object.keys(FINDING_CLASS_EVIDENCE).length * FINDING_FIELDS.length * 2);
  });

  it.each([null, [], 'solution', 5, true])('refuses %j, which is not an object', (payload) => {
    expect(ObservationSolutionSchema.safeParse(payload).success).toBe(false);
    expect(CelestialFindingSchema.safeParse(payload).success).toBe(false);
  });
});

describe('@albedian/contracts: the field table of the observation solution family', () => {
  it('names every field of every schema, with the same optional fields', () => {
    expect(Object.keys(fixture.fields).sort()).toEqual(Object.keys(SHAPES).sort());
    expect(Object.keys(fixture.homes).sort()).toEqual(Object.keys(SHAPES).sort());
    for (const [schema, shape] of Object.entries(SHAPES)) {
      const table = fixture.fields[schema]!;
      expect(Object.keys(table).sort(), schema).toEqual(Object.keys(shape).sort());
      for (const [name, entry] of Object.entries(table)) {
        const field = shape[name]!;
        expect(field.isOptional(), `${schema}.${name}`).toBe(entry.optional ?? false);
        const inner = field instanceof z.ZodOptional ? (field.unwrap() as z.ZodTypeAny) : field;
        expect(inner instanceof z.ZodArray || inner instanceof z.ZodTuple, `${schema}.${name}`).toBe(entry.array ?? false);
        expect((entry.rule === undefined) !== (entry.schema === undefined), `${schema}.${name}`).toBe(true);
        if (entry.rule !== undefined) expect(fixture.rules[entry.rule], `${schema}.${name}`).toBeDefined();
        else expect(SHAPES[entry.schema!], `${schema}.${name}`).toBeDefined();
      }
    }
    // The four kinds of finding differ in one thing: only an observed one may leave its sky position out
    for (const variant of findingVariants) {
      expect(Object.keys(variant.shape).sort()).toEqual(Object.keys(observedShape).sort());
      for (const [name, field] of Object.entries(variant.shape)) {
        if (name === 'evidenceClass') continue;
        const optional = name === 'skyPosition' ? variant === findingVariants[0] : (fixture.fields['CelestialFinding']![name]!.optional ?? false);
        expect(field.isOptional(), name).toBe(optional);
      }
    }
    expect(findingVariants.map((variant) => variant.shape.evidenceClass.value)).toEqual(fixture.rules['evidenceClass']!.values);
    // Every object the valid samples hold sits where the table says a schema of the family does
    const seen = new Set(Object.values(fixture.valid).flatMap((sample) => instances('ObservationSolution', sample).map((site) => site.schema)));
    expect([...seen].sort()).toEqual(Object.keys(SHAPES).sort());
  });

  /** What is tried, the schema's home object with one field changed, and whether the contract accepts it. */
  function edgeCases(schema: string): { tried: string; payload: Sample; accepted: boolean }[] {
    const home = fixture.homes[schema]!;
    const base = at(fixture.valid[home.sample], home.path) as Sample;
    const having = (field: string, value: unknown): Sample => ({ ...structuredClone(base), [field]: decode(structuredClone(value)) });
    const cases: { tried: string; payload: Sample; accepted: boolean }[] = [];
    for (const [field, entry] of Object.entries(fixture.fields[schema]!)) {
      if (entry.rule !== undefined) {
        const rule = fixture.rules[entry.rule]!;
        const copies = Math.max(entry.minItems ?? 1, 1);
        for (const verdict of ['accept', 'refuse'] as const) {
          for (const value of rule[verdict]) {
            const tried = entry.array ? Array.from({ length: copies }, () => value) : value;
            cases.push({ tried: `${schema}.${field} ${verdict}s ${JSON.stringify(value)}`, payload: having(field, tried), accepted: verdict === 'accept' });
          }
        }
        // A text a pattern takes, with a line break before or after it, is refused (the Python parser's fullmatch)
        if (rule.type === 'text' && rule.pattern !== undefined) {
          for (const value of rule.accept.filter((item): item is string => typeof item === 'string')) {
            for (const broken of [`${value}\n`, `\n${value}`, `${value}\r\n`]) {
              const tried = entry.array ? Array.from({ length: copies }, () => broken) : broken;
              cases.push({ tried: `${schema}.${field} refuses ${JSON.stringify(broken)}`, payload: having(field, tried), accepted: false });
            }
          }
        }
      }
      const kind = entry.array ? 'array' : entry.schema !== undefined ? 'object' : undefined;
      if (kind !== undefined) {
        for (const value of fixture.structureRefusals[kind]) {
          cases.push({ tried: `${schema}.${field} refuses ${JSON.stringify(value)}`, payload: having(field, value), accepted: false });
        }
      }
      if (entry.array && field in base) {
        const items = base[field] as unknown[];
        if ((entry.minItems ?? 0) > 0) {
          cases.push({ tried: `${schema}.${field} refuses too few`, payload: having(field, items.slice(0, entry.minItems! - 1)), accepted: false });
        }
        if (entry.maxItems !== undefined) {
          const tooMany = Array.from({ length: entry.maxItems + 1 }, () => items[0]);
          cases.push({ tried: `${schema}.${field} refuses too many`, payload: having(field, tooMany), accepted: false });
        }
      }
      if (!(entry.optional ?? false)) {
        const { [field]: _removed, ...rest } = structuredClone(base);
        cases.push({ tried: `${schema}.${field} is required`, payload: rest, accepted: false });
      }
    }
    return cases;
  }

  it.each(Object.keys(fixture.fields))('holds every field of %s to its rule at the edges', (schema) => {
    const home = fixture.homes[schema]!;
    expect(SCHEMAS[schema]!.safeParse(at(fixture.valid[home.sample], home.path)).success).toBe(true);
    const cases = edgeCases(schema);
    const wrong = cases.filter(({ payload, accepted }) => SCHEMAS[schema]!.safeParse(payload).success !== accepted).map(({ tried }) => tried);
    expect(wrong).toEqual([]);
    expect(cases.length).toBeGreaterThanOrEqual(4 * Object.keys(fixture.fields[schema]!).length);
  });
});

const BASES: Record<string, Sample> = {
  ...fixture.valid,
  ...Object.fromEntries(Object.entries(pythonSolutions).map(([name, sample]) => [`python:${name}`, sample])),
};
const SITES = Object.entries(BASES).flatMap(([base, sample]) => instances('ObservationSolution', sample).map((site) => ({ base, ...site })));
const finite = { noNaN: true, noDefaultInfinity: true } as const;

/** Values outside the rule: the table's own, and for a number any below, above or between its bounds. */
function outside(rule: Rule): fc.Arbitrary<unknown> {
  const options: fc.Arbitrary<unknown>[] = [fc.constantFrom(...rule.refuse.map(decode))];
  if (rule.type === 'number') {
    // The filters keep -0 out where it equals the bound: a bound of 0 takes -0 as it takes 0
    const { ge, gt, le } = rule;
    if (ge !== undefined) options.push(fc.double({ max: ge, ...finite }).filter((value) => value < ge));
    if (gt !== undefined) options.push(fc.double({ max: gt, ...finite }).filter((value) => value <= gt));
    if (le !== undefined) options.push(fc.double({ min: le, ...finite }).filter((value) => value > le));
    if (rule.integer) options.push(fc.double({ min: 0, max: 1e6, ...finite }).filter((value) => !Number.isInteger(value)));
  }
  return fc.oneof(...options);
}

/** Values the rule accepts. */
function inside(rule: Rule): fc.Arbitrary<unknown> {
  const fixed = rule.accept.length > 0 ? fc.constantFrom(...rule.accept.map(decode)) : undefined;
  if (rule.type === 'number') {
    const low = rule.ge ?? rule.gt ?? -1e6;
    const high = rule.le ?? 1e6;
    const drawn = rule.integer
      ? fc.integer({ min: rule.gt !== undefined ? Math.floor(low) + 1 : Math.ceil(low), max: Math.min(high, 2 ** 40) })
      : fc.double({ min: low, max: high, minExcluded: rule.gt !== undefined, ...finite });
    return fixed === undefined ? drawn : fc.oneof(drawn, fixed);
  }
  if (rule.type === 'text' && rule.pattern === undefined) return fc.string({ minLength: rule.minLength ?? 0, maxLength: 12 });
  if (rule.type === 'choice') return fc.constantFrom(...rule.values!);
  return fixed!;
}

const faultArb = fc.constantFrom(...SITES).chain((site) => {
  const names = Object.keys(fixture.fields[site.schema]!).sort();
  return fc.constantFrom(...names).chain((field) => {
    const entry = fixture.fields[site.schema]![field]!;
    const where = [...site.path, field];
    const present = field in (at(BASES[site.base], site.path) as Sample);
    const value: fc.Arbitrary<{ where: Key[]; step: Step }> =
      entry.rule !== undefined
        ? outside(fixture.rules[entry.rule]!).map((drawn) =>
            entry.array
              ? { where: [...where, 0], step: put(where, Array.from({ length: Math.max(entry.minItems ?? 1, 1) }, () => drawn)) }
              : { where, step: put(where, drawn) }
          )
        : fc.constantFrom(...fixture.structureRefusals[entry.array ? 'array' : 'object']).map((drawn) => ({ where, step: put(where, drawn) }));
    const ways = !(entry.optional ?? false) && present ? fc.oneof(value, fc.constant({ where, step: drop(where) })) : value;
    return ways.map((fault) => ({ base: site.base, ...fault }));
  });
});

describe('@albedian/contracts: properties of the observation solution contract', () => {
  it('P-CONTRACT-2: one field of a valid sample moved outside its contract is refused at that field', () => {
    fc.assert(
      fc.property(faultArb, ({ base, where, step }) => {
        const parsed = ObservationSolutionSchema.safeParse(patched(BASES[base], [step]));
        expect(parsed.success).toBe(false);
        expect(issuePaths(parsed)).toContain(dotted(where));
      }),
      {
        seed: FC_SEED,
        numRuns: 300,
        // The rare ones, every run: not a number, not finite, an unknown status, a fraction for a count, the
        // placeholder hash, a time with an offset, a flux of zero, a missing list
        examples: [
          [{ base: 'calibrated', where: ['findings', 0, 'catalogRef', 'bayerDesignation'], step: put(['findings', 0, 'catalogRef', 'bayerDesignation'], null) }],
          [{ base: 'calibrated_at_the_gate', where: ['quality', 'heldOutPairCount'], step: put(['quality', 'heldOutPairCount'], 2 ** 53) }],
          [{ base: 'calibrated_at_the_gate', where: ['quality', 'medianResidualPx'], step: put(['quality', 'medianResidualPx'], Number.NaN) }],
          [{ base: 'calibrated_at_the_gate', where: ['wcs', 'rotationDeg'], step: put(['wcs', 'rotationDeg'], Number.POSITIVE_INFINITY) }],
          [{ base: 'calibrated_at_the_gate', where: ['status'], step: put(['status'], 'Calibrated') }],
          [{ base: 'calibrated_at_the_gate', where: ['revision'], step: put(['revision'], 1.5) }],
          [{ base: 'calibrated_at_the_gate', where: ['sourceSha256'], step: put(['sourceSha256'], '0'.repeat(64)) }],
          [{ base: 'approximate', where: ['observedAtUTC'], step: put(['observedAtUTC'], '2030-01-01T02:00:00+00:00') }],
          [{ base: 'approximate', where: ['findings', 0, 'sourceDetection', 'flux'], step: put(['findings', 0, 'sourceDetection', 'flux'], 0) }],
          [{ base: 'approximate', where: ['findings'], step: drop(['findings']) }],
          // What the detector measured: a fraction of a star, the angle's excluded end, the noise tail
          [{ base: 'calibrated_at_the_gate', where: ['quality', 'detection', 'starProfile', 'stars'], step: put(['quality', 'detection', 'starProfile', 'stars'], 37.5) }],
          [{ base: 'ambiguous', where: ['quality', 'detection', 'starProfile', 'angleRad'], step: put(['quality', 'detection', 'starProfile', 'angleRad'], -PROFILE_ANGLE_LIMIT_RAD) }],
          [{ base: 'unresolved_pixels_only', where: ['quality', 'detection', 'noiseLimits', 0], step: put(['quality', 'detection', 'noiseLimits'], ['noise_tail']) }],
        ],
      }
    );
  });

  it('P-CONTRACT-1: an accepted sample comes back unchanged', () => {
    const changeArb = fc.constantFrom(...Object.keys(BASES).sort()).chain((base) => {
      const sites = SITES.filter((site) => site.base === base);
      const stepArb = fc.constantFrom(...sites).chain((site) => {
        const names = Object.entries(fixture.fields[site.schema]!)
          .filter(([, entry]) => entry.rule !== undefined)
          .map(([name]) => name)
          .sort();
        return fc.constantFrom(...names).chain((field) => {
          const entry = fixture.fields[site.schema]![field]!;
          return inside(fixture.rules[entry.rule!]!).map((drawn) =>
            put([...site.path, field], entry.array ? Array.from({ length: Math.max(entry.minItems ?? 1, 1) }, () => drawn) : drawn)
          );
        });
      });
      return fc.array(stepArb, { maxLength: 3 }).map((steps) => ({ base, steps }));
    });
    let accepted = 0;
    fc.assert(
      fc.property(changeArb, ({ base, steps }) => {
        const sample = patched(BASES[base], steps);
        const parsed = ObservationSolutionSchema.safeParse(sample);
        // A change inside each field's own rule may still break a rule between fields
        if (!parsed.success) return void expect(steps.length).toBeGreaterThan(0);
        accepted += 1;
        expect(parsed.data).toEqual(sample);
        expect(ObservationSolutionSchema.parse(parsed.data)).toEqual(sample);
      }),
      {
        seed: FC_SEED,
        numRuns: 300,
        examples: [
          [{ base: 'calibrated', steps: [] }],
          [{ base: 'approximate_with_pose', steps: [put(['poseEstimate', 'rollDeg'], -180)] }],
          [{ base: 'calibrated_at_the_gate', steps: [put(['revision'], 7), put(['frameTimeSec'], 3)] }],
          [{ base: 'pending_without_a_recording', steps: [put(['warnings'], ['', 'x'])] }],
          [{ base: 'ambiguous', steps: [put(['quality', 'detection', 'starProfile', 'sigmaMajorPx'], PROFILE_MAX_SIGMA_PX)] }],
          [{ base: 'calibrated_at_the_gate', steps: [put(['quality', 'detection', 'starProfile', 'stars'], PROFILE_MAX_STARS)] }],
        ],
      }
    );
    expect(accepted).toBeGreaterThanOrEqual(4);
  });

  it('P-CONTRACT-3: a verification marked as passed is accepted exactly at or above the held-out gate', () => {
    const gate = fixture.valid['calibrated_at_the_gate']!;
    const least = fixture.heldOutGate.minHeldOutInliers;
    const most = fixture.heldOutGate.maxMedianResidualPx;
    // The next number above the gate's median, as the fixture's named sample carries it
    const justAbove = at(fixture.invalid.find((entry) => entry.name === 'gate_median_just_above')!.patch[0]!, ['value']) as number;
    expect(justAbove).toBeGreaterThan(most);
    fc.assert(
      fc.property(
        fc.record({ inliers: fc.integer({ min: 0, max: 14 }), morePairs: fc.integer({ min: 0, max: 4 }), median: fc.double({ min: 0, max: 8, noNaN: true }) }),
        ({ inliers, morePairs, median }) => {
          const pairs = Math.max(inliers + morePairs, 1);
          const sample = patched(gate, [
            put(['quality', 'heldOutInliers'], inliers),
            put(['quality', 'heldOutPairCount'], pairs),
            put(['quality', 'medianResidualPx'], median),
          ]);
          expect(ObservationSolutionSchema.safeParse(sample).success).toBe(inliers >= least && median <= most);
          // Not passed, the same counts are fine: the gate binds only the flag
          const notPassed = patched(sample, [put(['quality', 'passedIndependentVerification'], false), put(['status'], 'ambiguous'), drop(['wcs'])]);
          expect(ObservationSolutionSchema.safeParse(notPassed).success).toBe(true);
        }
      ),
      {
        seed: FC_SEED,
        numRuns: 300,
        examples: [
          [{ inliers: least, morePairs: 0, median: most }],
          [{ inliers: least - 1, morePairs: 1, median: most }],
          [{ inliers: least, morePairs: 0, median: justAbove }],
          [{ inliers: least + 5, morePairs: 3, median: 0 }],
          [{ inliers: 0, morePairs: 1, median: 0 }],
        ],
      }
    );
  });
});

describe('@albedian/contracts: calendar days and finite numbers in the ingest contract', () => {
  const INGEST: Record<string, z.ZodTypeAny> = { SolveJob: SolveJobSchema, CaptureTimeHint: CaptureTimeHintSchema, LocationHint: LocationHintSchema };

  it('accepts and refuses the samples the Python mirror accepts and refuses', () => {
    expect(fixture.ingest.invalid.length).toBeGreaterThanOrEqual(10);
    for (const { schema, sample } of fixture.ingest.valid) {
      expect(INGEST[schema]!.safeParse(decode(sample)).error?.issues ?? [], schema).toEqual([]);
    }
    for (const { schema, sample, reason } of fixture.ingest.invalid) {
      expect(INGEST[schema]!.safeParse(decode(sample)).success, `${schema} should refuse: ${reason}`).toBe(false);
    }
  });
});

describe('scripts/verify-contracts.mjs: the contract constants it requires', () => {
  const CONSTANTS = [
    'MIN_HELD_OUT_INLIERS',
    'MAX_HELD_OUT_MEDIAN_RESIDUAL_PX',
    'SWEPT_FOV_TOLERANCE_DEG',
    'PROFILE_MIN_SIGMA_PX',
    'PROFILE_MAX_SIGMA_PX',
    'PROFILE_MIN_STARS',
    'PROFILE_MAX_STARS',
    'PROFILE_ANGLE_LIMIT_RAD',
  ];

  /** The script run in a copy of what it reads (the contracts' sources and the hooks), after `edit` changed the copy. */
  function verified(edit: (root: string) => void) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-verify-contracts-'));
    try {
      for (const folder of ['packages/contracts/src', '.kiro/hooks']) {
        fs.mkdirSync(path.join(root, folder), { recursive: true });
        for (const entry of fs.readdirSync(path.join(REPOSITORY, folder), { withFileTypes: true })) {
          if (entry.isFile()) fs.copyFileSync(path.join(REPOSITORY, folder, entry.name), path.join(root, folder, entry.name));
        }
      }
      edit(root);
      const run = spawnSync(process.execPath, [path.join(REPOSITORY, 'scripts/verify-contracts.mjs')], { cwd: root, encoding: 'utf8' });
      return { status: run.status, out: run.stdout, err: run.stderr };
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }

  it('passes on the contracts as they are, and counts every constant it requires', () => {
    const run = verified(() => undefined);
    expect(run.err).toBe('');
    expect(run.status).toBe(0);
    expect(run.out).toContain(`${CONSTANTS.length} contract constants`);
  });

  it.each(CONSTANTS)('fails, naming it, when %s is no longer exported', (name) => {
    const run = verified((root) => {
      const file = path.join(root, 'packages/contracts/src/astronomy.ts');
      const source = fs.readFileSync(file, 'utf8');
      expect(source).toContain(`export const ${name} =`);
      fs.writeFileSync(file, source.replace(`export const ${name} =`, `const ${name} =`));
    });
    expect(run.status).toBe(1);
    expect(run.err).toContain('Missing required contract constants:');
    expect(run.err).toContain(name);
    for (const other of CONSTANTS.filter((constant) => constant !== name)) expect(run.err).not.toContain(other);
    expect(run.out).not.toContain('[verify:contracts] OK');
  });
});

// P-SKY-7b (pointing-3): a pose from the device attitude and the pointing hint, judged by the same oracle as
// test_p_sky_7b_the_pointing_hint_follows_its_solution in tests/integration/test_pose_estimate_contract.py, on the
// same payloads built from the fixture's valid samples
describe('P-SKY-7b: the pointing hint follows its solution', () => {
  const BASES = {
    calibrated: 'calibrated_with_a_pointing_hint',
    approximate: 'approximate_with_device_attitude_pose',
    predicted_only: 'predicted_only_with_a_search_hint',
  } as const;
  const USES = [
    ['search_hint'], ['pose_estimate'], ['search_hint', 'pose_estimate'], ['pose_estimate', 'search_hint'], ['search_hint', 'search_hint'],
    ['moment'], ['search_hint', 'moment'], ['search_hint', 'pose_estimate', 'moment'], ['pose_estimate', 'moment'], ['moment', 'search_hint'],
  ];
  const ORDER = ['search_hint', 'pose_estimate', 'moment'];
  const specArb = fc.record({
    status: fc.constantFrom('calibrated', 'approximate', 'predicted_only'),
    pose: fc.constantFrom('none', 'moon', 'device'),
    hint: fc.boolean(),
    uses: fc.nat({ max: USES.length - 1 }),
    offset: fc.boolean(),
    time: fc.boolean(),
    place: fc.boolean(),
    sameRecording: fc.boolean(),
    centre: fc.boolean(),
  });
  type Spec = {
    status: keyof typeof BASES; pose: string; hint: boolean; uses: number; offset: boolean; time: boolean; place: boolean;
    sameRecording: boolean; centre: boolean;
  };
  const valid = fixture.valid as Record<string, Record<string, unknown>>;

  function build(spec: Spec): Record<string, unknown> {
    const out = structuredClone(valid[BASES[spec.status]]!) as Record<string, unknown>;
    delete out.poseEstimate;
    delete out.pointingHint;
    if (spec.pose === 'moon') out.poseEstimate = structuredClone(valid.approximate_with_pose!.poseEstimate);
    if (spec.pose === 'device') out.poseEstimate = structuredClone(valid.approximate_with_device_attitude_pose!.poseEstimate);
    if (spec.hint) {
      const hint = structuredClone(valid.approximate_with_device_attitude_pose!.pointingHint) as Record<string, unknown>;
      hint.usedAs = USES[spec.uses];
      hint.sourceAssetSha256 = spec.sameRecording ? out.sourceSha256 : 'ab'.repeat(32);
      if (spec.offset) hint.verifiedCenterOffsetDeg = 3.5;
      if (!spec.centre) for (const key of ['centerAltDeg', 'centerAzDeg', 'uncertaintyDeg']) delete hint[key];
      out.pointingHint = hint;
    }
    if (!spec.time) delete out.observedAtUTC;
    if (!spec.place) delete out.observerLocation;
    return out;
  }

  function oracle(spec: Spec): boolean {
    const uses = USES[spec.uses]!;
    // A pose only on an approximate solution, and an approximate one always has one (REQ-SKY-5.4)
    const poseOk = (spec.pose === 'none') === (spec.status !== 'approximate');
    const momentOk = spec.status === 'calibrated' || (spec.time && spec.place);
    if (!spec.hint) return poseOk && momentOk && spec.pose !== 'device';
    const usesOk = uses.every((use, i) => i === 0 || ORDER.indexOf(uses[i - 1]!) < ORDER.indexOf(use));
    // The builder's hint takes its time and its place from the record, so it must say moment (pointing-3b); it gives
    // a centre exactly when it was a search hint or the pose, and an offset from it exactly when calibrated as well
    const centred = uses.includes('search_hint') || uses.includes('pose_estimate');
    return (
      poseOk && momentOk && usesOk && spec.sameRecording && uses.includes('pose_estimate') === (spec.pose === 'device') &&
      uses.includes('moment') && spec.centre === centred &&
      spec.offset === (spec.status === 'calibrated' && centred) && spec.time && spec.place
    );
  }

  it('accepts a generated solution exactly when the oracle does', () => {
    fc.assert(
      fc.property(specArb, (spec) => {
        expect(ObservationSolutionSchema.safeParse(build(spec as Spec)).success).toBe(oracle(spec as Spec));
      }),
      { numRuns: 400, seed: FC_SEED },
    );
  });
});
