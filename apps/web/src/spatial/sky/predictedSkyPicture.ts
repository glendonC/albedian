import { DEFAULT_LIMITING_MAGNITUDE } from '../catalogStars/field/fieldTypes.js';
import { REAL_SKY_PANORAMA_URL } from '../../reveal/RealSkyLayer.js';
import { FLIGHT_MILKY_WAY_URL } from './flightMilkyWay.js';

/**
 * predsky-starless-one (DECIDED-BY-LEAD 24 Nov 18:04, choice A): the predicted sky's dome takes ESO's photograph with
 * its own stars removed (the baked starless glow, the same projection), and its catalog stars are drawn down to the
 * faintest magnitude the photograph itself shows, so each star is drawn once, at its catalog place, and the field keeps
 * what the photograph showed. Every other view keeps the photograph and the naked-eye limit; the music's sky feed
 * keeps 6.5.
 */

/**
 * The faintest magnitude bin in which catalog stars stand out in ESO's panorama, at the predicted sky's grade, more
 * often than the same test off the stars (LOOK's measure of 24 Nov, every star to G 12: 9.5 to 10.5 shows at 82.9%
 * against 79.4% in the plane of the Galaxy and 35.9% against 24.9% off it; 10.5 to 12 at 82.4% against 81.2% and
 * 25.4% against 24.6%, no more than the control).
 */
export const PREDICTED_SKY_LIMIT_MAG = 10.5;

/** Which picture the sky dome samples. */
export type SkyPicture = 'photograph' | 'starless';

export const SKY_PICTURE_URL: Readonly<Record<SkyPicture, string>> = {
  photograph: REAL_SKY_PANORAMA_URL,
  starless: FLIGHT_MILKY_WAY_URL,
};

/** The dome's picture and the catalog's limit for the sky view as shown. */
export function skyViewPicture(predictedSkyShown: boolean): { picture: SkyPicture; limitMag: number } {
  return predictedSkyShown ? { picture: 'starless', limitMag: PREDICTED_SKY_LIMIT_MAG } : { picture: 'photograph', limitMag: DEFAULT_LIMITING_MAGNITUDE };
}

/** The credits' eso0932a row: what the predicted sky draws in the photograph's place (components/creditRows.ts). */
export function milkyWayInPredictedSkyChanges(): string {
  return `In the predicted sky, the copy made from it with its own stars removed is drawn in its place, placed and graded the same, and the catalog's stars are drawn in theirs down to magnitude ${PREDICTED_SKY_LIMIT_MAG}.`;
}
