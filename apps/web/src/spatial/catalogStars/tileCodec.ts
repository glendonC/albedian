import {
  CATALOG_STAR_TILE_ASTRO_ENCODING as ASTRO,
  CATALOG_STAR_TILE_ENCODING_V1 as ENC,
  CATALOG_STAR_TILE_HEADER_VERSION,
  type CatalogStarCatalogId,
  type CatalogStarDistanceKind,
} from '@albedian/contracts';
import { faceXyToVec, nestToFaceIxIy, type Vec3 } from './healpix.js';

/**
 * Decoder for catalog star tiles (tile header version 1: core, detail and astro kinds), the
 * browser half of the codec in `services/science/catalog_tiles.py`. Values decoded here are
 * catalog data, never measurements from a user's recording.
 */

export const TILE_KIND_CORE = 1;
export const TILE_KIND_DETAIL = 2;
export const TILE_KIND_ASTRO = ASTRO.kind;

const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

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

export function readTileHeader(buffer: ArrayBuffer): CatalogStarTileHeader {
  if (buffer.byteLength < ENC.headerBytes) {
    throw new Error('catalog star tile is shorter than its header');
  }
  const bytes = new Uint8Array(buffer, 0, 4);
  const magic = String.fromCharCode(bytes[0] ?? 0, bytes[1] ?? 0, bytes[2] ?? 0, bytes[3] ?? 0);
  if (magic !== ENC.magic) {
    throw new Error('not an Albedian catalog star tile');
  }
  const view = new DataView(buffer);
  const version = view.getUint16(4, true);
  if (version !== CATALOG_STAR_TILE_HEADER_VERSION) {
    throw new Error(`unsupported catalog star tile header version ${version}`);
  }
  return {
    kind: view.getUint8(6),
    positionBits: view.getUint8(7),
    layer: view.getUint8(8),
    order: view.getUint8(9),
    pix: view.getUint32(12, true),
    count: view.getUint32(16, true),
  };
}

function expectTile(
  header: CatalogStarTileHeader,
  kind: number,
  expected?: { layer: number; order: number; pix: number }
): void {
  if (!LITTLE_ENDIAN) {
    throw new Error('catalog star tiles are little-endian; this platform is not');
  }
  if (header.kind !== kind) {
    throw new Error(`expected tile kind ${kind}, found ${header.kind}`);
  }
  if (
    expected &&
    (header.layer !== expected.layer || header.order !== expected.order || header.pix !== expected.pix)
  ) {
    throw new Error(
      `tile header L${header.layer}/o${header.order}/${header.pix} does not match request L${expected.layer}/o${expected.order}/${expected.pix}`
    );
  }
}

export function decodeMagnitude(code: number): number {
  return code / ENC.magnitudeScale - ENC.magnitudeOffset;
}

export function decodeColorIndex(code: number): number {
  return code === ENC.colorMissing ? Number.NaN : code / ENC.colorScale;
}

export function decodeDistancePc(code: number): number {
  return code === ENC.distanceMissing
    ? Number.NaN
    : 10 ** (ENC.distanceLog10Min + (code - 1) * ENC.distanceLog10Step);
}

function decodeBoundFraction(code: number, span: number): number {
  return code === ENC.boundMissing
    ? Number.NaN
    : 10 ** (ENC.boundLog10FractionMin + ((code - 1) / 254) * span);
}

export function decodeDistanceBounds(
  medianPc: number,
  lowerCode: number,
  upperCode: number
): { lowerPc: number; upperPc: number } {
  return {
    lowerPc: medianPc * (1 - decodeBoundFraction(lowerCode, ENC.boundLowerLog10Span)),
    upperPc: medianPc * (1 + decodeBoundFraction(upperCode, ENC.boundUpperLog10Span)),
  };
}

/** IEEE 754 binary16 to number; NaN marks a missing catalog value. */
export function halfToFloat(bits: number): number {
  const sign = bits & 0x8000 ? -1 : 1;
  const exponent = (bits >> 10) & 0x1f;
  const fraction = bits & 0x3ff;
  if (exponent === 0) return sign * 2 ** -14 * (fraction / 1024);
  if (exponent === 31) return fraction ? Number.NaN : sign * Number.POSITIVE_INFINITY;
  return sign * 2 ** (exponent - 15) * (1 + fraction / 1024);
}

