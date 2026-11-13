"""scripts/ci_linux.py runs CI's Python jobs in a Linux container; these tests check it without an engine.

Covers:
- The workflow is read as CI runs it, and the image and job.bash run the workflow's own steps; a workflow the
  container does not reproduce is refused
- The bound: its edges, its defaults, and a hang that becomes a failure naming the test that was running, with
  the container stopped and removed by its id (a stand-in docker, tests/integration/ci_linux_fake_docker.py)
- The files copied in: the working tree's or a commit's, never an owner folder or .cache
- The refusal to mount a .cache that is, holds or sits inside an owner folder or a checkout
- The watcher plugin names the running test, dumps the stacks on SIGUSR1, and lets SIGINT end pytest
Every child process and wait here has its own bound and fails by an assertion when it passes.
"""

from __future__ import annotations

from collections.abc import Iterator
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import threading
import time
import tempfile
import unicodedata

from hypothesis import example, given, settings, strategies as st
import pytest

from scripts.git_env import git_environment

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_linux.py"
IMAGE_DIR = ROOT / "scripts" / "ci_linux"
FAKE = Path(__file__).resolve().parent / "ci_linux_fake_docker.py"
WORKFLOW_TEXT = (ROOT / ".github" / "workflows" / "gates.yml").read_text(encoding="utf-8")
IGNORE_TEXT = (ROOT / ".gitignore").read_text(encoding="utf-8")
CHILD_BOUND_SEC = 60
POLL_SEC = 0.05


def _load():
    # In sys.modules before it runs: its dataclasses look their module up there
    name = "ci_linux_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ci = _load()


def _owners() -> tuple[str, ...]:
    """The script's owner folders for this repository's .gitignore; empty when it finds none, so the tests below fail
    by their assertions and not at import."""
    try:
        return ci.owner_folders(IGNORE_TEXT)
    except ci.Refused:
        return ()


OWNERS = _owners()
LEFT = (*OWNERS, ci.CACHE_FOLDER)
# The Python job's limit as the workflow states it, read here with a pattern of the test's own, so the tests follow
# the workflow and a fault in the script's own reading of it cannot hide behind them
PYTHON_JOB_LIMIT_SEC = 60 * int(
    re.search(r"^    timeout-minutes: (\d+)$", WORKFLOW_TEXT[WORKFLOW_TEXT.index("\n  python:\n") :], re.MULTILINE).group(1)
)


def git(root: Path, *args: str) -> str:
    """git in a folder of the test's own, never turned on another repository by GIT_DIR and its kind."""
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        capture_output=True,
        text=True,
        timeout=CHILD_BOUND_SEC,
        check=True,
        env=git_environment(),
    )
    return done.stdout


