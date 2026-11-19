"""Media routes: discovery of uploaded media and astronomy-aware restoration branches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from services.media.discovery import discover_uploaded_media_report
from services.media.runtime import MediaExperimentRuntime

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MediaFolders:
    """The folders discover scans: the uploads, the web app's public folder and the experiment artifacts."""

    uploads: Path
    public: Path
    artifacts: Path


# Resolved from the repository, so the API scans the same folders whatever its working directory
DEFAULT_MEDIA_FOLDERS = MediaFolders(
    uploads=REPO_ROOT / "data" / "uploads",
    public=REPO_ROOT / "apps" / "web" / "public",
    artifacts=REPO_ROOT / "data" / "artifacts",
)

router = APIRouter()


def media_folders(request: Request) -> MediaFolders:
    """The folders the app was built with (services/api.py create_app); never folders a request names."""
    return request.app.state.media_folders


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
def get_media_discover(folders: MediaFolders = Depends(media_folders)) -> dict[str, Any]:
    """Discover the image and video files in the app's uploads folder: probe their container metadata with
    `ffprobe`, hash their content, link rendered variants, and return each item's `ObservationSolution` and
    ephemerides. Every path in the answer is relative to its folder.
    """
    items, skipped = discover_uploaded_media_report(
        uploads_dir=folders.uploads,
        public_dir=folders.public,
        artifacts_dir=folders.artifacts,
    )
    return {
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
    if res.point_source_retention_ratio is not None:
        contract_dict["point_source_retention_ratio"] = res.point_source_retention_ratio
    contract_dict["false_point_artifact_count"] = res.false_point_artifact_count
    if res.temporal_flicker_index is not None:
        contract_dict["temporal_flicker_index"] = res.temporal_flicker_index
    return contract_dict


# =============================================================================================
# Restoration jobs and their variants (spec restoration-for-every-upload, design sections 9 and 11; restore-2b)
# =============================================================================================

from fastapi import Body  # noqa: E402
from fastapi import Path as PathParameter  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from services.contracts import media as media_contract  # noqa: E402
from services.ingest.router import get_ingest_store  # noqa: E402
from services.ingest.runner import NoUpload, runner_for  # noqa: E402
from services.ingest.store import IngestStore  # noqa: E402

SHA256_PATTERN = media_contract.MEDIA_PATTERNS["sha256"]
VARIANT_PATTERN = media_contract.MEDIA_ID_PATTERNS["variant"]
NO_UPLOAD = "no file with that hash is stored"
NO_VARIANT = "no variant with that id is stored for that file"
# A clip's restoration is restore-7's; until it lands no job is made for one [decided 2026-11-11: restore-2b answers
# plainly that it is not built]
CLIP_NOT_BUILT = "Restoring a clip is not built yet."


def _variant_folder(store: IngestStore, sha256: str, variant_id: str) -> Path:
    folder = store.folder(sha256) / "variants" / variant_id
    if not (folder / "variant.json").is_file():
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    return folder


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/api/media/{sha256}/restore")
def start_restoration_job(
    body: dict[str, Any] = Body(...),
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> JSONResponse:
    """Start, join or return a display copy or restoration job of a stored upload: 202 with a new job, 200 with the
    active job it joined or the current succeeded one; poll it at GET /api/ingest/jobs/{job_id}."""
    try:
        media_contract.RestoreRequest.model_validate(body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="the request is not a restore request the contract takes") from None
    record = store.read_record(sha256)
    if record is None:
        raise HTTPException(status_code=404, detail=NO_UPLOAD)
    if body["kind"] == "restoration" and record["sourceAsset"].get("mediaKind") != "image":
        raise HTTPException(status_code=501, detail=CLIP_NOT_BUILT)
    try:
        job, created = runner_for(store).start(sha256, body["kind"], body)
    except NoUpload:
        raise HTTPException(status_code=404, detail=NO_UPLOAD) from None
    return JSONResponse(job, status_code=202 if created else 200)


@router.get("/api/media/{sha256}/variants")
def list_variants(
    sha256: str = PathParameter(pattern=SHA256_PATTERN), store: IngestStore = Depends(get_ingest_store)
) -> dict[str, Any]:
    """An upload's stored variants and every finished attempt, oldest first; there is no listing across uploads."""
    if store.read_record(sha256) is None:
        raise HTTPException(status_code=404, detail=NO_UPLOAD)
    folder = store.folder(sha256)
    stored = [_read(path) for path in sorted((folder / "variants").glob("var_*/variant.json"))]
    attempts = [_read(path) for path in sorted((folder / "attempts").glob("att_*.json"))]
    stored.sort(key=lambda variant: (variant["createdAtUTC"], variant["id"]))
    attempts.sort(key=lambda attempt: (attempt["finishedAtUTC"], attempt["id"]))
    return {"variants": stored, "attempts": attempts}


@router.get("/api/media/{sha256}/variants/{variant_id}")
def get_variant(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> dict[str, Any]:
    """A stored variant's three records: the variant, the graph that made it and the attempt that passed its gate."""
    folder = _variant_folder(store, sha256, variant_id)
    return {"variant": _read(folder / "variant.json"), "graph": _read(folder / "graph.json"),
            "attempt": _read(folder / "attempt.json")}


@router.get("/api/media/{sha256}/variants/{variant_id}/file", response_class=FileResponse)
def get_variant_file(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> FileResponse:
    """The stored variant's bytes, served as the original is: cached privately, never sniffed, shown inline."""
    folder = _variant_folder(store, sha256, variant_id)
    variant = _read(folder / "variant.json")
    return FileResponse(
        folder / "variant.png",
        media_type=variant["mimeType"],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
