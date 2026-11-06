/**
 * Tempo, beat grid, key and per-bar chords measured from a recording's samples, for the music
 * spike. Notes added over a recording must fall on its beat grid and inside its chords; where a
 * bar's chord cannot be measured with confidence the caller falls back to the key's pentatonic
 * notes, so every bar reports how sure its chord is. Pure functions on mono samples in [-1, 1],
 * with no Web Audio, so they run in the page and in tests alike.
 */
import { fft } from '../listening/spectrum';

type Samples = ArrayLike<number>;

const PITCH_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'] as const;

// Onset STFT: a periodic Hann window of the power of two nearest 46.4 ms, hopped by a quarter of
// it. That is 1024 and 256 samples at 22.05 kHz and 2048 and 512 at 44.1 kHz (46.4 ms and 11.6 ms
// at both), and 2048 and 512 at 48 kHz (42.7 ms and 10.7 ms).
const ONSET_WINDOW_SEC = 0.0464;
/** The span of the local mean taken off the flux, centred on each frame. */
const ONSET_MEAN_SEC = 0.5;
/** estimateTempo averages the autocorrelation over windows this long (see there). */
const TEMPO_WINDOW_SEC = 8;
/** The standard deviation of the Gaussian that smooths the autocorrelation in estimateTempo. */
const TEMPO_SMOOTHING_SEC = 0.03;
/**
 * The log-magnitude floor, as a fraction (-60 dB) of the magnitude that white noise at the signal's
 * RMS gives a bin. It scales with the signal, so the envelope does not depend on the gain.
 */
const ONSET_FLOOR = 1e-3;

// Chroma STFT: a periodic Hann window of the power of two nearest 186 ms, hopped by a quarter of
// it: 4096 and 1024 samples at 22.05 kHz and 8192 and 2048 at 44.1 kHz (5.4 Hz bins every 46 ms at
// both), 8192 and 2048 at 48 kHz (5.9 Hz bins every 43 ms).
const CHROMA_WINDOW_SEC = 0.1858;
const CHROMA_MIN_HZ = 55;
const CHROMA_MAX_HZ = 2000;

// The comb in beatGrid searches beat periods within 1 percent of the autocorrelation's, in steps
// of 0.02 percent, and phases in steps of a quarter frame (2.9 ms at 11.6 ms frames).
const COMB_PERIOD_SPAN = 0.01;
const COMB_PERIOD_STEPS = 50;
const COMB_PHASE_STEP = 0.25;

// Confidence defaults for barChords, each midway between what the tests' signals gave. Measured on
// the test file's generators: 240 bars of the Am F C G progression (30 seeds and offsets, on the
// true grid) all named right, with scores from 0.753 and margins from 0.077; 1200 bars of white
// noise (random seeds and levels) scored at most 0.585 with margins at most 0.038; a bar of C and
// E alone, which C major and A minor share, has margin 0. Untested on a real mix (inference: its
// scores will run lower, as a loud bass note dominates power chroma and lifts every triad holding it).
const DEFAULT_MIN_SCORE = 0.67;
const DEFAULT_MIN_MARGIN = 0.055;

/** Krumhansl and Kessler (1982) probe-tone ratings, from the tonic up by semitone. */
const KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88] as const;
const KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17] as const;

/** The 24 major and minor triads: C to B major, then C minor to B minor. */
const TRIADS = Array.from({ length: 24 }, (_, i) => {
  const root = i % 12;
  const minor = i >= 12;
  return {
    name: `${PITCH_NAMES[root]}${minor ? 'm' : ''}`,
    pitchClasses: [root, (root + (minor ? 3 : 4)) % 12, (root + 7) % 12] as const,
  };
});

function checkSampleRate(sampleRate: number): void {
  if (!(sampleRate > 0) || !Number.isFinite(sampleRate)) throw new Error(`sample rate must be positive and finite, got ${sampleRate}`);
}

/** The power of two nearest (on a log scale) to `seconds` of samples, at least 16. */
function windowLength(seconds: number, sampleRate: number): number {
  return 2 ** Math.max(4, Math.round(Math.log2(seconds * sampleRate)));
}

