import {
  CATALOG_STAR_TILE_ASTRO_ENCODING as ASTRO,
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
} from '@albedian/contracts';
import { faceXyToVec, nestToFaceIxIy, type Vec3 } from '../healpix.js';
import {
  decodeColorIndex,
  decodeDistancePc,
  decodeExtinctionMag,
  decodeLuminositySolar,
  decodeMagnitude,
  decodeTeffK,
  halfToFloat,
} from './scalarDecoders.js';
import { TileColumnReader, expectTile, expectTileByteLength, readTileHeader } from './tileHeader.js';
import {
  CORE_FIXED_BYTES_PER_STAR,
  DETAIL_BYTES_PER_STAR,
  TILE_KIND_ASTRO,
  TILE_KIND_CORE,
  TILE_KIND_DETAIL,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
  type ExpectedTileCoordinates,
} from './tileTypes.js';

export function decodeCoreTile(
  buffer: ArrayBuffer,
  expected?: ExpectedTileCoordinates
): CatalogStarCoreTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_CORE, expected);
  const { count: n, positionBits } = header;
  if (positionBits !== 16 && positionBits !== 32) {
    throw new Error(`unsupported position width ${positionBits}`);
  }
  const posBytes = positionBits / 8;
  expectTileByteLength(buffer.byteLength, n, 2 * posBytes + CORE_FIXED_BYTES_PER_STAR, 'core');

  const reader = new TileColumnReader(buffer, n);
  const posU = positionBits === 32 ? reader.take(Uint32Array) : reader.take(Uint16Array);
  const posV = positionBits === 32 ? reader.take(Uint32Array) : reader.take(Uint16Array);
  const magCodes = reader.take(Uint16Array);
  const colorCodes = reader.take(Int16Array);
  const distanceCodes = reader.take(Uint16Array);
  const flags = reader.take(Uint8Array);

  const { face, ix, iy } = nestToFaceIxIy(header.order, header.pix);
  const nside = 2 ** header.order;
  const scale = 2 ** positionBits;
  const directions = new Float32Array(3 * n);
  const magnitude = new Float32Array(n);
  const colorIndex = new Float32Array(n);
  const distancePc = new Float32Array(n);
  const v: Vec3 = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    const x = (ix + ((posU[i] ?? 0) + 0.5) / scale) / nside;
    const y = (iy + ((posV[i] ?? 0) + 0.5) / scale) / nside;
    faceXyToVec(face, x, y, v);
    directions[3 * i] = v[0];
    directions[3 * i + 1] = v[1];
    directions[3 * i + 2] = v[2];
    magnitude[i] = decodeMagnitude(magCodes[i] ?? 0);
    colorIndex[i] = decodeColorIndex(colorCodes[i] ?? ENC.colorMissing);
    distancePc[i] = decodeDistancePc(distanceCodes[i] ?? ENC.distanceMissing);
  }
  return {
    layer: header.layer,
    order: header.order,
    pix: header.pix,
    count: n,
    positionBits,
    directions,
    magnitude,
    colorIndex,
    distancePc,
    flags: flags.slice(),
    byteLength: buffer.byteLength,
  };
}

export function decodeDetailTile(
  buffer: ArrayBuffer,
  expected?: ExpectedTileCoordinates
): CatalogStarDetailTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_DETAIL, expected);
  const n = header.count;
  expectTileByteLength(buffer.byteLength, n, DETAIL_BYTES_PER_STAR, 'detail');

  const reader = new TileColumnReader(buffer, n);
  return {
    layer: header.layer,
    order: header.order,
    pix: header.pix,
    count: n,
    idHi: reader.take(Uint32Array),
    idLo: reader.take(Uint32Array),
    pmRaHalf: reader.take(Uint16Array),
    pmDecHalf: reader.take(Uint16Array),
    radialVelocityHalf: reader.take(Uint16Array),
    distanceLowerCode: reader.take(Uint8Array),
    distanceUpperCode: reader.take(Uint8Array),
    byteLength: buffer.byteLength,
  };
}

export function decodeAstroTile(
  buffer: ArrayBuffer,
  expected?: ExpectedTileCoordinates
): CatalogStarAstroTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_ASTRO, expected);
  const n = header.count;
  expectTileByteLength(buffer.byteLength, n, ASTRO.bytesPerStar, 'astro');

  const reader = new TileColumnReader(buffer, n);
  return {
    layer: header.layer,
    order: header.order,
    pix: header.pix,
    count: n,
    teffK: reader.decodeFloat32Column(Uint16Array, decodeTeffK),
    teffLowerK: reader.decodeFloat32Column(Uint16Array, decodeTeffK),
    teffUpperK: reader.decodeFloat32Column(Uint16Array, decodeTeffK),
    luminositySolar: reader.decodeFloat32Column(Int16Array, decodeLuminositySolar),
    luminosityLowerSolar: reader.decodeFloat32Column(Int16Array, decodeLuminositySolar),
    luminosityUpperSolar: reader.decodeFloat32Column(Int16Array, decodeLuminositySolar),
    a0Mag: reader.decodeFloat32Column(Uint16Array, decodeExtinctionMag),
    a0LowerMag: reader.decodeFloat32Column(Uint16Array, decodeExtinctionMag),
    a0UpperMag: reader.decodeFloat32Column(Uint16Array, decodeExtinctionMag),
    agMag: reader.decodeFloat32Column(Uint16Array, decodeExtinctionMag),
    ruwe: reader.decodeFloat32Column(Uint16Array, halfToFloat),
    parallaxErrorMas: reader.decodeFloat32Column(Uint16Array, halfToFloat),
    byteLength: buffer.byteLength,
  };
}
