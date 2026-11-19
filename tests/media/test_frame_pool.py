"""The bounded pool a clip's frames run in (design section 10, "Frames in a pool of worker processes"; REQ-RST-7.7,
8.9): runs of consecutive frames, each with the neighbours its window needs, handed to at most `workers` processes
at a time, their results back in frame order, the same whatever the pool's size, and each worker's peak memory.

The work functions are module-level, so a spawned worker imports them by this module's name. Every test uses at most
two workers: the lane's limit of two processes for test runs (the default pool is for the product, design section 10).
"""

from __future__ import annotations

import multiprocessing
import os
import time

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from services.media import clip_io, frame_pool, temporal
from test_photo_path import SOURCE_MARKERS, numeric_constants


def frame_numbers(count: int) -> list[int]:
    return list(range(count))


# ---------------------------------------------------------------------------------------------------------------
# The runs
# ---------------------------------------------------------------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(count=st.integers(0, 60), run_frames=st.integers(1, 10), window=st.integers(0, 4))
@example(count=0, run_frames=8, window=3)
@example(count=1, run_frames=8, window=3)
@example(count=17, run_frames=8, window=3)
@example(count=16, run_frames=8, window=0)
def test_the_runs_cover_every_frame_once_in_order_with_the_neighbours_their_window_needs(count: int, run_frames: int,
                                                                                         window: int) -> None:
    frames = frame_numbers(count)
    made = list(frame_pool.runs(iter(frames), run_frames=run_frames, window=window))
    produced = [run.first + k for run in made for k in range(run.count)]
    assert produced == frames
    for run in made:
        assert 1 <= run.count <= run_frames
        assert run.before == min(window, run.first)
        after = min(window, count - (run.first + run.count))
        assert list(run.frames) == frames[run.first - run.before: run.first + run.count + after]
        # The run's own frames, without the neighbours
        assert list(run.own()) == frames[run.first: run.first + run.count]


def test_a_run_is_handed_out_before_the_frames_after_its_window_are_read() -> None:
    read = []

    def source():
        for k in range(40):
            read.append(k)
            yield k

    for run in frame_pool.runs(source(), run_frames=8, window=3):
        # The frames read so far end at the last neighbour this run needs
        last_needed = run.first + run.count + 3 - 1
        assert max(read) <= min(39, last_needed)
    assert read == list(range(40))


def test_the_runs_hold_no_more_frames_than_one_run_and_its_window() -> None:
    held = frame_pool.held_frames(run_frames=8, window=3)
    assert held == 8 + 2 * 3
    assert frame_pool.RUN_FRAMES == 8 and frame_pool.WINDOW_FRAMES == temporal.WINDOW_FRAMES


# ---------------------------------------------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------------------------------------------


def square_own(run: frame_pool.Run) -> list[int]:
    return [value * value for value in run.own()]


def slow_first(run: frame_pool.Run) -> list[int]:
    # Earlier runs take longer, so later runs finish first
    time.sleep(0.15 if run.first == 0 else 0.0)
    return [value * value for value in run.own()]


def neighbours_sum(run: frame_pool.Run) -> list[float]:
    """A stand-in for the frame work: each output frame is a sum over its window, as the merge reads its neighbours."""
    values = list(run.frames)
    out = []
    for k in range(run.count):
        centre = run.before + k
        out.append(float(np.sum(values[max(0, centre - 3): centre + 4])))
    return out


def fails_at_three(run: frame_pool.Run) -> list[int]:
    if run.first <= 3 < run.first + run.count:
        raise ValueError("run with frame 3")
    return list(run.own())


def holds_memory(run: frame_pool.Run) -> list[int]:
    block = np.ones(48 * 2**20, dtype=np.uint8)
    return [int(block[0]) + value for value in run.own()]


def own_pid(run: frame_pool.Run) -> list[int]:
    return [os.getpid() for _ in run.own()]


# A worker's exit is waited for at most this long [proposed: the executor joins its workers on shutdown, so they are gone
# at once; ten seconds covers a loaded machine]
EXIT_BOUND_SEC = 10.0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def workers_gone(pool: frame_pool.FramePool) -> bool:
    """The pool started workers, and every one of them, by its own process id, is gone within the bound. Other
    tests' processes are not looked at."""
    pids = pool.worker_pids
    assert pids and os.getpid() not in pids
    deadline = time.monotonic() + EXIT_BOUND_SEC
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not any(alive(pid) for pid in pids)


@pytest.mark.parametrize("workers", [1, 2])
def test_results_come_back_in_frame_order(workers: int) -> None:
    frames = frame_numbers(30)
    with frame_pool.FramePool(slow_first, workers=workers) as pool:
        out = list(pool.map(frame_pool.runs(iter(frames), run_frames=4, window=3)))
    assert out == [value * value for value in frames]
    assert workers_gone(pool) and len(pool.worker_pids) <= workers


