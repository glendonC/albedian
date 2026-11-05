"""Server-side voice intent: the rules parser, and the checks every action passes before the app may apply it.

The optional Claude path lives in `services/voice/claude.py`; when it forms no action, the rules here answer.
The backend holds no session state, so it applies nothing. Every reply fits the contract's
VoiceCommandResponseSchema (mirror: `services/contracts/voice.py`). An action that passes these checks comes
back "proposed", as the validated action, for the app's reducer to apply; a request that forms no action, or
an action these checks refuse, comes back as a rejected or needs_input outcome with no action in it, and an
action that fails the contract is refused as invalid_action with its issues, never its values. Every reply to
a request parsed here names who interpreted it, as the contract's ActionInterpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any

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
    historical_year,
    propose_action_with_claude,
)

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_ID = "req-voice-1"
# What the reducer answers for each action the app cannot do yet (packages/observation/src/reducer.ts)
NOT_IN_THE_APP_YET: dict[str, tuple[str, str]] = {
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


def _whole_word(name: str) -> re.Pattern[str]:
    """Match `name` only as a whole word, so the Moon is not found in "honeymoon"."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])")


# Every name the catalog and the ephemeris hold, longest first so "Deneb Algedi" wins over "Deneb"
_NAMED_FINDINGS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (_whole_word(star.common_name), f"finding-{star.id}")
    for star in sorted(ALL_SKY_CATALOG_STARS, key=lambda star: len(star.common_name), reverse=True)
) + tuple((_whole_word(body), f"finding-predicted-{body}") for body in PREDICTED_BODY_KEYS)


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

    session_id: str = "session-0"
    source_asset_id: str = "asset-0"
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
            session_id=str(data.get("sessionId", data.get("session_id", "session-0"))),
            source_asset_id=str(data.get("sourceAssetId", data.get("source_asset_id", "asset-0"))),
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
    """Return the first non-null value among `keys` as a string, or None when none is given."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value)
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


def _rules_intent(
    request_id: str,
    action: dict[str, Any] | None,
    reason: str | None = None,
    explanation: str | None = None,
) -> VoiceIntent:
    return VoiceIntent(request_id, action, {"parser": "rules"}, reason, explanation)


def _named_finding_id(utterance: str) -> str | None:
    """The catalog star or predicted body the utterance names as a whole word, if any."""
    lower = utterance.lower()
    return next((finding_id for pattern, finding_id in _NAMED_FINDINGS if pattern.search(lower)), None)


def _resolve_target_finding_id(utterance: str, context: ActionContext) -> str | None:
    """The object the utterance names, else the one pointed at, selected or focused, else None.

    Nothing else stands in: a request naming an object neither the catalog nor the ephemeris holds,
    with nothing in view, has no target.
    """
    named = _named_finding_id(utterance)
    if named is not None:
        return named
    if context.recent_pointer and context.recent_pointer.nearest_finding_id:
        return context.recent_pointer.nearest_finding_id
    return context.selected_finding_id or context.focused_finding_id or None


# Years are read as packages/observation/src/voiceParser.ts reads them, so both parsers read the same phrases:
# ASCII digits and word boundaries (re.ASCII), and JavaScript's whitespace in place of Python's \s.
_S = "[" + "".join(re.escape(char) for char in sorted(JAVASCRIPT_SPACES)) + "]"
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


_NO_TARGET = (
    "The request names no object the catalog or the ephemeris holds, and none is pointed at, selected "
    "or focused."
)


def _parse_with_rules(
    utterance: str, ctx: ActionContext, request_id: str, rev: int, current_year: int
) -> VoiceIntent:
    """Keyword families in a fixed order. A target comes only from a name or from the view, never a default."""
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

    if any(kw in text for kw in ("clean", "denoise", "restore", "brighten", "shadow", "noise", "enhance")):
        profile = "temporal" if "temporal" in text or "stack" in text else "star_protected"
        if "natural" in text or "tone" in text:
            profile = "natural"
        return formed(
            "media.restore",
            sourceAssetId=ctx.source_asset_id,
            profile=profile,
            constraints={"preservePeople": True, "allowSyntheticSky": False, "protectStars": True},
        )

    target = _resolve_target_finding_id(utterance, ctx)
    if any(kw in text for kw in ("3d", "space", "fly", "travel", "go to", "take me to", "enter", "beyond")):
        if target is None:
            return _rules_intent(request_id, None, "ambiguous_target", _NO_TARGET)
        return formed("space.enter", findingId=target, distancePolicy="measured_or_estimated")

    describe_kws = ("what is", "what were we", "how far", "where is", "describe", "tell me about", "identify")
    if any(kw in text for kw in describe_kws):
        if target is None:
            return _rules_intent(request_id, None, "ambiguous_target", _NO_TARGET)
        return formed("finding.describe", findingId=target, fields=["distance", "type", "motion", "evidence"])

    if target is not None and any(kw in text for kw in ("select", "focus", "lock", "point", "show me")):
        return formed("finding.select", findingId=target)

    if "predicted" in text or "planet" in text or "ephemeris" in text:
        return formed("sky.show", mode="predicted")

    if "sky" in text or "calibrate" in text or "constellation" in text:
        return formed("sky.show", mode="measured")

    if target is not None:
        return formed("finding.select", findingId=target)

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
    request_id: str = DEFAULT_REQUEST_ID,
    expected_view_revision: int | None = None,
    *,
    current_year: int | None = None,
) -> VoiceIntent:
    """Turn one utterance into at most one contract `AlbedianAction`, and name who interpreted it.

    A blank request forms no action and reaches no model. Otherwise the Claude path answers when it is
    enabled and forms a valid action, and the deterministic rules answer when it does not. `current_year`
    stands in for the UTC clock's year.
    """
    ctx = ActionContext.from_dict(context)
    rev = expected_view_revision if expected_view_revision is not None else ctx.view_revision
    # Blank by Python's measure or JavaScript's, so a question formed from what is left always fits the contract
    if all(char.isspace() or char in JAVASCRIPT_SPACES for char in utterance):
        return _rules_intent(
            request_id, None, "no_grounded_answer", "The request is empty, so there is nothing to act on."
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
    return (
        f"{star.common_name} ({star.bayer_designation} in {star.constellation}, spectral type "
        f"{star.spectral_type}, apparent magnitude {star.mag:+.2f}) is {star.median_pc:.1f} pc ({light}) "
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
    if finding_id in CATALOG_FINDING_IDS:
        return _describe_star(STAR_BY_ID[finding_id.removeprefix("finding-")])
    if finding_id in PREDICTED_FINDING_IDS:
        return _describe_body(finding_id.removeprefix("finding-predicted-").capitalize(), ctx)
    return None


def _check(
    utterance: str,
    ctx: ActionContext,
    action: dict[str, Any] | None,
    action_id: str,
    reason: str | None = None,
    explanation: str | None = None,
) -> dict[str, Any]:
    """The backend's verdict on one action, or on a request that formed none.

    In order: an unsafe request, no action, a stale revision, year 0 or a year out of range, an action
    that fails the contract, a pointer over several findings, a flight the reducer would refuse, an action
    the app cannot do yet. An action that passes all of them is proposed as the contract parses it.
    """
    lower_utt = utterance.lower()
    if any(phrase in lower_utt for phrase in UNSAFE_PHRASES):
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
        hints = {"ambiguous_target": ["Name a catalog star or planet, or point at one"], "invalid_epoch": EPOCH_ADVICE}
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

    if (
        ctx.recent_pointer is not None
        and len(ctx.recent_pointer.overlapping_candidate_ids) > 1
        and action_type in FINDING_ACTION_TYPES
        and _named_finding_id(utterance) is None
    ):
        choices = [
            {"id": cid, "label": cid.replace("finding-star-", "").capitalize()}
            for cid in ctx.recent_pointer.overlapping_candidate_ids
        ]
        return {
            "status": "needs_input",
            "actionId": action_id,
            "question": "Multiple celestial objects overlap that pointer region. Which one did you mean?",
            "choices": choices,
            "scientificExplanation": "Deictic pointer region overlaps multiple catalog objects; disambiguation required.",
        }

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
        action_id = request_id if isinstance(request_id, str) and request_id else DEFAULT_REQUEST_ID
        return _check(utterance, ctx, proposed_action, action_id)

    intent = parse_voice_intent(utterance, ctx, current_year=current_year)
    reply = _check(utterance, ctx, intent.action, intent.request_id, intent.reason, intent.explanation)
    reply["interpretation"] = intent.interpretation
    return reply
