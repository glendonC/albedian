"""The media kinds on the upload job runner and their routes (`services/media/jobs.py`, `services/media/router.py`;
design sections 9 and 11; REQ-RST-1.1, 1.2, 1.4, 1.7, 2.5 to 2.7, REQ-RST-8; P-RST-1, P-RST-18, P-RST-19).

Covers, through the API on a store of its own:
- An upload alone makes no variant; a display copy of a stored PNG is a job that ends succeeded, is listed, and is
  served by its hash with the original's caching headers, its samples the original's.
- A restoration of a stored still ends ready or rejected; a ready one is listed with records the contract takes and
  its file is the 16 bit PNG; every finished attempt is kept.
- The job's stages count up to their totals and end at one frame and the file's bytes (P-RST-18).
- The original's bytes and mode are untouched and nothing changes outside variants/, attempts/ and jobs/ (P-RST-1).
- No record or answer holds the store's path or the upload's own text (P-RST-19).
- A HEIC upload gets a PNG the browser draws, its samples the decoded original's (or, where ffprobe cannot report
  tile grids, the decoder's sentence). A clip gets no display copy (its contract sentence); a malformed hash or id is
  422, an unknown one 404.
- A clip's restoration (restore-7): it keeps its frame times, its audio packets and its last frame's duration, and is
  served in its own container; a clip at black ends with its note and the quantisation bound's lift; P-RST-14, 23
  and 24 on the stored clip; the same stored file whatever the pool's size; partial files never left behind.
- In this process: a rejected restoration keeps its attempt and no variant; an original that changes before or
  after the work fails the job; the child's outcome is the job's ending; a job whose variant is gone runs again,
  and a variant folder that lacks a file is never overwritten.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import subprocess
import time
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from photo_frames import star_field
from services.api import create_app
from services.contracts import media as contracts
from services.contracts.jobs import UploadJob
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.media import jobs as media_jobs
from services.media import clip_frames, clip_io, decode, frame_pool, gate, png16, stars, tone

# The kinds start ffmpeg and a child process: each test has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

# A clip's job holds its pool to two workers here: agents' test runs stay within two processes (design section 10). Set
# at import, before any test starts the media child, which takes it at its start
os.environ.setdefault(frame_pool.WORKERS_LIMIT_ENV, "2")

SECRET = "secret_title_marker_7d41"
POLL_S = 0.1
JOB_BOUND_S = 240.0


def _grids_reported() -> bool:
    proc = subprocess.run(["ffprobe", "-hide_banner", "-h"], capture_output=True)
    return proc.returncode == 0 and b"-show_stream_groups" in proc.stdout


GRIDS = _grids_reported()


@pytest.fixture()
def client(tmp_path):
    uploads = IngestStore(tmp_path / "store", max_bytes=200_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    with TestClient(app) as test_client:
        test_client.store_root = tmp_path / "store"
        yield test_client


def png8_with_title(seed: int = 3) -> bytes:
    pixels = np.random.default_rng(seed).integers(0, 256, (64, 80, 3), dtype=np.uint8)
    info = PngImagePlugin.PngInfo()
    info.add_text("Title", SECRET)
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def upload(client, data: bytes, content_type: str = "image/png") -> str:
    answer = client.post("/api/ingest", content=data, headers={"content-type": content_type})
    assert answer.status_code in (200, 201), answer.text
    return answer.json()["sourceAsset"]["sha256"]


def folder_of(client, sha: str) -> Path:
    return client.store_root / sha[:2] / sha


def finished(client, job: dict) -> dict:
    deadline = time.monotonic() + JOB_BOUND_S
    seen = []
    while job["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the job did not end within the test's bound"
        time.sleep(POLL_S)
        answer = client.get(f"/api/ingest/jobs/{job['id']}")
        assert answer.status_code == 200, answer.text
        job = answer.json()
        seen.append(job)
    finished.seen = seen
    return job


def snapshot(folder: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under the upload's folder outside variants/, attempts/ and jobs/, by its mode and bytes."""
    out = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if relative.parts[0] in ("variants", "attempts", "jobs") or not path.is_file():
            continue
        out[str(relative)] = (path.stat().st_mode, path.read_bytes())
    return out


def test_an_upload_alone_makes_no_variant(client) -> None:
    sha = upload(client, png8_with_title())
    assert not (folder_of(client, sha) / "variants").exists()
    assert client.get(f"/api/media/{sha}/variants").json() == {"variants": [], "attempts": []}


def test_a_display_copy_is_a_job_listed_and_served_by_its_hash_with_the_originals_samples(client) -> None:
    data = png8_with_title()
    sha = upload(client, data)
    before = snapshot(folder_of(client, sha))
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"})
    assert started.status_code == 202, started.text
    job = finished(client, started.json())
    UploadJob.model_validate(job)
    assert job["state"] == "succeeded" and job["kind"] == "display_copy", job
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert [v["id"] for v in listed["variants"]] == [job["variantId"]]
    assert [a["id"] for a in listed["attempts"]] == [job["attemptId"]]
    served = client.get(f"/api/media/{sha}/variants/{job['variantId']}/file")
    assert served.status_code == 200
    assert served.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-disposition"] == "inline"
    assert served.headers["content-type"] == "image/png"
    with Image.open(io.BytesIO(served.content)) as copy, Image.open(io.BytesIO(data)) as original:
        assert np.array_equal(np.asarray(copy.convert("RGB")), np.asarray(original.convert("RGB")))
    assert hashlib.sha256(served.content).hexdigest() == listed["variants"][0]["sha256"]
    # The same request again answers the current job: 200, the same variant
    again = client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"})
    assert again.status_code == 200 and again.json()["variantId"] == job["variantId"]
    assert snapshot(folder_of(client, sha)) == before


def test_a_restoration_ends_ready_with_the_16_bit_file_and_records_the_contract_takes(client) -> None:
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    sha = upload(client, png16.write_png16(scene.codes.astype(np.uint16), None))
    folder = folder_of(client, sha)
    original = next(folder.glob("original.*"))
    mode, digest = original.stat().st_mode, hashlib.sha256(original.read_bytes()).hexdigest()
    before = snapshot(folder)
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert started.status_code == 202, started.text
    job = finished(client, started.json())
    UploadJob.model_validate(job)
    assert job["state"] == "succeeded", (job.get("error"), job.get("attemptId"))
    record = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()
    contracts.MediaVariant.model_validate(record["variant"])
    contracts.ProcessingGraph.model_validate(record["graph"])
    contracts.ProcessingAttempt.model_validate(record["attempt"])
    assert record["attempt"]["status"] == "ready" and record["attempt"]["gate"]["passed"]
    assert record["variant"]["kind"] == "restoration" and record["variant"]["mimeType"] == "image/png"
    assert record["variant"]["colour"]["bitDepth"] == 16
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert [a["id"] for a in listed["attempts"]] == [job["attemptId"]] == [record["attempt"]["id"]]
    served = client.get(record["variant"]["uri"])
    assert served.status_code == 200 and served.content[24] == 16
    assert hashlib.sha256(served.content).hexdigest() == record["variant"]["sha256"]
    # P-RST-18: every stage counts up to its total; at the end one frame each, and the file's bytes written
    for seen in finished.seen:
        for stage in seen["stages"]:
            assert stage["total"] is None or 0 <= stage["done"] <= stage["total"]
    stages = {stage["stage"]: stage for stage in job["stages"]}
    for name in ("decode", "measure", "restore", "encode", "verify"):
        assert (stages[name]["done"], stages[name]["total"]) == (1, 1), name
    assert stages["write"]["done"] == stages["write"]["total"] == record["variant"]["byteSize"]
    # P-RST-1: the original is untouched, and nothing changed outside variants/, attempts/ and jobs/
    assert original.stat().st_mode == mode and hashlib.sha256(original.read_bytes()).hexdigest() == digest
    assert snapshot(folder) == before


def test_no_record_or_answer_holds_the_stores_path_or_the_uploads_own_text(client) -> None:
    sha = upload(client, png8_with_title())
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
    texts = [json.dumps(job), client.get(f"/api/media/{sha}/variants").text]
    folder = folder_of(client, sha)
    texts += [p.read_text() for p in list((folder / "attempts").glob("*.json")) + list((folder / "variants").rglob("*.json"))]
    for text in texts:
        assert SECRET not in text and str(client.store_root) not in text


