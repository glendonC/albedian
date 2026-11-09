import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { CatalogReferenceSchema } from '../index.js';

/**
 * The catalog reference against the samples the Python mirror wrote (services/contracts/catalog_reference.py,
 * tests/fixtures/catalog_reference_contract.json): Vega and Meissa as services/science/catalog_model.py writes them,
 * two real OpenNGC v20260501 rows (CC BY-SA 4.0, credit Mattia Verga) as deep-sky references, one without a
 * magnitude, and one fault per rule. Both sides must accept and refuse exactly the same references.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/catalog_reference_contract.json');

type Reference = Record<string, unknown>;

interface CatalogReferenceFixture {
  providers: string[];
  objectTypes: string[];
  distanceMethods: string[];
  valid: Reference[];
  invalid: { rule: string; reference: Reference }[];
  droppedKey: { reference: Reference; parsed: Reference };
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as CatalogReferenceFixture;

describe('@albedian/contracts: the catalog reference', () => {
  it('names the same providers, object types and distance methods as the Pydantic mirror', () => {
    const shape = CatalogReferenceSchema.innerType().shape;
    expect([...shape.provider.options]).toEqual(fixture.providers);
    expect([...shape.objectType.options]).toEqual(fixture.objectTypes);
    expect([...shape.bailerJonesDistancePc.unwrap().shape.method.options]).toEqual(fixture.distanceMethods);
  });

  it('parses the real references the Python side wrote, unchanged', () => {
    expect(fixture.valid).toHaveLength(4);
    for (const reference of fixture.valid) {
      const parsed = CatalogReferenceSchema.safeParse(reference);
      expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
      expect(parsed.data).toEqual(reference);
    }
    // One deep-sky reference has no magnitude: OpenNGC gives none for it, and none is filled in
    expect(fixture.valid.filter((r) => !('apparentMagnitude' in r)).map((r) => r.objectType)).toEqual(['deep_sky']);
  });

  it('refuses every reference the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThanOrEqual(10);
    for (const { rule, reference } of fixture.invalid) {
      expect(CatalogReferenceSchema.safeParse(reference).success, rule).toBe(false);
    }
  });

  it('drops a key the contract does not name, as the Python side does, rather than refusing it', () => {
    const parsed = CatalogReferenceSchema.safeParse(fixture.droppedKey.reference);
    expect(parsed.success).toBe(true);
    expect(parsed.data).toEqual(fixture.droppedKey.parsed);
  });

  it('PBT-CREF-1: a reference may leave its magnitude out, and name OpenNGC, only for a deep-sky object', () => {
    const base = fixture.valid.find((r) => r.provider === 'OpenNGC' && 'apparentMagnitude' in r)!;
    const caseArb = fc.record({
      objectType: fc.constantFrom(...fixture.objectTypes),
      provider: fc.constantFrom(...fixture.providers),
      withMagnitude: fc.boolean(),
    });
    fc.assert(
      fc.property(caseArb, ({ objectType, provider, withMagnitude }) => {
        const reference: Reference = { ...structuredClone(base), objectType, provider };
        if (!withMagnitude) delete reference.apparentMagnitude;
        const allowed =
          (withMagnitude || objectType === 'deep_sky') && (provider !== 'OpenNGC' || objectType === 'deep_sky');
        expect(CatalogReferenceSchema.safeParse(reference).success).toBe(allowed);
      }),
      {
        numRuns: 200,
        seed: SEED,
        examples: [
          [{ objectType: 'deep_sky', provider: 'OpenNGC', withMagnitude: false }],
          [{ objectType: 'star', provider: 'OpenNGC', withMagnitude: true }],
          [{ objectType: 'star', provider: 'Hipparcos', withMagnitude: false }],
          [{ objectType: 'deep_sky', provider: 'SIMBAD', withMagnitude: false }],
          [{ objectType: 'moon', provider: 'JPL Horizons', withMagnitude: false }],
        ],
      }
    );
  });
});
