import {
  BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  MathUtils,
  Mesh,
  Quaternion,
  Sphere,
  Vector3,
  type PerspectiveCamera,
} from 'three/webgpu';
import type { CatalogStarTileManifest } from '@albedian/contracts';
import {
  DEFAULT_LIMITING_MAGNITUDE,
  ICRS_TO_SCENE_QUATERNION,
  STAR_CATALOG_BASE_URL,
  type CatalogStarFieldStats,
  type CatalogStarFieldStatus,
} from '../../catalogStars/CatalogStarField.js';
import { CatalogStarTileLoader } from '../../catalogStars/CatalogStarTileLoader.js';
import { tileBoundingRadius, tileCenter } from '../../catalogStars/healpix.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../../catalogStars/tileCodec.js';
import { fluxFromMagnitude } from './blackbody.js';
import { createPsfStarMaterial, PSF_STAR_ATTRIBUTES, type PsfStarMaterial } from './psfStarMaterial.js';
import { StarColourTable } from './starColor.js';

/**
 * Linear exposure of the catalog sprites: the HDR energy of a magnitude 0 star, in reference
 * pixels. At 650 a magnitude 6 star (flux 10^-2.4) peaks at 2.59 x 0.9 x 0.456 x 1.19 = 1.27 in
 * linear radiance at its centre, against 1.3 at the centre of the WebGL point sprite for the
 * same star, so the naked-eye sky keeps its brightness across renderers.
 */
export const DEFAULT_CATALOG_STAR_EXPOSURE = 650;

/** Largest sprite radius (220 reference px, psfStarMaterial.ts) as an angle at 56 degrees FOV. */
const SPRITE_MARGIN_RAD = MathUtils.degToRad((220 / 1080) * 56);

const QUAD_POSITIONS = new BufferAttribute(new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]), 3);
const QUAD_INDEX = new BufferAttribute(new Uint16Array([0, 1, 2, 0, 2, 3]), 1);