const hannCache = new Map<number, Float64Array>();

/** A periodic Hann window. */
function hann(n: number): Float64Array {
  let window = hannCache.get(n);
  if (!window) {
    window = new Float64Array(n);
    for (let i = 0; i < n; i++) window[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n);
    hannCache.set(n, window);
  }
  return window;
}

/**
 * Calls `visit(m, power)` for frames m = first to end - 1, in order, with the power of bins `fromBin`
 * to `toBin` of the windowed samples from `frameStart(m)` (zero outside the signal). `power` is
 * reused, so `visit` must read it at once. Two real frames share one complex FFT, one in the real
 * part and one in the imaginary part, and are separated by the conjugate symmetry of real spectra.
 */
function powerSpectra(
  mono: Samples,
  window: Float64Array,
  frameStart: (m: number) => number,
  first: number,
  end: number,
  fromBin: number,
  toBin: number,
  visit: (m: number, power: Float64Array) => void
): void {
  const n = window.length;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  const powerA = new Float64Array(n / 2 + 1);
  const powerB = new Float64Array(n / 2 + 1);
  for (let m = first; m < end; m += 2) {
    const paired = m + 1 < end;
    const startA = frameStart(m);
    const startB = paired ? frameStart(m + 1) : 0;
    for (let i = 0; i < n; i++) {
      const a = startA + i;
      const b = startB + i;
      re[i] = a >= 0 && a < mono.length ? mono[a]! * window[i]! : 0;
      im[i] = paired && b >= 0 && b < mono.length ? mono[b]! * window[i]! : 0;
    }
    fft(re, im);
    for (let k = fromBin; k <= toBin; k++) {
      const j = (n - k) & (n - 1);
      const sumRe = re[k]! + re[j]!;
      const diffRe = re[k]! - re[j]!;
      const sumIm = im[k]! + im[j]!;
      const diffIm = im[k]! - im[j]!;
      powerA[k] = 0.25 * (sumRe * sumRe + diffIm * diffIm);
      powerB[k] = 0.25 * (sumIm * sumIm + diffRe * diffRe);
    }
    visit(m, powerA);
    if (paired) visit(m + 1, powerB);
  }
}

/** The onset strength over time; frame m stands for the time m / framesPerSecond. */
export interface OnsetEnvelope {
  readonly values: Float64Array;
  readonly framesPerSecond: number;
}

/**
 * Log-magnitude spectral flux: each frame's rises in log magnitude over the previous frame, summed
 * over all bins, minus the mean over the surrounding 0.5 s, floored at 0. Frame m's window ends half
 * a hop after m / framesPerSecond, so its flux reports what the newest hop of samples added, and a
 * frame before the signal counts as silence. Silence gives an envelope of zeros.
 */
export function onsetEnvelope(mono: Samples, sampleRate: number): OnsetEnvelope {
  checkSampleRate(sampleRate);
  const n = windowLength(ONSET_WINDOW_SEC, sampleRate);
  const hop = n / 4;
  const framesPerSecond = sampleRate / hop;
  const count = Math.floor(mono.length / hop) + 1;
  const values = new Float64Array(count);
  const window = hann(n);
  let energy = 0;
  for (let i = 0; i < mono.length; i++) energy += mono[i]! * mono[i]!;
  let windowEnergy = 0;
  for (let i = 0; i < n; i++) windowEnergy += window[i]! * window[i]!;
  const floor = ONSET_FLOOR * Math.sqrt((energy / Math.max(1, mono.length)) * windowEnergy);
  if (!(floor > 0)) return { values, framesPerSecond };

  const bins = n / 2 + 1;
  let previous = new Float64Array(bins).fill(Math.log(floor));
  let current = new Float64Array(bins);
  const flux = new Float64Array(count);
  powerSpectra(mono, window, (m) => m * hop + hop / 2 - n, 0, count, 0, n / 2, (m, power) => {
    let rise = 0;
    for (let k = 0; k < bins; k++) {
      const level = Math.log(Math.sqrt(power[k]!) + floor);
      if (level > previous[k]!) rise += level - previous[k]!;
      current[k] = level;
    }
    flux[m] = rise;
    [previous, current] = [current, previous];
  });

  const half = Math.max(1, Math.round((ONSET_MEAN_SEC * framesPerSecond) / 2));
  const prefix = new Float64Array(count + 1);
  for (let m = 0; m < count; m++) prefix[m + 1] = prefix[m]! + flux[m]!;
  for (let m = 0; m < count; m++) {
    const lo = Math.max(0, m - half);
    const hi = Math.min(count, m + half + 1);
    values[m] = Math.max(0, flux[m]! - (prefix[hi]! - prefix[lo]!) / (hi - lo));
  }
  return { values, framesPerSecond };
}

