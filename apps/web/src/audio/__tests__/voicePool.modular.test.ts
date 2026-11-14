import { afterEach, describe, expect, it, vi } from 'vitest';
import { gaussianPulseTrainSeries, periodicWaveCoefficients, schroederPhases, whiteNoise } from '../dsp';
import { beatingFifthHz, dbToGain, harmonicCentroidHz, planckHarmonicAmplitudes, REGIME_DISTANCE_WINDOWS } from '../mappings';
import {
  IDENTITY_PITCHES_HZ,
  IDENTITY_QUAT,
  planVoice,
  yawQuat,
  type AudioSourceInput,
  type Quat,
  type Vec3,
  type VoicePlan,
} from '../voicePlanning';
import * as VoicePoolModule from '../engine/VoicePool';
import { DEFAULT_HARMONIC_CEILING_HZ, VoicePool, type SlotState } from '../engine/VoicePool';

interface ParamEvent {
  readonly type: 'set' | 'target';
  readonly time: number;
  readonly value: number;
  readonly tau: number;
}

/**
 * Spec-faithful AudioParam timeline model covering cancelScheduledValues, setValueAtTime,
 * and setTargetAtTime so tests can assert exact click-free continuity at every timestamp.
 */
class TimelineAudioParam {
  private events: ParamEvent[] = [];
  readonly modInputs: FakeAudioNode[] = [];
  readonly cancelTimes: number[] = [];

  constructor(private readonly defaultValue: number) {}

  get value(): number {
    const last = this.events[this.events.length - 1];
    return last ? last.value : this.defaultValue;
  }

  cancelScheduledValues(time: number): this {
    this.cancelTimes.push(time);
    this.events = this.events.filter((e) => e.time < time);
    return this;
  }

  setValueAtTime(value: number, time: number): this {
    this.insert({ type: 'set', time, value, tau: 0 });
    return this;
  }

  setTargetAtTime(value: number, time: number, tau: number): this {
    this.insert({ type: 'target', time, value, tau });
    return this;
  }

  recordedEvents(): readonly ParamEvent[] {
    return this.events;
  }

  valueAt(time: number): number {
    let from = { time: Number.NEGATIVE_INFINITY, value: this.defaultValue };
    let target: { value: number; tau: number; time: number } | null = null;
    for (const e of this.events) {
      if (e.time > time) break;
      const startValue = target
        ? target.value + (from.value - target.value) * Math.exp(-(e.time - from.time) / target.tau)
        : from.value;
      if (e.type === 'set') {
        from = { time: e.time, value: e.value };
        target = null;
      } else {
        from = { time: e.time, value: startValue };
        target = { value: e.value, tau: e.tau, time: e.time };
      }
    }
    return target
      ? target.value + (from.value - target.value) * Math.exp(-(time - from.time) / target.tau)
      : from.value;
  }

  private insert(event: ParamEvent): void {
    const index = this.events.findIndex((e) => e.time > event.time);
    if (index < 0) this.events.push(event);
    else this.events.splice(index, 0, event);
  }
}

class FakeAudioNode {
  readonly outputs: Array<FakeAudioNode | TimelineAudioParam> = [];
  disconnectCalls = 0;

  constructor(readonly kind: string) {}

  connect(destination: FakeAudioNode | TimelineAudioParam): unknown {
    this.outputs.push(destination);
    if (destination instanceof TimelineAudioParam) {
      destination.modInputs.push(this);
    }
    return destination;
  }

  disconnect(): void {
    this.disconnectCalls++;
    for (const out of this.outputs) {
      if (out instanceof TimelineAudioParam) {
        const idx = out.modInputs.indexOf(this);
        if (idx >= 0) out.modInputs.splice(idx, 1);
      }
    }
    this.outputs.length = 0;
  }
}

class FakeGainNode extends FakeAudioNode {
  readonly gain = new TimelineAudioParam(1);
  constructor() {
    super('gain');
  }
}

class FakeBiquadFilterNode extends FakeAudioNode {
  type: BiquadFilterType = 'lowpass';
  readonly frequency = new TimelineAudioParam(350);
  readonly Q = new TimelineAudioParam(1);
  constructor() {
    super('biquad');
  }
}

class FakePannerNode extends FakeAudioNode {
  panningModel: PanningModelType = 'equalpower';
  distanceModel: DistanceModelType = 'inverse';
  refDistance = 1;
  rolloffFactor = 1;
  coneInnerAngle = 360;
  coneOuterAngle = 360;
  readonly positionX?: TimelineAudioParam;
  readonly positionY?: TimelineAudioParam;
  readonly positionZ?: TimelineAudioParam;
  readonly setPositionCalls: Vec3[] = [];

  constructor(legacyPositionOnly: boolean) {
    super('panner');
    if (!legacyPositionOnly) {
      this.positionX = new TimelineAudioParam(0);
      this.positionY = new TimelineAudioParam(0);
      this.positionZ = new TimelineAudioParam(0);
    }
  }

  setPosition(x: number, y: number, z: number): void {
    this.setPositionCalls.push([x, y, z]);
  }
}

interface FakePeriodicWave {
  readonly real: Float32Array;
  readonly imag: Float32Array;
  readonly options: PeriodicWaveConstraints | undefined;
}

class FakeOscillatorNode extends FakeAudioNode {
  type: OscillatorType = 'sine';
  readonly frequency = new TimelineAudioParam(440);
  readonly periodicWaves: FakePeriodicWave[] = [];
  readonly starts: number[] = [];
  readonly stops: number[] = [];
  throwOnRepeatStop = false;
  onended: (() => void) | null = null;

  constructor() {
    super('oscillator');
  }

