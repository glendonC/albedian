import { ConvolutionReverb } from './ConvolutionReverb.js';
import { MasterBus } from './MasterBus.js';
import {
  createSkyglowBed,
  type BedPreference,
  type BedRenderer,
  type OmnitoneLoader,
  type SkyglowBed,
} from './SkyglowBed.js';
import { VoicePool } from './VoicePool.js';

export interface EngineGraphBuildOptions {
  readonly capacity: number;
  readonly harmonicCeilingHz: number | undefined;
  readonly impulseResponseUrl: string | null;
  readonly fetchImpl: typeof fetch | undefined;
  readonly initialMasterVolume: number | null;
}

export interface EngineAudioGraph {
  readonly master: MasterBus;
  readonly voicesBus: GainNode;
  readonly reverb: ConvolutionReverb;
  readonly uiBus: GainNode;
  readonly musicBus: GainNode;
  readonly pool: VoicePool;
}

/** Builds the Web Audio sub-buses, convolution reverb, master bus, and voice pool on `ctx`. */
export function createEngineAudioGraph(
  ctx: AudioContext,
  options: EngineGraphBuildOptions
): EngineAudioGraph {
  const master = new MasterBus(ctx);
  const voicesBus = ctx.createGain();
  voicesBus.connect(master.input);
  const reverb = new ConvolutionReverb(ctx, master.input);
  const uiBus = ctx.createGain();
  uiBus.connect(master.input);
  const musicBus = ctx.createGain();
  musicBus.connect(master.input);
  if (options.initialMasterVolume !== null) master.setVolume(options.initialMasterVolume);
  const pool = new VoicePool(ctx, options.capacity, voicesBus, reverb.input, options.harmonicCeilingHz);
  if (options.impulseResponseUrl) {
    void reverb.load(options.impulseResponseUrl, options.fetchImpl ?? fetch);
  }
  return {
    master,
    voicesBus,
    reverb,
    uiBus,
    musicBus,
    pool,
  };
}

/**
 * Coordinates asynchronous SkyglowBed creation, renderer switching, token-based cancellation of
 * superseded loads, and live dBFS level updates.
 */
export class SkyglowBedController {
  private preference: BedPreference;
  private activeCtx: AudioContext | null = null;
  private activeDestination: AudioNode | null = null;
  private bed: SkyglowBed | null = null;
  private loading = false;
  private token = 0;
  private fallbackReasonValue: string | null = null;

  constructor(
    initialPreference: BedPreference,
    private readonly loadOmnitone: OmnitoneLoader
  ) {
    this.preference = initialPreference;
  }

  get renderer(): BedRenderer | 'loading' | 'none' {
    return this.bed?.renderer ?? (this.loading ? 'loading' : 'none');
  }

  get fallbackReason(): string | null {
    return this.fallbackReasonValue;
  }

  attach(ctx: AudioContext, destination: AudioNode, getCurrentLevelDb: () => number | null): void {
    this.activeCtx = ctx;
    this.activeDestination = destination;
    void this.reload(getCurrentLevelDb);
  }

  setPreference(preference: BedPreference, getCurrentLevelDb: () => number | null): void {
    this.preference = preference;
    if (this.activeCtx && this.activeDestination) {
      void this.reload(getCurrentLevelDb);
    }
  }

  applyLevelDb(levelDb: number | null): void {
    this.bed?.setLevelDb(levelDb ?? -Infinity);
  }

  dispose(): void {
    this.bed?.dispose();
    this.bed = null;
    this.activeCtx = null;
    this.activeDestination = null;
  }

  private async reload(getCurrentLevelDb: () => number | null): Promise<void> {
    const ctx = this.activeCtx;
    const destination = this.activeDestination;
    if (!ctx || !destination) return;
    const token = ++this.token;
    this.bed?.dispose();
    this.bed = null;
    this.loading = true;
    try {
      const { bed, fallbackReason } = await createSkyglowBed(
        ctx,
        destination,
        this.preference,
        this.loadOmnitone
      );
      if (token !== this.token || this.activeCtx !== ctx) {
        bed.dispose();
        return;
      }
      this.bed = bed;
      this.fallbackReasonValue = fallbackReason;
    } catch (err) {
      if (token === this.token) {
        this.fallbackReasonValue = err instanceof Error ? err.message : String(err);
      }
    } finally {
      if (token === this.token) this.loading = false;
    }
    this.applyLevelDb(getCurrentLevelDb());
  }
}
