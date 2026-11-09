#!/usr/bin/env python3
"""Bake the bright-star 3D supplement (data-11).

The G 12 star tiles (`apps/web/public/catalog/stars/gaia-dr3/`) take 226 bright stars from Hipparcos rather than
Gaia DR3 (their `bright-star-merge.json`, action `use_hipparcos`) and store them lossily for drawing: positions moved
to J2016.0, proper motions as 16-bit floats, distance bounds as 8-bit codes. This bake writes those 226 at full
precision, for a 3D flight, in the contract of `services/contracts/bright_stars.py`.

Input, in `.cache/hipparcos-bright-3d/` (registered in `data/downloads.json`) and pinned by size and SHA-256: one ADQL
query of the ESA Gaia archive's TAP service for exactly those 226 HIP numbers, Hipparcos, the New Reduction (van
Leeuwen 2007, `public.hipparcos_newreduction`) joined on hip with the Hipparcos Catalogue (ESA 1997,
`public.hipparcos`) for V. The ESA Space Science Archives distribute the data they host under CC BY-NC 3.0 IGO, credit
ESA, and commercial use needs ESA's prior authorisation.

Also read, from the tiles' folder (its own PROVENANCE.json records where they came from): the merge log, for the Gaia
DR3 best neighbour and the reason Hipparcos wins; `names.json`, for IAU names; and the core and detail tiles, for each
star's row (layer, HEALPix nested pixel, row index), found by HIP number among the rows flagged as Hipparcos.

Outputs in `apps/web/public/catalog/stars/hipparcos-bright-3d/`:
- `stars.json`: `BrightStarSupplement` (`evidenceKind: "catalog-derived"`), one row per star in HIP order, every value
  as the archive wrote it (a blank is null), positions at J1991.25 and not moved, no distance;
- `PROVENANCE.json`: `AssetProvenanceRecord` pinning `stars.json` and naming its four sources.

Usage, from the repository root:
    uv run python scripts/bake_bright_stars_3d.py
    uv run python scripts/bake_bright_stars_3d.py --verify
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.bright_stars import (  # noqa: E402
    BRIGHT_STAR_COLUMNS,
    BRIGHT_STAR_EPOCH_JULIAN_YEAR,
    BRIGHT_STAR_EVIDENCE_KIND,
    BRIGHT_STAR_FRAME,
    BRIGHT_STAR_SUPPLEMENT_FORMAT,
    BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
    BrightStarSupplement,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402
from services.science.catalog_tiles import FLAG_CATALOG_HIPPARCOS, decode_core_tile, decode_detail_tile  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "hipparcos-bright-3d"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "hipparcos-bright-3d"
DEFAULT_TILES = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
TILES_RELEASE = "gaia-dr3"
OUT_NAME = "stars.json"
CATALOGUE = "Hipparcos, the New Reduction (van Leeuwen 2007), with V from the Hipparcos Catalogue (ESA 1997)"
TAP_SYNC = "https://gea.esac.esa.int/tap-server/tap/sync"
ARCHIVE_SOURCE_ID = "hipparcos-2007-bright-3d"
HIPPARCOS_1997_SOURCE_ID = "hipparcos-1997"
# Sources the tiles' record already describes, copied from it with a note of how this bake uses them
TILES_SOURCE_IDS = ("gaia-dr3-hipparcos2-best-neighbour", "iau-csn")

# The contract's parallax rule: a 1000 / p distance only for p > 0 and e / p at most this
MAX_RELATIVE_PARALLAX_ERROR = 0.1

# The columns the query asked for, in its order; the bake refuses an answer whose header differs. multflag and ccdm
# (Hipparcos 1997) are read and not written.
ARCHIVE_HEADER = (
    "hip", "ra", "dec", "plx", "e_plx", "pm_ra", "pm_de", "e_ra_rad", "e_de_rad", "e_pm_ra", "e_pm_de", "hp_mag",
    "b_v", "e_b_v", "sn", "nc", "vmag", "multflag", "ccdm",
)


@dataclass(frozen=True)
class ArchiveInput:
    filename: str
    rows: int
    bytes: int
    sha256: str
    fetched: str


ARCHIVE = ArchiveInput(
    filename="hipparcos_bright_3d.csv",
    rows=226,
    bytes=28_076,
    sha256="96b8f753deb086e26e3ac0bafdf219ff3ab4b8e3dba289d8f29714e32836e631",
    fetched="2026-11-09T21:13:20Z",
)


def archive_query(hips: list[int]) -> str:
    """The ADQL the archive answered, for these HIP numbers."""
    return (
        "SELECT " + ",".join(
            [f"n.{name}" for name in ARCHIVE_HEADER[:16]] + [f"h.{name}" for name in ARCHIVE_HEADER[16:]]
        )
        + " FROM public.hipparcos_newreduction AS n LEFT OUTER JOIN public.hipparcos AS h ON h.hip=n.hip"
        + " WHERE n.hip IN (" + ",".join(str(hip) for hip in sorted(hips)) + ") ORDER BY n.hip"
    )


def archive_url(hips: list[int]) -> str:
    """The GET request that fetched the answer (data/downloads.json lists the same URL)."""
    return f"{TAP_SYNC}?REQUEST=doQuery&LANG=ADQL&FORMAT=csv&QUERY=" + quote(archive_query(hips), safe=",()=")


def parse_archive_csv(text: str) -> dict[int, dict[str, str]]:
    header = text.split("\n", 1)[0].rstrip("\r")
    if header != ",".join(ARCHIVE_HEADER):
        raise ValueError("the archive answer's header differs from the query's columns")
    rows: dict[int, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(text, newline="")):
        hip = int(row["hip"])
        if hip in rows:
            raise ValueError(f"HIP {hip} appears twice in the archive answer")
        rows[hip] = row
    return rows


def hipparcos_decisions(merge: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """The merge log's decisions to take a star from Hipparcos, by HIP number."""
    return {d["hip"]: d for d in merge["decisions"] if d["action"] == "use_hipparcos"}


