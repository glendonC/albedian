"""Voice route: a spoken or typed command parsed to an action and checked; nothing here applies it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from services.voice.intent import check_voice_command

router = APIRouter()


class VoiceCommandRequest(BaseModel):
    """Request payload for `POST /api/voice/command`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    utterance: str
    context: dict[str, Any] = Field(default_factory=dict)
    proposed_action: dict[str, Any] | None = Field(default=None, alias="proposedAction")


@router.post("/api/voice/command")
def post_voice_command(req: VoiceCommandRequest) -> dict[str, Any]:
    """Parse natural-language voice intent and validate against session revision & epistemic gates."""
    return check_voice_command(req.utterance, req.context, req.proposed_action)
