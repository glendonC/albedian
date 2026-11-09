"""OpenNGC deep-sky catalogue bake (data-7a): parsing, the committed table, its record, and real-data checks.

`scripts/bake_deepsky_openngc.py` writes `apps/web/public/catalog/deepsky/openngc.json` and its `PROVENANCE.json`
from OpenNGC v20260501 (github.com/mattiaverga/OpenNGC, CC-BY-SA-4.0, credit Mattia Verga), cached in `.cache/m2/`
and registered in `data/downloads.json`. The southern-sky audit (data-7) found no deep-sky table baked at all; these
tests pin the southern showpieces it named, cross-check OpenNGC's globular-cluster centres against the committed
Gaia DR3 G 12 star tiles, and rebake the table byte for byte where the CSVs are cached.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from services.contracts.deep_sky import DEEP_SKY_COLUMNS, DEEP_SKY_CONSTELLATIONS, DEEP_SKY_OBJECT_TYPES, DeepSkyCatalog
from services.contracts.provenance import AssetProvenanceRecord
from services.science.catalog_tiles import decode_core_directions, decode_core_tile, radec_to_vec
from tests.integration.test_deep_sky_contract import ROWS as CONTRACT_ROWS
from tests.integration.test_deep_sky_contract import TYPE_LABELS

ROOT = Path(__file__).resolve().parents[2]
NGC_REL = ".cache/m2/openngc_v20260501_NGC.csv"
ADDENDUM_REL = ".cache/m2/openngc_v20260501_addendum.csv"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "deepsky"
DOC_PATH = ROOT / "docs" / "science" / "DEEP_SKY_CATALOG.md"
TILES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_deepsky_openngc", ROOT / "scripts" / "bake_deepsky_openngc.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_deepsky_openngc.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


def col(name: str) -> int:
    return DEEP_SKY_COLUMNS.index(name)


# =============================================================================================
# Parsing
# =============================================================================================


def test_sexagesimal_positions_convert_to_degrees() -> None:
    # 47 Tucanae as OpenNGC v20260501 writes it
    assert bake.sexagesimal_deg("00:24:05.36", hours=True) == pytest.approx((24 + 5.36 / 60) / 60 * 15, abs=1e-6)
    assert bake.sexagesimal_deg("-72:04:53.2", hours=False) == pytest.approx(-(72 + 4 / 60 + 53.2 / 3600), abs=1e-6)
    assert bake.sexagesimal_deg("-00:30:00", hours=False) == pytest.approx(-0.5, abs=1e-9)
    assert bake.sexagesimal_deg("+05:00:00", hours=False) == pytest.approx(5.0, abs=1e-9)
    for bad in ("12:60:00", "12:00:60", "12:00", "north"):
        with pytest.raises(ValueError):
            bake.sexagesimal_deg(bad, hours=False)


def test_list_cells_split_at_commas_and_drop_blank_items() -> None:
    assert bake.list_items("") == []
    assert bake.list_items("C 092") == ["C 092"]
    assert bake.list_items("Carina Nebula,eta Car Nebula") == ["Carina Nebula", "eta Car Nebula"]
    assert bake.list_items(" PGC 1, ,UGC 2 ,") == ["PGC 1", "UGC 2"]


def _csv(*lines: str) -> str:
    return bake.OPENNGC_HEADER + "\n" + "\n".join(lines) + "\n"


def test_parse_keeps_blanks_null_and_refuses_another_header_or_row_count() -> None:
    nonex = "IC1064;NonEx" + ";" * 30
    rows = bake.parse_openngc_csv(_csv(nonex), expected_rows=1)
    assert rows == [["IC1064", "NonEx", None, None, None, None, None, None, None, None, None, None, [], [], [], []]]
    with pytest.raises(ValueError, match="header"):
        bake.parse_openngc_csv(bake.OPENNGC_HEADER.replace("Common names", "Names") + "\n" + nonex + "\n")
    with pytest.raises(ValueError, match="expected 2 rows"):
        bake.parse_openngc_csv(_csv(nonex), expected_rows=2)


def test_check_inputs_refuses_a_missing_or_altered_csv(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        bake.check_inputs(tmp_path)
    for item in bake.INPUTS:
        (tmp_path / item.filename).write_bytes(b"x" * item.bytes)
    with pytest.raises(SystemExit, match="sha256"):
        bake.check_inputs(tmp_path)
    (tmp_path / bake.INPUTS[0].filename).write_bytes(b"x")
    with pytest.raises(SystemExit, match="bytes"):
        bake.check_inputs(tmp_path)


# =============================================================================================
# The committed table
# =============================================================================================


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads((OUT_DIR / bake.OUT_NAME).read_text(encoding="utf-8"))


def test_committed_table_validates_and_is_one_line(committed: dict[str, Any]) -> None:
    parsed = DeepSkyCatalog.model_validate(committed)
    assert parsed.to_contract_dict() == committed
    assert (OUT_DIR / bake.OUT_NAME).read_text(encoding="utf-8").count("\n") == 1
    assert committed["release"] == "OpenNGC v20260501"
    assert committed["types"] == TYPE_LABELS


def test_committed_counts_match_openngc_v20260501(committed: dict[str, Any]) -> None:
    rows = committed["rows"]
    assert len(rows) == 13_969 + 64
    types = Counter(row[col("type")] for row in rows)
    assert types["G"] == 10_521 and types["OCl"] == 663 and types["GCl"] == 208 and types["Dup"] == 652
    assert set(types) == set(DEEP_SKY_OBJECT_TYPES)
    assert sum(1 for row in rows if row[col("raDeg")] is None) == 7
    assert {row[col("constellation")] for row in rows} - {None} == set(DEEP_SKY_CONSTELLATIONS)
    # OpenNGC gives 102 to no row: its M102 row is a duplicate (Dup) of M101 and carries 101
    messier = Counter(row[col("messier")] for row in rows if row[col("messier")] is not None)
    assert sum(messier.values()) == 110 and set(messier) == set(range(1, 111)) - {102}
    assert sorted((row[0], row[col("type")]) for row in rows if row[col("messier")] == 101) == [("M102", "Dup"), ("NGC5457", "G")]


def test_every_duplicate_points_at_rows_that_exist_and_at_a_master(committed: dict[str, Any]) -> None:
    """The contract checks that a duplicate names a target; here every target resolves to a row of the table.

    Measured on v20260501: all 652 duplicates resolve, and each names at least one row that is not itself a duplicate,
    so one redirect reaches the object. Three point at another duplicate as well (IC0843 and IC4088 at each other,
    NGC5620 at IC1005), so a parser that follows redirects must prefer the target that is not a duplicate.
    """
    rows = committed["rows"]
    by_name = {row[0]: row for row in rows}
    messier_owner = {row[col("messier")]: row[0] for row in rows if row[col("messier")] is not None and row[col("type")] != "Dup"}
    duplicates = [row for row in rows if row[col("type")] == "Dup"]
    assert len(duplicates) == 652
    dup_to_dup = []
    for row in duplicates:
        targets = [f"NGC{x}" for x in row[col("ngc")]] + [f"IC{x}" for x in row[col("ic")]]
        if row[col("messier")] is not None:
            assert row[col("messier")] in messier_owner, row[0]
            targets.append(messier_owner[row[col("messier")]])
        missing = [t for t in targets if t not in by_name]
        assert targets and not missing, (row[0], missing)
        assert any(by_name[t][col("type")] != "Dup" for t in targets), row[0]
        dup_to_dup += [(row[0], t) for t in targets if by_name[t][col("type")] == "Dup"]
    assert sorted(dup_to_dup) == [("IC0843", "IC4088"), ("IC4088", "IC0843"), ("NGC5620", "IC1005")]


def test_contract_fixture_rows_are_the_committed_rows(committed: dict[str, Any]) -> None:
    """The 16 real rows the contract tests hold, among them every southern showpiece of the data-7 audit."""
    by_name = {row[0]: row for row in committed["rows"]}
    for row in CONTRACT_ROWS:
        assert by_name[row[0]] == row


@pytest.mark.parametrize(
    ("name", "common"),
    [
        ("ESO056-115", "Large Magellanic Cloud"),
        ("NGC0292", "Small Magellanic Cloud"),
        ("NGC3372", "Carina Nebula"),
        ("NGC5139", "Omega Centauri"),
        ("NGC0104", "47 Tuc Cluster"),
        ("NGC4755", "Herschel's Jewel Box"),
        ("C099", "Coalsack Nebula"),
    ],
)
def test_southern_showpieces_carry_their_common_names(committed: dict[str, Any], name: str, common: str) -> None:
    row = next(row for row in committed["rows"] if row[0] == name)
    assert common in row[col("commonNames")]
    assert row[col("decDeg")] < -45


def _tile_stars() -> np.ndarray:
    vectors = []
    for path in sorted(TILES.glob("L*/*.core.bin")):
        header, cols = decode_core_tile(path.read_bytes())
        if header.count:
            vectors.append(decode_core_directions(header, cols))
    return np.concatenate(vectors)


@pytest.mark.parametrize("name", ["NGC5139", "NGC0104"])
def test_globular_cluster_centres_sit_on_gaia_overdensities(committed: dict[str, Any], name: str) -> None:
    """Two independent catalogues agree: OpenNGC's centre of each cluster is where the G 12 tiles crowd.

    The density of tile stars within 15 arcmin of the centre is at least five times the density 30 to 60 arcmin
    out (measured on the committed tiles: Omega Centauri 310 stars within 15 arcmin, 47 Tucanae 195).
    """
    row = next(row for row in committed["rows"] if row[0] == name)
    centre = radec_to_vec(np.array([row[col("raDeg")]]), np.array([row[col("decDeg")]]))[0]
    sep = np.degrees(np.arccos(np.clip(_tile_stars() @ centre, -1.0, 1.0)))

    def density(inner: float, outer: float) -> float:
        area = 2 * np.pi * (np.cos(np.radians(inner)) - np.cos(np.radians(outer)))
        return float(np.count_nonzero((sep >= inner) & (sep < outer))) / area

    assert density(0.0, 0.25) > 5 * density(0.5, 1.0)


# =============================================================================================
# Record, doc and registry
# =============================================================================================


def test_provenance_record_states_sources_licence_and_changes() -> None:
    record = AssetProvenanceRecord.model_validate_json((OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert [f.path for f in record.files] == [bake.OUT_NAME]
    entry = record.files[0]
    assert entry.sha256 == hashlib.sha256((OUT_DIR / bake.OUT_NAME).read_bytes()).hexdigest()
    assert entry.sources == tuple(item.source_id for item in bake.INPUTS)
    assert entry.produced_by.script == "scripts/bake_deepsky_openngc.py"
    for required in ("catalog-derived", "CC-BY-SA-4.0", "sexagesimal"):
        assert required in entry.note
    assert [s.id for s in record.sources] == [item.source_id for item in bake.INPUTS]
    for src, item in zip(record.sources, bake.INPUTS, strict=True):
        assert src.sha256 == item.sha256
        assert src.license.spdx == "CC-BY-SA-4.0"
        assert "Mattia Verga" in src.credit
        assert "shared under CC-BY-SA-4.0" in (src.license.conditions or "")
        assert str(src.url) == f"https://raw.githubusercontent.com/mattiaverga/OpenNGC/v20260501/{item.upstream}"


def test_science_doc_states_licence_fields_and_limits() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "\u2014" not in doc  # no em dash characters in project prose
    for required in ("OpenNGC v20260501", "Mattia Verga", "CC-BY-SA-4.0", "share", "`catalog-derived`", "J2000",
                     "Se1", "M102", "NonEx", "14,033"):  # fmt: skip
        assert required in doc


def test_registry_agrees_with_the_inputs_the_bake_pins() -> None:
    entries = {e["path"]: e for e in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}
    for item in bake.INPUTS:
        entry = entries[f".cache/m2/{item.filename}"]
        assert (entry["url"], entry["bytes"], entry["sha256"], entry["licence"]) == (
            f"https://raw.githubusercontent.com/mattiaverga/OpenNGC/v20260501/{item.upstream}",
            item.bytes,
            item.sha256,
            "CC-BY-SA-4.0",
        )


# =============================================================================================
# Real data: the cached CSVs
# =============================================================================================


@pytest.mark.local_dataset(NGC_REL, ADDENDUM_REL)
def test_cached_csvs_match_pins_and_rebake_is_byte_identical() -> None:
    """Byte comparison is platform-stable here: the bake only adds, multiplies, divides and rounds (each correctly
    rounded under IEEE 754 on every platform), with no trigonometry, exp or noise."""
    bake.check_inputs(ROOT / ".cache" / "m2")
    # The list cells the bake tidies: 16 Identifiers items with surrounding blanks, 1 empty item, nowhere else
    tidied = Counter()
    for item in bake.INPUTS:
        text = (ROOT / ".cache" / "m2" / item.filename).read_text(encoding="utf-8")
        for row in csv.DictReader(io.StringIO(text, newline=""), delimiter=";"):
            for name in ("NGC", "IC", "Identifiers", "Common names"):
                for part in row[name].split(",") if row[name] else []:
                    if not part.strip():
                        tidied[(name, "empty")] += 1
                    elif part != part.strip():
                        tidied[(name, "padded")] += 1
    assert tidied == {("Identifiers", "padded"): 16, ("Identifiers", "empty"): 1}
    built = bake.dumps_catalog(bake.build_catalog(ROOT / ".cache" / "m2"))
    assert built == (OUT_DIR / bake.OUT_NAME).read_text(encoding="utf-8")
    assert bake.verify_outputs(ROOT / ".cache" / "m2", OUT_DIR) == []
