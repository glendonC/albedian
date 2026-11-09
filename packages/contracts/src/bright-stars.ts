import { z } from 'zod';
import { ASSET_PROVENANCE_LIMITS, ASSET_PROVENANCE_PATTERNS } from './provenance.js';

/**
 * The bright-star 3D supplement: the stars the G 12 star tiles take from Hipparcos rather than Gaia DR3 (the tile
 * bake's merge: no Gaia DR3 best neighbour, a saturated one brighter than G 3, one outside the G < 12 subset, or one
 * that is a fainter companion), with full-precision astrometry for a 3D flight: Hipparcos, the New Reduction (van
 * Leeuwen 2007), joined with V and multiplicity from the Hipparcos Catalogue (ESA 1997), both from the ESA Gaia archive
 * (CC BY-NC 3.0 IGO, credit ESA). Catalogue values, none measured by this app, so the evidence kind is
 * 'catalog-derived'.
 *
 * A supplement is {format, formatVersion, evidenceKind, catalogue, frame, epochJulianYear, tilesRelease, columns,
 * rows}:
 * - frame 'ICRS' and epochJulianYear 1991.25: positions are Hipparcos's own, at its epoch, not moved; a reader
 *   propagates them with the proper motion to the epoch it draws (the tiles hold them moved to J2016.0);
 * - rows: tuples in the order `columns` names: HIP number; IAU name or null; right ascension and declination in degrees
 *   and their standard errors in mas (RA's as RA*cos(Dec)); parallax and its standard error in mas; proper motion in
 *   RA*cos(Dec) and in Dec, and their standard errors, in mas/yr; Hp magnitude; V magnitude (1997) or null; B-V or
 *   null; the 2007 solution type and number of components; the Gaia DR3 best neighbour or null; why Hipparcos wins;
 *   and the star's row in the tiles: layer, HEALPix nested pixel and row index in that tile's core block, so a reader
 *   can move exactly these stars without drawing them twice.
 *
 * The parallax rule (no distance is baked): a parallax p mas with standard error e mas gives a distance only where
 * p > 0 and e / p is at most 0.1: 1000 / p parsecs, with the 1 sigma range 1000 / (p + e) to 1000 / (p - e). Above
 * that threshold the inverse is a biased estimator, so no 1000 / p distance is given there: a reader that needs one
 * must choose and disclose its own estimator, as the data do not settle one. A parallax at or below zero gives no
 * distance.
 *
 * Rules beyond each value's own range: HIP numbers are unique; tile locations are unique; a parallax error is
 * positive. Integers (HIP, solution type, components, tile layer, pixel and index) are written plain, as 32349 and
 * never 32349.0: the Python mirror refuses a JSON number with a fraction where an integer goes, while this schema
 * cannot tell 32349.0 from 32349 once JSON.parse has read it, so a hand edit like that is caught on the Python side
 * only.
 *
 * Mirrored in Python by services/contracts/bright_stars.py, which the bake validates against; the samples in
 * tests/fixtures/bright_stars_contract.json are written by the Python side and parsed here.
 */

export const BRIGHT_STAR_SUPPLEMENT_FORMAT = 'albedian.bright-star-supplement';
export const BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION = 1;
export const BRIGHT_STAR_EVIDENCE_KIND = 'catalog-derived';
export const BRIGHT_STAR_FRAME = 'ICRS';
export const BRIGHT_STAR_EPOCH_JULIAN_YEAR = 1991.25;

export const BRIGHT_STAR_COLUMNS = [
  'hip',
  'name',
  'raDeg',
  'decDeg',
  'raErrorMas',
  'decErrorMas',
  'parallaxMas',
  'parallaxErrorMas',
  'pmRaCosDecMasPerYr',
  'pmDecMasPerYr',
  'pmRaErrorMasPerYr',
  'pmDecErrorMasPerYr',
  'hpMag',
  'vMag',
  'bMinusV',
  'solutionType',
  'components',
  'gaiaSourceId',
  'hipparcosReason',
  'tileLayer',
  'tilePixel',
  'tileIndex',
] as const;

export const BRIGHT_STAR_PATTERNS = {
  /** A Gaia DR3 source_id, written as a decimal string so it stays exact past 2^53. */
  gaiaSourceId: '^[1-9][0-9]{0,19}$',
} as const;

/** A string matching `pattern` with at most `max` Unicode code points (an astral character counts once). */
const bounded = (pattern: string, max: number) =>
  z
    .string()
    .regex(new RegExp(pattern))
    .refine((value) => [...value].length <= max, { message: `At most ${max} characters.` });

const shortText = bounded(ASSET_PROVENANCE_PATTERNS.text, ASSET_PROVENANCE_LIMITS.shortTextMaxLength);
const finite = z.number().finite();
const count = z.number().int();

export const BrightStarRowSchema = z.tuple([
  count.min(1), // hip
  shortText.nullable(), // name
  finite.min(0).lt(360), // raDeg
  finite.min(-90).max(90), // decDeg
  finite.min(0), // raErrorMas
  finite.min(0), // decErrorMas
  finite, // parallaxMas (may be zero or negative)
  finite.gt(0), // parallaxErrorMas
  finite, // pmRaCosDecMasPerYr
  finite, // pmDecMasPerYr
  finite.min(0), // pmRaErrorMasPerYr
  finite.min(0), // pmDecErrorMasPerYr
  finite, // hpMag
  finite.nullable(), // vMag
  finite.nullable(), // bMinusV
  count.min(0), // solutionType
  count.min(1), // components
  z.string().regex(new RegExp(BRIGHT_STAR_PATTERNS.gaiaSourceId)).nullable(), // gaiaSourceId
  shortText, // hipparcosReason
  count.min(0), // tileLayer
  count.min(0), // tilePixel
  count.min(0), // tileIndex
]);
export type BrightStarRow = z.infer<typeof BrightStarRowSchema>;

/** apps/web/public/catalog/stars/hipparcos-bright-3d/stars.json. */
export const BrightStarSupplementSchema = z
  .object({
    format: z.literal(BRIGHT_STAR_SUPPLEMENT_FORMAT),
    formatVersion: z.literal(BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION),
    evidenceKind: z.literal(BRIGHT_STAR_EVIDENCE_KIND),
    catalogue: shortText,
    frame: z.literal(BRIGHT_STAR_FRAME),
    epochJulianYear: z.literal(BRIGHT_STAR_EPOCH_JULIAN_YEAR),
    tilesRelease: shortText,
    columns: z.array(z.string()),
    rows: z.array(BrightStarRowSchema).min(1),
  })
  .strict()
  .superRefine((supplement, ctx) => {
    const issue = (message: string, path: (string | number)[] = []) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    const columns = BRIGHT_STAR_COLUMNS as readonly string[];
    if (supplement.columns.length !== columns.length || supplement.columns.some((name, i) => name !== columns[i])) {
      issue('The bright-star supplement columns, in order.', ['columns']);
    }
    const hips = supplement.rows.map((row) => row[0]);
    if (new Set(hips).size !== hips.length) issue('HIP numbers are unique.', ['rows']);
    const places = supplement.rows.map((row) => `${row[19]},${row[20]},${row[21]}`);
    if (new Set(places).size !== places.length) issue('Tile locations (layer, pixel, index) are unique.', ['rows']);
  });
export type BrightStarSupplement = z.infer<typeof BrightStarSupplementSchema>;
