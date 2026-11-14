import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  CATALOG_STAR_TILE_ASTRO_ENCODING,
  CATALOG_STAR_TILE_ENCODING_V1,
  CATALOG_STAR_TILE_HEADER_VERSION,
} from '@albedian/contracts';
import { angleBetween, faceXyToVec, nestToFaceIxIy, raDecDegToVec, type Vec3 } from '../healpix';
import * as barrelModule from '../index.js';
import * as tileCodecModule from '../tileCodec';
import * as tileCodecJsModule from '../tileCodec.js';
import {
  TILE_KIND_ASTRO,
  TILE_KIND_CORE,
  TILE_KIND_DETAIL,
  catalogIdAt,
  catalogOf,
  decodeAstroTile,
  decodeColorIndex,
  decodeCoreTile,
  decodeDetailTile,
  decodeDistanceBounds,
  decodeDistancePc,
  decodeExtinctionMag,
  decodeLuminositySolar,
  decodeMagnitude,
  decodeTeffK,
  distanceKindOf,
  halfToFloat,
  isNamed,
  magnitudeBandOf,
  readTileHeader,
  usesBMinusV,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
  type CatalogStarDetailTile,
  type CatalogStarTileHeader,
} from '../tileCodec';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteLength + bytes.byteOffset) as ArrayBuffer;
};

const ARCSEC = Math.PI / (180 * 3600);

interface HeaderSpec {
  magic?: string;
  version?: number;
  kind: number;
  positionBits: number;
  layer: number;
  order: number;
  pix: number;
  count: number;
}

function writeHeader(view: DataView, spec: HeaderSpec): void {
  const magic = spec.magic ?? CATALOG_STAR_TILE_ENCODING_V1.magic;
  for (let i = 0; i < 4; i++) {
    view.setUint8(i, magic.charCodeAt(i) || 0);
  }
  view.setUint16(4, spec.version ?? CATALOG_STAR_TILE_HEADER_VERSION, true);
  view.setUint8(6, spec.kind);
  view.setUint8(7, spec.positionBits);
  view.setUint8(8, spec.layer);
  view.setUint8(9, spec.order);
  view.setUint16(10, 0, true);
  view.setUint32(12, spec.pix, true);
  view.setUint32(16, spec.count, true);
}

function buildCoreBuffer(spec: {
  layer: number;
  order: number;
  pix: number;
  positionBits: 16 | 32;
  posU: number[];
  posV: number[];
  magCodes: number[];
  colorCodes: number[];
  distanceCodes: number[];
  flags: number[];
}): ArrayBuffer {
  const n = spec.magCodes.length;
  const posBytes = spec.positionBits / 8;
  const totalBytes = CATALOG_STAR_TILE_ENCODING_V1.headerBytes + n * (2 * posBytes + 7);
  const buffer = new ArrayBuffer(totalBytes);
  const view = new DataView(buffer);
  writeHeader(view, {
    kind: TILE_KIND_CORE,
    positionBits: spec.positionBits,
    layer: spec.layer,
    order: spec.order,
    pix: spec.pix,
    count: n,
  });
  let offset = CATALOG_STAR_TILE_ENCODING_V1.headerBytes;
  for (let i = 0; i < n; i++) {
    if (spec.positionBits === 32) {
      view.setUint32(offset, spec.posU[i]!, true);
    } else {
      view.setUint16(offset, spec.posU[i]!, true);
    }
    offset += posBytes;
  }
  for (let i = 0; i < n; i++) {
    if (spec.positionBits === 32) {
      view.setUint32(offset, spec.posV[i]!, true);
    } else {
      view.setUint16(offset, spec.posV[i]!, true);
    }
    offset += posBytes;
  }
  for (let i = 0; i < n; i++) {
    view.setUint16(offset, spec.magCodes[i]!, true);
    offset += 2;
  }
  for (let i = 0; i < n; i++) {
    view.setInt16(offset, spec.colorCodes[i]!, true);
    offset += 2;
  }
  for (let i = 0; i < n; i++) {
    view.setUint16(offset, spec.distanceCodes[i]!, true);
    offset += 2;
  }
  for (let i = 0; i < n; i++) {
    view.setUint8(offset, spec.flags[i]!);
    offset += 1;
  }
  return buffer;
}

