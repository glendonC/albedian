"""Star names a user may say, each resolving only to a row of the baked IAU star-name table.

The table is `apps/web/public/catalog/stars/gaia-dr3/names.json`: the IAU Catalog of Star Names (IAU-CSN),
matched to the Gaia DR3 and Hipparcos 2007 catalog tiles, with its source and licence in that folder's
PROVENANCE.json. A name resolves to the finding id the phone-photo solver gives that star
(`services/science/phone_photo_solver.py`): a pinned catalog star keeps its own id, any other star is
`finding-hip-<HIP>` or `finding-gaia-dr3-<source id>`.

Common names of southern objects the baked data has no row for yet resolve to nothing: the parsers refuse
them plainly, so a request for one never falls through to the selected finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

from services.science.catalog import ALL_SKY_CATALOG_STARS

NAMES_PATH = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3" / "names.json"

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

# Southern showpieces with no row in the baked data yet, by the names people say.
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


def whole_word(name: str) -> re.Pattern[str]:
    """Match `name` only as a whole word, so the Moon is not found in "honeymoon".

    The name comes first and the check on the character before it follows as a look-behind over the name, so
    the regex engine can skip straight to where the name occurs. With the look-behind first it tests every
    position, and with hundreds of names a long utterance took seconds.
    """
    literal = re.escape(name.lower())
    return re.compile(rf"{literal}(?<![a-z0-9]{literal})(?![a-z0-9])")


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


def southern_cross_stars() -> tuple[NamedStar, ...]:
    """The five named stars of the Southern Cross, in Bayer order, as the table holds them."""
    by_name = {star.name: star for star in named_stars()}
    return tuple(by_name[name] for name in SOUTHERN_CROSS_STARS if name in by_name)


def names_southern_cross(lower_utterance: str) -> bool:
    return any(whole_word(name).search(lower_utterance) for name in SOUTHERN_CROSS_NAMES)


def unbaked_object(lower_utterance: str) -> str | None:
    """The southern object the utterance names that the baked data holds no row for, if any."""
    for label, spoken in UNBAKED_OBJECTS.items():
        if any(whole_word(name).search(lower_utterance) for name in spoken):
            return label
    return None


def unbaked_refusal(label: str) -> str:
    return (
        f"{label[0].upper()}{label[1:]} is not in the app's baked data yet, so it cannot be selected, "
        "described or flown to."
    )
