import {
  CalibrationMachineState,
  CelestialFinding,
  IngestReceipt,
  IngestReceiptSchema,
  ObservationSession,
  ObservationSessionSchema,
  ObservationSolution,
  ObservationSolutionSchema,
  SolutionStatus,
  SolveJob,
  SolveJobSchema,
  UploadJob,
  UploadJobSchema,
  DisplayOrientationSchema,
  MediaVariantSchema,
  type MediaVariant,
  type PersonAlignment,
  type DisplayOrientation,
} from '@albedian/contracts';
import { createSessionForSourceAsset } from '@albedian/observation';
import { raDecToUnitVector } from '@albedian/camera-rig';
import { graphemes } from '../text/graphemes.js';
import { skyLayerSentence, solveStoppedSentence, type SkyAssetFacts, type StarSearchFacts } from './skyLayerWords.js';

export interface MediaAssetUrls {
  origUrl: string;
  v1Url: string;
  v2Url: string;
  origVideoUrl?: string;
  v1VideoUrl?: string;
  v2VideoUrl?: string;
}

export interface DiscoveredMediaItem {
  id: string;
  title: string;
  filename: string;
  sha256: string;
  mediaKind: 'image' | 'video';
  width: number;
  height: number;
  /** A clip's duration as discovery probed it; 0 for a still. */
  durationSec: number;
  /** A clip's frame rate as discovery probed it; a still has none. */
  fps: number | null;
  videoCodec: string;
  capturedAtUTC: string | null;
  latDeg: number | null;
  lonDeg: number | null;
  cameraModel: string | null;
  urls: MediaAssetUrls;
  solution?: ObservationSession['observationSolution'];
}

interface RawBackendDiscoveredItem {
  id: string;
  filename: string;
  sha256: string;
  mediaType?: string;
  width: number;
  height: number;
  durationSec?: number;
  fps?: number;
  videoCodec?: string;
  capturedAtUTC?: string | null;
  cameraModel?: string | null;
  observerLocation?: {
    latDeg: number;
    lonDeg: number;
  } | null;
  variants?: Record<string, string>;
  observationSolution?: ObservationSession['observationSolution'];
}

function isMediaKind(value: unknown): value is DiscoveredMediaItem['mediaKind'] {
  return value === 'image' || value === 'video';
}

function isPositiveFinite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

/** A still has no frame rate, so its asset carries none; a clip's is the rate discovery probed. */
function frameRateOf(item: DiscoveredMediaItem): { fpsNumerator?: number; fpsDenominator?: number } {
  if (item.mediaKind === 'image') return {};
  if (!isPositiveFinite(item.fps)) {
    throw new Error(`A clip needs the frame rate discovery probed for it, and ${item.filename} has none.`);
  }
  return { fpsNumerator: Math.max(1, Math.round(item.fps * 1000)), fpsDenominator: 1000 };
}

function toCartesian(raDeg: number, decDeg: number, distPc: number): [number, number, number] {
  const u = raDecToUnitVector(raDeg, decDeg);
  return [u[0] * distPc, u[1] * distPc, u[2] * distPc];
}

/** Distances and sky positions are never assumed: findings without both keep no 3D position. */
function withDerivedCartesianCoords(findings: CelestialFinding[]): CelestialFinding[] {
  return findings.map((f) => ({
    ...f,
    cartesianParsecICRS:
      f.cartesianParsecICRS ??
      (f.inferredDistancePc && f.skyPosition
        ? toCartesian(f.skyPosition.raDeg, f.skyPosition.decDeg, f.inferredDistancePc.median)
        : undefined),
  }));
}

export function formatObserverCoords(latDeg: number, lonDeg: number, decimals = 2): string {
  const latStr = `${Math.abs(latDeg).toFixed(decimals)}°${latDeg >= 0 ? 'N' : 'S'}`;
  const lonStr = `${Math.abs(lonDeg).toFixed(decimals)}°${lonDeg >= 0 ? 'E' : 'W'}`;
  return `${latStr}, ${lonStr}`;
}

function formatReadableTitle(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, '').replace(/[-_]\d{4}$/, '');
  return stem
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * The calibration machine has no `approximate` state, so an unverified pose counts
 * as `predicted_only`: overlays from it are predictions, not registrations.
 */
function calibrationStateForSolution(status: SolutionStatus): CalibrationMachineState {
  switch (status) {
    case 'pending':
      return 'not_requested';
    case 'approximate':
      return 'predicted_only';
    default:
      return status;
  }
}

