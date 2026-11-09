/**
 * Plays the chosen music direction (musicDirection.ts) in the live app, through the engine's music
 * input on its master bus, so the volume slider and the Audio control govern it (AUDIO_ENGINE.md
 * section 13). Each pass of the piece is built from the catalog stars on screen when it starts:
 * they play its data part, and a star missing a value plays nothing. When a pass ends the next one
 * is built from the stars then on screen.
 *
 * The samples and C's recording are fetched from `sourcesBase` (in development, the folder
 * `apps/web/spike/music/serveSources.mjs` serves). Without them the music stays silent and the
 * status says why; nothing stands in for a missing file.
 *
 * `scheduleArrangement` is the one scheduler: the demo page (spike/music/main.ts) renders with it
 * offline from time 0, and the layer plays with it on the live context from the clock plus a lead.
 */

import { energyNormalize } from '../dsp';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb';
import { compressorMakeupGainDb } from '../engine/MasterBus';
import {
  INSTRUMENTS,
  RECORDING,
  ambientBeats,
  organAndPulse,
  recordingArrangement,
  type Arrangement,
  type BusId,
  type InstrumentDef,
  type RecordingAnalysis,
} from './directions';
import type { MusicDirectionId } from './musicDirection';
import { SampleBank, chooseZone, playSample, type SampleZone } from './sampler';
import type { ScreenStar } from './starNotes';
import { duckUnderKicks, pingPongDelay, playKick, playPad, playRiser, playSub, syntheticHallImpulse } from './synth';

/** The glue compressor: hard knee, so its automatic makeup gain has a closed form and is divided out. */
const GLUE = { thresholdDb: -18, ratio: 2, attackSec: 0.03, releaseSec: 0.25 } as const;

const dbToGain = (db: number) => 10 ** (db / 20);
const fileUrl = (base: string, file: string) => base + file.split('/').map(encodeURIComponent).join('/');
const errorMessage = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Every zone a note may use: the root and layer chooseZone picks, with all of that pair's round robins. */
export function neededZones(arrangement: Arrangement): SampleZone[] {
  const out = new Map<string, SampleZone>();
  for (const e of arrangement.events) {
    if (e.type !== 'sample') continue;
    const zones = (INSTRUMENTS[e.instrument] as InstrumentDef).zones;
    const { zone } = chooseZone(zones, e.midi, e.layer, 0);
    for (const z of zones) if (z.rootMidi === zone.rootMidi && z.layer === zone.layer) out.set(z.file, z);
  }
  return [...out.values()];
}

/** The chapel response the engine's reverb uses, energy-normalized to stereo. */
export async function loadChapelImpulse(ctx: BaseAudioContext, fetchImpl: typeof fetch = fetch): Promise<AudioBuffer> {
  const response = await fetchImpl(DEFAULT_IMPULSE_RESPONSE_URL);
  if (!response.ok) throw new Error(`chapel response unavailable (${response.status})`);
  const decoded = await ctx.decodeAudioData(await response.arrayBuffer()).catch((error: unknown) => {
    throw new Error(`chapel response ${String(DEFAULT_IMPULSE_RESPONSE_URL)} could not be decoded: ${errorMessage(error)}`);
  });
  const channels = energyNormalize([decoded.getChannelData(0), decoded.getChannelData(decoded.numberOfChannels > 1 ? 1 : 0)]);
  const buffer = ctx.createBuffer(2, decoded.length, decoded.sampleRate);
  channels.forEach((data, c) => buffer.copyToChannel(data, c));
  return buffer;
}

/** Fetches and decodes a recording from the sources folder. */
export async function loadRecording(ctx: BaseAudioContext, sourcesBase: string, file: string, fetchImpl: typeof fetch = fetch): Promise<AudioBuffer> {
  const response = await fetchImpl(fileUrl(sourcesBase, file));
  if (!response.ok) throw new Error(`recording ${file} unavailable (${response.status})`);
  return ctx.decodeAudioData(await response.arrayBuffer());
}

/**
 * Schedules a whole arrangement into `destination`, its time 0 at `t0` on the context's clock: the
 * glue compressor, the room, the delay, the buses with their ducking, the recording and every event.
 * The bank must already hold every zone `neededZones` names.
 */
