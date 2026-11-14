/**
 * Where the sky dome's image points, so both renderers can sample it by view direction and draw
 * it under the catalog stars (which sit at ICRS directions through `ICRS_TO_SCENE_QUATERNION`).
 * The shaders in `SparkPortalRenderer.ts` (`skyMat`) and `webgpu/cislunar/skyBackground.ts`
 * implement `panoramaUv`; this CPU version is what the tests check against the measured star
 * images.
 *
 * The ESO Milky Way panorama (eso0932a) is plate carree in its own frame, about 3.8 degrees from
 * galactic, plus a vertical offset and a table of stitching offsets, all fitted to catalog stars
 * by `scripts/register_sky_panorama.py` into `eso0932a_registration.json`.
 *
 * Texture coordinates follow three: u to the right, v = 1 at the top row of the image.
 */

export type {
  Mat3Rows,
  PanoramaOffsetTable,
  PanoramaRegistration,
} from './registrationModules/types.js';
export {
  ICRS_TO_GALACTIC,
  SKY_PANORAMA_REGISTRATION_FORMAT,
  SKY_PANORAMA_REGISTRATION_URL,
} from './registrationModules/types.js';
export {
  applyRows,
  multiplyRows,
  sceneToIcrsRows,
  sceneToPanoramaRows,
  transposeRows,
} from './registrationModules/matrix3.js';
export {
  panoramaUv,
  plateCarreeUv,
  sampleOffsetTable,
} from './registrationModules/panoramaProjection.js';
export {
  loadPanoramaRegistration,
  parsePanoramaRegistration,
} from './registrationModules/registrationLoader.js';
