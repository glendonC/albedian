import type { ObservationSession } from '@albedian/contracts';

/**
 * D33, the predicted sky that Show predicted sky opens (UIUX-predsky-READY.md, decided by the lead 12:45): the words of
 * its status column. Every value is the recording's own (its capture time with the solved frame's offset, its place)
 * or the app's own star field's catalogues; nothing is lined up with the photo.
 */

/** Whether the screen shows now: the sky view opened for a recording whose stars were not matched (not Real sky). */
export function isPredictedSkyShown(session: ObservationSession): boolean {
  const solution = session.observationSolution;
  return session.sourceAsset !== undefined && session.spatialView?.mode === 'registered_sky' && solution !== undefined && solution.status !== 'calibrated';
}

/** The catalogues the app's star field draws (the There screen names the same two). */
export const PREDICTED_SKY_CATALOGUES = 'Hipparcos 2007 · Gaia DR3';

export interface PredictedSkyWords {
  heading: string;
  note: string;
  /** The time, the place and the catalogues, one per line, each only when the recording gives it. */
  facts: string[];
}

const two = (n: number) => String(n).padStart(2, '0');

/** "2025-07-24 05:32 UTC": the minute the moment falls in. */
export function formatSkyTime(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${two(d.getUTCMonth() + 1)}-${two(d.getUTCDate())} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())} UTC`;
}

/** "32.53° N · 95.63° W". */
export function formatSkyPlace(latDeg: number, lonDeg: number): string {
  return `${Math.abs(latDeg).toFixed(2)}° ${latDeg >= 0 ? 'N' : 'S'} · ${Math.abs(lonDeg).toFixed(2)}° ${lonDeg >= 0 ? 'E' : 'W'}`;
}

export function predictedSkyWords(session: ObservationSession): PredictedSkyWords {
  const asset = session.sourceAsset;
  const kind = asset?.mediaKind === 'video' ? 'clip' : 'photo';
  const facts: string[] = [];
  const captured = asset?.capturedAtUTC ? Date.parse(asset.capturedAtUTC) : Number.NaN;
  // For a clip the time is the solved frame's
  if (Number.isFinite(captured)) facts.push(formatSkyTime(captured + (session.observationSolution?.frameTimeSec ?? 0) * 1000));
  const place = asset?.observerLocation;
  if (place) facts.push(formatSkyPlace(place.latDeg, place.lonDeg));
  facts.push(PREDICTED_SKY_CATALOGUES);
  return {
    heading: `The sky predicted for your ${kind}'s time and place.`,
    note: `Drawn or computed by the app; not measured in the source recording. Not lined up with your ${kind}.`,
    facts,
  };
}
