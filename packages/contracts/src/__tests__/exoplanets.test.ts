import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  EXOPLANET_CALCULATED_REFSTR,
  EXOPLANET_COLUMNS,
  EXOPLANET_EVIDENCE_KIND,
  EXOPLANET_MASS_PROVENANCES,
  EXOPLANET_MODELLED_COLUMNS,
  EXOPLANET_PATTERNS,
  EXOPLANET_REQUIRED_NOTES,
  EXOPLANET_TABLE_FORMAT,
  EXOPLANET_TABLE_FORMAT_VERSION,
  EXOPLANET_VALUE_BASES,
  EXOPLANET_VALUE_COLUMNS,
  ExoplanetTableSchema,
} from '../index.js';

/**
 * The exoplanet table against the samples the Python mirror wrote (services/contracts/exoplanets.py,
 * tests/fixtures/exoplanet_contract.json): six real PSCompPars rows, and one fault per rule. Both sides must
 * accept and refuse exactly the same tables.
 */
const PYTHON_FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/exoplanet_contract.json');

type Cell = [number, number | null, number | null, number | null, number, string] | null;
type Row = unknown[];
type Reference = { refstr: string; label: string; url: string } & Record<string, unknown>;
type Table = Record<string, unknown> & {
  columns: string[];
  notes: Record<string, string>;
  references: Reference[];
  rows: Row[];
};

