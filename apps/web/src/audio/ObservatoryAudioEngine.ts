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

export type { ScaleAudioRegime } from './mappings.js';
export type { AudioSourceInput, Quat, Vec3 } from './voicePlanning.js';
export type { EngineSnapshot } from './engine/SpatialAudioEngine.js';

export class ObservatoryAudioEngine {
  readonly spatial: SpatialAudioEngine;

  constructor(options: SpatialAudioEngineOptions = {}) {
    this.spatial = new SpatialAudioEngine(options);
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
  }

  public disable(): void {
    this.spatial.stop();
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

export const observatoryAudio = new ObservatoryAudioEngine();