export function scheduleArrangement(
  ctx: BaseAudioContext,
  destination: AudioNode,
  arrangement: Arrangement,
  bank: SampleBank,
  t0: number,
  room: AudioBuffer,
  recording: AudioBuffer | null
): void {
  const master = ctx.createGain();
  const glue = ctx.createDynamicsCompressor();
  glue.threshold.value = GLUE.thresholdDb;
  glue.knee.value = 0;
  glue.ratio.value = GLUE.ratio;
  glue.attack.value = GLUE.attackSec;
  glue.release.value = GLUE.releaseSec;
  const makeup = ctx.createGain();
  makeup.gain.value = dbToGain(-compressorMakeupGainDb(GLUE.thresholdDb, GLUE.ratio));
  master.connect(glue);
  glue.connect(makeup);
  makeup.connect(destination);

  const convolver = ctx.createConvolver();
  convolver.normalize = false;
  convolver.buffer = room;
  const roomReturn = ctx.createGain();
  roomReturn.gain.value = dbToGain(arrangement.roomReturnDb);
  convolver.connect(roomReturn);
  roomReturn.connect(master);

  const delay = arrangement.delay ? pingPongDelay(ctx, arrangement.delay) : null;
  delay?.output.connect(master);

  const kickTimes = arrangement.events.flatMap((e) => (e.type === 'kick' ? [t0 + e.timeSec] : []));
  const buses = new Map<BusId, AudioNode>();
  for (const bus of arrangement.buses) {
    const input = ctx.createGain();
    input.gain.value = dbToGain(bus.gainDb);
    const duck = ctx.createGain();
    input.connect(duck);
    duck.connect(master);
    if (bus.duckUnderKick) duckUnderKicks(duck.gain, kickTimes, bus.duckUnderKick.depthDb, bus.duckUnderKick.releaseSec);
    if (bus.reverbSendDb !== null) {
      const send = ctx.createGain();
      send.gain.value = dbToGain(bus.reverbSendDb);
      duck.connect(send);
      send.connect(convolver);
    }
    if (bus.delaySendDb !== null && delay) {
      const send = ctx.createGain();
      send.gain.value = dbToGain(bus.delaySendDb);
      duck.connect(send);
      send.connect(delay.input);
    }
    buses.set(bus.id, input);
  }
  const busOf = (id: BusId): AudioNode => {
    const node = buses.get(id);
    if (!node) throw new Error(`${arrangement.id} uses bus ${id} but does not define it`);
    return node;
  };

  if (arrangement.recording) {
    if (!recording) throw new Error(`${arrangement.id} plays ${arrangement.recording.file}, which was not loaded`);
    const rec = arrangement.recording;
    const source = ctx.createBufferSource();
    source.buffer = recording;
    const fade = ctx.createGain();
    const g = dbToGain(rec.gainDb);
    fade.gain.setValueAtTime(0, t0);
    fade.gain.setValueAtTime(0, t0 + Math.min(0.3, rec.fadeInSec * 0.25));
    fade.gain.linearRampToValueAtTime(g, t0 + rec.fadeInSec);
    fade.gain.setValueAtTime(g, t0 + rec.durationSec - rec.fadeOutSec);
    fade.gain.linearRampToValueAtTime(0, t0 + rec.durationSec);
    source.connect(fade);
    fade.connect(busOf('recording'));
    source.start(t0, rec.fromSec, rec.durationSec + 0.05);
  }

  for (const e of arrangement.events) {
    const timeSec = t0 + e.timeSec;
    switch (e.type) {
      case 'sample': {
        const def = INSTRUMENTS[e.instrument] as InstrumentDef;
        playSample(ctx, bank, def, busOf(e.bus), { timeSec, midi: e.midi, durationSec: e.durationSec, gainDb: e.gainDb, pan: e.pan, layer: e.layer });
        break;
      }
      case 'pad':
        playPad(ctx, busOf(e.bus), { ...e, timeSec });
        break;
      case 'sub':
        playSub(ctx, busOf(e.bus), { ...e, timeSec });
        break;
      case 'kick':
        playKick(ctx, busOf(e.bus), timeSec, e.gainDb);
        break;
      case 'riser':
        playRiser(ctx, busOf(e.bus), timeSec, e.durationSec, e.gainDb);
        break;
    }
  }
}