interface ExoplanetFixture {
  format: string;
  formatVersion: number;
  evidenceKind: string;
  columns: string[];
  valueColumns: string[];
  modelledColumns: string[];
  requiredNotes: string[];
  valueBases: string[];
  massProvenances: string[];
  calculatedRefstr: string;
  patterns: Record<string, string>;
  valid: Table[];
  invalid: { rule: string; table: Table }[];
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const SAMPLE_NOTE = "Contract test sample; not the archive's own wording.";
const CALCULATION_PAGE = 'https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html';

const fixture = JSON.parse(fs.readFileSync(PYTHON_FIXTURE, 'utf8')) as ExoplanetFixture;
const col = (name: string) => (EXOPLANET_COLUMNS as readonly string[]).indexOf(name);
const FIRST_VALUE_COLUMN = EXOPLANET_COLUMNS.length - EXOPLANET_VALUE_COLUMNS.length;

const calcIndex = (t: Table) => t.references.findIndex((ref) => ref.refstr === EXOPLANET_CALCULATED_REFSTR);
const paperIndex = (t: Table) => t.references.findIndex((ref) => ref.refstr !== EXOPLANET_CALCULATED_REFSTR);
const firstValueCell = (t: Table) => t.rows[0]!.slice(FIRST_VALUE_COLUMN).find((cell) => cell !== null) as unknown[];
const setCell = (t: Table, name: string, value: number, ref: number, basis: string, provenance?: string) => {
  t.rows[0]![col(name)] = [value, null, null, 0, ref, basis];
  if (provenance !== undefined) t.rows[0]![col('massProvenance')] = provenance;
};

// Each fault breaks a rule of any valid table of at least two rows, one of them citing the calculation page
const FAULTS: Record<string, (t: Table) => void> = {
  format: (t) => void (t.format = 'albedian.exoplanet-list'),
  formatVersion: (t) => void (t.formatVersion = 2),
  'evidence kind': (t) => void (t.evidenceKind = 'observed'),
  'extra field': (t) => void (t.extra = 1),
  'column renamed': (t) => void (t.columns[0] = 'planet'),
  'no rows': (t) => void (t.rows = []),
  'short row': (t) => void t.rows[0]!.pop(),
  'long row': (t) => void t.rows[0]!.push(null),
  'temperature note missing': (t) => void (t.notes = {}),
  'note for an unknown column': (t) => void (t.notes.colour = 'Not a column.'),
  'blank-padded note': (t) => void (t.notes.equilibriumTemperatureK = ' Padded.'),
  'lower-case refstr': (t) => void (t.references[0]!.refstr = 'lower_case_2020'),
  'reference over plain http': (t) => void (t.references[0]!.url = 'http://ui.adsabs.harvard.edu/abs/x'),
  'reference without a host': (t) => void (t.references[0]!.url = 'https:///abs/x'),
  'blank-padded label': (t) => void (t.references[0]!.label = ' Padded et al. 2020'),
  'extra reference field': (t) => void (t.references[0]!.year = 2020),
  'repeated reference': (t) => void t.references.push(structuredClone(t.references[0]!)),
  'repeated reference with both copies cited': (t) => {
    // List the most-cited reference a second time and move one of its citations to the copy, so both are cited
    const cells = t.rows.flatMap((row) => row.slice(FIRST_VALUE_COLUMN).filter((cell) => cell !== null) as unknown[][]);
    const counts = new Map<number, number>();
    for (const cell of cells) counts.set(cell[4] as number, (counts.get(cell[4] as number) ?? 0) + 1);
    const [ref, citations] = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0]!;
    if (citations < 2) throw new Error('the fault needs a reference cited twice');
    t.references.push(structuredClone(t.references[ref]!));
    cells.find((cell) => cell[4] === ref)![4] = t.references.length - 1;
  },
  'unused reference': (t) =>
    void t.references.push({ refstr: 'UNUSED_2026', label: 'Unused 2026', url: 'https://example.org/unused' }),
  'reference index past the end': (t) => void (firstValueCell(t)[4] = t.references.length),
  'negative reference index': (t) => void (firstValueCell(t)[4] = -1),
  'repeated planet name': (t) => void (t.rows[1]![0] = t.rows[0]![0]),
  'blank-padded planet name': (t) => void (t.rows[0]![0] = `${String(t.rows[0]![0])} `),
  'zero stars': (t) => void (t.rows[0]![col('starCount')] = 0),
  'planet count as text': (t) => void (t.rows[0]![col('planetCount')] = '1'),
  'fractional discovery year': (t) => void (t.rows[0]![col('discoveryYear')] = 2011.5),
  'unknown mass provenance': (t) => void (t.rows[0]![col('massProvenance')] = 'Mass?'),
  'unknown basis': (t) => void (firstValueCell(t)[5] = 'estimated'),
  'positive lower uncertainty': (t) => void (firstValueCell(t)[2] = 0.5),
  'negative upper uncertainty': (t) => void (firstValueCell(t)[1] = -0.5),
  'limit flag 2': (t) => void (firstValueCell(t)[3] = 2),
  'boolean limit flag': (t) => void (firstValueCell(t)[3] = true),
  'value cell too short': (t) => void firstValueCell(t).pop(),
  'value as text': (t) => void (firstValueCell(t)[0] = '1.5'),
  'negative orbital period': (t) => setCell(t, 'orbitalPeriodDays', -1.0, paperIndex(t), 'measured'),
  'eccentricity of 1': (t) => setCell(t, 'eccentricity', 1.0, paperIndex(t), 'measured'),
  'right ascension of 360': (t) => setCell(t, 'raDeg', 360.0, paperIndex(t), 'measured'),
  'declination past the pole': (t) => setCell(t, 'decDeg', 90.5, paperIndex(t), 'measured'),
  'zero distance': (t) => setCell(t, 'distancePc', 0.0, paperIndex(t), 'measured'),
  'measured value citing the calculation page': (t) => setCell(t, 'orbitalPeriodDays', 3.0, calcIndex(t), 'measured'),
  'calculated value citing a paper': (t) => setCell(t, 'equilibriumTemperatureK', 900.0, paperIndex(t), 'calculated'),
  'modelled temperature': (t) => setCell(t, 'equilibriumTemperatureK', 900.0, calcIndex(t), 'modelled'),
  'calculated radius': (t) => setCell(t, 'radiusEarth', 1.5, calcIndex(t), 'calculated'),
  'modelled mass without the M-R provenance': (t) => setCell(t, 'massEarth', 3.0, calcIndex(t), 'modelled', 'Mass'),
  'M-R provenance on a measured mass': (t) =>
    setCell(t, 'massEarth', 3.0, paperIndex(t), 'measured', 'M-R relationship'),
};

