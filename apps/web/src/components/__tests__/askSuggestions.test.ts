import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { askSuggestionsFor, findingCanEnterSpace } from '../askSuggestions.js';

// The ask palette offers only questions the app's own rules turn into an action on the selected star, and only
// what the app can do for that star: Take me there only where the target card's Go there is enabled.

const NOW = new Date('2026-11-09T12:00:00Z');

function selecting(evidenceClass: CelestialFinding['evidenceClass']): { session: ObservationSession; finding: CelestialFinding } {
  const base = createSyntheticCalibratedSession();
  const finding = base.observationSolution?.findings.find((f) => f.evidenceClass === evidenceClass);
  if (!finding) throw new Error(`fixture has no ${evidenceClass} finding`);
  return { session: { ...base, selectedFindingId: finding.id }, finding };
}

function predictedPlanet(session: ObservationSession): CelestialFinding {
  return {
    id: 'finding_jupiter_prd',
    observationSolutionId: session.observationSolution!.id,
    evidenceClass: 'predicted',
    title: 'Jupiter',
    subtitle: 'Solar System Planet',
    skyPosition: { raDeg: 88.5, decDeg: 7.2, uncertaintyArcsec: 0.5 },
    evidence: {
      id: 'ev_jup',
      evidenceClass: 'predicted',
      verificationMethod: 'Ephemeris at observedAtUTC',
      limitations: ['Computed from recording time and location; not recovered from camera pixels.'],
      verifiedByIndependentHoldout: false,
    },
  };
}

describe('ask palette suggestions', () => {
  it('offers none when no star is selected', () => {
    expect(askSuggestionsFor(null)).toEqual([]);
    expect(askSuggestionsFor(undefined)).toEqual([]);
  });

  it('offers distance and Take me there for a catalog matched star with a distance', () => {
    const { finding } = selecting('catalog_matched');
    expect(findingCanEnterSpace(finding)).toBe(true);
    expect(askSuggestionsFor(finding)).toEqual(['How far away is it?', 'Take me there']);
  });

  it('offers only distance for a star the app cannot fly to', () => {
    const { session, finding } = selecting('catalog_matched');
    const noDistance = { ...finding, inferredDistancePc: undefined };
    const observed = { ...finding, evidenceClass: 'observed' as const };
    for (const star of [noDistance, observed, predictedPlanet(session)]) {
      expect(findingCanEnterSpace(star), star.title).toBe(false);
      expect(askSuggestionsFor(star), star.title).toEqual(['How far away is it?']);
    }
  });

  it('never offers the sightline question while the view does not draw it', () => {
    const { finding } = selecting('catalog_matched');
    expect(askSuggestionsFor(finding).some((s) => /behind/i.test(s))).toBe(false);
  });

  it('maps each suggestion to an action on the selected star through the rules parser', () => {
    const { session, finding } = selecting('catalog_matched');
    const expected: Record<string, { type: string; fields?: string[] }> = {
      'How far away is it?': { type: 'finding.describe', fields: ['distance', 'evidence'] },
      'Take me there': { type: 'space.enter' },
    };
    for (const suggestion of askSuggestionsFor(finding)) {
      const result = parseUtteranceToAction(suggestion, buildActionContext(session), session, { now: NOW });
      expect(result.error, suggestion).toBeUndefined();
      expect(result.disambiguation, suggestion).toBeUndefined();
      const action = result.action as { type: string; findingId?: string; fields?: string[] } | undefined;
      expect(action?.type, suggestion).toBe(expected[suggestion]?.type);
      expect(action?.findingId, suggestion).toBe(finding.id);
      if (expected[suggestion]?.fields) expect(action?.fields, suggestion).toEqual(expected[suggestion]?.fields);
    }
  });
});
