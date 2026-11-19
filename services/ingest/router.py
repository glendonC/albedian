"""Upload routes (roadmap Track 1 M1): store a photo or clip on this machine and read it back.

`POST /api/ingest` takes the file itself as the body, its type as Content-Type and, optionally, its name
in `X-Albedian-Filename` (percent-encoded; used only as the local title). It answers 201 with an
`IngestReceipt` for a new file, 200 with `alreadyStored` for bytes stored before, 413 over the size cap,
415 for an unknown or mismatched type and 422 for a file whose facts cannot be read; a refused upload
leaves nothing behind. `GET /api/ingest/{sha256}` returns the receipt of a stored file and
`GET /api/ingest/{sha256}/original` its bytes. There is no listing: a stored file is reached only by
its full hash.

`PUT /api/ingest/{sha256}/pointing` keeps the photo's pointing record (ios-capture-companion design component 4): the
exact body bytes, beside the receipt, once. It answers 201 with a `PointingRecordReceipt` for a record stored now,
200 with `alreadyStored` for the same bytes again, and a `PointingRecordRefusal` otherwise: 404 when no photo with
that hash is stored (an address that is not a SHA-256 names none), 422 for a body that is not JSON text, not a
record by its contract, or a record of another photo, 409 for a different record when one is held and for a first
record once a solve job exists for the photo. A refusal holds
only the contract's closed sentences and the record's own keys, never a value of the record, and nothing here logs a
record. `GET` of the same path returns the stored bytes, once checked against the contract and the address.

The solve is a job of the upload job runner (services/ingest/runner.py): `POST /api/ingest/{sha256}/solve`
starts, joins or answers it (at once with `wait=false`), `GET /api/ingest/jobs/{job_id}` serves any job, live while
this server runs it, and `POST /api/ingest/jobs/{job_id}/cancel` stops one. Job records are read through the job
store (services/ingest/job_store.py), checked against the contract: a stored record that fails it is never served,
and the job route answers 409 with one plain sentence. A receipt names the newest solve attempt it can read. The
solution served is the newest succeeded solve's, read through the store's one solution read and checked by the
solution contract's parser: one it refuses is never served, `GET /api/ingest/{sha256}/solution` answers 409 with one
plain sentence, and a start runs the solve again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import ValidationError

from services.contracts.ingest import JOB_ID_PATTERN, IngestReceipt
from services.contracts.pointing import POINTING_RULES, PointingRecord
from services.contracts.pointing_reply import (
    POINTING_RECEIPT_FORMAT,
    POINTING_RECEIPT_FORMAT_VERSION,
    POINTING_RECORD_KEYS,
    POINTING_REFUSAL_SENTENCES,
    POINTING_REPLY_ISSUES_MAX,
    POINTING_REPLY_SENTENCES,
    PointingRecordReceipt,
    PointingRecordRefusal,
)
from services.ingest.job_store import JobStore, Unreadable
from services.ingest.runner import NoUpload, runner_for
from services.ingest.metadata import HeifError, display_matrix_hint, exif_hints, quicktime_hints, read_heif
from services.ingest.probe import MediaFacts, ProbeError, probe_media
from services.ingest.store import DECLARABLE, IngestStore, Received, Refused
from services.ingest.store_pointing import PointingUnreadable

router = APIRouter()

def _sha256_path() -> Any:
    return Path(pattern=r"^[0-9a-f]{64}$", description="The file's SHA-256, 64 lowercase hex characters")


def _job_id_path() -> Any:
    return Path(
        pattern=JOB_ID_PATTERN,
        description="The solve job's id, job_ followed by 16 to 64 lowercase hex characters",
    )


UPLOAD_TYPES = sorted(name for names in DECLARABLE.values() for name in names)
UNREADABLE_JOB = "The stored record of that job does not meet the contract, so it is not served; run the solve again."
# The one reply for a stored solution that cannot be served (not JSON, not a solution, another upload's, or one the
# contract refuses); answers-4b's route takes this same constant (lead, 2026-11-13). Proposed wording for UIUX work
UNREADABLE_SOLUTION = (
    "The stored solution for that file does not meet the contract, so it is not served; run the solve again."
)


# The pointing route's GET answers beyond the reply contract, worded like the other GET routes [proposed]
NO_POINTING_RECORD = "no pointing record is stored for that file"
UNREADABLE_POINTING = "The stored pointing record for that file does not meet the contract, so it is not served."
SHA256_ADDRESS = re.compile(r"[0-9a-f]{64}")
# Where in the record each of its own rules is broken, for a refusal's issue: the reply contract's table
# (tests/integration/test_pointing_reply_contract.py RULE_PATHS), by the rule's sentence
POINTING_RULE_PATHS = {
    POINTING_RULES[rule]: path
    for rule, path in {
        "headingNeedsPlace": ["heading"],
        "headingInsideLimit": ["heading", "headingAccuracyDeg"],
        "noPlaceReasonNeedsNoPlace": ["heading", "reason"],
        "outsideLimitCarriesAccuracy": ["heading", "reportedHeadingAccuracyDeg"],
        "onlyOutsideLimitReportsAccuracy": ["heading", "reportedHeadingAccuracyDeg"],
        "unitQuaternion": ["heading", "attitude", "quaternion"],
        "gravityNearOneG": ["motion", "gravityDeviceG"],
        "sampleAtThePhoto": ["motion", "sampleOffsetFromPhotoSec"],
        "windowRunsForward": ["motion", "window"],
        "placeholderSha256": ["sourceAssetSha256"],
    }.items()
}
_RECORD_KEYS = frozenset(POINTING_RECORD_KEYS)
_SENTENCES = frozenset(POINTING_REFUSAL_SENTENCES)


def get_ingest_store() -> IngestStore:
    """The store, configured by ALBEDIAN_INGEST_DIR and ALBEDIAN_INGEST_MAX_BYTES."""
    return IngestStore.from_environment()


def _solve_job(store: IngestStore, sha256: str) -> Optional[dict[str, Any]]:
    """The newest solve attempt of an upload, active or ended, checked (a job of the runner's, or today's
    `job.json`); None when there is none or every record fails the contract."""
    return JobStore(store).newest_solve(sha256)


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
            job = _solve_job(store, received.sha256)
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
    job = _solve_job(store, sha256)
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
    sha256: str = _sha256_path(),
    wait: bool = Query(True, description="false: answer at once with the job (202 new, 200 joined or current)"),
    store: IngestStore = Depends(get_ingest_store),
) -> JSONResponse:
    """Start, join or return the solve job of a stored upload, which reports real byte, frame and candidate
    denominators. With `wait=false` it answers at once: 202 with a new job, 200 with the active job it joined or
    the current succeeded one. Without it, it waits for the job to end and answers 201, or 200 with the current
    succeeded job (until the web follows jobs). Leaving early never cancels the job."""
    runner = runner_for(store)
    try:
        job, created = runner.start(sha256, "solve")
    except NoUpload:
        raise HTTPException(status_code=404, detail="no file with that hash is stored") from None
    if not wait:
        return JSONResponse(job, status_code=202 if created else 200)
    if not created and job["state"] not in ("queued", "running"):
        return JSONResponse(job, status_code=200)
    ended = await run_in_threadpool(runner.wait, job["id"])
    return JSONResponse(ended, status_code=201)


def _job_or_404(store: IngestStore, job_id: str, read: Any) -> Any:
    try:
        answer = read(job_id)
    except Unreadable:
        raise HTTPException(status_code=409, detail=UNREADABLE_JOB) from None
    if answer is None:
        raise HTTPException(status_code=404, detail="no solve job with that id is stored")
    return answer


@router.get("/api/ingest/jobs/{job_id}")
def get_ingest_solve_job(
    job_id: str = _job_id_path(), store: IngestStore = Depends(get_ingest_store)
) -> dict[str, Any]:
    """The state and stage progress of a job of any kind: the live record while this server runs it."""
    # Today's solve records by their 16-character ids, the runner's by its 32-character ones; a stored record that
    # fails the contract is answered 409, never served (the docstring above is the published description)
    return _job_or_404(store, job_id, runner_for(store).get)


@router.post("/api/ingest/jobs/{job_id}/cancel")
def cancel_ingest_job(job_id: str = _job_id_path(), store: IngestStore = Depends(get_ingest_store)) -> JSONResponse:
    """Cancel a job of any kind: 200 with a queued job now canceled, or with a job that had already ended, unchanged;
    202 with a running job, which records when the cancel was asked and ends canceled once its work stops, or
    succeeded when it had already committed."""
    job, outcome = _job_or_404(store, job_id, runner_for(store).cancel)
    if outcome == "elsewhere":
        # Another server process runs it; this one cannot stop it (one process serves the API today)
        raise HTTPException(status_code=409)
    return JSONResponse(job, status_code=202 if outcome == "requested" else 200)


@router.get("/api/ingest/{sha256}/solution")
def get_ingest_solution(
    sha256: str = _sha256_path(), store: IngestStore = Depends(get_ingest_store)
) -> dict[str, Any]:
    """The contract ObservationSolution of the newest succeeded solve of a stored upload."""
    if store.read_record(sha256) is None or store.original(sha256) is None:
        raise HTTPException(status_code=404, detail="no file with that hash is stored")
    try:
        solution = JobStore(store).read_solution(sha256)
    except Unreadable:
        raise HTTPException(status_code=409, detail=UNREADABLE_SOLUTION) from None
    if solution is None:
        raise HTTPException(status_code=404, detail="no solution is stored for that file")
    return solution


# ---- The pointing record (pointing-2b) ----


class _NotJson(Exception):
    """The body is not JSON text as JavaScript's JSON.parse reads it (no NaN or Infinity, UTF-8, no BOM)."""


