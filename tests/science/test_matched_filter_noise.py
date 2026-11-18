"""The detector's signal-to-noise value is its matched response over the noise of that response, measured tile by tile
(`matched_filter_noise`, realsky-3), not over the pixel noise.

Covers:
- The kernel: unit L2 norm, so on white noise its response has the pixel noise's own spread; the background the
  detector subtracts has an error of its own that the kernel's sum (4.03) passes, so on white noise the map is 1.17
  times the pixel noise it replaces, and never more than those two terms added as if independent.
- Correlated noise (white noise smoothed, as a developed photo's is): the map is the measured spread of the response,
  2, 3 and 3.7 times the pixel noise, and false detections on pure noise stay at the white-noise rate.
- A frame whose noise changes across it is followed; stars up to a stated density raise the map by no more than a
  stated share; a flat, a quantised and a small frame give no detection and no error; a bright star is found at every
  phase of the tile grid; forced photometry reports the same value as detection.
- The tile follows the frame: 256 px on a 4032 x 3024 frame, the same share of the diagonal on others, at least 64 px.
- Properties under 20 seeds: scaling the frame scales the map and keeps every detection, adding a constant changes
  nothing, and the map is finite and positive everywhere.
- The map is never under the parent's divider (the frame's pixel noise), not by its last bit: a tile it cannot
  measure (clipped flats, ground) takes that divider, so stars on a sky crushed to black are found as the parent found
  them, and dark 8-bit skies near the black clip give no more false detections than the parent.
- The rules a mutant could break unseen: held at the outermost tile centre, the rounded tile count, the overlap, the
  unsampled edge, and the 4.75 gate in detection and in forced photometry.
- Two corpus photos (local_dataset): the map sees their correlated noise.
All other frames here are simulated.
"""

from __future__ import annotations

import math
from pathlib import Path

from hypothesis import example, given, settings, strategies as st
import numpy as np
from PIL import Image
import pytest
from scipy import ndimage

import services.science as science
from services.science import detector
from services.science.detector import detect_point_sources, matched_filter_noise

KERNEL = detector._build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
ROUNDING_SIGMA = 1.0 / math.sqrt(12.0)
# White noise smoothed by a Gaussian of these widths (px) and renormalised to unit pixel spread: the detector's own
# ratio of its response's spread to its pixel noise is then 2.0, 3.0 and 3.7 (found by bisection on a 1024 x 1024
# frame, seed 1, in the unit's lane notes). They shape the test noise only; no detector number comes from them
CORRELATION_SIGMA_PX = {2.0: 0.651, 3.0: 1.283, 3.7: 2.917}
REPO = Path(__file__).resolve().parents[2]
PHOTOS = {pid: f".cache/real-photos/{pid}/{pid}.jpg" for pid in ("rp-b-01", "rp-b-19")}


def _white(shape: tuple[int, int], seed: int, sigma: float = 1.0, level: float = 100.0) -> np.ndarray:
    return level + np.random.default_rng(seed).normal(0.0, sigma, shape)


def _correlated(shape: tuple[int, int], sigma_c: float, seed: int, level: float = 100.0) -> np.ndarray:
    noise = ndimage.gaussian_filter(np.random.default_rng(seed).normal(0.0, 1.0, shape), sigma_c, mode="wrap")
    return level + noise / float(np.std(noise))


def _mad_sigma(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64).ravel()
    return 1.4826 * float(np.median(np.abs(values - np.median(values))))


def _pixel_sigma(frame: np.ndarray) -> float:
    """The pixel noise the detector divided its matched response by before this unit, and still divides its pixel
    shape gates by."""
    _, sigma_bg = detector._estimate_background_and_noise(np.asarray(frame, dtype=np.float64))
    return detector._effective_sigma(sigma_bg, frame)


def _response(frame: np.ndarray) -> np.ndarray:
    """The detector's matched response: its kernel on the frame less its own background."""
    luma = np.asarray(frame, dtype=np.float64)
    bg, _ = detector._estimate_background_and_noise(luma)
    return ndimage.convolve(luma - bg, KERNEL, mode="nearest")


def _add_star(frame: np.ndarray, x: float, y: float, amplitude: float, sigma_px: float = 1.15) -> None:
    y0, x0 = int(round(y)), int(round(x))
    ys, xs = slice(max(0, y0 - 7), y0 + 8), slice(max(0, x0 - 7), x0 + 8)
    yy, xx = np.mgrid[ys, xs]
    frame[ys, xs] += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * sigma_px * sigma_px))


def _amplitude_for(snr: float, noise: float) -> float:
    """The peak of a 1.15 px Gaussian star whose matched response is `snr` noises on a pixel centre."""
    grid = np.arange(-3, 4, dtype=np.float64)
    gx, gy = np.meshgrid(grid, grid)
    return snr * noise / float(np.sum(np.exp(-(gx * gx + gy * gy) / (2.0 * 1.15**2)) * KERNEL))


def test_the_function_is_public_and_exported() -> None:
    assert science.matched_filter_noise is detector.matched_filter_noise
    assert "matched_filter_noise" in science.__all__


def test_the_kernels_noise_gain_on_white_noise_is_its_l2_norm() -> None:
    # Unit L2 norm: white noise of spread s gives a response of spread s; the sum, 4.03, is its gain on a constant
    assert float(np.sqrt(np.sum(KERNEL * KERNEL))) == pytest.approx(1.0, abs=1e-12)
    assert float(np.sum(KERNEL)) == pytest.approx(4.032, abs=1e-3)
    # The response of white noise with no background subtracted: the map is the kernel's gain times the noise. A MAD
    # over 0.8 million samples scatters by about 0.2 %, so 1.5 % is a wide margin
    noise = np.random.default_rng(3).normal(0.0, 2.5, (768, 1024))
    nmap = detector._matched_noise_map(ndimage.convolve(noise, KERNEL, mode="nearest"), None, floor=0.0)
    assert float(np.median(nmap)) == pytest.approx(2.5, rel=0.015)


