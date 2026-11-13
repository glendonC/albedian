import { z } from 'zod';

/**
 * Asset provenance records: where every file the web app serves from `apps/web/public/` came
 * from, which version or when it was retrieved, under which licence, with what credit line, its
 * SHA-256, and the script that made it or that it was downloaded as is (roadmap Track 2 M1).
 *
 * One record file, `PROVENANCE.json`, sits in each asset folder and describes the files beside it
 * (a path may reach into a subfolder). A file entry pins one file by its SHA-256. A file set pins
 * a `SHA256SUMS` list beside the record, one `<sha256>  <relative path>` line per file, so a bake
 * output such as the catalog star tiles is covered without listing every tile in the record.
 *
 * Mirrored in Python by `services/contracts/provenance.py`, which bake scripts use to write records
 * and `scripts/verify_provenance.py` uses to check them against the files. The shared samples in
 * `tests/fixtures/provenance_contract.json` are written by the Python side and parsed here.
 */

export const ASSET_PROVENANCE_FORMAT = 'albedian.asset-provenance';
export const ASSET_PROVENANCE_FORMAT_VERSION = 1;
export const ASSET_PROVENANCE_RECORD_FILENAME = 'PROVENANCE.json';

/**
 * SPDX License List identifiers a record may name, each checked at https://spdx.org/licenses/
 * (License List 3.29.0, 2026-09-16; ODbL-1.0 and BSD-3-Clause on their pages on 2026-11-04; OFL-1.1, "SIL Open
 * Font License 1.1", on its page on 2026-11-13).
 * Terms without a listed identifier use the SPDX form `LicenseRef-<name>` with a URL to the
 * terms. Add an identifier only after checking it there, on both sides of the contract.
 */
export const SPDX_LICENSE_IDS = [
  'CC-BY-4.0',
  'CC-BY-SA-4.0',
  'CC-BY-NC-4.0',
  'CC-BY-NC-3.0-IGO',
  'CC0-1.0',
  'ODbL-1.0',
  'MIT',
  'Apache-2.0',
  'BSD-3-Clause',
  'OFL-1.1',
] as const;
export type SpdxLicenseId = (typeof SPDX_LICENSE_IDS)[number];

/**
 * Whitespace and invisible format characters, listed out so both regex engines agree: Unicode
 * White_Space plus the zero-width, bidirectional-control and invisible-operator characters, the
 * Mongolian vowel separator and the byte order mark. JavaScript's `\s` and Rust's `\s` (which
 * Pydantic uses) disagree on U+0085 and U+FEFF, so no pattern here relies on `\s` or `\S`.
 */
export const BLANK_CHARACTERS =
  '\\t\\n\\u000b\\f\\r \\u0085\\u00a0\\u1680\\u180e\\u2000-\\u200f\\u2028-\\u202f\\u205f-\\u2064\\u3000\\ufeff';

/** Pattern sources, identical to the Python mirror's, so both sides accept the same strings. */
export const ASSET_PROVENANCE_PATTERNS = {
  id: '^[a-z0-9]+(?:[._-][a-z0-9]+)*$',
  // Relative POSIX path inside the record's folder: no leading slash, no empty, "." or ".."
  // segment, no hidden file, no backslash
  path: '^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$',
  sha256: '^[0-9a-f]{64}$',
  httpUrl: `^https?://[^${BLANK_CHARACTERS}/]+[^${BLANK_CHARACTERS}]*$`,
  doi: `^10\\.[0-9]{4,9}/[^${BLANK_CHARACTERS}]+$`,
  isoDate: '^(?:19|20|21)[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$',
  licenseRef: '^LicenseRef-[A-Za-z0-9.-]+$',
  // Text that neither starts nor ends with a blank character, so a rewrite never changes its bytes
  text: `^[^${BLANK_CHARACTERS}](?:[\\s\\S]*[^${BLANK_CHARACTERS}])?$`,
} as const;

/** Lengths count Unicode code points, as Python's `len` does, not UTF-16 code units. */
export const ASSET_PROVENANCE_LIMITS = {
  shortTextMaxLength: 300,
  creditMaxLength: 200,
  textMaxLength: 4000,
  urlMaxLength: 2000,
  pathMaxLength: 400,
} as const;

const pattern = (name: keyof typeof ASSET_PROVENANCE_PATTERNS) => new RegExp(ASSET_PROVENANCE_PATTERNS[name]);
const LIMITS = ASSET_PROVENANCE_LIMITS;

/** A string matching `name`'s pattern with at most `max` Unicode code points (an astral character counts once). */
const bounded = (name: keyof typeof ASSET_PROVENANCE_PATTERNS, max: number) =>
  z
    .string()
    .regex(pattern(name))
    .refine((value) => [...value].length <= max, { message: `At most ${max} characters.` });

