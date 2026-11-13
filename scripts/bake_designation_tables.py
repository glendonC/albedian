#!/usr/bin/env python3
"""Bake the tables a Bayer or Flamsteed designation is written from (catalog-2b), as twin modules for both languages.

Nothing in the tables is typed:
- The letters are the 24 three-letter (or two-letter) abbreviations the baked HYG designations use
  (apps/web/public/catalog/stars/designations/designations.json), each with the Greek small letter whose Unicode name
  begins with it (this interpreter's unicodedata, whose version the tables record) and that letter's name as the long
  form writes it. The one stated exception: Unicode spells U+03BB "LAMDA", and the long form writes "Lambda", as every
  designation the repository already ships does (tests/integration/test_designations.py pins both).
- The index runs over the superscript digits the baked data uses, 1 up to its largest, each with the glyph whose
  Unicode name is SUPERSCRIPT <digit name>.
- The constellations are the 88 of d3-celestial's data/constellations.json at the commit the download registry names
  (`.cache/d3celestial/constellations.json`, BSD-3-Clause, Copyright (c) 2015 Olaf Frohn): each one's three-letter
  abbreviation (desig), Latin name (name) and genitive (gen). Serpens is listed twice there, for its two parts
  (Serpens Caput, Serpens Cauda, one genitive), and once here, under the name both parts begin with.

Outputs, each with the same rows in the same order:
- packages/contracts/src/designation-tables.ts
- services/contracts/designation_tables.py

Usage, from the repository root:
    uv run python scripts/bake_designation_tables.py
    uv run python scripts/bake_designation_tables.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
CONSTELLATIONS = ROOT_DIR / ".cache" / "d3celestial" / "constellations.json"
CONSTELLATIONS_URL = (
    "https://raw.githubusercontent.com/ofrohn/d3-celestial/d2e20e104b86429d90ac8227a5b021262b45d75a/data/constellations.json"
)
CONSTELLATIONS_BYTES = 49_332
CONSTELLATIONS_SHA256 = "3649b9d93cc787e3816a06509a7dde895673717478c451f87f00c62c638ab93f"
DESIGNATIONS = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "designations" / "designations.json"
TS_OUT = ROOT_DIR / "packages" / "contracts" / "src" / "designation-tables.ts"
PY_OUT = ROOT_DIR / "services" / "contracts" / "designation_tables.py"
GREEK_SMALL = "GREEK SMALL LETTER "
FINAL_SIGMA = "GREEK SMALL LETTER FINAL SIGMA"
# The one stated exception to the Unicode names: Unicode spells U+03BB "LAMDA"; the long form writes "Lambda", as the
# repository's shipped designations and common usage do
NAME_EXCEPTIONS = {"Lamda": "Lambda"}
DIGIT_NAMES = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")


def check_input(path: Path = CONSTELLATIONS) -> bytes:
    if not path.is_file():
        raise SystemExit(f"[designation-tables] missing {path}; run: uv run python scripts/fetch_download.py .cache/d3celestial/constellations.json")
    raw = path.read_bytes()
    if len(raw) != CONSTELLATIONS_BYTES or hashlib.sha256(raw).hexdigest() != CONSTELLATIONS_SHA256:
        raise SystemExit(f"[designation-tables] {path.name} is not the registered file (size or sha256 differs)")
    return raw


def greek_letters() -> list[tuple[str, str]]:
    """(glyph, name as the long form writes it) for every Greek small letter, alphabet order, final sigma left out."""
    out = []
    for code in range(0x3B1, 0x3CA):
        uname = unicodedata.name(chr(code))
        if uname == FINAL_SIGMA:
            continue
        word = uname.removeprefix(GREEK_SMALL).title()
        out.append((chr(code), NAME_EXCEPTIONS.get(word, word)))
    return out


def letter_rows(abbreviations: set[str]) -> list[list[str]]:
    """[abbreviation, glyph, name] for each abbreviation the data uses, in alphabet order; each abbreviation begins the
    Unicode name of exactly one letter, or the bake stops."""
    rows = []
    begun: dict[str, list[str]] = {a: [] for a in abbreviations}
    for glyph, name in greek_letters():
        uname = unicodedata.name(glyph).removeprefix(GREEK_SMALL).title()
        hits = [a for a in abbreviations if uname.startswith(a)]
        if len(hits) > 1:
            raise ValueError(f"abbreviations {hits} all begin {uname}")
        if hits:
            begun[hits[0]].append(uname)
            rows.append([hits[0], glyph, name])
    for abbreviation, unames in sorted(begun.items()):
        if len(unames) > 1:
            raise ValueError(f"{abbreviation!r} begins the names {', '.join(unames)}")
    found = {row[0] for row in rows}
    if found != abbreviations:
        raise ValueError(f"no Greek letter's Unicode name begins {sorted(abbreviations - found)}")
    return rows


def index_rows(largest: int) -> list[list[str]]:
    """[digit, superscript glyph] for 1 up to the largest index the data uses."""
    if not 1 <= largest <= 9:
        raise ValueError(f"an index of {largest} is not one digit")
    return [[str(d), unicodedata.lookup(f"SUPERSCRIPT {DIGIT_NAMES[d]}")] for d in range(1, largest + 1)]


def constellation_rows(raw: bytes) -> list[list[str]]:
    """[abbreviation, name, genitive] for each constellation once, in the file's order; a part listed twice must agree."""
    rows: dict[str, list[str]] = {}
    for feature in json.loads(raw)["features"]:
        p = feature["properties"]
        row = rows.setdefault(p["desig"], [p["desig"], p["name"], p["gen"]])
        if row[2] != p["gen"]:
            raise ValueError(f"{p['desig']} is listed twice with different genitives")
        if row[1] != p["name"]:
            # Serpens, listed as Serpens Caput and Serpens Cauda: the name both parts begin with, word by word
            words = []
            for a, b in zip(row[1].split(), p["name"].split()):
                if a != b:
                    break
                words.append(a)
            if not words:
                raise ValueError(f"{p['desig']} is listed twice with names that share no word")
            row[1] = " ".join(words)
    if len(rows) != 88:
        raise ValueError(f"{len(rows)} constellations, not 88")
    return list(rows.values())


