"""Answer a person's question about a stored photo from its stored solution: the reader behind
POST /api/voice/answer/{sha256}.

What was asked is read from the words by fixed tables (QUESTION_PHRASES, REFUSING_WORDS, and the closed class of
English negators NEGATORS, whole words of the request folded and lowered by the tracked Unicode 15.1 tables); what is
answered comes only from the stored ObservationSolution's own fields, at the paths the answer contract lists
(services/contracts/voice.py SOLUTION_FIELD_SOURCES), or from the deterministic lunar phase service
(services/science/radiometry.py) for the solution's capture time. Each statement's evidence class is the one its source
decides (CLASS_RULES), and a sentence that speaks of a predicted or simulated value says so in its own words. No model
reads the question here. The route answers only a question that asks for exactly one kind about exactly one thing;
anything else, and anything the photo's data does not answer, is the contract's refusal with its reason and one fixed
sentence. Nothing here logs.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
import math
import re
from typing import Any

from pydantic import ValidationError

from services.contracts import voice as contract
from services.contracts.voice import lower_case
from services.science import radiometry
from services.voice import intent
from services.voice.grammar import VOICE_GRAMMAR, fold

# What a question asks, by the words that ask it. Every kind with a phrase in the request is asked; identity's
# phrases ("what is that") also stand in front of another kind's ("what is that star's distance"), so identity counts
# only when no other kind is asked
QUESTION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("moon_phase", ("phase of the moon", "moon phase", "moon's phase", "what phase", "full moon", "new moon",
                    "half moon", "how much of the moon", "moon lit")),
    ("confidence", ("how sure", "how confident", "confidence", "how accurate", "how reliable", "can i trust",
                    "how certain")),
    ("photo_contents", ("what is in this photo", "what's in this photo", "what is in the photo", "what's in the photo",
                        "in this picture", "how many stars", "what can you see", "what do you see")),
    ("distance", ("how far", "distance", "light years", "parsecs", "how distant")),
    ("colour_temperature", ("what colour", "what color", "colour", "color", "temperature", "how hot", "spectral")),
    ("brightness", ("how bright", "brightness", "magnitude", "how faint", "how dim")),
    ("constellation", ("constellation",)),
    ("sky_position", ("where is", "how high", "altitude", "azimuth", "coordinates", "right ascension", "declination",
                      "where in the sky")),
    ("identity", ("what is that", "what's that", "which star", "what star", "what is it", "what's it", "what is this",
                  "what's this", "identify", "name of", "what object")),
)
_WIDE_KIND = "identity"
# The closed class of English negators (not, every n't form with and without its apostrophe, cannot, no, never, none,
# nothing, neither, nor, without, except, anything but, other than, instead of); right before a comma a negator is an
# interjection ("no, how far is it?") and does not negate the clause
NEGATORS: tuple[str, ...] = (
    "not", "don't", "doesn't", "didn't", "won't", "wouldn't", "can't", "couldn't", "shouldn't", "isn't", "aren't",
    "wasn't", "weren't", "haven't", "hasn't", "hadn't", "mustn't", "needn't", "dont", "doesnt", "didnt", "wont",
    "wouldnt", "cant", "couldnt", "shouldnt", "isnt", "arent", "wasnt", "werent", "havent", "hasnt", "hadnt",
    "mustnt", "neednt", "cannot", "no", "never", "none", "nothing", "neither", "nor", "without", "except",
    "anything but", "other than", "instead of",
)
_NEGATOR_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in sorted(NEGATORS, key=len, reverse=True)) + r")\b(?!,)", re.ASCII
)
# Words that make a question another question than any kind answers (the lead's ruling, answers-4b v2): a sentence
# that holds one is refused. A superlative is a word of at least five letters ending in -est but for NOT_SUPERLATIVE,
# or one of the words listed. Its limit: an irregular superlative or a comparison the lists do not hold ("the pick of
# them") is not seen.
REFUSING_WORDS: dict[str, tuple[str, ...]] = {
    "superlative": ("most", "least", "worst", "best"),
    "comparison": ("than", "more", "less", "compare", "compared", "comparing", "comparison", "versus", "vs"),
    "relation": ("between", "apart", "among", "each other", "relative to"),
}
# Words of five or more letters ending in -est that rank nothing, each one a question about one thing may say
NOT_SUPERLATIVE = ("northwest", "southwest", "interest", "honest", "request", "suggest", "guest", "quest", "forest",
                   "harvest", "modest", "manifest")
_SUPERLATIVE = re.compile(r"\b[a-z]+est\b", re.ASCII)
_REFUSING = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for words in REFUSING_WORDS.values() for w in sorted(words, key=len, reverse=True))
    + r")\b", re.ASCII)
# A clause: the words between two of these marks. A negator takes away the kinds asked in its own clause
_CLAUSE_MARK = re.compile(r"[,;.!?]")
# The quantities stated for a question about one finding, in this order, each from its path in the stored solution
FINDING_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "identity": (("catalogRef", "commonName"), ("catalogRef", "objectType")),
    "distance": (("catalogRef", "bailerJonesDistancePc", "median"), ("inferredDistancePc", "median")),
    "brightness": (("catalogRef", "apparentMagnitude"), ("sourceDetection", "snr")),
    "colour_temperature": (("catalogRef", "colorIndexBpRp"), ("catalogRef", "spectralType")),
    "constellation": (("catalogRef", "constellation"),),
    "sky_position": (("skyPosition", "altDeg"), ("skyPosition", "azDeg"), ("skyPosition", "raDeg"), ("skyPosition", "decDeg")),
}
# For the photo as a whole
SOLUTION_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "photo_contents": (("quality", "detectedCandidateCount"), ("quality", "matchedInlierCount")),
    "confidence": (("status",), ("quality", "matchedInlierCount"), ("quality", "heldOutPairCount"),
                   ("quality", "medianResidualArcsec"), ("quality", "medianResidualPx"), ("poseEstimate", "maxCenterOffsetDeg")),
}
# A sky not worked out places nothing on it
UNSOLVED = frozenset({"pending", "unresolved", "ambiguous"})
# A distance is given once: the catalog's own, else the one the solution inferred
_ONE_OF = {"distance"}
PARSEC_LIGHT_YEARS = 3.261563777  # IAU: one parsec in light years, for the sentence only
# [proposed] The words in front of a sentence that speaks of a value of this class, so what a person reads or hears
# says it was not measured in the photo. Observed and catalog-matched values need none.
CLASS_WORDS = {"predicted": "Predicted, not measured: ", "simulated": "Drawn by the app, not measured: "}
# [proposed] With no stored bounds a number keeps two significant figures
FIGURES_WITHOUT_BOUNDS = 2
# Decimal digits that hold the exact difference of any two doubles' shortest reprs (17 significant digits each, their
# exponents from -324 to 308), so a half width's leading digit is read with no rounding
EXACT_DIGITS = 700
# [proposed] The sentences of the refusals the words themselves cause
SENTENCE_RANKS = "That compares or ranks things; I answer one value of one thing."
SENTENCE_TWO_KINDS = "That asks more than one thing; ask one at a time."
SENTENCE_TWO_NAMES = "That names more than one thing; ask about one at a time."
SENTENCE_ONLY_NEGATED = "I heard what not to answer, and no question left to answer."
# [proposed] An answered value whose number is too long for one spoken sentence
SENTENCE_TOO_LONG = "That value is too long to say in one sentence."
# The recording's own noun in every sentence that names it: "clip" for a video, "photo" for a still (C34, ruled 21 Nov
# 14:40 in .lead/uiux/UIUX-voiceyet2-READY.md; "clip" is the decided noun of C14, C16, C25 and D33)
NOUNS = ("photo", "clip")


def noun_of(media_kind: str | None) -> str:
    """The noun a sentence names a recording of this SourceAsset media kind by: "clip" for a video, else "photo"."""
    return "clip" if media_kind == "video" else "photo"


def _lowered(question: str) -> str:
    return lower_case(fold(VOICE_GRAMMAR, question).strip(intent._JAVASCRIPT_TRIM))


def asked_kinds(question: str) -> tuple[set[str], bool]:
    """The kinds the words ask, less every kind a negator takes away in its own clause, and whether one was taken."""
    text = _lowered(question)
    negators = [match.start() for match in _NEGATOR_PATTERN.finditer(text)]
    asked: set[str] = set()
    negated = False
    start = 0
    for end in [mark.start() for mark in _CLAUSE_MARK.finditer(text)] + [len(text)]:
        here = {kind for kind, pattern in _PATTERNS if pattern.search(text, start, end)}
        if here and any(start <= at < end for at in negators):
            negated = True
        else:
            asked |= here
        start = end + 1
    if len(asked) > 1:
        asked.discard(_WIDE_KIND)
    return asked, negated


def ranks_or_relates(question: str) -> bool:
    """Whether the words compare, rank or relate things (REFUSING_WORDS, or a superlative ending in -est)."""
    text = _lowered(question)
    if _REFUSING.search(text):
        return True
    return any(word not in NOT_SUPERLATIVE and len(word) > 4 for word in _SUPERLATIVE.findall(text))


def question_kind(question: str) -> str:
    """The one kind the words ask, or the contract's unanswerable kind when they ask none, or more than one."""
    asked, _negated = asked_kinds(question)
    return next(iter(asked)) if len(asked) == 1 else contract.UNANSWERABLE_QUESTION_KIND


