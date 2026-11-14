import * as THREE from 'three';
import type { CatalogStarName, CatalogStarTileManifest } from '@albedian/contracts';
import { CatalogStarTileLoader, type CatalogStarRecord } from './CatalogStarTileLoader.js';
import type { CatalogStarCoreTile } from './tileCodec.js';
import {
  DEFAULT_LIMITING_MAGNITUDE,
  DEFAULT_MAX_LIMITING_MAGNITUDE,
  PINNED_STAR_CATALOG_RELEASE,
  STAR_CATALOG_BASE_URL,
  clampLimitingMagnitude,
  type CatalogStarFieldOptions,
  type CatalogStarFieldStats,
  type CatalogStarFieldStatus,
} from './field/fieldTypes.js';
import {
  ICRS_TO_SCENE_QUATERNION,
  createViewSyncState,
  icrsToScene,
  invalidateViewSync,
  pollCameraIcrsView,
  raDecToSceneDirection,
} from './field/icrsFrame.js';
import {
  createCatalogStarMaterial,
  setStarMaterialLimitingMagnitude,
  setStarMaterialPixelRatio,
} from './field/starPointMaterial.js';
import {
  buildTileBufferGeometry,
  buildTintLut,
  createCatalogStarMountGroup,
  createTilePoints,
  disposeTileMeshEntry,
  formatTileKey,
  type TileMeshEntry,
} from './field/tilePointsMesh.js';

export {
  DEFAULT_LIMITING_MAGNITUDE,
  ICRS_TO_SCENE_QUATERNION,
  PINNED_STAR_CATALOG_RELEASE,
  STAR_CATALOG_BASE_URL,
  icrsToScene,
  type CatalogStarFieldStats,
  type CatalogStarFieldStatus,
};

/**
 * Real catalog stars for the WebGL sky: streams Gaia DR3 / Hipparcos 2007 tiles for the
 * camera's view and limiting magnitude and draws them at their catalog directions.
 * Each mount is a sky sphere of a given radius that shares the same tile geometry.
 */
export class CatalogStarField {
  private loader: CatalogStarTileLoader | null = null;
  private status: CatalogStarFieldStatus = 'loading';
  private readonly mounts: THREE.Group[] = [];
  private readonly tiles = new Map<string, TileMeshEntry>();
  private readonly material: THREE.ShaderMaterial;
  private readonly tint = buildTintLut();
  private limitingMagnitude: number;
  private readonly viewSync = createViewSyncState();
  private disposed = false;
  readonly ready: Promise<void>;

  constructor(options: CatalogStarFieldOptions = {}) {
    this.limitingMagnitude = options.limitingMagnitude ?? DEFAULT_LIMITING_MAGNITUDE;
    this.material = createCatalogStarMaterial(this.limitingMagnitude);
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
        this.limitingMagnitude = Math.min(this.limitingMagnitude, loader.manifest.magnitudeLimit);
        setStarMaterialLimitingMagnitude(this.material, this.limitingMagnitude);
      },
      (error: unknown) => {
        // No fallback stars: an unavailable catalog leaves the sky empty rather than invented
        this.status = 'unavailable';
        console.warn('[catalog stars] catalog unavailable', error);
      }
    );
  }

  /** A sky sphere of catalog stars at `radius` scene units, oriented ICRS to scene. */
  createMount(radius: number): THREE.Group {
    const group = createCatalogStarMountGroup(radius);
    for (const entry of this.tiles.values()) {
      const points = createTilePoints(entry.geometry, this.material);
      group.add(points);
      entry.points.push(points);
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

  /** Moves the limiting magnitude; fainter layers stream in for the current view. */
  setLimitingMagnitude(mag: number): void {
    const ceiling = this.loader?.manifest.magnitudeLimit ?? DEFAULT_MAX_LIMITING_MAGNITUDE;
    this.limitingMagnitude = clampLimitingMagnitude(mag, ceiling);
    setStarMaterialLimitingMagnitude(this.material, this.limitingMagnitude);
    invalidateViewSync(this.viewSync);
  }

  /** Call once per frame: feeds the camera's ICRS view cone to the tile loader. */
  update(camera: THREE.PerspectiveCamera, pixelRatio: number, nowMs: number = performance.now()): void {
    setStarMaterialPixelRatio(this.material, pixelRatio);
    if (!this.loader) return;
    const view = pollCameraIcrsView(this.viewSync, camera, this.mounts, this.limitingMagnitude, nowMs);
    if (view) this.loader.setView(view);
  }

  getStats(): CatalogStarFieldStats {
    return { ...(this.loader?.getStats() ?? {}), status: this.status, limitingMagnitude: this.limitingMagnitude };
  }

  /** IAU WGSN names attached to catalog stars (labels come from here, not from the renderer). */
  async getNamedStars(): Promise<CatalogStarName[]> {
    await this.ready;
    if (!this.loader) return [];
    return (await this.loader.loadNames()).names;
  }

  async getStarRecord(layer: number, pix: number, index: number): Promise<CatalogStarRecord | null> {
    await this.ready;
    return this.loader ? this.loader.getStarRecord(layer, pix, index) : null;
  }

  /** Scene direction (unit vector) of a catalog position, for anchoring labels to stars. */
  sceneDirection(raDeg: number, decDeg: number, target = new THREE.Vector3()): THREE.Vector3 {
    return raDecToSceneDirection(raDeg, decDeg, target);
  }

  dispose(): void {
    this.disposed = true;
    this.loader?.dispose();
    for (const key of [...this.tiles.keys()]) this.removeTile(key);
    this.material.dispose();
  }

  private addTile(tile: CatalogStarCoreTile): void {
    if (this.disposed) return;
    const key = formatTileKey(tile.layer, tile.pix);
    if (this.tiles.has(key)) return;
    const geometry = buildTileBufferGeometry(tile, this.tint);
    const points = this.mounts.map((mount) => {
      const p = createTilePoints(geometry, this.material);
      mount.add(p);
      return p;
    });
    this.tiles.set(key, { geometry, points });
  }

  private removeTile(key: string): void {
    const entry = this.tiles.get(key);
    if (!entry) return;
    disposeTileMeshEntry(entry);
    this.tiles.delete(key);
  }
}
