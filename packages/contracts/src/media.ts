import { z } from 'zod';
import {
  SHA256_HEX_PATTERN,
  UTC_INSTANT_PATTERN,
  WIRE_MESSAGES,
  wireInt,
  wireNumber,
  wireSha256,
  wireUtcInstant,
} from './wire.js';

export { SHA256_HEX_PATTERN } from './wire.js';

/**
 * Media contracts: the immutable SourceAsset with its orientation helpers, and the records of restoring and
 * exporting a stored upload (spec restoration-for-every-upload, design section 14): the request, the graph of the
 * steps that ran, the attempt with what it measured and the integrity gate's result, the stored variant, the job and
 * the export manifest.
 */

export const AudioStreamDescriptorSchema = z.object({
  index: wireInt().nonnegative(),
  codec: z.string().min(1),
  sampleRateHz: wireInt().positive(),
  channels: wireInt().positive(),
  startPtsMs: wireNumber(),
  durationMs: wireNumber().nonnegative(),
  sha256: z.string().min(8),
});
export type AudioStreamDescriptor = z.infer<typeof AudioStreamDescriptorSchema>;

/**
 * Where a piece of capture metadata came from: the container that held it and the tags read for it,
 * in the order the reader tried them. `heif` is a HEIF image's item properties (`irot`, `imir`), which
 * are neither EXIF nor QuickTime. `synthetic` marks test data, never a real file.
 */
export const METADATA_CONTAINERS = ['exif', 'quicktime', 'heif', 'xmp', 'user', 'synthetic'] as const;
export const METADATA_TAG_PATTERN = '^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$';
export const MetadataSourceSchema = z
  .object({
    container: z.enum(METADATA_CONTAINERS),
    tags: z.array(z.string().regex(new RegExp(METADATA_TAG_PATTERN))).min(1).max(8),
  })
  .strict();
export type MetadataSource = z.infer<typeof MetadataSourceSchema>;

/** A MIME type as a browser or ffprobe reports it, for example `image/jpeg` or `video/quicktime`. */
export const MIME_TYPE_PATTERN = '^[a-z]+/[a-z0-9.+-]+$';

/**
 * How to show stored pixels as the camera meant them: mirror them left to right if `mirrored`, then
 * turn them clockwise by `quarterTurnsClockwise` times 90 degrees. Pixels, detections and the WCS stay
 * in stored coordinates; only display applies this. EXIF Orientation, HEIF `irot` and `imir`, and a
 * clip's display matrix all convert to this one form.
 */
export const DisplayOrientationSchema = z
  .object({
    quarterTurnsClockwise: wireInt().min(0).max(3),
    mirrored: z.boolean(),
  })
  .strict();
export type DisplayOrientation = z.infer<typeof DisplayOrientationSchema>;

/** EXIF Orientation 1 to 8 in the canonical form, one to one (EXIF 2.3, tag 0x0112). */
export const EXIF_ORIENTATIONS: Readonly<Record<1 | 2 | 3 | 4 | 5 | 6 | 7 | 8, DisplayOrientation>> = {
  1: { quarterTurnsClockwise: 0, mirrored: false },
  2: { quarterTurnsClockwise: 0, mirrored: true },
  3: { quarterTurnsClockwise: 2, mirrored: false },
  4: { quarterTurnsClockwise: 2, mirrored: true },
  5: { quarterTurnsClockwise: 3, mirrored: true },
  6: { quarterTurnsClockwise: 1, mirrored: false },
  7: { quarterTurnsClockwise: 1, mirrored: true },
  8: { quarterTurnsClockwise: 3, mirrored: false },
};

/**
 * `first`, then `then`. A mirror reverses the sense of the turns that came before it, since mirroring
 * after a clockwise turn equals mirroring first and then turning anticlockwise.
 */
export function composeDisplayOrientations(first: DisplayOrientation, then: DisplayOrientation): DisplayOrientation {
  const carried = then.mirrored ? -first.quarterTurnsClockwise : first.quarterTurnsClockwise;
  return {
    quarterTurnsClockwise: (((then.quarterTurnsClockwise + carried) % 4) + 4) % 4,
    mirrored: first.mirrored !== then.mirrored,
  };
}

/** The orientation that undoes `orientation`: composed with it, in either order, it shows pixels as stored. */
export function invertDisplayOrientation(orientation: DisplayOrientation): DisplayOrientation {
  const turns = orientation.mirrored ? orientation.quarterTurnsClockwise : -orientation.quarterTurnsClockwise;
  return { quarterTurnsClockwise: ((turns % 4) + 4) % 4, mirrored: orientation.mirrored };
}

/**
 * The size a stored frame is shown at: a quarter or three-quarter turn swaps width and height.
 * Detections and the WCS stay in stored pixels; only display applies the orientation.
 */
export function orientedSize(
  storedWidthPx: number,
  storedHeightPx: number,
  orientation?: DisplayOrientation
): { widthPx: number; heightPx: number } {
  const turned = (orientation?.quarterTurnsClockwise ?? 0) % 2 === 1;
  return turned
    ? { widthPx: storedHeightPx, heightPx: storedWidthPx }
    : { widthPx: storedWidthPx, heightPx: storedHeightPx };
}

/**
 * Where a point of the stored frame is shown once the frame is oriented as the camera meant it, in the
 * canonical form: mirror left to right if `mirrored`, then turn clockwise a quarter at a time. Points are
 * image coordinates: (0, 0) is the top-left corner of the frame and (width, height) its bottom-right.
 */
export function orientStoredPoint(
  xPx: number,
  yPx: number,
  storedWidthPx: number,
  storedHeightPx: number,
  orientation?: DisplayOrientation
): { xPx: number; yPx: number } {
  let x = orientation?.mirrored ? storedWidthPx - xPx : xPx;
  let y = yPx;
  let height = storedHeightPx;
  let width = storedWidthPx;
  for (let turn = 0; turn < (orientation?.quarterTurnsClockwise ?? 0); turn++) {
    // A clockwise quarter turn sends (x, y) in a width x height frame to (height - y, x) in height x width
    [x, y] = [height - y, x];
    [width, height] = [height, width];
  }
  return { xPx: x, yPx: y };
}

/** Capture metadata a SourceAsset may carry; each one present needs its entry in `metadataSources`. */
export const SOURCE_ASSET_METADATA_FIELDS = ['capturedAtUTC', 'observerLocation', 'cameraMetadata', 'displayOrientation'] as const;

