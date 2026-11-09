/**
 * Plays the soundtrack (soundtrack.ts) in the live app, through the engine's music input on its
 * master bus, so the volume slider and the Audio control govern it (AUDIO_ENGINE.md section 13).
 * The director picks what plays when; this layer carries out its commands on the AudioContext's
 * clock. Each pass of a piece is arranged from the catalog stars on screen when it starts: they play
 * its data part, and a star missing a value plays nothing. Each pass has its own gain, so a fade
 * on a moment change touches only the pass that no longer fits.
 *
 * The samples and the recording are fetched from `sourcesBase` (in development, the folder
 * `apps/web/spike/music/serveSources.mjs` serves). Without them the music stays silent and the
 * status says why; nothing stands in for a missing file.
 *
 * `scheduleArrangement` is the one scheduler: the demo page (spike/music/main.ts) renders with it
 * offline from time 0, and the layer plays with it on the live context from the clock plus a lead.
 */

import { energyNormalize } from '../dsp';
import { DEFAULT_IMPULSE_RESPONSE_URL } from '../engine/ConvolutionReverb';
import { compressorMakeupGainDb } from '../engine/MasterBus';
import { INSTRUMENTS, RECORDING, type Arrangement, type BusId, type InstrumentDef, type RecordingAnalysis } from './directions';
import type { MusicSetting } from './musicSetting';
import { SampleBank, chooseZone, playSample, type SampleZone } from './sampler';
import {
  SOUNDTRACK,
  SoundtrackDirector,
  type DirectorCommand,
  type PieceId,
  type PieceSources,
  type SoundtrackMoment,
  type SoundtrackPiece,
} from './soundtrack';
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
  /** The Music setting to start with: 'soundtrack' (default), 'off', or a piece id to pin. */
  readonly setting?: MusicSetting;
  /** The moment of the visit to start with; 'arrive' by default. */
  readonly moment?: SoundtrackMoment;
  /** URL prefix of the sources folder, ending in '/'; null when no samples are served. */
  readonly sourcesBase: string | null;
  readonly fetch?: typeof fetch;
  /** Seconds from Audio on (or a pass being ready) to its first note, so it is scheduled ahead. Default 0.25. */
  readonly leadSec?: number;
  /** The pieces the director picks from; `SOUNDTRACK` by default. */
  readonly pieces?: readonly SoundtrackPiece[];
  /** Uniform draws in [0, 1) for the director's gaps and choices; Math.random by default. */
  readonly random?: () => number;
  /** Told each time a piece starts playing, for its credits. */
  readonly onPieceStarted?: (piece: PieceId) => void;
  /** Told each time the status changes, so the page can say whether music can play. */
  readonly onStatus?: (status: MusicStatus) => void;
}

/** off: Audio off, or the setting is off. loading: a piece is being prepared. gap: the silence between pieces. */
export type MusicState = 'off' | 'loading' | 'playing' | 'gap' | 'unavailable';

export interface MusicStatus {
  readonly setting: MusicSetting;
  readonly moment: SoundtrackMoment;
  /** The piece loading, playing or fading out; null otherwise. */
  readonly piece: PieceId | null;
  readonly state: MusicState;
  /** Why no music plays, when none does for a reason other than Audio being off. */
  readonly reason: string | null;
  /** While unavailable: no sources base ('not_served'), or a file that could not be fetched ('not_reachable'). */
  readonly why: 'not_served' | 'not_reachable' | null;
  /** Distinct stars the current pass plays in its data part. */
  readonly starsPlayed: number;
  /** Passes scheduled since the layer was made. */
  readonly passes: number;
  /** After a missing file, when the layer tries again, on the context's clock; null when no try waits. */
  readonly retryAtSec: number | null;
}

/** Audio off fades the music out over this long, then disconnects it. */
const STOP_FADE_SEC = 0.25;
/**
 * Audio on comes before the feed has read the tiles in view (on a cold start their astro columns
 * arrive several seconds later): the first pass waits this long for stars, loading its composed
 * parts' samples meanwhile.
 */
