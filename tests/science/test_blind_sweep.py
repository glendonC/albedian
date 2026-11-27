"""blind-sweep-one: the blind search swept over fields of view when its start's pose does not pass the gate.

The blind search scored poses at one field only, the start's (`fov_models[0]`), and a few percent of field outruns its
0.42 deg count: the right pose scored 0 of 24 at a start 6.6 percent wide and 22 at the right field (blind-search-zero).
Only when the app's own blind search on the whole sky's tile stars found no pose, or one the held-out gate refused, are
its fields swept (0.40 to 1.47 of the start, 3 percent apart, the start's own left out, the nearest first) and up to
three best-scored poses across the fields looked at in turn through the unchanged first look and gate, the first that
passes kept. These tests draw real tile stars through a known camera, so the search, the sweep and the gate read the
catalog they read on a photo.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from services.science import phone_photo_solver as pps
from services.science.catalog import ALL_SKY_CATALOG_STARS
from services.science.wcs_models import FrameMeasures, SourceDetection

WIDTH, HEIGHT = 1600, 1200
TRUE_FOV_DEG = 70.0
ASSET, SHA = "src_blindsweep01", "d" * 64


def _camera(ra_deg: float, dec_deg: float, roll_deg: float, fov_deg: float) -> pps.PhoneCameraWCS:
    forward = pps._radec_to_vec_batch(np.array([ra_deg]), np.array([dec_deg]))[0]
    right = np.cross(np.array([0.0, 0.0, 1.0]), forward)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    c, s = math.cos(math.radians(roll_deg)), math.sin(math.radians(roll_deg))
    rot = np.array([c * right + s * down, -s * right + c * down, forward])
    return pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=rot, fov_diagonal_deg=fov_deg, width=WIDTH, height=HEIGHT, lens_model="brown-conrady",
    )


def _sky(ra_deg: float = 84.0, dec_deg: float = -2.0, roll_deg: float = 17.0, seed: int = 5) -> list[SourceDetection]:
    """The tile stars to V 6.5 inside a frame of the true field, as detections 0.25 px from where the camera puts them,
    each as bright as its magnitude."""
    tiles = pps._get_tile_index()
    _, ra, dec, mag = tiles.query_bright(v_limit=6.5, max_stars=8000, epoch_jyear=tiles.epoch_jyear)
    px, py = _camera(ra_deg, dec_deg, roll_deg, TRUE_FOV_DEG).world_to_pixel_batch(ra, dec)
    inside = np.isfinite(px) & (px > 12.0) & (px < WIDTH - 12.0) & (py > 12.0) & (py < HEIGHT - 12.0)
    rng = np.random.default_rng(seed)
    dets = []
    for x, y, m in zip(px[inside], py[inside], np.asarray(mag)[inside]):
        nx, ny = rng.normal(0.0, 0.25, size=2)
        dets.append(SourceDetection(
            x_px=float(x + nx) + 0.37, y_px=float(y + ny) + 0.41, flux=float(4000.0 * 10 ** (-0.4 * m)), snr=30.0,
            fwhm_px=2.5, centroid_uncertainty_px=0.1, asset_id=ASSET, frame=FrameMeasures(),
        ))
    return sorted(dets, key=lambda d: -d.flux)


def _wrong_sky(seed: int = 11, count: int = 90) -> list[SourceDetection]:
    rng = np.random.default_rng(seed)
    return sorted((
        SourceDetection(
            x_px=float(rng.uniform(20, WIDTH - 20)) + 0.37, y_px=float(rng.uniform(20, HEIGHT - 20)) + 0.41,
            flux=float(rng.lognormal(3.0, 1.0)), snr=30.0, fwhm_px=2.5, centroid_uncertainty_px=0.1, asset_id=ASSET,
            frame=FrameMeasures(),
        ) for _ in range(count)
    ), key=lambda d: -d.flux)


def _solve(dets: list[SourceDetection], fov_deg: float, **extra: Any) -> Any:
    return pps.solve_phone_photo(
        np.full((HEIGHT, WIDTH), 10, dtype=np.uint8), detections=dets, fov_diagonal_deg=fov_deg,
        source_asset_id=ASSET, source_sha256=SHA, **extra,
    )


def _centre_off_deg(solution: Any) -> float:
    got = solution.wcs.pixel_to_world((WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0)
    want = _camera(84.0, -2.0, 17.0, TRUE_FOV_DEG).pixel_to_world((WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0)
    return float(np.degrees(np.arccos(np.clip(
        pps._radec_to_vec_batch(np.array([got[0]]), np.array([got[1]]))[0]
        @ pps._radec_to_vec_batch(np.array([want[0]]), np.array([want[1]]))[0], -1.0, 1.0))))


def _no_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The solver as it was: the start's pose or none, with no sweep."""
    monkeypatch.setattr(pps, "_blind_sweep_poses", lambda *args, **kwargs: [])


