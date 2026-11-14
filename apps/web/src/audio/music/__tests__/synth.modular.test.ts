import { describe, expect, it } from 'vitest';
import { energyNormalize, mulberry32, whiteNoise } from '../../dsp';
import { midiToHz } from '../harmony';
import * as synthModule from '../synth';
import {
  duckUnderKicks,
  pingPongDelay,
  playKick,
  playPad,
  playRiser,
  playSub,
  syntheticHallImpulse,
  type MonoNote,
  type PadNote,
} from '../synth';

type ParamEvent =
  | { readonly type: 'setValueAtTime'; readonly value: number; readonly time: number }
  | { readonly type: 'linearRampToValueAtTime'; readonly value: number; readonly time: number }
  | { readonly type: 'exponentialRampToValueAtTime'; readonly value: number; readonly time: number }
  | {
      readonly type: 'setValueCurveAtTime';
      readonly values: Float32Array;
      readonly snapshot: Float32Array;
      readonly time: number;
      readonly startTime: number;
      readonly duration: number;
    };

class SpyAudioParam {
  private current: number;
  readonly assignedValues: number[] = [];
  readonly events: ParamEvent[] = [];

  constructor(initialValue = 0) {
    this.current = initialValue;
  }

  get value(): number {
    return this.current;
  }

  set value(next: number) {
    this.current = next;
    this.assignedValues.push(next);
  }

  setValueAtTime(value: number, time: number): this {
    this.current = value;
    this.events.push({ type: 'setValueAtTime', value, time });
    return this;
  }

  linearRampToValueAtTime(value: number, time: number): this {
    this.current = value;
    this.events.push({ type: 'linearRampToValueAtTime', value, time });
    return this;
  }

  exponentialRampToValueAtTime(value: number, time: number): this {
    this.current = value;
    this.events.push({ type: 'exponentialRampToValueAtTime', value, time });
    return this;
  }

  setValueCurveAtTime(values: Float32Array, startTime: number, duration: number): this {
    this.events.push({
      type: 'setValueCurveAtTime',
      values,
      snapshot: new Float32Array(values),
      time: startTime,
      startTime,
      duration,
    });
    return this;
  }
}

class SpyPeriodicWave {
  readonly real: Float32Array;
  readonly imag: Float32Array;

  constructor(
    readonly context: SpyAudioContext,
    real: ArrayLike<number>,
    imag: ArrayLike<number>,
    readonly constraints?: PeriodicWaveConstraints
  ) {
    this.real = Float32Array.from(real);
    this.imag = Float32Array.from(imag);
  }
}

class SpyAudioBuffer {
  readonly channels: Float32Array[];
  readonly copies: { readonly channel: number; readonly offset: number; readonly length: number }[] = [];

  constructor(
    readonly numberOfChannels: number,
    readonly length: number,
    readonly sampleRate: number
  ) {
    this.channels = Array.from({ length: numberOfChannels }, () => new Float32Array(length));
  }

  get duration(): number {
    return this.length / this.sampleRate;
  }

  getChannelData(channel: number): Float32Array {
    const data = this.channels[channel];
    if (!data) throw new RangeError(`Channel ${channel} out of range`);
    return data;
  }

  copyToChannel(source: Float32Array, channelNumber: number, bufferOffset = 0): void {
    const target = this.getChannelData(channelNumber);
    target.set(source, bufferOffset);
    this.copies.push({ channel: channelNumber, offset: bufferOffset, length: source.length });
  }
}

type NodeKind = 'destination' | 'gain' | 'oscillator' | 'biquadFilter' | 'stereoPanner' | 'bufferSource' | 'delay';

class SpyAudioNode {
  readonly connections: SpyAudioNode[] = [];

  constructor(
    protected readonly owner: SpyAudioContext,
    readonly kind: NodeKind
  ) {}

  connect(destination: AudioNode): AudioNode {
    const target = destination as unknown as SpyAudioNode;
    this.connections.push(target);
    this.owner.edges.push({ from: this, to: target });
    return destination;
  }
}

class SpyGainNode extends SpyAudioNode {
  readonly gain = new SpyAudioParam(1);

  constructor(owner: SpyAudioContext) {
    super(owner, 'gain');
  }
}

