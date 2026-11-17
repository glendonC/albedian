export const EARTH_RADIUS_M = 6_371_000;
export const EARTH_HEIGHT_OFFSET_M = 11_000;
export const EARTH_HEIGHT_TEX_WIDTH = 4096;
export const EARTH_HEIGHT_TEX_HEIGHT = 2048;
export const EARTH_NORTH_STEP_M = (Math.PI / EARTH_HEIGHT_TEX_HEIGHT) * EARTH_RADIUS_M;
export const ETOPO_HIGH_BYTE_SCALE = 65_280;
export const ETOPO_LOW_BYTE_SCALE = 255;
export const ICE_SHELF_MIN_RG_LOW = 0.15;
export const ICE_SHELF_MIN_RG_HIGH = 0.35;
export const EARTH_MIN_COS_LAT = 0.05;

/**
 * Decodes a normalized `[0, 1]` `(r, g)` sample from `earth_height_etopo2022_4k.png`
 * (`round(height_m + 11000)` split across high byte `R` and low byte `G`) to metres above the
 * EGM2008 geoid: `r * 65280 + g * 255 - 11000`.
 */
export function decodeEtopoHeightMetres(r: number, g: number): number {
  return r * ETOPO_HIGH_BYTE_SCALE + g * ETOPO_LOW_BYTE_SCALE - EARTH_HEIGHT_OFFSET_M;
}

/**
 * Computes the open-water fraction from `earth_water_natural_earth_4k.png` and the linear RGB
 * sample of `earth_day_bluemarble_2004-03_base_8k.jpg`. Suppressing water where `min(r, g)` is
 * bright keeps sun glint and ocean tint off Antarctica's floating ice shelves.
 */
export function openWaterFraction(
  waterMask: number,
  dayLinearR: number,
  dayLinearG: number,
  _dayLinearB?: number,
): number {
  const minRg = Math.min(dayLinearR, dayLinearG);
  const t = Math.max(
    0,
    Math.min(1, (minRg - ICE_SHELF_MIN_RG_LOW) / (ICE_SHELF_MIN_RG_HIGH - ICE_SHELF_MIN_RG_LOW)),
  );
  const iceShelf = t * t * (3 - 2 * t);
  return waterMask * (1 - iceShelf);
}

/**
 * Computes the tangent-space unit normal `(east, north, up)` at true slopes from four cardinal
 * ETOPO 2022 height samples (in metres) on the `4096x2048` equirectangular grid. Where
 * `waterMask > 0`, ETOPO stores lake or sea floor, so the slope is scaled by `1 - waterMask`.
 */
export function earthTangentNormal(
  hWestM: number,
  hEastM: number,
  hSouthM: number,
  hNorthM: number,
  sinLat: number,
  waterMask: number,
): [number, number, number] {
  const land = Math.max(0, Math.min(1, 1 - waterMask));
  const cosLat = Math.max(EARTH_MIN_COS_LAT, Math.sqrt(Math.max(0, 1 - sinLat * sinLat)));
  const twoDEastM = 2 * EARTH_NORTH_STEP_M * cosLat;
  const twoDNorthM = 2 * EARTH_NORTH_STEP_M;
  const nx = -((hEastM - hWestM) / twoDEastM) * land;
  const ny = -((hNorthM - hSouthM) / twoDNorthM) * land;
  const nz = 1;
  const len = Math.hypot(nx, ny, nz);
  return [nx / len, ny / len, nz / len];
}
