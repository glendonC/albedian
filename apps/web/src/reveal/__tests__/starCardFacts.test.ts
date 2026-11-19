import { describe, expect, it } from 'vitest';
import type { CelestialFinding } from '@albedian/contracts';
import { sample } from '../../components/__tests__/poseSessions.js';
import { starCardFacts } from '../starCardFacts.js';

// The picked star's card (reveal, approved 2026-11-12, laptop screen 3; numberglyph, cautions and original, decided
// the same night). Every word and number is the finding's own; a fact it does not have is left out, never filled in.

const betelgeuse = sample('plate_calibrated').findings.find((f) => f.title === 'Betelgeuse')!;
// A catalog matched star as the server writes one today (the public sample's Acrux, its numbers rounded here)
const acrux = {
  ...betelgeuse,
  id: 'finding-acrux',
  title: 'Acrux',
  subtitle: 'Alpha1 Crux · HR 4730 · 98.7 pc [94.0-103.9 pc]',
  evidenceClass: 'catalog_matched',
  sourceDetection: { ...betelgeuse.sourceDetection!, snr: 186.717 },
  inferredDistancePc: { lower: 93.98, median: 98.7188, upper: 103.9, method: 'geometric_parallax' },
  catalogRef: {
    ...betelgeuse.catalogRef!,
    provider: 'Hipparcos',
    releaseVersion: 'Hipparcos 2007',
    catalogSourceId: 'HIP 60718',
    commonName: 'Acrux',
    bayerDesignation: 'Alpha1 Crux',
    constellation: 'Cru',
  },
  evidence: {
    ...betelgeuse.evidence,
    limitations: [
      'Distance is 1000/parallax from Hipparcos 2007, with the interval from the parallax error, not measured from this image.',
      'Apparent magnitude is Hipparcos Hp, not Johnson V.',
    ],
  },
} as CelestialFinding;
const withRef = (over: Record<string, unknown>) => ({ ...acrux, catalogRef: { ...acrux.catalogRef!, ...over } }) as CelestialFinding;

describe('starCardFacts: a catalog matched star', () => {
  const facts = starCardFacts(acrux);

  it('carries its own evidence class, its name, and its designation with the constellation written in full', () => {
    expect(facts.evidenceClass).toBe('catalog_matched');
    expect(facts.title).toBe('Acrux');
    expect(facts.subLine).toBe('Alpha1 Crux · Crux');
  });

  it('writes the catalog distance in light years to three figures, as a catalog value', () => {
    expect(facts.numbers[0]).toEqual({ value: '322', unit: 'ly', label: 'away from Earth', evidenceClass: 'catalog_matched' });
  });

  it('writes the measured signal to noise as a whole number of times, as an observed value', () => {
    expect(facts.numbers[1]).toEqual({ value: '187', unit: '×', label: 'above the noise', evidenceClass: 'observed' });
    expect(facts.numbers).toHaveLength(2);
  });

  it('names the catalog release and the row, and can be flown to', () => {
    expect(facts.sourceLine).toBe('Hipparcos 2007 · HIP 60718');
    expect(facts.canGoThere).toBe(true);
  });

  it('keeps three figures at any distance: a near star with a decimal, a far one in whole light years with thousands grouped', () => {
    const at = (pc: number) => starCardFacts({ ...acrux, inferredDistancePc: { lower: pc, median: pc, upper: pc, method: 'geometric_parallax' } } as CelestialFinding).numbers[0]!.value;
    expect(at(1.3)).toBe('4.24');
    expect(at(7.68)).toBe('25.0');
    expect(at(98.7188)).toBe('322');
    expect(at(479.1)).toBe('1,560');
  });

  it("puts the finding's own limitations at the foot, word for word and in order", () => {
    expect(facts.notes).toEqual([
      'Distance is 1000/parallax from Hipparcos 2007, with the interval from the parallax error, not measured from this image.',
      'Apparent magnitude is Hipparcos Hp, not Johnson V.',
    ]);
  });

  it("writes the sample's own Vega as the approved picture does: 25.0 ly, 427 times", () => {
    const vega = {
      ...acrux,
      title: 'Vega',
      sourceDetection: { ...acrux.sourceDetection!, snr: 426.7 },
      inferredDistancePc: { lower: 7.66, median: 7.68, upper: 7.7, method: 'geometric_parallax' },
      catalogRef: { ...acrux.catalogRef!, commonName: 'Vega', catalogSourceId: 'HIP 91262', bayerDesignation: 'Alpha Lyrae', constellation: 'Lyr' },
    } as CelestialFinding;
    const v = starCardFacts(vega);
    expect(v.subLine).toBe('Alpha Lyrae · Lyra');
    expect(v.numbers.map((n) => `${n.value} ${n.unit} ${n.label}`)).toEqual(['25.0 ly away from Earth', '427 × above the noise']);
    expect(v.sourceLine).toBe('Hipparcos 2007 · HIP 91262');
  });
});