/**
 * A solved session as it opens. A clip opens paused with the session's clock on the frame its sky was solved on, so
 * the stage shows that frame (SourcePhotoStage seeks to the clock), the anchor Back to Moment returns to is that
 * frame, and the clip time gate of the markers reads it (P-SKY-9). An approximate or predicted_only solution turns
 * the predicted layer on (REQ-SKY-6.2, 6.5).
 */
function openedAtSolvedFrame(session: ObservationSession, solution: ObservationSolution): ObservationSession {
  const timeSec = session.sourceAsset?.mediaKind === 'video' ? solution.frameTimeSec : session.currentSourceTimeSec;
  const predictedOn = solution.status === 'approximate' || solution.status === 'predicted_only';
  const spatialView = predictedOn
    ? {
        ...session.spatialView,
        activeOverlays: { ...session.spatialView.activeOverlays, predictedEphemerides: true },
      }
    : session.spatialView;
  const history = session.navigationHistory;
  const anchorId = history.anchorMomentCheckpoint.id;
  const anchor = {
    ...history.anchorMomentCheckpoint,
    sourceTimeSec: timeSec,
    playbackWasPlaying: session.isPlaying,
    spatialView: structuredClone(spatialView),
  };
  return {
    ...session,
    currentSourceTimeSec: timeSec,
    spatialView,
    navigationHistory: {
      ...history,
      anchorMomentCheckpoint: anchor,
      checkpoints: history.checkpoints.map((checkpoint) => (checkpoint.id === anchorId ? anchor : checkpoint)),
    },
  };
}

/**
 * Query the Python FastAPI backend (`GET /api/media/discover`) for any media files
 * dynamically present in `data/uploads/`. Returns an empty list when the backend is unavailable.
 * The kind, and a clip's frame rate and duration, are taken as discovery probed them: an item
 * without them is left out, never given guessed ones. A still has neither.
 */
export async function fetchDiscoveredMedia(): Promise<DiscoveredMediaItem[]> {
  try {
    const res = await fetch('/api/media/discover');
    if (!res.ok) return [];
    const data = (await res.json()) as { items?: RawBackendDiscoveredItem[] };
    if (!Array.isArray(data.items)) return [];

    return data.items.flatMap((raw): DiscoveredMediaItem[] => {
      if (!isMediaKind(raw.mediaType)) return [];
      let durationSec = 0;
      let fps: number | null = null;
      if (raw.mediaType === 'video') {
        if (!isPositiveFinite(raw.fps) || !isNonNegativeFinite(raw.durationSec)) return [];
        durationSec = raw.durationSec;
        fps = raw.fps;
      }
      const v = raw.variants ?? {};
      const origUrl = v.orig_png ?? v.default_png ?? v.orig_jpg ?? '';
      const v1Url = v.v1_png ?? origUrl;
      const v2Url = v.v2_png ?? v1Url;
      return [{
        id: raw.id,
        title: formatReadableTitle(raw.filename),
        filename: raw.filename,
        sha256: raw.sha256,
        mediaKind: raw.mediaType,
        width: raw.width,
        height: raw.height,
        durationSec,
        fps,
        videoCodec: raw.videoCodec ?? 'unknown',
        capturedAtUTC: raw.capturedAtUTC ?? null,
        latDeg: raw.observerLocation?.latDeg ?? null,
        lonDeg: raw.observerLocation?.lonDeg ?? null,
        cameraModel: raw.cameraModel ?? null,
        urls: {
          origUrl,
          v1Url,
          v2Url,
          origVideoUrl: v.orig_mp4,
          v1VideoUrl: v.v1_mp4,
          v2VideoUrl: v.v2_mp4,
        },
        solution: raw.observationSolution,
      }];
    });
  } catch {
    return [];
  }
}

/**
 * Build an `ObservationSession` from a backend-discovered capture. Findings come only from
 * the backend's own solution for that file; without one the session stays `pending`.
 * Media stays `importing` because audio stream descriptors are not probed yet.
 */
