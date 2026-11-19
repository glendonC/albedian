"""A solve started from a person's alignment as its own piece of work in the job runner (align-2r; the lead's 12:32
answer: one kind, the solve job carrying the optional PersonAlignment; PLATFORM's review of the five places,
.lead/kiro/reports/PLATFORM/align-2-runner-review.md).

Covers:
- One subject for a record wherever the runner and the store key it: a media job's graph, a plain solve's none, an
  aligned solve's `align_` and 16 hex of the SHA-256 of its alignment's canonical JSON (sorted keys, no spaces), the same
  for the same alignment whatever its key order, another for another alignment
- The lock of an aligned solve has its own name, which the store makes and its pattern takes
- In the runner: an aligned start writes its alignment in the record's request; a plain start beside it is its own job,
  never joined to or answered by the aligned one; the same alignment joins its job; another alignment is a new job
- Today's `job.json` (a plain solve) is never an aligned solve's newest attempt
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from services.ingest import runner as job_runner
from services.ingest.job_store import JobStore, LOCK_FILE, alignment_subject, record_subject
from services.ingest.runner import JobKind
from tests.integration.test_job_runner import Script, _wait_state, solve_kind, world  # noqa: F401

ALIGNMENT = {"format": "albedian.person-alignment", "formatVersion": 1, "source": "person", "centerAltDeg": 41.5,
             "centerAzDeg": 128.25, "rollDeg": -6.0, "fovDiagonalDeg": 68.5}
OTHER = {**ALIGNMENT, "centerAzDeg": 200.0}


def test_a_records_subject_is_its_graph_its_alignments_digest_or_none() -> None:
    sha = "a" * 64
    plain = {"kind": "solve", "sourceAssetSha256": sha}
    aligned = {"kind": "solve", "sourceAssetSha256": sha, "request": ALIGNMENT}
    media = {"kind": "restoration", "sourceAssetSha256": sha, "graphId": "graph_0123456789abcdef",
             "request": {"kind": "restoration", "exposureEv": 1.0}}
    assert record_subject(plain) is None
    assert record_subject(media) == "graph_0123456789abcdef"
    subject = record_subject(aligned)
    assert subject == alignment_subject(ALIGNMENT)
    assert subject.startswith("align_") and len(subject) == len("align_") + 16
    reordered = json.loads(json.dumps(dict(reversed(list(ALIGNMENT.items())))))
    assert alignment_subject(reordered) == subject
    assert alignment_subject(OTHER) != subject


def test_an_aligned_solves_lock_has_its_own_name(world: dict[str, Any]) -> None:
    store: JobStore = world["store"]
    sha = world["shas"][0]
    subject = alignment_subject(ALIGNMENT)
    lock = store.lock_of({"kind": "solve", "sourceAssetSha256": sha, "request": ALIGNMENT})
    assert lock.name == f"solve.{subject}.lock" and LOCK_FILE.fullmatch(lock.name)
    assert store.lock_of({"kind": "solve", "sourceAssetSha256": sha}).name == "solve.lock"
    with pytest.raises(ValueError):
        store.lock_path(sha, "solve", "align_not_hex_at_all")


def _aligned_kind(script: Script) -> JobKind:
    kind = solve_kind(script)
    return JobKind(**{**kind.__dict__, "subject": job_runner.solve_subject})


def test_an_aligned_start_is_its_own_piece_of_work_beside_the_plain_solve(world: dict[str, Any]) -> None:
    script = Script()
    world["kinds"]["solve"] = _aligned_kind(script)
    runner = world["runner"]()
    sha = world["shas"][0]
    aligned, created = runner.start(sha, "solve", dict(ALIGNMENT))
    assert created and aligned["request"] == ALIGNMENT and "graphId" not in aligned
    plain, plain_created = runner.start(sha, "solve")
    assert plain_created and plain["id"] != aligned["id"] and "request" not in plain
    again, again_created = runner.start(sha, "solve", json.loads(json.dumps(dict(reversed(list(ALIGNMENT.items()))))))
    assert not again_created and again["id"] == aligned["id"]
    other, other_created = runner.start(sha, "solve", dict(OTHER))
    assert other_created and other["id"] not in (aligned["id"], plain["id"]) and other["request"] == OTHER
    script.release.set()
    for job in (aligned, plain, other):
        ended = _wait_state(runner, job["id"], ("succeeded",))
        assert ended["state"] == "succeeded"
    assert runner.get(aligned["id"])["request"] == ALIGNMENT
    # Each ended: a start of each answers its own succeeded job, never another's
    assert runner.start(sha, "solve", dict(ALIGNMENT))[0]["id"] == aligned["id"]
    assert runner.start(sha, "solve")[0]["id"] == plain["id"]


def test_a_plain_solve_never_answers_an_aligned_start(world: dict[str, Any]) -> None:
    script = Script()
    world["kinds"]["solve"] = _aligned_kind(script)
    runner = world["runner"]()
    sha = world["shas"][1]
    plain, _ = runner.start(sha, "solve")
    script.release.set()
    assert _wait_state(runner, plain["id"], ("succeeded",))["state"] == "succeeded"
    aligned, created = runner.start(sha, "solve", dict(ALIGNMENT))
    assert created and aligned["id"] != plain["id"] and aligned["request"] == ALIGNMENT
    assert _wait_state(runner, aligned["id"], ("succeeded",))["state"] == "succeeded"


def test_todays_job_json_is_a_plain_solves_attempt_never_an_aligned_ones(world: dict[str, Any]) -> None:
    # A start of an aligned solve reads its newest attempt by its own subject; today's job.json, a plain solve, is
    # never it (PLATFORM's place 4), even while it says it is running
    script = Script()
    world["kinds"]["solve"] = _aligned_kind(script)
    store, sha = world["store"], world["shas"][0]
    legacy = {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "running",
              "createdAtUTC": "2020-01-01T05:00:00Z", "startedAtUTC": "2020-01-01T05:00:01Z",
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    (store.upload_folder(sha) / "job.json").write_text(json.dumps(legacy), encoding="utf-8")
    runner = world["runner"]()
    assert runner._newest(sha, "solve", None)["id"] == legacy["id"]
    assert runner._newest(sha, "solve", alignment_subject(ALIGNMENT)) is None
