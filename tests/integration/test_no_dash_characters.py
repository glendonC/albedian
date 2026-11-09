"""No tracked text file holds an em dash, an en dash or another em dash form.

The repository's style bans these characters in code, comments, docs, tests and the preserved research reports alike.
A test that has to name one, to assert that some text is free of it, writes its escape (a backslash, then u and the
code point) instead, which this guard does not read as the character.

Covers:
- Every file git tracks, skipping only links and binary files (a NUL byte in the first 8 KiB, as the trip guard does)
- A failure names each file:line and the code points found there, never the line itself
- Self-test: in a scratch git repository the scan reports exactly the planted text lines, including one in a file with
  a byte that is not UTF-8; an untracked file, a binary file, a link and an escaped form are not reported
- PBT: any banned character placed on any lines of any text is reported at exactly those lines
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]

# Code points, so this file holds none of the characters it bans: the em dash, the en dash, the horizontal bar, the
# two-em and three-em dashes, the small em dash and the vertical em and en dash presentation forms
BANNED = (0x2014, 0x2013, 0x2015, 0x2E3A, 0x2E3B, 0xFE58, 0xFE31, 0xFE32)
EM, EN = chr(0x2014), chr(0x2013)


def dash_points_by_line(text: str) -> list[tuple[int, list[str]]]:
    """Each 1-based line number (lines split on newline, as git grep counts them) with the banned code points on it."""
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        points = sorted({f"U+{ord(ch):04X}" for ch in line if ord(ch) in BANNED})
        if points:
            found.append((number, points))
    return found


def dash_lines(root: Path) -> list[str]:
    """'file:line U+XXXX' for every banned character in the text files git tracks under root."""
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    found = []
    for rel in listed.stdout.decode("utf-8").split("\0"):
        path = root / rel
        if not rel or path.is_symlink() or not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8192]:
            continue
        for number, points in dash_points_by_line(raw.decode("utf-8", errors="replace")):
            found.append(f"{rel}:{number} {' '.join(points)}")
    return found


def test_no_tracked_text_file_holds_an_em_or_en_dash() -> None:
    found = dash_lines(REPO_ROOT)
    # Locations and code points only: the lines themselves are never printed
    assert not found, f"{len(found)} tracked lines hold a dash character; use a colon, a comma or 'to':\n" + "\n".join(
        found[:60]
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_the_scan_reports_exactly_the_planted_text_lines(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "notes.md").write_text(f"a plain line\nan em{EM}dash here\n", encoding="utf-8")
    (tmp_path / "range.ts").write_text(f"// pages 1{EN}7\nconst ok = 1;\n", encoding="utf-8")
    # The escape a test writes to assert absence: a backslash, then u2014, never the character
    (tmp_path / "escaped.ts").write_text(f"expect(text).not.toMatch(/{chr(92)}u2014/);\n", encoding="utf-8")
    (tmp_path / "image.bin").write_bytes(b"\0\1" + (EM + EN).encode("utf-8"))
    # A text file with one byte that is not UTF-8 is still read: the dash on its second line is reported
    (tmp_path / "latin.txt").write_bytes(b"caf\xe9\n" + f"x{EM}y\n".encode("utf-8"))
    (tmp_path / "linked.md").symlink_to(tmp_path / "notes.md")
    (tmp_path / "untracked.md").write_text(EM, encoding="utf-8")
    _git(tmp_path, "add", "notes.md", "range.ts", "escaped.ts", "image.bin", "latin.txt", "linked.md")
    assert dash_lines(tmp_path) == ["latin.txt:2 U+2014", "notes.md:2 U+2014", "range.ts:1 U+2013"]


# Listed apart from BANNED, so dropping a form from BANNED fails here
FORMS = (0x2014, 0x2013, 0x2015, 0x2E3A, 0x2E3B, 0xFE58, 0xFE31, 0xFE32)


@pytest.mark.parametrize("point", FORMS, ids=[f"U+{p:04X}" for p in FORMS])
def test_every_banned_form_is_reported(tmp_path: Path, point: int) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "doc.md").write_text(f"one\ntwo {chr(point)} three\n", encoding="utf-8")
    _git(tmp_path, "add", "doc.md")
    assert dash_lines(tmp_path) == [f"doc.md:2 U+{point:04X}"]


_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=[chr(p) for p in BANNED] + ["\n"]),
    max_size=40,
)


@given(
    lines=st.lists(_SAFE_TEXT, min_size=1, max_size=12),
    marks=st.lists(st.tuples(st.integers(min_value=0), st.sampled_from(BANNED), st.integers(min_value=0)), max_size=8),
)
@example(lines=["a", "b"], marks=[(1, 0x2014, 0)])
@example(lines=["only"], marks=[(0, 0x2013, 99)])
@example(lines=["x", "", "y"], marks=[(1, 0x2015, 0), (1, 0x2014, 0)])
def test_pbt_banned_characters_are_reported_at_exactly_their_lines(
    lines: list[str], marks: list[tuple[int, int, int]]
) -> None:
    expected: dict[int, set[str]] = {}
    for index, point, offset in marks:
        row = index % len(lines)
        at = offset % (len(lines[row]) + 1)
        lines[row] = lines[row][:at] + chr(point) + lines[row][at:]
        expected.setdefault(row + 1, set()).add(f"U+{point:04X}")
    reported = dash_points_by_line("\n".join(lines))
    assert reported == [(number, sorted(points)) for number, points in sorted(expected.items())]
