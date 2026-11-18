"""The independent check: astrometry.net's own solver on one corpus photo, read back through its own tools.

astrometry.net (`solve-field`, with `wcsinfo`, `wcs-rd2xy`, `wcs-xy2rd`, `text2fits` and `tablist` beside it) is
installed on this laptop for tests only; it is never a dependency of the app. Its wide-field index files 4107 to 4119
(built from Tycho-2) are in `.cache/astrometry-net/4100/`, listed in `data/downloads.json`.

This module reads nothing from the repository's own WCS, detector or solver code, so a mistake in one cannot hide in
the other: the photo goes to `solve-field` as a file, the solution is read with `wcsinfo`, and a catalog position is
turned into a pixel with `wcs-rd2xy`. Its repository imports are the corpus root path and services/media's guarded
caller (`guarded_call`), which runs every external program this module starts. Importing that caller runs services/media's
package file, which loads the repository's science modules into the process; the check calls none of them.

Each attempt runs in a scratch folder inside the run's folder (under `.cache/solver-bench/real-photos/`, or the
folder `ALBEDIAN_REAL_PHOTO_BENCH` names, which the tests point at pytest's temporary folder) with a hard wall-clock
bound, since the `--cpulimit` flag did not stop a long search when the lead first ran the check. Each attempt runs
under services/media's guard (`guarded_call.run`), the repository's one way to run a program and end it with
everything it started: the guard starts it in a process group of its own and ends that whole group with SIGKILL when
the program ends, at the bound, on a stop, and when the measuring process goes away for any reason. SIGTERM, SIGHUP and SIGINT are only noted and read at points the code
chooses (`stop_on_signals`, `stop_point`), so stopping is ordinary code and never an exception at an arbitrary line;
each scratch folder is locked while it is in use (an attempt's guard holds the lock too), so a later start removes
what a dead run left. The config file,
the temporary files (`--temp-dir`) and every output stay in the scratch folder, which is removed after each attempt;
only the record, the logs and the solution's small WCS header file are kept. A folder is removed only through
`scratch_folder`, which removes exactly the folder that same call made, under a name it minted.

Pixels. astrometry.net writes FITS pixel coordinates, where the first pixel's centre is (1, 1) (Greisen and
Calabretta 2002, A&A 395, 1061, section 2.1). The repository's detections use array coordinates, where it is (0, 0).
`sky_to_pixel` and `pixel_to_sky` take and give array coordinates, so the two can be compared directly.

Limits of the check, stated in docs/science/REAL_PHOTO_BENCHMARK.md: it is one solver; the 4100 index files cover
wide fields only (quads from about 22 arcmin up to about 33 deg); a photo it does not solve is not shown unsolvable,
and its failures are not ours.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import select
import shutil
import signal
import stat
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from research.benchmarks.real_photos.corpus import ROOT
from services.media import guarded_call

ORACLE_NAME = "astrometry.net"
SOLVE_FIELD = "solve-field"
TOOLS = ("solve-field", "wcsinfo", "wcs-rd2xy", "wcs-xy2rd", "text2fits", "tablist")
INDEX_FOLDER = ".cache/astrometry-net/4100"
INDEX_FILES: tuple[str, ...] = tuple(f"{INDEX_FOLDER}/index-{number}.fits" for number in range(4107, 4120))
BENCH_FOLDER = ".cache/solver-bench/real-photos"
# The tests point this at pytest's temporary folder, so no test reaches the shared cache's records
BENCH_ENV = "ALBEDIAN_REAL_PHOTO_BENCH"
# Every scratch or store folder this benchmark removes carries this in its name, minted by `scratch_folder`
SCRATCH_MARK = "-albedian-scratch-"


def bench_root() -> Path:
    """Where runs keep their records: `.cache/solver-bench/real-photos/`, or the folder `ALBEDIAN_REAL_PHOTO_BENCH`
    names."""
    named = os.environ.get(BENCH_ENV)
    return Path(named) if named else ROOT / BENCH_FOLDER



# Every minted folder holds this file, locked (flock) by the process that made it while it lives, and in an attempt
# folder by the guard of the attempt's program too; a folder whose lock nobody holds belongs to a run that is gone
LOCK_NAME = ".albedian-lock"
# A folder is made under this mark, locked, and only then renamed to carry SCRATCH_MARK, so every folder that
# carries the mark has its lock file
STAGING_MARK = "-albedian-staging-"


def _mint(parent: Path, prefix: str) -> tuple[Path, int]:
    """A new folder under `parent` with a minted name, and the open descriptor that holds its lock."""
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{prefix}{STAGING_MARK}", dir=parent))
    lock = os.open(staging / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    made = staging.with_name(staging.name.replace(STAGING_MARK, SCRATCH_MARK, 1))
    os.rename(staging, made)
    return made, lock


@contextmanager
def scratch_folder(parent: Path, prefix: str) -> Iterator[Path]:
    """A new folder under `parent` with a minted name, locked while the block runs and removed when it ends.

    It is the only way this benchmark makes a folder it removes: it removes exactly the folder this same call made,
    which no caller can name, and `_remove_minted` refuses any folder whose name it did not mint. A run that is
    killed leaves its folder unlocked, and `remove_dead_scratch` removes it at a later start.
    """
    made, lock = _mint(parent, prefix)
    try:
        yield made
    finally:
        try:
            _remove_minted(made)
        finally:
            os.close(lock)


def remove_dead_scratch(parent: Path) -> list[str]:
    """Remove each minted folder under `parent` whose lock no live process holds (a run that was killed); a live
    run's folder is told apart by its lock, never by its age. Returns the names removed."""
    removed = []
    if not parent.is_dir():
        return removed
    for path in sorted(parent.iterdir()):
        if SCRATCH_MARK not in path.name or path.is_symlink() or not path.is_dir():
            continue
        try:
            lock = os.open(path / LOCK_NAME, os.O_RDONLY)
        except FileNotFoundError:
            lock = None  # minted before folders had locks: no live run of this version holds it
        try:
            if lock is not None:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
            _remove_minted(path)
            removed.append(path.name)
        finally:
            if lock is not None:
                os.close(lock)
    return removed


