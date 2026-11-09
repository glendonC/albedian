"""The brightest star in a photo, at the voice backend.

Which star is brightest in the photo is read from the brightness measured in its pixels (each finding's
sourceDetection flux), and the backend receives only ids and the view state, never the findings. So the backend
refuses the whole "brightest" family plainly, with or without a selection, before any model is asked; it never
describes the selected star in its place. The app's own parser answers it (packages/observation/src/voiceParser.ts,
voiceParser.brightest.test.ts), and both read the family with the same pattern.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.api import app
from services.voice import intent
from services.voice.intent import check_voice_command

ROOT = Path(__file__).resolve().parents[2]
YEAR = 2026

# Made-up values that point at no real person, place, time or file
BARE: dict[str, Any] = {
    "sessionId": "s-made-up",
    "sourceAssetId": "src_made-up",
    "viewRevision": 4,
    "calibrationState": "calibrated",
}

BRIGHTEST = (
    "what is the brightest star in my photo",
    "What is the brightest star in my photo?",
    "brightest star",
    "which is the brightest star here",
    "show me the brightest one",
    "select the brightest star in the picture",
    "tell me about the brightest star in the photo",
    "take me to the brightest star",
    "what is the brightest star in Orion",
    "the brightest star in the sky",
    "what is the second brightest star in my photo",
    "is Sirius the brightest star",
)


@pytest.mark.parametrize("selected", [None, "finding-star-sirius", "finding-star-vega"])
@pytest.mark.parametrize("utterance", BRIGHTEST)
def test_the_backend_refuses_the_brightest_family_plainly_and_never_takes_the_selection(
    utterance: str, selected: str | None
) -> None:
    reply = check_voice_command(utterance, {**BARE, "selectedFindingId": selected}, current_year=YEAR)
    assert reply["status"] == "rejected"
    assert reply["reason"] == "no_grounded_answer"
    assert reply["explanation"] == intent.BRIGHTEST_REFUSAL
    assert "proposedAction" not in reply


def test_no_model_is_asked_for_a_brightest(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_model(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a model was asked to rank brightness it cannot see")

    monkeypatch.setattr(intent, "propose_action_with_claude", no_model)
    reply = check_voice_command("what is the brightest star in my photo", BARE, current_year=YEAR)
    assert reply["reason"] == "no_grounded_answer"


def test_the_route_refuses_it_too() -> None:
    response = TestClient(app).post(
        "/api/voice/command",
        json={"utterance": "what is the brightest star in my photo", "context": {**BARE, "selectedFindingId": "finding-star-sirius"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["reason"], body["explanation"]) == ("rejected", "no_grounded_answer", intent.BRIGHTEST_REFUSAL)


def test_the_refusal_says_why_in_plain_words() -> None:
    assert "measured" in intent.BRIGHTEST_REFUSAL
    for refusal in (intent.BRIGHTEST_REFUSAL, intent.BRIGHTEST_KIND_REFUSAL):
        assert not re.search(r"[\u2013\u2014]", refusal)


UNRANKED = (
    "what is the brightest planet in my photo",
    "brightest galaxy",
    "the brightest nebula here",
    "take me to the brightest cluster",
    "which is the brightest moon",
    "brightest deep-sky object",
    "the brightest deep sky object",
)


@pytest.mark.parametrize("selected", [None, "finding-star-sirius"])
@pytest.mark.parametrize("utterance", UNRANKED)
def test_a_brightest_planet_moon_galaxy_nebula_or_cluster_is_refused_with_its_reason(
    utterance: str, selected: str | None
) -> None:
    reply = check_voice_command(utterance, {**BARE, "selectedFindingId": selected}, current_year=YEAR)
    assert (reply["status"], reply["reason"]) == ("rejected", "no_grounded_answer")
    assert reply["explanation"] == intent.BRIGHTEST_KIND_REFUSAL


def test_both_parsers_list_the_same_unranked_kinds() -> None:
    source = (ROOT / "packages" / "observation" / "src" / "voiceParser.ts").read_text(encoding="utf-8")
    block = source[source.index("export const BRIGHTEST_UNRANKED_KINDS") :]
    block = block[: block.index("];")]
    assert tuple(re.findall(r"'([^']+)'", block)) == intent.BRIGHTEST_UNRANKED_KINDS


@pytest.mark.parametrize(
    ("utterance", "kind"),
    [("brighten the photo", "media.restore"), ("select vega", "finding.select")],
)
def test_words_near_brightest_keep_their_own_rules(utterance: str, kind: str) -> None:
    reply = check_voice_command(utterance, {**BARE, "selectedFindingId": None}, current_year=YEAR)
    assert reply["status"] == "proposed"
    assert reply["proposedAction"]["type"] == kind


def test_both_parsers_read_the_family_with_the_same_pattern() -> None:
    source = (ROOT / "packages" / "observation" / "src" / "voiceParser.ts").read_text(encoding="utf-8")
    found = re.search(r"export const BRIGHTEST_PATTERN = /(.+)/(\w*);", source)
    assert found is not None
    ts_pattern, ts_flags = found.group(1), found.group(2)
    # The TS pattern ignores case; the backend lowercases the words first
    assert (ts_pattern, ts_flags) == (intent.BRIGHTEST.pattern, "i")
    for utterance in (*BRIGHTEST, "brighten the photo", "brightests", "the brightness"):
        assert bool(re.search(ts_pattern, utterance, re.IGNORECASE)) == bool(intent.BRIGHTEST.search(utterance.lower()))
