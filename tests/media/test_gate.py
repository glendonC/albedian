"""The integrity gate and the dissolve passes (`services/media/gate.py`; design section 7; REQ-RST-6.3 to 6.9,
6.12; P-RST-11, P-RST-13).

Covers, on synthetic frames with known truth:
- The gate's statistic: the luma less its B3 smoothing at step 1, the first wavelet scale.
- The noise floor: a star-shaped patch at the stated sensitivity reaches the variant threshold times the margin on a
  quiet sky, by the kernel's arithmetic, at every sub-pixel place.
- A point of the variant is a point the detector finds and the variant's own forced photometry verifies: a sharp
  edge, which the detector's bright-impulse rule passes, is not one.
- A point is confirmed by forced photometry on the original, under either statistic, or because its own samples are
  the original's, the lift undone, to within half a code; one changed sample of the nine undoes that.
- The flux check compares no star against an empty-aperture noise that is not a number or zero.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from scipy import ndimage

from services.media import gate, noise, stars
from services.science.detector import _build_gaussian_psf_kernel, detect_point_sources


def gaussian(shape: tuple[int, int], x: float, y: float, peak: float, sigma: float = 1.3) -> np.ndarray:
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float64)
    return peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def test_the_statistic_is_the_luma_less_its_b3_smoothing_at_step_one() -> None:
    luma = np.random.default_rng(1).uniform(0, 255, (40, 50))
    assert np.allclose(gate.statistic_image(luma), luma - noise.b3_smooth(luma, 1), rtol=0, atol=1e-12)


def test_the_response_per_code_is_the_kernels_least_answer_to_a_star_over_sub_pixel_places() -> None:
    kernel = _build_gaussian_psf_kernel()
    least = math.inf
    for dx in np.linspace(-0.5, 0.5, 11):
        for dy in np.linspace(-0.5, 0.5, 11):
            star = gate.statistic_image(gaussian((41, 41), 20 + dx, 20 + dy, 1.0, gate.STAR_SIGMA_PX))
            response = ndimage.convolve(star, kernel, mode="nearest")
            least = min(least, float(response[18:23, 18:23].max()))
    assert gate.response_per_code() == pytest.approx(least, rel=1e-9)


@settings(max_examples=12, deadline=None)
@given(dx=st.floats(-0.5, 0.5), dy=st.floats(-0.5, 0.5), seed=st.integers(0, 2**16))
@example(dx=0.5, dy=0.5, seed=1)
@example(dx=0.484375, dy=0.5, seed=1)
def test_a_star_at_the_stated_sensitivity_reaches_the_threshold_times_the_margin_on_a_quiet_sky(dx, dy, seed) -> None:
    rng = np.random.default_rng(seed)
    sky = 40.0 + 0.02 * rng.standard_normal((256, 256))
    x, y = 128 + dx, 128 + dy
    luma = sky + gaussian(sky.shape, x, y, gate.SENSITIVITY_PEAK_CODES)
    # Asked at the gate's own threshold: the detector picks its filter partly from the threshold it is given
    found = detect_point_sources(gate.statistic_image(luma), snr_threshold=gate.VARIANT_THRESHOLD_SNR, noise_floor=gate.noise_floor())
    snr = max((d.snr for d in found if math.hypot(d.x_px - x, d.y_px - y) <= stars.MATCH_RADIUS_PX), default=0.0)
    # Within a hundredth: the detector's own background takes a sliver of the star's light
    assert snr >= 0.99 * gate.VARIANT_THRESHOLD_SNR * gate.FLOOR_MARGIN


def test_a_sharp_edge_is_not_a_point_of_the_variant_though_the_detector_passes_it() -> None:
    # A bright square: its corners pass the detector's bright-impulse rule, never the variant's own forced photometry
    luma = np.full((256, 256), 30.0) + 0.02 * np.random.default_rng(2).standard_normal((256, 256))
    luma[100:140, 100:140] += 60.0
    clipped = np.zeros(luma.shape, dtype=bool)
    raw = detect_point_sources(gate.statistic_image(luma), snr_threshold=gate.VARIANT_THRESHOLD_SNR, noise_floor=gate.noise_floor())
    assert raw, "the control: the detector alone reports points on the square's edges"
    points, _ = gate.variant_points(luma, clipped)
    assert points == []


def test_a_star_is_a_point_of_the_variant_and_one_inside_a_clipped_region_is_left_out_and_counted() -> None:
    luma = np.full((256, 256), 30.0) + 0.02 * np.random.default_rng(3).standard_normal((256, 256))
    luma += gaussian(luma.shape, 60.3, 70.6, 20.0) + gaussian(luma.shape, 180.2, 190.4, 20.0)
    clipped = np.zeros(luma.shape, dtype=bool)
    clipped[185:196, 175:186] = True
    points, left_out = gate.variant_points(luma, clipped)
    assert [(round(p.x_px), round(p.y_px)) for p in points] == [(60, 71)]
    assert left_out == 1


def original_frame(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """A dark noisy sky with two stars: its 8 bit RGB codes and their luma."""
    rng = np.random.default_rng(seed)
    luma = 20 + 3 * rng.standard_normal((256, 256)) + gaussian((256, 256), 64.4, 64.7, 60) + gaussian((256, 256), 190.2, 150.6, 40)
    codes = np.clip(np.round(np.repeat(luma[..., None], 3, axis=-1)), 0, 255).astype(np.uint8)
    return codes, stars.luma_codes(codes)


def test_a_point_on_one_of_the_originals_stars_is_confirmed_and_one_elsewhere_is_not() -> None:
    codes, luma = original_frame(4)
    field = gate.original_stars(codes, 255)
    assert {(round(s.x_px), round(s.y_px)) for s in field.stars} >= {(64, 65), (190, 151)}
    original = gate.Original.of(field)
    places = [stars.Point(64.4 + 2.0, 64.7, 0.0, 0), stars.Point(120.0, 200.0, 0.0, 0), stars.Point(64.4 + 3.0, 64.7, 0.0, 0)]
    # Their samples changed, so only the original's stars can confirm them, within the match radius
    expected = np.full((256, 256, 3), 1000.0)
    assert gate.confirmed(places, original, expected + 500.0, expected) == [True, False, False]


def test_a_point_whose_own_samples_are_the_originals_as_stored_is_confirmed_and_one_changed_sample_undoes_it() -> None:
    codes, _ = original_frame(5)
    original = gate.Original.of(gate.original_stars(codes, 255))
    place = [stars.Point(120.0, 200.0, 0.0, 0)]
    expected = np.random.default_rng(8).integers(0, 65536, (256, 256, 3)).astype(np.float64)
    within = expected + gate.UNCHANGED_TOLERANCE_CODES16  # the store's rounding, not a change
    assert gate.confirmed(place, original, within, expected) == [True]
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            changed = within.copy()
            changed[200 + dy, 120 + dx, 1] += 1.0  # past one 16 bit code in one channel of one of the nine
            assert gate.confirmed(place, original, changed, expected) == [False], (dx, dy)
    beyond = within.copy()
    beyond[198, 120] += 3000.0  # outside the nine samples: still the original's own point
    assert gate.confirmed(place, original, beyond, expected) == [True]


def test_a_painted_star_under_a_strong_lift_changes_the_stored_codes_past_the_tolerance() -> None:
    # On the original's code scale a 4-code star under a gain of 64 at black moves a sample by under half a code;
    # in the stored codes it moves it by hundreds of 16 bit codes, so the unchanged rule cannot let it through
    peak16 = gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT
    nearest = peak16 * math.exp(-(0.5**2 + 0.5**2) / (2 * gate.STAR_SIGMA_PX**2))
    assert nearest > 100 * gate.UNCHANGED_TOLERANCE_CODES16


@pytest.mark.parametrize("places", [0, 1])
def test_a_star_is_not_measured_against_an_empty_aperture_noise_of_no_places_or_one(places: int) -> None:
    # No place gives sigma_F not a number, which would never reject; one gives 0, which rejects every change
    # (restore-3-v2 report, items 1 and 2): neither is compared against
    image = np.random.default_rng(6).standard_normal((64, 64))
    footprint = np.ones((64, 64))
    if places:
        footprint[15:42, 15:42] = 0.0  # one lattice point, (28, 28), lies 12 px clear of every footprint
    clipped = np.zeros((64, 64), dtype=bool)
    star = stars.Star(50.0, 50.0, 10.0, 2)
    assert len(stars.empty_places((64, 64), star.radius_px, footprint, clipped)) == places
    assert gate.flux_change_sigma(star, image, image + 1.0, footprint, clipped) is None


def test_a_clipped_star_keeps_its_halo_as_the_original_recorded_it() -> None:
    # A star clipped at the peak code: its core is left out of the gate, and its halo, measured outward from the
    # core's own extent, is kept, so the filter neither darkens it nor leaves a seam the gate would flag
    luma = 30 + 0.5 * np.random.default_rng(9).standard_normal((128, 128)) + gaussian((128, 128), 64.0, 64.0, 600.0, 3.0)
    codes = np.clip(np.round(np.repeat(luma[..., None], 3, axis=-1)), 0, 255).astype(np.uint8)
    discs = gate.bloom_discs(codes, 255)
    assert len(discs) == 1
    disc = discs[0]
    assert (round(disc.x_px), round(disc.y_px)) == (64, 64)
    core_extent = math.ceil(np.hypot(*np.nonzero((codes == 255).all(-1)) - np.array([[64], [64]])).max())
    assert disc.radius_px > core_extent
    # Where the halo has fallen to the noise the disc ends: the ring rule's own test
    yy, xx = np.mgrid[0:128, 0:128]
    ring = luma[(np.hypot(xx - 64, yy - 64) >= disc.radius_px - 0.5) & (np.hypot(xx - 64, yy - 64) < disc.radius_px + 0.5)]
    assert np.median(ring) - 30 <= 1.5


def test_a_bright_light_is_kept_and_a_sky_gradient_is_not() -> None:
    from services.media import noise as noise_module

    rng = np.random.default_rng(10)
    gradient = np.linspace(0.002, 0.02, 256)[:, None] * np.ones((1, 256))  # a sky brightening toward its horizon
    luminance = gradient + 2e-4 * rng.standard_normal((256, 256))
    luminance += gaussian((256, 256), 180.0, 60.0, 0.5, 4.0)  # a lamp
    frame = np.repeat(luminance[..., None], 3, axis=-1)
    codes = np.clip(np.round(frame * 65535), 0, 65535).astype(np.uint16)
    measurement = noise_module.measure_noise(frame, codes, np.zeros((256, 256)), np.zeros((256, 256), bool),
                                             encode_slope=lambda x: np.ones_like(x), code_steps=65535)
    assert measurement.reason is None
    kept = gate.bright_regions(luminance, measurement)
    assert kept[60, 180] == 1.0
    assert kept[250, 20] == 0.0 and kept[5, 20] == 0.0  # neither end of the gradient
    assert float(kept.mean()) < 0.02


def test_the_first_scale_is_smoothed_outside_the_kept_cores_and_never_inside() -> None:
    rng = np.random.default_rng(11)
    working = rng.uniform(0.01, 0.02, (64, 64, 3))
    weight = np.zeros((64, 64))
    weight[20:40, 20:40] = 1.0
    field = stars.StarField((), weight, np.zeros((64, 64), bool), 0, 0, 0)
    out = gate.smooth_first_scale(working, gate.Kept(field, weight))
    assert np.array_equal(out[20:40, 20:40], working[20:40, 20:40])
    outside = (weight == 0)
    assert np.std(out[outside]) < np.std(working[outside])
    # Every output sample is an average of input samples with positive weights (REQ-RST-5.2)
    assert out.min() >= working.min() - 1e-15 and out.max() <= working.max() + 1e-15


def test_a_dissolve_never_touches_a_kept_core_and_grows_the_kept_region_over_a_point_beside_it() -> None:
    rng = np.random.default_rng(12)
    linear = 0.01 + 0.001 * rng.standard_normal((256, 256, 3))
    working = np.full((256, 256, 3), 0.01)
    weight = np.zeros((256, 256))
    weight[100:130, 100:130] = 1.0
    working[100:130, 100:130] = linear[100:130, 100:130]
    # Two bright points the original does not hold: one just outside the core, one far from it
    for x, y in ((132.0, 115.0), (40.0, 40.0)):
        working += gaussian((256, 256), x, y, 0.02)[..., None]
    field = stars.StarField((), weight, np.zeros((256, 256), bool), 0, 0, 0)
    kept = gate.Kept(field, weight)
    codes8 = np.clip(np.round(linear * 255 * 20), 0, 255).astype(np.uint8)
    original = gate.Original.of(gate.original_stars(codes8, 255))
    to_stored = lambda light: np.clip(light, 0, 1) * 65535
    to_codes = lambda light: np.clip(light, 0, 1) * 255
    result = gate.dissolve(working, linear=linear, to_stored=to_stored, to_original_codes=to_codes, original=original, kept=kept)
    assert result.per_pass and result.per_pass[0] >= 2
    assert np.array_equal(result.working[100:130, 100:130], working[100:130, 100:130])
    # Beside the core the point took the original's own samples; far from it, the smoothed fill
    assert np.allclose(result.working[115, 132], linear[115, 132])
    assert not np.allclose(result.working[40, 40], linear[40, 40])
    assert result.working[40, 40, 1] < working[40, 40, 1]


def test_a_pass_that_looks_only_in_the_changed_tiles_ends_where_looking_everywhere_ends(monkeypatch) -> None:
    rng = np.random.default_rng(13)
    linear = 0.01 + 0.0005 * rng.standard_normal((600, 600, 3))
    working = np.full((600, 600, 3), 0.01)
    for x, y in ((50.0, 60.0), (300.0, 310.0), (530.0, 90.0), (120.0, 500.0)):
        working += gaussian((600, 600), x, y, 0.01)[..., None]
    field = stars.StarField((), np.zeros((600, 600)), np.zeros((600, 600), bool), 0, 0, 0)
    kept = gate.Kept(field, np.zeros((600, 600)))
    codes8 = np.clip(np.round(linear * 255 * 20), 0, 255).astype(np.uint8)
    original = gate.Original.of(gate.original_stars(codes8, 255))
    arguments = dict(linear=linear, to_stored=lambda light: np.clip(light, 0, 1) * 65535,
                     to_original_codes=lambda light: np.clip(light, 0, 1) * 255, original=original, kept=kept)
    restricted = gate.dissolve(working, **arguments)
    looked = []
    everywhere = gate.variant_points

    def every_tile(*args, only_tiles=None, **kwargs):
        looked.append(only_tiles)
        return everywhere(*args, **kwargs)

    monkeypatch.setattr(gate, "variant_points", every_tile)
    full = gate.dissolve(working, **arguments)
    assert restricted.per_pass == full.per_pass and restricted.per_pass
    assert np.array_equal(restricted.working, full.working)
    # The control: after the first pass the product looked in fewer tiles than the frame has
    assert looked[0] is None and any(tiles is not None and len(tiles) < len(stars.tiles(600, 600)) for tiles in looked[1:])


def test_p_rst_12_a_star_kept_passes_the_flux_check_and_one_smoothed_or_painted_does_not() -> None:
    rng = np.random.default_rng(14)
    original = 0.01 + 0.0004 * rng.standard_normal((200, 200)) + gaussian((200, 200), 100.0, 100.0, 0.05)
    star = stars.Star(100.0, 100.0, 50.0, 4)
    footprint = stars.footprint_map((200, 200), [star])
    field = stars.StarField((star,), footprint, np.zeros((200, 200), bool), 1, 1, 0)
    # Kept as recorded, with the sky around it smoothed: the star's own light is unchanged
    smoothed_sky = ndimage.gaussian_filter(original, 2.0)
    kept = footprint * original + (1 - footprint) * smoothed_sky
    measured, outside, worst, ratio = gate.flux_check(field, original, kept, footprint)
    assert (measured, outside) == (1, 0) and worst < 0.5 and ratio == pytest.approx(1.0, abs=0.02)
    # The mutant of P-RST-12: footprints switched off, the star smoothed with its sky
    measured, outside, worst, _ = gate.flux_check(field, original, smoothed_sky, footprint)
    assert outside == 1 and worst > gate.FLUX_TOLERANCE_SIGMA
    # A star painted onto the kept star brightens it past the tolerance
    painted = kept + gaussian((200, 200), 101.0, 100.0, 0.01)
    assert gate.flux_check(field, original, painted, footprint)[1] == 1


def test_a_pass_dissolves_a_point_the_detector_reports_only_at_the_gates_threshold(monkeypatch) -> None:
    # The detector picks its filter partly from the threshold it is given: a point it reports at the gate's
    # threshold may be missing at the dissolve's lower one, and a pass must dissolve it all the same
    linear = np.full((256, 256, 3), 0.01)
    working = linear + gaussian((256, 256), 128.0, 128.0, 0.01)[..., None]
    field = stars.StarField((), np.zeros((256, 256)), np.zeros((256, 256), bool), 0, 0, 0)
    kept = gate.Kept(field, np.zeros((256, 256)))
    real = gate.variant_points

    def only_at_the_gate(luma, clipped, *, only_tiles=None, threshold=gate.VARIANT_THRESHOLD_SNR):
        points, left = real(luma, clipped, only_tiles=only_tiles, threshold=threshold)
        return (points if threshold == gate.VARIANT_THRESHOLD_SNR else []), left

    monkeypatch.setattr(gate, "variant_points", only_at_the_gate)
    result = gate.dissolve(working, linear=linear, to_stored=lambda light: np.clip(light, 0, 1) * 65535,
                           to_original_codes=lambda light: np.clip(light, 0, 1) * 255,
                           original=gate.Original(np.zeros((0, 2))), kept=kept)
    assert result.per_pass and result.per_pass[0] == 1
    assert result.working[128, 128, 1] < working[128, 128, 1]


def gate_scene() -> tuple:
    """A sky with one star, its kept footprint, and the original as stored (16 bit codes of the light, no lift)."""
    rng = np.random.default_rng(15)
    light = 0.02 + 0.0005 * rng.standard_normal((256, 256)) + gaussian((256, 256), 100.0, 100.0, 0.3)
    star = stars.Star(100.0, 100.0, 80.0, 5)
    footprint = stars.footprint_map((256, 256), [star])
    field = stars.StarField((star,), footprint, np.zeros((256, 256), bool), 1, 1, 0)
    expected = np.repeat(np.round(light * 65535)[..., None], 3, axis=-1)
    return light, field, gate.Kept(field, footprint), gate.Original.of(field), expected


def test_the_gate_refuses_a_changed_star_for_its_flux_and_a_painted_point_for_itself() -> None:
    light, field, kept, original, expected = gate_scene()
    honest = gate.run_gate(expected.astype(np.uint16), original=original, kept=kept, expected=expected,
                           original_luminance=light, variant_luminance=light)
    assert honest.record["passed"] and honest.rejection is None
    # The star brightened in place: on a star, so no point is unconfirmed, but its flux is not the original's
    brighter = light + gaussian((256, 256), 100.0, 100.0, 0.05)
    stored = np.repeat(np.round(brighter * 65535)[..., None], 3, axis=-1)
    flux = gate.run_gate(stored.astype(np.uint16), original=original, kept=kept, expected=expected,
                         original_luminance=light, variant_luminance=brighter)
    assert flux.record["unconfirmedPointCount"] == 0 and flux.record["starsOutsideFluxTolerance"] == 1
    assert not flux.record["passed"] and flux.rejection == "star_flux_changed"
    # A star painted away from every star: an unconfirmed point, refused for that first
    painted = light + gaussian((256, 256), 180.0, 60.0, 0.05)
    stored = np.repeat(np.round(painted * 65535)[..., None], 3, axis=-1)
    point = gate.run_gate(stored.astype(np.uint16), original=original, kept=kept, expected=expected,
                          original_luminance=light, variant_luminance=painted)
    assert point.record["unconfirmedPointCount"] >= 1 and point.rejection == "unconfirmed_points"


def test_a_smoothed_disc_that_reaches_a_kept_core_stops_at_it() -> None:
    rng = np.random.default_rng(16)
    linear = 0.01 + 0.001 * rng.standard_normal((256, 256, 3))
    working = np.full((256, 256, 3), 0.01)
    weight = np.zeros((256, 256))
    weight[100:130, 100:130] = 1.0
    working[100:130, 100:130] = linear[100:130, 100:130]
    # A point 6 px from the core: farther than NEAR_CORE_PX, so smoothed, its disc and edge reaching into the core
    working += gaussian((256, 256), 136.0, 115.0, 0.02)[..., None]
    field = stars.StarField((), weight, np.zeros((256, 256), bool), 0, 0, 0)
    result = gate.dissolve(working, linear=linear, to_stored=lambda light: np.clip(light, 0, 1) * 65535,
                           to_original_codes=lambda light: np.clip(light, 0, 1) * 255,
                           original=gate.Original(np.zeros((0, 2))), kept=gate.Kept(field, weight))
    assert result.per_pass and np.array_equal(result.working[100:130, 100:130], working[100:130, 100:130])
    assert result.working[115, 136, 1] < working[115, 136, 1]


def test_the_next_pass_looks_in_every_tile_a_pass_changed(monkeypatch) -> None:
    linear = np.full((600, 600, 3), 0.01)
    working = linear + gaussian((600, 600), 300.0, 310.0, 0.01)[..., None]
    field = stars.StarField((), np.zeros((600, 600)), np.zeros((600, 600), bool), 0, 0, 0)
    looked = []
    real = gate.variant_points

    def spy(*args, only_tiles=None, **kwargs):
        looked.append(None if only_tiles is None else set(only_tiles))
        return real(*args, only_tiles=only_tiles, **kwargs)

    monkeypatch.setattr(gate, "variant_points", spy)
    gate.dissolve(working, linear=linear, to_stored=lambda light: np.clip(light, 0, 1) * 65535,
                  to_original_codes=lambda light: np.clip(light, 0, 1) * 255, original=gate.Original(np.zeros((0, 2))),
                  kept=gate.Kept(field, np.zeros((600, 600))))
    holding = {i for i, t in enumerate(stars.tiles(600, 600)) if t.x0 <= 300 < t.x1 and t.y0 <= 310 < t.y1}
    assert looked[0] is None and looked[2] is not None and holding <= looked[2]


def test_light_just_over_the_bright_rule_is_kept_and_light_under_it_is_not() -> None:
    from services.media import noise as noise_module

    rng = np.random.default_rng(17)
    sigma = 2e-4
    luminance = 0.01 + sigma * rng.standard_normal((256, 256))
    frame = np.repeat(luminance[..., None], 3, axis=-1)
    codes = np.clip(np.round(frame * 65535), 0, 65535).astype(np.uint16)
    measurement = noise_module.measure_noise(frame, codes, np.zeros((256, 256)), np.zeros((256, 256), bool),
                                             encode_slope=lambda x: np.ones_like(x), code_steps=65535)
    pixel = float(np.interp(0.01, measurement.intensities, dict(measurement.sigma)["luma"])) / noise_module.W1_WHITE_SIGMA
    # Flat squares of light, so smoothing one level leaves their middles as they are
    lit = luminance.copy()
    lit[40:60, 40:60] += 2.0 * gate.BRIGHT_SKY_NOISES * pixel
    lit[180:200, 180:200] += 0.5 * gate.BRIGHT_SKY_NOISES * pixel
    kept = gate.bright_regions(lit, measurement)
    assert kept[50, 50] == 1.0 and kept[190, 190] == 0.0


def test_a_ring_ends_where_its_median_first_falls_within_one_sigma_of_the_far_sky() -> None:
    rng = np.random.default_rng(18)
    sky_sigma = 1.0
    yy, xx = np.mgrid[0:120, 0:120]
    distance = np.hypot(xx - 60.0, yy - 60.0)
    profile = 40.0 * np.exp(-distance / 2.5)  # a halo with a slow wing
    luma = 100 + sky_sigma * rng.standard_normal((120, 120)) + profile
    radius = gate.ring_radius(luma, 60.0, 60.0, gate.STAR_MIN_RADIUS_PX, stars.FOOTPRINT_MAX_PX)
    # Where the wing itself falls under one sigma: 40 exp(-r / 2.5) <= 1 at r >= 9.2
    assert radius in (9, 10)


def test_a_clipped_stars_bloom_is_inside_the_kept_regions() -> None:
    luma = 30 + 0.5 * np.random.default_rng(19).standard_normal((128, 128)) + gaussian((128, 128), 64.0, 64.0, 600.0, 3.0)
    codes = np.clip(np.round(np.repeat(luma[..., None], 3, axis=-1)), 0, 255).astype(np.uint8)
    field = stars.StarField((), np.zeros((128, 128)), stars.clipped_map(codes, 255), 0, 0, 0)
    kept = gate.kept_regions(field, codes, 255, luma / 255.0, None)
    disc = gate.bloom_discs(codes, 255)[0]
    assert kept.weight[64, 64 + disc.radius_px - 1] == 1.0 and kept.weight[64, 64 + disc.radius_px + stars.FOOTPRINT_EDGE_PX + 2] == 0.0


def test_a_hot_pixel_the_detector_reports_is_no_star_and_a_real_star_is() -> None:
    from services.science.detector import detect_point_sources

    rng = np.random.default_rng(20)
    luma = 30 + 2.0 * rng.standard_normal((256, 256)) + gaussian((256, 256), 60.3, 70.6, 80.0)
    luma[180, 190] += 120.0  # one sensor sample: no star's shape
    codes = np.clip(np.round(np.repeat(luma[..., None], 3, axis=-1)), 0, 255).astype(np.uint8)
    reported = [(round(d.x_px), round(d.y_px)) for d in detect_point_sources(stars.luma_codes(codes))]
    assert (190, 180) in reported, "the control: the detector's bright-impulse rule reports the hot pixel"
    field = gate.original_stars(codes, 255)
    places = {(round(s.x_px), round(s.y_px)) for s in field.stars}
    assert (60, 71) in places and (190, 180) not in places
    assert field.found > field.confirmed


def test_a_lamps_halo_is_kept_with_it_and_a_faint_patch_alone_is_not() -> None:
    from services.media import noise as noise_module

    rng = np.random.default_rng(21)
    luminance = 0.01 + 2e-4 * rng.standard_normal((256, 256))
    frame = np.repeat(luminance[..., None], 3, axis=-1)
    codes = np.clip(np.round(frame * 65535), 0, 65535).astype(np.uint16)
    measurement = noise_module.measure_noise(frame, codes, np.zeros((256, 256)), np.zeros((256, 256), bool),
                                             encode_slope=lambda x: np.ones_like(x), code_steps=65535)
    pixel = float(np.interp(0.01, measurement.intensities, dict(measurement.sigma)["luma"])) / noise_module.W1_WHITE_SIGMA
    between = 0.5 * (gate.BRIGHT_HALO_NOISES + gate.BRIGHT_SKY_NOISES) * pixel
    lit = luminance.copy()
    lit[60:80, 60:80] += 3 * gate.BRIGHT_SKY_NOISES * pixel  # a lamp
    lit[56:84, 80:110] += between  # its halo, joined to it, under the seed level and over the halo level
    lit[180:200, 180:200] += between  # the same light alone, joined to no lamp
    kept = gate.bright_regions(lit, measurement)
    assert kept[70, 70] == 1.0 and kept[70, 100] == 1.0
    assert kept[190, 190] == 0.0