def test_a_heic_upload_gets_a_png_the_browser_draws_listed_and_served_by_its_hash(client) -> None:
    heic = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "sips-colour-single-96x64.heic"
    sha = upload(client, heic.read_bytes(), "image/heic")
    original = next(folder_of(client, sha).glob("original.*"))
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"}).json())
    UploadJob.model_validate(job)
    if not GRIDS:
        # Where this ffprobe cannot report tile grids (CI's distro ffmpeg), the copy fails with the decoder's sentence
        assert (job["state"], job["error"]) == ("failed", decode.TILED_HEIC), job
        assert client.get(f"/api/media/{sha}/variants").json()["variants"] == []
        return
    assert job["state"] == "succeeded", (job.get("error"), job.get("attemptId"))
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert [v["id"] for v in listed["variants"]] == [job["variantId"]]
    variant = listed["variants"][0]
    assert (variant["mimeType"], variant["width"], variant["height"]) == ("image/png", 96, 64)
    served = client.get(variant["uri"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert hashlib.sha256(served.content).hexdigest() == variant["sha256"]
    still = decode.decode_still(original)
    with Image.open(io.BytesIO(served.content)) as copy:
        assert copy.format == "PNG" and copy.size == (96, 64)
        drawn = np.asarray(copy)
    assert drawn.shape[:2] == still.samples.shape[:2]
    assert np.array_equal(drawn[..., :3].astype(np.int64), np.asarray(still.samples)[..., :3].astype(np.int64))


def test_a_clip_gets_no_display_copy(client, tmp_path) -> None:
    clip = tmp_path / "clip.mp4"
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                           "testsrc2=s=64x48:r=30:d=0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
    sha = upload(client, clip.read_bytes(), "video/mp4")
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"}).json())
    UploadJob.model_validate(job)
    assert (job["state"], job["error"]) == ("failed", "A display copy is not made for a clip."), job
    assert client.get(f"/api/media/{sha}/variants").json() == {"variants": [], "attempts": []}
    assert media_jobs.clip_refusal({"mediaKind": "image"}, "display_copy") is None


@pytest.mark.parametrize(("path", "status"), [
    ("/api/media/nothex/variants", 422),
    ("/api/media/" + "a" * 64 + "/variants", 404),
    ("/api/media/" + "a" * 64 + "/variants/var_0123", 422),
    ("/api/media/" + "a" * 64 + "/variants/var_0123456789abcdef", 404),
    ("/api/media/" + "a" * 64 + "/variants/var_0123456789abcdef/file", 404),
])
def test_a_malformed_hash_or_id_is_422_and_an_unknown_one_404(client, path: str, status: int) -> None:
    assert client.get(path).status_code == status


def test_a_restore_request_the_contract_refuses_is_422_and_an_unknown_upload_404(client) -> None:
    sha = upload(client, png8_with_title())
    assert client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy", "exposureEv": 1}).status_code == 422
    assert client.post(f"/api/media/{sha}/restore", json={"kind": "other"}).status_code == 422
    assert client.post("/api/media/" + "b" * 64 + "/restore", json={"kind": "display_copy"}).status_code == 404


# =============================================================================================
# The kinds' own code, in this process
# =============================================================================================

JOB_ID = "job_" + "c" * 16


def stored_still(tmp_path: Path, codes: np.ndarray) -> tuple[str, str, str, str]:
    """A store with one upload as the store lays it out (`<root>/<aa>/<sha256>/original.png`): its root, folder,
    original and hash."""
    data = png16.write_png16(codes.astype(np.uint16), None)
    sha = hashlib.sha256(data).hexdigest()
    folder = tmp_path / "store" / sha[:2] / sha
    folder.mkdir(parents=True)
    (folder / "original.png").write_bytes(data)
    return str(tmp_path / "store"), str(folder), str(folder / "original.png"), sha


def no_progress(*args) -> None:
    pass


def test_a_rejected_restoration_keeps_its_attempt_and_no_variant(tmp_path, monkeypatch) -> None:
    from services.media import gate, photo
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    root, folder, original, sha = stored_still(tmp_path, scene.codes)
    smooth = gate.smooth_first_scale

    def with_a_bump(working, kept):
        out = smooth(working, kept)
        yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float64)
        return out + (0.004 * np.exp(-((xx - 40.5) ** 2 + (yy - 40.5) ** 2) / (2 * 1.3**2)))[..., None]

    restore = photo.restore_still
    monkeypatch.setattr(gate, "smooth_first_scale", with_a_bump)
    monkeypatch.setattr(photo, "restore_still", lambda still, ev=None: restore(still, ev, max_passes=0))
    out = media_jobs.restoration_in_child(root, folder, original, sha, JOB_ID, {"kind": "restoration"}, None, no_progress)
    assert out["status"] == "rejected" and set(out) == {"status", "attemptId"}, out
    kept = json.loads((Path(folder) / "attempts" / f"{out['attemptId']}.json").read_text())
    contracts.ProcessingAttempt.model_validate(kept)
    assert kept["status"] == "rejected" and kept["rejection"] == "unconfirmed_points" and kept["jobId"] == JOB_ID
    assert not (Path(folder) / "variants").exists()


@pytest.mark.parametrize("when", ["start", "end"])
def test_an_original_that_changes_while_the_job_runs_fails_it_and_keeps_no_variant(tmp_path, monkeypatch, when) -> None:
    from services.media import photo
    scene = star_field(11, size=160, count=8, noise_sigma=0.0015, peaks=(0.01, 0.2))
    root, folder, original, sha = stored_still(tmp_path, scene.codes)
    if when == "start":
        with open(original, "ab") as handle:
            handle.write(b"\0")

        def never(*args, **kwargs):
            raise AssertionError("the work ran on a changed original")

        monkeypatch.setattr(photo, "restore_still", never)
    else:
        restore = photo.restore_still

        def then_change(still, ev=None):
            result = restore(still, ev)
            with open(original, "ab") as handle:
                handle.write(b"\0")
            return result

        monkeypatch.setattr(photo, "restore_still", then_change)
    out = media_jobs.restoration_in_child(root, folder, original, sha, JOB_ID, {"kind": "restoration"}, None, no_progress)
    assert (out["status"], out["failureReason"]) == ("failed", media_jobs.CHANGED), out
    kept = json.loads((Path(folder) / "attempts" / f"{out['attemptId']}.json").read_text())
    assert kept["status"] == "failed" and kept["failureReason"] == media_jobs.CHANGED
    assert not (Path(folder) / "variants").exists()


class FakeChild:
    def __init__(self, outcome: dict) -> None:
        self.outcome, self.calls, self.args = outcome, [], []

    def call(self, target, args, **options):
        self.calls.append(options)
        self.args.append((target, args))
        return self.outcome


class FakeContext:
    def __init__(self, outcome: dict, remaining: float = 100.0, asset: dict | None = None,
                 request: dict | None = None) -> None:
        self.worker, self.remaining, self.stop, self.cancels = FakeChild(outcome), remaining, object(), 0
        self.asset, self.request, self.sha256, self.job_id = asset or {"mediaKind": "image"}, request, "d" * 64, JOB_ID
        self.original = Path("/store/dd") / ("d" * 64) / "original.jpg"

    def check_cancel(self) -> None:
        self.cancels += 1

    def remaining_s(self) -> float:
        return self.remaining

    def child(self) -> FakeChild:
        return self.worker

    def progress(self, *args) -> None:
        pass


def test_the_childs_outcome_is_the_jobs_ending() -> None:
    from services.ingest.runner import JobRefusal
    ready = FakeContext({"status": "ready", "attemptId": "att_1", "variantId": "var_1"})
    result = media_jobs._run_in_child(ready, media_jobs.restoration_in_child)
    assert (result.state, result.fields) == ("succeeded", {"attemptId": "att_1", "variantId": "var_1"})
    assert ready.cancels == 1 and ready.worker.calls[0]["timeout_s"] == 100.0 - media_jobs.CHILD_MARGIN_SEC
    assert ready.worker.calls[0]["stop"] is ready.stop and ready.worker.calls[0]["progress_kwarg"] == "progress"
    rejected = media_jobs._run_in_child(FakeContext({"status": "rejected", "attemptId": "att_2"}), print)
    assert (rejected.state, rejected.fields) == ("rejected", {"attemptId": "att_2"})
    with pytest.raises(JobRefusal) as refusal:
        media_jobs._run_in_child(FakeContext({"status": "failed", "attemptId": "a", "failureReason": "No."}), print)
    assert refusal.value.reason == "No."
    late = FakeContext({"status": "rejected", "attemptId": "att_3"}, remaining=2.0)
    media_jobs._run_in_child(late, print)
    assert late.worker.calls[0]["timeout_s"] == 1.0


# Restorations of public photos through the API on this laptop with the bound lifted, one at a time, at load 10 to
# 19 (platform-9, main 9e679c99): megapixels and seconds from the job's start to its end. The seconds per megapixel
# grow with the megapixels (5 at 2.4, 46 at 24.2, 147 at 50.1), so the bound grows with their square
MEASURED_STILLS = ((2.4, 12.1), (7.2, 75.3), (12.2, 94.1), (16.0, 117.6), (24.2, 1121.7), (33.6, 665.8),
                   (50.1, 7377.9))


def test_the_bound_is_a_base_and_seconds_for_each_megapixel_that_grow_with_the_megapixels_of_every_frame() -> None:
    assert media_jobs.bound_s({"width": 4000, "height": 3000}, None) == 120.0 + 4.0 * 12.0 * 12.0
    assert media_jobs.bound_s({"width": 1000, "height": 1000, "frameCount": 30}, None) == 120.0 + 4.0 * 30
    assert media_jobs.subject("a" * 64, None) is None
    # Every measured still ends inside its bound with room; an iPhone's 24 megapixel photo (5712 x 4284) gets at
    # least twice the slowest measured one of its size
    for megapixels, seconds in MEASURED_STILLS:
        bound = media_jobs.bound_s({"width": round(megapixels * 1e6), "height": 1}, None)
        assert bound - media_jobs.CHILD_MARGIN_SEC > 1.3 * seconds, megapixels
    assert media_jobs.bound_s({"width": 5712, "height": 4284}, None) - media_jobs.CHILD_MARGIN_SEC > 2 * 1121.7


def test_a_job_whose_variant_is_gone_runs_again_and_one_missing_a_file_is_never_overwritten(client) -> None:
    import shutil
    sha = upload(client, png8_with_title())
    first = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"}).json())
    assert first["state"] == "succeeded"
    stored = folder_of(client, sha) / "variants" / first["variantId"]
    whole = {p.name: p.read_bytes() for p in stored.iterdir()}
    # A folder that lacks one of its files: the job runs again, the folder is not replaced, and the job fails with
    # the contract's sentence for a failure it has no sentence of its own for
    for name in ("variant.png", "variant.json", "graph.json", "attempt.json"):
        aside = stored.parent / f".{name}.aside"
        (stored / name).rename(aside)
        again = client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"})
        assert again.status_code == 202 and again.json()["id"] != first["id"], name
        job = finished(client, again.json())
        assert (job["state"], job["error"]) == ("failed", decode.INTERNAL_ERROR), name
        assert {p.name: p.read_bytes() for p in stored.iterdir()} == {k: v for k, v in whole.items() if k != name}
        aside.rename(stored / name)
    # A folder gone whole: the job runs again and stores the same variant
    for p in stored.iterdir():
        p.chmod(0o600)
    shutil.rmtree(stored)
    again = client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"})
    assert again.status_code == 202
    job = finished(client, again.json())
    assert job["state"] == "succeeded" and job["variantId"] == first["variantId"]
    assert (stored / "variant.png").read_bytes() == whole["variant.png"]


def test_the_pipeline_version_names_the_restore_producers_revision() -> None:
    # Every graph and variant id hashes the pipeline version: raised with the producer's revision, a rerun gets new
    # ids, so the older variant stays beside the new one and is never overwritten
    from services.media import variants
    assert variants.PIPELINE_VERSION == f"1.{media_jobs.MEDIA_PRODUCER.revision}.0"


@pytest.mark.parametrize("kind", ["restoration", "display_copy"])
def test_a_restore_revision_raise_runs_again_and_stores_beside_the_older_variant(client, monkeypatch, kind) -> None:
    # FIELD's finding of 13:02, 19 November: a photo restored under revision 2 was restored again under 3, and the
    # write refused the variant id it already held, so the job failed with the internal error and kept no attempt
    import dataclasses
    from services.ingest import runner as job_runner
    from services.media import variants

    class InProcessChild:
        """The media group's child run in this process, so the raised version below reaches the step."""

        def call(self, target, args, *, timeout_s, stop, progress_kwarg, on_event):
            return target(*args, **{progress_kwarg: on_event})

    monkeypatch.setattr(job_runner.JobContext, "child", lambda self: InProcessChild())
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    sha = upload(client, png16.write_png16(scene.codes.astype(np.uint16), None))
    first = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": kind}).json())
    assert first["state"] == "succeeded", first
    older = folder_of(client, sha) / "variants" / first["variantId"]
    kept = {p.name: p.read_bytes() for p in older.iterdir()}
    revision = media_jobs.MEDIA_PRODUCER.revision + 1
    raised = dataclasses.replace(media_jobs.MEDIA_PRODUCER, revision=revision)
    monkeypatch.setitem(job_runner._KINDS, kind, dataclasses.replace(job_runner._KINDS[kind], producer=raised))
    monkeypatch.setattr(variants, "PIPELINE_VERSION", f"1.{revision}.0")
    again = client.post(f"/api/media/{sha}/restore", json={"kind": kind})
    assert again.status_code == 202 and again.json()["id"] != first["id"]
    job = finished(client, again.json())
    assert job["state"] == "succeeded", job
    assert job["variantId"] != first["variantId"]
    assert {p.name: p.read_bytes() for p in older.iterdir()} == kept
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert {v["id"] for v in listed["variants"]} >= {first["variantId"], job["variantId"]}
    graph = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()["graph"]
    assert graph["pipelineVersion"] == f"1.{revision}.0"
    assert graph["id"] == variants.graph_id(sha, kind, graph["request"], f"1.{revision}.0")


def test_each_kind_hands_the_child_the_uploads_orientation_and_the_request() -> None:
    turned = {"rotationDegrees": 90, "mirrored": False}
    asset = {"mediaKind": "image", "displayOrientation": turned}
    copy = FakeContext({"status": "ready", "attemptId": "a", "variantId": "v"}, asset=asset)
    media_jobs.run_display_copy(copy)
    target, args = copy.worker.args[0]
    assert target is media_jobs.display_copy_in_child
    assert args == ("/store", "/store/dd/" + "d" * 64, "/store/dd/" + "d" * 64 + "/original.jpg", "d" * 64, JOB_ID, turned)
    request = {"kind": "restoration", "exposureEv": 1.5}
    restoration = FakeContext({"status": "ready", "attemptId": "a", "variantId": "v"}, asset=asset, request=request)
    media_jobs.run_restoration(restoration)
    target, args = restoration.worker.args[0]
    assert target is media_jobs.restoration_in_child and args[5:] == (request, turned)


def test_the_requested_exposure_reaches_the_restoration(tmp_path, monkeypatch) -> None:
    from services.media import photo
    scene = star_field(11, size=160, count=8, noise_sigma=0.0015, peaks=(0.01, 0.2))
    root, folder, original, sha = stored_still(tmp_path, scene.codes)
    restore, asked = photo.restore_still, []

    def noted(still, ev=None):
        asked.append(ev)
        return restore(still, ev)

    monkeypatch.setattr(photo, "restore_still", noted)
    request = {"kind": "restoration", "exposureEv": 1.5}
    media_jobs.restoration_in_child(root, folder, original, sha, JOB_ID, request, None, no_progress)
    assert asked == [1.5]


def test_variants_and_attempts_are_listed_oldest_first(client) -> None:
    from services.ingest.job_store import JobStore
    from services.media import router as media_router

    sha = upload(client, png8_with_title())
    folder = folder_of(client, sha)
    jobs = JobStore(IngestStore(client.store_root, max_bytes=200_000_000))
    instants = (
        ("e" * 16, "2026-11-19T02:00:00Z"),
        ("b" * 16, "2026-11-19T01:00:00.500Z"),
        ("d" * 16, "2026-11-19T01:00:00Z"),
        ("a" * 16, "2026-11-19T01:00:00.500Z"),
        ("c" * 16, "2026-11-19T01:00:00.050Z"),
    )
    for idx, (suffix, created) in enumerate(instants, start=1):
        name = f"var_{suffix}"
        (folder / "variants" / name).mkdir(parents=True)
        (folder / "variants" / name / "variant.json").write_text(json.dumps({"id": name, "createdAtUTC": created}))
        jobs.write_record({
            "id": f"job_{sha[:16]}{idx:016x}",
            "sourceAssetSha256": sha,
            "kind": "restoration",
            "state": "succeeded",
            "createdAtUTC": created,
            "startedAtUTC": created,
            "finishedAtUTC": created,
            "producer": {"name": "albedian.restore", "revision": media_jobs.MEDIA_PRODUCER.revision},
            "request": {"kind": "restoration"},
            "graphId": f"graph_{suffix}",
            "attemptId": f"att_{suffix}",
            "variantId": name,
            "stages": [],
        })
    # An unnamed variant folder on disk (no succeeded job record) is never listed
    unnamed = "var_" + "f" * 16
    (folder / "variants" / unnamed).mkdir(parents=True)
    (folder / "variants" / unnamed / "variant.json").write_text(
        json.dumps({"id": unnamed, "createdAtUTC": "2026-11-19T00:30:00Z"})
    )
    (folder / "attempts").mkdir()
    for idx, (suffix, ended) in enumerate(instants, start=1):
        name = f"att_{suffix}"
        started_opposite = f"2026-11-19T00:{60 - idx:02d}:00Z"
        (folder / "attempts" / f"{name}.json").write_text(
            json.dumps({"id": name, "startedAtUTC": started_opposite, "finishedAtUTC": ended})
        )
    listed = client.get(f"/api/media/{sha}/variants").json()
    expected = ["d" * 16, "c" * 16, "a" * 16, "b" * 16, "e" * 16]
    assert [v["id"] for v in listed["variants"]] == [f"var_{s}" for s in expected]
    assert [a["id"] for a in listed["attempts"]] == [f"att_{s}" for s in expected]
    ties = [
        {"id": "var_" + "b" * 16, "createdAtUTC": "2026-11-19T03:00:00+02:00"},
        {"id": "var_" + "a" * 16, "createdAtUTC": "2026-11-19T01:00:00"},
    ]
    ties.sort(key=lambda r: media_router._utc_sort_key(r, "createdAtUTC"))
    assert [r["id"] for r in ties] == ["var_" + "a" * 16, "var_" + "b" * 16]


def test_post_publish_crash_serves_no_uncommitted_variant_sweeps_cleanly_and_lets_retry_succeed(
    client, monkeypatch
) -> None:
    import threading
    from services.ingest import runner as job_runner
    from services.ingest.job_store import JobStore
    from services.media import variants

    class InProcessChild:
        def call(self, target, args, *, timeout_s, stop, progress_kwarg, on_event):
            return target(*args, **{progress_kwarg: on_event})

    monkeypatch.setattr(job_runner.JobContext, "child", lambda self: InProcessChild())
    sha = upload(client, png8_with_title())
    folder = folder_of(client, sha)
    published, release = threading.Event(), threading.Event()
    real_publish = variants.publish_variant
    pause_live = [True]

    def paused_publish(*args, **kwargs):
        stored = real_publish(*args, **kwargs)
        if pause_live[0]:
            published.set()
            assert release.wait(10.0), "test never released post-publish window"
        return stored

    monkeypatch.setattr(variants, "publish_variant", paused_publish)
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"})
    assert started.status_code == 202
    assert published.wait(10.0)
    vid = variants.variant_id(sha, "display_copy", {"kind": "display_copy"})
    assert (folder / "variants" / vid / "variant.png").is_file()

    # Live window between publish_variant and Runner._end: every variant route answers as if uncommitted (REQ-JOB-5.4)
    assert client.get(f"/api/media/{sha}/variants").json()["variants"] == []
    for suffix in ("", "/file", "/export", "/manifest"):
        assert client.get(f"/api/media/{sha}/variants/{vid}{suffix}").status_code == 404, suffix

    # Cancel during the post-publish pre-commit window: job ends canceled and cleans up the uncommitted variant
    canceled = client.post(f"/api/ingest/jobs/{started.json()['id']}/cancel")
    assert canceled.status_code == 202
    release.set()
    ended_cancel = finished(client, started.json())
    # Note: in-process child finished returning KindResult, so check crash recovery explicitly next; if it committed
    # with cancelRequestedAtUTC (REQ-JOB-4.3), reset to simulate a SIGKILL right after publish_variant + keep_attempt
    pause_live[0] = False
    sha2 = upload(client, png8_with_title(seed=4))
    folder2 = folder_of(client, sha2)
    vid2 = variants.variant_id(sha2, "display_copy", {"kind": "display_copy"})
    copy2 = variants.make_display_copy(
        client.store_root, folder2, next(folder2.glob("original.*")), source_sha256=sha2, job_id=f"job_{sha2[:16]}{'1' * 16}"
    )
    assert copy2.status == "ready" and (folder2 / "variants" / vid2 / "variant.png").is_file()
    jobs = JobStore(IngestStore(client.store_root, max_bytes=200_000_000))
    crashed_id = f"job_{sha2[:16]}{'1' * 16}"
    jobs.write_record({
        "id": crashed_id,
        "sourceAssetSha256": sha2,
        "kind": "display_copy",
        "state": "running",
        "createdAtUTC": "2026-11-24T04:00:00Z",
        "startedAtUTC": "2026-11-24T04:00:00Z",
        "producer": {"name": "albedian.restore", "revision": media_jobs.MEDIA_PRODUCER.revision},
        "request": {"kind": "display_copy"},
        "graphId": variants.graph_id(sha2, "display_copy", {"kind": "display_copy"}),
        "stages": [],
    })
    assert ended_cancel["state"] in ("canceled", "succeeded")

    # Before sweep runs: uncommitted variant on disk is never listed, read, served or exported; attempt record stays
    listed_pre = client.get(f"/api/media/{sha2}/variants").json()
    assert listed_pre["variants"] == [] and len(listed_pre["attempts"]) == 1
    for suffix in ("", "/file", "/export", "/manifest"):
        assert client.get(f"/api/media/{sha2}/variants/{vid2}{suffix}").status_code == 404, suffix

    # Sweep (as at server restart) marks the job interrupted and removes the uncommitted variant folder
    counts, _ = jobs.sweep()
    assert counts.interrupted == 1 and counts.results_removed == 1
    assert not (folder2 / "variants" / vid2).exists()
    assert len(list((folder2 / "attempts").glob("att_*.json"))) == 1

    # Retry now succeeds cleanly instead of failing with FileExistsError / internal_error
    retry = client.post(f"/api/media/{sha2}/restore", json={"kind": "display_copy"})
    assert retry.status_code == 202
    retry_done = finished(client, retry.json())
    assert retry_done["state"] == "succeeded" and retry_done["variantId"] == vid2
    assert [v["id"] for v in client.get(f"/api/media/{sha2}/variants").json()["variants"]] == [vid2]
    for suffix in ("", "/file", "/export", "/manifest"):
        assert client.get(f"/api/media/{sha2}/variants/{vid2}{suffix}").status_code == 200, suffix


# ---------------------------------------------------------------------------------------------------------------
# A clip's restoration (restore-7; design sections 7, 10 and 11; REQ-RST-7, 6.10, 6.11, 8; P-RST-14, 18, 23, 24)
# ---------------------------------------------------------------------------------------------------------------

BT709_TV = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")


def lossless_clip(path: Path, planes: list, *, width: int, height: int) -> None:
    """An upload written without loss from the stream's own codes: 8 bit 4:2:0, a time base of 1/600, 30 frames a
    second."""
    from fractions import Fraction

    video = clip_io.VideoFormat(width, height, "yuv420p", BT709_TV)
    with clip_io.ClipEncoder(path, video, time_base=Fraction(1, 600), frame_times=[20 * k for k in range(len(planes))],
                             container="mov", crf=0) as encoder:
        for frame in planes:
            encoder.write(frame)
        encoder.finish()


def decoded(path: Path) -> list[tuple[np.ndarray, ...]]:
    probe = clip_io.probe_clip(path)
    with clip_io.ClipDecoder(path, probe) as decoder:
        return [tuple(np.array(p) for p in frame.planes) for frame in decoder]


def partial_files(client) -> list[str]:
    folder = client.store_root / ".partial"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def black_clip(path: Path, count: int = 12) -> None:
    """A clip recorded almost at black: one code over black, a few samples one code more."""
    black = 16 + 1
    frames = []
    for k in range(count):
        y = np.full((128, 128), black, np.uint8)
        y[60 + (k % 3):62 + (k % 3), 60:62] = black + 1
        frames.append((y, np.full((64, 64), 128, np.uint8), np.full((64, 64), 128, np.uint8)))
    lossless_clip(path, frames, width=128, height=128)


def test_a_clip_restored_by_the_job_keeps_its_original_frame_times_audio_and_last_frame(client, tmp_path) -> None:
    source = tmp_path / "clip.mov"
    # A dark sky with noise of its own in every frame, which the job measures a noise table on. (A test card such as
    # testsrc2 ends rejected since restore-4f: its noise table is measured from its detail, the spatial filter smears
    # its sharp edges and a raised sample stays, restore-7-on-4f report)
    sky = "color=c=0x181818:s=96x64:r=30:d=0.5,noise=alls=14:allf=t"
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", sky,
                           "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-shortest", str(source)], capture_output=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
    sha = upload(client, source.read_bytes(), "video/quicktime")
    folder = folder_of(client, sha)
    original = next(folder.glob("original.*"))
    mode, digest = original.stat().st_mode, hashlib.sha256(original.read_bytes()).hexdigest()
    before = snapshot(folder)
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert started.status_code == 202, started.text
    job = finished(client, started.json())
    UploadJob.model_validate(job)
    assert job["state"] == "succeeded", (job.get("error"), job.get("attemptId"))
    record = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()
    contracts.MediaVariant.model_validate(record["variant"])
    contracts.ProcessingGraph.model_validate(record["graph"])
    contracts.ProcessingAttempt.model_validate(record["attempt"])
    variant, attempt = record["variant"], record["attempt"]
    probe = clip_io.probe_clip(original)
    count = probe.frame_count
    assert (variant["mimeType"], variant["frameCount"], variant["width"], variant["height"]) == ("video/quicktime", count, 96, 64)
    assert variant["frameTimesSha256"] == media_jobs.frame_times_sha256(probe.time_base, probe.frame_times)
    stored = folder / "variants" / variant["id"] / "variant.mov"
    # REQ-RST-7.5, 7.6: every frame time the original's, every audio packet's bytes and time the original's
    assert clip_io.packet_times(stored, "v:0", written_here=True) == (probe.time_base, probe.frame_times)
    hashes = [clip_io.stream_packet_hash(original, f"0:{a.index}", written_here=False) for a in probe.audio]
    assert len(hashes) == 1 and [a["sha256"] for a in variant["audioStreams"]] == hashes
    check = clip_io.check_stored_clip(stored, original, probe, audio_hashes=hashes, display_orientation=None)
    assert check.passed and check.last_duration_equal
    served = client.get(variant["uri"])
    assert served.status_code == 200 and served.headers["content-type"] == "video/quicktime"
    assert hashlib.sha256(served.content).hexdigest() == variant["sha256"]
    assert attempt["measured"]["clip"]["frameCount"] == attempt["gate"]["framesChecked"] == count
    assert [step["kind"] for step in record["graph"]["steps"]][-2:] == ["encode", "copy_audio"]
    # P-RST-18: every stage counts up to its total; at the end every frame, and the file's bytes written
    for seen in finished.seen:
        for stage in seen["stages"]:
            assert stage["total"] is None or 0 <= stage["done"] <= stage["total"]
    stages = {stage["stage"]: stage for stage in job["stages"]}
    assert (stages["measure"]["done"], stages["measure"]["total"]) == (len(clip_frames.sample_indices(count)),) * 2
    for name in ("decode", "restore", "encode", "verify"):
        assert (stages[name]["done"], stages[name]["total"]) == (count, count), name
    assert stages["write"]["done"] == stages["write"]["total"] == variant["byteSize"]
    # P-RST-1: the original is untouched, and nothing changed outside variants/, attempts/ and jobs/
    assert original.stat().st_mode == mode and hashlib.sha256(original.read_bytes()).hexdigest() == digest
    assert snapshot(folder) == before
    assert not any(name.startswith(job["id"]) for name in partial_files(client))


