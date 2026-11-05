import { z } from 'zod';

/**
 * Eclipse catalogues (roadmap Track 2 M2): Fred Espenak and Jean Meeus's Five Millennium Catalogs of Solar and
 * Lunar Eclipses, -1999 to +3000 (NASA's Goddard Space Flight Center), as scripts/bake_eclipses.py writes them to
 * apps/web/public/catalog/eclipses/. Every eclipse in them is computed, not observed, so a catalogue's evidence
 * class is `predicted`. NASA's key to the catalogues names delta T, the drift of Earth's rotation, as the largest
 * uncertainty in the predictions.
 *
 * A catalogue is {format, formatVersion, kind, evidenceClass, columns, rows}. Each row is a tuple in the order
 * `columns` names, and a value the catalogue leaves blank is null. Dates are NASA's: the Julian calendar up to
 * 1582 October 4 and the Gregorian from 1582 October 15, with years numbered astronomically (year 0 is 1 BCE).
 *
 * The rules beyond each column's own range, as NASA's catalogue keys (SEcat5/SEcatkey.html, LEcat5/LEcatkey.html)
 * define the columns, each holding for every row of both catalogues:
 * - the date exists in the calendar NASA uses for it, and the row names that calendar;
 * - rows run in catalogue order and in time: catalogue numbers and instants of greatest eclipse both increase;
 * - solar: a partial eclipse has no path width and no central duration; at a partial or non-central eclipse (+, -)
 *   the Sun is on the horizon at the point of greatest eclipse; only a hybrid path begins one way and ends the
 *   other (2, 3); a Saros series begins and ends with a partial eclipse (b, e); a partial eclipse has no central
 *   or umbral path (n, s, +, -);
 * - lunar: a penumbral eclipse has no partial or total phase and misses the umbra (umbral magnitude at most 0); a
 *   partial eclipse has a partial phase, no total phase and an umbral magnitude between 0 and 1; a total eclipse
 *   has both phases and an umbral magnitude of at least 1; each phase lies within the one before it; x, which the
 *   catalogue file writes for the key's *, marks a total penumbral eclipse (penumbral magnitude at least 1); + and
 *   - mark a central total eclipse; a Saros series begins and ends with a penumbral eclipse (b, e).
 *
 * Mirrored in Python by services/contracts/eclipses.py, which the bake validates against; the samples in
 * tests/fixtures/eclipse_contract.json are written by the Python side and parsed here.
 */

export const ECLIPSE_CATALOG_FORMAT = 'albedian.eclipse-catalog';
export const ECLIPSE_CATALOG_FORMAT_VERSION = 1;
export const ECLIPSE_EVIDENCE_CLASS = 'predicted';
export const ECLIPSE_CATALOG_YEARS = { first: -1999, last: 3000 } as const;
export const ECLIPSE_CALENDARS = ['julian', 'gregorian'] as const;
export type EclipseCalendar = (typeof ECLIPSE_CALENDARS)[number];

/** Eclipse types and the second character of the type column, as NASA's keys list them. */
export const SOLAR_ECLIPSE_TYPES = ['P', 'A', 'T', 'H'] as const;
export const SOLAR_ECLIPSE_QUALIFIERS = ['m', 'n', 's', '+', '-', '2', '3', 'b', 'e'] as const;
export const LUNAR_ECLIPSE_TYPES = ['N', 'P', 'T'] as const;
export const LUNAR_ECLIPSE_QUALIFIERS = ['m', '+', '-', 'x', 'b', 'e'] as const;

/** The quincena: the eclipse of the other kind a fortnight before (first character) and after (second), '-' for none. */
export const ECLIPSE_PATTERNS = { quincenaLunar: '^[-npt]{2}$', quincenaSolar: '^[-apth]{2}$' } as const;

export const SOLAR_ECLIPSE_COLUMNS = [
  'catalogNumber',
  'canonPlate',
  'year',
  'month',
  'day',
  'calendar',
  'greatestEclipseTdSeconds',
  'deltaTSeconds',
  'lunationNumber',
  'sarosNumber',
  'eclipseType',
  'typeQualifier',
  'quincenaLunarEclipse',
  'gamma',
  'magnitude',
  'latitudeDeg',
  'longitudeDeg',
  'sunAltitudeDeg',
  'sunAzimuthDeg',
  'pathWidthKm',
  'centralDurationSeconds',
] as const;

export const LUNAR_ECLIPSE_COLUMNS = [
  'catalogNumber',
  'year',
  'month',
  'day',
  'calendar',
  'greatestEclipseTdSeconds',
  'deltaTSeconds',
  'lunationNumber',
  'sarosNumber',
  'eclipseType',
  'typeQualifier',
  'quincenaSolarEclipse',
  'gamma',
  'penumbralMagnitude',
  'umbralMagnitude',
  'penumbralDurationMinutes',
  'partialDurationMinutes',
  'totalDurationMinutes',
  'zenithLatitudeDeg',
  'zenithLongitudeDeg',
] as const;