def refusal_sentence(reason: str, question: str | None = None, name: str | None = None, noun: str = "photo") -> str:
    """The one sentence of a refusal, for its reason (the name no finding has, when there is one), naming the
    recording by its noun. The sky not worked out says no "yet": it is said while a solve runs and after one ended
    without a sky (C34)."""
    if reason == "not_found_in_this_sky":
        nameless = f"No finding in this {noun} has that name."
        said = f"No finding in this {noun} is named {name}." if name else nameless
        return said if contract.utf16_length(said) <= contract.ANSWER_SENTENCE_MAX_UTF16 else nameless
    return {
        "photo_not_solved": f"This {noun}'s sky has not been worked out, so nothing in it can be answered.",
        "no_time_or_place": f"That needs the {noun}'s capture time, which this {noun} does not have.",
        "not_in_the_data": f"This {noun}'s data holds no value for that.",
        "outside_the_data": f"Nothing in this {noun}'s data answers that.",
    }[reason]


def _refused(question: str, reason: str, solution: dict[str, Any] | None, name: str | None = None,
             sentence: str | None = None, noun: str = "photo") -> dict[str, Any]:
    answer: dict[str, Any] = {"status": "unanswerable", "question": question, "reason": reason,
                              "sentence": sentence or refusal_sentence(reason, question, name, noun)}
    if solution is not None and isinstance(solution.get("id"), str):
        answer["observationSolutionId"] = solution["id"]
    return answer


