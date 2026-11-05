import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  AlbedianActionSchema,
  FINDING_ACTION_TYPES,
  RejectedOutcomeSchema,
  VoiceCommandResponseSchema,
} from '../index.js';

/**
 * The voice command reply against the samples the Python mirror wrote (`services/contracts/voice.py`,
 * `tests/fixtures/voice_contract.json`): both sides must accept, refuse and strip exactly the same records.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/voice_contract.json');

type SchemaName = 'AlbedianAction' | 'VoiceCommandResponse';

interface VoiceFixture {
  enums: { replyStatuses: string[]; rejectionReasons: string[]; actionTypes: string[]; findingActionTypes: string[] };
  limits: {
    questionMaxUtf16: number;
    epochIsoMinUtf16: number;
    epochMinYearCE: number;
    epochMaxYearCE: number;
    fractionMin: number;
    fractionMax: number;
  };
  valid: Record<SchemaName, unknown[]>;
  invalid: { schema: SchemaName; sample: unknown; reason: string }[];
  stripped: { schema: SchemaName; sample: unknown; parsed: unknown }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as VoiceFixture;
const SCHEMAS = { AlbedianAction: AlbedianActionSchema, VoiceCommandResponse: VoiceCommandResponseSchema };

type Reply = Record<string, unknown> & { status: string; proposedAction?: Record<string, unknown> };
const replies = fixture.valid.VoiceCommandResponse as Reply[];
const proposed = replies.filter((reply) => reply.status === 'proposed');
const actions = fixture.valid.AlbedianAction as (Record<string, unknown> & { type: string; requestId: string })[];

function parses(schema: SchemaName, record: unknown): boolean {
  return SCHEMAS[schema].safeParse(record).success;
}

describe('@albedian/contracts: the voice command reply', () => {
  it('shares its statuses, reasons and action types with the Pydantic mirror', () => {
    const replyStatuses = VoiceCommandResponseSchema.innerType().options.map((option) => option.shape.status.value);
    const actionTypes = AlbedianActionSchema.innerType().options.map((option) => option.shape.type.value);
    expect(fixture.enums.replyStatuses).toEqual(replyStatuses);
    expect(fixture.enums.rejectionReasons).toEqual([...RejectedOutcomeSchema.shape.reason.options]);
    expect(fixture.enums.actionTypes).toEqual(actionTypes);
    expect(fixture.enums.findingActionTypes).toEqual([...FINDING_ACTION_TYPES]);
    expect(replyStatuses).toEqual(['proposed', 'needs_input', 'rejected']);
  });

  it('holds the limits the Python mirror states', () => {
    const { limits } = fixture;
    const ask = (question: string) => ({ type: 'question.ask', requestId: 'req', expectedViewRevision: 0, question });
    expect(parses('AlbedianAction', ask('a'.repeat(limits.questionMaxUtf16)))).toBe(true);
    expect(parses('AlbedianAction', ask('a'.repeat(limits.questionMaxUtf16 + 1)))).toBe(false);
    const epoch = (epochISO: string, yearCE: number) => ({ type: 'space.epoch', requestId: 'req', expectedViewRevision: 0, epochISO, yearCE });
    expect(parses('AlbedianAction', epoch('x'.repeat(limits.epochIsoMinUtf16), 1))).toBe(true);
    expect(parses('AlbedianAction', epoch('x'.repeat(limits.epochIsoMinUtf16 - 1), 1))).toBe(false);
    for (const [year, ok] of [
      [limits.epochMinYearCE, true],
      [limits.epochMaxYearCE, true],
      [limits.epochMinYearCE - 1, false],
      [limits.epochMaxYearCE + 1, false],
    ] as const) {
      expect(parses('AlbedianAction', epoch('2000-01-01T00:00:00Z', year))).toBe(ok);
    }
    const back = (fraction: number) => ({ type: 'history.back', requestId: 'req', expectedViewRevision: 0, mode: 'partial', fraction });
    expect(parses('AlbedianAction', back(limits.fractionMin))).toBe(true);
    expect(parses('AlbedianAction', back(limits.fractionMax))).toBe(true);
    expect(parses('AlbedianAction', back(limits.fractionMin - 0.001))).toBe(false);
    expect(parses('AlbedianAction', back(limits.fractionMax + 0.001))).toBe(false);
  });

  it('parses every record the Python side wrote, unchanged', () => {
    for (const [name, records] of Object.entries(fixture.valid) as [SchemaName, unknown[]][]) {
      expect(records.length).toBeGreaterThan(0);
      for (const record of records) {
        const parsed = SCHEMAS[name].safeParse(record);
        expect(parsed.error?.issues ?? [], `${name}: ${JSON.stringify(record)}`).toEqual([]);
        expect(parsed.data).toEqual(record);
      }
    }
    // Every reply status and every rejection reason has a sample
    expect(new Set(replies.map((reply) => reply.status))).toEqual(new Set(fixture.enums.replyStatuses));
    const reasons = replies.filter((reply) => reply.status === 'rejected').map((reply) => reply.reason);
    expect(new Set(reasons)).toEqual(new Set(fixture.enums.rejectionReasons));
  });

  it('refuses every record the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(40);
    for (const { schema, sample, reason } of fixture.invalid) {
      expect(parses(schema, sample), `${schema} should refuse: ${reason}`).toBe(false);
    }
  });

  it('drops the unknown keys the Python side drops, and nothing else', () => {
    expect(fixture.stripped.length).toBeGreaterThan(0);
    for (const { schema, sample, parsed } of fixture.stripped) {
      expect(SCHEMAS[schema].safeParse(sample).data).toEqual(parsed);
    }
  });

  it('PBT: a proposed reply that carries a state version is refused, not stripped', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...proposed),
        fc.constantFrom('newStateVersion', 'settledStateVersion'),
        fc.jsonValue(),
        (reply, key, value) => !parses('VoiceCommandResponse', { ...reply, [key]: value })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[proposed[0]!, 'newStateVersion', 5]] }
    );
  });

  it('PBT: only a finding action is described, and a reply names its action by requestId', () => {
    const findings = FINDING_ACTION_TYPES as readonly string[];
    fc.assert(
      fc.property(fc.constantFrom(...actions), fc.string({ minLength: 1 }), (action, text) => {
        const reply = {
          status: 'proposed',
          actionId: action.requestId,
          proposedAction: action,
          visualStatus: `Checked ${action.type}; not applied`,
        };
        expect(parses('VoiceCommandResponse', reply)).toBe(true);
        expect(parses('VoiceCommandResponse', { ...reply, scientificExplanation: text })).toBe(findings.includes(action.type));
        expect(parses('VoiceCommandResponse', { ...reply, actionId: `${action.requestId}-other` })).toBe(false);
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: only an invalid_action refusal lists issues, and no refusal carries an action', () => {
    const refusal = { status: 'rejected', actionId: 'req', explanation: 'Made up.', nextActions: [] };
    const issue = fc.record({
      path: fc.array(fc.oneof(fc.string(), fc.nat()), { maxLength: 4 }),
      message: fc.string({ minLength: 1 }),
    });
    fc.assert(
      fc.property(
        fc.constantFrom(...fixture.enums.rejectionReasons),
        fc.option(fc.array(issue, { minLength: 1, maxLength: 3 }), { nil: undefined }),
        fc.constantFrom(...actions),
        (reason, issues, action) => {
          const reply = { ...refusal, reason, ...(issues ? { issues } : {}) };
          expect(parses('VoiceCommandResponse', reply)).toBe((reason === 'invalid_action') === (issues !== undefined));
          expect(parses('VoiceCommandResponse', { ...reply, proposedAction: action })).toBe(false);
        }
      ),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: no reply status the backend never sends is accepted', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...replies),
        fc.string().filter((status) => !fixture.enums.replyStatuses.includes(status)),
        (reply, status) => !parses('VoiceCommandResponse', { ...reply, status })
      ),
      { seed: SEED, numRuns: NUM_RUNS, examples: [[replies[0]!, 'completed'], [replies[0]!, 'canceled']] }
    );
  });
});