export interface TempoEstimate {
  readonly bpm: number;
  /** The (local, smoothed) autocorrelation at the chosen lag over its value at lag 0; near 1 for a strictly periodic envelope. */
  readonly strength: number;
}

/**
 * The tempo whose beat period best repeats the envelope. The autocorrelation of the mean-removed
 * envelope is taken as its average over 8-second windows: the mean product over the overlap, times
 * 8 s less the lag. A strictly periodic envelope repeats at every multiple of its period too, and
 * this weighting favours the period over its double by T / (8 s - 2T), 5 to 9 percent from 160 to
 * 100 BPM, at any recording length. It is smoothed by a 30 ms Gaussian (the autocorrelation of the
 * envelope smoothed by 21 ms), so a period that falls between frames keeps its full peak. Its local
 * maxima between the lags of `maxBpm` and `minBpm` are refined by parabolic interpolation and the
 * highest is taken; strength is that peak over the value at lag 0. Inference, untested: the same
 * weighting favours a half beat over the beat, so a recording whose off-beats repeat nearly as
 * strongly as its beats can read at double tempo, which `maxBpm` bounds. Throws for an envelope
 * with no variation, or one shorter than two periods of `minBpm`.
 */
export function estimateTempo(envelope: OnsetEnvelope, minBpm = 50, maxBpm = 180): TempoEstimate {
  const { values, framesPerSecond } = envelope;
  if (!(minBpm > 0 && maxBpm > minBpm && Number.isFinite(maxBpm))) throw new Error(`need 0 < minBpm < maxBpm, got ${minBpm} and ${maxBpm}`);
  const shortest = (60 * framesPerSecond) / maxBpm;
  const longest = (60 * framesPerSecond) / minBpm;
  const lo = Math.max(1, Math.ceil(shortest));
  const hi = Math.floor(longest);
  if (hi < lo) throw new Error(`no whole lag lies between ${minBpm} and ${maxBpm} BPM at ${framesPerSecond} frames per second`);
  const needed = 2 * (hi + 1);
  if (values.length < needed) throw new Error(`the envelope holds ${values.length} frames; tempos down to ${minBpm} BPM need ${needed}`);

  const count = values.length;
  let mean = 0;
  let squares = 0;
  for (let i = 0; i < count; i++) {
    mean += values[i]!;
    squares += values[i]! * values[i]!;
  }
  mean /= count;
  const x = new Float64Array(count);
  for (let i = 0; i < count; i++) x[i] = values[i]! - mean;
  const sigma = TEMPO_SMOOTHING_SEC * framesPerSecond;
  const radius = Math.ceil(3 * sigma);
  const span = Math.min(count, TEMPO_WINDOW_SEC * framesPerSecond);
  const local = new Float64Array(hi + 2 + radius);
  for (let lag = 0; lag < local.length && lag < count; lag++) {
    let sum = 0;
    for (let i = 0; i + lag < count; i++) sum += x[i]! * x[i + lag]!;
    if (lag === 0 && !(sum > 1e-12 * squares)) throw new Error('the envelope has no variation, so it holds no tempo');
    local[lag] = (sum / (count - lag)) * Math.max(0, span - lag);
  }
  const kernel = Array.from({ length: 2 * radius + 1 }, (_, j) => Math.exp(-0.5 * ((j - radius) / sigma) ** 2));
  const kernelSum = kernel.reduce((a, b) => a + b, 0);
  const r = new Float64Array(hi + 2);
  for (let lag = 0; lag <= hi + 1; lag++) {
    let sum = 0;
    for (let j = -radius; j <= radius; j++) sum += kernel[j + radius]! * local[Math.abs(lag + j)]!;
    r[lag] = sum / kernelSum;
  }

  let bestLag = Number.NaN;
  let bestValue = -Infinity;
  for (let lag = lo; lag <= hi; lag++) {
    const a = r[lag - 1]!;
    const b = r[lag]!;
    const c = r[lag + 1]!;
    if (!(b >= a && b > c)) continue;
    const offset = (0.5 * (a - c)) / (a - 2 * b + c);
    const value = b - 0.25 * (a - c) * offset;
    if (value > bestValue) {
      bestValue = value;
      bestLag = Math.min(longest, Math.max(shortest, lag + offset));
    }
  }
  if (Number.isNaN(bestLag)) {
    for (let lag = lo; lag <= hi; lag++) {
      if (r[lag]! > bestValue) {
        bestValue = r[lag]!;
        bestLag = lag;
      }
    }
  }
  return { bpm: (60 * framesPerSecond) / bestLag, strength: Math.max(-1, Math.min(1, bestValue / r[0]!)) };
}

