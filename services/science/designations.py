"""Which tile row a Bayer or Flamsteed designation goes on (catalog-2b): the one source and the one function the
predicted sky (services/science/predicted_sky.py) and the solver's catalog star (phone_photo_solver.to_catalog_star)
both take a star's designation from.

The baked designations (catalog-2a) are HYG's values by HIP number (apps/web/public/catalog/stars/designations/) and
which tile row each HIP number names (designation-links/). A designation goes on a tile row through links.json by HIP
number only, and only on the brightest, by HYG's V, of the HYG rows that share it, so a fainter component without a
letter of its own never takes its primary's; when two rows tie for the brightest, neither does. A Flamsteed number is
given only where its constellation is the one the star is placed in, so "27 Cas" never names a star given another.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from services.contracts.designation import (
    BAYER_DESIGNATION_PATTERN,
    catalog_title,
    flamsteed_designation,
    flamsteed_long_designation,
    long_designation,
)
from services.contracts.designation_tables import DESIGNATION_CONSTELLATIONS

if TYPE_CHECKING:
    from services.science.catalog_model import CatalogStar

STARS_DIR = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars"
_ABBR_BY_NAME = {name: abbr for abbr, name, _ in DESIGNATION_CONSTELLATIONS}
_BAYER = re.compile(BAYER_DESIGNATION_PATTERN)
# The tile stars' catalog ids, as the solver and the predicted sky write them (phone_photo_solver.to_catalog_star)
_TILE_SOURCE_ID = re.compile(r"(HIP|Gaia DR3) ([1-9][0-9]*)")


class RowDesignation(NamedTuple):
    """A tile row's designation: the Bayer designation in its wire form, the Flamsteed number, and the constellation
    (its three-letter abbreviation) the designation names."""

    bayer: str | None
    flamsteed: int | None
    constellation: str


def tile_row_designations(designations: Mapping[str, Any], links: Mapping[str, Any]) -> dict[tuple[bool, int], RowDesignation]:
    """Each tile row's designation, keyed (is a Hipparcos row, id), by the rule above."""
    cols = designations["columns"]
    at = {name: cols.index(name) for name in ("hip", "flamsteed", "bayer", "constellation", "vMag")}
    rows = {r[at["hip"]]: r for r in designations["rows"]}
    by_key: dict[tuple[Any, Any, str], list[tuple[float, int]]] = {}
    for hip, r in rows.items():
        by_key.setdefault((r[at["flamsteed"]], r[at["bayer"]], r[at["constellation"]]), []).append((r[at["vMag"]], hip))
    brightest = set()
    for members in by_key.values():
        members.sort()
        if len(members) == 1 or members[0][0] < members[1][0]:
            brightest.add(members[0][1])
    lcols = links["columns"]
    li = {name: lcols.index(name) for name in ("catalog", "catalogId", "hip")}
    out: dict[tuple[bool, int], RowDesignation] = {}
    for link in links["rows"]:
        hip = link[li["hip"]]
        if hip not in brightest:
            continue
        r = rows[hip]
        letter, _, index = (r[at["bayer"]] or "").partition("-")
        bayer = long_designation(letter, int(index) if index else None, r[at["constellation"]]) if letter else None
        key = (link[li["catalog"]] == "hipparcos-2007", int(link[li["catalogId"]]))
        out[key] = RowDesignation(bayer, r[at["flamsteed"]], r[at["constellation"]])
    return out


@lru_cache(maxsize=1)
def baked_tile_row_designations() -> dict[tuple[bool, int], RowDesignation]:
    """`tile_row_designations` of the shipped files, read once."""

    def read(rel: str) -> Mapping[str, Any]:
        return json.loads((STARS_DIR / rel).read_text(encoding="utf-8"))

    return tile_row_designations(read("designations/designations.json"), read("designation-links/links.json"))


