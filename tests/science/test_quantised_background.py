"""realsky-4b: on integer data the detector's background does not move in whole steps.

The detector's background is a 7 x 7 median on a strided copy of the frame (`detector._estimate_background_and_noise`).
On data that sits on a grid of whole steps (`detector._value_step`) whose noise is a few steps, that median takes whole
step values, so a sky that lies between two codes is read a fraction of a code off over whole patches of the frame.
On star-free 400 x 600 one-channel skies the plain median's rms error against the true sky was 0.34 to 0.45 codes,
against 0.07 to 0.27 codes for the same skies before rounding [tested, realsky-4b report, 36 skies].

With `grouped`, the median of each window is the grouped median: the window's middle value x, taken as the middle
of a bin one step wide, and placed within that bin by the share of the window's middle sample that falls in it,
x - h/2 + h (n/2 - F) / f, with F the samples below x, f the samples equal to x and h the step. This is the formula of
the Python standard library's `statistics.median_grouped` (CPython 3.14, Lib/statistics.py, read at the source), which
the tests below use as the oracle on every window. On the same 36 skies its error was within 0.042 codes of the
unrounded sky's [tested, realsky-4b report]. A frame on no grid keeps the plain median.
All frames here are simulated.
"""

from __future__ import annotations

from pathlib import Path
import statistics

from hypothesis import example, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest
from scipy import ndimage, optimize, stats

from services.science import detector

# The scales an 8-bit sky reaches the detector on: its codes, widened to 16 bits, scaled to 0..1
SCALES = [("codes", 1.0), ("16-bit", 257.0), ("0..1", 1.0 / 255.0)]


def _grouped_median_by_window(values: np.ndarray, step: float, size: int = 7) -> np.ndarray:
    """The standard library's grouped median of every size x size window, the frame's edge repeated outward (as
    scipy's mode "nearest")."""
    pad = size // 2
    padded = np.pad(values, pad, mode="edge")
    out = np.empty(values.shape)
    for r in range(values.shape[0]):
        for c in range(values.shape[1]):
            out[r, c] = statistics.median_grouped(padded[r : r + size, c : c + size].ravel().tolist(), interval=step)
    return out


@settings(max_examples=80, deadline=None)
@given(
    levels=st.lists(st.integers(0, 5), min_size=1, max_size=150),
    cols=st.integers(1, 13),
    offset=st.integers(0, 250),
    scale=st.sampled_from(SCALES),
)
@example(levels=[3] * 49, cols=7, offset=0, scale=SCALES[0])  # one value everywhere: every sample is the middle level
@example(levels=[0] * 24 + [1] * 25, cols=7, offset=0, scale=SCALES[0])  # the middle level holds the 25th sample only
@example(levels=[0] * 25 + [1] * 24, cols=7, offset=0, scale=SCALES[1])  # it holds the 25th as the last of its level
@example(levels=[2], cols=1, offset=40, scale=SCALES[2])  # a one-pixel frame: the edge fills the whole window
def test_pbt_the_grouped_median_is_the_standard_librarys_on_every_window(
    levels: list[int], cols: int, offset: int, scale: tuple[str, float]
) -> None:
    _, step = scale
    rows = max(1, len(levels) // cols)
    values = np.resize(np.asarray(levels, dtype=np.float64) + offset, (rows, cols)) * step
    got = detector._grouped_median_filter(values, step)
    want = _grouped_median_by_window(values, step)
    assert np.allclose(got, want, rtol=1e-12, atol=1e-12 * step)
    # The middle sample is the plain median, so the result lies inside its bin: within half a step of it
    plain = ndimage.median_filter(values, size=7, mode="nearest")
    assert np.all(np.abs(got - plain) <= 0.5 * step * (1.0 + 1e-9))


def _sky(base: float, slope: float, read_noise: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """A star-free 400 x 600 sky whose true background rises `slope` codes per 100 px across and half that down, so
    most of the frame lies between two codes, with Gaussian read noise: (truth, the sky before rounding)."""
    yy, xx = np.mgrid[0:400, 0:600]
    truth = base + slope * xx / 100.0 + 0.5 * slope * yy / 100.0
    return truth, truth + np.random.default_rng(seed).normal(0.0, read_noise, truth.shape)


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a * a)))


