"""Exoplanet table contract (data-6c0): Pydantic rules, a property, and the fixture shared with the Zod schema.

`tests/fixtures/exoplanet_contract.json` is written from `build_exoplanet_samples()` and parsed by
packages/contracts/src/__tests__/exoplanets.test.ts, so both sides accept and refuse the same tables. Regenerate it
after a deliberate contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_exoplanet_contract.py

The valid sample holds six real rows of the NASA Exoplanet Archive's Planetary Systems Composite Parameters
table (PSCompPars, `select * from pscomppars`, fetched 2026-11-05T00:36:15Z), turned into the contract's tuples by
a script and kept here as literals, with the references they cite. Every invalid sample breaks one rule of it.

The fixture carries the archive's acknowledgement (docs/acknowledge.html): "This research has made use of the NASA
Exoplanet Archive, which is operated by the California Institute of Technology, under contract with the National
Aeronautics and Space Administration under the Exoplanet Exploration Program." Each value's literature reference is
in the samples' reference lists, as the archive asks.
"""

from __future__ import annotations

import copy
import json
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import (
    EXOPLANET_COLUMNS,
    EXOPLANET_TABLE_FORMAT,
    EXOPLANET_TABLE_FORMAT_VERSION,
    ExoplanetTable,
)
from services.contracts import exoplanets as ex

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "exoplanet_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
SAMPLE_NOTE = "Contract test sample; not the archive's own wording."
ARCHIVE_ACKNOWLEDGEMENT = (
    "This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of "
    "Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet "
    "Exploration Program."
)

# The references the rows below cite, by index; the archive's "Calculated Value" link resolved against its host
REFERENCES: list[dict[str, str]] = [
    {"refstr": "CONT_ET_AL_2026", "label": "Cont et al. 2026", "url": "https://ui.adsabs.harvard.edu/abs/2026A&A...710A.345C/abstract"},
    {"refstr": "CALCULATED_VALUE", "label": "Calculated Value", "url": "https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html"},
    {"refstr": "STASSUN_ET_AL__2019", "label": "TICv8", "url": "https://ui.adsabs.harvard.edu/abs/2019AJ....158..138S/abstract"},
    {"refstr": "STASSUN_ET_AL__2017", "label": "Stassun et al. 2017", "url": "https://ui.adsabs.harvard.edu/abs/2017AJ....153..136S/abstract"},
    {"refstr": "BONOMO_ET_AL__2017", "label": "Bonomo et al. 2017", "url": "https://ui.adsabs.harvard.edu/abs/2017A&A...602A.107B/abstract"},
    {"refstr": "SOUTHWORTH_2010", "label": "Southworth 2010", "url": "https://ui.adsabs.harvard.edu/abs/2010MNRAS.408.1689S/abstract"},
    {"refstr": "PASS_ET_AL_2026", "label": "Pass et al. 2026", "url": "https://ui.adsabs.harvard.edu/abs/2026AJ....172..175P/abstract"},
    {"refstr": "GILBERT_ET_AL_2023", "label": "Gilbert et al. 2023", "url": "https://ui.adsabs.harvard.edu/abs/2023ApJ...944L..35G/abstract"},
    {"refstr": "RODRIGUEZ_ET_AL__2020", "label": "Rodriguez et al. 2020", "url": "https://ui.adsabs.harvard.edu/abs/2020AJ....160..117R/abstract"},
    {"refstr": "DRESSING_ET_AL__2017", "label": "Dressing et al. 2017", "url": "https://ui.adsabs.harvard.edu/abs/2017AJ....154..207D/abstract"},
    {"refstr": "ADAMS_ET_AL__2021", "label": "Adams et al. 2021", "url": "https://ui.adsabs.harvard.edu/abs/2021PSJ.....2..152A/abstract"},
    {"refstr": "SANCHIS_OJEDA_ET_AL__2015", "label": "Sanchis-Ojeda et al. 2015", "url": "https://ui.adsabs.harvard.edu/abs/2015ApJ...812..112S/abstract"},
    {"refstr": "RADZOM_ET_AL__2025", "label": "Radzom et al. 2025", "url": "https://ui.adsabs.harvard.edu/abs/2025AJ....169..189R/abstract"},
    {"refstr": "EXOFOP", "label": "ExoFOP", "url": "https://exofop.ipac.caltech.edu/tess/view_toi.php"},
]

