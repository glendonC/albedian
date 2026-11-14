import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, expectTypeOf, it, vi } from 'vitest';
import {
  CATALOG_STAR_TILE_ASTRO_ENCODING,
  CATALOG_STAR_TILE_ENCODING_V1,
  CATALOG_STAR_TILE_HEADER_VERSION,
  CatalogStarTileManifestSchema,
  type CatalogStarNameIndex,
  type CatalogStarTileLayer,
  type CatalogStarTileManifest,
} from '@albedian/contracts';
import { angleBetween, raDecDegToVec, tileBoundingRadius, tileCenter, type Vec3 } from '../healpix';
import {
  TILE_KIND_ASTRO,
  TILE_KIND_CORE,
  TILE_KIND_DETAIL,
  type CatalogStarAstroTile,
  type CatalogStarCoreTile,
} from '../tileCodec';
import * as loaderModule from '../CatalogStarTileLoader';
import * as catalogStarsBarrel from '../index';
import {
  CatalogStarTileLoader,
  type CatalogStarAstrophysics,
  type CatalogStarInterval,
  type CatalogStarLoaderStats,
  type CatalogStarRecord,
  type CatalogStarTileKey,
  type CatalogStarTileLoaderOptions,
  type CatalogStarTileTiming,
  type CatalogStarView,
} from '../CatalogStarTileLoader';

const ENC = CATALOG_STAR_TILE_ENCODING_V1;
const ASTRO = CATALOG_STAR_TILE_ASTRO_ENCODING;

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
const readBuffer = (rel: string): ArrayBuffer => {
  const bytes = fs.readFileSync(repoFile(rel));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
};

interface SyntheticStarSpec {
  posU?: number;
  posV?: number;
  magnitude?: number;
  colorIndex?: number | null;
  distancePc?: number | null;
  distanceLowerCode?: number;
  distanceUpperCode?: number;
  flags?: number;
  catalogId?: bigint;
  pmRaHalf?: number;
  pmDecHalf?: number;
  rvHalf?: number;
  teff?: [number, number, number];
  logLum?: [number, number, number];
  a0?: [number, number, number];
  ag?: number;
  ruweHalf?: number;
  parallaxErrorHalf?: number;
}

function writeHeader(
  view: DataView,
  kind: number,
  positionBits: 16 | 32,
  layer: number,
  order: number,
  pix: number,
  count: number
): void {
  for (let i = 0; i < 4; i++) {
    view.setUint8(i, ENC.magic.charCodeAt(i));
  }
  view.setUint16(4, CATALOG_STAR_TILE_HEADER_VERSION, true);
  view.setUint8(6, kind);
  view.setUint8(7, positionBits);
  view.setUint8(8, layer);
  view.setUint8(9, order);
  view.setUint32(12, pix, true);
  view.setUint32(16, count, true);
}

