import type { CatalogStarTileLayer, CatalogStarTileManifest } from '@albedian/contracts';
import { angleBetween, tileBoundingRadius, tileCenter, type Vec3 } from './healpix.js';
import type { CatalogStarCoreTile } from './tileCodec.js';
import {
  formatTileKey,
  type CatalogStarTileKey,
  type CatalogStarView,
  type PlannedCatalogTile,
} from './tileLoaderTypes.js';

interface TileGeometry {
  center: Vec3;
  radius: number;
}

export interface EvictionCandidate {
  id: string;
  tile: CatalogStarCoreTile;
  distance: number;
}

export function selectLayersForMagnitude(
  layers: readonly CatalogStarTileLayer[],
  limitingMagnitude: number
): CatalogStarTileLayer[] {
  return layers.filter((layer) => (layer.magMin ?? Number.NEGATIVE_INFINITY) < limitingMagnitude);
}

/**
 * Plans which HEALPix catalog star tiles intersect a view cone and orders non-wanted resident
 * tiles for eviction when the resident star budget is exceeded.
 */
export class TileViewPlanner {
  private readonly geometry = new Map<number, TileGeometry[]>();

  constructor(private readonly manifest: CatalogStarTileManifest) {}

  layersFor(limitingMagnitude: number): CatalogStarTileLayer[] {
    return selectLayersForMagnitude(this.manifest.layers, limitingMagnitude);
  }

  plan(view: CatalogStarView): PlannedCatalogTile[] {
    const out: PlannedCatalogTile[] = [];
    for (const layer of this.layersFor(view.limitingMagnitude)) {
      const geo = this.geometryFor(layer);
      layer.tileStarCounts.forEach((count, pix) => {
        const tile = geo[pix];
        if (count === 0 || !tile) return;
        const distance = angleBetween(view.centerIcrs, tile.center);
        if (distance - tile.radius <= view.radiusRad) {
          out.push({ key: { layer: layer.layer, pix }, distance });
        }
      });
    }
    out.sort((a, b) => a.key.layer - b.key.layer || a.distance - b.distance);
    return out;
  }

  planKeys(view: CatalogStarView): CatalogStarTileKey[] {
    return this.plan(view).map((entry) => entry.key);
  }

  rankEvictionCandidates(
    loaded: ReadonlyMap<string, CatalogStarCoreTile>,
    wanted: readonly PlannedCatalogTile[],
    viewCenterIcrs: Vec3 | null
  ): EvictionCandidate[] {
    const wantedIds = new Set(wanted.map((w) => formatTileKey(w.key.layer, w.key.pix)));
    return [...loaded.entries()]
      .filter(([id]) => !wantedIds.has(id))
      .map(([id, tile]) => {
        const geo = this.tileGeometryAt(tile.layer, tile.pix);
        return {
          id,
          tile,
          distance: viewCenterIcrs && geo ? angleBetween(viewCenterIcrs, geo.center) : 0,
        };
      })
      .sort((a, b) => b.tile.layer - a.tile.layer || b.distance - a.distance);
  }

  private tileGeometryAt(layerIndex: number, pix: number): TileGeometry | undefined {
    const cached = this.geometry.get(layerIndex);
    if (cached) return cached[pix];
    const layer = this.manifest.layers[layerIndex];
    return layer ? this.geometryFor(layer)[pix] : undefined;
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
}
