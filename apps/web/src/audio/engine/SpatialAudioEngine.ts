import {
  REGIME_DISTANCE_WINDOWS,
  clamp,
  isFiniteNumber,
  planckHarmonicAmplitudes,
  skyglowBedLevelDb,
  type ScaleAudioRegime,
} from '../mappings.js';
import { periodicWaveCoefficients, schroederPhases } from '../dsp.js';
import {
  DEFAULT_VOICE_CAPACITY,
  IDENTITY_QUAT,
  assignIdentityPitches,
  distanceWindowFor,
  normalizeQuat,
  planVoice,
  selectVoices,
  type AudioSourceInput,
  type PlanOutcome,
  type Quat,
  type VoicePlan,
} from '../voicePlanning.js';
import { ConvolutionReverb, DEFAULT_IMPULSE_RESPONSE_URL, type ReverbStatus } from './ConvolutionReverb.js';
import { MasterBus } from './MasterBus.js';
import {
  createSkyglowBed,
  loadOmnitoneModule,
  type BedPreference,
  type BedRenderer,
  type OmnitoneLoader,
  type SkyglowBed,
} from './SkyglowBed.js';
import { VoicePool } from './VoicePool.js';
import { createBrowserAudioContext, type AudioContextFactory } from './createAudioContext.js';

export interface SpatialAudioEngineOptions {
  readonly contextFactory?: AudioContextFactory;
  /** `null` skips loading the impulse response (the wet path stays silent). */
  readonly impulseResponseUrl?: string | null;
  readonly bedPreference?: BedPreference;
  readonly capacity?: number;
  readonly fetchImpl?: typeof fetch;
  readonly loadOmnitone?: OmnitoneLoader;
}

export type SilentOutcome = Extract<PlanOutcome, { status: 'silent' }>;

export interface VoiceSnapshot extends VoicePlan {
  readonly slot: number | null;
  readonly pitchHz: number | null;
  readonly centroidHz: number | null;
}

export interface BedSnapshot {
  readonly renderer: BedRenderer | 'loading' | 'none';
  /** `null` when sky brightness is missing; -Infinity when silent for another reason. */
  readonly levelDb: number | null;
  readonly silentBecause: string | null;
  readonly fallbackReason: string | null;
}

export interface EngineSnapshot {
  readonly contextState: AudioContextState | 'none';
  readonly enabled: boolean;
  readonly regime: ScaleAudioRegime;
  readonly reverb: { readonly status: ReverbStatus; readonly error: string | null };
  readonly bed: BedSnapshot;
  /** Voices sounding now (or that would sound once started). */
  readonly voices: readonly VoiceSnapshot[];
  /** Objects that are audible in principle but did not fit in the pool. */
  readonly notSelected: readonly string[];
  /** Objects that are silent, with the inputs that were missing. */
  readonly silent: readonly SilentOutcome[];
}

const LOCK_TONE_BASE_HZ = 220;
const LOCK_TONE_HARMONICS = 32;
const DETENT_GAIN = 0.11;
const SUSPEND_AFTER_FADE_MS = 1200;

/**
 * Data-driven spatial audio engine. Holds the latest data even before it is started, so the
 * first gesture plays the current state at once. `start()` must be called from a user
 * gesture handler; nothing else creates an AudioContext.
 */
export class SpatialAudioEngine {
  private readonly contextFactory: AudioContextFactory;
  private readonly impulseResponseUrl: string | null;
  private readonly capacity: number;
  private readonly fetchImpl: typeof fetch | undefined;
  private readonly loadOmnitone: OmnitoneLoader;
  private bedPreference: BedPreference;

  private ctx: AudioContext | null = null;
  private master: MasterBus | null = null;
  private reverb: ConvolutionReverb | null = null;
  private pool: VoicePool | null = null;
  private uiBus: GainNode | null = null;
  private bed: SkyglowBed | null = null;
  private bedLoading = false;
  private bedToken = 0;
  private bedFallbackReason: string | null = null;
  private suspendTimer: ReturnType<typeof setTimeout> | null = null;