export interface BeatGrid {
  readonly bpm: number;
  readonly beatSec: number;
  /** The earliest beat at or after 0 s, in [0, beatSec). */
  readonly firstBeatSec: number;
  readonly beatsPerBar: number;
  /** The earliest downbeat at or after 0 s: firstBeatSec plus a whole number of beats, under one bar. */
  readonly firstDownbeatSec: number;
  /** The tempo estimate's strength. */
  readonly strength: number;
}

/** The mean envelope at `phase + k * period` frames (linear interpolation), over the beats inside it. */
function combMean(values: Float64Array, period: number, phase: number): number {
  const last = values.length - 1;
  let sum = 0;
  let count = 0;
  for (let k = 0; ; k++) {
    const t = phase + k * period;
    if (t > last) break;
    const i = Math.floor(t);
    const f = t - i;
    sum += i < last ? values[i]! * (1 - f) + values[i + 1]! * f : values[i]!;
    count++;
  }
  return count > 0 ? sum / count : 0;
}

/**
 * The comb: the beat period and phase (in frames) whose beat times hold the most envelope on
 * average. The period is searched within 1 percent of the autocorrelation's because over a long
 * recording a small period error moves the late beats (0.1 percent is 30 ms after 30 s).
 */
function fitComb(values: Float64Array, period: number): { period: number; phase: number } {
  let best = { period, phase: 0, mean: -Infinity };
  for (let s = -COMB_PERIOD_STEPS; s <= COMB_PERIOD_STEPS; s++) {
    const candidate = period * (1 + (s / COMB_PERIOD_STEPS) * COMB_PERIOD_SPAN);
    for (let phase = 0; phase < candidate; phase += COMB_PHASE_STEP) {
      const mean = combMean(values, candidate, phase);
      if (mean > best.mean) best = { period: candidate, phase, mean };
    }
  }
  return best;
}

/**
 * The beat grid of a recording at a steady tempo. The tempo comes from estimateTempo and is refined
 * with the beat phase by a comb: the period (within 1 percent) and the offset in [0, beatSec) that
 * maximize the mean envelope at the beat times. The downbeat is the one of the `beatsPerBar` (default
 * 4) candidate bar phases whose bar boundaries show the most chroma change: the mean, over its
 * boundaries, of the distance between the unit-length mean chroma of the beat before and of the beat
 * after each one.
 */
export function beatGrid(mono: Samples, sampleRate: number, options: { beatsPerBar?: number; minBpm?: number; maxBpm?: number } = {}): BeatGrid {
  const beatsPerBar = options.beatsPerBar ?? 4;
  if (!Number.isInteger(beatsPerBar) || beatsPerBar < 1) throw new Error(`beats per bar must be a whole number from 1, got ${beatsPerBar}`);
  const envelope = onsetEnvelope(mono, sampleRate);
  const tempo = estimateTempo(envelope, options.minBpm, options.maxBpm);
  const comb = fitComb(envelope.values, (60 * envelope.framesPerSecond) / tempo.bpm);
  const beatSec = comb.period / envelope.framesPerSecond;
  const firstBeatSec = comb.phase / envelope.framesPerSecond;
  const offset = beatsPerBar > 1 ? downbeatOffset(mono, sampleRate, firstBeatSec, beatSec, beatsPerBar) : 0;
  return { bpm: 60 / beatSec, beatSec, firstBeatSec, beatsPerBar, firstDownbeatSec: firstBeatSec + offset * beatSec, strength: tempo.strength };
}

