import { afterEach, describe, expect, it, vi } from 'vitest';
import type { OmnitoneStatic } from 'omnitone';
import { skyglowBedLevelDb } from '../mappings.js';
import {
  DEFAULT_VOICE_CAPACITY,
  IDENTITY_PITCHES_HZ,
  directionFromAzimuthElevation,
  yawQuat,
  type AudioSourceInput,
} from '../voicePlanning.js';
import type { LifecycleHost } from '../engine/audioLifecycle.js';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb.js';
import { MASTER_BUS_SETTINGS } from '../engine/MasterBus.js';
import {
  SpatialAudioEngine,
  type BedSnapshot,
  type EngineSnapshot,
  type SilentOutcome,
  type SpatialAudioEngineOptions,
  type VoiceSnapshot,
} from '../engine/SpatialAudioEngine.js';

interface ParamEvent {
  readonly op: 'setValue' | 'setTarget' | 'linearRamp' | 'expRamp' | 'cancel' | 'curve';
  readonly value?: number;
  readonly time: number;
  readonly timeConstant?: number;
}

class RecordingParam {
  value: number;
  readonly events: ParamEvent[] = [];

  constructor(initial = 0) {
    this.value = initial;
  }

  setValueAtTime(value: number, time: number): this {
    this.value = value;
    this.events.push({ op: 'setValue', value, time });
    return this;
  }

  setTargetAtTime(value: number, time: number, timeConstant: number): this {
    this.value = value;
    this.events.push({ op: 'setTarget', value, time, timeConstant });
    return this;
  }

  linearRampToValueAtTime(value: number, time: number): this {
    this.value = value;
    this.events.push({ op: 'linearRamp', value, time });
    return this;
  }

  exponentialRampToValueAtTime(value: number, time: number): this {
    this.value = value;
    this.events.push({ op: 'expRamp', value, time });
    return this;
  }

  cancelScheduledValues(time: number): this {
    this.events.push({ op: 'cancel', time });
    return this;
  }

  setValueCurveAtTime(values: ArrayLike<number>, time: number): this {
    this.value = values[values.length - 1] ?? 0;
    this.events.push({ op: 'curve', value: this.value, time });
    return this;
  }
}

const AUDIO_PARAM_NAMES = new Set([
  'gain',
  'frequency',
  'detune',
  'Q',
  'pan',
  'threshold',
  'knee',
  'ratio',
  'attack',
  'release',
  'positionX',
  'positionY',
  'positionZ',
  'playbackRate',
  'delayTime',
]);

class RecordingNode {
  [key: string]: unknown;
  readonly outputs: unknown[] = [];
  disconnected = 0;
  readonly startTimes: (number | undefined)[] = [];
  readonly stopTimes: (number | undefined)[] = [];
  periodicWave: unknown = null;
  type = 'sine';
  onended: (() => void) | null = null;

  constructor(readonly kind: string) {
    return new Proxy(this, {
      get(target, prop, receiver) {
        if (typeof prop === 'string' && AUDIO_PARAM_NAMES.has(prop) && !(prop in target)) {
          target[prop] = new RecordingParam(prop === 'gain' ? 1 : 0);
        }
        return Reflect.get(target, prop, receiver);
      },
    });
  }

  param(name: string): RecordingParam {
    const existing = this[name];
    if (existing instanceof RecordingParam) return existing;
    const created = new RecordingParam(name === 'gain' ? 1 : 0);
    this[name] = created;
    return created;
  }

  connect(destination: unknown): unknown {
    this.outputs.push(destination);
    return destination;
  }

  disconnect(): void {
    this.disconnected += 1;
  }

  start(when?: number): void {
    this.startTimes.push(when);
  }

  stop(when?: number): void {
    this.stopTimes.push(when);
  }

  setPeriodicWave(wave: unknown): void {
    this.periodicWave = wave;
  }

  setPosition(): void {}

  getFloatTimeDomainData(buffer: Float32Array): void {
    buffer.fill(0);
  }
}

interface RecordedPeriodicWave {
  readonly real: Float32Array;
  readonly imag: Float32Array;
  readonly options: PeriodicWaveConstraints | undefined;
}

type EventListenerFn = (event?: { readonly type?: string; readonly target?: unknown }) => void;

class FakeEventTarget {
  private readonly map = new Map<string, Set<EventListenerFn>>();

  addEventListener(type: string, listener: EventListenerFn, options?: boolean | AddEventListenerOptions): void {
    const capture = typeof options === 'boolean' ? options : (options?.capture ?? false);
    const key = `${type}|${capture}`;
    let set = this.map.get(key);
    if (!set) {
      set = new Set();
      this.map.set(key, set);
    }
    set.add(listener);
  }

  removeEventListener(type: string, listener: EventListenerFn, options?: boolean | EventListenerOptions): void {
    const capture = typeof options === 'boolean' ? options : (options?.capture ?? false);
    this.map.get(`${type}|${capture}`)?.delete(listener);
  }

  fire(type: string, event?: { readonly type?: string; readonly target?: unknown }): void {
    for (const capture of [true, false]) {
      for (const fn of [...(this.map.get(`${type}|${capture}`) ?? [])]) fn(event);
    }
  }

  listenerCount(type: string): number {
    return (this.map.get(`${type}|true`)?.size ?? 0) + (this.map.get(`${type}|false`)?.size ?? 0);
  }
}

class RecordingAudioContext extends FakeEventTarget {
  readonly sampleRate: number;
  currentTime = 0;
  state: AudioContextState | 'interrupted' = 'running';
  readonly destination = new RecordingNode('destination');
  readonly nodes: RecordingNode[] = [];
  readonly periodicWaves: RecordedPeriodicWave[] = [];
  resumes = 0;
  suspends = 0;
  closes = 0;
  analyserFill = 0;
  limiterReduction = 0;

  constructor(sampleRate = 8000) {
    super();
    this.sampleRate = sampleRate;
  }