function buildDetailBuffer(spec: {
  layer: number;
  order: number;
  pix: number;
  idHi: number[];
  idLo: number[];
  pmRaHalf: number[];
  pmDecHalf: number[];
  radialVelocityHalf: number[];
  distanceLowerCode: number[];
  distanceUpperCode: number[];
}): ArrayBuffer {
  const n = spec.idHi.length;
  const buffer = new ArrayBuffer(CATALOG_STAR_TILE_ENCODING_V1.headerBytes + n * 16);
  const view = new DataView(buffer);
  writeHeader(view, {
    kind: TILE_KIND_DETAIL,
    positionBits: 0,
    layer: spec.layer,
    order: spec.order,
    pix: spec.pix,
    count: n,
  });
  let offset = CATALOG_STAR_TILE_ENCODING_V1.headerBytes;
  const writeU32 = (values: number[]) => {
    for (const v of values) {
      view.setUint32(offset, v, true);
      offset += 4;
    }
  };
  const writeU16 = (values: number[]) => {
    for (const v of values) {
      view.setUint16(offset, v, true);
      offset += 2;
    }
  };
  const writeU8 = (values: number[]) => {
    for (const v of values) {
      view.setUint8(offset, v);
      offset += 1;
    }
  };
  writeU32(spec.idHi);
  writeU32(spec.idLo);
  writeU16(spec.pmRaHalf);
  writeU16(spec.pmDecHalf);
  writeU16(spec.radialVelocityHalf);
  writeU8(spec.distanceLowerCode);
  writeU8(spec.distanceUpperCode);
  return buffer;
}

function buildAstroBuffer(spec: {
  layer: number;
  order: number;
  pix: number;
  teff: number[];
  teffLower: number[];
  teffUpper: number[];
  logLum: number[];
  logLumLower: number[];
  logLumUpper: number[];
  a0: number[];
  a0Lower: number[];
  a0Upper: number[];
  ag: number[];
  ruweHalf: number[];
  parallaxErrorHalf: number[];
}): ArrayBuffer {
  const n = spec.teff.length;
  const buffer = new ArrayBuffer(
    CATALOG_STAR_TILE_ENCODING_V1.headerBytes + n * CATALOG_STAR_TILE_ASTRO_ENCODING.bytesPerStar
  );
  const view = new DataView(buffer);
  writeHeader(view, {
    kind: TILE_KIND_ASTRO,
    positionBits: 0,
    layer: spec.layer,
    order: spec.order,
    pix: spec.pix,
    count: n,
  });
  let offset = CATALOG_STAR_TILE_ENCODING_V1.headerBytes;
  const writeU16 = (values: number[]) => {
    for (const v of values) {
      view.setUint16(offset, v, true);
      offset += 2;
    }
  };
  const writeI16 = (values: number[]) => {
    for (const v of values) {
      view.setInt16(offset, v, true);
      offset += 2;
    }
  };
  writeU16(spec.teff);
  writeU16(spec.teffLower);
  writeU16(spec.teffUpper);
  writeI16(spec.logLum);
  writeI16(spec.logLumLower);
  writeI16(spec.logLumUpper);
  writeU16(spec.a0);
  writeU16(spec.a0Lower);
  writeU16(spec.a0Upper);
  writeU16(spec.ag);
  writeU16(spec.ruweHalf);
  writeU16(spec.parallaxErrorHalf);
  return buffer;
}

describe('tileCodec runtime export surface and barrel parity', () => {
  const expectedExportKeys = [
    'TILE_KIND_ASTRO',
    'TILE_KIND_CORE',
    'TILE_KIND_DETAIL',
    'catalogIdAt',
    'catalogOf',
    'decodeAstroTile',
    'decodeColorIndex',
    'decodeCoreTile',
    'decodeDetailTile',
    'decodeDistanceBounds',
    'decodeDistancePc',
    'decodeExtinctionMag',
    'decodeLuminositySolar',
    'decodeMagnitude',
    'decodeTeffK',
    'distanceKindOf',
    'halfToFloat',
    'isNamed',
    'magnitudeBandOf',
    'readTileHeader',
    'usesBMinusV',
  ];

  it('pins exact Object.keys on extensionless and .js specifiers and re-exports identically from index.js', () => {
    expect(Object.keys(tileCodecModule).sort()).toEqual(expectedExportKeys);
    expect(Object.keys(tileCodecJsModule).sort()).toEqual(expectedExportKeys);
    for (const key of expectedExportKeys) {
      const k = key as keyof typeof tileCodecModule;
      expect(tileCodecJsModule[k]).toBe(tileCodecModule[k]);
      expect((barrelModule as Record<string, unknown>)[key]).toBe(tileCodecModule[k]);
    }
  });

  it('pins tile kind constants and exported type shapes', () => {
    expect(TILE_KIND_CORE).toBe(1);
    expect(TILE_KIND_DETAIL).toBe(2);
    expect(TILE_KIND_ASTRO).toBe(3);
    expect(TILE_KIND_ASTRO).toBe(CATALOG_STAR_TILE_ASTRO_ENCODING.kind);

    const headerShape = {
      kind: TILE_KIND_CORE,
      positionBits: 16,
      layer: 0,
      order: 0,
      pix: 0,
      count: 0,
    } satisfies CatalogStarTileHeader;
    expect(headerShape.kind).toBe(1);
  });
});

