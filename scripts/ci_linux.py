"""Runs CI's Python jobs (.github/workflows/gates.yml) in a Linux x86-64 container on this laptop, with a time bound.

Usage, from the root of any checkout of the repository:

    uv run --no-sync python scripts/ci_linux.py [options] <job> [pytest arguments ...]

<job> is one of the workflow's own Python jobs, named from its matrix: py312-distro (Python 3.12, Ubuntu's ffmpeg)
or py314-pinned (Python 3.14, the pinned FFmpeg-Builds static build). With no pytest arguments the whole job runs as
CI runs it; pytest arguments (test files, node ids, -x) are added to CI's own pytest line. A checkout that does not
hold this script yet can run it from another one, with its own folder as the working directory:

    uv run --no-sync python /path/to/a/checkout/with/scripts/ci_linux.py py314-pinned tests/media/test_clip_io.py

Options (before <job>):
  --rev REV        run a commit's files instead of the working tree's (tracked files plus new files git does not
                   ignore, as they are on disk)
  --bound SECONDS  the time bound for the container: by default the workflow's timeout-minutes for a whole job, and
                   600 seconds when the pytest arguments name tests [proposed: about five times what a unit's own
                   test files take on CI]; 1 to 21600
  --with-cache     mount the checkout's .cache (the public datasets) read only. Off by default, as on CI, where
                   every local_dataset test skips through its marker
  --docker CMD     the docker command line to call (default: docker)
  --log FILE       also write everything printed to FILE

The interpreters are CI's to the patch: py312-distro runs Ubuntu's own python3.12 package from the image's apt
state (CI's 3.12 job runs the runner image's /usr/bin/python3.12, Ubuntu's 3.12.3), and py314-pinned the build of
the workflow's Python version that the pinned uv downloads, as CI's 3.14 job does. Every run prints the version in
full after uv sync, with the distro package's own version, so a run that differs from CI's header says so.

What a run does:
1. Reads the workflow from the files it will run (the working tree, or --rev's tree), so the container follows
   that commit's CI: the uv version, the pinned ffmpeg address and sha256, each job's Python version and ffmpeg,
   and the timeout. It refuses when the workflow's Python job runs a command the container does not.
2. Builds scripts/ci_linux/Dockerfile for linux/amd64 (cached after the first build) with those values.
3. Creates a container from the image id, copies the files in as one tar, and starts it. Never copied, whatever
   git says: every folder of .gitignore's "Runtime & user media storage" block (the owner's media and the runtime's
   files: data/uploads, data/ingest and the rest, read from the checkout's .gitignore and the commit's), and .cache,
   however a path spells them: names are compared as this disk resolves them (letter case folded, one Unicode form).
   Every path left to copy is also asked of the disk itself, once before anything starts and again for each entry
   as it is read for the copy: a path whose folders or link target resolve, through any chain of links, to a
   left-out folder or anywhere inside one, a file that is the same file on disk as one inside one (a hard link), or
   a path that changed between its check and its read stops the run with a named fault (exit 2), and nothing is copied.
   The container sees nothing else of this laptop: no folder of it is mounted but .cache with --with-cache, read
   only, and never a folder that is, holds or sits inside one of those owner folders or a checkout. uv's download
   cache is a docker volume per job, kept on the engine's own disk (CI restores one too). The checkout is only read.
4. Streams the job's output. When the bound passes it names the test that was running (from the watcher plugin,
   scripts/ci_linux/ci_linux_watch.py), prints every thread's Python stack, interrupts pytest so it prints what
   failed so far, then stops the container by its id. The container is removed by its id when the run ends, and
   so when a SIGTERM or a hangup ends the run.

The engine is the colima profile albedian-ci (x86-64 through Rosetta), started by hand once; when it is not
running the script prints the command that starts it. Every docker call goes to that profile's socket, so the
current docker context (and Docker Desktop) is never used or changed.

Two lanes, one run in each: the engine has 4 processors and 8 GiB. A run that names tests within their 600 second
bound goes in the short lane (~/.colima/albedian-ci/ci_linux.short.lock); every other run, a whole job above all,
in the whole-job lane (~/.colima/albedian-ci/ci_linux.lock, the lock every earlier copy of this script takes). So a
short run never waits behind a whole job, and at most one of each kind runs at a time. A run takes its lane's lock
(an exclusive flock, which the system releases if the run dies) before it builds the image, and holds it until its
container is removed. Runs take a lane in the order they asked: each waits with a ticket in the lane's queue folder
(~/.colima/albedian-ci/ci_linux.lock.queue/ or ci_linux.short.lock.queue/, named by the time it asked and its
process id) and goes only when no live ticket is older. A run holds a lock on its own ticket while it waits, and a
ticket counts as live only while that lock is held, so a run that died holds nobody back, whatever has become of
its process id; a SIGTERM or a hangup takes its ticket away at once. A run that must wait prints, at once and then
every 60 seconds, `ci_linux: waiting for the engine's <lane> lane ...` with the seconds waited, how many are ahead
in line, and the run that holds it with its bound and how much of the bound is left. The wait comes before the
bound starts, so it never counts against it; after 21600 seconds of waiting the run gives up with exit 125.

Exit status: the job's own (pytest's) when it ends; 124 when the bound passed; 2 for a usage error or a refused
mount; 125 when the engine, the image build or the copy fails.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import IO, NoReturn
import unicodedata

SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_DIR = SCRIPT_DIR / "ci_linux"
WORKFLOW_PATH = ".github/workflows/gates.yml"
PROFILE = "albedian-ci"
WORKSPACE = "/home/runner/work/albedian/albedian"
WATCH_DIR = "/tmp/ci-linux"
UV_CACHE_DIR = "/home/runner/work/_temp/setup-uv-cache"
UV_CACHE_VOLUME = "albedian-ci-uv-cache"
# The runner account's uid and gid in the image (Dockerfile); the copied files belong to it, as a checkout does
RUNNER_ID = 1001
# The owner's folders are every folder of .gitignore's block that starts with this line (the runtime's files and the
# owner's media: the uploads, the ingest store, the artifacts, the renders), read from the ignore file itself so a
# folder added there is left out here with no change to this script. Never copied into the container, whatever git
# lists; the shared download cache is left out beside them.
MEDIA_BLOCK = "# Runtime & user media storage"
CACHE_FOLDER = ".cache"
# uv's x86_64 Linux archive per version: the release's own .sha256 file, the same value as setup-uv's
# known-checksums.json at the commit the workflow pins
UV_SHA256 = {"0.12.5": "68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2"}
# The single-line commands of the workflow's Python job, in order, that the image and job.bash run. The multi-line
# steps (the pinned download, the two ffprobe checks) are matched by tests/integration/test_ci_linux.py.
WORKFLOW_COMMANDS = (
    "sudo apt-get update && sudo apt-get install --yes --no-install-recommends ffmpeg",
    "ffmpeg -hide_banner -version && ffprobe -hide_banner -version",
    "uv sync --frozen",
    "uv run --frozen python scripts/verify_provenance.py",
    "uv run --frozen pytest -rs --no-unexpected-skips",
)
TESTS_BOUND_SEC = 600
BOUND_MIN_SEC = 1
BOUND_MAX_SEC = 6 * 3600
# [proposed] bounds of the steps around the job: the first build downloads about 200 MB and installs packages
BUILD_BOUND_SEC = 1800
COPY_BOUND_SEC = 300
CALL_BOUND_SEC = 60
# [proposed] after the interrupt, the time pytest has to print what failed so far before the container is stopped
INTERRUPT_GRACE_SEC = 30
STACKS_WAIT_SEC = 2
LOCK_PATH = Path.home() / ".colima" / PROFILE / "ci_linux.lock"
SHORT_LOCK_PATH = LOCK_PATH.with_name("ci_linux.short.lock")
LOCK_POLL_SEC = 1
LOCK_REPORT_SEC = 60
# [proposed] a run opens its ticket and then locks it, microseconds apart, and in between another run would find it
# free: a ticket still being made is cleared only once it is this old, far longer than the making and short beside
# any wait
MAKING_GRACE_SEC = 60
# [proposed] as long as the longest bound: a holder ends within its own bound and the interrupt's grace
LOCK_WAIT_LIMIT_SEC = 6 * 3600
EXIT_USAGE = 2
EXIT_BOUND = 124
EXIT_SETUP = 125


class Refused(Exception):
    """A run this script will not start; the message says why."""


class SetupFailed(Exception):
    """The engine, the image build or the copy failed; the message says which."""


@dataclass(frozen=True)
class Job:
    name: str
    python: str
    ffmpeg: str


@dataclass(frozen=True)
class Workflow:
    uv_version: str
    timeout_minutes: int
    jobs: tuple[Job, ...]
    ffmpeg_url: str
    ffmpeg_sha256: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class Entry:
    """One file of the snapshot: a regular file (with its mode and bytes) or a symbolic link (with its target)."""

    name: str
    mode: int
    data: bytes = b""
    link: str | None = None


def _one(pattern: str, text: str, what: str, flags: int = 0) -> re.Match[str]:
    found = re.search(pattern, text, flags)
    if found is None:
        raise Refused(f"the workflow's Python job has no {what}, so the container cannot follow it")
    return found


def parse_workflow(text: str) -> Workflow:
    """The values of the workflow's Python job that the container takes over."""
    start = text.find("\n  python:\n")
    if start < 0:
        raise Refused("the workflow has no job named python")
    following = re.search(r"\n  [A-Za-z0-9_-]+:\n", text[start + 1 :])
    job = text[start : start + 1 + following.start()] if following else text[start:]
    timeout = int(_one(r"^    timeout-minutes:\s*(\d+)\s*$", job, "timeout-minutes", re.MULTILINE).group(1))
    pairs = re.findall(r'-\s*python:\s*"(\d+\.\d+)"\s*\n\s*ffmpeg:\s*(\w+)\s*$', job, re.MULTILINE)
    if not pairs:
        raise Refused("the workflow's Python job has no matrix of Python and ffmpeg, so the container cannot follow it")
    jobs = tuple(Job(f"py{python.replace('.', '')}-{ffmpeg}", python, ffmpeg) for python, ffmpeg in pairs)
    setup_uv = job[_one(r"uses:\s*astral-sh/setup-uv@", job, "setup-uv step").start() :]
    uv_version = _one(r'^\s*version:\s*"([0-9]+(?:\.[0-9]+)*)"\s*$', setup_uv, "pinned uv version", re.MULTILINE).group(1)
    ffmpeg_url = _one(r"^\s*FFMPEG_URL:\s*(https://\S+)\s*$", job, "FFMPEG_URL", re.MULTILINE).group(1)
    ffmpeg_sha = _one(r"^\s*FFMPEG_SHA256:\s*([0-9a-f]{64})\s*$", job, "FFMPEG_SHA256", re.MULTILINE).group(1)
    commands = tuple(c.strip() for c in re.findall(r"^\s*(?:-\s+)?run:\s*(?!\|)(\S.*)$", job, re.MULTILINE))
    return Workflow(uv_version, timeout, jobs, ffmpeg_url, ffmpeg_sha, commands)


