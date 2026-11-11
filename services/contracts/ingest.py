"""Ingest contracts (roadmap Track 1 M1): the Python mirror of packages/contracts/src/ingest.ts and of
`SourceAssetSchema` in packages/contracts/src/media.ts.

What the backend returns when a photo or clip is uploaded: the stored, content-addressed
`SourceAsset`, the capture metadata read from the file as hints (each with the container and tags it
came from), and the solve job whose progress counts real work. Missing metadata stays missing: a hint
is absent when the file does not hold it, and a capture time written without a zone stays a local
time with no UTC. A time a file holds only in UTC (QuickTime's creation_time) has no local time, and a
file's own time is labelled as such: it may be when the file was written or exported, so it never
becomes the asset's capture time.

Field names, patterns and rules match the Zod schemas; tests/fixtures/ingest_contract.json is written
from `build_ingest_samples()` and parsed by the TypeScript tests, so a change on one side fails one of
them. Models run in strict mode and refuse an explicit null where the Zod field is optional but not
nullable, so both sides accept exactly the same records.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from services.contracts.wire import LOOSE_WIRE_CONFIG, SHA256_HEX_PATTERN, UTC_INSTANT_PATTERN, WIRE_CONFIG, WireInt, WireNumber, wire_length

MIME_TYPE_PATTERN = r"^[a-z]+/[a-z0-9.+-]+$"
METADATA_TAG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$"
_DATE = r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{3})?"
LOCAL_DATE_TIME_PATTERN = rf"^{_DATE}T{_TIME}$"
JOB_ID_PATTERN = r"^job_[0-9a-f]{16,64}$"
# The pattern Zod's string().datetime() accepts by default: UTC with any fraction; dates checked on parse
ZOD_DATETIME_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"

INGEST_PATTERNS = {
    "sha256": SHA256_HEX_PATTERN,
    "mimeType": MIME_TYPE_PATTERN,
    "metadataTag": METADATA_TAG_PATTERN,
    "localDateTime": LOCAL_DATE_TIME_PATTERN,
    "utcInstant": UTC_INSTANT_PATTERN,
    "jobId": JOB_ID_PATTERN,
}
METADATA_CONTAINERS = ("exif", "quicktime", "heif", "xmp", "user", "synthetic")
# How to show stored pixels: mirror left to right if `mirrored`, then turn clockwise by `quarterTurnsClockwise`
# quarter turns. EXIF Orientation 1 to 8 in that form, one to one (EXIF 2.3, tag 0x0112).
EXIF_ORIENTATIONS = {
    1: {"quarterTurnsClockwise": 0, "mirrored": False},
    2: {"quarterTurnsClockwise": 0, "mirrored": True},
    3: {"quarterTurnsClockwise": 2, "mirrored": False},
    4: {"quarterTurnsClockwise": 2, "mirrored": True},
    5: {"quarterTurnsClockwise": 3, "mirrored": True},
    6: {"quarterTurnsClockwise": 1, "mirrored": False},
    7: {"quarterTurnsClockwise": 1, "mirrored": True},
    8: {"quarterTurnsClockwise": 3, "mirrored": False},
}


def compose_display_orientations(first: dict, then: dict) -> dict:
    """`first`, then `then`; a mirror reverses the sense of the turns that came before it."""
    carried = -first["quarterTurnsClockwise"] if then["mirrored"] else first["quarterTurnsClockwise"]
    return {
        "quarterTurnsClockwise": (then["quarterTurnsClockwise"] + carried) % 4,
        "mirrored": first["mirrored"] != then["mirrored"],
    }


def invert_display_orientation(orientation: dict) -> dict:
    """The orientation that undoes `orientation`, in either order."""
    turns = orientation["quarterTurnsClockwise"] if orientation["mirrored"] else -orientation["quarterTurnsClockwise"]
    return {"quarterTurnsClockwise": turns % 4, "mirrored": orientation["mirrored"]}
# What a capture time marks: when the picture or clip was recorded, or the file's own time (QuickTime
# creation_time), which may be when the file was written or exported. Only a recorded time becomes the
# asset's capturedAtUTC.
CAPTURE_TIME_MARKS = ("recorded", "file_written")
JOB_STAGES = ("store", "decode", "detect", "solve", "verify")
PROGRESS_UNITS = ("bytes", "frames", "candidates", "steps")
JOB_STATES = ("queued", "running", "succeeded", "failed", "canceled")
UTC_OFFSET_LIMIT_MINUTES = 840
CAMERA_HINT_FIELDS = (
    "make",
    "model",
    "lens_model",
    "focal_length_mm",
    "focal_length_35mm_mm",
    "exposure_time_sec",
    "f_number",
    "iso",
)

Sha256 = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
UtcInstant = Annotated[str, StringConstraints(pattern=UTC_INSTANT_PATTERN)]
JobId = Annotated[str, StringConstraints(pattern=JOB_ID_PATTERN)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Text = Annotated[str, StringConstraints(min_length=1)]


def format_utc(moment: datetime) -> str:
    """An instant in the one UTC form both sides write: seconds, milliseconds when nonzero, then Z."""
    moment = moment.astimezone(timezone.utc)
    millis = moment.microsecond // 1000
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + (f".{millis:03d}" if millis else "") + "Z"


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _Model(BaseModel):
    """Strict, camelCase on the wire; no unknown keys; null only where the Zod field is nullable."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, strict=True, extra="forbid")
    nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _no_null_for_optional(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if value is None and key not in cls.nullable:
                    raise ValueError(f"{key} may be absent but not null")
        return data


class _Loose(_Model):
    """Like a non-strict z.object: unknown keys are dropped rather than refused."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, strict=True, extra="ignore")


class MetadataSource(_Model):
    container: Literal["exif", "quicktime", "heif", "xmp", "user", "synthetic"]
    tags: Annotated[list[Annotated[str, StringConstraints(pattern=METADATA_TAG_PATTERN)]], Field(min_length=1, max_length=8)]


class AudioStreamDescriptor(_Loose):
    model_config = LOOSE_WIRE_CONFIG
    index: Annotated[WireInt, Field(ge=0)]
    codec: Text
    sample_rate_hz: Annotated[WireInt, Field(gt=0)]
    channels: Annotated[WireInt, Field(gt=0)]
    start_pts_ms: WireNumber
    duration_ms: Annotated[WireNumber, Field(ge=0)]
    sha256: wire_length(8)


class ObserverLocation(_Loose):
    lat_deg: Annotated[float, Field(ge=-90, le=90)]
    lon_deg: Annotated[float, Field(ge=-180, le=180)]
    elevation_m: Optional[float] = None
    place_name: Optional[str] = None
    uncertainty_meters: Optional[Annotated[float, Field(ge=0)]] = None


class CameraMetadata(_Loose):
    make: Optional[str] = None
    model: Optional[str] = None
    lens_model: Optional[str] = None
    focal_length_mm: Optional[Annotated[float, Field(gt=0)]] = None
    focal_length_35mm_mm: Optional[Annotated[float, Field(gt=0)]] = Field(default=None, alias="focalLength35mmMm")
    estimated_fov_deg: Optional[Annotated[float, Field(gt=0, le=175)]] = None
    exposure_time_sec: Optional[Annotated[float, Field(gt=0)]] = None
    f_number: Optional[Annotated[float, Field(gt=0)]] = None
    iso: Optional[Annotated[float, Field(gt=0)]] = None


class DisplayOrientation(_Model):
    """How to show stored pixels: mirror left to right if `mirrored`, then turn clockwise by quarter turns."""

    model_config = WIRE_CONFIG
    quarter_turns_clockwise: Annotated[WireInt, Field(ge=0, le=3)]
    mirrored: bool


class MetadataSources(_Model):
    captured_at_utc: Optional[MetadataSource] = Field(default=None, alias="capturedAtUTC")
    observer_location: Optional[MetadataSource] = None
    camera_metadata: Optional[MetadataSource] = None
    display_orientation: Optional[MetadataSource] = None


class SourceAsset(_Loose):
    id: Text
    title: Text
    media_kind: Literal["image", "video"]
    sha256: Sha256
    uri: Text
    byte_size: Optional[Annotated[int, Field(gt=0)]] = None
    mime_type: Optional[Annotated[str, StringConstraints(pattern=MIME_TYPE_PATTERN)]] = None
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    duration_ms: Annotated[float, Field(ge=0)]
    # A clip's frame rate, both fields once probed, neither until then; a still has none
    fps_numerator: Optional[Annotated[int, Field(gt=0)]] = None
    fps_denominator: Optional[Annotated[int, Field(gt=0)]] = None
    video_codec: Text
    audio_streams: list[AudioStreamDescriptor]
    captured_at_utc: Optional[Annotated[str, StringConstraints(pattern=ZOD_DATETIME_PATTERN)]] = Field(default=None, alias="capturedAtUTC")
    observer_location: Optional[ObserverLocation] = None
    camera_metadata: Optional[CameraMetadata] = None
    # Absent when the file does not say; a file that says "as stored" gives 0 turns, not mirrored
    display_orientation: Optional[DisplayOrientation] = None
    metadata_sources: Optional[MetadataSources] = None
    privacy_class: Literal["private", "consented_demo", "public_sample"]
    immutable: Literal[True]

    @field_validator("captured_at_utc")
    @classmethod
    def _real_calendar_time(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _parse_utc(value)
        return value

    @model_validator(mode="after")
    def _frame_rate_is_a_clips(self) -> "SourceAsset":
        """A frame rate is a clip's and needs both fields; a still has none, so it carries neither."""
        has_numerator, has_denominator = self.fps_numerator is not None, self.fps_denominator is not None
        if has_numerator != has_denominator:
            raise ValueError("a frame rate needs both its numerator and its denominator")
        if has_numerator and self.media_kind == "image":
            raise ValueError("a still has no frame rate")
        return self

    @model_validator(mode="after")
    def _metadata_has_sources(self) -> "SourceAsset":
        sources = self.metadata_sources
        for field in ("captured_at_utc", "observer_location", "camera_metadata", "display_orientation"):
            present = getattr(self, field) is not None
            sourced = sources is not None and getattr(sources, field) is not None
            if present != sourced:
                raise ValueError(f"{field} needs its metadata source" if present else f"a source for {field}, which is absent")
        return self


class CaptureTimeHint(_Model):
    """A local time, a UTC instant, or both; the offset is known exactly when both are."""

    nullable: ClassVar[frozenset[str]] = frozenset(
        {"localDateTime", "local_date_time", "utcOffsetMinutes", "utc_offset_minutes", "utc"}
    )
    local_date_time: Optional[Annotated[str, StringConstraints(pattern=LOCAL_DATE_TIME_PATTERN)]]
    utc_offset_minutes: Optional[Annotated[int, Field(ge=-UTC_OFFSET_LIMIT_MINUTES, le=UTC_OFFSET_LIMIT_MINUTES)]]
    utc: Optional[UtcInstant]
    marks: Literal["recorded", "file_written"]
    source: MetadataSource

    @model_validator(mode="after")
    def _times_agree(self) -> "CaptureTimeHint":
        if self.local_date_time is None and self.utc is None:
            raise ValueError("a capture time needs a local time, a UTC instant, or both")
        both = self.local_date_time is not None and self.utc is not None
        if (self.utc_offset_minutes is not None) != both:
            raise ValueError("the offset is known exactly when both the local time and UTC are")
        if both:
            local = datetime.fromisoformat(self.local_date_time)
            expected = (local - timedelta(minutes=self.utc_offset_minutes)).replace(tzinfo=timezone.utc)
            if _parse_utc(self.utc) != expected:
                raise ValueError("utc must be the local time minus the offset")
        return self


class LocationHint(_Model):
    lat_deg: Annotated[float, Field(ge=-90, le=90)]
    lon_deg: Annotated[float, Field(ge=-180, le=180)]
    altitude_m: Optional[float] = None
    horizontal_accuracy_m: Optional[Annotated[float, Field(gt=0)]] = None
    source: MetadataSource


class CameraHint(_Model):
    make: Optional[Label] = None
    model: Optional[Label] = None
    lens_model: Optional[Label] = None
    focal_length_mm: Optional[Annotated[float, Field(gt=0, le=2000)]] = None
    focal_length_35mm_mm: Optional[Annotated[float, Field(gt=0, le=5000)]] = Field(default=None, alias="focalLength35mmMm")
    exposure_time_sec: Optional[Annotated[float, Field(gt=0, le=3600)]] = None
    f_number: Optional[Annotated[float, Field(gt=0, le=64)]] = None
    iso: Optional[Annotated[float, Field(gt=0, le=10_000_000)]] = None
    source: MetadataSource

    @model_validator(mode="after")
    def _holds_a_value(self) -> "CameraHint":
        if all(getattr(self, field) is None for field in CAMERA_HINT_FIELDS):
            raise ValueError("a camera hint needs at least one value read from the file")
        return self


class OrientationHint(_Model):
    """How to show the stored pixels, as the file says, with where it says so."""

    quarter_turns_clockwise: Annotated[int, Field(ge=0, le=3)]
    mirrored: bool
    source: MetadataSource


class MetadataHints(_Model):
    capture_time: Optional[CaptureTimeHint] = None
    location: Optional[LocationHint] = None
    camera: Optional[CameraHint] = None
    orientation: Optional[OrientationHint] = None


class JobStageProgress(_Model):
    nullable: ClassVar[frozenset[str]] = frozenset({"total"})
    stage: Literal["store", "decode", "detect", "solve", "verify"]
    unit: Literal["bytes", "frames", "candidates", "steps"]
    done: Annotated[int, Field(ge=0)]
    total: Optional[Annotated[int, Field(gt=0)]]

    @model_validator(mode="after")
    def _done_within_total(self) -> "JobStageProgress":
        if self.total is not None and self.done > self.total:
            raise ValueError("done cannot exceed total")
        return self


class SolveJob(_Model):
    id: JobId
    source_asset_sha256: Sha256
    state: Literal["queued", "running", "succeeded", "failed", "canceled"]
    created_at_utc: UtcInstant = Field(alias="createdAtUTC")
    started_at_utc: Optional[UtcInstant] = Field(default=None, alias="startedAtUTC")
    finished_at_utc: Optional[UtcInstant] = Field(default=None, alias="finishedAtUTC")
    stages: list[JobStageProgress]
    solution_id: Optional[Label] = None
    error: Optional[Annotated[str, StringConstraints(min_length=1, max_length=2000)]] = None

    @model_validator(mode="after")
    def _fits_its_state(self) -> "SolveJob":
        started, finished = self.started_at_utc is not None, self.finished_at_utc is not None
        solution, error = self.solution_id is not None, self.error is not None
        rules = {
            "queued": not started and not finished and not solution and not error,
            "running": started and not finished and not solution and not error,
            "succeeded": started and finished and solution and not error,
            "failed": finished and error and not solution,
            "canceled": finished and not solution and not error,
        }
        if not rules[self.state]:
            raise ValueError(f"fields do not fit a {self.state} job")
        created = _parse_utc(self.created_at_utc)
        start = _parse_utc(self.started_at_utc) if started else None
        end = _parse_utc(self.finished_at_utc) if finished else None
        if (start is not None and start < created) or (end is not None and end < (start or created)):
            raise ValueError("times must run created, started, finished")
        order = [JOB_STAGES.index(progress.stage) for progress in self.stages]
        if any(order[i] <= order[i - 1] for i in range(1, len(order))):
            raise ValueError("stages appear once each, in pipeline order")
        return self


class IngestReceipt(_Model):
    source_asset: SourceAsset
    hints: MetadataHints
    already_stored: bool
    solve_job_id: Optional[JobId] = None

    @model_validator(mode="after")
    def _asset_carries_its_hints(self) -> "IngestReceipt":
        asset, hints = self.source_asset, self.hints
        sources = asset.metadata_sources
        if asset.privacy_class != "private":
            raise ValueError("an uploaded asset is private")
        # The backend probes every clip it keeps, so a stored clip carries the frame rate it read
        if asset.media_kind == "video" and asset.fps_numerator is None:
            raise ValueError("a stored clip carries its probed frame rate")
        # The asset's capture time is a recorded one: a file's own time stays a hint, labelled as such
        hint = hints.capture_time
        recorded = hint.utc if hint is not None and hint.marks == "recorded" else None
        if asset.captured_at_utc != recorded:
            raise ValueError("capturedAtUTC must be the recorded capture time in UTC, or absent")
        if recorded is not None and sources.captured_at_utc != hint.source:
            raise ValueError("the capture time keeps its hint source")
        if (asset.observer_location is None) != (hints.location is None):
            raise ValueError("observerLocation must be the location hint, or absent")
        if hints.location is not None:
            if (asset.observer_location.lat_deg, asset.observer_location.lon_deg) != (hints.location.lat_deg, hints.location.lon_deg):
                raise ValueError("observerLocation must be the location hint")
            if sources.observer_location != hints.location.source:
                raise ValueError("the location keeps its hint source")
        if (asset.camera_metadata is None) != (hints.camera is None):
            raise ValueError("cameraMetadata must be the camera hint, or absent")
        if hints.camera is not None:
            for field in CAMERA_HINT_FIELDS:
                if getattr(asset.camera_metadata, field) != getattr(hints.camera, field):
                    raise ValueError(f"{field} must be the camera hint's")
            if sources.camera_metadata != hints.camera.source:
                raise ValueError("the camera keeps its hint source")
        orientation, hint_orientation = asset.display_orientation, hints.orientation
        if (orientation is None) != (hint_orientation is None):
            raise ValueError("displayOrientation must be the orientation hint, or absent")
        if hint_orientation is not None:
            if (orientation.quarter_turns_clockwise, orientation.mirrored) != (
                hint_orientation.quarter_turns_clockwise, hint_orientation.mirrored
            ):
                raise ValueError("displayOrientation must be the orientation hint")
            if sources.display_orientation != hint_orientation.source:
                raise ValueError("the orientation keeps its hint source")
        return self


# =============================================================================================
# Shared samples for the TypeScript tests (synthetic values only: dates in 2031, invented places)
# =============================================================================================


def build_ingest_samples() -> dict[str, Any]:
    exif_time = {"container": "exif", "tags": ["DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"]}
    exif_gps = {"container": "exif", "tags": ["GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef"]}
    exif_camera = {"container": "exif", "tags": ["Make", "Model", "LensModel", "FocalLength", "FocalLengthIn35mmFilm", "FNumber", "ExposureTime", "ISO"]}
    photo = {
        "id": "src_5f2b0c9a7d31e448",
        "title": "Field photo (synthetic)",
        "mediaKind": "image",
        "sha256": "5f2b0c9a7d31e4486a0be2f1c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f50",
        "uri": "/api/ingest/assets/5f2b0c9a7d31e4486a0be2f1c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f50",
        "byteSize": 3184211,
        "mimeType": "image/jpeg",
        "width": 4032,
        "height": 3024,
        "durationMs": 0,
        "videoCodec": "mjpeg",
        "audioStreams": [],
        "capturedAtUTC": "2031-05-02T03:42:18.120Z",
        "observerLocation": {"latDeg": 46.5197, "lonDeg": 7.9861},
        "cameraMetadata": {"make": "Synthetic", "model": "Test Phone", "lensModel": "Main 24 mm", "focalLengthMm": 6.9,
                           "focalLength35mmMm": 24, "exposureTimeSec": 10, "fNumber": 1.78, "iso": 1600},
        "metadataSources": {"capturedAtUTC": exif_time, "observerLocation": exif_gps, "cameraMetadata": exif_camera},
        "privacyClass": "private",
        "immutable": True,
    }
    clip = {
        "id": "src_0a1b2c3d4e5f6071",
        "title": "Night clip (synthetic)",
        "mediaKind": "video",
        "sha256": "0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
        "uri": "/api/ingest/assets/0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
        "width": 1080,
        "height": 1920,
        "durationMs": 12000,
        "fpsNumerator": 30000,
        "fpsDenominator": 1001,
        "videoCodec": "hevc",
        "audioStreams": [{"index": 1, "codec": "aac", "sampleRateHz": 48000, "channels": 2, "startPtsMs": 0, "durationMs": 12000, "sha256": "aa11bb22cc33dd44"}],
        "privacyClass": "private",
        "immutable": True,
    }
    apple_time = {"container": "quicktime", "tags": ["com.apple.quicktime.creationdate"]}
    file_time = {"container": "quicktime", "tags": ["creation_time"]}
    recorded_clip_time = {"localDateTime": "2031-05-02T21:30:00", "utcOffsetMinutes": 120, "utc": "2031-05-02T19:30:00Z",
                          "marks": "recorded", "source": apple_time}
    # QuickTime's creation_time alone: a UTC instant with no local time, and the file's own time
    file_written_time = {"localDateTime": None, "utcOffsetMinutes": None, "utc": "2031-05-02T19:31:40Z",
                         "marks": "file_written", "source": file_time}
    clip_recorded = {**clip, "capturedAtUTC": "2031-05-02T19:30:00Z", "metadataSources": {"capturedAtUTC": apple_time}}
    clip_file_time = {**clip, "id": "src_7e6f5a4b3c2d1e0f", "title": "Clip with only a file time (synthetic)",
                      "sha256": "7e6f5a4b3c2d1e0f" * 4, "uri": "/api/ingest/assets/" + "7e6f5a4b3c2d1e0f" * 4}
    # A clip whose frame rate nothing has probed yet carries none: a valid asset, but never a stored one
    clip_unrated = {key: value for key, value in clip.items() if key not in ("fpsNumerator", "fpsDenominator")}
    clip_unrated.update({"id": "src_9c8b7a6f5e4d3c2b", "title": "Clip with no frame rate yet (synthetic)",
                         "sha256": "9c8b7a6f5e4d3c2b" * 4, "uri": "blob:synthetic-unprobed-clip"})
    exif_orientation = {"container": "exif", "tags": ["Orientation"]}
    orientation_hints = [
        {**EXIF_ORIENTATIONS[6], "source": exif_orientation},
        {**EXIF_ORIENTATIONS[5], "source": exif_orientation},
        # HEIF irot 1 is a quarter turn anticlockwise, so three clockwise
        {"quarterTurnsClockwise": 3, "mirrored": False, "source": {"container": "heif", "tags": ["irot"]}},
        # A clip's display matrix, from its track header
        {"quarterTurnsClockwise": 1, "mirrored": False, "source": {"container": "quicktime", "tags": ["tkhd"]}},
    ]
    portrait_photo = {**photo, "id": "src_3c4d5e6f7a8b9c0d", "title": "Portrait photo (synthetic)",
                      "sha256": "3c4d5e6f7a8b9c0d" * 4, "uri": "/api/ingest/assets/" + "3c4d5e6f7a8b9c0d" * 4,
                      "displayOrientation": dict(EXIF_ORIENTATIONS[6]),
                      "metadataSources": {**photo["metadataSources"], "displayOrientation": exif_orientation}}
    hints_full = {
        "captureTime": {"localDateTime": "2031-05-02T05:42:18.120", "utcOffsetMinutes": 120, "utc": "2031-05-02T03:42:18.120Z",
                        "marks": "recorded", "source": exif_time},
        "location": {"latDeg": 46.5197, "lonDeg": 7.9861, "altitudeM": 3466, "source": exif_gps},
        "camera": {"make": "Synthetic", "model": "Test Phone", "lensModel": "Main 24 mm", "focalLengthMm": 6.9,
                   "focalLength35mmMm": 24, "exposureTimeSec": 10, "fNumber": 1.78, "iso": 1600, "source": exif_camera},
    }
    zoneless_photo = {k: v for k, v in photo.items() if k not in ("capturedAtUTC", "observerLocation", "metadataSources")}
    zoneless_photo["metadataSources"] = {"cameraMetadata": exif_camera}
    receipts = [
        {"sourceAsset": photo, "hints": hints_full, "alreadyStored": False, "solveJobId": "job_5f2b0c9a7d31e448"},
        {"sourceAsset": zoneless_photo, "hints": {
            "captureTime": {"localDateTime": "2031-05-02T05:42:18", "utcOffsetMinutes": None, "utc": None, "marks": "recorded",
                            "source": {"container": "exif", "tags": ["DateTimeOriginal"]}},
            "camera": hints_full["camera"]}, "alreadyStored": True},
        {"sourceAsset": clip, "hints": {}, "alreadyStored": False, "solveJobId": "job_0a1b2c3d4e5f6071"},
        {"sourceAsset": clip_recorded, "hints": {"captureTime": recorded_clip_time}, "alreadyStored": False},
        # The file time stays a hint: the asset has no capture time
        {"sourceAsset": clip_file_time, "hints": {"captureTime": file_written_time}, "alreadyStored": False},
        {"sourceAsset": portrait_photo, "hints": {**hints_full, "orientation": orientation_hints[0]}, "alreadyStored": False},
    ]
    capture_times = [hints_full["captureTime"], receipts[1]["hints"]["captureTime"], recorded_clip_time, file_written_time]
    sha = clip["sha256"]
    jobs = [
        {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "queued", "createdAtUTC": "2031-05-02T19:20:00Z", "stages": []},
        {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "running", "createdAtUTC": "2031-05-02T19:20:00Z",
         "startedAtUTC": "2031-05-02T19:20:01.250Z", "stages": [
             {"stage": "store", "unit": "bytes", "done": 52811776, "total": 52811776},
             {"stage": "decode", "unit": "frames", "done": 12, "total": 36},
             {"stage": "solve", "unit": "steps", "done": 0, "total": None}]},
        {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "succeeded", "createdAtUTC": "2031-05-02T19:20:00Z",
         "startedAtUTC": "2031-05-02T19:20:01.250Z", "finishedAtUTC": "2031-05-02T19:20:41Z", "solutionId": "sol-primary", "stages": [
             {"stage": "decode", "unit": "frames", "done": 36, "total": 36},
             {"stage": "detect", "unit": "frames", "done": 36, "total": 36},
             {"stage": "solve", "unit": "candidates", "done": 1200, "total": 1200},
             {"stage": "verify", "unit": "steps", "done": 1, "total": 1}]},
        {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "failed", "createdAtUTC": "2031-05-02T19:20:00Z",
         "finishedAtUTC": "2031-05-02T19:20:00.400Z", "error": "The decoder could not read the file.", "stages": []},
        {"id": "job_0a1b2c3d4e5f6071", "sourceAssetSha256": sha, "state": "canceled", "createdAtUTC": "2031-05-02T19:20:00Z",
         "startedAtUTC": "2031-05-02T19:20:01Z", "finishedAtUTC": "2031-05-02T19:20:05Z", "stages": []},
    ]

    def with_(base: dict, path: list, value: Any) -> dict:
        out = copy.deepcopy(base)
        target = out
        for key in path[:-1]:
            target = target[key]
        if value is _DROP:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        return out

    invalid: list[dict[str, Any]] = []

    def bad(schema: str, sample: dict, reason: str) -> None:
        invalid.append({"schema": schema, "sample": sample, "reason": reason})

    bad("SourceAsset", with_(photo, ["sha256"], "a8f5c2d9104e8b771234567890abcdef"), "a 32-character hash is not a SHA-256")
    bad("SourceAsset", with_(photo, ["sha256"], photo["sha256"].upper()), "the hash is lowercase hex")
    bad("SourceAsset", with_(photo, ["metadataSources", "capturedAtUTC"], _DROP), "a capture time with no source")
    bad("SourceAsset", with_(clip, ["metadataSources"], {"capturedAtUTC": exif_time}), "a source for a capture time that is absent")
    bad("SourceAsset", with_(photo, ["metadataSources", "observerLocation"], _DROP), "a place with no source")
    bad("SourceAsset", with_(photo, ["metadataSources", "cameraMetadata", "container"], "gps"), "an unknown container")
    bad("SourceAsset", with_(photo, ["metadataSources", "cameraMetadata", "tags"], []), "a source with no tags")
    bad("SourceAsset", with_(photo, ["metadataSources", "cameraMetadata", "tags"], ["Date Time"]), "a tag with a space")
    bad("SourceAsset", with_(photo, ["metadataSources", "extra"], exif_time), "an unknown metadata source key")
    bad("SourceAsset", with_(photo, ["mimeType"], "JPEG"), "not a MIME type")
    bad("SourceAsset", with_(photo, ["byteSize"], 0), "an empty file")
    bad("SourceAsset", with_(photo, ["width"], 0), "no width")
    bad("SourceAsset", with_(photo, ["immutable"], False), "a source asset is immutable")
    bad("SourceAsset", with_(photo, ["capturedAtUTC"], "2031-02-30T03:42:18Z"), "not a calendar date")
    bad("SourceAsset", with_(photo, ["byteSize"], None), "null where the field may only be absent")
    bad("SourceAsset", {**photo, "fpsNumerator": 1, "fpsDenominator": 1}, "a still has no frame rate")
    bad("SourceAsset", with_(clip, ["fpsDenominator"], _DROP), "a frame rate needs its denominator")
    bad("SourceAsset", with_(clip, ["fpsNumerator"], _DROP), "a frame rate needs its numerator")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "privacyClass"], "public_sample"), "an uploaded asset is private")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "capturedAtUTC"], "2031-05-02T03:42:19Z"), "the asset's time differs from its hint")
    bad("IngestReceipt", with_(with_(receipts[1], ["sourceAsset", "capturedAtUTC"], "2031-05-02T05:42:18Z"),
                               ["sourceAsset", "metadataSources", "capturedAtUTC"], {"container": "exif", "tags": ["DateTimeOriginal"]}),
        "a zoneless capture time cannot become a UTC instant")
    bad("IngestReceipt", with_(receipts[0], ["hints", "captureTime", "utc"], "2031-05-02T05:42:18.120Z"), "utc is not the local time minus the offset")
    bad("IngestReceipt", with_(receipts[1], ["hints", "captureTime", "utc"], "2031-05-02T05:42:18Z"), "a UTC instant without the offset")
    bad("IngestReceipt", with_(receipts[0], ["hints", "captureTime", "utcOffsetMinutes"], 900), "an offset beyond 14 hours")
    bad("IngestReceipt", with_(receipts[0], ["hints", "location", "latDeg"], 46.52), "the asset's place differs from its hint")
    bad("IngestReceipt", with_(receipts[0], ["hints", "camera"], {"source": exif_camera}), "a camera hint with no values")
    bad("IngestReceipt", with_(receipts[0], ["hints", "camera", "model"], "Other Phone"), "the asset's camera differs from its hint")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "metadataSources", "observerLocation"], {"container": "xmp", "tags": ["exif:GPSLatitude"]}),
        "the asset's place source differs from its hint's")
    bad("IngestReceipt", with_(receipts[0], ["hints", "weather"], {}), "an unknown hint")
    bad("IngestReceipt", with_(receipts[2], ["solveJobId"], "job_123"), "a malformed job id")
    bad("IngestReceipt", {**receipts[2], "sourceAsset": clip_unrated}, "a stored clip carries its probed frame rate")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset"], {**photo, "fpsNumerator": 1, "fpsDenominator": 1}),
        "a stored still carries no frame rate")
    bad("IngestReceipt", with_(with_(receipts[4], ["sourceAsset", "capturedAtUTC"], file_written_time["utc"]),
                               ["sourceAsset", "metadataSources"], {"capturedAtUTC": file_time}),
        "a file's own time never becomes the asset's capture time")
    bad("IngestReceipt", with_(receipts[3], ["hints", "captureTime", "marks"], "file_written"),
        "the asset keeps no capture time its hint marks as the file's own")
    bad("CaptureTimeHint", {**file_written_time, "utc": None}, "no time at all")
    bad("CaptureTimeHint", {**file_written_time, "utcOffsetMinutes": 120}, "an offset without a local time")
    bad("CaptureTimeHint", {**recorded_clip_time, "utcOffsetMinutes": None}, "both times without an offset")
    bad("CaptureTimeHint", {**recorded_clip_time, "utc": None}, "an offset without UTC")
    bad("CaptureTimeHint", {k: v for k, v in file_written_time.items() if k != "marks"}, "what the time marks is required")
    bad("CaptureTimeHint", {**file_written_time, "marks": "exported"}, "an unknown mark")
    bad("CaptureTimeHint", {k: v for k, v in file_written_time.items() if k != "localDateTime"},
        "an unknown local time is null, never absent")
    bad("OrientationHint", {**orientation_hints[0], "quarterTurnsClockwise": 4}, "four quarter turns are written as none")
    bad("OrientationHint", {**orientation_hints[0], "mirrored": 1}, "mirrored is true or false")
    bad("OrientationHint", {k: v for k, v in orientation_hints[0].items() if k != "source"}, "an orientation with no source")
    bad("OrientationHint", {**orientation_hints[0], "degrees": 90}, "an unknown orientation key")
    bad("SourceAsset", with_(portrait_photo, ["metadataSources", "displayOrientation"], _DROP), "an orientation with no source")
    bad("SourceAsset", with_(photo, ["metadataSources", "displayOrientation"], exif_orientation), "a source for an orientation that is absent")
    bad("IngestReceipt", with_(receipts[5], ["hints", "orientation", "mirrored"], True), "the asset's orientation differs from its hint")
    bad("IngestReceipt", with_(receipts[5], ["hints", "orientation"], _DROP), "an orientation the hints do not hold")
    bad("IngestReceipt", with_(receipts[5], ["sourceAsset", "metadataSources", "displayOrientation"], {"container": "heif", "tags": ["irot"]}),
        "the asset's orientation source differs from its hint's")
    bad("SolveJob", with_(jobs[0], ["startedAtUTC"], "2031-05-02T19:20:01Z"), "a queued job has not started")
    bad("SolveJob", with_(jobs[1], ["startedAtUTC"], _DROP), "a running job has a start time")
    bad("SolveJob", with_(jobs[2], ["solutionId"], _DROP), "a succeeded job names its solution")
    bad("SolveJob", with_(jobs[3], ["error"], _DROP), "a failed job says why")
    bad("SolveJob", with_(jobs[4], ["solutionId"], "sol-primary"), "a canceled job has no solution")
    bad("SolveJob", with_(jobs[2], ["finishedAtUTC"], "2031-05-02T19:20:01Z"), "finished before it started")
    bad("SolveJob", with_(jobs[1], ["startedAtUTC"], "2031-05-02T19:19:59Z"), "started before it was created")
    bad("SolveJob", with_(jobs[2], ["stages"], list(reversed(jobs[2]["stages"]))), "stages out of pipeline order")
    bad("SolveJob", with_(jobs[2], ["stages"], jobs[2]["stages"][:1] * 2), "a stage twice")
    bad("SolveJob", with_(jobs[1], ["stages", 1, "done"], 37), "more done than the total")
    bad("SolveJob", with_(jobs[1], ["stages", 1, "total"], 0), "a zero total")
    bad("SolveJob", with_(jobs[1], ["stages", 2, "total"], _DROP), "total is null when unknown, never absent")
    bad("SolveJob", with_(jobs[0], ["createdAtUTC"], "2031-05-02T19:20:00+00:00"), "UTC is written with Z")
    bad("SolveJob", with_(jobs[0], ["sourceAssetSha256"], "a8f5c2d9104e8b77"), "a short hash")
    bad("SolveJob", with_(jobs[0], ["id"], "solve-1"), "a malformed job id")

    return {
        "patterns": INGEST_PATTERNS,
        "enums": {
            "metadataContainers": list(METADATA_CONTAINERS),
            "captureTimeMarks": list(CAPTURE_TIME_MARKS),
            "jobStages": list(JOB_STAGES),
            "progressUnits": list(PROGRESS_UNITS),
            "jobStates": list(JOB_STATES),
        },
        "valid": {"SourceAsset": [photo, clip, zoneless_photo, clip_recorded, clip_file_time, portrait_photo, clip_unrated],
                  "IngestReceipt": receipts,
                  "CaptureTimeHint": capture_times, "OrientationHint": orientation_hints, "SolveJob": jobs},
        "orientations": {"exif": {str(value): form for value, form in EXIF_ORIENTATIONS.items()}},
        "invalid": invalid,
    }


class _Drop:
    pass


_DROP = _Drop()
