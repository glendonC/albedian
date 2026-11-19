"""The upload job runner (upload jobs design 3.2, platform-5): every kind of work on a stored upload runs here.

A kind plugs in by what it declares (`JobKind`): its name and group, its producer, its whole-job time bound, its fixed
sentences, what it adds to the lock key, the stages written at creation, how to tell its results are whole, and the
function that does the work through a `JobContext`. The runner imports no science or media module.

- One slot per group (`solve`; `media` for the display copy and the restoration), one worker thread per slot, a
  queue per group in creation order (REQ-JOB-1.1, 1.2), and one child per group for the whole process (`SlotWorker`).
- One attempt per piece of work: a start under the (upload, kind, subject) lock joins an active job, answers the
  current succeeded one, or makes a new queued job (REQ-JOB-1.3). The store's `flock` answers for other processes.
- A job is `queued` until its slot is free and `running` from then; its live record is served while it runs and
  follows to disk when a stage ends, when the pass changes, and at most once a second (REQ-JOB-2).
- A cancel or the job's deadline sets its stop event: the slot's child and its whole process group are stopped at
  once, work in the runner thread at its next `check_cancel` (REQ-JOB-3.6, REQ-JOB-4).
- Every record is checked by the contract when written (the store does it); a job that ends without its commit
  loses its partial outputs and every result it published (REQ-JOB-5.5).
- A running job that no live process holds is stored `interrupted` when it is read; a queued one is run (REQ-JOB-3).

Nothing a kind raises is logged or stored but its kind's fixed sentence: an exception's text can hold a path or a
capture value (REQ-JOB-9).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Optional

from pydantic import ValidationError

from services.contracts.ingest import format_utc
from services.contracts.jobs import JOB_PASS_LABELS, JOB_PASS_MAX, JOB_STAGE_UNITS, MEDIA_JOB_KINDS, UploadJob
from services.ingest.job_store import (
    JOB_PARTIAL,
    LEGACY_JOB_ID,
    PARTIAL_FOLDER,
    SOLUTIONS_FOLDER,
    TERMINAL_STATES,
    Ended,
    JobStore,
    Unreadable,
    instant,
    new_job_id,
)
from services.ingest.store import IngestStore
from services.science.bounded_worker import POLL_SEC, BoundedWorker, WorkerStopped

LOG = logging.getLogger("albedian.jobs")
# [proposed, the restoration's rule (restoration design section 11)] the record on disk follows the live one at
# most once a second while a stage advances; a stage's end and a pass change are written at once
WRITE_INTERVAL_SEC = 1.0
# [proposed] how long a start waits for the record of a job another process has just locked: that process writes
# it right after taking the lock, a few file system calls
JOIN_ELSEWHERE_BOUND_SEC = 5.0
GROUPS = ("solve", "media")
ACTIVE_STATES = ("queued", "running")


class NoUpload(Exception):
    """A start for an upload, or an upload record, that is not stored."""


class JobRefusal(Exception):
    """The kind refuses the work: the job ends `failed` with this fixed sentence of its contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobStopped(Exception):
    """A cancel or the job's deadline stopped the work."""


@dataclass(frozen=True)
class Producer:
    """The name and revision of the code a kind runs, and the source files its results come from, from the
    repository's root (design 3.5). A unit that changes results raises the revision; the guard
    (`tests/integration/test_producer_revisions.py`) pins both with a digest of the files."""

    name: str
    revision: int
    files: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {"name": self.name, "revision": self.revision}

    def made(self, record: Mapping[str, Any]) -> bool:
        """Whether a job record's producer is this one at this revision or later: a result to answer as current.
        One that names no producer (today's records), another producer or an older revision is stale (REQ-JOB-6.3)."""
        made_by = record.get("producer")
        return (isinstance(made_by, Mapping) and made_by.get("name") == self.name
                and isinstance(made_by.get("revision"), int) and made_by["revision"] >= self.revision)


