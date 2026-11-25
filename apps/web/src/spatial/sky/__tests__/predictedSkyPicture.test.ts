import { describe, expect, it } from 'vitest';
import { DEFAULT_LIMITING_MAGNITUDE } from '../../catalogStars/field/fieldTypes.js';
import { PREDICTED_SKY_LIMIT_MAG, SKY_PICTURE_URL, milkyWayInPredictedSkyChanges, skyViewPicture } from '../predictedSkyPicture.js';

// predsky-starless-one (DECIDED-BY-LEAD 24 Nov 18:04, choice A): the predicted sky alone takes the starless glow and the
// catalog to 10.5; every other sky view keeps ESO's photograph as published and the naked-eye 6.5.

describe('the sky view\'s picture and limit', () => {
  it('in the predicted sky: the copy with its own stars removed, and the catalog down to 10.5', () => {
    expect(skyViewPicture(true)).toEqual({ picture: 'starless', limitMag: 10.5 });
    expect(SKY_PICTURE_URL.starless).toBe('/textures/eso_milkyway_diffuse_6k.webp');
    expect(PREDICTED_SKY_LIMIT_MAG).toBe(10.5);
  });

  it('everywhere else: ESO\'s photograph as published, and the naked-eye limit as today', () => {
    expect(skyViewPicture(false)).toEqual({ picture: 'photograph', limitMag: DEFAULT_LIMITING_MAGNITUDE });
    expect(SKY_PICTURE_URL.photograph).toBe('/textures/eso_milkyway_panorama.jpg');
    expect(DEFAULT_LIMITING_MAGNITUDE).toBe(6.5);
  });

  it('says so on the credits row, with the limit drawn', () => {
    const line = milkyWayInPredictedSkyChanges();
    expect(line).toBe("In the predicted sky, the copy made from it with its own stars removed is drawn in its place, placed and graded the same, and the catalog's stars are drawn in theirs down to magnitude 10.5.");
    expect(line).not.toMatch(/[\u2013\u2014]/);
  });
});
