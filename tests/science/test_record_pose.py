"""A pose from the phone's pointing record when stars do not solve (pointing-5; ios-capture-companion design
component 6; spec predicted-sky-for-every-upload REQ-SKY-3 to 6).

The records are the pointing contract's first sample with a hand-built attitude [simulated]: a camera direction and
roll make a device-to-reference rotation (the documented reading, [open] until mobile-1's device check), SciPy turns
it into Core Motion's quaternion, and the gravity is the one that attitude gives. The stored frame's axes are the
back camera's sensor axes as built (`STORED_RIGHT_DEVICE`, `STORED_DOWN_DEVICE`), [open] until mobile-2's check.
The moment and place are the head-worn fixtures' open-ocean ones, which point at nobody.
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

from services.contracts.astronomy import PoseEstimate as PoseEstimateContract
from services.contracts.pointing import PointingRecord
from services.science import catalog
from services.science import predicted_sky as ps
from services.science import record_hint as rh
from services.science.catalog import angular_separation_deg, get_catalog_stars
from services.science.pose_estimate import Refusal
from services.science.solver import _build_unresolved_solution, triangle_solver_identity
from services.science.wcs_models import PointingHint

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pointing_contract.json"
SAMPLE = json.loads(FIXTURE.read_text(encoding="utf-8"))["valid"][0]["record"]
DOWN = np.array([0.0, 0.0, -1.0])
MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0
WIDTH, HEIGHT = 4032, 3024
SHA = "d" * 63 + "5"
ASSET_ID = "src_synthetic_companion"


def _direction(alt: float, az: float) -> np.ndarray:
    """Altitude and azimuth (north through east) in the reference frame: x north, y west, z up."""
    a, z = math.radians(alt), math.radians(az)
    return np.array([math.cos(a) * math.cos(z), -math.cos(a) * math.sin(z), math.sin(a)])


def _attitude(alt: float, az: float, roll: float) -> np.ndarray:
    """A device-to-reference rotation whose back camera points at (alt, az), the device's top turned `roll` degrees
    about that axis from the sky's up."""
    camera = _direction(alt, az)
    up = np.array([0.0, 0.0, 1.0]) - camera[2] * camera
    up /= np.linalg.norm(up)
    top = Rotation.from_rotvec(math.radians(roll) * camera).apply(up)
    z_axis = -camera
    return np.column_stack([np.cross(top, z_axis), top, z_axis])


def _record(rotation: np.ndarray, **changes: Any) -> PointingRecord:
    record = copy.deepcopy(SAMPLE)
    record["sourceAssetSha256"] = SHA
    x, y, z, w = Rotation.from_matrix(rotation).as_quat()
    record["heading"]["attitude"]["quaternion"] = {"x": x, "y": y, "z": z, "w": w}
    g = rotation.T @ DOWN
    record["motion"]["gravityDeviceG"] = {"x": g[0], "y": g[1], "z": g[2]}
    for path, value in changes.items():
        node = record
        keys = path.split("__")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return PointingRecord.model_validate(record)


def _pose(alt: float = 40.0, az: float = 120.0, roll: float = 0.0, *, orientation: Any = None, **changes: Any) -> Any:
    attitude = rh.record_attitude(_record(_attitude(alt, az, roll), **changes))
    assert attitude is not None
    return rh.record_pose(attitude, width=WIDTH, height=HEIGHT, orientation=orientation, moment=MOMENT, lat=LAT, lon=LON)


def _seen_direction(wcs: Any, x: float, y: float) -> np.ndarray:
    """Where a pixel of the pose's WCS lies in the reference frame (north, west, up), its altitude refracted as the
    camera saw it."""
    ra, dec = wcs.pixel_to_world(x, y)
    alt, az = catalog.icrs_to_altaz(ra, dec, LAT, LON, MOMENT, refracted=True)
    return _direction(alt, az)


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    # atan2 of the cross and dot products: acos near 1 resolves no better than about 1e-6 degrees
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a @ b)))


