// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 100, examples } as never);

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { detectClicks, type ClickReport } from '../clicks';

const RATE = 48000;
const FADE = 240; // 5 ms at 48 kHz
const DEFAULT_THRESHOLD = 10; // detectClicks's default; the method test ties the two together
const TOLERANCE = 2; // samples between a click's reported sample and the true discontinuity

/** Seeded uniform generator on [0, 1). */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Standard normal draws (Box-Muller) from a seeded generator. */
const normal = (random: () => number) => () => Math.sqrt(-2 * Math.log(1 - random())) * Math.cos(2 * Math.PI * random());

function sine(hz: number, amplitude: number, length = RATE, phase = 0): Float64Array {
  const out = new Float64Array(length);
  for (let i = 0; i < length; i++) out[i] = amplitude * Math.sin((2 * Math.PI * hz * i) / RATE + phase);
  return out;
}

/** Raised-cosine gain: 0 at k = 0, 1 from k = length on. */
const rise = (k: number, length: number): number => (k <= 0 ? 0 : k >= length ? 1 : 0.5 - 0.5 * Math.cos((Math.PI * k) / length));

function fadeEnds(signal: Float64Array, length = FADE): Float64Array {
  const last = signal.length - 1;
  for (let i = 0; i <= last; i++) signal[i] = signal[i]! * rise(i, length) * rise(last - i, length);
  return signal;
}

function addStep(signal: Float64Array, at: number, size: number): Float64Array {
  const out = signal.slice();
  for (let i = at; i < out.length; i++) out[i] = out[i]! + size;
  return out;
}

/** Sample `at` removed: everything after it plays one sample early. */
function dropSample(source: Float64Array, at: number): Float64Array {
  const out = new Float64Array(source.length - 1);
  for (let i = 0; i < out.length; i++) out[i] = source[i < at ? i : i + 1]!;
  return out;
}

/** Sample `at` played twice. */
function repeatSample(source: Float64Array, at: number): Float64Array {
  const out = new Float64Array(source.length + 1);
  for (let i = 0; i < out.length; i++) out[i] = source[i <= at ? i : i - 1]!;
  return out;
}

/** Silence, then a 440 Hz cosine from `at` on (so it starts at its peak), raised over `fade` samples if fade > 0. */
function startAt(at: number, fade: number): Float64Array {
  const out = new Float64Array(RATE);
  const w = (2 * Math.PI * 440) / RATE;
  for (let i = at; i < RATE; i++) out[i] = 0.5 * Math.cos(w * (i - at)) * (fade > 0 ? rise(i - at, fade) : 1);
  return out;
}

/** A 440 Hz sine that falls silent at `at`, cut mid-cycle or lowered over `fade` samples ending there. */
function stopAt(at: number, fade: number): Float64Array {
  const out = sine(440, 0.5);
  for (let i = 0; i < RATE; i++) out[i] = i >= at ? 0 : out[i]! * (fade > 0 ? rise(at - i, fade) : 1);
  return out;
}

// Onsets whose offsets within the 512-sample hop run from 440 down to 16.
const ONSETS = [3000, 8067, 13134, 18201, 23268, 28335, 33402, 38469, 43536];

/** Gaussian noise bursts (1 ms linear attack, 50 ms exponential decay) over a quiet 220 Hz sine pad. */
function drumHits(seed: number): Float64Array {
  const noise = normal(mulberry32(seed));
  const out = sine(220, 0.02);
  for (const onset of ONSETS) {
    for (let k = 0; onset + k < RATE; k++) {
      const gain = k < 48 ? k / 48 : Math.exp(-(k - 48) / 2400);
      out[onset + k] = out[onset + k]! + 0.15 * gain * noise();
    }
  }
  return out;
}

