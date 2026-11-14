import type { Samples } from './types.js';

const MULBERRY32_INCREMENT = 0x6d2b79f5;
const UINT32_RADIX = 4294967296;
const FOA_CHANNEL_SEED_STRIDE = 7919;
const BINAURAL_LEFT_SEED_OFFSET = 104729;
const BINAURAL_RIGHT_SEED_OFFSET = 1299709;
const DEFAULT_BINAURAL_CROSSOVER_HZ = 700;
const FIRST_ORDER_DIFFUSE_WEIGHT = 1 / Math.sqrt(3);

export const NOISE_TARGET_RMS = 0.2;

/**
 * Diffuse-field weights for first-order ambisonics in ACN order (W, Y, Z, X) with SN3D
 * normalization: for plane waves from uniformly random directions the first-order channels
 * each carry one third of W's power and all four are mutually uncorrelated.
 */
export const DIFFUSE_FIELD_SN3D_WEIGHTS: readonly [number, number, number, number] = [
  1,
  FIRST_ORDER_DIFFUSE_WEIGHT,
  FIRST_ORDER_DIFFUSE_WEIGHT,
  FIRST_ORDER_DIFFUSE_WEIGHT,
];

/** Mulberry32: small seeded PRNG so beds are reproducible run to run. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + MULBERRY32_INCREMENT) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / UINT32_RADIX;
  };
}

/** Scales to `targetRms` and hard-limits to [-1, 1] (at RMS 0.2 the limit is about 5 sigma). */
function normalizeRms(samples: Samples, targetRms: number): Samples {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += (samples[i] ?? 0) ** 2;
  const rms = Math.sqrt(sum / Math.max(1, samples.length));
  const k = rms > 0 ? targetRms / rms : 0;
  for (let i = 0; i < samples.length; i++) {
    const v = (samples[i] ?? 0) * k;
    samples[i] = v > 1 ? 1 : v < -1 ? -1 : v;
  }
  return samples;
}

export function whiteNoise(length: number, seed: number, targetRms = NOISE_TARGET_RMS): Samples {
  const random = mulberry32(seed);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) out[i] = random() * 2 - 1;
  return normalizeRms(out, targetRms);
}

/** Pink (-3 dB per octave) noise using Paul Kellet's refined filter on seeded white noise. */
export function pinkNoise(length: number, seed: number, targetRms = NOISE_TARGET_RMS): Samples {
  const random = mulberry32(seed);
  const out = new Float32Array(length);
  let b0 = 0;
  let b1 = 0;
  let b2 = 0;
  let b3 = 0;
  let b4 = 0;
  let b5 = 0;
  let b6 = 0;
  for (let i = 0; i < length; i++) {
    const white = random() * 2 - 1;
    b0 = 0.99886 * b0 + white * 0.0555179;
    b1 = 0.99332 * b1 + white * 0.0750759;
    b2 = 0.969 * b2 + white * 0.153852;
    b3 = 0.8665 * b3 + white * 0.3104856;
    b4 = 0.55 * b4 + white * 0.5329522;
    b5 = -0.7616 * b5 - white * 0.016898;
    out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362;
    b6 = white * 0.115926;
  }
  return normalizeRms(out, targetRms);
}

/** Four independent pink-noise channels weighted as an isotropic diffuse field (ACN/SN3D). */
export function diffuseFoaNoise(length: number, seed: number): Samples[] {
  return DIFFUSE_FIELD_SN3D_WEIGHTS.map((weight, channel) => {
    const noise = pinkNoise(length, seed + FOA_CHANNEL_SEED_STRIDE * (channel + 1));
    for (let i = 0; i < noise.length; i++) noise[i] = (noise[i] ?? 0) * weight;
    return noise;
  });
}

/**
 * Two-channel diffuse noise without HRTFs: a shared component below `crossoverHz` (where the
 * ears of a real diffuse field are highly coherent) plus independent components above it.
 * The complementary one-pole split sums back to the input exactly.
 */
export function binauralDiffuseNoise(
  length: number,
  sampleRate: number,
  seed: number,
  crossoverHz = DEFAULT_BINAURAL_CROSSOVER_HZ
): [Samples, Samples] {
  const common = pinkNoise(length, seed);
  const left = pinkNoise(length, seed + BINAURAL_LEFT_SEED_OFFSET);
  const right = pinkNoise(length, seed + BINAURAL_RIGHT_SEED_OFFSET);
  const a = Math.exp((-2 * Math.PI * crossoverHz) / sampleRate);
  let lowCommon = 0;
  let lowLeft = 0;
  let lowRight = 0;
  for (let i = 0; i < length; i++) {
    lowCommon = (1 - a) * (common[i] ?? 0) + a * lowCommon;
    lowLeft = (1 - a) * (left[i] ?? 0) + a * lowLeft;
    lowRight = (1 - a) * (right[i] ?? 0) + a * lowRight;
    left[i] = lowCommon + ((left[i] ?? 0) - lowLeft);
    right[i] = lowCommon + ((right[i] ?? 0) - lowRight);
  }
  return [normalizeRms(left, NOISE_TARGET_RMS), normalizeRms(right, NOISE_TARGET_RMS)];
}