function encodeCoreBuffer(
  meta: { layer: number; order: number; pix: number; positionBits?: 16 | 32 },
  stars: SyntheticStarSpec[]
): ArrayBuffer {
  const n = stars.length;
  const positionBits = meta.positionBits ?? (meta.order >= 3 ? 16 : 32);
  const posBytes = positionBits / 8;
  const buffer = new ArrayBuffer(ENC.headerBytes + n * (2 * posBytes + 7));
  const view = new DataView(buffer);
  writeHeader(view, TILE_KIND_CORE, positionBits, meta.layer, meta.order, meta.pix, n);

  let offset = ENC.headerBytes;
  for (let i = 0; i < n; i++) {
    const u = stars[i]?.posU ?? 32768;
    if (positionBits === 32) view.setUint32(offset + 4 * i, u, true);
    else view.setUint16(offset + 2 * i, u, true);
  }
  offset += n * posBytes;
  for (let i = 0; i < n; i++) {
    const v = stars[i]?.posV ?? 32768;
    if (positionBits === 32) view.setUint32(offset + 4 * i, v, true);
    else view.setUint16(offset + 2 * i, v, true);
  }
  offset += n * posBytes;
  for (let i = 0; i < n; i++) {
    const mag = stars[i]?.magnitude ?? 5.0;
    const code = Math.round((mag + ENC.magnitudeOffset) * ENC.magnitudeScale);
    view.setUint16(offset + 2 * i, code, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    const color = stars[i]?.colorIndex;
    const code =
      color === null || color === undefined
        ? ENC.colorMissing
        : Math.round(color * ENC.colorScale);
    view.setInt16(offset + 2 * i, code, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    const dist = stars[i]?.distancePc;
    const code =
      dist === null || dist === undefined
        ? ENC.distanceMissing
        : 1 + Math.round((Math.log10(dist) - ENC.distanceLog10Min) / ENC.distanceLog10Step);
    view.setUint16(offset + 2 * i, code, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    view.setUint8(offset + i, stars[i]?.flags ?? 1);
  }
  return buffer;
}

function encodeDetailBuffer(
  meta: { layer: number; order: number; pix: number; positionBits?: 16 | 32 },
  stars: SyntheticStarSpec[]
): ArrayBuffer {
  const n = stars.length;
  const positionBits = meta.positionBits ?? (meta.order >= 3 ? 16 : 32);
  const buffer = new ArrayBuffer(ENC.headerBytes + n * 16);
  const view = new DataView(buffer);
  writeHeader(view, TILE_KIND_DETAIL, positionBits, meta.layer, meta.order, meta.pix, n);

  let offset = ENC.headerBytes;
  for (let i = 0; i < n; i++) {
    const id = stars[i]?.catalogId ?? BigInt(1000 + i);
    view.setUint32(offset + 4 * i, Number((id >> 32n) & 0xffffffffn), true);
  }
  offset += 4 * n;
  for (let i = 0; i < n; i++) {
    const id = stars[i]?.catalogId ?? BigInt(1000 + i);
    view.setUint32(offset + 4 * i, Number(id & 0xffffffffn), true);
  }
  offset += 4 * n;
  for (let i = 0; i < n; i++) {
    view.setUint16(offset + 2 * i, stars[i]?.pmRaHalf ?? 0x3c00, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    view.setUint16(offset + 2 * i, stars[i]?.pmDecHalf ?? 0xc000, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    view.setUint16(offset + 2 * i, stars[i]?.rvHalf ?? 0x4000, true);
  }
  offset += 2 * n;
  for (let i = 0; i < n; i++) {
    view.setUint8(offset + i, stars[i]?.distanceLowerCode ?? 128);
  }
  offset += n;
  for (let i = 0; i < n; i++) {
    view.setUint8(offset + i, stars[i]?.distanceUpperCode ?? 128);
  }
  return buffer;
}

function encodeAstroBuffer(
  meta: { layer: number; order: number; pix: number; positionBits?: 16 | 32 },
  stars: SyntheticStarSpec[]
): ArrayBuffer {
  const n = stars.length;
  const positionBits = meta.positionBits ?? (meta.order >= 3 ? 16 : 32);
  const buffer = new ArrayBuffer(ENC.headerBytes + n * ASTRO.bytesPerStar);
  const view = new DataView(buffer);
  writeHeader(view, TILE_KIND_ASTRO, positionBits, meta.layer, meta.order, meta.pix, n);

  let offset = ENC.headerBytes;
  const writeU16Col = (pick: (s: SyntheticStarSpec) => number) => {
    for (let i = 0; i < n; i++) view.setUint16(offset + 2 * i, pick(stars[i] ?? {}), true);
    offset += 2 * n;
  };
  const writeI16Col = (pick: (s: SyntheticStarSpec) => number) => {
    for (let i = 0; i < n; i++) view.setInt16(offset + 2 * i, pick(stars[i] ?? {}), true);
    offset += 2 * n;
  };

  writeU16Col((s) => s.teff?.[0] ?? ASTRO.teffMissing);
  writeU16Col((s) => s.teff?.[1] ?? ASTRO.teffMissing);
  writeU16Col((s) => s.teff?.[2] ?? ASTRO.teffMissing);
  writeI16Col((s) => s.logLum?.[0] ?? ASTRO.logLumMissing);
  writeI16Col((s) => s.logLum?.[1] ?? ASTRO.logLumMissing);
  writeI16Col((s) => s.logLum?.[2] ?? ASTRO.logLumMissing);
  writeU16Col((s) => s.a0?.[0] ?? ASTRO.extinctionMissing);
  writeU16Col((s) => s.a0?.[1] ?? ASTRO.extinctionMissing);
  writeU16Col((s) => s.a0?.[2] ?? ASTRO.extinctionMissing);
  writeU16Col((s) => s.ag ?? ASTRO.extinctionMissing);
  writeU16Col((s) => s.ruweHalf ?? 0x7e00);
  writeU16Col((s) => s.parallaxErrorHalf ?? 0x7e00);
  return buffer;
}

interface LayerInput {
  magMin: number | null;
  magMax: number;
  order: number;
  counts: Record<number, number>;
}

function buildManifest(options: {
  formatVersion?: 1 | 2;
  releaseId?: string;
  tilePath?: string;
  namesFile?: string;
  epochJulianYear?: number;
  layers?: LayerInput[];
} = {}): CatalogStarTileManifest {
  const formatVersion = options.formatVersion ?? 2;
  const layerInputs: LayerInput[] = options.layers ?? [
    { magMin: null, magMax: 6.5, order: 0, counts: { 0: 2, 4: 1 } },
    { magMin: 6.5, magMax: 9.5, order: 1, counts: { 0: 3 } },
    { magMin: 9.5, magMax: 12.0, order: 3, counts: { 0: 2 } },
  ];
  const stats = { min: 0, median: 0, max: 0 };
  const layers = layerInputs.map((spec, index) => {
    const tileStarCounts = new Array<number>(12 * 4 ** spec.order).fill(0);
    for (const [pixStr, count] of Object.entries(spec.counts)) {
      tileStarCounts[Number(pixStr)] = count;
    }
    const stars = tileStarCounts.reduce((a, b) => a + b, 0);
    const nonEmptyTiles = tileStarCounts.filter((c) => c > 0).length;
    const base = {
      layer: index,
      magMin: spec.magMin,
      magMax: spec.magMax,
      order: spec.order,
      positionBits: (spec.order >= 3 ? 16 : 32) as 16 | 32,
      stars,
      nonEmptyTiles,
      coreBytes: 0,
      detailBytes: 0,
      coreTileBytes: stats,
      detailTileBytes: stats,
      maxPositionErrorArcsec: 0,
      tileStarCounts,
    };
    if (formatVersion === 2) {
      return {
        ...base,
        astroBytes: ENC.headerBytes * nonEmptyTiles + ASTRO.bytesPerStar * stars,
        astroTileBytes: stats,
      };
    }
    return base;
  });

  const totalStars = layers.reduce((sum, l) => sum + l.stars, 0);
  const encodingBase = {
    byteOrder: 'little-endian' as const,
    magic: ENC.magic,
    headerBytes: ENC.headerBytes,
    magnitude: { offset: ENC.magnitudeOffset, scale: ENC.magnitudeScale },
    color: { scale: ENC.colorScale, missing: ENC.colorMissing },
    distance: {
      log10Min: ENC.distanceLog10Min,
      log10Step: ENC.distanceLog10Step,
      missing: ENC.distanceMissing,
    },
    distanceBounds: {
      missing: ENC.boundMissing,
      log10FractionMin: ENC.boundLog10FractionMin,
      lowerLog10Span: ENC.boundLowerLog10Span,
      upperLog10Span: ENC.boundUpperLog10Span,
    },
    flags: {
      distanceKindMask: ENC.flags.distanceKindMask,
      distanceKinds: {
        '0': 'none' as const,
        '1': 'bailer-jones-2021-photogeometric' as const,
        '2': 'bailer-jones-2021-geometric' as const,
        '3': 'hipparcos-2007-parallax-inverse' as const,
      },
      magnitudeBandHp: ENC.flags.magnitudeBandHp,
      colorBMinusV: ENC.flags.colorBMinusV,
      catalogHipparcos: ENC.flags.catalogHipparcos,
      named: ENC.flags.named,
    },
  };

  const raw = {
    format: 'albedian.catalog-star-tiles',
    formatVersion,
    releaseId: options.releaseId ?? 'synthetic-release',
    title: 'Synthetic catalog release',
    evidenceKind: 'catalog-derived',
    disclosure: 'Catalog data.',
    frame: 'ICRS',
    epochJulianYear: options.epochJulianYear ?? 2016.0,
    magnitudeLimit: 12.0,
    magnitudeBands: { G: 'Gaia G', Hp: 'Hipparcos Hp' },
    counts:
      formatVersion === 2
        ? {
            stars: totalStars,
            gaiaSources: totalStars,
            hipparcosStars: 0,
            namedStars: 0,
            withTemperature: 0,
            withLuminosity: 0,
            withExtinction: 0,
            withRuwe: 0,
            withParallaxError: 0,
          }
        : {
            stars: totalStars,
            gaiaSources: totalStars,
            hipparcosStars: 0,
            namedStars: 0,
          },
    tiling:
      formatVersion === 2
        ? {
            scheme: 'healpix',
            ordering: 'nested',
            positionEncoding: 'healpix-face-subpixel',
            tilePath: options.tilePath ?? 'L{layer}/{pix}.{kind}.bin',
            kinds: ['core', 'detail', 'astro'],
          }
        : {
            scheme: 'healpix',
            ordering: 'nested',
            positionEncoding: 'healpix-face-subpixel',
            tilePath: options.tilePath ?? 'L{layer}/{pix}.{kind}.bin',
          },
    layers,
    encoding:
      formatVersion === 2
        ? {
            ...encodingBase,
            astro: {
              kind: ASTRO.kind,
              bytesPerStar: ASTRO.bytesPerStar,
              columns: [...ASTRO.columns],
              teff: { missing: ASTRO.teffMissing },
              logLum: { scale: ASTRO.logLumScale, missing: ASTRO.logLumMissing },
              extinction: { scale: ASTRO.extinctionScale, missing: ASTRO.extinctionMissing },
            },
          }
        : encodingBase,
    sources: [
      {
        id: 'gaia-dr3',
        title: 'Gaia DR3',
        provider: 'ESA',
        citation: 'Gaia Collaboration 2023',
        license: 'CC BY-NC 3.0 IGO',
      },
    ],
    selection: {},
    files: {
      names: options.namesFile ?? 'names.json',
      brightStarMerge: 'bright-star-merge.json',
      checksums: 'SHA256SUMS',
    },
    bake: { script: 'bake', command: 'bake', verify: 'verify' },
  };
  return CatalogStarTileManifestSchema.parse(raw);
}

function createNamesPayload(names: CatalogStarNameIndex['names'] = []): CatalogStarNameIndex {
  return {
    format: 'albedian.catalog-star-tiles.names',
    releaseId: 'synthetic-release',
    evidenceKind: 'catalog-derived',
    source: 'iau-csn',
    sourceUpdated: '2022-04-04',
    sourceSha256: 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90',
    names,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('CatalogStarTileLoader public module contract', () => {
  it('exports only CatalogStarTileLoader at runtime and preserves barrel re-export', () => {
    expect(Object.keys(loaderModule)).toEqual(['CatalogStarTileLoader']);
    expect(loaderModule.CatalogStarTileLoader).toBe(CatalogStarTileLoader);
    expect(catalogStarsBarrel.CatalogStarTileLoader).toBe(CatalogStarTileLoader);
  });

  it('preserves public TypeScript types and method signatures', () => {
    expectTypeOf<typeof CatalogStarTileLoader.open>().toEqualTypeOf<
      (baseUrl: string, options?: CatalogStarTileLoaderOptions) => Promise<CatalogStarTileLoader>
    >();
    expectTypeOf<CatalogStarTileLoader['manifest']>().toEqualTypeOf<CatalogStarTileManifest>();
    expectTypeOf<CatalogStarTileLoader['baseUrl']>().toEqualTypeOf<string>();
    expectTypeOf<CatalogStarTileLoader['layersFor']>().toEqualTypeOf<
      (limitingMagnitude: number) => CatalogStarTileLayer[]
    >();
    expectTypeOf<CatalogStarTileLoader['planTiles']>().toEqualTypeOf<
      (view: CatalogStarView) => CatalogStarTileKey[]
    >();
    expectTypeOf<CatalogStarTileLoader['setView']>().toEqualTypeOf<(view: CatalogStarView) => void>();
    expectTypeOf<CatalogStarTileLoader['getLoadedTiles']>().toEqualTypeOf<() => CatalogStarCoreTile[]>();
    expectTypeOf<CatalogStarTileLoader['getStats']>().toEqualTypeOf<() => CatalogStarLoaderStats>();
    expectTypeOf<CatalogStarTileLoader['loadNames']>().toEqualTypeOf<() => Promise<CatalogStarNameIndex>>();
    expectTypeOf<CatalogStarTileLoader['getStarRecord']>().toEqualTypeOf<
      (layer: number, pix: number, index: number) => Promise<CatalogStarRecord>
    >();
    expectTypeOf<CatalogStarTileLoader['loadAstro']>().toEqualTypeOf<
      (layer: number, pix: number) => Promise<CatalogStarAstroTile>
    >();
    expectTypeOf<CatalogStarTileLoader['getStarAstrophysics']>().toEqualTypeOf<
      (layer: number, pix: number, index: number) => Promise<CatalogStarAstrophysics>
    >();
    expectTypeOf<CatalogStarTileLoader['dispose']>().toEqualTypeOf<() => void>();
    expectTypeOf<CatalogStarLoaderStats['recent']>().toEqualTypeOf<CatalogStarTileTiming[]>();
    expectTypeOf<CatalogStarAstrophysics['temperatureK']>().toEqualTypeOf<CatalogStarInterval | null>();
  });
});

describe('CatalogStarTileLoader manifest opening and URL resolution', () => {
  it('normalizes baseUrl with or without a trailing slash and avoids double slashes', async () => {
    const manifest = buildManifest();
    const seenUrls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      seenUrls.push(url);
      if (url.endsWith('/manifest.json')) {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const withoutSlash = await CatalogStarTileLoader.open('/catalog/stars/gaia-dr3', { fetch: fetchMock });
    const withSlash = await CatalogStarTileLoader.open('/catalog/stars/gaia-dr3/', { fetch: fetchMock });

    expect(withoutSlash.baseUrl).toBe('/catalog/stars/gaia-dr3/');
    expect(withSlash.baseUrl).toBe('/catalog/stars/gaia-dr3/');
    expect(seenUrls).toEqual([
      '/catalog/stars/gaia-dr3/manifest.json',
      '/catalog/stars/gaia-dr3/manifest.json',
    ]);
  });

  it('throws a descriptive error when manifest HTTP status is not ok', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 503 })) as unknown as typeof fetch;
    await expect(CatalogStarTileLoader.open('/stars', { fetch: fetchMock })).rejects.toThrow(
      'catalog star manifest unavailable (503)'
    );
  });

  it('rejects manifests that violate the contract schema', async () => {
    const badManifest = { ...buildManifest(), frame: 'GALACTIC' };
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(badManifest), { status: 200 })
    ) as unknown as typeof fetch;
    await expect(CatalogStarTileLoader.open('/stars', { fetch: fetchMock })).rejects.toThrow();
  });

  it('substitutes custom tilePath templates and custom names path relative to baseUrl', async () => {
    const manifest = buildManifest({
      tilePath: 'chunks/band-{layer}/cell-{pix}.{kind}.albs',
      namesFile: 'meta/iau-names.json',
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 4: 1 } }],
    });
    const core = encodeCoreBuffer({ layer: 0, order: 0, pix: 4 }, [{ magnitude: 3.2 }]);
    const detail = encodeDetailBuffer({ layer: 0, order: 0, pix: 4 }, [{ catalogId: 42n }]);
    const astro = encodeAstroBuffer({ layer: 0, order: 0, pix: 4 }, [{ teff: [5800, 5750, 5850] }]);
    const names = createNamesPayload();

    const requested: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requested.push(url);
      if (url === 'https://cdn.example/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      if (url === 'https://cdn.example/stars/chunks/band-0/cell-4.core.albs') {
        return new Response(core.slice(0), { status: 200 });
      }
      if (url === 'https://cdn.example/stars/chunks/band-0/cell-4.detail.albs') {
        return new Response(detail.slice(0), { status: 200 });
      }
      if (url === 'https://cdn.example/stars/chunks/band-0/cell-4.astro.albs') {
        return new Response(astro.slice(0), { status: 200 });
      }
      if (url === 'https://cdn.example/stars/meta/iau-names.json') {
        return new Response(JSON.stringify(names), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('https://cdn.example/stars', { fetch: fetchMock });
    const record = await loader.getStarRecord(0, 4, 0);
    const astroRecord = await loader.getStarAstrophysics(0, 4, 0);

    expect(record.catalogId).toBe('42');
    expect(astroRecord.temperatureK?.median).toBe(5800);
    expect(requested).toEqual([
      'https://cdn.example/stars/manifest.json',
      'https://cdn.example/stars/chunks/band-0/cell-4.core.albs',
      'https://cdn.example/stars/chunks/band-0/cell-4.detail.albs',
      'https://cdn.example/stars/meta/iau-names.json',
      'https://cdn.example/stars/chunks/band-0/cell-4.astro.albs',
    ]);
  });
});

describe('CatalogStarTileLoader HEALPix view planning and layer thresholds', () => {
  it('applies strict less-than threshold on layer magMin and treats null magMin as -Infinity', async () => {
    const manifest = buildManifest({
      layers: [
        { magMin: null, magMax: 6.5, order: 0, counts: { 0: 1 } },
        { magMin: 6.5, magMax: 9.5, order: 1, counts: { 0: 1 } },
        { magMin: 9.5, magMax: 12.0, order: 2, counts: { 0: 1 } },
      ],
    });
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: vi.fn(async () => new Response(JSON.stringify(manifest), { status: 200 })) as unknown as typeof fetch,
    });

    expect(loader.layersFor(-2).map((l) => l.layer)).toEqual([0]);
    expect(loader.layersFor(6.5).map((l) => l.layer)).toEqual([0]);
    expect(loader.layersFor(6.5001).map((l) => l.layer)).toEqual([0, 1]);
    expect(loader.layersFor(9.5).map((l) => l.layer)).toEqual([0, 1]);
    expect(loader.layersFor(9.5001).map((l) => l.layer)).toEqual([0, 1, 2]);
    expect(loader.layersFor(Number.NEGATIVE_INFINITY)).toEqual([]);
  });

  it('includes tiles on the exact cone boundary, skips empty pixels, and orders brightest layer first then angular distance', async () => {
    const manifest = buildManifest({
      layers: [
        { magMin: null, magMax: 6.5, order: 0, counts: { 0: 2, 4: 3, 8: 4 } },
        { magMin: 6.5, magMax: 9.5, order: 2, counts: { 64: 5, 100: 2 } },
      ],
    });
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(manifest), { status: 200 })) as unknown as typeof fetch;
    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });

    // Look directly at order-2 pix 64 so its distance is 0, while order-0 tiles have larger center distance
    const center = tileCenter(2, 64);
    const distPix8 = angleBetween(center, tileCenter(0, 8));
    const radPix8 = tileBoundingRadius(0, 8);
    const exactBoundaryRadius = distPix8 - radPix8;

    // Verify exact boundary inclusion (distance - tile.radius === view.radiusRad) vs just below
    const withBoundary = loader.planTiles({
      centerIcrs: center,
      radiusRad: exactBoundaryRadius,
      limitingMagnitude: 6.5,
    });
    const justInside = loader.planTiles({
      centerIcrs: center,
      radiusRad: exactBoundaryRadius - 1e-9,
      limitingMagnitude: 6.5,
    });
    expect(withBoundary.some((k) => k.layer === 0 && k.pix === 8)).toBe(true);
    expect(justInside.some((k) => k.layer === 0 && k.pix === 8)).toBe(false);

    // Full-sky plan at mag 9: all layer 0 non-empty tiles MUST precede layer 1 tiles,
    // even though layer 1 pix 64 has angular distance 0 from center!
    const planned = loader.planTiles({
      centerIcrs: center,
      radiusRad: Math.PI,
      limitingMagnitude: 9.0,
    });
    const layer0Planned = planned.filter((k) => k.layer === 0);
    const layer1Planned = planned.filter((k) => k.layer === 1);
    expect(planned).toEqual([...layer0Planned, ...layer1Planned]);
    expect(layer0Planned).toHaveLength(3);
    expect(layer1Planned).toEqual([
      { layer: 1, pix: 64 },
      { layer: 1, pix: 100 },
    ]);

    // Within layer 0, verify ascending distance from center
    const l0Distances = layer0Planned.map((k) => angleBetween(center, tileCenter(0, k.pix)));
    expect(l0Distances[0]).toBeLessThanOrEqual(l0Distances[1]!);
    expect(l0Distances[1]).toBeLessThanOrEqual(l0Distances[2]!);

    // planTiles is side-effect free: no tile fetches and wanted count remains 0
    expect(loader.getStats().tilesWanted).toBe(0);
    expect(vi.mocked(fetchMock)).toHaveBeenCalledTimes(1);
  });
});

