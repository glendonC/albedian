"""Git in a folder of a test's or a script's own never acts on another repository, whatever the environment holds.

With GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or GIT_COMMON_DIR set, `git -C <folder>` and `git init <folder>` act on
the repository those name; on this laptop that once wrote a test's scratch work into the shared repository's own
configuration. scripts/git_env.py (Python) and scripts/git-env.mjs (Node) give git its environment without them.

Covers:
- The helper drops exactly the four variables, keeps every other, and leaves this process's own environment as it
  found it
- With all four pointing at a decoy repository, the tests that build repositories of their own (the dash scan's
  and the provenance check's, which also runs scripts/verify_provenance.py there) run in a pytest of their own and
  pass, and the decoy's HEAD, index, status and configuration file are byte for byte as before
- Every text that runs git in every tracked Python file, wherever it lives, sits in a call that passes the helper's
  environment, or is excused by name in tests/fixtures/git_calls.json with its reason. The scan reads each file's
  syntax tree, so the quotes, a tuple, an f-string, a shell line or a wrapper make no difference. The JavaScript and
  TypeScript files have the same scan in scripts/__tests__/git-env.test.mjs, from the same fixture
- Every tracked file the system can run by itself (a #! line or the executable bit) is of a kind one of the two
  scans reads, or holds no text that runs git anywhere in its raw text, comments included, by the same rule
Every child process here has its own bound.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.git_env import GIT_REDIRECTS, git_environment

ROOT = Path(__file__).resolve().parents[2]
CHILD_BOUND_SEC = 300
# The tests that write a repository of their own, run under the decoy
WRITERS = [
    "tests/integration/test_no_dash_characters.py::test_the_scan_reports_exactly_the_planted_text_lines",
    "tests/integration/test_asset_provenance.py",
]
CALLS = json.loads((ROOT / "tests" / "fixtures" / "git_calls.json").read_text(encoding="utf-8"))
# A text runs git when it is the word git alone or a path that ends in /git, or when it holds git followed by an
# option or one of git's own commands (ending where no letter, digit or hyphen follows), at its start or after a
# space, a shell separator, a quote or a path's slash; NUL stands for an f-string's run-time part
# (the classes are written out, not \s and \w, so the Node scan's twin of this pattern reads every text the same)
GIT_COMMAND = re.compile(
    r"^(?:[^ \t\n\r\f\v]*/)?git\Z|(?:^|[ \t\n\r\f\v;&|(`'\"/])git[ \t\n\r\f\v]+(?:-|\x00|(?:"
    + "|".join(map(re.escape, CALLS["commands"]))
    + r")(?![A-Za-z0-9_-]))"
)
HELPER = "git_environment()"


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        capture_output=True, text=True, timeout=CHILD_BOUND_SEC, check=True, env=git_environment(),
    )
    return done.stdout


def snapshot(repo: Path) -> tuple[bytes, bytes, bytes, str, str]:
    dot = repo / ".git"
    return ((dot / "HEAD").read_bytes(), (dot / "config").read_bytes(), (dot / "index").read_bytes(),
            git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"))


def decoy_repository(folder: Path) -> Path:
    folder.mkdir()
    git(folder, "init", "-q")
    (folder / "decoy.txt").write_text("the decoy's own file\n", encoding="utf-8")
    git(folder, "add", "decoy.txt")
    git(folder, "commit", "-q", "-m", "decoy")
    return folder


def test_the_helper_drops_exactly_the_four_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    assert GIT_REDIRECTS == ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
    for name in GIT_REDIRECTS:
        monkeypatch.setenv(name, "/nowhere")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("ALBEDIAN_KEPT", "kept")
    env = git_environment()
    assert not set(GIT_REDIRECTS) & set(env), "the helper's environment holds one of the four"
    assert env.get("GIT_CONFIG_NOSYSTEM") == "1", "another of git's variables was dropped"
    assert env.get("ALBEDIAN_KEPT") == "kept", "a variable that is not git's was dropped"
    assert {key: value for key, value in os.environ.items() if key not in GIT_REDIRECTS} == env


def test_the_helper_leaves_this_processs_own_environment_as_it_found_it(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GIT_REDIRECTS:
        monkeypatch.setenv(name, f"/nowhere/{name}")
    before = dict(os.environ)
    env = git_environment()
    assert dict(os.environ) == before, "the helper changed this process's own environment"
    assert env is not os.environ and type(env) is dict, "the helper handed out the process's own environment"
    env["ALBEDIAN_WRITTEN"] = "x"
    assert "ALBEDIAN_WRITTEN" not in os.environ, "a change to the helper's environment reached the process's"


def test_the_tests_that_build_repositories_leave_a_decoy_named_by_all_four_variables_untouched(tmp_path: Path) -> None:
    decoy = decoy_repository(tmp_path / "decoy")
    before = snapshot(decoy)
    env = {**git_environment(), "GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy),
           "GIT_INDEX_FILE": str(decoy / ".git" / "index"), "GIT_COMMON_DIR": str(decoy / ".git")}
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *WRITERS],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=CHILD_BOUND_SEC, stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, run.stdout[-3000:]
    assert snapshot(decoy) == before, "the decoy repository was written to"


# The scan of the Python files


def tracked() -> list[str]:
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True,
                            timeout=CHILD_BOUND_SEC, env=git_environment())
    return sorted(name for name in listed.stdout.decode("utf-8").split("\0") if name)


def literal_text(node: ast.AST) -> str | None:
    """A string literal's text, an f-string's run-time parts read as NUL; None for any other node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
        return node.value.decode("utf-8", "replace")
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "\0" for part in node.values)
    return None