# The attitude's own picture: a stored pixel's device direction is the axis plus its offset along the stored right
# and down axes, at the scale the record's field of view gives (a rectilinear frame)
def _expected_direction(rotation: np.ndarray, scale_deg: float, dx: float, dy: float) -> np.ndarray:
    right, down = np.array(rh.STORED_RIGHT_DEVICE), np.array(rh.STORED_DOWN_DEVICE)
    axis = np.array(rh.CAMERA_AXIS_DEVICE["back"])
    k = math.radians(scale_deg)
    device = axis + k * (dx * right + dy * down)
    return rotation @ (device / np.linalg.norm(device))


@settings(max_examples=60, deadline=None)
@given(alt=st.floats(40.0, 80.0), az=st.floats(0.0, 359.9), roll=st.floats(-180.0, 180.0))
def test_pbt_the_nominal_wcs_draws_the_sky_where_the_attitude_turns_the_frame(alt: float, az: float, roll: float) -> None:
    rotation = _attitude(alt, az, roll)
    pose = _pose(alt, az, roll)
    assert isinstance(pose, rh.RecordPose), pose
    wcs = pose.wcs
    # The centre is the hint's centre, refraction included, to the round trip of ERFA's transforms (0.04 arcsec)
    centre = _seen_direction(wcs, wcs.crpix_x, wcs.crpix_y)
    assert _angle(centre, _direction(alt, az)) < 1e-5
    # Other pixels where the attitude and the field of view put them: within 800 px (16 degrees) every point is more
    # than 20 degrees up, where how refraction differs across the frame and the frame's one rotation, both left out,
    # are a few hundredths of a degree
    for dx, dy in ((800.0, 0.0), (0.0, 700.0), (-600.0, 500.0), (700.0, -600.0)):
        seen = _seen_direction(wcs, wcs.crpix_x + dx, wcs.crpix_y + dy)
        assert _angle(seen, _expected_direction(rotation, wcs.scale_deg_per_px, dx, dy)) < 0.05


def test_the_stored_right_and_down_are_the_sensor_axes_of_a_phone_held_upright() -> None:
    # Held upright in portrait (the device's top to the sky's up), the stored frame lies on its side: its right is
    # the device's minus y, toward the ground, and its down the device's minus x, to the person's left
    pose = _pose(30.0, 90.0, 0.0)
    assert isinstance(pose, rh.RecordPose)
    wcs = pose.wcs
    centre_alt = catalog.icrs_to_altaz(*wcs.pixel_to_world(wcs.crpix_x, wcs.crpix_y), LAT, LON, MOMENT, refracted=True)[0]
    right_alt = catalog.icrs_to_altaz(*wcs.pixel_to_world(wcs.crpix_x + 1000.0, wcs.crpix_y), LAT, LON, MOMENT, refracted=True)[0]
    assert right_alt < centre_alt - 10.0
    # Facing east, the person's left is north
    down_az = catalog.icrs_to_altaz(*wcs.pixel_to_world(wcs.crpix_x, wcs.crpix_y + 1000.0), LAT, LON, MOMENT, refracted=True)[1]
    assert 0.0 < down_az < 90.0 - 10.0


def test_the_pose_is_swept_over_the_heading_tilt_roll_and_field_of_view() -> None:
    pose = _pose(0.0, 200.0, 0.0)
    assert isinstance(pose, rh.RecordPose)
    estimate, wcs = pose.estimate, pose.wcs
    assert len(estimate.swept_wcs) == 21
    assert all((w.crpix_x, w.crpix_y, w.width, w.height) == (wcs.crpix_x, wcs.crpix_y, WIDTH, HEIGHT) for w in estimate.swept_wcs)
    hint = rh.record_hint(_record(_attitude(0.0, 200.0, 0.0)))
    assert hint is not None
    turned = math.degrees(0.004 * 11.0)
    heading, tilt = 12.0 + turned, rh.TILT_ERROR_DEG + turned
    # The corners of the heading and tilt sweep, on the horizon: the centre moves by both at once
    assert estimate.max_center_offset_deg == pytest.approx(
        max(angular_separation_deg(wcs.crval_ra_deg, wcs.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg) for w in estimate.swept_wcs)
    )
    assert math.hypot(heading, tilt) * 0.98 < estimate.max_center_offset_deg <= hint.uncertainty_deg
    fovs = sorted({round(w.scale_deg_per_px, 12) for w in estimate.swept_wcs})
    assert len(fovs) == 3
    low, mid, high = (rh.fov_of_scale(s, WIDTH, HEIGHT) for s in fovs)
    assert mid == pytest.approx(estimate.fov_diagonal_deg)
    assert low == pytest.approx(mid * (1.0 - rh.FOV_SWEEP_FRACTION), rel=1e-6)
    assert high == pytest.approx(mid * (1.0 + rh.FOV_SWEEP_FRACTION), rel=1e-6)


