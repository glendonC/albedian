"""The media kinds of the upload job runner: a display copy and a restoration of a stored still (spec
restoration-for-every-upload, design sections 9, 11 and 13; REQ-RST-1, REQ-RST-2.5 to 2.7, REQ-RST-8).

Each kind's work runs in the media group's child process (`ctx.child()`), so pixels never reach the API process: the
child gets the store's paths, the hash and the request, reports its stages, and stores the variant folder and the
attempt record itself, as the last step before the kind returns (the runner's note: the media kinds publish with
`variants.publish_variant` and `variants.keep_attempt`). The original is hashed at the start and at the end.

Clips: a display copy is refused with the contract's sentence (REQ-RST-2.7). A restoration of a clip (restore-7) runs
the same way, its frames through the child's own pool of workers (`frame_pool`): measured once, then restored and
encoded in order, then its stored file checked and gated frame by frame against the original's own pixels.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import math
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from services.contracts.ingest import format_utc
from services.contracts.media import DISCLOSURES, NOTES, MediaVariant, ProcessingAttempt, ProcessingGraph, variant_uri
from services.ingest.job_store import JobStore
from services.ingest.runner import JobContext, JobKind, JobRefusal, KindResult, Producer
from services.media import (clip_frames, clip_io, decode, frame_pool, gate, noise, photo, spatial, stars, temporal, tone,
                            variants)

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
    "services/media/chroma.py",
    "services/media/bars.py",
    "services/media/tone.py",
    "services/science/detector.py",
    # A clip's result also comes from these (restore-7)
    "services/media/clip_io.py",
    "services/media/clip_frames.py",
    "services/media/frame_pool.py",
    "services/media/temporal.py",
)
# Revisions: 1, the first (restore-2b); 2, kept edges stored as the gate computes them and samples raised over both
# their surroundings and the original's own dissolved by the passes (restore-4f); 3, a stored still's colour
# profile keeps its colour data and loses its text (restore-8b). restore-7 keeps 3: it changes no result a
# still's job stored, and a clip had none before it; 4, a stored still's record names the profile its own file holds,
# and a photo whose profile cannot be rebuilt gets a converted or untagged display copy (restore-8d, 8e); 5, raised
# samples hiding each other on their rings read as a group against a ring round the group (restore-4g); 6, the flux
# check kept when no measured star keeps a positive flux ratio (the flux gate unit); 7, a tile's matched noise from
# the detector's single-tile noise map floored at its pixel and rounding noise, and its background and pixel sigma from
# the detector's grid path (quantised-noise); 8, coarse-scale chroma smoothing before dissolve on stills
# (smooth-chroma-1); 9, pre-filter C36 refusal of near-black clips whose darkest quarter sits on the black floor, per-frame
# MIN_SIDE_PX check in measure_clip, and measure stage held short of full until measure_clip returns (nearblackclip-1);
# 10, a still with black bars on both opposite edges that open its picture is measured, restored and gated inside them
# and keeps its bars (photo-bars-one); 11, a clip whose darkest quarter is a flat dark band above black takes the
# not-measured path as dark_level_flat, and a dark-level noise made of rounding reads as zero (flatband-two); 12, a
# still whose darkest quarter is a flat band off the black clip takes the not-measured path as dark_level_flat, by the
# clip's flat test, now one helper in noise.py, border reach included (photo-flatband-one); 13, a clip with black bars
# that open its picture on both opposite edges is measured, restored and gated inside them and keeps its own bars
# (letterbox-2); 14, bars after a lossy encode keep up to one transform block of ringing next to the picture with
# them (bars-lossy-one); 15, a still's sky keeps 0.35 of its own finest noise less its star-shaped part, put in after
# the lift and capped block by block where it would lift the gate's noise over its floor, or none where the gate
# refuses it (fine-noise-floor-one)
MEDIA_PRODUCER = Producer("albedian.restore", 15, MEDIA_PRODUCER_FILES)
# The whole job's bound: a base, and for every frame seconds per megapixel that grow with its megapixels (platform-9:
# public stills restored on this laptop at load 10 to 19 took 5 s per megapixel at 2.4, 46 at 24.2 and 147 at 50.1,
# so 4 s x megapixels squared leaves an iPhone's 24 megapixel photo twice the slowest measured one of its size and a
# 50 megapixel one 1.4 times its measured run) [measured; the rate stands until the gate's speed work lands]
BOUND_BASE_SEC = 120.0
BOUND_SEC_PER_SQUARE_MEGAPIXEL = 4.0
# A clip's: seconds per megapixel of every frame for each of its pool's workers [proposed: three times the worst rate
# measured on the owner's clips, 6.7 s per megapixel of a frame for each of 2 workers under a load of 13 to 19, restore-7;
# the restoration of a clip at black finds and gates hundreds of stars in every frame]
CLIP_BOUND_SEC_PER_MEGAPIXEL = 20.0
# The child's own bound leaves this much of the job's for storing the ending record [proposed: the runner writes it]
CHILD_MARGIN_SEC = 5.0
CLIP_DISPLAY_COPY = "A display copy is not made for a clip."
CHANGED = "The original changed while the job ran."
STEP_VERSION = "1.0.0"


def clip_frame_count(asset: Mapping[str, Any]) -> int:
    """A clip's frames as its ingest record counts them: its duration at its frame rate, rounded up, and one more; at
    CLIP_BOUND_FPS where the record gives no rate. A bound's count, never a stage's total (that is the probe's)."""
    numerator, denominator = asset.get("fpsNumerator"), asset.get("fpsDenominator")
    rate = numerator / denominator if numerator and denominator else CLIP_BOUND_FPS
    return math.ceil(float(asset.get("durationMs") or 0.0) / 1000.0 * rate) + 1


def bound_s(asset: Mapping[str, Any], request: Optional[Mapping[str, Any]]) -> float:
    """120 s and 4 s for each square megapixel of every frame of a still (platform-9; design section 11 had 6 s a
    megapixel); for a clip, 120 s and CLIP_BOUND_SEC_PER_MEGAPIXEL for each megapixel of every frame, divided by its
    pool's workers (restore-7)."""
    megapixels = int(asset["width"]) * int(asset["height"]) / 1e6
    if asset.get("mediaKind") != "video":
        frames = int(asset.get("frameCount") or 1)
        return BOUND_BASE_SEC + BOUND_SEC_PER_SQUARE_MEGAPIXEL * megapixels * megapixels * frames
    return BOUND_BASE_SEC + CLIP_BOUND_SEC_PER_MEGAPIXEL * megapixels * clip_frame_count(asset) / frame_pool.default_workers()


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
    refused (platform-8); a succeeded job's variant file, a still's or a clip's (`variants.VARIANT_FILES`), and
    records, so it answers as current only while they are in place."""
    folder = store.upload_folder(record["sourceAssetSha256"])
    if record.get("state") == "rejected":
        attempt_id = record.get("attemptId")
        return isinstance(attempt_id, str) and (folder / "attempts" / f"{attempt_id}.json").is_file()
    variant_id = record.get("variantId")
    if not isinstance(variant_id, str):
        return False
    variant = folder / "variants" / variant_id
    return (any((variant / name).is_file() for name in sorted(set(variants.VARIANT_FILES.values())))
            and all((variant / name).is_file() for name in ("variant.json", "graph.json", "attempt.json")))


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
    if ctx.asset.get("mediaKind") != "video":
        return _run_in_child(ctx, restoration_in_child, *_paths(ctx), ctx.sha256, ctx.job_id, dict(ctx.request or {}),
                             ctx.asset.get("displayOrientation"))
    extension = variants.variant_file_name(str(ctx.asset.get("mimeType"))).rsplit(".", 1)[-1] \
        if ctx.asset.get("mimeType") in variants.VARIANT_FILES else "clip"
    partials = (str(ctx.partial(f"clip_encoded.{extension}")), str(ctx.partial(f"variant.{extension}")))
    facts = {"mimeType": ctx.asset.get("mimeType"), "durationMs": ctx.asset.get("durationMs"),
             "audioStreams": list(ctx.asset.get("audioStreams") or [])}
    return _run_in_child(ctx, clip_restoration_in_child, *_paths(ctx), ctx.sha256, ctx.job_id, dict(ctx.request or {}),
                         ctx.asset.get("displayOrientation"), facts, partials)


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
    # The record names the profile the stored file holds; one that cannot be rebuilt leaves no true description, so
    # the attempt is refused before any restoring, in its own words (restore-8d; review row C20, restore-8e)
    colour = photo.stored_colour(still.colour, still.icc_profile)
    if colour is None:
        return failed(photo.PROFILE_NOT_CARRIED)
    progress("decode", "frames", 1, 1)
    progress("measure", "frames", 0, 1)
    try:
        result = photo.restore_still(still, request.get("exposureEv"))
    except photo.PhotoRefusal as refusal:
        return failed(refusal.sentence)
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
        "height": height, "durationMs": 0, "frameCount": 1, "audioStreams": [], "colour": {**colour, "bitDepth": 16},
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


# =============================================================================================
# A clip, in the child (restore-7; design sections 10 and 11)
# =============================================================================================

# A clip's variant is stored without loss, x264 at this quality being lossless, so the gate's exact rules hold on the
# stored clip as on a still's PNG [tested: restore-7, the owner's clips lifted as the job lifts them: crf 16 moved a
# sample by up to 24 to 40 codes of 255, crf 0 by none, at 1.6 to 3.7 times the bytes; decided 2026-11-19 by the lead:
# what is served is the file the gate ran on, and a smaller viewing copy would need a gate of its own]
CLIP_CRF = 0
CLIP_PRESET = clip_io.DEFAULT_PRESET
# A clip whose ingest record gives no frame rate is bounded as if at this many frames a second [proposed: above the
# rate of any phone's ordinary video]
CLIP_BOUND_FPS = 60
NOTE_BLACK = NOTES[1]
NOTE_REGISTRATION = NOTES[3]
NOTE_STREAMS = NOTES[6]


def frame_times_sha256(time_base: Fraction, frame_times: Sequence[int]) -> str:
    """The variant's `frameTimesSha256`: the SHA-256 of the canonical JSON of its time base, as "n/d", and its integer
    frame times [proposed: the contract names the two and no encoding]."""
    value = {"timeBase": f"{time_base.numerator}/{time_base.denominator}", "frameTimes": [int(t) for t in frame_times]}
    return hashlib.sha256(variants.canonical_json(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class _Recorder:
    """The attempt's record and its keeping, alike for every way a clip's job ends."""

    def __init__(self, sha256: str, job_id: str, request: dict, folder: Path, now: Optional[Callable[[], datetime]]):
        self.clock = now or (lambda: datetime.now().astimezone())
        self.started = format_utc(self.clock())
        self.wall, self.cpu = time.monotonic(), time.process_time()
        self.sha256, self.job_id, self.request, self.folder = sha256, job_id, request, folder
        self.graph_ref = variants.graph_id(sha256, "restoration", request)
        self.attempt_ref = variants.attempt_id(self.graph_ref, self.started)

    def record(self, status: str, **fields: Any) -> dict:
        record = {"id": self.attempt_ref, "sourceSha256": self.sha256, "graphId": self.graph_ref, "jobId": self.job_id,
                  "status": status, "startedAtUTC": self.started, "finishedAtUTC": format_utc(self.clock()),
                  "elapsedMs": round(1000 * (time.monotonic() - self.wall)),
                  "cpuMs": round(1000 * (time.process_time() - self.cpu)), **fields}
        ProcessingAttempt.model_validate(record)
        return record

    def failed(self, sentence: str) -> dict:
        record = self.record("failed", failureReason=sentence)
        variants.keep_attempt(self.folder, record)
        return {"status": "failed", "attemptId": record["id"], "failureReason": sentence}

    def rejected(self, rejection: str, measured: dict, gate_record: Optional[dict] = None) -> dict:
        fields = {"measured": measured, "rejection": rejection}
        if gate_record is not None:
            fields["gate"] = gate_record
        record = self.record("rejected", **fields)
        variants.keep_attempt(self.folder, record)
        return {"status": "rejected", "attemptId": record["id"]}


class _Stage:
    """One stage's count, reported as it rises and never above its total."""

    def __init__(self, progress: Callable[..., None], stage: str, unit: str, total: Optional[int]):
        self.progress, self.stage, self.unit, self.total, self.done = progress, stage, unit, total, 0
        progress(stage, unit, 0, total)

    def add(self, count: int = 1) -> None:
        self.done += count
        self.progress(self.stage, self.unit, self.done, self.total)


def _clip_steps(clip: clip_frames.ClipColour, settings: clip_frames.ClipSettings, probe: clip_io.ClipProbe,
                container: str, codec: str) -> list[dict]:
    """The clip's graph: the photo path's steps in pipeline order, the register and merge steps with a noise table, the
    encode as it ran and the audio copy."""
    measured = settings.measurement.reason is None
    steps = [{"kind": "decode", "version": STEP_VERSION, "parameters": clip.parameters()},
             {"kind": "find_stars", "version": stars.STEP_VERSION, "parameters": stars.parameters() + photo._settings(
                 starMinRadiusPx=gate.STAR_MIN_RADIUS_PX, farRingGapPx=gate.FAR_RING_GAP_PX,
                 farRingWidthPx=gate.FAR_RING_WIDTH_PX)},
             {"kind": "measure_noise", "version": noise.STEP_VERSION, "parameters": noise.parameters()}]
    if measured:
        steps.append({"kind": "register", "version": temporal.STEP_VERSION, "parameters": temporal.register_parameters()})
        steps.append({"kind": "temporal_merge", "version": temporal.STEP_VERSION, "parameters": temporal.merge_parameters()})
        steps.append({"kind": "spatial_denoise", "version": spatial.STEP_VERSION, "parameters": spatial.parameters() + photo._settings(
            keptGrowPx=gate.KEPT_GROW_PX, firstScaleLevels=gate.FIRST_SCALE_LEVELS, brightSkyNoises=gate.BRIGHT_SKY_NOISES,
            brightHaloNoises=gate.BRIGHT_HALO_NOISES, brightLevels=gate.BRIGHT_LEVELS,
            brightBackgroundPercentile=gate.BACKGROUND_PERCENTILE, brightBackgroundPx=gate.BACKGROUND_PX)})
    steps.append({"kind": "dissolve_unconfirmed", "version": gate.STEP_VERSION, "parameters": [
        photo._parameter("maxPasses", gate.MAX_PASSES), photo._parameter("variantThresholdSnr", gate.DISSOLVE_THRESHOLD_SNR),
        photo._parameter("statistic", gate.STATISTIC)] + photo._settings(
        sensitivityPeakCodes=gate.SENSITIVITY_PEAK_CODES, floorMargin=gate.FLOOR_MARGIN,
        gateThresholdSnr=gate.VARIANT_THRESHOLD_SNR, fillLevels=gate.DISSOLVE_FILL_LEVELS,
        discMinRadiusPx=gate.DISSOLVE_MIN_RADIUS_PX, discGrowPx=gate.DISSOLVE_GROW_PX, discEdgePx=gate.DISSOLVE_EDGE_PX,
        nearCorePx=gate.NEAR_CORE_PX, unchangedRadiusPx=gate.UNCHANGED_RADIUS_PX,
        unchangedToleranceCodes=gate.UNCHANGED_TOLERANCE_CODES16)})
    steps.append({"kind": "lift", "version": tone.STEP_VERSION, "parameters": settings.lift.parameters()})
    basis = "Proposed in design section 10."
    steps.append({"kind": "encode", "version": STEP_VERSION, "parameters": [
        photo._parameter("container", container, basis=basis), photo._parameter("encoder", codec, basis=basis),
        photo._parameter("preset", CLIP_PRESET, basis=basis),
        photo._parameter("crf", CLIP_CRF, basis="Decided by the lead in design section 10."),
        photo._parameter("pixelFormat", probe.video.pix_fmt, origin="measured", basis="Read from the stream's own pixel format."),
        photo._parameter("bitDepth", clip.bits, origin="measured", basis="Read from the stream's own pixel format.")]})
    if probe.audio:
        steps.append({"kind": "copy_audio", "version": STEP_VERSION, "parameters": [
            photo._parameter("streams", len(probe.audio), origin="measured", basis="Counted from the asset record's audio streams.")]})
    return steps


def _clip_measured(clip: clip_frames.ClipColour, settings: clip_frames.ClipSettings, probe: clip_io.ClipProbe,
                   frames: Sequence[clip_frames.FrameResult], gated: Sequence[clip_frames.FrameGate], peak_bytes: int) -> dict:
    """The attempt's `RestorationMeasurements` for a clip, every count added over its frames."""
    measurement = settings.measurement
    measured: dict = {"colour": clip.description(), "notes": []}
    if measurement.reason is None:
        measured["noise"] = measurement.table()
        measured["fineNoiseLeft"] = settings.fine_noise_left
    else:
        measured["noiseNotMeasured"] = measurement.reason
    measured["stars"] = {"found": sum(g.stars_found for g in gated), "confirmed": sum(g.stars_confirmed for g in gated)}
    passes = max((len(r.dissolved) for r in frames), default=0)
    measured["dissolvedPerPass"] = [sum(r.dissolved[i] for r in frames if len(r.dissolved) > i) for i in range(passes)]
    measured["lift"] = settings.lift.record()
    closure = {start: error for r in frames for start, error in r.closure if math.isfinite(error)}
    samples = sum(r.samples for r in frames)
    clip_counts = {"frameCount": len(frames), "framesWithNeighbours": sum(r.neighbours > 0 for r in frames),
                   "exactRepeatShare": sum(r.repeats for r in frames) / samples if samples else 0.0,
                   "pointsConfirmedOnFramesTogether": sum(g.together for g in gated),
                   "pointsFixedToSensor": sum(g.fixed for g in gated), "streamsNotCopied": probe.other_streams}
    if closure:
        clip_counts["medianClosureErrorPx"] = float(np.median(list(closure.values())))
    measured["clip"] = clip_counts
    measured["peakMemoryBytes"] = peak_bytes
    notes = [NOTE_BLACK if note == tone.NOTE_NOISE_NOT_MEASURED and measurement.reason == "fine_detail_mostly_zero" else note
             for note in settings.lift.notes]
    if measurement.reason is None and any(r.neighbours == 0 for r in frames):
        notes.append(NOTE_REGISTRATION)
    if any(measured["dissolvedPerPass"]):
        notes.append(photo.NOTE_DISSOLVED)
    if any(r.clipped for r in frames):
        notes.append(photo.NOTE_CLIPPED)
    if probe.other_streams:
        notes.append(NOTE_STREAMS)
    measured["notes"] = notes
    return measured


def clip_restoration_in_child(store_root: str, folder: str, original: str, sha256: str, job_id: str, request: dict,
                              orientation: Optional[dict], facts: dict, partials: Sequence[str],
                              progress: Callable[..., None], *, now: Optional[Callable[[], datetime]] = None,
                              workers: Optional[int] = None) -> dict:
    """Restore the stored clip: measure it once, restore and encode its frames in order through the pool, check the
    stored file (frame times, audio, orientation), gate it frame by frame against the original's own pixels, store
    its variant when every check and the gate pass, keep the attempt either way. Its partial files are removed on
    every way out but the one that publishes them."""
    recorder = _Recorder(sha256, job_id, request, Path(folder), now)
    original_path = Path(original)
    encoded_path, variant_path = Path(partials[0]), Path(partials[1])
    if _sha256(original_path) != sha256:
        return recorder.failed(CHANGED)
    try:
        return _restore_clip(recorder, Path(store_root), original_path, request, orientation, facts, encoded_path,
                             variant_path, progress, workers or frame_pool.default_workers())
    except clip_io.ClipError as error:
        return recorder.failed(error.sentence)
    except clip_frames.ClipRefusal as refusal:
        return recorder.failed(refusal.sentence)
    except clip_frames.ClipFramesError:
        return recorder.failed(decode.INTERNAL_ERROR)
    finally:
        _remove(encoded_path)
        _remove(variant_path)


def _restore_clip(recorder: _Recorder, store_root: Path, original_path: Path, request: dict, orientation: Optional[dict],
                  facts: dict, encoded_path: Path, variant_path: Path, progress: Callable[..., None], workers: int) -> dict:
    probe = clip_io.probe_clip(original_path)
    clip = clip_frames.clip_colour(probe.video)
    container = clip_io.container_for(str(facts.get("mimeType")))
    count = probe.frame_count
    # Once for the clip: the noise table and the lift from frames spread over the whole of it; `measure` stays short of
    # `total` until `measure_clip` returns so a pre-restoration refusal marks Measured rather than Opened or Restoring
    wanted = clip_frames.sample_indices(count)
    measure = _Stage(progress, "measure", "frames", len(wanted))
    sampled = []
    with clip_io.ClipDecoder(original_path, probe) as decoder:
        for frame in decoder:
            if frame.index in wanted:
                sampled.append(tuple(np.array(plane) for plane in frame.planes))
                if len(sampled) < len(wanted):
                    measure.add()
                else:
                    break
    # The measure frames' stars in the pool, as many at once as there are workers (clipmeasure-pool-one) [decided
    # 2026-11-24 by the lead: one after another they were 32 of the public clip's 34 s of measure]
    # found inside the clip's bars, as the measure reads them (letterbox-2)
    with frame_pool.FramePool(functools.partial(clip_frames.measure_stars, clip, clip_frames.clip_bars(clip, sampled)),
                              workers=max(1, min(workers, len(sampled)))) as pool:
        fields = list(pool.map(frame_pool.runs(iter(sampled), run_frames=1, window=0)))
        measure_peak = pool.worker_peak_bytes
    settings = clip_frames.measure_clip(clip, sampled, request.get("exposureEv"), fields)
    measure.add()
    del sampled, fields
    # Every frame restored in the pool and encoded in order under a partial name
    turned = clip_io._form(orientation) is not None
    encoded = encoded_path if turned else variant_path
    decoded, restored, written = (_Stage(progress, stage, "frames", count) for stage in ("decode", "restore", "encode"))
    frames: list[clip_frames.FrameResult] = []
    with clip_io.ClipDecoder(original_path, probe) as decoder, clip_io.ClipEncoder(
        encoded, probe.video, time_base=probe.time_base, frame_times=probe.frame_times, container=container,
        audio_source=original_path if probe.audio else None, crf=CLIP_CRF, preset=CLIP_PRESET,
        last_duration=probe.last_frame_duration,
    ) as encoder, frame_pool.FramePool(functools.partial(clip_frames.restore_run, settings), workers=workers) as pool:
        def source():
            for frame in decoder:
                decoded.add()
                yield frame.planes

        for result in pool.map(frame_pool.runs(source(), window=clip_frames.RUN_WINDOW)):
            restored.add()
            encoder.write(result.planes)
            written.add()
            frames.append(dataclasses.replace(result, planes=()))
        encoder.finish()
        codec = encoder.codec
        peak = max(pool.worker_peak_bytes, measure_peak)
    stored = clip_io.with_display_orientation(encoded, variant_path, orientation, container=container)
    if turned:
        _remove(encoded)
    size = stored.stat().st_size
    write = _Stage(progress, "write", "bytes", size)
    write.add(size)
    check = clip_io.check_stored_clip(stored, original_path, probe, audio_hashes=[a["sha256"] for a in facts["audioStreams"]],
                                      display_orientation=orientation)
    if not check.passed:
        if check.rejection is None:
            raise clip_io.ClipError("internal")
        measured = _clip_measured(clip, settings, probe, frames, [], max(peak, clip_io.peak_memory().self_bytes))
        return recorder.rejected(check.rejection, measured)
    # The gate on the stored file, each frame against the original's own pixels, rebuilt in the pool
    verify = _Stage(progress, "verify", "frames", count)
    stored_probe = clip_io.probe_clip(stored)
    gated: list[clip_frames.FrameGate] = []
    with clip_io.ClipDecoder(original_path, probe) as originals, clip_io.ClipDecoder(stored, stored_probe) as kept, \
            frame_pool.FramePool(functools.partial(clip_frames.gate_run, settings), workers=workers) as pool:
        # Each frame's own stars as the restore pass found them on the same original frame (restore-7s): the gate takes
        # them only for the luma they were found on, and finds them itself otherwise
        own_stars = {f.index: f.own_stars for f in frames}
        pairs = ((a.planes, b.planes, own_stars.get(a.index)) for a, b in zip(originals, kept))
        for result in pool.map(frame_pool.runs(pairs, window=clip_frames.RUN_WINDOW)):
            gated.append(result)
            verify.add()
        peak = max(peak, pool.worker_peak_bytes)
    if len(gated) != count:
        raise clip_io.ClipError("frame_times")
    gate_record = gate.clip_record([g.outcome for g in gated])
    measured = _clip_measured(clip, settings, probe, frames, gated, max(peak, clip_io.peak_memory().self_bytes))
    graph = {"id": recorder.graph_ref, "sourceSha256": recorder.sha256, "kind": "restoration",
             "pipelineVersion": variants.PIPELINE_VERSION, "request": request,
             "steps": _clip_steps(clip, settings, probe, container, codec)}
    ProcessingGraph.model_validate(graph)
    if _sha256(original_path) != recorder.sha256:
        return recorder.failed(CHANGED)
    if not gate_record["passed"]:
        rejection = "unconfirmed_points" if gate_record["unconfirmedPointCount"] else "star_flux_changed"
        return recorder.rejected(rejection, measured, gate_record)
    sha256 = recorder.sha256
    vid = variants.variant_id(sha256, "restoration", request)
    record = recorder.record("ready", variantId=vid, measured=measured, gate=gate_record)
    audio = [{**stream, "index": stored_probe.audio[k].index} for k, stream in enumerate(facts["audioStreams"])]
    variant = {
        "id": vid, "kind": "restoration", "sourceSha256": sha256, "sourceAssetId": f"src_{sha256[:16]}",
        "graphId": recorder.graph_ref, "attemptId": record["id"], "sha256": _file_sha256(stored),
        "uri": variant_uri(sha256, vid), "byteSize": size, "mimeType": facts["mimeType"], "width": probe.video.width,
        "height": probe.video.height, "durationMs": facts["durationMs"], "frameCount": count,
        "frameTimesSha256": frame_times_sha256(probe.time_base, probe.frame_times), "audioStreams": audio,
        "colour": clip.description(), "disclosure": DISCLOSURES["restoration"], "createdAtUTC": record["finishedAtUTC"],
    }
    if orientation is not None:
        variant["displayOrientation"] = orientation
    MediaVariant.model_validate(variant)
    variants.publish_variant(store_root, recorder.folder, None, {"variant": variant, "graph": graph, "attempt": record},
                             file_path=stored)
    variants.keep_attempt(recorder.folder, record)
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
