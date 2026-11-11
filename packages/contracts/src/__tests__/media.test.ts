import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { z } from 'zod';
import * as contracts from '../index.js';
import {
  ASSUMPTION_REASONS,
  ATTEMPT_STATUSES,
  AudioStreamDescriptorSchema,
  BASES,
  CHROMA_SUBSAMPLINGS,
  COLOUR_BIT_DEPTHS,
  COLOUR_MATRICES,
  COLOUR_MODELS,
  COLOUR_PRIMARIES,
  COLOUR_TRANSFERS,
  ColourDescriptionSchema,
  DISCLOSURES,
  ExportArtifactSchema,
  ExportManifestSchema,
  FAILURE_CAPABILITIES,
  FAILURE_FORMATS,
  FAILURE_SENTENCES,
  GRAPH_STEP_KINDS,
  GraphParameterSchema,
  GraphStepSchema,
  INGEST_PATTERNS,
  IntegrityGateResultSchema,
  JOB_ID_PATTERN,
  MEDIA_ENUMS,
  MEDIA_ID_PATTERNS,
  MEDIA_PATTERNS,
  MEDIA_REFINEMENT_MESSAGES,
  MEDIA_VOCABULARY,
  MediaVariantSchema,
  NOTES,
  NoiseTableSchema,
  ProcessingAttemptSchema,
  ProcessingGraphSchema,
  REJECTION_REASONS,
  RESTORATION_CRITERIA,
  RestorationMeasurementsSchema,
  RestoreRequestSchema,
  STEP_PARAMETERS,
  TEMPLATE_SLOTS,
  VARIANT_KINDS,
  variantUri,
} from '../index.js';
import { UTC_INSTANT_PATTERN } from '../wire.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

/**
 * The restoration and export contracts (spec restoration-for-every-upload, REQ-RST-10; P-RST-20) against the
 * samples the Python mirror wrote (`services/contracts/media.py`, `tests/fixtures/media_contract.json`): both sides
 * accept and refuse exactly the same records, each invalid one for the refinement it names, and read the raw JSON
 * samples (numbers only text can carry) with their own JSON reader. The properties below run the same generators and
 * the same oracle as `tests/integration/test_media_contract.py`; the closed lists are expanded here, slot by slot,
 * without the template walker the contract uses.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/media_contract.json');

const SCHEMAS = {
  RestoreRequest: RestoreRequestSchema,
  GraphParameter: GraphParameterSchema,
  GraphStep: GraphStepSchema,
  ProcessingGraph: ProcessingGraphSchema,
  ColourDescription: ColourDescriptionSchema,
  NoiseTable: NoiseTableSchema,
  IntegrityGateResult: IntegrityGateResultSchema,
  RestorationMeasurements: RestorationMeasurementsSchema,
  ProcessingAttempt: ProcessingAttemptSchema,
  MediaVariant: MediaVariantSchema,
  ExportManifest: ExportManifestSchema,
  ExportArtifact: ExportArtifactSchema,
} as const;
type SchemaName = keyof typeof SCHEMAS;
type Rec = Record<string, unknown>;
type Path = (string | number)[];

interface MediaFixture {
  patterns: Record<string, string>;
  idPatterns: Record<string, string>;
  jobIdPattern: string;
  enums: Record<string, unknown[]>;
  disclosures: Record<string, string>;
  vocabulary: unknown;
  messages: string[];
  pythonNames: Record<string, string>;
  privacyProbes: string[];
  valid: Record<SchemaName, Rec[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string; message: string | null }[];
  raw: { schema: SchemaName; json: string; accepted: boolean; reason: string; message: string | null }[];
  addedKeys: { keys: string[]; value: string; records: number };
}

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as MediaFixture;
const VALID = fixture.valid;
const clone = <T>(value: T): T => structuredClone(value);
const accepts = (schema: z.ZodTypeAny, record: unknown) => schema.safeParse(record).success;
const plain = (value: unknown) => JSON.parse(JSON.stringify(value)) as unknown;

/** Every text a template stands for, slot by slot: the oracle the template walker is checked against. */
function expand(template: string): string[] {
  const parts = template.split(/[{}]/);
  let texts = [''];
  parts.forEach((part, index) => {
    let choices = [part];
    if (index % 2 === 1) {
      const slot = TEMPLATE_SLOTS[part as keyof typeof TEMPLATE_SLOTS];
      choices =
        slot.type === 'text' ? [...slot.values] : Array.from({ length: slot.max - slot.min + 1 }, (_, i) => String(slot.min + i));
    }
    texts = texts.flatMap((text) => choices.map((choice) => text + choice));
  });
  return texts;
}
const FAILURES = [...new Set(FAILURE_SENTENCES.flatMap(expand))].sort();
const DEFAULT_BASES = [...new Set(BASES.default.flatMap(expand))].sort();