describe('IEEE-754 binary16 halfToFloat', () => {
  it('decodes signed zeros, subnormals, normals, infinities, and NaN payloads', () => {
    expect(Object.is(halfToFloat(0x0000), +0)).toBe(true);
    expect(Object.is(halfToFloat(0x8000), -0)).toBe(true);

    // Subnormals: exponent === 0, fraction !== 0
    expect(halfToFloat(0x0001)).toBe(2 ** -24);
    expect(halfToFloat(0x8001)).toBe(-(2 ** -24));
    expect(halfToFloat(0x03ff)).toBe(2 ** -14 * (1023 / 1024));
    expect(halfToFloat(0x83ff)).toBe(-(2 ** -14 * (1023 / 1024)));

    // Smallest and largest normals, powers of two, and intermediate fractions
    expect(halfToFloat(0x0400)).toBe(2 ** -14);
    expect(halfToFloat(0x8400)).toBe(-(2 ** -14));
    expect(halfToFloat(0x3800)).toBe(0.5);
    expect(halfToFloat(0x3c00)).toBe(1);
    expect(halfToFloat(0xbc00)).toBe(-1);
    expect(halfToFloat(0x3e00)).toBe(1.5);
    expect(halfToFloat(0xc000)).toBe(-2);
    expect(halfToFloat(0x7bff)).toBe(65504);
    expect(halfToFloat(0xfbff)).toBe(-65504);

    // Infinities: exponent === 31, fraction === 0
    expect(halfToFloat(0x7c00)).toBe(Number.POSITIVE_INFINITY);
    expect(halfToFloat(0xfc00)).toBe(Number.NEGATIVE_INFINITY);

    // Quiet and signaling NaN payloads of both signs
    for (const nanBits of [0x7c01, 0x7dff, 0x7e00, 0x7fff, 0xfc01, 0xfe00, 0xffff]) {
      expect(halfToFloat(nanBits)).toBeNaN();
    }
  });

  it('PBT: satisfies IEEE-754 sign symmetry, monotonic magnitude ordering, and exact NaN mask across 16-bit words', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 0x7fff }), (posBits) => {
        const negBits = posBits | 0x8000;
        const posVal = halfToFloat(posBits);
        const negVal = halfToFloat(negBits);
        const isNan = (posBits & 0x7c00) === 0x7c00 && (posBits & 0x03ff) !== 0;
        if (isNan) {
          expect(posVal).toBeNaN();
          expect(negVal).toBeNaN();
        } else {
          expect(Object.is(negVal, -posVal)).toBe(true);
          if (posBits < 0x7c00) {
            expect(halfToFloat(posBits + 1)).toBeGreaterThan(posVal);
          }
        }
      })
    );
  });
});