class SpyOscillatorNode extends SpyAudioNode {
  type: OscillatorType = 'sine';
  readonly frequency = new SpyAudioParam(440);
  readonly detune = new SpyAudioParam(0);
  periodicWave: SpyPeriodicWave | null = null;
  readonly starts: number[] = [];
  readonly stops: number[] = [];

  constructor(owner: SpyAudioContext) {
    super(owner, 'oscillator');
  }

  setPeriodicWave(wave: PeriodicWave): void {
    this.periodicWave = wave as unknown as SpyPeriodicWave;
  }

  start(when = 0): void {
    this.starts.push(when);
  }

  stop(when = 0): void {
    this.stops.push(when);
  }
}

class SpyBiquadFilterNode extends SpyAudioNode {
  type: BiquadFilterType = 'lowpass';
  readonly frequency = new SpyAudioParam(350);
  readonly Q = new SpyAudioParam(1);

  constructor(owner: SpyAudioContext) {
    super(owner, 'biquadFilter');
  }
}

class SpyStereoPannerNode extends SpyAudioNode {
  readonly pan = new SpyAudioParam(0);

  constructor(owner: SpyAudioContext) {
    super(owner, 'stereoPanner');
  }
}

class SpyBufferSourceNode extends SpyAudioNode {
  buffer: SpyAudioBuffer | null = null;
  loop = false;
  readonly playbackRate = new SpyAudioParam(1);
  readonly starts: number[] = [];
  readonly stops: number[] = [];

  constructor(owner: SpyAudioContext) {
    super(owner, 'bufferSource');
  }

  start(when = 0): void {
    this.starts.push(when);
  }

  stop(when = 0): void {
    this.stops.push(when);
  }
}

class SpyDelayNode extends SpyAudioNode {
  readonly delayTime = new SpyAudioParam(0);

  constructor(
    owner: SpyAudioContext,
    readonly maxDelayTime = 1
  ) {
    super(owner, 'delay');
  }
}

class SpyAudioContext {
  readonly destination: SpyAudioNode;
  readonly oscillators: SpyOscillatorNode[] = [];
  readonly gains: SpyGainNode[] = [];
  readonly filters: SpyBiquadFilterNode[] = [];
  readonly delays: SpyDelayNode[] = [];
  readonly panners: SpyStereoPannerNode[] = [];
  readonly bufferSources: SpyBufferSourceNode[] = [];
  readonly periodicWaves: SpyPeriodicWave[] = [];
  readonly buffers: SpyAudioBuffer[] = [];
  readonly edges: { readonly from: SpyAudioNode; readonly to: SpyAudioNode }[] = [];

  constructor(readonly sampleRate = 48_000) {
    this.destination = new SpyAudioNode(this, 'destination');
  }

  asBaseContext(): BaseAudioContext {
    return this as unknown as BaseAudioContext;
  }

  asDestination(): AudioNode {
    return this.destination as unknown as AudioNode;
  }

  createGain(): GainNode {
    const node = new SpyGainNode(this);
    this.gains.push(node);
    return node as unknown as GainNode;
  }

  createOscillator(): OscillatorNode {
    const node = new SpyOscillatorNode(this);
    this.oscillators.push(node);
    return node as unknown as OscillatorNode;
  }

  createBiquadFilter(): BiquadFilterNode {
    const node = new SpyBiquadFilterNode(this);
    this.filters.push(node);
    return node as unknown as BiquadFilterNode;
  }

  createStereoPanner(): StereoPannerNode {
    const node = new SpyStereoPannerNode(this);
    this.panners.push(node);
    return node as unknown as StereoPannerNode;
  }

  createBufferSource(): AudioBufferSourceNode {
    const node = new SpyBufferSourceNode(this);
    this.bufferSources.push(node);
    return node as unknown as AudioBufferSourceNode;
  }

  createDelay(maxDelayTime = 1): DelayNode {
    const node = new SpyDelayNode(this, maxDelayTime);
    this.delays.push(node);
    return node as unknown as DelayNode;
  }

  createPeriodicWave(real: ArrayLike<number>, imag: ArrayLike<number>, constraints?: PeriodicWaveConstraints): PeriodicWave {
    const wave = new SpyPeriodicWave(this, real, imag, constraints);
    this.periodicWaves.push(wave);
    return wave as unknown as PeriodicWave;
  }

