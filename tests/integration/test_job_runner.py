"""The upload job runner (upload jobs design 3.2, platform-5; REQ-JOB-1 to 4; P-JOB-1, 2, 4, 5, 7, 8).

The runner is tested with fake kinds, never with science: a kind here is a few lines that report progress, wait on
the test's own events, publish a made-up solution, refuse, raise, or start a grandchild in the group's child. Every
upload is a small PNG stored through the upload route, so the runner reads real `record.json` files. Every wait has
its own bound and fails by an assertion. The one exception is the media kinds' rejected ending (platform-8): the two
real media kinds run through the API with their child's step called in this process, so a test's stand-in reaches it.
"""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import io
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable, Iterator, Optional

from fastapi.testclient import TestClient
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
import numpy as np
from PIL import Image
import pytest

from services.api import create_app
from services.contracts.jobs import INTERRUPTED_SENTENCE, UploadJob
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.job_store import JobStore, new_job_id
from services.ingest.runner import (
    JobContext,
    JobKind,
    JobRefusal,
    JobStopped,
    KindResult,
    NoUpload,
    Producer,
    runner_for,
)
from services.ingest.store import IngestStore
from services.media import decode as media_decode
from services.media import jobs as media_jobs
from services.media import variants as media_variants
from services.science.bounded_worker import POLL_SEC, STOP_GRACE_SEC, WorkerTimeout

BOUND_S = 30.0
# A stop in the child is seen at its next poll, then the child's group is terminated and, after the grace, killed
STOP_BOUND_S = POLL_SEC + 2 * STOP_GRACE_SEC + 1.5
SOLVE_PRODUCER = Producer("albedian.test_solve", 1)
MEDIA_PRODUCER = Producer("albedian.test_media", 1)
TIME_LIMIT_SENTENCE = "The decoder stopped at its time limit."
INTERNAL_SENTENCE = "The solve stopped on an internal error."
REFUSAL_SENTENCE = "The decoder could not read the file."
MEDIA_TIME_LIMIT = "The restoration stopped at its time limit."
MEDIA_INTERNAL = "The restoration stopped on an internal error."
SECRET = "secret_place_and_path_marker_51c2"


# ---- Made-up uploads ----


