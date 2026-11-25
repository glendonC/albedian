"""POST /api/voice/answer/{sha256}: a question about a stored photo, answered from its stored solution.

Every number and class in an answer comes from the stored solution or a deterministic service (the lunar phase), each
statement with its own source and evidence class; no model reads the question here, and its free text could never
reach the reply. When nothing in the photo's data answers, the reply is the contract's refusal with its sentence.
The solutions are the made-up samples of tests/fixtures/python_contract_solutions.json, stored under a made-up upload.

Covers:
- An unknown upload is a 404; an upload with no solution, or one whose sky was not worked out, is refused
- Each answerable question kind is answered with the stored values, by the stored finding the words name or point at
- A name the sky does not hold, a finding question with nothing named or selected, and a question no data answers
  are refused, each with its reason and sentence
- Every statement's value is the stored solution's own at the path its source names
- The words reach no log beside the photo's capture time or place, and no model is asked
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.api import app
from services.contracts import voice as contract
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science import radiometry
from services.voice import claude

ROOT = Path(__file__).resolve().parents[2]
SOLUTIONS: dict[str, dict[str, Any]] = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text())
# A made-up upload: any bytes stand for it, since the route reads only the stored solution
BODY = b"albedian answer route test upload\n"
SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    store = IngestStore(tmp_path / "store", max_bytes=1_000_000)
    folder = store.folder(SHA)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original.bin").write_bytes(BODY)
    store.write_record(SHA, {"sha256": SHA, "made_up": True})
    return store


@pytest.fixture
def client(store: IngestStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The live model path switched on, with a client that fails the test if anything asks it
    monkeypatch.setenv(claude.ENABLE_ENV, "1")
    monkeypatch.setenv(claude.API_KEY_ENV, "sk-ant-test-not-a-real-key")

    def _no_model(api_key: str) -> Any:
        raise AssertionError("the answer route asked a model")

    monkeypatch.setattr(claude, "_shared_client", _no_model)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _store_solution(store: IngestStore, sample: str) -> dict[str, Any]:
    solution = {**SOLUTIONS[sample], "sourceSha256": SHA, "sourceAssetId": f"src_{SHA[:16]}"}
    (store.folder(SHA) / "solution.json").write_text(json.dumps(solution), encoding="utf-8")
    return solution


def _ask(client: TestClient, question: str, selected: str | None = None, sha: str = SHA) -> dict[str, Any]:
    body: dict[str, Any] = {"question": question}
    if selected is not None:
        body["selectedFindingId"] = selected
    resp = client.post(f"/api/voice/answer/{sha}", json=body)
    assert resp.status_code == 200, resp.text
    reply = resp.json()
    contract.VOICE_QUESTION_REPLY.validate_python(reply)
    assert reply["heard"] == question
    return reply["answer"]


def _at(solution: dict[str, Any], path: list[Any]) -> Any:
    node: Any = solution
    for step in path:
        node = node[step]
    return node


def _values(answer: dict[str, Any], solution: dict[str, Any]) -> None:
    """Every statement's value is the stored one at its source's path, or the lunar phase service's."""
    for statement in answer.get("statements", []):
        source, value = statement["source"], statement["value"]
        stored = value["amount"] if value["kind"] == "number" else value["text"]
        if source["kind"] == "solution_field":
            assert stored == _at(solution, source["path"]), statement
        else:
            assert source == {"kind": "ephemeris", "method": "lunar_phase"}
            phase = radiometry.compute_lunar_phase_and_illumination(solution["observedAtUTC"])
            expected = {"illuminated_fraction": phase["illuminationFraction"], "phase_angle": phase["alphaDeg"],
                        "moon_phase_name": phase["phaseName"]}[statement["quantity"]]
            assert stored == expected, statement


def test_an_unknown_upload_is_not_found(client: TestClient) -> None:
    resp = client.post(f"/api/voice/answer/{'0' * 64}", json={"question": "how far is Rigel?"})
    assert resp.status_code == 404


def test_a_record_with_no_original_is_not_found(client: TestClient, store: IngestStore) -> None:
    _store_solution(store, "plate_calibrated")
    (store.folder(SHA) / "original.bin").unlink()
    resp = client.post(f"/api/voice/answer/{SHA}", json={"question": "how far is Rigel?"})
    assert resp.status_code == 404


def test_the_words_come_back_as_heard_padding_and_all(client: TestClient, store: IngestStore) -> None:
    _store_solution(store, "plate_calibrated")
    answer = _ask(client, "  how far is Rigel?  ")
    assert (answer["status"], answer["question"]) == ("answered", "distance")


