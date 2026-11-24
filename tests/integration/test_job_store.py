"""The job store (upload jobs design 3.1, platform-4; REQ-JOB-3, REQ-JOB-5; P-JOB-3, P-JOB-6).

Every upload here is made up: a hash of one byte, an "original" of a few bytes, records built from the job contract's
own valid samples (`build_job_samples`) with the upload's hash and an id of the store's form put in. Every wait has
its own bound and fails by an assertion: a child process is given `CHILD_BOUND_S` to take a lock, to finish its writes
and to end; one that is still alive then is killed after the assertion has failed.
"""

from __future__ import annotations

import time
import copy
import dataclasses
from datetime import datetime, timezone
import hashlib
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable, Iterator, Optional

from fastapi.testclient import TestClient
from hypothesis import example, given, settings
from hypothesis import strategies as st
import pytest

from services.api import create_app
from services.contracts.jobs import INTERRUPTED_SENTENCE, UploadJob, build_job_samples
from services.ingest import job_store
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.job_store import Ended, JobStore, SweepCounts, Unreadable, new_job_id
from services.ingest.runner import JobRefusal, KindResult
from services.ingest.store import IngestStore

CHILD_BOUND_S = 60.0
NOW = datetime(2030, 1, 1, 5, 0, tzinfo=timezone.utc)
SHAS = [hashlib.sha256(bytes([n])).hexdigest() for n in range(3)]
VALID = build_job_samples()["valid"]
STATES = sorted({(r["kind"], r["state"]) for r in VALID})
SOLUTIONS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json").read_text(encoding="utf-8")
)
A_SOLUTION = SOLUTIONS["valid"]["calibrated"]
SOLUTION_SENTINEL = "unserved_solution_marker_9f3a"


