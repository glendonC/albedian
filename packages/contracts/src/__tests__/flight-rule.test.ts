import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  FLIGHT_CLASSES,
  REFUSAL_KINDS,
  SOLAR_SYSTEM_BODY_KEYS,
  STAR_CATALOG_DISTANCE_METHODS,
  flightRefusal,
  isSolarSystemBodyId,
  refusalSentence,
  type FlightTarget,
} from '../index.js';

/**
 * The flight rule (flight-rule-0, DECIDED-BY-LEAD 24 Nov 12:55) against the samples the Python mirror wrote
 * (services/contracts/astronomy.py `flight_rule_table`, tests/fixtures/observation_solution_contract.json `flightRule`):
 * both sides fly and refuse exactly the same findings, with the same refusal kind.
 */
interface FlightRuleTable {
  starCatalogDistanceMethods: string[];
  solarSystemBodyKeys: string[];
  flightClasses: string[];
  samples: { about: string; finding: FlightTarget; refusal: string | null }[];
}

const FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/observation_solution_contract.json');
const table = (JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as { flightRule: FlightRuleTable }).flightRule;
const seed = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

describe('@albedian/contracts: the flight rule', () => {
  it('holds the same lists as the Python mirror', () => {
    expect([...STAR_CATALOG_DISTANCE_METHODS]).toEqual(table.starCatalogDistanceMethods);
    expect([...SOLAR_SYSTEM_BODY_KEYS]).toEqual(table.solarSystemBodyKeys);
    expect([...FLIGHT_CLASSES]).toEqual(table.flightClasses);
    expect(STAR_CATALOG_DISTANCE_METHODS).not.toContain('ephemeris_au');
  });

  it.each(table.samples.map((sample) => [sample.about, sample] as const))('judges %s as the Python side does', (_about, sample) => {
    const before = structuredClone(sample.finding);
    expect(flightRefusal(sample.finding)).toBe(sample.refusal);
    // Judging a finding never changes it, its class least of all
    expect(sample.finding).toEqual(before);
  });

  it('names every refusal by a kind of the voice contract, spoken as ruled', () => {
    const kinds = new Set(table.samples.map((sample) => sample.refusal));
    expect(kinds).toEqual(new Set([null, 'no_catalog_distance', 'beyond_solar_system_only']));
    expect(REFUSAL_KINDS.no_catalog_distance).toEqual({ reasons: ['no_distance_estimate'], said: false, name: true, catalogRow: false, backend: true });
    expect(REFUSAL_KINDS.beyond_solar_system_only).toEqual({ reasons: ['no_distance_estimate'], said: false, name: false, catalogRow: false, backend: true });
    expect(refusalSentence({ kind: 'no_catalog_distance', name: 'Zosma' })).toBe('Zosma has no distance in the app\'s catalogs, so it cannot be flown to.');
    expect(refusalSentence({ kind: 'beyond_solar_system_only' }, 'video')).toBe('The flight goes only to stars beyond our solar system.');
  });

  it('knows a Solar System body by its id', () => {
    for (const key of SOLAR_SYSTEM_BODY_KEYS) {
      expect(isSolarSystemBodyId(`finding-predicted-${key}`)).toBe(true);
      expect(isSolarSystemBodyId(`finding-observed-${key}`)).toBe(true);
    }
    for (const other of ['finding-predicted-star-rigel', 'finding-obs-3', 'finding-predicted-pluto', 'finding-predicted-moonlet', 'moon']) {
      expect(isSolarSystemBodyId(other)).toBe(false);
    }
  });

  it('flies a catalogued star of either flight class whatever its distance, and never upgrades it', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...FLIGHT_CLASSES),
        fc.constantFrom(...STAR_CATALOG_DISTANCE_METHODS),
        fc.double({ min: 1e-3, max: 1e5, noNaN: true }),
        fc.double({ min: 0, max: 0.9, noNaN: true }),
        (evidenceClass, method, median, spread) => {
          const finding: FlightTarget = {
            id: 'finding-predicted-star-sample',
            evidenceClass,
            catalogRef: { objectType: 'star' },
            inferredDistancePc: { lower: median * (1 - spread), median, upper: median * (1 + spread), method },
          };
          expect(flightRefusal(finding)).toBeNull();
          expect(finding.evidenceClass).toBe(evidenceClass);
          expect(flightRefusal({ ...finding, inferredDistancePc: { ...finding.inferredDistancePc!, method: 'ephemeris_au' } })).toBe('beyond_solar_system_only');
        }
      ),
      { seed, numRuns: 200 }
    );
  });
});
