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
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from services.science.catalog import ALL_SKY_CATALOG_STARS

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
