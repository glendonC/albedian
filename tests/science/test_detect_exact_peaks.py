"""detect-exact-peaks-1: three of the detector's whole-frame max and min filters made exactly, with no result moved.

- `_clipped_flats`: a 3 x 3 max of the luma is at or under the frame's lowest value exactly when every pixel of the
  window is, so the test is the 3 x 3 min of the mask `luma <= low`; likewise at the top. The filters run on bytes.
- `_negative_count`'s `near_source`: a 7 x 7 max is at or over the threshold exactly when one of its pixels is, so it
  is the 7 x 7 max of the mask `snr >= threshold` (`_reaches`).
- The local maxima of `_negative_count` (7 x 7, on the negative image) and `_profile_stars` (9 x 9): a pixel is kept
  where it equals its window's maximum and passes the other tests, so the maximum is read only at the pixels that pass
  them (`_local_maxima`), the window's rows and columns clamped to the frame, which is scipy's "nearest" border.

Max and min are exact, so each rewrite returns the same booleans as main's expression wherever the array holds no NaN;
an array holding a NaN takes main's expression itself, whose NaN handling the reads at candidates would not repeat.
Each is held against main's expression on frames drawn from a few values (ties, plateaus, the frame's lowest and
highest, signed zeros, infinities, NaN) at every edge distance, and the whole detector with main's expressions put
back must return the same records, compared exactly in one process (so on any platform; no float is pinned).
"""

from __future__ import annotations

import math

from hypothesis import example, given, settings
from hypothesis import strategies as st
import numpy as np
import pytest
from scipy import ndimage

from services.science import detector


def _main_local_maxima(values: np.ndarray, candidate: np.ndarray, side: int) -> np.ndarray:
    """Main's peaks, as `_negative_count` and `_profile_stars` wrote them: the whole frame's max filter."""
    return (values == ndimage.maximum_filter(values, size=side, mode="nearest")) & candidate


def _main_reaches(values: np.ndarray, threshold: float, side: int) -> np.ndarray:
    """Main's `near_source`: the whole frame's max filter against the threshold."""
    return ndimage.maximum_filter(values, size=side, mode="nearest") >= float(threshold)


def _main_clipped_flats(luma: np.ndarray) -> np.ndarray:
    """Main's `_clipped_flats` body, as it was."""
    low, high = float(np.min(luma)), float(np.max(luma))
    at_low = ndimage.maximum_filter(luma, size=3, mode="nearest") <= low
    at_high = ndimage.minimum_filter(luma, size=3, mode="nearest") >= high
    return at_low | at_high


# A few values, so that ties and plateaus are common; the extremes and the signed zeros are among them
ALPHABET = (-math.inf, -2.0, -0.0, 0.0, 1.0, 1.5, 3.0, math.inf)


def _frames(with_nan: bool):
    values = st.sampled_from(ALPHABET + ((math.nan,) if with_nan else ()))
    return st.integers(1, 14).flatmap(
        lambda h: st.integers(1, 14).flatmap(
            lambda w: st.lists(values, min_size=h * w, max_size=h * w).map(
                lambda v: np.array(v, dtype=np.float64).reshape(h, w)
            )
        )
    )


def _with_mask(frames):
    return frames.flatmap(
        lambda f: st.lists(st.booleans(), min_size=f.size, max_size=f.size).map(
            lambda m: (f, np.array(m, dtype=bool).reshape(f.shape))
        )
    )


SIDES = st.sampled_from([1, 3, 5, 7, 9])


@settings(max_examples=500, deadline=None)
@given(pair=_with_mask(_frames(with_nan=True)), side=SIDES)
@example(pair=(np.zeros((1, 1)), np.ones((1, 1), dtype=bool)), side=9)
@example(pair=(np.full((3, 4), 2.0), np.ones((3, 4), dtype=bool)), side=7)
@example(pair=(np.array([[0.0, -0.0], [-0.0, 0.0]]), np.ones((2, 2), dtype=bool)), side=3)
@example(pair=(np.array([[1.0, math.nan, 1.0]]), np.ones((1, 3), dtype=bool)), side=3)
def test_pbt_the_local_maxima_are_mains_whole_frame_filter(pair: tuple[np.ndarray, np.ndarray], side: int) -> None:
    values, candidate = pair
    got = detector._local_maxima(values, candidate, side)
    assert got.dtype == np.bool_ and got.shape == values.shape
    assert np.array_equal(got, _main_local_maxima(values, candidate, side))


