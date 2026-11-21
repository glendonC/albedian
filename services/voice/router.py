"""Voice routes: a spoken or typed command parsed to an action and checked (nothing here applies it), and a question
about a stored photo answered from its stored solution."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
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
from services.voice.answers import answer_question, noun_of
from services.voice.intent import check_voice_command

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


@router.post("/api/voice/command")
def post_voice_command(req: VoiceCommandRequest) -> dict[str, Any]:
    """Parse natural-language voice intent and validate against session revision & epistemic gates."""
    return check_voice_command(req.utterance, req.context, req.proposed_action)


@router.post("/api/voice/answer/{sha256}")
def post_voice_answer(
    req: VoiceQuestionRequest,
    sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
    store: IngestStore = Depends(get_ingest_store),
) -> dict[str, Any]:
    """Answer a question about a stored upload from its stored solution: the words as heard and one VoiceAnswer.
    Nothing here logs the words, and no model reads them."""
    if store.read_record(sha256) is None or store.original(sha256) is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    solution = _read_checked_solution(store, sha256)
    # The recording named by its own noun, from the stored upload's media kind (C34)
    asset = (store.read_record(sha256) or {}).get("sourceAsset")
    noun = noun_of(asset.get("mediaKind") if isinstance(asset, dict) else None)
    reply = {"heard": req.question,
             "answer": answer_question(req.question, solution, req.selected_finding_id, noun=noun)}
    VOICE_QUESTION_REPLY.validate_python(reply)
    return reply
