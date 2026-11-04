"""Ground-truth camera geometry of the phone-photo benchmark.

The distortion models follow OpenCV's documented conventions: the pinhole model with Brown-Conrady
radial (k1, k2, k3) and tangential (p1, p2) terms (Brown 1966, Conrady 1919) and the fisheye model of
Kannala and Brandt (2006) with k1 to k4. Pixel coordinates count columns (x) and rows (y) with
integer values at pixel centres, as the solver's detections do. The camera frame is x right, y down,
z forward; the local horizon frame is east, north, up.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from research.benchmarks.phone_photos import astrometry as am
from research.benchmarks.phone_photos import geometry as gm

MOMENT = am.ObservationFrame(utc=datetime(2026, 3, 15, 22, 0, tzinfo=timezone.utc), lat_deg=30.0, lon_deg=-5.0)


def _camera(distortion: gm.Distortion, fov_deg: float = 84.0, width: int = 1200, height: int = 900) -> gm.Camera:
    return gm.Camera(gm.intrinsics_for_diagonal_fov(width, height, fov_deg, distortion), distortion)


def _ray(theta_deg: float, phi_deg: float) -> np.ndarray:
    """Unit ray `theta` off the optical axis, `phi` measured in the image plane from +x toward +y."""
    t, p = math.radians(theta_deg), math.radians(phi_deg)
    return np.array([[math.sin(t) * math.cos(p), math.sin(t) * math.sin(p), math.cos(t)]])


# =============================================================================================
# Fields of view
# =============================================================================================


def test_35mm_equivalent_focal_lengths_give_their_diagonal_fields() -> None:
    # A full-frame diagonal is sqrt(36^2 + 24^2) = 43.27 mm
    assert gm.diagonal_fov_from_35mm_equivalent(24.0) == pytest.approx(math.degrees(2 * math.atan(43.2666 / 48.0)), abs=1e-3)
    assert gm.diagonal_fov_from_35mm_equivalent(24.0) == pytest.approx(84.06, abs=0.01)
    assert gm.diagonal_fov_from_35mm_equivalent(13.0) == pytest.approx(118.0, abs=0.05)


@pytest.mark.parametrize(
    "distortion",
    [
        gm.BrownConrady(),
        gm.BrownConrady(k1=-0.12, k2=0.03),
        gm.KannalaBrandt(),
        gm.KannalaBrandt(k1=-0.02, k2=0.004),
    ],
)
@pytest.mark.parametrize("fov_deg", [60.0, 84.0, 120.0])
def test_the_diagonal_field_puts_the_corner_edge_ray_at_half_the_field(distortion: gm.Distortion, fov_deg: float) -> None:
    camera = _camera(distortion, fov_deg)
    # The outer edge of the corner pixel sits half a pixel beyond its centre
    ray = camera.pixel_to_ray(np.array([-0.5]), np.array([-0.5]))
    assert math.degrees(math.acos(float(ray[0, 2]))) == pytest.approx(fov_deg / 2.0, abs=1e-9)


# =============================================================================================
# Projection models
# =============================================================================================


def test_pinhole_projection_follows_f_tan_theta() -> None:
    camera = _camera(gm.BrownConrady())
    k = camera.intrinsics
    for theta in (5.0, 25.0, 40.0):
        x, y = camera.ray_to_pixel(_ray(theta, 0.0))
        assert float(x[0]) == pytest.approx(k.cx + k.fx * math.tan(math.radians(theta)), abs=1e-9)
        assert float(y[0]) == pytest.approx(k.cy, abs=1e-9)
        x, y = camera.ray_to_pixel(_ray(theta, 90.0))
        assert float(y[0]) == pytest.approx(k.cy + k.fy * math.tan(math.radians(theta)), abs=1e-9)


def test_brown_conrady_follows_the_opencv_model() -> None:
    model = gm.BrownConrady(k1=-0.21, k2=0.05, k3=-0.004, p1=0.0012, p2=-0.0007)
    x, y = 0.4, -0.25
    r2 = x * x + y * y
    radial = 1 - 0.21 * r2 + 0.05 * r2**2 - 0.004 * r2**3
    expected_x = x * radial + 2 * 0.0012 * x * y + (-0.0007) * (r2 + 2 * x * x)
    expected_y = y * radial + 0.0012 * (r2 + 2 * y * y) + 2 * (-0.0007) * x * y
    xd, yd = model.distort(np.array([x]), np.array([y]))
    assert float(xd[0]) == pytest.approx(expected_x, abs=1e-15)
    assert float(yd[0]) == pytest.approx(expected_y, abs=1e-15)


def test_kannala_brandt_follows_the_opencv_fisheye_model() -> None:
    model = gm.KannalaBrandt(k1=-0.03, k2=0.006, k3=-0.0009, k4=0.0001)
    camera = gm.Camera(gm.Intrinsics(width=2000, height=1500, fx=600.0, fy=600.0, cx=999.5, cy=749.5), model)
    theta = math.radians(64.0)
    theta_d = theta * (1 - 0.03 * theta**2 + 0.006 * theta**4 - 0.0009 * theta**6 + 0.0001 * theta**8)
    x, y = camera.ray_to_pixel(_ray(64.0, 30.0))
    assert float(x[0]) == pytest.approx(999.5 + 600.0 * theta_d * math.cos(math.radians(30.0)), abs=1e-9)
    assert float(y[0]) == pytest.approx(749.5 + 600.0 * theta_d * math.sin(math.radians(30.0)), abs=1e-9)
    # With no coefficients the fisheye model is the equidistant projection r = f theta
    equidistant = gm.Camera(camera.intrinsics, gm.KannalaBrandt())
    x, _ = equidistant.ray_to_pixel(_ray(60.0, 0.0))
    assert float(x[0]) == pytest.approx(999.5 + 600.0 * math.pi / 3.0, abs=1e-9)


def test_distortion_folds_back_beyond_its_valid_radius() -> None:
    # r (1 - 0.3 r^2) peaks at r = 1 / sqrt(0.9); a ray beyond it would land on a pixel twice
    model = gm.BrownConrady(k1=-0.3)
    assert model.max_valid_radius() == pytest.approx(1.0 / math.sqrt(0.9), rel=1e-9)
    camera = gm.Camera(gm.Intrinsics(width=4000, height=3000, fx=1000.0, fy=1000.0, cx=1999.5, cy=1499.5), model)
    inside = math.degrees(math.atan(1.0))
    outside = math.degrees(math.atan(1.1))
    x, _ = camera.ray_to_pixel(np.vstack([_ray(inside, 0.0), _ray(outside, 0.0)]))
    assert math.isfinite(float(x[0])) and math.isnan(float(x[1]))
    assert gm.KannalaBrandt(k1=-0.2).max_valid_theta() == pytest.approx(1.0 / math.sqrt(0.6), rel=1e-9)


def test_rays_behind_a_rectilinear_camera_are_not_projected() -> None:
    camera = _camera(gm.BrownConrady())
    x, y = camera.ray_to_pixel(np.array([[0.1, 0.0, -1.0], [0.0, 0.0, 0.0]]))
    assert np.all(np.isnan(x)) and np.all(np.isnan(y))


@settings(max_examples=200, deadline=None)
@given(
    k1=st.floats(-0.3, 0.3),
    k2=st.floats(-0.1, 0.1),
    k3=st.floats(-0.02, 0.02),
    p1=st.floats(-0.002, 0.002),
    p2=st.floats(-0.002, 0.002),
    radius_fraction=st.floats(0.0, 0.95),
    phi=st.floats(0.0, 2 * math.pi),
)
@example(k1=0.0, k2=0.0, k3=0.0, p1=0.0, p2=0.0, radius_fraction=0.0, phi=0.0)
@example(k1=-0.3, k2=0.0, k3=0.0, p1=0.002, p2=-0.002, radius_fraction=0.95, phi=0.7)
@example(k1=0.0, k2=0.0, k3=5e-324, p1=0.0, p2=0.0, radius_fraction=0.0, phi=0.0)  # a denormal once overflowed np.roots
def test_brown_conrady_round_trips_inside_its_valid_radius(
    k1: float, k2: float, k3: float, p1: float, p2: float, radius_fraction: float, phi: float
) -> None:
    model = gm.BrownConrady(k1=k1, k2=k2, k3=k3, p1=p1, p2=p2)
    r = radius_fraction * min(model.max_valid_radius(), 1.8)
    x, y = np.array([r * math.cos(phi)]), np.array([r * math.sin(phi)])
    xd, yd = model.distort(x, y)
    assume(np.all(np.isfinite(xd)))
    xu, yu = model.undistort(xd, yd)
    assert float(np.hypot(xu - x, yu - y)[0]) < 1e-10


@settings(max_examples=200, deadline=None)
@given(
    k1=st.floats(-0.08, 0.08),
    k2=st.floats(-0.02, 0.02),
    k3=st.floats(-0.005, 0.005),
    k4=st.floats(-0.001, 0.001),
    theta_fraction=st.floats(0.0, 0.95),
)
@example(k1=0.0, k2=0.0, k3=0.0, k4=0.0, theta_fraction=0.95)
@example(k1=0.0, k2=0.0, k3=0.0, k4=2.225073858507e-311, theta_fraction=0.0)  # a denormal once overflowed np.roots
def test_kannala_brandt_round_trips_inside_its_valid_angle(k1: float, k2: float, k3: float, k4: float, theta_fraction: float) -> None:
    model = gm.KannalaBrandt(k1=k1, k2=k2, k3=k3, k4=k4)
    theta = np.array([theta_fraction * min(model.max_valid_theta(), math.radians(100.0))])
    assert float(np.abs(model.theta_from_distorted(model.distorted_theta(theta)) - theta)[0]) < 1e-12


def test_corner_displacement_measures_distance_from_a_rectilinear_image() -> None:
    k1 = -0.08
    camera = _camera(gm.BrownConrady(k1=k1), fov_deg=84.0, width=1200, height=900)
    k = camera.intrinsics
    r_distorted = math.hypot(0.5 + k.cx, 0.5 + k.cy) / k.fx  # the corner pixel's outer edge
    # Solve r (1 + k1 r^2) = r_distorted for the undistorted radius by bisection
    lo, hi = 0.0, 3.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if mid * (1 + k1 * mid * mid) < r_distorted else (lo, mid)
    assert gm.corner_displacement_px(camera) == pytest.approx(k.fx * (lo - r_distorted), rel=1e-9)
    assert gm.corner_displacement_px(_camera(gm.BrownConrady())) == pytest.approx(0.0, abs=1e-9)


# =============================================================================================
# Attitude and the sky
# =============================================================================================


def test_the_boresight_lands_on_the_principal_point() -> None:
    truth = gm.CameraTruth(_camera(gm.BrownConrady(k1=-0.05)), MOMENT, az_deg=123.0, alt_deg=35.0, roll_deg=-7.0)
    x, y = truth.enu_to_pixel(gm.enu_from_altaz(np.array([123.0]), np.array([35.0])))
    assert float(x[0]) == pytest.approx(truth.camera.intrinsics.cx, abs=1e-9)
    assert float(y[0]) == pytest.approx(truth.camera.intrinsics.cy, abs=1e-9)


@pytest.mark.parametrize("roll_deg", [0.0, 10.0, -25.0])
def test_roll_turns_the_zenith_away_from_straight_up(roll_deg: float) -> None:
    # A level camera (roll 0) keeps the zenith straight above the image centre; rolling the camera
    # counterclockwise (its right side up, seen from behind) turns the scene clockwise in the image
    truth = gm.CameraTruth(_camera(gm.BrownConrady()), MOMENT, az_deg=200.0, alt_deg=40.0, roll_deg=roll_deg)
    x, y = truth.enu_to_pixel(gm.enu_from_altaz(np.array([0.0]), np.array([90.0])))
    k = truth.camera.intrinsics
    angle = math.degrees(math.atan2(float(x[0]) - k.cx, k.cy - float(y[0])))
    assert angle == pytest.approx(roll_deg, abs=1e-9)


def test_sky_parity_puts_east_to_the_left_when_north_is_up() -> None:
    # Facing south on the meridian, the right hand points west: a star east of the meridian (larger
    # right ascension) must land left of the centre, as in any unmirrored photo of the sky
    truth = gm.CameraTruth(_camera(gm.BrownConrady()), MOMENT, az_deg=180.0, alt_deg=45.0, roll_deg=0.0)
    ra_center, dec_center = truth.pixel_to_icrs(np.array([truth.camera.intrinsics.cx]), np.array([truth.camera.intrinsics.cy]))
    x, y = truth.icrs_to_pixel(np.array([ra_center[0], ra_center[0] + 2.0]), np.array([dec_center[0], dec_center[0]]))
    assert float(x[1]) < float(x[0])
    x, y = truth.icrs_to_pixel(np.array([ra_center[0], ra_center[0]]), np.array([dec_center[0], dec_center[0] + 2.0]))
    assert float(y[1]) < float(y[0])  # north is up


@settings(max_examples=120, deadline=None)
@given(
    az=st.floats(0.0, 360.0),
    alt=st.floats(5.0, 85.0),
    roll=st.floats(-45.0, 45.0),
    fov=st.floats(50.0, 125.0),
    fisheye=st.booleans(),
    k1=st.floats(-0.05, 0.05),
    u=st.floats(0.0, 1.0),
    v=st.floats(0.0, 1.0),
)
@example(az=0.0, alt=85.0, roll=45.0, fov=125.0, fisheye=True, k1=0.05, u=0.0, v=0.0)
@example(az=359.9, alt=5.0, roll=-45.0, fov=50.0, fisheye=False, k1=-0.05, u=1.0, v=1.0)
def test_pixels_round_trip_through_the_sky(
    az: float, alt: float, roll: float, fov: float, fisheye: bool, k1: float, u: float, v: float
) -> None:
    distortion = gm.KannalaBrandt(k1=k1 * 0.4) if fisheye else gm.BrownConrady(k1=k1)
    assume(fisheye or fov <= 110.0)  # a rectilinear lens wider than this is not a phone camera
    truth = gm.CameraTruth(_camera(distortion, fov), MOMENT, az_deg=az, alt_deg=alt, roll_deg=roll)
    k = truth.camera.intrinsics
    x0, y0 = np.array([u * (k.width - 1)]), np.array([v * (k.height - 1)])
    ra, dec = truth.pixel_to_icrs(x0, y0)
    x1, y1 = truth.icrs_to_pixel(ra, dec)
    assert float(np.hypot(x1 - x0, y1 - y0)[0]) < 1e-6


def test_truth_serializes_and_restores_exactly() -> None:
    truth = gm.CameraTruth(
        gm.Camera(gm.Intrinsics(width=4032, height=3024, fx=2700.5, fy=2700.5, cx=2015.5, cy=1511.5), gm.KannalaBrandt(k1=0.01)),
        MOMENT,
        az_deg=88.0,
        alt_deg=22.0,
        roll_deg=3.5,
    )
    restored = gm.CameraTruth.from_dict(truth.to_dict())
    assert restored == truth
    ra, dec = truth.pixel_to_icrs(np.array([10.0, 3000.0]), np.array([20.0, 2000.0]))
    np.testing.assert_array_equal(np.vstack(restored.pixel_to_icrs(np.array([10.0, 3000.0]), np.array([20.0, 2000.0]))), np.vstack([ra, dec]))
