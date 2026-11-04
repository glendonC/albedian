import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import {
  CatalogStarNameIndexSchema,
  CatalogStarTileManifestSchema,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import {
  angleBetween,
  faceXyToVec,
  nestToFaceIxIy,
  raDecDegToVec,
  tileBoundingRadius,
  tileCenter,
  type Vec3,
} from '../healpix';
import {
  catalogIdAt,
  decodeCoreTile,
  decodeDetailTile,
  decodeDistanceBounds,
  halfToFloat,
  isNamed,
} from '../tileCodec';
import { CatalogStarTileLoader } from '../CatalogStarTileLoader';
import { catalogStarTint } from '../catalogStarTint';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
};

const ARCSEC = Math.PI / (180 * 3600);

describe('catalog star HEALPix geometry', () => {
  // Reference values from services/science/catalog_tiles.py, which the bake checks against
  // the HEALPix index Gaia DR3 encodes in every source_id
  const reference: [number, number, number, number, number, Vec3][] = [
    [0, 0, 0, 0, 0, [0.52704627669473, 0.52704627669473, 0.666666666666667]],
    [0, 4, 4, 0, 0, [1, 0, 0]],
    [0, 8, 8, 0, 0, [0.52704627669473, 0.52704627669473, -0.666666666666667]],
    [0, 11, 11, 0, 0, [0.52704627669473, -0.52704627669473, -0.666666666666667]],
    [1, 7, 1, 1, 1, [-0.282597082630219, 0.28259708263022, 0.916666666666667]],
    [2, 100, 6, 2, 0, [-0.910957504051591, -0.377330952923713, -0.166666666666667]],
    [3, 0, 0, 0, 0, [0.70464727188699, 0.70464727188699, 0.083333333333333]],
    [3, 333, 5, 3, 2, [-0.096646203716825, 0.981265373652484, -0.166666666666667]],
    [3, 767, 11, 7, 7, [0.70464727188699, -0.70464727188699, -0.083333333333333]],
  ];

  it('matches the Python reference for pixel faces, indices and centers', () => {
    for (const [order, pix, face, ix, iy, center] of reference) {
      expect(nestToFaceIxIy(order, pix)).toEqual({ face, ix, iy });
      const got = tileCenter(order, pix);
      expect(angleBetween(got, center)).toBeLessThan(1e-12);
    }
  });

  it('PBT: every point of a tile lies within its bounding radius and on the unit sphere', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 3 }),
        fc.nat(),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (order, seed, u, v) => {
          const pix = seed % (12 * 4 ** order);
          const { face, ix, iy } = nestToFaceIxIy(order, pix);
          const nside = 2 ** order;
          const p = faceXyToVec(face, (ix + u) / nside, (iy + v) / nside);
          expect(Math.hypot(...p)).toBeCloseTo(1, 12);
          expect(angleBetween(tileCenter(order, pix), p)).toBeLessThanOrEqual(tileBoundingRadius(order, pix));
        }
      )
    );
  });
});

