import { describe, expect, it, vi } from 'vitest';
import * as samplerModule from '../sampler';
import {
  SampleBank,
  chooseZone,
  measureTuningCents,
  playSample,
  playbackRate,
  vcslRootMidi,
  type Instrument,
  type SampleNote,
  type SampleZone,
} from '../sampler';

interface RecordedGainEvent {
  readonly kind: 'set' | 'linear';
  readonly value: number;
  readonly time: number;
}

class FakeAudioParam {
  value = 0;
  readonly events: RecordedGainEvent[] = [];

  setValueAtTime(value: number, time: number): this {
    this.events.push({ kind: 'set', value, time });
    this.value = value;
    return this;
  }

  linearRampToValueAtTime(value: number, time: number): this {
    this.events.push({ kind: 'linear', value, time });
    this.value = value;
    return this;
  }
}

class FakeAudioNode {
  readonly outputs: unknown[] = [];

  constructor(readonly kind: string) {}

  connect(destination: unknown): unknown {
    this.outputs.push(destination);
    return destination;
  }
}

class FakeBufferSourceNode extends FakeAudioNode {
  buffer: AudioBuffer | null = null;
  readonly playbackRate = new FakeAudioParam();
  readonly starts: number[] = [];
  readonly stops: number[] = [];

  constructor() {
    super('bufferSource');
  }

  start(when = 0): void {
    this.starts.push(when);
  }

  stop(when = 0): void {
    this.stops.push(when);
  }
}

class FakeGainNode extends FakeAudioNode {
  readonly gain = new FakeAudioParam();

  constructor() {
    super('gain');
  }
}

class FakeStereoPannerNode extends FakeAudioNode {
  readonly pan = new FakeAudioParam();

  constructor() {
    super('panner');
  }
}

function makeAudioBuffer(channels: readonly Float32Array[], sampleRate = 48_000, durationOverride?: number): AudioBuffer {
  const length = channels[0]?.length ?? 0;
  const duration = durationOverride ?? (sampleRate > 0 ? length / sampleRate : 0);
  return {
    length,
    duration,
    sampleRate,
    numberOfChannels: channels.length,
    getChannelData(channel: number): Float32Array<ArrayBuffer> {
      const data = channels[channel];
      if (!data) throw new RangeError(`missing channel ${channel}`);
      return data as Float32Array<ArrayBuffer>;
    },
    copyFromChannel: vi.fn(),
    copyToChannel: vi.fn(),
  } as unknown as AudioBuffer;
}

class FakeSamplerContext {
  readonly sources: FakeBufferSourceNode[] = [];
  readonly gains: FakeGainNode[] = [];
  readonly panners: FakeStereoPannerNode[] = [];
  readonly decodeCalls: number[] = [];

  constructor(private readonly decodeImpl: (bytes: ArrayBuffer) => Promise<AudioBuffer> = async () => makeAudioBuffer([new Float32Array(48_000)])) {}

  createBufferSource(): AudioBufferSourceNode {
    const node = new FakeBufferSourceNode();
    this.sources.push(node);
    return node as unknown as AudioBufferSourceNode;
  }

  createGain(): GainNode {
    const node = new FakeGainNode();
    this.gains.push(node);
    return node as unknown as GainNode;
  }

  createStereoPanner(): StereoPannerNode {
    const node = new FakeStereoPannerNode();
    this.panners.push(node);
    return node as unknown as StereoPannerNode;
  }

  async decodeAudioData(bytes: ArrayBuffer): Promise<AudioBuffer> {
    this.decodeCalls.push(bytes.byteLength);
    return this.decodeImpl(bytes);
  }
}

function synthHarmonics(
  sampleRate: number,
  seconds: number,
  partials: readonly { hz: number; amplitude: number }[]
): Float32Array {
  const length = Math.round(seconds * sampleRate);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    let sample = 0;
    const t = i / sampleRate;
    for (const partial of partials) {
      sample += partial.amplitude * Math.sin(2 * Math.PI * partial.hz * t);
    }
    out[i] = sample;
  }
  return out;
}

describe('sampler public module surface', () => {
  it('exposes the exact 6 runtime bindings with no leaked internal helpers', () => {
    expect(Object.keys(samplerModule).sort()).toEqual([
      'SampleBank',
      'chooseZone',
      'measureTuningCents',
      'playSample',
      'playbackRate',
      'vcslRootMidi',
    ]);
    expect(typeof SampleBank).toBe('function');
    expect(typeof chooseZone).toBe('function');
    expect(typeof measureTuningCents).toBe('function');
    expect(typeof playSample).toBe('function');
    expect(typeof playbackRate).toBe('function');
    expect(typeof vcslRootMidi).toBe('function');
  });
});

