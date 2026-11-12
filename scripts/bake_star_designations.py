#!/usr/bin/env python3
"""Bake the Bayer and Flamsteed designations of the gaia-dr3 tile rows (catalog-2a).

Inputs, in `.cache/star-designations/` (registered in `data/downloads.json`) and pinned by size and SHA-256:
- `hygdata_v41.csv`: HYG v4.1 (astronexus), CC BY-SA 4.0. Its README says `bf` gives the Bayer and Flamsteed
  designation, "primarily from the Fifth Edition of the Yale Bright Star Catalog", with Flamsteed numbers added from
  Wagman (1987), and that `bayer`, `flam` and `con` give the letter with its superscript, the number and the
  constellation as distinct values. Those three are the values written here; `bf`, where present, only checks them.
- `Hipparcos2BestNeighbour.csv.gz`: the Gaia EDR3 to Hipparcos-2 best-neighbour table from ESA's bulk repository,
  CC BY-NC 3.0 IGO, credit ESA/Gaia/DPAC. ESA's DR3 bulk readme says the EDR3 cross-matches are valid for DR3.

Also read: the gaia-dr3 core and detail tiles, for each row's catalog, id, place (layer, HEALPix nested pixel, row
index), magnitude and colour. That folder's own PROVENANCE.json records where they came from.

A tile row is linked by HIP number only, never by position:
- a Hipparcos row takes the designation of its own HIP number;
- otherwise a Gaia row takes the designation of the HIP number it is the best neighbour of, if it holds at least half
  of the star's light: its V, from its G and BP-RP by Riello et al. (2021, A&A 649, A3, Table C.2), is at most
  2.5 log10 2 mag fainter than HYG's V for the star (a fainter row is a companion, not the star). A row whose BP-RP
  is missing or outside the relation's stated range, -0.5 < BP-RP < 5.0, gets no designation, as the test cannot be
  made. Every designation that does not reach a tile row is set aside with its reason.

No file mixes the two licences; a reader joins them by HIP number. Each folder has its own record:
- `apps/web/public/catalog/stars/designations/`, CC BY-SA 4.0 with HYG's attribution: `designations.json`, one compact
  line, `evidenceKind: "catalog-derived"`, one row per designated HYG row with a HIP number, in HIP order, every value
  as HYG wrote it (a blank is null), and the HYG rows set aside; `PROVENANCE.json` naming HYG as its one source.
- `apps/web/public/catalog/stars/designation-links/`, under ESA's terms as the tiles are: `links.json`, one compact
  line, one row per designated tile row in HIP order (catalog, id, place, HIP, how it was linked), and the HIP numbers
  that reach no tile row with their reason; `PROVENANCE.json` naming the three ESA sources and saying that the
  guard read HYG's V, which is not written there.

Usage, from the repository root:
    uv run python scripts/bake_star_designations.py
    uv run python scripts/bake_star_designations.py --verify
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402
from services.science.catalog_tiles import (  # noqa: E402
    FLAG_CATALOG_HIPPARCOS,
    FLAG_COLOR_B_V,
    decode_color,
    decode_core_tile,
    decode_detail_tile,
    decode_mag,
)

DEFAULT_CACHE = ROOT_DIR / ".cache" / "star-designations"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "designations"
DEFAULT_LINKS_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "designation-links"
DEFAULT_TILES = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
TILES_RELEASE = "gaia-dr3"
OUT_NAME = "designations.json"
LINKS_NAME = "links.json"
FORMAT = "albedian.catalog-star-designations"
LINKS_FORMAT = "albedian.catalog-star-designation-links"
FORMAT_VERSION = 1
EVIDENCE_KIND = "catalog-derived"
HYG_SOURCE_ID = "hyg-v41"
BEST_NEIGHBOUR_SOURCE_ID = "gaia-edr3-hipparcos2-best-neighbour"
RETRIEVED = "2026-11-12"


@dataclass(frozen=True)
class Input:
    filename: str
    url: str
    bytes: int
    sha256: str
    fetched: str


HYG = Input(
    filename="hygdata_v41.csv",
    url="https://raw.githubusercontent.com/astronexus/HYG-Database/c7f7f883fe678cc7680169a50ccd7dcc49b060ce/hyg/CURRENT/hygdata_v41.csv",
    bytes=33_932_548,
    sha256="d9f69fd86bbf90a4e4d52b4c5c53eacfa6dfc0bfdef85bfd94f095e0bebe4ebd",
    fetched="2026-11-12T14:51:33Z",
)
BEST_NEIGHBOUR = Input(
    filename="Hipparcos2BestNeighbour.csv.gz",
    url="https://cdn.gea.esac.esa.int/Gaia/gedr3/cross_match/hipparcos2_best_neighbour/Hipparcos2BestNeighbour.csv.gz",
    bytes=1_816_420,
    sha256="23a74b073ad78c6294d8248fc04be95be2cf7ac646162493eb7322c698eb3a9c",
    fetched="2026-11-12T14:56:41Z",
)
INPUTS = (HYG, BEST_NEIGHBOUR)

# HYG's three-letter abbreviations of the Greek letters (its README: "Alp", "Kap"), in alphabet order. A check only:
# the letter written is HYG's own text, and a letter outside this list stops the bake.
GREEK_LETTERS = (
    "Alp", "Bet", "Gam", "Del", "Eps", "Zet", "Eta", "The", "Iot", "Kap", "Lam", "Mu", "Nu", "Xi", "Omi", "Pi", "Rho",
    "Sig", "Tau", "Ups", "Phi", "Chi", "Psi", "Ome",
)
_LETTER = "(?:" + "|".join(GREEK_LETTERS) + ")"
BAYER_PATTERN = re.compile(rf"({_LETTER})(?:-([1-9]))?")
FLAMSTEED_PATTERN = re.compile(r"[1-9][0-9]*")
CONSTELLATION_PATTERN = re.compile(r"[A-Z][A-Za-z]{2}")
HIP_PATTERN = re.compile(r"[1-9][0-9]*")
# bf, as the README describes it: number, letter, superscript, constellation ("21Alp And", "Kap1Scl", "65Xi 1Cet")
BF_PATTERN = re.compile(rf"([1-9][0-9]*)?(?:({_LETTER}) ?([1-9])?)? *([A-Z][A-Za-z]{{2}})")
HYG_COLUMNS = ("id", "hip", "hd", "hr", "bf", "mag", "bayer", "flam", "con")
BEST_NEIGHBOUR_HEADER = ("source_id", "original_ext_source_id", "angular_distance", "number_of_neighbours", "xm_flag")

# Riello et al. 2021, A&A 649, A3 (arXiv:2012.01916), Table C.2: G - V as a cubic in BP-RP, for -0.5 < BP-RP < 5.0
G_MINUS_V = (-0.02704, 0.01424, -0.2156, 0.01426)
BP_RP_RANGE = (-0.5, 5.0)
# A row holding at least half of the star's light is at most this much fainter than the star
HALF_LIGHT_MAG = 2.5 * math.log10(2.0)

# designations.json (HYG only)
COLUMNS = ("hip", "flamsteed", "bayer", "constellation", "hd", "hr", "hygId")
SET_ASIDE_COLUMNS = ("hygId", "hip", "reason")
SET_ASIDE_REASONS = ("no-hip", "inconsistent-bf")
# links.json (ESA only)
LINK_COLUMNS = ("catalog", "catalogId", "layer", "pix", "index", "hip", "link")
LINK_SET_ASIDE_COLUMNS = ("hip", "reason")
LINK_SET_ASIDE_REASONS = ("no-best-neighbour", "best-neighbour-not-in-tiles", "no-colour-in-range", "fainter-than-half-light")
LINK_KINDS = ("hipparcos-row", "gaia-best-neighbour")


class Designation(NamedTuple):
    hyg_id: int
    hip: int
    flamsteed: int | None
    bayer: str | None
    constellation: str
    hd: int | None
    hr: int | None
    v_mag: float


class TileRow(NamedTuple):
    catalog_hipparcos: bool
    ident: int
    layer: int
    pix: int
    index: int
    # G for a Gaia row, Hp for a Hipparcos row, as the tile stores it
    mag: float
    # NaN when the row has no colour or its colour is B-V
    bp_rp: float


def parse_bf(bf: str) -> tuple[int | None, str | None, int | None, str]:
    """(Flamsteed number, letter, superscript, constellation) of a bf value; ValueError for one the README does not
    describe."""
    match = BF_PATTERN.fullmatch(bf)
    if match is None or (match.group(1) is None and match.group(2) is None):
        raise ValueError(f"bf {bf!r} is not a designation as the HYG README describes it")
    number, letter, superscript, constellation = match.groups()
    return (
        None if number is None else int(number),
        letter,
        None if superscript is None else int(superscript),
        constellation,
    )


def _optional_int(text: str, what: str, hyg_id: int) -> int | None:
    if text == "":
        return None
    if not HIP_PATTERN.fullmatch(text):
        raise ValueError(f"HYG {hyg_id}: {what} {text!r} is not a catalog number")
    return int(text)


def hyg_designations(text: str) -> tuple[list[Designation], list[tuple[int, int | None, str]]]:
    """Every HYG row with a Bayer letter or a Flamsteed number, and the rows set aside with their reason."""
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = [name for name in HYG_COLUMNS if name not in (reader.fieldnames or ())]
    if missing:
        raise ValueError(f"the HYG header lacks {missing}")
    designations: list[Designation] = []
    set_aside: list[tuple[int, int | None, str]] = []
    seen: set[int] = set()
    for r in reader:
        bayer, flam, bf, con = r["bayer"], r["flam"], r["bf"], r["con"]
        if not (bayer or flam or bf):
            continue
        hyg_id = int(r["id"])
        letter = superscript = None
        if bayer:
            match = BAYER_PATTERN.fullmatch(bayer)
            if match is None:
                raise ValueError(f"HYG {hyg_id}: bayer {bayer!r} is not a Greek letter as HYG writes them")
            letter = match.group(1)
            superscript = None if match.group(2) is None else int(match.group(2))
        if flam and not FLAMSTEED_PATTERN.fullmatch(flam):
            raise ValueError(f"HYG {hyg_id}: flam {flam!r} is not a Flamsteed number")
        if not CONSTELLATION_PATTERN.fullmatch(con):
            raise ValueError(f"HYG {hyg_id}: con {con!r} is not a constellation abbreviation")
        hip = _optional_int(r["hip"], "hip", hyg_id)
        if bf and parse_bf(bf) != (int(flam) if flam else None, letter, superscript, con):
            set_aside.append((hyg_id, hip, "inconsistent-bf"))
            continue
        if hip is None:
            set_aside.append((hyg_id, None, "no-hip"))
            continue
        if hip in seen:
            raise ValueError(f"HIP {hip} has two designated rows in HYG")
        seen.add(hip)
        v_mag = float(r["mag"])
        if not math.isfinite(v_mag):
            raise ValueError(f"HYG {hyg_id}: mag {r['mag']!r} is not a magnitude")
        designations.append(
            Designation(
                hyg_id=hyg_id,
                hip=hip,
                flamsteed=int(flam) if flam else None,
                bayer=bayer or None,
                constellation=con,
                hd=_optional_int(r["hd"], "hd", hyg_id),
                hr=_optional_int(r["hr"], "hr", hyg_id),
                v_mag=v_mag,
            )
        )
    return designations, set_aside


def best_neighbours(text: str) -> dict[int, int]:
    """The best-neighbour table as {HIP: Gaia source id}; a HIP or a source named twice stops the bake."""
    reader = csv.reader(io.StringIO(text, newline=""))
    if tuple(next(reader, ())) != BEST_NEIGHBOUR_HEADER:
        raise ValueError("the best-neighbour header differs from the bulk file's")
    by_hip: dict[int, int] = {}
    sources: set[int] = set()
    for row in reader:
        source, hip = int(row[0]), int(row[1])
        if hip in by_hip:
            raise ValueError(f"HIP {hip} has two best neighbours")
        if source in sources:
            raise ValueError(f"Gaia source {source} is the best neighbour of two HIP numbers")
        by_hip[hip] = source
        sources.add(source)
    return by_hip


def v_from_gaia(g_mag: float, bp_rp: float) -> float | None:
    """V from G and BP-RP by Riello et al. 2021, or None outside the relation's stated range (or without a colour)."""
    c = float(bp_rp)
    if not BP_RP_RANGE[0] < c < BP_RP_RANGE[1]:
        return None
    a0, a1, a2, a3 = G_MINUS_V
    return float(g_mag) - (a0 + a1 * c + a2 * c * c + a3 * c**3)