describe('catalog star tile codec', () => {
  const expected = JSON.parse(fs.readFileSync(repoFile('tests/fixtures/catalog_star_tiles/expected.json'), 'utf8'));

  it('decodes IEEE half floats, with NaN as the missing-value marker', () => {
    expect(halfToFloat(0x3c00)).toBe(1);
    expect(halfToFloat(0xc000)).toBe(-2);
    expect(halfToFloat(0x0001)).toBe(2 ** -24);
    expect(halfToFloat(0x7c00)).toBe(Number.POSITIVE_INFINITY);
    expect(halfToFloat(0x7e00)).toBeNaN();
  });

  it('decodes the shared fixture written by the Python encoder', () => {
    const core = decodeCoreTile(readBuffer('tests/fixtures/catalog_star_tiles/core.bin'), {
      layer: expected.layer,
      order: expected.order,
      pix: expected.pix,
    });
    const detail = decodeDetailTile(readBuffer('tests/fixtures/catalog_star_tiles/detail.bin'));
    expect(core.count).toBe(expected.stars.length);
    expected.stars.forEach((star: Record<string, number | string>, i: number) => {
      const dir: Vec3 = [core.directions[3 * i]!, core.directions[3 * i + 1]!, core.directions[3 * i + 2]!];
      const truth = raDecDegToVec(star.raDeg as number, star.decDeg as number);
      // 16-bit positions at order 3 hold the catalog direction to well under half an arcsecond
      expect(angleBetween(dir, truth) / ARCSEC).toBeLessThan(0.5);
      expect(core.magnitude[i]).toBeCloseTo(star.magnitude as number, 3);
      expect(core.colorIndex[i]).toBeCloseTo(star.colorIndex as number, 3);
      expect(Math.abs(core.distancePc[i]! / (star.distanceMedianPc as number) - 1)).toBeLessThan(1.2e-4);
      expect(catalogIdAt(detail, i)).toBe(star.catalogId);
      expect(halfToFloat(detail.pmRaHalf[i]!)).toBeCloseTo(star.pmRaMasYr as number, 1);
      expect(halfToFloat(detail.radialVelocityHalf[i]!)).toBeCloseTo(star.radialVelocityKmS as number, 1);
      const bounds = decodeDistanceBounds(core.distancePc[i]!, detail.distanceLowerCode[i]!, detail.distanceUpperCode[i]!);
      expect(Math.abs(bounds.lowerPc / (star.distanceLowerPc as number) - 1)).toBeLessThan(0.002);
      expect(Math.abs(bounds.upperPc / (star.distanceUpperPc as number) - 1)).toBeLessThan(0.002);
    });
    expect(isNamed(core.flags[1]!)).toBe(true);
  });

  it('refuses tiles whose header disagrees with the request', () => {
    const buffer = readBuffer('tests/fixtures/catalog_star_tiles/core.bin');
    expect(() => decodeCoreTile(buffer, { layer: 0, order: expected.order, pix: expected.pix })).toThrow();
    expect(() => decodeDetailTile(buffer)).toThrow();
    expect(() => decodeCoreTile(new ArrayBuffer(40))).toThrow();
  });

  it('turns 64-bit Gaia source ids into exact decimal strings', () => {
    const detail = decodeDetailTile(readBuffer('tests/fixtures/catalog_star_tiles/detail.bin'));
    const big = { ...detail, idHi: new Uint32Array([0x5fffffff]), idLo: new Uint32Array([0xfffffff0]) };
    expect(catalogIdAt(big, 0)).toBe(((0x5fffffffn << 32n) | 0xfffffff0n).toString());
  });

  it('tints by colour index without inventing colour for colour-less entries', () => {
    expect(catalogStarTint(-1, false)).toEqual(catalogStarTint(-0.3, false));
    expect(catalogStarTint(Number.NaN, false)).toEqual(catalogStarTint(0.3, false));
    const sunLike = catalogStarTint(0.82, false);
    expect(sunLike[0]).toBeGreaterThan(sunLike[2]);
  });
});

