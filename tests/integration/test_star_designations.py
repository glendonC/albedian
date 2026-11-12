"""Bayer and Flamsteed designations for the gaia-dr3 tile rows (catalog-2a): parsing, linking, the committed files,
their records, and real-data checks.

`scripts/bake_star_designations.py` writes two files that never mix their licences, joined by HIP number when read:
`apps/web/public/catalog/stars/designations/designations.json`, HYG v4.1's values only (astronexus, CC BY-SA 4.0),
whose `bayer`, `flam` and `con` fields give the designations; and
`apps/web/public/catalog/stars/designation-links/links.json`, which tile row each HIP number names, from the Gaia EDR3
to Hipparcos-2 best-neighbour table (ESA/Gaia/DPAC, CC BY-NC 3.0 IGO; ESA's DR3 bulk repository says it is valid for
DR3) and the tiles. Each folder has its own `PROVENANCE.json`. Both downloads are registered in `data/downloads.json`
and kept in `.cache/star-designations/`. A tile row is linked by HIP number only: a Hipparcos row by its own HIP, a
Gaia row as the best neighbour of that HIP. No position is compared.

Values read at their source before they were written here:
- G - V in BP-RP (Riello et al. 2021, A&A 649, A3, arXiv:2012.01916, Table C.2): -0.02704 + 0.01424 c - 0.2156 c^2
  + 0.01426 c^3, valid for -0.5 < c < 5.0, sigma 0.03017.
- The HYG README (v4.1, GitHub and Codeberg): `bf` gives the Flamsteed number first, then the Bayer letter's three
  letter abbreviation, its superscript and the constellation ("21Alp And", "Kap1Scl"); `bayer`, `flam` and `con`
  give the same as distinct values.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from services.contracts.provenance import AssetProvenanceRecord, dumps_record
from services.science import catalog_tiles as ct

ROOT = Path(__file__).resolve().parents[2]
HYG_REL = ".cache/star-designations/hygdata_v41.csv"
BN_REL = ".cache/star-designations/Hipparcos2BestNeighbour.csv.gz"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "designations"
LINKS_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "designation-links"
TILES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_star_designations", ROOT / "scripts" / "bake_star_designations.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("bake_star_designations", module)
    spec.loader.exec_module(module)
    return module


bake = _load_bake()

HYG_HEADER = (
    '"id","hip","hd","hr","gl","bf","proper","ra","dec","dist","pmra","pmdec","rv","mag","absmag","spect","ci","x","y",'
    '"z","vx","vy","vz","rarad","decrad","pmrarad","pmdecrad","bayer","flam","con","comp","comp_primary","base","lum",'
    '"var","var_min","var_max"'
)


def _hyg_line(hyg_id: int, hip: str, bf: str, bayer: str, flam: str, con: str, mag: str = "4.5", hd: str = "", hr: str = "") -> str:
    # Only the fields the bake reads carry values; the rest are blank, in HYG's own column order and quoting
    return (
        f'{hyg_id},{hip},{hd},{hr},"","{bf}","",0.0,0.0,0.0,0.0,0.0,0.0,{mag},0.0,"",0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,'
        f'0.0,0.0,"{bayer}","{flam}",{con},1,1,"",1.0,"",,'
    )


def _hyg(*lines: str) -> str:
    return "\n".join((HYG_HEADER, *lines)) + "\n"


BN_HEADER = "source_id,original_ext_source_id,angular_distance,number_of_neighbours,xm_flag"


def _row(catalog_hipparcos: bool, ident: int, g: float = 4.5, bp_rp: float = 0.5, place: tuple[int, int, int] = (0, 1, 2)):
    return bake.TileRow(catalog_hipparcos, ident, *place, g, bp_rp)


# =============================================================================================
# The designation fields
# =============================================================================================


@pytest.mark.parametrize(
    ("bf", "parts"),
    [
        ("21Alp And", (21, "Alp", None, "And")),
        ("Kap1Scl", (None, "Kap", 1, "Scl")),
        ("65Xi 1Cet", (65, "Xi", 1, "Cet")),
        ("Pi 1Hyi", (None, "Pi", 1, "Hyi")),
        ("29    Psc", (29, None, None, "Psc")),
        ("9Bet Lep", (9, "Bet", None, "Lep")),
        ("Gam2Cae", (None, "Gam", 2, "Cae")),
        ("Eps Oct", (None, "Eps", None, "Oct")),
    ],
)
def test_bf_reads_as_the_readme_describes_it(bf: str, parts: tuple[Any, ...]) -> None:
    assert bake.parse_bf(bf) == parts


@pytest.mark.parametrize("bf", ["", "Alp", "21", "Foo And", "0Alp And", "21Alp and", "21Alp Andr", " 21Alp And", "Alp0And"])
def test_bf_that_the_readme_does_not_describe_is_refused(bf: str) -> None:
    with pytest.raises(ValueError):
        bake.parse_bf(bf)


# One table of every bounded field the bake reads, with a sample at each inside end and one just outside
FIELD_EDGES = [
    # (field, inside samples, outside samples)
    ("flam", ["1", "9", "10", "140"], ["0", "01", "-1", "1.0", " 1", "a"]),
    ("bayer", ["Alp", "Ome", "Mu", "Xi", "Alp-1", "Tau-9"], ["Alpha", "alp", "Foo", "Alp-0", "Alp-10", "Alp1", "Alp-", "p"]),
    ("con", ["And", "CMa", "UMi", "Vul"], ["and", "CMaj", "CM", "C1a", " And"]),
]


@pytest.mark.parametrize(("field", "inside", "outside"), FIELD_EDGES)
def test_each_designation_field_takes_its_pattern_and_refuses_one_step_outside(field: str, inside: list[str], outside: list[str]) -> None:
    good = {"bayer": "Alp", "flam": "21", "con": "And"}
    for value in inside:
        values = {**good, field: value}
        designations, _ = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", values["bayer"], values["flam"], values["con"])))
        assert len(designations) == 1, value
    for value in outside:
        values = {**good, field: value}
        with pytest.raises(ValueError):
            bake.hyg_designations(_hyg(_hyg_line(1, "100", "", values["bayer"], values["flam"], values["con"])))


def test_the_greek_letters_are_the_twenty_four_in_alphabet_order() -> None:
    assert bake.GREEK_LETTERS == (
        "Alp", "Bet", "Gam", "Del", "Eps", "Zet", "Eta", "The", "Iot", "Kap", "Lam", "Mu", "Nu", "Xi", "Omi", "Pi", "Rho",
        "Sig", "Tau", "Ups", "Phi", "Chi", "Psi", "Ome",
    )


def test_a_designation_keeps_each_value_from_its_row() -> None:
    (d,), set_aside = bake.hyg_designations(_hyg(_hyg_line(7, "88", "21Alp And", "Alp", "21", "And", mag="2.07", hd="358", hr="15")))
    assert set_aside == []
    assert (d.hyg_id, d.hip, d.flamsteed, d.bayer, d.constellation, d.hd, d.hr, d.v_mag) == (7, 88, 21, "Alp", "And", 358, 15, 2.07)


def test_a_row_with_neither_letter_nor_number_is_not_a_designation_even_with_a_constellation() -> None:
    designations, set_aside = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "", "", "Psc")))
    assert (designations, set_aside) == ([], [])


def test_bf_alone_is_not_read_as_a_designation() -> None:
    # The distinct fields are the values; bf only checks them, so a bf without them is reported as inconsistent
    designations, set_aside = bake.hyg_designations(_hyg(_hyg_line(1, "100", "21Alp And", "", "", "And")))
    assert designations == [] and set_aside == [(1, 100, "inconsistent-bf")]


def test_a_number_or_letter_without_bf_is_taken_from_the_distinct_fields() -> None:
    designations, set_aside = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "", "37", "Psc"), _hyg_line(2, "101", "", "Pi-1", "", "Ori")))
    assert [(d.flamsteed, d.bayer, d.constellation) for d in designations] == [(37, None, "Psc"), (None, "Pi-1", "Ori")]
    assert set_aside == []


@pytest.mark.parametrize(
    ("bf", "bayer", "flam", "con"),
    [
        ("11    Sgr", "", "1", "Sgr"),  # the one such row in HYG v4.1 (HIP 89153): bf says 11, flam says 1
        ("21Alp And", "Bet", "21", "And"),
        ("21Alp And", "Alp", "21", "Peg"),
        ("21Alp1And", "Alp", "21", "And"),
        ("21Alp And", "Alp-1", "21", "And"),
        ("Alp2And", "Alp-1", "", "And"),
        ("21Alp And", "Alp", "", "And"),
        ("Alp And", "Alp", "21", "And"),
        ("21    And", "Alp", "21", "And"),
        ("21Alp", "Alp", "21", "And"),  # reads as number 21 in a constellation "Alp", so it disagrees with con
    ],
)
def test_a_row_whose_bf_disagrees_with_its_fields_is_set_aside(bf: str, bayer: str, flam: str, con: str) -> None:
    designations, set_aside = bake.hyg_designations(_hyg(_hyg_line(3, "89153", bf, bayer, flam, con)))
    assert designations == [] and set_aside == [(3, 89153, "inconsistent-bf")]


def test_a_designation_without_a_hip_number_is_set_aside() -> None:
    designations, set_aside = bake.hyg_designations(_hyg(_hyg_line(5, "", "Alp Cen", "Alp", "", "Cen")))
    assert designations == [] and set_aside == [(5, None, "no-hip")]


def test_a_hip_number_designated_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="HIP 100"):
        bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "Alp", "", "And"), _hyg_line(2, "100", "", "", "21", "And")))


def test_a_hyg_file_without_the_columns_the_bake_reads_is_refused() -> None:
    with pytest.raises(ValueError, match="header"):
        bake.hyg_designations(_hyg().replace('"flam",', '"flamsteed",'))


# =============================================================================================
# The best-neighbour table
# =============================================================================================


def test_the_best_neighbour_table_is_read_by_hip() -> None:
    text = "\n".join((BN_HEADER, "5188150893900488576,48752,0.016790653,1,8", "35845938787261952,13654,0.42688197,1,16")) + "\n"
    assert bake.best_neighbours(text) == {48752: 5188150893900488576, 13654: 35845938787261952}


@pytest.mark.parametrize(
    ("lines", "match"),
    [
        (("1,48752,0.1,1,8", "2,48752,0.1,1,8"), "HIP 48752"),
        (("7,48752,0.1,1,8", "7,13654,0.1,1,8"), "source 7"),
    ],
)
def test_a_hip_or_a_source_twice_in_the_best_neighbour_table_is_refused(lines: tuple[str, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        bake.best_neighbours("\n".join((BN_HEADER, *lines)) + "\n")


def test_a_best_neighbour_table_with_another_header_is_refused() -> None:
    with pytest.raises(ValueError, match="header"):
        bake.best_neighbours("source_id,hip\n1,2\n")


# =============================================================================================
# V from G and the half-light rule
# =============================================================================================


def test_v_from_g_is_riellos_relation() -> None:
    assert bake.v_from_gaia(5.0, 0.0) == pytest.approx(5.0 + 0.02704, abs=1e-12)
    c = 1.3
    expected = 5.0 - (-0.02704 + 0.01424 * c - 0.2156 * c**2 + 0.01426 * c**3)
    assert bake.v_from_gaia(5.0, c) == pytest.approx(expected, abs=1e-12)


# BP-RP edges from Riello et al.'s stated range, -0.5 < c < 5.0, strict at both ends
@pytest.mark.parametrize(
    ("bp_rp", "inside"),
    [(-0.5, False), (-0.4999, True), (4.9999, True), (5.0, False), (-0.6, False), (5.1, False), (math.nan, False), (math.inf, False)],
)
def test_v_from_g_holds_only_inside_the_relations_range(bp_rp: float, inside: bool) -> None:
    assert (bake.v_from_gaia(5.0, bp_rp) is not None) == inside


def test_the_half_light_bound_is_two_and_a_half_log_two() -> None:
    assert bake.HALF_LIGHT_MAG == 2.5 * math.log10(2.0)
    assert 0.7525 < bake.HALF_LIGHT_MAG < 0.7526


def _gaia_link(gap: float, bp_rp: float = 0.0) -> tuple[list[Any], list[Any]]:
    v_hyg = 5.0
    g = v_hyg + gap + (-0.02704 + 0.01424 * bp_rp - 0.2156 * bp_rp**2 + 0.01426 * bp_rp**3)
    (d,), _ = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "Alp", "", "And", mag=str(v_hyg))))
    return bake.link([d], {100: 9}, {}, {9: _row(False, 9, g=g, bp_rp=bp_rp)})


# The half-light edge: the row's V may be at most 2.5 log10 2 fainter than the star's, inclusive
@pytest.mark.parametrize(
    ("gap", "linked"),
    [(-3.0, True), (0.0, True), (0.7525, True), (0.7527, False), (0.8, False), (6.0, False)],
)
def test_a_gaia_row_takes_the_designation_only_when_it_holds_at_least_half_the_light(gap: float, linked: bool) -> None:
    rows, set_aside = _gaia_link(gap, bp_rp=0.8)
    assert (len(rows) == 1) == linked
    assert set_aside == ([] if linked else [(100, "fainter-than-half-light")])


def test_the_half_light_edge_itself_is_inside_and_one_step_past_it_is_not() -> None:
    # A star at V 0 and a row whose V is the bound exactly, in floating point: the gap is then the bound itself
    g = bake.HALF_LIGHT_MAG - 0.02704
    for _ in range(64):
        v_row = bake.v_from_gaia(g, 0.0)
        if v_row == bake.HALF_LIGHT_MAG:
            break
        g = float(np.nextafter(g, -np.inf if v_row > bake.HALF_LIGHT_MAG else np.inf))
    assert bake.v_from_gaia(g, 0.0) == bake.HALF_LIGHT_MAG
    for star_v, linked in ((0.0, True), (-1e-15, False)):
        (d,), _ = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "Alp", "", "And", mag=repr(star_v))))
        assert d.v_mag == star_v
        assert (bake.v_from_gaia(g, 0.0) - star_v > bake.HALF_LIGHT_MAG) != linked
        rows, _ = bake.link([d], {100: 9}, {}, {9: _row(False, 9, g=g, bp_rp=0.0)})
        assert (len(rows) == 1) == linked, star_v


def test_a_gaia_row_without_a_colour_in_the_relations_range_gets_no_designation() -> None:
    for bp_rp in (math.nan, 5.0, -0.5):
        (d,), _ = bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "Alp", "", "And", mag="5.0")))
        rows, set_aside = bake.link([d], {100: 9}, {}, {9: _row(False, 9, g=5.0, bp_rp=bp_rp)})
        assert rows == [] and set_aside == [(100, "no-colour-in-range")], bp_rp


# =============================================================================================
# Linking by HIP number
# =============================================================================================


def _one(hip: int = 100, **fields: str):
    values = {"bayer": "Alp", "flam": "", "con": "And", **fields}
    (d,), _ = bake.hyg_designations(_hyg(_hyg_line(1, str(hip), "", values["bayer"], values["flam"], values["con"], mag="5.0")))
    return d


def test_a_hipparcos_row_takes_its_own_hips_designation_whatever_its_magnitude() -> None:
    rows, set_aside = bake.link([_one()], {}, {100: _row(True, 100, g=9.0, bp_rp=math.nan, place=(0, 5, 21))}, {})
    assert set_aside == []
    assert rows == [["hipparcos-2007", "100", 0, 5, 21, 100, "hipparcos-row"]]


def test_a_hip_with_its_own_hipparcos_row_never_designates_its_best_neighbour() -> None:
    # As HIP 25606 (beta Lep) in the real tiles: its Hipparcos row and the Gaia row of its best neighbour both exist
    rows, set_aside = bake.link([_one()], {100: 9}, {100: _row(True, 100)}, {9: _row(False, 9, place=(1, 20, 7))})
    assert [r[0] for r in rows] == ["hipparcos-2007"] and set_aside == []


def test_a_gaia_row_takes_the_designation_of_the_hip_it_is_best_neighbour_of() -> None:
    rows, _ = bake.link([_one(flam="21")], {100: 4977699562277892864}, {}, {4977699562277892864: _row(False, 4977699562277892864, g=4.98)})
    assert rows == [["gaia-dr3", "4977699562277892864", 0, 1, 2, 100, "gaia-best-neighbour"]]


def test_a_hip_without_a_best_neighbour_or_whose_neighbour_is_not_a_tile_row_is_set_aside() -> None:
    assert bake.link([_one()], {}, {}, {}) == ([], [(100, "no-best-neighbour")])
    assert bake.link([_one()], {100: 9}, {}, {8: _row(False, 8)}) == ([], [(100, "best-neighbour-not-in-tiles")])


def test_no_position_is_read_to_link() -> None:
    assert set(bake.TileRow._fields) == {"catalog_hipparcos", "ident", "layer", "pix", "index", "mag", "bp_rp"}


def test_rows_are_written_in_hip_order_and_a_tile_row_designated_twice_is_refused() -> None:
    a, b = _one(hip=300), _one(hip=200)
    rows, _ = bake.link([a, b], {}, {300: _row(True, 300), 200: _row(True, 200)}, {})
    assert [r[5] for r in rows] == [200, 300]
    with pytest.raises(ValueError, match="twice"):
        bake.link([a, b], {300: 9, 200: 9}, {}, {9: _row(False, 9)})


def test_the_designation_rows_hold_hygs_values_only_in_hip_order() -> None:
    a = _one(hip=300, flam="21")
    b = _one(hip=200, bayer="Pi-1", con="Ori")
    assert bake.designation_rows([a, b]) == [[200, None, "Pi-1", "Ori", None, None, 1], [300, 21, "Alp", "And", None, None, 1]]


def test_the_columns_are_named_once_in_the_order_of_each_row_and_no_file_mixes_the_two_sources() -> None:
    assert bake.COLUMNS == ("hip", "flamsteed", "bayer", "constellation", "hd", "hr", "hygId")
    assert bake.SET_ASIDE_COLUMNS == ("hygId", "hip", "reason")
    assert bake.SET_ASIDE_REASONS == ("no-hip", "inconsistent-bf")
    assert bake.LINK_COLUMNS == ("catalog", "catalogId", "layer", "pix", "index", "hip", "link")
    assert bake.LINK_SET_ASIDE_COLUMNS == ("hip", "reason")
    assert bake.LINK_SET_ASIDE_REASONS == ("no-best-neighbour", "best-neighbour-not-in-tiles", "no-colour-in-range", "fainter-than-half-light")
    assert bake.LINK_KINDS == ("hipparcos-row", "gaia-best-neighbour")
    # The HIP number is the one key both sides share; nothing else is in both
    assert set(bake.COLUMNS) & set(bake.LINK_COLUMNS) == {"hip"}


def test_the_file_is_one_compact_ascii_line() -> None:
    text = bake.dumps_designations({"format": "x", "rows": [["gaia-dr3", "1", 0, 0, 0, 1, "gaia-best-neighbour", None, "Alp", "And", None, None, 1]]})
    assert text.endswith("\n") and text.count("\n") == 1 and text.isascii() and ", " not in text


def test_the_input_check_refuses_a_missing_short_or_changed_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        bake.check_inputs(tmp_path)
    for spec in bake.INPUTS:
        (tmp_path / spec.filename).write_bytes(b"x")
    with pytest.raises(SystemExit, match="bytes"):
        bake.check_inputs(tmp_path)


def test_the_input_check_refuses_a_file_of_the_right_size_with_other_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = bake.Input("a.csv", "https://example.org/a.csv", 1, hashlib.sha256(b"y").hexdigest(), "t")
    monkeypatch.setattr(bake, "INPUTS", (spec,))
    (tmp_path / "a.csv").write_bytes(b"x")
    with pytest.raises(SystemExit, match="sha256"):
        bake.check_inputs(tmp_path)
    (tmp_path / "a.csv").write_bytes(b"y")
    bake.check_inputs(tmp_path)


# Catalog numbers: a positive integer with no leading zero, or blank where the field may be blank
NUMBER_EDGES = [
    ("hip", ["1", "9", "10", "120416"], ["0", "01", "-1", "1.0", " 1", "a"]),
    ("hd", ["", "1", "358", "359083"], ["0", "01", "1.0", "x"]),
    ("hr", ["", "1", "15", "9110"], ["0", "01", "1.0", "x"]),
]


@pytest.mark.parametrize(("field", "inside", "outside"), NUMBER_EDGES)
def test_each_catalog_number_takes_its_pattern_and_refuses_one_step_outside(field: str, inside: list[str], outside: list[str]) -> None:
    def line(value: str) -> str:
        values = {"hip": "100", "hd": "", "hr": "", field: value}
        return _hyg_line(1, values["hip"], "", "Alp", "", "And", hd=values["hd"], hr=values["hr"])

    for value in inside:
        (d,), _ = bake.hyg_designations(_hyg(line(value)))
        assert getattr(d, field) == (int(value) if value else None), value
    for value in outside:
        with pytest.raises(ValueError):
            bake.hyg_designations(_hyg(line(value)))


@pytest.mark.parametrize("mag", ["nan", "inf", "-inf", "", "x"])
def test_a_designated_row_without_a_finite_magnitude_is_refused(mag: str) -> None:
    with pytest.raises(ValueError):
        bake.hyg_designations(_hyg(_hyg_line(1, "100", "", "Alp", "", "And", mag=mag)))


def _tile_pair(layer: int, pix: int, ids: list[int], flags: list[int], mags: list[float], colors: list[float]) -> tuple[bytes, bytes]:
    n = len(ids)
    rng = np.random.default_rng(pix)
    core = ct.encode_core_tile(
        layer=layer, order=0, pix=pix, position_bits=32,
        pos_u=ct.quantize_unit(rng.uniform(0, 1, n), 32), pos_v=ct.quantize_unit(rng.uniform(0, 1, n), 32),
        mag_code=ct.encode_mag(np.array(mags)), color_code=ct.encode_color(np.array(colors)),
        distance_code=ct.encode_distance(np.full(n, 10.0)), flags=np.array(flags, dtype=np.uint8),
    )
    star_ids = np.array(ids, dtype=np.uint64)
    detail = ct.encode_detail_tile(
        layer=layer, order=0, pix=pix, id_hi=star_ids >> np.uint64(32), id_lo=star_ids & np.uint64(0xFFFFFFFF),
        pm_ra_f16=ct.encode_f16(np.zeros(n)), pm_dec_f16=ct.encode_f16(np.zeros(n)), rv_f16=ct.encode_f16(np.full(n, np.nan)),
        lower_code=np.zeros(n, dtype=np.uint8), upper_code=np.zeros(n, dtype=np.uint8),
    )
    return core, detail


def _write_tiles(folder: Path, *tiles: tuple[int, int, list[int], list[int], list[float], list[float]]) -> None:
    for layer, pix, ids, flags, mags, colors in tiles:
        (folder / f"L{layer}").mkdir(exist_ok=True)
        core, detail = _tile_pair(layer, pix, ids, flags, mags, colors)
        (folder / f"L{layer}" / f"{pix}.core.bin").write_bytes(core)
        (folder / f"L{layer}" / f"{pix}.detail.bin").write_bytes(detail)


def test_tile_rows_take_every_hipparcos_row_and_only_the_wanted_gaia_rows(tmp_path: Path) -> None:
    hip, bv = ct.FLAG_CATALOG_HIPPARCOS, ct.FLAG_COLOR_B_V
    big = 5764614467999340032
    # Pixel 3 holds a Gaia source whose id equals a HIP number: it is not that star
    _write_tiles(
        tmp_path,
        (0, 3, [42, 7, big], [0, hip | bv, 0], [2.0, 3.0, 4.0], [0.25, 0.5, 1.25]),
        (1, 5, [9, 11], [hip, 0], [5.5, 6.75], [0.5, 2.0]),
    )
    hipparcos, gaia = bake.tile_rows(tmp_path, {42, big})
    assert sorted(hipparcos) == [7, 9] and sorted(gaia) == [42, big]
    seven = hipparcos[7]
    assert (seven.catalog_hipparcos, seven.layer, seven.pix, seven.index, seven.mag) == (True, 0, 3, 1, pytest.approx(3.0, abs=1e-3))
    assert math.isnan(seven.bp_rp)  # a B-V colour is not BP-RP
    assert (hipparcos[9].layer, hipparcos[9].pix, hipparcos[9].index) == (1, 5, 0)
    assert math.isnan(hipparcos[9].bp_rp) is False  # without the B-V flag the stored colour is BP-RP
    g = gaia[big]
    assert (g.catalog_hipparcos, g.ident, g.layer, g.pix, g.index) == (False, big, 0, 3, 2)
    assert g.mag == pytest.approx(4.0, abs=1e-3) and g.bp_rp == pytest.approx(1.25, abs=0.02)


def test_a_build_from_made_up_inputs_splits_hygs_values_from_esas_links(tmp_path: Path) -> None:
    cache, tiles = tmp_path / "cache", tmp_path / "tiles"
    cache.mkdir()
    tiles.mkdir()
    # HYG rows out of id order, two without a HIP number: the set-aside list comes out in id order all the same
    (cache / bake.HYG.filename).write_text(
        _hyg(
            _hyg_line(9, "", "Alp Cen", "Alp", "", "Cen"),
            _hyg_line(5, "300", "", "Alp", "", "And", mag="5.0", hd="358"),
            _hyg_line(3, "", "", "", "12", "Cen"),
            _hyg_line(4, "200", "21    Psc", "", "21", "Psc", mag="5.0", hr="15"),
        ),
        encoding="utf-8",
    )
    with gzip.open(cache / bake.BEST_NEIGHBOUR.filename, "wt", encoding="utf-8", newline="") as fh:
        fh.write("\n".join((BN_HEADER, "77,200,0.01,1,8", "78,300,0.6,1,16")) + "\n")
    _write_tiles(tiles, (0, 3, [300, 77, 78], [ct.FLAG_CATALOG_HIPPARCOS, 0, 0], [2.0, 5.0, 9.0], [0.0, 0.0, 0.0]))
    designations, links = bake.build_from(cache, tiles)
    assert designations["rows"] == [[200, 21, None, "Psc", None, 15, 4], [300, None, "Alp", "And", 358, None, 5]]
    assert designations["setAside"] == [[3, None, "no-hip"], [9, None, "no-hip"]]
    assert designations["counts"] == {"designated": 4, "rows": 2, "setAside": {"no-hip": 2, "inconsistent-bf": 0}}
    assert links["rows"] == [["gaia-dr3", "77", 0, 3, 1, 200, "gaia-best-neighbour"], ["hipparcos-2007", "300", 0, 3, 0, 300, "hipparcos-row"]]
    assert links["setAside"] == []
    assert links["counts"]["hips"] == 2 and links["counts"]["links"] == {"hipparcos-row": 1, "gaia-best-neighbour": 1}
    assert links["guardRead"] == {"id": bake.HYG_SOURCE_ID, "sha256": bake.HYG.sha256}
    # Nothing of HYG's but the HIP number is in the links, and nothing of ESA's is in the designations
    assert set(bake.COLUMNS) & set(bake.LINK_COLUMNS) == {"hip"} and "77" not in json.dumps(designations["rows"])


@pytest.mark.parametrize(("ids", "flags", "match"), [([7, 7], [ct.FLAG_CATALOG_HIPPARCOS] * 2, "HIP 7"), ([8, 8], [0, 0], "Gaia source 8")])
def test_a_row_found_twice_in_the_tiles_is_refused(tmp_path: Path, ids: list[int], flags: list[int], match: str) -> None:
    _write_tiles(tmp_path, (0, 3, ids[:1], flags[:1], [3.0], [0.5]), (0, 4, ids[1:], flags[1:], [3.0], [0.5]))
    with pytest.raises(ValueError, match=match):
        bake.tile_rows(tmp_path, {8})


# =============================================================================================
# The committed files and their records (no cache needed)
# =============================================================================================


def _doc() -> dict[str, Any]:
    return json.loads((OUT_DIR / "designations.json").read_text(encoding="utf-8"))


def _links() -> dict[str, Any]:
    return json.loads((LINKS_DIR / "links.json").read_text(encoding="utf-8"))


def test_the_committed_files_are_one_line_each_with_their_headers() -> None:
    for path in (OUT_DIR / "designations.json", LINKS_DIR / "links.json"):
        text = path.read_text(encoding="utf-8")
        assert text.count("\n") == 1 and text.isascii(), path
    doc, links = _doc(), _links()
    assert list(doc) == ["format", "formatVersion", "evidenceKind", "source", "counts", "columns", "rows", "setAsideColumns", "setAside"]
    assert (doc["format"], doc["formatVersion"], doc["evidenceKind"]) == ("albedian.catalog-star-designations", 1, "catalog-derived")
    assert doc["columns"] == list(bake.COLUMNS) and doc["setAsideColumns"] == list(bake.SET_ASIDE_COLUMNS)
    assert doc["source"] == {"id": bake.HYG_SOURCE_ID, "sha256": bake.HYG.sha256}
    assert all(len(r) == len(bake.COLUMNS) for r in doc["rows"])
    assert all(len(r) == 3 and r[2] in bake.SET_ASIDE_REASONS for r in doc["setAside"])
    assert list(links) == [
        "format", "formatVersion", "evidenceKind", "tilesRelease", "link", "guardRead", "counts", "columns", "rows",
        "setAsideColumns", "setAside",
    ]
    assert (links["format"], links["formatVersion"], links["evidenceKind"], links["tilesRelease"]) == (
        "albedian.catalog-star-designation-links", 1, "catalog-derived", "gaia-dr3",
    )
    assert links["columns"] == list(bake.LINK_COLUMNS) and links["setAsideColumns"] == list(bake.LINK_SET_ASIDE_COLUMNS)
    assert links["link"] == {"id": bake.BEST_NEIGHBOUR_SOURCE_ID, "sha256": bake.BEST_NEIGHBOUR.sha256}
    assert links["guardRead"] == {"id": bake.HYG_SOURCE_ID, "sha256": bake.HYG.sha256}
    assert all(len(r) == len(bake.LINK_COLUMNS) and r[6] in bake.LINK_KINDS for r in links["rows"])
    assert all(len(r) == 2 and r[1] in bake.LINK_SET_ASIDE_REASONS for r in links["setAside"])


def test_the_committed_counts_are_the_rows() -> None:
    doc, links = _doc(), _links()
    rows, aside = doc["rows"], doc["setAside"]
    reasons = {reason: sum(1 for r in aside if r[2] == reason) for reason in bake.SET_ASIDE_REASONS}
    assert doc["counts"] == {"designated": len(rows) + len(aside), "rows": len(rows), "setAside": reasons}
    kinds = {kind: sum(1 for r in links["rows"] if r[6] == kind) for kind in bake.LINK_KINDS}
    link_reasons = {reason: sum(1 for r in links["setAside"] if r[1] == reason) for reason in bake.LINK_SET_ASIDE_REASONS}
    assert links["counts"] == {"hips": len(rows), "rows": len(links["rows"]), "links": kinds, "setAside": link_reasons}


def test_every_designated_hip_is_either_linked_or_set_aside_once_and_the_two_files_join_by_hip() -> None:
    hips = [r[0] for r in _doc()["rows"]]
    assert hips == sorted(set(hips))
    linked = [r[5] for r in _links()["rows"]]
    aside = [r[0] for r in _links()["setAside"]]
    assert linked == sorted(linked) and aside == sorted(aside)
    assert sorted(linked + aside) == hips


def test_every_designation_holds_a_number_or_a_letter() -> None:
    for r in _doc()["rows"]:
        assert r[1] is not None or r[2] is not None, r


def test_every_link_points_at_its_tile_row() -> None:
    by_tile: dict[tuple[int, int], list[list[Any]]] = {}
    for r in _links()["rows"]:
        assert (r[0] == "hipparcos-2007") == (r[6] == "hipparcos-row")
        assert r[0] != "hipparcos-2007" or r[1] == str(r[5])
        by_tile.setdefault((r[2], r[3]), []).append(r)
    for (layer, pix), rows in by_tile.items():
        core_path = TILES / f"L{layer}" / f"{pix}.core.bin"
        _, core = ct.decode_core_tile(core_path.read_bytes())
        _, detail = ct.decode_detail_tile(core_path.with_name(f"{pix}.detail.bin").read_bytes())
        for r in rows:
            i = r[4]
            ident = (int(detail["idHi"][i]) << 32) | int(detail["idLo"][i])
            hipparcos = bool(core["flags"][i] & ct.FLAG_CATALOG_HIPPARCOS)
            assert (str(ident), hipparcos) == (r[1], r[0] == "hipparcos-2007"), r


def test_every_gaia_link_has_a_colour_in_the_relations_range_by_the_tile_rows_own_values() -> None:
    gaia = [r for r in _links()["rows"] if r[6] == "gaia-best-neighbour"]
    for r in gaia:
        core_path = TILES / f"L{r[2]}" / f"{r[3]}.core.bin"
        _, core = ct.decode_core_tile(core_path.read_bytes())
        g = float(ct.decode_mag(core["mag"][r[4] : r[4] + 1])[0])
        c = float(ct.decode_color(core["color"][r[4] : r[4] + 1])[0])
        assert not core["flags"][r[4]] & ct.FLAG_COLOR_B_V
        assert bake.v_from_gaia(g, c) is not None, r
    assert len(gaia) == _links()["counts"]["links"]["gaia-best-neighbour"]


def test_the_companions_found_in_the_real_tiles_get_no_link() -> None:
    # Each one's best neighbour is a tile row 1.4 to 6.5 mag fainter than the star (catalog-2a report): a companion
    links = _links()
    linked = {r[5] for r in links["rows"]}
    aside = {r[0]: r[1] for r in links["setAside"]}
    for hip in (77052, 23794, 23596, 43496, 25813, 80883):
        assert hip not in linked and aside[hip] in ("fainter-than-half-light", "no-colour-in-range"), hip
    (beta_lep,) = [r for r in links["rows"] if r[5] == 25606]
    assert (beta_lep[0], beta_lep[6]) == ("hipparcos-2007", "hipparcos-row")
    (letters,) = [r for r in _doc()["rows"] if r[0] == 25606]
    assert (letters[1], letters[2], letters[3]) == (9, "Bet", "Lep")


def test_the_letters_agree_with_the_iau_names_file_where_both_give_one() -> None:
    # IAU-CSN writes the letter as a lower-case abbreviation with a two-digit superscript ("tet01"); HYG as "The-1"
    iau = {"alf": "Alp", "bet": "Bet", "gam": "Gam", "del": "Del", "eps": "Eps", "zet": "Zet", "eta": "Eta",
           "tet": "The", "iot": "Iot", "kap": "Kap", "lam": "Lam", "mu": "Mu", "nu": "Nu", "ksi": "Xi", "omi": "Omi",
           "pi": "Pi", "rho": "Rho", "sig": "Sig", "tau": "Tau", "ups": "Ups", "phi": "Phi", "chi": "Chi", "psi": "Psi",
           "ome": "Ome"}
    linked = {r[5] for r in _links()["rows"]}
    ours = {r[0]: r for r in _doc()["rows"] if r[2] is not None and r[0] in linked}
    compared, differ = 0, []
    for n in json.loads((TILES / "names.json").read_text(encoding="utf-8"))["names"]:
        r = ours.get(n.get("hip"))
        if r is None or not n.get("bayer"):
            continue
        compared += 1
        base = n["bayer"].rstrip("0123456789")
        sup = n["bayer"][len(base):]
        letter, _, our_sup = r[2].partition("-")
        if iau.get(base) != letter or n["constellation"] != r[3] or (sup and our_sup and int(sup) != int(our_sup)):
            differ.append(n["hip"])
    # HIP 43587 (Copernicus): names.json has "55" in its bayer field, a Flamsteed number; HYG has Rho-1 and 55
    assert (compared, sorted(differ)) == (243, [43587])


def _registry() -> dict[str, dict[str, Any]]:
    return {e["path"]: e for e in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}


def test_the_designations_record_pins_its_file_and_names_hyg_alone_under_cc_by_sa() -> None:
    text = (OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8")
    record = AssetProvenanceRecord.model_validate_json(text)
    assert dumps_record(record) == text
    assert text == dumps_record(bake.build_provenance_record(OUT_DIR))
    (entry,) = record.files
    assert entry.path == "designations.json" and record.file_sets == ()
    assert entry.sha256 == hashlib.sha256((OUT_DIR / "designations.json").read_bytes()).hexdigest()
    assert entry.produced_by.method == "script" and entry.produced_by.script == "scripts/bake_star_designations.py"
    (hyg,) = record.sources
    assert entry.sources == (bake.HYG_SOURCE_ID,) and hyg.id == bake.HYG_SOURCE_ID
    assert hyg.license.spdx == "CC-BY-SA-4.0" and hyg.license.url == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert hyg.credit == "HYG Database, astronexus" and hyg.version == "v4.1" and hyg.sha256 == bake.HYG.sha256
    download = _registry()[HYG_REL]
    assert (download["url"], download["sha256"], download["bytes"]) == (hyg.url, bake.HYG.sha256, bake.HYG.bytes)
    assert download["licence"] == "CC-BY-SA-4.0" and download["fetch"] == "scripts/fetch_download.py"
    note = entry.note or ""
    assert "CC BY-SA 4.0" in note and "astronexus" in note and "ESA" in note and "../designation-links/links.json" in note


def test_the_links_record_pins_its_file_names_esas_sources_alone_and_says_the_guard_read_hyg() -> None:
    text = (LINKS_DIR / "PROVENANCE.json").read_text(encoding="utf-8")
    record = AssetProvenanceRecord.model_validate_json(text)
    assert dumps_record(record) == text
    assert text == dumps_record(bake.build_links_record(LINKS_DIR, TILES))
    (entry,) = record.files
    assert entry.path == "links.json" and record.file_sets == ()
    assert entry.sha256 == hashlib.sha256((LINKS_DIR / "links.json").read_bytes()).hexdigest()
    assert entry.produced_by.script == "scripts/bake_star_designations.py"
    sources = {s.id: s for s in record.sources}
    assert entry.sources == (bake.BEST_NEIGHBOUR_SOURCE_ID, "gaia-dr3", "hipparcos-2007") == tuple(sources)
    assert all(s.license.spdx == "CC-BY-NC-3.0-IGO" for s in record.sources)
    bn = sources[bake.BEST_NEIGHBOUR_SOURCE_ID]
    assert bn.credit == "ESA/Gaia/DPAC" and bn.sha256 == bake.BEST_NEIGHBOUR.sha256
    download = _registry()[BN_REL]
    assert (download["url"], download["sha256"], download["bytes"]) == (bn.url, bake.BEST_NEIGHBOUR.sha256, bake.BEST_NEIGHBOUR.bytes)
    assert download["licence"] == "CC-BY-NC-3.0-IGO" and download["fetch"] == "scripts/fetch_download.py"
    tiles = {s["id"]: s for s in json.loads((TILES / "PROVENANCE.json").read_text(encoding="utf-8"))["sources"]}
    ours = {s["id"]: s for s in record.to_contract_dict()["sources"]}
    for copied in ("gaia-dr3", "hipparcos-2007"):
        assert ours[copied] == {**tiles[copied], "note": ours[copied]["note"]}, copied
    for key in ("license", "credit", "citation", "doi"):
        assert ours[bake.BEST_NEIGHBOUR_SOURCE_ID][key] == tiles["gaia-dr3-hipparcos2-best-neighbour"][key], key
    note = entry.note or ""
    assert "The guard read HYG v4.1" in note and bake.HYG.sha256 in note and "[proposed" in note
    assert "CC BY-NC 3.0 IGO" in note and "../designations/designations.json" in note


# =============================================================================================
# Real data (the two cached downloads)
# =============================================================================================


CACHE = ROOT / ".cache" / "star-designations"


@pytest.mark.local_dataset(HYG_REL, BN_REL)
def test_the_cached_inputs_are_the_registered_ones_and_a_fresh_bake_matches_the_committed_files() -> None:
    bake.check_inputs(CACHE)
    assert bake.verify_outputs(CACHE, OUT_DIR, LINKS_DIR, TILES) == []


def _copy_outputs(tmp_path: Path) -> tuple[Path, Path]:
    out, links = tmp_path / "designations", tmp_path / "designation-links"
    for src, dst in ((OUT_DIR, out), (LINKS_DIR, links)):
        dst.mkdir()
        for path in src.iterdir():
            (dst / path.name).write_bytes(path.read_bytes())
    return out, links


@pytest.mark.local_dataset(HYG_REL, BN_REL)
@pytest.mark.parametrize(
    ("folder", "name", "old", "new"),
    [
        ("designations", "designations.json", '"Alp"', '"Bet"'),
        ("designations", "PROVENANCE.json", "HYG Database, astronexus", "HYG"),
        ("designation-links", "links.json", '"hipparcos-row"', '"gaia-best-neighbour"'),
        ("designation-links", "PROVENANCE.json", "The guard read", "The guard saw"),
    ],
)
def test_verify_reports_each_file_that_differs_from_a_fresh_bake(tmp_path: Path, folder: str, name: str, old: str, new: str) -> None:
    out, links = _copy_outputs(tmp_path)
    target = tmp_path / folder / name
    text = target.read_text(encoding="utf-8")
    assert old in text
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    assert bake.verify_outputs(CACHE, out, links, TILES) == [f"{target} differs from a fresh bake"]


@pytest.mark.local_dataset(HYG_REL, BN_REL)
def test_every_value_is_its_rows() -> None:
    import csv

    with open(ROOT / HYG_REL, encoding="utf-8", newline="") as fh:
        hyg = {int(r["id"]): r for r in csv.DictReader(fh)}
    with gzip.open(ROOT / BN_REL, "rt", newline="") as fh:
        bn = {int(r["original_ext_source_id"]): r["source_id"] for r in csv.DictReader(fh)}
    v_by_hip = {}
    for r in _doc()["rows"]:
        h = hyg[r[6]]
        assert int(h["hip"]) == r[0]
        assert (h["flam"] or None, h["bayer"] or None, h["con"]) == (None if r[1] is None else str(r[1]), r[2], r[3])
        assert (h["hd"] or None, h["hr"] or None) == (None if r[4] is None else str(r[4]), None if r[5] is None else str(r[5]))
        v_by_hip[r[0]] = float(h["mag"])
    for r in _links()["rows"]:
        if r[6] == "gaia-best-neighbour":
            assert bn[r[5]] == r[1]
            _, core = ct.decode_core_tile((TILES / f"L{r[2]}" / f"{r[3]}.core.bin").read_bytes())
            g = float(ct.decode_mag(core["mag"][r[4] : r[4] + 1])[0])
            c = float(ct.decode_color(core["color"][r[4] : r[4] + 1])[0])
            assert bake.v_from_gaia(g, c) - v_by_hip[r[5]] <= bake.HALF_LIGHT_MAG, r
    designated = sum(1 for h in hyg.values() if h["bayer"] or h["flam"] or h["bf"])
    assert designated == _doc()["counts"]["designated"]

@pytest.mark.local_dataset(HYG_REL, BN_REL)
def test_the_greek_letters_are_all_that_hyg_uses() -> None:
    import csv

    with open(ROOT / HYG_REL, encoding="utf-8", newline="") as fh:
        letters = {r["bayer"].partition("-")[0] for r in csv.DictReader(fh) if r["bayer"]}
    assert letters == set(bake.GREEK_LETTERS)


def test_v_from_g_returns_a_plain_float_for_numpy_values() -> None:
    assert isinstance(bake.v_from_gaia(np.float64(5.0), np.float64(0.2)), float)
