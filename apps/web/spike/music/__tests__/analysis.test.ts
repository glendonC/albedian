// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 20, examples } as never);

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { barChords, beatGrid, chromaFrames, estimateKey, estimateTempo, onsetEnvelope, type BeatGrid } from '../analysis';

/** Every signal here is synthesized at 22,050 Hz, which keeps the suite fast; the module takes any rate. */
const RATE = 22050;

/** mulberry32: a small seeded generator, uniform in [0, 1), so every signal is reproducible. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const seeds = fc.integer({ min: 1, max: 2 ** 31 - 1 });

/** Uniform white noise of the given RMS. */
function whiteNoise(seconds: number, rms: number, seed: number): Float64Array {
  const random = mulberry32(seed);
  const out = new Float64Array(Math.round(seconds * RATE));
  const amplitude = rms * Math.sqrt(3);
  for (let i = 0; i < out.length; i++) out[i] = amplitude * (2 * random() - 1);
  return out;
}

/**
 * A click track: from `firstClickSec`, one burst of uniform noise per beat (peak 0.5, decaying with
 * a 4 ms time constant, cut at 30 ms) over white noise of RMS 0.001, 54 dB below the bursts' peak.
 */
function clickTrack(bpm: number, firstClickSec: number, seconds: number, seed: number): Float64Array {
  const out = whiteNoise(seconds, 0.001, seed);
  const random = mulberry32(seed ^ 0x5bd1e995);
  const beatSec = 60 / bpm;
  for (let k = 0; firstClickSec + k * beatSec < seconds; k++) {
    const onset = (firstClickSec + k * beatSec) * RATE;
    const end = Math.min(out.length, Math.ceil(onset + 0.03 * RATE));
    for (let i = Math.ceil(onset); i < end; i++) {
      out[i] = out[i]! + 0.5 * Math.exp(-(i - onset) / (0.004 * RATE)) * (2 * random() - 1);
    }
  }
  return out;
}

/** The signed gap from `time` to the nearest of `reference + k * period` (any whole k), in seconds. */
function phaseGap(time: number, reference: number, period: number): number {
  const d = (((time - reference) % period) + period) % period;
  return d > period / 2 ? d - period : d;
}

const relativeGap = (a: number, b: number): number => Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b));

const midiHz = (midi: number): number => 440 * 2 ** ((midi - 69) / 12);

/**
 * Adds a harmonic tone (5 partials, amplitudes 1/k, random phases) from `startSec`: a 5 ms linear
 * attack, an exponential decay with time constant `decaySec`, and a 5 ms fade ending at `lengthSec`.
 */
function addTone(
  out: Float64Array,
  startSec: number,
  lengthSec: number,
  midi: number,
  amplitude: number,
  decaySec: number,
  random: () => number
): void {
  const hz = midiHz(midi);
  const start = Math.round(startSec * RATE);
  const length = Math.min(Math.round(lengthSec * RATE), out.length - start);
  const ramp = 0.005 * RATE;
  const phases = [0, 1, 2, 3, 4].map(() => 2 * Math.PI * random());
  for (let i = 0; i < length; i++) {
    const t = i / RATE;
    const gain = amplitude * Math.exp(-t / decaySec) * Math.min(1, i / ramp, (length - i) / ramp);
    let sum = 0;
    for (let k = 1; k <= 5; k++) sum += Math.sin(2 * Math.PI * k * hz * t + phases[k - 1]!) / k;
    out[start + i] = out[start + i]! + gain * sum;
  }
}

/** A pure sine of one second with 10 ms fades, at a fractional MIDI note. */
function sineTone(midi: number): Float64Array {
  const hz = midiHz(midi);
  const ramp = 0.01 * RATE;
  return Float64Array.from({ length: RATE }, (_, i) => 0.3 * Math.min(1, i / ramp, (RATE - i) / ramp) * Math.sin((2 * Math.PI * hz * i) / RATE));
}

const sumFrames = (frames: readonly Float64Array[]): Float64Array => {
  const total = new Float64Array(12);
  for (const frame of frames) for (let pc = 0; pc < 12; pc++) total[pc] = total[pc]! + frame[pc]!;
  return total;
};

const CHORD_BPM = 90;
const BEAT_SEC = 60 / CHORD_BPM;
const BAR_SEC = 4 * BEAT_SEC;
const FIRST_DOWNBEAT = 0.4;
const PROGRESSION = [
  { tones: [57, 60, 64], root: 45 }, // Am: A3 C4 E4 over A2
  { tones: [53, 57, 60], root: 41 }, // F: F3 A3 C4 over F2
  { tones: [60, 64, 67], root: 36 }, // C: C4 E4 G4 over C2
  { tones: [55, 59, 62], root: 43 }, // G: G3 B3 D4 over G2
];
const EXPECTED_CHORDS = ['Am', 'F', 'C', 'G', 'Am', 'F', 'C', 'G'];

