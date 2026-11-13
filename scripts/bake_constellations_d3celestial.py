#!/usr/bin/env python3
"""Bake constellation lines and boundaries from d3-celestial (data-7b).

Inputs, in `.cache/d3celestial/` (registered in `data/downloads.json`) and pinned by size and SHA-256, both from
d3-celestial (github.com/ofrohn/d3-celestial) at commit d2e20e104b86429d90ac8227a5b021262b45d75a (2020-03-20, the
last change to either file), BSD-3-Clause, Copyright (c) 2015, Olaf Frohn:
- `constellations.lines.json`: the constellation figures, 27,136 bytes. The d3-celestial readme gives their source as
  the IAU constellation pages with "name positions and some line modifications" by its author: the IAU figures with
  one person's edits, not an IAU product;
- `constellations.bounds.json`: the constellation boundaries, 40,714 bytes, from Davenhall and Leggett (1989),
  Catalogue of Constellation Boundary Data (VizieR VI/49), the boundaries Delporte drew for the IAU at equinox B1875.

Outputs in `apps/web/public/catalog/constellations/`, validated by `services/contracts/constellations.py` and
`services/contracts/provenance.py`:
- `lines.json`: `ConstellationLines` (`evidenceKind: "catalog-derived"`), all 89 figures;
- `bounds.json`: `ConstellationBoundaries`, all 89 boundary rings;
- `LICENSE`: the BSD-3-Clause notice, which must travel with the data, served beside it;
- `PROVENANCE.json`: `AssetProvenanceRecord` pinning the three files and both inputs, with the BSD-3-Clause notice.

Changed from the source, and nothing else: right ascension, which d3-celestial writes as a longitude from -180 to
180 degrees, is wrapped into 0 to 360 (a negative value gains 360 and is rounded back to the source's 4 decimals);
the rank, a string there, is an integer; and the two features d3-celestial names "Ser" become Se1 (Serpens Caput,
west of Ophiuchus, every right ascension below 250 degrees) and Se2 (Serpens Cauda, every one above), the codes
OpenNGC uses. Points, their order, and the order of the constellations are the source's.

The boundary vertices are J2000 positions of corners defined at equinox B1875, and each edge runs along a constant
right ascension or declination of B1875 (precessing the vertices back to B1875 puts 99.6% of them within 1 arcsec of
a whole arcminute of declination and within 0.1 s of a whole second of right ascension). A layer that draws or tests
an edge must densify it along B1875 right ascension or declination, as the contract says.

Usage, from the repository root:
    uv run python scripts/bake_constellations_d3celestial.py
    uv run python scripts/bake_constellations_d3celestial.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.contracts.constellations import (  # noqa: E402
    CONSTELLATION_BOUNDARIES_FORMAT,
    CONSTELLATION_BOUNDARY_EDGE_EQUINOX,
    CONSTELLATION_BOUNDARY_VERTEX_EQUINOX,
    CONSTELLATION_EVIDENCE_KIND,
    CONSTELLATION_FORMAT_VERSION,
    CONSTELLATION_LINES_EQUINOX,
    CONSTELLATION_LINES_FORMAT,
    ConstellationBoundaries,
    ConstellationLines,
)
from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402

DEFAULT_CACHE = ROOT_DIR / ".cache" / "d3celestial"
DEFAULT_OUT = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "constellations"
COMMIT = "d2e20e104b86429d90ac8227a5b021262b45d75a"
RELEASE = f"d3-celestial {COMMIT}"
REPO = "https://github.com/ofrohn/d3-celestial"
RAW = f"https://raw.githubusercontent.com/ofrohn/d3-celestial/{COMMIT}"
RETRIEVED = "2026-11-09"
CREDIT = "d3-celestial, Olaf Frohn (github.com/ofrohn/d3-celestial)"
SERPENS_SPLIT_RA_DEG = 250.0

# The LICENSE file of d3-celestial at the pinned commit, word for word (read through the GitHub contents API)
BSD_NOTICE = (
    "Copyright (c) 2015, Olaf Frohn\n"
    "All rights reserved.\n\n"
    "Redistribution and use in source and binary forms, with or without modification, are permitted provided that "
    "the following conditions are met:\n\n"
    "1. Redistributions of source code must retain the above copyright notice, this list of conditions and the "
    "following disclaimer.\n\n"
    "2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the "
    "following disclaimer in the documentation and/or other materials provided with the distribution.\n\n"
    "3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote "
    "products derived from this software without specific prior written permission.\n\n"
    'THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED '
    "WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A "
    "PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY "
    "DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, "
    "PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER "
    "CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR "
    "OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH "
    "DAMAGE."
)
# The notice as a file beside the data, served with the app at /catalog/constellations/LICENSE
NOTICE_NAME = "LICENSE"
NOTICE_TEXT = BSD_NOTICE + "\n"


@dataclass(frozen=True)
class D3Input:
    source_id: str
    filename: str
    out_name: str
    title: str
    bytes: int
    sha256: str


LINES = D3Input(
    source_id="d3-celestial-constellation-lines",
    filename="constellations.lines.json",
    out_name="lines.json",
    title="d3-celestial data/constellations.lines.json",
    bytes=27_136,
    sha256="294f66bef5d5cf50b1e17f16d2efa1d97a15131612c68dd935adef6e7373e13c",
)
BOUNDS = D3Input(
    source_id="d3-celestial-constellation-bounds",
    filename="constellations.bounds.json",
    out_name="bounds.json",
    title="d3-celestial data/constellations.bounds.json",
    bytes=40_714,
    sha256="f2e2687af6b20b24567879f838c21874d412efcc93ecc1966be07e78431cc196",
)
INPUTS = (LINES, BOUNDS)


def ra_deg(longitude: float) -> float:
    """d3-celestial's longitude (-180 to 180 degrees) as right ascension in [0, 360), at the source's 4 decimals."""
    if not -180 <= longitude < 180:
        raise ValueError(f"longitude outside -180 to 180: {longitude!r}")
    return longitude if longitude >= 0 else round(longitude + 360, 4)


def point(coordinate: list[float]) -> list[float]:
    if len(coordinate) != 2:
        raise ValueError(f"not a [longitude, latitude] pair: {coordinate!r}")
    return [ra_deg(coordinate[0]), coordinate[1]]


def feature_parts(feature: dict[str, Any], geometry_type: str) -> list[list[list[float]]]:
    geometry = feature["geometry"]
    if geometry["type"] != geometry_type:
        raise ValueError(f"{feature['id']}: geometry {geometry['type']}, expected {geometry_type}")
    return [[point(c) for c in part] for part in geometry["coordinates"]]


def constellation_code(feature_id: str, parts: list[list[list[float]]]) -> str:
    """The feature's IAU code, with the two Serpens parts told apart by right ascension."""
    if feature_id != "Ser":
        return feature_id
    ras = [p[0] for part in parts for p in part]
    if max(ras) < SERPENS_SPLIT_RA_DEG:
        return "Se1"
    if min(ras) > SERPENS_SPLIT_RA_DEG:
        return "Se2"
    raise ValueError("a Serpens feature straddles the split between Caput and Cauda")


