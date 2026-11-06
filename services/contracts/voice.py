"""Voice command contracts: the Python mirror of packages/contracts/src/voice-command.ts, with the
`AlbedianActionSchema` and `ActionInterpretationSchema` it carries (packages/contracts/src/spatial-and-voice.ts).

What `POST /api/voice/command` answers. The backend holds no session and applies nothing: an action that
passes its checks comes back "proposed", with no state version, for the reducer to apply; a request that
forms no action, or an action the checks refuse, comes back as the reducer's "needs_input" or "rejected"
outcome, with no action in it. When an action fails AlbedianActionSchema, the refusal (`invalid_action`)
lists the issues as field paths and messages, never the payload (`action_issues`). `interpretation` names
who turned the words into the action, and is absent when the client sent its own action.

tests/fixtures/voice_contract.json is written from `build_voice_samples()` and parsed by
packages/contracts/src/__tests__/voice-command.test.ts, so a rule that changes on one side only fails a
test. Where Pydantic's defaults differ from Zod's, the models follow Zod:
- booleans are strict, and a literal true or false is a boolean (Pydantic's Literal[False] takes 0);
- a length bound above 1 counts UTF-16 code units, as JavaScript strings do;
- the question's "a character that is not a space" uses JavaScript's whitespace, not str.isspace;
- an integral float is an integer, since JavaScript has one number type; NaN and the infinities, which
  standard JSON cannot carry, are refused;
- optional means absent: an explicit null is refused;
- a loose z.object drops unknown keys, and the reply's strict objects refuse them;
- fields are read by their camelCase names only.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, Optional, Union, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic.alias_generators import to_camel

UTTERANCE_MAX_UTF16 = 2000
UTTERANCE_MAX_MESSAGE = "Utterance must be at most 2,000 characters."
QUESTION_MAX_UTF16 = 500
EPOCH_ISO_MIN_UTF16 = 4
EPOCH_MIN_YEAR_CE = -10000
EPOCH_MAX_YEAR_CE = 12000
FRACTION_MIN = 0.05
FRACTION_MAX = 1.0
FINDING_ACTION_TYPES = ("finding.select", "finding.describe", "space.enter")
# What /\s/ matches in JavaScript: ECMAScript's WhiteSpace (TAB, VT, FF, ZWNBSP and the Zs category) and
# LineTerminator (LF, CR, LS, PS) code points
JAVASCRIPT_SPACES = frozenset(
    "\t\v\f﻿              "
    "  　\n\r  "
)


def utf16_length(text: str) -> int:
    """A string's length as JavaScript counts it: UTF-16 code units, two for a character beyond U+FFFF."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def check_utterance_length(text: str) -> str:
    """Refuse an utterance past UTTERANCE_MAX_UTF16 UTF-16 code units with a plain message."""
    if utf16_length(text) > UTTERANCE_MAX_UTF16:
        raise ValueError(UTTERANCE_MAX_MESSAGE)
    return text


def _units(minimum: int, maximum: int | None = None) -> AfterValidator:
    """Zod's min and max on a string, which count UTF-16 code units."""

    def check(text: str) -> str:
        length = utf16_length(text)
        if length < minimum:
            raise ValueError(f"is {length} UTF-16 code units long, under the minimum of {minimum}")
        if maximum is not None and length > maximum:
            raise ValueError(f"is {length} UTF-16 code units long, over the maximum of {maximum}")
        return text

    return AfterValidator(check)


def _has_a_non_space(text: str) -> str:
    """Zod's regex(/\\S/): at least one character that is not a JavaScript space."""
    if all(char in JAVASCRIPT_SPACES for char in text):
        raise ValueError("needs at least one character that is not a space")
    return text


