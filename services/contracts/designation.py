"""Bayer and Flamsteed designations, and the title order, beside `CatalogReference` (catalog-2b). The TypeScript twin is
packages/contracts/src/designation.ts; tests/fixtures/designation_cases.json holds cases both sides answer alike.

Every name comes from services/contracts/designation_tables.py (scripts/bake_designation_tables.py), never typed:
- The wire form of a Bayer designation is the long form the repository has always shipped: the letter's name, an
  optional index digit, a space and the constellation's genitive ("Alpha Orionis", "Alpha2 Capricorni", "Alpha Canis
  Majoris"). `BAYER_DESIGNATION_PATTERN` admits exactly that, from the same tables, on both sides.
- The compact form, for display only, has the Greek letter itself, the index as a superscript and the constellation's
  three-letter abbreviation ("α² Cap").
- A Flamsteed designation is written as its number and the constellation's abbreviation ("27 Cas").
- The title is the IAU name (a common name that is not the catalog id), else the catalog id, as both paths showed
  before catalog-2b; the Bayer designation, the Flamsteed designation and the catalog id that is not the title are
  the subtitle's parts, in that order. Titling a star with no IAU name by its designation is a wording choice for
  the owner, and changes this one function on each side.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from services.contracts.designation_tables import (
    DESIGNATION_CONSTELLATIONS,
    DESIGNATION_INDICES,
    DESIGNATION_LETTERS,
    FLAMSTEED_NUMBER_MAX,
)

__all__ = [
    "BAYER_DESIGNATION_PATTERN",
    "FLAMSTEED_NUMBER_MAX",
    "FLAMSTEED_NUMBER_MIN",
    "catalog_title",
    "compact_designation",
    "flamsteed_designation",
    "long_designation",
]

FLAMSTEED_NUMBER_MIN = 1
_LETTER = {abbr: (glyph, name) for abbr, glyph, name in DESIGNATION_LETTERS}
_GLYPH_BY_NAME = {name: glyph for _, glyph, name in DESIGNATION_LETTERS}
_SUPERSCRIPT = dict(DESIGNATION_INDICES)
_CONSTELLATION = {abbr: (name, genitive) for abbr, name, genitive in DESIGNATION_CONSTELLATIONS}
_ABBR_BY_GENITIVE = {genitive: abbr for abbr, _, genitive in DESIGNATION_CONSTELLATIONS}
_ABBR_BY_NAME = {name: abbr for abbr, name, _ in DESIGNATION_CONSTELLATIONS}


_REGEX_SPECIAL = re.compile(r"[.*+?^${}()|[\]\\]")


def _alternatives(words: list[str]) -> str:
    # Longest first, so no alternative stops at a shorter one that begins it; each word escaped with the same set the
    # TypeScript twin escapes, so both sides build one and the same pattern text
    return "|".join(_REGEX_SPECIAL.sub(lambda m: "\\" + m.group(0), w) for w in sorted(words, key=lambda w: (-len(w), w)))


_NAMES = _alternatives([name for _, _, name in DESIGNATION_LETTERS])
_DIGITS = _alternatives([digit for digit, _ in DESIGNATION_INDICES])
_GENITIVES = _alternatives([genitive for _, _, genitive in DESIGNATION_CONSTELLATIONS])
BAYER_DESIGNATION_PATTERN = rf"^({_NAMES})({_DIGITS})? ({_GENITIVES})$"
_BAYER = re.compile(BAYER_DESIGNATION_PATTERN)


def long_designation(letter: str, index: int | None, constellation: str) -> str:
    """The wire form from HYG's abbreviations: "Gam", 2, "Cas" gives "Gamma2 Cassiopeiae". KeyError for a letter or a
    constellation the tables do not hold, ValueError for an index outside them."""
    name = _LETTER[letter][1]
    genitive = _CONSTELLATION[constellation][1]
    if index is not None and str(index) not in _SUPERSCRIPT:
        raise ValueError(f"an index of {index} is outside the tables")
    return f"{name}{'' if index is None else index} {genitive}"


def compact_designation(designation: str) -> str | None:
    """The display form of a wire-form Bayer designation ("Alpha2 Capricorni" gives "α² Cap"); None for any other text."""
    match = _BAYER.fullmatch(designation)
    if match is None:
        return None
    name, digit, genitive = match.groups()
    return f"{_GLYPH_BY_NAME[name]}{'' if digit is None else _SUPERSCRIPT[digit]} {_ABBR_BY_GENITIVE[genitive]}"


def flamsteed_designation(number: int, constellation: str) -> str | None:
    """"27 Cas" from the number and the constellation, given by its abbreviation or its Latin name; None when the
    constellation is neither or the number is outside the contract's range."""
    abbr = constellation if constellation in _CONSTELLATION else _ABBR_BY_NAME.get(constellation)
    if abbr is None or isinstance(number, bool) or not FLAMSTEED_NUMBER_MIN <= number <= FLAMSTEED_NUMBER_MAX:
        return None
    return f"{number} {abbr}"


def catalog_title(reference: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """(title, subtitle parts) of a catalog reference in its wire form: the IAU name, else the catalog id; the subtitle
    holds the Bayer designation, the Flamsteed designation and the catalog id where it is not the title, in that
    order."""
    source_id = reference["catalogSourceId"]
    name = reference["commonName"] if reference["commonName"] != source_id else None
    bayer = reference.get("bayerDesignation")
    number = reference.get("flamsteedNumber")
    flamsteed = None if number is None else flamsteed_designation(number, reference["constellation"])
    title = name or source_id
    return title, tuple(part for part in (bayer, flamsteed, source_id) if part is not None and part != title)
