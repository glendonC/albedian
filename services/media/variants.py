"""The variant store and the display copy (spec restoration-for-every-upload, design sections 9 and 13; REQ-RST-1,
REQ-RST-2), as plain functions. The upload job runner (spec upload-jobs, design 3.10) calls them from its job kinds;
until it exists, nothing in the product calls them.

The store, under an upload's folder `<root>/<aa>/<sha256>/`, beside its untouched original:

    variants/<variantId>/variant.png   0400    the copy, the file its checks ran on (a clip's: variant.mov or .mp4)
                         variant.json  0400    MediaVariant
                         graph.json    0400    ProcessingGraph
                         attempt.json  0400    ProcessingAttempt, ready
    attempts/<attemptId>.json          0400    every finished attempt: ready, rejected or failed

Folders are 0700. A variant folder is assembled under the store root's `.partial/` and renamed into `variants/` whole,
onto a name that does not exist, so no reader sees a folder with some of its files and nothing is overwritten; an
attempt record is linked into place, which also fails when the name exists. Ids are content addresses (section 9):
`graph_<h>` and `var_<h>` with `h` the first 16 hex digits of the SHA-256 of the canonical JSON of {sourceSha256,
kind, pipelineVersion, request}, `att_<h2>` from the graph id and the attempt's start time. The pipeline version
is named by the restoration producer's revision, so a revision raise gives a rerun new ids and its variant is stored
beside the older one, which is kept as it is.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import time
import zlib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageCms

from services.contracts.ingest import format_utc
from services.contracts.media import (
    ASSUMPTION_REASONS, CONVERTED_DISCLOSURES, DISCLOSURES, RENDERING_INTENTS, MediaVariant, ProcessingAttempt,
    ProcessingGraph, variant_uri,
)
from services.media import colour as colour_math, decode, photo, png16

# Raised by any unit that changes pixels (design section 9): 1.<revision>.0 of the restoration producer
# (services/media/jobs.py MEDIA_PRODUCER, held to it by tests/media/test_jobs.py), so the unit that raises the
# revision raises this too. Every graph and variant id hashes it: under one version for every revision, a photo
# restored under revision 2 could not be restored again under 3, since the write refuses an id it holds (FIELD's
# finding of 2026-11-19). Revisions 1 and 2 stored their variants under 1.0.0.
PIPELINE_VERSION = "1.18.0"
# The version of each step this module writes [proposed: a step's own version, raised when what it does changes]
STEP_VERSION = "1.0.0"
# [decided 2026-11-11: REQ-RST-1.3, private files 0400 and folders 0700, matching services/ingest/store.py]
FILE_MODE, FOLDER_MODE = 0o400, 0o700
# The 8 bit copy is written by Pillow at zlib level 6, the level png16.py uses [proposed in design section 8]
ZLIB_LEVEL = png16.ZLIB_LEVEL
DEFAULT_BASIS_8 = "Proposed in design section 8."
FROM_ORIGINAL = "Read from the decoded original."


def canonical_json(value: Any) -> bytes:
    """Keys sorted, no spaces, UTF-8: the same value is the same bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _h16(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()[:16]


def graph_hash(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: Optional[str] = None) -> str:
    """P-RST-3: a function of the source hash, the request and the pipeline version only (PIPELINE_VERSION as it is
    when called, by default)."""
    version = PIPELINE_VERSION if pipeline_version is None else pipeline_version
    return _h16({"sourceSha256": source_sha256, "kind": kind, "pipelineVersion": version, "request": request})


def graph_id(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: Optional[str] = None) -> str:
    return "graph_" + graph_hash(source_sha256, kind, request, pipeline_version)


def variant_id(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: Optional[str] = None) -> str:
    return "var_" + graph_hash(source_sha256, kind, request, pipeline_version)


def attempt_id(graph: str, started_at_utc: str) -> str:
    return "att_" + _h16({"graphId": graph, "startedAtUTC": started_at_utc})


# =============================================================================================
# The store
# =============================================================================================


def _private_folder(folder: Path) -> None:
    folder.mkdir(exist_ok=True)
    os.chmod(folder, FOLDER_MODE)


def _fsync_folder(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> str:
    """Write a file that must not exist yet, fsync it, make it 0400; its SHA-256."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, FILE_MODE)
    return hashlib.sha256(data).hexdigest()


def _record_bytes(record: dict[str, Any]) -> bytes:
    return json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


# A variant's file is named by its type: a still's 16 bit PNG, a clip in its original's own container (restore-7)
VARIANT_FILES = {"image/png": "variant.png", "video/quicktime": "variant.mov", "video/mp4": "variant.mp4"}


# A restored clip's playback copy, beside its lossless file in the variant's folder, and its record beside the variant's
# (clip-display-copy; DECIDED-BY-LEAD 22:24 on 24 Nov: a stored record is never rewritten, one path for new and stored
# restorations)
PLAYBACK_FILE_NAME = "playback.mp4"
PLAYBACK_RECORD_NAME = "playback.json"
NAMES_ITS_COPY = "a variant's record never names its playback copy; the copy's record is its own file"
NOT_ITS_FILE = "a playback copy is the file its record names"


def variant_file_name(mime_type: str) -> str:
    """The name of a variant's file in its folder, from the variant record's `mimeType`."""
    return VARIANT_FILES[mime_type]


def _move_new(source: Path, path: Path) -> str:
    """Move a finished file onto a name that must not exist yet, on the same file system, fsync it, make it 0400; its
    SHA-256. The file is read once, so a clip of a few hundred MB is never held whole."""
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
        os.fsync(handle.fileno())
    if os.path.lexists(path):
        raise FileExistsError(path.name)
    os.rename(source, path)
    os.chmod(path, FILE_MODE)
    return digest.hexdigest()


@dataclass(frozen=True)
class StoredVariant:
    folder: Path
    variant: dict[str, Any]


@dataclass(frozen=True)
class PlaybackFile:
    """A finished playback copy under the store root, and its `playbackCopy` record."""
    record: dict[str, Any]
    path: Path


def place_playback(folder: Path, variant: dict[str, Any], playback: PlaybackFile) -> None:
    """Put a restored clip's playback copy beside its lossless file in the variant's folder: the one writer of a copy,
    for a variant being assembled and for one already published (a copy made after its restoration was stored). The
    record must pass the contract composed with its variant, and the file must be the one it names; the file is moved
    in, then its record written, each 0400 onto a name that must not exist yet, so a record is never there before its
    file. Anything refused leaves the folder as it was."""
    MediaVariant.model_validate({**variant, "playbackCopy": playback.record})
    stored = folder / PLAYBACK_FILE_NAME
    digest = _move_new(playback.path, stored)
    try:
        if (digest, stored.stat().st_size) != (playback.record["sha256"], playback.record["byteSize"]):
            raise ValueError(NOT_ITS_FILE)
        _write_new(folder / PLAYBACK_RECORD_NAME, _record_bytes(playback.record))
    except BaseException:
        stored.unlink()
        raise
    _fsync_folder(folder)


def publish_variant(store_root: Path, source_folder: Path, file_bytes: Optional[bytes], records: dict[str, dict[str, Any]],
                    *, file_path: Optional[Path] = None, playback: Optional[PlaybackFile] = None) -> StoredVariant:
    """Assemble `variants/<variantId>/` under `<store_root>/.partial/` and rename it into place whole. `records` holds
    "variant", "graph" and "attempt"; the variant record's id names the folder, and its type names the file
    (`variant_file_name`). The file is `file_bytes`, or the finished file at `file_path` under the store root, moved in
    (a clip), and a restored clip's playback copy beside it (`place_playback`), so a new restoration and its copy
    appear together. A variant of that id already in place is refused with FileExistsError, and the assembled folder
    is removed, with the moved files in it."""
    variant = records["variant"]
    if "playbackCopy" in variant:
        raise ValueError(NAMES_ITS_COPY)
    name = variant["id"]
    variants = source_folder / "variants"
    _private_folder(variants)
    target = variants / name
    partial_root = store_root / ".partial"
    _private_folder(partial_root)
    staging = partial_root / f"variant-{secrets.token_hex(8)}"
    staging.mkdir(mode=FOLDER_MODE)
    try:
        assembled = staging / name
        assembled.mkdir(mode=FOLDER_MODE)
        stored_file = assembled / variant_file_name(variant["mimeType"])
        if (file_path is None) == (file_bytes is None):
            raise ValueError("a variant is published from its bytes or from a file, one of the two")
        if file_path is None:
            _write_new(stored_file, file_bytes)
        else:
            _move_new(file_path, stored_file)
        if playback is not None:
            # A copy's failure never fails a restoration (DECIDED-BY-LEAD 04:47 on 25 Nov): a copy that cannot be
            # placed is left out, `place_playback` having taken out what it moved, and the variant is published
            # without it
            try:
                place_playback(assembled, variant, playback)
            except Exception:
                pass
        for key in ("variant", "graph", "attempt"):
            _write_new(assembled / f"{key}.json", _record_bytes(records[key]))
        _fsync_folder(assembled)
        # rename(2) replaces an empty folder; nothing here makes one, and a whole variant folder in place makes the
        # rename fail, so the check below keeps the rule "never onto a name that exists" for both
        if os.path.lexists(target):
            raise FileExistsError(name)
        os.rename(assembled, target)
        _fsync_folder(variants)
    finally:
        # Empty after the rename; after a failure it holds what was assembled, removed here (a 0400 file goes with its
        # 0700 folder)
        shutil.rmtree(staging)
    return StoredVariant(target, variant)


def keep_attempt(source_folder: Path, attempt: dict[str, Any]) -> Path:
    """`attempts/<attemptId>.json`, written once: an attempt of that id already kept is refused (FileExistsError)."""
    attempts = source_folder / "attempts"
    _private_folder(attempts)
    path = attempts / f"{attempt['id']}.json"
    scratch = attempts / f".{attempt['id']}-{secrets.token_hex(8)}.tmp"
    _write_new(scratch, _record_bytes(attempt))
    try:
        os.link(scratch, path)
    finally:
        scratch.unlink()
    _fsync_folder(attempts)
    return path


# =============================================================================================
# The display copy (design section 13)
# =============================================================================================


def encode_png(still: decode.DecodedStill) -> bytes:
    """The copy's PNG: 8 bits by Pillow for an 8 bit original, 16 by png16.py for a deeper one, each with the
    original's ICC colour data byte for byte and none of its text (`photo.profile_without_text`, restore-8b), no time
    and no text chunk (design section 8)."""
    profile = photo.profile_without_text(still.icc_profile)
    if still.samples.dtype == np.uint16:
        return png16.write_png16(still.samples, profile)
    out = io.BytesIO()
    options = {"compress_level": ZLIB_LEVEL}
    if profile:
        options["icc_profile"] = profile
    Image.fromarray(still.samples, "RGB").save(out, "PNG", **options)
    return out.getvalue()


def display_copy_graph(source_sha256: str, still: decode.DecodedStill, intent: Optional[str] = None) -> dict[str, Any]:
    """The two steps, decode and encode, each parameter with its origin and basis (REQ-RST-2.3, design section 13);
    with `intent`, a conversion to sRGB through the photo's own profile between them, whose copy is 8 bit (restore-8e)."""
    request = {"kind": "display_copy"}
    bits = 16 if still.samples.dtype == np.uint16 and intent is None else 8
    basis = FROM_ORIGINAL if intent is None or still.samples.dtype == np.uint8 else DEFAULT_BASIS_8
    origin = "measured" if basis == FROM_ORIGINAL else "default"
    encode = [
        {"name": "container", "value": "png", "origin": "default", "basis": DEFAULT_BASIS_8},
        {"name": "bitDepth", "value": bits, "origin": origin, "basis": basis},
        {"name": "pixelFormat", "value": "rgb48le" if bits == 16 else "rgb24", "origin": origin, "basis": basis},
        {"name": "zlibLevel", "value": ZLIB_LEVEL, "origin": "default", "basis": DEFAULT_BASIS_8},
    ]
    convert = [{"kind": "convert_colour", "version": STEP_VERSION, "parameters": [
        {"name": "renderingIntent", "value": intent, "origin": "measured", "basis": decode.MEASURED_FROM_ICC}]}] if intent else []
    graph = {
        "id": graph_id(source_sha256, "display_copy", request),
        "sourceSha256": source_sha256,
        "kind": "display_copy",
        "pipelineVersion": PIPELINE_VERSION,
        "request": request,
        "steps": [{"kind": "decode", "version": STEP_VERSION, "parameters": still.parameters}, *convert,
                  {"kind": "encode", "version": STEP_VERSION, "parameters": encode}],
    }
    ProcessingGraph.model_validate(graph)
    return graph


@dataclass(frozen=True)
class DisplayCopy:
    status: str  # ready, rejected or failed
    attempt: dict[str, Any]
    graph: Optional[dict[str, Any]] = None
    variant: Optional[dict[str, Any]] = None
    folder: Optional[Path] = None


def make_display_copy(store_root: Path, source_folder: Path, original: Path, *, source_sha256: str, job_id: str,
                      display_orientation: Optional[dict[str, Any]] = None, now: Any = None) -> DisplayCopy:
    """Decode, encode, verify and store a display copy of a still (REQ-RST-2.1 to 2.4). The written PNG is decoded
    and compared sample by sample with the decoded original: equal, or the attempt is `rejected` with `copy_differs`
    and no file is kept. A still this pipeline will not decode ends `failed` with the decoder's fixed sentence. Every
    finished attempt is kept under `attempts/`. `now` gives the UTC instants (for tests); the system clock otherwise."""
    clock = now or (lambda: datetime.now().astimezone())
    started = format_utc(clock())
    wall, cpu = time.monotonic(), time.process_time()
    request = {"kind": "display_copy"}
    graph_ref = graph_id(source_sha256, "display_copy", request)
    att = attempt_id(graph_ref, started)

    def attempt(status: str, **fields: Any) -> dict[str, Any]:
        record = {"id": att, "sourceSha256": source_sha256, "graphId": graph_ref, "jobId": job_id, "status": status,
                  "startedAtUTC": started, "finishedAtUTC": format_utc(clock()),
                  "elapsedMs": round(1000 * (time.monotonic() - wall)), "cpuMs": round(1000 * (time.process_time() - cpu)), **fields}
        ProcessingAttempt.model_validate(record)
        return record

    try:
        still = decode.decode_still(original)
        # The record names the profile the copy holds (restore-8d); a profile the rebuild cannot carry is converted
        # through, or, where nothing reads it, left off with its assumption recorded (restore-8e, review row C20)
        colour, copy, intent, disclosure = _copy_of(still)
        graph = display_copy_graph(source_sha256, still, intent)
        data = encode_png(copy)
        verified = verify_copy(data, copy.samples)
    except decode.DecodeRefusal as refusal:
        record = attempt("failed", failureReason=refusal.sentence)
        keep_attempt(source_folder, record)
        return DisplayCopy("failed", record)
    measured = {"colour": still.source_colour, "notes": []}
    if not verified:
        record = attempt("rejected", measured=measured, rejection="copy_differs")
        keep_attempt(source_folder, record)
        return DisplayCopy("rejected", record, graph)
    vid = variant_id(source_sha256, "display_copy", request)
    height, width = still.samples.shape[:2]
    # The variant comes to be when its attempt finishes: the two records are written together, just below
    record = attempt("ready", variantId=vid, measured=measured)
    variant = {
        "id": vid, "kind": "display_copy", "sourceSha256": source_sha256, "sourceAssetId": f"src_{source_sha256[:16]}",
        "graphId": graph_ref, "attemptId": att, "sha256": hashlib.sha256(data).hexdigest(),
        "uri": variant_uri(source_sha256, vid), "byteSize": len(data), "mimeType": "image/png", "width": width,
        "height": height, "durationMs": 0, "frameCount": 1, "audioStreams": [], "colour": colour,
        "disclosure": disclosure, "createdAtUTC": record["finishedAtUTC"],
    }
    if display_orientation is not None:
        variant["displayOrientation"] = display_orientation
    MediaVariant.model_validate(variant)
    stored = publish_variant(store_root, source_folder, data, {"variant": variant, "graph": graph, "attempt": record})
    keep_attempt(source_folder, record)
    return DisplayCopy("ready", record, graph, variant, stored.folder)


# Little CMS's own sRGB, rebuilt as every stored profile is: the profile of a converted copy (restore-8e)
SRGB_PROFILE = photo.profile_without_text(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
# The assumption of a copy kept without a profile nothing reads (review row C20, decided by the lead on 2026-11-19)
UNREAD_PROFILE_REASON = "The file's ICC profile could not be read, so sRGB is assumed."
UNREAD_PROFILE_ASSUMPTIONS = [entry for entry in ASSUMPTION_REASONS if entry["reason"] == UNREAD_PROFILE_REASON]


def convert_through_profile(still: decode.DecodedStill) -> Optional[tuple[np.ndarray, str]]:
    """The photo's pixels converted to sRGB through its own whole profile by Little CMS at the profile's own rendering
    intent, 8 bit (Pillow's Little CMS converts 8 bit R'G'B' only [tested: Pillow 13.0.0, Little CMS 2.19]; a 16 bit
    photo is rounded to 8 bits first), and the intent's name. None when Little CMS cannot read the profile or convert
    through it."""
    profile = still.icc_profile
    if not profile or len(profile) < 68 or still.samples.ndim != 3:
        return None
    code = int.from_bytes(profile[64:68], "big")
    intent = code if code < len(RENDERING_INTENTS) else 0
    samples = still.samples
    if samples.dtype == np.uint16:
        samples = ((samples.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    try:
        transform = ImageCms.buildTransform(ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                            ImageCms.ImageCmsProfile(io.BytesIO(SRGB_PROFILE)), "RGB", "RGB",
                                            renderingIntent=intent)
        out = ImageCms.applyTransform(Image.fromarray(samples, "RGB"), transform)
    except (ImageCms.PyCMSError, OSError, ValueError):
        return None
    return np.asarray(out, dtype=np.uint8), RENDERING_INTENTS[intent]


def _copy_of(still: decode.DecodedStill) -> tuple[dict[str, Any], decode.DecodedStill, Optional[str], str]:
    """The display copy's colour description, the still it is encoded from, the conversion's intent (None when not
    converted) and its disclosure: the original's samples with the rebuilt profile; where the rebuild cannot carry the
    profile, the samples converted through it to sRGB (restore-8e, option 2); where nothing reads it, the samples kept
    without a profile, the copy described as sRGB with that assumption recorded (option 3)."""
    colour = photo.stored_colour(still.colour, still.icc_profile)
    if colour is not None:
        return colour, still, None, DISCLOSURES["display_copy"]
    others = [entry for entry in still.colour.get("assumptions", []) if entry["field"] not in ("transfer", "primaries")]
    plain = {key: value for key, value in still.colour.items() if key != "iccProfileSha256"}
    converted = convert_through_profile(still)
    if converted is None:
        colour = {**plain, "transfer": "srgb", "primaries": "bt709", "assumptions": others + UNREAD_PROFILE_ASSUMPTIONS}
        return colour, replace(still, icc_profile=None), None, DISCLOSURES["display_copy"]
    samples, intent = converted
    kind, _ = colour_math.icc_tone_curves(SRGB_PROFILE)
    colour = {**plain, "bitDepth": 8, "transfer": kind, "primaries": "icc", "assumptions": others,
              "iccProfileSha256": hashlib.sha256(SRGB_PROFILE).hexdigest()}
    disclosure = CONVERTED_DISCLOSURES[1 if still.samples.dtype == np.uint16 else 0]
    return colour, replace(still, samples=samples, icc_profile=SRGB_PROFILE), intent, disclosure


def png_intact(data: bytes) -> bool:
    """Whether the bytes are a whole PNG: the signature, IHDR first, every chunk whole with its CRC right, one IEND and last,
    and the IDAT stream one complete zlib stream with its checksum [verified: PNG specification, chunk layout; zlib's
    own check of its Adler-32]. Pillow decodes a copy cut short by its last 20 bytes to the right samples [tested],
    so equal samples alone do not show the file is whole."""
    if not data.startswith(png16.SIGNATURE):
        return False
    position, stream, kinds = len(png16.SIGNATURE), zlib.decompressobj(), []
    while position + 12 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        kind, body = data[position + 4:position + 8], data[position + 8:position + 8 + length]
        crc = data[position + 8 + length:position + 12 + length]
        if len(body) != length or len(crc) != 4 or int.from_bytes(crc, "big") != zlib.crc32(kind + body) & 0xFFFFFFFF:
            return False
        if kind == b"IDAT":
            try:
                stream.decompress(body)
            except zlib.error:
                return False
        kinds.append(kind)
        position += 12 + length
    return (position == len(data) and kinds[:1] == [b"IHDR"] and kinds[-1:] == [b"IEND"] and kinds.count(b"IEND") == 1
            and stream.eof and not stream.unused_data)


def verify_copy(data: bytes, samples: np.ndarray) -> bool:
    """Whether the written PNG is whole and decodes, through the readers the original went through, to exactly
    `samples`. A tool timeout or internal failure in the reader raises `DecodeRefusal` instead of reporting
    `copy_differs`."""
    if not png_intact(data):
        return False
    try:
        written = decode.read_png(data)
    except decode.DecodeRefusal as refusal:
        if refusal.sentence != decode.UNDECODABLE:
            raise
        return False
    except (OSError, ValueError):
        return False
    return written.shape == samples.shape and written.dtype == samples.dtype and np.array_equal(written, samples)


# =============================================================================================
# The export and its manifest (design section 12; REQ-RST-9; review row D22)
# =============================================================================================

import struct  # noqa: E402

from services.contracts.media import EXPORT_MANIFEST_FORMAT, EXPORT_MANIFEST_FORMAT_VERSION, ExportManifest  # noqa: E402

# A variant's export name by its type: a still is exported as the PNG that is the variant (REQ-RST-9.5), a clip as
# the file restore-7 stored, losslessly, in its own container (restore-8c's second half)
EXPORT_EXTENSIONS = {"image/png": "png", "video/quicktime": "mov", "video/mp4": "mp4"}


def export_manifest(variant: dict[str, Any], graph: dict[str, Any], attempt: dict[str, Any],
                    solution_id: Optional[str]) -> dict[str, Any]:
    """The manifest of a stored variant (REQ-RST-9.2), built from its three stored records and the solution id the
    job store names for its upload at this moment: the same stored state gives the same manifest, with no clock of
    its own. It holds what the three records hold, which is no title, file name, capture time, place or path
    (P-RST-19). Raises ValidationError when the contract refuses it, a manifest of an attempt not `ready` among them."""
    manifest: dict[str, Any] = {
        "format": EXPORT_MANIFEST_FORMAT, "formatVersion": EXPORT_MANIFEST_FORMAT_VERSION,
        "sourceSha256": graph["sourceSha256"], "variant": variant, "graph": graph, "attempt": attempt,
    }
    if solution_id is not None:
        manifest["observationSolutionId"] = solution_id
    manifest["disclosure"] = variant["disclosure"]
    ExportManifest.model_validate(manifest)
    return manifest


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """The sidecar's bytes: pretty printed, UTF-8, one newline at the end, as the stored records are written."""
    return _record_bytes(manifest)


# A stored zip [inference: the layout of PKWARE's APPNOTE.TXT as recalled, not read again for this unit; tested: the
# writer writes what Python's zipfile writes for the same entries, below its zip64 limit and above a lowered one]:
# each entry's local header with its CRC-32 and sizes, so no data descriptor follows it, then the central directory
# and its end record; an entry, offset or directory past the limit is written as zip64, as zipfile writes it
_LOCAL = struct.Struct("<IHHHHHIIIHH")
_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_END = struct.Struct("<IHHHHIIH")
_ZIP64_END = struct.Struct("<IQHHIIQQQQ")
_ZIP64_LOCATOR = struct.Struct("<IIQI")
_ZIP_VERSION = 20  # 2.0, a stored entry's version [tested: equal to CPython zipfile's bytes, test_export]
_ZIP64_VERSION = 45  # 4.5, for zip64 entries and end records [tested: equal to CPython zipfile's bytes, test_export]
_MADE_BY_UNIX = 3 << 8  # made by Unix, system 3 [tested: equal to CPython zipfile's bytes, test_export]
# Readable by whoever unpacks it, whatever the store's own 0400 [proposed; tested: test_export reads 0o100644]
_EXTERNAL_ATTR = 0o100644 << 16
# Past 2 GiB less one byte an entry, an offset or the directory is zip64, as Python's zipfile writes it, since some
# readers take the 32 bit fields as signed [verified: CPython's zipfile ZIP64_LIMIT, 3.12 and 3.14, read for this unit]
ZIP64_LIMIT = (1 << 31) - 1


@dataclass(frozen=True)
class ZipEntry:
    """One file of a stored zip: its name in the archive, its size and CRC-32, and its bytes in blocks."""

    name: str
    size: int
    crc32: int
    blocks: Any  # a callable answering an iterator of bytes


def zip_date_time(utc_instant: str) -> tuple[int, int, int, int, int, int]:
    """An entry's time: the variant's own UTC instant (its `createdAtUTC`), at the zip format's two second steps."""
    moment = datetime.strptime(utc_instant[:19], "%Y-%m-%dT%H:%M:%S")
    return (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second - moment.second % 2)


def _dos(date_time: tuple[int, int, int, int, int, int]) -> tuple[int, int]:
    year, month, day, hour, minute, second = date_time
    return hour << 11 | minute << 5 | second // 2, (year - 1980) << 9 | month << 5 | day


def _plan(entries: list[ZipEntry], date_time: tuple[int, int, int, int, int, int], limit: int) -> tuple[list[bytes], bytes]:
    """Each entry's local header, and the central directory with its end records: everything but the entries' bytes,
    known from their names, sizes and CRC-32s before a byte is sent."""
    dos, offset, headers, central = _dos(date_time), 0, [], []
    for entry in entries:
        name = entry.name.encode("ascii")
        big, far = entry.size > limit, offset > limit
        size = 0xFFFFFFFF if big else entry.size
        local_extra = struct.pack("<HHQQ", 1, 16, entry.size, entry.size) if big else b""
        version = _ZIP64_VERSION if big else _ZIP_VERSION
        headers.append(_LOCAL.pack(0x04034B50, version, 0, 0, dos[0], dos[1], entry.crc32, size, size, len(name),
                                   len(local_extra)) + name + local_extra)
        fields = ([entry.size, entry.size] if big else []) + ([offset] if far else [])
        extra = struct.pack("<HH" + "Q" * len(fields), 1, 8 * len(fields), *fields) if fields else b""
        version = _ZIP64_VERSION if fields else _ZIP_VERSION
        central.append(_CENTRAL.pack(0x02014B50, _MADE_BY_UNIX | version, version, 0, 0, dos[0], dos[1], entry.crc32, size,
                                     size, len(name), len(extra), 0, 0, 0, _EXTERNAL_ATTR,
                                     0xFFFFFFFF if far else offset) + name + extra)
        offset += len(headers[-1]) + entry.size
    directory = b"".join(central)
    count, length, at, tail = len(entries), len(directory), offset, b""
    if count > 0xFFFF or at > limit or length > limit:
        tail = (_ZIP64_END.pack(0x06064B50, _ZIP64_END.size - 12, _ZIP64_VERSION, _ZIP64_VERSION, 0, 0, count, count,
                                length, at)
                + _ZIP64_LOCATOR.pack(0x07064B50, 0, at + length, 1))
        count, length, at = min(count, 0xFFFF), min(length, 0xFFFFFFFF), min(at, 0xFFFFFFFF)
    return headers, directory + tail + _END.pack(0x06054B50, 0, 0, count, count, length, at, 0)


def stored_zip_size(entries: list[ZipEntry], zip64_limit: int = ZIP64_LIMIT) -> int:
    """The zip's length in bytes, known before a byte of it is written."""
    headers, tail = _plan(entries, (1980, 1, 1, 0, 0, 0), zip64_limit)
    return sum(len(header) + entry.size for header, entry in zip(headers, entries)) + len(tail)


def stored_zip(entries: list[ZipEntry], date_time: tuple[int, int, int, int, int, int], zip64_limit: int = ZIP64_LIMIT) -> Any:
    """The zip's bytes, block by block: an entry's bytes are read as they are sent, never held whole. An entry whose
    blocks do not add up to its size stops the zip before its central directory, so no reader takes it as whole."""
    headers, tail = _plan(entries, date_time, zip64_limit)
    for entry, header in zip(entries, headers):
        yield header
        sent, crc = 0, 0
        for block in entry.blocks():
            sent, crc = sent + len(block), zlib.crc32(block, crc)
            yield block
        if sent != entry.size or crc & 0xFFFFFFFF != entry.crc32:
            raise ValueError(f"{entry.name} changed while it was sent")
    yield tail
