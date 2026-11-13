"""The upload job record: the Python half of packages/contracts/src/jobs.ts (upload jobs spec, design 3.8, platform-1).

One record per attempt of a job of any kind: a solve of an upload, or a display copy or restoration of it. It holds
no free text. States, kinds, stages, units and pass labels are closed lists; a failure is one of the fixed sentences
(the solve kind's below, the media kinds' those of `services/contracts/media.py`), and nothing a tool printed enters a
record. Every number is a safe integer or a finite number with its range stated; every id has the pattern its
minting code writes; every time is a UTC instant on a real calendar day. The wire rules are those of
`services/contracts/wire.py`.

`UploadJob` stands beside the older `SolveJob` of `services/contracts/ingest.py`, which today's solve job writes and
the web reads. It replaces it once the runner writes every record (upload jobs tasks 4 and 5); until then nothing
writes an `UploadJob` [proposed: changing `SolveJob` here would refuse what today's server writes and the web reads].
This module lives apart from `ingest.py` because `media.py` imports `ingest.py` and a job of the media kinds embeds
the media request [proposed].

`build_job_samples` writes `tests/fixtures/job_contract.json`, which both sides judge: valid records of every state
and kind, one fault per refinement with its message, an edge sample just inside and just outside every bound,
pattern and closed list, and at every level of every record an unknown key and each key under its Python name.
"""

from __future__ import annotations

import copy
from datetime import datetime
import math
from typing import Annotated, Any, ClassVar, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from services.contracts.ingest import JOB_ID_PATTERN
from services.contracts.media import FAILURE_SENTENCES, MEDIA_ID_PATTERNS, RestoreRequest, fills_any_template
from services.contracts.wire import MAX_SAFE_INTEGER, SHA256_HEX_PATTERN, UtcInstant, WireInt, WireModel, WireNumber

JOB_KINDS = ("solve", "display_copy", "restoration")
MEDIA_JOB_KINDS = ("display_copy", "restoration")
UPLOAD_JOB_STATES = ("queued", "running", "succeeded", "rejected", "failed", "canceled", "interrupted")
PROGRESS_UNITS = ("bytes", "frames", "candidates", "steps")
# Each kind's stages in order, with the units each may report (design 3.8; the media kinds' from the restoration spec)
JOB_STAGE_UNITS: dict[str, dict[str, tuple[str, ...]]] = {
    "solve": {"store": ("bytes",), "decode": ("frames",), "detect": ("frames",), "solve": ("candidates", "steps"),
              "verify": ("steps",)},
    "display_copy": {"decode": ("frames",), "encode": ("frames",), "write": ("bytes",), "verify": ("frames",)},
    "restoration": {"decode": ("frames",), "measure": ("frames",), "restore": ("frames",), "encode": ("frames",),
                    "write": ("bytes",), "verify": ("frames",)},
}
JOB_STAGES = tuple(dict.fromkeys(stage for stages in JOB_STAGE_UNITS.values() for stage in stages))
# The solve chain's passes (design 3.4) [proposed: a closed list, not the pattern design 3.8 first gave]
JOB_PASS_LABELS = ("phone_photo_hinted", "phone_photo_generic", "triangle")
# [proposed] More passes than any chain has; a stage reports one pass at a time
JOB_PASS_MAX = 16
# [proposed] A frame span of a clip lies within a day of its start
JOB_SPAN_MAX_SEC = 86_400
PRODUCER_NAME_PATTERN = r"^[a-z][a-z0-9_.]{0,79}$"
INTERRUPTED_SENTENCE = "The server stopped before this job finished."
# The only text a failed solve stores [proposed]: today's solve job's own sentences (services/ingest/jobs.py:68,
# 154-315) with the sizes, the time limit and the decoder's own words left out, and the runner's
SOLVE_FAILURE_SENTENCES = (
    "The decoder could not read the file.",
    "ffmpeg is not installed to decode the upload.",
    "The decoder stopped at its time limit.",
    "This ffmpeg could not report the HEIC photo's tile grid (ffprobe 7.1 or later is needed), so the photo was not decoded.",
    "The decoder could not read the HEIC photo's tiles.",
    "The file's tile grid does not cover the stored image.",
    "The decoder did not write one frame for every tile.",
    "The solve stopped on an internal error.",
    "The original changed while the job ran.",
    INTERRUPTED_SENTENCE,
)
JOB_PATTERNS = {
    "jobId": JOB_ID_PATTERN,
    "sha256": SHA256_HEX_PATTERN,
    "producerName": PRODUCER_NAME_PATTERN,
    "solutionId": MEDIA_ID_PATTERNS["solution"],
    "graphId": MEDIA_ID_PATTERNS["graph"],
    "attemptId": MEDIA_ID_PATTERNS["attempt"],
    "variantId": MEDIA_ID_PATTERNS["variant"],
}

# Every refinement's message, word for word the Zod side's
M_DONE = "done cannot exceed total"
M_SPAN = "a frame span ends no earlier than it starts"
M_STATE = "the fields do not fit the job's kind and state"
M_TIMES = "times run created, started, finished"
M_REQUEST_KIND = "the request is of the job's kind"
M_STAGE_KIND = "a stage belongs to the job's kind"
M_STAGE_ORDER = "stages appear once each, in the kind's order"
M_STAGE_UNIT = "a stage reports in one of its units"
M_PASS_KIND = "only a solve stage reports a pass"
M_CANCEL = "a cancel time lies within the job's run"
M_FAILURE = "a failure is one of the fixed sentences its kind and state allow"
M_INTERRUPTED = "an interrupted job says the server stopped"
JOB_MESSAGES = (M_DONE, M_SPAN, M_STATE, M_TIMES, M_REQUEST_KIND, M_STAGE_KIND, M_STAGE_ORDER, M_STAGE_UNIT, M_PASS_KIND,
                M_CANCEL, M_FAILURE, M_INTERRUPTED)

