"""The phone-photo benchmark end to end on its CI subset: catalog, Moon, renderer, scorer, solvers.

Everything rendered here is simulated. The real data in this file is the catalog: bright stars read
from the G 12 tiles are checked against the pinned Hipparcos positions in services/science, and the
Moon against ERFA's eraMoon98 test value and Meeus's worked example 47.a.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from research.benchmarks.phone_photos import cameras, render, scene, score, solvers, stars, suite
from services.science.catalog import PINNED_CATALOG_STARS

CATALOG = stars.TileCatalog()
AU_KM = 149597870.7


def _separation_deg(ra1, dec1, ra2, dec2) -> np.ndarray:
    ra1, dec1, ra2, dec2 = (np.radians(np.asarray(v, dtype=np.float64)) for v in (ra1, dec1, ra2, dec2))
    a = np.stack([np.cos(dec1) * np.cos(ra1), np.cos(dec1) * np.sin(ra1), np.sin(dec1)], axis=-1)
    b = np.stack([np.cos(dec2) * np.cos(ra2), np.cos(dec2) * np.sin(ra2), np.sin(dec2)], axis=-1)
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(a, b), axis=-1), np.sum(a * b, axis=-1)))


@pytest.fixture(scope="module")
def ci_specs() -> list[suite.FrameSpec]:
    return suite.build_suite("ci")


@pytest.fixture(scope="module")
def ci_frames(ci_specs: list[suite.FrameSpec]) -> list[render.RenderedFrame]:
    return [render.render_frame(spec, CATALOG) for spec in ci_specs]


# =============================================================================================
# Real catalog rows and the Moon
# =============================================================================================


def test_tiles_hold_the_pinned_bright_stars_where_hipparcos_puts_them() -> None:
    # The pinned catalog carries J2000 Hipparcos positions; the tiles carry J2016 Gaia DR3 or
    # Hipparcos 2007 positions, moved back here by their own proper motions
    for star in PINNED_CATALOG_STARS:
        field = CATALOG.cone(star.ra_deg, star.dec_deg, 0.05, mag_limit=6.5, epoch_jyear=2000.0)
        assert len(field) > 0, star.common_name
        sep_arcsec = 3600.0 * _separation_deg(field.ra_deg, field.dec_deg, star.ra_deg, star.dec_deg)
        assert float(np.min(sep_arcsec)) < 0.5, star.common_name


def test_a_cone_holds_exactly_the_catalog_stars_inside_it() -> None:
    whole_sky = CATALOG.cone(0.0, 90.0, 180.0, mag_limit=8.0, epoch_jyear=2016.0)
    assert len(whole_sky) == 12192 + 50602  # the manifest's L0 and L1 counts
    cone = CATALOG.cone(83.8, -5.4, 20.0, mag_limit=8.0, epoch_jyear=2016.0)
    inside = _separation_deg(whole_sky.ra_deg, whole_sky.dec_deg, 83.8, -5.4) <= 20.0
    assert len(cone) == int(np.count_nonzero(inside))
    assert np.all(cone.mag <= 8.0)  # rounded to 1 mmag after the bake split its layers


def test_moon_matches_meeus_47a_and_erfa_moon98() -> None:
    # Meeus 47.a: 1992 April 12, 0h TD, geometric, mean equinox of date
    lon, lat, distance_km = scene.moon_ecliptic_of_date(2448724.5)
    assert abs(lon - 133.162655) < 0.02 and abs(lat - (-3.229126)) < 0.02
    assert distance_km == pytest.approx(368409.7, rel=5e-4)
    # eraMoon98(2400000.5, 43999.9): geocentric GCRS position in au
    expected = np.array([-0.2601295959971044180e-2, 0.6139750944302742189e-3, 0.2640794528229828909e-3]) * AU_KM
    got = scene.moon_geocentric_icrs_km(2400000.5 + 43999.9)
    angle = math.degrees(math.acos(float(got @ expected) / (np.linalg.norm(got) * np.linalg.norm(expected))))
    assert angle < 0.02
    assert np.linalg.norm(got) == pytest.approx(np.linalg.norm(expected), rel=5e-4)


def test_cameras_carry_their_published_fields_of_view() -> None:
    specs = cameras.CAMERAS
    assert specs["iphone-18-pro-main"].diagonal_fov_deg == pytest.approx(84.06, abs=0.01)  # 24 mm equivalent
    assert specs["iphone-18-pro-ultra-wide"].diagonal_fov_deg == 120.0
    assert specs["pixel-11-pro-wide"].diagonal_fov_deg == 82.0
    assert specs["pixel-11-pro-ultrawide"].diagonal_fov_deg == 123.0
    assert specs["galaxy-s25-ultra-wide"].diagonal_fov_deg == 85.0
    assert specs["galaxy-s25-ultra-ultra-wide"].diagonal_fov_deg == 120.0
    # The head-worn camera is a modelled class, not a product: no maker's figures, its field swept
    worn = specs["head-worn-video"]
    assert worn.diagonal_fov_deg is None and worn.fov_open_range_deg is not None
    assert worn.source_url is None and worn.video_size is not None
    for spec in specs.values():
        if spec.source_url is not None:
            assert spec.source_url.startswith("https://") and spec.source_quote and spec.source_read == "2026-11-04"
        assert 0.0 < spec.aperture_mm_range[0] <= spec.aperture_mm_range[1]


# =============================================================================================
# Renderer
# =============================================================================================


def test_the_suite_is_seeded_and_labelled(ci_specs: list[suite.FrameSpec]) -> None:
    assert ci_specs == suite.build_suite("ci")
    assert {spec.kind for spec in ci_specs} == {"positive", "negative"}
    assert len({spec.frame_id for spec in ci_specs}) == len(ci_specs)
    for spec in ci_specs:
        assert suite.FrameSpec.from_dict(spec.to_dict()) == spec


def test_frames_are_deterministic(ci_specs: list[suite.FrameSpec], ci_frames: list[render.RenderedFrame]) -> None:
    again = render.render_frame(ci_specs[1], CATALOG)
    assert again.jpeg_bytes == ci_frames[1].jpeg_bytes
    other = render.render_frame(replace(ci_specs[1], seed=ci_specs[1].seed + 1), CATALOG)
    assert other.jpeg_bytes != ci_frames[1].jpeg_bytes
    for frame in ci_frames:
        assert frame.truth.evidence_class == "simulated"
        assert frame.rgb.shape == (frame.spec.height, frame.spec.width, 3) and frame.rgb.dtype == np.uint8


def test_rendered_stars_land_on_their_truth_pixels(ci_specs: list[suite.FrameSpec]) -> None:
    # The stars-only linear layer of the first positive, kept to bright stars so each can be
    # isolated: every rendered star must centre on the pixel the truth gives it. The PSF is widened
    # to a 2 px FWHM because an undersampled PSF's pixel centroid leans toward the pixel centre
    # (by up to 0.1 px at this frame's 0.6 px FWHM), which would hide the geometry being tested
    base = next(s for s in ci_specs if s.kind == "positive")
    spec = replace(base, mag_limit=4.5, sensor=replace(base.sensor, psf_fwhm_native_px=2.0 * base.binning))
    ideal = render.render_frame(spec, CATALOG, ideal=True)
    image, truth = ideal.linear, ideal.truth
    xs, ys, flux = truth.star_x, truth.star_y, truth.star_electrons
    checked = 0
    for i in np.argsort(-flux):
        x, y = xs[i], ys[i]
        if not (14 <= x < image.shape[1] - 14 and 14 <= y < image.shape[0] - 14):
            continue
        # An 8 px aperture holds a core of sigma about 0.9 px; no other star may reach it
        if np.count_nonzero((np.hypot(xs - x, ys - y) < 20.0) & (flux > 1e-4 * flux[i])) > 1:
            continue
        x0, y0 = int(round(x)), int(round(y))
        yy, xx = np.mgrid[y0 - 12 : y0 + 13, x0 - 12 : x0 + 13]
        patch = image[y0 - 12 : y0 + 13, x0 - 12 : x0 + 13] * (np.hypot(xx - x0, yy - y0) <= 8.0)
        cx, cy = float(np.sum(xx * patch) / np.sum(patch)), float(np.sum(yy * patch) / np.sum(patch))
        assert math.hypot(cx - x, cy - y) < 0.01
        checked += 1
    assert checked >= 5


def test_no_visible_star_sits_on_the_ground(ci_frames: list[render.RenderedFrame]) -> None:
    for frame in ci_frames:
        truth = frame.truth
        if truth.sky_mask is None or truth.star_x.size == 0:
            continue
        cols = np.clip(np.round(truth.star_x).astype(int), 0, frame.spec.width - 1)
        rows = np.clip(np.round(truth.star_y).astype(int), 0, frame.spec.height - 1)
        assert np.all(truth.sky_mask[rows, cols])


# =============================================================================================
# Scorer
# =============================================================================================


class _Shifted:
    def __init__(self, wcs: solvers.WcsLike, dx: float) -> None:
        self._wcs, self._dx = wcs, dx

    def world_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x, y = self._wcs.world_to_pixel(ra_deg, dec_deg)
        return x + self._dx, y


def _claim(frame: render.RenderedFrame, wcs: solvers.WcsLike, *, true_matches: bool) -> solvers.SolverOutcome:
    truth = frame.truth
    brightest = np.argsort(-truth.star_electrons)[:20]
    if true_matches:
        det_x, det_y = truth.star_x[brightest], truth.star_y[brightest]
    else:
        det_x, det_y = wcs.world_to_pixel(truth.star_ra[brightest], truth.star_dec[brightest])
    matches = list(zip(det_x.tolist(), det_y.tolist(), truth.star_ra[brightest].tolist(), truth.star_dec[brightest].tolist()))
    return solvers.SolverOutcome(
        status="calibrated", claimed=True, wcs=wcs, matches=matches, detections=len(matches),
        held_out_inliers=len(matches) - 4, self_median_residual_px=0.5, elapsed_s=0.01,
    )


def test_the_scorer_tells_correct_imprecise_wrong_and_false_apart(ci_frames: list[render.RenderedFrame]) -> None:
    positive = next(f for f in ci_frames if f.spec.kind == "positive")
    oracle = solvers.TruthWcs(positive.truth.camera_truth)
    assert score.score_frame(positive.truth, _claim(positive, oracle, true_matches=True)).outcome == "correct"
    shifted = _Shifted(oracle, 5.0)
    assert score.score_frame(positive.truth, _claim(positive, shifted, true_matches=True)).outcome == "imprecise"
    turned = solvers.TruthWcs(replace(positive.truth.camera_truth, roll_deg=positive.truth.camera_truth.roll_deg + 10.0))
    assert score.score_frame(positive.truth, _claim(positive, turned, true_matches=False)).outcome == "wrong"
    never = solvers.SolverOutcome(status="unresolved", claimed=False, wcs=None, matches=[], detections=0,
                                  held_out_inliers=0, self_median_residual_px=None, elapsed_s=0.01)
    assert score.score_frame(positive.truth, never).outcome == "unresolved"
    for frame in ci_frames:
        if frame.spec.kind == "negative" and frame.truth.camera_truth is None:
            scored = score.score_frame(frame.truth, _claim(positive, oracle, true_matches=True))
            assert scored.false_solve and scored.outcome == "false-solve"


# =============================================================================================
# Today's solver on the CI subset
# =============================================================================================


def test_todays_solver_runs_the_ci_subset_without_a_false_solve(ci_frames: list[render.RenderedFrame]) -> None:
    # A 2 s limit keeps CI in seconds; a timeout is recorded as no claim, never as a solve
    report = score.run_frames(ci_frames, "today-api", timeout_s=2.0)
    assert [r.frame_id for r in report.frames] == [f.spec.frame_id for f in ci_frames]
    assert report.summary["evidenceClass"] == "simulated"
    assert all(r.outcome in score.OUTCOMES for r in report.frames)
    assert report.summary["negatives"]["falseSolves"] == 0
    assert "| outcome |" in score.to_markdown(report.summary)
