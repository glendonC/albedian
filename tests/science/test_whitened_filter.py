"""The noise-whitened matched filter and the frames whose false rate the detector does not control (realsky-4).

Covers:
- The shrunk covariance is Ledoit and Wolf's estimator (their eq. 14), computed here the long way; it is always
  invertible, also with fewer patches than the footprint has pixels; patches with no variance give none.
- The whitened filter is the kernel on white noise, and on any other covariance no filter answers the model star
  higher over its own noise.
- The covariance is measured on noise, not on stars: the pixels near a star or a hole are left out, with each bound an
  edge-table row; the same noise with and without 400 stars gives the same filter.
- The statistic is chosen from each filter's measured noise, on both sides of the switch, and every detection names it.
- Its background, joined by a B-spline, has no kinks for it to read as stars: star-free noise correlated at ratio 3.7
  gives none.
- The flag: a sky under NEAR_CLIP_NOISES noises above the frame's lowest value, an edge-table row; integer data with
  little noise raises none since realsky-4b, and its star-free false count stays under twice the target at the read
  noises that left it before; the flag reaches every detection and the solve job's warnings in plain words.
All frames here are simulated.
"""

from __future__ import annotations

import hashlib
import math

from hypothesis import given, settings, strategies as st
import numpy as np
import pytest
from scipy import ndimage

from services.science import detector
from services.science.detector import detect_point_sources
from services.science.phone_photo_solver import solve_phone_photo

KERNEL = detector._build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
# White noise smoothed by a Gaussian of these widths (px) gives the kernel's response-over-pixel ratio 2, 3 and 3.7
CORRELATION_SIGMA_PX = {2.0: 0.651, 3.0: 1.283, 3.7: 2.917}


def _correlated(shape, sigma_c: float, seed: int, level: float = 100.0) -> np.ndarray:
    noise = np.random.default_rng(seed).normal(0.0, 1.0, shape)
    if sigma_c > 0:
        noise = ndimage.gaussian_filter(noise, sigma_c, mode="wrap")
    return level + noise / float(np.std(noise))


def _statistic(frame: np.ndarray, threshold: float = detector.DETECTION_THRESHOLD) -> detector.DetectionStatistic:
    luma = detector._to_luma_2d(frame)
    bg, sigma_bg = detector._estimate_background_and_noise(luma)
    return detector._detection_statistic(luma, luma - bg, None, detector._effective_sigma(sigma_bg, frame), threshold)


def _with_stars(frame: np.ndarray, count: int, peaks: tuple[float, float], seed: int, sigma: tuple[float, float] = (1.15, 1.15),
                angle: float = 0.0, spacing: float = 30.0) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """`frame` with `count` elliptical Gaussian stars (widths `sigma`, major axis at `angle`) of peaks drawn in `peaks`, at
    least `spacing` px apart and 15 px from the edges."""
    out = np.array(frame, dtype=np.float64)
    rng = np.random.default_rng(seed)
    h, w = out.shape
    places: list[tuple[float, float]] = []
    while len(places) < count:
        x, y = float(rng.uniform(15, w - 15)), float(rng.uniform(15, h - 15))
        if all((x - a) ** 2 + (y - b) ** 2 >= spacing * spacing for a, b in places):
            places.append((x, y))
    yy, xx = np.mgrid[0:h, 0:w]
    c, s = np.cos(angle), np.sin(angle)
    for x, y in places:
        ys, xs = slice(int(y) - 12, int(y) + 13), slice(int(x) - 12, int(x) + 13)
        u = c * (xx[ys, xs] - x) + s * (yy[ys, xs] - y)
        v = -s * (xx[ys, xs] - x) + c * (yy[ys, xs] - y)
        out[ys, xs] += rng.uniform(*peaks) * np.exp(-0.5 * (u * u / sigma[0] ** 2 + v * v / sigma[1] ** 2))
    return out, places


def _ledoit_wolf_the_long_way(patches: np.ndarray) -> tuple[np.ndarray, float]:
    n, p = patches.shape
    x = patches - patches.mean(axis=0)
    s = sum(np.outer(row, row) for row in x) / n
    norm2 = lambda a: float(np.trace(a @ a.T)) / p  # noqa: E731  (their Definition 1)
    m = float(np.trace(s)) / p
    d2 = norm2(s - m * np.eye(p))
    b2_bar = sum(norm2(np.outer(row, row) - s) for row in x) / (n * n)
    b2 = min(b2_bar, d2)
    return (b2 / d2) * m * np.eye(p) + ((d2 - b2) / d2) * s, b2 / d2


def test_the_shrunk_covariance_is_ledoit_and_wolfs_estimator() -> None:
    # Patches of correlated noise: the estimate equals eq. 14 with every sum written out (outer products, their norm)
    frame = _correlated((200, 300), CORRELATION_SIGMA_PX[3.0], seed=5)
    patches = np.lib.stride_tricks.sliding_window_view(frame, (7, 7))[3::7, 3::7].reshape(-1, 49)[:300]
    estimate, weight = detector._shrunk_covariance(patches)
    expected, expected_weight = _ledoit_wolf_the_long_way(patches)
    assert weight == pytest.approx(expected_weight, rel=1e-9)
    assert np.allclose(estimate, expected, rtol=1e-9, atol=1e-12)


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=2, max_value=120), st.floats(min_value=1e-3, max_value=1e3), st.integers(0, 2**31 - 1))
def test_the_shrunk_covariance_is_always_invertible(n: int, scale: float, seed: int) -> None:
    # Their p. 382: invertible also when there are fewer patches than variables (here 49), where S itself is singular.
    # Two patches, centred, are mirror images: every x_k x_k' is S, the weight on m I is 0 and the estimate would be S
    # itself, of rank 1, so none is given and the detector keeps the kernel
    rng = np.random.default_rng(seed)
    patches = scale * ndimage.gaussian_filter(rng.normal(0.0, 1.0, (n, 7, 7)), (0, 1.5, 1.5)).reshape(n, 49)
    shrunk = detector._shrunk_covariance(patches)
    if n == 2:
        assert shrunk is None
        return
    estimate, weight = shrunk
    assert 0.0 < weight <= 1.0
    assert float(np.min(np.linalg.eigvalsh(estimate))) > 0.0


def test_patches_with_no_variance_give_no_covariance() -> None:
    assert detector._shrunk_covariance(np.ones((50, 49))) is None
    assert detector._shrunk_covariance(np.zeros((1, 49))) is None


def test_on_white_noise_the_whitened_filter_is_the_kernel() -> None:
    assert np.allclose(detector._whitened_filter(2.5 * np.eye(49), KERNEL), KERNEL, rtol=0.0, atol=1e-15)


def test_no_filter_answers_the_model_star_higher_over_its_own_noise() -> None:
    # On a measured covariance of correlated noise: the whitened filter reaches sqrt(s' C^-1 s), and neither the kernel
    # nor 200 random filters on the footprint exceed it (Cauchy-Schwarz)
    frame = _correlated((400, 600), CORRELATION_SIGMA_PX[3.0], seed=6)
    patches = np.lib.stride_tricks.sliding_window_view(frame, (7, 7))[3::7, 3::7].reshape(-1, 49)
    covariance, _ = detector._shrunk_covariance(patches)
    star = detector._model_star_footprint(KERNEL).ravel()
    snr = lambda f: float(f @ star) / float(np.sqrt(f @ covariance @ f))  # noqa: E731
    best = snr(detector._whitened_filter(covariance, KERNEL).ravel())
    assert best == pytest.approx(float(np.sqrt(star @ np.linalg.solve(covariance, star))), rel=1e-9)
    assert snr(KERNEL.ravel()) < best
    rng = np.random.default_rng(7)
    assert max(snr(rng.normal(0.0, 1.0, 49)) for _ in range(200)) < best


