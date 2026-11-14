import type { ScaleAudioRegime } from '../mappings.js';
import { DEFAULT_VOICE_CAPACITY, type PlanOutcome, type VoicePlan } from '../voicePlanning.js';
import { DEFAULT_IMPULSE_RESPONSE_URL, type ReverbStatus } from './ConvolutionReverb.js';
import {
  loadOmnitoneModule,
  type BedPreference,
  type BedRenderer,
  type OmnitoneLoader,
} from './SkyglowBed.js';
import type { LifecycleHost } from './audioLifecycle.js';
import { createBrowserAudioContext, type AudioContextFactory } from './createAudioContext.js';

export interface SpatialAudioEngineOptions {
  readonly contextFactory?: AudioContextFactory;
  /** `null` skips loading the impulse response (the wet path stays silent). */
  readonly impulseResponseUrl?: string | null;
  readonly bedPreference?: BedPreference;
  readonly capacity?: number;
  readonly fetchImpl?: typeof fetch;
  readonly loadOmnitone?: OmnitoneLoader;
  /**
   * Highest harmonic of a star tone, Hz (default 16 kHz). Only the listening session lowers it, to
   * hear what the high harmonics add; the app keeps the default.
   */
  readonly harmonicCeilingHz?: number;
  /**
   * The page the context lives on, for its lifecycle on phones (audioLifecycle.ts): hidden, shown,
   * interrupted. The browser's document by default; null attaches none.
   */
  readonly lifecycleHost?: LifecycleHost | null;
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

export const SUSPEND_AFTER_FADE_MS = 1200;

export interface ResolvedEngineConfig {
  readonly contextFactory: AudioContextFactory;
  readonly lifecycleHost: LifecycleHost | null | undefined;
  readonly impulseResponseUrl: string | null;
  readonly capacity: number;
  readonly fetchImpl: typeof fetch | undefined;
  readonly loadOmnitone: OmnitoneLoader;
  readonly harmonicCeilingHz: number | undefined;
  readonly initialBedPreference: BedPreference;
}

export function resolveEngineConfig(options: SpatialAudioEngineOptions): ResolvedEngineConfig {
  return {
    contextFactory: options.contextFactory ?? createBrowserAudioContext,
    lifecycleHost: options.lifecycleHost,
    impulseResponseUrl:
      options.impulseResponseUrl === undefined ? DEFAULT_IMPULSE_RESPONSE_URL : options.impulseResponseUrl,
    capacity: Math.max(1, Math.floor(options.capacity ?? DEFAULT_VOICE_CAPACITY)),
    fetchImpl: options.fetchImpl,
    loadOmnitone: options.loadOmnitone ?? loadOmnitoneModule,
    harmonicCeilingHz: options.harmonicCeilingHz,
    initialBedPreference: options.bedPreference ?? 'auto',
  };
}
