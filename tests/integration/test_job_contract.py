"""The upload job record (platform-1; upload jobs design 3.8, P-JOB-9): `services/contracts/jobs.py` against the
fixture both sides judge.

`build_job_samples` writes `tests/fixtures/job_contract.json`; `packages/contracts/src/__tests__/jobs.test.ts` reads
it, so a record one side takes and the other refuses fails one of the two. Regenerate it after a deliberate change:

    ALBEDIAN_UPDATE_JOB_FIXTURE=1 uv run pytest tests/integration/test_job_contract.py
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Annotated, Any, Optional, get_args

from pydantic import BaseModel, Field, StringConstraints, ValidationError
import pytest

from services.contracts import ingest, jobs
from services.contracts.jobs import UploadJob, build_job_samples
from services.ingest import jobs as solve_job

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "job_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_JOB_FIXTURE"
SAMPLES = build_job_samples()


def verdict(record: Any) -> tuple[bool, str]:
    """Taken, or refused with the contract's own error: a validator that crashes on a record fails here by assertion."""
    try:
        UploadJob.model_validate(record)
        return True, ""
    except Exception as error:  # noqa: BLE001 - only a ValidationError is a refusal
        assert isinstance(error, ValidationError), f"the validator crashed: {error!r}"
        return False, str(error)


def fixture_text(samples: dict[str, Any]) -> str:
    """The fixture as written: each list one sample per line, so it stays near the size of what it holds."""
    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    parts = []
    for key, value in samples.items():
        if isinstance(value, list):
            parts.append(f"{json.dumps(key)}: [\n" + ",\n".join(" " + compact(v) for v in value) + "\n]")
        else:
            parts.append(f"{json.dumps(key)}: {compact(value)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def test_job_fixture_is_current() -> None:
    text = fixture_text(SAMPLES)
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, f"regenerate with {UPDATE_FIXTURE_ENV}=1"


def test_every_valid_record_round_trips_unchanged() -> None:
    assert len(SAMPLES["valid"]) >= 10
    for record in SAMPLES["valid"]:
        dumped = UploadJob.model_validate(record).model_dump(by_alias=True, exclude_unset=True)
        assert dumped == record


def test_every_state_and_kind_has_a_valid_record() -> None:
    assert {r["state"] for r in SAMPLES["valid"]} == set(jobs.UPLOAD_JOB_STATES)
    assert {r["kind"] for r in SAMPLES["valid"]} == set(jobs.JOB_KINDS)


@pytest.mark.parametrize("case", SAMPLES["invalid"], ids=[c["reason"] for c in SAMPLES["invalid"]])
def test_every_fault_is_refused_with_its_message(case: dict[str, Any]) -> None:
    accepted, why = verdict(case["sample"])
    assert not accepted
    assert case["message"] in why


def test_every_refinement_has_a_fault() -> None:
    assert {c["message"] for c in SAMPLES["invalid"]} == set(jobs.JOB_MESSAGES)


@pytest.mark.parametrize("case", SAMPLES["edges"], ids=[f"{c['what']}:{c['accepted']}:{i}" for i, c in enumerate(SAMPLES["edges"])])
def test_each_edge_sample_gets_its_verdict(case: dict[str, Any]) -> None:
    assert verdict(case["sample"])[0] is case["accepted"]


CONSTRAINT_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern", "enum", "const",
                   "minLength", "maxLength", "minItems", "maxItems", "ge", "le", "gt", "lt"}
# A wire number's schema keeps Field's own names (its validator runs before the type), read here as JSON Schema's
FIELD_NAMES = {"ge": "minimum", "le": "maximum", "gt": "exclusiveMinimum", "lt": "exclusiveMaximum"}
# A bound no valid record can reach, with the reason; its edges sit at the inside end a valid record can reach
UNREACHABLE_ENDS = {("UploadJob", "stages", "maxItems"): "each stage once, in its kind's order, and no kind has every stage"}


def _own_models(root: type[BaseModel]) -> dict[str, Any]:
    module = sys.modules[root.__module__]
    return {name: value for name, value in vars(module).items()
            if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__}


def _constrained_fields(root: type[BaseModel]) -> dict[tuple[str, str], dict[str, Any]]:
    """(model, wire name) of every field of the root's own module's models whose schema carries a bound, a pattern, a
    length or a closed list; a list's items are the field's name and "[]"."""
    schema = root.model_json_schema()
    own = _own_models(root)
    found = {}
    for name, model_schema in [(root.__name__, schema), *schema.get("$defs", {}).items()]:
        if name not in own:
            continue
        for key, prop in model_schema.get("properties", {}).items():
            for option in prop.get("anyOf", [prop]):
                if CONSTRAINT_KEYS & set(option):
                    found[(name, key)] = {FIELD_NAMES.get(k, k): v for k, v in option.items()}
                items = option.get("items")
                for item in (items.get("anyOf", [items]) if isinstance(items, dict) else []):
                    if CONSTRAINT_KEYS & set(item):
                        found[(name, key + "[]")] = {FIELD_NAMES.get(k, k): v for k, v in item.items()}
    return found


def _owner(path: list[Any], root: type[BaseModel] = UploadJob) -> tuple[str, str]:
    """The model and wire name an edge's path ends on, found by walking the models' own field types; a path that ends
    on a list's index names the list's items."""
    item = isinstance(path[-1], int)
    walk, last = (path[:-2], path[-2] + "[]") if item else (path[:-1], path[-1])
    model: Any = root
    for key in [k for k in walk if not isinstance(k, int)]:
        field = next(f for n, f in model.model_fields.items() if (f.alias or n) == key)
        stack, model = [field.annotation], None
        while stack:
            kind = stack.pop()
            if isinstance(kind, type) and issubclass(kind, BaseModel):
                model = kind
                break
            stack.extend(get_args(kind))
        assert model is not None, path
    return model.__name__, last


def _value_at(sample: Any, path: list[Any]) -> Any:
    for key in path:
        sample = sample[key]
    return sample


def _just_inside(bound: float, option: dict[str, Any], upward: bool) -> float:
    if option.get("type") == "integer":
        return bound + 1 if upward else bound - 1
    return math.nextafter(bound, math.inf if upward else -math.inf)


