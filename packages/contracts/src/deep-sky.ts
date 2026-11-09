import { z } from 'zod';
import { ASSET_PROVENANCE_LIMITS, ASSET_PROVENANCE_PATTERNS } from './provenance.js';

/**
 * The deep-sky catalogue (roadmap Track 2 M2): OpenNGC (github.com/mattiaverga/OpenNGC, CC-BY-SA-4.0, credit
 * Mattia Verga), its NGC and addendum tables, as scripts/bake_deepsky_openngc.py writes them to
 * apps/web/public/catalog/deepsky/. The rows are catalogue positions and sizes from the literature that OpenNGC
 * compiles, none measured by this app, so the evidence kind is 'catalog-derived'.
 *
 * A catalogue is {format, formatVersion, evidenceKind, release, types, columns, rows}:
 * - release: the OpenNGC release the rows come from, such as 'OpenNGC v20260501';
 * - types: each of OpenNGC's 21 object type codes with its label, exactly those codes;
 * - rows: tuples in the order `columns` names: the OpenNGC name, its type code, J2000 right ascension and
 *   declination in degrees, the constellation, major and minor axis in arcmin, the major axis position angle (north
 *   through east, 0 to 180 degrees), B and V magnitude, the mean surface brightness, the Messier number, the NGC and
 *   IC cross references as OpenNGC writes them ('0104', '5195A', '0190 NED01'), other catalogue identifiers, and
 *   common names. A value OpenNGC leaves blank is null (or an empty list), never filled.
 *
 * Rules beyond each cell's own range, each holding on every row of OpenNGC v20260501: names are unique; right
 * ascension and declination are both given or both null, and null only on a nonexistent object (type NonEx); the
 * constellation is null exactly when the position is; Serpens is 'Se1' (Caput) or 'Se2' (Cauda) as OpenNGC writes
 * it; a minor axis or a position angle comes only with a major axis, and the minor axis is never the larger; a
 * Messier number belongs to one row that is not a duplicate (type Dup), so M102, a duplicate of M101, may repeat it;
 * a duplicate names the object it duplicates in its NGC, IC or Messier cell.
 *
 * Mirrored in Python by services/contracts/deep_sky.py, which the bake validates against; the samples in
 * tests/fixtures/deep_sky_contract.json are written by the Python side and parsed here.
 */

export const DEEP_SKY_CATALOG_FORMAT = 'albedian.deep-sky-catalog';
export const DEEP_SKY_CATALOG_FORMAT_VERSION = 1;
export const DEEP_SKY_EVIDENCE_KIND = 'catalog-derived';

/** OpenNGC's object type codes, in the order of its NGC_guide.txt. */
export const DEEP_SKY_OBJECT_TYPES = [
  '*', '**', '*Ass', 'OCl', 'GCl', 'Cl+N', 'G', 'GPair', 'GTrpl', 'GGroup', 'PN', 'HII', 'DrkN', 'EmN', 'Neb',
  'RfN', 'SNR', 'Nova', 'NonEx', 'Dup', 'Other',
] as const; // prettier-ignore
export const DEEP_SKY_NONEXISTENT_TYPE = 'NonEx';
export const DEEP_SKY_DUPLICATE_TYPE = 'Dup';

/** The 88 IAU constellation abbreviations, with Serpens split into Caput (Se1) and Cauda (Se2) as OpenNGC writes it. */
export const DEEP_SKY_CONSTELLATIONS = [
  'And', 'Ant', 'Aps', 'Aql', 'Aqr', 'Ara', 'Ari', 'Aur', 'Boo', 'CMa', 'CMi', 'CVn', 'Cae', 'Cam', 'Cap', 'Car',
  'Cas', 'Cen', 'Cep', 'Cet', 'Cha', 'Cir', 'Cnc', 'Col', 'Com', 'CrA', 'CrB', 'Crt', 'Cru', 'Crv', 'Cyg', 'Del',
  'Dor', 'Dra', 'Equ', 'Eri', 'For', 'Gem', 'Gru', 'Her', 'Hor', 'Hya', 'Hyi', 'Ind', 'LMi', 'Lac', 'Leo', 'Lep',
  'Lib', 'Lup', 'Lyn', 'Lyr', 'Men', 'Mic', 'Mon', 'Mus', 'Nor', 'Oct', 'Oph', 'Ori', 'Pav', 'Peg', 'Per', 'Phe',
  'Pic', 'PsA', 'Psc', 'Pup', 'Pyx', 'Ret', 'Scl', 'Sco', 'Sct', 'Se1', 'Se2', 'Sex', 'Sge', 'Sgr', 'Tau', 'Tel',
  'TrA', 'Tri', 'Tuc', 'UMa', 'UMi', 'Vel', 'Vir', 'Vol', 'Vul',
] as const; // prettier-ignore
export const DEEP_SKY_MESSIER_MIN = 1;
export const DEEP_SKY_MESSIER_MAX = 110;

