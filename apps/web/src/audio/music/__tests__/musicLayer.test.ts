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

const flush = async () => {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setTimeout(resolve, 0));
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('MusicLayer', () => {
  it('plays nothing and fetches nothing when no direction is chosen', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ direction: null, sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toEqual([]);
    expect(ctx.nodes).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ direction: null, state: 'off', reason: 'no music direction chosen' });
  });

  it('stays silent and says why when the samples are not served', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ direction: 'organ-pulse', sourcesBase: null, fetch });
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toEqual([]);
    expect(ctx.sources()).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable' });
    expect(layer.getStatus().reason).toMatch(/samples are not served/);
  });

  it('stays silent and names the file when a sample cannot be fetched, with no stand-in sound', async () => {
    const { fetch } = sourcesFetch({ fail: (url) => url.includes('Pipe%20Organ') });
    const layer = new MusicLayer({ direction: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus().state).toBe('unavailable');
    expect(layer.getStatus().reason).toMatch(/Pipe Organ.*unavailable \(503\)/);
  });

  it('schedules the whole arrangement ahead of the clock into the music input, its stars as the data part', async () => {
    const { fetch, requests } = sourcesFetch();
    const layer = new MusicLayer({ direction: 'organ-pulse', sourcesBase: BASE, fetch, leadSec: 0.25 });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    await layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);

    // Every sample the arrangement needs, and the chapel response, fetched once each
    const expected = organAndPulse(STARS.slice(0, 2));
    const zones = neededZones(expected);
    for (const zone of zones) expect(requests).toContain(BASE + zone.file.split('/').map(encodeURIComponent).join('/'));
    expect(new Set(requests).size).toBe(requests.length);

    // One output into the music input; every source starts at or after the clock plus the lead
    const outputs = ctx.nodes.filter((n) => n.outputs.includes(music));
    expect(outputs).toHaveLength(1);
    const starts = ctx.sources().flatMap((s) => s.starts);
    expect(starts.length).toBe(expected.events.filter((e) => e.type === 'sample').length);
    expect(Math.min(...starts)).toBeGreaterThanOrEqual(10.25);
    expect(Math.max(...starts)).toBeLessThan(10.25 + expected.durationSec);

    // The star missing its temperature plays nothing; the two complete ones play the data part
    const status = layer.getStatus();
    expect(status).toMatchObject({ direction: 'organ-pulse', state: 'playing', reason: null, passes: 1 });
    expect(status.starsPlayed).toBe(new Set(expected.events.flatMap((e) => (e.type === 'sample' && e.star ? [e.star.starId] : []))).size);
    expect(status.starsPlayed).toBe(2);
  });

  it('plays the piece again from the stars then on screen, and stops all of it on stop', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ direction: 'ambient-beats', sourcesBase: BASE, fetch, leadSec: 0.25 });
    layer.setStars(STARS.slice(0, 1));
    const ctx = new FakeContext();
    const music = new FakeNode('music');
    const started = layer.start(ctx as unknown as AudioContext, music as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(0);
    await started;
    const first = ambientBeats(STARS.slice(0, 1));
    expect(layer.getStatus()).toMatchObject({ passes: 1, starsPlayed: 1 });

    // The stars change; the next pass, due at the end of the first, plays them
    layer.setStars(STARS);
    ctx.currentTime = 10 + first.durationSec - 2;
    await vi.advanceTimersByTimeAsync(first.durationSec * 1000);
    expect(layer.getStatus()).toMatchObject({ passes: 2, starsPlayed: 2 });
    const secondStarts = ctx.sources().slice(-1)[0]!.starts;
    expect(secondStarts[0]).toBeGreaterThanOrEqual(10.25 + first.durationSec);

    // Stop fades the output to zero, disconnects it and schedules no further pass
    layer.stop();
    const output = ctx.nodes.find((n) => n.outputs.includes(music))!;
    const fade = (output.gain as FakeParam).events.at(-1)!;
    expect(fade.value).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(output.disconnected).toBe(true);
    expect(layer.getStatus()).toMatchObject({ state: 'off', passes: 2 });
  });

  it('waits briefly for the first stars on screen, since Audio on comes before the feed has any', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    const { fetch } = sourcesFetch();
    const layer = new MusicLayer({ direction: 'organ-pulse', sourcesBase: BASE, fetch });
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
    const layer = new MusicLayer({ direction: 'organ-pulse', sourcesBase: BASE, fetch });
    layer.setStars(STARS.slice(2));
    const ctx = new FakeContext();
    const started = layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    await vi.advanceTimersByTimeAsync(7_000);
    expect(layer.getStatus()).toMatchObject({ state: 'loading', passes: 0 });
    await vi.advanceTimersByTimeAsync(2_000);
    await started;
    expect(layer.getStatus()).toMatchObject({ state: 'playing', passes: 1, starsPlayed: 0 });
  });

  it('plays direction C from the recording and its measured analysis, served beside the samples', async () => {
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
    const layer = new MusicLayer({ direction: 'recording', sourcesBase: BASE, fetch });
    layer.setStars(STARS);
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(requests).toContain(`${BASE}recording-analysis.json`);
    expect(requests).toContain(BASE + RECORDING.file.split('/').map(encodeURIComponent).join('/'));
    // The recording starts at the measured downbeat, on the clock plus the lead
    const recording = ctx.nodes.find((n) => n.kind === 'bufferSource' && n.starts.length > 0)!;
    expect(recording.starts[0]).toBeGreaterThanOrEqual(10);
    expect(layer.getStatus()).toMatchObject({ direction: 'recording', state: 'playing' });
  });

  it('has the sample bank call a browser-like fetch as a plain function, as the demo page uses it', async () => {
    const { fetch, requests } = sourcesFetch();
    const bank = new SampleBank(new FakeContext() as unknown as BaseAudioContext, BASE, fetch);
    await bank.load([{ file: 'vcsl/test.wav', rootMidi: null, layer: 0, roundRobin: 0 }]);
    expect(requests).toEqual([`${BASE}vcsl/test.wav`]);
  });

  it('says why direction C is silent when its analysis is missing', async () => {
    const { fetch } = sourcesFetch({ fail: (url) => url.endsWith('recording-analysis.json') });
    const layer = new MusicLayer({ direction: 'recording', sourcesBase: BASE, fetch });
    const ctx = new FakeContext();
    await layer.start(ctx as unknown as AudioContext, new FakeNode('music') as unknown as AudioNode);
    expect(ctx.sources().filter((s) => s.starts.length > 0)).toEqual([]);
    expect(layer.getStatus()).toMatchObject({ state: 'unavailable' });
    expect(layer.getStatus().reason).toMatch(/recording-analysis\.json unavailable \(503\)/);
  });
});
