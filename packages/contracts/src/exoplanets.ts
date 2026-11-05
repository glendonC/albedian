import { z } from 'zod';
import { ASSET_PROVENANCE_LIMITS, ASSET_PROVENANCE_PATTERNS, BLANK_CHARACTERS } from './provenance.js';

/**
 * The exoplanet table (roadmap Track 2 M2): the NASA Exoplanet Archive's Planetary Systems Composite Parameters
 * table (PSCompPars), as scripts/bake_exoplanets.py writes it to apps/web/public/catalog/exoplanets/. The archive
 * builds one row per planet from several papers, so a row is not one self-consistent solution, and every value
 * names its own reference. The evidence kind is 'catalog-derived': values from the literature and from the
 * archive's own calculations, none measured by this app.
 *
 * A table is {format, formatVersion, evidenceKind, columns, notes, references, rows}:
 * - notes: text per column, the equilibrium temperature's at least;
 * - references: {refstr, label, url} with an https url, each cited at least once and none listed twice. The
 *   archive's refstr does not identify one paper (a refstr can stand for two), so a value cites a reference by its
 *   index in this list;
 * - rows: tuples in the order `columns` names. The first eight hold the planet's name and host, the star and
 *   planet counts, its discovery and the archive's mass provenance (pl_bmassprov); each of the rest is null or
 *   [value, err1, err2, limit, ref, basis]:
 *   - err1 the positive uncertainty (0 or more) and err2 the negative one (0 or less), null where the archive
 *     gives none;
 *   - limit the archive's limit flag as the archive writes it (-1, 0 or 1), null where it gives none;
 *   - ref the index of a reference;
 *   - basis: measured (a paper's value), calculated (the archive's formula from other values) or modelled (the
 *     archive's mass-radius relation, Chen and Kipping 2017). The archive marks the last two "Calculated Value",
 *     and the table cites them to the archive's page on its calculations, whose refstr is CALCULATED_VALUE.
 *
 * Rules beyond each cell's own range: planet names are unique; a value cites the calculation page exactly when
 * its basis is calculated or modelled; such a radius or mass is modelled and any other calculated; a mass is
 * modelled exactly when its provenance is 'M-R relationship'.
 *
 * Mirrored in Python by services/contracts/exoplanets.py, which the bake validates against; the samples in
 * tests/fixtures/exoplanet_contract.json are written by the Python side and parsed here.
 */

export const EXOPLANET_TABLE_FORMAT = 'albedian.exoplanet-table';
export const EXOPLANET_TABLE_FORMAT_VERSION = 1;
export const EXOPLANET_EVIDENCE_KIND = 'catalog-derived';
export const EXOPLANET_VALUE_BASES = ['measured', 'calculated', 'modelled'] as const;
/** pl_bmassprov as the archive writes it. */
export const EXOPLANET_MASS_PROVENANCES = ['Mass', 'Msini', 'M-R relationship', 'Msin(i)/sin(i)'] as const;
export const EXOPLANET_CALCULATED_REFSTR = 'CALCULATED_VALUE';

export const EXOPLANET_IDENTITY_COLUMNS = [
  'name',
  'hostName',
  'starCount',
  'planetCount',
  'discoveryMethod',
  'discoveryYear',
  'discoveryFacility',
  'massProvenance',
] as const;
export const EXOPLANET_VALUE_COLUMNS = [
  'orbitalPeriodDays',
  'semiMajorAxisAu',
  'radiusEarth',
  'massEarth',
  'eccentricity',
  'equilibriumTemperatureK',
  'hostEffectiveTemperatureK',
  'hostRadiusSolar',
  'hostMassSolar',
  'distancePc',
  'raDeg',
  'decDeg',
  'vMagnitude',
] as const;
export const EXOPLANET_COLUMNS = [...EXOPLANET_IDENTITY_COLUMNS, ...EXOPLANET_VALUE_COLUMNS] as const;
/** The columns the archive fills from its mass-radius relation when a paper gives no value. */
export const EXOPLANET_MODELLED_COLUMNS = ['radiusEarth', 'massEarth'] as const;
export const EXOPLANET_REQUIRED_NOTES = ['equilibriumTemperatureK'] as const;

export const EXOPLANET_PATTERNS = {
  refstr: '^[A-Z0-9_]+$',
  httpsUrl: `^https://[^${BLANK_CHARACTERS}/]+[^${BLANK_CHARACTERS}]*$`,
} as const;

type ValueColumn = (typeof EXOPLANET_VALUE_COLUMNS)[number];

/** A string matching `pattern` with at most `max` Unicode code points (an astral character counts once). */
const bounded = (pattern: string, max: number) =>
  z
    .string()
    .regex(new RegExp(pattern))
    .refine((value) => [...value].length <= max, { message: `At most ${max} characters.` });

const shortText = bounded(ASSET_PROVENANCE_PATTERNS.text, ASSET_PROVENANCE_LIMITS.shortTextMaxLength);
const longText = bounded(ASSET_PROVENANCE_PATTERNS.text, ASSET_PROVENANCE_LIMITS.textMaxLength);
const httpsUrl = bounded(EXOPLANET_PATTERNS.httpsUrl, ASSET_PROVENANCE_LIMITS.urlMaxLength);
const finite = z.number().finite();

/** Each value column's own range. */
const VALUE_RANGES: Record<ValueColumn, z.ZodNumber> = {
  orbitalPeriodDays: finite.gt(0),
  semiMajorAxisAu: finite.gt(0),
  radiusEarth: finite.gt(0),
  massEarth: finite.gt(0),
  eccentricity: finite.min(0).lt(1),
  equilibriumTemperatureK: finite.gt(0),
  hostEffectiveTemperatureK: finite.gt(0),
  hostRadiusSolar: finite.gt(0),
  hostMassSolar: finite.gt(0),
  distancePc: finite.gt(0),
  raDeg: finite.min(0).lt(360),
  decDeg: finite.min(-90).max(90),
  vMagnitude: finite,
};

