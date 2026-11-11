"""Star names a user may say, each resolving only to a row of the baked IAU star-name table.

The table is `apps/web/public/catalog/stars/gaia-dr3/names.json`: the IAU Catalog of Star Names (IAU-CSN),
matched to the Gaia DR3 and Hipparcos 2007 catalog tiles, with its source and licence in that folder's
PROVENANCE.json. A name resolves to the finding id the phone-photo solver gives that star
(`services/science/phone_photo_solver.py`): a pinned catalog star keeps its own id, any other star is
`finding-hip-<HIP>` or `finding-gaia-dr3-<source id>`.

Common names of southern objects voice cannot reach yet resolve to nothing: the parsers refuse them plainly,
saying where the baked data holds each (UNBAKED_DEEP_SKY_ROWS), so a request for one never falls through to
the selected finding.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import TypeVar

from services.contracts.voice import JAVASCRIPT_SPACES
from services.science.catalog import ALL_SKY_CATALOG_STARS

T = TypeVar("T")

ROOT = Path(__file__).resolve().parents[2]
NAMES_PATH = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3" / "names.json"
# The app parser's copy of star_name_table(), written by `uv run python -m services.voice.names --write-ts`
TS_TABLE_PATH = ROOT / "packages" / "observation" / "src" / "generated" / "catalogStarNames.ts"

# Names a stargazer says for a star the IAU table names otherwise, each landing on that IAU name's row.
# packages/observation/src/voiceParser.ts holds the same list (SPOKEN_STAR_ALIASES); a test keeps them equal.
SPOKEN_ALIASES = {
    "alpha centauri": "Rigil Kentaurus",
    "beta centauri": "Hadar",
    "agena": "Hadar",
    "alpha crucis": "Acrux",
    "beta crucis": "Mimosa",
    "gamma crucis": "Gacrux",
    "delta crucis": "Imai",
    "epsilon crucis": "Ginan",
    "alpha carinae": "Canopus",
    "beta carinae": "Miaplacidus",
    "epsilon carinae": "Avior",
    "alpha eridani": "Achernar",
    "alpha pavonis": "Peacock",
    "alpha gruis": "Alnair",
    "alpha trianguli australis": "Atria",
}

# The Southern Cross asterism: alpha, beta, gamma, delta and epsilon Crucis, each a row of the table.
SOUTHERN_CROSS_STARS = ("Acrux", "Mimosa", "Gacrux", "Imai", "Ginan")
SOUTHERN_CROSS_NAMES = ("southern cross", "crux")

# Southern showpieces voice cannot reach yet, by the names people say.
UNBAKED_OBJECTS: dict[str, tuple[str, ...]] = {
    "the Large Magellanic Cloud": ("large magellanic cloud", "lmc"),
    "the Small Magellanic Cloud": ("small magellanic cloud", "smc"),
    "the Magellanic Clouds": ("magellanic clouds",),
    "Omega Centauri": ("omega centauri", "omega cen"),
    "47 Tucanae": ("47 tucanae", "47 tuc"),
    "the Carina Nebula": ("carina nebula", "eta carinae nebula"),
    "Eta Carinae": ("eta carinae", "eta car"),
    "the Jewel Box": ("jewel box", "kappa crucis cluster"),
    "the Coalsack": ("coalsack", "coal sack"),
}

# The row of the baked OpenNGC table (apps/web/public/catalog/deepsky/openngc.json) that holds each object above,
# or None when no baked table holds it by name. Voice does not reach deep-sky rows yet, so each is still refused;
# the refusal says where the object is. tests/integration/test_voice_southern_names.py checks every entry
# against the table, so a new data row fails it loudly.
UNBAKED_DEEP_SKY_ROWS: dict[str, str | None] = {
    "the Large Magellanic Cloud": "ESO056-115",
    "the Small Magellanic Cloud": "NGC0292",
    "the Magellanic Clouds": None,
    "Omega Centauri": "NGC5139",
    "47 Tucanae": "NGC0104",
    "the Carina Nebula": "NGC3372",
    "Eta Carinae": None,
    "the Jewel Box": "NGC4755",
    "the Coalsack": "C099",
}


# Star names that are also everyday English words, reviewed from the 32 table names that are lowercase entries of
# /usr/share/dict/words. These name a star only after a naming verb or with "star" beside them, so "zoom in, I ran
# out of room" names no star. The other 24 (castor, pollux, regulus, spica and the like, said as star names first;
# brachium, situla, torcular and the like, rare in speech) stay plain names.
EVERYDAY_WORD_NAMES: dict[str, str] = {
    "ran": "the past of run",
    "fang": "a tooth",
    "atlas": "a book of maps",
    "sham": "a fake",
    "skat": "a card game",
    "peacock": "a bird",
    "diadem": "a crown",
    "axolotl": "an amphibian",
}

# The naming verbs after which an everyday-word name is a star ("the" and "star" may come between). "show" is not
# one: "show the fang shaped cloud" names no star. "what is" is one, since otherwise "what is atlas" would name no
# star and a describe rule would answer about the selection instead.
NAME_CUE_VERBS = (
    "select", "fly to", "go to", "take me to", "travel to", "tell me about", "where is", "where's", "what is",
    "what's", "describe", "focus on", "look at", "point at",
)

# One space as JavaScript's /\s/ reads it (JAVASCRIPT_SPACES), so a pattern here reads the same phrases as
# packages/observation/src/voiceParser.ts: Python's \s also takes U+001C to U+001F and U+0085, and not U+FEFF.
# services/voice/intent.py reads years with it too.
JAVASCRIPT_SPACE = "[" + "".join(re.escape(char) for char in sorted(JAVASCRIPT_SPACES)) + "]"


def whole_word(name: str) -> re.Pattern[str]:
    """Match `name` only as a whole word, so the Moon is not found in "honeymoon".

    The name comes first and the check on the character before it follows as a look-behind over the name, so
    the regex engine can skip straight to where the name occurs. With the look-behind first it tests every
    position, and with hundreds of names a long utterance took seconds.
    """
    literal = re.escape(name.lower())
    return re.compile(rf"{literal}(?<![a-z0-9]{literal})(?![a-z0-9])")


def name_pattern(name: str) -> re.Pattern[str]:
    """How a request names this star: a plain whole word, or for an everyday-word name, only after a naming verb
    (NAME_CUE_VERBS, then "the" and "star" if said) or with "star" right before or after it.
    packages/observation/src/voiceParser.ts builds the same pattern from the generated lists."""
    if name.lower() not in EVERYDAY_WORD_NAMES:
        return whole_word(name)
    literal = re.escape(name.lower())
    cues = "|".join(re.escape(verb) for verb in NAME_CUE_VERBS)
    s = JAVASCRIPT_SPACE
    return re.compile(
        rf"(?:(?<![a-z0-9])(?:{cues}){s}+(?:the{s}+)?(?:star{s}+)?|(?<![a-z0-9])star{s}+){literal}(?![a-z0-9])"
        rf"|(?<![a-z0-9]){literal}{s}+star(?![a-z0-9])"
    )


# --- Words that name nothing ---------------------------------------------------------------------------------
# A request is about what it names. Once its names and its command are cut out, each word left is one of three
# kinds, and any other word is one the parser did not understand. It may be a name no table holds, so the request
# is then refused, whatever else it names: it is never read as being about what happens to be selected, or about
# a name said beside it. packages/observation/src/voiceParser.ts holds the same three lists
# (tests/integration/test_voice_named_targets.py keeps them equal), so both parsers read a sentence the same way.

# Words that point at what is selected, focused or pointed at.
POINTING_WORDS = (
    "that", "this", "it", "its", "there", "here", "these", "those", "them", "selected", "selection", "current",
)

# Words that say something about a thing without naming it. They stand only beside a pointing word ("that bright
# one", "how far is it in light years"); without one, the request does not say which thing it means.
DESCRIBING_WORDS = (
    # a thing with no name
    "one", "ones", "star", "stars", "thing", "things", "object", "objects", "body", "bodies", "dot", "dots",
    "point", "points", "light", "lights", "spot", "spots", "patch", "patches", "blob", "blobs", "speck", "smudge",
    "streak", "glow", "marker", "markers",
    # a kind of thing
    "planet", "planets", "galaxy", "galaxies", "nebula", "nebulae", "nebulas", "cluster", "clusters",
    "constellation", "constellations", "comet", "comets", "asteroid", "asteroids", "satellite", "satellites",
    "meteor", "meteors", "plane", "planes", "airplane", "aeroplane",
    # how it looks
    "bright", "brighter", "dim", "dimmer", "faint", "fainter", "big", "bigger", "large", "larger", "small",
    "smaller", "little", "tiny", "huge", "red", "reddish", "orange", "yellow", "yellowish", "white", "whitish",
    "blue", "bluish", "green", "greenish", "fuzzy", "blurry", "hazy", "glowing", "shiny", "twinkling",
    "flickering", "blinking", "moving",
    # where it is on the screen or among the others
    "left", "top", "bottom", "middle", "center", "centre", "upper", "lower", "corner", "edge", "above", "below",
    "beside", "next", "nearest", "closest", "other", "another", "same", "first", "second", "last", "something",
    "anything", "else",
    # measures
    "distance", "year", "years", "parsec", "parsecs", "kilometre", "kilometres", "kilometer", "kilometers", "km",
    "mile", "miles", "au", "ly", "pc",
    # the recording, the app and home
    "photo", "photos", "picture", "pictures", "image", "images", "video", "videos", "clip", "recording", "shot",
    "frame", "sky", "night", "screen", "view", "app", "solve", "solved", "space", "3d", "earth", "home",
    # what the app parser's commands speak of ("another viewpoint", "orbit", "from the side", "how distant")
    "orbit", "viewpoint", "angle", "side", "sightline", "different", "distant",
)

# Words that can be neither the thing asked about nor a description of it: grammar, politeness and the verbs a
# request is wrapped in.
FUNCTION_WORDS = (
    # articles and determiners
    "a", "an", "the", "my", "our", "your", "any", "some", "all", "every", "each", "both",
    # people
    "i", "me", "mine", "we", "us", "you",
    # be, do, have and the helping verbs
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "done", "doing", "can", "could",
    "will", "would", "shall", "should", "may", "might", "must", "have", "has", "had", "having", "get", "gets",
    "got", "getting", "let", "lets",
    # what is left of a contraction (what's, don't, I'm, we're, I'll, I've, I'd, isn't, won't)
    "s", "t", "d", "m", "ll", "re", "ve", "don", "doesn", "didn", "isn", "aren", "wasn", "weren", "won", "couldn",
    "wouldn", "shouldn", "im", "whats", "wheres",
    # joining words
    "and", "or", "but", "so", "then", "if", "as", "than", "because", "while", "when", "after", "before",
    # place and direction words
    "to", "of", "off", "in", "into", "on", "onto", "at", "from", "for", "with", "without", "by", "through",
    "across", "around", "about", "near", "between", "like", "over", "under", "up", "down", "out", "away", "back",
    "toward", "towards", "behind", "beyond", "past", "far", "close",
    # asking words
    "what", "which", "who", "whose", "where", "why", "how",
    # manner, time and politeness
    "please", "pls", "plz", "now", "today", "tonight", "again", "already", "still", "yet", "soon", "later",
    "quickly", "fast", "slowly", "straight", "directly", "just", "only", "even", "ever", "really", "very", "quite",
    "actually", "exactly", "roughly", "approximately", "nearly", "almost", "maybe", "perhaps", "probably", "right",
    "sorry", "ok", "okay", "alright", "sure", "yes", "yeah", "yep", "no", "nope", "not", "never", "thanks", "thank",
    "cheers", "hey", "hi", "hello", "um", "uh", "hmm", "ah", "oh", "wow", "cool", "nice", "great", "well", "also",
    "too", "much", "many", "more", "less", "most", "closer", "nearer", "further", "farther", "deeper",
    # the verbs a request is wrapped in
    "want", "wants", "wanted", "wanna", "need", "needs", "love", "think", "thought", "guess",
    "know", "wonder", "wondering", "curious", "mean", "meant", "try", "trying", "help", "see", "show", "find",
    "locate", "look", "looking", "watch", "go", "going", "take", "bring", "travel", "zoom", "move", "jump",
    "head", "come", "tell", "say", "said", "give", "explain", "describe", "identify", "select", "choose", "pick",
    "focus", "inspect", "lock", "open", "enter", "explore", "visit", "make", "looks", "seems", "appears", "called",
    "named", "name", "idea", "info", "information", "details", "mode",
    # the other words of the two parsers' command phrases ("fly there", "what lies beyond", "translate viewpoint")
    "fly", "lies", "along", "translate", "shift",
    # the app's own name
    "albedian",
)

# The plain words that ask. A request that asks, names nothing and points at nothing is a question about no one
# thing ("what is an orbit?"), not a command for what is in view.
ASKING_WORDS = ("what", "which", "who", "whose", "where", "why", "how")

# A word is a run of letters and digits of any script, as JavaScript's \p{L} and \p{N} find them, so a name
# written in another alphabet is a word no list holds, never nothing at all
_WORD = re.compile(r"[^\W_]+")
_POINTING = frozenset(POINTING_WORDS)
_ASKING = frozenset(ASKING_WORDS)
# An everyday-word name said without its cue names no star ("I ran the solve again"): it only describes
_DESCRIBING = frozenset(DESCRIBING_WORDS) | frozenset(EVERYDAY_WORD_NAMES)
_FUNCTION = frozenset(FUNCTION_WORDS)
# "there is", "are there", "there's": something exists, and nothing is pointed at
_EXISTS = frozenset({"is", "are", "was", "were"})
# The most words, and the most characters, of a request quoted back in a refusal [proposed: enough for any name,
# short enough to read on a phone]
MAX_QUOTED_WORDS = 6
MAX_QUOTED_CHARACTERS = 80


def blank(text: str, start: int, end: int) -> str:
    """`text` with the characters from `start` to `end` turned to spaces, so what is cut out no longer reads as
    words and everything else keeps its place."""
    return text[:start] + " " * (end - start) + text[end:]


def find_names(lower_text: str, table: Sequence[tuple[re.Pattern[str], T]]) -> tuple[list[tuple[int, T]], str]:
    """Every name of `table` the text says, each with where it starts, in the order said, and the text with those
    names cut out.

    `table` holds the longest name first, and a name found is cut out before a shorter one is looked for, so
    "Deneb Algedi" is one name and never also "Deneb" and "Algedi"."""
    found: list[tuple[int, T]] = []
    for pattern, named in table:
        spans = [match.span() for match in pattern.finditer(lower_text)]
        for start, end in spans:
            found.append((start, named))
            lower_text = blank(lower_text, start, end)
    return sorted(found, key=lambda hit: hit[0]), lower_text


def points(lower_text: str) -> bool:
    """True when a word of the text points at what is in view. "There" beside "is" or "are" points at nothing."""
    words = _WORD.findall(lower_text)
    for index, word in enumerate(words):
        if word not in _POINTING:
            continue
        if word == "there":
            before = words[index - 1] if index > 0 else ""
            after = words[index + 1] if index + 1 < len(words) else ""
            if before in _EXISTS or after in _EXISTS or after == "s":
                continue
        return True
    return False


@dataclass(frozen=True)
class Wording:
    """How the words of a request read once its names and its command are cut out.

    `points`: a word points at what is in view. `describes`: a word describes a thing without naming it.
    `asks`: a word outside the command asks. `unknown`: the first words that are none of these and not plain, as
    said: the words the parser did not understand, which a refusal quotes; empty when there are none."""

    points: bool
    describes: bool
    asks: bool
    unknown: str


def read_wording(without_names: str, without_names_or_command: str) -> Wording:
    """Read the words left of a lower-cased request. A pointing word counts wherever it stands, the command's own
    words included ("take me there"); the other words are judged with the command cut out too."""
    words = _WORD.findall(without_names_or_command)
    kinds = [
        "plain" if word in _FUNCTION or word in _POINTING else "describes" if word in _DESCRIBING else "unknown"
        for word in words
    ]
    unknown = ""
    if "unknown" in kinds:
        # Only the words that stopped the reading: the first unknown word and the unknown words right after it,
        # so "the andromeda galaxy" quotes "andromeda", and "the milky way" quotes "milky way"
        first = last = kinds.index("unknown")
        while last + 1 < len(kinds) and kinds[last + 1] == "unknown":
            last += 1
        phrase = words[first : last + 1]
        quoted = " ".join(phrase[:MAX_QUOTED_WORDS])
        cut = len(phrase) > MAX_QUOTED_WORDS or len(quoted) > MAX_QUOTED_CHARACTERS
        unknown = quoted[:MAX_QUOTED_CHARACTERS] + (" ..." if cut else "")
    return Wording(
        points=points(without_names),
        describes="describes" in kinds,
        asks=any(word in _ASKING for word in words),
        unknown=unknown,
    )


@dataclass(frozen=True)
class NamedStar:
    """One row of the IAU star-name table, with the finding id the solver gives it."""

    name: str
    name_ascii: str
    bayer: str | None
    constellation: str
    component: str | None
    finding_id: str


_PINNED_BY_SOURCE = {star.catalog_source_id: star for star in ALL_SKY_CATALOG_STARS}


def _finding_id(row: dict) -> str:
    is_hip = row.get("catalog") == "hipparcos-2007"
    source_id = int(row["catalogId"])
    pinned = _PINNED_BY_SOURCE.get(f"HIP {source_id}" if is_hip else f"Gaia DR3 {source_id}")
    if pinned is not None:
        return f"finding-{pinned.id}"
    return f"finding-hip-{source_id}" if is_hip else f"finding-gaia-dr3-{source_id}"


@lru_cache(maxsize=1)
def named_stars() -> tuple[NamedStar, ...]:
    """Every row of the baked table; empty when the table is absent."""
    if not NAMES_PATH.is_file():
        return ()
    rows = json.loads(NAMES_PATH.read_text(encoding="utf-8")).get("names", ())
    return tuple(
        NamedStar(
            name=row["name"],
            name_ascii=row.get("nameAscii") or row["name"],
            bayer=row.get("bayer"),
            constellation=row["constellation"],
            component=row.get("component"),
            finding_id=_finding_id(row),
        )
        for row in rows
    )


@lru_cache(maxsize=1)
def spoken_names() -> tuple[tuple[str, str], ...]:
    """(spoken name, finding id) for every IAU name, its ASCII form and the spoken aliases."""
    stars = named_stars()
    by_name = {star.name: star for star in stars}
    pairs: dict[str, str] = {}
    for star in stars:
        pairs[star.name.lower()] = star.finding_id
        pairs[star.name_ascii.lower()] = star.finding_id
    for spoken, iau_name in SPOKEN_ALIASES.items():
        if iau_name in by_name:
            pairs.setdefault(spoken, by_name[iau_name].finding_id)
    return tuple(pairs.items())


@lru_cache(maxsize=1)
def star_name_table() -> tuple[tuple[str, str, str], ...]:
    """(spoken name in lower case, finding id, name to show) for every star the backend names, sorted by spoken name.

    Built as services/voice/intent.py builds its table: the pinned catalog's names first, keeping their meaning,
    then every IAU name, its ASCII form and the spoken aliases (spoken_names). A name shows as the pinned or IAU
    name it lands on, so "alpha centauri" shows as Rigil Kentaurus.
    """
    shown: dict[str, str] = {}
    stars = named_stars()
    for star in stars:
        shown[star.name.lower()] = star.name
        shown[star.name_ascii.lower()] = star.name
    iau_names = {star.name for star in stars}
    for spoken, iau_name in SPOKEN_ALIASES.items():
        if iau_name in iau_names:
            shown.setdefault(spoken, iau_name)
    table = {star.common_name.lower(): (f"finding-{star.id}", star.common_name) for star in ALL_SKY_CATALOG_STARS}
    for spoken, finding_id in spoken_names():
        table.setdefault(spoken, (finding_id, shown[spoken]))
    return tuple(sorted((spoken, finding_id, name) for spoken, (finding_id, name) in table.items()))


def render_ts_table() -> str:
    """The TypeScript module that gives packages/observation/src/voiceParser.ts the same star names."""
    digest = hashlib.sha256(NAMES_PATH.read_bytes()).hexdigest()
    rows = "\n".join(
        f"  [{json.dumps(spoken, ensure_ascii=False)}, {json.dumps(finding_id)}, {json.dumps(name, ensure_ascii=False)}],"
        for spoken, finding_id, name in star_name_table()
    )
    return (
        "// Generated by `uv run python -m services.voice.names --write-ts`. Do not edit it by hand: rerun the command.\n"
        f"// Source: apps/web/public/catalog/stars/gaia-dr3/names.json (sha256 {digest}), the IAU Catalog of\n"
        "// Star Names matched to Gaia DR3 and Hipparcos 2007, with its source and licence in that folder's\n"
        "// PROVENANCE.json; the pinned catalog's names (services/science/catalog.py); the spoken aliases\n"
        "// (services/voice/names.py). tests/integration/test_voice_ts_star_names.py keeps it equal to the backend's.\n"
        "\n"
        "/** [spoken name in lower case, finding id the solver gives the star, name to show], as the backend names them. */\n"
        "export const CATALOG_STAR_NAMES: ReadonlyArray<readonly [string, string, string]> = [\n"
        f"{rows}\n"
        "];\n"
        "\n"
        "/** Star names that are also everyday words: each names a star only after a naming verb or beside \"star\". */\n"
        f"export const EVERYDAY_WORD_NAMES: readonly string[] = {json.dumps(sorted(EVERYDAY_WORD_NAMES))};\n"
        "\n"
        "/** The naming verbs after which an everyday-word name is a star. */\n"
        f"export const NAME_CUE_VERBS: readonly string[] = {json.dumps(list(NAME_CUE_VERBS))};\n"
    )


def southern_cross_stars() -> tuple[NamedStar, ...]:
    """The five named stars of the Southern Cross, in Bayer order, as the table holds them."""
    by_name = {star.name: star for star in named_stars()}
    return tuple(by_name[name] for name in SOUTHERN_CROSS_STARS if name in by_name)


def names_southern_cross(lower_utterance: str) -> bool:
    return any(whole_word(name).search(lower_utterance) for name in SOUTHERN_CROSS_NAMES)


def unbaked_object(lower_utterance: str) -> str | None:
    """The southern object the utterance names that voice cannot reach yet, if any."""
    for label, spoken in UNBAKED_OBJECTS.items():
        if any(whole_word(name).search(lower_utterance) for name in spoken):
            return label
    return None


def unbaked_refusal(label: str) -> str:
    """Why voice cannot act on this object: where the baked data holds it, or that no table holds it by name."""
    subject = f"{label[0].upper()}{label[1:]}"
    row = UNBAKED_DEEP_SKY_ROWS.get(label)
    if row is not None:
        return (
            f"{subject} is in the app's deep-sky catalogue (OpenNGC {row}), but voice does not reach deep-sky "
            "objects yet, so it cannot be selected, described or flown to."
        )
    return f"{subject} has no named row in the app's catalogues yet, so it cannot be selected, described or flown to by name."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the app parser's star-name table from the backend's.")
    parser.add_argument("--write-ts", action="store_true", help="write the TypeScript star-name table the app's parser reads")
    args = parser.parse_args(argv)
    if not args.write_ts:
        parser.print_help()
        return 2
    TS_TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TS_TABLE_PATH.write_text(render_ts_table(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