# Real PSCompPars rows: 51 Peg b (a modelled radius, a calculated temperature, an upper limit on eccentricity),
# HD 209458 b (all measured), TOI-700 d (a mass from the mass-radius relation), K2-90 c (a calculated temperature
# that has uncertainties), K2-22 b (two stars, an upper limit on mass, zero uncertainties on a), TOI-5143 c (a mass
# provenance but no mass).
ROWS: list[list[Any]] = [
    ["51 Peg b", "51 Peg", 1, 1, "Radial Velocity", 1995, "Haute-Provence Observatory", "Mass", [4.2307966, 2.7e-06, -2.7e-06, 0, 0, "measured"], [0.052, None, None, 0, 0, "measured"], [14.1, None, None, 0, 1, "modelled"], [193.87532827, 19.06970442, -15.89142035, 0, 0, "measured"], [0.0063, None, None, 1, 0, "measured"], [1328.93, None, None, 0, 1, "calculated"], [5761.0, 95.0, -95.0, 0, 0, "measured"], [1.19, 0.03, -0.03, 0, 0, "measured"], [1.07, 0.05, -0.05, 0, 0, "measured"], [15.4614, 0.0297, -0.0297, 0, 2, "measured"], [344.3675399, 2.55158333333333e-08, -2.55158333333333e-08, None, 2, "measured"], [20.7690958, 2.85736388888889e-08, -2.85736388888889e-08, None, 2, "measured"], [5.45309, 0.023, -0.023, 0, 2, "measured"]],
    ["HD 209458 b", "HD 209458", 1, 1, "Radial Velocity", 1999, "W. M. Keck Observatory", "Mass", [3.52474859, 3.8e-07, -3.8e-07, 0, 3, "measured"], [0.04707, 0.00045, -0.00047, 0, 4, "measured"], [15.58051, 0.22418, -0.22418, 0, 3, "measured"], [232.0159, 12.7132, -12.7132, 0, 3, "measured"], [0.0, None, None, 0, 3, "measured"], [1459.0, 12.0, -12.0, 0, 5, "measured"], [6091.0, 10.0, -10.0, 0, 3, "measured"], [1.19, 0.02, -0.02, 0, 3, "measured"], [1.23, 0.09, -0.09, 0, 3, "measured"], [48.3016, 0.124, -0.1234, 0, 2, "measured"], [330.7950219, 1.33780277777778e-08, -1.33780277777778e-08, None, 2, "measured"], [18.8842419, 1.24332222222222e-08, -1.24332222222222e-08, None, 2, "measured"], [7.65, 0.03, -0.03, 0, 2, "measured"]],
    ["TOI-700 d", "TOI-700", 1, 4, "Transit", 2020, "Transiting Exoplanet Survey Satellite (TESS)", "M-R relationship", [37.423457, 1.8e-05, -1.7e-05, 0, 6, "measured"], [0.1642, 0.0025, -0.0027, 0, 6, "measured"], [1.145, 0.032, -0.026, 0, 6, "measured"], [1.58, None, None, 0, 1, "modelled"], [0.042, 0.045, -0.03, 0, 7, "measured"], [268.8, 7.7, -7.6, 0, 8, "measured"], [3459.0, 65.0, -65.0, 0, 7, "measured"], [0.4104, 0.0093, -0.0069, 0, 6, "measured"], [0.417, 0.02, -0.02, 0, 6, "measured"], [31.1265, 0.0206, -0.0206, 0, 2, "measured"], [97.0957165, 6.36538888888889e-09, -6.36538888888889e-09, None, 2, "measured"], [-65.5786149, 8.43705555555556e-09, -8.43705555555556e-09, None, 2, "measured"], [13.151, 0.069, -0.069, 0, 2, "measured"]],
    ["K2-90 c", "K2-90", 1, 2, "Transit", 2016, "K2", "M-R relationship", [2.900706, 0.000331, -0.000319, 0, 9, "measured"], [0.035, 0.001, -0.001, 0, 9, "measured"], [1.3, 0.13, -0.11, 0, 9, "measured"], [2.24, None, None, 0, 1, "modelled"], [0.19, 0.211462, -0.153189, 0, 9, "measured"], [908.07, 30.93, -30.93, 0, 1, "calculated"], [4421.98, 93.48, -91.15, 0, 9, "measured"], [0.634754, 0.029302, -0.028633, 0, 9, "measured"], [0.704613, 0.070461, -0.070461, 0, 9, "measured"], [134.188, 0.421, -0.419, 0, 2, "measured"], [59.1527391, 5.96261111111111e-09, -5.96261111111111e-09, None, 2, "measured"], [22.4724517, 3.12658333333333e-09, -3.12658333333333e-09, None, 2, "measured"], [13.707, 0.103, -0.103, 0, 2, "measured"]],
    ["K2-22 b", "K2-22", 2, 1, "Transit", 2015, "K2", "Mass", [0.381073, 2.4e-05, -2.4e-05, 0, 10, "measured"], [0.009, 0.0, 0.0, 0, 9, "measured"], [2.3, 0.1, -0.1, 0, 10, "measured"], [444.962, None, None, 1, 11, "measured"], [0.19, 0.221175, -0.152914, 0, 9, "measured"], [2100.0, None, None, 0, 11, "measured"], [3879.0, 90.0, -90.0, 0, 10, "measured"], [0.58, 0.03, -0.03, 0, 10, "measured"], [0.595292, 0.059529, -0.059529, 0, 9, "measured"], [243.836, 2.7975, -2.7975, 0, 2, "measured"], [169.4827046, 1.15582777777778e-08, -1.15582777777778e-08, None, 2, "measured"], [2.6190363, 9.41388888888889e-09, -9.41388888888889e-09, None, 2, "measured"], [15.565, 0.046, -0.046, 0, 2, "measured"]],
    ["TOI-5143 c", "TOI-5143", 1, 1, "Transit", 2025, "Transiting Exoplanet Survey Satellite (TESS)", "Msini", [5.2097118, 3.2e-06, -3.9e-06, 0, 12, "measured"], [0.05602, 0.00082, -0.00079, 0, 12, "measured"], [16.25302201, 2.01761653, -1.34507768, 0, 12, "measured"], None, None, [904.48, None, None, 0, 13, "measured"], [5183.0, 125.0, -125.0, 0, 12, "measured"], [0.852, 0.02, -0.02, 0, 12, "measured"], [0.864, 0.04, -0.033, 0, 12, "measured"], [181.38, 2.13, -2.082, 0, 2, "measured"], [165.3651202, 1.29654444444444e-08, -1.29654444444444e-08, None, 2, "measured"], [5.1394812, 1.56103333333333e-08, -1.56103333333333e-08, None, 2, "measured"], [11.935, 0.029, -0.029, 0, 2, "measured"]],
]  # fmt: skip


