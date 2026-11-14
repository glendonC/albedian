"""Characterization tests pinning the public API surface and domain behavior of catalog_tiles.

Covers:
- Exact public API surface (constants, frozen dataclasses, function signatures, and __all__ when present)
- HEALPix nested geometry, spherical coordinate conversions, and Gaia source_id pixel extraction
- Scalar codecs (magnitude, color, distance, distance bounds, float16, temperature, luminosity, extinction, unit)
- Binary tile header and column codecs (core 16/32-bit, detail, astro, empty tiles, corruption checks)
- Astrometry propagation and Hipparcos + Gaia DR3 bright-star merge rules across all decision branches
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
import inspect
import math
import struct

from hypothesis import given, settings
from hypothesis import strategies as st
import numpy as np
import pytest

from services.science import catalog_tiles as ct

EXPECTED_CONSTANTS: dict[str, object] = {
    "TILE_MAGIC": b"ALBS",
    "TILE_FORMAT_VERSION": 1,
    "TILE_HEADER_BYTES": 32,
    "KIND_CORE": 1,
    "KIND_DETAIL": 2,
    "KIND_ASTRO": 3,
    "DIST_KIND_NONE": 0,
    "DIST_KIND_BJ_PHOTOGEO": 1,
    "DIST_KIND_BJ_GEO": 2,
    "DIST_KIND_PARALLAX_INVERSE": 3,
    "FLAG_DIST_KIND_MASK": 0b0000_0011,
    "FLAG_MAG_BAND_HP": 0b0000_0100,
    "FLAG_COLOR_B_V": 0b0000_1000,
    "FLAG_CATALOG_HIPPARCOS": 0b0001_0000,
    "FLAG_NAMED": 0b0010_0000,
    "MAG_OFFSET": 2.0,
    "MAG_SCALE": 1000.0,
    "COLOR_SCALE": 1000.0,
    "COLOR_MISSING": -32768,
    "DIST_LOG10_MIN": -1.0,
    "DIST_LOG10_STEP": 1e-4,
    "DIST_MISSING": 0,
    "BOUND_MISSING": 0,
    "BOUND_LOG10_FRAC_MIN": -5.0,
    "BOUND_LOWER_LOG10_SPAN": 5.0,
    "BOUND_UPPER_LOG10_SPAN": 7.0,
    "TEFF_MISSING": 0,
    "LOG_LUM_SCALE": 1000.0,
    "LOG_LUM_MISSING": -32768,
    "EXTINCTION_SCALE": 1000.0,
    "EXTINCTION_MISSING": 65535,
    "HALF_PI": math.pi / 2.0,
    "SAME_STAR_RADIUS_ARCSEC": 2.0,
    "MAS_TO_RAD": math.pi / (180.0 * 3600.0 * 1000.0),
    "DETAIL_STRIDE": 16,
    "ASTRO_LAYOUT": [
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
    ],
    "ASTRO_STRIDE": 24,
}

EXPECTED_DATACLASSES: dict[str, tuple[str, ...]] = {
    "TileHeader": ("kind", "position_bits", "layer", "order", "pix", "count"),
    "BrightMergeDecision": ("action", "hip", "gaia_source_id", "reason"),
}

P = inspect.Parameter.POSITIONAL_OR_KEYWORD
K = inspect.Parameter.KEYWORD_ONLY

EXPECTED_FUNCTIONS: dict[str, list[tuple[str, inspect._ParameterKind]]] = {
    # HEALPix and spherical directions
    "radec_to_face_xy": [("ra_deg", P), ("dec_deg", P)],
    "face_xy_to_vec": [("face", P), ("x", P), ("y", P)],
    "face_xy_to_nest": [("order", P), ("face", P), ("x", P), ("y", P)],
    "nest_to_face_ixiy": [("order", P), ("pix", P)],
    "radec_to_nest": [("order", P), ("ra_deg", P), ("dec_deg", P)],
    "vec_to_radec": [("vec", P)],
    "radec_to_vec": [("ra_deg", P), ("dec_deg", P)],
    "gaia_source_id_healpix": [("source_id", P), ("order", P)],
    # Scalar codecs
    "encode_mag": [("mag", P)],
    "decode_mag": [("code", P)],
    "encode_color": [("index", P)],
    "decode_color": [("code", P)],
    "encode_distance": [("distance_pc", P)],
    "decode_distance": [("code", P)],
    "encode_distance_bounds": [("median_pc", P), ("lower_pc", P), ("upper_pc", P)],
    "decode_distance_bounds": [("median_pc", P), ("lower_code", P), ("upper_code", P)],
    "encode_f16": [("values", P)],
    "decode_f16": [("bits", P)],
    "encode_teff": [("kelvin", P)],
    "decode_teff": [("code", P)],
    "encode_luminosity": [("lsun", P)],
    "decode_luminosity": [("code", P)],
    "encode_extinction": [("mag", P)],
    "decode_extinction": [("code", P)],
    "quantize_unit": [("u", P), ("bits", P)],
    "dequantize_unit": [("code", P), ("bits", P)],
    # Binary tile codec
    "read_header": [("blob", P)],
    "core_stride": [("position_bits", P)],
    "encode_core_tile": [
        ("layer", K),
        ("order", K),
        ("pix", K),
        ("position_bits", K),
        ("pos_u", K),
        ("pos_v", K),
        ("mag_code", K),
        ("color_code", K),
        ("distance_code", K),
        ("flags", K),
    ],
    "encode_detail_tile": [
        ("layer", K),
        ("order", K),
        ("pix", K),
        ("id_hi", K),
        ("id_lo", K),
        ("pm_ra_f16", K),
        ("pm_dec_f16", K),
        ("rv_f16", K),
        ("lower_code", K),
        ("upper_code", K),
    ],
    "encode_astro_tile": [
        ("layer", K),
        ("order", K),
        ("pix", K),
        ("teff", K),
        ("teff_lower", K),
        ("teff_upper", K),
        ("log_lum", K),
        ("log_lum_lower", K),
        ("log_lum_upper", K),
        ("a0", K),
        ("a0_lower", K),
        ("a0_upper", K),
        ("ag", K),
        ("ruwe_f16", K),
        ("parallax_error_f16", K),
    ],
    "decode_core_tile": [("blob", P)],
    "decode_detail_tile": [("blob", P)],
    "decode_astro_tile": [("blob", P)],
    "decode_core_directions": [("h", P), ("cols", P)],
    # Astrometry and bright-star merge
    "propagate_linear": [
        ("ra_deg", P),
        ("dec_deg", P),
        ("pm_ra_mas_yr", P),
        ("pm_dec_mas_yr", P),
        ("years", P),
    ],
    "angular_separation_arcsec": [("vec_a", P), ("vec_b", P)],
    "merge_bright_stars": [
        ("gaia_source_id", K),
        ("gaia_vec", K),
        ("gaia_mag", K),
        ("hip", K),
        ("hip_vec_gaia_epoch", K),
        ("hip_mag", K),
        ("hip_best_neighbour", K),
        ("hip_best_neighbour_mag", K),
        ("gaia_poor_brighter_than", K),
        ("gaia_mag_limit", K),
        ("duplicate_radius_arcsec", K),
        ("duplicate_mag_limit", K),
    ],
}

EXPECTED_PUBLIC_NAMES = set(EXPECTED_CONSTANTS) | set(EXPECTED_DATACLASSES) | set(EXPECTED_FUNCTIONS)
_STDLIB_OR_THIRD_PARTY_BINDINGS = {"annotations", "dataclass", "math", "np", "struct"}


# =============================================================================================
# 1. Public API surface, constants, dataclasses, and function signatures
# =============================================================================================


def test_public_surface_names_are_exact() -> None:
    actual_public = {name for name in vars(ct) if not name.startswith("_")} - _STDLIB_OR_THIRD_PARTY_BINDINGS
    assert actual_public == EXPECTED_PUBLIC_NAMES
    if hasattr(ct, "__all__"):
        assert len(ct.__all__) == len(set(ct.__all__))
        assert set(ct.__all__) == EXPECTED_PUBLIC_NAMES


def test_public_constants_preserve_exact_values_and_types() -> None:
    for name, expected in EXPECTED_CONSTANTS.items():
        actual = getattr(ct, name)
        assert type(actual) is type(expected), name
        assert actual == expected, name

    flag_masks = (
        ct.FLAG_DIST_KIND_MASK,
        ct.FLAG_MAG_BAND_HP,
        ct.FLAG_COLOR_B_V,
        ct.FLAG_CATALOG_HIPPARCOS,
        ct.FLAG_NAMED,
    )
    combined = 0
    for mask in flag_masks:
        assert (combined & mask) == 0
        combined |= mask
    assert combined == 0b0011_1111


def test_public_dataclasses_are_frozen_with_exact_fields() -> None:
    for cls_name, expected_fields in EXPECTED_DATACLASSES.items():
        cls = getattr(ct, cls_name)
        assert is_dataclass(cls), cls_name
        assert tuple(f.name for f in fields(cls)) == expected_fields

    header = ct.TileHeader(kind=1, position_bits=16, layer=0, order=2, pix=7, count=3)
    with pytest.raises(FrozenInstanceError):
        header.count = 9  # type: ignore[misc]

    decision = ct.BrightMergeDecision(action="keep_gaia", hip=1, gaia_source_id=10, reason="ok")
    with pytest.raises(FrozenInstanceError):
        decision.action = "drop_gaia"  # type: ignore[misc]


def test_public_function_signatures_match_contract() -> None:
    for fn_name, expected_params in EXPECTED_FUNCTIONS.items():
        fn = getattr(ct, fn_name)
        assert callable(fn), fn_name
        sig = inspect.signature(fn)
        actual_params = [(p.name, p.kind) for p in sig.parameters.values()]
        assert actual_params == expected_params, fn_name


# =============================================================================================
# 2. HEALPix nested geometry and spherical directions
# =============================================================================================


def test_healpix_covers_all_twelve_base_faces_and_inverts_cleanly() -> None:
    # Sample centres across north polar (45, 135, 225, 315 at +60 deg),
    # equatorial (0, 90, 180, 270 at 0 deg), and south polar (45, 135, 225, 315 at -60 deg)
    ra = np.array([45.0, 135.0, 225.0, 315.0, 0.0, 90.0, 180.0, 270.0, 45.0, 135.0, 225.0, 315.0])
    dec = np.array([60.0, 60.0, 60.0, 60.0, 0.0, 0.0, 0.0, 0.0, -60.0, -60.0, -60.0, -60.0])
    face, x, y = ct.radec_to_face_xy(ra, dec)
    assert face.tolist() == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert np.all((x >= 0.0) & (x <= 1.0)) and np.all((y >= 0.0) & (y <= 1.0))

    vec = ct.face_xy_to_vec(face, x, y)
    assert np.allclose(np.linalg.norm(vec, axis=-1), 1.0, atol=1e-15)
    ra_back, dec_back = ct.vec_to_radec(vec)
    sep = ct.angular_separation_arcsec(vec, ct.radec_to_vec(ra, dec))
    assert np.max(sep) < 1e-7
    assert np.allclose(dec_back, dec, atol=1e-11)
    assert np.allclose((ra_back - ra + 180.0) % 360.0 - 180.0, 0.0, atol=1e-11)


def test_healpix_morton_nesting_and_order_bounds() -> None:
    with pytest.raises(ValueError, match="order"):
        ct.face_xy_to_nest(-1, [0], [0.5], [0.5])
    with pytest.raises(ValueError, match="order"):
        ct.face_xy_to_nest(16, [0], [0.5], [0.5])

    # At order 2 (nside = 4): (ix=1, iy=0) -> subpixel 1, (ix=0, iy=1) -> subpixel 2, (ix=3, iy=3) -> subpixel 15
    face = np.array([5, 5, 5], dtype=np.int64)
    x = np.array([1.25 / 4.0, 0.75 / 4.0, 3.5 / 4.0])
    y = np.array([0.25 / 4.0, 1.5 / 4.0, 3.875 / 4.0])
    pix, u, v = ct.face_xy_to_nest(2, face, x, y)
    assert pix.tolist() == [(5 << 4) + 1, (5 << 4) + 2, (5 << 4) + 15]
    assert np.allclose(u, [0.25, 0.75, 0.5])
    assert np.allclose(v, [0.25, 0.5, 0.875])

    f_out, ix_out, iy_out = ct.nest_to_face_ixiy(2, pix)
    assert f_out.tolist() == [5, 5, 5]
    assert ix_out.tolist() == [1, 0, 3]
    assert iy_out.tolist() == [0, 1, 3]


def test_healpix_order_15_and_gaia_source_id_hierarchy() -> None:
    ra = np.array([101.287155, 279.234735, 37.954561])
    dec = np.array([-16.716116, 38.783689, 89.264109])
    face, x, y = ct.radec_to_face_xy(ra, dec)
    pix15, u15, v15 = ct.face_xy_to_nest(15, face, x, y)
    f15, ix15, iy15 = ct.nest_to_face_ixiy(15, pix15)
    nside15 = float(1 << 15)
    vec15 = ct.face_xy_to_vec(f15, (ix15 + u15) / nside15, (iy15 + v15) / nside15)
    assert ct.angular_separation_arcsec(vec15, ct.radec_to_vec(ra, dec)).max() < 1e-6

    pix12 = ct.radec_to_nest(12, ra, dec)
    synthetic_source_id = (pix12 << 35) | np.array([12345, 67890, 34359738367], dtype=np.int64)
    for order in (0, 1, 4, 8, 12):
        assert np.array_equal(ct.gaia_source_id_healpix(synthetic_source_id, order), ct.radec_to_nest(order, ra, dec))


@settings(max_examples=120, deadline=None)
@given(
    st.floats(0.0, 360.0, exclude_max=True, allow_nan=False, allow_infinity=False),
    st.floats(-90.0, 90.0, allow_nan=False, allow_infinity=False),
    st.integers(0, 15),
)
def test_pbt_healpix_nested_round_trip_all_orders(ra: float, dec: float, order: int) -> None:
    face, x, y = ct.radec_to_face_xy([ra], [dec])
    pix, u, v = ct.face_xy_to_nest(order, face, x, y)
    assert 0 <= int(pix[0]) < 12 * (1 << (2 * order))
    assert 0.0 <= float(u[0]) < 1.0 and 0.0 <= float(v[0]) < 1.0
    f2, ix, iy = ct.nest_to_face_ixiy(order, pix)
    nside = float(1 << order)
    vec = ct.face_xy_to_vec(f2, (ix + u) / nside, (iy + v) / nside)
    assert float(ct.angular_separation_arcsec(vec, ct.radec_to_vec([ra], [dec]))[0]) < 1e-5


def test_propagate_linear_and_angular_separation() -> None:
    # Zero and NaN proper motions leave directions fixed
    ra0, dec0 = np.array([120.0, 359.9]), np.array([60.0, -30.0])
    ra_moved, dec_moved = ct.propagate_linear(ra0, dec0, [np.nan, 0.0], [0.0, np.nan], 25.0)
    assert np.allclose(ra_moved, ra0, atol=1e-12) and np.allclose(dec_moved, dec0, atol=1e-12)

    # Pure northward proper motion of 1000 mas/yr over 10 yr = 10 arcsec along meridian
    ra1, dec1 = ct.propagate_linear([45.0], [10.0], [0.0], [1000.0], 10.0)
    assert abs(float(ra1[0]) - 45.0) < 1e-9
    assert abs((float(dec1[0]) - 10.0) * 3600.0 - 10.0) < 1e-5

    # Pure eastward proper motion (pmra includes cos dec): 1000 mas/yr over 10 yr = 10 arcsec great-circle shift
    ra2, dec2 = ct.propagate_linear([45.0], [60.0], [1000.0], [0.0], 10.0)
    sep = float(ct.angular_separation_arcsec(ct.radec_to_vec([45.0], [60.0]), ct.radec_to_vec(ra2, dec2))[0])
    assert abs(sep - 10.0) < 1e-4

    # Antipodal and orthogonal vectors
    v_x = np.array([[1.0, 0.0, 0.0]])
    v_y = np.array([[0.0, 1.0, 0.0]])
    v_neg_x = np.array([[-1.0, 0.0, 0.0]])
    assert abs(float(ct.angular_separation_arcsec(v_x, v_y)[0]) - 90.0 * 3600.0) < 1e-9
    assert abs(float(ct.angular_separation_arcsec(v_x, v_neg_x)[0]) - 180.0 * 3600.0) < 1e-9


# =============================================================================================
# 3. Scalar codecs
# =============================================================================================


def test_scalar_codec_boundaries_clamping_and_errors() -> None:
    # Magnitude: [-2.0, 63.535] in 1 mmag steps
    mag_codes = ct.encode_mag([-2.0, 0.0, 63.535])
    assert mag_codes.dtype == np.uint16
    assert mag_codes.tolist() == [0, 2000, 65535]
    assert np.allclose(ct.decode_mag(mag_codes), [-2.0, 0.0, 63.535])
    with pytest.raises(ValueError, match="magnitude"):
        ct.encode_mag([-2.001])
    with pytest.raises(ValueError, match="magnitude"):
        ct.encode_mag([63.536])

    # Color: 1 mmag steps, clamps finite extremes to +-32767 so -32768 stays reserved for missing
    col_codes = ct.encode_color([-50.0, 0.0, 50.0, np.nan, np.inf, -np.inf])
    assert col_codes.dtype == np.int16
    assert col_codes.tolist() == [-32767, 0, 32767, ct.COLOR_MISSING, ct.COLOR_MISSING, ct.COLOR_MISSING]
    col_back = ct.decode_color(col_codes)
    assert col_back[0] == -32.767 and col_back[1] == 0.0 and col_back[2] == 32.767
    assert np.all(np.isnan(col_back[3:]))

    # Distance: code 0 missing, code 1 = 0.1 pc, code 65535 = 10**5.5534 pc
    max_pc = 10.0 ** (ct.DIST_LOG10_MIN + 65534 * ct.DIST_LOG10_STEP)
    dist_codes = ct.encode_distance([0.1, 10.0, max_pc, 0.0, -3.0, np.nan, np.inf])
    assert dist_codes.dtype == np.uint16
    assert dist_codes[:3].tolist() == [1, 20001, 65535]
    assert dist_codes[3:].tolist() == [0, 0, 0, 0]
    dist_back = ct.decode_distance(dist_codes)
    assert np.allclose(dist_back[:3], [0.1, 10.0, max_pc], rtol=1e-12)
    assert np.all(np.isnan(dist_back[3:]))
    with pytest.raises(ValueError, match="distance"):
        ct.encode_distance([0.05])
    with pytest.raises(ValueError, match="distance"):
        ct.encode_distance([400_000.0])

    # Distance bounds: missing propagation and span clamping
    lo_c, hi_c = ct.encode_distance_bounds(
        [100.0, 100.0, np.nan, -10.0],
        [90.0, np.nan, 90.0, 5.0],
        [120.0, 100_000.0, 110.0, 15.0],
    )
    assert lo_c.dtype == np.uint8 and hi_c.dtype == np.uint8
    assert lo_c[0] > 0 and lo_c[1] == ct.BOUND_MISSING and lo_c[2] == ct.BOUND_MISSING and lo_c[3] == ct.BOUND_MISSING
    assert hi_c[1] == 255  # fraction 999 clamps to top code 255
    lo_pc, hi_pc = ct.decode_distance_bounds([100.0, 100.0, 100.0, 100.0], lo_c, hi_c)
    assert abs(lo_pc[0] - 90.0) < 2.0 and abs(hi_pc[0] - 120.0) < 2.0
    assert np.isnan(lo_pc[1]) and abs(hi_pc[1] - 10_100.0) < 1e-6

    # Float16
    f16 = ct.encode_f16([0.0, -65504.0, 65504.0, np.nan])
    assert f16.dtype == np.uint16
    f16_back = ct.decode_f16(f16)
    assert f16_back[0] == 0.0 and f16_back[1] == -65504.0 and f16_back[2] == 65504.0 and np.isnan(f16_back[3])
    with pytest.raises(ValueError, match="float16"):
        ct.encode_f16([65520.0])

    # Teff, luminosity, extinction
    assert ct.encode_teff([1.0, 65535.0, np.nan]).tolist() == [1, 65535, ct.TEFF_MISSING]
    assert ct.encode_luminosity([1.0, 10.0, np.nan]).tolist() == [0, 1000, ct.LOG_LUM_MISSING]
    assert ct.encode_extinction([0.0, 65.534, np.nan]).tolist() == [0, 65534, ct.EXTINCTION_MISSING]
    with pytest.raises(ValueError, match="extinction"):
        ct.encode_extinction([65.535])

    # Unit quantizer
    q16 = ct.quantize_unit([-0.1, 0.0, 0.5, 1.0, 1.2], 16)
    q32 = ct.quantize_unit([0.0, 0.5, 1.0], 32)
    assert q16.dtype == np.uint16 and q32.dtype == np.uint32
    assert q16.tolist() == [0, 0, 32768, 65535, 65535]
    assert np.allclose(ct.dequantize_unit([0, 65535], 16), [0.5 / 65536.0, 65535.5 / 65536.0])


# =============================================================================================
# 4. Binary tile codecs (core, detail, astro)
# =============================================================================================


def test_binary_tile_header_strides_empty_tiles_and_validation() -> None:
    assert ct.core_stride(16) == 11
    assert ct.core_stride(32) == 15
    assert ct.DETAIL_STRIDE == 16
    assert ct.ASTRO_STRIDE == 24

    with pytest.raises(ValueError, match="position_bits"):
        ct.encode_core_tile(
            layer=0,
            order=0,
            pix=0,
            position_bits=24,
            pos_u=np.array([], dtype=np.uint16),
            pos_v=np.array([], dtype=np.uint16),
            mag_code=np.array([], dtype=np.uint16),
            color_code=np.array([], dtype=np.int16),
            distance_code=np.array([], dtype=np.uint16),
            flags=np.array([], dtype=np.uint8),
        )

    # Empty tiles (count = 0) round-trip cleanly across all three kinds
    empty_core = ct.encode_core_tile(
        layer=1,
        order=2,
        pix=7,
        position_bits=32,
        pos_u=np.array([], dtype=np.uint32),
        pos_v=np.array([], dtype=np.uint32),
        mag_code=np.array([], dtype=np.uint16),
        color_code=np.array([], dtype=np.int16),
        distance_code=np.array([], dtype=np.uint16),
        flags=np.array([], dtype=np.uint8),
    )
    assert len(empty_core) == ct.TILE_HEADER_BYTES
    magic, ver, kind, pbits, layer, order, res0, pix, count, res1 = struct.unpack("<4sHBBBBHII12s", empty_core)
    assert (magic, ver, kind, pbits, layer, order, res0, pix, count, res1) == (
        b"ALBS",
        1,
        ct.KIND_CORE,
        32,
        1,
        2,
        0,
        7,
        0,
        bytes(12),
    )
    hc, cc = ct.decode_core_tile(empty_core)
    assert hc == ct.TileHeader(ct.KIND_CORE, 32, 1, 2, 7, 0)
    assert ct.decode_core_directions(hc, cc).shape == (0, 3)

    # Unsupported version rejected
    bad_ver = bytearray(empty_core)
    struct.pack_into("<H", bad_ver, 4, 2)
    with pytest.raises(ValueError, match="version"):
        ct.read_header(bytes(bad_ver))

    empty_detail = ct.encode_detail_tile(
        layer=1,
        order=2,
        pix=7,
        id_hi=np.array([], dtype=np.uint32),
        id_lo=np.array([], dtype=np.uint32),
        pm_ra_f16=np.array([], dtype=np.uint16),
        pm_dec_f16=np.array([], dtype=np.uint16),
        rv_f16=np.array([], dtype=np.uint16),
        lower_code=np.array([], dtype=np.uint8),
        upper_code=np.array([], dtype=np.uint8),
    )
    hd, _ = ct.decode_detail_tile(empty_detail)
    assert hd == ct.TileHeader(ct.KIND_DETAIL, 0, 1, 2, 7, 0)

    empty_u16 = np.array([], dtype=np.uint16)
    empty_i16 = np.array([], dtype=np.int16)
    empty_astro = ct.encode_astro_tile(
        layer=1,
        order=2,
        pix=7,
        teff=empty_u16,
        teff_lower=empty_u16,
        teff_upper=empty_u16,
        log_lum=empty_i16,
        log_lum_lower=empty_i16,
        log_lum_upper=empty_i16,
        a0=empty_u16,
        a0_lower=empty_u16,
        a0_upper=empty_u16,
        ag=empty_u16,
        ruwe_f16=empty_u16,
        parallax_error_f16=empty_u16,
    )
    ha, _ = ct.decode_astro_tile(empty_astro)
    assert ha == ct.TileHeader(ct.KIND_ASTRO, 0, 1, 2, 7, 0)

    # Kind mismatches and column length mismatches raise ValueError
    with pytest.raises(ValueError, match="detail"):
        ct.decode_detail_tile(empty_core)
    with pytest.raises(ValueError, match="astro"):
        ct.decode_astro_tile(empty_detail)
    with pytest.raises(ValueError, match="length"):
        ct.decode_core_tile(empty_core + b"\x00")
    with pytest.raises(ValueError, match="astro columns"):
        ct.encode_astro_tile(
            layer=1,
            order=2,
            pix=7,
            teff=np.array([5000], dtype=np.uint16),
            teff_lower=empty_u16,
            teff_upper=empty_u16,
            log_lum=empty_i16,
            log_lum_lower=empty_i16,
            log_lum_upper=empty_i16,
            a0=empty_u16,
            a0_lower=empty_u16,
            a0_upper=empty_u16,
            ag=empty_u16,
            ruwe_f16=empty_u16,
            parallax_error_f16=empty_u16,
        )


# =============================================================================================
# 5. Bright-star Hipparcos + Gaia DR3 merge characterization
# =============================================================================================


def test_merge_bright_stars_all_decision_branches_and_reasons() -> None:
    def v(ra: float, dec: float) -> np.ndarray:
        return ct.radec_to_vec([ra], [dec])[0]

    arcsec = 1.0 / 3600.0
    # Cases:
    # HIP 10 (bn=1001, bn_mag=NaN): best neighbour has no G mag -> use_hipparcos, drop 1001 ("replaced by")
    # HIP 20 (bn=1002, bn_mag=12.4 >= gaia_mag_limit=12.0): outside subset -> use_hipparcos; 1002 is within 2" so dropped
    # HIP 30 (bn=1003, bn_mag=4.5): well handled by Gaia -> neither used nor logged
    # HIP 40 (bn=0): unsaturated Gaia 1004 at 3.5" with |G - Hp| = 0.6 < 1.0 -> keep_gaia
    # HIP 50 (bn=0): unsaturated Gaia 1005 at 3.5" with |G - Hp| = 1.2 >= 1.0 -> use_hipparcos ("no Gaia DR3 best neighbour")
    #   and since |G - Hp| = 1.2 < 1.5 within duplicate_radius, 1005 is dropped ("duplicate of Hipparcos entry")
    # Gaia 1006: orphan with G=1.8 < gaia_poor_brighter_than=3.0 far from all HIP -> dropped with hip=None
    gaia_id = np.array([1001, 1002, 1003, 1004, 1005, 1006], dtype=np.int64)
    gaia_vec = np.stack(
        [
            v(10.0, 0.0),
            v(20.0 + 1.0 * arcsec, 0.0),
            v(30.0, 0.0),
            v(40.0 + 3.5 * arcsec, 0.0),
            v(50.0 + 3.5 * arcsec, 0.0),
            v(60.0, 0.0),
        ]
    )
    gaia_mag = np.array([4.0, 12.4, 4.5, 5.6, 6.2, 1.8])

    hip = np.array([10, 20, 30, 40, 50], dtype=np.int64)
    hip_vec = np.stack([v(10.0, 0.0), v(20.0, 0.0), v(30.0, 0.0), v(40.0, 0.0), v(50.0, 0.0)])
    hip_mag = np.array([4.1, 11.8, 4.5, 5.0, 5.0])
    best = np.array([1001, 1002, 1003, 0, 0], dtype=np.int64)
    best_mag = np.array([np.nan, 12.4, 4.5, np.nan, np.nan])

    use_hip, drop_gaia, decisions = ct.merge_bright_stars(
        gaia_source_id=gaia_id,
        gaia_vec=gaia_vec,
        gaia_mag=gaia_mag,
        hip=hip,
        hip_vec_gaia_epoch=hip_vec,
        hip_mag=hip_mag,
        hip_best_neighbour=best,
        hip_best_neighbour_mag=best_mag,
        gaia_poor_brighter_than=3.0,
        gaia_mag_limit=12.0,
        duplicate_radius_arcsec=10.0,
        duplicate_mag_limit=7.5,
    )

    assert use_hip.tolist() == [True, True, False, False, True]
    assert drop_gaia.tolist() == [True, True, False, False, True, True]

    by_key = {(d.action, d.hip, d.gaia_source_id): d.reason for d in decisions}
    assert by_key[("use_hipparcos", 10, 1001)] == "Gaia best neighbour has no G magnitude"
    assert by_key[("drop_gaia", 10, 1001)].startswith("replaced by Hipparcos entry:")
    assert "outside the G < 12 subset" in by_key[("use_hipparcos", 20, 1002)]
    assert by_key[("drop_gaia", 20, 1002)].startswith("replaced by Hipparcos entry:")
    assert by_key[("keep_gaia", 40, 1004)].startswith("no Gaia best neighbour, but a Gaia source agrees:")
    assert by_key[("use_hipparcos", 50, None)] == "no Gaia DR3 best neighbour"
    assert by_key[("drop_gaia", 50, 1005)].startswith("duplicate of Hipparcos entry:")
    assert "no Hipparcos star within 10 arcsec claimed it" in by_key[("drop_gaia", None, 1006)]