def tile_rows(tiles: Path, gaia_wanted: set[int]) -> tuple[dict[int, TileRow], dict[int, TileRow]]:
    """Every Hipparcos row by HIP number, and the Gaia rows whose source id is wanted, by source id."""
    parts: dict[str, list[np.ndarray]] = {name: [] for name in ("ident", "flags", "mag", "color", "layer", "pix", "index")}
    for core_path in sorted(tiles.glob("L*/*.core.bin")):
        header, core = decode_core_tile(core_path.read_bytes())
        _, detail = decode_detail_tile(core_path.with_name(core_path.name.replace(".core.", ".detail.")).read_bytes())
        n = len(core["flags"])
        parts["ident"].append((detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64))
        parts["flags"].append(core["flags"].astype(np.int64))
        parts["mag"].append(core["mag"])
        parts["color"].append(core["color"])
        parts["layer"].append(np.full(n, header.layer, dtype=np.int64))
        parts["pix"].append(np.full(n, header.pix, dtype=np.int64))
        parts["index"].append(np.arange(n, dtype=np.int64))
    cols = {name: np.concatenate(arrays) for name, arrays in parts.items()}
    is_hip = (cols["flags"] & FLAG_CATALOG_HIPPARCOS) != 0
    wanted = np.fromiter(gaia_wanted, dtype=np.int64, count=len(gaia_wanted))
    keep = np.flatnonzero(is_hip | np.isin(cols["ident"], wanted))
    mag = decode_mag(cols["mag"][keep])
    bp_rp = np.where((cols["flags"][keep] & FLAG_COLOR_B_V) != 0, np.nan, decode_color(cols["color"][keep]))
    hipparcos: dict[int, TileRow] = {}
    gaia: dict[int, TileRow] = {}
    for k, i in enumerate(keep):
        row = TileRow(
            bool(is_hip[i]), int(cols["ident"][i]), int(cols["layer"][i]), int(cols["pix"][i]), int(cols["index"][i]),
            float(mag[k]), float(bp_rp[k]),
        )
        table = hipparcos if row.catalog_hipparcos else gaia
        if row.ident in table:
            raise ValueError(f"{'HIP' if row.catalog_hipparcos else 'Gaia source'} {row.ident} is in the tiles twice")
        table[row.ident] = row
    return hipparcos, gaia


