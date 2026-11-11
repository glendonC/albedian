import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { z } from 'zod';
import { describe, expect, it } from 'vitest';
import {
  ANSWERABLE_QUESTION_KINDS,
  ANSWER_SENTENCE_MAX_UTF16,
  ANSWER_STATEMENTS_MAX,
  ANSWER_TEXT_MAX_UTF16,
  ANSWER_UNITS,
  AnswerStatementSchema,
  CATALOG_PROVIDERS,
  CATALOG_SOURCE_QUANTITIES,
  CLASS_RULES,
  CatalogReferenceSchema,
  EPHEMERIS_BODIES,
  EPHEMERIS_METHODS,
  EvidenceClassSchema,
  FINDING_ID_PATTERN,
  FINDING_INDEX_MAX,
  LETTERS_AND_DIGITS_UNICODE_VERSION,
  isLetterOrDigit,
  MOON_PHASE_NAMES,
  OBJECT_TYPES,
  ObservationSolutionSchema,
  PREDICTED_FINDING_ID_PATTERN,
  QUANTITY_SPECS,
  QUESTION_QUANTITIES,
  QuestionKindSchema,
  SAFE_INTEGER_MAX,
  SOLUTION_FIELD_SOURCES,
  SOLUTION_ID_PATTERN,
  SOLUTION_STATUSES,
  SOURCE_PATH_MAX_DEPTH,
  SolutionStatusSchema,
  UNANSWERABLE_REASONS,
  UNCERTAINTY_MEANINGS,
  VoiceAnswerSchema,
} from '../index.js';

/**
 * The answer contract against the samples the Python mirror wrote (`services/contracts/voice.py`,
 * `tests/fixtures/voice_answer_contract.json`): both sides must accept and refuse exactly the same records, hold
 * the same tables, read the same Unicode data, and refuse the same generated faults.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/voice_answer_contract.json');

type SchemaName = 'VoiceAnswer' | 'AnswerStatement';
type Json = Record<string, unknown>;

interface AnswerFixture {
  enums: Record<string, string[]>;
  tables: Record<string, unknown>;
  limits: Record<string, number | string>;
  letters: { unicodeVersion: string; count: number; sha256: string };
  valid: Record<SchemaName, Json[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as AnswerFixture;
const SCHEMAS = { VoiceAnswer: VoiceAnswerSchema, AnswerStatement: AnswerStatementSchema };

function parses(schema: SchemaName, record: unknown): boolean {
  return SCHEMAS[schema].safeParse(record).success;
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/** Whether `steps` names a field of `schema`, `*` standing for an array's elements. */
function names(schema: z.ZodTypeAny, steps: readonly string[]): boolean {
  if (schema instanceof z.ZodEffects) return names(schema.innerType(), steps);
  if (schema instanceof z.ZodOptional) return names(schema.unwrap(), steps);
  if (steps.length === 0) return true;
  const [step, ...rest] = steps;
  if (schema instanceof z.ZodDiscriminatedUnion) {
    return (schema.options as z.ZodTypeAny[]).some((option) => names(option, steps));
  }
  if (step === '*') return schema instanceof z.ZodArray && names(schema.element, rest);
  if (schema instanceof z.ZodObject) {
    const shape = schema.shape as Record<string, z.ZodTypeAny>;
    return Object.hasOwn(shape, step!) && names(shape[step!]!, rest);
  }
  return false;
}