def check_workflow(workflow: Workflow) -> None:
    """Refuses a workflow whose Python job the container does not reproduce."""
    if workflow.commands != WORKFLOW_COMMANDS:
        raise Refused(
            "the workflow's Python job runs "
            + "; ".join(workflow.commands)
            + ", and the container runs "
            + "; ".join(WORKFLOW_COMMANDS)
            + ": update scripts/ci_linux/ (and WORKFLOW_COMMANDS) to follow it"
        )
    if workflow.uv_version not in UV_SHA256:
        raise Refused(
            f"the workflow pins uv {workflow.uv_version}, whose sha256 is not in UV_SHA256: add it from the release's "
            "uv-x86_64-unknown-linux-gnu.tar.gz.sha256 and setup-uv's known-checksums.json"
        )
    for job in workflow.jobs:
        if job.ffmpeg not in ("distro", "pinned"):
            raise Refused(f"the workflow's job {job.name} uses an ffmpeg the image does not build: {job.ffmpeg}")


def parse_bound(text: str) -> int:
    if not re.fullmatch(r"[0-9]+", text):
        raise argparse.ArgumentTypeError(f"the bound is a whole number of seconds, not {text!r}")
    value = int(text)
    if not BOUND_MIN_SEC <= value <= BOUND_MAX_SEC:
        raise argparse.ArgumentTypeError(f"the bound is {BOUND_MIN_SEC} to {BOUND_MAX_SEC} seconds, not {value}")
    return value


