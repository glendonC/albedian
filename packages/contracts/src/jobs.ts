import { z } from 'zod';
import { FAILURE_SENTENCES, JOB_ID_PATTERN, MEDIA_ID_PATTERNS, RestoreRequestSchema, fillsAnyTemplate } from './media.js';
import { SHA256_HEX_PATTERN, wireInt, wireNumber, wireUtcInstant } from './wire.js';

/**
 * The upload job record: the TypeScript half of `services/contracts/jobs.py` (upload jobs spec, design 3.8,
 * platform-1).
 *
 * One record per attempt of a job of any kind: a solve of an upload, or a display copy or restoration of it. It holds
 * no free text: states, kinds, stages, units and pass labels are closed lists, and a failure is one of the fixed
 * sentences (the solve kind's below, the media kinds' those of `media.ts`). Every number is a safe integer or a finite
 * number with its range stated; every id has the pattern its minting code writes; every time is a UTC instant on a
 * real calendar day. The wire rules are those of `wire.ts`.
 *
 * `UploadJobSchema` stands beside the older `SolveJobSchema` of `ingest.ts`, which today's server writes and the web
 * reads, and replaces it once the runner writes every record (upload jobs tasks 4 and 5).
 */

export const JOB_KINDS = ['solve', 'display_copy', 'restoration'] as const;
export const MEDIA_JOB_KINDS = ['display_copy', 'restoration'] as const;
export const UPLOAD_JOB_STATES = ['queued', 'running', 'succeeded', 'rejected', 'failed', 'canceled', 'interrupted'] as const;
export const JOB_PROGRESS_UNITS = ['bytes', 'frames', 'candidates', 'steps'] as const;
type Unit = (typeof JOB_PROGRESS_UNITS)[number];
type Kind = (typeof JOB_KINDS)[number];
/** Each kind's stages in order, with the units each may report. */
export const JOB_STAGE_UNITS: Record<Kind, Record<string, readonly Unit[]>> = {
  solve: { store: ['bytes'], decode: ['frames'], detect: ['frames'], solve: ['candidates', 'steps'], verify: ['steps'] },
  display_copy: { decode: ['frames'], encode: ['frames'], write: ['bytes'], verify: ['frames'] },
  restoration: { decode: ['frames'], measure: ['frames'], restore: ['frames'], encode: ['frames'], write: ['bytes'], verify: ['frames'] },
};
export const UPLOAD_JOB_STAGES = [
  'store', 'decode', 'detect', 'solve', 'verify', 'encode', 'write', 'measure', 'restore',
] as const;
/** The solve chain's passes (design 3.4). */
export const JOB_PASS_LABELS = ['phone_photo_hinted', 'phone_photo_generic', 'triangle'] as const;
export const JOB_PASS_MAX = 16;
export const JOB_SPAN_MAX_SEC = 86_400;
export const PRODUCER_NAME_PATTERN = '^[a-z][a-z0-9_.]{0,79}$';
export const INTERRUPTED_SENTENCE = 'The server stopped before this job finished.';
/** The only text a failed solve stores. */
export const SOLVE_FAILURE_SENTENCES = [
  'The decoder could not read the file.',
  'ffmpeg is not installed to decode the upload.',
  'The decoder stopped at its time limit.',
  "This ffmpeg could not report the HEIC photo's tile grid (ffprobe 7.1 or later is needed), so the photo was not decoded.",
  "The decoder could not read the HEIC photo's tiles.",
  "The file's tile grid does not cover the stored image.",
  'The decoder did not write one frame for every tile.',
  'The solve stopped on an internal error.',
  'The original changed while the job ran.',
  INTERRUPTED_SENTENCE,
] as const;
export const JOB_PATTERNS = {
  jobId: JOB_ID_PATTERN,
  sha256: SHA256_HEX_PATTERN,
  producerName: PRODUCER_NAME_PATTERN,
  solutionId: MEDIA_ID_PATTERNS.solution,
  graphId: MEDIA_ID_PATTERNS.graph,
  attemptId: MEDIA_ID_PATTERNS.attempt,
  variantId: MEDIA_ID_PATTERNS.variant,
} as const;

/** Every refinement's message, word for word the Python mirror's. */
export const JOB_MESSAGES = {
  done: 'done cannot exceed total',
  span: 'a frame span ends no earlier than it starts',
  state: "the fields do not fit the job's kind and state",
  times: 'times run created, started, finished',
  requestKind: "the request is of the job's kind",
  stageKind: "a stage belongs to the job's kind",
  stageOrder: "stages appear once each, in the kind's order",
  stageUnit: 'a stage reports in one of its units',
  passKind: 'only a solve stage reports a pass',
  cancel: "a cancel time lies within the job's run",
  failure: 'a failure is one of the fixed sentences its kind and state allow',
  interrupted: 'an interrupted job says the server stopped',
} as const;
const M = JOB_MESSAGES;