export const SourceAssetSchema = z
  .object({
    id: z.string().min(1),
    title: z.string().min(1),
    mediaKind: z.enum(['image', 'video']),
    sha256: z.string().regex(new RegExp(SHA256_HEX_PATTERN)),
    uri: z.string().min(1),
    byteSize: z.number().int().positive().optional(),
    mimeType: z.string().regex(new RegExp(MIME_TYPE_PATTERN)).optional(),
    width: z.number().int().positive(),
    height: z.number().int().positive(),
    durationMs: z.number().nonnegative(),
    // A clip's frame rate, both fields once probed, neither until then; a still has none
    fpsNumerator: z.number().int().positive().optional(),
    fpsDenominator: z.number().int().positive().optional(),
    videoCodec: z.string().min(1),
    audioStreams: z.array(AudioStreamDescriptorSchema),
    capturedAtUTC: z.string().datetime().optional(),
    observerLocation: z
      .object({
        latDeg: z.number().min(-90).max(90),
        lonDeg: z.number().min(-180).max(180),
        elevationM: z.number().optional(),
        placeName: z.string().optional(),
        uncertaintyMeters: z.number().nonnegative().optional(),
      })
      .optional(),
    cameraMetadata: z
      .object({
        make: z.string().optional(),
        model: z.string().optional(),
        lensModel: z.string().optional(),
        focalLengthMm: z.number().positive().optional(),
        focalLength35mmMm: z.number().positive().optional(),
        estimatedFovDeg: z.number().positive().max(175).optional(),
        exposureTimeSec: z.number().positive().optional(),
        fNumber: z.number().positive().optional(),
        iso: z.number().positive().optional(),
      })
      .optional(),
    // Absent when the file does not say; a file that says "as stored" gives 0 turns, not mirrored
    displayOrientation: DisplayOrientationSchema.optional(),
    metadataSources: z
      .object({
        capturedAtUTC: MetadataSourceSchema.optional(),
        observerLocation: MetadataSourceSchema.optional(),
        cameraMetadata: MetadataSourceSchema.optional(),
        displayOrientation: MetadataSourceSchema.optional(),
      })
      .strict()
      .optional(),
    privacyClass: z.enum(['private', 'consented_demo', 'public_sample']),
    immutable: z.literal(true),
  })
  .superRefine((asset, ctx) => {
    // A frame rate is a clip's and needs both fields; a still has none, so it carries neither
    const hasNumerator = asset.fpsNumerator !== undefined;
    const hasDenominator = asset.fpsDenominator !== undefined;
    if (hasNumerator !== hasDenominator) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: [hasNumerator ? 'fpsDenominator' : 'fpsNumerator'],
        message: 'a frame rate needs both its numerator and its denominator',
      });
    } else if (hasNumerator && asset.mediaKind === 'image') {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['fpsNumerator'], message: 'a still has no frame rate' });
    }
    // Recorded where it came from, or not recorded at all: no capture metadata without its source
    for (const field of SOURCE_ASSET_METADATA_FIELDS) {
      const present = asset[field] !== undefined;
      const sourced = asset.metadataSources?.[field] !== undefined;
      if (present !== sourced) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['metadataSources', field],
          message: present ? `${field} needs the metadata source it came from` : `a source for ${field}, which is absent`,
        });
      }
    }
  });
export type SourceAsset = z.infer<typeof SourceAssetSchema>;

// =============================================================================================================
// Restoring and exporting a stored upload (spec restoration-for-every-upload, design section 14; REQ-RST-10)
//
// Mirrored in Python by `services/contracts/media.py`; the shared samples in `tests/fixtures/media_contract.json`
// are written by the Python side and parsed by `__tests__/media.test.ts`, and each invalid sample names the
// refinement message both sides refuse it with. Nothing produces these records yet: the units after restore-1 do.
// The job that makes them is the upload job runner's record (spec upload-jobs), not one of these.
//
// The wire rules (camelCase keys, finite and safe numbers, UTF-16 lengths, hashes, the UTC instant) are `wire.ts`'s.
// No record holds free text (REQ-RST-8.5, 9.4): a failure reason is one of the fixed sentences; a parameter's
// basis, an assumption's reason and a note come from the closed lists below, where a template's number slot holds
// only a whole number in its range, written without a leading zero; every id and hash has the exact form its minting
// code writes; and a parameter is one row of the step parameter table, its value of that row's type and range.
// =============================================================================================================

export const VARIANT_KINDS = ['display_copy', 'restoration'] as const;
export type VariantKind = (typeof VARIANT_KINDS)[number];
export const PARAMETER_ORIGINS = ['measured', 'requested', 'default'] as const;
export type ParameterOrigin = (typeof PARAMETER_ORIGINS)[number];
/** Pipeline order: a graph lists its steps in this order, each at most once. */
export const GRAPH_STEP_KINDS = [
  'decode',
  'find_stars',
  'measure_noise',
  'register',
  'temporal_merge',
  'spatial_denoise',
  'dissolve_unconfirmed',
  'lift',
  'encode',
  'copy_audio',
] as const;
export type GraphStepKind = (typeof GRAPH_STEP_KINDS)[number];
/** Each id as the code that mints it writes it (design section 9; `services/ingest/router.py` for src_, REQ-JOB-5.8 for sol_). */
export const MEDIA_ID_PATTERNS = {
  graph: '^graph_[0-9a-f]{16}$',
  variant: '^var_[0-9a-f]{16}$',
  attempt: '^att_[0-9a-f]{16}$',
  sourceAsset: '^src_[0-9a-f]{16}$',
  solution: '^sol_[0-9a-f]{16}$',
} as const;
/** The upload job runner's id (upload jobs design 3.1 and 3.8), kept beside the hash pattern; ingest.ts takes it from here. */
export const JOB_ID_PATTERN = '^job_[0-9a-f]{16,64}$';
export const DISCLOSURES = {
  display_copy: "The original's own pixels, re-encoded without loss so a browser can show them. Nothing was restored.",
  restoration: "Restored from the original's own pixels with deterministic filters. Nothing was generated.",
} as const;

/** Pattern sources, identical to the Python mirror's, so both sides accept the same strings. */
export const MEDIA_PATTERNS = {
  sha256: SHA256_HEX_PATTERN,
  utcInstant: UTC_INSTANT_PATTERN,
  // [0-9], not \d (pydantic's regex takes any Unicode digit for \d); each part 0 to 99 with no leading zero
  // [proposed: room for every version this pipeline will have, none for a year or a date]
  semver: '^(?:0|[1-9][0-9]?)\\.(?:0|[1-9][0-9]?)\\.(?:0|[1-9][0-9]?)$',
  // A stored variant is served at its own route (REQ-RST-8.6), never named by a path in the store
  variantUri: '^/api/media/[0-9a-f]{64}/variants/var_[0-9a-f]{16}/file$',
  // A codec as ffprobe names it [proposed: lower-case letters, digits and underscore, at most 32]
  codecName: '^[a-z0-9_]{1,32}$',
} as const;