def _no_constant(_token: str) -> Any:
    raise _NotJson


class _NoRecordText(Exception):
    """JSON text that no record can be and Python's reader cannot take whole: a whole number past its 4,300-digit
    limit (`ValueError`), or arrays nested past its recursion limit (`RecursionError`, on Python 3.12)."""


def _json_text(body: bytes) -> Any:
    """The body as JSON; `_NotJson` when it is not JSON text, `_NoRecordText` when the reader cannot take it whole,
    never with a byte of it in the error."""
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, _NotJson):
        raise _NotJson from None
    except (RecursionError, ValueError):
        raise _NoRecordText from None


def _pointing_refusal(status: int, detail: str, issues: list[dict[str, Any]] | None = None) -> JSONResponse:
    reply: dict[str, Any] = {"detail": POINTING_REPLY_SENTENCES[detail]}
    if issues:
        reply["issues"] = issues
    PointingRecordRefusal.model_validate(reply)
    return JSONResponse(reply, status_code=status)


def _pointing_receipt(sha256: str, body: bytes, already_stored: bool) -> JSONResponse:
    receipt = {
        "format": POINTING_RECEIPT_FORMAT,
        "formatVersion": POINTING_RECEIPT_FORMAT_VERSION,
        "sourceAssetSha256": sha256,
        "pointingRecordSha256": hashlib.sha256(body).hexdigest(),
        "alreadyStored": already_stored,
    }
    PointingRecordReceipt.model_validate(receipt)
    return JSONResponse(receipt, status_code=200 if already_stored else 201)