def _remove_minted(path: Path) -> None:
    """Remove a folder `scratch_folder` minted, read-only files and folders included; refuse any other path."""
    if SCRATCH_MARK not in path.name or path.is_symlink() or not path.is_dir():
        raise ValueError(f"not a scratch folder this benchmark minted: {path}")

    def writable_then_retry(function: Any, target: str, _: Any) -> None:
        os.chmod(os.path.dirname(target), stat.S_IRWXU)
        if os.path.isdir(target) and not os.path.islink(target):
            os.chmod(target, stat.S_IRWXU)
        function(target)

    shutil.rmtree(path, onexc=writable_then_retry)
# The lead's first run of the check: `--tweak-order 3` and `--crpix-center` on every attempt
TWEAK_ORDER = 3
# [proposed] The wall-clock bound of one attempt. The lead's two solves took 3 s and 5 s; a third ran 942 s without
# solving. 60 s is twelve times the slower solve and keeps a full corpus run of three attempts under three hours.
ATTEMPT_TIME_LIMIT_S = 60.0
# [proposed] The search range around the width a photo's FocalLengthIn35mmFilm tag gives: from 0.8 to 1.25 times it,
# since a phone's tag rounds to whole millimetres and some phones write it for the uncropped sensor
HINT_LOW, HINT_HIGH = 0.8, 1.25
# The 36 x 24 mm frame the 35 mm equivalent focal length refers to, by its diagonal (CIPA DC-008, FocalLengthIn35mmFilm)
FILM_DIAGONAL_MM = math.hypot(36.0, 24.0)


class OracleUnavailable(RuntimeError):
    """astrometry.net or its index files are not on this machine."""


@dataclass(frozen=True)
class Attempt:
    """One way of running solve-field: a downsample factor and a range for the image width in degrees."""

    label: str
    downsample: int
    scale_low_deg: float
    scale_high_deg: float
    source: str

    def arguments(self) -> list[str]:
        return [
            "--scale-units", "degwidth",
            "--scale-low", f"{self.scale_low_deg:g}",
            "--scale-high", f"{self.scale_high_deg:g}",
            "--downsample", str(self.downsample),
            "--crpix-center",
            "--tweak-order", str(TWEAK_ORDER),
        ]  # fmt: skip


# The fixed attempts, in order. A photo with a FocalLengthIn35mmFilm tag also gets the hinted attempt, second.
LEAD_ATTEMPT = Attempt("ds4-w25-120", 4, 25.0, 120.0, "the lead's first run, which solved two earlier phone photos in 3 s and 5 s")
FINE_ATTEMPT = Attempt("ds2-w10-120", 2, 10.0, 120.0, "[proposed] twice the pixels, so more faint stars, and fields down to a 2x zoom")
FIXED_ATTEMPTS: tuple[Attempt, ...] = (LEAD_ATTEMPT, FINE_ATTEMPT)


def width_from_35mm_deg(focal_35mm_mm: float, width_px: int, height_px: int) -> float:
    """The angle across the stored width of a rectilinear frame whose diagonal a 35 mm equivalent focal length gives.

    The equivalent scales by the diagonal, so the half diagonal subtends atan(d / 2f) with d the 43.27 mm film
    diagonal, and the stored width is its share of the pixel diagonal: 2 atan((w / D) * d / 2f).
    """
    if not (math.isfinite(focal_35mm_mm) and focal_35mm_mm > 0 and width_px > 0 and height_px > 0):
        raise ValueError("a positive focal length and a positive size are needed")
    share = width_px / math.hypot(width_px, height_px)
    return math.degrees(2.0 * math.atan(share * FILM_DIAGONAL_MM / (2.0 * focal_35mm_mm)))