# The most the grouped background's rms error exceeded the unrounded sky's over the 36 skies of the realsky-4b report
# was 0.042 codes [tested]; [proposed] 0.06 leaves room for the noise rounding adds (1/12 code^2), which the unrounded
# sky does not carry
GROUPED_EXCESS_CODES = 0.06
# The least the plain median's exceeded it on the three skies below was 0.19 codes [tested, realsky-4b report]
PLAIN_EXCESS_CODES = 0.15
SKIES = [
    pytest.param(40.0, 0.1, 0.4, 1, id="bg 40, read noise 0.4"),
    pytest.param(40.3, 0.05, 0.7, 2, id="bg 40.3, read noise 0.7"),
    pytest.param(2.2, 0.1, 1.0, 3, id="bg 2.2, read noise 1.0"),
]


@pytest.mark.parametrize(("base", "slope", "read_noise", "seed"), SKIES)
@pytest.mark.parametrize("scale", SCALES, ids=[s[0] for s in SCALES])
def test_on_an_integer_sky_the_grouped_background_is_as_close_as_the_unrounded_skys(
    base: float, slope: float, read_noise: float, seed: int, scale: tuple[str, float]
) -> None:
    _, step = scale
    truth, raw = _sky(base, slope, read_noise, seed)
    integer = np.clip(np.rint(raw), 0, 255) * step
    unrounded_error = _rms(detector._estimate_background_and_noise(raw * step)[0] - truth * step) / step
    plain_error = _rms(detector._estimate_background_and_noise(integer)[0] - truth * step) / step
    grouped_error = _rms(detector._estimate_background_and_noise(integer, grouped=True)[0] - truth * step) / step
    # The cause, kept in view: the plain median on the rounded sky is far off the sky
    assert plain_error > unrounded_error + PLAIN_EXCESS_CODES
    assert grouped_error <= unrounded_error + GROUPED_EXCESS_CODES


@pytest.mark.parametrize("kind", ["floats", "colour luma"])
def test_a_frame_on_no_grid_keeps_the_plain_median_background(kind: str) -> None:
    truth, raw = _sky(40.3, 0.05, 0.7, 4)
    if kind == "floats":
        frame = raw
    else:
        rng = np.random.default_rng(5)
        rgb = np.clip(np.rint(truth[..., None] + rng.normal(0.0, 0.7, truth.shape + (3,))), 0, 255)
        frame = detector._to_luma_2d(rgb) / 255.0
    assert detector._value_step(frame) is None
    plain_bg, plain_sigma = detector._estimate_background_and_noise(frame)
    bg, sigma = detector._estimate_background_and_noise(frame, grouped=True)
    # The same calls on the same values: equal by construction
    assert np.array_equal(bg, plain_bg) and sigma == plain_sigma


def test_a_masked_integer_sky_keeps_its_background_within_half_a_step_of_the_plain_one() -> None:
    # The mask fills the ground with the sky's median, which may sit between two codes: the grouped median still
    # takes the window's middle value as its bin, so it stays within half a step of the plain median there too
    truth, raw = _sky(40.3, 0.05, 0.7, 6)
    integer = np.clip(np.rint(raw), 0, 255)
    mask = np.ones(integer.shape, dtype=bool)
    mask[300:, :] = False
    plain_bg, _ = detector._estimate_background_and_noise(integer, mask)
    bg, _ = detector._estimate_background_and_noise(integer, mask, grouped=True)
    assert float(np.max(np.abs(bg - plain_bg))) <= 0.5 * (1.0 + 1e-9)
    assert _rms(bg[mask] - truth[mask]) < _rms(plain_bg[mask] - truth[mask])


