"""The catalog reference contract: the Zod schema and its Pydantic mirror judge the same references alike.

`CatalogReferenceSchema` in packages/contracts/src/astronomy.ts and `CatalogReference` in
services/contracts/catalog_reference.py. Regenerate the fixture with:
    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_catalog_reference_contract.py

- The fixture is current. It holds real samples: Vega (Hipparcos) and Meissa (Gaia DR3) as
  services/science/catalog_model.py writes them, and two real OpenNGC v20260501 rows as deep-sky references, M31 with
  its V magnitude and IC0113 with none. It also holds one fault per rule
- The mirror accepts every valid sample unchanged and refuses every fault; a key the contract does not name is dropped
  on both sides, not refused
- Every pinned star's reference is a valid reference
- PBT: a reference may leave its magnitude out, and may name OpenNGC, exactly when it is a deep-sky object
"""

from __future__ import annotations

import copy
import json
import math
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts.astronomy import NumberRule
from services.contracts.catalog_reference import (
    CATALOG_DISTANCE_METHODS,
    CATALOG_OBJECT_TYPES,
    CATALOG_PROVIDERS,
    CatalogReference,
)
from services.contracts.designation_tables import (
    DESIGNATION_CONSTELLATIONS,
    DESIGNATION_INDICES,
    DESIGNATION_LETTERS,
    FLAMSTEED_NUMBER_MAX,
)
from services.contracts.designation import FLAMSTEED_NUMBER_MIN
from services.science.catalog import ALL_SKY_CATALOG_STARS

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "catalog_reference_contract.json"
OPENNGC = ROOT / "apps" / "web" / "public" / "catalog" / "deepsky" / "openngc.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
OPENNGC_CITATION = "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0"
UNNAMED_KEY = {"note": "a key the contract does not name"}


def _pinned(star_id: str) -> dict[str, Any]:
    (star,) = [s for s in ALL_SKY_CATALOG_STARS if s.id == star_id]
    return json.loads(json.dumps(star.to_catalog_reference_dict()))


@lru_cache(maxsize=1)
def _openngc_table() -> dict[str, Any]:
    return json.loads(OPENNGC.read_text(encoding="utf-8"))


def _openngc(name: str) -> dict[str, Any]:
    """A real OpenNGC row as a deep-sky reference: its V magnitude when OpenNGC gives one, else none."""
    table = _openngc_table()
    at = {column: i for i, column in enumerate(table["columns"])}
    (row,) = [r for r in table["rows"] if r[at["name"]] == name]
    reference = {
        "id": f"openngc-{name}",
        "provider": "OpenNGC",
        "releaseVersion": table["release"],
        "catalogSourceId": name,
        "commonName": row[at["commonNames"]][0] if row[at["commonNames"]] else name,
        "constellation": row[at["constellation"]],
        "objectType": "deep_sky",
        "coordinateEpoch": "J2000.0",
        "raDeg": row[at["raDeg"]],
        "decDeg": row[at["decDeg"]],
        "citation": OPENNGC_CITATION,
    }
    if row[at["vMag"]] is not None:
        reference["apparentMagnitude"] = row[at["vMag"]]
    return reference


def valid_samples() -> dict[str, dict[str, Any]]:
    return {
        "hipparcos": _pinned("star-vega"),
        "gaia": _pinned("star-meissa"),
        "openngc": _openngc("NGC0224"),
        "openngc_no_magnitude": _openngc("IC0113"),
    }


def _put(**values: Any) -> Callable[[dict[str, Any]], None]:
    return lambda reference: reference.update(values)


def _drop_magnitude_as(object_type: str) -> Callable[[dict[str, Any]], None]:
    def fault(reference: dict[str, Any]) -> None:
        reference.pop("apparentMagnitude")
        reference["objectType"] = object_type

    return fault


def _distance_method(method: str) -> Callable[[dict[str, Any]], None]:
    return lambda reference: reference["bailerJonesDistancePc"].update(method=method)


