import { afterEach, describe, expect, it, vi } from 'vitest';
import { INSTRUMENTS, RECORDING, ambientBeats, barPitchSet, organAndPulse, type InstrumentDef, type RecordingAnalysis } from '../directions';
import { MusicLayer, neededZones } from '../MusicLayer';
import { selectionNote, type MusicSelection } from '../selectionTone';
import { SampleBank, chooseZone, playbackRate } from '../sampler';
import type { ScreenStar } from '../starNotes';

/**
 * A Web Audio stand-in that records every node made, what it connects to and when sources start.
 * Parameters are plain objects with the scheduling calls the scheduler makes.
 */
class FakeParam {
  value = 0;
  readonly events: { call: string; value: number; time: number }[] = [];
  private log(call: string, value: number, time: number) {
    this.events.push({ call, value, time });
    this.value = value;
    return this;
  }
  setValueAtTime(v: number, t: number) { return this.log('set', v, t); }
  linearRampToValueAtTime(v: number, t: number) { return this.log('linear', v, t); }
  exponentialRampToValueAtTime(v: number, t: number) { return this.log('exp', v, t); }
  setTargetAtTime(v: number, t: number) { return this.log('target', v, t); }
  setValueCurveAtTime(values: ArrayLike<number>, t: number) { return this.log('curve', values[values.length - 1] ?? 0, t); }
  cancelScheduledValues() { return this; }
}

const PARAMS = new Set(['gain', 'frequency', 'detune', 'Q', 'pan', 'threshold', 'knee', 'ratio', 'attack', 'release', 'playbackRate', 'delayTime']);

class FakeNode {
  [key: string]: unknown;
  readonly outputs: unknown[] = [];
  readonly starts: number[] = [];
  disconnected = false;
  constructor(readonly kind: string) {
    return new Proxy(this, {
      get(target, prop, receiver) {
        if (typeof prop === 'string' && PARAMS.has(prop) && !(prop in target)) target[prop] = new FakeParam();
        return Reflect.get(target, prop, receiver);
      },
    });
  }
  connect(destination: unknown) { this.outputs.push(destination); return destination; }
  disconnect() { this.disconnected = true; }
  start(time = 0) { this.starts.push(time); }
  stop() {}
  setPeriodicWave() {}
}

function fakeBuffer(seconds: number, sampleRate = 48_000) {
  const length = Math.round(seconds * sampleRate);
  const data = [new Float32Array(length), new Float32Array(length)];
  return { duration: seconds, length, sampleRate, numberOfChannels: 2, getChannelData: (c: number) => data[c]!, copyToChannel: (d: Float32Array, c: number) => data[c]!.set(d) };
}

class FakeContext {
  currentTime = 10;
  /** A running context; a phone's lifecycle may suspend it, and its clock then stands still. */
  state: AudioContextState | 'interrupted' = 'running';
  private readonly listeners = new Set<() => void>();
  addEventListener(_type: 'statechange', listener: () => void) { this.listeners.add(listener); }
  removeEventListener(_type: 'statechange', listener: () => void) { this.listeners.delete(listener); }
  setState(state: AudioContextState | 'interrupted') {
    this.state = state;
    for (const listener of [...this.listeners]) listener();
  }
  listening() { return this.listeners.size; }
  readonly sampleRate = 48_000;
  readonly destination = new FakeNode('destination');
  readonly nodes: FakeNode[] = [];
  readonly decoded: number[] = [];
  private make(kind: string) {
    const node = new FakeNode(kind);
    this.nodes.push(node);
    return node;
  }
  createGain() { return this.make('gain'); }
  createDynamicsCompressor() { return this.make('compressor'); }
  createConvolver() { return this.make('convolver'); }
  createDelay() { return this.make('delay'); }
  createBiquadFilter() { return this.make('biquad'); }
  createStereoPanner() { return this.make('panner'); }
  createChannelMerger() { return this.make('merger'); }
  createChannelSplitter() { return this.make('splitter'); }
  createBufferSource() { return this.make('bufferSource'); }
  createOscillator() { return this.make('oscillator'); }
  createWaveShaper() { return this.make('shaper'); }
  createPeriodicWave() { return {}; }
  createBuffer(channels: number, length: number, sampleRate: number) { return fakeBuffer(length / sampleRate, sampleRate); }
  async decodeAudioData(bytes: ArrayBuffer) {
    this.decoded.push(bytes.byteLength);
    return fakeBuffer(8);
  }
  sources() { return this.nodes.filter((n) => n.kind === 'bufferSource' || n.kind === 'oscillator'); }
}

/**
 * Serves any file under the sources base with a few bytes, and logs the requests. Like a browser's
 * fetch it refuses to be called as another object's method ("Illegal invocation").
 */
function sourcesFetch(options: { fail?: (url: string) => boolean; analysis?: RecordingAnalysis } = {}) {
  const requests: string[] = [];
  const impl = vi.fn(async function (this: unknown, input: RequestInfo | URL): Promise<Response> {
    if (this !== undefined && this !== globalThis) throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
    const url = String(input);
    requests.push(url);
    if (options.fail?.(url)) return new Response('unavailable in this test', { status: 503 });
    if (url.endsWith('recording-analysis.json')) return new Response(JSON.stringify(options.analysis ?? null));
    return new Response(new Uint8Array(16));
  });
  return { fetch: impl as unknown as typeof fetch, requests };
}

const BASE = 'http://127.0.0.1:5199/';

/** Two made-up complete stars and one missing its temperature; musicLayer plays what it is given. */
const STARS: ScreenStar[] = [
  { id: 'test-a', teffK: 5_000, luminositySolar: 1, distancePc: 10, screenX: -0.4, screenY: 0.2 },
  { id: 'test-b', teffK: 12_000, luminositySolar: 500, distancePc: 100, screenX: 0.5, screenY: -0.3 },
  { id: 'test-silent', teffK: null, luminositySolar: 50, distancePc: 30, screenX: 0, screenY: 0 },
];

/** A made-up analysis of the shape main.ts measures, enough to build the recording piece. */
const ANALYSIS: RecordingAnalysis = {
  file: RECORDING.file,
  bpm: 75,
  beatSec: 0.8,
  tempoStrength: 0.5,
  beatsPerBar: 4,
  key: { name: 'D major', tonic: 2, mode: 'major', correlation: 0.8 },
  fromSec: 203,
  bars: Array.from({ length: RECORDING.bars }, (_, i) => ({ bar: i + 1, chord: 'D', confident: true, score: 0.8, margin: 0.1 })),
  statement: 'made-up analysis for this test',
};