describe('vcslRootMidi, chooseZone, and playbackRate', () => {
  it('shifts VCSL note names one octave above scientific pitch notation and rejects malformed names', () => {
    expect(vcslRootMidi('C3')).toBe(60);
    expect(vcslRootMidi('A3')).toBe(69);
    expect(vcslRootMidi('Bb2')).toBe(58);
    expect(vcslRootMidi('Cb3')).toBe(59);
    expect(vcslRootMidi('F#1')).toBe(42);
    expect(vcslRootMidi('C-1')).toBe(12);
    expect(() => vcslRootMidi('H3')).toThrow(/not a note/);
  });

  it('throws the exact error message when an instrument has zero zones', () => {
    expect(() => chooseZone([], 60, 0, 0)).toThrow('an instrument needs at least one sample');
  });

  it('breaks rootMidi ties toward the lower root regardless of zone array order', () => {
    const ascending: readonly SampleZone[] = [
      { file: 'low.wav', rootMidi: 60, layer: 0, roundRobin: 0 },
      { file: 'high.wav', rootMidi: 64, layer: 0, roundRobin: 0 },
    ];
    const descending: readonly SampleZone[] = [ascending[1]!, ascending[0]!];

    expect(chooseZone(ascending, 62, 0, 0)).toEqual({ zone: ascending[0], semitones: 2 });
    expect(chooseZone(descending, 62, 0, 0)).toEqual({ zone: ascending[0], semitones: 2 });
    expect(descending.map((z) => z.file)).toEqual(['high.wav', 'low.wav']);
  });

  it('breaks velocity layer ties toward the lower layer regardless of zone array order', () => {
    const ascending: readonly SampleZone[] = [
      { file: 'layer-1.wav', rootMidi: 60, layer: 1, roundRobin: 0 },
      { file: 'layer-3.wav', rootMidi: 60, layer: 3, roundRobin: 0 },
    ];
    const descending: readonly SampleZone[] = [ascending[1]!, ascending[0]!];

    expect(chooseZone(ascending, 60, 2, 0)).toEqual({ zone: ascending[0], semitones: 0 });
    expect(chooseZone(descending, 60, 2, 0)).toEqual({ zone: ascending[0], semitones: 0 });
  });

  it('sorts roundRobin ascending and wraps negative and floating-point counters modulo pool length', () => {
    const unsortedPool: readonly SampleZone[] = [
      { file: 'rr-20.wav', rootMidi: 60, layer: 0, roundRobin: 20 },
      { file: 'rr-5.wav', rootMidi: 60, layer: 0, roundRobin: 5 },
      { file: 'rr-10.wav', rootMidi: 60, layer: 0, roundRobin: 10 },
    ];

    expect(chooseZone(unsortedPool, 60, 0, 0).zone.file).toBe('rr-5.wav');
    expect(chooseZone(unsortedPool, 60, 0, 1.9).zone.file).toBe('rr-10.wav');
    expect(chooseZone(unsortedPool, 60, 0, 2.01).zone.file).toBe('rr-20.wav');
    expect(chooseZone(unsortedPool, 60, 0, 3).zone.file).toBe('rr-5.wav');
    expect(chooseZone(unsortedPool, 60, 0, -0.1).zone.file).toBe('rr-20.wav');
    expect(chooseZone(unsortedPool, 60, 0, -1).zone.file).toBe('rr-20.wav');
    expect(chooseZone(unsortedPool, 60, 0, -2).zone.file).toBe('rr-10.wav');
    expect(chooseZone(unsortedPool, 60, 0, -3).zone.file).toBe('rr-5.wav');
    expect(chooseZone(unsortedPool, 60, 0, -4).zone.file).toBe('rr-20.wav');
  });

  it('returns semitones 0 for unpitched zones and prefers pitched zones when mixed', () => {
    const unpitched: readonly SampleZone[] = [
      { file: 'hit-soft.wav', rootMidi: null, layer: 0, roundRobin: 1 },
      { file: 'hit-loud-b.wav', rootMidi: null, layer: 2, roundRobin: 2 },
      { file: 'hit-loud-a.wav', rootMidi: null, layer: 2, roundRobin: 0 },
    ];
    expect(chooseZone(unpitched, 84, 2, 0)).toEqual({ zone: unpitched[2], semitones: 0 });
    expect(chooseZone(unpitched, 84, 2, 1)).toEqual({ zone: unpitched[1], semitones: 0 });

    const mixed: readonly SampleZone[] = [
      { file: 'noise.wav', rootMidi: null, layer: 0, roundRobin: 0 },
      { file: 'pitched.wav', rootMidi: 48, layer: 2, roundRobin: 0 },
    ];
    expect(chooseZone(mixed, 60, 0, 0)).toEqual({ zone: mixed[1], semitones: 12 });
  });

  it('computes equal-tempered playbackRate from semitones minus centsOffset', () => {
    expect(playbackRate(0, 0)).toBe(1);
    expect(playbackRate(12, 0)).toBe(2);
    expect(playbackRate(-12, 0)).toBe(0.5);
    expect(playbackRate(1, 100)).toBe(1);
    expect(playbackRate(-1, -100)).toBe(1);
    expect(playbackRate(3, -25)).toBeCloseTo(2 ** (3 / 12 + 25 / 1200), 12);
  });
});