# (name, residual pixel's distance from 0 in pixel noises, its Chebyshev distance from the patch's nearest pixel,
# whether the patch is used)
STAR_EDGES = [
    ("a pixel at WHITEN_STAR_NOISES is a star", detector.WHITEN_STAR_NOISES, 1, False),
    ("a pixel just under it is noise", detector.WHITEN_STAR_NOISES * (1.0 - 1e-9), 1, True),
    ("a hole at WHITEN_STAR_NOISES is left out too", -detector.WHITEN_STAR_NOISES, 1, False),
    ("a patch WHITEN_STAR_PX from a star is left out", detector.WHITEN_STAR_NOISES, detector.WHITEN_STAR_PX, False),
    ("a patch one px further is used", detector.WHITEN_STAR_NOISES, detector.WHITEN_STAR_PX + 1, True),
]


@pytest.mark.parametrize(("name", "value", "gap", "used"), STAR_EDGES, ids=[e[0] for e in STAR_EDGES])
def test_the_covariance_leaves_out_stars_and_holes_and_their_surroundings(name: str, value: float, gap: int, used: bool) -> None:
    # One 7 x 7 patch on the grid (centre (10, 10), pixels 7 to 13); the star pixel sits `gap` px right of its last
    # column. Every other residual is 0 and the pixel noise is 1, so only that pixel can leave the patch out
    residual = np.zeros((21, 28))
    residual[10, 13 + gap] = value
    valid = np.zeros_like(residual, dtype=bool)
    valid[7:14, 7:14] = True
    patches = detector._noise_patches(residual, valid, 1.0)
    assert (patches.shape[0] == 1) is used, name


def test_the_covariance_is_the_noises_and_not_the_stars() -> None:
    # The same correlated noise with and without 400 stars of peak 1.5 to 12 pixel noises: the covariance measured on
    # each gives whitened filters (for the kernel's star) whose cosine is above 0.99
    bare = _correlated((900, 1300), CORRELATION_SIGMA_PX[3.0], seed=8)
    starry, _ = _with_stars(bare, 400, (1.5, 12.0), seed=9, spacing=12.0)
    filters = []
    for frame in (bare, starry):
        bg, sigma_bg = detector._estimate_background_and_noise(frame)
        sigma_eff = detector._effective_sigma(sigma_bg, frame)
        smooth = frame - detector._estimate_background_and_noise(frame, smooth=True)[0]
        valid = np.zeros(frame.shape, dtype=bool)
        valid[4:-4, 4:-4] = True
        covariance, _ = detector._shrunk_covariance(detector._noise_patches(smooth, valid, sigma_eff))
        filters.append(detector._whitened_filter(covariance, KERNEL).ravel())
    assert float(filters[0] @ filters[1]) > 0.99


N = detector.NegativeImage
R = detector.StarRating
# (name, the rating on the frame's own stars, the kernel's negative image, the whitened filter's, whether the whitened
# filter is used). The margin written as its value, a gain of 1.10 on the stars, so a margin moved by a change fails
CHOICE_ROWS = [
    ("a gain of exactly 1.10, as many stars kept", R(1.10, 30, 30, 30), N(0, 1.0), N(0, 1.0), True),
    ("a gain just under 1.10, as many kept", R(1.10 * (1.0 - 1e-9), 30, 30, 30), N(0, 1.0), N(0, 1.0), False),
    ("a gain of 2 and 17 kept against 30: 34 against 33", R(2.0, 30, 17, 30), N(0, 1.0), N(0, 1.0), True),
    ("a gain of 2 and 16 kept against 30: 32 against 33", R(2.0, 30, 16, 30), N(0, 1.0), N(0, 1.0), False),
    ("the kernel's gates keep none, the whitened filter's one, a gain of 1.5", R(1.5, 0, 1, 30), N(0, 1.0), N(0, 1.0), True),
    ("neither keeps any", R(5.0, 0, 0, 30), N(0, 1.0), N(0, 1.0), False),
    ("no star gave both filters a response", R(None, 0, 0, 10), N(0, 1.0), N(0, 1.0), False),
    ("no rating", None, N(0, 1.0), N(0, 1.0), False),
    ("one negative peak against none: p 0.5", R(2.0, 30, 30, 30), N(0, 1.0), N(1, 1.0), True),
    ("four against none: p 1/16, above the level", R(2.0, 30, 30, 30), N(0, 1.0), N(4, 1.0), True),
    ("five against none: p 1/32, under the level", R(2.0, 30, 30, 30), N(0, 1.0), N(5, 1.0), False),
    ("eight against one: p 10/512", R(2.0, 30, 30, 30), N(1, 1.0), N(8, 1.0), False),
    ("two against two over half the area: p 0.41", R(2.0, 30, 30, 30), N(2, 1.0), N(2, 0.5), True),
    ("it could not be built", R(2.0, 30, 30, 30), N(3, 1.0), None, False),
]


@pytest.mark.parametrize(("name", "rating", "nm", "nw", "used"), CHOICE_ROWS, ids=[r[0] for r in CHOICE_ROWS])
def test_the_rule_of_choice_on_both_sides_of_each_condition(name, rating, nm, nw, used) -> None:
    assert detector._use_whitened(rating, nm, nw) is used, name
    # Near the black clip the negative image reads low: the kernel, whatever the figures
    assert detector._use_whitened(rating, nm, nw, near_clip=True) is False, name


def test_the_choice_p_value_is_the_exact_test_for_two_counts() -> None:
    # Given the sum n, the whitened filter's count is binomial(n, its share of the two areas): its upper tail
    assert detector._choice_p_value(N(0, 1.0), N(5, 1.0)) == pytest.approx(1.0 / 32.0, rel=1e-12)
    assert detector._choice_p_value(N(1, 1.0), N(8, 1.0)) == pytest.approx(10.0 / 512.0, rel=1e-12)
    third = 1.0 / 3.0
    assert detector._choice_p_value(N(2, 1.0), N(2, 0.5)) == pytest.approx(1.0 - (1 - third) ** 4 - 4 * third * (1 - third) ** 3, rel=1e-12)
    assert detector._choice_p_value(N(0, 1.0), N(0, 1.0)) == 1.0


def test_on_correlated_noise_with_bright_stars_the_whitened_filter_is_chosen_and_recorded() -> None:
    frame, _ = _with_stars(_correlated((900, 1300), CORRELATION_SIGMA_PX[3.0], seed=10), 60, (15.0, 30.0), seed=11)
    statistic = _statistic(frame)
    assert statistic.profile is not None and statistic.profile.stars >= detector.PROFILE_MIN_STARS
    assert statistic.name == "whitened"
    assert statistic.expected_snr_whitened > statistic.expected_snr_matched
    assert detector._choice_p_value(statistic.negative_matched, statistic.negative_whitened) >= detector.CHOICE_TEST_LEVEL
    dets = detect_point_sources(frame)
    record = dets[0].frame
    assert all(d.frame == record for d in dets) and record.statistic == "whitened"
    assert record.profile_stars == statistic.profile.stars and abs(record.profile_sigma_major_px - 1.15) < 0.1
    assert record.negative_peaks == statistic.negative_whitened.figure
    forced = detector.verify_candidates_on_original_pixels(frame, [(d.x_px, d.y_px) for d in dets])
    assert forced and {d.statistic for d in forced} == {"whitened"}