const LAST_JULIAN_DAY = [1582, 10, 4] as const;
const FIRST_GREGORIAN_DAY = [1582, 10, 15] as const;
const MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] as const;

/** Lexicographic order of numeric tuples of one length: negative, zero or positive. */
function compareTuples(a: readonly number[], b: readonly number[]): number {
  for (let i = 0; i < a.length; i++) {
    if (a[i]! !== b[i]!) return a[i]! < b[i]! ? -1 : 1;
  }
  return 0;
}

const remainder = (value: number, by: number) => ((value % by) + by) % by;

function isLeapYear(year: number, calendar: EclipseCalendar): boolean {
  if (calendar === 'julian') return remainder(year, 4) === 0;
  return remainder(year, 4) === 0 && (remainder(year, 100) !== 0 || remainder(year, 400) === 0);
}

/** The calendar NASA's catalogues date this day in, or null where the day does not exist in it. */
export function eclipseCalendarOf(year: number, month: number, day: number): EclipseCalendar | null {
  if (![year, month, day].every(Number.isInteger) || month < 1 || month > 12 || day < 1) return null;
  let calendar: EclipseCalendar;
  if (compareTuples([year, month, day], LAST_JULIAN_DAY) <= 0) calendar = 'julian';
  else if (compareTuples([year, month, day], FIRST_GREGORIAN_DAY) >= 0) calendar = 'gregorian';
  else return null; // 1582 October 5 to 14, which the reform skipped
  const length = month === 2 && isLeapYear(year, calendar) ? 29 : MONTH_DAYS[month - 1]!;
  return day <= length ? calendar : null;
}

const int = z.number().int();
const finite = z.number().finite();
const catalogNumber = int.min(1);
const year = int.min(ECLIPSE_CATALOG_YEARS.first).max(ECLIPSE_CATALOG_YEARS.last);
const month = int.min(1).max(12);
const day = int.min(1).max(31);
const calendar = z.enum(ECLIPSE_CALENDARS);
const tdSeconds = int.min(0).max(86399);

const SolarEclipseRowTupleSchema = z.tuple([
  catalogNumber,
  catalogNumber, // canon plate
  year,
  month,
  day,
  calendar,
  tdSeconds,
  int, // delta T, seconds
  int, // lunation number
  int, // Saros number
  z.enum(SOLAR_ECLIPSE_TYPES),
  z.enum(SOLAR_ECLIPSE_QUALIFIERS).nullable(),
  z.string().regex(new RegExp(ECLIPSE_PATTERNS.quincenaLunar)),
  finite, // gamma
  finite.min(0), // magnitude
  finite.min(-90).max(90),
  finite.min(-180).max(180),
  int.min(0).max(90), // Sun altitude
  int.min(0).max(360), // Sun azimuth
  int.min(0).nullable(), // path width, km
  int.min(0).nullable(), // central duration, s
]);
export type SolarEclipseRow = z.infer<typeof SolarEclipseRowTupleSchema>;

const LunarEclipseRowTupleSchema = z.tuple([
  catalogNumber,
  year,
  month,
  day,
  calendar,
  tdSeconds,
  int, // delta T, seconds
  int, // lunation number
  int, // Saros number
  z.enum(LUNAR_ECLIPSE_TYPES),
  z.enum(LUNAR_ECLIPSE_QUALIFIERS).nullable(),
  z.string().regex(new RegExp(ECLIPSE_PATTERNS.quincenaSolar)),
  finite, // gamma
  finite.gt(0), // penumbral magnitude
  finite, // umbral magnitude
  finite.gt(0), // penumbral phase, minutes
  finite.gt(0).nullable(), // partial phase, minutes
  finite.gt(0).nullable(), // total phase, minutes
  int.min(-90).max(90),
  int.min(-180).max(180),
]);
export type LunarEclipseRow = z.infer<typeof LunarEclipseRowTupleSchema>;

/** The first rule a solar row breaks beyond its columns' own ranges, or null. */
export function solarEclipseRowProblem(row: SolarEclipseRow): string | null {
  const [, , y, m, d, cal, , , , , type, qualifier, , , , , , altitude, , width, duration] = row;
  if (eclipseCalendarOf(y, m, d) !== cal) return 'the date does not exist in the calendar NASA uses for it';
  if ((qualifier === '2' || qualifier === '3') && type !== 'H') {
    return "only a hybrid eclipse's path begins one way and ends the other (2, 3)";
  }
  if ((qualifier === 'b' || qualifier === 'e') && type !== 'P') {
    return 'a solar Saros series begins and ends with a partial eclipse (b, e)';
  }
  if (qualifier !== null && ['n', 's', '+', '-'].includes(qualifier) && type === 'P') {
    return 'a partial eclipse has no central or umbral path (n, s, +, -)';
  }
  if (type === 'P' && (width !== null || duration !== null)) {
    return 'a partial eclipse has no path width and no central duration';
  }
  if ((type === 'P' || qualifier === '+' || qualifier === '-') && altitude !== 0) {
    return 'at a partial or non-central eclipse the Sun is on the horizon at the point of greatest eclipse';
  }
  return null;
}

