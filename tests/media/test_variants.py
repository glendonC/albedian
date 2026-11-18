"""The variant store and the display copy (`services/media/variants.py`; design sections 9 and 13; REQ-RST-1, 2).

Covers P-RST-1 (the original's bytes and mode, and nothing under its folder but `variants/` and `attempts/`, change),
P-RST-3 (the graph id is a function of the source hash, the request and the pipeline version only), P-RST-4 (a
display copy of any 8 or 16 bit RGB PNG or TIFF decodes to exactly the decoded original; one changed sample fails its
verification), the store's modes and its refusal to overwrite, the rejected and failed attempts, a HEIC's copy, and
the five design photos.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from PIL import Image

from services.contracts.media import DISCLOSURES, MEDIA_ID_PATTERNS, MediaVariant, ProcessingAttempt, ProcessingGraph
from services.media import decode, png16, variants

# Every test here starts ffmpeg or ffprobe: each has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]
MEDIA_FIXTURES = ROOT / "tests" / "fixtures" / "media"
JOB_ID = "job_" + "0123456789abcdef" * 2
PHOTOS = tuple(f".cache/real-photos/rp-b-0{k}/rp-b-0{k}.jpg" for k in range(1, 6))


def fixed_clock():
    moments = iter(datetime(2031, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=250 * k) for k in range(1000))
    return lambda: next(moments)


def stored_original(tmp_path: Path, data: bytes, extension: str) -> tuple[Path, Path, Path, str]:
    """An upload as the ingest store keeps it: <root>/<aa>/<sha256>/original.<ext>, 0400 in 0700 folders."""
    sha = hashlib.sha256(data).hexdigest()
    root = tmp_path / "store"
    folder = root / sha[:2] / sha
    folder.mkdir(parents=True)
    for level in (root, root / sha[:2], folder):
        os.chmod(level, 0o700)
    original = folder / f"original.{extension}"
    original.write_bytes(data)
    os.chmod(original, 0o400)
    return root, folder, original, sha


def snapshot(folder: Path) -> dict[str, tuple[int, str]]:
    """Every file and folder under `folder`: its mode and, for a file, its SHA-256."""
    found = {}
    for path in sorted(folder.rglob("*")):
        mode = stat.S_IMODE(path.lstat().st_mode)
        found[str(path.relative_to(folder))] = (mode, hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "")
    return found


def png_bytes(rgb: np.ndarray, **options) -> bytes:
    if rgb.dtype == np.uint16:
        return png16.write_png16(rgb, options.get("icc_profile"))
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "PNG", **options)
    return out.getvalue()


def tiff8_bytes(rgb: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "TIFF")
    return out.getvalue()


def tiff16_bytes(tmp_path: Path, rgb: np.ndarray) -> bytes:
    import subprocess

    raw = tmp_path / "frame.raw"
    raw.write_bytes(rgb.astype("<u2").tobytes())
    out = tmp_path / "frame.tif"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s",
                    f"{rgb.shape[1]}x{rgb.shape[0]}", "-i", str(raw), "-frames:v", "1", str(out)], check=True, capture_output=True)
    data = out.read_bytes()
    raw.unlink()
    out.unlink()
    return data


# =============================================================================================
# Ids (P-RST-3)
# =============================================================================================

SHA_A, SHA_B = "a" * 64, "b" * 64


@settings(max_examples=60, deadline=None)
@given(sha=st.sampled_from([SHA_A, SHA_B]), exposure=st.one_of(st.none(), st.floats(0, 6)),
       version=st.sampled_from(["1.0.0", "1.0.1", "2.0.0"]))
def test_p_rst_3_the_graph_id_is_a_function_of_the_source_the_request_and_the_pipeline_version(sha, exposure, version) -> None:
    request = {"kind": "restoration"} if exposure is None else {"kind": "restoration", "exposureEv": exposure}
    gid = variants.graph_id(sha, "restoration", request, version)
    # The same inputs, written in another key order, give the same id
    assert variants.graph_id(sha, "restoration", dict(reversed(list(request.items()))), version) == gid
    # Each input changed alone changes it
    assert variants.graph_id(SHA_B if sha == SHA_A else SHA_A, "restoration", request, version) != gid
    assert variants.graph_id(sha, "restoration", {**request, "exposureEv": (exposure or 0) + 0.5}, version) != gid
    assert variants.graph_id(sha, "restoration", request, version + "1") != gid
    assert variants.graph_id(sha, "display_copy", {"kind": "display_copy"}, version) != gid
    # The variant's id carries the same hash, as section 9 says
    assert variants.variant_id(sha, "restoration", request, version)[4:] == gid[6:]


def test_ids_have_the_form_their_contract_patterns_give_and_the_hash_section_9_names() -> None:
    import re

    request = {"kind": "display_copy"}
    gid = variants.graph_id(SHA_A, "display_copy", request)
    expected = hashlib.sha256(json.dumps({"kind": "display_copy", "pipelineVersion": variants.PIPELINE_VERSION,
                                          "request": request, "sourceSha256": SHA_A}, separators=(",", ":")).encode()).hexdigest()[:16]
    assert gid == "graph_" + expected
    assert re.fullmatch(MEDIA_ID_PATTERNS["graph"], gid)
    assert re.fullmatch(MEDIA_ID_PATTERNS["variant"], variants.variant_id(SHA_A, "display_copy", request))
    att = variants.attempt_id(gid, "2031-01-01T00:00:00Z")
    assert re.fullmatch(MEDIA_ID_PATTERNS["attempt"], att)
    assert att != variants.attempt_id(gid, "2031-01-01T00:00:00.250Z")


# =============================================================================================
# The display copy, end to end (P-RST-1, P-RST-4)
# =============================================================================================


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(bits=st.sampled_from([8, 16]), container=st.sampled_from(["png", "tif"]), height=st.integers(1, 7),
       width=st.integers(1, 7), seed=st.integers(0, 2**32 - 1))
@example(bits=16, container="tif", height=1, width=1, seed=0)
@example(bits=8, container="png", height=7, width=3, seed=1)
def test_p_rst_1_and_4_a_copy_decodes_to_the_decoded_original_and_touches_nothing_else(tmp_path_factory, bits, container,
                                                                                        height, width, seed) -> None:
    tmp_path = tmp_path_factory.mktemp("copy")
    top = 65536 if bits == 16 else 256
    rgb = np.random.default_rng(seed).integers(0, top, size=(height, width, 3)).astype(np.uint16 if bits == 16 else np.uint8)
    if container == "png":
        data = png_bytes(rgb)
    else:
        data = tiff16_bytes(tmp_path, rgb) if bits == 16 else tiff8_bytes(rgb)
    root, folder, original, sha = stored_original(tmp_path, data, container)
    before = snapshot(folder)
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready"
    stored = (copy.folder / "variant.png").read_bytes()
    # P-RST-4: the stored copy decodes, by the original's own readers, to exactly the decoded original
    assert np.array_equal(decode.read_png(stored), decode.decode_still(original).samples)
    assert np.array_equal(decode.decode_still(original).samples, rgb)
    # P-RST-1: the original's bytes and mode, and everything outside variants/ and attempts/, as they were
    after = snapshot(folder)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == sha
    assert {k: v for k, v in after.items() if not k.startswith(("variants", "attempts"))} == before
    shutil.rmtree(tmp_path)


def changed_one_sample(data: bytes, bits: int) -> bytes:
    """The same PNG with one sample of its first row raised by one, every chunk's CRC kept right."""
    chunks, position = [], 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        chunks.append((data[position + 4:position + 8], data[position + 8:position + 8 + length]))
        position += 12 + length
    out = data[:8]
    for kind, body in chunks:
        if kind == b"IDAT":
            raw = bytearray(zlib.decompress(body))
            # Row 0 is filtered Up against zeros (or not filtered), so its bytes are the samples
            raw[1 + (2 if bits == 16 else 1) - 1] ^= 1
            body = zlib.compress(bytes(raw))
        out += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return out


