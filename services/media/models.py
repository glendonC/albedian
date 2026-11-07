"""Domain models and content-addressing utilities for the Albedian Media Experiment Runtime."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from services.science.solver import SourceDetection


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RestorationMetrics:
    """Honest signal-level evaluation metrics for a media restoration attempt."""

    evaluation_method: str
    has_paired_reference: bool
    shadow_snr_gain_db: float
    estimated_noise_reduction_pct: float
    point_source_retention_ratio: float | None
    false_point_artifact_count: int
    temporal_flicker_index: float | None
    audio_sync_offset_ms: float | None = None
    face_region_stability_score: float | None = None
    validity_notes: list[str] = field(default_factory=list)

    def to_contract_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evaluationMethod": self.evaluation_method,
            "hasPairedReference": bool(self.has_paired_reference),
            "shadowSnrGainDb": float(self.shadow_snr_gain_db),
            "estimatedNoiseReductionPct": float(
                max(0.0, min(100.0, self.estimated_noise_reduction_pct))
            ),
            "falsePointArtifactCount": int(max(0, self.false_point_artifact_count)),
            "validityNotes": list(self.validity_notes),
        }
        if self.point_source_retention_ratio is not None:
            payload["pointSourceRetentionRatio"] = float(
                max(0.0, min(1.5, self.point_source_retention_ratio))
            )
        if self.face_region_stability_score is not None:
            payload["faceRegionStabilityScore"] = float(
                max(0.0, min(1.0, self.face_region_stability_score))
            )
        if self.temporal_flicker_index is not None:
            payload["temporalFlickerIndex"] = float(max(0.0, self.temporal_flicker_index))
        if self.audio_sync_offset_ms is not None:
            payload["audioSyncOffsetMs"] = float(self.audio_sync_offset_ms)
        return payload


@dataclass(frozen=True)
class RestorationBranchResult:
    """Output of a deterministic restoration branch execution."""

    attempt_id: str
    profile: str
    source_sha256: str
    output_sha256: str
    cache_key: str
    restored_frames: list[np.ndarray]
    source_detections: list[SourceDetection]
    restored_detections: list[SourceDetection]
    metrics: RestorationMetrics
    elapsed_ms: float
    cpu_ms: float = 0.0
    started_at_utc: str = field(default_factory=_utc_now_iso)
    completed_at_utc: str = field(default_factory=_utc_now_iso)
    allow_synthetic_sky: bool = False

    @property
    def primary_frame(self) -> np.ndarray:
        return self.restored_frames[0]

    @property
    def shadow_snr_gain_db(self) -> float:
        return self.metrics.shadow_snr_gain_db

    @property
    def point_source_retention_ratio(self) -> float | None:
        return self.metrics.point_source_retention_ratio

    @property
    def false_point_artifact_count(self) -> int:
        return self.metrics.false_point_artifact_count

    @property
    def temporal_flicker_index(self) -> float | None:
        return self.metrics.temporal_flicker_index

    def to_contract_dict(self, *, source_asset_id: str) -> dict[str, Any]:
        """The ProcessingAttempt record, for the recording the caller names: the runtime cannot know it."""
        return {
            "id": self.attempt_id,
            "sourceAssetId": source_asset_id,
            "graphId": f"graph-{self.profile}",
            "label": f"Restoration ({self.profile})",
            "status": "ready",
            "cacheKey": self.cache_key,
            "startedAtUTC": self.started_at_utc,
            "completedAtUTC": self.completed_at_utc,
            "elapsedMs": float(self.elapsed_ms),
            "cpuMs": float(self.cpu_ms),
            # No GPU execution path exists in the Python restoration runtime; all array work runs on the CPU.
            "gpuMs": 0.0,
            "outputVariantId": f"variant-{self.attempt_id}",
            "metrics": self.metrics.to_contract_dict(),
        }


@dataclass(frozen=True)
class ExportRemuxResult:
    """Verified output & provenance manifest from `export_video_with_audio_remux` (`EXP-10`)."""

    id: str
    output_path: str
    output_sha256: str
    source_sha256: str
    video_codec: str
    audio_preserved: bool
    audio_codec: str | None
    video_duration_sec: float | None
    audio_duration_sec: float | None
    duration_drift_ms: float | None
    audio_sync_offset_ms: float | None
    stream_types: list[str]
    frame_count: int
    fps: int
    exported_at_utc: str = field(default_factory=_utc_now_iso)

    def to_contract_dict(
        self,
        *,
        session_id: str,
        source_asset_id: str,
        variant_id: str,
        graph_id: str,
        catalog_citations: Sequence[str],
        epistemic_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The ExportArtifact record. The session, asset, variant and graph come from the caller, which knows
        them, and so do the citations of the catalog content the export shows: none for a remux of restored
        frames."""
        summary = (
            dict(epistemic_summary)
            if epistemic_summary is not None
            else {
                "observedCount": 0,
                "catalogMatchedCount": 0,
                "predictedCount": 0,
                "simulatedViewsIncluded": False,
                "strictFidelityEnforced": True,
            }
        )
        payload: dict[str, Any] = {
            "id": self.id,
            "sessionId": session_id,
            "sourceAssetId": source_asset_id,
            "sourceSha256": self.source_sha256,
            "exportedVariantId": variant_id,
            # By content, never by local path: a path names the account folder (the owner's name) and the folders
            # above the file. RFC 6920's named-information URI of the SHA-256 measured from the written file
            "outputUri": "ni:///sha-256;"
            + base64.urlsafe_b64encode(bytes.fromhex(self.output_sha256)).decode("ascii").rstrip("="),
            "outputSha256": self.output_sha256,
            "containerFormat": "mp4",
            "videoCodec": self.video_codec,
            "audioPreserved": bool(self.audio_preserved),
            "audioCodec": self.audio_codec,
            "processingGraphId": graph_id,
            "catalogCitations": list(catalog_citations),
            "epistemicSummary": summary,
            "exportedAtUTC": self.exported_at_utc,
        }
        if self.audio_sync_offset_ms is not None:
            payload["audioSyncOffsetMs"] = float(self.audio_sync_offset_ms)
        return payload


def compute_frames_sha256(frames: Sequence[np.ndarray]) -> str:
    """Compute a deterministic content-addressed SHA-256 digest over a sequence of frames."""
    hasher = hashlib.sha256()
    hasher.update(f"count={len(frames)}".encode("utf-8"))
    for idx, frame in enumerate(frames):
        arr = np.ascontiguousarray(np.round(np.asarray(frame, dtype=np.float64), 4))
        hasher.update(f"frame={idx}:shape={arr.shape}:".encode("utf-8"))
        hasher.update(arr.tobytes())
    return hasher.hexdigest()


def compute_file_sha256(file_path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file on disk without mutating it."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
