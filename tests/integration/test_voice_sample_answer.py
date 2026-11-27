"""sample-voice-one: POST /api/voice/answer/{sha256} answers a question asked on the sample night, from the app's own
solve of it kept on this machine. The web asks by the photo's hash, which for the sample is never an upload's
(DECIDED-BY-LEAD 25 Nov 07:11 and 08:00), so until now a question on the sample was a 404. The answer is the one an
upload of the same bytes with the same solve would get; an upload of those bytes, where there is one, answers from its
own solve; the kept solve answers only while its hash holds and it names this photo. A temporary root stands for the
repository and a temporary store for the uploads; the solutions are the made-up samples of
tests/fixtures/python_contract_solutions.json, under a made-up photo hash.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.api import create_app
from services.contracts import voice as contract
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.samples import make as samples_make
from services.samples import router as samples_router

ROOT = Path(__file__).resolve().parents[2]
RECORD = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
SOLUTIONS: dict[str, dict[str, Any]] = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text())
# A made-up photo: the answer reads only the kept solve, so the photo itself need not be on this machine
PHOTO = b"albedian sample answer test photo\n"
SHA = hashlib.sha256(PHOTO).hexdigest()
# Another photo's hash (64 zeros is the contract's placeholder, refused before any name is read)
OTHER = hashlib.sha256(b"another photo\n").hexdigest()
NIGHT = RECORD["samples"][0]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    record = json.loads(json.dumps(RECORD))
    record["samples"][0]["photo"]["sha256"] = SHA
    record["samples"][0]["photo"]["bytes"] = len(PHOTO)
    (tmp_path / "data" / "samples.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=1_000_000)


@pytest.fixture
def client(root: Path, store: IngestStore) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[samples_router.get_samples_root] = lambda: root
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    yield TestClient(app, base_url="http://127.0.0.1:8000")


def _solution(sample: str, sha: str = SHA) -> dict[str, Any]:
    # A whole copy, so no test changes the fixture another reads
    return {**json.loads(json.dumps(SOLUTIONS[sample])), "sourceSha256": sha, "sourceAssetId": f"src_{sha[:16]}"}


def _keep(root: Path, solution: dict[str, Any], digest: str | None = None) -> None:
    text = (json.dumps(solution) + "\n").encode("utf-8")
    path = root / NIGHT["solution"]["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    (root / NIGHT["solution"]["sha256Path"]).write_text((digest or hashlib.sha256(text).hexdigest()) + "\n", encoding="utf-8")
    # Made by this revision of the solve chain, as the setup writes it beside the solve
    night = samples_make.load_samples(root / "data" / "samples.json")[0]
    (root / samples_make.producer_path(night)).write_text(json.dumps(samples_make.current_producer()) + "\n", encoding="utf-8")


def _upload(store: IngestStore, solution: dict[str, Any]) -> None:
    folder = store.folder(SHA)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original.bin").write_bytes(PHOTO)
    store.write_record(SHA, {"sha256": SHA, "sourceAsset": {"mediaKind": "image"}})
    (folder / "solution.json").write_text(json.dumps(solution), encoding="utf-8")


def _ask(client: TestClient, question: str, sha: str = SHA, status: int = 200) -> Any:
    resp = client.post(f"/api/voice/answer/{sha}", json={"question": question})
    assert resp.status_code == status, resp.text
    if status != 200:
        return resp.json()
    reply = resp.json()
    contract.VOICE_QUESTION_REPLY.validate_python(reply)
    return reply["answer"]


def test_a_question_on_the_sample_is_answered_from_its_kept_solve_as_an_upload_of_it_would_be(
    client: TestClient, root: Path, store: IngestStore
) -> None:
    _keep(root, _solution("plate_calibrated"))
    on_the_sample = _ask(client, "how far is Rigel?")
    assert (on_the_sample["status"], on_the_sample["question"]) == ("answered", "distance")
    _upload(store, _solution("plate_calibrated"))
    assert _ask(client, "how far is Rigel?") == on_the_sample


def test_an_upload_of_the_same_bytes_answers_from_its_own_solve(client: TestClient, root: Path, store: IngestStore) -> None:
    _keep(root, _solution("plate_calibrated"))
    _upload(store, _solution("pending_unsolved"))
    answer = _ask(client, "how far is Rigel?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "photo_not_solved")


def test_a_sample_whose_solve_is_not_kept_here_is_answered_as_an_unsolved_photo(client: TestClient, store: IngestStore) -> None:
    answer = _ask(client, "how far is Rigel?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "photo_not_solved")
    # Word for word what a photo uploaded with no solve is told: a sample night is a photo
    folder = store.folder(SHA)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "original.bin").write_bytes(PHOTO)
    store.write_record(SHA, {"sha256": SHA, "sourceAsset": {"mediaKind": "image"}})
    assert _ask(client, "how far is Rigel?") == answer


@pytest.mark.parametrize("where", ["finding", "solution"])
def test_a_kept_solve_with_an_id_the_answer_contract_refuses_is_not_answered_from(client: TestClient, root: Path, where: str) -> None:
    solution = _solution("plate_calibrated")
    if where == "finding":
        solution["findings"][0] = {**solution["findings"][0], "id": "finding-a--b"}
    else:
        solution["id"] = "sol-a--b"
    _keep(root, solution)
    assert _ask(client, "how far is Rigel?", status=409) == {"detail": samples_router.UNEXPECTED_SAMPLE_FILE}


def test_a_kept_solve_that_fails_its_hash_is_not_answered_from(client: TestClient, root: Path) -> None:
    _keep(root, _solution("plate_calibrated"), digest="0" * 64)
    assert _ask(client, "how far is Rigel?", status=409) == {"detail": samples_router.UNEXPECTED_SAMPLE_FILE}


@pytest.mark.parametrize("case", ["named_for_another_photo", "another_photos_hash", "another_asset_id",
                                  "refused_by_the_contract", "made_by_another_revision"])
def test_a_solve_the_setup_would_not_keep_is_answered_as_an_unsolved_photo(client: TestClient, root: Path, case: str) -> None:
    """sample-solution-producer-one: the setup's own rule decides what is kept, so the answers never come from a solve
    the solution route would not serve."""
    solution = {
        "named_for_another_photo": _solution("plate_calibrated", sha=OTHER),
        "another_photos_hash": {**_solution("plate_calibrated"), "sourceSha256": OTHER},
        "another_asset_id": {**_solution("plate_calibrated"), "sourceAssetId": f"src_{OTHER[:16]}"},
        "refused_by_the_contract": {**_solution("plate_calibrated"), "status": "made up"},
        "made_by_another_revision": _solution("plate_calibrated"),
    }[case]
    _keep(root, solution)
    if case == "made_by_another_revision":
        current = samples_make.current_producer()
        night = samples_make.load_samples(root / "data" / "samples.json")[0]
        (root / samples_make.producer_path(night)).write_text(json.dumps({**current, "revision": current["revision"] - 1}), encoding="utf-8")
    answer = _ask(client, "how far is Rigel?")
    assert (answer["status"], answer["reason"]) == ("unanswerable", "photo_not_solved")


def test_a_hash_that_is_neither_an_upload_nor_a_sample_is_still_not_found(client: TestClient, root: Path) -> None:
    _keep(root, _solution("plate_calibrated"))
    missing = _ask(client, "how far is Rigel?", sha="1" * 64, status=404)
    assert missing == {"detail": "no file with that hash is stored"}