def _issues(error: ValidationError) -> list[dict[str, Any]]:
    """Each failed check of the record as a path of its own keys and a closed sentence: the rule's own sentence
    when it is one, else the route's `notARecord`. Never the value, never a key the record made up (a failure with
    no path of the record's keys is left out), at most the contract's number of issues. The record nests four keys
    deep at most, inside the contract's path depth of eight."""
    issues: list[dict[str, Any]] = []
    for failure in error.errors(include_url=False, include_input=False, include_context=False):
        said = failure["msg"].removeprefix("Value error, ")
        message = said if said in _SENTENCES else POINTING_REPLY_SENTENCES["notARecord"]
        path = POINTING_RULE_PATHS.get(message) or [
            part for part in failure["loc"] if isinstance(part, str) and part in _RECORD_KEYS
        ]
        issue = {"path": path, "message": message}
        if path and issue not in issues:
            issues.append(issue)
        if len(issues) == POINTING_REPLY_ISSUES_MAX:
            break
    return issues


def _solve_job_exists(store: IngestStore, sha256: str) -> bool:
    """Whether a solve of this photo was started: today's `job.json` is there, readable or not, or the job store holds
    a solve record for it. A record that arrives after that is not kept, so a solution never changes under the
    person (design component 4); the same record sent again is still a receipt."""
    if os.path.lexists(store.folder(sha256) / "job.json"):
        return True
    return any(job["kind"] == "solve" for job in JobStore(store).jobs_of(sha256))


