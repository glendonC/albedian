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
# Shared codec fixture for the web decoder
# =============================================================================================


def build_fixture() -> tuple[bytes, bytes, dict]:
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
            }
            for r in rows
        ],
    }
    return core, detail, expected


def test_shared_codec_fixture_is_current():
    core, detail, expected = build_fixture()
    paths = {
        "core.bin": core,
        "detail.bin": detail,
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
    on_disk = {p.relative_to(RELEASE_DIR).as_posix() for p in RELEASE_DIR.rglob("*") if p.is_file()} - {"SHA256SUMS"}
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
