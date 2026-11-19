"""The variant store and the display copy (spec restoration-for-every-upload, design sections 9 and 13; REQ-RST-1,
REQ-RST-2), as plain functions. The upload job runner (spec upload-jobs, design 3.10) calls them from its job kinds;
until it exists, nothing in the product calls them.

The store, under an upload's folder `<root>/<aa>/<sha256>/`, beside its untouched original:

    variants/<variantId>/variant.png   0400    the copy, the file its checks ran on
                         variant.json  0400    MediaVariant
                         graph.json    0400    ProcessingGraph
                         attempt.json  0400    ProcessingAttempt, ready
    attempts/<attemptId>.json          0400    every finished attempt: ready, rejected or failed

Folders are 0700. A variant folder is assembled under the store root's `.partial/` and renamed into `variants/` whole,
onto a name that does not exist, so no reader sees a folder with some of its files and nothing is overwritten; an
attempt record is linked into place, which also fails when the name exists. Ids are content addresses (section 9):
`graph_<h>` and `var_<h>` with `h` the first 16 hex digits of the SHA-256 of the canonical JSON of {sourceSha256,
kind, pipelineVersion, request}, `att_<h2>` from the graph id and the attempt's start time.
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from services.contracts.ingest import format_utc
from services.contracts.media import DISCLOSURES, MediaVariant, ProcessingAttempt, ProcessingGraph, variant_uri
from services.media import decode, photo, png16

# Raised by any unit that changes pixels (design section 9)
PIPELINE_VERSION = "1.0.0"
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


def graph_hash(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: str = PIPELINE_VERSION) -> str:
    """P-RST-3: a function of the source hash, the request and the pipeline version only."""
    return _h16({"sourceSha256": source_sha256, "kind": kind, "pipelineVersion": pipeline_version, "request": request})


def graph_id(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: str = PIPELINE_VERSION) -> str:
    return "graph_" + graph_hash(source_sha256, kind, request, pipeline_version)


def variant_id(source_sha256: str, kind: str, request: dict[str, Any], pipeline_version: str = PIPELINE_VERSION) -> str:
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


@dataclass(frozen=True)
class StoredVariant:
    folder: Path
    variant: dict[str, Any]


def publish_variant(store_root: Path, source_folder: Path, file_bytes: bytes, records: dict[str, dict[str, Any]]) -> StoredVariant:
    """Assemble `variants/<variantId>/` under `<store_root>/.partial/` and rename it into place whole. `records` holds
    "variant", "graph" and "attempt"; the variant record's id names the folder. A variant of that id already in
    place is refused with FileExistsError, and the assembled folder is removed."""
    variant = records["variant"]
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
        _write_new(assembled / "variant.png", file_bytes)
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


def display_copy_graph(source_sha256: str, still: decode.DecodedStill) -> dict[str, Any]:
    """The two steps, decode and encode, each parameter with its origin and basis (REQ-RST-2.3, design section 13)."""
    request = {"kind": "display_copy"}
    bits = 16 if still.samples.dtype == np.uint16 else 8
    encode = [
        {"name": "container", "value": "png", "origin": "default", "basis": DEFAULT_BASIS_8},
        {"name": "bitDepth", "value": bits, "origin": "measured", "basis": FROM_ORIGINAL},
        {"name": "pixelFormat", "value": "rgb48le" if bits == 16 else "rgb24", "origin": "measured", "basis": FROM_ORIGINAL},
        {"name": "zlibLevel", "value": ZLIB_LEVEL, "origin": "default", "basis": DEFAULT_BASIS_8},
    ]
    graph = {
        "id": graph_id(source_sha256, "display_copy", request),
        "sourceSha256": source_sha256,
        "kind": "display_copy",
        "pipelineVersion": PIPELINE_VERSION,
        "request": request,
        "steps": [{"kind": "decode", "version": STEP_VERSION, "parameters": still.parameters},
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
        graph = display_copy_graph(source_sha256, still)
        data = encode_png(still)
        verified = verify_copy(data, still.samples)
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
        "height": height, "durationMs": 0, "frameCount": 1, "audioStreams": [], "colour": still.colour,
        "disclosure": DISCLOSURES["display_copy"], "createdAtUTC": record["finishedAtUTC"],
    }
    if display_orientation is not None:
        variant["displayOrientation"] = display_orientation
    MediaVariant.model_validate(variant)
    stored = publish_variant(store_root, source_folder, data, {"variant": variant, "graph": graph, "attempt": record})
    keep_attempt(source_folder, record)
    return DisplayCopy("ready", record, graph, variant, stored.folder)


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

# A variant's export name by its type: a still is exported as the PNG that is the variant (REQ-RST-9.5). A clip's
# rows come with its stored file (restore-7) [open: until then a clip has no export]
EXPORT_EXTENSIONS = {"image/png": "png"}


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


# A stored zip [verified: PKWARE APPNOTE.TXT 6.3, sections 4.3.7, 4.3.12 and 4.3.16; the writer is tested to write
# what Python's zipfile writes for the same entries]: each entry's local header with its CRC-32 and sizes, so no data
# descriptor follows it, then the central directory and its end record. No zip64: an entry or offset past 4 GiB is
# refused [open: a long clip's export].
_LOCAL = struct.Struct("<IHHHHHIIIHH")
_CENTRAL = struct.Struct("<IHHHHHHIIIHHHHHII")
_END = struct.Struct("<IHHHHIIH")
_ZIP_VERSION = 20  # 2.0, the version Python's zipfile writes for a stored entry
_MADE_BY_UNIX = 3 << 8
# Readable by whoever unpacks it, whatever the store's own 0400
_EXTERNAL_ATTR = 0o100644 << 16
_ZIP32_LIMIT = 0xFFFFFFFF


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


def _local_header(entry: ZipEntry, dos: tuple[int, int]) -> bytes:
    name = entry.name.encode("ascii")
    return _LOCAL.pack(0x04034B50, _ZIP_VERSION, 0, 0, dos[0], dos[1], entry.crc32, entry.size, entry.size, len(name), 0) + name


def stored_zip_size(entries: list[ZipEntry]) -> int:
    """The zip's length in bytes, known before a byte of it is written."""
    names = sum(len(entry.name.encode("ascii")) for entry in entries)
    return (sum(_LOCAL.size + entry.size + _CENTRAL.size for entry in entries) + 2 * names + _END.size)


