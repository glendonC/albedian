"""playback-firstneed-job (DECIDED-BY-LEAD 22:19 on 24 Nov and 04:47 on 25 Nov; REQ-JOB-10): a restored clip stored with
no playback copy gets one at first need, through `POST /api/media/{sha256}/variants/{variantId}/playback`, made and
gated as a new restoration's and placed beside the variant already published; a gate's refusal is that job's answer
while its revision stands; a clip it cannot copy never gets a job. Every clip here is synthetic (ffmpeg's own sky)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api import create_app
from services.contracts import jobs as job_contract
from services.contracts import media as contracts
from services.ingest import router as ingest_router
from services.ingest.job_store import JobStore
from services.ingest.store import IngestStore
from services.media import clip_io, playback_jobs

POLL_S = 0.1
JOB_BOUND_S = 240.0
SKY = "color=c=0x181818:s=96x64:r=30:d=0.5,noise=alls=14:allf=t"


def finished(client: TestClient, job: dict) -> dict:
    deadline = time.monotonic() + JOB_BOUND_S
    while job["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the job did not end within the test's bound"
        time.sleep(POLL_S)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    return job


def clip(path: Path, *codec: str) -> bytes:
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", SKY, *codec, str(path)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]
    return path.read_bytes()


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    """One store with an 8-bit and a 10-bit clip each restored ready: the client, the store root, and for each the
    upload's hash, its folder and its variant id."""
    base = tmp_path_factory.mktemp("firstneed")
    store_root = base / "store"
    uploads = IngestStore(store_root, max_bytes=200_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    done = {}
    with TestClient(app) as client:
        for name, codec in (("eight", ("-c:v", "libx264", "-pix_fmt", "yuv420p")),
                            ("ten", ("-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1"))):
            data = clip(base / f"{name}.mp4", *codec)
            answer = client.post("/api/ingest", content=data, headers={"content-type": "video/mp4"})
            assert answer.status_code in (200, 201), answer.text
            sha = answer.json()["sourceAsset"]["sha256"]
            job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "restoration"}).json())
            assert job["state"] == "succeeded", job
            done[name] = (sha, store_root / sha[:2] / sha, job["variantId"])
        yield client, store_root, done


def take_copy_away(folder: Path, vid: str) -> None:
    """As a restoration stored before restore revision 16: its folder without a playback copy."""
    for name in ("playback.json", "playback.mp4"):
        path = folder / "variants" / vid / name
        if path.exists():
            path.unlink()


def test_a_restored_clip_stored_without_a_copy_gets_one_at_first_need_and_its_own_files_stay_as_they_were(stored) -> None:
    client, _, done = stored
    sha, folder, vid = done["eight"]
    take_copy_away(folder, vid)
    variant_folder = folder / "variants" / vid
    before = {p.name: p.read_bytes() for p in variant_folder.iterdir()}
    assert "playbackCopy" not in client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]
    started = client.post(f"/api/media/{sha}/variants/{vid}/playback")
    assert started.status_code == 202, started.text
    job = started.json()
    job_contract.UploadJob.model_validate(job)
    assert (job["kind"], job["request"], "graphId" in job) == ("playback_copy", {"kind": "playback_copy", "variantId": vid}, False)
    ended = finished(client, job)
    assert ended["state"] == "succeeded" and ended["variantId"] == vid, ended
    assert ended["producer"] == {"name": "albedian.playback", "revision": 1}
    copy = client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]["playbackCopy"]
    served = client.get(copy["uri"])
    assert served.status_code == 200 and hashlib.sha256(served.content).hexdigest() == copy["sha256"]
    assert copy["minPsnrDb"] >= contracts.PLAYBACK_COPY_MIN_PSNR_DB
    # The variant's own file and records are the bytes they were; only the copy and its record are new, each 0400
    after = {p.name: p.read_bytes() for p in variant_folder.iterdir()}
    assert {k: v for k, v in after.items() if not k.startswith("playback.")} == before
    assert all(os.stat(variant_folder / n).st_mode & 0o777 == 0o400 for n in ("playback.json", "playback.mp4"))
    # Asked again: the copy exists, so no job
    again = client.post(f"/api/media/{sha}/variants/{vid}/playback")
    assert (again.status_code, again.json()) == (409, {"detail": "that variant has a playback copy"})