def _integral(value: Any) -> Any:
    """JavaScript has one number type, so Zod reads 4.0 as the integer 4."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _exactly(expected: bool) -> AfterValidator:
    def check(value: bool) -> bool:
        if value is not expected:
            raise ValueError(f"must be {str(expected).lower()}")
        return value

    return AfterValidator(check)


Text = Annotated[str, _units(1)]
Int = Annotated[int, BeforeValidator(_integral)]
Number = Annotated[float, Field(allow_inf_nan=False)]
TrueOnly = Annotated[bool, _exactly(True)]
FalseOnly = Annotated[bool, _exactly(False)]


class _Model(BaseModel):
    """A loose z.object: camelCase names only, strict types, unknown keys dropped, optional means absent."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _absent_not_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                key = field.alias or name
                if key in data and data[key] is None:
                    raise ValueError(f"{key} may be absent but not null")
        return data


class _Strict(_Model):
    """A z.object(...).strict(): an unknown key is refused."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="forbid")


class _Action(_Model):
    request_id: Text
    expected_view_revision: Annotated[Int, Field(ge=0)]


class RestoreConstraints(_Model):
    preserve_people: bool
    allow_synthetic_sky: FalseOnly
    protect_stars: bool


class MediaRestore(_Action):
    type: Literal["media.restore"]
    source_asset_id: Text
    profile: Literal["natural", "temporal", "star_protected"]
    constraints: RestoreConstraints


class MediaCompare(_Action):
    type: Literal["media.compare"]
    variant_a_id: Text
    variant_b_id: Text
    preserve_time: TrueOnly


class MediaSeek(_Action):
    type: Literal["media.seek"]
    source_time_sec: Annotated[Number, Field(ge=0)]
    preserve_audio_sync: TrueOnly


class SkyShow(_Action):
    type: Literal["sky.show"]
    mode: Literal["measured", "predicted"]
    observation_solution_id: Optional[str] = None


class FindingSelect(_Action):
    type: Literal["finding.select"]
    finding_id: Text


class FindingDescribe(_Action):
    type: Literal["finding.describe"]
    finding_id: Text
    asked: Annotated[
        list[Literal["distance", "type", "motion", "evidence", "behind_sightline"]], Field(min_length=1)
    ] = Field(alias="fields")


class SpaceEnter(_Action):
    type: Literal["space.enter"]
    finding_id: Text
    distance_policy: Literal["measured_or_estimated"]


class SpaceViewpoint(_Action):
    type: Literal["space.viewpoint"]
    target_finding_id: Text
    units: Literal["pc"]
    offset_parsec: Annotated[list[Number], Field(min_length=3, max_length=3)]
    trajectory: Literal["orbit", "translate"]


class SpaceEpoch(_Action):
    type: Literal["space.epoch"]
    epoch_iso: Annotated[str, _units(EPOCH_ISO_MIN_UTF16)] = Field(alias="epochISO")
    year_ce: Annotated[Int, Field(ge=EPOCH_MIN_YEAR_CE, le=EPOCH_MAX_YEAR_CE)] = Field(alias="yearCE")


class HistoryBack(_Action):
    type: Literal["history.back"]
    mode: Literal["checkpoint", "partial"]
    fraction: Optional[Annotated[Number, Field(ge=FRACTION_MIN, le=FRACTION_MAX)]] = None


class NavigationCancel(_Action):
    type: Literal["navigation.cancel"]
    transition_id: Optional[str] = None


class UiOverlay(_Action):
    type: Literal["ui.overlay"]
    overlay: Literal[
        "observedMarkers",
        "catalogMatchedLabels",
        "predictedEphemerides",
        "constellationLines",
        "earthDirectionVector",
        "residualVectors",
        "sightlineCone",
    ]
    enabled: bool


class SourceReturn(_Action):
    type: Literal["source.return"]
    checkpoint_id: Optional[str] = None
    restore_audio: TrueOnly


class SpaceRegime(_Action):
    type: Literal["space.regime"]
    regime: Literal["earth_capture", "cislunar", "solar_neighborhood", "deep_sky"]


class SceneShow(_Action):
    type: Literal["scene.show"]
    scene: Literal["cislunar", "black_hole", "supergiant", "saturn_horizon"]


class AudioSet(_Action):
    type: Literal["audio.set"]
    muted: Optional[bool] = None
    gain: Optional[Annotated[Number, Field(ge=0, le=1)]] = None

    @model_validator(mode="after")
    def _sets_something(self) -> AudioSet:
        if self.muted is None and self.gain is None:
            raise ValueError("audio.set needs muted, gain or both")
        return self


class QuestionAsk(_Action):
    type: Literal["question.ask"]
    question: Annotated[str, _units(1, QUESTION_MAX_UTF16), AfterValidator(_has_a_non_space)]
    finding_id: Optional[Text] = None


ACTION_MODELS = (
    MediaRestore, MediaCompare, MediaSeek, SkyShow, FindingSelect, FindingDescribe, SpaceEnter, SpaceViewpoint,
    SpaceEpoch, HistoryBack, NavigationCancel, UiOverlay, SourceReturn, SpaceRegime, SceneShow, AudioSet, QuestionAsk,
)
AlbedianAction = Annotated[
    Union[
        MediaRestore, MediaCompare, MediaSeek, SkyShow, FindingSelect, FindingDescribe, SpaceEnter, SpaceViewpoint,
        SpaceEpoch, HistoryBack, NavigationCancel, UiOverlay, SourceReturn, SpaceRegime, SceneShow, AudioSet,
        QuestionAsk,
    ],
    Field(discriminator="type"),
]
ACTION_TYPES: tuple[str, ...] = tuple(get_args(model.model_fields["type"].annotation)[0] for model in ACTION_MODELS)
_ACTION_MODELS_BY_TYPE: dict[str, type[_Action]] = dict(zip(ACTION_TYPES, ACTION_MODELS))


class RulesInterpretation(_Model):
    parser: Literal["rules"]


class ModelInterpretation(_Model):
    parser: Literal["model"]
    requested_model: Text
    served_by: Text


ActionInterpretation = Annotated[Union[RulesInterpretation, ModelInterpretation], Field(discriminator="parser")]


class Choice(_Model):
    id: Text
    label: Text


class ProposedVoiceReply(_Strict):
    """An action that passed the backend's checks, for the reducer to apply."""

    status: Literal["proposed"]
    action_id: Text
    proposed_action: AlbedianAction
    interpretation: Optional[ActionInterpretation] = None
    visual_status: Text
    # The catalog or ephemeris description of the target, for a finding action only
    scientific_explanation: Optional[Text] = None

    @model_validator(mode="after")
    def _names_and_describes_its_action(self) -> ProposedVoiceReply:
        if self.action_id != self.proposed_action.request_id:
            raise ValueError("a proposed reply names its action by the action's requestId")
        if self.scientific_explanation is not None and self.proposed_action.type not in FINDING_ACTION_TYPES:
            raise ValueError("only a finding action is described")
        return self


