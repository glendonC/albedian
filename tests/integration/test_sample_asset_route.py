"""sample-asset-one: `GET /api/samples/{sample_id}/asset`, a sample night's photo as the app reads it. The web builds a
reveal from a SourceAsset and its solve, and an upload's comes in its receipt, which a sample never has (never an
upload, DECIDED-BY-LEAD 25 Nov 07:11 and 08:00). So the sample's own route gives it: the SourceAsset the ingest's own
reader makes of the photo whose bytes the record names, the same as `POST /api/ingest` reads of those bytes but for
its privacy (a public sample), where it is shown from (the sample's own route) and its title (the record's). 404 until
the photo is on this machine, 409 when it fails its hash; a read never fetches. A temporary root stands for the
repository."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from services.api import create_app
from services.contracts.samples import SampleAsset, SampleNight, sample_asset_matches
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.store import IngestStore
from services.samples import router as samples_router

ROOT = Path(__file__).resolve().parents[2]
RECORD = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
NIGHT_ID = RECORD["samples"][0]["id"]
# EXIF Orientation 6: shown turned a quarter clockwise; and a camera's model, both read by the ingest's own reader
ORIENTATION_TAG, MODEL_TAG = 0x0112, 0x0110


def _photo() -> bytes:
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = 6
    exif[MODEL_TAG] = "Stand-in camera"
    buffer = io.BytesIO()
    Image.new("L", (64, 48), 20).save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


PHOTO = _photo()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    record = json.loads(json.dumps(RECORD))
    record["samples"][0]["photo"]["sha256"] = hashlib.sha256(PHOTO).hexdigest()
    record["samples"][0]["photo"]["bytes"] = len(PHOTO)
    (tmp_path / "data" / "samples.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(tmp_path: Path):
    store = IngestStore(tmp_path / "ingest", max_bytes=16 * 1024 * 1024)
    yield store
    job_runner.runner_for(store).shutdown()


@pytest.fixture
def client(root: Path, store: IngestStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[samples_router.get_samples_root] = lambda: root
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _night(root: Path) -> SampleNight:
    return SampleNight.model_validate(json.loads((root / "data" / "samples.json").read_text(encoding="utf-8"))["samples"][0])


def _put_photo(root: Path, body: bytes = PHOTO) -> Path:
    path = root / RECORD["samples"][0]["photo"]["downloadPath"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_the_photo_as_the_ingests_reader_makes_it_a_public_sample_of_this_night(client: TestClient, root: Path) -> None:
    _put_photo(root)
    answer = client.get(f"/api/samples/{NIGHT_ID}/asset")
    assert answer.status_code == 200
    asset = SampleAsset.model_validate(answer.json())
    night = _night(root)
    assert sample_asset_matches(night, asset)
    assert (asset.privacy_class, asset.uri, asset.title) == ("public_sample", f"/api/samples/{NIGHT_ID}/original", night.title)
    assert asset.id == f"src_{night.photo.sha256[:16]}"
    # The stored pixels' size, and how to show them, as the file says
    assert (asset.width, asset.height) == (64, 48)
    assert (asset.display_orientation.quarter_turns_clockwise, asset.display_orientation.mirrored) == (1, False)
    assert asset.camera_metadata.model == "Stand-in camera"


def test_it_is_what_an_upload_of_those_bytes_reads_but_for_privacy_route_and_title(client: TestClient, root: Path) -> None:
    _put_photo(root)
    sample = client.get(f"/api/samples/{NIGHT_ID}/asset").json()
    upload = client.post("/api/ingest", content=PHOTO, headers={"content-type": "image/jpeg", "x-albedian-filename": "a.jpg"})
    assert upload.status_code == 201
    uploaded: dict[str, Any] = upload.json()["sourceAsset"]
    differ = {key for key in set(sample) | set(uploaded) if sample.get(key) != uploaded.get(key)}
    assert differ == {"privacyClass", "uri", "title"}
    assert uploaded["privacyClass"] == "private"


def test_it_answers_only_for_the_recorded_bytes_here_and_never_fetches(client: TestClient, root: Path) -> None:
    missing = client.get(f"/api/samples/{NIGHT_ID}/asset")
    assert (missing.status_code, missing.json()) == (404, {"detail": samples_router.NO_SAMPLE_PHOTO})
    assert not (root / ".cache").exists()
    _put_photo(root, PHOTO + b"changed")
    changed = client.get(f"/api/samples/{NIGHT_ID}/asset")
    assert (changed.status_code, changed.json()) == (409, {"detail": samples_router.UNEXPECTED_SAMPLE_FILE})


def test_bytes_of_the_recorded_size_but_another_hash_are_refused(client: TestClient, root: Path) -> None:
    _put_photo(root, PHOTO[:-1] + bytes([PHOTO[-1] ^ 1]))
    assert client.get(f"/api/samples/{NIGHT_ID}/asset").status_code == 409


def test_the_recorded_bytes_under_a_record_of_another_size_are_refused(client: TestClient, root: Path) -> None:
    record_path = root / "data" / "samples.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["samples"][0]["photo"]["bytes"] = len(PHOTO) + 1
    record_path.write_text(json.dumps(record), encoding="utf-8")
    _put_photo(root)
    assert client.get(f"/api/samples/{NIGHT_ID}/asset").status_code == 409


def test_a_record_naming_the_bytes_under_another_type_is_not_served_as_its_photo(client: TestClient, root: Path) -> None:
    record_path = root / "data" / "samples.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["samples"][0]["photo"]["mimeType"] = "image/png"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    _put_photo(root)
    wrong = client.get(f"/api/samples/{NIGHT_ID}/asset")
    assert (wrong.status_code, wrong.json()) == (409, {"detail": samples_router.UNEXPECTED_SAMPLE_FILE})


def test_a_photo_linked_from_elsewhere_is_not_served(client: TestClient, root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.jpg"
    elsewhere.write_bytes(PHOTO)
    path = root / RECORD["samples"][0]["photo"]["downloadPath"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(elsewhere)
    assert client.get(f"/api/samples/{NIGHT_ID}/asset").status_code == 404
    path.unlink()
    _put_photo(root)
    assert client.get(f"/api/samples/{NIGHT_ID}/asset").status_code == 200


def test_an_unknown_night_is_404_and_an_id_of_another_shape_is_refused(client: TestClient, root: Path) -> None:
    _put_photo(root)
    unknown = client.get("/api/samples/night-other/asset")
    assert (unknown.status_code, unknown.json()) == (404, {"detail": samples_router.NO_SAMPLE})
    assert client.get("/api/samples/Night%2012/asset").status_code == 422