/** Six-partial notes (amplitude 0.1/h, random phases) with a 2 ms raised-cosine attack; partial h decays with time constant 0.4/h s. */
function struckTones(seed: number): Float64Array {
  const random = mulberry32(seed);
  const pitches = [196, 220, 262, 294, 330, 392, 440, 494, 523];
  const out = new Float64Array(RATE);
  ONSETS.forEach((onset, j) => {
    const f0 = pitches[j]!;
    const partials = [1, 2, 3, 4, 5, 6].map((h) => ({
      w: (2 * Math.PI * f0 * h) / RATE,
      amplitude: 0.1 / h,
      phase: 2 * Math.PI * random(),
      tau: (0.4 * RATE) / h,
    }));
    for (let k = 0; onset + k < RATE; k++) {
      let s = 0;
      for (const p of partials) s += p.amplitude * Math.exp(-k / p.tau) * Math.sin(p.w * k + p.phase);
      out[onset + k] = out[onset + k]! + rise(k, 96) * s;
    }
  });
  return out;
}

/** Gaussian white noise at -20 dBFS RMS. */
function whiteNoise(seed: number): Float64Array {
  const noise = normal(mulberry32(seed));
  const out = new Float64Array(RATE);
  for (let i = 0; i < RATE; i++) out[i] = 0.1 * noise();
  return out;
}

/** Pink noise (Kellet's filter on Gaussian white noise) scaled to -20 dBFS RMS. */
function pinkNoise(seed: number): Float64Array {
  const noise = normal(mulberry32(seed));
  const out = new Float64Array(RATE);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  let power = 0;
  for (let i = 0; i < RATE; i++) {
    const w = noise();
    b0 = 0.99886 * b0 + w * 0.0555179;
    b1 = 0.99332 * b1 + w * 0.0750759;
    b2 = 0.969 * b2 + w * 0.153852;
    b3 = 0.8665 * b3 + w * 0.3104856;
    b4 = 0.55 * b4 + w * 0.5329522;
    b5 = -0.7616 * b5 - w * 0.016898;
    out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362;
    b6 = w * 0.115926;
    power += out[i]! * out[i]!;
  }
  const gain = 0.1 / Math.sqrt(power / RATE);
  for (let i = 0; i < RATE; i++) out[i] = out[i]! * gain;
  return out;
}

/** Eight sines at 0.1 each (C3 E3 G3 B3 D4 F#4 A4 C5, random phases) with 5 ms fades. */
function denseChord(seed: number): Float64Array {
  const random = mulberry32(seed);
  const out = new Float64Array(RATE);
  for (const hz of [130.81, 164.81, 196, 246.94, 293.66, 369.99, 440, 523.25]) {
    const tone = sine(hz, 0.1, RATE, 2 * Math.PI * random());
    for (let i = 0; i < RATE; i++) out[i] = out[i]! + tone[i]!;
  }
  return fadeEnds(out);
}

/** Kick (sine swept 150 to 50 Hz, 1 ms raised-cosine attack), claps (three bursts 8 ms apart and a tail) and closed hi-hats (differenced noise), the noise with 0.5 ms linear attacks. */
function percussion(seed: number): Float64Array {
  const noise = normal(mulberry32(seed));
  const out = new Float64Array(RATE);
  const add = (at: number, length: number, f: (k: number) => number) => {
    for (let k = 0; k < length && at + k < RATE; k++) out[at + k] = out[at + k]! + f(k);
  };
  for (const at of [2000, 26333]) {
    let phase = 0;
    add(at, 24000, (k) => {
      phase += (2 * Math.PI * (50 + 100 * Math.exp(-k / 1440))) / RATE;
      return 0.6 * rise(k, 48) * Math.exp(-k / 7200) * Math.sin(phase);
    });
  }
  for (const at of [14123, 38290]) {
    for (const lag of [0, 384, 768]) add(at + lag, 1200, (k) => 0.12 * Math.min(1, k / 24) * Math.exp(-k / 144) * noise());
    add(at + 1152, 9600, (k) => 0.08 * Math.min(1, k / 24) * Math.exp(-k / 1920) * noise());
  }
  for (const at of [5000, 11051, 17179, 23300, 29444, 35575, 41720]) {
    let previous = 0;
    add(at, 7200, (k) => {
      const n = noise();
      const hp = n - previous;
      previous = n;
      return 0.08 * Math.min(1, k / 24) * Math.exp(-k / 1440) * hp;
    });
  }
  return out;
}