def passes_helper(call: ast.Call) -> bool:
    return any(keyword.arg == "env" and HELPER in ast.unparse(keyword.value) for keyword in call.keywords)


def last_name(func: ast.expr) -> str:
    """The name a call is known by: `run` for subprocess.run, `print` for print."""
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ast.unparse(func)


def git_texts(source: str, name: str = "<text>") -> list[dict]:
    """Each text in a Python source that runs git: its line, its text, the innermost call around it (None for none)
    and whether a call around it passes the helper's environment. A piece of an f-string is read with its whole, and
    a string that is a statement of its own (a docstring) runs nothing."""
    tree = ast.parse(source, filename=name)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        text = literal_text(node)
        if text is None or not GIT_COMMAND.search(text):
            continue
        parent = parents.get(node)
        if isinstance(parent, (ast.JoinedStr, ast.Expr)):
            continue
        calls = []
        while parent is not None:
            if isinstance(parent, ast.Call):
                calls.append(parent)
            parent = parents.get(parent)
        found.append({"line": node.lineno, "text": text, "call": last_name(calls[0].func) if calls else None,
                      "passes": any(passes_helper(call) for call in calls)})
    return found


def scan(names: list[str], read=lambda name: (ROOT / name).read_text(encoding="utf-8"),
         excuses: list[dict] | None = None) -> tuple[list[str], int, set]:
    """The texts that run git without the helper and are not excused, how many texts run git, and the excuses used.
    An excuse names a file, the last name of the innermost call (None: outside any call) and a text (None: every
    text in that call in that file)."""
    excused = {(entry["file"], entry["call"], entry["text"]) for entry in (CALLS["not_run"] if excuses is None else excuses)}
    missing, count, used = [], 0, set()
    for name in names:
        for found in git_texts(read(name), name):
            count += 1
            key = next((key for key in ((name, found["call"], found["text"]), (name, found["call"], None))
                        if key in excused), None)
            if key is not None:
                used.add(key)
            elif not found["passes"]:
                missing.append(f"{name}:{found['line']} {found['call'] or 'outside any call'}: {found['text']!r}")
    return missing, count, used


def python_files() -> list[str]:
    return [name for name in tracked() if Path(name).suffix in CALLS["kinds"]["python"]]


def stale_excuses(used: set) -> list[dict]:
    """The excuses for Python files that no text in them used: each must go once its text is gone."""
    return [entry for entry in CALLS["not_run"] if Path(entry["file"]).suffix in CALLS["kinds"]["python"]
            and (entry["file"], entry["call"], entry["text"]) not in used]


def test_every_text_that_runs_git_in_a_tracked_python_file_passes_the_helpers_environment() -> None:
    names = python_files()
    assert len(names) > 150, f"the scan read only {len(names)} Python files"
    # Every tracked Python file, wherever it lives, by git's own pattern; some live outside tests, scripts and services
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.py"], capture_output=True, check=True,
                            timeout=CHILD_BOUND_SEC, env=git_environment()).stdout.decode("utf-8").split("\0")
    assert set(names) == {name for name in listed if name}, "the scan does not read every tracked Python file"
    assert any(not name.startswith(("tests/", "scripts/", "services/")) for name in names), "none outside three folders"
    missing, count, used = scan(names)
    assert not missing, "texts that run git with no call passing the helper's environment:\n" + "\n".join(missing)
    assert count >= 10, f"the scan found only {count} texts that run git; it no longer sees them"
    assert not stale_excuses(used), f"excused texts that are no longer there: {stale_excuses(used)}"


def test_an_excuse_no_text_uses_is_reported_and_a_used_one_is_not() -> None:
    python = [entry for entry in CALLS["not_run"] if Path(entry["file"]).suffix in CALLS["kinds"]["python"]]
    assert len(python) >= 2
    assert stale_excuses(set()) == python
    first = python[0]
    assert stale_excuses({(first["file"], first["call"], first["text"])}) == python[1:]


@pytest.mark.parametrize("group,runs", [("runs", True), ("names", False)])
def test_the_rule_tells_a_git_command_line_from_a_sentence_that_names_git(group: str, runs: bool) -> None:
    assert CALLS[group], group
    wrong = [text for text in CALLS[group] if bool(GIT_COMMAND.search(text)) is not runs]
    assert not wrong, f"{group} the rule reads the other way: {wrong}"


