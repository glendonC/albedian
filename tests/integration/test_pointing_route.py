"""The pointing record's route (pointing-2b; ios-capture-companion design component 4; REQ-IOS-1.1, 4.1, 6.1; P3).

`PUT /api/ingest/{sha256}/pointing` keeps the exact body bytes of a pointing record beside the photo's receipt, and
`GET` of the same path returns them. The rules: 404 when no photo with that hash is stored (also for an address that
is not a SHA-256); 422 when the body is not JSON text, not a record by its contract, or a record of another photo;
201 for a record stored now and 200 for the same bytes again, each a receipt that names the record's own hash; 409
for a different record for the same photo, and for a first record once a solve job exists for it. Every refusal is the reply contract's (closed sentences, paths of the
record's own keys), and no value of a record reaches a refusal or a log line. P3: the photo's stored bytes and
SHA-256 equal the uploaded ones, with a record and without, and the photo's receipt does not change.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import stat
import threading
from typing import Any, Iterator

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.contracts.jobs import build_job_samples
from services.contracts.pointing import POINTING_RULES
from services.contracts.pointing_reply import (
    POINTING_RECEIPT_FORMAT,
    POINTING_REPLY_SENTENCES,
    PointingRecordReceipt,
    PointingRecordRefusal,
)
from services.ingest import router as ingest_router
from services.ingest.job_store import JobStore
from services.ingest.store import PRIVATE_FILE_MODE, IngestStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pointing_contract.json"
SAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))["valid"]
ROUTE = POINTING_REPLY_SENTENCES
NOT_A_RECORD = ROUTE["notARecord"]
NO_SUCH_PHOTO = {"detail": ROUTE["noSuchPhoto"]}


def _png(seed: int, width: int = 16, height: int = 12) -> bytes:
    pixels = np.random.default_rng(seed).integers(0, 256, size=(height, width), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels, mode="L").save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=10_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _upload(client: TestClient, data: bytes) -> str:
    response = client.post("/api/ingest", content=data, headers={"content-type": "image/png"})
    assert response.status_code in (200, 201), response.text
    sha = response.json()["sourceAsset"]["sha256"]
    assert sha == hashlib.sha256(data).hexdigest()
    return sha


def _record(sha: str, sample: int = 0) -> dict[str, Any]:
    record = copy.deepcopy(SAMPLES[sample]["record"])
    record["sourceAssetSha256"] = sha
    return record


def _body(record: Any) -> bytes:
    return json.dumps(record).encode("utf-8")


def _put(client: TestClient, sha: str, body: bytes) -> Any:
    return client.put(f"/api/ingest/{sha}/pointing", content=body, headers={"content-type": "application/json"})


def _refusal(response: Any) -> dict[str, Any]:
    body = response.json()
    PointingRecordRefusal.model_validate(body)
    return body


def _pointing_file(store: IngestStore, sha: str) -> Path:
    return store.folder(sha) / "pointing.json"


# ---- Stored, the same again, a different one ----


def test_a_record_for_a_stored_photo_is_kept_byte_for_byte_and_its_receipt_names_its_own_hash(
    client: TestClient, store: IngestStore
) -> None:
    sha = _upload(client, _png(1))
    body = _body(_record(sha))
    response = _put(client, sha, body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    PointingRecordReceipt.model_validate(receipt)
    assert receipt == {
        "format": POINTING_RECEIPT_FORMAT,
        "formatVersion": 1,
        "sourceAssetSha256": sha,
        "pointingRecordSha256": hashlib.sha256(body).hexdigest(),
        "alreadyStored": False,
    }
    assert _pointing_file(store, sha).read_bytes() == body
    got = client.get(f"/api/ingest/{sha}/pointing")
    assert got.status_code == 200
    assert got.content == body
    assert got.headers["content-type"] == "application/json"
    assert got.headers["x-content-type-options"] == "nosniff"


def test_the_record_is_kept_private_like_the_photo(client: TestClient, store: IngestStore) -> None:
    sha = _upload(client, _png(2))
    assert _put(client, sha, _body(_record(sha))).status_code == 201
    path = _pointing_file(store, sha)
    assert stat.S_IMODE(os.lstat(path).st_mode) == PRIVATE_FILE_MODE
    assert not any(name.endswith(".tmp") for name in os.listdir(path.parent))


def test_the_same_bytes_again_are_a_receipt_already_stored(client: TestClient) -> None:
    sha = _upload(client, _png(3))
    body = _body(_record(sha))
    assert _put(client, sha, body).status_code == 201
    again = _put(client, sha, body)
    assert again.status_code == 200
    assert again.json() == {
        "format": POINTING_RECEIPT_FORMAT,
        "formatVersion": 1,
        "sourceAssetSha256": sha,
        "pointingRecordSha256": hashlib.sha256(body).hexdigest(),
        "alreadyStored": True,
    }


@pytest.mark.parametrize("other", ["another sample", "the same record spaced otherwise"])
def test_a_different_record_for_the_same_photo_is_refused_and_the_first_stands(
    client: TestClient, store: IngestStore, other: str
) -> None:
    sha = _upload(client, _png(4))
    first = _body(_record(sha))
    assert _put(client, sha, first).status_code == 201
    second = _body(_record(sha, 5)) if other == "another sample" else json.dumps(_record(sha), indent=1).encode()
    assert second != first
    response = _put(client, sha, second)
    assert response.status_code == 409
    assert _refusal(response) == {"detail": ROUTE["differentRecordHeld"]}
    assert _pointing_file(store, sha).read_bytes() == first
    assert client.get(f"/api/ingest/{sha}/pointing").content == first


def test_two_different_records_sent_at_once_leave_exactly_one(store: IngestStore, client: TestClient) -> None:
    sha = _upload(client, _png(5))
    bodies = [_body(_record(sha, n)) for n in range(6)]
    results: list[tuple[bytes, bool]] = []
    start = threading.Barrier(len(bodies))

    def write(body: bytes) -> None:
        start.wait()
        results.append(store.write_pointing(sha, body))

    threads = [threading.Thread(target=write, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    kept = _pointing_file(store, sha).read_bytes()
    assert kept in bodies
    assert sorted(stored for _, stored in results) == [False] * 5 + [True]
    assert all(standing == kept for standing, _ in results)


# ---- No such photo ----


def test_a_record_for_no_stored_photo_is_refused_404_and_nothing_is_made(
    client: TestClient, store: IngestStore
) -> None:
    sha = hashlib.sha256(b"never uploaded").hexdigest()
    response = _put(client, sha, _body(_record(sha)))
    assert response.status_code == 404
    assert _refusal(response) == NO_SUCH_PHOTO
    assert not store.folder(sha).exists()
    got = client.get(f"/api/ingest/{sha}/pointing")
    assert got.status_code == 404
    assert _refusal(got) == NO_SUCH_PHOTO


@pytest.mark.parametrize("address", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "zqxSecret"])
def test_an_address_that_is_not_a_sha256_is_answered_as_no_such_photo(client: TestClient, address: str) -> None:
    for response in (_put(client, address, b"{}"), client.get(f"/api/ingest/{address}/pointing")):
        assert response.status_code == 404
        assert _refusal(response) == NO_SUCH_PHOTO


def test_get_without_a_record_answers_404_with_a_plain_sentence(client: TestClient) -> None:
    sha = _upload(client, _png(6))
    response = client.get(f"/api/ingest/{sha}/pointing")
    assert response.status_code == 404
    assert response.json() == {"detail": ingest_router.NO_POINTING_RECORD}


# ---- Refused bodies ----


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"\xff\xfe{}",
        b"\xef\xbb\xbf{}",
        b'{"latDeg": NaN}',
        b"Infinity",
        b"{} {}",
        b'{"a": "\xc3"}',
    ],
    ids=["empty", "words", "utf-16 bom", "utf-8 bom", "nan", "infinity", "two values", "broken utf-8"],
)
def test_a_body_that_is_not_json_text_is_refused_422(client: TestClient, store: IngestStore, body: bytes) -> None:
    sha = _upload(client, _png(7))
    response = _put(client, sha, body)
    assert response.status_code == 422
    assert _refusal(response) == {"detail": ROUTE["notJson"]}
    assert not _pointing_file(store, sha).exists()


# JSON text no record can be, which Python's reader cannot take whole: a whole number past its 4,300-digit limit
# (`ValueError`), and arrays nested deeper than its recursion limit on Python 3.12 (`RecursionError`); on 3.14 the
# deep arrays are read and refused as no record. Each is inside the route's 64 KiB body limit
LONG_NUMBER = "9" * 4301
DEEP_ARRAYS = "[" * 30000 + "]" * 30000


@pytest.mark.parametrize(
    "text",
    [
        '{"x": ' + LONG_NUMBER + "}",
        '{"x": -' + LONG_NUMBER + "}",
        DEEP_ARRAYS,
        '{"x": ' + DEEP_ARRAYS + "}",
    ],
    ids=["a long number", "a long negative number", "deep arrays", "deep arrays inside an object"],
)
def test_json_text_python_cannot_read_whole_is_refused_as_no_record(client: TestClient, store: IngestStore, text: str) -> None:
    sha = _upload(client, _png(20))
    response = _put(client, sha, text.encode("ascii"))
    assert response.status_code == 422
    # Where the reader takes the text, the record's own rules refuse it and may list issues; never a 500
    assert _refusal(response)["detail"] == NOT_A_RECORD
    assert not _pointing_file(store, sha).exists()


def test_a_record_field_holding_a_number_python_cannot_read_is_refused_as_no_record(client: TestClient, store: IngestStore) -> None:
    sha = _upload(client, _png(21))
    text = _body(_record(sha)).decode("ascii").replace('"formatVersion": 1', '"formatVersion": ' + LONG_NUMBER, 1)
    assert LONG_NUMBER in text
    response = _put(client, sha, text.encode("ascii"))
    assert response.status_code == 422
    assert _refusal(response) == {"detail": NOT_A_RECORD}
    assert not _pointing_file(store, sha).exists()


@pytest.mark.parametrize("raised", [RecursionError, ValueError], ids=["recursion", "value"])
def test_any_reader_failure_on_json_text_is_a_refusal_never_a_500(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch, raised: type[Exception]
) -> None:
    sha = _upload(client, _png(22))
    body = _body(_record(sha))
    reads = json.loads

    def fails_on_the_body(text: Any, *args: Any, **kwargs: Any) -> Any:
        # Only the route's read of this body fails; the store's own reads of its files go on as they are
        if text == body.decode("utf-8"):
            raise raised("the reader stopped")
        return reads(text, *args, **kwargs)

    monkeypatch.setattr(ingest_router.json, "loads", fails_on_the_body)
    response = _put(client, sha, body)
    assert response.status_code == 422
    assert _refusal(response) == {"detail": NOT_A_RECORD}
    assert "stopped" not in response.text
    assert not _pointing_file(store, sha).exists()


def _changed(sha: str, path: list[Any], value: Any, sample: int = 0) -> dict[str, Any]:
    record = _record(sha, sample)
    target: Any = record
    for key in path[:-1]:
        target = target[key]
    if value is _DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return record


_DROP = object()
NO_FIX = next(n for n, s in enumerate(SAMPLES) if s["record"]["location"] == {"state": "none", "reason": "no_fix"})


@pytest.mark.parametrize(
    ("name", "build", "issues"),
    [
        ("not an object", lambda sha: [], None),
        ("a latitude outside its range", lambda sha: _changed(sha, ["location", "latDeg"], 95.0),
         [{"path": ["location", "latDeg"], "message": NOT_A_RECORD}]),
        ("a quaternion that is not a unit one", lambda sha: _changed(sha, ["heading", "attitude", "quaternion", "w"], 0.9),
         [{"path": ["heading", "attitude", "quaternion"], "message": POINTING_RULES["unitQuaternion"]}]),
        ("a heading with no place", lambda sha: {**_record(sha), "location": _record(sha, NO_FIX)["location"]},
         [{"path": ["heading"], "message": POINTING_RULES["headingNeedsPlace"]}]),
        ("gravity in metres per second squared", lambda sha: _changed(sha, ["motion", "gravityDeviceG"], {"x": 0.0, "y": -5.886, "z": -7.848}),
         [{"path": ["motion", "gravityDeviceG"], "message": POINTING_RULES["gravityNearOneG"]}]),
        ("a heading accuracy outside the limit", lambda sha: _changed(sha, ["heading", "headingAccuracyDeg"], 31.0),
         [{"path": ["heading", "headingAccuracyDeg"], "message": POINTING_RULES["headingInsideLimit"]}]),
        ("a true heading of a full turn", lambda sha: _changed(sha, ["heading", "trueHeadingDeg"], 360.0),
         [{"path": ["heading", "trueHeadingDeg"], "message": NOT_A_RECORD}]),
        ("the placeholder hash", lambda sha: _changed(sha, ["sourceAssetSha256"], "0" * 64),
         [{"path": ["sourceAssetSha256"], "message": POINTING_RULES["placeholderSha256"]}]),
        ("a key the record made up", lambda sha: {**_record(sha), "zqxSecret": 89.5}, None),
        ("two keys the record made up, inside", lambda sha: _record(sha) | {"camera": {**_record(sha)["camera"], "zqxSecret": 89.5, "zqxOther": 89.5}},
         [{"path": ["camera"], "message": NOT_A_RECORD}]),
        ("a Python field name", lambda sha: {**_changed(sha, ["sourceAssetSha256"], _DROP), "source_asset_sha256": sha},
         [{"path": ["sourceAssetSha256"], "message": NOT_A_RECORD}]),
        ("an unknown heading state", lambda sha: _changed(sha, ["heading", "state"], "zqxSecret"),
         [{"path": ["heading"], "message": NOT_A_RECORD}]),
        ("a null where a value may only be absent", lambda sha: _changed(sha, ["location", "altitude"], None),
         [{"path": ["location"], "message": NOT_A_RECORD}]),
    ],
)
def test_a_json_body_that_is_not_a_record_is_refused_422_by_its_paths_and_the_closed_sentences(
    client: TestClient, store: IngestStore, name: str, build: Any, issues: Any
) -> None:
    sha = _upload(client, _png(8))
    response = _put(client, sha, _body(build(sha)))
    assert response.status_code == 422, name
    refusal = _refusal(response)
    assert refusal["detail"] == NOT_A_RECORD
    if issues is None:
        assert all("zqxSecret" not in issue["path"] for issue in refusal.get("issues", []))
    else:
        assert refusal["issues"] == issues
    assert "zqxSecret" not in response.text and "89.5" not in response.text
    assert not _pointing_file(store, sha).exists()


def _every_value_of_the_wrong_kind(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _every_value_of_the_wrong_kind(v, k) for k, v in value.items()}
    return value if key == "state" else []


def test_a_record_with_more_failures_than_the_contract_lists_names_its_first_issues_only(
    client: TestClient, store: IngestStore
) -> None:
    sha = _upload(client, _png(15))
    response = _put(client, sha, _body(_every_value_of_the_wrong_kind(_record(sha))))
    assert response.status_code == 422
    refusal = _refusal(response)
    assert refusal["detail"] == NOT_A_RECORD
    assert len(refusal["issues"]) == 32
    assert len({json.dumps(issue) for issue in refusal["issues"]}) == 32
    assert not _pointing_file(store, sha).exists()


@pytest.mark.parametrize("missing", ["record.json", "the original"])
def test_a_photo_with_its_receipt_or_its_bytes_missing_is_no_stored_photo(
    client: TestClient, store: IngestStore, missing: str
) -> None:
    sha = _upload(client, _png(16))
    target = store.folder(sha) / "record.json" if missing == "record.json" else store.original(sha)
    assert target is not None
    target.unlink()
    for response in (_put(client, sha, _body(_record(sha))), client.get(f"/api/ingest/{sha}/pointing")):
        assert response.status_code == 404
        assert _refusal(response) == NO_SUCH_PHOTO
    assert not _pointing_file(store, sha).exists()


def test_a_folder_at_the_records_place_is_never_served_or_written_over(store: IngestStore) -> None:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app, raise_server_exceptions=False)
        sha = _upload(client, _png(17))
        _pointing_file(store, sha).mkdir()
        got = client.get(f"/api/ingest/{sha}/pointing")
        assert got.status_code == 409
        assert got.json() == {"detail": ingest_router.UNREADABLE_POINTING}
        assert _put(client, sha, _body(_record(sha))).status_code == 500
        assert _pointing_file(store, sha).is_dir() and not any(_pointing_file(store, sha).iterdir())
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def test_a_record_of_another_photo_is_refused_422_and_kept_for_neither(client: TestClient, store: IngestStore) -> None:
    one, other = _upload(client, _png(9)), _upload(client, _png(10))
    response = _put(client, one, _body(_record(other)))
    assert response.status_code == 422
    assert _refusal(response) == {
        "detail": ROUTE["anotherPhoto"],
        "issues": [{"path": ["sourceAssetSha256"], "message": ROUTE["anotherPhoto"]}],
    }
    assert not _pointing_file(store, one).exists() and not _pointing_file(store, other).exists()


# ---- A stored record that cannot be served ----


@pytest.mark.parametrize("damage", ["not json", "not a record", "another photo", "a link", "a number past the reader's limit"])
def test_a_stored_record_that_fails_the_contract_is_never_served(
    client: TestClient, store: IngestStore, tmp_path: Path, damage: str
) -> None:
    sha = _upload(client, _png(11))
    path = _pointing_file(store, sha)
    if damage == "a link":
        outside = tmp_path / "outside.json"
        outside.write_bytes(_body(_record(sha)))
        os.symlink(outside, path)
    else:
        text = {
            "not json": b"{",
            "not a record": _body(_changed(sha, ["location", "latDeg"], 95.0)),
            "another photo": _body(_record(hashlib.sha256(b"another").hexdigest())),
            "a number past the reader's limit": ('{"x": ' + "9" * 4301 + "}").encode("ascii"),
        }[damage]
        path.write_bytes(text)
    response = client.get(f"/api/ingest/{sha}/pointing")
    assert response.status_code == 409
    assert response.json() == {"detail": ingest_router.UNREADABLE_POINTING}


# ---- After a solve ----


def test_after_a_solve_the_same_record_is_still_a_receipt_and_a_different_one_is_refused(client: TestClient) -> None:
    sha = _upload(client, _png(12, 48, 32))
    body = _body(_record(sha))
    assert _put(client, sha, body).status_code == 201
    assert client.post(f"/api/ingest/{sha}/solve").status_code in (200, 202)
    again = _put(client, sha, body)
    assert again.status_code == 200 and again.json()["alreadyStored"] is True
    other = _put(client, sha, _body(_record(sha, 5)))
    assert other.status_code == 409
    assert _refusal(other) == {"detail": ROUTE["differentRecordHeld"]}


def _job_record(sha: str, kind: str) -> dict[str, Any]:
    """The job contract's first valid queued or running record of `kind`, made this upload's."""
    base = next(r for r in build_job_samples()["valid"] if r["kind"] == kind and r["state"] in ("queued", "running"))
    old = base["sourceAssetSha256"]
    made = json.loads(json.dumps(base).replace(old, sha).replace(old[:16], sha[:16]))
    made["id"] = f"job_{sha[:16]}{1:016x}"
    return made


@pytest.mark.parametrize("job", ["a solve run", "a solve job in the job store", "a solve record that cannot be read"])
def test_a_first_record_sent_once_a_solve_job_exists_is_refused_409(client: TestClient, store: IngestStore, job: str) -> None:
    sha = _upload(client, _png(18, 48, 32))
    if job == "a solve run":
        assert client.post(f"/api/ingest/{sha}/solve").status_code in (200, 202)
    elif job == "a solve job in the job store":
        JobStore(store).write_record(_job_record(sha, "solve"))
    else:
        (store.folder(sha) / "job.json").write_text("{", encoding="utf-8")
    response = _put(client, sha, _body(_record(sha)))
    assert response.status_code == 409
    assert _refusal(response) == {"detail": ROUTE["solveStarted"]}
    assert not _pointing_file(store, sha).exists()


def test_a_media_job_alone_does_not_stop_a_record(client: TestClient, store: IngestStore) -> None:
    sha = _upload(client, _png(19))
    JobStore(store).write_record(_job_record(sha, "display_copy"))
    assert _put(client, sha, _body(_record(sha))).status_code == 201


# ---- No record value in any log line or refusal (REQ-IOS-6.1) ----

MARKED = {
    ("location", "latDeg"): 31.4159265,
    ("location", "lonDeg"): -41.2718281,
    ("location", "horizontalAccuracyM"): 6.0221407,
    ("heading", "trueHeadingDeg"): 211.7734,
    ("device", "hardwareModel"): "Synthetic9,7",
    ("capturedAtDeviceClockUTC",): "2030-01-01T02:03:04.567Z",
}


def _marked(sha: str) -> dict[str, Any]:
    record = _record(sha)
    for path, value in MARKED.items():
        target = record
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return record


def _texts_of(value: Any) -> list[str]:
    return [json.dumps(value), str(value)] if not isinstance(value, str) else [value]


def test_no_value_of_a_record_reaches_a_log_line_or_a_refusal(
    client: TestClient, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    sha, other = _upload(client, _png(13)), _upload(client, _png(14))
    marked = _marked(sha)
    caplog.set_level(logging.DEBUG)
    replies = [
        _put(client, other, _body(marked)),  # another photo's record
        _put(client, sha, _body({**marked, "zqxSecret": 1})),  # not a record
        _put(client, sha, _body(_changed(sha, ["heading", "headingAccuracyDeg"], 31.0) | {"location": marked["location"]})),
        _put(client, sha, _body(marked)[:-1]),  # not JSON
        _put(client, hashlib.sha256(b"none").hexdigest(), _body(marked)),  # no such photo
    ]
    assert _put(client, sha, _body(marked)).status_code == 201
    replies.append(_put(client, sha, _body({**marked, "camera": {**marked["camera"], "videoZoomFactor": 2.0}})))
    replies.append(_put(client, sha, _body(marked)))  # the same again
    assert [reply.status_code for reply in replies] == [422, 422, 422, 422, 404, 409, 200]
    captured = capfd.readouterr()
    seen = caplog.text + captured.out + captured.err + "".join(reply.text for reply in replies)
    for value in MARKED.values():
        for text in _texts_of(value):
            assert text not in seen, text


# ---- P3: the photo is unchanged, with a record and without ----


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(seed=st.integers(0, 2**32 - 1), with_record=st.booleans(), sample=st.integers(0, len(SAMPLES) - 1))
def test_p3_the_stored_photo_and_its_hash_equal_the_upload_with_a_record_and_without(
    tmp_path_factory: pytest.TempPathFactory, seed: int, with_record: bool, sample: int
) -> None:
    store = IngestStore(tmp_path_factory.mktemp("p3") / "store", max_bytes=10_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app)
        photo = _png(seed)
        sha = _upload(client, photo)
        receipt_before = client.get(f"/api/ingest/{sha}").json()
        record_json_before = (store.folder(sha) / "record.json").read_bytes()
        if with_record:
            assert _put(client, sha, _body(_record(sha, sample))).status_code == 201
        original = client.get(f"/api/ingest/{sha}/original")
        assert original.content == photo
        assert hashlib.sha256(original.content).hexdigest() == sha
        assert store.original(sha) is not None and store.original(sha).read_bytes() == photo
        assert client.get(f"/api/ingest/{sha}").json() == receipt_before
        assert (store.folder(sha) / "record.json").read_bytes() == record_json_before
        assert _pointing_file(store, sha).exists() is with_record
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