export function buildSessionFromDiscoveredMedia(item: DiscoveredMediaItem): ObservationSession {
  // Discovery reads QuickTime tags, trying each pair in this order (services/media/discovery.py)
  const placed = item.latDeg !== null && item.lonDeg !== null;
  const session = createSessionForSourceAsset(
    {
      id: `src_${item.id}`,
      title: item.filename,
      mediaKind: item.mediaKind,
      sha256: item.sha256,
      uri: item.urls.origVideoUrl ?? item.urls.origUrl,
      width: item.width,
      height: item.height,
      durationMs: Math.round(item.durationSec * 1000),
      ...frameRateOf(item),
      videoCodec: item.videoCodec,
      audioStreams: [],
      capturedAtUTC: item.capturedAtUTC ?? undefined,
      observerLocation: placed ? { latDeg: item.latDeg!, lonDeg: item.lonDeg! } : undefined,
      cameraMetadata: item.cameraModel !== null ? { model: item.cameraModel } : undefined,
      metadataSources: {
        // Only the recorded time: discovery never takes the file's own creation_time as a capture moment
        capturedAtUTC: item.capturedAtUTC
          ? { container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] }
          : undefined,
        observerLocation: placed
          ? { container: 'quicktime', tags: ['com.apple.quicktime.location.ISO6709', 'location'] }
          : undefined,
        cameraMetadata:
          item.cameraModel !== null ? { container: 'quicktime', tags: ['com.apple.quicktime.model', 'model'] } : undefined,
      },
      privacyClass: 'private',
      immutable: true,
    },
    'importing'
  );
  const shaLabel = `SHA-256 ${item.sha256.slice(0, 12)}…`;

  if (!item.solution) {
    return {
      ...session,
      title: item.title,
      lastStatusBanner: `Loaded "${item.filename}" (${shaLabel}). No sky solution yet.`,
    };
  }

  const findings = withDerivedCartesianCoords(item.solution.findings);
  // No star search ran for a discovered clip: discovery runs only the Moon and horizon solver, which looks for one
  // bright body and a skyline and for no other point of light, and whose count says whether it found that body
  // [verified: services/media/discovery.py calls only solve_horizon_and_beacon_pose; services/science/
  // horizon_solver.py]. So its line makes no statement about points of light
  const search: StarSearchFacts = { starSearchRan: false };

  return openedAtSolvedFrame(
    {
      ...session,
      title: item.title,
      machines: { ...session.machines, calibration: calibrationStateForSolution(item.solution.status) },
      observationSolution: { ...item.solution, findings },
      lastStatusBanner: `Loaded "${item.filename}" (${shaLabel}). ${skyLayerSentence(item.solution, session.sourceAsset, search)}`,
    },
    item.solution
  );
}

/** A finished solve's sky, for its banner: the solution, and whether a star search ran for it (skyLayerWords.ts). */
export interface SolvedSky extends StarSearchFacts {
  solution: ObservationSolution;
}

/**
 * A solve job's record as the server answers it: today's `SolveJob`, or the runner's `UploadJob` of the solve kind
 * (upload jobs design 3.8), which adds the queue, the cancel time and the interrupted state.
 */
export type FollowedJob = SolveJob | UploadJob;

/** Reads a solve job's record of either form; a job of another kind, or a record of neither, is refused. */
export function parseFollowedJob(body: unknown): FollowedJob {
  if (body !== null && typeof body === 'object' && 'kind' in body) {
    const job = UploadJobSchema.parse(body);
    if (job.kind !== 'solve') throw new Error(`a ${job.kind} job is not a solve job`);
    return job;
  }
  return SolveJobSchema.parse(body);
}

/** How long the web waits between two reads of a job it follows (upload jobs design 3.9: once a second). */
export const JOB_POLL_MS = 1000;

/** Job states that end a job with no sky; each says why in the job's own words. */
const STOPPED_WITH_REASON: ReadonlySet<string> = new Set(['failed', 'interrupted']);

/**
 * Format a `SolveJob`'s stage progress using its real work denominators (`done/total unit`),
 * or elapsed state when `total` is `null`, without inventing percentages. Once the job ends, the
 * banner says in plain words which layer the sky is, or why there is none (skyLayerWords.ts),
 * from the solved sky its caller hands it and the recording's own facts. A failed job says the
 * solve stopped, whatever came with it.
 */
