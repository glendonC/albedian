import { z } from 'zod';

/**
 * Catalog Star Tiles: the manifest and name index written by
 * `scripts/bake_catalog_star_tiles.py` and read by the web client's tile loader.
 *
 * Every star in these tiles is catalog data (`evidenceKind: 'catalog-derived'`): positions,
 * magnitudes and distances come from Gaia DR3, Bailer-Jones (2021) and Hipparcos 2007, never
 * from a user's recording.
 */

export const CATALOG_STAR_TILE_FORMAT = 'albedian.catalog-star-tiles';

/**
 * Version in the 32-byte header of every tile file, of every kind. The core and detail layouts
 * have not changed since 1, and astro tiles (kind 3) are written at 1 too, so a release that adds
 * astro tiles leaves its core and detail tiles byte for byte as they were.
 */
export const CATALOG_STAR_TILE_HEADER_VERSION = 1;

/**
 * Manifest formats: 1 lists core and detail tiles; 2 adds astro tiles (Gaia DR3 GSP-Phot and
 * FLAME parameters and astrometric quality). This contract reads both.
 */
export const CATALOG_STAR_TILE_MANIFEST_FORMAT_VERSION = 2;

/** @deprecated The tile header version under its format 1 name; use CATALOG_STAR_TILE_HEADER_VERSION. */
export const CATALOG_STAR_TILE_FORMAT_VERSION = CATALOG_STAR_TILE_HEADER_VERSION;

/** Encoding constants of tile format version 1; the decoder refuses manifests that disagree. */
export const CATALOG_STAR_TILE_ENCODING_V1 = {
  magic: 'ALBS',
  headerBytes: 32,
  magnitudeOffset: 2,
  magnitudeScale: 1000,
  colorScale: 1000,
  colorMissing: -32768,
  distanceLog10Min: -1,
  distanceLog10Step: 1e-4,
  distanceMissing: 0,
  boundMissing: 0,
  boundLog10FractionMin: -5,
  boundLowerLog10Span: 5,
  boundUpperLog10Span: 7,
  flags: {
    distanceKindMask: 3,
    magnitudeBandHp: 4,
    colorBMinusV: 8,
    catalogHipparcos: 16,
    named: 32,
  },
} as const;

/**
 * Astro tiles (kind 3, manifest format 2): Gaia DR3 astrophysical parameters and astrometric
 * quality, one tile per core tile, index-aligned with it. Structure of arrays, little-endian:
 * GSP-Phot effective temperature with its 16th and 84th percentiles (kelvin), FLAME luminosity
 * with its percentiles (log10 L/Lsun in 0.001 dex), GSP-Phot extinction A0 at 547.7 nm with its
 * percentiles and AG (both in mmag), RUWE and the parallax standard error (mas) as IEEE half
 * floats. A value the catalog lacks keeps its reserved code (NaN for half floats) and decodes to
 * NaN; nothing is filled. Hipparcos entries carry no Gaia astrophysics, so every value is missing.
 */
export const CATALOG_STAR_TILE_ASTRO_ENCODING = {
  kind: 3,
  bytesPerStar: 24,
  columns: [
    'teff:u16',
    'teffLower:u16',
    'teffUpper:u16',
    'logLum:i16',
    'logLumLower:i16',
    'logLumUpper:i16',
    'a0:u16',
    'a0Lower:u16',
    'a0Upper:u16',
    'ag:u16',
    'ruwe:f16',
    'parallaxError:f16',
  ],
  teffMissing: 0,
  logLumScale: 1000,
  logLumMissing: -32768,
  extinctionScale: 1000,
  extinctionMissing: 65535,
} as const;

export const CatalogStarDistanceKindSchema = z.enum([
  'none',
  'bailer-jones-2021-photogeometric',
  'bailer-jones-2021-geometric',
  'hipparcos-2007-parallax-inverse',
]);
export type CatalogStarDistanceKind = z.infer<typeof CatalogStarDistanceKindSchema>;

export const CatalogStarCatalogIdSchema = z.enum(['gaia-dr3', 'hipparcos-2007']);
export type CatalogStarCatalogId = z.infer<typeof CatalogStarCatalogIdSchema>;

const TileByteStatsSchema = z.object({
  min: z.number().int().nonnegative(),
  median: z.number().int().nonnegative(),
  max: z.number().int().nonnegative(),
});

const count = z.number().int().nonnegative();

const tileLayerShape = {
  layer: count,
  magMin: z.number().nullable(),
  magMax: z.number(),
  order: z.number().int().min(0).max(12),
  positionBits: z.union([z.literal(16), z.literal(32)]),
  stars: count,
  nonEmptyTiles: count,
  coreBytes: count,
  detailBytes: count,
  coreTileBytes: TileByteStatsSchema,
  detailTileBytes: TileByteStatsSchema,
  maxPositionErrorArcsec: z.number().nonnegative(),
  tileStarCounts: z.array(count),
};

const coversEveryPixel = (layer: { order: number; tileStarCounts: number[] }) =>
  layer.tileStarCounts.length === 12 * 4 ** layer.order;