class ActionIssue(_Strict):
    """One way an action failed AlbedianActionSchema: where, and what was wrong. It never carries the value."""

    path: list[Union[str, Annotated[Int, Field(ge=0)]]]
    message: Text


def action_issues(action: Any) -> list[dict[str, Any]]:
    """How `action` fails AlbedianActionSchema, as the contract's issues; empty when it is a valid action.

    Each issue is a field path and a message. The action is validated against the model its type names, so
    Pydantic's union message, which quotes the type it was given, never appears, and no message here quotes
    a value from the action: the client keeps its own request.
    """
    if not isinstance(action, dict):
        return [{"path": [], "message": "An action is a JSON object"}]
    kind = action.get("type")
    model = _ACTION_MODELS_BY_TYPE.get(kind) if isinstance(kind, str) else None
    if model is None:
        return [{"path": ["type"], "message": "Not one of the contract's action types"}]
    try:
        model.model_validate(action)
    except ValidationError as error:
        return [
            {"path": list(issue["loc"]), "message": issue["msg"]}
            for issue in error.errors(include_url=False, include_context=False, include_input=False)
        ]
    return []


# A refusal or a question carries no action, so an action that failed the schema never reaches the client,
# which keeps its own request.
class NeedsInputVoiceReply(_Strict):
    status: Literal["needs_input"]
    action_id: Text
    interpretation: Optional[ActionInterpretation] = None
    question: Text
    choices: list[Choice]
    scientific_explanation: Optional[Text] = None


