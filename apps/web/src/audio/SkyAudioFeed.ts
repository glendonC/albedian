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

import { STAR_CATALOG_BASE_URL } from '../spatial/catalogStars/CatalogStarField.js';
import { CatalogStarTileLoader } from '../spatial/catalogStars/CatalogStarTileLoader.js';
import type { CatalogStarAstroTile } from '../spatial/catalogStars/tileCodec.js';
import { observatoryAudio } from './ObservatoryAudioEngine.js';
import {
  angleBetweenUnit,
  skySourcesInView,
  skyViewFromPose,
  type SkyCameraPose,
  type SkyTileData,
  type SkyView,
} from './skySources.js';
import type { AudioSourceInput, Quat, Vec3 } from './voicePlanning.js';

/** The engine calls the feed makes; `observatoryAudio` provides them. */
export interface SkyAudioTarget {
  setSources(sources: readonly AudioSourceInput[]): void;
  setListenerOrientation(orientation: Quat): void;
}

export type SkyCatalogStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

export interface SkyAudioFeedStatus {
  readonly catalog: SkyCatalogStatus;
  /** Why the catalog or an astro tile could not be read, if one could not. */
  readonly error: string | null;
  /** Drawn stars on screen at the last update, in tiles whose astro columns had arrived. */
  readonly starsInView: number;
  /** Of those, stars with a distance, a temperature and a luminosity. */
  readonly completeInView: number;
  /** Sources passed to the engine: stars with all three values, and stars missing one. */
  readonly audibleSources: number;
  readonly silentSources: number;
  /** Tiles in view whose astro columns are loading or failed; their stars are left out. */
  readonly tilesWithoutAstro: number;
  readonly astroFailures: number;
  /** True once the tiles the view wants and their astro columns have arrived or failed. */
  readonly settled: boolean;
}

export interface SkyAudioFeedOptions {
  readonly baseUrl?: string;
  readonly fetch?: typeof fetch;
  readonly now?: () => number;
  readonly maxAudible?: number;
  readonly maxSilent?: number;
  /** Resident star bound of the feed's own loader. */
  readonly maxResidentStars?: number;
  /**
   * False: the feed only turns the listener with the camera, and loads and passes no stars, so no
   * star voice is planned. Default true.
   */
  readonly voices?: boolean;
}

/** As the star fields: plan tiles again when the view moves 0.01 rad, at most every 120 ms. */
const VIEW_REPLAN_RAD = 0.01;
const VIEW_REPLAN_MS = 120;
/** The tile cone is the frustum's half diagonal plus this margin, as the star fields use. */
const VIEW_CONE_MARGIN_RAD = 0.08;
/** Sources are computed again at most ten times a second. */
const SOURCE_REFRESH_MS = 100;
/** A failed astro tile is asked for again after this long, the loader's own retry interval. */
const ASTRO_RETRY_MS = 15_000;
/** Orientations whose quaternions differ by less than this (1 - |dot|) are the same. */
const ORIENTATION_EPSILON = 1e-12;
/** The naked-eye layer holds about 12,000 stars; a fully unveiled view in one direction fits too. */
const DEFAULT_MAX_RESIDENT_STARS = 400_000;

type AstroState =
  | { readonly state: 'loading' }
  | { readonly state: 'ready'; readonly tile: CatalogStarAstroTile }
  | { readonly state: 'failed'; readonly at: number };

const tileKey = (layer: number, pix: number) => `${layer}/${pix}`;

function sameOrientation(a: Quat, b: Quat): boolean {
  const dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  return 1 - Math.abs(dot) < ORIENTATION_EPSILON;
}

