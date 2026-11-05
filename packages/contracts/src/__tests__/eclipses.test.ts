import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  ECLIPSE_CALENDARS,
  ECLIPSE_CATALOG_FORMAT,
  ECLIPSE_CATALOG_FORMAT_VERSION,
  ECLIPSE_CATALOG_YEARS,
  ECLIPSE_EVIDENCE_CLASS,
  ECLIPSE_PATTERNS,
  LUNAR_ECLIPSE_COLUMNS,
  LUNAR_ECLIPSE_QUALIFIERS,
  LUNAR_ECLIPSE_TYPES,
  LunarEclipseCatalogSchema,
  SOLAR_ECLIPSE_COLUMNS,
  SOLAR_ECLIPSE_QUALIFIERS,
  SOLAR_ECLIPSE_TYPES,
  SolarEclipseCatalogSchema,
  eclipseCalendarOf,
} from '../index.js';

/**
 * The eclipse catalogues against the samples the Python mirror wrote (services/contracts/eclipses.py,
 * tests/fixtures/eclipse_contract.json): real rows of Espenak and Meeus's catalogues, and one fault per rule.
 * Both sides must accept and refuse exactly the same catalogues.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/eclipse_contract.json');

type Kind = 'solar' | 'lunar';
type Row = unknown[];
type Catalog = Record<string, unknown> & { kind: Kind; columns: string[]; rows: Row[] };

interface EclipseFixture {
  format: string;
  formatVersion: number;
  evidenceClass: string;
  years: { first: number; last: number };
  calendars: string[];
  solarColumns: string[];
  lunarColumns: string[];
  solarTypes: string[];
  solarQualifiers: string[];
  lunarTypes: string[];
  lunarQualifiers: string[];
  patterns: Record<string, string>;
  valid: Record<Kind, Catalog[]>;
  invalid: { kind: Kind; rule: string; catalog: Catalog }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as EclipseFixture;
const SCHEMAS = { solar: SolarEclipseCatalogSchema, lunar: LunarEclipseCatalogSchema };
const COLUMNS: Record<Kind, readonly string[]> = { solar: SOLAR_ECLIPSE_COLUMNS, lunar: LUNAR_ECLIPSE_COLUMNS };

// =============================================================================================
// The calendar oracle (Meeus, Astronomical Algorithms, chapter 7), independent of the contract's rule
// =============================================================================================

const JDN_FIRST = 990924; // -1999 January 1, Julian calendar
const JDN_LAST = 2817152; // 3000 December 31, Gregorian calendar
const GREGORIAN_FROM_JDN = 2299161; // 1582 October 15

/** The civil date whose noon is Julian Day Number jdn, in NASA's calendars, with astronomical year numbers. */
function dateOfJdn(jdn: number): [number, number, number, string] {
  let a = jdn;
  let calendar = 'julian';
  if (jdn >= GREGORIAN_FROM_JDN) {
    const alpha = Math.floor((jdn - 1867216.25) / 36524.25);
    a = jdn + 1 + alpha - Math.floor(alpha / 4);
    calendar = 'gregorian';
  }
  const b = a + 1524;
  const c = Math.floor((b - 122.1) / 365.25);
  const d = Math.floor(365.25 * c);
  const e = Math.floor((b - d) / 30.6001);
  const day = b - d - Math.floor(30.6001 * e);
  const month = e < 14 ? e - 1 : e - 13;
  const year = month > 2 ? c - 4716 : c - 4715;
  return [year, month, day, calendar];
}

function jdnOfNewYear(year: number): number {
  const y = year - 1;
  const b = year <= 1582 ? 0 : 2 - Math.floor(y / 100) + Math.floor(Math.floor(y / 100) / 4);
  return Math.floor(365.25 * (y + 4716)) + Math.floor(30.6001 * 14) + 1 + b - 1524;
}

const SWEEP_YEARS = [
  ...Array.from({ length: 9 }, (_, i) => -1999 + i),
  -5, -4, -1, 0, 1, 4, 100, 1580, 1581, 1582, 1583, 1584, 1600, 1700, 1900, 2000, 2024, 2100, 2999, 3000,
]; // prettier-ignore