type Row = { quantity: keyof typeof QUANTITY_SPECS; unit: string | null; rule: keyof typeof CLASS_RULES };
type Template = Json;
const ROWS: [Template, Row][] = [
  ...SOLUTION_FIELD_SOURCES.map((row): [Template, Row] => [{ kind: 'solution_field', path: row.path }, row]),
  ...CATALOG_SOURCE_QUANTITIES.map((row): [Template, Row] => [{ kind: 'catalog' }, row]),
  ...Object.entries(EPHEMERIS_METHODS).flatMap(([method, spec]) =>
    spec.rows.map((row): [Template, Row] => [{ kind: 'ephemeris', method }, row])
  ),
];
const idPart = fc.stringOf(fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789'), { minLength: 1, maxLength: 8 });
type Range = { min: number | null; minOpen: boolean; max: number | null; maxOpen: boolean };
const rangeOf = (quantity: keyof typeof QUANTITY_SPECS): Range | null => {
  const spec: { value: string; range?: Range } = QUANTITY_SPECS[quantity];
  return spec.range ?? null;
};

function amountArb(range: Range, whole: boolean): fc.Arbitrary<number> {
  const min = range.min ?? -1e6;
  const max = range.max ?? 1e6;
  if (whole) return fc.integer({ min, max: Math.min(max, 1e6) });
  return fc.double({ min, max, noNaN: true, minExcluded: range.minOpen, maxExcluded: range.maxOpen });
}

function sourceArb(template: Template): fc.Arbitrary<Json> {
  if (template.kind === 'solution_field') {
    const steps = template.path as readonly string[];
    return fc
      .tuple(...steps.map((step): fc.Arbitrary<string | number> => (step === '*' ? fc.integer({ min: 0, max: FINDING_INDEX_MAX }) : fc.constant(step))))
      .map((p) => ({ kind: 'solution_field', path: p }));
  }
  if (template.kind === 'catalog') {
    return fc
      .record({ catalog: fc.constantFrom(...CATALOG_PROVIDERS), release: idPart, rowId: idPart })
      .map((r) => ({ kind: 'catalog', ...r }));
  }
  return fc.constant({ ...template });
}

function subjectArb(template: Template, key: string): fc.Arbitrary<Json> {
  if (key === 'solution') return fc.constant({ kind: 'solution' });
  if (key === 'ephemeris_body') {
    const bodies: readonly string[] =
      template.kind === 'ephemeris' ? EPHEMERIS_METHODS[template.method as keyof typeof EPHEMERIS_METHODS].bodies : EPHEMERIS_BODIES;
    return fc.constantFrom(...bodies).map((body) => ({ kind: 'ephemeris_body', body }));
  }
  return fc.tuple(fc.boolean(), idPart).map(([predictedForm, part]) => ({
    kind: 'finding',
    findingId: `${key === 'predicted' && predictedForm ? 'finding-predicted-' : 'finding-'}${part}`,
    evidenceClass: key,
  }));
}

function valueArb(row: Row): fc.Arbitrary<Json> {
  const spec: { value: string; values?: readonly string[] } = QUANTITY_SPECS[row.quantity];
  const range = rangeOf(row.quantity);
  if (range === null) {
    const words = spec.values ? fc.constantFrom(...spec.values) : idPart;
    return words.map((text) => ({ kind: 'text', text }));
  }
  const count = row.unit === 'count';
  return fc
    .tuple(
      amountArb(range, count),
      count ? fc.constant('none') : fc.constantFrom('none', 'interval', 'plus_minus'),
      fc.constantFrom(...UNCERTAINTY_MEANINGS),
      amountArb(range, false),
      amountArb(range, false),
      fc.double({ min: 0, max: 1e3, noNaN: true })
    )
    .map(([amount, shape, meaning, low, high, halfWidth]) => {
      const value: Json = { kind: 'number', amount, unit: row.unit };
      if (shape === 'interval') value.uncertainty = { kind: 'interval', lower: Math.min(low, amount), upper: Math.max(high, amount), meaning };
      if (shape === 'plus_minus') value.uncertainty = { kind: 'plus_minus', halfWidth, meaning };
      return value;
    });
}

function statementArb(quantity?: string, key?: string): fc.Arbitrary<Json> {
  const choices = ROWS.filter(
    ([, row]) => (quantity === undefined || row.quantity === quantity) && (key === undefined || key in CLASS_RULES[row.rule])
  );
  return fc.constantFrom(...choices).chain(([template, row]) => {
    const keys = Object.keys(CLASS_RULES[row.rule]).sort();
    return (key !== undefined ? fc.constant(key) : fc.constantFrom(...keys)).chain((k) =>
      fc.tuple(subjectArb(template, k), valueArb(row), sourceArb(template)).map(([subject, value, source]) => ({
        subject,
        quantity: row.quantity,
        value,
        evidenceClass: (CLASS_RULES[row.rule] as Record<string, string>)[k],
        source,
      }))
    );
  });
}

function oneClassPerFinding(statements: Json[]): Json[] {
  const seen = new Map<string, unknown>();
  return statements.filter((s) => {
    const subject = s.subject as Json;
    if (subject.kind !== 'finding') return true;
    const id = subject.findingId as string;
    if (!seen.has(id)) seen.set(id, subject.evidenceClass);
    return seen.get(id) === subject.evidenceClass;
  });
}

const answerArb: fc.Arbitrary<Json> = fc.constantFrom(...ANSWERABLE_QUESTION_KINDS).chain((question) =>
  fc
    .constantFrom(...QUESTION_QUANTITIES[question])
    .chain((q) =>
      fc.tuple(
        statementArb(q),
        fc.array(statementArb(), { maxLength: 3 }),
        fc.nat(),
        idPart,
        fc.stringOf(fc.constantFrom('a', 'b', 'c', '7', ' ', '.', ','), { minLength: 1, maxLength: 90 }).filter((t) => /[a-z0-9]/.test(t))
      )
    )
    .map(([answering, more, at, id, sentence]) => {
      // The answering statement first, so a later one that gives its finding another class is the one dropped
      const rest = oneClassPerFinding([answering, ...more]).slice(1);
      const position = at % (rest.length + 1);
      return {
        status: 'answered',
        question,
        observationSolutionId: `sol-${id}`,
        sentence,
        statements: [...rest.slice(0, position), answering, ...rest.slice(position)],
      };
    })
);

// The table, read from its file here, so the tests judge by it and never by this runtime's Unicode data
const TABLE_PATH = path.resolve(__dirname, '../data/letters-and-digits.json');
const TABLE_BYTES = fs.readFileSync(TABLE_PATH);
const TABLE = JSON.parse(TABLE_BYTES.toString('utf8')) as {
  unicodeVersion: string;
  count: number;
  ranges: [number, number][];
  differsFrom: Record<string, [number, number][]>;
};
const inTable = (cp: number) => TABLE.ranges.some(([first, last]) => first <= cp && cp <= last);
function expectedSentence(sentence: string): boolean {
  const chars = [...sentence];
  return (
    sentence.length >= 1 &&
    sentence.length <= ANSWER_SENTENCE_MAX_UTF16 &&
    !chars.some((c) => { const cp = c.codePointAt(0)!; return (cp >= 0xd800 && cp <= 0xdfff) || cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f); }) &&
    chars.some((c) => inTable(c.codePointAt(0)!))
  );
}

