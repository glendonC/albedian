"""association-grow-one: the grown first association as a second look, only where the first look does not pass.

The first look (the training split, refinement and held-out gate on the first association) runs as before. Only when
it does not pass, or has too few pairs to try, is the association grown (refit rigidly on its pairs, associate again
within the same radius while the count grows) and the same split, refinement and gate run once more on the grown
pairs, the refinement starting from the search's own pose. The second look is taken only when it passes.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np
import pytest

from services.science import phone_photo_solver as pps
from services.science.catalog import ALL_SKY_CATALOG_STARS, CatalogStar
from services.science.wcs_models import FrameMeasures, SourceDetection

WIDTH, HEIGHT = 5392, 3592
UTC, LAT, LON = "2031-01-15T04:00:00Z", 28.5, -17.8
TRUE_FOV_DEG = 81.1


def _no_naming_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    # Synthetic star ids have no deep tile to refuse a name against
    monkeypatch.setattr(pps, "naming_refusals", lambda wcs, named, gate_px, checked_xy, epoch_jyear, deep=None: [None] * len(named))


def _camera(rot: np.ndarray, fov_deg: float) -> pps.PhoneCameraWCS:
    return pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot, fov_diagonal_deg=fov_deg, width=WIDTH, height=HEIGHT, lens_model="brown-conrady",
        sky_frame=pps._ApparentSkyFrame.from_iso(UTC, LAT, LON),
    )


def _rolled(deg: float) -> np.ndarray:
    """The zenith camera (rows right, down, axis in east, north, up) turned by `deg` about its own axis."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64) @ np.eye(3, dtype=np.float64)


def _star(idx: int, ra: float, dec: float) -> CatalogStar:
    return replace(
        ALL_SKY_CATALOG_STARS[0], id=f"grow-star-{idx:03d}", common_name=f"GrowStar{idx:03d}", bayer_designation=None,
        flamsteed_number=None, names_file_designation=None, catalog_source_id=f"HIP {910000 + idx}",
        ra_deg=ra % 360.0, dec_deg=dec, mag=2.0 + 0.05 * idx, pm_ra_mas_yr=None, pm_dec_mas_yr=None,
    )


def _detection(idx: int, x: float, y: float) -> SourceDetection:
    return SourceDetection(
        x_px=x, y_px=y, flux=900.0 - 10.0 * idx, snr=40.0, fwhm_px=3.1, centroid_uncertainty_px=0.08,
        asset_id="src_grow", frame_time_sec=0.0, frame=FrameMeasures(),
    )


