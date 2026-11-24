"""A pointing record turned into a search hint (pointing-4; ios-capture-companion design component 5).

The records are the pointing contract's first sample with a hand-built attitude [simulated]: a camera direction and
roll make a device-to-reference rotation (the documented reading, [open] until mobile-1's device check), SciPy turns
it into Core Motion's quaternion, and the gravity is the one that attitude gives unless a test says otherwise.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from hypothesis import given, settings, strategies as st
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from services.contracts.pointing import PointingRecord
from services.science import record_hint as rh

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pointing_contract.json"
SAMPLE = json.loads(FIXTURE.read_text(encoding="utf-8"))["valid"][0]["record"]
DOWN = np.array([0.0, 0.0, -1.0])


def _direction(alt: float, az: float) -> np.ndarray:
    """Altitude and azimuth (north through east) in the reference frame: x north, y west, z up."""
    a, z = math.radians(alt), math.radians(az)
    return np.array([math.cos(a) * math.cos(z), -math.cos(a) * math.sin(z), math.sin(a)])


def _attitude(alt: float, az: float, roll: float, position: str) -> np.ndarray:
    """A device-to-reference rotation whose camera axis points at (alt, az), the device's top turned `roll` degrees
    about that axis from the sky's up."""
    camera = _direction(alt, az)
    up = np.array([0.0, 0.0, 1.0]) - camera[2] * camera
    up /= np.linalg.norm(up)
    top = Rotation.from_rotvec(math.radians(roll) * camera).apply(up)
    z_axis = -camera if position == "back" else camera
    x_axis = np.cross(top, z_axis)
    return np.column_stack([x_axis, top, z_axis])


def _record(rotation: np.ndarray, *, position: str = "back", gravity: np.ndarray | None = None, **changes: Any) -> PointingRecord:
    record = copy.deepcopy(SAMPLE)
    x, y, z, w = Rotation.from_matrix(rotation).as_quat()
    record["heading"]["attitude"]["quaternion"] = {"x": x, "y": y, "z": z, "w": w}
    g = rotation.T @ DOWN if gravity is None else gravity
    record["motion"]["gravityDeviceG"] = {"x": g[0], "y": g[1], "z": g[2]}
    record["camera"]["position"] = position
    for path, value in changes.items():
        node = record
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return PointingRecord.model_validate(record)