@pytest.mark.parametrize("shape", [(360, 480), (540, 960), (1512, 2016)])
def test_on_white_noise_the_map_is_the_pixel_noise_plus_the_backgrounds_own_error(shape: tuple[int, int]) -> None:
    # The background is a median of 49 samples, wrong by about 0.17 of the pixel noise, and the kernel's sum of 4.03
    # passes that error into the response. Added as if independent the two give sqrt(1 + (4.03 x 0.17)^2) = 1.21; the
    # median shares pixels with the kernel, so the measured map is a little under that. On ideal white noise the new
    # value is therefore the old one divided by about 1.17 (1.155 to 1.183 over five seeds at 480 x 360 to
    # 2016 x 1512 in the lane's measurement), not equal to it
    frame = _white(shape, seed=8)
    bg, _ = detector._estimate_background_and_noise(frame)
    bg_error = float(np.std((bg - 100.0)[8:-8, 8:-8]))
    independent = math.sqrt(1.0 + (float(np.sum(KERNEL)) * bg_error) ** 2)
    nmap = float(np.median(matched_filter_noise(frame)))
    # The frame's noise is 1 by construction
    assert nmap <= independent, (nmap, independent)
    ratio = nmap / _pixel_sigma(frame)
    assert 1.12 <= ratio <= 1.22, ratio


@pytest.mark.parametrize("target", sorted(CORRELATION_SIGMA_PX))
def test_on_correlated_noise_the_map_is_the_measured_spread_of_the_response(target: float) -> None:
    frame = _correlated((768, 1024), CORRELATION_SIGMA_PX[target], seed=21)
    nmap = matched_filter_noise(frame)
    response = _response(frame)[4:-4, 4:-4]
    # The map is the response's own spread, tile by tile, and so its spread over the frame
    assert float(np.median(nmap)) == pytest.approx(_mad_sigma(response), rel=0.03)
    assert float(np.median(nmap)) == pytest.approx(float(np.std(response)), rel=0.04)
    # And it is `target` times the pixel noise the old value divided by: the old value overstated by that much
    assert float(np.median(nmap)) / _pixel_sigma(frame) == pytest.approx(target, rel=0.06)


@pytest.mark.parametrize("target", [None, *sorted(CORRELATION_SIGMA_PX)])
def test_false_detections_on_pure_noise_stay_near_the_white_noise_rate(target: float | None) -> None:
    # At the benchmark's frame size (3.05 megapixels) and the 4.75 gate every caller reaches, the lane measured 0.20
    # (white) to 1.05 (correlated) false detections per megapixel over five seeds: about 3 per frame at most. The old
    # divider gave 1.2 per megapixel on white noise and 790 to 2,350 on correlated noise. A count of 12 or more has a
    # Poisson chance under 1e-4 at a mean of 3.2
    shape = (1512, 2016)
    for seed in (31, 32):
        frame = _white(shape, seed) if target is None else _correlated(shape, CORRELATION_SIGMA_PX[target], seed)
        dets = detect_point_sources(frame, snr_threshold=3.5)
        assert len(dets) < 12, (target, seed, len(dets))


