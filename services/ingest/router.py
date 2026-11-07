"""Upload routes (roadmap Track 1 M1): store a photo or clip on this machine and read it back.

`POST /api/ingest` takes the file itself as the body, its type as Content-Type and, optionally, its name
in `X-Albedian-Filename` (percent-encoded; used only as the local title). It answers 201 with an
`IngestReceipt` for a new file, 200 with `alreadyStored` for bytes stored before, 413 over the size cap,
415 for an unknown or mismatched type and 422 for a file whose facts cannot be read; a refused upload
leaves nothing behind. `GET /api/ingest/{sha256}` returns the receipt of a stored file and
`GET /api/ingest/{sha256}/original` its bytes. There is no listing: a stored file is reached only by
its full hash.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

from services.contracts.ingest import JOB_ID_PATTERN, IngestReceipt
from services.ingest.jobs import read_solution, read_solve_job, read_solve_job_by_id, run_solve_job
from services.ingest.metadata import HeifError, display_matrix_hint, exif_hints, quicktime_hints, read_heif
from services.ingest.probe import MediaFacts, ProbeError, probe_media
from services.ingest.store import DECLARABLE, IngestStore, Received, Refused

router = APIRouter()

def _sha256_path() -> Any:
    return Path(pattern=r"^[0-9a-f]{64}$", description="The file's SHA-256, 64 lowercase hex characters")


def _job_id_path() -> Any:
    return Path(
        pattern=JOB_ID_PATTERN,
        description="The solve job's id, job_ followed by 16 to 64 lowercase hex characters",
    )


UPLOAD_TYPES = sorted(name for names in DECLARABLE.values() for name in names)


def get_ingest_store() -> IngestStore:
    """The store, configured by ALBEDIAN_INGEST_DIR and ALBEDIAN_INGEST_MAX_BYTES."""
    return IngestStore.from_environment()


def _title(header: Optional[str], sha256: str) -> str:
    """The file's own name, without any folder, as the local title; a short hash when it has none."""
    name = unquote(header or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:255]
    return name or f"Upload {sha256[:12]}"


# What reading an EXIF block can raise: such a block gives no hints, since the file itself was readable
EXIF_FAILURES = (HeifError, OSError, SyntaxError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError)


def _hints(received: Received, facts: MediaFacts) -> dict[str, Any]:
    if received.file_type.media_kind == "video":
        hints = quicktime_hints(facts.format_tags)
        orientation = display_matrix_hint(facts.display_matrix)
        if orientation is not None:
            hints["orientation"] = orientation
        return hints
    if received.file_type.family != "heif":
        try:
            with Image.open(received.partial) as image:
                return exif_hints(image.getexif())
        except EXIF_FAILURES:
            return {}
    try:
        info = read_heif(received.partial.read_bytes())
    except HeifError:
        return {}
    hints = {}
    if info.exif is not None:
        try:
            exif = Image.Exif()
            exif.load(b"Exif\x00\x00" + info.exif)
            hints = exif_hints(exif)
        except EXIF_FAILURES:
            hints = {}
    # A HEIF's irot and imir decide when its primary item has either; its EXIF Orientation counts only when it has
    # neither. libavif's include/avif/avif.h and libheif's heif_properties.h say nothing of a HEIF's EXIF Orientation.
    # PhotoPrism's developer guide (docs.photoprism.app/developer-guide/media/heif/) says libheif applies irot and imir
    # when decoding, that the HEIF specification treats EXIF Orientation as informational on top of them, and that
    # some older Apple HEICs store their orientation only in EXIF.
    if info.orientation is not None:
        hints["orientation"] = info.orientation
    return hints


def _asset(received: Received, title: str, facts: MediaFacts, hints: dict[str, Any]) -> dict[str, Any]:
    sha = received.sha256
    asset: dict[str, Any] = {
        "id": f"src_{sha[:16]}",
        "title": title,
        "mediaKind": facts.media_kind,
        "sha256": sha,
        "uri": f"/api/ingest/{sha}/original",
        "byteSize": received.byte_size,
        "mimeType": received.file_type.mime_type,
        "width": facts.width,
        "height": facts.height,
        "durationMs": facts.duration_ms,
        # A still has no frame rate; a clip carries the one ffprobe read
        **({"fpsNumerator": facts.fps_numerator, "fpsDenominator": facts.fps_denominator} if facts.fps_numerator is not None else {}),
        "videoCodec": facts.codec,
        "audioStreams": list(facts.audio_streams),
        "privacyClass": "private",
        "immutable": True,
    }
    sources: dict[str, Any] = {}
    time = hints.get("captureTime")
    # Only a recorded time is the asset's capture time; a file's own time stays a hint
    if time is not None and time["marks"] == "recorded" and time["utc"] is not None:
        asset["capturedAtUTC"] = time["utc"]
        sources["capturedAtUTC"] = time["source"]
    place = hints.get("location")
    if place is not None:
        location: dict[str, Any] = {"latDeg": place["latDeg"], "lonDeg": place["lonDeg"]}
        if "altitudeM" in place:
            location["elevationM"] = place["altitudeM"]
        if "horizontalAccuracyM" in place:
            location["uncertaintyMeters"] = place["horizontalAccuracyM"]
        asset["observerLocation"] = location
        sources["observerLocation"] = place["source"]
    camera = hints.get("camera")
    if camera is not None:
        asset["cameraMetadata"] = {key: value for key, value in camera.items() if key != "source"}
        sources["cameraMetadata"] = camera["source"]
    # How to show the stored pixels; the pixels, detections and any WCS stay in stored coordinates
    orientation = hints.get("orientation")
    if orientation is not None:
        asset["displayOrientation"] = {key: orientation[key] for key in ("quarterTurnsClockwise", "mirrored")}
        sources["displayOrientation"] = orientation["source"]
    if sources:
        asset["metadataSources"] = sources
    return asset


