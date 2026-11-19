"""The job routes (upload jobs design 3.6, platform-5; REQ-JOB-3.4, REQ-JOB-4, REQ-JOB-7.1 to 7.4).

`POST /api/ingest/{sha256}/solve?wait=false` answers at once, `GET /api/ingest/jobs/{job_id}` serves any job live,
`POST /api/ingest/jobs/{job_id}/cancel` stops one, and the waiting start keeps today's answer. Most tests replace the
solve kind with a fake one that waits on the test's events; the last ones run the real solve kind on a synthetic
star field and a synthetic clip.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import io
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import create_app
from services.contracts.ingest import IngestReceipt
from services.contracts.jobs import INTERRUPTED_SENTENCE, UploadJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.job_store import JobStore, new_job_id
from services.ingest.runner import JobContext, KindResult
from services.ingest.store import IngestStore

BOUND_S = 30.0
FFMPEG = shutil.which("ffmpeg")


def _png(luma: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _starfield(h: int = 120, w: int = 160, n: int = 9) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


class Fake:
    """A solve kind that waits for `release`, then publishes a solution of the upload's own id."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.runs: list[str] = []

    def run(self, ctx: JobContext) -> KindResult:
        self.runs.append(ctx.job_id)
        ctx.progress("decode", "frames", 1, 1)
        self.entered.set()
        while not self.release.is_set():
            ctx.check_cancel()
            time.sleep(0.01)
        partial = ctx.partial("solution.json")
        partial.write_text(json.dumps(_solution(ctx.sha256)), encoding="utf-8")
        ctx.publish(partial, ctx.solution_path())
        return KindResult("succeeded", {"solutionId": f"sol_{ctx.sha256[:16]}"})


def _solution(sha: str) -> dict[str, Any]:
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json")
                         .read_text(encoding="utf-8"))["valid"]["calibrated"]
    text = json.dumps(fixture).replace(fixture["sourceSha256"], sha).replace(fixture["sourceAssetId"], f"src_{sha[:16]}")
    return {**json.loads(text), "id": f"sol_{sha[:16]}"}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    fake = Fake()
    monkeypatch.setitem(job_runner._KINDS, "solve", dataclasses.replace(ingest_jobs.SOLVE_KIND, run=fake.run))
    client = TestClient(app)
    shas = []
    for n in range(2):
        body = _png(np.full((4, 4), 10.0 + n))
        assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
        shas.append(hashlib.sha256(body).hexdigest())
    yield {"client": client, "store": store, "jobs": JobStore(store), "fake": fake, "shas": shas, "app": app}
    fake.release.set()
    job_runner.runner_for(store).shutdown()


def _until_state(client: TestClient, job_id: str, states: tuple[str, ...]) -> dict[str, Any]:
    deadline = time.monotonic() + BOUND_S
    while time.monotonic() < deadline:
        answer = client.get(f"/api/ingest/jobs/{job_id}")
        assert answer.status_code == 200, answer.text
        if answer.json()["state"] in states:
            return answer.json()
        time.sleep(0.02)
    raise AssertionError(f"the job did not reach {states} in {BOUND_S:g} s")