describe('CatalogStarTileLoader streaming concurrency, deduplication, retry backoff, and stats', () => {
  it('enforces maxConcurrent, deduplicates in-flight tiles across rapid setView calls, and retains in-flight tiles if view moves', async () => {
    const manifest = buildManifest({
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 0: 1, 1: 1, 2: 1, 4: 1 } }],
    });
    const gates = new Map<string, ReturnType<typeof deferred<Response>>>();
    for (const pix of [0, 1, 2, 4]) {
      gates.set(`/stars/L0/${pix}.core.bin`, deferred<Response>());
    }

    const coreCalls: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return Promise.resolve(new Response(JSON.stringify(manifest), { status: 200 }));
      }
      coreCalls.push(url);
      const gate = gates.get(url);
      if (!gate) return Promise.resolve(new Response(null, { status: 404 }));
      return gate.promise;
    }) as unknown as typeof fetch;

    const loadedOrder: number[] = [];
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      maxConcurrent: 2,
      onTileLoaded: (tile) => loadedOrder.push(tile.pix),
    });

    const viewAll: CatalogStarView = {
      centerIcrs: tileCenter(0, 4),
      radiusRad: Math.PI,
      limitingMagnitude: 6.5,
    };
    const planned = loader.planTiles(viewAll);
    expect(planned.map((p) => p.pix)[0]).toBe(4);

    loader.setView(viewAll);
    // Rapid second setView with identical cone must not issue duplicate requests
    loader.setView(viewAll);

    expect(loader.getStats().tilesWanted).toBe(4);
    expect(loader.getStats().tilesInFlight).toBe(2);
    expect(coreCalls).toHaveLength(2);
    expect(coreCalls[0]).toBe(`/stars/L0/${planned[0]!.pix}.core.bin`);
    expect(coreCalls[1]).toBe(`/stars/L0/${planned[1]!.pix}.core.bin`);

    // Move view to opposite hemisphere (narrow cone with zero wanted tiles) while 2 fetches are in flight
    loader.setView({
      centerIcrs: tileCenter(0, 11),
      radiusRad: 0.01,
      limitingMagnitude: 6.5,
    });
    expect(loader.getStats().tilesWanted).toBe(0);

    // Resolve the two in-flight tiles: they are still kept when within resident star budget
    for (const pix of [planned[0]!.pix, planned[1]!.pix]) {
      const buf = encodeCoreBuffer({ layer: 0, order: 0, pix }, [{ magnitude: 4.0 }]);
      gates.get(`/stars/L0/${pix}.core.bin`)!.resolve(new Response(buf, { status: 200 }));
    }
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(2));
    expect(loader.getStats().tilesInFlight).toBe(0);
    expect(loadedOrder).toEqual([planned[0]!.pix, planned[1]!.pix]);

    // Re-aiming at viewAll now fetches only the remaining 2 unloaded tiles
    loader.setView(viewAll);
    expect(coreCalls).toHaveLength(4);
    for (const pix of [planned[2]!.pix, planned[3]!.pix]) {
      const buf = encodeCoreBuffer({ layer: 0, order: 0, pix }, [{ magnitude: 4.5 }]);
      gates.get(`/stars/L0/${pix}.core.bin`)!.resolve(new Response(buf, { status: 200 }));
    }
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(4));
    expect(loader.getStats().starsResident).toBe(4);
  });

  it('backs off failed tiles for 15,000 ms without blocking sibling tiles, then retries at >= 15,000 ms', async () => {
    const manifest = buildManifest({
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 4: 2, 0: 1 } }],
    });
    let failPix4 = true;
    let clock = 1_000;
    const errors: { key: CatalogStarTileKey; message: string }[] = [];

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      if (url === '/stars/L0/4.core.bin') {
        if (failPix4) return new Response(null, { status: 502 });
        return new Response(
          encodeCoreBuffer({ layer: 0, order: 0, pix: 4 }, [{ magnitude: 2.0 }, { magnitude: 3.0 }]),
          { status: 200 }
        );
      }
      if (url === '/stars/L0/0.core.bin') {
        return new Response(encodeCoreBuffer({ layer: 0, order: 0, pix: 0 }, [{ magnitude: 4.0 }]), {
          status: 200,
        });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      maxConcurrent: 1,
      now: () => clock,
      onError: (err, key) =>
        errors.push({ key, message: err instanceof Error ? err.message : String(err) }),
    });

    const view: CatalogStarView = {
      centerIcrs: tileCenter(0, 4),
      radiusRad: Math.PI,
      limitingMagnitude: 6.5,
    };
    loader.setView(view);

    // Pix 4 fails at clock = 1_000, and pump immediately proceeds to load pix 0 despite maxConcurrent = 1
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(1));
    expect(loader.getStats().failures).toBe(1);
    expect(errors).toEqual([
      {
        key: { layer: 0, pix: 4 },
        message: 'catalog star tile unavailable (502): /stars/L0/4.core.bin',
      },
    ]);
    expect(loader.getLoadedTiles().map((t) => t.pix)).toEqual([0]);

    // At 14,999 ms elapsed (clock = 15,999), backoff is still active: no retry
    failPix4 = false;
    clock = 15_999;
    loader.setView(view);
    await new Promise((r) => setTimeout(r, 10));
    expect(loader.getStats().tilesLoaded).toBe(1);

    // At exactly 15,000 ms elapsed (clock = 16,000), backoff expires and pix 4 loads cleanly
    clock = 16_000;
    loader.setView(view);
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(2));
    expect(loader.getStats().starsResident).toBe(3);
    expect(loader.getStats().failures).toBe(1);
  });

  it('tracks byte/timing totals, caps recent tile timings at 64 entries, and returns defensive array copies', async () => {
    const counts: Record<number, number> = {};
    for (let pix = 0; pix < 70; pix++) counts[pix] = 1;
    const manifest = buildManifest({
      layers: [{ magMin: null, magMax: 9.5, order: 2, counts }],
    });

    let clock = 100;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      const match = /\/stars\/L0\/(\d+)\.core\.bin$/.exec(url);
      if (match) {
        clock += 3;
        const pix = Number(match[1]);
        return new Response(encodeCoreBuffer({ layer: 0, order: 2, pix }, [{ magnitude: 5.0 }]), {
          status: 200,
        });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      maxConcurrent: 16,
      now: () => {
        clock += 1;
        return clock;
      },
    });

    loader.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: 9.0 });
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(70));

    const stats = loader.getStats();
    const expectedTileBytes = ENC.headerBytes + (2 * 4 + 7);
    expect(stats.bytesLoaded).toBe(70 * expectedTileBytes);
    expect(stats.fetchMsTotal).toBeGreaterThan(0);
    expect(stats.decodeMsTotal).toBeGreaterThan(0);
    expect(stats.recent).toHaveLength(64);
    // First 6 entries were evicted FIFO from the 64-entry ring buffer
    expect(stats.recent[63]?.stars).toBe(1);
    expect(stats.recent[63]?.bytes).toBe(expectedTileBytes);

    // Mutating returned arrays does not corrupt internal state
    const loadedCopy = loader.getLoadedTiles();
    loadedCopy.pop();
    stats.recent.length = 0;
    expect(loader.getLoadedTiles()).toHaveLength(70);
    expect(loader.getStats().recent).toHaveLength(64);
  });
});

