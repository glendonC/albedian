"""Run the whole Python suite as file shards side by side, then the files that time themselves alone (gate-shards-one).

Usage, from the repository root:
    uv run python scripts/pytest_shards.py [--workers 6] [pytest options, for example -q -rs --no-unexpected-skips]

First pytest's own --collect-only, with the options given (its verbosity left out), names the files that hold tests,
whatever python_files matches (test_*.py and *_test.py by default), and the number of tests; when it fails, nothing
runs and its exit code is the run's. Every file it names, and every test_*.py under tests/, goes to exactly one place. The files in SERIAL_FILES assert wall-clock bounds, so they run
in one pytest process of their own after every shard has finished, never beside other work of the suite's. Every other
file goes to one of the shards: the files with a recorded time (scripts/pytest_shard_durations.json, seconds per file
from a whole serial run) longest first, each to the shard with the least recorded time so far; then each file with no
recorded time to the shard that is lightest at that moment. Each shard is its own pytest process, run by this Python,
with its own temporary folder (--basetemp and TMPDIR), pytest's cache plugin off (six processes must not write one
.pytest_cache at once), its file list on the command line, and the options given here passed on unchanged.

A shard names its files on the command line, so the suite's own whole-tree check under --no-unexpected-skips (every
test file collected) does not run inside it (tests/conftest.py, `_cli_targets_specific_file`); this script makes the
same check across the shards: a test file that collected no test fails the run, as it fails a whole serial run.

What it prints: one line per shard and one for the serial files (files, tests, seconds, pytest's exit code), the last
lines of any process that failed, each skipped test with its reason, then the sums, an expected failure counted apart
from a skip. With --no-unexpected-skips passed on, a skip that is not an owner_media or local_dataset marker's own
fails its shard, as it fails a whole serial run. It exits with the largest of pytest's codes 2 (interrupted), 3
(internal error) and 4 (usage error) when a process gave one; else 1 when a test failed or errored or a test file
collected no test or the shards ran a different number of tests than pytest collected; else the largest code a process gave (0 when all passed, 5 when one collected nothing); and 4
before running anything when a serial file it is given does not exist.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = "scripts/pytest_shard_durations.json"
WORKERS = 6
# The test files that assert a wall-clock bound, so more work beside them could fail them (the gate-durations-probe
# scan, `elapsed < ...` and the like): run alone, after the shards
SERIAL_FILES = (
    "tests/integration/test_fetch_download.py",
    "tests/integration/test_guarded_call.py",
    "tests/integration/test_test_bound.py",
    "tests/integration/test_voice_offline.py",
    "tests/media/test_clip_io.py",
    "tests/science/test_bounded_worker.py",
    "tests/science/test_real_photo_benchmark.py",
)
TAIL_LINES = 30
USAGE_ERROR = 4
# Set in each shard's environment to its name (shard1 to shard6, or serial), and taken out of pytest's own collection step,
# so a test or conftest can tell a shard from a whole run even when this script runs inside one of its own shards
SHARD_ENV = "ALBEDIAN_PYTEST_SHARD"
# pytest's exit codes for a run that did not run its tests through: interrupted, internal error, usage error
BROKEN = (2, 3, 4)


@dataclass(frozen=True)
class Plan:
    shards: tuple[tuple[str, ...], ...]
    serial: tuple[str, ...]
    loads: tuple[float, ...]


@dataclass
class Report:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    seconds: float = 0.0
    files: set[str] = field(default_factory=set)
    # Each skipped test (an expected failure is not a skip) with the reason pytest gave
    skips: list[tuple[str, str]] = field(default_factory=list)

    def add(self, other: "Report") -> None:
        self.passed += other.passed
        self.failed += other.failed
        self.errors += other.errors
        self.skipped += other.skipped
        self.xfailed += other.xfailed
        self.seconds += other.seconds
        self.files |= other.files
        self.skips += other.skips

    @property
    def tests(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped + self.xfailed


def test_files(root: Path) -> list[str]:
    """Every test_*.py under root/tests, as repository paths, sorted."""
    return sorted(path.relative_to(root).as_posix() for path in (root / "tests").rglob("test_*.py"))


def read_durations(path: Path) -> dict[str, float]:
    """The recorded seconds per test file; none when the record is absent."""
    if not path.is_file():
        return {}
    return {str(k): float(v) for k, v in json.loads(path.read_text())["seconds"].items()}


# Verbosity flags change what --collect-only prints, so the collection step leaves them out and uses its own -q
VERBOSITY = frozenset({"-q", "-qq", "-qqq", "--quiet", "-v", "-vv", "-vvv", "--verbose"})


def collect(root: Path, options: list[str]) -> tuple[list[str], int, int]:
    """What pytest itself collects with the options given: the files that hold its tests, by whatever name its
    python_files setting matches, the number of tests, and its exit code."""
    kept = [o for o in options if o not in VERBOSITY]
    env = {k: v for k, v in os.environ.items() if k != SHARD_ENV}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *kept],
        cwd=root, env=env, capture_output=True, text=True,
    )
    ids = [line.strip() for line in done.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    files = sorted({nodeid.split("::", 1)[0] for nodeid in ids})
    return files, len(ids), done.returncode


def plan(files: list[str], recorded: dict[str, float], workers: int, serial) -> Plan:
    """Each file in exactly one place: the serial tail, or the shard with the least recorded time when its turn comes
    (recorded files longest first, then each unrecorded file, by path, to the lightest shard). Ties go to the lower
    shard; empty shards are dropped."""
    present = set(files)
    tail = tuple(f for f in dict.fromkeys(serial) if f in present)
    rest = sorted(present - set(tail))
    known = sorted((f for f in rest if f in recorded), key=lambda f: (-recorded[f], f))
    unknown = [f for f in rest if f not in recorded]
    count = max(1, workers)
    groups: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for name in known:
        i = loads.index(min(loads))
        groups[i].append(name)
        loads[i] += recorded[name]
    for name in unknown:
        i = loads.index(min(loads))
        groups[i].append(name)
    kept = [(tuple(sorted(g)), loads[i]) for i, g in enumerate(groups) if g]
    return Plan(shards=tuple(g for g, _ in kept), serial=tail, loads=tuple(load for _, load in kept))


def read_report(path: Path) -> Report:
    """What one pytest junit XML (family xunit1, which names each test's file) says ran."""
    report = Report()
    if not path.is_file():
        return report
    for case in ET.parse(path).getroot().iter("testcase"):
        report.seconds += float(case.get("time") or 0.0)
        if case.get("file"):
            report.files.add(case.get("file"))
        tags = {child.tag: child for child in case}
        if "failure" in tags:
            report.failed += 1
        elif "error" in tags:
            report.errors += 1
        elif "skipped" in tags:
            # pytest's junit writes an expected failure as a skipped element of type pytest.xfail
            if "xfail" in (tags["skipped"].get("type") or ""):
                report.xfailed += 1
            else:
                report.skipped += 1
                where = f"{case.get('file') or case.get('classname')}::{case.get('name')}"
                report.skips.append((where, tags["skipped"].get("message") or ""))
        else:
            report.passed += 1
    return report


def _pytest(root: Path, files, options: list[str], work: Path, name: str) -> subprocess.Popen:
    temp = work / name
    (temp / "tmp").mkdir(parents=True)
    env = dict(os.environ, TMPDIR=str(temp / "tmp"), **{SHARD_ENV: name})
    command = [
        sys.executable, "-m", "pytest", *options,
        "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
        f"--basetemp={temp / 'base'}", f"--junitxml={temp / 'report.xml'}", *files,
    ]
    log = (temp / "output.log").open("w")
    return subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)


