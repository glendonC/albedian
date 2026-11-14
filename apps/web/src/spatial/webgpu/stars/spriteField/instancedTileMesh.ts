import {
  BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  MathUtils,
  Mesh,
  Sphere,
  Vector3,
  type NodeMaterial,
} from 'three/webgpu';
import { ICRS_TO_SCENE_QUATERNION } from '../../../catalogStars/CatalogStarField.js';
import { tileBoundingRadius, tileCenter } from '../../../catalogStars/healpix.js';
import type { CatalogStarCoreTile } from '../../../catalogStars/tileCodec.js';
import { PSF_STAR_ATTRIBUTES } from '../psfStarMaterial.js';
import type { StarColourTable } from '../starColor.js';
import { tileRadiance } from './tileRadiance.js';

export const CATALOG_STAR_SPRITE_MOUNT_NAME = 'catalogStarSprites';
export const CATALOG_STAR_TILE_MESH_NAME = 'catalogStarTile';
export const CATALOG_STAR_TILE_RENDER_ORDER = -10;

/** Largest sprite radius (220 reference px, psfStarMaterial.ts) as an angle at 56 degrees FOV. */
export const SPRITE_MARGIN_RAD = MathUtils.degToRad((220 / 1080) * 56);

export const QUAD_POSITIONS = new BufferAttribute(
  new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]),
  3
);
export const QUAD_INDEX = new BufferAttribute(new Uint16Array([0, 1, 2, 0, 2, 3]), 1);

export interface TileSpriteEntry {
  geometry: InstancedBufferGeometry;
  meshes: Mesh[];
}

export function formatTileKey(layer: number, pix: number): string {
  return `${layer}/${pix}`;
}

export function createCatalogStarMountGroup(radius: number): Group {
  const group = new Group();
  group.name = CATALOG_STAR_SPRITE_MOUNT_NAME;
  group.quaternion.copy(ICRS_TO_SCENE_QUATERNION);
  group.scale.setScalar(radius);
  return group;
}

export function createTileSpriteMesh(geometry: InstancedBufferGeometry, material: NodeMaterial): Mesh {
  const mesh = new Mesh(geometry, material);
  mesh.name = CATALOG_STAR_TILE_MESH_NAME;
  // Additive and depth-tested behind every opaque body; draw order among stars is irrelevant
  mesh.renderOrder = CATALOG_STAR_TILE_RENDER_ORDER;
  return mesh;
}

export function buildTileInstancedGeometry(
  tile: CatalogStarCoreTile,
  colours: StarColourTable
): InstancedBufferGeometry {
  const geometry = new InstancedBufferGeometry();
  geometry.setAttribute('position', QUAD_POSITIONS);
  geometry.setIndex(QUAD_INDEX);
  geometry.setAttribute(PSF_STAR_ATTRIBUTES.direction, new InstancedBufferAttribute(tile.directions, 3));
  geometry.setAttribute(PSF_STAR_ATTRIBUTES.radiance, new InstancedBufferAttribute(tileRadiance(tile, colours), 3));
  geometry.setAttribute(PSF_STAR_ATTRIBUTES.magnitude, new InstancedBufferAttribute(tile.magnitude, 1));
  geometry.instanceCount = tile.count;
  // Frustum culling per tile: the tile's cone on the unit sphere, widened by the largest sprite
  const center = tileCenter(tile.order, tile.pix);
  const angular = Math.min(Math.PI, tileBoundingRadius(tile.order, tile.pix) + SPRITE_MARGIN_RAD);
  geometry.boundingSphere = new Sphere(new Vector3(center[0], center[1], center[2]), 2 * Math.sin(angular / 2));
  return geometry;
}

export function disposeTileSpriteEntry(entry: TileSpriteEntry): void {
  for (const mesh of entry.meshes) mesh.removeFromParent();
  entry.geometry.dispose();
}