def link(
    designations: list[Designation],
    best: dict[int, int],
    hipparcos: dict[int, TileRow],
    gaia: dict[int, TileRow],
) -> tuple[list[list[Any]], list[tuple[int, str]]]:
    """The designated tile rows in HIP order, and the HIP numbers that reach no tile row, with their reason."""
    rows: list[list[Any]] = []
    set_aside: list[tuple[int, str]] = []
    taken: set[tuple[bool, int]] = set()
    for d in sorted(designations, key=lambda d: d.hip):
        if d.hip in hipparcos:
            row, how = hipparcos[d.hip], "hipparcos-row"
        elif d.hip not in best:
            set_aside.append((d.hip, "no-best-neighbour"))
            continue
        elif best[d.hip] not in gaia:
            set_aside.append((d.hip, "best-neighbour-not-in-tiles"))
            continue
        else:
            row, how = gaia[best[d.hip]], "gaia-best-neighbour"
            v_row = v_from_gaia(row.mag, row.bp_rp)
            if v_row is None:
                set_aside.append((d.hip, "no-colour-in-range"))
                continue
            if v_row - d.v_mag > HALF_LIGHT_MAG:
                set_aside.append((d.hip, "fainter-than-half-light"))
                continue
        key = (row.catalog_hipparcos, row.ident)
        if key in taken:
            raise ValueError(f"tile row {key} would be designated twice")
        taken.add(key)
        rows.append(["hipparcos-2007" if row.catalog_hipparcos else TILES_RELEASE, str(row.ident), row.layer, row.pix, row.index, d.hip, how])
    return rows, set_aside