def test_the_grid_is_the_lumas_own_and_not_the_sky_masks_fill() -> None:
    # A sky of exactly as many 40s as 41s: its median, which fills the ground under a sky mask, is 40.5, half a step
    # off the grid. The step stays the luma's, so the windows that hold no fill give the background they give with no
    # mask at all
    rng = np.random.default_rng(7)
    frame = np.full((400, 600), 40.0)
    frame[:300] = rng.permutation(np.repeat([40.0, 41.0], 300 * 600 // 2)).reshape(300, 600)
    mask = np.zeros(frame.shape, dtype=bool)
    mask[:300] = True
    assert float(np.median(frame[mask])) == 40.5
    masked_bg, _ = detector._estimate_background_and_noise(frame, mask, grouped=True)
    unmasked_bg, _ = detector._estimate_background_and_noise(frame, grouped=True)
    # Rows under 250 lie more than three strided samples (12 px) and one more for the joining lines from the fill
    assert np.allclose(masked_bg[:250], unmasked_bg[:250], rtol=0.0, atol=1e-12)


# On such skies at read noises of 0.3 to 50 codes the plain median's MAD read 0.40 to 1.61 times the rounded sky's own
# spread about its truth, and the clipped spread (detector._clipped_spread) on the grouped background 1.008 to 1.037
# times it [tested, realsky-4b report]; [proposed] 5 % leaves room over the largest
NOISE_RATIO_TOLERANCE = 0.05
NOISE_SKIES = [
    pytest.param(40.0, 0.1, 0.4, 1, id="bg 40, read noise 0.4"),
    pytest.param(40.3, 0.05, 0.9, 2, id="bg 40.3, read noise 0.9"),
    pytest.param(6.2, 0.1, 2.0, 1, id="bg 6.2, read noise 2.0"),
    pytest.param(40.0, 0.1, 3.0, 2, id="bg 40, read noise 3.0"),
]


@pytest.mark.parametrize(("base", "slope", "read_noise", "seed"), NOISE_SKIES)
@pytest.mark.parametrize("scale", SCALES, ids=[s[0] for s in SCALES])
def test_on_an_integer_sky_the_pixel_noise_is_the_rounded_skys_own(
    base: float, slope: float, read_noise: float, seed: int, scale: tuple[str, float]
) -> None:
    _, step = scale
    truth, raw = _sky(base, slope, read_noise, seed)
    integer = np.clip(np.rint(raw), 0, 255)
    spread = float(np.std(integer - truth))
    sigma = detector._estimate_background_and_noise(integer * step, grouped=True)[1] / step
    assert abs(sigma / spread - 1.0) <= NOISE_RATIO_TOLERANCE


def test_the_clipped_gaussian_spread_is_the_fixed_point_of_repeated_three_sigma_clipping() -> None:
    # A normal cut at plus and minus a keeps the spread g(a) (scipy.stats.truncnorm); clipping at 3 kept spreads until
    # nothing changes settles where s = g(3 s)
    def g(a: float) -> float:
        return float(stats.truncnorm(-a, a).std())

    fixed = optimize.brentq(lambda r: r - g(detector.CLIP_SIGMAS * r), 0.5, 1.0, xtol=1e-12)
    assert detector.CLIP_SIGMAS == 3.0
    assert abs(detector.CLIPPED_GAUSSIAN_SPREAD - fixed) < 1e-6
    # One clip at the true sigma would keep more, and that is not the constant
    assert g(3.0) - detector.CLIPPED_GAUSSIAN_SPREAD > 1e-3


@pytest.mark.parametrize("sigma", [0.5, 2.0, 40.0])
def test_the_clipped_spread_of_a_gaussian_is_its_sigma(sigma: float) -> None:
    values = np.random.default_rng(11).normal(7.0, sigma, 400_000)
    # The spread of 400,000 samples scatters by about 0.1 %; the constant puts the clipped one on sigma
    assert abs(detector._clipped_spread(values) / sigma - 1.0) < 0.005


def test_the_clip_drops_what_lies_beyond_three_kept_spreads() -> None:
    # 2,000 Gaussian samples and 20 at 50 sigma: the plain spread is inflated by them, the clipped one is not
    rng = np.random.default_rng(12)
    values = np.concatenate([rng.normal(0.0, 1.0, 2000), np.full(20, 50.0)])
    assert float(np.std(values)) > 4.0
    assert abs(detector._clipped_spread(values) - 1.0) < 0.06


# The bound on the grid path, at each side of it, by a stand-in clipped spread: just under 15 steps the grouped
# background and that spread are taken; at the bound and above, the plain median and its MAD. Written as the bound's
# value (the MAD within 1.2 % from 12 steps up, and a margin), not from the constant, so a bound moved by a change
# fails here
GRID_EDGES = [
    ("just under the bound", 15.0 * (1.0 - 1e-9), True),
    ("at the bound", 15.0, False),
    ("just over the bound", 15.0 * (1.0 + 1e-9), False),
]


@pytest.mark.parametrize(("name", "steps", "grid_path"), GRID_EDGES, ids=[e[0] for e in GRID_EDGES])
@pytest.mark.parametrize("scale", SCALES, ids=[s[0] for s in SCALES])
def test_the_grid_path_acts_only_under_its_bound(
    monkeypatch: pytest.MonkeyPatch, name: str, steps: float, grid_path: bool, scale: tuple[str, float]
) -> None:
    _, step = scale
    truth, raw = _sky(40.3, 0.05, 0.7, 8)
    frame = np.clip(np.rint(raw), 0, 255) * step
    measured_step = detector._value_step(frame)
    plain = detector._estimate_background_and_noise(frame)
    monkeypatch.setattr(detector, "_clipped_spread", lambda values, step=0.0: steps * measured_step)
    bg, sigma = detector._estimate_background_and_noise(frame, grouped=True)
    if grid_path:
        assert sigma == steps * measured_step
        assert not np.array_equal(bg, plain[0])
    else:
        assert np.array_equal(bg, plain[0]) and sigma == plain[1]


def test_a_colour_lumas_fine_grid_keeps_the_plain_path() -> None:
    # Rec. 709's three weights are whole multiples of 0.0002, so the luma of three integer channels sits on a grid of
    # 0.0002 codes where it is computed without rounding (here as whole ten-thousandths), and the grid is measured
    # where neighbouring values one step apart occur, as on the five corpus photos below (near-grey skies seldom hold
    # them: their nearest values here were two steps apart): here every pixel a colour drawn at random. Its noise is
    # thousands of such steps, so the plain path stands
    rgb = np.random.default_rng(10).integers(0, 256, (400, 600, 3)).astype(np.float64)
    luma = (2126.0 * rgb[..., 0] + 7152.0 * rgb[..., 1] + 722.0 * rgb[..., 2]) / 10000.0
    step = detector._value_step(luma)
    assert step is not None and abs(step / 0.0002 - 1.0) < 1e-6
    plain = detector._estimate_background_and_noise(luma)
    assert plain[1] > detector.GRID_NOISE_STEPS * step
    bg, sigma = detector._estimate_background_and_noise(luma, grouped=True)
    assert np.array_equal(bg, plain[0]) and sigma == plain[1]


# The five public corpus photos whose luma, as the phone solver hands it over (Rec. 709 on 0..1), sits on that fine grid
# [tested, realsky-4b report, corpus_grid]
GRID_PHOTOS = {pid: f".cache/real-photos/{pid}/{pid}.jpg" for pid in ("rp-b-05", "rp-b-09", "rp-b-10", "rp-b-16", "rp-b-21")}
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.local_dataset(*GRID_PHOTOS.values())
def test_the_corpus_photos_on_a_fine_grid_keep_the_plain_path() -> None:
    for pid, path in GRID_PHOTOS.items():
        with Image.open(REPO / path) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
        luma = detector._to_luma_2d(rgb) / 255.0
        step = detector._value_step(luma)
        assert step is not None and abs(step * 255.0 / 0.0002 - 1.0) < 1e-3, (pid, step)
        plain = detector._estimate_background_and_noise(luma)
        bg, sigma = detector._estimate_background_and_noise(luma, grouped=True)
        assert np.array_equal(bg, plain[0]) and sigma == plain[1], pid
        assert plain[1] >= 4.0 * 15.0 * step, pid  # so the grid path is not even tried (GRID_PLAIN_FACTOR)


def test_a_fine_grid_far_over_the_bound_does_not_run_the_grid_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # The colour luma's grid of 0.0002 codes: its noise is thousands of steps, so neither the grouped median nor the clip
    # is run only to be set aside (they cost 24 s of rp-b-21's 84 s through the upload path)
    rgb = np.random.default_rng(10).integers(0, 256, (400, 600, 3)).astype(np.float64)
    luma = (2126.0 * rgb[..., 0] + 7152.0 * rgb[..., 1] + 722.0 * rgb[..., 2]) / 10000.0
    plain = detector._estimate_background_and_noise(luma)

    def refused(*args, **kwargs):
        raise AssertionError("the grid path ran")

    monkeypatch.setattr(detector, "_grouped_median_filter", refused)
    monkeypatch.setattr(detector, "_clipped_spread", refused)
    bg, sigma = detector._estimate_background_and_noise(luma, grouped=True)
    assert np.array_equal(bg, plain[0]) and sigma == plain[1]


# The grid path is tried where the plain path's noise is under 4 x 15 = 60 steps, written as that value and not from
# the constants, so a factor or bound moved by a change fails here. Under it a clip under the bound is taken; at and over
# it the clip is not run, so the plain path stands even where the clip would have come under the bound
FACTOR_EDGES = [
    ("just under four times the bound", 60.0 * (1.0 - 1e-9), True),
    ("at four times the bound", 60.0, False),
    ("just over four times the bound", 60.0 * (1.0 + 1e-9), False),
]


@pytest.mark.parametrize(("name", "steps", "grid_path"), FACTOR_EDGES, ids=[e[0] for e in FACTOR_EDGES])
@pytest.mark.parametrize("scale", SCALES, ids=[s[0] for s in SCALES])
def test_the_grid_path_is_tried_only_under_four_times_its_bound(
    monkeypatch: pytest.MonkeyPatch, name: str, steps: float, grid_path: bool, scale: tuple[str, float]
) -> None:
    _, step = scale
    truth, raw = _sky(40.3, 0.05, 0.7, 8)
    frame = np.clip(np.rint(raw), 0, 255) * step
    measured_step = detector._value_step(frame)
    monkeypatch.setattr(detector, "_plain_noise", lambda valid, interquartile_only=False: steps * measured_step)
    monkeypatch.setattr(detector, "_clipped_spread", lambda values, step=0.0: 0.5 * measured_step)
    bg, sigma = detector._estimate_background_and_noise(frame, grouped=True)
    assert (sigma == 0.5 * measured_step) is grid_path, name
    if not grid_path:
        assert sigma == steps * measured_step


# Where the grid path is no longer tried the old rule (tried on every grid, GRID_PLAIN_FACTOR infinite) set it aside
# too: integer skies whose noise is under, at and well over 60 steps, on each scale, give the same background and
# noise bit for bit both ways
FACTOR_SKIES = [(40.3, 0.05, 50.0, 21), (90.6, -0.02, 60.0, 22), (120.2, 0.04, 62.0, 23), (128.4, 0.0, 80.0, 24),
                (200.7, 0.08, 150.0, 25), (500.1, 0.1, 400.0, 26)]


@pytest.mark.parametrize(("base", "slope", "read_noise", "seed"), FACTOR_SKIES)
@pytest.mark.parametrize("scale", SCALES, ids=[s[0] for s in SCALES])
def test_the_grid_path_left_untried_is_one_the_old_rule_set_aside(
    monkeypatch: pytest.MonkeyPatch, base: float, slope: float, read_noise: float, seed: int, scale: tuple[str, float]
) -> None:
    _, step = scale
    truth, raw = _sky(base, slope, read_noise, seed)
    frame = np.rint(raw) * step
    assert detector._value_step(frame) is not None
    bg, sigma = detector._estimate_background_and_noise(frame, grouped=True)
    monkeypatch.setattr(detector, "GRID_PLAIN_FACTOR", float("inf"))
    old_bg, old_sigma = detector._estimate_background_and_noise(frame, grouped=True)
    assert np.array_equal(bg, old_bg) and sigma == old_sigma


def _audit_h_frame(regime: str, seed: int) -> tuple[np.ndarray, list[tuple[float, float, float]]]:
    """AUDIT H's bar frames (its passbar.py `build`, `place_stars` and `add_stars`, as the realsky-4 pair's bar used
    them): 900 x 1300, up to 400 stars of 1.15 px at uniform peaks, 12 px apart, rounded to 8 bits. Simulated."""
    rng = np.random.default_rng(seed)
    shape = (900, 1300)
    if regime == "white 8-bit":
        frame, low, high = 30.0 + rng.normal(0, 2.0, shape), 2.0, 18.0
    else:
        frame, low, high = 1.1 + rng.normal(0, 0.5, shape), 1.0, 10.0  # dark 8-bit b
    stars: list[tuple[float, float, float]] = []
    tries = 0
    while len(stars) < 400 and tries < 400 * 50:
        tries += 1
        x, y = rng.uniform(12, shape[1] - 12), rng.uniform(12, shape[0] - 12)
        if all((x - sx) ** 2 + (y - sy) ** 2 >= 144 for sx, sy, _ in stars):
            stars.append((x, y, rng.uniform(low, high)))
    for x, y, amp in stars:
        x0, y0 = int(x) - 8, int(y) - 8
        yy, xx = np.mgrid[y0 : y0 + 17, x0 : x0 + 17]
        frame[y0 : y0 + 17, x0 : x0 + 17] += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.15 * 1.15))
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8), stars


