import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { AnswerStatementSchema, VoiceAnswerSchema } from '../index.js';

/**
 * The first audit of the answer contract (AUDIT lane C, answers-2): every record it found accepted is refused.
 * Uses only the two schemas and the shared fixture (tests/integration/test_voice_answer_audit_cases.py is its
 * Python twin): the auditor's b1 to b5 records and the same kinds with only the class wrong, every range end and
 * converted unit, sentences that say nothing, unsafe indexes and keys outside the solution contract, and an
 * infinite or NaN number wherever a number goes.
 */
const FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/voice_answer_contract.json');
type SchemaName = 'VoiceAnswer' | 'AnswerStatement';
type Json = Record<string, unknown>;
const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as {
  valid: Record<SchemaName, Json[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
};
const SCHEMAS = { VoiceAnswer: VoiceAnswerSchema, AnswerStatement: AnswerStatementSchema };
const AUDITED = ['auditor ', 'class ', 'table ', 'value ', 'range ', 'source ', 'subject ', 'sentence '];

describe('@albedian/contracts: the answer, against its first audit', () => {
  it('refuses every audited record', () => {
    const cases = fixture.invalid.filter((c) => AUDITED.some((prefix) => c.reason.startsWith(prefix)));
    expect(cases.length).toBeGreaterThanOrEqual(200);
    for (const { schema, sample, reason } of cases) {
      expect(SCHEMAS[schema].safeParse(sample).success, `${schema} should refuse: ${reason}`).toBe(false);
    }
  });

  it('still parses every valid record', () => {
    for (const [name, records] of Object.entries(fixture.valid) as [SchemaName, Json[]][]) {
      for (const record of records) {
        expect(SCHEMAS[name].safeParse(record).success, JSON.stringify(record).slice(0, 200)).toBe(true);
      }
    }
  });

  it.each([Infinity, -Infinity, NaN])('refuses %s wherever a number goes', (bad) => {
    const valid = fixture.valid.AnswerStatement;
    const uncertaintyKind = (s: Json) => ((s.value as Json).uncertainty as Json | undefined)?.kind;
    const withInterval = valid.find((s) => uncertaintyKind(s) === 'interval')!;
    const withHalfWidth = valid.find((s) => uncertaintyKind(s) === 'plus_minus')!;
    const apparent = valid.find((s) => s.quantity === 'apparent_magnitude')!;
    const cases: [Json, string, string | null][] = [
      [apparent, 'amount', null],
      [withInterval, 'uncertainty', 'lower'],
      [withInterval, 'uncertainty', 'upper'],
      [withHalfWidth, 'uncertainty', 'halfWidth'],
    ];
    for (const [statement, key, inner] of cases) {
      const broken = structuredClone(statement);
      const value = broken.value as Json;
      if (inner === null) value[key] = bad;
      else (value[key] as Json)[inner] = bad;
      expect(AnswerStatementSchema.safeParse(broken).success, `${key} ${inner}`).toBe(false);
    }
  });
});

/** Every object of `record`, with the path to it. */
function objectsOf(record: unknown, at: (string | number)[] = []): (string | number)[][] {
  if (Array.isArray(record)) return record.flatMap((item, index) => objectsOf(item, [...at, index]));
  if (record === null || typeof record !== 'object') return [];
  return [at, ...Object.entries(record).flatMap(([key, value]) => objectsOf(value, [...at, key]))];
}
const snake = (key: string) => key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
function changed(record: Json, at: (string | number)[], change: (node: Json) => void): Json {
  const copy = structuredClone(record);
  let node: unknown = copy;
  for (const step of at) node = (node as Record<string | number, unknown>)[step];
  change(node as Json);
  return copy;
}

describe('@albedian/contracts: the answer, an unknown or Python-named key at every level', () => {
  it('refuses an unknown key and every snake_case name, at every level of every valid record', () => {
    let refused = 0;
    for (const [schema, records] of Object.entries(fixture.valid) as [SchemaName, Json[]][]) {
      for (const record of records) {
        for (const at of objectsOf(record)) {
          const unknown = changed(record, at, (node) => { node.unknownKey = 1; });
          expect(SCHEMAS[schema].safeParse(unknown).success, `${schema} ${at.join('/')} unknownKey`).toBe(false);
          refused += 1;
          let node: unknown = record;
          for (const step of at) node = (node as Record<string | number, unknown>)[step];
          for (const key of Object.keys(node as Json)) {
            if (snake(key) === key) continue;
            const renamed = changed(record, at, (n) => { n[snake(key)] = n[key]; delete n[key]; });
            expect(SCHEMAS[schema].safeParse(renamed).success, `${schema} ${at.join('/')} ${key}`).toBe(false);
            refused += 1;
          }
        }
      }
    }
    expect(refused).toBeGreaterThan(1000);
  });
});
