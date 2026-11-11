import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  ACCURACY_AUTHORIZATIONS,
  CAMERA_DEVICE_TYPES,
  CAMERA_POSITIONS,
  GRAVITY_LENGTH_MAX_G,
  GRAVITY_LENGTH_MIN_G,
  HEADING_ACCURACY_LIMIT_DEG,
  HEADING_ORIENTATIONS,
  MOTION_SAMPLE_OFFSET_LIMIT_SEC,
  NO_HEADING_REASONS,
  NO_PLACE_REASONS,
  POINTING_DIMENSION_MAX_PX,
  POINTING_HARDWARE_MODEL_PATTERN,
  POINTING_HINT_STATEMENT,
  POINTING_LABEL_MAX_UTF16,
  POINTING_RECORD_FORMAT,
  POINTING_RECORD_FORMAT_VERSION,
  POINTING_RULES,
  POINTING_SAMPLE_COUNT_MAX,
  POINTING_SYSTEM_VERSION_MAX_UTF16,
  POINTING_SYSTEM_VERSION_PATTERN,
  PointingRecordSchema,
  UNIT_QUATERNION_TOLERANCE,
} from '../index.js';

/**
 * The pointing record (REQ-IOS-7, P4) against the samples the Python mirror wrote (services/contracts/pointing.py,
 * tests/fixtures/pointing_contract.json, written by tests/integration/test_pointing_contract.py): both sides must
 * accept and refuse exactly the same records, in the same words where the rule is the contract's own. Every value
 * is synthetic: the synthetic moment and open-ocean place of the other fixtures and hand-chosen sensor readings.
 * The wire faults are kept as how to build each one's JSON text from a valid sample; this file builds the same text
 * and parses it with JSON.parse, as a record off the wire is read, and must give the verdict the mirror gave.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/pointing_contract.json');

type Json = Record<string, any>;

interface PropertyCase {
  heading: string;
  headingAccuracyDeg: number | null;
  reported: number | null;
  location: string;
  altitude: boolean;
  quaternionScale: number;
  gravityScale: number;
  sampleOffsetSec: number;
  windowEndOffsetSec: number;
  variant: number | null;
}

interface WireFault {
  name: string;
  base: string;
  path: string[];
  kind: 'rename' | 'add' | 'raw';
  to?: string;
  raw?: string;
  label?: string;
  verdict: 'accepted' | 'refused';
}

interface PointingFixture {
  format: string;
  formatVersion: number;
  limits: Record<string, number>;
  patterns: { hardwareModel: string; systemVersion: string };
  enums: Record<string, string[]>;
  rules: Record<string, string>;
  statement: string;
  valid: { name: string; verdict: 'accepted'; record: Json }[];
  invalid: { name: string; verdict: 'refused'; message: string | null; record: Json }[];
  edges: { name: string; sample: string; changes: { path: string[]; value: unknown }[]; verdict: 'accepted' | 'refused' }[];
  wireFaults: WireFault[];
  fieldVariants: { path: string[]; value: unknown; ok: boolean }[];
  propertyExamples: PropertyCase[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as PointingFixture;
const base = (): Json => structuredClone(fixture.valid.find((sample) => sample.name === 'measured_heading_and_place')!.record);
const QUATERNION = base().heading.attitude.quaternion as Record<'x' | 'y' | 'z' | 'w', number>;

function judge(record: unknown): string[] | null {
  const parsed = PointingRecordSchema.safeParse(record);
  return parsed.success ? null : parsed.error.issues.map((issue) => issue.message);
}

/** The smallest double above a positive finite `x`. */
function nextUp(x: number): number {
  const buffer = new Float64Array([x]);
  const bits = new BigInt64Array(buffer.buffer);
  bits[0] = bits[0]! + 1n;
  return buffer[0]!;
}

/** The largest double below a positive finite `x`. */
function nextDown(x: number): number {
  const buffer = new Float64Array([x]);
  const bits = new BigInt64Array(buffer.buffer);
  bits[0] = bits[0]! - 1n;
  return buffer[0]!;
}

const WIRE_MARK = '@@wire fault@@';

/** The JSON text a wire fault describes, built from its valid sample as the Python side builds it. */
function wireText(fault: WireFault): string {
  const record = structuredClone(fixture.valid.find((sample) => sample.name === fault.base)!.record);
  const parents = fault.path.slice(0, -1);
  const last = fault.path.at(-1);
  let target: Json = record;
  for (const key of parents) target = target[key];
  if (fault.kind === 'add') {
    (last === undefined ? record : target[last]).unknownKey = 1;
    return JSON.stringify(record);
  }
  if (fault.kind === 'rename') {
    target[fault.to!] = target[last!];
    delete target[last!];
    return JSON.stringify(record);
  }
  target[last!] = WIRE_MARK;
  const text = JSON.stringify(record);
  expect(text.split(JSON.stringify(WIRE_MARK)).length).toBe(2);
  return text.replace(JSON.stringify(WIRE_MARK), () => fault.raw!);
}

