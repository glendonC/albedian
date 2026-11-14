/**
 * Stable star source identifiers, cached HEALPix tile bounding cones and raw tile row readers.
 */

import type { CatalogStarCoreTile } from '../../spatial/catalogStars/tileCodec.js';
import { tileBoundingRadius, tileCenter } from '../../spatial/catalogStars/healpix.js';
import type { Vec3 } from '../voicePlanning.js';

export interface TileBoundingCone {
  readonly center: Vec3;
  readonly radius: number;
}

export function skySourceId(releaseId: string, layer: number, pix: number, index: number): string {
  return `${releaseId}:L${layer}/${pix}#${index}`;
}

export function finiteOrNull(value: number | undefined): number | null {
  return value !== undefined && Number.isFinite(value) ? value : null;
}

export function starDirectionAt(directions: ArrayLike<number>, index: number): Vec3 {
  const base = 3 * index;
  return [
    directions[base] ?? Number.NaN,
    directions[base + 1] ?? Number.NaN,
    directions[base + 2] ?? Number.NaN,
  ];
}

const tileGeometry = new Map<string, TileBoundingCone>();

export function geometryOf(core: CatalogStarCoreTile): TileBoundingCone {
  const key = `${core.order}/${core.pix}`;
  let geometry = tileGeometry.get(key);
  if (!geometry) {
    geometry = { center: tileCenter(core.order, core.pix), radius: tileBoundingRadius(core.order, core.pix) };
    tileGeometry.set(key, geometry);
  }
  return geometry;
}