// =============================================================================================
// Generators of valid tables, by the same rules as the Python test's
// =============================================================================================

const POSITIVE = new Set([
  'orbitalPeriodDays', 'semiMajorAxisAu', 'radiusEarth', 'massEarth', 'equilibriumTemperatureK',
  'hostEffectiveTemperatureK', 'hostRadiusSolar', 'hostMassSolar', 'distancePc',
]); // prettier-ignore

const floats = (min: number, max: number, excluded: { min?: boolean; max?: boolean } = {}) =>
  fc.double({ min, max, noNaN: true, noDefaultInfinity: true, minExcluded: excluded.min, maxExcluded: excluded.max });

const valueOf = (name: string) =>
  POSITIVE.has(name)
    ? floats(0, 1e9, { min: true })
    : name === 'eccentricity'
      ? floats(0, 1, { max: true })
      : name === 'raDeg'
        ? floats(0, 360, { max: true })
        : name === 'decDeg'
          ? floats(-90, 90)
          : floats(-30, 50);

const valueCell = (name: string, papers: number[], calc: number) =>
  fc
    .record({
      derived: fc.boolean(),
      paper: fc.constantFrom(...papers),
      value: valueOf(name),
      err1: fc.option(floats(0, 1e6), { nil: null }),
      err2: fc.option(floats(-1e6, 0), { nil: null }),
      limit: fc.constantFrom(null, -1, 0, 1),
    })
    .map(({ derived, paper, value, err1, err2, limit }): Cell => {
      const modelled = (EXOPLANET_MODELLED_COLUMNS as readonly string[]).includes(name);
      return derived
        ? [value, err1, err2, limit, calc, modelled ? 'modelled' : 'calculated']
        : [value, err1, err2, limit, paper, 'measured'];
    });

const table = (rows: Row[], references: Reference[]): Table => ({
  format: EXOPLANET_TABLE_FORMAT,
  formatVersion: EXOPLANET_TABLE_FORMAT_VERSION,
  evidenceKind: EXOPLANET_EVIDENCE_KIND,
  columns: [...EXOPLANET_COLUMNS],
  notes: { equilibriumTemperatureK: SAMPLE_NOTE },
  references,
  rows,
});

const validTable = fc.integer({ min: 1, max: 4 }).chain((paperCount) =>
  fc
    .tuple(
      fc.uniqueArray(
        fc.stringMatching(/^[A-Z][A-Z0-9_]{0,24}$/).filter((s) => s !== EXOPLANET_CALCULATED_REFSTR),
        { minLength: paperCount, maxLength: paperCount }
      ),
      fc.integer({ min: 2, max: 5 })
    )
    .chain(([refstrs, rowCount]) => {
      const references: Reference[] = [
        { refstr: EXOPLANET_CALCULATED_REFSTR, label: 'Calculated Value', url: CALCULATION_PAGE },
        ...refstrs.map((refstr, i) => ({ refstr, label: `Paper ${i}`, url: `https://ui.adsabs.harvard.edu/abs/${i}` })),
      ];
      const papers = refstrs.map((_, i) => i + 1);
      const row = (number: number) =>
        fc
          .record({
            stars: fc.integer({ min: 1, max: 4 }),
            planets: fc.integer({ min: 1, max: 8 }),
            method: fc.constantFrom('Transit', 'Radial Velocity', 'Imaging'),
            year: fc.integer({ min: 1992, max: 2026 }),
            facility: fc.constantFrom('Kepler', 'K2', 'W. M. Keck Observatory'),
            cells: fc.tuple(...EXOPLANET_VALUE_COLUMNS.map((name) => fc.option(valueCell(name, papers, 0), { nil: null }))),
            measuredProvenance: fc.constantFrom('Mass', 'Msini', 'Msin(i)/sin(i)'),
            anyProvenance: fc.constantFrom(null, ...EXOPLANET_MASS_PROVENANCES),
          })
          .map((v): Row => {
            const mass = v.cells[(EXOPLANET_VALUE_COLUMNS as readonly string[]).indexOf('massEarth')];
            const provenance = mass === null ? v.anyProvenance : mass![5] === 'modelled' ? 'M-R relationship' : v.measuredProvenance;
            return [`Planet ${number} b`, `Host ${number}`, v.stars, v.planets, v.method, v.year, v.facility, provenance, ...v.cells];
          });
      return fc.tuple(...Array.from({ length: rowCount }, (_, i) => row(i))).map((rows) => {
        // Every reference is cited: the calculation page by the first two rows' temperatures (so a reference is cited
        // twice, as one fault needs), each paper by a period (or, past the last row, by a host temperature)
        rows[0]![col('equilibriumTemperatureK')] = [500.0, null, null, 0, 0, 'calculated'];
        rows[1]![col('equilibriumTemperatureK')] = [600.0, null, null, 0, 0, 'calculated'];
        for (const paper of papers) {
          const target = rows[(paper - 1) % rows.length]!;
          target[col(paper > rows.length ? 'hostEffectiveTemperatureK' : 'orbitalPeriodDays')] = [3.0, null, null, 0, paper, 'measured'];
        }
        return table(rows, references);
      });
    })
);

