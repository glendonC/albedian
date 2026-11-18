"""The detector's gates follow the measured noise, its threshold is one documented number, clipped cores are sources,
and the stacker's composite keeps its noise falling with the frames (realsky-4).

Covers:
- One threshold, DETECTION_THRESHOLD, is the default everywhere and acts as given: no hidden floor under it.
- On pure noise, white and correlated at ratios 2, 3 and 3.7, the default meets its stated target of false detections.
- The peak and 3x3 gates count noises of the matched response, so a smooth blob is told from a star of the same matched
  SNR, as the parent told them apart only when its threshold was raised by the noise's correlation.
- A clipped core (connected pixels at the data's top code) is one source at its centre, flagged saturated with its area,
  in detection and in forced photometry; one pixel at the top is no core; data with no full-scale top has none.
- The stacker adds the frames' own aligned backgrounds, not frame 0's, so its matched noise keeps falling with N.
- No production caller passes a threshold that would lower the documented one.
All frames here are simulated.
"""

from __future__ import annotations

import ast
import inspect
import math
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from services.science import detector
from services.science.detector import DETECTION_THRESHOLD, detect_point_sources, matched_filter_noise

REPO = Path(__file__).resolve().parents[2]
KERNEL = detector._build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
# White noise smoothed by a Gaussian of these widths (px) gives the detector's response-over-pixel ratio 2, 3 and 3.7
CORRELATION_SIGMA_PX = {2.0: 0.651, 3.0: 1.283, 3.7: 2.917}


def _correlated(shape, sigma_c: float, seed: int, level: float = 100.0) -> np.ndarray:
    noise = np.random.default_rng(seed).normal(0.0, 1.0, shape)
    if sigma_c > 0:
        noise = ndimage.gaussian_filter(noise, sigma_c, mode="wrap")
    return level + noise / float(np.std(noise))


def _gaussian(frame: np.ndarray, x: float, y: float, amplitude: float, sigma_px: float, radius: int = 12) -> None:
    y0, x0 = int(round(y)), int(round(x))
    ys, xs = slice(max(0, y0 - radius), y0 + radius + 1), slice(max(0, x0 - radius), x0 + radius + 1)
    yy, xx = np.mgrid[ys, xs]
    frame[ys, xs] += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * sigma_px * sigma_px))


def _peak_for_matched_snr(snr: float, noise: float, sigma_px: float) -> float:
    """The peak of a Gaussian of `sigma_px` whose matched response, on a pixel centre, is `snr` noises."""
    grid = np.arange(-3, 4, dtype=np.float64)
    gx, gy = np.meshgrid(grid, grid)
    return snr * noise / float(np.sum(np.exp(-(gx * gx + gy * gy) / (2.0 * sigma_px * sigma_px)) * KERNEL))


def test_the_threshold_is_one_documented_number_used_by_every_entry_point() -> None:
    # 4.75: with the computed gate shares, the smallest step of 0.25 at which pure noise, white and correlated at
    # ratios 2, 3 and 3.7, gives at most 0.5 false detections per megapixel (measured 0.13, 0.26, 0.07 and 0 per
    # megapixel over five 2016 x 1512 frames each; at 4.5 white noise gave 0.66 and ratio 2 0.85)
    assert DETECTION_THRESHOLD == 4.75
    for fn in (detect_point_sources, detector.verify_candidates_on_original_pixels, detector.evaluate_injection_recovery):
        assert inspect.signature(fn).parameters["snr_threshold"].default == DETECTION_THRESHOLD, fn.__name__


def test_the_threshold_acts_as_given_with_no_floor_under_it() -> None:
    # Stars from 3 to 6 matched noises on white noise: at the default every detection reaches it, and asked for 3.0 the
    # detector reports stars between 3.0 and the default too
    frame = 100.0 + np.random.default_rng(3).normal(0.0, 1.0, (600, 800))
    noise = float(np.median(matched_filter_noise(frame)))
    rng = np.random.default_rng(4)
    for k, (x, y) in enumerate(zip(rng.uniform(20, 780, 40), rng.uniform(20, 580, 40))):
        _gaussian(frame, float(x), float(y), _peak_for_matched_snr(3.0 + 3.0 * k / 39, noise, 1.15), 1.15)
    default = detect_point_sources(frame)
    assert default and min(d.snr for d in default) >= DETECTION_THRESHOLD
    lower = detect_point_sources(frame, snr_threshold=3.0)
    assert any(3.0 <= d.snr < DETECTION_THRESHOLD for d in lower)
    assert min(d.snr for d in lower) >= 3.0


@pytest.mark.parametrize("target", [None, *sorted(CORRELATION_SIGMA_PX)])
def test_pure_noise_meets_the_stated_target_of_false_detections(target: float | None) -> None:
    # The target is 0.5 per megapixel, 1.5 on these 3.05 megapixel frames; 5 or more has a Poisson chance under 2 % at
    # that mean, and the measured rate is 0.26 per megapixel or less
    for seed in (61, 62):
        frame = _correlated((1512, 2016), 0.0 if target is None else CORRELATION_SIGMA_PX[target], seed)
        assert len(detect_point_sources(frame)) <= 4, (target, seed)


