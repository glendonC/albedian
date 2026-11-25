"""wait-detections-zero (DECIDED-BY-LEAD 25 Nov 03:26, on PLATFORM's note of what a running job reports): the job
record says when a solve published its measured detections, the same list its solution later holds as observed, so
the wait can show the photo's own measured stars. `measuredAtUTC` follows the early sky's stamp (`predictedSkyAtUTC`):
only a solve that started may carry it, never a media job or a solve still queued, and it lies within the run.
Both sides read the same samples (tests/fixtures/job_contract.json, packages/contracts/src/__tests__/jobs.test.ts)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from services.contracts import jobs

SAMPLES = jobs.build_job_samples()


def _by(kind: str, state: str) -> dict[str, Any]:
    return copy.deepcopy(next(r for r in SAMPLES["valid"] if r["kind"] == kind and r["state"] == state
                              and "measuredAtUTC" not in r and "predictedSkyAtUTC" not in r
                              and (kind != "solve" or "request" not in r)))


def test_the_stamp_is_a_state_field_with_the_early_skys_rule() -> None:
    assert "measuredAtUTC" in jobs.JOB_STATE_FIELDS
    for group, rows in jobs.JOB_STATE_TABLE.items():
        for state, row in rows.items():
            if row is not None:
                assert row["fields"]["measuredAtUTC"] == row["fields"]["predictedSkyAtUTC"], (group, state)


def test_a_running_or_ended_solve_may_say_when_it_measured() -> None:
    for state in ("running", "succeeded", "failed", "interrupted"):
        record = _by("solve", state)
        # Its own start: the stamp lies within the run, its ends included
        record["measuredAtUTC"] = record["startedAtUTC"]
        assert jobs.UploadJob.model_validate(record).measured_at_utc is not None, state


@pytest.mark.parametrize(
    ("kind", "state", "at"),
    [("solve", "queued", "2031-05-02T19:20:00Z"), ("restoration", "succeeded", "2031-05-02T19:20:10Z"),
     ("solve", "running", "2031-05-02T19:20:01Z"), ("solve", "succeeded", "2031-05-02T19:20:42Z")],
    ids=["queued solve", "media job", "before the start", "after the finish"],
)
def test_a_stamp_out_of_place_is_refused(kind: str, state: str, at: str) -> None:
    with pytest.raises(ValidationError):
        jobs.UploadJob.model_validate({**_by(kind, state), "measuredAtUTC": at})


def test_the_samples_carry_it_both_ways() -> None:
    valid = [r for r in SAMPLES["valid"] if "measuredAtUTC" in r]
    bad = [case for case in SAMPLES["invalid"] if "measuredAtUTC" in case["sample"]]
    assert valid and len(bad) >= 4
