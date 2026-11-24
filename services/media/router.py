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

from collections.abc import Iterable  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402
from typing import BinaryIO, Optional  # noqa: E402
import zlib  # noqa: E402

import anyio  # noqa: E402
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
RECORD_UNREADABLE = "a stored record of that variant cannot be read"
VARIANT_RECORDS = ("variant.json", "graph.json", "attempt.json")

# Below the store's root these routes never go through a link, as the job store never does (AUDIT backend-security-b77,
# item L1): every folder on the way is checked with `lstat`, every record and file is opened with `O_NOFOLLOW` and must
# be a regular file, and a record must hold a JSON object naming its own folder or file.


class _Unreadable(ValueError):
    """A stored record that is a regular file but holds no JSON object in UTF-8."""


def _entered(store: IngestStore, sha256: str, *parts: str) -> Optional[Path]:
    """The folder `parts` below the upload's folder, every folder on the way from the store's root a folder and no
    link (`lstat`); None when one is missing or anything else."""
    folder = store.root
    for part in (*store.folder(sha256).relative_to(store.root).parts, *parts):
        folder = folder / part
        try:
            if not stat.S_ISDIR(os.lstat(folder).st_mode):
                return None
        except OSError:
            return None
    return folder


def _record(path: Path) -> dict[str, Any]:
    """A stored record: a regular file opened without following a link (FileNotFoundError for anything else) that
    holds a JSON object in UTF-8 (_Unreadable otherwise)."""
    data = _read_unlinked(path)
    try:
        record = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _Unreadable(path.name) from None
    if not isinstance(record, dict):
        raise _Unreadable(path.name)
    return record