def test_the_result_is_the_same_whatever_the_pools_size() -> None:
    frames = frame_numbers(45)
    results = []
    for workers in (1, 2):
        with frame_pool.FramePool(neighbours_sum, workers=workers) as pool:
            results.append(list(pool.map(frame_pool.runs(iter(frames), run_frames=8, window=3))))
    serial = [float(np.sum(frames[max(0, k - 3): k + 4])) for k in frames]
    assert results[0] == results[1] == serial


def test_no_more_runs_are_handed_out_than_there_are_workers() -> None:
    drawn = []

    def counted(source):
        for run in source:
            drawn.append(run.first)
            yield run

    with frame_pool.FramePool(square_own, workers=2) as pool:
        for k, _ in enumerate(pool.map(counted(frame_pool.runs(iter(frame_numbers(40)), run_frames=1, window=0)))):
            # When result k is handed back, runs 0 to k are done and at most two more are in the pool
            assert len(drawn) <= k + 1 + 2
    assert len(drawn) == 40


def test_the_work_runs_in_the_pools_own_processes() -> None:
    with frame_pool.FramePool(own_pid, workers=2) as pool:
        pids = set(pool.map(frame_pool.runs(iter(frame_numbers(24)), run_frames=2, window=0)))
    assert os.getpid() not in pids and 1 <= len(pids) <= 2
    # The workers that ran the work are the ones the pool says it started
    assert pids <= pool.worker_pids and workers_gone(pool)


def test_a_failure_in_a_run_ends_the_pool_and_reaches_the_caller() -> None:
    pool = frame_pool.FramePool(fails_at_three, workers=2)
    with pytest.raises(ValueError, match="run with frame 3"):
        with pool:
            list(pool.map(frame_pool.runs(iter(frame_numbers(20)), run_frames=2, window=1)))
    assert workers_gone(pool)


def test_leaving_early_ends_the_workers() -> None:
    with frame_pool.FramePool(square_own, workers=2) as pool:
        for value in pool.map(frame_pool.runs(iter(frame_numbers(200)), run_frames=1, window=0)):
            if value >= 9:
                break
    assert workers_gone(pool)


def test_the_pool_records_its_workers_peak_memory() -> None:
    with frame_pool.FramePool(holds_memory, workers=2) as pool:
        list(pool.map(frame_pool.runs(iter(frame_numbers(6)), run_frames=2, window=0)))
    # Each worker held 48 MiB at once; the parent held none of it
    assert pool.worker_peak_bytes >= 48 * 2**20
    with frame_pool.FramePool(square_own, workers=1) as idle:
        list(idle.map(frame_pool.runs(iter(frame_numbers(2)), run_frames=2, window=0)))
    assert 0 < idle.worker_peak_bytes < pool.worker_peak_bytes


def peak_after(run: frame_pool.Run) -> list[int]:
    """Run 0 holds 64 MiB and stays 3 s, so the other worker takes run 1; each reports its worker's own peak."""
    if run.first == 0:
        block = np.ones(64 * 2**20, dtype=np.uint8)
        time.sleep(3.0)
        del block
    return [clip_io.peak_memory().self_bytes for _ in run.own()]


def sleep_a_while(seconds: float) -> None:
    time.sleep(seconds)


def test_another_live_process_does_not_count_against_the_pool() -> None:
    # A child of the test process that is none of the pool's, alive throughout, as another test's worker can be
    other = multiprocessing.get_context("spawn").Process(target=sleep_a_while, args=(30.0,))
    other.start()
    try:
        with frame_pool.FramePool(square_own, workers=2) as pool:
            list(pool.map(frame_pool.runs(iter(frame_numbers(8)), run_frames=2, window=0)))
        assert other.pid not in pool.worker_pids
        assert workers_gone(pool) and other.is_alive()
    finally:
        other.terminate()
        other.join(EXIT_BOUND_SEC)


def test_the_pools_peak_is_the_largest_of_its_workers_not_the_last() -> None:
    with frame_pool.FramePool(peak_after, workers=2) as pool:
        peaks = list(pool.map(frame_pool.runs(iter(frame_numbers(2)), run_frames=1, window=0)))
    # The last report is the lighter worker's
    assert peaks[0] > peaks[1]
    assert pool.worker_peak_bytes >= peaks[0]


def test_a_pool_of_no_workers_is_refused() -> None:
    with pytest.raises(ValueError):
        frame_pool.FramePool(square_own, workers=0)


@pytest.mark.parametrize("cores,expected", [(None, 1), (1, 1), (2, 1), (3, 1), (4, 2), (18, 9)])
def test_the_default_pool_is_half_the_cores_and_at_least_one(monkeypatch: pytest.MonkeyPatch, cores, expected) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: cores)
    assert frame_pool.default_workers() == expected


def test_every_numeric_constant_of_the_pool_names_its_source() -> None:
    constants = numeric_constants(frame_pool)
    assert constants
    assert [f"{line} {name}" for name, line, comment in constants if not any(m in comment for m in SOURCE_MARKERS)] == []
