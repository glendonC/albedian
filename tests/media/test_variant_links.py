"""The variant routes never go through a link and never trust a damaged record (`services/media/router.py`; AUDIT's
backend-security-b77 item L1). Defense in depth: every case here needs someone who can already write the store's 0700
folders.

Covers, through the API on a store of its own holding one display copy:
- A record or the file of a variant reached through a link, or a FIFO or a folder put in its place, is never read or
  served: the record, file, export and manifest routes answer 404 at once (a FIFO never holds a request open), and
  the listing leaves that variant out when its own record is the one.
- The upload's bucket, its folder, its `variants` folder, a variant's folder or its `attempts` folder reached through
  a link is never entered: those routes answer 404 and the listing lists nothing found through it (through a linked
  bucket or upload folder the store keeps no upload at all, so the listing answers 404 too). An attempt record
  reached through a link, a FIFO or a folder is never listed.
- A record that is no JSON object (cut short, not UTF-8, a list, null, nothing) is 500 with the route's fixed sentence
  on every route that reads it, and is left out of the listing, which still lists the rest; so is a listed record
  without its UTC instant.
- A variant record naming another id than its folder, or an attempt record another id than its file, is never served
  or listed. A variant record naming no type the routes serve (missing, unknown, or no string) is 500 with the route's
  fixed sentence on the file, export and manifest routes, never an unhandled error.
- The file route sends the file it opened and checked, whole or by ranges, and never opens its path again.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import anyio
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from services.api import create_app
from services.ingest import router as ingest_router
from services.ingest.job_store import JobStore
from services.ingest.store import IngestStore
from services.media import jobs as media_jobs

# The display copy starts a child process: each test has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

POLL_S = 0.1
JOB_BOUND_S = 240.0
NO_VARIANT = "no variant with that id is stored for that file"
RECORD_UNREADABLE = "a stored record of that variant cannot be read"
RECORDS = ("variant.json", "graph.json", "attempt.json")
# What each route reads from the variant's folder
READS = {
    "": RECORDS,
    "/file": ("variant.json", "variant.png"),
    "/export": (*RECORDS, "variant.png"),
    "/manifest": RECORDS,
}
# A request still held open past this has met a FIFO it waited on: the test releases it and fails [proposed: far past
# the milliseconds a refusal takes, short enough to keep a failing run quick]
FIFO_BOUND_S = 20.0
FIFO_HOLD_S = 1.0


def finished(client: TestClient, job: dict) -> dict:
    deadline = time.monotonic() + JOB_BOUND_S
    while job["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the job did not end within the test's bound"
        time.sleep(POLL_S)
        answer = client.get(f"/api/ingest/jobs/{job['id']}")
        assert answer.status_code == 200, answer.text
        job = answer.json()
    return job


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    """One store with one display copy: the client, the upload's hash, its folder, the job and a folder outside it."""
    root = tmp_path_factory.mktemp("links")
    uploads = IngestStore(root / "store", max_bytes=50_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    pixels = np.random.default_rng(5).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, format="PNG")
    with TestClient(app) as client:
        answer = client.post("/api/ingest", content=buf.getvalue(), headers={"content-type": "image/png"})
        assert answer.status_code in (200, 201), answer.text
        sha = answer.json()["sourceAsset"]["sha256"]
        job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"}).json())
        assert job["state"] == "succeeded", job
        client.store_root = root / "store"
        outside = root / "outside"
        outside.mkdir()
        yield client, sha, root / "store" / sha[:2] / sha, job, outside


NO_UPLOAD = "no file with that hash is stored"


def no_upload_listed(client: TestClient, sha: str) -> bool:
    """A linked bucket or upload folder is no upload the store keeps (it never follows the link to the hash folder),
    so the listing answers 404 with the store's own sentence."""
    answer = client.get(f"/api/media/{sha}/variants")
    return (answer.status_code, answer.json()) == (404, {"detail": NO_UPLOAD})


def listed(client: TestClient, sha: str) -> tuple[list[str], list[str]]:
    answer = client.get(f"/api/media/{sha}/variants")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return [v["id"] for v in body["variants"]], [a["id"] for a in body["attempts"]]


def entry_mode(path: Path) -> int:
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return 0


def remove_entry(path: Path) -> None:
    if stat.S_ISDIR(entry_mode(path)):
        shutil.rmtree(path)
    elif entry_mode(path):
        path.unlink()


