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

from bisect import bisect_right
import copy
import hashlib
import json
import math
from pathlib import Path
import re
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

from services.contracts.render_layer import EvidenceClass

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


def _nonzero_year(year: int) -> int:
    """Historical counting skips year 0: 1 BCE (-1) is followed by 1 CE (1)."""
    if year == 0:
        raise ValueError("There is no year 0: 1 BCE is followed by 1 CE.")
    return year


def _exactly(expected: bool) -> AfterValidator:
    def check(value: bool) -> bool:
        if value is not expected:
            raise ValueError(f"must be {'true' if expected else 'false'}")
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
    year_ce: Annotated[
        Int, Field(ge=EPOCH_MIN_YEAR_CE, le=EPOCH_MAX_YEAR_CE), AfterValidator(_nonzero_year)
    ] = Field(alias="yearCE")


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


# Why a request has no target, as a kind a screen or a test reads without reading English: the mirror of
# REFUSAL_KINDS in packages/contracts/src/voice-command.ts. Each kind names the rejection reasons it goes out under,
# whether it carries `said` (the person's own words as read, in lower case), `name` (the name of the app's tables they
# stand for, which the sentence shows) and `catalogRow`, and whether the backend can know it:
# the backend holds no photo, so the kinds that need the photo's sky are the app parser's alone and a voice reply
# never carries one. The sentence shown is made from the refusal by `refusal_sentence`, on both sides.
REFUSAL_KINDS: dict[str, dict[str, Any]] = {
    # A word no list holds: it may be a name the parser does not know
    "not_understood": {"reasons": ["ambiguous_target"], "said": True, "name": False, "catalogRow": False, "backend": True},
    # A star or body the name tables know, not in this photo's sky
    "not_in_this_sky": {"reasons": ["ambiguous_target"], "said": True, "name": True, "catalogRow": False, "backend": False},
    # A deep-sky object a catalogue holds, which voice cannot reach yet
    "deep_sky_out_of_reach": {"reasons": ["no_grounded_answer"], "said": True, "name": True, "catalogRow": True, "backend": True},
    # A sky name no catalogue of the app holds by name
    "no_named_row": {"reasons": ["no_grounded_answer"], "said": True, "name": True, "catalogRow": False, "backend": True},
    # The Southern Cross, none of whose stars this sky holds
    "southern_cross_not_in_this_sky": {"reasons": ["ambiguous_target"], "said": False, "name": False, "catalogRow": False, "backend": False},
    # A thing described but not named, so it could be any of several
    "which_one": {"reasons": ["ambiguous_target"], "said": False, "name": False, "catalogRow": False, "backend": True},
    # A pointing word, or a command said alone, with nothing selected, focused or pointed at
    "nothing_in_view": {"reasons": ["ambiguous_target"], "said": False, "name": False, "catalogRow": False, "backend": True},
    # A name asked for in a sky not worked out, so nothing in it has a name yet
    "sky_not_worked_out": {"reasons": ["ambiguous_target", "unresolved_sky"], "said": False, "name": False, "catalogRow": False, "backend": False},
    # Nothing in view, in a sky the view marks unresolved
    "nothing_in_view_sky_not_worked_out": {"reasons": ["unresolved_sky"], "said": False, "name": False, "catalogRow": False, "backend": False},
    # A sentence that changes the scene with a word its rule does not account for: no action (answers-3b v4)
    "not_a_request": {"reasons": ["not_one_request"], "said": True, "name": False, "catalogRow": False, "backend": True},
    # A sentence that holds two requests that change the scene: neither is done
    "two_requests": {"reasons": ["not_one_request"], "said": False, "name": False, "catalogRow": False, "backend": True},
}
# [proposed] What a refusal quotes back: at most six words and 80 characters with " ..." (services/voice/names.py
# MAX_QUOTED_*), so 80 astral characters and the cut mark fit. Counted in UTF-16 code units.
REFUSAL_SAID_MAX_UTF16 = 200
CATALOG_ROW_MAX_UTF16 = 32
# [proposed] A name of the app's tables, as long as any text of the answer contract may be
REFUSAL_NAME_MAX_UTF16 = 128
# An OpenNGC row name as the baked table writes it (NGC0292, ESO056-115, C099)
CATALOG_ROW_PATTERN = r"^[A-Za-z0-9]+(?:[- ][A-Za-z0-9]+)*$"
# The Southern Cross asterism's stars, as the refusal names them (services/voice/names.py SOUTHERN_CROSS_STARS)
SOUTHERN_CROSS_STAR_NAMES = ("Acrux", "Mimosa", "Gacrux", "Imai", "Ginan")
REFUSAL_MEDIA = ("photo", "video")
# The 55 code points whose simple upper-case mapping (UnicodeData.txt field 12) differs between Unicode 15.0.0, which
# CI's Python 3.12 uses, and 17.0.0, which Node 24 uses: read from the two pinned files in .cache/unicode/ucd (AUDIT J
# found them for answers-3). The fixture holds a name starting with each, refused on both sides
UNEVEN_CASE_CODE_POINTS = (
    0x019B, 0x0264, 0x1C8A, 0xA7CD, 0xA7CF, 0xA7D3, 0xA7D5, 0xA7DB,
    *range(0x10D70, 0x10D86),
    *range(0x16EBB, 0x16ED4),
)


def _wire_text(maximum: int, pattern: str | None = None) -> AfterValidator:
    """1 to `maximum` UTF-16 units, no lone surrogate, no control character, and the whole text matching `pattern`."""
    compiled = re.compile(pattern.removeprefix("^").removesuffix("$")) if pattern is not None else None

    def check(text: str) -> str:
        length = utf16_length(text)
        if not 1 <= length <= maximum:
            raise ValueError(f"is {length} UTF-16 code units long, outside 1 to {maximum}")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
            raise ValueError("holds a lone surrogate")
        if any(ord(char) <= 0x1F or 0x7F <= ord(char) <= 0x9F for char in text):
            raise ValueError("holds a control character")
        if compiled is not None and compiled.fullmatch(text) is None:
            raise ValueError(f"does not match {pattern}")
        return text

    return AfterValidator(check)


# What may stand between two letters of a name, as the name tables write names ("Eta Carinae", "Al Na'ir")
REFUSAL_NAME_SEPARATORS = (" ", "'")


def is_table_name(name: str) -> bool:
    """A name as the name tables write it: an ASCII letter or digit first, then letters and digits of the tracked
    letters table, with one space or apostrophe between two of them (the mirror of isTableName in voice-command.ts).
    The first character is ASCII so the sentence's capital is made by a fixed rule, the same in every runtime."""
    if not name or not ("a" <= name[0] <= "z" or "A" <= name[0] <= "Z" or "0" <= name[0] <= "9"):
        return False
    for index, char in enumerate(name):
        if char in REFUSAL_NAME_SEPARATORS:
            if index + 1 == len(name) or name[index + 1] in REFUSAL_NAME_SEPARATORS:
                return False
        elif not is_letter_or_digit(ord(char)):
            return False
    return True


def _table_name(name: str) -> str:
    if not is_table_name(name):
        raise ValueError("a name is written as the name tables write it")
    return name


class VoiceRefusal(_Strict):
    kind: Literal[tuple(REFUSAL_KINDS)]  # type: ignore[valid-type]
    said: Optional[Annotated[str, _wire_text(REFUSAL_SAID_MAX_UTF16)]] = None
    name: Optional[Annotated[str, _wire_text(REFUSAL_NAME_MAX_UTF16), AfterValidator(_table_name)]] = None
    catalog_row: Optional[Annotated[str, _wire_text(CATALOG_ROW_MAX_UTF16, CATALOG_ROW_PATTERN)]] = None

    @model_validator(mode="after")
    def _says_what_its_kind_says(self) -> VoiceRefusal:
        spec = REFUSAL_KINDS[self.kind]
        if spec["said"] != (self.said is not None):
            raise ValueError(f"a {self.kind} refusal {'quotes' if spec['said'] else 'quotes nothing'} said")
        if spec["name"] != (self.name is not None):
            raise ValueError(f"a {self.kind} refusal {'names' if spec['name'] else 'names no'} the name it stands for")
        if spec["catalogRow"] != (self.catalog_row is not None):
            raise ValueError(f"a {self.kind} refusal {'names' if spec['catalogRow'] else 'names no'} catalogue row")
        return self


def _capitalized(text: str) -> str:
    """`text` with an ASCII small letter first raised to its capital, and any other first character left as it is: a
    fixed rule, never this Python's case tables, so both languages make the same sentence."""
    return chr(ord(text[0]) - 0x20) + text[1:] if text and "a" <= text[0] <= "z" else text


def refusal_sentence(refusal: dict[str, Any], media: str = "photo") -> str:
    """The one sentence a refusal is shown as, from its kind, what was said, and the recording's media word."""
    kind, said, row = refusal["kind"], refusal.get("said", ""), refusal.get("catalogRow", "")
    name = refusal.get("name", "")
    if kind == "not_understood":
        # [proposed, for the UIUX work] It fits a name no list holds ("pluto") as well as a word: it asks for nothing
        return f'I did not understand "{said}": no list of the app holds that word.'
    if kind == "not_in_this_sky":
        return f"{_capitalized(name)} was not found in this {media}'s sky."
    if kind == "deep_sky_out_of_reach":
        return (
            f"{_capitalized(name)} is in the app's deep-sky catalogue (OpenNGC {row}), but voice does not reach "
            "deep-sky objects yet, so it cannot be selected, described or flown to."
        )
    if kind == "no_named_row":
        return f"{_capitalized(name)} has no named row in the app's catalogues yet, so it cannot be selected, described or flown to by name."
    if kind == "southern_cross_not_in_this_sky":
        return (
            "The Southern Cross is a constellation, not one star, and none of its stars "
            f"({', '.join(SOUTHERN_CROSS_STAR_NAMES)}) is in the sky solution."
        )
    if kind == "which_one":
        return 'That does not say which one. Say its name, or select it and say "that one".'
    if kind == "nothing_in_view":
        return "Nothing is selected or pointed at. Select a star or planet first, or say its name."
    if kind == "sky_not_worked_out":
        return f"The sky in this {media} has not been worked out yet, so nothing in it can be found by name."
    if kind == "nothing_in_view_sky_not_worked_out":
        return f"The sky in this {media} has not been worked out yet, and nothing is selected or pointed at."
    if kind == "not_a_request":
        # [proposed, for the UIUX work]
        return f'I did not take that as a request: "{said}" is not a word of one.'
    if kind == "two_requests":
        # [proposed, for the UIUX work]
        return "That asks for two things at once; say one at a time."
    raise ValueError(f"no sentence for a {kind} refusal")


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
        # The words are not one request: a word no rule of it accounts for, or two requests at once (answers-3b v4)
        "not_one_request",
    ]
    explanation: Text
    next_actions: list[str]
    scientific_explanation: Optional[Text] = None
    # For an invalid_action refusal only: how the action failed the schema
    issues: Optional[Annotated[list[ActionIssue], Field(min_length=1)]] = None
    # Why the request has no target: every ambiguous_target refusal says so, and its explanation is the sentence
    refusal: Optional[VoiceRefusal] = None

    @model_validator(mode="after")
    def _issues_only_for_invalid_actions(self) -> RejectedVoiceReply:
        if (self.reason == "invalid_action") != (self.issues is not None):
            raise ValueError("an invalid_action refusal lists its issues, and no other refusal does")
        refusal = self.refusal
        if refusal is None:
            if self.reason in ("ambiguous_target", "not_one_request"):
                raise ValueError(f"a {self.reason} refusal says which kind it is")
            return self
        spec = REFUSAL_KINDS[refusal.kind]
        if not spec["backend"]:
            raise ValueError(f"a {refusal.kind} refusal needs the photo's sky, which the backend does not hold")
        if self.reason not in spec["reasons"]:
            raise ValueError(f"a {refusal.kind} refusal goes out as {' or '.join(spec['reasons'])}")
        if self.explanation != refusal_sentence(refusal.model_dump(by_alias=True, exclude_none=True)):
            raise ValueError("a refusal's explanation is its sentence")
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
VOICE_REFUSAL: TypeAdapter[Any] = TypeAdapter(VoiceRefusal)


# Answers: the mirror of packages/contracts/src/voice-answer.ts. An answer is either "answered", one short plain
# sentence plus the statements behind it, or "unanswerable", a plain sentence saying why this photo's data cannot
# answer, with no statements. tests/fixtures/voice_answer_contract.json is written from `build_answer_samples()` and
# parsed by packages/contracts/src/__tests__/voice-answer.test.ts. The shapes only: nothing computes or sends an
# answer yet.
#
# A statement's class is never the caller's choice: the source decides it. Each source a statement may cite is a row
# of a table here (SOLUTION_FIELD_SOURCES, CATALOG_SOURCE_QUANTITIES, EPHEMERIS_METHODS) naming the quantity it holds,
# its stored unit and its class rule; the rule (CLASS_RULES) gives the one class a statement from that source has
# about that subject, or none. Every number lies in its quantity's range (QUANTITY_SPECS), intervals included; every
# text is bounded in UTF-16 units with no lone surrogate and no control character; every integer is safe. These are
# the lead's wire rules (.lead/briefs/WIRE_RULES.md), implemented here until the shared wire module lands on main.