describe('CatalogStarTileLoader residency budget and eviction priority', () => {
  it('protects wanted tiles, keeps resident === maxResidentStars, and evicts faintest layer first then farthest distance while dropping cached details', async () => {
    // Layer 0 (order 0): pix 0 (2 stars), pix 4 (2 stars), pix 8 (2 stars)
    // Layer 1 (order 1): pix 16 (2 stars, same direction as order-0 pix 4: [1, 0, 0] neighborhood)
    const manifest = buildManifest({
      layers: [
        { magMin: null, magMax: 6.5, order: 0, counts: { 0: 2, 4: 2, 8: 2 } },
        { magMin: 6.5, magMax: 9.5, order: 1, counts: { 16: 2 } },
      ],
    });

    let detailFetchesForL1Pix16 = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      const coreMatch = /\/stars\/L(\d+)\/(\d+)\.core\.bin$/.exec(url);
      if (coreMatch) {
        const layer = Number(coreMatch[1]);
        const pix = Number(coreMatch[2]);
        const order = layer === 0 ? 0 : 1;
        return new Response(
          encodeCoreBuffer({ layer, order, pix }, [{ magnitude: 4.0 }, { magnitude: 5.0 }]),
          { status: 200 }
        );
      }
      if (url === '/stars/L1/16.detail.bin') {
        detailFetchesForL1Pix16 += 1;
        return new Response(
          encodeDetailBuffer({ layer: 1, order: 1, pix: 16 }, [{ catalogId: 77n }, { catalogId: 78n }]),
          { status: 200 }
        );
      }
      if (url === '/stars/names.json') {
        return new Response(JSON.stringify(createNamesPayload()), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const evicted: string[] = [];
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      maxResidentStars: 6,
      onTileEvicted: (key) => evicted.push(`${key.layer}/${key.pix}`),
    });

    // Step 1: Want ALL 4 tiles (8 stars > budget 6). Because all 4 are wanted, NONE may be evicted!
    loader.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: 9.0 });
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(4));
    expect(loader.getStats().starsResident).toBe(8);
    expect(evicted).toEqual([]);

    // Populate detail cache for L1/16
    await loader.getStarRecord(1, 16, 0);
    expect(detailFetchesForL1Pix16).toBe(1);

    // Step 2: Aim tightly at tileCenter(0, 4) with limitingMagnitude 6.5 (only L0/4 is wanted).
    // Note: L1/16 is much closer to tileCenter(0, 4) than L0/0 or L0/8,
    // BUT L1/16 is in fainter layer 1, so L1/16 MUST be evicted FIRST, bringing resident from 8 -> 6 (== budget),
    // and eviction MUST stop at 6 without evicting L0/0 or L0/8!
    const centerPix4 = tileCenter(0, 4);
    loader.setView({ centerIcrs: centerPix4, radiusRad: 0.05, limitingMagnitude: 6.5 });
    expect(evicted).toEqual(['1/16']);
    expect(loader.getStats().starsResident).toBe(6);

    // Because L1/16 was evicted, its detail promise must also have been cleared from cache
    await loader.getStarRecord(1, 16, 0);
    expect(detailFetchesForL1Pix16).toBe(2);

    // Step 3: Load another tile or lower budget? Let's test tie-breaking by angular distance within layer 0!
    // Create a second loader with maxResidentStars = 4 to verify farthest-first within layer 0.
    const evictedSameLayer: string[] = [];
    const loader2 = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      maxResidentStars: 4,
      onTileEvicted: (key) => evictedSameLayer.push(`${key.layer}/${key.pix}`),
    });
    loader2.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: 6.5 });
    await vi.waitFor(() => expect(loader2.getStats().tilesLoaded).toBe(3));
    expect(loader2.getStats().starsResident).toBe(6);

    // Aim tightly at L0/0: L0/4 vs L0/8 are both non-wanted in layer 0; whichever is farther from L0/0 center evicts!
    const centerPix0 = tileCenter(0, 0);
    const dist4 = angleBetween(centerPix0, tileCenter(0, 4));
    const dist8 = angleBetween(centerPix0, tileCenter(0, 8));
    const fartherPix = dist8 > dist4 ? 8 : 4;
    loader2.setView({ centerIcrs: centerPix0, radiusRad: 0.05, limitingMagnitude: 6.5 });
    expect(evictedSameLayer).toEqual([`0/${fartherPix}`]);
    expect(loader2.getStats().starsResident).toBe(4);
  });
});