# Each form a git call takes in Python, with the helper and without it (the fixture's python_forms, so no line of
# this file is itself such a call): the scan finds each, and passes it only with the helper named at the call.
@pytest.mark.parametrize("form", sorted(CALLS["python_forms"]))
@pytest.mark.parametrize("quote", ["'", '"'])
def test_the_python_scan_finds_each_form_of_git_call_and_passes_it_only_with_the_helper(form: str, quote: str) -> None:
    template = CALLS["python_forms"][form]
    without = template.format(q=quote, env="")
    assert [found["passes"] for found in git_texts(without)] == [False], without
    with_helper = template.format(q=quote, env=", env=" + HELPER)
    assert [found["passes"] for found in git_texts(with_helper)] == [True], with_helper
    elsewhere = "env = {}\n" + template.format(q=quote, env=", env=env")
    assert [found["passes"] for found in git_texts(elsewhere)] == [False], "the helper must be named at the call"


@pytest.mark.parametrize("case", CALLS["python_cases"], ids=lambda case: case["what"])
def test_the_python_scan_reads_each_case_as_the_fixture_says(case: dict) -> None:
    assert git_texts(case["source"]) == case["found"]


def test_an_excused_text_is_excused_only_in_its_own_file_and_its_own_call() -> None:
    entry = next(entry for entry in CALLS["not_run"] if Path(entry["file"]).suffix in CALLS["kinds"]["python"])
    source = (ROOT / entry["file"]).read_text(encoding="utf-8")
    assert scan([entry["file"]]) == ([], 1, {(entry["file"], entry["call"], entry["text"])})
    missing, _, used = scan(["elsewhere.py"], read=lambda name: source)
    assert missing and not used, "an excuse held in another file"
    # The same text as an f-string in another call (a run-time part where the excused text reads NUL)
    body = entry["text"].replace("{", "{{").replace("}", "}}").replace("\0", "{x}")
    missing, _, used = scan([entry["file"]], read=lambda name: f"subprocess.run(f{body!r}, shell=True)\n")
    assert missing and not used, "an excuse held in another call"


def test_an_excuse_for_every_text_of_a_call_holds_in_that_file_and_that_call_only() -> None:
    case = CALLS["excuse_cases"]["python"]
    excuse = case["excuse"]
    missing, count, used = scan([excuse["file"]], read=lambda name: case["source"], excuses=[excuse])
    assert (count, used) == (3, {(excuse["file"], excuse["call"], None)})
    assert missing == case["missing"], "a text of another call"
    missing, _, used = scan(["elsewhere.py"], read=lambda name: case["source"], excuses=[excuse])
    assert len(missing) == 3 and not used, "an excuse held in another file"


def runnable_unread(files: list[tuple[str, bool, bytes]]) -> tuple[list[str], list[str]]:
    """Of (name, executable bit, first bytes) per file: the files the system can run by themselves (the bit, or a #!
    line), and those of them of a kind neither scan reads."""
    kinds = set(CALLS["kinds"]["python"]) | set(CALLS["kinds"]["node"])
    runnable = [name for name, executable, head in files if executable or head[:3] in (b"#!/", b"#! ")]
    return runnable, [name for name in runnable if Path(name).suffix not in kinds]


def running_git_unread(unread: list[str], read=lambda name: (ROOT / name).read_text(encoding="utf-8", errors="replace")) -> list[str]:
    """Of the runnable files no parser here reads: those whose raw text, comments included, holds a text the rule takes
    for a git command line on any line. Such a file needs a scan of its own; one that runs no git may stay."""
    return [name for name in unread if any(GIT_COMMAND.search(line) for line in read(name).splitlines())]


def test_a_runnable_file_no_parser_reads_is_reported_only_when_it_holds_a_git_command_line() -> None:
    texts = CALLS["runnable_cases"]["texts"]
    assert running_git_unread(sorted(texts), read=texts.__getitem__) == CALLS["runnable_cases"]["running_git"]


def test_a_file_the_system_can_run_is_read_by_a_scan_or_reported() -> None:
    files = [("a.sh", True, b"echo"), ("b", False, b"#!/bin/sh\n"), ("c.rb", False, b"#! /usr/bin/ruby\n"),
             ("d.py", True, b"#!/usr/bin/env python3\n"), ("e.mjs", False, b"#!/usr/bin/env node\n"),
             ("f.sh", False, b"#!sh\n"), ("g.txt", False, b"text"), ("h.bin", False, b"#")]
    assert runnable_unread(files) == (["a.sh", "b", "c.rb", "d.py", "e.mjs"], ["a.sh", "b", "c.rb"])


def test_every_tracked_file_the_system_can_run_by_itself_is_of_a_kind_a_scan_reads() -> None:
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "-z"], capture_output=True, check=True,
                            timeout=CHILD_BOUND_SEC, env=git_environment()).stdout.decode("utf-8")
    executable = {record.split("\t", 1)[1] for record in listed.split("\0") if record.startswith("100755 ")}
    files = []
    for name in tracked():
        path = ROOT / name
        head = b""
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as handle:
                head = handle.read(3)
        files.append((name, name in executable, head))
    runnable, unread = runnable_unread(files)
    assert len(runnable) > 10, f"only {len(runnable)} runnable files found; the check no longer sees them"
    running = running_git_unread(unread)
    assert not running, f"files the system can run, of a kind no scan reads, that hold a git command line: {running}"
