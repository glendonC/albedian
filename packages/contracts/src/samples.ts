import { z } from 'zod';

/**
 * The sample night's record (first-use-sample-one, DECIDED-BY-LEAD 25 Nov 03:22 and 03:43): a public photo the app
 * opens with no photo of the person's own, named as the corpus photos are (its entry in data/downloads.json), with its
 * credit and licence, said to be a sample wherever it shows, and where on this machine the app's own solve of it is
 * kept with its hash beside it (never tracked, never cut). The mirror of services/contracts/samples.py; the record is
 * data/samples.json.
 */
export const SAMPLE_ID_PATTERN = /^night-[a-z0-9-]{1,40}$/;
const PHOTO_PATH_PATTERN = /^\.cache\/real-photos\/[a-z0-9-]{1,40}\/[a-z0-9-]{1,40}\.(jpg|jpeg|png)$/;
const SOLUTION_PATH_PATTERN = /^\.cache\/samples\/[a-z0-9-]{1,40}\/[a-z0-9.-]{1,60}$/;
// Printable ASCII only, so both languages read an address alike; lengths are UTF-16 units on both sides
const HTTPS_URL = /^https:\/\/[!-~]{1,300}$/;
const text = z.string().min(1).max(200);

export const SamplePhotoSchema = z
  .object({
    downloadPath: z.string().regex(PHOTO_PATH_PATTERN),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    bytes: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    mimeType: z.enum(['image/jpeg', 'image/png']),
  })
  .strict();

export const SampleCreditSchema = z
  .object({
    author: text,
    licence: z.enum(['CC0', 'CC BY 4.0', 'CC BY-SA 4.0']),
    licenceUrl: z.string().regex(HTTPS_URL),
    sourceUrl: z.string().regex(HTTPS_URL),
  })
  .strict();

export const SampleSolutionSchema = z
  .object({
    path: z.string().regex(SOLUTION_PATH_PATTERN),
    sha256Path: z.string().regex(SOLUTION_PATH_PATTERN),
  })
  .strict();

export const SampleNightSchema = z
  .object({
    id: z.string().regex(SAMPLE_ID_PATTERN),
    // Said to be a sample wherever it shows
    kind: z.literal('sample'),
    title: text,
    photo: SamplePhotoSchema,
    credit: SampleCreditSchema,
    solution: SampleSolutionSchema,
  })
  .strict();
export type SampleNight = z.infer<typeof SampleNightSchema>;

export const SampleRecordSchema = z
  .object({
    about: z.string().min(1).max(2000),
    samples: z.array(SampleNightSchema).min(1).max(8),
  })
  .strict();