def test_a_smooth_blob_is_told_from_a_star_of_the_same_matched_snr() -> None:
    # On noise correlated at ratio 3 (its matched noise is 3 times its pixel noise), 24 stars (1.15 px) and 24 smooth
    # blobs (4 px: an extended light, or a swell of the noise), alternating on a grid, all 6 matched noises up. A
    # unit-peak star's matched response is 2.04 times its peak and the blob's 3.74 times, so the star's peak is 2.94
    # matched noises and the blob's 1.60, against the peak gate's PEAK_GATE_SHARE (0.39) of the threshold, 1.85 at
    # 4.75. The noise moves each one's measured matched SNR by about one noise, so a star under the threshold is lost
    # and a blob from about 6.9 up passes: over seeds 71 to 76 the detector finds 18 to 21 of the stars and 3 to 7 of
    # the blobs. The blobs that pass are the cost of keeping stars as wide as 1.4 px at the threshold
    frame = _correlated((1024, 1536), CORRELATION_SIGMA_PX[3.0], 71)
    noise = float(np.median(matched_filter_noise(frame)))
    stars, blobs = [], []
    for row in range(8):
        for col in range(6):
            x, y = 128.0 + 256.0 * col + 0.3, 64.0 + 128.0 * row + 0.2
            if (row + col) % 2 == 0:
                _gaussian(frame, x, y, _peak_for_matched_snr(6.0, noise, 1.15), 1.15)
                stars.append((x, y))
            else:
                _gaussian(frame, x, y, _peak_for_matched_snr(6.0, noise, 4.0), 4.0, radius=16)
                blobs.append((x, y))
    dets = detect_point_sources(frame)
    found = lambda points: sum(any(math.hypot(d.x_px - x, d.y_px - y) < 2.0 for d in dets) for x, y in points)  # noqa: E731
    assert found(stars) >= 16
    assert found(blobs) <= 8


def _clipped_star_frame(top: float = 255.0) -> tuple[np.ndarray, float, float, int]:
    """An 8-bit night frame (sky 20, noise 3) with a star bright enough to clip a disk of pixels at `top`."""
    frame = 20.0 + np.random.default_rng(81).normal(0.0, 3.0, (400, 600))
    x, y = 300.4, 200.6
    _gaussian(frame, x, y, 4000.0, 1.6, radius=15)
    frame = np.clip(np.rint(frame), 0, top)
    return frame, x, y, int(np.count_nonzero(frame >= top))


def test_a_clipped_star_is_one_saturated_source_at_its_centre() -> None:
    frame, x, y, area = _clipped_star_frame()
    assert area >= 9
    dets = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - x, d.y_px - y) < 8.0]
    assert len(dets) == 1, [(d.x_px, d.y_px, d.saturated) for d in dets]
    core = dets[0]
    assert core.saturated and core.saturated_area_px == area
    assert math.hypot(core.x_px - x, core.y_px - y) < 0.5
    assert not core.is_hot_pixel_impulse
    # Forced photometry at the star reports the same saturated core
    forced = detector.verify_candidates_on_original_pixels(frame, [(x + 1.0, y - 1.0)])
    assert len(forced) == 1 and forced[0].saturated and forced[0].saturated_area_px == area


def test_no_core_without_a_full_scale_top_and_none_from_one_pixel() -> None:
    # The top code is known only where the data reaches a standard full scale (1, 255 or 65535): clipped at 200 the
    # star is not called saturated [proposed: a camera's own white level would say more]
    frame, x, y, _ = _clipped_star_frame(top=200.0)
    assert not any(d.saturated for d in detect_point_sources(frame))
    # One pixel at 255 is a hot pixel or a peak, not a clipped core
    single = 20.0 + np.random.default_rng(82).normal(0.0, 3.0, (300, 400))
    single = np.clip(np.rint(single), 0, 254)
    single[150, 200] = 255
    assert not any(d.saturated for d in detect_point_sources(single))
    assert detector._clipped_cores(single) == []


def test_the_stack_adds_the_frames_own_backgrounds_so_its_noise_keeps_falling() -> None:
    # EXP-02's scene: from 10 to 40 frames the stack's matched noise should fall by about sqrt(4) = 2. Adding frame 0's
    # own background estimate back left its error undivided: 2.31 at 10 frames and 1.83 at 40 [tested, realsky-4]
    w, h = 640, 360
    wcs = detector.WCSModel(crval_ra_deg=82.0, crval_dec_deg=9.0, crpix_x=w / 2.0, crpix_y=h / 2.0, scale_deg_per_px=0.118,
                            rotation_deg=2.0, k1=-0.02, k2=0.0, parity=-1, width=w, height=h)
    mask = np.ones((h, w), dtype=bool)
    mask[-18:, :] = False
    frames = [
        detector.render_synthetic_starfield(width=w, height=h, wcs=wcs, psf_sigma_px=1.15, background_level=30.0,
                                            read_noise_sigma=5.4, flux_scale=92.0, seed=200 + i, sky_mask=mask,
                                            shift_yx=(i * 0.8, -i * 1.1))[0]
        for i in range(40)
    ]
    noise = {n: float(np.median(matched_filter_noise(
        detector.stack_video_frames_for_calibration(frames[:n], sky_mask=mask).composite_luma, sky_mask=mask)[mask]))
        for n in (10, 40)}
    assert noise[40] / noise[10] <= 0.6, noise


