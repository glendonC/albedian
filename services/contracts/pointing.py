"""The pointing record: the Python mirror of `PointingRecordSchema` in packages/contracts/src/pointing.ts.

What the phone's own sensors said at the moment it took a photo (spec `ios-capture-companion`, REQ-IOS-1.2, 1.3,
2, 6 and 7), kept beside the photo and never inside it, tied to it by the photo's SHA-256. It says what was measured
and how well, and never more: the attitude only with a heading, a heading only with the accuracy the device stated
and only inside `HEADING_ACCURACY_LIMIT_DEG` (otherwise "no heading" and why), a place only when the person allowed
it, every angle with its unit and frame in its field's name or type. Its fixed evidence statement says it is a hint
for the solvers, never a measurement of the sky. The Apple documentation each field rests on is listed in
pointing.ts.

Field names, limits and rules match the Zod schema, and the contract's own rules are refused in the same words
(`POINTING_RULES`). tests/fixtures/pointing_contract.json is written by tests/integration/test_pointing_contract.py
and parsed by packages/contracts/src/__tests__/pointing.test.ts, so a rule that changes on one side only fails a
test. As in the Zod schema: unknown keys are refused, an optional field is absent and never null, no number is
infinite or not a number, and a whole number may be written with a zero fraction (4032.0), as JSON lets Zod read it.

The wire rules (.lead/briefs/WIRE_RULES.md) are held here until the shared wire module is on main: wire names only,
camelCase as the Zod schema reads them (no populate_by_name, so a Python field name on the wire is an unknown key);
every whole number bounded, so it is a safe integer on both sides; the two device labels exact ASCII patterns, so a
length counts the same in UTF-16 units and code points and a lone surrogate never fits; no year 0.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from services.contracts.ingest import SHA256_HEX_PATTERN, UTC_INSTANT_PATTERN

POINTING_RECORD_FORMAT = "albedian.pointing-record"
POINTING_RECORD_FORMAT_VERSION = 1
# [proposed] The triangle solver keeps catalog stars within 68 deg of a pointing hint (services/science/solver.py,
# solve_and_verify_field) and the iPhone main camera the phone solver lists has an 84.06 deg diagonal
# ("iphone 18 pro, 48mp fusion main" in PUBLISHED_CAMERA_FOV_DEG), 42.03 deg centre to corner: an azimuth error up to
# 25.97 deg keeps every corner inside the cut, and 25 is the whole degree below. Apple states no limit of its own.
HEADING_ACCURACY_LIMIT_DEG = 25.0
# [proposed] Five update periods at the 100 Hz Apple calls the usual device-motion maximum: a running stream has a
# sample within 0.01 s of the shutter; one further than this means the stream was not running
MOTION_SAMPLE_OFFSET_LIMIT_SEC = 0.05
# [proposed] Apple's quaternion is a unit one; device doubles are within about 1e-15 of it, single precision 1e-7
UNIT_QUATERNION_TOLERANCE = 1e-6
# [proposed] The band gravity's length must lie in, in g. Apple gives gravity in G's (coremotion/cmacceleration/x) and
# states no magnitude; Core Motion's gravity is the direction of gravity at about one g [inference], so a length near
# 9.81 means metres per second squared were written, and one near zero says no direction. Unit mobile-1 measures the
# spread on a device and the band follows it.
GRAVITY_LENGTH_MIN_G = 0.9
GRAVITY_LENGTH_MAX_G = 1.1
# [proposed] The largest side of a photo or of the camera format, in pixels: 2^15, about four times the long side of a
# 48 MP frame the phone solver lists at 4:3 (8000 px [inference]). A bound that keeps it a safe integer, not a measurement.
POINTING_DIMENSION_MAX_PX = 32768
# [proposed] The most samples a motion window holds: an hour at the 100 Hz the app asks for, far past any exposure.
POINTING_SAMPLE_COUNT_MAX = 360000
# [proposed] The longest hardware model, in UTF-16 units (it is ASCII, so in code points too)
POINTING_LABEL_MAX_UTF16 = 32
# The longest system version, in UTF-16 units: what its pattern allows, three groups of four digits and two dots
POINTING_SYSTEM_VERSION_MAX_UTF16 = 14
# The two device labels as exact patterns, so that a name a person gave the device never fits: the machine
# identifier `utsname.machine` gives, letters then two numbers joined by a comma ("iPhone18,1" [inference]), and
# UIDevice.systemVersion, one to three groups of digits joined by dots [proposed]
POINTING_HARDWARE_MODEL_PATTERN = r"^[A-Za-z]+[0-9]+,[0-9]+$"
POINTING_SYSTEM_VERSION_PATTERN = r"^[0-9]{1,4}(\.[0-9]{1,4}){0,2}$"

CAMERA_POSITIONS = ("back", "front")
CAMERA_DEVICE_TYPES = ("wide_angle", "ultra_wide", "telephoto")
HEADING_ORIENTATIONS = ("portrait", "portrait_upside_down", "landscape_left", "landscape_right")
NO_HEADING_REASONS = ("no_magnetometer", "no_place", "no_heading_event", "heading_invalid", "accuracy_outside_limit")
NO_PLACE_REASONS = ("not_allowed", "no_fix", "simulated_by_software")
ACCURACY_AUTHORIZATIONS = ("full", "reduced")

POINTING_HINT_STATEMENT = (
    "The phone sensors say where the phone pointed. This record is a hint for the solvers and the basis of an "
    "approximate pose, never a measurement of the sky: anything placed from it is predicted, and a finding becomes "
    "catalog_matched only through held-out verification."
)

POINTING_RULES = {
    "headingNeedsPlace": "A heading needs the place: true north is found from the device location, so a record with no place carries no heading.",
    "headingInsideLimit": "A measured heading has an accuracy inside the limit; outside it, the record says there is no heading and why.",
    "noPlaceReasonNeedsNoPlace": "No heading for want of a place needs a record with no place.",
    "outsideLimitCarriesAccuracy": "A heading left out for its accuracy carries the accuracy the device reported, and that accuracy is outside the limit.",
    "onlyOutsideLimitReportsAccuracy": "Only a heading left out for its accuracy carries a reported accuracy.",
    "unitQuaternion": "The attitude is a unit quaternion, as Core Motion reports it.",
    "gravityNearOneG": "Gravity is given in g, so its length lies inside the stated band around one g: a zero vector, or one written in metres per second squared, is refused.",
    "sampleAtThePhoto": "The motion sample is the one at the photo: its time lies within the limit of the photo timestamp.",
    "windowRunsForward": "The motion window runs forward: it ends no earlier than it starts.",
    "placeholderSha256": "A SHA-256 of 64 zeros is a placeholder, not a hash.",
}


def _whole(value: Any) -> Any:
    """A whole number written with a zero fraction is that whole number, as Zod reads JSON; true and false are not
    numbers, as Zod refuses them (a Literal[1] alone would take True, since True == 1 in Python)."""
    if isinstance(value, bool):
        raise ValueError("a whole number, not true or false")
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(strict=True, ge=0.0, allow_inf_nan=False)]
Positive = Annotated[float, Field(strict=True, gt=0.0, allow_inf_nan=False)]
DimensionPx = Annotated[int, BeforeValidator(_whole), Field(strict=True, gt=0, le=POINTING_DIMENSION_MAX_PX)]
SampleCount = Annotated[int, BeforeValidator(_whole), Field(strict=True, gt=0, le=POINTING_SAMPLE_COUNT_MAX)]
HardwareModel = Annotated[str, StringConstraints(strict=True, max_length=POINTING_LABEL_MAX_UTF16, pattern=POINTING_HARDWARE_MODEL_PATTERN)]
SystemVersion = Annotated[str, StringConstraints(strict=True, max_length=POINTING_SYSTEM_VERSION_MAX_UTF16, pattern=POINTING_SYSTEM_VERSION_PATTERN)]


class _Model(BaseModel):
    """Strict, camelCase on the wire and nothing else (no populate_by_name: a Python field name on the wire is an
    unknown key, as the Zod schema reads it); no unknown keys; an optional field is absent, never null."""

    model_config = ConfigDict(alias_generator=to_camel, strict=True, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_null_for_optional(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None:
                    raise ValueError(f"{key} may be absent but not null")
        return data


class DeviceVector(_Model):
    """A vector in the device's own frame (Core Motion's x, y and z axes of the device)."""

    x: Finite
    y: Finite
    z: Finite


class AttitudeQuaternion(_Model):
    """CMAttitude.quaternion as the device reported it: x, y, z, w with norm one. q and -q are the same attitude."""

    x: Finite
    y: Finite
    z: Finite
    w: Finite

    @model_validator(mode="after")
    def _unit(self) -> "AttitudeQuaternion":
        # The same arithmetic, in the same order, as the Zod refinement
        norm = math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z + self.w * self.w)
        if abs(norm - 1.0) > UNIT_QUATERNION_TOLERANCE:
            raise ValueError(POINTING_RULES["unitQuaternion"])
        return self


class TrueNorthAttitude(_Model):
    reference_frame: Literal["x_true_north_z_vertical"]
    quaternion: AttitudeQuaternion


class MeasuredHeading(_Model):
    """The motion sample's attitude in the true-north frame, Core Location's true heading of the axis
    `heading_orientation` names, the accuracy it stated, and the heading event's offset from the photo."""

    state: Literal["measured"]
    attitude: TrueNorthAttitude
    true_heading_deg: Annotated[float, Field(strict=True, ge=0.0, lt=360.0, allow_inf_nan=False)]
    heading_accuracy_deg: NonNegative
    heading_orientation: Literal[HEADING_ORIENTATIONS]
    event_offset_from_photo_sec: Finite


