import type { Group, PerspectiveCamera } from 'three/webgpu';
import type { CatalogStarTileManifest } from '@albedian/contracts';
import {
  DEFAULT_LIMITING_MAGNITUDE,
  STAR_CATALOG_BASE_URL,
  type CatalogStarFieldStats,
  type CatalogStarFieldStatus,
} from '../../catalogStars/CatalogStarField.js';
import { CatalogStarTileLoader } from '../../catalogStars/CatalogStarTileLoader.js';
import type { CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { createPsfStarMaterial, type PsfStarMaterial } from './psfStarMaterial.js';
import { StarColourTable } from './starColor.js';
import {
  buildTileInstancedGeometry,
  createCatalogStarMountGroup,
  createTileSpriteMesh,
  disposeTileSpriteEntry,
  formatTileKey,
  type TileSpriteEntry,
} from './spriteField/instancedTileMesh.js';
import {
  DEFAULT_CATALOG_STAR_EXPOSURE,
  clampLimitingMagnitude,
  type CatalogStarSpriteFieldOptions,
} from './spriteField/spriteFieldTypes.js';
import { tileRadiance } from './spriteField/tileRadiance.js';
import {
  REFERENCE_DRAWING_HEIGHT_PX,
  createViewSyncState,
  invalidateViewSync,
  pollCameraIcrsView,
} from './spriteField/viewConeSync.js';

export { DEFAULT_CATALOG_STAR_EXPOSURE, tileRadiance };

/**
 * Catalog stars for the WebGPU host: streams the Gaia DR3 / Hipparcos 2007 tiles for the
 * camera's view and limiting magnitude, as CatalogStarField does for the WebGL renderer, and
 * draws each star as an analytic-PSF sprite at its catalog direction. Each mount is a sky
 * sphere of a given radius sharing the tile geometry.
 */
export class CatalogStarSpriteField {
  private loader: CatalogStarTileLoader | null = null;
  private status: CatalogStarFieldStatus = 'loading';
  private readonly mounts: Group[] = [];
  private readonly tiles = new Map<string, TileSpriteEntry>();
  private readonly psf: PsfStarMaterial;
  private readonly colours = new StarColourTable();
  private limitingMagnitude: number;
  private readonly viewSync = createViewSyncState();
  private disposed = false;
  readonly ready: Promise<void>;

  constructor(options: CatalogStarSpriteFieldOptions = {}) {
    this.limitingMagnitude = options.limitingMagnitude ?? DEFAULT_LIMITING_MAGNITUDE;
    this.psf = createPsfStarMaterial({
      exposure: options.exposure ?? DEFAULT_CATALOG_STAR_EXPOSURE,
      limitingMagnitude: this.limitingMagnitude,
    });
    this.ready = CatalogStarTileLoader.open(options.baseUrl ?? STAR_CATALOG_BASE_URL, {
      ...(options.fetch ? { fetch: options.fetch } : {}),
      onTileLoaded: (tile) => this.addTile(tile),
      onTileEvicted: (key) => this.removeTile(formatTileKey(key.layer, key.pix)),
      onError: (error, key) => console.warn(`[catalog stars] tile ${formatTileKey(key.layer, key.pix)} failed`, error),
    }).then(
      (loader) => {
        if (this.disposed) return;
        this.loader = loader;
        this.status = 'ready';
        this.setLimitingMagnitude(Math.min(this.limitingMagnitude, loader.manifest.magnitudeLimit));
      },
      (error: unknown) => {
        // No fallback stars: an unavailable catalog leaves the sky empty rather than invented
        this.status = 'unavailable';
        console.warn('[catalog stars] catalog unavailable', error);
      }
    );
  }

  /** A sky sphere of catalog stars at `radius` scene units, oriented ICRS to scene. */
  createMount(radius: number): Group {
    const group = createCatalogStarMountGroup(radius);
    for (const entry of this.tiles.values()) {
      const mesh = createTileSpriteMesh(entry.geometry, this.psf.material);
      group.add(mesh);
      entry.meshes.push(mesh);
    }
    this.mounts.push(group);
    return group;
  }

  getStatus(): CatalogStarFieldStatus {
    return this.status;
  }

  getManifest(): CatalogStarTileManifest | null {
    return this.loader?.manifest ?? null;
  }

  getLimitingMagnitude(): number {
    return this.limitingMagnitude;
  }

  setLimitingMagnitude(mag: number): void {
    this.limitingMagnitude = clampLimitingMagnitude(mag, this.loader?.manifest.magnitudeLimit);
    this.psf.limitingMagnitude.value = this.limitingMagnitude;
    invalidateViewSync(this.viewSync);
  }

  /** Linear exposure of the sprites (HDR energy of a magnitude 0 star). */
  setExposure(exposure: number): void {
    this.psf.exposure.value = exposure;
  }

  /**
   * Call once per frame: scales the PSF to the drawing buffer and feeds the camera's ICRS view
   * cone to the tile loader. `force` re-plans immediately (benchmarks and captures).
   */
  update(camera: PerspectiveCamera, drawingBufferHeight: number, nowMs: number = performance.now(), force = false): void {
    this.psf.psfScale.value = drawingBufferHeight / REFERENCE_DRAWING_HEIGHT_PX;
    if (!this.loader) return;
    const view = pollCameraIcrsView(this.viewSync, camera, this.mounts, this.limitingMagnitude, nowMs, force);
    if (view) this.loader.setView(view);
  }

  /** True once every tile the current view wants is resident and none is still loading. */
  isSettled(): boolean {
    if (this.status === 'unavailable') return true;
    if (!this.loader) return false;
    const stats = this.loader.getStats();
    return stats.tilesInFlight === 0 && stats.tilesLoaded >= stats.tilesWanted;
  }

  getStats(): CatalogStarFieldStats {
    return { ...(this.loader?.getStats() ?? {}), status: this.status, limitingMagnitude: this.limitingMagnitude };
  }

  /** Stars currently drawn (resident in loaded tiles). */
  residentStars(): number {
    let n = 0;
    for (const entry of this.tiles.values()) n += entry.geometry.instanceCount;
    return n;
  }

  dispose(): void {
    this.disposed = true;
    this.loader?.dispose();
    for (const key of [...this.tiles.keys()]) this.removeTile(key);
    this.psf.material.dispose();
  }

  private addTile(tile: CatalogStarCoreTile): void {
    if (this.disposed) return;
    const key = formatTileKey(tile.layer, tile.pix);
    if (this.tiles.has(key)) return;
    const geometry = buildTileInstancedGeometry(tile, this.colours);
    const meshes = this.mounts.map((mount) => {
      const mesh = createTileSpriteMesh(geometry, this.psf.material);
      mount.add(mesh);
      return mesh;
    });
    this.tiles.set(key, { geometry, meshes });
  }

  private removeTile(key: string): void {
    const entry = this.tiles.get(key);
    if (!entry) return;
    disposeTileSpriteEntry(entry);
    this.tiles.delete(key);
  }
}
