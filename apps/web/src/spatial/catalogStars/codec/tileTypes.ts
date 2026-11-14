import { CATALOG_STAR_TILE_ASTRO_ENCODING as ASTRO } from '@albedian/contracts';

/**
 * Tile kind tags, per-star column byte widths, and decoded tile interfaces for catalog star tiles
 * (tile header version 1: core, detail and astro kinds), mirroring `services/science/catalog_tiles.py`.
 * Values represented here are catalog data, never measurements from a user's recording.
 */

export const TILE_KIND_CORE = 1;
export const TILE_KIND_DETAIL = 2;
export const TILE_KIND_ASTRO = ASTRO.kind;

/** Non-position bytes per star in a core tile: mag (u16) + color (i16) + distance (u16) + flags (u8). */
export const CORE_FIXED_BYTES_PER_STAR = 2 + 2 + 2 + 1;

/** Bytes per star in a detail tile: idHi (u32) + idLo (u32) + pmRa (f16) + pmDec (f16) + rv (f16) + bounds (2 * u8). */
export const DETAIL_BYTES_PER_STAR = 4 + 4 + 2 + 2 + 2 + 1 + 1;

export interface ExpectedTileCoordinates {
  layer: number;
  order: number;
  pix: number;
}

export interface CatalogStarTileHeader {
  kind: number;
  positionBits: number;
  layer: number;
  order: number;
  pix: number;
  count: number;
}

/** Render-side columns of one tile. Index `i` lines up with the matching detail tile. */
export interface CatalogStarCoreTile {
  layer: number;
  order: number;
  pix: number;
  count: number;
  positionBits: number;
  /** ICRS unit vectors at the catalog epoch, xyz per star */
  directions: Float32Array;
  magnitude: Float32Array;
  /** BP-RP for Gaia DR3 entries, B-V for Hipparcos entries (see flags); NaN when missing */
  colorIndex: Float32Array;
  /** Distance median in parsecs; NaN when the catalog gives none */
  distancePc: Float32Array;
  flags: Uint8Array;
  byteLength: number;
}

/** Catalog identity, kinematics and distance bounds, fetched only when a star is inspected. */
export interface CatalogStarDetailTile {
  layer: number;
  order: number;
  pix: number;
  count: number;
  idHi: Uint32Array;
  idLo: Uint32Array;
  pmRaHalf: Uint16Array;
  pmDecHalf: Uint16Array;
  radialVelocityHalf: Uint16Array;
  distanceLowerCode: Uint8Array;
  distanceUpperCode: Uint8Array;
  byteLength: number;
}

/**
 * Gaia DR3 astrophysics and astrometric quality for the stars of one tile, index-aligned with
 * its core tile: GSP-Phot effective temperature and extinction (A0 at 547.7 nm, and AG), FLAME
 * luminosity, each with its 16th and 84th percentiles, RUWE and the parallax standard error.
 * NaN where Gaia DR3 gives no value (and for every Hipparcos entry); nothing is filled in.
 */
export interface CatalogStarAstroTile {
  layer: number;
  order: number;
  pix: number;
  count: number;
  teffK: Float32Array;
  teffLowerK: Float32Array;
  teffUpperK: Float32Array;
  luminositySolar: Float32Array;
  luminosityLowerSolar: Float32Array;
  luminosityUpperSolar: Float32Array;
  a0Mag: Float32Array;
  a0LowerMag: Float32Array;
  a0UpperMag: Float32Array;
  agMag: Float32Array;
  ruwe: Float32Array;
  parallaxErrorMas: Float32Array;
  byteLength: number;
}