/** The record a P4 case describes, built from the base record as the Python property builds it, and whether its
 * field variant applied (only where its field is in the record). */
function buildPropertyRecord(spec: PropertyCase): [Json, boolean] {
  const record = base();
  if (spec.location !== 'allowed') record.location = { state: 'none', reason: spec.location };
  else if (!spec.altitude) delete record.location.altitude;
  if (spec.heading === 'measured') {
    record.heading.headingAccuracyDeg = spec.headingAccuracyDeg;
    record.heading.attitude.quaternion = Object.fromEntries(
      Object.entries(QUATERNION).map(([key, value]) => [key, value * spec.quaternionScale])
    );
  } else {
    record.heading = { state: 'none', reason: spec.heading, ...(spec.reported === null ? {} : { reportedHeadingAccuracyDeg: spec.reported }) };
  }
  record.motion.gravityDeviceG = Object.fromEntries(
    Object.entries(base().motion.gravityDeviceG as Record<string, number>).map(([key, value]) => [key, value * spec.gravityScale])
  );
  record.motion.sampleOffsetFromPhotoSec = spec.sampleOffsetSec;
  record.motion.window.endOffsetSec = spec.windowEndOffsetSec;
  let applied = false;
  if (spec.variant !== null) {
    const variant = fixture.fieldVariants[spec.variant]!;
    let target: unknown = record;
    for (const key of variant.path.slice(0, -1)) {
      target = target !== null && typeof target === 'object' && !Array.isArray(target) ? (target as Json)[key] : undefined;
    }
    const last = variant.path.at(-1)!;
    if (target !== null && typeof target === 'object' && !Array.isArray(target) && Object.hasOwn(target, last)) {
      (target as Json)[last] = structuredClone(variant.value);
      applied = true;
    }
  }
  return [record, applied];
}

/** Whether the record a P4 case describes is a valid pointing record, from the rules and the variant's own verdict
 * (as the Python oracle). */
function propertyOracle(spec: PropertyCase, applied: boolean): boolean {
  const placed = spec.location === 'allowed';
  let headingOk: boolean;
  if (spec.heading === 'measured') {
    headingOk = placed && spec.headingAccuracyDeg! >= 0 && spec.headingAccuracyDeg! <= HEADING_ACCURACY_LIMIT_DEG && spec.quaternionScale === 1;
  } else if (spec.heading === 'accuracy_outside_limit') {
    headingOk = spec.reported !== null && spec.reported > HEADING_ACCURACY_LIMIT_DEG;
  } else {
    headingOk = spec.reported === null && (spec.heading !== 'no_place' || !placed);
  }
  const motionOk =
    GRAVITY_LENGTH_MIN_G <= Math.abs(spec.gravityScale) &&
    Math.abs(spec.gravityScale) <= GRAVITY_LENGTH_MAX_G &&
    Math.abs(spec.sampleOffsetSec) <= MOTION_SAMPLE_OFFSET_LIMIT_SEC &&
    base().motion.window.startOffsetSec <= spec.windowEndOffsetSec;
  const variantOk = !applied || fixture.fieldVariants[spec.variant!]!.ok;
  return headingOk && motionOk && variantOk;
}