@pytest.mark.parametrize(
    "frame",
    [
        np.clip(np.rint(1.6 + np.random.default_rng(11).normal(0.0, 0.7, (900, 1300))), 0, 255).astype(np.uint8),
        np.zeros((300, 400), dtype=np.uint8),
        _correlated((600, 800), CORRELATION_SIGMA_PX[3.0], seed=12),
    ],
    ids=["a dark 8-bit sky with no bright star", "a sky crushed to black", "star-free correlated noise"],
)
def test_a_frame_with_too_few_bright_stars_keeps_the_kernel_and_says_so(frame: np.ndarray) -> None:
    statistic = _statistic(frame)
    assert statistic.profile is None and statistic.name == "matched" and statistic.expected_snr_whitened is None


@pytest.mark.parametrize("seed", [11, 12])
def test_correlated_noise_at_ratio_3_7_with_stars_gives_no_false_detection(seed: int) -> None:
    # With the stars that build a profile the whitened filter is used here; on the straight-line background it read the
    # background's kinks as stars (15 and 6 false detections over two such star-free frames)
    frame, places = _with_stars(_correlated((1512, 2016), CORRELATION_SIGMA_PX[3.7], seed), 40, (15.0, 30.0), seed=seed + 100)
    dets = detect_point_sources(frame)
    assert dets and dets[0].statistic == "whitened"
    far = [d for d in dets if min(np.hypot(d.x_px - x, d.y_px - y) for x, y in places) > 3.0]
    assert len(far) <= 1


@pytest.mark.parametrize(
    ("sigma", "angle"),
    [((1.15, 1.15), 0.0), ((1.6, 1.6), 0.0), ((2.2, 2.2), 0.0), ((2.0, 1.2), 0.6)],
    ids=["1.15 px", "1.6 px", "2.2 px", "a trail of 2.0 by 1.2 px"],
)
def test_the_profile_measures_the_frames_stars(sigma: tuple[float, float], angle: float) -> None:
    # 60 stars of peak 15 to 30 pixel noises on correlated noise; each half of the chosen stars gives the same
    frame, _ = _with_stars(_correlated((900, 1300), CORRELATION_SIGMA_PX[2.0], seed=13), 60, (15.0, 30.0), seed=14, sigma=sigma, angle=angle)
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    residual = frame - bg
    sigma_eff = detector._effective_sigma(sigma_bg, frame)
    clipped = detector._clipped_flats(frame)
    stars = detector._profile_stars(residual, sigma_eff, clipped)
    profile = detector._star_profile(residual, sigma_eff, clipped, stars)
    assert abs(profile.sigma_major_px - sigma[0]) < 0.08 and abs(profile.sigma_minor_px - sigma[1]) < 0.08
    if sigma[0] > sigma[1]:
        assert abs(((profile.angle_rad - angle + np.pi / 2) % np.pi) - np.pi / 2) < 0.1
    halves = [detector._star_profile(residual, sigma_eff, clipped, stars[k::2]) for k in (0, 1)]
    assert abs(halves[0].sigma_major_px - halves[1].sigma_major_px) < 0.1


def _bumps_or_stars(sigma: float | None) -> np.ndarray:
    """A flat frame with 150 one-level plus-shaped bumps (sigma None) or 150 Gaussian stars of `sigma` and peak 1,
    off the integer grid by a ten-thousandth of a level."""
    frame = np.ones((360, 480))
    yy, xx = np.mgrid[0:360, 0:480]
    for x in range(20, 460, 30):
        for y in range(20, 340, 30):
            if sigma is None:
                frame[y, x - 1 : x + 2] = 2.0
                frame[y - 1 : y + 2, x] = 2.0
            else:
                frame += np.exp(-((xx - x - 0.3) ** 2 + (yy - y - 0.2) ** 2) / (2 * sigma * sigma))
    return frame + 1e-4 * np.random.default_rng(3).random(frame.shape)


@pytest.mark.parametrize(
    ("sigma", "profiled"),
    [(None, False), (0.55, True)],
    ids=["plus-shaped bumps: the fit sits at its bound, no profile", "0.55 px stars: measured"],
)
def test_a_profile_narrower_than_the_pixels_can_measure_is_none(sigma, profiled) -> None:
    # PROFILE_MIN_SIGMA_PX (0.41 px): a Gaussian whose four nearest pixels hold 5 % of its peak
    assert detector.PROFILE_MIN_SIGMA_PX == pytest.approx(0.4085, abs=1e-4)
    frame = _bumps_or_stars(sigma)
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    residual = frame - bg
    profile = detector._star_profile(residual, detector._effective_sigma(sigma_bg, frame), detector._clipped_flats(frame))
    assert (profile is not None) is profiled
    if profiled:
        assert abs(profile.sigma_major_px - sigma) < 0.12


def test_the_profiles_stars_follow_the_rule() -> None:
    # Two stars 5 px apart (a pair), one with a neighbour of a fifth of its height 5 px away, one beside a saturated
    # flat, one 4 px from an edge, one under the brightness, and 12 good ones: only the good ones and the one whose
    # neighbour is under a quarter of its height are chosen; with 9 good ones there is no profile
    frame = np.zeros((300, 400))
    rng = np.random.default_rng(15)
    frame += rng.normal(0.0, 1.0, frame.shape)
    yy, xx = np.mgrid[0:300, 0:400]
    star = lambda x, y, a: a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.15**2))  # noqa: E731
    good = [(40.0 + 30.0 * k, 40.0) for k in range(12)]
    for x, y in good:
        frame += star(x, y, 30.0)
    frame += star(60.0, 150.0, 30.0) + star(65.0, 150.0, 25.0)      # a pair
    frame += star(160.0, 150.0, 30.0) + star(165.0, 150.0, 6.0)     # a faint neighbour, a fifth of its height
    frame += star(260.0, 150.0, 30.0)
    frame[150:156, 263:269] = 400.0                                  # a saturated flat beside it
    frame += star(4.0, 220.0, 30.0)                                   # on the edge
    frame += star(200.0, 250.0, 8.0)                                  # under PROFILE_NOISES
    top = float(frame.max())
    frame[frame >= 400.0] = top
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    residual = frame - bg
    sigma_eff = detector._effective_sigma(sigma_bg, frame)
    chosen = {(round(x), round(y)) for _, y, x in detector._profile_stars(residual, sigma_eff, detector._clipped_flats(frame))}
    assert chosen == {(round(x), round(y)) for x, y in good} | {(160, 150)}
    nine = [(v, y, x) for v, y, x in detector._profile_stars(residual, sigma_eff, detector._clipped_flats(frame)) if (x, y) != (160, 150)][:9]
    assert detector._star_profile(residual, sigma_eff, detector._clipped_flats(frame), nine) is None


