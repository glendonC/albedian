import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import * as contracts from '../index.js';

/**
 * The generated wire faults of WIRE_RULES.md rule 6, the TypeScript half: tests/integration/test_wire_faults.py is the
 * Python half, and scripts/generate_wire_faults.py writes the files both read.
 *
 * Each fault keeps how to build its JSON text from a valid sample of the pair's own fixture and the Python mirror's
 * reading of that text. Here each text is built the same way, read with JSON.parse and judged by the Zod schema. A
 * fault the two sides read differently is a defect, and the test passes only while the set of them is exactly the
 * index's `knownDisagreements`, each with the unit that removes it. A fault both sides read alike but against a wire
 * rule (a fault the rules refuse outright taken, or a minus zero kept) is exactly the index's `ruleGaps`.
 */

type Reading = 'refused' | 'accepted' | 'accepted as minus zero';
interface Fault {
  sample: string;
  pointer: string;
  fault: string;
  to?: string;
  raw?: string;
  python: Reading;
}
interface FaultFile {
  pair: string;
  base: string;
  overLongUtf16: number;
  constants: Record<string, string>;
  samples: Record<string, { schema: string; pointer: string }>;
  faults: Fault[];
}
interface Listed {
  pair: string;
  sample: string;
  pointer: string;
  fault: string;
  rule: string;
  unit: string;
  python?: Reading;
  typescript?: Reading;
  reading?: Reading;
}
interface Index {
  pairs: Record<string, { faults: string; base: string; schemas: string[] }>;
  overLongUtf16: number;
  refusedOutright: string[];
  knownDisagreements: Listed[];
  ruleGaps: Listed[];
}

const ROOT = path.resolve(__dirname, '../../../..');
const MARK = '@@wire fault@@';
const UNKNOWN_KEY = 'unknownKey';
const readJson = (relative: string): unknown => JSON.parse(fs.readFileSync(path.join(ROOT, relative), 'utf8'));
const index = readJson('tests/fixtures/wire_faults/index.json') as Index;
const REFUSED_OUTRIGHT = new Set(index.refusedOutright);
// Rule 6's faults, written out here from WIRE_RULES.md as the Python half writes them, not read from the files
const RULE_6_TEXTS: Record<string, string> = {
  '1.0': '1.0',
  '1e400': '1e400',
  'a 401 digit integer': '1' + '0'.repeat(400),
  '2^53 + 1': '9007199254740993',
  'minus zero': '-0.0',
  true: 'true',
  text: '"1"',
  '1': '1',
  'the text true': '"true"',
  'year 0': '"0000-01-01T00:00:00Z"',
  'day 30 of February': '"2031-02-30T00:00:00Z"',
};
const RULE_6_FAULTS = new Set([
  ...Object.keys(RULE_6_TEXTS),
  'rename',
  'unknown key',
  'over long',
  'a lone surrogate',
  'outside the basic plane',
  'a control character',
]);
// An over long text: past every text limit the pairs state, the longest being the pointing statement's 266 units
const OVER_LONG_UTF16 = 4097;
const TAKEN_BY_SOME_FIELD = new Set(['1.0', 'minus zero', '2^53 + 1', 'outside the basic plane']);
const PAIRS = Object.keys(index.pairs).sort();

function must<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new Error(`no ${what}`);
  return value;
}
const pairOf = (pair: string) => must(index.pairs[pair], `pair ${pair} in the index`);
const sampleOf = (file: FaultFile, name: string) => must(file.samples[name], `sample ${name} in the ${file.pair} faults`);

function parts(pointer: string): string[] {
  return pointer === '' ? [] : pointer.split('/').slice(1).map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'));
}

function resolve(document: unknown, pointer: string): unknown {
  let node = document;
  for (const part of parts(pointer)) {
    node = Array.isArray(node) ? node[Number(part)] : (node as Record<string, unknown>)[part];
  }
  return node;
}

