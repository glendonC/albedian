"""The job store (upload jobs design 3.1, platform-4): every kind's job records, results and partial outputs, inside
an upload's own folder, written in one step and checked against the contract when written and when read.

Layout, inside the upload store's root (`services/ingest/store.py`):

    <root>/.partial/<job_id>-<name>.part      a running job's partial outputs (the upload store's partial folder)
    <root>/<aa>/<sha256>/
      original.<ext>  record.json             the upload: never opened, written, moved or removed here
      job.json        solution.json           today's solve records: read here, never written or removed
      jobs/<job_id>.json                      every kind's job records; 0600 while active, 0400 once ended
      jobs/<kind>[.<subject>].lock            the lock of one piece of work
      solutions/<job_id>.json                 solve results, 0400

Below the root the store never goes through a link: every folder it makes or enters is checked with `lstat`, every
file it reads is opened with `O_NOFOLLOW`, and its scans never follow a link. The root itself is configuration and is
resolved once.

What it relies on [inference from the POSIX specifications, on the local file systems this project runs on: APFS on
macOS, ext4 and the container's overlay on Linux; never a network file system]: `os.replace` (rename(2)) replaces a
name in one step, so a reader sees the old file or the new one; `os.link` (link(2)) and `os.open` with `O_EXCL` fail
when the name exists; `fcntl.flock` locks an open file or directory, a second open descriptor of it is refused with
`LOCK_NB`, and the kernel releases the lock when the process that holds it ends, however it ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from services.contracts.astronomy import parse_observation_solution
from services.contracts.ingest import SolveJob, format_utc
from services.contracts.jobs import INTERRUPTED_SENTENCE, MEDIA_JOB_KINDS, UploadJob
from services.ingest.store import PRIVATE_FILE_MODE, PRIVATE_FOLDER_MODE, IngestStore, _fsync_dir
from services.science.designations import upgrade_stored_solution

LOG = logging.getLogger("albedian.jobs")
ACTIVE_FILE_MODE = 0o600
TERMINAL_STATES = frozenset({"succeeded", "rejected", "failed", "canceled", "interrupted"})
JOBS_FOLDER = "jobs"
SOLUTIONS_FOLDER = "solutions"
VARIANTS_FOLDER = "variants"
PARTIAL_FOLDER = ".partial"
# [proposed] bound for waiting on an upload's jobs/ folder lock across threads or processes: long enough for local
# record fsyncs under load, while guaranteeing no call in the store can wait without end
FOLDER_LOCK_BOUND_SEC = 30.0
FOLDER_LOCK_POLL_SEC = 0.002
# The names the store makes, and the only names its sweep may remove or rewrite; each is read with fullmatch
JOB_ID = re.compile(r"job_([0-9a-f]{16})[0-9a-f]{16}")
JOB_FILE = re.compile(r"(job_[0-9a-f]{32})\.json")
RESULT_FILE = re.compile(r"(job_[0-9a-f]{32})\.json")
VARIANT_DIR = re.compile(r"var_([0-9a-f]{16})")
VARIANT_STAGE_DIR = re.compile(r"variant-[0-9a-f]{16}")
GRAPH_ID = re.compile(r"graph_([0-9a-f]{16})")
MEDIA_LOCK_FILE = re.compile(r"(?:display_copy|restoration)\.graph_([0-9a-f]{16})\.lock")
VARIANT_ENTRY_NAMES = frozenset(
    {"variant.png", "variant.mov", "variant.mp4", "variant.json", "graph.json", "attempt.json"}
)
JOB_PARTIAL = re.compile(r"(job_[0-9a-f]{32})-[a-z0-9_]{1,64}(?:\.[a-z0-9]{1,8})?\.part")
STORE_SCRATCH = re.compile(r"\.job-[0-9a-f]{16}\.tmp")
LOCK_FILE = re.compile(r"[a-z_]{1,32}(?:\.(?:graph|align)_[0-9a-f]{16})?\.lock")
SHA256 = re.compile(r"[0-9a-f]{64}")
LEGACY_JOB_ID = re.compile(r"job_[0-9a-f]{16}")
BUCKET = re.compile(r"[0-9a-f]{2}")



def alignment_subject(alignment: Mapping[str, Any]) -> str:
    """The subject of a solve started from a person's alignment (align-2r): `align_` and the first 16 hex of the
    SHA-256 of the alignment's canonical JSON (sorted keys, no spaces), so a record read back from disk gives the same
    subject as the start that wrote it, whatever the order of its keys."""
    text = json.dumps(dict(alignment), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "align_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def record_subject(record: Mapping[str, Any]) -> Optional[str]:
    """Which piece of work of its upload and kind a record is, the one rule wherever the runner and the store key a
    record (PLATFORM's five places, align-2-runner-review.md): a media job's graph; a solve's alignment digest when it
    ran from one, else none (the plain solve, today's `job.json` among them)."""
    if record.get("kind") in MEDIA_JOB_KINDS:
        return record.get("graphId")
    request = record.get("request")
    return alignment_subject(request) if isinstance(request, Mapping) else None
_FOLDER_GUARDS: dict[Path, threading.Lock] = {}
_FOLDER_GUARDS_LOCK = threading.Lock()


class Unreadable(Exception):
    """A stored job record that fails the contract, or is no regular file: never served, never rewritten."""


class Ended(Exception):
    """A write over a record whose job has ended: an ended record is never rewritten."""


def new_job_id(sha256: str) -> str:
    """`job_` + the hash's first 16 hex characters + 16 random ones: found by the hash prefix, never reused."""
    if not SHA256.fullmatch(sha256):
        raise ValueError("not a SHA-256 hash")
    return f"job_{sha256[:16]}{secrets.token_hex(8)}"


@dataclass(frozen=True)
class SweepCounts:
    """What a sweep did, in counts only: no id, hash, name, place or time of any upload."""

    interrupted: int
    handed_back: int
    unreadable: int
    results_removed: int
    partials_removed: int
    scratch_removed: int


class JobStore:
    """Job records, results and partial outputs of every kind, inside the folders of an `IngestStore`."""

    def __init__(self, uploads: IngestStore) -> None:
        self.uploads = uploads
        self.root = Path(os.path.realpath(uploads.root))
        self._held: dict[Path, int] = {}
        self._held_guard = threading.Lock()

    # ---- Folders: only ever below the root, never through a link ----

    def _relative_parts(self, path: Path) -> tuple[str, ...]:
        """The parts of `path` strictly below `self.root`, with no `.` or `..` anywhere."""
        root_parts = self.root.parts
        parts = path.parts
        if (
            not path.is_absolute()
            or len(parts) <= len(root_parts)
            or parts[: len(root_parts)] != root_parts
            or ".." in parts[len(root_parts) :]
        ):
            raise ValueError("not a path inside the job store's root")
        return parts[len(root_parts) :]

    def upload_folder(self, sha256: str) -> Path:
        if not SHA256.fullmatch(sha256):
            raise ValueError("not a SHA-256 hash")
        return self.root / sha256[:2] / sha256

    def _enter(self, folder: Path, *, make: bool) -> bool:
        """Whether `folder` (below the root) is a real folder at every step from the root; a link or a file on the
        way is refused with an OSError, never followed. With `make`, the last step alone is made (0700) when it is
        missing: the store makes its own folders inside an upload's folder or the root, never the upload's."""
        relative = self._relative_parts(folder)
        if not stat.S_ISDIR(_lstat_mode(self.root)):
            if make:
                raise FileNotFoundError(errno.ENOENT, "no job store root")
            return False
        current = self.root
        for depth, part in enumerate(relative, start=1):
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                if not make or depth != len(relative):
                    if make:
                        raise FileNotFoundError(errno.ENOENT, "no stored upload for that job") from None
                    return False
                try:
                    os.mkdir(current, PRIVATE_FOLDER_MODE)
                except FileExistsError:
                    pass
                mode = os.lstat(current).st_mode
            if not stat.S_ISDIR(mode):
                raise OSError(errno.ENOTDIR, "the job store never goes through a link or a file")
        return True

    def _exists(self, folder: Path) -> bool:
        try:
            return self._enter(folder, make=False)
        except OSError as error:
            if error.errno in (errno.ENOTDIR, errno.ELOOP):
                return False
            raise

    # ---- Records ----

    def _record_identity(self, path: Path) -> tuple[str, str]:
        """The `(sha256, job_id)` a stored job record path stands for."""
        rel = self._relative_parts(path)
        if (
            len(rel) != 4
            or not BUCKET.fullmatch(rel[0])
            or not SHA256.fullmatch(rel[1])
            or not rel[1].startswith(rel[0])
            or rel[2] != JOBS_FOLDER
            or not JOB_FILE.fullmatch(rel[3])
        ):
            raise ValueError("not a job record path the store makes")
        return rel[1], rel[3].removesuffix(".json")

    def record_path(self, job_id: str) -> Optional[Path]:
        """Where a job's record is, found by its id's hash prefix; None when no one upload's folder has it."""
        match = JOB_ID.fullmatch(job_id)
        if not match:
            raise ValueError("not a job id the store makes")
        prefix = match.group(1)
        bucket = self.root / prefix[:2]
        try:
            if not self._exists(bucket):
                return None
            scandir_it = os.scandir(bucket)
        except OSError as error:
            raise Unreadable("a job bucket that cannot be read") from error
        with scandir_it as entries:
            found = [
                entry.name
                for entry in entries
                if SHA256.fullmatch(entry.name) and entry.name.startswith(prefix) and entry.is_dir(follow_symlinks=False)
            ]
        return bucket / found[0] / JOBS_FOLDER / f"{job_id}.json" if len(found) == 1 else None

    def write_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Checks the record against the contract, then writes it in one step under the `jobs/` folder's lock: a
        scratch file made with O_EXCL and mode 0600 in the same folder, locked, fsynced, made 0400 once the job has
        ended, `os.replace`d onto the record, the folder fsynced. The scratch is removed in a `finally` that covers
        the whole write. A record whose job has already ended is never replaced (Ended)."""
        record = UploadJob.model_validate(payload).model_dump(by_alias=True, exclude_unset=True)
        folder = self.upload_folder(record["sourceAssetSha256"]) / JOBS_FOLDER
        if not JOB_ID.fullmatch(record["id"]) or not record["id"].startswith(
            f"job_{record['sourceAssetSha256'][:16]}"
        ):
            raise ValueError("a job id the store did not make for this upload")
        self._enter(folder, make=True)
        target = folder / f"{record['id']}.json"
        with _locked_jobs_folder(folder):
            return self._write_record_locked(target, record)

    def _write_record_locked(self, target: Path, record: dict[str, Any]) -> dict[str, Any]:
        folder = target.parent
        try:
            existing = self.read_record(target)
        except FileNotFoundError:
            existing = None
        except Unreadable:
            raise Ended("a stored record that fails the contract is never rewritten") from None
        if existing is not None and existing["state"] in TERMINAL_STATES:
            raise Ended("a job that has ended keeps the record it ended with")
        scratch = folder / f".job-{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, ACTIVE_FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                json.dump(record, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
                if record["state"] in TERMINAL_STATES:
                    os.chmod(scratch, PRIVATE_FILE_MODE)
                os.replace(scratch, target)
        finally:
            scratch.unlink(missing_ok=True)
        _fsync_dir(folder)
        return record

    def read_record(self, path: Path) -> dict[str, Any]:
        """A stored record, checked against the contract and its own path; one that fails either, is not UTF-8, or is
        no regular file, raises Unreadable. A missing one raises FileNotFoundError."""
        expected_sha, expected_id = self._record_identity(path)
        try:
            exists = self._exists(path.parent)
        except OSError as error:
            raise Unreadable("a stored job record folder that cannot be read") from error
        if not exists:
            raise FileNotFoundError(errno.ENOENT, "no stored job record")
        try:
            text = _read_regular(path)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise Unreadable("a stored job record that cannot be read as a regular UTF-8 file") from error
        try:
            record = UploadJob.model_validate_json(text).model_dump(by_alias=True, exclude_unset=True)
        except (ValidationError, ValueError, RecursionError) as error:
            raise Unreadable("a stored job record fails the contract") from error
        if (
            record["id"] != expected_id
            or record["sourceAssetSha256"] != expected_sha
            or not expected_id.startswith(f"job_{expected_sha[:16]}")
        ):
            raise Unreadable("a stored job record names another job or upload")
        return record

    def read_job(self, job_id: str) -> Optional[dict[str, Any]]:
        """A job's record by its id, checked; None when none is stored, Unreadable when it fails the contract."""
        path = self.record_path(job_id)
        if path is None:
            return None
        try:
            return self.read_record(path)
        except FileNotFoundError:
            return None

    def jobs_of(self, sha256: str) -> list[dict[str, Any]]:
        """Every readable record of an upload, oldest first; unreadable ones are left out, never rewritten."""
        folder = self.upload_folder(sha256) / JOBS_FOLDER
        try:
            if not self._exists(folder):
                return []
            scandir_it = os.scandir(folder)
        except OSError:
            return []
        with scandir_it as raw_entries:
            entries = sorted(raw_entries, key=lambda e: e.name)
        records = []
        for entry in entries:
            if JOB_FILE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                try:
                    records.append(self.read_record(Path(entry.path)))
                except (Unreadable, FileNotFoundError):
                    continue
        return sorted(records, key=lambda r: (instant(r["createdAtUTC"]), r["id"]))

    def legacy_solve_job(self, sha256: str) -> Optional[dict[str, Any]]:
        """Today's `job.json`, checked against `SolveJob` under wire names and against this upload's hash and id;
        never written here. None when there is none, Unreadable when it fails the contract or is no regular file."""
        path = self.upload_folder(sha256) / "job.json"
        try:
            exists = self._exists(path.parent)
        except OSError as error:
            raise Unreadable("a stored job folder that cannot be read") from error
        if not exists:
            return None
        try:
            text = _read_regular(path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise Unreadable("a stored job record that cannot be read as a regular UTF-8 file") from error
        try:
            job = json.loads(
                text,
                parse_float=_finite_json_float,
                parse_int=_finite_json_int,
                parse_constant=_no_json_constant,
            )
            SolveJob.model_validate(job, by_name=False)
        except (ValidationError, ValueError, RecursionError) as error:
            raise Unreadable("a stored job record fails the contract") from error
        if job["id"] != f"job_{sha256[:16]}" or job["sourceAssetSha256"] != sha256:
            raise Unreadable("a stored solve job names another job or upload")
        return job

    def legacy_solve_job_by_id(self, job_id: str) -> Optional[dict[str, Any]]:
        """Today's `job.json` by its 16-character id, found by the hash prefix, checked as `legacy_solve_job`."""
        if not LEGACY_JOB_ID.fullmatch(job_id):
            raise ValueError("not a job id of today's solve records")
        prefix = job_id.removeprefix("job_")
        bucket = self.root / prefix[:2]
        try:
            if not self._exists(bucket):
                return None
            scandir_it = os.scandir(bucket)
        except OSError as error:
            raise Unreadable("a job bucket that cannot be read") from error
        with scandir_it as entries:
            names = sorted(entry.name for entry in entries if entry.is_dir(follow_symlinks=False))
        for name in names:
            if SHA256.fullmatch(name) and name.startswith(prefix):
                job = self.legacy_solve_job(name)
                if job is not None:
                    return job
        return None

    def read_solution(self, sha256: str) -> Optional[dict[str, Any]]:
        """The solution of the upload's served solve (`served_solve`: the newest succeeded, a job of the runner's or
        today's `job.json`, unless it is an aligned solve that did not calibrate), checked as `solution_of` reads it;
        when no solve has succeeded, today's `solution.json` as it is stored. So while a newer solve runs, the last good
        solution is the one served (REQ-JOB-6.5). None when there is none; Unreadable when the one to serve fails the
        contract."""
        newest = self.served_solve(sha256)
        if newest is None:
            return self._solution_file(sha256, self.upload_folder(sha256) / "solution.json")
        return self.solution_of(newest)

    def newest_solution_id(self, sha256: str) -> Optional[str]:
        """The solution id the restoration's export manifest names (restoration design section 12): that of the
        upload's newest succeeded solve (a job of the runner's, or today's `job.json`), when its solution reads whole
        and the contract takes it. None when no solve succeeded, or its solution is missing or refused: a manifest
        never names a solution that cannot be served. Today's `solution.json` with no succeeded job names none. While
        a newer solve runs, the last good one is named (REQ-JOB-6.5)."""
        newest = self.served_solve(sha256)
        if newest is None:
            return None
        try:
            solution = self.solution_of(newest)
        except (Unreadable, ValueError):
            return None
        return newest["solutionId"] if solution is not None else None

    def _succeeded_solves(self, sha256: str) -> list[dict[str, Any]]:
        """The upload's succeeded solves, a job of the runner's or today's `job.json`, newest first."""
        succeeded = [r for r in self.jobs_of(sha256) if r["kind"] == "solve" and r["state"] == "succeeded"]
        try:
            legacy = self.legacy_solve_job(sha256)
        except Unreadable:
            legacy = None
        if legacy is not None and legacy["state"] == "succeeded":
            succeeded.append(legacy)
        return sorted(succeeded, key=lambda r: (instant(r["createdAtUTC"]), len(r["id"]), r["id"]), reverse=True)

    def _calibrated(self, record: dict[str, Any]) -> bool:
        try:
            solution = self.solution_of(record)
        except (Unreadable, ValueError):
            return False
        return solution is not None and solution.get("status") == "calibrated"

    def served_solve(self, sha256: str) -> Optional[dict[str, Any]]:
        """The succeeded solve whose solution the upload serves: the newest, except that a solve from a person's
        alignment whose gate refused never takes the place of a calibrated solution served before it (the lead,
        2026-11-19 12:32; align-2v). A plain solve is served when it is the newest, calibrated or not: a re-solve is
        the truth of its producer's revision [proposed: the exception read as the aligned solve's]."""
        served: Optional[dict[str, Any]] = None
        for record in reversed(self._succeeded_solves(sha256)):
            if (served is not None and record.get("request") is not None and not self._calibrated(record)
                    and self._calibrated(served)):
                continue
            served = record
        return served

    def solution_of(self, record: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The solution a succeeded solve record names: `solutions/<job_id>.json` for a job of the runner's, today's
        `solution.json` for a record today's code wrote; checked as `_solution_file` checks it, and its id must be
        the one the record names. None when it is missing."""
        sha256 = record["sourceAssetSha256"]
        if "kind" in record:
            if record["kind"] != "solve" or not JOB_ID.fullmatch(record["id"]):
                raise ValueError("not a solve job of the store's")
            path = self.upload_folder(sha256) / SOLUTIONS_FOLDER / f"{record['id']}.json"
        else:
            path = self.upload_folder(sha256) / "solution.json"
        solution = self._solution_file(sha256, path)
        if solution is not None and record.get("solutionId") is not None and solution.get("id") != record["solutionId"]:
            raise Unreadable("a stored solution is not the one its job names")
        return solution

    def newest_solve(self, sha256: str) -> Optional[dict[str, Any]]:
        """The newest readable solve attempt of an upload, active or ended; today's `job.json` counts too."""
        attempts = [r for r in self.jobs_of(sha256) if r["kind"] == "solve"]
        try:
            legacy = self.legacy_solve_job(sha256)
        except Unreadable:
            legacy = None
        if legacy is not None:
            attempts.append(legacy)
        return max(attempts, key=lambda r: (instant(r["createdAtUTC"]), len(r["id"]), r["id"])) if attempts else None

    def interrupt(self, record: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
        """A running record no live process holds, stored as interrupted (REQ-JOB-3.4); when its job has meanwhile
        ended, the record it ended with. Any uncommitted media variant of that job is removed unless named by a
        succeeded job or held by a live process (REQ-JOB-5.5, platform-or4)."""
        ended = UploadJob.model_validate(_interrupted(record, now or datetime.now(timezone.utc))).model_dump(
            by_alias=True, exclude_unset=True)
        try:
            written = self.write_record(ended)
        except Ended:
            stored = self.read_job(record["id"])
            if stored is None:
                raise
            return stored
        if written.get("kind") in MEDIA_JOB_KINDS:
            self.remove_uncommitted_variant(written["sourceAssetSha256"], written.get("graphId"), check_held=True)
        return written

    def succeeded_variant_ids(self, sha256: str) -> set[str]:
        """The variant ids named by readable `succeeded` media job records of `sha256` (REQ-JOB-5.4, platform-or4)."""
        return {
            r["variantId"]
            for r in self.jobs_of(sha256)
            if r["kind"] in MEDIA_JOB_KINDS
            and r["state"] == "succeeded"
            and isinstance(r.get("variantId"), str)
            and VARIANT_DIR.fullmatch(r["variantId"]) is not None
        }

    def _media_variant_held(self, upload: Path, variant_id: str) -> bool:
        """Whether any live process holds a media lock for the graph that produces `variant_id`."""
        match = VARIANT_DIR.fullmatch(variant_id)
        if match is None:
            return False
        jobs = upload / JOBS_FOLDER
        suffix = match.group(1)
        return any(self.is_held(jobs / f"{kind}.graph_{suffix}.lock") for kind in MEDIA_JOB_KINDS)

    def _any_media_lock_held(self) -> bool:
        """Whether any upload has a held media job lock, so concurrent staging in `.partial/` is never removed."""
        for upload in self._upload_folders():
            jobs = upload / JOBS_FOLDER
            if not self._exists(jobs):
                continue
            try:
                with os.scandir(jobs) as raw_jobs:
                    entries = list(raw_jobs)
            except OSError:
                continue
            for entry in entries:
                if MEDIA_LOCK_FILE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    if self.is_held(Path(entry.path)):
                        return True
        return False

    def _remove_variant_folder(self, target: Path) -> bool:
        """Removes `variants/var_<16hex>` only when it is a real directory below the root containing regular variant
        files alone (`VARIANT_ENTRY_NAMES`, never a link or foreign entry; P-JOB-6)."""
        if not VARIANT_DIR.fullmatch(target.name) or not self._exists(target):
            return False
        try:
            with os.scandir(target) as raw_entries:
                entries = list(raw_entries)
        except OSError:
            return False
        if any(not entry.is_file(follow_symlinks=False) or entry.name not in VARIANT_ENTRY_NAMES for entry in entries):
            return False
        for entry in entries:
            try:
                os.unlink(entry.path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(target)
        except OSError:
            return False
        return True

    def _remove_variant_staging(self, staging: Path) -> bool:
        """Removes a crashed `publish_variant` staging directory (`.partial/variant-<16hex>`) only when it is a real
        directory holding at most `var_<16hex>` directories of regular variant files (`VARIANT_ENTRY_NAMES`), never
        through a link or over a foreign entry (P-JOB-6)."""
        if not VARIANT_STAGE_DIR.fullmatch(staging.name) or not self._exists(staging):
            return False
        try:
            with os.scandir(staging) as raw_subs:
                subs = list(raw_subs)
        except OSError:
            return False
        sub_files: list[tuple[Path, list[Path]]] = []
        for sub in subs:
            if not VARIANT_DIR.fullmatch(sub.name) or not sub.is_dir(follow_symlinks=False):
                return False
            sub_path = Path(sub.path)
            if not self._exists(sub_path):
                return False
            try:
                with os.scandir(sub_path) as raw_leaves:
                    leaves = list(raw_leaves)
            except OSError:
                return False
            if any(not leaf.is_file(follow_symlinks=False) or leaf.name not in VARIANT_ENTRY_NAMES for leaf in leaves):
                return False
            sub_files.append((sub_path, [Path(leaf.path) for leaf in leaves]))
        for sub_path, leaves in sub_files:
            for leaf_path in leaves:
                try:
                    os.unlink(leaf_path)
                except FileNotFoundError:
                    pass
            try:
                os.rmdir(sub_path)
            except OSError:
                return False
        try:
            os.rmdir(staging)
        except OSError:
            return False
        return True

    def remove_uncommitted_variant(self, sha256: str, graph_id: Optional[str], *, check_held: bool = False) -> bool:
        """Removes `<upload>/variants/var_<h>` for `graph_<h>` when no readable `succeeded` media job names it and,
        with `check_held`, when no live process holds that graph's lock."""
        match = GRAPH_ID.fullmatch(graph_id or "")
        if match is None:
            return False
        variant_id = f"var_{match.group(1)}"
        if variant_id in self.succeeded_variant_ids(sha256):
            return False
        upload = self.upload_folder(sha256)
        if check_held and self._media_variant_held(upload, variant_id):
            return False
        return self._remove_variant_folder(upload / VARIANTS_FOLDER / variant_id)

    def _solution_file(self, sha256: str, path: Path) -> Optional[dict[str, Any]]:
        """A stored solution file, read without following a link, parsed as RFC 8259 JSON with finite numbers,
        upgraded in memory for pre-catalog-2b star designations (`upgrade_stored_solution`), and checked against
        this upload's hash and id and the solution contract (`parse_observation_solution`), then returned without
        re-dumping through the model. None when none is stored; Unreadable when its folder or file cannot be read as
        a regular UTF-8 file, is not finite JSON, names another upload, or fails the contract. Never rewritten or
        removed here."""
        try:
            exists = self._exists(path.parent)
        except OSError as error:
            raise Unreadable("a stored solution folder that cannot be read") from error
        if not exists:
            return None
        try:
            text = _read_regular(path)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise Unreadable("a stored solution that cannot be read as a regular UTF-8 file") from error
        try:
            raw = json.loads(
                text,
                parse_float=_finite_json_float,
                parse_int=_finite_json_int,
                parse_constant=_no_json_constant,
            )
            if not isinstance(raw, dict):
                raise ValueError("stored solution is not a JSON object")
            solution = upgrade_stored_solution(raw)
            parsed = parse_observation_solution(solution)
        except (ValidationError, ValueError, RecursionError) as error:
            raise Unreadable("a stored solution fails the contract") from error
        if parsed.source_sha256 != sha256 or parsed.source_asset_id != f"src_{sha256[:16]}":
            raise Unreadable("a stored solution names another upload")
        return solution

    # ---- Locks: one per piece of work, held until its attempt has ended ----

    def lock_path(self, sha256: str, kind: str, graph_id: Optional[str] = None) -> Path:
        name = f"{kind}.{graph_id}.lock" if graph_id else f"{kind}.lock"
        if not LOCK_FILE.fullmatch(name):
            raise ValueError("not a lock name the store makes")
        return self.upload_folder(sha256) / JOBS_FOLDER / name

    def lock_of(self, record: dict[str, Any]) -> Path:
        return self.lock_path(record["sourceAssetSha256"], record["kind"], record_subject(record))

    def try_hold(self, lock: Path) -> bool:
        """Takes the lock without waiting: True when this process now holds it, False when any process does. A lock
        this process holds is refused here too, since a flock belongs to the open file, not the thread."""
        with self._held_guard:
            if lock in self._held:
                return False
            self._enter(lock.parent, make=True)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, ACTIVE_FILE_MODE)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise OSError(errno.EINVAL, "a lock the store makes is a regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(fd)
                if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    return False
                raise
            self._held[lock] = fd
            return True

    def release(self, lock: Path) -> None:
        with self._held_guard:
            fd = self._held.pop(lock, None)
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def is_held(self, lock: Path) -> bool:
        """Whether a live process holds the lock, asked without waiting and left as it was found. A lock file that is
        missing, or no regular file, is held by no one."""
        with self._held_guard:
            if lock in self._held:
                return True
        try:
            if not self._exists(lock.parent):
                return False
            fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    return True
                raise
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    # ---- Partial outputs and publishing ----

    def partial(self, job_id: str, name: str) -> Path:
        """A path for one of a running job's outputs in the upload store's partial folder, named with the job's id."""
        filename = f"{job_id}-{name}.part"
        if not JOB_PARTIAL.fullmatch(filename):
            raise ValueError("not a partial name the store makes")
        folder = self.root / PARTIAL_FOLDER
        self._enter(folder, make=True)
        return folder / filename

    def publish(self, partial: Path, target: Path) -> None:
        """Makes a partial output visible under `target` (`<upload>/solutions/<job_id>.json`) in one step: by
        `os.link`, refused when the name exists; then mode 0400, the partial removed and the folder fsynced."""
        partial_rel = self._relative_parts(partial)
        partial_match = (
            JOB_PARTIAL.fullmatch(partial_rel[1])
            if len(partial_rel) == 2 and partial_rel[0] == PARTIAL_FOLDER
            else None
        )
        if partial_match is None:
            raise ValueError("not a partial the store made")
        job_id = partial_match.group(1)
        target_rel = self._relative_parts(target)
        if (
            len(target_rel) != 4
            or not BUCKET.fullmatch(target_rel[0])
            or not SHA256.fullmatch(target_rel[1])
            or not target_rel[1].startswith(target_rel[0])
            or target_rel[2] != SOLUTIONS_FOLDER
            or target_rel[3] != f"{job_id}.json"
            or not job_id.startswith(f"job_{target_rel[1][:16]}")
        ):
            raise ValueError("not a result path the store publishes to")
        if not self._exists(partial.parent) or not stat.S_ISREG(_lstat_mode(partial)):
            raise ValueError("a partial the store publishes is a regular file, never a link")
        self._enter(target.parent, make=True)
        os.link(partial, target)
        # target is a new hard link to that regular file, so chmod has no link to follow
        os.chmod(target, PRIVATE_FILE_MODE)
        partial.unlink()
        _fsync_dir(target.parent)

    # ---- The sweep, at start-up ----

    def sweep(self, now: Optional[datetime] = None) -> tuple[SweepCounts, list[dict[str, Any]]]:
        """After a process stopped: an unheld running job becomes interrupted (what it recorded kept, its cancel time
        among it), an unheld queued job is handed back, each result that no succeeded record names is removed, each
        partial output whose job is not running is removed, and the store's own stray scratch files are removed.

        It touches only names the store makes, in the folders it makes (`jobs/`, `solutions/`, `.partial/`), never
        through a link: never an original, an upload's record, today's `job.json` or `solution.json`, a lock, an
        upload's own partial file, or anything else. It logs counts only. Returns the counts and the queued records
        handed back, oldest first."""
        moment = now or datetime.now(timezone.utc)
        counts = dict.fromkeys(SweepCounts.__dataclass_fields__, 0)
        handed_back: list[dict[str, Any]] = []
        running: set[str] = set()
        for upload in self._upload_folders():
            jobs = upload / JOBS_FOLDER
            succeeded: set[str] = set()
            succeeded_variants: set[str] = set()
            held_variants: set[str] = set()
            if self._exists(jobs):
                try:
                    with _locked_jobs_folder(jobs):
                        with os.scandir(jobs) as raw_jobs:
                            entries = list(raw_jobs)
                        for entry in entries:
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            lock_match = MEDIA_LOCK_FILE.fullmatch(entry.name)
                            if lock_match is not None:
                                if self.is_held(Path(entry.path)):
                                    held_variants.add(f"var_{lock_match.group(1)}")
                                continue
                            if STORE_SCRATCH.fullmatch(entry.name):
                                if not self.is_held(Path(entry.path)):
                                    try:
                                        os.unlink(entry.path)
                                        counts["scratch_removed"] += 1
                                    except FileNotFoundError:
                                        pass
                                continue
                            if not JOB_FILE.fullmatch(entry.name):
                                continue
                            try:
                                record = self.read_record(Path(entry.path))
                                held = self.is_held(self.lock_of(record))
                            except FileNotFoundError:
                                continue
                            except Unreadable:
                                counts["unreadable"] += 1
                                continue
                            if record["state"] == "running" and not held:
                                ended_record = UploadJob.model_validate(_interrupted(record, moment)).model_dump(
                                    by_alias=True, exclude_unset=True
                                )
                                try:
                                    self._write_record_locked(Path(entry.path), ended_record)
                                    counts["interrupted"] += 1
                                except Ended:
                                    pass
                            elif record["state"] == "running":
                                running.add(record["id"])
                                if record["kind"] in MEDIA_JOB_KINDS:
                                    gm = GRAPH_ID.fullmatch(record.get("graphId") or "")
                                    if gm is not None:
                                        held_variants.add(f"var_{gm.group(1)}")
                            elif record["state"] == "queued" and not held:
                                handed_back.append(record)
                                counts["handed_back"] += 1
                            elif record["state"] == "queued":
                                running.add(record["id"])
                                if record["kind"] in MEDIA_JOB_KINDS:
                                    gm = GRAPH_ID.fullmatch(record.get("graphId") or "")
                                    if gm is not None:
                                        held_variants.add(f"var_{gm.group(1)}")
                            elif record["state"] == "succeeded":
                                succeeded.add(record["id"])
                                if record["kind"] in MEDIA_JOB_KINDS and isinstance(record.get("variantId"), str):
                                    succeeded_variants.add(record["variantId"])
                except FileNotFoundError:
                    pass
            results = upload / SOLUTIONS_FOLDER
            if self._exists(results):
                try:
                    with os.scandir(results) as raw_results:
                        result_entries = list(raw_results)
                except FileNotFoundError:
                    result_entries = []
                for entry in result_entries:
                    match = RESULT_FILE.fullmatch(entry.name)
                    if match and entry.is_file(follow_symlinks=False) and match.group(1) not in succeeded:
                        try:
                            os.unlink(entry.path)
                            counts["results_removed"] += 1
                        except FileNotFoundError:
                            pass
            variants = upload / VARIANTS_FOLDER
            if self._exists(variants):
                try:
                    with os.scandir(variants) as raw_variants:
                        variant_entries = list(raw_variants)
                except FileNotFoundError:
                    variant_entries = []
                for entry in variant_entries:
                    if (
                        VARIANT_DIR.fullmatch(entry.name)
                        and entry.is_dir(follow_symlinks=False)
                        and entry.name not in succeeded_variants
                        and entry.name not in held_variants
                        and not self._media_variant_held(upload, entry.name)
                        and entry.name not in self.succeeded_variant_ids(upload.name)
                    ):
                        if self._remove_variant_folder(Path(entry.path)):
                            counts["results_removed"] += 1
        partials = self.root / PARTIAL_FOLDER
        if self._exists(partials):
            try:
                with os.scandir(partials) as raw_partials:
                    partial_entries = list(raw_partials)
            except FileNotFoundError:
                partial_entries = []
            for entry in partial_entries:
                match = JOB_PARTIAL.fullmatch(entry.name)
                if match and entry.is_file(follow_symlinks=False) and match.group(1) not in running:
                    try:
                        os.unlink(entry.path)
                        counts["partials_removed"] += 1
                    except FileNotFoundError:
                        pass
                elif (
                    VARIANT_STAGE_DIR.fullmatch(entry.name)
                    and entry.is_dir(follow_symlinks=False)
                    and not self._any_media_lock_held()
                ):
                    if self._remove_variant_staging(Path(entry.path)):
                        counts["partials_removed"] += 1
        result = SweepCounts(**counts)
        LOG.info(
            "job sweep: %d interrupted, %d handed back, %d unreadable, %d results, %d partials and %d scratch "
            "files removed",
            result.interrupted,
            result.handed_back,
            result.unreadable,
            result.results_removed,
            result.partials_removed,
            result.scratch_removed,
        )
        return result, sorted(handed_back, key=lambda r: (instant(r["createdAtUTC"]), r["id"]))

    def upload_hashes(self) -> list[str]:
        """The hashes of the uploads stored, found as the sweep finds their folders, never through a link."""
        return sorted(folder.name for folder in self._upload_folders())

    def _upload_folders(self) -> Iterator[Path]:
        if not stat.S_ISDIR(_lstat_mode(self.root)):
            return
        with os.scandir(self.root) as buckets:
            for bucket in buckets:
                if BUCKET.fullmatch(bucket.name) and bucket.is_dir(follow_symlinks=False):
                    with os.scandir(bucket.path) as uploads:
                        for upload in uploads:
                            if (
                                SHA256.fullmatch(upload.name)
                                and upload.name.startswith(bucket.name)
                                and upload.is_dir(follow_symlinks=False)
                            ):
                                yield Path(upload.path)


def _folder_guard(folder: Path) -> threading.Lock:
    with _FOLDER_GUARDS_LOCK:
        guard = _FOLDER_GUARDS.get(folder)
        if guard is None:
            guard = threading.Lock()
            _FOLDER_GUARDS[folder] = guard
        return guard


@contextmanager
def _locked_jobs_folder(folder: Path) -> Iterator[None]:
    """Serializes record writes and sweep passes on one upload's `jobs/` folder across threads and processes, bounded
    by `FOLDER_LOCK_BOUND_SEC`, re-opening if the folder was unlinked and recreated before the lock was taken."""
    deadline = time.monotonic() + FOLDER_LOCK_BOUND_SEC
    guard = _folder_guard(folder)
    if not guard.acquire(timeout=FOLDER_LOCK_BOUND_SEC):
        raise OSError(errno.ETIMEDOUT, "timed out waiting for the jobs folder lock")
    try:
        while True:
            fd = os.open(folder, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_DIRECTORY)
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                            raise
                        if time.monotonic() >= deadline:
                            raise OSError(errno.ETIMEDOUT, "timed out waiting for the jobs folder lock") from None
                        time.sleep(FOLDER_LOCK_POLL_SEC)
                if os.lstat(folder).st_ino == os.fstat(fd).st_ino:
                    break
                os.close(fd)
            except BaseException:
                os.close(fd)
                raise
            if time.monotonic() >= deadline:
                raise OSError(errno.ETIMEDOUT, "timed out waiting for the jobs folder lock")
            time.sleep(FOLDER_LOCK_POLL_SEC)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    finally:
        guard.release()


def _interrupted(record: dict[str, Any], moment: datetime) -> dict[str, Any]:
    """A running record the process stopped, ended: what it recorded stays, its cancel time among it; it finishes
    no earlier than it started or was asked to stop."""
    ended = {k: v for k, v in record.items() if k != "queueAhead"}
    known = [instant(record[key]) for key in ("startedAtUTC", "cancelRequestedAtUTC") if key in record]
    return {
        **ended,
        "state": "interrupted",
        "finishedAtUTC": format_utc(max([moment, *known])),
        "error": INTERRUPTED_SENTENCE,
    }


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.removesuffix("Z") + "+00:00")


def _lstat_mode(path: Path) -> int:
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return 0


def _finite_json_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number outside JSON's finite range")
    return value


def _finite_json_int(text: str) -> int:
    value = int(text)
    try:
        float(value)
    except OverflowError:
        raise ValueError("integer outside IEEE-754 finite range") from None
    return value


def _no_json_constant(token: str) -> Any:
    raise ValueError(f"{token} is not RFC 8259 JSON")


def _read_regular(path: Path) -> str:
    """A regular file's text, opened without following a link and without waiting (a FIFO named like a record would
    otherwise block the open); anything else raises OSError."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        return handle.read()