/** Moves the context's clock and the timers on together, in steps of 0.1 s, as a running context would. */
async function clockTo(ctx: FakeContext, sec: number): Promise<void> {
  while (ctx.currentTime < sec - 1e-9) {
    const step = Math.min(0.1, sec - ctx.currentTime);
    ctx.currentTime += step;
    await vi.advanceTimersByTimeAsync(step * 1000);
  }
}

/** The layer's output into the music input, and the gain of each pass under it, in order. */
function layerNodes(ctx: FakeContext, music: FakeNode) {
  const output = ctx.nodes.find((n) => n.outputs.includes(music))!;
  return { output, passes: ctx.nodes.filter((n) => n.outputs.includes(output)) };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('MusicLayer', () => {
  it('plays nothing and fetches nothing when the music is off', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'off', sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toEqual([]);
    expect(ctx.sources()).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ setting: 'off', piece: null, state: 'off', reason: 'the music is off', passes: 0 });
  });

  it('stays silent and says why when the samples are not served', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: null, fetch });
    // Known from the start, before Audio on: no music can play here, so it wants no stars
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable', why: 'not_served' });
    expect(layer.wantsStars()).toBe(false);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toEqual([]);
    expect(ctx.sources()).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable' });
    expect(layer.getStatus().reason).toMatch(/samples are not served/);
  });

  it('stays silent and names the file when a sample cannot be fetched, with no stand-in sound', async () => {
    const { fetch } = sourcesFetch({ fail: (url) => url.includes('Pipe%20Organ') });
    const states: string[] = [];
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, onStatus: (status) => states.push(status.state) });
    layer.setStars(STARS);
    expect(layer.wantsStars()).toBe(true);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(states.at(-1)).toBe('unavailable');
    expect(layer.wantsStars()).toBe(false);
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus().state).toBe('unavailable');
    expect(layer.getStatus().reason).toMatch(/Pipe Organ.*unavailable \(503\)/);
    layer.stop();
  });

  it('schedules the whole arrangement ahead of the clock into the music input, its stars as the data part', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, leadSec: 0.25 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);

    // Every sample the arrangement needs, and the chapel response, fetched once each
    const expected = organAndPulse(STARS.slice(0, 2));
    const zones = neededZones(expected);
    for (const zone of zones) expect(requests).toContain(BASE + zone.file.split('/').map(encodeURIComponent).join('/'));
    expect(new Set(requests).size).toBe(requests.length);

    // One output into the music input, one pass under it; every source starts at or after the clock plus the lead
    const outputs = ctx.nodes.filter((n) => n.outputs.includes(music));
    expect(outputs).toHaveLength(1);
    expect(layerNodes(ctx, music).passes).toHaveLength(1);
    const starts = ctx.sources().flatMap((s) => s.starts);
    expect(starts.length).toBe(expected.events.filter((e) => e.type === 'sample').length);
    expect(Math.min(...starts)).toBeGreaterThanOrEqual(10.25);
    expect(Math.max(...starts)).toBeLessThan(10.25 + expected.durationSec);

    // The star missing its temperature plays nothing; the two complete ones play the data part
    const status = layer.getStatus();
    expect(status).toMatchObject({ setting: 'organ-pulse', piece: 'organ-pulse', state: 'playing', reason: null, passes: 1 });
    expect(status.starsPlayed).toBe(new Set(expected.events.flatMap((e) => (e.type === 'sample' && e.star ? [e.star.starId] : []))).size);
    expect(status.starsPlayed).toBe(2);
  });

  it('plays a pinned piece again after a silence, from the stars then on screen, and stops all of it on stop', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'ambient-beats', sourcesBase: BASE, fetch, leadSec: 0.25, random: () => 0 });
    layer.setStars(STARS.slice(0, 1));
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    const started = layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(0);
    await started;
    const first = ambientBeats(STARS.slice(0, 1));
    const end = 10.25 + first.durationSec;
    expect(layer.getStatus()).toMatchObject({ piece: 'ambient-beats', passes: 1, starsPlayed: 1 });

    // The piece ends: 20 s of silence (the shortest gap, with a draw of 0), then the pin again
    layer.setStars(STARS);
    await clockTo(ctx, end + 1);
    expect(layer.getStatus()).toMatchObject({ state: 'gap', piece: null, passes: 1 });
    await clockTo(ctx, end + 19.8);
    expect(layer.getStatus()).toMatchObject({ state: 'gap', passes: 1 });
    await clockTo(ctx, end + 20.2);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', piece: 'ambient-beats', passes: 2, starsPlayed: 2 });
    const secondStarts = ctx.sources().slice(-1)[0]!.starts;
    expect(secondStarts[0]).toBeGreaterThanOrEqual(end + 20);

    // Stop fades the output to zero, disconnects it and schedules no further pass
    layer.stop();
    const { output } = layerNodes(ctx, music);
    const fade = (output.gain as FakeParam).events.at(-1)!;
    expect(fade.value).toBe(0);
    await clockTo(ctx, end + 200);
    expect(output.disconnected).toBe(true);
    expect(layer.getStatus()).toMatchObject({ state: 'off', piece: null, passes: 2 });
  });

  it('waits briefly for the first stars on screen, since Audio on comes before the feed has any', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    const started = layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(500);
    expect(layer.getStatus()).toMatchObject({ state: 'loading', passes: 0 });
    layer.setStars(STARS);
    await vi.advanceTimersByTimeAsync(0);
    await started;
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 1, starsPlayed: 2 });
  });

  it('plays the composed parts alone when no star with all its values comes on screen in time', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS.slice(2));
    const ctx = new FakeContext();
    const started = layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(7_000);
    expect(layer.getStatus()).toMatchObject({ state: 'loading', passes: 0 });
    await vi.advanceTimersByTimeAsync(2_000);
    await started;
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 1, starsPlayed: 0 });
  });

  it('plays the recording piece from the recording and its measured analysis, served beside the samples', async () => {
    const analysis: RecordingAnalysis = {
      file: RECORDING.file,
      bpm: 75,
      beatSec: 0.8,
      tempoStrength: 0.5,
      beatsPerBar: 4,
      key: { name: 'D major', tonic: 2, mode: 'major', correlation: 0.8 },
      fromSec: 203,
      bars: Array.from({ length: RECORDING.bars }, (_, i) => ({ bar: i + 1, chord: 'D', confident: true, score: 0.8, margin: 0.1 })),
      statement: 'made-up analysis for this test',
    };
    const { fetch, requests } = sourcesFetch({ analysis });
    const layer = new MusicLayer({ setting: 'recording', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toContain(`${BASE}recording-analysis.json`);
    expect(requests).toContain(BASE + RECORDING.file.split('/').map(encodeURIComponent).join('/'));
    // The recording starts at the measured downbeat, on the clock plus the lead
    const recording = ctx.nodes.find((n) => n.kind === 'bufferSource' && n.starts.length > 0)!;
    expect(recording.starts[0]).toBeGreaterThanOrEqual(10);
    expect(layer.getStatus()).toMatchObject({ setting: 'recording', piece: 'recording', state: 'playing' });
  });

  it('has the sample bank call a browser-like fetch as a plain function, as the demo page uses it', async () => {
    const { fetch, requests } = sourcesFetch();
    const bank = new SampleBank(new FakeContext() as unknown as BaseAudioContext, BASE, fetch);
    await bank.load([{ file: 'vcsl/test.wav', rootMidi: null, layer: 0, roundRobin: 0 }]);
    expect(requests).toEqual([`${BASE}vcsl/test.wav`]);
  });

  it('says why the recording piece is silent when its analysis is missing', async () => {
    const { fetch } = sourcesFetch({ fail: (url) => url.endsWith('recording-analysis.json') });
    const layer = new MusicLayer({ setting: 'recording', sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable' });
    expect(layer.getStatus().reason).toMatch(/recording-analysis\.json unavailable \(503\)/);
    layer.stop();
  });
});

describe('MusicLayer playing the soundtrack', () => {
  it('plays the moment piece, fades only its pass on a moment it does not fit, and plays that moment piece', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch({ analysis: ANALYSIS });
    const heard: string[] = [];
    const layer = new MusicLayer({ moment: 'look_around', sourcesBase: BASE, fetch, random: () => 0, onPieceStarted: (piece) => heard.push(piece) });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ setting: 'soundtrack', moment: 'look_around', piece: 'ambient-beats', state: 'playing', passes: 1 });

    // Travel: once it has held 1.5 s the ambient pass fades out over 3 s; the output stays up
    await clockTo(ctx, 15);
    layer.setMoment('travel');
    await clockTo(ctx, 16.4);
    const [firstPass] = layerNodes(ctx, music).passes;
    expect((firstPass!.gain as FakeParam).events.filter((e) => e.call === 'linear')).toEqual([]);
    await clockTo(ctx, 16.6);
    const ramp = (firstPass!.gain as FakeParam).events.filter((e) => e.call === 'linear');
    expect(ramp).toHaveLength(1);
    expect(ramp[0]!.value).toBe(0);
    // The fade starts when the timer finds the clock past the settle time, within one 0.1 s clock step
    expect(ramp[0]!.time).toBeGreaterThanOrEqual(16.5 + 3 - 1e-9);
    expect(ramp[0]!.time).toBeLessThanOrEqual(16.7 + 3);
    expect(layerNodes(ctx, music).output.disconnected).toBe(false);

    // After the fade, the recording in a pass of its own
    await clockTo(ctx, 19.4);
    expect(layer.getStatus()).toMatchObject({ moment: 'travel', passes: 1 });
    await clockTo(ctx, 19.8);
    expect(layer.getStatus()).toMatchObject({ moment: 'travel', piece: 'recording', state: 'playing', passes: 2 });
    const passes = layerNodes(ctx, music).passes;
    expect(passes).toHaveLength(2);
    expect(firstPass!.disconnected).toBe(true);
    expect(passes[1]!.disconnected).toBe(false);
    expect(heard).toEqual(['ambient-beats', 'recording']);
  });

  it('leaves a silence after a piece, then plays another, never the one just played', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ moment: 'look_around', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const end = 10.25 + ambientBeats(STARS.slice(0, 2)).durationSec;
    await clockTo(ctx, end + 0.2);
    expect(layer.getStatus()).toMatchObject({ state: 'gap', piece: null });
    await clockTo(ctx, end + 20.2);
    // Only ambient beats fits looking around and it just played: the next piece is another (a draw of 0 picks the organ)
    expect(layer.getStatus()).toMatchObject({ state: 'playing', piece: 'organ-pulse', passes: 2 });
    // The pass that ended on its own is let go once the next one starts
    const [first, second] = layerNodes(ctx, music).passes;
    expect(first!.disconnected).toBe(true);
    expect(second!.disconnected).toBe(false);
  });

  it('drops a pass still loading when the moment moves on, so only the new piece plays', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch({ analysis: ANALYSIS });
    const heard: string[] = [];
    const layer = new MusicLayer({ moment: 'look_around', sourcesBase: BASE, fetch, random: () => 0, onPieceStarted: (piece) => heard.push(piece) });
    const ctx = new FakeContext();
    // No stars yet: the ambient pass waits for them
    const started = layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await clockTo(ctx, 11);
    expect(layer.getStatus()).toMatchObject({ state: 'loading', piece: 'ambient-beats', passes: 0 });
    layer.setMoment('travel');
    await clockTo(ctx, 12.6);
    // The stars arrive after the moment moved on: the waiting ambient pass must not start
    layer.setStars(STARS);
    await clockTo(ctx, 15.7);
    await started;
    expect(heard).toEqual(['recording']);
    expect(layer.getStatus()).toMatchObject({ moment: 'travel', piece: 'recording', state: 'playing', passes: 1 });
  });

  it('turns off from the setting with a fade, and plays again at once when turned back on', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ moment: 'look_around', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    layer.setSetting('off');
    const [pass] = layerNodes(ctx, music).passes;
    expect((pass!.gain as FakeParam).events.at(-1)).toMatchObject({ call: 'linear', value: 0 });
    await clockTo(ctx, 13.2);
    expect(layer.getStatus()).toMatchObject({ setting: 'off', state: 'off', reason: 'the music is off', piece: null });
    expect(pass!.disconnected).toBe(true);
    layer.setSetting('soundtrack');
    await clockTo(ctx, 13.3);
    expect(layer.getStatus()).toMatchObject({ setting: 'soundtrack', state: 'playing', piece: 'organ-pulse', passes: 2 });
  });

  it('takes the moment and the setting before Audio on, and starts with them', async () => {
    const { fetch } = sourcesFetch({ analysis: ANALYSIS });
    const layer = new MusicLayer({ sourcesBase: BASE, fetch });
    layer.setMoment('travel');
    expect(layer.getStatus()).toMatchObject({ moment: 'travel', state: 'off' });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ moment: 'travel', piece: 'recording', state: 'playing' });
    layer.stop();
    layer.setSetting('ambient-beats');
    expect(layer.setting).toBe('ambient-beats');
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ setting: 'ambient-beats', piece: 'ambient-beats', state: 'playing', passes: 2 });
  });
});

