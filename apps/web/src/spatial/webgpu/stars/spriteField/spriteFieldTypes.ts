/**
 * Linear exposure of the catalog sprites: the HDR energy of a magnitude 0 star, in reference
 * pixels. At 650 a magnitude 6 star (flux 10^-2.4) peaks at 2.59 x 0.9 x 0.456 x 1.19 = 1.27 in
 * linear radiance at its centre, against 1.3 at the centre of the WebGL point sprite for the
 * same star, so the naked-eye sky keeps its brightness across renderers.
 */
export const DEFAULT_CATALOG_STAR_EXPOSURE = 650;

export const MIN_LIMITING_MAGNITUDE = -2;
export const DEFAULT_MAX_LIMITING_MAGNITUDE = 12;

export interface CatalogStarSpriteFieldOptions {
  baseUrl?: string;
  limitingMagnitude?: number;
  exposure?: number;
  fetch?: typeof fetch;
}

export function clampLimitingMagnitude(
  mag: number,
  ceiling: number = DEFAULT_MAX_LIMITING_MAGNITUDE
): number {
  return Math.max(MIN_LIMITING_MAGNITUDE, Math.min(ceiling, mag));
}