def _calls_with_a_threshold(root: Path) -> list[tuple[str, int, float]]:
    found = []
    for path in sorted((root / "services").rglob("*.py")) + sorted((root / "research").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in ("detect_point_sources", "verify_candidates_on_original_pixels", "evaluate_injection_recovery"):
                    for kw in node.keywords:
                        if kw.arg == "snr_threshold" and isinstance(kw.value, ast.Constant):
                            found.append((str(path.relative_to(root)), node.lineno, float(kw.value.value)))
    return found


def test_no_production_caller_lowers_the_documented_threshold() -> None:
    # Every stated threshold at or under the old hidden floor never acted; with no floor it would act and lower the
    # measured one, so it is gone from the call
    assert [c for c in _calls_with_a_threshold(REPO) if c[2] <= DETECTION_THRESHOLD] == []


def test_a_clipped_cores_fields_hold_what_their_names_say() -> None:
    # The star is 20 + 4000 g, with g a unit-peak Gaussian of 1.6 px, rounded and clipped at 255: its residual tops out
    # at 235, so its measured half maximum (117.5) lies where 4000 g = 117.5, a radius of 1.6 sqrt(2 ln(4000 / 117.5)) =
    # 4.25 px: the width at half maximum of the measured profile is 8.5 px, wider than the star's own 3.8 because of the
    # clipping. Its 5 x 5 centre is all clipped, so its peak is 1/25 of that patch
    frame, x, y, area = _clipped_star_frame()
    core = next(d for d in detect_point_sources(frame) if d.saturated)
    assert core.fwhm_px == pytest.approx(2.0 * 1.6 * math.sqrt(2.0 * math.log(4000.0 / 117.5)), rel=0.1)
    assert core.peak_to_total_ratio == pytest.approx(1.0 / 25.0, rel=0.05)
    assert 0.03 <= core.centroid_uncertainty_px <= 0.5
    bg, _ = detector._estimate_background_and_noise(frame)
    clipped = frame >= 255
    assert core.flux >= float(np.sum((frame - bg)[clipped]))  # the clipped pixels are a part of what it sums
    assert core.snr > DETECTION_THRESHOLD


def _plateau_frame(top: float, plateau: int, rest_max: float) -> np.ndarray:
    """Noise kept under `rest_max`, with `plateau` pixels in a row set to `top`."""
    frame = np.clip(20.0 + np.random.default_rng(91).normal(0.0, 2.0, (200, 300)), 0.0, rest_max)
    frame[100, 150 : 150 + plateau] = top
    return frame


# Edges of every bounded number this unit defines, from one table: (name, the call, what it must give)
EDGES = [
    ("threshold 0 is refused", lambda: detect_point_sources(np.ones((64, 64)), snr_threshold=0.0), ValueError),
    ("a negative threshold is refused", lambda: detect_point_sources(np.ones((64, 64)), snr_threshold=-1e-12), ValueError),
    ("a NaN threshold is refused", lambda: detect_point_sources(np.ones((64, 64)), snr_threshold=float("nan")), ValueError),
    ("an infinite threshold is refused", lambda: detect_point_sources(np.ones((64, 64)), snr_threshold=float("inf")), ValueError),
    ("forced photometry refuses threshold 0",
     lambda: detector.verify_candidates_on_original_pixels(np.ones((64, 64)), [(30.0, 30.0)], snr_threshold=0.0), ValueError),
    ("the smallest positive threshold is taken", lambda: detect_point_sources(np.ones((64, 64)), snr_threshold=1e-12), list),
    ("forced photometry takes the smallest positive threshold",
     lambda: detector.verify_candidates_on_original_pixels(np.ones((64, 64)), [(30.0, 30.0)], snr_threshold=1e-12), list),
    ("a frame with no finite value has no core", lambda: detector._clipped_cores(np.full((20, 20), np.nan)), 0),
    ("a top 5e-10 of 255 over it is a full scale", lambda: detector._clipped_cores(_plateau_frame(255.0 * (1 + 5e-10), 5, 250.0)), 1),
    ("a top 5e-9 of 255 over it is not", lambda: detector._clipped_cores(_plateau_frame(255.0 * (1 + 5e-9), 5, 250.0)), 0),
    ("one pixel at 255 is no core", lambda: detector._clipped_cores(_plateau_frame(255.0, 1, 250.0)), 0),
    ("two pixels at 255 are one core", lambda: detector._clipped_cores(_plateau_frame(255.0, 2, 250.0)), 1),
    ("a top of 254 is no full scale", lambda: detector._clipped_cores(_plateau_frame(254.0, 5, 250.0)), 0),
    ("a top just over 255 is no full scale", lambda: detector._clipped_cores(_plateau_frame(255.001, 5, 250.0)), 0),
    ("a top of 1 is a full scale", lambda: detector._clipped_cores(_plateau_frame(1.0, 5, 0.9) / 1.0), 1),
    ("a top just under 1 is not", lambda: detector._clipped_cores(_plateau_frame(0.999999, 5, 0.9)), 0),
    ("a top of 65535 is a full scale", lambda: detector._clipped_cores(_plateau_frame(65535.0, 5, 250.0)), 1),
]


@pytest.mark.parametrize(("name", "call", "expected"), EDGES, ids=[e[0] for e in EDGES])
def test_each_bound_at_its_edges(name: str, call, expected) -> None:
    if expected is ValueError:
        with pytest.raises(ValueError):
            call()
    elif expected is list:
        assert isinstance(call(), list)
    else:
        assert len(call()) == expected, name


@pytest.mark.parametrize(("share_of_gate", "kept"), [(1.0, True), (1.0 - 1e-6, False)])
def test_the_peak_gate_is_inclusive_at_its_share_of_the_threshold(share_of_gate: float, kept: bool) -> None:
    # A clean star whose peak is exactly PEAK_GATE_SHARE x threshold matched noises is kept, and a millionth less is not;
    # it is 1.6 px wide, so its 3x3 sum (2.33 times its peak over three noises, against BOX_GATE_SHARE / PEAK_GATE_SHARE
    # = 2.25) and its neighbours pass. With a gate noise of 1 the peak's count of noises is the peak itself, the same
    # float the gate compares, so the edge is exact on any platform
    yy, xx = np.mgrid[0:15, 0:15]
    peak = detector.PEAK_GATE_SHARE * DETECTION_THRESHOLD * share_of_gate
    residual = peak * np.exp(-((xx - 7) ** 2 + (yy - 7) ** 2) / (2.0 * 1.6**2))
    gate_sigma = 1.0
    core = KERNEL[2:5, 2:5]
    det = detector._measure_peak_photometry(
        residual, np.full(residual.shape, 50.0), 1e-3, core, float(np.linalg.norm(core)), 7, 7, DETECTION_THRESHOLD, None,
        0.0, allow_bright_impulse=False, gate_sigma=gate_sigma,
    )
    assert (det is not None) == kept


# The rules realsky-4's own mutation sweep (on realsky-3-v2) found no test for: a clipped core's fields, its window and
# edges, the core test's radius, the sky mask's look-up at a core, the stack's backgrounds and the forced threshold


def _rings(size: int, centre: tuple[int, int], values: list[float]) -> np.ndarray:
    """A residual whose square rings round `centre` (Chebyshev distance 0, 1, 2 ...) hold `values`, 0 beyond them."""
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.maximum(np.abs(yy - centre[0]), np.abs(xx - centre[1]))
    out = np.zeros((size, size))
    for ring, value in enumerate(values):
        out[distance == ring] = value
    return out


def _core_of(pixels: np.ndarray) -> "detector.ClippedCore":
    ys, xs = np.nonzero(pixels)
    return detector.ClippedCore(float(xs.mean()), float(ys.mean()), int(ys.size), math.sqrt(float(np.var(xs)) + float(np.var(ys))))


def test_a_clipped_cores_fields_on_a_profile_built_to_know_them() -> None:
    # The core's 3 x 3 at 1.0, then rings of 0.6, exactly half the peak (0.5: kept, at or above half), 0.49 and 0.2
    # (below half), and one pixel of the 0.6 ring at -0.3. So the half-maximum region is the 7 x 7 less that pixel
    # (48 px): flux 9 + 15 x 0.6 + 24 x 0.5, width 2 sqrt(48 / pi); the 5 x 5 patch's positive sum is 9 + 15 x 0.6; the
    # core's coordinates spread by sqrt(2/3 + 2/3), over sqrt(9) for the centroid's uncertainty
    residual = _rings(41, (20, 20), [1.0, 1.0, 0.6, 0.5, 0.49, 0.2])
    residual[18, 18] = -0.3
    snr_map = np.zeros((41, 41))
    snr_map[20, 20] = 7.5
    core = _core_of(residual >= 1.0)
    det = detector._core_detection(core, snr_map, residual, "asset", 1.5)
    assert det.flux == pytest.approx(9.0 + 15 * 0.6 + 24 * 0.5, rel=1e-12)
    assert det.fwhm_px == pytest.approx(2.0 * math.sqrt(48 / math.pi), rel=1e-12)
    assert det.peak_to_total_ratio == pytest.approx(1.0 / (9.0 + 15 * 0.6), rel=1e-12)
    assert det.centroid_uncertainty_px == pytest.approx(math.sqrt(4.0 / 3.0) / 3.0, rel=1e-12)
    assert (det.snr, det.saturated, det.saturated_area_px) == (7.5, True, 9)
    assert (det.x_px, det.y_px, det.asset_id, det.frame_time_sec) == (20.0, 20.0, "asset", 1.5)


def test_a_core_whose_centre_pixel_is_under_half_its_peak_sums_its_own_ring() -> None:
    # A ring of clipped pixels round a dimmer centre (0.1): no half-maximum region holds the centre pixel, so the
    # region is the one holding the peak's own pixels, the ring of 8 at 1.0; the flux, a lower bound, is their 8 and
    # never 0 (the detection contract takes a flux above 0 only), the width the ring's own 8 px; the dim pixels inside
    # and round it count for nothing
    residual = _rings(31, (15, 15), [0.1, 1.0, 0.2, 0.2])
    det = detector._core_detection(_core_of(residual >= 1.0), np.ones((31, 31)), residual, None, 0.0)
    assert det.flux == pytest.approx(8.0, rel=1e-12)
    assert det.fwhm_px == pytest.approx(2.0 * math.sqrt(8 / math.pi), rel=1e-12)


def test_a_core_whose_half_maximum_has_fewer_pixels_than_its_core_floors_its_width_at_the_core() -> None:
    # A 3 x 3 clipped core over a background step so only its centre pixel reaches half the residual peak (1 px < 9 px):
    # half_area is floored at core.area_px (9), so fwhm_px is never narrower than a disk of the clipped core's own area
    core_mask = np.zeros((31, 31), dtype=bool)
    core_mask[14:17, 14:17] = True
    residual = np.where(core_mask, 0.3, 0.0)
    residual[15, 15] = 1.0
    det = detector._core_detection(_core_of(core_mask), np.ones((31, 31)), residual, None, 0.0)
    assert det.fwhm_px == pytest.approx(2.0 * math.sqrt(9 / math.pi), rel=1e-12)


@pytest.mark.parametrize("edge", ["top left corner", "last row", "last column"])
def test_a_core_on_the_frames_edge_is_measured_inside_the_frame(edge: str) -> None:
    # A 2 px core at the edge with a 0.6 shoulder round it: its centre pixel, its window and its 5 x 5 patch stay inside
    # the frame, the snr is the map's largest value over the core's disk and one pixel beyond, inside the frame (each
    # pixel of the map holds its own value), and the flux is the whole shoulder and core
    h, w = 20, 20
    core_pixels = np.zeros((h, w), dtype=bool)
    if edge == "top left corner":
        core_pixels[0:2, 0:2] = True
    elif edge == "last row":
        core_pixels[h - 1, 9:11] = True
    else:
        core_pixels[9:11, w - 1] = True
    shoulder = ndimage.binary_dilation(core_pixels, structure=np.ones((5, 5), dtype=bool))
    residual = np.where(core_pixels, 1.0, np.where(shoulder, 0.6, 0.0))
    snr_map = 1.0 + np.arange(h * w, dtype=np.float64).reshape(h, w)
    core = _core_of(core_pixels)
    det = detector._core_detection(core, snr_map, residual, None, 0.0)
    yi, xi = int(round(core.y_px)), int(round(core.x_px))
    patch = residual[max(0, yi - 2) : yi + 3, max(0, xi - 2) : xi + 3]
    disk = math.sqrt(core.area_px / math.pi) + 1.0
    yy, xx = np.mgrid[0:h, 0:w]
    assert det.snr == float(np.max(snr_map[(yy - core.y_px) ** 2 + (xx - core.x_px) ** 2 <= disk * disk]))
    assert det.flux == pytest.approx(float(residual[shoulder].sum()), rel=1e-12)
    assert det.peak_to_total_ratio == pytest.approx(1.0 / float(patch.sum()), rel=1e-12)


def test_the_core_is_measured_out_to_its_radius_plus_12_px() -> None:
    # A 9 px core (radius ceil(sqrt(9 / pi)) = 2) on a half-maximum plateau wider than the window: the window, and so the
    # flux and the width, reach 2 + 12 = 14 px each way, a 29 x 29 square, and no farther
    residual = np.full((61, 61), 0.6)
    residual[29:32, 29:32] = 1.0
    det = detector._core_detection(_core_of(residual >= 1.0), np.ones((61, 61)), residual, None, 0.0)
    assert det.flux == pytest.approx(9 * 1.0 + (29 * 29 - 9) * 0.6, rel=1e-12)
    assert det.fwhm_px == pytest.approx(2.0 * math.sqrt(29 * 29 / math.pi), rel=1e-12)


@pytest.mark.parametrize(("beyond", "inside"), [(0.0, True), (0.02, False), (-0.02, True)])
def test_a_point_is_inside_a_core_out_to_its_disks_radius_plus_one_pixel(beyond: float, inside: bool) -> None:
    # The rule's own expression gives the edge, so the point on it is exactly on it on any machine
    core = detector.ClippedCore(0.0, 0.0, 9, 1.0)
    edge = math.sqrt(core.area_px / math.pi) + 1.0
    assert detector._inside_a_core(edge + beyond, 0.0, [core]) == inside


def _cores_frame(core_spots: list[tuple[slice, slice]]) -> np.ndarray:
    """An 8-bit 200 x 300 frame (sky 20, noise 3, all under 251) with these blocks of pixels at 255, each with the
    pixels next to it (8-neighbour) at 200, as a clipped star's profile goes on past its top (a block with an unlit
    ring of up to HOT_BLOCK_MAX_PX pixels is a hot-pixel block, not a core)."""
    frame = np.clip(np.rint(20.0 + np.random.default_rng(93).normal(0.0, 3.0, (200, 300))), 0, 250)
    block = np.zeros(frame.shape, dtype=bool)
    for ys, xs in core_spots:
        block[ys, xs] = True
    frame[ndimage.binary_dilation(block, structure=np.ones((3, 3), dtype=bool)) & ~block] = 200.0
    frame[block] = 255.0
    return frame


@pytest.mark.parametrize("masked_out", ["last row and column", "second-last row and column"])
def test_the_sky_mask_is_read_at_each_cores_own_centre(masked_out: str) -> None:
    # A core's centre pixel decides whether it is in the sky. With the last row and column out of the mask, a core in
    # the middle is reported; with the second-last ones out, cores on the last row and the last column are reported
    if masked_out == "last row and column":
        spots = [(slice(99, 102), slice(149, 152))]
        mask = np.ones((200, 300), dtype=bool)
        mask[-1, :] = False
        mask[:, -1] = False
    else:
        spots = [(slice(199, 200), slice(150, 152)), (slice(100, 102), slice(299, 300))]
        mask = np.ones((200, 300), dtype=bool)
        mask[-2, :] = False
        mask[:, -2] = False
    frame = _cores_frame(spots)
    cores = detector._clipped_cores(frame)
    assert len(cores) == len(spots)
    found = [d for d in detect_point_sources(frame, sky_mask=mask) if d.saturated]
    assert len(found) == len(spots)
    forced = detector.verify_candidates_on_original_pixels(frame, [(c.x_px, c.y_px) for c in cores], sky_mask=mask)
    assert len([d for d in forced if d.saturated]) == len(spots)
    # A core centred outside the mask is no source, by detection or by forced photometry (AUDIT M's mutants F4 and
    # F6, the mask's test on the cores taken away, lived: nothing here held it)
    outside = np.ones((200, 300), dtype=bool)
    for core in cores:
        outside[int(round(core.y_px)), int(round(core.x_px))] = False
    assert not any(d.saturated for d in detect_point_sources(frame, sky_mask=outside))
    forced_out = detector.verify_candidates_on_original_pixels(frame, [(c.x_px, c.y_px) for c in cores], sky_mask=outside)
    assert not any(d.saturated for d in forced_out)


def test_with_a_clipped_core_in_the_frame_the_other_stars_are_still_found() -> None:
    # A peak is skipped only inside a core: a bright unclipped star far from the clipped one is still found
    frame, x, y, _ = _clipped_star_frame()
    frame = frame.astype(np.float64)
    _gaussian(frame, 100.3, 100.7, 120.0, 1.15)
    frame = np.clip(np.rint(frame), 0, 254)
    frame[np.abs(np.arange(400)[:, None] - 200.6) + np.abs(np.arange(600)[None, :] - 300.4) < 2.0] = 255.0
    dets = detect_point_sources(frame)
    assert any(d.saturated for d in dets)
    assert any(not d.saturated and math.hypot(d.x_px - 100.3, d.y_px - 100.7) < 1.0 for d in dets)


def _stack_frames(levels: list[float], shifts: list[tuple[float, float]], seed: int, floor_at_zero: bool = False):
    """Frames of one field of 40 stars at the given sky levels, each moved by its shift, with noise 1."""
    rng = np.random.default_rng(seed)
    stars = list(zip(rng.uniform(30, 290, 40), rng.uniform(30, 210, 40), rng.uniform(80, 200, 40)))
    frames = []
    for k, (level, (dy, dx)) in enumerate(zip(levels, shifts)):
        frame = level + np.random.default_rng(seed + 1 + k).normal(0.0, 1.0, (240, 320))
        for x, y, amp in stars:
            _gaussian(frame, x + dx, y + dy, amp, 1.3)
        frames.append(np.clip(frame, 0.0, None) if floor_at_zero else frame)
    return frames


def test_the_stacks_sky_is_the_mean_of_every_frames_own_background() -> None:
    # Four frames of one field at sky levels 100, 110, 120 and 130: the composite's sky is their mean, 115
    frames = _stack_frames([100.0, 110.0, 120.0, 130.0], [(0.0, 0.0)] * 4, seed=94)
    composite = detector.stack_video_frames_for_calibration(frames).composite_luma
    assert float(np.median(composite)) == pytest.approx(115.0, abs=0.3)


def test_the_frames_backgrounds_are_shifted_as_their_residuals_are(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each frame's background goes through the same sub-pixel shift as its residual, linear and held at the edge
    calls: list[tuple] = []
    real = detector.ndimage.shift

    def recording(image, shift, *args, **kwargs):
        calls.append((tuple(np.round(np.asarray(shift, dtype=np.float64), 12)), kwargs.get("order"), kwargs.get("mode")))
        return real(image, shift, *args, **kwargs)

    monkeypatch.setattr(detector.ndimage, "shift", recording)
    frames = _stack_frames([100.0] * 3, [(0.0, 0.0), (0.4, -0.7), (0.9, -1.3)], seed=95)
    detector.stack_video_frames_for_calibration(frames)
    held = [(shift, order) for shift, order, mode in calls if mode == "nearest"]
    assert len(held) == 4 and held[0] == held[1] and held[2] == held[3]
    assert {order for _, order in held} == {1}


def test_a_black_stack_is_exactly_zero_where_its_sky_is_black() -> None:
    # Frames whose sky is crushed to 0: where the mean background and residual fall under 0, the composite is 0, no more
    frames = _stack_frames([0.0] * 3, [(0.0, 0.0)] * 3, seed=96, floor_at_zero=True)
    composite = detector.stack_video_frames_for_calibration(frames).composite_luma
    assert float(np.min(composite)) == 0.0


def test_an_ordinary_detection_is_not_saturated() -> None:
    assert detector.SourceDetection(1.0, 1.0, 1.0, 5.0, 2.0, 0.1).saturated_area_px == 0
    frame = 100.0 + np.random.default_rng(97).normal(0.0, 1.0, (200, 200))
    _gaussian(frame, 100.2, 99.7, 40.0, 1.15)
    star = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - 100.2, d.y_px - 99.7) < 1.0]
    assert len(star) == 1 and not star[0].saturated and star[0].saturated_area_px == 0


