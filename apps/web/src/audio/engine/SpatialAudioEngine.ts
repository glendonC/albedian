import { isFiniteNumber, type ScaleAudioRegime } from '../mappings.js';
import { IDENTITY_QUAT, type AudioSourceInput, type Quat } from '../voicePlanning.js';
import type { MasterBus } from './MasterBus.js';
import type { BedPreference } from './SkyglowBed.js';
import { AudioLifecycle, browserLifecycleHost } from './audioLifecycle.js';
import {
  SkyglowBedController,
  createEngineAudioGraph,
  type EngineAudioGraph,
} from './engineGraph.js';
import {
  SUSPEND_AFTER_FADE_MS,
  resolveEngineConfig,
  type EngineSnapshot,
  type ResolvedEngineConfig,
  type SpatialAudioEngineOptions,
} from './engineTypes.js';
import {
  EMPTY_PLANNED_FRAME,
  evaluateBedLevel,
  isKnownRegime,
  planAudioFrame,
  sanitizeOrientation,
  sanitizeSkyBrightness,
  sanitizeUnveil,
  type PlannedAudioFrame,
} from './framePlanner.js';
import { buildEngineSnapshot } from './snapshotBuilder.js';
import { DEFAULT_DETENT_PITCH_HZ, playDetentCue, playLockToneCue } from './uiCues.js';

export type {
  BedSnapshot,
  EngineSnapshot,
  SilentOutcome,
  SpatialAudioEngineOptions,
  VoiceSnapshot,
} from './engineTypes.js';

/**
 * Data-driven spatial audio engine. Holds the latest data even before it is started, so the
 * first gesture plays the current state at once. `start()` must be called from a user
 * gesture handler; nothing else creates an AudioContext.
 */
export class SpatialAudioEngine {
  private readonly config: ResolvedEngineConfig;
  private readonly bedController: SkyglowBedController;

  private ctx: AudioContext | null = null;
  private graph: EngineAudioGraph | null = null;
  private master: MasterBus | null = null;
  private lifecycle: AudioLifecycle | null = null;
  private suspendTimer: ReturnType<typeof setTimeout> | null = null;

  private enabledValue = false;
  private regime: ScaleAudioRegime = 'earth_capture';
  private sources: readonly AudioSourceInput[] = [];
  private skyBrightnessMagArcsec2: number | null = null;
  private unveil = 0;
  /** The user's volume, kept until the master bus exists; null leaves the bus default. */
  private masterVolume: number | null = null;
  private orientation: Quat = IDENTITY_QUAT;
  private frame: PlannedAudioFrame = EMPTY_PLANNED_FRAME;

  constructor(options: SpatialAudioEngineOptions = {}) {
    this.config = resolveEngineConfig(options);
    this.bedController = new SkyglowBedController(
      this.config.initialBedPreference,
      this.config.loadOmnitone
    );
  }

  get enabled(): boolean {
    return this.enabledValue;
  }

  get started(): boolean {
    return this.ctx !== null;
  }

  /** The AudioContext, once `start()` has made it in a user gesture; null before. */
  get context(): AudioContext | null {
    return this.ctx;
  }

  /** The music's input on the master bus (volume, fades, limiter), once the context exists. */
  get musicInput(): GainNode | null {
    return this.graph?.musicBus ?? null;
  }

  /** Creates (first call) or resumes the AudioContext. Call only from a user gesture. */
  start(): boolean {
    if (!this.ctx) {
      const ctx = this.config.contextFactory();
      if (!ctx) return false;
      this.ctx = ctx;
      this.build(ctx);
      // Hidden, shown, locked or interrupted: the lifecycle keeps the context in step with the page
      const host =
        this.config.lifecycleHost === undefined ? browserLifecycleHost() : this.config.lifecycleHost;
      if (host) {
        this.lifecycle = new AudioLifecycle(ctx, host, () => this.enabledValue);
        this.lifecycle.attach();
      }
    }
    this.clearSuspendTimer();
    // Suspended, or iOS's 'interrupted': this call comes from the user's gesture, so it may resume
    if ((this.ctx.state as string) !== 'running') void this.ctx.resume().catch(() => undefined);
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
    if (!isKnownRegime(regime)) return;
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
    this.skyBrightnessMagArcsec2 = sanitizeSkyBrightness(magArcsec2);
    this.applyBed();
  }