  createBuffer(numberOfChannels: number, length: number, sampleRate: number): AudioBuffer {
    const buffer = new SpyAudioBuffer(numberOfChannels, length, sampleRate);
    this.buffers.push(buffer);
    return buffer as unknown as AudioBuffer;
  }
}

const dbToLinear = (db: number): number => 10 ** (db / 20);

function expectSamplesEqual(actual: Float32Array, expected: Float32Array): void {
  expect(actual.length).toBe(expected.length);
  for (let i = 0; i < actual.length; i++) {
    if (!Object.is(actual[i], expected[i])) {
      expect({ index: i, actual: actual[i] }).toEqual({ index: i, actual: expected[i] });
    }
  }
}

function expectSamplesDiffer(a: Float32Array, b: Float32Array): void {
  let differs = a.length !== b.length;
  for (let i = 0; !differs && i < a.length; i++) {
    if (!Object.is(a[i], b[i])) differs = true;
  }
  expect(differs).toBe(true);
}

describe('music synth public module surface', () => {
  it('exports the exact 7 runtime functions with no leaked internal helpers', () => {
    expect(Object.keys(synthModule).sort()).toEqual([
      'duckUnderKicks',
      'pingPongDelay',
      'playKick',
      'playPad',
      'playRiser',
      'playSub',
      'syntheticHallImpulse',
    ]);
    expect(synthModule.playPad).toBe(playPad);
    expect(synthModule.playSub).toBe(playSub);
    expect(synthModule.playKick).toBe(playKick);
    expect(synthModule.playRiser).toBe(playRiser);
    expect(synthModule.syntheticHallImpulse).toBe(syntheticHallImpulse);
    expect(synthModule.pingPongDelay).toBe(pingPongDelay);
    expect(synthModule.duckUnderKicks).toBe(duckUnderKicks);
  });
});