export interface MusicLayerOptions {
  /** The direction to play (`resolveMusicDirection`); null plays none. */
  readonly direction: MusicDirectionId | null;
  /** URL prefix of the sources folder, ending in '/'; null when no samples are served. */
  readonly sourcesBase: string | null;
  readonly fetch?: typeof fetch;
  /** Seconds from Audio on (or a pass being ready) to its first note, so it is scheduled ahead. Default 0.25. */
  readonly leadSec?: number;
}

export type MusicState = 'off' | 'loading' | 'playing' | 'unavailable';

export interface MusicStatus {
  readonly direction: MusicDirectionId | null;
  readonly state: MusicState;
  /** Why no music plays, when none does for a reason other than Audio being off. */
  readonly reason: string | null;
  /** Distinct stars the current pass plays in its data part. */
  readonly starsPlayed: number;
  /** Passes of the piece scheduled since the layer was made. */
  readonly passes: number;
}

/** A pass is built and its samples loaded this long before the previous one ends. */
const PREPARE_SEC = 3;
/** Audio off fades the music out over this long, then disconnects it. */
const STOP_FADE_SEC = 0.25;
/**
 * Audio on comes before the feed has read the tiles in view (on a cold start their astro columns
 * arrive several seconds later): the first pass waits this long for stars, loading its composed
 * parts' samples meanwhile.
 */
const FIRST_STARS_WAIT_MS = 8_000;

const isComplete = (star: ScreenStar) => star.teffK != null && star.luminositySolar != null && star.distancePc != null;

export class MusicLayer {
  private stars: readonly ScreenStar[] = [];
  private ctx: BaseAudioContext | null = null;
  private bank: SampleBank | null = null;
  private readonly rooms = new Map<Arrangement['room'], Promise<AudioBuffer>>();
  private analysis: Promise<RecordingAnalysis> | null = null;
  private recording: Promise<AudioBuffer> | null = null;
  private output: GainNode | null = null;
  private token = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private status: MusicStatus;
  private starsArrived: (() => void) | null = null;

  constructor(private readonly options: MusicLayerOptions) {
    this.status = { direction: options.direction, state: 'off', reason: null, starsPlayed: 0, passes: 0 };
  }

  get direction(): MusicDirectionId | null {
    return this.options.direction;
  }

  /** The catalog stars on screen; only those with a temperature, a luminosity and a distance play. */
  setStars(stars: readonly ScreenStar[]): void {
    this.stars = stars.filter(isComplete);
    if (this.stars.length > 0) this.starsArrived?.();
  }

  getStatus(): MusicStatus {
    return this.status;
  }

  /**
   * Starts the piece on `ctx` into `destination`. Call after the engine's context exists (it is
   * made in the user's gesture). Resolves once the first pass is scheduled, or the music is silent.
   */
  async start(ctx: BaseAudioContext, destination: AudioNode): Promise<void> {
    if (this.output) return;
    const direction = this.options.direction;
    if (direction === null) {
      this.setStatus({ state: 'off', reason: 'no music direction chosen' });
      return;
    }
    const base = this.options.sourcesBase;
    if (!base) {
      this.setStatus({ state: 'unavailable', reason: 'the music samples are not served here (VITE_ALBEDIAN_MUSIC_SOURCES is not set), so the music stays silent' });
      return;
    }
    if (this.ctx !== ctx) {
      this.ctx = ctx;
      this.bank = new SampleBank(ctx, base, this.fetchImpl);
      this.rooms.clear();
      this.recording = null;
    }
    const token = ++this.token;
    const output = ctx.createGain();
    output.connect(destination);
    this.output = output;
    this.setStatus({ state: 'loading', reason: null });
    const preload = this.arrangementFor(direction, [], base).then((composed) => this.bank!.load(neededZones(composed)));
    try {
      await Promise.all([this.firstStars(), preload]);
    } catch (error) {
      // A file the piece needs is missing: say which now rather than after the wait
      this.starsArrived?.();
      if (token === this.token) this.fail(error);
      return;
    }
    if (token !== this.token) return;
    await this.schedulePass(token, ctx, output, direction, base, ctx.currentTime + this.lead);
  }