class RejectedVoiceReply(_Strict):
    status: Literal["rejected"]
    action_id: Text
    interpretation: Optional[ActionInterpretation] = None
    reason: Literal[
        "stale_revision",
        "unresolved_sky",
        "ambiguous_target",
        "no_distance_estimate",
        "invalid_epoch",
        "missing_source",
        "unsafe_synthetic_edit",
        "no_grounded_answer",
        "not_connected",
        "invalid_action",
    ]
    explanation: Text
    next_actions: list[str]
    scientific_explanation: Optional[Text] = None
    # For an invalid_action refusal only: how the action failed the schema
    issues: Optional[Annotated[list[ActionIssue], Field(min_length=1)]] = None

    @model_validator(mode="after")
    def _issues_only_for_invalid_actions(self) -> RejectedVoiceReply:
        if (self.reason == "invalid_action") != (self.issues is not None):
            raise ValueError("an invalid_action refusal lists its issues, and no other refusal does")
        return self


class VoiceCommandRequest(_Model):
    """What `POST /api/voice/command` receives: an utterance capped at 2,000 UTF-16 code units, an optional
    context object, and an optional client-formed proposedAction object for the backend to check."""

    utterance: Annotated[str, AfterValidator(check_utterance_length)]
    context: Optional[dict[str, Any]] = None
    proposed_action: Optional[dict[str, Any]] = None


REPLY_MODELS = (ProposedVoiceReply, NeedsInputVoiceReply, RejectedVoiceReply)
VoiceCommandResponse = Annotated[
    Union[ProposedVoiceReply, NeedsInputVoiceReply, RejectedVoiceReply], Field(discriminator="status")
]
REPLY_STATUSES: tuple[str, ...] = tuple(get_args(model.model_fields["status"].annotation)[0] for model in REPLY_MODELS)
REJECTION_REASONS: tuple[str, ...] = get_args(RejectedVoiceReply.model_fields["reason"].annotation)

ALBEDIAN_ACTION: TypeAdapter[Any] = TypeAdapter(AlbedianAction)
VOICE_COMMAND_REQUEST: TypeAdapter[Any] = TypeAdapter(VoiceCommandRequest)
VOICE_COMMAND_RESPONSE: TypeAdapter[Any] = TypeAdapter(VoiceCommandResponse)


class _Drop:
    """Marks a key `_with` removes."""


_DROP = _Drop()


def _with(sample: dict[str, Any], path: list[Any], value: Any) -> dict[str, Any]:
    """A deep copy of `sample` with the value at `path` replaced, or removed when `value` is `_DROP`."""
    result = copy.deepcopy(sample)
    node: Any = result
    for key in path[:-1]:
        node = node[key]
    if isinstance(value, _Drop):
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return result


