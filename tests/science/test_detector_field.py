"""The detector hands what it measured on an image to forced photometry on that same image (restore-7s2).

`detect_point_sources` and `verify_candidates_on_original_pixels` measure the same things on an image before they
look at a single peak: its background and pixel noise, its noise limits, the statistic they threshold, its clipped
cores and its frame measures. A clip frame's star search runs both on each tile, so each tile was measured twice.
`detection_field` measures them once; either function takes that field and uses it only when it was measured on the
same image with the same sky mask, threshold and noise floor (checked by the image's digest, so an array changed in
place is measured again), and measures them itself otherwise.

Covers:
- With the image's own field, detection and verification answer exactly as they do without it, on quiet and
  correlated noise, a clipped core, a sky mask, the gate's threshold and floor, an empty frame and a frame too small to
  search.
- With the field, neither function measures the statistic again.
- A field measured on another image, on the same array changed in place, or with another sky mask, threshold or noise
  floor is not used: the answer is the image's own, and the statistic is measured again.
- The gate's star search on an original and its point search on a variant hand each tile's field from the detection
  to the verification: their answers are those found with every field refused, and the statistic is measured once
  per tile.
All frames here are simulated.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from scipy import ndimage

from services.media import gate, stars
from services.science import detector
from services.science.detector import (
    DETECTION_THRESHOLD,
    detect_point_sources,
    detection_field,
    verify_candidates_on_original_pixels,
)


def gaussian(shape: tuple[int, int], x: float, y: float, peak: float, sigma: float = 1.3) -> np.ndarray:
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float64)
    return peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))


def sky(seed: int, shape: tuple[int, int] = (256, 256), *, correlation_px: float = 0.0, stars_n: int = 24,
        top: float | None = None) -> np.ndarray:
    """A noisy sky with stars of many brightnesses, rounded to codes; with `top`, every code above it is set to it."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(shape)
    if correlation_px > 0:
        noise = ndimage.gaussian_filter(noise, correlation_px, mode="wrap")
        noise /= float(np.std(noise))
    luma = 20.0 + 3.0 * noise
    for _ in range(stars_n):
        luma += gaussian(shape, rng.uniform(8, shape[1] - 8), rng.uniform(8, shape[0] - 8), rng.uniform(6.0, 140.0))
    luma = np.round(luma)
    return np.minimum(luma, top) if top is not None else luma