def edge_gaps(root: type[BaseModel], edges: list[dict[str, Any]], unreachable: dict = UNREACHABLE_ENDS) -> list[str]:
    """What the edge rows miss: a constrained field with no row, or a row without both verdicts, or a bound whose inside
    end is not taken or whose outside is not refused. Inclusive bounds are taken at the bound; exclusive ones are
    refused at the bound and taken one step inside it; lengths and item counts are measured."""
    gaps: list[str] = []
    by_field: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        by_field.setdefault(_owner(edge["path"], root), []).append(edge)
    for field, option in _constrained_fields(root).items():
        rows = by_field.get(field, [])
        values = {True: [_value_at(e["sample"], e["path"]) for e in rows if e["accepted"]],
                  False: [_value_at(e["sample"], e["path"]) for e in rows if not e["accepted"]]}
        if not values[True] or not values[False]:
            gaps.append(f"{field}: no row with both verdicts")
            continue
        for low, high, measure in (("minimum", "maximum", lambda v: v), ("minLength", "maxLength", len),
                                   ("minItems", "maxItems", len)):
            if low in option:
                if option[low] not in map(measure, values[True]):
                    gaps.append(f"{field}: {low} {option[low]} not taken")
                if not any(measure(v) < option[low] for v in values[False]):
                    gaps.append(f"{field}: nothing below {low} {option[low]} refused")
            if high in option:
                if not any(measure(v) > option[high] for v in values[False]):
                    gaps.append(f"{field}: nothing above {high} {option[high]} refused")
                if (*field, high) in unreachable:
                    reachable = max(len(stages) for stages in jobs.JOB_STAGE_UNITS.values())
                    if not (option[high] > reachable and max(map(measure, values[True])) == reachable):
                        gaps.append(f"{field}: {high} not out of reach, or its reachable end not taken")
                elif option[high] not in map(measure, values[True]):
                    gaps.append(f"{field}: {high} {option[high]} not taken")
        for bound_key, upward in (("exclusiveMinimum", True), ("exclusiveMaximum", False)):
            if bound_key in option:
                bound = option[bound_key]
                if bound not in values[False]:
                    gaps.append(f"{field}: {bound_key} {bound} itself not refused")
                if _just_inside(bound, option, upward) not in values[True]:
                    gaps.append(f"{field}: one step inside {bound_key} {bound} not taken")
    return gaps


def test_every_constrained_field_has_its_edges_at_both_ends() -> None:
    fields = _constrained_fields(UploadJob)
    assert ("UploadJob", "id") in fields and ("JobStage", "done") in fields and ("JobInputSpan", "endSec") in fields
    assert edge_gaps(UploadJob, SAMPLES["edges"]) == []


class _Probe(BaseModel):
    """Fields of the kinds the job record does not have yet, to show the edge check sees them."""

    names: list[Annotated[str, StringConstraints(pattern=r"^[a-z]+$")]]
    code: Annotated[str, Field(min_length=2, max_length=40)]
    ratio: Annotated[float, Field(gt=0, lt=1)]
    count: Annotated[int, Field(gt=0, lt=10)]


_PROBE = {"names": ["ab"], "code": "abc", "ratio": 0.5, "count": 5}


def _probe_rows(rows: dict[str, tuple[list[Any], list[Any]]]) -> list[dict[str, Any]]:
    edges = []
    for field, (taken, refused) in rows.items():
        path = [field, 0] if field == "names" else [field]
        for accepted, values in ((True, taken), (False, refused)):
            for value in values:
                sample = {**_PROBE, "names": [value]} if field == "names" else {**_PROBE, field: value}
                edges.append({"path": path, "sample": sample, "accepted": accepted})
    return edges


def test_the_edge_check_sees_list_items_lengths_and_exclusive_bounds() -> None:
    full = {"names": (["ab"], ["AB"]), "code": (["ab", "x" * 40], ["a", "x" * 41]),
            "ratio": ([math.nextafter(0.0, 1.0), math.nextafter(1.0, 0.0)], [0.0, 1.0]), "count": ([1, 9], [0, 10])}
    assert edge_gaps(_Probe, _probe_rows(full)) == []
    assert sorted(edge_gaps(_Probe, [])) == sorted(f"{('_Probe', f)}: no row with both verdicts"
                                                   for f in ("names[]", "code", "ratio", "count"))
    token = {"names": (["ab"], ["AB"]), "code": (["abc"], ["x" * 100]), "ratio": ([0.5], [2.0]), "count": ([5], [20])}
    gaps = edge_gaps(_Probe, _probe_rows(token))
    assert {gap.split(":")[0] for gap in gaps} == {str(("_Probe", f)) for f in ("code", "ratio", "count")}
    assert any("maxLength" in g for g in gaps) and any("minLength" in g for g in gaps)
    assert any("exclusiveMinimum" in g for g in gaps) and any("exclusiveMaximum" in g for g in gaps)
    for field in full:
        assert edge_gaps(_Probe, _probe_rows({k: v for k, v in full.items() if k != field})) != [], field


# ---- One state table drives what each state may and must hold ----


def _optional_wire_fields() -> list[str]:
    return [f.alias or n for n, f in UploadJob.model_fields.items() if not f.is_required()]


def test_the_state_table_names_every_state_and_every_optional_field() -> None:
    assert len(set(jobs.JOB_STATE_FIELDS)) == len(jobs.JOB_STATE_FIELDS)
    assert set(jobs.JOB_STATE_FIELDS) == {*_optional_wire_fields(), "workStages"}
    assert set(jobs.JOB_STATE_TABLE) == {jobs.job_group(kind) for kind in jobs.JOB_KINDS}
    for group, rows in jobs.JOB_STATE_TABLE.items():
        assert list(rows) == list(jobs.UPLOAD_JOB_STATES), group
        for state, row in rows.items():
            if row is not None:
                assert list(row["fields"]) == list(jobs.JOB_STATE_FIELDS), (group, state)
                assert row["errors"] in ("none", "failure", "interrupted"), (group, state)
                assert (row["errors"] == "none") is (row["fields"]["error"] == "forbidden"), (group, state)
    assert list(jobs.JOB_PRE_RUN_STAGES) == list(jobs.JOB_KINDS)


def test_the_state_table_says_what_the_requirements_say() -> None:
    table = jobs.JOB_STATE_TABLE
    for group, rows in table.items():
        # Only a running job becomes interrupted (REQ-JOB-3.2): it started, and gives the one sentence
        assert rows["interrupted"]["fields"]["startedAtUTC"] == "required" and rows["interrupted"]["errors"] == "interrupted"
        # A queued job has started nothing and counts no work (REQ-JOB-2.1, 2.3)
        assert all(rows["queued"]["fields"][f] == "forbidden" for f in ("startedAtUTC", "workStages", "inputSpan",
                                                                         "cancelRequestedAtUTC"))
        # A queue count only while queued (REQ-JOB-2.2)
        assert [s for s, r in rows.items() if r is not None and r["fields"]["queueAhead"] != "forbidden"] == ["queued"]
        # A failed job never gives the interrupted sentence; only failed and interrupted give any
        assert [s for s, r in rows.items() if r is not None and r["errors"] != "none"] == ["failed", "interrupted"]
        # A result only on a succeeded job (REQ-JOB-4.5, 5.4); the rejected media job names its attempt (design 3.8)
        for field in ("solutionId", "variantId"):
            assert [s for s, r in rows.items() if r is not None and r["fields"][field] != "forbidden"] == (
                ["succeeded"] if (field == "solutionId") is (group == "solve") else []), (group, field)
    assert table["solve"]["rejected"] is None  # REQ-JOB-3.7