def table(rows: list[list[Any]], references: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "format": EXOPLANET_TABLE_FORMAT,
        "formatVersion": EXOPLANET_TABLE_FORMAT_VERSION,
        "evidenceKind": ex.EXOPLANET_EVIDENCE_KIND,
        "columns": list(EXOPLANET_COLUMNS),
        "notes": {"equilibriumTemperatureK": SAMPLE_NOTE},
        "references": copy.deepcopy(references),
        "rows": copy.deepcopy(rows),
    }


def col(name: str) -> int:
    return EXOPLANET_COLUMNS.index(name)


def calc_index(t: dict[str, Any]) -> int:
    return next(i for i, ref in enumerate(t["references"]) if ref["refstr"] == ex.EXOPLANET_CALCULATED_REFSTR)


def paper_index(t: dict[str, Any]) -> int:
    return next(i for i, ref in enumerate(t["references"]) if ref["refstr"] != ex.EXOPLANET_CALCULATED_REFSTR)


def set_cell(t: dict[str, Any], name: str, value: float, ref: int, basis: str, provenance: Any = ...) -> None:
    t["rows"][0][col(name)] = [value, None, None, 0, ref, basis]
    if provenance is not ...:
        t["rows"][0][col("massProvenance")] = provenance


def first_value_cell(t: dict[str, Any]) -> list[Any]:
    return next(cell for cell in t["rows"][0][len(ex.EXOPLANET_COLUMNS) - len(ex.EXOPLANET_VALUE_COLUMNS) :] if cell is not None)


def repeat_a_reference_cited_twice(t: dict[str, Any]) -> None:
    """List the most-cited reference a second time and move one of its citations to the copy, so both are cited."""
    first_value = len(ex.EXOPLANET_IDENTITY_COLUMNS)
    cells = [cell for row in t["rows"] for cell in row[first_value:] if cell is not None]
    ref, citations = Counter(cell[4] for cell in cells).most_common(1)[0]
    assert citations >= 2, "the fault needs a reference cited twice"
    t["references"].append(copy.deepcopy(t["references"][ref]))
    next(cell for cell in cells if cell[4] == ref)[4] = len(t["references"]) - 1