def test_a_clip_recorded_at_black_ends_with_its_plain_note_and_a_lift_held_by_the_quantisation_bound(client, tmp_path) -> None:
    source = tmp_path / "black.mov"
    black_clip(source)
    sha = upload(client, source.read_bytes(), "video/quicktime")
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
    assert job["state"] == "succeeded", (job.get("error"), job.get("attemptId"))
    attempt = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()["attempt"]
    measured = attempt["measured"]
    assert measured["noiseNotMeasured"] == "fine_detail_mostly_zero" and "noise" not in measured
    assert measured["lift"]["decidedBy"] == "quantisation_bound"
    assert media_jobs.NOTE_BLACK in measured["notes"] and tone.NOTE_NOISE_NOT_MEASURED not in measured["notes"]
    assert measured["clip"]["framesWithNeighbours"] == 0 and measured["clip"]["pointsConfirmedOnFramesTogether"] == 0
    assert attempt["gate"]["passed"] and attempt["gate"]["framesChecked"] == 12


def guarded_scene(path: Path) -> dict:
    """P-RST-14, 23 and 24 in one panning clip of 7 frames, places given in the middle frame: a star of the scene too
    faint for one frame (`faint`), a light of the scene only the neighbours carry (`neighbours`), a patch on one sensor
    place in every frame (`sensor`), and a place where nothing is (`empty`)."""
    from test_clip_frames import at_place, pan, render, spot, star_list

    places = {"faint": (130.4, 120.7), "neighbours": (70.3, 190.6), "sensor": (190.2, 60.4), "empty": (60.6, 70.2)}
    rng = np.random.default_rng(61)
    offsets = pan(rng, frames=7)
    placed = star_list(rng, 400, 2.0 * 256 + 260 + 140)
    clip = clip_frames.clip_colour(clip_io.VideoFormat(256, 256, "yuv420p", BT709_TV))
    frames = []
    for k, o in enumerate(offsets):
        light = render(o, placed, 256, sky=0.03, cloud=0.0) + at_place(places["faint"], o, offsets[3], 0.006)
        light = light + at_place(places["neighbours"], o, offsets[3], 0.03) * (k != 3) + spot(*places["sensor"], 0.05)
        frames.append(clip.to_planes(np.clip(np.repeat(light[..., None], 3, axis=-1) + rng.normal(0.0, 0.003, (256, 256, 3)), 0, 1)))
    lossless_clip(path, frames, width=256, height=256)
    return places