function put(record: unknown, at: Path, value: unknown): Rec {
  const out = clone(record) as Rec;
  let target = out as Record<string | number, unknown>;
  for (const key of at.slice(0, -1)) target = target[key] as Record<string | number, unknown>;
  target[at[at.length - 1]!] = value;
  return out;
}
function get(record: unknown, at: Path): unknown {
  return at.reduce<unknown>((node, key) => (node as Record<string | number, unknown>)[key], record);
}
function keyPaths(node: unknown, at: Path = []): Path[] {
  if (Array.isArray(node)) return node.flatMap((value, i) => keyPaths(value, [...at, i]));
  if (node !== null && typeof node === 'object') {
    return Object.entries(node).flatMap(([key, value]) => [[...at, key], ...keyPaths(value, [...at, key])]);
  }
  return [];
}
function objectPaths(node: unknown, at: Path = []): Path[] {
  if (Array.isArray(node)) return node.flatMap((value, i) => objectPaths(value, [...at, i]));
  if (node !== null && typeof node === 'object') {
    return [at, ...Object.entries(node).flatMap(([key, value]) => objectPaths(value, [...at, key]))];
  }
  return [];
}
/** The record with `key` added at `at` as an own property, which `__proto__` is only when defined this way. */
function withKey(record: unknown, at: Path, key: string, value: unknown): Rec {
  const out = clone(record) as Rec;
  Object.defineProperty(get(out, at) as object, key, { value, enumerable: true, configurable: true, writable: true });
  return out;
}
function stringPaths(node: unknown, at: Path = []): Path[] {
  if (typeof node === 'string') return [at];
  if (Array.isArray(node)) return node.flatMap((value, i) => stringPaths(value, [...at, i]));
  if (node !== null && typeof node === 'object') return Object.entries(node).flatMap(([key, value]) => stringPaths(value, [...at, key]));
  return [];
}
function renamed(record: unknown, at: Path, name: string): Rec {
  const out = clone(record) as Rec;
  let target = out as Record<string | number, unknown>;
  for (const key of at.slice(0, -1)) target = target[key] as Record<string | number, unknown>;
  const last = at[at.length - 1] as string;
  target[name] = target[last];
  delete target[last];
  return out;
}