const FIRST_STARS_WAIT_MS = 8_000;
/** Why no music plays where no samples are served. */
const NOT_SERVED = 'the music samples are not served here (VITE_ALBEDIAN_MUSIC_SOURCES is not set), so the music stays silent';
/** The director's timer never fires sooner than this, so a clock that lags the timer cannot spin it. */
const MIN_TIMER_MS = 50;
/**
 * After a missing file, while Audio is on, the layer tries again after these waits, in seconds on the
 * context's clock, the last repeating: a sources server started late is found within two minutes.
 */
const RETRY_WAITS_SEC = [15, 30, 60, 120] as const;

const isComplete = (star: ScreenStar) => star.teffK != null && star.luminositySolar != null && star.distancePc != null;

export class MusicLayer {
  private stars: readonly ScreenStar[] = [];
  private ctx: BaseAudioContext | null = null;
  private bank: SampleBank | null = null;
  private readonly rooms = new Map<Arrangement['room'], Promise<AudioBuffer>>();
  private analysis: Promise<RecordingAnalysis> | null = null;
  private readonly recordings = new Map<string, Promise<AudioBuffer>>();
  /** The layer's output into the engine's music input, while Audio is on. */
  private output: GainNode | null = null;
  /** The pass last scheduled, with its own gain under `output`. */
  private pass: { readonly gain: GainNode; readonly piece: string } | null = null;
  /** Counts Audio on and off, so work begun before either finds itself stale. */
  private run = 0;
  /** Counts passes asked for and fades, so a pass still loading when faded never starts. */
  private passToken = 0;
  private firstPass = true;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  /** Tries made since the music last played or Audio was turned on. */
  private retries = 0;
  /** A try is under way: the music stays unavailable until a pass starts, so the page does not flicker. */
  private retrying = false;
  private destination: AudioNode | null = null;
  private status: MusicStatus;
  private starsArrived: (() => void) | null = null;
  private readonly pieces: readonly SoundtrackPiece[];
  private readonly director: SoundtrackDirector;
  /** Resolves when the context runs again, while it does not (a phone's lifecycle suspended it). */
  private resumed: Promise<void> | null = null;
  private stopWaitingToRun: (() => void) | null = null;

  constructor(private readonly options: MusicLayerOptions) {
    this.pieces = options.pieces ?? SOUNDTRACK;
    this.director = new SoundtrackDirector(this.pieces, {
      setting: options.setting ?? 'soundtrack',
      moment: options.moment ?? 'arrive',
      ...(options.random ? { random: options.random } : {}),
    });
    const { setting, moment } = this.director.state;
    this.status = { setting: setting as MusicSetting, moment, piece: null, state: 'off', reason: null, why: null, starsPlayed: 0, passes: 0, retryAtSec: null };
    // Known from the start: with no samples served, no music can play here
    if (!options.sourcesBase) this.status = { ...this.status, state: 'unavailable', reason: NOT_SERVED, why: 'not_served' };
  }

  /**
   * Whether the music wants the stars on screen: while it can play, or while a try after a missing file
   * runs (so the pass it recovers with plays them), and never with the music off.
   */
  wantsStars(): boolean {
    return (this.status.state !== 'unavailable' || this.retrying) && this.setting !== 'off';
  }

  get setting(): MusicSetting {
    return this.director.state.setting as MusicSetting;
  }

  /** The catalog stars on screen; only those with a temperature, a luminosity and a distance play. */
  setStars(stars: readonly ScreenStar[]): void {
    this.stars = stars.filter(isComplete);
    if (this.stars.length > 0) this.starsArrived?.();
  }

  getStatus(): MusicStatus {
    return this.status;
  }

