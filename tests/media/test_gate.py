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

from services.contracts import media as contracts
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


def test_a_passs_fill_takes_no_light_from_a_kept_regions_edge_and_leaves_the_edge_as_it_was() -> None:
    # A lit region kept up to its edge, and a point 4 px past its core, 2 px past its edge, beyond NEAR_CORE_PX: the
    # fill that replaces the point reads the sky around it, not the edge's light, and the edge keeps its samples
    # [restore-4e: on rp-b-03 a fill that took the edge's light in kept a point through three passes]
    star = stars.Star(128.0, 128.0, 90.0, 6)
    weight = stars.footprint_map((256, 256), [star])
    field = stars.StarField((), weight, np.zeros((256, 256), bool), 0, 0, 0)
    kept = gate.Kept(field, weight)
    linear = np.full((256, 256, 3), 0.01) + 0.2 * (weight > 0)[..., None]
    working = linear + gaussian((256, 256), 138.0, 128.0, 0.02)[..., None]
    result = gate.dissolve(working, linear=linear, to_stored=lambda l: np.clip(l, 0, 1) * 65535,
                           to_original_codes=lambda l: np.clip(l, 0, 1) * 255, original=gate.Original(np.zeros((0, 2))),
                           kept=kept)
    assert result.per_pass and result.per_pass[0] >= 1
    # The sky is 0.01 and the point 0.02 over it: filled from the sky around it, under half the point is left
    assert result.working[128, 138, 1] < 0.02, result.working[128, 138, 1]
    edge = (weight > 0) & (weight < 1)
    assert np.array_equal(result.working[edge], working[edge])


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

    def only_at_the_gate(luma, clipped, *, only_tiles=None, threshold=gate.VARIANT_THRESHOLD_SNR, kept=None):
        points, left = real(luma, clipped, only_tiles=only_tiles, threshold=threshold, kept=kept)
        return (points if threshold == gate.VARIANT_THRESHOLD_SNR else []), left

    monkeypatch.setattr(gate, "variant_points", only_at_the_gate)
    result = gate.dissolve(working, linear=linear, to_stored=lambda light: np.clip(light, 0, 1) * 65535,
                           to_original_codes=lambda light: np.clip(light, 0, 1) * 255,
                           original=gate.Original(np.zeros((0, 2))), kept=kept)
    assert result.per_pass and result.per_pass[0] == 1
    assert result.working[128, 128, 1] < working[128, 128, 1]


