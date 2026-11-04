"""Catalog star tiles: HEALPix geometry, binary codec, bright-star merge rules, baked release.

The codec fixture under tests/fixtures/catalog_star_tiles/ is shared with the web decoder
tests (apps/web/src/spatial/catalogStars/__tests__). Regenerate it after a deliberate format
change with:

    ALBEDIAN_REGENERATE_FIXTURES=1 uv run pytest tests/integration/test_catalog_star_tiles.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.contracts.provenance import AssetProvenanceRecord, dumps_record
from services.science import catalog_tiles as ct

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "catalog_star_tiles"
RELEASE_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"


def _load_bake_script():
    spec = importlib.util.spec_from_file_location("bake_catalog_star_tiles", ROOT / "scripts" / "bake_catalog_star_tiles.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Gaia DR3 rows (gaiadr3.gaia_source joined with external.gaiaedr3_distance) used as codec
# inputs. The Gaia HEALPix index lives in source_id >> 35, which checks our HEALPix code.
GAIA_ROWS = [
    # source_id, ra, dec, G, BP-RP, pmra, pmdec, RV, r_med_photogeo, r_lo_photogeo, r_hi_photogeo
    (33264522507136, 44.97466211975327, 0.4734652233431856, 9.887423, 1.2269411, 10.42019956762403, -24.870877096086232, 17.182444, 696.59296, 689.55566, 703.09735),
    (72915660589952, 44.702647733711565, 0.40269819741381024, 9.842077, 1.2580805, 13.778210381602644, -2.9411628686063813, 44.03256, 667.9206, 656.99884, 678.82135),
]

# The same two sources' astrophysics as the Gaia archive returned them on 2026-11-04: GSP-Phot
# temperature and extinction and the quality columns from gaiadr3.gaia_source, FLAME luminosity
# from gaiadr3.astrophysical_parameters. The first source has no GSP-Phot or FLAME result in
# DR3, so those values stay missing.
NAN = math.nan
GAIA_ASTRO_ROWS = {
    # teff, teff_lower, teff_upper, lum, lum_lower, lum_upper, a0, a0_lower, a0_upper, ag, ruwe, parallax_error
    33264522507136: (NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, 1.0022594, 0.015528413),
    72915660589952: (4631.998, 4626.547, 4636.2188, 49.509373, 47.806786, 51.30432, 0.1159, 0.1089, 0.1213, 0.09, 1.0765792, 0.027890556),
}


# =============================================================================================
# HEALPix
# =============================================================================================


def test_healpix_matches_gaia_source_id_encoding():
    sid = np.array([r[0] for r in GAIA_ROWS], dtype=np.int64)
    ra = np.array([r[1] for r in GAIA_ROWS])
    dec = np.array([r[2] for r in GAIA_ROWS])
    for order in (0, 3, 8, 12):
        assert np.array_equal(ct.radec_to_nest(order, ra, dec), ct.gaia_source_id_healpix(sid, order))


@settings(max_examples=300, deadline=None)
@given(st.floats(0, 360, exclude_max=True), st.floats(-90, 90), st.integers(0, 12))
def test_healpix_face_coordinates_round_trip(ra, dec, order):
    face, x, y = ct.radec_to_face_xy(np.array([ra]), np.array([dec]))
    assert 0 <= face[0] < 12 and 0 <= x[0] <= 1 and 0 <= y[0] <= 1
    pix, u, v = ct.face_xy_to_nest(order, face, x, y)
    f2, ix, iy = ct.nest_to_face_ixiy(order, pix)
    nside = 1 << order
    vec = ct.face_xy_to_vec(f2, (ix + u) / nside, (iy + v) / nside)
    err = ct.angular_separation_arcsec(vec, ct.radec_to_vec([ra], [dec]))
    assert float(err[0]) < 1e-6
    assert 0 <= pix[0] < 12 * 4**order


def test_healpix_poles_and_seams_stay_on_valid_faces():
    ra = np.array([0.0, 90.0, 180.0, 270.0, 359.9999999, 45.0, 45.0])
    dec = np.array([90.0, -90.0, 41.8103149, -41.8103149, 0.0, 89.9999999, -89.9999999])
    face, x, y = ct.radec_to_face_xy(ra, dec)
    vec = ct.face_xy_to_vec(face, x, y)
    assert ct.angular_separation_arcsec(vec, ct.radec_to_vec(ra, dec)).max() < 1e-6


# =============================================================================================
# Scalar codecs
# =============================================================================================


def test_distance_code_relative_precision_and_missing():
    d = np.array([1.3, 8.6, 263.0, 5400.0, 31000.0, np.nan, -1.0])
    code = ct.encode_distance(d)
    back = ct.decode_distance(code)
    ok = np.isfinite(d) & (d > 0)
    assert np.all(np.abs(back[ok] / d[ok] - 1) < 1.2e-4)
    assert np.all(code[~ok] == ct.DIST_MISSING) and np.all(np.isnan(back[~ok]))


@settings(max_examples=200, deadline=None)
@given(
    st.floats(0.5, 50_000),
    st.floats(1e-5, 0.95),
    st.floats(1e-5, 90.0),
)
def test_distance_bounds_round_trip(median, frac_lo, frac_hi):
    lo_code, hi_code = ct.encode_distance_bounds([median], [median * (1 - frac_lo)], [median * (1 + frac_hi)])
    lo, hi = ct.decode_distance_bounds([median], lo_code, hi_code)
    got_lo = (median - lo[0]) / median
    got_hi = (hi[0] - median) / median
    # one code step is 0.0197 dex below the median and 0.0276 dex above it
    assert abs(math.log10(got_lo / frac_lo)) <= 0.0197 / 2 + 1e-9
    assert abs(math.log10(got_hi / frac_hi)) <= 0.0276 / 2 + 1e-9


def test_float16_marks_missing_values_with_nan():
    bits = ct.encode_f16([10.42, -802.8, np.nan, 0.0])
    back = ct.decode_f16(bits)
    assert abs(back[0] - 10.42) < 0.01 and abs(back[1] + 802.8) < 0.5
    assert np.isnan(back[2]) and back[3] == 0.0


def test_color_and_magnitude_codes():
    assert np.allclose(ct.decode_mag(ct.encode_mag([-1.46, 0.03, 11.999])), [-1.46, 0.03, 11.999])
    color = ct.decode_color(ct.encode_color([1.2269411, -0.03, np.nan]))
    assert abs(color[0] - 1.227) < 1e-9 and abs(color[1] + 0.03) < 1e-9 and np.isnan(color[2])


@settings(max_examples=200, deadline=None)
@given(
    st.floats(2_000, 65_000),
    st.floats(1e-4, 1e7),
    st.floats(0, 60),
)
def test_astro_codes_round_trip_within_their_steps(teff, lum, extinction):
    assert abs(ct.decode_teff(ct.encode_teff([teff]))[0] - teff) <= 0.5
    # one code step is 0.001 dex
    assert abs(math.log10(ct.decode_luminosity(ct.encode_luminosity([lum]))[0] / lum)) <= 0.0005 + 1e-12
    assert abs(ct.decode_extinction(ct.encode_extinction([extinction]))[0] - extinction) <= 0.0005 + 1e-12


def test_astro_codes_keep_missing_values_missing():
    nan = np.array([np.nan])
    assert ct.encode_teff(nan)[0] == ct.TEFF_MISSING and np.isnan(ct.decode_teff(ct.encode_teff(nan))[0])
    assert ct.encode_luminosity(nan)[0] == ct.LOG_LUM_MISSING
    assert np.isnan(ct.decode_luminosity(ct.encode_luminosity(nan))[0])
    assert ct.encode_extinction(nan)[0] == ct.EXTINCTION_MISSING
    assert np.isnan(ct.decode_extinction(ct.encode_extinction(nan))[0])
    # Zero extinction is a value, not a gap
    assert ct.decode_extinction(ct.encode_extinction([0.0]))[0] == 0.0


@pytest.mark.parametrize(
    "encode,bad",
    [
        ("encode_teff", 70_000.0),
        ("encode_teff", 0.2),
        ("encode_luminosity", 0.0),
        ("encode_luminosity", -3.0),
        ("encode_luminosity", 1e40),
        ("encode_extinction", -0.01),
        ("encode_extinction", 70.0),
    ],
)
def test_astro_codes_refuse_values_they_cannot_hold(encode, bad):
    # A value outside the code range fails the bake; it is never clipped or dropped to missing
    with pytest.raises(ValueError):
        getattr(ct, encode)([bad])


# =============================================================================================
# Tile codec
# =============================================================================================


def _random_tile(rng: np.random.Generator, order: int, bits: int, n: int):
    face = np.full(n, int(rng.integers(0, 12)))
    nside = 1 << order
    ix, iy = int(rng.integers(0, nside)), int(rng.integers(0, nside))
    x = (ix + rng.random(n)) / nside
    y = (iy + rng.random(n)) / nside
    pix, u, v = ct.face_xy_to_nest(order, face, x, y)
    assert len(set(pix.tolist())) == 1
    return int(pix[0]), u, v, ct.face_xy_to_vec(face, x, y)


@pytest.mark.parametrize("order,bits,limit_arcsec", [(0, 32, 0.001), (2, 32, 0.001), (3, 16, 0.5)])
def test_core_and_detail_tiles_round_trip(order, bits, limit_arcsec):
    rng = np.random.default_rng(order)
    n = 257
    pix, u, v, true_vec = _random_tile(rng, order, bits, n)
    mag = np.sort(rng.uniform(-1.5, 12.0, n))
    ids = rng.integers(1, 2**62, n, dtype=np.int64)
    core = ct.encode_core_tile(
        layer=3,
        order=order,
        pix=pix,
        position_bits=bits,
        pos_u=ct.quantize_unit(u, bits),
        pos_v=ct.quantize_unit(v, bits),
        mag_code=ct.encode_mag(mag),
        color_code=ct.encode_color(rng.uniform(-0.4, 4.0, n)),
        distance_code=ct.encode_distance(rng.uniform(1, 9000, n)),
        flags=np.full(n, ct.DIST_KIND_BJ_PHOTOGEO, dtype=np.uint8),
    )
    h, cols = ct.decode_core_tile(core)
    assert (h.kind, h.layer, h.order, h.pix, h.count, h.position_bits) == (ct.KIND_CORE, 3, order, pix, n, bits)
    err = ct.angular_separation_arcsec(ct.decode_core_directions(h, cols), true_vec)
    assert err.max() < limit_arcsec
    assert np.allclose(ct.decode_mag(cols["mag"]), mag, atol=5e-4)

    detail = ct.encode_detail_tile(
        layer=3,
        order=order,
        pix=pix,
        id_hi=(ids >> 32).astype(np.uint32),
        id_lo=(ids & 0xFFFFFFFF).astype(np.uint32),
        pm_ra_f16=ct.encode_f16(rng.normal(0, 50, n)),
        pm_dec_f16=ct.encode_f16(rng.normal(0, 50, n)),
        rv_f16=ct.encode_f16(np.full(n, np.nan)),
        lower_code=np.zeros(n, dtype=np.uint8),
        upper_code=np.zeros(n, dtype=np.uint8),
    )
    _, dcols = ct.decode_detail_tile(detail)
    back = (dcols["idHi"].astype(np.int64) << 32) | dcols["idLo"].astype(np.int64)
    assert np.array_equal(back, ids)
    with pytest.raises(ValueError):
        ct.decode_core_tile(detail)


def test_tile_header_rejects_foreign_bytes():
    with pytest.raises(ValueError):
        ct.read_header(b"PNG\x00" + bytes(28))


def test_astro_tiles_round_trip_and_keep_header_version_1():
    rng = np.random.default_rng(7)
    n = 129
    teff = rng.uniform(3000, 30000, n)
    lum = 10 ** rng.uniform(-2, 5, n)
    a0 = rng.uniform(0, 4, n)
    ruwe = rng.uniform(0.8, 3, n)
    plx_err = rng.uniform(0.01, 0.2, n)
    teff[::5] = np.nan  # stars without GSP-Phot
    lum[::3] = np.nan  # stars without FLAME
    blob = ct.encode_astro_tile(
        layer=4,
        order=3,
        pix=511,
        teff=ct.encode_teff(teff),
        teff_lower=ct.encode_teff(teff - 50),
        teff_upper=ct.encode_teff(teff + 60),
        log_lum=ct.encode_luminosity(lum),
        log_lum_lower=ct.encode_luminosity(lum * 0.95),
        log_lum_upper=ct.encode_luminosity(lum * 1.05),
        a0=ct.encode_extinction(a0),
        a0_lower=ct.encode_extinction(a0 * 0.9),
        a0_upper=ct.encode_extinction(a0 * 1.1),
        ag=ct.encode_extinction(a0 * 0.8),
        ruwe_f16=ct.encode_f16(ruwe),
        parallax_error_f16=ct.encode_f16(plx_err),
    )
    assert len(blob) == ct.TILE_HEADER_BYTES + n * ct.ASTRO_STRIDE and ct.ASTRO_STRIDE == 24
    h, cols = ct.decode_astro_tile(blob)
    assert (h.kind, h.layer, h.order, h.pix, h.count, h.position_bits) == (ct.KIND_ASTRO, 4, 3, 511, n, 0)
    assert int.from_bytes(blob[4:6], "little") == ct.TILE_FORMAT_VERSION == 1
    back = ct.decode_teff(cols["teff"])
    assert np.array_equal(np.isnan(back), np.isnan(teff))
    assert np.nanmax(np.abs(back - teff)) <= 0.5
    assert np.array_equal(np.isnan(ct.decode_luminosity(cols["logLum"])), np.isnan(lum))
    assert np.allclose(ct.decode_extinction(cols["a0"]), a0, atol=5e-4)
    assert np.allclose(ct.decode_f16(cols["ruwe"]), ruwe, rtol=1e-3)
    with pytest.raises(ValueError):
        ct.decode_core_tile(blob)
    with pytest.raises(ValueError):
        ct.decode_astro_tile(blob[:-2])


# =============================================================================================
# Bright-star merge
# =============================================================================================


def test_bright_star_merge_rules():
    def vec(ra, dec):
        return ct.radec_to_vec([ra], [dec])[0]

    arcsec = 1 / 3600
    # Gaia rows (synthetic positions): 0 saturated counterpart of HIP 1, 1 artefact 4" from HIP 1,
    # 2 genuine faint companion 6" from HIP 1, 3 well-measured counterpart of HIP 2,
    # 4 Gaia source matching HIP 3 that the crossmatch missed, 5 saturated orphan,
    # 6 companion 2.5" from HIP 5 that the crossmatch picked, 7 saturated true counterpart of
    # HIP 5 (the beta Leporis pattern), 8 very red star reading 2.3 mag brighter in G than its
    # Hipparcos Hp (the R Hydrae pattern), 9 Gaia source 1.3" from HIP 7 reading 1.8 mag fainter
    gaia_id = np.array([101, 102, 103, 104, 105, 106, 107, 108, 109, 110], dtype=np.int64)
    gaia_vec = np.stack(
        [
            vec(10, 10),
            vec(10 + 4 * arcsec, 10),
            vec(10, 10 + 6 * arcsec),
            vec(50, -20),
            vec(80, 5),
            vec(200, 60),
            vec(120, 30 + 2.5 * arcsec),
            vec(120, 30),
            vec(150, -25),
            vec(84, -5.4 + 1.3 * arcsec),
        ]
    )
    gaia_mag = np.array([1.2, 2.5, 7.4, 4.1, 5.2, 2.0, 7.82, 2.63, 3.15, 6.63])
    hip = np.array([1, 2, 3, 4, 5, 6, 7], dtype=np.int64)
    hip_vec = np.stack(
        [vec(10, 10), vec(50, -20), vec(80, 5), vec(300, -40), vec(120, 30), vec(150, -25), vec(84, -5.4)]
    )
    hip_mag = np.array([1.3, 4.2, 5.4, 3.3, 2.97, 5.48, 4.86])
    best = np.array([101, 104, 0, 0, 107, 0, 0], dtype=np.int64)
    best_mag = np.array([1.2, 4.1, np.nan, np.nan, 7.82, np.nan, np.nan])

    use_hip, drop, decisions = ct.merge_bright_stars(
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
    assert use_hip.tolist() == [True, False, False, True, True, False, True]
    assert drop.tolist() == [True, True, False, False, False, True, False, True, False, True]
    actions = {(d.action, d.hip, d.gaia_source_id) for d in decisions}
    assert ("keep_gaia", 3, 105) in actions
    assert ("drop_gaia", None, 106) in actions
    assert ("drop_gaia", 5, 108) in actions and ("use_hipparcos", 5, 107) in actions
    assert ("keep_gaia", 6, 109) in actions
    assert ("drop_gaia", 7, 110) in actions


# =============================================================================================
# IAU names parser
# =============================================================================================

IAU_SAMPLE = """# "IAU Catalog of Star Names (IAU-CSN)"
# Last updated 2022-04-04 (see notes on latest edits at end of file)
#Name/ASCII       Name/Diacritics   Designation  ID    ID    Con #    WDS_J       mag  bnd  HIP     HD  RA(J2000)  Dec(J2000) Date       Notes
Absolutno         Absolutno         XO-5         _     _     Lyn _    _          11.95  G      _      _ 116.716506  39.094572 2019-12-17
Deneb Algedi      Deneb Algedi      HR 8322      del   δ     Cap Aa   21470-1608  2.85  V 107556 207098 326.760184 -16.127287 2017-02-01
Sirius            Sirius            HR 2491      alf   α     CMa A    06451-1643 -1.45  V  32349  48915 101.287155 -16.716116 2016-06-30
"""


def test_iau_csn_parser_reads_fixed_columns():
    bake = _load_bake_script()
    names, updated = bake.parse_iau_csn(IAU_SAMPLE)
    assert updated == "2022-04-04"
    assert [(n.name, n.constellation, n.component, n.hip) for n in names] == [
        ("Absolutno", "Lyn", "", None),
        ("Deneb Algedi", "Cap", "Aa", 107556),
        ("Sirius", "CMa", "A", 32349),
    ]
    assert names[1].bayer == "del" and names[2].designation == "HR 2491"


# =============================================================================================
# DR3 astrophysical parameters fetch (GSP-Phot and FLAME)
# =============================================================================================


def _select_list(query: str) -> list[str]:
    return query.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(", ")


def test_astro_chunk_query_returns_exactly_the_base_chunk_rows():
    bake = _load_bake_script()
    cfg = bake.RELEASES["gaia-dr3"]
    for order, pix in ((2, 5), (3, 17), (4, 300)):
        base = bake.gaia_chunk_query(cfg, order, pix)
        astro = bake.gaia_astro_chunk_query(cfg, order, pix)
        # Same WHERE clause on the same table, and a left join on a table with at most one row per
        # source, so every chunk returns the base chunk's sources and no others
        assert astro.split(" WHERE ", 1)[1] == base.split(" WHERE ", 1)[1]
        assert f"FROM {cfg.gaia_table} AS g LEFT OUTER JOIN {cfg.astro_table} AS ap ON ap.source_id = g.source_id" in astro


def test_astro_chunk_query_takes_the_gsp_phot_flame_and_quality_columns():
    bake = _load_bake_script()
    selected = _select_list(bake.gaia_astro_chunk_query(bake.RELEASES["gaia-dr3"], 3, 17))
    assert selected[0] == "g.source_id"
    assert len(selected) == len(set(selected))
    gsp_phot = ("teff", "logg", "mh", "distance", "azero", "ag", "ebpminrp")
    for name in gsp_phot:
        for suffix in ("", "_lower", "_upper"):
            assert f"g.{name}_gspphot{suffix}" in selected
    for name in ("libname_gspphot", "parallax", "parallax_error", "ruwe", "phot_variable_flag", "non_single_star"):
        assert f"g.{name}" in selected
    for name in ("lum", "radius", "mass", "age"):
        for suffix in ("", "_lower", "_upper"):
            assert f"ap.{name}_flame{suffix}" in selected
    assert "ap.evolstage_flame" in selected and "ap.flags_flame" in selected
    # The CSV header the parser expects is the select list without table prefixes
    assert [c.split(".", 1)[1] for c in selected] == ["source_id", *bake.GAIA_ASTRO_COLUMNS, *bake.FLAME_COLUMNS]


def test_astro_chunk_parser_keeps_missing_values_missing():
    bake = _load_bake_script()
    header = ["source_id", *bake.GAIA_ASTRO_COLUMNS, *bake.FLAME_COLUMNS]
    full = {c: "1.5" for c in header}
    full.update(source_id="33264522507136", libname_gspphot="MARCS", phot_variable_flag="NOT_AVAILABLE", flags_flame="00")
    empty = {c: "" for c in header}
    empty["source_id"] = "72915660589952"
    text = "\n".join([",".join(header), ",".join(full[c] for c in header), ",".join(empty[c] for c in header)]) + "\n"
    parsed = bake.parse_gaia_astro_chunk(text)
    assert parsed["source_id"].tolist() == [33264522507136, 72915660589952]
    assert parsed["teff_gspphot"][0] == 1.5 and np.isnan(parsed["teff_gspphot"][1])
    assert np.isnan(parsed["lum_flame_upper"][1]) and np.isnan(parsed["non_single_star"][1])
    assert parsed["libname_gspphot"].tolist() == ["MARCS", ""]
    assert parsed["flags_flame"].tolist() == ["00", ""]
    with pytest.raises(ValueError):
        bake.parse_gaia_astro_chunk("source_id,teff_gspphot\n1,2\n")


def test_astro_fetch_keeps_going_past_a_failing_chunk(tmp_path, monkeypatch):
    """When the archive drops one chunk, the others still reach the cache and a rerun resumes."""
    bake = _load_bake_script()
    cfg = bake.RELEASES["gaia-dr3"]
    header = ",".join(["source_id", *bake.GAIA_ASTRO_COLUMNS, *bake.FLAME_COLUMNS])
    good = f"{header}\n33264522507136" + "," * (header.count(",")) + "\n"

    def fake_tap(client, url, query):
        if "BETWEEN 0 AND" in query:
            raise bake.TapError("archive reset the connection")
        return good

    monkeypatch.setattr(bake, "tap_csv", fake_tap)
    chunks = [(2, 0, 1), (2, 1, 1)]
    merged = bake.fetch_gaia_astro(cfg, None, tmp_path, False, chunks, workers=2, keep_going=True)
    assert merged is None
    assert (tmp_path / "gaia-ap" / "o2-p1.csv.gz").exists()
    assert not (tmp_path / "gaia-ap" / "o2-p0.csv.gz").exists()
    with pytest.raises(bake.TapError):
        bake.fetch_gaia_astro(cfg, None, tmp_path, False, chunks, workers=2)


def test_archive_requests_identify_only_as_the_research_prototype():
    bake = _load_bake_script()
    assert bake.USER_AGENT == "Albedian/0.1 (research prototype)"


# =============================================================================================
# Shared codec fixture for the web decoder
# =============================================================================================


def _or_none(value: float) -> float | None:
    return None if math.isnan(value) else value


def build_fixture() -> tuple[bytes, bytes, bytes, dict]:
    rows = GAIA_ROWS
    ra = np.array([r[1] for r in rows])
    dec = np.array([r[2] for r in rows])
    face, x, y = ct.radec_to_face_xy(ra, dec)
    order = 3
    pix, u, v = ct.face_xy_to_nest(order, face, x, y)
    assert len(set(pix.tolist())) == 1
    mag = np.array([r[3] for r in rows])
    sort = np.argsort(mag, kind="stable")
    rows = [rows[i] for i in sort]
    u, v = u[sort], v[sort]
    sid = np.array([r[0] for r in rows], dtype=np.int64)
    med = np.array([r[8] for r in rows])
    core = ct.encode_core_tile(
        layer=2,
        order=order,
        pix=int(pix[0]),
        position_bits=16,
        pos_u=ct.quantize_unit(u, 16),
        pos_v=ct.quantize_unit(v, 16),
        mag_code=ct.encode_mag([r[3] for r in rows]),
        color_code=ct.encode_color([r[4] for r in rows]),
        distance_code=ct.encode_distance(med),
        flags=np.array([ct.DIST_KIND_BJ_PHOTOGEO, ct.DIST_KIND_BJ_PHOTOGEO | ct.FLAG_NAMED], dtype=np.uint8),
    )
    lo_code, hi_code = ct.encode_distance_bounds(med, [r[9] for r in rows], [r[10] for r in rows])
    detail = ct.encode_detail_tile(
        layer=2,
        order=order,
        pix=int(pix[0]),
        id_hi=(sid >> 32).astype(np.uint32),
        id_lo=(sid & 0xFFFFFFFF).astype(np.uint32),
        pm_ra_f16=ct.encode_f16([r[5] for r in rows]),
        pm_dec_f16=ct.encode_f16([r[6] for r in rows]),
        rv_f16=ct.encode_f16([r[7] for r in rows]),
        lower_code=lo_code,
        upper_code=hi_code,
    )
    astro_rows = [GAIA_ASTRO_ROWS[r[0]] for r in rows]

    def column(k: int) -> np.ndarray:
        return np.array([a[k] for a in astro_rows], dtype=np.float64)

    astro = ct.encode_astro_tile(
        layer=2,
        order=order,
        pix=int(pix[0]),
        teff=ct.encode_teff(column(0)),
        teff_lower=ct.encode_teff(column(1)),
        teff_upper=ct.encode_teff(column(2)),
        log_lum=ct.encode_luminosity(column(3)),
        log_lum_lower=ct.encode_luminosity(column(4)),
        log_lum_upper=ct.encode_luminosity(column(5)),
        a0=ct.encode_extinction(column(6)),
        a0_lower=ct.encode_extinction(column(7)),
        a0_upper=ct.encode_extinction(column(8)),
        ag=ct.encode_extinction(column(9)),
        ruwe_f16=ct.encode_f16(column(10)),
        parallax_error_f16=ct.encode_f16(column(11)),
    )
    astro_keys = (
        "teffK",
        "teffLowerK",
        "teffUpperK",
        "luminositySolar",
        "luminosityLowerSolar",
        "luminosityUpperSolar",
        "a0Mag",
        "a0LowerMag",
        "a0UpperMag",
        "agMag",
        "ruwe",
        "parallaxErrorMas",
    )
    expected = {
        "layer": 2,
        "order": order,
        "pix": int(pix[0]),
        "stars": [
            {
                "catalogId": str(r[0]),
                "raDeg": r[1],
                "decDeg": r[2],
                "magnitude": r[3],
                "colorIndex": r[4],
                "pmRaMasYr": r[5],
                "pmDecMasYr": r[6],
                "radialVelocityKmS": r[7],
                "distanceMedianPc": r[8],
                "distanceLowerPc": r[9],
                "distanceUpperPc": r[10],
                **{key: _or_none(value) for key, value in zip(astro_keys, a, strict=True)},
            }
            for r, a in zip(rows, astro_rows, strict=True)
        ],
    }
    return core, detail, astro, expected


def test_shared_codec_fixture_is_current():
    core, detail, astro, expected = build_fixture()
    paths = {
        "core.bin": core,
        "detail.bin": detail,
        "astro.bin": astro,
        "expected.json": (json.dumps(expected, indent=2) + "\n").encode(),
    }
    if os.environ.get("ALBEDIAN_REGENERATE_FIXTURES"):
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for name, blob in paths.items():
            (FIXTURE_DIR / name).write_bytes(blob)
    for name, blob in paths.items():
        assert (FIXTURE_DIR / name).read_bytes() == blob, f"{name} is stale; regenerate the fixture"


# =============================================================================================
# Baked release (apps/web/public/catalog/stars/gaia-dr3)
# =============================================================================================

needs_release = pytest.mark.skipif(not (RELEASE_DIR / "manifest.json").exists(), reason="release not baked")


@needs_release
def test_baked_release_checksums_and_counts():
    sums = (RELEASE_DIR / "SHA256SUMS").read_text().splitlines()
    listed = set()
    for line in sums:
        digest, rel = line.split("  ", 1)
        listed.add(rel)
        assert hashlib.sha256((RELEASE_DIR / rel).read_bytes()).hexdigest() == digest, rel
    on_disk = {p.relative_to(RELEASE_DIR).as_posix() for p in RELEASE_DIR.rglob("*") if p.is_file()}
    on_disk -= {"SHA256SUMS", "PROVENANCE.json"}
    assert on_disk == listed

    manifest = json.loads((RELEASE_DIR / "manifest.json").read_text())
    assert manifest["evidenceKind"] == "catalog-derived"
    total = 0
    for layer in manifest["layers"]:
        counts = layer["tileStarCounts"]
        assert len(counts) == 12 * 4 ** layer["order"]
        for pix, n in enumerate(counts):
            core = RELEASE_DIR / f"L{layer['layer']}" / f"{pix}.core.bin"
            assert core.exists() == (n > 0)
            if n:
                h = ct.read_header(core.read_bytes()[: ct.TILE_HEADER_BYTES])
                assert (h.count, h.layer, h.order, h.pix) == (n, layer["layer"], layer["order"], pix)
        total += sum(counts)
    assert total == manifest["counts"]["stars"]


def test_release_checksums_leave_out_the_record_and_the_record_pins_them(tmp_path):
    bake = _load_bake_script()
    cfg = bake.RELEASES["gaia-dr3"]
    (tmp_path / "L0").mkdir()
    (tmp_path / "L0" / "0.core.bin").write_bytes(b"core tile")
    (tmp_path / "manifest.json").write_text("{}\n")
    bake.write_checksums_and_record(cfg, tmp_path, names_updated="2022-04-04")

    sums = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8")
    assert [line.split("  ", 1)[1] for line in sums.splitlines()] == ["L0/0.core.bin", "manifest.json"]
    record = AssetProvenanceRecord.model_validate_json((tmp_path / "PROVENANCE.json").read_text(encoding="utf-8"))
    (file_set,) = record.file_sets
    assert record.files == ()
    assert file_set.checksums == "SHA256SUMS"
    assert file_set.sha256 == hashlib.sha256(sums.encode("utf-8")).hexdigest()
    assert file_set.produced_by.method == "script"
    assert file_set.produced_by.script == "scripts/bake_catalog_star_tiles.py"
    assert [s.id for s in record.sources] == [s["id"] for s in cfg.sources]
    assert set(file_set.sources) == {s["id"] for s in cfg.sources}

    # Writing again over an existing release lists neither the record nor the list itself
    first = {name: (tmp_path / name).read_bytes() for name in ("SHA256SUMS", "PROVENANCE.json")}
    bake.write_checksums_and_record(cfg, tmp_path, names_updated="2022-04-04")
    assert {name: (tmp_path / name).read_bytes() for name in first} == first


def test_release_record_credits_and_licences():
    bake = _load_bake_script()
    record = bake.provenance_record(bake.RELEASES["gaia-dr3"], "0" * 64, names_updated="2022-04-04")
    by_id = {s.id: s for s in record.sources}
    for gaia in ("gaia-dr3", "gaia-dr3-astrophysical-parameters", "gaia-dr3-hipparcos2-best-neighbour"):
        assert by_id[gaia].credit == "ESA/Gaia/DPAC"
    for archive in ("gaia-dr3", "gaia-dr3-astrophysical-parameters", "bailer-jones-2021", "hipparcos-2007", "gaia-dr3-hipparcos2-best-neighbour"):
        assert by_id[archive].license.spdx == "CC-BY-NC-3.0-IGO"
        assert "prior authorisation" in by_id[archive].license.conditions
    assert by_id["hipparcos-2007"].citation == "van Leeuwen, F. 2007, A&A 474, 653"
    assert by_id["iau-csn"].license.spdx.startswith("LicenseRef-")
    assert by_id["iau-csn"].version == "Last updated 2022-04-04"


@needs_release
def test_baked_release_record_is_what_the_bake_writes():
    bake = _load_bake_script()
    manifest = json.loads((RELEASE_DIR / "manifest.json").read_text())
    checksums = hashlib.sha256((RELEASE_DIR / "SHA256SUMS").read_bytes()).hexdigest()
    expected = dumps_record(
        bake.provenance_record(bake.RELEASES["gaia-dr3"], checksums, names_updated=manifest["selection"]["names"]["sourceUpdated"])
    )
    assert (RELEASE_DIR / "PROVENANCE.json").read_text(encoding="utf-8") == expected


@needs_release
def test_baked_release_named_bright_stars_carry_catalog_distances():
    names = json.loads((RELEASE_DIR / "names.json").read_text())["names"]
    by_name = {n["nameAscii"]: n for n in names}
    for star, catalog, lo_pc, hi_pc in (
        ("Sirius", "hipparcos-2007", 2.5, 2.8),
        ("Vega", "hipparcos-2007", 7.5, 7.9),
        ("Polaris", None, 100.0, 160.0),
    ):
        entry = by_name[star]
        if catalog:
            assert entry["catalog"] == catalog
        blob = (RELEASE_DIR / f"L{entry['layer']}" / f"{entry['pix']}.core.bin").read_bytes()
        h, cols = ct.decode_core_tile(blob)
        flags = int(cols["flags"][entry["index"]])
        assert flags & ct.FLAG_NAMED
        distance = float(ct.decode_distance(cols["distance"][entry["index"]:entry["index"] + 1])[0])
        assert lo_pc < distance < hi_pc, (star, distance)
        direction = ct.decode_core_directions(h, cols)[entry["index"]]
        sep = ct.angular_separation_arcsec(direction, ct.radec_to_vec([entry["raDeg"]], [entry["decDeg"]])[0])
        assert float(sep) < 0.5


# Gaia DR3 values for two named stars as the archive returned them on 2026-11-04: GSP-Phot
# temperature with its percentiles, FLAME luminosity with its percentiles, A0, AG, RUWE and the
# parallax standard error. Achird is a Sun-like dwarf, Segin a hot B star.
NAMED_GAIA_ASTRO = {
    "Achird": (5726.01, 5722.719, 5730.129, 1.2662435, 1.2604617, 1.2720298, 0.0, 0.0, 3.1213033, 0.1663396),
    "Segin": (14805.9375, 14742.155, 14875.629, 3058.6343, 2868.7202, 3262.5151, 0.1224, 0.1215, 2.4672916, 0.24873139),
}
# Independent archive counts over every gaia_source row with G < 12 (2026-11-04)
ARCHIVE_G12_COUNTS = {"withTemperature": 1812696, "withLuminosity": 1780875, "withRuwe": 3062317, "withParallaxError": 3062317}


def _astro_at(entry: dict) -> dict[str, float]:
    _, cols = ct.decode_astro_tile((RELEASE_DIR / f"L{entry['layer']}" / f"{entry['pix']}.astro.bin").read_bytes())
    i = slice(entry["index"], entry["index"] + 1)
    return {
        "teff": float(ct.decode_teff(cols["teff"][i])[0]),
        "teffLower": float(ct.decode_teff(cols["teffLower"][i])[0]),
        "teffUpper": float(ct.decode_teff(cols["teffUpper"][i])[0]),
        "lum": float(ct.decode_luminosity(cols["logLum"][i])[0]),
        "lumLower": float(ct.decode_luminosity(cols["logLumLower"][i])[0]),
        "lumUpper": float(ct.decode_luminosity(cols["logLumUpper"][i])[0]),
        "a0": float(ct.decode_extinction(cols["a0"][i])[0]),
        "ag": float(ct.decode_extinction(cols["ag"][i])[0]),
        "ruwe": float(ct.decode_f16(cols["ruwe"][i])[0]),
        "parallaxError": float(ct.decode_f16(cols["parallaxError"][i])[0]),
    }


@needs_release
def test_baked_release_astro_tiles_align_with_core_tiles():
    manifest = json.loads((RELEASE_DIR / "manifest.json").read_text())
    assert manifest["formatVersion"] == 2
    assert manifest["tiling"]["kinds"] == ["core", "detail", "astro"]
    for layer in manifest["layers"]:
        total = 0
        for pix, n in enumerate(layer["tileStarCounts"]):
            path = RELEASE_DIR / f"L{layer['layer']}" / f"{pix}.astro.bin"
            assert path.exists() == (n > 0)
            if n:
                blob = path.read_bytes()
                h = ct.read_header(blob[: ct.TILE_HEADER_BYTES])
                assert (h.kind, h.count, h.layer, h.order, h.pix) == (ct.KIND_ASTRO, n, layer["layer"], layer["order"], pix)
                total += len(blob)
        assert total == layer["astroBytes"]


@needs_release
def test_baked_release_named_stars_carry_the_archive_astrophysics_or_none():
    names = json.loads((RELEASE_DIR / "names.json").read_text())["names"]
    by_name = {n["nameAscii"]: n for n in names}
    for star, (teff, teff_lo, teff_hi, lum, lum_lo, lum_hi, a0, ag, ruwe, plx_err) in NAMED_GAIA_ASTRO.items():
        entry = by_name[star]
        assert entry["catalog"] == "gaia-dr3"
        got = _astro_at(entry)
        for key, want in (("teff", teff), ("teffLower", teff_lo), ("teffUpper", teff_hi)):
            assert abs(got[key] - want) <= 0.5, (star, key, got[key])
        for key, want in (("lum", lum), ("lumLower", lum_lo), ("lumUpper", lum_hi)):
            assert abs(math.log10(got[key] / want)) <= 0.0005 + 1e-9, (star, key, got[key])
        # Half a 1 mmag step; Segin's AG of 0.1215 mag sits exactly on a half step
        assert abs(got["a0"] - a0) <= 0.0005 + 1e-9 and abs(got["ag"] - ag) <= 0.0005 + 1e-9, star
        assert abs(got["ruwe"] / ruwe - 1) < 5e-4 and abs(got["parallaxError"] / plx_err - 1) < 5e-4, star
    # Hipparcos entries carry no Gaia DR3 astrophysics: missing, never filled
    for star in ("Sirius", "Vega"):
        entry = by_name[star]
        assert entry["catalog"] == "hipparcos-2007"
        assert all(math.isnan(v) for v in _astro_at(entry).values()), star


@needs_release
def test_baked_release_astro_counts_agree_with_the_tiles_and_the_archive():
    manifest = json.loads((RELEASE_DIR / "manifest.json").read_text())
    present = {"withTemperature": 0, "withLuminosity": 0, "withExtinction": 0, "withRuwe": 0, "withParallaxError": 0}
    for path in RELEASE_DIR.glob("L*/*.astro.bin"):
        _, cols = ct.decode_astro_tile(path.read_bytes())
        present["withTemperature"] += int(np.count_nonzero(cols["teff"] != ct.TEFF_MISSING))
        present["withLuminosity"] += int(np.count_nonzero(cols["logLum"] != ct.LOG_LUM_MISSING))
        present["withExtinction"] += int(np.count_nonzero(cols["a0"] != ct.EXTINCTION_MISSING))
        present["withRuwe"] += int(np.count_nonzero(np.isfinite(ct.decode_f16(cols["ruwe"]))))
        present["withParallaxError"] += int(np.count_nonzero(np.isfinite(ct.decode_f16(cols["parallaxError"]))))
    counts = manifest["counts"]
    assert {k: counts[k] for k in present} == present
    # The bright-star merge drops a few Gaia sources, so the tiles hold at most the archive's count
    dropped = counts["gaiaDropped"]
    for key, archive in ARCHIVE_G12_COUNTS.items():
        assert archive - dropped <= counts[key] <= archive, (key, counts[key])
