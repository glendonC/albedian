import { z } from 'zod';
import { POINTING_RULES, PointingRecordSchema } from './pointing.js';
import { wireSha256 } from './wire.js';

/**
 * What the laptop answers to `PUT /api/ingest/{photo SHA-256}/pointing` (the record route, not built
 * yet): a receipt for a record it holds, or a refusal. Mirrored by services/contracts/pointing_reply.py; both are
 * judged by tests/fixtures/pointing_reply_contract.json, which the iPhone app's reader is tested against too.
 *
 * The receipt (a final 2xx) proves which record the laptop holds:
 * - `sourceAssetSha256` is the photo's, as in the address and in the record;
 * - `pointingRecordSha256` is the SHA-256 of the exact body bytes the route received and kept: the pointing record's
 *   own hash. The phone hashes the bytes it sent and compares, so a receipt means the laptop holds that record;
 * - `alreadyStored` is true only when a record with the same hash was already held for that photo (the same record
 *   sent again), and false when this request stored it. A different record already held for the same photo is never
 *   a receipt: the route refuses it with `differentRecordHeld`.
 *
 * A refusal (a 4xx) never repeats what it refuses: its detail and each issue's message are closed sentences (the
 * record's own rules and the route's), and an issue's path is made of the record's own wire keys. No value of the
 * record, no key the record made up, and no text of the server's own errors is in it. The API's own 413 for a body over
 * the route's limit (services/api.py `body_too_large_detail`) is the same answer on every route and is not a record
 * refusal.
 *
 * Strict on both sides by the wire rules: unknown keys and Python field names are refused at every level.
 */
export const POINTING_RECEIPT_FORMAT = 'albedian.pointing-receipt';
export const POINTING_RECEIPT_FORMAT_VERSION = 1;
/** [proposed] At most this many issues: one per field of the record is far more than any refusal lists. */
export const POINTING_REPLY_ISSUES_MAX = 32;
/** [proposed] An issue's path is at most this deep: the record nests four objects deep, so eight leaves room. */
export const POINTING_REPLY_PATH_MAX_DEPTH = 8;

/** The route's own refusal sentences, worded the same on both sides. */
export const POINTING_REPLY_SENTENCES = {
  differentRecordHeld: 'A different pointing record is already held for this photo.',
  anotherPhoto: 'The record names another photo than the one in its address.',
  noSuchPhoto: 'No photo with this SHA-256 is held, so no record is kept for it.',
  notARecord: 'The body is not a pointing record by its contract.',
  notJson: 'The body is not JSON text.',
  solveStarted: 'A solve job exists for this photo, so no pointing record can be added to it now.',
} as const;
/** Every sentence a refusal may say: the record's own rules, then the route's. */
export const POINTING_REFUSAL_SENTENCES = [...Object.values(POINTING_RULES), ...Object.values(POINTING_REPLY_SENTENCES)] as [
  string,
  ...string[],
];

/** Every wire key the pointing record holds at any level, read from its schema, so a path names only the record's own. */
function keysOf(schema: z.ZodTypeAny, into: Set<string>): Set<string> {
  if (schema instanceof z.ZodObject) {
    for (const [key, field] of Object.entries(schema.shape as Record<string, z.ZodTypeAny>)) {
      into.add(key);
      keysOf(field, into);
    }
  } else if (schema instanceof z.ZodEffects) keysOf(schema.innerType(), into);
  else if (schema instanceof z.ZodOptional) keysOf(schema.unwrap(), into);
  else if (schema instanceof z.ZodDiscriminatedUnion)
    for (const option of schema.options as z.ZodTypeAny[]) keysOf(option, into);
  return into;
}
export const POINTING_RECORD_KEYS = [...keysOf(PointingRecordSchema, new Set())].sort() as [string, ...string[]];

const notAPlaceholder = (value: string) => !/^0+$/.test(value);

export const PointingRecordReceiptSchema = z
  .object({
    format: z.literal(POINTING_RECEIPT_FORMAT),
    formatVersion: z.literal(POINTING_RECEIPT_FORMAT_VERSION),
    sourceAssetSha256: wireSha256().refine(notAPlaceholder, { message: POINTING_RULES.placeholderSha256 }),
    pointingRecordSha256: wireSha256().refine(notAPlaceholder, { message: POINTING_RULES.placeholderSha256 }),
    alreadyStored: z.boolean(),
  })
  .strict();
export type PointingRecordReceipt = z.infer<typeof PointingRecordReceiptSchema>;

export const PointingRefusalIssueSchema = z
  .object({
    path: z.array(z.enum(POINTING_RECORD_KEYS)).min(1).max(POINTING_REPLY_PATH_MAX_DEPTH),
    message: z.enum(POINTING_REFUSAL_SENTENCES),
  })
  .strict();
export type PointingRefusalIssue = z.infer<typeof PointingRefusalIssueSchema>;

export const PointingRecordRefusalSchema = z
  .object({
    detail: z.enum(POINTING_REFUSAL_SENTENCES),
    issues: z.array(PointingRefusalIssueSchema).max(POINTING_REPLY_ISSUES_MAX).optional(),
  })
  .strict();
export type PointingRecordRefusal = z.infer<typeof PointingRecordRefusalSchema>;