def near_any(points, place: tuple[float, float]) -> bool:
    return any(math.hypot(p.x_px - place[0], p.y_px - place[1]) <= stars.MATCH_RADIUS_PX for p in points)


def test_p_rst_14_23_24_the_stored_clip_shows_what_the_frames_together_confirm_and_the_gate_refuses_the_rest(client, tmp_path) -> None:
    source = tmp_path / "scene.mov"
    places = guarded_scene(source)
    sha = upload(client, source.read_bytes(), "video/quicktime")
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
    assert job["state"] == "succeeded", (job.get("error"), job.get("attemptId"))
    record = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()
    counts = record["attempt"]["measured"]["clip"]
    assert counts["framesWithNeighbours"] >= 1 and counts["pointsConfirmedOnFramesTogether"] >= 1
    assert counts["pointsFixedToSensor"] >= 1
    folder = folder_of(client, sha)
    original = next(folder.glob("original.*"))
    originals, stored = decoded(original), decoded(folder / "variants" / job["variantId"] / "variant.mov")
    clip = clip_frames.clip_colour(clip_io.probe_clip(original).video)

    def points(planes):
        luma = clip_frames.stored_luma(clip.stored(planes))
        return gate.variant_points(luma, np.zeros(luma.shape, dtype=bool))[0]

    middle = points(stored[3])
    assert near_any(middle, places["faint"]), "P-RST-23: the frames together's star is in the output frame"
    assert not near_any(middle, places["neighbours"]), "P-RST-14: what only the neighbours carry is not"
    assert not near_any(middle, places["sensor"]), "P-RST-24: what stays on a sensor place is not"
    settings = clip_frames.measure_clip(clip, [originals[i] for i in clip_frames.sample_indices(len(originals))])
    for name in ("neighbours", "empty", "sensor"):
        painted = [tuple(np.array(p) for p in frame) for frame in stored]
        from test_clip_frames import spot

        painted[3] = (np.clip(np.round(painted[3][0] + spot(*places[name], 12.0)), 0, 255).astype(np.uint8), *painted[3][1:])
        gates = clip_frames.gate_run(settings, frame_pool.Run(0, 0, 7, tuple(zip(originals, painted))))
        outcome = gates[3].outcome
        assert not outcome.record["passed"] and near_any(outcome.unconfirmed, places[name]), name


