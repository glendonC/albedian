import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  DEEP_SKY_CATALOG_FORMAT,
  DEEP_SKY_CATALOG_FORMAT_VERSION,
  DEEP_SKY_COLUMNS,
  DEEP_SKY_CONSTELLATIONS,
  DEEP_SKY_EVIDENCE_KIND,
  DEEP_SKY_MESSIER_MAX,
  DEEP_SKY_MESSIER_MIN,
  DEEP_SKY_OBJECT_TYPES,
  DEEP_SKY_PATTERNS,
  DeepSkyCatalogSchema,
} from '../index.js';

/**
 * The deep-sky catalogue against the samples the Python mirror wrote (services/contracts/deep_sky.py,
 * tests/fixtures/deep_sky_contract.json): 16 real OpenNGC v20260501 rows (CC BY-SA, credit Mattia Verga), and one
 * fault per rule. Both sides must accept and refuse exactly the same catalogues.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/deep_sky_contract.json');

type Row = unknown[];
type Catalog = Record<string, unknown> & { types: Record<string, string>; columns: string[]; rows: Row[] };

interface DeepSkyFixture {
  format: string;
  formatVersion: number;
  evidenceKind: string;
  columns: string[];
  objectTypes: string[];
  constellations: string[];
  messierRange: [number, number];
  patterns: Record<string, string>;
  valid: Catalog[];
  invalid: { rule: string; table: Catalog }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as DeepSkyFixture;
const col = (name: string) => (DEEP_SKY_COLUMNS as readonly string[]).indexOf(name);
const put = (t: Catalog, values: Record<string, unknown>) => {
  for (const [name, value] of Object.entries(values)) t.rows[0]![col(name)] = value;
};
const repeatMessier = (t: Catalog) => {
  for (const row of t.rows.slice(0, 2)) {
    row[col('type')] = 'G';
    row[col('raDeg')] = 10.0;
    row[col('decDeg')] = 0.0;
    row[col('constellation')] = 'Psc';
    row[col('messier')] = 31;
  }
};

// Each fault breaks a rule of any valid catalogue of at least two rows (the same faults as the Python test's)
const FAULTS: Record<string, (t: Catalog) => void> = {
  format: (t) => void (t.format = 'albedian.deep-sky-list'),
  formatVersion: (t) => void (t.formatVersion = 2),
  'evidence kind': (t) => void (t.evidenceKind = 'observed'),
  'extra field': (t) => void (t.extra = 1),
  'blank-padded release': (t) => void (t.release = ' OpenNGC'),
  'column renamed': (t) => void (t.columns[0] = 'id'),
  'no rows': (t) => void (t.rows = []),
  'short row': (t) => void t.rows[0]!.pop(),
  'long row': (t) => void t.rows[0]!.push(null),
  'type label missing': (t) => void delete t.types.Dup,
  'label for an unknown type': (t) => void (t.types.QSO = 'Quasar'),
  'blank-padded type label': (t) => void (t.types.G = 'Galaxy '),
  'repeated name': (t) => void (t.rows[1]![0] = t.rows[0]![0]),
  'blank-padded name': (t) => put(t, { name: `${String(t.rows[0]![0])} ` }),
  'unknown type': (t) => put(t, { type: 'QSO' }),
  'right ascension of 360': (t) => put(t, { type: 'G', raDeg: 360.0, decDeg: 0.0, constellation: 'Psc' }),
  'negative right ascension': (t) => put(t, { type: 'G', raDeg: -0.5, decDeg: 0.0, constellation: 'Psc' }),
  'declination past the pole': (t) => put(t, { type: 'G', raDeg: 10.0, decDeg: 90.5, constellation: 'Cep' }),
  'right ascension without declination': (t) => put(t, { type: 'G', raDeg: 10.0, decDeg: null, constellation: 'Psc' }),
  'no position on a galaxy': (t) => put(t, { type: 'G', raDeg: null, decDeg: null, constellation: null }),
  'position as text': (t) => put(t, { type: 'G', raDeg: '10.0', decDeg: 0.0, constellation: 'Psc' }),
  'unknown constellation': (t) => put(t, { type: 'GCl', raDeg: 229.64, decDeg: 2.08, constellation: 'Ser' }),
  'constellation missing with a position': (t) => put(t, { type: 'G', raDeg: 10.0, decDeg: 0.0, constellation: null }),
  'constellation without a position': (t) => put(t, { type: 'NonEx', raDeg: null, decDeg: null, constellation: 'Cru' }),
  'zero major axis': (t) => put(t, { majorAxisArcmin: 0.0, minorAxisArcmin: null, positionAngleDeg: null }),
  'minor axis larger than major': (t) => put(t, { majorAxisArcmin: 1.0, minorAxisArcmin: 2.0 }),
  'minor axis without major': (t) => put(t, { majorAxisArcmin: null, minorAxisArcmin: 1.0, positionAngleDeg: null }),
  'position angle of 181': (t) => put(t, { majorAxisArcmin: 1.0, minorAxisArcmin: null, positionAngleDeg: 181.0 }),
  'negative position angle': (t) => put(t, { majorAxisArcmin: 1.0, minorAxisArcmin: null, positionAngleDeg: -1.0 }),
  'position angle without major axis': (t) =>
    put(t, { majorAxisArcmin: null, minorAxisArcmin: null, positionAngleDeg: 10.0 }),
  'magnitude as text': (t) => put(t, { bMag: '4.0' }),
  'messier 0': (t) => put(t, { messier: 0 }),
  'messier 111': (t) => put(t, { messier: 111 }),
  'fractional messier': (t) => put(t, { messier: 2.5 }),
  'boolean messier': (t) => put(t, { messier: true }),
  'messier repeated outside duplicates': repeatMessier,
  'duplicate naming no target': (t) =>
    put(t, { type: 'Dup', raDeg: 10.0, decDeg: 0.0, constellation: 'Psc', messier: null, ngc: [], ic: [] }),
  'prefixed NGC cross reference': (t) => put(t, { ngc: ['NGC0104'] }),
  'IC cross reference as one string': (t) => put(t, { ic: '0554' }),
  'blank-padded identifier': (t) => put(t, { identifiers: [' PGC 003085'] }),
  'empty common name': (t) => put(t, { commonNames: [''] }),
};

// =============================================================================================
// Generators of valid catalogues, by the same rules as the Python test's
// =============================================================================================

const floats = (min: number, max: number, excluded: { min?: boolean; max?: boolean } = {}) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true, minExcluded: excluded.min, maxExcluded: excluded.max });
const text = fc.stringMatching(/^[A-Za-z0-9][A-Za-z0-9 +'.-]{0,10}[A-Za-z0-9]$/);
const cross = fc.stringMatching(/^[0-9]{4}([A-Z]{1,2})?( NED[0-9]{2})?$/);

const rowArb = (name: string, messier: number | null) =>
  fc
    .record({
      kind: fc.constantFrom(...DEEP_SKY_OBJECT_TYPES),
      positioned: fc.boolean(),
      ra: floats(0, 360, { max: true }),
      dec: floats(-90, 90),
      constellation: fc.constantFrom(...DEEP_SKY_CONSTELLATIONS),
      major: fc.option(floats(0, 700, { min: true }), { nil: null }),
      minorFraction: fc.option(floats(0, 1, { min: true }), { nil: null }),
      angle: fc.option(floats(0, 180), { nil: null }),
      mags: fc.tuple(...[0, 1, 2].map(() => fc.option(floats(-2, 35), { nil: null }))),
      dupMessier: fc.option(fc.integer({ min: DEEP_SKY_MESSIER_MIN, max: DEEP_SKY_MESSIER_MAX }), { nil: null }),
      ngc: fc.array(cross, { maxLength: 2 }),
      ic: fc.array(cross, { maxLength: 2 }),
      identifiers: fc.array(text, { maxLength: 3 }),
      common: fc.array(text, { maxLength: 2 }),
    })
    .map((v): Row => {
      const positioned = v.kind !== 'NonEx' || v.positioned;
      const minor = v.major !== null && v.minorFraction !== null ? v.major * v.minorFraction : null;
      const number = v.kind === 'Dup' ? v.dupMessier : messier;
      const ngc = v.kind === 'Dup' && number === null && v.ngc.length === 0 && v.ic.length === 0 ? ['0001'] : v.ngc;
      return [
        name,
        v.kind,
        positioned ? v.ra : null,
        positioned ? v.dec : null,
        positioned ? v.constellation : null,
        v.major,
        minor !== null && minor > 0 ? minor : null,
        v.major !== null ? v.angle : null,
        ...v.mags,
        number,
        ngc,
        v.ic,
        v.identifiers,
        v.common,
      ];
    });

const catalogOf = (rows: Row[]): Catalog => ({
  format: DEEP_SKY_CATALOG_FORMAT,
  formatVersion: DEEP_SKY_CATALOG_FORMAT_VERSION,
  evidenceKind: DEEP_SKY_EVIDENCE_KIND,
  release: 'OpenNGC v20260501',
  types: structuredClone(fixture.valid[0]!.types),
  columns: [...DEEP_SKY_COLUMNS],
  rows,
});

const validCatalog = fc
  .array(fc.option(fc.integer({ min: 1, max: 110 }), { nil: null }), { minLength: 2, maxLength: 5 })
  .chain((numbers) => {
    // Messier numbers stay unique outside duplicates; a duplicate draws its own
    const seen = new Set<number>();
    const unique = numbers.map((n) => (n !== null && !seen.has(n) ? (seen.add(n), n) : null));
    return fc.tuple(...unique.map((n, i) => rowArb(`OBJ${String(i).padStart(4, '0')}`, n))).map(catalogOf);
  });

describe('@albedian/contracts: the deep-sky catalogue', () => {
  it('matches the Pydantic mirror on format, columns, codes and patterns', () => {
    expect(fixture.format).toBe(DEEP_SKY_CATALOG_FORMAT);
    expect(fixture.formatVersion).toBe(DEEP_SKY_CATALOG_FORMAT_VERSION);
    expect(fixture.evidenceKind).toBe(DEEP_SKY_EVIDENCE_KIND);
    expect(fixture.columns).toEqual([...DEEP_SKY_COLUMNS]);
    expect(fixture.objectTypes).toEqual([...DEEP_SKY_OBJECT_TYPES]);
    expect(fixture.constellations).toEqual([...DEEP_SKY_CONSTELLATIONS]);
    expect(fixture.messierRange).toEqual([DEEP_SKY_MESSIER_MIN, DEEP_SKY_MESSIER_MAX]);
    expect(fixture.patterns).toEqual(DEEP_SKY_PATTERNS);
  });

  it('parses the real rows the Python side wrote, unchanged', () => {
    expect(fixture.valid).toHaveLength(1);
    const parsed = DeepSkyCatalogSchema.safeParse(fixture.valid[0]);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
    expect(parsed.data).toEqual(fixture.valid[0]);
  });

  it('refuses every catalogue the Python side refuses', () => {
    expect(fixture.invalid.length).toBe(Object.keys(FAULTS).length);
    expect(fixture.invalid.map((c) => c.rule)).toEqual(Object.keys(FAULTS));
    for (const { rule, table: sample } of fixture.invalid) {
      expect(DeepSkyCatalogSchema.safeParse(sample).success, rule).toBe(false);
    }
  });

  it('refuses numbers that JSON cannot carry but a caller can pass', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      for (const name of ['raDeg', 'majorAxisArcmin', 'bMag']) {
        const t = structuredClone(fixture.valid[0]!);
        put(t, { [name]: bad });
        expect(DeepSkyCatalogSchema.safeParse(t).success, `${bad} at ${name}`).toBe(false);
      }
    }
  });

  it('PBT-DSO-1: the schema accepts exactly the catalogues the rules allow', () => {
    const caseArb = validCatalog.chain((t) =>
      fc.option(fc.constantFrom(...Object.keys(FAULTS)), { nil: undefined, freq: 2 }).map((fault) => {
        const copy = structuredClone(t);
        if (fault !== undefined) FAULTS[fault]!(copy);
        return [copy, fault] as const;
      })
    );
    let accepted = 0;
    let rejected = 0;
    fc.assert(
      fc.property(caseArb, ([t, fault]) => {
        const parsed = DeepSkyCatalogSchema.safeParse(t);
        expect(parsed.success, `${fault ?? 'no fault'}: ${JSON.stringify(parsed.error?.issues)}`).toBe(fault === undefined);
        if (parsed.success) {
          accepted++;
          expect(parsed.data).toEqual(t);
        } else {
          rejected++;
        }
      }),
      {
        numRuns: 300,
        seed: SEED,
        // The real rows: no position, duplicates by M, NGC and IC, Se1
        examples: [[[fixture.valid[0]!, undefined]]],
      }
    );
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
  }, 30_000);
});