describe('measureTuningCents', () => {
  const SR = 48_000;

  it('returns null when peak amplitude is at or below 1e-6 or empty', () => {
    expect(measureTuningCents([], SR, 440)).toBeNull();
    const atThreshold = new Float32Array(SR).fill(1e-6);
    expect(measureTuningCents(atThreshold, SR, 440)).toBeNull();
    const negThreshold = new Float32Array(SR).fill(-1e-6);
    expect(measureTuningCents(negThreshold, SR, 440)).toBeNull();

    const aboveThreshold = synthHarmonics(SR, 1.0, [{ hz: 440 * 2 ** (7 / 1200), amplitude: 2e-6 }]);
    const measured = measureTuningCents(aboveThreshold, SR, 440);
    expect(measured).not.toBeNull();
    expect(measured!).toBeCloseTo(7, 0);
  });

  it('returns null when the analyzed window is shorter than 1024 samples and succeeds at 1024 samples', () => {
    const fullWindow = new Float32Array(1024);
    for (let i = 0; i < fullWindow.length; i++) {
      fullWindow[i] = 0.5 * Math.cos((2 * Math.PI * 1500 * i) / SR);
    }
    expect(measureTuningCents(fullWindow.subarray(0, 1023), SR, 1500)).toBeNull();
    const exactMin = measureTuningCents(fullWindow, SR, 1500);
    expect(exactMin).not.toBeNull();
    expect(Math.abs(exactMin!)).toBeLessThan(1);
  });

  it('skips 0.3 s past onset when at least 0.25 s remains, and falls back to onset when shorter', () => {
    const longSample = new Float32Array(Math.round(0.7 * SR));
    const attack = synthHarmonics(SR, 0.3, [{ hz: 440 * 2 ** (32 / 1200), amplitude: 0.6 }]);
    const sustain = synthHarmonics(SR, 0.4, [{ hz: 440 * 2 ** (-12 / 1200), amplitude: 0.6 }]);
    longSample.set(attack, 0);
    longSample.set(sustain, attack.length);
    const skippedAttack = measureTuningCents(longSample, SR, 440);
    expect(skippedAttack).not.toBeNull();
    expect(skippedAttack!).toBeCloseTo(-12, 0);

    // Sub-threshold pre-roll (< 0.1 * peak) at -35 cents, then 0.35 s onset at +16 cents whose tail after +0.3 s is silent
    const preRoll = synthHarmonics(SR, 0.2, [{ hz: 440 * 2 ** (-35 / 1200), amplitude: 0.04 }]);
    const body = synthHarmonics(SR, 0.3, [{ hz: 440 * 2 ** (16 / 1200), amplitude: 0.8 }]);
    const quietTail = new Float32Array(Math.round(0.05 * SR));
    const shortAfterSkip = new Float32Array(preRoll.length + body.length + quietTail.length);
    shortAfterSkip.set(preRoll, 0);
    shortAfterSkip.set(body, preRoll.length);
    shortAfterSkip.set(quietTail, preRoll.length + body.length);

    const fallbackToOnset = measureTuningCents(shortAfterSkip, SR, 440);
    expect(fallbackToOnset).not.toBeNull();
    expect(fallbackToOnset!).toBeCloseTo(16, 0);
  });

  it('weights harmonics 1 to 4 by peak magnitude while excluding harmonics weaker than 0.25 of the strongest', () => {
    const nominal = 440;
    // h1: weight 1.0 at +10 cents; h2: weight 0.5 at +22 cents -> weighted mean = (10 + 11) / 1.5 = +14 cents.
    // h3 has amplitude 0.15 (< 0.25 * 1.0) at -40 cents and must be excluded; h5 is above h=4 and ignored.
    const samples = Array.from(
      synthHarmonics(SR, 1.5, [
        { hz: nominal * 2 ** (10 / 1200), amplitude: 1.0 },
        { hz: 2 * nominal * 2 ** (22 / 1200), amplitude: 0.5 },
        { hz: 3 * nominal * 2 ** (-40 / 1200), amplitude: 0.15 },
        { hz: 5 * nominal * 2 ** (-38 / 1200), amplitude: 2.0 },
      ])
    );

    const cents = measureTuningCents(samples, SR, nominal);
    expect(cents).not.toBeNull();
    expect(cents!).toBeCloseTo(14, 0);
  });

  it('returns null when spectral peaks hit search-window boundary bins or lie above Nyquist', () => {
    const gaussianNote = (fundamentalHz: number): Float64Array => {
      const length = 2048;
      const out = new Float64Array(length);
      out[0] = 0.2;
      const mid = length / 2;
      const sigma = 80;
      for (let i = 1; i < length; i++) {
        const env = Math.exp(-0.5 * ((i - mid) / sigma) ** 2);
        let sum = 0;
        for (let h = 1; h <= 4; h++) {
          sum += (1 / h) * Math.cos((2 * Math.PI * h * fundamentalHz * i) / SR);
        }
        out[i] = 0.5 * env * sum;
      }
      return out;
    };

    expect(measureTuningCents(gaussianNote(440 * 2 ** (80 / 1200)), SR, 440)).toBeNull();
    expect(measureTuningCents(gaussianNote(440 * 2 ** (-80 / 1200)), SR, 440)).toBeNull();
    expect(measureTuningCents(gaussianNote(440 * 2 ** (25 / 1200)), SR, 440)!).toBeCloseTo(25, 0);

    const validTone = synthHarmonics(SR, 1.2, [{ hz: 440, amplitude: 0.5 }]);
    expect(measureTuningCents(validTone, SR, 30_000)).toBeNull();
  });
});

