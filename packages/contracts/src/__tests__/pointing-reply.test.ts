import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import * as contracts from '../index.js';
import { POINTING_RULES } from '../pointing.js';
import {
  POINTING_RECEIPT_FORMAT,
  POINTING_RECEIPT_FORMAT_VERSION,
  POINTING_RECORD_KEYS,
  POINTING_REFUSAL_SENTENCES,
  POINTING_REPLY_ISSUES_MAX,
  POINTING_REPLY_PATH_MAX_DEPTH,
  POINTING_REPLY_SENTENCES,
  PointingRecordReceiptSchema,
  PointingRecordRefusalSchema,
  PointingRefusalIssueSchema,
} from '../pointing-reply.js';

/**
 * The record route's reply contract, the TypeScript half: tests/fixtures/pointing_reply_contract.json
 * is written by tests/integration/test_pointing_reply_contract.py, and every verdict in it holds here too.
 */
type Kind = 'receipt' | 'refusal';
type Json = Record<string, any>;

interface Sample {
  name: string;
  kind: Kind;
  verdict?: 'accepted' | 'refused';
  reply: unknown;
}

interface WireFault {
  name: string;
  kind: Kind;
  path: (string | number)[];
  op: 'add' | 'rename' | 'raw';
  to?: string;
  raw?: string;
  verdict: 'accepted' | 'refused';
}

interface Fixture {
  format: string;
  formatVersion: number;
  limits: { issuesMax: number; pathMaxDepth: number };
  routeSentences: Record<string, string>;
  refusalSentences: string[];
  recordKeys: string[];
  recordSha256: string;
  valid: Sample[];
  invalid: Sample[];
  edges: Sample[];
  wireFaults: WireFault[];
  valueFree: { rule: string; record: unknown; refusal: unknown }[];
}

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const WIRE_MARK = '@@wire fault@@';
const SHA256_HEX = /^[0-9a-f]{64}$/;
const ROOT = path.resolve(__dirname, '../../../..');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/pointing_reply_contract.json'), 'utf8')) as Fixture;
const SCHEMAS: Record<Kind, ZodTypeAny> = { receipt: PointingRecordReceiptSchema, refusal: PointingRecordRefusalSchema };
const accepts = (kind: Kind, reply: unknown): boolean => SCHEMAS[kind].safeParse(reply).success;

const baseReceipt = (): Json => structuredClone(fixture.valid.find((sample) => sample.kind === 'receipt')!.reply as Json);
const baseIssueRefusal = (): Json => ({
  detail: fixture.routeSentences.notARecord,
  issues: [{ path: ['motion'], message: fixture.routeSentences.notARecord }],
});

function wireText(fault: WireFault): string {
  const record: Json = fault.kind === 'refusal' ? baseIssueRefusal() : baseReceipt();
  if (fault.op === 'add') {
    let target: Json = record;
    for (const key of fault.path) target = target[key];
    target.unknownKey = 1;
    return JSON.stringify(record);
  }
  if (fault.op === 'rename') {
    let target: Json = record;
    for (const key of fault.path.slice(0, -1)) target = target[key];
    const last = String(fault.path.at(-1));
    const rebuilt: Json = {};
    for (const [key, value] of Object.entries(target)) rebuilt[key === last ? fault.to! : key] = value;
    if (fault.path.length === 1) return JSON.stringify(rebuilt);
    let parent: Json = record;
    for (const key of fault.path.slice(0, -2)) parent = parent[key];
    parent[fault.path.at(-2)!] = rebuilt;
    return JSON.stringify(record);
  }
  let target: Json = record;
  for (const key of fault.path.slice(0, -1)) target = target[key];
  target[fault.path.at(-1)!] = WIRE_MARK;
  return JSON.stringify(record).replace(JSON.stringify(WIRE_MARK), fault.raw!);
}

function validSha256(value: unknown): boolean {
  return typeof value === 'string' && SHA256_HEX.test(value) && !/^0+$/.test(value);
}

function expectedReceiptValid(candidate: Record<string, unknown>): boolean {
  const keys = Object.keys(candidate);
  return (
    keys.length === 5 &&
    keys.every((key) => ['format', 'formatVersion', 'sourceAssetSha256', 'pointingRecordSha256', 'alreadyStored'].includes(key)) &&
    candidate.format === POINTING_RECEIPT_FORMAT &&
    candidate.formatVersion === 1 &&
    validSha256(candidate.sourceAssetSha256) &&
    validSha256(candidate.pointingRecordSha256) &&
    typeof candidate.alreadyStored === 'boolean'
  );
}