// =============================================================================================
// Faults: each breaks a rule of any valid catalogue of at least two rows (as in the Python test)
// =============================================================================================

const index = (c: Catalog, name: string) => COLUMNS[c.kind].indexOf(name);
const setFirst = (c: Catalog, fields: Record<string, unknown>) => {
  for (const [name, value] of Object.entries(fields)) c.rows[0]![index(c, name)] = value;
};
const lunarPhases = (
  c: Catalog,
  eclipseType: string,
  typeQualifier: string | null,
  umbralMagnitude: number,
  penumbralMagnitude: number,
  [penumbral, partial, total]: [number, number | null, number | null]
) =>
  setFirst(c, {
    eclipseType,
    typeQualifier,
    umbralMagnitude,
    penumbralMagnitude,
    penumbralDurationMinutes: penumbral,
    partialDurationMinutes: partial,
    totalDurationMinutes: total,
  });

const COMMON_FAULTS: Record<string, (c: Catalog) => void> = {
  format: (c) => void (c.format = 'albedian.eclipse-table'),
  formatVersion: (c) => void (c.formatVersion = 2),
  'evidence class observed': (c) => void (c.evidenceClass = 'observed'),
  'extra field': (c) => void (c.extra = 1),
  'column renamed': (c) => void (c.columns[0] = 'number'),
  "other kind's columns": (c) => void (c.columns = [...COLUMNS[c.kind === 'solar' ? 'lunar' : 'solar']]),
  'no rows': (c) => void (c.rows = []),
  'short row': (c) => void c.rows[0]!.pop(),
  'long row': (c) => void c.rows[0]!.push(null),
  'catalogue number zero': (c) => setFirst(c, { catalogNumber: 0 }),
  'number written as text': (c) => setFirst(c, { catalogNumber: String(c.rows[0]![0]) }),
  'boolean for a number': (c) => setFirst(c, { gamma: true }),
  'text for a number': (c) => setFirst(c, { gamma: '0.5' }),
  'year before the catalogue': (c) => setFirst(c, { year: -2000, calendar: 'julian' }),
  'month 13': (c) => setFirst(c, { month: 13 }),
  '30 February': (c) => setFirst(c, { month: 2, day: 30 }),
  '29 February in a Gregorian common year': (c) => setFirst(c, { year: 1900, month: 2, day: 29, calendar: 'gregorian' }),
  'a day the calendar reform dropped': (c) => setFirst(c, { year: 1582, month: 10, day: 10, calendar: 'gregorian' }),
  'calendar that does not match the date': (c) =>
    setFirst(c, { calendar: c.rows[0]![index(c, 'calendar')] === 'julian' ? 'gregorian' : 'julian' }),
  'unknown calendar': (c) => setFirst(c, { calendar: 'proleptic' }),
  'time of day past midnight': (c) => setFirst(c, { greatestEclipseTdSeconds: 86400 }),
  'fractional seconds': (c) => setFirst(c, { greatestEclipseTdSeconds: 10.5 }),
  'unknown type': (c) => setFirst(c, { eclipseType: 'X' }),
  'unknown qualifier': (c) => setFirst(c, { typeQualifier: 'q' }),
  'rows out of order': (c) => void ([c.rows[0], c.rows[1]] = [c.rows[1]!, c.rows[0]!]),
  'repeated catalogue number': (c) => void (c.rows[1]![0] = c.rows[0]![0]),
  'two eclipses at one instant': (c) => {
    for (const name of ['year', 'month', 'day', 'calendar', 'greatestEclipseTdSeconds']) {
      c.rows[1]![index(c, name)] = c.rows[0]![index(c, name)];
    }
  },
};