def _photo_stored(store: IngestStore, sha256: str) -> bool:
    return bool(SHA256_ADDRESS.fullmatch(sha256)) and store.read_record(sha256) is not None and store.original(sha256) is not None


def _pointing_path() -> Any:
    return Path(description="The photo's SHA-256, 64 lowercase hex characters; any other text names no stored photo")


@router.put(
    "/api/ingest/{sha256}/pointing",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The pointing record (packages/contracts/src/pointing.ts) as JSON text, kept byte for byte",
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
    },
)
async def put_ingest_pointing(
    request: Request, sha256: str = _pointing_path(), store: IngestStore = Depends(get_ingest_store)
) -> JSONResponse:
    """Keep a stored photo's pointing record, once; a receipt names the record's own hash."""
    if not _photo_stored(store, sha256):
        return _pointing_refusal(404, "noSuchPhoto")
    body = await request.body()
    try:
        data = _json_text(body)
    except _NotJson:
        return _pointing_refusal(422, "notJson")
    except _NoRecordText:
        return _pointing_refusal(422, "notARecord")
    try:
        record = PointingRecord.model_validate(data)
    except ValidationError as error:
        return _pointing_refusal(422, "notARecord", _issues(error))
    except (RecursionError, ValueError):
        return _pointing_refusal(422, "notARecord")
    if record.source_asset_sha256 != sha256:
        return _pointing_refusal(422, "anotherPhoto", [{"path": ["sourceAssetSha256"], "message": POINTING_REPLY_SENTENCES["anotherPhoto"]}])
    held, stored = store.read_pointing(sha256), False
    if held is None:
        if _solve_job_exists(store, sha256):
            return _pointing_refusal(409, "solveStarted")
        held, stored = store.write_pointing(sha256, body)
    if held != body:
        return _pointing_refusal(409, "differentRecordHeld")
    return _pointing_receipt(sha256, body, already_stored=not stored)


def _servable_pointing(body: bytes, sha256: str) -> bool:
    try:
        record = PointingRecord.model_validate(_json_text(body))
    except (_NotJson, _NoRecordText, ValidationError, RecursionError, ValueError):
        return False
    return record.source_asset_sha256 == sha256


@router.get(
    "/api/ingest/{sha256}/pointing",
    response_class=Response,
    responses={200: {"description": "The stored pointing record, byte for byte", "content": {"application/json": {}}}},
)
def get_ingest_pointing(sha256: str = _pointing_path(), store: IngestStore = Depends(get_ingest_store)) -> Response:
    """The stored photo's pointing record, exactly as it was sent, once it meets the contract."""
    if not _photo_stored(store, sha256):
        return _pointing_refusal(404, "noSuchPhoto")
    try:
        body = store.read_pointing(sha256)
    except PointingUnreadable:
        raise HTTPException(status_code=409, detail=UNREADABLE_POINTING) from None
    if body is None:
        raise HTTPException(status_code=404, detail=NO_POINTING_RECORD)
    if not _servable_pointing(body, sha256):
        raise HTTPException(status_code=409, detail=UNREADABLE_POINTING)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
