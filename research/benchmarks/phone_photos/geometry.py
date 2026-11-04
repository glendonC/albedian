"""Ground-truth camera for the benchmark: intrinsics, lens distortion and attitude.

This is written independently of the solver's `WCSModel` (services/science/wcs_models.py), so a
mistake shared by the two cannot hide itself: a solver's claims get checked against a model that
shares none of its code, never against the solver's own projection.

Conventions:

- Pixels: x counts columns to the right and y rows downward, with integer values at pixel
  centres (the convention of the solver's detections). An image spans [-0.5, width - 0.5].
- Camera frame: x right, y down, z forward along the optical axis (OpenCV's convention).
- Horizon frame: (east, north, up) unit vectors of the observed sky; azimuth runs from north
  through east.
- Attitude: the optical axis points at (azimuth, altitude); roll turns the camera about that axis,
  positive when its right side rises as seen from behind it. At roll 0 the image's vertical is the
  local vertical.

Distortion models, both as OpenCV documents them:

- Brown-Conrady (Brown 1966, Photogrammetric Engineering 32, 444; Conrady 1919, MNRAS 79, 384):
  radial k1, k2, k3 and tangential p1, p2 on the pinhole's normalized coordinates.
- Kannala-Brandt (Kannala and Brandt 2006, IEEE TPAMI 28, 1335): the angle off the axis theta
  maps to theta (1 + k1 theta^2 + k2 theta^4 + k3 theta^6 + k4 theta^8); with no coefficients this
  is the equidistant fisheye.

Each model is used only where it is one-to-one: beyond the radius or angle where it folds back, a
ray has no pixel (NaN).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import cached_property
from typing import ClassVar, Union

import numpy as np

from research.benchmarks.phone_photos.astrometry import ObservationFrame

FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)


@dataclass(frozen=True)
class Intrinsics:
    """Pinhole intrinsics in pixels: focal lengths and principal point."""

    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.fx <= 0.0 or self.fy <= 0.0:
            raise ValueError(f"Intrinsics need positive size and focal lengths, got {self}.")


def _smallest_positive_root(coefficients: list[float]) -> float:
    """Smallest positive real root of a polynomial (highest power first), or inf without one.

    Coefficients below 1e-15 in size count as zero: they could only place a fold beyond about
    89.99 degrees off the axis, and denormal ones would overflow the companion matrix.
    """
    coefficients = [0.0 if abs(c) < 1e-15 else c for c in coefficients[:-1]] + [coefficients[-1]]
    roots = np.roots(coefficients) if any(c != 0.0 for c in coefficients[:-1]) else np.array([])
    real = [float(r.real) for r in roots if abs(r.imag) < 1e-12 and r.real > 0.0]
    return min(real) if real else math.inf


@dataclass(frozen=True)
class BrownConrady:
    """Radial and tangential distortion of normalized pinhole coordinates."""

    model: ClassVar[str] = "brown-conrady"
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    p1: float = 0.0
    p2: float = 0.0

    def _radial(self, r2: np.ndarray) -> np.ndarray:
        return 1.0 + r2 * (self.k1 + r2 * (self.k2 + r2 * self.k3))

    def distort(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        r2 = x * x + y * y
        radial = self._radial(r2)
        xd = x * radial + 2.0 * self.p1 * x * y + self.p2 * (r2 + 2.0 * x * x)
        yd = y * radial + self.p1 * (r2 + 2.0 * y * y) + 2.0 * self.p2 * x * y
        return xd, yd

    def undistort(self, xd: np.ndarray, yd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Invert `distort` by Newton iteration on both coordinates."""
        xd = np.asarray(xd, dtype=np.float64)
        yd = np.asarray(yd, dtype=np.float64)
        radial0 = self._radial(xd * xd + yd * yd)
        safe = np.where(np.abs(radial0) > 0.2, radial0, 1.0)
        x, y = xd / safe, yd / safe
        for _ in range(60):
            r2 = x * x + y * y
            radial = self._radial(r2)
            slope = 2.0 * self.k1 + r2 * (4.0 * self.k2 + 6.0 * self.k3 * r2)
            fx, fy = self.distort(x, y)
            fx, fy = fx - xd, fy - yd
            a = radial + slope * x * x + 2.0 * self.p1 * y + 6.0 * self.p2 * x
            b = slope * x * y + 2.0 * self.p1 * x + 2.0 * self.p2 * y
            d = radial + slope * y * y + 6.0 * self.p1 * y + 2.0 * self.p2 * x
            det = a * d - b * b
            step_x = (d * fx - b * fy) / det
            step_y = (a * fy - b * fx) / det
            x, y = x - step_x, y - step_y
            if np.all(np.abs(step_x) + np.abs(step_y) < 1e-15):
                break
        return x, y

    def max_valid_radius(self) -> float:
        """Normalized radius where the radial mapping r (1 + k1 r^2 + ...) stops increasing."""
        return math.sqrt(_smallest_positive_root([7.0 * self.k3, 5.0 * self.k2, 3.0 * self.k1, 1.0]))

    def distorted_radius(self, radius: float) -> float:
        return radius * float(self._radial(np.array(radius * radius)))


