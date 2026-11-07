"""NASA Exoplanet Archive Planetary Systems Composite Parameters table bake (data-6b).

`scripts/bake_exoplanets.py` reads the frozen TAP snapshot `.cache/m2/pscomppars.csv`
(`select * from pscomppars`, retrieved `2026-11-05T00:36:15Z`), validates all 6,451 rows and
1,838 cited references against `services/contracts/exoplanets.py`, and writes
`apps/web/public/catalog/exoplanets/pscomppars.json` and `PROVENANCE.json`.

Synthetic tests pin the HTML reference anchor parser, HTTP-to-HTTPS and relative URL resolution,
scientific-notation limit flags, basis classification, reference deduplication, and refusals on
malformed inputs. Always-run tests check the committed JSON table, summary statistics, the six
benchmark planets shared with `tests/integration/test_exoplanet_contract.py`, the provenance record,
and `docs/science/EXOPLANETS.md`. Real-data tests through `local_dataset` verify the cached CSV
snapshot and byte-identical rebake.
"""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from services.contracts.exoplanets import (
    EXOPLANET_COLUMNS,
    EXOPLANET_IDENTITY_COLUMNS,
    EXOPLANET_VALUE_COLUMNS,
    ExoplanetTable,
)
from services.contracts.provenance import AssetProvenanceRecord
from tests.integration.test_exoplanet_contract import (
    ARCHIVE_ACKNOWLEDGEMENT,
    REFERENCES as CONTRACT_REFERENCES,
    ROWS as CONTRACT_ROWS,
)

ROOT = Path(__file__).resolve().parents[2]
CSV_REL = ".cache/m2/pscomppars.csv"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "exoplanets"
DOC_PATH = ROOT / "docs" / "science" / "EXOPLANETS.md"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_exoplanets", ROOT / "scripts" / "bake_exoplanets.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_exoplanets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


# =============================================================================================
# Synthetic unit tests for reference anchors, limit flags, and CSV parsing
# =============================================================================================