def attempts_for(focal_35mm_mm: Optional[float], width_px: int, height_px: int) -> list[Attempt]:
    """The attempts for one photo, in order: the lead's, the hinted one when the file has a 35 mm tag, the fine one."""
    attempts = [LEAD_ATTEMPT]
    if focal_35mm_mm is not None:
        width_deg = width_from_35mm_deg(float(focal_35mm_mm), width_px, height_px)
        attempts.append(
            Attempt(
                "ds4-hint35",
                4,
                round(HINT_LOW * width_deg, 2),
                round(HINT_HIGH * width_deg, 2),
                "[proposed] the width the file's FocalLengthIn35mmFilm tag gives, from 0.8 to 1.25 times it",
            )
        )
    attempts.append(FINE_ATTEMPT)
    return attempts


@dataclass
class AttemptRecord:
    label: str
    solved: bool
    seconds: float
    stopped_at_bound: bool
    exit_code: Optional[int]
    sources: Optional[int]


@dataclass
class OracleRecord:
    """What the check says of one photo. Field centre and size are in degrees; pixels are array coordinates."""

    photo_id: str
    oracle: str
    version: Optional[str]
    solved: bool
    settings: Optional[dict[str, Any]]
    attempts: list[AttemptRecord]
    seconds: float
    matches: Optional[int] = None
    conflicts: Optional[int] = None
    distractors: Optional[int] = None
    index_stars: Optional[int] = None
    log_odds: Optional[float] = None
    index_file: Optional[str] = None
    correspondences: Optional[int] = None
    check_median_residual_px: Optional[float] = None
    centre_ra_deg: Optional[float] = None
    centre_dec_deg: Optional[float] = None
    field_width_deg: Optional[float] = None
    field_height_deg: Optional[float] = None
    diagonal_fov_deg: Optional[float] = None
    pixel_scale_arcsec: Optional[float] = None
    orientation_deg: Optional[float] = None
    parity: Optional[str] = None
    image_width_px: Optional[int] = None
    image_height_px: Optional[int] = None
    wcs_file: Optional[str] = None
    note: Optional[str] = None
    # The checkout that made this record (`code_state`); records made before this field existed have none
    code: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OracleRecord:
        values = dict(data)
        values["attempts"] = [AttemptRecord(**attempt) for attempt in values.get("attempts", [])]
        return cls(**values)


# -------------------------------------------------------------------------------------------------
# Reading astrometry.net's own outputs
# -------------------------------------------------------------------------------------------------

LOG_ODDS_LINE = re.compile(
    r"log-odds ratio (?P<odds>-?[0-9.]+(?:e[+-]?\d+)?) \([^)]*\), (?P<match>\d+) match, (?P<conflict>\d+) conflict,"
    r" (?P<distractors>\d+) distractors, (?P<index>\d+) index\."
)
SOLVED_LINE = re.compile(r"Field \d+: solved with index (?P<index>\S+)\.")
SOURCES_LINE = re.compile(r"simplexy: found (?P<sources>\d+) sources\.")
PARITY_LINE = re.compile(r"Field parity: (?P<parity>pos|neg)")


def parse_solve_log(text: str) -> dict[str, Any]:
    """The numbers solve-field prints: the sources it extracted and, for the match it solved with, its log-odds,
    matches, conflicts, distractors and index stars, the index file and the parity. A key is absent when its line is.

    The match that solved is the last log-odds line before the "solved with index" line; earlier ones were tried and
    tuned up, not kept.
    """
    out: dict[str, Any] = {}
    sources = SOURCES_LINE.findall(text)
    if sources:
        out["sources"] = int(sources[-1])
    solved = SOLVED_LINE.search(text)
    if solved is not None:
        out["index_file"] = solved["index"]
        odds = list(LOG_ODDS_LINE.finditer(text[: solved.start()]))
        if odds:
            last = odds[-1]
            out["log_odds"] = float(last["odds"])
            out["matches"] = int(last["match"])
            out["conflicts"] = int(last["conflict"])
            out["distractors"] = int(last["distractors"])
            out["index_stars"] = int(last["index"])
    parity = PARITY_LINE.search(text)
    if parity is not None:
        out["parity"] = parity["parity"]
    return out


def parse_wcsinfo(text: str) -> dict[str, Any]:
    """`wcsinfo`'s "key value" lines as a dict: a number where the value is one, the text otherwise."""
    info: dict[str, Any] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        try:
            number = float(value)
        except ValueError:
            info[key] = value.strip()
        else:
            info[key] = int(number) if re.fullmatch(r"-?\d+", value.strip()) else number
    return info


