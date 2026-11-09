"""The detector's noise never falls below the rounding noise of the data's own value step, nor below the old absolute
floor of 0.001 (solver-10l).

Covers:
- A dark 8-bit frame (sky at 1 grey level, as in the real night clips, where the MAD of the background is 0) with
  30 injected stars of known position: today the detector reports thousands of sources there; with the floor it
  recovers the stars, and over 20 noise seeds its false sources stay under a bound derived from the rounding odds.
- The floor follows the measured value step, not the dtype: the same frame as 16-bit (x257), as floats scaled to
  0..1 and as grey RGB gives the same detections; a colour RGB frame's luma gets the weighted rounding noise.
- A frame on no grid (a stack, a colour photo's luma on the 0..1 scale that solve_phone_photo hands the detector) keeps
  the old floor: never a lower noise and never more detections than the old rule gives.
- Over generated dark integer frames on three scales, the noise the detector divides by is never below the parent
  commit's rule (its MAD, else its interquartile range / 1.349, then at least 0.001), written out here.
- Frame registration in stack_video_frames_for_calibration keeps the parent's noise estimate, so a stack's shifts do
  not change.
- Forced photometry and injection recovery use the same floor.
- An average of frames sits on a finer grid (or none) and keeps its lower noise.
- The two real decode paths a clip takes, the upload job's PGM decode (8-bit values) and discovery's 16-bit gray
  decode (0..1 values), feed the detector the same dark frame with the same outcome.
All frames here are simulated.
"""

from __future__ import annotations

import hashlib
from math import erfc, sqrt
from pathlib import Path
import shutil
import subprocess

from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest
from scipy import ndimage

from services.science import detector, phone_photo_solver
from services.science.detector import detect_point_sources

FFMPEG = shutil.which("ffmpeg")
WIDTH, HEIGHT, STARS = 480, 360, 30
MATCH_PX = 1.5
POSITION_TOLERANCE_PX = 1e-3
# The dark frame: sky and read noise in grey levels, star PSF and the brightest star's peak
BACKGROUND, READ_NOISE, PSF_SIGMA_PX, MAX_AMPLITUDE = 1.2, 0.3, 1.3, 90.0
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)
ROUNDING_SIGMA = 1.0 / np.sqrt(12.0)
# The old code's absolute floor, on whatever scale the detector is handed
OLD_FLOOR = 1e-3
# Twenty fixed noise seeds for the false-source statistics
NOISE_SEEDS = range(101, 121)
# Gates of _measure_peak_photometry that every detection passes: its peak at least 2.3 sigma above the background and
# all four neighbours above 0.65 sigma, or (outside forced photometry) its peak alone 14 sigma up, as a bright impulse
PEAK_SIGMA, SIDE_SIGMA, IMPULSE_SIGMA = 2.3, 0.65, 14.0
# Pixels a detection can peak at: 4 px in from each edge
INTERIOR_PX = (WIDTH - 8) * (HEIGHT - 8)
# A detection farther than FAR_PX from every star is a false one on bare sky: its centroid lies in the 5x5 box of its
# peak, so the peak and its four neighbours lie at least FAR_PX - 2 sqrt(2) - 1 = 7.2 px from every star
FAR_PX = 11.0
PEAK_OFFSET_PX = 2.0 * np.sqrt(2.0)
# What 30 stars of at most MAX_AMPLITUDE add there: under 0.001 of a level, kept in the bound below
FAR_STARLIGHT = STARS * MAX_AMPLITUDE * np.exp(-((FAR_PX - PEAK_OFFSET_PX - 1.0) ** 2) / (2.0 * PSF_SIGMA_PX**2))


def _stars(seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    points: list[tuple[float, float]] = []
    while len(points) < STARS:
        x, y = rng.uniform(20, WIDTH - 20), rng.uniform(20, HEIGHT - 20)
        if all(np.hypot(x - px, y - py) > 25 for px, py in points):
            points.append((x, y))
    return np.array(points)


def _dark_frame(
    noise_seed: int = 6,
    background: float = BACKGROUND,
    read_noise: float = READ_NOISE,
    offset_yx: tuple[float, float] = (0.0, 0.0),
) -> np.ndarray:
    """An 8-bit dark frame: the sky rounds to 1 almost everywhere, so its MAD is 0, with the 30 stars of `_stars()`
    at 25 to 90 grey levels, moved by `offset_yx` pixels; `noise_seed` changes only the noise."""
    amplitudes = np.random.default_rng(4).uniform(25.0, MAX_AMPLITUDE, STARS)
    luma = np.full((HEIGHT, WIDTH), background) + np.random.default_rng(noise_seed).normal(0.0, read_noise, (HEIGHT, WIDTH))
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    for (x, y), amp in zip(_stars(), amplitudes):
        x, y = x + offset_yx[1], y + offset_yx[0]
        luma += amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * PSF_SIGMA_PX**2))
    return np.clip(np.rint(luma), 0, 255).astype(np.uint8)


