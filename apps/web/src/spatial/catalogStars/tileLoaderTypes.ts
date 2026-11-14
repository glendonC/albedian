import type {
  CatalogStarCatalogId,
  CatalogStarDistanceKind,
  CatalogStarName,
} from '@albedian/contracts';
import type { Vec3 } from './healpix.js';
import type { CatalogStarCoreTile } from './tileCodec.js';

/**
 * Public contracts and shared constants for streaming catalog star tiles.
 *
 * Everything described here is catalog data (`evidenceKind: 'catalog-derived'`),
 * never stars measured in a user's recording.
 */

export const DEFAULT_MAX_CONCURRENT_TILE_FETCHES = 6;
export const DEFAULT_MAX_RESIDENT_STARS = 1_500_000;
export const MAX_RECENT_TILE_TIMINGS = 64;
export const TILE_RETRY_AFTER_MS = 15_000;

export type CatalogTileKind = 'core' | 'detail' | 'astro';

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

export interface PlannedCatalogTile {
  key: CatalogStarTileKey;
  distance: number;
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

export function formatTileKey(layer: number, pix: number): string {
  return `${layer}/${pix}`;
}

export function normalizeCatalogBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
}

export function resolveCatalogTileUrl(
  baseUrl: string,
  tilePathTemplate: string,
  layer: number,
  pix: number,
  kind: CatalogTileKind
): string {
  const path = tilePathTemplate
    .replace('{layer}', String(layer))
    .replace('{pix}', String(pix))
    .replace('{kind}', kind);
  return `${baseUrl}${path}`;
}
