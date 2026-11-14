"""Format constants, bit flags, and scalar codec parameters for catalog star tiles.

Every value encoded with these constants represents external catalog data
(`evidenceKind: catalog-derived`) and is never measured from a user's recording.
"""

from __future__ import annotations

import math

TILE_MAGIC = b"ALBS"
TILE_FORMAT_VERSION = 1
TILE_HEADER_BYTES = 32
KIND_CORE = 1
KIND_DETAIL = 2
KIND_ASTRO = 3

# --- Flags (one byte per star, core tile) ---------------------------------------------------
DIST_KIND_NONE = 0
DIST_KIND_BJ_PHOTOGEO = 1
DIST_KIND_BJ_GEO = 2
DIST_KIND_PARALLAX_INVERSE = 3
FLAG_DIST_KIND_MASK = 0b0000_0011
FLAG_MAG_BAND_HP = 0b0000_0100  # set: Hipparcos Hp, clear: Gaia G
FLAG_COLOR_B_V = 0b0000_1000  # set: Johnson B-V from Hipparcos, clear: Gaia BP-RP
FLAG_CATALOG_HIPPARCOS = 0b0001_0000  # set: Hipparcos 2007 entry (id = HIP), clear: Gaia DR3
FLAG_NAMED = 0b0010_0000  # star carries an IAU WGSN proper name in names.json

# --- Scalar encodings ---------------------------------------------------------------------------
MAG_OFFSET = 2.0  # mag code = round((mag + 2) * 1000), 1 mmag steps
MAG_SCALE = 1000.0
COLOR_SCALE = 1000.0  # color code = round(index * 1000), 1 mmag steps
COLOR_MISSING = -32768
DIST_LOG10_MIN = -1.0  # distance code 1 = 0.1 pc
DIST_LOG10_STEP = 1e-4  # 0.023 % relative steps, code 65535 = 357 kpc
DIST_MISSING = 0
BOUND_MISSING = 0
BOUND_LOG10_FRAC_MIN = -5.0  # fractional bound (|bound - median| / median) floor of 1e-5
BOUND_LOWER_LOG10_SPAN = 5.0  # lower fraction in [1e-5, 1]
BOUND_UPPER_LOG10_SPAN = 7.0  # upper fraction in [1e-5, 100], larger values clamp to code 255
# Astro tiles: a value the catalog lacks keeps its reserved code and decodes to NaN
TEFF_MISSING = 0  # temperature code = round(kelvin), 1 K steps
LOG_LUM_SCALE = 1000.0  # luminosity code = round(log10(L / Lsun) * 1000), 0.001 dex steps
LOG_LUM_MISSING = -32768
EXTINCTION_SCALE = 1000.0  # extinction code = round(mag * 1000), 1 mmag steps
EXTINCTION_MISSING = 65535

# --- Spherical geometry and cross-match constants -----------------------------------------------
HALF_PI = math.pi / 2.0
SAME_STAR_RADIUS_ARCSEC = 2.0  # bright merge: a Gaia source this close to a Hipparcos star is that star
MAS_TO_RAD = math.pi / (180.0 * 3600.0 * 1000.0)

# --- Binary column layouts and strides ----------------------------------------------------------
DETAIL_STRIDE = 4 + 4 + 2 + 2 + 2 + 1 + 1

ASTRO_LAYOUT: list[tuple[str, str]] = [
    ("teff", "<u2"),
    ("teffLower", "<u2"),
    ("teffUpper", "<u2"),
    ("logLum", "<i2"),
    ("logLumLower", "<i2"),
    ("logLumUpper", "<i2"),
    ("a0", "<u2"),
    ("a0Lower", "<u2"),
    ("a0Upper", "<u2"),
    ("ag", "<u2"),
    ("ruwe", "<u2"),
    ("parallaxError", "<u2"),
]
ASTRO_STRIDE = 2 * len(ASTRO_LAYOUT)

__all__ = [
    "ASTRO_LAYOUT",
    "ASTRO_STRIDE",
    "BOUND_LOG10_FRAC_MIN",
    "BOUND_LOWER_LOG10_SPAN",
    "BOUND_MISSING",
    "BOUND_UPPER_LOG10_SPAN",
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
]