/** The JSON text a fault describes, built from its sample as scripts/generate_wire_faults.py's wire_text builds it. */
function wireText(fault: Fault, file: FaultFile, base: unknown): string {
  const record = structuredClone(resolve(base, sampleOf(file, fault.sample).pointer));
  const path_ = parts(fault.pointer);
  if (fault.fault === 'unknown key') {
    const target = resolve(record, fault.pointer) as Record<string, unknown>;
    expect(UNKNOWN_KEY in target).toBe(false);
    target[UNKNOWN_KEY] = 1;
    return JSON.stringify(record);
  }
  const parentPointer = path_.slice(0, -1).map((part) => '/' + part.replace(/~/g, '~0').replace(/\//g, '~1')).join('');
  const parent = resolve(record, parentPointer) as Record<string, unknown> | unknown[];
  const last = must(path_[path_.length - 1], `field at ${fault.pointer}`);
  if (fault.fault === 'rename') {
    const holder = parent as Record<string, unknown>;
    holder[fault.to as string] = holder[last];
    delete holder[last];
    return JSON.stringify(record);
  }
  if (Array.isArray(parent)) parent[Number(last)] = MARK;
  else parent[last] = MARK;
  const pieces = JSON.stringify(record).split(JSON.stringify(MARK));
  expect(pieces.length).toBe(2);
  const raw = fault.raw ?? file.constants[fault.fault] ?? JSON.stringify('a'.repeat(file.overLongUtf16));
  return pieces.join(raw);
}

function schemaOf(name: string): ZodTypeAny {
  const schema = (contracts as Record<string, unknown>)[`${name}Schema`];
  expect(schema, `${name}Schema is exported`).toBeDefined();
  return schema as ZodTypeAny;
}

function reading(schema: ZodTypeAny, fault: Fault, text: string): Reading {
  const parsed = schema.safeParse(JSON.parse(text));
  if (!parsed.success) return 'refused';
  if (fault.fault === 'minus zero' && Object.is(resolve(parsed.data, fault.pointer), -0)) return 'accepted as minus zero';
  return 'accepted';
}

const missesARule = (fault: string, read: Reading): boolean =>
  (REFUSED_OUTRIGHT.has(fault) && read !== 'refused') || read === 'accepted as minus zero';
const keyOf = (pair: string, item: { sample: string; pointer: string; fault: string }): string =>
  `${pair} ${item.sample} ${item.pointer} ${item.fault}`;

const readings = new Map<string, { fault: Fault; typescript: Reading }[]>();
function typescriptReadings(pair: string): { fault: Fault; typescript: Reading }[] {
  if (!readings.has(pair)) {
    const file = readJson(pairOf(pair).faults) as FaultFile;
    const base = readJson(file.base);
    readings.set(
      pair,
      file.faults.map((fault) => ({
        fault,
        typescript: reading(schemaOf(sampleOf(file, fault.sample).schema), fault, wireText(fault, file, base)),
      })),
    );
  }
  return readings.get(pair) as { fault: Fault; typescript: Reading }[];
}

describe('the generated wire faults (WIRE_RULES.md rule 6)', () => {
  it('names a fault file for each pair, whose every schema is exported', () => {
    expect(PAIRS.length).toBeGreaterThan(0);
    for (const pair of PAIRS) {
      const file = readJson(pairOf(pair).faults) as FaultFile;
      expect(file.pair).toBe(pair);
      expect(file.base).toBe(pairOf(pair).base);
      expect(file.overLongUtf16).toBe(index.overLongUtf16);
      expect(file.overLongUtf16).toBe(OVER_LONG_UTF16);
      expect(file.faults.length).toBeGreaterThan(0);
      expect(file.constants).toEqual(RULE_6_TEXTS);
      expect(file.faults.filter((fault) => !RULE_6_FAULTS.has(fault.fault))).toEqual([]);
      for (const name of pairOf(pair).schemas) schemaOf(name);
    }
  });

  it('refuses outright every fault of rule 6 but the four some field may take', () => {
    expect([...REFUSED_OUTRIGHT].sort()).toEqual([...RULE_6_FAULTS].filter((fault) => !TAKEN_BY_SOME_FIELD.has(fault)).sort());
  });

  it.each(PAIRS)('reads every %s fault as the Python mirror does, but for the known disagreements', (pair) => {
    const found = typescriptReadings(pair)
      .filter(({ fault, typescript }) => typescript !== fault.python)
      .map(({ fault, typescript }) => `${keyOf(pair, fault)}: python ${fault.python}, typescript ${typescript}`);
    const listed = index.knownDisagreements
      .filter((entry) => entry.pair === pair)
      .map((entry) => `${keyOf(pair, entry)}: python ${entry.python}, typescript ${entry.typescript}`);
    expect(found.sort()).toEqual(listed.sort());
  });

  it.each(PAIRS)('breaks a wire rule on %s only where the index lists it', (pair) => {
    const misses = typescriptReadings(pair)
      .filter(({ fault, typescript }) => missesARule(fault.fault, typescript))
      .map(({ fault }) => keyOf(pair, fault));
    const gaps = index.ruleGaps.filter((entry) => entry.pair === pair).map((entry) => keyOf(pair, entry));
    const split = index.knownDisagreements
      .filter((entry) => entry.pair === pair && missesARule(entry.fault, entry.typescript as Reading))
      .map((entry) => keyOf(pair, entry));
    expect(misses.sort()).toEqual([...gaps, ...split].sort());
  });

  it.each(PAIRS)('takes every %s sample the faults are built from', (pair) => {
    const file = readJson(pairOf(pair).faults) as FaultFile;
    const base = readJson(file.base);
    for (const [name, sample] of Object.entries(file.samples)) {
      expect(schemaOf(sample.schema).safeParse(resolve(base, sample.pointer)).success, name).toBe(true);
    }
  });

  it.each(PAIRS)('reads each %s rule gap as the index says', (pair) => {
    const read = new Map(typescriptReadings(pair).map(({ fault, typescript }) => [keyOf(pair, fault), typescript]));
    for (const entry of index.ruleGaps.filter((gap) => gap.pair === pair)) {
      expect(read.get(keyOf(pair, entry)), keyOf(pair, entry)).toBe(entry.reading);
    }
  });
});