/**
 * Am, F, C, G at 90 BPM, one 4-beat bar each, played twice from FIRST_DOWNBEAT (silence before it,
 * half a second after the last bar). The chord's three tones are re-struck on every beat (decay
 * 0.35 s), louder on the downbeat; the low root is struck once per bar (decay 1.5 s).
 */
function chordProgression(seed: number): Float64Array {
  const random = mulberry32(seed);
  const out = new Float64Array(Math.round((FIRST_DOWNBEAT + 8 * BAR_SEC + 0.5) * RATE));
  for (let bar = 0; bar < 8; bar++) {
    const { tones, root } = PROGRESSION[bar % 4]!;
    const barStart = FIRST_DOWNBEAT + bar * BAR_SEC;
    addTone(out, barStart, BAR_SEC, root, 0.12, 1.5, random);
    for (let beat = 0; beat < 4; beat++) {
      for (const tone of tones) addTone(out, barStart + beat * BEAT_SEC, BEAT_SEC, tone, beat === 0 ? 0.1 : 0.06, 0.35, random);
    }
  }
  return out;
}

let progressionCache: Float64Array | undefined;
const progression = (): Float64Array => (progressionCache ??= chordProgression(5));

/** The grid the progression was written on. */
const TRUE_GRID: BeatGrid = {
  bpm: CHORD_BPM,
  beatSec: BEAT_SEC,
  firstBeatSec: FIRST_DOWNBEAT,
  beatsPerBar: 4,
  firstDownbeatSec: FIRST_DOWNBEAT,
  strength: 1,
};

describe('the onset envelope', () => {
  it('runs at 22,050 / 256 frames per second (a 1024-sample window, a 256-sample hop) and is never negative', () => {
    const envelope = onsetEnvelope(clickTrack(100, 0.2, 5, 3), RATE);
    expect(envelope.framesPerSecond).toBeCloseTo(RATE / 256, 9);
    expect(Math.abs(envelope.values.length - 5 * envelope.framesPerSecond)).toBeLessThan(2);
    expect(Math.min(...envelope.values)).toBeGreaterThanOrEqual(0);
    expect(Math.max(...envelope.values)).toBeGreaterThan(0);
  });

  it('is zero throughout silence, and estimateTempo refuses it', () => {
    const envelope = onsetEnvelope(new Float64Array(5 * RATE), RATE);
    expect(envelope.values.every((v) => v === 0)).toBe(true);
    expect(() => estimateTempo(envelope)).toThrow(/no variation/);
  });
});

