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
export const CATALOG_STAR_TILE_FORMAT_VERSION = 1;

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

export const CatalogStarTileLayerSchema = z
  .object({
    layer: z.number().int().nonnegative(),
    magMin: z.number().nullable(),
    magMax: z.number(),
    order: z.number().int().min(0).max(12),
    positionBits: z.union([z.literal(16), z.literal(32)]),
    stars: z.number().int().nonnegative(),
    nonEmptyTiles: z.number().int().nonnegative(),
    coreBytes: z.number().int().nonnegative(),
    detailBytes: z.number().int().nonnegative(),
    coreTileBytes: TileByteStatsSchema,
    detailTileBytes: TileByteStatsSchema,
    maxPositionErrorArcsec: z.number().nonnegative(),
    tileStarCounts: z.array(z.number().int().nonnegative()),
  })
  .refine((layer) => layer.tileStarCounts.length === 12 * 4 ** layer.order, {
    message: 'tileStarCounts must list every HEALPix pixel of the layer order',
  })
  .refine((layer) => layer.tileStarCounts.reduce((a, b) => a + b, 0) === layer.stars, {
    message: 'tileStarCounts must sum to the layer star count',
  });
export type CatalogStarTileLayer = z.infer<typeof CatalogStarTileLayerSchema>;

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

export const CatalogStarTileManifestSchema = z
  .object({
    format: z.literal(CATALOG_STAR_TILE_FORMAT),
    formatVersion: z.literal(CATALOG_STAR_TILE_FORMAT_VERSION),
    releaseId: z.string().min(1),
    title: z.string().min(1),
    evidenceKind: z.literal('catalog-derived'),
    disclosure: z.string().min(1),
    frame: z.literal('ICRS'),
    epochJulianYear: z.number(),
    magnitudeLimit: z.number(),
    magnitudeBands: z.record(z.string(), z.string()),
    counts: z
      .object({
        stars: z.number().int().nonnegative(),
        gaiaSources: z.number().int().nonnegative(),
        hipparcosStars: z.number().int().nonnegative(),
        namedStars: z.number().int().nonnegative(),
      })
      .passthrough(),
    tiling: z
      .object({
        scheme: z.literal('healpix'),
        ordering: z.literal('nested'),
        positionEncoding: z.literal('healpix-face-subpixel'),
        tilePath: z.string().min(1),
      })
      .passthrough(),
    layers: z.array(CatalogStarTileLayerSchema).min(1),
    encoding: EncodingV1Schema,
    sources: z.array(CatalogStarSourceSchema).min(1),
    selection: z.record(z.string(), z.unknown()),
    files: z.object({
      names: z.string().min(1),
      brightStarMerge: z.string().min(1),
      checksums: z.string().min(1),
    }),
    bake: z.object({ script: z.string().min(1), command: z.string().min(1), verify: z.string().min(1) }),
  })
  .refine(
    (m) => m.layers.every((layer, i) => layer.layer === i),
    { message: 'layers must be listed in order starting at 0' }
  );
export type CatalogStarTileManifest = z.infer<typeof CatalogStarTileManifestSchema>;

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
