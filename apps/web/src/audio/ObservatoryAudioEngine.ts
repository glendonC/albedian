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
import { appHeardMusic, appMusicAvailability, type MusicAvailabilityState } from './audioCredits.js';
import { MusicLayer, type MusicStatus } from './music/MusicLayer.js';
import { DEV_BUILD, appMusicSetting, type MusicSetting } from './music/musicSetting.js';
import type { PieceId, SoundtrackMoment } from './music/soundtrack.js';
import { appSoundtrackMoment } from './music/soundtrackMoment.js';
import { appMusicSelection, type MusicSelection } from './music/selectionTone.js';
import type { ScreenStar } from './music/starNotes.js';

export type { ScaleAudioRegime } from './mappings.js';
export type { AudioSourceInput, Quat, Vec3 } from './voicePlanning.js';
export type { EngineSnapshot } from './engine/SpatialAudioEngine.js';
export type { MusicStatus } from './music/MusicLayer.js';

export interface ObservatoryAudioOptions extends SpatialAudioEngineOptions {
  /** The Music setting to start with (musicSetting.ts); the soundtrack by default. */
  readonly musicSetting?: MusicSetting;
  /** The moment of the visit to start with (soundtrackMoment.ts); arriving by default. */
  readonly musicMoment?: SoundtrackMoment;
  /** URL prefix the music's samples are fetched from, ending in '/'; none by default. */
  readonly musicSourcesBase?: string | null;
  /** Told each time a soundtrack piece starts playing, for its credits. */
  readonly onMusicPieceStarted?: (piece: PieceId) => void;
  /** Told the music's status when the engine is made and each time it changes, so the page can say whether music can play. */
  readonly onMusicStatus?: (status: MusicStatus) => void;
}

export class ObservatoryAudioEngine {
  readonly spatial: SpatialAudioEngine;
  private readonly music: MusicLayer;

  constructor(options: ObservatoryAudioOptions = {}) {
    this.spatial = new SpatialAudioEngine(options);
    this.music = new MusicLayer({
      setting: options.musicSetting ?? 'soundtrack',
      moment: options.musicMoment ?? 'arrive',
      sourcesBase: options.musicSourcesBase ?? null,
      ...(options.fetchImpl ? { fetch: options.fetchImpl } : {}),
      ...(options.onMusicPieceStarted ? { onPieceStarted: options.onMusicPieceStarted } : {}),
      ...(options.onMusicStatus ? { onStatus: options.onMusicStatus } : {}),
    });
    // The first status, so the page knows from load whether music can play (no samples served)
    options.onMusicStatus?.(this.music.getStatus());
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

  /** The Music setting: the soundtrack, off, or a pinned piece. */
  public getMusicSetting(): MusicSetting {
    return this.music.setting;
  }

  public setMusicSetting(setting: MusicSetting): void {
    this.music.setSetting(setting);
  }

  /** The moment of the visit the soundtrack follows (soundtrackMoment.ts). */
  public setMusicMoment(moment: SoundtrackMoment): void {
    this.music.setMoment(moment);
  }

  public getMusicStatus(): MusicStatus {
    return this.music.getStatus();
  }

  /** A selected finding's quiet note inside the playing piece (selectionTone.ts); nothing while Audio is off. */
  public playMusicSelection(selection: MusicSelection): void {
    this.music.playSelection(selection);
  }

  /** Whether the music wants the stars on screen: not where no music can play, nor with the music off. */
  public wantsMusicStars(): boolean {
    return this.music.wantsStars();
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

/**
 * Where the dev server's music samples are, when VITE_ALBEDIAN_MUSIC_SOURCES does not say: the app's own
 * origin, which Vite proxies to spike/music/serveSources.mjs (vite.config.ts), so a phone on the field
 * run's network reaches them too.
 */
const DEV_MUSIC_SOURCES = '/music-sources/';

// Vite replaces import.meta.env at build time; the app's tsconfig carries no vite/client types
const env = (import.meta as ImportMeta & { readonly env?: { readonly VITE_ALBEDIAN_MUSIC_SOURCES?: string } }).env;

/** Whether the music can play, from its status: 'not_served' with no sources base, 'not_reachable' after a failed fetch. */
export function musicAvailabilityOf(status: MusicStatus): MusicAvailabilityState {
  return status.state === 'unavailable' ? (status.why ?? 'not_reachable') : 'available';
}

/** What the app's engine is made with: the page's Music setting and moment, where the samples are, and the credits' record of what played. */
export const APP_AUDIO_OPTIONS: ObservatoryAudioOptions = {
  musicSetting: appMusicSetting.get(),
  musicMoment: appSoundtrackMoment.get(),
  musicSourcesBase: env?.VITE_ALBEDIAN_MUSIC_SOURCES || (DEV_BUILD ? DEV_MUSIC_SOURCES : null),
  onMusicPieceStarted: (piece) => appHeardMusic.add(piece),
  onMusicStatus: (status) => appMusicAvailability.set(musicAvailabilityOf(status)),
};

export const observatoryAudio = new ObservatoryAudioEngine(APP_AUDIO_OPTIONS);


// The Music control changes the page's setting and the viewport its moment; the engine follows both
appMusicSetting.subscribe(() => observatoryAudio.setMusicSetting(appMusicSetting.get()));
appSoundtrackMoment.subscribe(() => observatoryAudio.setMusicMoment(appSoundtrackMoment.get()));
// A newly selected finding plays its note; clearing the selection plays nothing
appMusicSelection.subscribe(() => {
  const selection = appMusicSelection.get();
  if (selection) observatoryAudio.playMusicSelection(selection);
});
