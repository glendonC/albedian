"""The phone-photo solver's blind search (no place in the file) over the whole sky (solve-search-1).

Before this fix the blind search searched only the first 28 stars of a list chosen within 55 deg of Orion and Taurus,
so a frame elsewhere (Crux, the southern Milky Way) could not be solved without a place, whatever its stars. These
tests render the benchmark's own synthetic phone frames (research/benchmarks/phone_photos, simulated, no real sky)
and solve them with no time and no place, the field of view given as a 35 mm tag would give it. The held-out gate is
the solver's own and unchanged: a calibrated result here passed it.
"""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.science import phone_photo_solver as pps
from services.science.catalog import angular_separation_deg

SHA = "c" * 63 + "1"
SUITE = {frame.frame_id: frame for frame in phone_suite.build_suite("full")}


def _blind_solve(frame_id: str):
    frame = phone_render.render_frame(SUITE[frame_id], phone_stars.TileCatalog())
    spec = frame.spec
    # The frame as an upload would hold it: its JPEG, decoded
    with Image.open(io.BytesIO(frame.jpeg_bytes)) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    solution = pps.solve_phone_photo(
        rgb, obs_utc_iso=None, observer_lat=None, observer_lon=None, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "c" * 16, source_sha256=SHA, solution_id="sol_blind",
    )
    return frame, solution


def _median_error_px(frame, solution) -> float:
    """The median distance, in pixels, between where the solution's WCS puts the frame's rendered stars and where the
    renderer put them, over the stars inside the frame."""
    truth, spec = frame.truth, frame.spec
    x, y = solution.wcs.world_to_pixel_batch(truth.star_ra, truth.star_dec)
    inside = (truth.star_x >= 0) & (truth.star_x < spec.width) & (truth.star_y >= 0) & (truth.star_y < spec.height)
    return float(np.median(np.hypot(x[inside] - truth.star_x[inside], y[inside] - truth.star_y[inside])))


@pytest.mark.parametrize("frame_id", ["pos-003", "pos-073"], ids=["crux, iPhone main", "crux, Pixel wide"])
def test_a_southern_frame_with_no_place_is_solved_blind(frame_id: str) -> None:
    frame, solution = _blind_solve(frame_id)
    assert solution.status == "calibrated", solution.warnings
    assert _median_error_px(frame, solution) < 3.0


def test_a_frame_of_orion_with_no_place_is_still_solved_blind() -> None:
    orion = next(f.frame_id for f in SUITE.values() if f.kind == "positive" and f.category == "orion")
    frame, solution = _blind_solve(orion)
    assert solution.status == "calibrated", solution.warnings
    assert _median_error_px(frame, solution) < 3.0


def test_the_blind_catalog_names_no_sky_region() -> None:
    ra, dec = pps._blind_search_catalog(pps._get_tile_index(), pps._get_tile_index().epoch_jyear)
    # The whole sky: stars in every quarter of right ascension and in both hemispheres, the south's far reaches too
    quarters = np.floor(np.mod(ra, 360.0) / 90.0)
    assert set(quarters.tolist()) == {0.0, 1.0, 2.0, 3.0}
    assert (dec < -60.0).sum() >= 20 and (dec > 60.0).sum() >= 20
    near_orion = np.array([angular_separation_deg(float(r), float(d), 83.8, -1.2) for r, d in zip(ra, dec)]) <= 55.0
    # No more than the share of the sphere a 55 deg cap holds, plus room for the uneven sky (about 21 %)
    assert near_orion.mean() < 0.3
    assert math.isclose(len(ra), len(set(zip(ra.tolist(), dec.tolist()))))