const sumsToStars = (layer: { stars: number; tileStarCounts: number[] }) =>
  layer.tileStarCounts.reduce((a, b) => a + b, 0) === layer.stars;
const EVERY_PIXEL = { message: 'tileStarCounts must list every HEALPix pixel of the layer order' };
const SUMS_TO_STARS = { message: 'tileStarCounts must sum to the layer star count' };

export const CatalogStarTileLayerSchema = z
  .object(tileLayerShape)
  .refine(coversEveryPixel, EVERY_PIXEL)
  .refine(sumsToStars, SUMS_TO_STARS);
export type CatalogStarTileLayer = z.infer<typeof CatalogStarTileLayerSchema>;

/** A format 2 layer: format 1's fields plus the size of its astro tiles. */
export const CatalogStarTileLayerV2Schema = z
  .object({ ...tileLayerShape, astroBytes: count, astroTileBytes: TileByteStatsSchema })
  .refine(coversEveryPixel, EVERY_PIXEL)
  .refine(sumsToStars, SUMS_TO_STARS)
  .refine(
    (layer) =>
      layer.astroBytes ===
      CATALOG_STAR_TILE_ASTRO_ENCODING.bytesPerStar * layer.stars +
        CATALOG_STAR_TILE_ENCODING_V1.headerBytes * layer.nonEmptyTiles,
    { message: 'astroBytes must be one header per non-empty tile plus the astro bytes of every star' }
  );
export type CatalogStarTileLayerV2 = z.infer<typeof CatalogStarTileLayerV2Schema>;

export const CatalogStarSourceSchema = z
  .object({
    id: z.string().min(1),
    title: z.string().min(1),
    provider: z.string().min(1),
    citation: z.string().min(1),
    license: z.string().min(1),
    doi: z.string().optional(),
    table: z.string().optional(),
    archive: z.string().optional(),
    url: z.string().optional(),
    licenseUrl: z.string().optional(),
    acknowledgement: z.string().optional(),
    note: z.string().optional(),
  })
  .passthrough();
export type CatalogStarSource = z.infer<typeof CatalogStarSourceSchema>;

const EncodingV1Schema = z
  .object({
    byteOrder: z.literal('little-endian'),
    magic: z.literal(CATALOG_STAR_TILE_ENCODING_V1.magic),
    headerBytes: z.literal(CATALOG_STAR_TILE_ENCODING_V1.headerBytes),
    magnitude: z
      .object({
        offset: z.literal(CATALOG_STAR_TILE_ENCODING_V1.magnitudeOffset),
        scale: z.literal(CATALOG_STAR_TILE_ENCODING_V1.magnitudeScale),
      })
      .passthrough(),
    color: z
      .object({
        scale: z.literal(CATALOG_STAR_TILE_ENCODING_V1.colorScale),
        missing: z.literal(CATALOG_STAR_TILE_ENCODING_V1.colorMissing),
      })
      .passthrough(),
    distance: z
      .object({
        log10Min: z.literal(CATALOG_STAR_TILE_ENCODING_V1.distanceLog10Min),
        log10Step: z.literal(CATALOG_STAR_TILE_ENCODING_V1.distanceLog10Step),
        missing: z.literal(CATALOG_STAR_TILE_ENCODING_V1.distanceMissing),
      })
      .passthrough(),
    distanceBounds: z
      .object({
        missing: z.literal(CATALOG_STAR_TILE_ENCODING_V1.boundMissing),
        log10FractionMin: z.literal(CATALOG_STAR_TILE_ENCODING_V1.boundLog10FractionMin),
        lowerLog10Span: z.literal(CATALOG_STAR_TILE_ENCODING_V1.boundLowerLog10Span),
        upperLog10Span: z.literal(CATALOG_STAR_TILE_ENCODING_V1.boundUpperLog10Span),
      })
      .passthrough(),
    flags: z
      .object({
        distanceKindMask: z.literal(CATALOG_STAR_TILE_ENCODING_V1.flags.distanceKindMask),
        distanceKinds: z.record(z.string(), CatalogStarDistanceKindSchema),
        magnitudeBandHp: z.literal(CATALOG_STAR_TILE_ENCODING_V1.flags.magnitudeBandHp),
        colorBMinusV: z.literal(CATALOG_STAR_TILE_ENCODING_V1.flags.colorBMinusV),
        catalogHipparcos: z.literal(CATALOG_STAR_TILE_ENCODING_V1.flags.catalogHipparcos),
        named: z.literal(CATALOG_STAR_TILE_ENCODING_V1.flags.named),
      })
      .passthrough(),
  })
  .passthrough();

const ASTRO = CATALOG_STAR_TILE_ASTRO_ENCODING;

