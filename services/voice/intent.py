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
    action_issues,
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
from services.voice.names import (
    EVERYDAY_WORD_NAMES,
    JAVASCRIPT_SPACE,
    find_names,
    name_pattern,
    names_southern_cross,
    read_wording,
    southern_cross_stars,
    spoken_names,
    star_name_table,
    unbaked_object,
    unbaked_refusal,
    whole_word,
)

logger = logging.getLogger(__name__)


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
_STAR_NAMES: dict[str, str] = {star.common_name.lower(): f"finding-{star.id}" for star in ALL_SKY_CATALOG_STARS}
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
            (body, whole_word(body), _Named(f"finding-predicted-{body}", None, body.capitalize()))
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
        return cls(
            x_px=float(data.get("xPx", data.get("x_px", 0.0))),
            y_px=float(data.get("yPx", data.get("y_px", 0.0))),
            at_timestamp_ms=float(data.get("atTimestampMs", data.get("at_timestamp_ms", 0.0))),
            view_revision=int(data.get("viewRevision", data.get("view_revision", 0))),
            nearest_finding_id=data.get("nearestFindingId", data.get("nearest_finding_id")),
            overlapping_candidate_ids=list(
                data.get("overlappingCandidateIds", data.get("overlapping_candidate_ids", []))
            ),
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
    def from_dict(cls, data: dict[str, Any] | ActionContext) -> ActionContext:
        if isinstance(data, ActionContext):
            return data
        ptr_raw = data.get("recentPointer", data.get("recent_pointer"))
        ptr = PointerContext.from_dict(ptr_raw) if isinstance(ptr_raw, dict) else ptr_raw
        return cls(
            session_id=_optional_str(data, "sessionId", "session_id"),
            source_asset_id=_optional_str(data, "sourceAssetId", "source_asset_id"),
            source_time_sec=float(data.get("sourceTimeSec", data.get("source_time_sec", 0.0))),
            navigation_state=str(data.get("navigationState", data.get("navigation_state", "source"))),
            calibration_state=str(data.get("calibrationState", data.get("calibration_state", "not_requested"))),
            selected_finding_id=data.get("selectedFindingId", data.get("selected_finding_id")),
            focused_finding_id=data.get("focusedFindingId", data.get("focused_finding_id")),
            recent_pointer=ptr,
            simulation_epoch_iso=_optional_str(data, "simulationEpochISO", "simulation_epoch_iso"),
            observed_at_utc=_optional_str(data, "observedAtUTC", "observed_at_utc"),
            view_revision=int(data.get("viewRevision", data.get("view_revision", 1))),
            reduced_motion=bool(data.get("reducedMotion", data.get("reduced_motion", False))),
            observer_lat=_optional_float(data, "observerLat", "observer_lat"),
            observer_lon=_optional_float(data, "observerLon", "observer_lon"),
        )


def _optional_float(data: dict[str, Any], *keys: str) -> float | None:
    """Return the first non-null value among `keys` as a float, or None when none is given."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return float(value)
    return None


def _optional_str(data: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-null, non-empty value among `keys` as a string, or None when none is given."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            text = str(value)
            return text if text else None
    return None


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


def _rules_intent(
    request_id: str,
    action: dict[str, Any] | None,
    reason: str | None = None,
    explanation: str | None = None,
    choices: tuple[dict[str, str], ...] = (),
) -> VoiceIntent:
    return VoiceIntent(request_id, action, {"parser": "rules"}, reason, explanation, choices)


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
    return bool(_said(utterance.lower())[0])


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
    refusal: str | None = None


def _view_target(context: ActionContext) -> _Target:
    """What is in view, or that nothing is."""
    in_view = _in_view(context)
    return _Target(finding_id=in_view) if in_view is not None else _Target(refusal=NOTHING_IN_VIEW)


def _named_target(named: list[_Named], context: ActionContext) -> _Target:
    """One name is that thing; two different names are a question, never a guess."""
    if len(named) == 1:
        return _Target(finding_id=_record_id(named[0], context))
    return _Target(
        choices=tuple({"id": _record_id(each, context), "label": each.label} for each in named),
        refusal="Which one did you mean: " + " or ".join(each.label for each in named) + "?",
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
    wording = read_wording(without_names, without_command)
    if wording.unknown:
        return _Target(refusal=NOT_UNDERSTOOD.format(said=wording.unknown))
    if named:
        return _named_target(named, context)
    if wording.points:
        return _view_target(context)
    if wording.describes:
        return _Target(refusal=WHICH_ONE)
    if wording.asks or alone is None:
        return None
    return _view_target(context) if alone == "view" else _Target(refusal=WHICH_ONE)


# Years are read as packages/observation/src/voiceParser.ts reads them, so both parsers read the same phrases:
# ASCII digits and word boundaries (re.ASCII), and JavaScript's whitespace in place of Python's \s.
_S = JAVASCRIPT_SPACE
_UNSAFE_SYNTHETIC = re.compile(
    r"\b(?:fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b",
    re.ASCII | re.IGNORECASE,
)


def _is_unsafe_synthetic(utterance: str) -> bool:
    lower = utterance.lower()
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
BRIGHTEST = re.compile(r"\bbrightest\b")
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
_BRIGHTEST_NEXT_WORD = re.compile(r"\bbrightest\b[\s-]*([a-z][a-z-]*)?")

# Why a request has no target, in words a person who is not an astronomer reads: no id, class or contract term.
# A word the rules do not know is no evidence about any sky, and the backend holds no photo: it says only that it
# did not understand the word, in the app parser's own sentence.
NOT_UNDERSTOOD = 'I did not understand "{said}". Say a star\'s or a planet\'s name, or select one and say "that one".'
WHICH_ONE = 'That does not say which one. Say its name, or select it and say "that one".'
NOTHING_IN_VIEW = "Nothing is selected or pointed at. Select a star or planet first, or say its name."
_SOUTHERN_CROSS_QUESTION = (
    "The Southern Cross is a constellation, not one star. Which of its stars did you mean: "
    + ", ".join(star.name for star in southern_cross_stars())
    + "?"
)
_SPACE_ENTER = re.compile(
    r"\b(?:take me (?:there|to)|fly (?:there|to)|go (?:there|to)|travel(?:\b| to)|"
    r"enter(?: (?:space|3d))?|3d space|into space|zoom into space|explore|fly|beyond)\b",
    re.ASCII,
)
_DESCRIBE = re.compile(
    r"\b(?:what is|how far|where is|describe|tell me about|identify)\b",
    re.ASCII,
)
# The describing commands that stand alone and act on what is in view; "what is", "where is" and "tell me about"
# need something after them
_DESCRIBE_ALONE = re.compile(r"\b(?:how far|describe|identify)\b", re.ASCII)
_SELECT = re.compile(r"\b(?:select|focus|lock|point|show me|that one|this one)\b", re.ASCII)
# The selecting words that are a command on their own. "Show me", "point", and "focus" or "lock" without "on",
# also open other requests ("show me the predicted sky", "the focus is off"), so they select only a thing the
# request names or points at in view; anything else is left to the rules after them, as the app's parser, which
# has no such command, reads it.
_SELECT_COMMAND = re.compile(r"\b(?:select|focus on|lock on(?:to)?|that one|this one)\b", re.ASCII)
# The restore command, from whole words. "Noise" and "shadows" ask for it only after a verb that does something
# to them, so "is that noise or a star?" proposes nothing.
_RESTORE = re.compile(
    r"\b(?:clean|denoise|restore|brighten|enhance)\b"
    r"|\b(?:reduce|remove|lower|less|fix)\b[^.?!]*?\bnoise\b"
    r"|\b(?:lift|raise|open|recover)\b[^.?!]*?\bshadows?\b",
    re.ASCII,
)
_RESTORE_TEMPORAL = re.compile(r"\b(?:temporal|stack)\b", re.ASCII)
_RESTORE_NATURAL = re.compile(r"\b(?:natural|tone)\b", re.ASCII)
_SKY_PREDICTED = re.compile(r"\b(?:predicted|planet|ephemeris)\b", re.ASCII)
# The requests for the sky that say so in full, read before any target is looked for, as the app's parser reads
# them: "what is in the sky" and "identify the stars" ask about no one star
_SKY_PHRASES = re.compile(
    r"\b(?:what were we looking at|what was overhead|show me what was overhead|"
    r"identify the stars|calibrate|register the sky|show (?:the )?sky|"
    r"what is in the sky|match the sky)\b",
    re.ASCII,
)
# Read after the target commands, like "predicted" and "planet" above: one word that asks for the measured sky
_SKY_MEASURED = re.compile(r"\bconstellation\b", re.ASCII)
_HOW_MANY = re.compile(rf"\bhow{_S}+many\b", re.ASCII)


def _parse_with_rules(
    utterance: str, ctx: ActionContext, request_id: str, rev: int, current_year: int
) -> VoiceIntent:
    """Keyword families in a fixed order. A request is about what it names: a target comes from a name, or
    from what is in view only when the request points at it or names nothing at all (`_target`)."""
    text = utterance.strip().lower()
    base = {"requestId": request_id, "expectedViewRevision": rev}

    def formed(action_type: str, **fields: Any) -> VoiceIntent:
        return _rules_intent(request_id, {"type": action_type, **base, **fields})

    if text in ("stop", "wait", "cancel", "hold on", "pause transition"):
        return formed("navigation.cancel")

    return_phrases = (
        "back to moment", "back to my video", "back to the video",
        "back to photo", "return to source", "return to original", "original recording",
    )
    if any(phrase in text for phrase in return_phrases):
        return formed("source.return", restoreAudio=True)

    if "go back" in text or "step back" in text or "halfway back" in text:
        if "halfway" in text or "partway" in text:
            return formed("history.back", mode="partial", fraction=0.5)
        return formed("history.back", mode="checkpoint")

    said = _said_year(text, current_year)
    if isinstance(said, int) and _year_refusal(said) is None:
        return formed("space.epoch", epochISO=epoch_iso(said), yearCE=said)
    if said is not None:
        return _rules_intent(request_id, None, "invalid_epoch", _year_refusal(said))

    if _RESTORE.search(text):
        if ctx.source_asset_id is None:
            return _rules_intent(
                request_id,
                None,
                "missing_source",
                "Restoring the recording needs its sourceAssetId in the request context.",
            )
        profile = "temporal" if _RESTORE_TEMPORAL.search(text) else "star_protected"
        if _RESTORE_NATURAL.search(text):
            profile = "natural"
        return formed(
            "media.restore",
            sourceAssetId=ctx.source_asset_id,
            profile=profile,
            constraints={"preservePeople": True, "allowSyntheticSky": False, "protectStars": True},
        )

    named, without_names = _said(text)
    # A southern object named but not baked, or the Southern Cross, is answered in its own words whatever else the
    # request names: it never falls through to the selection, or to a star named beside it
    unbaked = unbaked_object(text)
    if unbaked is not None:
        return _rules_intent(request_id, None, "no_grounded_answer", unbaked_refusal(unbaked))
    if names_southern_cross(text):
        return _rules_intent(request_id, None, "ambiguous_target", _SOUTHERN_CROSS_QUESTION)

    def about(target: _Target, action_type: str, **fields: Any) -> VoiceIntent:
        """The action on the target, the question between several, or the refusal that says why there is none."""
        if target.finding_id is not None:
            return formed(action_type, findingId=target.finding_id, **fields)
        return _rules_intent(request_id, None, "ambiguous_target", target.refusal, target.choices)

    if _SKY_PHRASES.search(text):
        return formed("sky.show", mode="measured")

    if _SPACE_ENTER.search(text):
        target = _target(named, without_names, ctx, _SPACE_ENTER, alone="view")
        if target is not None:
            return about(target, "space.enter", distancePolicy="measured_or_estimated")

    if _DESCRIBE.search(text):
        target = _target(
            named, without_names, ctx, _DESCRIBE, alone="view" if _DESCRIBE_ALONE.search(text) else None
        )
        if target is not None:
            return about(target, "finding.describe", fields=["distance", "type", "motion", "evidence"])

    if _SELECT.search(text):
        command = _SELECT_COMMAND.search(text) is not None
        target = _target(named, without_names, ctx, _SELECT, alone="which" if command else None)
        if target is not None and (command or target.finding_id is not None or target.choices):
            return about(target, "finding.select")

    # A thing the request names is what it is about, before a single word could read it as a request for the sky
    # ("is Rigel a planet?"), as the app's parser reads it; a word beside the name that was not understood still
    # stops it ("is Rigel brighter than Andromeda?")
    if named and not _HOW_MANY.search(text):
        target = _target(named, without_names, ctx, None, alone=None)
        if target is not None:
            return about(target, "finding.select")

    if _SKY_PREDICTED.search(text):
        return formed("sky.show", mode="predicted")

    if _SKY_MEASURED.search(text):
        return formed("sky.show", mode="measured")

    question = utterance.strip()
    # The contract counts a question's length as JavaScript does, in UTF-16 code units
    if utf16_length(question) > QUESTION_MAX_UTF16:
        return _rules_intent(
            request_id,
            None,
            "no_grounded_answer",
            "No action matches the request, and it is longer than a question can hold "
            f"({QUESTION_MAX_UTF16} characters as the app counts them).",
        )
    return formed("question.ask", question=question)


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
    ctx = ActionContext.from_dict(context)
    if request_id is None:
        request_id = new_request_id()
    rev = expected_view_revision if expected_view_revision is not None else ctx.view_revision
    # Blank by Python's measure or JavaScript's, so a question formed from what is left always fits the contract
    if all(char.isspace() or char in JAVASCRIPT_SPACES for char in utterance):
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
    lowered = utterance.lower()
    if BRIGHTEST.search(lowered):
        next_word = _BRIGHTEST_NEXT_WORD.search(lowered)
        unranked = next_word is not None and next_word.group(1) in BRIGHTEST_UNRANKED_KINDS
        return _rules_intent(
            request_id, None, "no_grounded_answer", BRIGHTEST_KIND_REFUSAL if unranked else BRIGHTEST_REFUSAL
        )

    proposal = propose_action_with_claude(utterance, ctx, request_id, rev, current_year=current_year)
    if proposal is not None:
        return VoiceIntent(request_id, proposal.action, proposal.interpretation)

    year = current_year if current_year is not None else datetime.now(timezone.utc).year
    return _parse_with_rules(utterance, ctx, request_id, rev, year)


def _rejected(action_id: str, reason: str, explanation: str, next_actions: list[str]) -> dict[str, Any]:
    return {
        "status": "rejected",
        "actionId": action_id,
        "reason": reason,
        "explanation": explanation,
        "nextActions": next_actions,
    }


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
    star = STAR_BY_ID.get(finding_id.removeprefix("finding-"))
    if star is not None:
        return _describe_star(star)
    if finding_id in PREDICTED_FINDING_IDS:
        return _describe_body(finding_id.removeprefix("finding-predicted-").capitalize(), ctx)
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
        {"id": cid, "label": cid.replace("finding-star-", "").capitalize()}
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
            if explanation == NOTHING_IN_VIEW:
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

    if action_type in FINDING_ACTION_TYPES:
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
    ctx = ActionContext.from_dict(context)
    if proposed_action is not None:
        request_id = proposed_action.get("requestId") if isinstance(proposed_action, dict) else None
        action_id = request_id if isinstance(request_id, str) and request_id else new_request_id()
        return _check(utterance, ctx, proposed_action, action_id)

    intent = parse_voice_intent(utterance, ctx, current_year=current_year)
    reply = _check(
        utterance, ctx, intent.action, intent.request_id, intent.reason, intent.explanation, intent.choices
    )
    reply["interpretation"] = intent.interpretation
    return reply