export function formatSolveJobProgressBanner(
  filename: string,
  job: FollowedJob,
  solved?: SolvedSky,
  asset?: SkyAssetFacts
): string {
  const parsedJob = parseFollowedJob(job);
  const stageParts = parsedJob.stages.map((entry) =>
    entry.total !== null
      ? `${entry.stage}: ${entry.done}/${entry.total} ${entry.unit}`
      : `${entry.stage}: ${entry.done} ${entry.unit} (elapsed)`
  );
  const stageSummary = stageParts.join(' · ');
  if (STOPPED_WITH_REASON.has(parsedJob.state)) {
    const stopped = solveStoppedSentence(asset).replace(/\.$/, '');
    const reason = (parsedJob.error ?? 'the server gave no reason').replace(/\.$/, '');
    return `Stored "${filename}" (${parsedJob.id}, ${stageSummary}). ${stopped}: ${reason}.`;
  }
  if (solved) {
    return `Loaded "${filename}" (${parsedJob.id}, ${stageSummary}). ${skyLayerSentence(solved.solution, asset, solved)}`;
  }
  return `Solving "${filename}" (${parsedJob.id}, ${parsedJob.state}): ${stageSummary}.`;
}

/**
 * Build a contract-validated `ObservationSession` from a backend `IngestReceipt` and optional
 * `SolveJob` and `ObservationSolution`.
 */
export function buildSessionFromIngestReceipt(
  receipt: IngestReceipt,
  options?: {
    localObjectUrl?: string;
    job?: FollowedJob;
    solution?: ObservationSolution;
  }
): ObservationSession {
  const parsedReceipt = IngestReceiptSchema.parse(receipt);
  const asset = {
    ...parsedReceipt.sourceAsset,
    uri: options?.localObjectUrl ?? parsedReceipt.sourceAsset.uri,
  };
  const baseSession = createSessionForSourceAsset(asset, 'ready');
  const parsedJob = options?.job ? parseFollowedJob(options.job) : undefined;

  if (parsedJob !== undefined && STOPPED_WITH_REASON.has(parsedJob.state)) {
    return ObservationSessionSchema.parse({
      ...baseSession,
      machines: { ...baseSession.machines, media: 'ready', calibration: 'unresolved' },
      observationSolution: {
        ...baseSession.observationSolution!,
        status: 'unresolved',
        warnings: [parsedJob.error ?? 'Solve job failed.'],
      },
      lastStatusBanner: formatSolveJobProgressBanner(asset.title, parsedJob, undefined, asset),
    });
  }

  if (!options?.solution) {
    const byteCount = asset.byteSize ?? 0;
    return ObservationSessionSchema.parse({
      ...baseSession,
      machines: { ...baseSession.machines, media: 'ready', calibration: 'solving' },
      lastStatusBanner: parsedJob
        ? formatSolveJobProgressBanner(asset.title, parsedJob)
        : `Stored "${asset.title}" (store: ${byteCount}/${byteCount} bytes). Starting sky calibration…`,
    });
  }

  const parsedSolution = ObservationSolutionSchema.parse(options.solution);
  const findings = withDerivedCartesianCoords(parsedSolution.findings);
  const shaLabel = `SHA-256 ${asset.sha256.slice(0, 12)}…`;
  // A star search ran for an upload's solution: its solve job runs the detector over the frame and the plate solver
  // over the points of light it finds before it writes any solution [verified: services/ingest/jobs.py
  // run_solve_job, detect_point_sources then the solve chain]
  const solved: SolvedSky = { solution: parsedSolution, starSearchRan: true };

  return ObservationSessionSchema.parse(
    openedAtSolvedFrame(
      {
        ...baseSession,
        machines: {
          ...baseSession.machines,
          media: 'ready',
          calibration: calibrationStateForSolution(parsedSolution.status),
        },
        observationSolution: { ...parsedSolution, findings },
        lastStatusBanner: parsedJob
          ? formatSolveJobProgressBanner(asset.title, parsedJob, solved, asset)
          : `Loaded "${asset.title}" (${shaLabel}). ${skyLayerSentence(parsedSolution, asset, solved)}`,
      },
      parsedSolution
    )
  );
}

/**
 * The server answered the upload with an error status. `POST /api/ingest` leaves nothing behind when it
 * refuses (services/ingest/router.py), so the file was not stored. `detail` is the server's own words, or null
 * when its answer held none; `reason` is the detail, else the HTTP status in words, for a status line only.
 */
export class UploadRefusedError extends Error {
  readonly status: number;
  readonly detail: string | null;
  readonly reason: string;

  constructor(status: number, detail: string | null) {
    const reason = detail ?? statusInWords(status);
    super(`Not stored: ${reason}`);
    this.name = 'UploadRefusedError';
    this.status = status;
    this.detail = detail;
    this.reason = reason;
  }
}