/** Notes of four partials under an ADSR envelope of raised-cosine segments (5 ms attack, 60 ms decay to 0.6, 120 ms release). */
function adsrNotes(seed: number): Float64Array {
  const random = mulberry32(seed);
  const out = new Float64Array(RATE);
  const level = (t: number) => (t < 240 ? rise(t, 240) : 1 - 0.4 * rise(t - 240, 2880));
  ONSETS.forEach((onset, j) => {
    const f0 = [262, 330, 392, 523, 294, 349, 440, 587, 220][j]!;
    const phases = [1, 2, 3, 4].map(() => 2 * Math.PI * random());
    const held = 4800 + 401 * j;
    for (let k = 0; k < held + 5760 && onset + k < RATE; k++) {
      const envelope = k < held ? level(k) : level(held) * (1 - rise(k - held, 5760));
      let s = 0;
      for (let h = 1; h <= 4; h++) s += (0.12 / h) * Math.sin((2 * Math.PI * f0 * h * k) / RATE + phases[h - 1]!);
      out[onset + k] = out[onset + k]! + envelope * s;
    }
  });
  return out;
}

interface Tone {
  readonly hz: number;
  readonly weight: number;
  readonly phase: number;
}

/** Sines with amplitudes scaled to sum to `total`, faded over 5 ms at both ends. */
function renderMix(tones: readonly Tone[], total: number, length: number): Float64Array {
  const weights = tones.reduce((sum, t) => sum + t.weight, 0);
  const out = new Float64Array(length);
  for (const t of tones) {
    const tone = sine(t.hz, (total * t.weight) / weights, length, t.phase);
    for (let i = 0; i < length; i++) out[i] = out[i]! + tone[i]!;
  }
  return fadeEnds(out);
}

// Four loud tones from 2.1 to 4.2 kHz. A 0.05 step on them scores about 20, near the weakest step
// seen in random mixes, because the step's residual tail raises its own frame's scale.
const HARD_MIX: readonly Tone[] = [
  { hz: 2100, weight: 0.85, phase: 2.6 },
  { hz: 2600, weight: 0.18, phase: 0.8 },
  { hz: 3900, weight: 0.62, phase: 0.4 },
  { hz: 4200, weight: 0.49, phase: 6 },
];

// Offsets 27 and 82 are skipped: they put the repeat on a crest, where it only bends the slope
// (see the module's limits).
const REPEAT_OFFSETS = [0, 20, 41, 55, 75, 96];

// Edges at eight offsets across one 512-sample hop.
const EDGES = Array.from({ length: 8 }, (_, j) => 20005 + 64 * j);

const CLICK_FREE: ReadonlyArray<readonly [string, () => Float64Array]> = [
  ['a clean 440 Hz sine', () => sine(440, 0.5)],
  ['a sine with a 5 ms fade-in', () => startAt(20000 + 123, FADE)],
  ['a sine with a 5 ms fade-out', () => stopAt(30000 + 37, FADE)],
  ['noise bursts over a quiet pad', () => drumHits(11)],
  ['struck six-partial notes', () => struckTones(12)],
  ['white noise at -20 dBFS', () => whiteNoise(13)],
  ['pink noise at -20 dBFS', () => pinkNoise(14)],
  ['a dense chord of eight sines', () => denseChord(15)],
  ['a kick, claps and hi-hats', () => percussion(16)],
  ['notes under a raised-cosine ADSR envelope', () => adsrNotes(17)],
];

