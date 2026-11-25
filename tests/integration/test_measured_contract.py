"""wait-detections-one: the measured route's payload (services/contracts/measured.py) and its TypeScript mirror read
one shared sample alike (tests/fixtures/measured_detections_contract.json, written here with
ALBEDIAN_UPDATE_MEASURED_FIXTURE=1, read by packages/contracts/src/__tests__/measured.test.ts)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from services.contracts.measured import MeasuredDetections

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "measured_detections_contract.json"
SHA = "4f055db773c14698" + "a" * 48


def _cases() -> list[dict]:
    solution = json.loads((ROOT / "tests" / "fixtures" / "observation_solution_contract.json").read_text(encoding="utf-8"))["valid"]["calibrated"]
    detections = [f["sourceDetection"] for f in solution["findings"] if f.get("sourceDetection")]
    first = detections[0]
    rows = [
        ("the calibrated sample's detections", {"sourceAssetSha256": SHA, "detections": detections}),
        ("none measured", {"sourceAssetSha256": SHA, "detections": []}),
        ("an unknown key", {"sourceAssetSha256": SHA, "detections": detections, "extra": 1}),
        ("a short hash", {"sourceAssetSha256": "abc", "detections": detections}),
        ("a detection with no position", {"sourceAssetSha256": SHA, "detections": [{k: v for k, v in first.items() if k != "xPx"}]}),
        ("a detection at a negative position", {"sourceAssetSha256": SHA, "detections": [{**first, "xPx": -1.0}]}),
    ]

    def accepted(value: dict) -> bool:
        try:
            MeasuredDetections.model_validate(value)
        except ValidationError:
            return False
        return True

    return [{"what": what, "value": value, "accepted": accepted(value)} for what, value in rows]


def test_the_payload_is_read_alike_and_held_in_the_shared_fixture() -> None:
    cases = _cases()
    assert {c["what"]: c["accepted"] for c in cases} == {
        "the calibrated sample's detections": True, "none measured": True, "an unknown key": False,
        "a short hash": False, "a detection with no position": False, "a detection at a negative position": False,
    }
    text = json.dumps({"about": "wait-detections-one: the measured route's payload, read alike", "cases": cases}, indent=1) + "\n"
    if os.environ.get("ALBEDIAN_UPDATE_MEASURED_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, "regenerate with ALBEDIAN_UPDATE_MEASURED_FIXTURE=1"
