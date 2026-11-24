"""Media routes: discovery of uploaded media, and restoration jobs and their variants."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from services.media.discovery import discover_uploaded_media_report

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


# =============================================================================================
# Restoration jobs and their variants (spec restoration-for-every-upload, design sections 9 and 11; restore-2b)
# =============================================================================================

import hashlib  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402
import zlib  # noqa: E402

from fastapi import Body  # noqa: E402
from fastapi import Path as PathParameter  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from services.contracts import media as media_contract  # noqa: E402
from services.ingest.job_store import JobStore  # noqa: E402
from services.ingest.router import get_ingest_store  # noqa: E402
from services.ingest.runner import NoUpload, runner_for  # noqa: E402
from services.ingest.store import IngestStore  # noqa: E402
from services.media import variants  # noqa: E402

SHA256_PATTERN = media_contract.MEDIA_PATTERNS["sha256"]
VARIANT_PATTERN = media_contract.MEDIA_ID_PATTERNS["variant"]
NO_UPLOAD = "no file with that hash is stored"
NO_VARIANT = "no variant with that id is stored for that file"


def _variant_folder(store: IngestStore, sha256: str, variant_id: str) -> Path:
    if variant_id not in JobStore(store).succeeded_variant_ids(sha256):
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    folder = store.folder(sha256) / "variants" / variant_id
    try:
        if not stat.S_ISDIR(os.lstat(folder).st_mode):
            raise HTTPException(status_code=404, detail=NO_VARIANT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
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
    succeeded = JobStore(store).succeeded_variant_ids(sha256)
    folder = store.folder(sha256)
    stored = [
        _read(path)
        for path in sorted((folder / "variants").glob("var_*/variant.json"))
        if path.parent.name in succeeded and not path.parent.is_symlink() and not path.is_symlink()
    ]
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
        folder / variants.variant_file_name(variant["mimeType"]),
        media_type=variant["mimeType"],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


# =============================================================================================
# The export and its manifest (design section 12; REQ-RST-9; review row D22; restore-8)
# =============================================================================================

# Built on request from the stored records and the solution id named at that moment, so never kept by a cache
EXPORT_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
VARIANT_RECORDS = ("variant.json", "graph.json", "attempt.json")
RECORDS_REFUSED = "the stored records do not make a manifest the contract takes"
FILE_CHANGED = "the stored file does not match its record"
READ_BLOCK = 1 << 20


def _export_records(store: IngestStore, sha256: str, variant_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """The variant's folder, its manifest and its variant record. 404 when no succeeded job names it, when the folder
    is missing, is a link, or lacks a record, and when its attempt is not `ready`: a rejected or failed attempt has
    nothing to export (REQ-JOB-5.4, REQ-RST-9.6)."""
    jobs = JobStore(store)
    if variant_id not in jobs.succeeded_variant_ids(sha256):
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    folder = store.folder(sha256) / "variants" / variant_id
    try:
        if not stat.S_ISDIR(os.lstat(folder).st_mode):
            raise HTTPException(status_code=404, detail=NO_VARIANT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
    try:
        variant, graph, attempt = (json.loads(_read_unlinked(folder / name)) for name in VARIANT_RECORDS)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
    if attempt.get("status") != "ready" or variant.get("id") != variant_id:
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    # Every type the contract serves has an export row; a record naming another is not a record the manifest takes
    if variant.get("mimeType") not in variants.EXPORT_EXTENSIONS:
        raise HTTPException(status_code=500, detail=RECORDS_REFUSED)
    try:
        manifest = variants.export_manifest(variant, graph, attempt, jobs.newest_solution_id(sha256))
    except ValidationError:
        raise HTTPException(status_code=500, detail=RECORDS_REFUSED) from None
    return folder, manifest, variant


def _open_unlinked(path: Path) -> int:
    """A descriptor of a regular file, never opened through a link. FileNotFoundError for a link or anything else."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise FileNotFoundError(path.name) from error
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise FileNotFoundError(path.name)
    return fd


def _read_unlinked(path: Path) -> bytes:
    with os.fdopen(_open_unlinked(path), "rb") as handle:
        return handle.read()


def _export_name(variant: dict[str, Any]) -> str:
    return f"{variant['id']}.{variants.EXPORT_EXTENSIONS[variant['mimeType']]}"


@router.get("/api/media/{sha256}/variants/{variant_id}/export", response_class=StreamingResponse)
def get_variant_export(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> StreamingResponse:
    """A ready variant to take away (REQ-RST-9.1, review row D22): one zip stored without compression, an attachment
    named `<variantId>.zip`, holding `<variantId>.png`, the stored file byte for byte, and `<variantId>.png.json`, its
    manifest. The file is read once to check it hashes to its record before a byte is sent, and again as it is sent."""
    folder, manifest, variant = _export_records(store, sha256, variant_id)
    name = _export_name(variant)
    try:
        # The variant's own file: a still's PNG, or a clip's `.mov` or `.mp4` as restore-7 stored it, exported as
        # `<variantId>.mov` or `.mp4` beside its `.mov.json` or `.mp4.json` (EXPORT_EXTENSIONS)
        fd = _open_unlinked(folder / variants.variant_file_name(variant["mimeType"]))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
    handle = os.fdopen(fd, "rb")
    digest, crc, size = hashlib.sha256(), 0, 0
    for block in iter(lambda: handle.read(READ_BLOCK), b""):
        digest.update(block)
        crc, size = zlib.crc32(block, crc), size + len(block)
    if digest.hexdigest() != variant["sha256"]:
        handle.close()
        raise HTTPException(status_code=500, detail=FILE_CHANGED)
    sidecar = variants.manifest_bytes(manifest)

    def file_blocks():
        handle.seek(0)
        return iter(lambda: handle.read(READ_BLOCK), b"")

    entries = [
        variants.ZipEntry(name, size, crc & 0xFFFFFFFF, file_blocks),
        variants.ZipEntry(f"{name}.json", len(sidecar), zlib.crc32(sidecar) & 0xFFFFFFFF, lambda: iter((sidecar,))),
    ]
    length = variants.stored_zip_size(entries)
    body = variants.stored_zip(entries, variants.zip_date_time(variant["createdAtUTC"]))

    def stream():
        try:
            yield from body
        finally:
            handle.close()

    return StreamingResponse(stream(), media_type="application/zip", headers={
        **EXPORT_HEADERS, "Content-Length": str(length),
        "Content-Disposition": f'attachment; filename="{variant_id}.zip"',
    })


@router.get("/api/media/{sha256}/variants/{variant_id}/manifest")
def get_variant_manifest(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> Response:
    """A ready variant's manifest alone (REQ-RST-9.2), the export's own sidecar, as an attachment named
    `<variantId>.png.json`."""
    _, manifest, variant = _export_records(store, sha256, variant_id)
    return Response(variants.manifest_bytes(manifest), media_type="application/json", headers={
        **EXPORT_HEADERS, "Content-Disposition": f'attachment; filename="{_export_name(variant)}.json"',
    })