@contextmanager
def writable(folder: Path):
    mode = stat.S_IMODE(os.lstat(folder).st_mode)
    os.chmod(folder, 0o700)
    try:
        yield
    finally:
        os.chmod(folder, mode)


@contextmanager
def replaced(path: Path, kind: str, outside: Path, data: bytes = b""):
    """For the block, `path` set aside and in its place: a link to a copy of it outside the store (`link`), a FIFO
    (`fifo`), an empty folder (`folder`) or a file of `data` (`bytes`); the original put back after it."""
    kept = path.with_name(path.name + ".kept")
    with writable(path.parent):
        os.rename(path, kept)
        try:
            if kind == "link":
                copy = outside / f"{path.parent.name}-{path.name}"
                remove_entry(copy)
                if stat.S_ISDIR(entry_mode(kept)):
                    shutil.copytree(kept, copy, symlinks=True)
                else:
                    shutil.copyfile(kept, copy)
                os.symlink(copy, path)
            elif kind == "fifo":
                os.mkfifo(path, 0o600)
            elif kind == "folder":
                path.mkdir(0o700)
            else:
                path.write_bytes(data)
            yield
        finally:
            remove_entry(path)
            os.rename(kept, path)


@contextmanager
def planted(path: Path, kind: str, outside: Path, data: bytes = b""):
    """For the block, a new entry at `path`: as `replaced` puts one, from `data` for a link's target."""
    with writable(path.parent):
        try:
            if kind == "link":
                copy = outside / f"planted-{path.name}"
                copy.write_bytes(data)
                os.symlink(copy, path)
            elif kind == "fifo":
                os.mkfifo(path, 0o600)
            elif kind == "folder":
                path.mkdir(0o700)
            else:
                path.write_bytes(data)
            yield
        finally:
            remove_entry(path)


@contextmanager
def released_after_bound(path: Path):
    """Fails the test, rather than hanging it, if the block still runs when the bound passes: a request is then held
    open by the FIFO at `path`, and a writer opened for a moment releases the reader the route left waiting."""
    fired = threading.Event()

    def release() -> None:
        fired.set()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            # ENXIO: nothing has the FIFO open to read, so nothing waits on it
            return
        try:
            # Held long enough for the waiting reader to wake and see a writer: one closed at once can be gone again
            # before it looks (macOS checks for a writer again once it wakes)
            time.sleep(FIFO_HOLD_S)
        finally:
            os.close(fd)

    timer = threading.Timer(FIFO_BOUND_S, release)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
    assert not fired.is_set(), f"a request waited on the FIFO {path.name} past the bound"


def held_bound(path: Path, kind: str):
    return released_after_bound(path) if kind == "fifo" else _nothing()


@contextmanager
def _nothing():
    yield


@pytest.mark.parametrize("kind", ["link", "fifo", "folder"])
@pytest.mark.parametrize("name", [*RECORDS, "variant.png"])
def test_a_record_or_file_reached_through_a_link_or_no_regular_file_is_never_read_or_served(stored, name, kind) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    path = folder / "variants" / vid / name
    with replaced(path, kind, outside), held_bound(path, kind):
        for route, reads in READS.items():
            if name not in reads:
                continue
            answer = client.get(f"/api/media/{sha}/variants/{vid}{route}")
            assert answer.status_code == 404, (route, answer.status_code)
            assert answer.json()["detail"] == NO_VARIANT, route
        variants_listed, attempts_listed = listed(client, sha)
        assert variants_listed == ([] if name == "variant.json" else [vid])
        assert attempts_listed == [job["attemptId"]]
    for route in READS:
        assert client.get(f"/api/media/{sha}/variants/{vid}{route}").status_code == 200, route
    assert listed(client, sha) == ([vid], [job["attemptId"]])


@pytest.mark.parametrize("where", ["bucket", "upload", "variants", "variant"])
def test_a_folder_on_the_way_to_a_variant_reached_through_a_link_is_never_entered(stored, where) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    path = {"bucket": folder.parent, "upload": folder, "variants": folder / "variants",
            "variant": folder / "variants" / vid}[where]
    with replaced(path, "link", outside):
        for route in READS:
            answer = client.get(f"/api/media/{sha}/variants/{vid}{route}")
            assert answer.status_code == 404, (route, answer.status_code)
        if where in ("bucket", "upload"):
            assert no_upload_listed(client, sha)
        else:
            assert listed(client, sha) == ([], [job["attemptId"]])
    assert listed(client, sha) == ([vid], [job["attemptId"]])