def _get(node: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return None
        node = node[step]
    return node


def _row(path: tuple[str, ...], finding: bool) -> dict[str, Any] | None:
    pattern = ["findings", "*", *path] if finding else list(path)
    return next((row for row in contract.SOLUTION_FIELD_SOURCES if row["path"] == pattern), None)


def _statement(subject: dict[str, Any], row: dict[str, Any], value: Any, path: list[Any]) -> dict[str, Any] | None:
    """A statement of the stored value, with the class its source decides; None when the source states nothing
    about this subject or the value is not one the contract takes."""
    key = subject["evidenceClass"] if subject["kind"] == "finding" else subject["kind"]
    decided = contract.CLASS_RULES[row["rule"]].get(key)
    if decided is None or value is None or isinstance(value, bool):
        return None
    if row["unit"] is None:
        if not isinstance(value, str):
            return None
        stated = {"kind": "text", "text": value}
    else:
        if not isinstance(value, (int, float)):
            return None
        stated = {"kind": "number", "amount": value, "unit": row["unit"]}
    statement = {"subject": subject, "quantity": row["quantity"], "value": stated, "evidenceClass": decided,
                 "source": {"kind": "solution_field", "path": path}}
    try:
        contract.ANSWER_STATEMENT.validate_python(statement)
    except ValidationError:
        return None
    return statement


def moon_statements(moment: str) -> list[dict[str, Any]]:
    """The Moon's phase for a capture time, from the lunar phase service: computed, so predicted."""
    phase = radiometry.compute_lunar_phase_and_illumination(moment)
    moon = {"kind": "ephemeris_body", "body": "Moon"}
    return [{"subject": moon, "quantity": quantity, "value": value, "evidenceClass": "predicted",
             "source": {"kind": "ephemeris", "method": "lunar_phase"}}
            for quantity, value in (
                ("illuminated_fraction", {"kind": "number", "amount": phase["illuminationFraction"], "unit": "fraction"}),
                ("phase_angle", {"kind": "number", "amount": phase["alphaDeg"], "unit": "deg"}),
                ("moon_phase_name", {"kind": "text", "text": phase["phaseName"]}))]


def _target(question: str, solution: dict[str, Any], selected: str | None) -> tuple[int | None, str | None]:
    """The index of the finding the words are about, and the name said when no finding has it. A request that names
    more than one thing is refused before this is asked."""
    findings = solution.get("findings") or []
    named, _without_names = intent._said(_lowered(question))
    if named:
        each = named[0]
        for index, finding in enumerate(findings):
            common = _get(finding, ("catalogRef", "commonName"))
            if finding.get("id") in (each.finding_id, each.predicted_id) or (
                isinstance(common, str) and lower_case(common) == lower_case(each.label)
            ):
                return index, None
        return None, each.label
    if selected is not None:
        for index, finding in enumerate(findings):
            if finding.get("id") == selected:
                return index, None
    return None, None


def readable(amount: float, bounds: tuple[float, float] | None = None) -> str:
    """A number as a person reads it, never with an exponent, and no more exact than its stored bounds allow.

    With bounds, it is rounded to the largest power of ten that is not more than half the bounds' width (the bounds
    say the value is known to about that much either way, so a finer figure says more than the data); with none, to
    FIGURES_WITHOUT_BOUNDS significant figures. Thousands are written with commas.

    The half width is read off the bounds as the decimals they are stored as (each float's repr, as the amount is),
    exactly: a float logarithm of a float difference moved the step by a factor of ten where the half width lies a hair
    either side of a power of ten (it said 300,000 for 340,000, and 5.00 for 4.9 to 5.1; AUDIT's verdict steptest.md)."""
    if amount == 0:
        return "0"
    if bounds is not None and bounds[1] > bounds[0]:
        # Wide enough that the difference of any two doubles' reprs is exact, so the leading digit is the true one
        with localcontext() as exact:
            exact.prec = EXACT_DIGITS
            exp = ((Decimal(repr(bounds[1])) - Decimal(repr(bounds[0]))) / 2).adjusted()
    else:
        exp = math.floor(math.log10(abs(amount))) - FIGURES_WITHOUT_BOUNDS + 1
    places = max(0, -exp)
    step = Decimal(10) ** exp
    rounded = (Decimal(repr(amount)) / step).quantize(Decimal(1), rounding=ROUND_HALF_EVEN) * step
    return f"{rounded:,.{places}f}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def _sentence(kind: str, statements: list[dict[str, Any]], name: str, finding: dict[str, Any] | None,
              noun: str = "photo") -> tuple[str, list[str]]:
    """The sentence a person reads, and the quantities it speaks of, naming the recording by its noun."""
    by = {s["quantity"]: s["value"] for s in statements}

    def number(quantity: str) -> float:
        return by[quantity]["amount"]

    def fitted(spoken: list[str], *choices: str) -> tuple[str, list[str]]:
        return next((c for c in choices if contract.utf16_length(c) <= contract.ANSWER_SENTENCE_MAX_UTF16),
                    SENTENCE_TOO_LONG), spoken

    if kind == "identity":
        thing = by.get("name", {}).get("text")
        kind_word = by.get("object_type", {}).get("text", "").replace("_", " ")
        spoken = [q for q in ("name", "object_type") if q in by]
        long_form = f"That is a {kind_word} whose name is too long to say here." if kind_word else SENTENCE_TOO_LONG
        return fitted(spoken, f"That is {thing}." if thing else f"That is a {kind_word}.", long_form)
    if kind == "distance":
        pc = number("distance")
        cited = next(s for s in statements if s["quantity"] == "distance")["source"]["path"]
        stored = _get(finding, tuple(cited[2:-1])) if finding is not None else None
        bounds = (stored["lower"], stored["upper"]) if isinstance(stored, dict) and _numbers(stored.get("lower"), stored.get("upper")) else None
        ly_bounds = (bounds[0] * PARSEC_LIGHT_YEARS, bounds[1] * PARSEC_LIGHT_YEARS) if bounds else None
        said = f"about {readable(pc, bounds)} parsecs away, about {readable(pc * PARSEC_LIGHT_YEARS, ly_bounds)} light years."
        return fitted(["distance"], f"{name} is {said}", f"It is {said}")
    if kind == "brightness":
        if "apparent_magnitude" in by:
            mag = f"{number('apparent_magnitude'):.2f}"
            return fitted(["apparent_magnitude"], f"{name} shines at magnitude {mag}.", f"It shines at magnitude {mag}.")
        times = _plural(round(number("detection_snr")), "time", "times")
        return fitted(["detection_snr"], f"{name} stands {times} above the {noun}'s noise.",
                      f"It stands {times} above the {noun}'s noise.")
    if kind == "colour_temperature":
        if "colour_index_bp_rp" in by:
            colour = f"{number('colour_index_bp_rp'):.2f}"
            return fitted(["colour_index_bp_rp"], f"{name} has a BP minus RP colour of {colour}.",
                          f"Its BP minus RP colour is {colour}.")
        return fitted(["spectral_type"], f"{name} is of spectral type {by['spectral_type']['text']}.",
                      "Its spectral type is in the catalog.")
    if kind == "constellation":
        return fitted(["constellation"], f"{name} is in {by['constellation']['text']}.",
                      f"It is in {by['constellation']['text']}.", "Its constellation is in the catalog.")
    if kind == "sky_position":
        if "altitude" in by and "azimuth" in by:
            up, around = f"{number('altitude'):.1f}", f"{number('azimuth'):.1f}"
            return fitted(["altitude", "azimuth"], f"{name} was {up} degrees up, at azimuth {around} degrees.",
                          f"It was {up} degrees up, at azimuth {around} degrees.")
        if "right_ascension" in by:
            ra = f"{number('right_ascension'):.2f}"
            return fitted(["right_ascension"], f"{name} lies at right ascension {ra} degrees.",
                          f"It lies at right ascension {ra} degrees.")
        if "declination" in by:
            dec = f"{number('declination'):.2f}"
            return fitted(["declination"], f"{name} lies at declination {dec} degrees.",
                          f"It lies at declination {dec} degrees.")
        if "altitude" in by:
            up = f"{number('altitude'):.1f}"
            return fitted(["altitude"], f"{name} was {up} degrees up.", f"It was {up} degrees up.")
        around = f"{number('azimuth'):.1f}"
        return fitted(["azimuth"], f"{name} was at azimuth {around} degrees.", f"It was at azimuth {around} degrees.")
    if kind == "photo_contents":
        if "detected_source_count" in by:
            found = _plural(int(number("detected_source_count")), "point of light", "points of light")
            if "matched_star_count" in by:
                matched = int(number("matched_star_count"))
                return fitted(["detected_source_count", "matched_star_count"],
                              f"This {noun} shows {found}, {matched:,} matched to catalog stars.")
            return fitted(["detected_source_count"], f"This {noun} shows {found}.")
        matched_text = _plural(int(number("matched_star_count")), "star", "stars")
        return fitted(["matched_star_count"], f"This {noun} has {matched_text} matched to catalog stars.")
    if kind == "confidence":
        status = by["solution_status"]["text"].replace("_", " ")
        if "matched_star_count" in by:
            matched = _plural(int(number("matched_star_count")), "matched star", "matched stars")
            return fitted(["solution_status", "matched_star_count"], f"The sky is {status}, from {matched}.")
        return fitted(["solution_status"], f"The sky is {status}.")
    if kind == "moon_phase":
        lit = f"{number('illuminated_fraction') * 100:.0f}"
        return fitted(["illuminated_fraction", "moon_phase_name"],
                      f"The Moon was {lit} percent lit, {by['moon_phase_name']['text']}.")
    raise ValueError(f"no sentence for {kind}")