def test_forced_photometry_holds_its_threshold_where_the_shape_gates_would_pass() -> None:
    # A star's 3 x 3 (peak 4) in a dip 3.5 below the sky, 2 to 3.2 px out, as round an over-subtracted background: its
    # peak, 3 x 3 sum, neighbours and PSF likeness pass the gates, but the dip pulls its matched response under the
    # threshold (3.0), and forced photometry turns it away on that alone
    frame = 100.0 + np.random.default_rng(98).normal(0.0, 1.0, (160, 160))
    yy, xx = np.mgrid[0:160, 0:160]
    r = np.hypot(yy - 80, xx - 80)
    frame += np.where(r <= 1.5, 4.0 * np.exp(-(r * r) / (2 * 1.15**2)), 0.0)
    frame -= np.where((r >= 2.0) & (r <= 3.2), 3.5, 0.0)
    bg, sigma_bg = detector._estimate_background_and_noise(frame)
    residual = frame - bg
    sigma_eff = detector._effective_sigma(sigma_bg, frame)
    matched = ndimage.convolve(residual, KERNEL, mode="nearest")
    noise_map = detector._detector_noise_map(frame, matched, None, sigma_eff)
    snr = matched / noise_map
    core = KERNEL[2:5, 2:5]
    gates = detector._measure_peak_photometry(residual, snr, sigma_eff, core, float(np.linalg.norm(core)), 80, 80, 3.0, None,
                                               0.0, allow_bright_impulse=False, gate_sigma=float(noise_map[80, 80]))
    assert gates is not None and float(snr[80, 80]) < 3.0
    assert detector.verify_candidates_on_original_pixels(frame, [(80.0, 80.0)], snr_threshold=3.0) == []


