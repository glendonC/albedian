import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  answerFindings,
  ANSWER_TEXT_MAX_UTF16,
  FINDING_ID_PATTERN,
  QUESTION_HEARD_MAX_UTF16,
  VoiceQuestionReplySchema,
  VoiceQuestionRequestSchema,
  VoiceAnswerSchema,
} from '../index.js';

/**
 * The answer route's request and reply (POST /api/voice/answer/{sha256}), judged on the samples the Python mirror
 * writes (tests/fixtures/voice_question_contract.json, by tests/integration/test_voice_question_contract.py): every
 * valid record parses to itself, every invalid one is refused, and the limits are the same on both sides.
 */
const FIXTURE = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/voice_question_contract.json'), 'utf8')
) as {
  limits: { heardMaxUtf16: number; findingIdMaxUtf16: number; findingIdPattern: string };
  edges: { schema: string; base: Record<string, unknown>; field: string; inside: unknown[]; outside: unknown[] }[];
  valid: Record<string, unknown[]>;
  invalid: { schema: string; sample: unknown; reason: string }[];
  wordsAsHeard: { kind: string; words: string }[];
  about: { what: string; answer: unknown; findings: string[] }[];
};
const SCHEMAS = { VoiceQuestionRequest: VoiceQuestionRequestSchema, VoiceQuestionReply: VoiceQuestionReplySchema } as const;
const schema = (name: string) => SCHEMAS[name as keyof typeof SCHEMAS];

describe('@albedian/contracts: the answer route', () => {
  it('holds the limits the backend holds', () => {
    expect(FIXTURE.limits).toEqual({ heardMaxUtf16: QUESTION_HEARD_MAX_UTF16, findingIdMaxUtf16: ANSWER_TEXT_MAX_UTF16, findingIdPattern: FINDING_ID_PATTERN.source });
  });

  it('parses every valid record to itself', () => {
    for (const [name, records] of Object.entries(FIXTURE.valid)) {
      expect(records.length, name).toBeGreaterThan(0);
      for (const record of records) expect(schema(name).parse(record)).toEqual(record);
    }
  });

  it('refuses every invalid record', () => {
    expect(FIXTURE.invalid.length).toBe(74);
    for (const { schema: name, sample, reason } of FIXTURE.invalid) expect(schema(name).safeParse(sample).success, reason).toBe(false);
  });

  it('takes each edge inside its bounds and refuses it just outside', () => {
    for (const edge of FIXTURE.edges) {
      for (const inside of edge.inside) expect(schema(edge.schema).safeParse({ ...edge.base, [edge.field]: inside }).success, `${edge.field} ${String(inside)}`).toBe(true);
      for (const outside of edge.outside) expect(schema(edge.schema).safeParse({ ...edge.base, [edge.field]: outside }).success, `${edge.field} ${String(outside)}`).toBe(false);
    }
  });

  it('gives back the words as heard unit for unit, in the request and in the reply', () => {
    expect(FIXTURE.wordsAsHeard.length).toBe(6);
    const answer = (FIXTURE.valid.VoiceQuestionReply![0] as { answer: unknown }).answer;
    for (const { kind, words } of FIXTURE.wordsAsHeard) {
      const question = VoiceQuestionRequestSchema.parse({ question: words }).question;
      const heard = VoiceQuestionReplySchema.parse({ heard: words, answer }).heard;
      expect([question.length, heard.length], kind).toEqual([words.length, words.length]);
      expect([...question].map((c) => c.codePointAt(0)), kind).toEqual([...words].map((c) => c.codePointAt(0)));
      expect(heard === words && question === words, kind).toBe(true);
    }
  });

  it('says which findings an answer is about: none, one or several, and none for a refusal', () => {
    const counts = new Set(FIXTURE.about.map((c) => `${(c.answer as { status: string }).status} ${c.findings.length}`));
    expect([...counts].sort()).toEqual(['answered 0', 'answered 1', 'answered 2', 'answered 3', 'unanswerable 0']);
    for (const { what, answer, findings } of FIXTURE.about) {
      expect(answerFindings(VoiceAnswerSchema.parse(answer)), what).toEqual(findings);
    }
  });

  it('keeps a finding named again at the place it first comes', () => {
    const again = FIXTURE.about.filter((c) => c.what.startsWith('named again'));
    expect(again.map((c) => c.what)).toEqual(['named again: A, B, A', 'named again: A, B, B, A', 'named again: A, B, C, B']);
    for (const { what, answer, findings } of again) {
      const parsed = VoiceAnswerSchema.parse(answer);
      const named = parsed.status === 'answered' ? parsed.statements.flatMap((s) => (s.subject.kind === 'finding' ? [s.subject.findingId] : [])) : [];
      const firstPlaces = named.filter((id, index) => named.indexOf(id) === index);
      expect(named.length, what).toBeGreaterThan(firstPlaces.length);
      expect([findings, answerFindings(parsed)], what).toEqual([firstPlaces, firstPlaces]);
    }
  });
});