describe('@albedian/contracts: the answer', () => {
  it('shares its kinds, lists, tables and limits with the Pydantic mirror', () => {
    expect(fixture.enums.questionKinds).toEqual([...QuestionKindSchema.options]);
    expect(fixture.enums.answerableQuestionKinds).toEqual([...ANSWERABLE_QUESTION_KINDS]);
    expect(fixture.enums.unanswerableReasons).toEqual([...UNANSWERABLE_REASONS]);
    expect(fixture.enums.units).toEqual([...ANSWER_UNITS]);
    expect(fixture.enums.evidenceClasses).toEqual([...EvidenceClassSchema.options]);
    expect(fixture.enums.objectTypes).toEqual([...OBJECT_TYPES]);
    expect(fixture.enums.solutionStatuses).toEqual([...SOLUTION_STATUSES]);
    expect(fixture.enums.moonPhaseNames).toEqual([...MOON_PHASE_NAMES]);
    expect(fixture.enums.catalogProviders).toEqual([...CATALOG_PROVIDERS]);
    expect(fixture.enums.ephemerisBodies).toEqual([...EPHEMERIS_BODIES]);
    expect(fixture.enums.uncertaintyMeanings).toEqual([...UNCERTAINTY_MEANINGS]);
    expect(fixture.tables.quantities).toEqual(QUANTITY_SPECS);
    expect(fixture.tables.questionQuantities).toEqual(QUESTION_QUANTITIES);
    expect(fixture.tables.classRules).toEqual(CLASS_RULES);
    expect(fixture.tables.solutionFieldSources).toEqual(SOLUTION_FIELD_SOURCES);
    expect(fixture.tables.catalogSourceQuantities).toEqual(CATALOG_SOURCE_QUANTITIES);
    expect(fixture.tables.ephemerisMethods).toEqual(EPHEMERIS_METHODS);
    expect(fixture.limits).toEqual({
      sentenceMaxUtf16: ANSWER_SENTENCE_MAX_UTF16,
      textMaxUtf16: ANSWER_TEXT_MAX_UTF16,
      statementsMax: ANSWER_STATEMENTS_MAX,
      findingIndexMax: FINDING_INDEX_MAX,
      sourcePathMaxDepth: SOURCE_PATH_MAX_DEPTH,
      safeIntegerMax: SAFE_INTEGER_MAX,
      findingIdPattern: FINDING_ID_PATTERN.source,
      predictedFindingIdPattern: PREDICTED_FINDING_ID_PATTERN.source,
      solutionIdPattern: SOLUTION_ID_PATTERN.source,
    });
  });

  it('takes its lists and every cited field from the solution contract', () => {
    const reference = CatalogReferenceSchema.innerType();
    expect([...OBJECT_TYPES]).toEqual([...reference.shape.objectType.options]);
    expect([...CATALOG_PROVIDERS]).toEqual([...reference.shape.provider.options]);
    expect([...SOLUTION_STATUSES]).toEqual([...SolutionStatusSchema.options]);
    for (const row of SOLUTION_FIELD_SOURCES) {
      expect(names(ObservationSolutionSchema, row.path), row.path.join('/')).toBe(true);
      expect(row.path.length).toBeLessThanOrEqual(SOURCE_PATH_MAX_DEPTH);
    }
    expect(names(ObservationSolutionSchema, ['__proto__'])).toBe(false);
    expect(names(ObservationSolutionSchema, ['findings', '*', 'nope'])).toBe(false);
  });

  it('reads the one letters-and-digits table Python reads, pinned by its provenance', () => {
    const digest = createHash('sha256').update(TABLE_BYTES).digest('hex');
    expect(fixture.letters).toEqual({ unicodeVersion: TABLE.unicodeVersion, count: TABLE.count, sha256: digest });
    expect(LETTERS_AND_DIGITS_UNICODE_VERSION).toBe('15.1.0');
    const shipped = path.resolve(__dirname, '../../../../apps/web/public/notices/unicode');
    expect(fs.readFileSync(path.join(shipped, 'letters-and-digits.json')).equals(TABLE_BYTES)).toBe(true);
    const provenance = JSON.parse(fs.readFileSync(path.join(shipped, 'PROVENANCE.json'), 'utf8')) as {
      files: { path: string; sha256: string }[];
    };
    expect(provenance.files.find((f) => f.path === 'letters-and-digits.json')?.sha256).toBe(digest);
    expect(TABLE.ranges.reduce((n, [first, last]) => n + last - first + 1, 0)).toBe(TABLE.count);
    // The four Hangul fillers are letters by category that never show: not in the table
    for (const cp of [0x115f, 0x1160, 0x3164, 0xffa0]) expect(isLetterOrDigit(cp)).toBe(false);
  });

  it('judges by the table every range edge and every fixed end', () => {
    const spans = TABLE.ranges;
    // Sorted, apart and maximal: the code point before a start and after an end lies in no range
    for (let i = 0; i < spans.length; i += 1) {
      expect(spans[i]![0] <= spans[i]![1]).toBe(true);
      if (i + 1 < spans.length) expect(spans[i]![1] + 1 < spans[i + 1]![0]).toBe(true);
    }
    const points = new Set([0x0, 0x10ffff, 0xd7ff, 0xd800, 0xdbff, 0xdc00, 0xdfff, 0xe000, 0x1f, 0x20, 0x7e, 0x7f, 0x80, 0x9f, 0xa0]);
    for (const [first, last] of spans) for (const cp of [first - 1, first, last, last + 1]) if (cp >= 0 && cp <= 0x10ffff) points.add(cp);
    const say = { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data' };
    for (const cp of points) {
      const letter = inTable(cp);
      const label = `U+${cp.toString(16)}`;
      expect(isLetterOrDigit(cp), label).toBe(letter);
      if (cp >= 0xd800 && cp <= 0xdfff) {
        const lone = String.fromCharCode(cp);
        expect(parses('VoiceAnswer', { ...say, sentence: lone }), label).toBe(false);
        expect(parses('VoiceAnswer', { ...say, sentence: `a${lone}` }), label).toBe(false);
        continue;
      }
      const control = cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f);
      expect(parses('VoiceAnswer', { ...say, sentence: String.fromCodePoint(cp) }), label).toBe(letter && !control);
      expect(parses('VoiceAnswer', { ...say, sentence: `a${String.fromCodePoint(cp)}` }), label).toBe(!control);
    }
  });

  it('judges by the table every code point where Unicode 15.0, 16.0 and 17.0 differ from it', () => {
    const refusal = (sentence: string) => ({ status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence });
    let checked = 0;
    for (const [version, ranges] of Object.entries(TABLE.differsFrom)) {
      expect(ranges.length, version).toBeGreaterThan(0);
      for (const [first, last] of ranges) {
        for (let cp = first; cp <= last; cp += 1) {
          expect(isLetterOrDigit(cp), `U+${cp.toString(16)}`).toBe(inTable(cp));
          expect(parses('VoiceAnswer', refusal(String.fromCodePoint(cp))), `U+${cp.toString(16)} (${version})`).toBe(inTable(cp));
          checked += 1;
        }
      }
    }
    expect(checked).toBe(622 + 4382 + 9039);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    for (const [name, records] of Object.entries(fixture.valid) as [SchemaName, Json[]][]) {
      expect(records.length).toBeGreaterThan(0);
      for (const record of records) {
        const parsed = SCHEMAS[name].safeParse(record);
        expect(parsed.error?.issues ?? [], `${name}: ${JSON.stringify(record)}`).toEqual([]);
        expect(parsed.data).toEqual(record);
      }
    }
  });

  it('refuses every record the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(250);
    for (const { schema, sample, reason } of fixture.invalid) {
      expect(parses(schema, sample), `${schema} should refuse: ${reason}`).toBe(false);
    }
  });

  it('refuses a lone surrogate and an index past the bound', () => {
    const name = fixture.valid.AnswerStatement.find((s) => s.quantity === 'name')!;
    expect(parses('AnswerStatement', { ...name, value: { kind: 'text', text: 'Sample \ud800 Star' } })).toBe(false);
    const distance = fixture.valid.AnswerStatement.find((s) => (s.source as Json).kind === 'solution_field' && s.quantity === 'distance')!;
    const tail = ((distance.source as Json).path as unknown[]).slice(2);
    const at = (index: number) => ({ ...distance, source: { kind: 'solution_field', path: ['findings', index, ...tail] } });
    expect(parses('AnswerStatement', at(FINDING_INDEX_MAX))).toBe(true);
    for (const index of [FINDING_INDEX_MAX + 1, SAFE_INTEGER_MAX + 1, Infinity]) {
      expect(parses('AnswerStatement', at(index)), String(index)).toBe(false);
    }
  });

  it('PBT: generated answers pass, and the four generated faults are refused', () => {
    fc.assert(
      fc.property(
        answerArb,
        fc.nat(),
        fc.constantFrom(...ROWS.filter(([, r]) => r.unit !== null).map(([, r]) => r.quantity)).chain((q) => statementArb(q)),
        fc.constantFrom('predicted', 'ephemeris_body').chain((key) => statementArb(undefined, key)),
        fc.tuple(...ROWS.map(([template, row]) => fc.tuple(valueArb(row), sourceArb(template)))),
        fc.constantFrom(...UNANSWERABLE_REASONS),
        fc.constantFrom(...ANSWERABLE_QUESTION_KINDS),
        (answer, at, number, aboutBody, claims, reason, asked) => {
          expect(VoiceAnswerSchema.safeParse(answer).data).toEqual(answer);
          const statements = answer.statements as Json[];
          const index = at % statements.length;
          const faulty = (change: (statement: Json) => void): Json => {
            const broken = clone(answer);
            change((broken.statements as Json[])[index]!);
            return broken;
          };
          // A statement with no source
          expect(parses('VoiceAnswer', faulty((s) => delete s.source))).toBe(false);
          // A number with no unit
          const unitless = clone(number);
          delete (unitless.value as Json).unit;
          expect(parses('AnswerStatement', number)).toBe(true);
          expect(parses('AnswerStatement', unitless)).toBe(false);
          // An observed statement about a predicted body, whatever the source
          expect(parses('AnswerStatement', aboutBody)).toBe(true);
          expect(parses('AnswerStatement', { ...aboutBody, evidenceClass: 'observed' })).toBe(false);
          ROWS.forEach(([, row], i) => {
            const [value, source] = claims[i]!;
            const claimed = { ...aboutBody, quantity: row.quantity, value, evidenceClass: 'observed', source };
            expect(parses('AnswerStatement', claimed)).toBe(false);
          });
          // A refusal that carries statements
          const question = reason === 'outside_the_data' ? 'unanswerable' : asked;
          const refusal = { status: 'unanswerable', question, reason, sentence: answer.sentence };
          expect(VoiceAnswerSchema.safeParse(refusal).data).toEqual(refusal);
          expect(parses('VoiceAnswer', { ...refusal, statements })).toBe(false);
        }
      ),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: only the class its source decides, and only numbers in range', () => {
    fc.assert(
      fc.property(statementArb(), fc.double({ min: 1e-6, max: 1e3, noNaN: true }), fc.integer({ min: 1, max: 1000 }), (statement, step, wholeStep) => {
        expect(parses('AnswerStatement', statement)).toBe(true);
        for (const other of EvidenceClassSchema.options) {
          if (other !== statement.evidenceClass) expect(parses('AnswerStatement', { ...statement, evidenceClass: other })).toBe(false);
        }
        const range = rangeOf(statement.quantity as keyof typeof QUANTITY_SPECS);
        if (range === null) return;
        // Past a count's end by a whole step: 2^53 - 1 plus a fraction is 2^53 - 1 again as a double
        const by = (statement.value as Json).unit === 'count' ? wholeStep : step;
        const outside: number[] = [];
        if (range.min !== null) outside.push(range.minOpen ? range.min : range.min - by);
        if (range.max !== null) outside.push(range.maxOpen ? range.max : range.max + by);
        const { uncertainty: _dropped, ...value } = statement.value as Json;
        for (const amount of outside) {
          expect(parses('AnswerStatement', { ...statement, value: { ...value, amount } }), String(amount)).toBe(false);
        }
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: a sentence counts UTF-16 units and holds a letter or digit that shows, with no control character', () => {
    const char = fc.oneof(
      fc.fullUnicode(),
      fc.constantFrom('\x1c', '\x85', '᠎', '​', 'ㅤ', '﻿', '\t', '\u{1F52D}', 'a', '7', ' ', '\ud800')
    );
    const sentence = fc.oneof(
      fc.array(char, { maxLength: 8 }).map((cs) => cs.join('')),
      fc.array(char, { minLength: 40, maxLength: 95 }).map((cs) => cs.join(''))
    );
    fc.assert(
      fc.property(sentence, (s) => {
        const refusal = { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence: s };
        expect(parses('VoiceAnswer', refusal)).toBe(expectedSentence(s));
      }),
      { seed: SEED, numRuns: NUM_RUNS, examples: [['\x1c'], ['ㅤ'], ['a'.repeat(89) + '\u{1F52D}'], ['\u{1D400}'.repeat(45)]] }
    );
  });
});