@pytest.mark.parametrize(("share_of_gate", "kept"), [(1.0, True), (1.0 - 1e-6, False)])
def test_the_3x3_gate_is_inclusive_at_its_share_of_the_threshold(share_of_gate: float, kept: bool) -> None:
    # A peaked 3 x 3 (centre about 1.5, sides 0.4, corners 0.2) whose sum is exactly BOX_GATE_SHARE x threshold, with a
    # gate noise of 1/3 so that three gate noises are exactly 1.0: its 3 x 3 count of noises is then the same float the
    # gate compares, and it is kept; a millionth less is not. Every value sits on a 2^-20 grid, so the sum is exact on
    # any machine; its peak (4.5 noises), neighbours and likeness to the PSF pass their own gates
    step = 2.0**-20
    side, corner = round(0.4 / step) * step, round(0.2 / step) * step
    target = detector.BOX_GATE_SHARE * DETECTION_THRESHOLD * share_of_gate
    residual = np.zeros((15, 15))
    residual[6:9, 6:9] = corner
    residual[6, 7] = residual[8, 7] = residual[7, 6] = residual[7, 8] = side
    residual[7, 7] = target - 4 * side - 4 * corner
    assert float(np.sum(residual[6:9, 6:9])) == target
    core = KERNEL[2:5, 2:5]
    det = detector._measure_peak_photometry(
        residual, np.full(residual.shape, 50.0), 1e-3, core, float(np.linalg.norm(core)), 7, 7, DETECTION_THRESHOLD, None,
        0.0, allow_bright_impulse=False, gate_sigma=1.0 / 3.0,
    )
    assert (det is not None) == kept