# The second reading: what the requirements and the design say, each sentence quoted and turned into the rule it states
# about a record, in its own form. `has` is the set of fields a record holds; "workStages" a stage after creation.
TERMINAL = {"succeeded", "rejected", "failed", "canceled", "interrupted"}
# The second reading, platform-1's fourth version: each rule cites whole sentences of the two spec files, each with
# the section it stands in, and says in its own form what they say about which fields a job holds.
# `test_every_quote_stands_whole_in_the_section_it_names` holds each sentence to that place. A sentence that says
# something the table does not is a disagreement to settle in the spec, the table or a question to the lead, never
# one cut around.
S_STATE = ("design 3.8", "Per state: `queued` has no start, finish, result id, error or cancel time; `running` has a "
           "start, no finish, result id or error, and may have a cancel time; `succeeded` has a start, a finish and its "
           "kind's result ids (`solutionId` for solve; `attemptId` and `variantId` for the media kinds) and no error; "
           "`rejected` (media kinds only) has a start, a finish and `attemptId`, no `variantId`, no error; `failed` has "
           "a start, a finish and an error, no `solutionId` or `variantId`, and may have `attemptId`; `canceled` has a "
           "finish and no result id or error; `interrupted` has a start, a finish and an error, and no result id "
           "[changed in platform-1's fourth version to say what the table says: a running job's cancel time by the "
           "lead's ruling of 2026-11-12, and the start of a failed and an interrupted job, which design 4 reaches only "
           "from `running`].")
S_CANCEL = ("design 3.8", "`cancelRequestedAtUTC` only on a job that started, and on a job canceled after it started "
            "always [decided 2026-11-12: a record never drops a true thing it recorded, REQ-JOB-4.2 and 4.3, so a "
            "cancel asked of a running job stays when it then succeeds, fails, is refused or is interrupted], not "
            "before `startedAtUTC` nor after `finishedAtUTC`.")
READING: list[tuple[list[tuple[str, str]], Any]] = [
    ([("REQ-JOB-2.1", "A job SHALL be `queued` from its creation until the runner gives it a slot, and `running` only "
                      "from then; `startedAtUTC` SHALL be the moment it got the slot.")],
     lambda g, st, has: (st != "queued" or "startedAtUTC" not in has) and (st != "running" or "startedAtUTC" in has)),
    ([S_STATE],  # its starts
     lambda g, st, has: st not in ("succeeded", "rejected", "failed", "interrupted") or "startedAtUTC" in has),
    ([S_STATE],  # its finishes
     lambda g, st, has: ("finishedAtUTC" in has) == (st in TERMINAL)),
    ([S_STATE],  # its errors
     lambda g, st, has: ("error" in has) == (st in ("failed", "interrupted"))),
    ([S_STATE,
      ("REQ-JOB-5.4", "Results SHALL be written once and made visible before the commit point; a `succeeded` job "
                      "SHALL always have every result it names, whole and valid; a result no `succeeded` job names "
                      "SHALL never be served."),
      ("REQ-JOB-4.5", "No result of a `canceled` job SHALL ever be served.")],  # its result ids
     lambda g, st, has: ("solutionId" not in has or st == "succeeded") and ("variantId" not in has or st == "succeeded")
     and ("attemptId" not in has or st in ("succeeded", "rejected", "failed"))
     and (st != "succeeded" or ({"solutionId"} <= has if g == "solve" else {"attemptId", "variantId"} <= has))
     and (st != "rejected" or "attemptId" in has)),
    ([("design 3.8", "Per kind: `solutionId` and `inputSpan` only for solve; `request`, `graphId`, `attemptId`, "
                     "`variantId` only for the media kinds, which require `request` and `graphId`, with "
                     "`request.kind` equal to the job's kind.")],
     lambda g, st, has: (g == "solve" or not {"solutionId", "inputSpan"} & has)
     and (g == "media" or not {"request", "graphId", "attemptId", "variantId"} & has)
     and (g == "solve" or {"request", "graphId"} <= has)),
    ([("REQ-JOB-2.2", "WHILE a job is `queued`, THE served record SHALL say how many jobs are ahead of it in its "
                      "group's queue in this process, counted at the moment of the answer, and this count SHALL never "
                      "be stored."),
      ("design 3.8", "`queueAhead` only when `queued` (a served count: the store refuses to write it).")],
     lambda g, st, has: "queueAhead" not in has or st == "queued"),
    ([("REQ-JOB-2.6", "FOR a clip, THE solve job SHALL record which frames it decoded (first frame index, frame count, "
                      "and their times in seconds into the clip)."),
      ("design 3.4", "Frame span: for a clip, the decode records `{firstFrameIndex: 0, frameCount: len(frames), "
                     "startSec: 0, endSec: (len(frames) - 1) / fps}` from the probed frame rate on the asset, as the "
                     "job field `inputSpan`.")],  # the decode runs after the start
     lambda g, st, has: "inputSpan" not in has or "startedAtUTC" in has),
    ([("REQ-JOB-2.3", "A stage that did not run SHALL be absent.")],  # no work stage runs before the start
     lambda g, st, has: "workStages" not in has or "startedAtUTC" in has),
    ([S_CANCEL,
      ("REQ-JOB-4.1", "A cancel of a `queued` job SHALL make it `canceled` at once."),
      ("REQ-JOB-4.2", "A cancel of a `running` job SHALL record when it was asked, stop the work at the next point the "
                      "kind can stop (a child process and every process it started are stopped at once; work in the "
                      "server process stops between steps), and then make the job `canceled`."),
      ("REQ-JOB-4.3", "WHEN the commit point was passed before the work stopped, THE job SHALL end `succeeded`, and "
                      "its record SHALL still show when the cancel was asked.")],
     lambda g, st, has: ("cancelRequestedAtUTC" not in has or "startedAtUTC" in has)
     and (not (st == "canceled" and "startedAtUTC" in has) or "cancelRequestedAtUTC" in has)),
    ([("REQ-JOB-6.1", "Every job that started SHALL record its producer, the name and revision of the runner that "
                      "took it: a name and a revision number, which a unit raises when it changes results [decided "
                      "2026-11-11]; a job that never started SHALL have none [decided 2026-11-13: a producer names the "
                      "code that made a job's result, and a job that never ran has none]."),
      ("design 3.8", "`producer` is required on a job that started and absent on one that never did, as design 4 "
                     "writes it with the start [decided 2026-11-13]; a record today's code wrote has none and could "
                     "not have had one, so the store judges it by `SolveJob` and never gives it one (3.1).")],
     lambda g, st, has: ("producer" in has) == ("startedAtUTC" in has)),
]
# REQ-JOB-3.7: the solve never ends `rejected`; REQ-JOB-3.2: the interrupted reason; and a failure is any other sentence
REJECTED_SOLVE_EXISTS = False
ERRORS_BY_STATE = {"failed": "failure", "interrupted": "interrupted"}
OTHER_SENTENCES: list[tuple[str, str]] = [
    ("REQ-JOB-3.7", "`rejected` SHALL be a terminal state for kinds whose work ends in a check that can refuse (the "
                    "display copy's comparison and the restoration's gate); the solve never ends `rejected`."),
    ("REQ-JOB-3.2", "Such a `running` job SHALL become `interrupted` with the plain reason \"The server stopped before "
                    "this job finished.\" and SHALL never run again by itself [decided 2026-11-11: a file that stopped "
                    "the server must not stop it again at every start]."),
]
SPEC = Path(__file__).resolve().parents[2] / ".kiro" / "specs" / "upload-jobs"