@dataclass(frozen=True)
class KannalaBrandt:
    """Fisheye mapping of the angle off the optical axis."""

    model: ClassVar[str] = "kannala-brandt"
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    k4: float = 0.0

    def distorted_theta(self, theta: np.ndarray) -> np.ndarray:
        t2 = theta * theta
        return theta * (1.0 + t2 * (self.k1 + t2 * (self.k2 + t2 * (self.k3 + t2 * self.k4))))

    def _slope(self, theta: np.ndarray) -> np.ndarray:
        t2 = theta * theta
        return 1.0 + t2 * (3.0 * self.k1 + t2 * (5.0 * self.k2 + t2 * (7.0 * self.k3 + t2 * 9.0 * self.k4)))

    def theta_from_distorted(self, theta_d: np.ndarray) -> np.ndarray:
        """Invert `distorted_theta` by Newton iteration."""
        theta_d = np.asarray(theta_d, dtype=np.float64)
        theta = theta_d.copy()
        for _ in range(60):
            step = (self.distorted_theta(theta) - theta_d) / self._slope(theta)
            theta = theta - step
            if np.all(np.abs(step) < 1e-15):
                break
        return theta

    def max_valid_theta(self) -> float:
        """Angle off the axis (radians, at most pi) where the mapping stops increasing."""
        root = _smallest_positive_root([9.0 * self.k4, 7.0 * self.k3, 5.0 * self.k2, 3.0 * self.k1, 1.0])
        return min(math.sqrt(root), math.pi)


Distortion = Union[BrownConrady, KannalaBrandt]
DISTORTION_MODELS: dict[str, type] = {BrownConrady.model: BrownConrady, KannalaBrandt.model: KannalaBrandt}