def test_parse_reference_anchor_resolves_https_relative_and_legacy_http_links() -> None:
    assert bake.parse_reference_anchor(
        "<a refstr=CONT_ET_AL_2026 href=https://ui.adsabs.harvard.edu/abs/2026A&A...710A.345C/abstract target=ref>Cont et al. 2026</a>"
    ) == (
        "CONT_ET_AL_2026",
        "Cont et al. 2026",
        "https://ui.adsabs.harvard.edu/abs/2026A&A...710A.345C/abstract",
    )
    assert bake.parse_reference_anchor(
        "<a refstr=CALCULATED_VALUE href=/docs/pscp_calc.html target=_blank>Calculated Value</a>"
    ) == (
        "CALCULATED_VALUE",
        "Calculated Value",
        "https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html",
    )
    assert bake.parse_reference_anchor(
        "<a refstr=KIC___SCPKEY href=http://www.cfa.harvard.edu/kepler/kic/format/format.html target=ref> KIC &amp; SCPKEY </a>"
    ) == (
        "KIC___SCPKEY",
        "KIC & SCPKEY",
        "https://www.cfa.harvard.edu/kepler/kic/format/format.html",
    )
    assert bake.parse_reference_anchor(
        "<a refstr=LUO_ET_AL__2018 href=http://vizier.u-strasbg.fr/viz-bin/VizieR-3?-source=V/153/stellar4 target=ref>Luo et al. 2018</a>"
    ) == (
        "LUO_ET_AL__2018",
        "Luo et al. 2018",
        "https://vizier.u-strasbg.fr/viz-bin/VizieR-3?-source=V/153/stellar4",
    )
    assert bake.parse_reference_anchor(
        "<a refstr=BERTA_THOMPSON_ET_AL__2015 href=http://www.nature.com/nature/journal/v527/n7577/full/nature15762.html target=ref>Berta-Thompson et al. 2015</a>"
    ) == (
        "BERTA_THOMPSON_ET_AL__2015",
        "Berta-Thompson et al. 2015",
        "https://www.nature.com/nature/journal/v527/n7577/full/nature15762.html",
    )


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("not an anchor", "anchor"),
        ("<a refstr=FOO_2020 href=https://example.org/x target=ref>   </a>", "label"),
        ("<a refstr=FOO_2020 href=/unknown/path.html target=ref>Foo 2020</a>", "relative"),
        ("<a refstr=FOO_2020 href=http://unapproved.example.org/x target=ref>Foo 2020</a>", "http"),
        ("<a refstr=FOO_2020 href=ftp://example.org/x target=ref>Foo 2020</a>", "URL"),
    ],
)
def test_parse_reference_anchor_refuses_malformed_or_unapproved_links(raw: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        bake.parse_reference_anchor(raw)


def test_parse_limit_flag_handles_integers_scientific_notation_and_refusals() -> None:
    assert bake.parse_limit_flag("") is None
    assert bake.parse_limit_flag("0") == 0
    assert bake.parse_limit_flag("1") == 1
    assert bake.parse_limit_flag("-1") == -1
    assert bake.parse_limit_flag("0.00000000000000e+00") == 0
    assert bake.parse_limit_flag("1.00000000000000e+00") == 1
    assert bake.parse_limit_flag("-1.00000000000000e+00") == -1
    for bad in ("2", "-2", "0.5", "nan", "inf", "abc"):
        with pytest.raises(ValueError, match="limit"):
            bake.parse_limit_flag(bad)


def _synthetic_csv_text(rows: list[dict[str, str]]) -> str:
    fieldnames = list(bake.REQUIRED_CSV_COLUMNS)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        full = {k: "" for k in fieldnames}
        full.update(row)
        writer.writerow(full)
    return buf.getvalue()


def test_parse_exoplanet_csv_deduplicates_references_and_validates_provenance(tmp_path: Path) -> None:
    calc_anchor = "<a refstr=CALCULATED_VALUE href=/docs/pscp_calc.html target=_blank>Calculated Value</a>"
    paper_a1 = "<a refstr=SMITH_2020 href=https://ui.adsabs.harvard.edu/abs/2020A target=ref>Smith et al. 2020</a>"
    paper_a2 = "<a refstr=SMITH_2020 href=https://ui.adsabs.harvard.edu/abs/2020B target=ref>Smith et al. 2020</a>"
    csv_text = _synthetic_csv_text(
        [
            {
                "pl_name": "Synth-1 b",
                "hostname": "Synth-1",
                "sy_snum": "1",
                "sy_pnum": "2",
                "discoverymethod": "Transit",
                "disc_year": "2020",
                "disc_facility": "TESS",
                "pl_bmassprov": "M-R relationship",
                "pl_orbper": "4.5",
                "pl_orbpererr1": "0.01",
                "pl_orbpererr2": "-0.01",
                "pl_orbperlim": "0",
                "pl_orbper_reflink": paper_a1,
                "pl_rade": "1.2",
                "pl_radeerr1": "0.1",
                "pl_radeerr2": "-0.1",
                "pl_radelim": "0",
                "pl_rade_reflink": paper_a1,
                "pl_bmasse": "1.8",
                "pl_bmasselim": "0.00000000000000e+00",
                "pl_bmasse_reflink": calc_anchor,
                "pl_eqt": "850.0",
                "pl_eqterr1": "20.0",
                "pl_eqterr2": "-20.0",
                "pl_eqtlim": "0",
                "pl_eqt_reflink": calc_anchor,
                "ra": "120.5",
                "raerr1": "1e-07",
                "raerr2": "-1e-07",
                "ra_reflink": paper_a2,
                "dec": "-15.25",
                "decerr1": "1e-07",
                "decerr2": "-1e-07",
                "dec_reflink": paper_a2,
            }
        ]
    )
    parsed = bake.parse_exoplanet_csv(csv_text)
    dumped = parsed.to_contract_dict()
    assert len(dumped["references"]) == 3
    assert [r["url"] for r in dumped["references"]] == [
        "https://ui.adsabs.harvard.edu/abs/2020A",
        "https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html",
        "https://ui.adsabs.harvard.edu/abs/2020B",
    ]
    row = dumped["rows"][0]
    assert row[EXOPLANET_COLUMNS.index("massEarth")] == [1.8, None, None, 0, 1, "modelled"]
    assert row[EXOPLANET_COLUMNS.index("equilibriumTemperatureK")] == [850.0, 20.0, -20.0, 0, 1, "calculated"]
    assert row[EXOPLANET_COLUMNS.index("raDeg")] == [120.5, 1e-07, -1e-07, None, 2, "measured"]

    (tmp_path / bake.OUT_NAME).write_text(bake.dumps_table(parsed), encoding="utf-8")
    prov = bake.build_provenance_record(tmp_path)
    assert [f.path for f in prov.files] == [bake.OUT_NAME]
    assert prov.files[0].sources == ("nasa-exoplanet-archive-pscomppars",)

    with pytest.raises(ValueError, match="missing required"):
        bake.parse_exoplanet_csv("pl_name,hostname\nfoo,bar\n")

    with pytest.raises(ValueError, match="inconsistent labels"):
        bake.parse_exoplanet_csv(
            _synthetic_csv_text(
                [
                    {
                        "pl_name": "Synth-1 b",
                        "hostname": "Synth-1",
                        "sy_snum": "1",
                        "sy_pnum": "1",
                        "discoverymethod": "Transit",
                        "disc_year": "2020",
                        "disc_facility": "TESS",
                        "pl_orbper": "4.5",
                        "pl_orbperlim": "0",
                        "pl_orbper_reflink": "<a refstr=SMITH_2020 href=https://ui.adsabs.harvard.edu/abs/2020A target=ref>Smith 2020</a>",
                        "pl_rade": "1.2",
                        "pl_radelim": "0",
                        "pl_rade_reflink": "<a refstr=SMITH_2020 href=https://ui.adsabs.harvard.edu/abs/2020A target=ref>Smith et al. 2020</a>",
                    }
                ]
            )
        )


# =============================================================================================
# Committed table, summary statistics, benchmark planets, provenance, and reference doc
# =============================================================================================


@pytest.fixture(scope="module")
def committed_table() -> dict[str, Any]:
    return json.loads((OUT_DIR / bake.OUT_NAME).read_text(encoding="utf-8"))


def test_committed_table_validates_against_the_contract(committed_table: dict[str, Any]) -> None:
    parsed = ExoplanetTable.model_validate(committed_table)
    assert parsed.to_contract_dict() == committed_table


def test_committed_counts_and_distributions_match_snapshot(committed_table: dict[str, Any]) -> None:
    rows = committed_table["rows"]
    assert len(rows) == 6451
    assert len(committed_table["references"]) == 1838

    prov_idx = EXOPLANET_COLUMNS.index("massProvenance")
    assert Counter(r[prov_idx] for r in rows) == {
        "M-R relationship": 3035,
        "Mass": 2477,
        "Msini": 924,
        "Msin(i)/sin(i)": 15,
    }

    method_idx = EXOPLANET_COLUMNS.index("discoveryMethod")
    assert Counter(r[method_idx] for r in rows) == {
        "Transit": 4785,
        "Radial Velocity": 1204,
        "Microlensing": 292,
        "Imaging": 98,
        "Transit Timing Variations": 29,
        "Eclipse Timing Variations": 17,
        "Orbital Brightness Modulation": 9,
        "Pulsar Timing": 8,
        "Astrometry": 6,
        "Pulsation Timing Variations": 2,
        "Disk Kinematics": 1,
    }

    eqt_idx = EXOPLANET_COLUMNS.index("equilibriumTemperatureK")
    calc_eqt = [r[eqt_idx] for r in rows if r[eqt_idx] is not None and r[eqt_idx][5] == "calculated"]
    assert len(calc_eqt) == 1090
    assert sum(1 for cell in calc_eqt if cell[1] is not None or cell[2] is not None) == 863


def test_named_planets_match_the_contract_fixture_rows(committed_table: dict[str, Any]) -> None:
    by_name = {r[0]: r for r in committed_table["rows"]}
    refs = committed_table["references"]
    first_val = len(EXOPLANET_IDENTITY_COLUMNS)

    for expected_row in CONTRACT_ROWS:
        name = expected_row[0]
        actual_row = by_name[name]
        assert actual_row[:first_val] == expected_row[:first_val]
        for col_offset, col_name in enumerate(EXOPLANET_VALUE_COLUMNS):
            idx = first_val + col_offset
            exp_cell = expected_row[idx]
            act_cell = actual_row[idx]
            if exp_cell is None:
                assert act_cell is None, f"{name}.{col_name} expected None, got {act_cell}"
            else:
                assert act_cell is not None, f"{name}.{col_name} expected cell, got None"
                assert act_cell[:4] == exp_cell[:4], f"{name}.{col_name} numeric/limit mismatch"
                assert act_cell[5] == exp_cell[5], f"{name}.{col_name} basis mismatch"
                assert refs[act_cell[4]] == CONTRACT_REFERENCES[exp_cell[4]], f"{name}.{col_name} reference mismatch"


def test_provenance_record_and_science_doc_state_sources_terms_and_caveats() -> None:
    record = AssetProvenanceRecord.model_validate_json((OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert [f.path for f in record.files] == [bake.OUT_NAME]
    file_entry = record.files[0]
    assert file_entry.sha256 == hashlib.sha256((OUT_DIR / bake.OUT_NAME).read_bytes()).hexdigest()
    assert file_entry.sources == ("nasa-exoplanet-archive-pscomppars",)
    assert file_entry.produced_by.method == "script"
    assert file_entry.produced_by.script == "scripts/bake_exoplanets.py"
    assert "catalog-derived" in file_entry.note

    assert len(record.sources) == 1
    src = record.sources[0]
    assert src.id == "nasa-exoplanet-archive-pscomppars"
    assert src.doi == "10.26133/NEA13"
    assert src.license.spdx == "LicenseRef-NASA-Exoplanet-Archive-Acknowledgement"
    assert src.acknowledgement == ARCHIVE_ACKNOWLEDGEMENT
    assert src.sha256 == bake.INPUT.sha256
    for required in (
        "2026-11-05T00:36:15Z",
        "select * from pscomppars",
        "not self-consistent",
        "Chen and Kipping (2017, ApJ 834, 17)",
        "Bond albedo",
        "863 of the 1,090",
        "do not report uncertainties",
        "API_PS_columns.html",
        "[open]",
        "10.26133/NEA13",
        "10.26133/NEA12",
        "https://",
    ):
        assert required in src.note or required in src.version or required in src.citation

    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "\u2014" not in doc
    for required in (
        ARCHIVE_ACKNOWLEDGEMENT,
        "Christiansen",
        "10.3847/PSJ/ade3c2",
        "10.26133/NEA13",
        "10.26133/NEA12",
        "2026-11-05T00:36:15Z",
        "select * from pscomppars",
        "Chen and Kipping (2017, ApJ 834, 17",
        "`catalog-derived`",
        "`measured`",
        "`calculated`",
        "`modelled`",
        "863 of the 1,090",
        "do not report uncertainties",
        "API_PS_columns.html",
        "[open]",
        "https://",
    ):
        assert required in doc


def test_registry_agrees_with_the_input_the_exoplanet_bake_pins() -> None:
    entries = {
        entry["path"]: entry
        for entry in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]
    }
    entry = entries[CSV_REL]
    assert (entry["url"], entry["bytes"], entry["sha256"]) == (bake.INPUT.url, bake.INPUT.bytes, bake.INPUT.sha256)


# =============================================================================================
# Real-data checks against .cache/m2/pscomppars.csv
# =============================================================================================


@pytest.mark.local_dataset(CSV_REL)
def test_cached_csv_matches_pins_and_rebake_is_byte_identical() -> None:
    raw = (ROOT / CSV_REL).read_bytes()
    assert len(raw) == bake.INPUT.bytes
    assert hashlib.sha256(raw).hexdigest() == bake.INPUT.sha256

    built = bake.dumps_table(bake.build_exoplanet_table(ROOT / CSV_REL))
    assert built == (OUT_DIR / bake.OUT_NAME).read_text(encoding="utf-8")
    assert bake.verify_outputs(ROOT / ".cache" / "m2", OUT_DIR) == []
