"""Deep-sky catalogue contract (data-7a0): Pydantic rules, a property, and the fixture shared with the Zod schema.

`tests/fixtures/deep_sky_contract.json` is written from `build_deep_sky_samples()` and parsed by
packages/contracts/src/__tests__/deep-sky.test.ts, so both sides accept and refuse the same catalogues. Regenerate it
after a deliberate contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_deep_sky_contract.py

The valid sample holds 16 real rows of OpenNGC v20260501 (database_files/NGC.csv and addendum.csv at
github.com/mattiaverga/OpenNGC, CC BY-SA, credit Mattia Verga), turned into the contract's tuples by a script and
kept here as literals: the Large and Small Magellanic Clouds, the Carina Nebula, Omega Centauri, 47 Tucanae, the
Jewel Box, the Coalsack, the Andromeda Galaxy, the Orion Nebula, the Pinwheel Galaxy and the duplicate row that
repeats its Messier number, a globular cluster in Serpens Caput, a nonexistent object with no position, and
duplicates that point through the NGC and IC columns. The type labels are OpenNGC's own (NGC_guide.txt). Every
invalid sample breaks one rule of it.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import (
    DEEP_SKY_CATALOG_FORMAT,
    DEEP_SKY_CATALOG_FORMAT_VERSION,
    DEEP_SKY_COLUMNS,
    DeepSkyCatalog,
)
from services.contracts import deep_sky as ds

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "deep_sky_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
RELEASE = "OpenNGC v20260501"

# OpenNGC v20260501 NGC_guide.txt, "Type: Object type"
TYPE_LABELS = {
    "*": "Star",
    "**": "Double star",
    "*Ass": "Association of stars",
    "OCl": "Open Cluster",
    "GCl": "Globular Cluster",
    "Cl+N": "Star cluster + Nebula",
    "G": "Galaxy",
    "GPair": "Galaxy Pair",
    "GTrpl": "Galaxy Triplet",
    "GGroup": "Group of galaxies",
    "PN": "Planetary Nebula",
    "HII": "HII Ionized region",
    "DrkN": "Dark Nebula",
    "EmN": "Emission Nebula",
    "Neb": "Nebula",
    "RfN": "Reflection Nebula",
    "SNR": "Supernova remnant",
    "Nova": "Nova star",
    "NonEx": "Nonexistent object",
    "Dup": "Duplicated object (see NGC or IC columns to find the master object)",
    "Other": "Other classification (see object notes)",
}

# name, type, raDeg, decDeg, constellation, majorAxisArcmin, minorAxisArcmin, positionAngleDeg, bMag, vMag,
# surfaceBrightness, messier, ngc, ic, identifiers, commonNames
ROWS: list[list[Any]] = [
    ["ESO056-115", "G", 80.89375, -69.756111, "Dor", 646.0, 550.0, 170.0, 0.8, 0.29, 23.45, None, [], [], ["ESO-LV 0561150", "IRAS 05240-6948", "PGC 017223"], ["Large Magellanic Cloud", "Nubecula Major"]],
    ["NGC0292", "G", 13.186583, -72.828611, "Tuc", 299.92, 179.89, 45.0, 2.75, 2.3, 23.42, None, [], [], ["ESO 029-021", "PGC 003085"], ["Small Magellanic Cloud"]],
    ["NGC3372", "HII", 161.285542, -59.866694, "Car", 120.0, 120.0, None, 3.0, None, None, None, [], [], ["C 092"], ["Carina Nebula", "eta Car Nebula"]],
    ["NGC5139", "GCl", 201.691208, -47.476861, "Cen", 27.0, None, None, 6.12, 5.33, None, None, [], [], ["C 080", "MWSC 2120"], ["Omega Centauri"]],
    ["NGC0104", "GCl", 6.022333, -72.081444, "Tuc", 31.8, None, None, 5.78, 4.09, None, None, [], [], ["2MASX J00240535-7204531", "C 106", "MWSC 0038"], ["47 Tuc Cluster"]],
    ["NGC4755", "OCl", 193.4045, -60.356306, "Cru", 7.8, None, None, None, None, None, None, [], [], ["C 094", "MWSC 2072"], ["Herschel's Jewel Box", "kappa Crucis Cluster"]],
    ["C099", "DrkN", 187.829167, -63.743333, "Cru", None, None, None, None, None, None, None, [], [], [], ["Coalsack Nebula"]],
    ["NGC0224", "G", 10.684792, 41.269056, "And", 177.83, 69.66, 35.0, 4.29, 3.44, 23.63, 31, [], [], ["2MASX J00424433+4116074", "IRAS 00400+4059", "MCG +07-02-016", "PGC 002557", "UGC 00454"], ["Andromeda Galaxy"]],
    ["NGC1976", "Cl+N", 83.818667, -5.389667, "Ori", 90.0, 60.0, None, 4.0, 4.0, None, 42, [], [], ["LBN 974", "MWSC 0582"], ["Great Orion Nebula", "Orion Nebula"]],
    ["NGC5457", "G", 210.80225, 54.348944, "UMa", 23.99, 23.07, 28.0, 8.36, 7.9, 23.97, 101, [], [], ["IRAS 14013+5435", "MCG +09-23-028", "PGC 050063", "SDSS J140312.52+542056.2", "UGC 08981"], []],
    ["M102", "Dup", 210.80225, 54.348944, "UMa", None, None, None, None, None, None, 101, [], [], [], []],
    ["NGC5904", "GCl", 229.640625, 2.082694, "Se1", 15.0, None, None, 7.34, 5.95, None, 5, [], [], ["MWSC 2286"], []],
    ["IC1064", "NonEx", None, None, None, None, None, None, None, None, None, None, [], [], [], []],
    ["IC0011", "Dup", 13.247292, 56.621889, "Cas", None, None, None, None, None, None, None, ["0281"], [], [], []],
    ["IC0555", "Dup", 145.487042, 12.296278, "Leo", None, None, None, None, None, None, None, [], ["0554"], [], []],
    ["NGC0281", "HII", 13.247292, 56.621889, "Cas", 35.0, 30.0, None, None, None, None, None, [], ["0011"], ["LBN 616"], []],
]  # fmt: skip


def catalog(rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "format": DEEP_SKY_CATALOG_FORMAT,
        "formatVersion": DEEP_SKY_CATALOG_FORMAT_VERSION,
        "evidenceKind": ds.DEEP_SKY_EVIDENCE_KIND,
        "release": RELEASE,
        "types": dict(TYPE_LABELS),
        "columns": list(DEEP_SKY_COLUMNS),
        "rows": copy.deepcopy(rows),
    }


def col(name: str) -> int:
    return DEEP_SKY_COLUMNS.index(name)


def put(t: dict[str, Any], **values: Any) -> None:
    """Set columns of the first row."""
    for name, value in values.items():
        t["rows"][0][col(name)] = value


def repeat_messier(t: dict[str, Any]) -> None:
    """Give the first two rows the same Messier number, both as galaxies (not duplicates)."""
    for row in t["rows"][:2]:
        row[col("type")], row[col("raDeg")], row[col("decDeg")], row[col("constellation")] = "G", 10.0, 0.0, "Psc"
        row[col("messier")] = 31


# Each fault breaks a rule of any valid catalogue of at least two rows
FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "format": lambda t: t.update(format="albedian.deep-sky-list"),
    "formatVersion": lambda t: t.update(formatVersion=2),
    "evidence kind": lambda t: t.update(evidenceKind="observed"),
    "extra field": lambda t: t.update(extra=1),
    "blank-padded release": lambda t: t.update(release=" OpenNGC"),
    "column renamed": lambda t: t["columns"].__setitem__(0, "id"),
    "no rows": lambda t: t.update(rows=[]),
    "short row": lambda t: t["rows"][0].pop(),
    "long row": lambda t: t["rows"][0].append(None),
    "type label missing": lambda t: t["types"].pop("Dup"),
    "label for an unknown type": lambda t: t["types"].update(QSO="Quasar"),
    "blank-padded type label": lambda t: t["types"].update(G="Galaxy "),
    "repeated name": lambda t: t["rows"][1].__setitem__(0, t["rows"][0][0]),
    "blank-padded name": lambda t: put(t, name=t["rows"][0][0] + " "),
    "unknown type": lambda t: put(t, type="QSO"),
    "right ascension of 360": lambda t: put(t, type="G", raDeg=360.0, decDeg=0.0, constellation="Psc"),
    "negative right ascension": lambda t: put(t, type="G", raDeg=-0.5, decDeg=0.0, constellation="Psc"),
    "declination past the pole": lambda t: put(t, type="G", raDeg=10.0, decDeg=90.5, constellation="Cep"),
    "right ascension without declination": lambda t: put(t, type="G", raDeg=10.0, decDeg=None, constellation="Psc"),
    "no position on a galaxy": lambda t: put(t, type="G", raDeg=None, decDeg=None, constellation=None),
    "position as text": lambda t: put(t, type="G", raDeg="10.0", decDeg=0.0, constellation="Psc"),
    "unknown constellation": lambda t: put(t, type="GCl", raDeg=229.64, decDeg=2.08, constellation="Ser"),
    "constellation missing with a position": lambda t: put(t, type="G", raDeg=10.0, decDeg=0.0, constellation=None),
    "constellation without a position": lambda t: put(t, type="NonEx", raDeg=None, decDeg=None, constellation="Cru"),
    "zero major axis": lambda t: put(t, majorAxisArcmin=0.0, minorAxisArcmin=None, positionAngleDeg=None),
    "minor axis larger than major": lambda t: put(t, majorAxisArcmin=1.0, minorAxisArcmin=2.0),
    "minor axis without major": lambda t: put(t, majorAxisArcmin=None, minorAxisArcmin=1.0, positionAngleDeg=None),
    "position angle of 181": lambda t: put(t, majorAxisArcmin=1.0, minorAxisArcmin=None, positionAngleDeg=181.0),
    "negative position angle": lambda t: put(t, majorAxisArcmin=1.0, minorAxisArcmin=None, positionAngleDeg=-1.0),
    "position angle without major axis": lambda t: put(t, majorAxisArcmin=None, minorAxisArcmin=None, positionAngleDeg=10.0),
    "magnitude as text": lambda t: put(t, bMag="4.0"),
    "messier 0": lambda t: put(t, messier=0),
    "messier 111": lambda t: put(t, messier=111),
    "fractional messier": lambda t: put(t, messier=2.5),
    "boolean messier": lambda t: put(t, messier=True),
    "messier repeated outside duplicates": lambda t: repeat_messier(t),
    "duplicate naming no target": lambda t: put(t, type="Dup", raDeg=10.0, decDeg=0.0, constellation="Psc", messier=None, ngc=[], ic=[]),
    "prefixed NGC cross reference": lambda t: put(t, ngc=["NGC0104"]),
    "IC cross reference as one string": lambda t: put(t, ic="0554"),
    "blank-padded identifier": lambda t: put(t, identifiers=[" PGC 003085"]),
    "empty common name": lambda t: put(t, commonNames=[""]),
}


def build_deep_sky_samples() -> dict[str, Any]:
    # Each fault is shown on the two Magellanic Clouds, which keeps the fixture small; the property test applies
    # every fault to generated catalogues too
    invalid = []
    for rule, fault in FAULTS.items():
        broken = catalog(ROWS[:2])
        fault(broken)
        invalid.append({"rule": rule, "table": broken})
    return {
        "credit": "OpenNGC, Mattia Verga, CC BY-SA",
        "format": DEEP_SKY_CATALOG_FORMAT,
        "formatVersion": DEEP_SKY_CATALOG_FORMAT_VERSION,
        "evidenceKind": ds.DEEP_SKY_EVIDENCE_KIND,
        "columns": list(DEEP_SKY_COLUMNS),
        "objectTypes": list(ds.DEEP_SKY_OBJECT_TYPES),
        "constellations": list(ds.DEEP_SKY_CONSTELLATIONS),
        "messierRange": [ds.DEEP_SKY_MESSIER_MIN, ds.DEEP_SKY_MESSIER_MAX],
        "patterns": {"crossReference": ds.CROSS_REFERENCE_PATTERN},
        "valid": [catalog(ROWS)],
        "invalid": invalid,
    }


def fixture_text(samples: dict[str, Any]) -> str:
    """Two-space JSON with each sample on one line, so the fixture stays small and a diff shows the samples that changed."""

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def block(value: Any, indent: int) -> str:
        pad = " " * indent
        return "[\n" + ",\n".join(f"{pad}  {compact(item)}" for item in value) + "\n" + pad + "]"

    lines = [f"  {json.dumps(key)}: {block(value, 2) if key in ('valid', 'invalid') else compact(value)}" for key, value in samples.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def test_deep_sky_fixture_is_current() -> None:
    samples = build_deep_sky_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(fixture_text(samples), encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python deep-sky contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_deep_sky_contract.py"
    )


def test_real_rows_parse_and_round_trip_unchanged() -> None:
    sample = build_deep_sky_samples()["valid"][0]
    parsed = DeepSkyCatalog.model_validate(sample)
    assert parsed.to_contract_dict() == sample
    assert json.loads(json.dumps(parsed.to_contract_dict())) == sample


def test_real_rows_keep_their_southern_showpieces() -> None:
    """The real rows name the southern objects the coverage audit (data-7) found missing from every baked table."""
    parsed = DeepSkyCatalog.model_validate(catalog(ROWS))
    common = {name for row in parsed.rows for name in row[col("commonNames")]}
    for name in ("Large Magellanic Cloud", "Small Magellanic Cloud", "Carina Nebula", "Omega Centauri",
                 "47 Tuc Cluster", "Herschel's Jewel Box", "Coalsack Nebula"):  # fmt: skip
        assert name in common
    by_name = {row[0]: row for row in parsed.rows}
    assert by_name["NGC0104"][col("decDeg")] == pytest.approx(-(72 + 4 / 60 + 53.2 / 3600), abs=1e-6)
    assert by_name["M102"][col("type")] == "Dup" and by_name["M102"][col("messier")] == 101


@pytest.mark.parametrize("case", build_deep_sky_samples()["invalid"], ids=lambda case: case["rule"])
def test_every_invalid_sample_is_refused(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DeepSkyCatalog.model_validate(case["table"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
@pytest.mark.parametrize("name", ["raDeg", "majorAxisArcmin", "bMag"])
def test_values_that_json_cannot_carry_are_refused(bad: float, name: str) -> None:
    broken = catalog(ROWS)
    put(broken, **{name: bad})
    with pytest.raises(ValidationError):
        DeepSkyCatalog.model_validate(broken)


# =============================================================================================
# PBT-DSO-1: the model accepts exactly the catalogues the rules allow
# =============================================================================================


def _floats(low: float, high: float, **bounds: bool) -> st.SearchStrategy[float]:
    return st.floats(low, high, allow_nan=False, allow_infinity=False, **bounds)


_TEXT = st.from_regex(r"\A[A-Za-z0-9][A-Za-z0-9 +'.-]{0,10}[A-Za-z0-9]\Z")
_CROSS = st.from_regex(r"\A[0-9]{4}([A-Z]{1,2})?( NED[0-9]{2})?\Z")


@st.composite
def row_strategy(draw: st.DrawFn, name: str, messier: int | None) -> list[Any]:
    kind = draw(st.sampled_from(ds.DEEP_SKY_OBJECT_TYPES))
    positioned = kind != "NonEx" or draw(st.booleans())
    ra = draw(_floats(0, 360, exclude_max=True)) if positioned else None
    dec = draw(_floats(-90, 90)) if positioned else None
    constellation = draw(st.sampled_from(ds.DEEP_SKY_CONSTELLATIONS)) if positioned else None
    major = draw(st.none() | _floats(0, 700, exclude_min=True))
    minor = draw(st.none() | _floats(0, major, exclude_min=True)) if major is not None else None
    angle = draw(st.none() | _floats(0, 180)) if major is not None else None
    mags = [draw(st.none() | _floats(-2, 35)) for _ in range(3)]
    if kind == "Dup":
        messier = draw(st.none() | st.integers(ds.DEEP_SKY_MESSIER_MIN, ds.DEEP_SKY_MESSIER_MAX))
    ngc = draw(st.lists(_CROSS, max_size=2))
    ic = draw(st.lists(_CROSS, max_size=2))
    if kind == "Dup" and messier is None and not ngc and not ic:
        ngc = ["0001"]
    identifiers = draw(st.lists(_TEXT, max_size=3))
    common = draw(st.lists(_TEXT, max_size=2))
    return [name, kind, ra, dec, constellation, major, minor, angle, *mags, messier, ngc, ic, identifiers, common]


@st.composite
def valid_catalog(draw: st.DrawFn) -> dict[str, Any]:
    count = draw(st.integers(2, 5))
    numbers = draw(st.lists(st.none() | st.integers(1, 110), min_size=count, max_size=count))
    seen: set[int] = set()
    rows = []
    for i, number in enumerate(numbers):
        # Messier numbers stay unique outside duplicates; a duplicate draws its own
        messier = number if number is not None and number not in seen else None
        if messier is not None:
            seen.add(messier)
        rows.append(draw(row_strategy(f"OBJ{i:04d}", messier)))
    return catalog(rows)


@st.composite
def catalog_with_fault(draw: st.DrawFn) -> tuple[dict[str, Any], str | None]:
    t = draw(valid_catalog())
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS))))
    if fault is not None:
        FAULTS[fault](t)
    return t, fault


@settings(max_examples=300, deadline=None)
@given(case=catalog_with_fault())
@example(case=(catalog(ROWS), None))  # the real rows: no position, duplicates by M, NGC and IC, Se1
def test_pbt_dso_1_model_accepts_exactly_the_catalogues_the_rules_allow(case: tuple[dict[str, Any], str | None]) -> None:
    """PBT-DSO-1 (Python half): validation succeeds exactly when no rule is broken, and the wire form round-trips."""
    t, fault = case
    try:
        parsed = DeepSkyCatalog.model_validate(t)
    except ValidationError:
        assert fault is not None, "a catalogue that keeps every rule was refused"
        return
    assert fault is None, f"a catalogue with the fault {fault!r} was accepted"
    assert DeepSkyCatalog.model_validate(parsed.to_contract_dict()) == parsed
    assert parsed.to_contract_dict() == t
