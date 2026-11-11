import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser.js';
import { CATALOG_STAR_NAMES, EVERYDAY_WORD_NAMES } from '../generated/catalogStarNames.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * Star names that are also everyday English words, in the app's parser (the same sentences as
 * tests/integration/test_voice_everyday_names.py; both read the boundary, separator and spaced rows from
 * tests/fixtures/everyday_name_rows.json). Such a word names a star only after a naming verb or with "star"
 * beside it, whether it would match the generated name table or a finding's own title, so "I ran" never selects
 * epsilon Eridani even when the photo holds it. A cued name is a star like any other: selected when the photo holds
 * it, refused when it does not.
 *
 * A space between the naming verb, "the", "star" and the name is what JavaScript's /\s/ matches, on both sides, so
 * both parsers read the same phrases: U+FEFF is one, and U+0085 and U+001C to U+001F (which Python's \s takes) are not.
 *
 * Known behaviour, kept by the lead's decision and not asserted here [tested]: "what is" and "what's" are naming
 * verbs, so that "what is atlas" describes Atlas. The cost is that "what is the fang shaped cloud" names Fang and
 * "what's the peacock doing" names Peacock.
 */

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();
const template = base.observationSolution!.findings.find((f) => f.id === 'finding_rigel')!;
const TABLE = new Map(CATALOG_STAR_NAMES.map(([spoken, id, shown]) => [spoken, { id, shown }]));
const SELECTED = 'finding_betelgeuse';

// The boundary, separator and spaced rows, which the backend's test reads too
const ROWS_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/everyday_name_rows.json');
interface EverydayNameRows {
  boundary: Array<[string, string]>;
  separators: Array<[string, string, boolean]>;
  spaced: Array<[string, string]>;
}
const ROWS = JSON.parse(fs.readFileSync(ROWS_FIXTURE, 'utf8')) as EverydayNameRows;

/** The synthetic session with Betelgeuse selected, plus the named stars as the solver titles them. */
function withStars(words: string[]): ObservationSession {
  const extra = words.map((word): CelestialFinding => {
    const { catalogRef: _dropped, ...rest } = structuredClone(template);
    return { ...rest, id: TABLE.get(word)!.id, title: TABLE.get(word)!.shown } as CelestialFinding;
  });
  return {
    ...base,
    selectedFindingId: SELECTED,
    observationSolution: { ...base.observationSolution!, findings: [...base.observationSolution!.findings, ...extra] },
  };
}

function parse(utterance: string, session: ObservationSession) {
  return parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
}

function targetOf(utterance: string, session: ObservationSession): string | undefined {
  const action = parse(utterance, session).action;
  return action && 'findingId' in action ? action.findingId : undefined;
}

const EVERYDAY = ['ran', 'fang', 'atlas', 'sham', 'skat', 'peacock', 'diadem', 'axolotl'];

describe('@albedian/observation voice parser: star names that are everyday words', () => {
  it('holds the reviewed set of eight, each a name in the table', () => {
    expect([...EVERYDAY_WORD_NAMES].sort()).toEqual([...EVERYDAY].sort());
    for (const word of EVERYDAY) expect(TABLE.has(word), word).toBe(true);
  });

  it.each([[[]], [['ran', 'fang']]] as Array<[string[]]>)(
    'reads the auditor sentences as no star (photo holding %j)',
    (held) => {
      const session = withStars(held);
      for (const utterance of ['zoom in, I ran out of room', 'show the fang shaped cloud']) {
        const result = parse(utterance, session);
        expect(result.error ?? '', utterance).not.toMatch(/\b(Ran|Fang) was not found/);
        expect(targetOf(utterance, session), utterance).not.toBe(TABLE.get('ran')!.id);
        expect(targetOf(utterance, session), utterance).not.toBe(TABLE.get('fang')!.id);
      }
      expect(targetOf('I ran the solve again, select that one', session)).toBe(SELECTED);
    }
  );

  it.each([
    ['select ran', 'ran'],
    ['tell me about the star ran', 'ran'],
    ['where is fang', 'fang'],
    ['take me to the peacock', 'peacock'],
    ['select the atlas star', 'atlas'],
    ['focus on star diadem', 'diadem'],
    ['what is atlas', 'atlas'],
    ['select the star fang', 'fang'],
    ['the star fang, select it', 'fang'],
    ['the atlas star, select it', 'atlas'],
  ])('"%s" names its star: selected when the photo holds it, refused when it does not', (utterance, word) => {
    expect(targetOf(utterance, withStars([word]))).toBe(TABLE.get(word)!.id);
    const lacking = parse(utterance, withStars([]));
    expect(lacking.action).toBeUndefined();
    expect(lacking.error).toBe(`${TABLE.get(word)!.shown} was not found in this video's sky.`);
  });

  it.each(EVERYDAY)('never takes "%s" as a star without a naming verb or the word star', (word) => {
    const session = withStars([word]);
    for (const utterance of [`show the ${word}`, `${word} out of room`, `i said ${word}`, `the ${word} was there`]) {
      expect(targetOf(utterance, session), utterance).not.toBe(TABLE.get(word)!.id);
    }
  });

  it.each(ROWS.boundary)(
    '"%s" names no star: the naming verb, the name or "star" runs on into a longer word',
    (utterance, word) => {
      const { id, shown } = TABLE.get(word)!;
      for (const said of [utterance, `${utterance}, select it`]) {
        expect(targetOf(said, withStars([word])), said).not.toBe(id);
        expect(parse(said, withStars([])).error ?? '', said).not.toContain(`${shown} was not found`);
      }
    }
  );

  // [label, space, whether JavaScript's /\s/ takes it]
  const SEPARATORS = ROWS.separators;
  // [utterance with {s} for the space, the everyday word]: the space between the cue and the name or between "star"
  // and the name. Each asks to select, which both parsers read as finding.select when the word is cued or "it" points.
  const SPACED = ROWS.spaced;

  it.each(SEPARATORS)('reads %s around a cued name as the backend does', (_label, space, separates) => {
    for (const [template, word] of SPACED) {
      const utterance = template.replaceAll('{s}', space);
      const said = JSON.stringify(utterance);
      if (!separates && !utterance.includes('select it')) {
        // Not cued, so the word names no star, and nothing points: refused, never read as the selection
        for (const session of [withStars([word]), withStars([])]) {
          const refused = parse(utterance, session);
          expect([refused.action, refused.reason], said).toEqual([undefined, 'ambiguous_target']);
        }
        continue;
      }
      expect(parse(utterance, withStars([word])).action?.type, said).toBe('finding.select');
      // Cued, the star; not cued, what "it" points at
      expect(targetOf(utterance, withStars([word])), said).toBe(separates ? TABLE.get(word)!.id : SELECTED);
      const lacking = parse(utterance, withStars([]));
      if (separates) {
        expect(lacking.error, said).toBe(`${TABLE.get(word)!.shown} was not found in this video's sky.`);
      } else {
        expect(lacking.action?.type, said).toBe('finding.select');
        expect(targetOf(utterance, withStars([])), said).toBe(SELECTED);
      }
    }
  });

  it('keeps a famous star name that is also a word a plain name', () => {
    expect(targetOf('what is spica', withStars(['spica']))).toBe(TABLE.get('spica')!.id);
    expect(parse('what is pollux', withStars([])).error).toBe("Pollux was not found in this video's sky.");
  });
});