def stored_zip(entries: list[ZipEntry], date_time: tuple[int, int, int, int, int, int]) -> Any:
    """The zip's bytes, block by block: an entry's bytes are read as they are sent, never held whole. An entry whose
    blocks do not add up to its size stops the zip before its central directory, so no reader takes it as whole."""
    if stored_zip_size(entries) > _ZIP32_LIMIT or len(entries) > 0xFFFF:
        raise ValueError("an export past 4 GiB needs zip64, which this writer does not write")
    dos, offset, central = _dos(date_time), 0, []
    for entry in entries:
        header = _local_header(entry, dos)
        name = entry.name.encode("ascii")
        central.append(_CENTRAL.pack(0x02014B50, _MADE_BY_UNIX | _ZIP_VERSION, _ZIP_VERSION, 0, 0, dos[0], dos[1],
                                     entry.crc32, entry.size, entry.size, len(name), 0, 0, 0, 0, _EXTERNAL_ATTR, offset) + name)
        yield header
        sent, crc = 0, 0
        for block in entry.blocks():
            sent, crc = sent + len(block), zlib.crc32(block, crc)
            yield block
        if sent != entry.size or crc & 0xFFFFFFFF != entry.crc32:
            raise ValueError(f"{entry.name} changed while it was sent")
        offset += len(header) + entry.size
    directory = b"".join(central)
    yield directory + _END.pack(0x06054B50, 0, 0, len(entries), len(entries), len(directory), offset, 0)
