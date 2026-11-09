import { afterEach, describe, expect, it, vi } from 'vitest';
import { ObservatoryAudioEngine } from '../ObservatoryAudioEngine';
import { MASTER_BUS_SETTINGS, MasterBus, compressorMakeupGainDb } from '../engine/MasterBus';
import type { AudioSourceInput } from '../voicePlanning';

/**
 * A minimal stand-in for AudioContext: every `createX()` returns a node whose AudioParams
 * record their last value. It exercises the engine's wiring and lifecycle in Node; how it
 * sounds is checked by ear in apps/web/spike/audio.
 */
class FakeParam {
  value: number;
  constructor(value = 0) {
    this.value = value;
  }
  setValueAtTime(v: number) {
    this.value = v;
    return this;
  }
  setTargetAtTime(v: number) {
    this.value = v;
    return this;
  }
  linearRampToValueAtTime(v: number) {
    this.value = v;
    return this;
  }
  exponentialRampToValueAtTime(v: number) {
    this.value = v;
    return this;
  }
  cancelScheduledValues() {
    return this;
  }
  setValueCurveAtTime(values: ArrayLike<number>) {
    this.value = values[values.length - 1] ?? 0;
    return this;
  }
}

const PARAM_NAMES = new Set([
  'gain', 'frequency', 'detune', 'Q', 'pan', 'threshold', 'knee', 'ratio', 'attack', 'release',
  'positionX', 'positionY', 'positionZ', 'playbackRate', 'delayTime',
]);

class FakeNode {
  [key: string]: unknown;
  started = 0;
  stopped = 0;
  readonly outputs: unknown[] = [];
  constructor(readonly kind: string) {
    return new Proxy(this, {
      get(target, prop, receiver) {
        if (typeof prop === 'string' && PARAM_NAMES.has(prop) && !(prop in target)) {
          target[prop] = new FakeParam();
        }
        return Reflect.get(target, prop, receiver);
      },
    });
  }
  connect(destination: unknown) {
    this.outputs.push(destination);
    return destination;
  }
  disconnect() {}
  start() {
    this.started++;
  }
  stop() {
    this.stopped++;
  }
  setPeriodicWave() {}
  setPosition() {}
  getFloatTimeDomainData(buffer: Float32Array) {
    buffer.fill(0);
  }
}

class FakeAudioContext {
  static instances = 0;
  readonly sampleRate = 8000;
  readonly currentTime = 0;
  state: AudioContextState = 'running';
  readonly destination = new FakeNode('destination');
  readonly created: Record<string, number> = {};
  constructor() {
    FakeAudioContext.instances++;
  }
  private make(kind: string) {
    this.created[kind] = (this.created[kind] ?? 0) + 1;
    return new FakeNode(kind);
  }
  createGain() { return this.make('gain'); }
  createBiquadFilter() { return this.make('biquad'); }
  createDynamicsCompressor() { return Object.assign(this.make('compressor'), { reduction: 0 }); }
  createWaveShaper() { return this.make('waveshaper'); }
  createAnalyser() { return Object.assign(this.make('analyser'), { fftSize: 2048 }); }
  createChannelSplitter() { return this.make('splitter'); }
  createConvolver() { return this.make('convolver'); }
  createPanner() { return this.make('panner'); }
  createStereoPanner() { return this.make('stereoPanner'); }
  createOscillator() { return this.make('oscillator'); }
  createBufferSource() { return this.make('bufferSource'); }
  createPeriodicWave() { this.created.periodicWave = (this.created.periodicWave ?? 0) + 1; return {}; }
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
  resume() { this.state = 'running'; return Promise.resolve(); }
  suspend() { this.state = 'suspended'; return Promise.resolve(); }
  close() { this.state = 'closed'; return Promise.resolve(); }
  createDelay() { return this.make('delay'); }
  decodeAudioData() { return Promise.resolve(this.createBuffer(2, 8000, 8000)); }
}

function makeEngine() {
  const contexts: FakeAudioContext[] = [];
  const factory = vi.fn(() => {
    const ctx = new FakeAudioContext();
    contexts.push(ctx);
    return ctx as unknown as AudioContext;
  });
  const engine = new ObservatoryAudioEngine({
    contextFactory: factory,
    impulseResponseUrl: null,
    bedPreference: 'stereo-diffuse',
  });
  return { engine, factory, contexts };
}

