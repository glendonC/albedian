import { z } from 'zod';
import {
  DisplayOrientationSchema,
  METADATA_TAG_PATTERN,
  MIME_TYPE_PATTERN,
  MetadataSourceSchema,
  JOB_ID_PATTERN,
  SHA256_HEX_PATTERN,
  SourceAssetSchema,
} from './media.js';
import { UTC_INSTANT_PATTERN } from './wire.js';

/**
 * Ingest contracts (roadmap Track 1 M1): what the backend returns when a photo or clip is uploaded,
 * the capture metadata read from the file as hints, each with the tags it came from, and the solve job
 * whose progress counts real work.
 *
 * Missing metadata stays missing: a hint is absent when the file does not hold it, and a capture time
 * written without a zone (EXIF DateTimeOriginal alone) stays a local time with no UTC, never a guess.
 * A time a file holds only in UTC (QuickTime's creation_time) has no local time, and a file's own time
 * is labelled as such: it may be when the file was written or exported, so it never becomes the
 * asset's capture time.
 *
 * Mirrored in Python by `services/contracts/ingest.py`. The shared samples in
 * `tests/fixtures/ingest_contract.json` are written by the Python side and parsed here.
 */

/** Pattern sources, identical to the Python mirror's, so both sides accept the same strings. */
export const INGEST_PATTERNS = {
  sha256: SHA256_HEX_PATTERN,
  mimeType: MIME_TYPE_PATTERN,
  metadataTag: METADATA_TAG_PATTERN,
  // A wall-clock time as the file writes it, with no zone; milliseconds when the file gives them
  localDateTime:
    '^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\\.[0-9]{3})?$',
  // An instant in UTC, written the one way both sides produce
  utcInstant: UTC_INSTANT_PATTERN,
  jobId: JOB_ID_PATTERN,
} as const;

const utcInstant = () => z.string().regex(new RegExp(INGEST_PATTERNS.utcInstant));
const label = () => z.string().min(1).max(128);

/** Offsets in use run from UTC-12:00 to UTC+14:00; the bound is symmetric at 14 hours. */
export const UTC_OFFSET_LIMIT_MINUTES = 840;

/**
 * What a capture time marks. `recorded`: the file says when the picture or clip was taken (EXIF
 * DateTimeOriginal, QuickTime com.apple.quicktime.creationdate). `file_written`: the file's own time
 * (QuickTime creation_time), which may be when the file was written or exported, not when it was
 * recorded. Only a recorded time becomes the asset's `capturedAtUTC`.
 */
export const CAPTURE_TIME_MARKS = ['recorded', 'file_written'] as const;
/** The EXIF GPS tags an offset's basis is read from. */
export const GPS_STAMP_TAGS = ['GPSDateStamp', 'GPSTimeStamp'] as const;

/**
 * A capture time as the file writes it: a local time, a UTC instant, or both. The offset is known
 * exactly when both are (then UTC is the local time minus the offset); a local time alone has no zone,
 * and a UTC instant alone has no local time.
 */
export const CaptureTimeHintSchema = z
  .object({
    localDateTime: z.string().regex(new RegExp(INGEST_PATTERNS.localDateTime)).nullable(),
    utcOffsetMinutes: z.number().int().min(-UTC_OFFSET_LIMIT_MINUTES).max(UTC_OFFSET_LIMIT_MINUTES).nullable(),
    utc: utcInstant().nullable(),
    marks: z.enum(CAPTURE_TIME_MARKS),
    source: MetadataSourceSchema,
  })
  .strict()
  .superRefine((hint, ctx) => {
    if (hint.localDateTime === null && hint.utc === null) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['localDateTime'], message: 'a capture time needs a local time, a UTC instant, or both' });
      return;
    }
    if ((hint.utcOffsetMinutes !== null) !== (hint.localDateTime !== null && hint.utc !== null)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['utcOffsetMinutes'], message: 'the offset is known exactly when both the local time and UTC are' });
      return;
    }
    if (hint.localDateTime !== null && hint.utc !== null && hint.utcOffsetMinutes !== null) {
      const expected = Date.parse(`${hint.localDateTime}Z`) - hint.utcOffsetMinutes * 60_000;
      if (Date.parse(hint.utc) !== expected) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['utc'], message: 'utc must be the local time minus the offset' });
      }
    }
    const basis = hint.source.basis;
    if (basis !== undefined) {
      const issue = (path: string[], message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
      if (hint.marks !== 'recorded' || hint.source.container !== 'exif' || hint.localDateTime === null) {
        issue(['source', 'basis'], 'a basis belongs to a recorded EXIF capture time');
      } else if ((basis.offsetSource === undefined) !== (hint.utcOffsetMinutes === null)) {
        issue(['source', 'basis', 'offsetSource'], "a basis names the offset's source exactly when the offset is known");
      } else if ((basis.gpsTimeStamp !== 'no_usable_time_stamp') !== GPS_STAMP_TAGS.every((tag) => hint.source.tags.includes(tag))) {
        issue(['source', 'tags'], 'a basis names the GPS date and time stamps among its tags exactly when it read them');
      }
    }
  });
