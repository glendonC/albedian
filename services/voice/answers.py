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

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from functools import lru_cache
import math
import re
from typing import Any

from pydantic import ValidationError

from services.contracts import voice as contract
from services.contracts.designation import flamsteed_long_designation
from services.contracts.designation_tables import (
    DESIGNATION_CONSTELLATIONS,
    DESIGNATION_INDICES,
    DESIGNATION_LETTERS,
)
from services.contracts.voice import lower_case
from services.science import deep_sky_catalog, radiometry
from services.voice import intent, names
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
# "What is" (or "what's") in a question naming a thing asks what that thing is ("what is Omega Centauri", "so what's
# the Jewel Box"): identity, the wide kind, read only when no other kind is asked and the words name something, so
# "what is the weather" asks nothing and "what is the distance of Acrux" asks the distance
_WHAT_IS_NAMED = re.compile(r"\bwhat(?:\s+is|'s)\s+\S", re.ASCII)
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
# Every 3-letter IAU code and OpenNGC's two Serpens halves ("Se1" Serpens Caput, "Se2" Serpens Cauda) mapped to the
# constellation's full Latin name for spoken sentences; a stored full Latin name stays as it is
CONSTELLATION_NAMES: dict[str, str] = {
    **{abbr: latin for abbr, latin, _genitive in DESIGNATION_CONSTELLATIONS},
    "Se1": "Serpens",
    "Se2": "Serpens",
}


def constellation_name(stored: str) -> str:
    """The full Latin constellation name for a stored 3-letter IAU code, OpenNGC's 'Se1'/'Se2' ('Serpens'), or a stored
    full Latin name."""
    return CONSTELLATION_NAMES.get(stored, stored)


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


@dataclass(frozen=True)
class _StaticTarget:
    key: str
    label: str
    ids: tuple[str, ...]
    bayer: str | None = None
    flamsteed: str | None = None
    is_moon: bool = False


@dataclass(frozen=True)
class _MatchedTarget:
    index: int | None
    label: str
    key: str
    is_moon: bool = False


_EXTRA_SOLAR_BODIES: tuple[tuple[str, str], ...] = (
    ("mercury", "Mercury"),
    ("neptune", "Neptune"),
    ("pluto", "Pluto"),
)
# An abbreviation after a number reads as a Flamsteed star only as the IAU writes it ("119 Tau"), since in lower case
# many are words ("50 per cent", "3 men"); "Oct" never does, as after a number it is the month
_DATE_ABBRS = frozenset({"Oct"})
_CANON_LETTER = {lower_case(name): name for _, _, name in DESIGNATION_LETTERS}
_CANON_GENITIVE = {lower_case(gen): gen for _, _, gen in DESIGNATION_CONSTELLATIONS}
_ABBR_BY_GENITIVE = {lower_case(gen): abbr for abbr, _, gen in DESIGNATION_CONSTELLATIONS}
_IAU_ABBRS = [abbr for abbr, _, _ in DESIGNATION_CONSTELLATIONS if abbr not in _DATE_ABBRS]


def _alt_lower(items: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(items, key=lambda s: (-len(s), s)))


_BAYER_QUERY_RE = re.compile(
    rf"\b(?P<letter>{_alt_lower(list(_CANON_LETTER))})(?P<digit>{_alt_lower([d for d, _ in DESIGNATION_INDICES])})?\s+(?P<gen>{_alt_lower(list(_CANON_GENITIVE))})\b",
    re.ASCII,
)
_FLAMSTEED_QUERY_RE = re.compile(
    rf"\b(?P<num>[1-9]\d{{0,2}})\s+(?P<const>{_alt_lower(list(_ABBR_BY_GENITIVE))})\b",
    re.ASCII,
)
_FLAMSTEED_ABBR_QUERY_RE = re.compile(rf"\b(?P<num>[1-9]\d{{0,2}})\s+(?P<const>{_alt_lower(_IAU_ABBRS)})\b", re.ASCII)
_NGC_IC_QUERY_RE = re.compile(r"\b(?P<cat>ngc|ic)\s*0*(?P<num>[1-9]\d*[a-z]*)\b", re.ASCII)