def test_git_variables_from_outside_never_turn_a_run_or_a_test_on_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = make_repo(tmp_path / "decoy")
    before = (git(decoy, "rev-parse", "HEAD"), (decoy / ".git" / "index").read_bytes(), git(decoy, "status", "--porcelain"))
    for name, value in (("GIT_DIR", decoy / ".git"), ("GIT_WORK_TREE", decoy), ("GIT_INDEX_FILE", decoy / ".git" / "index"),
                        ("GIT_COMMON_DIR", decoy / ".git")):
        monkeypatch.setenv(name, str(value))
    assert set(ci.GIT_REDIRECTS) == {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
    repo = make_repo(tmp_path / "repo")  # init, add and commit, all with the four set
    # A blob only this repository's history holds, so reading objects from the decoy cannot pass for it
    (repo / "only.txt").write_text("in this repository's history alone\n", encoding="utf-8")
    git(repo, "add", "only.txt")
    git(repo, "commit", "-q", "-m", "second")
    (repo / "plain.txt").write_text("only in repo\n", encoding="utf-8")
    assert ci.checkout_root(repo) == repo.resolve()
    worktree = entries_by_name(ci.worktree_entries(repo, LEFT))
    assert getattr(worktree.get("plain.txt"), "data", None) == b"only in repo\n", "the working tree's file"
    committed = entries_by_name(ci.rev_entries(repo, "HEAD", LEFT))
    assert getattr(committed.get("plain.txt"), "data", None) == b"committed\n", "the commit's file"
    assert getattr(committed.get("only.txt"), "data", None) == b"in this repository's history alone\n"
    for name in ci.GIT_REDIRECTS:
        monkeypatch.delenv(name)
    after = (git(decoy, "rev-parse", "HEAD"), (decoy / ".git" / "index").read_bytes(), git(decoy, "status", "--porcelain"))
    assert after == before, "the decoy repository was touched"


def wait_for(condition, what: str, limit: float = CHILD_BOUND_SEC) -> None:
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, f"{what} did not happen within {limit} s"
        time.sleep(POLL_SEC)


# The workflow, and the image's match to it


def test_the_workflow_is_read_as_ci_runs_it() -> None:
    workflow = ci.parse_workflow(WORKFLOW_TEXT)
    ci.check_workflow(workflow)
    assert workflow.uv_version == re.search(r'setup-uv@\S+.*\n\s*with:\n\s*version:\s*"([^"]+)"', WORKFLOW_TEXT).group(1)
    assert workflow.timeout_minutes * 60 == PYTHON_JOB_LIMIT_SEC
    assert [(job.name, job.python, job.ffmpeg) for job in workflow.jobs] == [
        ("py312-distro", "3.12", "distro"),
        ("py314-pinned", "3.14", "pinned"),
    ]
    assert f"FFMPEG_URL: {workflow.ffmpeg_url}\n" in WORKFLOW_TEXT
    assert f"FFMPEG_SHA256: {workflow.ffmpeg_sha256}\n" in WORKFLOW_TEXT
    assert workflow.commands == ci.WORKFLOW_COMMANDS


def test_the_image_and_job_bash_run_the_workflows_own_steps() -> None:
    dockerfile = (IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    job = (IMAGE_DIR / "job.bash").read_text(encoding="utf-8")
    distro = dockerfile[dockerfile.index("FROM base AS distro") : dockerfile.index("FROM base AS pinned")]
    pinned = dockerfile[dockerfile.index("FROM base AS pinned") :]
    assert f"RUN {ci.WORKFLOW_COMMANDS[0].replace('sudo ', '')}\n" in distro
    # The pinned build: download, check, unpack and the two binaries, in the workflow's order
    order = ["curl -fsSL --retry 3", "sha256sum --check --strict", "tar -xJf", "test -x /opt/ffmpeg/bin/ffmpeg",
             "test -x /opt/ffmpeg/bin/ffprobe"]
    assert [pinned.index(part) for part in order] == sorted(pinned.index(part) for part in order)
    assert '"$FFMPEG_SHA256  /tmp/ffmpeg.tar.xz"' in pinned
    assert "ENV PATH=/opt/ffmpeg/bin:$PATH" in pinned and "ffmpeg" not in distro.split("RUN", 1)[0]
    # The base image is pinned by digest and the uv archive is checked before it is unpacked
    assert re.search(r"^FROM ubuntu:[a-z0-9.-]+@sha256:[0-9a-f]{64} AS base$", dockerfile, re.MULTILINE)
    assert dockerfile.index('"$UV_SHA256  /tmp/uv.tar.gz" | sha256sum --check --strict') < dockerfile.index("tar -xzf")
    for command in ci.WORKFLOW_COMMANDS[1:4]:
        assert f"\n{command}\n" in job, command
    assert f"uv run --frozen pytest -rs --no-unexpected-skips -p ci_linux_watch \"$@\"\n" in job
    # The two ffprobe checks are the workflow's
    for line in ('ffprobe -hide_banner -h > "$help"', 'grep -q -e -show_stream_groups "$help"',
                 "This ffprobe reads HEIC tile grids, so the refusal path would go untested"):
        assert line.replace('"$help"', '"$RUNNER_TEMP/ffprobe-help.txt"') in WORKFLOW_TEXT
        assert line in job
    # Each branch checks its own ffprobe: the pinned one ends on the grep itself, the distro one refuses a match
    pinned_branch = job[job.index("if [[ $kind == pinned ]]") : job.index("elif [[ $kind == distro ]]")]
    distro_branch = job[job.index("elif [[ $kind == distro ]]") : job.index("\nelse\n")]
    assert pinned_branch.rstrip().endswith('grep -q -e -show_stream_groups "$help"')
    assert 'if grep -q -e -show_stream_groups "$help"; then' in distro_branch and "exit 1" in distro_branch


def test_the_job_runs_as_the_runner_under_bash_e_in_the_workflows_order() -> None:
    dockerfile = (IMAGE_DIR / "Dockerfile").read_text(encoding="utf-8")
    job = (IMAGE_DIR / "job.bash").read_text(encoding="utf-8")
    commands = [line for line in job.splitlines() if line and not line.startswith("#")]
    assert commands[0] == "set -e", "each workflow step runs under bash -e"
    steps = ["\ngit add --force --all\n", "\nffmpeg -hide_banner -version", "\nuv sync --frozen\n",
             "\nuv run --frozen python -VV\n", "\ndpkg-query --show --showformat '${Package} ${Version}\\n' python3.12\n",
             "\nuv run --frozen python scripts/verify_provenance.py\n", '-p ci_linux_watch "$@"\n']
    at = [job.index(part) for part in steps]
    assert at == sorted(at)
    assert f"useradd --create-home --uid {ci.RUNNER_ID} runner" in dockerfile
    for stage in ("FROM base AS distro", "FROM base AS pinned"):
        body = dockerfile[dockerfile.index(stage) :].split("\nFROM ", 1)[0]
        assert "\nUSER runner\n" in body and f"\nWORKDIR {ci.WORKSPACE}\n" in body, stage
    packages = re.search(r"apt-get install --yes --no-install-recommends ([a-z0-9. -]+)\n", dockerfile).group(1).split()
    assert {"git", "python3", "tzdata", "ca-certificates"} <= set(packages)
    assert "ENV LANG=C.UTF-8" in dockerfile
    # apt's DEBIAN_FRONTEND holds while the image is built and is not left set for the tests, in each stage with apt
    assert not [line for line in dockerfile.splitlines() if line.startswith("ENV") and "DEBIAN_FRONTEND" in line]
    for stage in ("AS base", "AS distro"):
        body = dockerfile[dockerfile.index(stage) :].split("\nFROM ", 1)[0]
        assert "\nARG DEBIAN_FRONTEND=noninteractive\n" in body and body.index("ARG DEBIAN_FRONTEND") < body.index(
            "apt-get"), stage
    # The uv cache volume lands on the folder setup-uv's variable names, which the runner owns in the image
    assert f"UV_CACHE_DIR={ci.UV_CACHE_DIR}" in dockerfile and f"mkdir -p {ci.WORKSPACE} {ci.UV_CACHE_DIR}" in dockerfile


def test_the_docstring_states_the_ranges_and_exit_codes_the_script_uses() -> None:
    doc = ci.__doc__
    assert f"{ci.BOUND_MIN_SEC} to {ci.BOUND_MAX_SEC}" in doc and (ci.BOUND_MIN_SEC, ci.BOUND_MAX_SEC) == (1, 21600)
    assert f"{ci.TESTS_BOUND_SEC} seconds when the pytest arguments name tests" in doc
    assert f"{ci.EXIT_BOUND} when the bound passed" in doc and ci.EXIT_BOUND == 124
    assert f"{ci.EXIT_USAGE} for a usage error" in doc and ci.EXIT_USAGE == 2
    assert f"{ci.EXIT_SETUP} when the engine" in doc and ci.EXIT_SETUP == 125
    assert f"colima profile {ci.PROFILE}" in doc
    assert ci.LOCK_PATH == Path.home() / ".colima" / ci.PROFILE / "ci_linux.lock"
    assert f"~/.colima/{ci.PROFILE}/{ci.LOCK_PATH.name}" in " ".join(doc.split())
    assert f"~/.colima/{ci.PROFILE}/{ci.EngineLock(ci.LOCK_PATH).queue.name}/" in " ".join(doc.split())
    assert ci.SHORT_LOCK_PATH == ci.LOCK_PATH.with_name("ci_linux.short.lock")
    assert f"~/.colima/{ci.PROFILE}/{ci.SHORT_LOCK_PATH.name})" in " ".join(doc.split())
    assert f"or {ci.EngineLock(ci.SHORT_LOCK_PATH).queue.name}/" in " ".join(doc.split())
    assert f"every {ci.LOCK_REPORT_SEC} seconds" in " ".join(doc.split()) and ci.LOCK_REPORT_SEC == 60
    assert f"after {ci.LOCK_WAIT_LIMIT_SEC} seconds of waiting" in doc and ci.LOCK_WAIT_LIMIT_SEC == 21600


def without(text: str, pattern: str) -> str:
    changed = re.sub(pattern, "", text, count=1, flags=re.MULTILINE)
    assert changed != text, pattern
    return changed


@pytest.mark.parametrize(
    "pattern,message",
    [
        (r"^  python:\n", "no job named python"),
        (r"^    timeout-minutes: \d+\n(?=    strategy:\n      fail-fast: false\n      matrix:\n        # 3\.12)",
         "no timeout-minutes"),
        (r'^          - python: "3\.12"\n            ffmpeg: distro\n          - python: "3\.14"\n            ffmpeg: pinned\n',
         "no matrix"),
        (r'^          version: "[0-9.]+"\n', "no pinned uv version"),
        (r"^          FFMPEG_URL: .*\n", "no FFMPEG_URL"),
        (r"^          FFMPEG_SHA256: .*\n", "no FFMPEG_SHA256"),
    ],
    ids=["no python job", "no timeout", "no matrix", "no uv version", "no ffmpeg address", "no ffmpeg sha256"],
)
def test_a_workflow_missing_a_value_the_container_needs_is_refused(pattern: str, message: str) -> None:
    with pytest.raises(ci.Refused, match=message):
        ci.parse_workflow(without(WORKFLOW_TEXT, pattern))


@pytest.mark.parametrize("change", [r"\1", r"\g<0>0"], ids=["one digit short", "one digit long"])
def test_a_sha256_of_63_or_65_digits_is_refused(change: str) -> None:
    edge = re.sub(r"(FFMPEG_SHA256: [0-9a-f]{63})[0-9a-f]", change, WORKFLOW_TEXT)
    assert edge != WORKFLOW_TEXT
    with pytest.raises(ci.Refused, match="FFMPEG_SHA256"):
        ci.parse_workflow(edge)


@pytest.mark.parametrize(
    "change",
    [
        ("      - run: uv sync --frozen\n", "      - run: uv sync --frozen\n      - run: uv run --frozen ruff check\n"),
        ("      - run: uv sync --frozen\n", ""),
        ("pytest -rs --no-unexpected-skips", "pytest -rs --no-unexpected-skips -x"),
    ],
    ids=["a command added", "a command removed", "a command changed"],
)
def test_a_workflow_whose_python_job_runs_other_commands_is_refused(change: tuple[str, str]) -> None:
    old, new = change
    assert old in WORKFLOW_TEXT
    with pytest.raises(ci.Refused, match="update scripts/ci_linux/"):
        ci.check_workflow(ci.parse_workflow(WORKFLOW_TEXT.replace(old, new, 1)))


def test_a_uv_version_without_a_known_sha256_and_an_unknown_ffmpeg_are_refused() -> None:
    workflow = ci.parse_workflow(WORKFLOW_TEXT)
    with pytest.raises(ci.Refused, match="uv 0.0.1"):
        ci.check_workflow(ci.Workflow(**{**workflow.__dict__, "uv_version": "0.0.1"}))
    odd = (*workflow.jobs, ci.Job("py313-system", "3.13", "system"))
    with pytest.raises(ci.Refused, match="py313-system"):
        ci.check_workflow(ci.Workflow(**{**workflow.__dict__, "jobs": odd}))


# The bound

BOUND_EDGES = [
    (str(ci.BOUND_MIN_SEC), ci.BOUND_MIN_SEC),
    (str(ci.BOUND_MAX_SEC), ci.BOUND_MAX_SEC),
    (str(ci.BOUND_MIN_SEC - 1), None),
    (str(ci.BOUND_MAX_SEC + 1), None),
    ("-5", None),
    ("1.5", None),
    (" 60", None),
    ("60s", None),
    ("", None),
]


@pytest.mark.parametrize("text,value", BOUND_EDGES, ids=[repr(text) for text, _ in BOUND_EDGES])
def test_the_bound_is_a_whole_number_of_seconds_within_its_range(text: str, value: int | None) -> None:
    if value is None:
        with pytest.raises(Exception, match="the bound is"):
            ci.parse_bound(text)
    else:
        assert ci.parse_bound(text) == value


@pytest.mark.parametrize(
    "pytest_args,names",
    [
        ([], False),
        (["-x"], False),
        (["-x", "-q"], False),
        (["tests"], True),
        (["tests/media/test_clip_io.py"], True),
        (["-x", "tests/media"], True),
        (["test_x.py::test_one"], True),
        (["testsx"], False),
    ],
)
def test_the_default_bound_is_the_workflows_timeout_for_a_whole_job_and_600_s_for_named_tests(
    pytest_args: list[str], names: bool
) -> None:
    workflow = ci.parse_workflow(WORKFLOW_TEXT)
    assert ci.names_tests(pytest_args) is names
    assert ci.default_bound(workflow, pytest_args) == (ci.TESTS_BOUND_SEC if names else PYTHON_JOB_LIMIT_SEC)
    assert ci.TESTS_BOUND_SEC == 600


# The files copied in


@pytest.mark.parametrize(
    "name,left_out",
    [
        ("data/uploads", True), ("data/uploads/a.mov", True), ("data/artifacts/x/y", True), ("data/renders", True),
        ("data/ingest", True), ("data/ingest/2026/a.heic", True), (".runtime", True), (".runtime/x.sqlite", True),
        (".cache", True), (".cache/dust/a.fits", True), ("data/ingestx", False), (".runtimex", False),
        ("data/uploadsx", False), ("data/uploads.md", False), ("data", False), ("data/downloads.json", False),
        (".cachex", False), ("x/.cache", False), ("x/data/uploads/a", False), ("data/artifact", False),
    ],
)
def test_only_an_owner_folder_or_cache_and_what_is_under_it_are_left_out(name: str, left_out: bool) -> None:
    assert ci.is_left_out(name, LEFT) is left_out


def test_the_script_finds_this_repositorys_owner_folders() -> None:
    assert any(line.startswith(ci.MEDIA_BLOCK) for line in IGNORE_TEXT.splitlines()), "no line opens the media block"
    assert OWNERS, "the script finds no owner folders in this repository's .gitignore"


def test_a_whole_jobs_default_bound_is_whatever_limit_the_workflow_states() -> None:
    changed = re.sub(r"^(    timeout-minutes: )\d+$", r"\g<1>37", WORKFLOW_TEXT[WORKFLOW_TEXT.index("\n  python:\n") :],
                     count=1, flags=re.MULTILINE)
    workflow = ci.parse_workflow(WORKFLOW_TEXT[: WORKFLOW_TEXT.index("\n  python:\n")] + changed)
    assert workflow.timeout_minutes == 37 and ci.default_bound(workflow, []) == 37 * 60


def test_the_owner_folders_are_every_folder_of_gitignores_media_block_and_hold_the_ingest_stores() -> None:
    lines = IGNORE_TEXT.splitlines()
    start = next(at for at, line in enumerate(lines) if line.startswith(ci.MEDIA_BLOCK))
    block = []
    for line in lines[start + 1 :]:
        if not line.strip() or line.startswith("#"):
            break
        block.append(line.strip())
    assert set(OWNERS) == {entry.strip("/") for entry in block if entry.endswith("/")}
    assert {".runtime", "data/uploads", "data/artifacts", "data/ingest", "data/renders"} <= set(OWNERS)
    from services.ingest.store import DEFAULT_ROOT  # where the store keeps the owner's uploads

    assert ci.is_left_out(DEFAULT_ROOT.relative_to(ROOT).as_posix(), LEFT)


def test_a_folder_added_to_the_block_is_left_out_with_no_change_to_the_script(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    ignore = (repo / ".gitignore").read_text(encoding="utf-8")
    header = next(line for line in ignore.splitlines() if line.startswith(ci.MEDIA_BLOCK))
    (repo / ".gitignore").write_text(ignore.replace(header + "\n", header + "\ndata/new-media/\n", 1), encoding="utf-8")
    (repo / "data" / "new-media").mkdir()
    (repo / "data" / "new-media" / "clip.mov").write_bytes(b"made up")
    git(repo, "add", "--force", ".gitignore", "data/new-media/clip.mov")
    git(repo, "commit", "-q", "-m", "a new media folder")
    left = ci.left_out_folders(repo, "HEAD")
    assert "data/new-media" in left
    assert "data/new-media/clip.mov" not in entries_by_name(ci.worktree_entries(repo, left))
    assert "data/new-media/clip.mov" not in entries_by_name(ci.rev_entries(repo, "HEAD", left))


@pytest.mark.parametrize("folder", sorted(OWNERS))
def test_every_owner_folder_stays_out_tracked_or_not_with_a_commit_or_without(tmp_path: Path, folder: str) -> None:
    repo = make_repo(tmp_path / "repo")  # private.bin under each folder, force-tracked and committed
    (repo / folder / "staged.bin").write_bytes(b"made up, staged, not committed")
    git(repo, "add", "--force", f"{folder}/staged.bin")
    (repo / folder / "loose.bin").write_bytes(b"made up, untracked")
    left = ci.left_out_folders(repo, "HEAD")
    worktree = entries_by_name(ci.worktree_entries(repo, left))
    committed = entries_by_name(ci.rev_entries(repo, "HEAD", left))
    assert not [name for name in (*worktree, *committed) if name.startswith(folder + "/")]
    assert "plain.txt" in worktree and "plain.txt" in committed


def test_the_checkouts_block_and_the_commits_block_are_both_kept(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    ignore = (repo / ".gitignore").read_text(encoding="utf-8")
    (repo / ".gitignore").write_text(ignore.replace("data/ingest/\n", "", 1), encoding="utf-8")
    git(repo, "commit", "-q", "-a", "-m", "a commit whose block lacks the ingest folder")
    (repo / ".gitignore").write_text(ignore, encoding="utf-8")  # the checkout's own block has it
    assert "data/ingest" in ci.left_out_folders(repo, "HEAD")
    git(repo, "commit", "-q", "-a", "-m", "the block whole again")
    (repo / ".gitignore").write_text(ignore.replace("data/ingest/\n", "", 1), encoding="utf-8")
    assert "data/ingest" in ci.left_out_folders(repo, "HEAD"), "the commit's block holds it"
    assert "data/ingest" not in ci.left_out_folders(repo, None), "the working tree's block alone does not"


@pytest.mark.parametrize(
    "text,folders",
    [
        (f"{ci.MEDIA_BLOCK}\na/\nb/\n\nc/\n", ("a", "b")),
        (f"x/\n{ci.MEDIA_BLOCK} (and more words)\na/\n# next block\nc/\n", ("a",)),
        (f"{ci.MEDIA_BLOCK}\na/\n*.db\nsplats/*.rad\nb/\n", ("a", "b")),
        (f"{ci.MEDIA_BLOCK}\n/a/\nb/c/\n", ("a", "b/c")),
    ],
    ids=["ends at a blank line", "ends at a comment", "file patterns are not folders", "a folder anchored at the root"],
)
def test_the_block_is_read_from_its_line_to_its_end(text: str, folders: tuple[str, ...]) -> None:
    assert ci.owner_folders(text) == folders


def outcome(call):
    """What a call returns, or the refusal or error it raises as text, so a test says by an assertion what went
    wrong either way."""
    try:
        return call()
    except (ci.Refused, OSError) as error:
        return f"{type(error).__name__}: {error}"


def test_a_text_without_the_block_does_not_hide_a_later_texts_block() -> None:
    assert outcome(lambda: ci.owner_folders("a/\n", f"{ci.MEDIA_BLOCK}\nb/\n")) == ("b",)
    assert outcome(lambda: ci.owner_folders(f"{ci.MEDIA_BLOCK}\nb/\n", "a/\n")) == ("b",)


def test_a_checkout_or_a_commit_without_an_ignore_file_takes_the_others_block(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    git(repo, "rm", "-q", "--cached", ".gitignore")
    git(repo, "commit", "-q", "-m", "a commit without the ignore file")
    found = outcome(lambda: ci.left_out_folders(repo, "HEAD"))
    assert isinstance(found, tuple) and set(OWNERS) <= set(found), f"the checkout's own block holds: {found}"
    (repo / ".gitignore").unlink()
    found = outcome(lambda: ci.left_out_folders(repo, "HEAD"))
    assert "Refused: " in str(found) and "owner's folders are not known" in str(found), found
    found = outcome(lambda: ci.left_out_folders(repo, "HEAD~1"))
    assert isinstance(found, tuple) and set(OWNERS) <= set(found), f"the commit's block holds: {found}"


@pytest.mark.parametrize("text", ["a/\nb/\n", f"{ci.MEDIA_BLOCK}\n*.db\n", ""], ids=["no block", "no folder", "empty"])
def test_no_block_or_a_block_without_a_folder_is_refused(text: str) -> None:
    with pytest.raises(ci.Refused, match="owner's folders are not known"):
        ci.owner_folders(text)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    (path / ".gitignore").write_text(IGNORE_TEXT + "\nignored.txt\n", encoding="utf-8")
    (path / "plain.txt").write_text("committed\n", encoding="utf-8")
    (path / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (path / "run.sh").chmod(0o755)
    (path / "deep" / "er").mkdir(parents=True)
    (path / "deep" / "er" / "leaf.py").write_text("x = 1\n", encoding="utf-8")
    (path / "gone.txt").write_text("deleted later\n", encoding="utf-8")
    (path / "link.txt").symlink_to("plain.txt")
    (path / ".github" / "workflows").mkdir(parents=True)
    (path / ".github" / "workflows" / "gates.yml").write_text(WORKFLOW_TEXT, encoding="utf-8")
    for folder in LEFT:
        (path / folder).mkdir(parents=True)
        (path / folder / "private.bin").write_bytes(b"never copied")
    (path / "data").mkdir(exist_ok=True)  # there even when the script finds no owner folder under it
    (path / "data" / "uploads.md").write_text("a file beside the folder\n", encoding="utf-8")
    git(path, "add", "--all")
    # Force-tracked, as a mistake could leave them: still never copied
    git(path, "add", "--force", *(f"{folder}/private.bin" for folder in LEFT))
    git(path, "commit", "-q", "-m", "first")
    return path


def entries_by_name(entries: Iterator) -> dict:
    try:
        return {entry.name: entry for entry in entries}
    except ci.Refused as error:
        pytest.fail(f"unexpected refusal: {error}")


def test_the_working_tree_snapshot_holds_what_is_on_disk_and_never_an_owner_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / "plain.txt").write_text("changed on disk\n", encoding="utf-8")
    (repo / "new.txt").write_text("new, not ignored\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (repo / "gone.txt").unlink()
    found = entries_by_name(ci.worktree_entries(repo, LEFT))
    assert found["plain.txt"].data == b"changed on disk\n"
    assert found["new.txt"].data == b"new, not ignored\n"
    assert "ignored.txt" not in found and "gone.txt" not in found
    assert found["run.sh"].mode == 0o755 and found["plain.txt"].mode == 0o644
    assert found["link.txt"].link == "plain.txt" and found["link.txt"].data == b""
    assert found["deep/er/leaf.py"].data == b"x = 1\n"
    assert "data/uploads.md" in found
    assert not [name for name in found if ci.is_left_out(name, LEFT)]


def test_a_commit_snapshot_holds_the_committed_bytes_and_never_an_owner_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    first = git(repo, "rev-parse", "HEAD").strip()
    (repo / "plain.txt").write_text("changed later\n", encoding="utf-8")
    (repo / "gone.txt").unlink()
    git(repo, "commit", "-q", "-a", "-m", "second")
    found = entries_by_name(ci.rev_entries(repo, first, LEFT))
    assert found["plain.txt"].data == b"committed\n"
    assert found["gone.txt"].data == b"deleted later\n"
    assert found["run.sh"].mode == 0o755 and found["plain.txt"].mode == 0o644
    assert found["link.txt"].link == "plain.txt"
    assert not [name for name in found if ci.is_left_out(name, LEFT)]
    assert entries_by_name(ci.rev_entries(repo, "HEAD", LEFT))["plain.txt"].data == b"changed later\n"


def test_the_tar_gives_every_file_and_folder_to_the_runner_account() -> None:
    out = io.BytesIO()
    entries = [ci.Entry("a/b/c.txt", 0o644, b"abc"), ci.Entry("a/run.sh", 0o755, b"#"), ci.Entry("a/l", 0o777, link="b")]
    assert ci.write_tar(iter(entries), out, 1.0) == 3
    out.seek(0)
    with tarfile.open(fileobj=out) as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert archive.extractfile("a/b/c.txt").read() == b"abc"
    assert sorted(members) == ["a", "a/b", "a/b/c.txt", "a/l", "a/run.sh"]
    assert all(m.uid == m.gid == ci.RUNNER_ID and m.uname == "runner" for m in members.values())
    assert members["a"].isdir() and members["a/b"].isdir() and members["a"].mode == 0o755
    assert members["a/run.sh"].mode == 0o755 and members["a/b/c.txt"].mode == 0o644
    assert members["a/l"].issym() and members["a/l"].linkname == "b"


# The cache mount


def point_cache(repo: Path, target: Path) -> None:
    link = repo / ".cache"
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        for child in link.iterdir():
            child.unlink()
        link.rmdir()
    link.symlink_to(target)


OWNER_REFUSAL = "is, holds or sits inside the owner's folder"
CHECKOUT_REFUSAL = "holds the checkout"


@pytest.mark.parametrize(
    "target,message",
    [
        ("data/uploads", OWNER_REFUSAL), ("data/artifacts/sub", OWNER_REFUSAL), ("data", OWNER_REFUSAL),
        (".", CHECKOUT_REFUSAL), ("..", CHECKOUT_REFUSAL), ("data/renders", OWNER_REFUSAL),
        ("data/ingest", OWNER_REFUSAL), ("data/ingest/sub", OWNER_REFUSAL), (".runtime", OWNER_REFUSAL),
    ],
    ids=["an owner folder", "inside one", "a folder that holds them", "the checkout", "above the checkout", "renders",
         "the ingest store", "inside the ingest store", "the runtime folder"],
)
def test_a_cache_that_is_holds_or_sits_inside_an_owner_folder_is_refused(tmp_path: Path, target: str, message: str) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / "data" / "artifacts" / "sub").mkdir()
    (repo / "data" / "ingest" / "sub").mkdir()
    point_cache(repo, (repo / target).resolve())
    with pytest.raises(ci.Refused, match=f"--with-cache: .*{re.escape(message)}"):
        ci.cache_source(repo, OWNERS)


def test_a_worktrees_cache_that_leads_to_the_main_checkouts_owner_folder_is_refused(tmp_path: Path) -> None:
    main = make_repo(tmp_path / "main")
    git(main, "worktree", "add", "-q", "--detach", str(tmp_path / "lane"))
    lane = tmp_path / "lane"
    point_cache(lane, main / "data" / "uploads")
    with pytest.raises(ci.Refused, match="owner's folder"):
        ci.cache_source(lane, OWNERS)
    (tmp_path / "public").mkdir()
    point_cache(lane, tmp_path / "public")
    assert ci.cache_source(lane, OWNERS) == (tmp_path / "public").resolve()


def test_a_missing_cache_or_one_that_is_a_file_is_refused_and_a_separate_folder_is_taken(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    point_cache(repo, tmp_path / "nowhere")
    with pytest.raises(ci.Refused, match="does not lead to a folder"):
        ci.cache_source(repo, OWNERS)
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    point_cache(repo, tmp_path / "a-file")
    with pytest.raises(ci.Refused, match="not a folder"):
        ci.cache_source(repo, OWNERS)
    (tmp_path / "datasets").mkdir()
    point_cache(repo, tmp_path / "datasets")
    assert ci.cache_source(repo, OWNERS) == (tmp_path / "datasets").resolve()
    command = ci.engine_command(repo, OWNERS)
    assert command[command.index("--mount") + 1] == str((tmp_path / "datasets").resolve())
    assert "--activate=false" in command and "--vz-rosetta" in command


# A whole run against the stand-in docker


UV_VOLUME = "type=volume,source=albedian-ci-uv-cache-{job},target=/home/runner/work/_temp/setup-uv-cache"


def mounts_of(created: list[str]) -> list[str]:
    return [created[i + 1] for i, arg in enumerate(created) if arg == "--mount"]


class Fake:
    def __init__(self, tmp_path: Path, mode: str):
        self.dir = tmp_path / f"fake-{len(list(tmp_path.glob('fake-*')))}"
        self.dir.mkdir()
        self.mode = mode
        self.command = shlex.join([sys.executable, str(FAKE)])

    def calls(self) -> list[list[str]]:
        log = self.dir / "calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line)["args"] for line in log.read_text(encoding="utf-8").splitlines()]

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls()]


@pytest.fixture
def run_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    repo = make_repo(tmp_path / "repo")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(ci, "INTERRUPT_GRACE_SEC", 3)
    monkeypatch.setattr(ci, "STACKS_WAIT_SEC", 0)
    monkeypatch.setattr(ci, "LOCK_PATH", tmp_path / "engine.lock")
    monkeypatch.setattr(ci, "SHORT_LOCK_PATH", tmp_path / "engine.short.lock")
    monkeypatch.setattr(ci, "LOCK_POLL_SEC", 0.1)
    monkeypatch.setattr(ci, "LOCK_WAIT_LIMIT_SEC", 30)
    monkeypatch.setenv("FAKE_DOCKER_LOCK", str(tmp_path / "engine.lock"))
    monkeypatch.setenv("FAKE_DOCKER_SHORT_LOCK", str(tmp_path / "engine.short.lock"))

    def run(mode: str, *args: str) -> tuple[int, str, Fake, float]:
        fake = Fake(tmp_path, mode)
        monkeypatch.setenv("FAKE_DOCKER_DIR", str(fake.dir))
        monkeypatch.setenv("FAKE_DOCKER_MODE", mode)
        started = time.monotonic()
        code = ci.main(["--docker", fake.command, *args])
        return code, capsys.readouterr().out, fake, time.monotonic() - started

    run.repo = repo
    return run


def test_a_hang_ends_at_the_bound_naming_the_test_with_its_stack_and_the_container_removed_by_id(run_script) -> None:
    # Five seconds, so the stand-in has started under any load before the bound passes
    code, out, fake, _ = run_script("hang", "--bound", "5", "py314-pinned", "tests/x.py")
    assert code == ci.EXIT_BOUND
    assert "THE BOUND OF 5 s PASSED while running tests/x.py::test_hangs" in out
    assert 'File "tests/x.py", line 3 in test_hangs' in out
    assert "KeyboardInterrupt" in out
    calls = fake.calls()
    container = "c0ffee" + "0123456789abcdef" * 3 + "0123456789"
    assert ["exec", container, "kill", "-USR1", "4242"] in calls
    assert ["exec", container, "kill", "-INT", "4242"] in calls
    assert calls[-1] == ["rm", "--force", container]
    assert "kill" not in fake.commands(), "the interrupt was enough; the container was not killed"


def test_a_hang_past_the_interrupt_is_stopped_by_the_containers_id(run_script) -> None:
    code, out, fake, _ = run_script("hang-past-interrupt", "--bound", "1", "py312-distro")
    assert code == ci.EXIT_BOUND
    container = "c0ffee" + "0123456789abcdef" * 3 + "0123456789"
    assert "pytest did not end after the interrupt; stopping the container by its id" in out
    assert ["kill", container] in fake.calls() and fake.calls()[-1] == ["rm", "--force", container]


def test_a_pid_file_that_holds_no_number_gets_no_signal_and_the_container_is_stopped_by_id(run_script) -> None:
    code, out, fake, _ = run_script("hang-bad-pid", "--bound", "5", "py314-pinned")
    assert code == ci.EXIT_BOUND and "while running tests/x.py::test_hangs" in out
    container = "c0ffee" + "0123456789abcdef" * 3 + "0123456789"
    assert not [call for call in fake.calls() if call[:3] == ["exec", container, "kill"]]
    assert ["kill", container] in fake.calls() and fake.calls()[-1] == ["rm", "--force", container]


def test_a_hang_between_tests_names_the_last_test_that_ended(run_script) -> None:
    code, out, fake, _ = run_script("hang-between-tests", "--bound", "5", "py314-pinned")
    assert code == ci.EXIT_BOUND
    assert "THE BOUND OF 5 s PASSED between tests, after tests/x.py::test_ok ended" in out
    assert "KeyboardInterrupt" in out


def test_a_hang_before_pytest_names_the_step(run_script) -> None:
    code, out, fake, _ = run_script("hang-before-pytest", "--bound", "5", "py312-distro")
    assert code == ci.EXIT_BOUND
    assert "PASSED before pytest started, in the step: uv sync --frozen" in out
    assert not [call for call in fake.calls() if call[:1] == ["exec"] and "kill" in call]


@pytest.mark.parametrize("mode,code", [("pass", 0), ("fail", 1)])
def test_a_job_that_ends_gives_its_own_exit_status(run_script, mode: str, code: int) -> None:
    found, out, fake, _ = run_script(mode, "py314-pinned")
    assert found == code
    assert ("PASSED (exit 0)" if code == 0 else "FAILED (exit 1)") in out
    assert f"bound {PYTHON_JOB_LIMIT_SEC} s" in out
    created = json.loads((fake.dir / "created").read_text(encoding="utf-8"))
    assert created[-4:] == ["sha256:" + "ab" * 32, "bash", "/opt/ci-linux/job.bash", "pinned"]
    for setting in ("CI=true", "GITHUB_ACTIONS=true", "UV_PYTHON=3.14"):
        assert setting in created
    assert "--init" in created and created[created.index("--platform") + 1] == "linux/amd64"
    assert not {"-v", "--volume"} & set(created)
    assert mounts_of(created) == [UV_VOLUME.format(job="py314-pinned")], "nothing of this laptop is mounted"
    assert fake.commands()[-1] == "rm"
    build = next(call for call in fake.calls() if call[0] == "buildx")
    assert build[build.index("--platform") + 1] == "linux/amd64" and build[build.index("--target") + 1] == "pinned"
    assert build[build.index("--builder") + 1] == "default"
    assert f"UV_SHA256={ci.UV_SHA256['0.12.5']}" in build
    assert any(arg.startswith("FFMPEG_SHA256=") for arg in build) and build[-1] == str(IMAGE_DIR)


def test_the_distro_job_builds_its_own_target_without_the_pinned_ffmpeg(run_script) -> None:
    code, _, fake, _ = run_script("pass", "py312-distro")
    assert code == 0
    build = next(call for call in fake.calls() if call[0] == "buildx")
    assert build[build.index("--target") + 1] == "distro"
    assert not [arg for arg in build if arg.startswith("FFMPEG_")]
    created = json.loads((fake.dir / "created").read_text(encoding="utf-8"))
    assert "UV_PYTHON=3.12" in created and created[-1] == "distro"


# A left-out folder spelled another way: this laptop's disk, and git on it, do not tell letter case apart, and treat a
# letter written composed or decomposed as one


def caseless(text: str) -> str:
    """Unicode's canonical caseless form, written here apart from the script's, so a fault in that cannot hide."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", text).casefold())


def inside_by_name(name: str, folders) -> bool:
    parts = [caseless(part) for part in PurePosixPath(name).parts]
    return any(parts[: len(PurePosixPath(f).parts)] == [caseless(p) for p in PurePosixPath(f).parts] for f in folders)


def spellings(folder: str) -> list[str]:
    """The folder under other letter cases: all upper, each part's first letter upper, every other letter upper."""
    first = "/".join(("." + part[1:2].upper() + part[2:]) if part.startswith(".") else part[:1].upper() + part[1:]
                     for part in folder.split("/"))
    every_other = "".join(c.upper() if at % 2 else c for at, c in enumerate(folder))
    return sorted({folder.upper(), first, every_other} - {folder})


def plant(repo: Path, name: str, data: bytes) -> None:
    """Writes made-up `data` at `name` as this disk resolves it, and commits that exact name: git add would take a
    tracked folder's own spelling on a disk that does not tell case apart, as the reader saw."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    blob = git(repo, "hash-object", "-w", "--", str(target)).strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    git(repo, "commit", "-q", "-m", f"planted {name}")


def copied(fake: Fake) -> tuple[list[str], list[bytes]]:
    with tarfile.open(fake.dir / "copied.tar") as archive:
        members = archive.getmembers()
        return [m.name for m in members], [archive.extractfile(m).read() for m in members if m.isfile()]


@pytest.mark.parametrize("folder,spelling", [(f, s) for f in sorted(LEFT) for s in spellings(f)])
def test_a_left_out_folder_spelled_another_way_never_enters_the_container(run_script, folder: str, spelling: str) -> None:
    marker = f"made up, planted under {spelling}\n".encode("utf-8")
    plant(run_script.repo, f"{spelling}/planted.bin", marker)
    for args in (("--rev", "HEAD"), ()):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == 0, out
        names, contents = copied(fake)
        assert not [name for name in names if inside_by_name(name, LEFT)], (args, spelling)
        assert marker not in contents, (args, spelling)


@pytest.mark.parametrize("name,folder", [("data/vide\u0301os/a.mov", "data/vid\u00e9os"),
                                         ("data/vid\u00e9os/a.mov", "data/vide\u0301os"),
                                         ("DATA/VID\u00c9OS/a.mov", "data/vid\u00e9os"),
                                         ("DATA/STRASSE/a.mov", "data/stra\u00dfe"),
                                         ("data/\u03a3\u0391\u03a3/a.mov", "data/\u03c3\u03b1\u03c2")],
                         ids=["decomposed name, composed folder", "composed name, decomposed folder", "upper case",
                              "full case folding, as this disk folds ss and sharp s", "a final sigma"])
def test_a_letter_written_composed_or_decomposed_names_the_same_folder(name: str, folder: str) -> None:
    assert ci.is_left_out(name, (folder,))
    assert not ci.is_left_out(name.replace("/a.mov", "x/a.mov", 1), (folder,)), "a folder one letter longer"


def test_a_path_the_disk_resolves_into_a_left_out_folder_stops_the_run_before_any_engine_call(run_script) -> None:
    # A folder of another name linked to the uploads: its files are the owner's, whatever their names say
    repo = run_script.repo
    (repo / "photos").symlink_to("data/uploads", target_is_directory=True)
    plant(repo, "photos/planted.bin", b"made up, reached through a link")
    for args in (("--rev", "HEAD"), ()):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == ci.EXIT_USAGE, out
        assert "inside the left-out folder data/uploads (as photos); nothing was copied" in out
        assert fake.calls() == [], "nothing reached the engine"
        assert "planted.bin" not in out, "no file of the folder is named"


def first_versions_check(name: str, left_out) -> bool:
    """The second version's name check, letter for letter: what the reader got past."""
    parts = PurePosixPath(name).parts
    return any(parts[: len(PurePosixPath(folder).parts)] == PurePosixPath(folder).parts for folder in left_out)


def test_with_a_name_check_blind_to_case_the_disk_check_still_stops_the_owners_folder(
    run_script, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = run_script.repo
    plant(repo, "data/Uploads/planted.bin", b"made up")
    monkeypatch.setattr(ci, "is_left_out", first_versions_check)
    resolves = os.path.samefile(repo / "data" / "Uploads", repo / "data" / "uploads")
    code, out, fake, _ = run_script("pass", "--rev", "HEAD", "py312-distro")
    if resolves:  # this disk does not tell case apart: data/Uploads is the owner's folder
        assert code == ci.EXIT_USAGE and fake.calls() == [], out
        assert "inside the left-out folder data/uploads (as data/Uploads); nothing was copied" in out
    else:  # this disk tells case apart: data/Uploads is a folder of its own, made by plant() above
        assert code == 0, out
        assert "data/Uploads/planted.bin" in copied(fake)[0]


# Where a thing really lives: a link anywhere inside a left-out folder, a chain of links, a link at a file's own name,
# a hard link, changes while the run waits (P4 and N4), and a file replaced between lstat and open


def when_said(monkeypatch: pytest.MonkeyPatch, text: str, action) -> None:
    """Runs `action` once, the first time the script says a line holding `text`."""
    original = ci.Run.say
    done = []

    def say(self, line: str) -> None:
        original(self, line)
        if text in line and not done:
            done.append(True)
            action()

    monkeypatch.setattr(ci.Run, "say", say)


@pytest.mark.parametrize("target", ["data/uploads/sub", "data/uploads/sub/deeper"], ids=["one level down", "two levels down"])
def test_a_folder_linked_anywhere_inside_a_left_out_folder_stops_the_run(run_script, target: str) -> None:
    repo = run_script.repo
    (repo / target).mkdir(parents=True)
    (repo / "photos").symlink_to(target, target_is_directory=True)
    plant(repo, "photos/planted.bin", b"made up, reached through a link inside the uploads")
    for args in (("--rev", "HEAD"), ()):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == ci.EXIT_USAGE, (args, out)
        assert "inside the left-out folder data/uploads (as photos); nothing was copied" in out
        assert fake.calls() == [], "nothing reached the engine"


def test_a_chain_of_links_into_a_left_out_folder_stops_the_run(run_script) -> None:
    repo = run_script.repo
    (repo / "data" / "uploads" / "sub").mkdir()
    (repo / "b").symlink_to("data/uploads/sub", target_is_directory=True)
    (repo / "a").symlink_to("b", target_is_directory=True)
    plant(repo, "a/planted.bin", b"made up, reached through two links")
    for args in (("--rev", "HEAD"), ()):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == ci.EXIT_USAGE and "inside the left-out folder data/uploads (as a); nothing was copied" in out, out
        assert fake.calls() == []


def test_a_link_at_a_files_own_name_into_a_left_out_folder_stops_the_run(run_script) -> None:
    repo = run_script.repo
    (repo / "data" / "uploads" / "sub").mkdir()
    (repo / "data" / "uploads" / "sub" / "marker.bin").write_bytes(b"made up, inside the uploads")
    (repo / "plain").mkdir()
    (repo / "plain" / "leak.bin").symlink_to("../data/uploads/sub/marker.bin")
    git(repo, "add", "plain/leak.bin")
    git(repo, "commit", "-q", "-m", "a file link")
    for args in ((), ("--rev", "HEAD")):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == ci.EXIT_USAGE, (args, out)
        assert "a link to copy resolves on this disk inside the left-out folder data/uploads (in plain); nothing was copied" in out
        assert "leak.bin" not in out and "marker.bin" not in out
        assert fake.calls() == []
    # Even when removed from the working tree, the commit's 120000 entry is refused from git's own blob
    (repo / "plain" / "leak.bin").unlink()
    code, out, fake, _ = run_script("pass", "--rev", "HEAD", "py312-distro")
    assert code == ci.EXIT_USAGE and "inside the left-out folder data/uploads (in plain); nothing was copied" in out, out
    assert fake.calls() == []
    # A link target that reaches data/uploads through an ignored intermediate directory link needs realpath on target
    (repo / ".git" / "info" / "exclude").write_text("mid\n", encoding="utf-8")
    (repo / "mid").symlink_to("data/uploads/sub", target_is_directory=True)
    (repo / "plain" / "via_mid.bin").symlink_to("../mid/marker.bin")
    code, out, fake, _ = run_script("pass", "py312-distro")
    assert code == ci.EXIT_USAGE and "inside the left-out folder data/uploads (in plain); nothing was copied" in out, out
    assert fake.calls() == []


def test_a_hard_link_to_a_file_inside_a_left_out_folder_stops_the_run(run_script) -> None:
    repo = run_script.repo
    (repo / "data" / "uploads" / "marker.bin").write_bytes(b"made up, inside the uploads")
    (repo / "plain").mkdir()
    os.link(repo / "data" / "uploads" / "marker.bin", repo / "plain" / "linked.bin")
    git(repo, "add", "plain/linked.bin")
    git(repo, "commit", "-q", "-m", "a hard link")
    for args in (("--rev", "HEAD"), ()):
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
        assert code == ci.EXIT_USAGE, (args, out)
        assert "the same file on disk as one inside the left-out folder data/uploads (in plain); nothing was copied" in out
        assert fake.calls() == []
        assert "linked.bin" not in out and "marker.bin" not in out, "no file of the folder is named"


@pytest.mark.parametrize("args", [("--rev", "HEAD"), ()], ids=["a commit", "the working tree"])
def test_a_folder_made_a_link_while_the_run_waits_is_judged_as_it_is_copied(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    # The reader's P4: photos is a plain folder when the run starts, and a link to the uploads when it copies
    repo = run_script.repo
    (repo / "photos").mkdir()
    plant(repo, "photos/planted.bin", b"made up, a plain folder's file")
    (repo / "data" / "uploads" / "planted.bin").write_bytes(b"made up, inside the uploads")
    stop = threading.Event()

    def make_it_a_link() -> None:
        (repo / "photos" / "planted.bin").unlink()
        (repo / "photos").rmdir()
        (repo / "photos").symlink_to("data/uploads", target_is_directory=True)
        stop.set()

    when_said(monkeypatch, "waiting for the engine's whole-job lane", make_it_a_link)
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert (repo / "photos").is_symlink(), "the folder became a link during the wait"
    assert code == ci.EXIT_USAGE, out
    assert "inside the left-out folder data/uploads (as photos); nothing was copied" in out
    assert "cp" not in fake.commands(), "nothing was copied into the container"
    assert fake.calls()[-1][:2] == ["rm", "--force"], "the container made before the copy is removed by its id"


@pytest.mark.parametrize("args", [("--rev", "HEAD"), ()], ids=["a commit", "the working tree"])
def test_a_file_made_a_hard_link_while_the_run_waits_is_judged_as_it_is_copied(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    repo = run_script.repo
    (repo / "data" / "uploads" / "marker.bin").write_bytes(b"made up, inside the uploads")
    plant(repo, "plain/linked.bin", b"made up, a plain file")
    stop = threading.Event()

    def make_it_a_hard_link() -> None:
        (repo / "plain" / "linked.bin").unlink()
        os.link(repo / "data" / "uploads" / "marker.bin", repo / "plain" / "linked.bin")
        stop.set()

    when_said(monkeypatch, "waiting for the engine's whole-job lane", make_it_a_hard_link)
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", *args, "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert os.stat(repo / "plain" / "linked.bin").st_nlink == 2, "the file became a hard link during the wait"
    assert code == ci.EXIT_USAGE, out
    assert "the same file on disk as one inside the left-out folder data/uploads (in plain); nothing was copied" in out
    assert "cp" not in fake.commands()


@pytest.mark.parametrize("change", ["replaced", "deleted"])
def test_a_file_changed_while_the_run_waits_stops_the_run(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repo = run_script.repo
    plant(repo, "plain/tracked.txt", b"made up, before the wait")
    stop = threading.Event()

    def alter() -> None:
        target = repo / "plain" / "tracked.txt"
        if change == "deleted":
            target.unlink()
        else:
            other = target.with_name("tracked.txt.next")
            other.write_bytes(b"made up, swapped during the wait")
            os.replace(other, target)
        stop.set()

    when_said(monkeypatch, "waiting for the engine's whole-job lane", alter)
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == ci.EXIT_USAGE, out
    assert "a path to copy changed between its check and its read (in plain); nothing was copied" in out
    assert "cp" not in fake.commands()


def test_a_file_added_while_the_run_waits_is_not_in_the_checked_listing(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kills AUDIT N's mutant N4 (re-listing worktree_names after the wait instead of copying the checked listing)
    repo = run_script.repo
    stop = threading.Event()

    def add_late_file() -> None:
        (repo / "late_during_wait.txt").write_bytes(b"made up, added after the pre-wait listing")
        stop.set()

    when_said(monkeypatch, "waiting for the engine's whole-job lane", add_late_file)
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == 0, out
    names, _ = copied(fake)
    assert "late_during_wait.txt" not in names, "only the pre-wait listing is copied"


def test_a_file_replaced_between_its_lstat_and_its_open_stops_the_run(
    run_script, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = run_script.repo / "plain.txt"
    real_open = os.open
    swapped: list[bool] = []
    mode = ["file"]
    opened_target_fds: list[int] = []

    def open_after_a_swap(name, flags, *rest, **kwargs):
        is_target = not isinstance(name, int) and Path(name) == target
        if is_target and not swapped:
            swapped.append(True)
            if mode[0] == "file":
                replacement = target.with_name("plain.txt.next")
                replacement.write_bytes(b"made up, another file under the same name")
                os.replace(replacement, target)
            else:
                target.unlink()
                target.symlink_to("data/uploads/private.bin")
        fd = real_open(name, flags, *rest, **kwargs)
        if is_target and mode[0] == "link":
            opened_target_fds.append(fd)
        return fd

    monkeypatch.setattr(ci.os, "open", open_after_a_swap)
    # Direct worktree_entries (seen=None) catches an lstat-to-open replacement by fstat vs lstat alone
    with pytest.raises(ci.Refused, match="changed between its check and its read"):
        list(ci.worktree_entries(run_script.repo, LEFT, ["plain.txt"]))
    assert swapped, "the file was opened in worktree_entries"
    swapped.clear()
    code, out, fake, _ = run_script("pass", "py312-distro")
    assert swapped, "the file was opened in main"
    assert code == ci.EXIT_USAGE, out
    assert "a path to copy changed between its check and its read (in .); nothing was copied" in out
    assert "cp" not in fake.commands()
    # Swapped into a link between lstat and open: O_NOFOLLOW refuses without opening the link target
    swapped.clear()
    mode[0] = "link"
    code, out, fake, _ = run_script("pass", "py312-distro")
    assert swapped and opened_target_fds == [], "O_NOFOLLOW refused the link without opening its target"
    assert code == ci.EXIT_USAGE and "changed between its check and its read (in .):" in out, out
    assert "cp" not in fake.commands()


# The property, against the real disk in a scratch folder. Each left-out folder holds folders three deep; a path
# reaches a place by a spelling of a left-out folder, by a folder link or a chain of two to a left-out folder or any
# subfolder inside one, by a link at the file's own name, by a hard link to a file inside one, or plainly; the oracle
# is the disk's own answer, by os.path.samefile, checked against ci.worktree_entries itself.
DISK_FOLDERS = ("data/uploads", ".runtime", "data/vid\u00e9os", "data/stra\u00dfe")


def really_inside(root: Path, name: str, left_out) -> bool:
    """The disk's own answer: an ancestor of the file's real place is a left-out folder, or the file is the same file
    as one inside a left-out folder."""
    real = Path(os.path.realpath(root / name))
    for folder in left_out:
        base = root / folder
        if any(ancestor.exists() and os.path.samefile(ancestor, base) for ancestor in (real, *real.parents)):
            return True
        for here, _, names in os.walk(base):
            if (root / name).exists() and any(os.path.samefile(root / name, os.path.join(here, each)) for each in names):
                return True
    return False


def copy_outcome(root: Path, name: str, left_out) -> tuple[bool, list]:
    """(refused, entries) when ci.worktree_entries itself reads `[name]` from `root`."""
    try:
        return False, list(ci.worktree_entries(root, left_out, [name]))
    except ci.Refused:
        return True, []


@settings(max_examples=80, deadline=None)
@example(folder=".runtime", how="spelling", depth=0, cases=[True] * 8, form="NFC", folder_is_link=False)
@example(folder="data/stra\u00dfe", how="spelling", depth=1, cases=[True] * 11, form="NFC", folder_is_link=False)
@example(folder="data/vid\u00e9os", how="spelling", depth=0, cases=[False] * 11, form="NFD", folder_is_link=False)
@example(folder="data/uploads", how="link", depth=1, cases=[False] * 12, form="NFC", folder_is_link=False)
@example(folder="data/uploads", how="link", depth=3, cases=[False] * 12, form="NFC", folder_is_link=True)
@example(folder="data/uploads", how="chain", depth=2, cases=[False] * 12, form="NFC", folder_is_link=False)
@example(folder="data/uploads", how="file link", depth=2, cases=[False] * 12, form="NFC", folder_is_link=False)
@example(folder="data/uploads", how="hard link", depth=2, cases=[False] * 12, form="NFC", folder_is_link=True)
@example(folder="data/uploads", how="plain", depth=0, cases=[False] * 12, form="NFC", folder_is_link=False)
@given(folder=st.sampled_from(DISK_FOLDERS),
       how=st.sampled_from(["spelling", "link", "chain", "file link", "hard link", "plain"]),
       depth=st.integers(0, 3), cases=st.lists(st.booleans(), min_size=12, max_size=12),
       form=st.sampled_from(["NFC", "NFD"]), folder_is_link=st.booleans())
def test_pbt_nothing_whose_real_place_is_inside_a_left_out_folder_is_copied(
    folder: str, how: str, depth: int, cases: list[bool], form: str, folder_is_link: bool
) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for at, each in enumerate(DISK_FOLDERS):
            if folder_is_link and each == folder:
                (root / "elsewhere" / str(at)).mkdir(parents=True)
                (root / each).parent.mkdir(parents=True, exist_ok=True)
                (root / each).symlink_to(root / "elsewhere" / str(at), target_is_directory=True)
            else:
                (root / each).mkdir(parents=True)
            (root / each / "sub0" / "sub1" / "sub2").mkdir(parents=True)
        inner = "".join(f"/sub{level}" for level in range(depth))
        target = root / (folder + inner)
        if how == "spelling":
            spelled = unicodedata.normalize(form, "".join(c.upper() if up else c for c, up in zip(folder, cases + cases)))
            name = f"{spelled}{inner}/x.bin"
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"made up")
        elif how in ("link", "chain"):
            (target / "x.bin").write_bytes(b"made up")
            (root / "b").symlink_to(target, target_is_directory=True)
            first = "b"
            if how == "chain":
                (root / "a").symlink_to(root / "b", target_is_directory=True)
                first = "a"
            name = f"{first}/x.bin"
        elif how == "file link":
            (target / "x.bin").write_bytes(b"made up")
            (root / "plain").mkdir()
            (root / "plain" / "x.bin").symlink_to(target / "x.bin")
            name = "plain/x.bin"
        elif how == "hard link":
            (target / "x.bin").write_bytes(b"made up")
            (root / "plain").mkdir()
            os.link(target / "x.bin", root / "plain" / "x.bin")
            name = "plain/x.bin"
        else:
            (root / "plain").mkdir()
            (root / "plain" / "x.bin").write_bytes(b"made up")
            name = "plain/x.bin"
        inside = really_inside(root, name, DISK_FOLDERS)
        kept = not ci.is_left_out(name, DISK_FOLDERS)
        refused, entries = copy_outcome(root, name, DISK_FOLDERS)
        if inside:
            assert entries == [], f"{how} {name!r} was copied though the disk puts it inside {folder}"
        assert refused == (kept and inside), f"{how} {name!r}: the judge stops exactly what the disk puts inside"
        if not inside and kept:
            assert len(entries) == 1


def test_a_commits_copied_files_hold_its_own_bytes_and_none_of_any_owner_folder(run_script) -> None:
    (run_script.repo / "plain.txt").write_text("changed after the commit\n", encoding="utf-8")
    code, _, fake, _ = run_script("pass", "--rev", "HEAD", "py312-distro")
    assert code == 0
    with tarfile.open(fake.dir / "copied.tar") as archive:
        names = set(archive.getnames())
        assert archive.extractfile("plain.txt").read() == b"committed\n"
    assert not [name for name in names if ci.is_left_out(name, LEFT)]
    assert not [name for name in names if name.startswith(("data/ingest", ".runtime", "data/uploads/"))]


def test_the_copied_files_are_the_checkouts_without_the_owner_folders(run_script) -> None:
    (run_script.repo / "plain.txt").write_text("as on disk\n", encoding="utf-8")
    code, _, fake, _ = run_script("pass", "py312-distro")
    assert code == 0
    with tarfile.open(fake.dir / "copied.tar") as archive:
        names = set(archive.getnames())
        assert archive.extractfile("plain.txt").read() == b"as on disk\n"
    assert {"plain.txt", "run.sh", "deep/er/leaf.py", ".github/workflows/gates.yml"} <= names
    assert not [name for name in names if ci.is_left_out(name, LEFT)]
    assert all(call[0] != "cp" or call[1:3] == ["--archive", "-"] for call in fake.calls())


def test_the_cache_is_mounted_read_only_and_only_when_asked(run_script, tmp_path: Path) -> None:
    (tmp_path / "datasets").mkdir()
    point_cache(run_script.repo, tmp_path / "datasets")
    code, out, fake, _ = run_script("pass", "--with-cache", "py312-distro")
    assert code == 0 and ".cache mounted read only" in out
    created = json.loads((fake.dir / "created").read_text(encoding="utf-8"))
    target = f"{ci.WORKSPACE}/.cache"
    assert mounts_of(created) == [
        UV_VOLUME.format(job="py312-distro"),
        f"type=bind,source={(tmp_path / 'datasets').resolve()},target={target},readonly",
    ]


def test_a_refused_cache_starts_nothing(run_script) -> None:
    point_cache(run_script.repo, run_script.repo / "data" / "uploads")
    code, out, fake, _ = run_script("pass", "--with-cache", "py312-distro")
    assert code == ci.EXIT_USAGE and "refused: --with-cache" in out
    assert not {"buildx", "create", "start"} & set(fake.commands())
    # The engine's own start command then mounts nothing at all
    command = ci.engine_command(run_script.repo, OWNERS)
    assert command[command.index("--mount") + 1] == "none"


def test_an_unreachable_engine_says_how_to_start_and_stop_it_and_starts_nothing(run_script) -> None:
    code, out, fake, _ = run_script("no-engine", "py312-distro")
    assert code == ci.EXIT_SETUP
    assert "colima start albedian-ci" in out and "colima stop albedian-ci" in out
    assert fake.commands() == ["version"]
    assert all(json.loads(line)["DOCKER_HOST"].endswith("/.colima/albedian-ci/docker.sock")
               for line in (fake.dir / "calls.jsonl").read_text(encoding="utf-8").splitlines())


def test_a_job_whose_exit_status_cannot_be_read_is_a_setup_failure_not_a_pass(run_script) -> None:
    code, out, fake, _ = run_script("inspect-fails", "py312-distro")
    assert code == ci.EXIT_SETUP and "could not read the job's exit status" in out
    assert fake.commands()[-1] == "rm"


@pytest.mark.parametrize("mode", ["build-fails", "build-fails-with-id"])
def test_a_failed_build_starts_no_container(run_script, mode: str) -> None:
    code, out, fake, _ = run_script(mode, "py314-pinned")
    assert code == ci.EXIT_SETUP and "the build broke" in out
    assert "create" not in fake.commands()


def test_an_unknown_job_and_an_unknown_commit_are_refused(run_script) -> None:
    code, out, fake, _ = run_script("pass", "py313-distro")
    assert code == ci.EXIT_USAGE and "py312-distro, py314-pinned" in out
    code, out, fake, _ = run_script("pass", "--rev", "f" * 40, "py312-distro")
    assert code == ci.EXIT_USAGE
    assert fake.commands() == []


def hold_engine_lock(path: Path, seconds: float, kind: int = fcntl.LOCK_EX,
                     record: str = "py312-distro on another checkout (process 1)",
                     stop: threading.Event | None = None) -> threading.Thread:
    """Holds a lane's lock from another open file for `seconds` (or until `stop` is set), as another run would,
    with its record."""
    held = threading.Event()
    stop = stop or threading.Event()
    released: list[float] = []

    def hold() -> None:
        with open(path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock, kind)
            lock.seek(0)
            lock.truncate()
            lock.write(record + "\n")
            lock.flush()
            held.set()
            stop.wait(seconds)
            released.append(time.time())
            fcntl.flock(lock, fcntl.LOCK_UN)

    thread = threading.Thread(target=hold, daemon=True)
    thread.released = released  # when it let go
    thread.start()
    assert held.wait(CHILD_BOUND_SEC), "the lock was not taken"
    return thread


def said(monkeypatch: pytest.MonkeyPatch, text: str, times: int = 1) -> threading.Event:
    """An event set once the script has said a line holding `text`, `times` times. A held lock is let go on it and
    not after a fixed time, so a run that reaches the lock late, on a loaded machine, still finds it held."""
    event = threading.Event()
    count = [0]
    original = ci.Run.say

    def say(self, line: str) -> None:
        original(self, line)
        if text in line:
            count[0] += 1
            if count[0] >= times:
                event.set()

    monkeypatch.setattr(ci.Run, "say", say)
    return event


@pytest.mark.parametrize("kind", [fcntl.LOCK_EX, fcntl.LOCK_SH], ids=["held alone", "held shared"])
def test_a_held_engine_waits_in_the_open_and_the_wait_never_counts_against_the_bound(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: int
) -> None:
    monkeypatch.setattr(ci, "LOCK_REPORT_SEC", 1)
    stop = said(monkeypatch, "waiting for the engine's whole-job lane", times=2)  # a wait of a second at least
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, kind, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "--bound", "1", "py314-pinned")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    released_after = holder.released[0]
    assert code == 0, "a wait of a second or more counted against a bound of one"
    assert out.count("ci_linux: waiting for the engine's whole-job lane, which runs one at a time") >= 2
    assert "it holds: py312-distro on another checkout (process 1)" in out
    times = {json.loads(line)["args"][0]: json.loads(line)["t"]
             for line in reversed((fake.dir / "calls.jsonl").read_text(encoding="utf-8").splitlines())}
    assert times["version"] < released_after < times["buildx"] < times["create"]
    assert (fake.dir / "lock_during_start").read_text(encoding="utf-8") == "whole=held short=free"


def test_the_engine_lock_is_held_through_the_job_and_given_back_after_the_container_is_removed(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kept alive here, so only the run's own release can free the lock (not the garbage collector closing its file)
    kept: list = []

    class KeptLock(ci.EngineLock):
        def __init__(self, path: Path, lane: str = "whole-job"):
            super().__init__(path, lane)
            kept.append(self)

    monkeypatch.setattr(ci, "EngineLock", KeptLock)
    code, out, fake, _ = run_script("fail", "py312-distro")
    assert code == 1 and "waiting for the engine" not in out and len(kept) == 1
    assert (fake.dir / "lock_during_start").read_text(encoding="utf-8") == "whole=held short=free"
    assert (fake.dir / "lock_during_rm").read_text(encoding="utf-8") == "whole=held short=free", "given back too early"
    record = json.loads((fake.dir / "lock_holder_whole").read_text(encoding="utf-8"))
    assert record["run"].startswith("py312-distro on the working tree at ")
    assert f"(process {os.getpid()}), since " in record["run"]
    assert record["bound"] == PYTHON_JOB_LIMIT_SEC and abs(record["bound_started"] - time.time()) < 120, "the bound's start is recorded"
    # While it builds and copies, before its container starts, the record says so to the runs behind it
    early = json.loads((fake.dir / "lock_holder_whole_at_create").read_text(encoding="utf-8"))
    assert early["run"] == record["run"] and early["bound"] == PYTHON_JOB_LIMIT_SEC and early["bound_started"] is None
    assert list((tmp_path / "engine.lock.queue").iterdir()) == [], "the run's own ticket is gone once it holds the lock"
    assert fake.commands()[-1] == "rm"
    with open(tmp_path / "engine.lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the run kept it
        fcntl.flock(lock, fcntl.LOCK_UN)


@pytest.mark.parametrize(
    "pytest_args,bound,lane",
    [
        (["tests/x.py"], ci.TESTS_BOUND_SEC, "short"),
        (["tests/x.py"], ci.TESTS_BOUND_SEC + 1, "whole-job"),
        (["tests/x.py"], ci.BOUND_MIN_SEC, "short"),
        ([], ci.TESTS_BOUND_SEC, "whole-job"),
        (["-x"], ci.BOUND_MIN_SEC, "whole-job"),
        (["tests/x.py::test_one"], 60, "short"),
    ],
)
def test_named_tests_within_their_bound_go_in_the_short_lane_and_everything_else_in_the_whole_job_lane(
    pytest_args: list[str], bound: int, lane: str
) -> None:
    assert ci.lane_of(pytest_args, bound) == lane


def test_a_short_run_never_waits_behind_a_whole_job(run_script, tmp_path: Path) -> None:
    stop = threading.Event()  # held until the short run has ended
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro", "tests/x.py")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == 0 and "waiting for the engine" not in out
    assert "the engine's short lane is this run's" in out
    assert (fake.dir / "lock_during_start").read_text(encoding="utf-8") == "whole=held short=held"
    record = json.loads((fake.dir / "lock_holder_short").read_text(encoding="utf-8"))
    assert record["bound"] == ci.TESTS_BOUND_SEC


def test_a_short_run_waits_behind_another_short_run(run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci, "LOCK_REPORT_SEC", 1)
    stop = said(monkeypatch, "waiting for the engine's short lane")
    holder = hold_engine_lock(tmp_path / "engine.short.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py314-pinned", "tests/x.py")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == 0 and "waiting for the engine's short lane, which runs one at a time" in out
    assert (fake.dir / "lock_during_start").read_text(encoding="utf-8") == "whole=free short=held"


def test_named_tests_with_a_longer_bound_wait_in_the_whole_job_lane(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = said(monkeypatch, "waiting for the engine's whole-job lane")
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "--bound", "601", "py312-distro", "tests/x.py")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == 0 and "waiting for the engine's whole-job lane" in out


def test_the_waiting_line_says_how_much_of_the_holders_bound_is_left(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.time() - 400
    record = json.dumps({"run": "py312-distro on commit abc from a lane (process 7), since 01:00:00",
                         "bound": 2400, "bound_started": started})
    stop = said(monkeypatch, "waiting for the engine's whole-job lane")
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, record=record, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    left = int(re.search(r"it holds: py312-distro on commit abc .*, bound 2400 s, (\d+) s of it left", out).group(1))
    assert 1900 <= left <= 2000


@pytest.mark.parametrize(
    "text,said",
    [
        ('{"run": "r", "bound": 600, "bound_started": 1000.0}', "r, bound 600 s, 500 s of it left"),
        ('{"run": "r", "bound": 600, "bound_started": 100.0}', "r, bound 600 s, 0 s of it left"),
        ('{"run": "r", "bound": 600, "bound_started": null}', "r, bound 600 s, not started yet (it is building or copying)"),
        ("py312-distro on commit abc (process 7, since 01:39:11)\n", "py312-distro on commit abc (process 7, since 01:39:11)"),
        ('{"run": "r"}', '{"run": "r"}'),
        ("", "a run that has not said which"),
    ],
    ids=["mid bound", "bound passed", "not started", "an older copy's line", "a record missing its bound", "empty"],
)
def test_the_holders_record_is_read_whatever_wrote_it(text: str, said: str) -> None:
    assert ci.holder_line(text, now=1100.0) == said


def write_ticket(queue: Path, asked_ns: int, pid: int, name: str | None = None) -> Path:
    """A ticket no run holds: what a run that died leaves, whatever process now has its id."""
    queue.mkdir(exist_ok=True)
    ticket = queue / (name or f"{asked_ns:020d}.{pid}")
    ticket.write_text("another run, waiting\n", encoding="utf-8")
    return ticket


def hold_ticket(ticket: Path, seconds: float, stop: threading.Event | None = None) -> threading.Thread:
    """Holds the lock on a ticket for `seconds` (or until `stop` is set) and then takes the ticket away, as a live
    run in line does."""
    held = threading.Event()
    stop = stop or threading.Event()
    released: list[float] = []

    def hold() -> None:
        with open(ticket, "r", encoding="utf-8") as own:
            fcntl.flock(own, fcntl.LOCK_EX)
            held.set()
            stop.wait(seconds)
            released.append(time.time())
            ticket.unlink(missing_ok=True)

    thread = threading.Thread(target=hold, daemon=True)
    thread.released = released  # when it let go
    thread.start()
    assert held.wait(CHILD_BOUND_SEC), "the ticket's lock was not taken"
    return thread


def test_runs_take_the_engine_in_the_order_they_asked(run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci, "LOCK_REPORT_SEC", 1)
    # An earlier run in line, holding its ticket, holds no lane yet: the run still waits its turn
    earlier = write_ticket(tmp_path / "engine.lock.queue", 1, os.getpid())
    stop = said(monkeypatch, "1 ahead in line")
    holder = hold_ticket(earlier, CHILD_BOUND_SEC, stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    released_after = holder.released[0]
    assert code == 0 and "1 ahead in line" in out
    times = {json.loads(line)["args"][0]: json.loads(line)["t"]
             for line in reversed((fake.dir / "calls.jsonl").read_text(encoding="utf-8").splitlines())}
    assert times["buildx"] > released_after
    assert list((tmp_path / "engine.lock.queue").iterdir()) == []


@pytest.mark.parametrize(
    "pid",
    [2**31 - 1, 1, os.getpid()],
    ids=["no process has the id", "process 1", "a live process of another run took the id"],
)
def test_a_ticket_no_run_holds_is_cleared_whatever_process_has_its_id(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid: int
) -> None:
    # A limit of its own: a run that took such a ticket for a live one fails by an assertion, it does not wait
    monkeypatch.setattr(ci, "LOCK_WAIT_LIMIT_SEC", 5)
    queue = tmp_path / "engine.lock.queue"
    dead = write_ticket(queue, 1, pid)
    making = write_ticket(queue, 0, pid, name=f".making.2.{pid}")  # begun and never put in line
    long_ago = time.time() - ci.MAKING_GRACE_SEC - 1
    os.utime(making, (long_ago, long_ago))
    later = write_ticket(queue, 10**19, os.getpid())  # asked after this run, and held
    stop = threading.Event()
    holder = hold_ticket(later, CHILD_BOUND_SEC, stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
        assert code == 0 and "waiting for the engine" not in out
        assert not dead.exists() and not making.exists() and later.exists()
        assert sorted(queue.iterdir()) == [later]
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)


def test_a_ticket_still_being_made_is_never_taken_for_a_dead_runs(run_script, tmp_path: Path) -> None:
    # A run opens its ticket and then locks it: between the two another run sees it free, and must leave it be
    queue = tmp_path / "engine.lock.queue"
    making = write_ticket(queue, 0, os.getpid(), name=f".making.{time.time_ns()}.{os.getpid()}")
    code, out, _, _ = run_script("pass", "py312-distro")
    assert code == 0 and "waiting for the engine" not in out, "a ticket being made holds nobody back"
    assert making.exists(), "a ticket being made is left for its run to lock and put in line"


@pytest.mark.parametrize(
    "age,held,cleared",
    [(59.5, False, False), (60.0, False, True), (60.5, False, True), (10**6, True, False), (0.0, False, False)],
    ids=["just under the grace", "at the grace", "past the grace", "long ago but held", "just begun"],
)
def test_a_ticket_begun_and_never_put_in_line_is_cleared_once_60_s_old_and_free(
    tmp_path: Path, age: float, held: bool, cleared: bool
) -> None:
    ticket = write_ticket(tmp_path / "queue", 0, os.getpid(), name=".making.1.2")
    begun = 1_000_000
    os.utime(ticket, (begun, begun))
    stop = threading.Event()
    holder = hold_ticket(ticket, CHILD_BOUND_SEC, stop) if held else None
    try:
        assert ci._abandoned(ticket, begun + age) is cleared
    finally:
        stop.set()
        if holder is not None:
            holder.join(CHILD_BOUND_SEC)
    assert ci._abandoned(tmp_path / "queue" / ".making.gone", begun + 10**6) is False, "a ticket already gone"


def run_apart(repo: Path, home: Path, fake: Fake, *args: str) -> subprocess.Popen[str]:
    """The script in a process of its own, its lanes under `home`, so it can be sent a signal."""
    (home / ".colima" / ci.PROFILE).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home), "FAKE_DOCKER_DIR": str(fake.dir), "FAKE_DOCKER_MODE": fake.mode,
           "FAKE_DOCKER_LOCK": str(home / ".colima" / ci.PROFILE / "ci_linux.lock"),
           "FAKE_DOCKER_SHORT_LOCK": str(home / ".colima" / ci.PROFILE / "ci_linux.short.lock")}
    return subprocess.Popen([sys.executable, str(SCRIPT), "--docker", fake.command, *args], cwd=repo, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)


def test_a_waiting_runs_ticket_holds_its_place_against_a_later_run(
    run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One run waits in a process of its own behind a held lane; a later run in this process sees it ahead in line
    home = tmp_path / "home"
    (home / ".colima" / ci.PROFILE).mkdir(parents=True)
    lock = home / ".colima" / ci.PROFILE / "ci_linux.lock"
    monkeypatch.setattr(ci, "LOCK_PATH", lock)
    monkeypatch.setattr(ci, "LOCK_WAIT_LIMIT_SEC", 3)
    stop = threading.Event()
    holder = hold_engine_lock(lock, CHILD_BOUND_SEC, stop=stop)
    queue = home / ".colima" / ci.PROFILE / "ci_linux.lock.queue"
    first = run_apart(run_script.repo, home, Fake(tmp_path, "pass"), "py312-distro")
    try:
        wait_for(lambda: queue.is_dir() and any(not p.name.startswith(".") for p in queue.iterdir()), "the first ticket")
        code, out, fake, _ = run_script("pass", "py312-distro")
        assert code == ci.EXIT_SETUP and "1 ahead in line" in out, out
        assert first.poll() is None and any(not p.name.startswith(".") for p in queue.iterdir()), "its ticket stayed"
    finally:
        first.send_signal(signal.SIGTERM)
        first.communicate(timeout=CHILD_BOUND_SEC)
        stop.set()
        holder.join(CHILD_BOUND_SEC)


def test_a_hangup_while_waiting_takes_the_runs_ticket_away(run_script, tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".colima" / ci.PROFILE).mkdir(parents=True)
    stop = threading.Event()
    holder = hold_engine_lock(home / ".colima" / ci.PROFILE / "ci_linux.lock", CHILD_BOUND_SEC, stop=stop)
    queue = home / ".colima" / ci.PROFILE / "ci_linux.lock.queue"
    child = run_apart(run_script.repo, home, Fake(tmp_path, "pass"), "py312-distro")
    try:
        wait_for(lambda: queue.is_dir() and any(not p.name.startswith(".") for p in queue.iterdir()), "the run's ticket")
        child.send_signal(signal.SIGHUP)
        out, _ = child.communicate(timeout=CHILD_BOUND_SEC)
        assert child.returncode == 128 + signal.SIGHUP, out
        assert list(queue.iterdir()) == [], "its ticket went with it"
        assert holder.is_alive(), "the lane's holder was never touched"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=CHILD_BOUND_SEC)
        stop.set()
        holder.join(CHILD_BOUND_SEC)


def test_a_hangup_while_the_job_runs_removes_its_container_by_id(run_script, tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake = Fake(tmp_path, "hang")
    child = run_apart(run_script.repo, home, fake, "py314-pinned", "tests/x.py")
    try:
        wait_for(lambda: (fake.dir / "lock_during_start").is_file(), "the job's start")
        child.send_signal(signal.SIGHUP)
        out, _ = child.communicate(timeout=CHILD_BOUND_SEC)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=CHILD_BOUND_SEC)
    assert child.returncode == 128 + signal.SIGHUP, out
    container = "c0ffee" + "0123456789abcdef" * 3 + "0123456789"
    assert fake.calls()[-1] == ["rm", "--force", container]


def test_an_engine_busy_past_the_wait_limit_starts_nothing(run_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci, "LOCK_WAIT_LIMIT_SEC", 1)
    stop = threading.Event()  # held until the run has given up
    holder = hold_engine_lock(tmp_path / "engine.lock", CHILD_BOUND_SEC, stop=stop)
    try:
        code, out, fake, _ = run_script("pass", "py312-distro")
    finally:
        stop.set()
        holder.join(CHILD_BOUND_SEC)
    assert code == ci.EXIT_SETUP and "the engine stayed busy" in out
    assert fake.commands() == ["version"]
    # At once, then every LOCK_REPORT_SEC (60 s): within one second, the one line said at once
    assert out.count("ci_linux: waiting for the engine") == 1 and "0 s so far, 0 ahead in line" in out
    assert list((tmp_path / "engine.lock.queue").iterdir()) == [], "a run that gives up takes its ticket with it"


def test_the_sigterm_handler_is_given_back(run_script) -> None:
    # A handler of the test's own, so a handler another run left behind cannot pass for the one given back
    def sentinel(signum: int, frame: object) -> None:
        raise AssertionError("not called")

    original = signal.signal(signal.SIGTERM, sentinel)
    try:
        run_script("pass", "py312-distro")
        assert signal.getsignal(signal.SIGTERM) is sentinel
    finally:
        signal.signal(signal.SIGTERM, original)


# The watcher plugin, in a pytest of its own

# A run started in the background by a script (the shared queue's `&`) inherits SIGINT ignored, and so would the
# sample's pytest; inside the container pytest descends from docker-init and takes SIGINT as Python's default does
SAMPLE_CONFTEST = "import signal\nsignal.signal(signal.SIGINT, signal.default_int_handler)\n"
SAMPLE = '''
import os, time
from pathlib import Path, PurePosixPath

def test_one():
    pass

def test_two():
    release = Path(os.environ["SAMPLE_RELEASE"])
    deadline = time.monotonic() + 20
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
'''


def test_the_watcher_names_the_running_test_dumps_its_stack_and_lets_sigint_end_pytest(tmp_path: Path) -> None:
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    (sample / "conftest.py").write_text(SAMPLE_CONFTEST, encoding="utf-8")
    watch = tmp_path / "watch"
    env = {**os.environ, "PYTHONPATH": str(IMAGE_DIR), "CI_LINUX_WATCH_DIR": str(watch),
           "SAMPLE_RELEASE": str(tmp_path / "release")}
    child = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "ci_linux_watch", "-p", "no:cacheprovider", "-q", "test_sample.py"],
        cwd=sample, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    )
    try:
        running = watch / "running"
        wait_for(lambda: running.is_file() and running.read_text(encoding="utf-8").endswith("::test_two"),
                 "the running file naming test_two")
        assert (watch / "last").read_text(encoding="utf-8") == "test_sample.py::test_one"
        assert (watch / "pid").read_text(encoding="utf-8") == str(child.pid)
        child.send_signal(signal.SIGUSR1)
        wait_for(lambda: "test_two" in (watch / "stacks").read_text(encoding="utf-8"), "the stack dump")
        child.send_signal(signal.SIGINT)
        out, _ = child.communicate(timeout=CHILD_BOUND_SEC)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=CHILD_BOUND_SEC)
    assert child.returncode == 2, out.decode("utf-8", "replace")
    assert b"KeyboardInterrupt" in out


def test_the_watcher_says_no_test_is_running_between_tests_and_names_the_last(tmp_path: Path) -> None:
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "release").touch()
    watch = tmp_path / "watch"
    env = {**os.environ, "PYTHONPATH": str(IMAGE_DIR), "CI_LINUX_WATCH_DIR": str(watch),
           "SAMPLE_RELEASE": str(tmp_path / "release")}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "ci_linux_watch", "-p", "no:cacheprovider", "-q", "test_sample.py"],
        cwd=sample, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=CHILD_BOUND_SEC,
    )
    assert done.returncode == 0, done.stdout.decode("utf-8", "replace")
    assert (watch / "running").read_text(encoding="utf-8") == ""
    assert (watch / "last").read_text(encoding="utf-8") == "test_sample.py::test_two"