def producer_digest(producer: Producer, root: Path) -> str:
    """The SHA-256 of a producer's files, each framed by its path, a NUL and its length in 8 bytes, in its order: the
    source text alone, so the same on every machine."""
    digest = hashlib.sha256()
    for path in producer.files:
        data = (root / path).read_bytes()
        digest.update(path.encode("utf-8") + b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


@dataclass(frozen=True)
class KindResult:
    """What a kind's run made: `succeeded` or `rejected`, and the result ids its contract allows."""

    state: str
    fields: Mapping[str, Any] = field(default_factory=dict)


def _no_subject(sha256: str, request: Optional[Mapping[str, Any]]) -> Optional[str]:
    return None


def _no_initial_stages(asset: Mapping[str, Any]) -> list[dict[str, Any]]:
    return []


def _results_whole(store: JobStore, record: Mapping[str, Any]) -> bool:
    return True


@dataclass(frozen=True)
class JobKind:
    """All the runner knows of a kind (design 3.2)."""

    name: str
    group: str
    producer: Producer
    run: Callable[["JobContext"], KindResult]
    time_limit_s: Callable[[Mapping[str, Any], Optional[Mapping[str, Any]]], float]
    time_limit_reason: str
    internal_error_reason: str
    subject: Callable[[str, Optional[Mapping[str, Any]]], Optional[str]] = _no_subject
    # The sentence for a job whose ending record could not be written; the internal error when the kind has none
    store_failure_reason: Optional[str] = None
    initial_stages: Callable[[Mapping[str, Any]], list[dict[str, Any]]] = _no_initial_stages
    results_valid: Callable[[JobStore, Mapping[str, Any]], bool] = _results_whole


_KINDS: dict[str, JobKind] = {}
_KINDS_GUARD = threading.Lock()


def register_kind(kind: JobKind) -> None:
    """Registers a kind by its name; the same kind twice is a no-op, another kind under a taken name is refused."""
    if kind.name not in JOB_STAGE_UNITS or kind.group not in GROUPS:
        raise ValueError("a kind the job contract does not know")
    with _KINDS_GUARD:
        known = _KINDS.get(kind.name)
        if known is not None and known is not kind:
            raise ValueError("another kind is registered under that name")
        _KINDS[kind.name] = kind


def register_default_kind(kind: JobKind) -> None:
    """Registers a kind unless a kind of that name is registered already: the app registers its kinds each time it
    is built, and a test that put a kind of its own under the name keeps it."""
    with _KINDS_GUARD:
        if kind.name in _KINDS:
            return
    register_kind(kind)


def _now() -> datetime:
    """Now, at the contract's resolution (milliseconds, as `format_utc` writes it)."""
    moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def _later(*moments: Optional[str]) -> str:
    """The latest of now and the stamps given, so a record's times never run backwards."""
    known = [instant(m) for m in moments if m is not None]
    return format_utc(max([_now(), *known]))


class _Job:
    """One active job of this process: its live record and what stopping and ending it needs."""

    def __init__(self, kind: JobKind, record: dict[str, Any], lock: Path, upload: Mapping[str, Any],
                 original: Path) -> None:
        self.kind = kind
        self.record = record
        self.lock = lock
        self.upload = upload
        self.original = original
        self.stop = threading.Event()
        self.stop_cause: Optional[str] = None
        self.ended = threading.Event()
        self.write_lock = threading.Lock()
        self.last_write = 0.0
        self.published: list[Path] = []
        self.pending_pass: dict[str, dict[str, Any]] = {}
        self.pass_raised: dict[str, bool] = {}
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.deadline = float("inf")

    @property
    def id(self) -> str:
        return self.record["id"]


class JobContext:
    """What a kind's run is given (design 3.2). Read the fields; call the methods."""

    def __init__(self, runner: "Runner", job: _Job) -> None:
        self._runner = runner
        self._job = job
        self.job_id: str = job.id
        self.sha256: str = job.record["sourceAssetSha256"]
        self.record: Mapping[str, Any] = copy.deepcopy(job.upload)
        self.asset: Mapping[str, Any] = self.record["sourceAsset"]
        self.original: Path = job.original
        self.request: Optional[Mapping[str, Any]] = copy.deepcopy(job.record.get("request"))
        self.stop: threading.Event = job.stop

    def progress(self, stage: str, unit: str, done: int, total: Optional[int]) -> None:
        self._runner._progress(self._job, stage, unit, done, total)

    def pass_(self, stage: str, number: int, label: str) -> None:
        self._runner._pass(self._job, stage, number, label)

    def input_span(self, first_frame_index: int, frame_count: int, start_sec: float, end_sec: float) -> None:
        self._runner._input_span(self._job, first_frame_index, frame_count, start_sec, end_sec)

    def check_cancel(self) -> None:
        if self._job.stop.is_set():
            raise JobStopped("the job was stopped")

    def remaining_s(self) -> float:
        return max(0.0, self._job.deadline - time.monotonic())

    def child(self) -> "SlotWorker":
        return self._runner._worker(self._job.kind.group)

    def partial(self, name: str) -> Path:
        return self._runner.store.partial(self.job_id, name)

    def solution_path(self) -> Path:
        return self._runner.store.upload_folder(self.sha256) / SOLUTIONS_FOLDER / f"{self.job_id}.json"

    def publish(self, partial: Path, target: Path) -> None:
        self._runner.store.publish(partial, target)
        self._job.published.append(target)

    def live(self) -> dict[str, Any]:
        """The job's record as a read would serve it now."""
        return self._runner._served(self._job)


class Runner:
    """The runner of one store root: its active jobs, its groups' queues, slots and children."""

    def __init__(self, uploads: IngestStore) -> None:
        self.uploads = uploads
        self.store = JobStore(uploads)
        self._cond = threading.Condition(threading.RLock())
        self._jobs: dict[str, _Job] = {}
        self._keys: dict[tuple[str, str, Optional[str]], str] = {}
        self._queues: dict[str, list[_Job]] = {group: [] for group in GROUPS}
        self._slots: dict[str, Optional[_Job]] = {group: None for group in GROUPS}
        self._threads: dict[str, threading.Thread] = {}
        self._last_created: Optional[datetime] = None
        self.closed = False

    # ---- Starting ----

    def start(self, sha256: str, kind_name: str, request: Optional[dict[str, Any]] = None, *,
              listener: Optional[Callable[[dict[str, Any]], None]] = None) -> tuple[dict[str, Any], bool]:
        """(record, created): an active job joined, the current succeeded one answered, or a new queued job. A
        `listener` of a job this process runs is told its record as served now and after every change, to its end."""
        kind = _KINDS.get(kind_name)
        if kind is None:
            raise ValueError("no kind of that name is registered")
        upload = self.uploads.read_record(sha256)
        original = self.uploads.original(sha256)
        if upload is None or original is None:
            raise NoUpload("no stored upload with that hash")
        if (kind.name in MEDIA_JOB_KINDS) != (request is not None):
            raise ValueError("a media job names its request and a solve none")
        subject = kind.subject(sha256, request)
        key = (sha256, kind.name, subject)
        lock = self.store.lock_path(sha256, kind.name, subject)
        waited_until = time.monotonic() + JOIN_ELSEWHERE_BOUND_SEC
        while True:
            with self._cond:
                if self.closed:
                    raise RuntimeError("the runner has stopped")
                joined = self._keys.get(key)
                if joined is not None:
                    return self._listened(self._jobs[joined], listener), False
                if self.store.try_hold(lock):
                    handed = False
                    try:
                        answer, created, handed = self._start_held(kind, key, lock, request, upload, original)
                        job = self._jobs.get(answer["id"]) if handed else None
                        return (self._listened(job, listener) if job is not None else answer), created
                    finally:
                        if not handed:
                            self.store.release(lock)
                elsewhere = self._newest(sha256, kind.name, subject)
                if elsewhere is not None and elsewhere["state"] in ACTIVE_STATES:
                    return elsewhere, False
            if time.monotonic() > waited_until:
                raise RuntimeError("another process holds that job's lock but wrote no active record")
            time.sleep(0.01)

    def _start_held(self, kind: JobKind, key: tuple[str, str, Optional[str]], lock: Path,
                    request: Optional[dict[str, Any]], upload: Mapping[str, Any],
                    original: Path) -> tuple[dict[str, Any], bool, bool]:
        """Under the lock: (record, created, whether the lock now belongs to a job of this process)."""
        sha256, _, subject = key
        newest = self._newest(sha256, kind.name, subject)
        if newest is not None and "kind" in newest:
            if newest["state"] == "queued":
                return self._served(self._adopt(kind, newest, lock, upload, original)), False, True
            if newest["state"] == "running":
                self.store.interrupt(newest)
            elif newest["state"] == "succeeded" and self._current(kind, newest):
                return newest, False, False
        elif newest is not None and newest["state"] == "succeeded" and self._current(kind, newest):
            return newest, False, False
        record: dict[str, Any] = {
            "id": new_job_id(sha256),
            "sourceAssetSha256": sha256,
            "kind": kind.name,
            "state": "queued",
            "createdAtUTC": format_utc(self._created_at()),
            "stages": kind.initial_stages(upload["sourceAsset"]),
        }
        if kind.name in MEDIA_JOB_KINDS:
            record["request"] = request
            record["graphId"] = subject
        written = self.store.write_record(record)
        return self._served(self._adopt(kind, written, lock, upload, original)), True, True

    def _listened(self, job: _Job, listener: Optional[Callable[[dict[str, Any]], None]]) -> dict[str, Any]:
        """The job's record as served now, told first to a new listener, which then hears every change."""
        served = self._served(job)
        if listener is not None:
            job.listeners.append(listener)
            _tell(listener, served)
        return served

    def _created_at(self) -> datetime:
        """Now, and at least a millisecond after the last job this runner made, so creation order is total."""
        moment = _now()
        if self._last_created is not None and moment <= self._last_created:
            moment = self._last_created + timedelta(milliseconds=1)
        self._last_created = moment
        return moment

    def _newest(self, sha256: str, kind: str, subject: Optional[str]) -> Optional[dict[str, Any]]:
        """The newest readable attempt of that piece of work; for the solve, today's `job.json` counts too."""
        attempts = [
            r for r in self.store.jobs_of(sha256)
            if r["kind"] == kind and (r.get("graphId") if kind in MEDIA_JOB_KINDS else None) == subject
        ]
        if kind == "solve":
            try:
                legacy = self.store.legacy_solve_job(sha256)
            except Unreadable:
                legacy = None
            if legacy is not None:
                attempts.append(legacy)
        if not attempts:
            return None
        return max(attempts, key=lambda r: (instant(r["createdAtUTC"]), len(r["id"]), r["id"]))

    def _current(self, kind: JobKind, record: Mapping[str, Any]) -> bool:
        """Whether a succeeded job's results are to be answered: made by the kind's producer at its revision or
        later, and whole."""
        if not kind.producer.made(record):
            return False
        try:
            return bool(kind.results_valid(self.store, record))
        except (Unreadable, OSError, ValueError):
            return False

    def _adopt(self, kind: JobKind, record: dict[str, Any], lock: Path, upload: Mapping[str, Any],
               original: Path) -> _Job:
        """A queued record this process now holds the lock of, put in its group's queue in creation order."""
        job = _Job(kind, {k: v for k, v in record.items() if k != "queueAhead"}, lock, upload, original)
        subject = record.get("graphId") if kind.name in MEDIA_JOB_KINDS else None
        self._jobs[job.id] = job
        self._keys[(record["sourceAssetSha256"], kind.name, subject)] = job.id
        queue = self._queues[kind.group]
        queue.append(job)
        queue.sort(key=lambda j: (instant(j.record["createdAtUTC"]), j.id))
        self._ensure_thread(kind.group)
        self._cond.notify_all()
        return job

    def recover(self) -> None:
        """At start: the store's sweep, then every queued job it handed back, run in creation order."""
        _, handed = self.store.sweep()
        with self._cond:
            for record in handed:
                self._hand_back(record)

    def _hand_back(self, record: dict[str, Any]) -> Optional[_Job]:
        kind = _KINDS.get(record["kind"])
        if kind is None or record["id"] in self._jobs:
            return self._jobs.get(record["id"])
        upload = self.uploads.read_record(record["sourceAssetSha256"])
        original = self.uploads.original(record["sourceAssetSha256"])
        lock = self.store.lock_of(record)
        if upload is None or original is None or not self.store.try_hold(lock):
            return None
        try:
            fresh = self.store.read_job(record["id"])
        except Unreadable:
            fresh = None
        if fresh is None or fresh["state"] != "queued":
            self.store.release(lock)
            return None
        return self._adopt(kind, fresh, lock, upload, original)

    # ---- Reading ----

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        """The live record when this process runs the job, else the stored one; Unreadable when it fails the
        contract. A stored running job no process holds is stored interrupted; a queued one is run."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is not None:
                return self._served(job)
        return self._stored(job_id)

    def _stored(self, job_id: str) -> Optional[dict[str, Any]]:
        if LEGACY_JOB_ID.fullmatch(job_id):
            return self.store.legacy_solve_job_by_id(job_id)
        if len(job_id) != len("job_") + 32:
            return None
        record = self.store.read_job(job_id)
        if record is None or record["state"] not in ACTIVE_STATES:
            return record
        if self.store.is_held(self.store.lock_of(record)):
            return record
        if record["state"] == "running":
            return self.store.interrupt(record)
        with self._cond:
            adopted = self._hand_back(record)
            if adopted is not None:
                return self._served(adopted)
        return record

    def _served(self, job: _Job) -> dict[str, Any]:
        with self._cond:
            served = copy.deepcopy(job.record)
            if served["state"] == "queued":
                group = job.kind.group
                queue = self._queues[group]
                ahead = queue.index(job) if job in queue else 0
                served["queueAhead"] = ahead + (1 if self._slots[group] is not None else 0)
            return served

    def wait(self, job_id: str, timeout_s: Optional[float] = None) -> Optional[dict[str, Any]]:
        """The record once the job has ended, or as it is when `timeout_s` passes first."""
        with self._cond:
            job = self._jobs.get(job_id)
        if job is not None:
            job.ended.wait(timeout_s)
        return self.get(job_id)

    # ---- Cancel ----

    def cancel(self, job_id: str) -> Optional[tuple[dict[str, Any], str]]:
        """(record, outcome): "canceled" for a queued job, "requested" for a running one, "finished" for one that
        had ended (unchanged), "elsewhere" for one another process runs; None when no such job is stored."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is not None and job.record["state"] == "queued" and not job.stop.is_set():
                queue = self._queues[job.kind.group]
                if job in queue:
                    queue.remove(job)
                # The slot never runs it now; its record is written before it is served (_end)
                job.stop_cause = "cancel"
                job.stop.set()
                canceled = {k: v for k, v in job.record.items() if k != "queueAhead"}
                canceled = {**canceled, "state": "canceled", "finishedAtUTC": _later(job.record["createdAtUTC"])}
                canceled_queued = True
            elif job is not None and job.record["state"] == "running":
                canceled_queued = False
                if "cancelRequestedAtUTC" not in job.record:
                    job.record = {**job.record, "cancelRequestedAtUTC": _later(job.record["startedAtUTC"])}
                    if job.stop_cause is None:
                        job.stop_cause = "cancel"
                    job.stop.set()
            elif job is not None:
                # Ended in this process a moment ago, its last write on its way
                return self._served(job), "finished"
            if job is not None and not canceled_queued:
                answer = self._served(job)
        if job is not None:
            if canceled_queued:
                self._end(job, canceled)
                self._finish(job)
                return self._served(job), "canceled"
            self._write(job, force=True, quiet=True)
            return answer, "requested"
        stored = self._stored(job_id)
        if stored is None:
            return None
        if stored["state"] in TERMINAL_STATES or "kind" not in stored:
            return stored, "finished"
        return stored, "elsewhere"

    # ---- Running ----

    def _ensure_thread(self, group: str) -> None:
        thread = self._threads.get(group)
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=self._serve_group, args=(group,), name=f"albedian-jobs-{group}",
                                      daemon=True)
            self._threads[group] = thread
            thread.start()

    def _serve_group(self, group: str) -> None:
        while True:
            with self._cond:
                while not self.closed and not self._queues[group]:
                    self._cond.wait()
                if self.closed:
                    return
                job = self._queues[group].pop(0)
                self._slots[group] = job
            try:
                self._run(job)
            except Exception as error:  # noqa: BLE001 - the slot must go on; only the kind of error is logged
                LOG.error("job slot error: %s", type(error).__name__)
            finally:
                with self._cond:
                    self._slots[group] = None

    def _worker(self, group: str) -> "SlotWorker":
        return _SLOT_WORKERS[group]

    def _run(self, job: _Job) -> None:
        kind = job.kind
        with self._cond:
            if job.record["state"] != "queued" or job.stop.is_set():
                return
            job.record = {**job.record, "state": "running", "startedAtUTC": _later(job.record["createdAtUTC"]),
                          "producer": kind.producer.as_record()}
        timer: Optional[threading.Timer] = None
        try:
            self._write(job, force=True)
            limit = float(kind.time_limit_s(job.upload["sourceAsset"], job.record.get("request")))
            job.deadline = time.monotonic() + limit
            timer = threading.Timer(limit, self._deadline_passed, args=(job,))
            timer.daemon = True
            timer.start()
            outcome: Any = kind.run(JobContext(self, job))
        except (JobStopped, WorkerStopped):
            outcome = JobStopped()
        except JobRefusal as refusal:
            outcome = refusal
        except Exception:  # noqa: BLE001 - its text is never stored or logged (REQ-JOB-9)
            outcome = None
        finally:
            if timer is not None:
                timer.cancel()
        if self.closed:
            # The server is stopping: the record stays as it was, and the next start's sweep says it was interrupted.
            # No ending is built, so nothing reads the clock or the store for a job that will not end here
            self._finish(job, keep_results=False)
            return
        try:
            if isinstance(outcome, KindResult):
                final = self._commit(job, outcome)
            elif isinstance(outcome, JobStopped):
                final = self._stopped(job)
            elif isinstance(outcome, JobRefusal):
                final = self._failed(job, outcome.reason)
            else:
                final = self._failed(job, None)
        except Exception:  # noqa: BLE001 - a kind's results check may raise anything; its text is never stored
            # The ending itself failed (a results check that raised): this one job fails, the slot goes on
            final = self._failed(job, None)
        try:
            self._end(job, final)
        except (OSError, ValidationError, ValueError):
            LOG.error("job record could not be written")
            if final["state"] in ("succeeded", "rejected"):
                # From the record as it ran, which the live one still is: an ending that names no result
                try:
                    self._end(job, self._failed(job, kind.store_failure_reason))
                except (OSError, ValidationError, ValueError):
                    LOG.error("job record could not be written")
        self._finish(job, keep_results=job.record["state"] in ("succeeded", "rejected"))

    def _end(self, job: _Job, record: dict[str, Any]) -> None:
        """Writes the record a job ends with, then serves it: the write is the commit point, so a reader served an
        ended record finds what it names already stored (design 3.1). A record already ended is never replaced."""
        with job.write_lock:
            try:
                self.store.write_record(record)
            except Ended:
                pass
            with self._cond:
                job.record = record
                told = self._served(job) if job.listeners else None
                for listener in job.listeners:
                    _tell(listener, told)

    def _deadline_passed(self, job: _Job) -> None:
        with self._cond:
            if not job.ended.is_set() and job.stop_cause is None:
                job.stop_cause = "deadline"
            job.stop.set()

    def _ended_record(self, job: _Job, state: str, **fields: Any) -> dict[str, Any]:
        record = {k: v for k, v in job.record.items() if k not in ("queueAhead", "error", "finishedAtUTC")}
        finished = _later(record.get("startedAtUTC"), record.get("cancelRequestedAtUTC"), record["createdAtUTC"])
        return {**record, **fields, "state": state, "finishedAtUTC": finished}

    def _commit(self, job: _Job, result: KindResult) -> dict[str, Any]:
        """The record that ends the job with its results, checked before it is written; a job past its bound, or
        whose results are not whole, fails instead."""
        if job.stop_cause == "deadline" or time.monotonic() >= job.deadline:
            return self._failed(job, job.kind.time_limit_reason)
        if not isinstance(result, KindResult) or result.state not in ("succeeded", "rejected"):
            return self._failed(job, None)
        record = self._ended_record(job, result.state, **dict(result.fields))
        try:
            UploadJob.model_validate(record)
        except (ValidationError, ValueError):
            return self._failed(job, None)
        if not self._current(job.kind, record):
            return self._failed(job, None)
        return record

    def _stopped(self, job: _Job) -> dict[str, Any]:
        if job.stop_cause == "cancel":
            return self._ended_record(job, "canceled")
        if job.stop_cause == "deadline":
            return self._failed(job, job.kind.time_limit_reason)
        return self._failed(job, None)

    def _failed(self, job: _Job, reason: Optional[str]) -> dict[str, Any]:
        """The failed record with `reason` when the contract allows it for the kind, else the kind's own sentence;
        past the job's bound, the kind's time-limit sentence."""
        if job.stop_cause == "deadline" or time.monotonic() >= job.deadline:
            reason = job.kind.time_limit_reason
        # One read of the clock: the fallback sentence ends the job at the same moment
        record = self._ended_record(job, "failed", error=reason or job.kind.internal_error_reason)
        try:
            UploadJob.model_validate(record)
        except (ValidationError, ValueError):
            return {**record, "error": job.kind.internal_error_reason}
        return record

    def _finish(self, job: _Job, keep_results: bool = False) -> None:
        """A job has ended: its results gone unless it committed, its partial outputs removed, its lock freed."""
        if not keep_results:
            for path in job.published:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        partials = self.store.root / PARTIAL_FOLDER
        try:
            names = os.listdir(partials)
        except FileNotFoundError:
            names = []
        for name in names:
            match = JOB_PARTIAL.fullmatch(name)
            if match and match.group(1) == job.id:
                try:
                    os.unlink(partials / name)
                except FileNotFoundError:
                    pass
        with self._cond:
            self._jobs.pop(job.id, None)
            subject = job.record.get("graphId") if job.kind.name in MEDIA_JOB_KINDS else None
            key = (job.record["sourceAssetSha256"], job.kind.name, subject)
            if self._keys.get(key) == job.id:
                self._keys.pop(key)
            self.store.release(job.lock)
            job.ended.set()

    # ---- Progress ----

    def _write(self, job: _Job, *, force: bool, quiet: bool = False) -> None:
        """Writes the live record, in the order the record changed: each write takes its snapshot under the job's
        write lock. A record whose job has ended is never replaced; a progress write that fails is left to the next
        one, so a full disk never stops the work mid-step."""
        with job.write_lock:
            with self._cond:
                if job.listeners:
                    told = self._served(job)
                    for listener in job.listeners:
                        _tell(listener, told)
                if not force and time.monotonic() - job.last_write < WRITE_INTERVAL_SEC:
                    return
                snapshot = {k: v for k, v in copy.deepcopy(job.record).items() if k != "queueAhead"}
                job.last_write = time.monotonic()
            try:
                self.store.write_record(snapshot)
            except Ended:
                pass
            except OSError:
                if not quiet:
                    raise
                LOG.error("job progress could not be written")

    def _progress(self, job: _Job, stage: str, unit: str, done: int, total: Optional[int]) -> None:
        units = JOB_STAGE_UNITS[job.kind.name]
        if stage not in units or unit not in units[stage]:
            raise ValueError("a stage or unit the kind does not declare")
        if not _count(done) or (total is not None and (not _count(total) or total < 1 or done > total)):
            raise ValueError("a count outside its total")
        with self._cond:
            stages = {s["stage"]: s for s in job.record["stages"]}
            previous = stages.get(stage)
            raised = job.pass_raised.pop(stage, False)
            if previous is not None and done < previous["done"] and not raised:
                raise ValueError("a count fell within one pass")
            entry: dict[str, Any] = {"stage": stage, "unit": unit, "done": done, "total": total}
            pass_ = job.pending_pass.pop(stage, None) or (previous or {}).get("pass")
            if pass_ is not None:
                entry["pass"] = pass_
            stages[stage] = entry
            order = list(units)
            job.record = {**job.record, "stages": sorted(stages.values(), key=lambda s: order.index(s["stage"]))}
        self._write(job, force=raised or (total is not None and done == total), quiet=True)

    def _pass(self, job: _Job, stage: str, number: int, label: str) -> None:
        if stage not in JOB_STAGE_UNITS[job.kind.name] or job.kind.name in MEDIA_JOB_KINDS:
            raise ValueError("only a solve stage reports a pass")
        if label not in JOB_PASS_LABELS or not _count(number) or not 1 <= number <= JOB_PASS_MAX:
            raise ValueError("a pass the contract does not know")
        with self._cond:
            stages = {s["stage"]: s for s in job.record["stages"]}
            current = (job.pending_pass.get(stage) or (stages.get(stage) or {}).get("pass") or {}).get("number", 0)
            if number <= current:
                raise ValueError("a pass number only rises")
            entry = {"number": number, "label": label}
            job.pass_raised[stage] = True
            if stage in stages:
                # A new pass has counted nothing yet, and its total is not known until it reports one
                stages[stage] = {"stage": stage, "unit": stages[stage]["unit"], "done": 0, "total": None,
                                 "pass": entry}
                job.record = {**job.record, "stages": [stages[s["stage"]] for s in job.record["stages"]]}
                write = True
            else:
                job.pending_pass[stage] = entry
                write = False
        if write:
            self._write(job, force=True, quiet=True)

    def _input_span(self, job: _Job, first: int, count: int, start_sec: float, end_sec: float) -> None:
        if job.kind.name in MEDIA_JOB_KINDS:
            raise ValueError("only a solve records a frame span")
        with self._cond:
            job.record = {**job.record, "inputSpan": {"firstFrameIndex": first, "frameCount": count,
                                                      "startSec": start_sec, "endSec": end_sec}}
        self._write(job, force=True, quiet=True)

    # ---- Stopping ----

    def shutdown(self) -> None:
        """The server is stopping: no job starts again, the slots' children and their groups are stopped, and the
        records of jobs that were running stay as they are, for the next start's sweep to end as interrupted."""
        with self._cond:
            self.closed = True
            self._cond.notify_all()
            for job in self._jobs.values():
                job.stop.set()
        with _RUNNERS_GUARD:
            for root, runner in list(_RUNNERS.items()):
                if runner is self:
                    del _RUNNERS[root]
            last = not _RUNNERS
        if last:
            # The children are the process's, shared by every runner: stopped with the last one
            for worker in _SLOT_WORKERS.values():
                worker.close()


