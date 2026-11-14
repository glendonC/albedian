import type { CatalogStarLoaderStats } from '../CatalogStarTileLoader.js';

/** Pinned catalog release; switching to Gaia DR4 means baking `gaia-dr4` and changing this. */
export const PINNED_STAR_CATALOG_RELEASE = 'gaia-dr3';
export const STAR_CATALOG_BASE_URL = `/catalog/stars/${PINNED_STAR_CATALOG_RELEASE}/`;
/** Naked-eye limit under a dark sky; the Unveil gesture moves this toward the catalog limit. */
export const DEFAULT_LIMITING_MAGNITUDE = 6.5;

/** Brightest clamp floor accepted by `setLimitingMagnitude`. */
export const MIN_LIMITING_MAGNITUDE = -2;
/** Fallback faint ceiling used before the catalog manifest resolves. */
export const DEFAULT_MAX_LIMITING_MAGNITUDE = 12;

export type CatalogStarFieldStatus = 'loading' | 'ready' | 'unavailable';

export interface CatalogStarFieldStats extends Partial<CatalogStarLoaderStats> {
  status: CatalogStarFieldStatus;
  limitingMagnitude: number;
}

export interface CatalogStarFieldOptions {
  baseUrl?: string;
  limitingMagnitude?: number;
  fetch?: typeof fetch;
}

export function clampLimitingMagnitude(mag: number, ceiling = DEFAULT_MAX_LIMITING_MAGNITUDE): number {
  return Math.max(MIN_LIMITING_MAGNITUDE, Math.min(ceiling, mag));
}