const SOLAR_FAULTS: Record<string, (c: Catalog) => void> = {
  'negative magnitude': (c) => setFirst(c, { magnitude: -0.0001 }),
  'latitude past the pole': (c) => setFirst(c, { latitudeDeg: 90.1 }),
  'longitude past the antimeridian': (c) => setFirst(c, { longitudeDeg: -180.1 }),
  'Sun below the horizon': (c) => setFirst(c, { sunAltitudeDeg: -1 }),
  'azimuth past 360': (c) => setFirst(c, { sunAzimuthDeg: 361 }),
  'fractional azimuth': (c) => setFirst(c, { sunAzimuthDeg: 12.5 }),
  'lunar quincena letter': (c) => setFirst(c, { quincenaLunarEclipse: 'a-' }),
  'negative path width': (c) =>
    setFirst(c, { eclipseType: 'T', typeQualifier: null, sunAltitudeDeg: 50, pathWidthKm: -1, centralDurationSeconds: 100 }),
  'partial eclipse with a path width': (c) =>
    setFirst(c, { eclipseType: 'P', typeQualifier: null, sunAltitudeDeg: 0, pathWidthKm: 100, centralDurationSeconds: null }),
  'partial eclipse with a central duration': (c) =>
    setFirst(c, { eclipseType: 'P', typeQualifier: null, sunAltitudeDeg: 0, pathWidthKm: null, centralDurationSeconds: 60 }),
  'partial eclipse with the Sun up': (c) =>
    setFirst(c, { eclipseType: 'P', typeQualifier: null, sunAltitudeDeg: 10, pathWidthKm: null, centralDurationSeconds: null }),
  'non-central eclipse with the Sun up': (c) =>
    setFirst(c, { eclipseType: 'A', typeQualifier: '-', sunAltitudeDeg: 10, pathWidthKm: null, centralDurationSeconds: null }),
  'hybrid mark on a total eclipse': (c) =>
    setFirst(c, { eclipseType: 'T', typeQualifier: '2', sunAltitudeDeg: 50, pathWidthKm: 100, centralDurationSeconds: 100 }),
  'series start on a central eclipse': (c) =>
    setFirst(c, { eclipseType: 'T', typeQualifier: 'b', sunAltitudeDeg: 50, pathWidthKm: 100, centralDurationSeconds: 100 }),
  'non-central mark on a partial eclipse': (c) =>
    setFirst(c, { eclipseType: 'P', typeQualifier: '+', sunAltitudeDeg: 0, pathWidthKm: null, centralDurationSeconds: null }),
  'total penumbral mark on a solar eclipse': (c) => setFirst(c, { typeQualifier: 'x' }),
};

const LUNAR_FAULTS: Record<string, (c: Catalog) => void> = {
  'zero penumbral magnitude': (c) => lunarPhases(c, 'N', null, -0.5, 0.0, [100.0, null, null]),
  'zero penumbral duration': (c) => lunarPhases(c, 'N', null, -0.5, 0.5, [0.0, null, null]),
  'zenith latitude past the pole': (c) => setFirst(c, { zenithLatitudeDeg: 91 }),
  'fractional zenith longitude': (c) => setFirst(c, { zenithLongitudeDeg: 12.5 }),
  'solar quincena letter': (c) => setFirst(c, { quincenaSolarEclipse: 'n-' }),
  'penumbral eclipse with a partial phase': (c) => lunarPhases(c, 'N', null, -0.1, 0.5, [100.0, 50.0, null]),
  'partial eclipse without a partial phase': (c) => lunarPhases(c, 'P', null, 0.5, 1.5, [200.0, null, null]),
  'partial eclipse with a total phase': (c) => lunarPhases(c, 'P', null, 0.5, 1.5, [200.0, 100.0, 50.0]),
  'total eclipse without a total phase': (c) => lunarPhases(c, 'T', null, 1.2, 2.2, [300.0, 200.0, null]),
  'penumbral eclipse touching the umbra': (c) => lunarPhases(c, 'N', null, 0.1, 0.9, [200.0, null, null]),
  'partial eclipse at full umbral immersion': (c) => lunarPhases(c, 'P', null, 1.0, 2.0, [300.0, 200.0, null]),
  'total eclipse short of full immersion': (c) => lunarPhases(c, 'T', null, 0.99, 2.0, [300.0, 200.0, 50.0]),
  'partial phase longer than the penumbral': (c) => lunarPhases(c, 'P', null, 0.5, 1.5, [100.0, 150.0, null]),
  'total phase longer than the partial': (c) => lunarPhases(c, 'T', null, 1.2, 2.2, [300.0, 100.0, 150.0]),
  'total penumbral mark on a partial eclipse': (c) => lunarPhases(c, 'P', 'x', 0.5, 1.5, [200.0, 100.0, null]),
  'total penumbral mark short of full immersion': (c) => lunarPhases(c, 'N', 'x', -0.1, 0.9, [200.0, null, null]),
  'central mark on a penumbral eclipse': (c) => lunarPhases(c, 'N', '+', -0.1, 0.9, [200.0, null, null]),
  'series start on a total eclipse': (c) => lunarPhases(c, 'T', 'b', 1.2, 2.2, [300.0, 200.0, 50.0]),
  'hybrid mark on a lunar eclipse': (c) => setFirst(c, { typeQualifier: '2' }),
};

