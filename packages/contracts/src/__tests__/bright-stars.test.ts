import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  BRIGHT_STAR_COLUMNS,
  BRIGHT_STAR_EPOCH_JULIAN_YEAR,
  BRIGHT_STAR_EVIDENCE_KIND,
  BRIGHT_STAR_FRAME,
  BRIGHT_STAR_PATTERNS,
  BRIGHT_STAR_SUPPLEMENT_FORMAT,
  BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
  BrightStarSupplementSchema,
} from '../index.js';

/**
 * The bright-star 3D supplement against the samples the Python mirror wrote (services/contracts/bright_stars.py,
 * tests/fixtures/bright_stars_contract.json): six real rows of one ESA Gaia archive query of Hipparcos 2007 with V from
 * Hipparcos 1997 (CC BY-NC 3.0 IGO, credit ESA), and one fault per rule. Both sides must accept and refuse exactly the
 * same supplements.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/bright_stars_contract.json');

type Row = unknown[];
type Supplement = Record<string, unknown> & { columns: string[]; rows: Row[] };

interface BrightStarFixture {
  format: string;
  formatVersion: number;
  evidenceKind: string;
  frame: string;
  epochJulianYear: number;
  columns: string[];
  patterns: Record<string, string>;
  valid: Supplement[];
  invalid: { rule: string; table: Supplement }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as BrightStarFixture;
const col = (name: string) => (BRIGHT_STAR_COLUMNS as readonly string[]).indexOf(name);
const put = (t: Supplement, values: Record<string, unknown>) => {
  for (const [name, value] of Object.entries(values)) t.rows[0]![col(name)] = value;
};

// Each fault breaks a rule of any valid supplement of at least two rows (the same faults as the Python test's)
const FAULTS: Record<string, (t: Supplement) => void> = {
  format: (t) => void (t.format = 'albedian.bright-star-list'),
  formatVersion: (t) => void (t.formatVersion = 2),
  'evidence kind': (t) => void (t.evidenceKind = 'observed'),
  'snake-case top-level key': (t) => {
    t.format_version = t.formatVersion;
    delete t.formatVersion;
  },
  'extra field': (t) => void (t.extra = 1),
  'frame FK5': (t) => void (t.frame = 'FK5'),
  'epoch J2016.0': (t) => void (t.epochJulianYear = 2016.0),
  'blank-padded catalogue': (t) => void (t.catalogue = ' Hipparcos'),
  'column renamed': (t) => void (t.columns[0] = 'id'),
  'no rows': (t) => void (t.rows = []),
  'short row': (t) => void t.rows[0]!.pop(),
  'long row': (t) => void t.rows[0]!.push(null),
  'repeated HIP': (t) => void (t.rows[1]![0] = t.rows[0]![0]),
  'repeated tile location': (t) => {
    for (const k of [19, 20, 21]) t.rows[1]![k] = t.rows[0]![k];
  },
  'HIP 0': (t) => put(t, { hip: 0 }),
  'HIP as text': (t) => put(t, { hip: '32349' }),
  'blank-padded name': (t) => put(t, { name: 'Sirius ' }),
  'right ascension of 360': (t) => put(t, { raDeg: 360.0 }),
  'negative right ascension': (t) => put(t, { raDeg: -0.5 }),
  'declination past the pole': (t) => put(t, { decDeg: 90.5 }),
  'declination below the south pole': (t) => put(t, { decDeg: -90.5 }),
  'negative position error': (t) => put(t, { raErrorMas: -0.1 }),
  'zero parallax error': (t) => put(t, { parallaxErrorMas: 0.0 }),
  'parallax as text': (t) => put(t, { parallaxMas: '379.21' }),
  'negative proper-motion error': (t) => put(t, { pmDecErrorMasPerYr: -1.0 }),
  'V as text': (t) => put(t, { vMag: '-1.44' }),
  'fractional solution type': (t) => put(t, { solutionType: 1.5 }),
  'negative solution type': (t) => put(t, { solutionType: -1 }),
  'zero components': (t) => put(t, { components: 0 }),
  'boolean components': (t) => put(t, { components: true }),
  'Gaia source id as a number': (t) => put(t, { gaiaSourceId: 5877748442 }),
  'Gaia source id with a letter': (t) => put(t, { gaiaSourceId: '5877748442128924544x' }),
  'empty reason': (t) => put(t, { hipparcosReason: '' }),
  'negative tile layer': (t) => put(t, { tileLayer: -1 }),
  'fractional tile index': (t) => put(t, { tileIndex: 0.5 }),
};

// =============================================================================================
// Generators of valid supplements, by the same rules as the Python test's
// =============================================================================================

const floats = (min: number, max: number, excluded: { min?: boolean; max?: boolean } = {}) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true, minExcluded: excluded.min, maxExcluded: excluded.max });
const text = fc.stringMatching(/^[A-Za-z0-9][A-Za-z0-9 +'.<-]{0,10}[A-Za-z0-9)]$/);
const gaiaId = fc.stringMatching(/^[1-9][0-9]{0,18}$/);
const nil = <T>(arb: fc.Arbitrary<T>) => fc.option(arb, { nil: null });

const rowArb = (hip: number, place: [number, number, number]) =>
  fc
    .tuple(
      nil(text),
      floats(0, 360, { max: true }),
      floats(-90, 90),
      floats(0, 50),
      floats(0, 50),
      floats(-100, 800),
      floats(0, 100, { min: true }),
      floats(-4000, 4000),
      floats(-4000, 4000),
      floats(0, 50),
      floats(0, 50),
      floats(-2, 13),
      nil(floats(-2, 13)),
      nil(floats(-0.5, 3)),
      fc.integer({ min: 0, max: 165 }),
      fc.integer({ min: 1, max: 4 }),
      nil(gaiaId),
      text
    )
    .map((v): Row => [hip, ...v, ...place]);

const supplementOf = (rows: Row[]): Supplement => ({
  format: BRIGHT_STAR_SUPPLEMENT_FORMAT,
  formatVersion: BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
  evidenceKind: BRIGHT_STAR_EVIDENCE_KIND,
  catalogue: 'Hipparcos, the New Reduction (van Leeuwen 2007), with V from the Hipparcos Catalogue (ESA 1997)',
  frame: BRIGHT_STAR_FRAME,
  epochJulianYear: BRIGHT_STAR_EPOCH_JULIAN_YEAR,
  tilesRelease: 'gaia-dr3',
  columns: [...BRIGHT_STAR_COLUMNS],
  rows,
});

const validSupplement = fc
  .tuple(
    fc.uniqueArray(fc.integer({ min: 1, max: 120_000 }), { minLength: 2, maxLength: 5 }),
    fc.uniqueArray(fc.tuple(fc.integer({ min: 0, max: 5 }), fc.integer({ min: 0, max: 49_151 }), fc.integer({ min: 0, max: 2_000 })), {
      minLength: 5,
      maxLength: 5,
      selector: (p) => p.join(','),
    })
  )
  .chain(([hips, places]) => fc.tuple(...hips.map((hip, i) => rowArb(hip, places[i]!))).map(supplementOf));

describe('@albedian/contracts: the bright-star 3D supplement', () => {
  it('matches the Pydantic mirror on format, frame, epoch, columns and patterns', () => {
    expect(fixture.format).toBe(BRIGHT_STAR_SUPPLEMENT_FORMAT);
    expect(fixture.formatVersion).toBe(BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION);
    expect(fixture.evidenceKind).toBe(BRIGHT_STAR_EVIDENCE_KIND);
    expect(fixture.frame).toBe(BRIGHT_STAR_FRAME);
    expect(fixture.epochJulianYear).toBe(BRIGHT_STAR_EPOCH_JULIAN_YEAR);
    expect(fixture.columns).toEqual([...BRIGHT_STAR_COLUMNS]);
    expect(fixture.patterns).toEqual(BRIGHT_STAR_PATTERNS);
  });

  it('parses the real rows the Python side wrote, unchanged', () => {
    expect(fixture.valid).toHaveLength(1);
    const parsed = BrightStarSupplementSchema.safeParse(fixture.valid[0]);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
    expect(parsed.data).toEqual(fixture.valid[0]);
  });

  it('refuses every supplement the Python side refuses', () => {
    expect(fixture.invalid.map((c) => c.rule)).toEqual(Object.keys(FAULTS));
    for (const { rule, table: sample } of fixture.invalid) {
      expect(BrightStarSupplementSchema.safeParse(sample).success, rule).toBe(false);
    }
  });

  it('refuses numbers that JSON cannot carry but a caller can pass', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      for (const name of ['raDeg', 'parallaxMas', 'pmRaCosDecMasPerYr', 'vMag']) {
        const t = structuredClone(fixture.valid[0]!);
        put(t, { [name]: bad });
        expect(BrightStarSupplementSchema.safeParse(t).success, `${bad} at ${name}`).toBe(false);
      }
    }
  });

  it('PBT-BSS-1: the schema accepts exactly the supplements the rules allow', () => {
    const caseArb = validSupplement.chain((t) =>
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
        const parsed = BrightStarSupplementSchema.safeParse(t);
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
        // The real rows: a negative parallax, a Gaia id, nulls
        examples: [[[fixture.valid[0]!, undefined]]],
      }
    );
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
  }, 30_000);
});
