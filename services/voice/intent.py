"""Server-side voice intent: the rules parser, and the checks every action passes before the app may apply it.

The optional Claude path lives in `services/voice/claude.py`; when it forms no action, the rules here answer.
The backend holds no session state, so it applies nothing. Every reply fits the contract's
VoiceCommandResponseSchema (mirror: `services/contracts/voice.py`). An action that passes these checks comes
back "proposed", as the validated action, for the app's reducer to apply; a request that forms no action, or
an action these checks refuse, comes back as a rejected or needs_input outcome with no action in it, and an
action that fails the contract is refused as invalid_action with its issues, never its values. Every reply to
a request parsed here names who interpreted it, as the contract's ActionInterpretation.

The rules read a request as being about what it names. Every word of it must be read: a word the rules do not
know may be a name no table holds, so the request is refused in plain words, as a word that was not understood,
whatever else it names. What is pointed at, selected or focused is taken only when the request points at it or
names nothing at all (`_target`); packages/observation/src/voiceParser.ts reads the same way, on the same words
(tests/integration/test_voice_named_targets.py). The backend holds no photo, so it never says what a sky lacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
import re
from typing import Any, Literal
import uuid

from services.contracts.voice import (
    ALBEDIAN_ACTION,
    EPOCH_MAX_YEAR_CE,
    EPOCH_MIN_YEAR_CE,
    FINDING_ACTION_TYPES,
    JAVASCRIPT_SPACES,
    QUESTION_MAX_UTF16,
    SAFE_INTEGER_MAX,
    action_issues,
    lower_case,
    refusal_sentence,
    utf16_length,
)
from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    STAR_BY_ID,
    CatalogStar,
    compute_light_travel_time_description,
)
from services.science.ephemeris import AU_KM, compute_solar_system_ephemerides
from services.voice.claude import (
    CATALOG_FINDING_IDS,
    PREDICTED_BODY_KEYS,
    PREDICTED_FINDING_IDS,
    epoch_iso,
    fresh_pointer,
    historical_year,
    propose_action_with_claude,
)
from services.voice.grammar import (
    VOICE_GRAMMAR,
    GrammarRule,
    another_request,
    blanked,
    changes_the_scene,
    conflicting_modifier,
    fields_for,
    fold,
    own_words_cut,
    rule_says,
)
from services.voice.names import (
    DESCRIBING_A_THING,
    EVERYDAY_WORD_NAMES,
    MAX_QUOTED_CHARACTERS,
    POINTING_WORDS,
    ascii_capitalized,
    JAVASCRIPT_SPACE,
    find_names,
    name_pattern,
    names_southern_cross,
    points,
    read_wording,
    southern_cross_stars,
    spoken_names,
    star_name_table,
    unbaked_object_said,
    unbaked_refusal_record,
    whole_word,
    words_of,
)

logger = logging.getLogger(__name__)
# Blank by Python's str.isspace or by JavaScript's /\s/, as fixed code points: JavaScript's list, and the five Python
# alone counts (U+001C to U+001F and U+0085), so a question formed from what is left always fits the contract
_BLANK = frozenset(JAVASCRIPT_SPACES) | frozenset("\x1c\x1d\x1e\x1f\x85")
# What JavaScript's trim() takes off both ends of a request, as fixed code points, never this Python's own whitespace
_JAVASCRIPT_TRIM = "".join(sorted(JAVASCRIPT_SPACES))


def new_request_id() -> str:
    """An id of its own for one request: no two requests share one."""
    return f"req-{uuid.uuid4().hex}"


# What the reducer answers for each action the app cannot do yet (packages/observation/src/reducer.ts)
NOT_IN_THE_APP_YET: dict[str, tuple[str, str]] = {
    "ui.overlay": ("not_connected", "Voice cannot do this in the app yet."),
    "space.regime": ("not_connected", "Voice cannot do this in the app yet."),
    "scene.show": ("not_connected", "Voice cannot do this in the app yet."),
    "audio.set": ("not_connected", "Voice cannot do this in the app yet."),
    # The view cannot show another year yet: it does not move the drawn stars to a chosen year, so a new epoch
    # would change only the words on screen
    "space.epoch": (
        "not_connected",
        "The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.",
    ),
    "question.ask": ("no_grounded_answer", "No tool can answer questions from your data yet."),
}
UNSAFE_PHRASES = ("fake star", "synthetic sky", "replace the sky", "hallucinate", "invent star")


# Every star name the pinned catalog and the baked IAU name table hold (services/voice/names.py)
_STAR_NAMES: dict[str, str] = {lower_case(star.common_name): f"finding-{star.id}" for star in ALL_SKY_CATALOG_STARS}
for _spoken, _finding_id in spoken_names():
    _STAR_NAMES.setdefault(_spoken, _finding_id)


@dataclass(frozen=True)
class _Named:
    """One thing a request can name: the record a solved sky gives it, the record a predicted sky gives it where
    that differs, and the name to show."""

    finding_id: str
    predicted_id: str | None
    label: str


# A sky that is predicted, not solved (the app sends an unverified pose as predicted_only too), holds a pinned
# star as finding-predicted-<its catalog id> (services/science/predicted_sky.py); a solved sky holds the matched
# star as finding-<its catalog id>. Only the pinned stars are ever predicted.
_PREDICTED_SKY_STATES = ("predicted_only", "approximate")
_PREDICTED_STAR_IDS = {f"finding-{star.id}": f"finding-predicted-{star.id}" for star in ALL_SKY_CATALOG_STARS}
_SHOWN_NAMES = {spoken: shown for spoken, _id, shown in star_name_table()}
# Every star name, then the bodies the ephemeris predicts; the longest name first, so "Deneb Algedi" is read
# before "Deneb" and "Algedi"
_NAME_TABLE: tuple[tuple[re.Pattern[str], _Named], ...] = tuple(
    (pattern, named)
    for _spoken, pattern, named in sorted(
        [
            (name, name_pattern(name), _Named(finding_id, _PREDICTED_STAR_IDS.get(finding_id), _SHOWN_NAMES[name]))
            for name, finding_id in _STAR_NAMES.items()
        ]
        + [
            (body, whole_word(body), _Named(f"finding-predicted-{body}", None, ascii_capitalized(body)))
            for body in PREDICTED_BODY_KEYS
        ],
        key=lambda row: (-len(row[0]), row[0]),
    )
)


# A star name that is also an everyday word, as a whole word with no cue: read as a name only beside another name
_EVERYDAY_BARE_TABLE: tuple[tuple[re.Pattern[str], _Named], ...] = tuple(
    (
        whole_word(name),
        _Named(_STAR_NAMES[name], _PREDICTED_STAR_IDS.get(_STAR_NAMES[name]), _SHOWN_NAMES[name]),
    )
    for name in sorted(EVERYDAY_WORD_NAMES, key=lambda name: (-len(name), name))
)


@dataclass(frozen=True)
class PointerContext:
    """Recent pointer or tap coordinates with candidate finding resolution."""

    x_px: float
    y_px: float
    at_timestamp_ms: float
    view_revision: int
    nearest_finding_id: str | None = None
    overlapping_candidate_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PointerContext:
        if not isinstance(data, dict):
            raise ValueError("recentPointer must be an object")
        raw_over = data.get("overlappingCandidateIds", data.get("overlapping_candidate_ids"))
        if raw_over is None:
            over: list[str] = []
        elif isinstance(raw_over, (list, tuple)) and all(isinstance(item, str) and bool(item) for item in raw_over):
            over = list(raw_over)
        else:
            raise ValueError("overlappingCandidateIds must be a list of non-empty strings")
        return cls(
            x_px=_finite_float(data, "xPx", "x_px", default=0.0),
            y_px=_finite_float(data, "yPx", "y_px", default=0.0),
            at_timestamp_ms=_finite_float(data, "atTimestampMs", "at_timestamp_ms", default=0.0),
            view_revision=_whole_revision(data, "viewRevision", "view_revision", default=0),
            nearest_finding_id=_optional_str(data, "nearestFindingId", "nearest_finding_id"),
            overlapping_candidate_ids=over,
        )


@dataclass(frozen=True)
class ActionContext:
    """Multimodal session context for deictic resolution and revision checking."""

    session_id: str | None = None
    source_asset_id: str | None = None
    source_time_sec: float = 0.0
    navigation_state: str = "source"
    calibration_state: str = "not_requested"
    selected_finding_id: str | None = None
    focused_finding_id: str | None = None
    recent_pointer: PointerContext | None = None
    # The view's epoch. No description is placed at it: with every epoch refused it is the capture time or,
    # for a recording without one, the moment the session was created, which nothing observed
    simulation_epoch_iso: str | None = None
    # The recording's capture time, as the client sends it: the moment a predicted body is placed at
    observed_at_utc: str | None = None
    view_revision: int = 1
    reduced_motion: bool = False
    observer_lat: float | None = None
    observer_lon: float | None = None

    @property
    def has_observation_moment(self) -> bool:
        """True only when the client sent the recording's capture time and place."""
        return (
            self.observed_at_utc is not None
            and self.observer_lat is not None
            and self.observer_lon is not None
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | ActionContext | None) -> ActionContext:
        if data is None:
            return cls()
        if isinstance(data, ActionContext):
            return data
        if not isinstance(data, dict):
            raise ValueError("context must be an object")
        ptr_raw = data.get("recentPointer", data.get("recent_pointer"))
        if ptr_raw is None or isinstance(ptr_raw, PointerContext):
            ptr = ptr_raw
        elif isinstance(ptr_raw, dict):
            ptr = PointerContext.from_dict(ptr_raw)
        else:
            raise ValueError("recentPointer must be an object or null")
        return cls(
            session_id=_optional_str(data, "sessionId", "session_id"),
            source_asset_id=_optional_str(data, "sourceAssetId", "source_asset_id"),
            source_time_sec=_finite_float(data, "sourceTimeSec", "source_time_sec", default=0.0, minimum=0.0),
            navigation_state=_str_field(data, "navigationState", "navigation_state", default="source"),
            calibration_state=_str_field(data, "calibrationState", "calibration_state", default="not_requested"),
            selected_finding_id=_optional_str(data, "selectedFindingId", "selected_finding_id"),
            focused_finding_id=_optional_str(data, "focusedFindingId", "focused_finding_id"),
            recent_pointer=ptr,
            simulation_epoch_iso=_optional_str(data, "simulationEpochISO", "simulation_epoch_iso"),
            observed_at_utc=_optional_str(data, "observedAtUTC", "observed_at_utc"),
            view_revision=_whole_revision(data, "viewRevision", "view_revision", default=1),
            reduced_motion=_bool_field(data, "reducedMotion", "reduced_motion", default=False),
            observer_lat=_optional_float(data, "observerLat", "observer_lat", minimum=-90.0, maximum=90.0),
            observer_lon=_optional_float(data, "observerLon", "observer_lon", minimum=-180.0, maximum=180.0),
        )


