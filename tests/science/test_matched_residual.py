"""matched-residual-one: a catalog name rests on a fit that was not made on its own star.

A verified solution names a detection only where its catalog star lies within the inlier gate under the solution's
reported WCS refitted without that star: for a star the training fit or the final polish was refined on, the same
recipe (training fit from the search's pose, then the polish from it) run on the pairs without it; for any other star,
the reported WCS itself, which no fit of it shaped. A star past the gate stays an observed finding, not counted as a
match. The held-out gate, its numbers and the status are unchanged.
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
GATE_LIMITATION = "Unmatched sensor detection outside verified catalog inlier gate."


def _no_naming_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    # Synthetic star ids have no deep tile to refuse a name against
    monkeypatch.setattr(pps, "naming_refusals", lambda wcs, named, gate_px, checked_xy, epoch_jyear, deep=None: [None] * len(named))


def _camera(fov_deg: float = TRUE_FOV_DEG) -> pps.PhoneCameraWCS:
    return pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3, dtype=np.float64), fov_diagonal_deg=fov_deg, width=WIDTH, height=HEIGHT,
        lens_model="brown-conrady", sky_frame=pps._ApparentSkyFrame.from_iso(UTC, LAT, LON),
    )


def _star(idx: int, ra: float, dec: float) -> CatalogStar:
    return replace(
        ALL_SKY_CATALOG_STARS[0], id=f"resid-star-{idx:03d}", common_name=f"ResidStar{idx:03d}", bayer_designation=None,
        flamsteed_number=None, names_file_designation=None, catalog_source_id=f"HIP {920000 + idx}",
        ra_deg=ra % 360.0, dec_deg=dec, mag=2.0 + 0.05 * idx, pm_ra_mas_yr=None, pm_dec_mas_yr=None,
    )


def _detection(idx: int, x: float, y: float) -> SourceDetection:
    return SourceDetection(
        x_px=x, y_px=y, flux=900.0 - 10.0 * idx, snr=40.0, fwhm_px=3.1, centroid_uncertainty_px=0.08,
        asset_id="src_resid", frame_time_sec=0.0, frame=FrameMeasures(),
    )


def _field() -> tuple[list[SourceDetection], list[CatalogStar]]:
    """A 7 by 5 grid of stars over the frame, measured by a 0.3 px centroid noise."""
    true_wcs = _camera()
    rng = np.random.default_rng(11)
    dets, stars = [], []
    for gx in np.linspace(300.0, WIDTH - 300.0, 7):
        for gy in np.linspace(250.0, HEIGHT - 250.0, 5):
            idx = len(dets)
            ra, dec = true_wcs.pixel_to_world(float(gx), float(gy))
            stars.append(_star(idx, float(ra), float(dec)))
            nx, ny = rng.normal(0.0, 0.3, size=2)
            dets.append(_detection(idx, float(gx) + float(nx), float(gy) + float(ny)))
    return dets, stars


def _solve(dets: list[SourceDetection], stars: list[CatalogStar]):
    wcs = _camera()
    cx, cy = (WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0
    seed = [wcs.pixel_to_world(cx, cy), wcs.pixel_to_world(cx + 20.0, cy), wcs.pixel_to_world(cx, cy + 20.0)]
    return pps.solve_phone_photo(
        np.full((HEIGHT, WIDTH), 10, dtype=np.uint8), detections=dets, obs_utc_iso=UTC, observer_lat=LAT,
        observer_lon=LON, fov_diagonal_deg=TRUE_FOV_DEG, catalog_stars=stars, source_asset_id="src_resid",
        source_sha256="d" * 64, seed_axes_icrs=seed,
    )


def _matched(sol) -> dict[int, object]:
    return {id(f.source_detection): f for f in sol.findings if f.evidence_class == "catalog_matched"}


def _pairs(dets: list[SourceDetection], stars: list[CatalogStar], idx: list[int]) -> list[tuple[SourceDetection, float, float]]:
    return [(dets[i], stars[i].ra_deg, stars[i].dec_deg) for i in idx]


def _look(dets, stars, train_idx: list[int], polish_idx: list[int]) -> pps._HeldOutLook:
    start = _camera(TRUE_FOV_DEG * 1.01)
    train = _pairs(dets, stars, train_idx)
    polish = _pairs(dets, stars, polish_idx)
    return pps._HeldOutLook(
        passed_gate=True, log_odds=40.0, all_pairs=[], gate_px=2.0, refined_wcs=_camera(), held_out_tuples=[],
        held_out_residuals_all=[], refined_on_ids=set(), train_det_ids={id(d) for d, _, _ in train}, train_tuples=train,
        start_wcs=start, trained_wcs=_camera(TRUE_FOV_DEG * 1.002), polish_pairs=polish,
    )


def test_a_training_star_is_placed_by_the_whole_recipe_run_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    dets, stars = _field()
    look = _look(dets, stars, train_idx=list(range(0, 12)), polish_idx=list(range(0, 24)))
    calls: list[tuple[int, list[int], object]] = []
    real = pps._refine_phone_camera_wcs

    def spy(start, pairs, **kwargs):
        calls.append((id(start), [dets.index(d) for d, _, _ in pairs], kwargs.get("fit_distortion")))
        return real(start, pairs, **kwargs)

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", spy)
    wcs = pps._fit_without(look, dets[3])
    without_train = [i for i in range(12) if i != 3]
    without_polish = [i for i in range(24) if i != 3]
    # The training fit from the search's pose (rigid, then with distortion), then the polish from that fit
    assert [c[1] for c in calls] == [without_train, without_train, without_polish]
    assert calls[0][0] == id(look.start_wcs) and calls[0][2] is False and calls[1][2] is True
    assert calls[2][2] is True  # 23 pairs: the polish fits distortion from nine pairs, as the polish itself does
    assert isinstance(wcs, pps.PhoneCameraWCS)


def test_a_held_out_star_of_the_polish_is_placed_by_the_polish_run_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    dets, stars = _field()
    look = _look(dets, stars, train_idx=list(range(0, 12)), polish_idx=list(range(0, 20)))
    calls: list[tuple[int, list[int], object]] = []
    real = pps._refine_phone_camera_wcs

    def spy(start, pairs, **kwargs):
        calls.append((id(start), [dets.index(d) for d, _, _ in pairs], kwargs.get("fit_distortion")))
        return real(start, pairs, **kwargs)

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", spy)
    pps._fit_without(look, dets[15])
    # The training fit was never refined on it: the polish alone runs again, from the training fit, without it
    assert calls == [(id(look.trained_wcs), [i for i in range(20) if i != 15], True)]


def test_a_star_no_fit_was_refined_on_is_placed_by_the_reported_wcs(monkeypatch: pytest.MonkeyPatch) -> None:
    dets, stars = _field()
    look = _look(dets, stars, train_idx=list(range(0, 12)), polish_idx=list(range(0, 20)))

    def never(*args, **kwargs):
        raise AssertionError("a star no fit was refined on was refitted")

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", never)
    assert pps._fit_without(look, dets[30]) is look.refined_wcs


def test_the_polish_without_a_star_fits_distortion_only_from_nine_pairs(monkeypatch: pytest.MonkeyPatch) -> None:
    dets, stars = _field()
    seen: list[object] = []
    real = pps._refine_phone_camera_wcs

    def spy(start, pairs, **kwargs):
        seen.append((len(pairs), kwargs.get("fit_distortion")))
        return real(start, pairs, **kwargs)

    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", spy)
    # Ten polish pairs, one left out: nine, distortion fitted; nine, one left out: eight, not fitted
    pps._fit_without(_look(dets, stars, train_idx=[0, 1, 2, 3, 4, 5], polish_idx=list(range(0, 10))), dets[8])
    pps._fit_without(_look(dets, stars, train_idx=[0, 1, 2, 3, 4, 5], polish_idx=list(range(0, 9))), dets[8])
    assert seen == [(9, True), (8, False)]


def test_the_left_out_residual_is_the_pixel_distance_under_the_fit_without_the_star(monkeypatch: pytest.MonkeyPatch) -> None:
    dets, stars = _field()
    look = _look(dets, stars, train_idx=list(range(0, 12)), polish_idx=list(range(0, 20)))
    det = dets[30]
    x, y = look.refined_wcs.world_to_pixel(stars[30].ra_deg, stars[30].dec_deg)
    want = math.hypot(x - det.x_px, y - det.y_px)
    assert pps._left_out_residual_px(look, det, stars[30].ra_deg, stars[30].dec_deg) == pytest.approx(want, abs=1e-9)
    # A star the fit cannot place (behind the camera) is never within a gate
    monkeypatch.setattr(pps.PhoneCameraWCS, "world_to_pixel", lambda self, ra, dec: (math.nan, math.nan))
    assert pps._left_out_residual_px(look, det, stars[30].ra_deg, stars[30].dec_deg) == math.inf


def test_a_star_past_the_gate_under_the_fit_without_it_stays_observed_and_uncounted(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    dets, stars = _field()
    base = _solve(dets, stars)
    assert base.status == "calibrated", (base.status, base.quality)
    named = _matched(base)
    assert len(named) >= 20
    cut_det, edge_det = dets[0], dets[6]
    assert id(cut_det) in named and id(edge_det) in named
    real = pps._left_out_residual_px
    gates: list[float] = []

    def pushed(look, det, ra, dec):
        gates.append(look.gate_px)
        if det is cut_det:
            return math.nextafter(look.gate_px, math.inf)
        if det is edge_det:
            return look.gate_px
        return real(look, det, ra, dec)

    monkeypatch.setattr(pps, "_left_out_residual_px", pushed)
    sol = _solve(dets, stars)
    matched = _matched(sol)
    assert sol.status == "calibrated"
    # Past the gate by the least step: not named; exactly at the gate: named, as the association's own inliers are
    assert id(cut_det) not in matched and id(edge_det) in matched
    assert set(matched) == set(named) - {id(cut_det)}
    left = [f for f in sol.findings if f.source_detection is cut_det]
    assert len(left) == 1 and left[0].evidence_class == "observed" and left[0].catalog_star is None
    assert left[0].limitations == (GATE_LIMITATION,)
    assert left[0].ra_deg is not None  # an observed point under a verified WCS keeps its sky position
    assert sol.quality.matched_inlier_count == len(matched) == base.quality.matched_inlier_count - 1
    # The gate's own numbers are the held-out check's, untouched by the names
    for field in ("held_out_inliers", "held_out_pair_count", "median_residual_px", "median_residual_arcsec",
                  "passed_independent_verification"):
        assert getattr(sol.quality, field) == getattr(base.quality, field), field
    assert gates and len(set(gates)) == 1


def test_a_clean_field_keeps_every_name_its_gate_gave() -> None:
    dets, stars = _field()
    calls: list[int] = []
    real = pps._left_out_residual_px

    def counting(look, det, ra, dec):
        calls.append(id(det))
        return real(look, det, ra, dec)

    with pytest.MonkeyPatch.context() as mp:
        _no_naming_refusals(mp)
        sol = _solve(dets, stars)
        mp.setattr(pps, "_left_out_residual_px", counting)
        checked = _solve(dets, stars)
    assert sol.status == checked.status == "calibrated"
    # Every star the gate paired is placed by a fit without it, and on a field measured to 0.3 px none is past it
    assert len(calls) == len(_matched(checked)) == len(_matched(sol)) >= 30


def test_a_lone_corner_star_the_fit_bent_to_is_past_the_gate_without_it() -> None:
    """The mechanism the rule meets on rp-b-14: a training star alone in a corner pulls the distortion to itself, so
    its residual under the fit made on it is small and under the fit without it is not. Twenty stars reach to 400 px
    and 300 px from the edges, so the fit without the corner star still places it, within about a pixel."""
    true_wcs = _camera()
    rng = np.random.default_rng(3)
    dets, stars = [], []
    for gx in np.linspace(400.0, WIDTH - 400.0, 5):
        for gy in np.linspace(300.0, HEIGHT - 300.0, 4):
            idx = len(dets)
            ra, dec = true_wcs.pixel_to_world(float(gx), float(gy))
            stars.append(_star(idx, float(ra), float(dec)))
            nx, ny = rng.normal(0.0, 0.3, size=2)
            dets.append(_detection(idx, float(gx) + float(nx), float(gy) + float(ny)))
    # The corner star's detection lies 6 px from its star along the radius: what a misplaced name would be
    ra, dec = true_wcs.pixel_to_world(WIDTH - 60.0, 50.0)
    stars.append(_star(len(stars), float(ra), float(dec)))
    ux, uy = (WIDTH - 60.0) - (WIDTH - 1) / 2.0, 50.0 - (HEIGHT - 1) / 2.0
    norm = math.hypot(ux, uy)
    corner = _detection(len(dets), WIDTH - 60.0 + 6.0 * ux / norm, 50.0 + 6.0 * uy / norm)
    dets.append(corner)
    train_idx = [len(dets) - 1, *range(0, 20, 2)]
    polish_idx = [*train_idx, *range(1, 20, 2)]
    look = _look(dets, stars, train_idx=train_idx, polish_idx=polish_idx)
    trained = pps._train_fit(look.start_wcs, look.train_tuples)
    reported = pps._polish_fit(trained, look.polish_pairs)
    look = replace(look, trained_wcs=trained, refined_wcs=reported)
    x, y = reported.world_to_pixel(stars[-1].ra_deg, stars[-1].dec_deg)
    in_sample = math.hypot(x - corner.x_px, y - corner.y_px)
    left_out = pps._left_out_residual_px(look, corner, stars[-1].ra_deg, stars[-1].dec_deg)
    tx, ty = pps._fit_without(look, corner).world_to_pixel(stars[-1].ra_deg, stars[-1].dec_deg)
    # The fit without it puts the star near its true place; the fit made on it bends to within any gate of the
    # detection (the gate is 1.8 to 4 px), and without it the detection lies past the widest gate
    assert math.hypot(tx - (WIDTH - 60.0), ty - 50.0) < 1.5
    assert in_sample < 1.0 and left_out > 4.0, (in_sample, left_out)


def test_a_look_records_the_recipe_its_reported_wcs_was_made_by(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_naming_refusals(monkeypatch)
    dets, stars = _field()
    looks: list[pps._HeldOutLook] = []
    real = pps._held_out_look

    def keeping(*args, **kwargs):
        look = real(*args, **kwargs)
        looks.append(look)
        return look

    monkeypatch.setattr(pps, "_held_out_look", keeping)
    sol = _solve(dets, stars)
    assert sol.status == "calibrated" and len(looks) == 1
    look = looks[0]
    assert look.polish_pairs and [d for d, _, _ in look.polish_pairs] == [
        *[d for d, _, _ in look.train_tuples], *[d for d, _, _ in look.held_out_tuples[:18]]]

    def same(a: pps.PhoneCameraWCS, b: pps.PhoneCameraWCS) -> bool:
        xs, ys = np.array([0.0, WIDTH - 1.0, 0.0, WIDTH - 1.0, WIDTH / 2.0]), np.array([0.0, 0.0, HEIGHT - 1.0, HEIGHT - 1.0, HEIGHT / 2.0])
        ra_a, dec_a = a.pixel_to_world_batch(xs, ys)
        ra_b, dec_b = b.pixel_to_world_batch(xs, ys)
        return bool(np.allclose(ra_a, ra_b, atol=1e-9) and np.allclose(dec_a, dec_b, atol=1e-9))

    # The training fit and the polish, made again from what the look records, are the look's own fits
    assert same(pps._train_fit(look.start_wcs, look.train_tuples), look.trained_wcs)
    assert same(pps._polish_fit(look.trained_wcs, look.polish_pairs), look.refined_wcs)
    assert not same(look.trained_wcs, look.refined_wcs)