  setPeriodicWave(wave: FakePeriodicWave): void {
    this.periodicWaves.push(wave);
  }

  start(when = 0): void {
    this.starts.push(when);
  }

  stop(when = 0): void {
    if (this.throwOnRepeatStop && this.stops.length > 0) {
      throw new Error('InvalidStateError: already stopped');
    }
    this.stops.push(when);
  }
}

interface FakeAudioBuffer {
  readonly numberOfChannels: number;
  readonly length: number;
  readonly sampleRate: number;
  readonly duration: number;
  getChannelData(channel: number): Float32Array;
  copyToChannel(source: Float32Array, channel: number): void;
}

class FakeBufferSourceNode extends FakeAudioNode {
  buffer: FakeAudioBuffer | null = null;
  loop = false;
  readonly starts: Array<{ readonly when: number; readonly offset: number | undefined }> = [];
  readonly stops: number[] = [];
  onended: (() => void) | null = null;

  constructor() {
    super('bufferSource');
  }

  start(when = 0, offset?: number): void {
    this.starts.push({ when, offset });
  }

  stop(when = 0): void {
    this.stops.push(when);
  }
}

interface RecordingContextOptions {
  readonly sampleRate?: number;
  readonly legacyPannerPosition?: boolean;
}

class RecordingAudioContext {
  readonly sampleRate: number;
  currentTime = 0;
  private readonly legacyPannerPosition: boolean;

  readonly gains: FakeGainNode[] = [];
  readonly biquads: FakeBiquadFilterNode[] = [];
  readonly panners: FakePannerNode[] = [];
  readonly oscillators: FakeOscillatorNode[] = [];
  readonly bufferSources: FakeBufferSourceNode[] = [];
  readonly periodicWaves: FakePeriodicWave[] = [];
  readonly buffers: FakeAudioBuffer[] = [];

  constructor(options: RecordingContextOptions = {}) {
    this.sampleRate = options.sampleRate ?? 48000;
    this.legacyPannerPosition = options.legacyPannerPosition ?? false;
  }

  createGain(): FakeGainNode {
    const node = new FakeGainNode();
    this.gains.push(node);
    return node;
  }

  createBiquadFilter(): FakeBiquadFilterNode {
    const node = new FakeBiquadFilterNode();
    this.biquads.push(node);
    return node;
  }

  createPanner(): FakePannerNode {
    const node = new FakePannerNode(this.legacyPannerPosition);
    this.panners.push(node);
    return node;
  }

  createOscillator(): FakeOscillatorNode {
    const node = new FakeOscillatorNode();
    this.oscillators.push(node);
    return node;
  }

  createBufferSource(): FakeBufferSourceNode {
    const node = new FakeBufferSourceNode();
    this.bufferSources.push(node);
    return node;
  }

  createPeriodicWave(
    real: Float32Array,
    imag: Float32Array,
    options?: PeriodicWaveConstraints
  ): FakePeriodicWave {
    const wave: FakePeriodicWave = {
      real: new Float32Array(real),
      imag: new Float32Array(imag),
      options,
    };
    this.periodicWaves.push(wave);
    return wave;
  }

  createBuffer(channels: number, length: number, sampleRate: number): FakeAudioBuffer {
    const channelData = Array.from({ length: channels }, () => new Float32Array(length));
    const buffer: FakeAudioBuffer = {
      numberOfChannels: channels,
      length,
      sampleRate,
      duration: length / sampleRate,
      getChannelData: (c: number) => channelData[c]!,
      copyToChannel: (source: Float32Array, c: number) => channelData[c]!.set(source),
    };
    this.buffers.push(buffer);
    return buffer;
  }

  advanceTo(nextTime: number): void {
    this.currentTime = nextTime;
    for (const osc of this.oscillators) {
      if (osc.stops.some((stopAt) => stopAt <= nextTime) && osc.onended) {
        const cb = osc.onended;
        osc.onended = null;
        cb();
      }
    }
    for (const src of this.bufferSources) {
      if (src.stops.some((stopAt) => stopAt <= nextTime) && src.onended) {
        const cb = src.onended;
        src.onended = null;
        cb();
      }
    }
  }
}

function makeHarness(options: RecordingContextOptions & { capacity?: number; harmonicCeilingHz?: number } = {}) {
  const ctx = new RecordingAudioContext(options);
  const output = new FakeGainNode();
  const reverbInput = new FakeGainNode();
  const capacity = options.capacity ?? 4;
  const pool =
    options.harmonicCeilingHz === undefined
      ? new VoicePool(
          ctx as unknown as BaseAudioContext,
          capacity,
          output as unknown as AudioNode,
          reverbInput as unknown as AudioNode
        )
      : new VoicePool(
          ctx as unknown as BaseAudioContext,
          capacity,
          output as unknown as AudioNode,
          reverbInput as unknown as AudioNode,
          options.harmonicCeilingHz
        );
  return { ctx, output, reverbInput, pool };
}

function makeStarPlan(id: string, overrides: Partial<VoicePlan> = {}): VoicePlan {
  return {
    id,
    kind: 'star',
    isTarget: false,
    evidenceClass: 'simulated',
    levelDb: -20,
    perceptualDistance: 0.25,
    drrDb: 7.5,
    directGain: Math.sqrt(0.85),
    reverbGain: Math.sqrt(0.15),
    distanceLowpassHz: 11500,
    dustLowpassHz: null,
    directionListener: [0, 0, -1],
    star: { teffK: 5772, planckScaleHz: 260.95 },
    pulsar: null,
    uncertainty: null,
    ...overrides,
  };
}