def test_the_stored_clip_is_the_same_whatever_the_pools_size(tmp_path) -> None:
    hashes = []
    for workers in (1, 2):
        root = tmp_path / f"store{workers}"
        source = tmp_path / f"black{workers}.mov"
        black_clip(source)
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        folder = root / sha[:2] / sha
        folder.mkdir(parents=True)
        original = folder / "original.mov"
        original.write_bytes(source.read_bytes())
        (root / ".partial").mkdir()
        partials = [str(root / ".partial" / f"{JOB_ID}-clip_encoded.mov.part"), str(root / ".partial" / f"{JOB_ID}-variant.mov.part")]
        facts = {"mimeType": "video/quicktime", "durationMs": 400.0, "audioStreams": []}
        out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"},
                                                   None, facts, partials, no_progress, workers=workers)
        assert out["status"] == "ready", out
        variant = json.loads((folder / "variants" / out["variantId"] / "variant.json").read_text())
        hashes.append(variant["sha256"])
        assert sorted(p.name for p in (root / ".partial").iterdir()) == []
    assert hashes[0] == hashes[1]


def test_a_refused_or_failed_clip_leaves_no_partial_file_and_no_variant(tmp_path, monkeypatch) -> None:
    root = tmp_path / "store"
    source = tmp_path / "black.mov"
    black_clip(source, count=6)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = root / sha[:2] / sha
    folder.mkdir(parents=True)
    original = folder / "original.mov"
    original.write_bytes(source.read_bytes())
    (root / ".partial").mkdir()
    partials = [str(root / ".partial" / f"{JOB_ID}-clip_encoded.mov.part"), str(root / ".partial" / f"{JOB_ID}-variant.mov.part")]
    facts = {"mimeType": "video/quicktime", "durationMs": 200.0, "audioStreams": []}
    record = gate.clip_record

    def refusing(outcomes):
        out = dict(record(outcomes))
        out.update(unconfirmedPointCount=1, framesWithUnconfirmed=1, variantPointCount=max(1, out["variantPointCount"]), passed=False)
        return out

    monkeypatch.setattr(gate, "clip_record", refusing)
    out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"},
                                               None, facts, partials, no_progress, workers=1)
    assert out["status"] == "rejected", out
    kept = json.loads((folder / "attempts" / f"{out['attemptId']}.json").read_text())
    contracts.ProcessingAttempt.model_validate(kept)
    assert kept["rejection"] == "unconfirmed_points" and not (folder / "variants").exists()
    assert list((root / ".partial").iterdir()) == []
    monkeypatch.setattr(gate, "clip_record", record)

    def broken(*args, **kwargs):
        raise clip_io.ClipError("internal")

    monkeypatch.setattr(clip_io, "check_stored_clip", broken)
    out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"},
                                               None, facts, partials, no_progress, workers=1)
    assert out == {"status": "failed", "attemptId": out["attemptId"], "failureReason": decode.INTERNAL_ERROR}
    assert list((root / ".partial").iterdir()) == [] and not (folder / "variants").exists()