export type CaptureTimeHint = z.infer<typeof CaptureTimeHintSchema>;

export const LocationHintSchema = z
  .object({
    latDeg: z.number().min(-90).max(90),
    lonDeg: z.number().min(-180).max(180),
    altitudeM: z.number().optional(),
    horizontalAccuracyM: z.number().positive().optional(),
    source: MetadataSourceSchema,
  })
  .strict();
export type LocationHint = z.infer<typeof LocationHintSchema>;

export const CAMERA_HINT_FIELDS = [
  'make',
  'model',
  'lensModel',
  'focalLengthMm',
  'focalLength35mmMm',
  'exposureTimeSec',
  'fNumber',
  'iso',
] as const;

export const CameraHintSchema = z
  .object({
    make: label().optional(),
    model: label().optional(),
    lensModel: label().optional(),
    focalLengthMm: z.number().positive().max(2000).optional(),
    focalLength35mmMm: z.number().positive().max(5000).optional(),
    exposureTimeSec: z.number().positive().max(3600).optional(),
    fNumber: z.number().positive().max(64).optional(),
    iso: z.number().positive().max(10_000_000).optional(),
    source: MetadataSourceSchema,
  })
  .strict()
  .refine((hint) => CAMERA_HINT_FIELDS.some((field) => hint[field] !== undefined), {
    message: 'a camera hint needs at least one value read from the file',
  });
export type CameraHint = z.infer<typeof CameraHintSchema>;

/** How to show the stored pixels, as the file says (EXIF Orientation, HEIF irot and imir, a clip's display matrix). */
export const OrientationHintSchema = DisplayOrientationSchema.extend({ source: MetadataSourceSchema }).strict();
export type OrientationHint = z.infer<typeof OrientationHintSchema>;

export const MetadataHintsSchema = z
  .object({
    captureTime: CaptureTimeHintSchema.optional(),
    location: LocationHintSchema.optional(),
    camera: CameraHintSchema.optional(),
    orientation: OrientationHintSchema.optional(),
  })
  .strict()
  .superRefine((hints, ctx) => {
    for (const key of ['location', 'camera', 'orientation'] as const) {
      if (hints[key]?.source.basis !== undefined) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: [key, 'source', 'basis'], message: "only a capture time's source has a basis" });
      }
    }
  });
export type MetadataHints = z.infer<typeof MetadataHintsSchema>;

export const JOB_STAGES = ['store', 'decode', 'detect', 'solve', 'verify'] as const;
export const PROGRESS_UNITS = ['bytes', 'frames', 'candidates', 'steps'] as const;

/** Progress counts real work: `total` is null until it is known, and then the interface shows elapsed time instead. */
export const JobStageProgressSchema = z
  .object({
    stage: z.enum(JOB_STAGES),
    unit: z.enum(PROGRESS_UNITS),
    done: z.number().int().nonnegative(),
    total: z.number().int().positive().nullable(),
  })
  .strict()
  .refine((progress) => progress.total === null || progress.done <= progress.total, {
    message: 'done cannot exceed total',
    path: ['done'],
  });
export type JobStageProgress = z.infer<typeof JobStageProgressSchema>;

export const JOB_STATES = ['queued', 'running', 'succeeded', 'failed', 'canceled'] as const;

export const SolveJobSchema = z
  .object({
    id: z.string().regex(new RegExp(INGEST_PATTERNS.jobId)),
    sourceAssetSha256: z.string().regex(new RegExp(INGEST_PATTERNS.sha256)),
    state: z.enum(JOB_STATES),
    createdAtUTC: utcInstant(),
    startedAtUTC: utcInstant().optional(),
    finishedAtUTC: utcInstant().optional(),
    stages: z.array(JobStageProgressSchema),
    solutionId: label().optional(),
    error: z.string().min(1).max(2000).optional(),
  })
  .strict()
  .superRefine((job, ctx) => {
    const has = {
      started: job.startedAtUTC !== undefined,
      finished: job.finishedAtUTC !== undefined,
      solution: job.solutionId !== undefined,
      error: job.error !== undefined,
    };
    const rules: Record<(typeof JOB_STATES)[number], boolean> = {
      queued: !has.started && !has.finished && !has.solution && !has.error,
      running: has.started && !has.finished && !has.solution && !has.error,
      succeeded: has.started && has.finished && has.solution && !has.error,
      failed: has.finished && has.error && !has.solution,
      canceled: has.finished && !has.solution && !has.error,
    };
    if (!rules[job.state]) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['state'], message: `fields do not fit a ${job.state} job` });
    }
    const created = Date.parse(job.createdAtUTC);
    const started = job.startedAtUTC === undefined ? undefined : Date.parse(job.startedAtUTC);
    const finished = job.finishedAtUTC === undefined ? undefined : Date.parse(job.finishedAtUTC);
    if ((started !== undefined && started < created) || (finished !== undefined && finished < (started ?? created))) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['finishedAtUTC'], message: 'times must run created, started, finished' });
    }
    const order = job.stages.map((progress) => JOB_STAGES.indexOf(progress.stage));
    if (order.some((index, i) => i > 0 && index <= order[i - 1]!)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['stages'], message: 'stages appear once each, in pipeline order' });
    }
  });