@pytest.mark.parametrize("bits", [8, 16])
def test_p_rst_4_one_changed_sample_fails_the_verification(bits: int) -> None:
    rgb = np.random.default_rng(bits).integers(0, 2**bits, size=(3, 4, 3)).astype(np.uint16 if bits == 16 else np.uint8)
    data = png_bytes(rgb)
    assert variants.verify_copy(data, rgb)
    assert not variants.verify_copy(changed_one_sample(data, bits), rgb)
    assert not variants.verify_copy(data[:-20], rgb), "a cut file does not verify either"


def test_a_copy_that_differs_is_rejected_and_no_file_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rgb = np.random.default_rng(4).integers(0, 256, size=(4, 5, 3), dtype=np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")
    real = variants.encode_png
    monkeypatch.setattr(variants, "encode_png", lambda still: changed_one_sample(real(still), 8))
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "rejected" and copy.attempt["rejection"] == "copy_differs"
    assert not (folder / "variants").exists() or list((folder / "variants").iterdir()) == []
    kept = json.loads((folder / "attempts" / f"{copy.attempt['id']}.json").read_text())
    assert kept == copy.attempt and ProcessingAttempt.model_validate(kept).status == "rejected"


def test_a_still_the_pipeline_will_not_decode_fails_with_its_sentence_and_keeps_the_attempt(tmp_path: Path) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (3, 3), (1, 2, 3, 4)).save(out, "PNG")
    root, folder, original, sha = stored_original(tmp_path, out.getvalue(), "png")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "failed"
    assert copy.attempt["failureReason"] == "This kind of file is not supported yet: an image with an alpha plane."
    assert (folder / "attempts" / f"{copy.attempt['id']}.json").is_file()
    assert not (folder / "variants").exists()


