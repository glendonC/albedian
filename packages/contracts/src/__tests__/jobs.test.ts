import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  INTERRUPTED_SENTENCE,
  JOB_KINDS,
  JOB_MESSAGES,
  JOB_PASS_LABELS,
  JOB_PASS_MAX,
  JOB_PATTERNS,
  JOB_PRE_RUN_STAGES,
  JOB_STATE_FIELDS,
  JOB_STATE_TABLE,
  jobStateRow,
  JOB_PROGRESS_UNITS,
  JOB_SPAN_MAX_SEC,
  JOB_STAGE_UNITS,
  SOLVE_FAILURE_SENTENCES,
  UPLOAD_JOB_STAGES,
  UPLOAD_JOB_OPTIONAL_FIELDS,
  UPLOAD_JOB_STATES,
  UploadJobSchema,
} from '../index.js';

/**
 * The upload job record (platform-1) against the samples the Python mirror wrote (`services/contracts/jobs.py`,
 * `tests/fixtures/job_contract.json`): both sides take and refuse exactly the same records, refuse each fault with
 * the same message, and give every edge sample the same verdict.
 */
const FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/job_contract.json');

interface JobFixture {
  patterns: Record<string, string>;
  enums: Record<string, string[]>;
  stageUnits: Record<string, Record<string, string[]>>;
  limits: { passMax: number; spanMaxSec: number };
  messages: string[];
  valid: unknown[];
  invalid: { schema: 'UploadJob'; sample: unknown; reason: string; message: string }[];
  edges: { what: string; sample: unknown; accepted: boolean }[];
  keyed: { what: string; sample: unknown }[];
  stateFields: string[];
  stateTable: unknown;
  preRunStages: Record<string, string[]>;
  grid: { kind: string; state: string; base: string; field: string; present: boolean; sample: unknown; accepted: boolean }[];
  dropped: { what: string; sample: unknown; accepted: boolean }[];
}

const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as JobFixture;
/** A parse that throws instead of refusing fails here by assertion. */
const parse = (record: unknown) => {
  let result: ReturnType<typeof UploadJobSchema.safeParse> | undefined;
  expect(() => {
    result = UploadJobSchema.safeParse(record);
  }).not.toThrow();
  return result!;
};
const messagesOf = (record: unknown): string[] => {
  const result = parse(record);
  return result.success ? [] : result.error.issues.map((issue) => issue.message);
};

describe('@albedian/contracts: the upload job record', () => {
  it('shares its patterns, lists, stage table, limits and messages with the Pydantic mirror', () => {
    expect(fixture.patterns).toEqual(JOB_PATTERNS);
    expect(fixture.enums).toEqual({
      kinds: [...JOB_KINDS],
      states: [...UPLOAD_JOB_STATES],
      stages: [...UPLOAD_JOB_STAGES],
      units: [...JOB_PROGRESS_UNITS],
      passLabels: [...JOB_PASS_LABELS],
      solveFailureSentences: [...SOLVE_FAILURE_SENTENCES],
    });
    expect(fixture.stageUnits).toEqual(JOB_STAGE_UNITS);
    expect(fixture.limits).toEqual({ passMax: JOB_PASS_MAX, spanMaxSec: JOB_SPAN_MAX_SEC });
    expect(fixture.messages).toEqual(Object.values(JOB_MESSAGES));
    expect(SOLVE_FAILURE_SENTENCES).toContain(INTERRUPTED_SENTENCE);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    for (const record of fixture.valid) {
      const parsed = parse(record);
      expect(parsed.success, JSON.stringify(parsed.success ? '' : parsed.error.issues)).toBe(true);
      expect(parsed.success && parsed.data).toEqual(record);
    }
  });

  it('refuses every fault with the message the Python side gives', () => {
    for (const fault of fixture.invalid) {
      expect(messagesOf(fault.sample), fault.reason).toContain(fault.message);
    }
  });

  it('gives every edge sample the verdict the Python side gives', () => {
    for (const edge of fixture.edges) {
      expect(parse(edge.sample).success, `${edge.what}: ${JSON.stringify(edge.sample)}`).toBe(edge.accepted);
    }
  });

  it('reads what each state may and must hold from one table, the same as the Python side', () => {
    expect(fixture.stateTable).toEqual(JOB_STATE_TABLE);
    expect(fixture.stateFields).toEqual([...JOB_STATE_FIELDS]);
    expect(fixture.preRunStages).toEqual(JOB_PRE_RUN_STAGES);
    expect(new Set(JOB_STATE_FIELDS).size).toBe(JOB_STATE_FIELDS.length);
    expect(new Set(JOB_STATE_FIELDS)).toEqual(new Set([...UPLOAD_JOB_OPTIONAL_FIELDS, 'workStages']));
    for (const rows of Object.values(JOB_STATE_TABLE)) expect(Object.keys(rows)).toEqual([...UPLOAD_JOB_STATES]);
    const letters = JOB_STATE_FIELDS.map(() => 'E').join(' ');
    expect(jobStateRow(letters, 'none').fields).toEqual(Object.fromEntries(JOB_STATE_FIELDS.map((f) => [f, 'either'])));
    expect(() => jobStateRow(`${letters} E`, 'none')).toThrow('state row needs');
    expect(() => jobStateRow(letters.slice(0, -2), 'none')).toThrow('state row needs');
  });

  it('gives every state against every field, present and absent, the verdict the Python side gives', () => {
    expect(fixture.grid.length).toBe(JOB_KINDS.length * UPLOAD_JOB_STATES.length * JOB_STATE_FIELDS.length * 2 * 2);
    const wrong = fixture.grid.filter((cell) => parse(cell.sample).success !== cell.accepted)
      .map((cell) => `${cell.kind} ${cell.state} ${cell.base} ${cell.field} ${cell.present}`);
    expect(wrong).toEqual([]);
  });

  it('gives every key dropped from every valid record the verdict the Python side gives', () => {
    expect(fixture.dropped.length).toBeGreaterThan(200);
    const wrong = fixture.dropped.filter((cell) => parse(cell.sample).success !== cell.accepted).map((cell) => cell.what);
    expect(wrong).toEqual([]);
  });

  it('refuses an unknown key and a Python-named key at every level of every record', () => {
    expect(fixture.keyed.length).toBeGreaterThan(100);
    for (const keyed of fixture.keyed) {
      expect(parse(keyed.sample).success, keyed.what).toBe(false);
    }
  });
});
