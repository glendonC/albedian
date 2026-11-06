"""Voice route: a spoken or typed command parsed to an action and checked; nothing here applies it."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.contracts.voice import check_utterance_length
from services.voice.intent import check_voice_command


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


@router.post("/api/voice/command")
def post_voice_command(req: VoiceCommandRequest) -> dict[str, Any]:
    """Parse natural-language voice intent and validate against session revision & epistemic gates."""
    return check_voice_command(req.utterance, req.context, req.proposed_action)