def _spec_section(where: str) -> str:
    """The text of the section a citation names, its lines joined and its spaces made single: a requirement's own
    numbered criterion (`REQ-JOB-2.1`), or a design section (`design 3.8`) up to the next heading; empty when the
    files have no such section."""
    if where.startswith("REQ-JOB-"):
        number, _, item = where.removeprefix("REQ-JOB-").partition(".")
        lines = (SPEC / "requirements.md").read_text(encoding="utf-8").splitlines()
        heading = next((l for l in lines if l.startswith(f"### REQ-JOB-{number}:")), None)
        if heading is None or not item:
            return ""
        start = lines.index(heading)
        body: list[str] = []
        for line in lines[start + 1:]:
            if line.startswith("#"):
                break
            if re.match(r"^  \d+\. ", line):
                if body:
                    break
                if line.startswith(f"  {item}. "):
                    body.append(line.split(". ", 1)[1])
                continue
            if body:
                body.append(line)
    else:
        number = where.removeprefix("design ")
        lines = (SPEC / "design.md").read_text(encoding="utf-8").splitlines()
        heading = f"### {number} " if "." in number else f"## {number}. "
        found = next((l for l in lines if l.startswith(heading)), None)
        if found is None:
            return ""
        start = lines.index(found)
        body = []
        for line in lines[start + 1:]:
            if line.startswith("## ") or line.startswith("### "):
                break
            body.append(line)
    return " ".join(" ".join(body).split())


def _all_citations() -> list[tuple[str, str]]:
    return [c for cites, _ in READING for c in cites] + OTHER_SENTENCES


def _quote_problem(where: str, sentence: str) -> str | None:
    """None when `sentence` stands whole in the section `where` names; otherwise what is wrong with it."""
    if not re.fullmatch(r"REQ-JOB-\d+\.\d+|design \d+(\.\d+)?", where):
        return "not a section name"
    text = _spec_section(where)
    at = text.find(sentence)
    if at < 0:
        return "not in that section"
    before, after = text[:at], text[at + len(sentence):]
    if not (before == "" or before.endswith((". ", "- "))):
        return "starts inside a sentence"
    if not (sentence.endswith(".") and (after == "" or after.startswith(" "))):
        return "ends inside a sentence"
    return None


@pytest.mark.parametrize(("where", "sentence"), sorted(set(_all_citations())))
def test_every_quote_stands_whole_in_the_section_it_names(where: str, sentence: str) -> None:
    assert _quote_problem(where, sentence) is None, f"{where}: {_quote_problem(where, sentence)}: {sentence[:60]}"


def test_a_cut_reworded_or_misfiled_quote_is_refused() -> None:
    whole = "A stage that did not run SHALL be absent."
    assert _quote_problem("REQ-JOB-2.3", whole) is None
    assert _quote_problem("REQ-JOB-2.3", "stage that did not run SHALL be absent.") == "starts inside a sentence"
    assert _quote_problem("REQ-JOB-2.3", "A stage that did not run SHALL") == "ends inside a sentence"
    assert _quote_problem("REQ-JOB-2.3", "A stage that did not run SHALL be missing.") == "not in that section"
    assert _quote_problem("REQ-JOB-2.2", whole) == "not in that section"
    assert _quote_problem("design 3.8", whole) == "not in that section"
    assert _quote_problem("REQ-JOB-2", whole) == "not a section name"
    # v3's cut of the per-state sentence, and its producer sentence found in neither file
    assert _quote_problem("design 3.8", "`running` has a start and none of the rest.") == "not in that section"
    assert _quote_problem("REQ-JOB-6.1", "Records written before the runner names its producer stay valid; nothing "
                                         "else binds it.") == "not in that section"
    # The sentence REQ-JOB-6.1 held before the ruling of 2026-11-13
    assert _quote_problem("REQ-JOB-6.1", "Every job SHALL record its producer: a name and a revision number, which a "
                                         "unit raises when it changes results [decided 2026-11-11].") \
        == "not in that section"


def test_every_rule_of_the_reading_cites_a_sentence() -> None:
    assert all(cites for cites, _ in READING)


def test_a_state_row_names_every_field_or_is_refused() -> None:
    n = len(jobs.JOB_STATE_FIELDS)
    letters = " ".join("E" for _ in jobs.JOB_STATE_FIELDS)
    assert jobs.state_row(letters, "none")["fields"] == {f: "either" for f in jobs.JOB_STATE_FIELDS}
    for wrong, count in ((letters + " E", n + 1), (letters.rsplit(" ", 1)[0], n - 1)):
        with pytest.raises(ValueError, match=f"^a state row needs {n} rules, not {count}$"):
            jobs.state_row(wrong, "none")


def test_the_table_takes_exactly_what_the_requirements_allow() -> None:
    from itertools import product

    fields = list(jobs.JOB_STATE_FIELDS)
    wrong = []
    for group, rows in jobs.JOB_STATE_TABLE.items():
        for state, row in rows.items():
            if group == "solve" and state == "rejected":
                assert (row is None) is not REJECTED_SOLVE_EXISTS
                continue
            assert row["errors"] == ERRORS_BY_STATE.get(state, "none"), (group, state)
            for bits in product((False, True), repeat=len(fields)):
                has = {f for f, b in zip(fields, bits) if b}
                table = all(jobs.state_allows(rule, f in has, "startedAtUTC" in has) for f, rule in row["fields"].items())
                reading = all(rule(group, state, has) for _, rule in READING)
                if table is not reading:
                    broken = [cites[0][1][:40] for cites, rule in READING if not rule(group, state, has)]
                    wrong.append((group, state, sorted(has), table, broken))
    assert wrong == [], f"{len(wrong)} patterns differ, the first: {wrong[:3]}"


