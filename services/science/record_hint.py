"""A phone's pointing record turned into a search hint (pointing-4; ios-capture-companion design component 5), and
into an approximate pose when stars do not solve (pointing-5; design component 6).

The record's attitude (Core Motion's `xTrueNorthZVertical`: x to true north, z up, so y to the west) turned onto the
camera's axis gives the frame centre's altitude and azimuth; its stated uncertainty and the record's field of view
narrow the searches. A hint is a hint: the held-out gate is unchanged, so a wrong hint can lose a solve but never
make one, and nothing placed from it is observed or catalog matched.

Conventions the device checks of mobile-1 settle, built here to the documented reading and labelled [open]:
- The quaternion turns the device frame onto the reference frame: a vector with device coordinates `v` has
  reference coordinates `R(q) v` (Apple states no direction in `coremotion/cmquaternion` or `cmattitude`).
  The record carries its own check: gravity in the device frame must be `R(q)^T (0, 0, -1)`, so a record whose
  attitude and gravity disagree gives no hint rather than a wrong one.
- The back camera looks along the device's minus z axis and the front camera along plus z [inference: Apple shows
  the axes only in an illustration on `coremotion/cmmotionmanager`].
- The reported horizontal field of view lies along the active format's width, and the zoom crops around the
  centre [inference].
- A stored photo from the back camera holds the sensor's pixels as AVFoundation wrote them, landscape with the
  device's top to the left, and its orientation tag says how to show them (the job decodes with `-noautorotate`): the
  stored right is the device's minus y and the stored down its minus x [inference: AVFoundation's native video
  orientation is landscape right; mobile-2's check on a device]. A stored frame of another size than the record's
  photo dimensions gets no pose, since the record's directions could not be laid on its pixels.
Nothing here logs, and no error here holds a value of a record.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from services.contracts.pointing import PointingCamera, PointingRecord
from services.science.catalog import (
    _least_rotation,
    airless_altitude_deg,
    altaz_to_icrs,
    anchored_horizon_rotation,
    anchored_rotation_omission_arcsec,
    angular_separation_deg,
    icrs_to_frame_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.pose_estimate import Refusal, _orientation, scale_deg_per_px, upright_vector
from services.science.wcs_models import PoseEstimate, WCSModel

# [inference, open: mobile-1's axis check] The camera's axis in the device frame
CAMERA_AXIS_DEVICE = {"back": (0.0, 0.0, -1.0), "front": (0.0, 0.0, 1.0)}
# [proposed, open] The attitude's tilt error, which Apple does not state; added to the heading accuracy
TILT_ERROR_DEG = 2.0
# [proposed] How far the record's gravity may lie from the direction its attitude gives, in degrees: Core Motion's
# gravity and attitude come from one fused estimate [inference], so a larger gap says the convention or the record
# is wrong
ATTITUDE_GRAVITY_TOLERANCE_DEG = 5.0
# The hint's uncertainty is never wider than the contract allows (pointingHint.uncertaintyDeg in (0, 180])
MAX_UNCERTAINTY_DEG = 180.0
# [inference, open: mobile-2's check] A stored back-camera photo's right and down in the device frame
STORED_RIGHT_DEVICE = (0.0, -1.0, 0.0)
STORED_DOWN_DEVICE = (-1.0, 0.0, 0.0)
# [proposed, open: to be set from real captures] How far the photo's diagonal field of view may lie from the one the
# record gives, either way, as a fraction: Apple states no tolerance for a format's reported field of view
FOV_SWEEP_FRACTION = 0.05
# The contract's bound on a pose's diagonal field of view (PoseEstimate.fovDiagonalDeg)
MAX_POSE_FOV_DIAGONAL_DEG = 175.0
# The record's frame (x north, y west, z up) as east, north, up
TO_ENU = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])

# The words a person reads are the design lane's C17 (.lead/uiux/UIUX-recordwords-READY.md, decided 2026-11-19)
_NO_RECORD_POSE = "Where your iPhone pointed could not place the predicted sky on this photo: "
NO_RECORD_FOV = _NO_RECORD_POSE + "it recorded no field of view for its camera."
NO_RECORD_WIDE = _NO_RECORD_POSE + "the field of view it recorded is too wide to draw as one flat picture."
NO_RECORD_FRONT = _NO_RECORD_POSE + "the photo was taken with the front camera, whose directions are not checked yet."
NO_RECORD_SIZE = (
    _NO_RECORD_POSE + "the photo's size is not the size it recorded, so its directions cannot be laid on the photo."
)
NO_RECORD_MIRRORED = _NO_RECORD_POSE + "the photo is stored mirrored, and the pointing is the camera's own."


@dataclass(frozen=True)
class RecordHint:
    """Where the record says the frame's centre was, as altitude and azimuth (from north through east), how far that
    may be off, and the frame's diagonal field of view from the record (None when the record gives none)."""

    center_alt_deg: float
    center_az_deg: float
    uncertainty_deg: float
    fov_diagonal_deg: float | None


