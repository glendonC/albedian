import { hasVerifiedWcs, type ObservationSession } from '@albedian/contracts';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import { distancePc } from '@albedian/observation';
import { canDrawRealSky } from './RealSkyLayer.js';

/**
 * Go there (fly, approved 2026-11-12): after a star card's Go there, the photo gives way to the real sky in the same
 * place, only the picked star's mark stays, and the readout takes the card's place. This screen shows for a photo with
 * a verified solve once the reducer has entered space toward the picked star, where the panorama can be drawn.
 */
export function isFlightShown(session: ObservationSession): boolean {
  const solution = session.observationSolution;
  const navigation = session.machines?.navigation;
  return (
    session.sourceAsset !== undefined &&
    session.selectedFindingId != null &&
    session.spatialView?.mode === 'catalog_3d' &&
    (navigation === 'space' || navigation === 'transitioning') &&
    solution !== undefined &&
    solution.status === 'calibrated' &&
    hasVerifiedWcs(solution) &&
    canDrawRealSky()
  );
}

export interface FlightReadout {
  name: string;
  /** How far the camera is from the star, in light years; none when the star has no position in space. */
  toStarLy: number | null;
  /** How far the camera is from Earth, in light years. */
  fromEarthLy: number;
}

/** The readout's numbers, from the flight's own camera position as it moves (the session's observer origin). */
export function flightReadout(session: ObservationSession): FlightReadout | null {
  const star = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId);
  if (!star) return null;
  const origin = session.spatialView?.observerOriginParsec ?? [0, 0, 0];
  const at = resolveFindingParsecPosition(star);
  return {
    name: star.title,
    toStarLy: at ? distancePc(origin, at) * PARSEC_TO_LIGHT_YEAR : null,
    fromEarthLy: Math.hypot(origin[0], origin[1], origin[2]) * PARSEC_TO_LIGHT_YEAR,
  };
}

/** One decimal, as the approved readout writes it ("1.0", "24.0"), thousands grouped. */
export function oneDecimal(value: number): string {
  return value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