def test_a_job_canceled_after_it_started_holds_its_cancel_time() -> None:
    # The second reader's three records (AUDIT J), each refused for REQ-JOB-4.2
    for case in [c for c in SAMPLES["invalid"] if c["reason"].startswith("canceled after it started")]:
        accepted, why = verdict(case["sample"])
        assert not accepted and jobs.M_STATE in why, case["reason"]
    assert len([c for c in SAMPLES["invalid"] if c["reason"].startswith("canceled after it started")]) == 3


def test_every_state_meets_every_field_present_and_absent() -> None:
    seen = {(c["kind"], c["state"], c["field"], c["present"]) for c in SAMPLES["grid"]}
    assert seen == {(k, s, f, p) for k in jobs.JOB_KINDS for s in jobs.UPLOAD_JOB_STATES for f in jobs.JOB_STATE_FIELDS
                    for p in (True, False)}
    wrong = [(c["kind"], c["state"], c["base"], c["field"], c["present"], c["accepted"]) for c in SAMPLES["grid"]
             if verdict(c["sample"])[0] is not c["accepted"]]
    assert wrong == []


def test_every_key_dropped_from_every_valid_record_gets_its_verdict() -> None:
    assert len(SAMPLES["dropped"]) > 200
    wrong = [c["what"] for c in SAMPLES["dropped"] if verdict(c["sample"])[0] is not c["accepted"]]
    assert wrong == []


def test_every_bound_has_an_accepted_and_a_refused_edge() -> None:
    whats = {c["what"] for c in SAMPLES["edges"]}
    for what in whats:
        verdicts = {c["accepted"] for c in SAMPLES["edges"] if c["what"] == what}
        assert verdicts == {True, False}, what


@pytest.mark.parametrize("case", SAMPLES["keyed"], ids=[c["what"] for c in SAMPLES["keyed"]])
def test_an_unknown_or_python_named_key_is_refused_at_every_level(case: dict[str, Any]) -> None:
    assert verdict(case["sample"])[0] is False


def test_minus_zero_is_read_and_written_back_as_zero() -> None:
    record = next(e["sample"] for e in SAMPLES["edges"] if e["what"] == "span start" and e["accepted"]
                  and str(e["sample"]["inputSpan"]["startSec"]) == "-0.0")
    dumped = UploadJob.model_validate(record).model_dump(by_alias=True, exclude_unset=True)
    assert str(dumped["inputSpan"]["startSec"]) == "0.0"


def test_the_stages_are_every_kinds_in_one_list_and_the_failures_hold_no_slot() -> None:
    assert list(jobs.JOB_STAGES) == list(dict.fromkeys(s for stages in jobs.JOB_STAGE_UNITS.values() for s in stages))
    assert all("{" not in sentence for sentence in jobs.SOLVE_FAILURE_SENTENCES)
    assert jobs.INTERRUPTED_SENTENCE in jobs.SOLVE_FAILURE_SENTENCES


# The runner's own sentences for a solve (platform-5s; the lead's answer of 2026-11-18): a job past its whole bound
# says so, since a time limit is not an internal error, and a job whose result could not be written says that
RUNNER_SOLVE_SENTENCES = ("The solve stopped at its time limit.", "The job's result could not be stored.")


def test_a_failed_solve_can_say_it_stopped_at_its_time_limit_or_could_not_store_its_result() -> None:
    valid = build_job_samples()["valid"]
    failed = next(r for r in valid if r["kind"] == "solve" and r["state"] == "failed")
    interrupted = next(r for r in valid if r["kind"] == "solve" and r["state"] == "interrupted")
    for sentence in RUNNER_SOLVE_SENTENCES:
        assert sentence in jobs.SOLVE_FAILURE_SENTENCES
        assert verdict({**failed, "error": sentence}) == (True, "")
        taken, why = verdict({**interrupted, "error": sentence})
        assert not taken and jobs.M_INTERRUPTED in why, "an interrupted job says only that the server stopped"
    restoration = next(r for r in valid if r["kind"] == "restoration" and r["state"] == "failed")
    for sentence in RUNNER_SOLVE_SENTENCES:
        assert not verdict({**restoration, "error": sentence})[0], "a solve's sentences are not a restoration's"


def test_the_older_solve_job_record_is_unchanged() -> None:
    # Today's server writes SolveJob and the web reads SolveJobSchema; UploadJob replaces them in tasks 4 and 5
    for record in ingest.build_ingest_samples()["valid"]["SolveJob"]:
        ingest.SolveJob.model_validate(record)
        assert verdict(record)[0] is False  # it has no kind, so it is not yet an UploadJob


# ---- Every job-shaped sample in every fixture (platform-1's fourth version, point 3) ----

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _job_shaped(value: Any) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].startswith("job_")
            and "state" in value and "createdAtUTC" in value)


def _job_shaped_in(value: Any) -> list[Any]:
    found = [value] if _job_shaped(value) else []
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    for child in children:
        found += _job_shaped_in(child)
    return found