function makePulsarPlan(id: string, pulseRateHz = 29.946923, overrides: Partial<VoicePlan> = {}): VoicePlan {
  return {
    id,
    kind: 'pulsar',
    isTarget: false,
    evidenceClass: 'catalog_matched',
    levelDb: -24,
    perceptualDistance: 0.6,
    drrDb: -3,
    directGain: Math.sqrt(0.33),
    reverbGain: Math.sqrt(0.67),
    distanceLowpassHz: 6200,
    dustLowpassHz: 4800,
    directionListener: [0.6, 0, -0.8],
    star: null,
    pulsar: { pulseRateHz },
    uncertainty: null,
    ...overrides,
  };
}

/**
 * Reads the static nodes pre-allocated for slot `slotIndex`.
 * Each slot allocates 4 gains (sum, level, direct, send), 2 biquads (dust, distance), and 1 panner.
 */
function inspectPreallocatedSlot(ctx: RecordingAudioContext, slotIndex: number) {
  const sum = ctx.gains[slotIndex * 4]!;
  const level = ctx.gains[slotIndex * 4 + 1]!;
  const direct = ctx.gains[slotIndex * 4 + 2]!;
  const send = ctx.gains[slotIndex * 4 + 3]!;
  const dust = ctx.biquads[slotIndex * 2]!;
  const distance = ctx.biquads[slotIndex * 2 + 1]!;
  const panner = ctx.panners[slotIndex]!;
  return { sum, dust, distance, level, direct, send, panner };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('VoicePool module contract and pre-allocation', () => {
  it('exports the exact runtime symbols and default 16 kHz harmonic ceiling', () => {
    expect(Object.keys(VoicePoolModule).sort()).toEqual(['DEFAULT_HARMONIC_CEILING_HZ', 'VoicePool']);
    expect(DEFAULT_HARMONIC_CEILING_HZ).toBe(16000);
  });

  it('pre-allocates shared PeriodicWaves, deterministic pulsar noise, and wired HRTF slots without starting sources', () => {
    const { ctx, output, reverbInput, pool } = makeHarness({ capacity: 3, sampleRate: 24000 });
    expect(pool.capacity).toBe(3);
    expect(pool.states()).toEqual([]);

    // Shared resources: 1 mono 2-second white-noise buffer and 2 PeriodicWaves (pulse + uncertainty)
    expect(ctx.buffers).toHaveLength(1);
    const noiseBuffer = ctx.buffers[0]!;
    expect(noiseBuffer.numberOfChannels).toBe(1);
    expect(noiseBuffer.length).toBe(48000);
    expect(noiseBuffer.sampleRate).toBe(24000);
    const expectedNoise = whiteNoise(48000, 0x9e3779b9, 0.5);
    expect(noiseBuffer.getChannelData(0)).toEqual(expectedNoise);

    expect(ctx.periodicWaves).toHaveLength(2);
    const expectedPulse = periodicWaveCoefficients(gaussianPulseTrainSeries(0.03).cosine);
    const expectedUncertainty = periodicWaveCoefficients([1, 0.5, 0.5]);
    expect(ctx.periodicWaves[0]!.real).toEqual(expectedPulse.real);
    expect(ctx.periodicWaves[0]!.imag).toEqual(expectedPulse.imag);
    expect(ctx.periodicWaves[0]!.options).toEqual({ disableNormalization: true });
    expect(ctx.periodicWaves[1]!.real).toEqual(expectedUncertainty.real);
    expect(ctx.periodicWaves[1]!.imag).toEqual(expectedUncertainty.imag);
    expect(ctx.periodicWaves[1]!.options).toEqual({ disableNormalization: true });

    // No oscillators or buffer sources before apply()
    expect(ctx.oscillators).toHaveLength(0);
    expect(ctx.bufferSources).toHaveLength(0);

    // 3 slots x (4 gains, 2 biquads, 1 panner)
    expect(ctx.gains).toHaveLength(12);
    expect(ctx.biquads).toHaveLength(6);
    expect(ctx.panners).toHaveLength(3);

    for (let i = 0; i < 3; i++) {
      const slot = inspectPreallocatedSlot(ctx, i);
      expect(slot.dust.type).toBe('lowpass');
      expect(slot.dust.Q.valueAt(0)).toBeCloseTo(-3.0103, 4);
      expect(slot.distance.type).toBe('lowpass');
      expect(slot.distance.Q.valueAt(0)).toBeCloseTo(-6.0206, 4);
      expect(slot.level.gain.valueAt(0)).toBe(0);

      expect(slot.panner.panningModel).toBe('HRTF');
      expect(slot.panner.distanceModel).toBe('inverse');
      expect(slot.panner.refDistance).toBe(1);
      expect(slot.panner.rolloffFactor).toBe(0);
      expect(slot.panner.coneInnerAngle).toBe(360);
      expect(slot.panner.coneOuterAngle).toBe(360);

      // Signal graph: sum -> dust -> distance -> level -> (direct -> panner -> output) + (send -> reverbInput)
      expect(slot.sum.outputs).toEqual([slot.dust]);
      expect(slot.dust.outputs).toEqual([slot.distance]);
      expect(slot.distance.outputs).toEqual([slot.level]);
      expect(slot.level.outputs).toEqual([slot.direct, slot.send]);
      expect(slot.direct.outputs).toEqual([slot.panner]);
      expect(slot.panner.outputs).toEqual([output]);
      expect(slot.send.outputs).toEqual([reverbInput]);
    }
  });

  it('handles zero or negative capacity safely and falls back to 16 kHz when harmonicCeilingHz is non-positive or non-finite', () => {
    const zero = makeHarness({ capacity: 0 });
    zero.pool.apply([makeStarPlan('s1')], new Map([['s1', 220]]));
    expect(zero.pool.states()).toEqual([]);
    zero.pool.dispose();

    const negative = makeHarness({ capacity: -2 });
    negative.pool.apply([makeStarPlan('s1')], new Map([['s1', 220]]));
    expect(negative.pool.states()).toEqual([]);
    negative.pool.dispose();

    const baseline = makeHarness({ capacity: 1, sampleRate: 48000 });
    baseline.pool.apply([makeStarPlan('hot', { star: { teffK: 25000, planckScaleHz: 1130 } })], new Map([['hot', 220]]));
    const defaultCentroid = baseline.pool.states()[0]!.centroidHz;

    for (const invalidCeiling of [0, -4000, Number.NaN, Number.POSITIVE_INFINITY]) {
      const fallback = makeHarness({ capacity: 1, sampleRate: 48000, harmonicCeilingHz: invalidCeiling });
      fallback.pool.apply([makeStarPlan('hot', { star: { teffK: 25000, planckScaleHz: 1130 } })], new Map([['hot', 220]]));
      expect(fallback.pool.states()[0]!.centroidHz).toBe(defaultCentroid);
    }
  });
});

describe('VoicePool slot assignment, retention, release, tail reuse, and disposal', () => {
  it('assigns slots in order, keeps incumbent slot indices, truncates past capacity, and falls back to 110 Hz when pitch is omitted', () => {
    const { pool } = makeHarness({ capacity: 2 });
    const a = makeStarPlan('star-a');
    const b = makeStarPlan('star-b', { star: { teffK: 9000, planckScaleHz: 407 } });
    const c = makeStarPlan('star-c');

    pool.apply([a, b, c], new Map([['star-b', 330]]));
    const initialStates: SlotState[] = pool.states();
    expect(initialStates).toHaveLength(2);
    expect(initialStates[0]).toMatchObject({ slot: 0, id: 'star-a', pitchHz: IDENTITY_PITCHES_HZ[0] });
    expect(initialStates[0]!.pitchHz).toBe(110);
    expect(initialStates[1]).toMatchObject({ slot: 1, id: 'star-b', pitchHz: 330 });

    // Reordering selected plans preserves star-b in slot 1 while star-a departs and star-c takes free slot 0
    pool.apply([b, c], new Map([['star-b', 330], ['star-c', 220]]));
    const nextStates = pool.states();
    expect(nextStates).toEqual([
      expect.objectContaining({ slot: 0, id: 'star-c', pitchHz: 220 }),
      expect.objectContaining({ slot: 1, id: 'star-b', pitchHz: 330 }),
    ]);
  });

  it('releases all active voices on apply([]), fades envelopes with 120 ms tau, stops sources at +1.0 s, and disconnects envelope on ended', () => {
    const { ctx, pool } = makeHarness({ capacity: 2 });
    ctx.currentTime = 2.0;
    pool.apply(
      [makeStarPlan('a'), makeStarPlan('b')],
      new Map([
        ['a', 220],
        ['b', 330],
      ])
    );
    expect(pool.states()).toHaveLength(2);
    // Gains 0..7 are the 2 slots; gains 8 and 9 are the per-voice envelopes
    const envA = ctx.gains[8]!;
    const oscA = ctx.oscillators[0]!;
    expect(envA.outputs).toHaveLength(1);
    expect(oscA.starts).toEqual([2.0]);

    // Let voice A settle, then release all at t = 3.0
    ctx.advanceTo(3.0);
    const beforeRelease = envA.gain.valueAt(3.0);
    expect(beforeRelease).toBeGreaterThan(0.99);

    pool.apply([], new Map());
    expect(pool.states()).toEqual([]);
    expect(oscA.stops).toEqual([4.0]);

    // Continuous click-free decay from exact value at t = 3.0 with tau = 0.12 s
    expect(envA.gain.valueAt(3.0)).toBeCloseTo(beforeRelease, 12);
    expect(envA.gain.valueAt(3.12)).toBeCloseTo(beforeRelease * Math.exp(-1), 6);
    expect(envA.disconnectCalls).toBe(0);

    // Advancing past stopAt triggers onended -> disconnects voice envelope from slot sum
    ctx.advanceTo(4.0);
    expect(envA.disconnectCalls).toBe(1);
  });

  it('prefers tail-free slots before slots with fading tails, fast-fades tails on reuse, and glides slot params when reusing', () => {
    const { ctx, pool } = makeHarness({ capacity: 2 });
    const slot0 = inspectPreallocatedSlot(ctx, 0);
    const slot1 = inspectPreallocatedSlot(ctx, 1);

    ctx.currentTime = 1.0;
    pool.apply([makeStarPlan('first', { levelDb: -12, directGain: 0.9, reverbGain: 0.43589 })], new Map([['first', 220]]));
    const firstEnv = ctx.gains[8]!;
    expect(pool.states().map((s) => ({ slot: s.slot, id: s.id }))).toEqual([{ slot: 0, id: 'first' }]);
    // Clean slot assignment lands level immediately at t = 1.0
    expect(slot0.level.gain.valueAt(1.0)).toBeCloseTo(dbToGain(-12), 6);

    // Release 'first' at t = 1.5 -> slot 0 now holds a tail until t = 2.5, while slot 1 has tailCount === 0
    ctx.advanceTo(1.5);
    pool.apply([], new Map());

    ctx.advanceTo(1.6);
    pool.apply([makeStarPlan('second', { levelDb: -18 })], new Map([['second', 275]]));
    // Must pick slot 1 (tail-free) rather than slot 0 (fading tail)
    expect(pool.states().map((s) => ({ slot: s.slot, id: s.id }))).toEqual([{ slot: 1, id: 'second' }]);
    expect(slot1.level.gain.valueAt(1.6)).toBeCloseTo(dbToGain(-18), 6);

    // Now slot 1 is busy and slot 0 is the ONLY free slot (still in tail at t = 1.7 < 2.5).
    // Assigning a second concurrent voice must reuse slot 0, fast-fade firstEnv (tau = 0.012 s),
    // and glide slot 0's shared parameters rather than stepping immediately.
    ctx.advanceTo(1.7);
    const tailValBeforeReuse = firstEnv.gain.valueAt(1.7);
    const slot0LevelBeforeReuse = slot0.level.gain.valueAt(1.7);
    pool.apply(
      [makeStarPlan('second', { levelDb: -18 }), makeStarPlan('third', { levelDb: -30 })],
      new Map([
        ['second', 275],
        ['third', 330],
      ])
    );
    expect(pool.states().map((s) => ({ slot: s.slot, id: s.id }))).toEqual([
      { slot: 0, id: 'third' },
      { slot: 1, id: 'second' },
    ]);
    // Fast tail fade with tau = 0.012 s
    expect(firstEnv.gain.valueAt(1.7)).toBeCloseTo(tailValBeforeReuse, 9);
    expect(firstEnv.gain.valueAt(1.7 + 0.012)).toBeCloseTo(tailValBeforeReuse * Math.exp(-1), 6);
    // Reused slot glides level from previous value toward -30 dB (tau = 0.06 s) instead of jumping at 1.7
    expect(slot0.level.gain.valueAt(1.7)).toBeCloseTo(slot0LevelBeforeReuse, 9);
    expect(slot0.level.gain.valueAt(1.7 + 0.06)).toBeCloseTo(
      dbToGain(-30) + (slot0LevelBeforeReuse - dbToGain(-30)) * Math.exp(-1),
      6
    );

    // After release and past tail stopAt (t = 4.0), slot 0 is clean again so a new voice sets level immediately
    ctx.advanceTo(2.8);
    pool.apply([], new Map());
    ctx.advanceTo(4.0);
    pool.apply([makeStarPlan('fourth', { levelDb: -15 })], new Map([['fourth', 165]]));
    expect(slot0.level.gain.valueAt(4.0)).toBeCloseTo(dbToGain(-15), 6);
  });

  it('disposes active and tailing sources immediately, disconnects panner and send, and tolerates already-stopped nodes', () => {
    const { ctx, pool } = makeHarness({ capacity: 2 });
    const slot0 = inspectPreallocatedSlot(ctx, 0);
    const slot1 = inspectPreallocatedSlot(ctx, 1);

    ctx.currentTime = 1.0;
    pool.apply([makeStarPlan('tailing'), makeStarPlan('active')], new Map([['tailing', 110], ['active', 220]]));
    const oscTailing = ctx.oscillators[0]!;
    const oscActive = ctx.oscillators[1]!;
    oscTailing.throwOnRepeatStop = true;

    // Release 'tailing' at t = 1.2 so it sits in slot 0's tail list (stopAt = 2.2), keeping 'active' live
    ctx.advanceTo(1.2);
    pool.apply([makeStarPlan('active')], new Map([['active', 220]]));
    expect(oscTailing.stops).toEqual([2.2]);

    // Dispose at t = 1.5 must not throw even when oscTailing refuses a second stop(), must stop oscActive at 1.5,
    // and must disconnect both slots' panner and reverb send nodes.
    ctx.advanceTo(1.5);
    expect(() => pool.dispose()).not.toThrow();
    expect(pool.states()).toEqual([]);
    expect(oscActive.stops).toContain(1.5);
    expect(slot0.panner.disconnectCalls).toBe(1);
    expect(slot0.send.disconnectCalls).toBe(1);
    expect(slot1.panner.disconnectCalls).toBe(1);
    expect(slot1.send.disconnectCalls).toBe(1);
  });
});

describe('Planck PeriodicWave synthesis and harmonic ceiling clamping', () => {
  it('synthesizes Schroeder-phased Planck PeriodicWaves, clamps harmonic count by ceiling, Nyquist guard, and MAX_HARMONICS, and caches unchanged tones', () => {
    const { ctx, pool } = makeHarness({ capacity: 1, sampleRate: 48000, harmonicCeilingHz: 4000 });
    const plan = makeStarPlan('vega-like', { star: { teffK: 9600, planckScaleHz: 433.8 } });

    pool.apply([plan], new Map([['vega-like', 300]]));
    const osc = ctx.oscillators[0]!;
    expect(osc.periodicWaves).toHaveLength(1);

    // floor(4000 / 300) = 13 harmonics -> PeriodicWave arrays have length 14 (DC + 13 harmonics)
    const expectedAmps = planckHarmonicAmplitudes(9600, 300, 13)!;
    const expectedCoeffs = periodicWaveCoefficients(
      expectedAmps,
      schroederPhases(Array.from(expectedAmps, (a) => a * a))
    );
    expect(osc.periodicWaves[0]!.real).toEqual(expectedCoeffs.real);
    expect(osc.periodicWaves[0]!.imag).toEqual(expectedCoeffs.imag);
    expect(osc.periodicWaves[0]!.options).toEqual({ disableNormalization: true });
    expect(osc.frequency.valueAt(0)).toBe(300);
    expect(pool.states()[0]!.centroidHz).toBeCloseTo(harmonicCentroidHz(expectedAmps, 300), 9);

    // Re-applying with identical teffK and pitchHz does NOT rebuild the PeriodicWave
    ctx.advanceTo(0.5);
    pool.apply([makeStarPlan('vega-like', { ...plan, levelDb: -16 })], new Map([['vega-like', 300]]));
    expect(osc.periodicWaves).toHaveLength(1);

    // Changing pitchHz or teffK rebuilds the PeriodicWave and glides osc.frequency with tau = 0.06 s
    ctx.advanceTo(1.0);
    pool.apply([makeStarPlan('vega-like', { star: { teffK: 12000, planckScaleHz: 542 } })], new Map([['vega-like', 400]]));
    expect(osc.periodicWaves).toHaveLength(2);
    expect(osc.periodicWaves[1]!.real).toHaveLength(10 + 1); // floor(4000 / 400) = 10 harmonics
    expect(osc.frequency.valueAt(1.0)).toBeCloseTo(300, 6);
    expect(osc.frequency.valueAt(1.0 + 0.06)).toBeCloseTo(400 + (300 - 400) * Math.exp(-1), 6);
  });

  it('enforces the 0.45 * sampleRate Nyquist guard, the 256-harmonic cap, and the 1-harmonic minimum', () => {
    // Low sample rate (8000 Hz): 0.45 * 8000 = 3600 Hz overrides the 16000 Hz ceiling -> floor(3600 / 400) = 9
    const lowRate = makeHarness({ capacity: 1, sampleRate: 8000 });
    lowRate.pool.apply([makeStarPlan('s', { star: { teffK: 10000, planckScaleHz: 452 } })], new Map([['s', 400]]));
    expect(lowRate.ctx.oscillators[0]!.periodicWaves[0]!.real).toHaveLength(9 + 1);

    // Low pitch (50 Hz) at 48 kHz: floor(16000 / 50) = 320 -> capped at MAX_HARMONICS = 256
    const manyHarmonics = makeHarness({ capacity: 1, sampleRate: 48000 });
    manyHarmonics.pool.apply([makeStarPlan('s', { star: { teffK: 10000, planckScaleHz: 452 } })], new Map([['s', 50]]));
    expect(manyHarmonics.ctx.oscillators[0]!.periodicWaves[0]!.real).toHaveLength(256 + 1);

    // Pitch above harmonicCeilingHz: floor(200 / 440) = 0 -> clamped to minimum 1 harmonic
    const minHarmonic = makeHarness({ capacity: 1, sampleRate: 48000, harmonicCeilingHz: 200 });
    minHarmonic.pool.apply([makeStarPlan('s', { star: { teffK: 10000, planckScaleHz: 452 } })], new Map([['s', 440]]));
    expect(minHarmonic.ctx.oscillators[0]!.periodicWaves[0]!.real).toHaveLength(1 + 1);
  });
});

describe('Spatialization, legacy PannerNode fallback, and listener orientation updates', () => {
  it('sets AudioParam positions immediately on clean assignment and glides with 30 ms tau as listener orientation rotates', () => {
    const { ctx, pool } = makeHarness({ capacity: 1 });
    const slot0 = inspectPreallocatedSlot(ctx, 0);

    const source: AudioSourceInput = {
      id: 'sirius',
      kind: 'star',
      directionWorld: [0, 0, -1],
      distancePc: 2.64,
      luminositySolar: 25.4,
      teffK: 9940,
    };
    const planFor = (orientation: Quat) => {
      const out = planVoice(source, {
        distanceWindow: REGIME_DISTANCE_WINDOWS.earth_capture,
        listenerOrientation: orientation,
      });
      if (out.status !== 'audible') throw new Error('expected audible plan');
      return out.plan;
    };

    // Initial orientation (identity): star straight ahead at [0, 0, -1]
    ctx.currentTime = 0;
    pool.apply([planFor(IDENTITY_QUAT)], new Map([['sirius', 220]]));
    expect(slot0.panner.positionX!.valueAt(0)).toBeCloseTo(0, 6);
    expect(slot0.panner.positionY!.valueAt(0)).toBeCloseTo(0, 6);
    expect(slot0.panner.positionZ!.valueAt(0)).toBeCloseTo(-1, 6);

    // Yaw listener +90 deg left about +y: world [0, 0, -1] moves to listener right [+1, 0, 0], gliding with tau = 0.03 s
    ctx.advanceTo(1.0);
    pool.apply([planFor(yawQuat(90))], new Map([['sirius', 220]]));
    expect(slot0.panner.positionX!.valueAt(1.0)).toBeCloseTo(0, 6);
    expect(slot0.panner.positionZ!.valueAt(1.0)).toBeCloseTo(-1, 6);
    expect(slot0.panner.positionX!.valueAt(1.0 + 0.03)).toBeCloseTo(1 - Math.exp(-1), 6);
    expect(slot0.panner.positionZ!.valueAt(1.0 + 0.03)).toBeCloseTo(-Math.exp(-1), 6);
    expect(slot0.panner.setPositionCalls).toEqual([]);
  });

  it('falls back to panner.setPosition(x, y, z) when AudioParam positionX is absent', () => {
    const { ctx, pool } = makeHarness({ capacity: 1, legacyPannerPosition: true });
    const slot0 = inspectPreallocatedSlot(ctx, 0);
    expect(slot0.panner.positionX).toBeUndefined();

    pool.apply([makeStarPlan('legacy', { directionListener: [0.6, 0.8, 0] })], new Map([['legacy', 220]]));
    ctx.advanceTo(0.5);
    pool.apply([makeStarPlan('legacy', { directionListener: [-0.5, 0, -0.866] })], new Map([['legacy', 220]]));

    expect(slot0.panner.setPositionCalls).toEqual([
      [0.6, 0.8, 0],
      [-0.5, 0, -0.866],
    ]);
  });
});

describe('Distance, dust filter, dry/reverb send envelopes, and click-free transitions', () => {
  it('applies Nyquist-clamped dust and distance cutoffs, dry/reverb gains, and 60 ms parameter glides', () => {
    const { ctx, pool } = makeHarness({ capacity: 1, sampleRate: 20000 }); // Nyquist = 10000 Hz
    const slot0 = inspectPreallocatedSlot(ctx, 0);

    // Initial plan: dustLowpassHz null -> Nyquist (10000), distanceLowpassHz 15000 -> clamped to Nyquist (10000)
    ctx.currentTime = 0;
    pool.apply(
      [
        makeStarPlan('s1', {
          levelDb: -20,
          directGain: 0.95,
          reverbGain: 0.31225,
          distanceLowpassHz: 15000,
          dustLowpassHz: null,
        }),
      ],
      new Map([['s1', 220]])
    );
    expect(slot0.dust.frequency.valueAt(0)).toBe(10000);
    expect(slot0.distance.frequency.valueAt(0)).toBe(10000);
    expect(slot0.direct.gain.valueAt(0)).toBeCloseTo(0.95, 6);
    expect(slot0.send.gain.valueAt(0)).toBeCloseTo(0.31225, 6);
    expect(slot0.level.gain.valueAt(0)).toBeCloseTo(dbToGain(-20), 6);

    // Voice attack envelope starts strictly at 0 (click-free) and rises toward 1 with tau = 0.08 s
    const voiceEnv = ctx.gains[4]!;
    expect(voiceEnv.gain.valueAt(0)).toBe(0);
    expect(voiceEnv.gain.valueAt(0.08)).toBeCloseTo(1 - Math.exp(-1), 6);

    // Update at t = 1.0: move farther and behind dust -> glide with tau = 0.06 s
    ctx.advanceTo(1.0);
    pool.apply(
      [
        makeStarPlan('s1', {
          levelDb: -32,
          directGain: 0.3,
          reverbGain: 0.95394,
          distanceLowpassHz: 3500,
          dustLowpassHz: 2500,
        }),
      ],
      new Map([['s1', 220]])
    );
    expect(slot0.dust.frequency.valueAt(1.0)).toBeCloseTo(10000, 6);
    expect(slot0.dust.frequency.valueAt(1.0 + 0.06)).toBeCloseTo(2500 + (10000 - 2500) * Math.exp(-1), 5);
    expect(slot0.distance.frequency.valueAt(1.0 + 0.06)).toBeCloseTo(3500 + (10000 - 3500) * Math.exp(-1), 5);
    expect(slot0.direct.gain.valueAt(1.0 + 0.06)).toBeCloseTo(0.3 + (0.95 - 0.3) * Math.exp(-1), 6);
    expect(slot0.send.gain.valueAt(1.0 + 0.06)).toBeCloseTo(0.95394 + (0.31225 - 0.95394) * Math.exp(-1), 6);
    expect(slot0.level.gain.valueAt(1.0 + 0.06)).toBeCloseTo(
      dbToGain(-32) + (dbToGain(-20) - dbToGain(-32)) * Math.exp(-1),
      6
    );
  });
});

describe('Uncertainty beating-fifth layer and pulsar noise-gate modulation', () => {
  it('spawns, retunes, and smoothly withdraws the beating-fifth uncertainty layer', () => {
    const { ctx, pool } = makeHarness({ capacity: 1 });

    // Start without residual: only 1 oscillator (the star tone)
    ctx.currentTime = 0;
    pool.apply([makeStarPlan('target', { uncertainty: null })], new Map([['target', 200]]));
    expect(ctx.oscillators).toHaveLength(1);

    // Introduce residual beatHz = 2.4 at t = 0.5: spawns lower & upper oscillators on 2 * pitchHz
    ctx.advanceTo(0.5);
    pool.apply([makeStarPlan('target', { uncertainty: { beatHz: 2.4 } })], new Map([['target', 200]]));
    expect(ctx.oscillators).toHaveLength(3);
    const lower = ctx.oscillators[1]!;
    const upper = ctx.oscillators[2]!;
    // Gains: 0..3 slot, 4 voice envelope, 5 uncertainty gain
    const uncGain = ctx.gains[5]!;
    const expectedInitial = beatingFifthHz(400, 2.4);
    expect(lower.periodicWaves).toEqual([ctx.periodicWaves[1]]);
    expect(upper.periodicWaves).toEqual([ctx.periodicWaves[1]]);
    expect(lower.frequency.valueAt(0.5)).toBeCloseTo(expectedInitial.lowerHz, 6);
    expect(upper.frequency.valueAt(0.5)).toBeCloseTo(expectedInitial.upperHz, 6);
    expect(uncGain.gain.valueAt(0.5)).toBe(0);
    expect(uncGain.gain.valueAt(0.5 + 0.08)).toBeCloseTo(0.35 * (1 - Math.exp(-1)), 6);

    // Narrow residual to beatHz = 0.4 at t = 1.5: glides existing oscillators (tau = 0.06 s) without spawning new ones
    ctx.advanceTo(1.5);
    pool.apply([makeStarPlan('target', { uncertainty: { beatHz: 0.4 } })], new Map([['target', 200]]));
    expect(ctx.oscillators).toHaveLength(3);
    const expectedNarrow = beatingFifthHz(400, 0.4);
    expect(upper.frequency.valueAt(1.5)).toBeCloseTo(expectedInitial.upperHz, 6);
    expect(upper.frequency.valueAt(1.5 + 0.06)).toBeCloseTo(
      expectedNarrow.upperHz + (expectedInitial.upperHz - expectedNarrow.upperHz) * Math.exp(-1),
      6
    );

    // Withdraw residual (null) at t = 2.0: fades layer gain to 0 (tau = 0.12 s), stops at t = 3.0, disconnects gain on ended
    ctx.advanceTo(2.0);
    const gainAtWithdraw = uncGain.gain.valueAt(2.0);
    pool.apply([makeStarPlan('target', { uncertainty: null })], new Map([['target', 200]]));
    expect(lower.stops).toEqual([3.0]);
    expect(upper.stops).toEqual([3.0]);
    expect(uncGain.gain.valueAt(2.0 + 0.12)).toBeCloseTo(gainAtWithdraw * Math.exp(-1), 6);
    expect(uncGain.disconnectCalls).toBe(0);
    ctx.advanceTo(3.0);
    expect(uncGain.disconnectCalls).toBe(1);
  });

  it('gates broadband noise with the Gaussian pulse wave at exact spin rates (no glide) and handles layer switching', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.25);
    const { ctx, pool } = makeHarness({ capacity: 1 });
    const expectedSeries = gaussianPulseTrainSeries(0.03);

    ctx.currentTime = 1.0;
    pool.apply([makePulsarPlan('crab', 29.946923)], new Map([['crab', 165]]));
    expect(pool.states()).toEqual([{ slot: 0, id: 'crab', pitchHz: 165, centroidHz: null }]);
    expect(ctx.bufferSources).toHaveLength(1);
    expect(ctx.oscillators).toHaveLength(1);

    const noiseSrc = ctx.bufferSources[0]!;
    const envOsc = ctx.oscillators[0]!;
    // Gains: 0..3 slot, 4 voice envelope, 5 pulsar vca
    const voiceEnv = ctx.gains[4]!;
    const vca = ctx.gains[5]!;

    expect(noiseSrc.buffer).toBe(ctx.buffers[0]);
    expect(noiseSrc.loop).toBe(true);
    expect(noiseSrc.starts).toEqual([{ when: 1.0, offset: 0.5 }]); // 0.25 * PULSAR_NOISE_SECONDS (2 s)
    expect(vca.gain.valueAt(1.0)).toBeCloseTo(expectedSeries.dc, 9);
    expect(vca.gain.modInputs).toEqual([envOsc]);
    expect(noiseSrc.outputs).toEqual([vca]);
    expect(vca.outputs).toEqual([voiceEnv]);
    expect(envOsc.periodicWaves).toEqual([ctx.periodicWaves[0]]);
    expect(envOsc.frequency.valueAt(1.0)).toBeCloseTo(29.946923, 9);

    // Updating spin frequency sets exact rate immediately at t = 2.0 (never glides through intermediate spin rates)
    ctx.advanceTo(2.0);
    pool.apply([makePulsarPlan('crab', 11.19465)], new Map([['crab', 165]]));
    expect(ctx.bufferSources).toHaveLength(1);
    expect(envOsc.frequency.valueAt(2.0)).toBeCloseTo(11.19465, 9);
    expect(envOsc.frequency.recordedEvents().every((e) => e.type === 'set')).toBe(true);

    // Switching the same voice id from pulsar to star stops pulsar nodes at now + 0.02 s and starts star tone
    ctx.advanceTo(3.0);
    pool.apply([makeStarPlan('crab', { star: { teffK: 5772, planckScaleHz: 260.95 } })], new Map([['crab', 165]]));
    expect(noiseSrc.stops).toEqual([3.02]);
    expect(envOsc.stops).toEqual([3.02]);
    expect(pool.states()[0]!.centroidHz).toBeGreaterThan(0);

    // Switching back from star to pulsar stops star oscillator at now + 0.02 s and restores centroidHz: null
    const starOsc = ctx.oscillators[1]!;
    ctx.advanceTo(4.0);
    pool.apply([makePulsarPlan('crab', 29.946923)], new Map([['crab', 165]]));
    expect(starOsc.stops).toEqual([4.02]);
    expect(pool.states()[0]!.centroidHz).toBeNull();
  });

  it('stops all combined star, uncertainty, and pulsar generators on release and skips PeriodicWave when teffK is invalid', () => {
    const { ctx, pool } = makeHarness({ capacity: 2 });
    ctx.currentTime = 0.5;
    const allLayers = makeStarPlan('combo', {
      star: { teffK: 6500, planckScaleHz: 294 },
      pulsar: { pulseRateHz: 15 },
      uncertainty: { beatHz: 1.5 },
    });
    const invalidStar = makeStarPlan('bad-teff', {
      star: { teffK: 0, planckScaleHz: 0 },
    });
    pool.apply([allLayers, invalidStar], new Map([['combo', 220], ['bad-teff', 330]]));

    // bad-teff produces no Planck PeriodicWave and keeps centroidHz 0
    const badOsc = ctx.oscillators[4]!;
    expect(badOsc.periodicWaves).toEqual([]);
    expect(pool.states().find((s) => s.id === 'bad-teff')?.centroidHz).toBe(0);

    // Release combo at t = 1.0 -> stops star.osc, uncertainty.lower, uncertainty.upper, pulsar.noise, pulsar.envelopeOsc at 2.0
    const comboEnv = ctx.gains[8]!;
    ctx.advanceTo(1.0);
    pool.apply([], new Map());
    expect(ctx.oscillators[0]!.stops).toEqual([2.0]);
    expect(ctx.oscillators[1]!.stops).toEqual([2.0]);
    expect(ctx.oscillators[2]!.stops).toEqual([2.0]);
    expect(ctx.oscillators[3]!.stops).toEqual([2.0]);
    expect(ctx.bufferSources[0]!.stops).toEqual([2.0]);
    expect(comboEnv.disconnectCalls).toBe(0);
    ctx.advanceTo(2.0);
    expect(comboEnv.disconnectCalls).toBe(1);
  });
});