# [proposed] One spoken sentence of at most 90 characters: the bound the lead approved for the language lane's
# spoken reply (.lead/STATE.md, 2026-11-09). Counted in UTF-16 code units.
ANSWER_SENTENCE_MAX_UTF16 = 90
# [proposed] The most UTF-16 units of any other text, the most statements in one answer, the highest finding index
ANSWER_TEXT_MAX_UTF16 = 128
ANSWER_STATEMENTS_MAX = 32
FINDING_INDEX_MAX = 99_999
SOURCE_PATH_MAX_DEPTH = 5
SOURCE_PATH_KEY_MAX_UTF16 = 64
# The largest integer both JSON readers hold exactly: 2^53 - 1
SAFE_INTEGER_MAX = 2**53 - 1
# The ids the backend mints: finding-obs-<n>, finding-<catalog star id>, finding-predicted-<body or star id>,
# finding-observed-moon, finding-openngc-<OpenNGC name>; sol-<name> and sol_<16 hex>
FINDING_ID_PATTERN = r"^finding-[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$"
PREDICTED_FINDING_ID_PATTERN = r"^finding-predicted-"
SOLUTION_ID_PATTERN = r"^sol[-_][A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$"
# The questions this repository's data can answer, from the survey of 2026-11-11 (section 3)
ANSWERABLE_QUESTION_KINDS = (
    "identity", "distance", "brightness", "colour_temperature", "constellation", "sky_position", "moon_phase",
    "photo_contents", "confidence",
)
# A question no data of this photo answers ("what is a light year?", "is that a plane?")
UNANSWERABLE_QUESTION_KIND = "unanswerable"
QUESTION_KINDS = (*ANSWERABLE_QUESTION_KINDS, UNANSWERABLE_QUESTION_KIND)
UNANSWERABLE_REASONS = (
    "not_found_in_this_sky", "photo_not_solved", "no_time_or_place", "not_in_the_data", "outside_the_data",
)
# The units sources store; a converted unit (light years, kilometres) is never stated
ANSWER_UNITS = ("pc", "au", "mag", "K", "deg", "arcsec", "px", "fraction", "count", "ratio")
EVIDENCE_CLASSES: tuple[str, ...] = get_args(EvidenceClass)
# The closed lists a text value may come from: the solution contract's object types and statuses and the Moon's
# phase names (services/science/radiometry.py)
OBJECT_TYPES = ("star", "planet", "moon", "deep_sky", "satellite_candidate")
SOLUTION_STATUSES = ("pending", "calibrated", "approximate", "predicted_only", "unresolved", "ambiguous")
MOON_PHASE_NAMES = (
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter",
    "Waning Crescent",
)


def _range(low: float | None, high: float | None, *, low_open: bool = False, high_open: bool = False) -> dict[str, Any]:
    """A number's range: None is no bound beyond being finite; an open end is excluded."""
    return {"min": low, "minOpen": low_open, "max": high, "maxOpen": high_open}


_DEGREES_AROUND = _range(0, 360, high_open=True)
_DEGREES_UP = _range(-90, 90)
_ABOVE_ZERO = _range(0, None, low_open=True)
_ZERO_OR_MORE = _range(0, None)
_COUNT = _range(0, SAFE_INTEGER_MAX)
# A magnitude and a colour index have no physical bound: only finite
_ANY_FINITE = _range(None, None)
# What a statement states: the kind of value it takes and, for a number, its range (both ends of an interval too)
QUANTITY_SPECS: dict[str, dict[str, Any]] = {
    "name": {"value": "text"},
    "object_type": {"value": "text", "values": list(OBJECT_TYPES)},
    "distance": {"value": "number", "range": _ABOVE_ZERO},
    "apparent_magnitude": {"value": "number", "range": _ANY_FINITE},
    "detection_snr": {"value": "number", "range": _ABOVE_ZERO},
    "colour_index_bp_rp": {"value": "number", "range": _ANY_FINITE},
    "effective_temperature": {"value": "number", "range": _ABOVE_ZERO},
    "spectral_type": {"value": "text"},
    "constellation": {"value": "text"},
    "altitude": {"value": "number", "range": _DEGREES_UP},
    "azimuth": {"value": "number", "range": _DEGREES_AROUND},
    "right_ascension": {"value": "number", "range": _DEGREES_AROUND},
    "declination": {"value": "number", "range": _DEGREES_UP},
    "position_uncertainty": {"value": "number", "range": _ZERO_OR_MORE},
    "illuminated_fraction": {"value": "number", "range": _range(0, 1)},
    "phase_angle": {"value": "number", "range": _range(0, 180)},
    "moon_phase_name": {"value": "text", "values": list(MOON_PHASE_NAMES)},
    "solution_status": {"value": "text", "values": list(SOLUTION_STATUSES)},
    "detected_source_count": {"value": "number", "range": _COUNT},
    "matched_star_count": {"value": "number", "range": _COUNT},
    "held_out_pair_count": {"value": "number", "range": _COUNT},
    "held_out_median_residual": {"value": "number", "range": _ZERO_OR_MORE},
    "pose_max_offset": {"value": "number", "range": _range(0, 180)},
}
# An answer to a question kind has at least one statement of one of these quantities
QUESTION_QUANTITIES: dict[str, list[str]] = {
    "identity": ["name", "object_type"],
    "distance": ["distance"],
    "brightness": ["apparent_magnitude", "detection_snr"],
    "colour_temperature": ["colour_index_bp_rp", "effective_temperature", "spectral_type"],
    "constellation": ["constellation"],
    "sky_position": ["altitude", "azimuth", "right_ascension", "declination"],
    "moon_phase": ["illuminated_fraction", "phase_angle", "moon_phase_name"],
    "photo_contents": ["name", "detected_source_count", "matched_star_count"],
    "confidence": [
        "solution_status", "matched_star_count", "held_out_pair_count", "held_out_median_residual",
        "pose_max_offset", "position_uncertainty",
    ],
}
# The one class a statement from a source of each rule has about each subject (a finding by its own class, an
# ephemeris body, the solution). A subject the rule does not list cannot be stated from that source.
CLASS_RULES: dict[str, dict[str, str]] = {
    # Measured from the photo's pixels: a detection's own fields
    "measured": {"observed": "observed", "catalog_matched": "observed"},
    # The finding's own sky position
    "own_class": {"observed": "observed", "catalog_matched": "catalog_matched", "predicted": "predicted", "simulated": "simulated"},
    # A catalog value, from the catalog or from its copy in the solution
    "catalog": {"catalog_matched": "catalog_matched", "predicted": "predicted", "simulated": "simulated"},
    # Computed by the ephemeris for the capture time and place
    "ephemeris": {"observed": "predicted", "catalog_matched": "predicted", "predicted": "predicted", "simulated": "simulated", "ephemeris_body": "predicted"},
    # What the solver measured of the photo as a whole
    "solve": {"solution": "observed"},
    # How far an approximate pose may be off, computed from stated assumptions, never checked against stars
    "pose": {"solution": "predicted"},
}


def _row(path: list[Any] | None, quantity: str, unit: str | None, rule: str) -> dict[str, Any]:
    return ({"path": path} if path is not None else {}) | {"quantity": quantity, "unit": unit, "rule": rule}


# The fields of the stored solution a statement may cite, each as its path with "*" for a finding's index
SOLUTION_FIELD_SOURCES: list[dict[str, Any]] = [
    _row(["status"], "solution_status", None, "solve"),
    _row(["quality", "detectedCandidateCount"], "detected_source_count", "count", "solve"),
    _row(["quality", "matchedInlierCount"], "matched_star_count", "count", "solve"),
    _row(["quality", "heldOutPairCount"], "held_out_pair_count", "count", "solve"),
    _row(["quality", "medianResidualPx"], "held_out_median_residual", "px", "solve"),
    _row(["quality", "medianResidualArcsec"], "held_out_median_residual", "arcsec", "solve"),
    _row(["poseEstimate", "maxCenterOffsetDeg"], "pose_max_offset", "deg", "pose"),
    _row(["findings", "*", "sourceDetection", "snr"], "detection_snr", "ratio", "measured"),
    _row(["findings", "*", "skyPosition", "raDeg"], "right_ascension", "deg", "own_class"),
    _row(["findings", "*", "skyPosition", "decDeg"], "declination", "deg", "own_class"),
    _row(["findings", "*", "skyPosition", "altDeg"], "altitude", "deg", "own_class"),
    _row(["findings", "*", "skyPosition", "azDeg"], "azimuth", "deg", "own_class"),
    _row(["findings", "*", "skyPosition", "uncertaintyArcsec"], "position_uncertainty", "arcsec", "own_class"),
    _row(["findings", "*", "catalogRef", "commonName"], "name", None, "catalog"),
    _row(["findings", "*", "catalogRef", "objectType"], "object_type", None, "catalog"),
    _row(["findings", "*", "catalogRef", "constellation"], "constellation", None, "catalog"),
    _row(["findings", "*", "catalogRef", "spectralType"], "spectral_type", None, "catalog"),
    _row(["findings", "*", "catalogRef", "apparentMagnitude"], "apparent_magnitude", "mag", "catalog"),
    _row(["findings", "*", "catalogRef", "colorIndexBpRp"], "colour_index_bp_rp", "mag", "catalog"),
    _row(["findings", "*", "catalogRef", "raDeg"], "right_ascension", "deg", "catalog"),
    _row(["findings", "*", "catalogRef", "decDeg"], "declination", "deg", "catalog"),
    _row(["findings", "*", "catalogRef", "bailerJonesDistancePc", "median"], "distance", "pc", "catalog"),
    _row(["findings", "*", "inferredDistancePc", "median"], "distance", "pc", "catalog"),
]
# The catalogs a row comes from (CatalogReference's providers), and the quantities a row holds
CATALOG_PROVIDERS = ("Gaia DR3", "Hipparcos", "JPL Horizons", "SIMBAD", "OpenNGC")
CATALOG_SOURCE_QUANTITIES: list[dict[str, Any]] = [
    _row(None, "name", None, "catalog"),
    _row(None, "object_type", None, "catalog"),
    _row(None, "constellation", None, "catalog"),
    _row(None, "spectral_type", None, "catalog"),
    _row(None, "apparent_magnitude", "mag", "catalog"),
    _row(None, "colour_index_bp_rp", "mag", "catalog"),
    _row(None, "effective_temperature", "K", "catalog"),
    _row(None, "right_ascension", "deg", "catalog"),
    _row(None, "declination", "deg", "catalog"),
    _row(None, "distance", "pc", "catalog"),
]
# The bodies the ephemeris computes (services/science/ephemeris.py), and its methods: the module's PLANET_METHOD,
# MOON_METHOD and SUN_METHOD, and radiometry.py's compute_lunar_phase_and_illumination
EPHEMERIS_BODIES = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")
_POSITION_ROWS = [
    _row(None, "altitude", "deg", "ephemeris"),
    _row(None, "azimuth", "deg", "ephemeris"),
    _row(None, "right_ascension", "deg", "ephemeris"),
    _row(None, "declination", "deg", "ephemeris"),
    _row(None, "distance", "au", "ephemeris"),
]
EPHEMERIS_METHODS: dict[str, dict[str, Any]] = {
    "planet": {"bodies": ["Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"], "rows": _POSITION_ROWS},
    "moon": {"bodies": ["Moon"], "rows": _POSITION_ROWS},
    "sun": {"bodies": ["Sun"], "rows": _POSITION_ROWS},
    "lunar_phase": {
        "bodies": ["Moon"],
        "rows": [
            _row(None, "illuminated_fraction", "fraction", "ephemeris"),
            _row(None, "phase_angle", "deg", "ephemeris"),
            _row(None, "moon_phase_name", None, "ephemeris"),
        ],
    },
}
# What an uncertainty means, as its source states it
UNCERTAINTY_MEANINGS = ("percentile_16_84", "error_bound", "one_sigma")
# The letters-and-digits table both contracts read (packages/contracts/src/data/letters-and-digits.json, written by
# scripts/generate_letters_and_digits.py, shipped with its notice on apps/web/public/notices/unicode/): categories L
# and N of one stated Unicode version, less Default_Ignorable_Code_Point. The table decides, never this Python's
# own unicodedata, so every runtime judges a sentence alike
LETTERS_AND_DIGITS_PATH = (
    Path(__file__).resolve().parents[2] / "packages" / "contracts" / "src" / "data" / "letters-and-digits.json"
)
_LETTERS_AND_DIGITS = json.loads(LETTERS_AND_DIGITS_PATH.read_text(encoding="utf-8"))
LETTERS_AND_DIGITS_UNICODE_VERSION: str = _LETTERS_AND_DIGITS["unicodeVersion"]
_LETTER_FIRSTS = [first for first, _last in _LETTERS_AND_DIGITS["ranges"]]
_LETTER_LASTS = [last for _first, last in _LETTERS_AND_DIGITS["ranges"]]


# The lower-case table both voice parsers read (packages/contracts/src/data/lower-case.json, written by
# scripts/generate_letters_and_digits.py): Unicode 15.1.0's simple lower-case mapping, one code point to one. A request
# is lowered by it, never by this Python's own case tables, so every runtime reads a request alike
LOWER_CASE_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "src" / "data" / "lower-case.json"
_LOWER_CASE_TABLE = json.loads(LOWER_CASE_PATH.read_text(encoding="utf-8"))
_LOWER_CASE: dict[int, int] = {upper: lower for upper, lower in _LOWER_CASE_TABLE["pairs"]}