describe('starCardFacts: a star with no name (INTERIM: UIUX-unnamed-QUESTION.md)', () => {
  const nameless = (provider: 'Gaia DR3' | 'Hipparcos', id: string) =>
    ({ ...withRef({ provider, catalogSourceId: id, commonName: id, bayerDesignation: undefined, constellation: 'Cen' }), title: id }) as CelestialFinding;

  it("titles it by its catalog's name and puts its number in that catalog with the constellation on the small line", () => {
    const facts = starCardFacts(nameless('Gaia DR3', 'Gaia DR3 6125166320994675456'));
    expect(facts.title).toBe('Gaia DR3');
    expect(facts.subLine).toBe('6125166320994675456 · Centaurus');
    // The title and the small line already say what the catalog line would
    expect(facts.sourceLine).toBeNull();
    const hip = starCardFacts(nameless('Hipparcos', 'HIP 71683'));
    expect(hip.title).toBe('Hipparcos');
    expect(hip.subLine).toBe('71683 · Centaurus');
  });

  it('keeps the number alone where the constellation has no full name, and changes nothing for a star with a name', () => {
    const facts = starCardFacts({ ...nameless('Gaia DR3', 'Gaia DR3 6125166320994675456'), catalogRef: { ...nameless('Gaia DR3', 'Gaia DR3 6125166320994675456').catalogRef!, constellation: 'Xyz' } } as CelestialFinding);
    expect(facts.subLine).toBe('6125166320994675456');
    expect(starCardFacts(acrux).title).toBe('Acrux');
    expect(starCardFacts(acrux).sourceLine).not.toBeNull();
  });
});

describe('starCardFacts: what a finding does not have is left out', () => {
  it('leaves the designation out of the line under the name when it is the name, and keeps the constellation', () => {
    expect(starCardFacts({ ...withRef({ commonName: 'HIP 60718' }), title: 'Alpha1 Crux' }).subLine).toBe('Crux');
    expect(starCardFacts(withRef({ bayerDesignation: undefined })).subLine).toBe('Crux');
  });

  it('writes no line under the name for a constellation it has no full name for, rather than a code', () => {
    expect(starCardFacts(withRef({ bayerDesignation: undefined, constellation: 'Xyz' })).subLine).toBeNull();
    expect(starCardFacts(withRef({ constellation: 'Xyz' })).subLine).toBe('Alpha1 Crux');
  });

  it('shows no distance without a catalog distance, and none from an ephemeris distance', () => {
    expect(starCardFacts({ ...acrux, inferredDistancePc: undefined }).numbers.map((n) => n.label)).toEqual(['above the noise']);
    const planet = { ...acrux, inferredDistancePc: { lower: 0.00002, median: 0.000021, upper: 0.000022, method: 'ephemeris' } } as unknown as CelestialFinding;
    expect(starCardFacts(planet).numbers.map((n) => n.label)).toEqual(['above the noise']);
  });

  it('shows no signal to noise for a finding that was not measured in the photo', () => {
    const predicted = { ...acrux, evidenceClass: 'predicted', sourceDetection: undefined } as CelestialFinding;
    const facts = starCardFacts(predicted);
    expect(facts.numbers).toEqual([{ value: '322', unit: 'ly', label: 'away from Earth', evidenceClass: 'predicted' }]);
    // A predicted star is never one the app flies to: its card offers Original pixels only
    expect(facts.canGoThere).toBe(false);
    expect(facts.evidenceClass).toBe('predicted');
  });

  it('cannot be flown to without a usable catalog distance', () => {
    expect(starCardFacts({ ...acrux, inferredDistancePc: undefined }).canGoThere).toBe(false);
  });

  it('has no source line and no numbers for a point with no catalog row and no measurement', () => {
    const bare = { ...acrux, evidenceClass: 'predicted', catalogRef: undefined, sourceDetection: undefined, inferredDistancePc: undefined } as CelestialFinding;
    const facts = starCardFacts(bare);
    expect(facts.subLine).toBeNull();
    expect(facts.sourceLine).toBeNull();
    expect(facts.numbers).toEqual([]);
  });

  it("adds Gaia's own caution after the limitations when its fit is loose, and not otherwise", () => {
    const loose = withRef({ astrophysics: { ruwe: 1.62 } });
    expect(starCardFacts(loose).notes.at(-1)).toBe("Gaia's position fit for this star is loose (RUWE 1.62). Read the distance with care.");
    expect(starCardFacts(loose).notes).toHaveLength(3);
    expect(starCardFacts(withRef({ astrophysics: { ruwe: 1.4 } })).notes).toHaveLength(2);
    expect(starCardFacts(acrux).notes).toHaveLength(2);
  });

  it('has no notes for a finding with no limitation', () => {
    expect(starCardFacts({ ...acrux, evidence: { ...acrux.evidence, limitations: [] } }).notes).toEqual([]);
  });
});