def _found_by_fifth_and_false(regime: str, seed: int) -> tuple[list[int], int]:
    """Stars found (a detection within 2.5 px) in each fifth of the peak range, faintest first, as AUDIT H's bar bins
    them, and detections with no star within 3 px, at the detector's defaults."""
    frame, stars = _audit_h_frame(regime, seed)
    low, high = (2.0, 18.0) if regime == "white 8-bit" else (1.0, 10.0)
    width = (high - low) / 5.0
    dets = detector.detect_point_sources(frame)
    found = [0] * 5
    for x, y, amp in stars:
        if any(np.hypot(d.x_px - x, d.y_px - y) < 2.5 for d in dets):
            found[min(4, int((amp - low) / width))] += 1
    false = sum(min(np.hypot(d.x_px - x, d.y_px - y) for x, y, _ in stars) >= 3.0 for d in dets)
    return found, false


# The two places where realsky-4b stays below its bar (the realsky-4b report), pinned both ways at seed 501 so neither
# grows worse unseen and a gain is noticed. Both are at 0 false detections here.
# - AUDIT H's dark 8-bit b, faintest fifth (peaks 1 to 2.8): 34 found, against 35 for main (at 3 false) and for the pair
#   (at 1 false) on this frame; over seeds 501 to 510 and a sweep of thresholds about 2 % under main at an equal false
#   count. [inference] The plain median read this sky 0.1 code low, which lifted every faint star by about 0.2 noises.
# - AUDIT H's white 8-bit, two faintest fifths (peaks 2 to 8.4): 2 and 57 found, against the pair's 3 and 63. The
#   pair's pixel noise read 1.51 codes for a true 2.02, which loosened the neighbour tests the detector keeps in pixel
#   noises (0.65 and 1.25, inherited from main): of the 46 stars the pair found and 4b did not over seeds 501 to 503,
#   30 and 13 fail them. Setting those levels from the target is a unit of its own.
# [tested, realsky-4b report]; [proposed] 2 stars either way, for the last bits of another machine's floats
BELOW_BAR_PINS = [
    pytest.param("dark 8-bit b", 0, 34, id="dark 8-bit b, faintest fifth"),
    pytest.param("white 8-bit", 0, 2, id="white 8-bit, faintest fifth"),
    pytest.param("white 8-bit", 1, 57, id="white 8-bit, second fifth"),
]
PIN_TOLERANCE_STARS = 2


