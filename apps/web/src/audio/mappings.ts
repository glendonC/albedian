/**
 * Pure data-to-parameter mappings for the Albedian spatial audio engine.
 *
 * One perceptual channel per physical quantity:
 *   distance            -> direct-to-reverberant ratio + high-frequency rolloff
 *   luminosity          -> source power, attenuated by the inverse-square law
 *   effective temp.     -> timbre brightness (spectral centroid from the Planck curve)
 *   sky brightness      -> level of a broadband noise bed (artificial skyglow only)
 *   residual            -> beat rate between two partials, clean interval at zero
 *   pulsar spin         -> pulse rate equal to the catalogue spin frequency, unscaled
 *   extinction A_V      -> low-pass muffling
 *
 * Every mapping returns `null` when its input is missing or invalid; the engine renders
 * `null` as silence for that layer and never substitutes a default value. Nothing in this
 * module touches Web Audio, so all of it runs, and is property-tested, in Node.
 */

export {
  DB_PER_MAGNITUDE,
  clamp,
  dbToGain,
  gainToDb,
  isFiniteNumber,
  lerp,
  logLerp,
} from './parameterMappings/math.js';

export {
  DISTANCE_CUES,
  PARSEC_KM,
  REGIME_DISTANCE_WINDOWS,
  STAR_DISTANCE_WINDOW,
  directReverbGains,
  directToReverberantDb,
  distanceLowpassHz,
  isValidDistanceWindow,
  perceptualDistance,
  type DistanceCueParams,
  type DistanceWindow,
  type ScaleAudioRegime,
} from './parameterMappings/distance.js';

export {
  LOUDNESS,
  SUN_ABSOLUTE_BOLOMETRIC_MAG,
  apparentBolometricMagnitude,
  receivedLevelDb,
  type LoudnessParams,
} from './parameterMappings/loudness.js';

export {
  PLANCK_MEAN_X,
  TIMBRE,
  ballesterosTeffFromBv,
  harmonicCentroidHz,
  planckFrequencyScaleHz,
  planckHarmonicAmplitudes,
  type TimbreParams,
} from './parameterMappings/timbre.js';

export {
  SKYGLOW,
  artificialSkyglowRatio,
  skyglowBedLevelDb,
  type SkyglowParams,
} from './parameterMappings/skyglow.js';

export {
  UNCERTAINTY,
  beatingFifthHz,
  uncertaintyBeatHz,
  type UncertaintyParams,
} from './parameterMappings/uncertainty.js';

export {
  PULSAR_MAX_SPIN_HZ,
  pulsarPulseRateHz,
} from './parameterMappings/pulsar.js';

export {
  DUST,
  dustLowpassHz,
  type DustParams,
} from './parameterMappings/dust.js';
