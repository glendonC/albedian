import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { CatalogReferenceSchema } from '../index.js';
import { wireInt } from '../wire.js';

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
  designationEdges: { field: string; value: unknown; valid: boolean }[];
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

  it('takes or refuses each designation bound as the Python side does, from the shared edge table', () => {
    const base = fixture.valid.find((r) => r.provider === 'Hipparcos')!;
    expect(fixture.designationEdges.length).toBeGreaterThanOrEqual(25);
    for (const { field, value, valid } of fixture.designationEdges) {
      const parsed = CatalogReferenceSchema.safeParse({ ...base, [field]: value });
      expect(parsed.success, `${field}=${JSON.stringify(value)}`).toBe(valid);
      if (valid) expect((parsed.data as Record<string, unknown>)[field]).toEqual(value);
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

  it('holds the Flamsteed number to its own edge in the solution field table, and a count to the largest safe integer', () => {
    // The table code on main gave every integer rule the largest safe integer as an accepted edge, right for a count
    // and wrong for 1 to 140; with an upper bound the edge is the bound and one past it
    const table = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/observation_solution_contract.json'), 'utf8'),
    ) as { rules: Record<string, { accept: unknown[]; refuse: unknown[]; le?: number }> };
    const flamsteed = table.rules.flamsteedNumber ?? { accept: [], refuse: [] };
    const count = table.rules.count ?? { accept: [], refuse: [] };
    expect(flamsteed.le).toBe(140);
    expect(flamsteed.accept).toContain(140);
    expect(flamsteed.accept).not.toContain(9007199254740991);
    expect(flamsteed.refuse).toContain(141);
    expect(count.accept).toContain(9007199254740991);
    expect(count.le).toBeUndefined();
    const base = fixture.valid[0];
    expect(CatalogReferenceSchema.safeParse({ ...base, flamsteedNumber: 140 }).success).toBe(true);
    for (const beyond of [141, 141.0, 9007199254740991]) {
      expect(CatalogReferenceSchema.safeParse({ ...base, flamsteedNumber: beyond }).success, String(beyond)).toBe(false);
    }
  });

  it('takes and refuses the integer edge samples of the field table as the Python side says, for every pair of bounds', () => {
    // catalog-2b v3: the samples are made from each rule's own bounds; Zod's own integer schema judges them here
    const tables = (fixture as unknown as { integerEdgeTables: { bounds: { ge?: number; gt?: number; le?: number }; accept: number[]; refuse: number[] }[] }).integerEdgeTables;
    expect(tables.length).toBeGreaterThanOrEqual(14);
    for (const { bounds, accept, refuse } of tables) {
      let schema = wireInt();
      if (bounds.ge !== undefined) schema = schema.gte(bounds.ge);
      if (bounds.gt !== undefined) schema = schema.gt(bounds.gt);
      if (bounds.le !== undefined) schema = schema.lte(bounds.le);
      for (const value of accept) expect(schema.safeParse(value).success, `${JSON.stringify(bounds)} ${value}`).toBe(true);
      for (const value of refuse) expect(schema.safeParse(value).success, `${JSON.stringify(bounds)} ${value}`).toBe(false);
    }
  });
});