/** How many beats after the first beat the first downbeat falls (see beatGrid). */
function downbeatOffset(mono: Samples, sampleRate: number, firstBeatSec: number, beatSec: number, beatsPerBar: number): number {
  const chroma = chromaSpan(mono, sampleRate, 0, chromaFrameCount(mono.length, sampleRate));
  const duration = mono.length / sampleRate;
  let best = 0;
  let bestChange = -Infinity;
  for (let offset = 0; offset < beatsPerBar; offset++) {
    let total = 0;
    let count = 0;
    for (let bar = 0; ; bar++) {
      const boundary = firstBeatSec + (offset + bar * beatsPerBar) * beatSec;
      if (boundary + beatSec > duration) break;
      if (boundary - beatSec < 0) continue;
      const before = unit(meanChroma(chroma, boundary - beatSec, boundary));
      const after = unit(meanChroma(chroma, boundary, boundary + beatSec));
      let squared = 0;
      for (let pc = 0; pc < 12; pc++) squared += (before[pc]! - after[pc]!) ** 2;
      total += Math.sqrt(squared);
      count++;
    }
    const change = count > 0 ? total / count : 0;
    if (change > bestChange) {
      bestChange = change;
      best = offset;
    }
  }
  return best;
}

/** One 12-element vector per frame (index 0 = C); frame m is centred on m / framesPerSecond. */
export interface ChromaFrames {
  readonly frames: Float64Array[];
  readonly framesPerSecond: number;
}

/** Chroma frames `first` to `end` (exclusive), with what meanChroma needs to place them. */
interface ChromaSpan {
  readonly frames: Float64Array[];
  readonly first: number;
  readonly framesPerSecond: number;
  readonly halfWindowSec: number;
}

function chromaFrameCount(length: number, sampleRate: number): number {
  return Math.floor(length / (windowLength(CHROMA_WINDOW_SEC, sampleRate) / 4)) + 1;
}

function chromaSpan(mono: Samples, sampleRate: number, first: number, end: number): ChromaSpan {
  checkSampleRate(sampleRate);
  const n = windowLength(CHROMA_WINDOW_SEC, sampleRate);
  const hop = n / 4;
  const window = hann(n);
  const binHz = sampleRate / n;
  const lowest = Math.max(1, Math.floor(CHROMA_MIN_HZ / binHz));
  const highest = Math.min(n / 2 - 1, Math.ceil(CHROMA_MAX_HZ / binHz));
  const frames: Float64Array[] = [];
  powerSpectra(mono, window, (m) => m * hop - n / 2, first, end, lowest - 1, highest + 1, (_, power) => {
    const chroma = new Float64Array(12);
    for (let k = lowest; k <= highest; k++) {
      const a = power[k - 1]!;
      const b = power[k]!;
      const c = power[k + 1]!;
      if (!(b > a && b >= c)) continue;
      const la = Math.log(a || Number.MIN_VALUE);
      const lb = Math.log(b);
      const lc = Math.log(c || Number.MIN_VALUE);
      const hz = (k + (0.5 * (la - lc)) / (la - 2 * lb + lc)) * binHz;
      if (hz < CHROMA_MIN_HZ || hz > CHROMA_MAX_HZ) continue;
      const pc = ((Math.round(69 + 12 * Math.log2(hz / 440)) % 12) + 12) % 12;
      chroma[pc] = chroma[pc]! + a + b + c;
    }
    frames.push(chroma);
  });
  return { frames, first, framesPerSecond: sampleRate / hop, halfWindowSec: n / 2 / sampleRate };
}

