/**
 * Pure signal helpers for the audio engine: deterministic noise for the skyglow bed, the
 * master soft-clip curve, low-crest-factor harmonic phases, PeriodicWave coefficients, the
 * pulsar pulse-train series and impulse-response energy normalization. No Web Audio here.
 */

/** Sample arrays backed by a plain ArrayBuffer, as Web Audio's typed signatures require. */
export type Samples = Float32Array<ArrayBuffer>;

/** Mulberry32: small seeded PRNG so beds are reproducible run to run. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const NOISE_TARGET_RMS = 0.2;

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

/**
 * Diffuse-field weights for first-order ambisonics in ACN order (W, Y, Z, X) with SN3D
 * normalization: for plane waves from uniformly random directions the first-order channels
 * each carry one third of W's power and all four are mutually uncorrelated.
 */
export const DIFFUSE_FIELD_SN3D_WEIGHTS: readonly [number, number, number, number] = [
  1,
  1 / Math.sqrt(3),
  1 / Math.sqrt(3),
  1 / Math.sqrt(3),
];

/** Four independent pink-noise channels weighted as an isotropic diffuse field (ACN/SN3D). */
export function diffuseFoaNoise(length: number, seed: number): Samples[] {
  return DIFFUSE_FIELD_SN3D_WEIGHTS.map((weight, channel) => {
    const noise = pinkNoise(length, seed + 7919 * (channel + 1));
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
  crossoverHz = 700
): [Samples, Samples] {
  const common = pinkNoise(length, seed);
  const left = pinkNoise(length, seed + 104729);
  const right = pinkNoise(length, seed + 1299709);
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

/**
 * WaveShaper curve that is the identity up to `kneeStart` and saturates smoothly (tanh) to
 * `ceiling`, so the output can never exceed `ceiling` whatever reaches it.
 */
export function softClipCurve(samples = 4097, ceiling = 0.966, kneeStart = 0.75): Samples {
  const n = Math.max(3, Math.floor(samples));
  const knee = Math.min(kneeStart, ceiling);
  const span = ceiling - knee;
  const curve = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const x = (i / (n - 1)) * 2 - 1;
    const magnitude = Math.abs(x);
    const shaped = magnitude <= knee || span <= 0 ? Math.min(magnitude, ceiling) : knee + span * Math.tanh((magnitude - knee) / span);
    curve[i] = Math.sign(x) * shaped;
  }
  return curve;
}

/**
 * Schroeder (1970) phases for a harmonic tone with the given relative powers:
 * phi_n = -2 pi sum_{l<n} (n - l) p_l, with p normalized to sum to 1. They keep the crest
 * factor low, which leaves headroom before the limiter.
 */
export function schroederPhases(powers: ArrayLike<number>): Float64Array {
  let total = 0;
  for (let i = 0; i < powers.length; i++) total += Math.max(0, powers[i] ?? 0);
  const phases = new Float64Array(powers.length);
  if (!(total > 0)) return phases;
  let sumP = 0; // sum of p_l for l < n
  let sumLP = 0; // sum of l * p_l for l < n
  for (let index = 0; index < powers.length; index++) {
    const n = index + 1;
    phases[index] = -2 * Math.PI * (n * sumP - sumLP);
    const p = Math.max(0, powers[index] ?? 0) / total;
    sumP += p;
    sumLP += n * p;
  }
  return phases;
}

/**
 * Coefficients for `createPeriodicWave` from harmonic amplitudes and phases, for
 * x(t) = sum a_n cos(n w t + phi_n). Index 0 (DC) is zero; Web Audio ignores it anyway.
 */
export function periodicWaveCoefficients(
  amplitudes: ArrayLike<number>,
  phases?: ArrayLike<number>
): { real: Samples; imag: Samples } {
  const real = new Float32Array(amplitudes.length + 1);
  const imag = new Float32Array(amplitudes.length + 1);
  for (let i = 0; i < amplitudes.length; i++) {
    const a = amplitudes[i] ?? 0;
    const phi = phases ? (phases[i] ?? 0) : 0;
    real[i + 1] = a * Math.cos(phi);
    imag[i + 1] = -a * Math.sin(phi);
  }
  return { real, imag };
}

/**
 * Fourier series of a periodic Gaussian pulse whose full width at half maximum is
 * `fwhmFraction` of the period, scaled so the pulse peak is about 1:
 * e(t) = dc + sum cosine[n-1] cos(2 pi n t / P). The pulse shape is generic; only the rate
 * comes from data.
 */
export function gaussianPulseTrainSeries(
  fwhmFraction: number,
  maxHarmonics = 512
): { dc: number; cosine: Float64Array } {
  const fwhm = Math.min(0.5, Math.max(1e-4, fwhmFraction));
  const sigma = fwhm / (2 * Math.sqrt(2 * Math.LN2));
  const count = Math.max(1, Math.min(maxHarmonics, Math.ceil(0.75 / sigma)));
  const base = sigma * Math.sqrt(2 * Math.PI);
  const cosine = new Float64Array(count);
  for (let n = 1; n <= count; n++) {
    cosine[n - 1] = 2 * base * Math.exp(-2 * Math.PI * Math.PI * n * n * sigma * sigma);
  }
  return { dc: base, cosine };
}

/** Evaluates the series above at phase `t` (in periods); used by tests and meters. */
export function evaluatePulseTrain(series: { dc: number; cosine: ArrayLike<number> }, t: number): number {
  let value = series.dc;
  for (let i = 0; i < series.cosine.length; i++) value += (series.cosine[i] ?? 0) * Math.cos(2 * Math.PI * (i + 1) * t);
  return value;
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

const twiddleCache = new Map<number, { cos: Float64Array; sin: Float64Array }>();

function twiddles(n: number): { cos: Float64Array; sin: Float64Array } {
  let t = twiddleCache.get(n);
  if (!t) {
    const cos = new Float64Array(n / 2);
    const sin = new Float64Array(n / 2);
    for (let k = 0; k < n / 2; k++) {
      cos[k] = Math.cos((2 * Math.PI * k) / n);
      sin[k] = -Math.sin((2 * Math.PI * k) / n);
    }
    t = { cos, sin };
    twiddleCache.set(n, t);
  }
  return t;
}

/** In-place iterative radix-2 FFT (forward, unscaled) of a complex sequence of power-of-two length. */
export function fft(re: Float64Array, im: Float64Array): void {
  const n = re.length;
  if (n !== im.length || n < 2 || (n & (n - 1)) !== 0) throw new Error(`FFT length must be a power of two, got ${n}`);
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j]!, re[i]!];
      [im[i], im[j]] = [im[j]!, im[i]!];
    }
  }
  const { cos, sin } = twiddles(n);
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1;
    const step = n / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0; k < half; k++) {
        const wr = cos[k * step]!;
        const wi = sin[k * step]!;
        const a = start + k;
        const b = a + half;
        const tr = re[b]! * wr - im[b]! * wi;
        const ti = re[b]! * wi + im[b]! * wr;
        re[b] = re[a]! - tr;
        im[b] = im[a]! - ti;
        re[a] = re[a]! + tr;
        im[a] = im[a]! + ti;
      }
    }
  }
}
