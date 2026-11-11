"""The head-worn Moon pose: lined up from the Moon, a camera profile and a level head (spec
predicted-sky-for-every-upload, REQ-SKY-3; design component 4).

Covers, with synthetic frames at the synthetic Moon moment and open-ocean place the fixtures use (the Moon 33 degrees
up), at the stored size of the glasses' clips (1344 x 1792):
- The Moon test, the Moon found by the contour at half its peak height above the sky: the brightest point after a
  2.5 px Gaussian (one hot pixel is not it), its peak at least 200 of 255 and at least 10 times the noise above the
  sky (the frame's median and MAD), its region (the pixels above the half-peak level) of an equivalent diameter
  inside the limits (0.8 times the Moon's predicted diameter at the widest field of view, 8 times at the narrowest),
  and no other spot above that level with half its area or more; each at its edge; the centroid, flux, FWHM and
  radius over the region grown by two pixels
- A soft-peaked Moon of the predicted size at 244 of 255 with only a few pixels near its top (as the first real
  head-worn clip records its Moon) is found within a pixel and lined up; a point light at 244, a lit cloud, a band of
  twilight, two comparable Moons and a peak under 200 are refused, each for its own reason
- The topocentric Moon: the ephemeris position lowered by its parallax shift (moon_parallax_shift_arcsec)
- One fit: the WCS a fixed field of view and roll give puts the topocentric Moon on the blob and agrees with a WCS
  built independently for the same pointing, roll and orientation (the level constraint's sign included)
- The sweep: nine WCS at the profile's two limits and middle and at rolls -10, 0 and +10 degrees, the nominal one at
  the middle field of view and roll 0, the largest centre offset across them and the Moon's radius as centroid term
- P-SKY-5: for any synthetic truth inside the profile's range and the roll range, the result is approximate, the
  topocentric Moon lands on the blob within 0.5 px under every swept WCS, and every in-frame predicted star's true
  pixel lies inside its ring (the largest distance to its swept projections plus the centroid term)
- P-SKY-6: two soft-peaked Moons of comparable area, or only point lights smaller than the Moon test allows, give no
  pose
- The approximate solution: its fields, the observed Moon finding, the detector's findings minus those on the Moon in
  the same frame, the predicted sky with in-frame subtitles, the warnings, and no capture value in any of them
- No pose: sky-2's predicted_only result with one more plain reason (no head-worn profile, the Moon not up, not
  confirmed as the Moon, the fit refused, a mirrored frame, no frame read), never a stand-in pose; a calibrated
  solution never takes the pose path
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from typing import Any

from hypothesis import HealthCheck, assume, example, given, settings, strategies as st
import numpy as np
import pytest
from scipy import ndimage

from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL, camera_profile
from services.science.catalog import altaz_to_icrs, angular_separation_deg, get_catalog_stars, icrs_to_altaz
from services.science.ephemeris import compute_solar_system_ephemerides, moon_parallax_shift_arcsec
from services.science import pose_estimate as pe
from services.science.pose_estimate import (
    MoonBlob,
    PoseResult,
    Refusal,
    find_moon_blob,
    fit_level_pose,
    head_worn_moon_pose,
    moon_diameter_limits_px,
    scale_deg_per_px,
    topocentric_moon,
)
from services.science.predicted_sky import complete_sky, frame_moment, predicted_sky_findings, sky_phase
from services.science.solver import _build_unresolved_solution, triangle_solver_identity
from services.science.wcs_models import ObservationSolution, SourceDetection, WCSModel

WIDTH, HEIGHT = 1344, 1792
# The synthetic Moon moment and open-ocean place of the fixtures (tests/integration/test_unregistered_detections.py):
# the Moon 33 degrees up. The second moment has the Moon below the horizon. They point at nobody
MOON_MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0
MOON_DOWN_MOMENT = "2030-01-01T02:00:00Z"
PROFILE = camera_profile(META_RAY_BAN_DISPLAY_MODEL)
ASSET_ID = "src_synthetic_head_worn"
SHA = hashlib.sha256(b"synthetic head-worn Moon pose").hexdigest()
LEAKS = (MOON_MOMENT, MOON_MOMENT[:10], MOON_MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00")
YY, XX = np.ogrid[0:HEIGHT, 0:WIDTH]


def _asset(moment: str = MOON_MOMENT, *, model: str | None = META_RAY_BAN_DISPLAY_MODEL, turns: int = 0,
           mirrored: bool = False, kind: str = "video") -> dict[str, Any]:
    asset: dict[str, Any] = {
        "id": ASSET_ID, "sha256": SHA, "mediaKind": kind, "width": WIDTH, "height": HEIGHT,
        "capturedAtUTC": moment, "observerLocation": {"latDeg": LAT, "lonDeg": LON},
    }
    if model is not None:
        asset["cameraMetadata"] = {"make": "Meta", "model": model}
    if turns or mirrored:
        asset["displayOrientation"] = {"quarterTurnsClockwise": turns, "mirrored": mirrored}
    return asset


def _solver_solution(detections: list[SourceDetection] | None = None, warning: str = "Too few sources.") -> ObservationSolution:
    return _build_unresolved_solution(
        detections or [], "sol-synthetic-head-worn", ASSET_ID, SHA, 0.0, MOON_MOMENT, LAT, LON, warning,
        "Pixel position only: no verified WCS places it on the sky.", triangle_solver_identity(get_catalog_stars()),
    )


def _frame(x: float, y: float, radius: float, *, value: float = 255.0, background: float = 2.0, seed: int = 0) -> np.ndarray:
    """A dark frame with read noise and one flat disk at `value`: a synthetic Moon whose core is clipped [simulated]."""
    rng = np.random.default_rng(seed)
    luma = np.clip(background + rng.normal(0.0, 1.0, (HEIGHT, WIDTH)), 0.0, None)
    luma[(XX - x) ** 2 + (YY - y) ** 2 <= radius * radius] = value
    return luma


def _rects(*rects: tuple[int, int, int, int], value: float = 255.0) -> np.ndarray:
    """A dark frame with flat bright rectangles (x0, y0, w, h), so each spot's area is exact. The sky is code 2 with
    every third pixel at 0, a noise of whole codes that weighs nothing in a centroid (the median is 2)."""
    luma = np.where((XX + YY) % 3 == 0, 0.0, 2.0)
    for x0, y0, w, h in rects:
        luma[y0 : y0 + h, x0 : x0 + w] = value
    return luma


def _capture_free(text: str) -> None:
    for value in LEAKS:
        assert value not in text, (value, text)


# --- An independent model of the truth -----------------------------------------------------------------------------


def _upright(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """A stored-pixel vector as the upright frame shows it (packages/contracts/src/media.ts: turned clockwise)."""
    for _ in range(turns):
        dx, dy = -dy, dx
    return dx, dy


def _stored(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """An upright-frame vector in stored pixels: the turns undone."""
    for _ in range(turns):
        dx, dy = dy, -dx
    return dx, dy


def _scale(fov_deg: float) -> float:
    return math.degrees(2.0 * math.tan(math.radians(fov_deg) / 2.0) / math.hypot(WIDTH, HEIGHT))


def _truth_wcs(centre_alt: float, centre_az: float, fov_deg: float, roll_deg: float, turns: int, moment: str) -> WCSModel:
    """The frame centre at this altitude and azimuth, straight up in the upright frame pointing to the zenith, then
    the picture turned clockwise by the roll (the horizon's right side lower for a positive roll)."""
    ra, dec = altaz_to_icrs(centre_alt, centre_az, LAT, LON, moment)
    unrotated = WCSModel(crval_ra_deg=ra, crval_dec_deg=dec, crpix_x=WIDTH / 2.0, crpix_y=HEIGHT / 2.0,
                         scale_deg_per_px=_scale(fov_deg), width=WIDTH, height=HEIGHT)
    up_ra, up_dec = altaz_to_icrs(centre_alt + 0.5, centre_az, LAT, LON, moment)
    zx, zy = unrotated.world_to_pixel(up_ra, up_dec)
    tx, ty = _stored(0.0, -1.0, turns)
    level = math.degrees(math.atan2(zy - HEIGHT / 2.0, zx - WIDTH / 2.0) - math.atan2(ty, tx))
    return replace(unrotated, rotation_deg=level - roll_deg)


def _topocentric(moment: str) -> tuple[float, float, float]:
    """The Moon as seen from the place: its geocentric altitude lowered by its parallax shift; (ra, dec, radius)."""
    (moon,) = compute_solar_system_ephemerides(moment, LAT, LON, bodies=("Moon",))
    shift = moon_parallax_shift_arcsec(moon.distance_au, moon.alt_deg)[1]
    ra, dec = altaz_to_icrs(moon.alt_deg - shift / 3600.0, moon.az_deg, LAT, LON, moment)
    distance_km = moon.distance_au * 149597870.7
    alt_rad = math.radians(moon.alt_deg)
    topo_km = math.sqrt(distance_km**2 + 6378.137**2 - 2.0 * distance_km * 6378.137 * math.sin(alt_rad))
    return ra, dec, math.degrees(math.asin(1737.4 / topo_km))


def _roll_of(wcs: WCSModel, turns: int, moment: str) -> float:
    """The horizon's tilt in the upright frame: two points at the centre's altitude, 10 degrees either side in
    azimuth; positive when the right one is lower."""
    alt, az = icrs_to_altaz(wcs.crval_ra_deg, wcs.crval_dec_deg, LAT, LON, moment)
    lx, ly = wcs.world_to_pixel(*altaz_to_icrs(alt, az - 10.0, LAT, LON, moment))
    rx, ry = wcs.world_to_pixel(*altaz_to_icrs(alt, az + 10.0, LAT, LON, moment))
    dx, dy = _upright(rx - lx, ry - ly, turns)
    assert dx > 0.0
    return math.degrees(math.atan2(dy, dx))


def _fov_of(wcs: WCSModel) -> float:
    return math.degrees(2.0 * math.atan(math.radians(wcs.scale_deg_per_px) * math.hypot(WIDTH, HEIGHT) / 2.0))


# --- The Moon test ---------------------------------------------------------------------------------------------------

EIGHT = np.ones((3, 3), dtype=bool)


def _limits() -> dict[str, float]:
    """The Moon test's diameter limits at the synthetic moment for the profile, as the pose uses them."""
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    low, high = moon_diameter_limits_px(moon, PROFILE.fov_diagonal_deg_range, WIDTH, HEIGHT)
    return {"min_diameter_px": low, "max_diameter_px": high}


def _predicted_radius_px(fov_deg: float = 95.0) -> float:
    """The Moon's predicted radius in pixels at this diagonal field of view (by default the profile's middle)."""
    return _topocentric(MOON_MOMENT)[2] / _scale(fov_deg)


def _noise(seed: int = 1, *, background: float = 2.0, sigma: float = 1.0) -> np.ndarray:
    """A sky of read noise only, in whole codes [simulated]."""
    rng = np.random.default_rng(seed)
    return np.round(np.clip(background + rng.normal(0.0, sigma, (HEIGHT, WIDTH)), 0.0, 255.0))


def _soft_disk(x: float, y: float, radius: float, blur: float, supersample: int = 5) -> tuple[slice, slice, np.ndarray]:
    """A disk of `radius` px about (x, y), each pixel's share of it counted on a 5 x 5 grid inside the pixel (pixel
    centres at whole coordinates), convolved with a Gaussian of `blur` px and scaled to 1 at its brightest pixel; with
    the rows and columns of the window it fills."""
    half = int(math.ceil(radius + 5.0 * blur + 4.0))
    x0, y0 = int(math.floor(x)) - half, int(math.floor(y)) - half
    n = 2 * half + 2
    sub = (np.arange(n * supersample) + 0.5) / supersample - 0.5
    yy, xx = np.meshgrid(sub + y0 - y, sub + x0 - x, indexing="ij")
    cover = (xx * xx + yy * yy <= radius * radius).reshape(n, supersample, n, supersample).mean(axis=(1, 3))
    soft = ndimage.gaussian_filter(cover, blur, mode="constant")
    return slice(y0, y0 + n), slice(x0, x0 + n), soft / soft.max()


def _soft_moon(
    x: float, y: float, radius: float | None = None, *, peak: float = 244.0, blur: float = 2.0,
    background: float = 2.0, sigma: float = 1.0, seed: int = 0, luma: np.ndarray | None = None,
) -> np.ndarray:
    """A sky of read noise (or `luma`) with one soft-peaked Moon over it: a disk of `radius` px (by default the Moon's
    predicted radius at the profile's middle field of view) convolved with a blur of `blur` px, rising from
    `background` to `peak` at its brightest pixel, in whole codes. Nothing is clipped: its top is a peak, not a
    plateau [simulated]."""
    luma = _noise(seed, background=background, sigma=sigma) if luma is None else luma.copy()
    rows, cols, soft = _soft_disk(x, y, _predicted_radius_px() if radius is None else radius, blur)
    luma[rows, cols] = np.maximum(luma[rows, cols], np.round(background + (peak - background) * soft))
    return luma


def _point_light(luma: np.ndarray, x: int, y: int, *, peak: float = 244.0, blur: float = 2.0) -> np.ndarray:
    """An unresolved light at the whole pixel (x, y) over a dark sky (code 2): a Gaussian of `blur` px rising to
    `peak` [simulated]."""
    luma = luma.copy()
    half = int(math.ceil(6.0 * blur)) + 1
    rows, cols = slice(y - half, y + half + 1), slice(x - half, x + half + 1)
    yy, xx = np.mgrid[rows, cols]
    spot = np.round(2.0 + (peak - 2.0) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * blur * blur)))
    luma[rows, cols] = np.maximum(luma[rows, cols], spot)
    return luma