def names_by_hip(names: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for entry in names["names"]:
        hip = entry.get("hip")
        if hip is None:
            continue
        if hip in out:
            raise ValueError(f"HIP {hip} has two names")
        out[hip] = entry["name"]
    return out


def tile_places(tiles: Path) -> dict[int, tuple[int, int, int]]:
    """(layer, pixel, row index) of every row flagged as a Hipparcos entry in the core tiles, by HIP number."""
    places: dict[int, tuple[int, int, int]] = {}
    for core_path in sorted(tiles.glob("L*/*.core.bin")):
        header, core = decode_core_tile(core_path.read_bytes())
        hipparcos = np.flatnonzero(core["flags"] & FLAG_CATALOG_HIPPARCOS)
        if not hipparcos.size:
            continue
        _, detail = decode_detail_tile(core_path.with_name(core_path.name.replace(".core.", ".detail.")).read_bytes())
        for index in hipparcos:
            hip = (int(detail["idHi"][index]) << 32) | int(detail["idLo"][index])
            if hip in places:
                raise ValueError(f"HIP {hip} is in the tiles twice")
            places[hip] = (header.layer, header.pix, int(index))
    return places


def optional_float(text: str) -> float | None:
    return float(text) if text != "" else None


def row_of(hip: int, r: dict[str, str], decision: dict[str, Any], name: str | None, place: tuple[int, int, int]) -> list[Any]:
    return [
        hip,
        name,
        float(r["ra"]),
        float(r["dec"]),
        float(r["e_ra_rad"]),
        float(r["e_de_rad"]),
        float(r["plx"]),
        float(r["e_plx"]),
        float(r["pm_ra"]),
        float(r["pm_de"]),
        float(r["e_pm_ra"]),
        float(r["e_pm_de"]),
        float(r["hp_mag"]),
        optional_float(r["vmag"]),
        optional_float(r["b_v"]),
        int(r["sn"]),
        int(r["nc"]),
        decision["gaiaSourceId"],
        decision["reason"],
        *place,
    ]


def build_supplement(
    archive: dict[int, dict[str, str]],
    decisions: dict[int, dict[str, Any]],
    names: dict[int, str],
    places: dict[int, tuple[int, int, int]],
) -> BrightStarSupplement:
    if set(archive) != set(decisions):
        extra, missing = sorted(set(archive) - set(decisions)), sorted(set(decisions) - set(archive))
        raise ValueError(f"the archive answer and the merge differ: extra HIP {extra[:5]}, missing HIP {missing[:5]}")
    unplaced = sorted(hip for hip in decisions if hip not in places)
    if unplaced:
        raise ValueError(f"HIP {unplaced[:5]} not found among the tiles' Hipparcos rows")
    rows = [row_of(hip, archive[hip], decisions[hip], names.get(hip), places[hip]) for hip in sorted(decisions)]
    return BrightStarSupplement.model_validate(
        {
            "format": BRIGHT_STAR_SUPPLEMENT_FORMAT,
            "formatVersion": BRIGHT_STAR_SUPPLEMENT_FORMAT_VERSION,
            "evidenceKind": BRIGHT_STAR_EVIDENCE_KIND,
            "catalogue": CATALOGUE,
            "frame": BRIGHT_STAR_FRAME,
            "epochJulianYear": BRIGHT_STAR_EPOCH_JULIAN_YEAR,
            "tilesRelease": TILES_RELEASE,
            "columns": list(BRIGHT_STAR_COLUMNS),
            "rows": rows,
        }
    )


def parallax_distance_pc(parallax_mas: float, error_mas: float) -> tuple[float, float, float] | None:
    """The contract's parallax rule: (1000 / p, 1000 / (p + e), 1000 / (p - e)) parsecs for p > 0 with e / p at most
    0.1, and no distance otherwise (the inverse is biased there; a reader must choose and disclose an estimator)."""
    if parallax_mas <= 0 or error_mas / parallax_mas > MAX_RELATIVE_PARALLAX_ERROR:
        return None
    return 1000.0 / parallax_mas, 1000.0 / (parallax_mas + error_mas), 1000.0 / (parallax_mas - error_mas)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_from(cache: Path, tiles: Path) -> BrightStarSupplement:
    return build_supplement(
        parse_archive_csv((cache / ARCHIVE.filename).read_text(encoding="utf-8")),
        hipparcos_decisions(_read_json(tiles / "bright-star-merge.json")),
        names_by_hip(_read_json(tiles / "names.json")),
        tile_places(tiles),
    )


def dumps_supplement(supplement: BrightStarSupplement) -> str:
    """Single-line compact JSON with a final newline (one line, so the trip guard never pairs values across rows)."""
    return json.dumps(supplement.to_contract_dict(), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


def _summary(rows: list[list[Any]]) -> dict[str, Any]:
    """The counts the record's note gives, from the written table."""
    p, e = BRIGHT_STAR_COLUMNS.index("parallaxMas"), BRIGHT_STAR_COLUMNS.index("parallaxErrorMas")
    by_hip = {row[0]: row for row in rows}
    non_positive = sorted(row[0] for row in rows if row[p] <= 0)
    noisy = [row[0] for row in rows if row[p] > 0 and row[e] / row[p] > MAX_RELATIVE_PARALLAX_ERROR]
    gap = by_hip[71681][p] - by_hip[71683][p]
    separation_au = (1000.0 / by_hip[71683][p] - 1000.0 / by_hip[71681][p]) * 206264.806
    return {
        "stars": len(rows),
        "withDistance": sum(parallax_distance_pc(row[p], row[e]) is not None for row in rows),
        "noisy": len(noisy),
        "nonPositive": non_positive,
        "tolimanGapMas": f"{gap:.2f}",
        "tolimanSeparationAu": f"{round(separation_au, -2):,.0f}",
    }


def build_provenance_record(out_dir: Path, tiles: Path = DEFAULT_TILES) -> AssetProvenanceRecord:
    out_bytes = (out_dir / OUT_NAME).read_bytes()
    rows = json.loads(out_bytes)["rows"]
    s = _summary(rows)
    tiles_sources = {source["id"]: source for source in _read_json(tiles / "PROVENANCE.json")["sources"]}
    licence = tiles_sources["hipparcos-2007"]["license"]
    hips = [row[0] for row in rows]
    sources = [
        {
            "id": ARCHIVE_SOURCE_ID,
            "title": "Hipparcos, the New Reduction of the Raw Data: the 226 stars the G 12 tiles take from Hipparcos",
            "url": archive_url(hips),
            "version": "2007 new reduction",
            "retrieved": ARCHIVE.fetched[:10],
            "license": licence,
            "credit": "ESA",
            "citation": "van Leeuwen, F. 2007, A&A 474, 653",
            "doi": "10.1051/0004-6361:20078357",
            "sha256": ARCHIVE.sha256,
            "note": (
                f"One ADQL query of the ESA Gaia archive's TAP service, as the url gives it: public.hipparcos_newreduction "
                f"left outer join public.hipparcos on hip, for the {len(hips)} HIP numbers, ordered by hip. Fetched at "
                f"{ARCHIVE.fetched}: {ARCHIVE.rows} rows, {ARCHIVE.bytes:,} bytes of CSV. The ESA Space Science "
                "Archives terms (read 2026-11-09) put the data they host under CC BY-NC 3.0 IGO, credit ESA; commercial "
                "use needs ESA's prior authorisation."
            ),
        },
        {
            "id": HIPPARCOS_1997_SOURCE_ID,
            "title": "The Hipparcos and Tycho Catalogues (ESA 1997)",
            "url": "https://gea.esac.esa.int/archive/",
            "version": "1997",
            "license": licence,
            "credit": "ESA",
            "citation": "ESA 1997, The Hipparcos and Tycho Catalogues, ESA SP-1200",
            "note": (
                f"Table public.hipparcos, joined on hip in the {ARCHIVE_SOURCE_ID} query: only its V magnitude (vmag) is "
                "written; multflag and ccdm are read and not written."
            ),
        },
        {
            **tiles_sources["gaia-dr3-hipparcos2-best-neighbour"],
            "note": (
                f"Read from the {TILES_RELEASE} tiles' bright-star-merge.json (that folder's PROVENANCE.json records "
                "its query): each star's Gaia DR3 best neighbour, or none, and why Hipparcos wins."
            ),
        },
        {
            **tiles_sources["iau-csn"],
            "note": f"Read from the {TILES_RELEASE} tiles' names.json: the IAU names, by HIP number.",
        },
    ]
    non_positive = ", ".join(f"HIP {hip}" for hip in s["nonPositive"])
    note = (
        f"All {s['stars']} stars the {TILES_RELEASE} G 12 tiles take from Hipparcos (bright-star-merge.json, action "
        "use_hipparcos), in HIP order, evidenceKind 'catalog-derived': Hipparcos 2007 astrometry with its errors, Hp, "
        "B-V, solution type and components; V from Hipparcos 1997; the Gaia DR3 best neighbour and the merge's reason; "
        "the IAU name; and each star's row in the tiles' core blocks, found by HIP number in the detail tiles. ICRS at "
        "J1991.25, not moved. No distance is written: the contract's parallax rule gives 1000 / p only where p > 0 and "
        f"e / p is at most 0.1 ({s['withDistance']} stars); {s['noisy']} have e / p above 0.1 and "
        f"{len(s['nonPositive'])} a parallax at or below zero ({non_positive}). Toliman (HIP 71681) and Rigil "
        f"Kentaurus (HIP 71683) are one binary, yet their 2007 parallaxes differ by {s['tolimanGapMas']} mas, so "
        f"placing each by its own puts them about {s['tolimanSeparationAu']} au apart along the line of sight; "
        "Kervella et al. 2016 give the pair one parallax, 747.17 ± 0.61 mas. Offered under CC BY-NC 3.0 IGO with "
        "credit ESA, as its sources are."
    )
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": sources,
            "files": [
                {
                    "path": OUT_NAME,
                    "sha256": hashlib.sha256(out_bytes).hexdigest(),
                    "sources": [source["id"] for source in sources],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_bright_stars_3d.py",
                        "command": "uv run python scripts/bake_bright_stars_3d.py",
                    },
                    "note": note,
                }
            ],
            "fileSets": [],
        }
    )