function statusInWords(status: number): string {
  return `the server answered HTTP ${status}`;
}

/**
 * The most of an answer's `detail` the client takes. Where the platform has a cluster splitter they are characters as
 * a person sees them (graphemes), so a cut never splits one; where it has none, the cut falls between code points
 * (text/graphemes.ts says what that can split). The store's own reasons are under 100.
 */
export const UPLOAD_DETAIL_MAX_CHARS = 240;

/**
 * A detail within the bound, or null when it holds no words: past the bound it is cut at the last space before it (at
 * the bound itself when it has no space) and ends with an ellipsis, so a person sees where it was cut.
 */
export function boundedDetail(detail: string): string | null {
  const text = detail.trim();
  if (text === '') return null;
  const chars = graphemes(text);
  if (chars.length <= UPLOAD_DETAIL_MAX_CHARS) return text;
  const head = chars.slice(0, UPLOAD_DETAIL_MAX_CHARS).join('');
  const space = head.lastIndexOf(' ');
  return `${space > 0 ? head.slice(0, space) : head}\u2026`;
}

/** The server's own words for an error answer: its `{"detail": "..."}` text within the bound, or null when it gave none. */
async function serverDetail(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.json();
    const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : undefined;
    if (typeof detail === 'string') return boundedDetail(detail);
  } catch {
    // Not JSON, so the server gave no words of its own
  }
  return null;
}

/** The server's reason for an error answer: its own words, else the HTTP status. */
async function serverReason(res: Response): Promise<string> {
  return (await serverDetail(res)) ?? statusInWords(res.status);
}

/** A failure in words a person can read; an answer that fails the contract is named, not dumped. */
export function failureReason(err: unknown): string {
  if (err instanceof Error && err.name === 'ZodError') return "the server's answer did not match the contract";
  if (err instanceof Error && err.message) return err.message;
  return String(err);
}

/** `fetch`, with a rejection (no HTTP answer at all) said as such. */
async function fetchAnswer(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    // The sentence ends there: the browser's own words for a failed request say nothing a person can act on (C10,
    // decided by the lead); they are kept as the error's cause
    throw new Error('no answer from the Albedian server', { cause: err });
  }
}

/**
 * What happened to an upload on its way to a solved sky, as it happens: the receipt (the file is stored from then
 * on), each record of the solve job the server answers with, the sky it predicted from an early pose once its record
 * names one (the lock-on, D25), and how it ended. A failure carries the one sentence a person reads: the job's own
 * error, word for word, or, when no job record says why, that the solve stopped.
 */
export type UploadEvent =
  | { kind: 'stored'; receipt: IngestReceipt }
  | { kind: 'job'; job: FollowedJob }
  | { kind: 'early'; solution: ObservationSolution }
  | { kind: 'solved'; job: FollowedJob }
  | { kind: 'failed'; sentence: string; job: FollowedJob | null };

export interface SolveOptions {
  localObjectUrl?: string;
  onSessionUpdate?: (session: ObservationSession) => void;
  onEvent?: (event: UploadEvent) => void;
  /** The wait between two reads of the job; JOB_POLL_MS unless a test says otherwise. */
  pollMs?: number;
  /** A person's alignment (D24's Solve from here), sent as the solve's body: that solve is its own job (SOLVER's route). */
  alignment?: PersonAlignment;
  /**
   * That alignment refused outright (C15): the route's 422 for an upload with no capture time or place, in its own
   * sentence. The solve did not stop, so no failure is told and nothing is followed.
   */
  onRefused?: (sentence: string) => void;
}

/** The word a canceled job shows, alone: the state's own name (UIUX-jobs-QUESTION.md, D2). */
export const CANCELED_WORD = 'Canceled';

/**
 * Asks the server to cancel a job (`POST /api/ingest/jobs/{id}/cancel`, upload jobs design 3.6): a queued job ends
 * canceled at once, a running one records when the cancel was asked and ends once its work stops. Throws with the
 * server's reason when it refuses.
 */
