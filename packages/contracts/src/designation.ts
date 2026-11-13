/**
 * Bayer and Flamsteed designations, and the title order, beside `CatalogReferenceSchema` (catalog-2b). The Python twin
 * is services/contracts/designation.py; tests/fixtures/designation_cases.json holds cases both sides answer alike.
 *
 * Every name comes from ./designation-tables.ts (scripts/bake_designation_tables.py), never typed:
 * - The wire form of a Bayer designation is the long form the repository has always shipped: the letter's name, an
 *   optional index digit, a space and the constellation's genitive ("Alpha Orionis", "Alpha2 Capricorni").
 *   `BAYER_DESIGNATION_PATTERN` admits exactly that, from the same tables, on both sides.
 * - The compact form, for display only: the Greek letter itself, the index as a superscript and the constellation's
 *   three-letter abbreviation ("α² Cap").
 * - A Flamsteed designation is its number and the constellation's abbreviation ("27 Cas").
 * - The title is the IAU name (a common name that is not the catalog id), else the catalog id, as both paths showed
 *   before catalog-2b; the Bayer designation, the Flamsteed designation and the catalog id that is not the title are
 *   the subtitle's parts, in that order. Titling a star with no IAU name by its designation is a wording choice for
 *   the owner, and changes this one function on each side.
 */
import {
  DESIGNATION_CONSTELLATIONS,
  DESIGNATION_INDICES,
  DESIGNATION_LETTERS,
  FLAMSTEED_NUMBER_MAX,
} from './designation-tables.js';

export { FLAMSTEED_NUMBER_MAX };
export const FLAMSTEED_NUMBER_MIN = 1;

const GLYPH_BY_NAME = new Map(DESIGNATION_LETTERS.map(([, glyph, name]) => [name, glyph]));
const SUPERSCRIPT = new Map(DESIGNATION_INDICES.map(([digit, glyph]) => [digit, glyph]));
const ABBR_BY_GENITIVE = new Map(DESIGNATION_CONSTELLATIONS.map(([abbr, , genitive]) => [genitive, abbr]));
const ABBR_BY_NAME = new Map(DESIGNATION_CONSTELLATIONS.map(([abbr, name]) => [name, abbr]));
const ABBRS = new Set(DESIGNATION_CONSTELLATIONS.map(([abbr]) => abbr));

// Longest first, so no alternative stops at a shorter one that begins it; ties in code-point order, as Python sorts
const alternatives = (words: readonly string[]): string =>
  [...words]
    .sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0))
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|');

export const BAYER_DESIGNATION_PATTERN =
  `^(${alternatives(DESIGNATION_LETTERS.map(([, , name]) => name))})` +
  `(${alternatives(DESIGNATION_INDICES.map(([digit]) => digit))})? ` +
  `(${alternatives(DESIGNATION_CONSTELLATIONS.map(([, , genitive]) => genitive))})$`;
const BAYER = new RegExp(BAYER_DESIGNATION_PATTERN);

/** The display form of a wire-form Bayer designation ("Alpha2 Capricorni" gives "α² Cap"); null for any other text. */
export function compactDesignation(designation: string): string | null {
  const match = BAYER.exec(designation);
  if (match === null) return null;
  // The pattern admits only names, digits and genitives the tables hold, so each lookup finds its row
  const [name, digit, genitive] = [match[1] ?? '', match[2], match[3] ?? ''];
  return `${GLYPH_BY_NAME.get(name)}${digit === undefined ? '' : SUPERSCRIPT.get(digit)} ${ABBR_BY_GENITIVE.get(genitive)}`;
}

/** "27 Cas" from the number and the constellation, by its abbreviation or its Latin name; null when the
 * constellation is neither or the number is outside the contract's range. */
export function flamsteedDesignation(number: number, constellation: string): string | null {
  const abbr = ABBRS.has(constellation) ? constellation : ABBR_BY_NAME.get(constellation);
  if (abbr === undefined || !Number.isInteger(number) || number < FLAMSTEED_NUMBER_MIN || number > FLAMSTEED_NUMBER_MAX) {
    return null;
  }
  return `${number} ${abbr}`;
}

export interface CatalogTitleInput {
  catalogSourceId: string;
  commonName: string;
  constellation: string;
  bayerDesignation?: string;
  flamsteedNumber?: number;
}

/** The title and the subtitle's parts of a catalog reference: the IAU name, else the catalog id; the subtitle holds
 * the Bayer designation, the Flamsteed designation and the catalog id where it is not the title, in that order. */
export function catalogTitle(reference: CatalogTitleInput): { title: string; subtitleParts: string[] } {
  const sourceId = reference.catalogSourceId;
  const name = reference.commonName !== sourceId ? reference.commonName : null;
  const bayer = reference.bayerDesignation ?? null;
  const flamsteed =
    reference.flamsteedNumber === undefined ? null : flamsteedDesignation(reference.flamsteedNumber, reference.constellation);
  const title = name ?? sourceId;
  return { title, subtitleParts: [bayer, flamsteed, sourceId].filter((part): part is string => part !== null && part !== title) };
}
