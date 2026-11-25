import { describe, expect, it } from 'vitest';
import { FLIGHT_DRAW_LIMIT_MAG, FLIGHT_LOAD_LIMIT_MAG } from '../../flight/flightFrame.js';
import { PREDICTED_SKY_LIMIT_MAG, skyViewPicture } from '../predictedSkyPicture.js';

// realsky-points-one (DECIDED-BY-LEAD on LOOK's depth table): the flight loads every shipped layer, to G 12, so a star
// that brightens as the camera nears can appear; the depth drawn is one number for the flight's start, Real sky (its
// stars' default, RealSkyStars.tsx) and the predicted sky, built at 10.5 until the lead rules it on pictures.

describe('one depth for the three skies', () => {
  it('loads to the shipped tiles\' own depth', () => {
    expect(FLIGHT_LOAD_LIMIT_MAG).toBe(12);
  });

  it('draws the flight, Real sky and the predicted sky to one depth, 10.5 for now', () => {
    expect(FLIGHT_DRAW_LIMIT_MAG).toBe(10.5);
    expect(PREDICTED_SKY_LIMIT_MAG).toBe(FLIGHT_DRAW_LIMIT_MAG);
    expect(skyViewPicture(true).limitMag).toBe(FLIGHT_DRAW_LIMIT_MAG);
    expect(FLIGHT_LOAD_LIMIT_MAG).toBeGreaterThanOrEqual(FLIGHT_DRAW_LIMIT_MAG);
  });
});