def _numbers(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


# The first words of a sentence that lower-case after the class words; a name keeps its capital
_LOWERED_OPENERS = ("That ", "It ", "Its ", "The ", "This ")


def with_class_words(sentence: str, classes: set[str]) -> str:
    """The sentence with the words that say a spoken value is predicted or simulated (simulated first: a drawn
    thing is drawn, whatever else), when they fit; else the shortest form that still says so."""
    for evidence in ("simulated", "predicted"):
        if evidence in classes:
            body = sentence[0].lower() + sentence[1:] if sentence.startswith(_LOWERED_OPENERS) else sentence
            said = CLASS_WORDS[evidence] + body
            if contract.utf16_length(said) <= contract.ANSWER_SENTENCE_MAX_UTF16:
                return said
            return CLASS_WORDS[evidence] + "the value is in the answer's statements."
    return sentence


def answer_question(question: str, solution: dict[str, Any] | None, selected: str | None = None, *,
                    noun: str = "photo") -> dict[str, Any]:
    """The contract VoiceAnswer to `question` about the recording whose stored solution is `solution` (None when none
    is stored), with `selected` the finding in view, if any, every sentence naming the recording by `noun` (NOUNS)."""
    if noun not in NOUNS:
        raise ValueError("a recording is named a photo or a clip")
    unanswerable = contract.UNANSWERABLE_QUESTION_KIND
    asked, negated = asked_kinds(question)
    if ranks_or_relates(question):
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_RANKS, noun=noun)
    if len(asked) > 1:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_TWO_KINDS, noun=noun)
    if not asked:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_ONLY_NEGATED if negated else None,
                        noun=noun)
    kind = next(iter(asked))
    said_names = intent._said(_lowered(question))[0]
    if len(said_names) > 1:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_TWO_NAMES, noun=noun)
    if kind == "moon_phase" and any(each.finding_id != "finding-predicted-moon" for each in said_names):
        return _refused(unanswerable, "outside_the_data", solution, noun=noun)
    if solution is None or solution.get("status") in UNSOLVED:
        return _refused(kind, "photo_not_solved", solution, noun=noun)
    statements: list[dict[str, Any]] = []
    name = "It"
    finding: dict[str, Any] | None = None
    if kind == "moon_phase":
        moment = solution.get("observedAtUTC")
        if not isinstance(moment, str):
            return _refused(kind, "no_time_or_place", solution, noun=noun)
        try:
            statements = moon_statements(moment)
        except ValueError:
            return _refused(kind, "no_time_or_place", solution, noun=noun)
    elif kind in SOLUTION_PATHS:
        for path in SOLUTION_PATHS[kind]:
            row = _row(path, finding=False)
            statement = _statement({"kind": "solution"}, row, _get(solution, path), list(path)) if row else None
            if statement is not None:
                statements.append(statement)
    else:
        index, missing = _target(question, solution, selected)
        if missing is not None:
            return _refused(kind, "not_found_in_this_sky", solution, missing, noun=noun)
        if index is None:
            return _refused(kind, "not_in_the_data", solution, noun=noun)
        finding = solution["findings"][index]
        subject = {"kind": "finding", "findingId": finding["id"], "evidenceClass": finding["evidenceClass"]}
        common = _get(finding, ("catalogRef", "commonName"))
        title = finding.get("title")
        name = common if isinstance(common, str) else title if isinstance(title, str) else "It"
        for path in FINDING_PATHS[kind]:
            row = _row(path, finding=True)
            statement = _statement(subject, row, _get(finding, path), ["findings", index, *path]) if row else None
            if statement is not None:
                if kind in _ONE_OF and any(s["quantity"] == statement["quantity"] for s in statements):
                    continue
                statements.append(statement)
    answering = contract.QUESTION_QUANTITIES[kind]
    if not any(s["quantity"] in answering for s in statements):
        return _refused(kind, "not_in_the_data", solution, noun=noun)
    sentence, spoken = _sentence(kind, statements, name, finding, noun)
    classes = {s["evidenceClass"] for s in statements if s["quantity"] in spoken}
    answer = {"status": "answered", "question": kind, "observationSolutionId": solution["id"],
              "sentence": with_class_words(sentence, classes), "statements": statements[: contract.ANSWER_STATEMENTS_MAX]}
    contract.VOICE_ANSWER.validate_python(answer)
    return answer


_PATTERNS = [(kind, re.compile(r"\b(?:" + "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + r")\b",
                               re.ASCII)) for kind, phrases in QUESTION_PHRASES]
