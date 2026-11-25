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
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

from services.media import clip_io, temporal

# Runs of 8 frames [proposed: design section 10, "runs of 8 frames"]
RUN_FRAMES = 8
# The neighbours either side a run carries: the temporal window's own
WINDOW_FRAMES = temporal.WINDOW_FRAMES
# The default pool is the machine's cores divided by this, and at least one worker [proposed: design section 10, "half
# the machine's cores and at least one"]
CORE_SHARE = 2
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


def default_workers() -> int:
    """Half the machine's cores, and at least one; fewer where WORKERS_LIMIT_ENV holds a smaller whole number above 0
    (agents' test runs, which stay within two processes), never more."""
    workers = max(1, (os.cpu_count() or 1) // CORE_SHARE)
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

    def __init__(self, work: Callable[[Run], Sequence[Any]], *, workers: int) -> None:
        if workers < 1:
            raise ValueError("a pool has one worker or more")
        self._work = work
        self._workers = workers
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
        self._executor = ProcessPoolExecutor(max_workers=self._workers, mp_context=multiprocessing.get_context("spawn"))
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
            results, peak = pending.popleft().result()
            self.worker_peak_bytes = max(self.worker_peak_bytes, peak)
            # The next run goes in before these results are handed back, so the pool stays busy meanwhile
            hand_out()
            yield from results
