"""The sample night, read-only (first-use-sample-one, DECIDED-BY-LEAD 25 Nov 03:22 and 03:43).

`GET /api/samples` gives the tracked record (data/samples.json, its contract services/contracts/samples.py).
`GET /api/samples/{sample_id}/original` gives the public photo from the shared cache, only when its bytes are the
ones the record names (size and SHA-256), and `GET /api/samples/{sample_id}/solution` gives the app's own solve of
it from the cache, only when its SHA-256 matches the one kept beside it. What is not on this machine yet is 404; what
fails its hash is 409 and is never served. A read never fetches and never solves: the setup step
(scripts/make_samples.py) does both, as the record says.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Path as PathParam
from fastapi.responses import FileResponse

from services.contracts.samples import SAMPLE_ID_PATTERN, SampleNight, load_samples

router = APIRouter()
REPO_ROOT = Path(__file__).resolve().parents[2]
NO_SAMPLE = "no sample night has that id"
NO_SAMPLE_PHOTO = "the sample's photo is not on this machine yet"
NO_SAMPLE_SOLUTION = "the sample's solve is not on this machine yet"
UNEXPECTED_SAMPLE_FILE = "the sample's file on this machine is not the one its record names"


def get_samples_root() -> Path:
    """The folder the record's paths start from: the repository (tests stand a temporary one in)."""
    return REPO_ROOT


def _sample_id() -> Any:
    return PathParam(pattern=SAMPLE_ID_PATTERN, description="A sample night's id, night-<name>")


def _night(root: Path, sample_id: str) -> SampleNight:
    night = next((s for s in load_samples(root / "data" / "samples.json") if s.id == sample_id), None)
    if night is None:
        raise HTTPException(status_code=404, detail=NO_SAMPLE)
    return night


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@router.get("/api/samples")
def get_samples(root: Path = Depends(get_samples_root)) -> dict[str, Any]:
    """The tracked record of the sample nights."""
    return json.loads((root / "data" / "samples.json").read_text(encoding="utf-8"))


@router.get("/api/samples/{sample_id}/original", response_class=FileResponse)
def get_sample_original(sample_id: str = _sample_id(), root: Path = Depends(get_samples_root)) -> FileResponse:
    """The sample's public photo, exactly as its recorded source served it."""
    night = _night(root, sample_id)
    path = root / night.photo.download_path
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail=NO_SAMPLE_PHOTO)
    if path.stat().st_size != night.photo.bytes or _sha256(path) != night.photo.sha256:
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE)
    return FileResponse(path, media_type=night.photo.mime_type, headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@router.get("/api/samples/{sample_id}/solution")
def get_sample_solution(sample_id: str = _sample_id(), root: Path = Depends(get_samples_root)) -> Any:
    """The app's own solve of the sample, made on this machine, whole."""
    night = _night(root, sample_id)
    path = root / night.solution.path
    kept = root / night.solution.sha256_path
    if not path.is_file() or path.is_symlink() or not kept.is_file():
        raise HTTPException(status_code=404, detail=NO_SAMPLE_SOLUTION)
    if _sha256(path) != kept.read_text(encoding="utf-8").strip():
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE)
    return json.loads(path.read_text(encoding="utf-8"))
