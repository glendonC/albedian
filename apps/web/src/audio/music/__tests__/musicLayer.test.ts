import { afterEach, describe, expect, it, vi } from 'vitest';
import { RECORDING, ambientBeats, organAndPulse, type RecordingAnalysis } from '../directions';
import { MusicLayer, neededZones } from '../MusicLayer';
import { SampleBank } from '../sampler';
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
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable' });
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
