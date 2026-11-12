"""Write the letters-and-digits table both answer contracts read, from one stated Unicode version.

A sentence of an answer must hold a letter or a digit that shows (packages/contracts/src/voice-answer.ts and
services/contracts/voice.py). Which code points count is decided by this table, never by the Unicode data of
whatever runtime parses the record, so two Pythons, or Python and Node, judge every sentence alike.

The table is Unicode 15.1.0's general categories L (letters) and N (numbers), less the Default_Ignorable_Code_Point
property (the Hangul fillers U+115F, U+1160, U+3164 and U+FFA0 are letters by category that never show). 15.1.0 is
the version none of the runtimes this repository tests on ships (Python 3.12 has 15.0.0, 3.14 has 16.0.0, 3.15 and
Node 24 have 17.0.0; 3.13 has 15.1.0), so a parser that fell back to its own tables would judge some code point
differently, and the tests would see it. The table also lists where 15.0.0, 16.0.0 and 17.0.0 differ from it, so
the tests check every such code point on both sides.

Inputs: UnicodeData.txt and DerivedCoreProperties.txt of each version, from https://www.unicode.org/Public/, each
pinned below by its SHA-256. data/downloads.json lists each at .cache/unicode/ucd/<version>/<name> with the same
SHA-256, so `uv run python scripts/fetch_download.py <path>` fetches it into the shared cache. Run, with the files
in <ucd>/<version>/ (for example .cache/unicode/ucd):

    uv run python scripts/generate_letters_and_digits.py <ucd>

Writes packages/contracts/src/data/letters-and-digits.json, the table both contracts read. The web app's bundle
carries its ranges, so the table also ships on the record the web's other shipped data has, which
scripts/verify_provenance.py checks: apps/web/public/notices/unicode/ holds a byte-identical copy of the table,
UNICODE-LICENSE.txt (the Unicode License v3 notice, which must travel with the data) and their PROVENANCE.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "contracts" / "src" / "data"
SHIPPED = ROOT / "apps" / "web" / "public" / "notices" / "unicode"
TABLE_VERSION = "15.1.0"
COMPARED_VERSIONS = ("15.0.0", "16.0.0", "17.0.0")
INPUT_SHA256 = {
    ("15.0.0", "UnicodeData.txt"): "806e9aed65037197f1ec85e12be6e8cd870fc5608b4de0fffd990f689f376a73",
    ("15.0.0", "DerivedCoreProperties.txt"): "d367290bc0867e6b484c68370530bdd1a08b6b32404601b8c7accaf83e05628d",
    ("15.1.0", "UnicodeData.txt"): "2fc713e6a31a87c4850a37fe2caffa4218180fadb5de86b43a143ddb4581fb86",
    ("15.1.0", "DerivedCoreProperties.txt"): "f55d0db69123431a7317868725b1fcbf1eab6b265d756d1bd7f0f6d9f9ee108b",
    ("16.0.0", "UnicodeData.txt"): "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f",
    ("16.0.0", "DerivedCoreProperties.txt"): "39d35161f2954497f69e08bdb9e701493f476a3d30222de20028feda36c1dabd",
    ("17.0.0", "UnicodeData.txt"): "2e1efc1dcb59c575eedf5ccae60f95229f706ee6d031835247d843c11d96470c",
    ("17.0.0", "DerivedCoreProperties.txt"): "24c7fed1195c482faaefd5c1e7eb821c5ee1fb6de07ecdbaa64b56a99da22c08",
}
UNICODE_LICENSE = """UNICODE LICENSE V3

COPYRIGHT AND PERMISSION NOTICE

Copyright © 1991-2026 Unicode, Inc.

NOTICE TO USER: Carefully read the following legal agreement. BY
DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING DATA FILES, AND/OR
SOFTWARE, YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE
TERMS AND CONDITIONS OF THIS AGREEMENT. IF YOU DO NOT AGREE, DO NOT
DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE THE DATA FILES OR SOFTWARE.

Permission is hereby granted, free of charge, to any person obtaining a
copy of data files and any associated documentation (the "Data Files") or
software and any associated documentation (the "Software") to deal in the
Data Files or Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, and/or sell
copies of the Data Files or Software, and to permit persons to whom the
Data Files or Software are furnished to do so, provided that either (a)
this copyright and permission notice appear with all copies of the Data
Files or Software, or (b) this copyright and permission notice appear in
associated Documentation.

THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT OF
THIRD PARTY RIGHTS.

IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS NOTICE
BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL DAMAGES,
OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,
ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THE DATA
FILES OR SOFTWARE.