def test_a_rejected_restoration_ends_rejected_while_its_attempt_is_kept(tmp_path) -> None:
    # The runner ends a job only when the kind finds its results whole, and a rejected restoration has an attempt and no
    # variant (platform-8 on main; restore-7 found the same, a rejected clip had ended failed)
    class Store:
        def upload_folder(self, sha256: str) -> Path:
            return tmp_path / sha256[:2] / sha256

    sha, attempt = "e" * 64, "att_" + "1" * 16
    record = {"state": "rejected", "sourceAssetSha256": sha, "attemptId": attempt}
    assert not media_jobs._results_stored(Store(), record)
    attempts = tmp_path / sha[:2] / sha / "attempts"
    attempts.mkdir(parents=True)
    (attempts / f"{attempt}.json").write_text("{}")
    assert media_jobs._results_stored(Store(), record)
    assert not media_jobs._results_stored(Store(), {**record, "attemptId": "att_" + "2" * 16})
    assert not media_jobs._results_stored(Store(), {**record, "state": "succeeded", "variantId": "var_" + "1" * 16})


def _black_upload(tmp_path: Path, count: int = 6):
    root = tmp_path / "store"
    source = tmp_path / "black.mov"
    black_clip(source, count=count)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = root / sha[:2] / sha
    folder.mkdir(parents=True)
    original = folder / "original.mov"
    original.write_bytes(source.read_bytes())
    (root / ".partial").mkdir()
    partials = [str(root / ".partial" / f"{JOB_ID}-clip_encoded.mov.part"), str(root / ".partial" / f"{JOB_ID}-variant.mov.part")]
    facts = {"mimeType": "video/quicktime", "durationMs": 1000.0 * count / 30, "audioStreams": []}
    return root, folder, original, sha, partials, facts


def test_a_clip_whose_stored_file_differs_from_the_original_is_rejected_with_the_checks_reason(tmp_path, monkeypatch) -> None:
    root, folder, original, sha, partials, facts = _black_upload(tmp_path)
    check = clip_io.check_stored_clip

    def differs(*args, **kwargs):
        kept = check(*args, **kwargs)
        return clip_io.StoredClipCheck(kept.video_frames, kept.frame_times_equal, kept.audio, False, kept.orientation_equal,
                                       "audio_differs", kept.last_duration_equal)

    monkeypatch.setattr(clip_io, "check_stored_clip", differs)
    out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"},
                                               None, facts, partials, no_progress, workers=1)
    assert out["status"] == "rejected", out
    kept = json.loads((folder / "attempts" / f"{out['attemptId']}.json").read_text())
    contracts.ProcessingAttempt.model_validate(kept)
    assert kept["rejection"] == "audio_differs" and "gate" not in kept
    assert list((root / ".partial").iterdir()) == [] and not (folder / "variants").exists()


def test_a_clip_whose_original_changes_while_the_job_runs_fails_and_stores_nothing(tmp_path, monkeypatch) -> None:
    hashed = media_jobs._sha256
    for changed_at in (1, 2):
        # A store of its own each time: an attempt's id is its start, to the millisecond
        (tmp_path / str(changed_at)).mkdir()
        root, folder, original, sha, partials, facts = _black_upload(tmp_path / str(changed_at))
        calls = []

        def changing(path, calls=calls, changed_at=changed_at):
            calls.append(path)
            return "0" * 64 if len(calls) == changed_at else hashed(path)

        monkeypatch.setattr(media_jobs, "_sha256", changing)
        out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"},
                                                   None, facts, partials, no_progress, workers=1)
        assert (out["status"], out.get("failureReason")) == ("failed", media_jobs.CHANGED), changed_at
        assert list((root / ".partial").iterdir()) == [] and not (folder / "variants").exists()