def test_the_negative_image_leaves_out_a_sources_footprint_and_scales_to_the_frame() -> None:
    # A response that swings below the threshold beside a star: the swing is inside the star's 7 x 7 footprint and is
    # not counted; the same swing far from any source is counted, scaled by the share of the frame left
    h, w = 100, 100
    residual = np.zeros((h, w))
    snr = np.zeros((h, w))
    noise = np.ones((h, w))
    snr[50, 50] = 10.0                 # a source
    snr[50, 53], residual[50, 53] = -6.0, -3.0   # its swing, 3 px away
    snr[20, 20], residual[20, 20] = -6.0, -3.0   # a noise peak of the negative image, far from it
    for y, x in ((50, 53), (20, 20)):
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            residual[y + dy, x + dx] = -2.0
        for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            residual[y + dy, x + dx] = -1.4
    negative = detector._negative_count(residual, snr, noise, 1.0, 4.75, 0.3, 0.8, None)
    left = 1.0 - 49.0 / (h * w)
    assert negative.count == 1 and negative.share_left == pytest.approx(left, rel=1e-12)
    assert negative.figure == pytest.approx(1.0 / left, rel=1e-12) and negative.spread == pytest.approx(1.0 / left, rel=1e-12)


def test_noise_tail_is_recorded_and_reaches_no_warning() -> None:
    # On a real photo the negative image's peaks also count real dark structure and miss what lies near stars: the
    # flag is kept with the result and never worded as a count of false detections
    measures = detector.FrameMeasures(negative_peaks=12.4, noise_limits=("noise_tail", "near_black_clip"))
    assert "noise_tail" not in detector.NOISE_LIMIT_WORDS
    assert detector.noise_limit_warnings(measures) == [detector.NOISE_LIMIT_WORDS["near_black_clip"]]


def test_the_smooth_background_stays_within_its_samples() -> None:
    # A step in the sky (a horizon): the B-spline's weights are positive, so it never leaves the samples' range
    frame = np.full((256, 256), 40.0)
    frame[128:, :] = 2.0
    bg, _ = detector._estimate_background_and_noise(frame, smooth=True)
    assert float(bg.min()) >= 2.0 - 1e-9 and float(bg.max()) <= 40.0 + 1e-9


# The read noises at which one 8-bit channel at background 40 left twice the target before realsky-4b (0.45 and 1.25 to
# 1.75 codes; up to 3.6 false detections per megapixel), the cause being the background's median moving in whole steps.
# On the grid's own background and noise the same skies stay under it [tested, realsky-4b report], and this holds it:
# star-free 1000 x 1500 frames, two fixed seeds a read noise, summed under twice FALSE_TARGET_PER_MP over their area
STEP_EXCESS_READ_NOISES = [0.45, 1.25, 1.5, 1.75]


@pytest.mark.parametrize("read_noise", STEP_EXCESS_READ_NOISES)
def test_integer_skies_with_little_noise_stay_under_twice_the_target_and_raise_no_flag(read_noise: float) -> None:
    false = 0
    for seed in (1, 2):
        sky = 40.0 + np.random.default_rng(seed).normal(0.0, read_noise, (1000, 1500))
        frame = np.clip(np.rint(sky), 0, 255).astype(np.uint8)
        dets = detect_point_sources(frame)
        false += len(dets)
        assert all(not d.noise_limits or d.noise_limits == ("noise_tail",) for d in dets), read_noise
        assert detector.noise_limits(frame) in ((), ("noise_tail",))
    assert false <= 2.0 * detector.FALSE_TARGET_PER_MP * 2 * 1.5, (read_noise, false)


# (name, background's median above the lowest value in pixel noises, whether "near_black_clip" is raised). Written as
# the bound's measured value, 1.25 noises (the flags' curves: twice the target left up to a measured 1.24 on colour and
# 1.14 on one 8-bit channel, not from 1.33 up), and not from the constant, so a bound moved by a change fails here
CLIP_EDGES = [
    ("under 1.25 noises", 1.25 * (1.0 - 1e-9), True),
    ("at 1.25 noises", 1.25, False),
]


@pytest.mark.parametrize(("name", "lift", "raised"), CLIP_EDGES, ids=[e[0] for e in CLIP_EDGES])
def test_a_sky_near_its_darkest_value_is_flagged(name: str, lift: float, raised: bool) -> None:
    # Lowest value 0 and pixel noise 1, so the background's median is its lift in noises exactly
    luma = np.zeros((40, 40))
    limits = detector._noise_limits(luma, np.full_like(luma, lift), 1.0, None)
    assert ("near_black_clip" in limits) is raised, name


def test_a_colour_photo_and_a_grey_one_far_from_the_clip_raise_no_flag() -> None:
    # Integer data with little noise is no flag since realsky-4b: the grey frame on its grid and the colour photo as the
    # solve job hands it over (off any grid) both raise none
    rgb = np.clip(np.rint(40.0 + np.random.default_rng(13).normal(0.0, 1.5, (300, 400, 3))), 0, 255).astype(np.uint8)
    luma = detector._to_luma_2d(rgb) / 255.0
    assert not set(detector.noise_limits(luma)) & set(detector.NOISE_LIMIT_WORDS)
    grey = np.clip(np.rint(40.0 + np.random.default_rng(13).normal(0.0, 1.5, (300, 400))), 0, 255).astype(np.uint8)
    assert not set(detector.noise_limits(grey.astype(np.float64) / 255.0)) & set(detector.NOISE_LIMIT_WORDS)


def test_the_flags_reach_every_detection_and_the_solution_in_plain_words() -> None:
    # A dark 8-bit sky at the clip (background 0.3, noise 0.6: its median is the lowest code) with four stars, too few
    # for a solve, so the solution is unresolved and says why, and that the count is not controlled
    frame = 0.3 + np.random.default_rng(14).normal(0.0, 0.6, (400, 600))
    yy, xx = np.mgrid[0:400, 0:600]
    for x, y in ((100.2, 100.3), (300.4, 120.1), (500.3, 90.2), (150.1, 300.4)):
        frame += 30.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.15**2))
    image = np.clip(np.rint(frame), 0, 255).astype(np.uint8)
    dets = detect_point_sources(image)
    assert dets and all("near_black_clip" in d.noise_limits for d in dets)
    solution = solve_phone_photo(image, source_asset_id="asset-flag", source_sha256=hashlib.sha256(image.tobytes()).hexdigest())
    assert detector.NOISE_LIMIT_WORDS["near_black_clip"] in solution.warnings


def _audit_dark_frame(background: float, read_noise: float, seed: int) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """AUDIT H's dark 8-bit frame (its passbar.py): 900 x 1300, a sky of `background` codes and `read_noise`, 400 stars of
    1.15 px and peak 1 to 10 codes at least 12 px apart, rounded and clipped to 8 bits."""
    rng = np.random.default_rng(seed)
    shape = (900, 1300)
    frame = background + rng.normal(0, read_noise, shape)
    stars: list[tuple[float, float, float]] = []
    tries = 0
    while len(stars) < 400 and tries < 20000:
        tries += 1
        x, y = rng.uniform(12, shape[1] - 12), rng.uniform(12, shape[0] - 12)
        if all((x - sx) ** 2 + (y - sy) ** 2 >= 144 for sx, sy, _ in stars):
            stars.append((x, y, rng.uniform(1.0, 10.0)))
    for x, y, amp in stars:
        x0, y0 = int(x) - 8, int(y) - 8
        yy, xx = np.mgrid[y0 : y0 + 17, x0 : x0 + 17]
        frame[y0 : y0 + 17, x0 : x0 + 17] += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.15**2))
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8), [(x, y) for x, y, _ in stars]