function fixtureManifest(): CatalogStarTileManifest {
  const zero = (order: number) => new Array(12 * 4 ** order).fill(0);
  const stats = { min: 0, median: 0, max: 0 };
  const layer = (n: number, magMin: number | null, magMax: number, order: number, counts: number[]) => ({
    layer: n,
    magMin,
    magMax,
    order,
    positionBits: order >= 3 ? 16 : 32,
    stars: counts.reduce((a, b) => a + b, 0),
    nonEmptyTiles: counts.filter((c) => c > 0).length,
    coreBytes: 0,
    detailBytes: 0,
    coreTileBytes: stats,
    detailTileBytes: stats,
    maxPositionErrorArcsec: 0,
    tileStarCounts: counts,
  });
  const deep = zero(3);
  deep[0] = 2;
  return CatalogStarTileManifestSchema.parse({
    format: 'albedian.catalog-star-tiles',
    formatVersion: 1,
    releaseId: 'fixture',
    title: 'Codec fixture',
    evidenceKind: 'catalog-derived',
    disclosure: 'Catalog data.',
    frame: 'ICRS',
    epochJulianYear: 2016,
    magnitudeLimit: 12,
    magnitudeBands: { G: 'Gaia G' },
    counts: { stars: 2, gaiaSources: 2, hipparcosStars: 0, namedStars: 1 },
    tiling: { scheme: 'healpix', ordering: 'nested', positionEncoding: 'healpix-face-subpixel', tilePath: 'L{layer}/{pix}.{kind}.bin' },
    layers: [layer(0, null, 6.5, 0, zero(0)), layer(1, 6.5, 9.5, 1, zero(1)), layer(2, 9.5, 12, 3, deep)],
    encoding: {
      byteOrder: 'little-endian',
      magic: 'ALBS',
      headerBytes: 32,
      magnitude: { offset: 2, scale: 1000 },
      color: { scale: 1000, missing: -32768 },
      distance: { log10Min: -1, log10Step: 0.0001, missing: 0 },
      distanceBounds: { missing: 0, log10FractionMin: -5, lowerLog10Span: 5, upperLog10Span: 7 },
      flags: {
        distanceKindMask: 3,
        distanceKinds: { '0': 'none', '1': 'bailer-jones-2021-photogeometric' },
        magnitudeBandHp: 4,
        colorBMinusV: 8,
        catalogHipparcos: 16,
        named: 32,
      },
    },
    sources: [{ id: 'gaia-dr3', title: 'Gaia DR3', provider: 'ESA', citation: 'Gaia Collaboration 2023', license: 'CC BY-NC 3.0 IGO' }],
    selection: {},
    files: { names: 'names.json', brightStarMerge: 'bright-star-merge.json', checksums: 'SHA256SUMS' },
    bake: { script: 'x', command: 'x', verify: 'x' },
  });
}

function mockFetch(files: Record<string, ArrayBuffer | object>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = files[url];
    if (body === undefined) return new Response(null, { status: 404 });
    return body instanceof ArrayBuffer
      ? new Response(body.slice(0), { status: 200 })
      : new Response(JSON.stringify(body), { status: 200 });
  }) as unknown as typeof fetch;
}