  /** The moment of the visit (soundtrackMoment.ts). */
  setMoment(moment: SoundtrackMoment): void {
    void this.answer(this.director.setMoment(moment, this.clock()));
  }

  /** The Music setting: the soundtrack, off, or a pinned piece. */
  setSetting(setting: MusicSetting): void {
    void this.answer(this.director.setSetting(setting, this.clock()));
  }

  /**
   * Starts the soundtrack on `ctx` into `destination`. Call after the engine's context exists (it is
   * made in the user's gesture). Resolves once the first pass is scheduled, or the music is silent.
   */
  async start(ctx: BaseAudioContext, destination: AudioNode): Promise<void> {
    if (this.output) return;
    this.retries = 0;
    this.retrying = false;
    this.disarmRetry();
    await this.begin(ctx, destination);
  }

  /** Audio on, or a try after a missing file. */
  private async begin(ctx: BaseAudioContext, destination: AudioNode): Promise<void> {
    if (this.output) return;
    const base = this.options.sourcesBase;
    if (!base) {
      this.setStatus({ state: 'unavailable', reason: NOT_SERVED, why: 'not_served' });
      return;
    }
    if (this.ctx !== ctx) {
      this.ctx = ctx;
      this.bank = new SampleBank(ctx, base, this.fetchImpl);
      this.rooms.clear();
      this.recordings.clear();
    }
    this.run += 1;
    this.destination = destination;
    const output = ctx.createGain();
    output.connect(destination);
    this.output = output;
    this.firstPass = true;
    if (!this.retrying) this.setStatus({ state: 'off', reason: null, why: null });
    await this.answer(this.director.start(ctx.currentTime));
  }

  /** Fades the music out and plays nothing more until `start`. */
  stop(): void {
    this.run += 1;
    this.passToken += 1;
    this.disarm();
    this.disarmRetry();
    this.retries = 0;
    this.retrying = false;
    if (this.status.retryAtSec !== null) this.setStatus({ retryAtSec: null });
    this.stopWaitingToRun?.();
    this.director.stop();
    this.pass = null;
    const output = this.output;
    this.output = null;
    if (output && this.ctx) {
      const now = this.ctx.currentTime;
      output.gain.cancelScheduledValues(now);
      output.gain.setValueAtTime(output.gain.value, now);
      output.gain.linearRampToValueAtTime(0, now + STOP_FADE_SEC);
      setTimeout(() => output.disconnect(), (STOP_FADE_SEC + 0.1) * 1000);
    }
    this.syncStatus();
  }

  /** The context's clock while Audio is on; 0 before (the director then takes inputs at once). */
  private clock(): number {
    return this.output && this.ctx ? this.ctx.currentTime : 0;
  }

  /** Carries out the director's commands, re-arms its timer, and resolves once any new pass is scheduled. */
  private async answer(commands: readonly DirectorCommand[]): Promise<void> {
    const plays: Promise<void>[] = [];
    for (const command of commands) {
      if (command.kind === 'fade') this.fadePass(command.fadeSec);
      else plays.push(this.playPass(command.piece, command.atSec));
    }
    this.arm();
    this.syncStatus();
    await Promise.all(plays);
  }

