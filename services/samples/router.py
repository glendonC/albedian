"""The sample night, read-only (first-use-sample-one, DECIDED-BY-LEAD 25 Nov 03:22 and 03:43).

`GET /api/samples` gives the tracked record (data/samples.json, its contract services/contracts/samples.py).
`GET /api/samples/{sample_id}/original` gives the public photo from the shared cache, only when its bytes are the
ones the record names (size and SHA-256); `GET /api/samples/{sample_id}/asset` gives that photo as the app reads it
(sample-asset-one: the SourceAsset the ingest's own reader makes of it, a public sample shown from its own route, its
contract SampleAsset), and `GET /api/samples/{sample_id}/solution` gives the app's own solve of
it from the cache, only when its SHA-256 matches the one kept beside it and the setup's own rule holds it kept
(sample-solution-producer-one: made by this revision of the solve chain, taken by the contract, named for this photo).
What is not on this machine yet, or not kept by that rule, is 404; what fails its hash is 409 and is never served. A read never fetches and never solves: the setup step
(scripts/make_samples.py) does both, as the record says.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Path as PathParam
from fastapi.responses import FileResponse

from services.contracts.samples import SAMPLE_ID_PATTERN, SampleAsset, SampleNight, load_samples, sample_asset_matches
from services.ingest.router import read_stored_asset
from services.ingest.store import SNIFF_BYTES, Received, sniff
from services.samples.make import SampleAltered, kept_solve

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


def sample_of_photo(root: Path, sha256: str) -> Optional[SampleNight]:
    """The sample night whose photo has these bytes, or None (sample-voice-one: the voice answer route asks it)."""
    return next((s for s in load_samples(root / "data" / "samples.json") if s.photo.sha256 == sha256), None)


def kept_solution(root: Path, night: SampleNight) -> Optional[dict[str, Any]]:
    """The app's own solve of the sample kept on this machine, by the setup's own rule (`kept_solve`: whole by its
    hash, made by this revision of the solve chain, taken by the contract, named for this photo): None when it is not
    so, 409 when its bytes are not the ones its hash names."""
    try:
        return kept_solve(night, root)
    except SampleAltered:
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE) from None


@router.get("/api/samples")
def get_samples(root: Path = Depends(get_samples_root)) -> dict[str, Any]:
    """The tracked record of the sample nights."""
    return json.loads((root / "data" / "samples.json").read_text(encoding="utf-8"))


def _recorded_photo(root: Path, night: SampleNight) -> Path:
    """The sample's photo on this machine when its bytes are the ones the record names: 404 when it is not here (or
    is a link to elsewhere), 409 when it fails its size or hash."""
    path = root / night.photo.download_path
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail=NO_SAMPLE_PHOTO)
    if path.stat().st_size != night.photo.bytes or _sha256(path) != night.photo.sha256:
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE)
    return path


@router.get("/api/samples/{sample_id}/original", response_class=FileResponse)
def get_sample_original(sample_id: str = _sample_id(), root: Path = Depends(get_samples_root)) -> FileResponse:
    """The sample's public photo, exactly as its recorded source served it."""
    night = _night(root, sample_id)
    path = _recorded_photo(root, night)
    return FileResponse(path, media_type=night.photo.mime_type, headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})


@router.get("/api/samples/{sample_id}/asset")
def get_sample_asset(sample_id: str = _sample_id(), root: Path = Depends(get_samples_root)) -> dict[str, Any]:
    """The sample's photo as the app reads it (sample-asset-one): what `POST /api/ingest` would read of those bytes,
    but a public sample, shown from the sample's own route and titled as its record titles it; the web builds the
    sample's reveal from it and the kept solve, as it builds an upload's from its receipt."""
    night = _night(root, sample_id)
    path = _recorded_photo(root, night)
    with path.open("rb") as handle:
        head = handle.read(SNIFF_BYTES)
    received = Received(partial=path, sha256=night.photo.sha256, byte_size=night.photo.bytes, file_type=sniff(head))
    asset = {**read_stored_asset(received, night.title), "privacyClass": "public_sample", "uri": f"/api/samples/{night.id}/original"}
    # A record that names the right bytes under another type is not the photo it names
    if not sample_asset_matches(night, SampleAsset.model_validate(asset)):
        raise HTTPException(status_code=409, detail=UNEXPECTED_SAMPLE_FILE)
    return asset


@router.get("/api/samples/{sample_id}/solution")
def get_sample_solution(sample_id: str = _sample_id(), root: Path = Depends(get_samples_root)) -> Any:
    """The app's own solve of the sample, made on this machine, whole."""
    solution = kept_solution(root, _night(root, sample_id))
    if solution is None:
        raise HTTPException(status_code=404, detail=NO_SAMPLE_SOLUTION)
    return solution