@pytest.mark.parametrize("along", ["x", "y"])
def test_a_frame_whose_noise_changes_across_it_is_followed(along: str) -> None:
    # White noise whose spread rises from 1 to 4 across the frame, along x and (the frame turned) along y. At each
    # column (row) the map is the response's own spread there, measured in a strip as wide as a tile, within 6 %, or
    # the parent's divider for the frame where that is larger: the quieter part is scored as the parent scored it
    h, w = 512, 1536
    ramp = 1.0 + 3.0 * np.arange(w) / (w - 1)
    frame = 100.0 + np.random.default_rng(5).normal(0.0, 1.0, (h, w)) * ramp[None, :]
    if along == "y":
        frame = np.ascontiguousarray(frame.T)
    nmap = matched_filter_noise(frame)
    response = _response(frame)
    if along == "y":
        nmap, response = nmap.T, response.T
    tile = detector._noise_tile_px(h, w)
    parent = _parent_divider(frame)
    raised = 0
    for x in range(2 * tile, w - 2 * tile, tile // 2):
        strip = response[8:-8, x - tile // 2 : x + tile // 2]
        expected = max(_mad_sigma(strip), parent)
        raised += _mad_sigma(strip) > 1.1 * parent
        assert float(np.median(nmap[8:-8, x])) == pytest.approx(expected, rel=0.06), (along, x)
    assert raised >= 5  # the noisier part is followed, not held at the parent's divider
    if along == "y":
        return
    # A step in the noise, 1 to 3: the noisier side is followed two tiles from the step, and the quieter side is held
    # at the parent's divider for the frame
    spread = np.where(np.arange(w) < w // 2, 1.0, 3.0)[None, :]
    step = 100.0 + np.random.default_rng(6).normal(0.0, 1.0, (h, w)) * spread
    smap = matched_filter_noise(step)
    left, right = float(np.median(smap[:, : w // 2 - 2 * tile])), float(np.median(smap[:, w // 2 + 2 * tile :]))
    assert left == pytest.approx(_parent_divider(step), rel=1e-9), left
    assert right == pytest.approx(3.0 * float(np.median(matched_filter_noise(_white((h, w), seed=6)))), rel=0.06), right


def test_the_sky_mask_keeps_the_ground_out_of_the_map() -> None:
    # Sky (spread 1) above, ground (spread 6) below, the ground masked out: tiles that straddle the horizon measure the
    # sky alone, and the ground's tiles, which have no sky pixel, take the parent's divider, the sky's pixel noise
    h, w = 512, 768
    spread = np.where(np.arange(h) < h // 2, 1.0, 6.0)[:, None]
    frame = 100.0 + np.random.default_rng(29).normal(0.0, 1.0, (h, w)) * spread
    sky = np.zeros((h, w), dtype=bool)
    sky[: h // 2, :] = True
    nmap = matched_filter_noise(frame, sky_mask=sky)
    own = float(np.median(matched_filter_noise(frame[: h // 2 - 40, :])))
    assert float(np.max(nmap[h // 2 - 40 : h // 2, :])) <= 1.1 * own
    assert float(np.max(nmap[h // 2 :, :])) <= 1.1 * own


def test_a_dark_sky_at_the_lowest_level_is_still_sampled() -> None:
    # An 8-bit sky so dark that 84 % of it rounds to 0, the frame's lowest value: it is sky, not a clip, because only
    # whole 3x3 neighbourhoods at the lowest value are clipped flats (0.84^9, about a fifth of its pixels, against 84 %
    # if a single pixel at the lowest value counted)
    frame = np.clip(np.rint(0.1 + np.random.default_rng(31).normal(0.0, 0.4, (400, 600))), 0, 255).astype(np.uint8)
    share = float(np.mean(detector._clipped_flats(frame.astype(np.float64))))
    assert 0.1 <= share <= 0.35, share
    assert float(np.median(matched_filter_noise(frame))) > ROUNDING_SIGMA


@pytest.mark.parametrize(("per_megapixel", "most_rise"), [(100, 0.02), (500, 0.06)])
def test_stars_raise_the_map_by_no_more_than_a_stated_share(per_megapixel: int, most_rise: float) -> None:
    # Stars of matched SNR 5 to 500 (log uniform) at 100 and 500 per megapixel; a 48 degree corpus photo (rp-b-01)
    # holds about 180 catalog stars to V 8 per megapixel. The MAD ignores a sample far out at no cost and rises by
    # about 1.1 times the share of samples it loses to the stars: a star raises the response above one noise over
    # about 30 (SNR 5) to 100 (SNR 500) pixels, so about 0.6 % and 3 % of the frame, and rises of at most 2 % and 6 %
    shape = (1024, 1024)
    base = _white(shape, seed=9)
    clean = float(np.median(matched_filter_noise(base)))
    rng = np.random.default_rng(10)
    starry = base.copy()
    n = int(round(per_megapixel * shape[0] * shape[1] / 1e6))
    xs, ys = rng.uniform(8, shape[1] - 8, n), rng.uniform(8, shape[0] - 8, n)
    for x, y, snr in zip(xs, ys, np.exp(rng.uniform(np.log(5), np.log(500), n))):
        _add_star(starry, float(x), float(y), _amplitude_for(float(snr), clean))
    rise = float(np.median(matched_filter_noise(starry))) / clean - 1.0
    assert 0.0 <= rise <= most_rise, (per_megapixel, rise)


@pytest.mark.parametrize(
    ("label", "frame", "floor"),
    [
        # A flat float frame sits on no grid: the floor is the old absolute floor, 0.001
        ("flat float", np.full((300, 400), 7.0), 1e-3),
        # A flat 8-bit frame holds one value, so no step can be measured on it: the same floor
        ("flat 8-bit", np.ones((300, 400), dtype=np.uint8), 1e-3),
        # Quantised: under 0.5 % of the pixels a level up, alone, so the step is 1 and over half the response is
        # exactly 0: the floor is the rounding noise
        ("quantised 8-bit", None, ROUNDING_SIGMA),
    ],
)
def test_flat_and_quantised_frames_give_no_detection_and_no_error(
    label: str, frame: np.ndarray | None, floor: float
) -> None:
    if frame is None:
        frame = np.ones((300, 400), dtype=np.uint8)
        rng = np.random.default_rng(14)
        frame[rng.integers(5, 295, 500), rng.integers(5, 395, 500)] = 2
    nmap = matched_filter_noise(frame)
    assert nmap.shape == frame.shape and bool(np.all(np.isfinite(nmap))), label
    assert float(np.min(nmap)) == pytest.approx(floor, rel=1e-9), label
    if label != "quantised 8-bit":
        assert float(np.max(nmap)) == pytest.approx(floor, rel=1e-9), label
    assert detect_point_sources(frame) == [], label
    assert detector.verify_candidates_on_original_pixels(frame, [(100.0, 100.0), (200.5, 150.5)]) == [], label


def _parent_divider(frame: np.ndarray) -> float:
    """The pixel noise the parent detector divided everything by on this frame."""
    _, sigma_bg = detector._estimate_background_and_noise(np.asarray(frame, dtype=np.float64))
    return detector._effective_sigma(sigma_bg, frame)


def test_a_lamp_on_clipped_ground_is_scored_as_the_parent_scored_it() -> None:
    # A night photo's ground often clips to black: there the data holds no noise, the response's deviation is 0, and
    # a map that sampled it would sit at its floor, so a lamp there would outscore every star (on a corpus phone photo,
    # rp-b-06, 44 of the ranked 65 were such lights at about 1,400 noises while its stars sat near 400). Clipped flats
    # are not sampled, and a tile they fill takes the parent's divider for the frame: the lamp scores what it scored
    # under the parent, no more
    h, w = 512, 768
    frame = 100.0 + np.random.default_rng(19).normal(0.0, 2.0, (h, w))
    frame[h // 2 :, :] = float(np.min(frame[: h // 2, :])) - 50.0  # the ground, clipped below the sky's lowest value
    sky_noise = float(np.median(matched_filter_noise(frame)[: h // 4, :]))
    lamp_x, lamp_y = w / 2 + 0.2, 3 * h / 4 - 0.3
    _add_star(frame, lamp_x, lamp_y, _amplitude_for(40.0, sky_noise))
    nmap = matched_filter_noise(frame)
    tile = detector._noise_tile_px(h, w)
    parent = _parent_divider(frame)
    assert np.allclose(nmap[h // 2 + tile :, :], parent, rtol=1e-9, atol=0.0)
    lamp = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - lamp_x, d.y_px - lamp_y) < 1.0]
    assert len(lamp) == 1 and lamp[0].snr == pytest.approx(40.0 * sky_noise / parent, rel=0.15), [d.snr for d in lamp]
    assert bool(np.all(detector._clipped_flats(frame)[h // 2 + 2 : h // 2 + 40, :]))
    assert not bool(np.any(detector._clipped_flats(frame)[: h // 2 - 2, :]))


def test_a_saturated_plateau_is_no_sample_of_the_noise_either() -> None:
    # A disk clipped at the data's top (a Moon, a lamp's core) holds no noise either: its tiles take the parent's divider
    h, w = 512, 768
    frame = 100.0 + np.random.default_rng(23).normal(0.0, 2.0, (h, w))
    top = float(np.max(frame)) + 50.0
    yy, xx = np.mgrid[0:h, 0:w]
    disk = np.hypot(xx - w / 2, yy - h / 2) < 150
    frame[disk] = top
    sky_noise = float(np.median(matched_filter_noise(frame)[:40, :]))
    flats = detector._clipped_flats(frame)
    assert bool(np.all(flats[np.hypot(xx - w / 2, yy - h / 2) < 148])) and not bool(np.any(flats[:40, :]))
    nmap = matched_filter_noise(frame)
    # At the disk's centre the tiles are all unmeasured, so the map is the parent's divider; farther out it is
    # interpolated toward the measured tiles at the disk's edge, whose spread is higher, and never falls below it
    parent = _parent_divider(frame)
    assert np.allclose(nmap[np.hypot(xx - w / 2, yy - h / 2) < 10], parent, rtol=1e-9, atol=0.0)
    assert float(np.min(nmap[np.hypot(xx - w / 2, yy - h / 2) < 60])) >= parent * (1.0 - 1e-9)
    assert sky_noise > _parent_divider(frame)  # the sky's own spread is measured where it can be


@pytest.mark.parametrize("shape", [(8, 8), (12, 12), (40, 52), (63, 63)])
def test_a_frame_smaller_than_a_tile_gives_one_value_and_no_error(shape: tuple[int, int]) -> None:
    frame = _white(shape, seed=15)
    nmap = matched_filter_noise(frame)
    assert nmap.shape == shape and bool(np.all(np.isfinite(nmap))) and float(np.min(nmap)) > 0.0
    # One tile: one value over the whole frame
    assert float(np.max(nmap)) == pytest.approx(float(np.min(nmap)), rel=1e-12)
    assert detect_point_sources(frame) == []
    if min(shape) >= 40:
        # A bright star in it is still found
        starry = frame.copy()
        _add_star(starry, shape[1] / 2 + 0.3, shape[0] / 2 - 0.2, _amplitude_for(40.0, float(nmap[0, 0])))
        dets = detect_point_sources(starry)
        assert len(dets) == 1
        assert math.hypot(dets[0].x_px - (shape[1] / 2 + 0.3), dets[0].y_px - (shape[0] / 2 - 0.2)) < 0.5


def test_a_star_well_above_the_threshold_is_found_at_every_tile_phase() -> None:
    # One star of matched SNR 12 at a time, swept over two tile periods in x and in y in steps of 5.3 px, so it sits at
    # every phase of the grid of tile centres the map is interpolated between, and at sub-pixel offsets. It is found
    # once, and its own part of the value (its matched response without the frame's noise, over the map there) is 12
    # less what its sub-pixel offset costs, at most 9 % (half a pixel off in x and y). The reported value adds the
    # frame's own response at the peak, about one noise either way, so it is not the quantity bounded here
    h, w = 640, 960
    frame = _white((h, w), seed=16)
    nmap = matched_filter_noise(frame)
    tile = detector._noise_tile_px(h, w)
    for k in range(int(2 * tile / 5.3)):
        for x, y in ((w / 2 - tile + 5.3 * k, h / 2 + 0.37), (w / 2 + 0.41, h / 2 - tile + 5.3 * k)):
            starry = frame.copy()
            _add_star(starry, x, y, _amplitude_for(12.0, float(nmap[int(round(y)), int(round(x))])))
            near = [d for d in detect_point_sources(starry) if math.hypot(d.x_px - x, d.y_px - y) < 1.0]
            assert len(near) == 1, (x, y, [d.snr for d in near])
            own = ndimage.convolve(starry - frame, KERNEL, mode="nearest")
            peak_y, peak_x = np.unravel_index(int(np.argmax(own)), own.shape)
            ratio = float(own[peak_y, peak_x]) / float(matched_filter_noise(starry)[peak_y, peak_x])
            assert 0.9 * 12.0 <= ratio <= 1.01 * 12.0, (x, y, ratio)


def test_forced_photometry_reports_the_value_detection_reports() -> None:
    frame = _correlated((480, 640), CORRELATION_SIGMA_PX[3.0], seed=17)
    noise = float(np.median(matched_filter_noise(frame)))
    rng = np.random.default_rng(18)
    for x, y in zip(rng.uniform(20, 620, 12), rng.uniform(20, 460, 12)):
        _add_star(frame, float(x), float(y), _amplitude_for(15.0, noise))
    dets = detect_point_sources(frame)
    assert len(dets) >= 10
    forced = detector.verify_candidates_on_original_pixels(frame, dets)
    by_place = {(round(d.x_px, 6), round(d.y_px, 6)): d.snr for d in forced}
    for d in dets:
        if (round(d.x_px, 6), round(d.y_px, 6)) in by_place:
            assert by_place[(round(d.x_px, 6), round(d.y_px, 6))] == pytest.approx(d.snr, rel=1e-12)
    assert len(by_place) >= 10


@pytest.mark.parametrize(
    ("shape", "tile"),
    [
        ((3024, 4032), 256), ((4032, 3024), 256), ((1512, 2016), 128),
        ((6144, 8160), 519), ((480, 640), 64), ((40, 40), 64),
    ],
)
def test_the_tile_follows_the_frame(shape: tuple[int, int], tile: int) -> None:
    # 256 px, the restoration spec's tile, on a 4032 x 3024 frame (diagonal 5040 px, the corpus's commonest size),
    # the same share of the diagonal on any other frame, and never under 64 px
    assert detector._noise_tile_px(*shape) == tile


def _property_frame(seed: int, sigma: float, level: float) -> np.ndarray:
    frame = _white((160, 200), seed, sigma=sigma, level=level)
    rng = np.random.default_rng(seed + 1)
    for x, y in zip(rng.uniform(12, 188, 5), rng.uniform(12, 148, 5)):
        _add_star(frame, float(x), float(y), 30.0 * sigma)
    return frame


def _same_detections(a, b, *, rel: float) -> None:
    assert len(a) == len(b), (len(a), len(b))
    for da, db in zip(a, b):
        assert abs(da.x_px - db.x_px) <= 1e-6 and abs(da.y_px - db.y_px) <= 1e-6
        assert da.snr == pytest.approx(db.snr, rel=rel)


@settings(max_examples=25, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=100_000),
    sigma=st.floats(min_value=0.5, max_value=5.0),
    level=st.floats(min_value=10.0, max_value=1000.0),
    scale=st.floats(min_value=0.1, max_value=1000.0),
)
@example(seed=1, sigma=1.0, level=100.0, scale=0.1)
@example(seed=2, sigma=5.0, level=10.0, scale=1000.0)
def test_pbt_scaling_the_frame_scales_the_map_and_keeps_every_detection(
    seed: int, sigma: float, level: float, scale: float
) -> None:
    # The response's spread here is at least 0.5 x 1.1 x 0.1 = 0.055, far above the 0.001 floor
    frame = _property_frame(seed, sigma, level)
    assert np.allclose(matched_filter_noise(frame * scale), scale * matched_filter_noise(frame), rtol=1e-9, atol=0.0)
    _same_detections(detect_point_sources(frame), detect_point_sources(frame * scale), rel=1e-9)


@settings(max_examples=25, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=100_000),
    sigma=st.floats(min_value=0.5, max_value=5.0),
    level=st.floats(min_value=10.0, max_value=1000.0),
    offset=st.floats(min_value=-500.0, max_value=10_000.0),
)
@example(seed=3, sigma=0.5, level=1000.0, offset=10_000.0)
@example(seed=4, sigma=2.0, level=10.0, offset=-500.0)
def test_pbt_adding_a_constant_changes_nothing(seed: int, sigma: float, level: float, offset: float) -> None:
    frame = _property_frame(seed, sigma, level)
    # Float rounding at the level of the constant, about 1e-12 of it, is all that may differ
    assert np.allclose(matched_filter_noise(frame + offset), matched_filter_noise(frame), rtol=1e-8, atol=0.0)
    _same_detections(detect_point_sources(frame), detect_point_sources(frame + offset), rel=1e-8)


@settings(max_examples=40, deadline=None)
@given(
    h=st.integers(min_value=1, max_value=300),
    w=st.integers(min_value=1, max_value=300),
    sigma=st.sampled_from([0.0, 0.2, 1.0, 30.0]),
    rounded=st.booleans(),
    sky=st.sampled_from(["none", "all", "rectangle", "sliver", "empty"]),
    seed=st.integers(min_value=0, max_value=100_000),
)
@example(h=1, w=1, sigma=0.0, rounded=False, sky="none", seed=0)
@example(h=300, w=300, sigma=0.2, rounded=True, sky="sliver", seed=1)
@example(h=200, w=300, sigma=1.0, rounded=False, sky="empty", seed=2)
def test_pbt_the_map_is_finite_and_positive_wherever_the_frame_has_sky(
    h: int, w: int, sigma: float, rounded: bool, sky: str, seed: int
) -> None:
    frame = _white((h, w), seed, sigma=sigma, level=20.0)
    if rounded:
        frame = np.clip(np.rint(frame), 0, 255).astype(np.uint8)
    mask = None
    if sky != "none":
        mask = np.zeros((h, w), dtype=bool)
        if sky == "all":
            mask[:] = True
        elif sky == "rectangle":
            mask[: max(1, h // 2), : max(1, (2 * w) // 3)] = True
        elif sky == "sliver":
            mask[:, : max(1, w // 40)] = True
    nmap = matched_filter_noise(frame, sky_mask=mask)
    assert nmap.shape == (h, w)
    assert bool(np.all(np.isfinite(nmap))) and float(np.min(nmap)) > 0.0
    if h >= 12 and w >= 12:
        detect_point_sources(frame, sky_mask=mask)


def _corpus_luma(path: str) -> np.ndarray:
    """The luma the phone solver hands the detector: Rec. 709 of the 8-bit RGB, on the 0..1 scale."""
    with Image.open(REPO / path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float64)
    return detector._to_luma_2d(rgb) / 255.0


@pytest.mark.local_dataset(*PHOTOS.values())
def test_on_corpus_photos_the_map_sees_the_correlated_noise_of_a_developed_photo() -> None:
    # Demosaicing, denoising and JPEG make neighbouring pixels share noise, so the response's spread is several times
    # the pixel noise (1.9 to 3.7 times on the restoration spec's crops, M5): far above white noise's 1.17
    for pid, path in PHOTOS.items():
        luma = _corpus_luma(path)
        h, w = luma.shape
        tile = detector._noise_tile_px(h, w)
        nmap = matched_filter_noise(luma)
        centres = nmap[tile // 2 :: tile, tile // 2 :: tile]
        ratio = float(np.median(centres)) / _pixel_sigma(luma)
        assert 1.9 <= ratio <= 3.7, (pid, ratio)


def _crushed_sky(peak: float, ground_noise: float | None = None) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """An 8-bit 600 x 1500 sky crushed to 0, with 13 stars (1.15 px) of `peak` codes; with `ground_noise`, 180 rows of
    ground at 40 codes and that noise below it (the audit's frames)."""
    h, w = 600 + (180 if ground_noise is not None else 0), 1500
    frame = np.zeros((h, w))
    stars = [(60.0 + 105.0 * k + 0.3, 120.0 + 30.0 * (k % 5) + 0.4) for k in range(13)]
    for x, y in stars:
        _add_star(frame, x, y, peak)
    if ground_noise is not None:
        frame[600:, :] = 40.0 + np.random.default_rng(41).normal(0.0, ground_noise, (180, w))
    return np.clip(np.rint(frame), 0, 255).astype(np.uint8), stars


@pytest.mark.parametrize(("peak", "ground_noise"), [(15.0, None), (3.0, None), (5.0, 3.0), (15.0, 8.0)])
def test_stars_on_a_sky_crushed_to_black_are_found_as_the_parent_finds_them(peak: float, ground_noise) -> None:
    # The sky's tiles are clipped flats, so their noise cannot be measured: they take the parent's divider, not the
    # spread of the stars' own wings or of the ground beside them. The parent finds all 13 here (the audit's probes)
    frame, stars = _crushed_sky(peak, ground_noise)
    dets = detect_point_sources(frame, snr_threshold=4.0)
    found = sum(any(math.hypot(d.x_px - x, d.y_px - y) < 1.5 for d in dets) for x, y in stars)
    assert found == 13, (peak, ground_noise, found)


def _parent_detections(frame: np.ndarray, **kwargs) -> list:
    """The parent's detections on `frame`: its divider, the frame's pixel noise, everywhere."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(detector, "_detector_noise_map", lambda luma, matched, sky_mask, sigma_eff, **_:
                      np.full(matched.shape, float(sigma_eff)))
        return detect_point_sources(frame, **kwargs)


@pytest.mark.parametrize(("background", "read_noise"), [(0.6, 0.4), (1.3, 0.6), (2.0, 0.4), (3.0, 0.6)])
def test_dark_8bit_skies_near_the_black_clip_give_no_more_false_detections_than_the_parent(background, read_noise) -> None:
    # Clipped at 0, the response's distribution is skewed and its MAD understates its upper tail (the audit measured
    # up to 1.7 times the parent's false detections with the map alone); the pixel term keeps the divider at least the
    # parent's in every tile
    for seed in (1, 2):
        sky = background + np.random.default_rng(seed).normal(0.0, read_noise, (1000, 1500))
        frame = np.clip(np.rint(sky), 0, 255).astype(np.uint8)
        new = len(detect_point_sources(frame, snr_threshold=3.5))
        parent = len(_parent_detections(frame, snr_threshold=3.5))
        assert new <= parent, (background, read_noise, seed, new, parent)


def test_the_map_is_held_at_the_outermost_tile_centres_value_beyond_it() -> None:
    # Linear between tile centres and constant beyond the outermost, never extrapolated past them
    response = np.random.default_rng(43).normal(0.0, 1.0, (256, 512)) * np.linspace(1.0, 4.0, 512)[None, :]
    nmap = detector._matched_noise_map(response, None, floor=0.0, tile=64, overlap=8)
    first, last = 31, 512 - 32  # the outermost tile centres in x (tiles of 64 px)
    assert np.allclose(nmap[:, :first], nmap[:, [first]], rtol=1e-12, atol=0.0)
    assert np.allclose(nmap[:, last + 1 :], nmap[:, [last]], rtol=1e-12, atol=0.0)
    assert float(nmap[128, last]) > float(nmap[128, first])


@pytest.mark.parametrize(("length", "tile", "tiles"), [(400, 256, 2), (383, 256, 1), (127, 256, 1), (1000, 64, 16), (1023, 64, 16)])
def test_the_tile_count_is_the_rounded_share_at_least_one(length: int, tile: int, tiles: int) -> None:
    # round(length / tile) equal tiles, at least one: 400 / 256 = 1.56 gives 2, 383 / 256 = 1.50 gives 1 (round half to
    # even), 127 / 256 gives 1
    edges = detector._tile_edges(length, tile)
    assert edges.size - 1 == tiles and edges[0] == 0 and edges[-1] == length


def test_the_gate_is_4_75_noises_in_detection_and_in_forced_photometry() -> None:
    # Stars from 4.0 to 5.5 matched noises on white noise, asked for at 3.5: no detection is under the gate of 4.75,
    # some lie just above it, and forced photometry keeps those at the same value
    frame = _white((800, 1200), seed=44)
    noise = float(np.median(matched_filter_noise(frame)))
    rng = np.random.default_rng(45)
    for k, (x, y) in enumerate(zip(rng.uniform(20, 1180, 80), rng.uniform(20, 780, 80))):
        _add_star(frame, float(x), float(y), _amplitude_for(4.0 + 1.5 * k / 79, noise))
    dets = detect_point_sources(frame, snr_threshold=3.5)
    assert dets and min(d.snr for d in dets) >= 4.75
    near = [d for d in dets if d.snr < 5.0]
    assert near
    forced = detector.verify_candidates_on_original_pixels(frame, near, snr_threshold=3.5)
    assert len(forced) == len(near)


def test_each_tile_is_measured_over_its_overlap_too() -> None:
    # A band of high spread just outside tile 0's own columns but inside its overlap (an eighth of 64 px) raises tile 0
    response = np.random.default_rng(46).normal(0.0, 1.0, (256, 256))
    response[:, 64:72] *= 50.0
    with_overlap = detector._matched_noise_map(response, None, floor=0.0, tile=64)
    without = detector._matched_noise_map(response, None, floor=0.0, tile=64, overlap=0)
    assert float(with_overlap[128, 31]) > 1.05 * float(without[128, 31])
    assert int(round(detector.NOISE_OVERLAP_SHARE * 64)) == 8


def test_the_edge_pixels_are_not_sampled() -> None:
    # The outer EDGE_MARGIN_PX pixels, where no peak is taken, carry no weight in the map
    response = np.random.default_rng(47).normal(0.0, 1.0, (256, 256))
    m = detector.EDGE_MARGIN_PX
    rim = np.ones((256, 256), dtype=bool)
    rim[m:-m, m:-m] = False
    response[rim] *= 100.0
    nmap = detector._matched_noise_map(response, None, floor=0.0, tile=256)
    assert float(np.median(nmap)) == pytest.approx(_mad_sigma(response[m:-m, m:-m]), rel=1e-9)


def _bound_case(bound: str, sample):
    """What the map does at one sample of one of its bounds (MAP_BOUNDS)."""
    if bound in ("tile", "overlap"):
        response = np.random.default_rng(48).normal(0.0, 1.0, (64, 64))
        tile, overlap = (sample, 0) if bound == "tile" else (32, sample)
        try:
            detector._matched_noise_map(response, None, floor=1e-3, tile=tile, overlap=overlap)
        except ValueError:
            return "raises"
        return "ok"
    if bound == "overlap share":
        # Two `sample` px tiles across, one down, with a column of 100 times the noise in the last column of an eighth of
        # a tile's overlap and one in the column after it: tile 0's value, held from the edge to its centre, is the
        # spread of its own pixels and those of its overlap, inside the edge margin. Of the overlaps one under, at and
        # one over an eighth, the one whose window gives that value within 1e-9 of it is the overlap; the loud columns
        # set the three windows' spreads well over 1e-4 apart, so the answer does not rest on the last bits
        m = detector.EDGE_MARGIN_PX
        eighth = sample // 8
        response = np.random.default_rng(54).normal(0.0, 1.0, (sample, 2 * sample))
        response[:, sample + eighth - 1] *= 100.0
        response[:, sample + eighth] *= 100.0
        held = float(detector._matched_noise_map(response, None, floor=1e-3, tile=sample)[sample // 2, 0])
        spreads = {o: _mad_sigma(response[m : sample - m, m : sample + o]) for o in (eighth - 1, eighth, eighth + 1)}
        found = [o for o, spread in spreads.items() if abs(spread - held) <= 1e-9 * held]
        return found[0] if len(found) == 1 else found
    if bound == "tile minimum":
        return detector._noise_tile_px(*sample)
    if bound == "tile count":
        return detector._tile_edges(*sample).size - 1
    if bound == "sky share":
        # One 128 px tile, no overlap, and `sample` sky pixels inside the edge margin
        response = np.random.default_rng(49).normal(0.0, 1.0, (128, 128))
        mask = np.zeros((128, 128), dtype=bool)
        inner = np.argwhere(np.ones((120, 120), dtype=bool))[:sample] + detector.EDGE_MARGIN_PX
        mask[inner[:, 0], inner[:, 1]] = True
        nmap = detector._matched_noise_map(response, mask, floor=1e-3, tile=128, overlap=0)
        if bool(np.all(nmap == 1e-3)):
            return "floor"
        assert float(np.median(nmap)) == pytest.approx(_mad_sigma(response[mask]), rel=1e-12)
        return "measured"
    # "interior": a square frame of `sample` px, one tile; the edge margin is left out only when the frame has pixels
    # beyond it, and a 9 px frame has one, which fills under a quarter of the tile
    response = np.random.default_rng(50).normal(0.0, 1.0, (sample, sample))
    nmap = detector._matched_noise_map(response, None, floor=1e-3, tile=sample, overlap=0)
    if bool(np.all(nmap == 1e-3)):
        return "floor"
    assert float(np.median(nmap)) == pytest.approx(_mad_sigma(response), rel=1e-12)
    return "measured"


# Every bound the noise map defines, from one table, with a sample at each inside end and one just outside:
# (bound, sample, what the map does there)
MAP_BOUNDS = [
    ("tile", 1, "ok"), ("tile", 0, "raises"),
    ("overlap", 0, "ok"), ("overlap", -1, "raises"),
    # NOISE_OVERLAP_SHARE: an eighth of the tile, 32 px on a 256 px tile and 8 px on a 64 px one, held at the tile's
    # own value out to the frame's edge
    ("overlap share", 256, 32), ("overlap share", 64, 8),
    # NOISE_TILE_MIN_PX: the diagonal's share giving 63 px (held at 64), 64 px and 65 px
    ("tile minimum", (744, 992), 64), ("tile minimum", (756, 1008), 64), ("tile minimum", (768, 1024), 65),
    # round(length / tile) tiles and at least one: 0.5 rounds to 0 and is held at 1; 1.5 rounds to 2
    ("tile count", (128, 256), 1), ("tile count", (384, 256), 2), ("tile count", (383, 256), 1),
    # NOISE_MIN_SKY_SHARE of a 128 px tile is 4,096 px: measured from there up to every pixel inside the edge margin
    ("sky share", 4096, "measured"), ("sky share", 4095, "floor"), ("sky share", 120 * 120, "measured"),
    # EDGE_MARGIN_PX: a 9 px frame has one pixel inside it, an 8 px frame none, so all of its pixels are sampled
    ("interior", 9, "floor"), ("interior", 8, "measured"),
]


@pytest.mark.parametrize(("bound", "sample", "expected"), MAP_BOUNDS)
def test_each_bound_of_the_map_at_its_inside_ends_and_just_outside(bound: str, sample, expected) -> None:
    assert _bound_case(bound, sample) == expected


@pytest.mark.parametrize("kind", ["white", "dark 8-bit near the clip", "crushed sky over ground", "flat", "quantised"])
def test_the_map_is_never_under_the_parents_divider_not_by_its_last_bit(kind: str) -> None:
    # The parent divided its response by the frame's pixel noise itself. The kernel's L2 norm is 1 only up to its last
    # bit (0.9999999999999998), so a floor of that norm times the pixel noise sat one bit under the parent's divider
    # and moved ties between neighbouring peaks: on one benchmark frame two of 352 detections came out otherwise
    if kind == "white":
        frame = _white((360, 480), seed=51)
    elif kind == "dark 8-bit near the clip":
        frame = np.clip(np.rint(1.3 + np.random.default_rng(52).normal(0.0, 0.6, (360, 480))), 0, 255).astype(np.uint8)
    elif kind == "crushed sky over ground":
        frame = _crushed_sky(5.0, 3.0)[0]
    elif kind == "flat":
        frame = np.full((300, 400), 7.0)
    else:
        frame = np.ones((300, 400), dtype=np.uint8)
        rng = np.random.default_rng(53)
        frame[rng.integers(5, 295, 500), rng.integers(5, 395, 500)] = 2
    assert float(np.min(matched_filter_noise(frame))) >= _parent_divider(frame), kind


def test_a_tile_part_clipped_is_measured_on_its_sky_alone() -> None:
    # One tile, half black-clipped ground and half sky whose response spreads well above the pixel noise: its value is
    # the spread of the sky's response alone, inside the edge margin, with the clipped flats left out. Sampling the
    # flats too would lower it (a fully clipped tile gives the parent's divider either way, so only a mixed one shows it)
    h, w = 256, 256
    sky = _correlated((h, w), CORRELATION_SIGMA_PX[3.0], seed=55)
    frame = sky.copy()
    frame[:, w // 2 :] = float(np.min(sky)) - 50.0
    nmap = matched_filter_noise(frame, tile=256)
    response = _response(frame)
    m = detector.EDGE_MARGIN_PX
    valid = np.zeros((h, w), dtype=bool)
    valid[m:-m, m:-m] = True
    valid &= ~detector._clipped_flats(frame)
    sky_spread = _mad_sigma(response[valid])
    assert sky_spread > _parent_divider(frame)  # so the floor does not decide it
    assert float(nmap[h // 2, w // 4]) == pytest.approx(sky_spread, rel=1e-12)
    assert _mad_sigma(response[m:-m, m:-m]) < 0.9 * sky_spread  # what sampling the flats would give


def test_every_row_of_a_frame_taller_than_one_fill_block_is_written() -> None:
    # The map is written 1,024 rows at a time. On a frame of 2,100 rows whose tiles all measure the same spread (a
    # checkerboard of +1 and -1: MAD 1 in every window) every row holds that value, the rows at each block's edge too
    yy, xx = np.mgrid[0:2100, 0:64]
    response = np.where((yy + xx) % 2 == 0, 1.0, -1.0)
    nmap = detector._matched_noise_map(response, None, floor=1e-3, tile=64, overlap=8)
    assert nmap == pytest.approx(1.4826, rel=1e-12)


def test_a_flat_is_a_pixel_whose_whole_3x3_neighbourhood_sits_at_the_frames_top_or_bottom() -> None:
    # A 5 x 5 block at the top and one at the bottom: only their inner 3 x 3 are flats; their rims touch other values
    frame = np.random.default_rng(57).normal(100.0, 1.0, (40, 40))
    top, bottom = float(np.max(frame)) + 10.0, float(np.min(frame)) - 10.0
    frame[5:10, 5:10] = top
    frame[25:30, 25:30] = bottom
    expected = np.zeros((40, 40), dtype=bool)
    expected[6:9, 6:9] = True
    expected[26:29, 26:29] = True
    assert np.array_equal(detector._clipped_flats(frame), expected)


def test_injection_scales_its_stars_by_the_map_the_detector_makes_of_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    # The first map evaluate_injection_recovery makes, before any detection, is the frame's own matched-filter noise map
    # made with the detector's kernel
    frame = _white((300, 400), seed=58)
    expected = matched_filter_noise(frame)
    seen: list[np.ndarray] = []
    real = detector._detector_noise_map

    def recording(*args, **kwargs):
        value = real(*args, **kwargs)
        seen.append(value)
        return value

    monkeypatch.setattr(detector, "_detector_noise_map", recording)
    detector.evaluate_injection_recovery(frame, injected_snrs=(8.0,), n_per_snr=2)
    assert np.allclose(seen[0], expected, rtol=1e-12, atol=0.0)


def test_between_a_measured_tile_and_an_unmeasured_one_the_map_runs_straight_to_the_parents_divider() -> None:
    # Four 128 px tile rows; the lower two have no sky and take the floor. Between the second tile's centre (row 191.5)
    # and the third's (319.5) the map is a straight line from the second's spread down to the floor, never under it
    response = np.random.default_rng(59).normal(0.0, 3.0, (512, 64))
    sky = np.zeros((512, 64), dtype=bool)
    sky[:256] = True
    nmap = detector._matched_noise_map(response, sky, floor=1.0, tile=128, overlap=0)
    segment = nmap[192:320, 32]
    assert float(np.max(np.abs(np.diff(segment, 2)))) < 1e-12
    assert float(segment[-1]) > 1.0 and float(nmap[400, 32]) == pytest.approx(1.0, rel=1e-12)


def test_forced_photometry_returns_no_point_under_the_4_75_gate() -> None:
    # 120 stars of 4.3 to 5.0 matched noises on white noise, each asked for by its position at 3.5: forced photometry
    # returns none under 4.75 noises, though several peak between 4.5 and 4.75
    frame = _white((800, 1200), seed=60)
    noise = float(np.median(matched_filter_noise(frame)))
    rng = np.random.default_rng(61)
    positions = []
    for k, (x, y) in enumerate(zip(rng.uniform(20, 1180, 120), rng.uniform(20, 780, 120))):
        _add_star(frame, float(x), float(y), _amplitude_for(4.3 + 0.7 * k / 119, noise))
        positions.append((float(x), float(y)))
    forced = detector.verify_candidates_on_original_pixels(frame, positions, snr_threshold=3.5)
    assert forced and min(d.snr for d in forced) >= 4.75
