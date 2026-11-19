"""The solve route with a person's alignment (align-2s; D24's Solve from here; the refusals in C15's words,
.lead/uiux/UIUX-alignwords-READY.md, decided 12:35).

Covers, with a JPEG whose EXIF gives a capture time and an open-ocean place (the capture-time tests' own), and the
solve kind's run replaced by a fake that waits (the route tests' pattern):
- A start with an alignment body answers 202 with a job whose request is the alignment; the same alignment joins it
  with 200; a plain start beside it is its own job; another alignment is a new job
- A body that is not an alignment (not JSON, an unknown key, a value out of range, another source) is refused with
  C15's second sentence, naming no value, and makes no job
- An alignment for an upload with no capture time or no place is refused with C15's first sentence, built as the
  web's own no-sky sentence is (when, where, when or where; photo taken, clip recorded), and makes no job
- A plain start with no body is as before
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Iterator

from fastapi.testclient import TestClient
import pytest

from services.api import create_app
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.job_store import JobStore
from services.ingest.runner import KindResult
from services.ingest.store import IngestStore
from tests.integration.test_capture_time_from_gps import FIX, LOCAL, OCEAN, gps_stamps, jpeg, local_tag, position

ALIGNMENT = {"format": "albedian.person-alignment", "formatVersion": 1, "source": "person", "centerAltDeg": 41.5,
             "centerAzDeg": 128.25, "rollDeg": -6.0, "fovDiagonalDeg": 68.5}
NOT_AN_ALIGNMENT = "The alignment sent with this solve does not meet the contract, so the photo was not solved from it."


class Waits:
    def __init__(self) -> None:
        self.release = threading.Event()

    def run(self, ctx: Any) -> KindResult:
        assert self.release.wait(30.0)
        return KindResult("failed")


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    waits = Waits()
    monkeypatch.setitem(job_runner._KINDS, "solve", dataclasses.replace(ingest_jobs.SOLVE_KIND, run=waits.run))
    client = TestClient(app)
    bodies = {
        "both": jpeg({"DateTimeOriginal": local_tag(LOCAL)}, {**position(OCEAN), **gps_stamps(FIX)}),
        # A local time with its offset is an instant with no place; without the offset the instant needs the place
        "no_place": jpeg({"DateTimeOriginal": local_tag(LOCAL), "OffsetTimeOriginal": "-03:00"}, None),
        "neither": jpeg(None, None),
    }
    shas = {}
    for name, body in bodies.items():
        answer = client.post("/api/ingest", content=body, headers={"content-type": "image/jpeg"})
        assert answer.status_code == 201, answer.text
        shas[name] = hashlib.sha256(body).hexdigest()
    yield {"client": client, "store": store, "jobs": JobStore(store), "shas": shas}
    waits.release.set()
    job_runner.runner_for(store).shutdown()


def _post(client: TestClient, sha: str, body: Any = None, raw: bytes | None = None) -> Any:
    if raw is not None:
        return client.post(f"/api/ingest/{sha}/solve", content=raw, headers={"content-type": "application/json"})
    if body is None:
        return client.post(f"/api/ingest/{sha}/solve")
    return client.post(f"/api/ingest/{sha}/solve", content=json.dumps(body), headers={"content-type": "application/json"})


def test_an_aligned_start_is_its_own_job_and_the_same_alignment_joins_it(setup: dict[str, Any]) -> None:
    client, sha = setup["client"], setup["shas"]["both"]
    first = _post(client, sha, ALIGNMENT)
    assert first.status_code == 202, first.text
    job = first.json()
    assert job["request"] == ALIGNMENT and job["kind"] == "solve"
    again = _post(client, sha, dict(reversed(list(ALIGNMENT.items()))))
    assert again.status_code == 200 and again.json()["id"] == job["id"]
    plain = _post(client, sha)
    assert plain.status_code == 202 and plain.json()["id"] != job["id"] and "request" not in plain.json()
    other = _post(client, sha, {**ALIGNMENT, "rollDeg": 4.0})
    assert other.status_code == 202 and other.json()["id"] not in (job["id"], plain.json()["id"])


@pytest.mark.parametrize("raw", [
    b"not json",
    json.dumps({**ALIGNMENT, "extra": 1}).encode(),
    json.dumps({**ALIGNMENT, "centerAltDeg": 91.0}).encode(),
    json.dumps({**ALIGNMENT, "source": "record"}).encode(),
    json.dumps([ALIGNMENT]).encode(),
])
def test_a_body_that_is_not_an_alignment_is_refused_in_c15s_words_and_makes_no_job(setup: dict[str, Any], raw: bytes) -> None:
    client, sha = setup["client"], setup["shas"]["both"]
    answer = _post(client, sha, raw=raw)
    assert answer.status_code == 422
    assert answer.json()["detail"] == NOT_AN_ALIGNMENT
    assert "91" not in answer.text
    assert setup["jobs"].jobs_of(sha) == []


@pytest.mark.parametrize("name, which", [("no_place", "where"), ("neither", "when or where")])
def test_an_alignment_for_a_photo_with_no_time_or_place_is_refused_in_c15s_words(
    setup: dict[str, Any], name: str, which: str
) -> None:
    client, sha = setup["client"], setup["shas"][name]
    answer = _post(client, sha, ALIGNMENT)
    assert answer.status_code == 422
    assert answer.json()["detail"] == f"Your photo does not say {which} it was taken, so its sky cannot be lined up by hand."
    assert setup["jobs"].jobs_of(sha) == []


def test_the_sentence_for_a_clip_says_recorded() -> None:
    assert ingest_router.alignment_needs_moment({"mimeType": "video/quicktime"}) == (
        "Your clip does not say when or where it was recorded, so its sky cannot be lined up by hand."
    )
    assert ingest_router.alignment_needs_moment(
        {"mimeType": "image/jpeg", "observerLocation": {"latDeg": 1.0, "lonDeg": 2.0}}
    ) == "Your photo does not say when it was taken, so its sky cannot be lined up by hand."
    assert ingest_router.alignment_needs_moment(
        {"mimeType": "image/jpeg", "capturedAtUTC": "2031-07-01T04:37:52Z", "observerLocation": {"latDeg": 1.0, "lonDeg": 2.0}}
    ) is None


def test_a_plain_start_with_no_body_is_as_before(setup: dict[str, Any]) -> None:
    client, sha = setup["client"], setup["shas"]["neither"]
    answer = _post(client, sha)
    assert answer.status_code == 202 and "request" not in answer.json()
