"""Catalog star tiles: HEALPix geometry, binary tile codec and bright-star merge rules.

Pure functions only (no network, no file I/O). The offline bake in
`scripts/bake_catalog_star_tiles.py` feeds these with Gaia DR3 and Hipparcos rows and
writes the results under `apps/web/public/catalog/stars/<release>/`. The browser decoder
in `apps/web/src/spatial/catalogStars/` mirrors the layout defined here.

Every value written by this codec is catalog data. Nothing here is measured from a user's
recording, so tiles carry `evidenceKind: catalog-derived` and must never be shown as observed.

Tile layout (little-endian), one file per (layer, HEALPix nested pixel) and kind:

    header (32 bytes)
      0  4s  magic b"ALBS"
      4  u16 format version (the header version, 1 for every kind)
      6  u8  kind (1 = core, 2 = detail, 3 = astro)
      7  u8  position bits (16 or 32 for core, 0 for detail and astro)
      8  u8  layer
      9  u8  HEALPix order
     10  u16 reserved (0)
     12  u32 HEALPix nested pixel index
     16  u32 star count n
     20  12  reserved (0)
    columns, structure of arrays, 4-byte columns first so every column stays aligned:
      core:   posU, posV (u16 or u32), mag (u16), color (i16), distance (u16), flags (u8)
      detail: idHi (u32), idLo (u32), pmRa (f16), pmDec (f16), radialVelocity (f16),
              distanceLower (u8), distanceUpper (u8)
      astro:  teff, teffLower, teffUpper (u16 K), logLum, logLumLower, logLumUpper
              (i16, 0.001 dex of L/Lsun), a0, a0Lower, a0Upper, ag (u16 mmag), ruwe (f16),
              parallaxError (f16 mas): Gaia DR3 GSP-Phot temperature and extinction, FLAME
              luminosity, each with its 16th and 84th percentiles, and astrometric quality
"""

from __future__ import annotations

from services.science.catalog_tile_binary import (
    TileHeader,
    core_stride,
    decode_astro_tile,
    decode_core_directions,
    decode_core_tile,
    decode_detail_tile,
    encode_astro_tile,
    encode_core_tile,
    encode_detail_tile,
    read_header,
)
from services.science.catalog_tile_constants import (
    ASTRO_LAYOUT,
    ASTRO_STRIDE,
    BOUND_LOG10_FRAC_MIN,
    BOUND_LOWER_LOG10_SPAN,
    BOUND_MISSING,
    BOUND_UPPER_LOG10_SPAN,
    COLOR_MISSING,
    COLOR_SCALE,
    DETAIL_STRIDE,
    DIST_KIND_BJ_GEO,
    DIST_KIND_BJ_PHOTOGEO,
    DIST_KIND_NONE,
    DIST_KIND_PARALLAX_INVERSE,
    DIST_LOG10_MIN,
    DIST_LOG10_STEP,
    DIST_MISSING,
    EXTINCTION_MISSING,
    EXTINCTION_SCALE,
    FLAG_CATALOG_HIPPARCOS,
    FLAG_COLOR_B_V,
    FLAG_DIST_KIND_MASK,
    FLAG_MAG_BAND_HP,
    FLAG_NAMED,
    HALF_PI,
    KIND_ASTRO,
    KIND_CORE,
    KIND_DETAIL,
    LOG_LUM_MISSING,
    LOG_LUM_SCALE,
    MAG_OFFSET,
    MAG_SCALE,
    MAS_TO_RAD,
    SAME_STAR_RADIUS_ARCSEC,
    TEFF_MISSING,
    TILE_FORMAT_VERSION,
    TILE_HEADER_BYTES,
    TILE_MAGIC,
)
from services.science.catalog_tile_healpix import (
    face_xy_to_nest,
    face_xy_to_vec,
    gaia_source_id_healpix,
    nest_to_face_ixiy,
    radec_to_face_xy,
    radec_to_nest,
    radec_to_vec,
    vec_to_radec,
)
from services.science.catalog_tile_merge import (
    BrightMergeDecision,
    angular_separation_arcsec,
    merge_bright_stars,
    propagate_linear,
)
from services.science.catalog_tile_scalars import (
    decode_color,
    decode_distance,
    decode_distance_bounds,
    decode_extinction,
    decode_f16,
    decode_luminosity,
    decode_mag,
    decode_teff,
    dequantize_unit,
    encode_color,
    encode_distance,
    encode_distance_bounds,
    encode_extinction,
    encode_f16,
    encode_luminosity,
    encode_mag,
    encode_teff,
    quantize_unit,
)

__all__ = [
    "ASTRO_LAYOUT",
    "ASTRO_STRIDE",
    "BOUND_LOG10_FRAC_MIN",
    "BOUND_LOWER_LOG10_SPAN",
    "BOUND_MISSING",
    "BOUND_UPPER_LOG10_SPAN",
    "BrightMergeDecision",
    "COLOR_MISSING",
    "COLOR_SCALE",
    "DETAIL_STRIDE",
    "DIST_KIND_BJ_GEO",
    "DIST_KIND_BJ_PHOTOGEO",
    "DIST_KIND_NONE",
    "DIST_KIND_PARALLAX_INVERSE",
    "DIST_LOG10_MIN",
    "DIST_LOG10_STEP",
    "DIST_MISSING",
    "EXTINCTION_MISSING",
    "EXTINCTION_SCALE",
    "FLAG_CATALOG_HIPPARCOS",
    "FLAG_COLOR_B_V",
    "FLAG_DIST_KIND_MASK",
    "FLAG_MAG_BAND_HP",
    "FLAG_NAMED",
    "HALF_PI",
    "KIND_ASTRO",
    "KIND_CORE",
    "KIND_DETAIL",
    "LOG_LUM_MISSING",
    "LOG_LUM_SCALE",
    "MAG_OFFSET",
    "MAG_SCALE",
    "MAS_TO_RAD",
    "SAME_STAR_RADIUS_ARCSEC",
    "TEFF_MISSING",
    "TILE_FORMAT_VERSION",
    "TILE_HEADER_BYTES",
    "TILE_MAGIC",
    "TileHeader",
    "angular_separation_arcsec",
    "core_stride",
    "decode_astro_tile",
    "decode_color",
    "decode_core_directions",
    "decode_core_tile",
    "decode_detail_tile",
    "decode_distance",
    "decode_distance_bounds",
    "decode_extinction",
    "decode_f16",
    "decode_luminosity",
    "decode_mag",
    "decode_teff",
    "dequantize_unit",
    "encode_astro_tile",
    "encode_color",
    "encode_core_tile",
    "encode_detail_tile",
    "encode_distance",
    "encode_distance_bounds",
    "encode_extinction",
    "encode_f16",
    "encode_luminosity",
    "encode_mag",
    "encode_teff",
    "face_xy_to_nest",
    "face_xy_to_vec",
    "gaia_source_id_healpix",
    "merge_bright_stars",
    "nest_to_face_ixiy",
    "propagate_linear",
    "quantize_unit",
    "radec_to_face_xy",
    "radec_to_nest",
    "radec_to_vec",
    "read_header",
    "vec_to_radec",
]