/** Star radiance per tile: unit-luminance colour from the colour index times 10^(-0.4 m). */
export function tileRadiance(tile: CatalogStarCoreTile, colours: StarColourTable): Float32Array {
  const radiance = new Float32Array(tile.count * 3);
  for (let i = 0; i < tile.count; i++) {
    colours.sampleInto(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0), radiance, i * 3);
    const flux = fluxFromMagnitude(tile.magnitude[i] ?? Number.POSITIVE_INFINITY);
    radiance[i * 3] = radiance[i * 3]! * flux;
    radiance[i * 3 + 1] = radiance[i * 3 + 1]! * flux;
    radiance[i * 3 + 2] = radiance[i * 3 + 2]! * flux;
  }
  return radiance;
}

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
  private readonly tiles = new Map<string, { geometry: InstancedBufferGeometry; meshes: Mesh[] }>();
  private readonly psf: PsfStarMaterial;
  private readonly colours = new StarColourTable();
  private limitingMagnitude: number;
  private readonly lastView = { dir: new Vector3(), radius: -1, limit: Number.NaN, at: Number.NEGATIVE_INFINITY };
  private readonly tmpDir = new Vector3();
  private readonly tmpQuat = new Quaternion();
  private disposed = false;
  readonly ready: Promise<void>;

  constructor(
    options: { baseUrl?: string; limitingMagnitude?: number; exposure?: number; fetch?: typeof fetch } = {}
  ) {
    this.limitingMagnitude = options.limitingMagnitude ?? DEFAULT_LIMITING_MAGNITUDE;
    this.psf = createPsfStarMaterial({
      exposure: options.exposure ?? DEFAULT_CATALOG_STAR_EXPOSURE,
      limitingMagnitude: this.limitingMagnitude,
    });
    this.ready = CatalogStarTileLoader.open(options.baseUrl ?? STAR_CATALOG_BASE_URL, {
      ...(options.fetch ? { fetch: options.fetch } : {}),
      onTileLoaded: (tile) => this.addTile(tile),
      onTileEvicted: (key) => this.removeTile(`${key.layer}/${key.pix}`),
      onError: (error, key) => console.warn(`[catalog stars] tile ${key.layer}/${key.pix} failed`, error),
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
    const group = new Group();
    group.name = 'catalogStarSprites';
    group.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
    group.scale.setScalar(radius);
    for (const entry of this.tiles.values()) {
      const mesh = this.makeMesh(entry.geometry);
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
    const ceiling = this.loader?.manifest.magnitudeLimit ?? 12;
    this.limitingMagnitude = Math.max(-2, Math.min(ceiling, mag));
    this.psf.limitingMagnitude.value = this.limitingMagnitude;
    this.lastView.at = Number.NEGATIVE_INFINITY;
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
    this.psf.psfScale.value = drawingBufferHeight / 1080;
    if (!this.loader) return;
    const mount = this.mounts.find((m) => m.parent?.visible !== false) ?? this.mounts[0];
    camera.getWorldDirection(this.tmpDir);
    if (mount) {
      this.tmpDir.applyQuaternion(mount.getWorldQuaternion(this.tmpQuat).invert());
    } else {
      this.tmpDir.applyQuaternion(this.tmpQuat.copy(ICRS_TO_SCENE_QUATERNION).invert());
    }
    const halfDiagonal = Math.atan(Math.tan(MathUtils.degToRad(camera.fov) / 2) * Math.hypot(1, camera.aspect));
    const radius = halfDiagonal + 0.08;
    const moved = this.lastView.dir.angleTo(this.tmpDir);
    const changed =
      moved > 0.01 || Math.abs(radius - this.lastView.radius) > 0.01 || this.limitingMagnitude !== this.lastView.limit;
    if (!force && (!changed || nowMs - this.lastView.at < 120)) return;
    this.lastView.dir.copy(this.tmpDir);
    this.lastView.radius = radius;
    this.lastView.limit = this.limitingMagnitude;
    this.lastView.at = nowMs;
    this.loader.setView({
      centerIcrs: [this.tmpDir.x, this.tmpDir.y, this.tmpDir.z],
      radiusRad: radius,
      limitingMagnitude: this.limitingMagnitude,
    });
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

  private makeMesh(geometry: InstancedBufferGeometry): Mesh {
    const mesh = new Mesh(geometry, this.psf.material);
    mesh.name = 'catalogStarTile';
    // Additive and depth-tested behind every opaque body; draw order among stars is irrelevant
    mesh.renderOrder = -10;
    return mesh;
  }

  private addTile(tile: CatalogStarCoreTile): void {
    if (this.disposed) return;
    const key = `${tile.layer}/${tile.pix}`;
    if (this.tiles.has(key)) return;
    const geometry = new InstancedBufferGeometry();
    geometry.setAttribute('position', QUAD_POSITIONS);
    geometry.setIndex(QUAD_INDEX);
    geometry.setAttribute(PSF_STAR_ATTRIBUTES.direction, new InstancedBufferAttribute(tile.directions, 3));
    geometry.setAttribute(PSF_STAR_ATTRIBUTES.radiance, new InstancedBufferAttribute(tileRadiance(tile, this.colours), 3));
    geometry.setAttribute(PSF_STAR_ATTRIBUTES.magnitude, new InstancedBufferAttribute(tile.magnitude, 1));
    geometry.instanceCount = tile.count;
    // Frustum culling per tile: the tile's cone on the unit sphere, widened by the largest sprite
    const center = tileCenter(tile.order, tile.pix);
    const angular = Math.min(Math.PI, tileBoundingRadius(tile.order, tile.pix) + SPRITE_MARGIN_RAD);
    geometry.boundingSphere = new Sphere(new Vector3(center[0], center[1], center[2]), 2 * Math.sin(angular / 2));
    const meshes = this.mounts.map((mount) => {
      const mesh = this.makeMesh(geometry);
      mount.add(mesh);
      return mesh;
    });
    this.tiles.set(key, { geometry, meshes });
  }

  private removeTile(key: string): void {
    const entry = this.tiles.get(key);
    if (!entry) return;
    for (const mesh of entry.meshes) mesh.removeFromParent();
    entry.geometry.dispose();
    this.tiles.delete(key);
  }
}
