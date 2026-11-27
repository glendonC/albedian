"""The horizon search's catalog comparison, rays first (horizon-rays-first-1).

`_search_horizon_attitude` counts, for each candidate rotation, the detections' rays that have a catalog star within
the gate. Main turned every catalog star by every rotation, then compared each ray with each turned star (two
einsums); `_horizon_inliers` turns the rays into the catalog's frame and makes one matrix product with the catalog.
The counts must be the einsum path's, so every rotation the search keeps and every solve stays as it was. The two
paths add in a different order, so a star within rounding of the gate could flip; the property below places stars on
both sides of the gate's cosine, a millionth of a degree away at the closest, and requires the counts to be equal.
"""

from __future__ import annotations

import math
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st
import numpy as np
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.science import phone_photo_solver as pps

GATE_DEG = 0.85
COS_GATE = math.cos(math.radians(GATE_DEG))


def _einsum_counts(d_rays: np.ndarray, rotations: np.ndarray, u_cat: np.ndarray, cos_gate: float) -> np.ndarray:
    """Main's lines before this unit, as they were: the catalog turned by each rotation, then each ray against it."""
    proj = np.einsum("kn,cmn->ckm", u_cat, rotations)
    sim = np.einsum("mn,ckn->cmk", d_rays, proj)
    return np.count_nonzero(np.max(sim, axis=2) >= cos_gate, axis=1)


def _rotations(rng: np.random.Generator, count: int) -> np.ndarray:
    q = rng.normal(size=(count, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], axis=-1),
        np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], axis=-1),
        np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=-1),
    ], axis=1)


def _star_at(ray: np.ndarray, rotation: np.ndarray, angle_deg: float, rng: np.random.Generator) -> np.ndarray:
    """A catalog direction that the rotation turns to `angle_deg` from the ray (the search compares ray . R u)."""
    side = np.cross(ray, rng.normal(size=3))
    side /= np.linalg.norm(side)
    a = math.radians(angle_deg)
    return rotation.T @ (math.cos(a) * ray + math.sin(a) * side)


def _rays(rng: np.random.Generator, count: int) -> np.ndarray:
    # Camera rays as the search makes them: unit vectors in front of the camera, up to about 60 degrees off axis
    xy = rng.uniform(-1.7, 1.7, size=(count, 2))
    rays = np.concatenate([xy, np.ones((count, 1))], axis=1)
    return rays / np.linalg.norm(rays, axis=1, keepdims=True)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1), n_rot=st.integers(min_value=1, max_value=24),
       n_rays=st.integers(min_value=6, max_value=38), n_stars=st.integers(min_value=6, max_value=110),
       margin_deg=st.sampled_from([1e-6, 1e-5, 1e-3, 0.05, 0.5]))
def test_pbt_the_rays_first_counts_are_the_einsum_paths_with_stars_at_the_gates_cosine(
        seed: int, n_rot: int, n_rays: int, n_stars: int, margin_deg: float) -> None:
    rng = np.random.default_rng(seed)
    rotations = _rotations(rng, n_rot)
    rays = _rays(rng, n_rays)
    stars = []
    for _ in range(n_stars):
        c, m = int(rng.integers(n_rot)), int(rng.integers(n_rays))
        # Half just inside the gate, half just outside, the closest a millionth of a degree from it
        angle = GATE_DEG - margin_deg if rng.random() < 0.5 else GATE_DEG + margin_deg
        stars.append(_star_at(rays[m], rotations[c], angle, rng))
    u_cat = np.array(stars)
    counts = pps._horizon_inliers(rays, rotations, u_cat, COS_GATE)
    assert counts.shape == (n_rot,)
    assert counts.tolist() == _einsum_counts(rays, rotations, u_cat, COS_GATE).tolist()


def test_a_ray_counts_with_a_star_a_millionth_of_a_degree_inside_the_gate_and_not_one_outside() -> None:
    rng = np.random.default_rng(7)
    # Eight rays at least ten degrees apart, so no star placed near one ray is near another
    rays = np.array([[math.sin(math.radians(a)) * math.cos(math.radians(b)),
                      math.sin(math.radians(a)) * math.sin(math.radians(b)), math.cos(math.radians(a))]
                     for a, b in [(0, 0), (15, 0), (15, 90), (15, 180), (15, 270), (35, 45), (35, 135), (35, 225)]])
    turn = _rotations(rng, 1)[0]
    rotations = np.stack([turn, np.eye(3)])
    inside = [_star_at(rays[m], turn, GATE_DEG - 1e-6, rng) for m in range(4)]
    outside = [_star_at(rays[m], turn, GATE_DEG + 1e-6, rng) for m in range(4, 8)]
    u_cat = np.array(inside + outside)
    counts = pps._horizon_inliers(rays, rotations, u_cat, COS_GATE)
    assert counts[0] == 4
    assert counts.tolist() == _einsum_counts(rays, rotations, u_cat, COS_GATE).tolist()


def test_a_star_exactly_at_the_gates_cosine_counts_as_on_mains_path() -> None:
    # Ray straight ahead, no turn: ray . R u is then 0 * x + 0 * y + 1 * z, exact in floating point on either path
    rays = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]])
    u_cat = np.array([[math.sqrt(1.0 - COS_GATE * COS_GATE), 0.0, COS_GATE]])
    rotations = np.eye(3)[None, :, :]
    assert pps._horizon_inliers(rays, rotations, u_cat, COS_GATE).tolist() == [1]
    assert _einsum_counts(rays, rotations, u_cat, COS_GATE).tolist() == [1]


_FRAME: Any = None


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def test_the_search_keeps_the_einsum_paths_rotation_on_a_pinned_field(monkeypatch: pytest.MonkeyPatch) -> None:
    # The benchmark's first CI frame (a rendered field with a time and a place, so the solve runs the horizon search):
    # its arguments caught on the way in, then the search run again with main's comparison in place of the new one
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    search = pps._search_horizon_attitude

    def caught(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return search(*args, **kwargs)

    monkeypatch.setattr(pps, "_search_horizon_attitude", caught)
    frame = _frame()
    spec = frame.spec
    solution = pps.solve_phone_photo(
        frame.rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "d" * 16, source_sha256="d" * 63 + "1",
        solution_id="sol_rays_first",
    )
    assert solution.status == "calibrated", solution.warnings
    assert len(calls) == 1
    args, kwargs = calls[0]
    used = []
    real = pps._horizon_inliers

    def counted(*a: Any) -> np.ndarray:
        used.append(1)
        return real(*a)

    monkeypatch.setattr(pps, "_horizon_inliers", counted)
    ours = search(*args, **kwargs)
    assert used, "the search counts its inliers through the rays-first comparison"
    monkeypatch.setattr(pps, "_horizon_inliers", _einsum_counts)
    mains = search(*args, **kwargs)
    assert ours is not None and mains is not None
    # The rotation is drawn from the same candidate array on either path, so equal counts give the same one
    assert np.allclose(np.array(ours.cam_rotation), np.array(mains.cam_rotation), rtol=0.0, atol=1e-12)
    assert (ours.fov_diagonal_deg, ours.lens_model) == (mains.fov_diagonal_deg, mains.lens_model)
