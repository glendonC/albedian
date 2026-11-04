import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { icrsToScene } from '../../catalogStars/CatalogStarField';
import { raDecDegToVec, vecToRaDecDeg, type Vec3 } from '../../catalogStars/healpix';
import {
  ICRS_TO_GALACTIC,
  applyRows,
  loadPanoramaRegistration,
  multiplyRows,
  panoramaUv,
  parsePanoramaRegistration,
  plateCarreeUv,
  sampleOffsetTable,
  sceneToIcrsRows,
  starMapUv,
  transposeRows,
  type PanoramaOffsetTable,
} from '../skyRegistration';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const registrationJson = JSON.parse(
  fs.readFileSync(repoFile('apps/web/public/textures/eso0932a_registration.json'), 'utf8')
) as {
  icrsToGalactic: number[][];
  image: { width: number; height: number; texelArcsec: number };
  checkStars: { name: string; raDeg: number; decDeg: number; modelX: number; modelY: number; measuredX: number; measuredY: number }[];
};
const registration = parsePanoramaRegistration(registrationJson);

const ARCSEC = Math.PI / (180 * 3600);
const toScene = (raDeg: number, decDeg: number): Vec3 => {
  const v = icrsToScene(raDecDegToVec(raDeg, decDeg));
  return [v.x, v.y, v.z];
};
const angle = (a: Vec3, b: Vec3) => Math.atan2(Math.hypot(a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]);
const unitVector = fc
  .tuple(fc.double({ min: -1, max: 1, noNaN: true }), fc.double({ min: -1, max: 1, noNaN: true }), fc.double({ min: -1, max: 1, noNaN: true }))
  .filter(([x, y, z]) => Math.hypot(x, y, z) > 0.1)
  .map(([x, y, z]): Vec3 => {
    const n = Math.hypot(x, y, z);
    return [x / n, y / n, z / n];
  });

describe('sky registration: ICRS to galactic', () => {
  const galactic = (raDeg: number, decDeg: number) => applyRows(ICRS_TO_GALACTIC, raDecDegToVec(raDeg, decDeg));

  it('is a rotation', () => {
    const identity = multiplyRows(ICRS_TO_GALACTIC, transposeRows(ICRS_TO_GALACTIC));
    [1, 0, 0, 0, 1, 0, 0, 0, 1].forEach((v, i) => expect(identity[i]).toBeCloseTo(v, 12));
  });

  it('puts the defining pole, node and centre where the Hipparcos frame does', () => {
    // North galactic pole at ICRS (192.85948, +27.12825) deg
    expect(angle(galactic(192.85948, 27.12825), [0, 0, 1])).toBeLessThan(0.1 * ARCSEC);
    // The galactic plane crosses the equator northward at RA = 192.85948 + 90 deg, longitude 32.93192 deg
    const node = galactic(282.85948, 0);
    expect(angle(node, [Math.cos((32.93192 * Math.PI) / 180), Math.sin((32.93192 * Math.PI) / 180), 0])).toBeLessThan(0.1 * ARCSEC);
    // Galactic centre (l = 0, b = 0) at RA 17h45m37.22s, Dec -28d56m10.2s
    expect(angle(galactic(266.405083, -28.936175), [1, 0, 0])).toBeLessThan(1 * ARCSEC);
  });

  it('matches the matrix the Python fit used (services/science/sky_registration.py)', () => {
    registrationJson.icrsToGalactic.flat().forEach((v, i) => expect(ICRS_TO_GALACTIC[i]).toBe(v));
  });
});

describe('sky registration: scene axes and plate carree', () => {
  it('PBT-SKY-REG-1: scene to ICRS undoes the rotation the catalog star mounts carry', () => {
    fc.assert(
      fc.property(unitVector, (v) => {
        const s = icrsToScene(v);
        expect(angle(applyRows(sceneToIcrsRows(), [s.x, s.y, s.z]), v)).toBeLessThan(1e-12);
      }),
      { numRuns: 300 }
    );
  });

  it('puts longitude 0 at the centre, east to the left and north at the top', () => {
    expect(plateCarreeUv([1, 0, 0])).toEqual([0.5, 0.5]);
    expect(plateCarreeUv([0, 1, 0])[0]).toBeCloseTo(0.25, 12);
    expect(plateCarreeUv([0, -1, 0])[0]).toBeCloseTo(0.75, 12);
    expect(plateCarreeUv([0, 0, 1])[1]).toBeCloseTo(1, 12);
    expect(plateCarreeUv([0, 0, -1])[1]).toBeCloseTo(0, 12);
  });

  it('PBT-SKY-REG-2: plate carree coordinates invert back to the same direction', () => {
    fc.assert(
      fc.property(unitVector, (v) => {
        const [u, w] = plateCarreeUv(v);
        expect(u).toBeGreaterThanOrEqual(0);
        expect(u).toBeLessThan(1);
        const lon = (0.5 - u) * 2 * Math.PI;
        const lat = (w - 0.5) * Math.PI;
        // asin loses precision next to the poles, where z rounds to +-1: about 1e-8 rad in float64
        expect(angle([Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)], v)).toBeLessThan(1e-7);
      }),
      { numRuns: 300 }
    );
  });

  it('samples the NASA SVS star map by right ascension with 0h at the centre, increasing to the left', () => {
    const [u0, v0] = starMapUv(toScene(0, 0));
    expect(u0).toBeCloseTo(0.5, 12);
    expect(v0).toBeCloseTo(0.5, 12);
    expect(starMapUv(toScene(90, 0))[0]).toBeCloseTo(0.25, 12);
    expect(starMapUv(toScene(270, 0))[0]).toBeCloseTo(0.75, 12);
    expect(starMapUv(toScene(101.287, -16.716))[1]).toBeCloseTo(0.5 - 16.716 / 180, 12);
  });
});

