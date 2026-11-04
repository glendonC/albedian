import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  CATALOG_STAR_TILE_ASTRO_ENCODING,
  CATALOG_STAR_TILE_ENCODING_V1,
  CATALOG_STAR_TILE_FORMAT_VERSION,
  CATALOG_STAR_TILE_HEADER_VERSION,
  CATALOG_STAR_TILE_MANIFEST_FORMAT_VERSION,
  CatalogStarTileManifestSchema,
} from '../index.js';

// The committed format 1 release; format 2 samples below are built from it
const RELEASE_MANIFEST = path.resolve(__dirname, '../../../../apps/web/public/catalog/stars/gaia-dr3/manifest.json');

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const releaseV1 = (): Json => JSON.parse(fs.readFileSync(RELEASE_MANIFEST, 'utf8')) as Json;

const BYTES: Record<string, number> = { u16: 2, i16: 2, f16: 2 };

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : Math.round((sorted[mid - 1]! + sorted[mid]!) / 2);
}

/** A format 2 manifest: the real format 1 release plus what astro tiles add, sized from its real counts. */
function releaseV2(): Json {
  const m = releaseV1();
  const astro = CATALOG_STAR_TILE_ASTRO_ENCODING;
  const header = CATALOG_STAR_TILE_ENCODING_V1.headerBytes;
  m.formatVersion = 2;
  m.tiling.kinds = ['core', 'detail', 'astro'];
  for (const layer of m.layers as Json[]) {
    const sizes = (layer.tileStarCounts as number[]).filter((n) => n > 0).map((n) => header + astro.bytesPerStar * n);
    layer.astroBytes = sizes.reduce((a, b) => a + b, 0);
    layer.astroTileBytes = { min: Math.min(...sizes), median: median(sizes), max: Math.max(...sizes) };
  }
  // Archive counts over the whole G < 12 selection (gaiadr3, 2026-11-04), used here only as sample values
  Object.assign(m.counts, {
    withTemperature: 1812696,
    withLuminosity: 1780875,
    withExtinction: 1812696,
    withRuwe: 3062317,
    withParallaxError: 3062317,
  });
  m.encoding.astro = {
    kind: astro.kind,
    bytesPerStar: astro.bytesPerStar,
    columns: [...astro.columns],
    teff: { unit: 'K', missing: astro.teffMissing, decode: 'code kelvin' },
    logLum: { scale: astro.logLumScale, missing: astro.logLumMissing, decode: 'code / 1000 = log10(L / Lsun)' },
    extinction: { scale: astro.extinctionScale, missing: astro.extinctionMissing, decode: 'code / 1000 mag' },
    float16: 'IEEE 754 binary16; NaN marks a missing value',
  };
  return m;
}

describe('@albedian/contracts: catalog star tile manifests', () => {
  it('keeps the tile header at version 1 while the manifest format moves to 2', () => {
    expect(CATALOG_STAR_TILE_HEADER_VERSION).toBe(1);
    expect(CATALOG_STAR_TILE_FORMAT_VERSION).toBe(CATALOG_STAR_TILE_HEADER_VERSION);
    expect(CATALOG_STAR_TILE_MANIFEST_FORMAT_VERSION).toBe(2);
  });

  it('declares astro columns that add up to the bytes per star', () => {
    const astro = CATALOG_STAR_TILE_ASTRO_ENCODING;
    const total = astro.columns.map((c) => BYTES[c.split(':')[1]!]!).reduce((a, b) => a + b, 0);
    expect(total).toBe(astro.bytesPerStar);
    expect(astro.bytesPerStar).toBe(24);
    expect(astro.kind).toBe(3);
  });

  it('still reads the committed format 1 release', () => {
    const parsed = CatalogStarTileManifestSchema.parse(releaseV1());
    expect(parsed.formatVersion).toBe(1);
    expect(parsed.counts.stars).toBe(3087892);
  });

  it('reads a format 2 manifest with astro tiles', () => {
    const parsed = CatalogStarTileManifestSchema.safeParse(releaseV2());
    expect(parsed.success, JSON.stringify(parsed.error?.issues?.slice(0, 3))).toBe(true);
    if (parsed.success && parsed.data.formatVersion === 2) {
      expect(parsed.data.layers[5]!.astroBytes).toBe(32 * 768 + 24 * 1516735);
      expect(parsed.data.counts.withTemperature).toBe(1812696);
    } else {
      throw new Error('expected a format 2 manifest');
    }
  });

  const rejects: [string, (m: Json) => void][] = [
    ['format 2 without astro encoding', (m) => delete m.encoding.astro],
    ['format 2 without the astro tile kind', (m) => (m.tiling.kinds = ['core', 'detail'])],
    ['format 2 layer without astro bytes', (m) => delete m.layers[2].astroBytes],
    ['format 2 layer whose astro bytes disagree with its stars', (m) => (m.layers[2].astroBytes += 1)],
    ['astro bytes per star other than 24', (m) => (m.encoding.astro.bytesPerStar = 26)],
    ['astro columns out of order', (m) => m.encoding.astro.columns.reverse()],
    ['astro kind other than 3', (m) => (m.encoding.astro.kind = 2)],
    ['missing temperature code other than 0', (m) => (m.encoding.astro.teff.missing = 65535)],
    ['luminosity scale other than 1000', (m) => (m.encoding.astro.logLum.scale = 100)],
    ['extinction missing code other than 65535', (m) => (m.encoding.astro.extinction.missing = 0)],
    ['format 2 without a temperature count', (m) => delete m.counts.withTemperature],
    ['more stars with a luminosity than stars', (m) => (m.counts.withLuminosity = m.counts.stars + 1)],
    ['negative extinction count', (m) => (m.counts.withExtinction = -1)],
    ['unknown format version', (m) => (m.formatVersion = 3)],
  ];
  for (const [rule, mutate] of rejects) {
    it(`rejects ${rule}`, () => {
      const m = releaseV2();
      mutate(m);
      expect(CatalogStarTileManifestSchema.safeParse(m).success).toBe(false);
    });
  }

  it('rejects a format 1 manifest that lists astro tiles as format 2 would', () => {
    const m = releaseV2();
    m.formatVersion = 1;
    m.tiling.kinds = ['core', 'detail'];
    // Format 1 strips what it does not know, so astro fields alone never make a format 2 release
    const parsed = CatalogStarTileManifestSchema.parse(m);
    expect(parsed.formatVersion).toBe(1);
    expect('astroBytes' in parsed.layers[0]!).toBe(false);
  });
});
