"""The horizon search's attitude prior takes a frame at any roll (the high-pointing prior, after solve-search-1).

Before this unit the search kept only rotations that look above the horizon, upright and within about 29 deg of level
roll, so a frame stored sideways or upside down (a phone photo decoded without its EXIF rotation, as the job decodes
it), or pointed within about 5 deg of the zenith, was never searched. Now the prior asks only that the optical axis
look above the horizon. The frames are the benchmark's own synthetic ones (research/benchmarks/phone_photos,
simulated), turned in their pixels; the held-out gate is the solver's own and unchanged.
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.science import phone_photo_solver as pps

SUITE = {frame.frame_id: frame for frame in phone_suite.build_suite("full")}


def _solve_turned(frame_id: str, quarter_turns: int):
    frame = phone_render.render_frame(SUITE[frame_id], phone_stars.TileCatalog())
    spec = frame.spec
    with Image.open(io.BytesIO(frame.jpeg_bytes)) as image:
        rgb = np.ascontiguousarray(np.rot90(np.asarray(image.convert("RGB"), dtype=np.uint8), k=quarter_turns))
    return pps.solve_phone_photo(
        rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "d" * 16, source_sha256="d" * 63 + "1",
        solution_id="sol_turned",
    )


@pytest.mark.parametrize("quarter_turns", [1, 2, 3], ids=["a quarter turn", "upside down", "three quarters"])
def test_a_frame_turned_in_its_pixels_is_still_lined_up(quarter_turns: int) -> None:
    solution = _solve_turned("pos-010", quarter_turns)
    assert solution.status == "calibrated", solution.warnings


def _rotation(axis_alt_deg: float, roll_deg: float) -> np.ndarray:
    """A camera rotation (rows: image right, image down, optical axis, in east, north, up) looking north at
    `axis_alt_deg`, turned by `roll_deg` about its axis."""
    a, r = math.radians(axis_alt_deg), math.radians(roll_deg)
    axis = np.array([0.0, math.cos(a), math.sin(a)])
    right0 = np.array([1.0, 0.0, 0.0])
    down0 = np.cross(axis, right0)
    right = math.cos(r) * right0 + math.sin(r) * down0
    down = np.cross(axis, right)
    return np.stack([right, down, axis])


def test_the_prior_asks_only_that_the_axis_look_above_the_horizon() -> None:
    upright, sideways, upside_down = _rotation(40, 0), _rotation(40, 90), _rotation(40, 180)
    near_zenith, below = _rotation(88, 30), _rotation(-10, 0)
    taken = pps._attitude_prior_takes(np.stack([upright, sideways, upside_down, near_zenith, below]))
    assert taken.tolist() == [True, True, True, True, False]
    # The axis must stand above the horizon by the prior's margin, as before
    assert not pps._attitude_prior_takes(np.stack([_rotation(math.degrees(math.asin(0.08)) - 0.1, 0)]))[0]


def test_a_frame_wider_than_the_refinement_takes_is_refined_without_failing() -> None:
    """A 35 mm tag of a very short lens gives a diagonal field of view past what the refinement fits (25 to 145 deg);
    its bounds then crossed and the solve failed. Once any roll is searched such a frame reaches the refinement (the
    whole-60 run of 19 Nov stopped on one), so the start is held inside the refinement's range."""
    from services.science.wcs_models import SourceDetection

    width, height = 4032, 3024
    wide = pps.PhoneCameraWCS.from_rotation_and_intrinsics(
        rot_matrix=np.eye(3), fov_diagonal_deg=158.0, width=width, height=height, lens_model="brown-conrady", sky_frame=None,
    )
    xs, ys = np.array([1200.0, 2800.0, 1500.0, 2500.0, 2016.0]), np.array([900.0, 1000.0, 2100.0, 2200.0, 1512.0])
    ra, dec = wide.pixel_to_world_batch(xs, ys)
    pairs = [
        (SourceDetection(x_px=float(x), y_px=float(y), flux=1000.0, snr=20.0, fwhm_px=2.5, centroid_uncertainty_px=0.1), float(r), float(d))
        for x, y, r, d in zip(xs, ys, ra, dec)
    ]
    refined = pps._refine_phone_camera_wcs(wide, pairs)
    assert 25.0 <= refined.fov_diagonal_deg <= 145.0


# The cut for a turned frame (the lead's 17:01 answer; attitude-prior.md's [open] item): a photo whose stored pixels
# are turned (its displayOrientation) is searched upright in the picture as it is shown, so the first, upright pass can
# line it up and the every-roll pass is not needed


def _solve_turned_told(frame_id: str, quarter_turns: int, monkeypatch: pytest.MonkeyPatch) -> tuple:
    passes: list[str] = []
    real = pps._solve_phone_photo_with

    def counted(*args, **kwargs):
        passes.append(kwargs["attitude_prior"].__name__)
        return real(*args, **kwargs)

    monkeypatch.setattr(pps, "_solve_phone_photo_with", counted)
    frame = phone_render.render_frame(SUITE[frame_id], phone_stars.TileCatalog())
    spec = frame.spec
    with Image.open(io.BytesIO(frame.jpeg_bytes)) as image:
        rgb = np.ascontiguousarray(np.rot90(np.asarray(image.convert("RGB"), dtype=np.uint8), k=quarter_turns))
    solution = pps.solve_phone_photo(
        rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "d" * 16, source_sha256="d" * 63 + "1",
        solution_id="sol_turned", display_turns=quarter_turns,
    )
    return solution, passes


@pytest.mark.parametrize("quarter_turns", [1, 2, 3], ids=["a quarter turn", "upside down", "three quarters"])
def test_a_turned_frame_told_its_turns_is_lined_up_in_the_upright_pass_alone(quarter_turns: int, monkeypatch: pytest.MonkeyPatch) -> None:
    # np.rot90 turns the pixels counterclockwise, so the picture is shown after as many clockwise quarter turns
    solution, passes = _solve_turned_told("pos-010", quarter_turns, monkeypatch)
    assert solution.status == "calibrated", solution.warnings
    assert len(passes) == 1, passes


def test_the_upright_prior_in_the_shown_picture_takes_one_roll_of_four() -> None:
    rolls = (0, 90, 180, 270)
    rotations = np.stack([_rotation(40, r) for r in rolls])
    assert pps._upright_prior_for(0) is pps._upright_prior_takes
    taken = {t: pps._upright_prior_for(t)(rotations).tolist() for t in range(4)}
    assert taken[0] == [True, False, False, False]
    for t in (1, 2, 3):
        assert sum(taken[t]) == 1, (t, taken[t])
    # Each shown orientation takes a different roll
    assert len({tuple(v) for v in taken.values()}) == 4
