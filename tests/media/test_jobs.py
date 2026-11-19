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
  tile grids, the decoder's sentence). A clip gets no display copy (its contract sentence) and no restoration
  yet (501, no job); a malformed hash or id is 422, an unknown one 404.
- In this process: a rejected restoration keeps its attempt and no variant; an original that changes before or
  after the work fails the job; the child's outcome is the job's ending; a job whose variant is gone runs again,
  and a variant folder that lacks a file is never overwritten.
"""

from __future__ import annotations

import hashlib
import io
import json
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
from services.media import decode, png16

# The kinds start ffmpeg and a child process: each test has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

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
    assert job["state"] == "succeeded", job
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
    for name in ("decode", "restore", "encode", "verify"):
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
    assert job["state"] == "succeeded", job
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


def test_a_clip_gets_no_display_copy_and_no_restoration_yet(client, tmp_path) -> None:
    clip = tmp_path / "clip.mp4"
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                           "testsrc2=s=64x48:r=30:d=0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-600:]
    sha = upload(client, clip.read_bytes(), "video/mp4")
    refused = client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"})
    assert (refused.status_code, refused.json()["detail"]) == (501, "Restoring a clip is not built yet.")
    assert not (folder_of(client, sha) / "jobs").exists() or not any((folder_of(client, sha) / "jobs").iterdir())
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
    sha = upload(client, png8_with_title())
    folder = folder_of(client, sha)
    for name, created in (("var_" + "a" * 16, "2026-11-19T02:00:00Z"), ("var_" + "b" * 16, "2026-11-19T01:00:00Z")):
        (folder / "variants" / name).mkdir(parents=True)
        (folder / "variants" / name / "variant.json").write_text(json.dumps({"id": name, "createdAtUTC": created}))
    (folder / "attempts").mkdir()
    for name, ended in (("att_" + "a" * 16, "2026-11-19T02:00:00Z"), ("att_" + "b" * 16, "2026-11-19T01:00:00Z")):
        (folder / "attempts" / f"{name}.json").write_text(json.dumps({"id": name, "finishedAtUTC": ended}))
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert [v["id"] for v in listed["variants"]] == ["var_" + "b" * 16, "var_" + "a" * 16]
    assert [a["id"] for a in listed["attempts"]] == ["att_" + "b" * 16, "att_" + "a" * 16]