describe('the tempo and the beat of a click track', () => {
  it('reads 66.3 BPM within 0.5 percent (not double or half) and puts a beat within 25 ms of the first click', () => {
    const bpm = 66.3;
    const firstClick = 0.137;
    const track = clickTrack(bpm, firstClick, 30, 1);
    expect(relativeGap(estimateTempo(onsetEnvelope(track, RATE)).bpm, bpm)).toBeLessThan(0.005);
    const grid = beatGrid(track, RATE);
    expect(relativeGap(grid.bpm, bpm)).toBeLessThan(0.005);
    expect(grid.beatSec).toBeCloseTo(60 / grid.bpm, 12);
    expect(grid.firstBeatSec).toBeGreaterThanOrEqual(0);
    expect(grid.firstBeatSec).toBeLessThan(grid.beatSec);
    expect(Math.abs(phaseGap(grid.firstBeatSec, firstClick, 60 / bpm))).toBeLessThan(0.025);
    const downbeat = (grid.firstDownbeatSec - grid.firstBeatSec) / grid.beatSec;
    expect(Math.abs(downbeat - Math.round(downbeat))).toBeLessThan(1e-9);
    expect([0, 1, 2, 3]).toContain(Math.round(downbeat));
  });

  it('refines the lag between frames: a beat of 40.5 frames (127.6 BPM) reads within 0.2 percent, where whole lags miss by 1.2', () => {
    const bpm = (60 * RATE) / 256 / 40.5;
    const tempo = estimateTempo(onsetEnvelope(clickTrack(bpm, 0.05, 20, 4), RATE));
    expect(relativeGap(tempo.bpm, bpm)).toBeLessThan(0.002);
  });

  // Each run builds and analyses a 15 s click track. Measured on the M5 Max: 1.4 to 4.7 s for the 22 runs
  // at load averages of 60 to 80, and 6.8 s in a gate run under heavier load, past Vitest's 5 s default.
  // 30 s is over four times the worst seen; the runs stay at 20 plus the seeded ends of the range.
  it('recovers any tempo from 60 to 160 BPM within 1 percent, and the beat within 25 ms, at any phase (property)', { timeout: 30_000 }, () => {
    fc.assert(
      fc.property(
        fc.double({ min: 60, max: 160, noNaN: true }),
        fc.double({ min: 0, max: 1, maxExcluded: true, noNaN: true }),
        seeds,
        (bpm, phase, seed) => {
          const firstClick = (phase * 60) / bpm;
          const track = clickTrack(bpm, firstClick, 15, seed);
          expect(relativeGap(estimateTempo(onsetEnvelope(track, RATE)).bpm, bpm)).toBeLessThan(0.01);
          const grid = beatGrid(track, RATE);
          expect(relativeGap(grid.bpm, bpm)).toBeLessThan(0.01);
          expect(Math.abs(phaseGap(grid.firstBeatSec, firstClick, 60 / bpm))).toBeLessThan(0.025);
        }
      ),
      params([
        [60, 0.5, 7],
        [160, 0.25, 8],
      ])
    );
  });

  it('does not change estimateTempo for any gain from 0.1 to 1 (property)', () => {
    const track = clickTrack(123, 0.31, 12, 21);
    const reference = estimateTempo(onsetEnvelope(track, RATE));
    fc.assert(
      fc.property(fc.double({ min: 0.1, max: 1, noNaN: true }), (gain) => {
        const tempo = estimateTempo(onsetEnvelope(track.map((x) => x * gain), RATE));
        expect(relativeGap(tempo.bpm, reference.bpm)).toBeLessThan(1e-9);
        expect(Math.abs(tempo.strength - reference.strength)).toBeLessThan(1e-9);
      }),
      params([[0.1], [1]])
    );
  });
});

describe('chroma', () => {
  it('runs at 22,050 / 1024 frames per second and puts an A4 sine in pitch class 9', () => {
    const chroma = chromaFrames(sineTone(69), RATE);
    expect(chroma.framesPerSecond).toBeCloseTo(RATE / 1024, 9);
    for (const frame of chroma.frames) expect(frame.length).toBe(12);
    const total = sumFrames(chroma.frames);
    expect(total.indexOf(Math.max(...total))).toBe(9);
  });

  it('puts at least 95 percent of a sine from A#1 to A#6, detuned up to a quarter tone, in its pitch class (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 34, max: 94 }), fc.double({ min: -0.25, max: 0.25, noNaN: true }), (midi, detune) => {
        const total = sumFrames(chromaFrames(sineTone(midi + detune), RATE).frames);
        const all = total.reduce((a, b) => a + b, 0);
        expect(total[midi % 12]! / all).toBeGreaterThan(0.95);
      }),
      params([
        [34, 0],
        [36, 0.25],
        [41, -0.25],
        [94, 0.25],
      ])
    );
  });
});