describe('MusicLayer on a phone, while the context is not running', () => {
  it('wakes no timer while the context is suspended, and carries on from the same point when it runs again', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'ambient-beats', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    const end = 10.25 + ambientBeats(STARS.slice(0, 2)).durationSec;
    await clockTo(ctx, 20);

    // The page is hidden: the lifecycle suspends the context and its clock stands still
    ctx.setState('suspended');
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(vi.getTimerCount()).toBe(0);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 1 });

    // Shown again: the piece ends and the next comes after its gap, on the context's clock
    ctx.setState('running');
    await clockTo(ctx, end + 19.8);
    expect(layer.getStatus()).toMatchObject({ state: 'gap', passes: 1 });
    await clockTo(ctx, end + 20.2);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 2 });
    expect(ctx.listening()).toBe(0);
  });

  it('starts no pass while the context is not running, and plays it once the context runs', async () => {
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    // A phone that has not let the context run yet (or an iOS interruption)
    ctx.setState('interrupted');
    const started = layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await flushAll();
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'loading', passes: 0 });
    ctx.currentTime = 12;
    ctx.setState('running');
    await started;
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 1 });
    const starts = ctx.sources().flatMap((s) => s.starts);
    expect(Math.min(...starts)).toBeGreaterThanOrEqual(12.25);
  });

  it('lets go of the context when Audio goes off while it waits to run', async () => {
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    ctx.setState('suspended');
    void layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await flushAll();
    expect(ctx.listening()).toBeGreaterThan(0);
    layer.stop();
    expect(ctx.listening()).toBe(0);
    ctx.setState('running');
    await flushAll();
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'off', passes: 0 });
  });
});