describe('scalar decoders and flag predicates', () => {
  it('decodes magnitude codes with 1 mmag scale and -2 mag offset', () => {
    expect(decodeMagnitude(0)).toBe(-2);
    expect(decodeMagnitude(2000)).toBe(0);
    expect(decodeMagnitude(8500)).toBeCloseTo(6.5, 12);
    expect(decodeMagnitude(14000)).toBe(12);
    expect(decodeMagnitude(65535)).toBeCloseTo(63.535, 12);
  });

  it('decodes color index codes and maps the missing sentinel (-32768) to NaN', () => {
    expect(decodeColorIndex(CATALOG_STAR_TILE_ENCODING_V1.colorMissing)).toBeNaN();
    expect(decodeColorIndex(-32768)).toBeNaN();
    expect(decodeColorIndex(-32767)).toBeCloseTo(-32.767, 12);
    expect(decodeColorIndex(-400)).toBeCloseTo(-0.4, 12);
    expect(decodeColorIndex(0)).toBe(0);
    expect(decodeColorIndex(820)).toBeCloseTo(0.82, 12);
    expect(decodeColorIndex(32767)).toBeCloseTo(32.767, 12);
  });

  it('decodes log10 distance codes and maps code 0 to NaN', () => {
    expect(decodeDistancePc(CATALOG_STAR_TILE_ENCODING_V1.distanceMissing)).toBeNaN();
    expect(decodeDistancePc(0)).toBeNaN();
    expect(decodeDistancePc(1)).toBeCloseTo(0.1, 12);
    expect(decodeDistancePc(10001)).toBeCloseTo(1, 12);
    expect(decodeDistancePc(40001)).toBeCloseTo(1000, 9);
    expect(decodeDistancePc(65535)).toBeCloseTo(10 ** (-1 + 65534 * 1e-4), 6);
    expect(decodeDistancePc(20001)).toBeGreaterThan(decodeDistancePc(20000));
  });

  it('decodes asymmetric log-fractional distance bounds and preserves missing sentinels independently', () => {
    expect(decodeDistanceBounds(100, 0, 0)).toEqual({ lowerPc: Number.NaN, upperPc: Number.NaN });
    const missingLower = decodeDistanceBounds(100, 0, 1);
    expect(missingLower.lowerPc).toBeNaN();
    expect(missingLower.upperPc).toBeCloseTo(100 * (1 + 1e-5), 10);

    const missingUpper = decodeDistanceBounds(100, 1, 0);
    expect(missingUpper.lowerPc).toBeCloseTo(100 * (1 - 1e-5), 10);
    expect(missingUpper.upperPc).toBeNaN();

    const missingMedian = decodeDistanceBounds(Number.NaN, 128, 128);
    expect(missingMedian.lowerPc).toBeNaN();
    expect(missingMedian.upperPc).toBeNaN();

    const fullSpan = decodeDistanceBounds(50, 255, 255);
    expect(fullSpan.lowerPc).toBeCloseTo(0, 12);
    expect(fullSpan.upperPc).toBeCloseTo(50 * 101, 9);

    const narrow = decodeDistanceBounds(200, 50, 50);
    const wide = decodeDistanceBounds(200, 150, 150);
    expect(wide.lowerPc).toBeLessThan(narrow.lowerPc);
    expect(wide.upperPc).toBeGreaterThan(narrow.upperPc);
  });

  it('decodes astro scalar codes (Teff, luminosity, extinction) and preserves reserved missing codes', () => {
    expect(decodeTeffK(0)).toBeNaN();
    expect(decodeTeffK(1)).toBe(1);
    expect(decodeTeffK(5772)).toBe(5772);
    expect(decodeTeffK(65535)).toBe(65535);

    expect(decodeLuminositySolar(-32768)).toBeNaN();
    expect(decodeLuminositySolar(-2000)).toBeCloseTo(0.01, 12);
    expect(decodeLuminositySolar(-1000)).toBeCloseTo(0.1, 12);
    expect(decodeLuminositySolar(0)).toBe(1);
    expect(decodeLuminositySolar(1000)).toBeCloseTo(10, 12);
    expect(decodeLuminositySolar(3000)).toBeCloseTo(1000, 9);

    expect(decodeExtinctionMag(65535)).toBeNaN();
    expect(decodeExtinctionMag(0)).toBe(0);
    expect(decodeExtinctionMag(116)).toBeCloseTo(0.116, 12);
    expect(decodeExtinctionMag(65534)).toBeCloseTo(65.534, 12);
  });

  it('exhaustively decodes all 256 core flag bytes with orthogonal bit masks', () => {
    const expectedKinds = [
      'none',
      'bailer-jones-2021-photogeometric',
      'bailer-jones-2021-geometric',
      'hipparcos-2007-parallax-inverse',
    ] as const;

    for (let flags = 0; flags <= 0xff; flags++) {
      expect(distanceKindOf(flags)).toBe(expectedKinds[flags & 0b11]);
      expect(magnitudeBandOf(flags)).toBe((flags & 0b0000_0100) !== 0 ? 'Hp' : 'G');
      expect(usesBMinusV(flags)).toBe((flags & 0b0000_1000) !== 0);
      expect(catalogOf(flags)).toBe((flags & 0b0001_0000) !== 0 ? 'hipparcos-2007' : 'gaia-dr3');
      expect(isNamed(flags)).toBe((flags & 0b0010_0000) !== 0);
    }
  });

  it('formats 64-bit catalog IDs across zero, 32-bit HIP, >2^53 Gaia DR3, max uint64, and out-of-range indices', () => {
    const detail: CatalogStarDetailTile = {
      layer: 0,
      order: 0,
      pix: 0,
      count: 4,
      idHi: new Uint32Array([0, 1, 0x5fffffff, 0xffffffff]),
      idLo: new Uint32Array([32349, 0, 0xfffffff0, 0xffffffff]),
      pmRaHalf: new Uint16Array(4),
      pmDecHalf: new Uint16Array(4),
      radialVelocityHalf: new Uint16Array(4),
      distanceLowerCode: new Uint8Array(4),
      distanceUpperCode: new Uint8Array(4),
      byteLength: 96,
    };
    expect(catalogIdAt(detail, 0)).toBe('32349');
    expect(catalogIdAt(detail, 1)).toBe('4294967296');
    expect(catalogIdAt(detail, 2)).toBe('6917529027641081840');
    expect(catalogIdAt(detail, 3)).toBe('18446744073709551615');
    expect(catalogIdAt(detail, 99)).toBe('0');
    expect(catalogIdAt(detail, -1)).toBe('0');
  });
});

