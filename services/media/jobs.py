"""The media kinds of the upload job runner: a display copy and a restoration of a stored still (spec
restoration-for-every-upload, design sections 9, 11 and 13; REQ-RST-1, REQ-RST-2.5 to 2.7, REQ-RST-8).

Each kind's work runs in the media group's child process (`ctx.child()`), so pixels never reach the API process: the
child gets the store's paths, the hash and the request, reports its stages, and stores the variant folder and the
attempt record itself, as the last step before the kind returns (the runner's note: the media kinds publish with
`variants.publish_variant` and `variants.keep_attempt`). The original is hashed at the start and at the end.

Clips: a display copy is refused with the contract's sentence (REQ-RST-2.7); a restoration of a clip is restore-7's
and is refused before any job is made (the route).
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from services.contracts.ingest import format_utc
from services.contracts.media import DISCLOSURES, MediaVariant, ProcessingAttempt, ProcessingGraph, variant_uri
from services.ingest.job_store import JobStore
from services.ingest.runner import JobContext, JobKind, JobRefusal, KindResult, Producer
from services.media import decode, photo, variants

# The source files a restoration's or display copy's result comes from (platform-6: the guard pins their digest)
MEDIA_PRODUCER_FILES = (
    "services/media/jobs.py",
    "services/media/photo.py",
    "services/media/gate.py",
    "services/media/encode.py",
    "services/media/decode.py",
    "services/media/colour.py",
    "services/media/png16.py",
    "services/media/variants.py",
    "services/media/stars.py",
    "services/media/noise.py",
    "services/media/spatial.py",
    "services/media/tone.py",
    "services/science/detector.py",
)
# Revisions: 1, the first (restore-2b); 2, kept edges stored as the gate computes them and samples raised over both
# their surroundings and the original's own dissolved by the passes (restore-4f); 3, a stored still's colour
# profile keeps its colour data and loses its text (restore-8b)
MEDIA_PRODUCER = Producer("albedian.restore", 3, MEDIA_PRODUCER_FILES)
# The whole job's bound: a base and seconds per megapixel of every frame [proposed in design section 11: three times the
# prototype's still rate and a base for starting the child, probing and hashing]
BOUND_BASE_SEC = 120.0
BOUND_SEC_PER_MEGAPIXEL = 6.0
# The child's own bound leaves this much of the job's for storing the ending record [proposed: the runner writes it]
CHILD_MARGIN_SEC = 5.0
CLIP_DISPLAY_COPY = "A display copy is not made for a clip."
CHANGED = "The original changed while the job ran."
STEP_VERSION = "1.0.0"


def bound_s(asset: Mapping[str, Any], request: Optional[Mapping[str, Any]]) -> float:
    """120 s and 6 s for each megapixel of every frame (design section 11)."""
    frames = int(asset.get("frameCount") or 1)
    megapixels = int(asset["width"]) * int(asset["height"]) / 1e6
    return BOUND_BASE_SEC + BOUND_SEC_PER_MEGAPIXEL * megapixels * frames


def subject(sha256: str, request: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The graph id: a job is one per upload, kind and request (design section 9)."""
    if request is None:
        return None
    return variants.graph_id(sha256, request["kind"], dict(request))


def clip_refusal(asset: Mapping[str, Any], kind: str) -> Optional[str]:
    """The sentence a kind refuses a clip with, or None for a still."""
    if asset.get("mediaKind") == "image":
        return None
    return CLIP_DISPLAY_COPY if kind == "display_copy" else None


