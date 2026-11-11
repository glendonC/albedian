"""The wire rules a record follows on both sides: the Python half of packages/contracts/src/wire.ts.

JSON is read differently by Python and by JavaScript, so a record is held to the subset both read the same way:
- Keys are camelCase and nothing else. A Python field name sent over the wire is refused, at every level, with the
  message an unknown key gets. Python code may still build a record by field name with `model_validate(data,
  by_name=True)`; parsing what came over the wire may not.
- Every number is finite. An integer field holds a safe integer (magnitude at most 2^53 - 1), as JavaScript holds
  one exactly; anything larger is refused. A whole number written as a float (1.0, 1e0) is that integer, minus zero
  is zero, and a boolean is never a number.
- A length is counted in UTF-16 code units, as JavaScript counts it.
- A SHA-256 is 64 lower-case hex characters, and a UTC instant is one form, with Z, on a real calendar day.

This module imports no other contract file, so every contract can use it. Today `media.py` and the two ingest shapes
a media record embeds (an audio stream and a display orientation) do; moving the other contracts onto it is a later
unit of the contracts lane.
"""

from __future__ import annotations

import calendar
from typing import Annotated, Any, ClassVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

# The largest integer a double holds exactly, and so the largest JavaScript reads exactly (Number.MAX_SAFE_INTEGER)
MAX_SAFE_INTEGER = 2**53 - 1
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
_DATE = r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{3})?"
# The one UTC form every record writes: seconds, milliseconds when nonzero, then Z
UTC_INSTANT_PATTERN = rf"^{_DATE}T{_TIME}Z$"
WIRE_PATTERNS = {"sha256": SHA256_HEX_PATTERN, "utcInstant": UTC_INSTANT_PATTERN}

# The messages both sides give, word for word
M_NOT_A_NUMBER = "a boolean is not a number"
M_NOT_FINITE = "a number is finite"
M_NOT_SAFE = "an integer is at most 2^53 - 1 in size"
M_CALENDAR = "not a real calendar day"
WIRE_MESSAGES = (M_NOT_A_NUMBER, M_NOT_FINITE, M_NOT_SAFE, M_CALENDAR)


def wire_int(value: Any) -> Any:
    """An integer as JavaScript reads it: 1.0 and 1e0 are 1 and -0.0 is 0; a fraction or an infinity is left for the
    strict int to refuse; nothing above 2^53 - 1 in size, which a double cannot hold exactly. A boolean is refused
    here, since a strict Literal[1] would take True."""
    if isinstance(value, bool):
        raise ValueError(M_NOT_A_NUMBER)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        raise ValueError(M_NOT_SAFE)
    return value


def wire_number(value: Any) -> Any:
    """A number as JavaScript reads it: an integer becomes its double, one too large for a double is refused, and
    -0.0 is 0.0, so it is written 0.0 again. A boolean and an infinity are left for the strict float to refuse (the
    model config does not allow inf or nan)."""
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            value = float(value)
        except OverflowError:
            raise ValueError(M_NOT_FINITE) from None
    if isinstance(value, float) and value == 0:
        value = 0.0
    return value


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def real_calendar_day(text: str) -> str:
    """A time that fits its pattern also names a real day, from year 1 to 9999: no 30 February."""
    year, month, day = int(text[0:4]), int(text[5:7]), int(text[8:10])
    if year < 1 or day > calendar.monthrange(year, month)[1]:
        raise ValueError(M_CALENDAR)
    return text


def wire_length(min_units: int, max_units: int = MAX_SAFE_INTEGER) -> Any:
    """A string from `min_units` to `max_units` UTF-16 code units long, as Zod's min and max count it."""

    def check(text: str) -> str:
        if not min_units <= utf16_units(text) <= max_units:
            raise ValueError(f"holds from {min_units} to {max_units} UTF-16 code units")
        return text

    return Annotated[str, AfterValidator(check)]


WireInt = Annotated[int, BeforeValidator(wire_int)]
WireNumber = Annotated[float, BeforeValidator(wire_number)]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
UtcInstant = Annotated[str, StringConstraints(pattern=UTC_INSTANT_PATTERN), AfterValidator(real_calendar_day)]

# camelCase on the wire and only there; strict types; no infinite number
WIRE_CONFIG = ConfigDict(alias_generator=to_camel, validate_by_alias=True, validate_by_name=False, serialize_by_alias=True,
                         strict=True, extra="forbid", allow_inf_nan=False)
LOOSE_WIRE_CONFIG = ConfigDict(alias_generator=to_camel, validate_by_alias=True, validate_by_name=False,
                               serialize_by_alias=True, strict=True, extra="ignore", allow_inf_nan=False)


class WireModel(BaseModel):
    """A strict record: camelCase keys only, no unknown key, null only where the Zod field is nullable."""

    model_config = WIRE_CONFIG
    nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _no_null_for_optional(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None and key not in cls.nullable:
                    raise ValueError(f"{key} may be absent but not null")
        return data