def test_the_passes_search_as_the_gate_does_with_the_kept_regions_left_out(monkeypatch) -> None:
    # A point the gate's search finds beside a kept region must be one the passes looked for, or an honest run ends
    # rejected: both read `search_statistic` with the same weights
    light, field, kept, original, expected = gate_scene()
    linear = np.repeat(light[..., None], 3, axis=-1)
    working = linear + gaussian((256, 256), 180.0, 60.0, 0.05)[..., None]
    seen = []
    real = gate.variant_points

    def noted(*args, **kwargs):
        seen.append(kwargs.get("kept"))
        return real(*args, **kwargs)

    monkeypatch.setattr(gate, "variant_points", noted)
    gate.dissolve(working, linear=linear, to_stored=lambda l: np.clip(l, 0, 1) * 65535,
                  to_original_codes=lambda l: np.clip(l, 0, 1) * 255, original=original, kept=kept)
    gate.run_gate(np.round(np.clip(linear, 0, 1) * 65535).astype(np.uint16), original=original, kept=kept,
                  expected=expected, original_luminance=light, variant_luminance=light)
    assert seen and all(given is kept for given in seen)


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
    # Stored as the job stores it: the kept edge the original blended with the stored sky around it (restore-4f)
    stored = gate.kept_samples(expected, expected, kept.weight).astype(np.uint16)
    honest = gate.run_gate(stored, original=original, kept=kept, expected=expected,
                           original_luminance=light, variant_luminance=light)
    assert honest.record["passed"] and honest.rejection is None
    # The star brightened in place: its kept core is not the original's, one place there (restore-4e), and its flux
    # is outside the tolerance too
    brighter = light + gaussian((256, 256), 100.0, 100.0, 0.05)
    stored = np.repeat(np.round(brighter * 65535)[..., None], 3, axis=-1)
    flux = gate.run_gate(stored.astype(np.uint16), original=original, kept=kept, expected=expected,
                         original_luminance=light, variant_luminance=brighter)
    assert flux.record["unconfirmedPointCount"] == 1 and flux.record["starsOutsideFluxTolerance"] == 1
    assert not flux.record["passed"] and flux.rejection == "unconfirmed_points"
    # With no kept region to compare (restore-4f makes a kept edge exact too), the same star is refused for its flux
    # alone: its point is the star's, confirmed by it
    soft = gate.Kept(field, np.zeros_like(kept.weight))
    flux = gate.run_gate(stored.astype(np.uint16), original=original, kept=soft, expected=expected,
                         original_luminance=light, variant_luminance=brighter)
    assert flux.record["unconfirmedPointCount"] == 0 and flux.record["starsOutsideFluxTolerance"] == 1
    assert not flux.record["passed"] and flux.rejection == "star_flux_changed"
    # A star painted away from every star: an unconfirmed point, refused for that first
    painted = light + gaussian((256, 256), 180.0, 60.0, 0.05)
    stored = np.repeat(np.round(painted * 65535)[..., None], 3, axis=-1)
    point = gate.run_gate(stored.astype(np.uint16), original=original, kept=kept, expected=expected,
                          original_luminance=light, variant_luminance=painted)
    assert point.record["unconfirmedPointCount"] >= 1 and point.rejection == "unconfirmed_points"


def test_a_kept_cores_sample_that_is_not_the_originals_is_an_unconfirmed_place() -> None:
    light, field, kept, original, expected = gate_scene()
    stored = expected.astype(np.uint16)
    assert gate.kept_changed(stored, expected, kept.core) == []
    # Inside the star's kept disc: one sample changed past the tolerance is one place, where it changed
    changed = stored.copy()
    changed[100, 103, 1] += 2
    places = gate.kept_changed(changed, expected, kept.core)
    assert [(p.x_px, p.y_px) for p in places] == [(103.0, 100.0)]
    # At the tolerance, or outside every kept core, it is none
    within = stored.copy()
    within[100, 103, 1] += 1
    outside = stored.copy()
    outside[200, 200, 0] += 500
    assert gate.kept_changed(within, expected, kept.core) == [] and gate.kept_changed(outside, expected, kept.core) == []
    # Two separate changes are two places; samples touching, a diagonal neighbour too, are one
    two = changed.copy()
    two[97, 97, 2] -= 3
    two[101, 104, 0] += 3
    assert len(gate.kept_changed(two, expected, kept.core)) == 2
    # The gate refuses it as a place the original does not confirm, with counts the contract takes
    outcome = gate.run_gate(changed, original=original, kept=kept, expected=expected, original_luminance=light,
                            variant_luminance=light)
    assert not outcome.record["passed"] and outcome.rejection == "unconfirmed_points"
    assert outcome.record["unconfirmedPointCount"] >= 1 and any(
        (p.x_px, p.y_px) == (103.0, 100.0) for p in outcome.unconfirmed)
    contracts.IntegrityGateResult.model_validate(outcome.record)


@settings(max_examples=80, deadline=None)
@given(radius=st.integers(gate.STAR_MIN_RADIUS_PX, 12), dx=st.floats(-0.5, 0.5), dy=st.floats(-0.5, 0.5),
       beyond=st.floats(0.0, stars.FOOTPRINT_EDGE_PX + 0.5), angle=st.floats(0.0, 2 * math.pi),
       ox=st.floats(-0.5, 0.5), oy=st.floats(-0.5, 0.5), scale=st.sampled_from([1.0, 0.5]))