def half_mask(shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    mask[:, : shape[1] // 2] = True
    return mask


# name: (image, sky mask, threshold, noise floor)
CASES = {
    "a quiet sky": (sky(1), None, DETECTION_THRESHOLD, 0.0),
    "correlated noise": (sky(2, correlation_px=1.283), None, DETECTION_THRESHOLD, 0.0),
    "a clipped core": (sky(3, stars_n=30, top=110.0), None, DETECTION_THRESHOLD, 0.0),
    "a sky mask": (sky(4), half_mask((256, 256)), DETECTION_THRESHOLD, 0.0),
    "the gate's threshold and floor": (gate.statistic_image(sky(5)), None, gate.VARIANT_THRESHOLD_SNR, gate.noise_floor()),
    "a wide frame": (sky(6, (256, 320), stars_n=40), None, DETECTION_THRESHOLD, 0.0),
    "an empty frame": (np.full((64, 64), 20.0), None, DETECTION_THRESHOLD, 0.0),
    "a frame too small to search": (sky(7, (10, 10), stars_n=0) + gaussian((10, 10), 5.2, 4.7, 90.0), None,
                                    DETECTION_THRESHOLD, 0.0),
}


def candidates(image: np.ndarray, found: list) -> list:
    """The detector's own detections, their places as pairs, and places on empty sky."""
    h, w = image.shape
    rng = np.random.default_rng(11)
    return list(found) + [(d.x_px + 0.4, d.y_px - 0.3) for d in found] + [
        (float(rng.uniform(0, w)), float(rng.uniform(0, h))) for _ in range(12)]


def same(a: list, b: list) -> bool:
    """Equal detections, field by field, every float exactly (a NaN equal to a NaN)."""
    return repr(a) == repr(b)


class Counting:
    """Counts the detector's measurements of its statistic."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        measure = detector._detection_statistic

        def counted(*args, **kwargs):
            self.calls += 1
            return measure(*args, **kwargs)

        monkeypatch.setattr(detector, "_detection_statistic", counted)


@pytest.mark.parametrize("name", CASES)
def test_with_the_images_own_field_detection_and_verification_answer_as_without_it(name: str) -> None:
    image, mask, threshold, floor = CASES[name]
    settings = {"sky_mask": mask, "snr_threshold": threshold, "noise_floor": floor}
    field = detection_field(image, **settings)
    alone = detect_point_sources(image, **settings)
    assert same(detect_point_sources(image, **settings, field=field), alone)
    places = candidates(image, alone)
    verified = verify_candidates_on_original_pixels(image, places, **settings)
    assert same(verify_candidates_on_original_pixels(image, places, **settings, field=field), verified)
    if name not in ("an empty frame", "a frame too small to search"):
        assert alone and verified, f"the control: {name} has detections to verify"


def test_with_the_field_neither_detection_nor_verification_measures_the_statistic_again(monkeypatch) -> None:
    image = CASES["a quiet sky"][0]
    counting = Counting(monkeypatch)
    field = detection_field(image)
    assert counting.calls == 1
    found = detect_point_sources(image, field=field)
    verify_candidates_on_original_pixels(image, candidates(image, found), field=field)
    assert counting.calls == 1, "the statistic was measured again though the image's own field was given"


def with_a_star(image: np.ndarray) -> np.ndarray:
    """The image with one more star, on an empty place of its sky."""
    return image + gaussian(image.shape, 200.3, 40.6, 90.0)


REFUSED = {
    "another image": lambda image: (with_a_star(image), {}),
    "another sky mask": lambda image: (image, {"sky_mask": half_mask(image.shape)}),
    "another threshold": lambda image: (image, {"snr_threshold": 4.0}),
    "another noise floor": lambda image: (image, {"noise_floor": 2.5}),
}


@pytest.mark.parametrize("name", REFUSED)
def test_a_field_measured_on_another_image_or_with_other_settings_is_not_used(name: str, monkeypatch) -> None:
    base = CASES["a quiet sky"][0]
    field = detection_field(base)
    image, settings = REFUSED[name](base)
    own = detect_point_sources(image, **settings)
    places = candidates(image, own) + [(200.3, 40.6)]
    own_verified = verify_candidates_on_original_pixels(image, places, **settings)
    counting = Counting(monkeypatch)
    assert same(detect_point_sources(image, **settings, field=field), own)
    assert counting.calls == 1, "detection used a field measured on something else"
    assert same(verify_candidates_on_original_pixels(image, places, **settings, field=field), own_verified)
    assert counting.calls == 2, "verification used a field measured on something else"


def test_a_field_is_not_used_once_its_array_has_changed_in_place(monkeypatch) -> None:
    image = CASES["a quiet sky"][0].copy()
    field = detection_field(image)
    image += gaussian(image.shape, 200.3, 40.6, 90.0)
    own = verify_candidates_on_original_pixels(image.copy(), [(200.3, 40.6)])
    assert own, "the control: the star added in place is verified on the changed array"
    counting = Counting(monkeypatch)
    assert same(verify_candidates_on_original_pixels(image, [(200.3, 40.6)], field=field), own)
    assert counting.calls == 1
    assert same(detect_point_sources(image, field=field), detect_point_sources(image.copy()))


def refuse_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detector.DetectionField, "fits", lambda self, *args, **kwargs: False)


def same_star_field(a: stars.StarField, b: stars.StarField) -> bool:
    for item in dataclasses.fields(a):
        x, y = getattr(a, item.name), getattr(b, item.name)
        if isinstance(x, np.ndarray):
            if not np.array_equal(x, y):
                return False
        elif x != y:
            return False
    return True


def original_codes() -> np.ndarray:
    """An original frame of four tiles and their overlaps, with stars and one clipped star, as 8 bit RGB codes."""
    luma = sky(21, (480, 480), stars_n=70) + gaussian((480, 480), 300.2, 260.7, 400.0, 2.0)
    return np.clip(np.round(np.repeat(luma[..., None], 3, axis=-1)), 0, 255).astype(np.uint8)


def test_the_gates_star_search_on_an_original_measures_each_tile_once_and_answers_as_with_every_field_refused(
        monkeypatch) -> None:
    codes = original_codes()
    counting = Counting(monkeypatch)
    handed = gate.original_stars(codes, 255)
    assert counting.calls == len(stars.tiles(*codes.shape[:2])), "a tile's statistic was measured more than once"
    assert handed.confirmed > 0 and handed.clipped_left_out > 0, "the control: stars verified and a clipped one left out"
    refuse_every_field(monkeypatch)
    assert same_star_field(handed, gate.original_stars(codes, 255))


def test_the_gates_point_search_on_a_variant_measures_each_tile_once_and_answers_as_with_every_field_refused(
        monkeypatch) -> None:
    image = gate.statistic_image(stars.luma_codes(original_codes()))
    clipped = np.zeros(image.shape, dtype=bool)
    counting = Counting(monkeypatch)
    handed = gate.points_on(image, clipped)
    assert counting.calls == len(stars.tiles(*image.shape)), "a tile's statistic was measured more than once"
    assert handed[0], "the control: the variant has points"
    refuse_every_field(monkeypatch)
    assert gate.points_on(image, clipped) == handed