def names_tests(pytest_args: Sequence[str]) -> bool:
    """True when the pytest arguments name test files, folders or node ids rather than only options."""
    return any(arg == "tests" or arg.startswith("tests/") or "::" in arg for arg in pytest_args)


def default_bound(workflow: Workflow, pytest_args: Sequence[str]) -> int:
    return TESTS_BOUND_SEC if names_tests(pytest_args) else workflow.timeout_minutes * 60


def lane_of(pytest_args: Sequence[str], bound: int) -> str:
    """The engine's lane a run waits in: "short" for named tests within the tests' bound, else "whole-job"."""
    return "short" if names_tests(pytest_args) and bound <= TESTS_BOUND_SEC else "whole-job"


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="scripts/ci_linux.py",
        description="Run CI's Python jobs in a Linux x86-64 container, bounded (see the module docstring).",
    )
    found.add_argument("--rev", help="run this commit's files instead of the working tree's")
    found.add_argument("--bound", type=parse_bound, help="the time bound for the container, in seconds")
    found.add_argument("--with-cache", action="store_true", help="mount the checkout's .cache read only")
    found.add_argument("--docker", default="docker", help="the docker command line to call")
    found.add_argument("--log", type=Path, help="also write the output to this file")
    found.add_argument("job", help="py312-distro or py314-pinned (the workflow's Python jobs)")
    found.add_argument("pytest_args", nargs=argparse.REMAINDER, help="added to CI's pytest line")
    return found


# Variables that would point git at another repository than the checkout's folder: never passed on to git here
GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def git_environment() -> dict[str, str]:
    """The environment for git: this process's, without the variables that would point it elsewhere."""
    return {key: value for key, value in os.environ.items() if key not in GIT_REDIRECTS}


def git(root: Path, *args: str) -> bytes:
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=CALL_BOUND_SEC,
        env=git_environment(),
    )
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def checkout_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").decode("utf-8").strip())


def owner_folders(*ignore_texts: str) -> tuple[str, ...]:
    """Every folder (an entry ending in /) of the MEDIA_BLOCK block of each .gitignore text given, up to the block's
    first blank or comment line. Refused when no text holds the block, so the list never comes back empty."""
    folders: set[str] = set()
    found = False
    for text in ignore_texts:
        lines = text.splitlines()
        starts = [at for at, line in enumerate(lines) if line.strip().startswith(MEDIA_BLOCK)]
        if not starts:
            continue
        found = True
        for line in lines[starts[0] + 1 :]:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                break
            if entry.endswith("/"):
                folders.add(entry.strip("/"))
    if not found or not folders:
        raise Refused(f"no .gitignore here holds the block '{MEDIA_BLOCK}', so the owner's folders are not known")
    return tuple(sorted(folders))


def left_out_folders(root: Path, rev: str | None) -> tuple[str, ...]:
    """The owner's folders, from the checkout's own .gitignore and, with --rev, the commit's too, and the cache."""
    texts = [(root / ".gitignore").read_text(encoding="utf-8")] if (root / ".gitignore").is_file() else []
    if rev is not None:
        try:
            texts.append(git(root, "show", f"{rev}:.gitignore").decode("utf-8"))
        except Refused:
            pass  # a commit with no .gitignore: the checkout's own block still holds
    return (*owner_folders(*texts), CACHE_FOLDER)