def parse_features(text: str, geometry_type: str) -> list[dict[str, Any]]:
    collection = json.loads(text)
    if collection.get("type") != "FeatureCollection" or set(collection) != {"type", "features"}:
        raise ValueError("not a bare GeoJSON FeatureCollection")
    entries = []
    for feature in collection["features"]:
        if set(feature["properties"]) != {"rank"}:
            raise ValueError(f"{feature['id']}: properties other than rank")
        parts = feature_parts(feature, geometry_type)
        entries.append({"code": constellation_code(feature["id"], parts), "rank": int(feature["properties"]["rank"], 10), "parts": parts})
    return entries


def build_lines(cache: Path) -> ConstellationLines:
    entries = parse_features((cache / LINES.filename).read_text(encoding="utf-8"), "MultiLineString")
    return ConstellationLines.model_validate(
        {
            "format": CONSTELLATION_LINES_FORMAT,
            "formatVersion": CONSTELLATION_FORMAT_VERSION,
            "evidenceKind": CONSTELLATION_EVIDENCE_KIND,
            "release": RELEASE,
            "equinox": CONSTELLATION_LINES_EQUINOX,
            "constellations": [{"code": e["code"], "rank": e["rank"], "polylines": e["parts"]} for e in entries],
        }
    )


def build_bounds(cache: Path) -> ConstellationBoundaries:
    entries = parse_features((cache / BOUNDS.filename).read_text(encoding="utf-8"), "Polygon")
    for e in entries:
        if len(e["parts"]) != 1:
            raise ValueError(f"{e['code']}: a boundary with {len(e['parts'])} rings")
    return ConstellationBoundaries.model_validate(
        {
            "format": CONSTELLATION_BOUNDARIES_FORMAT,
            "formatVersion": CONSTELLATION_FORMAT_VERSION,
            "evidenceKind": CONSTELLATION_EVIDENCE_KIND,
            "release": RELEASE,
            "vertexEquinox": CONSTELLATION_BOUNDARY_VERTEX_EQUINOX,
            "edgeEquinox": CONSTELLATION_BOUNDARY_EDGE_EQUINOX,
            "constellations": [{"code": e["code"], "rank": e["rank"], "ring": e["parts"][0]} for e in entries],
        }
    )