  private enabledValue = false;
  private regime: ScaleAudioRegime = 'earth_capture';
  private sources: readonly AudioSourceInput[] = [];
  private skyBrightnessMagArcsec2: number | null = null;
  private unveil = 0;
  /** The user's volume, kept until the master bus exists; null leaves the bus default. */
  private masterVolume: number | null = null;
  private orientation: Quat = IDENTITY_QUAT;
  private outcomes: readonly PlanOutcome[] = [];
  private selected: readonly VoicePlan[] = [];
  private pitches: Map<string, number> = new Map();

  constructor(options: SpatialAudioEngineOptions = {}) {
    this.contextFactory = options.contextFactory ?? createBrowserAudioContext;
    this.impulseResponseUrl =
      options.impulseResponseUrl === undefined ? DEFAULT_IMPULSE_RESPONSE_URL : options.impulseResponseUrl;
    this.capacity = Math.max(1, Math.floor(options.capacity ?? DEFAULT_VOICE_CAPACITY));
    this.fetchImpl = options.fetchImpl;
    this.loadOmnitone = options.loadOmnitone ?? loadOmnitoneModule;
    this.bedPreference = options.bedPreference ?? 'auto';
  }

  get enabled(): boolean {
    return this.enabledValue;
  }

  get started(): boolean {
    return this.ctx !== null;
  }

  /** Creates (first call) or resumes the AudioContext. Call only from a user gesture. */
  start(): boolean {
    if (!this.ctx) {
      const ctx = this.contextFactory();
      if (!ctx) return false;
      this.ctx = ctx;
      this.build(ctx);
    }
    this.clearSuspendTimer();
    if (this.ctx.state === 'suspended') void this.ctx.resume().catch(() => undefined);
    this.enabledValue = true;
    this.master?.fadeIn();
    this.refresh();
    return true;
  }

  /** Fades out, then suspends the context so a disabled engine costs no CPU. */
  stop(): void {
    this.enabledValue = false;
    if (!this.ctx || !this.master) return;
    this.master.fadeOut();
    this.clearSuspendTimer();
    const ctx = this.ctx;
    this.suspendTimer = setTimeout(() => {
      if (!this.enabledValue && ctx.state === 'running') void ctx.suspend().catch(() => undefined);
    }, SUSPEND_AFTER_FADE_MS);
  }

  setRegime(regime: ScaleAudioRegime): void {
    if (!Object.hasOwn(REGIME_DISTANCE_WINDOWS, regime)) return;
    this.regime = regime;
    this.refresh();
  }

  getRegime(): ScaleAudioRegime {
    return this.regime;
  }

  setSources(sources: readonly AudioSourceInput[]): void {
    this.sources = [...sources];
    this.refresh();
  }

  /** Sky brightness of the observed sky, V mag/arcsec^2; `null` silences the bed. */
  setSkyBrightness(magArcsec2: number | null): void {
    this.skyBrightnessMagArcsec2 = isFiniteNumber(magArcsec2) ? magArcsec2 : null;
    this.applyBed();
  }

  /** The user's reveal control, 0 (veiled) to 1 (fully unveiled). */
  setUnveil(fraction: number): void {
    this.unveil = isFiniteNumber(fraction) ? clamp(fraction, 0, 1) : 0;
    this.applyBed();
  }

  setListenerOrientation(orientation: Quat): void {
    this.orientation = normalizeQuat(orientation) ?? IDENTITY_QUAT;
    this.refresh();
  }

  setMasterVolume(linear: number): void {
    if (!isFiniteNumber(linear)) return;
    this.masterVolume = linear;
    this.master?.setVolume(linear);
  }

  setReverbReturnDb(levelDb: number): void {
    this.reverb?.setReturnLevelDb(levelDb);
  }

