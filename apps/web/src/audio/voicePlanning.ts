/**
 * Pure voice planning: turns data about objects into the parameters a voice needs, decides
 * which objects get one of the bounded HRTF voices, and gives each voice a pitch.
 *
 * A voice is silent (never planned) when any input its core sound depends on is missing.
 * Optional layers (dust muffling, uncertainty beating) are present only when their own input
 * is present. Nothing here touches Web Audio.
 */

export type {
  AudioSourceInput,
  AudioSourceKind,
  PlanContext,
  PlanOutcome,
  Quat,
  Vec3,
  VoicePlan,
} from './planning/planningTypes.js';
export { distanceWindowFor, planVoice } from './planning/singleVoicePlan.js';
export {
  DEFAULT_SELECTION_HYSTERESIS_DB,
  DEFAULT_VOICE_CAPACITY,
  selectVoices,
} from './planning/voiceSelection.js';
export {
  IDENTITY_PITCHES_HZ,
  assignIdentityPitches,
  fnv1a32,
} from './planning/identityPitch.js';
export {
  IDENTITY_QUAT,
  directionFromAzimuthElevation,
  normalizeQuat,
  rotateByQuat,
  worldToListenerDirection,
  yawQuat,
} from './planning/spatialOrientation.js';
