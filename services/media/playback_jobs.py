"""A stored restoration's playback copy, made at first need (playback-firstneed; DECIDED-BY-LEAD 22:19 on 24 Nov and
04:47 on 25 Nov; REQ-JOB-10 of the upload-jobs spec).

A restored clip stored before restore revision 16 has no playback copy, and no restore request can add one (a request
now makes a new variant, its id hashing the pipeline version). This job makes it: the same copy and gate as a new
restoration's (`clip_io.make_playback_copy`), placed by the same writer beside the variant already published
(`variants.place_playback`). The variant's own file, its `variant.json` and its hashes are never touched.

A gate's refusal ends the job `failed` with its fixed sentence, and a start answers that stored job, without a new
one, while this producer's revision is the one that made it (`answers_failed`), so a new encoder or gate tries again.
A restored clip the job cannot copy at all never gets a job: the route refuses it with `PLAYBACK_NOT_COPYABLE`
(`copyable`). This module is the kind's own, so the restore producer's pinned files do not move with it.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from services.contracts.jobs import PLAYBACK_FAILURE_SENTENCES, PLAYBACK_GATE_REFUSED
from services.contracts.media import PLAYBACK_COPY_MIN_PSNR_DB, playback_uri
from services.ingest.job_store import JobStore
from services.ingest.runner import JobContext, JobKind, JobRefusal, KindResult, Producer
from services.media import clip_io, frame_pool, variants

TIME_LIMIT = PLAYBACK_FAILURE_SENTENCES[1]
INTERNAL_ERROR = PLAYBACK_FAILURE_SENTENCES[2]
NOT_STORED = PLAYBACK_FAILURE_SENTENCES[3]
CHANGED = PLAYBACK_FAILURE_SENTENCES[4]
PLAYBACK_PRODUCER_FILES = ("services/media/playback_jobs.py", "services/media/clip_io.py", "services/media/variants.py")
PLAYBACK_PRODUCER = Producer("albedian.playback", 1, PLAYBACK_PRODUCER_FILES)
# The whole job's bound [proposed]: a base, and half a second for each megapixel of every frame, which leaves the
# public clip's copy (328 frames at 1920x1080, its encode and gate about ten seconds at load 9 on this laptop) more
# than ten times its time
BOUND_BASE_SEC = 120.0
BOUND_SEC_PER_FRAME_MEGAPIXEL = 0.5
CHILD_MARGIN_SEC = 5.0
READ_BLOCK = 1 << 20


def _read_record(path: Path) -> dict[str, Any]:
    """A JSON object from a regular file opened without following a link; ValueError otherwise."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            value = json.loads(handle.read())
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(READ_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def stored_clip(variant_folder: Path) -> tuple[dict[str, Any], Path]:
    """A restored clip's variant record and its own file in its folder; ValueError when it is not one."""
    variant = _read_record(variant_folder / "variant.json")
    mime = variant.get("mimeType")
    if variant.get("kind") != "restoration" or mime not in variants.VARIANT_FILES or str(mime).startswith("image/"):
        raise ValueError("not a restored clip")
    return variant, variant_folder / variants.variant_file_name(mime)


def copyable(stored: Path) -> bool:
    """Whether the job can copy this restored clip at all: 8-bit H.264 or 10-bit HEVC as the restore stores it, in a
    stored format the gate measures in as it is. Anything else gets no job (REQ-JOB-10.4)."""
    try:
        probe = clip_io.probe_clip(stored)
        return clip_io.playback_copy_supported(probe.video) and clip_io.stored_pixel_format(stored) in clip_io.PLAYBACK_GATE_FORMATS
    except (clip_io.ClipError, OSError, ValueError):
        return False


def playback_copy_in_child(folder: str, variant_id: str, partials: Sequence[str], progress: Callable[..., None], *,
                           workers: Optional[int] = None) -> dict:
    """In the job's child: the copy of the restored clip `variant_id` of the upload at `folder`, placed beside it.
    {"status": "ready", "variantId"} or {"status": "failed", "failureReason"} with one of the kind's sentences."""
    variant_folder = Path(folder) / "variants" / variant_id
    encoded, out = Path(partials[0]), Path(partials[1])
    try:
        variant, stored = stored_clip(variant_folder)
        if _sha256(stored) != variant.get("sha256"):
            return {"status": "failed", "failureReason": CHANGED}
        probe = clip_io.probe_clip(stored)
        # The encode and its gate report no count of their own: the stage stands with no total until they end
        progress("encode", "frames", 0, None)
        copy = clip_io.make_playback_copy(stored, encoded, out, probe, variant.get("displayOrientation"),
                                          threads=workers or frame_pool.default_workers(), min_psnr_db=PLAYBACK_COPY_MIN_PSNR_DB)
        progress("encode", "frames", probe.frame_count, probe.frame_count)
        progress("verify", "frames", probe.frame_count, probe.frame_count)
        if copy is None:
            return {"status": "failed", "failureReason": PLAYBACK_GATE_REFUSED}
        if _sha256(stored) != variant.get("sha256"):
            return {"status": "failed", "failureReason": CHANGED}
        size = copy.path.stat().st_size
        record = {"uri": playback_uri(variant["sourceSha256"], variant_id), "sha256": _sha256(copy.path), "byteSize": size,
                  "mimeType": "video/mp4", "codecs": copy.codecs, "minPsnrDb": copy.min_psnr_db}
        try:
            variants.place_playback(variant_folder, variant, variants.PlaybackFile(record, copy.path))
        except OSError:
            return {"status": "failed", "failureReason": NOT_STORED}
        progress("write", "bytes", size, size)
        return {"status": "ready", "variantId": variant_id}
    except clip_io.ClipError as error:
        return {"status": "failed", "failureReason": TIME_LIMIT if error.kind == "time_limit" else INTERNAL_ERROR}
    except (OSError, ValueError, KeyError):
        return {"status": "failed", "failureReason": INTERNAL_ERROR}
    finally:
        for path in (encoded, out):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def run_playback_copy(ctx: JobContext) -> KindResult:
    ctx.check_cancel()
    request = dict(ctx.request or {})
    partials = (str(ctx.partial("playback_encoded.mp4")), str(ctx.partial(variants.PLAYBACK_FILE_NAME)))
    outcome = ctx.child().call(
        playback_copy_in_child, (str(ctx.original.parent), request["variantId"], partials),
        timeout_s=max(1.0, ctx.remaining_s() - CHILD_MARGIN_SEC), stop=ctx.stop, progress_kwarg="progress",
        on_event=lambda stage, unit, done, total: ctx.progress(stage, unit, done, total))
    if outcome["status"] == "failed":
        raise JobRefusal(outcome["failureReason"])
    return KindResult("succeeded", {"variantId": outcome["variantId"]})


def bound_s(asset: Mapping[str, Any], request: Optional[Mapping[str, Any]]) -> float:
    megapixels = int(asset["width"]) * int(asset["height"]) / 1e6
    frames = int(asset.get("frameCount") or 1)
    return BOUND_BASE_SEC + BOUND_SEC_PER_FRAME_MEGAPIXEL * megapixels * frames


def subject(sha256: str, request: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The variant it copies: a job is one per restored clip (REQ-JOB-10.1)."""
    return None if request is None else request.get("variantId")


def copy_stored(store: JobStore, record: Mapping[str, Any]) -> bool:
    """Whether a succeeded job's copy is in place: its record and its file beside the variant, each a regular file."""
    variant_id = record.get("variantId")
    if not isinstance(variant_id, str):
        return False
    folder = store.upload_folder(record["sourceAssetSha256"]) / "variants" / variant_id
    try:
        return all(stat.S_ISREG(os.lstat(folder / name).st_mode)
                   for name in (variants.PLAYBACK_RECORD_NAME, variants.PLAYBACK_FILE_NAME))
    except OSError:
        return False


def gate_refused_here(record: Mapping[str, Any]) -> bool:
    """A failed job that is its work's own answer: the gate refused the copy, under this producer's revision."""
    return record.get("error") == PLAYBACK_GATE_REFUSED and record.get("producer") == PLAYBACK_PRODUCER.as_record()


PLAYBACK_COPY_KIND = JobKind(
    name="playback_copy", group="media", producer=PLAYBACK_PRODUCER, run=run_playback_copy, time_limit_s=bound_s,
    time_limit_reason=TIME_LIMIT, internal_error_reason=INTERNAL_ERROR, subject=subject, store_failure_reason=NOT_STORED,
    results_valid=copy_stored, answers_failed=gate_refused_here)