function expectedRefusalValid(candidate: Record<string, unknown>): boolean {
  const allowedSentences = new Set<string>(POINTING_REFUSAL_SENTENCES);
  const allowedKeys = new Set<string>(POINTING_RECORD_KEYS);
  const keys = Object.keys(candidate);
  if (!keys.includes('detail') || !keys.every((key) => key === 'detail' || key === 'issues')) return false;
  if (typeof candidate.detail !== 'string' || !allowedSentences.has(candidate.detail)) return false;
  if (!('issues' in candidate) || candidate.issues === undefined) return true;
  if (!Array.isArray(candidate.issues) || candidate.issues.length > POINTING_REPLY_ISSUES_MAX) return false;
  for (const issue of candidate.issues) {
    if (typeof issue !== 'object' || issue === null || Array.isArray(issue)) return false;
    const issueKeys = Object.keys(issue as Record<string, unknown>);
    if (issueKeys.length !== 2 || !issueKeys.includes('path') || !issueKeys.includes('message')) return false;
    const { path: issuePath, message } = issue as { path: unknown; message: unknown };
    if (!Array.isArray(issuePath) || issuePath.length < 1 || issuePath.length > POINTING_REPLY_PATH_MAX_DEPTH) return false;
    if (!issuePath.every((step) => typeof step === 'string' && allowedKeys.has(step))) return false;
    if (typeof message !== 'string' || !allowedSentences.has(message)) return false;
  }
  return true;
}