def parse_tablist(text: str) -> tuple[list[str], list[list[float]]]:
    """`tablist`'s table: the column names and one row of floats per line; `*` (no value) becomes NaN."""
    names: list[str] = []
    rows: list[list[float]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if not names:
            names = parts
            continue
        if parts[0].isdigit() and len(parts) == len(names) + 1:
            rows.append([math.nan if value == "*" else float(value) for value in parts[1:]])
    return names, rows


# -------------------------------------------------------------------------------------------------
# Running the tools
# -------------------------------------------------------------------------------------------------


COMMIT = re.compile(r"[0-9a-f]{40}")
GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
GIT_HEAD_LIMIT_S = 30.0
GIT_STATUS_LIMIT_S = 60.0
VERSION_LIMIT_S = 30.0
QUICK_TOOL_LIMIT_S = 120.0


def git_environment() -> dict[str, str]:
    """This process's environment without GIT_REDIRECTS, so `git -C <root>` reads `root` and no other repository."""
    return {key: value for key, value in os.environ.items() if key not in GIT_REDIRECTS}


def code_state(root: Path = ROOT) -> dict[str, Any]:
    """The code a run measured, as git itself reports it for the checkout at `root`: the commit, and whether a tracked
    file had uncommitted changes (untracked files do not count). Both None when git cannot tell."""
    unknown: dict[str, Any] = {"commit": None, "uncommittedChanges": None}
    head, _ = _call_guarded(["git", "-C", str(root), "rev-parse", "HEAD"], limit_s=GIT_HEAD_LIMIT_S, env=git_environment())
    if head.end != guarded_call.Exited(0):
        return unknown
    commit = head.stdout.decode("utf-8", errors="replace").strip()
    if not COMMIT.fullmatch(commit):
        return unknown
    status, _ = _call_guarded(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        limit_s=GIT_STATUS_LIMIT_S,
        env=git_environment(),
    )
    if status.end != guarded_call.Exited(0):
        return unknown
    return {"commit": commit, "uncommittedChanges": bool(status.stdout.strip())}


def missing_tools() -> list[str]:
    """The astrometry.net programs this check needs that are not on the PATH."""
    return [tool for tool in TOOLS if shutil.which(tool) is None]


def missing_index_files(root: Path = ROOT) -> list[str]:
    return [path for path in INDEX_FILES if not (root / path).is_file()]


def require_oracle(root: Path = ROOT) -> None:
    """Raise OracleUnavailable, saying plainly what is missing, unless the tools and the index files are here."""
    tools, files = missing_tools(), missing_index_files(root)
    problems = []
    if tools:
        problems.append(f"astrometry.net is not installed: {', '.join(tools)} not found on the PATH")
    if files:
        problems.append(f"{len(files)} of its index files are absent from {INDEX_FOLDER}")
    if problems:
        raise OracleUnavailable("; ".join(problems))


def _run(arguments: Sequence[str], folder: Path) -> str:
    """One quick astrometry.net program under the shared guard, as an argument list (never a shell string); its output."""
    done, seconds = _call_guarded(arguments, limit_s=QUICK_TOOL_LIMIT_S, folder=folder)
    code, stopped, _ = _outcome(Path(arguments[0]).name, done.end, seconds)
    if stopped:
        raise TimeoutError(f"{arguments[0]} timed out after {QUICK_TOOL_LIMIT_S:g} s")
    if code != 0:
        stderr = done.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise RuntimeError(f"{arguments[0]} exited with {code}: {stderr}")
    return done.stdout.decode("utf-8", errors="replace")


def solve_field_version() -> Optional[str]:
    done, _ = _call_guarded([SOLVE_FIELD, "--version"], limit_s=VERSION_LIMIT_S)
    if done.end != guarded_call.Exited(0):
        return None
    return done.stdout.decode("utf-8", errors="replace").strip() or None


class RunStopped(BaseException):
    """A stop signal (SIGTERM or SIGHUP) reached a run, read at one of the points the code chooses: raised there, so
    every cleanup on the way out runs (a BaseException, so no `except Exception` swallows it). SIGINT ends a run the
    same way as KeyboardInterrupt."""


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class _Stops:
    """The stop signals of the run in progress (main thread only). A handler only notes a signal: CPython writes
    its number to the wake-up descriptor, and `stop_point` reads it there. No handler raises."""

    depth = 0
    wake_read: Optional[int] = None
    seen: list[int] = []

    @classmethod
    def reset(cls) -> None:
        cls.depth, cls.wake_read, cls.seen = 0, None, []


def _noted(signum: int, frame: Any) -> None:
    """The handler of a stop signal: it does nothing, so no line of the run can be interrupted by an exception; the
    signal's number is already on the wake-up descriptor."""


def _stop_exception(signum: int) -> BaseException:
    return KeyboardInterrupt() if signum == signal.SIGINT else RunStopped(signal.Signals(signum).name)


def _read_stops() -> list[int]:
    """Move every signal number waiting on the wake-up descriptor into the run's record; the record so far."""
    if _Stops.wake_read is None:
        return _Stops.seen
    while True:
        try:
            data = os.read(_Stops.wake_read, 512)
        except BlockingIOError:
            break
        if not data:
            break
        _Stops.seen.extend(number for number in data if number in STOP_SIGNALS)
    return _Stops.seen


def stop_point() -> None:
    """A point where the run may stop: raise for the first stop signal noted so far (RunStopped, or
    KeyboardInterrupt for SIGINT). Outside `stop_on_signals`, or off the main thread, nothing happens."""
    if threading.current_thread() is threading.main_thread() and _Stops.depth > 0:
        seen = _read_stops()
        if seen:
            raise _stop_exception(seen[0])


def _every_step(steps: Sequence[Any]) -> None:
    """Run every step, even when an earlier one raises; then raise the first error, if any."""
    first: Optional[BaseException] = None
    for step in steps:
        try:
            step()
        except BaseException as error:  # noqa: BLE001
            first = first if first is not None else error
    if first is not None:
        raise first


@contextmanager
def stop_on_signals() -> Iterator[None]:
    """While the block runs, SIGTERM, SIGHUP and SIGINT are only noted, through a wake-up descriptor, and the run
    reads them at the points it chooses (`stop_point`, and the wait for each attempt, which then stops the check's
    group first). A signal the process was started to ignore stays ignored, and SIGINT is taken only from Python's
    own default handler. The handlers are swapped in and put back with the three signals blocked, so none lands
    halfway: one sent while they are swapped in is noted once they are, and one sent while they are put back goes
    to the handler the block found. The handlers, the mask and the wake-up descriptor the block found are put back
    on every way out, every step even when another fails, and a stop noted after the last point is raised there,
    so none is lost. Nested blocks share the outermost one. Signal handlers belong to the main thread: elsewhere it
    does nothing. The command line wraps `oracle` and `measure` in it; `run_bounded` holds one around each attempt."""
    if threading.current_thread() is not threading.main_thread() or _Stops.depth > 0:
        _Stops.depth += threading.current_thread() is threading.main_thread()
        try:
            yield
        finally:
            _Stops.depth -= threading.current_thread() is threading.main_thread()
        return
    taken = {}
    for sig in STOP_SIGNALS:
        current = signal.getsignal(sig)
        if current is signal.SIG_IGN or (sig == signal.SIGINT and current is not signal.default_int_handler):
            continue
        taken[sig] = current
    # Reading the mask runs any handler already due, so a stop that was waiting raises here, before anything changes
    found_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    pipe: list[int] = []
    swapped: list[int] = []
    found_wake: list[int] = []
    late: list[int] = []

    def put_back() -> None:
        """Every part of the run's signal state back as found; the mask last, so a signal that waits goes to the
        handler the block found."""
        _every_step(
            [lambda: signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS), lambda: late.extend(_read_stops())]
            + [lambda sig=sig: signal.signal(sig, taken[sig]) for sig in swapped]
            + [lambda: found_wake and signal.set_wakeup_fd(found_wake[0])]
            + [lambda fd=fd: os.close(fd) for fd in pipe]
            + [_Stops.reset, lambda: signal.pthread_sigmask(signal.SIG_SETMASK, found_mask)]
        )

    try:
        signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
        pipe.extend(os.pipe())
        for fd in pipe:
            os.set_blocking(fd, False)
        found_wake.append(signal.set_wakeup_fd(pipe[1], warn_on_full_buffer=False))
        _Stops.depth, _Stops.wake_read, _Stops.seen = 1, pipe[0], []
        for sig in taken:
            signal.signal(sig, _noted)
            swapped.append(sig)
        signal.pthread_sigmask(signal.SIG_SETMASK, found_mask)
    except BaseException:
        put_back()
        raise
    stopping = False
    try:
        yield
    except BaseException:
        stopping = True
        raise
    finally:
        put_back()
        if late and not stopping:
            raise _stop_exception(late[0])