export const COLOUR_MODELS = ['rgb', 'ycbcr'] as const;
export const COLOUR_BIT_DEPTHS = [8, 10, 16] as const;
export const COLOUR_RANGES = ['full', 'limited'] as const;
export const COLOUR_MATRICES = ['identity', 'bt601', 'bt709', 'bt2020nc'] as const;
export const COLOUR_TRANSFERS = ['srgb', 'bt709', 'hlg', 'pq', 'icc_parametric', 'icc_table'] as const;
export const COLOUR_PRIMARIES = ['bt709', 'p3_d65', 'bt2020', 'icc', 'unspecified'] as const;
export const CHROMA_SUBSAMPLINGS = ['444', '422', '420'] as const;
export const CHROMA_SITINGS = ['left', 'center', 'topleft'] as const;
export const ASSUMABLE_COLOUR_FIELDS = ['range', 'matrix', 'transfer', 'primaries', 'chromaSiting'] as const;
/** Every value an assumable field can hold, so an assumed value is one of them. */
export const ASSUMABLE_COLOUR_VALUES = Array.from(
  new Set<string>([...COLOUR_RANGES, ...COLOUR_MATRICES, ...COLOUR_TRANSFERS, ...COLOUR_PRIMARIES, ...CHROMA_SITINGS])
) as [string, ...string[]];
export const GATE_STATISTICS = ['detector', 'detector_local_background_removed'] as const;
export const NOISE_NOT_MEASURED_REASONS = ['too_few_flat_blocks', 'fine_detail_mostly_zero', 'frame_too_small'] as const;
export const LIFT_DECIDED_BY = ['none', 'target', 'requested', 'noise_bound', 'quantisation_bound', 'maximum'] as const;
export const ATTEMPT_STATUSES = ['ready', 'rejected', 'failed'] as const;
export const REJECTION_REASONS = ['unconfirmed_points', 'star_flux_changed', 'frame_times_differ', 'audio_differs', 'copy_differs'] as const;
/** The rejections the gate itself makes; the others come before the gate (design section 10) or without one. */
export const GATE_REJECTIONS = ['unconfirmed_points', 'star_flux_changed'] as const;
export const VARIANT_MIME_TYPES = ['image/png', 'video/mp4', 'video/quicktime'] as const;
/** The decoder's own pixel formats the pipeline reads and writes (design section 1; restore-5's clip_io). */
export const PIXEL_FORMATS = [
  'yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'yuv420p10le', 'yuv422p10le', 'yuv444p10le', 'rgb24', 'rgb48le',
] as const;
export const VARIANT_CONTAINERS = ['png', 'mov', 'mp4'] as const;
export const VARIANT_ENCODERS = ['png', 'libx264', 'libx265'] as const;
/** x264's and x265's preset names (design section 10). */
export const ENCODER_PRESETS = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo'] as const;
export const EXPORT_MANIFEST_FORMAT = 'albedian.export-manifest';
export const EXPORT_MANIFEST_FORMAT_VERSION = 1;
/** The largest exposure a request may ask for, in EV [proposed in design section 14; the lift's own bounds decide]. */
export const EXPOSURE_EV_MAX = 6;

export const MEDIA_ENUMS = {
  variantKinds: VARIANT_KINDS,
  parameterOrigins: PARAMETER_ORIGINS,
  graphStepKinds: GRAPH_STEP_KINDS,
  colourModels: COLOUR_MODELS,
  colourBitDepths: COLOUR_BIT_DEPTHS,
  colourRanges: COLOUR_RANGES,
  colourMatrices: COLOUR_MATRICES,
  colourTransfers: COLOUR_TRANSFERS,
  colourPrimaries: COLOUR_PRIMARIES,
  chromaSubsamplings: CHROMA_SUBSAMPLINGS,
  chromaSitings: CHROMA_SITINGS,
  assumableColourFields: ASSUMABLE_COLOUR_FIELDS,
  assumableColourValues: ASSUMABLE_COLOUR_VALUES,
  gateStatistics: GATE_STATISTICS,
  noiseNotMeasuredReasons: NOISE_NOT_MEASURED_REASONS,
  liftDecidedBy: LIFT_DECIDED_BY,
  attemptStatuses: ATTEMPT_STATUSES,
  rejectionReasons: REJECTION_REASONS,
  gateRejections: GATE_REJECTIONS,
  variantMimeTypes: VARIANT_MIME_TYPES,
  pixelFormats: PIXEL_FORMATS,
  variantContainers: VARIANT_CONTAINERS,
  variantEncoders: VARIANT_ENCODERS,
  encoderPresets: ENCODER_PRESETS,
} as const;

/**
 * The step parameter table [proposed: the parameters design sections 1 to 10 name for each step; a unit that needs
 * another adds its row here first]: each parameter's steps, its type, and its range or its values. Ranges are bounds
 * a value can never pass, not the values the design proposes.
 */
type NumericParameter = { name: string; steps: readonly GraphStepKind[]; type: 'integer' | 'number'; min: number; max: number; unit: string };
type TextParameter = { name: string; steps: readonly GraphStepKind[]; type: 'text'; values: readonly string[] };
export type StepParameter = NumericParameter | TextParameter;
const integer = (name: string, steps: readonly GraphStepKind[], min: number, max: number, unit: string): StepParameter => ({
  name, steps, type: 'integer', min, max, unit,
});
const number = (name: string, steps: readonly GraphStepKind[], min: number, max: number, unit: string): StepParameter => ({
  name, steps, type: 'number', min, max, unit,
});
const text = (name: string, steps: readonly GraphStepKind[], values: readonly string[]): StepParameter => ({ name, steps, type: 'text', values });
export const STEP_PARAMETERS: readonly StepParameter[] = [
  text('transfer', ['decode'], COLOUR_TRANSFERS),
  text('matrix', ['decode'], COLOUR_MATRICES),
  text('range', ['decode'], COLOUR_RANGES),
  text('primaries', ['decode'], COLOUR_PRIMARIES),
  text('chromaSubsampling', ['decode'], CHROMA_SUBSAMPLINGS),
  text('chromaSiting', ['decode'], CHROMA_SITINGS),
  text('pixelFormat', ['decode', 'encode'], PIXEL_FORMATS),
  integer('bitDepth', ['decode', 'encode'], 8, 16, 'bits'),
  text('container', ['encode'], VARIANT_CONTAINERS),
  text('encoder', ['encode'], VARIANT_ENCODERS),
  text('preset', ['encode'], ENCODER_PRESETS),
  integer('crf', ['encode'], 0, 51, 'x264 quality'),
  integer('zlibLevel', ['encode'], 0, 9, 'zlib level'),
  integer('tilePx', ['find_stars'], 16, 4096, 'px'),
  integer('overlapPx', ['find_stars'], 0, 1024, 'px'),
  number('confirmThresholdSnr', ['find_stars'], 0, 100, 'signal to noise'),
  number('matchRadiusPx', ['find_stars'], 0, 64, 'px'),
  number('mergeRadiusPx', ['find_stars'], 0, 64, 'px'),
  integer('footprintMaxPx', ['find_stars'], 1, 256, 'px'),
  integer('clippedGrowPx', ['find_stars'], 0, 64, 'px'),
  text('statistic', ['find_stars', 'dissolve_unconfirmed'], GATE_STATISTICS),
  integer('bins', ['measure_noise'], 3, 24, 'intensity bins'),
  integer('blockPx', ['measure_noise', 'register'], 2, 4096, 'px'),
  integer('windowFrames', ['register', 'temporal_merge'], 0, 30, 'frames either side'),
  number('closureMaxPx', ['register'], 0, 100, 'px'),
  number('blockResidualMaxPx', ['register'], 0, 100, 'px'),
  integer('windowPx', ['temporal_merge'], 1, 31, 'px'),
  number('toleranceFactor', ['temporal_merge'], 0, 100, 'noise variances'),
  integer('levels', ['spatial_denoise'], 1, 8, 'wavelet levels'),
  number('sigmaScale', ['spatial_denoise'], 0, 100, 'noise sigmas'),
  integer('maxPasses', ['dissolve_unconfirmed'], 0, 3, 'passes'),
  number('variantThresholdSnr', ['dissolve_unconfirmed'], 0, 100, 'signal to noise'),
  number('gainAtBlack', ['lift'], 1, 1000, 'gain'),
  integer('targetMedianCode', ['lift'], 0, 255, 'code of 255'),
  number('quantisationBoundSteps', ['lift'], 0, 256, 'output code steps'),
  number('maximumGain', ['lift'], 1, 1000, 'gain'),
  number('exposureEv', ['lift'], 0, EXPOSURE_EV_MAX, 'EV'),
  integer('streams', ['copy_audio'], 0, 64, 'audio streams'),
];
export const PARAMETER_NAMES = STEP_PARAMETERS.map((parameter) => parameter.name) as [string, ...string[]];
const PARAMETER_BY_NAME = new Map(STEP_PARAMETERS.map((parameter) => [parameter.name, parameter]));

