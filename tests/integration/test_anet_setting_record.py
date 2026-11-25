"""A solve made with the opt-in astrometry.net stage on says so in its record (anet-setting-record-one; the lead's order).

A plain solve made with the stage on names it in the solution's `candidateStages`, whatever it found; off, the field
is absent and the record is main's. With the stage on, a stored plain solve that did not calibrate and does not name
the stage was made with it off, so its next start solves it again; a calibrated one never is, nor a solve from a
person's alignment, nor anything with the stage off.

The program is the stand-in of tests/science/test_anet_stage.py, writing a TAN header of the synthetic frame's own
pose [simulated]; the app's own searches stand in as refusing, or as the phone solver's real solve of the frame.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from services.contracts.astronomy import CANDIDATE_STAGES_SOLVED
from services.ingest import jobs as ingest_jobs
from services.science import anet_stage
from services.science import phone_photo_solver as pps
from services.science.wcs_models import ContractRefusal
from tests.integration.test_anet_stage_chain import _frame, _header_for, _unresolved_like_the_search, client  # noqa: F401
from tests.science.test_anet_stage import stand_in  # noqa: F401 (the stand-in fixture)


def _stage(monkeypatch: pytest.MonkeyPatch, stand_in, on: bool) -> None:
    if on:
        monkeypatch.setenv(anet_stage.ENV_ON, "1")
        monkeypatch.setenv(anet_stage.ENV_BIN, str(Path(stand_in.settings.solve_field).parent))
        monkeypatch.setenv(anet_stage.ENV_INDEX, stand_in.settings.index_dir)
    else:
        monkeypatch.delenv(anet_stage.ENV_ON, raising=False)


def _refusing(solve_image: Any, *args: Any, **kwargs: Any) -> Any:
    # A real refused solution of this job's own upload: its asset id, hash and solution id
    return pps.solve_phone_photo(
        solve_image, detections=[], source_asset_id=kwargs["source_asset_id"], source_sha256=kwargs["sha256"],
        solution_id=kwargs["solution_id"],
    )


def _calibrating(solve_image: Any, *args: Any, **kwargs: Any) -> Any:
    # The phone solver's own solve of the frame at its time and place, with this job's ids: a calibration
    spec = _frame().spec
    return pps.solve_phone_photo(
        solve_image, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg,
        fov_diagonal_deg=spec.fov_deg, source_asset_id=kwargs["source_asset_id"], source_sha256=kwargs["sha256"],
        solution_id=kwargs["solution_id"],
    )


def _upload(client: TestClient) -> str:
    out = io.BytesIO()
    Image.fromarray(_frame().rgb, mode="RGB").save(out, format="PNG")
    body = out.getvalue()
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    return hashlib.sha256(body).hexdigest()


def _solve(client: TestClient, sha: str) -> tuple[dict, dict]:
    """A start of the photo's solve, to its end: the job it answers and the solution served."""
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job.get("error")
    return job, client.get(f"/api/ingest/{sha}/solution").json()


@pytest.mark.parametrize(("on", "stage_finds"), [(True, True), (True, False), (False, False)])
def test_a_solve_made_with_the_stage_on_names_it_whatever_it_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in, on: bool, stage_finds: bool
) -> None:
    # The searches refuse; the stage's candidate is the frame's pose, or that pose turned 40 deg, which the check refuses
    stand_in.plan(header=_header_for(0.0 if stage_finds else 40.0))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _refusing)
    _stage(monkeypatch, stand_in, on)
    _, solution = _solve(client, _upload(client))
    if on:
        assert solution["candidateStages"] == ["astrometry.net"]
        assert (solution["status"] == "calibrated") == stage_finds
        assert ("candidateSource" in solution) == stage_finds
    else:
        assert "candidateStages" not in solution and "candidateSource" not in solution
        assert stand_in.calls() == []


def test_a_calibration_by_the_apps_own_search_with_the_stage_on_names_it_and_the_program_never_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in
) -> None:
    stand_in.plan(header=_header_for())
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _calibrating)
    _stage(monkeypatch, stand_in, True)
    _, solution = _solve(client, _upload(client))
    assert solution["status"] == "calibrated" and solution["candidateStages"] == ["astrometry.net"]
    assert "candidateSource" not in solution and stand_in.calls() == []