def _results_stored(store: JobStore, record: Mapping[str, Any]) -> bool:
    """Whether what an ending record names is stored: a rejected job's kept attempt, which holds the reason it was
    refused (platform-8); a succeeded job's variant file and records, so it answers as current only while they are in
    place."""
    folder = store.upload_folder(record["sourceAssetSha256"])
    if record.get("state") == "rejected":
        attempt_id = record.get("attemptId")
        return isinstance(attempt_id, str) and (folder / "attempts" / f"{attempt_id}.json").is_file()
    variant_id = record.get("variantId")
    if not isinstance(variant_id, str):
        return False
    variant = folder / "variants" / variant_id
    return all((variant / name).is_file() for name in ("variant.png", "variant.json", "graph.json", "attempt.json"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _paths(ctx: JobContext) -> tuple[str, str, str]:
    """The store's root, the upload's folder and the original, as text for the child (the store's layout:
    `<root>/<aa>/<sha256>/original.<ext>`)."""
    folder = ctx.original.parent
    return str(folder.parent.parent), str(folder), str(ctx.original)


def _run_in_child(ctx: JobContext, target: Callable[..., dict], *args: Any) -> KindResult:
    ctx.check_cancel()
    outcome = ctx.child().call(
        target, args, timeout_s=max(1.0, ctx.remaining_s() - CHILD_MARGIN_SEC), stop=ctx.stop,
        progress_kwarg="progress", on_event=lambda stage, unit, done, total: ctx.progress(stage, unit, done, total))
    if outcome["status"] == "failed":
        raise JobRefusal(outcome["failureReason"])
    fields = {"attemptId": outcome["attemptId"]}
    if outcome["status"] == "ready":
        fields["variantId"] = outcome["variantId"]
        return KindResult("succeeded", fields)
    return KindResult("rejected", fields)


def run_display_copy(ctx: JobContext) -> KindResult:
    refusal = clip_refusal(ctx.asset, "display_copy")
    if refusal is not None:
        raise JobRefusal(refusal)
    return _run_in_child(ctx, display_copy_in_child, *_paths(ctx), ctx.sha256, ctx.job_id,
                         ctx.asset.get("displayOrientation"))


def run_restoration(ctx: JobContext) -> KindResult:
    return _run_in_child(ctx, restoration_in_child, *_paths(ctx), ctx.sha256, ctx.job_id, dict(ctx.request or {}),
                         ctx.asset.get("displayOrientation"))


# =============================================================================================
# In the child
# =============================================================================================


def display_copy_in_child(store_root: str, folder: str, original: str, sha256: str, job_id: str,
                          orientation: Optional[dict], progress: Callable[..., None]) -> dict:
    progress("decode", "frames", 0, 1)
    copy = variants.make_display_copy(Path(store_root), Path(folder), Path(original), source_sha256=sha256,
                                      job_id=job_id, display_orientation=orientation)
    attempt = copy.attempt
    if copy.status == "failed":
        return {"status": "failed", "attemptId": attempt["id"], "failureReason": attempt["failureReason"]}
    for stage in ("decode", "encode"):
        progress(stage, "frames", 1, 1)
    if copy.variant is not None:
        progress("write", "bytes", copy.variant["byteSize"], copy.variant["byteSize"])
    progress("verify", "frames", 1, 1)
    out = {"status": copy.status, "attemptId": attempt["id"]}
    if copy.variant is not None:
        out["variantId"] = copy.variant["id"]
    return out


def restoration_in_child(store_root: str, folder: str, original: str, sha256: str, job_id: str, request: dict,
                         orientation: Optional[dict], progress: Callable[..., None], *,
                         now: Optional[Callable[[], datetime]] = None) -> dict:
    """Restore the stored still, store its variant when the gate passes, keep the attempt either way."""
    clock = now or (lambda: datetime.now().astimezone())
    started = format_utc(clock())
    wall, cpu = time.monotonic(), time.process_time()
    graph_ref = variants.graph_id(sha256, "restoration", request)
    attempt_ref = variants.attempt_id(graph_ref, started)
    source_folder, original_path = Path(folder), Path(original)

    def attempt(status: str, **fields: Any) -> dict:
        record = {"id": attempt_ref, "sourceSha256": sha256, "graphId": graph_ref, "jobId": job_id, "status": status,
                  "startedAtUTC": started, "finishedAtUTC": format_utc(clock()),
                  "elapsedMs": round(1000 * (time.monotonic() - wall)),
                  "cpuMs": round(1000 * (time.process_time() - cpu)), **fields}
        ProcessingAttempt.model_validate(record)
        return record

    def failed(sentence: str) -> dict:
        record = attempt("failed", failureReason=sentence)
        variants.keep_attempt(source_folder, record)
        return {"status": "failed", "attemptId": record["id"], "failureReason": sentence}

    if _sha256(original_path) != sha256:
        return failed(CHANGED)
    progress("decode", "frames", 0, 1)
    try:
        still = decode.decode_still(original_path)
    except decode.DecodeRefusal as refusal:
        return failed(refusal.sentence)
    progress("decode", "frames", 1, 1)
    progress("measure", "frames", 0, 1)
    try:
        result = photo.restore_still(still, request.get("exposureEv"))
    except photo.PhotoError:
        return failed(decode.INTERNAL_ERROR)
    for stage in ("measure", "restore", "encode"):
        progress(stage, "frames", 1, 1)
    graph = {"id": graph_ref, "sourceSha256": sha256, "kind": "restoration", "pipelineVersion": variants.PIPELINE_VERSION,
             "request": request, "steps": result.steps}
    ProcessingGraph.model_validate(graph)
    if _sha256(original_path) != sha256:
        return failed(CHANGED)
    if result.status != "ready":
        record = attempt("rejected", measured=result.measured, gate=result.gate, rejection=result.rejection)
        variants.keep_attempt(source_folder, record)
        progress("verify", "frames", 1, 1)
        return {"status": "rejected", "attemptId": record["id"]}
    data = result.data
    vid = variants.variant_id(sha256, "restoration", request)
    record = attempt("ready", variantId=vid, measured=result.measured, gate=result.gate)
    height, width = result.stored.shape[:2]
    variant = {
        "id": vid, "kind": "restoration", "sourceSha256": sha256, "sourceAssetId": f"src_{sha256[:16]}",
        "graphId": graph_ref, "attemptId": record["id"], "sha256": hashlib.sha256(data).hexdigest(),
        "uri": variant_uri(sha256, vid), "byteSize": len(data), "mimeType": "image/png", "width": width,
        "height": height, "durationMs": 0, "frameCount": 1, "audioStreams": [], "colour": {**still.colour, "bitDepth": 16},
        "disclosure": DISCLOSURES["restoration"], "createdAtUTC": record["finishedAtUTC"],
    }
    if orientation is not None:
        variant["displayOrientation"] = orientation
    MediaVariant.model_validate(variant)
    progress("write", "bytes", 0, len(data))
    variants.publish_variant(Path(store_root), source_folder, data, {"variant": variant, "graph": graph, "attempt": record})
    progress("write", "bytes", len(data), len(data))
    variants.keep_attempt(source_folder, record)
    progress("verify", "frames", 1, 1)
    return {"status": "ready", "attemptId": record["id"], "variantId": vid}


DISPLAY_COPY_KIND = JobKind(
    name="display_copy", group="media", producer=MEDIA_PRODUCER, run=run_display_copy, time_limit_s=bound_s,
    time_limit_reason=decode.TIME_LIMIT, internal_error_reason=decode.INTERNAL_ERROR, subject=subject,
    results_valid=_results_stored)
RESTORATION_KIND = JobKind(
    name="restoration", group="media", producer=MEDIA_PRODUCER, run=run_restoration, time_limit_s=bound_s,
    time_limit_reason=decode.TIME_LIMIT, internal_error_reason=decode.INTERNAL_ERROR, subject=subject,
    results_valid=_results_stored)
MEDIA_KINDS = (DISPLAY_COPY_KIND, RESTORATION_KIND)