describe('@albedian/contracts: the pointing record (REQ-IOS-7)', () => {
  it('shares its format, limits, enums, rule words and evidence statement with the Pydantic mirror', () => {
    expect(fixture.format).toBe(POINTING_RECORD_FORMAT);
    expect(fixture.formatVersion).toBe(POINTING_RECORD_FORMAT_VERSION);
    expect(fixture.limits).toEqual({
      headingAccuracyLimitDeg: HEADING_ACCURACY_LIMIT_DEG,
      motionSampleOffsetLimitSec: MOTION_SAMPLE_OFFSET_LIMIT_SEC,
      unitQuaternionTolerance: UNIT_QUATERNION_TOLERANCE,
      gravityLengthMinG: GRAVITY_LENGTH_MIN_G,
      gravityLengthMaxG: GRAVITY_LENGTH_MAX_G,
      dimensionMaxPx: POINTING_DIMENSION_MAX_PX,
      sampleCountMax: POINTING_SAMPLE_COUNT_MAX,
      labelMaxUtf16: POINTING_LABEL_MAX_UTF16,
      systemVersionMaxUtf16: POINTING_SYSTEM_VERSION_MAX_UTF16,
    });
    expect(fixture.patterns).toEqual({ hardwareModel: POINTING_HARDWARE_MODEL_PATTERN, systemVersion: POINTING_SYSTEM_VERSION_PATTERN });
    expect(fixture.enums).toEqual({
      cameraPositions: [...CAMERA_POSITIONS],
      cameraDeviceTypes: [...CAMERA_DEVICE_TYPES],
      headingOrientations: [...HEADING_ORIENTATIONS],
      noHeadingReasons: [...NO_HEADING_REASONS],
      noPlaceReasons: [...NO_PLACE_REASONS],
      accuracyAuthorizations: [...ACCURACY_AUTHORIZATIONS],
    });
    expect(fixture.rules).toEqual(POINTING_RULES);
    expect(fixture.statement).toBe(POINTING_HINT_STATEMENT);
  });

  it('parses every record the Python side accepts, unchanged', () => {
    expect(fixture.valid.length).toBeGreaterThanOrEqual(10);
    for (const { name, verdict, record } of fixture.valid) {
      expect(verdict).toBe('accepted');
      const parsed = PointingRecordSchema.safeParse(record);
      expect(parsed.success, `${name}: ${JSON.stringify(parsed.error?.issues)}`).toBe(true);
      expect(parsed.data, name).toEqual(record);
    }
  });

  it('refuses every record the Python side refuses, in the contract words where the rule is its own', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(40);
    for (const { name, verdict, message, record } of fixture.invalid) {
      expect(verdict).toBe('refused');
      const refusal = judge(record);
      expect(refusal, `should refuse: ${name}`).not.toBeNull();
      if (message !== null) expect(refusal, name).toContain(message);
    }
    // Every rule of the contract's own has a fault in the fixture
    const faulted = new Set(fixture.invalid.map((sample) => sample.message));
    for (const message of Object.values(POINTING_RULES)) expect(faulted.has(message), message).toBe(true);
  });

  it('keeps a heading at its accuracy limit and leaves it out just past it', () => {
    const limit = HEADING_ACCURACY_LIMIT_DEG;
    const at = (accuracy: number) => {
      const record = base();
      record.heading.headingAccuracyDeg = accuracy;
      return judge(record);
    };
    expect(at(limit)).toBeNull();
    expect(at(0)).toBeNull();
    expect(at(nextUp(limit))).toContain(POINTING_RULES.headingInsideLimit);
    const leftOut = (reported: number) => {
      const record = base();
      record.heading = { state: 'none', reason: 'accuracy_outside_limit', reportedHeadingAccuracyDeg: reported };
      return judge(record);
    };
    expect(leftOut(nextUp(limit))).toBeNull();
    expect(leftOut(limit)).toContain(POINTING_RULES.outsideLimitCarriesAccuracy);
  });

  it('takes the motion sample only within its offset limit of the photo', () => {
    const limit = MOTION_SAMPLE_OFFSET_LIMIT_SEC;
    for (const [offset, accepted] of [[limit, true], [-limit, true], [0, true], [nextUp(limit), false], [-nextUp(limit), false]] as const) {
      const record = base();
      record.motion.sampleOffsetFromPhotoSec = offset;
      expect(judge(record) === null, String(offset)).toBe(accepted);
    }
  });

  it('holds the attitude to a unit quaternion within its tolerance, either sign', () => {
    const tolerance = UNIT_QUATERNION_TOLERANCE;
    // Along w alone, the norm is w: inside and outside by a tenth of the tolerance and ten times it. The pair at 0.9 and
    // 1.1 tells the norm from its square, which moves twice as far
    const factors = [0.1, 0.9, 1.1, 10];
    const cases = factors.flatMap((f) => [[1 + tolerance * f, f <= 1], [1 - tolerance * f, f <= 1]] as const);
    for (const [w, accepted] of cases) {
      const record = base();
      record.heading.attitude.quaternion = { x: 0, y: 0, z: 0, w };
      expect(judge(record) === null, String(w)).toBe(accepted);
    }
    const negated = base();
    negated.heading.attitude.quaternion = Object.fromEntries(Object.entries(QUATERNION).map(([key, value]) => [key, -value]));
    expect(judge(negated)).toBeNull();
  });

  it('holds gravity to its band around one g, the edges exact along one axis', () => {
    const at = (gravity: Record<'x' | 'y' | 'z', number>) => {
      const record = base();
      record.motion.gravityDeviceG = gravity;
      return judge(record);
    };
    const low = GRAVITY_LENGTH_MIN_G;
    const high = GRAVITY_LENGTH_MAX_G;
    for (const z of [low, high, 1, -low, -high]) expect(at({ x: 0, y: 0, z }), String(z)).toBeNull();
    for (const z of [nextDown(low), nextUp(high), 9.81, -9.81, 100, 0.01, 1e-300, 0, 2, 1e200]) {
      expect(at({ x: 0, y: 0, z }), String(z)).toContain(POINTING_RULES.gravityNearOneG);
    }
    expect(at({ x: 0.5, y: -0.5, z: -0.7 })).toBeNull();
    expect(at({ x: 5, y: -5, z: -7 })).not.toBeNull();
  });

  it('takes the two device labels only as exact patterns, which carry no name', () => {
    const at = (key: 'hardwareModel' | 'systemVersion', value: string) => {
      const record = base();
      record.device[key] = value;
      return judge(record);
    };
    expect(POINTING_LABEL_MAX_UTF16).toBe(32);
    // The system version's maximum is what its pattern allows: the longest version it takes is exactly that long
    expect(['9999.9999.9999'.length, POINTING_SYSTEM_VERSION_MAX_UTF16]).toEqual([14, 14]);
    for (const model of ['iPhone18,1', 'iPad16,10', 'Synthetic1,1', 'a'.repeat(29) + '1,1']) expect(at('hardwareModel', model), model).toBeNull();
    for (const model of ['a'.repeat(30) + '1,1', 'My phone1,1', "Synthetic's1,1", 'iPhone', '\u00e9a1,1', 'iPhone18,1\n', '\ud800', '\u{1f600}1,1']) {
      expect(at('hardwareModel', model), JSON.stringify(model)).not.toBeNull();
    }
    for (const version of ['27', '27.1', '27.1.2', '0.0.0', '9999.9999.9999']) expect(at('systemVersion', version), version).toBeNull();
    for (const version of ['27.1.2.3', '27.', '.27', '27..1', 'v27', '27.1 ', '12345', '27.1\n', '\u0662\u0667']) {
      expect(at('systemVersion', version), JSON.stringify(version)).not.toBeNull();
    }
  });

  it('bounds every whole number inside the safe integers', () => {
    expect(POINTING_DIMENSION_MAX_PX).toBeLessThanOrEqual(Number.MAX_SAFE_INTEGER);
    expect(POINTING_SAMPLE_COUNT_MAX).toBeLessThanOrEqual(Number.MAX_SAFE_INTEGER);
    const cases: [string[], number][] = [
      [['camera', 'photoDimensionsPx', 'width'], POINTING_DIMENSION_MAX_PX],
      [['camera', 'activeFormatDimensionsPx', 'height'], POINTING_DIMENSION_MAX_PX],
      [['motion', 'window', 'sampleCount'], POINTING_SAMPLE_COUNT_MAX],
    ];
    for (const [keys, bound] of cases) {
      for (const [value, accepted] of [[bound, true], [bound + 1, false], [2 ** 53 + 1, false], [-0, false], [0, false]] as const) {
        const record = base();
        let target = record;
        for (const key of keys.slice(0, -1)) target = target[key];
        target[keys.at(-1)!] = value;
        expect(judge(record) === null, `${keys.join('.')} = ${value}`).toBe(accepted);
      }
    }
  });

  it('gives every edge of the tables of bounds, patterns and lengths the verdict its table gives', () => {
    // The tables live in the Python generator: every bounded number at and a step past each end, every pattern and
    // length at its shortest and longest and one beyond
    expect(fixture.edges.length).toBeGreaterThanOrEqual(150);
    const wrong = fixture.edges.filter((edge) => {
      const record = structuredClone(fixture.valid.find((sample) => sample.name === edge.sample)!.record);
      for (const change of edge.changes) {
        let target: Json = record;
        for (const key of change.path.slice(0, -1)) target = target[key];
        target[change.path.at(-1)!] = structuredClone(change.value);
      }
      return (judge(record) === null ? 'accepted' : 'refused') !== edge.verdict;
    });
    expect(wrong.map((edge) => `${edge.name}: ${edge.verdict} by the table`)).toEqual([]);
  });

  it('gives every generated wire fault the verdict the Python mirror gave it', () => {
    expect(fixture.wireFaults.length).toBeGreaterThanOrEqual(300);
    expect(fixture.wireFaults.filter((fault) => fault.kind === 'rename').length).toBeGreaterThanOrEqual(30);
    const disagreements = fixture.wireFaults.filter((fault) => {
      const parsed = PointingRecordSchema.safeParse(JSON.parse(wireText(fault)));
      return (parsed.success ? 'accepted' : 'refused') !== fault.verdict;
    });
    expect(disagreements.map((fault) => `${fault.name}: ${fault.verdict} by the mirror`)).toEqual([]);
  });

  it('refuses an infinite or not-a-number reading and a true for a number', () => {
    const paths = [
      ['motion', 'rotationRateDeviceRadPerSec', 'x'],
      ['location', 'altitude', 'aboveEllipsoidM'],
      ['motion', 'window', 'startOffsetSec'],
      ['heading', 'eventOffsetFromPhotoSec'],
      ['location', 'fixOffsetFromPhotoSec'],
    ];
    for (const keys of paths) {
      for (const bad of [Infinity, -Infinity, NaN]) {
        const record = base();
        let target = record;
        for (const key of keys.slice(0, -1)) target = target[key];
        target[keys.at(-1)!] = bad;
        expect(judge(record), `${keys.join('.')} = ${bad}`).not.toBeNull();
      }
    }
    const flagged = base();
    flagged.camera.photoDimensionsPx.width = true;
    expect(judge(flagged)).not.toBeNull();
  });

  it('P4: generated records, with and without a heading and a place, are accepted exactly when the rules allow', () => {
    const caseArb: fc.Arbitrary<PropertyCase> = fc.record({
      // Half the draws measured and placed, so accepted measured headings come up often
      heading: fc.oneof(fc.constant('measured'), fc.constantFrom('measured', ...NO_HEADING_REASONS)),
      // Each rule field half the time inside what the rules take, so records accepted with a field variant come up
      headingAccuracyDeg: fc.oneof(fc.double({ min: 0, max: 25, noNaN: true }), fc.double({ min: -5, max: 40, noNaN: true })),
      reported: fc.oneof(fc.constant(null), fc.double({ min: 0, max: 50, noNaN: true })),
      location: fc.oneof(fc.constant('allowed'), fc.constantFrom('allowed', ...NO_PLACE_REASONS)),
      altitude: fc.boolean(),
      // One, or well away from one so the oracle needs no arithmetic: the tolerance is 1e-6
      quaternionScale: fc.oneof(
        fc.constant(1),
        fc.constant(1),
        fc.double({ min: 0.5, max: 0.9999, noNaN: true }),
        fc.double({ min: 1.0001, max: 1.5, noNaN: true })
      ),
      // One, inside the band or outside it, kept 0.05 g from its edges so the oracle needs no arithmetic either
      gravityScale: fc.oneof(
        fc.constant(1),
        fc.double({ min: 0.95, max: 1.05, noNaN: true }),
        fc.double({ min: -1.05, max: -0.95, noNaN: true }),
        fc.double({ min: 0, max: 0.85, noNaN: true }),
        fc.double({ min: 1.15, max: 12, noNaN: true })
      ),
      sampleOffsetSec: fc.oneof(fc.double({ min: -0.05, max: 0.05, noNaN: true }), fc.double({ min: -0.07, max: 0.07, noNaN: true })),
      windowEndOffsetSec: fc.double({ min: -1, max: 12, noNaN: true }),
      // Half the draws keep every other field as it is; the rest set one field to a value from the shared table
      variant: fc.oneof(fc.constant(null), fc.constantFrom(...fixture.fieldVariants.map((_, index) => index))),
    });
    fc.assert(
      fc.property(caseArb, (spec) => {
        const [record, applied] = buildPropertyRecord(spec);
        const parsed = PointingRecordSchema.safeParse(record);
        expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(propertyOracle(spec, applied));
        if (parsed.success && !applied) {
          expect(parsed.data).toEqual(record);
          expect('altitude' in parsed.data.location).toBe(spec.location === 'allowed' && spec.altitude);
        }
      }),
      { seed: SEED, numRuns: 300, examples: fixture.propertyExamples.map((example) => [example] as [PropertyCase]) }
    );
    // The shared examples reach every heading and place state and both verdicts
    expect(new Set(fixture.propertyExamples.map((example) => example.heading))).toEqual(new Set(['measured', ...NO_HEADING_REASONS]));
    expect(new Set(fixture.propertyExamples.map((example) => example.location))).toEqual(new Set(['allowed', ...NO_PLACE_REASONS]));
    expect(new Set(fixture.propertyExamples.map((example) => propertyOracle(example, buildPropertyRecord(example)[1])))).toEqual(
      new Set([true, false])
    );
  });
});
