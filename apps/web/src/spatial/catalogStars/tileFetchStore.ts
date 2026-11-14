import {
  CatalogStarNameIndexSchema,
  CatalogStarTileManifestSchema,
  type CatalogStarNameIndex,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import {
  decodeAstroTile,
  decodeCoreTile,
  decodeDetailTile,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
} from './tileCodec.js';
import {
  DEFAULT_MAX_CONCURRENT_TILE_FETCHES,
  DEFAULT_MAX_RESIDENT_STARS,
  MAX_RECENT_TILE_TIMINGS,
  TILE_RETRY_AFTER_MS,
  formatTileKey,
  normalizeCatalogBaseUrl,
  resolveCatalogTileUrl,
  type CatalogStarLoaderStats,
  type CatalogStarTileKey,
  type CatalogStarTileLoaderOptions,
  type CatalogStarTileTiming,
  type CatalogStarView,
  type CatalogTileKind,
  type PlannedCatalogTile,
} from './tileLoaderTypes.js';
import type { TileViewPlanner } from './tileViewPlanner.js';

function resolveFetchImpl(customFetch?: typeof fetch): typeof fetch {
  return customFetch ?? ((input, init) => fetch(input, init));
}

function getOrLoadCached<T>(
  cache: Map<string, Promise<T>>,
  key: string,
  load: () => Promise<T>
): Promise<T> {
  let promise = cache.get(key);
  if (!promise) {
    const created = load();
    cache.set(key, created);
    created.catch(() => {
      if (cache.get(key) === created) {
        cache.delete(key);
      }
    });
    promise = created;
  }
  return promise;
}

export async function fetchCatalogManifest(
  baseUrl: string,
  options: CatalogStarTileLoaderOptions
): Promise<{ manifest: CatalogStarTileManifest; normalizedBaseUrl: string }> {
  const fetchImpl = resolveFetchImpl(options.fetch);
  const normalizedBaseUrl = normalizeCatalogBaseUrl(baseUrl);
  const response = await fetchImpl(`${normalizedBaseUrl}manifest.json`);
  if (!response.ok) {
    throw new Error(`catalog star manifest unavailable (${response.status})`);
  }
  const manifest = CatalogStarTileManifestSchema.parse(await response.json());
  return { manifest, normalizedBaseUrl };
}

/**
 * Manages core tile streaming concurrency, retry backoff, resident star budget eviction,
 * timing/byte telemetry, and on-demand caching of names, detail tiles, and astro tiles.
 */
export class TileFetchStore {
  private readonly fetchImpl: typeof fetch;
  private readonly maxConcurrent: number;
  private readonly maxResidentStars: number;
  private readonly now: () => number;
  private readonly loaded = new Map<string, CatalogStarCoreTile>();
  private readonly inFlight = new Set<string>();
  private readonly failed = new Map<string, number>();
  private readonly details = new Map<string, Promise<CatalogStarDetailTile>>();
  private readonly astro = new Map<string, Promise<CatalogStarAstroTile>>();
  private namesPromise: Promise<CatalogStarNameIndex> | null = null;
  private wanted: PlannedCatalogTile[] = [];
  private view: CatalogStarView | null = null;
  private disposed = false;
  private stats = { bytesLoaded: 0, fetchMsTotal: 0, decodeMsTotal: 0, failures: 0 };
  private recent: CatalogStarTileTiming[] = [];

  constructor(
    private readonly manifest: CatalogStarTileManifest,
    private readonly baseUrl: string,
    private readonly planner: TileViewPlanner,
    private readonly options: CatalogStarTileLoaderOptions
  ) {
    this.fetchImpl = resolveFetchImpl(options.fetch);
    this.maxConcurrent = options.maxConcurrent ?? DEFAULT_MAX_CONCURRENT_TILE_FETCHES;
    this.maxResidentStars = options.maxResidentStars ?? DEFAULT_MAX_RESIDENT_STARS;
    this.now = options.now ?? (() => performance.now());
  }

  setView(view: CatalogStarView): void {
    if (this.disposed) return;
    this.view = view;
    this.wanted = this.planner.plan(view);
    this.evictIfNeeded();
    this.pump();
  }

  getLoadedTiles(): CatalogStarCoreTile[] {
    return [...this.loaded.values()];
  }

  getStats(): CatalogStarLoaderStats {
    let stars = 0;
    for (const tile of this.loaded.values()) stars += tile.count;
    return {
      releaseId: this.manifest.releaseId,
      tilesLoaded: this.loaded.size,
      tilesInFlight: this.inFlight.size,
      tilesWanted: this.wanted.length,
      starsResident: stars,
      ...this.stats,
      recent: [...this.recent],
    };
  }

  loadNames(): Promise<CatalogStarNameIndex> {
    if (!this.namesPromise) {
      const created = this.fetchImpl(`${this.baseUrl}${this.manifest.files.names}`)
        .then((response) => {
          if (!response.ok) {
            throw new Error(`catalog star names unavailable (${response.status})`);
          }
          return response.json();
        })
        .then((json) => CatalogStarNameIndexSchema.parse(json));
      this.namesPromise = created;
      created.catch(() => {
        if (this.namesPromise === created) {
          this.namesPromise = null;
        }
      });
    }
    return this.namesPromise;
  }

  async ensureCoreTile(layer: number, pix: number): Promise<CatalogStarCoreTile> {
    const key = formatTileKey(layer, pix);
    return this.loaded.get(key) ?? (await this.fetchCore({ layer, pix }));
  }

  loadDetailTile(layer: number, pix: number, order: number): Promise<CatalogStarDetailTile> {
    const key = formatTileKey(layer, pix);
    return getOrLoadCached(this.details, key, () =>
      this.fetchBuffer(this.tileUrl(layer, pix, 'detail')).then((buffer) =>
        decodeDetailTile(buffer, { layer, order, pix })
      )
    );
  }

  loadAstroTile(layer: number, pix: number): Promise<CatalogStarAstroTile> {
    if (this.manifest.formatVersion < 2) {
      return Promise.reject(
        new Error(
          `catalog release ${this.manifest.releaseId} has no astro tiles (manifest format ${this.manifest.formatVersion})`
        )
      );
    }
    const spec = this.manifest.layers[layer];
    if (!spec || !(pix >= 0 && (spec.tileStarCounts[pix] ?? 0) > 0)) {
      return Promise.reject(new Error(`no catalog star tile ${formatTileKey(layer, pix)}`));
    }
    const key = formatTileKey(layer, pix);
    return getOrLoadCached(this.astro, key, () =>
      this.fetchBuffer(this.tileUrl(layer, pix, 'astro')).then((buffer) =>
        decodeAstroTile(buffer, { layer, order: spec.order, pix })
      )
    );
  }

  dispose(): void {
    this.disposed = true;
    this.wanted = [];
    this.loaded.clear();
    this.details.clear();
    this.astro.clear();
  }

  private tileUrl(layer: number, pix: number, kind: CatalogTileKind): string {
    return resolveCatalogTileUrl(
      this.baseUrl,
      this.manifest.tiling.tilePath,
      layer,
      pix,
      kind
    );
  }

  private pump(): void {
    for (const { key } of this.wanted) {
      if (this.inFlight.size >= this.maxConcurrent) return;
      const id = formatTileKey(key.layer, key.pix);
      if (this.loaded.has(id) || this.inFlight.has(id)) continue;
      const failedAt = this.failed.get(id);
      if (failedAt !== undefined && this.now() - failedAt < TILE_RETRY_AFTER_MS) continue;
      this.inFlight.add(id);
      this.fetchCore(key)
        .then((tile) => {
          this.inFlight.delete(id);
          if (this.disposed) return;
          this.failed.delete(id);
          // Kept even if the view moved on meanwhile; eviction trims the farthest tiles
          this.loaded.set(id, tile);
          this.options.onTileLoaded?.(tile);
          this.evictIfNeeded();
          this.pump();
        })
        .catch((error: unknown) => {
          this.inFlight.delete(id);
          if (this.disposed) return;
          this.failed.set(id, this.now());
          this.stats.failures += 1;
          this.options.onError?.(error, key);
          this.pump();
        });
    }
  }

  private async fetchBuffer(url: string): Promise<ArrayBuffer> {
    const response = await this.fetchImpl(url);
    if (!response.ok) {
      throw new Error(`catalog star tile unavailable (${response.status}): ${url}`);
    }
    return response.arrayBuffer();
  }

  private async fetchCore(key: CatalogStarTileKey): Promise<CatalogStarCoreTile> {
    const layer = this.manifest.layers[key.layer];
    if (!layer) throw new Error(`unknown catalog star layer ${key.layer}`);
    const t0 = this.now();
    const buffer = await this.fetchBuffer(this.tileUrl(key.layer, key.pix, 'core'));
    const t1 = this.now();
    const tile = decodeCoreTile(buffer, { layer: key.layer, order: layer.order, pix: key.pix });
    const t2 = this.now();
    this.stats.bytesLoaded += buffer.byteLength;
    this.stats.fetchMsTotal += t1 - t0;
    this.stats.decodeMsTotal += t2 - t1;
    this.recent.push({
      key: formatTileKey(key.layer, key.pix),
      stars: tile.count,
      bytes: buffer.byteLength,
      fetchMs: t1 - t0,
      decodeMs: t2 - t1,
    });
    if (this.recent.length > MAX_RECENT_TILE_TIMINGS) {
      this.recent.shift();
    }
    return tile;
  }

  private evictIfNeeded(): void {
    let resident = 0;
    for (const tile of this.loaded.values()) resident += tile.count;
    if (resident <= this.maxResidentStars) return;
    const candidates = this.planner.rankEvictionCandidates(
      this.loaded,
      this.wanted,
      this.view?.centerIcrs ?? null
    );
    for (const candidate of candidates) {
      if (resident <= this.maxResidentStars) break;
      this.loaded.delete(candidate.id);
      this.details.delete(candidate.id);
      resident -= candidate.tile.count;
      this.options.onTileEvicted?.({ layer: candidate.tile.layer, pix: candidate.tile.pix });
    }
  }
}
