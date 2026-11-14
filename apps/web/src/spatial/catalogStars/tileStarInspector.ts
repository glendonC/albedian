import type {
  CatalogStarCatalogId,
  CatalogStarDistanceKind,
  CatalogStarNameIndex,
} from '@albedian/contracts';
import { vecToRaDecDeg } from './healpix.js';
import {
  catalogIdAt,
  catalogOf,
  decodeDistanceBounds,
  distanceKindOf,
  halfToFloat,
  magnitudeBandOf,
  usesBMinusV,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
} from './tileCodec.js';
import {
  formatTileKey,
  type CatalogStarAstrophysics,
  type CatalogStarInterval,
  type CatalogStarRecord,
} from './tileLoaderTypes.js';

const HALF_NAN = 0x7e00;

function finiteOrNull(value: number | undefined): number | null {
  return value !== undefined && Number.isFinite(value) ? value : null;
}

function intervalAt(
  median: Float32Array,
  lower: Float32Array,
  upper: Float32Array,
  index: number
): CatalogStarInterval | null {
  const m = finiteOrNull(median[index]);
  return m === null
    ? null
    : { median: m, lower: finiteOrNull(lower[index]), upper: finiteOrNull(upper[index]) };
}

function starRecordSourceIds(
  catalog: CatalogStarCatalogId,
  distanceKind: CatalogStarDistanceKind
): string[] {
  if (catalog !== 'gaia-dr3') {
    return ['hipparcos-2007'];
  }
  return distanceKind === 'none' ? ['gaia-dr3'] : ['gaia-dr3', 'bailer-jones-2021'];
}

export function assertCoreStarIndex(core: CatalogStarCoreTile, index: number): void {
  if (index < 0 || index >= core.count) {
    throw new Error(`star index ${index} outside tile ${formatTileKey(core.layer, core.pix)}`);
  }
}

export function assembleStarRecord(params: {
  core: CatalogStarCoreTile;
  detail: CatalogStarDetailTile;
  index: number;
  epochJulianYear: number;
  nameIndex: CatalogStarNameIndex | null;
}): CatalogStarRecord {
  const { core, detail, index, epochJulianYear, nameIndex } = params;
  const flags = core.flags[index] ?? 0;
  const catalog = catalogOf(flags);
  const catalogId = catalogIdAt(detail, index);
  const { raDeg, decDeg } = vecToRaDecDeg([
    core.directions[3 * index] ?? 0,
    core.directions[3 * index + 1] ?? 0,
    core.directions[3 * index + 2] ?? 0,
  ]);
  const median = core.distancePc[index] ?? Number.NaN;
  const bounds = decodeDistanceBounds(
    median,
    detail.distanceLowerCode[index] ?? 0,
    detail.distanceUpperCode[index] ?? 0
  );
  const distanceKind = distanceKindOf(flags);
  const pmRa = halfToFloat(detail.pmRaHalf[index] ?? HALF_NAN);
  const pmDec = halfToFloat(detail.pmDecHalf[index] ?? HALF_NAN);
  const rv = halfToFloat(detail.radialVelocityHalf[index] ?? HALF_NAN);
  const names = nameIndex?.names.filter(
    (n) => n.catalog === catalog && n.catalogId === catalogId
  );

  return {
    evidenceKind: 'catalog-derived',
    catalog,
    catalogId,
    designation: catalog === 'gaia-dr3' ? `Gaia DR3 ${catalogId}` : `HIP ${catalogId}`,
    names: names ?? [],
    raDeg,
    decDeg,
    epochJulianYear,
    magnitude: core.magnitude[index] ?? Number.NaN,
    magnitudeBand: magnitudeBandOf(flags),
    colorIndex: finiteOrNull(core.colorIndex[index]),
    colorIndexKind: usesBMinusV(flags) ? 'B-V' : 'BP-RP',
    distance: Number.isFinite(median)
      ? {
          medianPc: median,
          lowerPc: finiteOrNull(bounds.lowerPc),
          upperPc: finiteOrNull(bounds.upperPc),
          kind: distanceKind,
        }
      : null,
    properMotion:
      Number.isFinite(pmRa) && Number.isFinite(pmDec)
        ? { raMasYr: pmRa, decMasYr: pmDec }
        : null,
    radialVelocityKmS: finiteOrNull(rv),
    sourceIds: starRecordSourceIds(catalog, distanceKind),
  };
}

export function assembleStarAstrophysics(
  tile: CatalogStarAstroTile,
  index: number
): CatalogStarAstrophysics {
  if (!Number.isInteger(index) || index < 0 || index >= tile.count) {
    throw new Error(`star index ${index} outside tile ${formatTileKey(tile.layer, tile.pix)}`);
  }
  const luminositySolar = intervalAt(
    tile.luminositySolar,
    tile.luminosityLowerSolar,
    tile.luminosityUpperSolar,
    index
  );
  const record: CatalogStarAstrophysics = {
    evidenceKind: 'catalog-derived',
    temperatureK: intervalAt(tile.teffK, tile.teffLowerK, tile.teffUpperK, index),
    luminositySolar,
    extinctionA0Mag: intervalAt(tile.a0Mag, tile.a0LowerMag, tile.a0UpperMag, index),
    extinctionGMag: finiteOrNull(tile.agMag[index]),
    ruwe: finiteOrNull(tile.ruwe[index]),
    parallaxErrorMas: finiteOrNull(tile.parallaxErrorMas[index]),
    sourceIds: [],
  };
  const fromGaiaSource = [
    record.temperatureK,
    record.extinctionA0Mag,
    record.extinctionGMag,
    record.ruwe,
    record.parallaxErrorMas,
  ];
  if (fromGaiaSource.some((value) => value !== null)) {
    record.sourceIds.push('gaia-dr3');
  }
  if (luminositySolar !== null) {
    record.sourceIds.push('gaia-dr3-astrophysical-parameters');
  }
  return record;
}
