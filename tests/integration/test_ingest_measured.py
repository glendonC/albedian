"""wait-detections-one, the publishing unit (DECIDED-BY-LEAD 25 Nov 03:26; REQ-JOB-2.8): when a solve's detect stage
ends, the job publishes the detections it measured, the same list its solution later holds as observed, and its record
says when (`measuredAtUTC`). `GET /api/ingest/jobs/{job_id}/measured` serves them while the job runs and 404 before,
after the job ends (its solution then holds them) and for a job it does not know, as the predicted-sky route does."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from services.contracts.astronomy import SourceDetection
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.runner import JobContext, KindResult
from tests.integration.test_job_routes import BOUND_S, _png, _starfield, _until_state, setup  # noqa: F401

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "observation_solution_contract.json"


def _detections(sha: str) -> list[dict[str, Any]]:
    solution = json.loads(FIXTURE.read_text(encoding="utf-8"))["valid"]["calibrated"]
    observed = [f["sourceDetection"] for f in solution["findings"] if f.get("sourceDetection")]
    assert observed, "the fixture's calibrated solution holds measured detections"
    return [{**d, "assetId": f"src_{sha[:16]}"} for d in observed]


def test_the_route_serves_a_running_solves_measured_detections_and_nothing_else(
        setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake, sha = setup["client"], setup["fake"], setup["shas"][0]
    measured = _detections(sha)
    running, told = threading.Event(), threading.Event()
    real_run = fake.run

    def run(ctx: JobContext) -> KindResult:
        running.set()
        assert told.wait(BOUND_S)
        ctx.measured(measured)
        return real_run(ctx)

    monkeypatch.setitem(job_runner._KINDS, "solve", dataclasses.replace(ingest_jobs.SOLVE_KIND, run=run))
    job = client.post(f"/api/ingest/{sha}/solve").json()
    assert client.get("/api/ingest/jobs/job_" + "0" * 32 + "/measured").status_code == 404
    assert running.wait(BOUND_S)
    before = client.get(f"/api/ingest/jobs/{job['id']}/measured")
    assert (before.status_code, before.json()) == (404, {"detail": ingest_router.NO_MEASURED})
    told.set()
    deadline = time.monotonic() + BOUND_S
    while "measuredAtUTC" not in client.get(f"/api/ingest/jobs/{job['id']}").json():
        assert time.monotonic() < deadline, "the record never said when it measured"
        time.sleep(0.02)
    during = client.get(f"/api/ingest/jobs/{job['id']}/measured")
    assert during.status_code == 200
    assert during.json() == {"sourceAssetSha256": sha, "detections": measured}
    fake.release.set()
    ended = _until_state(client, job["id"], ("succeeded",))
    assert "measuredAtUTC" in ended
    assert client.get(f"/api/ingest/jobs/{job['id']}/measured").status_code == 404


def test_only_a_solve_publishes_and_only_detections(setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, fake, sha = setup["client"], setup["fake"], setup["shas"][0]
    refused: list[str] = []
    real_run = fake.run

    def run(ctx: JobContext) -> KindResult:
        for bad in ([{"xPx": "twelve"}], [{"not": "a detection"}], "detections"):
            try:
                ctx.measured(bad)  # type: ignore[arg-type]
            except ValueError:
                refused.append(repr(bad))
        return real_run(ctx)

    monkeypatch.setitem(job_runner._KINDS, "solve", dataclasses.replace(ingest_jobs.SOLVE_KIND, run=run))
    job = client.post(f"/api/ingest/{sha}/solve").json()
    fake.release.set()
    ended = _until_state(client, job["id"], ("succeeded",))
    assert len(refused) == 3
    assert "measuredAtUTC" not in ended, "a refused list stamps nothing"


def test_the_real_solve_publishes_the_detections_its_solution_holds_as_observed(
        setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, sha = setup["client"], None
    # The real solve kind on a synthetic star field, its published list caught as the runner keeps it
    monkeypatch.setitem(job_runner._KINDS, "solve", ingest_jobs.SOLVE_KIND)
    caught: list[list[dict[str, Any]]] = []
    real_measured = job_runner.Runner._measured

    def catch(self: Any, job: Any, detections: Any) -> None:
        caught.append(json.loads(json.dumps(list(detections))))
        real_measured(self, job, detections)

    monkeypatch.setattr(job_runner.Runner, "_measured", catch)
    body = _png(_starfield())
    stored = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert stored.status_code in (200, 201)
    sha = stored.json()["sourceAsset"]["sha256"]
    job = client.post(f"/api/ingest/{sha}/solve").json()
    ended = _until_state(client, job["id"], ("succeeded", "failed"))
    assert ended["state"] == "succeeded", ended
    assert "measuredAtUTC" in ended and ended["startedAtUTC"] <= ended["measuredAtUTC"] <= ended["finishedAtUTC"]
    assert len(caught) == 1
    for detection in caught[0]:
        SourceDetection.model_validate(detection)
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    observed = [f["sourceDetection"] for f in solution["findings"] if f["evidenceClass"] == "observed" and f.get("sourceDetection")]
    assert observed and all(d in caught[0] for d in observed), "every observed finding's detection was published"


def test_a_binned_stills_detections_are_published_in_its_stored_pixels() -> None:
    # bin-detect-v2 searches a large still binned; its solution restates each observed detection in the stored pixels
    # (services/science/binning.py in_stored_pixels), and the published list is restated the same way
    from services.science.binning import _detection as stored_detection
    from services.science.wcs_models import SourceDetection as Detection

    detection = Detection(x_px=100.0, y_px=50.0, flux=900.0, snr=30.0, fwhm_px=2.0, centroid_uncertainty_px=0.1)
    told: list[list[dict[str, Any]]] = []
    ingest_jobs._publish_measured(told.append, [detection], "src_0123456789abcdef", 4)
    assert told == [[stored_detection(detection, 4).to_contract_dict(asset_id="src_0123456789abcdef")]]
    assert told[0][0]["xPx"] != detection.x_px, "restated, not the binned position"
    # A failure telling it publishes nothing and raises nothing
    ingest_jobs._publish_measured(lambda _: (_ for _ in ()).throw(RuntimeError("down")), [detection], "src_0123456789abcdef", 1)