def test_the_pose_estimate_is_the_device_attitude_with_no_beacon_by_the_contract() -> None:
    pose = _pose()
    assert isinstance(pose, rh.RecordPose)
    estimate = pose.estimate
    assert (estimate.method, estimate.beacon, estimate.beacon_centroid_uncertainty_deg) == ("device_attitude", None, None)
    assert estimate.fov_source.startswith("The phone's pointing record")
    parsed = PoseEstimateContract.model_validate(estimate.to_contract_dict())
    assert parsed.method == "device_attitude"
    assert all("[" not in line for line in estimate.assumptions)


@pytest.mark.parametrize(("roll", "turns", "expected"), [(0.0, 1, 0.0), (20.0, 1, 20.0), (-35.0, 1, -35.0), (10.0, 0, -80.0)])
def test_the_roll_is_the_horizon_tilt_in_the_upright_frame(roll: float, turns: int, expected: float) -> None:
    # A portrait photo is stored on its side and shown after one clockwise quarter turn; a positive roll of the
    # device's top clockwise about the axis (seen from behind the phone) puts the horizon's right side lower
    pose = _pose(30.0, 90.0, -roll, orientation={"quarterTurnsClockwise": turns, "mirrored": False})
    assert isinstance(pose, rh.RecordPose)
    assert pose.estimate.roll_deg == pytest.approx(expected, abs=1e-6)


def test_no_pose_without_a_heading() -> None:
    record = copy.deepcopy(SAMPLE)
    record["heading"] = {"state": "none", "reason": "no_heading_event"}
    assert rh.record_attitude(PointingRecord.model_validate(record)) is None


@pytest.mark.parametrize(
    ("changes", "orientation", "kind"),
    [
        ({"camera__fieldOfView": {"state": "unknown"}}, None, "record_fov"),
        ({"camera__position": "front"}, None, "record_camera"),
        ({"camera__photoDimensionsPx": {"width": 3024, "height": 4032}}, None, "record_size"),
        ({}, {"quarterTurnsClockwise": 0, "mirrored": True}, "mirrored"),
        ({"camera__fieldOfView": {"state": "reported", "horizontalDeg": 179.0, "distortionCorrected": False}}, None, "record_fov"),
        # The middle of the sweep within the contract's 175 degrees, its widest past it
        ({"camera__fieldOfView": {"state": "reported", "horizontalDeg": 167.5, "distortionCorrected": False}}, None, "record_fov"),
    ],
)
def test_no_pose_when_the_record_does_not_describe_the_stored_picture(changes: dict, orientation: Any, kind: str) -> None:
    rotation = _attitude(40.0, 120.0, 0.0)
    if changes.get("camera__position") == "front":
        # The front camera looks along plus z: its record's gravity is the same, the axis turned
        rotation = rotation @ np.diag([-1.0, 1.0, -1.0])
    attitude = rh.record_attitude(_record(rotation, **changes))
    assert attitude is not None
    pose = rh.record_pose(attitude, width=WIDTH, height=HEIGHT, orientation=orientation, moment=MOMENT, lat=LAT, lon=LON)
    assert isinstance(pose, Refusal) and pose.kind == kind
    assert pose.reason and chr(0x2014) not in pose.reason and chr(0x2013) not in pose.reason
    assert str(LAT) not in pose.reason


def _unresolved() -> Any:
    return _build_unresolved_solution(
        [], "sol-synthetic-companion", ASSET_ID, SHA, 0.0, MOMENT, LAT, LON, "Too few sources.",
        "Pixel position only: no verified WCS places it on the sky.", triangle_solver_identity(get_catalog_stars()),
    )