describe('CatalogStarTileLoader names, star records, astro tiles, and astrophysics', () => {
  it('deduplicates loadNames and retries after a transient failure', async () => {
    const manifest = buildManifest();
    let failNames = true;
    let namesCalls = 0;
    const namesPayload = createNamesPayload([
      {
        name: 'Vega',
        nameAscii: 'Vega',
        designation: 'HR 7001',
        bayer: 'alp',
        constellation: 'Lyr',
        component: '',
        hip: 91262,
        catalog: 'hipparcos-2007',
        catalogId: '91262',
        layer: 0,
        pix: 0,
        index: 0,
        raDeg: 279.2347,
        decDeg: 38.7837,
        mag: 0.03,
        magBand: 'Hp',
      },
    ]);

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      if (url === '/stars/names.json') {
        namesCalls += 1;
        if (failNames) return new Response(null, { status: 500 });
        return new Response(JSON.stringify(namesPayload), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });
    await expect(loader.loadNames()).rejects.toThrow('catalog star names unavailable (500)');
    expect(namesCalls).toBe(1);

    failNames = false;
    const [first, second] = await Promise.all([loader.loadNames(), loader.loadNames()]);
    expect(first).toBe(second);
    expect(first.names[0]?.nameAscii).toBe('Vega');
    expect(namesCalls).toBe(2);
  });

  it('assembles Gaia DR3 and Hipparcos CatalogStarRecords across all flag, bound, kinematic, and missing-names cases', async () => {
    const manifest = buildManifest({
      epochJulianYear: 2016.0,
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 4: 3 } }],
    });

    // Star 0: Hipparcos star (Hp, B-V, named, parallax-inverse distance, missing bounds, only pmRa finite -> properMotion null, missing RV)
    const hipFlags =
      3 | // hipparcos-2007-parallax-inverse
      ENC.flags.magnitudeBandHp |
      ENC.flags.colorBMinusV |
      ENC.flags.catalogHipparcos |
      ENC.flags.named;

    // Star 1: Gaia DR3 star with no distance (flags = 0: distanceKind 'none'), missing color, finite PM & RV
    const gaiaNoDistFlags = 0;

    // Star 2: Gaia DR3 star with geometric distance (flags = 2) and finite bounds
    const gaiaGeoFlags = 2;

    const stars: SyntheticStarSpec[] = [
      {
        magnitude: 0.45,
        colorIndex: 0.09,
        distancePc: 7.68,
        distanceLowerCode: 0,
        distanceUpperCode: 0,
        flags: hipFlags,
        catalogId: 91262n,
        pmRaHalf: 0x3c00, // 1.0
        pmDecHalf: 0x7e00, // NaN -> properMotion must be null when either component is NaN
        rvHalf: 0x7e00, // NaN
      },
      {
        magnitude: 5.25,
        colorIndex: null,
        distancePc: null,
        distanceLowerCode: 0,
        distanceUpperCode: 0,
        flags: gaiaNoDistFlags,
        catalogId: 4503599627370496n,
        pmRaHalf: 0x3c00, // 1.0
        pmDecHalf: 0xc000, // -2.0
        rvHalf: 0x4000, // 2.0
      },
      {
        magnitude: 6.1,
        colorIndex: 1.12,
        distancePc: 120.0,
        distanceLowerCode: 140,
        distanceUpperCode: 140,
        flags: gaiaGeoFlags,
        catalogId: 9007199254740993n, // > 2^53 to verify exact 64-bit decimal catalogId
        pmRaHalf: 0x7e00,
        pmDecHalf: 0x7e00,
        rvHalf: 0x7e00,
      },
    ];

    const coreBuf = encodeCoreBuffer({ layer: 0, order: 0, pix: 4 }, stars);
    const detailBuf = encodeDetailBuffer({ layer: 0, order: 0, pix: 4 }, stars);

    let failDetailOnce = true;
    let detailCalls = 0;
    let coreCalls = 0;
    let namesAvailable = true;

    const namesPayload = createNamesPayload([
      {
        name: 'Vega',
        nameAscii: 'Vega',
        designation: 'HR 7001',
        bayer: 'alp',
        constellation: 'Lyr',
        component: '',
        hip: 91262,
        catalog: 'hipparcos-2007',
        catalogId: '91262',
        layer: 0,
        pix: 4,
        index: 0,
        raDeg: 0,
        decDeg: 0,
        mag: 0.45,
        magBand: 'Hp',
      },
      // Same catalogId number in gaia-dr3 must NOT match hipparcos-2007 star!
      {
        name: 'WrongCatalog',
        nameAscii: 'WrongCatalog',
        designation: 'X',
        bayer: '',
        constellation: 'Lyr',
        component: '',
        hip: 1,
        catalog: 'gaia-dr3',
        catalogId: '91262',
        layer: 0,
        pix: 4,
        index: 0,
        raDeg: 0,
        decDeg: 0,
        mag: 0.45,
        magBand: 'G',
      },
    ]);

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifest), { status: 200 });
      }
      if (url === '/stars/L0/4.core.bin') {
        coreCalls += 1;
        return new Response(coreBuf.slice(0), { status: 200 });
      }
      if (url === '/stars/L0/4.detail.bin') {
        detailCalls += 1;
        if (failDetailOnce) {
          failDetailOnce = false;
          return new Response(null, { status: 500 });
        }
        return new Response(detailBuf.slice(0), { status: 200 });
      }
      if (url === '/stars/names.json') {
        if (!namesAvailable) return new Response(null, { status: 404 });
        return new Response(JSON.stringify(namesPayload), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });

    // Out-of-range layer and out-of-bounds star index errors
    await expect(loader.getStarRecord(9, 0, 0)).rejects.toThrow('unknown catalog star layer 9');
    await expect(loader.getStarRecord(0, 4, -1)).rejects.toThrow('star index -1 outside tile 0/4');
    await expect(loader.getStarRecord(0, 4, 3)).rejects.toThrow('star index 3 outside tile 0/4');

    // First detail fetch fails, clearing detail cache so retry succeeds
    await expect(loader.getStarRecord(0, 4, 0)).rejects.toThrow('catalog star tile unavailable (500)');

    // Load core tile via setView so subsequent getStarRecord uses resident core tile without re-fetching core.bin
    loader.setView({ centerIcrs: [1, 0, 0], radiusRad: 0.5, limitingMagnitude: 6.5 });
    await vi.waitFor(() => expect(loader.getStats().tilesLoaded).toBe(1));
    const coreCallsBeforeInspect = coreCalls;

    const [hipStar, gaiaBare, gaiaGeo] = await Promise.all([
      loader.getStarRecord(0, 4, 0),
      loader.getStarRecord(0, 4, 1),
      loader.getStarRecord(0, 4, 2),
    ]);

    expect(coreCalls).toBe(coreCallsBeforeInspect);
    expect(detailCalls).toBe(2); // 1 failed + 1 shared across all 3 concurrent getStarRecord calls!

    expect(hipStar).toMatchObject({
      evidenceKind: 'catalog-derived',
      catalog: 'hipparcos-2007',
      catalogId: '91262',
      designation: 'HIP 91262',
      epochJulianYear: 2016.0,
      magnitudeBand: 'Hp',
      colorIndexKind: 'B-V',
      properMotion: null,
      radialVelocityKmS: null,
      sourceIds: ['hipparcos-2007'],
    });
    expect(hipStar.names.map((n) => n.nameAscii)).toEqual(['Vega']);
    expect(hipStar.colorIndex).toBeCloseTo(0.09, 3);
    expect(hipStar.distance?.kind).toBe('hipparcos-2007-parallax-inverse');
    expect(hipStar.distance?.lowerPc).toBeNull();
    expect(hipStar.distance?.upperPc).toBeNull();

    expect(gaiaBare).toMatchObject({
      evidenceKind: 'catalog-derived',
      catalog: 'gaia-dr3',
      catalogId: '4503599627370496',
      designation: 'Gaia DR3 4503599627370496',
      names: [],
      magnitudeBand: 'G',
      colorIndex: null,
      colorIndexKind: 'BP-RP',
      distance: null,
      properMotion: { raMasYr: 1, decMasYr: -2 },
      radialVelocityKmS: 2,
      sourceIds: ['gaia-dr3'],
    });

    expect(gaiaGeo).toMatchObject({
      evidenceKind: 'catalog-derived',
      catalog: 'gaia-dr3',
      catalogId: '9007199254740993',
      designation: 'Gaia DR3 9007199254740993',
      sourceIds: ['gaia-dr3', 'bailer-jones-2021'],
    });
    expect(gaiaGeo.distance?.kind).toBe('bailer-jones-2021-geometric');
    expect(gaiaGeo.distance?.lowerPc).not.toBeNull();
    expect(gaiaGeo.distance?.upperPc).not.toBeNull();

    // Verify graceful fallback to names: [] when names.json is unavailable
    namesAvailable = false;
    const loaderNoNames = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });
    const recordWithoutNames = await loaderNoNames.getStarRecord(0, 4, 0);
    expect(recordWithoutNames.names).toEqual([]);
  });

  it('validates astro tile requests, retries failed astro fetches, and tracks exact sourceIds per star', async () => {
    const manifestV1 = buildManifest({ formatVersion: 1 });
    const loaderV1 = await CatalogStarTileLoader.open('/stars', {
      fetch: vi.fn(async () => new Response(JSON.stringify(manifestV1), { status: 200 })) as unknown as typeof fetch,
    });
    await expect(loaderV1.loadAstro(0, 0)).rejects.toThrow(
      'catalog release synthetic-release has no astro tiles (manifest format 1)'
    );

    const manifestV2 = buildManifest({
      formatVersion: 2,
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 4: 4 } }],
    });
    const astroBuf = encodeAstroBuffer({ layer: 0, order: 0, pix: 4 }, [
      // Star 0: Hipparcos / all missing -> sourceIds []
      {},
      // Star 1: FLAME luminosity only (with missing lower bound) -> sourceIds ['gaia-dr3-astrophysical-parameters']
      { logLum: [0, ASTRO.logLumMissing, 100] },
      // Star 2: GSP-Phot temperature (with missing upper bound) + AG only -> sourceIds ['gaia-dr3']
      { teff: [5772, 5750, ASTRO.teffMissing], ag: 125 },
      // Star 3: Both RUWE and luminosity -> sourceIds ['gaia-dr3', 'gaia-dr3-astrophysical-parameters']
      { logLum: [300, 290, 310], ruweHalf: 0x3c00, parallaxErrorHalf: 0x3800 },
    ]);

    let failAstroOnce = true;
    let astroCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return new Response(JSON.stringify(manifestV2), { status: 200 });
      }
      if (url === '/stars/L0/4.astro.bin') {
        astroCalls += 1;
        if (failAstroOnce) {
          failAstroOnce = false;
          return new Response(null, { status: 503 });
        }
        return new Response(astroBuf.slice(0), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });

    // Unknown layer, negative pix, and empty tile (pix 0 has 0 stars) all reject cleanly
    await expect(loader.loadAstro(5, 0)).rejects.toThrow('no catalog star tile 5/0');
    await expect(loader.loadAstro(0, -1)).rejects.toThrow('no catalog star tile 0/-1');
    await expect(loader.loadAstro(0, 0)).rejects.toThrow('no catalog star tile 0/0');

    // First astro fetch fails and evicts rejected promise so retry succeeds
    await expect(loader.loadAstro(0, 4)).rejects.toThrow('catalog star tile unavailable (503)');

    // Concurrent calls share a single retry request
    const [a1, a2] = await Promise.all([loader.loadAstro(0, 4), loader.loadAstro(0, 4)]);
    expect(a1).toBe(a2);
    expect(astroCalls).toBe(2);

    // Index bounds and integer validation
    await expect(loader.getStarAstrophysics(0, 4, -1)).rejects.toThrow('star index -1 outside tile 0/4');
    await expect(loader.getStarAstrophysics(0, 4, 4)).rejects.toThrow('star index 4 outside tile 0/4');
    await expect(loader.getStarAstrophysics(0, 4, 1.5)).rejects.toThrow('star index 1.5 outside tile 0/4');
    await expect(loader.getStarAstrophysics(0, 4, Number.NaN)).rejects.toThrow('outside tile 0/4');

    const s0 = await loader.getStarAstrophysics(0, 4, 0);
    expect(s0).toEqual({
      evidenceKind: 'catalog-derived',
      temperatureK: null,
      luminositySolar: null,
      extinctionA0Mag: null,
      extinctionGMag: null,
      ruwe: null,
      parallaxErrorMas: null,
      sourceIds: [],
    });

    const s1 = await loader.getStarAstrophysics(0, 4, 1);
    expect(s1.luminositySolar?.median).toBeCloseTo(1, 5);
    expect(s1.luminositySolar?.lower).toBeNull();
    expect(s1.luminositySolar?.upper).toBeCloseTo(10 ** 0.1, 4);
    expect(s1.sourceIds).toEqual(['gaia-dr3-astrophysical-parameters']);

    const s2 = await loader.getStarAstrophysics(0, 4, 2);
    expect(s2.temperatureK).toEqual({ median: 5772, lower: 5750, upper: null });
    expect(s2.extinctionGMag).toBeCloseTo(0.125, 5);
    expect(s2.sourceIds).toEqual(['gaia-dr3']);

    const s3 = await loader.getStarAstrophysics(0, 4, 3);
    expect(s3.ruwe).toBe(1);
    expect(s3.parallaxErrorMas).toBe(0.5);
    expect(s3.sourceIds).toEqual(['gaia-dr3', 'gaia-dr3-astrophysical-parameters']);
  });

  it('matches the shared Python binary fixture through CatalogStarTileLoader end-to-end', async () => {
    const expected = JSON.parse(fs.readFileSync(repoFile('tests/fixtures/catalog_star_tiles/expected.json'), 'utf8'));
    const counts: Record<number, number> = { [expected.pix]: expected.stars.length };
    const manifest = buildManifest({
      formatVersion: 2,
      releaseId: 'fixture',
      layers: [
        { magMin: null, magMax: 6.5, order: 0, counts: {} },
        { magMin: 6.5, magMax: 9.5, order: 1, counts: {} },
        { magMin: 9.5, magMax: 12.0, order: expected.order, counts },
      ],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') return new Response(JSON.stringify(manifest), { status: 200 });
      if (url === '/stars/names.json') return new Response(JSON.stringify(createNamesPayload()), { status: 200 });
      if (url === `/stars/L2/${expected.pix}.core.bin`) {
        return new Response(readBuffer('tests/fixtures/catalog_star_tiles/core.bin'), { status: 200 });
      }
      if (url === `/stars/L2/${expected.pix}.detail.bin`) {
        return new Response(readBuffer('tests/fixtures/catalog_star_tiles/detail.bin'), { status: 200 });
      }
      if (url === `/stars/L2/${expected.pix}.astro.bin`) {
        return new Response(readBuffer('tests/fixtures/catalog_star_tiles/astro.bin'), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }) as unknown as typeof fetch;

    const loader = await CatalogStarTileLoader.open('/stars', { fetch: fetchMock });
    const rec0 = await loader.getStarRecord(2, expected.pix, 0);
    const dir0 = raDecDegToVec(rec0.raDeg, rec0.decDeg);
    const truth0 = raDecDegToVec(expected.stars[0].raDeg, expected.stars[0].decDeg);
    expect(angleBetween(dir0, truth0)).toBeLessThan(1e-5);
    expect(rec0.catalogId).toBe(expected.stars[0].catalogId);
  });
});

