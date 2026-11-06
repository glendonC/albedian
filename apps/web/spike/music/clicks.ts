/**
 * Click detection for rendered clips: the standard linear-prediction declicking detector.
 *
 * Per channel and per Hann-windowed frame, a linear predictor is fitted by the autocorrelation
 * method (Levinson-Durbin recursion) and the prediction residual is taken over the frame's central
 * half from the real preceding samples. A click is a residual peak that is large against the
 * frame's robust residual scale and impulsive: at least half of the residual energy within 64
 * samples of it lies within 2 samples. A step, a dropped or repeated sample, or a sound that starts
 * or stops without a fade leaves one or two such samples; a drum hit, a pluck, noise or a chord
 * spreads its residual over milliseconds and fails the impulsive test however loud it is.
 *
 * The robust scale is floored at 5e-4 of the frame's RMS level (-66 dB) as well as at 1e-7. On
 * clean synthetic tones the median residual sits near -100 dB, and beside digital silence it is
 * zero, so without that floor the curvature of a raised-cosine fade scores hundreds.
 *
 * Limits: the corner of a linear envelope segment is a slope change in one sample and leaves the
 * same residual as a dropped sample of that size, so on clean tones linear attacks of 5 ms or less
 * are flagged (raised-cosine attacks of 1 ms pass). A dropped or repeated sample at the crest of a
 * pure low tone only bends its slope and falls under the floor.
 */

export interface Click {
  readonly channel: number;
  /** Index of the sample where the residual peaks. */
  readonly sample: number;
  readonly timeSec: number;
  /** Peak |residual| over the frame's robust residual scale. */
  readonly strength: number;
}

export interface ClickOptions {
  /** Linear prediction order, default 24. */
  readonly order?: number;
  /** Analysis frame in samples (even, more than twice the order), default 1024; frames hop by half a frame. */
  readonly frame?: number;
  /** Candidate threshold in robust-scale units, default 10. */
  readonly threshold?: number;
  /** Most clicks listed, default 50; the count is always complete. */
  readonly maxReport?: number;
}

export interface ClickReport {
  /** Clicks in time order (channel order within a sample), at most maxReport of them. */
  readonly clicks: Click[];
  /** Every click found, listed or not. */
  readonly count: number;
  /** Analysis frames, summed over channels. */
  readonly framesChecked: number;
  /** One plain sentence naming the method and its parameters. */
  readonly method: string;
}

const DEFAULT_ORDER = 24;
const DEFAULT_FRAME = 1024;
// A step's own residual tail raises its frame's scale, so 0.05 to 0.3 steps on mixes peaking at
// 0.9 bottom out near strength 20, while click-free material tops out near 4.6 (a 1 ms
// raised-cosine kick attack); 10 sits between them with about twice the room on each side.
const DEFAULT_THRESHOLD = 10;
const DEFAULT_MAX_REPORT = 50;

const MAD_TO_SIGMA = 1.4826; // median absolute value to standard deviation for Gaussian residuals
const SCALE_FLOOR = 1e-7; // keeps digital silence from dividing by zero
const LEVEL_FLOOR = 5e-4; // scale floor as a fraction of the frame's RMS level (-66 dB)
const WHITE_NOISE_CORRECTION = 1e-9; // r[0] gain that keeps silence and pure tones stable
const NEAR = 2; // half-width of the window a click's energy must dominate
const FAR = 64; // half-width of the neighbourhood it is compared with
const MERGE = 32; // candidates closer than this are one click

interface Peak {
  sample: number;
  strength: number;
}

/**
 * Finds clicks in each channel; channels are analysed independently and may differ in length.
 * Throws a RangeError for an unusable option, a non-positive sample rate or a non-finite sample.
 */
export function detectClicks(
  channels: readonly ArrayLike<number>[],
  sampleRate: number,
  options: ClickOptions = {}
): ClickReport {
  const order = options.order ?? DEFAULT_ORDER;
  const frame = options.frame ?? DEFAULT_FRAME;
  const threshold = options.threshold ?? DEFAULT_THRESHOLD;
  const maxReport = options.maxReport ?? DEFAULT_MAX_REPORT;
  if (!(Number.isFinite(sampleRate) && sampleRate > 0)) throw new RangeError(`sampleRate must be positive, got ${sampleRate}`);
  if (!(Number.isInteger(order) && order >= 1)) throw new RangeError(`order must be a positive integer, got ${order}`);
  if (!(Number.isInteger(frame) && frame % 2 === 0 && frame > 2 * order)) {
    throw new RangeError(`frame must be an even integer above twice the order, got ${frame}`);
  }
  if (!(Number.isFinite(threshold) && threshold > 0)) throw new RangeError(`threshold must be positive, got ${threshold}`);
  if (!(Number.isInteger(maxReport) && maxReport >= 0)) throw new RangeError(`maxReport must be a whole number, got ${maxReport}`);

  const found: Click[] = [];
  let framesChecked = 0;
  channels.forEach((channel, index) => {
    const x = new Float64Array(channel.length);
    for (let i = 0; i < x.length; i++) {
      const value = channel[i];
      if (!Number.isFinite(value)) throw new RangeError(`channel ${index} sample ${i} is not a finite number`);
      x[i] = value as number;
    }
    const scan = scanChannel(x, order, frame, threshold);
    framesChecked += scan.frames;
    for (const peak of scan.peaks) {
      found.push({ channel: index, sample: peak.sample, timeSec: peak.sample / sampleRate, strength: peak.strength });
    }
  });
  found.sort((a, b) => a.sample - b.sample || a.channel - b.channel);

  const hop = frame / 2;
  const method =
    `Order-${order} linear prediction (autocorrelation method, Levinson-Durbin) on Hann-windowed ${frame}-sample frames ` +
    `with a ${hop}-sample hop, flagging residual peaks above ${threshold} times the frame's robust scale ` +
    `(1.4826 times the median absolute residual, floored at 5e-4 of the frame's RMS level and at 1e-7) ` +
    `that hold at least half the residual energy within 64 samples inside 2 samples, ` +
    `merging peaks closer than 32 samples and skipping the first ${order} samples.`;
  return { clicks: found.slice(0, maxReport), count: found.length, framesChecked, method };
}