/** A YYYY-MM-DD string that names a day on the calendar (no February 30). */
function isCalendarDate(value: string): boolean {
  const [year, month, day] = value.split('-').map(Number) as [number, number, number];
  const date = new Date(Date.UTC(2000, 0, 1));
  date.setUTCFullYear(year, month - 1, day);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

const shortText = bounded('text', LIMITS.shortTextMaxLength);
const creditLine = bounded('text', LIMITS.creditMaxLength);
const longText = bounded('text', LIMITS.textMaxLength);
const httpUrl = bounded('httpUrl', LIMITS.urlMaxLength);
const sha256 = z.string().regex(pattern('sha256'));
const sourceId = z.string().regex(pattern('id'));
const relativePath = z.string().max(LIMITS.pathMaxLength).regex(pattern('path'));
const isoDate = z.string().regex(pattern('isoDate')).refine(isCalendarDate, { message: 'Not a calendar date.' });
const doi = z.string().regex(pattern('doi'));

export const ProvenanceLicenseSchema = z
  .object({
    // An identifier from SPDX_LICENSE_IDS, or LicenseRef-<name> for terms the SPDX list lacks
    spdx: z.string().refine((value) => (SPDX_LICENSE_IDS as readonly string[]).includes(value) || pattern('licenseRef').test(value), {
      message: 'Neither a pinned SPDX License List id nor a LicenseRef- id.',
    }),
    name: shortText,
    url: httpUrl,
    // Obligations beyond the licence text, such as an authorisation needed for commercial use
    conditions: longText.optional(),
  })
  .strict();
export type ProvenanceLicense = z.infer<typeof ProvenanceLicenseSchema>;

export const ProvenanceSourceSchema = z
  .object({
    id: sourceId,
    title: shortText,
    // Where the bytes were retrieved: a download URL, an archive service or a landing page
    url: httpUrl,
    // The release or edition as the provider names it
    version: shortText.optional(),
    // The date the bytes were fetched, YYYY-MM-DD
    retrieved: isoDate.optional(),
    license: ProvenanceLicenseSchema,
    // The credit line the licence asks to display beside the asset
    credit: creditLine,
    citation: longText.optional(),
    doi: doi.optional(),
    acknowledgement: longText.optional(),
    // SHA-256 of the downloaded input, when it is kept
    sha256: sha256.optional(),
    note: longText.optional(),
  })
  .strict()
  .refine((source) => source.version !== undefined || source.retrieved !== undefined, {
    message: 'A source needs a version or a retrieval date.',
    path: ['retrieved'],
  });
export type ProvenanceSource = z.infer<typeof ProvenanceSourceSchema>;

export const ProvenanceProducerSchema = z.discriminatedUnion('method', [
  // The provider's file, byte for byte
  z.object({ method: z.literal('downloaded-as-is') }).strict(),
  // Made by a script in this repository (repository-relative path)
  z.object({ method: z.literal('script'), script: relativePath, command: shortText.optional() }).strict(),
]);
export type ProvenanceProducer = z.infer<typeof ProvenanceProducerSchema>;

const sourceReferences = z
  .array(sourceId)
  .min(1)
  .refine((ids) => new Set(ids).size === ids.length, { message: 'An entry names the same source more than once.' });

/** One asset file beside the record, pinned by its SHA-256. */
export const ProvenanceFileSchema = z
  .object({
    path: relativePath,
    sha256,
    sources: sourceReferences,
    producedBy: ProvenanceProducerSchema,
    note: longText.optional(),
  })
  .strict();
export type ProvenanceFile = z.infer<typeof ProvenanceFileSchema>;

/** Every file a SHA256SUMS list beside the record names, with the list itself pinned. */
export const ProvenanceFileSetSchema = z
  .object({
    checksums: relativePath,
    sha256,
    sources: sourceReferences,
    producedBy: ProvenanceProducerSchema,
    note: longText.optional(),
  })
  .strict();
export type ProvenanceFileSet = z.infer<typeof ProvenanceFileSetSchema>;

/** The `PROVENANCE.json` of one asset folder. */
export const AssetProvenanceRecordSchema = z
  .object({
    format: z.literal(ASSET_PROVENANCE_FORMAT),
    formatVersion: z.literal(ASSET_PROVENANCE_FORMAT_VERSION),
    sources: z.array(ProvenanceSourceSchema).min(1),
    files: z.array(ProvenanceFileSchema),
    fileSets: z.array(ProvenanceFileSetSchema),
  })
  .strict()
  .superRefine((record, ctx) => {
    const issue = (message: string, path: (string | number)[] = []) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    const defined = record.sources.map((source) => source.id);
    if (new Set(defined).size !== defined.length) issue('A source id appears more than once.', ['sources']);
    const entries = [...record.files, ...record.fileSets];
    if (entries.length === 0) issue('A record describes at least one file or file set.');
    const referenced = new Set(entries.flatMap((entry) => entry.sources));
    const undefinedIds = [...referenced].filter((id) => !defined.includes(id)).sort();
    if (undefinedIds.length > 0) issue(`Entries name undefined sources: ${undefinedIds.join(', ')}.`);
    const unused = defined.filter((id) => !referenced.has(id));
    if (unused.length > 0) issue(`Sources no entry names: ${unused.join(', ')}.`, ['sources']);
    const paths = [...record.files.map((file) => file.path), ...record.fileSets.map((set) => set.checksums)];
    if (new Set(paths).size !== paths.length) issue('A path appears more than once.');
    if (paths.includes(ASSET_PROVENANCE_RECORD_FILENAME)) {
      issue(`${ASSET_PROVENANCE_RECORD_FILENAME} is the record, not an asset it describes.`);
    }
  });
export type AssetProvenanceRecord = z.infer<typeof AssetProvenanceRecordSchema>;