@example(radius=2, dx=0.5, dy=0.5, beyond=stars.FOOTPRINT_EDGE_PX + 0.5, angle=math.pi / 4, ox=0.5, oy=0.5, scale=0.5)
def test_p_rst_11_a_star_painted_with_its_peak_on_a_sample_the_search_leaves_out_changes_a_core(
        radius: int, dx: float, dy: float, beyond: float, angle: float, ox: float, oy: float, scale: float) -> None:
    # The search leaves out every kept region, its core and its 2 px edge; a star painted anywhere whose peak
    # sample is one of those, at the stated sensitivity or half of it, changes a core sample past the tolerance, so
    # the exact check refuses what the search does not look at
    star = stars.Star(64.0 + dx, 64.0 + dy, 20.0, radius)
    kept = gate.Kept(stars.StarField((star,), np.zeros((128, 128)), np.zeros((128, 128), bool), 1, 1, 0),
                     stars.footprint_map((128, 128), [star]))
    x = star.x_px + (radius + beyond) * math.cos(angle) + ox
    y = star.y_px + (radius + beyond) * math.sin(angle) + oy
    if not kept.left_out[int(round(y)), int(round(x))]:
        return
    expected = np.full((128, 128, 3), 10000.0)
    stored = np.round(expected + gaussian((128, 128), x, y, scale * gate.SENSITIVITY_PEAK_CODES * gate.CODES_PER_8_BIT)[..., None])
    assert gate.kept_changed(stored.astype(np.uint16), expected, kept.core), (radius, beyond, angle, scale)


def test_the_search_leaves_out_every_kept_region_its_core_and_its_edge() -> None:
    star = stars.Star(64.0, 64.0, 20.0, 4)
    weight = stars.footprint_map((128, 128), [star])
    kept = gate.Kept(stars.StarField((star,), weight, np.zeros((128, 128), bool), 1, 1, 0), weight)
    assert np.array_equal(kept.left_out, weight > 0)
    assert kept.left_out[64, 64 + 4 + 1] and not kept.left_out[64, 64 + 4 + stars.FOOTPRINT_EDGE_PX]
    empty = gate.Kept(stars.StarField((), np.zeros((8, 8)), np.zeros((8, 8), bool), 0, 0, 0), np.zeros((8, 8)))
    assert not empty.left_out.any()


def test_the_search_statistic_leaves_kept_regions_out_and_is_the_gates_statistic_elsewhere() -> None:
    # A kept region bright up to its edge, as a bright region's or a clipped bloom's light is, on a noisy sky
    rng = np.random.default_rng(21)
    star = stars.Star(48.0, 48.0, 90.0, 6)
    weight = stars.footprint_map((96, 96), [star])
    luma = 20.0 + 0.3 * rng.standard_normal((96, 96)) + 80.0 * (weight > 0)
    plain, search = gate.statistic_image(luma), gate.search_statistic(luma, weight > 0)
    region = weight > 0
    # The B3 step-1 window is 5 x 5: a sample reads the region from 2 samples away along either axis
    reach = ndimage.distance_transform_cdt(~region, metric="chessboard")
    ring, far = (reach > 0) & (reach <= 2), reach > 2
    assert np.all(search[region] == 0.0)
    assert np.allclose(search[far], plain[far])
    # Beside the region the gate's statistic takes the region's light into its local mean: a negative lobe; the
    # search's local mean leaves it out, so the ring is the sky's own
    assert plain[ring].mean() < -1.0 and abs(search[ring].mean()) < 0.2
    assert np.array_equal(gate.search_statistic(luma, None), plain)
    assert np.array_equal(gate.search_statistic(luma, np.zeros((96, 96))), plain)