/** Every case that must give exactly one click, with where the discontinuity is. */
const CLICKED: ReadonlyArray<readonly [string, () => Float64Array, number]> = [
  ['a 0.2 step', () => addStep(sine(440, 0.5), 24137, 0.2), 24137],
  ...[0, 13, 27, 41, 55, 68, 82, 96].map(
    (offset) => [`a dropped sample, offset ${offset}`, () => dropSample(sine(440, 0.5, RATE + 1), 20000 + offset), 20000 + offset] as const
  ),
  ...REPEAT_OFFSETS.map(
    (offset) => [`a repeated sample, offset ${offset}`, () => repeatSample(sine(440, 0.5, RATE - 1), 30000 + offset), 30001 + offset] as const
  ),
  ['an abrupt start', () => startAt(20000 + 123, 0), 20000 + 123],
  ['an abrupt stop', () => stopAt(30000 + 37, 0), 30000 + 37],
  ['a 0.05 step on a 50 Hz sine', () => addStep(sine(50, 0.9), 12345, 0.05), 12345],
  ['a 0.05 step on four loud tones', () => addStep(renderMix(HARD_MIX, 0.9, RATE / 2), 2558, 0.05), 2558],
];

const describeClicks = (report: ClickReport) =>
  report.clicks.map((c) => `ch${c.channel}@${c.sample} (${c.strength.toFixed(1)})`).join(', ');

function expectOneClickAt(report: ClickReport, at: number, channel = 0) {
  expect(report.count, describeClicks(report)).toBe(1);
  const [click] = report.clicks;
  expect(click!.channel).toBe(channel);
  expect(Math.abs(click!.sample - at)).toBeLessThanOrEqual(TOLERANCE);
  expect(click!.timeSec).toBeCloseTo(click!.sample / RATE, 9);
  expect(click!.strength).toBeGreaterThan(DEFAULT_THRESHOLD);
}

describe('detectClicks on tones', () => {
  it('finds nothing in a clean 440 Hz sine at amplitude 0.5 and checks every frame', () => {
    const report = detectClicks([sine(440, 0.5)], RATE);
    expect(report.count).toBe(0);
    expect(report.clicks).toEqual([]);
    // Frames start every 512 samples; the last one is moved back to end at the last sample.
    expect(report.framesChecked).toBe(Math.ceil((RATE - 1024) / 512) + 1);
  });

  it('finds exactly one click within 2 samples of a 0.2 step added to the sine', () => {
    const at = 24137;
    expectOneClickAt(detectClicks([addStep(sine(440, 0.5), at, 0.2)], RATE), at);
  });

  it('finds one click near a dropped sample, wherever in the cycle it falls', () => {
    for (const offset of [0, 13, 27, 41, 55, 68, 82, 96]) {
      const at = 20000 + offset;
      expectOneClickAt(detectClicks([dropSample(sine(440, 0.5, RATE + 1), at)], RATE), at);
    }
  });

  it('finds one click near a repeated sample away from the crests', () => {
    for (const offset of REPEAT_OFFSETS) {
      const at = 30000 + offset;
      expectOneClickAt(detectClicks([repeatSample(sine(440, 0.5, RATE - 1), at)], RATE), at + 1);
    }
  });

  it('finds one click where a sine starts at its peak out of silence, and none when it rises over 5 ms', () => {
    for (const at of EDGES) {
      expectOneClickAt(detectClicks([startAt(at, 0)], RATE), at);
      const faded = detectClicks([startAt(at, FADE)], RATE);
      expect(faded.count, `fade-in at ${at}: ${describeClicks(faded)}`).toBe(0);
    }
  });

  it('finds one click where a sine is cut to silence mid-cycle, and none when it falls over 5 ms', () => {
    for (const at of EDGES) {
      expectOneClickAt(detectClicks([stopAt(at, 0)], RATE), at);
      const faded = detectClicks([stopAt(at, FADE)], RATE);
      expect(faded.count, `fade-out at ${at}: ${describeClicks(faded)}`).toBe(0);
    }
  });
});