describe('@albedian/contracts: restoration and export (REQ-RST-10, P-RST-20)', () => {
  it('shares its patterns, enums, lists, disclosures and refinement messages with the Pydantic mirror', () => {
    expect(fixture.patterns).toEqual(MEDIA_PATTERNS);
    expect(fixture.idPatterns).toEqual(MEDIA_ID_PATTERNS);
    expect(fixture.jobIdPattern).toBe(JOB_ID_PATTERN);
    expect(fixture.enums).toEqual(plain(MEDIA_ENUMS));
    expect(fixture.disclosures).toEqual(DISCLOSURES);
    expect(fixture.vocabulary).toEqual(plain(MEDIA_VOCABULARY));
    expect([...fixture.messages].sort()).toEqual([...MEDIA_REFINEMENT_MESSAGES].sort());
    // One UTC form, one hash form and one job id form for every record of the app: the wire module's and media's
    expect(MEDIA_PATTERNS.utcInstant).toBe(INGEST_PATTERNS.utcInstant);
    expect(MEDIA_PATTERNS.utcInstant).toBe(UTC_INSTANT_PATTERN);
    expect(MEDIA_PATTERNS.sha256).toBe(INGEST_PATTERNS.sha256);
    expect(INGEST_PATTERNS.jobId).toBe(JOB_ID_PATTERN);
    const names = STEP_PARAMETERS.map((row) => row.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    expect(Object.keys(VALID).sort()).toEqual(Object.keys(SCHEMAS).sort());
    for (const [name, records] of Object.entries(VALID) as [SchemaName, Rec[]][]) {
      expect(records.length, name).toBeGreaterThan(0);
      for (const record of records) {
        const parsed = SCHEMAS[name].safeParse(record);
        expect(parsed.success, `${name}: ${JSON.stringify(parsed.error?.issues)}`).toBe(true);
        expect(parsed.data).toEqual(record);
      }
    }
  });

  it('refuses every record the Python side refuses, for the reason it names', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(200);
    for (const { schema, sample, reason, message } of fixture.invalid) {
      const parsed = SCHEMAS[schema].safeParse(sample);
      expect(parsed.success, `${schema} should refuse: ${reason}`).toBe(false);
      if (message !== null) {
        const found = parsed.error?.issues.map((issue) => issue.message) ?? [];
        expect(found, reason).toContain(message);
      }
    }
  });

  it('has a refused sample for every refinement it writes', () => {
    const used = new Set(fixture.invalid.map((c) => c.message).filter((m): m is string => m !== null));
    expect(MEDIA_REFINEMENT_MESSAGES.filter((m) => !used.has(m))).toEqual([]);
  });

  it('no longer exports the DAG validator, the cache keys, the job or the shapes nothing produced', () => {
    // REQ-RST-10.3: nothing branches, caches or scores, so the contract describes none of it; the job is the runner's
    const exported = Object.keys(contracts);
    for (const name of [
      'validateProcessingGraphDAG',
      'computeNodeCacheKeys',
      'ProcessingNodeSchema',
      'ProcessingOperationKindSchema',
      'FidelityPolicySchema',
      'AttemptEvaluationMetricsSchema',
      'RestoreJobSchema',
      'RestoreStageProgressSchema',
      'RESTORE_JOB_STAGES',
      'RESTORE_JOB_STATES',
    ]) {
      expect(exported, name).not.toContain(name);
    }
    const removed: Record<string, [z.ZodTypeAny, string[]]> = {
      ProcessingAttempt: [ProcessingAttemptSchema, ['parentAttemptId', 'label', 'cacheKey', 'gpuMs', 'outputVariantId', 'metrics']],
      MediaVariant: [MediaVariantSchema, ['label', 'summaryNote', 'frameTimestampsMs', 'audioStreamCopiedFromSource', 'audioSyncOffsetMs']],
      ProcessingGraph: [ProcessingGraphSchema, ['parentGraphId', 'profileName', 'fidelityPolicy']],
      ExportArtifact: [
        ExportArtifactSchema,
        ['sessionId', 'outputUri', 'containerFormat', 'videoCodec', 'audioPreserved', 'audioCodec', 'audioSyncOffsetMs',
          'processingGraphId', 'catalogCitations', 'epistemicSummary'],
      ],
    };
    for (const [name, [schema, fields]] of Object.entries(removed)) {
      const base = VALID[name as SchemaName][0]!;
      for (const field of fields) expect(accepts(schema, { ...base, [field]: true }), `${name}.${field}`).toBe(false);
    }
  });

  it('refuses a Python field name on the wire at every level, as the Pydantic mirror does', () => {
    const names = fixture.pythonNames;
    expect(names.sourceSha256).toBe('source_sha256');
    let checked = 0;
    for (const [schema, records] of Object.entries(VALID) as [SchemaName, Rec[]][]) {
      for (const record of records) {
        for (const at of keyPaths(record)) {
          const name = names[at[at.length - 1] as string];
          if (name === undefined) continue;
          checked += 1;
          expect(accepts(SCHEMAS[schema], renamed(record, at, name)), `${schema}: ${at.join('.')}`).toBe(false);
        }
      }
    }
    expect(checked).toBeGreaterThan(700);
  });

  it('refuses an unknown key or a Python name beside its wire key at every level, as the Pydantic mirror does', () => {
    // Renaming a key leaves a required one missing; adding one is what a loose shape takes and drops (verdict
    // restore-1-v2, item 1). Every object of every valid sample, the embedded audio streams and orientation included
    const names = fixture.pythonNames;
    const added = fixture.addedKeys;
    let checked = 0;
    for (const [schema, records] of Object.entries(VALID) as [SchemaName, Rec[]][]) {
      for (const record of records) {
        for (const at of objectPaths(record)) {
          const target = get(record, at) as Record<string, unknown>;
          const extra: [string, unknown][] = added.keys.map((key) => [key, added.value]);
          for (const [key, value] of Object.entries(target)) {
            if (Object.hasOwn(names, key)) extra.push([names[key]!, value]);
          }
          for (const [key, value] of extra) {
            checked += 1;
            expect(accepts(SCHEMAS[schema], withKey(record, at, key, value)), `${schema}: ${key} at ${at.join('.')}`).toBe(false);
          }
        }
      }
    }
    expect(checked).toBe(added.records);
    expect(checked).toBeGreaterThan(2000);
  });

  it('reads every number form only JSON text can carry as the Pydantic mirror does', () => {
    expect(fixture.raw.length).toBeGreaterThanOrEqual(40);
    for (const { schema, json, accepted, reason, message } of fixture.raw) {
      const parsed = SCHEMAS[schema].safeParse(JSON.parse(json));
      expect(parsed.success, reason).toBe(accepted);
      if (message !== null) {
        expect(parsed.error?.issues.map((issue) => issue.message) ?? [], reason).toContain(message);
      }
    }
    expect(fixture.raw.filter((c) => c.message !== null).length).toBeGreaterThanOrEqual(18);
  });

  it('refuses an infinite number in every number field', () => {
    const gate = VALID.IntegrityGateResult[0]!;
    for (const key of ['variantThresholdSnr', 'matchRadiusPx', 'tilePx', 'variantPointCount']) {
      expect(accepts(IntegrityGateResultSchema, { ...gate, [key]: Infinity }), key).toBe(false);
    }
    expect(accepts(RestoreRequestSchema, { kind: 'restoration', exposureEv: Number.NaN })).toBe(false);
  });

  it('drops an unknown key of the embedded audio shape, as the Pydantic mirror does', () => {
    const stream = { index: 1, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 1, sha256: 'a'.repeat(8) };
    expect(AudioStreamDescriptorSchema.parse({ ...stream, bitRate: 96000 })).toEqual(stream);
  });

  it('counts the embedded audio shape hash length in UTF-16 units, as the Pydantic mirror does', () => {
    const stream = { index: 1, codec: 'aac', sampleRateHz: 48000, channels: 2, startPtsMs: 0, durationMs: 1 };
    const astral = '\u{1f600}';
    expect(accepts(AudioStreamDescriptorSchema, { ...stream, sha256: astral.repeat(4) })).toBe(true);
    expect(accepts(AudioStreamDescriptorSchema, { ...stream, sha256: `${astral.repeat(3)}a` })).toBe(false);
    expect(accepts(AudioStreamDescriptorSchema, { ...stream, sha256: 'a'.repeat(7) })).toBe(false);
    expect(accepts(AudioStreamDescriptorSchema, { ...stream, sha256: 'a'.repeat(8) })).toBe(true);
  });

  it('refuses a lone surrogate in every string of every record', () => {
    let checked = 0;
    for (const [schema, records] of Object.entries(VALID) as [SchemaName, Rec[]][]) {
      for (const record of records) {
        for (const at of stringPaths(record)) {
          const value = get(record, at) as string;
          for (const placed of [`${value}\uD800`, `\uDC00${value}`]) {
            checked += 1;
            expect(accepts(SCHEMAS[schema], put(record, at, placed)), `${schema}: ${at.join('.')}`).toBe(false);
          }
        }
      }
    }
    expect(checked).toBeGreaterThan(1000);
  });

  // Where a text goes, as a schema, a valid record and the path of the text in it
  const slots: Record<string, [SchemaName, () => Rec, Path]> = {
    'measured basis': ['GraphParameter', () => VALID.GraphParameter[0]!, ['basis']],
    'default basis': ['GraphParameter', () => VALID.GraphParameter[6]!, ['basis']],
    'requested basis': ['GraphParameter', () => VALID.GraphParameter[2]!, ['basis']],
    'text value': ['GraphParameter', () => VALID.GraphParameter[0]!, ['value']],
    note: ['RestorationMeasurements', () => VALID.RestorationMeasurements[0]!, ['notes', 0]],
    'failure reason': ['ProcessingAttempt', () => VALID.ProcessingAttempt[9]!, ['failureReason']],
    'assumption reason': ['ColourDescription', () => VALID.ColourDescription[0]!, ['assumptions', 0, 'reason']],
    'parameter name': ['GraphParameter', () => VALID.GraphParameter[0]!, ['name']],
    'audio codec': ['MediaVariant', () => VALID.MediaVariant[2]!, ['audioStreams', 0, 'codec']],
    'audio hash': ['MediaVariant', () => VALID.MediaVariant[2]!, ['audioStreams', 0, 'sha256']],
    'asset id': ['MediaVariant', () => VALID.MediaVariant[0]!, ['sourceAssetId']],
    'solution id': ['ExportManifest', () => VALID.ExportManifest[0]!, ['observationSolutionId']],
  };
  // What each slot accepts, written out in full
  const allowed: Record<string, Set<string>> = {
    'measured basis': new Set(BASES.measured),
    'default basis': new Set(DEFAULT_BASES),
    'requested basis': new Set(BASES.requested),
    'text value': new Set(COLOUR_MATRICES),
    note: new Set(NOTES),
    'failure reason': new Set(FAILURES),
    'assumption reason': new Set(ASSUMPTION_REASONS.filter((r) => r.field === 'chromaSiting').map((r) => r.reason)),
    'parameter name': new Set(['matrix']),
  };

  it('refuses every string the audit passed in every slot', () => {
    expect(fixture.privacyProbes.length).toBeGreaterThanOrEqual(70);
    for (const [slot, [schema, base, at]] of Object.entries(slots)) {
      for (const probe of fixture.privacyProbes) expect(accepts(SCHEMAS[schema], put(base(), at, probe)), `${slot}: ${probe}`).toBe(false);
    }
  });

  it('accepts every entry of every list in its slot', () => {
    for (const [slot, texts] of Object.entries(allowed)) {
      const [schema, base, at] = slots[slot]!;
      for (const text of texts) expect(accepts(SCHEMAS[schema], put(base(), at, text)), `${slot}: ${text}`).toBe(true);
    }
    expect(FAILURES.length).toBe(8 + FAILURE_FORMATS.length + FAILURE_CAPABILITIES.length);
    expect(DEFAULT_BASES.length).toBe(3 * 18 + RESTORATION_CRITERIA.length + 1);
  });

  it('PBT: any text outside a slot list is refused there', () => {
    const nearMisses = (text: string) => [
      `${text} `,
      ` ${text}`,
      text.slice(0, -1),
      text.toUpperCase(),
      text.replace(' ', '  '),
      text + text,
      text.replace('1', '01'),
      text.replace('e', 'е'),
    ];
    const names = Object.keys(allowed).sort();
    fc.assert(
      fc.property(
        fc.constantFrom(...names),
        fc.oneof(fc.fullUnicodeString({ maxLength: 120 }), fc.constantFrom(...fixture.privacyProbes)),
        fc.integer({ min: 0, max: 7 }),
        fc.integer({ min: 0, max: 1_000_000 }),
        (slot, text, miss, pick) => {
          const list = [...allowed[slot]!].sort();
          const [schema, base, at] = slots[slot]!;
          for (const candidate of [text, nearMisses(list[pick % list.length]!)[miss]!]) {
            expect(accepts(SCHEMAS[schema], put(base(), at, candidate)), `${slot}: ${candidate}`).toBe(allowed[slot]!.has(candidate));
          }
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          ['default basis', 'Proposed in design section 19.', 0, 0],
          ['failure reason', 'This kind of file is not supported yet: a sock.', 0, 0],
        ],
      }
    );
  });

  it('PBT: a parameter value is accepted exactly inside its row', () => {
    type Offset = 'min' | 'max' | 'below' | 'above' | 'fraction' | 'text';
    fc.assert(
      fc.property(
        fc.constantFrom(...STEP_PARAMETERS),
        fc.constantFrom<Offset>('min', 'max', 'below', 'above', 'fraction', 'text'),
        (row, offset) => {
          const step = GRAPH_STEP_KINDS.find((kind) => row.steps.includes(kind))!;
          let value: unknown;
          let expected: boolean;
          if (row.type === 'text') {
            const first = row.values[0]!;
            value = { min: first, max: row.values[row.values.length - 1], below: first.toUpperCase(), above: `${first}x`, fraction: 1.5, text: '' }[
              offset
            ];
            expected = typeof value === 'string' && row.values.includes(value);
          } else {
            value = { min: row.min, max: row.max, below: row.min - 1, above: row.max + 1, fraction: row.min + 0.5, text: String(row.min) }[offset];
            expected =
              typeof value === 'number' && value >= row.min && value <= row.max && (row.type === 'number' || Number.isInteger(value));
          }
          const parameter = { name: row.name, value, origin: 'default', basis: 'Proposed in design section 10.' };
          expect(accepts(GraphParameterSchema, parameter)).toBe(expected);
          expect(accepts(GraphStepSchema, { kind: step, version: '1.0.0', parameters: [parameter] })).toBe(expected);
        }
      ),
      { seed: SEED, numRuns: 300, examples: [[STEP_PARAMETERS.find((row) => row.name === 'tilePx')!, 'above']] }
    );
  });

  it('PBT P-RST-20: an attempt is accepted exactly when its fields fit its status', () => {
    const base = VALID.ProcessingAttempt[0]!;
    expect(base.status).toBe('ready');
    const gates = { none: undefined, passed: 0, unconfirmed: 1, flux: 2 } as const;
    const beforeTheGate = ['copy_differs', 'frame_times_differ', 'audio_differs'];
    type Gate = keyof typeof gates;
    fc.assert(
      fc.property(
        fc.constantFrom(...ATTEMPT_STATUSES),
        fc.boolean(),
        fc.boolean(),
        fc.constantFrom<Gate>('none', 'passed', 'unconfirmed', 'flux'),
        fc.constantFrom<string | null>(null, ...REJECTION_REASONS),
        fc.boolean(),
        fc.constantFrom('ordered', 'finished_before_started', 'not_a_day'),
        (status, hasVariant, hasMeasured, gate, rejection, hasFailure, times) => {
          const { variantId, measured, gate: _gate, rejection: _rejection, failureReason: _failure, ...rest } = base;
          const attempt: Rec = { ...rest, status };
          if (hasVariant) attempt.variantId = variantId;
          if (hasMeasured) attempt.measured = measured;
          if (gates[gate] !== undefined) attempt.gate = VALID.IntegrityGateResult[gates[gate]!];
          if (rejection !== null) attempt.rejection = rejection;
          if (hasFailure) attempt.failureReason = 'The restoration stopped at its time limit.';
          if (times === 'finished_before_started') {
            attempt.finishedAtUTC = '2031-05-02T19:20:00Z';
            attempt.startedAtUTC = '2031-05-02T19:20:01Z';
          } else if (times === 'not_a_day') {
            attempt.finishedAtUTC = '2031-02-30T19:20:01Z';
          }
          const fits = {
            ready: hasVariant && hasMeasured && rejection === null && !hasFailure,
            rejected: rejection !== null && hasMeasured && !hasVariant && !hasFailure,
            failed: hasFailure && !hasVariant && rejection === null,
          }[status];
          const refusedByGate = gate === 'unconfirmed' || gate === 'flux';
          const readyPassed = !(status === 'ready' && refusedByGate);
          const refusedIsRejected = !(status === 'failed' && refusedByGate);
          const gateNamed =
            (rejection !== 'unconfirmed_points' || gate === 'unconfirmed') && (rejection !== 'star_flux_changed' || gate === 'flux');
          const noGateBeforeIt = rejection === null || !beforeTheGate.includes(rejection) || gate === 'none';
          const expected = fits && readyPassed && refusedIsRejected && gateNamed && noGateBeforeIt && times === 'ordered';
          expect(accepts(ProcessingAttemptSchema, attempt)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 400,
        examples: [
          ['ready', true, true, 'passed', null, false, 'ordered'],
          ['ready', true, true, 'none', null, false, 'ordered'],
          ['rejected', false, true, 'unconfirmed', 'unconfirmed_points', false, 'ordered'],
          ['rejected', false, true, 'flux', 'star_flux_changed', false, 'ordered'],
          ['rejected', false, true, 'none', 'copy_differs', false, 'ordered'],
          ['failed', false, false, 'none', null, true, 'ordered'],
          ['failed', false, true, 'passed', null, true, 'ordered'],
          ['failed', false, true, 'unconfirmed', null, true, 'ordered'],
          ['rejected', false, true, 'none', 'frame_times_differ', true, 'ordered'],
        ],
      }
    );
  });

  it('PBT P-RST-20: a gate result is accepted exactly when its counts add up', () => {
    const { worstFluxChangeSigma: _w, medianFluxRatio: _m, ...base } = VALID.IntegrityGateResult[0]!;
    const tolerance = 3.0;
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 4 }),
        fc.integer({ min: 0, max: 4 }),
        fc.integer({ min: 1, max: 3 }),
        fc.integer({ min: 0, max: 4 }),
        fc.integer({ min: 0, max: 3 }),
        fc.integer({ min: 0, max: 3 }),
        // 3.0 is the tolerance itself, which a star's change may reach and stay within
        fc.constantFrom<number | null>(null, 1.0, 3.0, 5.0),
        fc.constantFrom<number | null>(null, 0.99),
        fc.boolean(),
        (variantPoints, unconfirmed, framesChecked, framesWith, stars, outside, worst, median, passed) => {
          const gate: Rec = {
            ...base,
            variantPointCount: variantPoints,
            unconfirmedPointCount: unconfirmed,
            framesChecked,
            framesWithUnconfirmed: framesWith,
            starsMeasured: stars,
            starsOutsideFluxTolerance: outside,
            fluxToleranceSigma: tolerance,
            passed,
          };
          if (worst !== null) gate.worstFluxChangeSigma = worst;
          if (median !== null) gate.medianFluxRatio = median;
          const expected =
            passed === (unconfirmed === 0 && outside === 0) &&
            unconfirmed <= variantPoints &&
            framesWith <= framesChecked &&
            (framesWith === 0) === (unconfirmed === 0) &&
            framesWith <= unconfirmed &&
            outside <= stars &&
            (worst !== null) === stars > 0 &&
            (median !== null) === stars > 0 &&
            (worst === null || (outside > 0) === worst > tolerance);
          expect(accepts(IntegrityGateResultSchema, gate)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 400,
        examples: [
          [3, 0, 1, 0, 2, 0, 1.0, 0.99, true],
          [3, 2, 2, 2, 2, 0, 1.0, 0.99, false],
          [3, 0, 1, 0, 2, 1, 5.0, 0.99, false],
          [0, 0, 1, 0, 0, 0, null, null, true],
        ],
      }
    );
  });

  it('PBT P-RST-20: a variant is accepted exactly when it fits its kind and its file', () => {
    const base = VALID.MediaVariant[0]!;
    const clipVariant = VALID.MediaVariant[2]!;
    expect(base.mimeType).toBe('image/png');
    expect(String(clipVariant.mimeType).startsWith('video/')).toBe(true);
    const sha = String(base.sourceSha256);
    const id = String(base.id);
    const uris = {
      own: variantUri(sha, id),
      another_variant: variantUri(sha, `var_${'0'.repeat(16)}`),
      store_path: `/srv/albedian/store/${sha.slice(0, 2)}/${sha}/variants/${id}/variant.png`,
    };
    type Uri = keyof typeof uris;
    fc.assert(
      fc.property(
        fc.constantFrom(...VARIANT_KINDS),
        fc.constantFrom(...VARIANT_KINDS),
        fc.boolean(),
        fc.constantFrom(1, 2),
        fc.constantFrom(0, 500),
        fc.boolean(),
        fc.boolean(),
        fc.constantFrom<Uri>('own', 'another_variant', 'store_path'),
        fc.constantFrom(...COLOUR_MODELS),
        fc.constantFrom('own', 'another_source'),
        (kind, disclosureOf, clip, frames, duration, audio, frameTimes, uri, model, asset) => {
          const { frameTimesSha256: _times, ...rest } = base;
          const variant: Rec = {
            ...rest,
            kind,
            disclosure: DISCLOSURES[disclosureOf],
            mimeType: clip ? 'video/quicktime' : 'image/png',
            frameCount: frames,
            durationMs: duration,
            audioStreams: audio ? clipVariant.audioStreams : [],
            uri: uris[uri],
            colour: model === 'rgb' ? VALID.ColourDescription[3] : VALID.ColourDescription[4],
            sourceAssetId: `src_${String(asset === 'own' ? base.sourceSha256 : clipVariant.sourceSha256).slice(0, 16)}`,
          };
          expect((variant.colour as Rec).model).toBe(model);
          if (frameTimes) variant.frameTimesSha256 = clipVariant.frameTimesSha256;
          const expected =
            disclosureOf === kind &&
            (clip || (frames === 1 && duration === 0 && !audio && !frameTimes)) &&
            (!clip || frameTimes) &&
            (kind !== 'display_copy' || !clip) &&
            uri === 'own' &&
            model === (clip ? 'ycbcr' : 'rgb') &&
            asset === 'own';
          expect(accepts(MediaVariantSchema, variant)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 400,
        examples: [
          ['restoration', 'restoration', false, 1, 0, false, false, 'own', 'rgb', 'own'],
          ['restoration', 'restoration', true, 2, 500, true, true, 'own', 'ycbcr', 'own'],
          ['display_copy', 'display_copy', false, 1, 0, false, false, 'own', 'rgb', 'own'],
          ['restoration', 'display_copy', false, 1, 0, false, false, 'own', 'rgb', 'own'],
        ],
      }
    );
  });

  it('PBT P-RST-20: a graph is accepted exactly when its steps and parameters fit its kind', () => {
    const graphBase = VALID.ProcessingGraph[1]!;
    const anyOrder = fc.array(fc.constantFrom(...GRAPH_STEP_KINDS), { minLength: 1, maxLength: 12 });
    const inOrder = fc
      .array(fc.boolean(), { minLength: GRAPH_STEP_KINDS.length, maxLength: GRAPH_STEP_KINDS.length })
      .map((keep) => ['decode', ...GRAPH_STEP_KINDS.slice(1).filter((_, i) => keep[i + 1])]);
    const steps = fc.oneof(anyOrder, inOrder, fc.constant(['decode', 'encode']));
    // The first row of the table a step may hold, at its lowest value, as a default of design section 10
    const parameterOf = (step: string): Rec => {
      const row = STEP_PARAMETERS.find((r) => (r.steps as readonly string[]).includes(step))!;
      return { name: row.name, value: row.type === 'text' ? row.values[0] : row.min, origin: 'default', basis: 'Proposed in design section 10.' };
    };
    type Fault = 'none' | 'no_origin' | 'empty_basis' | 'named_twice' | 'free_basis' | 'another_steps';
    fc.assert(
      fc.property(
        fc.constantFrom(...VARIANT_KINDS),
        fc.constantFrom(...VARIANT_KINDS),
        steps,
        fc.constantFrom<Fault>('none', 'no_origin', 'empty_basis', 'named_twice', 'free_basis', 'another_steps'),
        (kind, requestKind, kinds, fault) => {
          const graph = {
            id: graphBase.id,
            sourceSha256: graphBase.sourceSha256,
            kind,
            pipelineVersion: '1.0.0',
            request: { kind: requestKind },
            steps: kinds.map((step) => ({ kind: step, version: '1.0.0', parameters: [parameterOf(step)] as Rec[] })),
          };
          const first = graph.steps[0]!.parameters;
          if (fault === 'no_origin') delete first[0]!.origin;
          else if (fault === 'empty_basis') first[0]!.basis = '';
          else if (fault === 'named_twice') first.push({ ...first[0]!, basis: 'Decided by the lead in design section 10.' });
          else if (fault === 'free_basis') first[0]!.basis = 'Measured on night.HEIC.';
          else if (fault === 'another_steps') first.push(kinds[0] !== 'copy_audio' ? parameterOf('copy_audio') : parameterOf('decode'));
          const order = kinds.map((step) => (GRAPH_STEP_KINDS as readonly string[]).indexOf(step));
          const stepsFit =
            kind === 'display_copy'
              ? kinds.length === 2 && kinds[0] === 'decode' && kinds[1] === 'encode'
              : kinds[0] === 'decode' &&
                order.every((index, i) => i === 0 || index > order[i - 1]!) &&
                kinds.includes('lift') &&
                kinds.includes('encode');
          const expected = fault === 'none' && kinds.length >= 2 && kinds.length <= 16 && kind === requestKind && stepsFit;
          expect(accepts(ProcessingGraphSchema, graph)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 400,
        examples: [
          ['display_copy', 'display_copy', ['decode', 'encode'], 'none'],
          ['restoration', 'restoration', ['decode', 'lift', 'encode'], 'none'],
          ['restoration', 'restoration', [...GRAPH_STEP_KINDS], 'none'],
          ['restoration', 'restoration', ['decode', 'encode', 'lift'], 'none'],
          ['restoration', 'restoration', ['find_stars', 'lift', 'encode'], 'none'],
          ['restoration', 'restoration', ['decode', 'lift', 'lift', 'encode'], 'none'],
          ['restoration', 'restoration', ['decode', 'lift', 'encode'], 'no_origin'],
          ['restoration', 'restoration', ['decode', 'lift', 'encode'], 'another_steps'],
          ['restoration', 'restoration', ['find_stars', 'decode', 'lift', 'encode'], 'none'],
          ['display_copy', 'display_copy', ['decode', 'encode', 'copy_audio'], 'none'],
        ],
      }
    );
  });

  it('PBT P-RST-20: a colour description is accepted exactly when its fields agree', () => {
    type Assumption = 'none' | 'held' | 'other_value' | 'twice' | 'other_reason';
    const [held, full] = ASSUMPTION_REASONS;
    expect([held.field, held.assumed, full.assumed]).toEqual(['range', 'limited', 'full']);
    fc.assert(
      fc.property(
        fc.constantFrom(...COLOUR_MODELS),
        fc.constantFrom(...COLOUR_MATRICES),
        fc.constantFrom(...CHROMA_SUBSAMPLINGS),
        fc.boolean(),
        fc.constantFrom(...COLOUR_BIT_DEPTHS),
        fc.constantFrom(...COLOUR_TRANSFERS),
        fc.constantFrom(...COLOUR_PRIMARIES),
        fc.boolean(),
        fc.constantFrom<Assumption>('none', 'held', 'other_value', 'twice', 'other_reason'),
        (model, matrix, subsampling, siting, bitDepth, transfer, primaries, profile, assumption) => {
          const colour: Rec = {
            model,
            bitDepth,
            range: 'limited',
            matrix,
            transfer,
            primaries,
            chromaSubsampling: subsampling,
            assumptions: {
              none: [],
              held: [{ ...held }],
              other_value: [{ ...full }],
              twice: [{ ...held }, { ...held }],
              other_reason: [{ ...held, reason: full.reason }],
            }[assumption],
          };
          if (siting) colour.chromaSiting = 'left';
          if (profile) colour.iccProfileSha256 = 'c'.repeat(64);
          const expected =
            (model !== 'rgb' || (matrix === 'identity' && subsampling === '444')) &&
            (model !== 'ycbcr' || matrix !== 'identity') &&
            siting === (subsampling !== '444') &&
            (model === 'rgb' ? [8, 16] : [8, 10]).includes(bitDepth) &&
            (profile || !(transfer === 'icc_parametric' || transfer === 'icc_table' || primaries === 'icc')) &&
            (assumption === 'none' || assumption === 'held');
          expect(accepts(ColourDescriptionSchema, colour)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 400,
        examples: [
          ['rgb', 'identity', '444', false, 16, 'icc_parametric', 'icc', true, 'none'],
          ['ycbcr', 'bt2020nc', '420', true, 10, 'hlg', 'bt2020', false, 'held'],
          ['ycbcr', 'bt709', '420', true, 8, 'bt709', 'bt709', false, 'other_value'],
          ['rgb', 'identity', '444', false, 8, 'srgb', 'icc', false, 'none'],
        ],
      }
    );
  });

  it('PBT P-RST-20: a noise table is accepted exactly when its columns line up', () => {
    type Short = 'none' | 'r' | 'g' | 'b' | 'blockCounts';
    fc.assert(
      fc.property(
        fc.integer({ min: 2, max: 6 }),
        fc.constantFrom('increasing', 'repeated', 'decreasing'),
        fc.constantFrom<Short>('none', 'r', 'g', 'b', 'blockCounts'),
        (bins, order, short) => {
          const intensities = Array.from({ length: bins }, (_, i) => (i + 1) / (bins + 1));
          if (order === 'repeated') intensities[1] = intensities[0]!;
          else if (order === 'decreasing') intensities.reverse();
          const columns: Record<string, number[]> = {};
          for (const name of ['r', 'g', 'b']) columns[name] = Array.from({ length: bins }, (_, i) => 0.01 * (i + 1));
          let blocks = Array.from({ length: bins }, (_, i) => 10 + i);
          if (short === 'blockCounts') blocks = blocks.slice(0, -1);
          else if (short !== 'none') columns[short] = columns[short]!.slice(0, -1);
          const table = { intensities, sigma: columns, blockCounts: blocks };
          const expected = bins >= 3 && short === 'none' && order === 'increasing';
          expect(accepts(NoiseTableSchema, table)).toBe(expected);
        }
      ),
      { seed: SEED, numRuns: 200, examples: [[3, 'increasing', 'none']] }
    );
  });

  it('PBT P-RST-20: measurements are accepted exactly when their counts add up', () => {
    const { noise: _n, noiseNotMeasured: _r, lift: _l, ...base } = VALID.RestorationMeasurements[0]!;
    type Noise = 'table' | 'reason' | 'both' | 'neither';
    type Lift = 'absent' | 'none_at_one' | 'none_above_one' | 'target_above_one';
    fc.assert(
      fc.property(
        fc.constantFrom<Noise>('table', 'reason', 'both', 'neither'),
        fc.integer({ min: 0, max: 3 }),
        fc.integer({ min: 0, max: 3 }),
        fc.constantFrom<Lift>('absent', 'none_at_one', 'none_above_one', 'target_above_one'),
        fc.integer({ min: 0, max: 3 }),
        fc.integer({ min: 0, max: 2 }),
        fc.integer({ min: 0, max: 4 }),
        (noise, found, confirmed, lift, withNeighbours, together, passes) => {
          const measured: Rec = { ...base };
          if (noise === 'table' || noise === 'both') measured.noise = VALID.NoiseTable[0];
          if (noise === 'reason' || noise === 'both') measured.noiseNotMeasured = 'too_few_flat_blocks';
          measured.stars = { found, confirmed };
          if (lift !== 'absent') {
            measured.lift = { gainAtBlack: lift === 'none_at_one' ? 1 : 2.5, decidedBy: lift === 'target_above_one' ? 'target' : 'none' };
          }
          measured.clip = {
            frameCount: 2,
            framesWithNeighbours: withNeighbours,
            exactRepeatShare: 0.5,
            pointsConfirmedOnFramesTogether: together,
            pointsFixedToSensor: 0,
            streamsNotCopied: 0,
          };
          measured.dissolvedPerPass = Array.from({ length: passes }, () => 1);
          const expected =
            noise !== 'both' &&
            confirmed <= found &&
            lift !== 'none_above_one' &&
            withNeighbours <= 2 &&
            passes <= 3 &&
            (together === 0 || withNeighbours > 0);
          expect(accepts(RestorationMeasurementsSchema, measured)).toBe(expected);
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          ['table', 3, 3, 'target_above_one', 2, 1, 3],
          ['table', 3, 3, 'target_above_one', 0, 1, 3],
        ],
      }
    );
  });

  it('PBT P-RST-20: a manifest is accepted exactly when its parts agree', () => {
    const faults = [
      'graph_source',
      'graph_id',
      'attempt_id',
      'disclosure',
      'rejected',
      'no_gate',
      'no_noise',
      'solution_file',
      'solution_of_another',
      'still_gate_frames',
      'stars_beyond_found',
    ] as const;
    const base = VALID.ExportManifest[0]!;
    expect((base.graph as Rec).kind).toBe('restoration');
    fc.assert(
      fc.property(fc.uniqueArray(fc.constantFrom(...faults), { maxLength: 3 }), (chosen) => {
        const manifest = clone(base) as Rec & { graph: Rec; attempt: Rec & { measured: Rec; gate?: Rec } };
        if (chosen.includes('graph_source')) manifest.graph.sourceSha256 = 'd'.repeat(64);
        if (chosen.includes('graph_id')) manifest.graph.id = `graph_${'1'.repeat(16)}`;
        if (chosen.includes('attempt_id')) manifest.attempt.id = `att_${'2'.repeat(16)}`;
        if (chosen.includes('disclosure')) manifest.disclosure = DISCLOSURES.display_copy;
        if (chosen.includes('rejected')) {
          Object.assign(manifest.attempt, { status: 'rejected', rejection: 'unconfirmed_points', gate: VALID.IntegrityGateResult[1] });
          delete manifest.attempt.variantId;
        }
        if (chosen.includes('no_gate')) delete manifest.attempt.gate;
        if (chosen.includes('no_noise')) delete manifest.attempt.measured.noise;
        if (chosen.includes('solution_file')) manifest.observationSolutionId = 'solution.json';
        if (chosen.includes('solution_of_another')) manifest.observationSolutionId = `sol_${'e'.repeat(16)}`;
        if (chosen.includes('still_gate_frames') && manifest.attempt.gate !== undefined) {
          manifest.attempt.gate = { ...manifest.attempt.gate, framesChecked: 50 };
        }
        if (chosen.includes('stars_beyond_found')) manifest.attempt.measured.stars = { found: 30, confirmed: 30 };
        const effective = chosen.filter((fault) => fault !== 'still_gate_frames' || !chosen.includes('no_gate'));
        expect(accepts(ExportManifestSchema, manifest)).toBe(effective.length === 0);
      }),
      { seed: SEED, numRuns: 300, examples: [[[]], [['rejected']], [['still_gate_frames']]] }
    );
  });

  it('PBT P-RST-20: a request is accepted exactly when its option fits its kind', () => {
    fc.assert(
      fc.property(fc.constantFrom(...VARIANT_KINDS), fc.constantFrom<number | null>(null, 0, 1.5, 6, 6.5, -0.5), (kind, exposure) => {
        const request = exposure === null ? { kind } : { kind, exposureEv: exposure };
        const expected = exposure === null || (kind === 'restoration' && exposure >= 0 && exposure <= 6);
        expect(accepts(RestoreRequestSchema, request)).toBe(expected);
      }),
      { seed: SEED, numRuns: 200, examples: [['display_copy', 1.5]] }
    );
  });
});