const valueCell = (name: ValueColumn) =>
  z
    .tuple([
      VALUE_RANGES[name],
      finite.min(0).nullable(), // err1
      finite.max(0).nullable(), // err2
      z.number().int().min(-1).max(1).nullable(), // limit flag
      z.number().int().min(0), // reference index
      z.enum(EXOPLANET_VALUE_BASES),
    ])
    .nullable();

export const ExoplanetRowSchema = z.tuple([
  shortText, // name
  shortText, // host name
  z.number().int().min(1), // stars in the system
  z.number().int().min(1), // planets in the system
  shortText, // discovery method
  z.number().int(), // discovery year
  shortText, // discovery facility
  z.enum(EXOPLANET_MASS_PROVENANCES).nullable(),
  valueCell('orbitalPeriodDays'),
  valueCell('semiMajorAxisAu'),
  valueCell('radiusEarth'),
  valueCell('massEarth'),
  valueCell('eccentricity'),
  valueCell('equilibriumTemperatureK'),
  valueCell('hostEffectiveTemperatureK'),
  valueCell('hostRadiusSolar'),
  valueCell('hostMassSolar'),
  valueCell('distancePc'),
  valueCell('raDeg'),
  valueCell('decDeg'),
  valueCell('vMagnitude'),
]);
export type ExoplanetRow = z.infer<typeof ExoplanetRowSchema>;

/** A paper or page a value cites. */
export const ExoplanetReferenceSchema = z
  .object({ refstr: z.string().regex(new RegExp(EXOPLANET_PATTERNS.refstr)), label: shortText, url: httpsUrl })
  .strict();
export type ExoplanetReference = z.infer<typeof ExoplanetReferenceSchema>;

const MODELLED = EXOPLANET_MODELLED_COLUMNS as readonly string[];

/** apps/web/public/catalog/exoplanets/pscomppars.json. */
export const ExoplanetTableSchema = z
  .object({
    format: z.literal(EXOPLANET_TABLE_FORMAT),
    formatVersion: z.literal(EXOPLANET_TABLE_FORMAT_VERSION),
    evidenceKind: z.literal(EXOPLANET_EVIDENCE_KIND),
    columns: z.array(z.string()),
    notes: z.record(z.string(), longText),
    references: z.array(ExoplanetReferenceSchema).min(1),
    rows: z.array(ExoplanetRowSchema).min(1),
  })
  .strict()
  .superRefine((table, ctx) => {
    const issue = (message: string, path: (string | number)[] = []) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    const columns = EXOPLANET_COLUMNS as readonly string[];
    if (table.columns.length !== columns.length || table.columns.some((name, i) => name !== columns[i])) {
      issue('The exoplanet table columns, in order.', ['columns']);
    }
    const unknown = Object.keys(table.notes).filter((name) => !columns.includes(name));
    if (unknown.length > 0) issue(`Notes name columns the table does not have: ${unknown.join(', ')}.`, ['notes']);
    const missing = EXOPLANET_REQUIRED_NOTES.filter((name) => !(name in table.notes));
    if (missing.length > 0) issue(`Notes are required for ${missing.join(', ')}.`, ['notes']);
    const pairs = table.references.map((ref) => JSON.stringify([ref.refstr, ref.url]));
    if (new Set(pairs).size !== pairs.length) issue('A reference is listed twice (the same refstr and url).', ['references']);
    const names = table.rows.map((row) => row[0]);
    if (new Set(names).size !== names.length) issue('Planet names are unique.', ['rows']);

    const cited = new Set<number>();
    const firstValue = EXOPLANET_IDENTITY_COLUMNS.length;
    table.rows.forEach((row, i) => {
      EXOPLANET_VALUE_COLUMNS.forEach((name, j) => {
        const cell = row[firstValue + j] as unknown[] | null;
        if (cell === null) return;
        const ref = cell[4] as number;
        const basis = cell[5] as string;
        const reference = table.references[ref];
        if (reference === undefined) {
          issue(`Row ${i} (${row[0]}), ${name}: reference ${ref} is not listed.`, ['rows', i]);
          return;
        }
        cited.add(ref);
        const onCalculationPage = reference.refstr === EXOPLANET_CALCULATED_REFSTR;
        if (onCalculationPage !== (basis !== 'measured')) {
          issue(`Row ${i} (${row[0]}), ${name}: a value cites the calculation page exactly when it is calculated or modelled.`, ['rows', i]);
        } else if (onCalculationPage && basis !== (MODELLED.includes(name) ? 'modelled' : 'calculated')) {
          issue(`Row ${i} (${row[0]}), ${name}: the archive models radii and masses and calculates the rest.`, ['rows', i]);
        }
      });
      const mass = row[EXOPLANET_COLUMNS.indexOf('massEarth')] as unknown[] | null;
      if (mass !== null && (mass[5] === 'modelled') !== (row[EXOPLANET_COLUMNS.indexOf('massProvenance')] === 'M-R relationship')) {
        issue(`Row ${i} (${row[0]}): a mass is modelled exactly when its provenance is 'M-R relationship'.`, ['rows', i]);
      }
    });
    const unused = table.references.map((_, i) => i).filter((i) => !cited.has(i));
    if (unused.length > 0) issue(`References no value cites: ${unused.join(', ')}.`, ['references']);
  });
export type ExoplanetTable = z.infer<typeof ExoplanetTableSchema>;