@pytest.mark.parametrize("where", ["bucket", "upload", "attempts"])
def test_an_attempts_folder_reached_through_a_link_is_never_listed(stored, where) -> None:
    client, sha, folder, job, outside = stored
    path = {"bucket": folder.parent, "upload": folder, "attempts": folder / "attempts"}[where]
    with replaced(path, "link", outside):
        if where in ("bucket", "upload"):
            assert no_upload_listed(client, sha)
        else:
            assert listed(client, sha) == ([job["variantId"]], [])
    assert listed(client, sha) == ([job["variantId"]], [job["attemptId"]])


@pytest.mark.parametrize("kind", ["link", "fifo", "folder"])
def test_an_attempt_record_reached_through_a_link_or_no_regular_file_is_never_listed(stored, kind) -> None:
    client, sha, folder, job, outside = stored
    real = folder / "attempts" / f"{job['attemptId']}.json"
    other = "att_" + "7" * 16
    record = {**json.loads(real.read_text()), "id": other}
    path = folder / "attempts" / f"{other}.json"
    with planted(path, kind, outside, json.dumps(record).encode("utf-8")), held_bound(path, kind):
        assert listed(client, sha) == ([job["variantId"]], [job["attemptId"]])
    # The same record as a regular file of its own is listed (the same instant, so by id): the cases above differ in
    # one thing
    with planted(path, "bytes", outside, json.dumps(record).encode("utf-8")):
        assert listed(client, sha)[1] == sorted([job["attemptId"], other])


# Not a JSON object in UTF-8: cut short, UTF-16 with its mark, a list, null, nothing, and nested past the parser's
# recursion limit
NOT_A_RECORD = [b'{"id": ', b"\xff\xfe{\x00}\x00", b"[]", b"null", b"", b"[" * 200_000]
NOT_A_RECORD_IDS = ["cut", "utf16", "list", "null", "empty", "deep"]


@pytest.mark.parametrize("data", NOT_A_RECORD, ids=NOT_A_RECORD_IDS)
@pytest.mark.parametrize("name", RECORDS)
def test_a_record_that_is_no_json_object_is_500_and_left_out_of_the_listing(stored, name, data) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    path = folder / "variants" / vid / name
    with replaced(path, "bytes", outside, data):
        for route, reads in READS.items():
            if name not in reads:
                continue
            answer = client.get(f"/api/media/{sha}/variants/{vid}{route}")
            assert answer.status_code == 500, (route, answer.status_code)
            assert answer.headers["content-type"] == "application/json", route
            assert answer.json()["detail"] == RECORD_UNREADABLE, route
        assert listed(client, sha) == ([] if name == "variant.json" else [vid], [job["attemptId"]])
    assert listed(client, sha) == ([vid], [job["attemptId"]])


@pytest.mark.parametrize("data", NOT_A_RECORD, ids=NOT_A_RECORD_IDS)
def test_an_attempt_record_that_is_no_json_object_is_left_out_while_the_rest_are_listed(stored, data) -> None:
    client, sha, folder, job, outside = stored
    with planted(folder / "attempts" / ("att_" + "8" * 16 + ".json"), "bytes", outside, data):
        assert listed(client, sha) == ([job["variantId"]], [job["attemptId"]])


def mark_succeeded(client: TestClient, sha: str, vid: str, idx: int) -> Path:
    """A succeeded job record naming `vid`, so only the variant's own folder and records decide what is served."""
    jobs = JobStore(IngestStore(client.store_root, max_bytes=50_000_000))
    suffix = vid.removeprefix("var_")
    record = jobs.write_record({
        "id": f"job_{sha[:16]}{700 + idx:016x}",
        "sourceAssetSha256": sha,
        "kind": "display_copy",
        "state": "succeeded",
        "createdAtUTC": "2026-11-24T04:00:00Z",
        "startedAtUTC": "2026-11-24T04:00:00Z",
        "finishedAtUTC": "2026-11-24T04:00:01Z",
        "producer": {"name": "albedian.restore", "revision": media_jobs.MEDIA_PRODUCER.revision},
        "request": {"kind": "display_copy"},
        "graphId": f"graph_{suffix}",
        "attemptId": f"att_{suffix}",
        "variantId": vid,
        "stages": [],
    })
    path = jobs.record_path(record["id"])
    assert path is not None
    return path