describe('CatalogStarTileLoader disposal lifecycle', () => {
  it('clears resident caches, ignores setView after dispose, and suppresses late in-flight callbacks and failures', async () => {
    const manifest = buildManifest({
      layers: [{ magMin: null, magMax: 6.5, order: 0, counts: { 0: 1, 4: 1, 8: 1 } }],
    });
    const pendingPix0 = deferred<Response>();
    const pendingPix8 = deferred<Response>();

    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/stars/manifest.json') {
        return Promise.resolve(new Response(JSON.stringify(manifest), { status: 200 }));
      }
      if (url === '/stars/L0/4.core.bin') {
        return Promise.resolve(
          new Response(encodeCoreBuffer({ layer: 0, order: 0, pix: 4 }, [{ magnitude: 3.0 }]), {
            status: 200,
          })
        );
      }
      if (url === '/stars/L0/0.core.bin') return pendingPix0.promise;
      if (url === '/stars/L0/8.core.bin') return pendingPix8.promise;
      return Promise.resolve(new Response(null, { status: 404 }));
    }) as unknown as typeof fetch;

    const loadedTiles: number[] = [];
    const errors: unknown[] = [];
    const loader = await CatalogStarTileLoader.open('/stars', {
      fetch: fetchMock,
      onTileLoaded: (t) => loadedTiles.push(t.pix),
      onError: (e) => errors.push(e),
    });

    loader.setView({ centerIcrs: tileCenter(0, 4), radiusRad: Math.PI, limitingMagnitude: 6.5 });
    await vi.waitFor(() => expect(loader.getLoadedTiles()).toHaveLength(1));
    expect(loadedTiles).toEqual([4]);
    expect(loader.getStats().tilesInFlight).toBe(2);

    loader.dispose();
    expect(loader.getLoadedTiles()).toEqual([]);
    expect(loader.getStats().tilesLoaded).toBe(0);
    expect(loader.getStats().tilesWanted).toBe(0);

    // Late resolution and late rejection after dispose must not mutate state or fire callbacks
    pendingPix0.resolve(
      new Response(encodeCoreBuffer({ layer: 0, order: 0, pix: 0 }, [{ magnitude: 4.0 }]), { status: 200 })
    );
    pendingPix8.reject(new Error('network aborted'));
    await vi.waitFor(() => expect(loader.getStats().tilesInFlight).toBe(0));

    expect(loader.getLoadedTiles()).toEqual([]);
    expect(loadedTiles).toEqual([4]);
    expect(errors).toEqual([]);
    expect(loader.getStats().failures).toBe(0);

    // Calling setView after dispose is a strict no-op
    const callsAfterDispose = vi.mocked(fetchMock).mock.calls.length;
    loader.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: 6.5 });
    expect(loader.getStats().tilesWanted).toBe(0);
    expect(vi.mocked(fetchMock).mock.calls.length).toBe(callsAfterDispose);
  });
});
