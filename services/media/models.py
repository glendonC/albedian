"""Domain models and content-addressing utilities for the Albedian Media Experiment Runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Sequence

import numpy as np

from services.science.solver import SourceDetection


@dataclass(frozen=True)
class RestorationMetrics:
    """Honest signal-level evaluation metrics for a media restoration attempt."""

    evaluation_method: str
    has_paired_reference: bool
    shadow_snr_gain_db: float
    estimated_noise_reduction_pct: float
    point_source_retention_ratio: float
    false_point_artifact_count: int
    temporal_flicker_index: float
    audio_sync_offset_ms: float = 0.0
    face_region_stability_score: float = 1.0
    validity_notes: list[str] = field(default_factory=list)

    def to_contract_dict(self) -> dict:
        return {
            "evaluationMethod": self.evaluation_method,
            "hasPairedReference": bool(self.has_paired_reference),
            "shadowSnrGainDb": float(self.shadow_snr_gain_db),
            "estimatedNoiseReductionPct": float(
                max(0.0, min(100.0, self.estimated_noise_reduction_pct))
            ),
            "pointSourceRetentionRatio": float(
                max(0.0, min(1.5, self.point_source_retention_ratio))
            ),
            "falsePointArtifactCount": int(max(0, self.false_point_artifact_count)),
            "faceRegionStabilityScore": float(
                max(0.0, min(1.0, self.face_region_stability_score))
            ),
            "temporalFlickerIndex": float(max(0.0, self.temporal_flicker_index)),
            "audioSyncOffsetMs": float(max(0.0, self.audio_sync_offset_ms)),
            "validityNotes": list(self.validity_notes),
        }


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
    allow_synthetic_sky: bool = False

    @property
    def primary_frame(self) -> np.ndarray:
        return self.restored_frames[0]

    @property
    def shadow_snr_gain_db(self) -> float:
        return self.metrics.shadow_snr_gain_db

    @property
    def point_source_retention_ratio(self) -> float:
        return self.metrics.point_source_retention_ratio

    @property
    def false_point_artifact_count(self) -> int:
        return self.metrics.false_point_artifact_count

    @property
    def temporal_flicker_index(self) -> float:
        return self.metrics.temporal_flicker_index

    def to_contract_dict(self, source_asset_id: str = "asset-0") -> dict:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "id": self.attempt_id,
            "sourceAssetId": source_asset_id,
            "graphId": f"graph-{self.profile}",
            "label": f"Restoration ({self.profile})",
            "status": "ready",
            "cacheKey": self.cache_key,
            "startedAtUTC": now_iso,
            "completedAtUTC": now_iso,
            "elapsedMs": float(self.elapsed_ms),
            "cpuMs": float(self.elapsed_ms),
            "gpuMs": 0.0,
            "peakMemoryBytes": int(
                sum(f.nbytes for f in self.restored_frames) * 2
            ),
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
    video_duration_sec: float
    audio_duration_sec: float | None
    duration_drift_ms: float
    audio_sync_offset_ms: float
    stream_types: list[str]
    frame_count: int
    fps: int
    strict_fidelity_enforced: bool = True

    def to_contract_dict(
        self,
        session_id: str = "session-0",
        source_asset_id: str = "asset-0",
        variant_id: str = "variant-0",
        graph_id: str = "graph-star-protected",
    ) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "id": self.id,
            "sessionId": session_id,
            "sourceAssetId": source_asset_id,
            "sourceSha256": self.source_sha256,
            "exportedVariantId": variant_id,
            "outputUri": f"file://{Path(self.output_path).resolve()}",
            "outputSha256": self.output_sha256,
            "containerFormat": "mp4",
            "videoCodec": self.video_codec,
            "audioPreserved": bool(self.audio_preserved),
            "audioCodec": self.audio_codec,
            "audioSyncOffsetMs": float(self.audio_sync_offset_ms),
            "processingGraphId": graph_id,
            "catalogCitations": [
                "Gaia Collaboration (2023) Gaia DR3",
                "Bailer-Jones et al. (2021) AJ 161, 147",
            ],
            "epistemicSummary": {
                "observedCount": 0,
                "catalogMatchedCount": 0,
                "predictedCount": 0,
                "simulatedViewsIncluded": False,
                "strictFidelityEnforced": bool(self.strict_fidelity_enforced),
            },
            "exportedAtUTC": now_iso,
        }


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