def dumps_document(document: ConstellationLines | ConstellationBoundaries) -> str:
    """Single-line compact JSON with a final newline (one line, so the trip guard never pairs values across rows)."""
    return json.dumps(document.to_contract_dict(), ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"


LICENSE = {
    "spdx": "BSD-3-Clause",
    "name": "BSD 3-Clause License",
    "url": f"{REPO}/blob/{COMMIT}/LICENSE",
    "conditions": (
        "Keep this notice with the data and in the documentation: it is LICENSE beside this record, served with the "
        "app at /catalog/constellations/LICENSE (docs/science/CONSTELLATIONS.md carries it too): "
        + BSD_NOTICE
    ),
}

SOURCE_NOTES = {
    LINES.source_id: (
        "The IAU constellation figures with name positions and some line modifications by the d3-celestial author "
        "(readme source note 3): one person's edits of the IAU figures, not an IAU product. Vertices are star "
        "positions at equinox J2000. 89 features (Serpens twice), GeoJSON MultiLineString, longitude -180 to 180."
    ),
    BOUNDS.source_id: (
        "Davenhall and Leggett (1989), Catalogue of Constellation Boundary Data, VizieR VI/49 (readme source note "
        "4): the Delporte boundaries the IAU adopted, drawn at equinox B1875. Vertices here are their J2000 "
        "positions; each edge follows a constant B1875 right ascension or declination. 89 closed Polygon rings."
    ),
}


def build_provenance_record(out_dir: Path) -> AssetProvenanceRecord:
    sources = [
        {
            "id": item.source_id,
            "title": item.title,
            "url": f"{RAW}/data/{item.filename}",
            "version": f"git commit {COMMIT} (2020-03-20)",
            "retrieved": RETRIEVED,
            "license": LICENSE,
            "credit": CREDIT,
            "sha256": item.sha256,
            "note": f"{item.bytes:,} bytes. {SOURCE_NOTES[item.source_id]} Repository: {REPO}.",
        }
        for item in INPUTS
    ]
    sources[1]["citation"] = (
        "Davenhall, A. C. and Leggett, S. K. (1989), Catalogue of Constellation Boundary Data, VizieR catalogue VI/49."
    )
    changes = (
        "Changed from the source: right ascension wrapped from -180..180 into 0..360 at 4 decimals, rank as an "
        "integer, the two Serpens features named Se1 (Caput) and Se2 (Cauda) by right ascension; nothing else."
    )
    files = [
        {
            "path": LINES.out_name,
            "sha256": hashlib.sha256((out_dir / LINES.out_name).read_bytes()).hexdigest(),
            "sources": [LINES.source_id],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_constellations_d3celestial.py",
                "command": "uv run python scripts/bake_constellations_d3celestial.py",
            },
            "note": (
                "All 89 constellation figures, evidenceKind 'catalog-derived': the IAU figures as edited by the "
                f"d3-celestial author, not an IAU product. {changes}"
            ),
        },
        {
            "path": BOUNDS.out_name,
            "sha256": hashlib.sha256((out_dir / BOUNDS.out_name).read_bytes()).hexdigest(),
            "sources": [BOUNDS.source_id],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_constellations_d3celestial.py",
                "command": "uv run python scripts/bake_constellations_d3celestial.py",
            },
            "note": (
                "All 89 IAU constellation boundaries, evidenceKind 'catalog-derived'. Vertices are J2000 positions of "
                "B1875 corners; each edge follows a constant B1875 right ascension or declination, so a layer must "
                f"densify edges along B1875 right ascension or declination, never draw them straight. {changes}"
            ),
        },
        {
            "path": NOTICE_NAME,
            "sha256": hashlib.sha256((out_dir / NOTICE_NAME).read_bytes()).hexdigest(),
            "sources": [item.source_id for item in INPUTS],
            "producedBy": {
                "method": "script",
                "script": "scripts/bake_constellations_d3celestial.py",
                "command": "uv run python scripts/bake_constellations_d3celestial.py",
            },
            "note": (
                "d3-celestial's LICENSE at the pinned commit, word for word: the script writes it from its own copy "
                "of the text, read through the GitHub contents API. The notice must travel with lines.json and "
                "bounds.json."
            ),
        },
    ]
    return AssetProvenanceRecord.model_validate(
        {"format": "albedian.asset-provenance", "formatVersion": 1, "sources": sources, "files": files, "fileSets": []}
    )