describe('MusicLayer after a missing file', () => {
  const ORGAN = 'Pipe%20Organ';

  it('tries again after 15, 30, 60 and then every 120 s on the context clock, and plays once the file is served', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let missing = true;
    const { fetch, requests } = sourcesFetch({ fail: (url) => missing && url.includes(ORGAN) });
    const states: string[] = [];
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, onStatus: (status) => states.push(status.state) });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable', why: 'not_reachable', retryAtSec: 10 + 15 });
    expect(layer.wantsStars()).toBe(false);

    // Nothing is fetched again before its time; then each try fails and the wait grows
    const tries = () => requests.filter((url) => url.includes(ORGAN)).length;
    const first = tries();
    await clockTo(ctx, 24.8);
    expect(tries()).toBe(first);
    const waits: number[] = [];
    let at = layer.getStatus().retryAtSec!;
    for (let i = 0; i < 5; i++) {
      await clockTo(ctx, at + 0.2);
      const next = layer.getStatus().retryAtSec!;
      expect(next).not.toBeNull();
      waits.push(Math.round(next - at));
      at = next;
    }
    expect(waits).toEqual([30, 60, 120, 120, 120]);
    expect(tries()).toBeGreaterThan(first);
    // The page saw no music coming and going while the tries failed
    expect(states.slice(states.indexOf('unavailable'))).not.toContain('loading');

    // The file is served now: the next try plays, and what had loaded is not fetched again
    missing = false;
    const fetchedBefore = new Map<string, number>();
    for (const url of requests) fetchedBefore.set(url, (fetchedBefore.get(url) ?? 0) + 1);
    await clockTo(ctx, at + 0.5);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', piece: 'organ-pulse', retryAtSec: null, reason: null, why: null, passes: 1 });
    expect(layer.wantsStars()).toBe(true);
    for (const [url, count] of fetchedBefore) {
      if (url.includes(ORGAN)) continue;
      const after = requests.filter((u) => u === url).length;
      if (!url.endsWith('.json')) expect(after, url).toBe(count);
    }
  });

  it('asks for one file per try when the music files cannot be reached, not one per file', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let down = true;
    const { fetch, requests } = sourcesFetch({ fail: (url) => down && url.startsWith(BASE) });
    for (const setting of ['organ-pulse', 'ambient-beats'] as const) {
      requests.length = 0;
      down = true;
      const layer = new MusicLayer({ setting, sourcesBase: BASE, fetch });
      layer.setStars(STARS);
      const ctx = new FakeContext();
      await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
      expect(layer.getStatus().state).toBe('unavailable');
      expect(requests.filter((url) => url.startsWith(BASE)), setting).toHaveLength(1);
      await clockTo(ctx, 25.2);
      expect(requests.filter((url) => url.startsWith(BASE)), setting).toHaveLength(2);
      // Reachable again: the next try loads everything
      down = false;
      await clockTo(ctx, layer.getStatus().retryAtSec! + 0.5);
      expect(layer.getStatus().state, setting).toBe('playing');
      layer.stop();
    }
  });

  it('wants the stars on screen while a try runs, so the recovered pass plays them', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let missing = true;
    const { fetch } = sourcesFetch({ fail: (url) => missing && url.includes(ORGAN) });
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    // As the sky feed does: it hands the music the stars on screen each frame, only while the music wants them
    const frames = async (until: number) => {
      while (ctx.currentTime < until - 1e-9) {
        if (layer.wantsStars()) layer.setStars(STARS);
        const step = Math.min(0.1, until - ctx.currentTime);
        ctx.currentTime += step;
        await vi.advanceTimersByTimeAsync(step * 1000);
      }
    };
    // Audio on before the feed has any stars, and the organ missing: the first pass fails fast
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable', retryAtSec: 25 });
    await frames(24.9);
    expect(layer.wantsStars()).toBe(false);
    // The organ is served now; the try at 25 s takes the stars again and plays them
    missing = false;
    await frames(26);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', piece: 'organ-pulse', starsPlayed: 2 });
    layer.stop();
  });

  it('starts the waits over once the music has played again', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let missing = true;
    // The organ is missing at first; the recording piece never plays here (its analysis is not served)
    const { fetch } = sourcesFetch({ fail: (url) => missing && url.includes(ORGAN) });
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await clockTo(ctx, 25.2);
    const third = layer.getStatus().retryAtSec!;
    expect(Math.round(third - 25.2)).toBe(30);
    missing = false;
    await clockTo(ctx, third + 0.5);
    expect(layer.getStatus().state).toBe('playing');

    // A later piece cannot be played: the first wait is 15 s again, not the next in the old run
    layer.setSetting('recording');
    await clockTo(ctx, ctx.currentTime + 3.5);
    const status = layer.getStatus();
    expect(status.state).toBe('unavailable');
    expect(status.reason).toMatch(/recording-analysis\.json holds no analysis/);
    expect(status.retryAtSec! - ctx.currentTime).toBeGreaterThan(14.5);
    expect(status.retryAtSec! - ctx.currentTime).toBeLessThanOrEqual(15);
    layer.stop();
  });

  it('does not keep a failed chapel response, so the next try fetches it again', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    let missing = true;
    const { fetch, requests } = sourcesFetch({ fail: (url) => missing && url.includes('aachen-nikolauskapelle') });
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(layer.getStatus().state).toBe('unavailable');
    expect(layer.getStatus().reason).toMatch(/chapel response unavailable \(503\)/);
    missing = false;
    await clockTo(ctx, 25.2);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', retryAtSec: null });
    expect(requests.filter((url) => url.includes('aachen-nikolauskapelle'))).toHaveLength(2);
  });

  it('does not try again with no sources base, after Audio off, or while the context is not running', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    // No sources base: nothing is served, so nothing to try
    const none = sourcesFetch();
    const unserved = new MusicLayer({ setting: 'organ-pulse', sourcesBase: null, fetch: none.fetch });
    await unserved.start(new FakeContext() as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(unserved.getStatus()).toMatchObject({ state: 'unavailable', retryAtSec: null });
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(none.requests).toEqual([]);

    // Audio off cancels the next try
    const off = sourcesFetch({ fail: (url) => url.includes(ORGAN) });
    const stopped = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch: off.fetch });
    stopped.setStars(STARS);
    const ctx = new FakeContext();
    await stopped.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(stopped.getStatus().retryAtSec).toBe(25);
    stopped.stop();
    expect(stopped.getStatus().retryAtSec).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    const asked = off.requests.length;
    await clockTo(ctx, 400);
    expect(off.requests).toHaveLength(asked);

    // A suspended context: no try, and no timer, until it runs; then the try comes on its clock
    let missing = true;
    const later = sourcesFetch({ fail: (url) => missing && url.includes(ORGAN) });
    const waiting = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch: later.fetch });
    waiting.setStars(STARS);
    const phone = new FakeContext();
    await waiting.start(phone as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    phone.setState('suspended');
    const before = later.requests.length;
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(later.requests).toHaveLength(before);
    expect(vi.getTimerCount()).toBe(0);
    missing = false;
    phone.setState('running');
    await clockTo(phone, 25.2);
    expect(waiting.getStatus()).toMatchObject({ state: 'playing', retryAtSec: null });
  });
});


