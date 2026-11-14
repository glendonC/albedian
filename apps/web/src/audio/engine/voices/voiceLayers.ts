import { beatingFifthHz } from '../../mappings.js';
import type { VoicePlan } from '../../voicePlanning.js';
import { glideParam, setParamNow } from '../createAudioContext.js';
import { retuneStarGenerator } from './harmonicWaves.js';
import { stopScheduledSource } from './paramTransitions.js';
import {
  ATTACK_TIME_CONSTANT_SEC,
  LAYER_STOP_AFTER_SEC,
  PARAM_TIME_CONSTANT_SEC,
  PULSAR_NOISE_SECONDS,
  RELEASE_STOP_AFTER_SEC,
  RELEASE_TIME_CONSTANT_SEC,
  UNCERTAINTY_RELATIVE_GAIN,
  type SharedVoiceResources,
  type VoiceGenerators,
} from './voiceTypes.js';

/** Synchronizes the star, pulsar, and uncertainty generator layers of an active slot with `plan`. */
export function syncVoiceLayers(
  ctx: BaseAudioContext,
  generators: VoiceGenerators,
  plan: VoicePlan,
  pitchHz: number,
  shared: SharedVoiceResources,
  now: number
): void {
  syncStarLayer(ctx, generators, plan, pitchHz, shared.harmonicCeilingHz, now);
  syncPulsarLayer(ctx, generators, plan, shared, now);
  syncUncertaintyLayer(ctx, generators, plan, pitchHz, shared.uncertaintyWave, now);
}

function syncStarLayer(
  ctx: BaseAudioContext,
  generators: VoiceGenerators,
  plan: VoicePlan,
  pitchHz: number,
  harmonicCeilingHz: number,
  now: number
): void {
  // Star tone: Planck-shaped harmonic spectrum at the identity pitch.
  if (plan.star) {
    if (!generators.star) {
      const osc = ctx.createOscillator();
      generators.star = { osc, teffK: Number.NaN, pitchHz: Number.NaN, centroidHz: 0 };
      osc.connect(generators.envelope);
      retuneStarGenerator(ctx, generators.star, plan.star.teffK, pitchHz, harmonicCeilingHz, now, true);
      osc.start(now);
    } else if (generators.star.teffK !== plan.star.teffK || generators.star.pitchHz !== pitchHz) {
      retuneStarGenerator(ctx, generators.star, plan.star.teffK, pitchHz, harmonicCeilingHz, now, false);
    }
  } else if (generators.star) {
    stopScheduledSource(generators.star.osc, now + LAYER_STOP_AFTER_SEC);
    generators.star = null;
  }
}

function syncPulsarLayer(
  ctx: BaseAudioContext,
  generators: VoiceGenerators,
  plan: VoicePlan,
  shared: SharedVoiceResources,
  now: number
): void {
  if (!plan.pulsar && generators.pulsar) {
    stopScheduledSource(generators.pulsar.noise, now + LAYER_STOP_AFTER_SEC);
    stopScheduledSource(generators.pulsar.envelopeOsc, now + LAYER_STOP_AFTER_SEC);
    generators.pulsar = null;
  }

  // Pulsar: broadband noise gated at the catalogue spin frequency.
  if (plan.pulsar) {
    if (!generators.pulsar) {
      const noise = ctx.createBufferSource();
      noise.buffer = shared.noise;
      noise.loop = true;
      const vca = ctx.createGain();
      setParamNow(vca.gain, shared.pulseDc, now);
      const envelopeOsc = ctx.createOscillator();
      envelopeOsc.setPeriodicWave(shared.pulseWave);
      setParamNow(envelopeOsc.frequency, plan.pulsar.pulseRateHz, now);
      envelopeOsc.connect(vca.gain);
      noise.connect(vca);
      vca.connect(generators.envelope);
      noise.start(now, Math.random() * PULSAR_NOISE_SECONDS);
      envelopeOsc.start(now);
      generators.pulsar = { noise, envelopeOsc, vca };
    } else {
      // Exact rate, no glide: a glide would play rates the pulsar never had.
      setParamNow(generators.pulsar.envelopeOsc.frequency, plan.pulsar.pulseRateHz, now);
    }
  }
}

function syncUncertaintyLayer(
  ctx: BaseAudioContext,
  generators: VoiceGenerators,
  plan: VoicePlan,
  pitchHz: number,
  uncertaintyWave: PeriodicWave,
  now: number
): void {
  // Uncertainty: a fifth that beats at a rate proportional to the residual.
  if (plan.uncertainty) {
    const { lowerHz, upperHz } = beatingFifthHz(2 * pitchHz, plan.uncertainty.beatHz);
    if (!generators.uncertainty) {
      const lower = ctx.createOscillator();
      const upper = ctx.createOscillator();
      const gain = ctx.createGain();
      lower.setPeriodicWave(uncertaintyWave);
      upper.setPeriodicWave(uncertaintyWave);
      setParamNow(lower.frequency, lowerHz, now);
      setParamNow(upper.frequency, upperHz, now);
      setParamNow(gain.gain, 0, now);
      glideParam(gain.gain, UNCERTAINTY_RELATIVE_GAIN, now, ATTACK_TIME_CONSTANT_SEC);
      lower.connect(gain);
      upper.connect(gain);
      gain.connect(generators.envelope);
      lower.start(now);
      upper.start(now);
      generators.uncertainty = { lower, upper, gain };
    } else {
      glideParam(generators.uncertainty.lower.frequency, lowerHz, now, PARAM_TIME_CONSTANT_SEC);
      glideParam(generators.uncertainty.upper.frequency, upperHz, now, PARAM_TIME_CONSTANT_SEC);
    }
  } else if (generators.uncertainty) {
    // Residual withdrawn: the layer goes silent rather than resolving to a clean fifth.
    const layer = generators.uncertainty;
    glideParam(layer.gain.gain, 0, now, RELEASE_TIME_CONSTANT_SEC);
    stopScheduledSource(layer.lower, now + RELEASE_STOP_AFTER_SEC, () => layer.gain.disconnect());
    stopScheduledSource(layer.upper, now + RELEASE_STOP_AFTER_SEC);
    generators.uncertainty = null;
  }
}
