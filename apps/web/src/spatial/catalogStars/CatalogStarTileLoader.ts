import {
  CatalogStarNameIndexSchema,
  CatalogStarTileManifestSchema,
  type CatalogStarCatalogId,
  type CatalogStarDistanceKind,
  type CatalogStarName,
  type CatalogStarNameIndex,
  type CatalogStarTileLayer,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import { angleBetween, tileBoundingRadius, tileCenter, vecToRaDecDeg, type Vec3 } from './healpix.js';
import {
  catalogIdAt,
  catalogOf,
  decodeAstroTile,
  decodeCoreTile,
  decodeDetailTile,
  decodeDistanceBounds,
  distanceKindOf,
  halfToFloat,
  magnitudeBandOf,
  usesBMinusV,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
} from './tileCodec.js';

/**
 * Streams catalog star tiles for the current view down to a limiting magnitude.
 *
 * The view asks for every layer whose faint edge the limiting magnitude reaches, and inside
 * each layer only the HEALPix tiles that overlap the view cone. Brighter layers load first,
 * then tiles nearest the view center. Everything loaded here is catalog data
 * (`evidenceKind: 'catalog-derived'`), never stars measured in a user's recording.
 */

export interface CatalogStarView {
  /** View direction as an ICRS unit vector */
  centerIcrs: Vec3;
  /** Angular radius of the view cone, radians (half the frustum diagonal for a camera) */
  radiusRad: number;
  limitingMagnitude: number;
}

export interface CatalogStarTileKey {
  layer: number;
  pix: number;
}

export interface CatalogStarTileTiming {
  key: string;
  stars: number;
  bytes: number;
  fetchMs: number;
  decodeMs: number;
}

export interface CatalogStarLoaderStats {
  releaseId: string;
  tilesLoaded: number;
  tilesInFlight: number;
  tilesWanted: number;
  starsResident: number;
  bytesLoaded: number;
  fetchMsTotal: number;
  decodeMsTotal: number;
  failures: number;
  recent: CatalogStarTileTiming[];
}

export interface CatalogStarRecord {
  evidenceKind: 'catalog-derived';
  catalog: CatalogStarCatalogId;
  catalogId: string;
  /** "Gaia DR3 <source_id>" or "HIP <number>" */
  designation: string;
  names: CatalogStarName[];
  raDeg: number;
  decDeg: number;
  epochJulianYear: number;
  magnitude: number;
  magnitudeBand: 'G' | 'Hp';
  colorIndex: number | null;
  colorIndexKind: 'BP-RP' | 'B-V';
  distance: { medianPc: number; lowerPc: number | null; upperPc: number | null; kind: CatalogStarDistanceKind } | null;
  properMotion: { raMasYr: number; decMasYr: number } | null;
  radialVelocityKmS: number | null;
  sourceIds: string[];
}

/** A catalog value with its 16th and 84th percentiles; a bound the catalog lacks is null. */
export interface CatalogStarInterval {
  median: number;
  lower: number | null;
  upper: number | null;
}

/**
 * Gaia DR3 astrophysics for one star (manifest format 2 releases). Every field is null where
 * Gaia DR3 gives no value, which includes every Hipparcos entry; nothing is estimated here.
 */
export interface CatalogStarAstrophysics {
  evidenceKind: 'catalog-derived';
  /** GSP-Phot effective temperature, kelvin */
  temperatureK: CatalogStarInterval | null;
  /** FLAME luminosity, solar units */
  luminositySolar: CatalogStarInterval | null;
  /** GSP-Phot monochromatic extinction A0 at 547.7 nm, magnitudes */
  extinctionA0Mag: CatalogStarInterval | null;
  /** GSP-Phot extinction in the Gaia G band, magnitudes */
  extinctionGMag: number | null;
  /** Renormalised unit weight error of the astrometric solution */
  ruwe: number | null;
  /** Standard error of the Gaia parallax, milliarcseconds */
  parallaxErrorMas: number | null;
  /** Manifest source ids behind the values present */
  sourceIds: string[];
}

export interface CatalogStarTileLoaderOptions {
  fetch?: typeof fetch;
  maxConcurrent?: number;
  /** Tiles beyond this many resident stars are evicted, farthest from the view first */
  maxResidentStars?: number;
  onTileLoaded?: (tile: CatalogStarCoreTile) => void;
  onTileEvicted?: (key: CatalogStarTileKey) => void;
  onError?: (error: unknown, key: CatalogStarTileKey) => void;
  now?: () => number;
}

interface TileGeometry {
  center: Vec3;
  radius: number;
}

const tileKeyString = (layer: number, pix: number) => `${layer}/${pix}`;
const RETRY_AFTER_MS = 15_000;

const valueOrNull = (value: number | undefined) => (value !== undefined && Number.isFinite(value) ? value : null);

function intervalAt(
  median: Float32Array,
  lower: Float32Array,
  upper: Float32Array,
  index: number
): CatalogStarInterval | null {
  const m = valueOrNull(median[index]);
  return m === null ? null : { median: m, lower: valueOrNull(lower[index]), upper: valueOrNull(upper[index]) };
}

export class CatalogStarTileLoader {
  readonly manifest: CatalogStarTileManifest;
  readonly baseUrl: string;

  private readonly fetchImpl: typeof fetch;
  private readonly maxConcurrent: number;
  private readonly maxResidentStars: number;
  private readonly options: CatalogStarTileLoaderOptions;
  private readonly now: () => number;
  private readonly geometry = new Map<number, TileGeometry[]>();
  private readonly loaded = new Map<string, CatalogStarCoreTile>();
  private readonly inFlight = new Set<string>();
  private readonly failed = new Map<string, number>();
  private readonly details = new Map<string, Promise<CatalogStarDetailTile>>();
  private readonly astro = new Map<string, Promise<CatalogStarAstroTile>>();
  private namesPromise: Promise<CatalogStarNameIndex> | null = null;
  private wanted: { key: CatalogStarTileKey; distance: number }[] = [];
  private view: CatalogStarView | null = null;
  private disposed = false;
  private stats = { bytesLoaded: 0, fetchMsTotal: 0, decodeMsTotal: 0, failures: 0 };
  private recent: CatalogStarTileTiming[] = [];

  private constructor(manifest: CatalogStarTileManifest, baseUrl: string, options: CatalogStarTileLoaderOptions) {
    this.manifest = manifest;
    this.baseUrl = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
    this.options = options;
    this.fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    this.maxConcurrent = options.maxConcurrent ?? 6;
    this.maxResidentStars = options.maxResidentStars ?? 1_500_000;
    this.now = options.now ?? (() => performance.now());
  }

  /** Fetches and validates `<baseUrl>/manifest.json` against the contract schema. */
  static async open(baseUrl: string, options: CatalogStarTileLoaderOptions = {}): Promise<CatalogStarTileLoader> {
    const fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    const root = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
    const response = await fetchImpl(`${root}manifest.json`);
    if (!response.ok) {
      throw new Error(`catalog star manifest unavailable (${response.status})`);
    }
    const manifest = CatalogStarTileManifestSchema.parse(await response.json());
    return new CatalogStarTileLoader(manifest, root, options);
  }

  layersFor(limitingMagnitude: number): CatalogStarTileLayer[] {
    return this.manifest.layers.filter((layer) => (layer.magMin ?? Number.NEGATIVE_INFINITY) < limitingMagnitude);
  }

  /** Non-empty tiles overlapping the view, brightest layer first, then nearest the center. */
  planTiles(view: CatalogStarView): CatalogStarTileKey[] {
    return this.plan(view).map((entry) => entry.key);
  }

  setView(view: CatalogStarView): void {
    if (this.disposed) return;
    this.view = view;
    this.wanted = this.plan(view);
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

  /** IAU WGSN names attached to catalog stars by HIP number (`names.json`). */
  loadNames(): Promise<CatalogStarNameIndex> {
    if (!this.namesPromise) {
      this.namesPromise = this.fetchImpl(`${this.baseUrl}${this.manifest.files.names}`)
        .then((response) => {
          if (!response.ok) throw new Error(`catalog star names unavailable (${response.status})`);
          return response.json();
        })
        .then((json) => CatalogStarNameIndexSchema.parse(json));
      this.namesPromise.catch(() => {
        this.namesPromise = null;
      });
    }
    return this.namesPromise;
  }

  /** Full catalog record for one star of a loaded core tile; fetches its detail tile once. */
  async getStarRecord(layer: number, pix: number, index: number): Promise<CatalogStarRecord> {
    const key = tileKeyString(layer, pix);
    const core = this.loaded.get(key) ?? (await this.fetchCore({ layer, pix }));
    if (index < 0 || index >= core.count) {
      throw new Error(`star index ${index} outside tile ${key}`);
    }
    let detailPromise = this.details.get(key);
    if (!detailPromise) {
      detailPromise = this.fetchBuffer(this.tileUrl(layer, pix, 'detail')).then((buffer) =>
        decodeDetailTile(buffer, { layer, order: core.order, pix })
      );
      this.details.set(key, detailPromise);
      detailPromise.catch(() => this.details.delete(key));
    }
    const detail = await detailPromise;
    const flags = core.flags[index] ?? 0;
    const catalog = catalogOf(flags);
    const catalogId = catalogIdAt(detail, index);
    const { raDeg, decDeg } = vecToRaDecDeg([
      core.directions[3 * index] ?? 0,
      core.directions[3 * index + 1] ?? 0,
      core.directions[3 * index + 2] ?? 0,
    ]);
    const median = core.distancePc[index] ?? Number.NaN;
    const bounds = decodeDistanceBounds(
      median,
      detail.distanceLowerCode[index] ?? 0,
      detail.distanceUpperCode[index] ?? 0
    );
    const pmRa = halfToFloat(detail.pmRaHalf[index] ?? 0x7e00);
    const pmDec = halfToFloat(detail.pmDecHalf[index] ?? 0x7e00);
    const rv = halfToFloat(detail.radialVelocityHalf[index] ?? 0x7e00);
    const color = core.colorIndex[index] ?? Number.NaN;
    const names = (await this.loadNames().catch(() => null))?.names.filter(
      (n) => n.catalog === catalog && n.catalogId === catalogId
    );
    return {
      evidenceKind: 'catalog-derived',
      catalog,
      catalogId,
      designation: catalog === 'gaia-dr3' ? `Gaia DR3 ${catalogId}` : `HIP ${catalogId}`,
      names: names ?? [],
      raDeg,
      decDeg,
      epochJulianYear: this.manifest.epochJulianYear,
      magnitude: core.magnitude[index] ?? Number.NaN,
      magnitudeBand: magnitudeBandOf(flags),
      colorIndex: Number.isFinite(color) ? color : null,
      colorIndexKind: usesBMinusV(flags) ? 'B-V' : 'BP-RP',
      distance: Number.isFinite(median)
        ? {
            medianPc: median,
            lowerPc: Number.isFinite(bounds.lowerPc) ? bounds.lowerPc : null,
            upperPc: Number.isFinite(bounds.upperPc) ? bounds.upperPc : null,
            kind: distanceKindOf(flags),
          }
        : null,
      properMotion: Number.isFinite(pmRa) && Number.isFinite(pmDec) ? { raMasYr: pmRa, decMasYr: pmDec } : null,
      radialVelocityKmS: Number.isFinite(rv) ? rv : null,
      sourceIds:
        catalog === 'gaia-dr3'
          ? ['gaia-dr3', ...(distanceKindOf(flags) === 'none' ? [] : ['bailer-jones-2021'])]
          : ['hipparcos-2007'],
    };
  }

  /**
   * The astro tile of one non-empty tile (Gaia DR3 temperature, luminosity, extinction and
   * astrometric quality, index-aligned with its core tile), fetched once and kept. Releases
   * with manifest format 1 have none.
   */
  loadAstro(layer: number, pix: number): Promise<CatalogStarAstroTile> {
    if (this.manifest.formatVersion < 2) {
      return Promise.reject(
        new Error(`catalog release ${this.manifest.releaseId} has no astro tiles (manifest format ${this.manifest.formatVersion})`)
      );
    }
    const spec = this.manifest.layers[layer];
    if (!spec || !(pix >= 0 && (spec.tileStarCounts[pix] ?? 0) > 0)) {
      return Promise.reject(new Error(`no catalog star tile ${tileKeyString(layer, pix)}`));
    }
    const key = tileKeyString(layer, pix);
    let promise = this.astro.get(key);
    if (!promise) {
      promise = this.fetchBuffer(this.tileUrl(layer, pix, 'astro')).then((buffer) =>
        decodeAstroTile(buffer, { layer, order: spec.order, pix })
      );
      this.astro.set(key, promise);
      promise.catch(() => this.astro.delete(key));
    }
    return promise;
  }

  /** Gaia DR3 astrophysics of one star, from its astro tile; null wherever DR3 has no value. */
  async getStarAstrophysics(layer: number, pix: number, index: number): Promise<CatalogStarAstrophysics> {
    const tile = await this.loadAstro(layer, pix);
    if (!Number.isInteger(index) || index < 0 || index >= tile.count) {
      throw new Error(`star index ${index} outside tile ${tileKeyString(layer, pix)}`);
    }
    const luminositySolar = intervalAt(tile.luminositySolar, tile.luminosityLowerSolar, tile.luminosityUpperSolar, index);
    const record: CatalogStarAstrophysics = {
      evidenceKind: 'catalog-derived',
      temperatureK: intervalAt(tile.teffK, tile.teffLowerK, tile.teffUpperK, index),
      luminositySolar,
      extinctionA0Mag: intervalAt(tile.a0Mag, tile.a0LowerMag, tile.a0UpperMag, index),
      extinctionGMag: valueOrNull(tile.agMag[index]),
      ruwe: valueOrNull(tile.ruwe[index]),
      parallaxErrorMas: valueOrNull(tile.parallaxErrorMas[index]),
      sourceIds: [],
    };
    const fromGaiaSource = [record.temperatureK, record.extinctionA0Mag, record.extinctionGMag, record.ruwe, record.parallaxErrorMas];
    if (fromGaiaSource.some((value) => value !== null)) record.sourceIds.push('gaia-dr3');
    if (luminositySolar !== null) record.sourceIds.push('gaia-dr3-astrophysical-parameters');
    return record;
  }

  dispose(): void {
    this.disposed = true;
    this.wanted = [];
    this.loaded.clear();
    this.details.clear();
    this.astro.clear();
  }

  private tileUrl(layer: number, pix: number, kind: 'core' | 'detail' | 'astro'): string {
    const path = this.manifest.tiling.tilePath
      .replace('{layer}', String(layer))
      .replace('{pix}', String(pix))
      .replace('{kind}', kind);
    return `${this.baseUrl}${path}`;
  }

  private geometryFor(layer: CatalogStarTileLayer): TileGeometry[] {
    let geo = this.geometry.get(layer.layer);
    if (!geo) {
      geo = layer.tileStarCounts.map((_, pix) => ({
        center: tileCenter(layer.order, pix),
        radius: tileBoundingRadius(layer.order, pix),
      }));
      this.geometry.set(layer.layer, geo);
    }
    return geo;
  }

  private plan(view: CatalogStarView): { key: CatalogStarTileKey; distance: number }[] {
    const out: { key: CatalogStarTileKey; distance: number; layer: number }[] = [];
    for (const layer of this.layersFor(view.limitingMagnitude)) {
      const geo = this.geometryFor(layer);
      layer.tileStarCounts.forEach((count, pix) => {
        const tile = geo[pix];
        if (count === 0 || !tile) return;
        const distance = angleBetween(view.centerIcrs, tile.center);
        if (distance - tile.radius <= view.radiusRad) {
          out.push({ key: { layer: layer.layer, pix }, distance, layer: layer.layer });
        }
      });
    }
    out.sort((a, b) => a.layer - b.layer || a.distance - b.distance);
    return out.map(({ key, distance }) => ({ key, distance }));
  }

  private pump(): void {
    for (const { key } of this.wanted) {
      if (this.inFlight.size >= this.maxConcurrent) return;
      const id = tileKeyString(key.layer, key.pix);
      if (this.loaded.has(id) || this.inFlight.has(id)) continue;
      const failedAt = this.failed.get(id);
      if (failedAt !== undefined && this.now() - failedAt < RETRY_AFTER_MS) continue;
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
      key: tileKeyString(key.layer, key.pix),
      stars: tile.count,
      bytes: buffer.byteLength,
      fetchMs: t1 - t0,
      decodeMs: t2 - t1,
    });
    if (this.recent.length > 64) this.recent.shift();
    return tile;
  }

  private evictIfNeeded(): void {
    let resident = 0;
    for (const tile of this.loaded.values()) resident += tile.count;
    if (resident <= this.maxResidentStars) return;
    const wantedIds = new Set(this.wanted.map((w) => tileKeyString(w.key.layer, w.key.pix)));
    const center = this.view?.centerIcrs;
    const candidates = [...this.loaded.entries()]
      .filter(([id]) => !wantedIds.has(id))
      .map(([id, tile]) => {
        const geo = this.geometry.get(tile.layer)?.[tile.pix];
        return { id, tile, distance: center && geo ? angleBetween(center, geo.center) : 0 };
      })
      .sort((a, b) => b.tile.layer - a.tile.layer || b.distance - a.distance);
    for (const candidate of candidates) {
      if (resident <= this.maxResidentStars) break;
      this.loaded.delete(candidate.id);
      this.details.delete(candidate.id);
      resident -= candidate.tile.count;
      this.options.onTileEvicted?.({ layer: candidate.tile.layer, pix: candidate.tile.pix });
    }
  }
}