@pytest.mark.parametrize(("regime", "fifth", "count"), BELOW_BAR_PINS)
def test_the_places_below_the_bar_are_pinned_at_their_measured_counts(regime: str, fifth: int, count: int) -> None:
    found, false = _found_by_fifth_and_false(regime, 501)
    assert false == 0
    assert abs(found[fifth] - count) <= PIN_TOLERANCE_STARS, (regime, fifth, found)


def test_on_a_grid_the_clip_keeps_the_first_level_either_side() -> None:
    # 900 residuals at 0 and 50 at each of minus and plus one step: their spread is sqrt(0.1) = 0.316 steps, so a clip
    # at three spreads (0.95 steps) would cut both neighbouring levels away and leave a spread of 0
    values = np.concatenate([np.zeros(900), np.full(50, -1.0), np.full(50, 1.0)])
    assert detector._clipped_spread(values) == 0.0
    assert detector._clipped_spread(values, 1.0) == pytest.approx(np.sqrt(0.1) / detector.CLIPPED_GAUSSIAN_SPREAD, rel=1e-12)


# CLIP_MIN_STEPS at its inside end and just outside: 2,000 residuals at 0 hold the spread far under half a step, so the
# clip sits at CLIP_MIN_STEPS steps; a pair of samples just inside it is kept, just outside it is dropped
CLIP_EDGES = [
    ("just inside", detector.CLIP_MIN_STEPS * (1.0 - 1e-9), True),
    ("at the bound", detector.CLIP_MIN_STEPS, True),
    ("just outside", detector.CLIP_MIN_STEPS * (1.0 + 1e-9), False),
]


