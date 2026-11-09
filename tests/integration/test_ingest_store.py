"""Uploads stored on this machine (roadmap Track 1 M1): content addressing, write-once storage and refusals.

Synthetic files only. Each test stores into a temporary folder; the real default, data/ingest/, is
ignored by git (checked below), so an upload is never committed.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from hypothesis import example, given, settings
from hypothesis import strategies as st
from PIL import Image

from services.api import app
from services.contracts.ingest import IngestReceipt
from services.ingest import router as ingest_router
from services.ingest.store import DEFAULT_ROOT, SNIFF_BYTES, IngestStore, Refused, sniff

ROOT = Path(__file__).resolve().parents[2]
HEIC = ROOT / "tests" / "fixtures" / "ingest" / "synthetic-exif.heic"


def jpeg_bytes(shade: int = 30, size: tuple[int, int] = (64, 48), orientation: int | None = None) -> bytes:
    out = io.BytesIO()
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    Image.new("RGB", size, (shade, shade, shade)).save(out, "JPEG", exif=exif)
    return out.getvalue()


def files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=5_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def upload(client: TestClient, data: bytes, content_type: str | None = "image/jpeg", **headers: str):
    sent = {"content-type": content_type} if content_type else {}
    sent.update({key.replace("_", "-"): value for key, value in headers.items()})
    return client.post("/api/ingest", content=data, headers=sent)


def test_an_upload_is_stored_once_under_its_hash_and_read_only(client: TestClient, store: IngestStore) -> None:
    data = jpeg_bytes()
    sha = hashlib.sha256(data).hexdigest()
    first = upload(client, data)
    assert first.status_code == 201, first.text
    receipt = first.json()
    IngestReceipt.model_validate(receipt)
    asset = receipt["sourceAsset"]
    assert receipt["alreadyStored"] is False
    assert (asset["sha256"], asset["byteSize"], asset["mimeType"]) == (sha, len(data), "image/jpeg")
    assert asset["uri"] == f"/api/ingest/{sha}/original"
    # A still has no frame rate: its asset carries none, and a duration of 0
    assert "fpsNumerator" not in asset and "fpsDenominator" not in asset and asset["durationMs"] == 0
    assert asset["privacyClass"] == "private" and asset["immutable"] is True
    stored = store.root / sha[:2] / sha / "original.jpg"
    assert stored.read_bytes() == data
    assert stored.stat().st_mode & 0o222 == 0, "the stored original is read-only"
    before = stored.stat()

    again = upload(client, data, x_albedian_filename="another-name.jpg")
    assert again.status_code == 200
    assert again.json()["alreadyStored"] is True
    assert again.json()["sourceAsset"] == asset, "the first record stands"
    after = stored.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (before.st_ino, before.st_mtime_ns, before.st_size)

    other = upload(client, jpeg_bytes(shade=31))
    assert other.status_code == 201 and other.json()["sourceAsset"]["sha256"] != sha

    read = client.get(f"/api/ingest/{sha}")
    assert read.status_code == 200 and read.json() == {**receipt, "alreadyStored": True}
    original = client.get(f"/api/ingest/{sha}/original")
    assert original.status_code == 200 and original.content == data
    assert original.headers["content-type"] == "image/jpeg"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert files_under(store.root) == sorted(
        f"{h[:2]}/{h}/{name}" for h in (sha, other.json()["sourceAsset"]["sha256"]) for name in ("original.jpg", "record.json")
    )


def test_an_uploaded_photo_carries_the_orientation_its_exif_states(client: TestClient) -> None:
    # EXIF Orientation 6: the stored pixels are shown a quarter turn clockwise
    response = upload(client, jpeg_bytes(orientation=6))
    assert response.status_code == 201, response.text
    receipt = response.json()
    IngestReceipt.model_validate(receipt)
    source = {"container": "exif", "tags": ["Orientation"]}
    assert receipt["hints"]["orientation"] == {"quarterTurnsClockwise": 1, "mirrored": False, "source": source}
    assert receipt["sourceAsset"]["displayOrientation"] == {"quarterTurnsClockwise": 1, "mirrored": False}
    assert receipt["sourceAsset"]["metadataSources"]["displayOrientation"] == source
    # A photo whose EXIF says nothing of it has no orientation
    plain = upload(client, jpeg_bytes(shade=31)).json()
    assert "orientation" not in plain["hints"] and "displayOrientation" not in plain["sourceAsset"]


@pytest.mark.parametrize(
    ("data", "content_type", "status", "reason"),
    [
        (b"plain text, not a picture" * 4, "image/jpeg", 415, "not a JPEG"),
        (jpeg_bytes(), "video/mp4", 415, "declared as video/mp4"),
        (jpeg_bytes(), None, 415, "must declare its Content-Type"),
        (b"", "image/jpeg", 422, "empty"),
        (jpeg_bytes()[:40], "image/jpeg", 422, "could not be read"),
        (b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1" + b"\x00" * 64, "image/heic", 415, "'avif'"),
        (HEIC.read_bytes()[:1000], "image/heic", 422, "HEIC file could not be read"),
    ],
)
def test_a_refused_upload_leaves_nothing_behind(
    client: TestClient, store: IngestStore, data: bytes, content_type: str | None, status: int, reason: str
) -> None:
    response = upload(client, data, content_type)
    assert response.status_code == status, response.text
    assert reason in response.json()["detail"]
    assert files_under(store.root) == []


def test_an_upload_over_the_cap_is_refused_before_it_is_kept(tmp_path: Path) -> None:
    small = IngestStore(tmp_path / "small", max_bytes=1000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: small
    try:
        client = TestClient(app)
        data = jpeg_bytes(size=(256, 256))
        assert len(data) > 1000
        response = upload(client, data)
        assert response.status_code == 413 and "1000-byte limit" in response.json()["detail"]
        claimed = client.post("/api/ingest", content=b"\xff\xd8\xff", headers={"content-type": "image/jpeg", "content-length": "5000"})
        assert claimed.status_code == 413
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    assert files_under(small.root) == []


def test_the_cap_holds_while_streaming_when_no_length_is_declared(tmp_path: Path) -> None:
    store = IngestStore(tmp_path / "streamed", max_bytes=1000)
    data = jpeg_bytes(size=(256, 256))
    with pytest.raises(Refused) as refused:
        asyncio.run(store.receive(_chunks(data, 100), "image/jpeg"))
    assert refused.value.status == 413
    assert files_under(store.root) == []


def test_a_still_is_capped_lower_than_a_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = IngestStore(tmp_path / "caps", max_bytes=5000, max_still_bytes=1000)
    still = b"\xff\xd8\xff\xe0" + b"\x00" * 2000
    clip = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 2000
    with pytest.raises(Refused) as refused:
        asyncio.run(store.receive(_chunks(still, 100), "image/jpeg"))
    assert refused.value.status == 413 and "1000-byte limit for a still" in refused.value.reason
    assert asyncio.run(store.receive(_chunks(clip, 100), "video/quicktime")).byte_size == len(clip)
    with pytest.raises(Refused, match="for a still"):
        asyncio.run(store.receive(_chunks(b"", 1), "image/heic", declared_length=2000))
    assert asyncio.run(store.receive(_chunks(clip, 100), "video/quicktime", declared_length=len(clip))).byte_size == len(clip)
    defaults = IngestStore(tmp_path / "defaults")
    assert (defaults.max_still_bytes, defaults.max_bytes) == (256 * 1024**2, 2 * 1024**3)
    monkeypatch.setenv("ALBEDIAN_INGEST_DIR", str(tmp_path / "env"))
    monkeypatch.setenv("ALBEDIAN_INGEST_MAX_BYTES", "9000")
    monkeypatch.setenv("ALBEDIAN_INGEST_MAX_STILL_BYTES", "3000")
    configured = IngestStore.from_environment()
    assert (configured.root, configured.max_bytes, configured.max_still_bytes) == (tmp_path / "env", 9000, 3000)


def test_a_partial_file_left_for_a_day_is_swept_when_the_store_opens(tmp_path: Path) -> None:
    partial_dir = tmp_path / "swept" / ".partial"
    partial_dir.mkdir(parents=True)
    stale, fresh, other = partial_dir / "stale.part", partial_dir / "fresh.part", partial_dir / "notes.txt"
    for path in (stale, fresh, other):
        path.write_bytes(b"\xff\xd8\xff")
    day_and_an_hour_ago = time.time() - 25 * 3600
    for path in (stale, other):
        os.utime(path, (day_and_an_hour_ago, day_and_an_hour_ago))
    IngestStore(tmp_path / "swept")
    assert not stale.exists() and fresh.exists() and other.exists()


@pytest.mark.parametrize("name", ["../../etc", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "/", ""])
def test_only_a_full_hash_names_a_folder(store: IngestStore, name: str) -> None:
    with pytest.raises(ValueError):
        store.folder(name)
    with pytest.raises(ValueError):
        store.original(name)
    with pytest.raises(ValueError):
        store.read_record(name)
    assert store.folder("ab" * 32) == store.root / "ab" / ("ab" * 32)


def test_a_record_is_written_once(store: IngestStore) -> None:
    sha = "cd" * 32
    first = store.write_record(sha, {"title": "first"})
    second = store.write_record(sha, {"title": "second"})
    assert first == second == {"title": "first"}
    record = store.folder(sha) / "record.json"
    assert record.stat().st_mode & 0o222 == 0, "the record is read-only"
    assert sorted(path.name for path in record.parent.iterdir()) == ["record.json"], "no scratch file is left"


def test_only_the_owner_can_read_the_store(tmp_path: Path) -> None:
    # An original and its record can hold a capture time and a position: no other account on the machine reads
    # them, and no other account lists or enters a store folder, including a root that existed before the store
    root = tmp_path / "store"
    root.mkdir()
    os.chmod(root, 0o755)
    store = IngestStore(root, max_bytes=5_000_000)
    assert root.stat().st_mode & 0o777 == 0o700, "an existing root is closed when the store opens it"
    data = jpeg_bytes(shade=50)
    sha = hashlib.sha256(data).hexdigest()
    original, already = store.commit(asyncio.run(store.receive(_chunks(data, 100), "image/jpeg")))
    assert already is False
    record = store.folder(sha) / "record.json"
    store.write_record(sha, {"title": "private"})
    assert original.stat().st_mode & 0o777 == 0o400
    assert record.stat().st_mode & 0o777 == 0o400
    for folder in (root, root / ".partial", root / sha[:2], store.folder(sha)):
        assert folder.stat().st_mode & 0o777 == 0o700, folder.relative_to(tmp_path)


def test_the_file_name_is_only_a_title(client: TestClient, store: IngestStore, tmp_path: Path) -> None:
    data = jpeg_bytes(shade=40)
    sha = hashlib.sha256(data).hexdigest()
    response = upload(client, data, x_albedian_filename="..%2F..%2Fescape%0A.jpg")
    assert response.status_code == 201
    assert response.json()["sourceAsset"]["title"] == "escape.jpg"
    assert files_under(store.root) == [f"{sha[:2]}/{sha}/original.jpg", f"{sha[:2]}/{sha}/record.json"]
    assert not (tmp_path / "escape.jpg").exists()
    untitled = upload(client, jpeg_bytes(shade=41))
    assert untitled.json()["sourceAsset"]["title"].startswith("Upload ")


def test_only_a_full_hash_reaches_a_stored_file(client: TestClient) -> None:
    sha = hashlib.sha256(jpeg_bytes(shade=50)).hexdigest()
    assert client.get(f"/api/ingest/{sha}").status_code == 404
    assert client.get(f"/api/ingest/{sha}/original").status_code == 404
    assert client.get(f"/api/ingest/{sha.upper()}").status_code == 422
    assert client.get("/api/ingest/..%2F..%2Fetc%2Fpasswd").status_code in (404, 422)
    assert client.get("/api/ingest").status_code == 405


def test_the_default_store_is_ignored_by_git() -> None:
    assert DEFAULT_ROOT == ROOT / "data" / "ingest"
    probe = "data/ingest/ab/" + "ab" * 32 + "/original.jpg"
    assert subprocess.run(["git", "check-ignore", "-q", probe], cwd=ROOT).returncode == 0


@pytest.mark.parametrize(
    ("head", "mime_type"),
    [
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "image/heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic", "image/heic"),
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  ", "video/quicktime"),
        (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2", "video/mp4"),
    ],
)
def test_the_type_comes_from_the_first_bytes(head: bytes, mime_type: str) -> None:
    assert sniff(head + b"\x00" * 32).mime_type == mime_type


@pytest.mark.parametrize("head", [b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avif", b"\x00\x00\x00\x10ftypcrx \x00\x00\x00\x00", b"GIF89a", b""])
def test_other_files_are_refused_as_unsupported(head: bytes) -> None:
    with pytest.raises(Refused) as refused:
        sniff(head + b"\x00" * 32)
    assert refused.value.status == 415


MAGIC = {
    "image/jpeg": b"\xff\xd8\xff\xe0",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "video/quicktime": b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  ",
}


async def _chunks(data: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start:start + size]


@settings(max_examples=60, deadline=None)
@given(
    declared=st.sampled_from(sorted(MAGIC)),
    tail=st.binary(max_size=3 * SNIFF_BYTES),
    chunk=st.integers(min_value=1, max_value=4 * SNIFF_BYTES),
)
@example(declared="image/jpeg", tail=b"", chunk=1)
@example(declared="video/quicktime", tail=b"\x00" * SNIFF_BYTES, chunk=SNIFF_BYTES)
def test_pbt_ingest_store_1_any_bytes_come_back_identical_under_their_hash(declared: str, tail: bytes, chunk: int) -> None:
    data = MAGIC[declared] + tail
    with tempfile.TemporaryDirectory() as folder:
        store = IngestStore(Path(folder), max_bytes=10_000)
        received = asyncio.run(store.receive(_chunks(data, chunk), declared))
        assert received.sha256 == hashlib.sha256(data).hexdigest() and received.byte_size == len(data)
        path, already = store.commit(received)
        assert not already and path.read_bytes() == data
        assert path.parent.name == received.sha256 and path.parent.parent.name == received.sha256[:2]
        # The same bytes again: stored once, the first file untouched, no partial left
        again = asyncio.run(store.receive(_chunks(data, chunk + 1), declared))
        second, already = store.commit(again)
        assert already and second == path and path.read_bytes() == data
        assert files_under(Path(folder)) == [str(path.relative_to(folder))]
