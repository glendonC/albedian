/**
 * Albedian observatory audio: the public entry point the viewport uses.
 *
 * Keeps the original API (`toggle`, `getEnabled`, `enable`, `disable`, `setScaleRegime`,
 * `playDetentClick`, `playTargetLockTone`) on top of the data-driven SpatialAudioEngine.
 * The procedural per-regime ambience is gone: every sustained sound now comes from data
 * passed in through `setSources`, `setSkyBrightness` and `setUnveil`, and a missing input
 * silences the sound it drives. With no data, enabling audio plays only UI detents.
 *
 * No AudioContext exists until `toggle()`/`enable()` runs inside a user gesture.
 * Architecture, mappings and sources: docs/architecture/AUDIO_ENGINE.md.
 */

import { ballesterosTeffFromBv, type ScaleAudioRegime } from './mappings.js';
import type { AudioSourceInput, Quat } from './voicePlanning.js';
import {
  SpatialAudioEngine,
  type EngineSnapshot,
  type SpatialAudioEngineOptions,
} from './engine/SpatialAudioEngine.js';
import { MusicLayer, type MusicStatus } from './music/MusicLayer.js';
import { APP_MUSIC_DIRECTION, DEV_BUILD, type MusicDirectionId } from './music/musicDirection.js';
import type { ScreenStar } from './music/starNotes.js';

export type { ScaleAudioRegime } from './mappings.js';
export type { AudioSourceInput, Quat, Vec3 } from './voicePlanning.js';
export type { EngineSnapshot } from './engine/SpatialAudioEngine.js';
export type { MusicStatus } from './music/MusicLayer.js';

export interface ObservatoryAudioOptions extends SpatialAudioEngineOptions {
  /** The music direction to play on Audio on (`resolveMusicDirection`); none by default. */
  readonly musicDirection?: MusicDirectionId | null;
  /** URL prefix the music's samples are fetched from, ending in '/'; none by default. */
  readonly musicSourcesBase?: string | null;
}

export class ObservatoryAudioEngine {
  readonly spatial: SpatialAudioEngine;
  private readonly music: MusicLayer;

  constructor(options: ObservatoryAudioOptions = {}) {
    this.spatial = new SpatialAudioEngine(options);
    this.music = new MusicLayer({
      direction: options.musicDirection ?? null,
      sourcesBase: options.musicSourcesBase ?? null,
      ...(options.fetchImpl ? { fetch: options.fetchImpl } : {}),
    });
  }

  /** Call from a click or key handler: the first call creates the AudioContext. */
  public toggle(): boolean {
    if (this.spatial.enabled) this.disable();
    else this.enable();
    return this.spatial.enabled;
  }

  public getEnabled(): boolean {
    return this.spatial.enabled;
  }

  public enable(): void {
    if (!this.spatial.start()) return;
    this.spatial.playDetentClick(1100);
    const ctx = this.spatial.context;
    const input = this.spatial.musicInput;
    if (ctx && input) void this.music.start(ctx, input);
  }

  public disable(): void {
    this.music.stop();
    this.spatial.stop();
  }

  /** The music direction that plays on Audio on, for its credits; null when none does. */
  public getMusicDirection(): MusicDirectionId | null {
    return this.music.direction;
  }

  public getMusicStatus(): MusicStatus {
    return this.music.getStatus();
  }

  /** The catalog stars on screen, for the music's data part (`SkyAudioFeed` sends them). */
  public setMusicStars(stars: readonly ScreenStar[]): void {
    this.music.setStars(stars);
  }

  /** Selects the distance scale for the DRR and rolloff cues, and gates the skyglow bed. */
  public setScaleRegime(regime: ScaleAudioRegime): void {
    this.spatial.setRegime(regime);
  }

  /** Short UI detent. Interface feedback only; it carries no data. */
  public playDetentClick(pitchHz = 920): void {
    this.spatial.playDetentClick(pitchHz);
  }

  /**
   * Lock tone for a selected target: a clean just fifth whose timbre follows the target's
   * temperature (B-V through Ballesteros 2012), stereo-panned by `panX` in [-1, 1].
   * Silent when either value is missing; there are no default colours or positions.
   */
  public playTargetLockTone(bvIndex?: number | null, panX?: number | null): void {
    this.spatial.playLockTone(ballesterosTeffFromBv(bvIndex), panX ?? null);
  }

  // Data entry points for the viewport and renderer (see AUDIO_ENGINE.md, Integration).

  public setSources(sources: readonly AudioSourceInput[]): void {
    this.spatial.setSources(sources);
  }

  public setSkyBrightness(magArcsec2: number | null): void {
    this.spatial.setSkyBrightness(magArcsec2);
  }

  public setUnveil(fraction: number): void {
    this.spatial.setUnveil(fraction);
  }

  public setListenerOrientation(orientation: Quat): void {
    this.spatial.setListenerOrientation(orientation);
  }

  public setMasterVolume(linear: number): void {
    this.spatial.setMasterVolume(linear);
  }

  public getSnapshot(): EngineSnapshot {
    return this.spatial.getSnapshot();
  }
}

/** Where the dev server's music samples are, when VITE_ALBEDIAN_MUSIC_SOURCES does not say (spike/music/serveSources.mjs). */
const DEV_MUSIC_SOURCES = 'http://127.0.0.1:5199/';

// Vite replaces import.meta.env at build time; the app's tsconfig carries no vite/client types
const env = (import.meta as ImportMeta & { readonly env?: { readonly VITE_ALBEDIAN_MUSIC_SOURCES?: string } }).env;

export const observatoryAudio = new ObservatoryAudioEngine({
  musicDirection: APP_MUSIC_DIRECTION,
  musicSourcesBase: env?.VITE_ALBEDIAN_MUSIC_SOURCES || (DEV_BUILD ? DEV_MUSIC_SOURCES : null),
});