def rotation_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    """The rotation of the unit quaternion (x, y, z, w): device coordinates to reference coordinates [open]."""
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ]
    )


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    # A zero or NaN vector has no direction: 180 degrees, so a tolerance checked against it fails closed
    if not denom > 0.0:
        return 180.0
    cos = float(np.dot(a, b)) / denom
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def record_fov_diagonal_deg(camera: PointingCamera) -> float | None:
    """The frame's diagonal field of view from the record: for a rectilinear frame, tan(d / 2) = tan(h / 2) *
    hypot(w, H) / w / zoom, with h the reported horizontal field of view along the active format's width w and H its
    height [inference]. None when the record gives no field of view."""
    view = camera.field_of_view
    if view.state != "reported":
        return None
    width = float(camera.active_format_dimensions_px.width)
    height = float(camera.active_format_dimensions_px.height)
    half = math.tan(math.radians(float(view.horizontal_deg)) / 2.0) * math.hypot(width, height) / width
    fov = math.degrees(2.0 * math.atan(half / float(camera.video_zoom_factor)))
    return fov if fov > 0.0 else None


@dataclass(frozen=True)
class RecordAttitude:
    """What a pose from the record needs (pointing-5): its search hint, the attitude as a device-to-reference
    rotation (x north, y west, z up), the camera, the photo's dimensions as the record gives them, the reported
    horizontal field of view and zoom, the heading accuracy Core Location stated, and the most the phone turned over
    the motion window. Never logged."""

    hint: RecordHint
    rotation: tuple[tuple[float, float, float], ...]
    camera_position: str
    photo_width_px: int
    photo_height_px: int
    horizontal_fov_deg: float | None
    zoom: float
    heading_accuracy_deg: float
    turned_deg: float


@dataclass(frozen=True)
class RecordPose:
    """The pose lined up from a record: the nominal WCS, its pose estimate (the device attitude, no beacon), and the
    frame term, at most how far the WCS's one rotation puts a direction in the frame from its apparent place."""

    wcs: WCSModel
    estimate: PoseEstimate
    frame_term_arcsec: float


def _turned_deg(record: PointingRecord) -> float:
    window = record.motion.window
    return math.degrees(
        float(window.max_rotation_rate_rad_per_sec) * max(0.0, float(window.end_offset_sec - window.start_offset_sec))
    )