@pytest.mark.parametrize(("background", "read_noise", "floor"), [(1.6, 0.7, 329), (2.4, 0.9, 308)], ids=["dark 8-bit c", "dark 8-bit d"])
def test_quantised_dark_frames_keep_their_floor_and_their_flag(background: float, read_noise: float, floor: int) -> None:
    # On AUDIT H's dark 8-bit c and d the realsky-4 pair found fewer stars than main at the same zero false count (121
    # against 139 and 188 against 212 in their middle bins over seeds 501 to 503), and this pinned its 287 and 264 here.
    # The cause was the background's median and the pixel noise's MAD, both of which move in whole steps on such data.
    # With the grouped median, the clipped spread (realsky-4b) and the kernel's shares set for the measured star, these
    # frames give 331 and 310 of 400 with no false detection, and the middle bins exceed main's (224 against 139, 190
    # against 116) [tested, realsky-4-v2 report]; pinned 2 stars under for another machine's last bits. Their skies sit
    # over 2 noises above their lowest code, so no flag is raised
    frame, places = _audit_dark_frame(background, read_noise, 501)
    dets = detect_point_sources(frame)
    found = sum(any(np.hypot(d.x_px - x, d.y_px - y) < 2.5 for d in dets) for x, y in places)
    false = sum(min(np.hypot(d.x_px - x, d.y_px - y) for x, y in places) >= 3.0 for d in dets)
    assert found >= floor and false == 0
    assert not any(set(d.noise_limits) & set(detector.NOISE_LIMIT_WORDS) for d in dets)


@pytest.mark.parametrize("sigma", [0.7, 1.0, 1.6])
def test_the_profile_keeps_a_noiseless_stars_width(sigma: float) -> None:
    # 64 stars at sub-pixel places on a grid of eighths: the median of patches each over its own peak keeps the width
    # (taking the places' spread of 1/12 px^2 off, as a mean of the patches would need, made them 0.638, 0.958, 1.574)
    frame = np.full((600, 600), 10.0)
    yy, xx = np.mgrid[0:600, 0:600]
    for i in range(8):
        for j in range(8):
            frame += 50.0 * np.exp(-((xx - 40.0 - 65.0 * i - i / 8.0) ** 2 + (yy - 40.0 - 65.0 * j - j / 8.0) ** 2) / (2 * sigma * sigma))
    frame += 1e-3 * np.random.default_rng(1).random(frame.shape)
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    profile = detector._star_profile(frame - bg, detector._effective_sigma(sigma_bg, frame), detector._clipped_flats(frame))
    assert profile.stars == 64
    assert abs(profile.sigma_major_px - sigma) < 0.01 and abs(profile.sigma_minor_px - sigma) < 0.01


# (name, the candidate's peak, a neighbour 6 px away (its height), whether the candidate is chosen); pixel noise 0.1,
# so PROFILE_NOISES puts a peak at 1.0, and single-pixel peaks so that no wing adds to another
PROFILE_EDGES = [
    ("a peak at exactly PROFILE_NOISES noises", detector.PROFILE_NOISES * 0.1, 0.0, True),
    ("a peak just under it", detector.PROFILE_NOISES * 0.1 * (1.0 - 1e-9), 0.0, False),
    ("a neighbour of exactly PROFILE_ISOLATION_SHARE of its height", 20.0, 20.0 * detector.PROFILE_ISOLATION_SHARE, False),
    ("a neighbour just under it", 20.0, 20.0 * detector.PROFILE_ISOLATION_SHARE * (1.0 - 1e-9), True),
]


@pytest.mark.parametrize(("name", "peak", "neighbour", "chosen"), PROFILE_EDGES, ids=[e[0] for e in PROFILE_EDGES])
def test_the_profile_rules_edges(name: str, peak: float, neighbour: float, chosen: bool) -> None:
    residual = np.zeros((41, 41))
    residual[20, 20] = peak
    residual[20, 26] = neighbour
    stars = detector._profile_stars(residual, 0.1, np.zeros_like(residual, dtype=bool))
    assert ((20, 20) in {(y, x) for _, y, x in stars}) is chosen, name


def test_exactly_the_least_number_of_stars_builds_a_profile() -> None:
    frame = np.full((600, 600), 10.0)
    yy, xx = np.mgrid[0:600, 0:600]
    for i in range(8):
        for j in range(8):
            frame += 50.0 * np.exp(-((xx - 40.0 - 65.0 * i - i / 8.0) ** 2 + (yy - 40.0 - 65.0 * j - j / 8.0) ** 2) / (2 * 1.2**2))
    frame += 1e-3 * np.random.default_rng(1).random(frame.shape)
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    residual, sigma_eff, clipped = frame - bg, detector._effective_sigma(sigma_bg, frame), detector._clipped_flats(frame)
    stars = detector._profile_stars(residual, sigma_eff, clipped)
    assert detector._star_profile(residual, sigma_eff, clipped, stars[: detector.PROFILE_MIN_STARS]) is not None
    assert detector._star_profile(residual, sigma_eff, clipped, stars[: detector.PROFILE_MIN_STARS - 1]) is None


def _model_stars_for(profile: detector.StarProfile, margin: float | None = 0.25) -> list[np.ndarray]:
    pairs = [(profile.sigma_major_px, profile.sigma_minor_px)]
    if margin is not None:
        pairs.extend([
            (
                max(detector.PROFILE_MIN_SIGMA_PX, profile.sigma_major_px - margin),
                max(detector.PROFILE_MIN_SIGMA_PX, profile.sigma_minor_px - margin),
            ),
            (profile.sigma_major_px + margin, profile.sigma_minor_px + margin),
        ])
    return [
        detector._elliptical_gaussian(20, sa, sb, profile.angle_rad, dy, dx, footprint=False)
        for sa, sb in pairs
        for dy in detector.STAR_OFFSETS_PX
        for dx in detector.STAR_OFFSETS_PX
    ]


