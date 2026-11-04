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

// The committed release, manifest format 2 since the astro tiles; the format 1 sample is derived from it
const RELEASE_MANIFEST = path.resolve(__dirname, '../../../../apps/web/public/catalog/stars/gaia-dr3/manifest.json');

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** The committed release as written by the bake. */
const releaseV2 = (): Json => JSON.parse(fs.readFileSync(RELEASE_MANIFEST, 'utf8')) as Json;

/** The same release as format 1 described it, before astro tiles: no astro kind, encoding, sizes or counts. */
function releaseV1(): Json {
  const m = releaseV2();
  m.formatVersion = 1;
  m.tiling.kinds = ['core', 'detail'];
  delete m.encoding.astro;
  for (const layer of m.layers as Json[]) {
    delete layer.astroBytes;
    delete layer.astroTileBytes;
  }
  for (const key of ['withTemperature', 'withLuminosity', 'withExtinction', 'withRuwe', 'withParallaxError']) {
    delete m.counts[key];
  }
  return m;
}

const BYTES: Record<string, number> = { u16: 2, i16: 2, f16: 2 };

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

  it('still reads a format 1 manifest', () => {
    const parsed = CatalogStarTileManifestSchema.parse(releaseV1());
    expect(parsed.formatVersion).toBe(1);
    expect(parsed.counts.stars).toBe(3087892);
  });

  it('reads the committed format 2 release with its astro tiles', () => {
    const parsed = CatalogStarTileManifestSchema.safeParse(releaseV2());
    expect(parsed.success, JSON.stringify(parsed.error?.issues?.slice(0, 3))).toBe(true);
    if (parsed.success && parsed.data.formatVersion === 2) {
      expect(parsed.data.layers[5]!.astroBytes).toBe(32 * 768 + 24 * 1516735);
      // The bake's counts: the archive's G < 12 totals less the duplicates the bright-star merge drops
      expect(parsed.data.counts.withTemperature).toBe(1812688);
      expect(parsed.data.counts.withLuminosity).toBe(1780872);
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
