import {
  CelestialFinding,
  ObservationSession,
  deterministicHexDigest,
} from '@albedian/contracts';
import { createDefaultSaharaObservationSession } from '@albedian/observation';
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
  capturedAtUTC: string;
  latDeg: number;
  lonDeg: number;
  cameraModel: string;
  urls: MediaAssetUrls;
  solution?: ObservationSession['observationSolution'];
}

function toCartesian(raDeg: number, decDeg: number, distPc: number): [number, number, number] {
  const u = raDecToUnitVector(raDeg, decDeg);
  return [u[0] * distPc, u[1] * distPc, u[2] * distPc];
}

/**
 * Query the Python FastAPI backend (`GET /api/media/discover`) for any media files
 * dynamically present in `data/uploads/`. Returns an empty list if offline.
 */
export async function fetchDiscoveredMedia(): Promise<DiscoveredMediaItem[]> {
  try {
    const res = await fetch('/api/media/discover');
    if (!res.ok) return [];
    const data = (await res.json()) as { items?: DiscoveredMediaItem[] };
    return Array.isArray(data.items) ? data.items : [];
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
          observerLat: session.sourceAsset.observerLocation?.latDeg ?? 30.0,
          observerLon: session.sourceAsset.observerLocation?.lonDeg ?? -40.0,
          observedAtUTC: session.sourceAsset.capturedAtUTC ?? '2030-11-01T22:32:00Z',
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
 * Build an `ObservationSession` dynamically from any backend-discovered media item
 * without hardcoding specific filenames or coordinates.
 */
export function buildSessionFromDiscoveredMedia(item: DiscoveredMediaItem): ObservationSession {
  const base = createDefaultSaharaObservationSession();
  const sha = item.sha256 || deterministicHexDigest(item.filename);

  const findings: CelestialFinding[] =
    item.solution?.findings && item.solution.findings.length > 0
      ? item.solution.findings.map((f) => ({
          ...f,
          cartesianParsecICRS:
            f.cartesianParsecICRS ??
            toCartesian(
              f.skyPosition.raDeg,
              f.skyPosition.decDeg,
              f.inferredDistancePc?.median ?? 25.0
            ),
        }))
      : base.observationSolution.findings;

  return {
    ...base,
    id: `session_${item.id}`,
    title: `${item.title} (${item.latDeg.toFixed(4)}°N, ${item.lonDeg.toFixed(4)}°E · ${item.capturedAtUTC.slice(11, 19)} UTC)`,
    sourceAsset: {
      ...base.sourceAsset,
      id: `src_${item.id}`,
      title: item.filename,
      mediaKind: item.mediaKind,
      sha256: sha,
      uri: item.urls.origVideoUrl ?? item.urls.origUrl,
      width: item.width,
      height: item.height,
      durationMs: Math.round(item.durationSec * 1000),
      capturedAtUTC: item.capturedAtUTC,
      observerLocation: {
        latDeg: item.latDeg,
        lonDeg: item.lonDeg,
        placeName: `Field Observation (${item.latDeg.toFixed(3)}°, ${item.lonDeg.toFixed(3)}°)`,
      },
      cameraMetadata: {
        model: item.cameraModel,
      },
    },
    observationSolution: item.solution
      ? {
          ...item.solution,
          findings,
        }
      : {
          ...base.observationSolution,
          sourceAssetId: `src_${item.id}`,
          sourceSha256: sha,
          observedAtUTC: item.capturedAtUTC,
          observerLocation: {
            latDeg: item.latDeg,
            lonDeg: item.lonDeg,
          },
          findings,
        },
    selectedFindingId: findings[0]?.id ?? base.selectedFindingId,
    lastStatusBanner: `Loaded "${item.filename}" (${item.cameraModel} · SHA-256 ${sha.slice(0, 12)}…). Ready for synchronized Reveal A/B wipe and sky exploration.`,
  };
}