/** The format names a refusal may give (design section 1 for stills; section 10 and restore-5's clip_io for clips). */
export const FAILURE_FORMATS = [
  'a clip with no video stream',
  'a clip with more than one video stream',
  'an interlaced clip',
  'a clip of more than 10 bits',
  'a clip with an alpha plane',
  "a clip whose pixels are not planar Y'CbCr at 8 or 10 bits",
  'an odd frame size with subsampled chroma',
  'a time base that is not one over a whole number',
  'a colour range tag other than limited or full',
  'a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance',
  'a transfer other than sRGB, BT.709, HLG or PQ',
  'colour primaries this pipeline cannot tag',
  'a chroma siting this pipeline cannot tag',
  'a container other than QuickTime or MP4',
  'an image with an alpha plane',
  'a camera raw layout',
  'an image of more than 16 bits',
  'an ICC curve that does not increase',
] as const;
/** The capabilities a refusal may name (design sections 1 and 10; restore-5's clip_io). */
export const FAILURE_CAPABILITIES = [
  'a display orientation on a copied stream',
  ...['yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p'].map((format) => `8 bit H.264 encoding of ${format}`),
  ...['yuv420p10le', 'yuv422p10le', 'yuv444p10le'].map((format) => `10 bit HEVC encoding of ${format}`),
  'reading a tiled HEIC',
] as const;
/**
 * The acceptance criteria of the restoration spec, which a basis may cite: read from its requirements, so a
 * criterion that does not exist cannot be cited, and two numbers cannot be read together as a date or a clock time.
 */
// Generated by scripts/restoration_criteria.py from the restoration spec's requirements; do not edit by hand
export const RESTORATION_CRITERIA = [
  '1.1', '1.2', '1.3', '1.4', '1.5', '1.6', '1.7',
  '2.1', '2.2', '2.3', '2.4', '2.5', '2.6', '2.7',
  '3.1', '3.2', '3.3', '3.4', '3.5', '3.6', '3.7',
  '4.1', '4.2', '4.3', '4.4', '4.5', '4.6', '4.7', '4.8',
  '5.1', '5.2', '5.3', '5.4',
  '6.1', '6.2', '6.3', '6.4', '6.5', '6.6', '6.7', '6.8', '6.9', '6.10', '6.11', '6.12', '6.13',
  '7.1', '7.2', '7.3', '7.4', '7.5', '7.6', '7.7', '7.8', '7.9', '7.10',
  '8.1', '8.2', '8.3', '8.4', '8.5', '8.6', '8.7', '8.8', '8.9', '8.10',
  '9.1', '9.2', '9.3', '9.4', '9.5', '9.6', '9.7',
  '10.1', '10.2', '10.3', '10.4',
  '11.1', '11.2', '11.3',
  '12.1', '12.2', '12.3', '12.4',
] as const;
// End of the generated list
/** A template's slots: a text slot holds one of its values, a number slot a whole number in its range. */
export const TEMPLATE_SLOTS = {
  format: { type: 'text', values: FAILURE_FORMATS },
  capability: { type: 'text', values: FAILURE_CAPABILITIES },
  section: { type: 'integer', unit: 'section of the restoration design', min: 1, max: 18 },
  criterion: { type: 'text', values: RESTORATION_CRITERIA },
} as const;
type SlotName = keyof typeof TEMPLATE_SLOTS;
/** The only text a failed attempt stores (design section 11, and the runner's sentence for a stopped server). */
export const FAILURE_SENTENCES = [
  'The file could not be decoded.',
  'This kind of file is not supported yet: {format}.',
  'This ffmpeg cannot do what this file needs: {capability}.',
  'A display copy is not made for a clip.',
  'The frame times could not be kept.',
  'An audio stream could not be copied unchanged.',
  'The original changed while the job ran.',
  'The restoration stopped at its time limit.',
  'The restoration stopped on an internal error.',
  'The server stopped before this job finished.',
] as const;
/** The plain sentences a record's notes may hold [proposed, each from the requirement named in design section 14]. */
export const NOTES = [
  'The noise could not be measured, so no noise reduction ran and the lift is held by the quantisation bound alone.',
  'The clip was recorded almost at black: there was no noise to measure, and the lift is held by the quantisation bound.',
  'The requested exposure was beyond a measured bound, so the lift stops at that bound.',
  'Registration did not hold for every frame; those frames have no temporal step.',
  'Points the original does not confirm were dissolved before the gate.',
  'Clipped regions were left out of the gate.',
  'Streams that are neither video nor audio were not copied.',
  'The lift was held by its maximum.',
] as const;
/** Why a colour field was assumed: one sentence per field and assumed value (design section 1). */
export const ASSUMPTION_REASONS = [
  { field: 'range', assumed: 'limited', reason: "The file does not state its range, and Y'CbCr video is taken as limited range." },
  { field: 'range', assumed: 'full', reason: 'The file does not state its range, and a JPEG or HEIC still is taken as full range.' },
  { field: 'matrix', assumed: 'bt709', reason: 'The file does not state its matrix, and a frame 720 lines or taller is taken as BT.709.' },
  {
    field: 'matrix',
    assumed: 'bt601',
    reason: 'The file does not state its matrix, and a frame under 720 lines or a JPEG or HEIC still is taken as BT.601.',
  },
  { field: 'transfer', assumed: 'srgb', reason: 'The file has no ICC profile and no EXIF ColorSpace, so sRGB is assumed.' },
  { field: 'transfer', assumed: 'bt709', reason: 'The clip does not state its transfer, so BT.709 is assumed.' },
  { field: 'primaries', assumed: 'unspecified', reason: 'The file does not state its primaries, so they stay unspecified and are never converted.' },
  {
    field: 'chromaSiting',
    assumed: 'left',
    reason: 'The file does not state its chroma siting, so left is assumed, the common default of H.264 and HEVC.',
  },
] as const;
/** What a parameter's basis may say, by its origin: what on this file it was measured from, the request field, or a source. */
export const BASES = {
  measured: [
    "Read from the file's own colour tags.",
    "Read from the file's ICC profile.",
    "Read from the file's EXIF ColorSpace.",
    "Read from the stream's own pixel format.",
    'Read from the decoded original.',
    "Measured on this file's noise table.",
    'Solved for this file from its median and its measured bounds.',
    "Counted from the asset record's audio streams.",
    "Measured on this file's confirmed stars.",
    "Measured on this clip's registered frames.",
  ],
  requested: ['exposureEv'],
  default: [
    'Proposed in design section {section}.',
    'Decided by the lead in design section {section}.',
    'Verified at its source in design section {section}.',
    'Required by REQ-RST-{criterion}.',
    "The detector's own floor.",
  ],
} as const satisfies Record<ParameterOrigin, readonly string[]>;
/** Every closed list both sides hold, compared through the fixture. */
export const MEDIA_VOCABULARY = {
  failureSentences: FAILURE_SENTENCES,
  notes: NOTES,
  assumptionReasons: ASSUMPTION_REASONS,
  bases: BASES,
  slots: TEMPLATE_SLOTS,
  stepParameters: STEP_PARAMETERS,
} as const;