  /**
   * Wakes the director when it next has something to do, timed from the context's clock. While the
   * context is not running its clock stands still, so no timer runs; the layer waits for it to run.
   */
  private arm(): void {
    this.disarm();
    const ctx = this.ctx;
    const wake = this.director.nextWakeSec();
    if (!ctx || !this.output || wake === null) return;
    const run = this.run;
    if (ctx.state !== 'running') {
      void this.untilRunning(ctx).then(() => {
        if (run === this.run) this.arm();
      });
      return;
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      if (run === this.run) void this.answer(this.director.tick(ctx.currentTime));
    }, Math.max(MIN_TIMER_MS, (wake - ctx.currentTime) * 1000));
  }

  private disarm(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  /** Resolves when the context runs: at once if it does, else on its next statechange to running. */
  private untilRunning(ctx: BaseAudioContext): Promise<void> {
    if (ctx.state === 'running') return Promise.resolve();
    this.resumed ??= new Promise<void>((resolve) => {
      const done = () => {
        ctx.removeEventListener('statechange', listener);
        this.resumed = null;
        this.stopWaitingToRun = null;
      };
      const listener = () => {
        if (ctx.state !== 'running') return;
        done();
        resolve();
      };
      ctx.addEventListener('statechange', listener);
      // Audio off while waiting: let go of the context; whatever waited is stale and stays waiting
      this.stopWaitingToRun = done;
    });
    return this.resumed;
  }

  /** Fades the pass that sounds; a pass still loading is dropped. */
  private fadePass(fadeSec: number): void {
    this.passToken += 1;
    const pass = this.pass;
    this.pass = null;
    if (!pass || !this.ctx) return;
    const now = this.ctx.currentTime;
    pass.gain.gain.cancelScheduledValues(now);
    pass.gain.gain.setValueAtTime(pass.gain.gain.value, now);
    pass.gain.gain.linearRampToValueAtTime(0, now + fadeSec);
    setTimeout(() => pass.gain.disconnect(), (fadeSec + 0.1) * 1000);
  }

  private async playPass(id: string, atSec: number): Promise<void> {
    const token = ++this.passToken;
    const run = this.run;
    const ctx = this.ctx!;
    const output = this.output!;
    const base = this.options.sourcesBase!;
    try {
      const piece = this.pieces.find((p) => p.id === id);
      if (!piece) throw new Error(`the soundtrack has no piece ${id}`);
      const sources = this.sources(base);
      if (this.firstPass) {
        this.firstPass = false;
        const preload = piece.arrange([], sources).then((composed) => this.loadZones(neededZones(composed)));
        try {
          await Promise.all([this.firstStars(), preload]);
        } catch (error) {
          // A file the piece needs is missing: say which now rather than after the wait
          this.starsArrived?.();
          throw error;
        }
      }
      const arrangement = await piece.arrange(this.stars, sources);
      await this.loadZones(neededZones(arrangement));
      const room = await this.roomFor(ctx, arrangement.room);
      const recording = arrangement.recording ? await this.recordingFor(ctx, base, arrangement.recording.file) : null;
      // Nothing is scheduled on a context that is not running: it waits until it runs
      await this.untilRunning(ctx);
      if (token !== this.passToken || run !== this.run) return;
      const t0 = Math.max(atSec, ctx.currentTime + this.lead);
      const gain = ctx.createGain();
      gain.connect(output);
      scheduleArrangement(ctx, gain, arrangement, this.bank!, t0, room, recording);
      // The pass before ended on its own before the gap: let it go
      this.pass?.gain.disconnect();
      this.pass = { gain, piece: piece.id };
      const played = new Set(arrangement.events.flatMap((e) => (e.type === 'sample' && e.star ? [e.star.starId] : [])));
      this.director.started(piece.id, t0, t0 + arrangement.durationSec);
      // Playing again after a missing file: the tries start over next time
      this.retries = 0;
      this.retrying = false;
      this.setStatus({ starsPlayed: played.size, passes: this.status.passes + 1 });
      this.options.onPieceStarted?.(piece.id);
      this.arm();
      this.syncStatus();
    } catch (error) {
      if (token === this.passToken && run === this.run) this.fail(error);
    }
  }

  /**
   * Loads the zones the bank lacks, one first: when the music files cannot be reached, a try costs one
   * failed request rather than one per file.
   */
  private async loadZones(zones: readonly SampleZone[]): Promise<void> {
    const bank = this.bank!;
    if (zones.length > 1) await bank.load(zones.slice(0, 1));
    await bank.load(zones);
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

  private get lead(): number {
    return this.options.leadSec ?? 0.25;
  }

  /** The fetch to use, bound so it is never called as this layer's method (a browser refuses that). */
  private get fetchImpl(): typeof fetch {
    const impl = this.options.fetch ?? fetch;
    return (input, init) => impl(input, init);
  }

  /**
   * No stand-in: a missing file leaves the music silent and says which. While Audio is on the layer
   * tries again later (RETRY_WAITS_SEC), fetching only what it still lacks.
   */
  private fail(error: unknown): void {
    this.run += 1;
    this.disarm();
    this.director.stop();
    this.output?.disconnect();
    this.output = null;
    this.pass = null;
    this.retrying = false;
    this.setStatus({ state: 'unavailable', reason: errorMessage(error), why: 'not_reachable', piece: null });
    this.scheduleRetry();
  }

  /** Sets the next try on the context's clock, the wait growing with each failed try (a layer with no sources base never fails a fetch). */
  private scheduleRetry(): void {
    const ctx = this.ctx;
    if (!ctx || !this.destination) return;
    const wait = RETRY_WAITS_SEC[Math.min(this.retries, RETRY_WAITS_SEC.length - 1)]!;
    this.retries += 1;
    this.setStatus({ retryAtSec: ctx.currentTime + wait });
    this.armRetry();
  }

  /** Wakes for the next try; no timer runs while the context does not (its clock stands still). */
  private armRetry(): void {
    this.disarmRetry();
    const ctx = this.ctx;
    const at = this.status.retryAtSec;
    if (!ctx || at === null) return;
    const run = this.run;
    if (ctx.state !== 'running') {
      void this.untilRunning(ctx).then(() => {
        if (run === this.run) this.armRetry();
      });
      return;
    }
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (run !== this.run || this.output) return;
      if (ctx.currentTime < at) {
        this.armRetry();
        return;
      }
      this.retrying = true;
      this.setStatus({ retryAtSec: null });
      void this.begin(ctx, this.destination!);
    }, Math.max(MIN_TIMER_MS, (at - ctx.currentTime) * 1000));
  }

  private disarmRetry(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private sources(base: string): PieceSources {
    return { recordingAnalysis: () => (this.analysis ??= this.fetchAnalysis(base)) };
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

  private recordingFor(ctx: BaseAudioContext, base: string, file: string): Promise<AudioBuffer> {
    let buffer = this.recordings.get(file);
    if (!buffer) {
      buffer = loadRecording(ctx, base, file, this.fetchImpl);
      buffer.catch(() => this.recordings.delete(file));
      this.recordings.set(file, buffer);
    }
    return buffer;
  }

  private roomFor(ctx: BaseAudioContext, room: Arrangement['room']): Promise<AudioBuffer> {
    let buffer = this.rooms.get(room);
    if (!buffer) {
      buffer = room === 'chapel' ? loadChapelImpulse(ctx, this.fetchImpl) : Promise.resolve(syntheticHallImpulse(ctx));
      // A failed response is not kept, so the next try fetches it again
      buffer.catch(() => this.rooms.delete(room));
      this.rooms.set(room, buffer);
    }
    return buffer;
  }

  /** The status from the director, unless the music is unavailable and Audio has not been turned on again. */
  private syncStatus(): void {
    const d = this.director.state;
    const base = { setting: d.setting as MusicSetting, moment: d.moment };
    if (this.status.state === 'unavailable' && (this.output === null || this.retrying)) {
      this.setStatus(base);
      return;
    }
    const state: MusicState = d.phase === 'starting' ? 'loading' : d.phase === 'playing' || d.phase === 'fading' ? 'playing' : d.phase === 'gap' ? 'gap' : 'off';
    const reason = d.phase === 'silent' && d.setting === 'off' ? 'the music is off' : null;
    this.setStatus({ ...base, piece: d.piece as PieceId | null, state, reason, why: null });
  }

  private setStatus(change: Partial<MusicStatus>): void {
    this.status = { ...this.status, ...change };
    this.options.onStatus?.(this.status);
  }
}