def test_the_whitened_filter_is_built_and_gated_for_the_measured_star() -> None:
    # 1. Trailed stars (2.0 by 1.2 px) on noise smoothed at 1.0 px: the 7 x 7 model star keeps all 49 pixels
    # (footprint=False, removing the r = 3.5 step cliff inside the covariance window), and the whitened filter's gate
    # shares span +-0.25 px around the measured star
    frame, _ = _with_stars(_correlated((900, 1300), 1.0, seed=16), 60, (15.0, 30.0), seed=17,
                           sigma=(2.0, 1.2), angle=0.4)
    bg, sigma_bg = detector._estimate_background_and_noise(frame, grouped=True)
    residual = frame - bg
    sigma_eff = detector._effective_sigma(sigma_bg, frame)
    statistic = detector._detection_statistic(frame, residual, None, sigma_eff, detector.DETECTION_THRESHOLD)
    profile = statistic.profile
    assert profile is not None and profile.sigma_major_px > 1.8
    star = detector._elliptical_gaussian(3, profile.sigma_major_px, profile.sigma_minor_px, profile.angle_rad, footprint=False)
    star_clipped = detector._elliptical_gaussian(3, profile.sigma_major_px, profile.sigma_minor_px, profile.angle_rad, footprint=True)
    matched_map = detector._detector_noise_map(frame, ndimage.convolve(residual, KERNEL, mode="nearest"), None, sigma_eff)
    assert statistic.expected_snr_matched == pytest.approx(float(np.sum(KERNEL * star)) / float(np.median(matched_map)), rel=1e-12)
    smooth = frame - detector._estimate_background_and_noise(frame, smooth=True, grouped=True)[0]
    valid = np.zeros(frame.shape, dtype=bool)
    valid[detector.EDGE_MARGIN_PX : -detector.EDGE_MARGIN_PX, detector.EDGE_MARGIN_PX : -detector.EDGE_MARGIN_PX] = True
    valid &= ~detector._clipped_flats(frame)
    covariance, _ = detector._shrunk_covariance(detector._noise_patches(smooth, valid, sigma_eff))
    assert np.allclose(statistic.filt, detector._whitened_filter(covariance, star), rtol=1e-12, atol=1e-12)
    assert not np.allclose(statistic.filt, detector._whitened_filter(covariance, star_clipped), rtol=1e-3, atol=1e-3)
    assert statistic.name == "whitened"
    assert (statistic.peak_share, statistic.box_share) == detector._filter_shares(statistic.filt, _model_stars_for(profile))
    # 1b. Light correlation (0.4 px) with narrow stars (0.8 by 0.6 px): the wider end (+0.25 px) binds peak_share
    light, _ = _with_stars(_correlated((600, 800), 0.4, seed=16), 60, (15.0, 30.0), seed=17, sigma=(0.8, 0.6), angle=0.4)
    light_stat = _statistic(light)
    assert light_stat.name == "whitened" and light_stat.profile is not None
    light_stars = _model_stars_for(light_stat.profile)
    assert (light_stat.peak_share, light_stat.box_share) == detector._filter_shares(light_stat.filt, light_stars)
    assert light_stat.peak_share < detector._filter_shares(light_stat.filt, light_stars[:18])[0]
    # 2. Stronger correlation (ratio 3.0, 1.4 px stars with 3 % white share): the whitened filter's negative surround
    # makes star / response increase with width, so the narrower end (-0.25 px) binds peak_share
    strong, _ = _wide_star_frame(1.4, 0.03)
    strong_stat = _statistic(strong)
    assert strong_stat.name == "whitened" and strong_stat.profile is not None
    without_narrow = [
        detector._elliptical_gaussian(20, sa, sb, strong_stat.profile.angle_rad, dy, dx, footprint=False)
        for sa, sb in (
            (strong_stat.profile.sigma_major_px, strong_stat.profile.sigma_minor_px),
            (strong_stat.profile.sigma_major_px + 0.25, strong_stat.profile.sigma_minor_px + 0.25),
        )
        for dy in detector.STAR_OFFSETS_PX
        for dx in detector.STAR_OFFSETS_PX
    ]
    assert strong_stat.peak_share < detector._filter_shares(strong_stat.filt, without_narrow)[0]
    # 3. Narrow stars (0.55 px isotropic and 0.75 by 0.55 px): s - 0.25 clamps at PROFILE_MIN_SIGMA_PX on both axes
    for narrow_sigma in ((0.55, 0.55), (0.75, 0.55)):
        narrow, _ = _with_stars(_correlated((500, 700), CORRELATION_SIGMA_PX[3.0], seed=28), 50, (18.0, 32.0), seed=29,
                                sigma=narrow_sigma, angle=0.3)
        n_stat = _statistic(narrow)
        assert n_stat.name == "whitened" and n_stat.profile is not None
        assert n_stat.profile.sigma_minor_px - 0.25 < detector.PROFILE_MIN_SIGMA_PX
        unclamped = [
            detector._elliptical_gaussian(20, sa, sb, n_stat.profile.angle_rad, dy, dx, footprint=False)
            for sa, sb in (
                (n_stat.profile.sigma_major_px - 0.25, n_stat.profile.sigma_minor_px - 0.25),
                (n_stat.profile.sigma_major_px, n_stat.profile.sigma_minor_px),
                (n_stat.profile.sigma_major_px + 0.25, n_stat.profile.sigma_minor_px + 0.25),
            )
            for dy in detector.STAR_OFFSETS_PX
            for dx in detector.STAR_OFFSETS_PX
        ]
        assert (n_stat.peak_share, n_stat.box_share) == detector._filter_shares(n_stat.filt, _model_stars_for(n_stat.profile))
        assert n_stat.box_share != detector._filter_shares(n_stat.filt, unclamped)[1]
    # 4. On frames that keep the kernel with a measured profile (both narrower and wider than 1.4 px), the kernel's
    # gate shares come from the measured star at STAR_OFFSETS_PX, not fixed PEAK_GATE_SHARE / BOX_GATE_SHARE
    for white_sigma in ((1.15, 1.15), (1.8, 1.8)):
        wf, _ = _with_stars(_correlated((500, 700), 0.0, seed=30), 50, (18.0, 32.0), seed=31, sigma=white_sigma)
        w_stat = _statistic(wf)
        assert w_stat.name == "matched" and w_stat.profile is not None
        expected_k = detector._filter_shares(KERNEL, _model_stars_for(w_stat.profile, margin=None))
        assert (w_stat.peak_share, w_stat.box_share) == expected_k
        assert w_stat.peak_share != detector.PEAK_GATE_SHARE


def _statistic_with(
    negative: detector.NegativeImage,
    *,
    name: str = "matched",
    negative_whitened: detector.NegativeImage | None = None,
) -> detector.DetectionStatistic:
    blank = np.zeros((10, 10))
    return detector.DetectionStatistic(
        blank, blank + 1.0, name, detector.PEAK_GATE_SHARE, detector.BOX_GATE_SHARE, 1.0,
        2.0 if negative_whitened is not None else None, negative, negative_whitened, None, None, 0,
    )


@pytest.mark.parametrize(("count", "flagged"), [(1, False), (2, True)], ids=["at the target", "above it"])
def test_noise_tail_is_raised_above_the_target_only(count: int, flagged: bool) -> None:
    # A 2 MP frame: the target is FALSE_TARGET_PER_MP times 2, so 1.0 peaks of the negative image, read from whichever
    # statistic scored the frame (matched or whitened)
    luma = 100.0 + np.random.default_rng(18).normal(0.0, 1.0, (1000, 2000))
    bg = np.full_like(luma, 100.0)
    limits = detector._all_limits(luma, luma, bg, 1.0, None, _statistic_with(detector.NegativeImage(count, 1.0)))
    assert ("noise_tail" in limits) is flagged
    whitened_stat = _statistic_with(
        detector.NegativeImage(0, 1.0), name="whitened", negative_whitened=detector.NegativeImage(count, 1.0)
    )
    assert ("noise_tail" in detector._all_limits(luma, luma, bg, 1.0, None, whitened_stat)) is flagged


def test_the_record_carries_the_used_statistics_negative_figure() -> None:
    measures = detector._frame_measures(_statistic_with(detector.NegativeImage(2, 0.5)), ())
    assert measures.negative_peaks == 4.0 and measures.negative_peaks_spread == pytest.approx(math.sqrt(2.0) / 0.5, rel=1e-12)
    whitened = detector._frame_measures(
        _statistic_with(detector.NegativeImage(1, 0.5), name="whitened", negative_whitened=detector.NegativeImage(3, 0.5)), ()
    )
    assert whitened.negative_matched == 2.0 and whitened.negative_whitened == 6.0
    assert whitened.negative_peaks == 6.0 and whitened.negative_peaks_spread == pytest.approx(math.sqrt(3.0) / 0.5, rel=1e-12)


# The profile's stars by their fixed rule, at the peak's bound written as its value (a peak of 10 pixel noises: the
# rule fixed before any threshold, lead's answer 2 of the pair), so a bound moved by a change fails here (AUDIT M's
# mutant D7, the bound at 8, lived)
PROFILE_PEAK_EDGES = [("at 10 noises", 10.0, True), ("just under 10 noises", 10.0 * (1.0 - 1e-9), False)]