# How long the wait for an attempt's call looks again for a call to stop, once a stop is noted: the call is listed in
# its RunningCalls only once its guard has started, so a stop noted before that is sent again until the call has ended
STOP_RETRY_S = 0.05


class ToolNotRun(RuntimeError):
    """A program of the check did not run to an end of its own: it or its guard could not be started, or its guard was
    ended from outside. `end` is the shared caller's typed end (services/media/guarded_call.py)."""

    def __init__(self, program: str, end: guarded_call.End) -> None:
        super().__init__(f"{program}: {_not_run_reason(end)}")
        self.end = end


def _not_run_reason(end: guarded_call.End) -> str:
    """What a person reads when a program of the check did not run to an end of its own."""
    if isinstance(end, guarded_call.NotStarted):
        if end.cause is guarded_call.NotStartedCause.NOT_FOUND:
            return "no executable program of that name was found"
        return "the system could not start the program"
    if isinstance(end, guarded_call.GuardNotStarted):
        return "the program's guard did not start"
    if isinstance(end, guarded_call.Cut) and not end.by_caller:
        return "the program's guard was ended from outside"
    return "the program's call ended in a way the check does not expect"


def _outcome(program: str, end: guarded_call.End, seconds: float) -> tuple[Optional[int], bool, float]:
    """(exit code, or minus the signal that ended the program, or None at the bound; whether the bound passed; seconds)
    for the ends that are the program's own or the bound's; ToolNotRun for every other."""
    if isinstance(end, guarded_call.Exited):
        return end.status, False, seconds
    if isinstance(end, guarded_call.Signalled):
        return -end.signal_number, False, seconds
    if isinstance(end, guarded_call.BoundPassed) and end.during is guarded_call.Bound.CALL:
        return None, True, seconds
    raise ToolNotRun(program, end)


# The lock of each attempt folder `attempt_folder` made, by folder: the guard of the attempt's program holds it too
_ATTEMPT_LOCKS: dict[Path, int] = {}