def check_inputs(cache: Path) -> None:
    for item in INPUTS:
        path = cache / item.filename
        if not path.is_file():
            raise SystemExit(f"[constellations] missing {path}; run: uv run python scripts/fetch_download.py .cache/d3celestial/{item.filename}")
        raw = path.read_bytes()
        if len(raw) != item.bytes:
            raise SystemExit(f"[constellations] {path.name} has {len(raw)} bytes, expected {item.bytes}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item.sha256:
            raise SystemExit(f"[constellations] {path.name} has sha256 {digest}, expected {item.sha256}")


def built_texts(cache: Path) -> dict[str, str]:
    return {LINES.out_name: dumps_document(build_lines(cache)), BOUNDS.out_name: dumps_document(build_bounds(cache))}


def check_notice(out_dir: Path) -> list[str]:
    """The served notice against the bake's own text, byte for byte; needs no cache."""
    target = out_dir / NOTICE_NAME
    if not target.is_file() or target.read_bytes() != NOTICE_TEXT.encode("utf-8"):
        return [f"{target} differs from a fresh bake"]
    return []


def verify_outputs(cache: Path, out_dir: Path) -> list[str]:
    check_inputs(cache)
    errors = []
    for name, text in built_texts(cache).items():
        target = out_dir / name
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            errors.append(f"{target} differs from a fresh bake")
    errors += check_notice(out_dir)
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
            print(f"[constellations] {err}", file=sys.stderr)
        if not errors:
            print("[constellations] lines.json, bounds.json, LICENSE and PROVENANCE.json match a fresh bake")
        return 1 if errors else 0
    check_inputs(args.cache)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in built_texts(args.cache).items():
        (args.out / name).write_text(text, encoding="utf-8")
    (args.out / NOTICE_NAME).write_bytes(NOTICE_TEXT.encode("utf-8"))
    (args.out / "PROVENANCE.json").write_text(dumps_record(build_provenance_record(args.out)), encoding="utf-8")
    for name in (LINES.out_name, BOUNDS.out_name, NOTICE_NAME, "PROVENANCE.json"):
        print(f"[constellations] {name}: {(args.out / name).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
