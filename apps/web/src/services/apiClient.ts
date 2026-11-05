import {
  CalibrationMachineState,
  CelestialFinding,
  ObservationSession,
  SolutionStatus,
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
  durationSec: number;
  fps: number;
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
  mediaType?: 'image' | 'video';
  width: number;
  height: number;
  durationSec: number;
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

function toCartesian(raDeg: number, decDeg: number, distPc: number): [number, number, number] {
  const u = raDecToUnitVector(raDeg, decDeg);
  return [u[0] * distPc, u[1] * distPc, u[2] * distPc];
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
 */
export async function fetchDiscoveredMedia(): Promise<DiscoveredMediaItem[]> {
  try {
    const res = await fetch('/api/media/discover');
    if (!res.ok) return [];
    const data = (await res.json()) as { items?: RawBackendDiscoveredItem[] };
    if (!Array.isArray(data.items)) return [];

    return data.items.map((raw) => {
      const v = raw.variants ?? {};
      const origUrl = v.orig_png ?? v.default_png ?? v.orig_jpg ?? '';
      const v1Url = v.v1_png ?? origUrl;
      const v2Url = v.v2_png ?? v1Url;
      return {
        id: raw.id,
        title: formatReadableTitle(raw.filename),
        filename: raw.filename,
        sha256: raw.sha256,
        mediaKind: raw.mediaType ?? 'video',
        width: raw.width,
        height: raw.height,
        durationSec: raw.durationSec,
        fps: raw.fps ?? 1,
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
      };
    });
  } catch {
    return [];
  }
}

/**
 * Query the Python FastAPI backend (`POST /api/voice/command`) for natural-language
 * intent parsing and grounded scientific explanation, with deterministic offline fallback.
 */
export async function postVoiceIntentToBackend(
  utterance: string,
  session: ObservationSession
): Promise<{
  proposedAction?: Record<string, unknown>;
  scientificExplanation?: string;
} | null> {
  try {
    const res = await fetch('/api/voice/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        utterance,
        context: {
          sessionId: session.id,
          sourceAssetId: session.sourceAsset.id,
          sourceTimeSec: session.currentSourceTimeSec,
          navigationState: session.machines.navigation,
          calibrationState: session.machines.calibration,
          selectedFindingId: session.selectedFindingId,
          viewRevision: session.viewRevision,
          observerLat: session.sourceAsset.observerLocation?.latDeg ?? null,
          observerLon: session.sourceAsset.observerLocation?.lonDeg ?? null,
          observedAtUTC: session.sourceAsset.capturedAtUTC ?? null,
          simulationEpochISO: session.spatialView.simulationEpochISO,
        },
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as {
      proposedAction?: Record<string, unknown>;
      scientificExplanation?: string;
    };
  } catch {
    return null;
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
      fpsNumerator: Math.max(1, Math.round(item.fps * 1000)),
      fpsDenominator: 1000,
      videoCodec: item.videoCodec,
      audioStreams: [],
      capturedAtUTC: item.capturedAtUTC ?? undefined,
      observerLocation: placed ? { latDeg: item.latDeg!, lonDeg: item.lonDeg! } : undefined,
      cameraMetadata: item.cameraModel !== null ? { model: item.cameraModel } : undefined,
      metadataSources: {
        capturedAtUTC: item.capturedAtUTC
          ? { container: 'quicktime', tags: ['com.apple.quicktime.creationdate', 'creation_time'] }
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

  // Distances and sky positions are never assumed: findings without both keep no 3D position
  const findings: CelestialFinding[] = item.solution.findings.map((f) => ({
    ...f,
    cartesianParsecICRS:
      f.cartesianParsecICRS ??
      (f.inferredDistancePc && f.skyPosition
        ? toCartesian(f.skyPosition.raDeg, f.skyPosition.decDeg, f.inferredDistancePc.median)
        : undefined),
  }));

  return {
    ...session,
    title: item.title,
    machines: { ...session.machines, calibration: calibrationStateForSolution(item.solution.status) },
    observationSolution: { ...item.solution, findings },
    lastStatusBanner: `Loaded "${item.filename}" (${shaLabel}). Sky solution: ${item.solution.status}.`,
  };
}