def designation_rows(designations: list[Designation]) -> list[list[Any]]:
    """designations.json's rows in HIP order: HYG's values only."""
    return [[d.hip, d.flamsteed, d.bayer, d.constellation, d.hd, d.hr, d.hyg_id] for d in sorted(designations, key=lambda d: d.hip)]


def build_from(cache: Path, tiles: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(designations.json, links.json) as documents."""
    designations, aside_hyg = hyg_designations((cache / HYG.filename).read_text(encoding="utf-8"))
    with gzip.open(cache / BEST_NEIGHBOUR.filename, "rt", encoding="utf-8", newline="") as fh:
        best = best_neighbours(fh.read())
    hipparcos, gaia = tile_rows(tiles, set(best.values()))
    links, aside_link = link(designations, best, hipparcos, gaia)
    rows = designation_rows(designations)
    aside_hyg = sorted(aside_hyg)
    designations_doc = {
        "format": FORMAT,
        "formatVersion": FORMAT_VERSION,
        "evidenceKind": EVIDENCE_KIND,
        "source": {"id": HYG_SOURCE_ID, "sha256": HYG.sha256},
        "counts": {
            "designated": len(rows) + len(aside_hyg),
            "rows": len(rows),
            "setAside": {reason: sum(1 for s in aside_hyg if s[2] == reason) for reason in SET_ASIDE_REASONS},
        },
        "columns": list(COLUMNS),
        "rows": rows,
        "setAsideColumns": list(SET_ASIDE_COLUMNS),
        "setAside": [list(s) for s in aside_hyg],
    }
    links_doc = {
        "format": LINKS_FORMAT,
        "formatVersion": FORMAT_VERSION,
        "evidenceKind": EVIDENCE_KIND,
        "tilesRelease": TILES_RELEASE,
        "link": {"id": BEST_NEIGHBOUR_SOURCE_ID, "sha256": BEST_NEIGHBOUR.sha256},
        # Read by the half-light guard, not written here: which HIP numbers carry a designation, and each one's V
        "guardRead": {"id": HYG_SOURCE_ID, "sha256": HYG.sha256},
        "counts": {
            "hips": len(rows),
            "rows": len(links),
            "links": {kind: sum(1 for r in links if r[6] == kind) for kind in LINK_KINDS},
            "setAside": {reason: sum(1 for s in aside_link if s[1] == reason) for reason in LINK_SET_ASIDE_REASONS},
        },
        "columns": list(LINK_COLUMNS),
        "rows": links,
        "setAsideColumns": list(LINK_SET_ASIDE_COLUMNS),
        "setAside": [list(s) for s in aside_link],
    }
    return designations_doc, links_doc


def dumps_designations(doc: dict[str, Any]) -> str:
    """Single-line compact JSON with a final newline (one line, so the trip guard never pairs values across rows)."""
    return json.dumps(doc, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


_PRODUCED_BY = {
    "method": "script",
    "script": "scripts/bake_star_designations.py",
    "command": "uv run python scripts/bake_star_designations.py",
}


def _record(sources: list[dict[str, Any]], path: str, out_bytes: bytes, note: str) -> AssetProvenanceRecord:
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": sources,
            "files": [
                {
                    "path": path,
                    "sha256": hashlib.sha256(out_bytes).hexdigest(),
                    "sources": [source["id"] for source in sources],
                    "producedBy": _PRODUCED_BY,
                    "note": note,
                }
            ],
            "fileSets": [],
        }
    )


def build_provenance_record(out_dir: Path) -> AssetProvenanceRecord:
    """The record of designations.json: HYG alone, CC BY-SA 4.0."""
    out_bytes = (out_dir / OUT_NAME).read_bytes()
    counts = json.loads(out_bytes)["counts"]
    source = {
        "id": HYG_SOURCE_ID,
        "title": "HYG Database v4.1 (Hipparcos, Yale Bright Star and Gliese catalogs combined)",
        "url": HYG.url,
        "version": "v4.1",
        "retrieved": RETRIEVED,
        "license": {
            "spdx": "CC-BY-SA-4.0",
            "name": "Creative Commons Attribution-ShareAlike 4.0 International",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "conditions": (
                "The repository's LICENSE (read on GitHub and on Codeberg on 2026-11-12) puts HYG v4.0 and later "
                "under this licence: credit the work and share an adaptation under the same licence."
            ),
        },
        "credit": "HYG Database, astronexus",
        "sha256": HYG.sha256,
        "note": (
            f"The file hyg/CURRENT/{HYG.filename} of github.com/astronexus/HYG-Database at the commit the url names, "
            f"{HYG.bytes:,} bytes, fetched at {HYG.fetched}. That repository is archived; HYG now lives at "
            "codeberg.org/astronexus/hyg, whose current version is v4.4. Read: id, hip, hd, hr, mag and the "
            "designation fields bayer, flam and con, with bf as a check; the README says they come primarily from "
            "the Yale Bright Star Catalogue, fifth edition, with Flamsteed numbers added from Wagman (1987)."
        ),
    }
    aside = counts["setAside"]
    note = (
        f"The Bayer letters and Flamsteed numbers of {counts['rows']:,} HYG v4.1 rows, keyed by HIP number, in HIP "
        "order, evidenceKind 'catalog-derived', every value as HYG wrote it (hip, flam, bayer, con, hd, hr, id); "
        "nothing else is in this file. "
        f"Of {counts['designated']:,} designated HYG rows, set aside: {aside['no-hip']} without a HIP number and "
        f"{aside['inconsistent-bf']} whose bf disagrees with its own fields. Which tile row each HIP number names is in "
        "../designation-links/links.json, ESA data under its own record. Offered under CC BY-SA 4.0 with HYG's "
        "attribution (HYG Database, astronexus, codeberg.org/astronexus/hyg), as it is adapted from HYG."
    )
    return _record([source], OUT_NAME, out_bytes, note)


def build_links_record(links_dir: Path, tiles: Path = DEFAULT_TILES) -> AssetProvenanceRecord:
    """The record of links.json: ESA's sources alone, under ESA's terms as the tiles are."""
    out_bytes = (links_dir / LINKS_NAME).read_bytes()
    counts = json.loads(out_bytes)["counts"]
    tiles_sources = {source["id"]: source for source in _read_json(tiles / "PROVENANCE.json")["sources"]}
    bn_tiles = tiles_sources["gaia-dr3-hipparcos2-best-neighbour"]
    sources = [
        {
            "id": BEST_NEIGHBOUR_SOURCE_ID,
            "title": "Gaia EDR3 to Hipparcos-2 best-neighbour crossmatch, bulk file",
            "url": BEST_NEIGHBOUR.url,
            "version": "EDR3, valid for DR3",
            "retrieved": RETRIEVED,
            "license": bn_tiles["license"],
            "credit": bn_tiles["credit"],
            "citation": bn_tiles["citation"],
            "doi": bn_tiles["doi"],
            "sha256": BEST_NEIGHBOUR.sha256,
            "note": (
                f"{BEST_NEIGHBOUR.bytes:,} bytes, fetched at {BEST_NEIGHBOUR.fetched}; its MD5 equals the one in that "
                "folder's _MD5SUM.txt. ESA's DR3 bulk readme says the EDR3 cross-matches are valid for DR3. Read: "
                "source_id and original_ext_source_id (the HIP number)."
            ),
        },
        {
            **tiles_sources["gaia-dr3"],
            "note": f"Read from the {TILES_RELEASE} tiles: each Gaia row's source id, place, G and BP-RP.",
        },
        {
            **tiles_sources["hipparcos-2007"],
            "note": f"Read from the {TILES_RELEASE} tiles: each Hipparcos row's HIP number and place.",
        },
    ]
    links, aside = counts["links"], counts["setAside"]
    note = (
        f"Which {TILES_RELEASE} tile row each designated HIP number names: {counts['rows']:,} rows in HIP order "
        "(catalog, catalogId, layer, pix, index, hip, link), evidenceKind 'catalog-derived', linked by HIP number "
        f"only: {links['hipparcos-row']} Hipparcos rows by their own HIP, {links['gaia-best-neighbour']:,} Gaia rows "
        "as the best neighbour of that HIP. No designation is in this file; the designations are "
        "../designations/designations.json, under its own record and licence, joined by HIP number when read. "
        f"The guard read HYG v4.1 ({HYG.filename}, sha256 {HYG.sha256}) to decide which links are kept: which HIP "
        "numbers carry a designation, and each one's V, which is not written here. A Gaia row is kept only if its "
        "V, from its G and BP-RP by Riello et al. 2021 (A&A 649, A3, Table C.2, for -0.5 < BP-RP < 5.0), is at most "
        "2.5 log10 2 mag fainter than HYG's V, so it holds at least half the star's light [proposed: a bound derived "
        "from half the light, not tuned; it drops a row rather than risk a wrong star]. Of "
        f"{counts['hips']:,} designated HIP numbers, set aside: {aside['no-best-neighbour']} with no best neighbour, "
        f"{aside['best-neighbour-not-in-tiles']} whose neighbour is not a tile row, {aside['fainter-than-half-light']} "
        f"fainter than half the light, {aside['no-colour-in-range']} without a colour in range. Offered under CC "
        "BY-NC 3.0 IGO with credit ESA/Gaia/DPAC and ESA Hipparcos, as its sources are."
    )
    return _record(sources, LINKS_NAME, out_bytes, note)


def check_inputs(cache: Path) -> None:
    for spec in INPUTS:
        path = cache / spec.filename
        if not path.is_file():
            raise SystemExit(
                f"[designations] missing {path}; run: uv run python scripts/fetch_download.py .cache/star-designations/{spec.filename}"
            )
        raw = path.read_bytes()
        if len(raw) != spec.bytes:
            raise SystemExit(f"[designations] {path.name} has {len(raw)} bytes, expected {spec.bytes}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != spec.sha256:
            raise SystemExit(f"[designations] {path.name} has sha256 {digest}, expected {spec.sha256}")


def verify_outputs(cache: Path, out_dir: Path, links_dir: Path, tiles: Path = DEFAULT_TILES) -> list[str]:
    """Byte comparison is sound here: every value written is HYG's text or a tile's integer, copied with no
    arithmetic; the one computed value, V from G, only decides whether a link is written."""
    check_inputs(cache)
    designations_doc, links_doc = build_from(cache, tiles)
    errors: list[str] = []
    for folder, name, doc, record in (
        (out_dir, OUT_NAME, designations_doc, lambda: build_provenance_record(out_dir)),
        (links_dir, LINKS_NAME, links_doc, lambda: build_links_record(links_dir, tiles)),
    ):
        target = folder / name
        if not target.is_file() or target.read_text(encoding="utf-8") != dumps_designations(doc):
            errors.append(f"{target} differs from a fresh bake")
            continue
        record_path = folder / "PROVENANCE.json"
        if not record_path.is_file() or record_path.read_text(encoding="utf-8") != dumps_record(record()):
            errors.append(f"{record_path} differs from a fresh bake")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--links-out", type=Path, default=DEFAULT_LINKS_OUT)
    parser.add_argument("--tiles", type=Path, default=DEFAULT_TILES)
    parser.add_argument("--verify", action="store_true", help="check committed outputs byte for byte against a fresh bake")
    args = parser.parse_args(argv)
    if args.verify:
        errors = verify_outputs(args.cache, args.out, args.links_out, args.tiles)
        for err in errors:
            print(f"[designations] {err}", file=sys.stderr)
        if not errors:
            print("[designations] both files and both records match a fresh bake")
        return 1 if errors else 0
    check_inputs(args.cache)
    designations_doc, links_doc = build_from(args.cache, args.tiles)
    args.out.mkdir(parents=True, exist_ok=True)
    args.links_out.mkdir(parents=True, exist_ok=True)
    (args.out / OUT_NAME).write_text(dumps_designations(designations_doc), encoding="utf-8")
    (args.out / "PROVENANCE.json").write_text(dumps_record(build_provenance_record(args.out)), encoding="utf-8")
    (args.links_out / LINKS_NAME).write_text(dumps_designations(links_doc), encoding="utf-8")
    (args.links_out / "PROVENANCE.json").write_text(dumps_record(build_links_record(args.links_out, args.tiles)), encoding="utf-8")
    for path in (args.out / OUT_NAME, args.out / "PROVENANCE.json", args.links_out / LINKS_NAME, args.links_out / "PROVENANCE.json"):
        print(f"[designations] {path.parent.name}/{path.name}: {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