describe('@albedian/contracts: the exoplanet table', () => {
  it('matches the Pydantic mirror on format, columns, codes and patterns', () => {
    expect(fixture.format).toBe(EXOPLANET_TABLE_FORMAT);
    expect(fixture.formatVersion).toBe(EXOPLANET_TABLE_FORMAT_VERSION);
    expect(fixture.evidenceKind).toBe(EXOPLANET_EVIDENCE_KIND);
    expect(fixture.columns).toEqual([...EXOPLANET_COLUMNS]);
    expect(fixture.valueColumns).toEqual([...EXOPLANET_VALUE_COLUMNS]);
    expect(fixture.modelledColumns).toEqual([...EXOPLANET_MODELLED_COLUMNS]);
    expect(fixture.requiredNotes).toEqual([...EXOPLANET_REQUIRED_NOTES]);
    expect(fixture.valueBases).toEqual([...EXOPLANET_VALUE_BASES]);
    expect(fixture.massProvenances).toEqual([...EXOPLANET_MASS_PROVENANCES]);
    expect(fixture.calculatedRefstr).toBe(EXOPLANET_CALCULATED_REFSTR);
    expect(fixture.patterns).toEqual(EXOPLANET_PATTERNS);
  });

  it('parses the real rows the Python side wrote, unchanged', () => {
    expect(fixture.valid).toHaveLength(1);
    const parsed = ExoplanetTableSchema.safeParse(fixture.valid[0]);
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true);
    expect(parsed.data).toEqual(fixture.valid[0]);
  });

  it('refuses every table the Python side refuses', () => {
    expect(fixture.invalid.length).toBeGreaterThan(40);
    for (const { rule, table: sample } of fixture.invalid) {
      expect(ExoplanetTableSchema.safeParse(sample).success, rule).toBe(false);
    }
  });

  it('refuses numbers that JSON cannot carry but a caller can pass', () => {
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY]) {
      for (const position of [0, 1]) {
        const t = structuredClone(fixture.valid[0]!);
        firstValueCell(t)[position] = bad;
        expect(ExoplanetTableSchema.safeParse(t).success, `${bad} at ${position}`).toBe(false);
      }
    }
  });

  it('PBT-EXO-1: the schema accepts exactly the tables the rules allow', () => {
    const caseArb = validTable.chain((t) =>
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
        const parsed = ExoplanetTableSchema.safeParse(t);
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
        // The real rows: measured, modelled and calculated values, limits, a provenance without a mass
        examples: [[[fixture.valid[0]!, undefined]]],
      }
    );
    expect(accepted).toBeGreaterThan(30);
    expect(rejected).toBeGreaterThan(30);
    // About a second alone, but slower than Vitest's 5 s default when the whole suite runs at once
  }, 30_000);
});