/**
 * What each state may and must hold, for each group of kinds: the Python mirror's table, read from the requirements
 * (REQ-JOB-2.1 to 2.3 and 2.6, 3.2, 3.3, 3.7, 4.1 to 4.3, 4.5, 5.4; design 3.4 and 3.8; the reasons are written beside
 * the table in `services/contracts/jobs.py`). Each field is `required`, `forbidden`, `either`, `started` (allowed
 * only on a job that started) or `with_start` (required on a job that started, forbidden on one that did not). `workStages` is a stage other than those its kind writes at creation. `errors` names
 * the failure sentences the state allows. A group with no row for a state cannot be in it.
 */
export const JOB_PRE_RUN_STAGES: Record<Kind, readonly string[]> = { solve: ['store'], display_copy: [], restoration: [] };
export const JOB_STATE_FIELDS = [
  'startedAtUTC', 'finishedAtUTC', 'error', 'solutionId', 'attemptId', 'variantId', 'inputSpan',
  'cancelRequestedAtUTC', 'queueAhead', 'producer', 'request', 'graphId', 'workStages',
] as const;
type StateField = (typeof JOB_STATE_FIELDS)[number];
type Rule = 'required' | 'forbidden' | 'either' | 'started' | 'with_start';
type State = (typeof UPLOAD_JOB_STATES)[number];
export interface JobStateRow {
  fields: Record<StateField, Rule>;
  errors: 'none' | 'failure' | 'interrupted';
}
const RULES: Record<string, Rule> = { R: 'required', F: 'forbidden', E: 'either', S: 'started', A: 'with_start' };
/** One row of the state table from its letters, one per field of `JOB_STATE_FIELDS`; any other count throws. */
export const jobStateRow = (letters: string, errors: JobStateRow['errors']): JobStateRow => {
  const codes = letters.split(/\s+/);
  if (codes.length !== JOB_STATE_FIELDS.length) throw new Error(`a state row needs ${JOB_STATE_FIELDS.length} rules`);
  return { fields: Object.fromEntries(JOB_STATE_FIELDS.map((field, i) => [field, RULES[codes[i]!]!])) as JobStateRow['fields'], errors };
};
//                    start fin  err  sol  att  var  span cancel queue prod req graph work
export const JOB_STATE_TABLE: Record<'solve' | 'media', Record<State, JobStateRow | null>> = {
  solve: {
    queued:      jobStateRow('F    F    F    F    F    F    F    F      E     A    F   F     F', 'none'),
    running:     jobStateRow('R    F    F    F    F    F    E    S      F     A    F   F     E', 'none'),
    succeeded:   jobStateRow('R    R    F    R    F    F    E    S      F     A    F   F     E', 'none'),
    rejected:    null,
    failed:      jobStateRow('R    R    R    F    F    F    E    S      F     A    F   F     E', 'failure'),
    canceled:    jobStateRow('E    R    F    F    F    F    S    A      F     A    F   F     S', 'none'),
    interrupted: jobStateRow('R    R    R    F    F    F    E    S      F     A    F   F     E', 'interrupted'),
  },
  media: {
    queued:      jobStateRow('F    F    F    F    F    F    F    F      E     A    R   R     F', 'none'),
    running:     jobStateRow('R    F    F    F    F    F    F    S      F     A    R   R     E', 'none'),
    succeeded:   jobStateRow('R    R    F    F    R    R    F    S      F     A    R   R     E', 'none'),
    rejected:    jobStateRow('R    R    F    F    R    F    F    S      F     A    R   R     E', 'none'),
    failed:      jobStateRow('R    R    R    F    E    F    F    S      F     A    R   R     E', 'failure'),
    canceled:    jobStateRow('E    R    F    F    F    F    F    A      F     A    R   R     S', 'none'),
    interrupted: jobStateRow('R    R    R    F    F    F    F    S      F     A    R   R     E', 'interrupted'),
  },
};
export const jobGroup = (kind: Kind): 'solve' | 'media' => ((MEDIA_JOB_KINDS as readonly string[]).includes(kind) ? 'media' : 'solve');
export const stateAllows = (rule: Rule, present: boolean, started: boolean): boolean =>
  ({ required: present, forbidden: !present, either: true, started: started || !present, with_start: present === started })[rule];

const pattern = (source: string) => z.string().regex(new RegExp(source));
const safeCount = () => wireInt().min(0);
const safePositive = () => wireInt().min(1);
const spanSeconds = () => wireNumber().min(0).max(JOB_SPAN_MAX_SEC);

export const JobPassSchema = z
  .object({ number: wireInt().min(1).max(JOB_PASS_MAX), label: z.enum(JOB_PASS_LABELS) })
  .strict();

export const UploadJobStageSchema = z
  .object({
    stage: z.enum(UPLOAD_JOB_STAGES),
    unit: z.enum(JOB_PROGRESS_UNITS),
    done: safeCount(),
    total: safePositive().nullable(),
    pass: JobPassSchema.optional(),
  })
  .strict()
  .superRefine((stage, ctx) => {
    if (stage.total !== null && stage.done > stage.total) ctx.addIssue({ code: z.ZodIssueCode.custom, message: M.done });
  });