def _variant_records(jobs: JobStore, store: IngestStore, sha256: str, variant_id: str,
                     names: tuple[str, ...]) -> tuple[Path, list[dict[str, Any]]]:
    """The variant's folder and its records `names`, `variant.json` first. 404 when no succeeded job names the variant
    (REQ-JOB-5.4), when a folder on the way to it is missing or a link, when a record is missing, a link or no regular
    file, and when the variant record names another variant; 500 when a record holds no JSON object."""
    if variant_id not in jobs.succeeded_variant_ids(sha256):
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    folder = _entered(store, sha256, "variants", variant_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    try:
        records = [_record(folder / name) for name in names]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
    except _Unreadable:
        raise HTTPException(status_code=500, detail=RECORD_UNREADABLE) from None
    if records[0].get("id") != variant_id:
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    return folder, records


def _listed(paths: Iterable[tuple[str, Path]], field: str) -> list[dict[str, Any]]:
    """The records at `paths` (each with the id its folder or file names), oldest first by their UTC instant `field`.
    A record that is no regular file reached without a link, holds no JSON object, names another id or carries no
    instant in `field` is left out, so one damaged record never hides the rest (the job store's scans leave an
    unreadable record out the same way)."""
    kept = []
    for name, path in paths:
        try:
            record = _record(path)
            if record.get("id") == name:
                kept.append((_utc_sort_key(record, field), record))
        except (FileNotFoundError, _Unreadable, KeyError, TypeError, ValueError):
            continue
    return [record for _, record in sorted(kept, key=lambda pair: pair[0])]


class _OpenedFileResponse(FileResponse):
    """A FileResponse, ranges and all, that sends the file the route opened and checked and never its path again: its
    stat is the descriptor's, every read goes through the descriptor, and the server is never handed the path to send
    itself (the ASGI pathsend extension). The descriptor is closed when the answer ends, whether it was sent or not."""

    def __init__(self, path: Path, handle: BinaryIO, **kwargs: Any) -> None:
        self._handle = handle
        super().__init__(path, stat_result=os.fstat(handle.fileno()), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        extensions = {key: value for key, value in (scope.get("extensions") or {}).items()
                      if key != "http.response.pathsend"}
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            self._handle.close()

    @asynccontextmanager
    async def _open_file(self):
        yield anyio.wrap_file(self._handle)


def _utc_sort_key(record: dict[str, Any], field: str) -> tuple[datetime, str]:
    parsed = datetime.fromisoformat(record[field])
    instant = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return instant, record["id"]


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
    # Only what is reached without a link and reads as a record of its own is listed (_listed). Kept out of the
    # docstring, which is the route's published description (tests/fixtures/api_surface.json)
    if store.read_record(sha256) is None:
        raise HTTPException(status_code=404, detail=NO_UPLOAD)
    named = []
    for vid in sorted(JobStore(store).succeeded_variant_ids(sha256)):
        folder = _entered(store, sha256, "variants", vid)
        if folder is not None:
            named.append((vid, folder / "variant.json"))
    stored = _listed(named, "createdAtUTC")
    folder = _entered(store, sha256, "attempts")
    attempts = [] if folder is None else _listed(
        ((path.stem, path) for path in sorted(folder.glob("att_*.json"))), "finishedAtUTC")
    return {"variants": stored, "attempts": attempts}


@router.get("/api/media/{sha256}/variants/{variant_id}")
def get_variant(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> dict[str, Any]:
    """A stored variant's three records: the variant, the graph that made it and the attempt that passed its gate."""
    _, (variant, graph, attempt) = _variant_records(JobStore(store), store, sha256, variant_id, VARIANT_RECORDS)
    return {"variant": variant, "graph": graph, "attempt": attempt}


@router.get("/api/media/{sha256}/variants/{variant_id}/file", response_class=FileResponse)
def get_variant_file(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> FileResponse:
    """The stored variant's bytes, served as the original is: cached privately, never sniffed, shown inline."""
    # The file is opened once, without following a link, and that descriptor is what is sent (_OpenedFileResponse)
    folder, (variant,) = _variant_records(JobStore(store), store, sha256, variant_id, ("variant.json",))
    mime = variant.get("mimeType")
    # A record naming no type the store keeps a file for names no file: it is not a variant record the route can read
    if not isinstance(mime, str) or mime not in variants.VARIANT_FILES:
        raise HTTPException(status_code=500, detail=RECORD_UNREADABLE)
    path = folder / variants.variant_file_name(mime)
    try:
        fd = _open_unlinked(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=NO_VARIANT) from None
    return _OpenedFileResponse(
        path,
        os.fdopen(fd, "rb"),
        media_type=mime,
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
RECORDS_REFUSED = "the stored records do not make a manifest the contract takes"
FILE_CHANGED = "the stored file does not match its record"
READ_BLOCK = 1 << 20


def _export_records(store: IngestStore, sha256: str, variant_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """The variant's folder, its manifest and its variant record. 404 and 500 as _variant_records answers them, and 404
    when its attempt is not `ready`: a rejected or failed attempt has nothing to export (REQ-JOB-5.4, REQ-RST-9.6)."""
    jobs = JobStore(store)
    folder, (variant, graph, attempt) = _variant_records(jobs, store, sha256, variant_id, VARIANT_RECORDS)
    if attempt.get("status") != "ready":
        raise HTTPException(status_code=404, detail=NO_VARIANT)
    # Every type the contract serves has an export row; a record naming another, or naming none (not a string), is not
    # a record the manifest takes
    mime = variant.get("mimeType")
    if not isinstance(mime, str) or mime not in variants.EXPORT_EXTENSIONS:
        raise HTTPException(status_code=500, detail=RECORDS_REFUSED)
    try:
        manifest = variants.export_manifest(variant, graph, attempt, jobs.newest_solution_id(sha256))
    except ValidationError:
        raise HTTPException(status_code=500, detail=RECORDS_REFUSED) from None
    return folder, manifest, variant


def _open_unlinked(path: Path) -> int:
    """A descriptor of a regular file, never opened through a link, and opened without waiting (a FIFO named like a
    record would otherwise hold the request open). FileNotFoundError for a link or anything else."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise FileNotFoundError(path.name) from error
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise FileNotFoundError(path.name)
    return fd


def _read_unlinked(path: Path) -> bytes:
    with os.fdopen(_open_unlinked(path), "rb") as handle:
        return handle.read()


# Gap seven (DECIDED-BY-LEAD 13:09 on 24 Nov, beside D22): a person's download is named in plain words, never by an
# internal id, and carries no time, place or original name; the browser numbers a second download by itself. The
# manifest inside keeps its ids
EXPORT_TITLES = {"image": "Albedian restored photo", "video": "Albedian restored clip"}
# Every entry carries the zip format's first date (DECIDED-BY-LEAD 18:04 on 24 Nov), not the restoration's time, so the
# files unpacked from a download do not tell when the restoration was made; the same stored state still exports the
# same bytes
EXPORT_ENTRY_DATE = (1980, 1, 1, 0, 0, 0)


def _export_title(variant: dict[str, Any]) -> str:
    return EXPORT_TITLES[variant["mimeType"].split("/", 1)[0]]


def _export_name(variant: dict[str, Any]) -> str:
    return f"{_export_title(variant)}.{variants.EXPORT_EXTENSIONS[variant['mimeType']]}"


@router.get("/api/media/{sha256}/variants/{variant_id}/export", response_class=StreamingResponse)
def get_variant_export(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> StreamingResponse:
    """A ready variant to take away (REQ-RST-9.1, review row D22): one zip stored without compression, an attachment
    named `Albedian restored photo.zip`, holding `Albedian restored photo.png`, the stored file byte for byte, and
    `Albedian restored photo.png.json`, its manifest; a clip's is `Albedian restored clip.zip`. The file is read once
    to check it hashes to its record before a byte is sent, and again as it is sent."""
    folder, manifest, variant = _export_records(store, sha256, variant_id)
    name = _export_name(variant)
    try:
        # The variant's own file: a still's PNG, or a clip's `.mov` or `.mp4` as restore-7 stored it, exported as
        # `Albedian restored clip.mov` or `.mp4` beside its `.mov.json` or `.mp4.json` (EXPORT_EXTENSIONS)
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
    body = variants.stored_zip(entries, EXPORT_ENTRY_DATE)

    def stream():
        try:
            yield from body
        finally:
            handle.close()

    return StreamingResponse(stream(), media_type="application/zip", headers={
        **EXPORT_HEADERS, "Content-Length": str(length),
        "Content-Disposition": f'attachment; filename="{_export_title(variant)}.zip"',
    })


@router.get("/api/media/{sha256}/variants/{variant_id}/manifest")
def get_variant_manifest(
    sha256: str = PathParameter(pattern=SHA256_PATTERN),
    variant_id: str = PathParameter(pattern=VARIANT_PATTERN),
    store: IngestStore = Depends(get_ingest_store),
) -> Response:
    """A ready variant's manifest alone (REQ-RST-9.2), the export's own sidecar, as an attachment named
    `Albedian restored photo.png.json` (a clip's `Albedian restored clip.mov.json` or `.mp4.json`)."""
    _, manifest, variant = _export_records(store, sha256, variant_id)
    return Response(variants.manifest_bytes(manifest), media_type="application/json", headers={
        **EXPORT_HEADERS, "Content-Disposition": f'attachment; filename="{_export_name(variant)}.json"',
    })
