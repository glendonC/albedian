import type { CatalogStarTileManifest } from '@albedian/contracts';
import { STAR_CATALOG_BASE_URL } from '../../spatial/catalogStars/CatalogStarField.js';
import { CatalogStarTileLoader } from '../../spatial/catalogStars/CatalogStarTileLoader.js';
import type { CatalogStarAstroTile } from '../../spatial/catalogStars/tileCodec.js';
import type { SkyTileData } from '../skySources.js';
import type { Vec3 } from '../voicePlanning.js';
import { errorMessage, type SkyCatalogStatus } from './feedTypes.js';

/** A failed astro tile is asked for again after this long, the loader's own retry interval. */
export const ASTRO_RETRY_MS = 15_000;
/** The naked-eye layer holds about 12,000 stars; a fully unveiled view in one direction fits too. */
export const DEFAULT_MAX_RESIDENT_STARS = 400_000;
/** Manifest format version where Gaia DR3 astro tiles were introduced. */
export const MIN_ASTRO_MANIFEST_FORMAT = 2;

type AstroState =
  | { readonly state: 'loading' }
  | { readonly state: 'ready'; readonly tile: CatalogStarAstroTile }
  | { readonly state: 'failed'; readonly at: number };

export function formatTileKey(layer: number, pix: number): string {
  return `${layer}/${pix}`;
}

export function parseTileKey(key: string): [number, number] {
  const [layer, pix] = key.split('/').map(Number);
  return [layer!, pix!];
}

function supportsAstroTiles(manifest: CatalogStarTileManifest): boolean {
  return manifest.formatVersion >= MIN_ASTRO_MANIFEST_FORMAT;
}

export interface AstroTileStoreOptions {
  readonly baseUrl?: string;
  readonly fetch?: typeof fetch;
  readonly maxResidentStars?: number;
  readonly now: () => number;
  readonly onDirty: () => void;
}

/**
 * Owns the feed's `CatalogStarTileLoader` lifecycle and index-aligned Gaia DR3 astro tile cache,
 * including lazy manifest opening, 15 s retry backoff on failed astro tiles, and eviction cleanup.
 */
export class AstroTileStore {
  private loader: CatalogStarTileLoader | null = null;
  private catalogStatus: SkyCatalogStatus = 'idle';
  private lastError: string | null = null;
  private readonly astro = new Map<string, AstroState>();
  private withoutAstro: ReadonlySet<string> = new Set();
  private failures = 0;
  private disposed = false;

  constructor(private readonly options: AstroTileStoreOptions) {}

  get catalog(): SkyCatalogStatus {
    return this.catalogStatus;
  }

  get error(): string | null {
    return this.lastError;
  }

  get astroFailures(): number {
    return this.failures;
  }

  get releaseId(): string | null {
    return this.loader?.manifest.releaseId ?? null;
  }

  get isReady(): boolean {
    return this.loader !== null;
  }

  ensureOpen(): void {
    if (this.disposed || this.catalogStatus !== 'idle') return;
    this.catalogStatus = 'loading';
    CatalogStarTileLoader.open(this.options.baseUrl ?? STAR_CATALOG_BASE_URL, {
      ...(this.options.fetch ? { fetch: this.options.fetch } : {}),
      maxResidentStars: this.options.maxResidentStars ?? DEFAULT_MAX_RESIDENT_STARS,
      onTileLoaded: () => {
        this.options.onDirty();
      },
      onTileEvicted: (key) => {
        this.astro.delete(formatTileKey(key.layer, key.pix));
        this.options.onDirty();
      },
    }).then(
      (loader) => {
        if (this.disposed) {
          loader.dispose();
          return;
        }
        this.loader = loader;
        this.catalogStatus = 'ready';
        this.options.onDirty();
        if (!supportsAstroTiles(loader.manifest)) {
          this.lastError = `catalog release ${loader.manifest.releaseId} has no astro tiles, so every star stays silent`;
        }
      },
      (error: unknown) => {
        if (this.disposed) return;
        // No stand-in stars: without the catalog the sky stays silent
        this.catalogStatus = 'unavailable';
        this.lastError = errorMessage(error);
        console.warn('[sky audio] catalog unavailable; the sky stays silent', error);
      }
    );
  }

  setViewCone(centerIcrs: Vec3, radiusRad: number, limitingMagnitude: number): void {
    if (!this.loader) return;
    const [x, y, z] = centerIcrs;
    this.loader.setView({ centerIcrs: [x, y, z], radiusRad, limitingMagnitude });
  }

  getLoadedTilesWithAstro(): SkyTileData[] {
    if (!this.loader) return [];
    return this.loader.getLoadedTiles().map((core) => {
      const state = this.astro.get(formatTileKey(core.layer, core.pix));
      return { core, astro: state?.state === 'ready' ? state.tile : null };
    });
  }

  requestMissingAstro(tileKeysWithoutAstro: readonly string[], now: number): void {
    this.withoutAstro = new Set(tileKeysWithoutAstro);
    const loader = this.loader;
    if (!loader || !supportsAstroTiles(loader.manifest)) return;
    for (const key of tileKeysWithoutAstro) {
      this.requestSingleAstro(loader, key, now);
    }
  }

  /** True when a failed astro tile still in view has reached its retry interval. */
  isRetryDue(now: number): boolean {
    for (const key of this.withoutAstro) {
      const state = this.astro.get(key);
      if (state?.state === 'failed' && now - state.at >= ASTRO_RETRY_MS) return true;
    }
    return false;
  }

  isSettled(dirty: boolean): boolean {
    if (this.catalogStatus === 'unavailable') return true;
    if (this.catalogStatus !== 'ready' || dirty || !this.loader) return false;
    for (const state of this.astro.values()) {
      if (state.state === 'loading') return false;
    }
    const stats = this.loader.getStats();
    return stats.tilesInFlight === 0 && stats.tilesLoaded >= stats.tilesWanted;
  }

  dispose(): void {
    this.disposed = true;
    this.loader?.dispose();
    this.loader = null;
    this.astro.clear();
  }

  private requestSingleAstro(loader: CatalogStarTileLoader, key: string, now: number): void {
    const state = this.astro.get(key);
    if (state?.state === 'loading' || state?.state === 'ready') return;
    if (state?.state === 'failed' && now - state.at < ASTRO_RETRY_MS) return;
    const [layer, pix] = parseTileKey(key);
    this.astro.set(key, { state: 'loading' });
    loader.loadAstro(layer, pix).then(
      (tile) => {
        if (this.disposed) return;
        this.astro.set(key, { state: 'ready', tile });
        if (!this.hasFailedAstroTiles()) this.lastError = null;
        this.options.onDirty();
      },
      (error: unknown) => {
        if (this.disposed) return;
        this.astro.set(key, { state: 'failed', at: this.options.now() });
        this.failures += 1;
        this.lastError = errorMessage(error);
        this.options.onDirty();
      }
    );
  }

  private hasFailedAstroTiles(): boolean {
    for (const other of this.astro.values()) {
      if (other.state === 'failed') return true;
    }
    return false;
  }
}