def _tail(path: Path) -> str:
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-TAIL_LINES:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--workers", type=int, default=WORKERS, help="parallel shards (default 6)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--serial", default=None, help=argparse.SUPPRESS)
    args, options = parser.parse_known_args(argv)
    root = args.root.resolve()
    serial = SERIAL_FILES if args.serial is None else tuple(s for s in args.serial.split(",") if s)
    missing = [s for s in serial if not (root / s).is_file()]
    if missing:
        print(f"pytest_shards: serial files that do not exist: {', '.join(missing)}")
        return USAGE_ERROR
    # Every file pytest itself collects, of any name, and every test_*.py under tests/ (one that collects nothing must
    # still fail the run); the number pytest collects is the number the shards must run
    collected_files, collected_total, collected_code = collect(root, options)
    if collected_code not in (0, 5):
        print(f"pytest_shards: pytest's own collection exited {collected_code}; no shard runs")
        return collected_code
    files = sorted(set(collected_files) | set(test_files(root)))
    the_plan = plan(files, read_durations(root / DURATIONS), args.workers, serial)
    started = time.monotonic()
    total = Report()
    codes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="pytest-shards-") as folder:
        work = Path(folder)
        running = [
            (f"shard {i + 1}", shard, _pytest(root, shard, options, work, f"shard{i + 1}"))
            for i, shard in enumerate(the_plan.shards)
        ]
        for name, shard, process in running:
            code = process.wait()
            codes.append(code)
            report = read_report(work / name.replace(" ", "") / "report.xml")
            total.add(report)
            print(f"{name}: {len(shard)} files, {report.tests} tests, {report.seconds:.0f} s of test time, exit {code}")
            if code != 0:
                print(_tail(work / name.replace(" ", "") / "output.log"))
        if the_plan.serial:
            process = _pytest(root, the_plan.serial, options, work, "serial")
            code = process.wait()
            codes.append(code)
            report = read_report(work / "serial" / "report.xml")
            total.add(report)
            print(f"serial: {len(the_plan.serial)} files, {report.tests} tests, {report.seconds:.0f} s of test time, exit {code}")
            if code != 0:
                print(_tail(work / "serial" / "output.log"))
    # A process that could not run its tests (interrupted, internal error, usage error) names the run's exit code; the
    # file check below means something only when every process ran
    broken = [code for code in codes if code in BROKEN]
    empty = [] if broken else [f for f in files if f not in total.files]
    if empty:
        print(f"{len(empty)} test file(s) under tests/ collected no test: {', '.join(empty)}")
    for where, reason in total.skips:
        print(f"skipped: {where}: {reason}")
    print(
        f"{total.passed} passed, {total.failed} failed, {total.errors} errors, {total.skipped} skipped, "
        f"{total.xfailed} xfailed in {time.monotonic() - started:.0f} s across "
        f"{len(the_plan.shards)} shards and {len(the_plan.serial)} serial files"
    )
    short = not broken and total.tests != collected_total
    if short:
        print(f"pytest collected {collected_total} tests, the shards ran {total.tests}")
    if broken:
        return max(broken)
    if total.failed or total.errors or empty or short:
        return 1
    return max(codes, default=0)


if __name__ == "__main__":
    sys.exit(main())