def lower_case(text: str) -> str:
    """`text` with every code point the lower-case table maps replaced by its lower case, and every other kept."""
    return text.translate(_LOWER_CASE)


def is_letter_or_digit(code_point: int) -> bool:
    """Whether the table counts this code point as a letter or a digit."""
    index = bisect_right(_LETTER_FIRSTS, code_point) - 1
    return index >= 0 and code_point <= _LETTER_LASTS[index]

def _says_something(text: str) -> str:
    """At least one letter or digit of the table: it shows."""
    if not any(is_letter_or_digit(ord(char)) for char in text):
        raise ValueError("a sentence holds a letter or a digit")
    return text


AnswerText = Annotated[str, _wire_text(ANSWER_TEXT_MAX_UTF16)]
FindingId = Annotated[str, _wire_text(ANSWER_TEXT_MAX_UTF16, FINDING_ID_PATTERN)]
SolutionId = Annotated[str, _wire_text(ANSWER_TEXT_MAX_UTF16, SOLUTION_ID_PATTERN)]
AnswerSentence = Annotated[str, _wire_text(ANSWER_SENTENCE_MAX_UTF16), AfterValidator(_says_something)]
PathKey = Annotated[str, _wire_text(SOURCE_PATH_KEY_MAX_UTF16)]
PathIndex = Annotated[int, BeforeValidator(_integral), Field(ge=0, le=FINDING_INDEX_MAX)]


class FindingSubject(_Strict):
    """A finding of the solution, with that finding's own evidence class."""

    kind: Literal["finding"]
    finding_id: FindingId
    evidence_class: EvidenceClass


class EphemerisBodySubject(_Strict):
    """A body the ephemeris computed that has no finding."""

    kind: Literal["ephemeris_body"]
    body: Literal[EPHEMERIS_BODIES]  # type: ignore[valid-type]


class SolutionSubject(_Strict):
    kind: Literal["solution"]


AnswerSubject = Annotated[
    Union[FindingSubject, EphemerisBodySubject, SolutionSubject], Field(discriminator="kind")
]


class SolutionFieldSource(_Strict):
    """A field of the answer's stored solution: its path, which must match a row of SOLUTION_FIELD_SOURCES."""

    kind: Literal["solution_field"]
    path: Annotated[list[Union[PathKey, PathIndex]], Field(min_length=1, max_length=SOURCE_PATH_MAX_DEPTH)]


class CatalogSource(_Strict):
    kind: Literal["catalog"]
    catalog: Literal[CATALOG_PROVIDERS]  # type: ignore[valid-type]
    release: AnswerText
    row_id: AnswerText


class EphemerisSource(_Strict):
    """The ephemeris method run for the answer's solution's capture time and place."""

    kind: Literal["ephemeris"]
    method: Literal[tuple(EPHEMERIS_METHODS)]  # type: ignore[valid-type]


AnswerSource = Annotated[Union[SolutionFieldSource, CatalogSource, EphemerisSource], Field(discriminator="kind")]


class IntervalUncertainty(_Strict):
    kind: Literal["interval"]
    lower: Number
    upper: Number
    meaning: Literal[UNCERTAINTY_MEANINGS]  # type: ignore[valid-type]


class PlusMinusUncertainty(_Strict):
    kind: Literal["plus_minus"]
    half_width: Annotated[Number, Field(ge=0)]
    meaning: Literal[UNCERTAINTY_MEANINGS]  # type: ignore[valid-type]


AnswerUncertainty = Annotated[Union[IntervalUncertainty, PlusMinusUncertainty], Field(discriminator="kind")]


class NumberValue(_Strict):
    kind: Literal["number"]
    amount: Number
    unit: Literal[ANSWER_UNITS]  # type: ignore[valid-type]
    uncertainty: Optional[AnswerUncertainty] = None


class TextValue(_Strict):
    kind: Literal["text"]
    text: AnswerText


AnswerValue = Annotated[Union[NumberValue, TextValue], Field(discriminator="kind")]


def source_row(source: Any, quantity: str) -> dict[str, Any] | str:
    """The table row a source names for a quantity, or why there is none."""
    if isinstance(source, SolutionFieldSource):
        for row in SOLUTION_FIELD_SOURCES:
            if len(row["path"]) == len(source.path) and all(
                isinstance(given, int) if step == "*" else step == given for step, given in zip(row["path"], source.path)
            ):
                if row["quantity"] != quantity:
                    return f"the field {'/'.join(row['path'])} holds a {row['quantity']}, not a {quantity}"
                return row
        return "a source path names a field of the solution a statement may cite"
    rows = CATALOG_SOURCE_QUANTITIES if isinstance(source, CatalogSource) else EPHEMERIS_METHODS[source.method]["rows"]
    for row in rows:
        if row["quantity"] == quantity:
            return row
    return f"a {source.kind} source gives no {quantity}"


def in_range(amount: float, bounds: dict[str, Any]) -> bool:
    """Whether `amount` lies in `bounds`."""
    low, high = bounds["min"], bounds["max"]
    if low is not None and (not amount > low if bounds["minOpen"] else not amount >= low):
        return False
    if high is not None and (not amount < high if bounds["maxOpen"] else not amount <= high):
        return False
    return True


class AnswerStatement(_Strict):
    subject: AnswerSubject
    quantity: Literal[tuple(QUANTITY_SPECS)]  # type: ignore[valid-type]
    value: AnswerValue
    evidence_class: EvidenceClass
    source: AnswerSource

    @model_validator(mode="after")
    def _its_source_decides(self) -> AnswerStatement:
        subject, value, quantity = self.subject, self.value, self.quantity
        if (
            isinstance(subject, FindingSubject)
            and re.match(PREDICTED_FINDING_ID_PATTERN, subject.finding_id)
            and subject.evidence_class != "predicted"
        ):
            raise ValueError("a finding with an id of the predicted form is predicted")
        spec = QUANTITY_SPECS[quantity]
        if spec["value"] != value.kind:
            raise ValueError(f"a {quantity} statement takes a {spec['value']} value")
        if isinstance(value, TextValue) and "values" in spec and value.text not in spec["values"]:
            raise ValueError(f"a {quantity} is one of {', '.join(spec['values'])}")
        if isinstance(value, NumberValue):
            if not in_range(value.amount, spec["range"]):
                raise ValueError(f"a {quantity} lies in its range")
            if value.unit == "count" and not float(value.amount).is_integer():
                raise ValueError("a count is a whole number")
            u = value.uncertainty
            if u is not None and value.unit == "count":
                raise ValueError("a count is exact: it has no uncertainty")
            if isinstance(u, IntervalUncertainty):
                if not u.lower <= value.amount <= u.upper:
                    raise ValueError("an interval holds its value: lower <= amount <= upper")
                if not (in_range(u.lower, spec["range"]) and in_range(u.upper, spec["range"])):
                    raise ValueError(f"both ends of an interval lie in a {quantity}'s range")
        row = source_row(self.source, quantity)
        if isinstance(row, str):
            raise ValueError(row)
        unit = value.unit if isinstance(value, NumberValue) else None
        if unit != row["unit"]:
            raise ValueError(f"this source stores a {quantity} in {row['unit'] or 'text'}")
        if isinstance(self.source, EphemerisSource) and isinstance(subject, EphemerisBodySubject):
            bodies = EPHEMERIS_METHODS[self.source.method]["bodies"]
            if subject.body not in bodies:
                raise ValueError(f"the {self.source.method} method computes {', '.join(bodies)}, not {subject.body}")
        key = subject.evidence_class if isinstance(subject, FindingSubject) else subject.kind
        decided = CLASS_RULES[row["rule"]].get(key)
        if decided is None:
            raise ValueError(f"a {row['rule']} source states nothing about a {key} subject")
        if decided != self.evidence_class:
            raise ValueError(f"this statement is {decided}: its source decides, not the caller")
        return self


class Answered(_Strict):
    status: Literal["answered"]
    question: Literal[ANSWERABLE_QUESTION_KINDS]  # type: ignore[valid-type]
    observation_solution_id: SolutionId
    sentence: AnswerSentence
    statements: Annotated[list[AnswerStatement], Field(min_length=1, max_length=ANSWER_STATEMENTS_MAX)]

    @model_validator(mode="after")
    def _answers_its_question_with_one_class_per_finding(self) -> Answered:
        answering = QUESTION_QUANTITIES[self.question]
        if not any(statement.quantity in answering for statement in self.statements):
            raise ValueError(f"a {self.question} answer states at least one of {', '.join(answering)}")
        class_of: dict[str, str] = {}
        for statement in self.statements:
            subject = statement.subject
            if isinstance(subject, FindingSubject):
                if class_of.setdefault(subject.finding_id, subject.evidence_class) != subject.evidence_class:
                    raise ValueError("one answer gives a finding one class")
        return self


class Unanswerable(_Strict):
    """A refusal is a typed answer too: what was asked, why, in one plain sentence, and no statements."""

    status: Literal["unanswerable"]
    question: Literal[QUESTION_KINDS]  # type: ignore[valid-type]
    reason: Literal[UNANSWERABLE_REASONS]  # type: ignore[valid-type]
    sentence: AnswerSentence
    observation_solution_id: Optional[SolutionId] = None

    @model_validator(mode="after")
    def _outside_the_data_only_for_unanswerable_questions(self) -> Unanswerable:
        if (self.question == UNANSWERABLE_QUESTION_KIND) != (self.reason == "outside_the_data"):
            raise ValueError("a question no data answers is outside_the_data, and only such a question is")
        return self