export const JobProducerSchema = z.object({ name: pattern(PRODUCER_NAME_PATTERN), revision: safePositive() }).strict();

export const JobInputSpanSchema = z
  .object({ firstFrameIndex: safeCount(), frameCount: safePositive(), startSec: spanSeconds(), endSec: spanSeconds() })
  .strict()
  .superRefine((span, ctx) => {
    if (span.endSec < span.startSec) ctx.addIssue({ code: z.ZodIssueCode.custom, message: M.span });
  });

const UploadJobShape = {
    id: pattern(JOB_ID_PATTERN),
    sourceAssetSha256: pattern(SHA256_HEX_PATTERN),
    kind: z.enum(JOB_KINDS),
    state: z.enum(UPLOAD_JOB_STATES),
    createdAtUTC: wireUtcInstant(),
    startedAtUTC: wireUtcInstant().optional(),
    finishedAtUTC: wireUtcInstant().optional(),
    stages: z.array(UploadJobStageSchema).max(UPLOAD_JOB_STAGES.length),
    solutionId: pattern(MEDIA_ID_PATTERNS.solution).optional(),
    error: z.string().optional(),
    producer: JobProducerSchema.optional(),
    request: RestoreRequestSchema.optional(),
    graphId: pattern(MEDIA_ID_PATTERNS.graph).optional(),
    attemptId: pattern(MEDIA_ID_PATTERNS.attempt).optional(),
    variantId: pattern(MEDIA_ID_PATTERNS.variant).optional(),
    inputSpan: JobInputSpanSchema.optional(),
    cancelRequestedAtUTC: wireUtcInstant().optional(),
    queueAhead: safeCount().optional(),
};

/** The optional fields of the record, which the state table names one by one. */
export const UPLOAD_JOB_OPTIONAL_FIELDS = Object.entries(UploadJobShape)
  .filter(([, schema]) => schema.isOptional())
  .map(([name]) => name);

export const UploadJobSchema = z
  .object(UploadJobShape)
  .strict()
  .superRefine((job, ctx) => {
    const fail = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
    const media = (MEDIA_JOB_KINDS as readonly string[]).includes(job.kind);
    const started = job.startedAtUTC !== undefined;
    const finished = job.finishedAtUTC !== undefined;
    const stateRow = JOB_STATE_TABLE[jobGroup(job.kind)][job.state];
    const present = Object.fromEntries(
      Object.entries(UploadJobShape).filter(([, schema]) => schema.isOptional())
        .map(([name]) => [name, (job as Record<string, unknown>)[name] !== undefined]),
    ) as Record<string, boolean>;
    present.workStages = job.stages.some((stage) => !JOB_PRE_RUN_STAGES[job.kind].includes(stage.stage));
    if (stateRow === null || !JOB_STATE_FIELDS.every((field) => stateAllows(stateRow.fields[field], present[field]!, started))) {
      fail(M.state);
    }
    const created = Date.parse(job.createdAtUTC);
    const start = started ? Date.parse(job.startedAtUTC!) : undefined;
    const end = finished ? Date.parse(job.finishedAtUTC!) : undefined;
    if ((start !== undefined && start < created) || (end !== undefined && end < (start ?? created))) fail(M.times);
    if (media && job.request !== undefined && job.request.kind !== job.kind) fail(M.requestKind);
    const order = Object.keys(JOB_STAGE_UNITS[job.kind]);
    if (job.stages.some((stage) => !order.includes(stage.stage))) {
      fail(M.stageKind);
    } else {
      const places = job.stages.map((stage) => order.indexOf(stage.stage));
      if (places.some((place, i) => i > 0 && place <= places[i - 1]!)) fail(M.stageOrder);
      if (job.stages.some((stage) => !JOB_STAGE_UNITS[job.kind][stage.stage]!.includes(stage.unit))) fail(M.stageUnit);
    }
    if (media && job.stages.some((stage) => stage.pass !== undefined)) fail(M.passKind);
    if (job.cancelRequestedAtUTC !== undefined && start !== undefined) {
      const cancel = Date.parse(job.cancelRequestedAtUTC);
      if (cancel < start || (end !== undefined && cancel > end)) fail(M.cancel);
    }
    const allowed = stateRow === null ? null : stateRow.errors;
    if (job.error !== undefined && allowed === 'interrupted' && job.error !== INTERRUPTED_SENTENCE) fail(M.interrupted);
    if (job.error !== undefined && allowed === 'failure') {
      const fixed = media
        ? fillsAnyTemplate(job.error, FAILURE_SENTENCES)
        : (SOLVE_FAILURE_SENTENCES as readonly string[]).includes(job.error);
      if (!fixed || job.error === INTERRUPTED_SENTENCE) fail(M.failure);
    }
  });
export type UploadJob = z.infer<typeof UploadJobSchema>;
