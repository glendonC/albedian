"""The Sun pose in the solve (sky-6d; spec predicted-sky-for-every-upload REQ-SKY-4.3): complete_sky lines a daytime
photo up from the Sun and its skyline when the stars do not solve, after the record pose and before the Moon poses,
by day only, in the design lane's C23 words; everything placed through it is predicted.

Frames are synthetic day skies from a level camera [simulated] (tests/science/test_sun_pose.py); the moment and place
are open ocean and point at nobody.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from services.science import predicted_sky as ps
from services.science import sun_pose as sp
from services.science.catalog import get_catalog_stars
from services.science.detector import detect_point_sources
from services.science.solver import _build_unresolved_solution, triangle_solver_identity
from services.science.wcs_models import SourceDetection
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


def _unresolved_with(detections: list[SourceDetection], moment: str = POSE_MOMENT) -> Any:
    return _build_unresolved_solution(
        detections, "sol-synthetic-daytime", ASSET_ID, SHA, 0.0, moment, LAT, LON, "Too few sources.",
        "Pixel position only: no verified WCS places it on the sky.", triangle_solver_identity(get_catalog_stars()),
    )


def _served(solution: Any) -> list[tuple[float, float]]:
    return sorted(
        (round(f.source_detection.x_px, 6), round(f.source_detection.y_px, 6))
        for f in solution.findings if f.evidence_class == "observed" and f.source_detection is not None
    )


@pytest.mark.parametrize("turns", [0, 1])
def test_no_detection_inside_the_suns_core_is_served_beside_a_sun_pose(turns: int) -> None:
    # AUDIT's sweep of 21 Nov, finding one, and the ruling of 19 Nov (no observed glare finding until sky-6 (a)): the
    # pose drops the detections of its own frame inside the core it found, as the Moon pose drops the Moon's; the
    # glare is named in the pose's assumptions only. Points just inside and just outside the core's radius, in stored
    # pixels, on a photo stored as shown and on one stored turned (shown after one clockwise quarter turn)
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    stored = np.rot90(_level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg), turns)
    orientation = {"quarterTurnsClockwise": turns, "mirrored": False}
    pose = sp.sun_horizon_pose(stored, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=orientation, focal_35mm_mm=FOCAL35)
    assert isinstance(pose, sp.SunPose), pose
    cx, cy, r = pose.spot.x_px, pose.spot.y_px, pose.spot.radius_px
    inside = [(cx, cy), (cx + 0.95 * r, cy), (cx, cy - 0.6 * r)]
    outside = [(cx + 1.05 * r, cy), (40.0, 40.0), (850.0, 60.0)]
    detections = [
        SourceDetection(x, y, flux=50.0, snr=8.0, fwhm_px=2.4, centroid_uncertainty_px=0.2, asset_id=ASSET_ID)
        for x, y in inside + outside
    ]
    asset = {**_asset(), "width": stored.shape[1], "height": stored.shape[0], "displayOrientation": orientation}
    solution = ps.complete_sky(_unresolved_with(detections), asset, frames=_frames(stored))
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "sun_horizon"
    assert _served(solution) == sorted((round(x, 6), round(y, 6)) for x, y in outside)
    # The detector's count is what it measured; nothing else stands for the glare
    assert solution.quality.detected_candidate_count == len(detections)
    assert not any("Sun" in f.title for f in solution.findings if f.evidence_class == "observed")
    solution.to_contract_dict()


def test_a_detection_of_another_frame_on_the_cores_place_is_kept() -> None:
    # As the Moon pose's rule: only the pose's own frame shows the core there, so a point another frame measured at
    # that place is not the glare the pose found, and it is served as measured
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    pose = sp.sun_horizon_pose(frame, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(pose, sp.SunPose), pose
    same, other = (
        SourceDetection(pose.spot.x_px, pose.spot.y_px, flux=50.0, snr=8.0, fwhm_px=2.4, centroid_uncertainty_px=0.2,
                        asset_id=ASSET_ID, frame_time_sec=t)
        for t in (0.0, 1.0)
    )
    solution = ps.complete_sky(_unresolved_with([same, other]), _asset(), frames=_frames(frame))
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "sun_horizon"
    served = [f.source_detection for f in solution.findings if f.evidence_class == "observed"]
    assert [d.frame_time_sec for d in served] == [1.0]


def test_the_detectors_own_hits_on_the_core_are_dropped_and_the_rest_kept() -> None:
    # The detector on the synthetic day frame: its clipped core detection (one source at the core's centre) and any
    # other hit inside the core are dropped; every hit outside it is served as measured
    sun = sp.topocentric_sun(POSE_MOMENT, LAT, LON)
    frame = _level_camera_frame(sun.alt_deg - 15.0, sun.alt_deg)
    # Three points of light well away from the core, so the detector has hits to keep
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    for x, y in ((120.0, 140.0), (780.0, 220.0), (160.0, 520.0)):
        frame = frame + 60.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * 1.3**2))
    frame = np.clip(frame, 0, 255)
    pose = sp.sun_horizon_pose(frame, moment=POSE_MOMENT, lat=LAT, lon=LON, orientation=None, focal_35mm_mm=FOCAL35)
    assert isinstance(pose, sp.SunPose), pose
    detections = detect_point_sources(frame, asset_id=ASSET_ID)
    on_core = [d for d in detections if math.hypot(d.x_px - pose.spot.x_px, d.y_px - pose.spot.y_px) <= pose.spot.radius_px]
    assert on_core, "the synthetic core is detected, as the public lake photo's was"
    kept = [d for d in detections if all(d is not c for c in on_core)]
    assert any(math.hypot(d.x_px - 780.0, d.y_px - 220.0) < 2.0 for d in kept), "a point away from the core is kept"
    solution = ps.complete_sky(_unresolved_with(detections), _asset(), frames=_frames(frame))
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "sun_horizon"
    assert _served(solution) == sorted((round(d.x_px, 6), round(d.y_px, 6)) for d in kept)


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
