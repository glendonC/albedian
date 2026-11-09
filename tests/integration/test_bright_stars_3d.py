"""Bright-star 3D supplement bake (data-11): parsing, the committed table, its record, and real-data checks.

`scripts/bake_bright_stars_3d.py` writes `apps/web/public/catalog/stars/hipparcos-bright-3d/stars.json` and its
`PROVENANCE.json` for the 226 stars the G 12 tiles take from Hipparcos rather than Gaia DR3 (the tiles'
bright-star-merge.json, action use_hipparcos), from one ESA Gaia archive query of Hipparcos 2007 (van Leeuwen 2007)
joined with V from the Hipparcos Catalogue (ESA 1997), CC BY-NC 3.0 IGO, credit ESA, cached in
`.cache/hipparcos-bright-3d/` and registered in `data/downloads.json`. Each row carries its place in the tiles' core
blocks, so these tests check every place against the tiles and against the row's position moved to J2016.0.

Published comparison values, each read at its source before it was written here:
- alpha Centauri: Kervella, Mignard, Merand & Thevenin 2016, A&A 594, A107 (arXiv:1610.06079v1, Sect. 3.1 and
  Table 1): "We find pi = 747.17 +- 0.61 mas", one parallax for the A-B pair from the orbit and radial velocities.
- Sirius: Gatewood & Gatewood 1978 (ApJ 225, 191), a ground-based compilation independent of Hipparcos, 0.3777 +-
  0.0031 arcsec, and van Leeuwen 2007's 0.37921 +- 0.00158 arcsec, both as tabulated by Bond et al. 2017, ApJ 840, 70
  (arXiv:1703.10625v1, Table 5 and Sect. 6.1). Bond et al.'s own adopted value is a weighted mean that includes
  Hipparcos, so it is not used as a check of Hipparcos.
The journal pages (aanda.org, the ADS abstract) refused automated reading, so the arXiv texts were read.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from services.contracts.bright_stars import (
    BRIGHT_STAR_COLUMNS,
    BRIGHT_STAR_EPOCH_JULIAN_YEAR,
    BrightStarSupplement,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record
from services.science import catalog_tiles as ct

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_REL = ".cache/hipparcos-bright-3d/hipparcos_bright_3d.csv"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "hipparcos-bright-3d"
TILES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
DOC_PATH = ROOT / "docs" / "science" / "BRIGHT_STARS_3D.md"

# The codec's own round-trip bound for 32-bit tile positions
# (tests/integration/test_catalog_star_tiles.py::test_core_and_detail_tiles_round_trip); all 226 sit in layer 0
TILE_TOLERANCE_ARCSEC = 0.001
TILE_EPOCH = 2016.0

# Kervella et al. 2016, Sect. 3.1 and Table 1 (K16): the alpha Cen A-B parallax
KERVELLA_2016_ALPHA_CEN_MAS = (747.17, 0.61)
# Bond et al. 2017, Table 5: Gatewood & Gatewood 1978's ground-based compilation, and van Leeuwen 2007, for Sirius
GG78_SIRIUS_MAS = (377.7, 3.1)
VAN_LEEUWEN_2007_SIRIUS_MAS_PER_BOND = (379.21, 1.58)

SIRIUS, RIGIL_KENTAURUS, TOLIMAN = 32349, 71683, 71681
NON_POSITIVE_PARALLAX = {26220, 26224, 71502}


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_bright_stars_3d", ROOT / "scripts" / "bake_bright_stars_3d.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_bright_stars_3d.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


def col(name: str) -> int:
    return BRIGHT_STAR_COLUMNS.index(name)


def _committed() -> dict[str, Any]:
    return json.loads((OUT_DIR / "stars.json").read_text(encoding="utf-8"))


def _rows_by_hip() -> dict[int, list[Any]]:
    return {row[0]: row for row in _committed()["rows"]}


def _merge_decisions() -> dict[int, dict[str, Any]]:
    merge = json.loads((TILES / "bright-star-merge.json").read_text(encoding="utf-8"))
    return {d["hip"]: d for d in merge["decisions"] if d["action"] == "use_hipparcos"}


def _names() -> list[dict[str, Any]]:
    return json.loads((TILES / "names.json").read_text(encoding="utf-8"))["names"]


def _z(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) / math.hypot(a[1], b[1])


# =============================================================================================
# Parsing and building, on made-up rows (no cache needed)
# =============================================================================================


def _archive_csv(*rows: dict[str, str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(bake.ARCHIVE_HEADER), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def _made_up(hip: int, **values: str) -> dict[str, str]:
    row = {
        "hip": str(hip), "ra": "101.5", "dec": "-16.25", "plx": "100.5", "e_plx": "1.25", "pm_ra": "-546.01",
        "pm_de": "-1223.07", "e_ra_rad": "1.26", "e_de_rad": "1.13", "e_pm_ra": "1.33", "e_pm_de": "1.24",
        "hp_mag": "-1.0876", "b_v": "0.009", "e_b_v": "0.006", "sn": "0", "nc": "1", "vmag": "-1.44",
        "multflag": "", "ccdm": "",
    }
    row.update(values)
    return row


def test_the_archive_parser_reads_every_column_by_name_and_refuses_another_header() -> None:
    rows = bake.parse_archive_csv(_archive_csv(_made_up(7), _made_up(9, vmag="", b_v="")))
    assert sorted(rows) == [7, 9]
    assert rows[9]["vmag"] == "" and rows[9]["b_v"] == ""
    with pytest.raises(ValueError, match="header"):
        bake.parse_archive_csv(_archive_csv(_made_up(7)).replace("e_ra_rad", "e_ra", 1))
    with pytest.raises(ValueError, match="twice"):
        bake.parse_archive_csv(_archive_csv(_made_up(7), _made_up(7)))


def test_a_row_keeps_the_archive_values_and_writes_a_blank_as_null() -> None:
    archive = bake.parse_archive_csv(_archive_csv(_made_up(7), _made_up(9, vmag="", b_v="", plx="-3.5", sn="95", nc="2")))
    decisions = {
        7: {"action": "use_hipparcos", "hip": 7, "gaiaSourceId": None, "reason": "no Gaia DR3 best neighbour"},
        9: {"action": "use_hipparcos", "hip": 9, "gaiaSourceId": "5877748442128924544", "reason": "made up"},
    }
    names = {7: "Made-up Star"}
    places = {7: (0, 5, 0), 9: (0, 10, 3)}
    supplement = bake.build_supplement(archive, decisions, names, places)
    assert isinstance(supplement, BrightStarSupplement)
    rows = supplement.to_contract_dict()["rows"]
    assert [row[0] for row in rows] == [7, 9]
    seven, nine = rows
    assert seven == [
        7, "Made-up Star", 101.5, -16.25, 1.26, 1.13, 100.5, 1.25, -546.01, -1223.07, 1.33, 1.24, -1.0876, -1.44,
        0.009, 0, 1, None, "no Gaia DR3 best neighbour", 0, 5, 0,
    ]
    assert nine[col("name")] is None
    assert nine[col("vMag")] is None and nine[col("bMinusV")] is None
    assert nine[col("parallaxMas")] == -3.5
    assert (nine[col("solutionType")], nine[col("components")]) == (95, 2)
    assert nine[col("gaiaSourceId")] == "5877748442128924544"
    assert nine[col("tileLayer") :] == [0, 10, 3]


def test_the_build_refuses_a_star_set_that_differs_from_the_merge() -> None:
    archive = bake.parse_archive_csv(_archive_csv(_made_up(7), _made_up(9)))
    decision = {"action": "use_hipparcos", "gaiaSourceId": None, "reason": "made up"}
    both = {7: {**decision, "hip": 7}, 9: {**decision, "hip": 9}}
    places = {7: (0, 5, 0), 9: (0, 5, 1)}
    bake.build_supplement(archive, both, {}, places)
    with pytest.raises(ValueError, match="archive"):
        bake.build_supplement(archive, {7: both[7]}, {}, places)
    with pytest.raises(ValueError, match="archive"):
        bake.build_supplement(bake.parse_archive_csv(_archive_csv(_made_up(7))), both, {}, places)
    with pytest.raises(ValueError, match="tile"):
        bake.build_supplement(archive, both, {}, {7: (0, 5, 0)})


def test_only_the_merges_use_hipparcos_decisions_are_taken() -> None:
    merge = {"decisions": [
        {"action": "keep_gaia", "hip": 1, "gaiaSourceId": "11", "reason": "agrees"},
        {"action": "use_hipparcos", "hip": 2, "gaiaSourceId": None, "reason": "no Gaia DR3 best neighbour"},
        {"action": "drop_gaia", "hip": 2, "gaiaSourceId": "22", "reason": "duplicate"},
        {"action": "use_hipparcos", "hip": 3, "gaiaSourceId": "33", "reason": "saturated"},
    ]}
    assert bake.hipparcos_decisions(merge) == {2: merge["decisions"][1], 3: merge["decisions"][3]}


def test_the_table_is_written_as_one_compact_ascii_line() -> None:
    archive = bake.parse_archive_csv(_archive_csv(_made_up(7)))
    decisions = {7: {"action": "use_hipparcos", "hip": 7, "gaiaSourceId": None, "reason": "made up"}}
    supplement = bake.build_supplement(archive, decisions, {7: "Ac\u0101mar"}, {7: (0, 5, 0)})
    text = bake.dumps_supplement(supplement)
    assert text.endswith("\n") and text.count("\n") == 1 and text.isascii()
    assert text == json.dumps(json.loads(text), ensure_ascii=True, separators=(",", ":")) + "\n" and "\\u0101" in text
    assert json.loads(text) == supplement.to_contract_dict()


def test_names_attach_by_hip_and_a_hip_named_twice_is_refused() -> None:
    names = {"names": [{"hip": 7, "name": "Made-up Star"}, {"hip": None, "name": "No HIP"}, {"name": "No key"}]}
    assert bake.names_by_hip(names) == {7: "Made-up Star"}
    with pytest.raises(ValueError, match="two names"):
        bake.names_by_hip({"names": [{"hip": 7, "name": "A"}, {"hip": 7, "name": "B"}]})


def test_the_input_check_refuses_a_missing_short_or_changed_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        bake.check_inputs(tmp_path)
    target = tmp_path / bake.ARCHIVE.filename
    target.write_bytes(b"x" * (bake.ARCHIVE.bytes - 1))
    with pytest.raises(SystemExit, match="bytes"):
        bake.check_inputs(tmp_path)
    target.write_bytes(b"x" * bake.ARCHIVE.bytes)
    with pytest.raises(SystemExit, match="sha256"):
        bake.check_inputs(tmp_path)


def _tile_pair(pix: int, ids: list[int], hipparcos: list[bool]) -> tuple[bytes, bytes]:
    n = len(ids)
    rng = np.random.default_rng(pix)
    flags = np.array([ct.FLAG_CATALOG_HIPPARCOS if h else 0 for h in hipparcos], dtype=np.uint8)
    core = ct.encode_core_tile(
        layer=0, order=0, pix=pix, position_bits=32,
        pos_u=ct.quantize_unit(rng.uniform(0, 1, n), 32), pos_v=ct.quantize_unit(rng.uniform(0, 1, n), 32),
        mag_code=ct.encode_mag(np.linspace(-1, 5, n)), color_code=ct.encode_color(np.zeros(n)),
        distance_code=ct.encode_distance(np.full(n, 10.0)), flags=flags,
    )
    star_ids = np.array(ids, dtype=np.uint64)
    detail = ct.encode_detail_tile(
        layer=0, order=0, pix=pix, id_hi=star_ids >> np.uint64(32), id_lo=star_ids & np.uint64(0xFFFFFFFF),
        pm_ra_f16=ct.encode_f16(np.zeros(n)), pm_dec_f16=ct.encode_f16(np.zeros(n)), rv_f16=ct.encode_f16(np.full(n, np.nan)),
        lower_code=np.zeros(n, dtype=np.uint8), upper_code=np.zeros(n, dtype=np.uint8),
    )
    return core, detail


def test_tile_places_come_from_hipparcos_rows_only_and_a_star_found_twice_is_refused(tmp_path: Path) -> None:
    (tmp_path / "L0").mkdir()
    # Pixel 3 holds a Gaia source whose id equals a HIP number: it is not that star
    for pix, ids, hipparcos in ((3, [42, 7], [False, True]), (5, [9, 42], [True, True])):
        core, detail = _tile_pair(pix, ids, hipparcos)
        (tmp_path / "L0" / f"{pix}.core.bin").write_bytes(core)
        (tmp_path / "L0" / f"{pix}.detail.bin").write_bytes(detail)
    assert bake.tile_places(tmp_path) == {7: (0, 3, 1), 9: (0, 5, 0), 42: (0, 5, 1)}
    core, detail = _tile_pair(6, [7], [True])
    (tmp_path / "L0" / "6.core.bin").write_bytes(core)
    (tmp_path / "L0" / "6.detail.bin").write_bytes(detail)
    with pytest.raises(ValueError, match="twice"):
        bake.tile_places(tmp_path)


def test_the_parallax_rule_gives_a_distance_only_for_a_positive_parallax_with_e_over_p_at_most_a_tenth() -> None:
    assert bake.MAX_RELATIVE_PARALLAX_ERROR == 0.1
    assert bake.parallax_distance_pc(100.0, 10.0) == pytest.approx((10.0, 1000.0 / 110.0, 1000.0 / 90.0))
    assert bake.parallax_distance_pc(5.0, 0.5) == pytest.approx((200.0, 1000.0 / 5.5, 1000.0 / 4.5))
    assert bake.parallax_distance_pc(379.21, 1.58) == pytest.approx((1000.0 / 379.21, 1000.0 / 380.79, 1000.0 / 377.63))
    for parallax, error in ((5.0, 0.51), (5.0, 6.0), (1.53, 1.29), (0.0, 1.0), (-27.24, 8.21)):
        assert bake.parallax_distance_pc(parallax, error) is None, (parallax, error)


def test_the_contract_states_the_threshold_and_that_integers_are_written_plain() -> None:
    for path in (ROOT / "services" / "contracts" / "bright_stars.py", ROOT / "packages" / "contracts" / "src" / "bright-stars.ts"):
        text = " ".join(line.strip(" *") for line in path.read_text(encoding="utf-8").splitlines())
        assert "e / p is at most 0.1" in text, path
        assert "no 1000 / p distance is given" in text, path
        assert "written plain" in text and "32349.0" in text, path
    # What the comment says of this side: a HIP written 32349.0 by hand is refused
    sample = json.loads((ROOT / "tests" / "fixtures" / "bright_stars_contract.json").read_text(encoding="utf-8"))["valid"][0]
    text = json.dumps(sample)
    hip = sample["rows"][0][0]
    with pytest.raises(ValueError, match="valid integer"):
        BrightStarSupplement.model_validate_json(text.replace(f"[{hip},", f"[{hip}.0,", 1))


# =============================================================================================
# The committed table (no cache needed)
# =============================================================================================


def test_the_committed_table_is_one_valid_line() -> None:
    text = (OUT_DIR / "stars.json").read_text(encoding="utf-8")
    assert text.endswith("\n") and text.count("\n") == 1
    supplement = BrightStarSupplement.model_validate_json(text)
    assert supplement.to_contract_dict() == json.loads(text)
    data = json.loads(text)
    assert data["epochJulianYear"] == BRIGHT_STAR_EPOCH_JULIAN_YEAR == 1991.25
    assert data["frame"] == "ICRS" and data["evidenceKind"] == "catalog-derived"
    assert data["tilesRelease"] == "gaia-dr3"
    assert "van Leeuwen 2007" in data["catalogue"] and "ESA 1997" in data["catalogue"]


def test_exactly_the_stars_the_tiles_take_from_hipparcos_in_hip_order() -> None:
    hips = [row[0] for row in _committed()["rows"]]
    assert hips == sorted(_merge_decisions()) and len(hips) == 226


def test_gaia_neighbour_reason_and_name_are_the_tiles_own() -> None:
    decisions = _merge_decisions()
    names = {n["hip"]: n["name"] for n in _names() if n.get("hip")}
    rows = _rows_by_hip()
    for hip, row in rows.items():
        assert row[col("gaiaSourceId")] == decisions[hip]["gaiaSourceId"], hip
        assert row[col("hipparcosReason")] == decisions[hip]["reason"], hip
        assert row[col("name")] == names.get(hip), hip
    assert sum(1 for row in rows.values() if row[col("name")]) == 151
    assert (rows[SIRIUS][col("name")], rows[RIGIL_KENTAURUS][col("name")], rows[TOLIMAN][col("name")]) == (
        "Sirius", "Rigil Kentaurus", "Toliman"
    )


def _tile_rows(place: tuple[int, int, int]) -> tuple[int, int, int, np.ndarray]:
    layer, pix, index = place
    header, core = ct.decode_core_tile((TILES / f"L{layer}" / f"{pix}.core.bin").read_bytes())
    _, detail = ct.decode_detail_tile((TILES / f"L{layer}" / f"{pix}.detail.bin").read_bytes())
    assert header.position_bits == 32, place
    star_id = (int(detail["idHi"][index]) << 32) | int(detail["idLo"][index])
    return star_id, int(core["flags"][index]), int(core["mag"][index]), ct.decode_core_directions(header, core)[index]


def test_every_tile_place_points_at_that_star_where_its_position_moved_to_j2016_says() -> None:
    manifest = json.loads((TILES / "manifest.json").read_text(encoding="utf-8"))
    layer0 = next(layer for layer in manifest["layers"] if layer["layer"] == 0)
    assert layer0["positionBits"] == 32 and layer0["maxPositionErrorArcsec"] <= TILE_TOLERANCE_ARCSEC
    rows = list(_rows_by_hip().values())
    found = [_tile_rows(tuple(row[col("tileLayer") :])) for row in rows]
    for row, (star_id, flags, mag_code, _) in zip(rows, found):
        assert star_id == row[0] and flags & ct.FLAG_CATALOG_HIPPARCOS and flags & ct.FLAG_MAG_BAND_HP, row[0]
        assert mag_code == int(ct.encode_mag(np.array([row[col("hpMag")]]))[0]), row[0]
    tiles = np.array([f[3] for f in found])
    ra, dec = (np.array([row[col(name)] for row in rows]) for name in ("raDeg", "decDeg"))
    pm_ra, pm_dec = (np.array([row[col(name)] for row in rows]) for name in ("pmRaCosDecMasPerYr", "pmDecMasPerYr"))
    moved = ct.radec_to_vec(*ct.propagate_linear(ra, dec, pm_ra, pm_dec, TILE_EPOCH - BRIGHT_STAR_EPOCH_JULIAN_YEAR))
    assert ct.angular_separation_arcsec(moved, tiles).max() < TILE_TOLERANCE_ARCSEC
    # The check can tell: unmoved, even the slowest of the 226 sits over 20 times the tolerance from its tile row
    assert ct.angular_separation_arcsec(ct.radec_to_vec(ra, dec), tiles).min() > 20 * TILE_TOLERANCE_ARCSEC


def test_named_stars_sit_where_the_tiles_names_put_them() -> None:
    rows = _rows_by_hip()
    named = [n for n in _names() if n.get("hip") in rows]
    assert len(named) == 151
    for n in named:
        assert n["catalog"] == "hipparcos-2007", n["name"]
        assert rows[n["hip"]][col("tileLayer") :] == [n["layer"], n["pix"], n["index"]], n["name"]


def test_sirius_parallax_is_van_leeuwens_and_agrees_with_the_ground_based_one() -> None:
    row = _rows_by_hip()[SIRIUS]
    hipparcos = (row[col("parallaxMas")], row[col("parallaxErrorMas")])
    assert hipparcos == VAN_LEEUWEN_2007_SIRIUS_MAS_PER_BOND
    assert _z(hipparcos, GG78_SIRIUS_MAS) < 1.0
    nearest, lower, upper = bake.parallax_distance_pc(*hipparcos)
    assert lower < 1000.0 / GG78_SIRIUS_MAS[0] < upper
    assert nearest == pytest.approx(2.637, abs=5e-4)


def test_rigil_kentaurus_parallax_agrees_with_kervella_2016_within_two_sigma() -> None:
    row = _rows_by_hip()[RIGIL_KENTAURUS]
    hipparcos = (row[col("parallaxMas")], row[col("parallaxErrorMas")])
    assert hipparcos == (754.81, 4.11)
    assert 1.5 < _z(hipparcos, KERVELLA_2016_ALPHA_CEN_MAS) < 2.0
    nearest, lower, upper = bake.parallax_distance_pc(*hipparcos)
    assert nearest == pytest.approx(1.3248, abs=5e-5)
    # Kervella et al.'s 1.338 pc lies outside the 1 sigma range of the 2007 parallax alone; the doc says so
    assert upper < 1000.0 / KERVELLA_2016_ALPHA_CEN_MAS[0]


def test_toliman_and_rigil_kentaurus_are_one_system_with_two_parallaxes_and_the_doc_says_so() -> None:
    rows = _rows_by_hip()
    a, b = rows[RIGIL_KENTAURUS], rows[TOLIMAN]
    gap = round(b[col("parallaxMas")] - a[col("parallaxMas")], 2)
    assert gap == 42.11
    assert a[col("tileLayer") :][:2] == b[col("tileLayer") :][:2] == [0, 10]
    assert _z((b[col("parallaxMas")], b[col("parallaxErrorMas")]), KERVELLA_2016_ALPHA_CEN_MAS) < 2.0
    separation_au = (1000.0 / a[col("parallaxMas")] - 1000.0 / b[col("parallaxMas")]) * 206264.806
    assert 14_400 < separation_au < 14_500
    doc = DOC_PATH.read_text(encoding="utf-8")
    for needle in ("HIP 71681", "HIP 71683", "42.11 mas", "747.17", "14,400 au"):
        assert needle in doc, needle
    note = json.loads((OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))["files"][0]["note"]
    assert "Toliman" in note and "42.11 mas" in note


def test_stars_without_a_distance_under_the_rule_are_kept_and_counted_in_the_doc() -> None:
    rows = _rows_by_hip()
    non_positive = {hip for hip, row in rows.items() if row[col("parallaxMas")] <= 0}
    assert non_positive == NON_POSITIVE_PARALLAX
    ratio = {hip: row[col("parallaxErrorMas")] / row[col("parallaxMas")] for hip, row in rows.items() if hip not in non_positive}
    assert (sum(r > 0.1 for r in ratio.values()), sum(r > 0.2 for r in ratio.values())) == (26, 4)
    without = {hip for hip, row in rows.items() if bake.parallax_distance_pc(row[col("parallaxMas")], row[col("parallaxErrorMas")]) is None}
    assert without == non_positive | {hip for hip, r in ratio.items() if r > 0.1}
    assert len(rows) - len(without) == 197
    doc = DOC_PATH.read_text(encoding="utf-8")
    for needle in ("197 of the 226", "26 have", "4 of them", *(f"HIP {hip}" for hip in sorted(NON_POSITIVE_PARALLAX))):
        assert needle in doc, needle


# =============================================================================================
# The record, the registry and the doc (no cache needed)
# =============================================================================================


def test_the_record_pins_the_table_and_names_the_query_licence_and_credit() -> None:
    text = (OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8")
    record = AssetProvenanceRecord.model_validate_json(text)
    assert dumps_record(record) == text
    assert text == dumps_record(bake.build_provenance_record(OUT_DIR))
    (entry,) = record.files
    assert entry.path == "stars.json"
    assert entry.sha256 == hashlib.sha256((OUT_DIR / "stars.json").read_bytes()).hexdigest()
    sources = {source.id: source for source in record.sources}
    assert set(entry.sources) == set(sources)
    query = sources[bake.ARCHIVE_SOURCE_ID]
    registry = {e["path"]: e for e in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}
    download = registry[ARCHIVE_REL]
    assert (query.url, query.sha256) == (download["url"], download["sha256"])
    assert (download["bytes"], download["fetch"]) == (28_076, "scripts/fetch_download.py")
    assert download["licence"] == "CC-BY-NC-3.0-IGO"
    # As the gaia-dr3 tiles' own record has them: the archive's sources under CC BY-NC 3.0 IGO, the names under the IAU's
    tiles = {s["id"]: s for s in json.loads((TILES / "PROVENANCE.json").read_text(encoding="utf-8"))["sources"]}
    ours = {s["id"]: s for s in record.to_contract_dict()["sources"]}
    for copied in ("gaia-dr3-hipparcos2-best-neighbour", "iau-csn"):
        assert ours[copied] == {**tiles[copied], "note": ours[copied]["note"]}, copied
    archive = [s for s in record.sources if s.id != "iau-csn"]
    assert len(archive) == 3 and all(s.license.spdx == "CC-BY-NC-3.0-IGO" for s in archive)
    for source_id in (bake.ARCHIVE_SOURCE_ID, bake.HIPPARCOS_1997_SOURCE_ID):
        assert ours[source_id]["license"] == tiles["hipparcos-2007"]["license"], source_id
        assert ours[source_id]["credit"] == "ESA", source_id
    assert query.doi == "10.1051/0004-6361:20078357" and "van Leeuwen" in (query.citation or "")
    assert "public.hipparcos_newreduction" in query.url and "public.hipparcos%20" in query.url


def test_the_doc_states_the_epoch_the_parallax_rule_the_licence_and_where_each_comparison_was_read() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    for needle in (
        "J1991.25", "J2016.0", "1000 / p", "CC BY-NC 3.0 IGO", "ESA", "arXiv:1610.06079", "arXiv:1703.10625",
        "Gatewood", "catalog-derived", "scripts/bake_bright_stars_3d.py",
    ):
        assert needle in doc, needle
    assert re.search(r"no distance", doc)


# =============================================================================================
# Real data (the cached archive answer)
# =============================================================================================


@pytest.mark.local_dataset(ARCHIVE_REL)
def test_the_cached_answer_is_the_registered_one_and_a_fresh_bake_matches_the_committed_files() -> None:
    cache = ROOT / ".cache" / "hipparcos-bright-3d"
    bake.check_inputs(cache)
    assert bake.verify_outputs(cache, OUT_DIR, TILES) == []


@pytest.mark.local_dataset(ARCHIVE_REL)
def test_verify_reports_a_table_that_differs_from_a_fresh_bake(tmp_path: Path) -> None:
    for name in ("stars.json", "PROVENANCE.json"):
        (tmp_path / name).write_bytes((OUT_DIR / name).read_bytes())
    text = (tmp_path / "stars.json").read_text(encoding="utf-8")
    (tmp_path / "stars.json").write_text(text.replace('"tilesRelease":"gaia-dr3"', '"tilesRelease":"gaia-dr2"', 1), encoding="utf-8")
    assert bake.verify_outputs(ROOT / ".cache" / "hipparcos-bright-3d", tmp_path, TILES) == [f"{tmp_path / 'stars.json'} differs from a fresh bake"]


@pytest.mark.local_dataset(ARCHIVE_REL)
def test_every_value_is_the_archives() -> None:
    answer = {int(r["hip"]): r for r in csv.DictReader(io.StringIO((ROOT / ARCHIVE_REL).read_text(encoding="utf-8")))}
    rows = _rows_by_hip()
    assert sorted(answer) == sorted(rows)
    pairs = {
        "raDeg": "ra", "decDeg": "dec", "raErrorMas": "e_ra_rad", "decErrorMas": "e_de_rad", "parallaxMas": "plx",
        "parallaxErrorMas": "e_plx", "pmRaCosDecMasPerYr": "pm_ra", "pmDecMasPerYr": "pm_de",
        "pmRaErrorMasPerYr": "e_pm_ra", "pmDecErrorMasPerYr": "e_pm_de", "hpMag": "hp_mag", "vMag": "vmag",
        "bMinusV": "b_v",
    }
    for hip, row in rows.items():
        for name, source in pairs.items():
            text = answer[hip][source]
            assert row[col(name)] == (float(text) if text else None), (hip, name)
        assert row[col("solutionType")] == int(answer[hip]["sn"]) and row[col("components")] == int(answer[hip]["nc"])