def _rule(luma: np.ndarray) -> tuple[int, int, float, float, np.ndarray, np.ndarray]:
    """The Moon test's measurements written out independently: the brightest point after a Gaussian of 2.5 px, its
    code in the frame's own pixels, the frame's median, the 8-connected labels above the half-peak level, and the
    region holding the peak."""
    smoothed = ndimage.gaussian_filter(luma, sigma=2.5)
    py, px = np.unravel_index(int(np.argmax(smoothed)), luma.shape)
    peak, background = float(luma[py, px]), float(np.median(luma))
    labels, _ = ndimage.label(luma > background + (peak - background) / 2.0, structure=EIGHT)
    return int(px), int(py), peak, background, labels, labels == labels[py, px]


def test_a_soft_peaked_moon_of_the_predicted_size_is_found_at_half_its_peak_within_a_pixel() -> None:
    """As the first real head-worn clip records its Moon: a peak at 244 of 255 with only a few pixels near its top,
    so no flat clipped core; the contour at half its peak still follows the disk's edge."""
    luma = _soft_moon(500.3, 700.6)
    top, _ = ndimage.label(luma >= luma.max() - 3.0, structure=EIGHT)
    assert luma.max() == 244.0 and int(np.bincount(top.ravel())[1:].max()) <= 4
    blob = find_moon_blob(luma, **_limits())
    assert isinstance(blob, MoonBlob), blob
    assert math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0
    # The region at half the peak is the disk: its equivalent diameter within a pixel and a half of the predicted one
    assert abs(blob.equivalent_diameter_px - 2.0 * _predicted_radius_px()) < 1.5
    # A clipped Moon, a flat disk at 255, is found by the same contour, its edge
    flat = find_moon_blob(_frame(700.0, 500.0, 6.0), **_limits())
    assert isinstance(flat, MoonBlob) and flat.area_px == int(((XX - 700.0) ** 2 + (YY - 500.0) ** 2 <= 36.0).sum())