def _az_gap(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@settings(max_examples=300, deadline=None)
@given(
    alt=st.floats(-80.0, 85.0), az=st.floats(0.0, 359.999), roll=st.floats(-180.0, 180.0),
    position=st.sampled_from(("back", "front")),
)
def test_pbt_the_hint_points_where_the_attitude_turns_the_camera(alt: float, az: float, roll: float, position: str) -> None:
    hint = rh.record_hint(_record(_attitude(alt, az, roll, position), position=position))
    assert hint is not None
    assert abs(hint.center_alt_deg - alt) < 1e-6
    assert _az_gap(hint.center_az_deg, az) < 1e-6
    assert 0.0 <= hint.center_az_deg < 360.0


@pytest.mark.parametrize(("tilt", "alt"), [(0.0, -90.0), (60.0, -30.0), (90.0, 0.0), (120.0, 30.0)])
def test_mobile_1s_device_check_as_the_documented_reading_predicts_it(tilt: float, alt: float) -> None:
    # The phone flat, screen up, its top to true north (device y north, z up, x east), then its top tilted up by
    # `tilt` about the device's x axis: the back camera turns from straight down to the north horizon and above.
    # The owner's one-minute check is this pose; [open] until its recorded quaternion is compared with this
    t = math.radians(tilt)
    north, west, up = np.eye(3)
    top = math.cos(t) * north + math.sin(t) * up
    screen = -math.sin(t) * north + math.cos(t) * up
    rotation = np.column_stack([np.cross(top, screen), top, screen])
    assert np.allclose(rotation[:, 0], -west)
    hint = rh.record_hint(_record(rotation))
    assert hint is not None and abs(hint.center_alt_deg - alt) < 1e-9
    if tilt > 0.0:
        # Straight down has no azimuth; every tilt after it looks north, past the horizon at 90 deg
        assert _az_gap(hint.center_az_deg, 0.0) < 1e-6


def test_a_record_with_no_heading_gives_no_hint() -> None:
    record = copy.deepcopy(SAMPLE)
    record["heading"] = {"state": "none", "reason": "no_heading_event"}
    assert rh.record_hint(PointingRecord.model_validate(record)) is None


@pytest.mark.parametrize(("off_deg", "gives"), [(4.0, True), (6.0, False), (90.0, False)])
def test_an_attitude_its_own_gravity_disagrees_with_gives_no_hint(off_deg: float, gives: bool) -> None:
    rotation = _attitude(40.0, 120.0, 10.0, "back")
    true_gravity = rotation.T @ DOWN
    turned = Rotation.from_rotvec(math.radians(off_deg) * np.cross(true_gravity, [1.0, 0.0, 0.0]) /
                                  np.linalg.norm(np.cross(true_gravity, [1.0, 0.0, 0.0]))).apply(true_gravity)
    assert (rh.record_hint(_record(rotation, gravity=turned)) is not None) == gives


def test_the_reverse_reading_of_the_quaternion_is_caught_by_the_records_gravity() -> None:
    # Gravity as the other reading would give it: a record written so fails the check, so a reversed convention
    # gives no hint rather than a wrong one
    rotation = _attitude(35.0, 250.0, 0.0, "back")
    assert rh.record_hint(_record(rotation, gravity=rotation @ DOWN)) is None


def test_the_uncertainty_is_the_heading_accuracy_the_tilt_error_and_the_turn_over_the_window() -> None:
    # The sample: accuracy 12, a window from -0.5 s to 10.5 s at most 0.004 rad/s
    record = _record(_attitude(30.0, 200.0, 0.0, "back"), heading__headingAccuracyDeg=10.0)
    hint = rh.record_hint(record)
    assert hint is not None
    assert hint.uncertainty_deg == pytest.approx(10.0 + rh.TILT_ERROR_DEG + math.degrees(0.004 * 11.0), abs=1e-9)
    spun = _record(_attitude(30.0, 200.0, 0.0, "back"), motion__window__maxRotationRateRadPerSec=1.0)
    spun_hint = rh.record_hint(spun)
    assert spun_hint is not None and spun_hint.uncertainty_deg == rh.MAX_UNCERTAINTY_DEG


@pytest.mark.parametrize(("zoom", "diagonal"), [(1.0, 81.8608), (2.0, 46.8810)])
def test_the_records_field_of_view_gives_the_frames_diagonal(zoom: float, diagonal: float) -> None:
    # 69.5 deg along a 4032 px width of a 4032 x 3024 format: tan(d / 2) = tan(34.75 deg) * 5040 / 4032 / zoom
    hint = rh.record_hint(_record(_attitude(30.0, 200.0, 0.0, "back"), camera__videoZoomFactor=zoom))
    assert hint is not None and hint.fov_diagonal_deg == pytest.approx(diagonal, abs=1e-4)


def test_a_record_with_no_field_of_view_gives_none() -> None:
    hint = rh.record_hint(_record(_attitude(30.0, 200.0, 0.0, "back"), camera__fieldOfView={"state": "unknown"}))
    assert hint is not None and hint.fov_diagonal_deg is None


def test_a_field_of_view_underflowing_to_zero_under_extreme_zoom_gives_none() -> None:
    rec = _record(
        _attitude(30.0, 200.0, 0.0, "back"),
        camera__fieldOfView={"state": "reported", "horizontalDeg": 1e-300, "distortionCorrected": False},
        camera__videoZoomFactor=1e25,
    )
    hint = rh.record_hint(rec)
    assert hint is not None and hint.fov_diagonal_deg is None


@pytest.mark.parametrize("bad_vec", [np.zeros(3), np.array([math.nan, 0.0, -1.0])])
def test_zero_or_nan_gravity_vector_fails_closed_instead_of_bypassing_tolerance(bad_vec: np.ndarray) -> None:
    from services.contracts.pointing import DeviceVector  # noqa: PLC0415

    valid = _record(_attitude(35.0, 200.0, 0.0, "back"))
    bypassed_motion = valid.motion.model_copy(
        update={"gravity_device_g": DeviceVector.model_construct(x=float(bad_vec[0]), y=float(bad_vec[1]), z=float(bad_vec[2]))}
    )
    bypassed = valid.model_copy(update={"motion": bypassed_motion})
    assert rh.record_hint(bypassed) is None