describe('playPad and padSaw periodic wave cache', () => {
  it('builds a 32-harmonic 1/n saw wave, detunes [-8, 0, 8] cents with alternating stereo spread, and scales RMS power', () => {
    const ctx = new SpyAudioContext(48_000);
    const note: PadNote = {
      timeSec: 2.5,
      durationSec: 4.0,
      midis: [60, 64, 67],
      gainDb: -12,
      attackSec: 0.8,
      releaseSec: 1.5,
      cutoffFromHz: 320,
      cutoffToHz: 1800,
    };

    playPad(ctx.asBaseContext(), ctx.asDestination(), note);

    expect(ctx.periodicWaves).toHaveLength(1);
    const saw = ctx.periodicWaves[0]!;
    expect(saw.constraints).toBeUndefined();
    expect(saw.real).toHaveLength(33);
    expect(saw.imag).toHaveLength(33);
    expect(Array.from(saw.real)).toEqual(Array.from({ length: 33 }, () => 0));
    expect(saw.imag[0]).toBe(0);
    for (let n = 1; n <= 32; n++) {
      expect(saw.imag[n]).toBeCloseTo(1 / n, 6);
    }

    expect(ctx.filters).toHaveLength(1);
    const filter = ctx.filters[0]!;
    expect(filter.type).toBe('lowpass');
    expect(filter.Q.value).toBeCloseTo(0.6, 12);
    expect(filter.frequency.events).toEqual([
      { type: 'setValueAtTime', value: 320, time: 2.5 },
      { type: 'exponentialRampToValueAtTime', value: 1800, time: 6.5 },
    ]);

    expect(ctx.gains).toHaveLength(1);
    const env = ctx.gains[0]!;
    const expectedPeak = dbToLinear(-12) / Math.sqrt(3 * 3);
    expect(env.gain.events).toHaveLength(4);
    expect(env.gain.events[0]).toEqual({ type: 'setValueAtTime', value: 0, time: 2.5 });
    expect(env.gain.events[1]?.type).toBe('linearRampToValueAtTime');
    expect((env.gain.events[1] as { value: number }).value).toBeCloseTo(expectedPeak, 12);
    expect(env.gain.events[1]?.time).toBeCloseTo(3.3, 12);
    expect(env.gain.events[2]?.type).toBe('setValueAtTime');
    expect((env.gain.events[2] as { value: number }).value).toBeCloseTo(expectedPeak, 12);
    expect(env.gain.events[2]?.time).toBeCloseTo(6.5, 12);
    expect(env.gain.events[3]).toEqual({ type: 'linearRampToValueAtTime', value: 0, time: 8.0 });

    expect(filter.connections).toEqual([env]);
    expect(env.connections).toEqual([ctx.destination]);

    expect(ctx.oscillators).toHaveLength(9);
    expect(ctx.panners).toHaveLength(9);
    const expectedVoices = [
      { midi: 60, detune: -8, pan: -0.6 },
      { midi: 60, detune: 0, pan: 0 },
      { midi: 60, detune: 8, pan: 0.6 },
      { midi: 64, detune: -8, pan: 0.6 },
      { midi: 64, detune: 0, pan: 0 },
      { midi: 64, detune: 8, pan: -0.6 },
      { midi: 67, detune: -8, pan: -0.6 },
      { midi: 67, detune: 0, pan: 0 },
      { midi: 67, detune: 8, pan: 0.6 },
    ];

    expectedVoices.forEach((expected, index) => {
      const osc = ctx.oscillators[index]!;
      const pan = ctx.panners[index]!;
      expect(osc.periodicWave).toBe(saw);
      expect(osc.frequency.value).toBeCloseTo(midiToHz(expected.midi), 9);
      expect(osc.detune.value).toBe(expected.detune);
      expect(pan.pan.value).toBeCloseTo(expected.pan, 12);
      expect(osc.connections).toEqual([pan]);
      expect(pan.connections).toEqual([filter]);
      expect(osc.starts).toEqual([2.5]);
      expect(osc.stops).toHaveLength(1);
      expect(osc.stops[0]).toBeCloseTo(8.02, 12);
    });
  });

  it('reuses padSaw on the same BaseAudioContext, regenerates for a new context, and guards empty midi chords', () => {
    const ctxA = new SpyAudioContext(48_000);
    const ctxB = new SpyAudioContext(44_100);
    const baseNote: PadNote = {
      timeSec: 0,
      durationSec: 1,
      midis: [62],
      gainDb: -6,
      attackSec: 0.1,
      releaseSec: 0.2,
      cutoffFromHz: 400,
      cutoffToHz: 900,
    };

    playPad(ctxA.asBaseContext(), ctxA.asDestination(), baseNote);
    playPad(ctxA.asBaseContext(), ctxA.asDestination(), { ...baseNote, timeSec: 2, midis: [] });
    expect(ctxA.periodicWaves).toHaveLength(1);
    expect(ctxA.oscillators).toHaveLength(3);
    const emptyChordEnv = ctxA.gains[1]!;
    const emptyPeak = (emptyChordEnv.gain.events[1] as { value: number }).value;
    expect(emptyPeak).toBeCloseTo(dbToLinear(-6) / Math.sqrt(3), 12);

    playPad(ctxB.asBaseContext(), ctxB.asDestination(), baseNote);
    playPad(ctxB.asBaseContext(), ctxB.asDestination(), { ...baseNote, timeSec: 1.5 });
    expect(ctxB.periodicWaves).toHaveLength(1);
    expect(ctxB.periodicWaves[0]).not.toBe(ctxA.periodicWaves[0]);
  });
});

describe('playSub', () => {
  it('creates an unnormalized [0, 1, 0.12, 0.04] harmonic wave with 20 ms attack, 250 ms release, and stop at end + 0.27', () => {
    const ctx = new SpyAudioContext(48_000);
    const note: MonoNote = {
      timeSec: 1.25,
      durationSec: 2.0,
      midi: 38,
      gainDb: -9,
    };

    playSub(ctx.asBaseContext(), ctx.asDestination(), note);

    expect(ctx.periodicWaves).toHaveLength(1);
    const wave = ctx.periodicWaves[0]!;
    expect(Array.from(wave.real)).toEqual([0, 0, 0, 0]);
    expect(wave.imag).toHaveLength(4);
    expect(wave.imag[0]).toBe(0);
    expect(wave.imag[1]).toBeCloseTo(1, 6);
    expect(wave.imag[2]).toBeCloseTo(0.12, 6);
    expect(wave.imag[3]).toBeCloseTo(0.04, 6);
    expect(wave.constraints).toEqual({ disableNormalization: true });

    expect(ctx.oscillators).toHaveLength(1);
    const osc = ctx.oscillators[0]!;
    expect(osc.periodicWave).toBe(wave);
    expect(osc.frequency.value).toBeCloseTo(midiToHz(38), 9);
    expect(osc.starts).toEqual([1.25]);
    expect(osc.stops).toHaveLength(1);
    expect(osc.stops[0]).toBeCloseTo(3.52, 12);

    expect(ctx.gains).toHaveLength(1);
    const env = ctx.gains[0]!;
    const peak = dbToLinear(-9);
    expect(env.gain.events).toHaveLength(4);
    expect(env.gain.events[0]).toEqual({ type: 'setValueAtTime', value: 0, time: 1.25 });
    expect((env.gain.events[1] as { value: number }).value).toBeCloseTo(peak, 12);
    expect(env.gain.events[1]?.time).toBeCloseTo(1.27, 12);
    expect((env.gain.events[2] as { value: number }).value).toBeCloseTo(peak, 12);
    expect(env.gain.events[2]?.time).toBeCloseTo(3.25, 12);
    expect(env.gain.events[3]).toEqual({ type: 'linearRampToValueAtTime', value: 0, time: 3.5 });

    expect(osc.connections).toEqual([env]);
    expect(env.connections).toEqual([ctx.destination]);
  });
});