def check_inputs(cache: Path) -> None:
    path = cache / ARCHIVE.filename
    if not path.is_file():
        raise SystemExit(
            f"[bright-3d] missing {path}; run: uv run python scripts/fetch_download.py .cache/hipparcos-bright-3d/{ARCHIVE.filename}"
        )
    raw = path.read_bytes()
    if len(raw) != ARCHIVE.bytes:
        raise SystemExit(f"[bright-3d] {path.name} has {len(raw)} bytes, expected {ARCHIVE.bytes}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ARCHIVE.sha256:
        raise SystemExit(f"[bright-3d] {path.name} has sha256 {digest}, expected {ARCHIVE.sha256}")


def verify_outputs(cache: Path, out_dir: Path, tiles: Path = DEFAULT_TILES) -> list[str]:
    """Byte comparison is sound here: every value is parsed from the archive's text and written back by json, with no
    arithmetic."""
    check_inputs(cache)
    errors: list[str] = []
    target = out_dir / OUT_NAME
    if not target.is_file() or target.read_text(encoding="utf-8") != dumps_supplement(build_from(cache, tiles)):
        errors.append(f"{target} differs from a fresh bake")
    if not errors:
        record_path = out_dir / "PROVENANCE.json"
        if not record_path.is_file() or record_path.read_text(encoding="utf-8") != dumps_record(build_provenance_record(out_dir, tiles)):
            errors.append(f"{record_path} differs from a fresh bake")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--tiles", type=Path, default=DEFAULT_TILES)
    parser.add_argument("--verify", action="store_true", help="check committed outputs byte-for-byte against a fresh bake")
    args = parser.parse_args(argv)
    if args.verify:
        errors = verify_outputs(args.cache, args.out, args.tiles)
        for err in errors:
            print(f"[bright-3d] {err}", file=sys.stderr)
        if not errors:
            print("[bright-3d] stars.json and PROVENANCE.json match a fresh bake")
        return 1 if errors else 0
    check_inputs(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / OUT_NAME).write_text(dumps_supplement(build_from(args.cache, args.tiles)), encoding="utf-8")
    (args.out / "PROVENANCE.json").write_text(dumps_record(build_provenance_record(args.out, args.tiles)), encoding="utf-8")
    for name in (OUT_NAME, "PROVENANCE.json"):
        print(f"[bright-3d] {name}: {(args.out / name).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