/**
 * Chroma: in each frame, every local maximum of the STFT power from 55 Hz to 2 kHz adds its power
 * (its bin and both neighbours) to the pitch class of its frequency, interpolated by a parabola
 * through the log powers, in equal temperament with A4 = 440 Hz. Taking the peaks rather than every
 * bin keeps a low note's main lobe, wider than a semitone below about 110 Hz, out of the
 * neighbouring pitch classes.
 */
export function chromaFrames(mono: Samples, sampleRate: number): ChromaFrames {
  const span = chromaSpan(mono, sampleRate, 0, chromaFrameCount(mono.length, sampleRate));
  return { frames: span.frames, framesPerSecond: span.framesPerSecond };
}

/**
 * The mean chroma over [startSec, endSec): of the frames whose whole window lies inside it, or,
 * when none does, of the frames centred inside it. Null when no frame qualifies.
 */
function meanChroma(span: ChromaSpan, startSec: number, endSec: number): Float64Array | null {
  const fps = span.framesPerSecond;
  let lo = Math.ceil((startSec + span.halfWindowSec) * fps - 1e-9);
  let hi = Math.floor((endSec - span.halfWindowSec) * fps + 1e-9);
  if (hi < lo) {
    lo = Math.ceil(startSec * fps - 1e-9);
    hi = Math.ceil(endSec * fps - 1e-9) - 1;
  }
  lo = Math.max(lo, span.first);
  hi = Math.min(hi, span.first + span.frames.length - 1);
  if (hi < lo) return null;
  const mean = new Float64Array(12);
  for (let m = lo; m <= hi; m++) {
    const frame = span.frames[m - span.first]!;
    for (let pc = 0; pc < 12; pc++) mean[pc] = mean[pc]! + frame[pc]!;
  }
  for (let pc = 0; pc < 12; pc++) mean[pc] = mean[pc]! / (hi - lo + 1);
  return mean;
}

/** The vector scaled to unit length; a zero vector (or none) stays zero. */
function unit(vector: Float64Array | null): Float64Array {
  const out = new Float64Array(12);
  if (!vector) return out;
  let norm = 0;
  for (let pc = 0; pc < 12; pc++) norm += vector[pc]! * vector[pc]!;
  norm = Math.sqrt(norm);
  if (norm > 0) for (let pc = 0; pc < 12; pc++) out[pc] = vector[pc]! / norm;
  return out;
}

export interface KeyEstimate {
  readonly tonic: number;
  readonly mode: 'major' | 'minor';
  readonly name: string;
  readonly correlation: number;
}

/**
 * The key whose Krumhansl and Kessler (1982) profile, rotated to its tonic, has the highest Pearson
 * correlation with the chroma, over the 24 major and minor keys. Throws for a chroma that is not 12
 * long or has no variation.
 */
export function estimateKey(chroma: ArrayLike<number>): KeyEstimate {
  if (chroma.length !== 12) throw new Error(`a chroma has 12 pitch classes, got ${chroma.length}`);
  let mean = 0;
  for (let pc = 0; pc < 12; pc++) mean += chroma[pc]!;
  mean /= 12;
  let spread = 0;
  for (let pc = 0; pc < 12; pc++) spread += (chroma[pc]! - mean) ** 2;
  if (!(spread > 0)) throw new Error('the chroma has no variation, so it names no key');
  let best: KeyEstimate | null = null;
  for (let tonic = 0; tonic < 12; tonic++) {
    for (const [mode, profile] of [
      ['major', KK_MAJOR],
      ['minor', KK_MINOR],
    ] as const) {
      const profileMean = profile.reduce((a, b) => a + b, 0) / 12;
      let covariance = 0;
      let profileSpread = 0;
      for (let pc = 0; pc < 12; pc++) {
        const p = profile[(pc - tonic + 12) % 12]! - profileMean;
        covariance += (chroma[pc]! - mean) * p;
        profileSpread += p * p;
      }
      const correlation = covariance / Math.sqrt(spread * profileSpread);
      if (!best || correlation > best.correlation) best = { tonic, mode, name: `${PITCH_NAMES[tonic]} ${mode}`, correlation };
    }
  }
  return best!;
}

