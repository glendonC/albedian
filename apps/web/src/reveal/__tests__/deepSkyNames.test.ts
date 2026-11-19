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
