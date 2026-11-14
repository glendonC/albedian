import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { icrsToScene } from '../../catalogStars/CatalogStarField.js';
import type { Vec3 } from '../../catalogStars/healpix.js';
import * as skyRegistrationExtensionless from '../skyRegistration';
import * as skyRegistrationModule from '../skyRegistration.js';
import {
  ICRS_TO_GALACTIC,
  SKY_PANORAMA_REGISTRATION_FORMAT,
  SKY_PANORAMA_REGISTRATION_URL,
  applyRows,
  loadPanoramaRegistration,
  multiplyRows,
  panoramaUv,
  parsePanoramaRegistration,
  plateCarreeUv,
  sampleOffsetTable,
  sceneToIcrsRows,
  sceneToPanoramaRows,
  transposeRows,
  type Mat3Rows,
  type PanoramaOffsetTable,
  type PanoramaRegistration,
} from '../skyRegistration.js';

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const IDENTITY_ROWS: Mat3Rows = [1, 0, 0, 0, 1, 0, 0, 0, 1];

function det3(m: Mat3Rows): number {
  return (
    m[0] * (m[4] * m[8] - m[5] * m[7]) -
    m[1] * (m[3] * m[8] - m[5] * m[6]) +
    m[2] * (m[3] * m[7] - m[4] * m[6])
  );
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary);
}

const finiteScalar = fc.double({ min: -100, max: 100, noNaN: true, noDefaultInfinity: true });
const mat3Arb: fc.Arbitrary<Mat3Rows> = fc
  .tuple(
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar,
    finiteScalar
  )
  .map((entries): Mat3Rows => entries);

const vec3Arb: fc.Arbitrary<Vec3> = fc.tuple(finiteScalar, finiteScalar, finiteScalar);

describe('skyRegistration public module contract', () => {
  it('exposes the exact 13 runtime exports with zero leaked internal helpers', () => {
    expect(Object.keys(skyRegistrationModule).sort()).toEqual([
      'ICRS_TO_GALACTIC',
      'SKY_PANORAMA_REGISTRATION_FORMAT',
      'SKY_PANORAMA_REGISTRATION_URL',
      'applyRows',
      'loadPanoramaRegistration',
      'multiplyRows',
      'panoramaUv',
      'parsePanoramaRegistration',
      'plateCarreeUv',
      'sampleOffsetTable',
      'sceneToIcrsRows',
      'sceneToPanoramaRows',
      'transposeRows',
    ]);
    expect(Object.keys(skyRegistrationExtensionless).sort()).toEqual(Object.keys(skyRegistrationModule).sort());
    for (const key of Object.keys(skyRegistrationModule) as Array<keyof typeof skyRegistrationModule>) {
      expect(skyRegistrationExtensionless[key]).toBe(skyRegistrationModule[key]);
    }
  });

  it('preserves public constants and function arities', () => {
    expect(SKY_PANORAMA_REGISTRATION_URL).toBe('/textures/eso0932a_registration.json');
    expect(SKY_PANORAMA_REGISTRATION_FORMAT).toBe('albedian.sky-panorama-registration');
    expect(ICRS_TO_GALACTIC).toEqual([
      -0.0548755604162154, -0.873437090234885, -0.4838350155487132, 0.4941094278755837, -0.4448296299600112,
      0.7469822444972189, -0.8676661490190047, -0.1980763734312015, 0.4559837761750669,
    ]);
    expect(multiplyRows.length).toBe(2);
    expect(transposeRows.length).toBe(1);
    expect(applyRows.length).toBe(2);
    expect(sceneToIcrsRows.length).toBe(0);
    expect(sceneToPanoramaRows.length).toBe(1);
    expect(plateCarreeUv.length).toBe(1);
    expect(sampleOffsetTable.length).toBe(3);
    expect(panoramaUv.length).toBe(2);
    expect(parsePanoramaRegistration.length).toBe(1);
    expect(loadPanoramaRegistration.length).toBe(0);
  });
});