def _receipt(record: dict[str, Any], already_stored: bool) -> dict[str, Any]:
    receipt = {**record, "alreadyStored": already_stored}
    IngestReceipt.model_validate(receipt)
    return receipt


def _content_length(header: Optional[str]) -> Optional[int]:
    try:
        return int(header) if header is not None else None
    except ValueError:
        return None


@router.post(
    "/api/ingest",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The file itself, with its type as Content-Type",
            "content": {name: {"schema": {"type": "string", "format": "binary"}} for name in UPLOAD_TYPES},
        },
        "parameters": [{
            "name": "X-Albedian-Filename",
            "in": "header",
            "required": False,
            "description": "The file's name, percent-encoded; used only as the local title",
            "schema": {"type": "string"},
        }],
    },
)
async def ingest_upload(request: Request, store: IngestStore = Depends(get_ingest_store)) -> JSONResponse:
    """Store an upload on this machine and read its facts and hints; refused uploads leave nothing behind."""
    try:
        received = await store.receive(
            request.stream(), request.headers.get("content-type"), _content_length(request.headers.get("content-length"))
        )
    except Refused as refused:
        return JSONResponse({"detail": refused.reason}, status_code=refused.status)
    try:
        record = store.read_record(received.sha256)
        if record is not None:
            store.discard(received)
            job = read_solve_job(store, received.sha256)
            if job is not None:
                record = {**record, "solveJobId": job["id"]}
            return JSONResponse(_receipt(record, True), status_code=200)
        facts = await run_in_threadpool(probe_media, received.partial, received.file_type)
        hints = await run_in_threadpool(_hints, received, facts)
        title = _title(request.headers.get("x-albedian-filename"), received.sha256)
        record = {"sourceAsset": _asset(received, title, facts, hints), "hints": hints}
        _receipt(record, False)
    except ProbeError as exc:
        store.discard(received)
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except BaseException:
        store.discard(received)
        raise
    _, already_stored = store.commit(received)
    record = store.write_record(received.sha256, record)
    return JSONResponse(_receipt(record, already_stored), status_code=200 if already_stored else 201)


@router.get("/api/ingest/{sha256}")
def get_ingest_receipt(sha256: str = _sha256_path(), store: IngestStore = Depends(get_ingest_store)) -> dict[str, Any]:
    """The receipt of a stored file."""
    record = store.read_record(sha256)
    if record is None or store.original(sha256) is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    job = read_solve_job(store, sha256)
    if job is not None:
        record = {**record, "solveJobId": job["id"]}
    return _receipt(record, True)


@router.get("/api/ingest/{sha256}/original", response_class=FileResponse)
def get_ingest_original(sha256: str = _sha256_path(), store: IngestStore = Depends(get_ingest_store)) -> FileResponse:
    """The stored bytes, exactly as uploaded."""
    record = store.read_record(sha256)
    path = store.original(sha256)
    if record is None or path is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    return FileResponse(
        path,
        media_type=record["sourceAsset"]["mimeType"],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.post("/api/ingest/{sha256}/solve", status_code=201)
async def start_ingest_solve_job(
    sha256: str = _sha256_path(), store: IngestStore = Depends(get_ingest_store)
) -> JSONResponse:
    """Run or return the solve job for a stored upload, reporting real byte, frame and candidate denominators."""
    if store.read_record(sha256) is None or store.original(sha256) is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    existing = read_solve_job(store, sha256)
    if existing is not None and existing.get("state") == "succeeded" and read_solution(store, sha256) is not None:
        return JSONResponse(existing, status_code=200)
    job = await run_in_threadpool(run_solve_job, store, sha256)
    return JSONResponse(job, status_code=201)


@router.get("/api/ingest/jobs/{job_id}")
def get_ingest_solve_job(
    job_id: str = _job_id_path(), store: IngestStore = Depends(get_ingest_store)
  ) -> dict[str, Any]:
    """The state and stage progress of a solve job."""
    job = read_solve_job_by_id(store, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no solve job with that id is stored")
    return job


@router.get("/api/ingest/{sha256}/solution")
def get_ingest_solution(
    sha256: str = _sha256_path(), store: IngestStore = Depends(get_ingest_store)
) -> dict[str, Any]:
    """The contract ObservationSolution produced for a stored upload."""
    if store.read_record(sha256) is None or store.original(sha256) is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    solution = read_solution(store, sha256)
    if solution is None:
        raise HTTPException(status_code=404, detail="no solution is stored for that file")
    return solution