def record_hint(record: PointingRecord) -> RecordHint | None:
    """The search hint a record gives: None without a measured heading, or when its attitude and its gravity
    disagree. The uncertainty is the heading accuracy Core Location states, the tilt error Apple does not
    (`TILT_ERROR_DEG`), and the most the phone turned over the exposure window at its largest rotation rate."""
    heading = record.heading
    if heading.state != "measured":
        return None
    q = heading.attitude.quaternion
    rotation = rotation_matrix(float(q.x), float(q.y), float(q.z), float(q.w))
    gravity = record.motion.gravity_device_g
    measured = np.array([float(gravity.x), float(gravity.y), float(gravity.z)])
    if _angle_deg(measured, rotation.T @ np.array([0.0, 0.0, -1.0])) > ATTITUDE_GRAVITY_TOLERANCE_DEG:
        return None
    axis = rotation @ np.array(CAMERA_AXIS_DEVICE[record.camera.position])
    north, west, up = (float(v) for v in axis / np.linalg.norm(axis))
    alt = math.degrees(math.asin(max(-1.0, min(1.0, up))))
    az = math.degrees(math.atan2(-west, north)) % 360.0
    # An azimuth a hair west of north wraps to 360.0 in floating point; it is north
    az = 0.0 if az >= 360.0 else az
    uncertainty = min(float(heading.heading_accuracy_deg) + TILT_ERROR_DEG + _turned_deg(record), MAX_UNCERTAINTY_DEG)
    return RecordHint(alt, az, uncertainty, record_fov_diagonal_deg(record.camera))


def record_attitude(record: PointingRecord) -> RecordAttitude | None:
    """What a pose from the record needs: None exactly when the record gives no search hint (no measured heading, or
    an attitude its gravity disagrees with), so there is never a sensor pose without a heading."""
    hint = record_hint(record)
    if hint is None or record.heading.state != "measured":
        return None
    q = record.heading.attitude.quaternion
    rotation = rotation_matrix(float(q.x), float(q.y), float(q.z), float(q.w))
    camera = record.camera
    view = camera.field_of_view
    return RecordAttitude(
        hint=hint,
        rotation=tuple(tuple(float(v) for v in row) for row in rotation),
        camera_position=str(camera.position),
        photo_width_px=int(camera.photo_dimensions_px.width),
        photo_height_px=int(camera.photo_dimensions_px.height),
        horizontal_fov_deg=float(view.horizontal_deg) if view.state == "reported" else None,
        zoom=float(camera.video_zoom_factor),
        heading_accuracy_deg=float(record.heading.heading_accuracy_deg),
        turned_deg=_turned_deg(record),
    )


def fov_of_scale(scale_deg_per_px_: float, width: int, height: int) -> float:
    """The diagonal field of view of a rectilinear frame at this scale: the inverse of `scale_deg_per_px`."""
    return math.degrees(2.0 * math.atan(math.radians(scale_deg_per_px_) * math.hypot(width, height) / 2.0))


def _turn(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    return Rotation.from_rotvec(math.radians(angle_deg) * axis / np.linalg.norm(axis)).as_matrix()


def _wcs_of(rows_icrs: np.ndarray, fov_deg: float, width: int, height: int) -> WCSModel:
    """The TAN WCS (parity -1, no distortion, crpix at the frame centre) of a camera whose stored right, stored down
    and axis are the ICRS rows: a direction a little along the right lands right of the centre, one along the down
    lands below it."""
    right, down, axis = rows_icrs
    ra, dec = unit_vector_to_ra_dec(axis)
    a, d = math.radians(ra), math.radians(dec)
    east = np.array([-math.sin(a), math.cos(a), 0.0])
    north = np.array([-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d)])
    rotation = math.degrees(math.atan2(-float(right @ north), -float(right @ east)))
    return WCSModel(
        crval_ra_deg=float(ra) % 360.0,
        crval_dec_deg=float(dec),
        crpix_x=width / 2.0,
        crpix_y=height / 2.0,
        scale_deg_per_px=scale_deg_per_px(fov_deg, width, height),
        rotation_deg=rotation if rotation < 180.0 else rotation - 360.0,
        k1=0.0,
        k2=0.0,
        parity=-1,
        width=width,
        height=height,
    )