describe('chords of an Am F C G progression at 90 BPM, played twice', () => {
  it('names every bar from the true grid, all confident', () => {
    const chords = barChords(progression(), RATE, TRUE_GRID);
    expect(chords.map((c) => c.chord)).toEqual(EXPECTED_CHORDS);
    expect(chords.every((c) => c.confident)).toBe(true);
    expect(chords.map((c) => c.bar)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(chords[0]!.pitchClasses).toEqual([9, 0, 4]);
    expect(chords[1]!.pitchClasses).toEqual([5, 9, 0]);
    chords.forEach((c, bar) => {
      expect(c.startSec).toBeCloseTo(FIRST_DOWNBEAT + bar * BAR_SEC, 9);
      expect(c.endSec).toBeCloseTo(FIRST_DOWNBEAT + (bar + 1) * BAR_SEC, 9);
      expect(c.margin).toBeGreaterThan(0);
      expect(c.score).toBeLessThanOrEqual(1);
    });
  });

  it('keeps only the bars inside [startSec, endSec)', () => {
    const chords = barChords(progression(), RATE, TRUE_GRID, { startSec: 3, endSec: 14 });
    expect(chords.map((c) => c.bar)).toEqual([1, 2, 3, 4]);
    expect(chords.map((c) => c.chord)).toEqual(['F', 'C', 'G', 'Am']);
  });

  it('finds the tempo, the beat and the downbeat from the recording alone, and the same chords on that grid', () => {
    const grid = beatGrid(progression(), RATE);
    expect(relativeGap(grid.bpm, CHORD_BPM)).toBeLessThan(0.01);
    expect(Math.abs(phaseGap(grid.firstBeatSec, FIRST_DOWNBEAT, BEAT_SEC))).toBeLessThan(0.025);
    expect(Math.abs(phaseGap(grid.firstDownbeatSec, FIRST_DOWNBEAT, BAR_SEC))).toBeLessThan(0.03);
    expect(barChords(progression(), RATE, grid).map((c) => c.chord)).toEqual(EXPECTED_CHORDS);
  });

  it('names the key of the summed chroma A minor or C major, the relative keys sharing every note', () => {
    const key = estimateKey(sumFrames(chromaFrames(progression(), RATE).frames));
    expect(['A minor', 'C major']).toContain(key.name);
  });
});

describe('chords that cannot be measured', () => {
  const grid: BeatGrid = { bpm: 120, beatSec: 0.5, firstBeatSec: 0, beatsPerBar: 4, firstDownbeatSec: 0, strength: 0 };

  it('never names a confident chord in white noise of any seed and level (property)', () => {
    fc.assert(
      fc.property(seeds, fc.double({ min: 0.01, max: 0.5, noNaN: true }), (seed, rms) => {
        const chords = barChords(whiteNoise(12, rms, seed), RATE, grid);
        expect(chords).toHaveLength(6);
        for (const c of chords) {
          expect(c.chord).not.toBeNull();
          expect(c.confident).toBe(false);
        }
      }),
      params()
    );
  });

  it('is not confident about a bar holding only C and E, which C major and A minor share', () => {
    const e4 = sineTone(64);
    const dyad = sineTone(60).map((x, i) => x + e4[i]!);
    const [bar] = barChords(dyad, RATE, { ...grid, beatSec: 0.25 });
    expect(bar!.confident).toBe(false);
    expect(['C', 'Am']).toContain(bar!.chord);
  });

  it('has no chord at all for a silent bar', () => {
    const [bar] = barChords(new Float64Array(3 * RATE), RATE, grid);
    expect(bar).toMatchObject({ chord: null, pitchClasses: null, score: 0, margin: 0, confident: false });
  });
});

describe('keys from Krumhansl and Kessler profiles', () => {
  const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const indicator = (pitchClasses: readonly number[]): Float64Array => {
    const chroma = new Float64Array(12);
    for (const pc of pitchClasses) chroma[pc % 12] = 1;
    return chroma;
  };

  it('names a C major scale C major and an A harmonic minor scale (A B C D E F G#) A minor', () => {
    expect(estimateKey(indicator([0, 2, 4, 5, 7, 9, 11]))).toMatchObject({ tonic: 0, mode: 'major', name: 'C major' });
    expect(estimateKey(indicator([9, 11, 0, 2, 4, 5, 8]))).toMatchObject({ tonic: 9, mode: 'minor', name: 'A minor' });
  });

  it('names the major and harmonic minor scale on every tonic, with sharps', () => {
    for (let tonic = 0; tonic < 12; tonic++) {
      const major = estimateKey(indicator([0, 2, 4, 5, 7, 9, 11].map((s) => s + tonic)));
      expect(major.name).toBe(`${NAMES[tonic]} major`);
      const minor = estimateKey(indicator([0, 2, 3, 5, 7, 8, 11].map((s) => s + tonic)));
      expect(minor.name).toBe(`${NAMES[tonic]} minor`);
      expect(minor.correlation).toBeGreaterThan(0.5);
    }
  });

  it('names the same two scales from the chroma of the scales played as harmonic tones', () => {
    const play = (notes: readonly number[]): Float64Array => {
      const out = new Float64Array(Math.round(notes.length * 0.4 * RATE));
      const random = mulberry32(9);
      notes.forEach((midi, i) => addTone(out, i * 0.4, 0.4, midi, 0.2, 0.5, random));
      return out;
    };
    const cMajor = play([60, 62, 64, 65, 67, 69, 71, 72]);
    const aHarmonicMinor = play([57, 59, 60, 62, 64, 65, 68, 69]);
    expect(estimateKey(sumFrames(chromaFrames(cMajor, RATE).frames)).name).toBe('C major');
    expect(estimateKey(sumFrames(chromaFrames(aHarmonicMinor, RATE).frames)).name).toBe('A minor');
  });

  it('refuses a chroma that is not 12 long or has no variation', () => {
    expect(() => estimateKey(new Float64Array(11))).toThrow(/12/);
    expect(() => estimateKey(new Float64Array(12).fill(0.5))).toThrow(/no variation/);
  });
});
