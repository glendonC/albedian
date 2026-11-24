import { describe, expect, it, vi, afterEach } from 'vitest';
import { VoiceQuestionReplySchema, type AnswerStatement, type ObservationSession, type ObservationSolution } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { askVoiceAnswer, isQuestion } from '../../services/voiceAnswer.js';
import { statementLine, valueWords } from '../answerWords.js';

// Asking a question (ask, approved 2026-11-12): the words go to the answer route, whose reply is the contract's; each
// statement shows its evidence tag, and a value line only for the kind the approved picture words ("7.68 parsecs ·
// Hipparcos 2007 parallax"), until the design lane words the rest.

const SHA = '2b38f0f6006832a38206946de87acf8d0c140f38d0a884b2985d16f62e35fa9d';

/** The synthetic calibrated photo, its first catalog matched star given a catalog distance from a parallax. */
function photo(method: 'geometric_parallax' | 'photogeometric_bailer_jones_edr3' = 'geometric_parallax') {
  const base = createSyntheticCalibratedSession();
  const solution = base.observationSolution!;
  const index = solution.findings.findIndex((f) => f.evidenceClass === 'catalog_matched' && f.catalogRef);
  const star = solution.findings[index]!;
  const findings = solution.findings.map((f, i) =>
    i === index
      ? { ...f, id: 'finding-hip-91262', catalogRef: { ...f.catalogRef!, releaseVersion: 'Hipparcos 2007', bailerJonesDistancePc: { lower: 7.6, median: 7.6787, upper: 7.75, method } } }
      : f
  );
  const session = { ...base, observationSolution: { ...solution, id: 'sol-synthetic-1', findings } as ObservationSolution } as ObservationSession;
  return { session, index, star: { ...star, id: 'finding-hip-91262' } };
}

function distanceStatement(index: number, amount = 7.6787): AnswerStatement {
  return {
    subject: { kind: 'finding', findingId: 'finding-hip-91262', evidenceClass: 'catalog_matched' },
    quantity: 'distance',
    value: { kind: 'number', amount, unit: 'pc' },
    evidenceClass: 'catalog_matched',
    source: { kind: 'solution_field', path: ['findings', index, 'catalogRef', 'bailerJonesDistancePc', 'median'] },
  } as AnswerStatement;
}

describe('statementLine: the small line under an answer, in the approved form "value unit · source"', () => {
  it('writes a catalog distance from a parallax as the approved picture does: three figures, parsecs, the release and parallax', () => {
    const { session, index } = photo();
    expect(statementLine(distanceStatement(index), session.observationSolution)).toBe('7.68 parsecs · Hipparcos 2007 parallax');
    expect(statementLine(distanceStatement(index, 98.71883010683966), session.observationSolution)).toBe('98.7 parsecs · Hipparcos 2007 parallax');
  });

  it('writes a distance from another method by its catalog alone, and no line for a path to no star or another solution', () => {
    const bj = photo('photogeometric_bailer_jones_edr3');
    expect(statementLine(distanceStatement(bj.index), bj.session.observationSolution)).toBe('7.68 parsecs · Hipparcos 2007');
    const { session, index } = photo();
    expect(statementLine(distanceStatement(index + 100000), session.observationSolution)).toBeNull();
    expect(statementLine(distanceStatement(index), undefined)).toBeNull();
    // The statement names another finding than the row the path reaches
    const other = { ...distanceStatement(index), subject: { kind: 'finding', findingId: 'finding-hip-1', evidenceClass: 'catalog_matched' } } as AnswerStatement;
    expect(statementLine(other, session.observationSolution)).toBeNull();
  });

  it('writes every other statement as C7 decides: its value in its unit written out once, then its source in two or three words', () => {
    const { session, index } = photo();
    const sol = session.observationSolution;
    const at = (over: Partial<AnswerStatement>) => statementLine({ ...distanceStatement(index), ...over } as AnswerStatement, sol);
    expect(at({ quantity: 'apparent_magnitude', value: { kind: 'number', amount: 0.03, unit: 'mag' }, source: { kind: 'solution_field', path: ['findings', index, 'catalogRef', 'apparentMagnitude'] } } as Partial<AnswerStatement>)).toBe('magnitude 0.03 · Hipparcos 2007');
    expect(
      at({
        subject: { kind: 'solution' },
        quantity: 'matched_star_count',
        value: { kind: 'number', amount: 1982, unit: 'count' },
        evidenceClass: 'observed',
        source: { kind: 'solution_field', path: ['quality', 'matchedInlierCount'] },
      } as Partial<AnswerStatement>)
    ).toBe("1,982 · this photo's solve");
    expect(
      at({ quantity: 'detection_snr', value: { kind: 'number', amount: 214.4, unit: 'ratio' }, evidenceClass: 'observed', source: { kind: 'solution_field', path: ['findings', index, 'sourceDetection', 'snr'] } } as Partial<AnswerStatement>)
    ).toBe("214 times · this photo's pixels");
    expect(at({ quantity: 'altitude', value: { kind: 'number', amount: 45.25, unit: 'deg' }, source: { kind: 'solution_field', path: ['findings', index, 'skyPosition', 'altDeg'] } } as Partial<AnswerStatement>)).toBe(
      "45.3 degrees · this photo's solve"
    );
    expect(at({ subject: { kind: 'ephemeris_body', body: 'mars' }, quantity: 'distance', value: { kind: 'number', amount: 1.52, unit: 'au' }, evidenceClass: 'predicted', source: { kind: 'ephemeris', body: 'mars' } } as unknown as Partial<AnswerStatement>)).toBe(
      '1.52 astronomical units · ERFA ephemeris'
    );
    // A path that is no source of that quantity, or a source no words are decided for, writes no line
    expect(at({ source: { kind: 'solution_field', path: ['findings', index, 'catalogRef', 'parallaxMas', 'median'] } } as Partial<AnswerStatement>)).toBeNull();
    expect(at({ value: { kind: 'text', text: 'Vega' } } as unknown as Partial<AnswerStatement>)).toBeNull();
  });
});