class NoHeading(_Model):
    """No heading, and why; with accuracy_outside_limit, the accuracy the device stated."""

    state: Literal["none"]
    reason: Literal[NO_HEADING_REASONS]
    reported_heading_accuracy_deg: Optional[NonNegative] = None


class Altitude(_Model):
    above_mean_sea_level_m: Finite
    above_ellipsoid_m: Finite
    # CLLocation.verticalAccuracy: 0 or negative means the altitudes are invalid
    vertical_accuracy_m: Positive


class AllowedPlace(_Model):
    """A place the person allowed: WGS 84, the horizontal accuracy the device stated (a radius in metres), the
    accuracy granted, the altitudes only when valid, and the fix's offset from the photo."""

    state: Literal["allowed"]
    lat_deg: Annotated[float, Field(strict=True, ge=-90.0, le=90.0, allow_inf_nan=False)]
    lon_deg: Annotated[float, Field(strict=True, ge=-180.0, le=180.0, allow_inf_nan=False)]
    horizontal_accuracy_m: NonNegative
    accuracy_authorization: Literal[ACCURACY_AUTHORIZATIONS]
    altitude: Optional[Altitude] = None
    fix_offset_from_photo_sec: Finite


class NoPlace(_Model):
    state: Literal["none"]
    reason: Literal[NO_PLACE_REASONS]


