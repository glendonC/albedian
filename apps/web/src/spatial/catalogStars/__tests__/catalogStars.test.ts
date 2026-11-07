import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import {
  CATALOG_STAR_TILE_ASTRO_ENCODING,
  CATALOG_STAR_TILE_HEADER_VERSION,
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
  decodeAstroTile,
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

  it('decodes the astro tile of the shared fixture: real Gaia DR3 values, NaN where DR3 has none', () => {
    const astro = decodeAstroTile(readBuffer('tests/fixtures/catalog_star_tiles/astro.bin'), {
      layer: expected.layer,
      order: expected.order,
      pix: expected.pix,
    });
    expect(astro.count).toBe(expected.stars.length);
    const within = (got: number, want: number | null, tolerance: number, relative = false) => {
      if (want === null) {
        expect(got).toBeNaN();
        return;
      }
      expect(relative ? Math.abs(got / want - 1) : Math.abs(got - want)).toBeLessThanOrEqual(tolerance);
    };
    expected.stars.forEach((star: Record<string, number | null>, i: number) => {
      within(astro.teffK[i]!, star.teffK!, 0.5);
      within(astro.teffLowerK[i]!, star.teffLowerK!, 0.5);
      within(astro.teffUpperK[i]!, star.teffUpperK!, 0.5);
      // Half a 0.001 dex step, plus float32 storage
      within(astro.luminositySolar[i]!, star.luminositySolar!, 0.00116, true);
      within(astro.luminosityLowerSolar[i]!, star.luminosityLowerSolar!, 0.00116, true);
      within(astro.luminosityUpperSolar[i]!, star.luminosityUpperSolar!, 0.00116, true);
      within(astro.a0Mag[i]!, star.a0Mag!, 0.0005);
      within(astro.a0LowerMag[i]!, star.a0LowerMag!, 0.0005);
      within(astro.a0UpperMag[i]!, star.a0UpperMag!, 0.0005);
      within(astro.agMag[i]!, star.agMag!, 0.0005);
      // float16 keeps 11 significant bits
      within(astro.ruwe[i]!, star.ruwe!, 0.0005, true);
      within(astro.parallaxErrorMas[i]!, star.parallaxErrorMas!, 0.0005, true);
    });
    // Gaia DR3 has no GSP-Phot or FLAME result for the second fixture star: missing, never filled
    expect(astro.teffK[1]).toBeNaN();
    expect(astro.luminositySolar[1]).toBeNaN();
    expect(astro.ruwe[1]).toBeCloseTo(1.0022594, 3);
  });

  it('checks tile headers against the header version, which astro tiles share', () => {
    const astro = readBuffer('tests/fixtures/catalog_star_tiles/astro.bin');
    expect(new DataView(astro).getUint16(4, true)).toBe(CATALOG_STAR_TILE_HEADER_VERSION);
    expect(new DataView(astro).getUint8(6)).toBe(CATALOG_STAR_TILE_ASTRO_ENCODING.kind);
    const bumped = astro.slice(0);
    new DataView(bumped).setUint16(4, 2, true);
    expect(() => decodeAstroTile(bumped)).toThrow(/version 2/);
    expect(() => decodeAstroTile(readBuffer('tests/fixtures/catalog_star_tiles/core.bin'))).toThrow();
    expect(() => decodeAstroTile(astro.slice(0, astro.byteLength - 2))).toThrow();
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
    sourceSha256: 'f9bd5c237841e096a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718',
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
    expect(names.sourceSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(names.sourceSha256).not.toBe('0'.repeat(64));
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

  /** The fixture release as manifest format 2, with the astro tile of the shared fixture. */
  function astroFiles() {
    const manifest = structuredClone(fixtureManifest()) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    const astro = CATALOG_STAR_TILE_ASTRO_ENCODING;
    manifest.formatVersion = 2;
    manifest.tiling.kinds = ['core', 'detail', 'astro'];
    for (const layer of manifest.layers) {
      layer.astroBytes = 32 * layer.nonEmptyTiles + astro.bytesPerStar * layer.stars;
      layer.astroTileBytes = { min: 0, median: 0, max: 0 };
    }
    Object.assign(manifest.counts, { withTemperature: 1, withLuminosity: 1, withExtinction: 1, withRuwe: 2, withParallaxError: 2 });
    manifest.encoding.astro = {
      kind: astro.kind,
      bytesPerStar: astro.bytesPerStar,
      columns: [...astro.columns],
      teff: { missing: astro.teffMissing },
      logLum: { scale: astro.logLumScale, missing: astro.logLumMissing },
      extinction: { scale: astro.extinctionScale, missing: astro.extinctionMissing },
    };
    return {
      ...files(),
      '/stars/manifest.json': CatalogStarTileManifestSchema.parse(manifest),
      '/stars/L2/0.astro.bin': readBuffer('tests/fixtures/catalog_star_tiles/astro.bin'),
    };
  }

  it('loads astro tiles on request and gives null for what Gaia DR3 does not have', async () => {
    const fetchImpl = mockFetch(astroFiles());
    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchImpl });
    const tile = await loader.loadAstro(2, 0);
    expect(tile.count).toBe(2);
    expect(await loader.loadAstro(2, 0)).toBe(tile);
    expect(vi.mocked(fetchImpl).mock.calls.filter(([url]) => String(url).endsWith('.astro.bin'))).toHaveLength(1);

    const giant = await loader.getStarAstrophysics(2, 0, 0);
    expect(giant.evidenceKind).toBe('catalog-derived');
    expect(giant.temperatureK?.median).toBe(4632);
    expect(giant.temperatureK?.lower).toBe(4627);
    expect(giant.luminositySolar?.median).toBeCloseTo(49.51, 0);
    expect(giant.extinctionA0Mag?.upper).toBeCloseTo(0.121, 3);
    expect(giant.extinctionGMag).toBeCloseTo(0.09, 3);
    expect(giant.sourceIds).toEqual(['gaia-dr3', 'gaia-dr3-astrophysical-parameters']);

    const bare = await loader.getStarAstrophysics(2, 0, 1);
    expect(bare).toMatchObject({ temperatureK: null, luminositySolar: null, extinctionA0Mag: null, extinctionGMag: null });
    expect(bare.ruwe).toBeCloseTo(1.002, 3);
    expect(bare.parallaxErrorMas).toBeCloseTo(0.0155, 4);
    expect(bare.sourceIds).toEqual(['gaia-dr3']);
    await expect(loader.getStarAstrophysics(2, 0, 2)).rejects.toThrow(/outside/);
  });

  it('says plainly when a release has no astro tiles', async () => {
    const loader = await CatalogStarTileLoader.open('/stars', { fetch: mockFetch(files()) });
    await expect(loader.loadAstro(2, 0)).rejects.toThrow(/no astro tiles/);
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

  it('decodes real Gaia DR3 astrophysics from astro tiles, and none for Hipparcos entries', () => {
    const manifest = CatalogStarTileManifestSchema.parse(JSON.parse(fs.readFileSync(releaseManifest, 'utf8')));
    expect(manifest.formatVersion).toBe(2);
    const names = CatalogStarNameIndexSchema.parse(
      JSON.parse(fs.readFileSync(repoFile('apps/web/public/catalog/stars/gaia-dr3/names.json'), 'utf8'))
    );
    const astroOf = (name: string) => {
      const entry = names.names.find((n) => n.nameAscii === name)!;
      const tile = decodeAstroTile(
        readBuffer(`apps/web/public/catalog/stars/gaia-dr3/L${entry.layer}/${entry.pix}.astro.bin`),
        { layer: entry.layer, order: manifest.layers[entry.layer]!.order, pix: entry.pix }
      );
      return { entry, tile, i: entry.index };
    };
    // Achird as the Gaia archive gives it (2026-11-04): GSP-Phot 5726.01 K (5722.719 to 5730.129),
    // FLAME 1.2662435 Lsun (1.2604617 to 1.2720298), A0 0, RUWE 3.1213033
    const achird = astroOf('Achird');
    expect(achird.entry.catalog).toBe('gaia-dr3');
    expect(Math.abs(achird.tile.teffK[achird.i]! - 5726.01)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(achird.tile.teffLowerK[achird.i]! - 5722.719)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(achird.tile.luminositySolar[achird.i]! / 1.2662435 - 1)).toBeLessThan(0.00116);
    expect(Math.abs(achird.tile.luminosityUpperSolar[achird.i]! / 1.2720298 - 1)).toBeLessThan(0.00116);
    expect(achird.tile.a0Mag[achird.i]).toBe(0);
    expect(Math.abs(achird.tile.ruwe[achird.i]! / 3.1213033 - 1)).toBeLessThan(0.0005);
    // Sirius is a Hipparcos entry: no Gaia DR3 astrophysics, so nothing is filled in
    const sirius = astroOf('Sirius');
    expect(sirius.entry.catalog).toBe('hipparcos-2007');
    expect(sirius.tile.teffK[sirius.i]).toBeNaN();
    expect(sirius.tile.luminositySolar[sirius.i]).toBeNaN();
    expect(sirius.tile.ruwe[sirius.i]).toBeNaN();
  });
});
