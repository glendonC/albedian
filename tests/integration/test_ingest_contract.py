"""Ingest contracts (roadmap Track 1 M1): Pydantic rules, properties, and the fixture shared with Zod.

`tests/fixtures/ingest_contract.json` is written from `build_ingest_samples()` and parsed by
packages/contracts/src/__tests__/ingest.test.ts, so a rule that changes on one side only fails a
test. Regenerate it after a deliberate change with:

    ALBEDIAN_UPDATE_INGEST_FIXTURE=1 uv run pytest tests/integration/test_ingest_contract.py
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import io

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from PIL import Image, ImageOps
from pydantic import ValidationError

from services.contracts import ingest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "ingest_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_INGEST_FIXTURE"
MODELS = {
    "SourceAsset": ingest.SourceAsset,
    "IngestReceipt": ingest.IngestReceipt,
    "CaptureTimeHint": ingest.CaptureTimeHint,
    "OrientationHint": ingest.OrientationHint,
    "SolveJob": ingest.SolveJob,
}
FORMS = [{"quarterTurnsClockwise": q, "mirrored": m} for m in (False, True) for q in range(4)]


def show(form: dict, pixels: np.ndarray) -> np.ndarray:
    """The canonical form applied to stored pixels: mirror left to right if asked, then turn clockwise."""
    mirrored = np.fliplr(pixels) if form["mirrored"] else pixels
    return np.rot90(mirrored, k=-form["quarterTurnsClockwise"])


@pytest.mark.parametrize("value", range(1, 9))
def test_exif_orientations_show_pixels_as_pillows_exif_transpose_does(value: int) -> None:
    stored = np.arange(6, dtype=np.uint8).reshape(2, 3) * 40
    image = Image.fromarray(stored, "L")
    exif = image.getexif()
    exif[0x0112] = value
    out = io.BytesIO()
    image.save(out, "PNG", exif=exif)
    shown = np.asarray(ImageOps.exif_transpose(Image.open(io.BytesIO(out.getvalue()))))
    assert np.array_equal(show(ingest.EXIF_ORIENTATIONS[value], stored), shown)


def test_the_eight_exif_values_are_the_eight_forms() -> None:
    forms = [ingest.EXIF_ORIENTATIONS[value] for value in range(1, 9)]
    assert sorted(map(str, forms)) == sorted(map(str, FORMS))


@settings(max_examples=200, deadline=None)
@given(
    first=st.sampled_from(FORMS),
    then=st.sampled_from(FORMS),
    pixels=st.integers(2, 4).flatmap(lambda h: st.integers(2, 5).flatmap(
        lambda w: st.lists(st.integers(0, 255), min_size=h * w, max_size=h * w).map(lambda v: np.array(v, dtype=np.uint8).reshape(h, w)))),
)
@example(first=FORMS[1], then=FORMS[4], pixels=np.arange(6, dtype=np.uint8).reshape(2, 3))
@example(first=FORMS[7], then=FORMS[7], pixels=np.arange(12, dtype=np.uint8).reshape(3, 4))
def test_pbt_ingest_4_orientations_compose_and_invert_as_the_pixels_they_move(first: dict, then: dict, pixels: np.ndarray) -> None:
    composed = ingest.compose_display_orientations(first, then)
    assert np.array_equal(show(composed, pixels), show(then, show(first, pixels)))
    undo = ingest.invert_display_orientation(first)
    assert np.array_equal(show(undo, show(first, pixels)), pixels)
    assert np.array_equal(show(first, show(undo, pixels)), pixels)
    assert ingest.compose_display_orientations(first, undo) == {"quarterTurnsClockwise": 0, "mirrored": False}


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


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("has_numerator", [False, True])
@pytest.mark.parametrize("has_denominator", [False, True])
def test_a_frame_rate_is_a_clips_both_fields_or_neither_and_a_stored_clip_carries_its_probed_rate(
    kind: str, has_numerator: bool, has_denominator: bool
) -> None:
    # A photo receipt with every hint and a clip receipt with none
    receipts = ingest.build_ingest_samples()["valid"]["IngestReceipt"]
    receipt = copy.deepcopy(receipts[0] if kind == "image" else receipts[2])
    asset = receipt["sourceAsset"]
    assert asset["mediaKind"] == kind
    asset.pop("fpsNumerator", None)
    asset.pop("fpsDenominator", None)
    if has_numerator:
        asset["fpsNumerator"] = 30000
    if has_denominator:
        asset["fpsDenominator"] = 1001
    asset_ok = has_numerator == has_denominator and (kind == "video" or not has_numerator)
    # The backend probes every clip it keeps, so a receipt's clip carries its rate
    receipt_ok = asset_ok and (kind == "image" or has_numerator)
    for model, sample, ok in ((ingest.SourceAsset, asset, asset_ok), (ingest.IngestReceipt, receipt, receipt_ok)):
        if ok:
            model.model_validate(sample)
        else:
            with pytest.raises(ValidationError):
                model.model_validate(sample)


def test_missing_metadata_stays_missing() -> None:
    receipt = ingest.build_ingest_samples()["valid"]["IngestReceipt"][0]
    bare = {**receipt, "hints": {}, "sourceAsset": {k: v for k, v in receipt["sourceAsset"].items()
                                                    if k not in ("capturedAtUTC", "observerLocation", "cameraMetadata", "metadataSources")}}
    parsed = ingest.IngestReceipt.model_validate(bare)
    assert parsed.hints.capture_time is None and parsed.source_asset.captured_at_utc is None
    # A zoneless local time is recorded as such; it never becomes a guessed UTC
    zoneless = {"localDateTime": "2031-05-02T05:42:18", "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
                "source": {"container": "exif", "tags": ["DateTimeOriginal"]}}
    assert ingest.CaptureTimeHint.model_validate(zoneless).utc is None
    with pytest.raises(ValidationError):
        ingest.CaptureTimeHint.model_validate({**zoneless, "utc": "2031-05-02T05:42:18Z"})
    # A time held only in UTC has no local time, and a file's own time stays a hint
    file_time = {"localDateTime": None, "utcOffsetMinutes": None, "utc": "2031-05-02T19:31:40Z", "marks": "file_written",
                 "source": {"container": "quicktime", "tags": ["creation_time"]}}
    assert ingest.CaptureTimeHint.model_validate(file_time).local_date_time is None
    clip = ingest.build_ingest_samples()["valid"]["SourceAsset"][1]
    receipt = {"sourceAsset": clip, "hints": {"captureTime": file_time}, "alreadyStored": False}
    assert ingest.IngestReceipt.model_validate(receipt).source_asset.captured_at_utc is None
    with pytest.raises(ValidationError):
        ingest.IngestReceipt.model_validate({**receipt, "sourceAsset": {
            **clip, "capturedAtUTC": file_time["utc"], "metadataSources": {"capturedAtUTC": file_time["source"]}}})


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
        "marks": "recorded",
        "source": {"container": "exif", "tags": ["DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"]},
    }
    assert ingest.CaptureTimeHint.model_validate(hint).utc == hint["utc"]
    if shift != 0:
        wrong = {**hint, "utc": ingest.format_utc(utc + timedelta(minutes=shift))}
        with pytest.raises(ValidationError):
            ingest.CaptureTimeHint.model_validate(wrong)


_SOURCES = {
    "recorded": {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]},
    "file_written": {"container": "quicktime", "tags": ["creation_time"]},
}


@settings(max_examples=300, deadline=None)
@given(
    local=st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2099, 12, 31)),
    offset=st.integers(-840, 840),
    has_local=st.booleans(),
    has_offset=st.booleans(),
    has_utc=st.booleans(),
    marks=st.sampled_from(ingest.CAPTURE_TIME_MARKS),
    asset_takes_it=st.booleans(),
)
@example(local=datetime(2031, 5, 2, 21, 31, 40), offset=0, has_local=False, has_offset=False, has_utc=True,
         marks="file_written", asset_takes_it=False)
@example(local=datetime(2031, 5, 2, 21, 31, 40), offset=0, has_local=False, has_offset=False, has_utc=True,
         marks="file_written", asset_takes_it=True)
@example(local=datetime(2031, 5, 2, 21, 31, 40), offset=120, has_local=True, has_offset=True, has_utc=True,
         marks="recorded", asset_takes_it=True)
@example(local=datetime(2031, 5, 2, 21, 31, 40), offset=0, has_local=False, has_offset=False, has_utc=False,
         marks="recorded", asset_takes_it=False)
def test_pbt_ingest_3_a_capture_time_is_local_utc_or_both_and_only_a_recorded_one_reaches_the_asset(
    local: datetime, offset: int, has_local: bool, has_offset: bool, has_utc: bool, marks: str, asset_takes_it: bool
) -> None:
    local = local.replace(microsecond=0)
    utc = ingest.format_utc((local - timedelta(minutes=offset)).replace(tzinfo=timezone.utc))
    hint = {
        "localDateTime": local.strftime("%Y-%m-%dT%H:%M:%S") if has_local else None,
        "utcOffsetMinutes": offset if has_offset else None,
        "utc": utc if has_utc else None,
        "marks": marks,
        "source": _SOURCES[marks],
    }
    if not ((has_local or has_utc) and has_offset == (has_local and has_utc)):
        with pytest.raises(ValidationError):
            ingest.CaptureTimeHint.model_validate(hint)
        return
    assert ingest.CaptureTimeHint.model_validate(hint).marks == marks
    clip = ingest.build_ingest_samples()["valid"]["SourceAsset"][1]
    takes = asset_takes_it and has_utc
    asset = {**clip, "capturedAtUTC": utc, "metadataSources": {"capturedAtUTC": hint["source"]}} if takes else clip
    receipt = {"sourceAsset": asset, "hints": {"captureTime": hint}, "alreadyStored": False}
    if takes == (marks == "recorded" and has_utc):
        assert ingest.IngestReceipt.model_validate(receipt).source_asset.captured_at_utc == (utc if takes else None)
    else:
        with pytest.raises(ValidationError):
            ingest.IngestReceipt.model_validate(receipt)


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