def test_a_kept_edge_is_the_original_blended_with_the_stored_sky_and_a_change_in_it_is_a_place() -> None:
    rng = np.random.default_rng(31)
    star = stars.Star(64.0, 64.0, 20.0, 4)
    weight = stars.footprint_map((128, 128), [star])
    expected = np.round(20000 + 300 * rng.standard_normal((128, 128, 3)))
    stored = np.round(20000 + 40 * rng.standard_normal((128, 128, 3)))
    must = gate.kept_samples(stored, expected, weight)
    core, edge, outside = weight >= 1, (weight > 0) & (weight < 1), weight == 0
    assert np.array_equal(must[core], expected[core]) and np.array_equal(must[outside], stored[outside])
    # An edge sample lies between the original's and the sky's, by its weight, and reads no kept sample
    fill = gate.smooth_without(stored, (weight > 0).astype(np.float64), gate.KEPT_EDGE_FILL_LEVELS)
    w = weight[edge][:, None]
    assert np.array_equal(must[edge], np.round(w * expected[edge] + (1 - w) * fill[edge]))
    changed_inside = stored.copy()
    changed_inside[64, 64] += 9999
    assert np.array_equal(gate.kept_samples(changed_inside, expected, weight)[edge], must[edge])
    # A file stored so is honest, exactly; one edge sample raised is one place there
    honest = must.astype(np.uint16)
    assert gate.kept_changed(honest, gate.kept_samples(honest, expected, weight), weight > 0) == []
    ys, xs = np.nonzero(edge)
    raised = honest.copy()
    raised[ys[0], xs[0]] += 2
    places = gate.kept_changed(raised, gate.kept_samples(raised, expected, weight), weight > 0)
    assert [(p.x_px, p.y_px) for p in places] == [(float(xs[0]), float(ys[0]))]


def test_a_pass_dissolves_a_raised_sample_the_original_does_not_hold() -> None:
    rng = np.random.default_rng(34)
    linear = np.full((256, 256, 3), 0.1) + 0.00002 * rng.standard_normal((256, 256, 3))
    working = linear.copy()
    working[100, 120] += 0.012  # about 3 codes of 255 in the stored file
    field = stars.StarField((), np.zeros((256, 256)), np.zeros((256, 256), bool), 0, 0, 0)
    result = gate.dissolve(working, linear=linear, to_stored=lambda l: np.clip(l, 0, 1) * 65535,
                           to_original_codes=lambda l: np.clip(l, 0, 1) * 255, original=gate.Original(np.zeros((0, 2))),
                           kept=gate.Kept(field, np.zeros((256, 256))))
    assert result.per_pass and result.per_pass[0] == 1
    assert result.working[100, 120, 1] - linear[100, 120, 1] < 0.003


def test_a_points_own_samples_are_the_originals_leaving_out_the_kept_samples_beside_it() -> None:
    expected = np.full((9, 9, 3), 1000.0)
    variant = expected.copy()
    variant[4, 5] += 300
    point = [stars.Point(4.0, 4.0, 9.0, 0)]
    assert gate.unchanged(point, variant, expected) == [False]
    kept = np.zeros((9, 9), bool)
    kept[4, 5] = True
    assert gate.unchanged(point, variant, expected, kept) == [True]
    assert gate.confirmed(point, gate.Original(np.zeros((0, 2))), variant, expected, kept) == [True]


def test_a_kept_edge_sample_the_fill_cannot_reach_is_the_originals_so_a_stored_file_checks_exactly() -> None:
    # An edge sample deep in a kept region, every free sample past the fill's reach: the fill keeps its own value
    # through every level, so its blend would read itself; it is the original's, as a core's [restore-4f: rp-b-01's
    # and rp-b-04's crops ended rejected for 1 and 6 such samples]
    weight = np.ones((80, 80))
    weight[:, :4] = 0.0
    weight[40, 40] = 0.5
    expected = np.full((80, 80, 3), 30000.0)
    working = np.full((80, 80, 3), 12345.0)
    must = gate.kept_samples(working, expected, weight)
    assert np.array_equal(must[40, 40], expected[40, 40])
    stored = must.astype(np.uint16)
    assert gate.kept_changed(stored, gate.kept_samples(stored, expected, weight), weight > 0) == []