const FAULTS: Record<Kind, Record<string, (c: Catalog) => void>> = {
  solar: { ...COMMON_FAULTS, ...SOLAR_FAULTS },
  lunar: { ...COMMON_FAULTS, ...LUNAR_FAULTS },
};

// =============================================================================================
// Generators of valid catalogues, by the same rules as the Python test's
// =============================================================================================

const floats = (min: number, max: number, excluded: { min?: boolean; max?: boolean } = {}) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true, minExcluded: excluded.min, maxExcluded: excluded.max });

const SOLAR_QUALIFIERS_BY_TYPE: Record<string, (string | null)[]> = {
  P: [null, 'm', 'b', 'e'],
  A: [null, 'm', 'n', 's', '+', '-'],
  T: [null, 'm', 'n', 's', '+', '-'],
  H: [null, 'm', 'n', 's', '+', '-', '2', '3'],
};
const LUNAR_QUALIFIERS_BY_TYPE: Record<string, (string | null)[]> = {
  N: [null, 'm', 'x', 'b', 'e'],
  P: [null, 'm'],
  T: [null, 'm', '+', '-'],
};

const solarRow = (number: number, jdn: number) =>
  fc
    .constantFrom(...SOLAR_ECLIPSE_TYPES)
    .chain((type) => fc.tuple(fc.constant(type), fc.constantFrom(...SOLAR_QUALIFIERS_BY_TYPE[type]!)))
    .chain(([type, qualifier]) => {
      const partial = type === 'P';
      const sunOnHorizon = partial || qualifier === '+' || qualifier === '-';
      return fc
        .record({
          plate: fc.integer({ min: 1, max: 600 }),
          seconds: fc.integer({ min: 0, max: 86399 }),
          deltaT: fc.integer({ min: -10, max: 50000 }),
          lunation: fc.integer({ min: -60000, max: 15000 }),
          saros: fc.integer({ min: -20, max: 200 }),
          quincena: fc.stringMatching(/^[-npt]{2}$/),
          gamma: floats(-1.6, 1.6),
          magnitude: floats(0, 1.1),
          latitude: floats(-90, 90),
          longitude: floats(-180, 180),
          altitude: sunOnHorizon ? fc.constant(0) : fc.integer({ min: 0, max: 90 }),
          azimuth: fc.integer({ min: 0, max: 360 }),
          width: partial ? fc.constant(null) : fc.option(fc.integer({ min: 0, max: 1500 }), { nil: null }),
          duration: partial ? fc.constant(null) : fc.option(fc.integer({ min: 0, max: 800 }), { nil: null }),
        })
        .map((v) => {
          const [year, month, day, calendar] = dateOfJdn(jdn);
          return [
            number, v.plate, year, month, day, calendar, v.seconds, v.deltaT, v.lunation, v.saros, type, qualifier,
            v.quincena, v.gamma, v.magnitude, v.latitude, v.longitude, v.altitude, v.azimuth, v.width, v.duration,
          ] as Row; // prettier-ignore
        });
    });