def test_a_start_without_waiting_answers_202_then_joins_with_200_and_the_job_is_followed_live(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    first = client.post(f"/api/ingest/{sha}/solve?wait=false")
    assert first.status_code == 202, first.text
    job = first.json()
    UploadJob.model_validate(job)
    assert job["kind"] == "solve" and job["state"] in ("queued", "running")
    assert fake.entered.wait(BOUND_S)
    joined = client.post(f"/api/ingest/{sha}/solve?wait=false")
    assert joined.status_code == 200 and joined.json()["id"] == job["id"]
    live = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert live["state"] == "running" and {s["stage"] for s in live["stages"]} == {"store", "decode"}
    receipt = client.get(f"/api/ingest/{sha}").json()
    IngestReceipt.model_validate(receipt)
    assert receipt["solveJobId"] == job["id"], "the receipt names the newest attempt, active or not"
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 404
    fake.release.set()
    ended = _until_state(client, job["id"], ("succeeded",))
    assert ended["solutionId"] == f"sol_{sha[:16]}"
    current = client.post(f"/api/ingest/{sha}/solve?wait=false")
    assert (current.status_code, current.json()) == (200, ended)
    assert client.get(f"/api/ingest/{sha}/solution").json() == _solution(sha)
    assert fake.runs == [job["id"]]


def test_a_start_followed_to_its_end_then_answers_200_with_the_current_job(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    fake.release.set()
    status, ran = _solved(client, sha)
    assert status == 202 and ran["state"] == "succeeded"
    again = client.post(f"/api/ingest/{sha}/solve")
    assert (again.status_code, again.json()) == (200, ran)


def test_a_start_for_a_file_never_stored_is_404_and_makes_no_job(setup: dict[str, Any]) -> None:
    client = setup["client"]
    for query in ("", "?wait=false"):
        answer = client.post(f"/api/ingest/{'f' * 64}/solve{query}")
        assert answer.status_code == 404 and answer.json() == {"detail": "no file with that hash is stored"}
    assert setup["fake"].runs == []


def test_a_queued_job_is_served_with_the_jobs_ahead_of_it_and_canceled_at_once(setup: dict[str, Any]) -> None:
    client, (sha_a, sha_b), fake = setup["client"], setup["shas"], setup["fake"]
    running = client.post(f"/api/ingest/{sha_a}/solve?wait=false").json()
    assert fake.entered.wait(BOUND_S)
    queued = client.post(f"/api/ingest/{sha_b}/solve?wait=false")
    assert queued.status_code == 202 and queued.json()["state"] == "queued" and queued.json()["queueAhead"] == 1
    served = client.get(f"/api/ingest/jobs/{queued.json()['id']}").json()
    assert served["queueAhead"] == 1
    canceled = client.post(f"/api/ingest/jobs/{queued.json()['id']}/cancel")
    assert canceled.status_code == 200 and canceled.json()["state"] == "canceled"
    assert "queueAhead" not in canceled.json() and "startedAtUTC" not in canceled.json()
    again = client.post(f"/api/ingest/jobs/{queued.json()['id']}/cancel")
    assert (again.status_code, again.json()) == (200, canceled.json()), "a cancel of an ended job changes nothing"
    fake.release.set()
    _until_state(client, running["id"], ("succeeded",))
    assert fake.runs == [running["id"]]


def test_a_cancel_of_a_running_job_answers_202_and_the_job_ends_canceled(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    job = client.post(f"/api/ingest/{sha}/solve?wait=false").json()
    assert fake.entered.wait(BOUND_S)
    asked = client.post(f"/api/ingest/jobs/{job['id']}/cancel")
    assert asked.status_code == 202 and asked.json()["state"] == "running"
    assert "cancelRequestedAtUTC" in asked.json()
    ended = _until_state(client, job["id"], ("canceled",))
    assert ended["cancelRequestedAtUTC"] == asked.json()["cancelRequestedAtUTC"] and "solutionId" not in ended
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 404, "a canceled job serves no result"


def test_cancel_and_read_of_an_unknown_or_unreadable_job(setup: dict[str, Any]) -> None:
    client, sha, jobs = setup["client"], setup["shas"][0], setup["jobs"]
    unknown = new_job_id(sha)
    for answer in (client.get(f"/api/ingest/jobs/{unknown}"), client.post(f"/api/ingest/jobs/{unknown}/cancel")):
        assert answer.status_code == 404
    assert client.post("/api/ingest/jobs/not_a_job/cancel").status_code == 422
    made = jobs.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "queued",
                              "createdAtUTC": "2030-01-01T05:00:00Z", "stages": []})
    path = jobs.record_path(made["id"])
    path.write_text("{}", encoding="utf-8")
    for answer in (client.get(f"/api/ingest/jobs/{made['id']}"), client.post(f"/api/ingest/jobs/{made['id']}/cancel")):
        assert (answer.status_code, answer.json()) == (409, {"detail": ingest_router.UNREADABLE_JOB})
    assert path.read_text(encoding="utf-8") == "{}", "an unreadable record is never rewritten"


def test_a_cancel_of_todays_solve_record_changes_nothing(setup: dict[str, Any]) -> None:
    client, sha, store = setup["client"], setup["shas"][0], setup["store"]
    legacy = {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "failed",
              "createdAtUTC": "2020-01-01T05:00:00Z", "startedAtUTC": "2020-01-01T05:00:01Z",
              "finishedAtUTC": "2020-01-01T05:00:02Z", "error": "The solve stopped on an internal error.",
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    (store.folder(sha) / "job.json").write_text(json.dumps(legacy), encoding="utf-8")
    before = (store.folder(sha) / "job.json").read_bytes()
    answer = client.post(f"/api/ingest/jobs/{legacy['id']}/cancel")
    assert (answer.status_code, answer.json()) == (200, legacy)
    assert (store.folder(sha) / "job.json").read_bytes() == before


def test_a_cancel_with_a_body_is_refused(setup: dict[str, Any]) -> None:
    client, sha = setup["client"], setup["shas"][0]
    answer = client.post(f"/api/ingest/jobs/{new_job_id(sha)}/cancel", content=b"x")
    assert answer.status_code == 413


def test_a_running_job_no_process_holds_is_served_and_stored_interrupted(setup: dict[str, Any]) -> None:
    client, sha, jobs = setup["client"], setup["shas"][0], setup["jobs"]
    made = jobs.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "running",
                              "createdAtUTC": "2030-01-01T05:00:00Z", "startedAtUTC": "2030-01-01T05:00:01Z",
                              "producer": {"name": "albedian.solve_chain", "revision": 1}, "stages": []})
    served = client.get(f"/api/ingest/jobs/{made['id']}").json()
    assert (served["state"], served["error"]) == ("interrupted", INTERRUPTED_SENTENCE)
    assert jobs.read_job(made["id"]) == served