describe('playKick and kickEnvelope cache', () => {
  it('sweeps 150 -> 48 Hz over 110 ms under a smooth normalized 0.5 s curve with a 20 ms tail fade', () => {
    const sampleRate = 48_000;
    const ctx = new SpyAudioContext(sampleRate);

    playKick(ctx.asBaseContext(), ctx.asDestination(), 4.0, -6);

    expect(ctx.oscillators).toHaveLength(1);
    const osc = ctx.oscillators[0]!;
    expect(osc.periodicWave).toBeNull();
    expect(osc.frequency.events).toEqual([
      { type: 'setValueAtTime', value: 150, time: 4.0 },
      { type: 'exponentialRampToValueAtTime', value: 48, time: 4.11 },
    ]);
    expect(osc.starts).toEqual([4.0]);
    expect(osc.stops).toHaveLength(1);
    expect(osc.stops[0]).toBeCloseTo(4.51, 12);

    expect(ctx.gains).toHaveLength(2);
    const [env, level] = ctx.gains as [SpyGainNode, SpyGainNode];
    expect(env.gain.assignedValues).toEqual([0]);
    expect(env.gain.events).toHaveLength(1);
    const curveEvent = env.gain.events[0]!;
    expect(curveEvent.type).toBe('setValueCurveAtTime');
    if (curveEvent.type !== 'setValueCurveAtTime') throw new Error('Expected setValueCurveAtTime');

    expect(curveEvent.startTime).toBe(4.0);
    expect(curveEvent.duration).toBe(0.5);
    const curve = curveEvent.values;
    const n = Math.round(0.5 * sampleRate);
    expect(curve).toHaveLength(n);
    expect(curve[0]).toBe(0);
    expect(curve[n - 1]).toBe(0);

    let maxVal = 0;
    let maxIdx = 0;
    let rawPeak = 0;
    for (let i = 0; i < n; i++) {
      const v = curve[i]!;
      if (v > maxVal) {
        maxVal = v;
        maxIdx = i;
      }
      const t = i / sampleRate;
      rawPeak = Math.max(rawPeak, (1 - Math.exp(-t / 0.0012)) * Math.exp(-t / 0.085));
    }
    expect(maxVal).toBeCloseTo(1, 6);
    const analyticPeakSample = Math.round(0.0012 * Math.log(1 + 0.085 / 0.0012) * sampleRate);
    expect(Math.abs(maxIdx - analyticPeakSample)).toBeLessThanOrEqual(1);

    const fade = Math.round(0.02 * sampleRate);
    const midTailIdx = n - 1 - Math.floor(fade / 2);
    const midTailT = midTailIdx / sampleRate;
    const expectedMidTail =
      (((1 - Math.exp(-midTailT / 0.0012)) * Math.exp(-midTailT / 0.085)) / rawPeak) *
      ((n - 1 - midTailIdx) / fade);
    expect(curve[midTailIdx]).toBeCloseTo(expectedMidTail, 6);

    expect(level.gain.value).toBeCloseTo(dbToLinear(-6), 12);
    expect(osc.connections).toEqual([env]);
    expect(env.connections).toEqual([level]);
    expect(level.connections).toEqual([ctx.destination]);
  });

  it('caches kickEnvelope by sampleRate and recomputes when sampleRate changes', () => {
    const ctx48a = new SpyAudioContext(48_000);
    const ctx48b = new SpyAudioContext(48_000);
    const ctx44 = new SpyAudioContext(44_100);

    playKick(ctx48a.asBaseContext(), ctx48a.asDestination(), 0, 0);
    playKick(ctx48b.asBaseContext(), ctx48b.asDestination(), 1, -3);
    const curve48a = (ctx48a.gains[0]!.gain.events[0] as Extract<ParamEvent, { type: 'setValueCurveAtTime' }>).values;
    const curve48b = (ctx48b.gains[0]!.gain.events[0] as Extract<ParamEvent, { type: 'setValueCurveAtTime' }>).values;
    expect(curve48b).toBe(curve48a);

    playKick(ctx44.asBaseContext(), ctx44.asDestination(), 0, 0);
    const curve44 = (ctx44.gains[0]!.gain.events[0] as Extract<ParamEvent, { type: 'setValueCurveAtTime' }>).values;
    expect(curve44).not.toBe(curve48a);
    expect(curve44).toHaveLength(22_050);
  });
});

