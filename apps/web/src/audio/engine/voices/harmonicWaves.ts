import {
  gaussianPulseTrainSeries,
  periodicWaveCoefficients,
  schroederPhases,
  whiteNoise,
} from '../../dsp.js';
import { harmonicCentroidHz, planckHarmonicAmplitudes } from '../../mappings.js';
import { applyParam } from './paramTransitions.js';
import {
  DEFAULT_HARMONIC_CEILING_HZ,
  MAX_HARMONICS,
  NYQUIST_HARMONIC_FRACTION,
  PARAM_TIME_CONSTANT_SEC,
  PULSAR_NOISE_RMS,
  PULSAR_NOISE_SECONDS,
  PULSAR_NOISE_SEED,
  PULSE_FWHM_FRACTION,
  UNCERTAINTY_HARMONICS,
  type SharedVoiceResources,
  type StarGenerator,
} from './voiceTypes.js';

export function resolveHarmonicCeilingHz(harmonicCeilingHz: number): number {
  return Number.isFinite(harmonicCeilingHz) && harmonicCeilingHz > 0
    ? harmonicCeilingHz
    : DEFAULT_HARMONIC_CEILING_HZ;
}

/** Pre-computes the shared pulsar noise buffer, Gaussian pulse PeriodicWave, and uncertainty fifth wave. */
export function createSharedVoiceResources(
  ctx: BaseAudioContext,
  harmonicCeilingHz: number
): SharedVoiceResources {
  const noiseData = whiteNoise(
    Math.round(PULSAR_NOISE_SECONDS * ctx.sampleRate),
    PULSAR_NOISE_SEED,
    PULSAR_NOISE_RMS
  );
  const noise = ctx.createBuffer(1, noiseData.length, ctx.sampleRate);
  noise.copyToChannel(noiseData, 0);

  const series = gaussianPulseTrainSeries(PULSE_FWHM_FRACTION);
  const pulse = periodicWaveCoefficients(series.cosine);
  const uncertainty = periodicWaveCoefficients(UNCERTAINTY_HARMONICS);

  return {
    noise,
    pulseWave: ctx.createPeriodicWave(pulse.real, pulse.imag, { disableNormalization: true }),
    pulseDc: series.dc,
    uncertaintyWave: ctx.createPeriodicWave(uncertainty.real, uncertainty.imag, {
      disableNormalization: true,
    }),
    harmonicCeilingHz: resolveHarmonicCeilingHz(harmonicCeilingHz),
  };
}

/** Synthesizes a Schroeder-phased Planck harmonic PeriodicWave for a star voice and updates its pitch/centroid. */
export function retuneStarGenerator(
  ctx: BaseAudioContext,
  star: StarGenerator,
  teffK: number,
  pitchHz: number,
  harmonicCeilingHz: number,
  now: number,
  immediate: boolean
): void {
  const ceiling = Math.min(harmonicCeilingHz, ctx.sampleRate * NYQUIST_HARMONIC_FRACTION);
  const count = Math.max(1, Math.min(MAX_HARMONICS, Math.floor(ceiling / pitchHz)));
  const amplitudes = planckHarmonicAmplitudes(teffK, pitchHz, count);
  if (!amplitudes) return;
  const powers = amplitudes.map((a) => a * a);
  const { real, imag } = periodicWaveCoefficients(amplitudes, schroederPhases(powers));
  star.osc.setPeriodicWave(ctx.createPeriodicWave(real, imag, { disableNormalization: true }));
  applyParam(star.osc.frequency, pitchHz, now, immediate, PARAM_TIME_CONSTANT_SEC);
  star.teffK = teffK;
  star.pitchHz = pitchHz;
  star.centroidHz = harmonicCentroidHz(amplitudes, pitchHz);
}