def test_the_app_runs_queued_jobs_at_start_and_leaves_a_running_one_for_the_next_start(
        setup: dict[str, Any]) -> None:
    app, store, jobs, fake, sha_a = setup["app"], setup["store"], setup["jobs"], setup["fake"], setup["shas"][0]
    left = jobs.write_record({"id": new_job_id(sha_a), "sourceAssetSha256": sha_a, "kind": "solve", "state": "queued",
                              "createdAtUTC": "2030-01-01T05:00:00Z", "stages": []})
    job_runner.runner_for(store).shutdown()
    with TestClient(app) as client:
        assert fake.entered.wait(BOUND_S), "the queued job the sweep handed back runs"
        assert client.get(f"/api/ingest/jobs/{left['id']}").json()["state"] == "running"
    # The server stopped while it ran: its record stays running, and the stopped slot frees its lock
    lock = jobs.lock_path(sha_a, "solve")
    deadline = time.monotonic() + BOUND_S
    while jobs.is_held(lock) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not jobs.is_held(lock)
    assert jobs.read_job(left["id"])["state"] == "running"
    with TestClient(app) as client:
        served = client.get(f"/api/ingest/jobs/{left['id']}").json()
    assert (served["state"], served["error"]) == ("interrupted", INTERRUPTED_SENTENCE)