  /** Resolves when a star with all its values is on screen, or after FIRST_STARS_WAIT_MS without one. */
  private firstStars(): Promise<void> {
    if (this.stars.length > 0) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        clearTimeout(timer);
        this.starsArrived = null;
        resolve();
      };
      const timer = setTimeout(done, FIRST_STARS_WAIT_MS);
      this.starsArrived = done;
    });
  }

  /** Fades the music out and schedules no further pass. */
  stop(): void {
    this.token += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const output = this.output;
    this.output = null;
    if (output && this.ctx) {
      const now = this.ctx.currentTime;
      output.gain.cancelScheduledValues(now);
      output.gain.setValueAtTime(output.gain.value, now);
      output.gain.linearRampToValueAtTime(0, now + STOP_FADE_SEC);
      setTimeout(() => output.disconnect(), (STOP_FADE_SEC + 0.1) * 1000);
    }
    if (this.status.state !== 'unavailable') this.setStatus({ state: 'off', reason: null });
  }

  private get lead(): number {
    return this.options.leadSec ?? 0.25;
  }

  /** The fetch to use, bound so it is never called as this layer's method (a browser refuses that). */
  private get fetchImpl(): typeof fetch {
    const impl = this.options.fetch ?? fetch;
    return (input, init) => impl(input, init);
  }

  private async schedulePass(token: number, ctx: BaseAudioContext, output: GainNode, direction: MusicDirectionId, base: string, plannedT0: number): Promise<void> {
    try {
      const stars = this.stars;
      const arrangement = await this.arrangementFor(direction, stars, base);
      await this.bank!.load(neededZones(arrangement));
      const room = await this.roomFor(ctx, arrangement.room);
      const recording = arrangement.recording ? await (this.recording ??= loadRecording(ctx, base, arrangement.recording.file, this.fetchImpl)) : null;
      if (token !== this.token) return;
      const t0 = Math.max(plannedT0, ctx.currentTime + this.lead);
      scheduleArrangement(ctx, output, arrangement, this.bank!, t0, room, recording);
      const played = new Set(arrangement.events.flatMap((e) => (e.type === 'sample' && e.star ? [e.star.starId] : [])));
      this.setStatus({ state: 'playing', reason: null, starsPlayed: played.size, passes: this.status.passes + 1 });
      const next = t0 + arrangement.durationSec;
      this.timer = setTimeout(
        () => void this.schedulePass(token, ctx, output, direction, base, next),
        Math.max(0, (next - PREPARE_SEC - ctx.currentTime) * 1000)
      );
    } catch (error) {
      if (token === this.token) this.fail(error);
    }
  }

  /** No stand-in: a missing file leaves the music silent and says which. */
  private fail(error: unknown): void {
    this.output?.disconnect();
    this.output = null;
    this.setStatus({ state: 'unavailable', reason: errorMessage(error) });
  }

  private async arrangementFor(direction: MusicDirectionId, stars: readonly ScreenStar[], base: string): Promise<Arrangement> {
    if (direction === 'organ-pulse') return organAndPulse(stars);
    if (direction === 'ambient-beats') return ambientBeats(stars);
    // C plays inside the recording's measured beats and chords (analysis.ts, measured when the demo page was rendered)
    this.analysis ??= this.fetchAnalysis(base);
    return recordingArrangement(stars, await this.analysis);
  }

  private async fetchAnalysis(base: string): Promise<RecordingAnalysis> {
    const url = `${base}recording-analysis.json`;
    try {
      const response = await this.fetchImpl(url);
      if (!response.ok) throw new Error(`${url} unavailable (${response.status})`);
      const analysis = (await response.json()) as RecordingAnalysis | null;
      if (!analysis || analysis.file !== RECORDING.file) throw new Error(`${url} holds no analysis of ${RECORDING.file}`);
      return analysis;
    } catch (error) {
      this.analysis = null;
      throw error;
    }
  }

  private roomFor(ctx: BaseAudioContext, room: Arrangement['room']): Promise<AudioBuffer> {
    let buffer = this.rooms.get(room);
    if (!buffer) {
      buffer = room === 'chapel' ? loadChapelImpulse(ctx, this.fetchImpl) : Promise.resolve(syntheticHallImpulse(ctx));
      this.rooms.set(room, buffer);
    }
    return buffer;
  }

  private setStatus(change: Partial<MusicStatus>): void {
    this.status = { ...this.status, direction: this.options.direction, ...change };
  }
}
