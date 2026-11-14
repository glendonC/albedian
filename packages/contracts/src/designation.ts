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
 * - The title is the IAU name (a common name that is not the catalog id), else the Bayer designation, else the
 *   Flamsteed designation with the constellation's genitive in full ("119 Tauri"), else the catalog id (catalog-2c,
 *   the owner's yes; the Flamsteed title's form is proposed to the owner); the Bayer designation, the Flamsteed
 *   designation in its short form ("119 Tau") and the catalog id are the subtitle's parts, in that order, each left
 *   out where it is the title.
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
const GENITIVE_BY_ABBR = new Map(DESIGNATION_CONSTELLATIONS.map(([abbr, , genitive]) => [abbr, genitive]));

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

const BLANK = /^[ \t\n\r\f\v\u00a0]*$/;

const nonBlank = (value: unknown): string | null =>
  typeof value === 'string' && !BLANK.test(value) ? value : null;

/** The display form of a wire-form Bayer designation ("Alpha2 Capricorni" gives "α² Cap"); null for any other text. */
export function compactDesignation(designation: string): string | null {
  if (typeof designation !== 'string') return null;
  const match = BAYER.exec(designation);
  if (match === null) return null;
  // The pattern admits only names, digits and genitives the tables hold, so each lookup finds its row
  const [name, digit, genitive] = [match[1] ?? '', match[2], match[3] ?? ''];
  return `${GLYPH_BY_NAME.get(name)}${digit === undefined ? '' : SUPERSCRIPT.get(digit)} ${ABBR_BY_GENITIVE.get(genitive)}`;
}

/** "27 Cas" from the number (an integer or whole float in the contract's range) and the constellation, by its
 * abbreviation or its Latin name; null for any other number, type or constellation. */
export function flamsteedDesignation(number: number, constellation: string): string | null {
  const abbr = ABBRS.has(constellation) ? constellation : ABBR_BY_NAME.get(constellation);
  if (abbr === undefined || !Number.isInteger(number) || number < FLAMSTEED_NUMBER_MIN || number > FLAMSTEED_NUMBER_MAX) {
    return null;
  }
  return `${number} ${abbr}`;
}

/** "119 Tauri" from the number and the constellation (its abbreviation or its Latin name), the genitive in full as the
 * Bayer form writes it; null where `flamsteedDesignation` gives null. */
export function flamsteedLongDesignation(number: number, constellation: string): string | null {
  const short = flamsteedDesignation(number, constellation);
  if (short === null) return null;
  const space = short.indexOf(' ');
  return `${short.slice(0, space)} ${GENITIVE_BY_ABBR.get(short.slice(space + 1))}`;
}

export interface CatalogTitleInput {
  catalogSourceId: string;
  commonName: string;
  constellation: string;
  bayerDesignation?: string;
  flamsteedNumber?: number;
}

/** The title and the subtitle's parts of a catalog reference: the IAU name, else the Bayer designation, else the
 * Flamsteed designation with the genitive in full, else the catalog id; the subtitle holds the Bayer designation, the
 * Flamsteed designation in its short form and the catalog id, in that order, each left out where it is the title or
 * blank. */
export function catalogTitle(reference: CatalogTitleInput): { title: string; subtitleParts: string[] } {
  const sourceId = typeof reference.catalogSourceId === 'string' ? reference.catalogSourceId : '';
  const sourcePart = nonBlank(sourceId);
  const common = nonBlank(reference.commonName);
  const name = common !== sourceId ? common : null;
  const bayer = nonBlank(reference.bayerDesignation);
  const flamsteed = flamsteedDesignation(reference.flamsteedNumber as number, reference.constellation);
  const flamsteedLong = flamsteedLongDesignation(reference.flamsteedNumber as number, reference.constellation);
  const title = name ?? bayer ?? flamsteedLong ?? sourceId;
  const shown = new Set<string | null>([title, ...(title === flamsteedLong ? [flamsteed] : [])]);
  return {
    title,
    subtitleParts: [bayer, flamsteed, sourcePart].filter((part): part is string => part !== null && !shown.has(part)),
  };
}