@dataclass(frozen=True)
class Camera:
    """Pixels to camera-frame rays and back."""

    intrinsics: Intrinsics
    distortion: Distortion

    @cached_property
    def _limit(self) -> float:
        if isinstance(self.distortion, BrownConrady):
            return self.distortion.max_valid_radius()
        return self.distortion.max_valid_theta()

    def ray_to_pixel(self, rays: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixel (x, y) of camera-frame rays (n, 3); NaN where the camera does not image the ray."""
        rays = np.atleast_2d(np.asarray(rays, dtype=np.float64))
        ray_x, ray_y, ray_z = rays[:, 0], rays[:, 1], rays[:, 2]
        k = self.intrinsics
        with np.errstate(divide="ignore", invalid="ignore"):
            if isinstance(self.distortion, BrownConrady):
                valid = ray_z > 1e-12
                x = np.where(valid, ray_x / np.where(valid, ray_z, 1.0), 0.0)
                y = np.where(valid, ray_y / np.where(valid, ray_z, 1.0), 0.0)
                valid &= np.hypot(x, y) <= self._limit
                xd, yd = self.distortion.distort(x, y)
            else:
                rho = np.hypot(ray_x, ray_y)
                theta = np.arctan2(rho, ray_z)
                valid = (theta <= self._limit) & ((rho > 0.0) | (ray_z > 0.0))
                scale = np.where(rho > 0.0, self.distortion.distorted_theta(theta) / np.where(rho > 0.0, rho, 1.0), 0.0)
                xd, yd = ray_x * scale, ray_y * scale
        px = np.where(valid, k.fx * xd + k.cx, np.nan)
        py = np.where(valid, k.fy * yd + k.cy, np.nan)
        return px, py

    def pixel_to_ray(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Unit camera-frame rays (n, 3) of pixels; NaN for pixels no ray reaches."""
        k = self.intrinsics
        xd = (np.atleast_1d(np.asarray(x, dtype=np.float64)) - k.cx) / k.fx
        yd = (np.atleast_1d(np.asarray(y, dtype=np.float64)) - k.cy) / k.fy
        if isinstance(self.distortion, BrownConrady):
            limit = self.distortion.distorted_radius(self._limit) if math.isfinite(self._limit) else math.inf
            xu, yu = self.distortion.undistort(xd, yd)
            rays = np.stack([xu, yu, np.ones_like(xu)], axis=-1)
            rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
            reachable = np.hypot(xd, yd) <= limit
        else:
            rd = np.hypot(xd, yd)
            theta = self.distortion.theta_from_distorted(rd)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(rd > 0.0, np.sin(theta) / np.where(rd > 0.0, rd, 1.0), 0.0)
            rays = np.stack([xd * ratio, yd * ratio, np.cos(theta)], axis=-1)
            reachable = rd <= float(self.distortion.distorted_theta(np.array(self._limit)))
        rays[~reachable] = np.nan
        return rays


def diagonal_fov_from_35mm_equivalent(focal_mm: float) -> float:
    """Diagonal field of view in degrees of a rectilinear lens given its 35 mm equivalent focal length."""
    return math.degrees(2.0 * math.atan(FULL_FRAME_DIAGONAL_MM / 2.0 / focal_mm))


def intrinsics_for_diagonal_fov(width: int, height: int, fov_deg: float, distortion: Distortion) -> Intrinsics:
    """Square-pixel intrinsics centred on the image whose corner edges see `fov_deg` across the diagonal.

    The field is measured between the outer edges of opposite corner pixels, through the radial part
    of the distortion.
    """
    half = math.radians(fov_deg / 2.0)
    if isinstance(distortion, BrownConrady):
        if fov_deg >= 180.0:
            raise ValueError("A rectilinear camera cannot see 180 degrees or more.")
        radius = math.tan(half)
        if radius > distortion.max_valid_radius():
            raise ValueError(f"{distortion} folds back inside a {fov_deg} degree field.")
        distorted = distortion.distorted_radius(radius)
    else:
        if half > distortion.max_valid_theta():
            raise ValueError(f"{distortion} folds back inside a {fov_deg} degree field.")
        distorted = float(distortion.distorted_theta(np.array(half)))
    focal = 0.5 * math.hypot(width, height) / distorted
    return Intrinsics(width=width, height=height, fx=focal, fy=focal, cx=(width - 1) / 2.0, cy=(height - 1) / 2.0)


def corner_displacement_px(camera: Camera) -> float:
    """How far the top-left corner's ray lands from that corner in a distortion-free image, in pixels."""
    ray = camera.pixel_to_ray(np.array([-0.5]), np.array([-0.5]))
    x, y = Camera(camera.intrinsics, BrownConrady()).ray_to_pixel(ray)
    return float(math.hypot(float(x[0]) + 0.5, float(y[0]) + 0.5))


def enu_from_altaz(az_deg, alt_deg) -> np.ndarray:
    """(east, north, up) unit vectors (n, 3) for azimuths from north through east and altitudes."""
    az = np.radians(np.atleast_1d(np.asarray(az_deg, dtype=np.float64)))
    alt = np.radians(np.atleast_1d(np.asarray(alt_deg, dtype=np.float64)))
    return np.stack([np.cos(alt) * np.sin(az), np.cos(alt) * np.cos(az), np.sin(alt)], axis=-1)


def rotation_enu_to_camera(az_deg: float, alt_deg: float, roll_deg: float) -> np.ndarray:
    """Rows are the camera's right, down and forward axes in (east, north, up) coordinates."""
    forward = enu_from_altaz(az_deg, alt_deg)[0]
    az = math.radians(az_deg)
    right0 = np.array([math.cos(az), -math.sin(az), 0.0])  # horizontal, 90 degrees clockwise of the azimuth
    down0 = np.cross(forward, right0)
    roll = math.radians(roll_deg)
    right = math.cos(roll) * right0 - math.sin(roll) * down0
    down = math.cos(roll) * down0 + math.sin(roll) * right0
    return np.vstack([right, down, forward])


@dataclass(frozen=True)
class CameraTruth:
    """The exact ground-truth WCS of one frame: a camera, its attitude, a moment and a place."""

    camera: Camera
    frame: ObservationFrame
    az_deg: float
    alt_deg: float
    roll_deg: float

    @cached_property
    def rotation(self) -> np.ndarray:
        return rotation_enu_to_camera(self.az_deg, self.alt_deg, self.roll_deg)

    def enu_to_pixel(self, enu: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.camera.ray_to_pixel(np.atleast_2d(enu) @ self.rotation.T)

    def pixel_to_enu(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return self.camera.pixel_to_ray(x, y) @ self.rotation

    def icrs_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixel of ICRS directions of the observation date (after proper motion)."""
        return self.enu_to_pixel(self.frame.icrs_to_enu(ra_deg, dec_deg))

    def pixel_to_icrs(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """ICRS right ascension and declination (degrees, of the observation date) seen at pixels.

        Pixels no ray reaches come back as NaN.
        """
        with np.errstate(invalid="ignore"):
            return self.frame.enu_to_icrs(self.pixel_to_enu(x, y))

    def to_dict(self) -> dict:
        distortion = {"model": self.camera.distortion.model, **asdict(self.camera.distortion)}
        return {
            "evidenceClass": "simulated",
            "camera": {**asdict(self.camera.intrinsics), "distortion": distortion},
            "observation": {
                "utc": self.frame.utc.isoformat(),
                "latDeg": self.frame.lat_deg,
                "lonDeg": self.frame.lon_deg,
                "pressureHpa": self.frame.pressure_hpa,
                "temperatureC": self.frame.temperature_c,
            },
            "pointing": {"azDeg": self.az_deg, "altDeg": self.alt_deg, "rollDeg": self.roll_deg},
            "conventions": {
                "pixels": "x columns right, y rows down, integer values at pixel centres",
                "cameraFrame": "x right, y down, z forward",
                "horizon": "azimuth from north through east; observed (refracted) altitude",
                "roll": "positive when the camera's right side rises, seen from behind",
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CameraTruth":
        camera = dict(data["camera"])
        distortion = dict(camera.pop("distortion"))
        distortion_cls = DISTORTION_MODELS[distortion.pop("model")]
        observation = data["observation"]
        pointing = data["pointing"]
        return cls(
            camera=Camera(Intrinsics(**camera), distortion_cls(**distortion)),
            frame=ObservationFrame(
                utc=datetime.fromisoformat(observation["utc"]),
                lat_deg=observation["latDeg"],
                lon_deg=observation["lonDeg"],
                pressure_hpa=observation["pressureHpa"],
                temperature_c=observation["temperatureC"],
            ),
            az_deg=pointing["azDeg"],
            alt_deg=pointing["altDeg"],
            roll_deg=pointing["rollDeg"],
        )