def build(raw: bytes, designations: dict[str, Any]) -> dict[str, Any]:
    cols = designations["columns"]
    bayer = [r[cols.index("bayer")] for r in designations["rows"] if r[cols.index("bayer")] is not None]
    letters = {b.partition("-")[0] for b in bayer}
    largest = max((int(b.partition("-")[2]) for b in bayer if "-" in b), default=0)
    flamsteed = [r[cols.index("flamsteed")] for r in designations["rows"] if r[cols.index("flamsteed")] is not None]
    return {
        "letters": letter_rows(letters),
        "indices": index_rows(largest),
        "constellations": constellation_rows(raw),
        "flamsteedMax": max(flamsteed),
        "designationsSha256": hashlib.sha256(DESIGNATIONS.read_bytes()).hexdigest() if DESIGNATIONS.is_file() else "",
    }


def _rows_text(rows: list[list[Any]], indent: str) -> str:
    return "\n".join(f"{indent}{json.dumps(row, ensure_ascii=False)}," for row in rows)


HEADER = (
    "Generated by `uv run python scripts/bake_designation_tables.py`; do not edit it by hand, rerun the command.\n"
    "Letters: the abbreviations of apps/web/public/catalog/stars/designations/designations.json (sha256 {sha}), each\n"
    "with the Greek small letter whose Unicode name begins with it and its name, with one stated\n"
    "exception: Unicode spells U+03BB LAMDA, the long form writes Lambda. Indices: the superscript digits 1 to {imax}.\n"
    "Constellations: d3-celestial data/constellations.json at commit d2e20e104b86429d90ac8227a5b021262b45d75a\n"
    "(sha256 {csha}), BSD-3-Clause, Copyright (c) 2015 Olaf Frohn: abbreviation, Latin name, genitive."
)