@pytest.mark.parametrize("cause", [
    "The restoration stopped at its time limit.",
    "The restoration stopped on an internal error.",
])
def test_a_tool_timeout_or_signal_while_verifying_a_copy_fails_with_its_cause_not_copy_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    rgb = np.random.default_rng(14).integers(0, 65536, size=(3, 4, 3), dtype=np.uint16)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")

    def interrupted(_data: bytes) -> np.ndarray:
        raise decode.DecodeRefusal(cause)

    monkeypatch.setattr(decode, "read_png", interrupted)
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "failed" and copy.attempt["failureReason"] == cause
    assert "rejection" not in copy.attempt
    assert (folder / "attempts" / f"{copy.attempt['id']}.json").is_file()
    assert not (folder / "variants").exists()


def test_the_stored_copy_has_its_records_its_modes_and_the_originals_profile(monkeypatch, tmp_path: Path) -> None:
    rgb = np.random.default_rng(6).integers(0, 256, size=(5, 6, 3), dtype=np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")
    orientation = {"quarterTurnsClockwise": 1, "mirrored": False}
    token_sizes: list[int | None] = []
    real_token_hex = variants.secrets.token_hex
    monkeypatch.setattr(variants.secrets, "token_hex", lambda n=None: (token_sizes.append(n) or real_token_hex(n)))
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, display_orientation=orientation,
                                      now=fixed_clock())
    assert token_sizes == [8, 8]
    files = sorted(p.name for p in copy.folder.iterdir())
    assert files == ["attempt.json", "graph.json", "variant.json", "variant.png"]
    for path in [copy.folder / name for name in files] + list((folder / "attempts").iterdir()):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
    for path in (folder / "variants", copy.folder, folder / "attempts"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    variant = MediaVariant.model_validate(json.loads((copy.folder / "variant.json").read_text()))
    assert "\n  \"id\":" in (copy.folder / "variant.json").read_text()
    graph = ProcessingGraph.model_validate(json.loads((copy.folder / "graph.json").read_text()))
    attempt = ProcessingAttempt.model_validate(json.loads((copy.folder / "attempt.json").read_text()))
    assert [step.kind for step in graph.steps] == ["decode", "encode"]
    assert variant.disclosure == DISCLOSURES["display_copy"] and variant.attempt_id == attempt.id and attempt.status == "ready"
    assert variant.sha256 == hashlib.sha256((copy.folder / "variant.png").read_bytes()).hexdigest()
    assert variant.display_orientation is not None and variant.display_orientation.quarter_turns_clockwise == 1
    # Nothing is left in the store's partial folder
    assert list((root / ".partial").iterdir()) == []


def test_a_variant_is_created_when_its_attempt_finishes_not_when_it_starts(tmp_path: Path) -> None:
    # LANE item 5: createdAtUTC is the instant the variant came to be, as the contract's own sample has it (its
    # finishedAtUTC); the clock moves 250 ms at each reading, so start and finish differ
    rgb = np.random.default_rng(8).integers(0, 256, size=(3, 4, 3), dtype=np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    stored = json.loads((copy.folder / "variant.json").read_text())
    attempt = json.loads((copy.folder / "attempt.json").read_text())
    assert datetime.fromisoformat(attempt["startedAtUTC"]) < datetime.fromisoformat(attempt["finishedAtUTC"])
    assert stored["createdAtUTC"] == attempt["finishedAtUTC"] == copy.attempt["finishedAtUTC"]
    assert copy.variant == stored


@pytest.mark.parametrize("bits", [8, 16])
def test_the_copy_carries_the_originals_icc_profile_byte_for_byte(tmp_path: Path, bits: int) -> None:
    from test_decode import SRGB_PARA, icc_profile

    profile = icc_profile({b"rTRC": SRGB_PARA, b"gTRC": SRGB_PARA, b"bTRC": SRGB_PARA})
    rgb = np.random.default_rng(bits).integers(0, 2**bits, size=(3, 3, 3)).astype(np.uint16 if bits == 16 else np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb, icc_profile=profile), "png")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    with Image.open(copy.folder / "variant.png") as image:
        assert image.info.get("icc_profile") == profile
    assert copy.variant["colour"]["iccProfileSha256"] == hashlib.sha256(profile).hexdigest()


def test_a_second_copy_of_the_same_graph_is_refused_and_the_first_stands(tmp_path: Path) -> None:
    rgb = np.random.default_rng(8).integers(0, 256, size=(2, 2, 3), dtype=np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")
    first = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    held = snapshot(first.folder)
    clock = fixed_clock()
    clock()
    with pytest.raises(FileExistsError):
        variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=clock)
    assert snapshot(first.folder) == held
    assert list((root / ".partial").iterdir()) == []


def test_an_attempt_is_kept_once() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        record = {"id": "att_" + "1" * 16, "status": "failed"}
        variants.keep_attempt(folder, record)
        with pytest.raises(FileExistsError):
            variants.keep_attempt(folder, {**record, "status": "ready"})
        assert json.loads((folder / "attempts" / f"{record['id']}.json").read_text()) == record
        assert [p.name for p in (folder / "attempts").iterdir()] == [f"{record['id']}.json"]


def test_a_heics_copy_is_its_rgb_at_8_bits_and_says_re_encoded(tmp_path: Path) -> None:
    data = (MEDIA_FIXTURES / "sips-colour-single-96x64.heic").read_bytes()
    root, folder, original, sha = stored_original(tmp_path, data, "heic")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    try:
        still = decode.decode_still(original)
    except decode.DecodeRefusal as refusal:
        # An ffprobe that cannot report tile grids (CI's distro build) refuses every HEIC, and the attempt says so
        assert copy.status == "failed" and copy.attempt["failureReason"] == refusal.sentence == decode.TILED_HEIC
        return
    assert copy.status == "ready" and still.reencoded
    assert np.array_equal(decode.read_png((copy.folder / "variant.png").read_bytes()), still.samples)
    assert copy.variant["colour"]["model"] == "rgb" and copy.variant["colour"]["bitDepth"] == 8
    assert copy.attempt["measured"]["colour"]["model"] == "ycbcr"
    assert "re-encoded" in copy.variant["disclosure"]


@pytest.mark.local_dataset(*PHOTOS)
@pytest.mark.parametrize("photo", PHOTOS, ids=[Path(p).stem for p in PHOTOS])
def test_a_display_copy_of_each_design_photo_compares_equal(tmp_path: Path, photo: str) -> None:
    data = (ROOT / photo).read_bytes()
    root, folder, original, sha = stored_original(tmp_path, data, "jpg")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready"
    assert np.array_equal(decode.read_png((copy.folder / "variant.png").read_bytes()), decode.decode_still(original).samples)


@pytest.mark.parametrize("cut", ["no IEND", "short IDAT", "bad CRC", "trailing bytes", "no signature", "IEND not last"])
def test_a_png_that_is_not_whole_does_not_verify(cut: str) -> None:
    rgb = np.random.default_rng(9).integers(0, 256, size=(3, 4, 3), dtype=np.uint8)
    data = png_bytes(rgb)
    assert variants.png_intact(data) and variants.verify_copy(data, rgb)
    end = data.rindex(b"IEND") - 4
    broken = {
        "no IEND": data[:end],
        "short IDAT": data[:-20],
        "bad CRC": data[:-1] + bytes([data[-1] ^ 1]),
        "trailing bytes": data + b"\x00",
        "no signature": data[1:],
        "IEND not last": data + data[end:],
    }[cut]
    assert not variants.png_intact(broken)
    assert not variants.verify_copy(broken, rgb)


def test_a_png_with_another_signature_is_not_whole_though_every_chunk_is() -> None:
    data = png_bytes(np.zeros((2, 2, 3), np.uint8))
    assert not variants.png_intact(bytes(8) + data[8:])


# =============================================================================================
# The attempt's clock and times, the encode step's depth, and a file's mode while it is written
# =============================================================================================


class SteppedTime:
    """`time` for variants.py alone: each clock reads its start first and its start plus a step after."""

    def __init__(self, wall: tuple[float, float], cpu: tuple[float, float]) -> None:
        self.readings = {"monotonic": list(wall), "process_time": list(cpu)}

    def _read(self, name: str) -> float:
        start, step = self.readings[name]
        self.readings[name] = [start + step, 0.0]
        return start

    def monotonic(self) -> float:
        return self._read("monotonic")

    def process_time(self) -> float:
        return self._read("process_time")


def test_an_attempt_reads_its_times_from_the_injected_clock_and_its_durations_in_whole_milliseconds(tmp_path: Path, monkeypatch) -> None:
    # The wall clock moves 2.5 s and the processor 1.25 s between the start and the record, so the milliseconds
    # are 2500 and 1250 exactly, and a factor or a sign gone wrong shows
    monkeypatch.setattr(variants, "time", SteppedTime((100.0, 2.5), (5.0, 1.25)))
    rgb = np.random.default_rng(10).integers(0, 256, size=(2, 3, 3), dtype=np.uint8)
    root, folder, original, sha = stored_original(tmp_path, png_bytes(rgb), "png")
    copy = variants.make_display_copy(root, folder, original, source_sha256=sha, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready"
    assert (copy.attempt["elapsedMs"], copy.attempt["cpuMs"]) == (2500, 1250)
    assert copy.attempt["startedAtUTC"] == "2031-01-01T00:00:00Z"


@pytest.mark.parametrize("bits", [8, 16])
def test_the_encode_step_records_the_depth_and_pixel_format_of_the_samples_it_keeps(tmp_path: Path, bits: int) -> None:
    dtype = np.uint16 if bits == 16 else np.uint8
    rgb = np.random.default_rng(11).integers(0, 2**bits, size=(2, 3, 3), dtype=dtype)
    path = tmp_path / "still.png"
    path.write_bytes(png_bytes(rgb))
    still = decode.decode_still(path)
    encode = {p["name"]: p["value"] for p in variants.display_copy_graph("a" * 64, still)["steps"][1]["parameters"]}
    assert (encode["bitDepth"], encode["pixelFormat"]) == ((16, "rgb48le") if bits == 16 else (8, "rgb24"))


def test_a_stored_file_is_readable_by_its_owner_alone_while_it_is_written(tmp_path: Path, monkeypatch) -> None:
    # Between its creation and its chmod to 0400 the file is 0600 (under the process's umask), so no other user can
    # read a record or a copy half written
    modes: list[int] = []
    sync = os.fsync

    def watched(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
        sync(fd)

    monkeypatch.setattr(variants.os, "fsync", watched)
    umask = os.umask(0o022)
    os.umask(umask)
    variants._write_new(tmp_path / "record.json", b"{}\n")
    assert modes == [0o600 & ~umask]
    assert stat.S_IMODE((tmp_path / "record.json").stat().st_mode) == 0o400