@pytest.mark.parametrize(("name", "peak", "chosen"), PROFILE_PEAK_EDGES, ids=[e[0] for e in PROFILE_PEAK_EDGES])
def test_a_profile_star_peaks_at_least_ten_pixel_noises_up(name: str, peak: float, chosen: bool) -> None:
    residual = np.zeros((41, 41))
    residual[20, 20] = peak
    stars = detector._profile_stars(residual, 1.0, np.zeros_like(residual, dtype=bool))
    assert (stars == [(peak, 20, 20)]) is chosen, (name, stars)


# The covariance's patches keep 3 px (Chebyshev) from any pixel 4 pixel noises from 0, written as values (AUDIT M's
# mutant E4, the surround at 1 px, lived): a star pixel 3 px outside a patch takes it away, one 4 px outside does not
SURROUND_EDGES = [("3 px outside", 3, 0), ("4 px outside", 4, 1)]


@pytest.mark.parametrize(("name", "gap", "kept"), SURROUND_EDGES, ids=[e[0] for e in SURROUND_EDGES])
def test_a_noise_patch_keeps_three_pixels_from_a_star(name: str, gap: int, kept: int) -> None:
    # One patch's place on the grid: rows and columns 7 to 13 of a 21 x 28 residual; the star pixel sits `gap` px to the
    # right of its last column, on its middle row, at exactly 4 noises (a star by the rule's inclusive bound)
    residual = np.zeros((21, 28))
    valid = np.zeros_like(residual, dtype=bool)
    valid[7:14, 7:14] = True
    residual[10, 13 + gap] = 4.0
    assert detector._noise_patches(residual, valid, 1.0).shape[0] == kept, name
    residual[10, 13 + gap] = 4.0 * (1.0 - 1e-9)
    assert detector._noise_patches(residual, valid, 1.0).shape[0] == 1, name


def _whitened_frame() -> np.ndarray:
    """The frame on which the whitened filter is chosen (60 stars of peak 15 to 30 on noise correlated at ratio 3.0)."""
    frame, _ = _with_stars(_correlated((900, 1300), CORRELATION_SIGMA_PX[3.0], seed=10), 60, (15.0, 30.0), seed=11)
    return frame


def test_a_quiet_patch_sits_at_the_whitened_maps_floor_and_gives_no_detection() -> None:
    # A 300 x 300 patch with a thousandth of the frame's noise: its tiles' measured spread is far under the floor, so
    # the map there is the floor and nothing in it reaches the threshold (AUDIT M's mutant E6, the floor at 0, lived)
    frame = _whitened_frame()
    frame[300:600, 500:800] = 100.0 + 0.001 * np.random.default_rng(21).normal(0.0, 1.0, (300, 300))
    statistic = _statistic(frame)
    assert statistic.name == "whitened"
    patch = statistic.noise_map[320:580, 520:780]
    assert float(np.min(patch)) > 0.0 and float(np.max(patch)) == float(np.min(patch))
    dets = detect_point_sources(frame)
    assert not any(320 <= d.y_px < 580 and 520 <= d.x_px < 780 for d in dets)


def test_near_the_black_clip_the_frame_keeps_the_kernel_where_the_whitened_filter_would_win() -> None:
    # The same frame brought down to its black clip (its sky 0.5 noises above 0, clipped there): flagged near_black_clip,
    # and the kernel scores it, though its stars still gain under the whitened filter (AUDIT M's mutant A9, the flag
    # never passed to the rule, lived)
    frame = np.clip(_whitened_frame() - 100.0 + 0.5, 0.0, None)
    rating = _statistic(frame).rating
    assert rating is not None and rating.gain >= 1.1 and rating.gain * rating.kept_whitened >= 1.1 * rating.kept_matched
    dets = detect_point_sources(frame)
    assert dets and all("near_black_clip" in d.noise_limits and d.statistic == "matched" for d in dets)


def _wide_star_frame(width: float, white: float) -> tuple[np.ndarray, np.ndarray]:
    """AUDIT M's probe_wide3.py frame (realsky-pair finding 1): 500 x 700, noise smoothed at 1.2 px with a white share,
    120 stars of `width` px and peak 2 to 15, seed 9301."""
    shape = (500, 700)
    rng = np.random.default_rng(9301)
    smooth = ndimage.gaussian_filter(rng.normal(0, 1, shape), 1.2, mode="wrap")
    smooth /= smooth.std()
    frame = 100.0 + math.sqrt(1 - white) * smooth + math.sqrt(white) * rng.normal(0, 1, shape)
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]]
    stars: list[tuple[float, float]] = []
    while len(stars) < 120:
        x, y = rng.uniform(15, shape[1] - 15), rng.uniform(15, shape[0] - 15)
        if all((x - a) ** 2 + (y - b) ** 2 >= 225 for a, b in stars):
            stars.append((x, y))
            frame += rng.uniform(2.0, 15.0) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * width * width))
    return frame, np.array(stars)


def _score_stars(dets: list[detector.SourceDetection], xy: np.ndarray) -> tuple[int, int]:
    from scipy.spatial import cKDTree

    if not dets:
        return 0, 0
    pts = np.array([(d.x_px, d.y_px) for d in dets], dtype=np.float64)
    dists, idxs = cKDTree(xy).query(pts)
    false = int(np.count_nonzero(dists > 3.0))
    found = len({int(i) for d, i in zip(dists, idxs) if d <= 2.5})
    return found, false


# Finding 1's nine probe_wide3.py rows (seed 9301): stars wider than the kernel on noise smoothed at 1.2 px with 1 to
# 5 % white share. At 1.4 px the whitened filter is chosen and exceeds main even at default 4.75 (97, 96, 96 against
# main's 96, 95, 95; 100, 100, 99 at 4.5, 0 false); at 1.8 and 2.4 px the kernel is chosen and sits at 96, 99, 99 and
# 98, 99, 100 against main's 98, 96, 96 and 99, 99, 98, pinned within the 4-stars-a-frame kernel-gates spread owed by
# the gates-levels unit: (name, width, white, statistic, threshold, main_found, allowance)
WIDE_ROWS = [
    ("1.4 px, white 0.01", 1.4, 0.01, "whitened", 4.5, 96, 0),
    ("1.4 px, white 0.03", 1.4, 0.03, "whitened", 4.5, 95, 0),
    ("1.4 px, white 0.05", 1.4, 0.05, "whitened", 4.5, 95, 0),
    ("1.8 px, white 0.01", 1.8, 0.01, "matched", 4.5, 98, 4),
    ("1.8 px, white 0.03", 1.8, 0.03, "matched", 4.25, 96, 4),
    ("1.8 px, white 0.05", 1.8, 0.05, "matched", 4.25, 96, 4),
    ("2.4 px, white 0.01", 2.4, 0.01, "matched", 4.75, 99, 4),
    ("2.4 px, white 0.03", 2.4, 0.03, "matched", 4.75, 99, 4),
    ("2.4 px, white 0.05", 2.4, 0.05, "matched", 4.75, 98, 4),
]


@pytest.mark.parametrize(("name", "width", "white", "expected_stat", "threshold", "main_found", "allowance"), WIDE_ROWS,
                         ids=[r[0] for r in WIDE_ROWS])
