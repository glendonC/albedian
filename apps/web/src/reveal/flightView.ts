import type { ObservationSession } from '@albedian/contracts';
import { flightSourceOf } from '../spatial/flight/flightFrame.js';
import { resolveFindingParsecPosition } from '@albedian/camera-rig';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import { distancePc } from '@albedian/observation';
import { canDrawRealSky } from './RealSkyLayer.js';
import { readoutFigures } from '../spatial/flight/nearStar.js';

/**
 * Go there (fly, approved 2026-11-12): after a star card's Go there, the photo gives way to the real sky in the same
 * place, only the picked star's mark stays, and the readout takes the card's place. This screen shows for a photo with
 * a camera to leave from (a verified solve, a pose estimate, or the predicted sky's view kept in the checkpoint it
 * left from) once the reducer has entered space toward the picked star, where the panorama can be drawn.
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
    // A verified solve, a pose estimate (go-there-predicted-1) or the predicted sky's kept view (go-there-predicted-2)
    flightSourceOf(session) !== null &&
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

/**
 * Three figures, as the approved screens write the readout (fly: "24.0" from Earth, "25.0" to "1.00" to the star; D30:
 * "160", "321"). readout-units-one (UIUX-flight-READY "The readout's units near a star", DECIDED-BY-LEAD 27 Nov 00:21,
 * rule 2): three significant figures at every size ("0.0158", "75,700"), grouped, zero as "0"; the readout's own
 * figures (nearStar.ts readoutFigures), with which its units go (readoutDistance).
 */
export function threeFigures(value: number): string {
  return readoutFigures(value);
}