def _whole_revision(data: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        if key in data:
            whole = _as_whole(data[key])
            if whole is None or not 0 <= whole <= SAFE_INTEGER_MAX:
                raise ValueError(f"{key} must be a non-negative safe integer")
            return whole
    return default


def _finite_number(raw: Any) -> float | None:
    """`raw` as a finite float, or None for a boolean, a non-number, NaN, an infinity, or a whole number past a float's
    range (JSON carries one, and `float` of it raises OverflowError, which would leave the route as a 500)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _finite_float(data: dict[str, Any], *keys: str, default: float, minimum: float | None = None) -> float:
    for key in keys:
        if key in data:
            raw = data[key]
            value = _finite_number(raw)
            if value is None:
                raise ValueError(f"{key} must be a finite number")
            if minimum is not None and value < minimum:
                raise ValueError(f"{key} must be at least {minimum}")
            return value
    return default


def _optional_float(
    data: dict[str, Any], *keys: str, minimum: float | None = None, maximum: float | None = None
) -> float | None:
    """Return the first non-null value among `keys` as a finite float in bounds, or None when none is given."""
    for key in keys:
        if key in data:
            raw = data[key]
            if raw is None:
                continue
            value = _finite_number(raw)
            if value is None:
                raise ValueError(f"{key} must be a finite number")
            if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
                raise ValueError(f"{key} is out of bounds")
            return value
    return None


def _optional_str(data: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-null, non-empty string among `keys`, or None when none is given."""
    for key in keys:
        if key in data:
            value = data[key]
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            return value if value else None
    return None


def _str_field(data: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        if key in data and data[key] is not None:
            if not isinstance(data[key], str):
                raise ValueError(f"{key} must be a string")
            return data[key]
    return default


def _bool_field(data: dict[str, Any], *keys: str, default: bool) -> bool:
    for key in keys:
        if key in data and data[key] is not None:
            if not isinstance(data[key], bool):
                raise ValueError(f"{key} must be a boolean")
            return data[key]
    return default


@dataclass(frozen=True)
class VoiceIntent:
    """What one utterance became, and who interpreted it.

    `action` is a contract AlbedianAction, or None when none could be formed; then `reason`, a contract
    rejection reason, and `explanation` say why. `interpretation` is the contract's ActionInterpretation:
    the rules, or the model that was asked and the model whose answer it is.
    """

    request_id: str
    action: dict[str, Any] | None
    interpretation: dict[str, str]
    reason: str | None = None
    explanation: str | None = None
    # The things to choose between when the request names several: then `explanation` is the question
    choices: tuple[dict[str, str], ...] = ()
    # Why the request has no target, as the contract's VoiceRefusal: then `explanation` is its sentence
    refusal: dict[str, str] | None = None


def _rules_intent(
    request_id: str,
    action: dict[str, Any] | None,
    reason: str | None = None,
    explanation: str | None = None,
    choices: tuple[dict[str, str], ...] = (),
    refusal: dict[str, str] | None = None,
) -> VoiceIntent:
    if refusal is not None:
        explanation = refusal_sentence(refusal)
    return VoiceIntent(request_id, action, {"parser": "rules"}, reason, explanation, choices, refusal)


def _said(lower_text: str) -> tuple[list[_Named], str]:
    """Each different thing the text names, in the order said, and the text with those names cut out.

    A request that names something is read for every name it may say: beside another name, a star name that is
    also an everyday word is a name without its cue ("how far is Rigel from Ran?"), so it is never dropped, as a
    word that only describes, for the name beside it. With no other name it stays an everyday word."""
    found, without_names = find_names(lower_text, _NAME_TABLE)
    if found:
        bare, without_names = find_names(without_names, _EVERYDAY_BARE_TABLE)
        if bare:
            found = sorted(found + bare, key=lambda hit: hit[0])
    different: list[_Named] = []
    for _start, named in found:
        if all(named.finding_id != seen.finding_id for seen in different):
            different.append(named)
    return different, without_names


def _names_something(utterance: str) -> bool:
    """True when the utterance names a catalog star or a predicted body."""
    return bool(_said(lower_case(utterance))[0])


def _record_id(named: _Named, context: ActionContext) -> str:
    """The id of the record a request for this thing acts on, in the sky the context says the app holds."""
    if named.predicted_id is not None and context.calibration_state in _PREDICTED_SKY_STATES:
        return named.predicted_id
    return named.finding_id


def _in_view(context: ActionContext) -> str | None:
    """What is pointed at, else selected, else focused, else None."""
    pointer = fresh_pointer(context)
    if pointer is not None:
        if pointer.nearest_finding_id:
            return pointer.nearest_finding_id
        if len(pointer.overlapping_candidate_ids) == 1:
            return pointer.overlapping_candidate_ids[0]
    return context.selected_finding_id or context.focused_finding_id or None


@dataclass(frozen=True)
class _Target:
    """What a request is about: one record, a question between several, or why it is about none."""

    finding_id: str | None = None
    choices: tuple[dict[str, str], ...] = ()
    # The question between `choices`
    question: str | None = None
    refusal: dict[str, str] | None = None


def _view_target(context: ActionContext) -> _Target:
    """What is in view, or that nothing is."""
    in_view = _in_view(context)
    return _Target(finding_id=in_view) if in_view is not None else _Target(refusal={"kind": "nothing_in_view"})


def _named_target(named: list[_Named], context: ActionContext) -> _Target:
    """One name is that thing; two different names are a question, never a guess."""
    if len(named) == 1:
        return _Target(finding_id=_record_id(named[0], context))
    return _Target(
        choices=tuple({"id": _record_id(each, context), "label": each.label} for each in named),
        question="Which one did you mean: " + " or ".join(each.label for each in named) + "?",
    )


def _target(
    named: list[_Named],
    without_names: str,
    context: ActionContext,
    command: re.Pattern[str] | None,
    *,
    alone: Literal["view", "which"] | None,
) -> _Target | None:
    """What a command is about, read from the request's own words.

    Every word must be read. A word no list holds may be a name the backend does not know, so the request is
    refused as not understood, whatever else it names: never read as being about a name said beside the word, or
    about what is in view. Then a name is that thing, and two different names are a question. With no name, what
    is pointed at, selected or focused is taken only when a word points at it, or when the request describes and
    asks nothing at all and the command, said alone, acts on what is in view (`alone` is "view": "explore", "how
    far?"). A command that needs to be told which one says so when said alone (`alone` is "which": "select").
    None means the words say nothing about a target: a question about no one thing ("what is 3d space?"), or a
    command that is none on its own (`alone` is None). `command` is the rule's own words, cut out before the
    rest is judged; None when the request holds no command.
    """
    without_command = without_names
    if command is not None:
        without_command = command.sub(lambda match: " " * len(match.group()), without_names)
    # The words that change nothing (er, the photo, can you) are plain to the target's reading too (answers-3b v4)
    assert VOICE_GRAMMAR.unchanging_pattern is not None
    wording = read_wording(without_names, blanked(VOICE_GRAMMAR.unchanging_pattern, without_command))
    if wording.unknown:
        return _Target(refusal={"kind": "not_understood", "said": wording.unknown})
    if named:
        return _named_target(named, context)
    if wording.points:
        return _view_target(context)
    # A rule that is about a named thing (alone is None) says nothing of a thing only described: the next rule reads it
    if alone is None:
        return None
    if wording.describes:
        return _Target(refusal={"kind": "which_one"})
    if wording.asks:
        return None
    return _view_target(context) if alone == "view" else _Target(refusal={"kind": "which_one"})


# Years are read as packages/observation/src/voiceParser.ts reads them, so both parsers read the same phrases:
# ASCII digits and word boundaries (re.ASCII), and JavaScript's whitespace in place of Python's \s.
_S = JAVASCRIPT_SPACE
_UNSAFE_SYNTHETIC = re.compile(
    r"\b(?:fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b",
    re.ASCII | re.IGNORECASE,
)


def _is_unsafe_synthetic(utterance: str) -> bool:
    lower = lower_case(utterance)
    return bool(_UNSAFE_SYNTHETIC.search(lower)) or any(phrase in lower for phrase in UNSAFE_PHRASES)
# A number may not continue another: no digit, and no digit and separator, before it or after it
_NUMBER = r"(?<!\d)(?<!\d[,.])(\d{1,3}(?:,\d{3})+|\d+)"
_NOT_CONTINUED = r"(?!\d|[,.]\d)"
_YEARS_AGO = re.compile(rf"{_NUMBER}{_S}+years?{_S}+ago\b", re.ASCII)
_A_THOUSAND_YEARS_AGO = re.compile(rf"(?<!half{_S})\ba{_S}+thousand{_S}+years?{_S}+ago\b", re.ASCII)
_YEARS_FROM_NOW = re.compile(rf"{_NUMBER}{_S}+years?{_S}+from{_S}+now\b", re.ASCII)
_YEAR_THEN_ERA = re.compile(rf"{_NUMBER}{_S}*(bce|bc|ce|ad)\b", re.ASCII)
_ERA_THEN_YEAR = re.compile(rf"\b(?:ad|ce){_S}+{_NUMBER}{_NOT_CONTINUED}", re.ASCII)
_YEAR_WORD = re.compile(rf"\b(?:year|epoch){_S}+(-?){_NUMBER}{_NOT_CONTINUED}", re.ASCII)
# Words that count years. Only "a thousand years ago" is read from words; any other count right before
# "years ago" or "years from now" is refused, never misread. "how many years ago..." asks, it does not count.
# The digit branch starts only where a run of digits and separators starts, so a long run is searched in
# linear time: a branch that could start at every digit of the run would retry it from each one.
_COUNT_WORDS = (
    "a", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "hundreds", "thousand", "thousands",
    "million", "millions", "billion", "billions", "few", "several", "couple", "dozen", "dozens", "some", "of",
)
_COUNTED_YEARS = re.compile(
    rf"(?:(?<![\d.,])[.,]*\d[\d.,]*|\b(?:{'|'.join(_COUNT_WORDS)}|(?<!how{_S})many)){_S}+years?{_S}+(?:ago|from{_S}+now)\b",
    re.ASCII,
)
# JavaScript's numbers hold every integer of up to 15 digits exactly, and Python reads at most 4,300 digits
# from text: a longer number is never read, and no year past 15 digits is quoted back
MAX_YEAR_DIGITS = 15
_YEAR_ZERO = "year_zero"
_TOO_LONG = "too_long"
_NOT_IN_DIGITS = "not_in_digits"

NO_YEAR_ZERO = "There is no year 0: 1 BCE is followed by 1 CE."
NOT_IN_DIGITS = "The number of years must be a whole number in digits, as in 2000 years ago."


def _era(year_ce: int) -> str:
    return f"{-year_ce} BCE" if year_ce < 0 else f"{year_ce} CE"


EPOCH_RANGE = f"{_era(EPOCH_MIN_YEAR_CE)} to {_era(EPOCH_MAX_YEAR_CE)}"
EPOCH_ADVICE = [f"Choose a year from {EPOCH_RANGE}"]


def _out_of_range(year_ce: int | None) -> str:
    """The TypeScript parser's words for a year outside the contract's range; one past 15 digits is not quoted."""
    if year_ce is None or abs(year_ce) >= 10**MAX_YEAR_DIGITS:
        return f"The year is outside the range of a space.epoch year, {EPOCH_RANGE}."
    return f"Year {_era(year_ce)} is outside the range of a space.epoch year, {EPOCH_RANGE}."


def _whole_number(digits: str) -> int | None:
    """The number `digits` says, or None past 15 significant digits."""
    significant = digits.replace(",", "").lstrip("0")
    return None if len(significant) > MAX_YEAR_DIGITS else int(significant or "0")


def _signed(year: int | None, *, negative: bool) -> int | str:
    if year is None:
        return _TOO_LONG
    if year == 0:
        return _YEAR_ZERO
    return -year if negative else year


def _said_year(text: str, current_year: int) -> int | str | None:
    """The historical year a lower-cased request names, a marker for one it names but cannot give, or None.

    "N years ago" counts back from `current_year`, across 1 BCE to 1 CE. BCE years are negative, as in the
    Claude path's space_epoch tool (500 BCE is -500). Era words count only as whole words, so the "ce" in
    "space" or "since" names no year. The markers: year 0, a number past 15 digits, and years counted in
    anything but one whole number in digits.
    """
    if match := _YEARS_AGO.search(text):
        years = _whole_number(match.group(1))
        return _TOO_LONG if years is None else historical_year(current_year - years)
    if _A_THOUSAND_YEARS_AGO.search(text):
        return historical_year(current_year - 1000)
    if match := _YEARS_FROM_NOW.search(text):
        years = _whole_number(match.group(1))
        return _TOO_LONG if years is None else current_year + years
    if match := _YEAR_THEN_ERA.search(text):
        return _signed(_whole_number(match.group(1)), negative=match.group(2) in ("bce", "bc"))
    if match := _ERA_THEN_YEAR.search(text):
        return _signed(_whole_number(match.group(1)), negative=False)
    if match := _YEAR_WORD.search(text):
        return _signed(_whole_number(match.group(2)), negative=match.group(1) == "-")
    if _COUNTED_YEARS.search(text):
        return _NOT_IN_DIGITS
    return None


def _year_refusal(said: int | str) -> str | None:
    """Why a said year forms no space.epoch, in the TypeScript parser's words; None for a year in range."""
    if said == _YEAR_ZERO:
        return NO_YEAR_ZERO
    if said == _NOT_IN_DIGITS:
        return NOT_IN_DIGITS
    if isinstance(said, str):  # a number past 15 digits
        return _out_of_range(None)
    if not EPOCH_MIN_YEAR_CE <= said <= EPOCH_MAX_YEAR_CE:
        return _out_of_range(said)
    return None


# The brightest family, read as packages/observation/src/voiceParser.ts reads it (BRIGHTEST_PATTERN). The ranking
# comes from the fluxes measured in the photo's pixels, which the app holds and this service never receives.
BRIGHTEST = re.compile(r"\bbrightest\b", re.ASCII)
BRIGHTEST_REFUSAL = (
    "Which star is brightest in a photo is read from the brightness measured in its pixels, which the voice "
    "service does not receive, so it names none."
)
# Kinds of body the measured brightness does not rank at all, as voiceParser.ts lists them (BRIGHTEST_UNRANKED_KINDS)
BRIGHTEST_UNRANKED_KINDS = (
    "planet", "planets", "moon", "moons", "galaxy", "galaxies", "nebula", "nebulae", "nebulas", "cluster", "clusters",
    "deep", "deep-sky", "comet", "comets", "asteroid", "asteroids", "satellite", "satellites", "constellation",
    "constellations",
)
BRIGHTEST_KIND_REFUSAL = (
    "Brightness measured in a photo ranks stars and points of light only, so a brightest planet, moon, galaxy, "
    "nebula or cluster is not named."
)
_BRIGHTEST_NEXT_WORD = re.compile(rf"\bbrightest\b(?:{_S}|-)*([a-z][a-z-]*)?", re.ASCII)

# Why a request has no target, in words a person who is not an astronomer reads: no id, class or contract term.
# A word the rules do not know is no evidence about any sky, and the backend holds no photo: it says only that it
# did not understand the word, in the app parser's own sentence.
NOT_UNDERSTOOD = refusal_sentence({"kind": "not_understood", "said": "{said}"})
WHICH_ONE = refusal_sentence({"kind": "which_one"})
NOTHING_IN_VIEW = refusal_sentence({"kind": "nothing_in_view"})
_SOUTHERN_CROSS_QUESTION = (
    "The Southern Cross is a constellation, not one star. Which of its stars did you mean: "
    + ", ".join(star.name for star in southern_cross_stars())
    + "?"
)
_HOW_MANY = re.compile(rf"\bhow{_S}+many\b", re.ASCII)


def _parse_with_rules(
    utterance: str, ctx: ActionContext, request_id: str, rev: int, current_year: int
) -> VoiceIntent:
    """The grammar's rules in its order. A request is about what it names: a target comes from a name, or
    from what is in view only when the request points at it or names nothing at all (`_target`)."""
    text = lower_case(utterance.strip(_JAVASCRIPT_TRIM))
    base = {"requestId": request_id, "expectedViewRevision": rev}
    # The commands, tried in the order of the voice command grammar (packages/contracts/src/data/voice-command-grammar.json),
    # which the app's parser reads too
    named, without_names = _said(text)
    reading = _Reading(text, utterance, ctx, request_id, base, current_year, named, without_names)
    # The first refusal of a sentence a rule acted on but did not account for, given when no later rule accounts for it
    not_a_request: VoiceIntent | None = None
    for rule in VOICE_GRAMMAR.rules:
        if rule.id == VOICE_GRAMMAR.names_refused_before:
            # A southern object named but not baked, or the Southern Cross, is answered in its own words whatever
            # else the request names: it never falls through to the selection, or to a star named beside it
            unbaked = unbaked_object_said(text)
            if unbaked is not None:
                return _rules_intent(request_id, None, "no_grounded_answer", refusal=unbaked_refusal_record(*unbaked))
            if names_southern_cross(text):
                return _rules_intent(request_id, None, "ambiguous_target", _SOUTHERN_CROSS_QUESTION)
        handler = _RULE_HANDLERS[rule.action]
        if rule.match in ("year", "seconds", "name", "rest"):
            intent = handler(reading, rule, rule.fixed)
        elif rule_says(rule, text):
            intent = handler(reading, rule, fields_for(rule, text))
        else:
            intent = None
        if intent is not None:
            # A sentence that changes the scene acts only when every word of it is accounted for (answers-3b v4): a
            # rule that leaves a word passes it to the later rules, and two requests are refused at once. A compare,
            # or a restore without a sourceAssetId, has no action here only because the context lacks the recording's
            # fields, so its words are still checked before missing_source is returned
            if (intent.action is not None or intent.reason == "missing_source") and changes_the_scene(VOICE_GRAMMAR, rule):
                stop = _not_one_request(reading, rule)
                if stop is None:
                    return intent
                if stop.refusal is not None and stop.refusal["kind"] == "two_requests":
                    return stop
                # The name rule's own refusal is never kept: a sentence that is more than a name is the later rules' to read
                if rule.match != "name":
                    not_a_request = not_a_request or stop
                continue
            if rule.match == "rest" and not_a_request is not None:
                return not_a_request
            return intent
    raise AssertionError("the voice command grammar ends with its rest rule, which always reads the request")


# The words a rule that changes the scene accounts for beside its own: the pointing words (that, it, there), and for a
# rule with a target, the words that describe the thing itself (bright, red, star) and the star names that are also
# everyday words (ran, fang): not measures, the recording's words or another command's words
_POINTING_ACCOUNTED = frozenset(POINTING_WORDS)
_TARGET_ACCOUNTED = _POINTING_ACCOUNTED | frozenset(DESCRIBING_A_THING) | frozenset(EVERYDAY_WORD_NAMES)
# What the code of a year rule reads: the first of these its reading takes
_YEAR_READS = (_YEARS_AGO, _A_THOUSAND_YEARS_AGO, _YEARS_FROM_NOW, _YEAR_THEN_ERA, _ERA_THEN_YEAR, _YEAR_WORD)


def _code_read(rule: GrammarRule, text: str, rest: str) -> str:
    """`rest` with what a code-read rule's own code read of `text` turned to spaces: the seconds after a seek phrase,
    or the year said."""
    if rule.match == "seconds":
        said = _seconds_said(rule, text)
        if said is not None:
            rest = rest[: said.start()] + " " * (said.end() - said.start()) + rest[said.end():]
    elif rule.match == "year":
        said = next((m for m in (pattern.search(text) for pattern in _YEAR_READS) if m is not None), None)
        if said is not None:
            rest = rest[: said.start()] + " " * (said.end() - said.start()) + rest[said.end():]
    return rest


def _not_one_request(reading: _Reading, rule: GrammarRule) -> VoiceIntent | None:
    """The refusal of a sentence the rule's action came from that is not one request, or None when it is one: a
    second request that changes the scene in it (two_requests), else the first word nothing accounts for
    (not_a_request): not the rule's own words, a name, a pointing word, for a rule with a target a describing word,
    what its code read, or a word that changes nothing (the table's unchanging words)."""
    assert VOICE_GRAMMAR.unchanging_pattern is not None
    own = own_words_cut(rule, reading.without_names)
    # The name rule has no words of its own, so a rule's words beside a name are that rule's to read, not a second request
    if rule.match != "name" and another_request(VOICE_GRAMMAR, rule, own, reading.without_names):
        return _rules_intent(reading.request_id, None, "not_one_request", refusal={"kind": "two_requests"})
    conflict = conflicting_modifier(rule, reading.without_names)
    if conflict is not None:
        return _rules_intent(reading.request_id, None, "not_one_request",
                             refusal={"kind": "not_a_request", "said": words_of(conflict)[0][:MAX_QUOTED_CHARACTERS]})
    rest = _code_read(rule, reading.text, blanked(VOICE_GRAMMAR.unchanging_pattern, own))
    accounted = _TARGET_ACCOUNTED if rule.target is not None else _POINTING_ACCOUNTED
    word = next((word for word in words_of(rest) if word not in accounted), None)
    if word is None:
        return None
    return _rules_intent(reading.request_id, None, "not_one_request",
                         refusal={"kind": "not_a_request", "said": word[:MAX_QUOTED_CHARACTERS]})


@dataclass(frozen=True)
class _Reading:
    """One request as the rules read it: its words in lower case, its context, and the names it says."""

    text: str
    utterance: str
    ctx: ActionContext
    request_id: str
    base: dict[str, Any]
    current_year: int
    named: list[_Named]
    without_names: str

    def formed(self, action_type: str, **fields: Any) -> VoiceIntent:
        return _rules_intent(self.request_id, {"type": action_type, **self.base, **fields})

    def about(self, rule: GrammarRule, action_type: str, act: Any) -> VoiceIntent | None:
        """The action on the target the rule finds, the question between several, the refusal that says why there
        is none, or None when the rule says nothing about a target (its target is about or named and the words name
        or point at nothing; a named rule also passes on a refusal)."""
        alone = rule.target if rule.target in ("view", "which") else None
        # A rule read from phrases cuts its own words before the rest is read; the name rule has none
        target = _target(self.named, self.without_names, self.ctx, rule.pattern, alone=alone)
        if target is None:
            return None
        if rule.target == "named" and target.finding_id is None and not target.choices:
            return None
        if target.finding_id is not None:
            return self.formed(action_type, **act(target.finding_id))
        return _rules_intent(
            self.request_id, None, "ambiguous_target", target.question, target.choices, refusal=target.refusal
        )


def _epoch(reading: _Reading, rule: GrammarRule, fields: dict[str, Any]) -> VoiceIntent | None:
    said = _said_year(reading.text, reading.current_year)
    if isinstance(said, int) and _year_refusal(said) is None:
        return reading.formed("space.epoch", epochISO=epoch_iso(said), yearCE=said)
    if said is not None:
        return _rules_intent(reading.request_id, None, "invalid_epoch", _year_refusal(said))
    return None


def _restore(reading: _Reading, rule: GrammarRule, fields: dict[str, Any]) -> VoiceIntent:
    if reading.ctx.source_asset_id is None:
        return _rules_intent(
            reading.request_id, None, "missing_source", "Restoring the recording needs its sourceAssetId in the request context."
        )
    return reading.formed(
        "media.restore",
        sourceAssetId=reading.ctx.source_asset_id,
        **fields,
        constraints={"preservePeople": True, "allowSyntheticSky": False, "protectStars": True},
    )


def _seconds_said(rule: GrammarRule, text: str) -> re.Match[str] | None:
    """A seek phrase and the seconds said after it, as the seek rule reads them."""
    assert rule.pattern is not None
    return re.search(rf"(?:{rule.pattern.pattern}){_S}+(?:to{_S}+)?(\d+(?:\.\d+)?){_S}*(?:s|sec|seconds)?\b", text, re.ASCII)


def _seek(reading: _Reading, rule: GrammarRule, fields: dict[str, Any]) -> VoiceIntent | None:
    assert rule.pattern is not None
    said = _seconds_said(rule, reading.text)
    if said is None:
        return None
    return reading.formed("media.seek", sourceTimeSec=float(said.group(1)), preserveAudioSync=True)


def _select(reading: _Reading, rule: GrammarRule, fields: dict[str, Any]) -> VoiceIntent | None:
    # The name rule: a thing the request names is what it is about, before a single word could read it as a request
    # for the sky ("is Rigel a planet?"); a word beside the name that was not understood still stops it
    if rule.match == "name" and (not reading.named or _HOW_MANY.search(reading.text)):
        return None
    return reading.about(rule, "finding.select", lambda finding_id: {"findingId": finding_id})


def _question(reading: _Reading, rule: GrammarRule, fields: dict[str, Any]) -> VoiceIntent:
    question = reading.utterance.strip(_JAVASCRIPT_TRIM)
    # The contract counts a question's length as JavaScript does, in UTF-16 code units
    if utf16_length(question) > QUESTION_MAX_UTF16:
        return _rules_intent(
            reading.request_id,
            None,
            "no_grounded_answer",
            "No action matches the request, and it is longer than a question can hold "
            f"({QUESTION_MAX_UTF16} characters as the app counts them).",
        )
    return reading.formed("question.ask", question=question)


# One handler per contract action the grammar names; each reads the rule's fixed fields
_RULE_HANDLERS: dict[str, Any] = {
    "navigation.cancel": lambda r, rule, f: r.formed("navigation.cancel"),
    "history.back": lambda r, rule, f: r.formed("history.back", **f),
    "source.return": lambda r, rule, f: r.formed("source.return", restoreAudio=True),
    "space.epoch": _epoch,
    "ui.overlay": lambda r, rule, f: r.formed("ui.overlay", **f),
    # The voice service receives no variants of the recording, so it cannot name the two to compare
    "media.compare": lambda r, rule, f: _rules_intent(
        r.request_id, None, "missing_source", "Comparing needs the recording's variants, which the request context does not carry."
    ),
    "media.restore": _restore,
    "media.seek": _seek,
    "sky.show": lambda r, rule, f: r.formed("sky.show", **{"mode": "measured", **f}),
    "space.viewpoint": lambda r, rule, f: r.about(
        rule, "space.viewpoint", lambda finding_id: {"targetFindingId": finding_id, "units": "pc", "offsetParsec": [12, 8, -5], **f}
    ),
    "finding.describe": lambda r, rule, f: r.about(rule, "finding.describe", lambda finding_id: {"findingId": finding_id, **f}),
    "space.enter": lambda r, rule, f: r.about(
        rule, "space.enter", lambda finding_id: {"findingId": finding_id, "distancePolicy": "measured_or_estimated"}
    ),
    "finding.select": _select,
    "question.ask": _question,
}
# The rules of the voice command grammar this parser reads: every one whose action has a handler here
HANDLED_RULES = frozenset(rule.id for rule in VOICE_GRAMMAR.rules if rule.action in _RULE_HANDLERS)


def parse_voice_intent(
    utterance: str,
    context: ActionContext | dict[str, Any],
    request_id: str | None = None,
    expected_view_revision: int | None = None,
    *,
    current_year: int | None = None,
) -> VoiceIntent:
    """Turn one utterance into at most one contract `AlbedianAction`, and name who interpreted it.

    A blank request forms no action and reaches no model. Otherwise the Claude path answers when it is
    enabled and forms a valid action, and the deterministic rules answer when it does not. `current_year`
    stands in for the UTC clock's year. A request with no `request_id` gets an id of its own.
    """
    # The one way in: each typographic apostrophe or quote is read as its plain one before anything reads the words
    utterance = fold(VOICE_GRAMMAR, utterance)
    ctx = ActionContext.from_dict(context)
    if request_id is None:
        request_id = new_request_id()
    rev = expected_view_revision if expected_view_revision is not None else ctx.view_revision
    # Blank by Python's measure or JavaScript's, so a question formed from what is left always fits the contract
    if all(char in _BLANK for char in utterance):
        return _rules_intent(
            request_id, None, "no_grounded_answer", "The request is empty, so there is nothing to act on."
        )
    if _is_unsafe_synthetic(utterance):
        return _rules_intent(
            request_id,
            None,
            "unsafe_synthetic_edit",
            "Albedian never fabricates or replaces sky observations with synthetic stars.",
        )
    # Before any model and any rule, so neither names a star, or the selection, in place of a measured ranking
    lowered = lower_case(utterance)
    if BRIGHTEST.search(lowered):
        next_word = _BRIGHTEST_NEXT_WORD.search(lowered)
        unranked = next_word is not None and next_word.group(1) in BRIGHTEST_UNRANKED_KINDS
        return _rules_intent(
            request_id, None, "no_grounded_answer", BRIGHTEST_KIND_REFUSAL if unranked else BRIGHTEST_REFUSAL
        )

    # The names and words are read first, by the rules: the model never widens what a sentence may act on
    year = current_year if current_year is not None else datetime.now(timezone.utc).year
    rules = _parse_with_rules(utterance, ctx, request_id, rev, year)
    if _the_words_decide(rules):
        return rules
    proposal = propose_action_with_claude(utterance, ctx, request_id, rev, current_year=current_year)
    if proposal is not None and _names_or_points_at(proposal.action, utterance, ctx):
        return VoiceIntent(request_id, proposal.action, proposal.interpretation)
    return rules


# The refusals the words of a sentence decide: a word not understood, a name the data does not hold or voice cannot
# reach, or a sentence that is not one request. No model is asked about such a sentence
_WORD_OR_NAME_KINDS = frozenset({"not_understood", "deep_sky_out_of_reach", "no_named_row", "not_a_request", "two_requests"})


def _the_words_decide(rules: VoiceIntent) -> bool:
    """True when the rules stop the sentence for a word or a name, or it names several things and asks which:
    what any model answered would be a guess about the very words that stopped it."""
    if rules.refusal is not None and rules.refusal["kind"] in _WORD_OR_NAME_KINDS:
        return True
    return bool(rules.choices) or rules.explanation == _SOUTHERN_CROSS_QUESTION


def _names_or_points_at(action: dict[str, Any], utterance: str, ctx: ActionContext) -> bool:
    """True when a model's action acts on nothing, or on a thing the sentence names or points at."""
    finding_id = action.get("findingId")
    if finding_id is None:
        return True
    named, without_names = _said(lower_case(utterance))
    # The record the rules would act on for each name: in a predicted sky a star's predicted record, never the solved
    # one, which that sky does not hold
    allowed = {_record_id(each, ctx) for each in named}
    if points(without_names):
        in_view = _in_view(ctx)
        if in_view is not None:
            allowed.add(in_view)
    return finding_id in allowed


def _rejected(
    action_id: str, reason: str, explanation: str, next_actions: list[str], refusal: dict[str, str] | None = None
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "status": "rejected",
        "actionId": action_id,
        "reason": reason,
        "explanation": explanation,
        "nextActions": next_actions,
    }
    if refusal is not None:
        reply["refusal"] = dict(refusal)
    return reply


def _as_whole(value: Any) -> int | None:
    """`value` as a whole number, or None: text, booleans and fractions are not whole numbers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _space_enter_refusal(action: dict[str, Any], ctx: ActionContext) -> tuple[str, str, list[str]] | None:
    """Why the reducer would refuse this flight, as far as the backend can tell, or None.

    The reducer flies only to a catalog-matched finding with a distance interval. Only a calibrated sky
    has catalog-matched findings, and the plate solver names each one after its catalog star.
    """
    if ctx.calibration_state != "calibrated":
        return (
            "unresolved_sky",
            "Entering 3D catalog space needs a calibrated sky with catalog-matched stars; the sky's "
            f"calibration state is '{ctx.calibration_state}'.",
            ["Calibrate the sky first"],
        )
    finding_id = str(action.get("findingId", ""))
    if finding_id not in CATALOG_FINDING_IDS:
        what = "a predicted ephemeris position" if finding_id in PREDICTED_FINDING_IDS else "not a catalog star"
        return (
            "no_distance_estimate",
            f"3D catalog space flies only to a catalog-matched star with a distance interval, and "
            f"{finding_id} is {what}.",
            ["Select a catalog-matched star first"],
        )
    return None


def _epoch_refusal(action: dict[str, Any]) -> tuple[str, str, list[str]] | None:
    """Why a space.epoch year cannot stand, as the rules say it: year 0, or a whole year outside the contract's
    range. None otherwise.

    A missing or malformed year fails the contract itself, and is refused as invalid_action.
    """
    year_ce = _as_whole(action.get("yearCE"))
    if year_ce is None:
        return None
    refusal = _year_refusal(_YEAR_ZERO if year_ce == 0 else year_ce)
    return None if refusal is None else ("invalid_epoch", refusal, list(EPOCH_ADVICE))


def _describe_star(star: CatalogStar) -> str:
    light = compute_light_travel_time_description(star.median_pc)["description"]
    spectral_clause = f", spectral type {star.spectral_type}" if star.spectral_type is not None else ""
    # A star with no IAU name shows its Bayer designation as its name: not twice
    designation = "" if star.bayer_designation == star.common_name else f"{star.bayer_designation} "
    return (
        f"{star.common_name} ({designation}in {star.constellation}{spectral_clause}, "
        f"apparent magnitude {star.mag:+.2f}) is {star.median_pc:.1f} pc ({light}) "
        f"away [{star.lower_pc:.1f} to {star.upper_pc:.1f} pc]. Distance: {star.distance_source}. Proper "
        f"motion ({star.pm_ra_mas_yr:+.1f}, {star.pm_dec_mas_yr:+.1f}) mas/yr. Catalog values for "
        f"{star.catalog_source_id}, not measured from this recording."
    )


def _describe_body(name: str, ctx: ActionContext) -> str | None:
    """A predicted body at the recording's capture time and place, or why it has no position.

    The text says the moment is the capture time and never writes it, nor the place: they stay in their own
    fields, out of any free text.
    """
    when, lat, lon = ctx.observed_at_utc, ctx.observer_lat, ctx.observer_lon
    if when is None or lat is None or lon is None:
        return (
            f"{name}'s position depends on when and where you are observing, "
            f"and no observation time and location are set yet."
        )
    try:
        eph = compute_solar_system_ephemerides(obs_utc_iso=when, lat_deg=lat, lon_deg=lon, bodies=(name,))[0]
    except ValueError as exc:
        # The error's message can hold the requested moment, so only its kind is logged
        logger.warning("No ephemeris position for %s at the recording's capture time (%s)", name, type(exc).__name__)
        return None
    light = compute_light_travel_time_description(eph.distance_pc)["description"]
    distance = f"{eph.distance_au * AU_KM:,.0f} km" if eph.object_type == "moon" else f"{eph.distance_au:.4f} AU"
    bound_km = (eph.distance_upper_au - eph.distance_au) * AU_KM
    return (
        f"{eph.name} (evidence class predicted) is {distance} ({light}) away, to within {bound_km:,.0f} km, "
        f"at altitude {eph.alt_deg:+.1f}° and azimuth {eph.az_deg:.1f}° (RA {eph.ra_deg:.2f}°, Dec "
        f"{eph.dec_deg:+.2f}°) at the recording's capture time, to within about "
        f"{eph.uncertainty_arcsec:,.0f} arcsec. Model: {eph.method}. " + " ".join(eph.limitations)
    )


def _describe_finding(finding_id: str, ctx: ActionContext) -> str | None:
    """The catalog row or ephemeris prediction behind a finding id, in its source's words; None if neither holds it."""
    # The backend holds catalog rows for the pinned stars only; a star named from the IAU table is described
    # by the app from its own solved finding
    star = STAR_BY_ID.get(finding_id.removeprefix("finding-predicted-").removeprefix("finding-"))
    if star is not None:
        return _describe_star(star)
    if finding_id in PREDICTED_FINDING_IDS:
        return _describe_body(ascii_capitalized(finding_id.removeprefix("finding-predicted-")), ctx)
    return None


def _southern_cross_choice(action_id: str) -> dict[str, Any]:
    """Which of the Southern Cross's five named stars, as the baked IAU name table holds them."""
    return {
        "status": "needs_input",
        "actionId": action_id,
        "question": _SOUTHERN_CROSS_QUESTION,
        "choices": [{"id": star.finding_id, "label": star.name} for star in southern_cross_stars()],
    }


def _pointer_disambiguation(
    utterance: str, pointer: PointerContext | None, action_id: str
) -> dict[str, Any] | None:
    if pointer is None or len(pointer.overlapping_candidate_ids) <= 1 or _names_something(utterance):
        return None
    choices = [
        {"id": cid, "label": ascii_capitalized(cid.replace("finding-star-", ""))}
        for cid in pointer.overlapping_candidate_ids
    ]
    return {
        "status": "needs_input",
        "actionId": action_id,
        "question": "Multiple celestial objects overlap that pointer region. Which one did you mean?",
        "choices": choices,
        "scientificExplanation": "Deictic pointer region overlaps multiple catalog objects; disambiguation required.",
    }


def _check(
    utterance: str,
    ctx: ActionContext,
    action: dict[str, Any] | None,
    action_id: str,
    reason: str | None = None,
    explanation: str | None = None,
    choices: tuple[dict[str, str], ...] = (),
    refusal: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The backend's verdict on one action, or on a request that formed none.

    In order: an unsafe request, no action, a stale revision, year 0 or a year out of range, an action
    that fails the contract, a pointer over several findings, a flight the reducer would refuse, an action
    the app cannot do yet. An action that passes all of them is proposed as the contract parses it.
    """
    if _is_unsafe_synthetic(utterance):
        return {
            **_rejected(
                action_id,
                "unsafe_synthetic_edit",
                "Albedian never fabricates or replaces sky observations with synthetic stars.",
                ["media.restore(star_protected)", "sky.show(predicted)"],
            ),
            "scientificExplanation": (
                "Scientific Integrity Policy: Albedian never injects synthetic catalog stars "
                "into sensor restoration outputs (`allowSyntheticSky: false`)."
            ),
        }

    if action is None:
        if choices and explanation:
            # The request names several things: the question between them, never one picked for it
            return {
                "status": "needs_input",
                "actionId": action_id,
                "question": explanation,
                "choices": [dict(choice) for choice in choices],
            }
        if reason == "ambiguous_target":
            if explanation == _SOUTHERN_CROSS_QUESTION:
                return _southern_cross_choice(action_id)
            # Only a request that points, or names nothing, is asked which of the findings under the pointer it
            # means: one that names or describes something else is refused, never offered what the pointer overlaps
            if refusal is not None and refusal["kind"] == "nothing_in_view":
                disambiguation = _pointer_disambiguation(utterance, fresh_pointer(ctx), action_id)
                if disambiguation is not None:
                    return disambiguation
        hints = {
            "ambiguous_target": ["Name a catalog star or planet, or point at one"],
            "invalid_epoch": EPOCH_ADVICE,
            "missing_source": ["Send the active recording's sourceAssetId in the request context"],
        }
        return _rejected(
            action_id,
            reason or "no_grounded_answer",
            explanation or "The request formed no action.",
            list(hints.get(reason or "", [])),
            refusal,
        )

    if not isinstance(action, dict):
        return {
            **_rejected(action_id, "invalid_action", "The action does not match the contract.", []),
            "issues": action_issues(action),
        }
    action_type = action.get("type")
    # A revision that is not a whole number fails the contract below, as invalid_action
    expected_rev = _as_whole(action.get("expectedViewRevision"))
    if expected_rev is not None and expected_rev != ctx.view_revision:
        return {
            **_rejected(
                action_id,
                "stale_revision",
                f"Action expected viewRevision {expected_rev}, but current session is at revision {ctx.view_revision}.",
                ["Refresh context and retry command"],
            ),
            "scientificExplanation": "Stale viewRevision rejected to preserve state machine determinism.",
        }

    if action_type == "space.epoch":
        refusal = _epoch_refusal(action)
        if refusal is not None:
            return _rejected(action_id, *refusal)

    # The boundary on what a model or a client may propose: an action that fails the contract goes no
    # further, and the reply names where it fails, never what it held
    issues = action_issues(action)
    if issues:
        return {
            **_rejected(action_id, "invalid_action", "The action does not match the contract.", []),
            "issues": issues,
        }

    if action_type in (*FINDING_ACTION_TYPES, "space.viewpoint"):
        disambiguation = _pointer_disambiguation(utterance, fresh_pointer(ctx), action_id)
        if disambiguation is not None:
            return disambiguation

    refusal = None
    if action_type == "space.enter":
        refusal = _space_enter_refusal(action, ctx)
    elif action_type in NOT_IN_THE_APP_YET:
        refusal = (*NOT_IN_THE_APP_YET[action_type], [])
    if refusal is not None:
        return _rejected(action_id, *refusal)

    # As the contract parses it: unknown keys dropped and whole numbers written as integers
    valid = ALBEDIAN_ACTION.dump_python(ALBEDIAN_ACTION.validate_python(action), by_alias=True, exclude_unset=True)
    reply: dict[str, Any] = {
        "status": "proposed",
        "actionId": action_id,
        "proposedAction": valid,
        "visualStatus": f"Checked {action_type}; not applied",
    }
    if action_type in FINDING_ACTION_TYPES:
        description = _describe_finding(valid["findingId"], ctx)
        if description is not None:
            reply["scientificExplanation"] = description
    return reply


def check_voice_command(
    utterance: str,
    context: ActionContext | dict[str, Any],
    proposed_action: dict[str, Any] | None = None,
    *,
    current_year: int | None = None,
) -> dict[str, Any]:
    """Check one voice command and say what the app may do with it. Nothing here applies an action.

    An action that passes every check comes back with status "proposed", carrying the validated action and
    no state version, since only the app's reducer changes state; otherwise the reply is the contract's
    rejected or needs_input outcome, with no action in it, so the client keeps its own request. The reply
    names who interpreted the utterance when the backend parsed it rather than receiving the client's own
    action.
    """
    utterance = fold(VOICE_GRAMMAR, utterance)
    ctx = ActionContext.from_dict(context)
    if proposed_action is not None:
        request_id = proposed_action.get("requestId") if isinstance(proposed_action, dict) else None
        action_id = request_id if isinstance(request_id, str) and request_id else new_request_id()
        return _check(utterance, ctx, proposed_action, action_id)

    intent = parse_voice_intent(utterance, ctx, current_year=current_year)
    reply = _check(
        utterance,
        ctx,
        intent.action,
        intent.request_id,
        intent.reason,
        intent.explanation,
        intent.choices,
        intent.refusal,
    )
    reply["interpretation"] = intent.interpretation
    return reply
