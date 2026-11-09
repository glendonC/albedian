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
import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts.catalog_reference import (
    CATALOG_DISTANCE_METHODS,
    CATALOG_OBJECT_TYPES,
    CATALOG_PROVIDERS,
    CatalogReference,
)
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
    }


def test_catalog_reference_fixture_is_current() -> None:
    samples = build_catalog_reference_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == samples, (
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