const lunarRow = (number: number, jdn: number) =>
  fc
    .constantFrom(...LUNAR_ECLIPSE_TYPES)
    .chain((type) => fc.tuple(fc.constant(type), fc.constantFrom(...LUNAR_QUALIFIERS_BY_TYPE[type]!), floats(1, 400)))
    .chain(([type, qualifier, penumbralMinutes]) => {
      const phases =
        type === 'N'
          ? fc.tuple(floats(-1.2, 0), qualifier === 'x' ? floats(1, 1.2) : floats(0, 1.2, { min: true }), fc.constant(null), fc.constant(null))
          : type === 'P'
            ? fc.tuple(floats(0, 1, { min: true, max: true }), floats(0.5, 3), floats(0.1, penumbralMinutes, { max: true }), fc.constant(null))
            : floats(0.1, penumbralMinutes, { max: true }).chain((partial) =>
                fc.tuple(floats(1, 1.9), floats(1.5, 3), fc.constant(partial), floats(0.01, partial, { max: true }))
              );
      return fc
        .record({
          seconds: fc.integer({ min: 0, max: 86399 }),
          deltaT: fc.integer({ min: -10, max: 50000 }),
          lunation: fc.integer({ min: -60000, max: 15000 }),
          saros: fc.integer({ min: -25, max: 200 }),
          quincena: fc.stringMatching(/^[-apth]{2}$/),
          gamma: floats(-1.6, 1.6),
          phases,
          zenithLatitude: fc.integer({ min: -90, max: 90 }),
          zenithLongitude: fc.integer({ min: -180, max: 180 }),
        })
        .map((v) => {
          const [year, month, day, calendar] = dateOfJdn(jdn);
          const [umbral, penumbral, partial, total] = v.phases;
          return [
            number, year, month, day, calendar, v.seconds, v.deltaT, v.lunation, v.saros, type, qualifier, v.quincena,
            v.gamma, penumbral, umbral, penumbralMinutes, partial, total, v.zenithLatitude, v.zenithLongitude,
          ] as Row; // prettier-ignore
        });
    });

const catalog = (kind: Kind, rows: Row[]): Catalog => ({
  format: ECLIPSE_CATALOG_FORMAT,
  formatVersion: ECLIPSE_CATALOG_FORMAT_VERSION,
  kind,
  evidenceClass: ECLIPSE_EVIDENCE_CLASS,
  columns: [...COLUMNS[kind]],
  rows,
});

const validCatalog = fc.constantFrom<Kind>('solar', 'lunar').chain((kind) =>
  fc.integer({ min: 2, max: 6 }).chain((size) =>
    fc
      .tuple(
        fc.uniqueArray(fc.integer({ min: JDN_FIRST, max: JDN_LAST }), { minLength: size, maxLength: size }),
        fc.integer({ min: 1, max: 50 }),
        fc.array(fc.integer({ min: 1, max: 9 }), { minLength: size - 1, maxLength: size - 1 })
      )
      .chain(([days, first, steps]) => {
        const sorted = [...days].sort((a, b) => a - b);
        const numbers = steps.reduce((acc, step) => [...acc, acc[acc.length - 1]! + step], [first]);
        const row = kind === 'solar' ? solarRow : lunarRow;
        return fc.tuple(...sorted.map((day, i) => row(numbers[i]!, day))).map((rows) => catalog(kind, rows));
      })
  )
);