def test_one_hot_pixel_brighter_than_the_moon_is_not_taken_for_it() -> None:
    luma = _soft_moon(500.3, 700.6)
    luma[100, 1200] = 255.0
    blob = find_moon_blob(luma, **_limits())
    assert isinstance(blob, MoonBlob), blob
    assert math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0
    # Its peak is the Moon's own top, 244, not the hot pixel's 255: the region and SNR are the Moon's
    px, py, peak, background, _, region = _rule(luma)
    assert (px, py, peak) == (500, 701, 244.0) and blob.area_px == int(region.sum())
    assert blob.snr == pytest.approx((peak - background) / (1.4826 * float(np.median(np.abs(luma - background)))))
    # and the floor is judged on that peak: a Moon at 199 beside a hot pixel at 255 is refused
    dim = _soft_moon(500.3, 700.6, peak=199.0)
    dim[100, 1200] = 255.0
    refusal = find_moon_blob(dim, **_limits())
    assert isinstance(refusal, Refusal) and "peaks at 199 of 255, under 200" in refusal.reason


def test_a_point_light_at_244_is_refused_on_its_size() -> None:
    """An unresolved light's region at half its peak is only its blur: here 21 px, 5.2 px across."""
    limits = _limits()
    luma = _point_light(_noise(), 600, 900, peak=244.0, blur=2.0)
    _, _, peak, _, _, region = _rule(luma)
    assert peak == 244.0 and int(region.sum()) == 21
    refusal = find_moon_blob(luma, **limits)
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon"
    assert refusal.reason == (
        "The frame's bright spots were not confirmed as the Moon: the brightest spot is 5.2 px across at half its "
        f"peak, under the {limits['min_diameter_px']:.1f} px the Moon needs."
    )


def test_a_lit_cloud_or_a_band_of_twilight_wider_than_eight_moons_is_refused_on_its_size() -> None:
    limits = _limits()
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    cloud = np.maximum(_noise(), np.round(2.0 + 228.0 * np.exp(-((xx - 700.0) ** 2 + (yy - 600.0) ** 2) / (2.0 * 60.0**2))))
    twilight = np.maximum(_noise(), np.round(2.0 + 228.0 * np.exp(-((HEIGHT - 1.0 - yy) ** 2) / (2.0 * 250.0**2))))
    for luma in (cloud, twilight):
        refusal = find_moon_blob(luma, **limits)
        assert isinstance(refusal, Refusal) and refusal.kind == "not_moon", refusal
        assert refusal.reason.endswith(f"px across at half its peak, over the {limits['max_diameter_px']:.1f} px the Moon allows.")
    # A Moon beside a lit cloud brighter than it is refused with it: the cloud is the brightest peak
    assert isinstance(find_moon_blob(_soft_moon(1100.0, 1500.0, peak=220.0, luma=cloud), **limits), Refusal)


def test_two_comparable_moons_are_refused() -> None:
    luma = _soft_moon(900.0, 400.0, peak=240.0, luma=_soft_moon(400.0, 1200.0, peak=244.0))
    refusal = find_moon_blob(luma, **_limits())
    assert isinstance(refusal, Refusal) and refusal.reason == (
        "The frame's bright spots were not confirmed as the Moon: another spot rises above half the brightest one's "
        "peak with half its area or more."
    )


def test_a_rival_counts_only_when_it_rises_above_the_moons_half_peak_level() -> None:
    """The Moon peaks at 244 over a sky at 2, so its half-peak level is 123. A wider spot at 180 (under three
    quarters of the Moon's height, 183.5) rises above 123 over more than half the Moon's area: refused. The same
    spot at 110, under 123, is no rival, however large it is at half its own peak."""
    moon = _soft_moon(400.0, 1200.0)
    rival = _soft_moon(900.0, 400.0, 8.0, peak=180.0, luma=moon)
    px, py, peak, background, labels, region = _rule(rival)
    assert (peak, background) == (244.0, 2.0)
    assert int((labels == labels[400, 900]).sum()) >= 0.5 * int(region.sum()) and 180.0 < 2.0 + 0.75 * 242.0
    assert isinstance(find_moon_blob(rival, **_limits()), Refusal)
    quiet = _soft_moon(900.0, 400.0, 8.0, peak=110.0, luma=moon)
    blob = find_moon_blob(quiet, **_limits())
    assert isinstance(blob, MoonBlob) and math.hypot(blob.x_px - 400.0, blob.y_px - 1200.0) < 1.0


def test_a_peak_under_200_is_refused_and_200_is_enough() -> None:
    refusal = find_moon_blob(_soft_moon(500.0, 700.0, peak=199.0), **_limits())
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon"
    assert refusal.reason == (
        "The frame's bright spots were not confirmed as the Moon: the brightest spot peaks at 199 of 255, under 200."
    )
    assert isinstance(find_moon_blob(_soft_moon(500.0, 700.0, peak=200.0), **_limits()), MoonBlob)
    # A dark frame's brightest point is noise
    assert "peaks at" in find_moon_blob(_noise(), **_limits()).reason
    assert pe.MIN_PEAK_LUMA == 200.0