def _model_star_through_the_gates(sigma: float, offset: float, matched_snr: float):
    """A noiseless Gaussian star of `sigma`, `offset` px off a pixel centre in x and in y, scaled so its matched SNR at
    the pixel where its response peaks is `matched_snr` (the map at 1, as the gate noise), through the shape gates at
    the default threshold."""
    yy, xx = np.mgrid[0:41, 0:41]
    star = np.exp(-((xx - 20.0 - offset) ** 2 + (yy - 20.0 - offset) ** 2) / (2.0 * sigma * sigma))
    matched = ndimage.convolve(star, KERNEL, mode="nearest")
    y0, x0 = np.unravel_index(int(np.argmax(matched)), matched.shape)
    residual = star * (matched_snr / float(matched[y0, x0]))
    snr = ndimage.convolve(residual, KERNEL, mode="nearest")
    core = KERNEL[2:5, 2:5]
    return detector._measure_peak_photometry(
        residual, snr, 1e-3, core, float(np.linalg.norm(core)), int(y0), int(x0), DETECTION_THRESHOLD, None, 0.0,
        allow_bright_impulse=False, gate_sigma=1.0,
    )


def test_the_gate_shares_are_computed_from_the_kernel_and_the_model_star() -> None:
    # On the kernel's own star on a pixel centre the peak is 0.49 of the matched response and the 3x3 sum 0.92 of three
    # times it; the shares are the least of those ratios over the margin of model stars, here its widest and most off-
    # centre star's
    assert detector._gate_shares(1.15, 1.15, (0.0,)) == pytest.approx((0.4906, 0.9189), abs=1e-3)
    widest = detector._gate_shares(detector.STAR_SIGMA_MAX_PX, detector.STAR_SIGMA_MAX_PX, (max(detector.STAR_OFFSETS_PX),))
    assert (detector.PEAK_GATE_SHARE, detector.BOX_GATE_SHARE) == widest
    assert detector.PEAK_GATE_SHARE < 0.49 and detector.BOX_GATE_SHARE < 0.92