describe('@albedian/contracts: eclipse catalogues', () => {
  it('matches the Pydantic mirror on format, columns, codes, years and patterns', () => {
    expect(fixture.format).toBe(ECLIPSE_CATALOG_FORMAT);
    expect(fixture.formatVersion).toBe(ECLIPSE_CATALOG_FORMAT_VERSION);
    expect(fixture.evidenceClass).toBe(ECLIPSE_EVIDENCE_CLASS);
    expect(fixture.years).toEqual(ECLIPSE_CATALOG_YEARS);
    expect(fixture.calendars).toEqual([...ECLIPSE_CALENDARS]);
    expect(fixture.solarColumns).toEqual([...SOLAR_ECLIPSE_COLUMNS]);
    expect(fixture.lunarColumns).toEqual([...LUNAR_ECLIPSE_COLUMNS]);
    expect(fixture.solarTypes).toEqual([...SOLAR_ECLIPSE_TYPES]);
    expect(fixture.solarQualifiers).toEqual([...SOLAR_ECLIPSE_QUALIFIERS]);
    expect(fixture.lunarTypes).toEqual([...LUNAR_ECLIPSE_TYPES]);
    expect(fixture.lunarQualifiers).toEqual([...LUNAR_ECLIPSE_QUALIFIERS]);
    expect(fixture.patterns).toEqual(ECLIPSE_PATTERNS);
  });

  it('parses every real catalogue the Python side wrote, unchanged', () => {
    for (const kind of ['solar', 'lunar'] as const) {
      expect(fixture.valid[kind].length).toBeGreaterThan(0);
      for (const sample of fixture.valid[kind]) {
        const parsed = SCHEMAS[kind].safeParse(sample);
        expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
        expect(parsed.data).toEqual(sample);
      }
    }
  });

  it('refuses every catalogue the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThan(70);
    for (const { kind, rule, catalog: sample } of fixture.invalid) {
      expect(SCHEMAS[kind].safeParse(sample).success, `${kind}: ${rule}`).toBe(false);
    }
  });

  it('refuses numbers that JSON cannot carry but a caller can pass', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      const solar = structuredClone(fixture.valid.solar[0]!);
      solar.rows[0]![SOLAR_ECLIPSE_COLUMNS.indexOf('gamma')] = bad;
      expect(SolarEclipseCatalogSchema.safeParse(solar).success, `solar gamma ${bad}`).toBe(false);
      const lunar = structuredClone(fixture.valid.lunar[0]!);
      lunar.rows[0]![LUNAR_ECLIPSE_COLUMNS.indexOf('penumbralDurationMinutes')] = bad;
      expect(LunarEclipseCatalogSchema.safeParse(lunar).success, `lunar penumbral phase ${bad}`).toBe(false);
    }
  });

  it('knows which calendar NASA uses for each day, and which days do not exist', () => {
    for (const year of SWEEP_YEARS) {
      const days = new Map<string, string>();
      let jdn = jdnOfNewYear(year);
      expect(dateOfJdn(jdn).slice(0, 3)).toEqual([year, 1, 1]);
      for (let date = dateOfJdn(jdn); date[0] === year; date = dateOfJdn(++jdn)) days.set(`${date[1]}-${date[2]}`, date[3]);
      for (let month = 1; month <= 12; month++) {
        for (let day = 1; day <= 31; day++) {
          expect(eclipseCalendarOf(year, month, day), `${year}-${month}-${day}`).toBe(days.get(`${month}-${day}`) ?? null);
        }
      }
    }
  });

  it('PBT-ECL-1: the schemas accept exactly the catalogues the rules allow', () => {
    const caseArb = validCatalog.chain((c) =>
      fc.option(fc.constantFrom(...Object.keys(FAULTS[c.kind])), { nil: undefined, freq: 2 }).map((fault) => {
        const copy = structuredClone(c);
        if (fault !== undefined) FAULTS[c.kind][fault]!(copy);
        return [copy, fault] as const;
      })
    );
    let accepted = 0;
    let rejected = 0;
    fc.assert(
      fc.property(caseArb, ([c, fault]) => {
        const parsed = SCHEMAS[c.kind].safeParse(c);
        expect(parsed.success, `${fault ?? 'no fault'}: ${JSON.stringify(parsed.error?.issues)}`).toBe(fault === undefined);
        if (parsed.success) {
          accepted++;
          expect(parsed.data).toEqual(c);
        } else {
          rejected++;
        }
      }),
      {
        numRuns: 300,
        seed: SEED,
        // The real rows (every type, the 1582 reform) and the last Julian and first Gregorian rows alone
        examples: [
          [[fixture.valid.solar[0]!, undefined]],
          [[fixture.valid.lunar[0]!, undefined]],
          [[catalog('solar', fixture.valid.solar[0]!.rows.slice(5, 7)), undefined]],
        ],
      }
    );
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
    // Well under a second alone; the margin covers a loaded machine running the whole suite
  }, 30_000);
});