def test_the_peak_must_be_at_least_10_times_the_noise_above_the_sky(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Moon at 244 over a bright, noisy sky at 150: read noise of 10 codes puts it under 10 times the noise (the
    frame's median and 1.4826 times its median absolute deviation), 8 codes over it."""
    for sigma, enough in ((10.0, False), (8.0, True)):
        luma = _soft_moon(500.0, 700.0, background=150.0, sigma=sigma, seed=5)
        _, _, peak, background, _, _ = _rule(luma)
        snr = (peak - background) / (1.4826 * float(np.median(np.abs(luma - background))))
        assert (snr >= 10.0) is enough, snr
        result = find_moon_blob(luma, **_limits())
        if enough:
            assert isinstance(result, MoonBlob) and result.snr == pytest.approx(snr, rel=1e-9)
        else:
            assert isinstance(result, Refusal) and result.reason == (
                f"The frame's bright spots were not confirmed as the Moon: the brightest spot peaks {snr:.1f} times "
                "the noise above the sky, under 10."
            )
    # At the edge: the measured SNR itself is enough, a hair over it is not
    luma = _soft_moon(500.0, 700.0)
    _, _, peak, background, _, _ = _rule(luma)
    snr = (peak - background) / (1.4826 * float(np.median(np.abs(luma - background))))
    assert isinstance(find_moon_blob(luma, **_limits()), MoonBlob)
    assert pe.MIN_PEAK_SNR == 10.0
    monkeypatch.setattr(pe, "MIN_PEAK_SNR", snr)
    assert isinstance(find_moon_blob(luma, **_limits()), MoonBlob)
    monkeypatch.setattr(pe, "MIN_PEAK_SNR", snr * (1.0 + 1e-9))
    assert isinstance(find_moon_blob(luma, **_limits()), Refusal)
    monkeypatch.undo()
    # A sky with no noise at all (its MAD 0) is floored at 1e-4, as horizon_solver.py floors it, so a flat clipped
    # Moon on it passes; the spot's reported SNR keeps the detector's rounding noise of the frame's code step (for
    # two codes, 4 and 255, that step is 251, so the SNR is sqrt(12))
    flat = np.full((HEIGHT, WIDTH), 4.0)
    flat[(XX - 500.0) ** 2 + (YY - 700.0) ** 2 <= 36.0] = 255.0
    clipped = find_moon_blob(flat, **_limits())
    assert isinstance(clipped, MoonBlob) and clipped.snr == pytest.approx(math.sqrt(12.0), rel=1e-9)


def test_the_region_is_above_the_half_peak_level_and_the_centroid_is_measured_over_it_grown_by_two() -> None:
    luma = _soft_moon(700.3, 500.6, 7.0, seed=3)
    blob = find_moon_blob(luma, min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(blob, MoonBlob), blob
    px, py, peak, background, _, region = _rule(luma)
    assert peak == 244.0
    grown = ndimage.binary_dilation(region, structure=EIGHT, iterations=2)
    weights = np.where(grown, np.clip(luma - background, 0.0, None), 0.0)
    yy, xx = np.indices(luma.shape)
    assert blob.x_px == pytest.approx(float((xx * weights).sum() / weights.sum()), abs=1e-9)
    assert blob.y_px == pytest.approx(float((yy * weights).sum() / weights.sum()), abs=1e-9)
    assert abs(blob.x_px - 700.3) < 0.1 and abs(blob.y_px - 500.6) < 0.1
    assert blob.area_px == int(region.sum())
    assert blob.equivalent_diameter_px == pytest.approx(2.0 * math.sqrt(region.sum() / math.pi))
    assert blob.radius_px == pytest.approx(math.sqrt(grown.sum() / math.pi))
    assert blob.flux == pytest.approx(float(weights.sum()))
    sigma_sq = float((((xx - blob.x_px) ** 2 + (yy - blob.y_px) ** 2) * weights).sum() / (2.0 * weights.sum()))
    assert blob.fwhm_px == pytest.approx(2.355 * math.sqrt(sigma_sq))
    noise = 1.4826 * float(np.median(np.abs(luma - background)))
    assert blob.snr == pytest.approx((peak - background) / noise, rel=1e-9) and blob.snr > 100.0
    assert 0.0 < blob.peak_to_total_ratio < 1.0
    assert blob.peak_to_total_ratio == pytest.approx((peak - background) / blob.flux)
    assert blob.centroid_uncertainty_px == pytest.approx(blob.fwhm_px / (2.355 * blob.snr))
    # Strictly above the half-peak level: a ring exactly at it, 123 for a peak of 244 over a sky at 2, is left out
    ringed = _rects((598, 798, 14, 14), value=123.0)
    ringed[800:810, 600:610] = 244.0
    ring = find_moon_blob(ringed, min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(ring, MoonBlob) and ring.area_px == 100


def test_the_blob_must_be_at_least_its_minimum_and_at_most_its_maximum_equivalent_diameter() -> None:
    frame = _rects((600, 800, 10, 10))
    diameter = 2.0 * math.sqrt(100.0 / math.pi)
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter - 1e-6, max_diameter_px=50.0), MoonBlob)
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter + 1e-6, max_diameter_px=50.0), Refusal)
    assert isinstance(find_moon_blob(frame, min_diameter_px=5.0, max_diameter_px=diameter + 1e-6), MoonBlob)
    assert isinstance(find_moon_blob(frame, min_diameter_px=5.0, max_diameter_px=diameter - 1e-6), Refusal)
    # Both limits hold their own value: "at least" the minimum, "at most" the maximum
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter, max_diameter_px=diameter), MoonBlob)


