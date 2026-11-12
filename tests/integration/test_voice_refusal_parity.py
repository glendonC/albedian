"""Both parsers give the same refusal kind and the same sentence for every one of 568 sentences.

The sentences are the 217 natural sentences of packages/observation/src/__tests__/voiceParser.namedTargets.test.ts
(its SHARED_JSON block) and the 351 of tests/fixtures/voice_everyday_sentences.json. Each is read by the backend in
three views of the backend's own solutions (tests/fixtures/python_contract_solutions.json), and the readings are
written to tests/fixtures/voice_refusal_parity.json, which
packages/observation/src/__tests__/voiceParser.refusalParity.test.ts reads to hold the app's parser to the same
kinds and sentences. Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_VOICE_REFUSAL_PARITY=1 uv run pytest tests/integration/test_voice_refusal_parity.py

Every context here is made up and points at no real person, place, time or file.

Covers:
- The readings are current and every sentence is held once
- Every refusal the backend gives names its kind, a kind the backend can know, with its reason and its sentence
- Every ambiguous_target refusal says its kind
- Where one parser refuses and the other forms a question or a command (116 sentence-views, written by the app
  parser's test to tests/fixtures/voice_refusal_one_sided.json), the backend's half of each is its own reading
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

import pytest

from services.contracts import voice as contract
from services.voice import claude
from services.voice.intent import check_voice_command

ROOT = Path(__file__).resolve().parents[2]
TS_TEST = ROOT / "packages" / "observation" / "src" / "__tests__" / "voiceParser.namedTargets.test.ts"
EVERYDAY = ROOT / "tests" / "fixtures" / "voice_everyday_sentences.json"
SOLUTIONS: dict[str, dict[str, Any]] = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text())
FIXTURE = ROOT / "tests" / "fixtures" / "voice_refusal_parity.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_VOICE_REFUSAL_PARITY"
ONE_SIDED = ROOT / "tests" / "fixtures" / "voice_refusal_one_sided.json"
YEAR = 2101  # a clock year no code path may assume
_SHARED = re.search(r"const SHARED_JSON = String\.raw`(.*?)`;", TS_TEST.read_text(encoding="utf-8"), re.S)
assert _SHARED is not None, "the shared rows are missing from the app parser's test"
NATURAL: list[str] = json.loads(_SHARED.group(1))["natural"]
SENTENCES: list[str] = NATURAL + [row["text"] for row in json.loads(EVERYDAY.read_text())["sentences"]]
# A solved sky with a star selected, a predicted sky with a body selected, and a solved sky with nothing in view
VIEWS = [("plate_calibrated", "finding-star-rigel"), ("horizon_predicted_only", "finding-predicted-saturn"),
         ("plate_calibrated", None)]


def _state(sample: str) -> str:
    status = SOLUTIONS[sample]["status"]
    return {"pending": "not_requested", "approximate": "predicted_only"}.get(status, status)


def _reading(reply: dict[str, Any]) -> dict[str, Any]:
    """What the backend made of a sentence: the action's type and target, the question, or the refusal."""
    if reply["status"] == "proposed":
        action = reply["proposedAction"]
        reading: dict[str, Any] = {"status": "proposed", "action": action["type"]}
        if "findingId" in action:
            reading["target"] = action["findingId"]
        return reading
    if reply["status"] == "needs_input":
        return {"status": "needs_input", "choices": [choice["id"] for choice in reply["choices"]]}
    reading = {"status": "rejected", "reason": reply["reason"], "explanation": reply["explanation"]}
    if "refusal" in reply:
        reading["refusal"] = reply["refusal"]
    return reading


def build_readings() -> dict[str, Any]:
    """The rules' readings with no model path: the live switch and the key are taken away for the reading only, and
    the test framework's patching puts them back as they were."""
    views = []
    with pytest.MonkeyPatch.context() as patch:
        for key in (claude.ENABLE_ENV, claude.API_KEY_ENV):
            patch.delenv(key, raising=False)
        for sample, selected in VIEWS:
            context = {"sessionId": "s-made-up", "sourceAssetId": "src_made-up", "viewRevision": 4,
                       "calibrationState": _state(sample), "selectedFindingId": selected}
            readings = [_reading(check_voice_command(text, context, current_year=YEAR)) for text in SENTENCES]
            views.append({"sample": sample, "selected": selected, "readings": readings})
    return {"sentences": SENTENCES, "views": views}


def test_the_readings_are_current_and_every_sentence_is_held_once() -> None:
    assert len(NATURAL) == 217 and len(SENTENCES) == len(set(SENTENCES)) == 568
    text = json.dumps(build_readings(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_ENV}=1"


def test_every_backend_refusal_names_its_kind_its_reason_and_its_sentence() -> None:
    kinds: dict[str, int] = {}
    for view in build_readings()["views"]:
        for reading in view["readings"]:
            if reading["status"] != "rejected":
                continue
            refusal = reading.get("refusal")
            if reading["reason"] == "ambiguous_target":
                assert refusal is not None, reading
            if refusal is None:
                continue
            spec = contract.REFUSAL_KINDS[refusal["kind"]]
            assert spec["backend"] and reading["reason"] in spec["reasons"], reading
            assert reading["explanation"] == contract.refusal_sentence(refusal), reading
            kinds[refusal["kind"]] = kinds.get(refusal["kind"], 0) + 1
    # The 568 sentences reach four of the five kinds the backend can give over the three views; no_named_row (the
    # Magellanic Clouds, Eta Carinae) is said by none of them and is held by the contract fixture instead
    assert kinds == {"not_understood": 177, "which_one": 12, "deep_sky_out_of_reach": 6, "nothing_in_view": 57}, kinds


def test_the_backend_half_of_every_one_sided_reading_is_its_own() -> None:
    doc = json.loads(ONE_SIDED.read_text(encoding="utf-8"))
    readings = doc["readings"]
    assert doc["count"] == len(readings) == 116
    assert sum(r["refuses"] == "app" for r in readings) == 55 and sum(r["refuses"] == "backend" for r in readings) == 61
    built = build_readings()["views"]
    places = []
    for reading in readings:
        view = VIEWS.index((reading["sample"], reading["selected"]))
        index = SENTENCES.index(reading["sentence"])
        places.append((view, index))
        assert reading["backend"] == built[view]["readings"][index], reading["sentence"]
        assert set(reading) == {"sample", "selected", "sentence", "refuses", "app", "backend"}, reading["sentence"]
        if reading["refuses"] == "backend":
            assert "refusal" in reading["backend"] and "refusal" not in reading["app"], reading["sentence"]
        else:
            assert reading["refuses"] == "app", reading["sentence"]
            assert "refusal" in reading["app"] and "refusal" not in reading["backend"], reading["sentence"]
    assert places == sorted(set(places)), "each sentence-view once, in the views' and the sentences' order"


def test_reading_the_sentences_leaves_the_environment_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(claude.ENABLE_ENV, "0")
    monkeypatch.setenv(claude.API_KEY_ENV, "test-key-not-a-secret")
    build_readings()
    assert (os.environ.get(claude.ENABLE_ENV), os.environ.get(claude.API_KEY_ENV)) == ("0", "test-key-not-a-secret")
