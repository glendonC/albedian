import type {
  CatalogStarNameIndex,
  CatalogStarTileLayer,
  CatalogStarTileManifest,
} from '@albedian/contracts';
import type { CatalogStarAstroTile, CatalogStarCoreTile } from './tileCodec.js';
import { fetchCatalogManifest, TileFetchStore } from './tileFetchStore.js';
import {
  normalizeCatalogBaseUrl,
  type CatalogStarAstrophysics,
  type CatalogStarInterval,
  type CatalogStarLoaderStats,
  type CatalogStarRecord,
  type CatalogStarTileKey,
  type CatalogStarTileLoaderOptions,
  type CatalogStarTileTiming,
  type CatalogStarView,
} from './tileLoaderTypes.js';
import {
  assembleStarAstrophysics,
  assembleStarRecord,
  assertCoreStarIndex,
} from './tileStarInspector.js';
import { TileViewPlanner } from './tileViewPlanner.js';

export type {
  CatalogStarAstrophysics,
  CatalogStarInterval,
  CatalogStarLoaderStats,
  CatalogStarRecord,
  CatalogStarTileKey,
  CatalogStarTileLoaderOptions,
  CatalogStarTileTiming,
  CatalogStarView,
};

/**
 * Streams catalog star tiles for the current view down to a limiting magnitude.
 *
 * The view asks for every layer whose faint edge the limiting magnitude reaches, and inside
 * each layer only the HEALPix tiles that overlap the view cone. Brighter layers load first,
 * then tiles nearest the view center. Everything loaded here is catalog data
 * (`evidenceKind: 'catalog-derived'`), never stars measured in a user's recording.
 */
export class CatalogStarTileLoader {
  readonly manifest: CatalogStarTileManifest;
  readonly baseUrl: string;

  private readonly planner: TileViewPlanner;
  private readonly store: TileFetchStore;

  private constructor(
    manifest: CatalogStarTileManifest,
    baseUrl: string,
    options: CatalogStarTileLoaderOptions
  ) {
    this.manifest = manifest;
    this.baseUrl = normalizeCatalogBaseUrl(baseUrl);
    this.planner = new TileViewPlanner(manifest);
    this.store = new TileFetchStore(manifest, this.baseUrl, this.planner, options);
  }

  /** Fetches and validates `<baseUrl>/manifest.json` against the contract schema. */
  static async open(
    baseUrl: string,
    options: CatalogStarTileLoaderOptions = {}
  ): Promise<CatalogStarTileLoader> {
    const { manifest, normalizedBaseUrl } = await fetchCatalogManifest(baseUrl, options);
    return new CatalogStarTileLoader(manifest, normalizedBaseUrl, options);
  }

  layersFor(limitingMagnitude: number): CatalogStarTileLayer[] {
    return this.planner.layersFor(limitingMagnitude);
  }

  /** Non-empty tiles overlapping the view, brightest layer first, then nearest the center. */
  planTiles(view: CatalogStarView): CatalogStarTileKey[] {
    return this.planner.planKeys(view);
  }

  setView(view: CatalogStarView): void {
    this.store.setView(view);
  }

  getLoadedTiles(): CatalogStarCoreTile[] {
    return this.store.getLoadedTiles();
  }

  getStats(): CatalogStarLoaderStats {
    return this.store.getStats();
  }

  /** IAU WGSN names attached to catalog stars by HIP number (`names.json`). */
  loadNames(): Promise<CatalogStarNameIndex> {
    return this.store.loadNames();
  }

  /** Full catalog record for one star of a loaded core tile; fetches its detail tile once. */
  async getStarRecord(layer: number, pix: number, index: number): Promise<CatalogStarRecord> {
    const core = await this.store.ensureCoreTile(layer, pix);
    assertCoreStarIndex(core, index);
    const detail = await this.store.loadDetailTile(layer, pix, core.order);
    const nameIndex = await this.loadNames().catch(() => null);
    return assembleStarRecord({
      core,
      detail,
      index,
      epochJulianYear: this.manifest.epochJulianYear,
      nameIndex,
    });
  }

  /**
   * The astro tile of one non-empty tile (Gaia DR3 temperature, luminosity, extinction and
   * astrometric quality, index-aligned with its core tile), fetched once and kept. Releases
   * with manifest format 1 have none.
   */
  loadAstro(layer: number, pix: number): Promise<CatalogStarAstroTile> {
    return this.store.loadAstroTile(layer, pix);
  }

  /** Gaia DR3 astrophysics of one star, from its astro tile; null wherever DR3 has no value. */
  async getStarAstrophysics(
    layer: number,
    pix: number,
    index: number
  ): Promise<CatalogStarAstrophysics> {
    const tile = await this.loadAstro(layer, pix);
    return assembleStarAstrophysics(tile, index);
  }

  dispose(): void {
    this.store.dispose();
  }
}