@pytest.mark.parametrize(("name", "offset", "kept"), CLIP_EDGES, ids=[e[0] for e in CLIP_EDGES])
def test_the_clip_on_a_grid_at_its_bound(name: str, offset: float, kept: bool) -> None:
    values = np.concatenate([np.zeros(2000), [-offset, offset]])
    spread = detector._clipped_spread(values, 1.0)
    assert (spread > 0.0) == kept


def test_one_level_a_last_bit_apart_counts_as_one_bin() -> None:
    # On the 0..1 scale a level reached by adding whole steps can sit a float's last bit off the level read from the
    # frame: (k - 1)/255 + 1/255 is not always k/255. Both count in one bin, so the grouped median is the one the exact
    # levels give
    codes = np.resize(np.array([3, 4, 4, 5, 4, 3, 4], dtype=np.float64), (9, 11))
    exact = codes / 255.0
    nudged = exact.copy()
    nudged[::2, ::3] = np.nextafter(nudged[::2, ::3], np.inf)
    assert not np.array_equal(exact, nudged)
    assert np.allclose(detector._grouped_median_filter(nudged, 1.0 / 255.0), detector._grouped_median_filter(exact, 1.0 / 255.0), rtol=0.0, atol=1e-12)


def test_injected_stars_keep_the_frame_on_its_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    # Injection recovery adds its stars in whole steps of the frame's grid, so the injected image is measured as the
    # frame was (on the owner's real clips, added off the grid, 6 spurious detections came back beside 18 stars)
    _, raw = _sky(40.3, 0.05, 0.7, 13)
    frame = np.clip(np.rint(raw), 0, 255)
    steps: list[float | None] = []
    real_detect = detector.detect_point_sources

    def recording_detect(image, **kwargs):
        steps.append(detector._value_step(np.asarray(image, dtype=np.float64)))
        return real_detect(image, **kwargs)

    monkeypatch.setattr(detector, "detect_point_sources", recording_detect)
    detector.evaluate_injection_recovery(frame, n_per_snr=2)
    assert len(steps) >= 2 and all(s == pytest.approx(1.0) for s in steps), steps