@lru_cache(maxsize=1)
def _static_targets() -> tuple[tuple[tuple[re.Pattern[str], _StaticTarget], ...], dict[str, tuple[str, str]]]:
    entries: list[tuple[str, re.Pattern[str], _StaticTarget]] = []
    seen_spoken: set[str] = set()

    for spoken, finding_id in intent._STAR_NAMES.items():
        pred_id = finding_id if finding_id.startswith("finding-predicted-") else finding_id.replace("finding-", "finding-predicted-", 1)
        shown = intent._SHOWN_NAMES[spoken]
        seen_spoken.add(spoken)
        entries.append((spoken, names.name_pattern(spoken), _StaticTarget(finding_id, shown, (finding_id, pred_id))))

    for body_key in intent.PREDICTED_BODY_KEYS:
        fid = f"finding-predicted-{body_key}"
        shown = names.ascii_capitalized(body_key)
        seen_spoken.add(body_key)
        entries.append((body_key, names.whole_word(body_key), _StaticTarget(fid, shown, (fid,), is_moon=(body_key == "moon"))))

    for spoken, shown in _EXTRA_SOLAR_BODIES:
        fid = f"finding-predicted-{spoken}"
        seen_spoken.add(spoken)
        entries.append((spoken, names.whole_word(spoken), _StaticTarget(fid, shown, (fid,))))

    for raw_label, spoken_list in names.UNBAKED_OBJECTS.items():
        clean_label = raw_label.removeprefix("the ")
        row_code = names.UNBAKED_DEEP_SKY_ROWS.get(raw_label)
        fids = (deep_sky_catalog.finding_id(row_code),) if row_code is not None else ()
        key = fids[0] if fids else f"unbaked:{lower_case(clean_label)}"
        for sp in spoken_list:
            if sp not in seen_spoken:
                seen_spoken.add(sp)
                entries.append((sp, names.whole_word(sp), _StaticTarget(key, clean_label, fids)))

    table, at = deep_sky_catalog.table_and_columns()
    by_code: dict[str, tuple[str, str]] = {}
    common_counts: dict[str, int] = {}
    candidate_rows = deep_sky_catalog._candidate_rows()
    for row in candidate_rows:
        for cn in row[at["commonNames"]] or []:
            low = lower_case(cn)
            common_counts[low] = common_counts.get(low, 0) + 1

    for row in candidate_rows:
        row_name = row[at["name"]]
        fid = deep_sky_catalog.finding_id(row_name)
        disp = deep_sky_catalog.display_name(row_name, row[at["commonNames"]] or [])
        low_code = lower_case(row_name)
        by_code[low_code] = (fid, disp)
        unpadded = re.sub(r"^(ngc|ic)0+(\d)", r"\1\2", low_code)
        by_code.setdefault(unpadded, (fid, disp))

        for cn in row[at["commonNames"]] or []:
            sp = lower_case(cn)
            if sp in seen_spoken or (" " not in sp and common_counts.get(sp, 0) > 1):
                continue
            seen_spoken.add(sp)
            entries.append((sp, names.whole_word(sp), _StaticTarget(fid, disp, (fid,))))

        messier = row[at["messier"]]
        if messier is not None:
            m_num = int(messier)
            m_key = f"messier {m_num}"
            if m_key not in seen_spoken:
                seen_spoken.add(m_key)
                entries.append(
                    (m_key, re.compile(rf"\b(?:messier\s+|m){m_num}\b", re.ASCII), _StaticTarget(fid, disp, (fid,)))
                )

    ordered = tuple(
        (pattern, spec) for _sp, pattern, spec in sorted(entries, key=lambda item: (-len(item[0]), item[0]))
    )
    return ordered, by_code


def _finding_flamsteed_long(finding: dict[str, Any]) -> str | None:
    num = _get(finding, ("catalogRef", "flamsteedNumber"))
    const = _get(finding, ("catalogRef", "constellation"))
    if isinstance(num, (int, float)) and not isinstance(num, bool) and isinstance(const, str):
        return flamsteed_long_designation(num, const)
    return None


