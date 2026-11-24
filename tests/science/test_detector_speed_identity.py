"""detector-speed-1: three changes for speed that leave every detection as it was (detector-speed-profile).

- The core search: a point is tested with `math.hypot` only against the cores whose box (the grown radius either way
  in x and in y) holds it, built once per call; a faithfully rounded hypot is never below |dx| or |dy|, so a core the
  box leaves out fails the test too, and the first core that passes is the same one.
- Dilation and erosion by an odd square on a whole frame as max and min filters with a zero border, which they equal
  for booleans.
- The per-peak 5 x 5 grid as a fixed grid plus the peak's offset: the same int64 values.

Each is held against its reference: the brute-force search, scipy's binary morphology and np.mgrid, and the whole
detector run with the references put back must return the same records, compared exactly in one process (so on any
platform). A digest pinned across platforms would compare floats byte for byte, which these tests never do.
"""

from __future__ import annotations

import math

from hypothesis import example, given, settings
from hypothesis import strategies as st
import numpy as np
import pytest
from scipy import ndimage

from services.science import detector
from services.science.detector import ClippedCore


def _brute_first_core(x: float, y: float, cores) -> int | None:
    """The search as it was: every core in order, the first whose disk grown by one pixel holds the point."""
    return next(
        (i for i, c in enumerate(cores) if math.hypot(x - c.x_px, y - c.y_px) <= math.sqrt(c.area_px / math.pi) + 1.0),
        None,
    )


core_st = st.builds(
    ClippedCore,
    x_px=st.floats(0.0, 60.0, allow_nan=False),
    y_px=st.floats(0.0, 60.0, allow_nan=False),
    area_px=st.integers(1, 400),
    spread_px=st.just(1.0),
)


@settings(max_examples=400, deadline=None)
@given(cores=st.lists(core_st, max_size=40), x=st.floats(-5.0, 65.0, allow_nan=False), y=st.floats(-5.0, 65.0, allow_nan=False))
@example(cores=[], x=1.0, y=1.0)
# On the grown radius exactly, along each axis either way (a core at the origin, so dx or dy is the radius itself),
# and one ulp past it
@example(cores=[ClippedCore(0.0, 0.0, 1, 1.0)], x=math.sqrt(1 / math.pi) + 1.0, y=0.0)
@example(cores=[ClippedCore(0.0, 0.0, 1, 1.0)], x=-(math.sqrt(1 / math.pi) + 1.0), y=0.0)
@example(cores=[ClippedCore(0.0, 0.0, 1, 1.0)], x=0.0, y=math.sqrt(1 / math.pi) + 1.0)
@example(cores=[ClippedCore(0.0, 0.0, 1, 1.0)], x=0.0, y=-(math.sqrt(1 / math.pi) + 1.0))
@example(cores=[ClippedCore(0.0, 0.0, 1, 1.0)], x=math.nextafter(math.sqrt(1 / math.pi) + 1.0, 9.0), y=0.0)
# Inside the box, outside the disk (the corner); and two cores that both hold the point, the first wins
@example(cores=[ClippedCore(10.0, 10.0, 50, 1.0)], x=14.5, y=14.5)
@example(cores=[ClippedCore(10.0, 10.0, 50, 1.0), ClippedCore(11.0, 10.0, 50, 1.0)], x=10.5, y=10.0)
def test_pbt_the_core_search_finds_the_brute_force_first_core(cores: list, x: float, y: float) -> None:
    boxes = detector._core_boxes(cores)
    assert detector._first_core(x, y, cores, boxes) == _brute_first_core(x, y, cores)
    assert detector._inside_a_core(x, y, cores, boxes) == (_brute_first_core(x, y, cores) is not None)
    assert detector._inside_a_core(x, y, cores) == (_brute_first_core(x, y, cores) is not None)


masks = st.integers(1, 30).flatmap(
    lambda h: st.integers(1, 30).flatmap(
        lambda w: st.lists(st.booleans(), min_size=h * w, max_size=h * w).map(lambda v: np.array(v, dtype=bool).reshape(h, w))
    )
)