@contextmanager
def copied_variant(client: TestClient, sha: str, folder: Path, vid: str, copy_id: str, idx: int, variant: dict):
    """A copy of the variant's folder at `copy_id`, its variant record `variant`, named by a succeeded job."""
    target = folder / "variants" / copy_id
    job_path = None
    with writable(folder / "variants"):
        try:
            shutil.copytree(folder / "variants" / vid, target)
            os.chmod(target, 0o700)
            os.chmod(target / "variant.json", 0o600)
            (target / "variant.json").write_text(json.dumps(variant))
            job_path = mark_succeeded(client, sha, copy_id, idx)
            yield
        finally:
            if job_path is not None:
                os.chmod(job_path, 0o600)
                job_path.unlink(missing_ok=True)
            shutil.rmtree(target, ignore_errors=True)


def test_a_variant_record_naming_another_id_than_its_folder_is_never_served_or_listed(stored) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    record = json.loads((folder / "variants" / vid / "variant.json").read_text())
    copy_id = "var_" + "9" * 16
    with copied_variant(client, sha, folder, vid, copy_id, 1, record):
        for route in READS:
            answer = client.get(f"/api/media/{sha}/variants/{copy_id}{route}")
            assert answer.status_code == 404, (route, answer.status_code)
        assert listed(client, sha)[0] == [vid]
    # The same copy naming its own id is served and listed: the case above differs in one thing
    with copied_variant(client, sha, folder, vid, copy_id, 1, {**record, "id": copy_id}):
        for route in ("", "/file"):
            assert client.get(f"/api/media/{sha}/variants/{copy_id}{route}").status_code == 200, route
        assert sorted(listed(client, sha)[0]) == sorted([vid, copy_id])


def test_an_attempt_record_naming_another_id_than_its_file_is_never_listed(stored) -> None:
    client, sha, folder, job, outside = stored
    real = (folder / "attempts" / f"{job['attemptId']}.json").read_bytes()
    with planted(folder / "attempts" / ("att_" + "a" * 16 + ".json"), "bytes", outside, real):
        assert listed(client, sha) == ([job["variantId"]], [job["attemptId"]])


@pytest.mark.parametrize("instant", ["missing", 5, "not a time"])
def test_a_listed_record_without_its_utc_instant_is_left_out_while_the_rest_are_listed(stored, instant) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    record = json.loads((folder / "variants" / vid / "variant.json").read_text())
    attempt = json.loads((folder / "attempts" / f"{job['attemptId']}.json").read_text())
    copy_id, attempt_id = "var_" + "b" * 16, "att_" + "b" * 16
    variant = {**record, "id": copy_id}
    attempt = {**attempt, "id": attempt_id}
    if instant == "missing":
        del variant["createdAtUTC"], attempt["finishedAtUTC"]
    else:
        variant["createdAtUTC"] = attempt["finishedAtUTC"] = instant
    with copied_variant(client, sha, folder, vid, copy_id, 2, variant), planted(
            folder / "attempts" / f"{attempt_id}.json", "bytes", outside, json.dumps(attempt).encode("utf-8")):
        assert listed(client, sha) == ([vid], [job["attemptId"]])


def test_the_file_route_sends_the_file_it_opened_whole_and_by_ranges_and_never_opens_its_path_again(
        stored, monkeypatch) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    stored_bytes = (folder / "variants" / vid / "variant.png").read_bytes()

    def opened_again(*args, **kwargs):
        raise AssertionError("the response opened the file's path again")

    monkeypatch.setattr(anyio, "open_file", opened_again)
    url = f"/api/media/{sha}/variants/{vid}/file"
    whole = client.get(url)
    assert whole.status_code == 200
    assert whole.content == stored_bytes
    assert hashlib.sha256(whole.content).hexdigest() == job_variant_sha(client, sha, vid)
    assert whole.headers["content-length"] == str(len(stored_bytes))
    assert whole.headers["accept-ranges"] == "bytes" and whole.headers["etag"] and whole.headers["last-modified"]
    assert whole.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert whole.headers["x-content-type-options"] == "nosniff"
    assert whole.headers["content-disposition"] == "inline"
    assert whole.headers["content-type"] == "image/png"
    part = client.get(url, headers={"range": "bytes=10-99"})
    assert part.status_code == 206 and part.content == stored_bytes[10:100]
    assert part.headers["content-range"] == f"bytes 10-99/{len(stored_bytes)}"
    tail = client.get(url, headers={"range": f"bytes={len(stored_bytes) - 7}-"})
    assert tail.status_code == 206 and tail.content == stored_bytes[-7:]
    parts = client.get(url, headers={"range": "bytes=0-3,20-29"})
    assert parts.status_code == 206 and parts.headers["content-type"].startswith("multipart/byteranges")
    assert stored_bytes[0:4] in parts.content and stored_bytes[20:30] in parts.content
    # And again after ranges: each answer reads its own descriptor from its start
    assert client.get(url).content == stored_bytes


