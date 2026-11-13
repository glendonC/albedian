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
  EvidenceClassSchema,
  FINDING_CLASS_EVIDENCE,
  FINDING_EVIDENCE_RULES,
  FINDING_FIELDS,
  findingClassIssues,
  LocationHintSchema,
  MAX_HELD_OUT_MEDIAN_RESIDUAL_PX,
  MIN_HELD_OUT_INLIERS,
  ObservationSolutionSchema,
  PoseEstimateSchema,
  SHA256_HEX_PATTERN,
  SWEPT_FOV_TOLERANCE_DEG,
  ScientificEvidenceSchema,
  SkyPositionSchema,
  SolutionStatusSchema,
  SolveJobSchema,
  SourceDetectionSchema,
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
  keyCases: { levels: number; dropped: number; refusedWhenRenamed: number; leftOutWhenRenamed: number };
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
  SolverIdentity: solutionShape.solver.unwrap(),
  ObserverLocation: solutionShape.observerLocation.unwrap(),
  PoseEstimate: PoseEstimateSchema,
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
  SolverIdentity: solutionShape.solver.unwrap().shape,
  ObserverLocation: solutionShape.observerLocation.unwrap().shape,
  PoseEstimate: PoseEstimateSchema.innerType().shape,
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
    expect(fixture.rules['poseBeacon']!.values).toEqual(PoseEstimateSchema.innerType().shape.beacon.options);
    expect(fixture.rules['catalogProvider']!.values).toEqual(referenceShape.provider.options);
    expect(fixture.rules['catalogObjectType']!.values).toEqual(referenceShape.objectType.options);
    expect(fixture.rules['catalogDistanceMethod']!.values).toEqual(referenceShape.bailerJonesDistancePc.unwrap().shape.method.options);
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
    const counts = { levels: 0, dropped: 0, refusedWhenRenamed: 0, leftOutWhenRenamed: 0 };
    for (const level of levels) {
      const where = `${level.name} ${level.schema} ${dotted(level.path)}`;
      const record = at(level.sample, level.path) as Sample;
      const names = Object.entries(fixture.pythonNames[level.schema]!).filter(([wire]) => wire in record);
      // A key no schema names, whatever its value, and the Python name of every field set beside its wire name,
      // all at once and one at a time: each is dropped, and the record parses to what it was
      const twins = names.map(([wire, python]) => put([...level.path, python], record[wire]));
      const added = [...unknownValues.map((value) => [put([...level.path, 'unknownToTheContract'], value)]), twins, ...twins.map((twin) => [twin])];
      for (const steps of added) {
        const parsed = level.parse.safeParse(patched(level.sample, steps));
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
          [{ base: 'calibrated_at_the_gate', where: ['quality', 'medianResidualPx'], step: put(['quality', 'medianResidualPx'], Number.NaN) }],
          [{ base: 'calibrated_at_the_gate', where: ['wcs', 'rotationDeg'], step: put(['wcs', 'rotationDeg'], Number.POSITIVE_INFINITY) }],
          [{ base: 'calibrated_at_the_gate', where: ['status'], step: put(['status'], 'Calibrated') }],
          [{ base: 'calibrated_at_the_gate', where: ['revision'], step: put(['revision'], 1.5) }],
          [{ base: 'calibrated_at_the_gate', where: ['sourceSha256'], step: put(['sourceSha256'], '0'.repeat(64)) }],
          [{ base: 'approximate', where: ['observedAtUTC'], step: put(['observedAtUTC'], '2030-01-01T02:00:00+00:00') }],
          [{ base: 'approximate', where: ['findings', 0, 'sourceDetection', 'flux'], step: put(['findings', 0, 'sourceDetection', 'flux'], 0) }],
          [{ base: 'approximate', where: ['findings'], step: drop(['findings']) }],
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
  const CONSTANTS = ['MIN_HELD_OUT_INLIERS', 'MAX_HELD_OUT_MEDIAN_RESIDUAL_PX', 'SWEPT_FOV_TOLERANCE_DEG'];

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

  it('passes on the contracts as they are, and counts the three constants', () => {
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
