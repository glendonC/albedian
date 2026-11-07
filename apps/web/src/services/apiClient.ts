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
} from '@albedian/contracts';
import { createSessionForSourceAsset } from '@albedian/observation';
import { raDecToUnitVector } from '@albedian/camera-rig';

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

  return {
    ...session,
    title: item.title,
    machines: { ...session.machines, calibration: calibrationStateForSolution(item.solution.status) },
    observationSolution: { ...item.solution, findings },
    lastStatusBanner: `Loaded "${item.filename}" (${shaLabel}). Sky solution: ${item.solution.status}.`,
  };
}

/**
 * Format a `SolveJob`'s stage progress using its real work denominators (`done/total unit`),
 * or elapsed state when `total` is `null`, without inventing percentages.
 */
export function formatSolveJobProgressBanner(
  filename: string,
  job: SolveJob,
  solution?: ObservationSolution
): string {
  const parsedJob = SolveJobSchema.parse(job);
  const stageParts = parsedJob.stages.map((entry) =>
    entry.total !== null
      ? `${entry.stage}: ${entry.done}/${entry.total} ${entry.unit}`
      : `${entry.stage}: ${entry.done} ${entry.unit} (elapsed)`
  );
  const stageSummary = stageParts.join(' · ');
  if (parsedJob.state === 'failed') {
    return `Solve job ${parsedJob.id} failed for "${filename}": ${parsedJob.error ?? 'unknown error'} (${stageSummary}).`;
  }
  if (solution) {
    return `Loaded "${filename}" (${parsedJob.id}, ${stageSummary}). Sky solution: ${solution.status}.`;
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
    job?: SolveJob;
    solution?: ObservationSolution;
  }
): ObservationSession {
  const parsedReceipt = IngestReceiptSchema.parse(receipt);
  const asset = {
    ...parsedReceipt.sourceAsset,
    uri: options?.localObjectUrl ?? parsedReceipt.sourceAsset.uri,
  };
  const baseSession = createSessionForSourceAsset(asset, 'ready');
  const parsedJob = options?.job ? SolveJobSchema.parse(options.job) : undefined;

  if (parsedJob?.state === 'failed') {
    return ObservationSessionSchema.parse({
      ...baseSession,
      machines: { ...baseSession.machines, media: 'ready', calibration: 'unresolved' },
      observationSolution: {
        ...baseSession.observationSolution,
        status: 'unresolved',
        warnings: [parsedJob.error ?? 'Solve job failed.'],
      },
      lastStatusBanner: formatSolveJobProgressBanner(asset.title, parsedJob),
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

  return ObservationSessionSchema.parse({
    ...baseSession,
    machines: {
      ...baseSession.machines,
      media: 'ready',
      calibration: calibrationStateForSolution(parsedSolution.status),
    },
    observationSolution: { ...parsedSolution, findings },
    lastStatusBanner: parsedJob
      ? formatSolveJobProgressBanner(asset.title, parsedJob, parsedSolution)
      : `Loaded "${asset.title}" (${shaLabel}). Sky solution: ${parsedSolution.status}.`,
  });
}

/**
 * Upload a photo or clip to `POST /api/ingest`, run `POST /api/ingest/{sha256}/solve`, and load
 * `GET /api/ingest/{sha256}/solution` into a contract-validated `ObservationSession`.
 */
export async function uploadAndSolveMedia(
  file: Blob,
  filename: string,
  options?: {
    localObjectUrl?: string;
    onSessionUpdate?: (session: ObservationSession) => void;
  }
): Promise<ObservationSession> {
  const bytes = await file.arrayBuffer();
  const ingestRes = await fetch('/api/ingest', {
    method: 'POST',
    headers: {
      'content-type': file.type || 'application/octet-stream',
      'x-albedian-filename': encodeURIComponent(filename),
    },
    body: bytes,
  });
  if (!ingestRes.ok) {
    throw new Error(`Upload to /api/ingest failed with HTTP ${ingestRes.status}`);
  }
  const receipt = IngestReceiptSchema.parse(await ingestRes.json());
  const ingestedSession = buildSessionFromIngestReceipt(receipt, {
    localObjectUrl: options?.localObjectUrl,
  });
  options?.onSessionUpdate?.(ingestedSession);

  const sha256 = receipt.sourceAsset.sha256;
  const solveRes = await fetch(`/api/ingest/${sha256}/solve`, { method: 'POST' });
  if (!solveRes.ok) {
    throw new Error(`Solve job request for ${sha256} failed with HTTP ${solveRes.status}`);
  }
  const job = SolveJobSchema.parse(await solveRes.json());
  if (job.state === 'failed') {
    const failedSession = buildSessionFromIngestReceipt(receipt, {
      localObjectUrl: options?.localObjectUrl,
      job,
    });
    options?.onSessionUpdate?.(failedSession);
    return failedSession;
  }

  const solRes = await fetch(`/api/ingest/${sha256}/solution`);
  if (!solRes.ok) {
    throw new Error(`Reading solution for ${sha256} failed with HTTP ${solRes.status}`);
  }
  const solution = ObservationSolutionSchema.parse(await solRes.json());
  const finalSession = buildSessionFromIngestReceipt(receipt, {
    localObjectUrl: options?.localObjectUrl,
    job,
    solution,
  });
  options?.onSessionUpdate?.(finalSession);
  return finalSession;
}