describe('SampleBank', () => {
  const BASE = 'https://example.test/music-sources/';

  it('encodes path segments preserving slashes, calls fetchImpl unbound, skips loaded files, mixes down channels, and reports durations', async () => {
    const SR = 48_000;
    const silentChannel = new Float32Array(SR);
    const detunedChannel = synthHarmonics(SR, 1.0, [{ hz: 440 * 2 ** (12 / 1200), amplitude: 0.8 }]);
    const stereoPitched = makeAudioBuffer([silentChannel, detunedChannel], SR, 1.0);
    const unpitchedSine = makeAudioBuffer([synthHarmonics(SR, 0.5, [{ hz: 440, amplitude: 0.8 }])], SR, 0.5);

    const bySize = new Map<number, AudioBuffer>([
      [11, stereoPitched],
      [22, unpitchedSine],
    ]);
    const ctx = new FakeSamplerContext(async (bytes) => bySize.get(bytes.byteLength)!);

    const requestedUrls: string[] = [];
    const fetchImpl = vi.fn(async function (this: unknown, input: RequestInfo | URL): Promise<Response> {
      if (this !== undefined && this !== globalThis) {
        throw new TypeError('fetch called with bound receiver');
      }
      const url = String(input);
      requestedUrls.push(url);
      const size = url.includes('C%233') ? 11 : 22;
      return new Response(new Uint8Array(size), { status: 200 });
    });

    const bank = new SampleBank(ctx as unknown as BaseAudioContext, BASE, fetchImpl as unknown as typeof fetch);
    const pitchedZone: SampleZone = {
      file: 'vcsl/Pipe Organ/NT5_A#2 & C#3.wav',
      rootMidi: 69,
      layer: 0,
      roundRobin: 0,
    };
    const unpitchedZone: SampleZone = {
      file: 'vcsl/Idiophones/clave hit.wav',
      rootMidi: null,
      layer: 0,
      roundRobin: 0,
    };

    await bank.load([pitchedZone, unpitchedZone]);
    await bank.load([pitchedZone]);

    expect(requestedUrls).toEqual([
      `${BASE}vcsl/Pipe%20Organ/NT5_A%232%20%26%20C%233.wav`,
      `${BASE}vcsl/Idiophones/clave%20hit.wav`,
    ]);
    expect(ctx.decodeCalls).toEqual([11, 22]);

    expect(bank.buffer(pitchedZone.file)).toBe(stereoPitched);
    expect(bank.buffer(unpitchedZone.file)).toBe(unpitchedSine);
    expect(() => bank.buffer('vcsl/not-loaded.wav')).toThrow('sample vcsl/not-loaded.wav was not loaded');

    const measured = bank.tuningCents(pitchedZone.file);
    expect(measured).not.toBeNull();
    expect(measured!).toBeCloseTo(12, 0);
    expect(bank.tuningCents(unpitchedZone.file)).toBeNull();
    expect(bank.tuningCents('vcsl/not-loaded.wav')).toBeNull();

    expect(bank.report()).toEqual([
      { file: pitchedZone.file, tuningCents: measured, seconds: 1.0 },
      { file: unpitchedZone.file, tuningCents: null, seconds: 0.5 },
    ]);

    expect(bank.nextCount('organ')).toBe(0);
    expect(bank.nextCount('organ')).toBe(1);
    expect(bank.nextCount('clave')).toBe(0);
    expect(bank.nextCount('organ')).toBe(2);
  });

  it('surfaces exact HTTP status and decodeAudioData failure messages for Error and non-Error rejections', async () => {
    const httpCtx = new FakeSamplerContext();
    const failingHttpBank = new SampleBank(
      httpCtx as unknown as BaseAudioContext,
      BASE,
      (async () => new Response('missing', { status: 404 })) as unknown as typeof fetch
    );
    const zone: SampleZone = { file: 'vcsl/missing.wav', rootMidi: 60, layer: 0, roundRobin: 0 };
    await expect(failingHttpBank.load([zone])).rejects.toThrow('sample vcsl/missing.wav unavailable (404)');
    expect(() => failingHttpBank.buffer(zone.file)).toThrow('sample vcsl/missing.wav was not loaded');

    const errorDecodeCtx = new FakeSamplerContext(async () => {
      throw new Error('corrupt RIFF chunk');
    });
    const errorDecodeBank = new SampleBank(
      errorDecodeCtx as unknown as BaseAudioContext,
      BASE,
      (async () => new Response(new Uint8Array(8), { status: 200 })) as unknown as typeof fetch
    );
    await expect(errorDecodeBank.load([zone])).rejects.toThrow(
      'sample vcsl/missing.wav could not be decoded: corrupt RIFF chunk'
    );

    const stringDecodeCtx = new FakeSamplerContext(async () => {
      throw 'unsupported encoding';
    });
    const stringDecodeBank = new SampleBank(
      stringDecodeCtx as unknown as BaseAudioContext,
      BASE,
      (async () => new Response(new Uint8Array(8), { status: 200 })) as unknown as typeof fetch
    );
    await expect(stringDecodeBank.load([zone])).rejects.toThrow(
      'sample vcsl/missing.wav could not be decoded: unsupported encoding'
    );
  });
});