def caseless(part: str) -> str:
    """A name as this laptop's disk resolves it: APFS, and git on it (core.ignorecase), do not tell letter case apart,
    and take a letter written composed or decomposed (é as one code point or as e and an accent) as the same. So the
    case is folded (str.casefold, Unicode's full folding) and one Unicode form taken (NFD): Unicode's canonical
    caseless match, NFD(casefold(NFD(name)))."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())


def is_left_out(name: str, left_out: Sequence[str]) -> bool:
    """True for a path in or under one of the folders `left_out`, compared part by part in their caseless forms, so
    data/Uploads or .RUNTIME names the folder this disk would. On a disk that tells case apart (Linux's) this leaves
    out more than that disk would, never less."""
    parts = [caseless(part) for part in PurePosixPath(name).parts]
    for folder in left_out:
        wanted = [caseless(part) for part in PurePosixPath(folder).parts]
        if parts[: len(wanted)] == wanted:
            return True
    return False


Seen = dict[str, tuple[str, int, int] | None]


class Judge:
    """Where each thing to copy really is, asked of the disk itself each time: a path whose folders resolve, through
    any chain of links, to a left-out folder or anywhere inside one (every ancestor of the real place is compared
    with each left-out folder by device and inode, so depth and spelling do not matter), a link whose target resolves
    there, and a file that is the same file on disk as one inside a left-out folder (a hard link). Each fault is
    Refused, exit 2, naming the left-out folder and the spelling of the path's folders, never a file in it."""

    def __init__(self, root: Path, left_out: Sequence[str]):
        self.root = root
        self.left_out = left_out
        self.folders: dict[tuple[int, int], str] = {}
        for folder in left_out:
            try:
                info = os.stat(root / folder)
            except OSError:
                continue
            self.folders[(info.st_dev, info.st_ino)] = folder
        self._places: dict[str, str | None] = {}
        self._files: dict[tuple[int, int], str] | None = None

    def folder_holding(self, place: str) -> str | None:
        """The left-out folder that a real place (links already resolved) is or sits inside, at any depth."""
        here = Path(place)
        for step in (here, *here.parents):
            key = str(step)
            if key not in self._places:
                try:
                    info = os.stat(step)
                    self._places[key] = self.folders.get((info.st_dev, info.st_ino))
                except OSError:
                    self._places[key] = None
            if self._places[key] is not None:
                return self._places[key]
        return None

    def folders_of(self, name: str) -> None:
        """Refuses `name` when one of its folders really resolves inside a left-out folder, resolved now."""
        parts = PurePosixPath(name).parts
        for end in range(1, len(parts)):
            spelled = "/".join(parts[:end])
            found = self.folder_holding(os.path.realpath(self.root / spelled))
            if found is not None:
                raise Refused(f"a path to copy resolves on this disk inside the left-out folder {found} (as {spelled}); "
                              "nothing was copied")

    def link_target(self, name: str, target: str) -> None:
        """Refuses a link entry whose target resolves on this disk to a left-out folder or anywhere inside one."""
        parent = os.path.realpath(self.root / PurePosixPath(name).parent)
        resolved = os.path.realpath(os.path.join(parent, target))
        found = self.folder_holding(resolved)
        if found is not None:
            if os.path.isdir(resolved):
                raise Refused(f"a path to copy resolves on this disk inside the left-out folder {found} (as {name}); "
                              "nothing was copied")
            raise Refused(f"a link to copy resolves on this disk inside the left-out folder {found} "
                          f"(in {PurePosixPath(name).parent}); nothing was copied")

    def files(self) -> dict[tuple[int, int], str]:
        """Device and inode of each regular file inside a left-out folder, listed once, when a file with more than one
        link first asks (1841 files in 0.02 s on the main checkout, 1732 in 0.33 s at load 112 in a lane folder)."""
        if self._files is None:
            self._files = {}
            for folder in self.left_out:
                for here, _, names in os.walk(self.root / folder):
                    for each in names:
                        try:
                            info = os.lstat(os.path.join(here, each))
                        except OSError:
                            continue
                        if stat.S_ISREG(info.st_mode):
                            self._files[(info.st_dev, info.st_ino)] = folder
        return self._files

    def same_file(self, info: os.stat_result, name: str) -> None:
        """Refuses a regular file with more than one link that is the same file as one inside a left-out folder."""
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            found = self.files().get((info.st_dev, info.st_ino))
            if found is not None:
                raise Refused(f"a file to copy is the same file on disk as one inside the left-out folder {found} "
                              f"(in {PurePosixPath(name).parent}); nothing was copied")

    def disk_file(self, name: str) -> None:
        """A commit's file: the entry at its name on this disk, if there is one, may not link into a left-out folder."""
        candidate = self.root / name
        try:
            info = os.lstat(candidate)
        except OSError:
            return
        if stat.S_ISLNK(info.st_mode):
            self.link_target(name, os.readlink(candidate))
        else:
            self.same_file(info, name)

    def before_commit(self, blobs: Sequence[tuple[str, str, str]]) -> None:
        """A commit's listing, judged before the run waits for the engine; judged again as each blob is copied."""
        for mode, sha, name in blobs:
            if is_left_out(name, self.left_out):
                continue
            self.folders_of(name)
            self.disk_file(name)
            if mode == "120000":
                self.link_target(name, git(self.root, "cat-file", "blob", sha).decode("utf-8"))

    def snapshot_worktree(self, names: Sequence[str]) -> Seen:
        """The working tree's listing, judged before the run waits for the engine, with each entry's identity recorded
        so a change between the check and the read stops the run with a named fault."""
        seen: Seen = {}
        for name in names:
            if is_left_out(name, self.left_out):
                continue
            self.folders_of(name)
            candidate = self.root / name
            try:
                info = os.lstat(candidate)
            except FileNotFoundError:
                seen[name] = None
                continue
            if stat.S_ISLNK(info.st_mode):
                self.link_target(name, os.readlink(candidate))
                seen[name] = ("link", info.st_dev, info.st_ino)
            elif stat.S_ISREG(info.st_mode):
                self.same_file(info, name)
                seen[name] = ("reg", info.st_dev, info.st_ino)
        return seen


def _refuse_changed(name: str, detail: str = "") -> NoReturn:
    extra = f": {detail}" if detail else ""
    raise Refused(f"a path to copy changed between its check and its read (in {PurePosixPath(name).parent}){extra}; "
                  "nothing was copied")


def worktree_names(root: Path) -> list[str]:
    """The working tree's tracked files and the new files git does not ignore, by name."""
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode("utf-8")
    return sorted(set(listed.split("\0")) - {""})


def worktree_entries(
    root: Path, left_out: Sequence[str], names: Sequence[str] | None = None, seen: Seen | None = None
) -> Iterator[Entry]:
    """The working tree's tracked files and the new files git does not ignore, as they are on disk (`names`, when
    given, is the listing already made, and `seen` each entry's identity at that check). Each is judged as it is read:
    its folders before the entry is opened, its identity against `seen` when given, a regular file opened with
    O_NOFOLLOW and checked by fstat against lstat and against hard links into left-out folders, and a link entry's
    target; any fault stops the run before docker cp."""
    judge = Judge(root, left_out)
    for name in worktree_names(root) if names is None else names:
        if is_left_out(name, left_out):
            continue
        judge.folders_of(name)
        path = root / name
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if seen is not None and seen.get(name) is not None:
                _refuse_changed(name)
            continue  # deleted before the check: a commit of it would not hold it either
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            judge.link_target(name, target)
            if seen is not None and name in seen:
                before = seen[name]
                if before is None or before[0] != "link" or (info.st_dev, info.st_ino) != (before[1], before[2]):
                    _refuse_changed(name)
            yield Entry(name, 0o777, link=target)
        elif stat.S_ISREG(info.st_mode):
            try:
                handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError as error:
                _refuse_changed(name, error.strerror or "could not open")
            with os.fdopen(handle, "rb") as opened:
                found = os.fstat(opened.fileno())
                if not stat.S_ISREG(found.st_mode) or (found.st_dev, found.st_ino) != (info.st_dev, info.st_ino):
                    _refuse_changed(name)
                judge.same_file(found, name)
                if seen is not None and name in seen:
                    before = seen[name]
                    if before is None or before[0] != "reg" or (found.st_dev, found.st_ino) != (before[1], before[2]):
                        _refuse_changed(name)
                yield Entry(name, 0o755 if found.st_mode & 0o100 else 0o644, opened.read())


def rev_blobs(root: Path, rev: str) -> list[tuple[str, str, str]]:
    """One commit's files as (mode, object, name), from its tree."""
    listing = git(root, "ls-tree", "-r", "-z", "--full-tree", f"{rev}^{{commit}}")
    blobs: list[tuple[str, str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, raw_name = record.split(b"\t", 1)
        mode, kind, sha = meta.decode("ascii").split()
        if kind == "blob":
            blobs.append((mode, sha, raw_name.decode("utf-8")))
    return blobs


def rev_entries(
    root: Path, rev: str, left_out: Sequence[str], listed: Sequence[tuple[str, str, str]] | None = None
) -> Iterator[Entry]:
    """The files of one commit, read from git's objects (no checkout, no archive attributes); `listed`, when given,
    is the listing already made, so what is copied is what was checked."""
    blobs = [blob for blob in (rev_blobs(root, rev) if listed is None else listed) if not is_left_out(blob[2], left_out)]
    judge = Judge(root, left_out)
    reader = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=git_environment()
    )
    assert reader.stdin is not None and reader.stdout is not None
    try:
        for mode, sha, name in blobs:
            judge.folders_of(name)
            judge.disk_file(name)
            reader.stdin.write(f"{sha}\n".encode("ascii"))
            reader.stdin.flush()
            header = reader.stdout.readline().decode("ascii").split()
            if len(header) != 3 or header[1] != "blob":
                raise Refused(f"git cat-file could not read {name}")
            data = reader.stdout.read(int(header[2]))
            reader.stdout.read(1)
            if mode == "120000":
                target = data.decode("utf-8")
                judge.link_target(name, target)
                yield Entry(name, 0o777, link=target)
            else:
                yield Entry(name, 0o755 if mode == "100755" else 0o644, data)
    finally:
        reader.stdin.close()
        try:
            reader.wait(timeout=CALL_BOUND_SEC)
        except subprocess.TimeoutExpired:
            reader.kill()
            reader.wait(timeout=CALL_BOUND_SEC)


def write_tar(entries: Iterator[Entry], out: IO[bytes], mtime: float) -> int:
    """Writes the files, and a folder entry for each of their folders, all owned by the runner account."""
    count = 0
    folders: set[str] = set()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as archive:

        def add(info: tarfile.TarInfo, data: bytes | None = None) -> None:
            info.uid = info.gid = RUNNER_ID
            info.uname = info.gname = "runner"
            info.mtime = int(mtime)
            archive.addfile(info, None if data is None else _Reader(data))

        for entry in entries:
            parents = PurePosixPath(entry.name).parents
            for folder in reversed([str(p) for p in parents if str(p) != "."]):
                if folder not in folders:
                    folders.add(folder)
                    info = tarfile.TarInfo(folder)
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    add(info)
            info = tarfile.TarInfo(entry.name)
            info.mode = entry.mode
            if entry.link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, entry.link
                add(info)
            else:
                info.size = len(entry.data)
                add(info, entry.data)
            count += 1
    return count


class _Reader:
    def __init__(self, data: bytes):
        self._data, self._at = data, 0

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._at + size
        chunk, self._at = self._data[self._at : end], min(end, len(self._data))
        return chunk


def main_checkout(root: Path) -> Path:
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode("utf-8").strip())
    return common.parent


def cache_source(root: Path, owners: Sequence[str]) -> Path:
    """The real folder .cache points to, refused when it is, holds or sits inside one of the owner's folders
    `owners` (of this checkout or of the main checkout it belongs to) or a checkout."""
    link = root / CACHE_FOLDER
    try:
        source = link.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as error:
        raise Refused(f"--with-cache: {link} does not lead to a folder ({error})") from None
    if not source.is_dir():
        raise Refused(f"--with-cache: {link} leads to {source}, which is not a folder")
    checkouts = {root.resolve(), main_checkout(root).resolve()}
    for checkout in checkouts:
        if checkout == source or checkout.is_relative_to(source):
            raise Refused(f"--with-cache: {source} holds the checkout {checkout}, and with it the owner's folders")
        for folder in owners:
            owner = (checkout / folder).resolve()
            if owner == source or owner.is_relative_to(source) or source.is_relative_to(owner):
                raise Refused(f"--with-cache: {source} is, holds or sits inside the owner's folder {owner}")
    return source


def engine_command(root: Path, owners: Sequence[str]) -> list[str]:
    """The colima command that starts this script's engine, with .cache (if any) as its only folder, read only."""
    command = ["colima", "start", PROFILE, "--runtime", "docker", "--vm-type", "vz", "--vz-rosetta"]
    command += ["--arch", "aarch64", "--cpus", "4", "--memory", "8", "--disk", "40"]
    try:
        command += ["--mount", str(cache_source(root, owners))]
    except Refused:
        command += ["--mount", "none"]
    return command + ["--activate=false", "--ssh-config=false", "--template=false"]


def _held(ticket: Path) -> bool:
    """True while the run that made the ticket still holds the lock on it. The system frees that lock when the run
    dies, however it dies, so a ticket proves its own run whatever has become of its process id."""
    try:
        with open(ticket, "r", encoding="utf-8") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(other, fcntl.LOCK_UN)
            return False
    except FileNotFoundError:
        return False


def _abandoned(ticket: Path, now: float) -> bool:
    """True for a ticket begun at least MAKING_GRACE_SEC before `now` that no run holds: its run died before it put
    the ticket in line. A younger one may be a live run's, between making it and locking it."""
    try:
        begun = ticket.stat().st_mtime
    except FileNotFoundError:
        return False
    return now - begun >= MAKING_GRACE_SEC and not _held(ticket)


def holder_line(text: str, now: float) -> str:
    """What a waiting run says of the holder: its run, its bound and how much of the bound is left, read from the
    holder's record; a holder that wrote no record (an older copy of this script) is quoted as it wrote."""
    try:
        record = json.loads(text)
        run, bound, started = record["run"], int(record["bound"]), record["bound_started"]
    except (ValueError, KeyError, TypeError):
        return text.strip() or "a run that has not said which"
    if started is None:
        return f"{run}, bound {bound} s, not started yet (it is building or copying)"
    left = max(0, round(bound - (now - float(started))))
    return f"{run}, bound {bound} s, {left} s of it left"


class EngineLock:
    """One of the engine's two lanes: one run at a time in it, in the order the runs asked, waited for in the open
    (see the module docstring). Each waiting run keeps a ticket in the lane's queue folder, named by the time it
    asked and its process id, and holds a lock on it while it waits; it takes the lane only when no live ticket is
    older than its own. A ticket counts as live only while its lock is held, and one that is not is removed by the
    next run that looks. The holder keeps a record in the lock file (its run, its bound and when the bound started)
    so the runs behind it can say how long it has left."""

    def __init__(self, path: Path, lane: str = "whole-job"):
        self.path = path
        self.lane = lane
        self.queue = path.with_name(path.name + ".queue")
        self._file: IO[str] | None = None
        self._record: dict[str, object] = {}

    def _write_record(self) -> None:
        assert self._file is not None
        self._file.seek(0)
        self._file.truncate()
        self._file.write(json.dumps(self._record) + "\n")
        self._file.flush()

    def bound_started(self) -> None:
        """Called as the container starts: the bound the waiting runs count down from begins now."""
        if self._file is not None:
            self._record["bound_started"] = time.time()
            self._write_record()

    def ahead(self, ticket: Path) -> list[Path]:
        """The live tickets older than `ticket`; the others are removed on the way, and so is a ticket a run began
        to make and never put in line (a name that starts with a dot) once it is abandoned (see _abandoned)."""
        older = []
        for other in sorted(self.queue.iterdir()):
            if other.name.startswith("."):
                if _abandoned(other, time.time()):
                    other.unlink(missing_ok=True)
                continue
            if other.name >= ticket.name:
                continue
            if _held(other):
                older.append(other)
            else:
                other.unlink(missing_ok=True)
        return older

    def acquire(self, run: Run, holder: str, bound: int) -> float:
        """Takes the lock in its turn, saying while it waits; the seconds it waited."""
        started = time.monotonic()
        self.queue.mkdir(exist_ok=True)
        asked = time.time_ns()
        # Locked before it is in line, so no run ever sees it unlocked and takes it for a dead run's
        making = self.queue / f".making.{asked}.{os.getpid()}"
        ticket_file = open(making, "w", encoding="utf-8")  # noqa: SIM115 - held until the run leaves the line
        fcntl.flock(ticket_file, fcntl.LOCK_EX)
        ticket_file.write(f"{holder}, waiting since {time.strftime('%H:%M:%S')}\n")
        ticket_file.flush()
        ticket = self.queue / f"{asked:020d}.{os.getpid()}"
        os.rename(making, ticket)
        self._file = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 - held until release()
        reported: float | None = None
        try:
            while True:
                waiting = self.ahead(ticket)
                if not waiting:
                    try:
                        fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        pass
                waited = time.monotonic() - started
                if waited >= LOCK_WAIT_LIMIT_SEC:
                    self.release()
                    raise SetupFailed(f"the engine stayed busy for {waited:.0f} s; nothing was started")
                if reported is None or time.monotonic() - reported >= LOCK_REPORT_SEC:
                    self._file.seek(0)
                    other = holder_line(self._file.read(), time.time())
                    run.say(f"ci_linux: waiting for the engine's {self.lane} lane, which runs one at a time: "
                            f"{waited:.0f} s so far, {len(waiting)} ahead in line; it holds: {other}")
                    reported = time.monotonic()
                time.sleep(LOCK_POLL_SEC)
        finally:
            ticket.unlink(missing_ok=True)
            ticket_file.close()
        self._record = {"run": f"{holder}, since {time.strftime('%H:%M:%S')}", "bound": bound, "bound_started": None}
        self._write_record()
        return time.monotonic() - started

    def release(self) -> None:
        if self._file is not None:
            try:
                fcntl.flock(self._file, fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


class Run:
    """One run: its docker command line, its output (printed, and to --log), the container it created and the
    engine's lock it holds."""

    def __init__(self, docker: Sequence[str], log: IO[str] | None):
        self.docker = list(docker)
        self.log = log
        self.container: str | None = None
        self.engine_lock: EngineLock | None = None
        self.last_step = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text)
            sys.stdout.flush()
            if self.log is not None:
                self.log.write(text)
                self.log.flush()

    def say(self, text: str) -> None:
        self.write(text if text.endswith("\n") else text + "\n")

    def call(
        self, *args: str, timeout: float = CALL_BOUND_SEC, stdin: IO[bytes] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                [*self.docker, *args],
                capture_output=True,
                stdin=stdin if stdin is not None else subprocess.DEVNULL,
                timeout=timeout,
                env=docker_env(),
            )
        except subprocess.TimeoutExpired:
            raise SetupFailed(f"docker {args[0]} did not end within {timeout:.0f} s") from None
        except FileNotFoundError:
            raise SetupFailed(f"the docker command line {self.docker[0]} was not found") from None

    def read_watch(self, name: str) -> str:
        assert self.container is not None
        try:
            done = self.call("exec", self.container, "cat", f"{WATCH_DIR}/{name}")
        except SetupFailed:
            return ""
        return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""

    def signal_pytest(self, pid: str, name: str) -> None:
        assert self.container is not None
        try:
            self.call("exec", self.container, "kill", f"-{name}", pid)
        except SetupFailed:
            pass

    def stop(self) -> None:
        """Removes the container by its id, then gives the engine's lock back."""
        try:
            if self.container is not None:
                try:
                    self.call("rm", "--force", self.container)
                except SetupFailed as error:
                    self.say(f"ci_linux: could not remove container {self.container}: {error}")
        finally:
            if self.engine_lock is not None:
                self.engine_lock.release()


def docker_env() -> dict[str, str]:
    env = dict(os.environ)
    env["DOCKER_HOST"] = f"unix://{Path.home() / '.colima' / PROFILE / 'docker.sock'}"
    env.pop("DOCKER_CONTEXT", None)
    return env


def build_image(run: Run, workflow: Workflow, job: Job) -> str:
    args = ["buildx", "build", "--builder", "default", "--platform", "linux/amd64", "--target", job.ffmpeg]
    args += ["--build-arg", f"UV_VERSION={workflow.uv_version}"]
    args += ["--build-arg", f"UV_SHA256={UV_SHA256[workflow.uv_version]}"]
    if job.ffmpeg == "pinned":
        args += ["--build-arg", f"FFMPEG_URL={workflow.ffmpeg_url}"]
        args += ["--build-arg", f"FFMPEG_SHA256={workflow.ffmpeg_sha256}"]
    definition = hashlib.sha256()
    for path in sorted(IMAGE_DIR.iterdir()):
        if path.is_file():
            definition.update(path.name.encode("utf-8") + b"\0" + path.read_bytes())
    definition.update("\0".join(args).encode("utf-8"))
    tag = f"albedian-ci-linux:{job.name}-{definition.hexdigest()[:12]}"
    with tempfile.TemporaryDirectory(prefix="ci-linux-") as scratch:
        idfile = Path(scratch) / "image-id"
        started = time.monotonic()
        done = run.call(*args, "--iidfile", str(idfile), "--tag", tag, str(IMAGE_DIR), timeout=BUILD_BOUND_SEC)
        if done.returncode != 0 or not idfile.is_file():
            tail = done.stdout.decode("utf-8", "replace").splitlines()[-30:]
            tail += done.stderr.decode("utf-8", "replace").splitlines()[-30:]
            raise SetupFailed("the image build failed:\n" + "\n".join(tail))
        image = idfile.read_text(encoding="utf-8").strip()
    run.say(f"ci_linux: image {tag} ({image[:19]}), ready in {time.monotonic() - started:.0f} s")
    return image


def create_container(run: Run, image: str, job: Job, cache: Path | None, pytest_args: Sequence[str]) -> str:
    name = f"ci-linux-{job.name}-{os.getpid()}-{secrets.token_hex(3)}"
    args = ["create", "--init", "--platform", "linux/amd64", "--name", name]
    # As on the runner: CI and GITHUB_ACTIONS (Hypothesis loads its ci profile, pytest stops truncating), the
    # temporary folder, and the Python version setup-uv sets for the job
    for setting in ("CI=true", "GITHUB_ACTIONS=true", "RUNNER_TEMP=/home/runner/work/_temp", f"UV_PYTHON={job.python}"):
        args += ["--env", setting]
    # uv's download cache, as setup-uv restores one on CI: a docker volume per job, kept in the engine's own disk
    args += ["--mount", f"type=volume,source={UV_CACHE_VOLUME}-{job.name},target={UV_CACHE_DIR}"]
    if cache is not None:
        args += ["--mount", f"type=bind,source={cache},target={WORKSPACE}/.cache,readonly"]
    args += [image, "bash", "/opt/ci-linux/job.bash", job.ffmpeg, *pytest_args]
    done = run.call(*args)
    if done.returncode != 0:
        raise SetupFailed("docker create failed: " + done.stderr.decode("utf-8", "replace").strip())
    run.container = done.stdout.decode("utf-8").strip()
    return run.container


def copy_files(run: Run, entries: Iterator[Entry]) -> int:
    assert run.container is not None
    with tempfile.TemporaryFile() as tar:
        count = write_tar(entries, tar, time.time())
        tar.seek(0)
        done = run.call("cp", "--archive", "-", f"{run.container}:{WORKSPACE}", stdin=tar, timeout=COPY_BOUND_SEC)
    if done.returncode != 0:
        raise SetupFailed("docker cp failed: " + done.stderr.decode("utf-8", "replace").strip())
    return count


STEP_MARK = "ci_linux step: "


def stream(run: Run, pipe: IO[bytes]) -> None:
    """Copies the job's output as it comes (a line of test dots shows before it ends) and notes the last step."""
    pending = ""
    while chunk := os.read(pipe.fileno(), 65536):
        text = chunk.decode("utf-8", "replace")
        run.write(text)
        pending += text
        *lines, pending = pending.split("\n")
        for line in lines:
            if line.startswith(STEP_MARK):
                run.last_step = line[len(STEP_MARK) :].strip()


def report_bound(run: Run, bound: int) -> None:
    """Names what was running when the bound passed, prints the stacks, interrupts pytest and stops the container."""
    running = run.read_watch("running").strip()
    last = run.read_watch("last").strip()
    pid = run.read_watch("pid").strip()
    stacks = ""
    if re.fullmatch(r"[0-9]+", pid):
        run.signal_pytest(pid, "USR1")
        time.sleep(STACKS_WAIT_SEC)
        stacks = run.read_watch("stacks")
    if running:
        where = f"while running {running}"
    elif last:
        where = f"between tests, after {last} ended"
    elif re.fullmatch(r"[0-9]+", pid):
        where = "in pytest before any test ran (collection or the start of the session)"
    else:
        where = f"before pytest started, in the step: {run.last_step or 'the start of the container'}"
    run.say(f"\nci_linux: THE BOUND OF {bound} s PASSED {where}")
    if stacks:
        run.say("ci_linux: every thread's Python stack in pytest at that moment:\n" + stacks)
    if re.fullmatch(r"[0-9]+", pid):
        run.say(f"ci_linux: interrupting pytest so it prints what failed so far (up to {INTERRUPT_GRACE_SEC} s)")
        run.signal_pytest(pid, "INT")


def wait_job(run: Run, bound: int) -> int:
    """Starts the container attached, bounded; the job's exit status, or EXIT_BOUND when the bound passed."""
    assert run.container is not None
    if run.engine_lock is not None:
        run.engine_lock.bound_started()
    attached = subprocess.Popen(
        [*run.docker, "start", "--attach", run.container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=docker_env(),
    )
    assert attached.stdout is not None
    reader = threading.Thread(target=stream, args=(run, attached.stdout), daemon=True)
    reader.start()
    try:
        attached.wait(timeout=bound)
    except subprocess.TimeoutExpired:
        report_bound(run, bound)
        try:
            attached.wait(timeout=INTERRUPT_GRACE_SEC)
        except subprocess.TimeoutExpired:
            run.say("ci_linux: pytest did not end after the interrupt; stopping the container by its id")
            try:
                run.call("kill", run.container)
            except SetupFailed:
                pass
            try:
                attached.wait(timeout=CALL_BOUND_SEC)
            except subprocess.TimeoutExpired:
                attached.kill()
                attached.wait(timeout=CALL_BOUND_SEC)
        reader.join(timeout=CALL_BOUND_SEC)
        return EXIT_BOUND
    reader.join(timeout=CALL_BOUND_SEC)
    done = run.call("inspect", "--format", "{{.State.ExitCode}}", run.container)
    code = done.stdout.decode("utf-8").strip()
    if done.returncode != 0 or not re.fullmatch(r"-?[0-9]+", code):
        raise SetupFailed("docker inspect could not read the job's exit status")
    return int(code)


def load_average() -> str:
    try:
        return " ".join(f"{value:.2f}" for value in os.getloadavg())
    except OSError:
        return "unknown"


def describe(root: Path, rev: str | None) -> str:
    if rev is not None:
        return f"commit {git(root, 'rev-parse', '--short', f'{rev}^{{commit}}').decode('utf-8').strip()}"
    head = git(root, "rev-parse", "--short", "HEAD").decode("utf-8").strip()
    changed = [line for line in git(root, "status", "--porcelain").decode("utf-8").splitlines() if line]
    return f"the working tree at {head} ({len(changed)} paths differ from it)"


def run_job(
    args: argparse.Namespace,
    root: Path,
    workflow: Workflow,
    job: Job,
    run: Run,
    entries: Callable[[], Iterator[Entry]],
    owners: Sequence[str],
) -> int:
    cache = cache_source(root, owners) if args.with_cache else None
    bound = args.bound if args.bound is not None else default_bound(workflow, args.pytest_args)
    version = run.call("version", "--format", "{{.Server.Version}}")
    if version.returncode != 0:
        raise SetupFailed(
            f"the engine (colima profile {PROFILE}) is not reachable. Start it once with:\n  "
            + shlex.join(engine_command(root, owners))
            + f"\nand stop it with:\n  colima stop {PROFILE}"
        )
    snapshot = describe(root, args.rev)
    run.say(f"ci_linux: {job.name} (Python {job.python}, {job.ffmpeg} ffmpeg) on {snapshot}")
    run.say(f"ci_linux: bound {bound} s; .cache {'mounted read only' if cache else 'absent, as on CI'}")
    lane = lane_of(args.pytest_args, bound)
    run.engine_lock = EngineLock(SHORT_LOCK_PATH if lane == "short" else LOCK_PATH, lane)
    holder = f"{job.name} on {snapshot} from {root} (process {os.getpid()})"
    waited = run.engine_lock.acquire(run, holder, bound)
    run.say(f"ci_linux: the engine's {lane} lane is this run's after {waited:.0f} s of waiting")
    run.say(f"ci_linux: load average at the start {load_average()}")
    image = build_image(run, workflow, job)
    create_container(run, image, job, cache, args.pytest_args)
    count = copy_files(run, entries())
    run.say(f"ci_linux: container {run.container[:12] if run.container else ''}, {count} files copied in")
    started = time.monotonic()
    code = wait_job(run, bound)
    elapsed = time.monotonic() - started
    verdict = "BOUND PASSED" if code == EXIT_BOUND else ("PASSED" if code == 0 else "FAILED")
    run.say(
        f"ci_linux: {job.name} {verdict} (exit {code}) after {elapsed:.0f} s; load average at the end {load_average()}"
    )
    return code


def _stop_on_term(signum: int, frame: object) -> None:
    """A SIGTERM or a hangup ends the run through its cleanup: its ticket goes and its container is removed by id."""
    raise SystemExit(128 + signum)


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    on_main_thread = threading.current_thread() is threading.main_thread()
    previous = {sig: signal.signal(sig, _stop_on_term) for sig in STOP_SIGNALS} if on_main_thread else {}
    log = open(args.log, "a", encoding="utf-8") if args.log else None  # noqa: SIM115 - closed below
    try:
        run = Run(shlex.split(args.docker), log)
        try:
            root = checkout_root(Path.cwd())
            if args.rev is not None:
                git(root, "rev-parse", "--verify", "--quiet", f"{args.rev}^{{commit}}")
                text = git(root, "show", f"{args.rev}:{WORKFLOW_PATH}").decode("utf-8")
            else:
                text = (root / WORKFLOW_PATH).read_text(encoding="utf-8")
            workflow = parse_workflow(text)
            check_workflow(workflow)
            jobs = {job.name: job for job in workflow.jobs}
            if args.job not in jobs:
                raise Refused(f"no job {args.job!r} in the workflow; its Python jobs are {', '.join(jobs)}")

            left_out = left_out_folders(root, args.rev)
            owners = tuple(folder for folder in left_out if folder != CACHE_FOLDER)
            # One listing, judged against the disk before anything starts, and judged again as each entry is copied
            blobs = rev_blobs(root, args.rev) if args.rev is not None else []
            names = [blob[2] for blob in blobs] if args.rev is not None else worktree_names(root)
            pre_judge = Judge(root, left_out)
            if args.rev is not None:
                pre_judge.before_commit(blobs)
                seen: Seen | None = None
            else:
                seen = pre_judge.snapshot_worktree(names)

            def entries() -> Iterator[Entry]:
                if args.rev is not None:
                    return rev_entries(root, args.rev, left_out, blobs)
                return worktree_entries(root, left_out, names, seen)

            return run_job(args, root, workflow, jobs[args.job], run, entries, owners)
        except Refused as error:
            run.say(f"ci_linux: refused: {error}")
            return EXIT_USAGE
        except SetupFailed as error:
            run.say(f"ci_linux: setup failed: {error}")
            return EXIT_SETUP
        finally:
            run.stop()
    finally:
        if log is not None:
            log.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