export async function cancelJob(jobId: string): Promise<FollowedJob> {
  const res = await fetchAnswer(`/api/ingest/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error(await serverReason(res));
  // The one cancel route serves every kind of job (upload jobs design 3.6)
  const body: unknown = await res.json();
  return body !== null && typeof body === 'object' && 'kind' in body ? UploadJobSchema.parse(body) : SolveJobSchema.parse(body);
}

/**
 * Starts, joins or returns the restoration of a stored still (`POST /api/media/{sha256}/restore`, restore-2b): 202
 * with a new job, 200 with the active one it joined or the current succeeded one. Throws with the server's reason when
 * it refuses (a clip, until restore-7, is answered "Restoring a clip is not built yet.").
 */
export async function startRestoration(sha256: string): Promise<UploadJob> {
  const res = await fetchAnswer(`/api/media/${sha256}/restore`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ kind: 'restoration' }),
  });
  if (!res.ok) throw new Error(await serverReason(res));
  const job = UploadJobSchema.parse(await res.json());
  if (job.kind !== 'restoration') throw new Error(`a ${job.kind} job is not a restoration`);
  return job;
}

/**
 * Follows a job of any kind at `GET /api/ingest/jobs/{id}` while it is queued or running, once per `pollMs`
 * (JOB_POLL_MS), telling each record, and resolves with the record it ended in. A job that already ended is read no
 * further. Throws with the server's reason when a read is refused, and "stopped following" once `signal` aborts.
 */
export async function followJob(
  job: UploadJob,
  onRecord: (job: UploadJob) => void,
  options: { pollMs?: number; signal?: AbortSignal } = {}
): Promise<UploadJob> {
  const pollMs = options.pollMs ?? JOB_POLL_MS;
  let current = job;
  while (current.state === 'queued' || current.state === 'running') {
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    if (options.signal?.aborted) throw new Error('stopped following');
    const res = await fetchAnswer(`/api/ingest/jobs/${encodeURIComponent(current.id)}`);
    if (!res.ok) throw new Error(await serverReason(res));
    current = UploadJobSchema.parse(await res.json());
    onRecord(current);
    if (options.signal?.aborted) throw new Error('stopped following');
  }
  return current;
}

/**
 * The sky a running solve predicted from a pose it knew before its search ended (platform-10b:
 * `GET /api/ingest/jobs/{id}/predicted-sky`): an approximate solution with its pose estimate. None when the route
 * answers otherwise (404 before the record names it, after the job ends, or for another job) or with anything else.
 */
export async function readPredictedSky(jobId: string): Promise<ObservationSolution | null> {
  const res = await fetchAnswer(`/api/ingest/jobs/${encodeURIComponent(jobId)}/predicted-sky`);
  if (!res.ok) return null;
  const parsed = ObservationSolutionSchema.safeParse(await res.json().catch(() => undefined));
  if (!parsed.success || parsed.data.status !== 'approximate' || parsed.data.poseEstimate === undefined) return null;
  return parsed.data;
}

/**
 * Why a restoration attempt was refused, from the upload's attempt records (`GET /api/media/{sha256}/variants`): its
 * rejection reason and, from its gate, how many points of light the restoration showed that the photo lacks. Null when
 * the attempt is not listed or says no reason.
 */
export async function readRefusal(
  sha256: string,
  attemptId: string
): Promise<{ rejection: string; unconfirmedPointCount: number | null } | null> {
  const res = await fetchAnswer(`/api/media/${sha256}/variants`);
  if (!res.ok) throw new Error(await serverReason(res));
  const body = (await res.json()) as { attempts?: Array<Record<string, unknown>> };
  const attempt = body.attempts?.find((entry) => entry.id === attemptId);
  if (!attempt || typeof attempt.rejection !== 'string') return null;
  const gate = attempt.gate as { unconfirmedPointCount?: unknown } | undefined;
  const count = gate && Number.isInteger(gate.unconfirmedPointCount) ? (gate.unconfirmedPointCount as number) : null;
  return { rejection: attempt.rejection, unconfirmedPointCount: count };
}

/**
 * How a stored variant is turned to be shown upright, from its record (`GET /api/media/{sha256}/variants/{id}`): a
 * restored PNG carries no orientation tag of its own, so the browser draws it as stored. Unturned when the record names
 * none. Throws with the server's reason when it refuses.
 */
export async function readVariantOrientation(sha256: string, variantId: string): Promise<DisplayOrientation> {
  const res = await fetchAnswer(`/api/media/${sha256}/variants/${encodeURIComponent(variantId)}`);
  if (!res.ok) throw new Error(await serverReason(res));
  const body = (await res.json()) as { variant?: { displayOrientation?: unknown } };
  const named = body.variant?.displayOrientation;
  return named === undefined ? { quarterTurnsClockwise: 0, mirrored: false } : DisplayOrientationSchema.parse(named);
}

/** A ready restoration of the upload as the variants route lists it (D36). */
export interface StoredRestoration {
  id: string;
  createdAtUTC: string;
  byteSize: number;
  orientation: DisplayOrientation;
  /** The variant as the route lists it, which the session holds while it is shown (voice-restore-one). */
  variant?: MediaVariant;
}

/**
 * The upload's ready restorations, newest first (D36): the variants route stores a variant only once its gate passed,
 * so every restoration it lists is ready. Anything that is not a restoration of this upload is left out.
 */
export async function readRestorations(sha256: string): Promise<StoredRestoration[]> {
  const res = await fetchAnswer(`/api/media/${sha256}/variants`);
  if (!res.ok) throw new Error(await serverReason(res));
  const body = (await res.json()) as { variants?: unknown[] };
  return (body.variants ?? [])
    .flatMap((raw) => {
      const parsed = MediaVariantSchema.safeParse(raw);
      if (!parsed.success || parsed.data.kind !== 'restoration' || parsed.data.sourceSha256 !== sha256) return [];
      const v = parsed.data;
      return [{ id: v.id, createdAtUTC: v.createdAtUTC, byteSize: v.byteSize, orientation: v.displayOrientation ?? { quarterTurnsClockwise: 0, mirrored: false }, variant: v }];
    })
    .sort((a, b) => Date.parse(b.createdAtUTC) - Date.parse(a.createdAtUTC) || (a.id < b.id ? 1 : -1));
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** When a restoration was made, in the viewer's local time: "19 Nov, 13:20" (D36). */
export function madeAtWords(utc: string): string {
  const d = new Date(utc);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** Where a stored variant's bytes are served (`GET /api/media/{sha256}/variants/{variantId}/file`). */
export function variantFileUrl(sha256: string, variantId: string): string {
  return `/api/media/${sha256}/variants/${encodeURIComponent(variantId)}/file`;
}

/** One stored zip of a ready variant, byte for byte, and its manifest (D22; EXPORT's restore-8), sent as an attachment. */
export function variantExportUrl(sha256: string, variantId: string): string {
  return `/api/media/${sha256}/variants/${encodeURIComponent(variantId)}/export`;
}

/**
 * Hand a file to the browser's own download (D22: no toast, no progress; the browser's downloads list shows it): a link
 * with the download attribute, pressed once and taken away, so the page never leaves for the file.
 */
export function saveFromServer(url: string): void {
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', '');
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Upload a photo or clip to `POST /api/ingest`, then solve it (`solveStoredUpload`).
 *
 * An error answer to the upload throws `UploadRefusedError` with the server's reason (nothing was
 * stored). Once the receipt arrives the file is stored, so a solve that does not finish returns the
 * stored session with the reason instead of throwing.
 */
export async function uploadAndSolveMedia(file: Blob, filename: string, options?: SolveOptions): Promise<ObservationSession> {
  const bytes = await file.arrayBuffer();
  const ingestRes = await fetchAnswer('/api/ingest', {
    method: 'POST',
    headers: {
      'content-type': file.type || 'application/octet-stream',
      'x-albedian-filename': encodeURIComponent(filename),
    },
    body: bytes,
  });
  if (!ingestRes.ok) {
    throw new UploadRefusedError(ingestRes.status, await serverDetail(ingestRes));
  }
  const receipt = IngestReceiptSchema.parse(await ingestRes.json().catch(() => undefined));
  options?.onEvent?.({ kind: 'stored', receipt });
  return solveStoredUpload(receipt, options, filename);
}

/**
 * Solve a stored upload: start `POST /api/ingest/{sha256}/solve?wait=false`, read `GET /api/ingest/jobs/{id}` once a
 * second while the job is queued or running (upload jobs design 3.9), reporting every record, and load
 * `GET /api/ingest/{sha256}/solution` into a contract-validated `ObservationSession` once it succeeded. A server
 * that answers the start with a job already ended is followed no further. The same call starts the solve again after
 * one that stopped. It never throws: a solve that does not finish returns the stored session with the reason.
 */
export async function solveStoredUpload(
  receipt: IngestReceipt,
  options?: SolveOptions,
  filename: string = receipt.sourceAsset.title
): Promise<ObservationSession> {
  // The name in the status line: the one the upload was sent under, else the receipt's own
  const ingestedSession = buildSessionFromIngestReceipt(receipt, {
    localObjectUrl: options?.localObjectUrl,
  });
  options?.onSessionUpdate?.(ingestedSession);

  // Stored from here on: a solve that does not finish keeps the file on screen and says why
  const solveDidNotFinish = (err: unknown, job: FollowedJob | null): ObservationSession => {
    const stored = ObservationSessionSchema.parse({
      ...ingestedSession,
      machines: { ...ingestedSession.machines, calibration: 'not_requested' },
      lastStatusBanner: `Stored "${filename}", but the sky solve did not finish: ${failureReason(err)}.`,
    });
    options?.onSessionUpdate?.(stored);
    options?.onEvent?.({ kind: 'failed', sentence: solveStoppedSentence(receipt.sourceAsset), job });
    return stored;
  };

  const sha256 = receipt.sourceAsset.sha256;
  // The lock-on (D25): once a running record names the sky predicted from an early pose, it is read once and told; a
  // read that fails tells nothing, so the working screen stays as approved
  let earlyRead = false;
  const tellEarly = async (record: FollowedJob) => {
    if (earlyRead || record.state !== 'running' || !('predictedSkyAtUTC' in record) || record.predictedSkyAtUTC === undefined) return;
    earlyRead = true;
    const sky = await readPredictedSky(record.id).catch(() => null);
    if (sky) options?.onEvent?.({ kind: 'early', solution: sky });
  };
  let job: FollowedJob;
  try {
    const solveRes = await fetchAnswer(
      `/api/ingest/${sha256}/solve?wait=false`,
      options?.alignment ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(options.alignment) } : { method: 'POST' }
    );
    if (!solveRes.ok) {
      const detail = await serverDetail(solveRes);
      const asset = receipt.sourceAsset;
      if (solveRes.status === 422 && options?.alignment && options.onRefused && detail && (!asset.capturedAtUTC || !asset.observerLocation)) {
        options.onRefused(detail);
        return ingestedSession;
      }
      throw new Error(detail ?? statusInWords(solveRes.status));
    }
    job = parseFollowedJob(await solveRes.json());
  } catch (err) {
    return solveDidNotFinish(err, null);
  }
  options?.onEvent?.({ kind: 'job', job });
  await tellEarly(job);
  const pollMs = options?.pollMs ?? JOB_POLL_MS;
  while (job.state === 'queued' || job.state === 'running') {
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    try {
      const jobRes = await fetchAnswer(`/api/ingest/jobs/${encodeURIComponent(job.id)}`);
      if (!jobRes.ok) throw new Error(await serverReason(jobRes));
      job = parseFollowedJob(await jobRes.json());
    } catch (err) {
      return solveDidNotFinish(err, job);
    }
    options?.onEvent?.({ kind: 'job', job });
    await tellEarly(job);
  }
  if (job.state === 'canceled') {
    const stored = ObservationSessionSchema.parse({
      ...ingestedSession,
      machines: { ...ingestedSession.machines, calibration: 'not_requested' },
      lastStatusBanner: `Stored "${filename}", but the sky solve did not finish: canceled.`,
    });
    options?.onSessionUpdate?.(stored);
    options?.onEvent?.({ kind: 'failed', sentence: CANCELED_WORD, job });
    return stored;
  }
  if (STOPPED_WITH_REASON.has(job.state)) {
    const failedSession = buildSessionFromIngestReceipt(receipt, {
      localObjectUrl: options?.localObjectUrl,
      job,
    });
    options?.onSessionUpdate?.(failedSession);
    options?.onEvent?.({ kind: 'failed', sentence: job.error ?? solveStoppedSentence(receipt.sourceAsset), job });
    return failedSession;
  }

  let solution: ObservationSolution;
  try {
    const solRes = await fetchAnswer(`/api/ingest/${sha256}/solution`);
    if (!solRes.ok) throw new Error(await serverReason(solRes));
    solution = ObservationSolutionSchema.parse(await solRes.json());
  } catch (err) {
    return solveDidNotFinish(err, job);
  }
  const finalSession = buildSessionFromIngestReceipt(receipt, {
    localObjectUrl: options?.localObjectUrl,
    job,
    solution,
  });
  options?.onSessionUpdate?.(finalSession);
  options?.onEvent?.({ kind: 'solved', job });
  return finalSession;
}
