import { z } from 'zod';

/**
 * The wire rules a record follows on both sides: the TypeScript half of `services/contracts/wire.py`.
 *
 * JSON is read differently by Python and by JavaScript, so a record is held to the subset both read the same way:
 * - Keys are camelCase and nothing else: a strict object refuses any other key, and the Python side refuses a
 *   Python field name with the message an unknown key gets.
 * - Every number is finite. An integer field holds a safe integer (magnitude at most 2^53 - 1); anything larger is
 *   refused, as `JSON.parse` would round it. A whole number written as a float (1.0, 1e0) is that integer, minus
 *   zero is zero, and a boolean is never a number.
 * - A length is counted in UTF-16 code units, which is what a JavaScript string's length is.
 * - A SHA-256 is 64 lower-case hex characters, and a UTC instant is one form, with Z, on a real calendar day.
 *
 * This module imports no other contract file, so every contract can use it. Today `media.ts` and the two shapes a
 * media record embeds (an audio stream and a display orientation) do; moving the other contracts onto it is a later
 * unit of the contracts lane.
 */

/** A SHA-256 digest as 64 lowercase hex characters: the identity of every source file. */
export const SHA256_HEX_PATTERN = '^[0-9a-f]{64}$';
const DATE = '[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])';
const TIME = '(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\\.[0-9]{3})?';
/** The one UTC form every record writes: seconds, milliseconds when nonzero, then Z. */
export const UTC_INSTANT_PATTERN = `^${DATE}T${TIME}Z$`;
export const WIRE_PATTERNS = { sha256: SHA256_HEX_PATTERN, utcInstant: UTC_INSTANT_PATTERN } as const;

/** The messages both sides give, word for word. */
export const WIRE_MESSAGES = {
  notANumber: 'a boolean is not a number',
  notFinite: 'a number is finite',
  notSafe: 'an integer is at most 2^53 - 1 in size',
  calendar: 'not a real calendar day',
} as const;

/** A finite number. */
export const wireNumber = () => z.number().finite({ message: WIRE_MESSAGES.notFinite });
/** A safe integer: whole (so finite), and at most 2^53 - 1 in size. */
export const wireInt = () => z.number().int().safe({ message: WIRE_MESSAGES.notSafe });

export const wireSha256 = () => z.string().regex(new RegExp(SHA256_HEX_PATTERN));

/** A time that fits its pattern also names a real day, from year 1 to 9999: `Date.parse` rolls 30 February into March. */
export function isRealCalendarDay(text: string): boolean {
  const year = Number(text.slice(0, 4));
  const month = Number(text.slice(5, 7));
  const day = Number(text.slice(8, 10));
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return year >= 1 && days !== undefined && day <= days;
}
export const wireUtcInstant = () =>
  z.string().regex(new RegExp(UTC_INSTANT_PATTERN)).refine(isRealCalendarDay, { message: WIRE_MESSAGES.calendar });