# Each fault breaks a rule of any valid table of at least two rows, one of them citing the archive's calculation page
FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "format": lambda t: t.update(format="albedian.exoplanet-list"),
    "formatVersion": lambda t: t.update(formatVersion=2),
    "evidence kind": lambda t: t.update(evidenceKind="observed"),
    "extra field": lambda t: t.update(extra=1),
    "column renamed": lambda t: t["columns"].__setitem__(0, "planet"),
    "no rows": lambda t: t.update(rows=[]),
    "short row": lambda t: t["rows"][0].pop(),
    "long row": lambda t: t["rows"][0].append(None),
    "temperature note missing": lambda t: t.update(notes={}),
    "note for an unknown column": lambda t: t["notes"].update(colour="Not a column."),
    "blank-padded note": lambda t: t["notes"].update(equilibriumTemperatureK=" Padded."),
    "lower-case refstr": lambda t: t["references"][0].update(refstr="lower_case_2020"),
    "reference over plain http": lambda t: t["references"][0].update(url="http://ui.adsabs.harvard.edu/abs/x"),
    "reference without a host": lambda t: t["references"][0].update(url="https:///abs/x"),
    "blank-padded label": lambda t: t["references"][0].update(label=" Padded et al. 2020"),
    "extra reference field": lambda t: t["references"][0].update(year=2020),
    "repeated reference": lambda t: t["references"].append(copy.deepcopy(t["references"][0])),
    "repeated reference with both copies cited": repeat_a_reference_cited_twice,
    "unused reference": lambda t: t["references"].append({"refstr": "UNUSED_2026", "label": "Unused 2026", "url": "https://example.org/unused"}),
    "reference index past the end": lambda t: first_value_cell(t).__setitem__(4, len(t["references"])),
    "negative reference index": lambda t: first_value_cell(t).__setitem__(4, -1),
    "repeated planet name": lambda t: t["rows"][1].__setitem__(0, t["rows"][0][0]),
    "blank-padded planet name": lambda t: t["rows"][0].__setitem__(0, t["rows"][0][0] + " "),
    "zero stars": lambda t: t["rows"][0].__setitem__(col("starCount"), 0),
    "planet count as text": lambda t: t["rows"][0].__setitem__(col("planetCount"), "1"),
    "fractional discovery year": lambda t: t["rows"][0].__setitem__(col("discoveryYear"), 2011.5),
    "unknown mass provenance": lambda t: t["rows"][0].__setitem__(col("massProvenance"), "Mass?"),
    "unknown basis": lambda t: first_value_cell(t).__setitem__(5, "estimated"),
    "positive lower uncertainty": lambda t: first_value_cell(t).__setitem__(2, 0.5),
    "negative upper uncertainty": lambda t: first_value_cell(t).__setitem__(1, -0.5),
    "limit flag 2": lambda t: first_value_cell(t).__setitem__(3, 2),
    "boolean limit flag": lambda t: first_value_cell(t).__setitem__(3, True),
    "value cell too short": lambda t: first_value_cell(t).pop(),
    "value as text": lambda t: first_value_cell(t).__setitem__(0, "1.5"),
    "negative orbital period": lambda t: set_cell(t, "orbitalPeriodDays", -1.0, paper_index(t), "measured"),
    "eccentricity of 1": lambda t: set_cell(t, "eccentricity", 1.0, paper_index(t), "measured"),
    "right ascension of 360": lambda t: set_cell(t, "raDeg", 360.0, paper_index(t), "measured"),
    "declination past the pole": lambda t: set_cell(t, "decDeg", 90.5, paper_index(t), "measured"),
    "zero distance": lambda t: set_cell(t, "distancePc", 0.0, paper_index(t), "measured"),
    "measured value citing the calculation page": lambda t: set_cell(t, "orbitalPeriodDays", 3.0, calc_index(t), "measured"),
    "calculated value citing a paper": lambda t: set_cell(t, "equilibriumTemperatureK", 900.0, paper_index(t), "calculated"),
    "modelled temperature": lambda t: set_cell(t, "equilibriumTemperatureK", 900.0, calc_index(t), "modelled"),
    "calculated radius": lambda t: set_cell(t, "radiusEarth", 1.5, calc_index(t), "calculated"),
    "modelled mass without the M-R provenance": lambda t: set_cell(t, "massEarth", 3.0, calc_index(t), "modelled", "Mass"),
    "M-R provenance on a measured mass": lambda t: set_cell(t, "massEarth", 3.0, paper_index(t), "measured", "M-R relationship"),
}