@settings(max_examples=300, deadline=None)
@given(values=_frames(with_nan=False), side=SIDES)
def test_pbt_the_window_maximum_at_each_pixel_is_scipys_nearest_border(values: np.ndarray, side: int) -> None:
    ys, xs = np.nonzero(np.ones(values.shape, dtype=bool))
    got = detector._window_max_at(values, ys, xs, side)
    assert np.array_equal(got, ndimage.maximum_filter(values, size=side, mode="nearest")[ys, xs])


@settings(max_examples=500, deadline=None)
@given(values=_frames(with_nan=True), threshold=st.sampled_from(ALPHABET + (math.nan, 0.5, 2.9)), side=SIDES)
@example(values=np.array([[3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]]), threshold=3.0, side=7)
@example(values=np.array([[math.nan, 0.0, 5.0]]), threshold=1.0, side=3)
def test_pbt_reaching_the_threshold_is_mains_whole_frame_filter(values: np.ndarray, threshold: float, side: int) -> None:
    got = detector._reaches(values, threshold, side)
    assert got.dtype == np.bool_ and got.shape == values.shape
    assert np.array_equal(got, _main_reaches(values, threshold, side))


@settings(max_examples=500, deadline=None)
@given(luma=_frames(with_nan=True))
@example(luma=np.full((4, 5), 7.0))
@example(luma=np.array([[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 9.0]]))
@example(luma=np.array([[math.nan, 0.0, 0.0], [0.0, 0.0, 0.0]]))
def test_pbt_the_clipped_flats_are_mains_whole_frame_filters(luma: np.ndarray) -> None:
    got = detector._clipped_flats(luma)
    assert got.dtype == np.bool_
    assert np.array_equal(got, _main_clipped_flats(luma))


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32])
def test_the_clipped_flats_are_mains_on_stored_types(dtype) -> None:
    rng = np.random.default_rng(5)
    top = 255 if dtype == np.uint8 else 1023
    luma = rng.integers(0, top + 1, size=(40, 50)).astype(dtype)
    luma[:12, :] = 0
    luma[20:26, 30:37] = top
    luma[39, 0] = top
    assert np.array_equal(detector._clipped_flats(luma), _main_clipped_flats(luma))
    assert detector._clipped_flats(luma)[:11, :].all()