describe('playRiser and noiseBuffer cache', () => {
  it('plays looped 4 s stereo seeded white noise through a Q=1.6 bandpass 400 -> 7000 Hz with a two-stage swell', () => {
    const sampleRate = 24_000;
    const ctx = new SpyAudioContext(sampleRate);

    playRiser(ctx.asBaseContext(), ctx.asDestination(), 8.0, 6.0, -15);

    expect(ctx.buffers).toHaveLength(1);
    const buffer = ctx.buffers[0]!;
    const expectedLength = 4 * sampleRate;
    expect(buffer.numberOfChannels).toBe(2);
    expect(buffer.length).toBe(expectedLength);
    expect(buffer.sampleRate).toBe(sampleRate);
    expectSamplesEqual(buffer.getChannelData(0), whiteNoise(expectedLength, 0x5eed1, 0.25));
    expectSamplesEqual(buffer.getChannelData(1), whiteNoise(expectedLength, 0x5eed2, 0.25));
    expectSamplesDiffer(buffer.getChannelData(0), buffer.getChannelData(1));

    expect(ctx.bufferSources).toHaveLength(1);
    const source = ctx.bufferSources[0]!;
    expect(source.buffer).toBe(buffer);
    expect(source.loop).toBe(true);
    expect(source.starts).toEqual([8.0]);
    expect(source.stops).toHaveLength(1);
    expect(source.stops[0]).toBeCloseTo(14.08, 12);

    expect(ctx.filters).toHaveLength(1);
    const band = ctx.filters[0]!;
    expect(band.type).toBe('bandpass');
    expect(band.Q.value).toBeCloseTo(1.6, 12);
    expect(band.frequency.events).toEqual([
      { type: 'setValueAtTime', value: 400, time: 8.0 },
      { type: 'exponentialRampToValueAtTime', value: 7000, time: 14.0 },
    ]);

    expect(ctx.gains).toHaveLength(1);
    const env = ctx.gains[0]!;
    expect(env.gain.events).toHaveLength(4);
    expect(env.gain.events[0]).toEqual({ type: 'setValueAtTime', value: 0, time: 8.0 });
    expect(env.gain.events[1]?.type).toBe('linearRampToValueAtTime');
    expect((env.gain.events[1] as { value: number }).value).toBeCloseTo(dbToLinear(-39), 12);
    expect(env.gain.events[1]?.time).toBeCloseTo(11.0, 12);
    expect(env.gain.events[2]?.type).toBe('linearRampToValueAtTime');
    expect((env.gain.events[2] as { value: number }).value).toBeCloseTo(dbToLinear(-15), 12);
    expect(env.gain.events[2]?.time).toBeCloseTo(14.0, 12);
    expect(env.gain.events[3]).toEqual({ type: 'linearRampToValueAtTime', value: 0, time: 14.06 });

    expect(source.connections).toEqual([band]);
    expect(band.connections).toEqual([env]);
    expect(env.connections).toEqual([ctx.destination]);

    // Second riser on the same context reuses the cached noise buffer; a distinct context allocates its own
    playRiser(ctx.asBaseContext(), ctx.asDestination(), 20.0, 4.0, -18);
    expect(ctx.buffers).toHaveLength(1);
    expect(ctx.bufferSources[1]?.buffer).toBe(buffer);

    const otherCtx = new SpyAudioContext(sampleRate);
    playRiser(otherCtx.asBaseContext(), otherCtx.asDestination(), 0, 2.0, -12);
    expect(otherCtx.buffers).toHaveLength(1);
    expect(otherCtx.buffers[0]).not.toBe(buffer);
  });
});

