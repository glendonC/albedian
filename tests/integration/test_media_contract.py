"""Restoration and export contracts (spec restoration-for-every-upload, REQ-RST-10; P-RST-20): Pydantic rules,
properties, and the fixture shared with Zod.

`tests/fixtures/media_contract.json` is written from `build_media_samples()` and parsed by
packages/contracts/src/__tests__/media.test.ts, so a rule that changes on one side only fails a test. Each
invalid sample names the refinement message it must be refused with, and both sides check that message. The raw
samples are JSON text with a number only text can carry (1e400, an integer of 401 digits, 1e0, -0.0), read by each
side's own JSON reader. Regenerate the fixture after a deliberate change with:

    ALBEDIAN_UPDATE_MEDIA_FIXTURE=1 uv run pytest tests/integration/test_media_contract.py

The properties below are P-RST-20 in Python; the same generators and the same oracle run in fast-check in the
TypeScript file. Each oracle restates the rule in its own words rather than calling the code under test; the closed
lists are expanded here, slot by slot, without the template walker the contract uses.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import ingest, media, wire

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "media_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_MEDIA_FIXTURE"
MODELS = media.MODELS
SAMPLES = media.build_media_samples()
VALID = SAMPLES["valid"]


def accepts(model: Any, record: Any) -> bool:
    try:
        model.model_validate(record)
    except ValidationError:
        return False
    return True


def errors(model: Any, record: Any) -> list[dict[str, Any]]:
    try:
        model.model_validate(record)
    except ValidationError as error:
        return error.errors()
    return []


def messages(model: Any, record: Any) -> list[str]:
    return [item["msg"] for item in errors(model, record)]


def expand(template: str) -> list[str]:
    """Every text a template stands for, slot by slot: the oracle the template walker is checked against."""
    parts = template.replace("}", "{").split("{")
    choices = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            choices.append([part])
            continue
        slot = media.TEMPLATE_SLOTS[part]
        choices.append(slot["values"] if slot["type"] == "text" else [str(n) for n in range(slot["min"], slot["max"] + 1)])
    return ["".join(pieces) for pieces in itertools.product(*choices)]


FAILURES = sorted({text for template in media.FAILURE_SENTENCES for text in expand(template)})
DEFAULT_BASES = sorted({text for template in media.BASES["default"] for text in expand(template)})


# =============================================================================================
# The fixture both sides judge
# =============================================================================================


def test_media_fixture_is_current() -> None:
    text = json.dumps(SAMPLES, indent=2, ensure_ascii=True) + "\n"
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_every_valid_sample_round_trips_unchanged() -> None:
    assert set(VALID) == set(MODELS)
    for name, records in VALID.items():
        assert records, name
        for record in records:
            parsed = MODELS[name].model_validate(record)
            assert parsed.model_dump(by_alias=True, exclude_unset=True) == record, name


def test_every_invalid_sample_is_refused_for_its_reason() -> None:
    invalid = SAMPLES["invalid"]
    assert len(invalid) >= 200
    for case in invalid:
        found = messages(MODELS[case["schema"]], case["sample"])
        assert found, f"{case['schema']} should refuse: {case['reason']}"
        if case["message"] is not None:
            assert any(case["message"] in text for text in found), f"{case['reason']}: {found}"


def test_every_refinement_has_a_sample_refused_with_its_message() -> None:
    used = {case["message"] for case in SAMPLES["invalid"] if case["message"] is not None}
    assert set(SAMPLES["messages"]) == set(media.REFINEMENT_MESSAGES)
    assert set(media.REFINEMENT_MESSAGES) <= used, set(media.REFINEMENT_MESSAGES) - used


def test_the_fixture_carries_the_constants_and_lists_both_sides_share() -> None:
    assert SAMPLES["patterns"] == media.MEDIA_PATTERNS
    assert SAMPLES["idPatterns"] == media.MEDIA_ID_PATTERNS
    assert SAMPLES["jobIdPattern"] == ingest.JOB_ID_PATTERN
    assert SAMPLES["disclosures"] == media.DISCLOSURES
    assert SAMPLES["vocabulary"] == json.loads(json.dumps(media.MEDIA_VOCABULARY))
    assert SAMPLES["enums"]["graphStepKinds"] == list(media.GRAPH_STEP_KINDS)
    # One UTC form and one hash form for every record of the app: the wire module's, which ingest takes too
    assert media.MEDIA_PATTERNS["utcInstant"] == ingest.UTC_INSTANT_PATTERN == wire.UTC_INSTANT_PATTERN
    assert media.MEDIA_PATTERNS["sha256"] == ingest.SHA256_HEX_PATTERN == wire.SHA256_HEX_PATTERN
    # The parameter table has one row per name, each for steps of the pipeline
    names = [row["name"] for row in media.STEP_PARAMETERS]
    assert len(names) == len(set(names)) and all(set(row["steps"]) <= set(media.GRAPH_STEP_KINDS) for row in media.STEP_PARAMETERS)


def test_the_removed_shapes_are_gone() -> None:
    # REQ-RST-10.3: nothing branches, caches or scores, so the contract describes none of it; the job is the runner's
    for name in ("ProcessingNode", "FidelityPolicy", "AttemptEvaluationMetrics", "validate_processing_graph_dag",
                 "compute_node_cache_keys", "RestoreJob", "RestoreStageProgress", "RESTORE_JOB_STAGES", "PRIVATE_TEXT_PATTERN"):
        assert not hasattr(media, name), name
    removed = {
        "ProcessingAttempt": ["parentAttemptId", "label", "cacheKey", "gpuMs", "outputVariantId", "metrics"],
        "MediaVariant": ["label", "summaryNote", "frameTimestampsMs", "audioStreamCopiedFromSource", "audioSyncOffsetMs"],
        "ProcessingGraph": ["parentGraphId", "profileName", "fidelityPolicy"],
        "ExportArtifact": ["sessionId", "outputUri", "containerFormat", "videoCodec", "audioPreserved", "audioCodec",
                           "audioSyncOffsetMs", "processingGraphId", "catalogCitations", "epistemicSummary"],
    }
    for name, fields in removed.items():
        base = VALID[name][0]
        for field in fields:
            assert not accepts(MODELS[name], {**base, field: True}), f"{name}.{field}"


# =============================================================================================
# The wire: keys, numbers, lengths, surrogates
# =============================================================================================


def _key_paths(node: Any, path: tuple = ()) -> list[tuple]:
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((*path, key))
            found.extend(_key_paths(value, (*path, key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_key_paths(value, (*path, index)))
    return found


def _renamed(record: Any, path: tuple, name: str) -> Any:
    out = copy.deepcopy(record)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[name] = target.pop(path[-1])
    return out


def _all_python_names(node: Any) -> Any:
    if isinstance(node, dict):
        return {SAMPLES["pythonNames"].get(key, key): _all_python_names(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_all_python_names(value) for value in node]
    return node


def test_a_python_field_name_on_the_wire_is_refused_as_an_unknown_key_is() -> None:
    names = SAMPLES["pythonNames"]
    assert names["sourceSha256"] == "source_sha256" and names["startedAtUTC"] == "started_at_utc"
    checked = 0
    for schema, records in VALID.items():
        for record in records:
            for path in _key_paths(record):
                if path[-1] not in names:
                    continue
                found = errors(MODELS[schema], _renamed(record, path, names[path[-1]]))
                checked += 1
                assert found, f"{schema}: {path} under its Python name was accepted"
                assert "extra_forbidden" in {item["type"] for item in found}, (schema, path)
    assert checked > 700
    # Python code may still build a record by its field names, when it says so
    attempt = _all_python_names(VALID["ProcessingAttempt"][0])
    assert not accepts(media.ProcessingAttempt, attempt)
    media.ProcessingAttempt.model_validate(attempt, by_name=True)


def _object_paths(node: Any, path: tuple = ()) -> list[tuple]:
    if isinstance(node, dict):
        return [path, *(found for key, value in node.items() for found in _object_paths(value, (*path, key)))]
    if isinstance(node, list):
        return [found for index, value in enumerate(node) for found in _object_paths(value, (*path, index))]
    return []


def _with_key(record: Any, path: tuple, key: str, value: Any) -> Any:
    out = copy.deepcopy(record)
    target = out
    for step in path:
        target = target[step]
    target[key] = value
    return out


def test_an_unknown_key_or_a_python_name_beside_its_wire_key_is_refused_at_every_level() -> None:
    # Renaming a key leaves a required one missing; adding one is what a loose shape takes and drops (verdict
    # restore-1-v2, item 1). Every object of every valid sample, the embedded audio streams and orientation included
    names, added = SAMPLES["pythonNames"], SAMPLES["addedKeys"]
    checked = 0
    for schema, records in VALID.items():
        for record in records:
            for path in _object_paths(record):
                target = _get(record, path)
                extra = [(key, added["value"]) for key in added["keys"]]
                extra += [(names[key], value) for key, value in target.items() if key in names]
                for key, value in extra:
                    found = errors(MODELS[schema], _with_key(record, path, key, value))
                    checked += 1
                    assert "extra_forbidden" in {item["type"] for item in found}, f"{schema}: {key!r} at {path}"
    assert checked == added["records"] > 2000


def _criteria_script() -> Any:
    loaded = importlib.util.spec_from_file_location("restoration_criteria", ROOT / "scripts" / "restoration_criteria.py")
    script = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(script)
    return script


def test_the_criteria_reader_follows_the_specs_numbering() -> None:
    script = _criteria_script()
    spec = ("## Requirements\n### REQ-RST-1: One\n- **Acceptance Criteria:**\n  1. first\n     1. a sub-item, not a criterion\n"
            "  2. second\n### REQ-RST-2: Two\n  1. first\n## After\n  3. outside the section\n")
    assert script.criteria(spec) == ["1.1", "1.2", "2.1"]
    broken = [spec.replace("  2. second", "  3. second"), spec.replace("REQ-RST-2:", "REQ-RST-3:"),
              spec.replace("### REQ-RST-2: Two\n  1. first\n", "### REQ-RST-2: Two\n"),
              spec.replace("  1. first\n     1. a sub", "     1. a sub"), "## Requirements\n## After\n", "# No section\n"]
    for text in broken:
        with pytest.raises(ValueError):
            script.criteria(text)


def test_the_criteria_script_refuses_a_file_without_one_pair_of_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _criteria_script()
    begin, end = "# begin\n", "# end\n"
    target = tmp_path / "list.py"
    monkeypatch.setattr(script, "TARGETS", {target: (begin, end)})
    for text in ("no markers\n", begin, end + begin, begin + end + begin + end):
        target.write_text(text)
        with pytest.raises(ValueError, match="lacks one pair of generated markers"):
            script.rewritten(target, ["1.1"])
    target.write_text("head\n" + begin + "old\n" + end + "tail\n")
    assert script.rewritten(target, ["1.1", "2.1"]) == 'head\n# begin\nRESTORATION_CRITERIA = (\n    "1.1",\n    "2.1",\n)\n# end\ntail\n'


def test_the_criteria_a_basis_may_cite_are_the_specs_own_in_both_languages() -> None:
    script = _criteria_script()
    assert list(media.RESTORATION_CRITERIA) == script.criteria(script.SPEC.read_text())
    assert script.differences() == [], "run: uv run python scripts/restoration_criteria.py"
    assert media.TEMPLATE_SLOTS["criterion"]["values"] == list(media.RESTORATION_CRITERIA)


def test_every_number_form_only_json_text_can_carry_reads_the_same_on_both_sides() -> None:
    assert len(SAMPLES["raw"]) >= 40
    for case in SAMPLES["raw"]:
        assert accepts(MODELS[case["schema"]], json.loads(case["json"])) == case["accepted"], case["reason"]
        if case["message"] is not None:
            found = messages(MODELS[case["schema"]], json.loads(case["json"]))
            assert any(case["message"] in text for text in found), f"{case['reason']}: {found}"
    assert sum(case["message"] is not None for case in SAMPLES["raw"]) >= 18


def test_null_where_a_field_may_only_be_absent_is_refused_with_its_message() -> None:
    found = messages(media.RestoreRequest, {"kind": "restoration", "exposureEv": None})
    assert any("exposureEv may be absent but not null" in text for text in found), found


def test_an_infinite_number_is_refused_in_every_number_field() -> None:
    gate = VALID["IntegrityGateResult"][0]
    for key in ("variantThresholdSnr", "matchRadiusPx", "tilePx", "variantPointCount"):
        assert not accepts(media.IntegrityGateResult, {**gate, key: float("inf")}), key
    assert not accepts(media.RestoreRequest, {"kind": "restoration", "exposureEv": float("nan")})


# Every number field that may hold zero, where minus zero must be read and written back as zero, as JavaScript writes it
_ZERO_FIELDS = [
    ("RestoreRequest", 2, ["exposureEv"]), ("IntegrityGateResult", 0, ["worstFluxChangeSigma"]),
    ("IntegrityGateResult", 0, ["clippedPointsLeftOut"]), ("NoiseTable", 1, ["sigma", "b", 0]), ("NoiseTable", 0, ["sigma", "r", 0]),
    ("NoiseTable", 0, ["intensities", 0]), ("RestorationMeasurements", 0, ["fineNoiseLeft"]),
    ("RestorationMeasurements", 2, ["clip", "exactRepeatShare"]), ("RestorationMeasurements", 2, ["clip", "medianClosureErrorPx"]),
    ("ProcessingAttempt", 0, ["elapsedMs"]), ("ProcessingAttempt", 0, ["cpuMs"]), ("MediaVariant", 2, ["durationMs"]),
    ("MediaVariant", 2, ["audioStreams", 0, "startPtsMs"]), ("MediaVariant", 2, ["audioStreams", 0, "durationMs"]),
]


@pytest.mark.parametrize("schema,index,path", _ZERO_FIELDS)
def test_minus_zero_is_read_and_written_back_as_zero(schema: str, index: int, path: list) -> None:
    record = _put(VALID[schema][index], tuple(path), -0.0)
    written = json.loads(json.dumps(MODELS[schema].model_validate(record).model_dump(by_alias=True, exclude_unset=True)))
    for key in path:
        written = written[key]
    assert written == 0 and math.copysign(1.0, written) == 1.0, (schema, path)


def test_the_embedded_audio_shape_drops_an_unknown_key_as_zod_does() -> None:
    stream = {"index": 1, "codec": "aac", "sampleRateHz": 48000, "channels": 2, "startPtsMs": 0, "durationMs": 1, "sha256": "a" * 8}
    parsed = ingest.AudioStreamDescriptor.model_validate({**stream, "bitRate": 96000})
    assert parsed.model_dump(by_alias=True) == stream


def test_the_embedded_audio_shape_counts_its_hash_length_in_utf16_units() -> None:
    stream = {"index": 1, "codec": "aac", "sampleRateHz": 48000, "channels": 2, "startPtsMs": 0, "durationMs": 1}
    astral = "\U0001f600"
    assert accepts(ingest.AudioStreamDescriptor, {**stream, "sha256": astral * 4})
    assert not accepts(ingest.AudioStreamDescriptor, {**stream, "sha256": astral * 3 + "a"})
    assert not accepts(ingest.AudioStreamDescriptor, {**stream, "sha256": "a" * 7})
    assert accepts(ingest.AudioStreamDescriptor, {**stream, "sha256": "a" * 8})


def _string_paths(node: Any, path: tuple = ()) -> list[tuple]:
    if isinstance(node, str):
        return [path]
    if isinstance(node, dict):
        return [p for key, value in node.items() for p in _string_paths(value, (*path, key))]
    if isinstance(node, list):
        return [p for index, value in enumerate(node) for p in _string_paths(value, (*path, index))]
    return []


def _put(record: Any, path: tuple, value: Any) -> Any:
    out = copy.deepcopy(record)
    target = out
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return out


def test_a_lone_surrogate_is_refused_in_every_string_of_every_record() -> None:
    checked = 0
    for schema, records in VALID.items():
        for record in records:
            for path in _string_paths(record):
                value = _get(record, path)
                for placed in (value + "\ud800", "\udc00" + value):
                    checked += 1
                    assert not accepts(MODELS[schema], _put(record, path, placed)), (schema, path)
    assert checked > 1000


def _get(record: Any, path: tuple) -> Any:
    for key in path:
        record = record[key]
    return record


# =============================================================================================
# No free text: closed lists, templates, exact ids
# =============================================================================================

# Where a text goes, as (schema, a valid record, the path of the text in it)
_SLOTS = {
    "measured basis": ("GraphParameter", lambda: VALID["GraphParameter"][0], ("basis",)),
    "default basis": ("GraphParameter", lambda: VALID["GraphParameter"][6], ("basis",)),
    "requested basis": ("GraphParameter", lambda: VALID["GraphParameter"][2], ("basis",)),
    "text value": ("GraphParameter", lambda: VALID["GraphParameter"][0], ("value",)),
    "note": ("RestorationMeasurements", lambda: VALID["RestorationMeasurements"][0], ("notes", 0)),
    "failure reason": ("ProcessingAttempt", lambda: VALID["ProcessingAttempt"][9], ("failureReason",)),
    "assumption reason": ("ColourDescription", lambda: VALID["ColourDescription"][0], ("assumptions", 0, "reason")),
    "parameter name": ("GraphParameter", lambda: VALID["GraphParameter"][0], ("name",)),
    "audio codec": ("MediaVariant", lambda: VALID["MediaVariant"][2], ("audioStreams", 0, "codec")),
    "audio hash": ("MediaVariant", lambda: VALID["MediaVariant"][2], ("audioStreams", 0, "sha256")),
    "asset id": ("MediaVariant", lambda: VALID["MediaVariant"][0], ("sourceAssetId",)),
    "solution id": ("ExportManifest", lambda: VALID["ExportManifest"][0], ("observationSolutionId",)),
}
# What each slot accepts, written out in full
_ALLOWED = {
    "measured basis": set(media.BASES["measured"]),
    "default basis": set(DEFAULT_BASES),
    "requested basis": set(media.BASES["requested"]),
    "text value": set(media.COLOUR_MATRICES),
    "note": set(media.NOTES),
    "failure reason": set(FAILURES),
    "assumption reason": {next(r["reason"] for r in media.ASSUMPTION_REASONS if r["field"] == "chromaSiting")},
    "parameter name": {"matrix"},
}


def test_every_string_the_audit_passed_is_refused_in_every_slot() -> None:
    probes = SAMPLES["privacyProbes"]
    assert len(probes) >= 70
    for slot, (schema, base, path) in _SLOTS.items():
        for probe in probes:
            assert not accepts(MODELS[schema], _put(base(), path, probe)), f"{slot}: {probe!r}"


def test_every_entry_of_every_list_is_accepted_in_its_slot() -> None:
    for slot, allowed in _ALLOWED.items():
        schema, base, path = _SLOTS[slot]
        for text in allowed:
            assert accepts(MODELS[schema], _put(base(), path, text)), f"{slot}: {text!r}"
    assert len(FAILURES) == 8 + len(media.FAILURE_FORMATS) + len(media.FAILURE_CAPABILITIES)
    assert len(DEFAULT_BASES) == 3 * 18 + len(media.RESTORATION_CRITERIA) + 1


def _near_misses(text: str) -> list[str]:
    return [text + " ", " " + text, text[:-1], text.upper(), text.replace(" ", "  ", 1), text + text, text.replace("1", "01", 1),
            text.replace("e", "е", 1)]


@settings(max_examples=300, deadline=None)
@given(slot=st.sampled_from(sorted(_ALLOWED)), text=st.one_of(st.text(max_size=120), st.sampled_from(SAMPLES["privacyProbes"])),
       miss=st.integers(0, 7), seed_text=st.integers(0, 10**6))
@example(slot="default basis", text="Proposed in design section 19.", miss=0, seed_text=0)
@example(slot="failure reason", text="This kind of file is not supported yet: a sock.", miss=0, seed_text=0)
def test_pbt_any_text_outside_a_slots_list_is_refused_there(slot: str, text: str, miss: int, seed_text: int) -> None:
    allowed = sorted(_ALLOWED[slot])
    schema, base, path = _SLOTS[slot]
    for candidate in (text, _near_misses(allowed[seed_text % len(allowed)])[miss]):
        assert accepts(MODELS[schema], _put(base(), path, candidate)) == (candidate in _ALLOWED[slot]), (slot, candidate)


@settings(max_examples=300, deadline=None)
@given(row=st.sampled_from(media.STEP_PARAMETERS), offset=st.sampled_from(["min", "max", "below", "above", "fraction", "text"]))
@example(row=next(r for r in media.STEP_PARAMETERS if r["name"] == "tilePx"), offset="above")
def test_pbt_a_parameter_value_is_accepted_exactly_inside_its_row(row: dict[str, Any], offset: str) -> None:
    step = next(kind for kind in media.GRAPH_STEP_KINDS if kind in row["steps"])
    if row["type"] == "text":
        value = {"min": row["values"][0], "max": row["values"][-1], "below": row["values"][0].upper(), "above": row["values"][0] + "x",
                 "fraction": 1.5, "text": ""}[offset]
        expected = value in row["values"]
    else:
        value = {"min": row["min"], "max": row["max"], "below": row["min"] - 1, "above": row["max"] + 1,
                 "fraction": row["min"] + 0.5, "text": str(row["min"])}[offset]
        expected = isinstance(value, (int, float)) and row["min"] <= value <= row["max"] and (
            row["type"] == "number" or float(value).is_integer())
    parameter = {"name": row["name"], "value": value, "origin": "default", "basis": "Proposed in design section 10."}
    graph_step = {"kind": step, "version": "1.0.0", "parameters": [parameter]}
    assert accepts(media.GraphParameter, parameter) == expected
    assert accepts(media.GraphStep, graph_step) == expected


# =============================================================================================
# P-RST-20: both languages accept and refuse the same generated records
# =============================================================================================

_GATE = {"none": None, "passed": 0, "unconfirmed": 1, "flux": 2}
_BEFORE_THE_GATE = ("copy_differs", "frame_times_differ", "audio_differs")


@settings(max_examples=400, deadline=None)
@given(
    status=st.sampled_from(media.ATTEMPT_STATUSES),
    has_variant=st.booleans(),
    has_measured=st.booleans(),
    gate=st.sampled_from(list(_GATE)),
    rejection=st.sampled_from([None, *media.REJECTION_REASONS]),
    has_failure=st.booleans(),
    times=st.sampled_from(["ordered", "finished_before_started", "not_a_day"]),
)
@example(status="ready", has_variant=True, has_measured=True, gate="passed", rejection=None, has_failure=False, times="ordered")
@example(status="ready", has_variant=True, has_measured=True, gate="none", rejection=None, has_failure=False, times="ordered")
@example(status="rejected", has_variant=False, has_measured=True, gate="unconfirmed", rejection="unconfirmed_points",
         has_failure=False, times="ordered")
@example(status="rejected", has_variant=False, has_measured=True, gate="flux", rejection="star_flux_changed",
         has_failure=False, times="ordered")
@example(status="rejected", has_variant=False, has_measured=True, gate="none", rejection="copy_differs",
         has_failure=False, times="ordered")
@example(status="failed", has_variant=False, has_measured=False, gate="none", rejection=None, has_failure=True, times="ordered")
@example(status="failed", has_variant=False, has_measured=True, gate="passed", rejection=None, has_failure=True, times="ordered")
@example(status="failed", has_variant=False, has_measured=True, gate="unconfirmed", rejection=None, has_failure=True, times="ordered")
@example(status="rejected", has_variant=False, has_measured=True, gate="none", rejection="frame_times_differ", has_failure=True,
         times="ordered")
def test_pbt_rst_20_an_attempt_is_accepted_exactly_when_its_fields_fit_its_status(
    status: str, has_variant: bool, has_measured: bool, gate: str, rejection: str | None, has_failure: bool, times: str
) -> None:
    base = VALID["ProcessingAttempt"][0]
    assert base["status"] == "ready" and base["gate"]["passed"]
    attempt = {k: v for k, v in base.items() if k not in ("variantId", "measured", "gate", "rejection", "failureReason")}
    attempt["status"] = status
    if has_variant:
        attempt["variantId"] = base["variantId"]
    if has_measured:
        attempt["measured"] = base["measured"]
    if _GATE[gate] is not None:
        attempt["gate"] = VALID["IntegrityGateResult"][_GATE[gate]]
    if rejection is not None:
        attempt["rejection"] = rejection
    if has_failure:
        attempt["failureReason"] = "The restoration stopped at its time limit."
    if times == "finished_before_started":
        attempt["finishedAtUTC"] = "2031-05-02T19:20:00Z"
        attempt["startedAtUTC"] = "2031-05-02T19:20:01Z"
    elif times == "not_a_day":
        attempt["finishedAtUTC"] = "2031-02-30T19:20:01Z"
    fits = {
        "ready": has_variant and has_measured and rejection is None and not has_failure,
        "rejected": rejection is not None and has_measured and not has_variant and not has_failure,
        "failed": has_failure and not has_variant and rejection is None,
    }[status]
    refused_by_gate = gate in ("unconfirmed", "flux")
    ready_passed = not (status == "ready" and refused_by_gate)
    refused_is_rejected = not (status == "failed" and refused_by_gate)
    gate_named = (rejection != "unconfirmed_points" or gate == "unconfirmed") and (
        rejection != "star_flux_changed" or gate == "flux"
    )
    no_gate_before_it = rejection not in _BEFORE_THE_GATE or gate == "none"
    expected = fits and ready_passed and refused_is_rejected and gate_named and no_gate_before_it and times == "ordered"
    assert accepts(media.ProcessingAttempt, attempt) == expected


@settings(max_examples=400, deadline=None)
@given(
    variant_points=st.integers(0, 4),
    unconfirmed=st.integers(0, 4),
    frames_checked=st.integers(1, 3),
    frames_with=st.integers(0, 4),
    stars=st.integers(0, 3),
    outside=st.integers(0, 3),
    # 3.0 is the tolerance itself, which a star's change may reach and stay within
    worst=st.sampled_from([None, 1.0, 3.0, 5.0]),
    median=st.sampled_from([None, 0.99]),
    passed=st.booleans(),
)
@example(variant_points=3, unconfirmed=0, frames_checked=1, frames_with=0, stars=2, outside=0, worst=1.0, median=0.99, passed=True)
@example(variant_points=3, unconfirmed=2, frames_checked=2, frames_with=2, stars=2, outside=0, worst=1.0, median=0.99, passed=False)
@example(variant_points=3, unconfirmed=0, frames_checked=1, frames_with=0, stars=2, outside=1, worst=5.0, median=0.99, passed=False)
@example(variant_points=0, unconfirmed=0, frames_checked=1, frames_with=0, stars=0, outside=0, worst=None, median=None, passed=True)
def test_pbt_rst_20_a_gate_result_is_accepted_exactly_when_its_counts_add_up(
    variant_points: int, unconfirmed: int, frames_checked: int, frames_with: int, stars: int, outside: int,
    worst: float | None, median: float | None, passed: bool,
) -> None:
    gate = {k: v for k, v in VALID["IntegrityGateResult"][0].items() if k not in ("worstFluxChangeSigma", "medianFluxRatio")}
    tolerance = 3.0
    gate.update({"variantPointCount": variant_points, "unconfirmedPointCount": unconfirmed, "framesChecked": frames_checked,
                 "framesWithUnconfirmed": frames_with, "starsMeasured": stars, "starsOutsideFluxTolerance": outside,
                 "fluxToleranceSigma": tolerance, "passed": passed})
    if worst is not None:
        gate["worstFluxChangeSigma"] = worst
    if median is not None:
        gate["medianFluxRatio"] = median
    expected = (
        passed == (unconfirmed == 0 and outside == 0)
        and unconfirmed <= variant_points
        and frames_with <= frames_checked
        and (frames_with == 0) == (unconfirmed == 0)
        and frames_with <= unconfirmed
        and outside <= stars
        and (worst is not None) == (stars > 0)
        and (median is not None) == (stars > 0)
        and (worst is None or (outside > 0) == (worst > tolerance))
    )
    assert accepts(media.IntegrityGateResult, gate) == expected


@settings(max_examples=400, deadline=None)
@given(
    kind=st.sampled_from(media.VARIANT_KINDS),
    disclosure_of=st.sampled_from(media.VARIANT_KINDS),
    clip=st.booleans(),
    frames=st.sampled_from([1, 2]),
    duration=st.sampled_from([0, 500]),
    audio=st.booleans(),
    frame_times=st.booleans(),
    uri=st.sampled_from(["own", "another_variant", "store_path"]),
    model=st.sampled_from(media.COLOUR_MODELS),
    asset=st.sampled_from(["own", "another_source"]),
)
@example(kind="restoration", disclosure_of="restoration", clip=False, frames=1, duration=0, audio=False, frame_times=False,
         uri="own", model="rgb", asset="own")
@example(kind="restoration", disclosure_of="restoration", clip=True, frames=2, duration=500, audio=True, frame_times=True,
         uri="own", model="ycbcr", asset="own")
@example(kind="display_copy", disclosure_of="display_copy", clip=False, frames=1, duration=0, audio=False, frame_times=False,
         uri="own", model="rgb", asset="own")
@example(kind="restoration", disclosure_of="display_copy", clip=False, frames=1, duration=0, audio=False, frame_times=False,
         uri="own", model="rgb", asset="own")
def test_pbt_rst_20_a_variant_is_accepted_exactly_when_it_fits_its_kind_and_file(
    kind: str, disclosure_of: str, clip: bool, frames: int, duration: int, audio: bool, frame_times: bool, uri: str, model: str,
    asset: str,
) -> None:
    base = VALID["MediaVariant"][0]
    assert base["mimeType"] == "image/png" and base["kind"] == "restoration"
    clip_variant = VALID["MediaVariant"][2]
    assert clip_variant["mimeType"].startswith("video/")
    variant = {k: v for k, v in base.items() if k != "frameTimesSha256"}
    other = "var_" + "0" * 16
    variant.update({
        "kind": kind,
        "disclosure": media.DISCLOSURES[disclosure_of],
        "mimeType": "video/quicktime" if clip else "image/png",
        "frameCount": frames,
        "durationMs": duration,
        "audioStreams": clip_variant["audioStreams"] if audio else [],
        "uri": {"own": media.variant_uri(base["sourceSha256"], base["id"]),
                "another_variant": media.variant_uri(base["sourceSha256"], other),
                "store_path": f"/srv/albedian/store/{base['sourceSha256'][:2]}/{base['sourceSha256']}/variants/{base['id']}/variant.png"}[uri],
        "colour": VALID["ColourDescription"][3] if model == "rgb" else VALID["ColourDescription"][4],
        "sourceAssetId": f"src_{(base['sourceSha256'] if asset == 'own' else clip_variant['sourceSha256'])[:16]}",
    })
    assert variant["colour"]["model"] == model
    if frame_times:
        variant["frameTimesSha256"] = clip_variant["frameTimesSha256"]
    expected = (
        disclosure_of == kind
        and (clip or (frames == 1 and duration == 0 and not audio and not frame_times))
        and (not clip or frame_times)
        and (kind != "display_copy" or not clip)
        and uri == "own"
        and model == ("ycbcr" if clip else "rgb")
        and asset == "own"
    )
    assert accepts(media.MediaVariant, variant) == expected


def _graph_steps() -> st.SearchStrategy[list[str]]:
    any_order = st.lists(st.sampled_from(media.GRAPH_STEP_KINDS), min_size=1, max_size=12)
    in_order = st.lists(st.booleans(), min_size=len(media.GRAPH_STEP_KINDS), max_size=len(media.GRAPH_STEP_KINDS)).map(
        lambda keep: ["decode"] + [kind for kind, k in zip(media.GRAPH_STEP_KINDS[1:], keep[1:]) if k]
    )
    return st.one_of(any_order, in_order, st.just(["decode", "encode"]))


def _a_parameter_of(step: str) -> dict[str, Any]:
    """The first row of the table this step may hold, at its lowest value, as a default of design section 10."""
    row = next(r for r in media.STEP_PARAMETERS if step in r["steps"])
    value = row["values"][0] if row["type"] == "text" else row["min"]
    return {"name": row["name"], "value": value, "origin": "default", "basis": "Proposed in design section 10."}


@settings(max_examples=400, deadline=None)
@given(
    kind=st.sampled_from(media.VARIANT_KINDS),
    request_kind=st.sampled_from(media.VARIANT_KINDS),
    steps=_graph_steps(),
    fault=st.sampled_from(["none", "no_origin", "empty_basis", "named_twice", "free_basis", "another_steps"]),
)
@example(kind="display_copy", request_kind="display_copy", steps=["decode", "encode"], fault="none")
@example(kind="restoration", request_kind="restoration", steps=["decode", "lift", "encode"], fault="none")
@example(kind="restoration", request_kind="restoration", steps=list(media.GRAPH_STEP_KINDS), fault="none")
@example(kind="restoration", request_kind="restoration", steps=["decode", "encode", "lift"], fault="none")
@example(kind="restoration", request_kind="restoration", steps=["find_stars", "lift", "encode"], fault="none")
@example(kind="restoration", request_kind="restoration", steps=["decode", "lift", "lift", "encode"], fault="none")
@example(kind="restoration", request_kind="restoration", steps=["decode", "lift", "encode"], fault="no_origin")
@example(kind="restoration", request_kind="restoration", steps=["decode", "lift", "encode"], fault="another_steps")
@example(kind="restoration", request_kind="restoration", steps=["find_stars", "decode", "lift", "encode"], fault="none")
@example(kind="display_copy", request_kind="display_copy", steps=["decode", "encode", "copy_audio"], fault="none")
def test_pbt_rst_20_a_graph_is_accepted_exactly_when_its_steps_and_parameters_fit_its_kind(
    kind: str, request_kind: str, steps: list[str], fault: str
) -> None:
    graph = {
        "id": VALID["ProcessingGraph"][1]["id"],
        "sourceSha256": VALID["ProcessingGraph"][1]["sourceSha256"],
        "kind": kind,
        "pipelineVersion": "1.0.0",
        "request": {"kind": request_kind},
        "steps": [{"kind": step, "version": "1.0.0", "parameters": [_a_parameter_of(step)]} for step in steps],
    }
    first = graph["steps"][0]["parameters"]
    if fault == "no_origin":
        del first[0]["origin"]
    elif fault == "empty_basis":
        first[0]["basis"] = ""
    elif fault == "named_twice":
        first.append(dict(first[0], basis="Decided by the lead in design section 10."))
    elif fault == "free_basis":
        first[0]["basis"] = "Measured on night.HEIC."
    elif fault == "another_steps":
        first.append(_a_parameter_of("copy_audio") if steps[0] != "copy_audio" else _a_parameter_of("decode"))
    order = [media.GRAPH_STEP_KINDS.index(step) for step in steps]
    if kind == "display_copy":
        steps_fit = steps == ["decode", "encode"]
    else:
        steps_fit = (
            steps[0] == "decode"
            and all(later > earlier for earlier, later in zip(order, order[1:]))
            and "lift" in steps
            and "encode" in steps
        )
    expected = fault == "none" and 2 <= len(steps) <= 16 and kind == request_kind and steps_fit
    assert accepts(media.ProcessingGraph, graph) == expected


@settings(max_examples=400, deadline=None)
@given(
    model=st.sampled_from(media.COLOUR_MODELS),
    matrix=st.sampled_from(media.COLOUR_MATRICES),
    subsampling=st.sampled_from(media.CHROMA_SUBSAMPLINGS),
    siting=st.booleans(),
    bit_depth=st.sampled_from(media.COLOUR_BIT_DEPTHS),
    transfer=st.sampled_from(media.COLOUR_TRANSFERS),
    primaries=st.sampled_from(media.COLOUR_PRIMARIES),
    profile=st.booleans(),
    assumption=st.sampled_from(["none", "held", "other_value", "twice", "other_reason"]),
)
@example(model="rgb", matrix="identity", subsampling="444", siting=False, bit_depth=16, transfer="icc_parametric",
         primaries="icc", profile=True, assumption="none")
@example(model="ycbcr", matrix="bt2020nc", subsampling="420", siting=True, bit_depth=10, transfer="hlg",
         primaries="bt2020", profile=False, assumption="held")
@example(model="ycbcr", matrix="bt709", subsampling="420", siting=True, bit_depth=8, transfer="bt709",
         primaries="bt709", profile=False, assumption="other_value")
@example(model="rgb", matrix="identity", subsampling="444", siting=False, bit_depth=8, transfer="srgb",
         primaries="icc", profile=False, assumption="none")
def test_pbt_rst_20_a_colour_description_is_accepted_exactly_when_its_fields_agree(
    model: str, matrix: str, subsampling: str, siting: bool, bit_depth: int, transfer: str, primaries: str,
    profile: bool, assumption: str,
) -> None:
    colour = {"model": model, "bitDepth": bit_depth, "range": "limited", "matrix": matrix, "transfer": transfer,
              "primaries": primaries, "chromaSubsampling": subsampling, "assumptions": []}
    if siting:
        colour["chromaSiting"] = "left"
    if profile:
        colour["iccProfileSha256"] = "c" * 64
    held, full = (dict(r) for r in media.ASSUMPTION_REASONS[:2])
    assert (held["field"], held["assumed"], full["assumed"]) == ("range", "limited", "full")
    colour["assumptions"] = {
        "none": [], "held": [held], "other_value": [full], "twice": [held, held],
        "other_reason": [dict(held, reason=full["reason"])],
    }[assumption]
    expected = (
        (model != "rgb" or (matrix == "identity" and subsampling == "444"))
        and (model != "ycbcr" or matrix != "identity")
        and siting == (subsampling != "444")
        and bit_depth in ((8, 16) if model == "rgb" else (8, 10))
        and (profile or not (transfer in ("icc_parametric", "icc_table") or primaries == "icc"))
        and assumption in ("none", "held")
    )
    assert accepts(media.ColourDescription, colour) == expected


@settings(max_examples=200, deadline=None)
@given(
    bins=st.integers(2, 6),
    order=st.sampled_from(["increasing", "repeated", "decreasing"]),
    short=st.sampled_from(["none", "r", "g", "b", "blockCounts"]),
)
@example(bins=3, order="increasing", short="none")
def test_pbt_rst_20_a_noise_table_is_accepted_exactly_when_its_columns_line_up(bins: int, order: str, short: str) -> None:
    intensities = [(i + 1) / (bins + 1) for i in range(bins)]
    if order == "repeated":
        intensities[1] = intensities[0]
    elif order == "decreasing":
        intensities.reverse()
    columns = {name: [0.01 * (i + 1) for i in range(bins)] for name in ("r", "g", "b")}
    blocks = [10 + i for i in range(bins)]
    if short in columns:
        columns[short] = columns[short][:-1]
    elif short == "blockCounts":
        blocks = blocks[:-1]
    table = {"intensities": intensities, "sigma": columns, "blockCounts": blocks}
    expected = bins >= 3 and short == "none" and order == "increasing"
    assert accepts(media.NoiseTable, table) == expected


@settings(max_examples=300, deadline=None)
@given(
    noise=st.sampled_from(["table", "reason", "both", "neither"]),
    found=st.integers(0, 3),
    confirmed=st.integers(0, 3),
    lift=st.sampled_from(["absent", "none_at_one", "none_above_one", "target_above_one"]),
    with_neighbours=st.integers(0, 3),
    together=st.integers(0, 2),
    passes=st.integers(0, 4),
)
@example(noise="table", found=3, confirmed=3, lift="target_above_one", with_neighbours=2, together=1, passes=3)
@example(noise="table", found=3, confirmed=3, lift="target_above_one", with_neighbours=0, together=1, passes=3)
def test_pbt_rst_20_measurements_are_accepted_exactly_when_their_counts_add_up(
    noise: str, found: int, confirmed: int, lift: str, with_neighbours: int, together: int, passes: int
) -> None:
    base = VALID["RestorationMeasurements"][0]
    measured = {k: v for k, v in base.items() if k not in ("noise", "noiseNotMeasured", "lift")}
    table = VALID["NoiseTable"][0]
    if noise in ("table", "both"):
        measured["noise"] = table
    if noise in ("reason", "both"):
        measured["noiseNotMeasured"] = "too_few_flat_blocks"
    measured["stars"] = {"found": found, "confirmed": confirmed}
    if lift != "absent":
        measured["lift"] = {"gainAtBlack": 1 if lift == "none_at_one" else 2.5,
                            "decidedBy": "target" if lift == "target_above_one" else "none"}
    measured["clip"] = {"frameCount": 2, "framesWithNeighbours": with_neighbours, "exactRepeatShare": 0.5,
                        "pointsConfirmedOnFramesTogether": together, "pointsFixedToSensor": 0, "streamsNotCopied": 0}
    measured["dissolvedPerPass"] = [1] * passes
    expected = (noise != "both" and confirmed <= found and lift != "none_above_one" and with_neighbours <= 2 and passes <= 3
                and (together == 0 or with_neighbours > 0))
    assert accepts(media.RestorationMeasurements, measured) == expected


_MANIFEST_FAULTS = ("graph_source", "graph_id", "attempt_id", "disclosure", "rejected", "no_gate", "no_noise", "solution_file",
                    "solution_of_another", "still_gate_frames", "stars_beyond_found")


@settings(max_examples=300, deadline=None)
@given(faults=st.sets(st.sampled_from(_MANIFEST_FAULTS), max_size=3))
@example(faults=set())
@example(faults={"rejected"})
@example(faults={"still_gate_frames"})
def test_pbt_rst_20_a_manifest_is_accepted_exactly_when_its_parts_agree(faults: set[str]) -> None:
    manifest = copy.deepcopy(VALID["ExportManifest"][0])
    assert manifest["graph"]["kind"] == "restoration" and "noise" in manifest["attempt"]["measured"]
    if "graph_source" in faults:
        manifest["graph"]["sourceSha256"] = "d" * 64
    if "graph_id" in faults:
        manifest["graph"]["id"] = "graph_" + "1" * 16
    if "attempt_id" in faults:
        manifest["attempt"]["id"] = "att_" + "2" * 16
    if "disclosure" in faults:
        manifest["disclosure"] = media.DISCLOSURES["display_copy"]
    if "rejected" in faults:
        manifest["attempt"].update(status="rejected", rejection="unconfirmed_points", gate=VALID["IntegrityGateResult"][1])
        del manifest["attempt"]["variantId"]
    if "no_gate" in faults:
        manifest["attempt"].pop("gate", None)
    if "no_noise" in faults:
        del manifest["attempt"]["measured"]["noise"]
    if "solution_file" in faults:
        manifest["observationSolutionId"] = "solution.json"
    if "solution_of_another" in faults:
        manifest["observationSolutionId"] = "sol_" + "e" * 16
    if "still_gate_frames" in faults and "gate" in manifest["attempt"]:
        manifest["attempt"]["gate"] = {**manifest["attempt"]["gate"], "framesChecked": 50}
    if "stars_beyond_found" in faults:
        manifest["attempt"]["measured"]["stars"] = {"found": 30, "confirmed": 30}
    gate_left = "no_gate" not in faults
    effective = {f for f in faults if f != "still_gate_frames" or gate_left}
    assert accepts(media.ExportManifest, manifest) == (not effective)


@settings(max_examples=200, deadline=None)
@given(kind=st.sampled_from(media.VARIANT_KINDS), exposure=st.sampled_from([None, 0, 1.5, 6, 6.5, -0.5]))
@example(kind="display_copy", exposure=1.5)
def test_pbt_rst_20_a_request_is_accepted_exactly_when_its_option_fits_its_kind(kind: str, exposure: float | None) -> None:
    request = {"kind": kind} if exposure is None else {"kind": kind, "exposureEv": exposure}
    expected = exposure is None or (kind == "restoration" and 0 <= exposure <= 6)
    assert accepts(media.RestoreRequest, request) == expected


# A union whose order decides a value's type: GraphParameter.value (StrictBool, StrictInt, WireNumber, StrictStr). A
# whole number stays a whole number, also past what a float holds exactly; a float stays a float; a flag a flag.
# Every other union of the contracts on the wire module is decided by a tag, or by literals, or is Optional.
VALUE_READS = [
    ("0", int), ("5", int), (str(2**53), int), (str(2**53 + 1), int), ("-7", int),
    ("5.0", float), ("5.5", float), ("-0.0", float), ("true", bool), ("false", bool), ('"x"', str),
]  # fmt: skip


@pytest.mark.parametrize(("text", "kind"), VALUE_READS, ids=[text for text, _ in VALUE_READS])
def test_a_graph_parameters_value_keeps_the_type_its_json_text_has(text: str, kind: type) -> None:
    from pydantic import ConfigDict, TypeAdapter

    adapter = TypeAdapter(media.GraphParameter.model_fields["value"].annotation, config=ConfigDict(strict=True))
    read = adapter.validate_python(json.loads(text))
    assert type(read) is kind
    written = json.dumps(adapter.dump_python(read, mode="json"))
    assert written == ("0.0" if text == "-0.0" else text)  # minus zero is zero (rule 2); everything else as sent


@pytest.mark.parametrize(("name", "value"), [("bitDepth", 8), ("bitDepth", 16), ("maxPasses", 0), ("maxPasses", 3), ("tilePx", 4096)])
def test_a_whole_number_parameter_is_written_back_whole_at_its_rows_edges(name: str, value: int) -> None:
    sample = next(p for p in VALID["GraphParameter"] if p["name"] == name)
    record = media.GraphParameter.model_validate({**sample, "value": value})
    dumped = record.model_dump(by_alias=True)["value"]
    assert type(dumped) is int and dumped == value


# An integer row given a whole number written as a float (maxPasses as 3.0) holds that integer on both sides (wire
# rule 2), read and written; and what each side writes for the same JSON text, compared byte for byte through
# tests/fixtures/graph_parameter_writers.json, which this file writes and media.test.ts reads.
WRITERS_FIXTURE = ROOT / "tests" / "fixtures" / "graph_parameter_writers.json"
WRITERS_UPDATE_ENV = "ALBEDIAN_UPDATE_GRAPH_PARAMETER_WRITERS"
# (row name, the value's JSON text, taken): the integer rows at both inside edges and one step outside, as whole
# floats; a number row's and a text row's own values beside them
INTEGER_ROW_EDGES = [
    ("bitDepth", "8.0", True), ("bitDepth", "16.0", True), ("bitDepth", "7.0", False), ("bitDepth", "17.0", False),
    ("maxPasses", "0.0", True), ("maxPasses", "3.0", True), ("maxPasses", "-0.0", True), ("maxPasses", "4.0", False),
    ("tilePx", "16.0", True), ("tilePx", "4096.0", True), ("tilePx", "1e3", True), ("tilePx", "4097.0", False),
    ("maxPasses", "2.5", False),
]  # fmt: skip
OTHER_ROWS = [("exposureEv", "1.5"), ("exposureEv", "2.0"), ("confirmThresholdSnr", "4.75"), ("transfer", '"hlg"')]
# A name that is no name (a list or an object, which no row is named by) beside a whole float: refused on both sides,
# never raised (a review's nine cases)
NOT_A_NAME = ['["maxPasses"]', '{"row":"maxPasses"}', "[]"]
WHOLE_FLOATS = ["3.0", "1e3", "-0.0"]
# A record that is not an object (text, a number, a list, nothing, true), alone and as a step's parameter: refused on
# both sides, never raised (a review's case: a bare string escaped as an AttributeError with the guard taken away)
NOT_AN_OBJECT = ['"maxPasses"', "3", '["maxPasses",3.0]', "null", "true"]
# A name that is text but names no row, beside a whole float
NOT_A_ROW = '"notARow"'


def _not_a_name_text(name_text: str, value_text: str) -> str:
    return _parameter_text("maxPasses", value_text).replace('"name":"maxPasses"', f'"name":{name_text}', 1)


def _parameter_text(name: str, value_text: str) -> str:
    sample = next(p for p in VALID["GraphParameter"] if p["name"] == name)
    return json.dumps({**sample, "value": "@@value@@"}, separators=(",", ":")).replace('"@@value@@"', value_text)


def _as_a_steps_parameter(record_text: str) -> str:
    """The record as the one parameter of a step of the kind maxPasses belongs to."""
    step = {"kind": "dissolve_unconfirmed", "version": "1.0.0", "parameters": ["@@record@@"]}
    return json.dumps(step, separators=(",", ":")).replace('"@@record@@"', record_text)


def _python_writes(text: str, schema: str = "GraphParameter") -> str | None:
    """The bytes the Python side writes for a record of `schema` read from `text`, compact as JSON.stringify writes;
    None when it is refused."""
    try:
        read = getattr(media, schema).model_validate(json.loads(text))
    except ValidationError:
        return None
    return json.dumps(read.model_dump(by_alias=True, mode="json"), separators=(",", ":"), ensure_ascii=False)


def _judged(row: dict[str, Any]) -> str:
    """What the Python side does with a row: taken, refused by the contract (its ValidationError), or anything it
    raises instead, named by its class."""
    try:
        getattr(media, row["schema"]).model_validate(json.loads(row["text"]))
    except ValidationError:
        return "refused"
    except Exception as error:  # noqa: BLE001 (an escape of any class is what this names)
        return f"escaped as {type(error).__name__}"
    return "taken"


def writers_rows() -> list[dict[str, Any]]:
    """The rows without what the Python side writes for them, so collecting a test over them reads no record."""
    p = "GraphParameter"
    rows = [{"name": f"{name} {value}", "schema": p, "text": _parameter_text(name, value), "taken": taken} for name, value, taken in INTEGER_ROW_EDGES]
    rows += [{"name": f"{name} {value}", "schema": p, "text": _parameter_text(name, value), "taken": True} for name, value in OTHER_ROWS]
    rows += [{"name": f"a name of {name} beside {value}", "schema": p, "text": _not_a_name_text(name, value), "taken": False} for name in NOT_A_NAME for value in WHOLE_FLOATS]
    rows += [{"name": f"a name of {NOT_A_ROW} beside 3.0", "schema": p, "text": _not_a_name_text(NOT_A_ROW, "3.0"), "taken": False}]
    rows += [{"name": f"a record of {record} alone", "schema": p, "text": record, "taken": False} for record in NOT_AN_OBJECT]
    rows += [{"name": f"a record of {record} as a step's parameter", "schema": "GraphStep", "text": _as_a_steps_parameter(record), "taken": False} for record in NOT_AN_OBJECT]
    rows += [{"name": "a step with its own parameter", "schema": "GraphStep", "text": _as_a_steps_parameter(_parameter_text("maxPasses", "3.0")), "taken": True}]
    return rows


def writers_cases() -> dict[str, Any]:
    return {"rows": [{**row, "python": _python_writes(row["text"], row["schema"])} for row in writers_rows()]}


def test_the_writers_fixture_is_current() -> None:
    text = json.dumps(writers_cases(), indent=1, ensure_ascii=True) + "\n"
    if os.environ.get(WRITERS_UPDATE_ENV) == "1":
        WRITERS_FIXTURE.write_text(text, encoding="utf-8")
    assert WRITERS_FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {WRITERS_UPDATE_ENV}=1"


@pytest.mark.parametrize(("name", "value", "taken"), INTEGER_ROW_EDGES, ids=[f"{n} {v}" for n, v, _ in INTEGER_ROW_EDGES])
def test_a_whole_float_on_an_integer_row_is_that_integer_read_and_written(name: str, value: str, taken: bool) -> None:
    written = _python_writes(_parameter_text(name, value))
    assert (written is not None) == taken
    if taken:
        read = json.loads(written)["value"]
        assert type(read) is int and read == int(float(value)) and f'"value":{int(float(value))},' in written


@pytest.mark.parametrize("row", writers_rows(), ids=lambda row: row["name"])
def test_every_row_is_taken_or_refused_by_the_contract_and_never_raised(row: dict[str, Any]) -> None:
    """Whatever the parser raises is caught and named, so a guard taken away fails here by this assertion, with the
    row's name, and not by the error escaping."""
    assert _judged(row) == ("taken" if row["taken"] else "refused"), row["name"]


def test_a_number_row_keeps_its_float_and_a_text_row_its_text() -> None:
    assert json.loads(_python_writes(_parameter_text("exposureEv", "1.5")))["value"] == 1.5
    assert type(json.loads(_python_writes(_parameter_text("exposureEv", "2.0")))["value"]) is float
    assert json.loads(_python_writes(_parameter_text("transfer", '"hlg"')))["value"] == "hlg"