@settings(max_examples=300, deadline=None)
@given(mask=masks, side=st.sampled_from([1, 3, 5, 7, 9, 11]))
@example(mask=np.zeros((7, 9), dtype=bool), side=7)
@example(mask=np.ones((7, 9), dtype=bool), side=7)
@example(mask=np.eye(5, dtype=bool)[::-1], side=3)
@example(mask=np.ones((3, 3), dtype=bool), side=11)
def test_pbt_square_morphology_is_scipys_binary_morphology(mask: np.ndarray, side: int) -> None:
    square = np.ones((side, side), dtype=bool)
    dilated = detector._dilate_square(mask, side)
    eroded = detector._erode_square(mask, side)
    assert dilated.dtype == bool and eroded.dtype == bool
    assert np.array_equal(dilated, ndimage.binary_dilation(mask, structure=square))
    assert np.array_equal(eroded, ndimage.binary_erosion(mask, structure=square, border_value=0))


@pytest.mark.parametrize(("y0", "x0"), [(2, 2), (7, 300), (4000, 5)])
def test_the_peak_grid_is_np_mgrid_around_the_peak(y0: int, x0: int) -> None:
    yy, xx = detector._peak_grid(y0, x0)
    want_y, want_x = np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
    assert yy.dtype == want_y.dtype and xx.dtype == want_x.dtype
    assert np.array_equal(yy, want_y) and np.array_equal(xx, want_x)


def _field(seed: int) -> np.ndarray:
    """An 8-bit night field of 480 x 640: a sky of 20 with noise correlated over a few pixels, 260 stars of 12 to 150
    levels, and 90 clipped disks of radius 1.5 to 6 at 255 (cores), every value rounded to a whole level."""
    rng = np.random.default_rng(seed)
    h, w = 480, 640
    noise = ndimage.gaussian_filter(rng.normal(0.0, 6.0, (h, w)), 1.2)
    sky = 20.0 + noise
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(260):
        cx, cy, amp = rng.uniform(6, w - 6), rng.uniform(6, h - 6), rng.uniform(12.0, 150.0)
        sky += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.3**2))
    for _ in range(90):
        cx, cy, radius = rng.uniform(8, w - 8), rng.uniform(8, h - 8), rng.uniform(1.5, 6.0)
        sky[(xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2] = 255.0
    return np.clip(np.round(sky), 0.0, 255.0)


@pytest.mark.parametrize("seed", [3, 11])
def test_the_detector_returns_the_same_records_with_the_references_put_back(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    luma = _field(seed)
    assert len(detector._clipped_cores(luma)) >= 40
    found = detector.detect_point_sources(luma, asset_id="src_synthetic")
    verified = detector.verify_candidates_on_original_pixels(luma, [(d.x_px, d.y_px) for d in found])

    calls = {"dilate": 0, "erode": 0, "core": 0}

    def dilate(mask, side):
        calls["dilate"] += 1
        return ndimage.binary_dilation(mask, structure=np.ones((side, side), dtype=bool))

    def erode(mask, side):
        calls["erode"] += 1
        return ndimage.binary_erosion(mask, structure=np.ones((side, side), dtype=bool), border_value=0)

    def first_core(x, y, cores, boxes):
        calls["core"] += 1
        return _brute_first_core(x, y, cores)

    def peak_grid(y0, x0):
        return tuple(np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3])

    monkeypatch.setattr(detector, "_dilate_square", dilate)
    monkeypatch.setattr(detector, "_erode_square", erode)
    monkeypatch.setattr(detector, "_first_core", first_core)
    monkeypatch.setattr(detector, "_peak_grid", peak_grid)
    assert detector.detect_point_sources(luma, asset_id="src_synthetic") == found
    assert detector.verify_candidates_on_original_pixels(luma, [(d.x_px, d.y_px) for d in found]) == verified
    # The references ran: every changed step is on this field's path
    assert calls["dilate"] > 0 and calls["erode"] > 0 and calls["core"] > 0
    assert len(found) > 100
