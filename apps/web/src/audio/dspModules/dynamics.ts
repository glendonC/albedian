import type { Samples } from './types.js';

const DEFAULT_SOFT_CLIP_SAMPLES = 4097;
const MIN_SOFT_CLIP_SAMPLES = 3;
const DEFAULT_SOFT_CLIP_CEILING = 0.966;
const DEFAULT_SOFT_CLIP_KNEE_START = 0.75;

/**
 * WaveShaper curve that is the identity up to `kneeStart` and saturates smoothly (tanh) to
 * `ceiling`, so the output can never exceed `ceiling` whatever reaches it.
 */
export function softClipCurve(
  samples = DEFAULT_SOFT_CLIP_SAMPLES,
  ceiling = DEFAULT_SOFT_CLIP_CEILING,
  kneeStart = DEFAULT_SOFT_CLIP_KNEE_START
): Samples {
  const n = Math.max(MIN_SOFT_CLIP_SAMPLES, Math.floor(samples));
  const knee = Math.min(kneeStart, ceiling);
  const span = ceiling - knee;
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    const magnitude = Math.abs(x);
    const shaped =
      magnitude <= knee || span <= 0
        ? Math.min(magnitude, ceiling)
        : knee + span * Math.tanh((magnitude - knee) / span);
    curve[i] = Math.sign(x) * shaped;
  }
  return curve;
}

/**
 * Scales an impulse response so its mean channel energy (sum of squares) is 1. With unit
 * energy, the reverb return carries the same power as its send for broadband input, which
 * is what makes the direct-to-reverberant split in `directReverbGains` meaningful.
 */
export function energyNormalize(channels: readonly Float32Array[]): Samples[] {
  if (channels.length === 0) return [];
  let energy = 0;
  for (const channel of channels) {
    for (let i = 0; i < channel.length; i++) energy += (channel[i] ?? 0) ** 2;
  }
  const meanEnergy = energy / channels.length;
  const k = meanEnergy > 0 && Number.isFinite(meanEnergy) ? 1 / Math.sqrt(meanEnergy) : 0;
  return channels.map((channel) => {
    const out = new Float32Array(channel.length);
    for (let i = 0; i < channel.length; i++) out[i] = (channel[i] ?? 0) * k;
    return out;
  });
}