export interface ChordEstimate {
  /** The bar's index on the grid: bar 0 starts at firstDownbeatSec. */
  readonly bar: number;
  readonly startSec: number;
  readonly endSec: number;
  /** The best triad, like 'Am' or 'F#' (sharps); null only when the bar holds no measurable chroma. */
  readonly chord: string | null;
  /** The best triad's pitch classes: root, third, fifth. */
  readonly pitchClasses: number[] | null;
  /** The best triad's cosine similarity with the bar's chroma. */
  readonly score: number;
  /** The best similarity minus the second best. */
  readonly margin: number;
  readonly confident: boolean;
}

/**
 * The chord of each bar of the grid that lies wholly inside [startSec, endSec) (default the whole
 * recording): the bar's mean chroma, unit length, against 24 binary triad templates by cosine
 * similarity. The score is the best similarity and the margin the best minus the second best; the
 * bar is confident when score >= minScore (default 0.67) and margin >= minMargin (default 0.055;
 * see DEFAULT_MIN_SCORE for how both were chosen). A bar that is not confident still names its best
 * guess; only a bar with no measurable chroma (silence) has a null chord.
 */
export function barChords(
  mono: Samples,
  sampleRate: number,
  grid: BeatGrid,
  options: { startSec?: number; endSec?: number; minScore?: number; minMargin?: number } = {}
): ChordEstimate[] {
  checkSampleRate(sampleRate);
  const barSec = grid.beatSec * grid.beatsPerBar;
  if (!(barSec > 0) || !Number.isFinite(barSec) || !Number.isFinite(grid.firstDownbeatSec)) throw new Error('the grid needs a positive, finite bar length and a finite first downbeat');
  const duration = mono.length / sampleRate;
  const startSec = Math.max(0, options.startSec ?? 0);
  const endSec = Math.min(duration, options.endSec ?? duration);
  const minScore = options.minScore ?? DEFAULT_MIN_SCORE;
  const minMargin = options.minMargin ?? DEFAULT_MIN_MARGIN;
  const firstBar = Math.ceil((startSec - grid.firstDownbeatSec) / barSec - 1e-9);
  const lastBar = Math.floor((endSec - grid.firstDownbeatSec) / barSec + 1e-9) - 1;
  if (lastBar < firstBar) return [];

  const hop = windowLength(CHROMA_WINDOW_SEC, sampleRate) / 4;
  const fps = sampleRate / hop;
  const first = Math.max(0, Math.floor((grid.firstDownbeatSec + firstBar * barSec) * fps));
  const end = Math.min(chromaFrameCount(mono.length, sampleRate), Math.ceil((grid.firstDownbeatSec + (lastBar + 1) * barSec) * fps) + 1);
  const span = chromaSpan(mono, sampleRate, first, end);

  const chords: ChordEstimate[] = [];
  for (let bar = firstBar; bar <= lastBar; bar++) {
    const barStart = grid.firstDownbeatSec + bar * barSec;
    const barEnd = barStart + barSec;
    chords.push({ bar: bar === 0 ? 0 : bar, startSec: barStart, endSec: barEnd, ...nameChord(meanChroma(span, barStart, barEnd), minScore, minMargin) });
  }
  return chords;
}

function nameChord(
  chroma: Float64Array | null,
  minScore: number,
  minMargin: number
): Pick<ChordEstimate, 'chord' | 'pitchClasses' | 'score' | 'margin' | 'confident'> {
  const vector = unit(chroma);
  if (!vector.some((v) => v > 0)) return { chord: null, pitchClasses: null, score: 0, margin: 0, confident: false };
  let best = TRIADS[0]!;
  let bestScore = -Infinity;
  let secondScore = -Infinity;
  for (const triad of TRIADS) {
    const [root, third, fifth] = triad.pitchClasses;
    const score = (vector[root]! + vector[third]! + vector[fifth]!) / Math.sqrt(3);
    if (score > bestScore) {
      secondScore = bestScore;
      bestScore = score;
      best = triad;
    } else if (score > secondScore) {
      secondScore = score;
    }
  }
  const margin = bestScore - secondScore;
  return {
    chord: best.name,
    pitchClasses: [...best.pitchClasses],
    score: bestScore,
    margin,
    confident: bestScore >= minScore && margin >= minMargin,
  };
}