function scanChannel(x: Float64Array, order: number, frame: number, threshold: number): { frames: number; peaks: Peak[] } {
  const n = x.length;
  if (n <= order) return { frames: 0, peaks: [] };

  // Frames start every hop; the last starts frame samples before the end. Each frame's checked
  // region is its central half, widened at the ends so the regions tile [order, n).
  const hop = frame / 2;
  const starts: number[] = [];
  if (n <= frame) starts.push(0);
  else {
    for (let start = 0; start + frame < n; start += hop) starts.push(start);
    starts.push(n - frame);
  }
  const reach = hop + Math.floor(hop / 2);
  const window = hann(Math.min(frame, n));
  const residual = new Float64Array(n);
  const scale = new Float64Array(n);
  let lo = order;
  starts.forEach((start, k) => {
    const hi = k === starts.length - 1 ? n : start + reach;
    const a = predictor(x, start, window, order);
    let power = 0;
    for (let i = start; i < start + window.length; i++) power += x[i]! * x[i]!;
    for (let i = lo; i < hi; i++) {
      let prediction = 0;
      for (let j = 0; j < order; j++) prediction += a[j]! * x[i - 1 - j]!;
      residual[i] = x[i]! - prediction;
    }
    const floor = Math.max(SCALE_FLOOR, LEVEL_FLOOR * Math.sqrt(power / window.length));
    scale.fill(robustScale(residual, lo, hi, floor), lo, hi);
    lo = hi;
  });

  const peaks: Peak[] = [];
  let lastCandidate = -Infinity;
  for (let i = order; i < n; i++) {
    const magnitude = Math.abs(residual[i]!);
    if (!(magnitude > threshold * scale[i]!) || !impulsive(residual, i, order)) continue;
    const strength = magnitude / scale[i]!;
    const last = peaks[peaks.length - 1];
    if (last && i - lastCandidate < MERGE) {
      if (strength > last.strength) {
        last.sample = i;
        last.strength = strength;
      }
    } else {
      peaks.push({ sample: i, strength });
    }
    lastCandidate = i;
  }
  return { frames: starts.length, peaks };
}

/** Symmetric Hann window. */
function hann(length: number): Float64Array {
  const w = new Float64Array(length);
  if (length === 1) w[0] = 1;
  for (let i = 0; i < length && length > 1; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (length - 1));
  return w;
}

/** Predictor coefficients a[0..order-1], where x[n] is predicted as the sum of a[j] x[n-1-j]. */
function predictor(x: Float64Array, start: number, window: Float64Array, order: number): Float64Array {
  const length = window.length;
  const y = new Float64Array(length);
  for (let i = 0; i < length; i++) y[i] = x[start + i]! * window[i]!;
  const r = new Float64Array(order + 1);
  for (let lag = 0; lag <= order; lag++) {
    let sum = 0;
    for (let i = lag; i < length; i++) sum += y[i]! * y[i - lag]!;
    r[lag] = sum;
  }
  r[0] = r[0]! * (1 + WHITE_NOISE_CORRECTION);
  return levinson(r, order);
}

/** Levinson-Durbin recursion; stops early (keeping the lower order) if the recursion turns unstable. */
function levinson(r: Float64Array, order: number): Float64Array {
  const a = new Float64Array(order);
  const previous = new Float64Array(order);
  let error = r[0]!;
  if (!(error > 0)) return a;
  for (let i = 0; i < order; i++) {
    let acc = r[i + 1]!;
    for (let j = 0; j < i; j++) acc -= a[j]! * r[i - j]!;
    const k = acc / error;
    if (!(Math.abs(k) < 1)) break;
    previous.set(a);
    a[i] = k;
    for (let j = 0; j < i; j++) a[j] = previous[j]! - k * previous[i - 1 - j]!;
    error *= 1 - k * k;
  }
  return a;
}

/** 1.4826 times the median |residual| over [lo, hi), but not below `floor`. */
function robustScale(residual: Float64Array, lo: number, hi: number, floor: number): number {
  const magnitudes = new Float64Array(hi - lo);
  for (let i = lo; i < hi; i++) magnitudes[i - lo] = Math.abs(residual[i]!);
  magnitudes.sort();
  const mid = magnitudes.length >> 1;
  const median = magnitudes.length % 2 === 1 ? magnitudes[mid]! : (magnitudes[mid - 1]! + magnitudes[mid]!) / 2;
  return Math.max(floor, MAD_TO_SIGMA * median);
}

/** True when the residual energy within NEAR samples of i is at least half of that within FAR. */
function impulsive(residual: Float64Array, i: number, first: number): boolean {
  let near = 0;
  let far = 0;
  const to = Math.min(residual.length - 1, i + FAR);
  for (let j = Math.max(first, i - FAR); j <= to; j++) {
    const energy = residual[j]! * residual[j]!;
    far += energy;
    if (j >= i - NEAR && j <= i + NEAR) near += energy;
  }
  return near >= 0.5 * far;
}
