import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction, SOUTHERN_CROSS_STARS, unbakedRefusal } from '../voiceParser.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * Names a stargazer says under a southern sky, read as services/voice/names.py reads them
 * (tests/integration/test_voice_southern_names.py): a name or alias resolves only to a solved finding whose
 * title is that IAU name; a southern object voice cannot reach yet, or a named star the solution lacks, is
 * refused and never falls through to the selection; the Southern Cross asks which of its stars.
 * The ids are those the phone-photo solver gives these stars (finding-hip-<HIP> from names.json).
 */

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();
const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
const SELECTED = template.id;

const SOUTHERN: Record<string, string> = {
  Canopus: 'finding-hip-30438',
  Achernar: 'finding-hip-7588',
  Acrux: 'finding-hip-60718',
  Mimosa: 'finding-hip-62434',
  Gacrux: 'finding-hip-61084',
  'Rigil Kentaurus': 'finding-hip-71683',
  Hadar: 'finding-hip-68702',
};

function southernSession(titles: string[]): ObservationSession {
  const findings: CelestialFinding[] = [
    ...base.observationSolution!.findings,
    ...titles.map((title) => ({ ...structuredClone(template), id: SOUTHERN[title]!, title })),
  ];
  return {
    ...base,
    selectedFindingId: SELECTED,
    observationSolution: { ...base.observationSolution!, findings },
  };
}

function parse(utterance: string, session: ObservationSession) {
  return parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
}

const VERBS = ['select {}', 'what is {}', 'take me to {}'];

describe('southern names', () => {
  const session = southernSession(Object.keys(SOUTHERN));

  it.each([
    ['Canopus', 'Canopus'],
    ['Achernar', 'Achernar'],
    ['Acrux', 'Acrux'],
    ['Rigil Kentaurus', 'Rigil Kentaurus'],
    ['Alpha Centauri', 'Rigil Kentaurus'],
    ['Beta Centauri', 'Hadar'],
    ['Agena', 'Hadar'],
    ['Alpha Crucis', 'Acrux'],
    ['Beta Crucis', 'Mimosa'],
    ['Alpha Carinae', 'Canopus'],
    ['Alpha Eridani', 'Achernar'],
  ])('%s resolves to the finding titled %s', (said, iauName) => {
    for (const verb of VERBS) {
      const result = parse(verb.replace('{}', said), session);
      expect(result.action, verb.replace('{}', said)).toBeDefined();
      expect((result.action as { findingId?: string }).findingId).toBe(SOUTHERN[iauName]);
    }
  });

  it.each([
    ['the Large Magellanic Cloud', 'the Large Magellanic Cloud'],
    ['the LMC', 'the Large Magellanic Cloud'],
    ['the SMC', 'the Small Magellanic Cloud'],
    ['Omega Centauri', 'Omega Centauri'],
    ['47 Tucanae', '47 Tucanae'],
    ['Eta Carinae', 'Eta Carinae'],
    ['the Carina Nebula', 'the Carina Nebula'],
    ['the Jewel Box', 'the Jewel Box'],
    ['the Coalsack', 'the Coalsack'],
  ])('%s is refused plainly and never becomes the selection', (said, label) => {
    for (const verb of VERBS) {
      const result = parse(verb.replace('{}', said), session);
      expect(result.action, verb.replace('{}', said)).toBeUndefined();
      expect(result.reason).toBe('no_grounded_answer');
      expect(result.error?.toLowerCase()).toContain(label.toLowerCase());
    }
  });

  it('says where a deep-sky object is and that voice cannot reach it, in the backend words', () => {
    // The same sentences as services/voice/names.py (tests/integration/test_voice_southern_names.py)
    expect(unbakedRefusal('Omega Centauri')).toBe(
      "Omega Centauri is in the app's deep-sky catalogue (OpenNGC NGC5139), but voice does not reach deep-sky objects yet, so it cannot be selected, described or flown to."
    );
    expect(unbakedRefusal('the Coalsack')).toBe(
      "The Coalsack is in the app's deep-sky catalogue (OpenNGC C099), but voice does not reach deep-sky objects yet, so it cannot be selected, described or flown to."
    );
    expect(unbakedRefusal('Eta Carinae')).toBe(
      "Eta Carinae has no named row in the app's catalogues yet, so it cannot be selected, described or flown to by name."
    );
    expect(parse('take me to omega centauri', session).error).toBe(unbakedRefusal('Omega Centauri'));
  });

  it('a named southern star the solution lacks is refused, not swapped for the selection', () => {
    const withoutCanopus = southernSession(['Acrux']);
    for (const said of ['Canopus', 'Alpha Centauri', 'Hadar']) {
      for (const verb of VERBS) {
        const result = parse(verb.replace('{}', said), withoutCanopus);
        expect(result.action, verb.replace('{}', said)).toBeUndefined();
        expect(result.reason).toBe('ambiguous_target');
      }
    }
  });

  it('the Southern Cross asks which of its stars the solution holds', () => {
    const cross = southernSession(['Acrux', 'Mimosa', 'Gacrux']);
    for (const said of ['the Southern Cross', 'Crux']) {
      for (const verb of VERBS) {
        const result = parse(verb.replace('{}', said), cross);
        expect(result.disambiguation?.choices).toEqual([
          { id: SOUTHERN.Acrux, label: 'Acrux' },
          { id: SOUTHERN.Mimosa, label: 'Mimosa' },
          { id: SOUTHERN.Gacrux, label: 'Gacrux' },
        ]);
        expect(result.disambiguation?.question).toContain(SOUTHERN_CROSS_STARS.join(', '));
      }
    }
    const none = parse('select the Southern Cross', southernSession([]));
    expect(none.action).toBeUndefined();
    expect(none.reason).toBe('ambiguous_target');
  });
});