  setBedPreference(preference: BedPreference): void {
    this.bedPreference = preference;
    if (this.ctx) void this.loadBed();
  }

  /** Short UI detent (not data). Plays only while enabled. */
  playDetentClick(pitchHz = 920): void {
    const ctx = this.ctx;
    const bus = this.uiBus;
    if (!ctx || !bus || !this.enabledValue || !isFiniteNumber(pitchHz) || pitchHz <= 0) return;
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(pitchHz, now);
    osc.frequency.exponentialRampToValueAtTime(pitchHz * 0.35, now + 0.024);
    gain.gain.setValueAtTime(DETENT_GAIN, now);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.026);
    osc.connect(gain);
    gain.connect(bus);
    osc.start(now);
    osc.stop(now + 0.028);
  }

  /**
   * One-shot lock tone: a clean just fifth whose timbre follows Teff, panned by `panX`.
   * Silent when either input is missing.
   */
  playLockTone(teffK: number | null, panX: number | null): void {
    const ctx = this.ctx;
    const bus = this.uiBus;
    if (!ctx || !bus || !this.enabledValue || !isFiniteNumber(panX)) return;
    const tones = [LOCK_TONE_BASE_HZ, LOCK_TONE_BASE_HZ * 1.5].map((hz) => ({
      hz,
      amplitudes: planckHarmonicAmplitudes(teffK, hz, LOCK_TONE_HARMONICS),
    }));
    if (tones.some((tone) => tone.amplitudes === null)) return;

    const now = ctx.currentTime;
    const panner = ctx.createStereoPanner();
    panner.pan.setValueAtTime(clamp(panX, -0.9, 0.9), now);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, now);
    env.gain.linearRampToValueAtTime(0.09, now + 0.03);
    env.gain.exponentialRampToValueAtTime(0.0001, now + 0.65);
    env.connect(panner);
    panner.connect(bus);
    for (const tone of tones) {
      const amplitudes = tone.amplitudes!;
      const { real, imag } = periodicWaveCoefficients(amplitudes, schroederPhases(amplitudes.map((a) => a * a)));
      const osc = ctx.createOscillator();
      osc.setPeriodicWave(ctx.createPeriodicWave(real, imag, { disableNormalization: true }));
      osc.frequency.setValueAtTime(tone.hz, now);
      osc.connect(env);
      osc.start(now);
      osc.stop(now + 0.68);
    }
  }

  /** RMS level at the master output, dBFS. */
  getOutputLevelDb(): number {
    return this.master?.getOutputLevelDb() ?? -Infinity;
  }

  getLimiterReductionDb(): number {
    return this.master?.getLimiterReductionDb() ?? 0;
  }

  getSnapshot(): EngineSnapshot {
    const states = new Map((this.pool?.states() ?? []).map((state) => [state.id, state]));
    const voices: VoiceSnapshot[] = this.selected.map((plan) => {
      const state = states.get(plan.id);
      return {
        ...plan,
        slot: state?.slot ?? null,
        pitchHz: state?.pitchHz ?? this.pitches.get(plan.id) ?? null,
        centroidHz: state?.centroidHz ?? null,
      };
    });
    const selectedIds = new Set(this.selected.map((plan) => plan.id));
    const notSelected = this.outcomes.flatMap((outcome) =>
      outcome.status === 'audible' && !selectedIds.has(outcome.plan.id) ? [outcome.plan.id] : []
    );
    const silent = this.outcomes.filter((outcome): outcome is SilentOutcome => outcome.status === 'silent');
    const bed = this.bedLevel();
    return {
      contextState: this.ctx?.state ?? 'none',
      enabled: this.enabledValue,
      regime: this.regime,
      reverb: { status: this.reverb?.status ?? 'idle', error: this.reverb?.error ?? null },
      bed: {
        renderer: this.bed?.renderer ?? (this.bedLoading ? 'loading' : 'none'),
        levelDb: bed.levelDb,
        silentBecause: bed.silentBecause,
        fallbackReason: this.bedFallbackReason,
      },
      voices,
      notSelected,
      silent,
    };
  }

  dispose(): void {
    this.clearSuspendTimer();
    this.pool?.dispose();
    this.bed?.dispose();
    this.master?.dispose();
    void this.ctx?.close().catch(() => undefined);
    this.ctx = null;
    this.master = null;
    this.reverb = null;
    this.pool = null;
    this.uiBus = null;
    this.bed = null;
    this.enabledValue = false;
  }

  private build(ctx: AudioContext): void {
    const master = new MasterBus(ctx);
    const voicesBus = ctx.createGain();
    voicesBus.connect(master.input);
    const reverb = new ConvolutionReverb(ctx, master.input);
    const uiBus = ctx.createGain();
    uiBus.connect(master.input);
    this.master = master;
    if (this.masterVolume !== null) master.setVolume(this.masterVolume);
    this.reverb = reverb;
    this.uiBus = uiBus;
    this.pool = new VoicePool(ctx, this.capacity, voicesBus, reverb.input);
    if (this.impulseResponseUrl) void reverb.load(this.impulseResponseUrl, this.fetchImpl ?? fetch);
    void this.loadBed();
  }

  private async loadBed(): Promise<void> {
    const ctx = this.ctx;
    const master = this.master;
    if (!ctx || !master) return;
    const token = ++this.bedToken;
    this.bed?.dispose();
    this.bed = null;
    this.bedLoading = true;
    try {
      const { bed, fallbackReason } = await createSkyglowBed(ctx, master.input, this.bedPreference, this.loadOmnitone);
      if (token !== this.bedToken || this.ctx !== ctx) {
        bed.dispose();
        return;
      }
      this.bed = bed;
      this.bedFallbackReason = fallbackReason;
    } catch (err) {
      if (token === this.bedToken) this.bedFallbackReason = err instanceof Error ? err.message : String(err);
    } finally {
      if (token === this.bedToken) this.bedLoading = false;
    }
    this.applyBed();
  }

  private bedLevel(): { levelDb: number | null; silentBecause: string | null } {
    if (this.regime !== 'earth_capture') {
      return { levelDb: -Infinity, silentBecause: 'skyglow is heard only in the earth_capture regime' };
    }
    const levelDb = skyglowBedLevelDb(this.skyBrightnessMagArcsec2, this.unveil);
    if (levelDb === null) return { levelDb: null, silentBecause: 'skyBrightnessMagArcsec2 missing' };
    if (levelDb === -Infinity) {
      return {
        levelDb,
        silentBecause: this.unveil >= 1 ? 'sky fully unveiled' : 'no audible artificial skyglow at this brightness',
      };
    }
    return { levelDb, silentBecause: null };
  }

  private applyBed(): void {
    const { levelDb } = this.bedLevel();
    this.bed?.setLevelDb(levelDb ?? -Infinity);
  }

  private refresh(): void {
    const outcomes = this.sources.map((source) =>
      planVoice(source, {
        distanceWindow: distanceWindowFor(source.kind, this.regime),
        listenerOrientation: this.orientation,
      })
    );
    const audible = outcomes.flatMap((outcome) => (outcome.status === 'audible' ? [outcome.plan] : []));
    const incumbents = new Set(this.selected.map((plan) => plan.id));
    const selected = selectVoices(audible, incumbents, this.capacity);
    this.pitches = assignIdentityPitches(
      selected.map((plan) => plan.id),
      this.pitches
    );
    this.outcomes = outcomes;
    this.selected = selected;
    this.pool?.apply(selected, this.pitches);
    this.applyBed();
  }

  private clearSuspendTimer(): void {
    if (this.suspendTimer !== null) clearTimeout(this.suspendTimer);
    this.suspendTimer = null;
  }
}