  private make(kind: string): RecordingNode {
    const node = new RecordingNode(kind);
    this.nodes.push(node);
    return node;
  }

  byKind(kind: string): RecordingNode[] {
    return this.nodes.filter((n) => n.kind === kind);
  }

  fadeNode(): RecordingNode {
    const fade = this.byKind('gain').find((g) => g.outputs.includes(this.destination));
    if (!fade) throw new Error('expected master fade gain node connected to destination');
    return fade;
  }

  createGain(): RecordingNode {
    return this.make('gain');
  }

  createBiquadFilter(): RecordingNode {
    return this.make('biquad');
  }

  createDynamicsCompressor(): RecordingNode {
    const comp = this.make('compressor');
    Object.defineProperty(comp, 'reduction', {
      get: () => this.limiterReduction,
      configurable: true,
    });
    return comp;
  }

  createWaveShaper(): RecordingNode {
    return this.make('waveshaper');
  }

  createAnalyser(): RecordingNode {
    const analyser = Object.assign(this.make('analyser'), { fftSize: 2048 });
    analyser.getFloatTimeDomainData = (buffer: Float32Array) => {
      buffer.fill(this.analyserFill);
    };
    return analyser;
  }

  createChannelSplitter(): RecordingNode {
    return this.make('splitter');
  }

  createConvolver(): RecordingNode {
    return this.make('convolver');
  }

  createPanner(): RecordingNode {
    return this.make('panner');
  }

  createStereoPanner(): RecordingNode {
    return this.make('stereoPanner');
  }

  createOscillator(): RecordingNode {
    return this.make('oscillator');
  }

  createBufferSource(): RecordingNode {
    return this.make('bufferSource');
  }

  createDelay(): RecordingNode {
    return this.make('delay');
  }

  createPeriodicWave(
    real: Float32Array,
    imag: Float32Array,
    options?: PeriodicWaveConstraints
  ): RecordedPeriodicWave {
    const wave: RecordedPeriodicWave = {
      real: new Float32Array(real),
      imag: new Float32Array(imag),
      options,
    };
    this.periodicWaves.push(wave);
    return wave;
  }

  createBuffer(channels: number, length: number, sampleRate: number) {
    const data = Array.from({ length: channels }, () => new Float32Array(length));
    return {
      numberOfChannels: channels,
      length,
      sampleRate,
      duration: length / sampleRate,
      getChannelData: (c: number) => data[c]!,
      copyToChannel: (source: Float32Array, c: number) => data[c]!.set(source),
    };
  }

  resume(): Promise<void> {
    this.resumes += 1;
    if (this.state !== 'closed') {
      this.state = 'running';
      this.fire('statechange');
    }
    return Promise.resolve();
  }

  suspend(): Promise<void> {
    this.suspends += 1;
    if (this.state === 'running') {
      this.state = 'suspended';
      this.fire('statechange');
    }
    return Promise.resolve();
  }

  close(): Promise<void> {
    this.closes += 1;
    this.state = 'closed';
    this.fire('statechange');
    return Promise.resolve();
  }

  decodeAudioData(): Promise<ReturnType<RecordingAudioContext['createBuffer']>> {
    return Promise.resolve(this.createBuffer(2, 64, this.sampleRate));
  }
}

class FakeLifecycleHost extends FakeEventTarget implements LifecycleHost {
  visibilityState: DocumentVisibilityState = 'visible';
  readonly window = new FakeEventTarget();
  audioSession: { type: string } | undefined = { type: 'auto' };

  hide(): void {
    this.visibilityState = 'hidden';
    this.fire('visibilitychange');
  }

