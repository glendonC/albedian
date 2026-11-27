import { describe, expect, it } from 'vitest';
import { deepSkyName } from '../deepSky.js';

// The lead's ruling of 2026-11-27 00:11 (deepsky-name-passthrough-one): one place writes deep-sky names, the server's
// cited table (services/science/deep_sky_catalog.py, FULL_NAMES), which writes a catalog short form in full only as a
// named reference writes it for that object; the page passes the served name through and makes no form of its own.

describe('deepSkyName', () => {
  it('passes a served short form through as it is: the page writes no full form of its own', () => {
    for (const name of ['tet Car Cluster', 'omi Vel Cluster', 'omi Per Cloud', 'rho Oph Nebula', 'lam Cen Nebula', 'eta Car Nebula', 'S Nor Cluster', 'gam Cyg']) {
      expect(deepSkyName(name)).toBe(name);
    }
  });

  it('passes a name the server wrote in full, and any other name, through unchanged', () => {
    // The longest served names (Small Sagittarius Star Cloud, 28 letters; OpenNGC's longest, 25) and a longer synthetic one
    for (const name of ['Lambda Centauri Nebula', '47 Tucanae', 'Gemini A', 'Small Sagittarius Star Cloud', "Amas de l'Ecu de Sobieski", 'A synthetic name that runs past forty letters', 'Omega Centauri', 'Pleiades', 'Eta']) {
      expect(deepSkyName(name)).toBe(name);
    }
  });
});

describe("the card's title for a deep-sky object (AUDIT-C, 13:24)", () => {
  it('is the served name the mark reads, and a star keeps its own title', async () => {
    const { starCardFacts } = await import('../starCardFacts.js');
    const { sample } = await import('../../components/__tests__/poseSessions.js');
    const template = sample('plate_calibrated').findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const deep = (id: string, title: string) =>
      ({ ...template, id: `finding-openngc-${id}`, evidenceClass: 'predicted', title, catalogRef: { ...template.catalogRef!, provider: 'OpenNGC', objectType: 'deep_sky', catalogSourceId: id, commonName: title } }) as unknown as typeof template;
    expect(starCardFacts(deep('IC2944', 'Lambda Centauri Nebula')).title).toBe('Lambda Centauri Nebula');
    expect(starCardFacts(deep('IC2602', 'tet Car Cluster')).title).toBe('tet Car Cluster');
    expect(starCardFacts(template).title).toBe(template.title);
  });
});
