import {
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
  type CatalogStarCatalogId,
  type CatalogStarDistanceKind,
} from '@albedian/contracts';
import type { CatalogStarDetailTile } from './tileTypes.js';

const DISTANCE_KINDS: readonly CatalogStarDistanceKind[] = [
  'none',
  'bailer-jones-2021-photogeometric',
  'bailer-jones-2021-geometric',
  'hipparcos-2007-parallax-inverse',
];

export function distanceKindOf(flags: number): CatalogStarDistanceKind {
  return DISTANCE_KINDS[flags & ENC.flags.distanceKindMask] ?? 'none';
}

export function catalogOf(flags: number): CatalogStarCatalogId {
  return flags & ENC.flags.catalogHipparcos ? 'hipparcos-2007' : 'gaia-dr3';
}

export function isNamed(flags: number): boolean {
  return (flags & ENC.flags.named) !== 0;
}

export function usesBMinusV(flags: number): boolean {
  return (flags & ENC.flags.colorBMinusV) !== 0;
}

export function magnitudeBandOf(flags: number): 'G' | 'Hp' {
  return flags & ENC.flags.magnitudeBandHp ? 'Hp' : 'G';
}

/** Gaia DR3 source_id or HIP number as a decimal string (source_ids exceed 2^53). */
export function catalogIdAt(detail: CatalogStarDetailTile, index: number): string {
  const hi = BigInt(detail.idHi[index] ?? 0);
  const lo = BigInt(detail.idLo[index] ?? 0);
  return ((hi << 32n) | lo).toString();
}
