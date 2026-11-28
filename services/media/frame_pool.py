"""The bounded pool a clip's frames run in (spec restoration-for-every-upload, design section 10, "Frames in a pool of
worker processes"; REQ-RST-7.7, 8.9).

The job's child decodes the clip once, in order, and cuts it into runs of consecutive frames, each with the
neighbours either side that its window needs. A pool of worker processes does the work between decode and encode for
one run at a time each; no more runs are handed out than there are workers, so memory does not grow with the clip,
and the results come back in frame order for the one encoder. The workers are started with `spawn`, so none inherits
the child's threads, and they stay in the child's process group, so a cancel or the time bound that stops the child's
group stops them (the upload jobs spec, design 3.3). Each worker reports its own peak resident size.
"""

from __future__ import annotations

import multiprocessing
import os
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

from services.media import clip_io, temporal

# Runs of 8 frames [proposed: design section 10, "runs of 8 frames"]
RUN_FRAMES = 8
# The neighbours either side a run carries: the temporal window's own
WINDOW_FRAMES = temporal.WINDOW_FRAMES
# The default pool is the machine's cores less this many, and at least one worker, so the app and the machine keep
# two [decided by the lead at the 16:24 stamp of 27 Nov, clip-workers-one, only as the stored files stay the same; it was
# half the cores, design section 10]
FREE_CORES = 2
# The playback copy's encoder keeps the machine's cores divided by this, and at least one thread: x264's bytes depend
# on its threads, and these are the threads it had when the pool was half the cores [tested: clip-workers-one, the
# public clip's copy another SHA-256 at 6, 9 and 16 threads, its stored file the same]
ENCODER_CORE_SHARE = 2
# The name of the environment variable that holds the pool under its default [decided 2026-11-11 in design section
# 10: the limit of two processes is for agents' test runs; the product sets nothing]
WORKERS_LIMIT_ENV = "ALBEDIAN_MEDIA_WORKERS"
# Each worker's maths libraries run on one thread: a library that threads each worker (OpenBLAS on Linux) would ask
# for workers times cores [decided 2026-11-25 by the lead, pool-threads-one: on the public clip at the default pool the
# cap at one was no slower than none and the stored file and records identical to the byte; numpy on this laptop's
# Accelerate threads none of these calls, so it is a guard]. Set for the workers as they start, never for the caller.
INNER_THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                          "NUMEXPR_NUM_THREADS")
INNER_THREADS = "1"  # [decided 2026-11-25 by the lead, pool-threads-one]
# How often the pool reads its counters while it waits for a run's results [proposed: a person sees a row move about
# every second; the public clip's frames take 3 to 6 s each in a worker, so 6 workers finish one about every second]
COUNT_POLL_SEC = 0.5

# In a worker: the counters its pool shares with it, by stage (clip-speed-one); none outside a pool that counts
_COUNTERS: dict = {}


def _count_into(counters: dict) -> None:
    global _COUNTERS
    _COUNTERS = counters


def frame_done(stage: str) -> None:
    """In a worker: one more frame of `stage` finished, counted at once in the counter its pool shares, so the job
    reports it while the worker's run goes on; outside a pool that counts `stage`, nothing."""
    counter = _COUNTERS.get(stage)
    if counter is not None:
        with counter.get_lock():
            counter.value += 1


@dataclass(frozen=True)
class Run:
    """Consecutive frames `first` to `first + count - 1`, with `before` neighbours ahead of them and the neighbours after
    them that the clip has, up to the window, all in `frames`."""

    first: int
    before: int
    count: int
    frames: tuple[Any, ...]

    def own(self) -> tuple[Any, ...]:
        """The run's own frames, without its neighbours."""
        return self.frames[self.before:self.before + self.count]


def held_frames(*, run_frames: int = RUN_FRAMES, window: int = WINDOW_FRAMES) -> int:
    """The most frames `runs` holds at once: one run and the window either side."""
    return run_frames + 2 * window


def runs(frames: Iterable[Any], *, run_frames: int = RUN_FRAMES, window: int = WINDOW_FRAMES) -> Iterator[Run]:
    """The clip's frames cut into runs of `run_frames`, the last one shorter, each carrying up to `window` neighbours
    either side. A frame is read only when a run needs it, so at most `held_frames` are held."""
    if run_frames < 1 or window < 0:
        raise ValueError("a run holds one frame or more and a window is not negative")
    source = iter(frames)
    held: deque[Any] = deque()
    base = 0  # the index of held[0]
    read = 0  # how many frames have been read
    ended = False
    first = 0
    while True:
        # Every frame up to this run's last neighbour, or the clip's end
        while not ended and read < first + run_frames + window:
            try:
                held.append(next(source))
            except StopIteration:
                ended = True
                break
            read += 1
        if read <= first:
            return
        count = min(run_frames, read - first)
        start = max(0, first - window)
        while base < start:
            held.popleft()
            base += 1
        end = min(read, first + count + window)
        yield Run(first, first - start, count, tuple(held[k - base] for k in range(start, end)))
        first += count


