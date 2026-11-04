"""Voice route: a spoken or typed command parsed to an action and validated against the view revision."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from services.voice.intent import parse_voice_intent, validate_and_execute_voice_command

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
    action = (
        req.proposed_action
        if req.proposed_action is not None
        else parse_voice_intent(req.utterance, req.context)
    )
    outcome = validate_and_execute_voice_command(
        utterance=req.utterance,
        context=req.context,
        proposed_action=action,
    )
    outcome["proposedAction"] = action
    return outcome