def ts_text(t: dict[str, Any]) -> str:
    head = HEADER.format(sha=t["designationsSha256"], imax=len(t["indices"]), csha=CONSTELLATIONS_SHA256)
    comment = "\n".join(f"// {line}" for line in head.splitlines())
    return (
        f"{comment}\n\n"
        "/** [abbreviation as HYG writes it, Greek small letter, the letter's name in a long form] */\n"
        "export const DESIGNATION_LETTERS: ReadonlyArray<readonly [string, string, string]> = [\n"
        f"{_rows_text(t['letters'], '  ')}\n];\n\n"
        "/** [index digit, its superscript glyph] */\n"
        "export const DESIGNATION_INDICES: ReadonlyArray<readonly [string, string]> = [\n"
        f"{_rows_text(t['indices'], '  ')}\n];\n\n"
        "/** [three-letter abbreviation, Latin name, genitive] for the 88 constellations */\n"
        "export const DESIGNATION_CONSTELLATIONS: ReadonlyArray<readonly [string, string, string]> = [\n"
        f"{_rows_text(t['constellations'], '  ')}\n];\n\n"
        "/** The largest Flamsteed number in the baked designations */\n"
        f"export const FLAMSTEED_NUMBER_MAX = {t['flamsteedMax']};\n"
    )


def py_text(t: dict[str, Any]) -> str:
    head = HEADER.format(sha=t["designationsSha256"], imax=len(t["indices"]), csha=CONSTELLATIONS_SHA256)
    comment = "\n".join(f"# {line}" for line in head.splitlines())
    return (
        f"{comment}\n\n"
        "# [abbreviation as HYG writes it, Greek small letter, the letter's name in a long form]\n"
        "DESIGNATION_LETTERS: tuple[tuple[str, str, str], ...] = (\n"
        + "\n".join(f"    ({', '.join(json.dumps(v, ensure_ascii=False) for v in row)})," for row in t["letters"])
        + "\n)\n\n"
        "# [index digit, its superscript glyph]\n"
        "DESIGNATION_INDICES: tuple[tuple[str, str], ...] = (\n"
        + "\n".join(f"    ({', '.join(json.dumps(v, ensure_ascii=False) for v in row)})," for row in t["indices"])
        + "\n)\n\n"
        "# [three-letter abbreviation, Latin name, genitive] for the 88 constellations\n"
        "DESIGNATION_CONSTELLATIONS: tuple[tuple[str, str, str], ...] = (\n"
        + "\n".join(f"    ({', '.join(json.dumps(v, ensure_ascii=False) for v in row)})," for row in t["constellations"])
        + "\n)\n\n"
        "# The largest Flamsteed number in the baked designations\n"
        f"FLAMSTEED_NUMBER_MAX = {t['flamsteedMax']}\n"
    )


def outputs() -> dict[Path, str]:
    tables = build(check_input(), json.loads(DESIGNATIONS.read_text(encoding="utf-8")))
    return {TS_OUT: ts_text(tables), PY_OUT: py_text(tables)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="check both modules byte for byte against a fresh bake")
    args = parser.parse_args(argv)
    fresh = outputs()
    if args.verify:
        stale = [str(p.relative_to(ROOT_DIR)) for p, text in fresh.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for path in stale:
            print(f"[designation-tables] {path} differs from a fresh bake", file=sys.stderr)
        if not stale:
            print("[designation-tables] both modules match a fresh bake")
        return 1 if stale else 0
    for path, text in fresh.items():
        path.write_text(text, encoding="utf-8")
        print(f"[designation-tables] {path.relative_to(ROOT_DIR)}: {len(text.encode()):,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