def _match_finding(spec: _StaticTarget, findings: list[dict[str, Any]]) -> int | None:
    low_label = lower_case(spec.label)
    for index, finding in enumerate(findings):
        fid = finding.get("id")
        if fid in spec.ids:
            return index
        common = _get(finding, ("catalogRef", "commonName"))
        if isinstance(common, str) and lower_case(common) == low_label:
            return index
        # A designation's label is the designation itself, so a title that is the designation matched just above
        title = finding.get("title")
        if isinstance(title, str) and lower_case(title) == low_label:
            return index
        if spec.bayer is not None and _get(finding, ("catalogRef", "bayerDesignation")) == spec.bayer:
            return index
        if spec.flamsteed is not None and _finding_flamsteed_long(finding) == spec.flamsteed:
            return index
    if spec.is_moon:
        for index, finding in enumerate(findings):
            if finding.get("id") == "finding-observed-moon":
                return index
    return None


def _named_targets(question: str, solution: dict[str, Any] | None) -> list[_MatchedTarget]:
    """Every target named in `question`, in the order spoken, resolved against `solution['findings']` when present."""
    findings: list[dict[str, Any]] = list((solution or {}).get("findings") or [])
    rest = _lowered(question)
    static_list, by_code = _static_targets()
    raw_hits: list[tuple[int, _StaticTarget]] = []

    def take_spans(spans: list[tuple[int, int]], spec: _StaticTarget) -> None:
        nonlocal rest
        for start, end in spans:
            raw_hits.append((start, spec))
            rest = names.blank(rest, start, end)

    for pattern, spec in static_list:
        spans = [m.span() for m in pattern.finditer(rest)]
        if spans:
            take_spans(spans, spec)

    for match in list(_NGC_IC_QUERY_RE.finditer(rest)):
        start, end = match.span()
        if rest[start:end].strip() == "":
            continue
        cat = match.group("cat").upper()
        num = match.group("num").upper()
        lookup = f"{cat.lower()}{num.lower()}"
        fid, disp = by_code.get(lookup, (f"finding-openngc-{cat}{num}", f"{cat} {num}"))
        take_spans([(start, end)], _StaticTarget(fid, disp, (fid,)))

    for match in list(_BAYER_QUERY_RE.finditer(rest)):
        start, end = match.span()
        if rest[start:end].strip() == "":
            continue
        digit = match.group("digit") or ""
        canon = f"{_CANON_LETTER[match.group('letter')]}{digit} {_CANON_GENITIVE[match.group('gen')]}"
        take_spans([(start, end)], _StaticTarget(f"bayer:{canon}", canon, (), bayer=canon))

    for match in list(_FLAMSTEED_QUERY_RE.finditer(rest)):
        start, end = match.span()
        if rest[start:end].strip() == "":
            continue
        canon_flam = flamsteed_long_designation(int(match.group("num")), _ABBR_BY_GENITIVE[match.group("const")])
        if canon_flam is not None:
            take_spans([(start, end)], _StaticTarget(f"flamsteed:{canon_flam}", canon_flam, (), flamsteed=canon_flam))

    # The abbreviation in its IAU case, read from the words as written, at the same places as the lowered ones
    cased = fold(VOICE_GRAMMAR, question).strip(intent._JAVASCRIPT_TRIM)
    if len(cased) == len(rest):
        for match in list(_FLAMSTEED_ABBR_QUERY_RE.finditer(cased)):
            start, end = match.span()
            if rest[start:end] != lower_case(cased[start:end]):
                continue
            canon_flam = flamsteed_long_designation(int(match.group("num")), match.group("const"))
            if canon_flam is not None:
                take_spans([(start, end)], _StaticTarget(f"flamsteed:{canon_flam}", canon_flam, (), flamsteed=canon_flam))

    for index, finding in enumerate(findings):
        title = finding.get("title")
        if isinstance(title, str) and len(title.strip()) >= 3:
            pat = names.whole_word(title.strip())
            spans = [m.span() for m in pat.finditer(rest)]
            if spans:
                fid = str(finding.get("id") or f"finding-idx-{index}")
                take_spans(spans, _StaticTarget(fid, title.strip(), (fid,)))

    if raw_hits:
        for pattern, named in intent._EVERYDAY_BARE_TABLE:
            spans = [m.span() for m in pattern.finditer(rest)]
            if spans:
                pred_id = named.finding_id.replace("finding-", "finding-predicted-", 1)
                take_spans(spans, _StaticTarget(named.finding_id, named.label, (named.finding_id, pred_id)))

    raw_hits.sort(key=lambda hit: hit[0])
    unique: list[_MatchedTarget] = []
    seen_indices: set[int] = set()
    seen_keys: set[str] = set()
    for _pos, spec in raw_hits:
        idx = _match_finding(spec, findings)
        if idx is not None:
            if idx in seen_indices:
                continue
            seen_indices.add(idx)
        else:
            if spec.key in seen_keys:
                continue
            seen_keys.add(spec.key)
        unique.append(_MatchedTarget(index=idx, label=spec.label, key=spec.key, is_moon=spec.is_moon))
    return unique