export type SolveJob = z.infer<typeof SolveJobSchema>;

const CAMERA_ASSET_FIELDS = CAMERA_HINT_FIELDS;

function sameSource(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** What an upload returns: the stored, content-addressed asset, the hints read from it, and its solve job. */
export const IngestReceiptSchema = z
  .object({
    sourceAsset: SourceAssetSchema,
    hints: MetadataHintsSchema,
    alreadyStored: z.boolean(),
    solveJobId: z.string().regex(new RegExp(INGEST_PATTERNS.jobId)).optional(),
  })
  .strict()
  .superRefine((receipt, ctx) => {
    const { sourceAsset: asset, hints } = receipt;
    const issue = (path: (string | number)[], message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
    // Uploads are the owner's own media: they stay private and on this machine
    if (asset.privacyClass !== 'private') issue(['sourceAsset', 'privacyClass'], 'an uploaded asset is private');
    // The backend probes every clip it keeps, so a stored clip carries the frame rate it read
    if (asset.mediaKind === 'video' && asset.fpsNumerator === undefined) {
      issue(['sourceAsset', 'fpsNumerator'], 'a stored clip carries its probed frame rate');
    }
    // The asset carries exactly what its hints read, with the same sources. Its capture time is a
    // recorded one: a file's own time stays a hint, labelled as such
    const recorded = hints.captureTime?.marks === 'recorded' ? (hints.captureTime.utc ?? undefined) : undefined;
    if (asset.capturedAtUTC !== recorded) {
      issue(['sourceAsset', 'capturedAtUTC'], 'capturedAtUTC must be the recorded capture time in UTC, or absent');
    }
    if (recorded !== undefined && !sameSource(asset.metadataSources?.capturedAtUTC, hints.captureTime?.source)) {
      issue(['sourceAsset', 'metadataSources', 'capturedAtUTC'], 'the capture time keeps its hint source');
    }
    const place = asset.observerLocation;
    if ((place === undefined) !== (hints.location === undefined)) {
      issue(['sourceAsset', 'observerLocation'], 'observerLocation must be the location hint, or absent');
    } else if (place !== undefined && hints.location !== undefined) {
      if (place.latDeg !== hints.location.latDeg || place.lonDeg !== hints.location.lonDeg) {
        issue(['sourceAsset', 'observerLocation'], 'observerLocation must be the location hint');
      }
      if (!sameSource(asset.metadataSources?.observerLocation, hints.location.source)) {
        issue(['sourceAsset', 'metadataSources', 'observerLocation'], 'the location keeps its hint source');
      }
    }
    const camera = asset.cameraMetadata;
    if ((camera === undefined) !== (hints.camera === undefined)) {
      issue(['sourceAsset', 'cameraMetadata'], 'cameraMetadata must be the camera hint, or absent');
    } else if (camera !== undefined && hints.camera !== undefined) {
      for (const field of CAMERA_ASSET_FIELDS) {
        if (camera[field] !== hints.camera[field]) issue(['sourceAsset', 'cameraMetadata', field], `${field} must be the camera hint's`);
      }
      if (!sameSource(asset.metadataSources?.cameraMetadata, hints.camera.source)) {
        issue(['sourceAsset', 'metadataSources', 'cameraMetadata'], 'the camera keeps its hint source');
      }
    }
    const orientation = asset.displayOrientation;
    if ((orientation === undefined) !== (hints.orientation === undefined)) {
      issue(['sourceAsset', 'displayOrientation'], 'displayOrientation must be the orientation hint, or absent');
    } else if (orientation !== undefined && hints.orientation !== undefined) {
      if (
        orientation.quarterTurnsClockwise !== hints.orientation.quarterTurnsClockwise ||
        orientation.mirrored !== hints.orientation.mirrored
      ) {
        issue(['sourceAsset', 'displayOrientation'], 'displayOrientation must be the orientation hint');
      }
      if (!sameSource(asset.metadataSources?.displayOrientation, hints.orientation.source)) {
        issue(['sourceAsset', 'metadataSources', 'displayOrientation'], 'the orientation keeps its hint source');
      }
    }
  });
export type IngestReceipt = z.infer<typeof IngestReceiptSchema>;