describe('tile header parsing and validation errors', () => {
  it('parses valid headers and ignores reserved padding bytes', () => {
    const buffer = new ArrayBuffer(32);
    const view = new DataView(buffer);
    writeHeader(view, {
      kind: TILE_KIND_CORE,
      positionBits: 32,
      layer: 4,
      order: 3,
      pix: 767,
      count: 512,
    });
    view.setUint16(10, 0xbeef, true);
    view.setUint32(20, 0xdeadbeef, true);
    expect(readTileHeader(buffer)).toEqual({
      kind: TILE_KIND_CORE,
      positionBits: 32,
      layer: 4,
      order: 3,
      pix: 767,
      count: 512,
    });
  });

  it('rejects truncated headers, bad magic bytes, and unsupported header versions', () => {
    for (const byteLength of [0, 3, 31]) {
      expect(() => readTileHeader(new ArrayBuffer(byteLength))).toThrow(/shorter than its header/);
    }

    for (const badMagic of ['ALB!', 'albs', '\0\0\0\0', 'PNG\x89']) {
      const buf = new ArrayBuffer(32);
      writeHeader(new DataView(buf), {
        magic: badMagic,
        kind: TILE_KIND_CORE,
        positionBits: 16,
        layer: 0,
        order: 0,
        pix: 0,
        count: 0,
      });
      expect(() => readTileHeader(buf)).toThrow(/not an Albedian catalog star tile/);
    }

    for (const badVersion of [0, 2, 256, 65535]) {
      const buf = new ArrayBuffer(32);
      writeHeader(new DataView(buf), {
        version: badVersion,
        kind: TILE_KIND_CORE,
        positionBits: 16,
        layer: 0,
        order: 0,
        pix: 0,
        count: 0,
      });
      expect(() => readTileHeader(buf)).toThrow(`unsupported catalog star tile header version ${badVersion}`);
    }
  });

  it('enforces kind, expected coordinate tuple, positionBits, and exact byteLength across core, detail, and astro decoders', () => {
    const coreBuf = buildCoreBuffer({
      layer: 1,
      order: 2,
      pix: 17,
      positionBits: 16,
      posU: [100],
      posV: [200],
      magCodes: [5000],
      colorCodes: [100],
      distanceCodes: [10001],
      flags: [0],
    });
    const detailBuf = buildDetailBuffer({
      layer: 1,
      order: 2,
      pix: 17,
      idHi: [0],
      idLo: [1],
      pmRaHalf: [0],
      pmDecHalf: [0],
      radialVelocityHalf: [0],
      distanceLowerCode: [0],
      distanceUpperCode: [0],
    });
    const astroBuf = buildAstroBuffer({
      layer: 1,
      order: 2,
      pix: 17,
      teff: [5000],
      teffLower: [4900],
      teffUpper: [5100],
      logLum: [0],
      logLumLower: [-10],
      logLumUpper: [10],
      a0: [0],
      a0Lower: [0],
      a0Upper: [0],
      ag: [0],
      ruweHalf: [0x3c00],
      parallaxErrorHalf: [0x3c00],
    });

    // Kind mismatches
    expect(() => decodeCoreTile(detailBuf)).toThrow('expected tile kind 1, found 2');
    expect(() => decodeCoreTile(astroBuf)).toThrow('expected tile kind 1, found 3');
    expect(() => decodeDetailTile(coreBuf)).toThrow('expected tile kind 2, found 1');
    expect(() => decodeDetailTile(astroBuf)).toThrow('expected tile kind 2, found 3');
    expect(() => decodeAstroTile(coreBuf)).toThrow('expected tile kind 3, found 1');
    expect(() => decodeAstroTile(detailBuf)).toThrow('expected tile kind 3, found 2');

    // Request coordinate tuple mismatches (layer, order, pix independently)
    for (const mismatch of [
      { layer: 9, order: 2, pix: 17 },
      { layer: 1, order: 3, pix: 17 },
      { layer: 1, order: 2, pix: 18 },
    ]) {
      expect(() => decodeCoreTile(coreBuf, mismatch)).toThrow(/does not match request/);
      expect(() => decodeDetailTile(detailBuf, mismatch)).toThrow(/does not match request/);
      expect(() => decodeAstroTile(astroBuf, mismatch)).toThrow(/does not match request/);
    }

    // Unsupported positionBits on core tiles
    for (const badBits of [0, 8, 24, 64]) {
      const buf = coreBuf.slice(0);
      new DataView(buf).setUint8(7, badBits);
      expect(() => decodeCoreTile(buf)).toThrow(`unsupported position width ${badBits}`);
    }

    // Truncated or overlong buffers
    expect(() => decodeCoreTile(coreBuf.slice(0, coreBuf.byteLength - 1))).toThrow(
      'core tile length does not match its header'
    );
    expect(() => decodeCoreTile(new ArrayBuffer(coreBuf.byteLength + 1))).toThrow();
    const paddedCore = new ArrayBuffer(coreBuf.byteLength + 2);
    new Uint8Array(paddedCore).set(new Uint8Array(coreBuf));
    expect(() => decodeCoreTile(paddedCore)).toThrow('core tile length does not match its header');

    const paddedDetail = new ArrayBuffer(detailBuf.byteLength + 2);
    new Uint8Array(paddedDetail).set(new Uint8Array(detailBuf));
    expect(() => decodeDetailTile(detailBuf.slice(0, detailBuf.byteLength - 1))).toThrow(
      'detail tile length does not match its header'
    );
    expect(() => decodeDetailTile(paddedDetail)).toThrow('detail tile length does not match its header');

    const paddedAstro = new ArrayBuffer(astroBuf.byteLength + 2);
    new Uint8Array(paddedAstro).set(new Uint8Array(astroBuf));
    expect(() => decodeAstroTile(astroBuf.slice(0, astroBuf.byteLength - 2))).toThrow(
      'astro tile length does not match its header'
    );
    expect(() => decodeAstroTile(paddedAstro)).toThrow('astro tile length does not match its header');
  });
});

