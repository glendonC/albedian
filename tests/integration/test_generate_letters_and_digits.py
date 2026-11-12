"""scripts/generate_letters_and_digits.py: how it reads the Unicode files and writes the table.

Covers:
- A range of the Unicode data (its First and Last lines) is read whole; only categories L and N count; every
  Default_Ignorable_Code_Point, one code point or a span, is taken out; a comment, a blank line or a third field is
  read as nothing more; the ranges are maximal
- A file whose SHA-256 is not the pinned one stops the script
- data/downloads.json lists each Unicode file the script pins, at .cache/unicode/ucd/<version>/<name>, with the
  script's own SHA-256, its https://www.unicode.org/Public/ URL and the Unicode License v3
- With those files in the shared .cache (`uv run python scripts/fetch_download.py <path>`), the script writes the
  tracked table, its copy, the notice and the record byte for byte; where one is absent the local_dataset marker
  skips the test with its reason, since the repository holds no Unicode data files
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate_letters_and_digits.py"
UCD_CACHE = ".cache/unicode/ucd"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_letters_and_digits", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNICODE_DATA = "\n".join([
    "0020;SPACE;Zs;0;WS;;;;;N;;;;;",
    "002B;PLUS SIGN;Sm;0;ES;;;;;N;;;;;",
    "0030;DIGIT ZERO;Nd;0;EN;;0;0;0;N;;;;;",
    "0041;LATIN CAPITAL LETTER A;Lu;0;L;;;;;N;;;;0061;",
    "0042;LATIN CAPITAL LETTER B;Lu;0;L;;;;;N;;;;0062;",
    "0300;COMBINING GRAVE ACCENT;Mn;230;NSM;;;;;N;;;;;",
    "115F;HANGUL CHOSEONG FILLER;Lo;0;L;;;;;N;;;;;",
    "1160;HANGUL JUNGSEONG FILLER;Lo;0;L;;;;;N;;;;;",
    "3164;HANGUL FILLER;Lo;0;L;<compat> 1160;;;;N;;;;;",
    "3400;<CJK Ideograph Extension A, First>;Lo;0;L;;;;;N;;;;;",
    "3405;<CJK Ideograph Extension A, Last>;Lo;0;L;;;;;N;;;;;",
    "D800;<Non Private Use High Surrogate, First>;Cs;0;L;;;;;N;;;;;",
    "DB7F;<Non Private Use High Surrogate, Last>;Cs;0;L;;;;;N;;;;;",
]) + "\n"
DERIVED = "\n".join([
    "# DerivedCoreProperties-test.txt",
    "",
    "0030          ; Math # Nd       DIGIT ZERO",
    "0041          ; InCB; Linker # a property with a third field",
    "115F..1160    ; Default_Ignorable_Code_Point # Lo   [2] HANGUL CHOSEONG FILLER..HANGUL JUNGSEONG FILLER",
    "3164          ; Default_Ignorable_Code_Point # Lo       HANGUL FILLER",
]) + "\n"


def _write_ucd(folder: Path, version: str) -> dict[tuple[str, str], str]:
    pins = {}
    for name, body in (("UnicodeData.txt", UNICODE_DATA), ("DerivedCoreProperties.txt", DERIVED)):
        path = folder / version / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        pins[(version, name)] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return pins


def test_a_range_is_read_whole_only_letters_and_digits_count_and_ignorables_go(tmp_path: Path) -> None:
    script = _script()
    script.INPUT_SHA256 = _write_ucd(tmp_path, "9.9.9")
    found = script.letters_and_digits(tmp_path, "9.9.9")
    assert found == {0x30, 0x41, 0x42, *range(0x3400, 0x3406)}
    assert script.ranges(found) == [[0x30, 0x30], [0x41, 0x42], [0x3400, 0x3405]]


def test_ranges_are_maximal_and_sorted() -> None:
    ranges = _script().ranges
    assert ranges({7, 1, 2, 3, 5, 8}) == [[1, 3], [5, 5], [7, 8]]
    assert ranges(set()) == []
    assert ranges({0x10FFFF}) == [[0x10FFFF, 0x10FFFF]]


def test_a_file_that_is_not_the_pinned_one_stops_the_script(tmp_path: Path) -> None:
    script = _script()
    pins = _write_ucd(tmp_path, "9.9.9")
    script.INPUT_SHA256 = {**pins, ("9.9.9", "UnicodeData.txt"): "0" * 64}
    with pytest.raises(SystemExit) as stopped:
        script.letters_and_digits(tmp_path, "9.9.9")
    assert "is not the pinned" in str(stopped.value)


UCD_FILES = sorted(f"{UCD_CACHE}/{version}/{name}" for version, name in _script().INPUT_SHA256)


def test_the_registry_lists_every_unicode_file_the_script_pins() -> None:
    by_path = {entry["path"]: entry for entry in json.loads((ROOT / "data" / "downloads.json").read_text())["downloads"]}
    listed = {path for path in by_path if path.startswith(f"{UCD_CACHE}/")}
    assert listed == set(UCD_FILES)
    for (version, name), sha256 in _script().INPUT_SHA256.items():
        entry = by_path[f"{UCD_CACHE}/{version}/{name}"]
        assert entry["sha256"] == sha256, (version, name)
        assert entry["url"] == f"https://www.unicode.org/Public/{version}/ucd/{name}", (version, name)
        assert entry["fetch"] == "scripts/fetch_download.py", (version, name)
        assert (entry["licence"], entry["licenceUrl"]) == ("LicenseRef-Unicode-3.0", "https://www.unicode.org/license.txt")


@pytest.mark.local_dataset(*UCD_FILES)
def test_the_script_writes_the_tracked_files_byte_for_byte(tmp_path: Path) -> None:
    script = _script()
    script.OUT = tmp_path / "data"
    script.SHIPPED = tmp_path / "shipped"
    assert script.main([str(ROOT / UCD_CACHE)]) == 0
    tracked = {
        script.OUT / "letters-and-digits.json": ROOT / "packages/contracts/src/data/letters-and-digits.json",
        script.SHIPPED / "letters-and-digits.json": ROOT / "apps/web/public/notices/unicode/letters-and-digits.json",
        script.SHIPPED / "UNICODE-LICENSE.txt": ROOT / "apps/web/public/notices/unicode/UNICODE-LICENSE.txt",
        script.SHIPPED / "PROVENANCE.json": ROOT / "apps/web/public/notices/unicode/PROVENANCE.json",
    }
    for written, kept in tracked.items():
        assert written.read_bytes() == kept.read_bytes(), kept.name