def test_with_the_stage_turned_on_a_stored_solve_that_did_not_calibrate_is_solved_again_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in
) -> None:
    stand_in.plan(header=_header_for())
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _refusing)
    sha = _upload(client)
    # Off: refused, and a second start answers the same job
    _stage(monkeypatch, stand_in, False)
    first, solution = _solve(client, sha)
    assert solution["status"] != "calibrated" and "candidateStages" not in solution
    assert _solve(client, sha)[0]["id"] == first["id"]
    # On: the stored refusal does not name the stage, so the next start solves it again, and the stage calibrates it
    _stage(monkeypatch, stand_in, True)
    second, solution = _solve(client, sha)
    assert second["id"] != first["id"]
    assert solution["status"] == "calibrated" and solution["candidateStages"] == ["astrometry.net"]
    # It is never solved again by this: the next start, on or off, answers that job
    assert _solve(client, sha)[0]["id"] == second["id"]
    _stage(monkeypatch, stand_in, False)
    assert _solve(client, sha)[0]["id"] == second["id"]
    assert len(stand_in.calls()) > 0


def test_a_solve_the_stage_was_on_for_is_not_solved_again_when_it_still_did_not_calibrate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in
) -> None:
    stand_in.plan(header=_header_for(40.0))
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _refusing)
    _stage(monkeypatch, stand_in, True)
    sha = _upload(client)
    first, solution = _solve(client, sha)
    assert solution["status"] != "calibrated" and solution["candidateStages"] == ["astrometry.net"]
    calls = len(stand_in.calls())
    assert _solve(client, sha)[0]["id"] == first["id"] and len(stand_in.calls()) == calls


def test_a_stored_calibration_made_with_the_stage_off_is_never_solved_again_when_it_is_turned_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in
) -> None:
    stand_in.plan(header=_header_for())
    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _calibrating)
    sha = _upload(client)
    _stage(monkeypatch, stand_in, False)
    first, solution = _solve(client, sha)
    assert solution["status"] == "calibrated" and "candidateStages" not in solution
    _stage(monkeypatch, stand_in, True)
    assert _solve(client, sha)[0]["id"] == first["id"]
    assert stand_in.calls() == []


@pytest.mark.parametrize("status", ["calibrated", "approximate", "predicted_only", "unresolved"])
@pytest.mark.parametrize("named", [True, False])
@pytest.mark.parametrize("person", [True, False])
@pytest.mark.parametrize("on", [True, False])
def test_only_a_plain_solve_that_did_not_calibrate_and_does_not_name_the_stage_awaits_it_and_only_with_it_on(
    monkeypatch: pytest.MonkeyPatch, status: str, named: bool, person: bool, on: bool
) -> None:
    monkeypatch.setattr(anet_stage, "settings_from_env", lambda env=None: object() if on else None)
    monkeypatch.setattr(ingest_jobs, "record_subject", lambda record: "a" * 64 if person else None)
    solution: dict[str, Any] = {"status": status, **({"candidateStages": ["astrometry.net"]} if named else {})}
    awaits = ingest_jobs._awaits_the_stage({"kind": "solve"}, solution)
    assert awaits == (on and status != "calibrated" and not named and not person)


def test_the_stages_a_solve_names_follow_the_contract_in_the_domain_record() -> None:
    from dataclasses import replace

    refused = _unresolved_like_the_search()
    named = replace(refused, candidate_stages=("astrometry.net",))
    assert named.to_contract_dict()["candidateStages"] == ["astrometry.net"]
    assert "candidateStages" not in refused.to_contract_dict()
    for wrong in ((), ("solve-field",), ("astrometry.net", "astrometry.net"), ("/usr/local/bin/solve-field",)):
        with pytest.raises(ContractRefusal):
            replace(refused, candidate_stages=wrong)
    with pytest.raises(ContractRefusal, match=CANDIDATE_STAGES_SOLVED[:40]):
        replace(refused, status="pending", candidate_stages=("astrometry.net",))