def _roll_deg(rows_enu: np.ndarray, turns: int) -> float:
    """The horizon's roll in the upright frame: the angle from the upright frame's right to the direction of growing
    azimuth at the centre, positive when the horizon's right side is lower; 0 for an axis straight up or down."""
    right, down, axis = rows_enu
    across = np.cross(axis, np.array([0.0, 0.0, 1.0]))
    if float(np.linalg.norm(across)) < 1e-12:
        return 0.0
    ux, uy = upright_vector(float(across @ right), float(across @ down), turns)
    roll = math.degrees(math.atan2(uy, ux))
    return 0.0 if abs(roll) < 1e-12 else roll


def _sweep(attitude: RecordAttitude, axis_enu: np.ndarray) -> list[np.ndarray]:
    """The attitude's turns of the sweep, each a rotation in east, north, up: none, the four corners of the heading
    and the tilt at once, and the roll either way; each widened by the most the phone turned over the window."""
    heading = attitude.heading_accuracy_deg + attitude.turned_deg
    tilt = TILT_ERROR_DEG + attitude.turned_deg
    up = np.array([0.0, 0.0, 1.0])
    across = np.cross(axis_enu, up)
    if float(np.linalg.norm(across)) < 1e-12:
        # Straight up or down a tilt is any horizontal turn; east serves
        across = np.array([1.0, 0.0, 0.0])
    turns = [np.eye(3)]
    for h in (heading, -heading):
        for t in (tilt, -tilt):
            turns.append(_turn(up, h) @ _turn(across, t))
    turns += [_turn(axis_enu, tilt), _turn(axis_enu, -tilt)]
    return turns


def record_pose(
    attitude: RecordAttitude,
    *,
    width: int,
    height: int,
    orientation: Mapping[str, Any] | None,
    moment: str,
    lat: float,
    lon: float,
) -> RecordPose | Refusal:
    """The approximate pose of a stored frame (`width` by `height` pixels, shown after `orientation`) from its
    record, at the solve's moment and place: the nominal WCS through the record's attitude and field of view, swept
    over the heading accuracy and the tilt at once (four corners), the roll either way, and the field of view
    `FOV_SWEEP_FRACTION` either way, 21 WCS in all; refused for a mirrored frame, the front camera, a frame of another
    size than the record's photo, or no field of view a flat picture can take.

    The sky is drawn through one rotation anchored on the frame's centre (catalog.py `anchored_horizon_rotation`):
    the centre where the record's axis points, its altitude read as refracted, and every other direction within the
    frame term of its apparent place; the camera's rows are turned by the centre's refraction so the centre lands on
    its own apparent place."""
    turns, mirrored = _orientation(orientation)
    if mirrored:
        return Refusal("mirrored", NO_RECORD_MIRRORED)
    if attitude.camera_position != "back":
        return Refusal("record_camera", NO_RECORD_FRONT)
    if (width, height) != (attitude.photo_width_px, attitude.photo_height_px):
        return Refusal("record_size", NO_RECORD_SIZE)
    hint = attitude.hint
    fov = hint.fov_diagonal_deg
    if fov is None or attitude.horizontal_fov_deg is None:
        return Refusal("record_fov", NO_RECORD_FOV)
    fovs = (fov * (1.0 - FOV_SWEEP_FRACTION), fov, fov * (1.0 + FOV_SWEEP_FRACTION))
    # A field of view so small that its scale underflows to 0 gives no WCS the contract takes (its scale is above 0)
    if not scale_deg_per_px(fovs[0], width, height) > 0.0:
        return Refusal("record_fov", NO_RECORD_FOV)
    if fovs[-1] > MAX_POSE_FOV_DIAGONAL_DEG:
        return Refusal("record_fov", NO_RECORD_WIDE)
    rotation = np.asarray(attitude.rotation, dtype=np.float64)
    rows_enu = np.array([
        TO_ENU @ rotation @ np.array(STORED_RIGHT_DEVICE),
        TO_ENU @ rotation @ np.array(STORED_DOWN_DEVICE),
        TO_ENU @ rotation @ np.array(CAMERA_AXIS_DEVICE["back"]),
    ])
    rows_enu /= np.linalg.norm(rows_enu, axis=1, keepdims=True)
    # Un-refracting a pointing near straight down stops at the nadir (-90 deg) rather than folding across the pole
    airless_alt = max(-90.0, airless_altitude_deg(hint.center_alt_deg))
    anchor_ra, anchor_dec = altaz_to_icrs(airless_alt, hint.center_az_deg, lat, lon, moment)
    frame = anchored_horizon_rotation(anchor_ra, anchor_dec, lat, lon, moment)
    seen = frame @ np.asarray(ra_dec_to_unit_vector(anchor_ra, anchor_dec), dtype=np.float64)
    # The centre's refraction: the rows turned so the axis lies on the anchor's apparent (airless) place
    lift = _least_rotation(rows_enu[2], seen / np.linalg.norm(seen))
    swept: list[WCSModel] = []
    swept_fovs: list[float] = []
    nominal: WCSModel | None = None
    for value in fovs:
        for index, turn in enumerate(_sweep(attitude, rows_enu[2])):
            rows = (frame.T @ (lift @ turn @ rows_enu.T)).T
            wcs = _wcs_of(rows, value, width, height)
            swept.append(wcs)
            swept_fovs.append(value)
            if value == fov and index == 0:
                nominal = wcs
    assert nominal is not None
    term = anchored_rotation_omission_arcsec(
        anchor_ra, anchor_dec, [(w.crval_ra_deg, w.crval_dec_deg, v / 2.0) for w, v in zip(swept, swept_fovs, strict=True)],
        lat, lon, moment,
    )
    centre_alt, centre_az = icrs_to_frame_altaz(frame, nominal.crval_ra_deg, nominal.crval_dec_deg)
    estimate = PoseEstimate(
        method="device_attitude",
        beacon=None,
        center_alt_deg=centre_alt,
        center_az_deg=0.0 if centre_az >= 360.0 else centre_az,
        roll_deg=_roll_deg(rows_enu, turns),
        fov_diagonal_deg=fov,
        fov_source=(
            f"The field of view your iPhone's camera reported: {attitude.horizontal_fov_deg:g} degrees across its "
            f"width, at a zoom of {attitude.zoom:g}."
        ),
        assumptions=_assumptions(attitude, fovs, term),
        swept_wcs=tuple(swept),
        max_center_offset_deg=max(
            angular_separation_deg(nominal.crval_ra_deg, nominal.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg)
            for w in swept
        ),
        beacon_centroid_uncertainty_deg=None,
    )
    return RecordPose(nominal, estimate, term)