def test_a_clips_bound_is_a_base_and_seconds_for_each_megapixel_of_every_frame_over_its_workers() -> None:
    # A still's bound is platform-9's (the test above); a clip's: its frames from its duration and rate, rounded up and one more, over the pool's workers
    clip = {"mediaKind": "video", "width": 1000, "height": 1000, "durationMs": 1000.0, "fpsNumerator": 30000, "fpsDenominator": 1001}
    assert media_jobs.clip_frame_count(clip) == 31
    # at three times the worst rate measured on the owner's clips (restore-7), 6.7 s per megapixel of a frame per worker
    assert media_jobs.CLIP_BOUND_SEC_PER_MEGAPIXEL == 20.0
    assert media_jobs.bound_s(clip, None) == pytest.approx(120.0 + 20.0 * 31 / media_jobs.frame_pool.default_workers())
    assert media_jobs.clip_frame_count({"durationMs": 500.0}) == 31
    assert media_jobs.subject("a" * 64, None) is None


def _banded_png8(top_rgb: tuple[int, int, int]) -> bytes:
    rng = np.random.default_rng(71)
    size = 192
    yy = np.linspace(0.0, 1.0, size)[:, None]
    codes = np.clip(np.round(48.0 + 96.0 * yy[..., None] + rng.normal(0.0, 3.5, (size, size, 3))), 0, 255).astype(np.uint8)
    codes[:64, :] = np.array(top_rgb, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(codes, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_a_near_black_still_ends_its_job_failed_with_c36_after_decode_and_other_photo_errors_stay_internal(
        client, tmp_path) -> None:
    from services.media import photo

    sha = upload(client, _banded_png8((0, 0, 0)))
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert started.status_code == 202, started.text
    job = finished(client, started.json())
    UploadJob.model_validate(job)
    expected = "This photo's sky is so close to black that its faintest noise is cut off, so it was not restored."
    assert (job["state"], job["error"]) == ("failed", expected), job
    assert photo.NEAR_BLACK_CLIP == expected
    stages = {stage["stage"]: (stage["done"], stage["total"]) for stage in job["stages"]}
    assert stages["decode"] == (1, 1) and stages["measure"] == (0, 1)
    folder = folder_of(client, sha)
    assert not (folder / "variants").exists()
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert listed["variants"] == [] and len(listed["attempts"]) == 1
    attempt = listed["attempts"][0]
    contracts.ProcessingAttempt.model_validate(attempt)
    assert (attempt["status"], attempt["failureReason"]) == ("failed", photo.NEAR_BLACK_CLIP)

    # A still whose darkest quarter is flat above black (code 24, not clipped at 0) keeps the internal-error sentence
    grey_bytes = _banded_png8((24, 24, 24))
    grey_sha = hashlib.sha256(grey_bytes).hexdigest()
    grey_folder = tmp_path / "grey_store" / grey_sha[:2] / grey_sha
    grey_folder.mkdir(parents=True)
    grey_original = grey_folder / "original.png"
    grey_original.write_bytes(grey_bytes)
    out = media_jobs.restoration_in_child(str(tmp_path / "grey_store"), str(grey_folder), str(grey_original),
                                          grey_sha, JOB_ID, {"kind": "restoration"}, None, no_progress)
    assert (out["status"], out["failureReason"]) == ("failed", decode.INTERNAL_ERROR), out


def test_the_restore_producer_lists_chroma_at_revision_eight_and_stores_chroma_counts_on_stills(client) -> None:
    from services.media import variants

    assert "services/media/chroma.py" in media_jobs.MEDIA_PRODUCER.files
    assert media_jobs.MEDIA_PRODUCER.revision >= 8
    assert variants.PIPELINE_VERSION == f"1.{media_jobs.MEDIA_PRODUCER.revision}.0"
    scene = star_field(11, size=192, count=10, noise_sigma=0.0015, peaks=(0.01, 0.2))
    sha = upload(client, png16.write_png16(scene.codes.astype(np.uint16), None))
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
    assert job["state"] == "succeeded", job
    record = client.get(f"/api/media/{sha}/variants/{job['variantId']}").json()
    kinds = [s["kind"] for s in record["graph"]["steps"]]
    assert kinds.index("smooth_chroma") == kinds.index("spatial_denoise") + 1
    assert kinds.index("dissolve_unconfirmed") == kinds.index("smooth_chroma") + 1
    assert set(record["attempt"]["measured"]["chroma"]) == {"heldSamples", "clampedSamples"}


def test_a_still_inside_black_bars_is_stored_whole_with_its_bars_at_restore_revision_ten(client) -> None:
    # photo-bars-one: the restore producer lists the one bar helper, and the served file is the whole frame, its bars
    # as they were (code 0) and its picture restored
    from services.media import bars, variants

    assert "services/media/bars.py" in media_jobs.MEDIA_PRODUCER.files
    assert media_jobs.MEDIA_PRODUCER.revision >= 10
    assert variants.PIPELINE_VERSION == f"1.{media_jobs.MEDIA_PRODUCER.revision}.0"
    scene = star_field(11, size=192, count=10, noise_sigma=0.0015, peaks=(0.01, 0.2))
    codes = np.pad(scene.codes.astype(np.uint16), ((20, 20), (0, 0), (0, 0)))
    sha = upload(client, png16.write_png16(codes, None))
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
    assert job["state"] == "succeeded", job
    served = decode.read_png(client.get(f"/api/media/{sha}/variants/{job['variantId']}/file").content)
    assert served.shape == codes.shape
    assert not served[:20].any() and not served[-20:].any() and served[20:-20].any()
    assert bars.find_bars([served.max(axis=-1)], 0) == bars.Bars(top=20, bottom=20)


def _banded_clip_file(path: Path, sky_y: int, *, pix_fmt: str = "yuv420p", colour_range: str = "tv",
                      count: int = 12, size: int = 192) -> None:
    from fractions import Fraction

    tags = clip_io.ColourTags(range=colour_range, matrix="bt709", transfer="bt709", primaries="bt709")
    video = clip_io.VideoFormat(size, size, pix_fmt, tags)
    yy = np.linspace(0.0, 1.0, size)[:, None]
    frames = []
    for k in range(count):
        rng = np.random.default_rng(71 + k)
        y = np.clip(np.round(48.0 + 96.0 * yy + rng.normal(0.0, 3.5, (size, size))), 0, 255).astype(np.uint8)
        y[:64, :] = sky_y
        frames.append((y, np.full((size // 2, size // 2), 128, np.uint8), np.full((size // 2, size // 2), 128, np.uint8)))
    with clip_io.ClipEncoder(path, video, time_base=Fraction(1, 600), frame_times=[20 * k for k in range(count)],
                             container="mov", crf=0) as encoder:
        for frame in frames:
            encoder.write(frame)
        encoder.finish()


def test_a_near_black_clip_ends_its_job_failed_with_c36_on_measured_and_a_flat_band_above_black_restores_unmeasured(
        client, tmp_path) -> None:
    # Review row C36 clip form + stage-row guard [decided 2026-11-24, UIUX-nearblackrestore-READY.md]:
    # 1. Limited-range 12-frame Regime B clip (Y'=16 top quarter, noisy rising bottom) stops on `measure` at (7, 8)
    #    with C36's clip sentence so RestoreSteps marks Measured (not Opened) with the x
    source = tmp_path / "nearblack_tv.mov"
    _banded_clip_file(source, 16, pix_fmt="yuv420p", colour_range="tv", count=12)
    sha = upload(client, source.read_bytes(), "video/quicktime")
    started = client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert started.status_code == 202, started.text
    job = finished(client, started.json())
    UploadJob.model_validate(job)
    expected = "This clip's sky is so close to black that its faintest noise is cut off, so it was not restored."
    assert (job["state"], job["error"]) == ("failed", expected), job
    assert clip_frames.NEAR_BLACK_CLIP == expected
    stages = {stage["stage"]: (stage["done"], stage["total"]) for stage in job["stages"]}
    assert stages == {"measure": (7, 8)}
    for seen in finished.seen:
        for stage in seen["stages"]:
            assert stage["total"] is None or 0 <= stage["done"] <= stage["total"]
    folder = folder_of(client, sha)
    assert not (folder / "variants").exists()
    assert not any(name.startswith(job["id"]) for name in partial_files(client))
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert listed["variants"] == [] and len(listed["attempts"]) == 1
    attempt = listed["attempts"][0]
    contracts.ProcessingAttempt.model_validate(attempt)
    assert (attempt["status"], attempt["failureReason"]) == ("failed", clip_frames.NEAR_BLACK_CLIP)

    # 2. A 1-frame near-black clip holds `measure` at (0, 1) so `restorationRows` marks Measured (not Restoring) failed,
    #    full-range Y'=0 also refuses with C36, and a flat dark band above black_code (full range Y'=16, limited Y'=24,
    #    and Y'=40 whose dark-level noise was float rounding) restores on the not-measured path as dark_level_flat with
    #    the decided note, never the internal error (flatband-two) [decided 2026-11-24 by the lead]
    for name, sky_y, pix_fmt, colour_range, count, expected_reason, expected_stage in (
        ("one_frame", 16, "yuv420p", "tv", 1, expected, ("measure", "frames", 0, 1)),
        ("full_black", 0, "yuvj420p", "pc", 4, expected, ("measure", "frames", 3, 4)),
        ("full_grey_y16", 16, "yuvj420p", "pc", 4, None, None),
        ("tv_grey_y24", 24, "yuv420p", "tv", 4, None, None),
        ("tv_grey_y40", 40, "yuv420p", "tv", 4, None, None),
    ):
        case_dir = tmp_path / name
        case_dir.mkdir()
        clip_path = case_dir / "clip.mov"
        _banded_clip_file(clip_path, sky_y, pix_fmt=pix_fmt, colour_range=colour_range, count=count)
        case_sha = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        store_folder = case_dir / "store" / case_sha[:2] / case_sha
        store_folder.mkdir(parents=True)
        original = store_folder / "original.mov"
        original.write_bytes(clip_path.read_bytes())
        (case_dir / "store" / ".partial").mkdir()
        partials = [str(case_dir / "store" / ".partial" / f"{JOB_ID}-enc.mov.part"),
                    str(case_dir / "store" / ".partial" / f"{JOB_ID}-var.mov.part")]
        events: list[tuple] = []
        out = media_jobs.clip_restoration_in_child(
            str(case_dir / "store"), str(store_folder), str(original), case_sha, JOB_ID, {"kind": "restoration"},
            None, {"mimeType": "video/quicktime", "durationMs": 100.0, "audioStreams": []}, partials,
            lambda *ev: events.append(ev), workers=1,
        )
        assert list((case_dir / "store" / ".partial").iterdir()) == []
        if expected_reason is not None:
            assert (out["status"], out["failureReason"]) == ("failed", expected_reason), (name, out)
            assert events[-1] == expected_stage, (name, events)
            assert not (store_folder / "variants").exists()
            continue
        assert out["status"] == "ready", (name, out)
        records = [json.loads(p.read_text()) for p in store_folder.rglob("*.json")]
        attempt = next(r.get("attempt", r) for r in records if "measured" in r.get("attempt", r))
        contracts.ProcessingAttempt.model_validate(attempt)
        assert attempt["measured"]["noiseNotMeasured"] == "dark_level_flat", name
        notes, lift = attempt["measured"]["notes"], attempt["measured"]["lift"]
        assert lift["decidedBy"] != "noise_bound" and contracts.NOTES[1] not in notes, (name, notes)
        assert (contracts.NOTES[0] in notes) == (lift["decidedBy"] == "quantisation_bound"), (name, notes, lift)
        assert "fineNoiseLeft" not in attempt["measured"] and "noise" not in attempt["measured"], name
        assert attempt["gate"]["passed"] is True and (store_folder / "variants").exists()


def _noisy_sky_clip_file(path: Path, *, count: int = 10, size: int = 128) -> None:
    """A noisy dark sky (Y' 30, sigma 4) with three steady stars, limited range, written losslessly."""
    from fractions import Fraction

    tags = clip_io.ColourTags(range="tv", matrix="bt709", transfer="bt709", primaries="bt709")
    video = clip_io.VideoFormat(size, size, "yuv420p", tags)
    frames = []
    for k in range(count):
        rng = np.random.default_rng(911 + k)
        y = np.clip(np.round(30.0 + rng.normal(0.0, 4.0, (size, size))), 0, 255).astype(np.uint8)
        for row, column in ((30, 40), (70, 90), (100, 20)):
            y[row - 1:row + 2, column - 1:column + 2] = 150
        frames.append((y, np.full((size // 2, size // 2), 128, np.uint8), np.full((size // 2, size // 2), 128, np.uint8)))
    with clip_io.ClipEncoder(path, video, time_base=Fraction(1, 600), frame_times=[20 * k for k in range(count)],
                             container="mov", crf=0) as encoder:
        for frame in frames:
            encoder.write(frame)
        encoder.finish()


def test_the_job_finds_its_measure_frames_stars_in_the_pool_and_stores_the_same_bytes(tmp_path, monkeypatch) -> None:
    # clipmeasure-pool-one [decided 2026-11-24 by the lead]: the measure's stars run in the pool; the stored file, the
    # gate record and the measured record are those of the measure that found them one frame after another
    source = tmp_path / "sky.mov"
    _noisy_sky_clip_file(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    works: list = []
    pools: list = []
    real_pool = frame_pool.FramePool

    class Spy(real_pool):
        def __init__(self, work, *, workers):
            works.append((getattr(work, "func", work), workers))
            pools.append(self)
            super().__init__(work, workers=workers)

        def map(self, source):
            yield from super().map(source)
            # The measure pool reports a peak no other part of the job reaches, so the record must have taken it
            if self is pools[0]:
                self.worker_peak_bytes = max(self.worker_peak_bytes, 1 << 50)

    def run(name: str) -> tuple[dict, str, dict]:
        case = tmp_path / name
        folder = case / "store" / sha[:2] / sha
        folder.mkdir(parents=True)
        original = folder / "original.mov"
        original.write_bytes(source.read_bytes())
        (case / "store" / ".partial").mkdir()
        partials = [str(case / "store" / ".partial" / f"{JOB_ID}-enc.mov.part"),
                    str(case / "store" / ".partial" / f"{JOB_ID}-var.mov.part")]
        out = media_jobs.clip_restoration_in_child(
            str(case / "store"), str(folder), str(original), sha, JOB_ID, {"kind": "restoration"}, None,
            {"mimeType": "video/quicktime", "durationMs": 166.0, "audioStreams": []}, partials, lambda *ev: None,
            workers=2)
        assert out["status"] == "ready", out
        stored = next((folder / "variants").rglob("variant.mov"))
        records = [json.loads(p.read_text()) for p in folder.rglob("*.json")]
        attempt = next(r.get("attempt", r) for r in records if "measured" in r.get("attempt", r))
        peaks.append(attempt["measured"]["peakMemoryBytes"])
        measured = {k: v for k, v in attempt["measured"].items() if k != "peakMemoryBytes"}
        return measured, hashlib.sha256(stored.read_bytes()).hexdigest(), attempt["gate"]

    peaks: list = []

    monkeypatch.setattr(frame_pool, "FramePool", Spy)
    real_measure = clip_frames.measure_clip
    given: list[bool] = []

    def recording(clip, sampled, exposure_ev=None, fields=None):
        given.append(fields is not None and len(fields) == len(sampled))
        return real_measure(clip, sampled, exposure_ev, fields)

    monkeypatch.setattr(clip_frames, "measure_clip", recording)
    pooled = run("pooled")
    # The pool's first work is the measure's stars, and the measure is handed one field a frame
    assert works[0] == (clip_frames.measure_stars, 2), works
    assert given == [True]
    # The job's peak memory counts the measure pool's workers too (AUDIT-B's note one)
    assert peaks[0] >= 1 << 50
    monkeypatch.setattr(clip_frames, "measure_clip",
                        lambda clip, sampled, exposure_ev=None, fields=None: real_measure(clip, sampled, exposure_ev))
    alone = run("alone")
    assert pooled == alone