VoiceAnswer = Annotated[Union[Answered, Unanswerable], Field(discriminator="status")]
ANSWER_STATEMENT: TypeAdapter[Any] = TypeAdapter(AnswerStatement)
VOICE_ANSWER: TypeAdapter[Any] = TypeAdapter(VoiceAnswer)


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
    # One refusal of each kind, with what a person might have said
    refusals = [
        {"kind": "not_understood", "said": "zorblax"},
        {"kind": "not_understood", "said": "milky way"},
        {"kind": "not_understood", "said": "a b c d e f ..."},
        {"kind": "not_understood", "said": "\u0441\u0438\u0440\u0438\u0443\u0441"},
        {"kind": "not_understood", "said": "\U0001d400" * 80 + " ..."},
        {"kind": "not_in_this_sky", "said": "vega", "name": "Vega"},
        {"kind": "not_in_this_sky", "said": "alpha centauri", "name": "Rigil Kentaurus"},
        {"kind": "deep_sky_out_of_reach", "said": "lmc", "name": "the Large Magellanic Cloud", "catalogRow": "ESO056-115"},
        {"kind": "deep_sky_out_of_reach", "said": "omega cen", "name": "Omega Centauri", "catalogRow": "NGC5139"},
        {"kind": "deep_sky_out_of_reach", "said": "coal sack", "name": "the Coalsack", "catalogRow": "C099"},
        {"kind": "no_named_row", "said": "magellanic clouds", "name": "the Magellanic Clouds"},
        {"kind": "no_named_row", "said": "eta car", "name": "Eta Carinae"},
        {"kind": "southern_cross_not_in_this_sky"},
        {"kind": "which_one"},
        {"kind": "nothing_in_view"},
        {"kind": "sky_not_worked_out"},
        {"kind": "nothing_in_view_sky_not_worked_out"},
        {"kind": "not_a_request", "said": "nobody"},
        {"kind": "not_a_request", "said": "\U0001d400" * 80},
        {"kind": "two_requests"},
    ]
    backend_refusals = [r for r in refusals if REFUSAL_KINDS[r["kind"]]["backend"]]
    # The sentence of every refusal, in each media word, and of names whose first letter the ASCII rule raises or
    # leaves (a digit), with letters of the letters table and an apostrophe inside
    sentences = [
        {"refusal": r, "media": media, "sentence": refusal_sentence(r, media)}
        for r in [
            *refusals,
            {"kind": "not_in_this_sky", "said": "tx", "name": "tx"},
            {"kind": "not_in_this_sky", "said": "the moon", "name": "the Moon"},
            {"kind": "not_in_this_sky", "said": "bibha", "name": "Bibh\u0101"},
            {"kind": "not_in_this_sky", "said": "al na'ir", "name": "al Na'ir"},
            {"kind": "not_in_this_sky", "said": "7 star", "name": "7 star"},
            {"kind": "no_named_row", "said": "z", "name": "z"},
            {"kind": "not_understood", "said": 'say "x"'},
        ]
        for media in REFUSAL_MEDIA
    ]
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
        *[
            {**refusal, "reason": REFUSAL_KINDS[r["kind"]]["reasons"][0], "interpretation": rules,
             "explanation": refusal_sentence(r), "refusal": r,
             "nextActions": ["Name a catalog star or planet, or point at one"]}
            for r in backend_refusals
        ],
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
    bad(action, _with(epoch, ["yearCE"], 0), "there is no year 0")
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
    # Refusals
    understood = next(r for r in rejections if r.get("refusal", {}).get("kind") == "not_understood")
    out_of_reach = next(r for r in rejections if r.get("refusal", {}).get("kind") == "deep_sky_out_of_reach")
    which = next(r for r in rejections if r.get("refusal", {}).get("kind") == "which_one")
    not_a_req = next(r for r in rejections if r.get("refusal", {}).get("kind") == "not_a_request")
    two_reqs = next(r for r in rejections if r.get("refusal", {}).get("kind") == "two_requests")
    bad(reply, _with(understood, ["refusal"], _DROP), "an ambiguous_target refusal says its kind")
    bad(reply, _with(not_a_req, ["refusal"], _DROP), "a not_one_request refusal says its kind")
    bad(reply, _with(not_a_req, ["reason"], "ambiguous_target"), "not_a_request goes out as not_one_request")
    bad(reply, _with(not_a_req, ["refusal", "said"], _DROP), "not_a_request quotes the word that stopped it")
    bad(reply, _with(two_reqs, ["reason"], "ambiguous_target"), "two_requests goes out as not_one_request")
    bad(reply, _with(two_reqs, ["refusal", "said"], "and"), "two_requests quotes nothing")
    bad(reply, _with(understood, ["refusal", "kind"], "not_heard"), "an unknown refusal kind")
    bad(reply, _with(understood, ["refusal", "kind"], _DROP), "a refusal has a kind")
    bad(reply, _with(understood, ["refusal"], None), "a refusal is absent, never null")
    bad(reply, _with(understood, ["reason"], "no_grounded_answer"), "a word not understood is ambiguous_target")
    bad(reply, _with(out_of_reach, ["reason"], "ambiguous_target"), "an object out of reach is no_grounded_answer")
    bad(reply, _with(understood, ["explanation"], "I did not understand that."), "the explanation is the sentence")
    bad(reply, _with(understood, ["refusal", "said"], "other"), "the sentence quotes what was said")
    bad(reply, _with(out_of_reach, ["refusal", "catalogRow"], "NGC0001"), "the sentence names the row")
    bad(reply, _with(understood, ["refusal", "said"], _DROP), "a word not understood quotes what was said")
    bad(reply, _with(which, ["refusal", "said"], "that"), "which one quotes nothing")
    bad(reply, _with(out_of_reach, ["refusal", "catalogRow"], _DROP), "an object out of reach names its row")
    bad(reply, _with(understood, ["refusal", "catalogRow"], "NGC0292"), "a word not understood names no row")
    bad(reply, _with(out_of_reach, ["refusal", "name"], _DROP), "an object out of reach names what it is")
    bad(reply, _with(out_of_reach, ["refusal", "name"], "Somewhere Else"), "the sentence shows the name")
    bad(reply, _with(understood, ["refusal", "name"], "Zorblax"), "a word not understood stands for no name")
    for kind in ("not_in_this_sky", "southern_cross_not_in_this_sky", "sky_not_worked_out", "nothing_in_view_sky_not_worked_out"):
        app_refusal = next(r for r in refusals if r["kind"] == kind)
        bad(reply, {**understood, "reason": REFUSAL_KINDS[kind]["reasons"][0], "explanation": refusal_sentence(app_refusal),
                    "refusal": app_refusal}, f"the backend holds no photo, so it never says {kind}")
    bad(reply, {**understood, "proposedAction": select}, "a refusal carries no action")
    bad(reply, {**described, "refusal": understood["refusal"]}, "an action carries no refusal")
    bad(reply, {**needs_input, "refusal": understood["refusal"]}, "a question carries no refusal")
    bad(reply, {**understood, "refusal": {**understood["refusal"], "note": "x"}}, "an unknown key on a strict refusal")
    refusal_schema = "VoiceRefusal"
    bad(refusal_schema, {"kind": "not_understood", "said": ""}, "an empty said")
    bad(refusal_schema, {"kind": "not_understood", "said": "x" * (REFUSAL_SAID_MAX_UTF16 + 1)}, "said of 201 units")
    bad(refusal_schema, {"kind": "not_understood", "said": "\U0001d400" * 100 + "x"}, "said of 201 units, astral")
    bad(refusal_schema, {"kind": "not_understood", "said": "zorb\nlax"}, "said with a control character")
    bad(refusal_schema, {"kind": "not_understood", "said": "zorb\ud800"}, "said with a lone surrogate")
    bad(refusal_schema, {"kind": "not_understood", "said": 7}, "said as a number")
    bad(refusal_schema, {"kind": "no_named_row", "said": "x", "name": ""}, "an empty name")
    bad(refusal_schema, {"kind": "no_named_row", "said": "x", "name": "n" * (REFUSAL_NAME_MAX_UTF16 + 1)}, "a name of 129 units")
    bad(refusal_schema, {"kind": "no_named_row", "said": "x", "name": "Eta\x00Carinae"}, "a name with a control character")
    for code_point in UNEVEN_CASE_CODE_POINTS:
        bad(refusal_schema, {"kind": "no_named_row", "said": "x", "name": chr(code_point) + "x"},
            f"a name starting with U+{code_point:04X}, whose case maps differ between Unicode 15.0.0 and 17.0.0")
    for name, why in (("\u00e9toile", "a first letter outside ASCII"), ("\u00dfeta", "a first letter outside ASCII"),
                      ("\u01c6ubhe", "a first letter outside ASCII"), ("\U00010428x", "a first letter outside the basic plane"),
                      (" Vega", "a leading space"), ("Vega ", "a trailing space"), ("Eta  Carinae", "two spaces"),
                      ("'Vega", "a leading apostrophe"), ("Na''ir", "two apostrophes"), ("Na' ir", "an apostrophe before a space"),
                      ("Eta-Carinae", "a hyphen, which no name holds"), ("Eta.", "a period"), ("Vega!", "a sign"),
                      ("Ve\u0301ga", "a combining mark, which is no letter of the table"), ("Vega\u00a0x", "a space that is not ASCII"),
                      ("x\U0001f600", "a sign outside the basic plane")):
        bad(refusal_schema, {"kind": "no_named_row", "said": "x", "name": name}, f"a name with {why}")
    bad(refusal_schema, {"kind": "no_named_row", "name": "Eta Carinae"}, "a name with nothing said")
    bad(refusal_schema, {"kind": "deep_sky_out_of_reach", "said": "x", "name": "X", "catalogRow": "NGC 0292;"}, "a row from no table")
    bad(refusal_schema, {"kind": "deep_sky_out_of_reach", "said": "x", "name": "X", "catalogRow": "N" * 33}, "a row of 33 units")
    bad(refusal_schema, {"kind": "deep_sky_out_of_reach", "said": "x", "name": "X", "catalogRow": "NGC0292\n"}, "a row with a newline")
    bad(refusal_schema, {"kind": "which_one", "catalog_row": "NGC0292"}, "snake_case is not the contract's")
    bad(refusal_schema, {"kind": "Not_Understood", "said": "x"}, "kinds are exact")
    bad(refusal_schema, {}, "no kind")
    # Edges from one table: every bound and pattern of a refusal at its inside end (valid) and just outside (refused)
    understood_base = {"kind": "not_understood"}
    named_base = {"kind": "no_named_row", "said": "x"}
    row_base = {"kind": "deep_sky_out_of_reach", "said": "x", "name": "X"}
    refusal_edges = [
        ("said", understood_base, "said", "x" * REFUSAL_SAID_MAX_UTF16, "x" * (REFUSAL_SAID_MAX_UTF16 + 1)),
        ("said in astral letters", understood_base, "said", "\U0001d400" * (REFUSAL_SAID_MAX_UTF16 // 2),
         "\U0001d400" * (REFUSAL_SAID_MAX_UTF16 // 2) + "x"),
        ("said of one unit", understood_base, "said", "x", ""),
        ("name", named_base, "name", "n" * REFUSAL_NAME_MAX_UTF16, "n" * (REFUSAL_NAME_MAX_UTF16 + 1)),
        ("name of one unit", named_base, "name", "n", ""),
        ("name starting with a digit", named_base, "name", "4x", "\u0664x"),
        ("name with a letter of the table inside", named_base, "name", "Bibh\u0101", "Bibh\u0101\u0301"),
        ("name with a space between words", named_base, "name", "Eta Carinae", "Eta Carinae "),
        ("name with an apostrophe between letters", named_base, "name", "Na'ir", "Na'"),
        ("row", row_base, "catalogRow", "N" * CATALOG_ROW_MAX_UTF16, "N" * (CATALOG_ROW_MAX_UTF16 + 1)),
        ("row of one unit", row_base, "catalogRow", "N", ""),
        ("row of two parts", row_base, "catalogRow", "NGC 0292", "NGC 0292 "),
        ("row joined by a hyphen", row_base, "catalogRow", "ESO056-115", "ESO056--115"),
        ("row starting with a part", row_base, "catalogRow", "C099", "-C099"),
    ]
    refusal_edge_valid = []
    for label, base_refusal, key, inside, outside in refusal_edges:
        refusal_edge_valid.append({**base_refusal, key: inside})
        bad(refusal_schema, {**base_refusal, key: outside}, f"edge {label} just outside its bound")

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
        "refusalKinds": REFUSAL_KINDS,
        "refusalLimits": {
            "saidMaxUtf16": REFUSAL_SAID_MAX_UTF16,
            "catalogRowMaxUtf16": CATALOG_ROW_MAX_UTF16,
            "nameMaxUtf16": REFUSAL_NAME_MAX_UTF16,
            "catalogRowPattern": CATALOG_ROW_PATTERN,
            "southernCrossStars": list(SOUTHERN_CROSS_STAR_NAMES),
            "media": list(REFUSAL_MEDIA),
        },
        "refusalSentences": sentences,
        "valid": {"AlbedianAction": actions, "VoiceCommandRequest": requests, "VoiceCommandResponse": replies,
                  "VoiceRefusal": refusals + refusal_edge_valid},
        "invalid": invalid,
        "stripped": stripped,
    }


def build_answer_samples() -> dict[str, Any]:
    """Valid and invalid answers and statements, for the fixture both sides parse.

    Every value is made up and points at no real person, place, time, file or catalog row. The invalid records
    include, verbatim, the first auditor's records of kinds b1 to b5 (AUDIT lane C, gen_cases.py), and each of
    them again with only its class wrong, so a widened class rule is caught by the record that names it.
    """
    solution_id = "sol-sample"

    def finding(evidence_class: str, finding_id: str) -> dict[str, Any]:
        return {"kind": "finding", "findingId": finding_id, "evidenceClass": evidence_class}

    def field(*path: Any) -> dict[str, Any]:
        return {"kind": "solution_field", "path": list(path)}

    catalog = {"kind": "catalog", "catalog": "Gaia DR3", "release": "sample-release-1", "rowId": "sample-row-1"}

    def ephemeris(method: str) -> dict[str, Any]:
        return {"kind": "ephemeris", "method": method}

    def body(name: str) -> dict[str, Any]:
        return {"kind": "ephemeris_body", "body": name}

    solution = {"kind": "solution"}

    def number(amount: Any, unit: str, uncertainty: dict[str, Any] | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {"kind": "number", "amount": amount, "unit": unit}
        if uncertainty is not None:
            value["uncertainty"] = uncertainty
        return value

    def words(text: str) -> dict[str, Any]:
        return {"kind": "text", "text": text}

    def say(subject: dict[str, Any], quantity: str, value: dict[str, Any], evidence_class: str,
            source: dict[str, Any]) -> dict[str, Any]:
        return {"subject": subject, "quantity": quantity, "value": value, "evidenceClass": evidence_class,
                "source": source}

    observed = finding("observed", "finding-obs-1")
    matched = finding("catalog_matched", "finding-star-sample")
    predicted = finding("predicted", "finding-predicted-moon")
    simulated = finding("simulated", "finding-scene-sample")

    def interval(lower: float, upper: float) -> dict[str, Any]:
        return {"kind": "interval", "lower": lower, "upper": upper, "meaning": "percentile_16_84"}

    bound = {"kind": "plus_minus", "halfWidth": 0.00001, "meaning": "error_bound"}

    name = say(matched, "name", words("Sample Star"), "catalog_matched", field("findings", 0, "catalogRef", "commonName"))
    kind = say(matched, "object_type", words("star"), "catalog_matched", catalog)
    star_distance = say(matched, "distance", number(7.75, "pc", interval(7.5, 8.25)), "catalog_matched",
                        field("findings", 0, "inferredDistancePc", "median"))
    catalog_distance = say(matched, "distance", number(7.75, "pc"), "catalog_matched",
                           field("findings", 0, "catalogRef", "bailerJonesDistancePc", "median"))
    row_distance = say(matched, "distance", number(7.75, "pc", interval(7.5, 8.25)), "catalog_matched", catalog)
    body_distance = say(predicted, "distance", number(0.00257, "au", bound), "predicted", ephemeris("moon"))
    below_distance = say(body("Saturn"), "distance", number(9.5, "au"), "predicted", ephemeris("planet"))
    sun_distance = say(body("Sun"), "distance", number(1.0, "au"), "predicted", ephemeris("sun"))
    magnitude = say(matched, "apparent_magnitude", number(-0.5, "mag"), "catalog_matched", catalog)
    stored_magnitude = say(matched, "apparent_magnitude", number(-0.5, "mag"), "catalog_matched",
                           field("findings", 0, "catalogRef", "apparentMagnitude"))
    snr = say(observed, "detection_snr", number(42.5, "ratio"), "observed", field("findings", 1, "sourceDetection", "snr"))
    matched_snr = say(matched, "detection_snr", number(80.25, "ratio"), "observed",
                      field("findings", 0, "sourceDetection", "snr"))
    colour = say(matched, "colour_index_bp_rp", number(-0.25, "mag"), "catalog_matched", catalog)
    stored_colour = say(matched, "colour_index_bp_rp", number(-0.25, "mag"), "catalog_matched",
                        field("findings", 0, "catalogRef", "colorIndexBpRp"))
    temperature = say(matched, "effective_temperature", number(8000, "K", interval(7900, 8100)), "catalog_matched",
                      catalog)
    spectral = say(matched, "spectral_type", words("A0V"), "catalog_matched", catalog)
    stored_spectral = say(matched, "spectral_type", words("A0V"), "catalog_matched",
                          field("findings", 0, "catalogRef", "spectralType"))
    constellation = say(predicted, "constellation", words("Sample"), "predicted",
                        field("findings", 2, "catalogRef", "constellation"))
    row_constellation = say(matched, "constellation", words("Sample"), "catalog_matched", catalog)
    stored_type = say(matched, "object_type", words("star"), "catalog_matched", field("findings", 0, "catalogRef", "objectType"))
    altitude = say(predicted, "altitude", number(-12.5, "deg"), "predicted", ephemeris("moon"))
    zenith = say(predicted, "altitude", number(90, "deg"), "predicted", field("findings", 2, "skyPosition", "altDeg"))
    nadir = say(body("Mars"), "altitude", number(-90, "deg"), "predicted", ephemeris("planet"))
    azimuth = say(predicted, "azimuth", number(271.25, "deg"), "predicted", field("findings", 2, "skyPosition", "azDeg"))
    north = say(body("Jupiter"), "azimuth", number(0, "deg"), "predicted", ephemeris("planet"))
    almost_north = say(body("Venus"), "azimuth", number(359.999, "deg"), "predicted", ephemeris("planet"))
    right_ascension = say(matched, "right_ascension", number(279.25, "deg"), "catalog_matched",
                          field("findings", 0, "skyPosition", "raDeg"))
    stored_ra = say(matched, "right_ascension", number(0, "deg"), "catalog_matched",
                    field("findings", 0, "catalogRef", "raDeg"))
    observed_dec = say(observed, "declination", number(38.75, "deg"), "observed", field("findings", 1, "skyPosition", "decDeg"))
    declination = say(matched, "declination", number(-90, "deg"), "catalog_matched", catalog)
    stored_dec = say(matched, "declination", number(38.75, "deg"), "catalog_matched",
                     field("findings", 0, "catalogRef", "decDeg"))
    row_ra = say(matched, "right_ascension", number(279.25, "deg"), "catalog_matched", catalog)
    body_ra = say(body("Uranus"), "right_ascension", number(12.5, "deg"), "predicted", ephemeris("planet"))
    body_dec = say(body("Uranus"), "declination", number(4.5, "deg"), "predicted", ephemeris("planet"))
    position = say(predicted, "position_uncertainty", number(3.5, "arcsec"), "predicted",
                   field("findings", 2, "skyPosition", "uncertaintyArcsec"))
    simulated_altitude = say(simulated, "altitude", number(45, "deg"), "simulated", ephemeris("moon"))
    observed_altitude = say(observed, "altitude", number(30.5, "deg"), "predicted", ephemeris("moon"))
    matched_altitude = say(matched, "altitude", number(30.5, "deg"), "predicted", ephemeris("moon"))
    simulated_ra = say(simulated, "right_ascension", number(10.5, "deg"), "simulated", field("findings", 3, "skyPosition", "raDeg"))
    lit = say(predicted, "illuminated_fraction", number(0.75, "fraction"), "predicted", ephemeris("lunar_phase"))
    new_moon = say(body("Moon"), "illuminated_fraction", number(0, "fraction"), "predicted", ephemeris("lunar_phase"))
    full_moon = say(body("Moon"), "illuminated_fraction", number(1, "fraction"), "predicted", ephemeris("lunar_phase"))
    phase_angle = say(body("Moon"), "phase_angle", number(60.5, "deg"), "predicted", ephemeris("lunar_phase"))
    opposite = say(body("Moon"), "phase_angle", number(180, "deg"), "predicted", ephemeris("lunar_phase"))
    phase_name = say(body("Moon"), "moon_phase_name", words("Waxing Gibbous"), "predicted", ephemeris("lunar_phase"))
    detected = say(solution, "detected_source_count", number(120, "count"), "observed",
                   field("quality", "detectedCandidateCount"))
    # An integral float is a whole count, as it is to JavaScript, and minus zero is zero
    matched_count = say(solution, "matched_star_count", number(12.0, "count"), "observed",
                        field("quality", "matchedInlierCount"))
    no_match = say(solution, "matched_star_count", number(-0.0, "count"), "observed", field("quality", "matchedInlierCount"))
    safe_count = say(solution, "detected_source_count", number(SAFE_INTEGER_MAX, "count"), "observed",
                     field("quality", "detectedCandidateCount"))
    status = say(solution, "solution_status", words("calibrated"), "observed", field("status"))
    held_out = say(solution, "held_out_pair_count", number(0, "count"), "observed", field("quality", "heldOutPairCount"))
    residual = say(solution, "held_out_median_residual", number(0.75, "px"), "observed",
                   field("quality", "medianResidualPx"))
    residual_arcsec = say(solution, "held_out_median_residual", number(0, "arcsec"), "observed",
                          field("quality", "medianResidualArcsec"))
    pose = say(solution, "pose_max_offset", number(2.5, "deg"), "predicted", field("poseEstimate", "maxCenterOffsetDeg"))
    pose_end = say(solution, "pose_max_offset", number(180, "deg"), "predicted", field("poseEstimate", "maxCenterOffsetDeg"))
    drawn = say(simulated, "name", words("Sample Scene Body"), "simulated",
                field("findings", 3, "catalogRef", "commonName"))
    # The first auditor's control: a simulated finding's distance, stated simulated
    simulated_distance = say(simulated, "distance", number(10, "pc"), "simulated",
                             field("findings", 3, "inferredDistancePc", "median"))
    tiny_distance = say(matched, "distance", number(1e-9, "pc"), "catalog_matched", catalog)

    def answered(question: str, sentence: str, *statements: dict[str, Any]) -> dict[str, Any]:
        return {"status": "answered", "question": question, "observationSolutionId": solution_id,
                "sentence": sentence, "statements": list(statements)}

    def refused(question: str, reason: str, sentence: str, **extra: Any) -> dict[str, Any]:
        return {"status": "unanswerable", "question": question, "reason": reason, "sentence": sentence, **extra}

    star = answered("identity", "That is Sample Star, a star in a made-up catalog.", name, kind)
    # The sentence may say light years; the statement carries the parsecs its source stores
    far = answered("distance", "Sample Star is about 25 light years away.", star_distance)
    moon_far = answered("distance", "Predicted for your photo's time and place: 385,000 km.", body_distance)
    phase = answered("moon_phase", "Predicted for your photo's time and place: 75 percent lit.", lit, phase_angle,
                     phase_name)
    contents = answered("photo_contents", "120 points of light, 12 of them matched to stars.", detected, matched_count,
                        name, drawn)
    answers = [
        star, far, moon_far,
        answered("distance", "Below your horizon at the time; predicted 9.5 AU away.", below_distance),
        answered("brightness", "Magnitude -0.5 in the catalog; clearly above the noise here.", magnitude, snr),
        answered("brightness", "a" + "\U0001f52d" * 44 + "a", matched_snr),
        answered("colour_temperature", "a" * ANSWER_SENTENCE_MAX_UTF16, colour, temperature, spectral),
        answered("constellation", "Predicted to lie in Sample.", constellation),
        answered("sky_position", "Predicted below your horizon.", altitude, position),
        answered("sky_position", "7", right_ascension, row_ra),
        answered("sky_position", "٣", observed_dec, observed_altitude),
        phase,
        answered("moon_phase", "New.", new_moon),
        contents,
        answered("confidence", "Calibrated on 12 stars; held-out residual under a pixel.", status, matched_count,
                 held_out, residual),
        answered("confidence", "An estimate from the Moon, good to about 2.5 degrees.", pose),
        refused("unanswerable", "outside_the_data", "Nothing in this photo's data answers that."),
        refused("distance", "not_found_in_this_sky", "I could not find that in this photo's sky.",
                observationSolutionId=solution_id),
        refused("identity", "photo_not_solved", "This photo's sky is not solved, so nothing is named."),
        refused("moon_phase", "no_time_or_place", "The Moon's phase needs your photo's time and place.",
                observationSolutionId="sol_0123456789abcdef"),
        refused("distance", "not_in_the_data", "This star has no distance in its catalog.",
                observationSolutionId=solution_id),
    ]
    # Sentences that say something: a letter or digit of any script, with spaces, joiners or line separators beside it
    for sentence in ("a", "é", "Ⅻ", "中", "a‍b", "a b", " ㅤaㅤ ", "​z", "a­b"):
        answers.append(refused("unanswerable", "outside_the_data", sentence))

    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: Any, reason: str) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason})

    answer, statement = "VoiceAnswer", "AnswerStatement"
    # The four generated faults of the brief, once each by hand
    bad(statement, _with(name, ["source"], _DROP), "a statement with no source")
    bad(statement, _with(star_distance, ["value", "unit"], _DROP), "a value with no unit")
    bad(statement, _with(body_distance, ["evidenceClass"], "observed"), "an observed statement about a predicted body")
    bad(answer, {**refused("unanswerable", "outside_the_data", "No."), "statements": [name]},
        "a refusal that carries statements")
    bad(answer, {**refused("unanswerable", "outside_the_data", "No."), "statements": []},
        "a refusal carries no statements, not even none")

    # The first auditor's records of kinds b1 to b5, verbatim (gen_cases.py), each refused
    a_catalog = {"kind": "catalog", "catalog": "Sample Catalog", "release": "r1", "rowId": "row-1"}
    a_ephemeris = {"kind": "ephemeris", "method": "sample method"}
    a_body = {"kind": "ephemeris_body", "body": "SampleBody"}

    def a_finding(evidence_class: str, finding_id: str = "finding-sample-1") -> dict[str, Any]:
        return finding(evidence_class, finding_id)

    for reason, record in (
        ("b1 predicted finding id declared observed", say(a_finding("observed", "finding-predicted-jupiter"), "altitude", number(30, "deg"), "observed", field("findings", 12, "skyPosition", "altDeg"))),
        ("b1 solution subject, observed, path into a predicted finding's position", say(solution, "altitude", number(30, "deg"), "observed", field("findings", 12, "skyPosition", "altDeg"))),
        ("b1 solution subject, observed apparent magnitude", say(solution, "apparent_magnitude", number(0.4, "mag"), "observed", field("findings", 0, "catalogRef", "apparentMagnitude"))),
        ("b1 simulated finding stated predicted", say(a_finding("simulated"), "distance", number(10, "pc"), "predicted", field("findings", 0, "inferredDistancePc", "median"))),
        ("b1 ephemeris body stated observed", say(a_body, "altitude", number(30, "deg"), "observed", field("findings", 12, "skyPosition", "altDeg"))),
        ("b2 catalog source labelled observed", say(a_finding("catalog_matched"), "apparent_magnitude", number(0.4, "mag"), "observed", a_catalog)),
        ("b2 catalog value via solution_field path, observed", say(a_finding("catalog_matched"), "apparent_magnitude", number(0.4, "mag"), "observed", field("findings", 0, "catalogRef", "apparentMagnitude"))),
        ("b2 catalog spectral type via solution_field path, observed", say(a_finding("observed"), "spectral_type", words("G2V"), "observed", field("findings", 0, "catalogRef", "spectralType"))),
        ("b2 catalog distance via solution_field path, observed, observed finding", say(a_finding("observed"), "distance", number(10, "pc"), "observed", field("findings", 0, "catalogRef", "bailerJonesDistancePc", "median"))),
        ("b3 ephemeris source labelled observed", say(a_finding("catalog_matched"), "altitude", number(30, "deg"), "observed", a_ephemeris)),
        ("b3 ephemeris source labelled catalog_matched", say(a_finding("catalog_matched"), "altitude", number(30, "deg"), "catalog_matched", a_ephemeris)),
        ("b4 catalog_matched statement about an observed finding", say(a_finding("observed"), "name", words("Sample"), "catalog_matched", a_catalog)),
        ("b4 catalog_matched statement about a predicted finding", say(a_finding("predicted"), "name", words("Sample"), "catalog_matched", a_catalog)),
        ("b5 catalog_matched statement about the solution", say(solution, "matched_star_count", number(10, "count"), "catalog_matched", field("quality", "matchedInlierCount"))),
        ("b5 solution subject observed distance", say(solution, "distance", number(10, "pc"), "observed", field("findings", 0, "inferredDistancePc", "median"))),
        ("b5 solution subject observed name", say(solution, "name", words("Sample Star"), "observed", field("findings", 0, "catalogRef", "commonName"))),
        ("b5 solution subject observed constellation", say(solution, "constellation", words("Orion"), "observed", field("findings", 0, "catalogRef", "constellation"))),
        ("b5 solution subject observed illuminated fraction", say(solution, "illuminated_fraction", number(0.5, "fraction"), "observed", field("findings", 12, "illuminatedFraction"))),
    ):
        bad(statement, record, f"auditor {reason}")
    bad(answer, {"status": "answered", "question": "identity", "observationSolutionId": "sol-1", "sentence": "That is Sample.",
                 "statements": [say(a_finding("predicted", "finding-x"), "altitude", number(30, "deg"), "predicted", a_ephemeris),
                                say(a_finding("catalog_matched", "finding-x"), "name", words("Sample"), "catalog_matched", a_catalog)]},
        "auditor b4 one finding declared predicted and catalog_matched in one answer")

    # The same kinds with only the class or the subject wrong: every source and field valid
    for reason, record in (
        ("b1 an id of the predicted form declared observed", _with(zenith, ["subject"], finding("observed", "finding-predicted-moon"))),
        ("b1 an id of the predicted form declared catalog_matched", _with(zenith, ["subject"], finding("catalog_matched", "finding-predicted-moon"))),
        ("b1 an id of the predicted form declared simulated", _with(zenith, ["subject"], finding("simulated", "finding-predicted-moon"))),
        ("b1 the solution cites a finding's sky position", _with(zenith, ["subject"], solution)),
        ("b1 the solution cites a catalog magnitude", _with(_with(stored_magnitude, ["subject"], solution), ["evidenceClass"], "observed")),
        ("b1 a simulated finding stated predicted", _with(simulated_distance, ["evidenceClass"], "predicted")),
        ("b1 an ephemeris body cites a finding's sky position", _with(zenith, ["subject"], body("Moon"))),
        ("b1 a predicted finding's sky position stated observed", _with(zenith, ["evidenceClass"], "observed")),
        ("b1 a catalog_matched finding's sky position stated observed", _with(right_ascension, ["evidenceClass"], "observed")),
        ("b1 an observed finding's sky position stated catalog_matched", _with(observed_dec, ["evidenceClass"], "catalog_matched")),
        ("b2 a catalog row stated observed", _with(magnitude, ["evidenceClass"], "observed")),
        ("b2 a stored catalog magnitude stated observed", _with(stored_magnitude, ["evidenceClass"], "observed")),
        ("b2 a stored catalog name stated observed", _with(name, ["evidenceClass"], "observed")),
        ("b2 a stored catalog distance stated observed", _with(catalog_distance, ["evidenceClass"], "observed")),
        ("b2 a stored inferred distance stated observed", _with(star_distance, ["evidenceClass"], "observed")),
        ("b2 a stored catalog colour stated predicted", _with(stored_colour, ["evidenceClass"], "predicted")),
        ("b2 a stored catalog value on an observed finding", _with(_with(stored_spectral, ["subject"], observed), ["evidenceClass"], "observed")),
        ("b2 a catalog row on an observed finding", _with(_with(spectral, ["subject"], observed), ["evidenceClass"], "observed")),
        ("b2 a stored catalog value of a predicted finding stated catalog_matched", _with(constellation, ["evidenceClass"], "catalog_matched")),
        ("b2 a catalog row of a simulated finding stated catalog_matched", _with(_with(row_constellation, ["subject"], simulated), ["evidenceClass"], "catalog_matched")),
        ("b2 a detection's signal to noise stated catalog_matched", _with(matched_snr, ["evidenceClass"], "catalog_matched")),
        ("b2 a detection's signal to noise on a predicted finding", _with(_with(snr, ["subject"], predicted), ["evidenceClass"], "predicted")),
        ("b2 a detection's signal to noise on a simulated finding", _with(_with(snr, ["subject"], simulated), ["evidenceClass"], "simulated")),
        ("b3 an ephemeris value stated observed", _with(altitude, ["evidenceClass"], "observed")),
        ("b3 an ephemeris value stated catalog_matched", _with(_with(altitude, ["subject"], matched), ["evidenceClass"], "catalog_matched")),
        ("b3 an ephemeris value of a simulated finding stated predicted", _with(simulated_altitude, ["evidenceClass"], "predicted")),
        ("b3 an ephemeris value of an observed finding stated observed", _with(observed_altitude, ["evidenceClass"], "observed")),
        ("b3 an ephemeris body stated simulated", _with(below_distance, ["evidenceClass"], "simulated")),
        ("b3 the solution from the ephemeris", _with(phase_name, ["subject"], solution)),
        ("b3 a catalog row about an ephemeris body", _with(magnitude, ["subject"], body("Moon"))),
        ("b4 a catalog row about the solution", _with(_with(magnitude, ["subject"], solution), ["evidenceClass"], "observed")),
        ("b5 the solution's count stated catalog_matched", _with(matched_count, ["evidenceClass"], "catalog_matched")),
        ("b5 the solution's count stated predicted", _with(detected, ["evidenceClass"], "predicted")),
        ("b5 the solution's status stated predicted", _with(status, ["evidenceClass"], "predicted")),
        ("b5 the solution's residual stated catalog_matched", _with(residual, ["evidenceClass"], "catalog_matched")),
        ("b5 the solution's pose offset stated observed", _with(pose, ["evidenceClass"], "observed")),
        ("b5 a finding cites the solution's counts", _with(_with(detected, ["subject"], matched), ["evidenceClass"], "observed")),
        ("b5 a finding cites the solution's pose", _with(_with(pose, ["subject"], predicted), ["evidenceClass"], "predicted")),
        ("b5 the solution states a name", _with(_with(name, ["subject"], solution), ["evidenceClass"], "observed")),
        ("b5 the solution states an illuminated fraction", _with(_with(lit, ["subject"], solution), ["evidenceClass"], "observed")),
    ):
        bad(statement, record, f"class {reason}")
    # Every row of every source table, about every kind of subject, in every class: the one class its rule decides
    # is valid, every other pair is refused, so widening any entry of CLASS_RULES or any row's rule is caught
    sample_values = {
        "name": words("Sample"), "object_type": words("star"), "spectral_type": words("A0V"),
        "constellation": words("Sample"), "moon_phase_name": words("Full Moon"), "solution_status": words("calibrated"),
        "distance": 1.5, "apparent_magnitude": 1.5, "detection_snr": 10.5, "colour_index_bp_rp": 0.5,
        "effective_temperature": 5000, "altitude": 10.5, "azimuth": 10.5, "right_ascension": 10.5, "declination": 10.5,
        "position_uncertainty": 1.5, "illuminated_fraction": 0.5, "phase_angle": 10.5, "detected_source_count": 3,
        "matched_star_count": 3, "held_out_pair_count": 3, "held_out_median_residual": 0.5, "pose_max_offset": 1.5,
    }
    grid_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for row in SOLUTION_FIELD_SOURCES:
        path = [0 if step == "*" else step for step in row["path"]]
        grid_rows.append(("/".join(row["path"]), field(*path), row))
    for row in CATALOG_SOURCE_QUANTITIES:
        grid_rows.append((f"catalog/{row['quantity']}", catalog, row))
    for method, spec in EPHEMERIS_METHODS.items():
        for row in spec["rows"]:
            grid_rows.append((f"{method}/{row['quantity']}", ephemeris(method), row))
    grid_subjects = {
        "observed": finding("observed", "finding-obs-9"), "catalog_matched": finding("catalog_matched", "finding-star-grid"),
        "predicted": finding("predicted", "finding-predicted-grid"), "simulated": finding("simulated", "finding-scene-grid"),
        "solution": solution,
    }
    grid_valid: list[dict[str, Any]] = []
    for label, source, row in grid_rows:
        given = sample_values[row["quantity"]]
        value = given if isinstance(given, dict) else number(given, row["unit"])
        bodies = EPHEMERIS_METHODS[source["method"]]["bodies"] if source["kind"] == "ephemeris" else ["Moon"]
        for key, subject in {**grid_subjects, "ephemeris_body": body(bodies[0])}.items():
            for evidence_class in EVIDENCE_CLASSES:
                record = say(subject, row["quantity"], value, evidence_class, source)
                if CLASS_RULES[row["rule"]].get(key) == evidence_class:
                    grid_valid.append(record)
                else:
                    bad(statement, record, f"table {label} about {key} stated {evidence_class}")
    edge_valid: list[dict[str, Any]] = []
    edge_answers: list[dict[str, Any]] = []
    # Every end of every quantity's range, on every row that gives the quantity, just past the end (a whole step for
    # a count): refused
    for label, source, row in grid_rows:
        spec = QUANTITY_SPECS[row["quantity"]]
        if spec["value"] != "number":
            continue
        key = next(iter(CLASS_RULES[row["rule"]]))
        bodies = EPHEMERIS_METHODS[source["method"]]["bodies"] if source["kind"] == "ephemeris" else ["Moon"]
        subject = {**grid_subjects, "ephemeris_body": body(bodies[0])}[key]
        step = 1 if row["unit"] == "count" else 0.001
        bounds = spec["range"]
        for end, at, is_open, outward in (("min", bounds["min"], bounds["minOpen"], -step), ("max", bounds["max"], bounds["maxOpen"], step)):
            if at is not None:
                outside = at if is_open else at + outward
                bad(statement, say(subject, row["quantity"], number(outside, row["unit"]), CLASS_RULES[row["rule"]][key], source),
                    f"range {label} past its {end} at {outside}")
                # The inside end itself, or the nearest double inside an open end: valid
                inside = math.nextafter(at, -outward * math.inf) if is_open else at
                edge_valid.append(say(subject, row["quantity"], number(inside, row["unit"]), CLASS_RULES[row["rule"]][key], source))
    # Edges from one table: every bounded length, count, index and pattern the contract defines, at its inside end
    # (valid) and just outside it (refused). A path key has no valid long form (every key is a row's), so only its
    # outside end is held, under "source a key of 65 units"
    tiny = math.nextafter(0, -1)
    edges = [
        ("a name", statement, name, ["value", "text"], "n" * ANSWER_TEXT_MAX_UTF16, "n" * (ANSWER_TEXT_MAX_UTF16 + 1)),
        ("a name in astral letters", statement, name, ["value", "text"], "\U0001d400" * (ANSWER_TEXT_MAX_UTF16 // 2),
         "\U0001d400" * (ANSWER_TEXT_MAX_UTF16 // 2) + "n"),
        ("a name of one unit", statement, name, ["value", "text"], "n", ""),
        ("a release", statement, magnitude, ["source", "release"], "r" * ANSWER_TEXT_MAX_UTF16, "r" * (ANSWER_TEXT_MAX_UTF16 + 1)),
        ("a row id", statement, magnitude, ["source", "rowId"], "r" * ANSWER_TEXT_MAX_UTF16, "r" * (ANSWER_TEXT_MAX_UTF16 + 1)),
        ("a finding id", statement, name, ["subject", "findingId"], "finding-" + "a" * (ANSWER_TEXT_MAX_UTF16 - 8),
         "finding-" + "a" * (ANSWER_TEXT_MAX_UTF16 - 7)),
        ("a finding id at its shortest", statement, name, ["subject", "findingId"], "finding-a", "finding-"),
        ("a finding id of two parts", statement, name, ["subject", "findingId"], "finding-a_b", "finding-a_"),
        ("a finding id with no prefix", statement, name, ["subject", "findingId"], "finding-A1", "findings-a"),
        ("a predicted id", statement, zenith, ["subject", "findingId"], "finding-predicted-a", "finding-predicted-"),
        ("a solution id", answer, star, ["observationSolutionId"], "sol-" + "a" * (ANSWER_TEXT_MAX_UTF16 - 4),
         "sol-" + "a" * (ANSWER_TEXT_MAX_UTF16 - 3)),
        ("a solution id at its shortest", answer, star, ["observationSolutionId"], "sol_a", "sol-"),
        ("a solution id with no prefix", answer, star, ["observationSolutionId"], "sol-a-b", "solx-a"),
        ("a sentence", answer, star, ["sentence"], "a" * ANSWER_SENTENCE_MAX_UTF16, "a" * (ANSWER_SENTENCE_MAX_UTF16 + 1)),
        ("a sentence of one unit", answer, star, ["sentence"], "a", ""),
        ("statements", answer, star, ["statements"], [name] * ANSWER_STATEMENTS_MAX, [name] * (ANSWER_STATEMENTS_MAX + 1)),
        ("statements at their fewest", answer, star, ["statements"], [name], []),
        ("a finding index", statement, star_distance, ["source", "path", 1], FINDING_INDEX_MAX, FINDING_INDEX_MAX + 1),
        ("a finding index at its lowest", statement, star_distance, ["source", "path", 1], 0, -1),
        ("a half width", statement, body_distance, ["value", "uncertainty", "halfWidth"], 0, tiny),
    ]
    for label, schema, sample, path, inside, outside in edges:
        (edge_valid if schema == statement else edge_answers).append(_with(sample, path, inside))
        bad(schema, _with(sample, path, outside), f"edge {label} just outside its bound")
    two_classes = answered("identity", "That is Sample.", name,
                           _with(_with(kind, ["subject", "evidenceClass"], "predicted"), ["evidenceClass"], "predicted"))
    bad(answer, two_classes, "class b4 one finding with two classes in one answer")
    bad(answer, answered("sky_position", "Measured.", observed_dec, _with(right_ascension, ["subject"], {**observed, "evidenceClass": "catalog_matched"})),
        "class b4 one finding observed in one statement, catalog_matched in another")

    # Values: every range end and the stored unit
    for reason, sample, path, value in (
        ("a distance of zero", star_distance, ["value", "amount"], 0),
        ("a negative distance", row_distance, ["value", "amount"], -5),
        ("a body's distance of zero", body_distance, ["value", "amount"], 0),
        ("a temperature of 0 K", temperature, ["value", "amount"], 0),
        ("a negative temperature", temperature, ["value", "amount"], -100),
        ("a signal to noise of zero", snr, ["value", "amount"], 0),
        ("a negative signal to noise", snr, ["value", "amount"], -4),
        ("an altitude above 90", zenith, ["value", "amount"], 90.000001),
        ("an altitude of 200", altitude, ["value", "amount"], 200),
        ("an altitude below -90", nadir, ["value", "amount"], -90.000001),
        ("a declination above 90", declination, ["value", "amount"], 100),
        ("a declination below -90", declination, ["value", "amount"], -90.5),
        ("a stored declination above 90", stored_dec, ["value", "amount"], 90.5),
        ("a right ascension of 360", right_ascension, ["value", "amount"], 360),
        ("a right ascension of 400", row_ra, ["value", "amount"], 400),
        ("a negative right ascension", stored_ra, ["value", "amount"], -10),
        ("a body's right ascension of 360", body_ra, ["value", "amount"], 360),
        ("an azimuth of 360", almost_north, ["value", "amount"], 360),
        ("an azimuth of 400", azimuth, ["value", "amount"], 400),
        ("a negative azimuth", north, ["value", "amount"], -0.5),
        ("a body's declination below -90", body_dec, ["value", "amount"], -91),
        ("a phase angle above 180", opposite, ["value", "amount"], 180.5),
        ("a phase angle of 500", phase_angle, ["value", "amount"], 500),
        ("a negative phase angle", phase_angle, ["value", "amount"], -10),
        ("a fraction above 1", full_moon, ["value", "amount"], 1.0000001),
        ("a negative fraction", new_moon, ["value", "amount"], -0.25),
        ("a negative position uncertainty", position, ["value", "amount"], -3),
        ("a negative residual", residual, ["value", "amount"], -1),
        ("a negative residual in arcsec", residual_arcsec, ["value", "amount"], -0.5),
        ("a negative pose offset", pose, ["value", "amount"], -2),
        ("a pose offset above 180", pose_end, ["value", "amount"], 180.5),
        ("a fractional count", detected, ["value", "amount"], 120.5),
        ("a negative count", detected, ["value", "amount"], -1),
        ("a count past 2^53 - 1", safe_count, ["value", "amount"], SAFE_INTEGER_MAX + 1),
        ("a count of 10^400", safe_count, ["value", "amount"], 10**400),
        ("a negative held-out count", held_out, ["value", "amount"], -1),
        ("a fraction's interval up to 1.5", lit, ["value", "uncertainty"], interval(0.5, 1.5)),
        ("a fraction's interval from below 0", lit, ["value", "uncertainty"], interval(-0.5, 0.9)),
        ("a distance interval from -5 pc", star_distance, ["value", "uncertainty"], interval(-5, 8.25)),
        ("a distance interval from 0 pc", row_distance, ["value", "uncertainty"], interval(0, 8.25)),
        ("a temperature interval from 0 K", temperature, ["value", "uncertainty"], interval(0, 8100)),
        ("an altitude interval past 90", zenith, ["value", "uncertainty"], interval(89, 91)),
        ("an azimuth interval up to 360", almost_north, ["value", "uncertainty"], interval(359, 360)),
        ("a phase angle interval past 180", opposite, ["value", "uncertainty"], interval(179, 181)),
        ("an uncertainty on a count", detected, ["value", "uncertainty"], {"kind": "plus_minus", "halfWidth": 0.5, "meaning": "one_sigma"}),
        ("an interval below its value", star_distance, ["value", "uncertainty", "upper"], 7.5),
        ("an interval above its value", star_distance, ["value", "uncertainty", "lower"], 8),
        ("a negative half width", body_distance, ["value", "uncertainty", "halfWidth"], -0.5),
        ("an unknown uncertainty meaning", body_distance, ["value", "uncertainty", "meaning"], "error bound"),
        ("an uncertainty is absent, never null", body_distance, ["value", "uncertainty"], None),
        ("a distance in light years", star_distance, ["value"], number(25.25, "ly")),
        ("a distance in kilometres", body_distance, ["value"], number(385000, "km")),
        ("a stored parsec distance stated in au", star_distance, ["value"], number(1598000, "au")),
        ("an ephemeris au distance stated in pc", body_distance, ["value"], number(1.2e-8, "pc")),
        ("a catalog row distance stated in au", row_distance, ["value"], number(1598000, "au")),
        ("a residual in px cited from the arcsec field", residual_arcsec, ["value", "unit"], "px"),
        ("a residual in arcsec cited from the px field", residual, ["value", "unit"], "arcsec"),
        ("a distance in magnitudes", star_distance, ["value", "unit"], "mag"),
        ("a held-out residual in degrees", residual, ["value", "unit"], "deg"),
        ("an unknown unit", star_distance, ["value", "unit"], "m"),
        ("a name is text, not a number", name, ["value"], number(1, "count")),
        ("a distance is a number, not text", star_distance, ["value"], words("far")),
        ("a text value has no unit", name, ["value", "unit"], "count"),
        ("a text value has no uncertainty", name, ["value", "uncertainty"], bound),
        ("an object type from no list", kind, ["value", "text"], "comet"),
        ("a status from no list", status, ["value", "text"], "solved"),
        ("a phase name from no list", phase_name, ["value", "text"], "Half Moon"),
        ("a boolean is not an amount", star_distance, ["value", "amount"], True),
        ("an amount as text", star_distance, ["value", "amount"], "7.75"),
        ("an empty text value", name, ["value", "text"], ""),
        ("an unknown quantity", name, ["quantity"], "mood"),
    ):
        bad(statement, _with(sample, path, value), f"value {reason}")

    # Sources: the cited field holds the stated quantity, and every key and method comes from a closed list
    for reason, sample, path, value in (
        ("a field holding another quantity", name, ["source", "path"], ["findings", 0, "catalogRef", "constellation"]),
        ("a catalog row gives no altitude", altitude, ["source"], catalog),
        ("a catalog row gives no illuminated fraction", lit, ["source"], catalog),
        ("the lunar phase gives no altitude", altitude, ["source", "method"], "lunar_phase"),
        ("the planet method gives no phase name", phase_name, ["source", "method"], "planet"),
        ("the moon method does not compute Saturn", below_distance, ["source", "method"], "moon"),
        ("the planet method does not compute the Sun", sun_distance, ["source", "method"], "planet"),
        ("the lunar phase is the Moon's", new_moon, ["subject", "body"], "Mars"),
        ("a body the ephemeris does not compute", below_distance, ["subject", "body"], "Pluto"),
        ("an ephemeris method from no list", body_distance, ["source", "method"], "sample method"),
        ("a catalog from no list", magnitude, ["source", "catalog"], "Sample Catalog"),
        ("a finding title as a name", drawn, ["source", "path"], ["findings", 3, "title"]),
        ("a pose's centre altitude", zenith, ["source", "path"], ["poseEstimate", "centerAltDeg"]),
        ("a key past the table", star_distance, ["source", "path"], ["findings", 0, "inferredDistancePc", "median", "x"]),
        ("a key in place of an index", star_distance, ["source", "path"], ["findings", "0", "inferredDistancePc", "median"]),
        ("an index in place of a key", detected, ["source", "path"], ["quality", 0]),
        ("the __proto__ key", star_distance, ["source", "path"], ["__proto__", "polluted"]),
        ("a constructor key", star_distance, ["source", "path"], ["constructor", "prototype"]),
        ("six steps", star_distance, ["source", "path"], ["findings", 0, "catalogRef", "bailerJonesDistancePc", "median", 0]),
        ("an index past the bound", star_distance, ["source", "path"], ["findings", FINDING_INDEX_MAX + 1, "inferredDistancePc", "median"]),
        ("an index past 2^53 - 1", star_distance, ["source", "path"], ["findings", SAFE_INTEGER_MAX + 1, "inferredDistancePc", "median"]),
        ("an index of 10^400", star_distance, ["source", "path"], ["findings", 10**400, "inferredDistancePc", "median"]),
        ("a negative index", star_distance, ["source", "path"], ["findings", -1, "inferredDistancePc", "median"]),
        ("a fractional index", star_distance, ["source", "path"], ["findings", 0.5, "inferredDistancePc", "median"]),
        ("a boolean index", star_distance, ["source", "path"], ["findings", True, "inferredDistancePc", "median"]),
        ("an empty path", name, ["source", "path"], []),
        ("an empty key", name, ["source", "path"], ["", 0]),
        ("a key of 65 units", name, ["source", "path"], ["k" * 65]),
        ("a path is a list", name, ["source", "path"], "findings.0"),
        ("a catalog source names its release", magnitude, ["source", "release"], _DROP),
        ("a catalog source names its row", magnitude, ["source", "rowId"], ""),
        ("a row id of 129 units", magnitude, ["source", "rowId"], "r" * 129),
        ("a row id with a control character", magnitude, ["source", "rowId"], "row\x001"),
        ("a release with a lone surrogate", magnitude, ["source", "release"], "r\ud800"),
        ("an unknown source kind", name, ["source", "kind"], "model"),
        ("an unknown key on a strict source", name, ["source", "url"], "https://example.invalid"),
    ):
        bad(statement, _with(sample, path, value), f"source {reason}")

    # Subjects and ids
    for reason, sample, path, value in (
        ("a finding subject names its own class", name, ["subject", "evidenceClass"], _DROP),
        ("an empty finding id", name, ["subject", "findingId"], ""),
        ("a finding id the backend never mints", name, ["subject", "findingId"], "star-sample"),
        ("a finding id with a space", name, ["subject", "findingId"], "finding-star sample"),
        ("a finding id with a trailing newline", name, ["subject", "findingId"], "finding-star-sample\n"),
        ("a finding id of 129 units", name, ["subject", "findingId"], "finding-" + "a" * 121),
        ("a finding id with a lone surrogate", name, ["subject", "findingId"], "finding-a\udc00"),
        ("an empty body name", below_distance, ["subject", "body"], ""),
        ("the solution subject is the answer's own", detected, ["subject", "observationSolutionId"], solution_id),
        ("an unknown subject kind", name, ["subject", "kind"], "galaxy"),
        ("a name with a control character", name, ["value", "text"], "Sample\x07Star"),
        ("a name with a lone surrogate", name, ["value", "text"], "Sample \ud83d"),
        ("a name of 129 units", name, ["value", "text"], "n" * 129),
        ("an unknown evidence class", name, ["evidenceClass"], "measured"),
    ):
        bad(statement, _with(sample, path, value), f"subject {reason}")
    bad(statement, {**name, "confidence": 0.9}, "an unknown key on a strict statement")
    bad(statement, {"subject": matched, "quantity": "name", "value": words("x"), "evidence_class": "catalog_matched",
                    "source": catalog}, "snake_case names are not the contract's")
    bad(statement, _with(snr, ["subject"], {"kind": "finding", "finding_id": "finding-obs-1", "evidenceClass": "observed"}),
        "a snake_case key on a subject")

    # Answers
    bad(answer, _with(star, ["statements"], []), "an answer states something")
    bad(answer, _with(star, ["statements"], _DROP), "an answer carries its statements")
    bad(answer, _with(star, ["statements"], [name] * (ANSWER_STATEMENTS_MAX + 1)), "an answer of 33 statements")
    bad(answer, _with(far, ["statements"], [name]), "a distance answer states a distance")
    bad(answer, _with(contents, ["statements"], [status]), "a photo_contents answer states what is in the photo")
    bad(answer, _with(star, ["statements", 0, "source"], _DROP), "an answer's statements are valid statements")
    bad(answer, _with(star, ["question"], "unanswerable"), "a question no data answers is never answered")
    bad(answer, _with(star, ["question"], "weather"), "an unknown question kind")
    bad(answer, _with(star, ["observationSolutionId"], _DROP), "an answer names its solution")
    bad(answer, _with(star, ["observationSolutionId"], ""), "an empty solution id")
    bad(answer, _with(star, ["observationSolutionId"], "solution-1"), "a solution id the backend never mints")
    bad(answer, _with(star, ["sentence"], _DROP), "an answer has a sentence")
    bad(answer, _with(star, ["status"], "proposed"), "an unknown status")
    bad(answer, _with(star, ["status"], _DROP), "no status")
    bad(answer, {**star, "reason": "not_in_the_data"}, "an answered answer has no reason")
    refusal = answers[19]
    assert refusal["reason"] == "no_time_or_place"
    bad(answer, _with(refusal, ["reason"], "outside_the_data"), "only a question no data answers is outside_the_data")
    bad(answer, _with(answers[16], ["reason"], "not_in_the_data"), "a question no data answers is outside_the_data")
    bad(answer, _with(refusal, ["reason"], "not_wired"), "an unknown reason")
    bad(answer, _with(refusal, ["reason"], _DROP), "a refusal says why")
    bad(answer, _with(refusal, ["observationSolutionId"], None), "a solution id is absent, never null")
    bad(answer, _with(refusal, ["observationSolutionId"], ""), "an empty solution id on a refusal")
    bad(answer, _with(refusal, ["question"], "weather"), "an unknown question kind on a refusal")
    bad(answer, {**refusal, "explanation": "More words."}, "an unknown key on a strict refusal")

    # Sentences that say nothing: the first auditor's eleven characters, alone and three times over, the other
    # hidden letters, spaces only, control characters, lone surrogates, and the UTF-16 bound
    nothing = {
        "U+200B": "​", "U+200C": "‌", "U+200D": "‍", "U+2060": "⁠", "U+00AD": "­",
        "U+180E": "᠎", "U+2800": "⠀", "U+3164": "ㅤ", "U+0085": "\x85", "U+001C": "\x1c", "U+001F": "\x1f",
        "U+115F": "ᅟ", "U+1160": "ᅠ", "U+FFA0": "ﾠ", "U+FEFF": "﻿", "U+2028": " ",
    }
    for base_name, base in (("refusal", refusal), ("answer", star)):
        for label, char in nothing.items():
            for times in (1, 3):
                bad(answer, _with(base, ["sentence"], char * times), f"sentence {base_name}: only {label} x{times}")
    for reason, sentence in (
        ("empty", ""), ("JavaScript spaces only", " \t　﻿ "), ("punctuation only", "?!..."),
        ("an emoji only", "\U0001f52d"), ("a letter and a tab", "a\tb"), ("a letter and a newline", "Yes.\n"),
        ("a letter and NUL", "a\x00"), ("a letter and U+0085", "a\x85"), ("a letter and DEL", "a\x7f"),
        ("a letter and a lone high surrogate", "a\ud800"), ("a letter and a lone low surrogate", "\udfffa"),
        ("91 units", "a" * (ANSWER_SENTENCE_MAX_UTF16 + 1)), ("90 code points, 91 units", "a" * 89 + "\U0001f52d"),
        ("45 astral letters and one more", "\U0001d400" * 45 + "a"),
    ):
        bad(answer, _with(star, ["sentence"], sentence), f"sentence {reason}")

    statements = [
        name, kind, star_distance, catalog_distance, row_distance, body_distance, below_distance, sun_distance, magnitude,
        stored_magnitude, snr, matched_snr, colour, stored_colour, temperature, spectral, stored_spectral, constellation,
        row_constellation, stored_type, altitude, zenith, nadir, azimuth, north, almost_north, right_ascension, stored_ra,
        observed_dec, declination, stored_dec, row_ra, body_ra, body_dec, position, simulated_altitude, observed_altitude,
        matched_altitude, simulated_ra,
        lit, new_moon, full_moon, phase_angle, opposite, phase_name, detected, matched_count, no_match, safe_count,
        status, held_out, residual, residual_arcsec, pose, pose_end, drawn, simulated_distance, tiny_distance,
        *grid_valid,
        *edge_valid,
    ]
    answers += edge_answers
    return {
        "enums": {
            "questionKinds": list(QUESTION_KINDS),
            "answerableQuestionKinds": list(ANSWERABLE_QUESTION_KINDS),
            "unanswerableReasons": list(UNANSWERABLE_REASONS),
            "units": list(ANSWER_UNITS),
            "evidenceClasses": list(EVIDENCE_CLASSES),
            "objectTypes": list(OBJECT_TYPES),
            "solutionStatuses": list(SOLUTION_STATUSES),
            "moonPhaseNames": list(MOON_PHASE_NAMES),
            "catalogProviders": list(CATALOG_PROVIDERS),
            "ephemerisBodies": list(EPHEMERIS_BODIES),
            "uncertaintyMeanings": list(UNCERTAINTY_MEANINGS),
        },
        "tables": {
            "quantities": QUANTITY_SPECS,
            "questionQuantities": QUESTION_QUANTITIES,
            "classRules": CLASS_RULES,
            "solutionFieldSources": SOLUTION_FIELD_SOURCES,
            "catalogSourceQuantities": CATALOG_SOURCE_QUANTITIES,
            "ephemerisMethods": EPHEMERIS_METHODS,
        },
        "limits": {
            "sentenceMaxUtf16": ANSWER_SENTENCE_MAX_UTF16,
            "textMaxUtf16": ANSWER_TEXT_MAX_UTF16,
            "statementsMax": ANSWER_STATEMENTS_MAX,
            "findingIndexMax": FINDING_INDEX_MAX,
            "sourcePathMaxDepth": SOURCE_PATH_MAX_DEPTH,
            "safeIntegerMax": SAFE_INTEGER_MAX,
            "findingIdPattern": FINDING_ID_PATTERN,
            "predictedFindingIdPattern": PREDICTED_FINDING_ID_PATTERN,
            "solutionIdPattern": SOLUTION_ID_PATTERN,
        },
        # The table the sentence rule reads, never this runtime's Unicode data
        "letters": {
            "unicodeVersion": LETTERS_AND_DIGITS_UNICODE_VERSION,
            "count": _LETTERS_AND_DIGITS["count"],
            "sha256": hashlib.sha256(LETTERS_AND_DIGITS_PATH.read_bytes()).hexdigest(),
        },
        "valid": {"AnswerStatement": statements, "VoiceAnswer": answers},
        "invalid": invalid,
    }


# The answer route's request and reply: POST /api/voice/answer/{sha256}, the upload named in the path as
# /api/ingest/{sha256}/solution names it (the mirror of VoiceQuestionRequestSchema and VoiceQuestionReplySchema in
# packages/contracts/src/voice-answer.ts). The words are the person's own, as heard: text within its bounds, which
# the contract never reads or changes (no trim, case or normalisation). The reply carries those words and one
# VoiceAnswer, nothing else. An answer is about the distinct findings its statements name as subjects, which may be
# none, one or several, each at the place it first comes; a refusal names none (answer_findings).
QUESTION_HEARD_MAX_UTF16 = QUESTION_MAX_UTF16


class VoiceQuestionRequest(_Strict):
    question: Annotated[str, _wire_text(QUESTION_HEARD_MAX_UTF16)]
    selected_finding_id: Optional[Annotated[str, _wire_text(ANSWER_TEXT_MAX_UTF16, FINDING_ID_PATTERN)]] = None


class VoiceQuestionReply(_Strict):
    heard: Annotated[str, _wire_text(QUESTION_HEARD_MAX_UTF16)]
    answer: VoiceAnswer


VOICE_QUESTION_REQUEST: TypeAdapter[Any] = TypeAdapter(VoiceQuestionRequest)
VOICE_QUESTION_REPLY: TypeAdapter[Any] = TypeAdapter(VoiceQuestionReply)
# How many valid samples of each record get an unknown key and a Python name at every level in the fixture
QUESTION_KEY_SAMPLES = 4


def answer_findings(answer: dict[str, Any]) -> list[str]:
    """The distinct finding ids an answer's statements name as subjects, each at the place it first comes: none, one
    or several. A refusal names none. The mirror of answerFindings in voice-answer.ts."""
    if answer.get("status") != "answered":
        return []
    found: list[str] = []
    for statement in answer["statements"]:
        subject = statement["subject"]
        if subject["kind"] == "finding" and subject["findingId"] not in found:
            found.append(subject["findingId"])
    return found


# The words as heard that a contract would change if it trimmed, cased or normalised them: each is held as given
WORDS_AS_HEARD = (
    ("spaces at both ends", "  how far is that star?  "),
    ("capitals", "How Far Is VEGA"),
    ("a base letter and a combining mark", "where is Ve\u0301ga"),
    ("the same letter precomposed", "where is V\u00e9ga"),
    ("two spaces inside", "how  far is it"),
    ("a script with no case", "\u5317\u6975\u661f\u306f\u3069\u3053"),
)


def python_name(key: str) -> str:
    """A camelCase wire name as Python would write it (selectedFindingId -> selected_finding_id)."""
    return "".join("_" + chr(ord(char) + 0x20) if "A" <= char <= "Z" else char for char in key)


def build_question_samples() -> dict[str, Any]:
    """Valid and invalid requests and replies of the answer route, for the fixture both sides parse. Made up."""
    answer_samples = build_answer_samples()
    answers = answer_samples["valid"]["VoiceAnswer"]
    answered = [a for a in answers if a["status"] == "answered"]
    refused = [a for a in answers if a["status"] == "unanswerable"]
    words = "how far is that star?"
    requests = [
        {"question": words},
        {"question": "what is that one", "selectedFindingId": "finding-star-vega"},
        {"question": "is Saturn up?", "selectedFindingId": "finding-predicted-saturn"},
        {"question": "сириус?"},
    ]
    replies = [{"heard": words, "answer": a} for a in [answered[0], refused[0], *answered[1:], *refused[1:]]]
    requests += [{"question": heard} for _kind, heard in WORDS_AS_HEARD]
    replies += [{"heard": heard, "answer": answered[0]} for _kind, heard in WORDS_AS_HEARD]
    # One answer of each count of findings it is about, and a refusal
    about = []
    for count in (0, 1, 2):
        answer = next(a for a in answered if len(answer_findings(a)) == count)
        about.append({"what": f"an answer about {count} findings", "answer": answer, "findings": answer_findings(answer)})
    about.append({"what": "a refusal", "answer": refused[0], "findings": []})
    # A finding named again after another keeps the place it first comes
    two = next(a for a in answered if len(answer_findings(a)) == 2)
    first_a, first_b = answer_findings(two)
    by_finding = {sid: next(st for st in two["statements"] if st["subject"].get("findingId") == sid) for sid in (first_a, first_b)}
    pool = {"A": by_finding[first_a], "B": by_finding[first_b],
            "C": {**by_finding[first_a], "subject": {**by_finding[first_a]["subject"], "findingId": "finding-sample-c"}}}
    others = [st for st in two["statements"] if st["subject"]["kind"] != "finding"]
    for order in ("ABA", "ABBA", "ABCB"):
        answer = {**two, "statements": [pool[letter] for letter in order] + others}
        VOICE_ANSWER.validate_python(answer)
        about.append({"what": f"named again: {', '.join(order)}", "answer": answer, "findings": answer_findings(answer)})
    finding_room = ANSWER_TEXT_MAX_UTF16 - len("finding-")
    edges = [
        {"schema": "VoiceQuestionRequest", "base": {}, "field": "question",
         "inside": ["x", "x" * QUESTION_HEARD_MAX_UTF16, "\U0001d400" * (QUESTION_HEARD_MAX_UTF16 // 2)],
         "outside": ["", "x" * (QUESTION_HEARD_MAX_UTF16 + 1), "\U0001d400" * (QUESTION_HEARD_MAX_UTF16 // 2) + "x",
                     "a\nb", "a\ud800b", 7]},
        {"schema": "VoiceQuestionReply", "base": {"answer": answered[0]}, "field": "heard",
         "inside": ["x", "x" * QUESTION_HEARD_MAX_UTF16],
         "outside": ["", "x" * (QUESTION_HEARD_MAX_UTF16 + 1), "a\x85b", None]},
        {"schema": "VoiceQuestionRequest", "base": {"question": words}, "field": "selectedFindingId",
         "inside": ["finding-x", "finding-" + "a" * finding_room, "finding-a_b-c9"],
         "outside": ["finding-", "finding-" + "a" * (finding_room + 1), "finding-x-", "Finding-x", "finding--x",
                     "finding-x y", "finding-é", None]},
    ]
    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: Any, reason: str) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason})

    for edge in edges:
        for outside in edge["outside"]:
            bad(edge["schema"], {**edge["base"], edge["field"]: outside}, f"edge {edge['field']} {outside!r}"[:200])
    bad("VoiceQuestionRequest", {}, "no words")
    bad("VoiceQuestionRequest", [words], "a list, not a record")
    bad("VoiceQuestionReply", {"heard": words}, "no answer")
    bad("VoiceQuestionReply", {"answer": answered[0]}, "no words heard")
    bad("VoiceQuestionReply", {"heard": words, "answer": None}, "a null answer")
    bad("VoiceQuestionReply", {"heard": words, "answer": answer_samples["invalid"][0]["sample"]},
        "an answer the answer contract refuses")
    bad("VoiceQuestionReply", {"heard": words, "answer": answered[0], "question": words}, "the request's words beside the reply's")

    def nodes(node: Any, at: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if isinstance(node, dict):
            return [at] + [path for key, value in node.items() for path in nodes(value, (*at, key))]
        if isinstance(node, list):
            return [path for index, value in enumerate(node) for path in nodes(value, (*at, index))]
        return []

    def changed(sample: Any, path: tuple[Any, ...], change: Any) -> Any:
        copied = json.loads(json.dumps(sample))
        node = copied
        for step in path:
            node = node[step]
        change(node)
        return copied

    for name, valid in (("VoiceQuestionRequest", requests), ("VoiceQuestionReply", replies)):
        for index, sample in enumerate(valid[:QUESTION_KEY_SAMPLES]):
            for path in nodes(sample):
                bad(name, changed(sample, path, lambda node: node.__setitem__("unknownKey", 1)),
                    f"{name}[{index}] unknown key at {list(path)}")
                node = sample
                for step in path:
                    node = node[step]
                for key in list(node):
                    snake = python_name(key)
                    if snake != key:
                        bad(name, changed(sample, path, lambda n, k=key, s=snake: n.__setitem__(s, n.pop(k))),
                            f"{name}[{index}] Python name {snake} at {list(path)}")
    return {
        "limits": {"heardMaxUtf16": QUESTION_HEARD_MAX_UTF16, "findingIdMaxUtf16": ANSWER_TEXT_MAX_UTF16,
                   "findingIdPattern": FINDING_ID_PATTERN},
        "edges": edges,
        "wordsAsHeard": [{"kind": kind, "words": heard} for kind, heard in WORDS_AS_HEARD],
        "about": about,
        "valid": {"VoiceQuestionRequest": requests, "VoiceQuestionReply": replies},
        "invalid": invalid,
    }
