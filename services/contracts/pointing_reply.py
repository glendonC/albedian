"""What the laptop answers to the record route: the Python mirror of packages/contracts/src/pointing-reply.ts.

`PUT /api/ingest/{photo SHA-256}/pointing` (the record route, not built yet) answers a receipt for a record it holds,
or a refusal. Both sides are judged by tests/fixtures/pointing_reply_contract.json, which
tests/integration/test_pointing_reply_contract.py writes and the Vitest file and the iPhone app's reader read.

The receipt (a final 2xx) proves which record the laptop holds: `sourceAssetSha256` is the photo's;
`pointingRecordSha256` is the SHA-256 of the exact body bytes the route received and kept, the pointing record's own
hash, which the phone compares with the hash of the bytes it sent; `alreadyStored` is true only when a record with
the same hash was already held for that photo, and false when this request stored it. A different record already
held for the same photo is never a receipt: the route refuses it with `differentRecordHeld`.

A refusal (a 4xx) never repeats what it refuses: its detail and each issue's message are closed sentences (the
record's own rules and the route's), and an issue's path is made of the record's own wire keys. No value of the
record, no key the record made up, and no text of the server's own errors is in it. The API's own 413 for a body over
the route's limit (services/api.py `body_too_large_detail`) is the same answer on every route and is not a record
refusal. Strict by the wire rules
(services/contracts/wire.py): unknown keys and Python field names are refused at every level.
"""

from __future__ import annotations

import typing
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from services.contracts.pointing import POINTING_RULES, PointingRecord
from services.contracts.wire import WIRE_CONFIG, Sha256, WireModel, wire_int

POINTING_RECEIPT_FORMAT = "albedian.pointing-receipt"
POINTING_RECEIPT_FORMAT_VERSION = 1
# [proposed] At most this many issues: one per field of the record is far more than any refusal lists
POINTING_REPLY_ISSUES_MAX = 32
# [proposed] An issue's path is at most this deep: the record nests four objects deep, so eight leaves room
POINTING_REPLY_PATH_MAX_DEPTH = 8

# The route's own refusal sentences, worded the same on both sides
POINTING_REPLY_SENTENCES = {
    "differentRecordHeld": "A different pointing record is already held for this photo.",
    "anotherPhoto": "The record names another photo than the one in its address.",
    "noSuchPhoto": "No photo with this SHA-256 is held, so no record is kept for it.",
    "notARecord": "The body is not a pointing record by its contract.",
    "notJson": "The body is not JSON text.",
    "solveStarted": "A solve job exists for this photo, so no pointing record can be added to it now.",
}
# Every sentence a refusal may say: the record's own rules, then the route's
POINTING_REFUSAL_SENTENCES = (*POINTING_RULES.values(), *POINTING_REPLY_SENTENCES.values())


def _keys_of(model: type[BaseModel], into: set[str]) -> set[str]:
    """Every wire key the record holds at any level, read from its models, so a path names only the record's own."""
    for field in model.model_fields.values():
        into.add(field.alias)  # type: ignore[arg-type]
        for inner in _models_in(field.annotation):
            _keys_of(inner, into)
    return into


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for argument in typing.get_args(annotation) for model in _models_in(argument)]


POINTING_RECORD_KEYS = tuple(sorted(_keys_of(PointingRecord, set())))


def _scrub_errors(title: str, exc: ValidationError) -> ValidationError:
    details: list[InitErrorDetails] = [
        InitErrorDetails(
            type=PydanticCustomError(err["type"], "{message}", {"message": err["msg"]}),
            loc=err["loc"],
            input=None,
        )
        for err in exc.errors(include_url=False, include_input=False)
    ]
    return ValidationError.from_exception_data(title, details, hide_input=True)


class _ReplyModel(WireModel):
    model_config = ConfigDict(**WIRE_CONFIG, hide_input_in_errors=True)

    @model_validator(mode="wrap")
    @classmethod
    def _no_input_in_errors(cls, data: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        try:
            return handler(data)
        except ValidationError as exc:
            raise _scrub_errors(cls.__name__, exc) from None

    @classmethod
    def model_validate_json(cls, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().model_validate_json(*args, **kwargs)
        except ValidationError as exc:
            raise _scrub_errors(cls.__name__, exc) from None

    def to_wire_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class PointingRecordReceipt(_ReplyModel):
    format: Literal["albedian.pointing-receipt"]
    format_version: Annotated[Literal[1], BeforeValidator(wire_int)]
    source_asset_sha256: Sha256
    pointing_record_sha256: Sha256
    already_stored: bool

    @field_validator("source_asset_sha256", "pointing_record_sha256")
    @classmethod
    def _not_a_placeholder(cls, value: str) -> str:
        if set(value) == {"0"}:
            raise ValueError(POINTING_RULES["placeholderSha256"])
        return value


def _in_set(allowed: tuple[str, ...]) -> AfterValidator:
    members = frozenset(allowed)

    def check(value: str) -> str:
        if value not in members:
            raise ValueError
        return value

    return AfterValidator(check)


RecordKey = Annotated[str, _in_set(POINTING_RECORD_KEYS)]
Sentence = Annotated[str, _in_set(POINTING_REFUSAL_SENTENCES)]


class PointingRefusalIssue(_ReplyModel):
    path: Annotated[list[RecordKey], Field(min_length=1, max_length=POINTING_REPLY_PATH_MAX_DEPTH)]
    message: Sentence


class PointingRecordRefusal(_ReplyModel):
    detail: Sentence
    issues: Optional[Annotated[list[PointingRefusalIssue], Field(max_length=POINTING_REPLY_ISSUES_MAX)]] = None

