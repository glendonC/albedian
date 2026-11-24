"""The flight rule (flight-rule-0, DECIDED-BY-LEAD 24 Nov 12:55): which findings the app may fly to, and the refusal
kind of each it may not, the same in both languages (packages/contracts/src/astronomy.ts `flightRefusal`).

A finding may be flown to when it is catalog_matched, or predicted with a catalog reference, and its distance interval
is a usable one whose method is a star catalog's; an ephemeris distance never qualifies, and the Sun, the Moon and the
planets are refused as beyond the flight's reach. Nothing here changes a finding or its class.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from services.contracts import astronomy as contract
from services.contracts import voice
from services.contracts.sky_context import SKY_CONTEXT_STAR_DISTANCE_METHODS
from services.science.ephemeris import BODY_NAMES
from services.science.predicted_sky import predicted_sky_findings

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "observation_solution_contract.json"


def test_the_star_catalog_methods_are_the_three_star_catalog_distances() -> None:
    assert contract.STAR_CATALOG_DISTANCE_METHODS == (
        "geometric_parallax",
        "photogeometric_bailer_jones_edr3",
        "geometric_bailer_jones_edr3",
    )
    assert "ephemeris_au" not in contract.STAR_CATALOG_DISTANCE_METHODS
    assert set(contract.STAR_CATALOG_DISTANCE_METHODS) == set(SKY_CONTEXT_STAR_DISTANCE_METHODS)


def test_the_solar_system_bodies_are_the_ephemeris_table() -> None:
    assert contract.SOLAR_SYSTEM_BODY_KEYS == tuple(name.lower() for name in BODY_NAMES)
    for key in contract.SOLAR_SYSTEM_BODY_KEYS:
        assert contract.is_solar_system_body_id(f"finding-predicted-{key}")
        assert contract.is_solar_system_body_id(f"finding-observed-{key}")
    for other in ("finding-predicted-star-rigel", "finding-star-sirius", "finding-obs-3", "finding-openngc-NGC5904",
                  "finding-predicted-pluto", "finding-predicted-moonlet", "finding-predicted-", "moon"):
        assert not contract.is_solar_system_body_id(other), other


@pytest.mark.parametrize("sample", contract.flight_rule_samples(), ids=lambda sample: sample["about"])
def test_each_sample_flies_or_is_refused_as_ruled(sample: dict[str, Any]) -> None:
    finding = sample["finding"]
    before = copy.deepcopy(finding)
    assert contract.flight_refusal(finding) == sample["refusal"]
    # Judging a finding never changes it, its class least of all
    assert finding == before


def test_the_samples_cover_every_verdict_and_every_class() -> None:
    samples = contract.flight_rule_samples()
    assert {sample["refusal"] for sample in samples} == {None, "no_catalog_distance", "beyond_solar_system_only"}
    assert {sample["finding"]["evidenceClass"] for sample in samples} == set(contract.FINDING_CLASS_EVIDENCE)
    assert {sample["refusal"] for sample in samples} - {None} <= set(voice.REFUSAL_KINDS)


def test_the_shared_fixture_carries_the_rule() -> None:
    table = json.loads(FIXTURE.read_text(encoding="utf-8"))["flightRule"]
    assert table == json.loads(json.dumps(contract.flight_rule_table()))


def test_the_two_flight_refusals_read_as_ruled() -> None:
    assert voice.refusal_sentence({"kind": "no_catalog_distance", "name": "Zosma"}) == (
        "Zosma has no distance in the app's catalogs, so it cannot be flown to."
    )
    assert voice.refusal_sentence({"kind": "no_catalog_distance", "name": "the Pearl Cluster"}) == (
        "The Pearl Cluster has no distance in the app's catalogs, so it cannot be flown to."
    )
    for media in ("photo", "video"):
        assert voice.refusal_sentence({"kind": "beyond_solar_system_only"}, media) == (
            "The flight goes only to stars beyond our solar system."
        )
    for kind in ("no_catalog_distance", "beyond_solar_system_only"):
        spec = voice.REFUSAL_KINDS[kind]
        assert spec["reasons"] == ["no_distance_estimate"] and spec["said"] is False and spec["catalogRow"] is False
    assert voice.REFUSAL_KINDS["no_catalog_distance"]["name"] is True
    assert voice.REFUSAL_KINDS["beyond_solar_system_only"]["name"] is False


def test_the_catalog_refusals_are_spelled_catalog() -> None:
    lmc = voice.refusal_sentence(
        {"kind": "deep_sky_out_of_reach", "said": "lmc", "name": "the Large Magellanic Cloud", "catalogRow": "ESO056-115"}
    )
    assert "the app's deep-sky catalog (OpenNGC ESO056-115)" in lmc and "catalogue" not in lmc
    eta = voice.refusal_sentence({"kind": "no_named_row", "said": "eta car", "name": "Eta Carinae"})
    assert "the app's catalogs yet" in eta and "catalogue" not in eta


def test_a_predicted_sky_on_real_catalog_data_flies_its_catalogued_stars_only() -> None:
    # A public place and moment (the Royal Observatory, Greenwich, on a winter evening), not any recording's
    records = predicted_sky_findings(
        "2025-01-15T20:00:00Z", 51.4769, -0.0005,
        solution_id="sol-flight-probe", source_sha256=hashlib.sha256(b"flight-rule-probe").hexdigest(),
    )
    wire = [record.to_contract_dict(source_asset_id="asset-flight-probe") for record in records]
    verdicts = {finding["id"]: contract.flight_refusal(finding) for finding in wire}
    flies = [f for f in wire if verdicts[f["id"]] is None]
    bodies = [f for f in wire if contract.is_solar_system_body_id(f["id"])]
    assert flies and bodies
    for finding in flies:
        assert finding["evidenceClass"] == "predicted" and "catalogRef" in finding
        assert finding["inferredDistancePc"]["method"] in contract.STAR_CATALOG_DISTANCE_METHODS
    for finding in bodies:
        assert verdicts[finding["id"]] == "beyond_solar_system_only"
    for finding in wire:
        if finding not in flies and finding not in bodies:
            assert verdicts[finding["id"]] == "no_catalog_distance"
            assert finding.get("inferredDistancePc") is None