def _target(question: str, solution: dict[str, Any], selected: str | None) -> tuple[int | None, str | None]:
    """The index of the finding the words are about, and the name said when no finding has it. A request that names
    more than one thing is refused before this is asked."""
    findings = solution.get("findings") or []
    matched = _named_targets(question, solution)
    if matched:
        return matched[0].index, (None if matched[0].index is not None else matched[0].label)
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
        # The first form that fits with the class words its spoken values take already on (the answer adds them):
        # a name short enough alone but not with "Predicted, not measured: " gives way to the form after it
        classes = {s["evidenceClass"] for s in statements if s["quantity"] in spoken}
        fits = [c for c in choices if contract.utf16_length(c) <= contract.ANSWER_SENTENCE_MAX_UTF16]
        return next((c for c in fits if _class_worded(c, classes) is not None), fits[0] if fits else SENTENCE_TOO_LONG), spoken

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
        where = constellation_name(by["constellation"]["text"])
        return fitted(["constellation"], f"{name} is in {where}.",
                      f"It is in {where}.", "Its constellation is in the catalog.")
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


def _class_worded(sentence: str, classes: set[str]) -> str | None:
    """The sentence with the words that say a spoken value is predicted or simulated (simulated first: a drawn thing
    is drawn, whatever else), the sentence itself when it takes none, or None when they do not fit with it."""
    for evidence in ("simulated", "predicted"):
        if evidence in classes:
            body = sentence[0].lower() + sentence[1:] if sentence.startswith(_LOWERED_OPENERS) else sentence
            said = CLASS_WORDS[evidence] + body
            return said if contract.utf16_length(said) <= contract.ANSWER_SENTENCE_MAX_UTF16 else None
    return sentence


def with_class_words(sentence: str, classes: set[str]) -> str:
    """The sentence with its class words when they fit; else the shortest form that still says so."""
    worded = _class_worded(sentence, classes)
    if worded is not None:
        return worded
    evidence = "simulated" if "simulated" in classes else "predicted"
    return CLASS_WORDS[evidence] + "the value is in the answer's statements."


def answer_question(question: str, solution: dict[str, Any] | None, selected: str | None = None, *,
                    noun: str = "photo") -> dict[str, Any]:
    """The contract VoiceAnswer to `question` about the recording whose stored solution is `solution` (None when none
    is stored), with `selected` the finding in view, if any, every sentence naming the recording by `noun` (NOUNS)."""
    if noun not in NOUNS:
        raise ValueError("a recording is named a photo or a clip")
    unanswerable = contract.UNANSWERABLE_QUESTION_KIND
    asked, negated = asked_kinds(question)
    if not asked and _WHAT_IS_NAMED.search(_lowered(question)) and _named_targets(question, solution):
        asked = {_WIDE_KIND}
    if ranks_or_relates(question):
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_RANKS, noun=noun)
    if len(asked) > 1:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_TWO_KINDS, noun=noun)
    if not asked:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_ONLY_NEGATED if negated else None,
                        noun=noun)
    kind = next(iter(asked))
    said_targets = _named_targets(question, solution)
    if len(said_targets) > 1:
        return _refused(unanswerable, "outside_the_data", solution, sentence=SENTENCE_TWO_NAMES, noun=noun)
    if kind == "moon_phase" and any(not each.is_moon for each in said_targets):
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
        # The finding by its title, the one its card shows (DECIDED-BY-LEAD 24 Nov 18:46): the common-name field holds the
        # catalog id wherever the IAU names file has no name. Up to its bracket, as the app parser reads a title
        # (packages/observation/src/voiceParser.ts spokenNamesOf), so "Moon (Predicted Ephemeris)" is "Moon"
        shown = title.split(" (")[0].strip() if isinstance(title, str) else ""
        name = shown if shown else common if isinstance(common, str) else "It"
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