# What each state may and must hold, for each group of kinds, read from the requirements: a job is queued until it gets
# a slot and started from then (REQ-JOB-2.1), with a queue count only while queued (2.2); a stage that did not run is
# absent (2.3), so a job that never started holds only the stages written at its creation (the solve's store stage,
# services/ingest/jobs.py run_solve_job); a clip's frames are recorded by its decode (2.6, design 3.4); only a running
# job becomes interrupted, with one sentence (3.2), and a queued one runs again (3.3); only the media kinds end rejected
# (3.7); a queued job is canceled at once (4.1) and a cancel time is recorded once it runs (4.2, 4.3); a result is named
# only by a succeeded job (4.5, 5.4), the rejected one naming its attempt (design 3.8); a media job names its request
# and graph and a solve neither (design 3.8).
# A job reaches succeeded, rejected, failed and interrupted only from running, and canceled from queued or running
# (design 4), so only a canceled job may hold no start; once it started, the cancel was asked and recorded (4.2). A
# record never drops a true thing it recorded (the lead's ruling, 2026-11-12, from 4.2 and 4.3): a cancel time asked of a
# running job stays when the job then succeeds, fails, is refused or is interrupted.
# Each field is "required", "forbidden", "either", "started" (allowed only on a job that started) or "with_start"
# (required on a job that started, forbidden on one that did not). "workStages" is a
# stage other than those its kind writes at creation. "errors" names the failure sentences the state allows: none, the
# kind's failures but the interrupted one, or the interrupted one alone. A group with no row for a state cannot be in it.
JOB_PRE_RUN_STAGES: dict[str, tuple[str, ...]] = {"solve": ("store",), "display_copy": (), "restoration": ()}
JOB_STATE_FIELDS = ("startedAtUTC", "finishedAtUTC", "error", "solutionId", "attemptId", "variantId", "inputSpan",
                    "cancelRequestedAtUTC", "queueAhead", "producer", "request", "graphId", "workStages")
_RULES = {"R": "required", "F": "forbidden", "E": "either", "S": "started", "A": "with_start"}
#                     start fin  err  sol  att  var  span cancel queue prod req graph work  errors
_STATE_ROWS: dict[str, dict[str, Optional[tuple[str, str]]]] = {
    "solve": {
        "queued":      ("F    F    F    F    F    F    F    F      E     A    F   F     F", "none"),
        "running":     ("R    F    F    F    F    F    E    S      F     A    F   F     E", "none"),
        "succeeded":   ("R    R    F    R    F    F    E    S      F     A    F   F     E", "none"),
        "rejected":    None,
        "failed":      ("R    R    R    F    F    F    E    S      F     A    F   F     E", "failure"),
        "canceled":    ("E    R    F    F    F    F    S    A      F     A    F   F     S", "none"),
        "interrupted": ("R    R    R    F    F    F    E    S      F     A    F   F     E", "interrupted"),
    },
    "media": {
        "queued":      ("F    F    F    F    F    F    F    F      E     A    R   R     F", "none"),
        "running":     ("R    F    F    F    F    F    F    S      F     A    R   R     E", "none"),
        "succeeded":   ("R    R    F    F    R    R    F    S      F     A    R   R     E", "none"),
        "rejected":    ("R    R    F    F    R    F    F    S      F     A    R   R     E", "none"),
        "failed":      ("R    R    R    F    E    F    F    S      F     A    R   R     E", "failure"),
        "canceled":    ("E    R    F    F    F    F    F    A      F     A    R   R     S", "none"),
        "interrupted": ("R    R    R    F    F    F    F    S      F     A    R   R     E", "interrupted"),
    },
}


def state_row(letters: str, errors: str) -> dict[str, Any]:
    """One row of the state table from its letters, one per field of JOB_STATE_FIELDS; any other count is refused."""
    codes = letters.split()
    if len(codes) != len(JOB_STATE_FIELDS):
        raise ValueError(f"a state row needs {len(JOB_STATE_FIELDS)} rules, not {len(codes)}")
    return {"fields": {field: _RULES[code] for field, code in zip(JOB_STATE_FIELDS, codes)}, "errors": errors}


JOB_STATE_TABLE: dict[str, dict[str, Optional[dict[str, Any]]]] = {
    group: {state: None if row is None else state_row(*row) for state, row in rows.items()}
    for group, rows in _STATE_ROWS.items()
}


def job_group(kind: str) -> str:
    return "media" if kind in MEDIA_JOB_KINDS else "solve"


def state_allows(rule: str, present: bool, started: bool) -> bool:
    """Whether a field's presence fits its rule in the state table."""
    return {"required": present, "forbidden": not present, "either": True, "started": started or not present,
            "with_start": present == started}[rule]


def _pattern(source: str) -> Any:
    return Annotated[str, StringConstraints(pattern=source)]