function sameView(a: SkyView, b: SkyView): boolean {
  return (
    sameOrientation(a.orientationIcrs, b.orientationIcrs) &&
    a.tanHalfFovX === b.tanHalfFovX &&
    a.tanHalfFovY === b.tanHalfFovY &&
    a.limitingMagnitude === b.limitingMagnitude
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class SkyAudioFeed {
  private readonly now: () => number;
  private loader: CatalogStarTileLoader | null = null;
  private catalog: SkyCatalogStatus = 'idle';
  private error: string | null = null;
  private readonly astro = new Map<string, AstroState>();
  private astroFailures = 0;
  private orientation: Quat | null = null;
  private planned: { center: Vec3; radius: number; limit: number; at: number } | null = null;
  private computed: { view: SkyView; at: number } | null = null;
  private dirty = true;
  private evidenceClass: string | null = null;
  private passedIds: ReadonlySet<string> = new Set();
  /** The evidence class and ids last passed; the engine starts with no sources and no class. */
  private passedKey = '|';
  /** Tiles in view at the last update whose astro columns were absent. */
  private withoutAstro: ReadonlySet<string> = new Set();
  private counts = { starsInView: 0, completeInView: 0, audibleSources: 0, silentSources: 0, tilesWithoutAstro: 0 };
  private disposed = false;

  constructor(
    private readonly audio: SkyAudioTarget,
    private readonly options: SkyAudioFeedOptions = {}
  ) {
    this.now = options.now ?? (() => performance.now());
  }

  /** Whether the feed passes the stars in view to the engine (see `SkyAudioFeedOptions.voices`). */
  get voicesEnabled(): boolean {
    return this.options.voices !== false;
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
    if (!this.voicesEnabled) return;
    if (this.catalog === 'idle') this.open();
    const loader = this.loader;
    if (!loader) return;
    const now = this.now();
    this.planTiles(loader, view, now);
    if (this.astroRetryDue(now)) this.dirty = true;
    if (this.computed && now - this.computed.at < SOURCE_REFRESH_MS) return;
    if (!this.dirty && this.computed && sameView(this.computed.view, view)) return;
    this.computeSources(loader, view, now);
  }

  /** The evidence class of the star layer being drawn; null where the renderer declares none. */
  setEvidenceClass(evidenceClass: string | null): void {
    const next = typeof evidenceClass === 'string' && evidenceClass.length > 0 ? evidenceClass : null;
    if (next === this.evidenceClass) return;
    this.evidenceClass = next;
    this.dirty = true;
  }

  getStatus(): SkyAudioFeedStatus {
    let pendingAstro = 0;
    for (const state of this.astro.values()) if (state.state === 'loading') pendingAstro += 1;
    const stats = this.loader?.getStats();
    const tilesArrived = stats !== undefined && stats.tilesInFlight === 0 && stats.tilesLoaded >= stats.tilesWanted;
    return {
      catalog: this.catalog,
      error: this.error,
      ...this.counts,
      astroFailures: this.astroFailures,
      settled: this.catalog === 'unavailable' || (this.catalog === 'ready' && tilesArrived && pendingAstro === 0 && !this.dirty),
    };
  }

  dispose(): void {
    this.disposed = true;
    this.loader?.dispose();
    this.loader = null;
    this.astro.clear();
  }

  private open(): void {
    this.catalog = 'loading';
    CatalogStarTileLoader.open(this.options.baseUrl ?? STAR_CATALOG_BASE_URL, {
      ...(this.options.fetch ? { fetch: this.options.fetch } : {}),
      maxResidentStars: this.options.maxResidentStars ?? DEFAULT_MAX_RESIDENT_STARS,
      onTileLoaded: () => {
        this.dirty = true;
      },
      onTileEvicted: (key) => {
        this.astro.delete(tileKey(key.layer, key.pix));
        this.dirty = true;
      },
    }).then(
      (loader) => {
        if (this.disposed) {
          loader.dispose();
          return;
        }
        this.loader = loader;
        this.catalog = 'ready';
        this.dirty = true;
        if (loader.manifest.formatVersion < 2) {
          this.error = `catalog release ${loader.manifest.releaseId} has no astro tiles, so every star stays silent`;
        }
      },
      (error: unknown) => {
        if (this.disposed) return;
        // No stand-in stars: without the catalog the sky stays silent
        this.catalog = 'unavailable';
        this.error = errorMessage(error);
        console.warn('[sky audio] catalog unavailable; the sky stays silent', error);
      }
    );
  }

  private planTiles(loader: CatalogStarTileLoader, view: SkyView, now: number): void {
    const radius = view.halfDiagonalRad + VIEW_CONE_MARGIN_RAD;
    const last = this.planned;
    const changed =
      last === null ||
      angleBetweenUnit(last.center, view.centerIcrs) > VIEW_REPLAN_RAD ||
      Math.abs(radius - last.radius) > 0.01 ||
      last.limit !== view.limitingMagnitude;
    if (!changed || (last !== null && now - last.at < VIEW_REPLAN_MS)) return;
    const [x, y, z] = view.centerIcrs;
    loader.setView({ centerIcrs: [x, y, z], radiusRad: radius, limitingMagnitude: view.limitingMagnitude });
    this.planned = { center: view.centerIcrs, radius, limit: view.limitingMagnitude, at: now };
  }

  private computeSources(loader: CatalogStarTileLoader, view: SkyView, now: number): void {
    this.dirty = false;
    this.computed = { view, at: now };
    const tiles: SkyTileData[] = loader.getLoadedTiles().map((core) => {
      const state = this.astro.get(tileKey(core.layer, core.pix));
      return { core, astro: state?.state === 'ready' ? state.tile : null };
    });
    const result = skySourcesInView(tiles, view, {
      releaseId: loader.manifest.releaseId,
      ...(this.options.maxAudible !== undefined ? { maxAudible: this.options.maxAudible } : {}),
      ...(this.options.maxSilent !== undefined ? { maxSilent: this.options.maxSilent } : {}),
      evidenceClass: this.evidenceClass,
      incumbents: this.passedIds,
    });
    this.withoutAstro = new Set(result.tilesWithoutAstro);
    for (const key of result.tilesWithoutAstro) this.requestAstro(loader, key, now);

    let audibleSources = 0;
    for (const source of result.sources) {
      if (source.distancePc != null && source.teffK != null && source.luminositySolar != null) audibleSources += 1;
    }
    this.counts = {
      starsInView: result.starsInView,
      completeInView: result.completeInView,
      audibleSources,
      silentSources: result.sources.length - audibleSources,
      tilesWithoutAstro: result.tilesWithoutAstro.length,
    };

    // A star's catalog values never change within a release, so its id stands for its values
    const ids = result.sources.map((source) => source.id);
    const passedKey = `${this.evidenceClass ?? ''}|${ids.join(',')}`;
    if (passedKey === this.passedKey) return;
    this.passedKey = passedKey;
    this.passedIds = new Set(ids);
    this.audio.setSources(result.sources);
  }

  private requestAstro(loader: CatalogStarTileLoader, key: string, now: number): void {
    if (loader.manifest.formatVersion < 2) return;
    const state = this.astro.get(key);
    if (state?.state === 'loading' || state?.state === 'ready') return;
    if (state?.state === 'failed' && now - state.at < ASTRO_RETRY_MS) return;
    const [layer, pix] = key.split('/').map(Number) as [number, number];
    this.astro.set(key, { state: 'loading' });
    loader.loadAstro(layer, pix).then(
      (tile) => {
        if (this.disposed) return;
        this.astro.set(key, { state: 'ready', tile });
        if (![...this.astro.values()].some((other) => other.state === 'failed')) this.error = null;
        this.dirty = true;
      },
      (error: unknown) => {
        if (this.disposed) return;
        this.astro.set(key, { state: 'failed', at: this.now() });
        this.astroFailures += 1;
        this.error = errorMessage(error);
        this.dirty = true;
      }
    );
  }

  /** A failed astro tile still in view is due to be asked for again. */
  private astroRetryDue(now: number): boolean {
    for (const key of this.withoutAstro) {
      const state = this.astro.get(key);
      if (state?.state === 'failed' && now - state.at >= ASTRO_RETRY_MS) return true;
    }
    return false;
  }
}

/**
 * The feed the viewport drives, sounding through the app's audio engine. Its continuous star
 * voices are off (`voices: false`): under the music-first architecture (AUDIO_ENGINE.md sections 8
 * and 13), the stars in view are voiced inside the musical score rather than as continuous
 * sustained drones, so `skyAudio` updates only listener orientation with the camera.
 */
export const skyAudio = new SkyAudioFeed(observatoryAudio, { voices: false });