@pytest.mark.parametrize("size", [1, 2, 3])
def test_a_raised_sample_or_small_cluster_is_a_place_and_an_edge_a_gradient_or_the_originals_own_is_not(size: int) -> None:
    rng = np.random.default_rng(32)
    luma = 40.0 + 0.2 * rng.standard_normal((96, 96))
    clipped, none = np.zeros((96, 96), bool), np.zeros((96, 96), bool)
    assert gate.raised_places(luma, luma, none, clipped) == []
    block = luma.copy()
    block[40:40 + size, 50:50 + size] += gate.RAISE_CODES + 0.8
    places = gate.raised_places(block, luma, none, clipped)
    assert len(places) == 1 and 50 <= places[0].x_px < 50 + size and 40 <= places[0].y_px < 40 + size
    # Under the threshold, in a clipped region, or no brighter than the original's own samples there, it is none
    low = luma.copy()
    low[40:40 + size, 50:50 + size] += gate.RAISE_CODES - 0.8
    assert gate.raised_places(low, luma, none, clipped) == []
    at_clip = clipped.copy()
    at_clip[40:40 + size, 50:50 + size] = True
    assert gate.raised_places(block, luma, none, at_clip) == []
    assert gate.raised_places(block, block, none, clipped) == []
    # A step and a steep gradient the original holds, its noise apart, are not raised: a step's ring is uneven, and
    # on a plane the ring's mean is the centre
    step = luma + 30.0 * (np.arange(96)[None, :] >= 48)
    noisy_step = step + 1.5 * rng.standard_normal((96, 96))
    assert gate.raised_places(step, noisy_step, none, clipped) == []
    slope = luma + 2.0 * np.arange(96)[None, :]
    assert gate.raised_places(slope, slope + 1.5 * rng.standard_normal((96, 96)), none, clipped) == []
    on_slope = slope.copy()
    on_slope[40:40 + size, 50:50 + size] += gate.RAISE_CODES + 0.8
    assert len(gate.raised_places(on_slope, slope, none, clipped)) == 1


def test_a_sample_within_2_px_of_the_frames_edge_is_not_compared() -> None:
    # Its ring would leave the frame, and a padded ring repeats the edge's samples; the frame's outer 2 px are left
    # out, as the detector leaves out its own border
    luma = np.full((40, 40), 30.0)
    for x, y in ((1, 20), (20, 0), (38, 20), (20, 39)):
        raised = luma.copy()
        raised[y, x] += 10.0
        assert gate.raised_places(raised, luma, np.zeros((40, 40), bool), np.zeros((40, 40), bool)) == [], (x, y)
    inside = luma.copy()
    inside[2, 20] += 10.0
    assert len(gate.raised_places(inside, luma, np.zeros((40, 40), bool), np.zeros((40, 40), bool))) == 1


def test_the_gate_and_the_passes_confirm_a_points_own_samples_with_the_kept_ones_left_out(monkeypatch) -> None:
    # A kept edge is stored as a blend, not the original's samples, so a point whose 3 x 3 reaches one would never be
    # the original's own: both the passes and the gate leave the kept samples out of that comparison [restore-4f: on
    # rp-b-01's and rp-b-05's crops such points ended unconfirmed once the edges were stored exactly]
    light, field, kept, original, expected = gate_scene()
    linear = np.repeat(light[..., None], 3, axis=-1)
    working = linear + gaussian((256, 256), 180.0, 60.0, 0.05)[..., None]
    seen = []
    real = gate.confirmed

    def noted(points, original_, variant, expected_, ignore=None):
        seen.append(ignore)
        return real(points, original_, variant, expected_, ignore)

    monkeypatch.setattr(gate, "confirmed", noted)
    gate.dissolve(working, linear=linear, to_stored=lambda l: np.clip(l, 0, 1) * 65535,
                  to_original_codes=lambda l: np.clip(l, 0, 1) * 255, original=original, kept=kept)
    gate.run_gate(gate.kept_samples(expected, expected, kept.weight).astype(np.uint16), original=original, kept=kept,
                  expected=expected, original_luminance=light, variant_luminance=light)
    assert seen and all(mask is kept.left_out for mask in seen)


