import * as THREE from 'three';
import { B_MINUS_V_TO_BP_RP, catalogStarTint } from '../catalogStarTint.js';
import { tileBoundingRadius, tileCenter } from '../healpix.js';
import { usesBMinusV, type CatalogStarCoreTile } from '../tileCodec.js';
import { ICRS_TO_SCENE_QUATERNION } from './icrsFrame.js';

export const TINT_LUT_SIZE = 512;
export const TINT_LUT_MIN = -0.6;
export const TINT_LUT_MAX = 3.0;

export const CATALOG_STAR_MOUNT_NAME = 'catalogStarField';
export const CATALOG_STAR_TILE_POINTS_NAME = 'catalogStarTile';

export interface StarTintLut {
  readonly lut: Float32Array;
  readonly neutral: [number, number, number];
}

export interface TileMeshEntry {
  readonly geometry: THREE.BufferGeometry;
  readonly points: THREE.Points[];
}

export function formatTileKey(layer: number, pix: number): string {
  return `${layer}/${pix}`;
}

export function buildTintLut(): StarTintLut {
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

export function packTileStarTints(tile: CatalogStarCoreTile, tint: StarTintLut): Float32Array {
  const tints = new Float32Array(3 * tile.count);
  const { lut, neutral } = tint;
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
  return tints;
}

export function buildTileBufferGeometry(tile: CatalogStarCoreTile, tint: StarTintLut): THREE.BufferGeometry {
  const tints = packTileStarTints(tile, tint);
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
  return geometry;
}

export function createCatalogStarMountGroup(radius: number): THREE.Group {
  const group = new THREE.Group();
  group.name = CATALOG_STAR_MOUNT_NAME;
  group.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
  group.scale.setScalar(radius);
  return group;
}

export function createTilePoints(geometry: THREE.BufferGeometry, material: THREE.ShaderMaterial): THREE.Points {
  const points = new THREE.Points(geometry, material);
  points.name = CATALOG_STAR_TILE_POINTS_NAME;
  return points;
}

export function disposeTileMeshEntry(entry: TileMeshEntry): void {
  for (const points of entry.points) points.removeFromParent();
  entry.geometry.dispose();
}