def _grouped_at_centre(values: list[float]) -> float:
    """The grouped median of a 7 x 7 window holding exactly these 49 values, read at its centre (step 1)."""
    return float(detector._grouped_median_filter(np.asarray(values, dtype=np.float64).reshape(7, 7), 1.0)[3, 3])


def test_a_value_half_a_step_below_the_middle_counts_in_its_bin_and_one_half_a_step_above_does_not() -> None:
    # Bins are half open, [x - h/2, x + h/2): with the middle x = 0, two values at -0.5 count in its bin and one at +0.5
    # counts above it. 2 at -0.5, 23 at 0 and 24 at 1: F = 0, f = 25, so 0 - 0.5 + (24.5 - 0) / 25 = 0.48; with one of the
    # 1s at +0.5 instead the bin still holds 25 and the result is the same
    assert _grouped_at_centre([-0.5] * 2 + [0.0] * 23 + [1.0] * 24) == pytest.approx(0.48, abs=1e-12)
    assert _grouped_at_centre([-0.5] * 2 + [0.0] * 23 + [0.5] + [1.0] * 23) == pytest.approx(0.48, abs=1e-12)


# The grid path needs two sky samples at least (one sample has no spread to clip): with a sky mask of one pixel the
# plain path stands, with two the grid path is taken
SAMPLE_EDGES = [("one sky sample", 1, False), ("two sky samples", 2, True)]