def test_a_stored_gate_refusal_is_the_answer_under_its_own_revision_and_tried_again_under_another(stored) -> None:
    client, store_root, done = stored
    sha, folder, vid = done["eight"]
    take_copy_away(folder, vid)
    jobs = JobStore(IngestStore(store_root, max_bytes=200_000_000))
    refusal = {"id": f"job_{sha[:16]}" + "a" * 16, "sourceAssetSha256": sha, "kind": "playback_copy", "state": "failed",
               "createdAtUTC": "2031-05-02T19:20:00Z", "startedAtUTC": "2031-05-02T19:20:01Z",
               "finishedAtUTC": "2031-05-02T19:20:02Z", "stages": [], "error": job_contract.PLAYBACK_GATE_REFUSED,
               "producer": {"name": "albedian.playback", "revision": 1}, "request": {"kind": "playback_copy", "variantId": vid}}
    jobs.write_record(refusal)
    answer = client.post(f"/api/media/{sha}/variants/{vid}/playback")
    assert answer.status_code == 200 and answer.json()["id"] == refusal["id"], answer.text
    # Another revision's refusal is no answer: a new job runs, and copies the clip
    jobs.write_record({**refusal, "id": f"job_{sha[:16]}" + "b" * 16, "createdAtUTC": "2031-05-02T19:21:00Z",
                       "startedAtUTC": "2031-05-02T19:21:01Z", "finishedAtUTC": "2031-05-02T19:21:02Z",
                       "producer": {"name": "albedian.playback", "revision": 2}})
    newer = client.post(f"/api/media/{sha}/variants/{vid}/playback")
    assert newer.status_code == 202, newer.text
    assert finished(client, newer.json())["state"] == "succeeded"


def test_a_clip_the_job_cannot_copy_is_refused_with_the_typed_answer_and_gets_no_job(stored) -> None:
    client, store_root, done = stored
    sha, folder, vid = done["ten"]
    take_copy_away(folder, vid)
    jobs_before = sorted(p.name for p in (folder / "jobs").iterdir())
    answer = client.post(f"/api/media/{sha}/variants/{vid}/playback")
    assert (answer.status_code, answer.json()) == (422, {"detail": job_contract.PLAYBACK_NOT_COPYABLE})
    assert sorted(p.name for p in (folder / "jobs").iterdir()) == jobs_before


def test_no_job_is_made_for_what_is_not_a_stored_restored_clip(stored) -> None:
    client, _, done = stored
    sha, _, vid = done["eight"]
    assert client.post(f"/api/media/{sha}/variants/var_{'0' * 16}/playback").status_code == 404
    assert client.post(f"/api/media/{'0' * 64}/variants/{vid}/playback").status_code == 404


def test_the_childs_own_gate_refusal_ends_with_its_fixed_sentence_and_places_nothing(stored, monkeypatch, tmp_path) -> None:
    _, _, done = stored
    sha, folder, vid = done["eight"]
    take_copy_away(folder, vid)
    monkeypatch.setattr(playback_jobs, "PLAYBACK_COPY_MIN_PSNR_DB", 99)
    partials = [str(tmp_path / "encoded.part"), str(tmp_path / "playback.part")]
    out = playback_jobs.playback_copy_in_child(str(folder), vid, partials, lambda *args: None, workers=1)
    assert out == {"status": "failed", "failureReason": job_contract.PLAYBACK_GATE_REFUSED}
    assert not (folder / "variants" / vid / "playback.json").exists() and list(tmp_path.iterdir()) == []


def test_a_lossless_file_changed_under_the_copy_ends_it_with_the_changed_sentence(stored, monkeypatch, tmp_path) -> None:
    _, _, done = stored
    sha, folder, vid = done["eight"]
    take_copy_away(folder, vid)
    monkeypatch.setattr(playback_jobs, "_sha256", lambda path: "0" * 64)
    made = []
    monkeypatch.setattr(clip_io, "make_playback_copy", lambda *args, **kwargs: made.append(1))
    out = playback_jobs.playback_copy_in_child(str(folder), vid, [str(tmp_path / "e"), str(tmp_path / "p")],
                                               lambda *args: None, workers=1)
    assert out == {"status": "failed", "failureReason": "The restored clip changed while its copy was made."}
    # A file already changed is never copied: the check comes before the work
    assert made == []


def test_only_this_revisions_gate_refusal_answers(stored) -> None:
    base = {"error": job_contract.PLAYBACK_GATE_REFUSED, "producer": {"name": "albedian.playback", "revision": 1}}
    assert playback_jobs.gate_refused_here(base)
    assert not playback_jobs.gate_refused_here({**base, "producer": {"name": "albedian.playback", "revision": 2}})
    assert not playback_jobs.gate_refused_here({**base, "error": "The playback copy stopped at its time limit."})
    assert not playback_jobs.gate_refused_here({**base, "producer": {"name": "albedian.restore", "revision": 1}})
    assert clip_io.PLAYBACK_GATE_FORMATS