/**
 * Whether `text` fills `template` (slots written `{name}`): literal parts as they are, a text slot with one of its
 * values, a number slot with a whole number in its range written without a leading zero. The Python mirror walks the
 * template the same way.
 */
export function fillsTemplate(text: string, template: string): boolean {
  const parts = template.split(/\{([a-z]+)\}/);
  const match = (position: number, index: number): boolean => {
    if (index === parts.length) return position === text.length;
    const part = parts[index]!;
    if (index % 2 === 0) return text.startsWith(part, position) && match(position + part.length, index + 1);
    const slot = TEMPLATE_SLOTS[part as SlotName];
    if (slot.type === 'integer') {
      const digits = /^(?:0|[1-9][0-9]*)/.exec(text.slice(position))?.[0];
      if (digits === undefined) return false;
      const value = Number(digits);
      return value >= slot.min && value <= slot.max && match(position + digits.length, index + 1);
    }
    return slot.values.some((value) => text.startsWith(value, position) && match(position + value.length, index + 1));
  };
  return match(0, 0);
}
export const fillsAnyTemplate = (text: string, templates: readonly string[]): boolean => templates.some((template) => fillsTemplate(text, template));

/**
 * Whether `value` is of the parameter row's type and inside its range or its values. An infinity never gets here (the
 * union's number refuses it).
 */
export function parameterValueFits(parameter: StepParameter, value: unknown): boolean {
  if (parameter.type === 'text') return typeof value === 'string' && parameter.values.includes(value);
  if (typeof value !== 'number') return false;
  if (parameter.type === 'integer' && !Number.isSafeInteger(value)) return false;
  return value >= parameter.min && value <= parameter.max;
}
export const basisFits = (origin: ParameterOrigin, basis: string): boolean =>
  origin === 'default' ? fillsAnyTemplate(basis, BASES.default) : (BASES[origin] as readonly string[]).includes(basis);

/** Every refinement's message, word for word the Python mirror's. */
const M = {
  calendar: WIRE_MESSAGES.calendar,
  requestOption: 'a display copy takes no option',
  parameterValue: "a parameter's value has its row's type and range",
  basis: "a parameter's basis is one its origin allows",
  parameterOnce: 'a step names each parameter once',
  parameterStep: "a step holds only its kind's parameters",
  graphKind: 'the graph is of the kind its request asks for',
  firstDecode: 'the first step is decode',
  displayCopySteps: 'a display copy is decode then encode, nothing more',
  pipelineOrder: 'steps run once each, in pipeline order',
  liftAndEncode: 'a restoration holds a lift and an encode',
  rgb: "R'G'B' has no matrix and no chroma subsampling",
  ycbcr: "Y'CbCr names its matrix",
  siting: 'chroma siting is given exactly when chroma is subsampled',
  bitDepth: "R'G'B' has 8 or 16 bits and Y'CbCr 8 or 10",
  profile: "a description read from an ICC profile names the profile's hash",
  assumedOnce: 'each field is assumed once',
  assumedValue: 'an assumed value is the value the description holds',
  assumptionReason: "an assumption's reason is the one its field and value have",
  noiseColumns: 'every column of the noise table has one value per intensity',
  noiseIncreasing: 'noise table intensities strictly increase',
  gatePassed: 'the gate passes exactly when no point is unconfirmed and no star changed',
  gatePoints: 'unconfirmed points are points of the variant',
  gateFrames: 'frames with unconfirmed points are frames checked',
  gateAnyFrame: 'a frame is counted exactly when an unconfirmed point is',
  gateFramePoints: 'each frame counted holds at least one unconfirmed point',
  gateStars: 'stars outside the tolerance are stars measured',
  gateFluxGiven: 'the flux change is given exactly when a star was measured',
  gateWorst: 'a star is outside the tolerance exactly when the worst change exceeds it',
  starsConfirmed: 'confirmed stars are stars found',
  noLift: 'no lift is a gain of one',
  clipNeighbours: 'frames with neighbours are frames of the clip',
  framesTogether: 'points confirmed on frames together need frames with neighbours',
  note: 'a note is one of the fixed notes',
  measuredNoise: 'the noise is measured or the reason it is not, never both',
  failureSentence: 'a failure reason is one of the fixed sentences',
  readyPassed: 'a ready attempt passed its gate',
  refusedIsRejected: 'an attempt the gate refused is rejected, never failed',
  gateRejection: 'a rejection by the gate carries the gate that refused',
  rejectionNamed: 'the rejection names what the gate found',
  noGateBefore: 'a rejection made before or without the gate carries no gate result',
  gateStarsFound: 'a gate measures no more stars than were found',
  attemptTimes: 'an attempt finishes after it starts',
  disclosure: 'the disclosure is the fixed sentence of its kind',
  sourceAsset: "the asset id is the source's own",
  still: 'a still is one frame with no duration, audio or frame times',
  clipTimes: 'a clip names the hash of its frame times',
  displayCopyStill: 'a display copy is a PNG of a still',
  variantUri: 'a variant is served at its own route',
  variantColour: "a PNG holds R'G'B' and a clip Y'CbCr",
  manifestSource: 'the parts are of one source',
  manifestIds: 'the variant, its graph and its attempt name each other',
  manifestReady: 'only a ready attempt is exported',
  manifestDisclosure: "the manifest carries the variant's disclosure",
  manifestGate: 'a restoration carries its gate result and a display copy none',
  manifestNoise: 'a restoration records its noise table or why it has none',
  manifestFrames: "the measured clip has the variant's frame count",
  stillGate: "a still's gate checks one frame",
  solution: "the solution is the source's own",
} as const;
const attemptFit = (status: string) => `fields do not fit a ${status} attempt`;
export const MEDIA_REFINEMENT_MESSAGES: readonly string[] = [...Object.values(M), ...ATTEMPT_STATUSES.map(attemptFit)];