def test_every_other_spot_above_the_half_peak_level_must_be_under_half_its_area() -> None:
    # 100 px against 49 px: accepted; against 50 px, exactly half: refused
    blob = find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 7, 7)), min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(blob, MoonBlob) and (round(blob.x_px, 6), round(blob.y_px, 6)) == (604.5, 804.5)
    refused = find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 10, 5)), min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(refused, Refusal) and "not confirmed as the Moon" in refused.reason
    # A larger lit cloud refuses the Moon
    cloud = find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 60, 60)), min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(cloud, Refusal)
    # Diagonal neighbours are one spot (8-connected), so a spot touching at a corner is not a second one
    corner = find_moon_blob(_rects((600, 800, 10, 10), (610, 810, 8, 8)), min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(corner, MoonBlob) and corner.area_px == 164


def test_no_bright_spot_or_none_of_the_moons_size_is_refused() -> None:
    assert isinstance(find_moon_blob(_rects(), min_diameter_px=5.0, max_diameter_px=50.0), Refusal)
    small = _rects((100, 100, 3, 3), (400, 400, 4, 4), (900, 900, 2, 2))
    assert isinstance(find_moon_blob(small, min_diameter_px=5.0, max_diameter_px=50.0), Refusal)


def test_the_size_limits_are_the_moons_predicted_diameter_at_the_profiles_two_limits() -> None:
    ra, dec, radius = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    low, high = moon_diameter_limits_px(moon, PROFILE.fov_diagonal_deg_range, WIDTH, HEIGHT)
    assert low == pytest.approx(0.8 * 2.0 * radius / _scale(110.0), rel=1e-9)
    assert high == pytest.approx(8.0 * 2.0 * radius / _scale(80.0), rel=1e-9)
    assert scale_deg_per_px(95.0, WIDTH, HEIGHT) == pytest.approx(_scale(95.0), rel=1e-12)


def test_the_moon_is_lined_up_where_it_is_seen_from_the_place_not_from_the_earths_centre() -> None:
    (geo,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    shift = moon_parallax_shift_arcsec(geo.distance_au, geo.alt_deg)[1]
    assert shift > 2000.0
    ra, dec, radius = _topocentric(MOON_MOMENT)
    assert angular_separation_deg(moon.ra_deg, moon.dec_deg, ra, dec) < 1e-9
    assert moon.alt_deg == pytest.approx(geo.alt_deg - shift / 3600.0, abs=1e-9)
    assert moon.az_deg == pytest.approx(geo.az_deg, abs=1e-9)
    assert moon.radius_deg == pytest.approx(radius, rel=1e-9)
    assert angular_separation_deg(moon.ra_deg, moon.dec_deg, geo.ra_deg, geo.dec_deg) == pytest.approx(shift / 3600.0, rel=1e-3)


# --- One fit and the sweep --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("fov", "roll", "turns"), [(95.0, 0.0, 0), (80.0, 10.0, 0), (110.0, -10.0, 1), (88.0, 7.0, 3), (101.0, -4.0, 2)])
def test_one_fit_is_the_pointing_that_roll_and_field_of_view_give(fov: float, roll: float, turns: int) -> None:
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    truth = _truth_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, fov, roll, turns, MOON_MOMENT)
    mx, my = truth.world_to_pixel(ra, dec)
    fitted = fit_level_pose(moon, mx, my, width=WIDTH, height=HEIGHT, fov_diagonal_deg=fov, roll_deg=roll,
                            orientation={"quarterTurnsClockwise": turns, "mirrored": False},
                            moment=MOON_MOMENT, lat=LAT, lon=LON)
    assert isinstance(fitted, WCSModel)
    assert (fitted.parity, fitted.k1, fitted.k2) == (-1, 0.0, 0.0)
    assert (fitted.crpix_x, fitted.crpix_y) == (WIDTH / 2.0, HEIGHT / 2.0)
    assert fitted.scale_deg_per_px == pytest.approx(_scale(fov), rel=1e-12)
    fx, fy = fitted.world_to_pixel(ra, dec)
    assert math.hypot(fx - mx, fy - my) <= 0.5
    assert _roll_of(fitted, turns, MOON_MOMENT) == pytest.approx(roll, abs=0.05)
    # Every bright star in the frame lands where the independently built truth puts it
    for star in get_catalog_stars()[:200]:
        tx, ty = truth.world_to_pixel(star.ra_deg, star.dec_deg)
        if not (0.0 <= tx < WIDTH and 0.0 <= ty < HEIGHT):
            continue
        sx, sy = fitted.world_to_pixel(star.ra_deg, star.dec_deg)
        assert math.hypot(sx - tx, sy - ty) < 0.5, star.id


def test_a_fit_that_shows_the_sky_upside_down_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The level constraint holds for a frame turned by half a turn too; the fit must refuse that one. Built by hand:
    the upright truth turned by 180 degrees puts the Moon at the pixel mirrored through the frame centre, so the fit
    is handed that pixel and that WCS, which meets all three residuals."""
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    truth = _truth_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, 95.0, 0.0, 0, MOON_MOMENT)
    mx, my = truth.world_to_pixel(ra, dec)

    class Fit:
        x = np.array([truth.crval_ra_deg, truth.crval_dec_deg, truth.rotation_deg + 180.0])

    monkeypatch.setattr(pe.optimize, "least_squares", lambda *args, **kwargs: Fit())
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None,
                  moment=MOON_MOMENT, lat=LAT, lon=LON)
    turned = fit_level_pose(moon, WIDTH - mx, HEIGHT - my, **kwargs)
    assert isinstance(turned, Refusal) and turned.kind == "fit"
    # The same hand-built fit the right way up is accepted, so only the turn refused it
    Fit.x = np.array([truth.crval_ra_deg, truth.crval_dec_deg, truth.rotation_deg])
    assert isinstance(fit_level_pose(moon, mx, my, **kwargs), WCSModel)


def test_the_fit_is_accepted_only_up_to_half_a_pixel() -> None:
    assert pe.MAX_FIT_RESIDUAL_PX == 0.5
    assert pe._fit_accepted(np.array([0.5, -0.5, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, 0.5000001, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, -0.5000001, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, float("nan"), 0.0]))


def _pose(luma: np.ndarray, turns: int = 0, frame_time: float = 0.0) -> PoseResult | Refusal:
    return head_worn_moon_pose(
        luma, frame_time_sec=frame_time, moment=frame_moment(MOON_MOMENT, frame_time), lat=LAT, lon=LON,
        profile=PROFILE, orientation={"quarterTurnsClockwise": turns, "mirrored": False},
    )


def test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way() -> None:
    assert pe.SWEPT_ROLLS_DEG == (-10.0, 0.0, 10.0)
    pose = _pose(_frame(500.0, 700.0, 6.0))
    assert isinstance(pose, PoseResult)
    estimate = pose.pose_estimate
    swept = estimate.swept_wcs
    assert len(swept) == 9
    pairs = sorted((round(_fov_of(w), 6), round(_roll_of(w, 0, MOON_MOMENT), 1)) for w in swept)
    assert pairs == sorted((fov, roll) for fov in (80.0, 95.0, 110.0) for roll in (-10.0, 0.0, 10.0))
    # The nominal WCS is the middle field of view at roll 0, and it is one of the swept ones
    assert pose.wcs in swept
    assert round(_fov_of(pose.wcs), 6) == 95.0 and abs(_roll_of(pose.wcs, 0, MOON_MOMENT)) < 0.05
    assert (estimate.fov_diagonal_deg, estimate.roll_deg) == (95.0, 0.0)
    centre_alt, centre_az = icrs_to_altaz(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, LAT, LON, MOON_MOMENT)
    assert estimate.center_alt_deg == pytest.approx(centre_alt, abs=1e-9)
    assert estimate.center_az_deg == pytest.approx(centre_az, abs=1e-9)
    offsets = [angular_separation_deg(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg) for w in swept]
    assert estimate.max_center_offset_deg == pytest.approx(max(offsets), abs=1e-9) and max(offsets) > 1.0
    _, _, radius = _topocentric(MOON_MOMENT)
    assert estimate.beacon_centroid_uncertainty_deg == pytest.approx(radius, rel=1e-9)
    assert (estimate.method, estimate.beacon) == ("moon_camera_profile_level_head", "moon")
    assert META_RAY_BAN_DISPLAY_MODEL in estimate.fov_source and "[open]" in estimate.fov_source
    words = " ".join(estimate.assumptions)
    for needed in ("80 to 110 degrees", "10 degrees either way", "rectilinear", "topocentric", "refraction", "radius"):
        assert needed in words, needed
    # The profile's range is for the camera with no zoom, and nothing in the file is read to show a zoom was not used
    assert "The camera was not zoomed" in words
    for text in (*estimate.assumptions, estimate.fov_source):
        _capture_free(text)


def test_frames_are_tried_in_time_order_and_the_first_that_passes_is_kept() -> None:
    """Each frame is judged on its own: a brighter clipped Moon in a later frame does not stop the soft one in an
    earlier frame, and a point light earlier still is refused."""
    dark = np.full((HEIGHT, WIDTH), 2.0)
    frames = [
        (5.0, frame_moment(MOON_MOMENT, 5.0), _frame(900.0, 400.0, 6.0)),
        (1.0, frame_moment(MOON_MOMENT, 1.0), dark),
        (4.0, frame_moment(MOON_MOMENT, 4.0), _soft_moon(800.0, 300.0)),
        (2.0, frame_moment(MOON_MOMENT, 2.0), _point_light(_noise(), 600, 900)),
        (3.0, frame_moment(MOON_MOMENT, 3.0), _soft_moon(500.3, 700.6)),
    ]
    pose = pe.pose_from_frames(frames, lat=LAT, lon=LON, profile=PROFILE, orientation=None)
    assert isinstance(pose, PoseResult)
    assert pose.frame_time_sec == 3.0 and pose.moment == frame_moment(MOON_MOMENT, 3.0)
    assert math.hypot(pose.blob.x_px - 500.3, pose.blob.y_px - 700.6) < 1.0


# --- Through the job's frames: the soft-peaked Moon ------------------------------------------------------------------

NOT_CONFIRMED = (
    "The sky is not lined up over the picture: what was seen {where} was not confirmed as the Moon (that needs a frame "
    "whose brightest spot peaks at 200 of 255 or more and at least 10 times the noise above the sky, is about the "
    "Moon's size at half its peak, and has no other spot above that level with half its area or more)."
)


def _timed(*frames: tuple[float, np.ndarray]) -> list[tuple[float, str, np.ndarray]]:
    return [(t, frame_moment(MOON_MOMENT, t), luma) for t, luma in frames]


def test_a_soft_peaked_moon_at_244_is_lined_up_from_it() -> None:
    """The first real head-worn clip's Moon peaks at 241 to 244 of 255 with no flat clipped core; a synthetic one of
    the predicted size at 244 is lined up, in a clip and in a photo."""
    luma = _soft_moon(500.3, 700.6)
    region = _rule(luma)[5]
    for kind, frame_time in (("video", 3.0), ("image", 0.0)):
        out = complete_sky(_solver_solution(), _asset(kind=kind), frames=lambda: [(frame_time, luma)])
        assert out.status == "approximate", out.warnings
        assert out.frame_time_sec == frame_time and len(out.pose_estimate.swept_wcs) == 9
        (moon,) = [f for f in out.findings if f.id == "finding-observed-moon"]
        detection = moon.source_detection
        assert math.hypot(detection.x_px - 500.3, detection.y_px - 700.6) < 1.0
        assert moon.title == "Moon (Observed Bright Spot)"
        assert moon.subtitle.endswith(f"· {int(region.sum())} px above half its peak")
        assert not any(f.evidence_class == "catalog_matched" for f in out.findings)


def test_a_peak_under_200_gives_no_pose_with_the_rule_in_plain_words() -> None:
    solver, clip, photo = _solver_solution(), _asset(), _asset(kind="image")
    dim = _soft_moon(500.0, 700.0, peak=199.0)
    out = complete_sky(solver, clip, frames=lambda: [(0.5, dim), (1.5, _noise())])
    reason = out.warnings[-2]
    assert reason == NOT_CONFIRMED.format(where="in the 2 frames searched")
    _assert_sky_2_plus(out, solver, clip, reason)
    one = complete_sky(solver, photo, frames=lambda: [(0.0, dim)])
    assert one.warnings[-2] == NOT_CONFIRMED.format(where="in the photo")
    _assert_sky_2_plus(one, solver, photo, one.warnings[-2])
    single = complete_sky(solver, clip, frames=lambda: [(0.5, dim)]).warnings[-2]
    assert single == NOT_CONFIRMED.format(where="in the frame searched")
    assert pe.pose_from_frames(_timed((0.5, dim)), lat=LAT, lon=LON, profile=PROFILE, orientation=None).kind == "not_moon"
    # The same Moon peaking at 200 is lined up
    bright_enough = _soft_moon(500.0, 700.0, peak=200.0)
    assert complete_sky(solver, clip, frames=lambda: [(0.5, bright_enough), (1.5, _noise())]).status == "approximate"


# --- P-SKY-5 -------------------------------------------------------------------------------------------------------


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
@given(
    fov=st.floats(80.0, 110.0),
    roll=st.floats(-10.0, 10.0),
    d_alt=st.floats(-45.0, 45.0),
    d_az=st.floats(-60.0, 60.0),
    turns=st.sampled_from([0, 1, 2, 3]),
    bloom=st.floats(1.0, 3.0),
    blur=st.floats(0.5, 2.5),
    peak=st.integers(200, 255),
    frame_time=st.floats(0.0, 15.0),
)
@example(fov=80.0, roll=-10.0, d_alt=15.0, d_az=-15.0, turns=0, bloom=1.0, blur=0.5, peak=255, frame_time=0.0)
@example(fov=110.0, roll=10.0, d_alt=-15.0, d_az=15.0, turns=0, bloom=3.0, blur=2.5, peak=200, frame_time=0.0)
@example(fov=80.0, roll=10.0, d_alt=-12.0, d_az=-12.0, turns=1, bloom=1.0, blur=2.0, peak=244, frame_time=7.5)
@example(fov=110.0, roll=-10.0, d_alt=12.0, d_az=12.0, turns=3, bloom=2.0, blur=1.0, peak=241, frame_time=14.0)
# The smallest Moon the profile allows (the widest field of view, no bloom) under the widest blur, at the floor
@example(fov=110.0, roll=0.0, d_alt=1.0, d_az=0.0, turns=0, bloom=1.0, blur=2.5, peak=200, frame_time=0.0)
# The Moon near a corner with the frame centre high, where the first-order start runs off and the fit needs the
# start lined up about the Moon itself
@example(fov=110.0, roll=10.0, d_alt=41.0, d_az=17.0, turns=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
@example(fov=110.0, roll=-10.0, d_alt=38.4, d_az=31.7, turns=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
def test_pbt_p_sky_5_a_synthetic_truth_is_covered_by_the_pose_and_its_sweep(
    fov: float, roll: float, d_alt: float, d_az: float, turns: int, bloom: float, blur: float, peak: int,
    frame_time: float,
) -> None:
    moment = frame_moment(MOON_MOMENT, frame_time)
    ra, dec, radius = _topocentric(moment)
    moon_alt, moon_az = icrs_to_altaz(ra, dec, LAT, LON, moment)
    truth = _truth_wcs(moon_alt + d_alt, moon_az + d_az, fov, roll, turns, moment)
    mx, my = truth.world_to_pixel(ra, dec)
    disk = radius / truth.scale_deg_per_px * bloom
    margin = disk + 5.0 * blur + 8.0
    assume(margin <= mx <= WIDTH - margin and margin <= my <= HEIGHT - margin)
    asset = _asset(turns=turns)
    luma = _soft_moon(mx, my, disk, peak=float(peak), blur=blur, seed=7)
    solution = complete_sky(_solver_solution(), asset, frames=lambda: [(frame_time, luma)])
    assert solution.status == "approximate", solution.warnings
    assert solution.frame_time_sec == frame_time and solution.observed_at_utc == moment
    pose = solution.pose_estimate
    assert pose is not None and len(pose.swept_wcs) == 9
    (moon_finding,) = [f for f in solution.findings if f.id == "finding-observed-moon"]
    cx, cy = moon_finding.source_detection.x_px, moon_finding.source_detection.y_px
    # The centroid is the drawn Moon's centre of light (each pixel's share of the disk was counted on a 5 x 5 grid)
    assert math.hypot(cx - mx, cy - my) < 0.1
    for wcs in (solution.wcs, *pose.swept_wcs):
        px, py = wcs.world_to_pixel(ra, dec)
        assert math.hypot(px - cx, py - cy) <= 0.5
    term_px = pose.beacon_centroid_uncertainty_deg / solution.wcs.scale_deg_per_px
    checked = 0
    for finding in solution.findings:
        if finding.evidence_class != "predicted":
            continue
        tx, ty = truth.world_to_pixel(finding.ra_deg, finding.dec_deg)
        if not (0.0 <= tx < WIDTH and 0.0 <= ty < HEIGHT):
            continue
        nx, ny = solution.wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        ring = max(math.hypot(x - nx, y - ny) for x, y in (w.world_to_pixel(finding.ra_deg, finding.dec_deg) for w in pose.swept_wcs))
        assert math.hypot(tx - nx, ty - ny) <= ring + term_px, finding.id
        checked += 1
    assert solution.quality.passed_independent_verification is False
    assert not any(f.evidence_class == "catalog_matched" for f in solution.findings)
    assert checked >= 1


# --- P-SKY-6 -------------------------------------------------------------------------------------------------------


def _assert_not_confirmed(luma: np.ndarray, detail: str) -> None:
    """No pose, with the rule in plain words; the Moon test itself refuses for `detail`."""
    refusal = find_moon_blob(luma, **_limits())
    assert isinstance(refusal, Refusal) and detail in refusal.reason, refusal
    solver = _solver_solution()
    out = complete_sky(solver, _asset(), frames=lambda: [(0.0, luma)])
    assert out.status == "predicted_only" and out.wcs is None and out.pose_estimate is None
    assert not any(f.id == "finding-observed-moon" for f in out.findings)
    assert out.warnings[-2] == NOT_CONFIRMED.format(where="in the frame searched")
    assert out.warnings[-1] == sky_phase(compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Sun",))[0].alt_deg).sentence


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(radius=st.floats(4.0, 20.0), ratio=st.floats(0.72, 1.0), blur=st.floats(0.5, 2.5), peak=st.integers(200, 255))
@example(radius=4.0, ratio=0.75, blur=2.5, peak=200)
@example(radius=20.0, ratio=1.0, blur=0.5, peak=255)
# At the first real head-worn clip's peak, two Moons of about the predicted size still refuse
@example(radius=5.0, ratio=0.9, blur=2.0, peak=244)
def test_pbt_p_sky_6_two_moons_of_comparable_area_give_no_pose(radius: float, ratio: float, blur: float, peak: int) -> None:
    first = _soft_moon(400.0, 500.0, radius, peak=float(peak), blur=blur, seed=2)
    luma = _soft_moon(900.0, 1300.0, radius * ratio, peak=float(peak), blur=blur, luma=first)
    # Written out independently: the two regions above the half-peak level, the smaller one at least half the
    # larger, and the brightest one inside the size limits (so only the rival can refuse it)
    _, _, _, _, labels, region = _rule(luma)
    areas = sorted(int((labels == labels[y, x]).sum()) for x, y in ((400, 500), (900, 1300)))
    limits = _limits()
    assume(labels[500, 400] != labels[1300, 900] and areas[0] >= 0.5 * areas[1])
    assume(limits["min_diameter_px"] <= 2.0 * math.sqrt(region.sum() / math.pi) <= limits["max_diameter_px"])
    _assert_not_confirmed(luma, "another spot rises above half the brightest one's peak with half its area or more")


@settings(max_examples=15, deadline=None)
@given(lights=st.lists(st.tuples(st.floats(0.5, 2.0), st.integers(200, 255)), min_size=1, max_size=6))
# The point light of the Moon test's own test, at the first real head-worn clip's peak, and at the floor
@example(lights=[(2.0, 244)])
@example(lights=[(2.0, 200)])
@example(lights=[(0.5, 255), (2.0, 255)])
def test_pbt_p_sky_6_only_point_lights_smaller_than_the_moon_test_allows_give_no_pose(lights: list[tuple[float, int]]) -> None:
    luma = _noise(4)
    for i, (blur, peak) in enumerate(lights):
        luma = _point_light(luma, 100 + 200 * i, 200 + 250 * i, peak=float(peak), blur=blur)
    region = _rule(luma)[5]
    assert 2.0 * math.sqrt(region.sum() / math.pi) < _limits()["min_diameter_px"]
    _assert_not_confirmed(luma, "px across at half its peak, under the")


# --- The approximate solution ----------------------------------------------------------------------------------------


def test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky() -> None:
    frame_time = 3.0
    moment = frame_moment(MOON_MOMENT, frame_time)
    luma = _frame(500.0, 700.0, 8.0)
    blob = _pose(luma, frame_time=frame_time).blob
    bx, by, r = blob.x_px, blob.y_px, blob.radius_px
    detections = [
        # On the Moon in the same frame: left out, inside the blob's radius
        SourceDetection(bx + (r - 0.05), by, 900.0, 40.0, 3.0, 0.1, asset_id=ASSET_ID, frame_time_sec=frame_time),
        # Just outside it in the same frame, and on the Moon's pixel in another frame: both kept
        SourceDetection(bx, by + (r + 0.05), 50.0, 8.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=frame_time),
        SourceDetection(bx, by, 60.0, 9.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0),
        SourceDetection(1200.0, 100.0, 70.0, 10.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0),
    ]
    solver = _solver_solution(detections, "The plate solve found too few stars.")
    before = [f.to_contract_dict(source_asset_id=ASSET_ID) for f in solver.findings]
    out = complete_sky(solver, _asset(), frames=lambda: [(frame_time, luma)])
    assert [f.to_contract_dict(source_asset_id=ASSET_ID) for f in solver.findings] == before
    assert out.status == "approximate" and out.frame_time_sec == frame_time and out.observed_at_utc == moment
    assert (out.observer_lat, out.observer_lon) == (LAT, LON)
    assert out.wcs is not None and out.wcs in out.pose_estimate.swept_wcs
    q = out.quality
    assert (q.detected_candidate_count, q.matched_inlier_count, q.held_out_pair_count, q.held_out_inliers) == (4, 1, 0, 0)
    assert (q.median_residual_px, q.p95_residual_px, q.median_residual_arcsec, q.passed_independent_verification) == (None, None, None, False)
    observed = [f for f in out.findings if f.evidence_class == "observed"]
    kept = [f.source_detection for f in observed if f.id != "finding-observed-moon"]
    assert kept == detections[1:]
    assert all(not f.has_sky_position for f in observed)
    (moon,) = [f for f in observed if f.id == "finding-observed-moon"]
    d = moon.source_detection
    assert (d.x_px, d.y_px, d.flux, d.snr, d.fwhm_px) == (blob.x_px, blob.y_px, blob.flux, blob.snr, blob.fwhm_px)
    assert d.frame_time_sec == frame_time and d.asset_id == ASSET_ID
    assert any("other frames" in text for text in moon.limitations)
    assert not any(f.evidence_class == "catalog_matched" for f in out.findings)
    predicted = [f for f in out.findings if f.evidence_class == "predicted"]
    expected = predicted_sky_findings(moment, LAT, LON, solution_id=solver.id, source_sha256=SHA)
    assert [f.id for f in predicted] == [f.id for f in expected]
    inside = 0
    for finding, plain in zip(predicted, expected):
        assert (finding.ra_deg, finding.dec_deg, finding.alt_deg, finding.az_deg) == (plain.ra_deg, plain.dec_deg, plain.alt_deg, plain.az_deg)
        x, y = out.wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        in_frame = math.isfinite(x) and math.isfinite(y) and 0.0 <= x < WIDTH and 0.0 <= y < HEIGHT
        assert finding.subtitle == plain.subtitle + (" · Inside the frame (lined up, approximate)" if in_frame else " · Outside the frame")
        assert finding.source_detection is None
        if in_frame:
            inside += 1
            assert any("approximate pose" in text for text in finding.limitations)
    assert inside >= 1 and any(f.id == "finding-predicted-moon" for f in predicted)
    # The plate solver's warning, then how it was lined up, then where the Sun was
    assert out.warnings[0] == "The plate solve found too few stars."
    lined_up = out.warnings[1]
    pose = out.pose_estimate
    within = math.ceil(pose.max_center_offset_deg + pose.beacon_centroid_uncertainty_deg)
    assert f"({blob.x_px:.0f}, {blob.y_px:.0f}) px" in lined_up and "3.00 s into the clip" in lined_up
    assert f"within about {within} degrees near the Moon, more toward the edges; not checked against stars" in lined_up
    assert "80 to 110 degrees across the diagonal with no zoom, and a level head" in lined_up
    (sun,) = compute_solar_system_ephemerides(moment, LAT, LON, bodies=("Sun",))
    assert out.warnings[2] == sky_phase(sun.alt_deg).sentence and len(out.warnings) == 3
    for text in out.warnings:
        _capture_free(text)
    assert out.solver is not None and out.solver.name == "AlbedianHeadWornMoonPose"
    ObservationSolution(**{**out.__dict__})


def test_a_photo_is_lined_up_from_its_one_frame_and_says_so() -> None:
    out = complete_sky(_solver_solution(), _asset(kind="image"), frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
    assert out.status == "approximate" and out.frame_time_sec == 0.0 and out.observed_at_utc == MOON_MOMENT
    assert "in the photo" in out.warnings[1] and "into the clip" not in out.warnings[1]
    (moon,) = [f for f in out.findings if f.id == "finding-observed-moon"]
    assert not any("other frames" in text for text in moon.limitations)


# --- No pose -------------------------------------------------------------------------------------------------------


def _assert_sky_2_plus(out: ObservationSolution, solver: ObservationSolution, asset: dict, reason: str) -> None:
    """Exactly sky-2's predicted_only result (complete_sky given no frames, layer 1 alone), with the reason before
    the sky phase sentence."""
    sky_2 = complete_sky(solver, asset)
    assert sky_2.status == "predicted_only" and len(sky_2.warnings) == len(solver.warnings) + 1
    assert out.status == "predicted_only" and out.wcs is None and out.pose_estimate is None
    assert out.findings == sky_2.findings and out.quality == sky_2.quality and out.solver == sky_2.solver
    assert out.observed_at_utc == sky_2.observed_at_utc and out.frame_time_sec == sky_2.frame_time_sec
    assert out.warnings == [*sky_2.warnings[:-1], reason, sky_2.warnings[-1]]
    _capture_free(reason)


def test_without_a_head_worn_profile_there_is_no_pose_and_the_reason_is_given() -> None:
    solver = _solver_solution()
    for model in (None, "iPhone 17 Pro"):
        asset = _asset(model=model)
        out = complete_sky(solver, asset, frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
        assert out.status == "predicted_only" and out.pose_estimate is None
        assert out.warnings == [*solver.warnings, pe.NO_PROFILE, out.warnings[-1]]
        assert pe.NO_PROFILE == (
            "The sky is not lined up over the picture: the camera has no head-worn camera profile, so its field of "
            "view is not known."
        )


def test_with_the_moon_down_there_is_no_pose_and_no_frame_is_read() -> None:
    (moon,) = compute_solar_system_ephemerides(MOON_DOWN_MOMENT, LAT, LON, bodies=("Moon",))
    assert moon.alt_deg <= 5.0
    solver = replace(_solver_solution(), observed_at_utc=MOON_DOWN_MOMENT)
    asset = _asset(MOON_DOWN_MOMENT)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(solver, asset, frames=frames)
    _assert_sky_2_plus(out, solver, asset, pe.MOON_NOT_UP)
    assert "5 degrees" in pe.MOON_NOT_UP and calls == []
    # Checked again at each frame's own moment
    assert pe.pose_from_frames(
        [(0.0, MOON_DOWN_MOMENT, _frame(500.0, 700.0, 8.0))], lat=LAT, lon=LON, profile=PROFILE, orientation=None
    ).reason == pe.MOON_NOT_UP


def test_a_moon_only_5_degrees_up_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pe.MIN_MOON_ALT_DEG == 5.0
    (moon,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    monkeypatch.setattr(pe, "MIN_MOON_ALT_DEG", moon.alt_deg)
    assert isinstance(_pose(_frame(500.0, 700.0, 8.0)), Refusal)
    monkeypatch.setattr(pe, "MIN_MOON_ALT_DEG", moon.alt_deg - 1e-6)
    assert isinstance(_pose(_frame(500.0, 700.0, 8.0)), PoseResult)


def test_a_spot_not_confirmed_as_the_moon_gives_no_pose_with_the_reason() -> None:
    solver = _solver_solution()
    asset = _asset()
    luma = _rects((300, 400, 12, 12), (800, 1200, 12, 12))
    out = complete_sky(solver, asset, frames=lambda: [(0.5, luma), (1.5, luma)])
    reason = out.warnings[-2]
    assert reason == NOT_CONFIRMED.format(where="in the 2 frames searched")
    _assert_sky_2_plus(out, solver, asset, reason)


def test_a_refused_fit_gives_no_pose_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "MAX_FIT_RESIDUAL_PX", -1.0)
    solver = _solver_solution()
    asset = _asset()
    out = complete_sky(solver, asset, frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
    reason = out.warnings[-2]
    assert "(500, 700) px" in reason and "confirmed as the Moon, but no level-head pose" in reason
    _assert_sky_2_plus(out, solver, asset, reason)


def test_a_mirrored_frame_or_no_frame_read_gives_no_pose() -> None:
    solver = _solver_solution()
    mirrored = _asset(mirrored=True)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(solver, mirrored, frames=frames)
    _assert_sky_2_plus(out, solver, mirrored, pe.MIRRORED)
    # Refused before any frame is read, and refused again by the frame search itself
    assert calls == []
    assert pe.pose_from_frames(
        [(0.0, MOON_MOMENT, _frame(500.0, 700.0, 8.0))], lat=LAT, lon=LON, profile=PROFILE,
        orientation={"quarterTurnsClockwise": 0, "mirrored": True},
    ).reason == pe.MIRRORED
    asset = _asset()
    _assert_sky_2_plus(complete_sky(solver, asset, frames=lambda: []), solver, asset, pe.NO_FRAMES)


def test_given_no_frames_complete_sky_is_layer_1_alone() -> None:
    solver = _solver_solution()
    out = complete_sky(solver, _asset())
    assert out.status == "predicted_only" and out.pose_estimate is None
    assert out.warnings == [*solver.warnings, out.warnings[-1]] and "USNO" in out.warnings[-1]


def test_a_calibrated_solution_never_takes_the_pose_path() -> None:
    from services.science.solver import detect_point_sources, render_synthetic_starfield, solve_and_verify_field

    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    calibrated = solve_and_verify_field(
        detect_point_sources(frame, snr_threshold=4.0), 960, 540, obs_utc_iso=MOON_MOMENT, observer_lat=LAT,
        observer_lon=LON, source_asset_id=ASSET_ID, source_sha256=SHA, solution_id="sol-calibrated",
    )
    assert calibrated.status == "calibrated"
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(calibrated, _asset(), frames=frames)
    assert out.status == "calibrated" and out.wcs == calibrated.wcs and out.quality == calibrated.quality
    assert out.pose_estimate is None and calls == []
    assert out.findings[: len(calibrated.findings)] == calibrated.findings
    assert pe.NO_PROFILE not in out.warnings


def test_without_a_time_or_place_no_frame_is_read() -> None:
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return []

    solver = replace(_solver_solution(), observed_at_utc=None, status="unresolved")
    asset = _asset()
    del asset["capturedAtUTC"]
    out = complete_sky(solver, asset, frames=frames)
    assert out.status == "unresolved" and calls == []
