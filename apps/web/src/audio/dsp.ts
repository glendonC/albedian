/**
 * Pure signal helpers for the audio engine: deterministic noise for the skyglow bed, the
 * master soft-clip curve, low-crest-factor harmonic phases, PeriodicWave coefficients, the
 * pulsar pulse-train series, impulse-response energy normalization, and radix-2 FFT.
 * No Web Audio here.
 */

export type { Samples } from './dspModules/types.js';
export {
  DIFFUSE_FIELD_SN3D_WEIGHTS,
  NOISE_TARGET_RMS,
  binauralDiffuseNoise,
  diffuseFoaNoise,
  mulberry32,
  pinkNoise,
  whiteNoise,
} from './dspModules/noise.js';
export { energyNormalize, softClipCurve } from './dspModules/dynamics.js';
export {
  evaluatePulseTrain,
  gaussianPulseTrainSeries,
  periodicWaveCoefficients,
  schroederPhases,
} from './dspModules/harmonics.js';
export { fft } from './dspModules/fft.js';
