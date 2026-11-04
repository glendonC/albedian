import * as THREE from 'three';
import type { CatalogStarName, CatalogStarTileManifest } from '@albedian/contracts';
import {
  CatalogStarTileLoader,
  type CatalogStarLoaderStats,
  type CatalogStarRecord,
} from './CatalogStarTileLoader.js';
import { B_MINUS_V_TO_BP_RP, catalogStarTint } from './catalogStarTint.js';
import { raDecDegToVec, tileBoundingRadius, tileCenter, type Vec3 } from './healpix.js';
import { usesBMinusV, type CatalogStarCoreTile } from './tileCodec.js';

/** Pinned catalog release; switching to Gaia DR4 means baking `gaia-dr4` and changing this. */
export const PINNED_STAR_CATALOG_RELEASE = 'gaia-dr3';
export const STAR_CATALOG_BASE_URL = `/catalog/stars/${PINNED_STAR_CATALOG_RELEASE}/`;
/** Naked-eye limit under a dark sky; the Unveil gesture moves this toward the catalog limit. */
export const DEFAULT_LIMITING_MAGNITUDE = 6.5;

/**
 * ICRS axes (x toward RA 0h on the equator, z toward the north celestial pole) to scene axes:
 * +Y is celestial north, -Z faces RA 0h, +X faces RA 18h, so east sits to the left of a
 * viewer facing the equator, as on the real sky.
 */
export const ICRS_TO_SCENE_QUATERNION = new THREE.Quaternion().setFromRotationMatrix(
  new THREE.Matrix4().set(0, -1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0, 0, 0, 1)
);

export function icrsToScene(v: Vec3, target = new THREE.Vector3()): THREE.Vector3 {
  return target.set(v[0], v[1], v[2]).applyQuaternion(ICRS_TO_SCENE_QUATERNION);
}

export type CatalogStarFieldStatus = 'loading' | 'ready' | 'unavailable';

export interface CatalogStarFieldStats extends Partial<CatalogStarLoaderStats> {
  status: CatalogStarFieldStatus;
  limitingMagnitude: number;
}

const TINT_LUT_SIZE = 512;
const TINT_LUT_MIN = -0.6;
const TINT_LUT_MAX = 3.0;

function buildTintLut(): { lut: Float32Array; neutral: [number, number, number] } {
  const color = new THREE.Color();
  const lut = new Float32Array(TINT_LUT_SIZE * 3);
  for (let i = 0; i < TINT_LUT_SIZE; i++) {
    const bpRp = TINT_LUT_MIN + ((TINT_LUT_MAX - TINT_LUT_MIN) * i) / (TINT_LUT_SIZE - 1);
    const [r, g, b] = catalogStarTint(bpRp, false);
    color.setRGB(r, g, b, THREE.SRGBColorSpace);
    lut[3 * i] = color.r;
    lut[3 * i + 1] = color.g;
    lut[3 * i + 2] = color.b;
  }
  const [r, g, b] = catalogStarTint(Number.NaN, false);
  color.setRGB(r, g, b, THREE.SRGBColorSpace);
  return { lut, neutral: [color.r, color.g, color.b] };
}