def _asset() -> dict[str, Any]:
    return {
        "id": ASSET_ID, "sha256": SHA, "mediaKind": "image", "width": WIDTH, "height": HEIGHT,
        "capturedAtUTC": MOMENT, "observerLocation": {"latDeg": LAT, "lonDeg": LON},
    }


def _search_hint(attitude: Any) -> PointingHint:
    hint = attitude.hint
    return PointingHint(
        source_asset_sha256=SHA, used_as=("search_hint",), time_from="file", place_from="file",
        center_alt_deg=hint.center_alt_deg, center_az_deg=hint.center_az_deg, uncertainty_deg=hint.uncertainty_deg,
    )


def _never_read() -> Any:
    raise AssertionError("the frames are read only when a Moon pose is tried")


def test_complete_sky_lines_up_an_unsolved_photo_from_the_record_before_any_moon_pose() -> None:
    attitude = rh.record_attitude(_record(_attitude(40.0, 120.0, 0.0)))
    assert attitude is not None
    solution = ps.complete_sky(_unresolved(), _asset(), frames=_never_read, attitude=attitude, hint=_search_hint(attitude))
    assert solution.status == "approximate"
    assert solution.pose_estimate is not None and solution.pose_estimate.method == "device_attitude"
    assert solution.solver == ps.RECORD_POSE_SOLVER
    hint = solution.pointing_hint
    assert hint is not None and hint.used_as == ("search_hint", "pose_estimate")
    assert (hint.center_alt_deg, hint.center_az_deg) == (attitude.hint.center_alt_deg, attitude.hint.center_az_deg)
    assert hint.verified_center_offset_deg is None
    assert solution.quality.matched_inlier_count == 0 and not solution.quality.passed_independent_verification
    assert {f.evidence_class for f in solution.findings} == {"predicted"}
    inside = [f for f in solution.findings if f.subtitle.endswith(ps.INSIDE_THE_FRAME)]
    assert inside and all(ps.RECORD_LINED_UP_LIMITATION in f.limitations for f in inside)
    lined_up = [w for w in solution.warnings if w.startswith("Lined up from the phone's pointing record")]
    assert len(lined_up) == 1 and "not checked against stars" in lined_up[0]
    # The bound it states is the sweep's farthest centre plus the frame term, rounded up to a whole degree
    pose = rh.record_pose(attitude, width=WIDTH, height=HEIGHT, orientation=None, moment=MOMENT, lat=LAT, lon=LON)
    assert isinstance(pose, rh.RecordPose)
    within = math.ceil(pose.estimate.max_center_offset_deg + pose.frame_term_arcsec / 3600.0)
    assert f"within about {within} degrees of the frame's centre" in lined_up[0]
    assert str(LAT) not in " ".join(solution.warnings) and MOMENT[:10] not in " ".join(solution.warnings)
    # The contract reads it
    assert solution.to_contract_dict()["pointingHint"]["usedAs"] == ["search_hint", "pose_estimate"]


def test_complete_sky_with_a_lent_moment_keeps_the_moment_use_and_builds_the_hint_when_none_was_given() -> None:
    attitude = rh.record_attitude(_record(_attitude(40.0, 120.0, 0.0)))
    assert attitude is not None
    lent = ps.LentMoment("record", "record", 10.0, "full")
    solution = ps.complete_sky(_unresolved(), _asset(), frames=None, attitude=attitude, lent=lent)
    assert solution.status == "approximate"
    hint = solution.pointing_hint
    assert hint is not None and hint.used_as == ("pose_estimate", "moment")
    assert (hint.time_from, hint.place_from) == ("record", "record")
    assert hint.uncertainty_deg == attitude.hint.uncertainty_deg


def test_a_refused_record_pose_says_why_and_the_moon_poses_still_follow() -> None:
    attitude = rh.record_attitude(_record(_attitude(40.0, 120.0, 0.0), camera__fieldOfView={"state": "unknown"}))
    assert attitude is not None
    read: list[int] = []

    def frames() -> list[tuple[float, np.ndarray]]:
        read.append(1)
        return [(0.0, np.zeros((HEIGHT, WIDTH), dtype=np.float32))]

    solution = ps.complete_sky(_unresolved(), _asset(), frames=frames, attitude=attitude, hint=_search_hint(attitude))
    assert solution.status == "predicted_only" and solution.pose_estimate is None
    assert rh.NO_RECORD_FOV in solution.warnings
    assert read == [1]
    assert solution.pointing_hint is None