describe('the record route reply contract', () => {
  it('holds the decisions the Python side writes', () => {
    expect([POINTING_RECEIPT_FORMAT, POINTING_RECEIPT_FORMAT_VERSION]).toEqual([fixture.format, fixture.formatVersion]);
    expect([POINTING_RECEIPT_FORMAT, POINTING_RECEIPT_FORMAT_VERSION]).toEqual(['albedian.pointing-receipt', 1]);
    expect([POINTING_REPLY_ISSUES_MAX, POINTING_REPLY_PATH_MAX_DEPTH]).toEqual([fixture.limits.issuesMax, fixture.limits.pathMaxDepth]);
    expect([POINTING_REPLY_ISSUES_MAX, POINTING_REPLY_PATH_MAX_DEPTH]).toEqual([32, 8]);
    expect(POINTING_REPLY_SENTENCES).toEqual(fixture.routeSentences);
    expect(POINTING_REFUSAL_SENTENCES).toEqual([...Object.values(POINTING_RULES), ...Object.values(fixture.routeSentences)]);
    expect(POINTING_REFUSAL_SENTENCES).toEqual(fixture.refusalSentences);
    expect(POINTING_RECORD_KEYS).toEqual(fixture.recordKeys);
  });

  it.each(fixture.valid.map((sample) => [sample.name, sample] as const))('accepts %s unchanged', (_, sample) => {
    const read = SCHEMAS[sample.kind].safeParse(sample.reply);
    expect(read.success && read.data).toEqual(sample.reply);
    expect(accepts(sample.kind === 'receipt' ? 'refusal' : 'receipt', sample.reply)).toBe(false);
  });

  it.each(fixture.invalid.map((sample) => [sample.name, sample] as const))('refuses %s', (_, sample) => {
    expect(accepts(sample.kind, sample.reply)).toBe(false);
  });

  it.each(fixture.edges.map((sample) => [sample.name, sample] as const))('holds the bound at %s', (_, sample) => {
    expect(accepts(sample.kind, sample.reply)).toBe(sample.verdict === 'accepted');
  });

  it.each(fixture.wireFaults.map((fault) => [fault.name, fault] as const))('judges wire fault %s from raw JSON as Python does', (_, fault) => {
    const parsedJson = JSON.parse(wireText(fault));
    const read = SCHEMAS[fault.kind].safeParse(parsedJson);
    expect(read.success).toBe(fault.verdict === 'accepted');
    if (read.success && fault.kind === 'receipt') {
      expect((read.data as { formatVersion: number }).formatVersion).toBe(1);
    }
  });

  it('accepts each value-free refusal the Python side pairs with the record it refuses', () => {
    expect(fixture.valueFree.length).toBe(Object.keys(POINTING_RULES).length);
    for (const pair of fixture.valueFree) expect(accepts('refusal', pair.refusal), pair.rule).toBe(true);
  });

  it('judges generated receipts and refusals by the contract rules (PBT)', () => {
    const photoSha = (baseReceipt().sourceAssetSha256 as string);
    const recordSha = fixture.recordSha256;
    const shaArb = fc.oneof(
      fc.constant(photoSha),
      fc.constant(recordSha),
      fc.constant('0'.repeat(64)),
      fc.constant(`${'0'.repeat(63)}1`),
      fc.constant(`1${'0'.repeat(63)}`),
      fc.hexaString({ minLength: 64, maxLength: 64 }).map((value) => value.toLowerCase()),
      fc.string({ minLength: 63, maxLength: 65 })
    );
    const receiptArb = fc.record(
      {
        format: fc.constantFrom(POINTING_RECEIPT_FORMAT, 'albedian.pointing-record', ''),
        formatVersion: fc.constantFrom<unknown>(1, 0, 2, 1.5, true, '1'),
        sourceAssetSha256: shaArb,
        pointingRecordSha256: shaArb,
        alreadyStored: fc.constantFrom<unknown>(false, true, 1, 'true', null),
        extraKey: fc.constant(1),
      },
      { requiredKeys: ['format', 'formatVersion', 'sourceAssetSha256', 'pointingRecordSha256', 'alreadyStored'] }
    );
    fc.assert(
      fc.property(receiptArb, (candidate) => {
        expect(accepts('receipt', candidate)).toBe(expectedReceiptValid(candidate));
      }),
      {
        seed: SEED,
        numRuns: 80,
        examples: [
          [baseReceipt() as any],
          [{ ...baseReceipt(), sourceAssetSha256: '0'.repeat(64) } as any],
          [{ ...baseReceipt(), sourceAssetSha256: `${'0'.repeat(63)}1`, pointingRecordSha256: `1${'0'.repeat(63)}` } as any],
        ],
      }
    );

    const issueArb = fc.record(
      {
        path: fc.array(fc.constantFrom(...POINTING_RECORD_KEYS.slice(0, 6), 'unknownKey', ''), {
          minLength: 0,
          maxLength: POINTING_REPLY_PATH_MAX_DEPTH + 1,
        }),
        message: fc.constantFrom(...POINTING_REFUSAL_SENTENCES.slice(0, 6), 'free text'),
        extraIssueKey: fc.constant(1),
      },
      { requiredKeys: ['path', 'message'] }
    );
    const refusalArb = fc.record(
      {
        detail: fc.constantFrom<unknown>(...POINTING_REFUSAL_SENTENCES.slice(0, 6), 'free text', null),
        issues: fc.oneof(fc.constant(null), fc.array(issueArb, { minLength: 0, maxLength: POINTING_REPLY_ISSUES_MAX + 1 })),
        extraRefusalKey: fc.constant(1),
      },
      { requiredKeys: ['detail'] }
    );
    fc.assert(
      fc.property(refusalArb, (candidate) => {
        expect(accepts('refusal', candidate)).toBe(expectedRefusalValid(candidate));
      }),
      {
        seed: SEED,
        numRuns: 80,
        examples: [
          [{ detail: POINTING_REPLY_SENTENCES.differentRecordHeld } as any],
          [baseIssueRefusal() as any],
          [{ detail: POINTING_REPLY_SENTENCES.notARecord, issues: Array.from({ length: POINTING_REPLY_ISSUES_MAX + 1 }, () => ({ path: ['motion'], message: POINTING_REPLY_SENTENCES.notARecord })) } as any],
        ],
      }
    );
  });

  it('names the placeholder rule sentence when either receipt hash is 64 zeros', () => {
    for (const field of ['sourceAssetSha256', 'pointingRecordSha256'] as const) {
      const parsed = PointingRecordReceiptSchema.safeParse({ ...baseReceipt(), [field]: '0'.repeat(64) });
      expect(parsed.success).toBe(false);
      expect(parsed.error?.issues.map((issue) => [issue.path, issue.message])).toEqual([[[field], POINTING_RULES.placeholderSha256]]);
    }
  });

  it('is exported from @albedian/contracts, checked by verify:contracts, and registered in wire_faults', () => {
    const barrel = contracts as Record<string, unknown>;
    expect(barrel.PointingRecordReceiptSchema).toBe(PointingRecordReceiptSchema);
    expect(barrel.PointingRefusalIssueSchema).toBe(PointingRefusalIssueSchema);
    expect(barrel.PointingRecordRefusalSchema).toBe(PointingRecordRefusalSchema);
    expect(barrel.POINTING_RECEIPT_FORMAT).toBe(POINTING_RECEIPT_FORMAT);

    const verify = fs.readFileSync(path.join(ROOT, 'scripts/verify-contracts.mjs'), 'utf8');
    for (const name of ['PointingRecordReceiptSchema', 'PointingRecordRefusalSchema']) {
      expect(verify, name).toContain(`'${name}'`);
    }

    const wireIndex = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/fixtures/wire_faults/index.json'), 'utf8')) as {
      pairs: Record<string, { faults: string; base: string; schemas: string[] }>;
    };
    expect(wireIndex.pairs.pointing_reply).toEqual({
      faults: 'tests/fixtures/wire_faults/pointing_reply.json',
      base: 'tests/fixtures/pointing_reply_contract.json',
      schemas: ['PointingRecordReceipt', 'PointingRecordRefusal'],
    });
  });
});