def test_a_request_the_contract_refuses_is_refused(client: TestClient, store: IngestStore) -> None:
    _store_solution(store, "plate_calibrated")
    for body in ({}, {"question": ""}, {"question": "x" * 501}, {"question": "x", "selected_finding_id": "finding-x"}):
        assert client.post(f"/api/voice/answer/{SHA}", json=body).status_code == 422, body


def test_an_upload_with_no_solution_or_an_unsolved_sky_is_refused(client: TestClient, store: IngestStore) -> None:
    answer = _ask(client, "how far is Rigel?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "photo_not_solved")
    _store_solution(store, "pending_unsolved")
    answer = _ask(client, "how far is Rigel?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "photo_not_solved")


@pytest.mark.parametrize(
    "question, selected, kind, quantities, subject",
    [
        ("how far is Rigel?", None, "distance", ["distance"], "finding-star-rigel"),
        ("how far away is that one", "finding-star-betelgeuse", "distance", ["distance"], "finding-star-betelgeuse"),
        ("what star is that?", "finding-star-aldebaran", "identity", ["name", "object_type"], "finding-star-aldebaran"),
        ("how bright is Rigel", None, "brightness", ["apparent_magnitude", "detection_snr"], "finding-star-rigel"),
        ("what constellation is it in", "finding-star-rigel", "constellation", ["constellation"], "finding-star-rigel"),
        ("how high is Betelgeuse?", None, "sky_position", ["altitude", "azimuth", "right_ascension", "declination"],
         "finding-star-betelgeuse"),
        ("how bright is that one", "finding-obs-12", "brightness", ["detection_snr"], "finding-obs-12"),
    ],
)
def test_a_question_about_a_finding_is_answered_from_its_stored_values(
    client: TestClient, store: IngestStore, question: str, selected: str | None, kind: str, quantities: list[str], subject: str
) -> None:
    solution = _store_solution(store, "plate_calibrated")
    answer = _ask(client, question, selected)
    assert (answer["status"], answer["question"]) == ("answered", kind), answer
    assert answer["observationSolutionId"] == solution["id"]
    assert [s["quantity"] for s in answer["statements"]] == quantities
    assert {s["subject"]["findingId"] for s in answer["statements"]} == {subject}
    _values(answer, solution)


def test_the_photo_as_a_whole_and_the_moon_are_answered_from_the_solution_and_the_phase_service(
    client: TestClient, store: IngestStore
) -> None:
    solution = _store_solution(store, "plate_calibrated")
    contents = _ask(client, "what's in this photo?")
    assert (contents["question"], [s["quantity"] for s in contents["statements"]]) == (
        "photo_contents", ["detected_source_count", "matched_star_count"])
    confidence = _ask(client, "how sure are you about that?")
    assert confidence["question"] == "confidence"
    assert [s["quantity"] for s in confidence["statements"]][:2] == ["solution_status", "matched_star_count"]
    moon = _ask(client, "what phase is the moon in?")
    assert (moon["question"], [s["quantity"] for s in moon["statements"]]) == (
        "moon_phase", ["illuminated_fraction", "phase_angle", "moon_phase_name"])
    assert all(s["subject"] == {"kind": "ephemeris_body", "body": "Moon"} for s in moon["statements"])
    for answer in (contents, confidence, moon):
        _values(answer, solution)


def test_the_moon_needs_the_capture_time(client: TestClient, store: IngestStore) -> None:
    _store_solution(store, "plate_calibrated_timeless")
    answer = _ask(client, "what phase is the moon in?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "no_time_or_place")


@pytest.mark.parametrize(
    "question, selected, reason, sentence",
    [
        ("how far is Vega?", None, "not_found_in_this_sky", "No finding in this photo is named Vega."),
        ("how far is it?", None, "not_in_the_data", "This photo's data holds no value for that."),
        ("what constellation is that in", "finding-obs-12", "not_in_the_data", "This photo's data holds no value for that."),
        ("what is a light year?", None, "outside_the_data", "Nothing in this photo's data answers that."),
        ("is that a plane", "finding-star-rigel", "outside_the_data", "Nothing in this photo's data answers that."),
    ],
)
def test_what_the_data_cannot_answer_is_refused_with_its_reason(
    client: TestClient, store: IngestStore, question: str, selected: str | None, reason: str, sentence: str
) -> None:
    _store_solution(store, "plate_calibrated")
    answer = _ask(client, question, selected)
    assert (answer["status"], answer["reason"], answer["sentence"]) == ("unanswerable", reason, sentence), answer
    assert "statements" not in answer


def test_the_words_reach_no_log_beside_the_time_or_place(client: TestClient, store: IngestStore, caplog: pytest.LogCaptureFixture) -> None:
    solution = _store_solution(store, "plate_calibrated")
    words = "how far is Rigel from here, made up words 7f3a"
    with caplog.at_level(logging.DEBUG):
        _ask(client, words)
    for record in caplog.records:
        text = record.getMessage()
        if "7f3a" in text:
            assert solution["observedAtUTC"] not in text
            assert str(solution["observerLocation"].get("latDeg", "no-lat")) not in text


@pytest.mark.parametrize(
    "question, selected, sentence",
    [
        ("how far is Rigel?", None, "Rigel is about 260 parsecs away, about 860 light years."),
        ("what star is that?", "finding-star-aldebaran", "That is Aldebaran."),
        ("how bright is Rigel", None, "Rigel shines at magnitude 0.13."),
        ("how bright is that one", "finding-obs-12", "Observed Point Source #12 stands {snr} times above the photo's noise."),
        ("what constellation is it in", "finding-star-rigel", "Rigel is in Orion."),
        ("what's in this photo?", None, "This photo shows 12 points of light, 9 matched to catalog stars."),
        ("how sure are you?", None, "The sky is calibrated, from 9 matched stars."),
    ],
)
def test_the_sentence_says_the_stored_values(client: TestClient, store: IngestStore, question: str, selected: str | None,
                                             sentence: str) -> None:
    solution = _store_solution(store, "plate_calibrated")
    snr = next(f for f in solution["findings"] if f["id"] == "finding-obs-12")["sourceDetection"]["snr"]
    assert _ask(client, question, selected)["sentence"] == sentence.format(snr=f"{snr:.0f}")


def test_a_named_finding_is_found_by_its_catalog_name_whatever_its_id(client: TestClient, store: IngestStore) -> None:
    # A real solve names a finding by its catalog row, not by the voice tables' id: the name finds it all the same
    solution = {**SOLUTIONS["plate_calibrated"], "sourceSha256": SHA, "sourceAssetId": f"src_{SHA[:16]}"}
    rigel_index = next(i for i, f in enumerate(solution["findings"]) if f["id"] == "finding-star-rigel")
    solution["findings"] = [{**f, "id": f"finding-solved-{index}"} for index, f in enumerate(solution["findings"])]
    (store.folder(SHA) / "solution.json").write_text(json.dumps(solution), encoding="utf-8")
    answer = _ask(client, "how far is Rigel?")
    assert answer["status"] == "answered", answer
    assert {s["subject"]["findingId"] for s in answer["statements"]} == {f"finding-solved-{rigel_index}"}


def test_an_unreadable_stored_solution_answers_409_with_the_fixed_sentence(
    client: TestClient, store: IngestStore, tmp_path: Path
) -> None:
    import copy

    base = {**copy.deepcopy(SOLUTIONS["plate_calibrated"]), "sourceSha256": SHA, "sourceAssetId": f"src_{SHA[:16]}"}
    path = store.folder(SHA) / "solution.json"

    def drop(d: dict[str, Any], *keys: str) -> dict[str, Any]:
        out = copy.deepcopy(d)
        node: Any = out
        for k in keys[:-1]:
            node = node[k]
        node.pop(keys[-1], None)
        return out

    def put(d: dict[str, Any], *keys_and_val: Any) -> dict[str, Any]:
        out = copy.deepcopy(d)
        node: Any = out
        for k in keys_and_val[:-2]:
            node = node[k]
        node[keys_and_val[-2]] = keys_and_val[-1]
        return out

    malformed_json = [
        drop(base, "quality", "detectedCandidateCount"),
        put(base, "quality", "detectedCandidateCount", None),
        put(base, "quality", "detectedCandidateCount", 3.5),
        put(base, "quality", "detectedCandidateCount", "secret-marker"),
        drop(base, "status"),
        put(base, "status", None),
        put(base, "status", "secret-marker"),
        put(base, "findings", {}),
        put(base, "findings", 123),
        put(base, "findings", ["secret-marker"]),
        drop(base, "findings", 0, "id"),
        drop(base, "findings", 0, "evidenceClass"),
        put(base, "findings", 0, "id", "secret-marker"),
        put(base, "findings", 0, "catalogRef", "catalogSourceId", 123),
        drop(base, "id"),
        put(base, "id", "secret-marker"),
        put(base, "observedAtUTC", "not-a-time-secret-marker"),
        put(base, "sourceSha256", "1" * 64),
        put(base, "sourceAssetId", "src_1111111111111111"),
    ]
    raw_cases: list[bytes] = [
        b"",
        b"\xff\xfe\xfd",
        b"{secret-marker",
        b"[]",
        b'"secret-marker"',
        b"null",
        b"42",
        b'{"id": NaN}',
        *(json.dumps(item).encode("utf-8") for item in malformed_json),
    ]
    for raw in raw_cases:
        path.write_bytes(raw)
        for q in ("how far is Rigel?", "what is a light year?"):
            resp = client.post(f"/api/voice/answer/{SHA}", json={"question": q})
            assert resp.status_code == 409, (raw[:80], resp.status_code, resp.text)
            assert resp.json() == {"detail": ingest_router.UNREADABLE_SOLUTION}
            assert "secret-marker" not in resp.text and str(store.folder(SHA)) not in resp.text

    path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(base), encoding="utf-8")
    path.symlink_to(outside)
    resp = client.post(f"/api/voice/answer/{SHA}", json={"question": "how far is Rigel?"})
    assert resp.status_code == 409 and resp.json() == {"detail": ingest_router.UNREADABLE_SOLUTION}


def test_sky_position_with_ra_360_and_no_altitude_is_answered_from_declination(
    client: TestClient, store: IngestStore
) -> None:
    import copy

    solution = {**copy.deepcopy(SOLUTIONS["plate_calibrated"]), "sourceSha256": SHA, "sourceAssetId": f"src_{SHA[:16]}"}
    obs = next(f for f in solution["findings"] if f["id"] == "finding-obs-12")
    obs["skyPosition"] = {k: v for k, v in obs["skyPosition"].items() if k not in ("altDeg", "azDeg")}
    obs["skyPosition"]["raDeg"] = 360.0
    obs["skyPosition"]["decDeg"] = -12.34
    (store.folder(SHA) / "solution.json").write_text(json.dumps(solution), encoding="utf-8")
    answer = _ask(client, "where in the sky is it", "finding-obs-12")
    assert answer["status"] == "answered"
    assert [s["quantity"] for s in answer["statements"]] == ["declination"]
    assert answer["sentence"] == "Observed Point Source #12 lies at declination -12.34 degrees."


def test_the_question_table_reads_word_edges_as_ascii() -> None:
    # As JavaScript's \b does: a letter outside ASCII is no word letter, so a phrase right after it is still a phrase
    from services.voice import answers

    assert answers.question_kind("éhow far") == "distance"
    assert answers.question_kind("showhow far") == "unanswerable"


def test_a_stored_clip_is_answered_as_a_clip(tmp_path: Path, client: TestClient) -> None:
    """C34: the route names the recording by the stored upload's own media kind, "clip" for a video."""
    clip = IngestStore(tmp_path / "clip-store", max_bytes=1_000_000)
    folder = clip.folder(SHA)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original.bin").write_bytes(BODY)
    clip.write_record(SHA, {"sha256": SHA, "made_up": True, "sourceAsset": {"mediaKind": "video"}})
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: clip
    answer = _ask(client, "how far is Rigel?")
    assert answer["sentence"] == "This clip's sky has not been worked out, so nothing in it can be answered."
    _store_solution(clip, "plate_calibrated")
    assert _ask(client, "how far is Vega?")["sentence"] == "No finding in this clip is named Vega."


def test_the_route_speaks_full_constellation_names_for_iau_and_serpens_codes_while_stating_stored_codes(
    client: TestClient, store: IngestStore
) -> None:
    import copy

    horizon = _store_solution(store, "horizon_approximate")
    nunki = _ask(client, "what constellation is it in", "finding-predicted-hip-92855")
    assert nunki["sentence"] == "Predicted, not measured: Nunki is in Sagittarius."
    _values(nunki, horizon)

    for code, common in (("Cru", "Acrux"), ("Se1", "Messier 5"), ("Se2", "Eagle Nebula")):
        sky = {**copy.deepcopy(SOLUTIONS["plate_calibrated"]), "sourceSha256": SHA, "sourceAssetId": f"src_{SHA[:16]}"}
        finding = next(f for f in sky["findings"] if f["id"] == "finding-star-rigel")
        # The voice names a finding by its title, the one its card shows (voice-star-name-1)
        finding["catalogRef"]["commonName"] = finding["title"] = common
        finding["catalogRef"]["constellation"] = code
        (store.folder(SHA) / "solution.json").write_text(json.dumps(sky), encoding="utf-8")
        answer = _ask(client, "what constellation is it in", "finding-star-rigel")
        expected_constellation = "Crux" if code == "Cru" else "Serpens"
        assert answer["sentence"] == f"{common} is in {expected_constellation}."
        _values(answer, sky)