describe('valueWords: each unit written out once (C7)', () => {
  it('writes each stored unit as decided', () => {
    expect(valueWords(7.6787, 'pc')).toBe('7.68 parsecs');
    expect(valueWords(1.524, 'au')).toBe('1.52 astronomical units');
    expect(valueWords(0.03, 'mag')).toBe('magnitude 0.03');
    expect(valueWords(5778, 'K')).toBe('5,780 kelvin');
    expect(valueWords(45.25, 'deg')).toBe('45.3 degrees');
    expect(valueWords(3.2, 'arcsec')).toBe('3.2 arcseconds');
    expect(valueWords(1.2, 'px')).toBe('1.2 pixels');
    expect(valueWords(0.25, 'fraction')).toBe('0.25 of 1');
    expect(valueWords(31371, 'count')).toBe('31,371');
    expect(valueWords(214.4, 'ratio')).toBe('214 times');
  });
});

describe('isQuestion: which words go to the answer route', () => {
  it('sends a question about a star, and words no command reads, and keeps commands for the app', () => {
    const { session, star } = photo();
    expect(isQuestion(`How far away is ${star.title}?`, session)).toBe(true);
    expect(isQuestion('Is that a plane?', session)).toBe(true);
    expect(isQuestion(`Take me to ${star.title}`, session)).toBe(false);
    expect(isQuestion('Back to moment', session)).toBe(false);
    // A question the parser has no rule for answering is still a question; a name it cannot find is a command
    expect(isQuestion('What is the second brightest star?', session)).toBe(true);
    expect(isQuestion('Take me to Zorblax', session)).toBe(false);
  });

  it('keeps a command the parser refuses in its own typed words, so that sentence shows and not the route\'s (deepsky-answer-1)', () => {
    const { session } = photo();
    // No table holds these by name: the parser's no_named_row refusal is for a command, never a question
    for (const words of ['select Eta Carinae', 'take me to the Magellanic Clouds', 'tell me about Eta Carinae']) {
      expect(isQuestion(words, session), words).toBe(false);
    }
    // A question no command reads still goes to the route; a refused edit, typed or not, stays with the app
    expect(isQuestion('Is that a plane?', session)).toBe(true);
    expect(isQuestion('add a nebula over the field', session)).toBe(false);
  });
});

describe('askVoiceAnswer: the answer route, checked both ways', () => {
  afterEach(() => vi.unstubAllGlobals());
  const reply = { heard: 'Is that a plane?', answer: { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence: "Nothing in this photo's data answers that." } };

  it('posts the words as heard and the picked star to the upload named by its sha256, and returns the reply the contract accepts', async () => {
    expect(VoiceQuestionReplySchema.safeParse(reply).success).toBe(true);
    const fetch = vi.fn(async () => new Response(JSON.stringify(reply), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    await expect(askVoiceAnswer(SHA, 'Is that a plane?', 'finding-hip-91262')).resolves.toEqual(reply);
    const [url, init] = fetch.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe(`/api/voice/answer/${SHA}`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ question: 'Is that a plane?', selectedFindingId: 'finding-hip-91262' });
    await askVoiceAnswer(SHA, 'Is that a plane?', null);
    expect(JSON.parse(String((fetch.mock.calls[1] as unknown as [string, RequestInit])[1].body))).toEqual({ question: 'Is that a plane?' });
  });

  it('asks nothing for an address that is not 64 lowercase hex, or words the contract refuses', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(reply), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    for (const sha of ['', SHA.toUpperCase(), `${SHA}0`, '../ingest', SHA.slice(1)]) {
      await expect(askVoiceAnswer(sha, 'Is that a plane?', null)).resolves.toBeNull();
    }
    await expect(askVoiceAnswer(SHA, 'x'.repeat(501), null)).resolves.toBeNull();
    await expect(askVoiceAnswer(SHA, 'Is that a plane?', 'not a finding id')).resolves.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('returns nothing for an error status, a reply the contract refuses, or no answer at all', async () => {
    for (const answer of [
      async () => new Response('{}', { status: 500 }),
      // An error status, whatever its body holds
      async () => new Response(JSON.stringify(reply), { status: 500 }),
      async () => new Response(JSON.stringify({ heard: 'x', answer: { status: 'answered' } }), { status: 200 }),
      async () => new Response('<html>', { status: 200 }),
      async () => {
        throw new TypeError('failed to fetch');
      },
    ]) {
      vi.stubGlobal('fetch', vi.fn(answer));
      await expect(askVoiceAnswer(SHA, 'Is that a plane?', null)).resolves.toBeNull();
    }
  });
});