const AstroEncodingSchema = z
  .object({
    kind: z.literal(ASTRO.kind),
    bytesPerStar: z.literal(ASTRO.bytesPerStar),
    columns: z
      .array(z.string())
      .refine((columns) => columns.length === ASTRO.columns.length && columns.every((c, i) => c === ASTRO.columns[i]), {
        message: 'astro columns must be the contract columns, in order',
      }),
    teff: z.object({ missing: z.literal(ASTRO.teffMissing) }).passthrough(),
    logLum: z.object({ scale: z.literal(ASTRO.logLumScale), missing: z.literal(ASTRO.logLumMissing) }).passthrough(),
    extinction: z
      .object({ scale: z.literal(ASTRO.extinctionScale), missing: z.literal(ASTRO.extinctionMissing) })
      .passthrough(),
  })
  .passthrough();

const EncodingV2Schema = EncodingV1Schema.extend({ astro: AstroEncodingSchema });

const countsShape = { stars: count, gaiaSources: count, hipparcosStars: count, namedStars: count };

// Stars that carry each astro value; none can exceed the star count
const CountsV2Schema = z
  .object({
    ...countsShape,
    withTemperature: count,
    withLuminosity: count,
    withExtinction: count,
    withRuwe: count,
    withParallaxError: count,
  })
  .passthrough()
  .refine(
    (c) => [c.withTemperature, c.withLuminosity, c.withExtinction, c.withRuwe, c.withParallaxError].every((n) => n <= c.stars),
    { message: 'a count of stars with a value cannot exceed the star count' }
  );

const tilingShape = {
  scheme: z.literal('healpix'),
  ordering: z.literal('nested'),
  positionEncoding: z.literal('healpix-face-subpixel'),
  tilePath: z.string().min(1),
};

const manifestShape = {
  format: z.literal(CATALOG_STAR_TILE_FORMAT),
  releaseId: z.string().min(1),
  title: z.string().min(1),
  evidenceKind: z.literal('catalog-derived'),
  disclosure: z.string().min(1),
  frame: z.literal('ICRS'),
  epochJulianYear: z.number(),
  magnitudeLimit: z.number(),
  magnitudeBands: z.record(z.string(), z.string()),
  sources: z.array(CatalogStarSourceSchema).min(1),
  selection: z.record(z.string(), z.unknown()),
  files: z.object({
    names: z.string().min(1),
    brightStarMerge: z.string().min(1),
    checksums: z.string().min(1),
  }),
  bake: z.object({ script: z.string().min(1), command: z.string().min(1), verify: z.string().min(1) }),
};

/** Format 1: core and detail tiles. */
const CatalogStarTileManifestV1Schema = z.object({
  ...manifestShape,
  formatVersion: z.literal(1),
  counts: z.object(countsShape).passthrough(),
  tiling: z.object(tilingShape).passthrough(),
  layers: z.array(CatalogStarTileLayerSchema).min(1),
  encoding: EncodingV1Schema,
});

/** Format 2: format 1 plus astro tiles, their encoding, their sizes and how many stars carry each value. */
const CatalogStarTileManifestV2Schema = z.object({
  ...manifestShape,
  formatVersion: z.literal(2),
  counts: CountsV2Schema,
  tiling: z
    .object({ ...tilingShape, kinds: z.tuple([z.literal('core'), z.literal('detail'), z.literal('astro')]) })
    .passthrough(),
  layers: z.array(CatalogStarTileLayerV2Schema).min(1),
  encoding: EncodingV2Schema,
});

export const CatalogStarTileManifestSchema = z
  .discriminatedUnion('formatVersion', [CatalogStarTileManifestV1Schema, CatalogStarTileManifestV2Schema])
  .refine((m) => m.layers.every((layer, i) => layer.layer === i), {
    message: 'layers must be listed in order starting at 0',
  });
export type CatalogStarTileManifest = z.infer<typeof CatalogStarTileManifestSchema>;
export type CatalogStarTileManifestV2 = Extract<CatalogStarTileManifest, { formatVersion: 2 }>;

export const CatalogStarNameSchema = z.object({
  name: z.string().min(1),
  nameAscii: z.string().min(1),
  designation: z.string(),
  bayer: z.string(),
  constellation: z.string(),
  component: z.string(),
  hip: z.number().int().positive(),
  catalog: CatalogStarCatalogIdSchema,
  catalogId: z.string().regex(/^\d+$/),
  layer: z.number().int().nonnegative(),
  pix: z.number().int().nonnegative(),
  index: z.number().int().nonnegative(),
  raDeg: z.number().min(0).max(360),
  decDeg: z.number().min(-90).max(90),
  mag: z.number(),
  magBand: z.enum(['G', 'Hp']),
});
export type CatalogStarName = z.infer<typeof CatalogStarNameSchema>;

export const CatalogStarNameIndexSchema = z.object({
  format: z.literal(`${CATALOG_STAR_TILE_FORMAT}.names`),
  releaseId: z.string().min(1),
  evidenceKind: z.literal('catalog-derived'),
  source: z.literal('iau-csn'),
  sourceUpdated: z.string(),
  sourceSha256: z.string().regex(/^[0-9a-f]{64}$/),
  names: z.array(CatalogStarNameSchema),
});
export type CatalogStarNameIndex = z.infer<typeof CatalogStarNameIndexSchema>;