describe('synthetic and fixture binary tile decoding', () => {
  it('decodes empty 0-star core (16-bit and 32-bit), detail, and astro tiles', () => {
    const emptyCore16 = decodeCoreTile(
      buildCoreBuffer({
        layer: 3,
        order: 3,
        pix: 0,
        positionBits: 16,
        posU: [],
        posV: [],
        magCodes: [],
        colorCodes: [],
        distanceCodes: [],
        flags: [],
      })
    );
    expect(emptyCore16.count).toBe(0);
    expect(emptyCore16.directions).toHaveLength(0);
    expect(emptyCore16.byteLength).toBe(32);

    const emptyCore32 = decodeCoreTile(
      buildCoreBuffer({
        layer: 0,
        order: 0,
        pix: 4,
        positionBits: 32,
        posU: [],
        posV: [],
        magCodes: [],
        colorCodes: [],
        distanceCodes: [],
        flags: [],
      })
    );
    expect(emptyCore32.positionBits).toBe(32);
    expect(emptyCore32.count).toBe(0);

    const emptyDetail = decodeDetailTile(
      buildDetailBuffer({
        layer: 0,
        order: 0,
        pix: 4,
        idHi: [],
        idLo: [],
        pmRaHalf: [],
        pmDecHalf: [],
        radialVelocityHalf: [],
        distanceLowerCode: [],
        distanceUpperCode: [],
      })
    );
    expect(emptyDetail.count).toBe(0);
    expect(emptyDetail.idHi).toHaveLength(0);

    const emptyAstro = decodeAstroTile(
      buildAstroBuffer({
        layer: 0,
        order: 0,
        pix: 4,
        teff: [],
        teffLower: [],
        teffUpper: [],
        logLum: [],
        logLumLower: [],
        logLumUpper: [],
        a0: [],
        a0Lower: [],
        a0Upper: [],
        ag: [],
        ruweHalf: [],
        parallaxErrorHalf: [],
      })
    );
    expect(emptyAstro.count).toBe(0);
    expect(emptyAstro.teffK).toHaveLength(0);
  });

  it('decodes 16-bit and 32-bit core tiles with exact quantized HEALPix unit vectors and defensive flags copy', () => {
    // 32-bit core tile at order 1, pix 7 (face 1, ix 1, iy 1)
    const posU32 = [0, 0x80000000, 0xffffffff];
    const posV32 = [0, 0x40000000, 0xffffffff];
    const rawCore32 = buildCoreBuffer({
      layer: 1,
      order: 1,
      pix: 7,
      positionBits: 32,
      posU: posU32,
      posV: posV32,
      magCodes: [540, 8500, 13999],
      colorCodes: [-32768, 0, 1250],
      distanceCodes: [0, 10001, 40001],
      flags: [0b0011_1111, 0b0000_0001, 0b0010_0010],
    });
    const core32: CatalogStarCoreTile = decodeCoreTile(rawCore32, { layer: 1, order: 1, pix: 7 });
    expect(core32.count).toBe(3);
    expect(core32.positionBits).toBe(32);
    expect(core32.byteLength).toBe(32 + 3 * 15);

    const { face, ix, iy } = nestToFaceIxIy(1, 7);
    for (let i = 0; i < 3; i++) {
      const u = (posU32[i]! + 0.5) / 2 ** 32;
      const v = (posV32[i]! + 0.5) / 2 ** 32;
      const expectedVec = faceXyToVec(face, (ix + u) / 2, (iy + v) / 2);
      expect(core32.directions[3 * i]).toBe(Math.fround(expectedVec[0]));
      expect(core32.directions[3 * i + 1]).toBe(Math.fround(expectedVec[1]));
      expect(core32.directions[3 * i + 2]).toBe(Math.fround(expectedVec[2]));
      expect(
        Math.hypot(core32.directions[3 * i]!, core32.directions[3 * i + 1]!, core32.directions[3 * i + 2]!)
      ).toBeCloseTo(1, 6);
    }

    expect(core32.magnitude[0]).toBeCloseTo(-1.46, 5);
    expect(core32.magnitude[1]).toBeCloseTo(6.5, 5);
    expect(core32.colorIndex[0]).toBeNaN();
    expect(core32.colorIndex[1]).toBe(0);
    expect(core32.colorIndex[2]).toBeCloseTo(1.25, 5);
    expect(core32.distancePc[0]).toBeNaN();
    expect(core32.distancePc[1]).toBeCloseTo(1, 5);
    expect(core32.distancePc[2]).toBeCloseTo(1000, 3);

    // Mutating source buffer after decode must not mutate core32.flags
    new Uint8Array(rawCore32).fill(0xff);
    expect(Array.from(core32.flags)).toEqual([0b0011_1111, 0b0000_0001, 0b0010_0010]);
  });

  it('decodes distinct values in every column of detail and astro tiles without column transposition', () => {
    const detailBuf = buildDetailBuffer({
      layer: 2,
      order: 3,
      pix: 333,
      idHi: [0x11111111, 0x22222222],
      idLo: [0x33333333, 0x44444444],
      pmRaHalf: [0x3c00, 0x7e00], // 1.0, NaN
      pmDecHalf: [0xc000, 0x3800], // -2.0, 0.5
      radialVelocityHalf: [0x4200, 0xc400], // 3.0, -4.0
      distanceLowerCode: [10, 0],
      distanceUpperCode: [20, 255],
    });
    const detail = decodeDetailTile(detailBuf, { layer: 2, order: 3, pix: 333 });
    expect(detail.byteLength).toBe(32 + 2 * 16);
    expect(Array.from(detail.idHi)).toEqual([0x11111111, 0x22222222]);
    expect(Array.from(detail.idLo)).toEqual([0x33333333, 0x44444444]);
    expect(halfToFloat(detail.pmRaHalf[0]!)).toBe(1);
    expect(halfToFloat(detail.pmRaHalf[1]!)).toBeNaN();
    expect(halfToFloat(detail.pmDecHalf[0]!)).toBe(-2);
    expect(halfToFloat(detail.pmDecHalf[1]!)).toBe(0.5);
    expect(halfToFloat(detail.radialVelocityHalf[0]!)).toBe(3);
    expect(halfToFloat(detail.radialVelocityHalf[1]!)).toBe(-4);
    expect(Array.from(detail.distanceLowerCode)).toEqual([10, 0]);
    expect(Array.from(detail.distanceUpperCode)).toEqual([20, 255]);

    const astroBuf = buildAstroBuffer({
      layer: 2,
      order: 3,
      pix: 333,
      teff: [5100, 0],
      teffLower: [5000, 0],
      teffUpper: [5200, 0],
      logLum: [1000, -32768],
      logLumLower: [900, -32768],
      logLumUpper: [1100, -32768],
      a0: [120, 65535],
      a0Lower: [110, 65535],
      a0Upper: [130, 65535],
      ag: [95, 0],
      ruweHalf: [0x3e00, 0x7e00], // 1.5, NaN
      parallaxErrorHalf: [0x3800, 0x3400], // 0.5, 0.25
    });
    const astro: CatalogStarAstroTile = decodeAstroTile(astroBuf, { layer: 2, order: 3, pix: 333 });
    expect(astro.byteLength).toBe(32 + 2 * 24);
    expect(astro.teffK[0]).toBe(5100);
    expect(astro.teffLowerK[0]).toBe(5000);
    expect(astro.teffUpperK[0]).toBe(5200);
    expect(astro.luminositySolar[0]).toBeCloseTo(10, 5);
    expect(astro.luminosityLowerSolar[0]).toBeCloseTo(10 ** 0.9, 5);
    expect(astro.luminosityUpperSolar[0]).toBeCloseTo(10 ** 1.1, 5);
    expect(astro.a0Mag[0]).toBeCloseTo(0.12, 6);
    expect(astro.a0LowerMag[0]).toBeCloseTo(0.11, 6);
    expect(astro.a0UpperMag[0]).toBeCloseTo(0.13, 6);
    expect(astro.agMag[0]).toBeCloseTo(0.095, 6);
    expect(astro.ruwe[0]).toBe(1.5);
    expect(astro.parallaxErrorMas[0]).toBe(0.5);

    expect(astro.teffK[1]).toBeNaN();
    expect(astro.teffLowerK[1]).toBeNaN();
    expect(astro.teffUpperK[1]).toBeNaN();
    expect(astro.luminositySolar[1]).toBeNaN();
    expect(astro.luminosityLowerSolar[1]).toBeNaN();
    expect(astro.luminosityUpperSolar[1]).toBeNaN();
    expect(astro.a0Mag[1]).toBeNaN();
    expect(astro.a0LowerMag[1]).toBeNaN();
    expect(astro.a0UpperMag[1]).toBeNaN();
    expect(astro.agMag[1]).toBe(0);
    expect(astro.ruwe[1]).toBeNaN();
    expect(astro.parallaxErrorMas[1]).toBe(0.25);
  });

  it('decodes all columns and headers of the shared Python fixture tiles', () => {
    const expected = JSON.parse(fs.readFileSync(repoFile('tests/fixtures/catalog_star_tiles/expected.json'), 'utf8'));
    const coreRaw = readBuffer('tests/fixtures/catalog_star_tiles/core.bin');
    const detailRaw = readBuffer('tests/fixtures/catalog_star_tiles/detail.bin');
    const astroRaw = readBuffer('tests/fixtures/catalog_star_tiles/astro.bin');

    expect(readTileHeader(coreRaw)).toEqual({
      kind: TILE_KIND_CORE,
      positionBits: 16,
      layer: expected.layer,
      order: expected.order,
      pix: expected.pix,
      count: expected.stars.length,
    });
    expect(readTileHeader(detailRaw)).toEqual({
      kind: TILE_KIND_DETAIL,
      positionBits: 0,
      layer: expected.layer,
      order: expected.order,
      pix: expected.pix,
      count: expected.stars.length,
    });
    expect(readTileHeader(astroRaw)).toEqual({
      kind: TILE_KIND_ASTRO,
      positionBits: 0,
      layer: expected.layer,
      order: expected.order,
      pix: expected.pix,
      count: expected.stars.length,
    });

    const core = decodeCoreTile(coreRaw, { layer: expected.layer, order: expected.order, pix: expected.pix });
    const detail = decodeDetailTile(detailRaw, { layer: expected.layer, order: expected.order, pix: expected.pix });
    const astro = decodeAstroTile(astroRaw, { layer: expected.layer, order: expected.order, pix: expected.pix });

    expected.stars.forEach((star: Record<string, number | string | null>, i: number) => {
      const dir: Vec3 = [core.directions[3 * i]!, core.directions[3 * i + 1]!, core.directions[3 * i + 2]!];
      const truth = raDecDegToVec(star.raDeg as number, star.decDeg as number);
      expect(angleBetween(dir, truth) / ARCSEC).toBeLessThan(0.5);
      expect(catalogOf(core.flags[i]!)).toBe('gaia-dr3');
      expect(distanceKindOf(core.flags[i]!)).toBe('bailer-jones-2021-photogeometric');
      expect(magnitudeBandOf(core.flags[i]!)).toBe('G');
      expect(usesBMinusV(core.flags[i]!)).toBe(false);
      expect(isNamed(core.flags[i]!)).toBe(i === 1);

      expect(catalogIdAt(detail, i)).toBe(star.catalogId);
      expect(halfToFloat(detail.pmRaHalf[i]!)).toBeCloseTo(star.pmRaMasYr as number, 1);
      expect(halfToFloat(detail.pmDecHalf[i]!)).toBeCloseTo(star.pmDecMasYr as number, 1);
      expect(halfToFloat(detail.radialVelocityHalf[i]!)).toBeCloseTo(star.radialVelocityKmS as number, 1);
      const bounds = decodeDistanceBounds(
        core.distancePc[i]!,
        detail.distanceLowerCode[i]!,
        detail.distanceUpperCode[i]!
      );
      expect(Math.abs(bounds.lowerPc / (star.distanceLowerPc as number) - 1)).toBeLessThan(0.002);
      expect(Math.abs(bounds.upperPc / (star.distanceUpperPc as number) - 1)).toBeLessThan(0.002);

      expect(astro.ruwe[i]).toBeCloseTo(star.ruwe as number, 3);
      expect(astro.parallaxErrorMas[i]).toBeCloseTo(star.parallaxErrorMas as number, 4);
    });
  });
});