describe('skyRegistration row-major 3x3 matrix operations', () => {
  it('computes asymmetric multiplyRows, transposeRows and applyRows in row-major order', () => {
    const a: Mat3Rows = [1, 2, 3, 4, 5, 6, 7, 8, 9];
    const b: Mat3Rows = [2, 0, 1, 3, 5, 0, 0, 4, 6];
    expect(transposeRows(a)).toEqual([1, 4, 7, 2, 5, 8, 3, 6, 9]);
    expect(applyRows(a, [10, 20, 30])).toEqual([140, 320, 500]);
    expect(multiplyRows(a, b)).toEqual([8, 22, 19, 23, 49, 40, 38, 76, 61]);
    expect(multiplyRows(b, a)).toEqual([9, 12, 15, 23, 31, 39, 58, 68, 78]);
  });

  it('keeps ICRS_TO_GALACTIC and sceneToIcrsRows right-handed orthonormal rotations', () => {
    for (const rot of [ICRS_TO_GALACTIC, sceneToIcrsRows()]) {
      const rTr = multiplyRows(transposeRows(rot), rot);
      const rRt = multiplyRows(rot, transposeRows(rot));
      for (let i = 0; i < 9; i++) {
        expect(rTr[i]).toBeCloseTo(IDENTITY_ROWS[i]!, 12);
        expect(rRt[i]).toBeCloseTo(IDENTITY_ROWS[i]!, 12);
      }
      expect(det3(rot)).toBeCloseTo(1, 12);
    }

    const basisVectors: Vec3[] = [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ];
    for (const basis of basisVectors) {
      const mounted = icrsToScene(basis);
      const back = applyRows(sceneToIcrsRows(), [mounted.x, mounted.y, mounted.z]);
      expect(back[0]).toBeCloseTo(basis[0], 14);
      expect(back[1]).toBeCloseTo(basis[1], 14);
      expect(back[2]).toBeCloseTo(basis[2], 14);
    }
  });

  it('composes sceneToPanoramaRows as galacticToPanorama * (ICRS_TO_GALACTIC * sceneToIcrsRows)', () => {
    const customGalacticToPanorama: Mat3Rows = [0, -1, 0, 0, 0, 1, -1, 0, 0];
    const reg: PanoramaRegistration = {
      imageWidth: 400,
      imageHeight: 200,
      galacticToPanorama: customGalacticToPanorama,
      yOffsetPx: 0,
      offsetTable: { width: 2, height: 2, scale: 1, bias: 0, codes: new Uint8Array(8) },
    };
    const expected = multiplyRows(customGalacticToPanorama, multiplyRows(ICRS_TO_GALACTIC, sceneToIcrsRows()));
    expect(sceneToPanoramaRows(reg)).toEqual(expected);
  });

  it('PBT-SKY-REG-MOD-1: satisfies associativity, transpose involution, anti-homomorphism and vector action', () => {
    fc.assert(
      fc.property(mat3Arb, mat3Arb, mat3Arb, vec3Arb, (a, b, c, v) => {
        expect(transposeRows(transposeRows(a))).toEqual(a);
        const leftId = multiplyRows(IDENTITY_ROWS, a);
        const rightId = multiplyRows(a, IDENTITY_ROWS);
        const idVec = applyRows(IDENTITY_ROWS, v);
        for (let i = 0; i < 9; i++) {
          expect(leftId[i]! === a[i]!).toBe(true);
          expect(rightId[i]! === a[i]!).toBe(true);
        }
        for (let k = 0; k < 3; k++) {
          expect(idVec[k]! === v[k]!).toBe(true);
        }

        const abThenC = multiplyRows(multiplyRows(a, b), c);
        const aThenBc = multiplyRows(a, multiplyRows(b, c));
        const abT = transposeRows(multiplyRows(a, b));
        const bTaT = multiplyRows(transposeRows(b), transposeRows(a));
        for (let i = 0; i < 9; i++) {
          expect(Math.abs(abThenC[i]! - aThenBc[i]!)).toBeLessThan(1e-6);
          expect(Math.abs(abT[i]! - bTaT[i]!)).toBeLessThan(1e-9);
        }

        const abv = applyRows(multiplyRows(a, b), v);
        const aBv = applyRows(a, applyRows(b, v));
        for (let k = 0; k < 3; k++) {
          expect(Math.abs(abv[k]! - aBv[k]!)).toBeLessThan(1e-6);
        }
      }),
      {
        numRuns: 200,
        seed: SEED,
        examples: [[IDENTITY_ROWS, IDENTITY_ROWS, IDENTITY_ROWS, [0, 0, 0]]],
      }
    );
  });
});