const star = (id: string, overrides: Partial<AudioSourceInput> = {}): AudioSourceInput => ({
  id,
  kind: 'star',
  directionWorld: [0, 0, -1],
  distancePc: 10,
  luminositySolar: 50,
  teffK: 6500,
  ...overrides,
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('ObservatoryAudioEngine lifecycle', () => {
  it('creates no AudioContext at import or before the first toggle, then exactly one', async () => {
    const constructed = vi.fn();
    class SpyAudioContext extends FakeAudioContext {
      constructor() {
        super();
        constructed();
      }
    }
    vi.stubGlobal('window', { AudioContext: SpyAudioContext });

    const module = await import('../ObservatoryAudioEngine');
    expect(module.observatoryAudio.getEnabled()).toBe(false);

    // Default browser factory, as the app uses it.
    const engine = new module.ObservatoryAudioEngine({ impulseResponseUrl: null, bedPreference: 'stereo-diffuse' });
    engine.setScaleRegime('cislunar');
    engine.setSources([star('a')]);
    engine.setSkyBrightness(18);
    engine.setUnveil(0.5);
    engine.playDetentClick(1020);
    engine.playTargetLockTone(0.65, 0);
    expect(constructed).not.toHaveBeenCalled();
    expect(engine.getEnabled()).toBe(false);

    expect(engine.toggle()).toBe(true);
    expect(constructed).toHaveBeenCalledTimes(1);
  });

  it('toggles on and off, reusing one context and suspending it once faded out', () => {
    vi.useFakeTimers();
    const { engine, factory, contexts } = makeEngine();
    expect(engine.toggle()).toBe(true);
    expect(factory).toHaveBeenCalledTimes(1);
    expect(engine.toggle()).toBe(false);
    vi.advanceTimersByTime(2000);
    expect(contexts[0]!.state).toBe('suspended');
    expect(engine.toggle()).toBe(true);
    expect(contexts[0]!.state).toBe('running');
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it('stays disabled when no AudioContext can be created', () => {
    const engine = new ObservatoryAudioEngine({ contextFactory: () => null, impulseResponseUrl: null });
    expect(engine.toggle()).toBe(false);
    expect(engine.getEnabled()).toBe(false);
  });

  it('applies a volume set before the gesture once the master bus exists', () => {
    const setVolume = vi.spyOn(MasterBus.prototype, 'setVolume');
    const { engine } = makeEngine();
    engine.setMasterVolume(0.3);
    expect(setVolume).not.toHaveBeenCalled();
    engine.toggle();
    expect(setVolume).toHaveBeenCalledWith(0.3);
    setVolume.mockRestore();
  });

  it('plays data that arrived before the gesture as soon as audio is enabled', () => {
    const { engine } = makeEngine();
    engine.setSources([star('a')]);
    engine.toggle();
    const snapshot = engine.getSnapshot();
    expect(snapshot.voices.map((v) => v.id)).toEqual(['a']);
    expect(snapshot.voices[0]!.slot).toBe(0);
  });
});

describe('ObservatoryAudioEngine music', () => {
  const SOURCES = 'http://127.0.0.1:5199/';
  const flush = async () => {
    for (let i = 0; i < 20; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  };
  function musicEngine(musicSetting?: 'ambient-beats' | 'off', sourcesBase: string | null = SOURCES) {
    const requests: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      requests.push(String(input));
      return new Response(new Uint8Array(16));
    }) as unknown as typeof fetch;
    const factory = vi.fn(() => new FakeAudioContext() as unknown as AudioContext);
    const heard: string[] = [];
    const states: string[] = [];
    const engine = new ObservatoryAudioEngine({
      contextFactory: factory,
      impulseResponseUrl: null,
      bedPreference: 'stereo-diffuse',
      ...(musicSetting ? { musicSetting } : {}),
      musicSourcesBase: sourcesBase,
      fetchImpl,
      onMusicPieceStarted: (piece) => heard.push(piece),
      onMusicStatus: (status) => states.push(status.state),
    });
    // A made-up complete star, so the piece has a data part to play
    engine.setMusicStars([{ id: 'test-a', teffK: 5_000, luminositySolar: 1, distancePc: 10, screenX: 0.2, screenY: 0.1 }]);
    return { engine, factory, requests, heard, states };
  }

  it('makes and fetches nothing before Audio on, plays the pinned piece on Audio on, and stops it on Audio off', async () => {
    const { engine, factory, requests, heard } = musicEngine('ambient-beats');
    expect(engine.getMusicSetting()).toBe('ambient-beats');
    expect(factory).not.toHaveBeenCalled();
    expect(requests).toEqual([]);
    engine.enable();
    await flush();
    expect(factory).toHaveBeenCalledTimes(1);
    expect(requests.some((url) => url.startsWith(`${SOURCES}vcsl/`))).toBe(true);
    expect(engine.getMusicStatus()).toMatchObject({ setting: 'ambient-beats', piece: 'ambient-beats', state: 'playing', reason: null, passes: 1, starsPlayed: 1 });
    expect(heard).toEqual(['ambient-beats']);
    engine.disable();
    expect(engine.getMusicStatus()).toMatchObject({ state: 'off', passes: 1 });
  });

  it('puts the music on the master bus input, so the slider, the fades and the limiter apply to it', () => {
    const { engine } = musicEngine();
    expect(engine.spatial.musicInput).toBeNull();
    engine.enable();
    const music = engine.spatial.musicInput as unknown as FakeNode;
    const master = (engine.spatial as unknown as { master: { input: FakeNode } }).master;
    expect(music.outputs).toEqual([master.input]);
    // The master input is the one the UI sounds use, and its chain reaches the speakers
    const reached = new Set<unknown>();
    const walk = (node: unknown) => {
      if (reached.has(node)) return;
      reached.add(node);
      for (const next of (node as FakeNode).outputs ?? []) walk(next);
    };
    walk(master.input);
    const ctx = engine.spatial.context as unknown as FakeAudioContext;
    expect(reached.has(ctx.destination)).toBe(true);
    engine.disable();
  });

  it('plays the soundtrack by default, for the moment the app gives it', async () => {
    const { engine, heard } = musicEngine();
    expect(engine.getMusicSetting()).toBe('soundtrack');
    engine.setMusicMoment('look_around');
    engine.enable();
    await flush();
    expect(engine.getMusicStatus()).toMatchObject({ setting: 'soundtrack', moment: 'look_around', piece: 'ambient-beats', state: 'playing' });
    expect(heard).toEqual(['ambient-beats']);
    engine.disable();
  });

  it('plays nothing, fetches nothing and wants no stars where no samples are served (no sources base)', async () => {
    const { engine, requests, states } = musicEngine(undefined, null);
    // Known and reported when the engine is made, before Audio on, so nothing says music plays and no tile is loaded for it
    expect(states).toEqual(['unavailable']);
    expect(engine.getMusicStatus()).toMatchObject({ state: 'unavailable' });
    expect(engine.getMusicStatus().reason).toMatch(/not served/);
    expect(engine.wantsMusicStars()).toBe(false);
    engine.enable();
    await flush();
    expect(requests).toEqual([]);
    expect(engine.getMusicStatus()).toMatchObject({ state: 'unavailable', piece: null });
    expect(engine.wantsMusicStars()).toBe(false);
    expect(states).not.toContain('playing');
    engine.disable();
  });

  it('wants the stars on screen only while music can play: not with the music off, nor once a file is missing', async () => {
    const { engine, states } = musicEngine();
    expect(engine.wantsMusicStars()).toBe(true);
    engine.setMusicSetting('off');
    expect(engine.wantsMusicStars()).toBe(false);
    engine.setMusicSetting('soundtrack');
    expect(engine.wantsMusicStars()).toBe(true);

    const failing = new ObservatoryAudioEngine({
      contextFactory: () => new FakeAudioContext() as unknown as AudioContext,
      impulseResponseUrl: null,
      bedPreference: 'stereo-diffuse',
      musicSourcesBase: SOURCES,
      fetchImpl: (async () => new Response('missing', { status: 404 })) as unknown as typeof fetch,
      onMusicStatus: (status) => states.push(`failing:${status.state}`),
    });
    failing.setMusicStars([{ id: 'test-a', teffK: 5_000, luminositySolar: 1, distancePc: 10, screenX: 0.2, screenY: 0.1 }]);
    failing.enable();
    await flush();
    expect(failing.getMusicStatus().state).toBe('unavailable');
    expect(failing.wantsMusicStars()).toBe(false);
    expect(states).toContain('failing:unavailable');
    failing.disable();
  });

  it('plays no music and fetches no sample when the music is off, and plays at once when it is turned on', async () => {
    const { engine, requests } = musicEngine('off');
    engine.setMusicMoment('look_around');
    engine.enable();
    await flush();
    expect(requests.filter((url) => url.startsWith(SOURCES))).toEqual([]);
    expect(engine.getMusicStatus()).toMatchObject({ setting: 'off', state: 'off', reason: 'the music is off' });
    engine.setMusicSetting('soundtrack');
    await flush();
    expect(engine.getMusicSetting()).toBe('soundtrack');
    expect(engine.getMusicStatus()).toMatchObject({ piece: 'ambient-beats', state: 'playing' });
    engine.disable();
  });
});

describe('Master bus makeup compensation', () => {
  it('matches the hard-knee makeup gains measured in Chrome 152 for the old settings', () => {
    // OfflineAudioContext measurement: threshold -3 dB, ratio 20 gave +1.79 dB.
    expect(compressorMakeupGainDb(-3, 20)).toBeCloseTo(1.71, 2);
    expect(compressorMakeupGainDb(0, 20)).toBe(0);
    expect(compressorMakeupGainDb(-10, 1)).toBe(0);
  });

  it('compensates both dynamics stages so the bus has unity gain below threshold', () => {
    const { compressor, limiter } = MASTER_BUS_SETTINGS;
    expect(compressorMakeupGainDb(compressor.thresholdDb, compressor.ratio)).toBeCloseTo(4.5, 9);
    expect(compressorMakeupGainDb(limiter.thresholdDb, limiter.ratio)).toBeCloseTo(1.14, 9);
  });
});

describe('ObservatoryAudioEngine data rules', () => {
  it('bounds the pool at eight voices and reports the rest as not selected', () => {
    const { engine } = makeEngine();
    engine.toggle();
    engine.setSources(Array.from({ length: 20 }, (_, i) => star(`s${i}`, { luminositySolar: 10 + i })));
    const snapshot = engine.getSnapshot();
    expect(snapshot.voices).toHaveLength(8);
    expect(snapshot.notSelected).toHaveLength(12);
    expect(snapshot.voices.map((v) => v.id)).toContain('s19');
    expect(new Set(snapshot.voices.map((v) => v.pitchHz)).size).toBe(8);
  });

  it('keeps a star with a missing temperature silent and says which input was missing', () => {
    const { engine, contexts } = makeEngine();
    engine.toggle();
    const oscillatorsBefore = contexts[0]!.created.oscillator ?? 0;
    engine.setSources([star('no-teff', { teffK: null })]);
    const snapshot = engine.getSnapshot();
    expect(snapshot.voices).toHaveLength(0);
    expect(snapshot.silent).toEqual([
      { status: 'silent', id: 'no-teff', reason: 'missing_input', missing: ['teffK'] },
    ]);
    expect(contexts[0]!.created.oscillator ?? 0).toBe(oscillatorsBefore);
  });

  it('plays no lock tone without a colour or a position', () => {
    const { engine, contexts } = makeEngine();
    engine.toggle();
    const count = () => contexts[0]!.created.oscillator ?? 0;
    const before = count();
    engine.playTargetLockTone(undefined, 0);
    engine.playTargetLockTone(0.65, undefined);
    engine.playTargetLockTone(Number.NaN, 0.2);
    expect(count()).toBe(before);
    engine.playTargetLockTone(0.65, 0.2);
    expect(count()).toBe(before + 2);
  });

  it('sounds the skyglow bed only with a sky brightness, only under the sky, and not when unveiled', async () => {
    const { engine } = makeEngine();
    engine.toggle();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(engine.getSnapshot().bed).toMatchObject({ levelDb: null, silentBecause: 'skyBrightnessMagArcsec2 missing' });

    engine.setSkyBrightness(18.5);
    const lit = engine.getSnapshot().bed;
    expect(lit.levelDb).toBeGreaterThan(-72);
    expect(lit.renderer).toBe('stereo-diffuse');

    engine.setUnveil(1);
    expect(engine.getSnapshot().bed.levelDb).toBe(-Infinity);
    engine.setUnveil(0);

    engine.setScaleRegime('deep_sky');
    expect(engine.getSnapshot().bed).toMatchObject({ levelDb: -Infinity });
  });

  it('sounds a star at its distance among the stars at every scale, and a pulsar at its regime scale', () => {
    const { engine } = makeEngine();
    const crab: AudioSourceInput = { id: 'crab', kind: 'pulsar', directionWorld: [0, 0, -1], distancePc: 2000, luminositySolar: 1e5, spinFrequencyHz: 29.946923 };
    engine.setSources([star('near', { distancePc: 5.9 }), star('far', { distancePc: 140 }), crab]);
    const cues = (id: string) => {
      const voice = engine.getSnapshot().voices.find((v) => v.id === id)!;
      return { drrDb: voice.drrDb, lowpassHz: voice.distanceLowpassHz };
    };
    engine.setScaleRegime('earth_capture');
    const near = cues('near');
    const far = cues('far');
    expect(near.drrDb).toBeGreaterThan(far.drrDb);
    expect(near.lowpassHz).toBeGreaterThan(far.lowpassHz);
    const pulsarCues = new Set<number>();
    for (const regime of ['cislunar', 'solar_neighborhood', 'deep_sky', 'planetary_surface'] as const) {
      engine.setScaleRegime(regime);
      expect(cues('near')).toEqual(near);
      expect(cues('far')).toEqual(far);
      pulsarCues.add(cues('crab').drrDb);
    }
    expect(pulsarCues.size).toBeGreaterThan(1);
  });

  it('caps star harmonics at a given ceiling for listening tests, and keeps 16 kHz by default', () => {
    // At the fake's 8 kHz the Nyquist guard (0.45 of the rate) would cap every tone first
    const context48k = () => Object.assign(new FakeAudioContext(), { sampleRate: 48000 }) as unknown as AudioContext;
    const hotVoice = (harmonicCeilingHz?: number) => {
      const engine = new ObservatoryAudioEngine({
        contextFactory: context48k,
        impulseResponseUrl: null,
        bedPreference: 'stereo-diffuse',
        ...(harmonicCeilingHz === undefined ? {} : { harmonicCeilingHz }),
      });
      engine.toggle();
      engine.setSources([star('hot', { teffK: 25000 })]);
      return engine.getSnapshot().voices[0]!;
    };
    const open = hotVoice();
    // A 25,000 K voice centres near 4.3 kHz with harmonics up to 16 kHz (section 4.3)
    expect(open.centroidHz).toBeGreaterThan(4000);
    expect(hotVoice(16000).centroidHz).toBe(open.centroidHz);
    expect(hotVoice(4000).centroidHz).toBeLessThan(4000);
  });

  it('ignores regimes it does not know, including inherited object keys', () => {
    const { engine } = makeEngine();
    engine.setScaleRegime('solar_neighborhood');
    engine.setScaleRegime('toString' as never);
    engine.setScaleRegime('galactic' as never);
    expect(engine.getSnapshot().regime).toBe('solar_neighborhood');
  });

  it('falls back to the plain Web Audio bed when Omnitone cannot initialize', async () => {
    const engine = new ObservatoryAudioEngine({
      contextFactory: () => new FakeAudioContext() as unknown as AudioContext,
      impulseResponseUrl: null,
      bedPreference: 'auto',
      loadOmnitone: () => Promise.reject(new Error('blocked in test')),
    });
    engine.toggle();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const bed = engine.getSnapshot().bed;
    expect(bed.renderer).toBe('stereo-diffuse');
    expect(bed.fallbackReason).toContain('blocked in test');
  });
});
