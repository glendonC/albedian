"""Black bars round a picture (photo-bars-one; DECIDED-BY-LEAD 2026-11-24 13:25 and 13:36): whole edge rows or columns
of one code at or below black on both opposite edges, each opening the picture, found by the one helper stills and
clips share.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.media import bars


def sky(height: int, width: int, seed: int = 1, level: int = 40) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(level + rng.normal(0.0, 3.0, (height, width)), 1, 255).astype(np.int64)


def padded(plane: np.ndarray, top: int, bottom: int, left: int, right: int, code: int = 0) -> np.ndarray:
    return np.pad(plane, ((top, bottom), (left, right)), constant_values=code)


def test_bars_on_both_opposite_edges_that_open_the_picture_are_found() -> None:
    picture = sky(40, 60)
    assert bars.find_bars([padded(picture, 7, 5, 0, 0)], 0) == bars.Bars(top=7, bottom=5)
    assert bars.find_bars([padded(picture, 0, 0, 3, 9)], 0) == bars.Bars(left=3, right=9)
    assert bars.find_bars([padded(picture, 4, 4, 6, 2)], 0) == bars.Bars(4, 4, 6, 2)


def test_no_bars_for_a_band_on_one_edge_only_a_picture_without_any_or_a_frame_all_black() -> None:
    picture = sky(40, 60)
    assert not bars.find_bars([picture], 0)
    assert not bars.find_bars([padded(picture, 8, 0, 0, 0)], 0)
    assert not bars.find_bars([padded(picture, 0, 0, 0, 11)], 0)
    assert not bars.find_bars([np.zeros((30, 30), dtype=np.int64)], 0)


def test_a_band_above_black_or_of_more_than_one_code_is_no_bar() -> None:
    picture = sky(40, 60)
    assert not bars.find_bars([padded(picture, 5, 5, 0, 0, code=1)], 0)
    assert bars.find_bars([padded(picture, 5, 5, 0, 0, code=16)], 16) == bars.Bars(top=5, bottom=5)
    noisy_bar = padded(picture, 5, 5, 0, 0)
    noisy_bar[2, 30] = 1
    assert bars.find_bars([noisy_bar], 0) == bars.Bars()
    # At or below black is not enough: a bar is one code (a limited-range clip's 15s and 16s are no bar)
    mixed = padded(picture, 5, 5, 0, 0, code=16)
    mixed[3, ::2] = 15
    assert bars.find_bars([mixed], 16) == bars.Bars()


def test_a_black_sky_reaching_the_edges_is_no_bar_since_the_row_past_it_does_not_open_the_picture() -> None:
    # A starry sky at black: whole edge rows at code 0, and the rows past them black too but for a star or two
    rng = np.random.default_rng(5)
    plane = np.zeros((64, 64), dtype=np.int64)
    plane[20:40] = sky(20, 64, seed=6)
    for y, x in ((10, 20), (50, 31), (53, 7)):
        plane[y, x] = 200
    plane[45:48, 10:12] = rng.integers(1, 3, (3, 2))
    assert not bars.find_bars([plane], 0)


def test_every_measure_frame_must_hold_the_bar() -> None:
    picture = sky(40, 60)
    held, broken = padded(picture, 6, 6, 0, 0), padded(picture, 6, 6, 0, 0)
    broken[1, 5] = 9
    assert bars.find_bars([held, held], 0) == bars.Bars(top=6, bottom=6)
    assert bars.find_bars([held, broken], 0) == bars.Bars(top=0, bottom=0)


def test_bars_are_counted_toward_the_edge_to_the_grid_given() -> None:
    picture = sky(40, 60)
    assert bars.find_bars([padded(picture, 7, 5, 3, 9)], 0, down=2, across=2) == bars.Bars(6, 4, 2, 8)
    assert bars.find_bars([padded(picture, 1, 5, 0, 0)], 0, down=2) == bars.Bars()


@settings(max_examples=25, deadline=None)
@given(top=st.integers(0, 9), bottom=st.integers(0, 9), left=st.integers(0, 9), right=st.integers(0, 9),
       down=st.sampled_from([1, 2]), across=st.sampled_from([1, 2]))
def test_crop_and_put_back_return_the_picture_and_the_whole_frame_exactly(top, bottom, left, right, down, across) -> None:
    found = bars.Bars(top, bottom, left, right)
    whole = np.arange(30 * 40 * 3, dtype=np.int64).reshape(30, 40, 3)
    rows, columns = found.slices(whole.shape, down, across)
    inner = found.crop(whole, down, across)
    assert np.array_equal(inner, whole[rows, columns]) and inner.flags.c_contiguous
    assert np.array_equal(found.put_back(whole, inner, down, across), whole)
    changed = found.put_back(whole, inner + 1, down, across)
    assert np.array_equal(changed[rows, columns], inner + 1)
    outside = np.ones(whole.shape[:2], dtype=bool)
    outside[rows, columns] = False
    assert np.array_equal(changed[outside], whole[outside])


def test_no_bars_is_false_and_crops_nothing() -> None:
    none = bars.Bars()
    assert not none and bool(bars.Bars(bottom=1))
    whole = np.arange(12).reshape(3, 4)
    assert np.array_equal(none.crop(whole), whole)


def test_find_bars_needs_its_frames() -> None:
    assert not bars.find_bars([], 0)
    with pytest.raises(ValueError):
        bars.find_bars([np.zeros((4, 4)), np.zeros((5, 4))], 0)