class SlotWorker:
    """A group's child, one per process, shared by the runners of every store root: it holds the solvers and their
    star tiles in memory, so a process keeps one per group, as it kept one solve child before the runner. Each runner
    runs one job of a group at a time; a call waits for the child while another root's job uses it, and stops
    waiting when its job is stopped (`WorkerStopped`), as a call does."""

    def __init__(self) -> None:
        self._worker = BoundedWorker()
        self._lock = threading.Lock()

    @property
    def pid(self) -> Optional[int]:
        return self._worker.pid

    def call(self, *args: Any, stop: Optional[threading.Event] = None, **kwargs: Any) -> Any:
        while not self._lock.acquire(timeout=POLL_SEC):
            if stop is not None and stop.is_set():
                raise WorkerStopped("the call was stopped while it waited for the child")
        try:
            return self._worker.call(*args, stop=stop, **kwargs)
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._worker.close()


_SLOT_WORKERS: dict[str, SlotWorker] = {group: SlotWorker() for group in GROUPS}


def count_stale(uploads: IngestStore, kind: str, producer: Producer) -> dict[str, int]:
    """The uploads whose newest succeeded job of `kind` the producer made at its revision or later (`current`), at an
    older revision or by another producer (`stale`), or that names no producer, today's records (`no_producer`).
    Counts only (unit 10i's report); an upload with no succeeded job of the kind is not counted."""
    store = JobStore(uploads)
    counts = {"current": 0, "stale": 0, "no_producer": 0}
    for sha256 in store.upload_hashes():
        succeeded = [r for r in store.jobs_of(sha256) if r["kind"] == kind and r["state"] == "succeeded"]
        if kind == "solve":
            try:
                legacy = store.legacy_solve_job(sha256)
            except Unreadable:
                legacy = None
            if legacy is not None and legacy["state"] == "succeeded":
                succeeded.append(legacy)
        if not succeeded:
            continue
        newest = max(succeeded, key=lambda r: (instant(r["createdAtUTC"]), len(r["id"]), r["id"]))
        key = "no_producer" if "producer" not in newest else "current" if producer.made(newest) else "stale"
        counts[key] += 1
    return counts


def _tell(listener: Callable[[dict[str, Any]], None], record: dict[str, Any]) -> None:
    """A listener is told a copy; one that raises never stops the job, and only the kind of its error is logged."""
    try:
        listener(copy.deepcopy(record))
    except Exception as error:  # noqa: BLE001 - a listener's failure never stops the job
        LOG.error("job listener error: %s", type(error).__name__)


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


_RUNNERS: dict[str, Runner] = {}
_RUNNERS_GUARD = threading.Lock()


def runner_for(uploads: IngestStore) -> Runner:
    """The one runner of this store's resolved root in this process, made on first use (design 3.2)."""
    root = os.path.realpath(uploads.root)
    with _RUNNERS_GUARD:
        runner = _RUNNERS.get(root)
        if runner is None or runner.closed:
            runner = _RUNNERS[root] = Runner(uploads)
        return runner