/** The first rule a lunar row breaks beyond its columns' own ranges, or null. */
export function lunarEclipseRowProblem(row: LunarEclipseRow): string | null {
  const [, y, m, d, cal, , , , , type, qualifier, , , penumbral, umbral, penumbralMinutes, partial, total] = row;
  if (eclipseCalendarOf(y, m, d) !== cal) return 'the date does not exist in the calendar NASA uses for it';
  if (type === 'N' && (partial !== null || total !== null || umbral > 0)) {
    return 'a penumbral eclipse has no partial or total phase and misses the umbra';
  }
  if (type === 'P' && (partial === null || total !== null || !(umbral > 0 && umbral < 1))) {
    return 'a partial eclipse has a partial phase, no total phase and an umbral magnitude between 0 and 1';
  }
  if (type === 'T' && (partial === null || total === null || umbral < 1)) {
    return 'a total eclipse has partial and total phases and an umbral magnitude of at least 1';
  }
  if (partial !== null && partial >= penumbralMinutes) return 'the partial phase lies within the penumbral phase';
  if (total !== null && partial !== null && total >= partial) return 'the total phase lies within the partial phase';
  if (qualifier === 'x' && (type !== 'N' || penumbral < 1)) {
    return 'x marks a total penumbral eclipse: the whole Moon within the penumbra';
  }
  if ((qualifier === '+' || qualifier === '-') && type !== 'T') return '+ and - mark a central total eclipse';
  if ((qualifier === 'b' || qualifier === 'e') && type !== 'N') {
    return 'a lunar Saros series begins and ends with a penumbral eclipse (b, e)';
  }
  return null;
}

export const SolarEclipseRowSchema = SolarEclipseRowTupleSchema.superRefine((row, ctx) => {
  const problem = solarEclipseRowProblem(row);
  if (problem !== null) ctx.addIssue({ code: z.ZodIssueCode.custom, message: problem });
});

export const LunarEclipseRowSchema = LunarEclipseRowTupleSchema.superRefine((row, ctx) => {
  const problem = lunarEclipseRowProblem(row);
  if (problem !== null) ctx.addIssue({ code: z.ZodIssueCode.custom, message: problem });
});

/** Catalogue numbers and instants (year, month, day, seconds of TD) both increase from row to row. */
function orderProblem(rows: readonly (readonly unknown[])[], yearAt: number): string | null {
  const instant = (row: readonly unknown[]) =>
    [row[yearAt], row[yearAt + 1], row[yearAt + 2], row[yearAt + 4]] as number[];
  for (let i = 1; i < rows.length; i++) {
    const before = rows[i - 1]!;
    const after = rows[i]!;
    if (!((after[0] as number) > (before[0] as number))) return `Row ${i}: catalogue numbers increase from row to row.`;
    if (compareTuples(instant(after), instant(before)) <= 0) return `Row ${i}: the eclipses follow one another in time.`;
  }
  return null;
}

const sameColumns = (columns: readonly string[], expected: readonly string[]) =>
  columns.length === expected.length && columns.every((name, i) => name === expected[i]);

const catalogFields = {
  format: z.literal(ECLIPSE_CATALOG_FORMAT),
  formatVersion: z.literal(ECLIPSE_CATALOG_FORMAT_VERSION),
  evidenceClass: z.literal(ECLIPSE_EVIDENCE_CLASS),
  columns: z.array(z.string()),
};

/** apps/web/public/catalog/eclipses/solar.json: the Five Millennium Catalog of Solar Eclipses. */
export const SolarEclipseCatalogSchema = z
  .object({ ...catalogFields, kind: z.literal('solar'), rows: z.array(SolarEclipseRowSchema).min(1) })
  .strict()
  .superRefine((catalog, ctx) => {
    if (!sameColumns(catalog.columns, SOLAR_ECLIPSE_COLUMNS)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['columns'], message: 'The solar catalogue columns, in order.' });
    }
    const problem = orderProblem(catalog.rows, 2);
    if (problem !== null) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['rows'], message: problem });
  });
export type SolarEclipseCatalog = z.infer<typeof SolarEclipseCatalogSchema>;

/** apps/web/public/catalog/eclipses/lunar.json: the Five Millennium Catalog of Lunar Eclipses. */
export const LunarEclipseCatalogSchema = z
  .object({ ...catalogFields, kind: z.literal('lunar'), rows: z.array(LunarEclipseRowSchema).min(1) })
  .strict()
  .superRefine((catalog, ctx) => {
    if (!sameColumns(catalog.columns, LUNAR_ECLIPSE_COLUMNS)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['columns'], message: 'The lunar catalogue columns, in order.' });
    }
    const problem = orderProblem(catalog.rows, 1);
    if (problem !== null) ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['rows'], message: problem });
  });
export type LunarEclipseCatalog = z.infer<typeof LunarEclipseCatalogSchema>;