def _field(seed: int) -> np.ndarray:
    """An 8-bit night field of 360 x 480: a sky of 20 with noise correlated over a few pixels, 220 stars of 12 to 150
    levels, 40 disks clipped at 255 and a black-clipped ground along the bottom, every value a whole level."""
    rng = np.random.default_rng(seed)
    h, w = 360, 480
    sky = 20.0 + ndimage.gaussian_filter(rng.normal(0.0, 6.0, (h, w)), 1.2)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(220):
        cx, cy, amp = rng.uniform(6, w - 6), rng.uniform(6, h - 50), rng.uniform(12.0, 150.0)
        sky += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.3**2))
    for _ in range(40):
        cx, cy, radius = rng.uniform(8, w - 8), rng.uniform(8, h - 50), rng.uniform(1.5, 5.0)
        sky[(xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2] = 255.0
    sky[h - 40 :, :] = 0.0
    return np.clip(np.round(sky), 0.0, 255.0)


def _assert_same(a, b) -> None:
    """Equal field by field, arrays element by element (NaN where NaN), dataclasses and tuples walked."""
    import dataclasses

    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        assert isinstance(a, np.ndarray) and isinstance(b, np.ndarray)
        assert a.dtype == b.dtype and np.array_equal(a, b, equal_nan=a.dtype.kind == "f")
    elif dataclasses.is_dataclass(a) and not isinstance(a, type):
        assert type(a) is type(b)
        for f in dataclasses.fields(a):
            _assert_same(getattr(a, f.name), getattr(b, f.name))
    elif isinstance(a, (tuple, list)) and not hasattr(a, "_fields"):
        assert type(a) is type(b) and len(a) == len(b)
        for x, y in zip(a, b):
            _assert_same(x, y)
    else:
        assert a == b


@pytest.mark.parametrize("seed", [4, 9])
def test_the_detector_returns_the_same_records_with_mains_filters_put_back(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    luma = _field(seed)
    sky_mask = np.ones(luma.shape, dtype=bool)
    sky_mask[:, :30] = False
    found = detector.detect_point_sources(luma, asset_id="src_synthetic")
    masked = detector.detect_point_sources(luma, sky_mask=sky_mask, asset_id="src_synthetic")
    field = detector.detection_field(luma)
    measures = detector.frame_measures(luma)

    calls = {"maxima": 0, "reaches": 0, "flats": 0}

    def local_maxima(values, candidate, side):
        calls["maxima"] += 1
        return _main_local_maxima(values, candidate, side)

    def reaches(values, threshold, side):
        calls["reaches"] += 1
        return _main_reaches(values, threshold, side)

    def clipped_flats(frame):
        calls["flats"] += 1
        return _main_clipped_flats(frame)

    monkeypatch.setattr(detector, "_local_maxima", local_maxima)
    monkeypatch.setattr(detector, "_reaches", reaches)
    monkeypatch.setattr(detector, "_clipped_flats", clipped_flats)
    assert detector.detect_point_sources(luma, asset_id="src_synthetic") == found
    assert detector.detect_point_sources(luma, sky_mask=sky_mask, asset_id="src_synthetic") == masked
    _assert_same(detector.detection_field(luma), field)
    assert detector.frame_measures(luma) == measures
    # Main's expressions ran in place of every rewrite: each is on this field's path
    assert calls["maxima"] > 0 and calls["reaches"] > 0 and calls["flats"] > 0
    assert len(found) > 100 and detector._clipped_flats(luma).any()


def test_no_float_frame_goes_through_a_whole_frame_max_or_min_filter_in_the_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """The speed this unit is for: on a frame with no NaN, the three steps filter only byte masks."""
    luma = _field(4)
    seen: list[str] = []
    real_max, real_min = ndimage.maximum_filter, ndimage.minimum_filter

    def spy_max(values, *args, **kwargs):
        seen.append(np.asarray(values).dtype.name)
        return real_max(values, *args, **kwargs)

    def spy_min(values, *args, **kwargs):
        seen.append(np.asarray(values).dtype.name)
        return real_min(values, *args, **kwargs)

    residual = luma - float(np.median(luma))
    snr = residual / 2.0
    noise = np.full(luma.shape, 2.0)
    clipped = _main_clipped_flats(luma)
    monkeypatch.setattr(detector.ndimage, "maximum_filter", spy_max)
    monkeypatch.setattr(detector.ndimage, "minimum_filter", spy_min)
    detector._clipped_flats(luma)
    detector._negative_count(residual, snr, noise, 2.0, 4.75, 0.0, 0.0, None)
    detector._profile_stars(residual, 2.0, clipped)
    assert seen and all(name in ("uint8", "bool") for name in seen), seen


def _main_negative_count(residual, snr_map, noise_map, sigma_eff, threshold, peak_share, box_share, sky_mask):
    """Main's `_negative_count` body, as it was."""
    negated_residual = -residual
    negated = -snr_map
    local_max = ndimage.maximum_filter(negated, size=7, mode="nearest")
    near_source = ndimage.maximum_filter(snr_map, size=7, mode="nearest") >= float(threshold)
    is_peak = (negated == local_max) & (negated >= float(threshold)) & (negated_residual > 0.0) & ~near_source
    margin = detector.EDGE_MARGIN_PX
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False
    if sky_mask is not None:
        is_peak &= np.asarray(sky_mask, dtype=bool)
    left = 1.0 - float(np.mean(near_source))
    if not left > 0.0:
        return detector.NegativeImage(0, 0.0)
    kernel = detector._build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    core3 = kernel[2:5, 2:5]
    core3_norm = float(np.linalg.norm(core3))
    count = 0
    for y0, x0 in zip(*np.nonzero(is_peak)):
        det = detector._measure_peak_photometry(
            negated_residual, negated, sigma_eff, core3, core3_norm, int(y0), int(x0), float(threshold), None, 0.0,
            allow_bright_impulse=True, gate_sigma=float(noise_map[y0, x0]), peak_share=peak_share, box_share=box_share,
        )
        count += det is not None
    return detector.NegativeImage(count, left)


def _main_profile_stars(residual, sigma_eff, clipped):
    """Main's `_profile_stars` body, as it was."""
    from scipy.spatial import cKDTree

    h, w = residual.shape
    half = detector.PROFILE_HALF_PX
    peaks = (residual == ndimage.maximum_filter(residual, size=detector.PROFILE_PEAK_BOX_PX, mode="nearest")) & (
        residual >= detector.PROFILE_NOISES * float(sigma_eff)
    )
    ys, xs = np.nonzero(peaks)
    inside = (ys >= half) & (ys < h - half) & (xs >= half) & (xs < w - half)
    ys, xs = ys[inside], xs[inside]
    if ys.size == 0:
        return []
    values = residual[ys, xs]
    near_flat = detector._dilate_square(clipped, 2 * half + 1)
    tree = cKDTree(np.stack([ys, xs], axis=1).astype(np.float64))
    chosen = []
    for i in np.argsort(-values, kind="stable"):
        if len(chosen) >= detector.PROFILE_MAX_STARS:
            break
        if near_flat[ys[i], xs[i]]:
            continue
        neighbours = [j for j in tree.query_ball_point([float(ys[i]), float(xs[i])], detector.PROFILE_ISOLATION_PX) if j != i]
        if any(values[j] >= detector.PROFILE_ISOLATION_SHARE * values[i] for j in neighbours):
            continue
        chosen.append((float(values[i]), int(ys[i]), int(xs[i])))
    return chosen


def _quarter_field(seed: int, h: int = 120, w: int = 160) -> np.ndarray:
    """A residual in whole quarters (so values sit exactly on the thresholds 4.75 and 10 and tie often): correlated
    noise, stars of 4.75 to 30 with holes swinging below zero beside them, pairs of peaks 3 to 5 px apart, and peaks and
    holes within the edge margin."""
    rng = np.random.default_rng(seed)
    residual = ndimage.gaussian_filter(rng.normal(0.0, 2.2, (h, w)), 0.7)
    for _ in range(60):
        y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
        amp = float(rng.choice([4.75, 5.0, 10.0, 12.5, 30.0]))
        residual[y, x] = amp * float(rng.choice([1.0, -1.0]))
        dy, dx = int(rng.integers(-3, 4)), int(rng.integers(-3, 4))
        if 0 <= y + dy < h and 0 <= x + dx < w and (dy or dx):
            residual[y + dy, x + dx] = -float(rng.choice([4.75, 6.0, 10.0]))
    for _ in range(25):
        y, x = int(rng.integers(6, h - 6)), int(rng.integers(6, w - 10))
        gap = int(rng.integers(3, 6))
        residual[y, x] = 20.0
        residual[y, x + gap] = float(rng.choice([10.0, 6.0, 20.0]))
    return np.round(residual * 4.0) / 4.0


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("masked", [False, True])
def test_the_negative_count_is_mains_peak_for_peak(seed: int, masked: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    residual = _quarter_field(seed)
    snr = residual / 1.0
    noise = np.ones(residual.shape)
    sky_mask = None
    if masked:
        sky_mask = np.ones(residual.shape, dtype=bool)
        sky_mask[:, :40] = False
    peaks: list[tuple[int, int]] = []
    real = detector._measure_peak_photometry

    def photometry(*args, **kwargs):
        peaks.append((args[5], args[6]))
        return real(*args, **kwargs)

    monkeypatch.setattr(detector, "_measure_peak_photometry", photometry)
    for threshold in (4.75, 6.0):
        for shares in ((0.0, 0.0), (0.3, 0.2)):
            peaks.clear()
            got = detector._negative_count(residual, snr, noise, 1.0, threshold, shares[0], shares[1], sky_mask)
            got_peaks = list(peaks)
            peaks.clear()
            want = _main_negative_count(residual, snr, noise, 1.0, threshold, shares[0], shares[1], sky_mask)
            assert got == want and got_peaks == peaks
            assert len(peaks) > 0


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_profile_stars_are_mains(seed: int) -> None:
    residual = _quarter_field(seed)
    clipped = np.zeros(residual.shape, dtype=bool)
    clipped[50:53, 60:63] = True
    for sigma in (1.0, 0.5, 2.0):
        got = detector._profile_stars(residual, sigma, clipped)
        assert got == _main_profile_stars(residual, sigma, clipped)
    assert len(detector._profile_stars(residual, 1.0, clipped)) > 0