/** Where a stored variant is served (REQ-RST-8.6): its own route under its original, never a store path. */
export function variantUri(sourceSha256: string, variantId: string): string {
  return `/api/media/${sourceSha256}/variants/${variantId}/file`;
}

const count = () => wireInt().nonnegative();
const positiveInt = () => wireInt().positive();
const sha256 = () => wireSha256();
const pattern = (source: string) => z.string().regex(new RegExp(source));
const mediaId = (kind: keyof typeof MEDIA_ID_PATTERNS) => pattern(MEDIA_ID_PATTERNS[kind]);
const semver = () => pattern(MEDIA_PATTERNS.semver);
const utcInstant = () => wireUtcInstant();
const disclosure = () => z.enum([DISCLOSURES.display_copy, DISCLOSURES.restoration]);

type Issue = (path: (string | number)[], message: string) => void;
const issuer =
  (ctx: z.RefinementCtx): Issue =>
  (path, message) =>
    ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });

export const RestoreRequestSchema = z
  .object({ kind: z.enum(VARIANT_KINDS), exposureEv: wireNumber().min(0).max(EXPOSURE_EV_MAX).optional() })
  .strict()
  .refine((request) => request.kind === 'restoration' || request.exposureEv === undefined, {
    message: M.requestOption,
    path: ['exposureEv'],
  });
export type RestoreRequest = z.infer<typeof RestoreRequestSchema>;

export const GraphParameterSchema = z
  .object({
    name: z.enum(PARAMETER_NAMES),
    value: z.union([wireNumber(), z.string(), z.boolean()]),
    origin: z.enum(PARAMETER_ORIGINS),
    // What on this file it was measured from, the request field, or the default's source
    basis: z.string(),
  })
  .strict()
  .superRefine((parameter, ctx) => {
    const issue = issuer(ctx);
    if (!parameterValueFits(PARAMETER_BY_NAME.get(parameter.name)!, parameter.value)) issue(['value'], M.parameterValue);
    if (!basisFits(parameter.origin, parameter.basis)) issue(['basis'], M.basis);
  });
export type GraphParameter = z.infer<typeof GraphParameterSchema>;

export const GraphStepSchema = z
  .object({
    kind: z.enum(GRAPH_STEP_KINDS),
    version: semver(),
    parameters: z.array(GraphParameterSchema).max(64),
  })
  .strict()
  .superRefine((step, ctx) => {
    const issue = issuer(ctx);
    if (new Set(step.parameters.map((parameter) => parameter.name)).size !== step.parameters.length) issue(['parameters'], M.parameterOnce);
    if (step.parameters.some((parameter) => !PARAMETER_BY_NAME.get(parameter.name)!.steps.includes(step.kind))) {
      issue(['parameters'], M.parameterStep);
    }
  });
export type GraphStep = z.infer<typeof GraphStepSchema>;

/** The ordered steps that ran on one original for one request, every parameter with where it came from. */
export const ProcessingGraphSchema = z
  .object({
    id: mediaId('graph'),
    sourceSha256: sha256(),
    kind: z.enum(VARIANT_KINDS),
    pipelineVersion: semver(),
    request: RestoreRequestSchema,
    steps: z.array(GraphStepSchema).min(2).max(16),
  })
  .strict()
  .superRefine((graph, ctx) => {
    const issue = issuer(ctx);
    const kinds = graph.steps.map((step) => step.kind);
    if (graph.kind !== graph.request.kind) issue(['request', 'kind'], M.graphKind);
    if (kinds[0] !== 'decode') issue(['steps', 0, 'kind'], M.firstDecode);
    if (graph.kind === 'display_copy') {
      if (kinds.length !== 2 || kinds[1] !== 'encode') issue(['steps'], M.displayCopySteps);
    } else {
      const order = kinds.map((kind) => GRAPH_STEP_KINDS.indexOf(kind));
      if (order.some((index, i) => i > 0 && index <= order[i - 1]!)) issue(['steps'], M.pipelineOrder);
      if (!kinds.includes('lift') || !kinds.includes('encode')) issue(['steps'], M.liftAndEncode);
    }
  });
export type ProcessingGraph = z.infer<typeof ProcessingGraphSchema>;

export const ColourAssumptionSchema = z
  .object({
    field: z.enum(ASSUMABLE_COLOUR_FIELDS),
    assumed: z.enum(ASSUMABLE_COLOUR_VALUES),
    reason: z.string(),
  })
  .strict()
  .refine(
    (assumption) =>
      ASSUMPTION_REASONS.some(
        (entry) => entry.field === assumption.field && entry.assumed === assumption.assumed && entry.reason === assumption.reason
      ),
    { message: M.assumptionReason, path: ['reason'] }
  );

export const ColourDescriptionSchema = z
  .object({
    model: z.enum(COLOUR_MODELS),
    bitDepth: z.union([z.literal(8), z.literal(10), z.literal(16)]),
    range: z.enum(COLOUR_RANGES),
    matrix: z.enum(COLOUR_MATRICES),
    transfer: z.enum(COLOUR_TRANSFERS),
    primaries: z.enum(COLOUR_PRIMARIES),
    chromaSubsampling: z.enum(CHROMA_SUBSAMPLINGS),
    chromaSiting: z.enum(CHROMA_SITINGS).optional(),
    iccProfileSha256: sha256().optional(),
    assumptions: z.array(ColourAssumptionSchema).max(5),
  })
  .strict()
  .superRefine((colour, ctx) => {
    const issue = issuer(ctx);
    if (colour.model === 'rgb' && (colour.matrix !== 'identity' || colour.chromaSubsampling !== '444')) issue(['matrix'], M.rgb);
    if (colour.model === 'ycbcr' && colour.matrix === 'identity') issue(['matrix'], M.ycbcr);
    if ((colour.chromaSiting !== undefined) !== (colour.chromaSubsampling !== '444')) issue(['chromaSiting'], M.siting);
    // REQ-RST-3.3: R'G'B' stills at 8 and 16 bits, Y'CbCr at 8 and 10
    if (!(colour.model === 'rgb' ? [8, 16] : [8, 10]).includes(colour.bitDepth)) issue(['bitDepth'], M.bitDepth);
    const fromProfile = colour.transfer === 'icc_parametric' || colour.transfer === 'icc_table' || colour.primaries === 'icc';
    if (fromProfile && colour.iccProfileSha256 === undefined) issue(['iccProfileSha256'], M.profile);
    const fields = colour.assumptions.map((assumption) => assumption.field);
    if (new Set(fields).size !== fields.length) issue(['assumptions'], M.assumedOnce);
    if (colour.assumptions.some((assumption) => colour[assumption.field] !== assumption.assumed)) issue(['assumptions'], M.assumedValue);
  });
export type ColourDescription = z.infer<typeof ColourDescriptionSchema>;