@pytest.mark.parametrize("step", [(0, 2), (2, 0), (2, 2), (2, -2)])
@pytest.mark.parametrize("count", [2, 3])
def test_raised_samples_2_px_apart_do_not_hide_each_other(step: tuple[int, int], count: int) -> None:
    # Each lies on the other's ring: counted in that ring's unevenness, a pair 2 px apart in a row passed at 4 codes
    # in 7 of 10 trials (AUDIT-B's restore-4f verdict); a line of three passed at 32 codes in 2 of 10
    rng = np.random.default_rng(36)
    luma = 40.0 + 0.2 * rng.standard_normal((96, 96))
    none = np.zeros((96, 96), bool)
    for codes in (gate.SENSITIVITY_PEAK_CODES, 4 * gate.SENSITIVITY_PEAK_CODES, 8 * gate.SENSITIVITY_PEAK_CODES):
        raised = luma.copy()
        for k in range(count):
            raised[40 + k * step[0], 40 + k * step[1]] += codes
        places = gate.raised_places(raised, luma, none, none)
        assert len(places) == count, (step, count, codes, places)


def test_a_dense_pattern_of_raised_samples_is_raised_although_its_rings_hold_little_else() -> None:
    # Every second sample raised over a patch: each ring is mostly the others, so a ring left without them is too
    # sparse to read; such a sample is a raise all the same
    luma = np.full((64, 64), 30.0)
    raised = luma.copy()
    raised[20:44:2, 20:44:2] += 8.0
    places = gate.raised_places(raised, luma, np.zeros((64, 64), bool), np.zeros((64, 64), bool))
    assert len(places) == 12 * 12


    rng = np.random.default_rng(33)
    star = stars.Star(48.0, 48.0, 90.0, 4)
    weight = stars.footprint_map((96, 96), [star])
    luma = 40.0 + 0.2 * rng.standard_normal((96, 96)) + 200.0 * (weight > 0)
    left_out, clipped = weight > 0, np.zeros((96, 96), bool)
    assert gate.raised_places(luma, luma, left_out, clipped) == []
    beside = luma.copy()
    beside[48, 48 + 4 + 2 + 1] += gate.RAISE_CODES + 0.8
    places = gate.raised_places(beside, luma, left_out, clipped)
    assert [(p.x_px, p.y_px) for p in places] == [(55.0, 48.0)]
    # Reading the kept star's light, the same ring would have hidden it
    unmasked = gate.raised_places(beside, luma, np.zeros((96, 96), bool), clipped)
    assert (55.0, 48.0) not in [(p.x_px, p.y_px) for p in unmasked]


def test_a_stars_flux_is_refused_just_past_the_tolerance_and_kept_just_inside_it() -> None:
    # Finding B of AUDIT-B (2026-11-19): the tolerance raised from 3 to 5 sigma_F passed every test. The star is
    # brightened by a compact patch inside its aperture until its change is just over, then just under, the tolerance
    rng = np.random.default_rng(14)
    original = 0.01 + 0.0004 * rng.standard_normal((200, 200)) + gaussian((200, 200), 100.0, 100.0, 0.05)
    star = stars.Star(100.0, 100.0, 50.0, 4)
    footprint = stars.footprint_map((200, 200), [star])
    field = stars.StarField((star,), footprint, np.zeros((200, 200), bool), 1, 1, 0)
    sigma = gate.empty_aperture_sigma(original, star.radius_px, footprint, field.clipped)
    before = gate.aperture_flux(original, 100.0, 100.0, star.radius_px, footprint > 0)
    patch = gaussian((200, 200), 100.0, 100.0, 1.0, sigma=1.0)
    unit = gate.aperture_flux(original + patch, 100.0, 100.0, star.radius_px, footprint > 0, original) - before
    # The stated tolerance is 3 sigma_F (design section 7): the changes are absolute, so a changed constant fails
    for change, outside in ((3.2, 1), (2.8, 0)):
        variant = original + (change * sigma / unit) * patch
        assert gate.flux_change_sigma(star, original, variant, footprint, field.clipped) == pytest.approx(change, abs=1e-6)
        assert gate.flux_check(field, original, variant, footprint)[:2] == (1, outside), change


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