  /** The user's reveal control, 0 (veiled) to 1 (fully unveiled). */
  setUnveil(fraction: number): void {
    this.unveil = sanitizeUnveil(fraction);
    this.applyBed();
  }

  setListenerOrientation(orientation: Quat): void {
    this.orientation = sanitizeOrientation(orientation);
    this.refresh();
  }

  setMasterVolume(linear: number): void {
    if (!isFiniteNumber(linear)) return;
    this.masterVolume = linear;
    this.master?.setVolume(linear);
  }

  setReverbReturnDb(levelDb: number): void {
    this.graph?.reverb.setReturnLevelDb(levelDb);
  }

  setBedPreference(preference: BedPreference): void {
    this.bedController.setPreference(preference, () => this.currentBedLevelDb());
  }

  /** Short UI detent (not data). Plays only while enabled. */
  playDetentClick(pitchHz = DEFAULT_DETENT_PITCH_HZ): void {
    const ctx = this.ctx;
    const bus = this.graph?.uiBus ?? null;
    if (!ctx || !bus || !this.enabledValue) return;
    playDetentCue(ctx, bus, pitchHz);
  }

  /**
   * One-shot lock tone: a clean just fifth whose timbre follows Teff, panned by `panX`.
   * Silent when either input is missing.
   */
  playLockTone(teffK: number | null, panX: number | null): void {
    const ctx = this.ctx;
    const bus = this.graph?.uiBus ?? null;
    if (!ctx || !bus || !this.enabledValue) return;
    playLockToneCue(ctx, bus, teffK, panX);
  }

  /** RMS level at the master output, dBFS. */
  getOutputLevelDb(): number {
    return this.master?.getOutputLevelDb() ?? -Infinity;
  }

  getLimiterReductionDb(): number {
    return this.master?.getLimiterReductionDb() ?? 0;
  }

  getSnapshot(): EngineSnapshot {
    return buildEngineSnapshot({
      contextState: this.ctx?.state ?? 'none',
      enabled: this.enabledValue,
      regime: this.regime,
      reverbStatus: this.graph?.reverb.status ?? 'idle',
      reverbError: this.graph?.reverb.error ?? null,
      bedRenderer: this.bedController.renderer,
      bedLevel: evaluateBedLevel(this.regime, this.skyBrightnessMagArcsec2, this.unveil),
      bedFallbackReason: this.bedController.fallbackReason,
      frame: this.frame,
      poolStates: this.graph?.pool.states() ?? [],
    });
  }

  dispose(): void {
    this.clearSuspendTimer();
    this.lifecycle?.detach();
    this.lifecycle = null;
    this.graph?.pool.dispose();
    this.bedController.dispose();
    this.master?.dispose();
    void this.ctx?.close().catch(() => undefined);
    this.ctx = null;
    this.graph = null;
    this.master = null;
    this.enabledValue = false;
  }

  private build(ctx: AudioContext): void {
    const graph = createEngineAudioGraph(ctx, {
      capacity: this.config.capacity,
      harmonicCeilingHz: this.config.harmonicCeilingHz,
      impulseResponseUrl: this.config.impulseResponseUrl,
      fetchImpl: this.config.fetchImpl,
      initialMasterVolume: this.masterVolume,
    });
    this.graph = graph;
    this.master = graph.master;
    this.bedController.attach(ctx, graph.master.input, () => this.currentBedLevelDb());
  }

  private currentBedLevelDb(): number | null {
    return evaluateBedLevel(this.regime, this.skyBrightnessMagArcsec2, this.unveil).levelDb;
  }

  private applyBed(): void {
    this.bedController.applyLevelDb(this.currentBedLevelDb());
  }

  private refresh(): void {
    this.frame = planAudioFrame({
      sources: this.sources,
      regime: this.regime,
      orientation: this.orientation,
      capacity: this.config.capacity,
      previousSelected: this.frame.selected,
      previousPitches: this.frame.pitches,
    });
    this.graph?.pool.apply(this.frame.selected, this.frame.pitches);
    this.applyBed();
  }

  private clearSuspendTimer(): void {
    if (this.suspendTimer !== null) clearTimeout(this.suspendTimer);
    this.suspendTimer = null;
  }
}