@contextmanager
def attempt_folder(parent: Path, prefix: str) -> Iterator[Path]:
    """A minted, locked scratch folder for one attempt of the check, removed however the block ends. The guard of the
    attempt's program (`run_bounded`) holds the folder's lock while it lives, so the folder counts as live until the
    program's whole group is gone. A measuring process killed by SIGKILL leaves the folder, and a later start removes
    it by its free lock (`remove_dead_scratch`)."""
    made, lock = _mint(parent, prefix)
    _ATTEMPT_LOCKS[made] = lock
    try:
        yield made
    finally:
        _ATTEMPT_LOCKS.pop(made, None)
        try:
            if made.exists():
                _remove_minted(made)
        finally:
            os.close(lock)


# Extra seconds beyond a call's own bound that `_wait` allows for the guard's cleanup before declaring the worker stuck
WAIT_MARGIN_S = 2.0 * guarded_call.GROUP_END_WAIT_SEC + 5.0


def _wait(worker: threading.Thread, done: int, running: guarded_call.RunningCalls, bound_s: float) -> None:
    """Wait at most `bound_s` for the thread that runs the call to signal `done` and end. On the main thread inside
    `stop_on_signals`, a stop signal noted meanwhile stops the call (the shared caller stops a call from any thread),
    again every STOP_RETRY_S until the thread has ended; `stop_point` raises it after. Raises RuntimeError if the thread
    ends without signalling `done`, or TimeoutError if `bound_s` passes while it still runs."""
    watching = threading.current_thread() is threading.main_thread() and _Stops.depth > 0 and _Stops.wake_read is not None
    stopping = False
    deadline = time.monotonic() + bound_s
    while True:
        if not worker.is_alive():
            worker.join(STOP_RETRY_S)
            if not select.select([done], [], [], 0.0)[0]:
                raise RuntimeError("the guarded call's thread ended without signalling its completion pipe")
        if watching and not stopping and _read_stops():
            stopping = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"waiting for the guarded call exceeded {bound_s:g} s")
        if stopping:
            running.stop_all()
            ready, _, _ = select.select([done], [], [], min(STOP_RETRY_S, remaining))
        else:
            ready, _, _ = select.select([done, _Stops.wake_read] if watching else [done], [], [], remaining)
        if done in ready:
            marker = os.read(done, 1)
            worker.join(max(STOP_RETRY_S, deadline - time.monotonic()))
            if marker != b"x":
                raise RuntimeError("the guarded call's thread ended without signalling its completion pipe")
            return


def _log_bytes(output: bytes, errors: bytes) -> bytes:
    """An attempt's log: the program's output, then its error stream, with a line break between them when the output
    does not end in one, so no line of either is joined to a line of the other."""
    joint = b"\n" if output and errors and not output.endswith(b"\n") else b""
    return output + joint + errors


def _command_with_env(arguments: Sequence[str], env: Optional[Mapping[str, str]]) -> list[str]:
    """`arguments` as a command for `guarded_call.run`, wrapped in POSIX `env` when `env` differs from `os.environ`."""
    if env is None:
        return [*arguments]
    unsets = [item for key in sorted(os.environ) if key not in env for item in ("-u", key)]
    changes = [f"{key}={value}" for key, value in sorted(env.items()) if os.environ.get(key) != value]
    if not unsets and not changes:
        return [*arguments]
    return ["env", *unsets, *changes, *arguments]