def _png(n: int) -> bytes:
    pixels = np.full((4, 4), n % 256, dtype=np.uint8)
    pixels[0, 0] = (n // 256) % 256
    buf = io.BytesIO()
    Image.fromarray(pixels, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _upload(root: Path, n: int) -> str:
    """Stores PNG number `n` through the upload route, so its folder and record are the upload store's own."""
    uploads = IngestStore(root, max_bytes=10_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    answer = TestClient(app).post("/api/ingest", content=_png(n), headers={"content-type": "image/png"})
    assert answer.status_code in (200, 201), answer.text
    return answer.json()["sourceAsset"]["sha256"]


def _store_stage(asset: dict[str, Any]) -> list[dict[str, Any]]:
    size = int(asset["byteSize"])
    return [{"stage": "store", "unit": "bytes", "done": size, "total": size}]


# ---- Fake kinds ----


class Script:
    """What a fake kind does in one run, told by the test: named events it waits on, and what it returns."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.runs: list[str] = []
        self.overlap = 0
        self.active = 0
        self.guard = threading.Lock()
        self.contexts: list[JobContext] = []


def _solution_payload(sha: str) -> dict[str, Any]:
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json")
                         .read_text(encoding="utf-8"))["valid"]["calibrated"]
    text = json.dumps(fixture).replace(fixture["sourceSha256"], sha).replace(fixture["sourceAssetId"], f"src_{sha[:16]}")
    out = json.loads(text)
    out["id"] = f"sol_{sha[:16]}"
    return out


def _publish_solution(ctx: JobContext) -> KindResult:
    partial = ctx.partial("solution.json")
    partial.write_text(json.dumps(_solution_payload(ctx.sha256)), encoding="utf-8")
    ctx.publish(partial, ctx.solution_path())
    return KindResult("succeeded", {"solutionId": f"sol_{ctx.sha256[:16]}"})


def solve_kind(script: Script, body: Optional[Callable[[JobContext], KindResult]] = None, *,
               time_limit_s: float = BOUND_S) -> JobKind:
    def run(ctx: JobContext) -> KindResult:
        with script.guard:
            script.active += 1
            script.overlap = max(script.overlap, script.active)
            script.runs.append(ctx.job_id)
            script.contexts.append(ctx)
        try:
            script.entered.set()
            if body is not None:
                return body(ctx)
            assert script.release.wait(BOUND_S), "the test never released the run"
            return _publish_solution(ctx)
        finally:
            with script.guard:
                script.active -= 1

    return JobKind(
        name="solve", group="solve", producer=SOLVE_PRODUCER, run=run,
        time_limit_s=lambda asset, request: time_limit_s,
        time_limit_reason=TIME_LIMIT_SENTENCE, internal_error_reason=INTERNAL_SENTENCE,
        initial_stages=_store_stage,
        results_valid=lambda store, record: store.solution_of(record) is not None,
    )


def media_kind(script: Script, body: Optional[Callable[[JobContext], KindResult]] = None) -> JobKind:
    def run(ctx: JobContext) -> KindResult:
        with script.guard:
            script.runs.append(ctx.job_id)
            script.contexts.append(ctx)
        script.entered.set()
        if body is not None:
            return body(ctx)
        assert script.release.wait(BOUND_S), "the test never released the run"
        ctx.progress("decode", "frames", 1, 1)
        return KindResult("succeeded", {"attemptId": f"att_{ctx.job_id[4:20]}", "variantId": f"var_{ctx.job_id[4:20]}"})

    return JobKind(
        name="restoration", group="media", producer=MEDIA_PRODUCER, run=run,
        time_limit_s=lambda asset, request: BOUND_S,
        time_limit_reason=MEDIA_TIME_LIMIT, internal_error_reason=MEDIA_INTERNAL,
        subject=lambda sha, request: "graph_" + hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16],
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A store root with three uploads, and the kinds the runner knows replaced by the test's fake ones."""
    root = tmp_path / "store"
    shas = [_upload(root, n) for n in range(3)]
    kinds: dict[str, JobKind] = {}
    monkeypatch.setattr(job_runner, "_KINDS", kinds)
    uploads = IngestStore(root, max_bytes=10_000_000)
    made: list[job_runner.Runner] = []

    def runner() -> job_runner.Runner:
        r = runner_for(uploads)
        if r not in made:
            made.append(r)
        return r

    yield {"root": root, "shas": shas, "kinds": kinds, "uploads": uploads, "runner": runner,
           "store": JobStore(uploads)}
    for r in made:
        r.shutdown()


def _wait_state(runner: job_runner.Runner, job_id: str, states: tuple[str, ...]) -> dict[str, Any]:
    deadline = time.monotonic() + BOUND_S
    while time.monotonic() < deadline:
        record = runner.get(job_id)
        if record is not None and record["state"] in states:
            return record
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {states} in {BOUND_S:g} s")


def _on_disk(store: JobStore, job_id: str) -> dict[str, Any]:
    path = store.record_path(job_id)
    assert path is not None
    return json.loads(path.read_text(encoding="utf-8"))


class Writes:
    """Every record the store writes, as written, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.records: list[dict[str, Any]] = []
        real = JobStore.write_record
        outer = self

        def spy(store: JobStore, payload: dict[str, Any]) -> dict[str, Any]:
            written = real(store, payload)
            outer.records.append(copy.deepcopy(written))
            return written

        monkeypatch.setattr(JobStore, "write_record", spy)

    def of(self, job_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r["id"] == job_id]


# ---- A job's life: queued, running, ended ----


def test_a_job_is_queued_then_running_then_succeeded_and_every_record_passes_the_contract(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    writes = Writes(monkeypatch)
    script = Script()
    world["kinds"]["solve"] = solve_kind(script)
    runner, sha = world["runner"](), world["shas"][0]
    record, created = runner.start(sha, "solve")
    assert created and record["kind"] == "solve" and record["state"] in ("queued", "running")
    assert script.entered.wait(BOUND_S)
    running = runner.get(record["id"])
    assert running["state"] == "running" and running["producer"] == {"name": "albedian.test_solve", "revision": 1}
    script.release.set()
    ended = runner.wait(record["id"], BOUND_S)
    assert ended["state"] == "succeeded" and ended["solutionId"] == f"sol_{sha[:16]}"
    states = [r["state"] for r in writes.of(record["id"])]
    assert states[0] == "queued" and states[-1] == "succeeded" and set(states) == {"queued", "running", "succeeded"}
    first = writes.of(record["id"])[0]
    assert "startedAtUTC" not in first and "producer" not in first and first["stages"] == _store_stage(
        world["uploads"].read_record(sha)["sourceAsset"])
    for written in writes.of(record["id"]):
        UploadJob.model_validate(written)
    assert _on_disk(world["store"], record["id"]) == ended
    assert world["store"].solution_of(ended)["id"] == f"sol_{sha[:16]}"
    assert not world["store"].is_held(world["store"].lock_path(sha, "solve")), "the lock is freed once the job ended"
    assert record["id"].startswith(f"job_{sha[:16]}") and len(record["id"]) == 36


def test_a_start_for_an_upload_never_stored_or_an_unknown_kind_is_refused(world: dict[str, Any]) -> None:
    script = Script()
    world["kinds"]["solve"] = solve_kind(script)
    runner = world["runner"]()
    with pytest.raises(NoUpload):
        runner.start("f" * 64, "solve")
    with pytest.raises(ValueError):
        runner.start(world["shas"][0], "restoration")
    assert script.runs == []


def test_the_current_succeeded_job_is_answered_and_a_job_whose_results_are_gone_runs_again(
        world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    runner, sha = world["runner"](), world["shas"][0]
    first, _ = runner.start(sha, "solve")
    ended = runner.wait(first["id"], BOUND_S)
    again, created = runner.start(sha, "solve")
    assert (again, created, len(script.runs)) == (ended, False, 1)
    (world["store"].upload_folder(sha) / "solutions" / f"{first['id']}.json").unlink()
    third, created = runner.start(sha, "solve")
    assert created and third["id"] != first["id"]
    runner.wait(third["id"], BOUND_S)
    assert len(script.runs) == 2


# ---- P-JOB-2: one attempt per piece of work ----


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(threads=st.integers(min_value=2, max_value=12))
@example(threads=2)
def test_p_job_2_starts_from_many_threads_make_one_attempt_and_run_it_once(world: dict[str, Any], threads: int) -> None:
    script = Script()
    world["kinds"]["solve"] = solve_kind(script)
    runner = world["runner"]()
    sha = world["shas"][threads % 3]
    answers: list[tuple[str, bool]] = []
    barrier = threading.Barrier(threads)

    def start() -> None:
        barrier.wait(BOUND_S)
        record, created = runner.start(sha, "solve")
        answers.append((record["id"], created))

    pool = [threading.Thread(target=start) for _ in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(BOUND_S)
    assert len(answers) == threads
    assert len({job_id for job_id, _ in answers}) == 1
    assert sum(created for _, created in answers) == 1
    script.release.set()
    runner.wait(answers[0][0], BOUND_S)
    assert len(script.runs) == 1
    # The next example starts afresh: the job that ended here is current, so a later start answers it
    world["store"].upload_folder(sha).joinpath("solutions", f"{answers[0][0]}.json").unlink()


def _start_in_child(root: str, sha: str, answers: Any, release: Any) -> None:
    """One process's start of the solve of `sha`, with a kind that waits for `release`; it reports (id, created)."""
    uploads = IngestStore(Path(root), max_bytes=10_000_000)

    def run(ctx: JobContext) -> KindResult:
        if not release.wait(BOUND_S):
            os._exit(4)
        return _publish_solution(ctx)

    job_runner._KINDS["solve"] = JobKind(
        name="solve", group="solve", producer=SOLVE_PRODUCER, run=run,
        time_limit_s=lambda asset, request: BOUND_S, time_limit_reason=TIME_LIMIT_SENTENCE,
        internal_error_reason=INTERNAL_SENTENCE, initial_stages=_store_stage)
    record, created = runner_for(uploads).start(sha, "solve")
    answers.put((record["id"], created))
    if created:
        runner_for(uploads).wait(record["id"], BOUND_S)


def test_p_job_2_starts_from_two_processes_make_one_attempt(world: dict[str, Any]) -> None:
    context = multiprocessing.get_context("spawn")
    answers, release = context.Queue(), context.Event()
    sha = world["shas"][0]
    children = [context.Process(target=_start_in_child, args=(str(world["root"]), sha, answers, release), daemon=True)
                for _ in range(2)]
    try:
        for child in children:
            child.start()
        got = [answers.get(timeout=BOUND_S * 2) for _ in children]
        release.set()
        for child in children:
            child.join(BOUND_S)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
    assert len({job_id for job_id, _ in got}) == 1 and sorted(created for _, created in got) == [False, True]
    records = world["store"].jobs_of(sha)
    assert [r["state"] for r in records] == ["succeeded"]


def test_starts_with_different_subjects_get_their_own_jobs(world: dict[str, Any]) -> None:
    script = Script()
    world["kinds"]["restoration"] = media_kind(script)
    runner, sha = world["runner"](), world["shas"][0]
    one, created_one = runner.start(sha, "restoration", {"kind": "restoration"})
    two, created_two = runner.start(sha, "restoration", {"kind": "restoration", "exposureEv": 0.5})
    joined, created_three = runner.start(sha, "restoration", {"kind": "restoration"})
    assert created_one and created_two and not created_three
    assert one["id"] != two["id"] and joined["id"] == one["id"]
    assert one["graphId"] != two["graphId"] and one["request"] == {"kind": "restoration"}
    script.release.set()
    for record in (one, two):
        assert runner.wait(record["id"], BOUND_S)["state"] == "succeeded"


# ---- P-JOB-8: each group in creation order, one at a time ----


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(arrivals=st.lists(st.tuples(st.sampled_from(["solve", "restoration"]), st.integers(0, 2)), min_size=1,
                         max_size=6))
@example(arrivals=[("solve", 0), ("solve", 1), ("solve", 2), ("restoration", 0)])
def test_p_job_8_each_group_starts_its_jobs_in_creation_order_and_runs_one_at_a_time(
        world: dict[str, Any], arrivals: list[tuple[str, int]]) -> None:
    order: list[tuple[str, str]] = []
    gate = threading.Event()
    active = {"solve": 0, "media": 0}
    peak = {"solve": 0, "media": 0}
    guard = threading.Lock()

    def body(group: str) -> Callable[[JobContext], KindResult]:
        def run(ctx: JobContext) -> KindResult:
            with guard:
                active[group] += 1
                peak[group] = max(peak[group], active[group])
                order.append((group, ctx.job_id))
            try:
                assert gate.wait(BOUND_S)
                if group == "solve":
                    return _publish_solution(ctx)
                return KindResult("succeeded", {"attemptId": f"att_{ctx.job_id[4:20]}",
                                                "variantId": f"var_{ctx.job_id[4:20]}"})
            finally:
                with guard:
                    active[group] -= 1
        return run

    world["kinds"]["solve"] = solve_kind(Script(), body("solve"))
    world["kinds"]["restoration"] = media_kind(Script(), body("media"))
    runner = world["runner"]()
    made: list[tuple[str, dict[str, Any]]] = []
    for n, (kind, upload) in enumerate(arrivals):
        request = {"kind": "restoration", "exposureEv": float(n)} if kind == "restoration" else None
        record, created = runner.start(world["shas"][upload], kind, request)
        if created:
            made.append(("solve" if kind == "solve" else "media", record))
    for group in ("solve", "media"):
        mine = [r for g, r in made if g == group]
        if not mine:
            continue
        _wait_state(runner, mine[0]["id"], ("running",))
        served = [runner.get(r["id"]) for r in mine]
        # The jobs that run before it: the one in the slot and the queued ones made earlier
        assert [s.get("queueAhead") for s in served[1:]] == list(range(1, len(mine)))
        assert "queueAhead" not in served[0]
        for r in mine[1:]:
            assert "queueAhead" not in _on_disk(world["store"], r["id"]), "a queue count is never stored"
    gate.set()
    for _, record in made:
        runner.wait(record["id"], BOUND_S)
    for group in ("solve", "media"):
        created_order = [r["id"] for g, r in made if g == group]
        assert [job for g, job in order if g == group] == created_order
        assert peak[group] <= 1
    for _, record in made:
        if record["kind"] == "solve":
            world["store"].upload_folder(record["sourceAssetSha256"]).joinpath(
                "solutions", f"{record['id']}.json").unlink()


def test_the_solve_and_media_groups_each_have_their_own_slot(world: dict[str, Any]) -> None:
    solve, media = Script(), Script()
    world["kinds"]["solve"] = solve_kind(solve)
    world["kinds"]["restoration"] = media_kind(media)
    runner = world["runner"]()
    runner.start(world["shas"][0], "solve")
    runner.start(world["shas"][1], "restoration", {"kind": "restoration"})
    assert solve.entered.wait(BOUND_S) and media.entered.wait(BOUND_S), "both groups run at once"
    solve.release.set()
    media.release.set()


# ---- REQ-JOB-4, P-JOB-4: cancel ----


def test_a_cancel_of_a_queued_job_cancels_it_at_once_and_its_work_never_runs(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    writes = Writes(monkeypatch)
    script = Script()
    world["kinds"]["solve"] = solve_kind(script)
    runner = world["runner"]()
    first, _ = runner.start(world["shas"][0], "solve")
    second, _ = runner.start(world["shas"][1], "solve")
    assert script.entered.wait(BOUND_S)
    record, outcome = runner.cancel(second["id"])
    assert outcome == "canceled" and record["state"] == "canceled"
    assert "startedAtUTC" not in record and "producer" not in record and "finishedAtUTC" in record
    assert _on_disk(world["store"], second["id"]) == record
    assert not world["store"].is_held(world["store"].lock_path(world["shas"][1], "solve"))
    script.release.set()
    runner.wait(first["id"], BOUND_S)
    assert script.runs == [first["id"]]
    assert [r["state"] for r in writes.of(second["id"])] == ["queued", "canceled"]


def test_a_cancel_of_a_running_job_is_recorded_then_stops_the_work_and_its_outputs_are_removed(
        world: dict[str, Any]) -> None:
    stopped = threading.Event()
    published: list[Path] = []

    def body(ctx: JobContext) -> KindResult:
        partial = ctx.partial("solution.json")
        partial.write_text(json.dumps(_solution_payload(ctx.sha256)), encoding="utf-8")
        ctx.publish(partial, ctx.solution_path())
        published.append(ctx.solution_path())
        left = ctx.partial("scratch")
        left.write_text("x", encoding="utf-8")
        published.append(left)
        while True:
            try:
                ctx.check_cancel()
            except JobStopped:
                stopped.set()
                raise
            time.sleep(0.01)

    script = Script()
    world["kinds"]["solve"] = solve_kind(script, body)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    assert script.entered.wait(BOUND_S)
    while len(published) < 2:
        time.sleep(0.01)
    asked, outcome = runner.cancel(record["id"])
    assert outcome == "requested" and asked["state"] == "running" and "cancelRequestedAtUTC" in asked
    assert stopped.wait(BOUND_S)
    ended = runner.wait(record["id"], BOUND_S)
    assert ended["state"] == "canceled" and ended["cancelRequestedAtUTC"] == asked["cancelRequestedAtUTC"]
    assert "solutionId" not in ended and "error" not in ended
    assert not any(p.exists() for p in published), "a canceled job keeps no result and no partial output"
    assert world["store"].solution_of(ended) is None


def test_a_cancel_after_the_commit_point_leaves_the_job_succeeded_with_its_cancel_time(world: dict[str, Any]) -> None:
    asked = threading.Event()
    go_on = threading.Event()

    def body(ctx: JobContext) -> KindResult:
        asked.set()
        assert go_on.wait(BOUND_S)
        return _publish_solution(ctx)  # the work ignores the cancel: it was past its last check

    script = Script()
    world["kinds"]["solve"] = solve_kind(script, body)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    assert asked.wait(BOUND_S)
    _, outcome = runner.cancel(record["id"])
    go_on.set()
    ended = runner.wait(record["id"], BOUND_S)
    assert outcome == "requested"
    assert ended["state"] == "succeeded" and "cancelRequestedAtUTC" in ended and ended["solutionId"]


def test_a_cancel_of_a_finished_job_changes_nothing(world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    ended = runner.wait(record["id"], BOUND_S)
    before = world["store"].record_path(record["id"]).read_bytes()
    answer, outcome = runner.cancel(record["id"])
    assert (answer, outcome) == (ended, "finished")
    assert world["store"].record_path(record["id"]).read_bytes() == before
    assert runner.cancel(new_job_id(world["shas"][1])) is None


def _child_pid() -> int:
    # Of this module, so a child that runs it has imported this module
    return os.getpid()


def _start_grandchild_then_sleep(seconds: float, report: Callable[..., None]) -> None:
    grandchild = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    report("grandchild", grandchild.pid, os.getpgid(0))
    time.sleep(seconds)


def _group_is_gone(pgid: int) -> bool:
    """The bounded worker tests' check, one for both files: an ended member not yet reaped counts as gone. Imported
    here, not at the top, so a slot worker, which imports this module again, does not import that one too."""
    from tests.science.test_bounded_worker import _group_is_gone as group_is_gone

    return group_is_gone(pgid)


def _until(check: Callable[[], bool], bound_s: float) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()


@pytest.mark.parametrize("how", ["cancel", "deadline"])
def test_p_job_4_5_a_cancel_or_the_bound_stops_the_child_and_every_process_it_started(
        world: dict[str, Any], how: str) -> None:
    seen: list[tuple] = []
    in_child = threading.Event()

    def body(ctx: JobContext) -> KindResult:
        def on_event(*event: Any) -> None:
            seen.append(event)
            in_child.set()
        ctx.child().call(_start_grandchild_then_sleep, (60.0,), timeout_s=min(120.0, ctx.remaining_s()),
                         progress_kwarg="report", on_event=on_event, stop=ctx.stop)
        return _publish_solution(ctx)

    limit = 4.0 if how == "deadline" else BOUND_S
    world["kinds"]["solve"] = solve_kind(Script(), body, time_limit_s=limit)
    runner = world["runner"]()
    # The child up first, and this module imported in it by a call of one of its functions (3.2 s on the laptop, more
    # under load on Linux, past the 4 s bound), so the bound stops the work, not a fresh child's start-up or import
    job_runner._SLOT_WORKERS["solve"].call(_child_pid, timeout_s=BOUND_S)
    started = time.monotonic()
    record, _ = runner.start(world["shas"][0], "solve")
    assert in_child.wait(BOUND_S)
    if how == "cancel":
        asked = time.monotonic()
        runner.cancel(record["id"])
    ended = runner.wait(record["id"], BOUND_S)
    if how == "cancel":
        assert ended["state"] == "canceled"
        assert time.monotonic() - asked < STOP_BOUND_S + 1.0
    else:
        assert (ended["state"], ended["error"]) == ("failed", TIME_LIMIT_SENTENCE)
        assert time.monotonic() - started < limit + STOP_BOUND_S + 2.0
    ((_, grandchild, pgid),) = seen
    assert _until(lambda: _group_is_gone(pgid), STOP_BOUND_S), "a process of the job's group is left"


def test_p_job_5_work_in_the_runner_thread_past_the_bound_fails_with_the_kinds_sentence(world: dict[str, Any]) -> None:
    def body(ctx: JobContext) -> KindResult:
        while True:
            ctx.check_cancel()
            time.sleep(0.01)

    world["kinds"]["solve"] = solve_kind(Script(), body, time_limit_s=0.5)
    runner = world["runner"]()
    started = time.monotonic()
    record, _ = runner.start(world["shas"][0], "solve")
    ended = runner.wait(record["id"], BOUND_S)
    assert (ended["state"], ended["error"]) == ("failed", TIME_LIMIT_SENTENCE)
    assert 0.5 <= time.monotonic() - started < 0.5 + 2.0
    assert "cancelRequestedAtUTC" not in ended


@pytest.mark.parametrize("kind_name, sentence", [("solve", TIME_LIMIT_SENTENCE), ("restoration", MEDIA_TIME_LIMIT)])
def test_a_child_call_past_its_own_bound_ends_the_job_with_the_kinds_time_limit_sentence(
        world: dict[str, Any], kind_name: str, sentence: str) -> None:
    """A kind bounds its child call a little inside the job's bound (the media kinds leave five seconds for the
    ending), so the child's bound passes while the job's has not: the job ran out of time all the same and ends with
    the kind's time-limit sentence, never the internal error one (platform-9; found in platform-5d)."""
    def body(ctx: JobContext) -> KindResult:
        ctx.child().call(time.sleep, (60.0,), timeout_s=1.0, stop=ctx.stop)
        raise AssertionError("the child's bound did not stop the call")

    world["kinds"][kind_name] = solve_kind(Script(), body) if kind_name == "solve" else media_kind(Script(), body)
    runner = world["runner"]()
    started = time.monotonic()
    record, _ = runner.start(world["shas"][0], kind_name, None if kind_name == "solve" else {"kind": "restoration"})
    ended = runner.wait(record["id"], BOUND_S)
    assert (ended["state"], ended["error"]) == ("failed", sentence)
    assert time.monotonic() - started < BOUND_S, "the job's own bound had not passed"


# ---- Failures ----


def test_a_refusal_ends_the_job_failed_with_its_sentence_and_an_error_with_the_kinds_own(
        world: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    fates = iter([JobRefusal(REFUSAL_SENTENCE), RuntimeError(SECRET), JobRefusal(f"Not a fixed sentence {SECRET}.")])

    def body(ctx: JobContext) -> KindResult:
        raise next(fates)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    errors = []
    with caplog.at_level(logging.DEBUG):
        for sha in world["shas"]:
            record, _ = runner.start(sha, "solve")
            errors.append(runner.wait(record["id"], BOUND_S))
    assert [(e["state"], e["error"]) for e in errors] == [
        ("failed", REFUSAL_SENTENCE), ("failed", INTERNAL_SENTENCE), ("failed", INTERNAL_SENTENCE)]
    assert SECRET not in caplog.text, "an exception's text is never logged"
    for record in errors:
        assert SECRET not in json.dumps(_on_disk(world["store"], record["id"]))


def test_a_result_the_kind_names_but_never_published_fails_the_job(world: dict[str, Any]) -> None:
    def body(ctx: JobContext) -> KindResult:
        return KindResult("succeeded", {"solutionId": f"sol_{ctx.sha256[:16]}"})

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    ended = runner.wait(record["id"], BOUND_S)
    assert (ended["state"], ended["error"]) == ("failed", INTERNAL_SENTENCE)


# ---- REQ-JOB-2: progress ----


def test_a_read_serves_the_latest_progress_and_the_disk_follows_at_milestones(world: dict[str, Any]) -> None:
    reported = threading.Event()
    hold = threading.Event()

    def body(ctx: JobContext) -> KindResult:
        ctx.progress("decode", "frames", 1, 1)
        for n in range(0, 51):
            ctx.progress("detect", "frames", n, 100)
        reported.set()
        assert hold.wait(BOUND_S)
        ctx.progress("detect", "frames", 100, 100)
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    assert reported.wait(BOUND_S)
    live = runner.get(record["id"])
    assert [s for s in live["stages"] if s["stage"] == "detect"] == [
        {"stage": "detect", "unit": "frames", "done": 50, "total": 100}]
    stored = _on_disk(world["store"], record["id"])
    assert {s["stage"]: s for s in stored["stages"]}["decode"]["done"] == 1, "a stage's end is written at once"
    hold.set()
    ended = runner.wait(record["id"], BOUND_S)
    assert {s["stage"]: s for s in ended["stages"]}["detect"] == {
        "stage": "detect", "unit": "frames", "done": 100, "total": 100}


def test_a_stage_or_unit_the_kind_does_not_declare_or_a_count_past_its_total_is_an_error(world: dict[str, Any]) -> None:
    wrong = iter([("encode", "frames", 1, 1), ("detect", "bytes", 1, 1), ("detect", "frames", 3, 2),
                  ("detect", "frames", -1, 2)])

    def body(ctx: JobContext) -> KindResult:
        ctx.progress(*next(wrong))
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    for _ in range(4):
        sha = world["shas"][0]
        record, _ = runner.start(sha, "solve")
        ended = runner.wait(record["id"], BOUND_S)
        assert (ended["state"], ended["error"]) == ("failed", INTERNAL_SENTENCE)
        assert all(s["stage"] in ("store",) for s in ended["stages"])


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(steps=st.lists(st.one_of(
    st.tuples(st.just("pass"), st.sampled_from(["phone_photo_hinted", "phone_photo_generic", "triangle"])),
    st.tuples(st.just("count"), st.integers(0, 40), st.integers(1, 40)),
), min_size=1, max_size=12))
@example(steps=[("count", 5, 10), ("pass", "triangle"), ("count", 2, 10)])
@example(steps=[("count", 5, 10), ("count", 2, 10)])
def test_p_job_7_done_falls_only_when_the_pass_number_rises(world: dict[str, Any], steps: list[tuple]) -> None:
    served: list[dict[str, Any]] = []

    def body(ctx: JobContext) -> KindResult:
        number = 0
        for step in steps:
            if step[0] == "pass":
                number += 1
                ctx.pass_("solve", number, step[1])
            else:
                _, done, total = step
                ctx.progress("solve", "candidates", min(done, total), total)
            served.append(copy.deepcopy(ctx.live()))
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    sha = world["shas"][0]
    record, _ = runner.start(sha, "solve")
    ended = runner.wait(record["id"], BOUND_S)
    previous: Optional[dict[str, Any]] = None
    for snapshot in [s for s in served] + ([ended] if ended["state"] == "succeeded" else []):
        UploadJob.model_validate(snapshot)
        stage = next((s for s in snapshot["stages"] if s["stage"] == "solve"), None)
        if stage is None:
            continue
        assert stage["unit"] in ("candidates", "steps")
        assert stage["total"] is None or stage["done"] <= stage["total"]
        if previous is not None and stage["done"] < previous["done"]:
            assert stage.get("pass", {}).get("number", 0) > previous.get("pass", {}).get("number", 0)
        previous = stage
    if ended["state"] == "succeeded":
        world["store"].upload_folder(sha).joinpath("solutions", f"{record['id']}.json").unlink()
    else:
        assert ended["error"] == INTERNAL_SENTENCE, "only a count that falls within one pass is refused"


# ---- REQ-JOB-3: every job ends ----


def _running_record(sha: str, store: JobStore) -> dict[str, Any]:
    record = {"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "running",
              "createdAtUTC": "2030-01-01T05:00:00Z", "startedAtUTC": "2030-01-01T05:00:01Z",
              "producer": {"name": "albedian.test_solve", "revision": 1},
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    return store.write_record(record)


def test_a_read_that_finds_a_running_job_no_process_holds_stores_it_interrupted(world: dict[str, Any]) -> None:
    world["kinds"]["solve"] = solve_kind(Script())
    store, sha = world["store"], world["shas"][0]
    made = _running_record(sha, store)
    served = world["runner"]().get(made["id"])
    assert served["state"] == "interrupted" and served["error"] == INTERRUPTED_SENTENCE
    assert _on_disk(store, made["id"]) == served


def test_after_a_restart_queued_jobs_run_in_creation_order_and_running_ones_end_interrupted(
        world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    store = world["store"]
    queued = []
    for n, sha in enumerate(reversed(world["shas"])):
        record = {"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "queued",
                  "createdAtUTC": f"2030-01-01T05:00:0{n}Z", "stages": []}
        queued.append(store.write_record(record))
    running = _running_record(world["shas"][0], store)
    runner = world["runner"]()
    runner.recover()
    for record in queued:
        assert runner.wait(record["id"], BOUND_S)["state"] == "succeeded"
    assert script.runs == [r["id"] for r in queued], "handed back in creation order"
    assert store.read_job(running["id"])["state"] == "interrupted"


def test_a_start_that_finds_an_unheld_queued_job_of_its_key_joins_and_runs_it(world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    store, sha = world["store"], world["shas"][0]
    left = store.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "queued",
                               "createdAtUTC": "2030-01-01T05:00:00Z", "stages": []})
    runner = world["runner"]()
    record, created = runner.start(sha, "solve")
    assert (record["id"], created) == (left["id"], False)
    assert runner.wait(left["id"], BOUND_S)["state"] == "succeeded"


# ---- P-JOB-1: every change of state is a row of the transition table ----

ALLOWED = {
    (None, "queued"), ("queued", "running"), ("queued", "canceled"), ("running", "running"),
    ("running", "succeeded"), ("running", "failed"), ("running", "canceled"), ("running", "interrupted"),
}
FATES = ["succeed", "refuse", "raise", "cancel_queued", "cancel_running", "cancel_after_commit", "deadline",
         "bad_progress"]
ENDS = {"succeed": "succeeded", "refuse": "failed", "raise": "failed", "cancel_queued": "canceled",
        "cancel_running": "canceled", "cancel_after_commit": "succeeded", "deadline": "failed", "bad_progress": "failed"}


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(fates=st.lists(st.sampled_from(FATES), min_size=1, max_size=5))
@example(fates=FATES)
def test_p_job_1_every_state_change_is_a_row_of_the_table_and_ended_records_never_change(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fates: list[str]) -> None:
    writes = Writes(monkeypatch)
    # The solve group runs one job at a time in creation order, so each run takes the next entry
    todo: list[tuple[str, threading.Event, threading.Event]] = []
    todo_guard = threading.Lock()
    limit = [BOUND_S]

    def body(ctx: JobContext) -> KindResult:
        with todo_guard:
            fate, ready, go = todo.pop(0)
        ready.set()
        if fate in ("cancel_running", "deadline"):
            while True:
                ctx.check_cancel()
                time.sleep(0.005)
        assert go.wait(BOUND_S)
        if fate == "refuse":
            raise JobRefusal(REFUSAL_SENTENCE)
        if fate == "raise":
            raise RuntimeError(SECRET)
        if fate == "bad_progress":
            ctx.progress("detect", "frames", 5, 4)
        ctx.progress("decode", "frames", 1, 1)
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    world["kinds"]["solve"] = JobKind(**{**world["kinds"]["solve"].__dict__,
                                        "time_limit_s": lambda asset, request: limit[0]})
    runner, shas = world["runner"](), world["shas"]

    def clear() -> None:
        for sha in shas:
            for path in world["store"].upload_folder(sha).joinpath("solutions").glob("*.json"):
                path.unlink()

    def fresh(sha: str, fate: Optional[str]) -> tuple[dict[str, Any], threading.Event, threading.Event]:
        ready, go = threading.Event(), threading.Event()
        if fate is not None:
            with todo_guard:
                todo.append((fate, ready, go))
        record, created = runner.start(sha, "solve")
        assert created, "every example clears the results it made, so each start makes a new job"
        return record, ready, go

    clear()
    for n, fate in enumerate(fates):
        sha = shas[n % 3]
        limit[0] = 0.3 if fate == "deadline" else BOUND_S
        if fate == "cancel_queued":
            ahead, _, hold = fresh(shas[(n + 1) % 3], "succeed")
            record, _, _ = fresh(sha, None)
            assert runner.cancel(record["id"])[1] == "canceled"
            hold.set()
            runner.wait(ahead["id"], BOUND_S)
        else:
            record, ready, go = fresh(sha, fate)
            if fate in ("cancel_running", "cancel_after_commit"):
                assert ready.wait(BOUND_S)
                assert runner.cancel(record["id"])[1] == "requested"
            go.set()
        ended = runner.wait(record["id"], BOUND_S)
        assert ended["state"] == ENDS[fate], (fate, ended)
        clear()
    for job_id in {r["id"] for r in writes.records}:
        states = [r["state"] for r in writes.of(job_id)]
        for before, after in zip([None, *states], states):
            assert (before, after) in ALLOWED, (job_id, states)
        for record in writes.of(job_id):
            UploadJob.model_validate(record)
        ends = [n for n, s in enumerate(states) if s not in ("queued", "running")]
        assert len(ends) == 1 and ends[0] == len(states) - 1, "an ended record is written once and never changes"


def test_an_ending_record_that_cannot_be_written_fails_the_job_with_the_kinds_storing_sentence(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script()
    script.release.set()
    kind = solve_kind(script)
    world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "store_failure_reason": "The job's result could not be stored."})
    real = JobStore.write_record
    refused = [0]

    def refuse_the_commit(store: JobStore, payload: dict[str, Any]) -> dict[str, Any]:
        if payload["state"] == "succeeded":
            refused[0] += 1
            raise OSError(28, "No space left on device")
        return real(store, payload)

    monkeypatch.setattr(JobStore, "write_record", refuse_the_commit)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    ended = runner.wait(record["id"], BOUND_S)
    assert refused[0] == 1
    assert (ended["state"], ended["error"]) == ("failed", "The job's result could not be stored.")
    assert not runner.store.upload_folder(world["shas"][0]).joinpath("solutions", f"{record['id']}.json").exists()


# ---- Closing the first mutant round's survivors ----


def test_a_start_that_joins_a_job_of_this_process_answers_its_live_record(world: dict[str, Any]) -> None:
    reported, hold = threading.Event(), threading.Event()

    def body(ctx: JobContext) -> KindResult:
        ctx.progress("decode", "frames", 1, 1)
        for n in range(0, 51):
            ctx.progress("detect", "frames", n, 100)
        reported.set()
        assert hold.wait(BOUND_S)
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner, sha = world["runner"](), world["shas"][0]
    record, _ = runner.start(sha, "solve")
    assert reported.wait(BOUND_S)
    joined, created = runner.start(sha, "solve")
    assert not created and joined["id"] == record["id"]
    assert {s["stage"]: s for s in joined["stages"]}["detect"]["done"] == 50, "the live count, not the one on disk"
    stored = {s["stage"]: s for s in _on_disk(world["store"], record["id"])["stages"]}
    assert stored.get("detect", {"done": 0})["done"] < 50, "a stage that advances is written at most once a second"
    hold.set()
    runner.wait(record["id"], BOUND_S)


def test_work_that_returns_after_its_bound_still_fails_with_the_time_limit(world: dict[str, Any]) -> None:
    def body(ctx: JobContext) -> KindResult:
        time.sleep(0.6)  # past the bound, between two checks
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body, time_limit_s=0.3)
    runner, sha = world["runner"](), world["shas"][0]
    record, _ = runner.start(sha, "solve")
    ended = runner.wait(record["id"], BOUND_S)
    assert (ended["state"], ended["error"]) == ("failed", TIME_LIMIT_SENTENCE)
    assert not world["store"].upload_folder(sha).joinpath("solutions", f"{record['id']}.json").exists()


def test_a_pass_number_that_does_not_rise_fails_the_job(world: dict[str, Any]) -> None:
    def body(ctx: JobContext) -> KindResult:
        ctx.pass_("solve", 1, "phone_photo_hinted")
        ctx.pass_("solve", 1, "phone_photo_generic")
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner = world["runner"]()
    record, _ = runner.start(world["shas"][0], "solve")
    assert (runner.wait(record["id"], BOUND_S)["error"]) == INTERNAL_SENTENCE


def test_jobs_made_in_one_millisecond_still_have_a_total_creation_order(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = job_runner._now()
    monkeypatch.setattr(job_runner, "_now", lambda: frozen)
    world["kinds"]["solve"] = solve_kind(Script())
    runner = world["runner"]()
    made = [runner.start(sha, "solve")[0] for sha in world["shas"]]
    stamps = [datetime.fromisoformat(r["createdAtUTC"].replace("Z", "+00:00")) for r in made]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


def test_a_record_handed_back_runs_only_while_it_is_still_queued(world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    store, sha = world["store"], world["shas"][0]
    queued = store.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "queued",
                                 "createdAtUTC": "2030-01-01T05:00:00Z", "stages": []})
    store.write_record({**queued, "state": "canceled", "finishedAtUTC": "2030-01-01T05:00:01Z"})
    runner = world["runner"]()
    with runner._cond:
        assert runner._hand_back(queued) is None
    assert script.runs == [] and not store.is_held(store.lock_of(queued))


def test_runners_of_two_store_roots_share_one_child_per_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One process keeps one child per group however many store roots it serves (tests make one per test), as it kept
    one solve child before the runner: each child holds the solvers and their star tiles in memory."""
    pids: list[int] = []

    def body(ctx: JobContext) -> KindResult:
        pids.append(ctx.child().call(os.getpid, timeout_s=BOUND_S, stop=ctx.stop))
        raise JobRefusal(REFUSAL_SENTENCE)

    monkeypatch.setattr(job_runner, "_KINDS", {"solve": solve_kind(Script(), body)})
    runners = []
    for name in ("one", "two"):
        sha = _upload(tmp_path / name, 0)
        runner = runner_for(IngestStore(tmp_path / name, max_bytes=10_000_000))
        runners.append(runner)
        record, _ = runner.start(sha, "solve")
        assert runner.wait(record["id"], BOUND_S)["state"] == "failed"
    assert len(pids) == 2 and pids[0] == pids[1], "a second root's runner started a second child"
    for runner in runners:
        runner.shutdown()


def test_a_call_waiting_for_the_shared_child_stops_waiting_when_its_job_is_stopped() -> None:
    from services.science.bounded_worker import WorkerStopped

    worker = job_runner.SlotWorker()
    stop = threading.Event()
    outcome: list[BaseException] = []

    def call() -> None:
        try:
            worker.call(os.getpid, timeout_s=BOUND_S, stop=stop)
        except BaseException as error:  # noqa: BLE001 - what it raises is the test
            outcome.append(error)

    assert worker._lock.acquire(timeout=1.0), "another root's job holds the child"
    try:
        waiter = threading.Thread(target=call, daemon=True)
        waiter.start()
        waiter.join(0.5)
        assert waiter.is_alive(), "it waits while the child is busy"
        stop.set()
        waiter.join(2.0)
        assert not waiter.is_alive() and len(outcome) == 1 and isinstance(outcome[0], WorkerStopped)
    finally:
        worker._lock.release()
        worker.close()


# ---- platform-6: results know which code made them (REQ-JOB-6.3 to 6.5) ----


def _succeeded_of(store: JobStore, sha: str, producer: Optional[dict[str, Any]], created: str) -> dict[str, Any]:
    """A succeeded solve of `sha` made by `producer`, with its solution published where the store reads it."""
    job_id = new_job_id(sha)
    record = {"id": job_id, "sourceAssetSha256": sha, "kind": "solve", "state": "succeeded", "createdAtUTC": created,
              "startedAtUTC": created, "finishedAtUTC": created, "solutionId": f"sol_{sha[:16]}",
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    if producer is not None:
        record["producer"] = producer
    written = store.write_record(record)
    folder = store.upload_folder(sha) / "solutions"
    folder.mkdir(mode=0o700, exist_ok=True)
    (folder / f"{job_id}.json").write_text(json.dumps(_solution_payload(sha)), encoding="utf-8")
    return written


@pytest.mark.parametrize(("producer", "runs_again"), [
    ({"name": "albedian.test_solve", "revision": 1}, True),
    ({"name": "albedian.test_solve", "revision": 2}, False),
    ({"name": "albedian.test_solve", "revision": 3}, False),
    ({"name": "albedian.other_chain", "revision": 2}, True),
])
def test_a_succeeded_solve_of_an_older_revision_or_another_producer_runs_again_and_a_current_one_is_answered(
        world: dict[str, Any], producer: dict[str, Any], runs_again: bool) -> None:
    script = Script()
    script.release.set()
    kind = solve_kind(script)
    world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "producer": Producer("albedian.test_solve", 2)})
    store, sha = world["store"], world["shas"][0]
    stored = _succeeded_of(store, sha, producer, "2030-01-01T05:00:00Z")
    record, created = world["runner"]().start(sha, "solve")
    assert created is runs_again
    assert (record["id"] == stored["id"]) is not runs_again


def test_a_solve_todays_code_ran_names_no_producer_and_runs_again(world: dict[str, Any]) -> None:
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    store, sha = world["store"], world["shas"][0]
    folder = store.upload_folder(sha)
    legacy = {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "succeeded",
              "createdAtUTC": "2020-01-01T05:00:00Z", "startedAtUTC": "2020-01-01T05:00:01Z",
              "finishedAtUTC": "2020-01-01T05:00:02Z", "solutionId": f"sol_{sha[:16]}",
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    (folder / "job.json").write_text(json.dumps(legacy), encoding="utf-8")
    (folder / "solution.json").write_text(json.dumps(_solution_payload(sha)), encoding="utf-8")
    record, created = world["runner"]().start(sha, "solve")
    assert created and record["id"] != legacy["id"]
    assert world["runner"]().wait(record["id"], BOUND_S)["state"] == "succeeded"


def test_the_store_counts_the_uploads_whose_newest_solve_is_current_stale_or_from_todays_code(
        world: dict[str, Any]) -> None:
    store, (current, stale, legacy) = world["store"], world["shas"]
    producer = Producer("albedian.test_solve", 2)
    _succeeded_of(store, current, {"name": "albedian.test_solve", "revision": 1}, "2030-01-01T05:00:00Z")
    _succeeded_of(store, current, {"name": "albedian.test_solve", "revision": 2}, "2030-01-01T05:00:01Z")
    # The current upload's older solve was stale; the stale upload has only a stale one, so counting any but the
    # newest succeeded solve of each upload moves the counts
    _succeeded_of(store, stale, {"name": "albedian.test_solve", "revision": 1}, "2030-01-01T05:00:01Z")
    folder = store.upload_folder(legacy)
    (folder / "job.json").write_text(json.dumps({
        "id": f"job_{legacy[:16]}", "sourceAssetSha256": legacy, "state": "succeeded",
        "createdAtUTC": "2020-01-01T05:00:00Z", "startedAtUTC": "2020-01-01T05:00:01Z",
        "finishedAtUTC": "2020-01-01T05:00:02Z", "solutionId": f"sol_{legacy[:16]}",
        "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}), encoding="utf-8")
    assert job_runner.count_stale(world["uploads"], "solve", producer) == {"current": 1, "stale": 1, "no_producer": 1}
    assert job_runner.count_stale(world["uploads"], "solve", Producer("albedian.test_solve", 1)) == {
        "current": 2, "stale": 0, "no_producer": 1}


def test_the_count_judges_the_solve_an_upload_serves_not_a_newer_aligned_one_that_did_not_calibrate(
        world: dict[str, Any]) -> None:
    """platform-12, with align-2v's served solve: a plain solve that calibrated at revision 1, then a solve from a
    person's alignment at revision 2 whose gate refused. The upload serves the plain one, so a count for revision 2
    calls the upload stale, as a re-solve would change what it serves; the newest succeeded solve would say current."""
    from tests.integration.test_aligned_solution_served import ALIGNMENT, _publishing  # noqa: PLC0415

    sha = world["shas"][0]
    kind = solve_kind(Script(), _publishing(["calibrated", "predicted_only_with_a_persons_alignment"]))
    for revision, request in ((1, None), (2, {**ALIGNMENT})):
        world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "producer": Producer(kind.producer.name, revision)})
        job, created = world["runner"]().start(sha, "solve", request)
        assert created and world["runner"]().wait(job["id"], BOUND_S)["state"] == "succeeded"
    served = world["store"].served_solve(sha)
    assert served["producer"]["revision"] == 1 and served.get("request") is None
    assert job_runner.count_stale(world["uploads"], "solve", Producer(kind.producer.name, 2)) == {
        "current": 0, "stale": 1, "no_producer": 0}


def test_while_a_newer_solve_runs_the_last_good_solution_is_still_served(world: dict[str, Any]) -> None:
    script = Script()
    kind = solve_kind(script)
    world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "producer": Producer("albedian.test_solve", 2)})
    store, sha = world["store"], world["shas"][0]
    older = _succeeded_of(store, sha, {"name": "albedian.test_solve", "revision": 1}, "2020-01-01T05:00:00Z")
    runner = world["runner"]()
    newer, created = runner.start(sha, "solve")
    assert created and script.entered.wait(BOUND_S)
    assert store.read_solution(sha) == _solution_payload(sha), "the older solve's solution, while the newer runs"
    assert store.newest_solve(sha)["id"] == newer["id"] != older["id"], "the receipt names the solve that runs"
    script.release.set()
    assert runner.wait(newer["id"], BOUND_S)["state"] == "succeeded"
    assert store.newest_solve(sha)["id"] == newer["id"]


# ---- platform-7 found: the live record ended before its commit was on disk ----


def test_a_read_never_serves_an_ended_record_before_it_is_written(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The commit point is the write of the ending record: a reader that is served `succeeded` and then reads the
    solution must find it, so the live record ends only once the store has it. The ending write is slowed here so
    any earlier serving of it is seen."""
    script = Script()
    script.release.set()
    world["kinds"]["solve"] = solve_kind(script)
    real = JobStore.write_record
    writing = threading.Event()

    def slow_ending(store: JobStore, payload: dict[str, Any]) -> dict[str, Any]:
        if payload["state"] not in ("queued", "running"):
            writing.set()
            time.sleep(0.5)
        return real(store, payload)

    monkeypatch.setattr(JobStore, "write_record", slow_ending)
    runner, sha = world["runner"](), world["shas"][0]
    record, _ = runner.start(sha, "solve")
    assert writing.wait(BOUND_S)
    deadline = time.monotonic() + BOUND_S
    while time.monotonic() < deadline:
        served = runner.get(record["id"])
        if served["state"] == "succeeded":
            assert world["store"].read_solution(sha) is not None, "succeeded was served before its commit was on disk"
            assert world["store"].read_job(record["id"])["state"] == "succeeded"
            break
        time.sleep(0.01)
    else:
        raise AssertionError("the job never ended")


def test_a_job_whose_runner_stops_builds_no_ending_and_leaves_its_record_for_the_next_start(
        world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """At shutdown a running job's work stops, and no ending is built: the record stays running for the next start's
    sweep, and nothing reads the clock for a job that will not end here (one did, after the next test had set its own
    clock, and moved that test's stamps)."""
    script = Script()

    def body(ctx: JobContext) -> KindResult:
        script.entered.set()
        while True:
            ctx.check_cancel()
            time.sleep(0.01)

    world["kinds"]["solve"] = solve_kind(script, body)
    runner, sha = world["runner"](), world["shas"][0]
    record, _ = runner.start(sha, "solve")
    assert script.entered.wait(BOUND_S)
    reads: list[datetime] = []
    real_now = job_runner._now
    monkeypatch.setattr(job_runner, "_now", lambda: reads.append(real_now()) or reads[-1])
    job = runner._jobs[record["id"]]
    runner.shutdown()
    assert job.ended.wait(BOUND_S), "the stopped job's slot let it go"
    assert reads == [], "nothing read the clock for a job that does not end here"
    assert _on_disk(world["store"], record["id"])["state"] == "running"


@pytest.mark.parametrize("error", [KeyError("a key"), TypeError("findings is not a list")])
def test_an_error_while_a_job_ends_fails_that_job_and_the_group_runs_the_next(
        world: dict[str, Any], error: Exception) -> None:
    """A kind's results check that raises (a damaged stored solution makes the solve's raise TypeError) fails its one
    job with the kind's internal-error sentence; the lock and the group's slot are freed, so the same upload starts a
    new job and another upload's solve runs to its end (AUDIT-B's read of platform-7b)."""
    script = Script()
    script.release.set()
    kind = solve_kind(script)
    checks = {"raise": True}

    def results_valid(store: JobStore, record: dict[str, Any]) -> bool:
        if checks["raise"]:
            raise error
        return store.solution_of(record) is not None

    world["kinds"]["solve"] = JobKind(**{**kind.__dict__, "results_valid": results_valid})
    runner, (sha_a, sha_b, _) = world["runner"](), world["shas"]
    first, _ = runner.start(sha_a, "solve")
    ended = runner.wait(first["id"], BOUND_S)
    assert (ended["state"], ended["error"]) == ("failed", INTERNAL_SENTENCE)
    checks["raise"] = False
    again, created = runner.start(sha_a, "solve")
    assert created and again["id"] != first["id"]
    other, _ = runner.start(sha_b, "solve")
    assert runner.wait(again["id"], BOUND_S)["state"] == "succeeded"
    assert runner.wait(other["id"], BOUND_S)["state"] == "succeeded", "the group's slot runs the next job"


# ---- platform-8: a media job its own check refuses ends rejected, naming the attempt that says why ----

# A restoration the gate refused, as the media contract's fixture holds it (its `rejection` is the reason)
REJECTED_ATTEMPT = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "media_contract.json")
                              .read_text(encoding="utf-8"))["valid"]["ProcessingAttempt"][4]


class InProcessChild:
    """The media group's child as the kinds call it, run in this process, so a test's stand-in reaches the step."""

    def call(self, target: Callable[..., dict], args: tuple, *, timeout_s: float, stop: threading.Event,
             progress_kwarg: str, on_event: Callable[..., None]) -> dict:
        return target(*args, **{progress_kwarg: on_event})


def _keep_rejected_restoration(store_root: str, folder: str, original: str, sha256: str, job_id: str, request: dict,
                               orientation: Optional[dict], progress: Callable[..., None]) -> dict:
    """Stands in for the restoration's step at its rejected end (its gate is RESTORE-PHOTO's): keeps the attempt the
    gate refused, made this upload's and this job's, and answers as the step does."""
    attempt = copy.deepcopy(REJECTED_ATTEMPT)
    graph = media_variants.graph_id(sha256, "restoration", request)
    attempt.update(id=media_variants.attempt_id(graph, attempt["startedAtUTC"]), sourceSha256=sha256, graphId=graph,
                   jobId=job_id)
    media_variants.keep_attempt(Path(folder), attempt)
    return {"status": "rejected", "attemptId": attempt["id"]}


@pytest.fixture
def media_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API on a store of its own, with the real media kinds and their child's step called in this process."""
    monkeypatch.setattr(JobContext, "child", lambda self: InProcessChild())
    uploads = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    with TestClient(app) as client:
        client.uploads = uploads  # type: ignore[attr-defined]
        yield client
    runner_for(uploads).shutdown()


def _media_upload(client: TestClient, n: int) -> str:
    answer = client.post("/api/ingest", content=_png(n), headers={"content-type": "image/png"})
    assert answer.status_code in (200, 201), answer.text
    return answer.json()["sourceAsset"]["sha256"]


def _followed(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + BOUND_S
    while job["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the job did not end within the test's bound"
        time.sleep(0.02)
        answer = client.get(f"/api/ingest/jobs/{job['id']}")
        assert answer.status_code == 200, answer.text
        job = answer.json()
    return job


@pytest.mark.parametrize("kind, rejection", [("display_copy", "copy_differs"), ("restoration", "unconfirmed_points")])
def test_a_media_job_its_own_check_refuses_ends_rejected_naming_the_attempt_that_says_why(
        media_client: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str, rejection: str) -> None:
    """AUDIT's finding on main d9134b6b: a refused display copy or restoration keeps its attempt as rejected, yet its
    job ended failed with the internal error sentence, because the commit asked for a variant a rejected job never
    has. The display copy's own comparison refuses here (it is told the copy differs)."""
    monkeypatch.setattr(media_variants, "verify_copy", lambda data, samples: False)
    monkeypatch.setattr(media_jobs, "restoration_in_child", _keep_rejected_restoration)
    sha = _media_upload(media_client, 7)
    started = media_client.post(f"/api/media/{sha}/restore", json={"kind": kind})
    assert started.status_code == 202, started.text
    ended = _followed(media_client, started.json())
    assert (ended["state"], ended.get("error"), ended.get("variantId")) == ("rejected", None, None)
    UploadJob.model_validate(ended)
    assert _on_disk(JobStore(media_client.uploads), ended["id"]) == ended  # type: ignore[attr-defined]
    listing = media_client.get(f"/api/media/{sha}/variants").json()
    assert listing["variants"] == []
    [attempt] = [a for a in listing["attempts"] if a["id"] == ended["attemptId"]]
    assert (attempt["status"], attempt["rejection"], attempt["jobId"]) == ("rejected", rejection, ended["id"])


def test_a_rejected_ending_that_names_an_attempt_never_kept_fails_with_the_kinds_sentence(
        media_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The commit still checks what a rejected ending names: an attempt that is not stored is no reason to serve."""
    monkeypatch.setattr(media_jobs, "restoration_in_child",
                        lambda *args, progress: {"status": "rejected", "attemptId": "att_" + "0" * 16})
    sha = _media_upload(media_client, 8)
    started = media_client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert started.status_code == 202, started.text
    ended = _followed(media_client, started.json())
    assert (ended["state"], ended["error"]) == ("failed", media_decode.INTERNAL_ERROR), ended
    assert "attemptId" not in ended


class TimedOutChild:
    """The media group's child as it answers a call that ran past the bound the kind gave it."""

    def call(self, target: Callable[..., dict], args: tuple, *, timeout_s: float, **options: Any) -> dict:
        raise WorkerTimeout(f"over {timeout_s:g} s")


@pytest.mark.parametrize("kind", ["display_copy", "restoration"])
def test_a_media_job_whose_child_passes_its_bound_ends_with_the_contracts_time_limit_sentence(
        media_client: TestClient, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """The two real media kinds: their child's bound comes five seconds before the job's, and a still that takes
    longer ends "The restoration stopped at its time limit.", the media contract's sentence (platform-9)."""
    monkeypatch.setattr(JobContext, "child", lambda self: TimedOutChild())
    sha = _media_upload(media_client, 9)
    started = media_client.post(f"/api/media/{sha}/restore", json={"kind": kind})
    assert started.status_code == 202, started.text
    ended = _followed(media_client, started.json())
    assert (ended["state"], ended["error"]) == ("failed", media_decode.TIME_LIMIT), ended
    assert media_decode.TIME_LIMIT == "The restoration stopped at its time limit."


# ---- platform-10b: the sky a solve predicts from a pose known before its search ends ----


def _approximate_with_pose(sha: str) -> dict[str, Any]:
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json")
                         .read_text(encoding="utf-8"))["valid"]["approximate_with_pose"]
    text = json.dumps(fixture)
    if fixture.get("sourceSha256"):
        text = text.replace(fixture["sourceSha256"], sha)
    out = json.loads(text.replace(fixture["sourceAssetId"], f"src_{sha[:16]}"))
    out["id"] = f"sol_{sha[:16]}"
    return out


def test_a_solve_publishes_its_predicted_sky_while_it_runs_and_its_record_says_when(world: dict[str, Any]) -> None:
    """REQ-JOB-2.7: the pose-placed sky is served while the search goes on, the record names its time on disk and
    live, keeps it when the job ends, and the sky itself goes with the job's other partial outputs."""
    published, release = threading.Event(), threading.Event()

    def body(ctx: JobContext) -> KindResult:
        ctx.predicted_sky(_approximate_with_pose(ctx.sha256))
        published.set()
        assert release.wait(BOUND_S), "the test never released the run"
        return _publish_solution(ctx)

    world["kinds"]["solve"] = solve_kind(Script(), body)
    runner, sha = world["runner"](), world["shas"][0]
    record, _ = runner.start(sha, "solve")
    assert published.wait(BOUND_S)
    live = runner.get(record["id"])
    UploadJob.model_validate(live)
    assert live["state"] == "running" and live["startedAtUTC"] <= live["predictedSkyAtUTC"]
    assert _on_disk(world["store"], record["id"])["predictedSkyAtUTC"] == live["predictedSkyAtUTC"]
    assert runner.predicted_sky(record["id"]) == _approximate_with_pose(sha)
    # A job whose ending is being written serves none, even before its partials are gone
    job = runner._jobs[record["id"]]
    job.ended.set()
    assert runner.predicted_sky(record["id"]) is None
    job.ended.clear()
    release.set()
    ended = runner.wait(record["id"], BOUND_S)
    assert ended["state"] == "succeeded" and ended["predictedSkyAtUTC"] == live["predictedSkyAtUTC"]
    UploadJob.model_validate(ended)
    assert runner.predicted_sky(record["id"]) is None
    partials = world["root"] / ".partial"
    assert not any(p.name.startswith(record["id"]) for p in (partials.iterdir() if partials.exists() else []))


def test_only_a_solve_publishes_a_predicted_sky_and_only_an_approximate_one_with_its_pose(world: dict[str, Any]) -> None:
    refusals: list[str] = []

    def body(ctx: JobContext) -> KindResult:
        # A whole solution the contract takes, but calibrated and with no pose, and no solution at all
        calibrated = _solution_payload(ctx.sha256)
        for sky in (calibrated, {"not": "a solution"}):
            try:
                ctx.predicted_sky(sky)
            except ValueError:
                refusals.append("refused")
        return _publish_solution(ctx)

    def media_body(ctx: JobContext) -> KindResult:
        try:
            ctx.predicted_sky(_approximate_with_pose(ctx.sha256))
        except ValueError:
            refusals.append("media refused")
        return KindResult("succeeded", {"attemptId": f"att_{ctx.job_id[4:20]}", "variantId": f"var_{ctx.job_id[4:20]}"})

    world["kinds"]["solve"] = solve_kind(Script(), body)
    world["kinds"]["restoration"] = media_kind(Script(), media_body)
    runner, sha = world["runner"](), world["shas"][0]
    solve, _ = runner.start(sha, "solve")
    media, _ = runner.start(sha, "restoration", {"kind": "restoration"})
    for ended in (runner.wait(solve["id"], BOUND_S), runner.wait(media["id"], BOUND_S)):
        assert ended["state"] == "succeeded" and "predictedSkyAtUTC" not in ended, ended
    assert sorted(refusals) == ["media refused", "refused", "refused"]
