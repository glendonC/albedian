"""first-use-sample-one (DECIDED-BY-LEAD 25 Nov 03:22 and 03:43): the sample night served read-only. The record as
tracked, the photo from the shared cache only when its bytes are the recorded ones, and the app's own solve of it
from the cache only when its SHA-256 matches the one kept beside it; 404 for what is not on this machine yet, 409
for what fails its hash, and nothing ever fetched or solved by a read. A temporary root stands for the repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.api import create_app
from services.samples import router as samples_router

ROOT = Path(__file__).resolve().parents[2]
RECORD = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
NIGHT = RECORD["samples"][0]
PHOTO = b"\xff\xd8\xff\xe0 a stand-in photo for the route's tests \xff\xd9"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    record = json.loads(json.dumps(RECORD))
    record["samples"][0]["photo"]["sha256"] = hashlib.sha256(PHOTO).hexdigest()
    record["samples"][0]["photo"]["bytes"] = len(PHOTO)
    (tmp_path / "data" / "samples.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(root: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[samples_router.get_samples_root] = lambda: root
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _put_photo(root: Path, body: bytes = PHOTO) -> None:
    path = root / NIGHT["photo"]["downloadPath"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _put_solution(root: Path, solution: dict[str, Any], digest: str | None = None) -> bytes:
    text = (json.dumps(solution) + "\n").encode("utf-8")
    path = root / NIGHT["solution"]["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    (root / NIGHT["solution"]["sha256Path"]).write_text((digest or hashlib.sha256(text).hexdigest()) + "\n", encoding="utf-8")
    return text


def _solution() -> dict[str, Any]:
    return json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))["plate_calibrated"]


def test_the_record_is_served_as_tracked(client: TestClient, root: Path) -> None:
    answer = client.get("/api/samples")
    assert answer.status_code == 200
    assert answer.json()["samples"] == json.loads((root / "data" / "samples.json").read_text(encoding="utf-8"))["samples"]
    assert answer.json()["samples"][0]["kind"] == "sample"


def test_the_photo_is_served_only_as_recorded(client: TestClient, root: Path) -> None:
    sample = NIGHT["id"]
    assert client.get(f"/api/samples/{sample}/original").status_code == 404
    _put_photo(root)
    served = client.get(f"/api/samples/{sample}/original")
    assert (served.status_code, served.content, served.headers["content-type"]) == (200, PHOTO, "image/jpeg")
    _put_photo(root, PHOTO + b"changed")
    assert client.get(f"/api/samples/{sample}/original").status_code == 409


def test_the_solution_is_served_only_when_made_and_its_hash_holds(client: TestClient, root: Path) -> None:
    sample = NIGHT["id"]
    missing = client.get(f"/api/samples/{sample}/solution")
    assert (missing.status_code, missing.json()) == (404, {"detail": samples_router.NO_SAMPLE_SOLUTION})
    solution = _solution()
    _put_solution(root, solution)
    served = client.get(f"/api/samples/{sample}/solution")
    assert (served.status_code, served.json()) == (200, solution)
    _put_solution(root, solution, digest="0" * 64)
    assert client.get(f"/api/samples/{sample}/solution").status_code == 409


def test_an_unknown_sample_and_a_malformed_id_are_refused(client: TestClient) -> None:
    for path in ("/api/samples/night-unknown/original", "/api/samples/night-unknown/solution"):
        assert client.get(path).status_code == 404
    assert client.get("/api/samples/..%2Fdata/original").status_code in (404, 422)


def test_a_read_fetches_nothing_and_solves_nothing(client: TestClient, root: Path) -> None:
    client.get(f"/api/samples/{NIGHT['id']}/original")
    client.get(f"/api/samples/{NIGHT['id']}/solution")
    assert not (root / ".cache").exists(), "a read writes no file"


def test_the_routes_read_only() -> None:
    operations = create_app().openapi()["paths"]
    for path in ("/api/samples", "/api/samples/{sample_id}/original", "/api/samples/{sample_id}/solution"):
        assert set(operations[path]) == {"get"}, path
        assert "requestBody" not in operations[path]["get"]
