"""The Sun pose in the solve (sky-6d; spec predicted-sky-for-every-upload REQ-SKY-4.3): complete_sky lines a daytime
photo up from the Sun and its skyline when the stars do not solve, after the record pose and before the Moon poses,
by day only, in the design lane's C23 words; everything placed through it is predicted.

Frames are synthetic day skies from a level camera [simulated] (tests/science/test_sun_pose.py); the moment and place
are open ocean and point at nobody.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from services.science import predicted_sky as ps
from services.science import sun_pose as sp
from services.science.catalog import get_catalog_stars
from services.science.solver import _build_unresolved_solution, triangle_solver_identity
from tests.science.test_sun_pose import FOCAL35, H, LAT, LON, POSE_MOMENT, W, _level_camera_frame

SHA = "f" * 63 + "6"
ASSET_ID = "src_synthetic_daytime"


def _asset(moment: str = POSE_MOMENT, *, focal: float | None = FOCAL35) -> dict[str, Any]:
    asset: dict[str, Any] = {
        "id": ASSET_ID, "sha256": SHA, "mediaKind": "image", "width": W, "height": H, "capturedAtUTC": moment,
        "observerLocation": {"latDeg": LAT, "lonDeg": LON},
    }
    if focal is not None:
        asset["cameraMetadata"] = {"make": "Synthetic", "model": "Camera", "focalLength35mmMm": focal}
    return asset


def _unresolved(moment: str = POSE_MOMENT) -> Any:
    return _build_unresolved_solution(
        [], "sol-synthetic-daytime", ASSET_ID, SHA, 0.0, moment, LAT, LON, "Too few sources.",
        "Pixel position only: no verified WCS places it on the sky.", triangle_solver_identity(get_catalog_stars()),
    )


def _frames(luma: np.ndarray):
    return lambda: [(0.0, luma)]


def test_an_unsolved_daytime_photo_is_lined_up_from_the_sun_and_its_skyline() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    solution = ps.complete_sky(_unresolved(), _asset(), frames=_frames(frame))
    assert solution.status == "approximate", solution.warnings
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "sun_horizon"
    assert solution.pose_estimate.beacon == "sun"
    assert solution.solver == ps.SUN_POSE_SOLVER
    assert solution.quality.matched_inlier_count == 0 and not solution.quality.passed_independent_verification
    assert {f.evidence_class for f in solution.findings} <= {"observed", "predicted"}
    inside = [f for f in solution.findings if f.subtitle.endswith(ps.INSIDE_THE_FRAME)]
    assert inside and all(f.limitations[-1] == ps.SUN_LINED_UP_LIMITATION for f in inside)
    assert ps.SUN_LINED_UP_LIMITATION == "Placed in the frame only from the Sun and the skyline: predicted, not checked against stars."
    lined = [w for w in solution.warnings if w.startswith("Lined up by taking a bright spot to be the Sun")]
    assert len(lined) == 1
    within = int(lined[0].split("within about ")[1].split(" degrees")[0])
    assert lined[0] == (
        f"Lined up by taking a bright spot to be the Sun, with the skyline taken to be level, within about {within} "
        "degrees at the middle of the frame and more toward the edges: predicted, not checked against stars. The "
        "camera's clock agreed with the Sun's height, through the file's 24 mm lens."
    )
    estimate = solution.pose_estimate
    assert within == int(np.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg))
    assert str(LAT) not in " ".join(solution.warnings) and POSE_MOMENT[:10] not in " ".join(solution.warnings)
    solution.to_contract_dict()


def test_a_camera_clock_that_disagrees_with_the_sun_gets_no_pose_and_says_so() -> None:
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg + 4.0)
    solution = ps.complete_sky(_unresolved(), _asset(), frames=_frames(frame))
    assert solution.status == "predicted_only" and solution.pose_estimate is None
    assert any(w.startswith(sp.FAMILY_PREFIX + "the camera's clock or its time zone looks wrong.") for w in solution.warnings)


def test_a_daytime_photo_with_no_35mm_focal_length_gets_the_day_refusal_and_no_frame_is_read() -> None:
    # The Sun pose's clock check needs the file's 35 mm focal length; without one the Moon poses refuse by day as
    # before (pose_estimate.DAYTIME) and the frames are not read
    from services.science.pose_estimate import DAYTIME  # noqa: PLC0415

    read: list[int] = []

    def frames() -> list:
        read.append(1)
        return [(0.0, np.zeros((H, W)))]

    solution = ps.complete_sky(_unresolved(), _asset(focal=None), frames=frames)
    assert solution.status == "predicted_only" and DAYTIME in solution.warnings and read == []


def test_by_night_the_sun_pose_is_not_tried() -> None:
    night = "2031-06-21T02:00:00Z"
    assert sp.topocentric_sun(night, LAT, LON).alt_deg < 0.0
    solution = ps.complete_sky(_unresolved(night), _asset(night), frames=_frames(np.zeros((H, W))))
    assert solution.pose_estimate is None
    assert not any(w.startswith(sp.FAMILY_PREFIX + "the Sun was too low") for w in solution.warnings)
    assert sp.SUN_POSE_SOLVER_NAME not in str(solution.solver)


def test_the_record_pose_comes_before_the_sun_pose() -> None:
    from tests.science.test_record_pose import _attitude, _record  # noqa: PLC0415
    from services.science import record_hint as rh  # noqa: PLC0415

    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    record = _record(_attitude(40.0, 120.0, 0.0), camera__photoDimensionsPx={"width": W, "height": H})
    attitude = rh.record_attitude(record)
    assert attitude is not None
    solution = ps.complete_sky(_unresolved(), _asset(), frames=_frames(frame), attitude=attitude)
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "device_attitude"


def test_a_daytime_recording_with_no_readable_frame_says_so() -> None:
    from services.science.pose_estimate import NO_FRAMES  # noqa: PLC0415

    solution = ps.complete_sky(_unresolved(), _asset(), frames=lambda: [])
    assert solution.status == "predicted_only" and NO_FRAMES in solution.warnings