def _job_shaped_with_paths(value: Any, path: str) -> list[tuple[str, Any]]:
    """As `_job_shaped_in`, each with where it is in `value` (`returned`, `transitions[1]`, `writes[1].payload`)."""
    found = [(path or "(the value itself)", value)] if _job_shaped(value) else []
    if isinstance(value, dict):
        for key, child in value.items():
            found += _job_shaped_with_paths(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found += _job_shaped_with_paths(child, f"{path}[{index}]")
    return found


def _job_verdicts(data: dict[str, Any]) -> list[tuple[str, Any, bool, Any]]:
    """job_contract.json: what each of its sections says of each sample."""
    out = [(f"valid[{i}]", s, True) for i, s in enumerate(data["valid"])]
    out += [(f"invalid[{i}] {c['reason']}", c["sample"], False) for i, c in enumerate(data["invalid"])]
    for section in ("edges", "grid", "dropped"):
        out += [(f"{section}[{i}]", c["sample"], c["accepted"]) for i, c in enumerate(data[section])]
    out += [(f"keyed[{i}] {c['what']}", c["sample"], False) for i, c in enumerate(data["keyed"])]
    return [(where, sample, accepted, sample) for where, sample, accepted in out]


def _ingest_verdicts(data: dict[str, Any]) -> list[tuple[str, Any, bool, Any]]:
    """ingest_contract.json: today's SolveJob samples, taken or refused. Such a record holds no `kind` and no
    producer and could not have had one (REQ-JOB-6.1, design 3.8), and the store judges it by `SolveJob`, never
    giving it one; so the job contract is asked whether it takes everything else the record holds: `kind: "solve"`
    added, and on a job that started the producer the job contract's own solve samples name (`TODAYS_PRODUCER_GAP`),
    here only, in this test."""
    out = [(f"valid.SolveJob[{i}]", s, True) for i, s in enumerate(data["valid"]["SolveJob"])]
    out += [(f"invalid[{i}] {c['reason']}", c["sample"], False) for i, c in enumerate(data["invalid"])
            if c["schema"] == "SolveJob"]
    return [(where, sample, accepted, _todays_record_but_its_gap(sample)) for where, sample, accepted in out]


TODAYS_PRODUCER_GAP = {"name": "albedian.solve", "revision": 1}


def _todays_record_but_its_gap(sample: Any) -> Any:
    if not isinstance(sample, dict):
        return sample
    record = {**sample, "kind": "solve"}
    return {**record, "producer": TODAYS_PRODUCER_GAP} if "startedAtUTC" in record else record


# Each fixture that holds a job-shaped object, and what it says of each one. A fixture with one that no reader here
# judges fails the test below, so a new sample cannot disagree in silence.
def _solution_ingest_verdicts(data: dict[str, Any]) -> list[tuple[str, Any, bool, Any]]:
    """observation_solution_contract.json: the SolveJob samples of its `ingest` section, read as ingest_contract.json's."""
    out = [(f"ingest.valid[{i}]", c["sample"], True) for i, c in enumerate(data["ingest"]["valid"]) if c["schema"] == "SolveJob"]
    out += [(f"ingest.invalid[{i}] {c['reason']}", c["sample"], False) for i, c in enumerate(data["ingest"]["invalid"])
            if c["schema"] == "SolveJob"]
    return [(where, sample, accepted, _todays_record_but_its_gap(sample)) for where, sample, accepted in out]


def _characterization_verdicts(data: dict[str, Any]) -> list[tuple[str, Any, bool, Any]]:
    """solve_upload_characterization.json: what `run_solve_job` returned, reported and wrote in each case, as today's
    code wrote it, so the job contract is expected to take each record (with its placeholders read back into values
    of their form: each `<now k>` the characterization's own clock instant, the sent hash and size made-up values,
    and, as for today's other records, `kind: "solve"` and on a started job the solve samples' producer).
    The exception, named: `decode_refuses_the_file` stands for one of the nine failure texts today's solve job can
    store that the job contract refuses (`test_every_failure_text_todays_solve_job_can_store_is_judged` finds all
    fourteen from the code and pins the nine). Its error keeps the decoder's own words ("The decoder could not read
    the upload: " and what the decoder said); the job contract takes only its kind's fixed sentences (platform-1,
    design 3.8 as built), so it refuses those three records (the returned one, the last transition and the last
    job.json write) by that rule, and SolveJob, which judges today's records, takes them."""
    out = []
    for case, record in data.items():
        for where, job in _job_shaped_with_paths(record, ""):
            # A stand-in for the nine refused texts, each given its fixed sentence by the runner (platform-5, the
            # spec's task 5): refused by this rule, by name
            refused = case == "decode_refuses_the_file" and job["state"] == "failed"
            accepted: Any = jobs.M_FAILURE if refused else True
            out.append((f"{case} {where}: a {job['state']} record", job, accepted,
                        _todays_record_but_its_gap(_unplaced(job))))
    return out


CHARACTERIZATION_HASH = "c" * 64
CHARACTERIZATION_CLOCK = datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc)  # the characterization's CLOCK_START