describe('detectClicks on musical transients', () => {
  const none = (signal: Float64Array) => {
    const report = detectClicks([signal], RATE);
    expect(report.count, describeClicks(report)).toBe(0);
  };

  it('passes noise bursts with a 1 ms linear attack and a 50 ms decay over a quiet sine pad', () => none(drumHits(1)));
  it('passes struck six-partial notes with a 2 ms raised-cosine attack and exponential decay', () => none(struckTones(2)));
  it('passes Gaussian white noise at -20 dBFS for 1 s', () => none(whiteNoise(3)));
  it('passes pink noise at -20 dBFS for 1 s', () => none(pinkNoise(4)));
  it('passes a dense chord of eight sines', () => none(denseChord(5)));
  it('passes a kick drum with a 1 ms raised-cosine attack, claps and closed hi-hats', () => none(percussion(6)));
  it('passes notes under a raised-cosine ADSR envelope', () => none(adsrNotes(7)));
});

describe('detectClicks on several channels', () => {
  it('reports a click only in channel 1 with channel 1', () => {
    const at = 30017;
    const right = sine(330, 0.4);
    right[at] = right[at]! + 0.1;
    const report = detectClicks([sine(440, 0.5), right], RATE);
    expectOneClickAt(report, at, 1);
    expect(report.framesChecked).toBe(2 * (Math.ceil((RATE - 1024) / 512) + 1));
  });
});

describe('detectClicks as a property', () => {
  const LENGTH = RATE / 2;
  const toneArb = fc.record({
    hz: fc.double({ min: 50, max: 5000, noNaN: true }),
    weight: fc.double({ min: 0.01, max: 1, noNaN: true }),
    phase: fc.double({ min: 0, max: 2 * Math.PI, noNaN: true }),
  });
  const mixArb = fc.record({
    tones: fc.array(toneArb, { minLength: 1, maxLength: 4 }),
    total: fc.double({ min: 0.01, max: 0.9, noNaN: true }),
  });
  const stepArb = fc.record({
    at: fc.integer({ min: 2000, max: LENGTH - 2000 }),
    size: fc.double({ min: 0.05, max: 0.3, noNaN: true }),
    negative: fc.boolean(),
  });
  const lone = (hz: number, total: number) => ({ tones: [{ hz, weight: 1, phase: 0 }], total });
  const four = { tones: [50, 51, 2500, 5000].map((hz, i) => ({ hz, weight: 1, phase: i })), total: 0.9 };

  it('finds nothing in faded sums of 1 to 4 sines, and one click within 2 samples of an added step', () => {
    fc.assert(
      fc.property(mixArb, stepArb, (mix, step) => {
        const clean = renderMix(mix.tones, mix.total, LENGTH);
        const before = detectClicks([clean], RATE);
        expect(before.count, describeClicks(before)).toBe(0);
        expectOneClickAt(detectClicks([addStep(clean, step.at, step.negative ? -step.size : step.size)], RATE), step.at);
      }),
      params([
        // The smallest step at both allowed bounds, the largest on the quietest tone, and the hard mix.
        [lone(50, 0.9), { at: 2000, size: 0.05, negative: false }],
        [lone(5000, 0.9), { at: LENGTH - 2000, size: 0.05, negative: true }],
        [lone(50, 0.01), { at: LENGTH - 2000, size: 0.3, negative: true }],
        [four, { at: 2000, size: 0.05, negative: true }],
        [four, { at: LENGTH - 2000, size: 0.05, negative: false }],
        [{ tones: [...HARD_MIX], total: 0.9 }, { at: 2558, size: 0.05, negative: false }],
      ])
    );
  }, 120_000);
});

