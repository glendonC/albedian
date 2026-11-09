"""Bright-star 3D supplement contract (data-11a): Pydantic rules, a property, and the fixture shared with the Zod schema.

`tests/fixtures/bright_stars_contract.json` is written from `build_bright_star_samples()` and parsed by
packages/contracts/src/__tests__/bright-stars.test.ts, so both sides accept and refuse the same supplements.
Regenerate it after a deliberate contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_bright_stars_contract.py

The valid sample holds six real rows, made by .lanetools/bright_rows.py from one ESA Gaia archive query of
public.hipparcos_newreduction (van Leeuwen 2007) joined with public.hipparcos (ESA 1997) for the 226 stars the G 12
tiles take from Hipparcos (CC BY-NC 3.0 IGO, credit ESA), the tiles' names.json and bright-star-merge.json, and each
star's row in the core tiles: Sirius, Rigil Kentaurus and Toliman (one system, two very different parallax errors),
Canopus, Betelgeuse, and HIP 26224, whose 2007 parallax is negative. Every invalid sample breaks one rule.
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

from services.contracts import BRIGHT_STAR_COLUMNS, BrightStarSupplement
from services.contracts import bright_stars as bs

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "bright_stars_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
CATALOGUE = "Hipparcos, the New Reduction (van Leeuwen 2007), with V from the Hipparcos Catalogue (ESA 1997)"

# fmt: off
ROWS: list[list[Any]] = [
    [32349, "Sirius", 101.2885410520664, -16.713143062644765, 1.21, 1.04, 379.21, 1.58, -546.01, -1223.07, 1.33, 1.24, -1.0876, -1.44, 0.009, 0, 1, None, "no Gaia DR3 best neighbour", 0, 5, 0],
    [71683, "Rigil Kentaurus", 219.9204081301028, -60.83514521897498, 3.07, 2.46, 754.81, 4.11, -3679.25, 473.67, 3.89, 3.24, 0.1373, -0.01, 0.71, 95, 1, None, "no Gaia DR3 best neighbour", 0, 10, 0],
    [71681, "Toliman", 219.91412459936643, -60.83948046402479, 19.15, 14.24, 796.92, 25.9, -3614.39, 802.98, 20.48, 19.52, 1.2429, 1.35, 0.9, 95, 1, "5877748442128924544", "Gaia best neighbour outside the G < 12 subset (G 20.949)", 0, 10, 5],
    [30438, "Canopus", 95.98787789671694, -52.69571786807983, 0.48, 0.53, 10.55, 0.56, 19.93, 23.24, 0.52, 0.69, -0.5536, -0.62, 0.164, 5, 1, None, "no Gaia DR3 best neighbour", 0, 9, 0],
    [27989, "Betelgeuse", 88.79287148995971, 7.407036532062891, 0.71, 0.58, 6.55, 0.83, 27.54, 11.3, 1.03, 0.65, 0.4997, 0.45, 1.5, 1, 1, None, "no Gaia DR3 best neighbour", 0, 5, 3],
    [26224, None, 83.82165270697894, -5.387677413448033, 6.03, 4.65, -27.24, 8.21, -7.11, 8.29, 7.25, 4.8, 4.7663, 6.71, 0.08, 15, 1, None, "no Gaia DR3 best neighbour", 0, 5, 189],
]
# fmt: on


def supplement(rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "format": bs.BRIGHT_STAR_SUPPLEMENT_FORMAT,
        "formatVersion": bs.BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
        "evidenceKind": bs.BRIGHT_STAR_EVIDENCE_KIND,
        "catalogue": CATALOGUE,
        "frame": bs.BRIGHT_STAR_FRAME,
        "epochJulianYear": bs.BRIGHT_STAR_EPOCH_JULIAN_YEAR,
        "tilesRelease": "gaia-dr3",
        "columns": list(BRIGHT_STAR_COLUMNS),
        "rows": copy.deepcopy(rows),
    }


def col(name: str) -> int:
    return BRIGHT_STAR_COLUMNS.index(name)


def put(t: dict[str, Any], **values: Any) -> None:
    """Set columns of the first row."""
    for name, value in values.items():
        t["rows"][0][col(name)] = value


# Each fault breaks a rule of any valid supplement of at least two rows
FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "format": lambda t: t.update(format="albedian.bright-star-list"),
    "formatVersion": lambda t: t.update(formatVersion=2),
    "evidence kind": lambda t: t.update(evidenceKind="observed"),
    "snake-case top-level key": lambda t: t.update(format_version=t.pop("formatVersion")),
    "extra field": lambda t: t.update(extra=1),
    "frame FK5": lambda t: t.update(frame="FK5"),
    "epoch J2016.0": lambda t: t.update(epochJulianYear=2016.0),
    "blank-padded catalogue": lambda t: t.update(catalogue=" Hipparcos"),
    "column renamed": lambda t: t["columns"].__setitem__(0, "id"),
    "no rows": lambda t: t.update(rows=[]),
    "short row": lambda t: t["rows"][0].pop(),
    "long row": lambda t: t["rows"][0].append(None),
    "repeated HIP": lambda t: t["rows"][1].__setitem__(0, t["rows"][0][0]),
    "repeated tile location": lambda t: [t["rows"][1].__setitem__(k, t["rows"][0][k]) for k in (19, 20, 21)],
    "HIP 0": lambda t: put(t, hip=0),
    "HIP as text": lambda t: put(t, hip="32349"),
    "blank-padded name": lambda t: put(t, name="Sirius "),
    "right ascension of 360": lambda t: put(t, raDeg=360.0),
    "negative right ascension": lambda t: put(t, raDeg=-0.5),
    "declination past the pole": lambda t: put(t, decDeg=90.5),
    "declination below the south pole": lambda t: put(t, decDeg=-90.5),
    "negative position error": lambda t: put(t, raErrorMas=-0.1),
    "zero parallax error": lambda t: put(t, parallaxErrorMas=0.0),
    "parallax as text": lambda t: put(t, parallaxMas="379.21"),
    "negative proper-motion error": lambda t: put(t, pmDecErrorMasPerYr=-1.0),
    "V as text": lambda t: put(t, vMag="-1.44"),
    "fractional solution type": lambda t: put(t, solutionType=1.5),
    "negative solution type": lambda t: put(t, solutionType=-1),
    "zero components": lambda t: put(t, components=0),
    "boolean components": lambda t: put(t, components=True),
    "Gaia source id as a number": lambda t: put(t, gaiaSourceId=5877748442),
    "Gaia source id with a letter": lambda t: put(t, gaiaSourceId="5877748442128924544x"),
    "empty reason": lambda t: put(t, hipparcosReason=""),
    "negative tile layer": lambda t: put(t, tileLayer=-1),
    "fractional tile index": lambda t: put(t, tileIndex=0.5),
}


def build_bright_star_samples() -> dict[str, Any]:
    # Each fault is shown on Sirius and Rigil Kentaurus, which keeps the fixture small; the property test applies
    # every fault to generated supplements too
    invalid = []
    for rule, fault in FAULTS.items():
        broken = supplement(ROWS[:2])
        fault(broken)
        invalid.append({"rule": rule, "table": broken})
    return {
        "credit": "ESA; Hipparcos, van Leeuwen 2007; CC BY-NC 3.0 IGO",
        "format": bs.BRIGHT_STAR_SUPPLEMENT_FORMAT,
        "formatVersion": bs.BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
        "evidenceKind": bs.BRIGHT_STAR_EVIDENCE_KIND,
        "frame": bs.BRIGHT_STAR_FRAME,
        "epochJulianYear": bs.BRIGHT_STAR_EPOCH_JULIAN_YEAR,
        "columns": list(BRIGHT_STAR_COLUMNS),
        "patterns": {"gaiaSourceId": bs.GAIA_SOURCE_ID_PATTERN},
        "valid": [supplement(ROWS)],
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


def test_bright_star_fixture_is_current() -> None:
    samples = build_bright_star_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(fixture_text(samples), encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python bright-star contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_bright_stars_contract.py"
    )


def test_real_rows_parse_and_round_trip_unchanged() -> None:
    sample = build_bright_star_samples()["valid"][0]
    parsed = BrightStarSupplement.model_validate(sample)
    assert parsed.to_contract_dict() == sample
    assert json.loads(json.dumps(parsed.to_contract_dict())) == sample


def test_real_rows_keep_a_negative_parallax_and_the_alpha_centauri_pair() -> None:
    """A negative parallax is a published value, kept as it is (the contract's parallax rule gives it no distance);
    Rigil Kentaurus and Toliman are one system, yet their 2007 parallaxes differ by 42 mas."""
    parsed = BrightStarSupplement.model_validate(supplement(ROWS))
    by_hip = {row[0]: row for row in parsed.rows}
    assert by_hip[26224][col("parallaxMas")] < 0 and by_hip[26224][col("name")] is None
    a, b = by_hip[71683], by_hip[71681]
    assert (a[col("tilePixel")], b[col("tilePixel")]) == (10, 10)
    assert abs(a[col("parallaxMas")] - b[col("parallaxMas")]) == pytest.approx(42.11, abs=1e-6)
    assert b[col("gaiaSourceId")] == "5877748442128924544"


@pytest.mark.parametrize("case", build_bright_star_samples()["invalid"], ids=lambda case: case["rule"])
def test_every_invalid_sample_is_refused(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BrightStarSupplement.model_validate(case["table"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
@pytest.mark.parametrize("name", ["raDeg", "parallaxMas", "pmRaCosDecMasPerYr", "vMag"])
def test_values_that_json_cannot_carry_are_refused(bad: float, name: str) -> None:
    broken = supplement(ROWS)
    put(broken, **{name: bad})
    with pytest.raises(ValidationError):
        BrightStarSupplement.model_validate(broken)


# =============================================================================================
# PBT-BSS-1: the model accepts exactly the supplements the rules allow
# =============================================================================================


def _floats(low: float, high: float, **bounds: bool) -> st.SearchStrategy[float]:
    return st.floats(low, high, allow_nan=False, allow_infinity=False, **bounds)


_TEXT = st.from_regex(r"\A[A-Za-z0-9][A-Za-z0-9 +'.<-]{0,10}[A-Za-z0-9)]\Z")


@st.composite
def row_strategy(draw: st.DrawFn, hip: int, place: tuple[int, int, int]) -> list[Any]:
    return [
        hip,
        draw(st.none() | _TEXT),
        draw(_floats(0, 360, exclude_max=True)),
        draw(_floats(-90, 90)),
        draw(_floats(0, 50)),
        draw(_floats(0, 50)),
        draw(_floats(-100, 800)),
        draw(_floats(0, 100, exclude_min=True)),
        draw(_floats(-4000, 4000)),
        draw(_floats(-4000, 4000)),
        draw(_floats(0, 50)),
        draw(_floats(0, 50)),
        draw(_floats(-2, 13)),
        draw(st.none() | _floats(-2, 13)),
        draw(st.none() | _floats(-0.5, 3)),
        draw(st.integers(0, 165)),
        draw(st.integers(1, 4)),
        draw(st.none() | st.from_regex(r"\A[1-9][0-9]{0,18}\Z")),
        draw(_TEXT),
        *place,
    ]


@st.composite
def valid_supplement(draw: st.DrawFn) -> dict[str, Any]:
    count = draw(st.integers(2, 5))
    hips = draw(st.lists(st.integers(1, 120_000), min_size=count, max_size=count, unique=True))
    places = draw(st.lists(st.tuples(st.integers(0, 5), st.integers(0, 49_151), st.integers(0, 2_000)), min_size=count, max_size=count, unique=True))
    return supplement([draw(row_strategy(hip, place)) for hip, place in zip(hips, places, strict=True)])


@st.composite
def supplement_with_fault(draw: st.DrawFn) -> tuple[dict[str, Any], str | None]:
    t = draw(valid_supplement())
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS))))
    if fault is not None:
        FAULTS[fault](t)
    return t, fault


@settings(max_examples=300, deadline=None)
@given(case=supplement_with_fault())
@example(case=(supplement(ROWS), None))  # the real rows: a negative parallax, a Gaia id, nulls
def test_pbt_bss_1_model_accepts_exactly_the_supplements_the_rules_allow(case: tuple[dict[str, Any], str | None]) -> None:
    """PBT-BSS-1 (Python half): validation succeeds exactly when no rule is broken, and the wire form round-trips."""
    t, fault = case
    try:
        parsed = BrightStarSupplement.model_validate(t)
    except ValidationError:
        assert fault is not None, "a supplement that keeps every rule was refused"
        return
    assert fault is None, f"a supplement with the fault {fault!r} was accepted"
    assert BrightStarSupplement.model_validate(parsed.to_contract_dict()) == parsed
    assert parsed.to_contract_dict() == t
