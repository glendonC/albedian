#!/usr/bin/env python3
"""Bake the OpenNGC deep-sky catalogue (data-7a).

Inputs, in `.cache/m2/` (registered in `data/downloads.json`) and pinned by size and SHA-256, both from OpenNGC
release v20260501 (github.com/mattiaverga/OpenNGC, tag v20260501, `database_files/`), CC-BY-SA-4.0, credit
Mattia Verga:
- `openngc_v20260501_NGC.csv`: 13,969 NGC and IC objects, 3,876,288 bytes;
- `openngc_v20260501_addendum.csv`: 64 further objects (among them the Large Magellanic Cloud, the Coalsack and
  Messier objects outside NGC and IC), 17,484 bytes.

Outputs in `apps/web/public/catalog/deepsky/`, validated by `services/contracts/deep_sky.py` and
`services/contracts/provenance.py`:
- `openngc.json`: `DeepSkyCatalog` (`evidenceKind: "catalog-derived"`), every row of both tables;
- `PROVENANCE.json`: `AssetProvenanceRecord` pinning `openngc.json` and both inputs.

OpenNGC is CC-BY-SA-4.0, so `openngc.json`, an adaptation of it, is offered under CC-BY-SA-4.0 too.

Each row keeps OpenNGC's own values: right ascension and declination are its J2000 sexagesimal values in degrees,
rounded to 1e-6 degree (0.0036 arcsec, finer than the 0.01 s and 0.1 arcsec it writes); a blank cell is null; a
list cell (NGC, IC, Identifiers, Common names) is split at its commas, each item stripped of surrounding blanks and
empty items dropped (in v20260501, 16 stripped items and 1 empty one, all in the Identifiers column). Columns the catalogue does not carry
(near-infrared magnitudes, parallax, proper motion, radial velocity, redshift, Hubble type, central stars, notes,
sources) are left out, not altered.

Usage, from the repository root:
    uv run python scripts/bake_deepsky_openngc.py
    uv run python scripts/bake_deepsky_openngc.py --verify
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

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.deep_sky import (  # noqa: E402
    DEEP_SKY_CATALOG_FORMAT,
    DEEP_SKY_CATALOG_FORMAT_VERSION,
    DEEP_SKY_COLUMNS,
    DEEP_SKY_EVIDENCE_KIND,
    DeepSkyCatalog,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "m2"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "deepsky"
OUT_NAME = "openngc.json"
RELEASE = "OpenNGC v20260501"
REPO = "https://github.com/mattiaverga/OpenNGC"
RAW = "https://raw.githubusercontent.com/mattiaverga/OpenNGC/v20260501"
RETRIEVED = "2026-11-09"
CREDIT = "OpenNGC, Mattia Verga (github.com/mattiaverga/OpenNGC)"
# .reuse/dep5 at tag v20260501: "Copyright: 2023 Mattia Verga"
COPYRIGHT = "Copyright 2023 Mattia Verga"
# The DOI badge of the README at tag v20260501 (a GAVO VO DOI, dc.zah.uni-heidelberg.de/voidoi)
DOI = "10.21938/y.1ejWUD_MQ6b_eDFoVbbw"
CITATION = f"Verga, M., OpenNGC: a license friendly NGC/IC objects database, release v20260501, doi:{DOI}."
# One sentence of ours naming what OpenNGC merges, then the three acknowledgements its README at tag v20260501
# gives for NED, HyperLeda and SIMBAD, as written there (line breaks joined, a final full stop added)
ACKNOWLEDGEMENT = (
    "OpenNGC merges NED, HyperLEDA, SIMBAD, HEASARC tables and Harold Corwin's NGC/IC Positions and Notes. "
    "This research has made use of the NASA/IPAC Extragalactic Database (NED) which is operated by the Jet "
    "Propulsion Laboratory, California Institute of Technology, under contract with the National Aeronautics and "
    "Space Administration. We acknowledge the usage of the HyperLeda database (http://leda.univ-lyon1.fr). This "
    "research has made use of the SIMBAD database, operated at CDS, Strasbourg, France."
)

# OpenNGC v20260501 NGC_guide.txt, "Type: Object type"
TYPE_LABELS = {
    "*": "Star",
    "**": "Double star",
    "*Ass": "Association of stars",
    "OCl": "Open Cluster",
    "GCl": "Globular Cluster",
    "Cl+N": "Star cluster + Nebula",
    "G": "Galaxy",
    "GPair": "Galaxy Pair",
    "GTrpl": "Galaxy Triplet",
    "GGroup": "Group of galaxies",
    "PN": "Planetary Nebula",
    "HII": "HII Ionized region",
    "DrkN": "Dark Nebula",
    "EmN": "Emission Nebula",
    "Neb": "Nebula",
    "RfN": "Reflection Nebula",
    "SNR": "Supernova remnant",
    "Nova": "Nova star",
    "NonEx": "Nonexistent object",
    "Dup": "Duplicated object (see NGC or IC columns to find the master object)",
    "Other": "Other classification (see object notes)",
}

# The source columns read, in OpenNGC's header order; the bake refuses a table whose header differs
OPENNGC_HEADER = (
    "Name;Type;RA;Dec;Const;MajAx;MinAx;PosAng;B-Mag;V-Mag;J-Mag;H-Mag;K-Mag;SurfBr;Hubble;Pax;Pm-RA;Pm-Dec;RadVel;"
    "Redshift;Cstar U-Mag;Cstar B-Mag;Cstar V-Mag;M;NGC;IC;Cstar Names;Identifiers;Common names;NED notes;"
    "OpenNGC notes;Sources"
)


@dataclass(frozen=True)
class OpenNgcInput:
    source_id: str
    filename: str
    upstream: str
    rows: int
    bytes: int
    sha256: str


INPUTS = (
    OpenNgcInput(
        source_id="openngc-v20260501-ngc",
        filename="openngc_v20260501_NGC.csv",
        upstream="database_files/NGC.csv",
        rows=13_969,
        bytes=3_876_288,
        sha256="840fe0c9ee1332e551b2e722a0e92726cd7b157914a3d2177602832aadd3aa9e",
    ),
    OpenNgcInput(
        source_id="openngc-v20260501-addendum",
        filename="openngc_v20260501_addendum.csv",
        upstream="database_files/addendum.csv",
        rows=64,
        bytes=17_484,
        sha256="1d8f0914e643ada325a5a94d88d8fefad6a4937a2f77cc34f21483af22b11983",
    ),
)


def sexagesimal_deg(text: str, hours: bool) -> float:
    """'HH:MM:SS.SS' (hours) or '+DD:MM:SS.S' (degrees) to degrees, rounded to 1e-6 degree."""
    token = text.strip()
    sign = -1.0 if token.startswith("-") else 1.0
    parts = token.lstrip("+-").split(":")
    if len(parts) != 3:
        raise ValueError(f"not sexagesimal: {text!r}")
    whole, minutes, seconds = (float(p) for p in parts)
    if not (0 <= minutes < 60 and 0 <= seconds < 60):
        raise ValueError(f"minutes or seconds out of range: {text!r}")
    value = sign * (whole + minutes / 60.0 + seconds / 3600.0) * (15.0 if hours else 1.0)
    return round(value, 6)


def optional_float(text: str) -> float | None:
    token = text.strip()
    return float(token) if token else None


def list_items(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def row_tuple(r: dict[str, str]) -> list[Any]:
    positioned = bool(r["RA"].strip())
    messier = r["M"].strip()
    return [
        r["Name"],
        r["Type"],
        sexagesimal_deg(r["RA"], hours=True) if positioned else None,
        sexagesimal_deg(r["Dec"], hours=False) if r["Dec"].strip() else None,
        r["Const"].strip() or None,
        optional_float(r["MajAx"]),
        optional_float(r["MinAx"]),
        optional_float(r["PosAng"]),
        optional_float(r["B-Mag"]),
        optional_float(r["V-Mag"]),
        optional_float(r["SurfBr"]),
        int(messier, 10) if messier else None,
        list_items(r["NGC"]),
        list_items(r["IC"]),
        list_items(r["Identifiers"]),
        list_items(r["Common names"]),
    ]


def parse_openngc_csv(text: str, expected_rows: int | None = None) -> list[list[Any]]:
    header = text.split("\n", 1)[0].rstrip("\r")
    if header != OPENNGC_HEADER:
        raise ValueError("the OpenNGC header differs from v20260501's")
    rows = [row_tuple(r) for r in csv.DictReader(io.StringIO(text, newline=""), delimiter=";")]
    if expected_rows is not None and len(rows) != expected_rows:
        raise ValueError(f"expected {expected_rows} rows, found {len(rows)}")
    return rows


def build_catalog(cache: Path) -> DeepSkyCatalog:
    rows: list[list[Any]] = []
    for item in INPUTS:
        rows += parse_openngc_csv((cache / item.filename).read_text(encoding="utf-8"), item.rows)
    return DeepSkyCatalog.model_validate(
        {
            "format": DEEP_SKY_CATALOG_FORMAT,
            "formatVersion": DEEP_SKY_CATALOG_FORMAT_VERSION,
            "evidenceKind": DEEP_SKY_EVIDENCE_KIND,
            "release": RELEASE,
            "types": dict(TYPE_LABELS),
            "columns": list(DEEP_SKY_COLUMNS),
            "rows": rows,
        }
    )


def dumps_catalog(catalog: DeepSkyCatalog) -> str:
    """Single-line compact JSON with a final newline (one line, so the trip guard never pairs values across rows)."""
    return json.dumps(catalog.to_contract_dict(), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


LICENSE = {
    "spdx": "CC-BY-SA-4.0",
    "name": "Creative Commons Attribution-ShareAlike 4.0 International",
    "url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "conditions": (
        f"{COPYRIGHT} (.reuse/dep5 at tag v20260501). Credit OpenNGC and Mattia Verga, link the licence, and say that the data were changed. An adaptation, such "
        "as openngc.json, must be shared under CC-BY-SA-4.0 or a compatible licence (OpenNGC .reuse/dep5 at tag "
        "v20260501: 'Files: * License: CC-BY-SA-4.0')."
    ),
}


def build_provenance_record(out_dir: Path) -> AssetProvenanceRecord:
    out_sha = hashlib.sha256((out_dir / OUT_NAME).read_bytes()).hexdigest()
    sources = [
        {
            "id": item.source_id,
            "title": f"OpenNGC {item.upstream}",
            "url": f"{RAW}/{item.upstream}",
            "version": f"{RELEASE} (git tag v20260501, published 2026-05-01)",
            "retrieved": RETRIEVED,
            "license": LICENSE,
            "credit": CREDIT,
            "citation": CITATION,
            "doi": DOI,
            "acknowledgement": ACKNOWLEDGEMENT,
            "sha256": item.sha256,
            "note": (
                f"{item.rows:,} rows, {item.bytes:,} bytes, fetched from {RAW}/{item.upstream} at "
                f"2026-11-09T04:09:06Z; byte-identical to the copy fetched earlier into .cache/m2. Positions are "
                f"J2000. Repository: {REPO}."
            ),
        }
        for item in INPUTS
    ]
    return AssetProvenanceRecord.model_validate(
        {
            "format": "albedian.asset-provenance",
            "formatVersion": 1,
            "sources": sources,
            "files": [
                {
                    "path": OUT_NAME,
                    "sha256": out_sha,
                    "sources": [item.source_id for item in INPUTS],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_deepsky_openngc.py",
                        "command": "uv run python scripts/bake_deepsky_openngc.py",
                    },
                    "note": (
                        "All 14,033 rows of OpenNGC v20260501 (13,969 NGC and IC, 64 addendum) in 16 columns, "
                        "evidenceKind 'catalog-derived'. Changed from the source: positions converted from "
                        "sexagesimal to degrees, list cells split at commas, and the columns not listed left out. "
                        "Offered under CC-BY-SA-4.0, as OpenNGC is."
                    ),
                }
            ],
            "fileSets": [],
        }
    )


def check_inputs(cache: Path) -> None:
    for item in INPUTS:
        path = cache / item.filename
        if not path.is_file():
            raise SystemExit(f"[deepsky] missing {path}; run: uv run python scripts/fetch_download.py .cache/m2/{item.filename}")
        raw = path.read_bytes()
        if len(raw) != item.bytes:
            raise SystemExit(f"[deepsky] {path.name} has {len(raw)} bytes, expected {item.bytes}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item.sha256:
            raise SystemExit(f"[deepsky] {path.name} has sha256 {digest}, expected {item.sha256}")


def verify_outputs(cache: Path, out_dir: Path) -> list[str]:
    check_inputs(cache)
    errors: list[str] = []
    target = out_dir / OUT_NAME
    if not target.is_file() or target.read_text(encoding="utf-8") != dumps_catalog(build_catalog(cache)):
        errors.append(f"{target} differs from a fresh bake")
    if not errors:
        record_path = out_dir / "PROVENANCE.json"
        if not record_path.is_file() or record_path.read_text(encoding="utf-8") != dumps_record(build_provenance_record(out_dir)):
            errors.append(f"{record_path} differs from a fresh bake")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true", help="check committed outputs byte-for-byte against a fresh bake")
    args = parser.parse_args(argv)
    if args.verify:
        errors = verify_outputs(args.cache, args.out)
        for err in errors:
            print(f"[deepsky] {err}", file=sys.stderr)
        if not errors:
            print("[deepsky] openngc.json and PROVENANCE.json match a fresh bake")
        return 1 if errors else 0
    check_inputs(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / OUT_NAME).write_text(dumps_catalog(build_catalog(args.cache)), encoding="utf-8")
    (args.out / "PROVENANCE.json").write_text(dumps_record(build_provenance_record(args.out)), encoding="utf-8")
    for name in (OUT_NAME, "PROVENANCE.json"):
        print(f"[deepsky] {name}: {(args.out / name).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
