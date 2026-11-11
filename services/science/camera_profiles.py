"""Camera profiles: what is known of a camera's field of view, looked up by the exact model string the file carries
(`SourceAsset.cameraMetadata.model`), for the head-worn Moon pose (`services/science/pose_estimate.py`; spec
predicted-sky-for-every-upload, REQ-SKY-3.2).

A profile states the diagonal field of view as a range with its source and a label: [verified] only when read at the
maker's published specification for that exact model, else [open] with the reason. A profile is added only for a
model someone has uploaded. Kept apart from `phone_photo_solver.py`'s `PUBLISHED_CAMERA_FOV_DEG`, which the plate
solver owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

HEAD_WORN = "head_worn"
PROFILE_KINDS = (HEAD_WORN,)
FOV_LABELS = ("[verified]", "[open]")
# The contract's PoseEstimate.fovDiagonalDeg bound (packages/contracts/src/astronomy.ts)
MAX_FOV_DIAGONAL_DEG = 175.0

# The QuickTime model tag (com.apple.quicktime.model) of the owner's head-worn clip
META_RAY_BAN_DISPLAY_MODEL = "Meta Ray-Ban Display Glasses"


@dataclass(frozen=True)
class CameraProfile:
    """A camera's field of view as far as it is known: the diagonal field of view's range in degrees, its label
    ([verified] or [open]), where the range comes from, and notes."""

    model: str
    kind: str
    fov_diagonal_deg_range: tuple[float, float]
    fov_label: str
    fov_source: str
    notes: str

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("A camera profile names the exact model string it is for.")
        if self.kind not in PROFILE_KINDS:
            raise ValueError(f"A camera profile's kind is one of {', '.join(PROFILE_KINDS)} (got {self.kind!r}).")
        low, high = (float(v) for v in self.fov_diagonal_deg_range)
        if not (math.isfinite(low) and math.isfinite(high) and 0.0 < low < high <= MAX_FOV_DIAGONAL_DEG):
            raise ValueError(
                "A camera profile's diagonal field of view is a range of two increasing positive degrees, at most "
                f"{MAX_FOV_DIAGONAL_DEG:g}."
            )
        if self.fov_label not in FOV_LABELS:
            raise ValueError("A camera profile's field of view is labelled [verified] or [open].")
        if not isinstance(self.fov_source, str) or not self.fov_source.strip():
            raise ValueError("A camera profile says where its field of view comes from.")
        object.__setattr__(self, "fov_diagonal_deg_range", (low, high))

    @property
    def fov_diagonal_deg_mid(self) -> float:
        low, high = self.fov_diagonal_deg_range
        return (low + high) / 2.0


_PROFILES = (
    CameraProfile(
        model=META_RAY_BAN_DISPLAY_MODEL,
        kind=HEAD_WORN,
        fov_diagonal_deg_range=(80.0, 110.0),
        fov_label="[open]",
        fov_source=(
            "Meta publishes a 100 degree field of view for the Ray-Ban Meta glasses' 12 MP camera without naming "
            "its axis (meta.com/au/ai-glasses/compare, \"100° field of view, left-oriented\" for Ray-Ban Meta "
            "(Gen 3) Wayfarer, read at source 2026-11-11; a web search's excerpt of ray-ban.com's Ray-Ban Meta "
            "specification page gives the same figure, but that page answered HTTP 403 to a read that day, so it "
            "was not read at source); no figure was found for the Meta Ray-Ban Display model. Its clip frames "
            "(1344 x 1792) are smaller than "
            "the 1440 x 1920 video size retailers list, so a crop may narrow its field of view [inference]. The "
            "range of 80 to 110 degrees across the diagonal is the one the phone benchmark already uses for its "
            "modelled head-worn class (research/benchmarks/phone_photos/cameras.py), so it is [open]: no product "
            "figure stands behind it."
        ),
        notes=(
            "Head-worn camera glasses: no horizon need be in view, and the head moves during a clip. The glasses have "
            "a zoom: a web search's excerpt of Meta's product page for them "
            "(meta.com/au/ai-glasses/meta-ray-ban-display-glasses-and-neural-band) lists a \"12MP camera with 3x "
            "zoom\", but the page gave no readable text to a read on 2026-11-11, so it was not read at source [open]. "
            "The range of 80 to 110 degrees is for no zoom; a zoomed clip sees a narrower field, which the sweep does "
            "not cover [inference]."
        ),
    ),
)
CAMERA_PROFILES: Mapping[str, CameraProfile] = {profile.model: profile for profile in _PROFILES}


def camera_profile(model: object) -> CameraProfile | None:
    """The profile for exactly this model string, or None: no case folding, trimming or near match."""
    if not isinstance(model, str):
        return None
    return CAMERA_PROFILES.get(model)


def head_worn_profile(asset: Mapping[str, Any]) -> CameraProfile | None:
    """The head-worn profile for the asset's camera model (`cameraMetadata.model`), or None."""
    camera = asset.get("cameraMetadata")
    if not isinstance(camera, Mapping):
        return None
    profile = camera_profile(camera.get("model"))
    return profile if profile is not None and profile.kind == HEAD_WORN else None
