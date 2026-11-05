"""Ingest contracts (roadmap Track 1 M1): the Python mirror of packages/contracts/src/ingest.ts and of
`SourceAssetSchema` in packages/contracts/src/media.ts.

What the backend returns when a photo or clip is uploaded: the stored, content-addressed
`SourceAsset`, the capture metadata read from the file as hints (each with the container and tags it
came from), and the solve job whose progress counts real work. Missing metadata stays missing: a hint
is absent when the file does not hold it, and a capture time written without a zone stays a local
time with no UTC.

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

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
MIME_TYPE_PATTERN = r"^[a-z]+/[a-z0-9.+-]+$"
METADATA_TAG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$"
_DATE = r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{3})?"
LOCAL_DATE_TIME_PATTERN = rf"^{_DATE}T{_TIME}$"
UTC_INSTANT_PATTERN = rf"^{_DATE}T{_TIME}Z$"
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
METADATA_CONTAINERS = ("exif", "quicktime", "xmp", "user", "synthetic")
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
    container: Literal["exif", "quicktime", "xmp", "user", "synthetic"]
    tags: Annotated[list[Annotated[str, StringConstraints(pattern=METADATA_TAG_PATTERN)]], Field(min_length=1, max_length=8)]


class AudioStreamDescriptor(_Loose):
    index: Annotated[int, Field(ge=0)]
    codec: Text
    sample_rate_hz: Annotated[int, Field(gt=0)]
    channels: Annotated[int, Field(gt=0)]
    start_pts_ms: float
    duration_ms: Annotated[float, Field(ge=0)]
    sha256: Annotated[str, StringConstraints(min_length=8)]


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


class MetadataSources(_Model):
    captured_at_utc: Optional[MetadataSource] = Field(default=None, alias="capturedAtUTC")
    observer_location: Optional[MetadataSource] = None
    camera_metadata: Optional[MetadataSource] = None


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
    fps_numerator: Annotated[int, Field(gt=0)]
    fps_denominator: Annotated[int, Field(gt=0)]
    video_codec: Text
    audio_streams: list[AudioStreamDescriptor]
    captured_at_utc: Optional[Annotated[str, StringConstraints(pattern=ZOD_DATETIME_PATTERN)]] = Field(default=None, alias="capturedAtUTC")
    observer_location: Optional[ObserverLocation] = None
    camera_metadata: Optional[CameraMetadata] = None
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
    def _metadata_has_sources(self) -> "SourceAsset":
        sources = self.metadata_sources
        for field in ("captured_at_utc", "observer_location", "camera_metadata"):
            present = getattr(self, field) is not None
            sourced = sources is not None and getattr(sources, field) is not None
            if present != sourced:
                raise ValueError(f"{field} needs its metadata source" if present else f"a source for {field}, which is absent")
        return self


class CaptureTimeHint(_Model):
    nullable: ClassVar[frozenset[str]] = frozenset({"utcOffsetMinutes", "utc", "utc_offset_minutes"})
    local_date_time: Annotated[str, StringConstraints(pattern=LOCAL_DATE_TIME_PATTERN)]
    utc_offset_minutes: Optional[Annotated[int, Field(ge=-UTC_OFFSET_LIMIT_MINUTES, le=UTC_OFFSET_LIMIT_MINUTES)]]
    utc: Optional[UtcInstant]
    source: MetadataSource

    @model_validator(mode="after")
    def _utc_follows_the_offset(self) -> "CaptureTimeHint":
        if (self.utc_offset_minutes is None) != (self.utc is None):
            raise ValueError("utc is known exactly when the file gives the offset")
        local = datetime.fromisoformat(self.local_date_time)
        if self.utc is not None and self.utc_offset_minutes is not None:
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


class MetadataHints(_Model):
    capture_time: Optional[CaptureTimeHint] = None
    location: Optional[LocationHint] = None
    camera: Optional[CameraHint] = None


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
        utc = hints.capture_time.utc if hints.capture_time is not None else None
        if asset.captured_at_utc != utc:
            raise ValueError("capturedAtUTC must be the capture-time hint in UTC, or absent")
        if utc is not None and sources.captured_at_utc != hints.capture_time.source:
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
        "fpsNumerator": 1,
        "fpsDenominator": 1,
        "videoCodec": "mjpeg",
        "audioStreams": [],
        "capturedAtUTC": "2031-05-02T19:14:09.120Z",
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
    hints_full = {
        "captureTime": {"localDateTime": "2031-05-02T21:14:09.120", "utcOffsetMinutes": 120, "utc": "2031-05-02T19:14:09.120Z", "source": exif_time},
        "location": {"latDeg": 46.5197, "lonDeg": 7.9861, "altitudeM": 3466, "source": exif_gps},
        "camera": {"make": "Synthetic", "model": "Test Phone", "lensModel": "Main 24 mm", "focalLengthMm": 6.9,
                   "focalLength35mmMm": 24, "exposureTimeSec": 10, "fNumber": 1.78, "iso": 1600, "source": exif_camera},
    }
    zoneless_photo = {k: v for k, v in photo.items() if k not in ("capturedAtUTC", "observerLocation", "metadataSources")}
    zoneless_photo["metadataSources"] = {"cameraMetadata": exif_camera}
    receipts = [
        {"sourceAsset": photo, "hints": hints_full, "alreadyStored": False, "solveJobId": "job_5f2b0c9a7d31e448"},
        {"sourceAsset": zoneless_photo, "hints": {
            "captureTime": {"localDateTime": "2031-05-02T21:14:09", "utcOffsetMinutes": None, "utc": None, "source": {"container": "exif", "tags": ["DateTimeOriginal"]}},
            "camera": hints_full["camera"]}, "alreadyStored": True},
        {"sourceAsset": clip, "hints": {}, "alreadyStored": False, "solveJobId": "job_0a1b2c3d4e5f6071"},
    ]
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
    bad("SourceAsset", with_(photo, ["capturedAtUTC"], "2031-02-30T19:14:09Z"), "not a calendar date")
    bad("SourceAsset", with_(photo, ["byteSize"], None), "null where the field may only be absent")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "privacyClass"], "public_sample"), "an uploaded asset is private")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "capturedAtUTC"], "2031-05-02T19:14:10Z"), "the asset's time differs from its hint")
    bad("IngestReceipt", with_(with_(receipts[1], ["sourceAsset", "capturedAtUTC"], "2031-05-02T21:14:09Z"),
                               ["sourceAsset", "metadataSources", "capturedAtUTC"], {"container": "exif", "tags": ["DateTimeOriginal"]}),
        "a zoneless capture time cannot become a UTC instant")
    bad("IngestReceipt", with_(receipts[0], ["hints", "captureTime", "utc"], "2031-05-02T21:14:09.120Z"), "utc is not the local time minus the offset")
    bad("IngestReceipt", with_(receipts[1], ["hints", "captureTime", "utc"], "2031-05-02T21:14:09Z"), "a UTC instant without the offset")
    bad("IngestReceipt", with_(receipts[0], ["hints", "captureTime", "utcOffsetMinutes"], 900), "an offset beyond 14 hours")
    bad("IngestReceipt", with_(receipts[0], ["hints", "location", "latDeg"], 46.52), "the asset's place differs from its hint")
    bad("IngestReceipt", with_(receipts[0], ["hints", "camera"], {"source": exif_camera}), "a camera hint with no values")
    bad("IngestReceipt", with_(receipts[0], ["hints", "camera", "model"], "Other Phone"), "the asset's camera differs from its hint")
    bad("IngestReceipt", with_(receipts[0], ["sourceAsset", "metadataSources", "observerLocation"], {"container": "xmp", "tags": ["exif:GPSLatitude"]}),
        "the asset's place source differs from its hint's")
    bad("IngestReceipt", with_(receipts[0], ["hints", "weather"], {}), "an unknown hint")
    bad("IngestReceipt", with_(receipts[2], ["solveJobId"], "job_123"), "a malformed job id")
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
            "jobStages": list(JOB_STAGES),
            "progressUnits": list(PROGRESS_UNITS),
            "jobStates": list(JOB_STATES),
        },
        "valid": {"SourceAsset": [photo, clip, zoneless_photo], "IngestReceipt": receipts, "SolveJob": jobs},
        "invalid": invalid,
    }


class _Drop:
    pass


_DROP = _Drop()