def test_stars_wider_than_the_kernel_keep_the_right_filter_and_are_found(
    name: str, width: float, white: float, expected_stat: str, threshold: float, main_found: int, allowance: int
) -> None:
    frame, xy = _wide_star_frame(width, white)
    dets = detect_point_sources(frame, snr_threshold=threshold)
    found, false = _score_stars(dets, xy)
    assert dets and dets[0].statistic == expected_stat, name
    assert false == 0 and found >= main_found - allowance, (name, found, false, main_found)


# Finding 1's corr_1.2_wide frames (bar_m.py: 800 x 1100, noise smoothed at 1.2 px with 0 % white share, 300 stars of
# 1.8 px at least 14 px apart, seeds 7301 to 7303). v1 chose the whitened filter and found 0 stars against main's 695;
# v2 keeps the kernel on all three and finds 224 + 231 + 232 = 687 against main's 225 + 231 + 239 = 695 at equal false
# counts (0, 0, 1), pinned within the same 4-stars-a-frame kernel-gates spread
CORR_WIDE_SEEDS = [(7301, 4.75, 225, 0), (7302, 4.5, 231, 0), (7303, 4.5, 239, 1)]


def test_wide_stars_on_pure_correlated_noise_keep_the_kernel_within_the_spread() -> None:
    found_sum = main_sum = 0
    for seed, threshold, main_found, main_false in CORR_WIDE_SEEDS:
        rng = np.random.default_rng(seed)
        shape = (800, 1100)
        g = ndimage.gaussian_filter(rng.normal(0.0, 1.0, shape), 1.2, mode="wrap")
        frame = 100.0 + g / float(np.std(g))
        stars: list[tuple[float, float, float]] = []
        tries = 0
        while len(stars) < 300 and tries < 18000:
            tries += 1
            x, y = rng.uniform(14, shape[1] - 14), rng.uniform(14, shape[0] - 14)
            if all((x - sx) ** 2 + (y - sy) ** 2 >= 196.0 for sx, sy, _ in stars):
                stars.append((x, y, rng.uniform(1.5, 15.0)))
        for x, y, amp in stars:
            x0, x1, y0, y1 = int(x) - 10, int(x) + 11, int(y) - 10, int(y) + 11
            yy, xx = np.mgrid[y0:y1, x0:x1]
            frame[y0:y1, x0:x1] += amp * np.exp(-0.5 * (((xx - x) ** 2 + (yy - y) ** 2) / (1.8 * 1.8)))
        dets = detect_point_sources(frame, snr_threshold=threshold)
        assert dets and dets[0].statistic == "matched", seed
        found, false = _score_stars(dets, np.array([(x, y) for x, y, _ in stars]))
        assert false <= main_false, (seed, false, main_false)
        found_sum += found
        main_sum += main_found
    assert found_sum >= main_sum - 4 * len(CORR_WIDE_SEEDS), (found_sum, main_sum)


def _audit_m_frame(name: str, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """AUDIT M's bar frames for realsky-pair finding 2 (its bar_m.py build, copied): 800 x 1100, noise smoothed at the
    named length and scaled to 1, 350 stars of 1.15 px and peak 1.5 to 15 at least 12 px apart; "crowded" is noise at
    0.7 px with 2200 stars of 1.4 px at least 7 px apart."""
    rng = np.random.default_rng(seed)
    shape = (800, 1100)
    length = 0.7 if name == "crowded" else float(name.split("_")[1])
    noise = ndimage.gaussian_filter(rng.normal(0.0, 1.0, shape), length, mode="wrap")
    frame = 100.0 + 1.0 * (noise / float(np.std(noise)))
    count, spacing, sigma = (2200, 7.0, 1.4) if name == "crowded" else (350, 12.0, 1.15)
    stars: list[tuple[float, float, float]] = []
    tries = 0
    while len(stars) < count and tries < count * 60:
        tries += 1
        x, y = rng.uniform(14, shape[1] - 14), rng.uniform(14, shape[0] - 14)
        if all((x - sx) ** 2 + (y - sy) ** 2 >= spacing * spacing for sx, sy, _ in stars):
            stars.append((x, y, rng.uniform(1.5, 15.0)))
    c, s = math.cos(0.0), math.sin(0.0)
    for x, y, amp in stars:
        x0, x1, y0, y1 = int(x) - 10, int(x) + 11, int(y) - 10, int(y) + 11
        yy, xx = np.mgrid[y0:y1, x0:x1]
        u = c * (xx - x) + s * (yy - y)
        v = -s * (xx - x) + c * (yy - y)
        frame[y0:y1, x0:x1] += amp * np.exp(-0.5 * (u * u / (sigma * sigma) + v * v / (sigma * sigma)))
    return frame, np.array([(x, y) for x, y, _ in stars])


# Finding 2's pins (lead, 19:11 and 19:13), over all of AUDIT M's bar_m.py seeds (7301 to 7308, 7301 to 7303 for
# crowded), compared at an equal false count from both sides: main's threshold raised until its false count is at most
# v2's at default 4.75, and v2's adjusted while staying at or under main's false count there. Per frame:
# (seed, v2_threshold, main_found, main_false) [tested, realsky-4-v2 report, main = 932c423c, detector == 61e1ca2a].
# Sums: 0.6 px 2198 vs 2179 (+2.38/seed, se 1.86) and crowded 5336 vs 5062 (+91.33/seed, se 15.62) at or above main;
# 0.7 px 2099 vs 2123 (-3.00/seed, se 1.86), within the 4-stars/seed spread; 0.8 px 2117 vs 2110 (+0.88/seed, se 1.53;
# pinned under the 10-stars/seed stated exception owed by the gates-levels unit)
PIN_FRAMES = {
    "corr_0.6": [
        (7301, 4.5, 262, 0), (7302, 4.25, 281, 0), (7303, 4.5, 269, 0), (7304, 4.5, 277, 0),
        (7305, 4.5, 269, 0), (7306, 4.5, 266, 0), (7307, 4.25, 279, 0), (7308, 4.75, 276, 0),
    ],
    "corr_0.7": [
        (7301, 4.5, 256, 0), (7302, 4.5, 273, 0), (7303, 4.5, 262, 0), (7304, 4.5, 267, 0),
        (7305, 4.5, 264, 0), (7306, 4.5, 261, 0), (7307, 4.25, 271, 0), (7308, 4.5, 269, 0),
    ],
    "corr_0.8": [
        (7301, 5.0, 263, 2), (7302, 4.75, 269, 0), (7303, 4.75, 257, 0), (7304, 4.5, 266, 0),
        (7305, 4.75, 263, 0), (7306, 4.75, 260, 0), (7307, 4.5, 272, 0), (7308, 4.75, 260, 0),
    ],
    "crowded": [(7301, 4.0, 1654, 0), (7302, 3.75, 1724, 0), (7303, 3.75, 1684, 0)],
}
PIN_ALLOWANCE = {"corr_0.6": 0, "corr_0.7": 4, "corr_0.8": 10, "crowded": 0}


@pytest.mark.parametrize("name", list(PIN_FRAMES))
def test_light_correlation_and_crowding_against_mains_measured_counts(name: str) -> None:
    found_sum = main_sum = 0
    for seed, threshold, main_found, main_false in PIN_FRAMES[name]:
        frame, stars = _audit_m_frame(name, seed)
        dets = detect_point_sources(frame, snr_threshold=threshold)
        found, false = _score_stars(dets, stars)
        assert false <= main_false, (name, seed, false, main_false)
        found_sum += found
        main_sum += main_found
    assert found_sum >= main_sum - PIN_ALLOWANCE[name] * len(PIN_FRAMES[name]), (name, found_sum, main_sum)