export const DEEP_SKY_COLUMNS = [
  'name',
  'type',
  'raDeg',
  'decDeg',
  'constellation',
  'majorAxisArcmin',
  'minorAxisArcmin',
  'positionAngleDeg',
  'bMag',
  'vMag',
  'surfaceBrightness',
  'messier',
  'ngc',
  'ic',
  'identifiers',
  'commonNames',
] as const;

export const DEEP_SKY_PATTERNS = {
  /** An NGC or IC number as OpenNGC writes it: four digits, an optional component letter or two, an optional NED part. */
  crossReference: '^[0-9]{4}[A-Z]{0,2}(?: NED[0-9]{2})?$',
} as const;

/** A string matching `pattern` with at most `max` Unicode code points (an astral character counts once). */
const bounded = (pattern: string, max: number) =>
  z
    .string()
    .regex(new RegExp(pattern))
    .refine((value) => [...value].length <= max, { message: `At most ${max} characters.` });

const shortText = bounded(ASSET_PROVENANCE_PATTERNS.text, ASSET_PROVENANCE_LIMITS.shortTextMaxLength);
const crossReference = z.string().regex(new RegExp(DEEP_SKY_PATTERNS.crossReference));
const finite = z.number().finite();

export const DeepSkyRowSchema = z.tuple([
  shortText, // name
  z.enum(DEEP_SKY_OBJECT_TYPES), // type
  finite.min(0).lt(360).nullable(), // raDeg
  finite.min(-90).max(90).nullable(), // decDeg
  z.enum(DEEP_SKY_CONSTELLATIONS).nullable(), // constellation
  finite.gt(0).nullable(), // majorAxisArcmin
  finite.gt(0).nullable(), // minorAxisArcmin
  finite.min(0).max(180).nullable(), // positionAngleDeg
  finite.nullable(), // bMag
  finite.nullable(), // vMag
  finite.nullable(), // surfaceBrightness
  z.number().int().min(DEEP_SKY_MESSIER_MIN).max(DEEP_SKY_MESSIER_MAX).nullable(), // messier
  z.array(crossReference), // ngc
  z.array(crossReference), // ic
  z.array(shortText), // identifiers
  z.array(shortText), // commonNames
]);
export type DeepSkyRow = z.infer<typeof DeepSkyRowSchema>;

/** apps/web/public/catalog/deepsky/openngc.json. */
export const DeepSkyCatalogSchema = z
  .object({
    format: z.literal(DEEP_SKY_CATALOG_FORMAT),
    formatVersion: z.literal(DEEP_SKY_CATALOG_FORMAT_VERSION),
    evidenceKind: z.literal(DEEP_SKY_EVIDENCE_KIND),
    release: shortText,
    types: z.record(z.enum(DEEP_SKY_OBJECT_TYPES), shortText),
    columns: z.array(z.string()),
    rows: z.array(DeepSkyRowSchema).min(1),
  })
  .strict()
  .superRefine((catalog, ctx) => {
    const issue = (message: string, path: (string | number)[] = []) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    const columns = DEEP_SKY_COLUMNS as readonly string[];
    if (catalog.columns.length !== columns.length || catalog.columns.some((name, i) => name !== columns[i])) {
      issue('The deep-sky catalogue columns, in order.', ['columns']);
    }
    const missing = DEEP_SKY_OBJECT_TYPES.filter((code) => !(code in catalog.types));
    if (missing.length > 0) issue(`Every object type has a label; missing ${missing.join(', ')}.`, ['types']);
    const names = catalog.rows.map((row) => row[0]);
    if (new Set(names).size !== names.length) issue('Object names are unique.', ['rows']);

    const messierOwner = new Map<number, string>();
    catalog.rows.forEach((row, i) => {
      const [name, kind, ra, dec, constellation, major, minor, angle] = row;
      const [messier, ngc, ic] = [row[11], row[12], row[13]];
      const at = ['rows', i];
      if ((ra === null) !== (dec === null)) issue(`Row ${i} (${name}): right ascension and declination are both given or both null.`, at);
      if (ra === null && kind !== DEEP_SKY_NONEXISTENT_TYPE) issue(`Row ${i} (${name}): only a nonexistent object has no position.`, at);
      if ((constellation === null) !== (ra === null)) issue(`Row ${i} (${name}): the constellation is null exactly when the position is.`, at);
      if (minor !== null && (major === null || minor > major)) {
        issue(`Row ${i} (${name}): a minor axis comes with a major axis and is never the larger.`, at);
      }
      if (angle !== null && major === null) issue(`Row ${i} (${name}): a position angle comes with a major axis.`, at);
      if (kind === DEEP_SKY_DUPLICATE_TYPE) {
        if (messier === null && ngc.length === 0 && ic.length === 0) {
          issue(`Row ${i} (${name}): a duplicate names its object in NGC, IC or Messier.`, at);
        }
      } else if (messier !== null) {
        const owner = messierOwner.get(messier);
        if (owner !== undefined) issue(`Row ${i} (${name}): M${messier} already belongs to ${owner}.`, at);
        else messierOwner.set(messier, name);
      }
    });
  });
export type DeepSkyCatalog = z.infer<typeof DeepSkyCatalogSchema>;