# ---- The real solve kind ----


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setitem(job_runner._KINDS, "solve", ingest_jobs.SOLVE_KIND)
    store = IngestStore(tmp_path / "store", max_bytes=10_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    yield {"client": TestClient(app), "store": store}
    job_runner.runner_for(store).shutdown()


def test_the_solve_kind_records_its_producer_its_passes_and_publishes_its_solution(real: dict[str, Any]) -> None:
    client, store = real["client"], real["store"]
    body = _png(_starfield())
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    sha = hashlib.sha256(body).hexdigest()
    started = client.post(f"/api/ingest/{sha}/solve?wait=false")
    assert started.status_code == 202
    ended = _until_state(client, started.json()["id"], ("succeeded", "failed"))
    assert ended["state"] == "succeeded", ended.get("error")
    UploadJob.model_validate(ended)
    assert ended["producer"] == ingest_jobs.SOLVE_PRODUCER.as_record()
    solve = next(s for s in ended["stages"] if s["stage"] == "solve")
    assert solve["pass"]["label"] in ("triangle", "phone_photo_generic") and solve["pass"]["number"] >= 1
    assert "inputSpan" not in ended, "a still has no frame span"
    published = store.folder(sha) / "solutions" / f"{ended['id']}.json"
    assert published.is_file() and not (store.folder(sha) / "solution.json").exists()
    assert client.get(f"/api/ingest/{sha}/solution").json() == json.loads(published.read_text(encoding="utf-8"))


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_a_clip_solve_records_which_frames_it_decoded(real: dict[str, Any], tmp_path: Path) -> None:
    client = real["client"]
    clip = tmp_path / "three.mp4"
    raw = np.clip(_starfield(), 0, 255).astype(np.uint8).tobytes() * 3
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
         "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=raw, check=True, capture_output=True,
    )
    body = clip.read_bytes()
    stored = client.post("/api/ingest", content=body, headers={"content-type": "video/mp4"})
    assert stored.status_code == 201, stored.text
    asset = stored.json()["sourceAsset"]
    job = client.post(f"/api/ingest/{asset['sha256']}/solve?wait=false").json()
    ended = _until_state(client, job["id"], ("succeeded", "failed"))
    assert ended["state"] == "succeeded", ended.get("error")
    decoded = next(s for s in ended["stages"] if s["stage"] == "decode")["done"]
    fps = asset["fpsNumerator"] / asset["fpsDenominator"]
    assert ended["inputSpan"]["firstFrameIndex"] == 0 and ended["inputSpan"]["frameCount"] == decoded >= 2
    assert ended["inputSpan"]["startSec"] == 0
    assert abs(ended["inputSpan"]["endSec"] - (decoded - 1) / fps) < 1e-9