# Each rule, the valid sample it breaks, and how
FAULTS: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "a star without a magnitude": ("hipparcos", _drop_magnitude_as("star")),
    "a planet without a magnitude": ("hipparcos", _drop_magnitude_as("planet")),
    "an OpenNGC reference that is not a deep-sky object": ("openngc", _put(objectType="star")),
    "a provider outside the list": ("hipparcos", _put(provider="NED")),
    "an object type outside the list": ("openngc", _put(objectType="nebula")),
    "a right ascension past 360": ("hipparcos", _put(raDeg=360.5)),
    "a declination past -90": ("openngc_no_magnitude", _put(decDeg=-90.5)),
    "an empty common name": ("gaia", _put(commonName="")),
    "a negative parallax error": ("hipparcos", _put(parallaxErrorMas=-0.1)),
    "a distance method outside the list": ("gaia", _distance_method("kinematic")),
    "a magnitude given as null": ("openngc_no_magnitude", _put(apparentMagnitude=None)),
    "a magnitude given as text": ("openngc", _put(apparentMagnitude="3.44")),
}


def designation_edges() -> list[dict[str, Any]]:
    """Each bound the two designation fields have, from the tables: every inside end and one step outside, and the
    wire rules for a number (a whole float is that integer; a boolean, text, minus zero, past 2^53 are refused)."""
    names = [name for _, _, name in DESIGNATION_LETTERS]
    genitives = [genitive for _, _, genitive in DESIGNATION_CONSTELLATIONS]
    digits = [digit for digit, _ in DESIGNATION_INDICES]
    first, last = names[0], names[-1]
    first_gen, last_gen = genitives[0], genitives[-1]
    multiword = next(g for g in genitives if " " in g)
    abbr = DESIGNATION_CONSTELLATIONS[0][0]
    glyph = DESIGNATION_LETTERS[0][1]
    superscript = DESIGNATION_INDICES[0][1]
    bayer = [
        (f"{first} {first_gen}", True),
        (f"{last} {last_gen}", True),
        (f"{first}{digits[0]} {multiword}", True),
        (f"{last}{digits[-1]} {first_gen}", True),
        (f"{first}0 {first_gen}", False),
        (f"{first}{int(digits[-1]) + 1} {first_gen}", False),
        (f"{first}{digits[0]}{digits[0]} {first_gen}", False),
        ("Lamda Orionis", False),
        (f"{first.lower()} {first_gen}", False),
        (f"{first} {first_gen.lower()}", False),
        (f"{first}  {first_gen}", False),
        (f" {first} {first_gen}", False),
        (f"{first} {first_gen} ", False),
        (f"{first} {first_gen}\n", False),
        (f"{first}{first_gen}", False),
        (f"{first} {DESIGNATION_CONSTELLATIONS[0][1]}x", False),
        (f"{first} {abbr}", False),
        (f"{glyph} {abbr}", False),
        (f"{first}{superscript} {first_gen}", False),
        ("", False),
    ]
    flamsteed = [
        (1, True),
        (FLAMSTEED_NUMBER_MAX, True),
        (1.0, True),
        (0, False),
        (FLAMSTEED_NUMBER_MAX + 1, False),
        (1.5, False),
        (True, False),
        ("27", False),
        (-0.0, False),
        (2**53 + 1, False),
    ]
    return [{"field": "bayerDesignation", "value": v, "valid": ok} for v, ok in bayer] + [
        {"field": "flamsteedNumber", "value": v, "valid": ok} for v, ok in flamsteed
    ]


# Bound pairs for an integer rule: the reader's three (an upper bound alone, a negative upper bound alone, a range of two
# values), each kind alone, both, a range of one value, a fractional bound, and a negative range
INTEGER_BOUNDS: list[dict[str, float]] = [
    {"le": 5}, {"le": -10}, {"ge": 1, "le": 2},
    {}, {"ge": 0}, {"gt": 0}, {"ge": -7}, {"gt": -7.5}, {"ge": 1, "le": 140}, {"gt": 0, "le": 3}, {"ge": 4, "le": 4},
    {"ge": 0.5, "le": 2.5}, {"ge": -20, "le": -18}, {"gt": 2.9, "le": 3.2},
]