def _unplaced(value: Any) -> Any:
    """A characterization record with each of its placeholders read back into a value of its form."""
    if isinstance(value, dict):
        return {k: _unplaced(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_unplaced(v) for v in value]
    if isinstance(value, str):
        now = re.fullmatch(r"<now (\d+)>", value)
        if now:
            return ingest.format_utc(CHARACTERIZATION_CLOCK + timedelta(minutes=int(now.group(1))))
        if value == "<bytes sent>":
            return 1000
        return value.replace("<sha256 sent>", CHARACTERIZATION_HASH).replace("<sha16 sent>", CHARACTERIZATION_HASH[:16])
    return value


JOB_FIXTURE_READERS: dict[str, Any] = {"job_contract.json": _job_verdicts, "ingest_contract.json": _ingest_verdicts,
                                       "observation_solution_contract.json": _solution_ingest_verdicts,
                                       "solve_upload_characterization.json": _characterization_verdicts}


def _fixture_disagreements(folder: Path) -> tuple[list[str], list[str], int]:
    """For every JSON fixture under `folder`: the job-shaped objects no reader judges, the samples the job contract
    judges otherwise than their fixture says, and how many were judged."""
    unread: list[str] = []
    wrong: list[str] = []
    judged = 0
    for path in sorted(folder.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        shaped = _job_shaped_in(data)
        rel = str(path.relative_to(folder))
        if rel not in JOB_FIXTURE_READERS:
            if shaped:
                unread.append(f"{rel}: {len(shaped)} job-shaped objects no reader judges")
            continue
        verdicts = JOB_FIXTURE_READERS[rel](data)
        covered = {id(sample) for _, sample, _, _ in verdicts}
        unread += [f"{rel}: a job-shaped object outside every section its reader reads"
                   for sample in shaped if id(sample) not in covered]
        for where, _, accepted, record in verdicts:
            judged += 1
            # An expectation is True (taken), False (refused) or the message of the one rule that refuses it
            taken, why = verdict(record)
            if taken is not (accepted is True) or (isinstance(accepted, str) and accepted not in why):
                rule = "" if taken or not isinstance(accepted, str) else f", not by the rule \"{accepted}\""
                wrong.append(f"{rel} {where}: the job contract {'takes' if taken else 'refuses'} it{rule}")
    return unread, wrong, judged


def test_every_job_shaped_sample_in_every_fixture_is_judged_as_its_fixture_says() -> None:
    unread, wrong, judged = _fixture_disagreements(FIXTURES)
    assert unread == [] and wrong == [], (unread, wrong[:10], len(wrong))
    # the four readers judge 2,672 job-shaped samples on 2026-11-13: 1,827 in the two contract fixtures, 6 in the
    # solution contract's ingest section, 839 in the solve chain's characterization
    assert judged >= 2672


def test_the_fixture_check_finds_an_unread_job_and_a_wrong_verdict(tmp_path: Path) -> None:
    job = json.loads((FIXTURES / "job_contract.json").read_text(encoding="utf-8"))
    (tmp_path / "other.json").write_text(json.dumps({"a": [job["valid"][0]]}), encoding="utf-8")
    job["valid"].append(job["invalid"][0]["sample"])
    job["invalid"][0]["sample"] = job["valid"][0]
    (tmp_path / "job_contract.json").write_text(json.dumps(job), encoding="utf-8")
    ingest = json.loads((FIXTURES / "ingest_contract.json").read_text(encoding="utf-8"))
    ingest["valid"]["SolveJob"].append({**ingest["valid"]["SolveJob"][3], "startedAtUTC": None})
    ingest["stray"] = [ingest["valid"]["SolveJob"][0]]
    (tmp_path / "ingest_contract.json").write_text(json.dumps(ingest), encoding="utf-8")
    unread, wrong, _ = _fixture_disagreements(tmp_path)
    assert unread == ["ingest_contract.json: a job-shaped object outside every section its reader reads",
                      "other.json: 1 job-shaped objects no reader judges"]
    assert [w.split(": ")[0] for w in wrong] == [
        f"ingest_contract.json valid.SolveJob[{len(ingest['valid']['SolveJob']) - 1}]",
        f"job_contract.json valid[{len(job['valid']) - 1}]",
        f"job_contract.json invalid[0] {job['invalid'][0]['reason']}"]


CHARACTERIZATION = "solve_upload_characterization.json"


def _characterization_with(tmp_path: Path, change: Callable[[dict[str, Any]], None]) -> list[str]:
    """The fixture check's wrong verdicts on a copy of the characterization alone, changed by `change`."""
    data = json.loads((FIXTURES / CHARACTERIZATION).read_text(encoding="utf-8"))
    change(data)
    (tmp_path / CHARACTERIZATION).write_text(json.dumps(data), encoding="utf-8")
    unread, wrong, _ = _fixture_disagreements(tmp_path)
    assert unread == []
    return wrong


def test_a_record_refused_by_another_rule_than_the_one_named_is_a_wrong_verdict(tmp_path: Path) -> None:
    """One of the three expected refused by the failure rule, given a fixed sentence and a finish before its start: the
    contract still refuses it, by the times rule alone, and the check names the record and the rule it expected."""
    def change(data: dict[str, Any]) -> None:
        returned = data["decode_refuses_the_file"]["returned"]
        returned["error"] = "The solve stopped on an internal error."
        returned["startedAtUTC"], returned["finishedAtUTC"] = "<now 9>", "<now 0>"

    assert _characterization_with(tmp_path, change) == [
        f"{CHARACTERIZATION} decode_refuses_the_file returned: a failed record: the job contract refuses it, "
        f"not by the rule \"{jobs.M_FAILURE}\""]


def test_a_succeeded_record_finished_before_it_started_is_a_wrong_verdict(tmp_path: Path) -> None:
    """Each `<now k>` is read back as its own instant, k minutes after the clock's start: a succeeded record whose
    finish is before its start is named."""
    found: list[str] = []

    def change(data: dict[str, Any]) -> None:
        for case, record in data.items():
            for where, job in _job_shaped_with_paths(record, ""):
                if job["state"] == "succeeded" and not found:
                    job["startedAtUTC"], job["finishedAtUTC"] = "<now 9>", "<now 0>"
                    found.append(f"{CHARACTERIZATION} {case} {where}: a succeeded record: the job contract refuses it")

    assert _characterization_with(tmp_path, change) == found and len(found) == 1


# ---- Every failure text today's solve job can store (platform-3's fourth version) ----

REPO = Path(__file__).resolve().parents[2]
STAND_IN_ORIGINAL = Path("/stand-in/original.heic")
# What the job contract does with each, by name. A failure text added to the solve job, or one changed, fails the test
# below until it is added here. The nine refused each get their kind's fixed sentence from the runner (platform-5, the
# spec's task 5), which drops the decoder's own words and sizes.
TAKEN_FAILURE_TEXTS = {
    "ffmpeg is not installed to decode the upload.",
    solve_job.HEIF_PROBE_REFUSAL,
    solve_job.TILE_DECODE_REFUSAL,
    "The decoder did not write one frame for every tile.",
    solve_job.UNEXPECTED_ERROR_REASON,
}
REFUSED_FAILURE_TEXTS = {
    "The decoder wrote a frame without a PGM header.",
    "The decoder wrote an unexpected {width}x{height} frame with maximum value {maxval}.",
    "The decoder's last frame is cut short.",
    f"ffmpeg took longer than {solve_job.DECODE_TIMEOUT_SEC} s to decode the upload.",
    "The decoder could not read the file: {msg}",
    "The decoder could not read the file: no frame bytes produced",
    "The decoder produced a {frame.shape[1]}x{frame.shape[0]} frame, not the stored {width}x{height}.",
    "The file's tile grids do not give one {width}x{height} image.",
    "The file's tile grid does not cover the stored {width}x{height} image.",
}


def _text_forms(node: ast.expr, function: Optional[ast.AST], module: Any, where: str) -> list[str]:
    """The texts a SolveJobError's argument can be: a literal; a module constant; an f-string whose slots are module
    constants (filled) or other values (kept as `{the expression}`), and, for a slot that is a variable its function
    sets to a literal in one branch, that literal too. Any other form fails here, so no text is left out unread."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and isinstance(getattr(module, node.id, None), str):
        return [getattr(module, node.id)]
    assert isinstance(node, ast.JoinedStr), f"{where}: a SolveJobError text this test cannot read"
    forms = [""]
    for part in node.values:
        if isinstance(part, ast.Constant):
            options = [str(part.value)]
        else:
            assert isinstance(part, ast.FormattedValue) and part.conversion == -1 and part.format_spec is None, \
                f"{where}: a SolveJobError text this test cannot read"
            value = part.value
            constant = getattr(module, value.id, None) if isinstance(value, ast.Name) else None
            if isinstance(constant, (str, int)) and not isinstance(constant, bool):
                options = [str(constant)]
            else:
                options = ["{" + ast.unparse(value) + "}"]
                if isinstance(value, ast.Name) and function is not None:
                    options += _literals_set_to(value.id, function)
        forms = [form + option for form in forms for option in options]
    return forms


def _literals_set_to(name: str, function: ast.AST) -> list[str]:
    """The string literals `function` sets `name` to, directly or as a branch of a conditional expression."""
    out = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and [ast.unparse(t) for t in node.targets] == [name]:
            branches = [node.value.body, node.value.orelse] if isinstance(node.value, ast.IfExp) else [node.value]
            out += [b.value for b in branches if isinstance(b, ast.Constant) and isinstance(b.value, str)]
    return out


def _failure_texts_in(source: str, rel: str, module: Any) -> dict[str, list[str]]:
    """The texts of every `SolveJobError(...)` in one module's source, each with where it is written."""
    found: dict[str, list[str]] = {}
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and ast.unparse(call.func).split(".")[-1] == "SolveJobError"):
            continue
        where = f"{rel}:{call.lineno}"
        assert len(call.args) == 1 and not call.keywords, f"{where}: a SolveJobError this test cannot read"
        # the innermost function the call is written in
        function = min((f for f in functions if f.lineno <= call.lineno <= (f.end_lineno or f.lineno)),
                       key=lambda f: (f.end_lineno or f.lineno) - f.lineno, default=None)
        for text in _text_forms(call.args[0], function, module, where):
            found.setdefault(text, []).append(where)
    return found


def _storable_failure_texts() -> dict[str, list[str]]:
    """Every failure text today's solve job can store, found in the code: each `SolveJobError(...)` under `services/`
    (the solve job stores its text through `_stored_reason`) and the internal error; each with where it is written."""
    found: dict[str, list[str]] = {}
    for path in sorted((REPO / "services").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "SolveJobError" not in source:
            continue
        rel = path.relative_to(REPO)
        module = importlib.import_module(".".join(rel.with_suffix("").parts))
        for text, where in _failure_texts_in(source, str(rel), module).items():
            found.setdefault(text, []).extend(where)
    found.setdefault(solve_job.UNEXPECTED_ERROR_REASON, []).append("UNEXPECTED_ERROR_REASON")
    return found


def _fixed_sentences_fitting(text: str) -> list[str]:
    """The solve kind's fixed sentences that `text` gives when each `{slot}` is filled with any text, the empty one
    too; none for a text with no slot, which is judged as it is."""
    pieces = re.split(r"\{[^{}]*\}", text)
    if len(pieces) == 1:
        return []
    pattern = ".*".join(re.escape(piece) for piece in pieces)
    return [s for s in jobs.SOLVE_FAILURE_SENTENCES if re.fullmatch(pattern, s, re.DOTALL)]


def _refused_by_the_failure_rule_alone(why: str) -> bool:
    """Whether the job contract's refusal names the failure rule and no other of its rules. (A field's own error stops
    the record before the rules are read, so a refusal that names a rule names no field error.)"""
    rules = [getattr(jobs, n) for n in dir(jobs) if n.startswith("M_") and getattr(jobs, n) in why]
    return rules == [jobs.M_FAILURE]


def test_every_failure_text_todays_solve_job_can_store_is_judged() -> None:
    """Each text as `_stored_reason` stores it, set as the error of the characterization's failed record that the job
    contract takes (decode_fails_unexpectedly's), and judged as the fixture check judges it. A text with a slot is
    judged with the slot as written, and no fixed sentence fits its pattern, so no filling of it is taken either."""
    data = json.loads((FIXTURES / CHARACTERIZATION).read_text(encoding="utf-8"))
    base = next(j for j in _job_shaped_in(data["decode_fails_unexpectedly"]) if j["state"] == "failed")
    assert verdict(_todays_record_but_its_gap(_unplaced(base)))[0], "the base record is taken as it is"
    taken, refused = set(), set()
    for text in _storable_failure_texts():
        stored = solve_job._stored_reason(text, STAND_IN_ORIGINAL)
        ok, why = verdict(_todays_record_but_its_gap(_unplaced({**base, "error": stored})))
        if ok:
            taken.add(text)
        else:
            assert _refused_by_the_failure_rule_alone(why), (text, why)
            refused.add(text)
        assert _fixed_sentences_fitting(text) == [], f"a filling of {text!r} may be taken"
    assert (len(taken | refused), len(refused)) == (14, 9)
    assert (taken, refused) == (TAKEN_FAILURE_TEXTS, REFUSED_FAILURE_TEXTS)


def test_the_failure_text_finder_reads_every_form_and_refuses_one_it_cannot_read() -> None:
    module = type("M", (), {"LIMIT": 60, "FIXED": "A fixed text."})
    source = ('def f(d):\n    word = d[0] if d else "outer"\n\n    def g(d):\n        word = d[0] if d else "none"\n'
              '        raise SolveJobError(f"Read {word}: {LIMIT} s.")\n    raise SolveJobError(FIXED)\n')
    assert _failure_texts_in(source, "m.py", module) == {
        "Read {word}: 60 s.": ["m.py:6"], "Read none: 60 s.": ["m.py:6"], "A fixed text.": ["m.py:7"]}
    for call in ('SolveJobError("a", "b")', 'SolveJobError("a", cause=1)', "SolveJobError()"):
        with pytest.raises(AssertionError, match="^m.py:1: a SolveJobError this test cannot read"):
            _failure_texts_in(call, "m.py", module)
    for unread in ('"a" + "b"', 'f"{x!r}"', 'f"{x:>4}"', "OTHER"):
        with pytest.raises(AssertionError, match="^f: a SolveJobError text this test cannot read"):
            _text_forms(ast.parse(unread, mode="eval").body, None, module, "f")
    assert _fixed_sentences_fitting("ffmpeg is not installed to decode the {what}.") == [
        "ffmpeg is not installed to decode the upload."]
    assert _fixed_sentences_fitting("ffmpeg is not installed to decode the upload.{tail}") == [
        "ffmpeg is not installed to decode the upload."], "a slot may be filled with nothing"
    assert _fixed_sentences_fitting("ffmpeg is not installed to decode the upload.") == []


def test_a_refusal_counts_as_the_failure_rules_only_when_it_names_that_one_error() -> None:
    data = json.loads((FIXTURES / CHARACTERIZATION).read_text(encoding="utf-8"))
    base = _todays_record_but_its_gap(_unplaced(next(
        j for j in _job_shaped_in(data["decode_fails_unexpectedly"]) if j["state"] == "failed")))
    wrong_text = {**base, "error": "Not a fixed sentence."}
    assert _refused_by_the_failure_rule_alone(verdict(wrong_text)[1])
    taken, why = verdict({**wrong_text, "finishedAtUTC": "2000-01-01T00:00:00Z"})
    assert not taken and jobs.M_FAILURE in why and jobs.M_TIMES in why
    assert not _refused_by_the_failure_rule_alone(why)


def test_a_record_todays_code_wrote_is_judged_by_its_own_contract_and_never_given_a_producer() -> None:
    """What `run_solve_job` writes today (`job.json`): no kind, no producer, a 16-character id. `SolveJob` takes it as it
    is, which is how the store reads it and what a person is served (design 3.1); the job contract would refuse it for
    the producer alone, since a job that started records one, so it is never judged by the job contract and never given
    an invented producer."""
    sha = "a" * 64
    today = {"id": f"job_{sha[:16]}", "sourceAssetSha256": sha, "state": "succeeded",
             "createdAtUTC": "2031-05-02T19:20:00Z", "startedAtUTC": "2031-05-02T19:20:01Z",
             "finishedAtUTC": "2031-05-02T19:20:41Z", "solutionId": f"sol_{sha[:16]}",
             "stages": [{"stage": "store", "unit": "bytes", "done": 9, "total": 9}]}
    assert ingest.SolveJob.model_validate(today).model_dump(by_alias=True, exclude_none=True) == today
    as_upload = {**today, "kind": "solve"}
    taken, why = verdict(as_upload)
    assert not taken and jobs.M_STATE in why
    assert verdict({**as_upload, "producer": TODAYS_PRODUCER_GAP})[0]