def _solution_for(sha: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """A contract solution belonging to upload `sha`: its `sourceSha256` and `sourceAssetId` set to `sha`'s."""
    source = A_SOLUTION if base is None else base
    old_sha = source.get("sourceSha256") or A_SOLUTION["sourceSha256"]
    old_asset = source.get("sourceAssetId") or A_SOLUTION["sourceAssetId"]
    out = _swap(copy.deepcopy(source), old_sha, sha)
    out = _swap(out, old_asset, f"src_{sha[:16]}")
    out["sourceSha256"] = sha
    out["sourceAssetId"] = f"src_{sha[:16]}"
    return out


def _refused_sample(name: str) -> dict[str, Any]:
    """A named solution of the solution contract's fixture that the contract refuses: its base with its steps."""
    case = next(c for c in SOLUTIONS["invalid"] if c["name"] == name)
    out = copy.deepcopy(SOLUTIONS["valid"][case["base"]])
    for step in case["patch"]:
        target = out
        for key in step["path"][:-1]:
            target = target[key]
        if step["op"] == "remove":
            del target[step["path"][-1]]
        else:
            target[step["path"][-1]] = step["value"]
    return out


def _bad_catalog_provider(sha: str) -> dict[str, Any]:
    """A calibrated sample whose catalog provider fails validation with a sentinel string in Pydantic's error."""
    out = _solution_for(sha)
    matched = next(f for f in out["findings"] if f["evidenceClass"] == "catalog_matched")
    matched["catalogRef"]["provider"] = SOLUTION_SENTINEL
    return out


def _refused_solutions(sha: str) -> dict[str, bytes]:
    other_sha = SHAS[1] if sha != SHAS[1] else SHAS[2]
    valid = _solution_for(sha)
    return {
        "bytes that are not UTF-8": b"\xff\xfe{}",
        "text that is not JSON": f'{{"id": "{SOLUTION_SENTINEL}"'.encode(),
        "non-JSON NaN in an ignored key": ('{"extra": NaN, ' + json.dumps(valid)[1:]).encode(),
        "non-JSON Infinity in an ignored key": ('{"extra": Infinity, ' + json.dumps(valid)[1:]).encode(),
        "overflow float in an ignored key": ('{"extra": 1e500, ' + json.dumps(valid)[1:]).encode(),
        "overflow integer in an ignored key": ('{"extra": ' + "9" * 400 + ", " + json.dumps(valid)[1:]).encode(),
        "deeply nested JSON": b"[" * 2500 + b"]" * 2500,
        "a JSON list": b"[]",
        "a JSON list of key-value pairs": json.dumps(list(valid.items())).encode(),
        "JSON null": b"null",
        "JSON number": b"42",
        "JSON text": f'"{SOLUTION_SENTINEL}"'.encode(),
        "JSON boolean": b"true",
        "an empty object": b"{}",
        "an observed finding relabelled catalog_matched": json.dumps(
            _solution_for(sha, _refused_sample("audit_j_observed_relabelled_catalog_matched_its_evidence_still_observed"))
        ).encode(),
        "a catalog match without its catalog entry": json.dumps(
            _solution_for(sha, _refused_sample("audit_j_catalog_match_without_its_catalog_entry"))
        ).encode(),
        "a catalog reference failing validation with secret input": json.dumps(_bad_catalog_provider(sha)).encode(),
        "another upload's solution": json.dumps(_solution_for(other_sha)).encode(),
        "wrong sourceSha256 only": json.dumps({**valid, "sourceSha256": other_sha}).encode(),
        "wrong sourceAssetId only": json.dumps({**valid, "sourceAssetId": f"src_{other_sha[:16]}"}).encode(),
    }


# ---- Made-up uploads and records ----


def _swap(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {k: _swap(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap(v, old, new) for v in value]
    if isinstance(value, str):
        return new if value == old else value.replace(old, new).replace(old[:16], new[:16])
    return value


def record(sha: str, kind: str, state: str, n: int) -> dict[str, Any]:
    """The contract's first valid record of that kind and state, made this upload's, with id `n`."""
    base = next(r for r in VALID if r["kind"] == kind and r["state"] == state)
    made = _swap(copy.deepcopy(base), base["sourceAssetSha256"], sha)
    made["id"] = f"job_{sha[:16]}{n:016x}"
    UploadJob.model_validate(made)
    return made


def make_upload(root: Path, sha: str) -> Path:
    """An upload's folder as the upload store leaves it: its original and its record, both read-only."""
    folder = root / sha[:2] / sha
    folder.mkdir(parents=True)
    for name, body in (("original.jpg", b"original " + sha.encode()), ("record.json", b"{}")):
        (folder / name).write_bytes(body)
        os.chmod(folder / name, 0o400)
    return folder


def stores(root: Path) -> tuple[IngestStore, JobStore]:
    root.mkdir(parents=True, exist_ok=True)
    uploads = IngestStore(root, max_bytes=1000)
    return uploads, JobStore(uploads)


def snapshot(top: Path) -> dict[str, tuple[Any, ...]]:
    """Every name under `top`, never through a link: its kind, mode and bytes, or a link's target."""
    seen: dict[str, tuple[Any, ...]] = {}
    for folder, dirs, files in os.walk(top, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(folder) / name
            info = os.lstat(path)
            rel = str(path.relative_to(top))
            if stat.S_ISLNK(info.st_mode):
                seen[rel] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                seen[rel] = ("folder", stat.S_IMODE(info.st_mode))
            else:
                seen[rel] = ("file", stat.S_IMODE(info.st_mode), path.read_bytes())
    return seen


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, IngestStore, JobStore]:
    uploads, jobs = stores(tmp_path / "store")
    make_upload(jobs.root, SHAS[0])
    return tmp_path, uploads, jobs


# ---- Ids and records ----


def test_a_job_id_names_its_upload_and_is_never_reused() -> None:
    ids = {new_job_id(SHAS[0]) for _ in range(1000)}
    assert len(ids) == 1000
    assert all(re.fullmatch(rf"job_{SHAS[0][:16]}[0-9a-f]{{16}}", i) for i in ids)
    for bad in ("A" * 64, "a" * 64 + "0", "a" * 63):
        with pytest.raises(ValueError):
            new_job_id(bad)


def test_jobs_folder_lock_bounds_waits_and_reopens_replaced_folder(tree: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno
    import fcntl
    import threading

    tmp, uploads, jobs = tree
    folder = jobs.upload_folder(SHAS[0]) / "jobs"
    folder.mkdir()

    # Thread guard timeout at the exact bound
    guard = job_store._folder_guard(folder)
    assert guard.acquire(timeout=1.0)
    try:
        monkeypatch.setattr(job_store, "FOLDER_LOCK_BOUND_SEC", 0.005)
        errors: list[int | None] = []

        def waiter() -> None:
            try:
                with job_store._locked_jobs_folder(folder):
                    errors.append(-1)
            except OSError as exc:
                errors.append(exc.errno)

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        t.join(5.0)
        assert not t.is_alive() and errors == [errno.ETIMEDOUT]
    finally:
        guard.release()

    # Non-directory / symlink refused immediately; non-EWOULDBLOCK flock error propagated immediately
    not_dir = tmp / "plain-file"
    not_dir.write_bytes(b"x")
    with pytest.raises(OSError):
        with job_store._locked_jobs_folder(not_dir):
            pass
    link_dir = tmp / "link-dir"
    link_dir.symlink_to(folder, target_is_directory=True)
    with pytest.raises(OSError):
        with job_store._locked_jobs_folder(link_dir):
            pass

    real_flock = job_store.fcntl.flock
    monkeypatch.setattr(job_store.fcntl, "flock", lambda fd, op: (_ for _ in ()).throw(OSError(errno.EBADF, "bad fd")))
    with pytest.raises(OSError) as bad_err:
        with job_store._locked_jobs_folder(folder):
            pass
    assert bad_err.value.errno == errno.EBADF
    monkeypatch.setattr(job_store.fcntl, "flock", real_flock)

    # Inner flock deadline at exact >= boundary without sleeping past deadline
    held_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(held_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        monkeypatch.setattr(job_store, "FOLDER_LOCK_BOUND_SEC", 0.0)
        ticks = iter([10.0, 10.0, 10.0])
        monkeypatch.setattr(job_store.time, "monotonic", lambda: next(ticks, 10.0))
        monkeypatch.setattr(
            job_store.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("slept past exact deadline"))
        )
        with pytest.raises(OSError) as inner_timeout:
            with job_store._locked_jobs_folder(folder):
                pass
        assert inner_timeout.value.errno == errno.ETIMEDOUT
    finally:
        fcntl.flock(held_fd, fcntl.LOCK_UN)
        os.close(held_fd)

    # Outer inode-replacement loop re-opens replaced folder, and obeys deadline if inode keeps changing
    monkeypatch.undo()
    old_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(old_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    entered = threading.Event()
    acquired_ino: list[int] = []

    def lock_live_folder() -> None:
        entered.set()
        with job_store._locked_jobs_folder(folder):
            probe = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired_ino.append(os.fstat(probe).st_ino)
            finally:
                os.close(probe)

    worker = threading.Thread(target=lock_live_folder, daemon=True)
    worker.start()
    assert entered.wait(5.0)
    moved = tmp / "moved-jobs"
    folder.rename(moved)
    folder.mkdir()
    new_ino = os.lstat(folder).st_ino
    fcntl.flock(old_fd, fcntl.LOCK_UN)
    os.close(old_fd)
    worker.join(5.0)
    assert not worker.is_alive() and acquired_ino == [new_ino]

    with monkeypatch.context() as mp:
        mp.setattr(job_store, "FOLDER_LOCK_BOUND_SEC", 0.0)
        ticks2 = iter([20.0, 20.0, 20.0])
        mp.setattr(job_store.time, "monotonic", lambda: next(ticks2, 20.0))
        lstat_calls = [0]

        def churning_lstat(_p: Any) -> Any:
            lstat_calls[0] += 1
            assert lstat_calls[0] <= 3, "outer loop spun past deadline without raising ETIMEDOUT"
            return type("S", (), {"st_ino": -1, "st_mode": stat.S_IFDIR})()

        mp.setattr(job_store.os, "lstat", churning_lstat)
        mp.setattr(
            job_store.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("outer loop slept past deadline"))
        )
        with pytest.raises(OSError) as outer_timeout:
            with job_store._locked_jobs_folder(folder):
                pass
        assert outer_timeout.value.errno == errno.ETIMEDOUT


@pytest.mark.parametrize(("kind", "state"), STATES)
def test_a_record_is_written_whole_and_read_back_checked(tree: Any, kind: str, state: str) -> None:
    _, _, jobs = tree
    made = record(SHAS[0], kind, state, 1)
    assert jobs.write_record(made) == made
    assert jobs.read_job(made["id"]) == made
    path = jobs.record_path(made["id"])
    assert stat.S_IMODE(path.stat().st_mode) == (0o400 if state in job_store.TERMINAL_STATES else 0o600)
    assert sorted(os.listdir(path.parent)) == [path.name]


@pytest.mark.parametrize("breaks", ["json.dump", "os.fsync", "os.replace"])
def test_a_failed_write_leaves_the_record_it_had_and_no_scratch(tree: Any, monkeypatch: pytest.MonkeyPatch,
                                                                 breaks: str) -> None:
    _, _, jobs = tree
    before = record(SHAS[0], "solve", "running", 1)
    jobs.write_record(before)
    path = jobs.record_path(before["id"])
    stored = path.read_bytes()
    module, name = breaks.split(".")
    real = getattr(getattr(job_store, module), name)

    def broken(*args: Any, **kwargs: Any) -> Any:
        if module == "json":
            args[1].write('{"half": ')
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(getattr(job_store, module), name, broken)
    with pytest.raises(OSError):
        jobs.write_record(before)
    monkeypatch.setattr(getattr(job_store, module), name, real)
    assert path.read_bytes() == stored
    assert sorted(os.listdir(path.parent)) == [path.name]


def test_a_record_whose_job_has_ended_is_never_rewritten(tree: Any) -> None:
    _, _, jobs = tree
    ended = record(SHAS[0], "solve", "succeeded", 1)
    jobs.write_record(ended)
    path = jobs.record_path(ended["id"])
    stored = path.read_bytes()
    for state in ("running", "failed"):
        with pytest.raises(Ended):
            jobs.write_record({**record(SHAS[0], "solve", state, 1)})
    assert path.read_bytes() == stored


def test_a_record_that_fails_the_contract_is_never_served_or_rewritten(tree: Any) -> None:
    _, _, jobs = tree
    good = record(SHAS[0], "solve", "running", 1)
    jobs.write_record(good)
    path = jobs.record_path(good["id"])
    os.chmod(path, 0o600)
    path.write_text(json.dumps({**good, "state": "finished"}), encoding="utf-8")
    stored = path.read_bytes()
    with pytest.raises(Unreadable):
        jobs.read_job(good["id"])
    assert jobs.jobs_of(SHAS[0]) == []
    with pytest.raises(Ended):
        jobs.write_record(good)
    counts, handed = jobs.sweep(now=NOW)
    assert (counts.unreadable, counts.interrupted, handed) == (1, 0, [])
    assert path.read_bytes() == stored


def test_the_store_never_reads_or_writes_through_a_link(tree: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp, _, jobs = tree
    outside = tmp / "outside"
    outside.mkdir()
    outside_record = record(SHAS[0], "solve", "queued", 99)
    (outside / f"{outside_record['id']}.json").write_text(json.dumps(outside_record), encoding="utf-8")
    folder = jobs.upload_folder(SHAS[0])
    (folder / "jobs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        jobs.write_record(record(SHAS[0], "solve", "queued", 1))
    assert jobs.read_job(outside_record["id"]) is None
    real_scandir = job_store.os.scandir
    scanned: list[str] = []
    monkeypatch.setattr(job_store.os, "scandir", lambda p: scanned.append(os.fsdecode(p)) or real_scandir(p))
    assert jobs.jobs_of(SHAS[0]) == [] and scanned == []
    monkeypatch.setattr(job_store.os, "scandir", real_scandir)
    assert sorted(os.listdir(outside)) == [f"{outside_record['id']}.json"]
    (folder / "jobs").unlink()
    made = record(SHAS[0], "solve", "queued", 2)
    jobs.write_record(made)
    target = outside / "a.json"
    target.write_text(json.dumps(made), encoding="utf-8")
    link = jobs.record_path(f"job_{SHAS[0][:16]}{3:016x}")
    link.symlink_to(target)
    with pytest.raises(Unreadable):
        jobs.read_job(link.stem)
    assert [r["id"] for r in jobs.jobs_of(SHAS[0])] == [made["id"]]


def test_the_store_never_makes_an_upload_folder(tmp_path: Path) -> None:
    _, jobs = stores(tmp_path / "store")
    with pytest.raises(FileNotFoundError):
        jobs.write_record(record(SHAS[1], "solve", "queued", 1))
    assert os.listdir(jobs.root) == []


def test_a_partial_is_published_in_one_step_and_never_over_a_name(tree: Any) -> None:
    _, _, jobs = tree
    job_id = f"job_{SHAS[0][:16]}{1:016x}"
    partial = jobs.partial(job_id, "solution")
    partial.write_text("{}", encoding="utf-8")
    target = jobs.upload_folder(SHAS[0]) / "solutions" / f"{job_id}.json"
    jobs.publish(partial, target)
    assert target.read_text(encoding="utf-8") == "{}" and not partial.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o400
    again = jobs.partial(job_id, "solution")
    again.write_text("[]", encoding="utf-8")
    with pytest.raises(FileExistsError):
        jobs.publish(again, target)
    assert target.read_text(encoding="utf-8") == "{}"
    for bad in ("../x", "x.part-y"):
        with pytest.raises(ValueError):
            jobs.partial(job_id, bad)


# ---- Names, ids and folders the store refuses, finds or leaves ----


@pytest.mark.parametrize("pattern", ["JOB_ID", "JOB_FILE", "RESULT_FILE", "JOB_PARTIAL", "STORE_SCRATCH", "LOCK_FILE",
                                     "SHA256", "LEGACY_JOB_ID", "BUCKET"])
def test_each_name_the_store_makes_is_matched_whole(pattern: str) -> None:
    made = {"JOB_ID": f"job_{'a' * 32}", "JOB_FILE": f"job_{'a' * 32}.json", "RESULT_FILE": f"job_{'a' * 32}.json",
            "JOB_PARTIAL": f"job_{'a' * 32}-solution.part", "STORE_SCRATCH": ".job-0123456789abcdef.tmp",
            "LOCK_FILE": "display_copy.graph_0123456789abcdef.lock", "SHA256": "a" * 64,
            "LEGACY_JOB_ID": f"job_{'a' * 16}", "BUCKET": "ab"}[pattern]
    compiled = getattr(job_store, pattern)
    assert compiled.fullmatch(made)
    for other in (f"-{made}", f"{made}x", f"{made}\n", f"{made[:-1]}"):
        assert not compiled.fullmatch(other), (pattern, other)


def test_the_sweeps_counts_are_frozen_and_each_named() -> None:
    counts = SweepCounts(1, 2, 3, 4, 5, 6)
    with pytest.raises(AttributeError):
        counts.interrupted = 0  # type: ignore[misc]
    with pytest.raises(TypeError):
        SweepCounts()  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "../" + "a" * 61, "a" * 64 + "\n"])
def test_only_a_hash_names_an_upload_folder(tree: Any, bad: str) -> None:
    _, _, jobs = tree
    with pytest.raises(ValueError):
        jobs.upload_folder(bad)


@pytest.mark.parametrize(
    "bad",
    [f"job_{'a' * 16}", f"job_{'a' * 40}", f"job_{SHAS[0][:16]}{'a' * 24}", f"job_{'A' * 32}", f"job_{'a' * 32}\n"],
)
def test_a_job_id_the_store_did_not_make_is_refused(tree: Any, bad: str) -> None:
    _, _, jobs = tree
    with pytest.raises(ValueError):
        jobs.read_job(bad)
    with pytest.raises(ValueError):
        jobs.write_record({**record(SHAS[0], "solve", "queued", 1), "id": bad})
    assert not (jobs.upload_folder(SHAS[0]) / "jobs").exists()


def test_a_record_for_another_uploads_id_is_refused(tree: Any) -> None:
    _, _, jobs = tree
    make_upload(jobs.root, SHAS[1])
    with pytest.raises(ValueError, match="did not make for this upload"):
        jobs.write_record({**record(SHAS[0], "solve", "queued", 1), "id": f"job_{SHAS[1][:16]}{1:016x}"})
    assert not (jobs.upload_folder(SHAS[0]) / "jobs").exists()


def test_a_record_for_an_upload_never_stored_is_refused_in_plain_words(tmp_path: Path) -> None:
    _, jobs = stores(tmp_path / "store")
    with pytest.raises(FileNotFoundError, match="no stored upload for that job"):
        jobs.write_record(record(SHAS[1], "solve", "queued", 1))


def test_reads_make_no_folder_and_find_nothing_where_nothing_is(tree: Any) -> None:
    _, _, jobs = tree
    unknown = f"job_{SHAS[2][:16]}{1:016x}"
    assert jobs.read_job(unknown) is None
    assert jobs.read_job(f"job_{SHAS[0][:16]}{1:016x}") is None
    assert jobs.jobs_of(SHAS[0]) == [] and jobs.jobs_of(SHAS[2]) == []
    assert jobs.legacy_solve_job(SHAS[0]) is None and jobs.legacy_solve_job(SHAS[2]) is None
    assert jobs.legacy_solve_job_by_id(f"job_{SHAS[2][:16]}") is None
    assert jobs.read_solution(SHAS[0]) is None and jobs.read_solution(SHAS[2]) is None
    assert not jobs.is_held(jobs.lock_path(SHAS[0], "solve"))
    assert sorted(os.listdir(jobs.upload_folder(SHAS[0]))) == ["original.jpg", "record.json"]
    assert sorted(os.listdir(jobs.root)) == [SHAS[0][:2]]


def test_an_id_finds_only_its_own_upload_folder(tree: Any) -> None:
    tmp, _, jobs = tree
    made = record(SHAS[0], "solve", "queued", 1)
    jobs.write_record(made)
    prefix = SHAS[0][:16]
    bucket = jobs.root / SHAS[0][:2]
    # Decoys in the same bucket, none of them the upload's folder: the prefix inside a name, a file and a link to a
    # folder each named like one, a name too short to be a hash
    (bucket / (SHAS[0][:2] + prefix + "0" * 46)).mkdir()
    linked = tmp / "elsewhere"
    linked.mkdir()
    (bucket / (prefix + "f" * 48)).write_text("{}", encoding="utf-8")
    (bucket / (prefix + "e" * 48)).symlink_to(linked, target_is_directory=True)
    (bucket / prefix).mkdir()
    (bucket / (prefix + "b" * 48 + "0")).mkdir()
    assert jobs.read_job(made["id"]) == made
    # Two real folders that share the prefix: no one folder is the job's
    (bucket / (prefix + "d" * 48)).mkdir()
    assert jobs.read_job(made["id"]) is None


def test_a_record_file_is_written_as_indented_json(tree: Any) -> None:
    _, _, jobs = tree
    made = record(SHAS[0], "solve", "failed", 1)
    written = jobs.write_record(made)
    text = jobs.record_path(made["id"]).read_text(encoding="utf-8")
    assert text == json.dumps(written, indent=2, ensure_ascii=False)


def test_an_uploads_records_are_listed_oldest_first_and_only_its_records(tree: Any) -> None:
    _, _, jobs = tree
    made = [record(SHAS[0], "solve", state, n) for n, state in
            enumerate(("queued", "running", "succeeded", "failed"), start=2)]
    for one in made:
        jobs.write_record(one)
    folder = jobs.upload_folder(SHAS[0]) / "jobs"
    for n in (1, 15):
        (folder / f"job_{SHAS[0][:16]}{n:016x}.json").write_text("{}", encoding="utf-8")
    (folder / "backup.json").write_text(json.dumps(record(SHAS[0], "solve", "queued", 9)), encoding="utf-8")
    (folder / f"job_{SHAS[0][:16]}{10:016x}.json.bak").write_text(json.dumps(record(SHAS[0], "solve", "queued", 10)),
                                                              encoding="utf-8")
    elsewhere = folder.parent.parent.parent / "elsewhere.json"
    elsewhere.write_text(json.dumps(record(SHAS[0], "solve", "queued", 11)), encoding="utf-8")
    (folder / f"job_{SHAS[0][:16]}{11:016x}.json").symlink_to(elsewhere)
    (folder / f"job_{SHAS[0][:16]}{12:016x}.json").mkdir()
    listed = jobs.jobs_of(SHAS[0])
    assert [r["id"] for r in listed] == [r["id"] for r in sorted(made, key=lambda r: (r["createdAtUTC"], r["id"]))]


def test_todays_records_are_never_read_through_a_linked_upload_folder(tree: Any) -> None:
    tmp, uploads, jobs = tree
    outside = tmp / "outside-upload"
    outside.mkdir()
    (outside / "job.json").write_text(json.dumps(_legacy_job(SHAS[1])), encoding="utf-8")
    (outside / "solution.json").write_text(json.dumps(_solution_for(SHAS[1])), encoding="utf-8")
    bucket = jobs.root / SHAS[1][:2]
    bucket.mkdir(exist_ok=True)
    (bucket / SHAS[1]).symlink_to(outside, target_is_directory=True)
    assert jobs.legacy_solve_job(SHAS[1]) is None
    assert jobs.legacy_solve_job_by_id(f"job_{SHAS[1][:16]}") is None
    assert jobs.read_solution(SHAS[1]) is None
    assert ingest_router._solve_job(uploads, SHAS[1]) is None
    (outside / "solution.json").write_text("{}", encoding="utf-8")
    assert jobs.read_solution(SHAS[1]) is None
    with pytest.raises(ValueError):
        jobs.legacy_solve_job_by_id(f"job_{SHAS[1][:16]}0")


@pytest.mark.parametrize(("kind", "graph"), [("Solve", None), ("solve.x", None), ("a.lockb", None), ("display_copy", "graph_xyz"),
                                             ("display_copy", "../graph_0123456789abcdef")])
def test_a_lock_name_the_store_does_not_make_is_refused(tree: Any, kind: str, graph: str | None) -> None:
    _, _, jobs = tree
    with pytest.raises(ValueError):
        jobs.lock_path(SHAS[0], kind, graph)


def test_a_media_jobs_lock_is_its_graphs(tree: Any) -> None:
    _, _, jobs = tree
    media = next(r for r in VALID if r["kind"] in ("display_copy", "restoration") and r.get("graphId"))
    made = record(SHAS[0], media["kind"], media["state"], 1)
    assert jobs.lock_of(made).name == f"{made['kind']}.{made['graphId']}.lock"
    assert jobs.lock_of(record(SHAS[0], "solve", "queued", 2)).name == "solve.lock"


def test_a_lock_that_is_a_link_or_a_folder_is_held_by_no_one(tree: Any) -> None:
    tmp, uploads, jobs = tree
    holder = JobStore(uploads)
    elsewhere = jobs.lock_path(SHAS[0], "restoration")
    assert holder.try_hold(elsewhere)
    try:
        lock = jobs.lock_path(SHAS[0], "solve")
        lock.symlink_to(elsewhere)
        assert not jobs.is_held(lock)
        with pytest.raises(OSError):
            jobs.try_hold(lock)
        lock.unlink()
        lock.mkdir()
        assert not jobs.is_held(lock)
    finally:
        holder.release(elsewhere)


def test_a_lock_this_process_holds_stays_held_when_its_file_is_removed(tree: Any) -> None:
    """The store's own map decides for a lock this process holds: a new file at the lock's name, made after the held
    one was removed, is not a second lock to take, and the held one is still held."""
    _, _, jobs = tree
    lock = jobs.lock_path(SHAS[0], "solve")
    assert jobs.try_hold(lock)
    try:
        lock.unlink()
        assert jobs.is_held(lock), "a held lock whose file was removed is still held"
        assert not jobs.try_hold(lock), "a held lock is not taken again through a new file"
        assert not lock.exists(), "a refused take makes no file"
    finally:
        jobs.release(lock)
    assert jobs.try_hold(lock)
    jobs.release(lock)


def test_publish_takes_only_a_partial_the_store_made(tree: Any) -> None:
    tmp, _, jobs = tree
    job_id = f"job_{SHAS[0][:16]}{1:016x}"
    target = jobs.upload_folder(SHAS[0]) / "solutions" / f"{job_id}.json"
    stray = tmp / f"{job_id}-solution.part"
    stray.write_text("{}", encoding="utf-8")
    misnamed = jobs.partial(job_id, "solution").parent / "notes.part"
    misnamed.write_text("{}", encoding="utf-8")
    tailed = misnamed.parent / f"{job_id}-x.part-y.part"
    tailed.write_text("{}", encoding="utf-8")
    linked = misnamed.parent / f"{job_id}-linked.part"
    linked.symlink_to(stray)
    for partial in (stray, misnamed, tailed, linked):
        with pytest.raises(ValueError):
            jobs.publish(partial, target)
    assert not target.exists() and stray.exists() and misnamed.exists() and tailed.exists() and linked.is_symlink()


def test_a_record_filed_under_another_jobs_name_is_left_as_unreadable(tree: Any) -> None:
    _, _, jobs = tree
    made = record(SHAS[0], "solve", "running", 1)
    jobs.write_record(made)
    other = jobs.record_path(f"job_{SHAS[0][:16]}{2:016x}")
    other.write_text(json.dumps(made), encoding="utf-8")
    stored = other.read_bytes()
    counts, _ = jobs.sweep(now=NOW)
    assert (counts.unreadable, counts.interrupted) == (1, 1)
    assert other.read_bytes() == stored


@pytest.mark.parametrize("later", ["startedAtUTC", "cancelRequestedAtUTC"])
def test_an_interrupted_job_finishes_no_earlier_than_it_started_or_was_asked_to_stop(later: str) -> None:
    running = record(SHAS[0], "solve", "running", 1)
    running["cancelRequestedAtUTC"] = running["startedAtUTC"]
    running[later] = "2040-01-01T00:00:00Z"
    if later == "startedAtUTC":
        running["cancelRequestedAtUTC"] = "2040-01-01T00:00:00Z"
    ended = job_store._interrupted(running, NOW)
    assert ended["finishedAtUTC"] == "2040-01-01T00:00:00Z"
    assert job_store._interrupted({**running, "startedAtUTC": "2029-01-01T00:00:00Z",
                                   "cancelRequestedAtUTC": "2029-01-01T00:00:01Z"}, NOW)["finishedAtUTC"] \
        == "2030-01-01T05:00:00Z"
    assert "queueAhead" not in ended and ended["state"] == "interrupted"


# ---- Locks across processes (each wait bounded) ----


def _hold(root: str, sha: str, ready: Any, release: Any, how: str) -> None:
    _, jobs = stores(Path(root))
    lock = jobs.lock_path(sha, "solve")
    if not jobs.try_hold(lock):
        os._exit(3)
    ready.set()
    if not release.wait(CHILD_BOUND_S):
        os._exit(4)
    if how == "dies":
        os._exit(0)
    jobs.release(lock)


def _child(target: Callable[..., None], *args: Any) -> Any:
    process = multiprocessing.get_context("spawn").Process(target=target, args=args, daemon=True)
    process.start()
    return process


def _ended(process: Any, what: str) -> int:
    process.join(CHILD_BOUND_S)
    try:
        assert not process.is_alive(), f"{what}: the child did not end in {CHILD_BOUND_S:g} s"
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    return process.exitcode


@pytest.mark.parametrize("how", ["releases", "dies"])
def test_a_lock_another_process_holds_is_refused_and_freed_when_it_ends(tree: Any, how: str) -> None:
    _, uploads, jobs = tree
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = _child(_hold, str(jobs.root), SHAS[0], ready, release, how)
    try:
        assert ready.wait(CHILD_BOUND_S), f"the child did not take the lock in {CHILD_BOUND_S:g} s"
        lock = jobs.lock_path(SHAS[0], "solve")
        assert jobs.is_held(lock)
        assert not jobs.try_hold(lock)
    finally:
        release.set()
    assert _ended(process, "the holder") == 0
    assert not jobs.is_held(lock)
    assert jobs.try_hold(lock) and jobs.is_held(lock)
    assert not jobs.try_hold(lock), "a store refuses a lock it holds"
    assert not JobStore(uploads).try_hold(lock), "a second open file of a held lock is refused in one process too"
    jobs.release(lock)
    assert not jobs.is_held(lock)


# ---- P-JOB-3: a process stopped after any one of an attempt's writes ----

ATTEMPT = ["queued", "running", "progress", "partial", "publish", "succeeded"]


def _attempt(root: str, sha: str, stop_after: int, inside: bool) -> None:
    """One solve attempt with the lock held, ending the process with os._exit right after write `stop_after`, or,
    with `inside`, inside that write after its scratch file is fsynced and before it replaces the record."""
    _, jobs = stores(Path(root))
    jobs.try_hold(jobs.lock_path(sha, "solve"))
    job_id = f"job_{sha[:16]}{1:016x}"
    if inside:
        def stop(*args: Any, **kwargs: Any) -> None:
            os._exit(0)
    for step, name in enumerate(ATTEMPT, start=1):
        if inside and step == stop_after:
            job_store.os.replace = stop  # type: ignore[assignment]
        if name in ("queued", "running", "succeeded"):
            jobs.write_record({**record(sha, "solve", name, 1), "id": job_id})
        elif name == "progress":
            jobs.write_record({**record(sha, "solve", "running", 1), "id": job_id})
        elif name == "partial":
            jobs.partial(job_id, "solution").write_text("{}", encoding="utf-8")
        else:
            jobs.publish(jobs.partial(job_id, "solution"),
                         jobs.upload_folder(sha) / "solutions" / f"{job_id}.json")
        if step == stop_after:
            os._exit(0)


WRITES = [n for n, name in enumerate(ATTEMPT, start=1) if name in ("queued", "running", "progress", "succeeded")]


@pytest.mark.parametrize(("stop_after", "inside"),
                         [(n, False) for n in range(1, len(ATTEMPT) + 1)] + [(n, True) for n in WRITES],
                         ids=[f"after {ATTEMPT[n - 1]}" for n in range(1, len(ATTEMPT) + 1)]
                         + [f"inside {ATTEMPT[n - 1]}" for n in WRITES])
def test_p_job_3_a_process_stopped_after_any_write_leaves_a_store_the_sweep_makes_whole(
        tree: Any, stop_after: int, inside: bool) -> None:
    _, _, jobs = tree
    original = snapshot(jobs.upload_folder(SHAS[0]))
    original = {k: v for k, v in original.items() if not k.startswith(("jobs", "solutions"))}
    assert _ended(_child(_attempt, str(jobs.root), SHAS[0], stop_after, inside), "the attempt") == 0
    job_id = f"job_{SHAS[0][:16]}{1:016x}"
    done = stop_after - 1 if inside else stop_after
    counts, handed = jobs.sweep(now=NOW)
    last = max((n for n in WRITES if n <= done), default=0)
    stored = jobs.read_job(job_id)
    if last == 0:
        assert stored is None and handed == []
    elif ATTEMPT[last - 1] == "queued":
        assert stored["state"] == "queued" and [r["id"] for r in handed] == [job_id]
    elif ATTEMPT[last - 1] == "succeeded":
        assert stored == {**record(SHAS[0], "solve", "succeeded", 1), "id": job_id}
    else:
        running = {**record(SHAS[0], "solve", "running", 1), "id": job_id}
        assert stored == job_store._interrupted(running, NOW) and counts.interrupted == 1
        assert stored["error"] == INTERRUPTED_SENTENCE
    result = jobs.upload_folder(SHAS[0]) / "solutions" / f"{job_id}.json"
    assert result.exists() == (last > 0 and ATTEMPT[last - 1] == "succeeded")
    assert counts.scratch_removed == int(inside)
    folder = jobs.upload_folder(SHAS[0])
    assert not any(name.endswith(".tmp") for name in os.listdir(folder / "jobs")) if (folder / "jobs").exists() else True
    assert not [n for n in os.listdir(jobs.root / ".partial") if n.startswith("job_")] \
        if (jobs.root / ".partial").exists() else True
    after = {k: v for k, v in snapshot(folder).items() if not k.startswith(("jobs", "solutions"))}
    assert after == original, "the upload's own files changed"
    assert not jobs.is_held(jobs.lock_path(SHAS[0], "solve"))


# ---- P-JOB-6: the sweep touches only what the store made, inside its own folders ----

EXTRAS = ("unreadable", "scratch", "other_name", "scratch_like", "link_record", "link_scratch", "folder_like_record",
          "result_named", "result_unnamed", "result_other_name", "link_result", "legacy_files")
PARTIALS = ("job_running", "job_ended", "upload_partial", "link_partial", "other_partial")


@st.composite
def layouts(draw: Any) -> dict[str, Any]:
    uploads = []
    for sha in draw(st.lists(st.sampled_from(SHAS), min_size=1, max_size=3, unique=True)):
        uploads.append({
            "sha": sha,
            "jobs": draw(st.lists(st.tuples(st.sampled_from(STATES), st.booleans()), max_size=4)),
            "extras": draw(st.sets(st.sampled_from(EXTRAS))),
            "jobs_link": draw(st.booleans()),
            "upload_link": draw(st.booleans()),
        })
    return {"uploads": uploads, "partials": draw(st.sets(st.sampled_from(PARTIALS)))}


def build(top: Path, layout: dict[str, Any]) -> tuple[JobStore, JobStore, dict[str, Any]]:
    """The store a layout describes, with an outside folder its links point into; the holder holds every lock the
    layout marks held. Returns the sweeper, the holder and what the sweep must do."""
    outside = top / "outside"
    outside.mkdir()
    (outside / "a running record.json").write_text("{}", encoding="utf-8")
    (outside / "linked.json").write_text("{}", encoding="utf-8")
    _, jobs = stores(top / "store")
    holder = JobStore(IngestStore(jobs.root, max_bytes=1000))
    expect: dict[str, Any] = {"interrupt": {}, "remove": set(), "handed": [], "running": set(), "unreadable": 0}
    n = 0
    for spec in layout["uploads"]:
        sha = spec["sha"]
        if spec["upload_link"]:
            # An upload's folder that is a link into the outside folder holding a job: never entered
            target = outside / f"upload-{sha[:8]}"
            make_upload(target.parent / "u", sha).rename(target)
            (jobs.root / sha[:2]).mkdir(exist_ok=True)
            (jobs.root / sha[:2] / sha).symlink_to(target, target_is_directory=True)
            (target / "jobs").mkdir()
            n += 1
            (target / "jobs" / f"job_{sha[:16]}{n:016x}.json").write_text(
                json.dumps(record(sha, "solve", "running", n)), encoding="utf-8")
            continue
        folder = make_upload(jobs.root, sha)
        succeeded: set[str] = set()
        written = []
        for (kind, state), hold in spec["jobs"]:
            n += 1
            made = record(sha, kind, state, n)
            jobs.write_record(made)
            lock = holder.lock_of(made)
            if hold and lock not in holder._held:
                assert holder.try_hold(lock)
            written.append(made)
        for made in written:
            # A lock is one piece of work's: every record of a held lock is held
            held = holder.lock_of(made) in holder._held
            state = made["state"]
            path = f"store/{sha[:2]}/{sha}/jobs/{made['id']}.json"
            if state == "running" and not held:
                expect["interrupt"][path] = job_store._interrupted(made, NOW)
            if state in ("running", "queued") and held:
                expect["running"].add(made["id"])
            if state == "queued" and not held:
                expect["handed"].append(made)
            if state == "succeeded":
                succeeded.add(made["id"])
        jobs_dir = folder / "jobs"
        jobs_dir.mkdir(exist_ok=True)
        results = folder / "solutions"
        results.mkdir(exist_ok=True)
        extras = spec["extras"]
        rel = f"store/{sha[:2]}/{sha}"
        if "unreadable" in extras:
            n += 1
            (jobs_dir / f"job_{sha[:16]}{n:016x}.json").write_text('{"state": "running"}', encoding="utf-8")
            expect["unreadable"] += 1
        if "scratch" in extras:
            (jobs_dir / ".job-0123456789abcdef.tmp").write_text("half", encoding="utf-8")
            expect["remove"].add(f"{rel}/jobs/.job-0123456789abcdef.tmp")
        if "other_name" in extras:
            (jobs_dir / "notes.txt").write_text("kept", encoding="utf-8")
        if "scratch_like" in extras:
            (jobs_dir / ".job-xyz.tmp").write_text("kept", encoding="utf-8")
            (jobs_dir / ".job.json-0123456789abcdef.tmp").write_text("kept", encoding="utf-8")
        if "link_record" in extras:
            n += 1
            (jobs_dir / f"job_{sha[:16]}{n:016x}.json").symlink_to(outside / "a running record.json")
        if "link_scratch" in extras:
            (jobs_dir / ".job-fedcba9876543210.tmp").symlink_to(outside / "linked.json")
        if "folder_like_record" in extras:
            n += 1
            (jobs_dir / f"job_{sha[:16]}{n:016x}.json").mkdir()
        named = sorted(succeeded)
        if "result_named" in extras and named:
            (results / f"{named[0]}.json").write_text("{}", encoding="utf-8")
        if "result_unnamed" in extras:
            n += 1
            (results / f"job_{sha[:16]}{n:016x}.json").write_text("{}", encoding="utf-8")
            expect["remove"].add(f"{rel}/solutions/job_{sha[:16]}{n:016x}.json")
        if "result_other_name" in extras:
            (results / "solution.json").write_text("{}", encoding="utf-8")
        if "link_result" in extras:
            n += 1
            (results / f"job_{sha[:16]}{n:016x}.json").symlink_to(outside / "linked.json")
        if "legacy_files" in extras:
            for name in ("job.json", "solution.json", ".job.json-0123456789abcdef.tmp"):
                (folder / name).write_text("{}", encoding="utf-8")
        if spec["jobs_link"]:
            # The solutions folder of this upload replaced by a link into the outside folder: never entered
            for entry in list(results.iterdir()):
                expect["remove"].discard(f"{rel}/solutions/{entry.name}")
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
            results.rmdir()
            linked = outside / f"solutions-{sha[:8]}"
            linked.mkdir()
            (linked / f"job_{sha[:16]}{0:016x}.json").write_text("{}", encoding="utf-8")
            results.symlink_to(linked, target_is_directory=True)
    _decoys(jobs.root, outside, [s["sha"] for s in layout["uploads"] if not s["upload_link"]])
    partials = jobs.root / ".partial"
    partials.mkdir(exist_ok=True)
    kept = sorted(expect["running"])
    if "job_running" in layout["partials"] and kept:
        (partials / f"{kept[0]}-solution.part").write_text("{}", encoding="utf-8")
    if "job_ended" in layout["partials"]:
        (partials / f"job_{SHAS[0][:16]}{999:016x}-solution.part").write_text("{}", encoding="utf-8")
        expect["remove"].add(f"store/.partial/job_{SHAS[0][:16]}{999:016x}-solution.part")
    if "upload_partial" in layout["partials"]:
        (partials / f"{'ab' * 16}.part").write_text("an upload arriving", encoding="utf-8")
    if "link_partial" in layout["partials"]:
        (partials / f"job_{SHAS[0][:16]}{998:016x}-x.part").symlink_to(outside / "linked.json")
    if "other_partial" in layout["partials"]:
        (partials / "notes.part").write_text("kept", encoding="utf-8")
    return jobs, holder, expect


def _decoys(root: Path, outside: Path, uploads: list[str]) -> None:
    """Names a wrong sweep would take for its own, each holding a running record it would rewrite or a file it would
    remove: a tail after a store name, a bucket of three characters, a hash with its bucket inside it past its start,
    a folder that is no hash, a hash with a tail, a bucket that is a link out of the store."""
    running = json.dumps(record(SHAS[0], "solve", "running", 900))
    for sha in uploads:
        folder = root / sha[:2] / sha
        (folder / "jobs" / f"job_{sha[:16]}{900:016x}.json.bak").write_text(running, encoding="utf-8")
        (folder / "jobs" / ".job-0123456789abcdef.tmp.keep").write_text("kept", encoding="utf-8")
        (folder / "solutions").mkdir(exist_ok=True)
        if not (folder / "solutions").is_symlink():
            (folder / "solutions" / f"job_{sha[:16]}{901:016x}.json.old").write_text("{}", encoding="utf-8")
    (root / ".partial").mkdir(exist_ok=True)
    (root / ".partial" / f"job_{SHAS[0][:16]}{902:016x}-x.part.keep").write_text("kept", encoding="utf-8")
    b = SHAS[0][:2]
    for folder in (root / (b + "0") / (b + "0" + "f" * 61), root / b / ("0" + b + "f" * 61), root / b / "notes",
                   root / b / (b + "f" * 62 + "0")):
        (folder / "jobs").mkdir(parents=True)
        (folder / "jobs" / f"job_{SHAS[0][:16]}{903:016x}.json").write_text(running, encoding="utf-8")
        (folder / "solutions").mkdir()
        (folder / "solutions" / f"job_{SHAS[0][:16]}{903:016x}.json").write_text("{}", encoding="utf-8")
    linked = outside / "bucket-ee" / ("ee" + "f" * 62)
    (linked / "jobs").mkdir(parents=True)
    (linked / "jobs" / f"job_{SHAS[0][:16]}{904:016x}.json").write_text(running, encoding="utf-8")
    (root / "ee").symlink_to(outside / "bucket-ee", target_is_directory=True)


@settings(max_examples=150, deadline=None, database=None)
@given(layout=layouts())
@example(
    layout={
        "uploads": [
            {
                "sha": SHAS[0],
                "jobs": [(("solve", "running"), False), (("solve", "queued"), False), (("solve", "succeeded"), False)],
                "extras": set(EXTRAS),
                "jobs_link": False,
                "upload_link": False,
            },
            {
                "sha": SHAS[1],
                "jobs": [(("display_copy", "running"), True)],
                "extras": set(EXTRAS),
                "jobs_link": True,
                "upload_link": False,
            },
            {"sha": SHAS[2], "jobs": [], "extras": set(), "jobs_link": False, "upload_link": True},
        ],
        "partials": set(PARTIALS),
    }
)
def test_p_job_6_the_sweep_touches_only_what_the_store_made_inside_its_own_folders(layout: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp)
        jobs, holder, expect = build(top, layout)
        before = snapshot(top)
        try:
            counts, handed = jobs.sweep(now=NOW)
            after = snapshot(top)
        finally:
            for lock in list(holder._held):
                holder.release(lock)
        changed = {k for k in before if k in after and before[k] != after[k]}
        assert set(after) == set(before) - expect["remove"], "the sweep removed or made other names"
        assert changed == set(expect["interrupt"]), "the sweep rewrote other files"
        for path, interrupted in expect["interrupt"].items():
            assert json.loads(after[path][2]) == interrupted
            assert after[path][1] == 0o400
        assert [r["id"] for r in handed] == [r["id"] for r in sorted(expect["handed"],
                                                                    key=lambda r: (r["createdAtUTC"], r["id"]))]
        removed_scratch = sum(1 for p in expect["remove"] if p.endswith(".tmp"))
        removed_results = sum(1 for p in expect["remove"] if "/solutions/" in p)
        removed_partials = sum(1 for p in expect["remove"] if "/.partial/" in p)
        assert counts == SweepCounts(len(expect["interrupt"]), len(expect["handed"]), expect["unreadable"],
                                     removed_results, removed_partials, removed_scratch)


def test_the_sweep_logs_counts_only(tree: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, jobs = tree
    for n in (1, 2, 3):
        jobs.write_record(record(SHAS[0], "solve", "running", n))
    real_read = jobs.read_record
    first_vanished = [True]

    def vanishing_first(path: Path) -> dict[str, Any]:
        if first_vanished[0]:
            first_vanished[0] = False
            raise FileNotFoundError(2, "vanished")
        return real_read(path)

    monkeypatch.setattr(jobs, "read_record", vanishing_first)
    with caplog.at_level(logging.INFO, logger="albedian.jobs"):
        counts, _ = jobs.sweep(now=NOW)
    assert counts == SweepCounts(2, 0, 0, 0, 0, 0)
    messages = [r.getMessage() for r in caplog.records if r.name == "albedian.jobs"]
    assert messages == ["job sweep: 2 interrupted, 0 handed back, 0 unreadable, 0 results, 0 partials and 0 scratch "
                        "files removed"]


# ---- The app and its routes ----


@pytest.fixture
def app_and_store(tmp_path: Path) -> Iterator[tuple[Any, IngestStore, JobStore]]:
    uploads, jobs = stores(tmp_path / "store")
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    yield app, uploads, jobs
    app.dependency_overrides.clear()


def fake_solve_kind(monkeypatch: pytest.MonkeyPatch, body: Optional[Callable[[Any], Any]] = None) -> list[str]:
    """The solve kind replaced by one that records the upload it ran for, then runs `body` or refuses the work."""
    ran: list[str] = []

    def run(ctx: Any) -> Any:
        ran.append(ctx.sha256)
        if body is not None:
            return body(ctx)
        raise JobRefusal("The decoder could not read the file.")

    monkeypatch.setitem(job_runner._KINDS, "solve", dataclasses.replace(ingest_jobs.SOLVE_KIND, run=run))
    return ran


def _legacy_job(sha: str) -> dict[str, Any]:
    return {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "queued", "createdAtUTC": "2030-01-01T05:00:00Z",
            "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}


def test_the_app_sweeps_its_store_once_at_start(app_and_store: Any, caplog: pytest.LogCaptureFixture) -> None:
    app, _, jobs = app_and_store
    make_upload(jobs.root, SHAS[0])
    made = record(SHAS[0], "solve", "running", 1)
    jobs.write_record(made)
    with caplog.at_level(logging.INFO, logger="albedian.jobs"), TestClient(app):
        stored = jobs.read_job(made["id"])
    assert stored["state"] == "interrupted" and stored["error"] == INTERRUPTED_SENTENCE
    # Once: a second sweep would log a second line, of nothing found.
    assert [r.getMessage() for r in caplog.records if r.name == "albedian.jobs"] == [
        "job sweep: 1 interrupted, 0 handed back, 0 unreadable, 0 results, 0 partials and 0 scratch files removed"]


def test_the_app_sweeps_once_and_starts_when_the_sweep_fails_logging_only_its_kind(
        app_and_store: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    app, uploads, _ = app_and_store
    swept: list[Path] = []

    def failing_sweep(self: JobStore, now: Any = None) -> Any:
        swept.append(self.root)
        raise PermissionError(13, "Permission denied", str(self.root / "jobs"))

    monkeypatch.setattr(JobStore, "sweep", failing_sweep)
    with caplog.at_level(logging.INFO, logger="albedian.jobs"), TestClient(app) as client:
        assert client.get(f"/api/ingest/{'0' * 64}").status_code == 404, "the app serves after a failed sweep"
    assert swept == [JobStore(uploads).root], "one sweep, of the store the ingest routes use"
    assert [r.getMessage() for r in caplog.records if r.name == "albedian.jobs"] == ["job sweep stopped: PermissionError"]
    assert str(uploads.root) not in caplog.text


def test_the_job_route_serves_a_checked_record_and_refuses_one_that_fails(app_and_store: Any) -> None:
    app, _, jobs = app_and_store
    folder = make_upload(jobs.root, SHAS[0])
    made = record(SHAS[0], "solve", "succeeded", 1)
    jobs.write_record(made)
    (folder / "job.json").write_text(json.dumps(_legacy_job(SHAS[0])), encoding="utf-8")
    client = TestClient(app)
    assert client.get(f"/api/ingest/jobs/{made['id']}").json() == made
    assert client.get(f"/api/ingest/jobs/job_{SHAS[0][:16]}").json() == _legacy_job(SHAS[0])
    assert client.get(f"/api/ingest/jobs/job_{SHAS[0][:16]}{'0' * 20}").status_code == 404
    (folder / "job.json").write_text(json.dumps({**_legacy_job(SHAS[0]), "state": "finished"}), encoding="utf-8")
    path = jobs.record_path(made["id"])
    os.chmod(path, 0o600)
    path.write_text(json.dumps({**made, "state": "finished"}), encoding="utf-8")
    for job_id in (made["id"], f"job_{SHAS[0][:16]}"):
        response = client.get(f"/api/ingest/jobs/{job_id}")
        assert (response.status_code, response.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})


def test_a_stored_solution_is_read_through_the_contract_and_returned_as_stored(tree: Any) -> None:
    _, _, jobs = tree
    path = jobs.upload_folder(SHAS[0]) / "solution.json"
    stored_valid = {**_solution_for(SHAS[0]), "extraPreserved": 7}
    body = json.dumps(stored_valid, indent=1).encode()
    path.write_bytes(body)
    assert jobs.read_solution(SHAS[0]) == stored_valid
    assert path.read_bytes() == body
    old_shape = _solution_for(SHAS[0])
    matched = next(f for f in old_shape["findings"] if f["evidenceClass"] == "catalog_matched")
    matched["catalogRef"] = {
        **matched["catalogRef"],
        "catalogSourceId": "HIP 24436",
        "constellation": "Ori",
        "bayerDesignation": "HR 1713",
    }
    matched["catalogRef"].pop("flamsteedNumber", None)
    old_bytes = json.dumps(old_shape).encode()
    path.write_bytes(old_bytes)
    upgraded = jobs.read_solution(SHAS[0])
    assert upgraded is not None
    up_matched = next(f for f in upgraded["findings"] if f["evidenceClass"] == "catalog_matched")
    assert up_matched["catalogRef"]["bayerDesignation"] == "Beta Orionis"
    assert up_matched["catalogRef"]["flamsteedNumber"] == 19
    assert path.read_bytes() == old_bytes
    with pytest.raises(ValueError):
        jobs.read_solution("not-a-sha256")


@pytest.mark.parametrize("kind", sorted(_refused_solutions(SHAS[0])))
def test_a_stored_solution_that_cannot_be_served_is_unreadable_and_left_as_it_is(tree: Any, kind: str) -> None:
    _, _, jobs = tree
    payload = _refused_solutions(SHAS[0])[kind]
    path = jobs.upload_folder(SHAS[0]) / "solution.json"
    path.write_bytes(payload)
    with pytest.raises(Unreadable):
        jobs.read_solution(SHAS[0])
    assert path.read_bytes() == payload
    assert sorted(os.listdir(path.parent)) == ["original.jpg", "record.json", "solution.json"]


def test_a_solution_that_is_a_link_or_a_folder_is_refused_and_never_followed(tree: Any) -> None:
    tmp, _, jobs = tree
    outside = tmp / "outside-solution.json"
    outside.write_text(json.dumps(_solution_for(SHAS[0])), encoding="utf-8")
    path = jobs.upload_folder(SHAS[0]) / "solution.json"
    path.symlink_to(outside)
    with pytest.raises(Unreadable):
        jobs.read_solution(SHAS[0])
    assert path.is_symlink()
    path.unlink()
    path.mkdir()
    with pytest.raises(Unreadable):
        jobs.read_solution(SHAS[0])
    assert path.is_dir()
    path.rmdir()
    path.write_text(json.dumps(_solution_for(SHAS[0])), encoding="utf-8")
    os.chmod(path, 0o000)
    try:
        with pytest.raises(Unreadable):
            jobs.read_solution(SHAS[0])
    finally:
        os.chmod(path, 0o600)
    bucket = path.parent.parent
    os.chmod(bucket, 0o000)
    try:
        with pytest.raises(Unreadable):
            jobs.read_solution(SHAS[0])
    finally:
        os.chmod(bucket, 0o700)


def test_the_solution_route_serves_a_checked_solution_and_refuses_every_other_kind_alike(
        app_and_store: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert ingest_router.UNREADABLE_SOLUTION == (
        "The stored solution for that file does not meet the contract, so it is not served; run the solve again."
    )
    assert ingest_router.UNREADABLE_SOLUTION != ingest_router.UNREADABLE_JOB
    app, _, jobs = app_and_store
    client = TestClient(app)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
                        "0000000a49444154789c63600000000200015e2fe9e30000000049454e44ae426082")
    stored = client.post("/api/ingest", content=png, headers={"content-type": "image/png"})
    assert stored.status_code == 201, stored.text
    sha = stored.json()["sourceAsset"]["sha256"]
    path = jobs.upload_folder(sha) / "solution.json"
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 404, "no solution stored"
    with caplog.at_level(logging.DEBUG):
        for kind, body in _refused_solutions(sha).items():
            path.write_bytes(body)
            answer = client.get(f"/api/ingest/{sha}/solution")
            assert (answer.status_code, answer.json()) == (409, {"detail": ingest_router.UNREADABLE_SOLUTION}), kind
            assert path.read_bytes() == body, kind
            assert SOLUTION_SENTINEL not in answer.text and str(path) not in answer.text, kind
        path.unlink()
        outside = tmp_path / "outside-valid-solution.json"
        outside.write_text(json.dumps(_solution_for(sha)), encoding="utf-8")
        path.symlink_to(outside)
        answer = client.get(f"/api/ingest/{sha}/solution")
        assert (answer.status_code, answer.json()) == (409, {"detail": ingest_router.UNREADABLE_SOLUTION}), "symlink"
        path.unlink()
        path.mkdir()
        answer = client.get(f"/api/ingest/{sha}/solution")
        assert (answer.status_code, answer.json()) == (409, {"detail": ingest_router.UNREADABLE_SOLUTION}), "folder"
        path.rmdir()
    assert SOLUTION_SENTINEL not in caplog.text and str(path) not in caplog.text
    valid_for_sha = _solution_for(sha)
    path.write_text(json.dumps(valid_for_sha), encoding="utf-8")
    answer = client.get(f"/api/ingest/{sha}/solution")
    assert (answer.status_code, answer.json()) == (200, valid_for_sha)


def test_no_read_or_lock_waits_on_a_fifo_named_like_a_store_file(tree: Any) -> None:
    """A FIFO named like a record, today's job.json or solution.json, or a lock is refused at once as no regular file,
    never waited on and never read as an empty file: each call runs in a thread given 10 s, and one still waiting fails
    by an assertion."""
    import errno
    import threading

    _, _, jobs = tree
    folder = jobs.upload_folder(SHAS[0])
    (folder / "jobs").mkdir()
    job_id = f"job_{SHAS[0][:16]}{1:016x}"
    os.mkfifo(folder / "jobs" / f"{job_id}.json")
    os.mkfifo(folder / "job.json")
    os.mkfifo(folder / "solution.json")
    os.mkfifo(jobs.lock_path(SHAS[0], "solve"))
    calls = {"read_job": lambda: jobs.read_job(job_id), "legacy": lambda: jobs.legacy_solve_job(SHAS[0]),
             "solution": lambda: jobs.read_solution(SHAS[0]),
             "is_held": lambda: jobs.is_held(jobs.lock_path(SHAS[0], "solve")),
             "try_hold": lambda: jobs.try_hold(jobs.lock_path(SHAS[0], "solve")),
             "jobs_of": lambda: jobs.jobs_of(SHAS[0]), "sweep": lambda: jobs.sweep(now=NOW)}
    # An OSError is named with its errno: EINVAL is the store's own "not a regular file", raised before any read or lock
    no_regular_file = f"OSError {errno.EINVAL}"
    expected = {"read_job": "Unreadable", "legacy": "Unreadable", "solution": "Unreadable", "is_held": False,
                "try_hold": no_regular_file, "jobs_of": []}
    for name, call in calls.items():
        out: dict[str, Any] = {}

        def run() -> None:
            try:
                out["value"] = call()
            except OSError as error:
                out["value"] = f"{type(error).__name__} {error.errno}"
            except Exception as error:  # noqa: BLE001 - what it raises is the answer
                out["value"] = type(error).__name__

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        runner.join(10)
        assert not runner.is_alive(), f"{name} waited on a FIFO"
        if name != "sweep":
            assert out["value"] == expected[name], (name, out)


def test_a_held_queued_jobs_partial_is_kept(tree: Any) -> None:
    _, uploads, jobs = tree
    made = record(SHAS[0], "solve", "queued", 1)
    jobs.write_record(made)
    holder = JobStore(uploads)
    assert holder.try_hold(holder.lock_of(made))
    try:
        partial = jobs.partial(made["id"], "solution")
        partial.write_text("{}", encoding="utf-8")
        counts, handed = jobs.sweep(now=NOW)
        assert partial.exists() and handed == [] and counts.partials_removed == 0
    finally:
        holder.release(holder.lock_of(made))


def test_a_sweep_of_a_store_with_no_root_does_nothing(tmp_path: Path) -> None:
    jobs = JobStore(IngestStore(tmp_path / "never made", max_bytes=1000))
    job_id = f"job_{SHAS[0][:16]}{1:016x}"
    assert jobs.read_job(job_id) is None
    with pytest.raises(FileNotFoundError, match="no job store root"):
        jobs.partial(job_id, "x")
    try:
        result: Any = jobs.sweep(now=NOW)
    except OSError as error:
        result = type(error).__name__
    assert result == (SweepCounts(0, 0, 0, 0, 0, 0), []) and not (tmp_path / "never made").exists()
    real_root = tmp_path / "swapped-root"
    real_root.mkdir()
    swapped = JobStore(IngestStore(real_root, max_bytes=1000))
    target_root = tmp_path / "target-root"
    make_upload(target_root, SHAS[0])
    real_root.rmdir()
    real_root.symlink_to(target_root, target_is_directory=True)
    assert swapped.read_job(job_id) is None
    with pytest.raises(FileNotFoundError, match="no job store root"):
        swapped.partial(job_id, "x")


def test_a_lock_inside_a_linked_jobs_folder_is_held_by_no_one(tree: Any) -> None:
    tmp, uploads, jobs = tree
    outside = tmp / "outside-jobs"
    outside.mkdir()
    holder = JobStore(uploads)
    fd = os.open(outside / "solve.lock", os.O_RDWR | os.O_CREAT, 0o600)
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        (jobs.upload_folder(SHAS[0]) / "jobs").symlink_to(outside, target_is_directory=True)
        assert not jobs.is_held(jobs.lock_path(SHAS[0], "solve"))
        (jobs.upload_folder(SHAS[0]) / "jobs").unlink()
        (jobs.upload_folder(SHAS[0]) / "jobs").mkdir()
        folder_lock = jobs.lock_path(SHAS[0], "restoration")
        folder_lock.mkdir()
        dfd = os.open(folder_lock, os.O_RDONLY)
        fcntl.flock(dfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert not jobs.is_held(folder_lock), "a folder named like a lock is no lock"
        finally:
            os.close(dfd)
    finally:
        os.close(fd)
    assert holder.try_hold(jobs.lock_path(SHAS[0], "solve"))
    holder.release(jobs.lock_path(SHAS[0], "solve"))


def test_todays_job_by_id_is_found_only_in_its_own_upload_folder(tree: Any) -> None:
    _, _, jobs = tree
    prefix = SHAS[1][:16]
    bucket = jobs.root / SHAS[1][:2]
    bucket.mkdir(exist_ok=True)

    def found() -> Any:
        try:
            return jobs.legacy_solve_job_by_id(f"job_{prefix}")
        except Exception as error:  # noqa: BLE001 - a crash is a wrong answer here
            return type(error).__name__

    # decoys: the prefix inside a hash (in another bucket, and in this bucket past the name's start), a hash with a
    # tail, a name that is no hash, all holding a matching job
    for name in ("0" + SHAS[1][:2] + prefix + "0" * 45, SHAS[1][:2] + prefix + "0" * 46, prefix + "e" * 48 + "0",
                 prefix + "-notes"):
        decoy = bucket / name
        decoy.mkdir()
        (decoy / "job.json").write_text(json.dumps(_legacy_job(SHAS[1])), encoding="utf-8")
    assert found() is None
    # the upload's own folder, whose job.json is another job's: refused as Unreadable, never silently absent
    own = make_upload(jobs.root, SHAS[1])
    (own / "job.json").write_text(json.dumps({**_legacy_job(SHAS[1]), "id": f"job_{'d' * 16}"}), encoding="utf-8")
    assert found() == "Unreadable"
    (own / "job.json").write_text(json.dumps(_legacy_job(SHAS[1])), encoding="utf-8")
    assert found() == _legacy_job(SHAS[1])


# ---- v2 findings: two-process races, misfiled records, wire names, path containment, mode 000, UTF-8, links ----


def _progress_record(sha: str, n: int, step: int) -> dict[str, Any]:
    made = record(sha, "solve", "running", n)
    made["stages"] = [{"stage": "store", "unit": "bytes", "done": 100, "total": 100},
                      {"stage": "decode", "unit": "frames", "done": step, "total": max(1, step)}]
    return made


def _race_end_vs_running(root: str, sha: str, role: str, trials: int, barrier: Any, out: Any) -> None:
    _, jobs = stores(Path(root))
    results: list[str] = []
    for trial in range(trials):
        job_num = 5000 + trial
        payload = record(sha, "solve", "succeeded", job_num) if role == "end" else _progress_record(sha, job_num, 99)
        barrier.wait()
        try:
            jobs.write_record(payload)
            results.append("ok")
        except Ended:
            results.append("Ended")
        except Exception as error:  # noqa: BLE001
            results.append(f"{type(error).__name__}: {error}")
        barrier.wait()
    out.put((role, results))


def _holder_writes_while_swept(root: str, sha: str, ready: Any, done: Any, sweeper_done: Any, out: Any) -> None:
    _, jobs = stores(Path(root))
    assert jobs.try_hold(jobs.lock_path(sha, "solve"))
    try:
        jobs.write_record(_progress_record(sha, 6000, 0))
        ready.set()
        errors: list[str] = []
        for step in range(1, 120):
            try:
                jobs.write_record(_progress_record(sha, 6000, step))
            except Exception as error:  # noqa: BLE001
                errors.append(f"{type(error).__name__}: {error}")
        done.set()
        assert sweeper_done.wait(CHILD_BOUND_S)
        out.put(("holder", errors))
    finally:
        jobs.release(jobs.lock_path(sha, "solve"))


def _concurrent_sweeper(root: str, ready: Any, done: Any, sweeper_done: Any, out: Any) -> None:
    _, jobs = stores(Path(root))
    assert ready.wait(CHILD_BOUND_S)
    errors: list[str] = []
    interrupted = 0
    removed = 0
    while not done.is_set():
        try:
            counts, _ = jobs.sweep(now=NOW)
            interrupted += counts.interrupted
            removed += counts.scratch_removed
        except Exception as error:  # noqa: BLE001
            errors.append(f"{type(error).__name__}: {error}")
    sweeper_done.set()
    out.put(("sweeper", errors, interrupted, removed))


def _barrier_sweep(root: str, barrier: Any, out: Any) -> None:
    _, jobs = stores(Path(root))
    barrier.wait()
    try:
        counts, _ = jobs.sweep(now=NOW)
        out.put(("ok", counts.interrupted, counts.unreadable))
    except Exception as error:  # noqa: BLE001
        out.put(("err", type(error).__name__, str(error)))


def _barrier_app_start(root: str, barrier: Any, out: Any) -> None:
    uploads = IngestStore(Path(root), max_bytes=1000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    barrier.wait()
    try:
        with TestClient(app) as client:
            status = client.get(f"/api/ingest/{'0' * 64}").status_code
        out.put(("ok", status))
    except Exception as error:  # noqa: BLE001
        out.put(("err", type(error).__name__, str(error)))


def test_two_writers_on_one_job_never_replace_an_ended_record(tree: Any) -> None:
    _, _, jobs = tree
    trials = 80
    for trial in range(trials):
        jobs.write_record(_progress_record(SHAS[0], 5000 + trial, 0))
    ctx = multiprocessing.get_context("spawn")
    barrier, out = ctx.Barrier(2, timeout=CHILD_BOUND_S), ctx.Queue()
    procs = [_child(_race_end_vs_running, str(jobs.root), SHAS[0], role, trials, barrier, out) for role in ("end", "run")]
    got = dict(out.get(timeout=CHILD_BOUND_S) for _ in procs)
    for proc in procs:
        assert _ended(proc, "race writer") == 0
    for trial in range(trials):
        assert got["end"][trial] == "ok"
        assert got["run"][trial] in ("ok", "Ended"), got["run"][trial]
        stored = jobs.read_job(f"job_{SHAS[0][:16]}{5000 + trial:016x}")
        assert stored is not None and stored["state"] == "succeeded", (trial, got["end"][trial], got["run"][trial], stored)
        assert stat.S_IMODE(jobs.record_path(stored["id"]).stat().st_mode) == 0o400
    assert [p.name for p in (jobs.upload_folder(SHAS[0]) / "jobs").iterdir() if p.name.startswith(".")] == []


def test_a_sweep_never_removes_a_live_writers_scratch_and_concurrent_sweeps_or_starts_succeed(
        tmp_path: Path) -> None:
    _, jobs = stores(tmp_path / "live")
    make_upload(jobs.root, SHAS[0])
    held_scratch_seen: list[bool] = []
    real_replace = job_store.os.replace

    def spy_replace(src: Any, dst: Any) -> None:
        held_scratch_seen.append(JobStore(IngestStore(jobs.root, max_bytes=1000)).is_held(Path(src)))
        real_replace(src, dst)

    job_store.os.replace = spy_replace  # type: ignore[assignment]
    try:
        jobs.write_record(_progress_record(SHAS[0], 5999, 0))
    finally:
        job_store.os.replace = real_replace  # type: ignore[assignment]
    assert held_scratch_seen == [True]
    import fcntl

    scratch_path = jobs.upload_folder(SHAS[0]) / "jobs" / ".job-0123456789abcdef.tmp"
    scratch_fd = os.open(scratch_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(scratch_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        counts_locked, _ = jobs.sweep(now=NOW)
        assert counts_locked.scratch_removed == 0 and scratch_path.exists()
    finally:
        fcntl.flock(scratch_fd, fcntl.LOCK_UN)
        os.close(scratch_fd)
    counts_unlocked, _ = jobs.sweep(now=NOW)
    assert counts_unlocked.scratch_removed == 1 and not scratch_path.exists()

    ctx = multiprocessing.get_context("spawn")
    ready, done, sweeper_done, out = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
    procs = [_child(_holder_writes_while_swept, str(jobs.root), SHAS[0], ready, done, sweeper_done, out),
             _child(_concurrent_sweeper, str(jobs.root), ready, done, sweeper_done, out)]
    results = {item[0]: item[1:] for item in (out.get(timeout=CHILD_BOUND_S) for _ in procs)}
    for proc in procs:
        assert _ended(proc, "live writer or sweeper") == 0
    assert results["holder"] == ([],)
    assert results["sweeper"] == ([], 0, 0)
    final = jobs.read_job(f"job_{SHAS[0][:16]}{6000:016x}")
    assert final is not None and final["state"] == "running" and final["stages"][-1]["done"] == 119

    _, jobs_e = stores(tmp_path / "two-sweeps")
    make_upload(jobs_e.root, SHAS[0])
    make_upload(jobs_e.root, SHAS[1])
    for n in range(30):
        jobs_e.write_record(_progress_record(SHAS[n % 2], 7000 + n, 1))
    barrier_e, out_e = ctx.Barrier(2, timeout=CHILD_BOUND_S), ctx.Queue()
    sweepers = [_child(_barrier_sweep, str(jobs_e.root), barrier_e, out_e) for _ in range(2)]
    sweep_outcomes = [out_e.get(timeout=CHILD_BOUND_S) for _ in sweepers]
    for proc in sweepers:
        assert _ended(proc, "concurrent sweeper") == 0
    assert all(item[0] == "ok" and item[2] == 0 for item in sweep_outcomes), sweep_outcomes
    assert sum(item[1] for item in sweep_outcomes) == 30
    assert all(jobs_e.read_job(f"job_{SHAS[n % 2][:16]}{7000 + n:016x}")["state"] == "interrupted" for n in range(30))

    _, jobs_f = stores(tmp_path / "two-apps")
    make_upload(jobs_f.root, SHAS[0])
    make_upload(jobs_f.root, SHAS[1])
    for n in range(20):
        jobs_f.write_record(_progress_record(SHAS[n % 2], 8000 + n, 1))
    barrier_f, out_f = ctx.Barrier(2, timeout=CHILD_BOUND_S), ctx.Queue()
    starters = [_child(_barrier_app_start, str(jobs_f.root), barrier_f, out_f) for _ in range(2)]
    start_outcomes = [out_f.get(timeout=CHILD_BOUND_S) for _ in starters]
    for proc in starters:
        assert _ended(proc, "concurrent app starter") == 0
    assert start_outcomes == [("ok", 404), ("ok", 404)]


def _tiny_png(gray: int) -> bytes:
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (4, 4), color=gray).save(buf, format="PNG")
    return buf.getvalue()


def test_misfiled_records_and_python_named_legacy_keys_are_refused_everywhere(
        app_and_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, jobs = app_and_store
    client = TestClient(app)
    png_a, png_b = _tiny_png(10), _tiny_png(200)
    sha_a = client.post("/api/ingest", content=png_a, headers={"content-type": "image/png"}).json()["sourceAsset"]["sha256"]
    sha_b = client.post("/api/ingest", content=png_b, headers={"content-type": "image/png"}).json()["sourceAsset"]["sha256"]
    ran = fake_solve_kind(monkeypatch)

    own_1 = record(sha_a, "solve", "running", 1)
    own_2 = record(sha_a, "solve", "succeeded", 2)
    other_id_same_sha = record(sha_a, "solve", "running", 3)
    wrong_sha_same_id = {**own_1, "sourceAssetSha256": sha_b}
    foreign_record = record(sha_b, "solve", "running", 4)
    jobs.write_record(own_1)
    jobs.write_record(own_2)
    path_1 = jobs.record_path(own_1["id"])
    assert path_1 is not None

    for misfiled in (other_id_same_sha, wrong_sha_same_id, foreign_record):
        path_1.write_text(json.dumps(misfiled), encoding="utf-8")
        with pytest.raises(Unreadable):
            jobs.read_record(path_1)
        with pytest.raises(Unreadable):
            jobs.read_job(own_1["id"])
        assert [r["id"] for r in jobs.jobs_of(sha_a)] == [own_2["id"]]
        with pytest.raises(Ended):
            jobs.write_record(_progress_record(sha_a, 1, 5))
        assert json.loads(path_1.read_text(encoding="utf-8")) == misfiled
        answer = client.get(f"/api/ingest/jobs/{own_1['id']}")
        assert (answer.status_code, answer.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})

    wrong_prefix_id = f"job_{sha_b[:16]}{1:016x}"
    wrong_prefix_path = jobs.upload_folder(sha_a) / "jobs" / f"{wrong_prefix_id}.json"
    wrong_prefix_path.write_text(json.dumps({**own_1, "id": wrong_prefix_id}), encoding="utf-8")
    with pytest.raises(Unreadable):
        jobs.read_record(wrong_prefix_path)
    assert [r["id"] for r in jobs.jobs_of(sha_a)] == [own_2["id"]]
    wrong_prefix_path.unlink()

    folder_a = jobs.upload_folder(sha_a)
    (folder_a / "solution.json").write_text("{}", encoding="utf-8")
    valid_legacy_a = {**_legacy_job(sha_a), "state": "succeeded", "startedAtUTC": "2030-01-01T05:00:01Z",
                      "finishedAtUTC": "2030-01-01T05:00:02Z", "solutionId": f"sol_{sha_a[:16]}"}
    foreign_legacy_b = {**_legacy_job(sha_b), "state": "succeeded", "startedAtUTC": "2030-01-01T05:00:01Z",
                        "finishedAtUTC": "2030-01-01T05:00:02Z", "solutionId": f"sol_{sha_b[:16]}"}
    wrong_id_legacy = {**valid_legacy_a, "id": f"job_{'d' * 16}"}
    snake_legacy = {k: v for k, v in valid_legacy_a.items() if k != "sourceAssetSha256"}
    snake_legacy["source_asset_sha256"] = sha_a
    snake_time_legacy = {k: v for k, v in valid_legacy_a.items() if k != "createdAtUTC"}
    snake_time_legacy["created_at_utc"] = valid_legacy_a["createdAtUTC"]
    snake_sol_legacy = {k: v for k, v in valid_legacy_a.items() if k != "solutionId"}
    snake_sol_legacy["solution_id"] = valid_legacy_a["solutionId"]

    raw_bad_legacies = [json.dumps(b) for b in (foreign_legacy_b, wrong_id_legacy, snake_legacy, snake_time_legacy, snake_sol_legacy)] + [
        '{"extra": NaN, ' + json.dumps(valid_legacy_a)[1:],
        '{"extra": Infinity, ' + json.dumps(valid_legacy_a)[1:],
        '{"extra": 1e500, ' + json.dumps(valid_legacy_a)[1:],
        '{"extra": ' + "9" * 400 + ", " + json.dumps(valid_legacy_a)[1:],
    ]

    for bad_legacy_text in raw_bad_legacies:
        (folder_a / "job.json").write_text(bad_legacy_text, encoding="utf-8")
        with pytest.raises(Unreadable):
            jobs.legacy_solve_job(sha_a)
        with pytest.raises(Unreadable):
            jobs.legacy_solve_job_by_id(f"job_{sha_a[:16]}")
        by_id = client.get(f"/api/ingest/jobs/job_{sha_a[:16]}")
        assert (by_id.status_code, by_id.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})
        # The receipt names the newest readable solve attempt: own_2, whatever job.json holds
        receipt = client.get(f"/api/ingest/{sha_a}")
        assert receipt.status_code == 200 and receipt.json()["solveJobId"] == own_2["id"]
        reupload = client.post("/api/ingest", content=png_a, headers={"content-type": "image/png"})
        assert reupload.status_code == 200 and reupload.json()["solveJobId"] == own_2["id"]
        ran.clear()
        assert _solved(client, sha_a)[0] == 202 and ran == [sha_a]


def test_partial_and_publish_refuse_traversal_outside_paths_and_the_upload_folder_itself(tree: Any) -> None:
    tmp, _, jobs = tree
    make_upload(jobs.root, SHAS[1])
    valid_id = f"job_{SHAS[0][:16]}{1:016x}"
    other_id = f"job_{SHAS[0][:16]}{2:016x}"
    before_top = sorted(p.name for p in tmp.iterdir())
    for bad_id, bad_name in (
        (f"../../escape/{valid_id}", "x"),
        (f"../../{valid_id}", "x"),
        (f"/abs/{valid_id}", "x"),
        (valid_id, "../x"),
        (valid_id, "a/b"),
        (valid_id + "\x00", "x"),
    ):
        with pytest.raises(ValueError):
            jobs.partial(bad_id, bad_name)
    for bad_folder in (
        jobs.root,
        jobs.root / SHAS[0][:2] / ".." / ".." / "escape",
        tmp / "outside-dir",
        tmp / "outside-dir" / "deep" / "sub",
        Path("relative/folder"),
    ):
        with pytest.raises(ValueError):
            jobs._enter(bad_folder, make=True)
    assert sorted(p.name for p in tmp.iterdir()) == before_top

    other_bucket = "00" if SHAS[0][:2] != "00" else "ff"
    for bad_rec_path in (
        jobs.upload_folder(SHAS[0]) / f"{valid_id}.json",
        jobs.upload_folder(SHAS[0]) / "jobs" / "sub" / f"{valid_id}.json",
        jobs.root / SHAS[0][:3] / SHAS[0] / "jobs" / f"{valid_id}.json",
        jobs.root / SHAS[0][:2] / (SHAS[0] + "0") / "jobs" / f"{valid_id}.json",
        jobs.root / other_bucket / SHAS[0] / "jobs" / f"{valid_id}.json",
        jobs.upload_folder(SHAS[0]) / "solutions" / f"{valid_id}.json",
        jobs.upload_folder(SHAS[0]) / "jobs" / f"{valid_id}.json.bak",
    ):
        with pytest.raises(ValueError):
            jobs.read_record(bad_rec_path)

    assert job_store._finite_json_float("1.5") == 1.5
    with pytest.raises(ValueError):
        job_store._finite_json_float("1e500")

    good = jobs.partial(valid_id, "out")
    good.write_bytes(b"partial-bytes")
    upload_0 = jobs.upload_folder(SHAS[0])
    upload_1 = jobs.upload_folder(SHAS[1])
    upload_before = snapshot(upload_0)
    bucket_stray = jobs.root / SHAS[0][:2] / f"{valid_id}-out.part"
    bucket_stray.write_bytes(b"stray")
    for bad_partial in (
        jobs.root / ".partial" / "sub" / f"{valid_id}-out.part",
        jobs.root / "other" / f"{valid_id}-out.part",
        bucket_stray,
    ):
        with pytest.raises(ValueError):
            jobs.publish(bad_partial, upload_0 / "solutions" / f"{valid_id}.json")
    assert bucket_stray.read_bytes() == b"stray"
    bucket_stray.unlink()
    for bad_target in (
        jobs.root / SHAS[0][:2] / ".." / ".." / "published.txt",
        tmp / "outside-published.txt",
        Path("relative-published.txt"),
        upload_0 / "solution.json",
        upload_0 / "solutions" / "sub" / f"{valid_id}.json",
        jobs.root / SHAS[0][:3] / SHAS[0] / "solutions" / f"{valid_id}.json",
        jobs.root / SHAS[0][:2] / (SHAS[0] + "0") / "solutions" / f"{valid_id}.json",
        jobs.root / other_bucket / SHAS[0] / "solutions" / f"{valid_id}.json",
        upload_0 / "original.jpg",
        upload_0 / "record.json",
        upload_0 / "jobs" / f"{valid_id}.json",
        upload_0 / "other" / f"{valid_id}.json",
        upload_1 / "solutions" / f"{valid_id}.json",
        upload_0 / "solutions" / f"{other_id}.json",
    ):
        with pytest.raises(ValueError):
            jobs.publish(good, bad_target)
    assert good.read_bytes() == b"partial-bytes"
    assert snapshot(upload_0) == upload_before
    assert sorted(p.name for p in tmp.iterdir()) == before_top

    outside_partials = tmp / "outside-partials"
    outside_partials.mkdir()
    (outside_partials / f"{valid_id}-out.part").write_bytes(b"outside-partial")
    good.unlink()
    (jobs.root / ".partial").rmdir()
    (jobs.root / ".partial").symlink_to(outside_partials, target_is_directory=True)
    with pytest.raises(ValueError):
        jobs.publish(jobs.root / ".partial" / f"{valid_id}-out.part", upload_0 / "solutions" / f"{valid_id}.json")
    (jobs.root / ".partial").unlink()


def test_unreadable_buckets_and_non_utf8_records_answer_409_and_never_500(
        app_and_store: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    app, _, jobs = app_and_store
    client = TestClient(app, raise_server_exceptions=False)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
                        "0000000a49444154789c63600000000200015e2fe9e30000000049454e44ae426082")
    sha = client.post("/api/ingest", content=png, headers={"content-type": "image/png"}).json()["sourceAsset"]["sha256"]
    ran = fake_solve_kind(monkeypatch)

    made = record(sha, "solve", "failed", 1)
    jobs.write_record(made)
    rec_path = jobs.record_path(made["id"])
    assert rec_path is not None
    folder = jobs.upload_folder(sha)
    legacy_id = f"job_{sha[:16]}"
    (folder / "job.json").write_text(json.dumps(_legacy_job(sha)), encoding="utf-8")

    # Non-UTF-8 bytes in jobs/<id>.json and in job.json across every route
    os.chmod(rec_path, 0o600)
    rec_path.write_bytes(b"\xff\xfe{}")
    (folder / "job.json").write_bytes(b"\xff\xfe{}")
    with pytest.raises(Unreadable):
        jobs.read_record(rec_path)
    with pytest.raises(Unreadable):
        jobs.read_job(made["id"])
    with pytest.raises(Unreadable):
        jobs.legacy_solve_job(sha)
    with pytest.raises(Unreadable):
        jobs.legacy_solve_job_by_id(legacy_id)
    assert jobs.jobs_of(sha) == []
    counts, _ = jobs.sweep(now=NOW)
    assert counts.unreadable == 1

    with caplog.at_level(logging.DEBUG):
        for job_id in (made["id"], legacy_id):
            resp = client.get(f"/api/ingest/jobs/{job_id}")
            assert (resp.status_code, resp.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})
            assert str(jobs.root) not in resp.text and "Traceback" not in resp.text
        receipt = client.get(f"/api/ingest/{sha}")
        assert receipt.status_code == 200 and "solveJobId" not in receipt.json()
        reupload = client.post("/api/ingest", content=png, headers={"content-type": "image/png"})
        assert reupload.status_code == 200 and "solveJobId" not in reupload.json()
        assert _solved(client, sha)[0] == 202 and ran == [sha]
    assert str(jobs.root) not in caplog.text

    # Mode-000 jobs/ folder on jobs_of, and mode-000 bucket on both id lengths
    os.chmod(folder / "jobs", 0o000)
    try:
        assert jobs.jobs_of(sha) == []
    finally:
        os.chmod(folder / "jobs", 0o700)

    bucket = folder.parent
    os.chmod(bucket, 0o000)
    try:
        with pytest.raises(Unreadable):
            jobs.record_path(made["id"])
        with pytest.raises(Unreadable):
            jobs.read_job(made["id"])
        with pytest.raises(Unreadable):
            jobs.legacy_solve_job_by_id(legacy_id)
        for job_id in (made["id"], legacy_id):
            resp = client.get(f"/api/ingest/jobs/{job_id}")
            assert (resp.status_code, resp.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})
            assert str(jobs.root) not in resp.text and "Traceback" not in resp.text
    finally:
        os.chmod(bucket, 0o700)


def test_self_loop_links_and_a_linked_unreadable_bucket_are_never_followed(tree: Any) -> None:
    tmp, _, jobs = tree
    outside_unreadable = tmp / "outside-mode-000"
    outside_unreadable.mkdir()
    os.chmod(outside_unreadable, 0o000)
    try:
        (jobs.root / "ee").symlink_to(outside_unreadable, target_is_directory=True)
        loop_bucket = jobs.root / "ff"
        loop_bucket.symlink_to(loop_bucket.name)

        prefix = SHAS[0][:16]
        bucket_0 = jobs.root / SHAS[0][:2]
        loop_upload = bucket_0 / (prefix + "f" * 48)
        loop_upload.symlink_to(loop_upload.name)

        folder_0 = jobs.upload_folder(SHAS[0])
        (folder_0 / "jobs").mkdir(exist_ok=True)
        (folder_0 / "solutions").mkdir(exist_ok=True)
        (jobs.root / ".partial").mkdir(exist_ok=True)
        job_id = f"job_{prefix}{99:016x}"
        for loop_file in (
            folder_0 / "jobs" / f"{job_id}.json",
            folder_0 / "solutions" / f"{job_id}.json",
            jobs.root / ".partial" / f"{job_id}-out.part",
        ):
            loop_file.symlink_to(loop_file.name)

        with pytest.raises(Unreadable):
            jobs.read_job(job_id)
        assert jobs.read_job(f"job_{prefix}{98:016x}") is None
        assert jobs.jobs_of(SHAS[0]) == []
        assert jobs.legacy_solve_job_by_id(f"job_{prefix}") is None
        assert jobs.sweep(now=NOW) == (SweepCounts(0, 0, 0, 0, 0, 0), [])
    finally:
        os.chmod(outside_unreadable, 0o700)


def test_a_start_runs_the_solve_again_unless_a_checked_job_succeeded_with_its_solution(
        app_and_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app, uploads, jobs = app_and_store
    client = TestClient(app)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e9b55"
                        "0000000a49444154789c63600000000200015e2fe9e30000000049454e44ae426082")
    stored = client.post("/api/ingest", content=png, headers={"content-type": "image/png"})
    assert stored.status_code == 201, stored.text
    sha = stored.json()["sourceAsset"]["sha256"]
    folder = jobs.upload_folder(sha)
    original_path = uploads.original(sha)
    assert original_path is not None
    original_before = original_path.read_bytes()
    record_before = (folder / "record.json").read_bytes()
    valid_sol = {**_solution_for(sha), "id": f"sol_{sha[:16]}"}
    # Today's records are older than any job of the runner's
    succeeded = {**_legacy_job(sha), "createdAtUTC": "2020-01-01T05:00:00Z", "state": "succeeded",
                 "startedAtUTC": "2020-01-01T05:00:01Z", "finishedAtUTC": "2020-01-01T05:00:02Z",
                 "solutionId": f"sol_{sha[:16]}"}
    write_new_solution = [False]

    def publish(ctx: Any) -> Any:
        if not write_new_solution[0]:
            raise JobRefusal("The decoder could not read the file.")
        partial = ctx.partial("solution.json")
        partial.write_text(json.dumps(valid_sol), encoding="utf-8")
        ctx.publish(partial, ctx.solution_path())
        return KindResult("succeeded", {"solutionId": valid_sol["id"]})

    ran = fake_solve_kind(monkeypatch, publish)
    (folder / "job.json").write_text(json.dumps(succeeded), encoding="utf-8")
    assert _solved(client, sha)[0] == 202 and ran == [sha], "no solution: run again"
    ingest_jobs._write_json(
        folder,
        "solution.json",
        _solution_for(sha, _refused_sample("audit_j_observed_relabelled_catalog_matched_its_evidence_still_observed")),
        read_only=True,
    )
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 409, "the newest succeeded solve's is refused"
    write_new_solution[0] = True
    assert _solved(client, sha)[0] == 202 and ran == [sha, sha], "refused solution: run again"
    served = client.get(f"/api/ingest/{sha}/solution")
    assert (served.status_code, served.json()) == (200, valid_sol)
    current = jobs.newest_solve(sha)
    assert current["state"] == "succeeded" and current["kind"] == "solve"
    answer = client.post(f"/api/ingest/{sha}/solve")
    assert (answer.status_code, answer.json(), ran) == (200, current, [sha, sha])
    write_new_solution[0] = False
    (folder / "solutions" / f"{current['id']}.json").unlink()
    assert _solved(client, sha)[0] == 202 and ran == [sha] * 3, "result gone: run again"
    (folder / "job.json").write_text(json.dumps({**succeeded, "state": "finished"}), encoding="utf-8")
    assert _solved(client, sha)[0] == 202 and ran == [sha] * 4, "unreadable: run again"
    assert original_path.read_bytes() == original_before
    assert (folder / "record.json").read_bytes() == record_before


def test_a_json_dump_that_fails_in_todays_writer_leaves_no_scratch_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap design section 1 names in `_write_json`: a failure inside `json.dump` (a full disk) left its
    `.<name>-<hex>.tmp` behind, and nothing swept it."""
    folder = tmp_path / "upload"

    def full_disk(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ingest_jobs.json, "dump", full_disk)
    with pytest.raises(OSError):
        ingest_jobs._write_json(folder, "job.json", {"id": "x"})
    assert sorted(p.name for p in folder.iterdir()) == []


def test_an_uploads_records_are_ordered_by_their_instant_not_their_text(tree: Any) -> None:
    """"05:00:00.300Z" sorts before "05:00:00Z" as text, though it is 300 ms later."""
    _, _, jobs = tree
    later = {**record(SHAS[0], "solve", "queued", 1), "createdAtUTC": "2030-01-01T05:00:00.300Z"}
    earlier = {**record(SHAS[0], "solve", "queued", 2), "createdAtUTC": "2030-01-01T05:00:00Z"}
    jobs.write_record(later)
    jobs.write_record(earlier)
    assert [r["id"] for r in jobs.jobs_of(SHAS[0])] == [earlier["id"], later["id"]]


def test_a_solution_file_that_is_not_the_one_its_job_names_is_unreadable(tree: Any) -> None:
    _, _, jobs = tree
    made = record(SHAS[0], "solve", "succeeded", 1)
    made["solutionId"] = f"sol_{SHAS[0][:16]}"
    jobs.write_record(made)
    folder = jobs.upload_folder(SHAS[0]) / "solutions"
    folder.mkdir(mode=0o700)
    (folder / f"{made['id']}.json").write_text(json.dumps(_solution_for(SHAS[0])), encoding="utf-8")
    assert _solution_for(SHAS[0])["id"] != made["solutionId"]
    with pytest.raises(Unreadable):
        jobs.read_solution(SHAS[0])
    (folder / f"{made['id']}.json").unlink()
    (folder / f"{made['id']}.json").write_text(
        json.dumps({**_solution_for(SHAS[0]), "id": made["solutionId"]}), encoding="utf-8")
    assert jobs.read_solution(SHAS[0])["id"] == made["solutionId"]


# ---- platform-4c: the solution id the restoration's export manifest names (design 3.1) ----


def _succeeded_solve(jobs: JobStore, sha: str, n: int, created: str, *, with_solution: bool = True) -> dict[str, Any]:
    made = {**record(sha, "solve", "succeeded", n), "createdAtUTC": created, "startedAtUTC": created,
            "finishedAtUTC": created, "solutionId": f"sol_{sha[:16]}"}
    jobs.write_record(made)
    if with_solution:
        folder = jobs.upload_folder(sha) / "solutions"
        folder.mkdir(mode=0o700, exist_ok=True)
        (folder / f"{made['id']}.json").write_text(
            json.dumps({**_solution_for(sha), "id": f"sol_{sha[:16]}"}), encoding="utf-8")
    return made


def test_the_newest_solution_id_is_that_of_the_newest_succeeded_solve_whose_solution_reads(tree: Any) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    assert jobs.newest_solution_id(sha) is None, "no solve"
    jobs.write_record({**record(sha, "solve", "failed", 1), "createdAtUTC": "2020-01-01T05:00:00Z",
                       "startedAtUTC": "2020-01-01T05:00:00Z", "finishedAtUTC": "2020-01-01T05:00:01Z"})
    assert jobs.newest_solution_id(sha) is None, "a failed solve names no solution"
    older = _succeeded_solve(jobs, sha, 2, "2020-01-01T05:00:02Z")
    assert jobs.newest_solution_id(sha) == f"sol_{sha[:16]}"
    newer = _succeeded_solve(jobs, sha, 3, "2020-01-01T05:00:03Z", with_solution=False)
    assert jobs.newest_solution_id(sha) is None, "the newest succeeded solve's solution is missing: none is named"
    (jobs.upload_folder(sha) / "solutions" / f"{newer['id']}.json").write_text("{}", encoding="utf-8")
    assert jobs.newest_solution_id(sha) is None, "nor when the contract refuses it"
    assert older["id"] != newer["id"]


def test_a_solve_that_runs_does_not_hide_the_last_good_solution_id(tree: Any) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    _succeeded_solve(jobs, sha, 1, "2020-01-01T05:00:00Z")
    jobs.write_record({**record(sha, "solve", "running", 2), "createdAtUTC": "2020-01-01T05:00:05Z",
                       "startedAtUTC": "2020-01-01T05:00:05Z"})
    assert jobs.newest_solution_id(sha) == f"sol_{sha[:16]}"


def test_todays_succeeded_job_names_its_solution_and_a_solution_with_no_job_names_none(tree: Any) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    folder = jobs.upload_folder(sha)
    (folder / "solution.json").write_text(json.dumps({**_solution_for(sha), "id": f"sol_{sha[:16]}"}), encoding="utf-8")
    assert jobs.newest_solution_id(sha) is None, "today's solution.json alone is not a succeeded solve"
    succeeded = {**_legacy_job(sha), "createdAtUTC": "2020-01-01T05:00:00Z", "state": "succeeded",
                 "startedAtUTC": "2020-01-01T05:00:01Z", "finishedAtUTC": "2020-01-01T05:00:02Z",
                 "solutionId": f"sol_{sha[:16]}"}
    (folder / "job.json").write_text(json.dumps(succeeded), encoding="utf-8")
    assert jobs.newest_solution_id(sha) == f"sol_{sha[:16]}"
    assert jobs.newest_solution_id(SHAS[1]) is None, "an upload never stored names none, and nothing is made"
    assert not jobs.upload_folder(SHAS[1]).exists()


def _solved(client: TestClient, sha: str) -> tuple[int, dict]:
    """Starts the solve of `sha` as the web does and follows its job route until the job ends (the start no longer
    waits, upload jobs REQ-JOB-7.5): the start's status and the job as it ended."""
    started = client.post(f"/api/ingest/{sha}/solve")
    job = started.json()
    deadline = time.monotonic() + 300.0
    while isinstance(job, dict) and job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    return started.status_code, job


# ---- platform-or4: post-publish_variant crash window, unnamed variants and variant staging (REQ-JOB-5.4, 5.5) ----


def _media_record(sha: str, kind: str, state: str, n: int, suffix: str) -> dict[str, Any]:
    made: dict[str, Any] = {
        "id": f"job_{sha[:16]}{n:016x}",
        "sourceAssetSha256": sha,
        "kind": kind,
        "state": state,
        "createdAtUTC": "2030-01-01T04:59:00Z",
        "request": {"kind": kind},
        "graphId": f"graph_{suffix}",
        "stages": [],
    }
    if state != "queued":
        made["startedAtUTC"] = "2030-01-01T04:59:01Z"
        made["producer"] = {"name": "albedian.restore", "revision": 1}
    if state in ("succeeded", "rejected", "failed", "canceled", "interrupted"):
        made["finishedAtUTC"] = "2030-01-01T04:59:02Z"
    if state == "succeeded":
        made["variantId"] = f"var_{suffix}"
        made["attemptId"] = f"att_{suffix}"
    elif state == "rejected":
        made["attemptId"] = f"att_{suffix}"
    elif state == "failed":
        made["error"] = "The restoration stopped on an internal error."
    elif state == "canceled" and "startedAtUTC" in made:
        made["cancelRequestedAtUTC"] = "2030-01-01T04:59:01Z"
    elif state == "interrupted":
        made["error"] = INTERRUPTED_SENTENCE
    UploadJob.model_validate(made)
    return made


def _write_variant_dir(variants_root: Path, variant_id: str, *, media_file: str = "variant.png") -> Path:
    variants_root.mkdir(mode=0o700, exist_ok=True)
    target = variants_root / variant_id
    target.mkdir(mode=0o700)
    for name, body in (
        (media_file, b"\x89PNG\r\n\x1a\nvariant"),
        ("variant.json", json.dumps({"id": variant_id}).encode("utf-8")),
        ("graph.json", b"{}\n"),
        ("attempt.json", b"{}\n"),
    ):
        (target / name).write_bytes(body)
        os.chmod(target / name, 0o400)
    return target


def test_succeeded_variant_ids_names_only_readable_succeeded_media_jobs(tree: Any) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    assert jobs.succeeded_variant_ids(sha) == set()
    assert jobs.succeeded_variant_ids(SHAS[1]) == set()
    assert not jobs.upload_folder(SHAS[1]).exists()
    with pytest.raises(ValueError):
        jobs.succeeded_variant_ids("not-a-sha256")

    jobs.write_record(record(sha, "solve", "succeeded", 1))
    for idx, state in enumerate(("queued", "running", "rejected", "failed", "canceled", "interrupted"), start=2):
        jobs.write_record(_media_record(sha, "restoration", state, idx, f"{idx:016x}"))
    assert jobs.succeeded_variant_ids(sha) == set()

    copy_ok = _media_record(sha, "display_copy", "succeeded", 20, "1" * 16)
    rest_ok = _media_record(sha, "restoration", "succeeded", 21, "2" * 16)
    jobs.write_record(copy_ok)
    jobs.write_record(rest_ok)
    assert jobs.succeeded_variant_ids(sha) == {"var_" + "1" * 16, "var_" + "2" * 16}

    rest_path = jobs.record_path(rest_ok["id"])
    assert rest_path is not None
    os.chmod(rest_path, 0o600)
    rest_path.write_text("{corrupt", encoding="utf-8")
    assert jobs.succeeded_variant_ids(sha) == {"var_" + "1" * 16}


def test_sweep_removes_unnamed_variants_and_unheld_variant_staging_while_keeping_named_held_and_decoys(
    tree: Any,
) -> None:
    tmp, uploads, jobs = tree
    sha = SHAS[0]
    folder = jobs.upload_folder(sha)
    outside = tmp / "outside-variants"
    outside.mkdir()
    (outside / "kept.png").write_bytes(b"outside")
    outside_dir = outside / "dir"
    outside_dir.mkdir()
    (outside_dir / "variant.png").write_bytes(b"outside-dir")

    holder = JobStore(IngestStore(jobs.root, max_bytes=1000))
    named_job = _media_record(sha, "display_copy", "succeeded", 1, "1" * 16)
    crashed_job = _media_record(sha, "restoration", "running", 2, "2" * 16)
    held_job = _media_record(sha, "restoration", "running", 3, "3" * 16)
    jobs.write_record(named_job)
    jobs.write_record(crashed_job)
    jobs.write_record(held_job)
    assert holder.try_hold(holder.lock_of(held_job))
    lock_only = holder.lock_path(sha, "display_copy", "graph_" + "4" * 16)
    assert holder.try_hold(lock_only)

    variants_dir = folder / "variants"
    named_dir = _write_variant_dir(variants_dir, "var_" + "1" * 16)
    crashed_dir = _write_variant_dir(variants_dir, "var_" + "2" * 16, media_file="variant.mov")
    held_rec_dir = _write_variant_dir(variants_dir, "var_" + "3" * 16)
    held_lock_dir = _write_variant_dir(variants_dir, "var_" + "4" * 16)
    orphan_dir = _write_variant_dir(variants_dir, "var_" + "5" * 16, media_file="variant.mp4")

    # Decoys inside variants/: symlinked variant dir, variant dir holding a symlink, foreign file, subdir, aside file
    link_var = variants_dir / ("var_" + "a" * 16)
    link_var.symlink_to(outside_dir, target_is_directory=True)
    with_link = _write_variant_dir(variants_dir, "var_" + "b" * 16)
    os.chmod(with_link / "variant.png", 0o600)
    (with_link / "variant.png").unlink()
    (with_link / "variant.png").symlink_to(outside / "kept.png")
    with_foreign = _write_variant_dir(variants_dir, "var_" + "c" * 16)
    (with_foreign / "notes.txt").write_bytes(b"decoy")
    with_subdir = variants_dir / ("var_" + "d" * 16)
    with_subdir.mkdir(mode=0o700)
    (with_subdir / "variant.png").mkdir(mode=0o700)
    aside_file = variants_dir / ".variant.png.aside"
    aside_file.write_bytes(b"aside")

    # Staging folders in .partial/: while a media lock is held, live staging is preserved
    partials = jobs.root / ".partial"
    partials.mkdir(mode=0o700, exist_ok=True)
    stage_full = partials / ("variant-" + "aa" * 8)
    _write_variant_dir(stage_full, "var_" + "9" * 16, media_file="variant.mp4")
    stage_empty = partials / ("variant-" + "bb" * 8)
    stage_empty.mkdir(mode=0o700)
    stage_link = partials / ("variant-" + "cc" * 8)
    stage_link.symlink_to(outside_dir, target_is_directory=True)
    stage_foreign_top = partials / ("variant-" + "dd" * 8)
    stage_foreign_top.mkdir(mode=0o700)
    (stage_foreign_top / "notes.txt").write_bytes(b"keep")
    stage_foreign_leaf = partials / ("variant-" + "ee" * 8)
    bad_assembled = _write_variant_dir(stage_foreign_leaf, "var_" + "8" * 16)
    (bad_assembled / "extra.txt").write_bytes(b"keep")

    try:
        counts, _ = jobs.sweep(now=NOW)
        assert (counts.interrupted, counts.results_removed, counts.partials_removed) == (1, 2, 0)
        assert named_dir.is_dir() and held_rec_dir.is_dir() and held_lock_dir.is_dir()
        assert not crashed_dir.exists() and not orphan_dir.exists()
        assert stage_full.is_dir() and stage_empty.is_dir(), "held media lock keeps live variant staging untouched"
    finally:
        holder.release(holder.lock_of(held_job))
        holder.release(lock_only)

    # Once media locks are released (and held_job finished without succeeding), staging and unheld variants are swept
    counts2, _ = jobs.sweep(now=NOW)
    assert (counts2.interrupted, counts2.results_removed, counts2.partials_removed) == (1, 2, 2)
    assert not held_rec_dir.exists() and not held_lock_dir.exists()
    assert not stage_full.exists() and not stage_empty.exists()
    assert named_dir.is_dir()
    # Every decoy and outside target is untouched
    assert link_var.is_symlink() and (outside_dir / "variant.png").read_bytes() == b"outside-dir"
    assert with_link.is_dir() and (outside / "kept.png").read_bytes() == b"outside"
    assert (with_foreign / "notes.txt").read_bytes() == b"decoy"
    assert (with_subdir / "variant.png").is_dir()
    assert aside_file.read_bytes() == b"aside"
    assert stage_link.is_symlink()
    assert (stage_foreign_top / "notes.txt").read_bytes() == b"keep"
    assert (bad_assembled / "extra.txt").read_bytes() == b"keep"

    # Linked variants/ directory is never entered by sweep
    for child in list(variants_dir.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            for sub in list(child.iterdir()):
                if sub.is_symlink() or sub.is_file():
                    sub.unlink()
                else:
                    sub.rmdir()
            child.rmdir()
    variants_dir.rmdir()
    outside_variants = outside / "linked-variants"
    _write_variant_dir(outside_variants, "var_" + "f" * 16)
    variants_dir.symlink_to(outside_variants, target_is_directory=True)
    counts3, _ = jobs.sweep(now=NOW)
    assert counts3.results_removed == 0 and (outside_variants / ("var_" + "f" * 16)).is_dir()


def test_interrupt_removes_uncommitted_media_variant_unless_succeeded_or_held(tree: Any) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    folder = jobs.upload_folder(sha)

    # Unheld running media job: interrupt marks interrupted and removes uncommitted variant
    r1 = _media_record(sha, "restoration", "running", 1, "1" * 16)
    jobs.write_record(r1)
    v1 = _write_variant_dir(folder / "variants", "var_" + "1" * 16)
    ended1 = jobs.interrupt(r1, now=NOW)
    assert ended1["state"] == "interrupted" and not v1.exists()

    # Earlier succeeded job already names var_22...: interrupt on a newer rerun keeps var_22...
    s2 = _media_record(sha, "display_copy", "succeeded", 2, "2" * 16)
    r2 = _media_record(sha, "display_copy", "running", 3, "2" * 16)
    jobs.write_record(s2)
    jobs.write_record(r2)
    v2 = _write_variant_dir(folder / "variants", "var_" + "2" * 16)
    ended2 = jobs.interrupt(r2, now=NOW)
    assert ended2["state"] == "interrupted" and v2.is_dir()

    # Record meanwhile succeeded on disk (Ended path): variant stays
    s3 = _media_record(sha, "restoration", "succeeded", 4, "3" * 16)
    r3_stale = _media_record(sha, "restoration", "running", 4, "3" * 16)
    jobs.write_record(s3)
    v3 = _write_variant_dir(folder / "variants", "var_" + "3" * 16)
    ended3 = jobs.interrupt(r3_stale, now=NOW)
    assert ended3["state"] == "succeeded" and v3.is_dir()

    # Another process holds the graph's lock when interrupt runs: variant stays
    r4 = _media_record(sha, "restoration", "running", 5, "4" * 16)
    jobs.write_record(r4)
    v4 = _write_variant_dir(folder / "variants", "var_" + "4" * 16)
    holder = JobStore(IngestStore(jobs.root, max_bytes=1000))
    assert holder.try_hold(holder.lock_of(r4))
    try:
        ended4 = jobs.interrupt(r4, now=NOW)
        assert ended4["state"] == "interrupted" and v4.is_dir()
    finally:
        holder.release(holder.lock_of(r4))


MEDIA_ATTEMPT_STEPS = ("queued", "running", "staging", "publish_variant", "keep_attempt", "succeeded")


def _media_attempt(root: str, sha: str, stop_after: int, inside_commit: bool) -> None:
    _, jobs = stores(Path(root))
    suffix = "7" * 16
    jobs.try_hold(jobs.lock_path(sha, "restoration", f"graph_{suffix}"))
    job_id = f"job_{sha[:16]}{1:016x}"
    folder = jobs.upload_folder(sha)
    stage_dir = jobs.root / ".partial" / ("variant-" + "ab" * 8)
    for step, name in enumerate(MEDIA_ATTEMPT_STEPS, start=1):
        if inside_commit and step == stop_after:
            job_store.os.replace = lambda *a, **kw: os._exit(0)  # type: ignore[assignment]
        if name in ("queued", "running", "succeeded"):
            jobs.write_record({**_media_record(sha, "restoration", name, 1, suffix), "id": job_id})
        elif name == "staging":
            (jobs.root / ".partial").mkdir(mode=0o700, exist_ok=True)
            _write_variant_dir(stage_dir, f"var_{suffix}")
        elif name == "publish_variant":
            (folder / "variants").mkdir(mode=0o700, exist_ok=True)
            os.rename(stage_dir / f"var_{suffix}", folder / "variants" / f"var_{suffix}")
            stage_dir.rmdir()
        elif name == "keep_attempt":
            attempts = folder / "attempts"
            attempts.mkdir(mode=0o700, exist_ok=True)
            att = attempts / f"att_{suffix}.json"
            att.write_bytes(b"{}\n")
            os.chmod(att, 0o400)
        if step == stop_after:
            os._exit(0)


@pytest.mark.parametrize(
    ("stop_after", "inside_commit"),
    [(n, False) for n in range(1, len(MEDIA_ATTEMPT_STEPS) + 1)] + [(len(MEDIA_ATTEMPT_STEPS), True)],
    ids=[f"after {MEDIA_ATTEMPT_STEPS[n - 1]}" for n in range(1, len(MEDIA_ATTEMPT_STEPS) + 1)] + ["inside succeeded"],
)
def test_p_job_3_media_process_stopped_after_any_write_leaves_a_store_the_sweep_makes_whole(
    tree: Any, stop_after: int, inside_commit: bool
) -> None:
    _, _, jobs = tree
    sha = SHAS[0]
    suffix = "7" * 16
    folder = jobs.upload_folder(sha)
    original = {k: v for k, v in snapshot(folder).items() if not k.startswith(("jobs", "variants", "attempts"))}
    assert _ended(_child(_media_attempt, str(jobs.root), sha, stop_after, inside_commit), "media attempt") == 0
    counts, handed = jobs.sweep(now=NOW)
    done = stop_after - 1 if inside_commit else stop_after
    committed = MEDIA_ATTEMPT_STEPS[done - 1] == "succeeded"
    stored = jobs.read_job(f"job_{sha[:16]}{1:016x}")
    if MEDIA_ATTEMPT_STEPS[done - 1] == "queued":
        assert stored["state"] == "queued" and len(handed) == 1
    elif committed:
        assert stored["state"] == "succeeded" and jobs.succeeded_variant_ids(sha) == {f"var_{suffix}"}
    else:
        assert stored["state"] == "interrupted" and counts.interrupted == 1
        assert jobs.succeeded_variant_ids(sha) == set()
    assert (folder / "variants" / f"var_{suffix}").exists() == committed
    assert (folder / "attempts" / f"att_{suffix}.json").exists() == (done >= 5)
    assert not list((jobs.root / ".partial").iterdir()) if (jobs.root / ".partial").exists() else True
    after = {k: v for k, v in snapshot(folder).items() if not k.startswith(("jobs", "variants", "attempts"))}
    assert after == original