def integer_edge_table(bounds: dict[str, float]) -> dict[str, Any]:
    """The table's finite numbers for an integer rule with these bounds, the ones a JSON reader takes as numbers."""
    entry = NumberRule("edges", integer=True, **bounds).table_entry()
    finite = [v for v in entry["accept"] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    refused = [v for v in entry["refuse"] if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e300]
    return {"bounds": bounds, "accept": finite, "refuse": refused}


def build_catalog_reference_samples() -> dict[str, Any]:
    valid = valid_samples()
    invalid = []
    for rule, (base, fault) in FAULTS.items():
        broken = copy.deepcopy(valid[base])
        fault(broken)
        invalid.append({"rule": rule, "reference": broken})
    return {
        "providers": list(CATALOG_PROVIDERS),
        "objectTypes": list(CATALOG_OBJECT_TYPES),
        "distanceMethods": list(CATALOG_DISTANCE_METHODS),
        "valid": list(valid.values()),
        "invalid": invalid,
        # A key the contract does not name is dropped by both sides, not refused: the reference parses to this
        "droppedKey": {"reference": {**valid["openngc"], **UNNAMED_KEY}, "parsed": valid["openngc"]},
        # The two designation fields' bounds, each set on the Hipparcos sample
        "designationEdges": designation_edges(),
        # The solution field table's integer edge samples for bounds nobody has written yet, which the TS side judges
        # with its own integer schema (catalog-2b v3)
        "integerEdgeTables": [integer_edge_table(bounds) for bounds in INTEGER_BOUNDS],
    }


def test_catalog_reference_fixture_is_current() -> None:
    samples = build_catalog_reference_samples()
    rendered = json.dumps(samples, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(rendered, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == rendered, (
        f"{FIXTURE.name} no longer matches the Python catalog reference contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_catalog_reference_contract.py"
    )


def test_the_mirror_accepts_every_valid_sample_unchanged() -> None:
    for sample in valid_samples().values():
        parsed = CatalogReference.model_validate(sample)
        assert parsed.model_dump(by_alias=True, exclude_none=True) == sample


@pytest.mark.parametrize("rule", FAULTS)
def test_the_mirror_refuses_every_fault(rule: str) -> None:
    base, fault = FAULTS[rule]
    broken = copy.deepcopy(valid_samples()[base])
    fault(broken)
    with pytest.raises(ValidationError):
        CatalogReference.model_validate(broken)


@pytest.mark.parametrize("edge", designation_edges(), ids=lambda e: f"{e['field']}={e['value']!r}")
def test_each_designation_bound_is_taken_or_refused_as_its_edge_table_row_says(edge: dict[str, Any]) -> None:
    sample = {**valid_samples()["hipparcos"], edge["field"]: edge["value"]}
    if edge["valid"]:
        parsed = CatalogReference.model_validate(sample).model_dump(by_alias=True, exclude_none=True)
        assert parsed[edge["field"]] == edge["value"]
    else:
        with pytest.raises(ValidationError):
            CatalogReference.model_validate(sample)


def test_a_key_the_contract_does_not_name_is_dropped_as_zod_drops_it() -> None:
    sample = valid_samples()["openngc"]
    parsed = CatalogReference.model_validate({**sample, **UNNAMED_KEY})
    assert parsed.model_dump(by_alias=True, exclude_none=True) == sample


def test_every_pinned_star_reference_is_a_valid_reference() -> None:
    refused = {}
    for star in ALL_SKY_CATALOG_STARS:
        try:
            CatalogReference.model_validate(star.to_catalog_reference_dict())
        except ValidationError as error:
            refused[star.id] = str(error)
    assert refused == {}
    assert len(ALL_SKY_CATALOG_STARS) >= 70


@settings(max_examples=200, deadline=None)
@given(
    object_type=st.sampled_from(CATALOG_OBJECT_TYPES),
    provider=st.sampled_from(CATALOG_PROVIDERS),
    with_magnitude=st.booleans(),
)
@example(object_type="deep_sky", provider="OpenNGC", with_magnitude=False)
@example(object_type="star", provider="OpenNGC", with_magnitude=True)
@example(object_type="star", provider="Hipparcos", with_magnitude=False)
@example(object_type="deep_sky", provider="SIMBAD", with_magnitude=False)
@example(object_type="moon", provider="JPL Horizons", with_magnitude=False)
def test_pbt_magnitude_and_openngc_belong_to_deep_sky_objects_only(
    object_type: str, provider: str, with_magnitude: bool
) -> None:
    reference = copy.deepcopy(valid_samples()["openngc"])
    reference.update(objectType=object_type, provider=provider)
    if not with_magnitude:
        reference.pop("apparentMagnitude")
    allowed = (with_magnitude or object_type == "deep_sky") and (provider != "OpenNGC" or object_type == "deep_sky")
    try:
        CatalogReference.model_validate(reference)
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted == allowed


def test_the_solution_field_table_holds_the_flamsteed_numbers_own_edge_and_a_counts_largest_safe_integer() -> None:
    # The table code on main gave every integer rule the largest safe integer as an accepted edge, right for a count
    # with no upper bound and wrong for 1 to 140; with an upper bound the edge is the bound and one past it
    table = json.loads((ROOT / "tests" / "fixtures" / "observation_solution_contract.json").read_text(encoding="utf-8"))
    flamsteed, count = table["rules"]["flamsteedNumber"], table["rules"]["count"]
    assert (flamsteed["ge"], flamsteed["le"], flamsteed["integer"]) == (FLAMSTEED_NUMBER_MIN, FLAMSTEED_NUMBER_MAX, True)
    assert FLAMSTEED_NUMBER_MAX in flamsteed["accept"] and 9007199254740991 not in flamsteed["accept"]
    assert FLAMSTEED_NUMBER_MAX + 1 in flamsteed["refuse"] and float(FLAMSTEED_NUMBER_MAX + 1) in flamsteed["refuse"]
    assert 9007199254740991 in count["accept"] and "le" not in count
    base = copy.deepcopy(next(iter(valid_samples().values())))
    CatalogReference.model_validate({**base, "flamsteedNumber": FLAMSTEED_NUMBER_MAX})
    for beyond in (FLAMSTEED_NUMBER_MAX + 1, float(FLAMSTEED_NUMBER_MAX + 1), 9007199254740991):
        with pytest.raises(ValidationError):
            CatalogReference.model_validate({**base, "flamsteedNumber": beyond})



def _judged_alike(rule: NumberRule) -> None:
    entry = rule.table_entry()
    wrong_accept = [v for v in entry["accept"] if rule.problem(v) is not None]
    wrong_refuse = [v for v in entry["refuse"] if rule.problem(v) is None]
    assert wrong_accept == [] and wrong_refuse == [], (rule, wrong_accept, wrong_refuse)


@pytest.mark.parametrize(
    ("bounds", "never_refused", "never_accepted"),
    [({"le": 5}, [-4], []), ({"le": -10}, [], [-3, -1.0]), ({"ge": 1, "le": 2}, [], [3.0])],
    ids=["an upper bound of 5 alone", "an upper bound of -10 alone", "from 1 to 2"],
)
def test_the_readers_integer_rules_get_edge_samples_their_own_judgment_agrees_with(
    bounds: dict[str, float], never_refused: list[float], never_accepted: list[float]
) -> None:
    # AUDIT K's three cases: the table code main had gave each a sample its rule contradicts
    rule = NumberRule("edges", integer=True, **bounds)
    _judged_alike(rule)
    entry = rule.table_entry()
    assert not [v for v in never_refused if v in entry["refuse"]] and not [v for v in never_accepted if v in entry["accept"]]


@st.composite
def integer_rules(draw: st.DrawFn) -> NumberRule:
    """An integer rule with bounds drawn at random: either one missing, either one negative or fractional, the range as
    narrow as one whole number, never empty."""
    lower = draw(st.sampled_from(["none", "ge", "gt"]))
    upper = draw(st.booleans())
    bound = st.one_of(st.integers(min_value=-60, max_value=60), st.integers(min_value=-120, max_value=120).map(lambda n: n / 4))
    kwargs: dict[str, float] = {}
    if lower != "none":
        kwargs[lower] = draw(bound)
    if upper:
        low_whole = None if lower == "none" else (math.floor(kwargs[lower]) + 1 if lower == "gt" else math.ceil(kwargs[lower]))
        kwargs["le"] = draw(bound if low_whole is None else st.integers(min_value=low_whole, max_value=low_whole + 40).flatmap(
            lambda whole: st.sampled_from([whole, whole + 0.25, whole + 0.75])))
    return NumberRule("drawn", integer=True, **kwargs)


@settings(max_examples=300, deadline=None)
@given(rule=integer_rules())
@example(rule=NumberRule("drawn", integer=True))
@example(rule=NumberRule("drawn", integer=True, le=5))
@example(rule=NumberRule("drawn", integer=True, le=-10))
@example(rule=NumberRule("drawn", integer=True, ge=1, le=2))
@example(rule=NumberRule("drawn", integer=True, ge=4, le=4))
@example(rule=NumberRule("drawn", integer=True, gt=2.9, le=3.2))
def test_pbt_an_integer_rules_edge_samples_are_what_its_own_judgment_says_for_any_bounds(rule: NumberRule) -> None:
    _judged_alike(rule)
    # And the edges are there, found from the rule's own judgment rather than from its bounds: the lowest whole number
    # it accepts and the one below it, the highest and the one above it (as an int and as a whole float), an accepted
    # whole float, the largest safe integer on an unbounded side, and a number that is not whole
    entry = rule.table_entry()
    accepted = [n for n in range(-200, 201) if rule.problem(n) is None]

    def has_int(values: list[object], n: int) -> bool:
        return any(type(v) is int and v == n for v in values)

    def has_float(values: list[object], x: float) -> bool:
        return any(type(v) is float and v == x for v in values)

    bounded_below = rule.problem(-200) is not None
    bounded_above = rule.problem(200) is not None
    assert any(type(v) is float and v.is_integer() for v in entry["accept"]), entry
    if bounded_below:
        assert has_int(entry["accept"], accepted[0]) and has_int(entry["refuse"], accepted[0] - 1), (accepted[0], entry)
    if bounded_above:
        assert has_int(entry["accept"], accepted[-1]) and has_int(entry["refuse"], accepted[-1] + 1), (accepted[-1], entry)
        assert has_float(entry["refuse"], float(accepted[-1] + 1)), (accepted[-1], entry)
        if not bounded_below:
            assert has_int(entry["accept"], -9007199254740991), entry
    else:
        assert has_int(entry["accept"], 9007199254740991), entry
    assert any(type(v) is float and not v.is_integer() for v in entry["refuse"]), entry


def test_the_counts_edge_samples_are_as_they_were() -> None:
    # The counts' samples stay byte for byte (the fixture's rows on main): a lower bound alone keeps its old samples
    from services.contracts.astronomy import COUNT, POSITIVE_COUNT

    assert (COUNT.table_entry()["accept"][:3], COUNT.table_entry()["refuse"][:3]) == ([0, 2.0, 9007199254740991], [-1, 0.5, 1.5])
    assert (POSITIVE_COUNT.table_entry()["accept"][:3], POSITIVE_COUNT.table_entry()["refuse"][:3]) == ([1, 3.0, 9007199254740991], [0, 1.5, 2.5])


def test_the_fixtures_integer_edge_tables_are_judged_by_each_rule() -> None:
    for table in json.loads(FIXTURE.read_text(encoding="utf-8"))["integerEdgeTables"]:
        rule = NumberRule("edges", integer=True, **table["bounds"])
        assert all(rule.problem(v) is None for v in table["accept"]) and all(rule.problem(v) is not None for v in table["refuse"]), table