def _parent_background(luma: np.ndarray) -> np.ndarray:
    """The parent commit's background (e64b0f67's detector, written out): a 7-sample median filter on every
    `stride`-th pixel, zoomed back to the frame's size by linear interpolation."""
    h, w = luma.shape
    stride = max(1, min(4, min(h, w) // 32))
    small_bg = ndimage.median_filter(luma[::stride, ::stride], size=7, mode="nearest")
    return ndimage.zoom(small_bg, (h / small_bg.shape[0], w / small_bg.shape[1]), order=1, mode="nearest")[:h, :w]


def _parent_noise(residual: np.ndarray) -> float:
    """The parent commit's noise estimate (e64b0f67's detector, written out): 1.4826 times the MAD of the residuals,
    or, when that is under 1e-7, their interquartile range / 1.349."""
    values = np.asarray(residual, dtype=np.float64).ravel()
    sigma = 1.4826 * float(np.median(np.abs(values - np.median(values))))
    if sigma < 1e-7:
        q75, q25 = np.percentile(values, [75.0, 25.0])
        sigma = float((q75 - q25) / 1.349)
    return sigma


def _parent_effective_sigma(image: np.ndarray) -> float:
    """The noise the parent's detector divided by on a 2D frame: its noise estimate, at least 0.001."""
    luma = np.asarray(image, dtype=np.float64)
    return max(_parent_noise(luma - _parent_background(luma)), OLD_FLOOR)


def _score(dets, truth: np.ndarray, scale_xy: float = 1.0) -> tuple[int, int]:
    """(recovered stars, other sources) for detections against the injected positions."""
    found = np.array([[d.x_px * scale_xy, d.y_px * scale_xy] for d in dets]) if dets else np.zeros((0, 2))
    if found.size == 0:
        return 0, 0
    dist = np.hypot(found[:, None, 0] - truth[None, :, 0], found[:, None, 1] - truth[None, :, 1])
    recovered = int((dist.min(axis=0) <= MATCH_PX).sum())
    others = int((dist.min(axis=1) > MATCH_PX).sum())
    return recovered, others


def _far(dets) -> int:
    """Detections farther than FAR_PX from every star: false sources on bare sky."""
    truth = _stars()
    return sum(1 for d in dets if float(np.min(np.hypot(truth[:, 0] - d.x_px, truth[:, 1] - d.y_px))) > FAR_PX)


def _level_probabilities(mean: float, top: int = 8) -> np.ndarray:
    """P(a pixel of true value mean + READ_NOISE z, z standard normal, rounds to level k) for k = 0..top; level 0 takes
    the clipped low tail and level `top` the rest of the high tail."""
    cdf = [0.5 * erfc(-(k + 0.5 - mean) / (READ_NOISE * sqrt(2.0))) for k in range(top)]
    return np.diff(np.concatenate([[0.0], cdf, [1.0]]))


def _expected_false_sources(values: np.ndarray, probs: np.ndarray, floor: float, background: float) -> float:
    """An upper bound on the mean number of false sources among INTERIOR_PX independent sky pixels whose values take
    `values` with `probs`, for a background no lower than `background` and a noise no lower than `floor`.

    A false source needs five distinct pixels up, its peak PEAK_SIGMA floors and its four neighbours SIDE_SIGMA floors,
    or its peak alone IMPULSE_SIGMA floors; the other gates (matched filter, local maximum, PSF shape) only remove
    more. So the chance at one pixel is at most p_peak p_side^4 + p_impulse.
    """
    residual = np.asarray(values, dtype=np.float64) - background
    p_peak = float(probs[residual >= PEAK_SIGMA * floor].sum())
    p_side = float(probs[residual >= SIDE_SIGMA * floor].sum())
    p_impulse = float(probs[residual >= IMPULSE_SIGMA * floor].sum())
    return INTERIOR_PX * (p_peak * p_side**4 + p_impulse)


def _seed_mean_limit(bound: float, draws: int) -> float:
    """The most the mean of `draws` false counts may reach when their true mean is at most `bound`: a count of many
    rare, nearly independent events is close to Poisson (variance at most `bound`), and its mean over `draws` passes
    three standard errors above `bound` with a chance of about 0.1 %."""
    return bound + 3.0 * np.sqrt(bound / draws)


def _sites(luma: np.ndarray, floor: float, *, impulses: bool) -> np.ndarray:
    """Pixels where a detection can peak, by the gates above, against the detector's own background and a noise of
    `floor`: a necessary condition, so a frame holds at most this many detections there."""
    bg, _ = detector._estimate_background_and_noise(np.asarray(luma, dtype=np.float64))
    residual = np.asarray(luma, dtype=np.float64) - bg
    side = residual >= SIDE_SIGMA * floor
    sites = np.zeros_like(side)
    sites[1:-1, 1:-1] = (residual[1:-1, 1:-1] >= PEAK_SIGMA * floor) & side[:-2, 1:-1] & side[2:, 1:-1] & side[1:-1, :-2] & side[1:-1, 2:]
    if impulses:
        sites |= residual >= IMPULSE_SIGMA * floor
    return sites


def _far_sites(luma: np.ndarray, floor: float) -> int:
    """Sites that can hold a detection farther than FAR_PX from every star."""
    truth = _stars()
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    nearest = np.min([np.hypot(xx - x, yy - y) for x, y in truth], axis=0)
    return int((_sites(luma, floor, impulses=True) & (nearest > FAR_PX - PEAK_OFFSET_PX)).sum())


def _pairs_frame() -> np.ndarray:
    """A flat 8-bit frame at 1 with 300 one-level pairs, as compression leaves them: under 1 % of the pixels."""
    rng = np.random.default_rng(12)
    frame = np.ones((HEIGHT, WIDTH), dtype=np.uint8)
    for x, y in zip(rng.integers(10, WIDTH - 10, 300), rng.integers(10, HEIGHT - 10, 300)):
        frame[y, x : x + 2] = 2
    return frame


def test_the_background_noise_no_longer_collapses_on_a_frame_whose_mad_is_zero() -> None:
    frame = _dark_frame().astype(np.float64)
    assert np.median(frame) == 1.0
    # Over half the pixels share one value: the MAD is 0, as in the real clips
    assert float(np.median(np.abs(frame - np.median(frame)))) == 0.0
    _, sigma = detector._estimate_background_and_noise(frame)
    # The interquartile range is 0 as well (the sky rounds to 0, 1 and 2 with chances 0.010, 0.831 and 0.159), so the
    # noise is the spread of the bare sky's rounded values, 0.38 levels: their standard deviation, not their root mean
    # square from the background (0.41 here [tested]), which adds the 0.15 level the sky's mean sits above the 1 most
    # of it rounds to
    truth = _stars()
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    nearest = np.min([np.hypot(xx - x, yy - y) for x, y in truth], axis=0)
    assert sigma == pytest.approx(float(np.std(frame[nearest > FAR_PX])), rel=0.03)


def test_injected_stars_are_recovered_without_thousands_of_false_sources() -> None:
    # The sky rounds from 1.2 + 0.3 z to 1, or to 2 with chance Q(1) = 0.159 (with the stars' far light, slightly
    # more). Against a background of 1 and a noise of at least 1/sqrt(12), a pixel at 1 passes neither the peak gate
    # (0.66 levels) nor the side gate (0.19 levels), so each false source needs five pixels at 2 or more: on average
    # at most INTERIOR_PX Q(1)^5 = 16.7 of them per frame (17.0 with the far starlight), plus under 1e-40 bright
    # impulses (a pixel 6 levels up)
    probs = _level_probabilities(BACKGROUND + FAR_STARLIGHT)
    background = 1.0 - 1e-6
    bound = _expected_false_sources(np.arange(probs.size), probs, ROUNDING_SIGMA, background)
    false_counts = []
    for seed in NOISE_SEEDS:
        frame = _dark_frame(noise_seed=seed)
        bg, _ = detector._estimate_background_and_noise(frame.astype(np.float64))
        assert float(bg.min()) >= background, seed  # the bound's premise
        dets = detect_point_sources(frame)
        recovered, _ = _score(dets, _stars())
        assert recovered >= STARS - 3, (seed, recovered)
        false_counts.append(_far(dets))
    assert np.mean(false_counts) <= _seed_mean_limit(bound, len(false_counts)), (bound, false_counts)


@pytest.mark.parametrize(
    ("label", "convert"),
    [
        ("16-bit, x257", lambda f: f.astype(np.uint16) * 257),
        ("16-bit, x256", lambda f: f.astype(np.uint16) * 256),
        ("float, /255", lambda f: f.astype(np.float64) / 255.0),
        ("grey RGB", lambda f: np.repeat(f[:, :, None], 3, axis=2)),
    ],
)
def test_the_floor_follows_the_measured_value_step(label: str, convert) -> None:
    frame = _dark_frame()
    base = sorted((d.x_px, d.y_px) for d in detect_point_sources(frame))
    other = sorted((d.x_px, d.y_px) for d in detect_point_sources(convert(frame)))
    assert len(other) == len(base), label
    # Float positions within a stated tolerance, never compared bit for bit
    assert np.allclose(np.array(other), np.array(base), atol=POSITION_TOLERANCE_PX), label


def test_a_dark_colour_frame_with_independent_channel_noise_reads_the_same_way() -> None:
    # Phone JPEGs are colour: each channel rounds on its own, so the luma sits on no grid of its own. The bound is the
    # one above, for the luma 0.2126 R + 0.7152 G + 0.0722 B of three channels each rounded from 1.2 + 0.3 z, against
    # its noise floor, the weighted rounding noise sqrt(sum of squared weights) / sqrt(12) = 0.216: at most 188 per
    # frame. The bound is loose (a luma one green level up passes the side gate alone), but it is derived, not fitted
    channel = _level_probabilities(BACKGROUND + FAR_STARLIGHT)
    levels = np.arange(channel.size)
    r, g, b = np.meshgrid(levels, levels, levels, indexing="ij")
    values = LUMA_WEIGHTS[0] * r + LUMA_WEIGHTS[1] * g + LUMA_WEIGHTS[2] * b
    probs = channel[r] * channel[g] * channel[b]
    floor = np.sqrt(sum(w * w for w in LUMA_WEIGHTS)) * ROUNDING_SIGMA
    background = 1.0 - 1e-6
    bound = _expected_false_sources(values.ravel(), probs.ravel(), floor, background)
    false_counts = []
    for seed in NOISE_SEEDS:
        colour = np.stack([_dark_frame(noise_seed=200 + 3 * seed + c) for c in range(3)], axis=2)
        bg, _ = detector._estimate_background_and_noise(detector._to_luma_2d(colour))
        assert float(bg.min()) >= background, seed  # the bound's premise
        dets = detect_point_sources(colour)
        recovered, _ = _score(dets, _stars())
        assert recovered >= STARS - 3, (seed, recovered)
        false_counts.append(_far(dets))
    # Measuring the noise after the matched filter would lower the count further [open]
    assert np.mean(false_counts) <= _seed_mean_limit(bound, len(false_counts)), (bound, false_counts)


class _Stop(Exception):
    pass


def _phone_photo_detector_call(image: np.ndarray, monkeypatch: pytest.MonkeyPatch):
    """solve_phone_photo's own detector call: the luma it hands the detector, its arguments, the detections and the
    noise the detector divided by."""
    seen: dict = {}
    sigmas: list[float] = []
    real_sigma = detector._effective_sigma

    def recording_sigma(*args):
        sigmas.append(float(real_sigma(*args)))
        return sigmas[-1]

    def stop_after(luma, **kwargs):
        seen.update(luma=luma, kwargs=kwargs, dets=detect_point_sources(luma, **kwargs))
        raise _Stop

    with monkeypatch.context() as patch:
        patch.setattr(detector, "_effective_sigma", recording_sigma)
        patch.setattr(phone_photo_solver, "detect_point_sources", stop_after)
        with pytest.raises(_Stop):
            phone_photo_solver.solve_phone_photo(
                image, source_asset_id="asset-noise-model", source_sha256=hashlib.sha256(image.tobytes()).hexdigest()
            )
    return seen["luma"], seen["kwargs"], seen["dets"], sigmas


def _stack(frames: int) -> np.ndarray:
    """The upload job's composite of a clip: its 8-bit frames aligned and averaged (it decodes up to 8)."""
    return detector.stack_video_frames_for_calibration(
        [_dark_frame(noise_seed=40 + k).astype(np.float64) for k in range(frames)]
    ).composite_luma


@pytest.mark.parametrize(
    ("label", "make"),
    [
        ("dark colour frame", lambda: np.stack([_dark_frame(noise_seed=20 + c) for c in range(3)], axis=2)),
        ("3-frame stack", lambda: _stack(3)),
        ("8-frame stack", lambda: _stack(8)),
    ],
)
def test_solve_phone_photo_never_gets_a_lower_noise_or_more_detections_than_the_old_floor_gave(
    label: str, make, monkeypatch: pytest.MonkeyPatch
) -> None:
    # solve_phone_photo hands the detector a 2D luma on the 0..1 scale. A stack, or the Rec. 709 luma of a colour
    # photo, sits on no grid, so only the old absolute floor keeps its noise from falling to its MAD
    luma, kwargs, dets, sigmas = _phone_photo_detector_call(make(), monkeypatch)
    assert float(np.max(luma)) <= 1.0 and len(sigmas) == 1, label
    # The old rule, max(MAD noise, 0.001), on the same luma
    with monkeypatch.context() as patch:
        patch.setattr(detector, "_effective_sigma", lambda sigma_bg, *_: max(float(sigma_bg), OLD_FLOOR))
        old = detect_point_sources(luma, **kwargs)
    # Both checks at once, so a failure shows which of them broke
    assert (sigmas[0] >= OLD_FLOOR, len(dets) <= len(old)) == (True, True), (label, sigmas[0], len(dets), len(old))


SCALES = {
    "0..255": lambda f: f.astype(np.float64),
    "0..1": lambda f: f.astype(np.float64) / 255.0,
    "16-bit, x257": lambda f: f.astype(np.uint16) * 257,
}


@settings(max_examples=40, deadline=None)
@given(
    background=st.floats(min_value=0.6, max_value=3.0),
    read_noise=st.floats(min_value=0.2, max_value=0.6),
    noise_seed=st.integers(min_value=0, max_value=100_000),
    scale=st.sampled_from(sorted(SCALES)),
)
# A sky at 1.3 with a read noise of 0.5 rounds 60 % to one level and 34 % to the next: the MAD is 0 and the
# interquartile range one level, so the parent divided by 1 / 1.349 = 0.741 levels, while the spread of the residuals
# without their top percent is 0.59 [tested]
@example(background=1.3, read_noise=0.5, noise_seed=6, scale="0..255")
@example(background=1.3, read_noise=0.5, noise_seed=6, scale="0..1")
@example(background=1.3, read_noise=0.5, noise_seed=6, scale="16-bit, x257")
def test_pbt_the_noise_the_detector_divides_by_is_never_below_the_parents(
    background: float, read_noise: float, noise_seed: int, scale: str
) -> None:
    image = SCALES[scale](_dark_frame(noise_seed=noise_seed, background=background, read_noise=read_noise))
    parent = _parent_effective_sigma(image)
    sigmas: list[float] = []
    real_sigma = detector._effective_sigma

    def recording_sigma(*args):
        sigmas.append(float(real_sigma(*args)))
        return sigmas[-1]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(detector, "_effective_sigma", recording_sigma)
        detect_point_sources(image)
        detector.verify_candidates_on_original_pixels(image, [(float(x), float(y)) for x, y in _stars()])
    # Detection and forced photometry each divide by one noise
    assert len(sigmas) == 2, sigmas
    assert min(sigmas) >= parent * (1.0 - 1e-12), (scale, background, read_noise, noise_seed, parent, sigmas)


# Where frame k's stars sit relative to frame 0, in pixels (y, x): sub-pixel and whole-pixel moves, within the
# stacker's 8 px search
CLIP_OFFSETS_YX = ((0.0, 0.0), (0.6, -0.4), (1.3, 0.9), (-0.7, 1.6), (2.1, -1.2), (0.4, 2.5))


@pytest.mark.parametrize(
    ("label", "background", "read_noise", "frame_count"),
    [
        # The test's dark sky: each frame's MAD and interquartile range are 0
        ("dark clip", BACKGROUND, READ_NOISE, len(CLIP_OFFSETS_YX)),
        # A flatter sky (98.8 % of it rounds to 1), so the composite's MAD and interquartile range are 0 as well
        ("flat clip", 1.0, 0.2, len(CLIP_OFFSETS_YX)),
        ("one frame", BACKGROUND, READ_NOISE, 1),
    ],
)
def test_frame_registration_in_the_stacker_keeps_the_parents_noise_estimate(
    label: str, background: float, read_noise: float, frame_count: int
) -> None:
    # A dark 8-bit clip whose stars move by known amounts. The stacker weights each frame's registration signal by its
    # noise, so a different noise moves the shifts: the parent's estimate (its IQR fallback gives 0 here, raised to the
    # stacker's 1e-4) must stay, or the upload job's stack of a real clip changes
    offsets = CLIP_OFFSETS_YX[:frame_count]
    frames = [
        _dark_frame(noise_seed=60 + k, background=background, read_noise=read_noise, offset_yx=offset).astype(np.float64)
        for k, offset in enumerate(offsets)
    ]
    stack = detector.stack_video_frames_for_calibration(frames)
    # The clip registers: each shift moves frame k back onto frame 0
    assert np.allclose(stack.shifts_yx, [(-dy, -dx) for dy, dx in offsets], atol=0.3), (label, stack.shifts_yx)

    def parent_estimate(luma, sky_mask=None, **_):
        assert sky_mask is None
        bg = _parent_background(np.asarray(luma, dtype=np.float64))
        return bg, _parent_noise(luma - bg)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(detector, "_estimate_background_and_noise", parent_estimate)
        parent = detector.stack_video_frames_for_calibration(frames)
    # Two runs in one process: equal within 1e-9 px, never compared bit for bit
    assert np.allclose(stack.shifts_yx, parent.shifts_yx, rtol=0.0, atol=1e-9), (label, stack.shifts_yx, parent.shifts_yx)
    assert np.allclose(stack.composite_luma, parent.composite_luma, rtol=0.0, atol=1e-9), label
    assert stack.single_frame_noise_sigma == pytest.approx(parent.single_frame_noise_sigma, rel=1e-9, abs=1e-12), label
    assert stack.stacked_noise_sigma == pytest.approx(parent.stacked_noise_sigma, rel=1e-9, abs=1e-12), label


@pytest.mark.parametrize(("threshold", "expected"), [(3.5, 165), (6.0, 0), (8.0, 0)])
def test_forced_photometry_measures_one_level_bumps_against_the_rounding_noise(threshold: float, expected: int) -> None:
    # A plus-shaped bump one level high on a flat 8-bit frame (MAD and spread 0): at the rounding noise its peak is
    # 1 / (1/sqrt(12)) = 3.46 sigma, its 3x3 sum 5 / (3/sqrt(12)) = 5.77 and its matched filter 6.4. A forced
    # measurement at threshold T needs the peak at max(2.3, 0.58 T) and the sum at max(3.6, 0.92 T) sigma: all pass at
    # 3.5, the peak (3.46 against 3.48) fails at 6, and all three fail at 8 (the matched gate is then 8). At a floor of
    # 0.001 each bump would be 1,000 sigma
    frame = np.ones((HEIGHT, WIDTH), dtype=np.uint8)
    spots = [(x, y) for x in range(20, WIDTH - 20, 30) for y in range(20, HEIGHT - 20, 30)]
    for x, y in spots:
        frame[y, x - 1 : x + 2] = 2
        frame[y - 1 : y + 2, x] = 2
    kept = detector.verify_candidates_on_original_pixels(frame, [(float(x), float(y)) for x, y in spots], snr_threshold=threshold)
    assert len(spots) == 165
    assert len(kept) == expected, (threshold, len(kept))


def _dark_colour_frame() -> np.ndarray:
    """Three dark 8-bit channels, each rounded on its own, so the luma the detector sees sits on no grid."""
    return np.stack([_dark_frame(noise_seed=200 + c) for c in range(3)], axis=2)


@pytest.mark.parametrize("label", ["pairs frame", "dark colour frame", "flat grey frame"])
def test_injection_recovery_scales_its_stars_to_the_noise_the_detector_divides_by(
    label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An injected SNR s star must be s noises in the detector's matched filter, where the noise is the one the
    # detector divides by on the 2D luma it is handed. On the pairs frame the MAD and the spread are 0, so that is the
    # rounding noise 1/sqrt(12), not the old 0.001 floor. On a colour frame each channel rounds on its own and the luma
    # sits on no grid, so it is not the channels' weighted rounding noise either. A star at most half a pixel off a
    # pixel centre in x and y keeps exp(-(0.5^2 + 0.5^2) / (4 x 1.15^2)) = 0.91 of its matched peak, and none exceeds it
    frame = {
        "pairs frame": _pairs_frame,
        "dark colour frame": _dark_colour_frame,
        # A flatter sky (98.8 % of it rounds to 1): the spread of the injected luma falls under the rounding noise
        "flat grey frame": lambda: _dark_frame(noise_seed=60, background=1.0, read_noise=0.2),
    }[label]()
    images: list[np.ndarray] = []
    detector_sigmas: list[float] = []
    real_detect = detector.detect_point_sources
    real_sigma = detector._effective_sigma
    inside_detect = [False]

    def recording_sigma(*args):
        value = float(real_sigma(*args))
        if inside_detect[0]:
            detector_sigmas.append(value)
        return value

    def recording_detect(image, **kwargs):
        images.append(np.array(image, dtype=np.float64))
        inside_detect[0] = True
        try:
            return real_detect(image, **kwargs)
        finally:
            inside_detect[0] = False

    monkeypatch.setattr(detector, "_effective_sigma", recording_sigma)
    monkeypatch.setattr(detector, "detect_point_sources", recording_detect)
    snrs = (6.0, 18.0)
    detector.evaluate_injection_recovery(frame, injected_snrs=snrs, n_per_snr=4)
    baseline, injected = images[0], images[1]
    # The noise the detector divided by on the baseline luma, the frame the stars were scaled from, and on the injected
    # luma, the call that finds them: adding the stars moves pixels off the frame's grid, so that call must keep the
    # base frame's rounding floor or its stars read many times brighter than injected
    assert len(detector_sigmas) == 2, detector_sigmas
    if label in ("pairs frame", "flat grey frame"):
        assert detector_sigmas[0] == pytest.approx(ROUNDING_SIGMA, rel=1e-9)
    kernel = detector._build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    peak = float(ndimage.convolve(injected - baseline, kernel, mode="nearest").max())
    for call, sigma in zip(("baseline", "injected"), detector_sigmas):
        assert 0.85 * max(snrs) <= peak / sigma <= max(snrs) * (1.0 + 1e-9), (label, call, peak / sigma)


@pytest.mark.parametrize("through_a_variant", [False, True])
def test_injection_recovery_on_the_pairs_frame_reports_no_faint_star_and_nothing_spurious(through_a_variant: bool) -> None:
    # At the pairs frame's rounding noise an SNR 3 star stays under the 3.5 threshold and the frame has no sources,
    # so nothing spurious appears and the SNR 3 bin recovers none, whether candidates come from the frame itself or
    # from a variant checked on it. Measured against a noise that lost the frame's floor, the stars read thousands of
    # times brighter and the pairs become sources
    frame = _pairs_frame()
    report = detector.evaluate_injection_recovery(
        frame, variant_image=frame if through_a_variant else None, injected_snrs=(3.0, 18.0), n_per_snr=6
    )
    faint, bright = report.bins
    assert faint.snr_min <= 3.0 <= faint.snr_max and faint.injected_count > 0 and faint.recovered_count == 0, faint
    assert bright.snr_min <= 18.0 <= bright.snr_max and bright.recovered_count == bright.injected_count > 0, bright
    assert report.spurious_count == 0, report


def test_forced_photometry_keeps_a_rounding_floor_it_is_handed() -> None:
    # The bump frame of the forced photometry test, moved off its grid by a ten-thousandth of a level: on its own it
    # loses the rounding floor and every bump passes at threshold 6 against the 0.001 floor; handed the frame's
    # rounding noise, as injection recovery does, none does (the bump's peak is 3.46 of it)
    frame = np.ones((HEIGHT, WIDTH), dtype=np.float64)
    spots = [(x, y) for x in range(20, WIDTH - 20, 30) for y in range(20, HEIGHT - 20, 30)]
    for x, y in spots:
        frame[y, x - 1 : x + 2] = 2.0
        frame[y - 1 : y + 2, x] = 2.0
    frame += 1e-4 * np.random.default_rng(3).random(frame.shape)
    assert detector._value_step(frame) is None
    candidates = [(float(x), float(y)) for x, y in spots]
    assert len(detector.verify_candidates_on_original_pixels(frame, candidates, snr_threshold=6.0)) == len(spots)
    kept = detector.verify_candidates_on_original_pixels(frame, candidates, snr_threshold=6.0, noise_floor=ROUNDING_SIGMA)
    assert kept == []


def test_injection_recovery_hands_the_frames_rounding_noise_to_every_call_on_an_injected_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Through a variant, candidates come from the injected variant and are checked on the injected original: both
    # calls get the base frame's rounding noise, the baseline call (on the frame itself) none
    frame = _pairs_frame()
    floors: list[tuple[str, float]] = []
    real_detect, real_verify = detector.detect_point_sources, detector.verify_candidates_on_original_pixels

    def detect(image, **kwargs):
        floors.append(("detect", float(kwargs.get("noise_floor", 0.0))))
        return real_detect(image, **kwargs)

    def verify(image, candidates, **kwargs):
        floors.append(("verify", float(kwargs.get("noise_floor", 0.0))))
        return real_verify(image, candidates, **kwargs)

    monkeypatch.setattr(detector, "detect_point_sources", detect)
    monkeypatch.setattr(detector, "verify_candidates_on_original_pixels", verify)
    detector.evaluate_injection_recovery(frame, variant_image=frame, injected_snrs=(6.0,), n_per_snr=2)
    assert floors[0] == ("detect", 0.0)
    assert [call for call, _ in floors[1:]] == ["detect", "verify"]
    assert all(floor == pytest.approx(ROUNDING_SIGMA, rel=1e-9) for _, floor in floors[1:]), floors


def test_the_interquartile_estimate_is_kept_where_it_is_the_larger() -> None:
    # A sky at 1.3 with a read noise of 0.5: the MAD is 0, the interquartile range a whole level (1 / 1.349 = 0.741)
    # and the spread of the kept residuals 0.59, so the noise is the old estimate exactly, not a larger one
    frame = _dark_frame(background=1.3, read_noise=0.5).astype(np.float64)
    _, sigma = detector._estimate_background_and_noise(frame)
    parent = _parent_noise(frame - _parent_background(frame))
    assert parent == pytest.approx(1.0 / 1.349, rel=1e-9)
    assert sigma == pytest.approx(parent, rel=1e-12)


def test_the_measured_step_and_floor() -> None:
    frame = _dark_frame()
    assert detector._value_step(frame) == pytest.approx(1.0)
    assert detector._value_step(frame.astype(np.uint16) * 257) == pytest.approx(257.0)
    assert detector._value_step(frame.astype(np.float64) / 255.0) == pytest.approx(1.0 / 255.0, rel=1e-9)
    rng = np.random.default_rng(0)
    assert detector._value_step(rng.normal(size=(50, 50))) is None  # continuous values sit on no grid
    # Limited-range codes widened to full-range 16 bits land 299 or 300 apart: still a grid
    assert detector._value_step(np.rint(np.arange(20) * 65535.0 / 219.0)) == pytest.approx(299.0)
    assert detector._quantization_sigma(frame) == pytest.approx(1.0 / np.sqrt(12.0))
    # A grey RGB frame's luma is its channel, so it keeps the unweighted rounding noise
    assert detector._quantization_sigma(np.repeat(frame[:, :, None], 3, axis=2)) == pytest.approx(1.0 / np.sqrt(12.0))
    colour = np.stack([frame, np.clip(frame.astype(int) + 1, 0, 255).astype(np.uint8), frame], axis=2)
    weighted = np.sqrt(0.2126**2 + 0.7152**2 + 0.0722**2) / np.sqrt(12.0)
    assert detector._quantization_sigma(colour) == pytest.approx(weighted)


@pytest.mark.parametrize("frames", [3, 5])
def test_a_float_composite_sits_on_no_grid(frames: int) -> None:
    # Aligned by sub-pixel shifts and averaged, a composite's values sit on no grid: the smallest gap between them is
    # float rounding (about 1e-16 of the largest value), not a step of the data
    composite = _stack(frames)
    assert detector._value_step(composite) is None
    assert detector._value_step(composite / 255.0) is None


def test_a_luma_that_mixes_a_green_and_a_blue_step_sits_on_no_grid() -> None:
    # The Rec. 709 luma of red 1, green 1 or 2 and blue 0, 1 or 2: one blue step (0.0722) apart within each green level,
    # and 7.906 blue steps from one green level to the next, 0.094 off a whole number of steps
    green, blue = np.meshgrid([1, 2], [0, 1, 2])
    luma = LUMA_WEIGHTS[0] + LUMA_WEIGHTS[1] * green + LUMA_WEIGHTS[2] * blue
    assert detector._value_step(luma) is None


def test_a_frame_whose_red_equals_its_green_is_not_grey() -> None:
    # Red and green round together and blue on its own: the luma's rounding noise is
    # sqrt((0.2126 + 0.7152)^2 + 0.0722^2) / sqrt(12) = 0.931 / sqrt(12). The grey rule's 1 / sqrt(12) would sit above
    # it, and a floor above the noise hides faint stars
    frame, other = _dark_frame(), _dark_frame(noise_seed=7)
    rgb = np.stack([frame, frame, other], axis=2)
    exact = np.sqrt((LUMA_WEIGHTS[0] + LUMA_WEIGHTS[1]) ** 2 + LUMA_WEIGHTS[2] ** 2) * ROUNDING_SIGMA
    assert detector._quantization_sigma(rgb) <= exact


def test_a_non_finite_pixel_does_not_hide_the_grid() -> None:
    # A hot pixel stored as inf, or a masked one as NaN, is no value on the grid: the rest of the frame still steps 1
    frame = _dark_frame().astype(np.float64)
    for bad in (np.inf, np.nan):
        marked = frame.copy()
        marked[10, 10] = bad
        assert detector._value_step(marked) == pytest.approx(1.0), bad


def test_one_level_pairs_on_a_flat_frame_are_not_sources() -> None:
    # Under 1 % of the pixels sit one level up, in pairs: the residuals' spread is exactly 0, so only the rounding floor
    # keeps a pair (matched SNR about 3 at the floor) from an enormous SNR. A pair is no site: it has one raised
    # neighbour, not four, and its one level is 3.5, not 14, rounding sigmas. With the old 0.001 floor this frame gave
    # 549 sources [tested]
    frame = _pairs_frame()
    _, sigma = detector._estimate_background_and_noise(frame.astype(np.float64))
    assert sigma < 1e-9  # zero up to float rounding
    sites = int(_sites(frame, ROUNDING_SIGMA, impulses=True).sum())
    assert len(detect_point_sources(frame)) <= sites
    # Forced photometry at the pairs, as a denoised variant might propose them
    rng = np.random.default_rng(12)
    pairs = [(int(x), int(y)) for x, y in zip(rng.integers(10, WIDTH - 10, 300), rng.integers(10, HEIGHT - 10, 300))]
    kept = detector.verify_candidates_on_original_pixels(frame, [(x + 0.5, float(y)) for x, y in pairs])
    assert len(kept) <= int(_sites(frame, ROUNDING_SIGMA, impulses=False).sum()), len(kept)


def test_a_saturated_light_does_not_hide_the_grid_of_a_flat_frame() -> None:
    # The pairs frame with one clipped 5x5 light at 255: the values still step 1, 1/255 of the largest, which a float
    # rounding step (1e-16 of the largest value) never reaches. If the light hid the grid, the noise would fall to the
    # 0.001 floor and every pair would be a source (549 of them [tested])
    frame = _pairs_frame()
    frame[100:105, 200:205] = 255
    assert detector._value_step(frame) == pytest.approx(1.0)
    # The light's 25 pixels are the frame's only sites: at most that many detections
    sites = int(_sites(frame, ROUNDING_SIGMA, impulses=True).sum())
    assert len(detect_point_sources(frame)) <= sites, sites


def test_an_average_of_frames_keeps_its_lower_noise() -> None:
    # The same stars under eight independent noise draws, averaged as a stack is
    stack = np.mean([_dark_frame(noise_seed=6 + k).astype(np.float64) for k in range(8)], axis=0)
    assert detector._quantization_sigma(stack) <= (1.0 / 8.0) / np.sqrt(12.0) + 1e-12
    dets = detect_point_sources(stack)
    recovered, others = _score(dets, _stars())
    assert recovered >= STARS - 3, (recovered, others)
    # The detector divides by at least the stack's measured noise, so a false source needs a site at that noise
    _, sigma = detector._estimate_background_and_noise(stack)
    assert _far(dets) <= _far_sites(stack, sigma), (_far(dets), _far_sites(stack, sigma))


def _clip(path: Path, frames: list[np.ndarray]) -> Path:
    raw = b"".join(f.tobytes() for f in frames)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{WIDTH}x{HEIGHT}", "-r", "1",
                    "-i", "-", "-frames:v", str(len(frames)), "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(path)],
                   input=raw, check=True, capture_output=True)
    return path


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
def test_both_clip_decode_paths_feed_the_detector_a_frame_it_reads_the_same_way(tmp_path: Path) -> None:
    from services.ingest import jobs as ingest_jobs
    from services.media import discovery

    frame = _dark_frame()
    clip = _clip(tmp_path / "dark.mp4", [frame, frame, frame])
    truth = _stars()

    # The upload job's decode: PGM frames, 8-bit values as float64
    frames, _ = ingest_jobs._decode_luma_frames(clip, "video", WIDTH, HEIGHT, lambda done, total: None)
    assert detector._value_step(frames[0]) == pytest.approx(1.0)
    dets = detect_point_sources(frames[0])
    recovered, others = _score(dets, truth)
    assert recovered >= STARS - 3, ("job", recovered, others)
    assert _far(dets) <= _far_sites(frames[0], ROUNDING_SIGMA), ("job", _far(dets))

    # Discovery's decode: the picked frame at its native size in 16-bit gray, as 0..1
    luma, _trajectory, _time = discovery._extract_representative_luma(clip, WIDTH, HEIGHT)
    assert float(luma.max()) <= 1.0
    # ffmpeg widens the clip's limited-range luma (16 to 235) to full-range 16 bits, so the codes land 299 or 300
    # sixteen-bit levels apart: a grid of about 299/65535, not 1/255
    step = detector._value_step(luma)
    assert step is not None and step == pytest.approx(299.0 / 65535.0, rel=0.01), step
    dets = detect_point_sources(luma)
    recovered, others = _score(dets, truth)
    assert recovered >= STARS - 3, ("discovery", recovered, others)
    assert _far(dets) <= _far_sites(luma, step / np.sqrt(12.0)), ("discovery", _far(dets))
