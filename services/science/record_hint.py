"""A phone's pointing record turned into a search hint (pointing-4; ios-capture-companion design component 5).

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
Nothing here logs, and no error here holds a value of a record.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from services.contracts.pointing import PointingCamera, PointingRecord

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
    cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
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
    return math.degrees(2.0 * math.atan(half / float(camera.video_zoom_factor)))


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
    window = record.motion.window
    turned = math.degrees(float(window.max_rotation_rate_rad_per_sec) * max(0.0, float(window.end_offset_sec - window.start_offset_sec)))
    uncertainty = min(float(heading.heading_accuracy_deg) + TILT_ERROR_DEG + turned, MAX_UNCERTAINTY_DEG)
    return RecordHint(alt, az, uncertainty, record_fov_diagonal_deg(record.camera))