def _call_guarded(
    arguments: Sequence[str],
    *,
    limit_s: float,
    folder: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> tuple[guarded_call.Finished, float]:
    """Run one external program under `guarded_call.run` within `limit_s`, stopping its group on a noted stop signal
    and holding `folder`'s lock in the guard when `folder` is an `attempt_folder`."""
    command = _command_with_env(arguments, env)
    with stop_on_signals():
        stop_point()
        lock = None if folder is None else _ATTEMPT_LOCKS.get(Path(folder))
        running = guarded_call.RunningCalls()
        finished: list[guarded_call.Finished] = []
        failed: list[BaseException] = []
        done_read, done_write = os.pipe()
        write_open = True

        def call() -> None:
            nonlocal write_open
            try:
                # The guard takes the descriptors it is given and closes the caller's copies, so it gets a duplicate of
                # the lock, which holds the same lock; attempt_folder closes its own
                held = () if lock is None else (os.dup(lock),)
                finished.append(guarded_call.run(command, bound_sec=limit_s, running=running, pass_fds=held))
            except BaseException as error:  # noqa: BLE001 (raised again on the calling thread)
                failed.append(error)
            finally:
                try:
                    os.write(done_write, b"x")
                finally:
                    write_open = False
                    os.close(done_write)

        worker = threading.Thread(target=call, name="albedian-check-program")
        started = time.monotonic()
        try:
            worker.start()
            _wait(worker, done_read, running, limit_s + WAIT_MARGIN_S)
        finally:
            cleanup_deadline = time.monotonic() + WAIT_MARGIN_S
            while worker.is_alive() and time.monotonic() < cleanup_deadline:
                running.stop_all()
                worker.join(STOP_RETRY_S)
            os.close(done_read)
            if write_open:
                os.close(done_write)
        seconds = time.monotonic() - started
        if failed:
            raise failed[0]
        stop_point()
        return finished[0], seconds


def run_bounded(arguments: Sequence[str], log_path: Path, folder: Path, limit_s: float) -> tuple[Optional[int], bool, float]:
    """Run one program of the check with a wall-clock bound, under services/media's guard (guarded_call.run): the guard
    starts it in a process group of its own and ends that whole group on every way out (the program's own end, the
    bound, a stop, an error, or the measuring process going away for any reason). In an `attempt_folder` (`folder`),
    the guard holds that folder's lock while it lives. The program's output, then its error stream, is written to
    `log_path` (`_log_bytes`) when the call reached an end of the program's own or the bound. A stop signal noted
    while it runs stops the call, and is raised once the call has ended.

    Returns (exit code, or minus the signal that ended the program, or None when the bound passed; whether the bound
    passed; seconds from the start of the call, its guard's start included). Raises ToolNotRun when the program or its
    guard could not be started, or its guard was ended from outside."""
    done, seconds = _call_guarded(arguments, limit_s=limit_s, folder=folder)
    outcome = _outcome(Path(arguments[0]).name, done.end, seconds)
    Path(log_path).write_bytes(_log_bytes(done.stdout, done.stderr))
    return outcome


def _write_config(path: Path, index_dir: Path, limit_s: float) -> None:
    """The engine's config: only cpulimit, the index folder and autoindex, as the lead first ran it."""
    path.write_text(f"cpulimit {limit_s:g}\nadd_path {index_dir}\nautoindex\n", encoding="utf-8")


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle angle between two directions (haversine), in degrees."""
    p1, p2 = math.radians(dec1), math.radians(dec2)
    dp, dl = p2 - p1, math.radians(ra2 - ra1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(h))))


def _table(arguments_for: Any, rows: Sequence[Sequence[float]], header: str, scratch: Path) -> list[list[float]]:
    """Write rows to a FITS table with text2fits, run one converter on it, and read its output with tablist."""
    text = scratch / "in.txt"
    text.write_text("".join(" ".join(f"{value:.12f}" for value in row) + "\n" for row in rows), encoding="utf-8")
    _run(["text2fits", "-H", header, "-f", "dd", str(text), str(scratch / "in.fits")], scratch)
    _run(arguments_for(scratch / "in.fits", scratch / "out.fits"), scratch)
    _, out = parse_tablist(_run(["tablist", str(scratch / "out.fits")], scratch))
    if len(out) != len(rows):
        raise RuntimeError(f"expected {len(rows)} rows back, got {len(out)}")
    return out


def sky_to_pixel(wcs_file: Path, ra_deg: Sequence[float], dec_deg: Sequence[float], scratch_root: Path) -> list[tuple[float, float]]:
    """Each (RA, Dec) as an array-coordinate pixel through `wcs-rd2xy` (SIP included); NaN on the far side."""
    if len(ra_deg) == 0:
        return []
    with attempt_folder(scratch_root, "rd2xy") as folder:
        out = _table(
            lambda src, dst: ["wcs-rd2xy", "-w", str(wcs_file), "-i", str(src), "-o", str(dst)],
            list(zip(ra_deg, dec_deg)),
            "RA DEC",
            folder,
        )
    return [(x - 1.0, y - 1.0) for x, y in out]


def pixel_to_sky(wcs_file: Path, x_px: Sequence[float], y_px: Sequence[float], scratch_root: Path) -> list[tuple[float, float]]:
    """Each array-coordinate pixel as (RA, Dec) through `wcs-xy2rd` (SIP included)."""
    if len(x_px) == 0:
        return []
    with attempt_folder(scratch_root, "xy2rd") as folder:
        out = _table(
            lambda src, dst: ["wcs-xy2rd", "-w", str(wcs_file), "-i", str(src), "-o", str(dst)],
            [(x + 1.0, y + 1.0) for x, y in zip(x_px, y_px)],
            "X Y",
            folder,
        )
    return [(ra, dec) for ra, dec in out]


def diagonal_fov_deg(wcs_file: Path, width_px: int, height_px: int, scratch_root: Path) -> float:
    """The angle between opposite corners of the image, averaged over the two diagonals."""
    corners = pixel_to_sky(wcs_file, [-0.5, width_px - 0.5, width_px - 0.5, -0.5], [-0.5, height_px - 0.5, -0.5, height_px - 0.5], scratch_root)
    (a, b), (c, d), (e, f), (g, h) = corners
    return 0.5 * (_angular_separation_deg(a, b, c, d) + _angular_separation_deg(e, f, g, h))


def _check_residual_px(corr_file: Path, scratch: Path) -> tuple[int, Optional[float]]:
    """How many stars the solution paired, and the median distance between each field source and its index star."""
    _, rows = parse_tablist(_run(["tablist", f"{corr_file}[col field_x;field_y;index_x;index_y]"], scratch))
    distances = sorted(math.hypot(fx - ix, fy - iy) for fx, fy, ix, iy in rows if not any(map(math.isnan, (fx, fy, ix, iy))))
    if not distances:
        return len(rows), None
    middle = len(distances) // 2
    median = distances[middle] if len(distances) % 2 else 0.5 * (distances[middle - 1] + distances[middle])
    return len(rows), median


# -------------------------------------------------------------------------------------------------
# One photo
# -------------------------------------------------------------------------------------------------


def oracle_folder(label: str) -> Path:
    return bench_root() / label / "oracle"


def check_photo(
    photo_id: str,
    photo_path: Path,
    attempts: Sequence[Attempt],
    out_folder: Path,
    *,
    limit_s: float = ATTEMPT_TIME_LIMIT_S,
    root: Path = ROOT,
) -> OracleRecord:
    """Run the attempts in order until one solves; keep the record, the logs and the solution's WCS in `out_folder`."""
    require_oracle(root)
    out_folder.mkdir(parents=True, exist_ok=True)
    # What an earlier run that was killed left here (its lock is free); a live run keeps its own
    remove_dead_scratch(out_folder)
    index_dir = root / INDEX_FOLDER
    records: list[AttemptRecord] = []
    started = time.monotonic()
    version = solve_field_version()
    checkout = code_state(root)
    for attempt in attempts:
        with attempt_folder(out_folder, f"{photo_id}-{attempt.label}") as scratch:
            config = scratch / "astrometry.cfg"
            _write_config(config, index_dir, limit_s)
            arguments = [
                SOLVE_FIELD, "--config", str(config), "--dir", str(scratch), "--temp-dir", str(scratch), "--out", "photo",
                "--no-plots", "--new-fits", "none", "--overwrite", *attempt.arguments(), str(photo_path),
            ]  # fmt: skip
            log_path = scratch / "solve.log"
            code, stopped, seconds = run_bounded(arguments, log_path, scratch, limit_s)
            log = log_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_solve_log(log)
            solved = (scratch / "photo.solved").is_file() and (scratch / "photo.wcs").is_file() and not stopped
            records.append(AttemptRecord(attempt.label, solved, round(seconds, 2), stopped, code, parsed.get("sources")))
            (out_folder / f"{photo_id}.{attempt.label}.log").write_text(log, encoding="utf-8")
            if not solved:
                continue
            kept_wcs = out_folder / f"{photo_id}.wcs"
            shutil.copyfile(scratch / "photo.wcs", kept_wcs)
            info = parse_wcsinfo(_run(["wcsinfo", str(kept_wcs)], scratch))
            pairs, residual = _check_residual_px(scratch / "photo.corr", scratch)
            width, height = int(info["imagew"]), int(info["imageh"])
            record = OracleRecord(
                photo_id=photo_id,
                oracle=ORACLE_NAME,
                version=version,
                solved=True,
                settings={**asdict(attempt), "tweakOrder": TWEAK_ORDER, "crpixCenter": True, "limitS": limit_s},
                attempts=records,
                seconds=round(time.monotonic() - started, 2),
                matches=parsed.get("matches"),
                conflicts=parsed.get("conflicts"),
                distractors=parsed.get("distractors"),
                index_stars=parsed.get("index_stars"),
                log_odds=parsed.get("log_odds"),
                index_file=parsed.get("index_file"),
                correspondences=pairs,
                check_median_residual_px=None if residual is None else round(residual, 3),
                centre_ra_deg=float(info["ra_center"]),
                centre_dec_deg=float(info["dec_center"]),
                field_width_deg=float(info["fieldw"]) if info.get("fieldunits") == "degrees" else None,
                field_height_deg=float(info["fieldh"]) if info.get("fieldunits") == "degrees" else None,
                diagonal_fov_deg=round(diagonal_fov_deg(kept_wcs, width, height, scratch), 4),
                pixel_scale_arcsec=float(info["pixscale"]),
                orientation_deg=float(info["orientation"]),
                parity=parsed.get("parity"),
                image_width_px=width,
                image_height_px=height,
                wcs_file=kept_wcs.relative_to(root).as_posix() if kept_wcs.is_relative_to(root) else str(kept_wcs),
                code=checkout,
            )
            write_record(record, out_folder)
            return record
    record = OracleRecord(
        photo_id=photo_id,
        oracle=ORACLE_NAME,
        version=version,
        solved=False,
        settings=None,
        attempts=records,
        seconds=round(time.monotonic() - started, 2),
        note=f"not solved by the check with {len(records)} attempt(s) of at most {limit_s:g} s each",
        code=checkout,
    )
    write_record(record, out_folder)
    return record


def write_record(record: OracleRecord, out_folder: Path) -> Path:
    out_folder.mkdir(parents=True, exist_ok=True)
    path = out_folder / f"{record.photo_id}.json"
    path.write_text(json.dumps(record.to_dict(), indent=1) + "\n", encoding="utf-8")
    return path


def read_record(photo_id: str, out_folder: Path) -> Optional[OracleRecord]:
    path = out_folder / f"{photo_id}.json"
    if not path.is_file():
        return None
    return OracleRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def wcs_path(record: OracleRecord, root: Path = ROOT) -> Optional[Path]:
    if not record.solved or record.wcs_file is None:
        return None
    path = Path(record.wcs_file)
    return path if path.is_absolute() else root / path
