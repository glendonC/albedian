"""Media routes: discovery of uploaded media and astronomy-aware restoration branches."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from services.media.discovery import discover_uploaded_media_report
from services.media.runtime import MediaExperimentRuntime

router = APIRouter()

_media_runtime = MediaExperimentRuntime()


class RestoreRequest(BaseModel):
    """Request payload for `POST /api/media/restore`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    profile: str = Field(default="star_protected")
    preserve_stars: bool = Field(default=True, alias="preserveStars")
    frames: list[list[list[float]]] | None = None
    image_luma: list[list[float]] | None = None
    # The recording the frames come from; the response hashes the frames it received itself
    source_asset_id: str = Field(alias="sourceAssetId", min_length=1)


_NO_INPUT_DETAIL = "Send {inputs}; the API does not substitute synthetic input for a missing observation."


@router.get("/api/media/discover")
def get_media_discover(
    uploads_dir: str = Query(default="data/uploads"),
    public_dir: str = Query(default="apps/web/public"),
    artifacts_dir: str = Query(default="data/artifacts"),
) -> dict[str, Any]:
    """Dynamically discover all uploaded image/video assets in `data/uploads/`,
    probe QuickTime/container metadata via `ffprobe`, compute content SHA-256,
    link rendered variants, and return each item's `ObservationSolution` and ephemerides.
    """
    items, skipped = discover_uploaded_media_report(
        uploads_dir=uploads_dir,
        public_dir=public_dir,
        artifacts_dir=artifacts_dir,
    )
    return {
        "uploadsDir": uploads_dir,
        "count": len(items),
        "items": [item.to_contract_dict() for item in items],
        # Files left out, each with the reason: nothing is listed with a size, rate or duration it does not have
        "skipped": skipped,
    }


@router.post("/api/media/restore")
def post_media_restore(req: RestoreRequest) -> dict[str, Any]:
    """Execute an astronomy-aware media restoration branch and return honest signal metrics."""
    if req.frames is not None:
        frame_arrays = [np.asarray(f, dtype=np.float64) for f in req.frames]
    elif req.image_luma is not None:
        frame_arrays = [np.asarray(req.image_luma, dtype=np.float64)]
    else:
        raise HTTPException(status_code=422, detail=_NO_INPUT_DETAIL.format(inputs="frames or image_luma"))

    try:
        res = _media_runtime.execute_restoration_branch(
            frames=frame_arrays,
            profile=req.profile,
            preserve_stars=req.preserve_stars,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    contract_dict = res.to_contract_dict(source_asset_id=req.source_asset_id)
    contract_dict["profile"] = res.profile
    contract_dict["sourceSha256"] = res.source_sha256
    contract_dict["outputSha256"] = res.output_sha256
    contract_dict["allowSyntheticSky"] = False
    contract_dict["shadow_snr_gain_db"] = res.shadow_snr_gain_db
    contract_dict["point_source_retention_ratio"] = res.point_source_retention_ratio
    contract_dict["false_point_artifact_count"] = res.false_point_artifact_count
    contract_dict["temporal_flicker_index"] = res.temporal_flicker_index
    return contract_dict