def designation_of_row(
    is_hipparcos: bool, ident: int, constellation: str, pinned_bayer: str | None = None
) -> tuple[str | None, int | None]:
    """(Bayer designation, Flamsteed number) of one tile row placed in `constellation` (its abbreviation or its Latin
    name): the number only where its constellation is that one. A pinned star's shipped designation is the fallback
    where the rule reaches none of its rows (five pinned Gaia rows have no link); wherever both exist they are the
    same text (tests/integration/test_designations.py). (None, None) for a row with neither."""
    found = baked_tile_row_designations().get((is_hipparcos, ident))
    if found is None:
        return pinned_bayer, None
    placed = constellation if constellation not in _ABBR_BY_NAME else _ABBR_BY_NAME[constellation]
    return found.bayer or pinned_bayer, found.flamsteed if found.constellation == placed else None


def designation_parts(star: CatalogStar) -> list[str]:
    """The designations a star's subtitle shows, in this order: its Bayer designation, its Flamsteed designation ("27
    Cas", from the number and the star's constellation) and the IAU names file's own designation as written there;
    each left out where it only repeats the star's name or is the star's title (catalog-2c: the Flamsteed designation
    in its short form too, where its long form is the title), and none twice. The one function both the predicted
    sky's subtitle and the solver's matched-star subtitle take these parts from."""
    number = star.flamsteed_number
    flamsteed = None if number is None else flamsteed_designation(number, star.constellation)
    title = star_title(star)
    shown = {star.common_name, title} | ({flamsteed} if number is not None and title == flamsteed_long_designation(number, star.constellation) else set())
    parts: list[str] = []
    for part in (star.bayer_designation, flamsteed, star.names_file_designation):
        if part and part not in shown and part not in parts:
            parts.append(part)
    return parts


def star_title(star: CatalogStar) -> str:
    """The title a star's finding shows, the one function the predicted sky and the solver's matched star both take
    it from: `catalog_title` of its reference (the IAU name, else a designation, else the catalog id)."""
    return catalog_title(star.to_catalog_reference_dict())[0]


def upgrade_stored_solution(solution: Mapping[str, Any]) -> dict[str, Any]:
    """A stored contract ObservationSolution read as the contract reads it now; a new dict, the stored one untouched.

    Before catalog-2b the solver wrote the IAU names file's own designation (an HR, HD, HIP, GJ or BD id or a host's
    name) into a matched star's catalogRef.bayerDesignation, which the contract now refuses. Such a reference is read as
    what it was: that value is no Bayer designation, so it leaves bayerDesignation, and stays only where the solver
    also wrote it, the finding's subtitle, which is not touched (the contract has no field for the names file's
    designation); bayerDesignation and flamsteedNumber are then what the baked designations give that tile star
    (`designation_of_row`), else absent. A reference whose designation the contract takes, every other value and a
    solution of the new shape are returned as they are, so reading twice gives the same."""
    out = copy.deepcopy(dict(solution))
    for finding in out.get("findings") or ():
        ref = finding.get("catalogRef") if isinstance(finding, dict) else None
        if not isinstance(ref, dict):
            continue
        old = ref.get("bayerDesignation")
        if not isinstance(old, str) or _BAYER.fullmatch(old):
            continue
        source = _TILE_SOURCE_ID.fullmatch(ref.get("catalogSourceId") or "")
        constellation = ref.get("constellation")
        bayer = flamsteed = None
        if source is not None and isinstance(constellation, str):
            bayer, flamsteed = designation_of_row(source[1] == "HIP", int(source[2]), constellation)
        upgraded: dict[str, Any] = {}
        for key, value in ref.items():
            if key != "bayerDesignation":
                upgraded[key] = value
                continue
            if bayer is not None:
                upgraded[key] = bayer
            if flamsteed is not None and "flamsteedNumber" not in ref:
                upgraded["flamsteedNumber"] = flamsteed
        finding["catalogRef"] = upgraded
    return out