class Dimensions(_Model):
    width: DimensionPx
    height: DimensionPx


class ReportedFieldOfView(_Model):
    state: Literal["reported"]
    horizontal_deg: Annotated[float, Field(strict=True, gt=0.0, lt=180.0, allow_inf_nan=False)]
    distortion_corrected: bool


class UnknownFieldOfView(_Model):
    state: Literal["unknown"]


class PointingCamera(_Model):
    """The camera that took the photo and its active format at the shutter (see PointingCameraSchema)."""

    position: Literal[CAMERA_POSITIONS]
    device_type: Literal[CAMERA_DEVICE_TYPES]
    active_format_dimensions_px: Dimensions
    photo_dimensions_px: Dimensions
    field_of_view: Annotated[Union[ReportedFieldOfView, UnknownFieldOfView], Field(discriminator="state")]
    video_zoom_factor: Annotated[float, Field(strict=True, ge=1.0, allow_inf_nan=False)]
    content_aware_distortion_correction: bool


class MotionWindow(_Model):
    start_offset_sec: Finite
    end_offset_sec: Finite
    sample_count: SampleCount
    max_rotation_rate_rad_per_sec: NonNegative


class PointingMotion(_Model):
    """The device-motion sample nearest the photo (gravity in g and rotation rate in rad/s, device frame) and the
    window of samples over the exposure."""

    sample_offset_from_photo_sec: Finite
    gravity_device_g: DeviceVector
    rotation_rate_device_rad_per_sec: DeviceVector
    window: MotionWindow


