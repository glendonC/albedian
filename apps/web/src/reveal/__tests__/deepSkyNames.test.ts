import { describe, expect, it } from 'vitest';
import { deepSkyName } from '../deepSky.js';

// C19 item 3 (UIUX-walkasks-READY.md, decided by the lead 12:59): a deep-sky common name that begins with a Bayer
// abbreviation is written as a star's Bayer name is, the letter's name and the constellation's genitive in full; the
// rest is kept as OpenNGC gives it, and a name with no such prefix is unchanged.

describe('deepSkyName', () => {
  it('writes the leading Greek letter and constellation in full', () => {
    expect(deepSkyName('lam Cen Nebula')).toBe('Lambda Centauri Nebula');
    expect(deepSkyName('Lam Cen Nebula')).toBe('Lambda Centauri Nebula');
    expect(deepSkyName('eta Car Nebula')).toBe('Eta Carinae Nebula');
    expect(deepSkyName('alp Per Cluster')).toBe('Alpha Persei Cluster');
    expect(deepSkyName('kap Cru Cluster')).toBe('Kappa Crucis Cluster');
  });

  it('leaves a name with no such prefix as it is', () => {
    for (const name of ['Omega Centauri', 'Pearl Cluster', 'S Nor Cluster', 'Orion Nebula', 'Eta', 'lam Xyz Nebula', 'Pleiades']) expect(deepSkyName(name)).toBe(name);
  });
});

describe("the card's title for a deep-sky object (AUDIT-C, 13:24)", () => {
  it('is the same full name the mark reads, and a star keeps its own title', async () => {
    const { starCardFacts } = await import('../starCardFacts.js');
    const { sample } = await import('../../components/__tests__/poseSessions.js');
    const template = sample('plate_calibrated').findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const nebula = { ...template, id: 'finding-openngc-IC2944', evidenceClass: 'predicted', title: 'lam Cen Nebula', catalogRef: { ...template.catalogRef!, provider: 'OpenNGC', objectType: 'deep_sky', catalogSourceId: 'IC 2944', commonName: 'lam Cen Nebula' } } as unknown as typeof template;
    expect(starCardFacts(nebula).title).toBe('Lambda Centauri Nebula');
    expect(starCardFacts(template).title).toBe(template.title);
  });
});