const DISTANCE_KINDS: CatalogStarDistanceKind[] = [
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

export function decodeCoreTile(
  buffer: ArrayBuffer,
  expected?: { layer: number; order: number; pix: number }
): CatalogStarCoreTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_CORE, expected);
  const { count: n, positionBits } = header;
  if (positionBits !== 16 && positionBits !== 32) {
    throw new Error(`unsupported position width ${positionBits}`);
  }
  const posBytes = positionBits / 8;
  if (buffer.byteLength !== ENC.headerBytes + n * (2 * posBytes + 7)) {
    throw new Error('core tile length does not match its header');
  }
  let offset = ENC.headerBytes;
  const posU = positionBits === 32 ? new Uint32Array(buffer, offset, n) : new Uint16Array(buffer, offset, n);
  offset += n * posBytes;
  const posV = positionBits === 32 ? new Uint32Array(buffer, offset, n) : new Uint16Array(buffer, offset, n);
  offset += n * posBytes;
  const magCodes = new Uint16Array(buffer, offset, n);
  offset += 2 * n;
  const colorCodes = new Int16Array(buffer, offset, n);
  offset += 2 * n;
  const distanceCodes = new Uint16Array(buffer, offset, n);
  offset += 2 * n;
  const flags = new Uint8Array(buffer, offset, n);

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
  expected?: { layer: number; order: number; pix: number }
): CatalogStarDetailTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_DETAIL, expected);
  const n = header.count;
  if (buffer.byteLength !== ENC.headerBytes + n * 16) {
    throw new Error('detail tile length does not match its header');
  }
  let offset = ENC.headerBytes;
  const take32 = () => {
    const a = new Uint32Array(buffer, offset, n);
    offset += 4 * n;
    return a;
  };
  const take16 = () => {
    const a = new Uint16Array(buffer, offset, n);
    offset += 2 * n;
    return a;
  };
  const take8 = () => {
    const a = new Uint8Array(buffer, offset, n);
    offset += n;
    return a;
  };
  return {
    layer: header.layer,
    order: header.order,
    pix: header.pix,
    count: n,
    idHi: take32(),
    idLo: take32(),
    pmRaHalf: take16(),
    pmDecHalf: take16(),
    radialVelocityHalf: take16(),
    distanceLowerCode: take8(),
    distanceUpperCode: take8(),
    byteLength: buffer.byteLength,
  };
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

export function decodeTeffK(code: number): number {
  return code === ASTRO.teffMissing ? Number.NaN : code;
}

export function decodeLuminositySolar(code: number): number {
  return code === ASTRO.logLumMissing ? Number.NaN : 10 ** (code / ASTRO.logLumScale);
}

export function decodeExtinctionMag(code: number): number {
  return code === ASTRO.extinctionMissing ? Number.NaN : code / ASTRO.extinctionScale;
}

export function decodeAstroTile(
  buffer: ArrayBuffer,
  expected?: { layer: number; order: number; pix: number }
): CatalogStarAstroTile {
  const header = readTileHeader(buffer);
  expectTile(header, TILE_KIND_ASTRO, expected);
  const n = header.count;
  if (buffer.byteLength !== ENC.headerBytes + n * ASTRO.bytesPerStar) {
    throw new Error('astro tile length does not match its header');
  }
  let offset = ENC.headerBytes;
  const column = <T extends Uint16Array | Int16Array>(View: new (b: ArrayBuffer, o: number, n: number) => T, decode: (code: number) => number) => {
    const codes = new View(buffer, offset, n);
    offset += 2 * n;
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = decode(codes[i] ?? 0);
    return out;
  };
  return {
    layer: header.layer,
    order: header.order,
    pix: header.pix,
    count: n,
    teffK: column(Uint16Array, decodeTeffK),
    teffLowerK: column(Uint16Array, decodeTeffK),
    teffUpperK: column(Uint16Array, decodeTeffK),
    luminositySolar: column(Int16Array, decodeLuminositySolar),
    luminosityLowerSolar: column(Int16Array, decodeLuminositySolar),
    luminosityUpperSolar: column(Int16Array, decodeLuminositySolar),
    a0Mag: column(Uint16Array, decodeExtinctionMag),
    a0LowerMag: column(Uint16Array, decodeExtinctionMag),
    a0UpperMag: column(Uint16Array, decodeExtinctionMag),
    agMag: column(Uint16Array, decodeExtinctionMag),
    ruwe: column(Uint16Array, halfToFloat),
    parallaxErrorMas: column(Uint16Array, halfToFloat),
    byteLength: buffer.byteLength,
  };
}

/** Gaia DR3 source_id or HIP number as a decimal string (source_ids exceed 2^53). */
export function catalogIdAt(detail: CatalogStarDetailTile, index: number): string {
  const hi = BigInt(detail.idHi[index] ?? 0);
  const lo = BigInt(detail.idLo[index] ?? 0);
  return ((hi << 32n) | lo).toString();
}