export const NoiseTableSchema = z
  .object({
    // Linear light, increasing
    intensities: z.array(wireNumber().min(0).max(1)).min(3).max(24),
    sigma: z
      .object({ r: z.array(wireNumber().nonnegative()), g: z.array(wireNumber().nonnegative()), b: z.array(wireNumber().nonnegative()) })
      .strict(),
    blockCounts: z.array(positiveInt()),
  })
  .strict()
  .superRefine((table, ctx) => {
    const issue = issuer(ctx);
    const bins = table.intensities.length;
    const columns = [table.sigma.r, table.sigma.g, table.sigma.b, table.blockCounts];
    if (columns.some((column) => column.length !== bins)) issue(['sigma'], M.noiseColumns);
    if (table.intensities.some((value, i) => i > 0 && value <= table.intensities[i - 1]!)) issue(['intensities'], M.noiseIncreasing);
  });
export type NoiseTable = z.infer<typeof NoiseTableSchema>;

export const IntegrityGateResultSchema = z
  .object({
    statistic: z.enum(GATE_STATISTICS),
    variantThresholdSnr: wireNumber().positive(),
    confirmThresholdSnr: wireNumber().positive(),
    tilePx: positiveInt(),
    matchRadiusPx: wireNumber().positive(),
    framesChecked: positiveInt(),
    variantPointCount: count(),
    unconfirmedPointCount: count(),
    framesWithUnconfirmed: count(),
    clippedPointsLeftOut: count(),
    starsMeasured: count(),
    starsOutsideFluxTolerance: count(),
    fluxToleranceSigma: wireNumber().positive(),
    // Absent when no star was measured
    worstFluxChangeSigma: wireNumber().nonnegative().optional(),
    medianFluxRatio: wireNumber().positive().optional(),
    passed: z.boolean(),
  })
  .strict()
  .superRefine((gate, ctx) => {
    const issue = issuer(ctx);
    const unconfirmed = gate.unconfirmedPointCount;
    const frames = gate.framesWithUnconfirmed;
    const outside = gate.starsOutsideFluxTolerance;
    if (gate.passed !== (unconfirmed === 0 && outside === 0)) issue(['passed'], M.gatePassed);
    if (unconfirmed > gate.variantPointCount) issue(['unconfirmedPointCount'], M.gatePoints);
    if (frames > gate.framesChecked) issue(['framesWithUnconfirmed'], M.gateFrames);
    if ((frames === 0) !== (unconfirmed === 0)) issue(['framesWithUnconfirmed'], M.gateAnyFrame);
    if (frames > unconfirmed) issue(['framesWithUnconfirmed'], M.gateFramePoints);
    if (outside > gate.starsMeasured) issue(['starsOutsideFluxTolerance'], M.gateStars);
    const measured = gate.starsMeasured > 0;
    if ((gate.worstFluxChangeSigma !== undefined) !== measured || (gate.medianFluxRatio !== undefined) !== measured) {
      issue(['worstFluxChangeSigma'], M.gateFluxGiven);
    }
    const worst = gate.worstFluxChangeSigma;
    if (worst !== undefined && outside > 0 !== worst > gate.fluxToleranceSigma) issue(['worstFluxChangeSigma'], M.gateWorst);
  });
export type IntegrityGateResult = z.infer<typeof IntegrityGateResultSchema>;

export const RestorationMeasurementsSchema = z
  .object({
    colour: ColourDescriptionSchema,
    noise: NoiseTableSchema.optional(),
    noiseNotMeasured: z.enum(NOISE_NOT_MEASURED_REASONS).optional(),
    // The fine noise after over before, in the darkest quarter
    fineNoiseLeft: wireNumber().nonnegative().optional(),
    stars: z
      .object({ found: count(), confirmed: count() })
      .strict()
      .refine((stars) => stars.confirmed <= stars.found, { message: M.starsConfirmed, path: ['confirmed'] })
      .optional(),
    dissolvedPerPass: z.array(count()).max(3).optional(),
    lift: z
      .object({ gainAtBlack: wireNumber().min(1), decidedBy: z.enum(LIFT_DECIDED_BY) })
      .strict()
      .refine((lift) => lift.decidedBy !== 'none' || lift.gainAtBlack === 1, { message: M.noLift, path: ['gainAtBlack'] })
      .optional(),
    clip: z
      .object({
        frameCount: positiveInt(),
        framesWithNeighbours: count(),
        medianClosureErrorPx: wireNumber().nonnegative().optional(),
        exactRepeatShare: wireNumber().min(0).max(1),
        // Confirmed by no single frame alone
        pointsConfirmedOnFramesTogether: count(),
        // Refused: on the same sensor pixel while the scene moved
        pointsFixedToSensor: count(),
        streamsNotCopied: count(),
      })
      .strict()
      .superRefine((clip, ctx) => {
        const issue = issuer(ctx);
        if (clip.framesWithNeighbours > clip.frameCount) issue(['framesWithNeighbours'], M.clipNeighbours);
        // REQ-RST-6.10: frames together are a frame and its registered neighbours
        if (clip.pointsConfirmedOnFramesTogether > 0 && clip.framesWithNeighbours === 0) issue(['pointsConfirmedOnFramesTogether'], M.framesTogether);
      })
      .optional(),
    peakMemoryBytes: positiveInt().optional(),
    notes: z.array(z.string().refine((note) => (NOTES as readonly string[]).includes(note), { message: M.note })).max(8),
  })
  .strict()
  .refine((measured) => measured.noise === undefined || measured.noiseNotMeasured === undefined, {
    message: M.measuredNoise,
    path: ['noiseNotMeasured'],
  });
export type RestorationMeasurements = z.infer<typeof RestorationMeasurementsSchema>;

/** One run of the job on one original and one request, stored whether it ends ready, rejected or failed. */
export const ProcessingAttemptSchema = z
  .object({
    id: mediaId('attempt'),
    sourceSha256: sha256(),
    graphId: mediaId('graph'),
    jobId: pattern(JOB_ID_PATTERN),
    status: z.enum(ATTEMPT_STATUSES),
    startedAtUTC: utcInstant(),
    finishedAtUTC: utcInstant(),
    elapsedMs: wireNumber().nonnegative(),
    cpuMs: wireNumber().nonnegative(),
    variantId: mediaId('variant').optional(),
    measured: RestorationMeasurementsSchema.optional(),
    gate: IntegrityGateResultSchema.optional(),
    rejection: z.enum(REJECTION_REASONS).optional(),
    failureReason: z.string().refine((reason) => fillsAnyTemplate(reason, FAILURE_SENTENCES), { message: M.failureSentence }).optional(),
  })
  .strict()
  .superRefine((attempt, ctx) => {
    const issue = issuer(ctx);
    const variant = attempt.variantId !== undefined;
    const measured = attempt.measured !== undefined;
    const rejection = attempt.rejection !== undefined;
    const failure = attempt.failureReason !== undefined;
    const fits = {
      ready: variant && measured && !rejection && !failure,
      rejected: rejection && measured && !variant && !failure,
      failed: failure && !variant && !rejection,
    }[attempt.status];
    if (!fits) issue(['status'], attemptFit(attempt.status));
    const gate = attempt.gate;
    if (attempt.status === 'ready' && gate !== undefined && !gate.passed) issue(['gate', 'passed'], M.readyPassed);
    // REQ-RST-6.5, 6.6: what the gate refused is rejected; failed is for a job that could not finish
    if (attempt.status === 'failed' && gate !== undefined && !gate.passed) issue(['status'], M.refusedIsRejected);
    if (attempt.rejection === 'unconfirmed_points' || attempt.rejection === 'star_flux_changed') {
      if (gate === undefined || gate.passed) {
        issue(['gate'], M.gateRejection);
      } else if (
        (attempt.rejection === 'unconfirmed_points' && gate.unconfirmedPointCount === 0) ||
        (attempt.rejection === 'star_flux_changed' && gate.starsOutsideFluxTolerance === 0)
      ) {
        issue(['rejection'], M.rejectionNamed);
      }
    } else if (attempt.rejection !== undefined && gate !== undefined) {
      issue(['gate'], M.noGateBefore);
    }
    const stars = attempt.measured?.stars;
    if (gate !== undefined && stars !== undefined && gate.starsMeasured > stars.found) issue(['gate', 'starsMeasured'], M.gateStarsFound);
    if (Date.parse(attempt.finishedAtUTC) < Date.parse(attempt.startedAtUTC)) issue(['finishedAtUTC'], M.attemptTimes);
  });
