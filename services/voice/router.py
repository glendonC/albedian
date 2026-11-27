"""Voice routes: a spoken or typed command parsed to an action and checked (nothing here applies it), and a question
about a stored photo answered from its stored solution."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from pathlib import Path as FilePath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from services.contracts.voice import (
    VOICE_QUESTION_REPLY,
    FindingId,
    SolutionId,
    VoiceQuestionRequest,
    check_utterance_length,
)
from services.ingest.job_store import JobStore, Unreadable
from services.ingest.router import UNREADABLE_SOLUTION, get_ingest_store
from services.ingest.store import IngestStore
from services.samples.router import UNEXPECTED_SAMPLE_FILE, get_samples_root, kept_solution, sample_of_photo
from services.voice.answers import answer_question, noun_of
from services.voice.intent import ActionContext, check_voice_command

_SOLUTION_ID: TypeAdapter[str] = TypeAdapter(SolutionId)
_FINDING_ID: TypeAdapter[str] = TypeAdapter(FindingId)


class _VoiceRoute(APIRoute):
    """Refuse an invalid voice request without echoing its input back in the 422 body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                detail = [
                    {"type": err["type"], "loc": list(err["loc"]), "msg": err["msg"]}
                    for err in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})

        return handle


router = APIRouter(route_class=_VoiceRoute)


class VoiceCommandRequest(BaseModel):
    """Request payload for `POST /api/voice/command`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    utterance: str
    context: dict[str, Any] = Field(default_factory=dict)
    proposed_action: dict[str, Any] | None = Field(default=None, alias="proposedAction")

    @field_validator("utterance")
    @classmethod
    def _cap_utterance(cls, value: str) -> str:
        return check_utterance_length(value)

    @field_validator("context")
    @classmethod
    def _validate_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        # A field of the wrong type is refused here, as a 422 that echoes nothing, not met later as a 500
        ActionContext.from_dict(value)
        return value


def _read_checked_solution(store: IngestStore, sha256: str) -> dict[str, Any] | None:
    """The stored ObservationSolution for `sha256`, checked by the contract's parser and the answer contract's id
    patterns, or None when no solution is stored."""
    try:
        solution = JobStore(store).read_solution(sha256)
        if solution is not None:
            _SOLUTION_ID.validate_python(solution["id"])
            for finding in solution["findings"]:
                _FINDING_ID.validate_python(finding["id"])
        return solution
    except (Unreadable, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=UNREADABLE_SOLUTION) from exc


def _checked_sample_solution(root: FilePath, sha256: str) -> tuple[bool, dict[str, Any] | None]:
    """For a sample night's photo (sample-voice-one): (True, its kept solve, by the setup's own rule and with the ids
    the answer contract takes), or (True, None) when no solve is kept here by that rule (not made, made by another
    revision, refused by the contract or named for another photo: sample-solution-producer-one); (False, None) when no
    sample has these bytes. 409 when the kept solve fails its hash or holds an id the answer contract refuses."""
    night = sample_of_photo(root, sha256)
    if night is None:
        return False, None
    solution = kept_solution(root, night)
    if solution is None:
        return True, None
    try:
        _SOLUTION_ID.validate_python(solution["id"])
        for finding in solution["findings"]:
            _FINDING_ID.validate_python(finding["id"])
    except (ValidationError, TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE) from exc
    return True, solution


@router.post("/api/voice/command")
def post_voice_command(req: VoiceCommandRequest) -> dict[str, Any]:
    """Parse natural-language voice intent and validate against session revision & epistemic gates."""
    return check_voice_command(req.utterance, req.context, req.proposed_action)


@router.post("/api/voice/answer/{sha256}")
def post_voice_answer(
    req: VoiceQuestionRequest,
    sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
    store: IngestStore = Depends(get_ingest_store),
    samples_root: FilePath = Depends(get_samples_root),
) -> dict[str, Any]:
    """Answer a question about a stored upload from its stored solution, or about the sample night from its kept solve
    (sample-voice-one): the words as heard and one VoiceAnswer. An upload of the sample's bytes answers from its own.
    Nothing here logs the words, and no model reads them."""
    record = store.read_record(sha256)
    if record is None or store.original(sha256) is None:
        is_sample, solution = _checked_sample_solution(samples_root, sha256)
        if not is_sample:
            raise HTTPException(status_code=404, detail="no file with that hash is stored")
        # A sample night is a photo
        noun = noun_of("image")
    else:
        solution = _read_checked_solution(store, sha256)
        # The recording named by its own noun, from the stored upload's media kind (C34)
        asset = record.get("sourceAsset")
        noun = noun_of(asset.get("mediaKind") if isinstance(asset, dict) else None)
    reply = {"heard": req.question,
             "answer": answer_question(req.question, solution, req.selected_finding_id, noun=noun)}
    VOICE_QUESTION_REPLY.validate_python(reply)
    return reply