SafeCount = Annotated[WireInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
SafePositive = Annotated[WireInt, Field(ge=1, le=MAX_SAFE_INTEGER)]
SpanSeconds = Annotated[WireNumber, Field(ge=0, le=JOB_SPAN_MAX_SEC)]


def _refuse(failures: list[str]) -> None:
    """Every rule that fails is named, as Zod reports every issue its refinement adds."""
    if failures:
        raise ValueError("; ".join(failures))


class JobPass(WireModel):
    number: Annotated[WireInt, Field(ge=1, le=JOB_PASS_MAX)]
    label: Literal[*JOB_PASS_LABELS]


class JobStage(WireModel):
    nullable: ClassVar[frozenset[str]] = frozenset({"total"})
    stage: Literal[*JOB_STAGES]
    unit: Literal[*PROGRESS_UNITS]
    done: SafeCount
    total: Optional[SafePositive]
    pass_: Optional[JobPass] = Field(default=None, alias="pass")

    @model_validator(mode="after")
    def _done_within_total(self) -> "JobStage":
        _refuse([M_DONE] if self.total is not None and self.done > self.total else [])
        return self


class JobProducer(WireModel):
    name: _pattern(PRODUCER_NAME_PATTERN)
    revision: SafePositive


class JobInputSpan(WireModel):
    first_frame_index: SafeCount
    frame_count: SafePositive
    start_sec: SpanSeconds
    end_sec: SpanSeconds

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> "JobInputSpan":
        _refuse([M_SPAN] if self.end_sec < self.start_sec else [])
        return self


def _instant(text: Optional[str]) -> Optional[datetime]:
    return None if text is None else datetime.fromisoformat(text.replace("Z", "+00:00"))


class UploadJob(WireModel):
    id: _pattern(JOB_ID_PATTERN)
    source_asset_sha256: _pattern(SHA256_HEX_PATTERN)
    kind: Literal[*JOB_KINDS]
    state: Literal[*UPLOAD_JOB_STATES]
    created_at_utc: UtcInstant = Field(alias="createdAtUTC")
    started_at_utc: Optional[UtcInstant] = Field(default=None, alias="startedAtUTC")
    finished_at_utc: Optional[UtcInstant] = Field(default=None, alias="finishedAtUTC")
    stages: Annotated[list[JobStage], Field(max_length=len(JOB_STAGES))]
    solution_id: Optional[_pattern(MEDIA_ID_PATTERNS["solution"])] = None
    error: Optional[str] = None
    producer: Optional[JobProducer] = None
    request: Optional[RestoreRequest] = None
    graph_id: Optional[_pattern(MEDIA_ID_PATTERNS["graph"])] = None
    attempt_id: Optional[_pattern(MEDIA_ID_PATTERNS["attempt"])] = None
    variant_id: Optional[_pattern(MEDIA_ID_PATTERNS["variant"])] = None
    input_span: Optional[JobInputSpan] = None
    cancel_requested_at_utc: Optional[UtcInstant] = Field(default=None, alias="cancelRequestedAtUTC")
    queue_ahead: Optional[SafeCount] = None

    @model_validator(mode="after")
    def _fits_its_state_and_kind(self) -> "UploadJob":
        failures: list[str] = []
        media = self.kind in MEDIA_JOB_KINDS
        row = JOB_STATE_TABLE[job_group(self.kind)][self.state]
        present = {field.alias or name: getattr(self, name) is not None
                   for name, field in type(self).model_fields.items() if not field.is_required()}
        present["workStages"] = any(stage.stage not in JOB_PRE_RUN_STAGES[self.kind] for stage in self.stages)
        started = self.started_at_utc is not None
        if row is None or not all(state_allows(rule, present[field], started) for field, rule in row["fields"].items()):
            failures.append(M_STATE)
        created, start, end = _instant(self.created_at_utc), _instant(self.started_at_utc), _instant(self.finished_at_utc)
        if (start is not None and start < created) or (end is not None and end < (start or created)):
            failures.append(M_TIMES)
        if media and self.request is not None and self.request.kind != self.kind:
            failures.append(M_REQUEST_KIND)
        order = list(JOB_STAGE_UNITS[self.kind])
        if any(stage.stage not in order for stage in self.stages):
            failures.append(M_STAGE_KIND)
        else:
            places = [order.index(stage.stage) for stage in self.stages]
            if any(places[i] <= places[i - 1] for i in range(1, len(places))):
                failures.append(M_STAGE_ORDER)
            if any(stage.unit not in JOB_STAGE_UNITS[self.kind][stage.stage] for stage in self.stages):
                failures.append(M_STAGE_UNIT)
        if media and any(stage.pass_ is not None for stage in self.stages):
            failures.append(M_PASS_KIND)
        cancel = _instant(self.cancel_requested_at_utc)
        if cancel is not None and start is not None and (cancel < start or (end is not None and cancel > end)):
            failures.append(M_CANCEL)
        allowed = None if row is None else row["errors"]
        if self.error is not None and allowed == "interrupted" and self.error != INTERRUPTED_SENTENCE:
            failures.append(M_INTERRUPTED)
        if self.error is not None and allowed == "failure":
            fixed = fills_any_template(self.error, FAILURE_SENTENCES) if media else self.error in SOLVE_FAILURE_SENTENCES
            if not fixed or self.error == INTERRUPTED_SENTENCE:
                failures.append(M_FAILURE)
        _refuse(failures)
        return self


# =============================================================================================
# Shared samples for the TypeScript tests (synthetic values only: dates in 2031, made-up hashes)
# =============================================================================================

PYTHON_NAMES = {
    "sourceAssetSha256": "source_asset_sha256", "createdAtUTC": "created_at_utc", "startedAtUTC": "started_at_utc",
    "finishedAtUTC": "finished_at_utc", "solutionId": "solution_id", "graphId": "graph_id", "attemptId": "attempt_id",
    "variantId": "variant_id", "inputSpan": "input_span", "cancelRequestedAtUTC": "cancel_requested_at_utc",
    "queueAhead": "queue_ahead", "firstFrameIndex": "first_frame_index", "frameCount": "frame_count",
    "startSec": "start_sec", "endSec": "end_sec", "exposureEv": "exposure_ev", "pass": "pass_",
}


class _Drop:
    pass


_DROP = _Drop()


def _with(base: dict, path: list, value: Any) -> dict:
    out = copy.deepcopy(base)
    target = out
    for key in path[:-1]:
        target = target[key]
    if value is _DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return out


def _object_paths(node: Any, path: tuple = ()) -> list[tuple]:
    found = [path] if isinstance(node, dict) else []
    children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in children:
        found += _object_paths(value, (*path, key))
    return found


def _at(node: Any, path: tuple) -> Any:
    for key in path:
        node = node[key]
    return node


def _fit_producer(record: dict[str, Any]) -> dict[str, Any]:
    """A sample as the producer rule has it: a job that started names its producer, one that never started none."""
    return record if "startedAtUTC" in record else {k: v for k, v in record.items() if k != "producer"}


def build_job_samples() -> dict[str, Any]:
    sha = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9"
    producer = {"name": "albedian.solve", "revision": 1}
    created, started, finished = "2031-05-02T19:20:00Z", "2031-05-02T19:20:01.250Z", "2031-05-02T19:20:41Z"
    solve_base = {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "kind": "solve", "createdAtUTC": created,
                  "producer": producer}
    solve_stages = [
        {"stage": "store", "unit": "bytes", "done": 52811776, "total": 52811776},
        {"stage": "decode", "unit": "frames", "done": 8, "total": 8},
        {"stage": "detect", "unit": "frames", "done": 8, "total": 8},
        {"stage": "solve", "unit": "candidates", "done": 1200, "total": 1200,
         "pass": {"number": 2, "label": "phone_photo_generic"}},
        {"stage": "verify", "unit": "steps", "done": 1, "total": 1},
    ]
    span = {"firstFrameIndex": 0, "frameCount": 8, "startSec": 0, "endSec": 3.5}
    solve = {
        "queued": {**solve_base, "state": "queued", "stages": [], "queueAhead": 2},
        "running": {**solve_base, "state": "running", "startedAtUTC": started, "stages": solve_stages[:2] + [
            {"stage": "detect", "unit": "frames", "done": 3, "total": None}], "inputSpan": span},
        "succeeded": {**solve_base, "state": "succeeded", "startedAtUTC": started, "finishedAtUTC": finished,
                      "stages": solve_stages, "solutionId": "sol_0a1b2c3d4e5f6071", "inputSpan": span},
        "failed": {**solve_base, "state": "failed", "startedAtUTC": started, "finishedAtUTC": "2031-05-02T19:20:02Z",
                   "error": "The decoder could not read the file.", "stages": []},
        "canceled": {**solve_base, "state": "canceled", "startedAtUTC": started, "finishedAtUTC": "2031-05-02T19:20:05Z",
                     "cancelRequestedAtUTC": "2031-05-02T19:20:04Z", "stages": solve_stages[:1]},
        "interrupted": {**solve_base, "state": "interrupted", "startedAtUTC": started, "finishedAtUTC": finished,
                        "error": INTERRUPTED_SENTENCE, "stages": solve_stages[:2]},
    }
    solve = {state: _fit_producer(record) for state, record in solve.items()}
    media_base = {"id": "job_7e6f5a4b3c2d1e0f9a8b7c6d", "sourceAssetSha256": sha, "kind": "restoration",
                  "createdAtUTC": created, "request": {"kind": "restoration", "exposureEv": 1.5},
                  "graphId": "graph_7e6f5a4b3c2d1e0f", "producer": {"name": "albedian.restore", "revision": 3}}
    media_stages = [{"stage": stage, "unit": units[0], "done": 36, "total": 36}
                    for stage, units in JOB_STAGE_UNITS["restoration"].items()]
    media = {
        "succeeded": {**media_base, "state": "succeeded", "startedAtUTC": started, "finishedAtUTC": finished,
                      "stages": media_stages, "attemptId": "att_7e6f5a4b3c2d1e0f", "variantId": "var_7e6f5a4b3c2d1e0f"},
        "rejected": {**media_base, "state": "rejected", "startedAtUTC": started, "finishedAtUTC": finished,
                     "stages": media_stages, "attemptId": "att_7e6f5a4b3c2d1e0f"},
        "failed": {**media_base, "state": "failed", "startedAtUTC": started, "finishedAtUTC": finished, "stages": media_stages[:1],
                   "attemptId": "att_7e6f5a4b3c2d1e0f", "error": "This kind of file is not supported yet: an interlaced clip."},
    }
    media = {state: _fit_producer(record) for state, record in media.items()}
    display = {**media_base, "kind": "display_copy", "request": {"kind": "display_copy"}, "state": "running",
               "startedAtUTC": started, "stages": [{"stage": "decode", "unit": "frames", "done": 1, "total": 1}],
               "cancelRequestedAtUTC": "2031-05-02T19:20:30Z"}
    # A job that started and names no producer: a fault, as every job that started records its producer
    unproduced = {key: value for key, value in solve["canceled"].items() if key != "producer"}
    # A restoration the server stopped, and jobs canceled before they started (only their creation's stages)
    interrupted_media = {**media_base, "state": "interrupted", "startedAtUTC": started, "finishedAtUTC": finished,
                         "error": INTERRUPTED_SENTENCE, "stages": media_stages[:2]}
    canceled_queued_solve = _fit_producer({**solve_base, "state": "canceled", "finishedAtUTC": "2031-05-02T19:20:00.300Z",
                                           "stages": solve_stages[:1]})
    canceled_queued_media = _fit_producer({**media_base, "state": "canceled", "finishedAtUTC": "2031-05-02T19:20:00.300Z",
                                           "stages": []})
    # A cancel asked of a running job stays when the server then stops, or the restoration's check refuses
    interrupted_after_cancel = {**solve["interrupted"], "cancelRequestedAtUTC": "2031-05-02T19:20:04Z"}
    rejected_after_cancel = {**media["rejected"], "cancelRequestedAtUTC": "2031-05-02T19:20:04Z"}
    valid = [*solve.values(), *media.values(), display, interrupted_media, canceled_queued_solve,
             canceled_queued_media, interrupted_after_cancel, rejected_after_cancel]

    invalid: list[dict[str, Any]] = []

    def bad(sample: dict, reason: str, message: Optional[str] = None) -> None:
        invalid.append({"schema": "UploadJob", "sample": sample, "reason": reason, "message": message})

    # One fault per refinement, with the message each side must give
    bad(_with(solve["succeeded"], ["stages", 1, "done"], 9), "more done than the total", M_DONE)
    bad(_with(_with(solve["succeeded"], ["inputSpan", "endSec"], 1.0), ["inputSpan", "startSec"], 2.0), "a span that ends before it starts", M_SPAN)
    bad(_with(solve["queued"], ["startedAtUTC"], started), "a queued job has not started", M_STATE)
    bad(unproduced, "a job that started records its producer", M_STATE)
    bad({**solve["queued"], "producer": producer}, "a job that never started has no producer", M_STATE)
    bad(_with(solve["running"], ["startedAtUTC"], _DROP), "a running job has started", M_STATE)
    bad(_with(solve["running"], ["finishedAtUTC"], finished), "a running job has not finished", M_STATE)
    bad(_with(solve["succeeded"], ["solutionId"], _DROP), "a succeeded solve names its solution", M_STATE)
    bad(_with(media["succeeded"], ["variantId"], _DROP), "a succeeded media job names its variant", M_STATE)
    bad(_with(media["succeeded"], ["attemptId"], _DROP), "a succeeded media job names its attempt", M_STATE)
    bad(_with(_with(solve["succeeded"], ["state"], "rejected"), ["solutionId"], _DROP), "only a media job is rejected", M_STATE)
    bad(_with(media["rejected"], ["variantId"], "var_7e6f5a4b3c2d1e0f"), "a rejected job made no variant", M_STATE)
    bad(_with(media["rejected"], ["attemptId"], _DROP), "a rejected job names its attempt", M_STATE)
    bad(_with(solve["failed"], ["error"], _DROP), "a failed job says why", M_STATE)
    bad(_with(solve["failed"], ["finishedAtUTC"], _DROP), "a failed job has finished", M_STATE)
    bad(_with(media["failed"], ["variantId"], "var_7e6f5a4b3c2d1e0f"), "a failed job made no variant", M_STATE)
    bad(_with(solve["canceled"], ["error"], "The decoder could not read the file."), "a canceled job gives no failure", M_STATE)
    bad(_with(solve["interrupted"], ["error"], _DROP), "an interrupted job says why", M_STATE)
    bad(_with(solve["interrupted"], ["solutionId"], "sol_0a1b2c3d4e5f6071"), "an interrupted job has no result", M_STATE)
    bad(_with(solve["succeeded"], ["error"], "The decoder could not read the file."), "a succeeded job gives no failure", M_STATE)
    bad(_with(solve["running"], ["startedAtUTC"], "2031-05-02T19:19:59Z"), "started before it was created", M_TIMES)
    bad(_with(solve["succeeded"], ["finishedAtUTC"], "2031-05-02T19:20:01Z"), "finished before it started", M_TIMES)
    bad(_with(solve["failed"], ["finishedAtUTC"], "2031-05-02T19:19:00Z"), "finished before it was created", M_TIMES)
    bad(_with(media["succeeded"], ["solutionId"], "sol_0a1b2c3d4e5f6071"), "a media job has no solution", M_STATE)
    bad(_with(media["succeeded"], ["inputSpan"], span), "a media job has no frame span", M_STATE)
    bad(_with(solve["queued"], ["graphId"], "graph_7e6f5a4b3c2d1e0f"), "a solve has no graph", M_STATE)
    bad(_with(solve["queued"], ["request"], {"kind": "restoration"}), "a solve has no request", M_STATE)
    bad(_with(display, ["request"], _DROP), "a media job names its request", M_STATE)
    bad(_with(display, ["graphId"], _DROP), "a media job names its graph", M_STATE)
    # The audit's cases: work counted on a job that never started, an interrupted job that never started, a result id
    # on a media state that has none, a variant on a solve, and a later pair of stages out of order
    bad(_with(solve["queued"], ["stages"], solve_stages), "work counted on a queued solve", M_STATE)
    bad(_with(solve["queued"], ["inputSpan"], span), "a frame span on a queued solve", M_STATE)
    bad(_with(_with(canceled_queued_media, ["stages"], media_stages[:1]), ["state"], "canceled"),
        "work counted on a media job canceled before it started", M_STATE)
    bad(_with(solve["failed"], ["startedAtUTC"], _DROP), "a failed solve that never started", M_STATE)
    bad(_with(canceled_queued_solve, ["stages"], solve_stages), "work counted on a solve canceled before it started", M_STATE)
    # The second reader's records (AUDIT J): a job canceled after it started holds its cancel time (REQ-JOB-4.2)
    bad(_with(solve["canceled"], ["cancelRequestedAtUTC"], _DROP), "canceled after it started, the fixture's solve without its cancel time", M_STATE)
    bad({**media_base, "state": "canceled", "startedAtUTC": started, "finishedAtUTC": finished, "stages": media_stages[:2]},
        "canceled after it started, a restoration with no cancel time", M_STATE)
    bad({**solve_base, "state": "canceled", "startedAtUTC": started, "finishedAtUTC": finished, "stages": solve_stages[:3],
         "inputSpan": span}, "canceled after it started, a solve with work and a span and no cancel time", M_STATE)
    bad(_with(solve["interrupted"], ["startedAtUTC"], _DROP), "an interrupted job that never started", M_STATE)
    bad(_with({**media_base, "state": "running", "startedAtUTC": started, "stages": media_stages[:1]}, ["attemptId"],
              "att_7e6f5a4b3c2d1e0f"), "an attempt on a running restoration", M_STATE)
    bad(_with(solve["succeeded"], ["variantId"], "var_7e6f5a4b3c2d1e0f"), "a variant on a solve", M_STATE)
    bad(_with(solve["succeeded"], ["stages"], [solve_stages[0], solve_stages[2], solve_stages[1]]),
        "a later pair of stages out of order", M_STAGE_ORDER)
    bad(_with(solve["failed"], ["error"], INTERRUPTED_SENTENCE), "a failed solve giving the interrupted sentence", M_FAILURE)
    bad(_with(media["failed"], ["error"], INTERRUPTED_SENTENCE), "a failed media job giving the interrupted sentence", M_FAILURE)
    bad(_with(display, ["request"], {"kind": "restoration"}), "the request is of another kind", M_REQUEST_KIND)
    bad(_with(display, ["stages", 0, "stage"], "measure"), "a stage of another kind", M_STAGE_KIND)
    bad(_with(solve["succeeded"], ["stages"], list(reversed(solve_stages))), "stages out of order", M_STAGE_ORDER)
    bad(_with(solve["succeeded"], ["stages"], solve_stages[:1] * 2), "a stage twice", M_STAGE_ORDER)
    bad(_with(solve["succeeded"], ["stages", 0, "unit"], "frames"), "a stage in a unit not its own", M_STAGE_UNIT)
    bad(_with(media["succeeded"], ["stages", 0, "pass"], {"number": 1, "label": "triangle"}), "a media stage with a pass", M_PASS_KIND)
    bad(_with(solve["running"], ["queueAhead"], 0), "a queue count on a running job", M_STATE)
    bad(_with(solve["queued"], ["cancelRequestedAtUTC"], created), "a cancel time on a queued job", M_STATE)
    bad(_with(solve["canceled"], ["cancelRequestedAtUTC"], "2031-05-02T19:20:01Z"), "a cancel before the start", M_CANCEL)
    bad(_with(solve["canceled"], ["cancelRequestedAtUTC"], "2031-05-02T19:20:06Z"), "a cancel after the finish", M_CANCEL)
    bad(_with(canceled_queued_solve, ["cancelRequestedAtUTC"], "2031-05-02T19:20:00.200Z"), "a cancel time on a job that never started", M_STATE)
    bad(_with(solve["failed"], ["error"], "The decoder could not read the file: something it printed"), "a tool's own words", M_FAILURE)
    bad(_with(solve["failed"], ["error"], "The file could not be decoded."), "a media sentence on a solve", M_FAILURE)
    bad(_with(media["failed"], ["error"], "The decoder could not read the file."), "a solve sentence on a media job", M_FAILURE)
    bad(_with(media["failed"], ["error"], "This kind of file is not supported yet: a gif."), "a format outside the list", M_FAILURE)
    bad(_with(solve["interrupted"], ["error"], "The decoder could not read the file."), "an interrupted job says the server stopped", M_INTERRUPTED)

    # Edge samples: each bound, pattern and closed list just inside (valid) and just outside (refused), from one table
    def nudge_down(value: float) -> float:
        return math.nextafter(value, -math.inf)

    def nudge_up(value: float) -> float:
        return math.nextafter(value, math.inf)

    hexes = "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a"
    edge_table: list[tuple[str, dict, list, list[Any], list[Any]]] = [
        # (what, base record, path, accepted values, refused values)
        ("job id length", solve["queued"], ["id"], ["job_" + hexes[:16], "job_" + hexes[:64]], ["job_" + hexes[:15], "job_" + hexes[:65], "job_" + "A" * 16]),
        ("sha256", solve["queued"], ["sourceAssetSha256"], [sha], [sha[:63], sha + "0", sha.upper()]),
        ("solution id", solve["succeeded"], ["solutionId"], ["sol_" + hexes[:16]], ["sol_" + hexes[:15], "sol_" + hexes[:17]]),
        ("graph id", display, ["graphId"], ["graph_" + hexes[:16]], ["graph_" + hexes[:15], "graph_" + hexes[:17]]),
        ("attempt id", media["rejected"], ["attemptId"], ["att_" + hexes[:16]], ["att_" + hexes[:15], "att_" + hexes[:17]]),
        ("variant id", media["succeeded"], ["variantId"], ["var_" + hexes[:16]], ["var_" + hexes[:15], "var_" + hexes[:17]]),
        ("producer name", solve["running"], ["producer", "name"], ["a", "a" + "b" * 79], ["", "a" + "b" * 80, "Albedian", "9a", "a-b"]),
        ("producer revision", solve["running"], ["producer", "revision"], [1, MAX_SAFE_INTEGER], [0, MAX_SAFE_INTEGER + 1]),
        ("stage done", solve["running"], ["stages", 2, "done"], [0, MAX_SAFE_INTEGER], [-1, MAX_SAFE_INTEGER + 1]),
        ("stage total", solve["running"], ["stages", 0, "total"], [52811776, MAX_SAFE_INTEGER], [MAX_SAFE_INTEGER + 1]),
        ("stage total low", _with(solve["queued"], ["stages"], [{"stage": "store", "unit": "bytes", "done": 0, "total": 1}]),
         ["stages", 0, "total"], [1], [0]),
        # The list's own bound (one place per stage name) is out of a valid record's reach: each stage appears once,
        # in its kind's order, and no kind has every stage. So its inside end is the longest kind's full list.
        ("stage list length", media["succeeded"], ["stages"], [media_stages],
         [media_stages + [{"stage": "store", "unit": "bytes", "done": 1, "total": 1}] * (len(JOB_STAGES) + 1 - len(media_stages))]),
        ("pass number", solve["succeeded"], ["stages", 3, "pass", "number"], [1, JOB_PASS_MAX], [0, JOB_PASS_MAX + 1]),
        ("pass label", solve["succeeded"], ["stages", 3, "pass", "label"], list(JOB_PASS_LABELS), ["triangle_", "Triangle", ""]),
        ("span first frame", solve["running"], ["inputSpan", "firstFrameIndex"], [0, MAX_SAFE_INTEGER], [-1, MAX_SAFE_INTEGER + 1]),
        ("span frame count", solve["running"], ["inputSpan", "frameCount"], [1, MAX_SAFE_INTEGER], [0, MAX_SAFE_INTEGER + 1]),
        ("span start", solve["running"], ["inputSpan", "startSec"], [0, 3.5, -0.0], [nudge_down(0.0)]),
        ("span start high", _with(solve["running"], ["inputSpan", "endSec"], float(JOB_SPAN_MAX_SEC)), ["inputSpan", "startSec"],
         [float(JOB_SPAN_MAX_SEC)], [nudge_up(float(JOB_SPAN_MAX_SEC))]),
        ("span end", solve["running"], ["inputSpan", "endSec"], [3.5, float(JOB_SPAN_MAX_SEC)], [nudge_up(float(JOB_SPAN_MAX_SEC))]),
        ("span end low", _with(solve["running"], ["inputSpan", "startSec"], 0.0), ["inputSpan", "endSec"], [0.0], [nudge_down(0.0)]),
        ("queue ahead", solve["queued"], ["queueAhead"], [0, MAX_SAFE_INTEGER], [-1, MAX_SAFE_INTEGER + 1]),
        ("kind", solve["queued"], ["kind"], ["solve"], ["Solve", "export", ""]),
        ("state", solve["queued"], ["state"], ["queued"], ["Queued", "paused", ""]),
        ("stage name", solve["running"], ["stages", 0, "stage"], ["store"], ["upload", "Store"]),
        ("stage unit", solve["running"], ["stages", 0, "unit"], ["bytes"], ["byte", "seconds"]),
        ("start at creation", solve["running"], ["startedAtUTC"], [created], ["2031-05-02T19:19:59.999Z", "2031-05-02T19:20:00+00:00"]),
        ("finish at start", solve["succeeded"], ["finishedAtUTC"], [started], ["2031-05-02T19:20:01.249Z", "2031-05-02T19:20:41+00:00"]),
        ("finish at creation", canceled_queued_solve, ["finishedAtUTC"], [created], ["2031-05-02T19:19:59.999Z"]),
        ("cancel within the run", solve["canceled"], ["cancelRequestedAtUTC"], [started, "2031-05-02T19:20:05Z"],
         ["2031-05-02T19:20:01.249Z", "2031-05-02T19:20:05.001Z", "2031-05-02T19:20:04+00:00"]),
        ("created", solve["queued"], ["createdAtUTC"], ["2031-05-02T19:20:00Z", "2028-02-29T00:00:00.000Z", "0001-01-01T00:00:00Z"],
         ["2031-02-29T00:00:00Z", "0000-01-01T00:00:00Z", "2031-05-02T19:20:00+00:00", "2031-05-02T19:20:00.12Z", "2031-05-02 19:20:00Z"]),
        ("solve failure", solve["failed"], ["error"], [x for x in SOLVE_FAILURE_SENTENCES if x != INTERRUPTED_SENTENCE],
         ["The decoder could not read the file", "", INTERRUPTED_SENTENCE]),
        ("media failure", media["failed"], ["error"], ["The file could not be decoded.", "This ffmpeg cannot do what this file needs: reading a tiled HEIC."],
         ["The file could not be decoded", "This ffmpeg cannot do what this file needs: ."]),
    ]
    edges: list[dict[str, Any]] = []
    for what, base, path, accepted, refused in edge_table:
        for value in accepted:
            edges.append({"what": what, "path": path, "sample": _with(base, path, value), "accepted": True})
        for value in refused:
            edges.append({"what": what, "path": path, "sample": _with(base, path, value), "accepted": False})

    # At every level of every record: an unknown key, and each key under its Python name, are refused
    keyed: list[dict[str, Any]] = []
    for index, record in enumerate(valid):
        for path in _object_paths(record):
            node = _at(record, path)
            keyed.append({"what": f"valid[{index}] {'/'.join(map(str, path)) or 'top'}: an unknown key",
                          "sample": _with(record, [*path, "unknownKey"], 1) if path else {**record, "unknownKey": 1}})
            for key in node:
                if key in PYTHON_NAMES:
                    renamed = copy.deepcopy(record)
                    target = _at(renamed, path)
                    target[PYTHON_NAMES[key]] = target.pop(key)
                    keyed.append({"what": f"valid[{index}] {'/'.join(map(str, path)) or 'top'}: {key} as {PYTHON_NAMES[key]}",
                                  "sample": renamed})

    # Every state of every kind against every field of the state table, present and absent, from two records: one that
    # holds only what the state requires and one that holds all it allows. The verdict is the table's
    def record_for(kind: str, state: str, presence: dict[str, bool]) -> dict[str, Any]:
        row = JOB_STATE_TABLE[job_group(kind)][state]
        stage_names = list(JOB_STAGE_UNITS[kind]) if presence["workStages"] else list(JOB_PRE_RUN_STAGES[kind])
        record: dict[str, Any] = {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "kind": kind, "state": state,
                                  "createdAtUTC": created,
                                  "stages": [{"stage": name, "unit": JOB_STAGE_UNITS[kind][name][0], "done": 1, "total": 1}
                                             for name in stage_names]}
        sentence = INTERRUPTED_SENTENCE if row is not None and row["errors"] == "interrupted" else (
            "The decoder could not read the file." if kind == "solve" else "The file could not be decoded.")
        values = {"startedAtUTC": started, "finishedAtUTC": finished, "error": sentence,
                  "solutionId": "sol_0a1b2c3d4e5f6071", "attemptId": "att_7e6f5a4b3c2d1e0f",
                  "variantId": "var_7e6f5a4b3c2d1e0f", "inputSpan": span, "cancelRequestedAtUTC": "2031-05-02T19:20:04Z",
                  "queueAhead": 0, "producer": producer,
                  "request": {"kind": "restoration", "exposureEv": 1.5} if kind != "display_copy" else {"kind": kind},
                  "graphId": "graph_7e6f5a4b3c2d1e0f"}
        for field in JOB_STATE_FIELDS:
            if field != "workStages" and presence[field]:
                record[field] = values[field]
        return record

    def table_verdict(kind: str, state: str, presence: dict[str, bool]) -> bool:
        row = JOB_STATE_TABLE[job_group(kind)][state]
        return row is not None and all(state_allows(rule, presence[f], presence["startedAtUTC"])
                                       for f, rule in row["fields"].items())

    grid: list[dict[str, Any]] = []
    for kind in JOB_KINDS:
        for state in UPLOAD_JOB_STATES:
            row = JOB_STATE_TABLE[job_group(kind)][state] or JOB_STATE_TABLE["solve"]["succeeded"]
            for base in ("least", "most"):
                held = {f: (rule == "required") if base == "least" else (rule != "forbidden") for f, rule in row["fields"].items()}
                for field in JOB_STATE_FIELDS:
                    for present in (True, False):
                        presence = {**held, field: present}
                        grid.append({"kind": kind, "state": state, "base": base, "field": field, "present": present,
                                     "sample": record_for(kind, state, presence),
                                     "accepted": table_verdict(kind, state, presence)})

    # Every key of every valid record dropped at every level: refused where the key is required, and where an
    # optional field's absence breaks the state table; otherwise taken
    def model_at(path: tuple) -> Any:
        model: Any = UploadJob
        for key in [k for k in path if not isinstance(k, int)]:
            field = next(f for n, f in model.model_fields.items() if (f.alias or n) == key)
            stack, model = [field.annotation], None
            while stack:
                kind_ = stack.pop()
                if isinstance(kind_, type) and hasattr(kind_, "model_fields"):
                    model = kind_
                    break
                stack.extend(getattr(kind_, "__args__", ()))
        return model

    dropped: list[dict[str, Any]] = []
    for index, record in enumerate(valid):
        for path in _object_paths(record):
            model = model_at(path)
            for key in list(_at(record, path)):
                field = next(f for n, f in model.model_fields.items() if (f.alias or n) == key)
                if field.is_required():
                    accepted = False
                elif path:
                    accepted = True
                else:
                    presence = {f: f in record and f != key for f in JOB_STATE_FIELDS}
                    names = [stage["stage"] for stage in record["stages"]]
                    presence["workStages"] = any(name not in JOB_PRE_RUN_STAGES[record["kind"]] for name in names)
                    accepted = table_verdict(record["kind"], record["state"], presence)
                dropped.append({"what": f"valid[{index}] {'/'.join(map(str, path)) or 'top'}: {key} dropped",
                                "sample": _with(record, [*path, key], _DROP), "accepted": accepted})

    return {
        "patterns": JOB_PATTERNS,
        "enums": {"kinds": list(JOB_KINDS), "states": list(UPLOAD_JOB_STATES), "stages": list(JOB_STAGES),
                  "units": list(PROGRESS_UNITS), "passLabels": list(JOB_PASS_LABELS),
                  "solveFailureSentences": list(SOLVE_FAILURE_SENTENCES)},
        "stageUnits": {kind: {stage: list(units) for stage, units in stages.items()} for kind, stages in JOB_STAGE_UNITS.items()},
        "limits": {"passMax": JOB_PASS_MAX, "spanMaxSec": JOB_SPAN_MAX_SEC},
        "stateFields": list(JOB_STATE_FIELDS),
        "stateTable": JOB_STATE_TABLE,
        "preRunStages": {kind: list(stages) for kind, stages in JOB_PRE_RUN_STAGES.items()},
        "messages": list(JOB_MESSAGES),
        "valid": valid,
        "invalid": invalid,
        "edges": edges,
        "keyed": keyed,
        "grid": grid,
        "dropped": dropped,
    }
