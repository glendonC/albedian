"""A clip's runs keep their tile searches by the exact values each read (RESTORE-PHOTO speed-2's `gate.Searches`), for
the restore pass's dissolve passes and for the gate pass, one `Searches` per run (lead, 22 Nov; restore-searches).

A tile is searched again only for values that differ in a bit, so a tile whose values repeat (a repeated frame's)
is answered from its first search. The probe on the public sample's first 30 frames counted 1350 gate searches, 58 of
them repeats within one process [tested: .lanetools/search_probe/].

Covers:
- The gate pass with its run's searches gives every frame the outcome, record and counts it gives with every search
  made afresh, and searches a repeated stored frame's tiles once.
- The restore pass gives every frame the result it gives with every search made afresh, and hands its dissolve passes
  one `Searches` for the whole run.
Scenes are synthetic (test_clip_frames' own drawing).
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from services.media import clip_frames, frame_pool, gate
from test_clip_frames import scene_clip, video


def afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every search made again, the kept ones never answered."""
    monkeypatch.setattr(gate.Searches, "points", lambda self, part, threshold, floor: gate._search_tile(part, threshold, floor))


class Counting:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        search = gate._search_tile

        def counted(*args):
            self.calls += 1
            return search(*args)

        monkeypatch.setattr(gate, "_search_tile", counted)


def same(a, b) -> bool:
    """Equal dataclasses field by field, arrays exactly and tuples of arrays element by element."""
    for item in dataclasses.fields(a):
        x, y = getattr(a, item.name), getattr(b, item.name)
        if isinstance(x, np.ndarray):
            if not np.array_equal(x, y):
                return False
        elif isinstance(x, tuple) and x and isinstance(x[0], np.ndarray):
            if len(x) != len(y) or not all(np.array_equal(p, q) for p, q in zip(x, y)):
                return False
        elif dataclasses.is_dataclass(x) and not isinstance(x, type):
            if not same(x, y):
                return False
        elif x != y:
            return False
    return True


@pytest.fixture(scope="module")
def restored():
    clip = clip_frames.clip_colour(video(width=128, height=128))
    frames = scene_clip(clip, 5, 45, sigma=0.002, sky=0.03, moving=True, size=128)
    measured = clip_frames.measure_clip(clip, frames)
    results = clip_frames.restore_run(measured, frame_pool.Run(0, 0, len(frames), tuple(frames)))
    return measured, frames, results


def gated(measured, pairs):
    return clip_frames.gate_run(measured, frame_pool.Run(0, 0, len(pairs), tuple(pairs)))


def test_the_gate_pass_with_its_runs_searches_answers_as_afresh_and_searches_a_repeated_frame_once(
        restored, monkeypatch) -> None:
    measured, frames, results = restored
    pairs = list(zip(frames, [r.planes for r in results]))
    # The third frame repeats the second, as a clip's repeated frame does
    pairs = pairs[:2] + [pairs[1]] + pairs[2:]
    counting = Counting(monkeypatch)
    kept = gated(measured, pairs)
    with_searches = counting.calls
    afresh(monkeypatch)
    counting.calls = 0
    every = gated(measured, pairs)
    assert counting.calls > with_searches, "the run's gate searched a repeated frame's tiles again"
    for a, b in zip(kept, every):
        assert a.outcome.record == b.outcome.record and a.outcome.unconfirmed == b.outcome.unconfirmed
        assert (a.outcome.changes, a.outcome.ratios, a.outcome.rejection) == (b.outcome.changes, b.outcome.ratios,
                                                                              b.outcome.rejection)
        assert (a.index, a.stars_found, a.stars_confirmed, a.together, a.fixed) == (
            b.index, b.stars_found, b.stars_confirmed, b.together, b.fixed)


def test_the_restore_pass_hands_one_search_store_to_every_frames_dissolve_and_answers_as_afresh(
        restored, monkeypatch) -> None:
    measured, frames, results = restored
    handed = []
    dissolve = gate.dissolve

    def spied(*args, **kwargs):
        handed.append(kwargs.get("searches"))
        return dissolve(*args, **kwargs)

    monkeypatch.setattr(gate, "dissolve", spied)
    again = clip_frames.restore_run(measured, frame_pool.Run(0, 0, len(frames), tuple(frames)))
    assert handed, "the control: the frames went through the dissolve passes"
    assert all(isinstance(s, gate.Searches) for s in handed) and len({id(s) for s in handed}) == 1, \
        "the run's dissolve passes were not handed one Searches"
    afresh(monkeypatch)
    every = clip_frames.restore_run(measured, frame_pool.Run(0, 0, len(frames), tuple(frames)))
    for a, b, c in zip(results, again, every):
        assert same(a, b) and same(b, c)