def test_a_calibrated_solve_keeps_its_wcs_and_takes_no_record_pose() -> None:
    from tests.integration.test_pointing_search_hint import _solve  # noqa: PLC0415

    solved = _solve()
    assert solved.status == "calibrated"
    attitude = rh.record_attitude(_record(_attitude(40.0, 120.0, 0.0)))
    asset = {**_asset(), "capturedAtUTC": solved.observed_at_utc.replace("+00:00", "Z"), "observerLocation": {
        "latDeg": solved.observer_lat, "lonDeg": solved.observer_lon}}
    solution = ps.complete_sky(solved, asset, frames=_never_read, attitude=attitude)
    assert solution.status == "calibrated" and solution.wcs == solved.wcs and solution.pose_estimate is None


def _starless_png(utc: str, seed: int = 5) -> bytes:
    """A companion photo with no star in it: a dark frame of sensor noise, 480 x 360, with the camera's EXIF and its
    own time and no GPS, as the companion app sends a photo [simulated]."""
    import io  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    gray = np.clip(6.0 + 2.0 * rng.standard_normal((360, 480)), 0, 255).astype(np.uint8)
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Apple", "iPhone 18 Pro, 24 mm f/1.78"
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011], sub[0xA405] = utc[:19].replace("-", ":").replace("T", " "), "+00:00", 24
    buf = io.BytesIO()
    Image.fromarray(np.stack([gray] * 3, axis=-1), mode="RGB").save(buf, format="PNG", exif=exif.tobytes())
    return buf.getvalue()


def test_a_companion_photo_no_search_solves_is_lined_up_from_its_record_through_the_routes(tmp_path: Path) -> None:
    import hashlib  # noqa: PLC0415
    import time  # noqa: PLC0415

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from services.api import app  # noqa: PLC0415
    from services.ingest import router as ingest_router  # noqa: PLC0415
    from services.ingest.store import IngestStore  # noqa: PLC0415
    from tests.integration.test_pointing_search_hint import _frame, _record as aimed  # noqa: PLC0415

    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app)
        spec = _frame().spec
        body = _starless_png(spec.utc)
        assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
        sha = hashlib.sha256(body).hexdigest()
        # A record of this photo's size with a measured heading: the hinted and the unhinted searches both find
        # nothing in a frame with no star, so the record lines it up
        record = {**aimed(sha, spec.alt_deg, spec.az_deg, accuracy=3.0), "sourceAssetSha256": sha}
        record["camera"]["photoDimensionsPx"] = {"width": 480, "height": 360}
        put = client.put(f"/api/ingest/{sha}/pointing", content=json.dumps(record), headers={"content-type": "application/json"})
        assert put.status_code == 201, put.text
        job = client.post(f"/api/ingest/{sha}/solve").json()
        deadline = time.monotonic() + 300.0
        while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.02)
            job = client.get(f"/api/ingest/jobs/{job['id']}").json()
        assert job["state"] == "succeeded", job
        solution = client.get(f"/api/ingest/{sha}/solution").json()
        assert solution["status"] == "approximate", solution["warnings"]
        assert solution["poseEstimate"]["method"] == "device_attitude"
        assert "beacon" not in solution["poseEstimate"]
        assert solution["solver"]["name"] == ps.RECORD_POSE_SOLVER.name
        hint = solution["pointingHint"]
        assert hint["usedAs"] == ["search_hint", "pose_estimate", "moment"]
        assert "verifiedCenterOffsetDeg" not in hint
        assert not [f for f in solution["findings"] if f["evidenceClass"] in ("catalog_matched", "simulated")]
        # A record of another photo size (pointing-4's own route tests: the sample's 4032 by 3024 on a 480 by 360
        # frame) keeps the solve predicted only, with the reason
        assert (spec.width, spec.height) != (4032, 3024)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
