"""Five Millennium Catalogs of Solar and Lunar Eclipses (data-6a).

`scripts/bake_eclipses.py` reads Fred Espenak and Jean Meeus's ASCII catalogues from `.cache/m2/`
(`5MKSEcatalog.txt` and `5MKLEcatalog.txt`), validates every row against `services/contracts/eclipses.py`,
and writes `apps/web/public/catalog/eclipses/solar.json`, `lunar.json` and `PROVENANCE.json`.

Synthetic tests pin the line parsers and their refusals on BCE/negative years, calendar transitions,
blank or `-` fields, and malformed tokens. Always-run tests check the committed JSON tables, NASA's
published summary statistics and individual eclipses, the provenance record, and the reference doc.
Real-data tests through `local_dataset` verify the cached inputs and byte-identical rebake.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from services.contracts.eclipses import (
    LUNAR_ECLIPSE_COLUMNS,
    SOLAR_ECLIPSE_COLUMNS,
    LunarEclipseCatalog,
    SolarEclipseCatalog,
)
from services.contracts.provenance import AssetProvenanceRecord

ROOT = Path(__file__).resolve().parents[2]
SOLAR_REL = ".cache/m2/5MKSEcatalog.txt"
LUNAR_REL = ".cache/m2/5MKLEcatalog.txt"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "eclipses"
DOC_PATH = ROOT / "docs" / "science" / "ECLIPSE_CATALOGS.md"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_eclipses", ROOT / "scripts" / "bake_eclipses.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


# =============================================================================================
# Synthetic line parsing and refusals
# =============================================================================================


def test_parse_solar_line_reads_bce_julian_and_gregorian_rows_with_blank_or_dash_fields() -> None:
    first = "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   T   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s"
    assert bake.parse_solar_line(first) == [
        1, 1, -1999, 6, 12, "julian", 11691, 46438, -49456, 5, "T", None, "-n",
        -0.2701, 1.0733, 6.0, -33.3, 74, 344, 247, 397,
    ]
    partial_blank = "    5  001  -1997 Apr 22  13:19:56  46393 -49433  -13   P   -t  -1.4670  0.1611  60.6S 106.4W   0  281             "
    assert bake.parse_solar_line(partial_blank) == [
        5, 1, -1997, 4, 22, "julian", 47996, 46393, -49433, -13, "P", None, "-t",
        -1.467, 0.1611, -60.6, -106.4, 0, 281, None, None,
    ]
    no_width = "  151  008  -1936 Feb 08  11:24:38  44926 -48681   19   An  t-   0.9793  0.9202  53.9N 167.7E  11  148   -   07m38s"
    assert bake.parse_solar_line(no_width) == [
        151, 8, -1936, 2, 8, "julian", 41078, 44926, -48681, 19, "A", "n", "t-",
        0.9793, 0.9202, 53.9, 167.7, 11, 148, None, 458,
    ]
    non_central_dash = "11896  595   2999 Oct 30  09:34:33   4420  12366  159   A-  -t  -1.0023  0.9586  70.9S  84.7W   0  137   -     -   "
    assert bake.parse_solar_line(non_central_dash) == [
        11896, 595, 2999, 10, 30, "gregorian", 34473, 4420, 12366, 159, "A", "-", "-t",
        -1.0023, 0.9586, -70.9, -84.7, 0, 137, None, None,
    ]


def test_parse_lunar_line_reads_penumbral_partial_and_total_rows() -> None:
    penumbral = "00001  -1999 Jun 26  14:13:28  46437 -49456  017   N   t-  -1.0981  0.8791 -0.1922  268.8    -      -     24S   22W"
    assert bake.parse_lunar_line(penumbral) == [
        1, -1999, 6, 26, "julian", 51208, 46437, -49456, 17, "N", None, "t-",
        -1.0981, 0.8791, -0.1922, 268.8, None, None, -24, -22,
    ]
    total_penumbral = "00025  -1989 Dec 01  17:21:02  46184 -49327  013   Nx  h-  -1.0024  1.0436 -0.0058  271.9    -      -     18N   69W"
    assert bake.parse_lunar_line(total_penumbral) == [
        25, -1989, 12, 1, "julian", 62462, 46184, -49327, 13, "N", "x", "h-",
        -1.0024, 1.0436, -0.0058, 271.9, None, None, 18, -69,
    ]
    total = "09701   2022 Nov 08  11:00:22     73    282  136   T+  p-   0.2570  2.4143  1.3589  353.9  219.8   85.0   17N  169W"
    assert bake.parse_lunar_line(total) == [
        9701, 2022, 11, 8, "gregorian", 39622, 73, 282, 136, "T", "+", "p-",
        0.257, 2.4143, 1.3589, 353.9, 219.8, 85.0, 17, -169,
    ]


@pytest.mark.parametrize(
    ("kind", "line", "match"),
    [
        (
            "solar",
            "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   X   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s",
            "eclipse type",
        ),
        (
            "solar",
            "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   Tz  -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s",
            "eclipse type",
        ),
        (
            "solar",
            "    1  001   1582 Oct 10  03:14:51    128  -5160  120   T   nn  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s",
            "calendar",
        ),
        (
            "solar",
            "    1  001  -1999 Foo 12  03:14:51  46438 -49456    5   T   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s",
            "month",
        ),
        (
            "solar",
            "    1  001  -1999 Jun 12  24:00:00  46438 -49456    5   T   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s",
            "time",
        ),
        (
            "solar",
            "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   T   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m60s",
            "duration",
        ),
        (
            "solar",
            "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   T   -n  -0.2701  1.0733   6.0X  33.3W  74  344  247  06m37s",
            "coordinate",
        ),
        (
            "lunar",
            "00001  -1999 Jun 26  14:13:28  46437 -49456  017   Q   t-  -1.0981  0.8791 -0.1922  268.8    -      -     24S   22W",
            "eclipse type",
        ),
        (
            "lunar",
            "00001  -1999 Jun 26  14:13:28  46437 -49456  017   N*  t-  -1.0981  0.8791 -0.1922  268.8    -      -     24S   22W",
            "eclipse type",
        ),
        (
            "lunar",
            "00001  -1999 Jun 26  14:13:28  46437 -49456  017   N   t-  -1.0981  0.8791 -0.1922  268.8    -      -     24E   22W",
            "coordinate",
        ),
    ],
)
def test_parsers_refuse_malformed_lines(kind: str, line: str, match: str) -> None:
    parser = bake.parse_solar_line if kind == "solar" else bake.parse_lunar_line
    with pytest.raises(ValueError, match=match):
        parser(line)


def test_catalog_parsers_refuse_wrong_headers_and_empty_tables(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="header"):
        bake.parse_solar_catalog("Wrong title\n")
    with pytest.raises(ValueError, match="header"):
        bake.parse_lunar_catalog("Wrong title\n")

    solar_header = (
        "Title: Five Millennium Catalog of Solar Eclipses: -1999 to +3000 (2000 BCE to 3000CE)\n"
        "Authors: Fred Espenak and Jean Meeus\n"
        "Source: NASA Technical Publication NASA/TP-2008-214170\n"
        "Date: 2008 Oct 07\n\n\n\n"
        "  Cat  Canon    Calendar   Greatest          Luna  Saros Ecl.               Sun  Path Central\n"
        "  Num  Plate      Date      Eclipse     DT   Num    Num  Type QLE  Gamma    Mag.   Lat.   Long. Alt  Azm Width   Dur.\n"
        "                                      \xa1      \xa1   \xa1    \xa1    km\n"
    )
    lunar_header = (
        "Title: Five Millennium Catalog of Lunar Eclipses: -1999 to +3000 (2000 BCE to 3000CE)\n"
        "Authors: Fred Espenak and Jean Meeus\n"
        "Source: NASA Technical Publication NASA/TP-2009-214173\n"
        "Date: 2011 May 23\n" + "\n" * 9
    )
    solar_row = "    1  001  -1999 Jun 12  03:14:51  46438 -49456    5   T   -n  -0.2701  1.0733   6.0N  33.3W  74  344  247  06m37s\n"
    lunar_row = "00001  -1999 Jun 26  14:13:28  46437 -49456  017   N   t-  -1.0981  0.8791 -0.1922  268.8    -      -     24S   22W\n"
    solar_cat = bake.parse_solar_catalog(solar_header + solar_row)
    lunar_cat = bake.parse_lunar_catalog(lunar_header + lunar_row)
    (tmp_path / bake.SOLAR_OUT_NAME).write_text(bake.dumps_catalog(solar_cat), encoding="utf-8")
    (tmp_path / bake.LUNAR_OUT_NAME).write_text(bake.dumps_catalog(lunar_cat), encoding="utf-8")
    prov = bake.build_provenance_record(tmp_path)
    assert {f.path: f.sources for f in prov.files} == {
        bake.SOLAR_OUT_NAME: ("nasa-gsfc-5mkse",),
        bake.LUNAR_OUT_NAME: ("nasa-gsfc-5mkle",),
    }


# =============================================================================================
# Committed catalogues, NASA's published statistics and single eclipses
# =============================================================================================


@pytest.fixture(scope="module")
def committed_catalogs() -> tuple[dict, dict]:
    solar = json.loads((OUT_DIR / bake.SOLAR_OUT_NAME).read_text(encoding="utf-8"))
    lunar = json.loads((OUT_DIR / bake.LUNAR_OUT_NAME).read_text(encoding="utf-8"))
    return solar, lunar


def test_committed_catalogs_validate_against_the_contract(committed_catalogs: tuple[dict, dict]) -> None:
    solar, lunar = committed_catalogs
    parsed_solar = SolarEclipseCatalog.model_validate(solar)
    parsed_lunar = LunarEclipseCatalog.model_validate(lunar)
    assert parsed_solar.to_contract_dict() == solar
    assert parsed_lunar.to_contract_dict() == lunar


def test_committed_counts_and_type_distributions_match_nasa_publications(committed_catalogs: tuple[dict, dict]) -> None:
    # Verified against NASA's Five Millennium Catalog pages (5MKSE.html, SEcatalog.html, 5MKLE.html)
    solar, lunar = committed_catalogs
    assert len(solar["rows"]) == 11_898
    assert len(lunar["rows"]) == 12_064

    solar_type_idx = SOLAR_ECLIPSE_COLUMNS.index("eclipseType")
    solar_qual_idx = SOLAR_ECLIPSE_COLUMNS.index("typeQualifier")
    solar_types = Counter(row[solar_type_idx] for row in solar["rows"])
    assert solar_types == {"P": 4200, "A": 3956, "T": 3173, "H": 569}

    annular_quals = Counter(row[solar_qual_idx] for row in solar["rows"] if row[solar_type_idx] == "A")
    assert annular_quals["n"] + annular_quals["s"] == 61
    assert annular_quals["+"] + annular_quals["-"] == 68
    assert 3956 - 61 - 68 == 3827

    total_quals = Counter(row[solar_qual_idx] for row in solar["rows"] if row[solar_type_idx] == "T")
    assert total_quals["n"] + total_quals["s"] == 26
    assert total_quals["+"] + total_quals["-"] == 26
    assert 3173 - 26 - 26 == 3121

    lunar_type_idx = LUNAR_ECLIPSE_COLUMNS.index("eclipseType")
    lunar_qual_idx = LUNAR_ECLIPSE_COLUMNS.index("typeQualifier")
    lunar_types = Counter(row[lunar_type_idx] for row in lunar["rows"])
    assert lunar_types == {"N": 4378, "P": 4207, "T": 3479}
    assert sum(1 for row in lunar["rows"] if row[lunar_type_idx] == "N" and row[lunar_qual_idx] == "x") == 141


def test_single_eclipses_and_extremes_match_nasa_tables(committed_catalogs: tuple[dict, dict]) -> None:
    solar, lunar = committed_catalogs
    solar_by_date = {(r[2], r[3], r[4]): dict(zip(SOLAR_ECLIPSE_COLUMNS, r, strict=True)) for r in solar["rows"]}
    lunar_by_date = {(r[1], r[2], r[3]): dict(zip(LUNAR_ECLIPSE_COLUMNS, r, strict=True)) for r in lunar["rows"]}

    e2017 = solar_by_date[(2017, 8, 21)]
    assert e2017 == {
        "catalogNumber": 9546,
        "canonPlate": 478,
        "year": 2017,
        "month": 8,
        "day": 21,
        "calendar": "gregorian",
        "greatestEclipseTdSeconds": 18 * 3600 + 26 * 60 + 40,
        "deltaTSeconds": 70,
        "lunationNumber": 218,
        "sarosNumber": 145,
        "eclipseType": "T",
        "typeQualifier": None,
        "quincenaLunarEclipse": "p-",
        "gamma": 0.4367,
        "magnitude": 1.0306,
        "latitudeDeg": 37.0,
        "longitudeDeg": -87.7,
        "sunAltitudeDeg": 64,
        "sunAzimuthDeg": 198,
        "pathWidthKm": 115,
        "centralDurationSeconds": 2 * 60 + 40,
    }

    e2024 = solar_by_date[(2024, 4, 8)]
    assert e2024 == {
        "catalogNumber": 9561,
        "canonPlate": 479,
        "year": 2024,
        "month": 4,
        "day": 8,
        "calendar": "gregorian",
        "greatestEclipseTdSeconds": 18 * 3600 + 18 * 60 + 29,
        "deltaTSeconds": 74,
        "lunationNumber": 300,
        "sarosNumber": 139,
        "eclipseType": "T",
        "typeQualifier": None,
        "quincenaLunarEclipse": "n-",
        "gamma": 0.3431,
        "magnitude": 1.0566,
        "latitudeDeg": 25.3,
        "longitudeDeg": -104.1,
        "sunAltitudeDeg": 70,
        "sunAzimuthDeg": 149,
        "pathWidthKm": 198,
        "centralDurationSeconds": 4 * 60 + 28,
    }

    l2022 = lunar_by_date[(2022, 11, 8)]
    assert l2022 == {
        "catalogNumber": 9701,
        "year": 2022,
        "month": 11,
        "day": 8,
        "calendar": "gregorian",
        "greatestEclipseTdSeconds": 11 * 3600 + 0 * 60 + 22,
        "deltaTSeconds": 73,
        "lunationNumber": 282,
        "sarosNumber": 136,
        "eclipseType": "T",
        "typeQualifier": "+",
        "quincenaSolarEclipse": "p-",
        "gamma": 0.257,
        "penumbralMagnitude": 2.4143,
        "umbralMagnitude": 1.3589,
        "penumbralDurationMinutes": 353.9,
        "partialDurationMinutes": 219.8,
        "totalDurationMinutes": 85.0,
        "zenithLatitudeDeg": 17,
        "zenithLongitudeDeg": -169,
    }

    # Extreme durations and five-eclipse years from SEcatalog.html
    assert solar_by_date[(2186, 7, 16)]["centralDurationSeconds"] == 7 * 60 + 29
    assert solar_by_date[(150, 12, 7)]["centralDurationSeconds"] == 12 * 60 + 23
    by_year = Counter(r[2] for r in solar["rows"])
    five_eclipse_years = sorted(year for year, count in by_year.items() if count == 5)
    assert five_eclipse_years == [
        -1852, -1805, -1787, -1740, -1675, -1154, -1089, -568, -503, -438, -373,
        18, 83, 148, 604, 669, 734, 1255, 1805, 1935, 2206, 2709, 2774, 2839, 2904,
    ]


def test_provenance_record_and_science_doc_state_sources_terms_and_caveats() -> None:
    record = AssetProvenanceRecord.model_validate_json((OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
    by_path = {entry.path: entry for entry in record.files}
    assert set(by_path) == {bake.SOLAR_OUT_NAME, bake.LUNAR_OUT_NAME}
    for name, source_id in ((bake.SOLAR_OUT_NAME, "nasa-gsfc-5mkse"), (bake.LUNAR_OUT_NAME, "nasa-gsfc-5mkle")):
        entry = by_path[name]
        assert entry.sha256 == hashlib.sha256((OUT_DIR / name).read_bytes()).hexdigest()
        assert entry.sources == (source_id,)
        assert entry.produced_by.method == "script" and entry.produced_by.script == "scripts/bake_eclipses.py"
        assert "predicted" in entry.note

    sources = {source.id: source for source in record.sources}
    solar_src = sources["nasa-gsfc-5mkse"]
    lunar_src = sources["nasa-gsfc-5mkle"]
    for src, key in ((solar_src, "solar"), (lunar_src, "lunar")):
        assert src.license.spdx == "LicenseRef-NASA-Eclipse-Permission"
        assert src.credit == "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"
        assert src.acknowledgement == "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"
        assert src.sha256 == bake.INPUTS[key].sha256
        assert "Morrison and Stephenson (2004)" in src.note
    assert "NASA/TP-2008-214170" in solar_src.note and "NASA/TP-2009-214174" in solar_src.note
    assert "A-56 to A-150" in solar_src.note
    assert "NASA/TP-2009-214173" in lunar_src.citation

    doc = DOC_PATH.read_text(encoding="utf-8")
    for required in (
        "Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)",
        "NASA/TP-2008-214170",
        "NASA/TP-2009-214174",
        "NASA/TP-2009-214173",
        "Morrison and Stephenson (2004)",
        "1582 October 4",
        "1582 October 15",
        "`predicted`",
        "A-56 to A-150",
    ):
        assert required in doc


def test_registry_agrees_with_the_inputs_the_eclipse_bake_pins() -> None:
    entries = {entry["path"]: entry for entry in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}
    for source in bake.INPUTS.values():
        entry = entries[f".cache/m2/{source.filename}"]
        assert (entry["url"], entry["bytes"], entry["sha256"]) == (source.url, source.bytes, source.sha256)


# =============================================================================================
# Real-data checks against .cache/m2/
# =============================================================================================


@pytest.mark.local_dataset(SOLAR_REL, LUNAR_REL)
def test_cached_downloads_match_pins_and_rebake_is_byte_identical() -> None:
    for rel, key in ((SOLAR_REL, "solar"), (LUNAR_REL, "lunar")):
        raw = (ROOT / rel).read_bytes()
        assert len(raw) == bake.INPUTS[key].bytes
        assert hashlib.sha256(raw).hexdigest() == bake.INPUTS[key].sha256

    solar_built = bake.dumps_catalog(bake.build_solar_catalog(ROOT / SOLAR_REL))
    lunar_built = bake.dumps_catalog(bake.build_lunar_catalog(ROOT / LUNAR_REL))
    assert solar_built == (OUT_DIR / bake.SOLAR_OUT_NAME).read_text(encoding="utf-8")
    assert lunar_built == (OUT_DIR / bake.LUNAR_OUT_NAME).read_text(encoding="utf-8")
    assert bake.verify_outputs(ROOT / ".cache" / "m2", OUT_DIR) == []