# The margin of model stars, from one table: (star, matched SNR in thresholds, kept by the shape gates)
GATE_SHARE_EDGES = [
    ("the kernel's own star, centred, at the threshold", (1.15, 0.0, 1.0 + 1e-6), True),
    ("the widest model star half a pixel off, just over the threshold", (detector.STAR_SIGMA_MAX_PX, 0.5, 1.0 + 1e-6), True),
    ("the widest model star half a pixel off, just under it", (detector.STAR_SIGMA_MAX_PX, 0.5, 1.0 - 1e-6), False),
    ("a blob 2 px wide, a tenth over the threshold", (2.0, 0.0, 1.1), False),
]


@pytest.mark.parametrize(("name", "star", "kept"), GATE_SHARE_EDGES, ids=[e[0] for e in GATE_SHARE_EDGES])
def test_each_model_star_passes_the_shape_gates_from_the_threshold_and_a_broader_blob_does_not(name, star, kept) -> None:
    sigma, offset, share = star
    assert (_model_star_through_the_gates(sigma, offset, share * DETECTION_THRESHOLD) is not None) == kept, name


def _hot_block_frame(pixels: list[tuple[int, int]], wing: float | None = None) -> np.ndarray:
    """AUDIT M's frame for its hot-pixel probe (probe_hotpair.py): an 8-bit sky of 30 codes, noise 3, 20 unsaturated
    stars of 1.4 px away from the centre, and these pixels at the top code 255 from (150, 200); with `wing`, the
    pixels next to them (8-neighbour) at that level, as a clipped star's profile goes on past its flat top."""
    rng = np.random.default_rng(9201)
    h, w = 300, 400
    frame = 30.0 + rng.normal(0, 3.0, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(20):
        x, y = rng.uniform(20, w - 20), rng.uniform(20, h - 20)
        if abs(x - 200) < 20 and abs(y - 150) < 20:
            continue
        frame += rng.uniform(30, 150) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.4**2))
    block = np.zeros((h, w), dtype=bool)
    for dy, dx in pixels:
        block[150 + dy, 200 + dx] = True
    if wing is not None:
        frame[ndimage.binary_dilation(block, structure=np.ones((3, 3), dtype=bool)) & ~block] = wing
    frame[block] = 255.0
    return np.clip(np.rint(frame), 0, 255)