describe('CatalogStarTileLoader', () => {
  const names = {
    format: 'albedian.catalog-star-tiles.names',
    releaseId: 'fixture',
    evidenceKind: 'catalog-derived',
    source: 'iau-csn',
    sourceUpdated: '2022-04-04',
    sourceSha256: '0'.repeat(64),
    names: [],
  };
  const files = () => ({
    '/stars/manifest.json': fixtureManifest(),
    '/stars/names.json': names,
    '/stars/L2/0.core.bin': readBuffer('tests/fixtures/catalog_star_tiles/core.bin'),
    '/stars/L2/0.detail.bin': readBuffer('tests/fixtures/catalog_star_tiles/detail.bin'),
  });
  const towardFixture = tileCenter(3, 0);

  it('asks for deeper layers only once the limiting magnitude reaches them', async () => {
    const loader = await CatalogStarTileLoader.open('/stars', { fetch: mockFetch(files()) });
    expect(loader.layersFor(6.5).map((l) => l.layer)).toEqual([0]);
    expect(loader.layersFor(9.6).map((l) => l.layer)).toEqual([0, 1, 2]);
    expect(loader.planTiles({ centerIcrs: towardFixture, radiusRad: 0.2, limitingMagnitude: 9 })).toEqual([]);
    expect(loader.planTiles({ centerIcrs: towardFixture, radiusRad: 0.2, limitingMagnitude: 12 })).toEqual([
      { layer: 2, pix: 0 },
    ]);
    const away: Vec3 = [-towardFixture[0], -towardFixture[1], -towardFixture[2]];
    expect(loader.planTiles({ centerIcrs: away, radiusRad: 0.5, limitingMagnitude: 12 })).toEqual([]);
  });

  it('streams view tiles, reports timings, and assembles catalog records on demand', async () => {
    const loaded: number[] = [];
    let ticks = 0;
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: mockFetch(files()),
      onTileLoaded: (tile) => loaded.push(tile.count),
      now: () => (ticks += 5),
    });
    loader.setView({ centerIcrs: towardFixture, radiusRad: 0.2, limitingMagnitude: 12 });
    await vi.waitFor(() => expect(loaded).toEqual([2]));
    const stats = loader.getStats();
    expect(stats).toMatchObject({ tilesLoaded: 1, starsResident: 2, failures: 0 });
    expect(stats.recent[0]).toMatchObject({ key: '2/0', stars: 2 });
    expect(stats.recent[0]!.fetchMs).toBeGreaterThan(0);

    const record = await loader.getStarRecord(2, 0, 1);
    expect(record).toMatchObject({
      evidenceKind: 'catalog-derived',
      catalog: 'gaia-dr3',
      catalogId: '33264522507136',
      designation: 'Gaia DR3 33264522507136',
      magnitudeBand: 'G',
      colorIndexKind: 'BP-RP',
    });
    expect(record.distance?.kind).toBe('bailer-jones-2021-photogeometric');
    expect(record.distance?.medianPc).toBeCloseTo(696.59, 0);
    expect(record.radialVelocityKmS).toBeCloseTo(17.18, 1);
  });

  it('evicts the farthest tiles once the resident star budget is exceeded', async () => {
    const evicted: string[] = [];
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: mockFetch(files()),
      maxResidentStars: 1,
      onTileEvicted: (key) => evicted.push(`${key.layer}/${key.pix}`),
    });
    loader.setView({ centerIcrs: towardFixture, radiusRad: 0.2, limitingMagnitude: 12 });
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(1));
    loader.setView({ centerIcrs: [-towardFixture[0], -towardFixture[1], -towardFixture[2]], radiusRad: 0.2, limitingMagnitude: 12 });
    expect(evicted).toEqual(['2/0']);
  });

  it('rejects a manifest that does not follow the contract', async () => {
    const bad = { ...fixtureManifest(), evidenceKind: 'observed' };
    await expect(CatalogStarTileLoader.open('/stars', { fetch: mockFetch({ '/stars/manifest.json': bad }) })).rejects.toThrow();
  });
});

const releaseManifest = repoFile('apps/web/public/catalog/stars/gaia-dr3/manifest.json');

describe.skipIf(!fs.existsSync(releaseManifest))('baked gaia-dr3 release', () => {
  it('follows the manifest and name contracts and decodes real named stars', () => {
    const manifest = CatalogStarTileManifestSchema.parse(JSON.parse(fs.readFileSync(releaseManifest, 'utf8')));
    expect(manifest.evidenceKind).toBe('catalog-derived');
    const names = CatalogStarNameIndexSchema.parse(
      JSON.parse(fs.readFileSync(repoFile('apps/web/public/catalog/stars/gaia-dr3/names.json'), 'utf8'))
    );
    for (const [name, minPc, maxPc] of [
      ['Sirius', 2.5, 2.8],
      ['Betelgeuse', 100, 260],
      ['Capella', 12, 14],
    ] as const) {
      const entry = names.names.find((n) => n.nameAscii === name);
      expect(entry, name).toBeDefined();
      const layer = manifest.layers[entry!.layer]!;
      const tile = decodeCoreTile(
        readBuffer(`apps/web/public/catalog/stars/gaia-dr3/L${entry!.layer}/${entry!.pix}.core.bin`),
        { layer: entry!.layer, order: layer.order, pix: entry!.pix }
      );
      const i = entry!.index;
      expect(isNamed(tile.flags[i]!)).toBe(true);
      expect(tile.distancePc[i]).toBeGreaterThan(minPc);
      expect(tile.distancePc[i]).toBeLessThan(maxPc);
      const dir: Vec3 = [tile.directions[3 * i]!, tile.directions[3 * i + 1]!, tile.directions[3 * i + 2]!];
      expect(angleBetween(dir, raDecDegToVec(entry!.raDeg, entry!.decDeg)) / ARCSEC).toBeLessThan(0.5);
    }
  });
});