def build_voice_samples() -> dict[str, Any]:
    """Valid, invalid and stripped samples of both schemas, for the fixture both sides parse.

    Every value is made up and points at no real person, place, time or file.
    """
    base = {"requestId": "req-sample-1", "expectedViewRevision": 3}

    def act(action_type: str, **fields: Any) -> dict[str, Any]:
        return {"type": action_type, **base, **fields}

    restore = act(
        "media.restore",
        sourceAssetId="src_sample",
        profile="star_protected",
        constraints={"preservePeople": True, "allowSyntheticSky": False, "protectStars": True},
    )
    compare = act("media.compare", variantAId="var-sample-a", variantBId="var-sample-b", preserveTime=True)
    seek = act("media.seek", sourceTimeSec=0, preserveAudioSync=True)
    sky = act("sky.show", mode="measured")
    select = act("finding.select", findingId="finding-star-vega")
    describe = act(
        "finding.describe",
        findingId="finding-predicted-saturn",
        fields=["distance", "type", "motion", "evidence", "behind_sightline"],
    )
    enter = act("space.enter", findingId="finding-star-vega", distancePolicy="measured_or_estimated")
    viewpoint = act(
        "space.viewpoint", targetFindingId="finding-star-vega", units="pc", offsetParsec=[0.5, -1.25, 2], trajectory="orbit"
    )
    epoch = act("space.epoch", epochISO="1500-01-01T00:00:00Z", yearCE=1500)
    back = act("history.back", mode="partial", fraction=FRACTION_MIN)
    cancel = act("navigation.cancel")
    named_cancel = act("navigation.cancel", transitionId="")
    overlay = act("ui.overlay", overlay="constellationLines", enabled=False)
    source = act("source.return", restoreAudio=True)
    regime = act("space.regime", regime="deep_sky")
    scene = act("scene.show", scene="saturn_horizon")
    mute = act("audio.set", muted=True)
    silence = act("audio.set", gain=0)
    question = act("question.ask", question="how old is that light?")
    actions = [
        restore, compare, seek, sky,
        act("sky.show", mode="predicted", observationSolutionId="sol-sample"),
        select, describe, enter, viewpoint, epoch,
        act("space.epoch", epochISO="-10000-01-01T00:00:00Z", yearCE=EPOCH_MIN_YEAR_CE),
        # An integral float is an integer, as it is to JavaScript
        act("space.epoch", epochISO="12000-01-01T00:00:00Z", yearCE=float(EPOCH_MAX_YEAR_CE)),
        # Four UTF-16 code units in two characters: Zod's min(4) counts units
        act("space.epoch", epochISO="\U0001f52d\U0001f52d", yearCE=1),
        act("history.back", mode="checkpoint"), back,
        act("history.back", mode="partial", fraction=FRACTION_MAX),
        cancel, named_cancel, overlay, source,
        act("source.return", checkpointId="cp-sample", restoreAudio=True),
        regime, scene, mute, silence,
        act("audio.set", muted=False, gain=1),
        question,
        act("question.ask", question="a" * QUESTION_MAX_UTF16, findingId="finding-obs-1"),
        # 250 characters, 500 UTF-16 code units
        act("question.ask", question="\U0001f52d" * (QUESTION_MAX_UTF16 // 2)),
        # A space to Python's str.isspace, not to JavaScript
        act("question.ask", question="\x1c"),
    ]

    def proposed(action: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "status": "proposed",
            "actionId": action["requestId"],
            "proposedAction": action,
            "visualStatus": f"Checked {action['type']}; not applied",
            **extra,
        }

    rules = {"parser": "rules"}
    model = {"parser": "model", "requestedModel": "claude-opus-5-5", "servedBy": "claude-opus-5-5"}
    fallback = {"parser": "model", "requestedModel": "claude-opus-5-5", "servedBy": "claude-opus-4-8"}
    needs_input = {
        "status": "needs_input",
        "actionId": "req-sample-1",
        "interpretation": rules,
        "question": "Multiple celestial objects overlap that pointer region. Which one did you mean?",
        "choices": [{"id": "finding-obs-1", "label": "Finding-obs-1"}, {"id": "finding-obs-2", "label": "Finding-obs-2"}],
        "scientificExplanation": "Deictic pointer region overlaps multiple catalog objects; disambiguation required.",
    }
    refusal = {"status": "rejected", "actionId": "req-sample-1", "explanation": "A made-up explanation.", "nextActions": []}
    stale = {
        **refusal,
        "reason": "stale_revision",
        "explanation": "Action expected viewRevision 2, but current session is at revision 3.",
        "nextActions": ["Refresh context and retry command"],
        "scientificExplanation": "Stale viewRevision rejected to preserve state machine determinism.",
    }
    # Issues as action_issues finds them, for an unknown type and for a describe with nothing to describe
    unknown_type = action_issues({"type": "media.delete", "requestId": "req-sample-1", "expectedViewRevision": 3})
    nothing_asked = action_issues(_with(describe, ["fields"], []))
    rejections = [
        stale,
        {**refusal, "reason": "unresolved_sky", "interpretation": model},
        {**refusal, "reason": "ambiguous_target", "interpretation": rules,
         "nextActions": ["Name a catalog star or planet, or point at one"]},
        {**refusal, "reason": "no_distance_estimate", "interpretation": fallback},
        {**refusal, "reason": "invalid_epoch", "interpretation": rules},
        {**refusal, "reason": "missing_source"},
        {**refusal, "reason": "unsafe_synthetic_edit", "interpretation": rules,
         "scientificExplanation": "Albedian never adds synthetic stars to a restoration."},
        {**refusal, "reason": "no_grounded_answer", "interpretation": rules},
        {**refusal, "reason": "not_connected"},
        {**refusal, "reason": "invalid_action", "issues": unknown_type},
        {**refusal, "reason": "invalid_action", "interpretation": model, "issues": nothing_asked},
    ]
    replies = [
        proposed(describe, interpretation=rules, scientificExplanation=(
            "Saturn's position depends on when and where you are observing, and no observation time and "
            "location are set yet."
        )),
        proposed(enter, interpretation=model, scientificExplanation="A made-up description of a catalog star."),
        proposed(select, interpretation=fallback),
        proposed(sky, interpretation=rules),
        # The client's own action: nobody here interpreted it
        proposed(cancel),
        needs_input,
        {"status": "needs_input", "actionId": "req-sample-2", "question": "Which one?", "choices": []},
        *rejections,
    ]

    requests = [
        {"utterance": "take me to Vega"},
        {"utterance": ""},
        {"utterance": "a" * UTTERANCE_MAX_UTF16, "context": {"viewRevision": 3}},
        # 1,000 astral characters = 2,000 UTF-16 code units
        {"utterance": "\U0001f52d" * (UTTERANCE_MAX_UTF16 // 2), "proposedAction": select},
        {"utterance": "do it", "context": {"viewRevision": 3}, "proposedAction": {"type": "media.delete"}},
    ]

    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: Any, reason: str) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason})

    action, request, reply = "AlbedianAction", "VoiceCommandRequest", "VoiceCommandResponse"
    bad(request, {"utterance": "a" * (UTTERANCE_MAX_UTF16 + 1)}, "2,001 characters")
    bad(request, {"utterance": "a" * (UTTERANCE_MAX_UTF16 - 1) + "\U0001f52d"},
        "2,000 characters but 2,001 UTF-16 code units")
    bad(request, {}, "no utterance")
    bad(request, {"utterance": None}, "a null utterance")
    bad(request, {"utterance": 42}, "an utterance as a number")
    bad(request, {"utterance": "ok", "context": None}, "an optional context is absent, never null")
    bad(request, {"utterance": "ok", "context": ["not", "an", "object"]}, "a context is an object")
    bad(request, {"utterance": "ok", "proposedAction": None}, "an optional proposedAction is absent, never null")
    bad(request, {"utterance": "ok", "proposedAction": "finding.select"}, "a proposedAction is an object")
    bad(action, _with(restore, ["constraints", "allowSyntheticSky"], True), "a restoration never allows a synthetic sky")
    bad(action, _with(restore, ["constraints", "allowSyntheticSky"], 0), "0 is not false")
    bad(action, _with(restore, ["profile"], "dramatic"), "an unknown profile")
    bad(action, _with(compare, ["preserveTime"], 1), "1 is not true")
    bad(action, _with(seek, ["sourceTimeSec"], -0.5), "a negative time")
    bad(action, _with(sky, ["mode"], "imagined"), "an unknown sky mode")
    bad(action, _with(sky, ["observationSolutionId"], None), "an optional field is absent, never null")
    bad(action, _with(select, ["findingId"], ""), "an empty finding id")
    bad(action, _with(describe, ["fields"], []), "nothing to describe")
    bad(action, _with(describe, ["fields"], ["colour"]), "an unknown field to describe")
    bad(action, _with(enter, ["distancePolicy"], "guessed"), "an unknown distance policy")
    bad(action, _with(viewpoint, ["offsetParsec"], [1, 2]), "an offset with two components")
    bad(action, _with(viewpoint, ["offsetParsec"], [1, 2, 3, 4]), "an offset with four components")
    bad(action, _with(viewpoint, ["units"], "ly"), "an offset not in parsecs")
    bad(action, _with(epoch, ["yearCE"], EPOCH_MAX_YEAR_CE + 1), "a year after the range")
    bad(action, _with(epoch, ["yearCE"], EPOCH_MIN_YEAR_CE - 1), "a year before the range")
    bad(action, _with(epoch, ["yearCE"], 1500.5), "a fractional year")
    bad(action, _with(epoch, ["yearCE"], True), "a boolean is not a year")
    bad(action, _with(epoch, ["yearCE"], "1500"), "a year as text")
    bad(action, _with(epoch, ["yearCE"], _DROP), "no year")
    bad(action, _with(epoch, ["epochISO"], "\U0001f52d"), "two UTF-16 code units are fewer than four")
    bad(action, _with(epoch, ["epochISO"], "150"), "three characters are fewer than four")
    bad(action, _with(back, ["fraction"], 0.04), "a fraction below the range")
    bad(action, _with(back, ["fraction"], 1.01), "a fraction above the range")
    bad(action, _with(named_cancel, ["transitionId"], None), "a null transition id")
    bad(action, _with(overlay, ["overlay"], "auroraMap"), "an unknown overlay")
    bad(action, _with(overlay, ["enabled"], "yes"), "enabled as text")
    bad(action, _with(source, ["restoreAudio"], False), "a return that drops the audio")
    bad(action, _with(regime, ["regime"], "planetary_surface"), "a regime voice has no path into")
    bad(action, _with(scene, ["scene"], "deep_sky"), "a regime is not a scene")
    bad(action, _with(mute, ["muted"], _DROP), "audio.set that sets nothing")
    bad(action, _with(silence, ["gain"], 1.5), "a gain above 1")
    bad(action, _with(silence, ["gain"], -0.1), "a negative gain")
    bad(action, _with(mute, ["muted"], "true"), "muted as text")
    bad(action, _with(question, ["question"], ""), "an empty question")
    bad(action, _with(question, ["question"], " \t　﻿ "), "a question of JavaScript spaces only")
    bad(action, _with(question, ["question"], "a" * (QUESTION_MAX_UTF16 - 1) + "\U0001f52d"),
        "500 characters but 501 UTF-16 code units")
    bad(action, _with(question, ["question"], "a" * (QUESTION_MAX_UTF16 + 1)), "501 characters")
    bad(action, _with(question, ["findingId"], ""), "an empty finding id on a question")
    bad(action, _with(select, ["requestId"], ""), "an empty request id")
    bad(action, _with(select, ["expectedViewRevision"], -1), "a negative revision")
    bad(action, _with(select, ["expectedViewRevision"], 2.5), "a fractional revision")
    bad(action, _with(select, ["expectedViewRevision"], False), "a boolean revision")
    bad(action, _with(select, ["type"], "media.delete"), "an unknown action type")
    bad(action, _with(select, ["type"], _DROP), "no action type")
    bad(action, _with(select, ["requestId"], _DROP), "no request id")
    bad(action, {"type": "finding.select", "request_id": "req-sample-1", "expected_view_revision": 3,
                 "finding_id": "finding-star-vega"}, "snake_case names are not the contract's")

    described = replies[0]
    bad(reply, {**described, "newStateVersion": 4}, "the backend applies nothing, so it has no state version")
    bad(reply, {**described, "settledStateVersion": 4}, "nor a settled one")
    bad(reply, {**_with(described, ["status"], "completed"), "newStateVersion": 4}, "the backend never reports completed")
    bad(reply, {**_with(described, ["status"], "canceled"), "settledStateVersion": 4}, "the backend never reports canceled")
    bad(reply, _with(replies[3], ["scientificExplanation"], "The sky, described."), "only a finding action is described")
    bad(reply, _with(described, ["actionId"], "req-other"), "a proposed reply names its action by requestId")
    bad(reply, _with(described, ["proposedAction", "fields"], []), "a proposed action is a valid action")
    bad(reply, _with(described, ["proposedAction"], _DROP), "a proposed reply carries its action")
    bad(reply, _with(described, ["interpretation"], None), "an interpretation is absent, never null")
    bad(reply, _with(described, ["interpretation"], {"parser": "model", "requestedModel": "claude-opus-5-5"}),
        "a model interpretation names both models")
    bad(reply, _with(described, ["interpretation"], {"parser": "guess"}), "an unknown parser")
    bad(reply, {**described, "spokenFeedback": "Done."}, "an unknown key on a strict reply")
    bad(reply, _with(described, ["visualStatus"], ""), "an empty status line")
    bad(reply, _with(described, ["scientificExplanation"], ""), "an empty description")
    bad(reply, _with(described, ["status"], _DROP), "no status")
    bad(reply, _with(stale, ["reason"], "not_wired"), "an unknown reason")
    bad(reply, _with(stale, ["nextActions"], _DROP), "a refusal lists its next actions, even none")
    bad(reply, _with(stale, ["explanation"], ""), "a refusal says why")
    bad(reply, {**stale, "newStateVersion": 4}, "a refusal has no state version either")
    bad(reply, {**stale, "proposedAction": {**source, "expectedViewRevision": 2}}, "a refusal carries no action")
    bad(reply, {**needs_input, "proposedAction": select}, "a question carries no action")
    invalid_action = rejections[-2]
    bad(reply, _with(invalid_action, ["issues"], _DROP), "an invalid_action refusal lists its issues")
    bad(reply, {**stale, "issues": unknown_type}, "only an invalid_action refusal lists issues")
    bad(reply, _with(invalid_action, ["issues"], []), "an invalid_action refusal lists at least one issue")
    bad(reply, _with(invalid_action, ["issues", 0, "message"], ""), "an issue says what was wrong")
    bad(reply, _with(invalid_action, ["issues", 0, "path"], "type"), "a path is a list")
    bad(reply, _with(invalid_action, ["issues", 0, "path"], [-1]), "a negative index")
    bad(reply, _with(invalid_action, ["issues", 0, "path"], [1.5]), "a fractional index")
    bad(reply, _with(invalid_action, ["issues", 0, "path"], [True]), "a boolean in a path")
    bad(reply, _with(invalid_action, ["issues", 0, "input"], "media.delete"), "an issue never carries the value")
    bad(reply, _with(needs_input, ["choices"], _DROP), "a question offers its choices, even none")
    bad(reply, _with(needs_input, ["choices", 0, "label"], ""), "an empty choice label")
    bad(reply, _with(needs_input, ["question"], ""), "an empty question")
    bad(reply, {**needs_input, "visualStatus": "Waiting"}, "an unknown key on a strict question")

    stripped = [
        {"schema": action, "sample": {**select, "colour": "blue", "note": None}, "parsed": select},
        {"schema": action, "sample": _with(restore, ["constraints", "brightness"], 2), "parsed": restore},
        {"schema": request, "sample": {"utterance": "stop", "extraKey": True}, "parsed": {"utterance": "stop"}},
        {
            "schema": reply,
            "sample": proposed({**select, "extra": True}, interpretation={**rules, "confidence": 0.9}),
            "parsed": proposed(select, interpretation=rules),
        },
    ]

    return {
        "enums": {
            "replyStatuses": list(REPLY_STATUSES),
            "rejectionReasons": list(REJECTION_REASONS),
            "actionTypes": list(ACTION_TYPES),
            "findingActionTypes": list(FINDING_ACTION_TYPES),
        },
        "limits": {
            "utteranceMaxUtf16": UTTERANCE_MAX_UTF16,
            "utteranceMaxMessage": UTTERANCE_MAX_MESSAGE,
            "questionMaxUtf16": QUESTION_MAX_UTF16,
            "epochIsoMinUtf16": EPOCH_ISO_MIN_UTF16,
            "epochMinYearCE": EPOCH_MIN_YEAR_CE,
            "epochMaxYearCE": EPOCH_MAX_YEAR_CE,
            "fractionMin": FRACTION_MIN,
            "fractionMax": FRACTION_MAX,
        },
        "valid": {"AlbedianAction": actions, "VoiceCommandRequest": requests, "VoiceCommandResponse": replies},
        "invalid": invalid,
        "stripped": stripped,
    }