HOT_BLOCKS = {
    "a pair": [(0, 0), (0, 1)],
    "an L of 3": [(0, 0), (0, 1), (1, 0)],
    "a 2 x 2": [(0, 0), (0, 1), (1, 0), (1, 1)],
}


@pytest.mark.parametrize("shape", list(HOT_BLOCKS), ids=list(HOT_BLOCKS))
def test_a_block_of_hot_pixels_is_an_impulse_and_not_a_star_for_the_solver(shape: str) -> None:
    # Main reports each of these as a hot-pixel impulse (widths 0.94, 1.19 and 1.22 px, under the impulse rule's 1.25),
    # which the solver keeps out of its pool; realsky-4 made a saturated core of each (AUDIT M, finding 4). Nothing round
    # them is lit, so they are no clipped star: what is found there is an impulse, by detection and by forced photometry
    frame = _hot_block_frame(HOT_BLOCKS[shape])
    near = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - 200.5, d.y_px - 150.5) <= 3.0]
    assert near and all(d.is_hot_pixel_impulse and not d.saturated for d in near), [(d.fwhm_px, d.saturated) for d in near]
    forced = detector.verify_candidates_on_original_pixels(frame, [(200.0, 150.0)])
    assert not any(d.saturated or not d.is_hot_pixel_impulse for d in forced), [(d.fwhm_px, d.saturated) for d in forced]


@pytest.mark.parametrize("shape", list(HOT_BLOCKS), ids=list(HOT_BLOCKS))
def test_the_same_block_with_a_lit_ring_round_it_is_a_clipped_star(shape: str) -> None:
    # A clipped star's profile goes on past its top: with the pixels next to the block lit (200 codes, far above the
    # noise), the block is the core of a star, one saturated source
    frame = _hot_block_frame(HOT_BLOCKS[shape], wing=200.0)
    near = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - 200.5, d.y_px - 150.5) <= 3.0]
    assert len(near) == 1 and near[0].saturated, [(d.fwhm_px, d.saturated) for d in near]


def test_a_wingless_block_of_five_pixels_stays_one_saturated_source() -> None:
    # HOT_BLOCK_MAX_PX is 4: a block of 5 at 255 with nothing lit round it (a plus) is past it and stays one clipped
    # core, as a hard-edged disk does, so its flat top gives no peak at each of its pixels
    plus = [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]
    frame = _hot_block_frame(plus)
    near = [d for d in detect_point_sources(frame) if math.hypot(d.x_px - 200.0, d.y_px - 150.0) <= 3.0]
    assert len(near) == 1 and near[0].saturated and near[0].saturated_area_px == 5, [(d.fwhm_px, d.saturated) for d in near]


# The ring's bound at its edge, written as its value: 4.75 pixel noises (the detection threshold's), a ring's median
# at it keeps the core, just under it does not
RING_EDGES = [("ring at 4.75 noises", 4.75, 1), ("ring just under 4.75 noises", 4.75 * (1.0 - 1e-9), 0)]


@pytest.mark.parametrize(("name", "ring", "kept"), RING_EDGES, ids=[e[0] for e in RING_EDGES])
def test_a_small_core_is_a_star_only_with_its_ring_lit_to_the_threshold(name: str, ring: float, kept: int) -> None:
    luma = np.zeros((21, 21))
    luma[10, 10:12] = 255.0
    block = luma >= 255.0
    residual = np.where(ndimage.binary_dilation(block, structure=np.ones((3, 3), dtype=bool)) & ~block, ring, 0.0)
    residual[block] = 250.0
    assert len(detector._star_cores(luma, residual, 1.0, None)) == kept, name


def test_a_cores_snr_is_the_statistics_peak_over_its_disk_and_a_core_with_none_above_0_is_not_reported() -> None:
    # On the public corpus's rp-a-04 a whitened response read -54 at an 18 px core's centre and 136 within it; the
    # contract takes an snr above 0 only. A 3 x 3 core: the statistic -5 at its centre and 9 one pixel past its disk's
    # edge (at 2.69 px, the disk being sqrt(9 / pi) + 1 = 2.69 px) is reported at 9; with every value at or under 0 there,
    # the core is not reported
    residual = np.zeros((41, 41))
    residual[19:22, 19:22] = 1.0
    core = _core_of(residual >= 1.0)
    snr_map = np.full((41, 41), -1.0)
    snr_map[20, 20] = -5.0
    snr_map[20, 22] = 9.0
    snr_map[20, 24] = 50.0  # 4 px out: beyond the disk, not read
    assert detector._core_detection(core, snr_map, residual, None, 0.0).snr == 9.0
    snr_map[20, 22] = 0.0
    assert detector._core_detection(core, snr_map, residual, None, 0.0) is None
