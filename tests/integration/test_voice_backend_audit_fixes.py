"""Backend voice audit regression suite (voice-backend-fixes-1):
1. Unix-epoch pointer freshness, non-finite timestamp rejection, and 60 s future clock-skew guard (REQ-VNA-1.3, PBT-VNA-3).
2. Deictic pointer disambiguation on space.viewpoint when multiple candidates overlap (REQ-VNA-1.4).
3. Strict ActionContext / PointerContext wire validation on POST /api/voice/command (no fractional/boolean viewRevision bypass of stale_revision, no 500s; an absent context reads as empty and a null one is refused, as the voice contract says).
4. Predicted-sky star composition in claude.compose_action and catalog scientificExplanation on finding-predicted-* pinned stars.
5. Bayer, Flamsteed, OpenNGC (common name, NGC/IC, Messier), Solar System (Mercury, Neptune, Pluto), and predicted-IAU star target resolution parity in answers.py without leaking selectedFindingId on absent targets.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
from hypothesis import example, given, settings, strategies as st
import pytest

from services.api import app
from services.contracts import voice as contract
from services.voice import answers, claude
from services.voice.intent import ActionContext, check_voice_command, parse_voice_intent

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS: dict[str, dict[str, Any]] = json.loads(
    (ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8")
)
CALIBRATED = SOLUTIONS["plate_calibrated"]
RIGEL = "finding-star-rigel"
YEAR = 2101


@pytest.fixture(autouse=True)
def _live_model_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (claude.ENABLE_ENV, claude.API_KEY_ENV):
        monkeypatch.delenv(key, raising=False)


def _epoch_ctx(at_ms: float, *, rev: int = 4) -> ActionContext:
    return ActionContext.from_dict(
        {
            "viewRevision": rev,
            "calibrationState": "calibrated",
            "selectedFindingId": "finding-star-vega",
            "focusedFindingId": "finding-star-sirius",
            "recentPointer": {
                "xPx": 100.0,
                "yPx": 200.0,
                "atTimestampMs": at_ms,
                "viewRevision": rev,
                "nearestFindingId": "finding-star-rigel",
                "overlappingCandidateIds": ["finding-star-rigel"],
            },
        }
    )


@settings(max_examples=200, deadline=None)
@given(delta_ms=st.floats(min_value=-200_000.0, max_value=200_000.0, allow_nan=False, allow_infinity=False))
@example(delta_ms=0.0)
@example(delta_ms=10_000.0)
@example(delta_ms=10_000.5)
@example(delta_ms=-60_000.0)
@example(delta_ms=-60_000.5)
def test_pbt_unix_epoch_pointer_freshness_and_clock_skew_guard(delta_ms: float) -> None:
    now_ms = 1_795_500_000_000.0
    ctx = _epoch_ctx(now_ms - delta_ms)
    got = claude.fresh_pointer(ctx, now_ms=now_ms)
    expected_fresh = -60_000.0 <= delta_ms <= 10_000.0
    assert (got is not None) == expected_fresh, (delta_ms, got)


def test_stale_or_future_unix_epoch_pointer_is_ignored_in_deictic_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now_sec = 1_795_500_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_now_sec)
    now_ms = fixed_now_sec * 1000.0

    fresh_ctx = _epoch_ctx(now_ms - 5_000.0)
    parsed_fresh = parse_voice_intent("what is that one?", fresh_ctx, current_year=YEAR)
    assert parsed_fresh.action is not None and parsed_fresh.action["findingId"] == "finding-star-rigel"

    stale_ctx = _epoch_ctx(now_ms - 15_000.0)
    parsed_stale = parse_voice_intent("what is that one?", stale_ctx, current_year=YEAR)
    assert parsed_stale.action is not None and parsed_stale.action["findingId"] == "finding-star-vega"
    assert json.loads(claude.user_turn("what is that one?", stale_ctx))["state"]["pointer"] is None

    skewed_ctx = _epoch_ctx(now_ms + 90_000.0)
    parsed_skewed = parse_voice_intent("what is that one?", skewed_ctx, current_year=YEAR)
    assert parsed_skewed.action is not None and parsed_skewed.action["findingId"] == "finding-star-vega"


def test_space_viewpoint_disambiguates_overlapping_pointer_candidates() -> None:
    overlapping = {
        "viewRevision": 4,
        "calibrationState": "calibrated",
        "selectedFindingId": "finding-star-vega",
        "recentPointer": {
            "xPx": 100.0,
            "yPx": 200.0,
            "atTimestampMs": 5.0,
            "viewRevision": 4,
            "nearestFindingId": "finding-obs-1",
            "overlappingCandidateIds": ["finding-obs-1", "finding-obs-2"],
        },
    }
    for utterance in ("orbit that one", "shift viewpoint around it", "another viewpoint"):
        reply = check_voice_command(utterance, overlapping, current_year=YEAR)
        contract.VOICE_COMMAND_RESPONSE.validate_python(reply)
        assert reply["status"] == "needs_input", (utterance, reply)
        assert [c["id"] for c in reply["choices"]] == ["finding-obs-1", "finding-obs-2"]

    named_reply = check_voice_command("orbit Vega", overlapping, current_year=YEAR)
    assert named_reply["status"] == "proposed"
    assert named_reply["proposedAction"]["targetFindingId"] == "finding-star-vega"


def test_malformed_context_returns_422_without_echo_and_never_bypasses_stale_revision() -> None:
    client = TestClient(app)
    absent = client.post("/api/voice/command", json={"utterance": "stop"})
    assert absent.status_code == 200 and absent.json()["status"] == "proposed"
    # The voice contract's own bad sample: an optional context is absent, never null
    contract.VOICE_COMMAND_REQUEST.validate_python({"utterance": "stop"})
    with pytest.raises(ValueError):
        contract.VOICE_COMMAND_REQUEST.validate_python({"utterance": "stop", "context": None})
    null = client.post("/api/voice/command", json={"utterance": "stop", "context": None})
    assert null.status_code == 422, null.text

    secret_bad = "secret-not-an-int-revision"
    bad_contexts: list[dict[str, Any]] = [
        {"viewRevision": 1.9},
        {"viewRevision": True},
        {"viewRevision": False},
        {"viewRevision": -1},
        {"viewRevision": None},
        {"viewRevision": secret_bad},
        {"sourceTimeSec": -0.5},
        {"sourceTimeSec": True},
        {"sourceTimeSec": "bad"},
        {"reducedMotion": "yes"},
        {"selectedFindingId": 123},
        {"focusedFindingId": ["bad"]},
        {"observerLat": 95.0},
        {"observerLon": -190.0},
        {"observerLat": True},
        {"recentPointer": "not-an-object"},
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1.5}},
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1, "nearestFindingId": 9}},
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1, "overlappingCandidateIds": "bad"}},
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1, "overlappingCandidateIds": [1]}},
    ]
    for bad in bad_contexts:
        with pytest.raises(ValueError):
            ActionContext.from_dict(bad)
        resp = client.post(
            "/api/voice/command",
            json={
                "utterance": "stop",
                "context": bad,
                "proposedAction": {"type": "navigation.cancel", "requestId": "req-1", "expectedViewRevision": 1},
            },
        )
        assert resp.status_code == 422, (bad, resp.status_code, resp.text)
        assert secret_bad not in resp.text

    for bad_float in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            ActionContext.from_dict({"sourceTimeSec": bad_float})
        with pytest.raises(ValueError):
            ActionContext.from_dict(
                {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": bad_float, "viewRevision": 1}}
            )


def test_predicted_sky_pinned_star_composes_in_claude_and_attaches_catalog_explanation() -> None:
    predicted_ctx = ActionContext.from_dict({"viewRevision": 3, "calibrationState": "predicted_only"})
    composed = claude.compose_action(
        "finding_describe",
        {"findingId": "finding-predicted-star-rigel", "fields": ["distance", "evidence"]},
        predicted_ctx,
        "req-pred",
        3,
        current_year=YEAR,
    )
    assert composed is not None and composed["findingId"] == "finding-predicted-star-rigel"
    assert (
        claude.compose_action(
            "history_back", {"mode": "partial", "fraction": math.nan}, predicted_ctx, "req-nan", 3, current_year=YEAR
        )
        is None
    )

    reply = check_voice_command("where is Rigel?", predicted_ctx, current_year=YEAR)
    assert reply["status"] == "proposed"
    assert reply["proposedAction"]["findingId"] == "finding-predicted-star-rigel"
    assert "Rigel" in reply.get("scientificExplanation", "")
    assert "not measured from this recording" in reply["scientificExplanation"]


def _with_extra_findings(solution: dict[str, Any]) -> dict[str, Any]:
    sky = copy.deepcopy(solution)
    sol_id = sky["id"]
    sha = sky["sourceSha256"]
    sky["findings"].extend(
        [
            {
                "id": "finding-hip-72607",
                "observationSolutionId": sol_id,
                "evidenceClass": "catalog_matched",
                "title": "Xi2 Centauri",
                "subtitle": "HIP 72607",
                "sourceDetection": {
                    "assetId": sky["sourceAssetId"],
                    "frameTimeSec": 0.0,
                    "xPx": 200.0,
                    "yPx": 300.0,
                    "flux": 5000.0,
                    "snr": 40.0,
                },
                "skyPosition": {"raDeg": 196.7, "decDeg": -49.9, "altDeg": 35.0, "azDeg": 200.0},
                "catalogRef": {
                    "id": "hip-72607",
                    "provider": "Hipparcos",
                    "releaseVersion": "2007",
                    "catalogSourceId": "HIP 72607",
                    "bayerDesignation": "Xi2 Centauri",
                    "constellation": "Cen",
                    "objectType": "star",
                    "coordinateEpoch": "J1991.25",
                    "raDeg": 196.7,
                    "decDeg": -49.9,
                    "apparentMagnitude": 4.27,
                    "citation": "van Leeuwen 2007",
                },
                "evidence": {
                    "id": "ev-xi2",
                    "evidenceClass": "catalog_matched",
                    "verificationMethod": "Held-out WCS check",
                    "sourceAssetSha256": sha,
                    "wcsSolutionId": sol_id,
                    "catalogReferenceId": "hip-72607",
                    "pixelResidualPx": 0.4,
                    "angularResidualArcsec": 5.0,
                    "limitations": ["Synthetic test fixture"],
                    "verifiedByIndependentHoldout": True,
                },
            },
            {
                "id": "finding-hip-26069",
                "observationSolutionId": sol_id,
                "evidenceClass": "catalog_matched",
                "title": "119 Tauri",
                "subtitle": "119 Tau · HIP 26069",
                "sourceDetection": {
                    "assetId": sky["sourceAssetId"],
                    "frameTimeSec": 0.0,
                    "xPx": 400.0,
                    "yPx": 500.0,
                    "flux": 4200.0,
                    "snr": 35.0,
                },
                "skyPosition": {"raDeg": 83.0, "decDeg": 18.6, "altDeg": 42.0, "azDeg": 150.0},
                "catalogRef": {
                    "id": "hip-26069",
                    "provider": "Hipparcos",
                    "releaseVersion": "2007",
                    "catalogSourceId": "HIP 26069",
                    "flamsteedNumber": 119,
                    "constellation": "Tau",
                    "objectType": "star",
                    "coordinateEpoch": "J1991.25",
                    "raDeg": 83.0,
                    "decDeg": 18.6,
                    "apparentMagnitude": 4.32,
                    "bailerJonesDistancePc": {
                        "lower": 500.0,
                        "median": 550.0,
                        "upper": 600.0,
                        "method": "geometric_parallax",
                    },
                    "citation": "van Leeuwen 2007",
                },
                "evidence": {
                    "id": "ev-119tau",
                    "evidenceClass": "catalog_matched",
                    "verificationMethod": "Held-out WCS check",
                    "sourceAssetSha256": sha,
                    "wcsSolutionId": sol_id,
                    "catalogReferenceId": "hip-26069",
                    "pixelResidualPx": 0.5,
                    "angularResidualArcsec": 6.0,
                    "limitations": ["Synthetic test fixture"],
                    "verifiedByIndependentHoldout": True,
                },
            },
            {
                "id": "finding-openngc-NGC3766",
                "observationSolutionId": sol_id,
                "evidenceClass": "predicted",
                "title": "Pearl Cluster",
                "subtitle": "Open cluster · Cen · OpenNGC CC BY-SA 4.0",
                "skyPosition": {"raDeg": 174.06, "decDeg": -61.61, "altDeg": 25.0, "azDeg": 195.0},
                "catalogRef": {
                    "id": "openngc-NGC3766",
                    "provider": "OpenNGC",
                    "releaseVersion": "20231210",
                    "catalogSourceId": "NGC3766",
                    "commonName": "Pearl Cluster",
                    "constellation": "Cen",
                    "objectType": "deep_sky",
                    "coordinateEpoch": "J2000.0",
                    "raDeg": 174.06,
                    "decDeg": -61.61,
                    "apparentMagnitude": 5.3,
                    "citation": "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0",
                },
                "evidence": {
                    "id": "ev-ngc3766",
                    "evidenceClass": "predicted",
                    "verificationMethod": "OpenNGC catalog position projected through verified WCS",
                    "sourceAssetSha256": sha,
                    "wcsSolutionId": sol_id,
                    "catalogReferenceId": "openngc-NGC3766",
                    "limitations": ["Catalog centre only"],
                    "verifiedByIndependentHoldout": False,
                },
            },
            {
                "id": "finding-openngc-NGC5904",
                "observationSolutionId": sol_id,
                "evidenceClass": "predicted",
                "title": "NGC 5904",
                "subtitle": "Globular cluster · Se1 · OpenNGC CC BY-SA 4.0",
                "skyPosition": {"raDeg": 229.64, "decDeg": 2.08, "altDeg": 48.0, "azDeg": 140.0},
                "catalogRef": {
                    "id": "openngc-NGC5904",
                    "provider": "OpenNGC",
                    "releaseVersion": "20231210",
                    "catalogSourceId": "NGC5904",
                    "commonName": "NGC 5904",
                    "constellation": "Se1",
                    "objectType": "deep_sky",
                    "coordinateEpoch": "J2000.0",
                    "raDeg": 229.64,
                    "decDeg": 2.08,
                    "apparentMagnitude": 5.95,
                    "citation": "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC), CC BY-SA 4.0",
                },
                "evidence": {
                    "id": "ev-ngc5904",
                    "evidenceClass": "predicted",
                    "verificationMethod": "OpenNGC catalog position projected through verified WCS",
                    "sourceAssetSha256": sha,
                    "wcsSolutionId": sol_id,
                    "catalogReferenceId": "openngc-NGC5904",
                    "limitations": ["Catalog centre only"],
                    "verifiedByIndependentHoldout": False,
                },
            },
            {
                "id": "finding-predicted-gaia-dr3-5044368071870093312",
                "observationSolutionId": sol_id,
                "evidenceClass": "predicted",
                "title": "Acamar",
                "subtitle": "Theta1 Eridani · Eri",
                "skyPosition": {"raDeg": 44.56, "decDeg": -40.3, "altDeg": 30.0, "azDeg": 210.0},
                "catalogRef": {
                    "id": "gaia-5044368071870093312",
                    "provider": "Gaia DR3",
                    "releaseVersion": "DR3",
                    "catalogSourceId": "Gaia DR3 5044368071870093312",
                    "bayerDesignation": "Theta1 Eridani",
                    "constellation": "Eri",
                    "objectType": "star",
                    "coordinateEpoch": "J2016.0",
                    "raDeg": 44.56,
                    "decDeg": -40.3,
                    "apparentMagnitude": 2.88,
                    "citation": "Gaia Collaboration 2023",
                },
                "evidence": {
                    "id": "ev-acamar",
                    "evidenceClass": "predicted",
                    "verificationMethod": "Predicted sky tile star",
                    "sourceAssetSha256": sha,
                    "limitations": ["Predicted for moment"],
                    "verifiedByIndependentHoldout": False,
                },
            },
        ]
    )
    return sky


def test_bayer_flamsteed_openngc_and_solar_system_targets_resolve_or_refuse_without_leaking_selected() -> None:
    rich = _with_extra_findings(CALIBRATED)
    betelgeuse = "finding-star-betelgeuse"

    # Present targets resolve by Bayer, Flamsteed (long and short), OpenNGC (common name, NGC, Messier), and predicted Gaia ID,
    # even when another star (Betelgeuse) is currently selected!
    present_cases = [
        ("how far is Beta Orionis?", betelgeuse, "Rigel is about 260 parsecs away, about 860 light years."),
        ("what constellation is Xi2 Centauri in?", betelgeuse, "Xi2 Centauri is in Centaurus."),
        ("how far is 119 Tauri?", betelgeuse, "119 Tauri is about 550 parsecs away, about 1,800 light years."),
        ("how far is 119 Tau?", betelgeuse, "119 Tauri is about 550 parsecs away, about 1,800 light years."),
        ("what constellation is Pearl Cluster in?", betelgeuse, "Predicted, not measured: Pearl Cluster is in Centaurus."),
        ("how bright is NGC 3766?", betelgeuse, "Predicted, not measured: Pearl Cluster shines at magnitude 5.30."),
        ("how bright is NGC3766?", None, "Predicted, not measured: Pearl Cluster shines at magnitude 5.30."),
        ("what constellation is M5 in?", betelgeuse, "Predicted, not measured: NGC 5904 is in Serpens."),
        ("what constellation is Messier 5 in?", None, "Predicted, not measured: NGC 5904 is in Serpens."),
        ("what constellation is Acamar in?", betelgeuse, "Predicted, not measured: Acamar is in Eridanus."),
    ]
    for question, selected, expected_sentence in present_cases:
        ans = answers.answer_question(question, rich, selected)
        contract.VOICE_ANSWER.validate_python(ans)
        assert ans["status"] == "answered", (question, ans)
        assert ans["sentence"] == expected_sentence, (question, ans)

    # Absent Bayer, Flamsteed, OpenNGC, and Solar System targets MUST refuse as not_found_in_this_sky (never returning RIGEL!)
    absent_cases = [
        ("what constellation is Gamma Cassiopeiae in?", "Gamma Cassiopeiae"),
        ("how far is 51 Pegasi?", "51 Pegasi"),
        ("how far is 51 Peg?", "51 Pegasi"),
        ("what constellation is Omega Centauri in?", "Omega Centauri"),
        ("how bright is 47 Tucanae?", "47 Tucanae"),
        ("where is the Orion Nebula?", "Great Orion Nebula"),
        ("how bright is M42?", "Great Orion Nebula"),
        ("how bright is NGC 1976?", "Great Orion Nebula"),
        ("where is Pluto?", "Pluto"),
    ]
    for question, expected_name in absent_cases:
        ans = answers.answer_question(question, CALIBRATED, RIGEL)
        contract.VOICE_ANSWER.validate_python(ans)
        assert (ans["status"], ans["reason"], ans["sentence"]) == (
            "unanswerable",
            "not_found_in_this_sky",
            f"No finding in this photo is named {expected_name}.",
        ), (question, ans)
    # Mercury and Neptune, which the predicted sky never places, are said as only what the app knows
    # (unpredicted-body-one, tests/integration/test_voice_unpredicted_bodies.py)
    for question, expected_name in (("where is Mercury?", "Mercury"), ("how far is Neptune?", "Neptune")):
        ans = answers.answer_question(question, CALIBRATED, RIGEL)
        contract.VOICE_ANSWER.validate_python(ans)
        assert (ans["status"], ans["reason"], ans["sentence"]) == (
            "unanswerable",
            "not_found_in_this_sky",
            f"The app does not predict where {expected_name} is, so it cannot be found in this photo's sky.",
        ), (question, ans)

    # Moon phase for another planet (Mercury, Neptune) refuses as outside_the_data, and two names across classes refuse as SENTENCE_TWO_NAMES
    for planet_q in ("what phase is Mercury in?", "what phase is Neptune in?", "what phase is Pearl Cluster in?"):
        ans = answers.answer_question(planet_q, CALIBRATED, RIGEL)
        assert (ans["status"], ans["reason"]) == ("unanswerable", "outside_the_data"), (planet_q, ans)

    for two_q in (
        "how far is Beta Orionis from Sirius?",
        "how bright are Pearl Cluster and Rigel?",
        "where is 51 Pegasi or Vega?",
        "where is Mercury and Mars?",
        "where in the sky are Mercury and Mars?",
    ):
        ans = answers.answer_question(two_q, rich, RIGEL)
        assert (ans["status"], ans["sentence"]) == ("unanswerable", answers.SENTENCE_TWO_NAMES), (two_q, ans)

    # Numbers before "and" or "for" are never misread as Andromeda ("And") or Fornax ("For") Flamsteed stars
    deictic_ok = answers.answer_question("how far is it in 1 and 2?", CALIBRATED, RIGEL)
    assert deictic_ok["status"] == "answered" and "Rigel" in deictic_ok["sentence"]


def test_flamsteed_abbreviation_reads_only_in_its_iau_case_and_a_date_or_rate_is_no_star() -> None:
    rich = _with_extra_findings(CALIBRATED)
    # A day of October is not a star of Octans, in any case: the Moon's phase is still answered
    for question in (
        "what was the moon phase on 3 oct?",
        "what was the moon phase on 3 Oct?",
        "what was the moon phase on 3 OCT?",
    ):
        ans = answers.answer_question(question, CALIBRATED, None)
        contract.VOICE_ANSWER.validate_python(ans)
        assert (ans["status"], ans["question"]) == ("answered", "moon_phase"), (question, ans)
    # A rate or a date after a number beside a named star leaves the one star asked about
    for question in ("how bright is Rigel at 50 per cent?", "how far was Rigel on 3 oct?", "how far is Rigel, 3 men?"):
        ans = answers.answer_question(question, rich, None)
        assert ans["status"] == "answered" and ans["sentence"].startswith("Rigel "), (question, ans)
    # The genitive reads in any case; the abbreviation only as the IAU writes it
    for question in ("how far is 119 tauri?", "how far is 119 TAURI?", "how far is 119 Tau?"):
        ans = answers.answer_question(question, rich, "finding-star-betelgeuse")
        assert ans["sentence"].startswith("119 Tauri "), (question, ans)
    absent = answers.answer_question("how far is 21 And?", CALIBRATED, RIGEL)
    assert (absent["status"], absent["reason"], absent["sentence"]) == (
        "unanswerable",
        "not_found_in_this_sky",
        "No finding in this photo is named 21 Andromedae.",
    ), absent


def test_not_a_number_pointer_time_and_model_number_are_refused() -> None:
    ctx = _epoch_ctx(5.0)
    assert ctx.recent_pointer is not None and claude.fresh_pointer(ctx) is not None
    nan_pointer = dataclasses.replace(ctx.recent_pointer, at_timestamp_ms=math.nan)
    assert claude.fresh_pointer(dataclasses.replace(ctx, recent_pointer=nan_pointer)) is None
    assert claude._matches({"type": "number"}, 0.5)
    for bad in (math.nan, math.inf, -math.inf, True):
        assert not claude._matches({"type": "number"}, bad), bad


def test_one_thing_named_twice_or_by_its_designation_is_one_target() -> None:
    rich = _with_extra_findings(CALIBRATED)
    # Two names of one finding, or one name said twice, ask about one thing
    for question, sentence in (
        ("how far is Rigel, also called Beta Orionis?", "Rigel is about 260 parsecs away, about 860 light years."),
        ("what constellation is Messier 5 Ser in?", "Predicted, not measured: NGC 5904 is in Serpens."),
    ):
        ans = answers.answer_question(question, rich, "finding-star-betelgeuse")
        assert (ans["status"], ans["sentence"]) == ("answered", sentence), (question, ans)
    for question, name in (
        ("how bright is M42, the Orion Nebula?", "Great Orion Nebula"),
        ("how far is Pluto, is Pluto up?", "Pluto"),
        ("how bright is NGC 104 Tuc?", answers.answer_question("how bright is NGC 104?", CALIBRATED, None)["sentence"]
         .removeprefix("No finding in this photo is named ").removesuffix(".")),
    ):
        ans = answers.answer_question(question, CALIBRATED, RIGEL)
        assert (ans["status"], ans["reason"], ans["sentence"]) == (
            "unanswerable",
            "not_found_in_this_sky",
            f"No finding in this photo is named {name}.",
        ), (question, ans)
    # A finding found by its Flamsteed number when its title is its IAU name, or by a Bayer title alone
    flamsteed = copy.deepcopy(rich)
    betelgeuse = next(f for f in flamsteed["findings"] if f["id"] == "finding-star-betelgeuse")
    betelgeuse["catalogRef"].update({"flamsteedNumber": 58, "constellation": "Ori"})
    for question in ("how far is 58 Orionis?", "how far is 58 Ori?"):
        ans = answers.answer_question(question, flamsteed, RIGEL)
        assert ans["status"] == "answered" and ans["sentence"].startswith("Betelgeuse "), (question, ans)
    titled = copy.deepcopy(rich)
    xi2 = next(f for f in titled["findings"] if f["id"] == "finding-hip-72607")
    del xi2["catalogRef"]["bayerDesignation"]
    ans = answers.answer_question("what constellation is Xi2 Centauri in?", titled, RIGEL)
    assert (ans["status"], ans["sentence"]) == ("answered", "Xi2 Centauri is in Centaurus."), ans


HUGE = 10**400  # a whole number JSON carries and Python reads, past a float's range


def test_a_number_past_a_floats_range_is_refused_like_any_other_bad_number() -> None:
    # Each helper: the required finite number and the optional bounded one
    for bad in ({"sourceTimeSec": HUGE}, {"sourceTimeSec": -HUGE}, {"observerLat": HUGE}, {"observerLon": -HUGE}):
        with pytest.raises(ValueError):
            ActionContext.from_dict(bad)
    for field_name in ("xPx", "yPx", "atTimestampMs"):
        pointer = {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1, field_name: HUGE}
        with pytest.raises(ValueError):
            ActionContext.from_dict({"recentPointer": pointer})
    # The largest whole number a float holds is still a number, refused only by its bound
    assert ActionContext.from_dict({"sourceTimeSec": 10**300}).source_time_sec == 1e300
    with pytest.raises(ValueError):
        ActionContext.from_dict({"observerLat": 10**300})
    # The route: a 422 that echoes nothing, never a 500
    client = TestClient(app)
    for context in (
        {"sourceTimeSec": HUGE},
        {"observerLat": HUGE},
        {"recentPointer": {"xPx": HUGE, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1}},
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": HUGE, "viewRevision": 1}},
    ):
        body = '{"utterance": "stop", "context": ' + json.dumps(context) + "}"
        resp = client.post("/api/voice/command", content=body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 422, (context, resp.status_code)
        assert str(HUGE) not in resp.text and str(HUGE)[:40] not in resp.text


def test_each_context_guard_refuses_what_it_names() -> None:
    # A view revision is a safe integer: 2^53 - 1 stands, 2^53 does not
    assert ActionContext.from_dict({"viewRevision": 2**53 - 1}).view_revision == 2**53 - 1
    with pytest.raises(ValueError):
        ActionContext.from_dict({"viewRevision": 2**53})
    # A state is a string, never a number read as one
    for bad in ({"navigationState": 5}, {"calibrationState": 0}, {"navigationState": ["source"]}):
        with pytest.raises(ValueError):
            ActionContext.from_dict(bad)
    # Every candidate id names something
    with pytest.raises(ValueError):
        ActionContext.from_dict(
            {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1,
                               "overlappingCandidateIds": ["", "finding-star-rigel"]}}
        )
    ok = ActionContext.from_dict(
        {"recentPointer": {"xPx": 0.0, "yPx": 0.0, "atTimestampMs": 1.0, "viewRevision": 1,
                           "overlappingCandidateIds": ["finding-star-rigel"]}}
    )
    assert ok.recent_pointer is not None and ok.recent_pointer.overlapping_candidate_ids == ["finding-star-rigel"]


def test_a_non_number_where_a_number_is_due_is_refused_without_its_text() -> None:
    marker = "marker-not-a-number"
    for bad in ({"sourceTimeSec": marker}, {"sourceTimeSec": [1.0]}, {"observerLat": {"deg": 1}}, {"observerLon": marker}):
        with pytest.raises(ValueError) as refused:
            ActionContext.from_dict(bad)
        assert marker not in str(refused.value), refused.value
    client = TestClient(app)
    for context in ({"sourceTimeSec": marker}, {"sourceTimeSec": [1.0]}, {"observerLon": marker}):
        resp = client.post("/api/voice/command", json={"utterance": "stop", "context": context})
        assert resp.status_code == 422 and marker not in resp.text, (context, resp.status_code, resp.text)