  show(): void {
    this.visibilityState = 'visible';
    this.fire('visibilitychange');
  }
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeFakeOmnitone(initPromise: Promise<void> = Promise.resolve()) {
  const createdRenderers: { input: RecordingNode; output: RecordingNode; initialized: number }[] = [];
  const omnitone = {
    createFOARenderer: () => {
      const input = new RecordingNode('omnitoneInput');
      const output = new RecordingNode('omnitoneOutput');
      const record = { input, output, initialized: 0 };
      createdRenderers.push(record);
      return {
        input: input as unknown as AudioNode,
        output: output as unknown as AudioNode,
        initialize: async () => {
          await initPromise;
          record.initialized += 1;
        },
        setRenderingMode: () => {},
        setRotationMatrix3: () => {},
        setRotationMatrix4: () => {},
      };
    },
  } as unknown as OmnitoneStatic;
  return { omnitone, createdRenderers };
}

const flushMicrotasks = async (turns = 10): Promise<void> => {
  for (let i = 0; i < turns; i++) await Promise.resolve();
};

const makeStar = (id: string, overrides: Partial<AudioSourceInput> = {}): AudioSourceInput => ({
  id,
  kind: 'star',
  directionWorld: [0, 0, -1],
  distancePc: 10,
  luminositySolar: 50,
  teffK: 6500,
  ...overrides,
});

const makePulsar = (id: string, overrides: Partial<AudioSourceInput> = {}): AudioSourceInput => ({
  id,
  kind: 'pulsar',
  directionWorld: [0, 0, -1],
  distancePc: 2000,
  luminositySolar: 1e5,
  spinFrequencyHz: 29.946923,
  ...overrides,
});

function harness(options: SpatialAudioEngineOptions = {}, sampleRate = 8000) {
  const contexts: RecordingAudioContext[] = [];
  const factory = vi.fn(() => {
    const ctx = new RecordingAudioContext(sampleRate);
    contexts.push(ctx);
    return ctx as unknown as AudioContext;
  });
  const engine = new SpatialAudioEngine({
    contextFactory: factory,
    impulseResponseUrl: null,
    bedPreference: 'stereo-diffuse',
    lifecycleHost: null,
    ...options,
  });
  return { engine, factory, contexts };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('SpatialAudioEngine constructor no-context guarantee and pre-start buffering', () => {
  it('never allocates a browser AudioContext before start(), then allocates exactly one and reuses it', () => {
    const constructed = vi.fn();
    class SpyBrowserAudioContext extends RecordingAudioContext {
      constructor() {
        super(8000);
        constructed();
      }
    }
    const winTarget = new FakeEventTarget();
    const docTarget = new FakeEventTarget();
    vi.stubGlobal('window', {
      AudioContext: SpyBrowserAudioContext,
      addEventListener: winTarget.addEventListener.bind(winTarget),
      removeEventListener: winTarget.removeEventListener.bind(winTarget),
    });
    vi.stubGlobal('document', {
      visibilityState: 'visible',
      addEventListener: docTarget.addEventListener.bind(docTarget),
      removeEventListener: docTarget.removeEventListener.bind(docTarget),
    });
    vi.stubGlobal('navigator', {});

    const engine = new SpatialAudioEngine({
      impulseResponseUrl: null,
      bedPreference: 'stereo-diffuse',
    });

    engine.setRegime('cislunar');
    engine.setSources([makeStar('s1')]);
    engine.setSkyBrightness(18.5);
    engine.setUnveil(0.25);
    engine.setListenerOrientation(yawQuat(30));
    engine.setMasterVolume(0.5);
    engine.setReverbReturnDb(-3);
    engine.playDetentClick(920);
    engine.playLockTone(5772, 0.1);
    engine.stop();

    expect(constructed).not.toHaveBeenCalled();
    expect(docTarget.listenerCount('visibilitychange')).toBe(0);
    expect(engine.started).toBe(false);
    expect(engine.enabled).toBe(false);
    expect(engine.context).toBeNull();
    expect(engine.musicInput).toBeNull();
    expect(engine.getOutputLevelDb()).toBe(-Infinity);
    expect(engine.getLimiterReductionDb()).toBe(0);

    expect(engine.start()).toBe(true);
    expect(constructed).toHaveBeenCalledTimes(1);
    expect(docTarget.listenerCount('visibilitychange')).toBe(1);
    expect(winTarget.listenerCount('pagehide')).toBe(1);
    expect(winTarget.listenerCount('pageshow')).toBe(1);

    // Calling start() again reuses the existing AudioContext and does not attach duplicate listeners
    expect(engine.start()).toBe(true);
    expect(constructed).toHaveBeenCalledTimes(1);
    expect(docTarget.listenerCount('visibilitychange')).toBe(1);

    engine.dispose();
    expect(docTarget.listenerCount('visibilitychange')).toBe(0);
    expect(winTarget.listenerCount('pagehide')).toBe(0);
    expect(winTarget.listenerCount('pageshow')).toBe(0);
  });

  it('re-exports public snapshot and option types cleanly and starts idle before user gesture', () => {
    const { engine, factory } = harness();
    expect(engine.enabled).toBe(false);
    expect(engine.started).toBe(false);
    expect(engine.context).toBeNull();
    expect(engine.musicInput).toBeNull();
    expect(engine.getRegime()).toBe('earth_capture');
    expect(engine.getOutputLevelDb()).toBe(-Infinity);
    expect(engine.getLimiterReductionDb()).toBe(0);
    expect(factory).not.toHaveBeenCalled();

    const snap: EngineSnapshot = engine.getSnapshot();
    const bed: BedSnapshot = snap.bed;
    const voices: readonly VoiceSnapshot[] = snap.voices;
    const silent: readonly SilentOutcome[] = snap.silent;
    expect(snap.contextState).toBe('none');
    expect(snap.enabled).toBe(false);
    expect(snap.regime).toBe('earth_capture');
    expect(snap.reverb).toEqual({ status: 'idle', error: null });
    expect(bed).toEqual({
      renderer: 'none',
      levelDb: null,
      silentBecause: 'skyBrightnessMagArcsec2 missing',
      fallbackReason: null,
    });
    expect(voices).toEqual([]);
    expect(snap.notSelected).toEqual([]);
    expect(silent).toEqual([]);
  });

  it('buffers sources, regime, skyBrightness, unveil, masterVolume (including 0), orientation, and bedPreference before start()', async () => {
    const { omnitone } = makeFakeOmnitone();
    const loadOmnitone = vi.fn(async () => ({ default: omnitone }));
    const { engine, factory, contexts } = harness({
      capacity: 2.9, // floors to 2
      bedPreference: 'stereo-diffuse',
      loadOmnitone,
    });

    const callerArray: AudioSourceInput[] = [
      makeStar('bright', { luminositySolar: 200, directionWorld: directionFromAzimuthElevation(0, 0) }),
      makeStar('mid', { luminositySolar: 50, directionWorld: directionFromAzimuthElevation(90, 0) }),
      makeStar('dimmer', { luminositySolar: 10 }),
      makeStar('missing-teff', { teffK: null }),
    ];
    engine.setSources(callerArray);
    // Mutating the caller's array after setSources must not affect buffered sources
    callerArray.length = 0;

    engine.setListenerOrientation(yawQuat(90));
    engine.setSkyBrightness(18.5);
    engine.setUnveil(0.4);
    engine.setMasterVolume(0); // explicit 0 mute must not be treated as missing/null
    engine.setMasterVolume(Number.NaN); // ignored without clobbering 0
    engine.setBedPreference('omnitone-foa');

    expect(factory).not.toHaveBeenCalled();
    expect(loadOmnitone).not.toHaveBeenCalled();

    const preStart = engine.getSnapshot();
    expect(preStart.contextState).toBe('none');
    expect(preStart.enabled).toBe(false);
    expect(preStart.voices.map((v) => v.id)).toEqual(['bright', 'mid']);
    expect(preStart.voices[0]!.slot).toBeNull();
    expect(preStart.voices[0]!.pitchHz).not.toBeNull();
    expect(preStart.voices[0]!.centroidHz).toBeNull();
    // Yaw +90 deg left rotates frontal [0, 0, -1] to [+1, 0, 0] in listener coordinates
    expect(preStart.voices[0]!.directionListener[0]).toBeCloseTo(1, 5);
    expect(preStart.voices[0]!.directionListener[2]).toBeCloseTo(0, 5);
    expect(preStart.notSelected).toEqual(['dimmer']);
    expect(preStart.silent).toEqual([
      { status: 'silent', id: 'missing-teff', reason: 'missing_input', missing: ['teffK'] },
    ]);
    const expectedBedDb = skyglowBedLevelDb(18.5, 0.4);
    expect(expectedBedDb).not.toBeNull();
    expect(preStart.bed).toEqual({
      renderer: 'none',
      levelDb: expectedBedDb,
      silentBecause: null,
      fallbackReason: null,
    });

    // Regime gating works before start() too
    engine.setRegime('cislunar');
    expect(engine.getRegime()).toBe('cislunar');
    expect(engine.getSnapshot().bed).toMatchObject({
      levelDb: -Infinity,
      silentBecause: 'skyglow is heard only in the earth_capture regime',
    });
    engine.setRegime('earth_capture');

    // Now start() inside user gesture: all buffered state materializes immediately
    expect(engine.start()).toBe(true);
    expect(factory).toHaveBeenCalledTimes(1);
    const ctx = contexts[0]!;
    expect(engine.started).toBe(true);
    expect(engine.enabled).toBe(true);
    expect(engine.context).toBe(ctx);
    expect(engine.musicInput).not.toBeNull();

    // Master volume 0 buffered before start() was glided on the volume GainNode (which initialized to defaultVolume 0.85)
    const volumeNodes = ctx
      .byKind('gain')
      .filter(
        (g) =>
          g.param('gain').events.some((e) => e.op === 'setValue' && e.value === MASTER_BUS_SETTINGS.defaultVolume) &&
          g.param('gain').events.some((e) => e.op === 'setTarget' && e.value === 0)
      );
    expect(volumeNodes).toHaveLength(1);

    // Updating master volume while live clamps to [0, 1]
    engine.setMasterVolume(1.7);
    expect(volumeNodes[0]!.param('gain').value).toBe(1);

    await flushMicrotasks();
    expect(loadOmnitone).toHaveBeenCalledTimes(1);
    const postStart = engine.getSnapshot();
    expect(postStart.contextState).toBe('running');
    expect(postStart.enabled).toBe(true);
    expect(postStart.bed).toEqual({
      renderer: 'omnitone-foa',
      levelDb: expectedBedDb,
      silentBecause: null,
      fallbackReason: null,
    });
    expect(postStart.voices.map((v) => v.slot)).toEqual([0, 1]);
    expect(postStart.voices.map((v) => v.pitchHz)).toEqual(preStart.voices.map((v) => v.pitchHz));
    expect(postStart.voices[0]!.centroidHz).toBeGreaterThan(0);
  });

  it('sanitizes invalid skyBrightness, unveil, orientation, regime, and capacity inputs', () => {
    const { engine } = harness({ capacity: 0 });
    engine.setSources([makeStar('s1', { luminositySolar: 100 }), makeStar('s2', { luminositySolar: 20 })]);
    // capacity: 0 clamps to at least 1
    expect(engine.getSnapshot().voices).toHaveLength(1);
    expect(engine.getSnapshot().notSelected).toEqual(['s2']);

    // Default capacity when omitted is DEFAULT_VOICE_CAPACITY (8)
    const defaultCapEngine = harness().engine;
    defaultCapEngine.setSources(Array.from({ length: 12 }, (_, i) => makeStar(`star-${i}`, { luminositySolar: 10 + i })));
    expect(defaultCapEngine.getSnapshot().voices).toHaveLength(DEFAULT_VOICE_CAPACITY);
    expect(defaultCapEngine.getSnapshot().notSelected).toHaveLength(12 - DEFAULT_VOICE_CAPACITY);

    // Non-finite skyBrightness becomes null
    engine.setSkyBrightness(18);
    expect(engine.getSnapshot().bed.levelDb).not.toBeNull();
    engine.setSkyBrightness(Number.POSITIVE_INFINITY);
    expect(engine.getSnapshot().bed).toMatchObject({
      levelDb: null,
      silentBecause: 'skyBrightnessMagArcsec2 missing',
    });

    // Pristine sky (22.0 mag/arcsec^2 or darker) has zero artificial skyglow
    engine.setSkyBrightness(22.2);
    expect(engine.getSnapshot().bed).toMatchObject({
      levelDb: -Infinity,
      silentBecause: 'no audible artificial skyglow at this brightness',
    });

    // Unveil >= 1 clamps to 1 and reports sky fully unveiled; negative clamps to 0; NaN resets unveil to 0
    engine.setSkyBrightness(18.0);
    const fullGlowDb = skyglowBedLevelDb(18.0, 0);
    engine.setUnveil(-0.5);
    expect(engine.getSnapshot().bed.levelDb).toBe(fullGlowDb);
    engine.setUnveil(1.5);
    expect(engine.getSnapshot().bed).toMatchObject({
      levelDb: -Infinity,
      silentBecause: 'sky fully unveiled',
    });
    engine.setUnveil(Number.NaN);
    expect(engine.getSnapshot().bed.levelDb).toBe(fullGlowDb);
    expect(engine.getSnapshot().bed.silentBecause).toBeNull();

    // Degenerate quaternion falls back to identity
    engine.setListenerOrientation([0, 0, 0, 0]);
    expect(engine.getSnapshot().voices[0]!.directionListener).toEqual([0, 0, -1]);

    // Unknown or prototype regime keys are ignored
    engine.setRegime('deep_sky');
    engine.setRegime('constructor' as never);
    engine.setRegime('toString' as never);
    engine.setRegime('unknown_regime' as never);
    expect(engine.getRegime()).toBe('deep_sky');
  });
});

describe('SpatialAudioEngine bus graph wiring', () => {
  it('routes voices, reverb return, UI cues, musicInput, and skyglow bed into master.input and through MasterBus to destination', async () => {
    const { engine, contexts } = harness({ capacity: 2 });
    engine.setSources([makeStar('v1')]);
    engine.setSkyBrightness(18.5);
    engine.start();
    await flushMicrotasks();

    const ctx = contexts[0]!;
    const musicBus = engine.musicInput as unknown as RecordingNode;
    expect(musicBus).not.toBeNull();
    expect(musicBus.outputs).toHaveLength(1);
    const masterInput = musicBus.outputs[0] as RecordingNode;

    // Default master volume remains MASTER_BUS_SETTINGS.defaultVolume (0.85) when setMasterVolume was not called
    const volumeNode = ctx
      .byKind('gain')
      .find((g) => g.param('gain').events.some((e) => e.op === 'setValue' && e.value === MASTER_BUS_SETTINGS.defaultVolume));
    expect(volumeNode).toBeDefined();
    expect(volumeNode!.param('gain').value).toBe(MASTER_BUS_SETTINGS.defaultVolume);

    // Trigger a UI detent so we can trace its output bus
    engine.playDetentClick(1000);
    const clickOsc = ctx.byKind('oscillator').at(-1)!;
    const clickGain = clickOsc.outputs[0] as RecordingNode;
    const uiBus = clickGain.outputs[0] as RecordingNode;
    expect(uiBus.outputs).toEqual([masterInput]);

    // Voice panner routes to voicesBus -> masterInput
    const panner = ctx.byKind('panner')[0]!;
    const voicesBus = panner.outputs[0] as RecordingNode;
    expect(voicesBus.outputs).toEqual([masterInput]);

    // Convolution reverb return routes to masterInput
    const convolver = ctx.byKind('convolver')[0]!;
    const reverbReturn = convolver.outputs[0] as RecordingNode;
    expect(reverbReturn.outputs).toEqual([masterInput]);

    // Stereo-diffuse skyglow bed highpass filter routes to masterInput
    const bedSource = ctx.byKind('bufferSource').at(-1)!;
    const bedLevel = bedSource.outputs[0] as RecordingNode;
    const bedHighpass = bedLevel.outputs[0] as RecordingNode;
    expect(bedHighpass.outputs).toEqual([masterInput]);

    // MasterBus chain reaches ctx.destination and stereo analysers
    const reachable = new Set<unknown>();
    const walk = (node: unknown) => {
      if (reachable.has(node)) return;
      reachable.add(node);
      for (const next of (node as RecordingNode).outputs ?? []) walk(next);
    };
    walk(masterInput);
    expect(reachable.has(ctx.destination)).toBe(true);
    for (const analyser of ctx.byKind('analyser')) {
      expect(reachable.has(analyser)).toBe(true);
    }
  });
});

describe('SpatialAudioEngine start, stop, 1200 ms suspend timer, and dispose', () => {
  it('returns false and stays unstarted when contextFactory returns null', () => {
    const engine = new SpatialAudioEngine({ contextFactory: () => null, impulseResponseUrl: null });
    expect(engine.start()).toBe(false);
    expect(engine.enabled).toBe(false);
    expect(engine.started).toBe(false);
    expect(engine.context).toBeNull();
    // stop() and dispose() on an unstarted engine are safe no-ops
    engine.stop();
    engine.dispose();
    expect(engine.getSnapshot().contextState).toBe('none');
  });

  it('fades in on start(), fades out on stop(), resets the 1200 ms timer on repeated stop(), and resumes interrupted contexts', () => {
    vi.useFakeTimers();
    const { engine, contexts } = harness();

    expect(engine.start()).toBe(true);
    const ctx = contexts[0]!;
    const fade = ctx.fadeNode();
    expect(fade.param('gain').events).toContainEqual({
      op: 'setTarget',
      value: 1,
      time: 0,
      timeConstant: MASTER_BUS_SETTINGS.fadeTimeConstantSec,
    });

    engine.stop();
    expect(engine.enabled).toBe(false);
    expect(fade.param('gain').events).toContainEqual({
      op: 'setTarget',
      value: 0,
      time: 0,
      timeConstant: MASTER_BUS_SETTINGS.fadeTimeConstantSec,
    });

    // Calling stop() again after 700 ms resets the 1200 ms suspend timer
    vi.advanceTimersByTime(700);
    engine.stop();
    vi.advanceTimersByTime(1199);
    expect(ctx.suspends).toBe(0);
    expect(ctx.state).toBe('running');

    vi.advanceTimersByTime(1);
    expect(ctx.suspends).toBe(1);
    expect(ctx.state).toBe('suspended');

    // Restarting from iOS 'interrupted' state calls resume() and swallows rejected resume/suspend promises safely
    ctx.state = 'interrupted';
    expect(engine.start()).toBe(true);
    expect(ctx.resumes).toBe(1);
    expect(ctx.state).toBe('running');

    // Restarting before the 1200 ms fade completes cancels the pending suspend timer
    engine.stop();
    vi.advanceTimersByTime(600);
    expect(engine.start()).toBe(true);
    vi.advanceTimersByTime(2000);
    expect(ctx.suspends).toBe(1);
    expect(ctx.state).toBe('running');
  });

  it('cancels the pending suspend timer, detaches lifecycle, closes context, and allows clean restart after dispose()', async () => {
    vi.useFakeTimers();
    const host = new FakeLifecycleHost();
    const { engine, factory, contexts } = harness({ lifecycleHost: host });

    engine.setSources([makeStar('persisted')]);
    engine.start();
    const ctx = contexts[0]!;
    ctx.analyserFill = 0.5;
    ctx.limiterReduction = -2.5;
    expect(engine.getOutputLevelDb()).toBeCloseTo(-6.0206, 2);
    expect(engine.getLimiterReductionDb()).toBe(-2.5);
    expect(host.listenerCount('visibilitychange')).toBe(1);
    expect(host.window.listenerCount('pagehide')).toBe(1);
    expect(host.window.listenerCount('pageshow')).toBe(1);
    expect(ctx.listenerCount('statechange')).toBe(1);

    // Trigger stop() to arm the 1200 ms suspend timer, then dispose() before it fires
    engine.stop();
    engine.dispose();

    vi.advanceTimersByTime(2000);
    expect(ctx.suspends).toBe(0);
    expect(ctx.closes).toBe(1);
    expect(host.listenerCount('visibilitychange')).toBe(0);
    expect(host.window.listenerCount('pagehide')).toBe(0);
    expect(host.window.listenerCount('pageshow')).toBe(0);
    expect(ctx.listenerCount('statechange')).toBe(0);

    expect(engine.enabled).toBe(false);
    expect(engine.started).toBe(false);
    expect(engine.context).toBeNull();
    expect(engine.musicInput).toBeNull();
    expect(engine.getOutputLevelDb()).toBe(-Infinity);
    expect(engine.getLimiterReductionDb()).toBe(0);
    expect(engine.getSnapshot().contextState).toBe('none');

    // Starting again after dispose() creates a second AudioContext and restores buffered voices
    expect(engine.start()).toBe(true);
    expect(factory).toHaveBeenCalledTimes(2);
    expect(engine.context).toBe(contexts[1]);
    expect(engine.getSnapshot().voices.map((v) => v.id)).toEqual(['persisted']);
    expect(engine.getSnapshot().voices[0]!.slot).toBe(0);
    engine.dispose();
  });
});

describe('SpatialAudioEngine skyglow bed loading, token cancellation, and fallback reporting', () => {
  it('cancels an in-flight bed load when setBedPreference changes mid-load and disposes the late bed', async () => {
    const omnitoneInit = deferred<void>();
    const { omnitone, createdRenderers } = makeFakeOmnitone(omnitoneInit.promise);
    const loadOmnitone = vi.fn(async () => ({ default: omnitone }));
    const { engine, contexts } = harness({
      bedPreference: 'omnitone-foa',
      loadOmnitone,
    });

    engine.setSkyBrightness(18.0);
    engine.start();
    const ctx = contexts[0]!;

    // While Omnitone initialize() is still pending, snapshot reports 'loading'
    await flushMicrotasks(2);
    expect(engine.getSnapshot().bed.renderer).toBe('loading');
    expect(createdRenderers).toHaveLength(1);

    // Switch preference to stereo-diffuse while Omnitone is still initializing
    engine.setBedPreference('stereo-diffuse');
    await flushMicrotasks();
    expect(engine.getSnapshot().bed.renderer).toBe('stereo-diffuse');
    expect(engine.getSnapshot().bed.fallbackReason).toBeNull();

    const bufferSourcesBeforeLateResolve = ctx.byKind('bufferSource').length;
    // Now resolve the stale Omnitone load: it must be discarded and disposed immediately
    omnitoneInit.resolve();
    await flushMicrotasks();

    expect(engine.getSnapshot().bed.renderer).toBe('stereo-diffuse');
    expect(engine.getSnapshot().bed.fallbackReason).toBeNull();
    // The stale Omnitone bed created 1 extra bufferSource and immediately scheduled stop() on it
    const lateSources = ctx.byKind('bufferSource').slice(bufferSourcesBeforeLateResolve);
    expect(lateSources).toHaveLength(1);
    expect(lateSources[0]!.stopTimes).toEqual([0.4]);
  });

  it('discards an in-flight bed load when dispose() runs before loadOmnitone resolves', async () => {
    const omnitoneInit = deferred<void>();
    const { omnitone } = makeFakeOmnitone(omnitoneInit.promise);
    const { engine, contexts } = harness({
      bedPreference: 'auto',
      loadOmnitone: async () => ({ default: omnitone }),
    });

    engine.start();
    const ctx = contexts[0]!;
    await flushMicrotasks(2);
    expect(engine.getSnapshot().bed.renderer).toBe('loading');

    engine.dispose();
    omnitoneInit.resolve();
    await flushMicrotasks();

    expect(engine.getSnapshot().bed.renderer).toBe('none');
    const stoppedBedSources = ctx.byKind('bufferSource').filter((s) => s.stopTimes.length > 0);
    expect(stoppedBedSources.length).toBeGreaterThanOrEqual(1);
  });

  it('reports fallbackReason when Omnitone fails, clears it on stereo-diffuse, and updates live bed gain on sky changes', async () => {
    const { engine, contexts } = harness({
      bedPreference: 'auto',
      loadOmnitone: () => Promise.reject(new Error('HRIR fetch failed')),
    });

    engine.setSkyBrightness(19.0);
    engine.start();
    await flushMicrotasks();

    expect(engine.getSnapshot().bed).toEqual({
      renderer: 'stereo-diffuse',
      levelDb: skyglowBedLevelDb(19.0, 0),
      silentBecause: null,
      fallbackReason: 'Omnitone unavailable: HRIR fetch failed',
    });

    // Explicitly choosing stereo-diffuse succeeds with fallbackReason: null
    engine.setBedPreference('stereo-diffuse');
    await flushMicrotasks();
    expect(engine.getSnapshot().bed).toEqual({
      renderer: 'stereo-diffuse',
      levelDb: skyglowBedLevelDb(19.0, 0),
      silentBecause: null,
      fallbackReason: null,
    });

    // Live setUnveil(1) glides the active stereo bed level gain down to 0
    const ctx = contexts[0]!;
    const bedSource = ctx.byKind('bufferSource').at(-1)!;
    const bedGain = bedSource.outputs[0] as RecordingNode;
    expect(bedGain.param('gain').value).toBeGreaterThan(0);
    engine.setUnveil(1);
    expect(bedGain.param('gain').value).toBe(0);
  });
});

describe('SpatialAudioEngine UI cues (playDetentClick and playLockTone)', () => {
  it('synthesizes detent clicks onto uiBus only while enabled with a positive finite pitch', () => {
    const { engine, contexts } = harness();
    // Ignored before start()
    engine.playDetentClick();

    engine.start();
    const ctx = contexts[0]!;
    ctx.currentTime = 1.25;
    const oscBefore = ctx.byKind('oscillator').length;

    // Invalid pitches produce no oscillator
    engine.playDetentClick(0);
    engine.playDetentClick(-200);
    engine.playDetentClick(Number.NaN);
    engine.playDetentClick(Number.POSITIVE_INFINITY);
    expect(ctx.byKind('oscillator')).toHaveLength(oscBefore);

    // Default pitch (920 Hz)
    engine.playDetentClick();
    expect(ctx.byKind('oscillator')).toHaveLength(oscBefore + 1);
    const osc = ctx.byKind('oscillator').at(-1)!;
    expect(osc.type).toBe('sine');
    expect(osc.param('frequency').events).toEqual([
      { op: 'setValue', value: 920, time: 1.25 },
      { op: 'expRamp', value: 920 * 0.35, time: 1.25 + 0.024 },
    ]);
    expect(osc.startTimes).toEqual([1.25]);
    expect(osc.stopTimes).toEqual([1.25 + 0.028]);

    const clickGain = osc.outputs[0] as RecordingNode;
    expect(clickGain.kind).toBe('gain');
    expect(clickGain.param('gain').events).toEqual([
      { op: 'setValue', value: 0.11, time: 1.25 },
      { op: 'expRamp', value: 0.0001, time: 1.25 + 0.026 },
    ]);
    const uiBus = clickGain.outputs[0] as RecordingNode;
    const masterInput = (engine.musicInput as unknown as RecordingNode).outputs[0];
    expect(uiBus.outputs).toEqual([masterInput]);

    // Ignored after stop()
    engine.stop();
    engine.playDetentClick(1100);
    expect(ctx.byKind('oscillator')).toHaveLength(oscBefore + 1);
  });

  it('synthesizes Planck-timbre just-fifth lock tones panned within [-0.9, 0.9], silent on missing inputs', () => {
    const { engine, contexts } = harness();
    // Ignored before start()
    engine.playLockTone(5772, 0);

    engine.start();
    const ctx = contexts[0]!;
    ctx.currentTime = 2.0;
    const oscBefore = ctx.byKind('oscillator').length;
    const wavesBefore = ctx.periodicWaves.length;

    // Missing or invalid teffK or panX produces no nodes
    engine.playLockTone(null, 0);
    engine.playLockTone(0, 0);
    engine.playLockTone(-500, 0);
    engine.playLockTone(Number.NaN, 0);
    engine.playLockTone(5772, null);
    engine.playLockTone(5772, Number.NaN);
    engine.playLockTone(5772, Number.POSITIVE_INFINITY);
    expect(ctx.byKind('oscillator')).toHaveLength(oscBefore);

    // Valid lock tone with extreme panX = -1.5 clamps pan to -0.9
    engine.playLockTone(5772, -1.5);
    expect(ctx.byKind('oscillator')).toHaveLength(oscBefore + 2);
    expect(ctx.periodicWaves).toHaveLength(wavesBefore + 2);

    const [lowerOsc, upperOsc] = ctx.byKind('oscillator').slice(-2);
    expect(lowerOsc!.param('frequency').events).toEqual([{ op: 'setValue', value: 220, time: 2.0 }]);
    expect(upperOsc!.param('frequency').events).toEqual([{ op: 'setValue', value: 330, time: 2.0 }]);
    expect(lowerOsc!.startTimes).toEqual([2.0]);
    expect(lowerOsc!.stopTimes).toEqual([2.0 + 0.68]);
    expect(upperOsc!.startTimes).toEqual([2.0]);
    expect(upperOsc!.stopTimes).toEqual([2.0 + 0.68]);

    // 32 harmonics -> PeriodicWave arrays have length 33 (DC + 32 harmonics), disableNormalization: true
    const lowerWave = ctx.periodicWaves.at(-2)!;
    expect(lowerWave.real).toHaveLength(33);
    expect(lowerWave.options).toEqual({ disableNormalization: true });

    const env = lowerOsc!.outputs[0] as RecordingNode;
    expect(upperOsc!.outputs[0]).toBe(env);
    expect(env.param('gain').events).toEqual([
      { op: 'setValue', value: 0.0001, time: 2.0 },
      { op: 'linearRamp', value: 0.09, time: 2.0 + 0.03 },
      { op: 'expRamp', value: 0.0001, time: 2.0 + 0.65 },
    ]);

    const panner = env.outputs[0] as RecordingNode;
    expect(panner.kind).toBe('stereoPanner');
    expect(panner.param('pan').events).toEqual([{ op: 'setValue', value: -0.9, time: 2.0 }]);

    // Upper clamp at +0.9
    engine.playLockTone(9000, 1.8);
    const secondPanner = ctx.byKind('stereoPanner').at(-1)!;
    expect(secondPanner.param('pan').value).toBeCloseTo(0.9, 6);

    // Ignored after stop()
    const oscAfter = ctx.byKind('oscillator').length;
    engine.stop();
    engine.playLockTone(5772, 0.2);
    expect(ctx.byKind('oscillator')).toHaveLength(oscAfter);
  });
});

describe('SpatialAudioEngine voice planning, pitch persistence, hysteresis, silent outcomes, harmonic ceiling, and reverb', () => {
  it('persists identity pitches and slots across source updates and respects incumbent hysteresis', () => {
    const { engine } = harness({ capacity: 3 });
    engine.start();

    engine.setSources([
      makeStar('alpha', { luminositySolar: 120 }),
      makeStar('beta', { luminositySolar: 80 }),
      makeStar('gamma', { luminositySolar: 40 }),
    ]);
    const snap1 = engine.getSnapshot();
    expect(snap1.voices.map((v) => v.id)).toEqual(['alpha', 'beta', 'gamma']);
    const betaVoice1 = snap1.voices.find((v) => v.id === 'beta')!;
    const gammaVoice1 = snap1.voices.find((v) => v.id === 'gamma')!;
    expect(IDENTITY_PITCHES_HZ).toContain(betaVoice1.pitchHz);

    // Introduce newcomer 'delta' slightly louder than incumbent 'gamma' (+1.5 dB < 3 dB hysteresis),
    // and target 'target-dim' which is faint but marked isTarget: true.
    // 'alpha' departs, 'beta' stays, 'target-dim' takes top priority, and incumbent 'gamma' beats 'delta'.
    const gammaSlightlyBrighterLum = 40 * Math.pow(10, 1.5 / 10);
    engine.setSources([
      makeStar('beta', { luminositySolar: 80 }),
      makeStar('gamma', { luminositySolar: 40 }),
      makeStar('delta', { luminositySolar: gammaSlightlyBrighterLum }),
      makeStar('target-dim', { luminositySolar: 2, isTarget: true }),
    ]);

    const snap2 = engine.getSnapshot();
    expect(snap2.voices.map((v) => v.id)).toEqual(['target-dim', 'beta', 'gamma']);
    expect(snap2.notSelected).toEqual(['delta']);

    const betaVoice2 = snap2.voices.find((v) => v.id === 'beta')!;
    const gammaVoice2 = snap2.voices.find((v) => v.id === 'gamma')!;
    expect(betaVoice2.pitchHz).toBe(betaVoice1.pitchHz);
    expect(betaVoice2.slot).toBe(betaVoice1.slot);
    expect(gammaVoice2.pitchHz).toBe(gammaVoice1.pitchHz);
    expect(gammaVoice2.slot).toBe(gammaVoice1.slot);
  });

  it('classifies missing_input and below_audibility_floor sources in snapshot().silent and forwards harmonicCeilingHz', () => {
    const { engine } = harness({ capacity: 4, harmonicCeilingHz: 3500 }, 48000);
    engine.start();

    engine.setSources([
      makeStar('hot-capped', { teffK: 25000, luminositySolar: 500 }),
      makePulsar('vela', { spinFrequencyHz: 11.1946 }),
      makeStar('too-faint', { luminositySolar: 1e-6, distancePc: 1e6 }),
      makePulsar('no-spin', { spinFrequencyHz: null }),
      makeStar('no-dist-or-dir', { distancePc: null, directionWorld: null }),
    ]);

    const snap = engine.getSnapshot();
    expect(snap.voices.map((v) => v.id)).toEqual(['hot-capped', 'vela']);
    // 25,000 K star normally centres > 4 kHz, but harmonicCeilingHz: 3500 caps its harmonics below 3.5 kHz
    expect(snap.voices[0]!.centroidHz).toBeGreaterThan(0);
    expect(snap.voices[0]!.centroidHz).toBeLessThan(3500);
    // Pulsar has a slot and pitchHz, and null star centroidHz
    expect(snap.voices[1]!.slot).toBe(1);
    expect(snap.voices[1]!.pitchHz).not.toBeNull();
    expect(snap.voices[1]!.centroidHz).toBeNull();
    expect(snap.voices[1]!.pulsar).toEqual({ pulseRateHz: 11.1946 });

    expect(snap.notSelected).toEqual([]);
    expect(snap.silent).toEqual([
      { status: 'silent', id: 'too-faint', reason: 'below_audibility_floor', missing: [] },
      { status: 'silent', id: 'no-spin', reason: 'missing_input', missing: ['spinFrequencyHz'] },
      { status: 'silent', id: 'no-dist-or-dir', reason: 'missing_input', missing: ['directionWorld', 'distancePc'] },
    ]);
  });

  it('loads DEFAULT_IMPULSE_RESPONSE_URL when impulseResponseUrl is omitted, skips when null, and reports status/errors', async () => {
    const defaultFetch = vi.fn(async () => new Response(new Uint8Array(32), { status: 200 })) as unknown as typeof fetch;
    const withDefaultIr = new SpatialAudioEngine({
      contextFactory: () => new RecordingAudioContext() as unknown as AudioContext,
      bedPreference: 'stereo-diffuse',
      lifecycleHost: null,
      fetchImpl: defaultFetch,
    });
    withDefaultIr.start();
    await flushMicrotasks();
    expect(defaultFetch).toHaveBeenCalledWith(DEFAULT_IMPULSE_RESPONSE_URL);
    expect(withDefaultIr.getSnapshot().reverb).toEqual({ status: 'ready', error: null });

    const skippedFetch = vi.fn() as unknown as typeof fetch;
    const withNullIr = harness({ impulseResponseUrl: null, fetchImpl: skippedFetch }).engine;
    withNullIr.start();
    await flushMicrotasks();
    expect(skippedFetch).not.toHaveBeenCalled();
    expect(withNullIr.getSnapshot().reverb).toEqual({ status: 'idle', error: null });

    const okFetch = vi.fn(async () => new Response(new Uint8Array(32), { status: 200 })) as unknown as typeof fetch;
    const { engine, contexts } = harness({
      impulseResponseUrl: 'https://example.test/chapel.wav',
      fetchImpl: okFetch,
    });

    // Safe no-op before start()
    engine.setReverbReturnDb(-6);
    engine.start();
    await flushMicrotasks();

    expect(okFetch).toHaveBeenCalledWith('https://example.test/chapel.wav');
    expect(engine.getSnapshot().reverb).toEqual({ status: 'ready', error: null });

    const ctx = contexts[0]!;
    engine.setReverbReturnDb(-12);
    const glidedReturnGains = ctx
      .byKind('gain')
      .filter((g) => g.param('gain').events.some((e) => e.op === 'setTarget' && e.timeConstant === 0.05));
    expect(glidedReturnGains.length).toBeGreaterThanOrEqual(1);

    // Failed impulse load reports status: 'failed' with HTTP status error
    const failFetch = vi.fn(async () => new Response('missing', { status: 404 })) as unknown as typeof fetch;
    const failing = harness({
      impulseResponseUrl: 'https://example.test/missing.wav',
      fetchImpl: failFetch,
    }).engine;
    failing.start();
    await flushMicrotasks();
    expect(failing.getSnapshot().reverb).toEqual({
      status: 'failed',
      error: 'HTTP 404 for https://example.test/missing.wav',
    });
  });
});