describe('sky registration: offset table', () => {
  const table: PanoramaOffsetTable = {
    width: 4,
    height: 3,
    scale: 32,
    bias: 128,
    codes: Uint8Array.from({ length: 24 }, (_, i) => (i * 37) % 256),
  };
  const texel = (x: number, y: number) =>
    [0, 1].map((k) => (table.codes[2 * (y * table.width + x) + k]! - table.bias) / table.scale);

  it('returns texel values at texel centres, repeats in u and clamps in v', () => {
    for (let y = 0; y < table.height; y++) {
      for (let x = 0; x < table.width; x++) {
        const s = sampleOffsetTable(table, (x + 0.5) / table.width, (y + 0.5) / table.height);
        expect(s[0]).toBeCloseTo(texel(x, y)[0]!, 12);
        expect(s[1]).toBeCloseTo(texel(x, y)[1]!, 12);
      }
    }
    // u = 0 lies halfway between the last and the first column
    const seam = sampleOffsetTable(table, 0, 0.5 / table.height);
    expect(seam[0]).toBeCloseTo((texel(3, 0)[0]! + texel(0, 0)[0]!) / 2, 12);
    expect(sampleOffsetTable(table, 0.125, 0)[1]).toBeCloseTo(texel(0, 0)[1]!, 12);
    expect(sampleOffsetTable(table, 0.125, 1)[1]).toBeCloseTo(texel(0, 2)[1]!, 12);
  });

  it('PBT-SKY-REG-3: a bilinear sample stays within the range of the table', () => {
    const values = Array.from(table.codes, (c) => (c - table.bias) / table.scale);
    fc.assert(
      fc.property(fc.double({ min: -2, max: 3, noNaN: true }), fc.double({ min: -0.5, max: 1.5, noNaN: true }), (u, v) => {
        for (const s of sampleOffsetTable(table, u, v)) {
          expect(s).toBeGreaterThanOrEqual(Math.min(...values) - 1e-12);
          expect(s).toBeLessThanOrEqual(Math.max(...values) + 1e-12);
        }
      }),
      { numRuns: 300 }
    );
  });
});

describe('sky registration: ESO panorama solution (eso0932a_registration.json)', () => {
  const { width, height, texelArcsec } = registrationJson.image;
  const projected = registrationJson.checkStars.map((star) => {
    const [u, v] = panoramaUv(toScene(star.raDeg, star.decDeg), registration);
    return { star, x: (((u * width) % width) + width) % width, y: (1 - v) * height };
  });
  const wrapDx = (dx: number) => ((((dx + width / 2) % width) + width) % width) - width / 2;

  it('lists bright IAU-named check stars', () => {
    expect(projected.length).toBeGreaterThanOrEqual(60);
    expect(texelArcsec).toBeCloseTo(216, 6);
  });

  it('reproduces the Python model position of every check star (shader math parity)', () => {
    for (const { star, x, y } of projected) {
      expect(Math.abs(wrapDx(x - star.modelX))).toBeLessThan(0.01);
      expect(Math.abs(y - star.modelY)).toBeLessThan(0.01);
    }
  });

  it('lands within a texel or two of the measured star images', () => {
    const texels = projected.map(({ star, x, y }) => {
      const lat = (0.5 - y / height) * Math.PI;
      return Math.hypot(wrapDx(star.measuredX - x) * Math.cos(lat), star.measuredY - y);
    });
    texels.sort((a, b) => a - b);
    expect(texels[Math.floor(texels.length / 2)]).toBeLessThan(0.5);
    expect(texels[texels.length - 1]).toBeLessThan(2);
  });

  it('puts the galactic centre about 3.6 degrees from the image centre', () => {
    // The fitted frame's origin sits near l = 3.2, b = -1.7 deg
    const { raDeg, decDeg } = vecToRaDecDeg(applyRows(transposeRows(ICRS_TO_GALACTIC), [1, 0, 0]));
    const [u, v] = panoramaUv(toScene(raDeg, decDeg), registration);
    const offsetDeg = Math.hypot((u - 0.5) * 360, (v - 0.5) * 180);
    expect(offsetDeg).toBeGreaterThan(3);
    expect(offsetDeg).toBeLessThan(4);
  });
});

describe('sky registration: loading', () => {
  it('rejects a document that is not a version 1 registration or has a short table', () => {
    expect(() => parsePanoramaRegistration({ ...registrationJson, format: 'something-else' })).toThrow();
    expect(() => parsePanoramaRegistration({ ...registrationJson, formatVersion: 2 })).toThrow();
    const table = (registrationJson as unknown as { offsetTable: { data: string } }).offsetTable;
    expect(() =>
      parsePanoramaRegistration({ ...registrationJson, offsetTable: { ...table, data: table.data.slice(0, 400) } })
    ).toThrow();
  });

  it('fetches and parses the solution, and fails on an HTTP error', async () => {
    const ok = (async () => new Response(JSON.stringify(registrationJson))) as unknown as typeof fetch;
    const loaded = await loadPanoramaRegistration('/x.json', ok);
    expect(loaded.offsetTable.codes).toEqual(registration.offsetTable.codes);
    const missing = (async () => new Response('', { status: 404 })) as unknown as typeof fetch;
    await expect(loadPanoramaRegistration('/x.json', missing)).rejects.toThrow('404');
  });
});