describe('syntheticHallImpulse, pingPongDelay, and duckUnderKicks', () => {
  it('synthesizes a deterministic two-band stereo hall impulse with 18 ms pre-delay, 4 ms fade-in, and unit mean channel energy', () => {
    const sr = 10_000;
    const ctx = new SpyAudioContext(sr);

    // Default seconds = 3.6 and seed = 0x4a11
    const hallDefault = syntheticHallImpulse(ctx.asBaseContext()) as unknown as SpyAudioBuffer;
    const hallExplicit = syntheticHallImpulse(ctx.asBaseContext(), 3.6, 0x4a11) as unknown as SpyAudioBuffer;
    const hallDifferentSeed = syntheticHallImpulse(ctx.asBaseContext(), 3.6, 0x4a20) as unknown as SpyAudioBuffer;

    expect(hallDefault.numberOfChannels).toBe(2);
    expect(hallDefault.sampleRate).toBe(sr);
    expect(hallDefault.length).toBe(Math.round(3.6 * sr));

    const left = hallDefault.getChannelData(0);
    const right = hallDefault.getChannelData(1);
    expectSamplesEqual(left, hallExplicit.getChannelData(0));
    expectSamplesEqual(right, hallExplicit.getChannelData(1));
    expectSamplesDiffer(left, right);
    expectSamplesDiffer(left, hallDifferentSeed.getChannelData(0));

    const predelay = Math.round(0.018 * sr);
    const fade = Math.round(0.004 * sr);
    for (let i = 0; i < predelay; i++) {
      expect(left[i]).toBe(0);
      expect(right[i]).toBe(0);
    }
    // At i === predelay, (i - predelay) / fade === 0, so the fade-in begins cleanly at zero
    expect(left[predelay]).toBeCloseTo(0, 12);
    expect(right[predelay]).toBeCloseTo(0, 12);

    // Verify exact two-band 2 kHz crossover and 3.4 s / 1.6 s -60 dB decay math normalized via energyNormalize
    const expectedRaw: Float32Array[] = [];
    for (let c = 0; c < 2; c++) {
      const rand = mulberry32(0x4a11 + c);
      const out = new Float32Array(hallDefault.length);
      let low = 0;
      const a = Math.exp((-2 * Math.PI * 2000) / sr);
      for (let i = predelay; i < hallDefault.length; i++) {
        const t = (i - predelay) / sr;
        const white = 2 * rand() - 1;
        low = a * low + (1 - a) * white;
        const high = white - low;
        const value = low * 10 ** ((-3 * t) / 3.4) + high * 10 ** ((-3 * t) / 1.6);
        out[i] = value * Math.min(1, (i - predelay) / fade);
      }
      expectedRaw.push(out);
    }
    const [expectedLeft, expectedRight] = energyNormalize(expectedRaw) as [Float32Array, Float32Array];
    expectSamplesEqual(left, expectedLeft);
    expectSamplesEqual(right, expectedRight);

    let energyLeft = 0;
    let energyRight = 0;
    for (let i = 0; i < hallDefault.length; i++) {
      energyLeft += left[i]! ** 2;
      energyRight += right[i]! ** 2;
    }
    expect((energyLeft + energyRight) / 2).toBeCloseTo(1, 5);
  });

  it('wires pingPongDelay with -0.8 / +0.8 stereo panners, low-pass damping, feedback loop, and wet output gain', () => {
    const ctx = new SpyAudioContext(48_000);
    const nodes = pingPongDelay(ctx.asBaseContext(), {
      delaySec: 0.375,
      feedback: 0.42,
      wetDb: -8,
      lowpassHz: 2400,
    });

    expect(ctx.gains).toHaveLength(3);
    const [input, output, feedback] = ctx.gains as [SpyGainNode, SpyGainNode, SpyGainNode];
    expect(nodes.input).toBe(input);
    expect(nodes.output).toBe(output);
    expect(input.gain.value).toBe(1);
    expect(output.gain.value).toBeCloseTo(dbToLinear(-8), 12);
    expect(feedback.gain.value).toBeCloseTo(0.42, 12);

    expect(ctx.delays).toHaveLength(2);
    const [left, right] = ctx.delays as [SpyDelayNode, SpyDelayNode];
    expect(left.maxDelayTime).toBe(2);
    expect(right.maxDelayTime).toBe(2);
    expect(left.delayTime.value).toBeCloseTo(0.375, 12);
    expect(right.delayTime.value).toBeCloseTo(0.375, 12);

    expect(ctx.filters).toHaveLength(1);
    const damp = ctx.filters[0]!;
    expect(damp.type).toBe('lowpass');
    expect(damp.frequency.value).toBe(2400);

    expect(ctx.panners).toHaveLength(2);
    const [panL, panR] = ctx.panners as [SpyStereoPannerNode, SpyStereoPannerNode];
    expect(panL.pan.value).toBeCloseTo(-0.8, 12);
    expect(panR.pan.value).toBeCloseTo(0.8, 12);

    expect(ctx.edges).toEqual([
      { from: input, to: left },
      { from: left, to: damp },
      { from: damp, to: right },
      { from: right, to: feedback },
      { from: feedback, to: left },
      { from: left, to: panL },
      { from: right, to: panR },
      { from: panL, to: output },
      { from: panR, to: output },
    ]);
  });

  it('schedules ramp-only kick ducking on sorted copies of kickTimes across clamped, close, medium, and wide gaps', () => {
    const emptyParam = new SpyAudioParam(1);
    duckUnderKicks(emptyParam as unknown as AudioParam, [], -5, 0.25);
    expect(emptyParam.events).toEqual([{ type: 'setValueAtTime', value: 1, time: 0 }]);

    const param = new SpyAudioParam(1);
    const callerKickTimes = [1.0, 0.002, 0.58, 0.5];
    const depthDb = -5;
    const releaseSec = 0.25;
    const low = dbToLinear(depthDb);

    duckUnderKicks(param as unknown as AudioParam, callerKickTimes, depthDb, releaseSec);

    // Caller array is never mutated
    expect(callerKickTimes).toEqual([1.0, 0.002, 0.58, 0.5]);

    // 1 initial setValueAtTime(1, 0) + 3 linear ramps per kick * 4 kicks = 13 events
    expect(param.events).toHaveLength(13);
    expect(param.events[0]).toEqual({ type: 'setValueAtTime', value: 1, time: 0 });
    for (const event of param.events.slice(1)) {
      expect(event.type).toBe('linearRampToValueAtTime');
    }

    const expectedRamps = [
      // Kick 1 at t = 0.002 (next = 0.5): t - 0.005 clamps to 0; back = min(0.252, 0.46) = 0.252
      { value: 1, time: 0 },
      { value: low, time: 0.032 },
      { value: 1, time: 0.252 },
      // Kick 2 at t = 0.5 (next = 0.58): back = min(0.75, 0.54) = 0.54 < t + 0.06, clamps to 0.56
      { value: 1, time: 0.495 },
      { value: low, time: 0.53 },
      { value: 1, time: 0.56 },
      // Kick 3 at t = 0.58 (next = 1.0): back = min(0.83, 0.96) = 0.83
      { value: 1, time: 0.575 },
      { value: low, time: 0.61 },
      { value: 1, time: 0.83 },
      // Kick 4 at t = 1.0 (next = Infinity): back = 1.25
      { value: 1, time: 0.995 },
      { value: low, time: 1.03 },
      { value: 1, time: 1.25 },
    ];

    expectedRamps.forEach((expected, idx) => {
      const actual = param.events[idx + 1] as { type: string; value: number; time: number };
      expect(actual.value).toBeCloseTo(expected.value, 12);
      expect(actual.time).toBeCloseTo(expected.time, 12);
    });

    // Medium gap where t + 0.06 < next - 0.04 < t + releaseSec (t = 2.0, next = 2.15, releaseSec = 0.25 -> back = 2.11)
    const mediumParam = new SpyAudioParam(1);
    duckUnderKicks(mediumParam as unknown as AudioParam, [2.0, 2.15], -6, 0.25);
    expect((mediumParam.events[3] as { time: number }).time).toBeCloseTo(2.11, 12);
  });
});