def _field() -> tuple[list[SourceDetection], list[CatalogStar]]:
    """Four stars within 400 px of the centre and a ring of 30 from 900 px out, measured by a 0.3 px centroid noise.

    A candidate rolled 1.9 deg with a field 4% too wide is off by up to about 26 px at 400 px from the centre and
    by about 100 px and more at the edges: its first association holds only the four near the centre."""
    true_wcs = _camera(np.eye(3, dtype=np.float64), TRUE_FOV_DEG)
    cx, cy = (WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0
    pixels = [(cx + 160.0, cy + 40.0), (cx - 120.0, cy - 220.0), (cx - 300.0, cy + 150.0), (cx + 250.0, cy - 290.0)]
    for gx in np.linspace(300.0, WIDTH - 300.0, 7):
        for gy in np.linspace(250.0, HEIGHT - 250.0, 5):
            if math.hypot(gx - cx, gy - cy) >= 900.0:
                pixels.append((float(gx), float(gy)))
    rng = np.random.default_rng(7)
    dets, stars = [], []
    for idx, (x, y) in enumerate(pixels):
        ra, dec = true_wcs.pixel_to_world(x, y)
        stars.append(_star(idx, float(ra), float(dec)))
        nx, ny = rng.normal(0.0, 0.3, size=2)
        dets.append(_detection(idx, x + float(nx), y + float(ny)))
    return dets, stars


def _seed(rot: np.ndarray, fov_deg: float) -> list[tuple[float, float]]:
    wcs = _camera(rot, fov_deg)
    cx, cy = (WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0
    return [wcs.pixel_to_world(cx, cy), wcs.pixel_to_world(cx + 20.0, cy), wcs.pixel_to_world(cx, cy + 20.0)]


def _solve(dets: list[SourceDetection], stars: list[CatalogStar], seed: list[tuple[float, float]], fov_deg: float):
    return pps.solve_phone_photo(
        np.full((HEIGHT, WIDTH), 10, dtype=np.uint8), detections=dets, obs_utc_iso=UTC, observer_lat=LAT,
        observer_lon=LON, fov_diagonal_deg=fov_deg, catalog_stars=stars, source_asset_id="src_grow",
        source_sha256="c" * 64, seed_axes_icrs=seed,
    )


def test_a_candidate_rolled_and_a_field_off_calibrates_on_the_grown_second_look(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    dets, stars = _field()
    wide = TRUE_FOV_DEG * 1.04
    sol = _solve(dets, stars, _seed(_rolled(1.9), wide), wide)
    assert sol.status == "calibrated", (sol.status, sol.quality)
    assert sol.quality.held_out_inliers >= pps.MIN_HELD_OUT_INLIERS
    assert sol.quality.median_residual_px is not None and sol.quality.median_residual_px <= pps.MAX_HELD_OUT_MEDIAN_PX
    true_wcs = _camera(np.eye(3, dtype=np.float64), TRUE_FOV_DEG)
    got = sol.wcs.pixel_to_world((WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0)
    want = true_wcs.pixel_to_world((WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0)
    assert math.hypot((got[0] - want[0]) * math.cos(math.radians(want[1])), got[1] - want[1]) < 0.05


def test_the_second_look_trains_from_the_searchs_own_pose_on_more_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    dets, stars = _field()
    looks: list[tuple[int, int, bool]] = []
    real_look = pps._held_out_look

    def spying(initial_wcs, first_pairs, *args, **kwargs):
        look = real_look(initial_wcs, first_pairs, *args, **kwargs)
        looks.append((id(initial_wcs), len(first_pairs), bool(look.passed_gate)))
        return look

    grown: list[list[tuple[int, int, float]]] = []
    grown_from: list[int] = []
    real_grow = pps._grow_first_association

    def growing(wcs, *args, **kwargs):
        pairs = real_grow(wcs, *args, **kwargs)
        grown.append(pairs)
        grown_from.append(id(wcs))
        return pairs

    monkeypatch.setattr(pps, "_held_out_look", spying)
    monkeypatch.setattr(pps, "_grow_first_association", growing)
    wide = TRUE_FOV_DEG * 1.04
    sol = _solve(dets, stars, _seed(_rolled(1.9), wide), wide)
    assert sol.status == "calibrated"
    # The helper hands back pairs and nothing else, so no grown pose reaches the training
    assert len(grown) == 1 and isinstance(grown[0], list) and all(len(pair) == 3 for pair in grown[0])
    # Four pairs at the first association are too few to look at; the one look is on the grown pairs, and it trains
    # from the very pose the search gave, the one the growth started from
    assert len(looks) == 1 and looks[0][1] == len(grown[0]) > 6 and looks[0][2]
    assert looks[0][0] == grown_from[0]


def test_a_look_that_passes_is_never_grown(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    dets, stars = _field()

    def never(*args, **kwargs):
        raise AssertionError("the first association was grown after a look that passed")

    monkeypatch.setattr(pps, "_grow_first_association", never)
    sol = _solve(dets, stars, _seed(np.eye(3, dtype=np.float64), TRUE_FOV_DEG), TRUE_FOV_DEG)
    assert sol.status == "calibrated", (sol.status, sol.quality)


def test_a_wrong_sky_stays_unresolved_after_its_second_look(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    true_wcs = _camera(np.eye(3, dtype=np.float64), TRUE_FOV_DEG)
    rng = np.random.default_rng(5)
    # Detections and catalog stars scattered independently: no pose lines them up. Dense enough that chance pairs
    # give a first look (10 pairs) and the growth finds more by chance (15), so both looks run
    dets = [_detection(i, float(rng.uniform(60, WIDTH - 60)), float(rng.uniform(60, HEIGHT - 60))) for i in range(85)]
    stars = []
    for i in range(400):
        ra, dec = true_wcs.pixel_to_world(float(rng.uniform(10, WIDTH - 10)), float(rng.uniform(10, HEIGHT - 10)))
        stars.append(_star(i, float(ra), float(dec)))
    looks: list[tuple[bool, int]] = []
    real_look = pps._held_out_look

    def spying(initial_wcs, first_pairs, *args, **kwargs):
        look = real_look(initial_wcs, first_pairs, *args, **kwargs)
        looks.append((bool(look.passed_gate), len(look.held_out_residuals_all)))
        return look

    monkeypatch.setattr(pps, "_held_out_look", spying)
    sol = _solve(dets, stars, _seed(np.eye(3, dtype=np.float64), TRUE_FOV_DEG), TRUE_FOV_DEG)
    assert sol.status != "calibrated"
    assert len(looks) == 2 and not any(passed for passed, _ in looks), looks
    # A second look that does not pass is dropped: the solution reports the first look's held-out pairs
    assert sol.quality.held_out_pair_count == looks[0][1] != looks[1][1]


def test_the_held_out_bounds_are_unchanged() -> None:
    assert pps.MAX_HELD_OUT_MEDIAN_PX == 3.0
    assert pps.MIN_HELD_OUT_INLIERS == 6
    assert pps.FIRST_ASSOCIATION_GROW_ROUNDS == 4


def test_the_growth_associates_within_the_first_radius_only() -> None:
    true_wcs = _camera(np.eye(3, dtype=np.float64), TRUE_FOV_DEG)
    dets, stars = _field()
    radius_px = 28.0
    # One detection moved 1.5 radii from its star: inside twice the radius, outside the first radius
    moved = 12
    dets[moved] = replace(dets[moved], x_px=dets[moved].x_px + 1.5 * radius_px)
    cat_ra = np.array([s.ra_deg for s in stars], dtype=np.float64)
    cat_dec = np.array([s.dec_deg for s in stars], dtype=np.float64)
    near = [(i, i, 0.0) for i in range(4)] + [(i, i, 0.0) for i in (5, 9, 20)]
    grown = pps._grow_first_association(true_wcs, dets, cat_ra, cat_dec, near, radius_px)
    assert len(grown) > len(near)
    assert all(res_px <= radius_px for _, _, res_px in grown)
    assert moved not in {d_i for d_i, _, _ in grown}