def cited_only(rows: list[list[Any]], references: list[dict[str, str]]) -> dict[str, Any]:
    """A table of these rows with only the references they cite, re-indexed in their original order."""
    first_value = len(ex.EXOPLANET_IDENTITY_COLUMNS)
    cited = sorted({cell[4] for row in rows for cell in row[first_value:] if cell is not None})
    renumber = {old: new for new, old in enumerate(cited)}
    rows = copy.deepcopy(rows)
    for row in rows:
        for cell in row[first_value:]:
            if cell is not None:
                cell[4] = renumber[cell[4]]
    return table(rows, [references[i] for i in cited])


def build_exoplanet_samples() -> dict[str, Any]:
    # Each fault is shown on two real rows, 51 Peg b (a modelled radius, a calculated temperature) and TOI-700 d (a
    # modelled mass), which keeps the fixture small; the property test applies every fault to generated tables too
    invalid = []
    for rule, fault in FAULTS.items():
        broken = cited_only([ROWS[0], ROWS[3]], REFERENCES)
        fault(broken)
        invalid.append({"rule": rule, "table": broken})
    return {
        "acknowledgement": ARCHIVE_ACKNOWLEDGEMENT,
        "format": EXOPLANET_TABLE_FORMAT,
        "formatVersion": EXOPLANET_TABLE_FORMAT_VERSION,
        "evidenceKind": ex.EXOPLANET_EVIDENCE_KIND,
        "columns": list(EXOPLANET_COLUMNS),
        "valueColumns": list(ex.EXOPLANET_VALUE_COLUMNS),
        "modelledColumns": list(ex.EXOPLANET_MODELLED_COLUMNS),
        "requiredNotes": list(ex.EXOPLANET_REQUIRED_NOTES),
        "valueBases": list(ex.EXOPLANET_VALUE_BASES),
        "massProvenances": list(ex.EXOPLANET_MASS_PROVENANCES),
        "calculatedRefstr": ex.EXOPLANET_CALCULATED_REFSTR,
        "patterns": {"refstr": ex.REFSTR_PATTERN, "httpsUrl": ex.HTTPS_URL_PATTERN},
        "valid": [cited_only(ROWS, REFERENCES)],
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


def test_exoplanet_fixture_is_current() -> None:
    samples = build_exoplanet_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(fixture_text(samples), encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python exoplanet contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_exoplanet_contract.py"
    )


def test_real_rows_parse_and_round_trip_unchanged() -> None:
    sample = build_exoplanet_samples()["valid"][0]
    parsed = ExoplanetTable.model_validate(sample)
    assert parsed.to_contract_dict() == sample
    assert json.loads(json.dumps(parsed.to_contract_dict())) == sample


@pytest.mark.parametrize("case", build_exoplanet_samples()["invalid"], ids=lambda case: case["rule"])
def test_every_invalid_sample_is_refused(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExoplanetTable.model_validate(case["table"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_values_that_json_cannot_carry_are_refused(bad: float) -> None:
    for position in (0, 1):
        broken = cited_only(ROWS, REFERENCES)
        first_value_cell(broken)[position] = bad
        with pytest.raises(ValidationError):
            ExoplanetTable.model_validate(broken)


# =============================================================================================
# PBT-EXO-1: the model accepts exactly the tables the rules allow
# =============================================================================================

POSITIVE = {"orbitalPeriodDays", "semiMajorAxisAu", "radiusEarth", "massEarth", "equilibriumTemperatureK",
            "hostEffectiveTemperatureK", "hostRadiusSolar", "hostMassSolar", "distancePc"}  # fmt: skip


def _floats(low: float, high: float, **bounds: bool) -> st.SearchStrategy[float]:
    return st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False, **bounds)


def value_strategy(name: str) -> st.SearchStrategy[float]:
    if name in POSITIVE:
        return _floats(0.0, 1e9, exclude_min=True)
    if name == "eccentricity":
        return _floats(0.0, 1.0, exclude_max=True)
    if name == "raDeg":
        return _floats(0.0, 360.0, exclude_max=True)
    if name == "decDeg":
        return _floats(-90.0, 90.0)
    return _floats(-30.0, 50.0)


@st.composite
def value_cell(draw: st.DrawFn, name: str, papers: list[int], calc: int) -> list[Any]:
    derived = draw(st.booleans())
    if derived:
        ref, basis = calc, "modelled" if name in ex.EXOPLANET_MODELLED_COLUMNS else "calculated"
    else:
        ref, basis = draw(st.sampled_from(papers)), "measured"
    err1 = draw(st.one_of(st.none(), _floats(0.0, 1e6)))
    err2 = draw(st.one_of(st.none(), _floats(-1e6, 0.0)))
    limit = draw(st.sampled_from([None, -1, 0, 1]))
    return [draw(value_strategy(name)), err1, err2, limit, ref, basis]


@st.composite
def valid_table(draw: st.DrawFn) -> dict[str, Any]:
    paper_count = draw(st.integers(1, 4))
    refstrs = draw(st.lists(st.from_regex(r"\A[A-Z][A-Z0-9_]{0,24}\Z").filter(lambda s: s != ex.EXOPLANET_CALCULATED_REFSTR), min_size=paper_count, max_size=paper_count, unique=True))
    references = [{"refstr": ex.EXOPLANET_CALCULATED_REFSTR, "label": "Calculated Value", "url": "https://exoplanetarchive.ipac.caltech.edu/docs/pscp_calc.html"}]
    references += [{"refstr": r, "label": f"Paper {i}", "url": f"https://ui.adsabs.harvard.edu/abs/{i}"} for i, r in enumerate(refstrs)]
    calc, papers = 0, list(range(1, paper_count + 1))
    rows = []
    for number in range(draw(st.integers(2, 5))):
        row: list[Any] = [
            f"Planet {number} b", f"Host {number}", draw(st.integers(1, 4)), draw(st.integers(1, 8)),
            draw(st.sampled_from(["Transit", "Radial Velocity", "Imaging"])), draw(st.integers(1992, 2026)),
            draw(st.sampled_from(["Kepler", "K2", "W. M. Keck Observatory"])), None,
        ]  # fmt: skip
        for name in ex.EXOPLANET_VALUE_COLUMNS:
            row.append(draw(st.one_of(st.none(), value_cell(name, papers, calc))))
        mass = row[col("massEarth")]
        if mass is None:
            row[col("massProvenance")] = draw(st.sampled_from([None, *ex.EXOPLANET_MASS_PROVENANCES]))
        else:
            row[col("massProvenance")] = "M-R relationship" if mass[5] == "modelled" else draw(st.sampled_from(["Mass", "Msini", "Msin(i)/sin(i)"]))
        rows.append(row)
    # Every reference is cited: the first two rows' temperatures cite the calculation page (so a reference is cited
    # twice, as one fault needs), and each paper is cited by a row's period (or, past the last row, a host temperature)
    rows[0][col("equilibriumTemperatureK")] = [500.0, None, None, 0, calc, "calculated"]
    rows[1][col("equilibriumTemperatureK")] = [600.0, None, None, 0, calc, "calculated"]
    for paper in papers:
        target = rows[(paper - 1) % len(rows)]
        target[col("hostEffectiveTemperatureK") if paper > len(rows) else col("orbitalPeriodDays")] = [3.0, None, None, 0, paper, "measured"]
    return table(rows, references)


@st.composite
def table_with_fault(draw: st.DrawFn) -> tuple[dict[str, Any], str | None]:
    t = draw(valid_table())
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS))))
    if fault is not None:
        FAULTS[fault](t)
    return t, fault


@settings(max_examples=300, deadline=None)
@given(case=table_with_fault())
@example(case=(cited_only(ROWS, REFERENCES), None))  # the real rows: measured, modelled, calculated, limits, no mass
def test_pbt_exo_1_model_accepts_exactly_the_tables_the_rules_allow(case: tuple[dict[str, Any], str | None]) -> None:
    """PBT-EXO-1 (Python half): validation succeeds exactly when no rule is broken, and the wire form round-trips."""
    t, fault = case
    try:
        parsed = ExoplanetTable.model_validate(t)
    except ValidationError:
        assert fault is not None, "a table that keeps every rule was refused"
        return
    assert fault is None, f"a table with the fault {fault!r} was accepted"
    assert ExoplanetTable.model_validate(parsed.to_contract_dict()) == parsed
    assert parsed.to_contract_dict() == t