describe('MusicLayer playing a selected star', () => {
  const SIRIUS: MusicSelection = { id: 'f-sirius', evidenceClass: 'catalog_matched', temperatureK: 9940, panX: 0.4 };
  const PLAIN: MusicSelection = { id: 'f-spot', evidenceClass: 'observed', temperatureK: null, panX: null };

  async function playing() {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    return { layer, ctx, music, requests, arrangement: organAndPulse(STARS.slice(0, 2)) };
  }
  /** Sources started on the context so far: the pass's notes and any selection's. */
  const notesStarted = (ctx: FakeContext) => ctx.sources().filter((s) => s.starts.length > 0).length;

  it('plays it on the next beat of the playing pass, in its bar, panned, and coloured by its catalog temperature', async () => {
    const { layer, ctx, arrangement } = await playing();
    await clockTo(ctx, 12.1);
    const before = ctx.sources().length;
    const filters = ctx.nodes.filter((n) => n.kind === 'biquad').length;
    layer.playSelection(SIRIUS);
    await vi.advanceTimersByTimeAsync(0);
    const added = ctx.sources().slice(before);
    expect(added).toHaveLength(1);
    const expected = selectionNote(SIRIUS, { arrangement, t0: 10.25 }, 12.1, 0.25)!;
    // The organ piece runs at 60 beats a minute from 10.25 s: the beat after 12.35 s is 13.25 s
    expect(added[0]!.starts[0]).toBeCloseTo(13.25, 6);
    expect(expected.timeSec).toBeCloseTo(13.25, 6);
    const lowpass = ctx.nodes.filter((n) => n.kind === 'biquad').slice(filters);
    expect(lowpass).toHaveLength(1);
    expect((lowpass[0]!.frequency as FakeParam).value).toBeCloseTo(expected.brightnessHz!, 6);
    const panner = ctx.nodes.filter((n) => n.kind === 'panner').at(-1)!;
    expect((panner.pan as FakeParam).value).toBeCloseTo(expected.pan, 9);
    layer.stop();
  });

  it('plays a finding without a catalog temperature as the plain instrument, with no filter', async () => {
    const { layer, ctx } = await playing();
    const filters = ctx.nodes.filter((n) => n.kind === 'biquad').length;
    const before = ctx.sources().length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    expect(ctx.sources().length).toBe(before + 1);
    expect(ctx.nodes.filter((n) => n.kind === 'biquad').length).toBe(filters);
    layer.stop();
  });

  it('plays nothing before Audio on, after Audio off, or while the context is not running', async () => {
    const { fetch } = sourcesFetch();
    const idle = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch });
    idle.playSelection(SIRIUS);
    const { layer, ctx } = await playing();
    const started = () => ctx.sources().filter((s) => s.starts.length > 0).length;
    const before = started();
    // Selected while the context is suspended: dropped, not played later when it runs again
    ctx.setState('suspended');
    layer.playSelection(SIRIUS);
    ctx.setState('running');
    await vi.advanceTimersByTimeAsync(0);
    expect(started()).toBe(before);
    // Selected, then Audio off before the note is scheduled: nothing
    layer.playSelection(SIRIUS);
    layer.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(started()).toBe(before);
    layer.playSelection(SIRIUS);
    await vi.advanceTimersByTimeAsync(0);
    expect(started()).toBe(before);
  });

  it('plays at once in a silence between pieces, in the key of the piece just played, straight to the layer output', async () => {
    const { layer, ctx, music, arrangement } = await playing();
    const end = 10.25 + arrangement.durationSec;
    await clockTo(ctx, end + 2);
    expect(layer.getStatus().state).toBe('gap');
    const before = ctx.sources().length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const added = ctx.sources().slice(before);
    expect(added).toHaveLength(1);
    expect(added[0]!.starts[0]).toBeCloseTo(ctx.currentTime + 0.25, 6);
    // In the key of the piece just played: the root of its last bar's set, in its data register
    const expected = selectionNote(PLAIN, { arrangement, t0: 10.25 }, ctx.currentTime, 0.25)!;
    expect(((expected.midi % 12) + 12) % 12).toBe(barPitchSet(arrangement.chords.at(-1)!.chord).root);
    const { semitones } = chooseZone((INSTRUMENTS[expected.instrument] as InstrumentDef).zones, expected.midi, 0, 0);
    expect((added[0]!.playbackRate as FakeParam).value).toBeCloseTo(playbackRate(semitones, 0), 9);
    // A piece that ended on its own is let go at once when the next is scheduled, which would cut a note still
    // sounding on its data bus: a note in the silence goes straight to the layer output
    expect(ctx.nodes.filter((n) => n.kind === 'panner').at(-1)!.outputs).toEqual([layerNodes(ctx, music).output]);
    layer.stop();
  });

  /** A cool catalog star (made up for the test): its note needs a sample file the organ pass did not fetch. */
  const COOL: MusicSelection = { id: 'f-cool', evidenceClass: 'catalog_matched', temperatureK: 3_000, panX: -0.5 };
  /** The URL of the sample file the selection's note would play in the organ pass, at the clock now. */
  const noteFileUrl = (selection: MusicSelection, arrangement: ReturnType<typeof organAndPulse>, ctx: FakeContext) => {
    const note = selectionNote(selection, { arrangement, t0: 10.25 }, ctx.currentTime, 0.25)!;
    const { zone } = chooseZone((INSTRUMENTS[note.instrument] as InstrumentDef).zones, note.midi, note.layer, 0);
    return BASE + zone.file.split('/').map(encodeURIComponent).join('/');
  };

  it('plays nothing once the Music setting is off, though a piece played before, and fetches nothing for it', async () => {
    const { layer, ctx, requests, arrangement } = await playing();
    layer.setSetting('off');
    await clockTo(ctx, 14);
    expect(layer.getStatus()).toMatchObject({ setting: 'off', state: 'off', reason: 'the music is off' });
    const before = notesStarted(ctx);
    const fetches = requests.length;
    expect(requests).not.toContain(noteFileUrl(COOL, arrangement, ctx));
    layer.playSelection(PLAIN);
    layer.playSelection(SIRIUS);
    layer.playSelection(COOL);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    expect(requests.length).toBe(fetches);
    layer.stop();
  });

  it('plays nothing after Audio off, the Music setting off, and Audio on again', async () => {
    const { layer, ctx, music, requests, arrangement } = await playing();
    layer.stop();
    layer.setSetting('off');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    await clockTo(ctx, 14);
    expect(layer.getStatus()).toMatchObject({ setting: 'off', state: 'off' });
    const before = notesStarted(ctx);
    const fetches = requests.length;
    expect(requests).not.toContain(noteFileUrl(COOL, arrangement, ctx));
    layer.playSelection(PLAIN);
    layer.playSelection(SIRIUS);
    layer.playSelection(COOL);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    expect(requests.length).toBe(fetches);
    layer.stop();
  });

  it("plays nothing when the Music setting goes off while the note's samples load", async () => {
    const { layer, ctx } = await playing();
    await clockTo(ctx, 12.1);
    const before = notesStarted(ctx);
    layer.playSelection(SIRIUS);
    layer.setSetting('off');
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    layer.stop();
  });

  it("plays nothing in the new run when Audio goes off and on again while the note's samples load", async () => {
    // The same Audio off and on with no selection: the notes the new run plays by itself
    const control = await playing();
    control.layer.stop();
    await control.layer.start(control.ctx as unknown as AudioContext, control.music as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(0);
    const { layer, ctx, music, arrangement } = await playing();
    const firstRun = notesStarted(ctx);
    const colour = selectionNote(SIRIUS, { arrangement, t0: 10.25 }, ctx.currentTime, 0.25)!.brightnessHz!;
    const lowpassesAtColour = () => ctx.nodes.filter((n) => n.kind === 'biquad' && Math.abs((n.frequency as FakeParam).value - colour) < 1e-6).length;
    layer.playSelection(SIRIUS);
    layer.stop();
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(0);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 2 });
    expect(notesStarted(ctx)).toBeGreaterThan(firstRun);
    expect(notesStarted(ctx)).toBe(notesStarted(control.ctx));
    expect(lowpassesAtColour()).toBe(0);
    layer.stop();
    control.layer.stop();
  });

  it("schedules nothing when the context stops running while the note's samples load, nor once it runs again", async () => {
    const { layer, ctx } = await playing();
    await clockTo(ctx, 12.1);
    const before = notesStarted(ctx);
    layer.playSelection(SIRIUS);
    ctx.setState('suspended');
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    ctx.setState('running');
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    layer.stop();
  });

  it("joins the playing pass's data bus beside its data notes, not the layer output", async () => {
    const { layer, ctx, music, arrangement } = await playing();
    await clockTo(ctx, 12.1);
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const bus = ctx.nodes.filter((n) => n.kind === 'panner').at(-1)!.outputs[0];
    expect(bus).not.toBe(layerNodes(ctx, music).output);
    // Every note of the pass on its data bus goes into that node, and now the selection's note too
    const dataNotes = arrangement.events.filter((e) => e.type === 'sample' && e.bus === 'data').length;
    expect(dataNotes).toBeGreaterThan(0);
    expect(ctx.nodes.filter((n) => n.kind === 'panner' && n.outputs.includes(bus))).toHaveLength(dataNotes + 1);
    // A coloured note reaches the same bus through its low-pass
    layer.playSelection(SIRIUS);
    await vi.advanceTimersByTimeAsync(0);
    expect(ctx.nodes.filter((n) => n.kind === 'biquad').at(-1)!.outputs).toEqual([bus]);
    layer.stop();
  });

  it('plays through the faded piece, straight to the layer output, while a piece fades after a moment change and the next has not started', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ moment: 'back_to_moment', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ piece: 'organ-pulse', state: 'playing', passes: 1 });
    // A star selected: the moment moves on, and once it has held 1.5 s the organ fades out over 3 s
    await clockTo(ctx, 12);
    layer.setMoment('star_card');
    await clockTo(ctx, 14);
    const { output, passes } = layerNodes(ctx, music);
    expect(passes).toHaveLength(1);
    expect((passes[0]!.gain as FakeParam).events.at(-1)).toMatchObject({ call: 'linear', value: 0 });
    expect(layer.getStatus()).toMatchObject({ piece: 'organ-pulse', passes: 1 });
    const before = ctx.sources().length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const added = ctx.sources().slice(before);
    expect(added).toHaveLength(1);
    // On the faded piece's beat and in its bar
    const expected = selectionNote(PLAIN, { arrangement: organAndPulse(STARS.slice(0, 2)), t0: 10.25 }, ctx.currentTime, 0.25)!;
    expect(added[0]!.starts[0]).toBeCloseTo(expected.timeSec, 6);
    const { semitones } = chooseZone((INSTRUMENTS[expected.instrument] as InstrumentDef).zones, expected.midi, expected.layer, 0);
    expect((added[0]!.playbackRate as FakeParam).value).toBeCloseTo(playbackRate(semitones, 0), 9);
    // Not into the fading pass's data bus, which would fade it out with the piece: the layer output
    expect(ctx.nodes.filter((n) => n.kind === 'panner').at(-1)!.outputs).toEqual([output]);
    // After the fade the next piece plays
    await clockTo(ctx, 18);
    expect(layer.getStatus()).toMatchObject({ piece: 'ambient-beats', state: 'playing', passes: 2 });
    layer.stop();
  });

  /**
   * Serves the sources (with the recording's analysis), fails every request, or leaves requests
   * unanswered as a host that does not answer would, on the test's say; `release` answers the held
   * ones as served.
   */
  function gatedSources() {
    const requests: string[] = [];
    const held: { url: string; answer: (response: Response) => void }[] = [];
    const gate = { mode: 'serve' as 'serve' | 'fail' | 'hold' };
    const served = (url: string) => (url.endsWith('recording-analysis.json') ? new Response(JSON.stringify(ANALYSIS)) : new Response(new Uint8Array(16)));
    const impl = async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      requests.push(url);
      if (gate.mode === 'hold') return new Promise<Response>((answer) => held.push({ url, answer }));
      if (gate.mode === 'fail') return new Response('unavailable in this test', { status: 503 });
      return served(url);
    };
    const release = () => {
      for (const { url, answer } of held.splice(0)) answer(served(url));
    };
    return { fetch: impl as unknown as typeof fetch, requests, gate, release };
  }
  /** The newest layer output into the music input: a try after a missing file makes a new one. */
  const newestOutput = (ctx: FakeContext, music: FakeNode) => ctx.nodes.filter((n) => n.outputs.includes(music)).at(-1)!;
  /** The data bus a note's last node joined, checked to be the newest pass's: other panners (its data notes) join it too. */
  const joinedPassBus = (ctx: FakeContext, music: FakeNode, node: FakeNode) => {
    const bus = node.outputs[0] as FakeNode;
    const output = newestOutput(ctx, music);
    const newestPass = ctx.nodes.filter((n) => n.outputs.includes(output)).at(-1)!;
    expect(bus).not.toBe(output);
    expect(ctx.nodes.indexOf(bus)).toBeGreaterThan(ctx.nodes.indexOf(newestPass));
    expect(ctx.nodes.filter((n) => n.kind === 'panner' && n.outputs.includes(bus)).length).toBeGreaterThan(1);
  };

  it('plays nothing while a try after a missing file runs, though a selection in the silence before loaded its sample, and plays once the try succeeds', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch, requests, gate, release } = gatedSources();
    const layer = new MusicLayer({ moment: 'back_to_moment', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ piece: 'organ-pulse', state: 'playing' });
    const end = 10.25 + organAndPulse(STARS.slice(0, 2)).durationSec;
    // In the silence after the organ a plain finding plays, loading its sample
    await clockTo(ctx, end + 2);
    expect(layer.getStatus().state).toBe('gap');
    let before = notesStarted(ctx);
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before + 1);
    // The next piece's files cannot be fetched: no music, and the page says so
    gate.mode = 'fail';
    await clockTo(ctx, end + 22);
    const failed = layer.getStatus();
    expect(failed).toMatchObject({ state: 'unavailable', why: 'not_reachable', piece: null });
    // The try: its first request is not answered yet, and the music stays unavailable
    gate.mode = 'hold';
    await clockTo(ctx, failed.retryAtSec! + 0.3);
    expect(layer.wantsStars()).toBe(true);
    const trying = layer.getStatus();
    expect(trying).toMatchObject({ state: 'unavailable', why: 'not_reachable', retryAtSec: null });
    before = notesStarted(ctx);
    const asked = requests.length;
    layer.playSelection({ id: 'f-spot-2', evidenceClass: 'observed', temperatureK: null, panX: 0.2 });
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before);
    expect(requests).toHaveLength(asked);
    expect(layer.getStatus()).toEqual(trying);
    // The files are served again: the try plays, and so does the next selection, on its data bus
    gate.mode = 'serve';
    release();
    await clockTo(ctx, ctx.currentTime + 1);
    expect(layer.getStatus()).toMatchObject({ state: 'playing', reason: null, why: null, passes: 2 });
    before = notesStarted(ctx);
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(before + 1);
    joinedPassBus(ctx, music, ctx.nodes.filter((n) => n.kind === 'panner').at(-1)!);
    layer.stop();
  });

  it('plays nothing, plain or coloured, and fetches nothing for it while a try after a missing file runs, then plays once the try succeeds', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch, requests, gate, release } = gatedSources();
    const layer = new MusicLayer({ moment: 'back_to_moment', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    expect(layer.getStatus()).toMatchObject({ piece: 'organ-pulse', state: 'playing' });
    // A star selected while the organ plays: the piece that fits the card cannot be fetched
    await clockTo(ctx, 12);
    gate.mode = 'fail';
    layer.setMoment('star_card');
    await clockTo(ctx, 20);
    const failed = layer.getStatus();
    expect(failed).toMatchObject({ state: 'unavailable', why: 'not_reachable', piece: null });
    gate.mode = 'hold';
    await clockTo(ctx, failed.retryAtSec! + 0.3);
    expect(layer.wantsStars()).toBe(true);
    const trying = layer.getStatus();
    expect(trying).toMatchObject({ state: 'unavailable', why: 'not_reachable', retryAtSec: null });
    // Catalog stars at a screen star's temperature take that star's tone, whose sample the organ loaded
    const LIKE_A: MusicSelection = { id: 'f-like-a', evidenceClass: 'catalog_matched', temperatureK: 5_000, panX: 0 };
    const LIKE_B: MusicSelection = { id: 'f-like-b', evidenceClass: 'catalog_matched', temperatureK: 12_000, panX: 0 };
    const before = notesStarted(ctx);
    const asked = requests.length;
    for (const selection of [PLAIN, SIRIUS, COOL, LIKE_A, LIKE_B]) {
      layer.playSelection(selection);
      await vi.advanceTimersByTimeAsync(0);
    }
    expect(notesStarted(ctx)).toBe(before);
    expect(requests).toHaveLength(asked);
    expect(layer.getStatus()).toEqual(trying);
    // The files are served again: the try plays the card's piece, and a coloured selection plays on its data bus
    gate.mode = 'serve';
    release();
    await clockTo(ctx, ctx.currentTime + 1);
    expect(layer.getStatus()).toMatchObject({ piece: 'ambient-beats', state: 'playing', reason: null, why: null });
    const played = notesStarted(ctx);
    const filters = ctx.nodes.filter((n) => n.kind === 'biquad').length;
    layer.playSelection(SIRIUS);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(played + 1);
    const lowpass = ctx.nodes.filter((n) => n.kind === 'biquad').slice(filters);
    expect(lowpass).toHaveLength(1);
    joinedPassBus(ctx, music, lowpass[0]!);
    layer.stop();
  });

  it('sends a note selected late in a silence straight to the layer output, so letting the ended piece go never cuts it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const end = 10.25 + organAndPulse(STARS.slice(0, 2)).durationSec;
    // With random() at 0 the silence after a pinned piece lasts 20 s: select 1 s before it ends
    await clockTo(ctx, end + 19);
    expect(layer.getStatus().state).toBe('gap');
    const { output, passes } = layerNodes(ctx, music);
    const ended = passes.at(-1)!;
    const before = ctx.nodes.length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const panner = ctx.nodes.slice(before).find((n) => n.kind === 'panner')!;
    expect(panner.outputs).toEqual([output]);
    // The next pass is scheduled and the ended one let go while the note still sounds; the note's way out stays
    await clockTo(ctx, end + 24);
    expect(layer.getStatus().passes).toBe(2);
    expect(ended.disconnected).toBe(true);
    expect(output.disconnected).toBe(false);
    layer.stop();
  });

  it('sends a note that would outlast its piece straight to the layer output, though the piece still plays', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const end = 10.25 + organAndPulse(STARS.slice(0, 2)).durationSec;
    // Half a second before the piece ends: its next beat leaves no room for the held note and its release
    await clockTo(ctx, end - 0.5);
    expect(layer.getStatus().state).toBe('playing');
    const { output } = layerNodes(ctx, music);
    const before = ctx.nodes.length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const panner = ctx.nodes.slice(before).find((n) => n.kind === 'panner')!;
    expect(panner.outputs).toEqual([output]);
    layer.stop();
  });

  it("counts the note's release: one whose key lifts inside the piece but whose release runs past it goes to the layer output", async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const arrangement = organAndPulse(STARS.slice(0, 2));
    const end = 10.25 + arrangement.durationSec;
    // The note is held 1.2 s, then released: find a clock time whose planned note lifts its key before the piece
    // ends and finishes its release after
    const heldSec = 1.2;
    let at: number | null = null;
    for (let t = end - 6; t < end && at === null; t += 0.01) {
      const note = selectionNote(PLAIN, { arrangement, t0: 10.25 }, t, 0.25)!;
      const releaseSec = (INSTRUMENTS[note.instrument] as InstrumentDef).releaseSec;
      if (note.timeSec + heldSec <= end && note.timeSec + heldSec + releaseSec > end) at = t;
    }
    expect(at).not.toBeNull();
    await clockTo(ctx, at!);
    expect(layer.getStatus().state).toBe('playing');
    const { output } = layerNodes(ctx, music);
    const before = ctx.nodes.length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const panner = ctx.nodes.slice(before).find((n) => n.kind === 'panner')!;
    expect(panner.outputs).toEqual([output]);
    layer.stop();
  });

  it("joins the playing pass's data bus when the note's release ends just inside the piece", async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ setting: 'organ-pulse', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const arrangement = organAndPulse(STARS.slice(0, 2));
    const end = 10.25 + arrangement.durationSec;
    // The latest clock time, near the end, whose planned note finishes its hold and release inside the piece
    const heldSec = 1.2;
    let at: number | null = null;
    for (let t = end - 1; t > end - 8 && at === null; t -= 0.01) {
      const note = selectionNote(PLAIN, { arrangement, t0: 10.25 }, t, 0.25)!;
      const releaseSec = (INSTRUMENTS[note.instrument] as InstrumentDef).releaseSec;
      if (note.timeSec + heldSec + releaseSec <= end && note.timeSec + heldSec + releaseSec > end - 1) at = t;
    }
    expect(at).not.toBeNull();
    await clockTo(ctx, at!);
    expect(layer.getStatus().state).toBe('playing');
    const before = ctx.nodes.length;
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    const panner = ctx.nodes.slice(before).find((n) => n.kind === 'panner')!;
    joinedPassBus(ctx, music, panner);
    layer.stop();
  });

  it('plays nothing after a failure, Audio off and on, until a piece plays again', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch, gate, release } = gatedSources();
    const layer = new MusicLayer({ moment: 'back_to_moment', sourcesBase: BASE, fetch, random: () => 0 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    const end = 10.25 + organAndPulse(STARS.slice(0, 2)).durationSec;
    await clockTo(ctx, end + 2);
    // The next piece's files cannot be fetched: no music, and the page says so
    gate.mode = 'fail';
    await clockTo(ctx, end + 22);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable', why: 'not_reachable' });
    // Audio off and on: the new run's first request is not answered yet
    layer.stop();
    gate.mode = 'hold';
    void layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    await clockTo(ctx, ctx.currentTime + 0.5);
    expect(layer.getStatus().state).toBe('loading');
    const before = notesStarted(ctx);
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    // No note in the key of a piece from before the failure
    expect(notesStarted(ctx)).toBe(before);
    gate.mode = 'serve';
    release();
    await clockTo(ctx, ctx.currentTime + 1);
    expect(layer.getStatus().state).toBe('playing');
    const played = notesStarted(ctx);
    layer.playSelection(PLAIN);
    await vi.advanceTimersByTimeAsync(0);
    expect(notesStarted(ctx)).toBe(played + 1);
    layer.stop();
  });
});

/** Lets every pending fetch and decode in the fakes settle. */
async function flushAll(): Promise<void> {
  for (let i = 0; i < 50; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}