def encoder_threads() -> int:
    """The playback copy's encoder threads: the machine's cores over ENCODER_CORE_SHARE, and at least one; fewer where
    WORKERS_LIMIT_ENV holds a smaller whole number above 0, as the pool, never more. Its own, so the copy's bytes do
    not move with the pool's size."""
    threads = max(1, (os.cpu_count() or 1) // ENCODER_CORE_SHARE)
    limit = os.environ.get(WORKERS_LIMIT_ENV, "")
    if limit.isdigit() and int(limit) >= 1:
        return min(threads, int(limit))
    return threads


def default_workers() -> int:
    """The machine's cores less FREE_CORES, and at least one; fewer where WORKERS_LIMIT_ENV holds a smaller whole number
    above 0 (agents' test runs, which stay within two processes), never more."""
    workers = max(1, (os.cpu_count() or 1) - FREE_CORES)
    limit = os.environ.get(WORKERS_LIMIT_ENV, "")
    if limit.isdigit() and int(limit) >= 1:
        return min(workers, int(limit))
    return workers


def _measured(work: Callable[[Run], Sequence[Any]], run: Run) -> tuple[list[Any], int]:
    """A worker's turn: the run's results, and the worker's own peak resident size so far."""
    results = list(work(run))
    return results, clip_io.peak_memory().self_bytes


class FramePool:
    """`with FramePool(work, workers=n) as pool: for result in pool.map(runs(...)): ...`. `work` is a module-level
    function of one run, returning one result per frame of the run's own. At most `workers` runs are in the pool at
    once; results come in frame order; a failure in any run ends the pool and reaches the caller."""

    def __init__(self, work: Callable[[Run], Sequence[Any]], *, workers: int, counted: Sequence[str] = (),
                 on_count: Optional[Callable[[str, int], None]] = None) -> None:
        if workers < 1:
            raise ValueError("a pool has one worker or more")
        self._work = work
        self._workers = workers
        # The stages its workers count with `frame_done`, and the caller told of each rise, one frame at a time
        self._counted = tuple(counted)
        self._on_count = on_count
        self._counters: dict = {}
        self._reported: dict = {}
        self._executor: Optional[ProcessPoolExecutor] = None
        self._worker_pids: set[int] = set()
        self._callers_threads: dict[str, Optional[str]] = {}
        # The largest peak resident size any worker reported, in bytes
        self.worker_peak_bytes = 0

    def __enter__(self) -> "FramePool":
        # The job's media child is a daemonic process (services/science/bounded_worker.py), which multiprocessing
        # forbids to start children. It clears its own flag here; its parent's view of it is unchanged, so the parent
        # still ends it at its own exit, and a stop of the child's group still ends these workers with it
        # [tested: .lanetools/restore7_daemon_pool.py, tests/media/test_frame_pool.py]
        current = multiprocessing.current_process()
        if current.daemon:
            current.daemon = False
        # The workers start with `spawn` from this process's environment, so the cap is set here for the pool's life
        # (a worker may start late) and the caller's own values come back at its end
        self._callers_threads = {name: os.environ.get(name) for name in INNER_THREAD_VARIABLES}
        os.environ.update({name: INNER_THREADS for name in INNER_THREAD_VARIABLES})
        context = multiprocessing.get_context("spawn")
        self._counters = {stage: context.Value("q", 0) for stage in self._counted}
        self._reported = {stage: 0 for stage in self._counted}
        if self._counters:
            self._executor = ProcessPoolExecutor(max_workers=self._workers, mp_context=context, initializer=_count_into,
                                                 initargs=(self._counters,))
        else:
            self._executor = ProcessPoolExecutor(max_workers=self._workers, mp_context=context)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._executor is not None:
            self._note_workers()
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        for name, value in self._callers_threads.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._callers_threads = {}

    def _note_workers(self) -> None:
        """Keep the process ids of the executor's workers, read before it shuts down. CPython's
        ProcessPoolExecutor holds them in `_processes`, process id to process [verified: CPython 3.12 and 3.14,
        Lib/concurrent/futures/process.py]."""
        processes = getattr(self._executor, "_processes", None) or {}
        self._worker_pids.update(int(pid) for pid in processes)

    def _report(self) -> None:
        """Each counted stage's rise since the last read, told to the caller one frame at a time."""
        for stage, counter in self._counters.items():
            value = counter.value
            while self._reported[stage] < value:
                self._reported[stage] += 1
                if self._on_count is not None:
                    self._on_count(stage, self._reported[stage])

    def _results(self, future: Future) -> tuple[list[Any], int]:
        """A run's results, the counters read while they are awaited and once more after: a worker counts a frame
        before it returns, so every frame of the run is reported before its results are handed back."""
        if not self._counters:
            return future.result()
        while True:
            try:
                out = future.result(timeout=COUNT_POLL_SEC)
            except FutureTimeout:
                self._report()
                continue
            self._report()
            return out

    @property
    def worker_pids(self) -> frozenset[int]:
        """Every worker process this pool started, by process id."""
        return frozenset(self._worker_pids)

    def map(self, source: Iterable[Run]) -> Iterator[Any]:
        if self._executor is None:
            raise RuntimeError("the pool is used inside its with block")
        executor = self._executor
        pending: deque[Future] = deque()
        upcoming = iter(source)

        def hand_out() -> None:
            for run in upcoming:
                pending.append(executor.submit(_measured, self._work, run))
                return

        for _ in range(self._workers):
            hand_out()
        while pending:
            results, peak = self._results(pending.popleft())
            self.worker_peak_bytes = max(self.worker_peak_bytes, peak)
            # The next run goes in before these results are handed back, so the pool stays busy meanwhile
            hand_out()
            yield from results