class DeviceIdentity(_Model):
    """The device as a machine identifier and a system version, never by a name a person gave it."""

    hardware_model: HardwareModel
    system_version: SystemVersion


class PointingEvidence(_Model):
    role: Literal["pointing_hint"]
    statement: Literal[POINTING_HINT_STATEMENT]


class PointingRecord(_Model):
    format: Literal["albedian.pointing-record"]
    format_version: Annotated[Literal[1], BeforeValidator(_whole)]
    source_asset_sha256: Annotated[str, StringConstraints(strict=True, pattern=SHA256_HEX_PATTERN)]
    captured_at_device_clock_utc: Annotated[str, StringConstraints(strict=True, pattern=UTC_INSTANT_PATTERN)] = Field(
        alias="capturedAtDeviceClockUTC"
    )
    device: DeviceIdentity
    camera: PointingCamera
    motion: PointingMotion
    heading: Annotated[Union[MeasuredHeading, NoHeading], Field(discriminator="state")]
    location: Annotated[Union[AllowedPlace, NoPlace], Field(discriminator="state")]
    evidence: PointingEvidence

    @field_validator("source_asset_sha256")
    @classmethod
    def _not_a_placeholder(cls, value: str) -> str:
        if set(value) == {"0"}:
            raise ValueError(POINTING_RULES["placeholderSha256"])
        return value

    @field_validator("captured_at_device_clock_utc")
    @classmethod
    def _a_calendar_instant(cls, value: str) -> str:
        # A real calendar day; datetime has no year 0, as the Zod schema refuses it too
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="after")
    def _rules(self) -> "PointingRecord":
        heading, location, motion = self.heading, self.location, self.motion
        if isinstance(heading, MeasuredHeading):
            # True north is computed from the place (xTrueNorthZVertical, CLHeading.trueHeading)
            if not isinstance(location, AllowedPlace):
                raise ValueError(POINTING_RULES["headingNeedsPlace"])
            if heading.heading_accuracy_deg > HEADING_ACCURACY_LIMIT_DEG:
                raise ValueError(POINTING_RULES["headingInsideLimit"])
        else:
            if heading.reason == "no_place" and isinstance(location, AllowedPlace):
                raise ValueError(POINTING_RULES["noPlaceReasonNeedsNoPlace"])
            reported = heading.reported_heading_accuracy_deg
            if heading.reason == "accuracy_outside_limit":
                if reported is None or reported <= HEADING_ACCURACY_LIMIT_DEG:
                    raise ValueError(POINTING_RULES["outsideLimitCarriesAccuracy"])
            elif reported is not None:
                raise ValueError(POINTING_RULES["onlyOutsideLimitReportsAccuracy"])
        gravity = motion.gravity_device_g
        # The same arithmetic, in the same order, as the Zod refinement
        length = math.sqrt(gravity.x * gravity.x + gravity.y * gravity.y + gravity.z * gravity.z)
        if length < GRAVITY_LENGTH_MIN_G or length > GRAVITY_LENGTH_MAX_G:
            raise ValueError(POINTING_RULES["gravityNearOneG"])
        if abs(motion.sample_offset_from_photo_sec) > MOTION_SAMPLE_OFFSET_LIMIT_SEC:
            raise ValueError(POINTING_RULES["sampleAtThePhoto"])
        if motion.window.end_offset_sec < motion.window.start_offset_sec:
            raise ValueError(POINTING_RULES["windowRunsForward"])
        return self