def job_variant_sha(client: TestClient, sha: str, vid: str) -> str:
    return client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]["sha256"]


def test_the_file_answer_sends_its_descriptor_even_offered_pathsend_and_closes_it(tmp_path) -> None:
    from services.media import router as media_router

    data = bytes(range(256)) * 300
    path = tmp_path / "variant.png"
    path.write_bytes(data)
    handle = open(path, "rb")
    # The path is gone once it is open: only the descriptor can answer, its stat included
    path.unlink()
    answer = media_router._OpenedFileResponse(path, handle, media_type="image/png")
    sent: list[dict] = []

    async def receive() -> dict:
        await anyio.sleep_forever()

    async def send(message: dict) -> None:
        sent.append(message)

    # A server offering the ASGI pathsend extension would be handed the path to send itself
    scope = {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"},
             "extensions": {"http.response.pathsend": {}}}
    anyio.run(answer, scope, receive, send)
    assert [m["type"] for m in sent][0] == "http.response.start"
    assert all(m["type"] in ("http.response.start", "http.response.body") for m in sent), [m["type"] for m in sent]
    assert b"".join(m.get("body", b"") for m in sent[1:]) == data
    assert dict(sent[0]["headers"])[b"content-length"] == str(len(data)).encode()
    assert handle.closed
    # An answer never sent still closes its descriptor when called for a HEAD
    other = tmp_path / "other.png"
    other.write_bytes(data)
    second = open(other, "rb")
    sent.clear()
    anyio.run(media_router._OpenedFileResponse(other, second, media_type="image/png"),
              {**scope, "method": "HEAD"}, receive, send)
    assert b"".join(m.get("body", b"") for m in sent[1:]) == b"" and second.closed


RECORDS_REFUSED = "the stored records do not make a manifest the contract takes"


@pytest.mark.parametrize("mime", ["missing", "image/heic", 5, ["image/png"], {"type": "image/png"}],
                         ids=["missing", "unknown", "number", "list", "object"])
def test_a_variant_record_naming_no_type_the_routes_serve_is_500_with_their_sentence(stored, mime) -> None:
    client, sha, folder, job, outside = stored
    vid = job["variantId"]
    path = folder / "variants" / vid / "variant.json"
    record = json.loads(path.read_text())
    if mime == "missing":
        del record["mimeType"]
    else:
        record["mimeType"] = mime
    with replaced(path, "bytes", outside, json.dumps(record).encode("utf-8")):
        for route, sentence in (("/file", RECORD_UNREADABLE), ("/export", RECORDS_REFUSED),
                                ("/manifest", RECORDS_REFUSED)):
            answer = client.get(f"/api/media/{sha}/variants/{vid}{route}")
            assert answer.status_code == 500, (route, answer.status_code)
            assert answer.headers["content-type"] == "application/json", route
            assert answer.json()["detail"] == sentence, route
        # The record route answers the records as they are stored
        assert client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"] == record
    assert client.get(f"/api/media/{sha}/variants/{vid}/file").status_code == 200


def test_the_routes_type_tables_name_the_types_the_contract_serves() -> None:
    from services.contracts.media import VARIANT_MIME_TYPES
    from services.media import variants

    # The file route takes a type with a file name and the export one with an extension; while both tables name the
    # contract's types, the export's own check refuses what the manifest's contract check already refuses, and a type
    # added to the contract alone fails here before a route meets it
    assert set(variants.VARIANT_FILES) == set(variants.EXPORT_EXTENSIONS) == set(VARIANT_MIME_TYPES)
