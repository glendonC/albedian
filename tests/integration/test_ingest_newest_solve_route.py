"""job-by-upload-one (DECIDED-BY-LEAD 24 Nov 22:38, for WEB-UX's open-direct-two): `GET /api/ingest/{sha256}/solve`
gives the newest solve job of a stored upload, active or ended, in the job record's shape, read from the file's hash
alone: a page that reloads during a solve (a phone's browser does that whenever the tab comes back) finds its job
without a job id. Read-only: it starts nothing. The same guards as the solution route: 404 for a file not stored, 404
when no solve of it was started, 422 for an address that is not a hash. The record is the one
`GET /api/ingest/jobs/{job_id}` serves for that job, the live one while this server runs it; the job is the one the
receipt names (`solveJobId`, the newest readable attempt).
"""

from __future__ import annotations

import json
from typing import Any

from services.contracts.jobs import INTERRUPTED_SENTENCE, UploadJob
from services.ingest import router as ingest_router
from services.ingest.job_store import new_job_id
from tests.integration.test_job_routes import BOUND_S, _until_state, setup  # noqa: F401  (setup is a fixture)


def _newest(client: Any, sha: str) -> Any:
    return client.get(f"/api/ingest/{sha}/solve")


def test_a_running_solve_is_served_live_as_the_job_route_serves_it(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    job = client.post(f"/api/ingest/{sha}/solve?wait=false").json()
    assert fake.entered.wait(BOUND_S)
    served = _newest(client, sha)
    assert served.status_code == 200, served.text
    UploadJob.model_validate(served.json())
    assert served.json() == client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert served.json()["state"] == "running" and {s["stage"] for s in served.json()["stages"]} == {"store", "decode"}
    assert served.json()["id"] == client.get(f"/api/ingest/{sha}").json()["solveJobId"]
    fake.release.set()
    ended = _until_state(client, job["id"], ("succeeded",))
    assert _newest(client, sha).json() == ended
    assert fake.runs == [job["id"]], "reading the newest solve starts none"


def test_the_newest_attempt_is_served_after_a_stopped_one(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    first = client.post(f"/api/ingest/{sha}/solve?wait=false").json()
    assert fake.entered.wait(BOUND_S)
    client.post(f"/api/ingest/jobs/{first['id']}/cancel")
    canceled = _until_state(client, first["id"], ("canceled",))
    # A stopped solve is the newest until another is started: the page can show the stopped screen
    assert _newest(client, sha).json() == canceled
    second = client.post(f"/api/ingest/{sha}/solve?wait=false").json()
    assert second["id"] != first["id"]
    assert _newest(client, sha).json()["id"] == second["id"]
    fake.release.set()
    _until_state(client, second["id"], ("succeeded",))


def test_one_upload_is_not_answered_with_anothers_job(setup: dict[str, Any]) -> None:
    client, (sha_a, sha_b), fake = setup["client"], setup["shas"], setup["fake"]
    job = client.post(f"/api/ingest/{sha_a}/solve?wait=false").json()
    assert fake.entered.wait(BOUND_S)
    assert _newest(client, sha_a).json()["id"] == job["id"]
    assert (_newest(client, sha_b).status_code, _newest(client, sha_b).json()) == (404, {"detail": ingest_router.NO_SOLVE_JOB})
    fake.release.set()


def test_the_guards_of_the_solution_route(setup: dict[str, Any]) -> None:
    client, sha, fake = setup["client"], setup["shas"][0], setup["fake"]
    never = "f" * 64
    for path in (f"/api/ingest/{never}/solve", f"/api/ingest/{never}/solution"):
        assert (client.get(path).status_code, client.get(path).json()) == (404, {"detail": "no file with that hash is stored"})
    unsolved = _newest(client, sha)
    assert (unsolved.status_code, unsolved.json()) == (404, {"detail": "no solve job is stored for that file"})
    for bad in ("F" * 64, "f" * 63, "f" * 64 + "0", "..%2F" + "f" * 60):
        assert client.get(f"/api/ingest/{bad}/solve").status_code == client.get(f"/api/ingest/{bad}/solution").status_code, bad
    assert client.get(f"/api/ingest/{'F' * 64}/solve").status_code == 422
    assert fake.runs == [] and setup["jobs"].jobs_of(sha) == [], "a read makes no job"


def test_todays_solve_record_is_served_as_the_job_route_serves_it(setup: dict[str, Any]) -> None:
    client, sha, store = setup["client"], setup["shas"][0], setup["store"]
    legacy = {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "failed",
              "createdAtUTC": "2020-01-01T05:00:00Z", "startedAtUTC": "2020-01-01T05:00:01Z",
              "finishedAtUTC": "2020-01-01T05:00:02Z", "error": "The solve stopped on an internal error.",
              "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    (store.folder(sha) / "job.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert (_newest(client, sha).status_code, _newest(client, sha).json()) == (200, legacy)


def test_a_running_record_no_process_holds_is_served_interrupted_as_the_job_route_serves_it(setup: dict[str, Any]) -> None:
    client, sha, jobs = setup["client"], setup["shas"][0], setup["jobs"]
    made = jobs.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "running",
                              "createdAtUTC": "2030-01-01T05:00:00Z", "startedAtUTC": "2030-01-01T05:00:01Z",
                              "producer": {"name": "albedian.solve_chain", "revision": 1}, "stages": []})
    served = _newest(client, sha).json()
    assert (served["id"], served["state"], served["error"]) == (made["id"], "interrupted", INTERRUPTED_SENTENCE)
    assert jobs.read_job(made["id"]) == served


def test_an_unreadable_record_is_left_out_as_the_receipt_leaves_it_out(setup: dict[str, Any]) -> None:
    client, sha, jobs = setup["client"], setup["shas"][0], setup["jobs"]
    older = jobs.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "canceled",
                               "createdAtUTC": "2030-01-01T05:00:00Z", "finishedAtUTC": "2030-01-01T05:00:02Z", "stages": []})
    newer = jobs.write_record({"id": new_job_id(sha), "sourceAssetSha256": sha, "kind": "solve", "state": "queued",
                               "createdAtUTC": "2030-01-02T05:00:00Z", "stages": []})
    jobs.record_path(newer["id"]).write_text("{}", encoding="utf-8")
    assert _newest(client, sha).json()["id"] == older["id"] == client.get(f"/api/ingest/{sha}").json()["solveJobId"]
    assert jobs.record_path(newer["id"]).read_text(encoding="utf-8") == "{}", "an unreadable record is never rewritten"


def test_the_address_holds_the_hash_alone_and_the_route_reads_only(setup: dict[str, Any]) -> None:
    operations = setup["app"].openapi()["paths"]["/api/ingest/{sha256}/solve"]
    assert set(operations) == {"get", "post"}
    get = operations["get"]
    # No query and no body: no name, time or place in the address, only the file's hash
    assert [(p["name"], p["in"]) for p in get["parameters"]] == [("sha256", "path")]
    assert "requestBody" not in get
