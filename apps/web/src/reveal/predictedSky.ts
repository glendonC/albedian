import type { ObservationSession } from '@albedian/contracts';
import { startPose } from './alignPose.js';

/**
 * D33, the predicted sky that Show predicted sky opens (UIUX-predsky-READY.md, decided by the lead 12:45): the words of
 * its status column. The time and place are the ones the solve predicted the sky for (the solution's observedAtUTC and
 * observerLocation), so a time or place lent by the iPhone's record shows as used (the lead, 13:04); the catalogues are
 * the app's own star field's. Nothing is lined up with the photo.
 */

/** Whether the screen shows now: the sky view opened for a recording whose stars were not matched (not Real sky). */
export function isPredictedSkyShown(session: ObservationSession): boolean {
  const solution = session.observationSolution;
  return session.sourceAsset !== undefined && session.spatialView?.mode === 'registered_sky' && solution !== undefined && solution.status !== 'calibrated';
}

/** How high D33's sky opens: 30 degrees up on the laptop and 26 on the phone, so the horizon sits near the foot (13:23). */
export const PREDICTED_SKY_OPEN_ALT_DEG = { laptop: 30, phone: 26 } as const;

/**
 * Where D33's sky opens (decided by the lead 13:23): facing D24's start (the file's compass heading, else the
 * brightest predicted star above the horizon), at the height above. None where D24 has no start.
 */
export function predictedSkyStart(session: ObservationSession, phone: boolean): { azDeg: number; altDeg: number } | null {
  const start = startPose(session);
  return start ? { azDeg: start.pose.azDeg, altDeg: phone ? PREDICTED_SKY_OPEN_ALT_DEG.phone : PREDICTED_SKY_OPEN_ALT_DEG.laptop } : null;
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

/** At phone width the time and place are one line, "{time} · {place}" (item "Build, phone"). */
export function phoneFacts(words: PredictedSkyWords): string[] {
  const values = words.facts.filter((fact) => fact !== PREDICTED_SKY_CATALOGUES);
  return values.length > 0 ? [values.join(' · ')] : [];
}

export function predictedSkyWords(session: ObservationSession): PredictedSkyWords {
  const asset = session.sourceAsset;
  const kind = asset?.mediaKind === 'video' ? 'clip' : 'photo';
  const facts: string[] = [];
  const solution = session.observationSolution;
  const observed = solution?.observedAtUTC ? Date.parse(solution.observedAtUTC) : Number.NaN;
  if (Number.isFinite(observed)) facts.push(formatSkyTime(observed));
  const place = solution?.observerLocation;
  if (place) facts.push(formatSkyPlace(place.latDeg, place.lonDeg));
  facts.push(PREDICTED_SKY_CATALOGUES);
  return {
    heading: `The sky predicted for your ${kind}'s time and place.`,
    note: `Drawn or computed by the app; not measured in the source recording. Not lined up with your ${kind}.`,
    facts,
  };
}
