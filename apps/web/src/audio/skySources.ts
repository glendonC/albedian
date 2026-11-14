/**
 * The catalog stars in view as inputs of the spatial audio engine (AUDIO_ENGINE.md section 8).
 *
 * Pure functions from decoded catalog tiles and the camera's pose. Every value is carried as the
 * catalog gives it: the ICRS direction of the core tile, the Bailer-Jones (2021) median distance,
 * and the Gaia DR3 GSP-Phot effective temperature and FLAME luminosity of the astro tile. Where
 * Gaia DR3 has no value the input is null and the engine keeps that star silent; nothing is
 * estimated or filled in here. Directions and the listener's orientation share one frame, ICRS,
 * so turning the camera is all that moves a voice.
 */

export {
  DEFAULT_INCUMBENT_MARGIN_RAD,
  DEFAULT_MAX_AUDIBLE_SOURCES,
  DEFAULT_MAX_SILENT_SOURCES,
} from './skySourceModules/types.js';
export type {
  SkyCameraPose,
  SkySourceOptions,
  SkySourceResult,
  SkyTileData,
  SkyView,
} from './skySourceModules/types.js';
export {
  angleBetweenUnit,
  conjugateQuat,
  listenerOrientationIcrs,
  multiplyQuat,
} from './skySourceModules/icrsQuaternion.js';
export {
  directionInView,
  skyViewFromPose,
  widenView,
} from './skySourceModules/frustumView.js';
export { skySourceId } from './skySourceModules/tileBoundsCache.js';
export { skySourcesInView } from './skySourceModules/sourceSelection.js';