def _spying(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    seen: dict[str, list] = {"sweeps": [], "looks": []}
    real_sweep, real_look = pps._blind_sweep_poses, pps._look_at_pose

    def sweep(*args: Any, **kwargs: Any) -> Any:
        poses = real_sweep(*args, **kwargs)
        seen["sweeps"].append([p.fov_diagonal_deg for p in poses])
        return poses

    def look(initial_wcs: Any, **kwargs: Any) -> Any:
        looked = real_look(initial_wcs, **kwargs)
        seen["looks"].append((initial_wcs.fov_diagonal_deg, looked.look is not None and looked.look.passed_gate))
        return looked

    monkeypatch.setattr(pps, "_blind_sweep_poses", sweep)
    monkeypatch.setattr(pps, "_look_at_pose", look)
    return seen


def test_the_fields_run_from_the_start_outward_over_point_four_to_one_and_a_half() -> None:
    factors = pps.BLIND_SWEEP_FACTORS
    assert len(factors) == 44 and len(set(factors)) == 44
    assert min(factors) == 0.4 and 1.46 < max(factors) < 1.5
    assert all(abs(math.log(a)) <= abs(math.log(b)) for a, b in zip(factors, factors[1:]))
    steps = sorted(factors)
    # 3 percent apart, but across the start's own field, which the start's search has scored
    assert all(1.029 < b / a < 1.031 for a, b in zip(steps, steps[1:]) if not a < 1.0 < b)
    assert factors[:2] == (0.9709, 1.03) and 1.0 not in factors
    assert pps.BLIND_MIN_SCORE == 7 and pps.BLIND_SWEEP_STANDS_OUT == 1.5 and pps.BLIND_SWEEP_APART_DEG == 5.0


def test_a_start_six_percent_wide_calibrates_on_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _sky()
    wide = TRUE_FOV_DEG * 1.06
    seen = _spying(monkeypatch)
    solution = _solve(dets, wide)
    assert solution.status == "calibrated", (solution.status, solution.quality)
    assert solution.quality.held_out_inliers >= pps.MIN_HELD_OUT_INLIERS
    assert solution.quality.median_residual_px <= pps.MAX_HELD_OUT_MEDIAN_PX
    assert _centre_off_deg(solution) < 0.1
    assert seen["sweeps"] == [[pytest.approx(seen["looks"][-1][0])]]
    assert seen["looks"][-1][1] and not any(passed for _, passed in seen["looks"][:-1])
    # The start alone does not calibrate it: the sweep is what does
    _no_sweep(monkeypatch)
    assert _solve(dets, wide).status != "calibrated"


def test_a_pose_that_passes_at_the_start_is_kept_and_no_sweep_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _sky()
    seen = _spying(monkeypatch)
    solution = _solve(dets, TRUE_FOV_DEG)
    assert solution.status == "calibrated" and _centre_off_deg(solution) < 0.1
    assert seen["sweeps"] == [] and len(seen["looks"]) == 1
    _no_sweep(monkeypatch)
    assert _solve(dets, TRUE_FOV_DEG).to_contract_dict() == solution.to_contract_dict()


def test_a_wrong_sky_stays_unresolved_and_reads_as_the_start_left_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dets = _wrong_sky()
    seen = _spying(monkeypatch)
    solution = _solve(dets, TRUE_FOV_DEG)
    assert solution.status != "calibrated" and solution.wcs is None
    assert len(seen["sweeps"]) == 1 and len(seen["sweeps"][0]) <= 1
    assert len(seen["looks"]) <= 2 and not any(passed for _, passed in seen["looks"])
    _no_sweep(monkeypatch)
    assert solution.to_contract_dict() == _solve(dets, TRUE_FOV_DEG).to_contract_dict()


def _sweep_with(monkeypatch: pytest.MonkeyPatch, scores: list[int], axes_ra: list[float]) -> tuple[list, list[float]]:
    """The sweep with each field's best given: the score, and a pose whose axis is at that right ascension on the
    equator (none for a score of 0)."""
    dets = _wrong_sky(seed=3)
    given = iter(zip(scores + [0] * 60, axes_ra + [0.0] * 60))
    fields: list[float] = []

    def best(d_rays: Any, tris: Any, width: int, height: int, fov_deg: float, lens: str, counts: Any) -> Any:
        fields.append(fov_deg)
        score, ra_deg = next(given)
        return score, (None if score == 0 else _camera(ra_deg, 0.0, 0.0, fov_deg))

    monkeypatch.setattr(pps, "_blind_best_pose", best)
    tiles = pps._get_tile_index()
    ra, dec = pps._blind_search_catalog(tiles, tiles.epoch_jyear)
    return pps._blind_sweep_poses(dets, WIDTH, HEIGHT, ra, dec, (60.0, "brown-conrady"), None, 0), fields


def test_the_best_pose_is_kept_where_it_scores_half_again_the_best_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    # 15 at the second field; 14 at the first, its pose 3 deg away (the same place at a neighbouring field, not
    # counted); 10 and 9 more than 5 deg away: 15 is 1.5 times 10
    poses, fields = _sweep_with(monkeypatch, [14, 15, 10, 9], [0.0, 3.0, 40.0, 80.0])
    assert [round(p.fov_diagonal_deg, 4) for p in poses] == [round(fields[1], 4)]
    assert fields[0] == pytest.approx(60.0 * 0.9709) and fields[1] == pytest.approx(60.0 * 1.03)


def test_the_best_pose_is_refused_where_another_place_comes_near(monkeypatch: pytest.MonkeyPatch) -> None:
    # 15 against 11 elsewhere is under 1.5 times: no pose, so the start's solve stands
    poses, _ = _sweep_with(monkeypatch, [14, 15, 11, 9], [0.0, 3.0, 40.0, 80.0])
    assert poses == []
    # A pose 5.5 deg away is elsewhere
    poses, _ = _sweep_with(monkeypatch, [12, 15], [5.5, 0.0])
    assert poses == []


def test_on_a_tie_the_nearer_field_is_the_best(monkeypatch: pytest.MonkeyPatch) -> None:
    # 12 at the second and the fourth field, 1 deg apart; 7 elsewhere
    poses, fields = _sweep_with(monkeypatch, [7, 12, 5, 12], [0.0, 30.0, 60.0, 31.0])
    assert [round(p.fov_diagonal_deg, 4) for p in poses] == [round(fields[1], 4)]


def test_a_field_that_reaches_the_searchs_stopping_count_ends_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _wrong_sky(seed=3)
    scores = iter([8, 21, 30, 30])
    fields: list[float] = []

    def best(d_rays: Any, tris: Any, width: int, height: int, fov_deg: float, lens: str, counts: Any) -> Any:
        fields.append(fov_deg)
        return next(scores), _camera(0.0, 0.0, 0.0, fov_deg)

    monkeypatch.setattr(pps, "_blind_best_pose", best)
    tiles = pps._get_tile_index()
    ra, dec = pps._blind_search_catalog(tiles, tiles.epoch_jyear)
    poses = pps._blind_sweep_poses(dets, WIDTH, HEIGHT, ra, dec, (60.0, "brown-conrady"), None, 0)
    # 24 detections stop at 21, the blind search's own max(10, 24 - 3)
    assert len(fields) == 2 and [round(p.fov_diagonal_deg, 4) for p in poses] == [round(fields[1], 4)]


@pytest.mark.parametrize("fov_deg", [30.0, 52.5, 70.0, 88.0, 110.0])
def test_the_shared_triangles_are_each_fields_own(fov_deg: float) -> None:
    tiles = pps._get_tile_index()
    ra, dec = pps._blind_search_catalog(tiles, tiles.epoch_jyear)
    reach = min(0.85 * fov_deg, 75.0)
    own = pps._blind_catalog_triangles(ra, dec, reach)
    cut = pps._blind_catalog_triangles(ra, dec, min(0.85 * 123.6, 75.0)).within(reach)

    def rows(t: Any) -> set:
        return {(int(a), int(b), int(c)) for a, b, c in zip(t.v0, t.v1, t.v2)}

    assert rows(cut) == rows(own) and own.long.size > 0


def test_the_count_grows_through_the_sweep_and_never_falls() -> None:
    dets = _sky()
    counts: list[tuple[int, int]] = []
    _solve(dets, TRUE_FOV_DEG * 1.06, on_candidate=lambda done, total: counts.append((done, total)))
    assert counts and all(0 <= d <= t for d, t in counts)
    assert all(b[0] >= a[0] and b[1] >= a[1] for a, b in zip(counts, counts[1:]))
    assert counts[-1][1] > counts[0][1]
    # The start's search reports each candidate; the sweep once at its start and once a field
    start_total = counts[0][1]
    swept = [c for c in counts if c[1] > start_total]
    assert 1 <= len(swept) <= len(pps.BLIND_SWEEP_FACTORS) + 1
    per_field = start_total
    assert swept[0] == (start_total, start_total + per_field * len(pps.BLIND_SWEEP_FACTORS))


def test_only_the_apps_own_blind_search_on_the_tile_stars_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _sky()
    seen = _spying(monkeypatch)
    # A custom catalog (research and tests), and a horizon search with a time and a place: no sweep
    _solve(dets, TRUE_FOV_DEG * 1.06, catalog_stars=list(ALL_SKY_CATALOG_STARS)[:40])
    _solve(dets[:30], TRUE_FOV_DEG * 1.06, obs_utc_iso="2031-03-20T12:00:00Z", observer_lat=20.0, observer_lon=-140.0)
    # A candidate handed in (the astrometry.net stage's check, the stored-pixel handover): no sweep
    pps._solve_phone_photo_with(
        np.full((HEIGHT, WIDTH), 10, dtype=np.uint8), attitude_prior=pps._attitude_prior_takes, detections=dets,
        fov_diagonal_deg=TRUE_FOV_DEG, source_asset_id=ASSET, source_sha256=SHA,
        _preset_initial_wcs=_camera(10.0, 40.0, 0.0, TRUE_FOV_DEG),
    )
    assert seen["sweeps"] == []
    # The whole-sky pass of a photo with its own moment is the blind search, and sweeps
    _solve(dets, TRUE_FOV_DEG * 1.06, obs_utc_iso="2031-03-20T12:00:00Z", observer_lat=20.0, observer_lon=-140.0,
           whole_sky=True)
    assert len(seen["sweeps"]) == 1


def test_a_best_scoring_seven_gives_a_pose_and_one_scoring_six_none(monkeypatch: pytest.MonkeyPatch) -> None:
    poses, fields = _sweep_with(monkeypatch, [7], [0.0])
    assert [round(p.fov_diagonal_deg, 4) for p in poses] == [round(fields[0], 4)]
    assert len(fields) == len(pps.BLIND_SWEEP_FACTORS)
    poses, _ = _sweep_with(monkeypatch, [6, 5], [0.0, 50.0])
    assert poses == []
    # Nothing elsewhere: the floor alone refuses a best of 6
    poses, _ = _sweep_with(monkeypatch, [6], [0.0])
    assert poses == []


def test_a_start_that_finds_no_pose_is_swept_too(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _sky()
    monkeypatch.setattr(pps, "_search_blind_icrs_triangles", lambda *args, **kwargs: None)
    seen = _spying(monkeypatch)
    solution = _solve(dets, TRUE_FOV_DEG * 1.06)
    assert solution.status == "calibrated" and _centre_off_deg(solution) < 0.1
    assert len(seen["sweeps"]) == 1 and seen["looks"] and seen["looks"][-1][1]


def test_a_sweep_pose_the_gate_refuses_leaves_the_solve_as_the_start_left_it(monkeypatch: pytest.MonkeyPatch) -> None:
    dets = _sky()
    wide = TRUE_FOV_DEG * 1.06
    _no_sweep(monkeypatch)
    alone = _solve(dets, wide).to_contract_dict()
    # A pose of another part of the sky: looked at, refused, and not kept
    monkeypatch.setattr(pps, "_blind_sweep_poses", lambda *args, **kwargs: [_camera(200.0, 45.0, 0.0, wide)])
    seen = _spying(monkeypatch)
    solution = _solve(dets, wide)
    assert seen["looks"] and seen["looks"][-1][0] == pytest.approx(wide) and not seen["looks"][-1][1]
    assert solution.to_contract_dict() == alone


def test_the_sweep_reports_its_total_at_its_start_then_each_fields_count_at_its_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dets = _wrong_sky(seed=3)
    fields: list[float] = []

    def best(d_rays: Any, tris: Any, width: int, height: int, fov_deg: float, lens: str, counts: Any) -> Any:
        # Each field stops after 5 of its candidates, as a search that stops early does
        fields.append(fov_deg)
        counts(0, 2024)
        counts(5, 2024)
        return 0, None

    monkeypatch.setattr(pps, "_blind_best_pose", best)
    tiles = pps._get_tile_index()
    ra, dec = pps._blind_search_catalog(tiles, tiles.epoch_jyear)
    reports: list[tuple[int, int]] = []
    poses = pps._blind_sweep_poses(dets, WIDTH, HEIGHT, ra, dec, (60.0, "brown-conrady"), lambda d, t: reports.append((d, t)), 100)
    n, per = len(pps.BLIND_SWEEP_FACTORS), 24 * 23 * 22 // 6
    total = 100 + per * n
    assert poses == [] and len(fields) == n
    assert reports == [(100, total)] + [(100 + i * per + 5, total) for i in range(n)]