describe('skyRegistration plateCarreeUv, sampleOffsetTable and panoramaUv', () => {
  it('projects plateCarreeUv with east-left u in [0, 1), seam wrap at -x and clamped out-of-unit z', () => {
    expect(plateCarreeUv([1, 0, 0])).toEqual([0.5, 0.5]);
    expect(plateCarreeUv([3.5, 0, 0])).toEqual([0.5, 0.5]);
    expect(plateCarreeUv([0, 1, 0])[0]).toBeCloseTo(0.25, 14);
    expect(plateCarreeUv([0, -1, 0])[0]).toBeCloseTo(0.75, 14);

    // Both +pi and -pi wrap to u = 0 in [0, 1)
    expect(plateCarreeUv([-1, 0, 0])).toEqual([0, 0.5]);
    expect(plateCarreeUv([-1, -0, 0])).toEqual([0, 0.5]);
    const nearNegPi = plateCarreeUv([-1, -1e-6, 0])[0];
    expect(nearNegPi).toBeGreaterThan(0.999999);
    expect(nearNegPi).toBeLessThan(1);

    // Out-of-unit z values clamp to [-1, 1] instead of returning NaN from Math.asin
    expect(plateCarreeUv([0, 0, 1.75])).toEqual([0.5, 1]);
    expect(plateCarreeUv([0, 0, -2.25])).toEqual([0.5, 0]);
  });

  it('interpolates sampleOffsetTable bilinearly with periodic u seam wrap and clamped v edges', () => {
    // 3x2 table with distinct east and north codes at each texel
    const codes = new Uint8Array([
      // row y = 0: x = 0, 1, 2
      110, 102, 130, 114, 170, 126,
      // row y = 1: x = 0, 1, 2
      150, 138, 190, 150, 230, 162,
    ]);
    const table: PanoramaOffsetTable = {
      width: 3,
      height: 2,
      scale: 10,
      bias: 100,
      codes,
    };
    const decoded = (x: number, y: number, k: 0 | 1) => (codes[2 * (y * 3 + x) + k]! - 100) / 10;

    // Interior fractional point: fx = 0.25 (between x=0 and x=1), fy = 0.75 (between y=0 and y=1)
    const uInterior = (0.25 + 0.5) / 3;
    const vInterior = (0.75 + 0.5) / 2;
    const interior = sampleOffsetTable(table, uInterior, vInterior);
    for (const k of [0, 1] as const) {
      const b = decoded(0, 0, k) * 0.75 + decoded(1, 0, k) * 0.25;
      const t = decoded(0, 1, k) * 0.75 + decoded(1, 1, k) * 0.25;
      expect(interior[k]).toBeCloseTo(b * 0.25 + t * 0.75, 14);
    }

    // Across horizontal seam: u = 0.1 / 3 gives fx = -0.4 -> x0 = -1 (wraps to x = 2), xb = 0, tx = 0.6
    const uSeam = 0.1 / 3;
    const seamSample = sampleOffsetTable(table, uSeam, 0.25);
    expect(seamSample[0]).toBeCloseTo(decoded(2, 0, 0) * 0.4 + decoded(0, 0, 0) * 0.6, 14);
    expect(seamSample[1]).toBeCloseTo(decoded(2, 0, 1) * 0.4 + decoded(0, 0, 1) * 0.6, 14);

    // Periodic in u for negative and >1 coordinates
    for (const shift of [-3, -1, 1, 2]) {
      const wrapped = sampleOffsetTable(table, uSeam + shift, vInterior);
      expect(wrapped[0]).toBeCloseTo(sampleOffsetTable(table, uSeam, vInterior)[0], 13);
      expect(wrapped[1]).toBeCloseTo(sampleOffsetTable(table, uSeam, vInterior)[1], 13);
    }

    // Vertical clamp at bottom (v <= 0.25) and top (v >= 0.75)
    for (const vBottom of [-1.5, 0, 0.25]) {
      const bottom = sampleOffsetTable(table, 0.5 / 3, vBottom);
      expect(bottom[0]).toBeCloseTo(decoded(0, 0, 0), 14);
      expect(bottom[1]).toBeCloseTo(decoded(0, 0, 1), 14);
    }
    for (const vTop of [0.75, 1, 2.5]) {
      const top = sampleOffsetTable(table, 0.5 / 3, vTop);
      expect(top[0]).toBeCloseTo(decoded(0, 1, 0), 14);
      expect(top[1]).toBeCloseTo(decoded(0, 1, 1), 14);
    }
  });

  it('applies east / cosLat, north - yOffsetPx and the 1e-3 polar cosLat floor in panoramaUv', () => {
    // Choose galacticToPanorama = (ICRS_TO_GALACTIC * sceneToIcrsRows)^T so sceneToPanoramaRows === I
    const identityFrame = transposeRows(multiplyRows(ICRS_TO_GALACTIC, sceneToIcrsRows()));
    // Constant offset table: east = (140 - 100) / 10 = +4 px, north = (160 - 100) / 10 = +6 px
    const codes = new Uint8Array([140, 160, 140, 160, 140, 160, 140, 160]);
    const reg: PanoramaRegistration = {
      imageWidth: 1000,
      imageHeight: 500,
      galacticToPanorama: identityFrame,
      yOffsetPx: 2,
      offsetTable: { width: 2, height: 2, scale: 10, bias: 100, codes },
    };

    // Equator lon = 0: u = 0.5 - 4 / (1000 * 1) = 0.496, v = 0.5 + (6 - 2) / 500 = 0.508
    const eq = panoramaUv([1, 0, 0], reg);
    expect(eq[0]).toBeCloseTo(0.496, 12);
    expect(eq[1]).toBeCloseTo(0.508, 12);

    // Latitude 60 deg (cosLat = 0.5): east displacement doubles to 4 / (1000 * 0.5) = 0.008
    const lat60 = panoramaUv([0.5, 0, Math.sqrt(3) / 2], reg);
    expect(lat60[0]).toBeCloseTo(0.492, 12);
    expect(lat60[1]).toBeCloseTo(5 / 6 + 4 / 500, 12);

    // Off-meridian latitude 60 deg with p[1] != 0: hypot(0.3, 0.4) = 0.5 != |p[0]| = 0.3
    const offMeridianVec: Vec3 = [0.3, 0.4, Math.sqrt(0.75)];
    const [uBaseOff, vBaseOff] = plateCarreeUv(offMeridianVec);
    const offMeridian = panoramaUv(offMeridianVec, reg);
    expect(offMeridian[0]).toBeCloseTo(uBaseOff - 4 / (1000 * 0.5), 12);
    expect(offMeridian[1]).toBeCloseTo(vBaseOff + 4 / 500, 12);

    // Exact pole (hypot < 1e-15) and inside polar floor (hypot = 5e-4 < 1e-3): cosLat floors at 1e-3, u is not wrapped
    const polePanorama = applyRows(sceneToPanoramaRows(reg), [0, 0, 1]);
    expect(Math.hypot(polePanorama[0], polePanorama[1])).toBeLessThan(1e-14);
    const northPole = panoramaUv([0, 0, 1], reg);
    expect(northPole[0] - plateCarreeUv(polePanorama)[0]).toBeCloseTo(-4 / (1000 * 1e-3), 12);
    expect(northPole[1]).toBeCloseTo(1 + 4 / 500, 12);

    const subFloorPole = panoramaUv([5e-4, 0, Math.sqrt(1 - 25e-8)], reg);
    expect(subFloorPole[0]).toBeCloseTo(0.5 - 4 / (1000 * 1e-3), 12);

    // Above polar floor (hypot = 2e-3 > 1e-3): uses actual hypot = 2e-3 -> shift = 4 / 2 = 2
    const aboveFloorPole = panoramaUv([2e-3, 0, Math.sqrt(1 - 4e-6)], reg);
    expect(aboveFloorPole[0]).toBeCloseTo(0.5 - 4 / (1000 * 2e-3), 12);
  });
});