describe('the default threshold', () => {
  it('is the value these tests assume', () => {
    const signal = [sine(440, 0.5, 4096)];
    expect(detectClicks(signal, RATE).method).toBe(detectClicks(signal, RATE, { threshold: DEFAULT_THRESHOLD }).method);
  });

  // Measured: the weakest required click is the step on the hard mix (about 20) and the strongest
  // click-free peak is the kick's 1 ms raised-cosine attack (about 4.6), so 10 has about 2x each side.
  it('sits at least 1.5 times below the weakest click these tests require', () => {
    for (const [name, make, at] of CLICKED) {
      const report = detectClicks([make()], RATE, { threshold: 1, maxReport: 1e6 });
      const near = report.clicks.filter((c) => Math.abs(c.sample - at) <= TOLERANCE);
      expect(near.length, name).toBe(1);
      expect(near[0]!.strength, name).toBeGreaterThan(1.5 * DEFAULT_THRESHOLD);
    }
  });

  it('sits at least 1.5 times above every impulsive residual peak in the click-free material', () => {
    for (const [name, make] of CLICK_FREE) {
      const report = detectClicks([make()], RATE, { threshold: DEFAULT_THRESHOLD / 1.5 });
      expect(report.count, `${name}: ${describeClicks(report)}`).toBe(0);
    }
  });
});

describe('the click report', () => {
  it('lists at most maxReport clicks in time order and counts them all', () => {
    const signal = sine(440, 0.2);
    const spikes = Array.from({ length: 12 }, (_, i) => 2000 + 3700 * i);
    for (const at of spikes) signal[at] = signal[at]! + 0.1;
    const report = detectClicks([signal], RATE, { maxReport: 5 });
    expect(report.count).toBe(12);
    expect(report.clicks.length).toBe(5);
    report.clicks.forEach((click, i) => expect(Math.abs(click.sample - spikes[i]!)).toBeLessThanOrEqual(TOLERANCE));
    expect(detectClicks([signal], RATE).clicks.length).toBe(12);
  });

  it('reads digital silence without dividing by zero, and finds one sample of 0.01 in it', () => {
    const silence = new Float64Array(RATE);
    expect(detectClicks([silence], RATE).count).toBe(0);
    silence[25000] = 0.01;
    const report = detectClicks([silence], RATE);
    expectOneClickAt(report, 25000);
    expect(Number.isFinite(report.clicks[0]!.strength)).toBe(true);
  });

  it('skips the first order samples and checks a signal shorter than one frame', () => {
    const early = new Float64Array(600);
    early[10] = 0.5;
    expect(detectClicks([early], RATE).count).toBe(0);
    early[10] = 0;
    early[300] = 0.5;
    const report = detectClicks([early], RATE);
    expect(report.framesChecked).toBe(1);
    expectOneClickAt(report, 300);
  });

  it('names its method and parameters in one sentence', () => {
    const { method } = detectClicks([sine(440, 0.5, 4096)], RATE, { order: 16, frame: 512, threshold: 7 });
    for (const value of ['16', '512', '256', '7']) expect(method).toContain(value);
    expect(method.trim().endsWith('.')).toBe(true);
    expect(method.trim().slice(0, -1)).not.toContain('. ');
  });

  it('rejects options and input it cannot use', () => {
    const ok = [sine(440, 0.5, 4096)];
    expect(() => detectClicks(ok, 0)).toThrow(RangeError);
    expect(() => detectClicks(ok, RATE, { order: 0 })).toThrow(RangeError);
    expect(() => detectClicks(ok, RATE, { frame: 1023 })).toThrow(RangeError);
    expect(() => detectClicks(ok, RATE, { order: 24, frame: 48 })).toThrow(RangeError);
    expect(() => detectClicks(ok, RATE, { threshold: 0 })).toThrow(RangeError);
    expect(() => detectClicks(ok, RATE, { maxReport: -1 })).toThrow(RangeError);
    const bad = sine(440, 0.5, 4096);
    bad[100] = Number.NaN;
    expect(() => detectClicks([bad], RATE)).toThrow(RangeError);
  });
});