export type ProcessingAttempt = z.infer<typeof ProcessingAttemptSchema>;

/** An audio stream of a variant: the ingest shape, with the codec as ffprobe names it and the stream's full packet hash. */
/** Strict, as every media record is: an unknown key or a Python field name is refused, not dropped. */
export const VariantAudioStreamSchema = AudioStreamDescriptorSchema.extend({
  codec: pattern(MEDIA_PATTERNS.codecName),
  sha256: sha256(),
}).strict();

/** A file made from one original and stored beside it: a display copy or a restoration. */
export const MediaVariantSchema = z
  .object({
    id: mediaId('variant'),
    kind: z.enum(VARIANT_KINDS),
    sourceSha256: sha256(),
    sourceAssetId: mediaId('sourceAsset'),
    graphId: mediaId('graph'),
    attemptId: mediaId('attempt'),
    sha256: sha256(),
    uri: pattern(MEDIA_PATTERNS.variantUri),
    byteSize: positiveInt(),
    mimeType: z.enum(VARIANT_MIME_TYPES),
    width: positiveInt(),
    height: positiveInt(),
    durationMs: wireNumber().nonnegative(),
    frameCount: positiveInt(),
    // Of the time base and the integer frame times; a clip has it, a still not
    frameTimesSha256: sha256().optional(),
    audioStreams: z.array(VariantAudioStreamSchema),
    colour: ColourDescriptionSchema,
    displayOrientation: DisplayOrientationSchema.optional(),
    disclosure: disclosure(),
    createdAtUTC: utcInstant(),
  })
  .strict()
  .superRefine((variant, ctx) => {
    const issue = issuer(ctx);
    const still = variant.mimeType === 'image/png';
    if (variant.disclosure !== DISCLOSURES[variant.kind]) issue(['disclosure'], M.disclosure);
    if (variant.sourceAssetId !== `src_${variant.sourceSha256.slice(0, 16)}`) issue(['sourceAssetId'], M.sourceAsset);
    if (
      still &&
      (variant.frameCount !== 1 || variant.durationMs !== 0 || variant.audioStreams.length > 0 || variant.frameTimesSha256 !== undefined)
    ) {
      issue(['frameCount'], M.still);
    }
    if (!still && variant.frameTimesSha256 === undefined) issue(['frameTimesSha256'], M.clipTimes);
    if (variant.kind === 'display_copy' && !still) issue(['mimeType'], M.displayCopyStill);
    if (variant.uri !== variantUri(variant.sourceSha256, variant.id)) issue(['uri'], M.variantUri);
    if (variant.colour.model !== (still ? 'rgb' : 'ycbcr')) issue(['colour', 'model'], M.variantColour);
  });
export type MediaVariant = z.infer<typeof MediaVariantSchema>;

/** The sidecar of an export: how the exported file was made, from the three stored records. */
export const ExportManifestSchema = z
  .object({
    format: z.literal(EXPORT_MANIFEST_FORMAT),
    formatVersion: z.literal(EXPORT_MANIFEST_FORMAT_VERSION),
    sourceSha256: sha256(),
    variant: MediaVariantSchema,
    graph: ProcessingGraphSchema,
    attempt: ProcessingAttemptSchema,
    observationSolutionId: mediaId('solution').optional(),
    disclosure: disclosure(),
  })
  .strict()
  .superRefine((manifest, ctx) => {
    const issue = issuer(ctx);
    const { variant, graph, attempt } = manifest;
    const source = manifest.sourceSha256;
    if (variant.sourceSha256 !== source || graph.sourceSha256 !== source || attempt.sourceSha256 !== source) {
      issue(['sourceSha256'], M.manifestSource);
    }
    if (
      variant.graphId !== graph.id ||
      attempt.graphId !== graph.id ||
      variant.attemptId !== attempt.id ||
      attempt.variantId !== variant.id ||
      variant.kind !== graph.kind
    ) {
      issue(['variant'], M.manifestIds);
    }
    if (attempt.status !== 'ready') issue(['attempt', 'status'], M.manifestReady);
    if (manifest.disclosure !== variant.disclosure) issue(['disclosure'], M.manifestDisclosure);
    if ((attempt.gate !== undefined) !== (graph.kind === 'restoration')) issue(['attempt', 'gate'], M.manifestGate);
    const measured = attempt.measured;
    if (graph.kind === 'restoration' && measured !== undefined && (measured.noise === undefined) === (measured.noiseNotMeasured === undefined)) {
      issue(['attempt', 'measured'], M.manifestNoise);
    }
    // REQ-RST-7.5: the variant has the original's frames, which the clip's measurements counted
    if (measured?.clip !== undefined && measured.clip.frameCount !== variant.frameCount) issue(['attempt', 'measured', 'clip'], M.manifestFrames);
    if (variant.mimeType === 'image/png' && attempt.gate !== undefined && attempt.gate.framesChecked !== 1) {
      issue(['attempt', 'gate', 'framesChecked'], M.stillGate);
    }
    // REQ-JOB-5.8: the solution of this upload is sol_ and its hash's first 16 hex characters
    if (manifest.observationSolutionId !== undefined && manifest.observationSolutionId !== `sol_${source.slice(0, 16)}`) {
      issue(['observationSolutionId'], M.solution);
    }
  });
export type ExportManifest = z.infer<typeof ExportManifestSchema>;

/** What a session remembers of a download; the manifest is the record of how the file was made. */
export const ExportArtifactSchema = z
  .object({
    variantId: mediaId('variant'),
    variantSha256: sha256(),
    exportedAtUTC: utcInstant(),
  })
  .strict();
export type ExportArtifact = z.infer<typeof ExportArtifactSchema>;