describe('skyRegistration parsePanoramaRegistration and loadPanoramaRegistration', () => {
  const sampleBytes = new Uint8Array([148, 0, 1, 127, 128, 200, 255, 42, 99, 10, 20, 30]);
  const validNestedDoc = () => ({
    format: SKY_PANORAMA_REGISTRATION_FORMAT,
    formatVersion: 1,
    image: { width: 6000, height: 3000 },
    galacticToPanorama: [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ],
    yOffsetPx: -3.25,
    offsetTable: {
      width: 3,
      height: 2,
      scale: 32,
      bias: 128,
      data: bytesToBase64(sampleBytes),
    },
  });

  it('parses both nested 3x3 and flat 9-entry galacticToPanorama matrices and decodes 8-bit codes', () => {
    const fromNested = parsePanoramaRegistration(validNestedDoc());
    const fromFlat = parsePanoramaRegistration({
      ...validNestedDoc(),
      galacticToPanorama: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    });
    expect(fromNested).toEqual(fromFlat);
    expect(fromNested.imageWidth).toBe(6000);
    expect(fromNested.imageHeight).toBe(3000);
    expect(fromNested.yOffsetPx).toBe(-3.25);
    expect(fromNested.galacticToPanorama).toEqual(IDENTITY_ROWS);
    expect(fromNested.offsetTable.codes).toBeInstanceOf(Uint8Array);
    expect(Array.from(fromNested.offsetTable.codes)).toEqual(Array.from(sampleBytes));
  });

  it('throws exact version error message when format or formatVersion is invalid', () => {
    for (const invalid of [
      null,
      undefined,
      42,
      {},
      { ...validNestedDoc(), format: 'wrong.format' },
      { ...validNestedDoc(), formatVersion: 0 },
      { ...validNestedDoc(), formatVersion: 2 },
      { ...validNestedDoc(), formatVersion: '1' },
    ]) {
      expect(() => parsePanoramaRegistration(invalid)).toThrow('not a version 1 sky panorama registration');
    }
  });

  it('throws exact missing-fields error message when any numeric or string field is missing or non-finite', () => {
    const base = validNestedDoc();
    const malformedCases: unknown[] = [
      { ...base, galacticToPanorama: null },
      { ...base, galacticToPanorama: [1, 2, 3, 4, 5, 6, 7, 8] },
      { ...base, galacticToPanorama: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] },
      { ...base, galacticToPanorama: [1, 0, 0, 0, Number.NaN, 0, 0, 0, 1] },
      { ...base, galacticToPanorama: [1, 0, 0, 0, Number.POSITIVE_INFINITY, 0, 0, 0, 1] },
      { ...base, image: undefined },
      { ...base, image: { width: Number.NaN, height: 3000 } },
      { ...base, image: { width: 6000, height: Number.NEGATIVE_INFINITY } },
      { ...base, yOffsetPx: Number.NaN },
      { ...base, yOffsetPx: '0' },
      { ...base, offsetTable: undefined },
      { ...base, offsetTable: { ...base.offsetTable, width: Number.NaN } },
      { ...base, offsetTable: { ...base.offsetTable, height: Number.POSITIVE_INFINITY } },
      { ...base, offsetTable: { ...base.offsetTable, scale: '32' } },
      { ...base, offsetTable: { ...base.offsetTable, bias: undefined } },
      { ...base, offsetTable: { ...base.offsetTable, data: 123 } },
    ];
    for (const doc of malformedCases) {
      expect(() => parsePanoramaRegistration(doc)).toThrow('sky panorama registration is missing fields');
    }
  });

  it('throws exact size error message when decoded offsetTable byte count differs from width * height * 2', () => {
    const base = validNestedDoc();
    expect(() =>
      parsePanoramaRegistration({
        ...base,
        offsetTable: { ...base.offsetTable, data: bytesToBase64(sampleBytes.slice(0, 11)) },
      })
    ).toThrow('sky panorama offset table has the wrong size');

    expect(() =>
      parsePanoramaRegistration({
        ...base,
        offsetTable: { ...base.offsetTable, data: bytesToBase64(new Uint8Array(13)) },
      })
    ).toThrow('sky panorama offset table has the wrong size');
  });

  it('loads default SKY_PANORAMA_REGISTRATION_URL via unbound fetchImpl and reports exact HTTP status on failure', async () => {
    const seenUrls: string[] = [];
    const seenContexts: unknown[] = [];
    const okFetch = async function (this: unknown, input: RequestInfo | URL): Promise<Response> {
      seenContexts.push(this);
      seenUrls.push(String(input));
      return new Response(JSON.stringify(validNestedDoc()), { status: 200 });
    } as typeof fetch;

    const loaded = await loadPanoramaRegistration(undefined, okFetch);
    expect(seenUrls).toEqual([SKY_PANORAMA_REGISTRATION_URL]);
    expect(seenContexts).toEqual([undefined]);
    expect(loaded.imageWidth).toBe(6000);
    expect(loaded.offsetTable.codes).toEqual(sampleBytes);

    const notFoundFetch = (async () => new Response('', { status: 404 })) as unknown as typeof fetch;
    await expect(loadPanoramaRegistration('/custom.json', notFoundFetch)).rejects.toThrow(
      'sky panorama registration: HTTP 404'
    );

    const unavailableFetch = (async () => new Response('', { status: 503 })) as unknown as typeof fetch;
    await expect(loadPanoramaRegistration('/custom.json', unavailableFetch)).rejects.toThrow(
      'sky panorama registration: HTTP 503'
    );
  });
});