describe('playSample', () => {
  const BASE = 'https://example.test/music-sources/';

  it('advances round-robin counter, applies tuning-corrected rate, clamps pan and negative timeSec, and wires source -> env -> panner -> destination', async () => {
    const SR = 48_000;
    const detunedA4 = makeAudioBuffer(
      [synthHarmonics(SR, 2.0, [{ hz: 440 * 2 ** (15 / 1200), amplitude: 0.6 }])],
      SR,
      4.0
    );
    const ctx = new FakeSamplerContext(async () => detunedA4);
    const bank = new SampleBank(
      ctx as unknown as BaseAudioContext,
      BASE,
      (async () => new Response(new Uint8Array(8), { status: 200 })) as unknown as typeof fetch
    );

    const instrument: Instrument = {
      id: 'vibes',
      zones: [
        { file: 'vibes-rr0.wav', rootMidi: 69, layer: 0, roundRobin: 0 },
        { file: 'vibes-rr1.wav', rootMidi: 69, layer: 0, roundRobin: 1 },
      ],
      attackSec: 0.04,
      releaseSec: 0.3,
      gainDb: -3,
    };
    await bank.load(instrument.zones);
    const cents = bank.tuningCents('vibes-rr0.wav')!;
    expect(cents).toBeCloseTo(15, 0);

    const destination = new FakeAudioNode('destination');
    const firstNote: SampleNote = {
      timeSec: -0.5,
      midi: 72,
      durationSec: 1.2,
      gainDb: -3,
      pan: -2.4,
      layer: 0,
    };
    const first = playSample(
      ctx as unknown as BaseAudioContext,
      bank,
      instrument,
      destination as unknown as AudioNode,
      firstNote
    );

    const expectedRate = playbackRate(3, cents);
    expect(first).toEqual({ file: 'vibes-rr0.wav', rate: expectedRate });

    const source = ctx.sources[0]!;
    const env = ctx.gains[0]!;
    const panner = ctx.panners[0]!;
    expect(source.buffer).toBe(detunedA4);
    expect(source.playbackRate.value).toBe(expectedRate);
    expect(panner.pan.value).toBe(-1);

    const expectedPeak = 10 ** (-6 / 20);
    expect(env.gain.events).toEqual([
      { kind: 'set', value: 0, time: 0 },
      { kind: 'linear', value: expectedPeak, time: 0.04 },
      { kind: 'set', value: expectedPeak, time: 1.2 },
      { kind: 'linear', value: 0, time: 1.5 },
    ]);
    expect(source.outputs).toEqual([env]);
    expect(env.outputs).toEqual([panner]);
    expect(panner.outputs).toEqual([destination]);
    expect(source.starts).toEqual([0]);
    expect(source.stops).toEqual([1.5 + 0.01]);

    const second = playSample(
      ctx as unknown as BaseAudioContext,
      bank,
      instrument,
      destination as unknown as AudioNode,
      { timeSec: 2.5, midi: 69, durationSec: 0.01, gainDb: 0, pan: 3.5, layer: 0 }
    );
    expect(second.file).toBe('vibes-rr1.wav');
    expect(ctx.panners[1]!.pan.value).toBe(1);
    // durationSec (0.01) < attackSec (0.04) -> hold floors at attackSec (0.04)
    expect(ctx.gains[1]!.gain.events.map((e) => e.time)).toEqual([2.5, 2.54, 2.54, 2.54 + 0.3]);
    expect(ctx.sources[1]!.stops).toEqual([2.54 + 0.3 + 0.01]);
  });

  it('bounds release to available / 2 and hold to available - release when a short pitched sample plays fast', async () => {
    // 0.8 s unpitched/silent buffer played an octave up (rate = 2) has available = 0.4 s
    const shortBuffer = makeAudioBuffer([new Float32Array(1024)], 48_000, 0.8);
    const ctx = new FakeSamplerContext(async () => shortBuffer);
    const bank = new SampleBank(
      ctx as unknown as BaseAudioContext,
      BASE,
      (async () => new Response(new Uint8Array(8), { status: 200 })) as unknown as typeof fetch
    );

    const instrument: Instrument = {
      id: 'short-pluck',
      zones: [{ file: 'pluck.wav', rootMidi: 60, layer: 0, roundRobin: 0 }],
      attackSec: 0.05,
      releaseSec: 0.5,
      gainDb: 0,
    };
    await bank.load(instrument.zones);

    const destination = new FakeAudioNode('destination');
    const result = playSample(ctx as unknown as BaseAudioContext, bank, instrument, destination as unknown as AudioNode, {
      timeSec: 1.0,
      midi: 72,
      durationSec: 2.0,
      gainDb: 0,
      pan: 0.25,
      layer: 0,
    });

    expect(result).toEqual({ file: 'pluck.wav', rate: 2 });
    expect(ctx.panners[0]!.pan.value).toBe(0.25);

    // available = 0.8 / 2 = 0.4; release = min(0.5, 0.4 / 2) = 0.2; hold = max(0.05, min(2.0, 0.4 - 0.2)) = 0.2
    const envEvents = ctx.gains[0]!.gain.events;
    expect(envEvents).toHaveLength(4);
    expect(envEvents[0]).toEqual({ kind: 'set', value: 0, time: 1.0 });
    expect(envEvents[1]!.time).toBeCloseTo(1.05, 12);
    expect(envEvents[2]!.time).toBeCloseTo(1.2, 12);
    expect(envEvents[3]).toEqual({ kind: 'linear', value: 0, time: 1.4 });
    expect(ctx.sources[0]!.starts).toEqual([1.0]);
    expect(ctx.sources[0]!.stops[0]).toBeCloseTo(1.41, 12);
  });
});