@pytest.mark.parametrize(("name", "samples", "grid_path"), SAMPLE_EDGES, ids=[e[0] for e in SAMPLE_EDGES])
def test_the_grid_path_needs_two_sky_samples(monkeypatch: pytest.MonkeyPatch, name: str, samples: int, grid_path: bool) -> None:
    truth, raw = _sky(40.3, 0.05, 0.7, 14)
    frame = np.clip(np.rint(raw), 0, 255)
    mask = np.zeros(frame.shape, dtype=bool)
    mask[200, 300 : 300 + samples] = True
    plain = detector._estimate_background_and_noise(frame, mask)
    monkeypatch.setattr(detector, "_clipped_spread", lambda values, step=0.0: 0.123)
    bg, sigma = detector._estimate_background_and_noise(frame, mask, grouped=True)
    assert (sigma == 0.123) is grid_path, name
    if not grid_path:
        assert np.array_equal(bg, plain[0]) and sigma == plain[1]


def test_a_sky_mask_with_no_sky_reads_no_noise() -> None:
    # No residual to measure: the noise is 0 on both paths, not the median of nothing
    frame = np.clip(np.rint(_sky(40.3, 0.05, 0.7, 14)[1]), 0, 255)
    mask = np.zeros(frame.shape, dtype=bool)
    for grouped in (False, True):
        assert detector._estimate_background_and_noise(frame, mask, grouped=grouped)[1] == 0.0, grouped


def test_the_clip_is_centred_on_the_kept_samples_median() -> None:
    # SExtractor's rule: "clipped iteratively until convergence at ±3σ around its median". On a Gaussian with a tenth of
    # its count in a tail 4 sigma up, a clip centred on the mean keeps another set and reads 0.26 % higher
    rng = np.random.default_rng(31)
    values = np.concatenate([rng.normal(0.0, 1.0, 4000), rng.normal(4.0, 0.3, 400)])

    def clipped(centre) -> float:
        kept = values
        for _ in range(detector.CLIP_PASSES_MAX):
            nxt = values[np.abs(values - centre(kept)) <= detector.CLIP_SIGMAS * float(np.std(kept))]
            if nxt.size == kept.size:
                break
            kept = nxt
        return float(np.std(kept)) / detector.CLIPPED_GAUSSIAN_SPREAD

    by_median, by_mean = clipped(np.median), clipped(np.mean)
    assert abs(by_mean - by_median) > 1e-3
    assert detector._clipped_spread(values) == pytest.approx(by_median, rel=1e-12)
