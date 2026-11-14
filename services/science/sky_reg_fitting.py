"""Rigid galactic-to-panorama rotation and vertical offset fitting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from services.science.sky_reg_frames import PanoramaGeometry, frame_to_pixel, lon_lat, wrap_dx

_ROTVEC_PARAM_SCALE = 1e-3
_OFFSET_PARAM_SCALE = 1.0
_RIGID_X_SCALE = (_ROTVEC_PARAM_SCALE, _ROTVEC_PARAM_SCALE, _ROTVEC_PARAM_SCALE, _OFFSET_PARAM_SCALE)


@dataclass(frozen=True)
class RigidSolution:
    """Rotation from galactic to the panorama frame and the vertical offset in pixels."""

    rotation: np.ndarray
    y_offset_px: float


def _to_panorama_frame(galactic: np.ndarray, solution: RigidSolution) -> np.ndarray:
    return galactic @ solution.rotation.T


def kabsch(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Rotation R that minimises sum |R s_i - t_i|^2 over paired unit vectors."""
    u, _, vt = np.linalg.svd(source.T @ target)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    return vt.T @ np.diag([1.0, 1.0, d]) @ u.T


def model_pixels(galactic: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry) -> tuple[np.ndarray, np.ndarray]:
    return frame_to_pixel(_to_panorama_frame(galactic, solution), geom, solution.y_offset_px)


def pixel_residuals(
    galactic: np.ndarray, measured_xy: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry
) -> np.ndarray:
    """Measured minus modelled pixel position, (dx, dy) per star."""
    x, y = model_pixels(galactic, solution, geom)
    return np.stack([wrap_dx(measured_xy[:, 0] - x, geom.width), measured_xy[:, 1] - y], axis=-1)


def fit_rigid(
    galactic: np.ndarray, measured_xy: np.ndarray, start: RigidSolution, geom: PanoramaGeometry, scale_px: float = 1.0
) -> RigidSolution:
    """Least-squares rotation and vertical offset, robust to stray matches (soft L1 loss)."""

    def unpack(q: np.ndarray) -> RigidSolution:
        return RigidSolution(Rotation.from_rotvec(q[:3]).as_matrix() @ start.rotation, float(q[3]))

    def residuals(q: np.ndarray) -> np.ndarray:
        return pixel_residuals(galactic, measured_xy, unpack(q), geom).ravel()

    q0 = np.array([0.0, 0.0, 0.0, start.y_offset_px])
    fit = least_squares(residuals, q0, loss="soft_l1", f_scale=scale_px, x_scale=_RIGID_X_SCALE)
    return unpack(fit.x)


def tangent_offsets(
    galactic: np.ndarray, measured_xy: np.ndarray, solution: RigidSolution, geom: PanoramaGeometry
) -> np.ndarray:
    """Residuals as (east, north) in texels at the equator, at each star's modelled position.

    East is along increasing panorama longitude, which runs to the left in the image, so a
    star east of its model sits at x - east / cos(lat).
    """
    p = _to_panorama_frame(galactic, solution)
    _, lat = lon_lat(p)
    x, y = frame_to_pixel(p, geom, solution.y_offset_px)
    dx = wrap_dx(measured_xy[:, 0] - x, geom.width)
    dy = measured_xy[:, 1] - y
    return np.stack([-dx * np.cos(lat), -dy], axis=-1)


__all__ = [
    "RigidSolution",
    "fit_rigid",
    "kabsch",
    "model_pixels",
    "pixel_residuals",
    "tangent_offsets",
]