Except as contained in this notice, the name of a copyright holder shall
not be used in advertising or otherwise to promote the sale, use or other
dealings in these Data Files or Software without prior written
authorization of the copyright holder.
"""


def _read(ucd: Path, version: str, name: str) -> str:
    path = ucd / version / name
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != INPUT_SHA256[(version, name)]:
        sys.exit(f"{path}: sha256 {digest} is not the pinned {INPUT_SHA256[(version, name)]}")
    return data.decode("utf-8")


def letters_and_digits(ucd: Path, version: str) -> set[int]:
    """The code points of categories L and N in `version`, less its Default_Ignorable_Code_Point."""
    found: set[int] = set()
    first: int | None = None
    for line in _read(ucd, version, "UnicodeData.txt").splitlines():
        fields = line.split(";")
        cp, name, category = int(fields[0], 16), fields[1], fields[2]
        if name.endswith(", First>"):
            first = cp
            continue
        span = range(first, cp + 1) if name.endswith(", Last>") and first is not None else range(cp, cp + 1)
        first = None
        if category[0] in "LN":
            found.update(span)
    for line in _read(ucd, version, "DerivedCoreProperties.txt").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        points, prop = (part.strip() for part in body.split(";")[:2])
        if prop != "Default_Ignorable_Code_Point":
            continue
        low, _, high = points.partition("..")
        found.difference_update(range(int(low, 16), int(high or low, 16) + 1))
    return found


def ranges(points: set[int]) -> list[list[int]]:
    """`points` as sorted inclusive [first, last] ranges."""
    out: list[list[int]] = []
    for cp in sorted(points):
        if out and out[-1][1] == cp - 1:
            out[-1][1] = cp
        else:
            out.append([cp, cp])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ucd", type=Path, help="folder holding <version>/UnicodeData.txt and DerivedCoreProperties.txt")
    ucd = parser.parse_args(argv).ucd
    table = letters_and_digits(ucd, TABLE_VERSION)
    table_doc = {
        "format": "albedian.letters-and-digits",
        "formatVersion": 1,
        "unicodeVersion": TABLE_VERSION,
        "rule": "General_Category L or N, less Default_Ignorable_Code_Point",
        "count": len(table),
        "ranges": ranges(table),
        "differsFrom": {v: ranges(table ^ letters_and_digits(ucd, v)) for v in COMPARED_VERSIONS},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    table_text = json.dumps(table_doc, separators=(",", ":")) + "\n"
    (OUT / "letters-and-digits.json").write_text(table_text, encoding="utf-8")
    SHIPPED.mkdir(parents=True, exist_ok=True)
    (SHIPPED / "letters-and-digits.json").write_text(table_text, encoding="utf-8")
    (SHIPPED / "UNICODE-LICENSE.txt").write_text(UNICODE_LICENSE, encoding="utf-8")

    def source(version: str) -> dict:
        return {
            "id": f"unicode-ucd-{version}",
            "title": f"The Unicode Character Database, version {version}",
            "url": f"https://www.unicode.org/Public/{version}/ucd/",
            "version": version,
            "license": {
                "spdx": "LicenseRef-Unicode-3.0",
                "name": "Unicode License v3",
                "url": "https://www.unicode.org/license.txt",
                "conditions": (
                    "Use, copying, changes and redistribution are free, provided the copyright and permission notice "
                    "appears with all copies or in their documentation: it is UNICODE-LICENSE.txt beside this record, "
                    "served with the app at /notices/unicode/UNICODE-LICENSE.txt."
                ),
            },
            "credit": "Unicode, Inc.",
            "note": "UnicodeData.txt (sha256 " + INPUT_SHA256[(version, "UnicodeData.txt")] + ") and "
            "DerivedCoreProperties.txt (sha256 " + INPUT_SHA256[(version, "DerivedCoreProperties.txt")] + ").",
        }

    record = {
        "format": "albedian.asset-provenance",
        "formatVersion": 1,
        "sources": [source(TABLE_VERSION), *(source(v) for v in COMPARED_VERSIONS)],
        "files": [
            {
                "path": "letters-and-digits.json",
                "sha256": hashlib.sha256(table_text.encode("utf-8")).hexdigest(),
                "sources": [f"unicode-ucd-{v}" for v in (TABLE_VERSION, *COMPARED_VERSIONS)],
                "producedBy": {
                    "method": "script",
                    "script": "scripts/generate_letters_and_digits.py",
                    "command": "uv run python scripts/generate_letters_and_digits.py <ucd>",
                },
                "note": f"A byte-identical copy of packages/contracts/src/data/letters-and-digits.json, which both "
                "answer contracts read and the web bundle carries. The ranges are Unicode "
                f"{TABLE_VERSION}'s; differsFrom lists where 15.0.0, 16.0.0 and 17.0.0 differ from them.",
            },
            {
                "path": "UNICODE-LICENSE.txt",
                "sha256": hashlib.sha256(UNICODE_LICENSE.encode("utf-8")).hexdigest(),
                "sources": [f"unicode-ucd-{TABLE_VERSION}"],
                "producedBy": {
                    "method": "script",
                    "script": "scripts/generate_letters_and_digits.py",
                    "command": "uv run python scripts/generate_letters_and_digits.py <ucd>",
                },
                "note": "The script writes the notice from its own copy of the text, which is byte-equal to "
                "https://www.unicode.org/license.txt as fetched on 2026-11-11. It must travel with the data.",
            },
        ],
        "fileSets": [],
    }
    (SHIPPED / "PROVENANCE.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(table)} letters and digits in {len(table_doc['ranges'])} ranges; differs from "
          + ", ".join(f"{v}: {sum(b - a + 1 for a, b in r)}" for v, r in table_doc["differsFrom"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