def test_a_decoder_failure_is_stored_as_the_kinds_fixed_sentence(
        real: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = real["client"], real["store"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()
    original = str(store.original(sha))

    def decode_fails(*args: Any, **kwargs: Any) -> Any:
        raise ingest_jobs.SolveJobError(f"The decoder could not read the file: {original}: Invalid data")

    monkeypatch.setattr(ingest_jobs, "_decode_luma_frames", decode_fails)
    ended = _solved(client, sha)[1]
    assert (ended["state"], ended["error"]) == ("failed", "The decoder could not read the file.")
    assert original not in json.dumps(ended)


def test_a_solve_past_its_whole_bound_says_it_stopped_at_its_time_limit(
        real: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = real["client"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(ingest_jobs, "SOLVE_JOB_TIME_LIMIT_SEC", 0.0)
    ended = _solved(client, sha)[1]
    assert (ended["state"], ended["error"]) == ("failed", "The solve stopped at its time limit.")
    assert client.get(f"/api/ingest/{sha}/solution").status_code == 404


def test_a_solution_that_cannot_be_written_says_the_result_could_not_be_stored_and_leaves_nothing(
        real: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = real["client"], real["store"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()

    def full_disk(path: Path, payload: dict[str, Any]) -> None:
        path.write_text("{", encoding="utf-8")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ingest_jobs, "_write_partial", full_disk)
    ended = _solved(client, sha)[1]
    assert (ended["state"], ended["error"]) == ("failed", "The job's result could not be stored.")
    assert not (store.folder(sha) / "solutions" / f"{ended['id']}.json").exists()
    assert [p.name for p in (Path(store.root) / ".partial").iterdir() if p.name.startswith(ended["id"])] == []


# ---- Closing the second mutant round's survivors ----


def _camera_png(focal_35mm: int | None) -> bytes:
    """A star field tagged with a camera no published field of view names, with or without its 35 mm focal length."""
    exif = Image.Exif()
    exif[0x010F] = "Testmaker"
    exif[0x0110] = "Testcam One"
    if focal_35mm is not None:
        exif.get_ifd(0x8769)[0xA405] = focal_35mm
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(_starfield()), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG", exif=exif.tobytes())
    return buf.getvalue()


@pytest.mark.parametrize(("focal", "labels"), [
    (26, ["phone_photo_hinted", "phone_photo_generic", "triangle"]),
    (None, ["phone_photo_generic", "phone_photo_generic", "triangle"]),
])
def test_only_the_first_phone_search_of_a_tagged_upload_with_a_35mm_focal_length_is_hinted(
        real: dict[str, Any], monkeypatch: pytest.MonkeyPatch, focal: int | None, labels: list[str]) -> None:
    client = real["client"]
    body = _camera_png(focal)
    stored = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert "cameraMetadata" in stored.json()["sourceAsset"]
    sha = hashlib.sha256(body).hexdigest()
    real_chain = ingest_jobs._solve_chain

    def three_searches(*args: Any, on_event: Any, **kwargs: Any) -> Any:
        # The chain's three searches as `_solve_chain` announces them when the first two do not calibrate
        for name in ("phone_photo", "phone_photo", "triangle"):
            on_event("solver", name)
        return real_chain(*args, on_event=lambda *event: None, **kwargs)

    told: list[tuple[int, str]] = []
    real_pass = job_runner.JobContext.pass_

    def spy(ctx: JobContext, stage: str, number: int, label: str) -> None:
        told.append((number, label))
        real_pass(ctx, stage, number, label)

    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain", three_searches)
    monkeypatch.setattr(job_runner.JobContext, "pass_", spy)
    ended = _solved(client, sha)[1]
    assert ended["state"] == "succeeded", ended.get("error")
    assert told == list(enumerate(labels, start=1))


@pytest.mark.parametrize(("job_bound", "search_bound"), [(600.0, ingest_jobs.SOLVE_TIME_LIMIT_SEC), (100.0, 100.0)])
def test_the_search_keeps_its_own_bound_and_never_runs_past_what_is_left_of_its_job(
        real: dict[str, Any], monkeypatch: pytest.MonkeyPatch, job_bound: float, search_bound: float) -> None:
    client = real["client"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()
    asked: list[float] = []
    real_call = job_runner.SlotWorker.call

    def spy(worker: Any, *args: Any, timeout_s: float, **kwargs: Any) -> Any:
        asked.append(timeout_s)
        return real_call(worker, *args, timeout_s=timeout_s, **kwargs)

    monkeypatch.setattr(ingest_jobs, "SOLVE_JOB_TIME_LIMIT_SEC", job_bound)
    monkeypatch.setattr(job_runner.SlotWorker, "call", spy)
    assert _solved(client, sha)[1]["state"] == "succeeded"
    (timeout,) = asked
    assert search_bound - 30.0 < timeout <= search_bound


# ---- platform-5b: the solve job's whole bound, from measured real cases ----

# The largest server-thread time measured outside every step's own bound (stacking, detection and the predicted sky
# less its spread frames) over the 13 owner clips and 20 of the 60 public real photos, at load 7 to 12 on this laptop
# (.units/platform-5b/steps.txt, 2026-11-18): 18.2 s, a 24.2 MP photo's detection
MEASURED_UNTIMED_MAX_S = 18.2


def test_the_solve_jobs_bound_is_its_steps_bounds_and_four_times_its_largest_untimed_part_in_whole_minutes() -> None:
    """The step bounds of the longest chain: a HEIC photo's tile probe and tile decode (two decoder bounds) and the
    search, or a clip's decode, the search and its spread frames; either is 300 s. Four times the measured untimed
    part is room for a slower machine and a larger file, as the body limits take four times their largest body."""
    decoder, search = ingest_jobs.DECODE_TIMEOUT_SEC, ingest_jobs.SOLVE_TIME_LIMIT_SEC
    steps = max(2 * decoder + search, decoder + search + decoder)
    assert steps == 300
    expected = math.ceil((steps + 4 * MEASURED_UNTIMED_MAX_S) / 60.0) * 60.0
    assert expected == 420.0
    assert ingest_jobs.SOLVE_JOB_TIME_LIMIT_SEC == expected
    assert ingest_jobs.SOLVE_KIND.time_limit_s({"mediaKind": "image"}, None) == expected


# ---- platform-5c: today's run_solve_job is the runner's thin wrapper ----


def test_run_solve_job_starts_the_runners_job_waits_for_it_and_writes_none_of_todays_files(real: dict[str, Any]) -> None:
    client, store = real["client"], real["store"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()
    seen: list[dict[str, Any]] = []
    ended = ingest_jobs.run_solve_job(store, sha, on_transition=seen.append)
    UploadJob.model_validate(ended)
    assert ended["state"] == "succeeded" and ended["kind"] == "solve" and len(ended["id"]) == len("job_") + 32
    assert not (store.folder(sha) / "job.json").exists() and not (store.folder(sha) / "solution.json").exists()
    assert (store.folder(sha) / "solutions" / f"{ended['id']}.json").is_file()
    assert [s["state"] for s in seen][0] == "queued" and seen[-1] == ended
    assert {s["state"] for s in seen} == {"queued", "running", "succeeded"}
    for snapshot in seen:
        UploadJob.model_validate({k: v for k, v in snapshot.items() if k != "queueAhead"})
    assert client.get(f"/api/ingest/jobs/{ended['id']}").json() == ended
    with pytest.raises(FileNotFoundError):
        ingest_jobs.run_solve_job(store, "f" * 64)
    assert not hasattr(ingest_jobs, "_SOLVE_WORKER") and not hasattr(ingest_jobs, "_SOLVE_LOCK")


def test_an_on_transition_that_raises_never_stops_the_job_and_its_error_reaches_the_caller(real: dict[str, Any]) -> None:
    client, store = real["client"], real["store"]
    body = _png(_starfield())
    client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    sha = hashlib.sha256(body).hexdigest()

    def refuse(record: dict[str, Any]) -> None:
        raise ValueError("a listener's own check failed")

    with pytest.raises(ValueError, match="a listener's own check failed"):
        ingest_jobs.run_solve_job(store, sha, on_transition=refuse)
    ended = JobStore(store).newest_solve(sha)
    assert ended["state"] == "succeeded", "the job itself ran to its end"


# ---- platform-7: the start no longer waits (REQ-JOB-7.5) ----


def test_a_start_answers_at_once_however_it_is_asked(setup: dict[str, Any]) -> None:
    client, (sha_a, sha_b), fake = setup["client"], setup["shas"], setup["fake"]
    for query in ("", "?wait=true", "?wait=false"):
        sha = sha_a if query != "?wait=true" else sha_b
        answers: list[Any] = []
        start = threading.Thread(target=lambda: answers.append(client.post(f"/api/ingest/{sha}/solve{query}")),
                                 daemon=True)
        start.start()
        start.join(5.0)
        waited = start.is_alive()
        if waited:
            fake.release.set()  # let the held start end, so the test fails by its assertion and does not hang
            start.join(BOUND_S)
        assert not waited, f"the start {query or '(no query)'} waited for the job"
        (answer,) = answers
        assert answer.status_code in (200, 202) and answer.json()["state"] in ("queued", "running"), (query, answer.text)
    assert fake.entered.wait(BOUND_S), "the work runs on after the answer"
    openapi = client.get("/openapi.json").json()
    parameters = openapi["paths"]["/api/ingest/{sha256}/solve"]["post"].get("parameters", [])
    assert [p["name"] for p in parameters] == ["sha256"], "the start takes no wait"
    fake.release.set()


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
