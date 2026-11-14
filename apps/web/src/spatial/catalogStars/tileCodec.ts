/**
 * Decoder for catalog star tiles (tile header version 1: core, detail and astro kinds), the
 * browser half of the codec in `services/science/catalog_tiles.py`. Values decoded here are
 * catalog data, never measurements from a user's recording.
 */

export {
  TILE_KIND_ASTRO,
  TILE_KIND_CORE,
  TILE_KIND_DETAIL,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
  type CatalogStarTileHeader,
} from './codec/tileTypes.js';
export { readTileHeader } from './codec/tileHeader.js';
export {
  decodeColorIndex,
  decodeDistanceBounds,
  decodeDistancePc,
  decodeExtinctionMag,
  decodeLuminositySolar,
  decodeMagnitude,
  decodeTeffK,
  halfToFloat,
} from './codec/scalarDecoders.js';
export {
  catalogIdAt,
  catalogOf,
  distanceKindOf,
  isNamed,
  magnitudeBandOf,
  usesBMinusV,
} from './codec/starFlags.js';
export { decodeAstroTile, decodeCoreTile, decodeDetailTile } from './codec/binaryDecoders.js';
