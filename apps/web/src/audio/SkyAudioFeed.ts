/**
 * Feeds the spatial audio engine from the catalog sky the renderer draws (AUDIO_ENGINE.md
 * section 8). Each frame the camera's pose turns the listener; the catalog stars on screen become
 * the engine's sources, with their Gaia DR3 temperature and luminosity from the astro tiles.
 *
 * The feed keeps its own tile loader on the release the star fields draw, so it needs nothing from
 * a renderer but the camera's pose, and the core tiles it reads are files the star field already
 * fetched. It fetches nothing before the first pose, and the viewport sends poses only while audio
 * is on. A renderer that reports no pose leaves the sky silent.
 */

import { observatoryAudio } from './ObservatoryAudioEngine.js';
import { AstroTileStore } from './skyFeed/astroTileStore.js';
import {
  EMPTY_FEED_COUNTS,
  normalizeEvidenceClass,
  type SkyAudioFeedCounts,
  type SkyAudioFeedOptions,
  type SkyAudioFeedStatus,
  type SkyAudioTarget,
  type SkyCatalogStatus,
  type SkyMusicTarget,
} from './skyFeed/feedTypes.js';
import {
  nextPlannedTileCone,
  sameOrientation,
  shouldComputeSources,
  type ComputedSourceFrame,
  type PlannedTileCone,
} from './skyFeed/poseCadence.js';
import {
  EMPTY_PASSED_SOURCES_KEY,
  nextVoiceEmission,
  projectMusicScreenStars,
  projectViewSources,
} from './skyFeed/sourceProjection.js';
import { skyViewFromPose, type SkyCameraPose, type SkyView } from './skySources.js';
import type { Quat } from './voicePlanning.js';

export type {
  SkyAudioFeedOptions,
  SkyAudioFeedStatus,
  SkyAudioTarget,
  SkyCatalogStatus,
  SkyMusicTarget,
};

export class SkyAudioFeed {
  private readonly now: () => number;
  private readonly store: AstroTileStore;
  private orientation: Quat | null = null;
  private planned: PlannedTileCone | null = null;
  private computed: ComputedSourceFrame | null = null;
  private dirty = true;
  private evidenceClass: string | null = null;
  private passedIds: ReadonlySet<string> = new Set();
  private passedKey = EMPTY_PASSED_SOURCES_KEY;
  private counts: SkyAudioFeedCounts = EMPTY_FEED_COUNTS;
  private disposed = false;

  constructor(
    private readonly audio: SkyAudioTarget,
    private readonly options: SkyAudioFeedOptions = {}
  ) {
    this.now = options.now ?? (() => performance.now());
    this.store = new AstroTileStore({
      ...(options.baseUrl !== undefined ? { baseUrl: options.baseUrl } : {}),
      ...(options.fetch !== undefined ? { fetch: options.fetch } : {}),
      ...(options.maxResidentStars !== undefined ? { maxResidentStars: options.maxResidentStars } : {}),
      now: this.now,
      onDirty: () => {
        this.dirty = true;
      },
    });
  }

  /** Whether the feed passes the stars in view to the engine (see `SkyAudioFeedOptions.voices`). */
  get voicesEnabled(): boolean {
    return this.options.voices !== false;
  }

  /** Whether the feed hands the stars on screen to a music target: only while that music can play. */
  get musicEnabled(): boolean {
    return this.options.music?.wantsMusicStars() ?? false;
  }

  /**
   * Call once per animation frame while audio is on. `null` (no renderer, or no pose yet) changes
   * nothing: the engine keeps what it last had.
   */
  follow(pose: SkyCameraPose | null): void {
    if (this.disposed || pose === null) return;
    const view = skyViewFromPose(pose);
    if (!view) return;
    if (this.orientation === null || !sameOrientation(this.orientation, view.orientationIcrs)) {
      this.orientation = view.orientationIcrs;
      this.audio.setListenerOrientation(view.orientationIcrs);
    }
    if (!this.voicesEnabled && !this.musicEnabled) return;
    this.store.ensureOpen();
    if (!this.store.isReady) return;
    const now = this.now();
    const cone = nextPlannedTileCone(this.planned, view, now);
    if (cone !== null) {
      this.store.setViewCone(cone.center, cone.radius, cone.limit);
      this.planned = cone;
    }
    if (this.store.isRetryDue(now)) this.dirty = true;
    if (!shouldComputeSources(this.computed, view, this.dirty, now)) return;
    this.computeSources(view, now);
  }

  /** The evidence class of the star layer being drawn; null where the renderer declares none. */
  setEvidenceClass(evidenceClass: string | null): void {
    const next = normalizeEvidenceClass(evidenceClass);
    if (next === this.evidenceClass) return;
    this.evidenceClass = next;
    this.dirty = true;
  }

  getStatus(): SkyAudioFeedStatus {
    return {
      catalog: this.store.catalog,
      error: this.store.error,
      ...this.counts,
      astroFailures: this.store.astroFailures,
      settled: this.store.isSettled(this.dirty),
    };
  }

  dispose(): void {
    this.disposed = true;
    this.store.dispose();
  }

  private computeSources(view: SkyView, now: number): void {
    const releaseId = this.store.releaseId;
    if (releaseId === null) return;
    this.dirty = false;
    this.computed = { view, at: now };
    const projected = projectViewSources({
      tiles: this.store.getLoadedTilesWithAstro(),
      view,
      releaseId,
      ...(this.options.maxAudible !== undefined ? { maxAudible: this.options.maxAudible } : {}),
      ...(this.options.maxSilent !== undefined ? { maxSilent: this.options.maxSilent } : {}),
      evidenceClass: this.evidenceClass,
      incumbents: this.passedIds,
    });
    this.store.requestMissingAstro(projected.tilesWithoutAstro, now);
    this.counts = projected.counts;

    // Screen positions move with the camera, so the music gets every update
    if (this.musicEnabled) {
      this.options.music?.setMusicStars(projectMusicScreenStars(projected.sources, view));
    }
    if (!this.voicesEnabled) return;

    const emission = nextVoiceEmission(this.passedKey, this.evidenceClass, projected.sources);
    if (emission === null) return;
    this.passedKey = emission.key;
    this.passedIds = emission.ids;
    this.audio.setSources(projected.sources);
  }
}

/**
 * The feed the viewport drives, sounding through the app's audio engine. Its continuous star
 * voices are off (`voices: false`): under the music-first architecture (AUDIO_ENGINE.md sections 8
 * and 13), the stars in view play inside the musical score rather than as continuous sustained
 * drones. The feed hands the soundtrack the stars on screen while music can play: it asks the engine
 * each frame, since the viewer can change the Music setting while the page is open, and where no
 * samples are served (or the music is off) it loads no tile for the music.
 */
export const skyAudio = new SkyAudioFeed(observatoryAudio, { voices: false, music: observatoryAudio });