function createCatalogStarMaterial(limitingMagnitude: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uLimitingMagnitude: { value: limitingMagnitude },
      uPixelRatio: { value: 1 },
    },
    vertexShader: /* glsl */ `
      attribute float aMagnitude;
      attribute vec3 aTint;
      uniform float uLimitingMagnitude;
      uniform float uPixelRatio;
      varying vec3 vTint;
      varying float vAlpha;
      varying float vGain;
      varying float vSpike;
      void main() {
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        // Catalog stars fainter than the limiting magnitude fade out over its last 0.3 mag
        float fade = clamp((uLimitingMagnitude - aMagnitude) / 0.3, 0.0, 1.0);
        // Size, opacity and gain follow catalog flux relative to a 6th magnitude star:
        // fainter stars dim, brighter stars grow and run hot enough to bloom
        float rel = pow(10.0, -0.4 * (aMagnitude - 6.0));
        vAlpha = clamp(sqrt(rel), 0.05, 1.0) * fade;
        vGain = clamp(pow(rel, 0.18), 1.0, 3.5);
        vTint = aTint;
        vSpike = aMagnitude < 1.0 ? 1.0 : 0.0;
        float size = clamp(1.9 * pow(rel, 0.3), 1.5, 22.0) * mix(1.0, 2.2, vSpike);
        gl_PointSize = fade > 0.0 ? size * uPixelRatio : 0.0;
      }
    `,
    fragmentShader: /* glsl */ `
      varying vec3 vTint;
      varying float vAlpha;
      varying float vGain;
      varying float vSpike;
      void main() {
        vec2 uv = gl_PointCoord * 2.0 - 1.0;
        float d = length(uv);
        if (d > 1.0) discard;
        // Spike sprites are drawn larger; scale the radius so the core keeps its size
        float r = d * mix(1.0, 2.2, vSpike);
        float core = exp(-r * r * 3.2);
        float halo = exp(-r * 3.0) * 0.3;
        float spikes = 0.0;
        if (vSpike > 0.5) {
          float sx = exp(-abs(uv.y) * 42.0) * exp(-abs(uv.x) * 3.8);
          float sy = exp(-abs(uv.x) * 42.0) * exp(-abs(uv.y) * 3.8);
          spikes = (sx + sy) * 0.55;
        }
        float intensity = (core + halo + spikes) * vAlpha * vGain;
        vec3 col = mix(vTint, vec3(1.0), clamp(core * 0.6, 0.0, 1.0)) * intensity;
        // Additive blending multiplies by alpha, so intensity lives in rgb only (applying it
        // twice would square it and erase faint stars); values above 1 feed the bloom pass
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
}

/**
 * Real catalog stars for the WebGL sky: streams Gaia DR3 / Hipparcos 2007 tiles for the
 * camera's view and limiting magnitude and draws them at their catalog directions.
 * Each mount is a sky sphere of a given radius that shares the same tile geometry.
 */
export class CatalogStarField {
  private loader: CatalogStarTileLoader | null = null;
  private status: CatalogStarFieldStatus = 'loading';
  private readonly mounts: THREE.Group[] = [];
  private readonly tiles = new Map<string, { geometry: THREE.BufferGeometry; points: THREE.Points[] }>();
  private readonly material: THREE.ShaderMaterial;
  private readonly tint = buildTintLut();
  private limitingMagnitude: number;
  private readonly lastView = { dir: new THREE.Vector3(), radius: -1, limit: Number.NaN, at: Number.NEGATIVE_INFINITY };
  private readonly tmpDir = new THREE.Vector3();
  private readonly tmpQuat = new THREE.Quaternion();
  private disposed = false;
  readonly ready: Promise<void>;

  constructor(options: { baseUrl?: string; limitingMagnitude?: number; fetch?: typeof fetch } = {}) {
    this.limitingMagnitude = options.limitingMagnitude ?? DEFAULT_LIMITING_MAGNITUDE;
    this.material = createCatalogStarMaterial(this.limitingMagnitude);
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
        this.limitingMagnitude = Math.min(this.limitingMagnitude, loader.manifest.magnitudeLimit);
        this.material.uniforms.uLimitingMagnitude!.value = this.limitingMagnitude;
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
    const group = new THREE.Group();
    group.name = 'catalogStarField';
    group.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
    group.scale.setScalar(radius);
    for (const entry of this.tiles.values()) {
      const points = this.makePoints(entry.geometry);
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
    const ceiling = this.loader?.manifest.magnitudeLimit ?? 12;
    this.limitingMagnitude = Math.max(-2, Math.min(ceiling, mag));
    this.material.uniforms.uLimitingMagnitude!.value = this.limitingMagnitude;
    this.lastView.at = Number.NEGATIVE_INFINITY;
  }

  /** Call once per frame: feeds the camera's ICRS view cone to the tile loader. */
  update(camera: THREE.PerspectiveCamera, pixelRatio: number, nowMs: number = performance.now()): void {
    this.material.uniforms.uPixelRatio!.value = pixelRatio;
    if (!this.loader) return;
    const mount = this.mounts.find((m) => m.parent?.visible !== false) ?? this.mounts[0];
    camera.getWorldDirection(this.tmpDir);
    if (mount) {
      this.tmpDir.applyQuaternion(mount.getWorldQuaternion(this.tmpQuat).invert());
    } else {
      this.tmpDir.applyQuaternion(this.tmpQuat.copy(ICRS_TO_SCENE_QUATERNION).invert());
    }
    const halfDiagonal = Math.atan(
      Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * Math.hypot(1, camera.aspect)
    );
    const radius = halfDiagonal + 0.08;
    const moved = this.lastView.dir.angleTo(this.tmpDir);
    const changed =
      moved > 0.01 ||
      Math.abs(radius - this.lastView.radius) > 0.01 ||
      this.limitingMagnitude !== this.lastView.limit;
    if (!changed || nowMs - this.lastView.at < 120) return;
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
    return icrsToScene(raDecDegToVec(raDeg, decDeg), target);
  }

  dispose(): void {
    this.disposed = true;
    this.loader?.dispose();
    for (const key of [...this.tiles.keys()]) this.removeTile(key);
    this.material.dispose();
  }

  private makePoints(geometry: THREE.BufferGeometry): THREE.Points {
    const points = new THREE.Points(geometry, this.material);
    points.name = 'catalogStarTile';
    return points;
  }

  private addTile(tile: CatalogStarCoreTile): void {
    if (this.disposed) return;
    const key = `${tile.layer}/${tile.pix}`;
    if (this.tiles.has(key)) return;
    const tints = new Float32Array(3 * tile.count);
    const { lut, neutral } = this.tint;
    for (let i = 0; i < tile.count; i++) {
      let index = tile.colorIndex[i] ?? Number.NaN;
      if (usesBMinusV(tile.flags[i] ?? 0)) index *= B_MINUS_V_TO_BP_RP;
      if (Number.isFinite(index)) {
        const t = (index - TINT_LUT_MIN) / (TINT_LUT_MAX - TINT_LUT_MIN);
        const j = 3 * Math.round(Math.max(0, Math.min(1, t)) * (TINT_LUT_SIZE - 1));
        tints[3 * i] = lut[j] ?? 1;
        tints[3 * i + 1] = lut[j + 1] ?? 1;
        tints[3 * i + 2] = lut[j + 2] ?? 1;
      } else {
        tints.set(neutral, 3 * i);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(tile.directions, 3));
    geometry.setAttribute('aMagnitude', new THREE.BufferAttribute(tile.magnitude, 1));
    geometry.setAttribute('aTint', new THREE.BufferAttribute(tints, 3));
    const center = tileCenter(tile.order, tile.pix);
    const radius = tileBoundingRadius(tile.order, tile.pix);
    geometry.boundingSphere = new THREE.Sphere(
      new THREE.Vector3(center[0], center[1], center[2]),
      2 * Math.sin(Math.min(radius, Math.PI) / 2)
    );
    const points = this.mounts.map((mount) => {
      const p = this.makePoints(geometry);
      mount.add(p);
      return p;
    });
    this.tiles.set(key, { geometry, points });
  }

  private removeTile(key: string): void {
    const entry = this.tiles.get(key);
    if (!entry) return;
    for (const p of entry.points) p.removeFromParent();
    entry.geometry.dispose();
    this.tiles.delete(key);
  }
}
