"""Ingest contracts (roadmap Track 1 M1): Pydantic rules, properties, and the fixture shared with Zod.

`tests/fixtures/ingest_contract.json` is written from `build_ingest_samples()` and parsed by
packages/contracts/src/__tests__/ingest.test.ts, so a rule that changes on one side only fails a
test. Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_INGEST_FIXTURE=1 uv run pytest tests/integration/test_ingest_contract.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import ingest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "ingest_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_INGEST_FIXTURE"
MODELS = {"SourceAsset": ingest.SourceAsset, "IngestReceipt": ingest.IngestReceipt, "SolveJob": ingest.SolveJob}


def test_ingest_fixture_is_current() -> None:
    samples = ingest.build_ingest_samples()
    text = json.dumps(samples, indent=2, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_every_valid_sample_round_trips_and_every_invalid_one_is_rejected() -> None:
    samples = ingest.build_ingest_samples()
    for name, valid in samples["valid"].items():
        assert valid, name
        for sample in valid:
            # Unset fields stay out; explicit nulls (an unknown offset, an unknown total) stay in
            assert MODELS[name].model_validate(sample).model_dump(by_alias=True, exclude_unset=True) == sample
    assert len(samples["invalid"]) >= 20
    for case in samples["invalid"]:
        with pytest.raises(ValidationError):
            MODELS[case["schema"]].model_validate(case["sample"])


def test_missing_metadata_stays_missing() -> None:
    receipt = ingest.build_ingest_samples()["valid"]["IngestReceipt"][0]
    bare = {**receipt, "hints": {}, "sourceAsset": {k: v for k, v in receipt["sourceAsset"].items()
                                                    if k not in ("capturedAtUTC", "observerLocation", "cameraMetadata", "metadataSources")}}
    parsed = ingest.IngestReceipt.model_validate(bare)
    assert parsed.hints.capture_time is None and parsed.source_asset.captured_at_utc is None
    # A zoneless local time is recorded as such; it never becomes a guessed UTC
    zoneless = {"localDateTime": "2031-05-02T21:14:09", "utcOffsetMinutes": None, "utc": None,
                "source": {"container": "exif", "tags": ["DateTimeOriginal"]}}
    assert ingest.CaptureTimeHint.model_validate(zoneless).utc is None
    with pytest.raises(ValidationError):
        ingest.CaptureTimeHint.model_validate({**zoneless, "utc": "2031-05-02T21:14:09Z"})


@settings(max_examples=200, deadline=None)
@given(
    local=st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2099, 12, 31)),
    offset=st.integers(-840, 840),
    millis=st.integers(0, 999),
    shift=st.integers(-1440, 1440),
)
@example(local=datetime(2031, 1, 1, 0, 0, 0), offset=-840, millis=0, shift=0)
@example(local=datetime(2031, 12, 31, 23, 59, 59), offset=840, millis=999, shift=1)
def test_pbt_ingest_1_capture_time_utc_is_the_local_time_minus_the_offset(local: datetime, offset: int, millis: int, shift: int) -> None:
    local = local.replace(microsecond=millis * 1000)
    utc = (local - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)
    hint = {
        "localDateTime": local.strftime("%Y-%m-%dT%H:%M:%S") + (f".{millis:03d}" if millis else ""),
        "utcOffsetMinutes": offset,
        "utc": ingest.format_utc(utc),
        "source": {"container": "exif", "tags": ["DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"]},
    }
    assert ingest.CaptureTimeHint.model_validate(hint).utc == hint["utc"]
    if shift != 0:
        wrong = {**hint, "utc": ingest.format_utc(utc + timedelta(minutes=shift))}
        with pytest.raises(ValidationError):
            ingest.CaptureTimeHint.model_validate(wrong)


_STATES = list(ingest.JOB_STATES)


@settings(max_examples=300, deadline=None)
@given(
    state=st.sampled_from(_STATES),
    started=st.booleans(),
    finished=st.booleans(),
    solution=st.booleans(),
    error=st.booleans(),
    order=st.sampled_from(["ordered", "started-before-created", "finished-before-started"]),
)
@example(state="succeeded", started=True, finished=True, solution=True, error=False, order="ordered")
@example(state="queued", started=False, finished=False, solution=False, error=False, order="ordered")
@example(state="failed", started=False, finished=True, solution=False, error=True, order="ordered")
@example(state="running", started=True, finished=False, solution=False, error=False, order="started-before-created")
def test_pbt_ingest_2_job_records_obey_their_state_rules(
    state: str, started: bool, finished: bool, solution: bool, error: bool, order: str
) -> None:
    created = datetime(2031, 5, 2, 21, 0, tzinfo=timezone.utc)
    start = created + timedelta(seconds=-5 if order == "started-before-created" else 2)
    end = start + timedelta(seconds=-1 if order == "finished-before-started" else 40)
    job = {"id": "job_0123456789abcdef", "sourceAssetSha256": "a" * 64, "state": state,
           "createdAtUTC": ingest.format_utc(created), "stages": []}
    if started:
        job["startedAtUTC"] = ingest.format_utc(start)
    if finished:
        job["finishedAtUTC"] = ingest.format_utc(end)
    if solution:
        job["solutionId"] = "sol-primary"
    if error:
        job["error"] = "The decoder could not read the file."
    rules = {
        "queued": not started and not finished and not solution and not error,
        "running": started and not finished and not solution and not error,
        "succeeded": started and finished and solution and not error,
        "failed": finished and error and not solution,
        "canceled": finished and not solution and not error,
    }
    in_order = (not started or order != "started-before-created") and (
        not (started and finished) or order != "finished-before-started"
    )
    allowed = rules[state] and in_order
    if allowed:
        assert ingest.SolveJob.model_validate(job).state == state
    else:
        with pytest.raises(ValidationError):
            ingest.SolveJob.model_validate(job)