def _assumptions(attitude: RecordAttitude, fovs: tuple[float, float, float], term_arcsec: float) -> tuple[str, ...]:
    low, mid, high = fovs
    # C17's six assumptions (the sweep: the heading and the tilt at once to each corner, the roll either way)
    return (
        f"Your iPhone's compass is taken to be within {attitude.heading_accuracy_deg:g} degrees, as it stated, and its "
        f"tilt within {TILT_ERROR_DEG:g} degrees, which it does not state; the predicted sky was drawn for both errors "
        "at once, to each corner and turned either way.",
        f"Your iPhone turned at most {attitude.turned_deg:.2f} degrees around the shot; the predicted sky was drawn for "
        "that much more turn.",
        f"The photo's field of view is taken as {mid:.1f} degrees across the diagonal, from the "
        f"{attitude.horizontal_fov_deg:g} degrees your iPhone reported across its width at a zoom of {attitude.zoom:g}; "
        f"the predicted sky was drawn for {low:.1f}, {mid:.1f} and {high:.1f} degrees.",
        "The photo's pixels are taken to be the camera's as your iPhone wrote them, turned as Apple's drawing of the "
        "iPhone's axes shows; not yet checked on a phone.",
        "The lens is taken to be a plain one with no distortion, centred on the frame.",
        f"The middle of the frame allows for the air bending the light; elsewhere differences of up to "
        f"{term_arcsec:,.0f} arcseconds are left out of the predicted sky.",
    )
