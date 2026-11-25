"""Confirmed stars and their footprints on an original's own luma (design section 3; REQ-RST-6.1, 6.2), and what
the gate will call: a frame's points, their confirmation on the original, and aperture flux (design section 7)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import ndimage

from photo_frames import LUMA, star_field
from services.contracts import media as contracts
from services.media import stars
from services.science.detector import (
    DETECTION_THRESHOLD,
    _build_gaussian_psf_kernel,
    _estimate_background_and_noise,
    matched_filter_noise,
)


def luma(scene) -> np.ndarray:
    return stars.luma_codes(scene.codes)


def test_the_tiles_cover_the_frame_with_their_overlap() -> None:
    for height, width in ((1000, 700), (256, 256), (100, 300), (480, 481)):
        tiles = stars.tiles(height, width)
        covered = np.zeros((height, width), int)
        for tile in tiles:
            assert tile.y1 - tile.y0 <= stars.TILE_PX and tile.x1 - tile.x0 <= stars.TILE_PX
            covered[tile.y0:tile.y1, tile.x0:tile.x1] += 1
        assert covered.min() >= 1, (height, width)
    # Neighbouring tiles share their overlap
    a, b = stars.tiles(1000, 700)[:2]
    assert a.x1 - b.x0 == stars.OVERLAP_PX


def test_the_matched_noise_is_the_spread_of_the_detectors_own_response() -> None:
    scene = star_field(1)
    tile = luma(scene)[:256, :256]
    background, _ = _estimate_background_and_noise(tile, grouped=True)
    response = ndimage.convolve(tile - background, _build_gaussian_psf_kernel(), mode="nearest")
    expected = 1.4826 * np.median(np.abs(response - np.median(response)))
    # Measured as one tile of the crop's size (leaving out the 4 px border and clipped flats, never subdivided into
    # 64 px sub-tiles): 1.31 % off the whole-tile MAD on this 16 bit scene [tested], within 4 % [proposed]
    assert stars.matched_noise(tile) == pytest.approx(float(np.median(matched_filter_noise(tile, tile=stars.TILE_PX))), rel=1e-12)
    assert stars.tile_noise(tile).matched == stars.matched_noise(tile)
    assert stars.matched_noise(tile) == pytest.approx(expected, rel=0.04)
    # A crop wider than TILE_PX handed directly to `matched_noise` is still measured as one single tile
    rng = np.random.default_rng(21)
    wide = 100.0 + rng.normal(0.0, 1.0, (400, 400))
    wide[:100, :] = 100.0 + rng.normal(0.0, 8.0, (100, 400))
    assert stars.matched_noise(wide) == pytest.approx(float(np.median(matched_filter_noise(wide, tile=400))), rel=1e-12)


ROUNDING_SIGMA_8BIT = 1.0 / math.sqrt(12.0)
# Over the 20 tiles of each 768 x 1024 8 bit dark sky below, each tile's `matched_noise` differed from the median over
# that tile of the whole-frame `matched_filter_noise` map by at most 3.87 % [tested]; 4 % leaves room across float
# implementations [proposed]
DARK_TILE_MAP_TOLERANCE = 0.04
# At read noise 0.70 codes on a sky between two codes (16.3), the detector's grouped background and clipped spread keep
# the pixel sigma within 5 % of the rounded tile's own spread about its truth (realsky-4b; plain MAD read 63 % high)
DARK_SIGMA_TOLERANCE = 0.05
QUANTISED_DARK_SKIES = [
    pytest.param(-0.2, 0.35, 11, id="near black clip, 98 % at code 0"),
    pytest.param(16.0, 0.16, 12, id="quiet 8 bit sky, over 99 % at code 16"),
    pytest.param(16.2, 0.40, 13, id="sub-code 8 bit sky, read noise 0.40"),
    pytest.param(16.3, 0.70, 14, id="sub-code 8 bit sky, read noise 0.70"),
]


@pytest.mark.parametrize(("base", "read_noise", "seed"), QUANTISED_DARK_SKIES)
def test_a_quantised_dark_tile_never_reads_zero_and_matches_the_detectors_matched_filter_noise(
    base: float, read_noise: float, seed: int
) -> None:
    rng = np.random.default_rng(seed)
    frame = np.clip(np.rint(base + rng.normal(0.0, read_noise, (768, 1024))), 0, 255).astype(np.float64)
    frame_map = matched_filter_noise(frame)
    for tile in stars.tiles(*frame.shape):
        part = frame[tile.y0:tile.y1, tile.x0:tile.x1]
        measured = stars.tile_noise(part)
        # Every tile holds at least two 8 bit codes here, so neither its matched nor its pixel noise falls below the
        # 8 bit rounding floor, and neither ever reads 0
        assert measured.matched >= ROUNDING_SIGMA_8BIT * (1.0 - 1e-12)
        assert measured.sigma >= ROUNDING_SIGMA_8BIT * (1.0 - 1e-12)
        assert measured.matched == pytest.approx(float(np.median(matched_filter_noise(part, tile=stars.TILE_PX))), rel=1e-12)
        want = float(np.median(frame_map[tile.y0:tile.y1, tile.x0:tile.x1]))
        assert measured.matched == pytest.approx(want, rel=DARK_TILE_MAP_TOLERANCE)
        expected_bg, _ = _estimate_background_and_noise(part, grouped=True)
        assert np.array_equal(measured.residual, part - expected_bg)
        if base > 1.0 and read_noise >= 0.4:
            spread = float(np.std(part - base))
            assert abs(measured.sigma / spread - 1.0) <= DARK_SIGMA_TOLERANCE
    # A completely flat tile of one value keeps the pixel floor on `sigma` and stays un-floored (within float rounding
    # of 0, never clamped to 0.001) on `matched`
    flat = stars.tile_noise(np.full((256, 256), 16.0))
    assert flat.sigma >= 1e-3 and flat.matched == pytest.approx(0.0, abs=1e-12)


def test_a_dark_linear_light_tile_keeps_its_own_matched_noise_below_the_detectors_code_floor() -> None:
    rng = np.random.default_rng(16)
    dark = 0.002 + rng.normal(0.0, 0.0002, (256, 256))
    background, _ = _estimate_background_and_noise(dark, grouped=True)
    response = ndimage.convolve(dark - background, _build_gaussian_psf_kernel(), mode="nearest")
    expected = 1.4826 * float(np.median(np.abs(response - np.median(response))))
    # Continuous linear light (0..1) is not clamped at ABSOLUTE_NOISE_FLOOR = 0.001: ~0.00023 here [tested]
    got = stars.matched_noise(dark)
    assert got == pytest.approx(expected, rel=0.05)
    assert 0.00018 < got < 0.0003
    # When over three quarters of a dark linear tile sits on black clip 0.0, it falls back to the positive pixel noise
    # (never 0.0 as the plain whole-tile MAD gave, and never clamped up to 0.001)
    crushed = np.zeros((256, 256), dtype=np.float64)
    crushed[:96, :96] = np.maximum(0.0, 0.002 + rng.normal(0.0, 0.0002, (96, 96)))
    crushed_got = stars.matched_noise(crushed)
    assert 0.0 < crushed_got < 0.0003


def nearest(points: np.ndarray, x: float, y: float) -> float:
    return float(np.min(np.hypot(points[:, 0] - x, points[:, 1] - y))) if len(points) else np.inf


def test_the_stars_are_the_drawn_ones_found_and_confirmed() -> None:
    scene = star_field(2, size=320, count=24, peaks=(0.02, 0.3))
    field = stars.find_stars(scene.codes, peak=scene.code_steps)
    places = np.array([(s.x_px, s.y_px) for s in field.stars])
    # Every confirmed star is a drawn one, and every bright drawn star is confirmed
    for star in field.stars:
        assert nearest(scene.stars, star.x_px, star.y_px) < 1.0
    for x, y, peak in scene.stars:
        if peak > 0.05:
            assert nearest(places, x, y) < 1.0, (x, y, peak)
    assert field.confirmed == len(field.stars) <= field.found
    counts = contracts.StarCounts.model_validate(field.counts())
    assert counts.confirmed == field.confirmed
    # No two stars within the merge radius: the overlapping tiles' duplicates are merged
    for i, a in enumerate(field.stars):
        for b in field.stars[i + 1:]:
            assert np.hypot(a.x_px - b.x_px, a.y_px - b.y_px) > stars.MERGE_RADIUS_PX


def test_a_point_its_confirmation_refuses_is_found_but_is_no_star() -> None:
    # A lone hot pixel: the detector finds it as a bright impulse, and its verification, which takes no impulse,
    # refuses it
    scene = star_field(7, size=256, count=10, peaks=(0.05, 0.2))
    clean = stars.find_stars(scene.codes, peak=scene.code_steps)
    codes = scene.codes.copy()
    far = [(x, y) for x in range(40, 216, 8) for y in range(40, 216, 8) if nearest(scene.stars, x, y) > 20]
    x, y = far[0]
    codes[y, x] = scene.code_steps * 3 // 5
    field = stars.find_stars(codes, peak=scene.code_steps)
    places = np.array([(s.x_px, s.y_px) for s in field.stars])
    assert nearest(places, x, y) > 3, "the hot pixel is no star"
    assert field.found == clean.found + 1 and field.confirmed == clean.confirmed


def test_a_star_inside_a_clipped_region_is_left_out_and_counted() -> None:
    scene = star_field(3, size=256, count=12, peaks=(0.05, 0.2))
    codes = scene.codes.copy()
    x, y, _ = scene.stars[0]
    # A saturated patch beside the first star, so its grown region holds the star
    codes[int(y) - 3:int(y) + 4, int(x) + 3:int(x) + 8] = scene.code_steps
    field = stars.find_stars(codes, peak=scene.code_steps)
    assert field.clipped[int(round(y)), int(round(x))]
    assert nearest(np.array([(s.x_px, s.y_px) for s in field.stars]), x, y) > 3
    assert field.clipped_left_out >= 1


def test_each_point_inside_a_clipped_region_is_left_out_and_counted_once() -> None:
    scene = star_field(3, size=256, count=12, peaks=(0.05, 0.2))
    plane = luma(scene)
    nothing = np.zeros(plane.shape, bool)
    points, left_out = stars.find_points(plane, stars.CONFIRM_THRESHOLD_SNR, nothing)
    assert left_out == 0 and len(points) > 1
    one = nothing.copy()
    one[int(round(points[0].y_px)), int(round(points[0].x_px))] = True
    kept, left_out = stars.find_points(plane, stars.CONFIRM_THRESHOLD_SNR, one)
    assert left_out == 1 and kept == points[1:]


def test_the_clipped_map_is_every_sample_at_the_peak_grown_by_its_stated_width() -> None:
    codes = np.zeros((64, 64, 3), np.uint8)
    codes[32, 32, 1] = 255  # one channel at its peak is enough
    clipped = stars.clipped_map(codes, peak=255)
    distance = np.hypot(*np.mgrid[0:64, 0:64] - 32)
    assert np.array_equal(clipped, distance <= stars.CLIPPED_GROW_PX)
    # A luma plane alike: a sample at the peak is clipped, one a code under it is not
    plane = np.zeros((64, 64), np.uint8)
    plane[32, 32], plane[10, 10] = 255, 254
    assert np.array_equal(stars.clipped_map(plane, peak=255), distance <= stars.CLIPPED_GROW_PX)


def test_a_frame_with_no_clipped_sample_has_an_empty_clipped_map_and_loses_no_noise_block() -> None:
    """Verdict restore-3, item 2: with nothing at the peak, the guard returns the empty map. Without it scipy's distance
    transform, given no background, measures from just outside the frame's top left corner and marks samples there
    (seven on these frames), and the noise table loses a block."""
    from services.media import noise
    from photo_frames import srgb_encode_slope

    for codes in (np.zeros((32, 32), np.uint8), np.full((16, 24, 3), 254, np.uint8)):
        clipped = stars.clipped_map(codes, peak=255)
        assert clipped.shape == codes.shape[:2] and not clipped.any()
    for seed in (3, 5, 21):
        scene = star_field(seed, size=192, count=8)
        assert not (scene.codes >= scene.code_steps).any(), "a frame with no sample at the peak"
        field = stars.find_stars(scene.codes, peak=scene.code_steps)
        assert not field.clipped.any() and field.clipped_left_out == 0
        # The noise table's blocks are those of a frame with nothing clipped at all
        measured = [noise.measure_noise(scene.linear, scene.codes, field.footprint, clipped, encode_slope=srgb_encode_slope,
                                        code_steps=scene.code_steps).table()["blockCounts"]
                    for clipped in (field.clipped, np.zeros(field.clipped.shape, dtype=bool))]
        assert measured[0] == measured[1], seed


def test_of_two_duplicates_the_one_with_the_higher_snr_is_kept() -> None:
    for first, second in ((5.0, 9.0), (9.0, 5.0)):
        kept = stars._merged([stars.Point(0.0, 0.0, first, 0), stars.Point(1.0, 0.0, second, 1)])
        assert [point.snr for point in kept] == [9.0]


def test_a_footprint_follows_its_stars_own_profile() -> None:
    size = 128
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(4)
    sky = 2000 + 40 * rng.standard_normal((size, size))
    radii = []
    for peak, sigma in ((600.0, 1.3), (6000.0, 1.3), (60000.0, 4.0)):
        image = sky + peak * np.exp(-((xx - 64) ** 2 + (yy - 64) ** 2) / (2 * sigma**2))
        radii.append(stars.footprint_radius(image, 64.0, 64.0))
    assert 1 <= radii[0] <= radii[1] <= radii[2] == stars.FOOTPRINT_MAX_PX
    # Read against the sky with its background taken away, the faint star ends well inside the largest radius
    assert radii[0] < stars.FOOTPRINT_MAX_PX // 2


def test_the_footprint_map_is_one_inside_with_a_cosine_edge_and_the_larger_weight_where_two_meet() -> None:
    placed = (stars.Star(x_px=20.0, y_px=20.0, snr=10.0, radius_px=4), stars.Star(x_px=26.0, y_px=20.0, snr=8.0, radius_px=3))
    weights = stars.footprint_map((40, 48), placed)
    assert weights[20, 20] == 1.0 and weights[20, 24] == 1.0
    assert weights[20, 15] == pytest.approx(0.5, abs=1e-12)  # one pixel into the 2 px edge of the 4 px star
    assert weights[20, 14] == pytest.approx(0.0, abs=1e-12) and weights[5, 5] == 0.0
    assert weights[20, 30] == pytest.approx(0.5, abs=1e-12)  # the second star's edge, where the first gives nothing
    assert weights.min() >= 0.0 and weights.max() <= 1.0


EDGE_PLACES = [(1.2, 0.6), (38.7, 1.4), (0.4, 37.9), (38.9, 38.6), (20.3, 19.6)]


def test_the_footprint_map_is_its_formula_over_the_whole_frame_at_every_edge() -> None:
    yy, xx = np.mgrid[0:40, 0:40]
    for radius in (1, 3, stars.FOOTPRINT_MAX_PX):
        placed = tuple(stars.Star(x, y, 10.0, radius) for x, y in EDGE_PLACES)
        expected = np.zeros((40, 40))
        for star in placed:
            beyond = np.hypot(xx - star.x_px, yy - star.y_px) - radius
            weight = np.where(beyond <= 0, 1.0, np.where(beyond < stars.FOOTPRINT_EDGE_PX,
                                                         0.5 * (1 + np.cos(np.pi * beyond / stars.FOOTPRINT_EDGE_PX)), 0.0))
            expected = np.maximum(expected, weight)
        assert np.abs(stars.footprint_map((40, 40), placed) - expected).max() < 1e-12, radius


def test_the_aperture_flux_is_its_formula_over_the_whole_frame_at_every_edge() -> None:
    rng = np.random.default_rng(16)
    image = rng.uniform(0, 1, (40, 40))
    yy, xx = np.mgrid[0:40, 0:40]
    for radius in (0, 3, 6):
        aperture = max(radius, stars.APERTURE_MIN_PX)
        for x, y in EDGE_PLACES:
            distance = np.hypot(xx - x, yy - y)
            inside = distance <= aperture
            annulus = (distance >= aperture + stars.ANNULUS_INNER_PX) & (distance <= aperture + stars.ANNULUS_OUTER_PX)
            expected = image[inside].sum() - inside.sum() * np.median(image[annulus])
            assert stars.aperture_flux(image, x, y, radius) == pytest.approx(expected, rel=1e-9, abs=1e-9), (radius, x, y)


def test_a_point_the_original_does_not_hold_is_not_confirmed() -> None:
    scene = star_field(5, size=256, count=10, peaks=(0.05, 0.2))
    original = luma(scene)
    variant = original.copy()
    yy, xx = np.mgrid[0:256, 0:256]
    variant += 4000 * np.exp(-((xx - 200.0) ** 2 + (yy - 40.0) ** 2) / (2 * 1.3**2))
    field = stars.find_stars(scene.codes, peak=scene.code_steps)
    points, left_out = stars.find_points(variant, stars.CONFIRM_THRESHOLD_SNR, field.clipped)
    confirmed = stars.confirm(points, original)
    painted = [ok for point, ok in zip(points, confirmed) if np.hypot(point.x_px - 200, point.y_px - 40) < 2]
    assert painted == [False]
    assert sum(confirmed) >= field.confirmed - 1 and left_out == 0


def test_the_aperture_flux_of_a_known_star() -> None:
    size = 96
    yy, xx = np.mgrid[0:size, 0:size]
    total = 50.0
    image = 0.01 + total / (2 * np.pi * 1.3**2) * np.exp(-((xx - 48.3) ** 2 + (yy - 47.6) ** 2) / (2 * 1.3**2))
    assert stars.aperture_flux(image, 48.3, 47.6, 5) == pytest.approx(total, rel=0.01)
    # The aperture is never under its stated smallest radius
    assert stars.aperture_flux(image, 48.3, 47.6, 0) == pytest.approx(stars.aperture_flux(image, 48.3, 47.6, stars.APERTURE_MIN_PX))


def test_the_empty_aperture_noise_is_measured_on_a_fixed_lattice_clear_of_stars() -> None:
    rng = np.random.default_rng(6)
    image = 0.02 + 0.001 * rng.standard_normal((400, 400))
    footprint = np.zeros((400, 400))
    footprint[:200] = 1.0
    clipped = np.zeros((400, 400), bool)
    sigma = stars.empty_aperture_sigma(image, 3, footprint, clipped)
    pixels = int(np.sum(np.hypot(*np.mgrid[-3:4, -3:4]) <= 3))
    assert sigma == pytest.approx(0.001 * np.sqrt(pixels), rel=0.3)
    assert stars.empty_aperture_sigma(image, 3, footprint, clipped) == sigma
    places = stars.empty_places(image.shape, 3, footprint, clipped)
    assert len(places) == stars.EMPTY_APERTURES
    assert all(y - 199 >= stars.EMPTY_CLEARANCE_PX for _, y in places)


def test_the_star_step_records_its_parameters() -> None:
    step = {"kind": "find_stars", "version": stars.STEP_VERSION, "parameters": stars.parameters()}
    contracts.GraphStep.model_validate(step)
    names = {p["name"] for p in step["parameters"]}
    assert names == {"tilePx", "overlapPx", "confirmThresholdSnr", "matchRadiusPx", "mergeRadiusPx", "footprintMaxPx",
                     "clippedGrowPx", "statistic"}
    assert all(p["origin"] == "default" and p["basis"] for p in step["parameters"])


def test_the_luma_is_the_detectors_weighting_of_the_codes() -> None:
    codes = np.arange(2 * 3 * 3).reshape(2, 3, 3)
    assert np.allclose(stars.luma_codes(codes), codes @ LUMA)
    assert np.array_equal(stars.luma_codes(codes[..., 0]), codes[..., 0].astype(np.float64))
    # Any channel after the third is left out of the luma
    with_fourth = np.concatenate([codes, np.full((2, 3, 1), 255)], axis=-1)
    assert np.allclose(stars.luma_codes(with_fourth), codes @ LUMA)


def test_the_confirmation_threshold_is_the_detectors_default_threshold() -> None:
    # The detector takes a threshold as given, DETECTION_THRESHOLD in both its calls unless the caller gives another;
    # the step confirms at that default and records it as its own
    assert stars.CONFIRM_THRESHOLD_SNR == DETECTION_THRESHOLD
    assert {p["name"]: p["value"] for p in stars.parameters()}["confirmThresholdSnr"] == stars.CONFIRM_THRESHOLD_SNR


def ring_profile(values: dict[int, float], size: int = 41) -> np.ndarray:
    """A residual holding values[r] on the ring of each whole radius r (from r - 0.5 to r + 0.5), zero elsewhere."""
    yy, xx = np.mgrid[0:size, 0:size]
    ring = np.floor(np.hypot(xx - size // 2, yy - size // 2) + 0.5).astype(int)
    out = np.zeros((size, size))
    for r, value in values.items():
        out[ring == r] = value
    return out


def test_a_footprint_radius_is_the_first_ring_at_or_under_one_sigma() -> None:
    center = 20.0
    # A faint star whose first ring is sky already
    assert stars._radius(ring_profile({0: 5.0, 1: 1.0}), 1.0, center, center) == 1
    # A ring of 3 sigma is still the star; one of half a sigma is sky
    assert stars._radius(ring_profile({0: 9.0, 1: 3.0, 2: 0.5}), 1.0, center, center) == 2
    # A ring of exactly one sigma is sky
    assert stars._radius(ring_profile({0: 9.0, 1: 4.0, 2: 1.0}), 1.0, center, center) == 2
    # Never beyond the largest radius
    assert stars._radius(ring_profile({r: 2.0 for r in range(30)}), 1.0, center, center) == stars.FOOTPRINT_MAX_PX


def ring_decided_by_one_line(x: float, y: float, *, row: int | None = None, column: int | None = None,
                             size: int = 41) -> np.ndarray:
    """A residual whose rings 1 to 10 hold 2 sigma and whose ring 11 has a median of at most 1 sigma only with its
    samples in one row or one column: those and just under half of the others are 0, the rest 2."""
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.hypot(xx - x, yy - y)
    residual = np.where(distance < 10.5, 2.0, 0.0)
    ring = (distance >= 10.5) & (distance < 11.5)
    in_row = ring & ((yy == row) if row is not None else (xx == column))
    others = np.flatnonzero(ring & ~in_row)
    zeros = (others.size - 1) // 2  # fewer than half of the others, so without the row the median is 2
    values = np.full(others.size, 2.0)
    values[:zeros] = 0.0
    residual.flat[others] = values
    assert in_row.sum() + zeros >= ring.sum() / 2, "with the row, at least half the ring is 0"
    return residual


def test_the_radius_rule_reads_the_whole_outermost_ring_at_the_frames_edges_and_beyond_the_centre() -> None:
    # The far row: a centre more than half a pixel past its row puts ring 11's last samples 12 rows below it
    assert stars._radius(ring_decided_by_one_line(20.0, 20.7, row=32), 1.0, 20.0, 20.7) == 11
    # The frame's top and left edges: a centre 11 rows down or 11 columns in puts ring 11's first samples in row 0
    # or column 0, and one more than half a pixel past its column puts the last 12 columns on
    assert stars._radius(ring_decided_by_one_line(20.0, 11.0, row=0), 1.0, 20.0, 11.0) == 11
    assert stars._radius(ring_decided_by_one_line(11.0, 20.0, column=0), 1.0, 11.0, 20.0) == 11
    assert stars._radius(ring_decided_by_one_line(20.7, 20.0, column=32), 1.0, 20.7, 20.0) == 11


def residual_at_distances(x: float, y: float, values: dict[float, float], size: int = 41) -> np.ndarray:
    """A residual holding values[d] at every sample whose distance from (x, y), rounded to 3 places, is d; 0 elsewhere."""
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.round(np.hypot(xx - x, yy - y), 3)
    out = np.zeros((size, size))
    for d, value in values.items():
        out[distance == d] = value
    return out


def test_the_radius_rings_are_half_open_and_start_at_one() -> None:
    # A centre within one sigma still gives a radius of 1
    assert stars._radius(ring_profile({0: 0.5, 1: 0.5}), 1.0, 20.0, 20.0) == 1
    # Centred on a sample: ring 1 holds the four at 1 px (2 sigma) and the four at 1.414 px (0), so its median is
    # 1 sigma; the centre (2 sigma) is not in it
    centred = residual_at_distances(20.0, 20.0, {0.0: 2.0, 1.0: 2.0, 1.414: 0.0})
    assert stars._radius(centred, 1.0, 20.0, 20.0) == 1
    # Centred between two samples: ring 1 holds the two at exactly 0.5 px (0), and of the four at 1.118 px one is 0
    # and three 2, so its median is 1; the two at exactly 1.5 px (2) belong to ring 2, as do those at 1.803 px
    between = residual_at_distances(20.5, 20.0, {0.5: 0.0, 1.118: 2.0, 1.5: 2.0, 1.803: 2.0})
    one_of_four = [(19, 20)]  # (row, column) of one sample at 1.118 px
    between[one_of_four[0]] = 0.0
    assert stars._radius(between, 1.0, 20.5, 20.0) == 1


def test_the_matched_noise_decides_the_confirmation_where_the_noise_is_shared_between_neighbours() -> None:
    # Noise shared between neighbours: the matched filter sums it, so the pixel sigma underrates its response's spread.
    # The strongest noise peaks of such a frame pass the detector's gate on the pixel sigma and fail it on sigma_m.
    rng = np.random.default_rng(9)
    field = ndimage.gaussian_filter(rng.standard_normal((256, 256)), 1.5, mode="wrap")
    original = 1000 + 20 * field / field.std()
    noise = stars.tile_noise(original)
    response = ndimage.convolve(noise.residual, _build_gaussian_psf_kernel(), mode="nearest")
    peaks = np.argwhere((response == ndimage.maximum_filter(response, size=7)) & (response > 0))
    strongest = sorted(peaks.tolist(), key=lambda p: -response[p[0], p[1]])[:5]
    points = [stars.Point(float(x), float(y), 0.0, 0) for y, x in strongest if 8 <= x < 248 and 8 <= y < 248]
    assert points
    assert stars.confirm(points, original) == [False] * len(points)
    # The control: on the pixel sigma alone some of them pass. This is the detector's statistic before it divided by
    # its response's own noise: the unit-norm kernel's response over the pixel sigma, each point a peak of it
    pixel_snr = response / noise.sigma
    assert any(pixel_snr[int(p.y_px), int(p.x_px)] >= stars.CONFIRM_THRESHOLD_SNR for p in points)

def test_every_point_found_meets_the_threshold_asked_for() -> None:
    # The detector returns only peaks whose snr is at least the threshold, taken as given, so no second filter
    scene = star_field(2, size=320, count=24, peaks=(0.02, 0.3))
    clipped = stars.clipped_map(scene.codes, scene.code_steps)
    for threshold in (stars.CONFIRM_THRESHOLD_SNR, 6.0, 12.0):
        points, _ = stars.find_points(luma(scene), threshold, clipped)
        assert points and all(point.snr >= threshold for point in points)


def test_on_a_clear_frame_the_lattice_runs_to_the_frames_edges_and_stays_inside() -> None:
    # Nothing to keep clear of: every lattice point counts, from the margin at the frame's own edges (no distance to
    # the frame's border is taken as clearance), and none lies where its annulus would leave the frame
    shape, radius = (64, 64), 2
    nothing = np.zeros(shape)
    places = stars.empty_places(shape, radius, nothing, nothing.astype(bool))
    margin = max(radius, stars.APERTURE_MIN_PX) + stars.ANNULUS_OUTER_PX
    expected = [(x, y) for y in range(margin, 64 - margin, 2 * margin + 1) for x in range(margin, 64 - margin, 2 * margin + 1)]
    assert places == expected and len(places) < stars.EMPTY_APERTURES


def clear_lattice(shape: tuple[int, int], radius: int, footprint: np.ndarray, clipped: np.ndarray) -> list[tuple[int, int]]:
    """Every point (x, y) of the fixed lattice, in row order, EMPTY_CLEARANCE_PX clear of every footprint and clipped
    region, its aperture and annulus inside the frame: what the empty places are taken from."""
    margin = max(radius, stars.APERTURE_MIN_PX) + stars.ANNULUS_OUTER_PX
    clearance = stars.clearance_map(footprint, clipped)
    return [(x, y) for y in range(margin, shape[0] - margin, 2 * margin + 1)
            for x in range(margin, shape[1] - margin, 2 * margin + 1) if clearance[y, x] >= stars.EMPTY_CLEARANCE_PX]


def test_the_empty_places_spread_over_the_whole_frame_not_its_top_rows() -> None:
    # gate-flux-floor-one (decided by the lead at the 22:38 and 01:34 stamps): the first sixty lattice points in row
    # order all lay on the top rows of a frame, so sigma_F was the top strip's noise; a 1080 line frame's places now
    # reach from its top rows to its bottom ones
    shape, radius = (1080, 1920), 2
    nothing = np.zeros(shape)
    places = stars.empty_places(shape, radius, nothing, nothing.astype(bool))
    clear = clear_lattice(shape, radius, nothing, nothing.astype(bool))
    assert len(places) == stars.EMPTY_APERTURES and len(set(places)) == len(places)
    assert places[0] == clear[0] and places[-1] == clear[-1]
    rows = {y for _, y in places}
    assert min(rows) == clear[0][1] and max(rows) == clear[-1][1] and len(rows) > 20


@settings(max_examples=60, deadline=None)
@given(height=st.integers(40, 700), width=st.integers(40, 700), radius=st.integers(1, stars.FOOTPRINT_MAX_PX),
       blots=st.lists(st.tuples(st.floats(0, 1), st.floats(0, 1), st.integers(1, 60)), max_size=6), clip_one=st.booleans())
def test_the_empty_places_are_every_clear_lattice_point_or_sixty_taken_evenly_from_them(height, width, radius, blots,
                                                                                        clip_one) -> None:
    # All of the clear points where there are no more than sixty; otherwise sixty of them, the first and the last
    # among them, in row order, never one twice, each step along the clear points the same to within one
    footprint, clipped = np.zeros((height, width)), np.zeros((height, width), bool)
    for k, (fy, fx, side) in enumerate(blots):
        y, x = int(fy * (height - 1)), int(fx * (width - 1))
        target = clipped if clip_one and k == 0 else footprint
        target[max(0, y - side):y + side, max(0, x - side):x + side] = 1
    places = stars.empty_places((height, width), radius, footprint, clipped)
    clear = clear_lattice((height, width), radius, footprint, clipped)
    # Guard 1's own rule keeps the first sixty in row order (QUESTION-3)
    assert stars.first_empty_places((height, width), radius, footprint, clipped) == clear[:stars.EMPTY_APERTURES]
    if len(clear) <= stars.EMPTY_APERTURES:
        assert places == clear
        return
    assert len(places) == stars.EMPTY_APERTURES
    index = [clear.index(place) for place in places]
    assert index[0] == 0 and index[-1] == len(clear) - 1
    steps = np.diff(index)
    even = (len(clear) - 1) / (stars.EMPTY_APERTURES - 1)
    assert steps.min() >= math.floor(even) and steps.max() <= math.ceil(even) and steps.min() >= 1, steps


def test_the_empty_aperture_noise_of_one_or_two_places_is_their_mad() -> None:
    # sigma_F is 1.4826 x MAD of the sums at the places found: one place gives 0, two give half their difference;
    # restore-4's gate must not compare a flux change with either (design section 7), nor with the NaN of none.
    # At radius 2 the margin is 9 and the lattice step 19: a 37 px side holds one place, 56 px two.
    def frame(height: int, width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image = np.zeros((height, width))
        image[8:11, 8:11] = 1.0  # a patch at the first place only, so two places' sums differ
        return image, np.zeros((height, width)), np.zeros((height, width), bool)

    image, footprint, clipped = frame(37, 37)
    assert stars.empty_places(image.shape, 2, footprint, clipped) == [(9, 9)]
    assert stars.empty_aperture_sigma(image, 2, footprint, clipped) == 0.0
    image, footprint, clipped = frame(37, 56)
    two = stars.empty_places(image.shape, 2, footprint, clipped)
    sums = [stars.aperture_flux(image, x, y, 2) for x, y in two]
    assert two == [(9, 9), (28, 9)] and sums[0] != sums[1]
    assert stars.empty_aperture_sigma(image, 2, footprint, clipped) == pytest.approx(
        stars.MAD_TO_SIGMA * abs(sums[0] - sums[1]) / 2, rel=1e-12)


def test_the_empty_aperture_noise_is_the_mad_about_the_sums_own_median() -> None:
    # A faint disc of a different brightness at each place, so the sums' median is far from zero: the MAD is about
    # that median, as written out here
    shape, radius = (400, 400), 3
    nothing = np.zeros(shape)
    places = stars.empty_places(shape, radius, nothing, nothing.astype(bool))
    image = np.zeros(shape)
    yy, xx = np.mgrid[0:400, 0:400]
    for k, (x, y) in enumerate(places):
        image[np.hypot(xx - x, yy - y) <= 1.5] = 0.01 * (1 + k % 7)
    sums = np.array([stars.aperture_flux(image, x, y, radius) for x, y in places])
    assert np.median(sums) > 0.05
    expected = stars.MAD_TO_SIGMA * float(np.median(np.abs(sums - np.median(sums))))
    assert stars.empty_aperture_sigma(image, radius, nothing, nothing.astype(bool)) == pytest.approx(expected, rel=1e-12)


def test_the_find_stars_parameters_come_in_the_contracts_table_order() -> None:
    table = [row["name"] for row in contracts.STEP_PARAMETERS if "find_stars" in row["steps"]]
    names = [row["name"] for row in stars.parameters()]
    assert names == [name for name in table if name in names]


def _add_star(plane: np.ndarray, x_px: float, y_px: float, amp: float, sigma: float = 1.2) -> None:
    h, w = plane.shape
    yy, xx = np.mgrid[max(0, int(y_px) - 6) : min(h, int(y_px) + 7), max(0, int(x_px) - 6) : min(w, int(x_px) + 7)]
    plane[max(0, int(y_px) - 6) : min(h, int(y_px) + 7), max(0, int(x_px) - 6) : min(w, int(x_px) + 7)] += amp * np.exp(
        -((xx - x_px) ** 2 + (yy - y_px) ** 2) / (2.0 * sigma * sigma)
    )


def test_tile_margin_peaks_with_subpixel_centroids_past_half_pixel_are_confirmed_on_single_and_overlapping_tiles() -> None:
    rng = np.random.default_rng(104)
    single = 0.02 + 1e-4 * rng.standard_normal((256, 256))
    # Four margin stars on a 256x256 single-tile frame whose peaks are at 4 / 251 while 5x5 centroids round to 3 or 252
    _add_star(single, 4.0, 128.0, 0.05)
    single[126, 2] += 0.08
    single[130, 2] += 0.08
    _add_star(single, 251.0, 128.0, 0.05)
    single[126, 253] += 0.08
    single[130, 253] += 0.08
    _add_star(single, 128.0, 4.0, 0.05)
    single[2, 126] += 0.08
    single[2, 130] += 0.08
    _add_star(single, 128.0, 251.0, 0.05)
    single[253, 126] += 0.08
    single[253, 130] += 0.08

    clipped_single = np.zeros(single.shape, dtype=bool)
    pts, left_out = stars.find_points(single, stars.CONFIRM_THRESHOLD_SNR, clipped_single)
    assert len(pts) == 4 and left_out == 0
    assert any(p.x_px < 3.5 for p in pts) and any(p.x_px > 251.5 for p in pts)
    assert any(p.y_px < 3.5 for p in pts) and any(p.y_px > 251.5 for p in pts)
    assert stars.confirm(pts, single) == [True, True, True, True]
    field = stars.find_stars(single, peak=1.0)
    assert field.found == 4 and field.confirmed == 4
    for s in field.stars:
        assert field.footprint[int(round(s.y_px)), int(round(s.x_px))] == pytest.approx(1.0, abs=1e-12)

    # Multi-tile 256x480 frame: Tile 0 is [0, 256), Tile 1 is [224, 480).
    # (a) Seam star at x ~ 251.6 where Tile 1 has higher noise so _merged keeps Tile 0's detection.
    # (b) Variant shift across Tile 1's 4-px left border: original star at x = 227.3 (inside Tile 0, < 4 px into Tile 1),
    #     variant star nudged to x = 228.1 with tile = 1.
    multi = 0.02 + 1e-4 * rng.standard_normal((256, 480))
    multi[:, 260:] += 8e-4 * rng.standard_normal((256, 220))  # raises Tile 1's sigma_m above Tile 0's
    _add_star(multi, 251.0, 96.0, 0.05)
    multi[94, 253] += 0.08
    multi[98, 253] += 0.08
    _add_star(multi, 227.3, 180.0, 0.05)

    multi_field = stars.find_stars(multi, peak=1.0)
    assert multi_field.found == 2 and multi_field.confirmed == 2

    variant_shifted = [
        stars.Point(x_px=228.1, y_px=180.0, snr=12.0, tile=1),
        stars.Point(x_px=240.0, y_px=60.0, snr=12.0, tile=1),  # painted only in variant: must remain unconfirmed
    ]
    assert stars.confirm(variant_shifted, multi) == [True, False]

    # Half-open tile membership [x0, x1) x [y0, y1) at every edge of a 480x480 2x2 tile grid
    seen_tiles: list[tuple[int, int]] = []

    def fake_verify(part: np.ndarray, places: list[tuple[float, float]], noise_floor: float):
        seen_tiles.append((part.shape[0], len(places)))
        return []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stars, "verify_candidates_on_original_pixels", fake_verify)
        patch.setattr(stars, "matched_noise", lambda part: 1.0)
        # x = 224.0, y = 224.0 enters all 4 tiles; x = 223.9, y = 223.9 enters only Tile 0; x = 256.0 enters only right/bottom tiles
        edge_pts = [
            stars.Point(224.0, 224.0, 8.0, 0),
            stars.Point(223.9, 223.9, 8.0, 0),
            stars.Point(256.0, 256.0, 8.0, 3),
            stars.Point(480.0, 20.0, 8.0, 1),
            stars.Point(20.0, 480.0, 8.0, 2),
            stars.Point(-0.1, 20.0, 8.0, 0),
            stars.Point(20.0, -0.1, 8.0, 0),
        ]
        stars.confirm(edge_pts, np.zeros((480, 480)))
    assert seen_tiles == [(256, 2), (256, 1), (256, 1), (256, 2)]


def _merged_by_every_pair(points: list[stars.Point]) -> list[stars.Point]:
    kept: list[stars.Point] = []
    for point in sorted(points, key=lambda p: -p.snr):
        if all(math.hypot(point.x_px - other.x_px, point.y_px - other.y_px) > stars.MERGE_RADIUS_PX for other in kept):
            kept.append(point)
    return kept


@settings(max_examples=60, deadline=None)
@given(seed=st.integers(0, 2**16), count=st.integers(0, 400), spread=st.floats(2.0, 60.0))
def test_merging_by_grid_keeps_the_points_comparing_every_pair_keeps(seed: int, count: int, spread: float) -> None:
    # Clustered points, many within the merge radius of another, ties in snr among them: the grid keeps the same
    # points in the same order as comparing each with every kept one (the restore speed unit)
    rng = np.random.default_rng(seed)
    centres = rng.uniform(0, spread, (max(1, count // 4), 2))
    places = centres[rng.integers(0, len(centres), count)] + rng.normal(0, 1.2, (count, 2))
    snrs = rng.choice([5.0, 6.0, 7.5, 9.0, 12.0], count)
    points = [stars.Point(float(x), float(y), float(s), 0) for (x, y), s in zip(places, snrs)]
    assert stars._merged(points) == _merged_by_every_pair(points)


@settings(max_examples=60, deadline=None)
@given(height=st.integers(1, 900), width=st.integers(1, 900), seed=st.integers(0, 2**16))
def test_the_tiles_holding_a_point_are_found_as_testing_each_tile_finds_them(height: int, width: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    grid = stars.tiles(height, width)
    xs = np.concatenate([rng.uniform(0, width, 50), rng.integers(0, width, 20).astype(np.float64)])
    ys = np.concatenate([rng.uniform(0, height, 50), rng.integers(0, height, 20).astype(np.float64)])
    rows0, rows1, columns0, columns1, columns = stars.tile_ranges(height, width, xs, ys)
    first = stars.first_tiles(height, width, xs, ys)
    for i, (x, y) in enumerate(zip(xs, ys)):
        holding = [k for k, t in enumerate(grid) if t.x0 <= x < t.x1 and t.y0 <= y < t.y1]
        found = sorted(int(r * columns + c) for r in range(rows0[i], rows1[i] + 1) for c in range(columns0[i], columns1[i] + 1))
        assert found == holding and int(first[i]) == holding[0], (x, y, height, width)


def test_empty_places_from_a_clearance_measured_once_are_the_places_measured_for_each_radius() -> None:
    # restore-7s: a frame's flux check asks for empty places at every aperture radius of its stars; the clearance from
    # its footprints and clipped regions does not depend on the radius, so it is measured once and handed to each
    rng = np.random.default_rng(61)
    footprint = np.zeros((300, 360))
    for x, y in rng.uniform(20, 280, (40, 2)):
        footprint[int(y) - 3:int(y) + 4, int(x) - 3:int(x) + 4] = 0.5
    clipped = np.zeros((300, 360), bool)
    clipped[40:60, 300:330] = True
    clearance = stars.clearance_map(footprint, clipped)
    for radius in range(1, stars.FOOTPRINT_MAX_PX + 1):
        assert stars.empty_places((300, 360), radius, footprint, clipped, clearance) == \
            stars.empty_places((300, 360), radius, footprint, clipped), radius
    nothing = np.zeros((50, 50))
    assert np.all(np.isinf(stars.clearance_map(nothing, nothing.astype(bool))))
