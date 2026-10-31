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

interface RawBackendDiscoveredItem {
  id: string;
  filename: string;
  sha256: string;
  mediaType?: 'image' | 'video';
  width: number;
  height: number;
  durationSec: number;
  capturedAtUTC: string;
  cameraModel?: string | null;
  observerLocation?: {
    latDeg: number;
    lonDeg: number;
  };
  variants?: Record<string, string>;
  observationSolution?: ObservationSession['observationSolution'];
}

function toCartesian(raDeg: number, decDeg: number, distPc: number): [number, number, number] {
  const u = raDecToUnitVector(raDeg, decDeg);
  return [u[0] * distPc, u[1] * distPc, u[2] * distPc];
}

function formatReadableTitle(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, '');
  return stem
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * Query the Python FastAPI backend (`GET /api/media/discover`) for any media files
 * dynamically present in `data/uploads/`. Returns an empty list if offline.
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
        capturedAtUTC: raw.capturedAtUTC,
        latDeg: raw.observerLocation?.latDeg ?? 31.2021,
        lonDeg: raw.observerLocation?.lonDeg ?? -3.9458,
        cameraModel: raw.cameraModel ?? 'Field Sensor',
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
          observerLat: session.sourceAsset.observerLocation?.latDeg ?? 31.2021,
          observerLon: session.sourceAsset.observerLocation?.lonDeg ?? -3.9458,
          observedAtUTC: session.sourceAsset.capturedAtUTC ?? '2026-10-20T22:21:02Z',
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
