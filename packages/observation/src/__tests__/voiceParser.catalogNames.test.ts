import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser.js';
import { CATALOG_STAR_NAMES } from '../generated/catalogStarNames.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * Every star name the backend knows (services/voice/names.py: the pinned catalog's names, all 410 IAU-CSN names,
 * their ASCII forms and the spoken aliases), read by the app's own parser through the generated table. A named
 * star resolves by the finding id the solver gives it, so a title that differs from the IAU name still resolves;
 * a named star the sky solution lacks is refused, and the selection is never taken in its place.
 */

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();
const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;

/** A finding with no name of its own: no catalogRef, a title no star is called. */
function plain(id: string): CelestialFinding {
  const { catalogRef: _dropped, ...rest } = structuredClone(template);
  return { ...rest, id, title: 'Unnamed catalog star' } as CelestialFinding;
}

const SELECTED = 'finding-selected-made-up';

function sessionWith(ids: string[]): ObservationSession {
  const findings = [plain(SELECTED), ...ids.map(plain)];
  return {
    ...base,
    selectedFindingId: SELECTED,
    observationSolution: { ...base.observationSolution!, findings },
  };
}

function parse(utterance: string, session: ObservationSession) {
  return parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
}

describe('@albedian/observation voice parser: every catalog star name', () => {
  it('holds the backend star table: names, aliases and ASCII forms, in lower case', () => {
    expect(CATALOG_STAR_NAMES.length).toBeGreaterThanOrEqual(410);
    const spoken = CATALOG_STAR_NAMES.map(([name]) => name);
    expect(new Set(spoken).size).toBe(spoken.length);
    for (const name of spoken) expect(name).toBe(name.toLowerCase());
    const byName = new Map(CATALOG_STAR_NAMES.map(([name, id, shown]) => [name, [id, shown]]));
    expect(byName.get('canopus')).toEqual(['finding-hip-30438', 'Canopus']);
    expect(byName.get('alpha centauri')).toEqual(['finding-hip-71683', 'Rigil Kentaurus']);
    expect(byName.get('polaris')?.[1]).toBe('Polaris');
  });

  it.each(CATALOG_STAR_NAMES.map(([name, id, shown]) => [name, id, shown]))(
    '"%s" selects its own finding (%s) when the solution holds it, and is refused when it does not',
    (name, id, shown) => {
      const holding = sessionWith([id]);
      for (const verb of ['select {}', 'take me to {}']) {
        const said = verb.replace('{}', name);
        const result = parse(said, holding);
        expect((result.action as { findingId?: string } | undefined)?.findingId, said).toBe(id);
      }

      const lacking = sessionWith([]);
      for (const verb of ['select {}', 'what is {}', 'take me to {}']) {
        const said = verb.replace('{}', name);
        const result = parse(said, lacking);
        expect(result.action, said).toBeUndefined();
        expect(result.reason, said).toBe('ambiguous_target');
        expect(result.error, said).toBe(`${shown} was not found in this video's sky.`);
      }
    }
  );

  it('reads a name only as a whole word, so "orange" does not name Ran', () => {
    const result = parse('select the orange one', sessionWith([]));
    expect(result.error ?? '').not.toMatch(/\bRan\b/);
  });

  it('keeps a "how many" question that names a star a question, as the backend reads it', () => {
    expect(parse('how many years ago did the light leave Polaris?', sessionWith([])).action?.type).toBe('question.ask');
  });

  it('says the sky is unresolved, not that the star is missing, when no star is placed yet', () => {
    const lacking = sessionWith([]);
    const unresolved: ObservationSession = { ...lacking, machines: { ...lacking.machines, calibration: 'unresolved' } };
    const result = parse('take me to Polaris', unresolved);
    expect(result.reason).toBe('unresolved_sky');
    expect(result.action).toBeUndefined();
  });

  it('reads the longest name first, so a two-word name is not read as its last word', () => {
    const rows = new Map(CATALOG_STAR_NAMES.map(([name, id]) => [name, id]));
    const kentaurus = rows.get('rigil kentaurus')!;
    expect(parse('select rigil kentaurus', sessionWith([kentaurus])).action).toMatchObject({ findingId: kentaurus });
    // Polaris and Polaris Australis are both names; with both held, the longer one said is the one taken
    const polaris = rows.get('polaris')!;
    const australis = rows.get('polaris australis')!;
    expect(parse('select polaris australis', sessionWith([polaris, australis])).action).toMatchObject({ findingId: australis });
    expect(parse('select polaris', sessionWith([polaris, australis])).action).toMatchObject({ findingId: polaris });
  });
});
