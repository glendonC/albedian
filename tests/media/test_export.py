"""The export and its manifest (`services/media/router.py`, `services/media/variants.py`; restoration design section 12;
REQ-RST-9.1 to 9.7; P-RST-21; review row D22, `.lead/uiux/UIUX-export-READY.md`).

Covers, through the API on a store of its own:
- An export of a ready restoration is one zip stored without compression, an attachment named `<variant id>.zip`,
  holding `<variant id>.png`, the stored variant byte for byte, and `<variant id>.png.json`, its manifest pretty
  printed; the manifest route answers the same manifest. The exported bytes hash to the manifest's variant hash,
  and the manifest parses under the contract (P-RST-21).
- The manifest names the upload's newest solution id when the job store names one, and no solution otherwise.
- A synthetic upload planted with a file name, a capture time, a place, container tags (tEXt, iTXt, tIME, eXIf with a
  camera make) and an ICC profile with planted text in every kind of text tag: the upload's record holds the name, the
  time, the place and the camera, and the export holds none of them, in the manifest or in the exported file's
  chunks; the profile's colour data is kept byte for byte and its text tags hold only the fixed text (REQ-RST-9.4).
- A rejected attempt has nothing to export, nor a variant whose attempt is not ready (REQ-RST-9.6); a malformed id is
  422; an unknown one, a folder or file reached through a link, a folder missing a record or naming another variant
  404; a record naming a type the export has no row for 500; a stored file that no longer hashes to its record is
  never served,
  and an entry that changes while it is sent stops the zip before its directory.
- A restored clip, QuickTime and MP4, exports the same way: `<variant id>.mov` or `.mp4`, its stored file byte for
  byte (lossless, restore-7), with `<variant id>.mov.json` or `.mp4.json`, its manifest (restore-8c's second half).
- The builder, given the contract fixture's own parts, writes the fixture's manifests key for key (the Vitest parity
  suite parses those under ExportManifestSchema, so the builder's shape parses in both languages), and refuses a
  manifest of a rejected attempt.
- The stored zip writer writes what Python's zipfile writes for the same entries stored, and its size is known before
  a byte is sent.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import time
import zipfile
import zlib
from unittest import mock
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from PIL import Image, TiffImagePlugin
from pydantic import ValidationError

import icc_profiles as icc
from photo_frames import star_field
from services.api import create_app
from services.contracts import media as contracts
from services.ingest import router as ingest_router
from services.ingest.job_store import JobStore
from services.ingest.store import IngestStore
from services.media import jobs as media_jobs
from services.media import photo, png16, variants

ROOT = Path(__file__).resolve().parents[2]
MEDIA_CONTRACT = ROOT / "tests" / "fixtures" / "media_contract.json"
POLL_S = 0.1
JOB_BOUND_S = 240.0
REQUEST = {"kind": "restoration"}
RECORDS_REFUSED = "the stored records do not make a manifest the contract takes"

# What the synthetic upload is planted with; none of it may reach the export
PLANTED_NAME = "planted-name-c41e9b.png"
PLANTED_TITLE = "planted title 7d20aa"
PLANTED_COMMENT = "planted comment 5be613"
PLANTED_XMP = "planted xmp 0f94d2"
PLANTED_MAKE = "planted make 3e1b07"
PLANTED_EXIF_TIME = "2021:03:04 05:06:07"
PLANTED_OFFSET = "+02:00"
PLANTED_UTC = "2021-03-04T03:06:07"
# 10 deg 7 min 24.41616 s north, 20 deg 15 min 33.5 s east: made up for this test
Rat = TiffImagePlugin.IFDRational
PLANTED_LAT = (Rat(10, 1), Rat(7, 1), Rat(2441616, 100000))
PLANTED_LON = (Rat(20, 1), Rat(15, 1), Rat(335, 10))
PLANTED_CHUNKS = (b"tEXt", b"iTXt", b"tIME", b"eXIf")
# A real ICC profile, Little CMS's own sRGB as Pillow builds it (made here, so no profile file is copied into the
# repository), with the planted name, title and other marks in every kind of text tag it can hold (restore-8b)
PROFILE = icc.planted(icc.srgb(), {"desc": PLANTED_TITLE, "cprt": PLANTED_NAME, "dmnd": PLANTED_MAKE,
                                   "dmdd": PLANTED_COMMENT, "vued": PLANTED_XMP, "mmod": PLANTED_EXIF_TIME,
                                   "meta": PLANTED_UTC})
# And the planted name in every other byte of it that is no colour data: free header bytes, padding, gaps (restore-8b v3)
PROFILE = icc.planted_everywhere(PROFILE, PLANTED_NAME.encode("latin-1"))


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def planted_exif() -> bytes:
    """An EXIF block with a capture time, its offset and a GPS place, as the eXIf chunk holds it (no Exif header)."""
    exif = Image.Exif()
    exif[0x010E] = PLANTED_TITLE  # ImageDescription
    exif[0x010F] = PLANTED_MAKE  # Make
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = PLANTED_EXIF_TIME  # DateTimeOriginal
    sub[0x9011] = PLANTED_OFFSET  # OffsetTimeOriginal
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", PLANTED_LAT, "E", PLANTED_LON
    data = exif.tobytes()
    assert data.startswith(b"Exif\x00\x00")
    return data[6:]


def planted_png(codes: np.ndarray) -> bytes:
    """A 16 bit PNG of `codes` with an ICC profile, a title, a comment, XMP, a modification time and an EXIF block
    (time, place and camera) between its IHDR and its first IDAT, where a reader meets them before the pixels."""
    plain = png16.write_png16(codes.astype(np.uint16), None)
    ihdr_end = len(png16.SIGNATURE) + 12 + 13
    assert plain[len(png16.SIGNATURE) + 4:len(png16.SIGNATURE) + 8] == b"IHDR"
    xmp = f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description>{PLANTED_XMP}</rdf:Description></x:xmpmeta>'
    extra = b"".join([
        chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(PROFILE)),
        chunk(b"tEXt", b"Title\x00" + PLANTED_TITLE.encode("latin-1")),
        chunk(b"tEXt", b"Comment\x00" + PLANTED_COMMENT.encode("latin-1")),
        chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp.encode("utf-8")),
        chunk(b"tIME", struct.pack(">HBBBBB", 2021, 3, 4, 5, 6, 7)),
        chunk(b"eXIf", planted_exif()),
    ])
    return plain[:ihdr_end] + extra + plain[ihdr_end:]


def png_chunk_kinds(data: bytes) -> list[bytes]:
    position, kinds = len(png16.SIGNATURE), []
    while position + 12 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        kinds.append(data[position + 4:position + 8])
        position += 12 + length
    return kinds


def png_profile(data: bytes) -> bytes:
    """The ICC profile of a PNG's one iCCP chunk: its keyword, a zero, the compression method zero, then zlib."""
    position, found = len(png16.SIGNATURE), []
    while position + 12 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        if data[position + 4:position + 8] == b"iCCP":
            body = data[position + 8:position + 8 + length]
            assert body[body.index(b"\x00") + 1] == 0
            found.append(zlib.decompress(body[body.index(b"\x00") + 2:]))
        position += 12 + length
    assert len(found) == 1, len(found)
    return found[0]


def finished(client: TestClient, job: dict) -> dict:
    deadline = time.monotonic() + JOB_BOUND_S
    while job["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the job did not end within the test's bound"
        time.sleep(POLL_S)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    return job


@pytest.fixture(scope="module")
def restored(tmp_path_factory):
    """One store with the planted upload restored ready: the client, the upload's hash, its folder and the job."""
    store_root = tmp_path_factory.mktemp("export") / "store"
    uploads = IngestStore(store_root, max_bytes=200_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    with TestClient(app) as client:
        answer = client.post("/api/ingest", content=planted_png(scene.codes),
                             headers={"content-type": "image/png", "x-albedian-filename": quote(PLANTED_NAME)})
        assert answer.status_code in (200, 201), answer.text
        sha = answer.json()["sourceAsset"]["sha256"]
        job = finished(client, client.post(f"/api/media/{sha}/restore", json=REQUEST).json())
        assert job["state"] == "succeeded", job
        client.store_root = store_root
        yield client, sha, store_root / sha[:2] / sha, job


# A clip for the clips' export: a dark sky with noise of its own in every frame, a tone for its sound (synthetic)
CLIP_SKY = "color=c=0x181818:s=96x64:r=30:d=0.5,noise=alls=14:allf=t"
CLIP_TYPES = {"mov": "video/quicktime", "mp4": "video/mp4"}
# Planted into each clip (synthetic): a place far from anyone's, a time, a title, a comment and a stream's handler
CLIP_PLANTED_PLACE = "+10.1234+020.5678/"
CLIP_PLANTED_HANDLER = "planted handler 9a41c2"
CLIP_PLANTED = (PLANTED_TITLE, PLANTED_COMMENT, CLIP_PLANTED_PLACE, CLIP_PLANTED_HANDLER, "2021-03-04T03:06:07")


@pytest.fixture(scope="module")
def restored_clips(tmp_path_factory):
    """One store with a QuickTime clip and an MP4 clip each restored ready: the client, and for each extension the
    upload's hash, its folder and the job."""
    import subprocess

    base = tmp_path_factory.mktemp("export-clips")
    store_root = base / "store"
    uploads = IngestStore(store_root, max_bytes=200_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    done = {}
    with TestClient(app) as client:
        for extension, mime in CLIP_TYPES.items():
            source = base / f"clip.{extension}"
            made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", CLIP_SKY, "-f", "lavfi",
                                   "-i", "sine=frequency=440:duration=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a",
                                   "aac", "-shortest", "-movflags", "use_metadata_tags", "-metadata", f"title={PLANTED_TITLE}",
                                   "-metadata", f"comment={PLANTED_COMMENT}", "-metadata", f"location={CLIP_PLANTED_PLACE}",
                                   "-metadata", "creation_time=2021-03-04T03:06:07Z", "-metadata:s:v:0",
                                   f"handler_name={CLIP_PLANTED_HANDLER}", "-metadata:s:a:0",
                                   f"handler_name={CLIP_PLANTED_HANDLER}", str(source)],
                                  capture_output=True, stdin=subprocess.DEVNULL)
            assert made.returncode == 0, made.stderr.decode("utf-8", "replace")[-600:]
            # The upload holds every planted value, so their absence from the export is the restoration's doing
            assert all(value.encode("utf-8") in source.read_bytes() for value in CLIP_PLANTED), extension
            answer = client.post("/api/ingest", content=source.read_bytes(), headers={"content-type": mime})
            assert answer.status_code in (200, 201), answer.text
            sha = answer.json()["sourceAsset"]["sha256"]
            job = finished(client, client.post(f"/api/media/{sha}/restore", json=REQUEST).json())
            assert job["state"] == "succeeded", job
            done[extension] = (sha, store_root / sha[:2] / sha, job)
        yield client, done


def unzip(content: bytes) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    archive = zipfile.ZipFile(io.BytesIO(content))
    assert archive.testzip() is None
    return archive, archive.infolist()


def test_an_export_is_one_stored_zip_of_the_variant_byte_for_byte_and_its_manifest(restored) -> None:
    client, sha, folder, job = restored
    vid = job["variantId"]
    answer = client.get(f"/api/media/{sha}/variants/{vid}/export")
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "application/zip"
    assert answer.headers["content-disposition"] == f'attachment; filename="{vid}.zip"'
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["cache-control"] == "no-store"
    assert int(answer.headers["content-length"]) == len(answer.content)
    archive, entries = unzip(answer.content)
    assert [entry.filename for entry in entries] == [f"{vid}.png", f"{vid}.png.json"]
    assert all(entry.compress_type == zipfile.ZIP_STORED and entry.flag_bits == 0 for entry in entries)
    # Readable by everyone who unpacks it, whatever the store's own 0400
    assert all((entry.external_attr >> 16) == 0o100644 for entry in entries)
    stored = (folder / "variants" / vid / "variant.png").read_bytes()
    exported = archive.read(f"{vid}.png")
    assert exported == stored == client.get(f"/api/media/{sha}/variants/{vid}/file").content
    sidecar = archive.read(f"{vid}.png.json")
    manifest = json.loads(sidecar.decode("utf-8"))
    # P-RST-21: the manifest parses and its variant hash is the SHA-256 of the exported bytes
    contracts.ExportManifest.model_validate(manifest)
    assert manifest["variant"]["sha256"] == hashlib.sha256(exported).hexdigest()
    assert manifest["variant"]["byteSize"] == len(exported)
    records = client.get(f"/api/media/{sha}/variants/{vid}").json()
    assert (manifest["variant"], manifest["graph"], manifest["attempt"]) == (records["variant"], records["graph"], records["attempt"])
    assert manifest["format"] == "albedian.export-manifest" and manifest["formatVersion"] == 1
    assert manifest["sourceSha256"] == sha and manifest["attempt"]["status"] == "ready"
    assert manifest["disclosure"] == contracts.DISCLOSURES["restoration"]
    assert sidecar == (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    # The manifest route answers the zip's own sidecar
    alone = client.get(f"/api/media/{sha}/variants/{vid}/manifest")
    assert alone.status_code == 200 and alone.content == sidecar
    assert alone.headers["content-type"] == "application/json"
    assert alone.headers["content-disposition"] == f'attachment; filename="{vid}.png.json"'
    assert alone.headers["cache-control"] == "no-store"
    # The same stored state exports the same bytes: no clock of its own
    assert client.get(f"/api/media/{sha}/variants/{vid}/export").content == answer.content


def test_the_manifest_names_the_newest_solution_the_job_store_names_and_none_without_one(restored, monkeypatch) -> None:
    client, sha, _, job = restored
    vid = job["variantId"]
    asked = []

    def named(self, of):
        asked.append(of)
        return f"sol_{of[:16]}"

    without = client.get(f"/api/media/{sha}/variants/{vid}/manifest").json()
    assert "observationSolutionId" not in without
    monkeypatch.setattr(JobStore, "newest_solution_id", named)
    for path in ("manifest", "export"):
        answer = client.get(f"/api/media/{sha}/variants/{vid}/{path}")
        assert answer.status_code == 200, answer.text
        manifest = answer.json() if path == "manifest" else json.loads(unzip(answer.content)[0].read(f"{vid}.png.json"))
        contracts.ExportManifest.model_validate(manifest)
        assert manifest["observationSolutionId"] == f"sol_{sha[:16]}"
        assert list(manifest)[-2:] == ["observationSolutionId", "disclosure"]
    assert asked == [sha, sha]


def keys_and_leaves(node, keys: set, leaves: list) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys_and_leaves(value, keys, leaves)
    elif isinstance(node, list):
        for value in node:
            keys_and_leaves(value, keys, leaves)
    else:
        leaves.append(node)


def test_an_export_holds_no_name_time_place_or_container_tag_the_upload_was_planted_with(restored) -> None:
    client, sha, _, job = restored
    asset = client.get(f"/api/ingest/{sha}").json()["sourceAsset"]
    # The planting reached the upload's own record, so each absence below is a real one
    assert asset["title"] == PLANTED_NAME
    assert asset["capturedAtUTC"].startswith(PLANTED_UTC)
    lat, lon = asset["observerLocation"]["latDeg"], asset["observerLocation"]["lonDeg"]
    assert PLANTED_MAKE in json.dumps(asset["cameraMetadata"])
    assert abs(lat - 10.123449) < 1e-5 and abs(lon - 20.259306) < 1e-5
    vid = job["variantId"]
    content = client.get(f"/api/media/{sha}/variants/{vid}/export").content
    archive, _ = unzip(content)
    exported, sidecar = archive.read(f"{vid}.png"), archive.read(f"{vid}.png.json").decode("utf-8")
    texts = [PLANTED_NAME, PLANTED_NAME.rsplit(".", 1)[0], PLANTED_TITLE, PLANTED_COMMENT, PLANTED_XMP, PLANTED_MAKE, PLANTED_EXIF_TIME,
             PLANTED_UTC, PLANTED_UTC[:10], str(client.store_root), str(Path.home())]
    for text in texts:
        assert text.encode("utf-8") not in content, text
    kinds = png_chunk_kinds(exported)
    assert not set(kinds) & set(PLANTED_CHUNKS), kinds
    assert set(kinds) <= {b"IHDR", b"IDAT", b"IEND", b"iCCP", b"sRGB", b"gAMA", b"cHRM"}, kinds
    # The colour profile is built anew from its colour data alone [decided 2026-11-19 by the lead at 07:31 and 08:34]:
    # it is the clean sRGB's rebuild byte for byte, and none of the planted text is left in it in any encoding
    kept = png_profile(exported)
    assert kept == photo.profile_without_text(icc.srgb())
    before, after = icc.tag_data(icc.srgb()), icc.tag_data(kept)
    assert all(after[sig] == before[sig] for sig in icc.COLOUR_SIGNATURES if sig in before)
    assert set(icc.text_tags(kept)) == {b"desc", b"cprt"}
    assert {text for _, text in icc.text_tags(kept).values()} == {photo.PROFILE_DESCRIPTION, photo.PROFILE_TEXT}
    for text in texts + ["planted", "2021"]:
        for encoding in ("latin-1", "utf-16-be"):
            assert text.encode(encoding) not in kept, (text, encoding)
    manifest = json.loads(sidecar)
    # The manifest's variant names the profile of the PNG beside it, its attempt the original's (restore-8d)
    assert manifest["variant"]["colour"]["iccProfileSha256"] == hashlib.sha256(kept).hexdigest()
    assert manifest["attempt"]["measured"]["colour"]["iccProfileSha256"] == hashlib.sha256(PROFILE).hexdigest()
    keys, leaves = set(), []
    keys_and_leaves(manifest, keys, leaves)
    # A graph parameter's "name" is the parameter's (its decode or encode setting), so it is not among these
    forbidden = {"title", "fileName", "filename", "path", "capturedAtUTC", "observerLocation", "location", "latDeg",
                 "lonDeg", "elevationM", "place", "exif", "tags", "cameraMetadata", "metadataSources"}
    assert not keys & forbidden, keys & forbidden
    numbers = [value for value in leaves if isinstance(value, float)]
    assert all(abs(value - lat) > 1e-9 and abs(value - lon) > 1e-9 for value in numbers)
    for text in (repr(lat), repr(lon), f"{lat:.6f}", f"{lon:.6f}"):
        assert text not in sidecar, text
    # The one path-like field is the variant's route under its original (REQ-RST-8.6), never a folder of the store
    assert manifest["variant"]["uri"] == f"/api/media/{sha}/variants/{vid}/file"


def test_a_rejected_attempt_has_nothing_to_export(restored, monkeypatch) -> None:
    """REQ-RST-9.6: the gate refuses a restoration with a bump it made up; its attempt is kept and listed, and neither
    the variant id its graph would have nor the attempt's own id exports anything."""
    from services.media import gate, photo
    client, _, _, _ = restored
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    # Another upload of the same sky, so its graph is its own
    sha = client.post("/api/ingest", content=png16.write_png16(scene.codes.astype(np.uint16) ^ 1, None),
                      headers={"content-type": "image/png"}).json()["sourceAsset"]["sha256"]
    folder = client.store_root / sha[:2] / sha
    smooth = gate.smooth_first_scale

    def with_a_bump(working, kept):
        out = smooth(working, kept)
        yy, xx = np.mgrid[0:out.shape[0], 0:out.shape[1]].astype(np.float64)
        return out + (0.004 * np.exp(-((xx - 40.5) ** 2 + (yy - 40.5) ** 2) / (2 * 1.3**2)))[..., None]

    restore = photo.restore_still
    monkeypatch.setattr(gate, "smooth_first_scale", with_a_bump)
    monkeypatch.setattr(photo, "restore_still", lambda still, ev=None: restore(still, ev, max_passes=0))
    out = media_jobs.restoration_in_child(str(client.store_root), str(folder), str(folder / "original.png"), sha,
                                          "job_" + "c" * 16, REQUEST, None, lambda *args: None)
    assert out["status"] == "rejected", out
    listed = client.get(f"/api/media/{sha}/variants").json()
    assert listed["variants"] == [] and [a["status"] for a in listed["attempts"]] == ["rejected"]
    would_be = variants.variant_id(sha, "restoration", REQUEST)
    attempt_as_variant = "var_" + out["attemptId"][len("att_"):]
    for vid in (would_be, attempt_as_variant):
        for path in ("export", "manifest"):
            assert client.get(f"/api/media/{sha}/variants/{vid}/{path}").status_code == 404, (vid, path)


def rewrite(path: Path, data: bytes) -> bytes:
    before = path.read_bytes()
    os.chmod(path, 0o600)
    path.write_bytes(data)
    os.chmod(path, 0o400)
    return before


@pytest.mark.parametrize("status", ["rejected", "failed"])
def test_a_variant_whose_attempt_is_not_ready_exports_nothing(restored, status: str) -> None:
    client, sha, folder, job = restored
    vid = job["variantId"]
    path = folder / "variants" / vid / "attempt.json"
    record = json.loads(path.read_text())
    before = rewrite(path, json.dumps({**record, "status": status}).encode("utf-8"))
    try:
        for name in ("export", "manifest"):
            assert client.get(f"/api/media/{sha}/variants/{vid}/{name}").status_code == 404, name
    finally:
        rewrite(path, before)
    assert client.get(f"/api/media/{sha}/variants/{vid}/export").status_code == 200


def test_a_stored_file_that_no_longer_hashes_to_its_record_is_never_served(restored) -> None:
    client, sha, folder, job = restored
    vid = job["variantId"]
    path = folder / "variants" / vid / "variant.png"
    stored = path.read_bytes()
    changed = bytearray(stored)
    changed[-20] ^= 0xFF
    before = rewrite(path, bytes(changed))
    try:
        answer = client.get(f"/api/media/{sha}/variants/{vid}/export")
        assert answer.status_code == 500
        assert answer.headers["content-type"] == "application/json" and bytes(changed) not in answer.content
    finally:
        rewrite(path, before)


def test_a_variant_folder_missing_a_record_or_reached_through_a_link_exports_nothing(restored) -> None:
    client, sha, folder, job = restored
    vid = job["variantId"]
    place = folder / "variants" / vid
    # A link named like a variant, to the real folder: never followed
    linked = "var_" + "e" * 16
    os.symlink(place, folder / "variants" / linked)
    try:
        for name in ("export", "manifest"):
            assert client.get(f"/api/media/{sha}/variants/{linked}/{name}").status_code == 404, name
    finally:
        (folder / "variants" / linked).unlink()
    # A copy of the folder with its graph gone
    partial = "var_" + "f" * 16
    shutil.copytree(place, folder / "variants" / partial)
    os.chmod(folder / "variants" / partial, 0o700)
    (folder / "variants" / partial / "graph.json").unlink()
    try:
        for name in ("export", "manifest"):
            assert client.get(f"/api/media/{sha}/variants/{partial}/{name}").status_code == 404, name
    finally:
        shutil.rmtree(folder / "variants" / partial)


def renamed_copy(place: Path, target: Path, new_id: str, sha: str) -> None:
    """A copy of a variant folder whose records all name `new_id`, so the contract takes it as a variant of its own."""
    shutil.copytree(place, target)
    os.chmod(target, 0o700)
    for name, fields in (("variant.json", {"id": new_id, "uri": contracts.variant_uri(sha, new_id)}),
                         ("attempt.json", {"variantId": new_id})):
        record = json.loads((target / name).read_text())
        rewrite(target / name, json.dumps({**record, **fields}).encode("utf-8"))


def test_a_folder_or_file_reached_through_a_link_or_naming_another_variant_exports_nothing(restored, tmp_path) -> None:
    client, sha, folder, job = restored
    place, made = folder / "variants" / job["variantId"], []
    try:
        # A whole copy under its own consistent id exports: the cases below differ from it in one thing each
        ours = "var_" + "1" * 16
        renamed_copy(place, folder / "variants" / ours, ours, sha)
        made.append(folder / "variants" / ours)
        assert client.get(f"/api/media/{sha}/variants/{ours}/export").status_code == 200
        # The folder is a link to such a copy kept outside the store
        outside, linked = tmp_path / "outside", "var_" + "2" * 16
        renamed_copy(place, outside, linked, sha)
        os.symlink(outside, folder / "variants" / linked)
        made.append(folder / "variants" / linked)
        # The folder is real, its file a link to the real variant's file
        file_linked = "var_" + "3" * 16
        renamed_copy(place, folder / "variants" / file_linked, file_linked, sha)
        made.append(folder / "variants" / file_linked)
        (folder / "variants" / file_linked / "variant.png").unlink()
        os.symlink(place / "variant.png", folder / "variants" / file_linked / "variant.png")
        # A real folder whose graph record is a folder, not a file
        not_a_file = "var_" + "5" * 16
        renamed_copy(place, folder / "variants" / not_a_file, not_a_file, sha)
        made.append(folder / "variants" / not_a_file)
        (folder / "variants" / not_a_file / "graph.json").unlink()
        (folder / "variants" / not_a_file / "graph.json").mkdir()
        # A whole copy whose records name the variant it was copied from
        other = "var_" + "4" * 16
        shutil.copytree(place, folder / "variants" / other)
        os.chmod(folder / "variants" / other, 0o700)
        made.append(folder / "variants" / other)
        for vid, names in ((linked, ("export", "manifest")), (file_linked, ("export",)), (other, ("export", "manifest")),
                           (not_a_file, ("export", "manifest"))):
            for name in names:
                assert client.get(f"/api/media/{sha}/variants/{vid}/{name}").status_code == 404, (vid, name)
    finally:
        for path in made:
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path)


def test_a_variant_record_naming_a_type_with_no_export_row_is_refused_as_a_record(restored) -> None:
    client, sha, folder, job = restored
    path = folder / "variants" / job["variantId"] / "variant.json"
    record = json.loads(path.read_text())
    before = rewrite(path, json.dumps({**record, "mimeType": "image/heic"}).encode("utf-8"))
    try:
        for name in ("export", "manifest"):
            answer = client.get(f"/api/media/{sha}/variants/{job['variantId']}/{name}")
            assert answer.status_code == 500 and answer.json()["detail"] == RECORDS_REFUSED, name
    finally:
        rewrite(path, before)


@pytest.mark.parametrize("extension", sorted(CLIP_TYPES))
def test_a_clips_export_is_one_stored_zip_of_its_stored_file_byte_for_byte_and_its_manifest(restored_clips,
                                                                                             extension: str) -> None:
    client, done = restored_clips
    sha, folder, job = done[extension]
    vid = job["variantId"]
    answer = client.get(f"/api/media/{sha}/variants/{vid}/export")
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "application/zip"
    assert answer.headers["content-disposition"] == f'attachment; filename="{vid}.zip"'
    assert int(answer.headers["content-length"]) == len(answer.content)
    archive, entries = unzip(answer.content)
    assert [entry.filename for entry in entries] == [f"{vid}.{extension}", f"{vid}.{extension}.json"]
    assert all(entry.compress_type == zipfile.ZIP_STORED for entry in entries)
    stored = (folder / "variants" / vid / f"variant.{extension}").read_bytes()
    exported = archive.read(f"{vid}.{extension}")
    assert exported == stored == client.get(f"/api/media/{sha}/variants/{vid}/file").content
    sidecar = archive.read(f"{vid}.{extension}.json")
    manifest = json.loads(sidecar.decode("utf-8"))
    # P-RST-21: the manifest parses and its variant hash is the SHA-256 of the exported bytes
    contracts.ExportManifest.model_validate(manifest)
    assert manifest["variant"]["mimeType"] == CLIP_TYPES[extension] and manifest["attempt"]["status"] == "ready"
    assert manifest["variant"]["sha256"] == hashlib.sha256(exported).hexdigest()
    assert manifest["variant"]["byteSize"] == len(exported)
    records = client.get(f"/api/media/{sha}/variants/{vid}").json()
    assert (manifest["variant"], manifest["graph"], manifest["attempt"]) == (records["variant"], records["graph"], records["attempt"])
    alone = client.get(f"/api/media/{sha}/variants/{vid}/manifest")
    assert alone.status_code == 200 and alone.content == sidecar
    assert alone.headers["content-disposition"] == f'attachment; filename="{vid}.{extension}.json"'
    assert client.get(f"/api/media/{sha}/variants/{vid}/export").content == answer.content
    # No planted title, comment, place, time or stream tag goes out, in the clip or in its manifest
    for value in CLIP_PLANTED:
        assert value.encode("utf-8") not in answer.content, value


@pytest.mark.parametrize(("path", "status"), [
    ("/api/media/nothex/variants/var_0123456789abcdef/export", 422),
    ("/api/media/" + "a" * 64 + "/variants/var_0123/export", 422),
    ("/api/media/" + "a" * 64 + "/variants/var_0123456789abcdef/export", 404),
    ("/api/media/" + "a" * 64 + "/variants/var_0123456789abcdef/manifest", 404),
    ("/api/media/" + "a" * 64 + "/variants/..%2F..%2Fvariant.json/export", 404),
    ("/api/media/" + "a" * 64 + "/variants/var_0123456789abcdeF/manifest", 422),
])
def test_a_malformed_hash_or_id_is_422_and_an_unknown_one_404(restored, path: str, status: int) -> None:
    client = restored[0]
    assert client.get(path).status_code == status


def test_a_display_copy_exports_with_its_own_disclosure_and_no_text_of_the_originals_profile(restored) -> None:
    client, sha, _, _ = restored
    job = finished(client, client.post(f"/api/media/{sha}/restore", json={"kind": "display_copy"}).json())
    assert job["state"] == "succeeded", job
    vid = job["variantId"]
    archive, _ = unzip(client.get(f"/api/media/{sha}/variants/{vid}/export").content)
    manifest = json.loads(archive.read(f"{vid}.png.json"))
    contracts.ExportManifest.model_validate(manifest)
    assert manifest["disclosure"] == contracts.DISCLOSURES["display_copy"] and "gate" not in manifest["attempt"]
    exported = archive.read(f"{vid}.png")
    assert hashlib.sha256(exported).hexdigest() == manifest["variant"]["sha256"]
    # The same rebuild as a restoration's (restore-8b, decided 2026-11-19 07:40 by the lead): the clean sRGB's
    kept = png_profile(exported)
    assert kept == photo.profile_without_text(icc.srgb())
    assert {text for _, text in icc.text_tags(kept).values()} == {photo.PROFILE_DESCRIPTION, photo.PROFILE_TEXT}
    for text in (PLANTED_TITLE, PLANTED_NAME, PLANTED_MAKE, PLANTED_COMMENT, PLANTED_XMP, PLANTED_EXIF_TIME, PLANTED_UTC):
        for encoding in ("latin-1", "utf-16-be"):
            assert text.encode(encoding) not in kept, (text, encoding)


# =============================================================================================
# The builder and the zip writer, in this process
# =============================================================================================


def fixture_manifests() -> list[dict]:
    return json.loads(MEDIA_CONTRACT.read_text(encoding="utf-8"))["valid"]["ExportManifest"]


def test_the_builder_writes_the_contract_fixtures_manifests_key_for_key() -> None:
    """The fixture's manifests are those the Vitest parity suite parses under ExportManifestSchema: the builder's
    output on their own parts is each of them, keys in the same order, so it parses in both languages."""
    manifests = fixture_manifests()
    assert len(manifests) == 4
    for manifest in manifests:
        built = variants.export_manifest(manifest["variant"], manifest["graph"], manifest["attempt"],
                                         manifest.get("observationSolutionId"))
        assert json.dumps(built) == json.dumps(manifest)
        assert variants.manifest_bytes(built) == (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def test_the_builder_refuses_a_manifest_the_contract_refuses() -> None:
    manifest = fixture_manifests()[0]
    rejected = {**manifest["attempt"], "status": "rejected"}
    with pytest.raises(ValidationError):
        variants.export_manifest(manifest["variant"], manifest["graph"], rejected, None)
    with pytest.raises(ValidationError):
        variants.export_manifest(manifest["variant"], manifest["graph"], manifest["attempt"], "sol_" + "0" * 16)


@settings(max_examples=60, deadline=None)
@given(contents=st.lists(st.binary(max_size=9_000), min_size=1, max_size=3),
       moment=st.datetimes(min_value=datetime(1980, 1, 1), max_value=datetime(2107, 12, 31, 23, 59, 58)))
@example(contents=[b"", b""], moment=datetime(1980, 1, 1))
@example(contents=[bytes(70_001), b"{}\n"], moment=datetime(2026, 11, 19, 6, 18, 42))
def test_the_stored_zip_is_what_pythons_zipfile_writes_and_its_size_is_known_first(contents, moment) -> None:
    stamp = (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second - moment.second % 2)
    written, expected = both_zips(contents, stamp)
    assert written == expected
    _, read = unzip(written)
    assert [zipfile.ZipFile(io.BytesIO(written)).read(info) for info in read] == contents


def zip_entry(name: str, data: bytes) -> variants.ZipEntry:
    return variants.ZipEntry(name, len(data), zlib.crc32(data) & 0xFFFFFFFF,
                             lambda: (data[i:i + 4096] for i in range(0, len(data), 4096)))


def both_zips(contents: list[bytes], stamp: tuple, limit: int | None = None) -> tuple[bytes, bytes]:
    """What the writer writes for `contents` and what Python's zipfile writes for them stored, both with zip64 from
    `limit` bytes on when one is given (zipfile's own ZIP64_LIMIT lowered to it), its default otherwise. The writer's
    size, known first, is checked against what it wrote."""
    names = [f"var_{index:016x}.png" for index in range(len(contents))]
    entries = [zip_entry(name, data) for name, data in zip(names, contents)]
    options = {} if limit is None else {"zip64_limit": limit}
    written = b"".join(variants.stored_zip(entries, stamp, **options))
    assert variants.stored_zip_size(entries, **options) == len(written)
    expected = io.BytesIO()
    with mock.patch.object(zipfile, "ZIP64_LIMIT", zipfile.ZIP64_LIMIT if limit is None else limit):
        with zipfile.ZipFile(expected, "w", zipfile.ZIP_STORED) as archive:
            for name, data in zip(names, contents):
                info = zipfile.ZipInfo(name, date_time=stamp)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
    return written, expected.getvalue()


def test_the_writers_zip64_limit_is_pythons_two_gib() -> None:
    """Python's zipfile writes zip64 for any entry, offset or directory past 2 GiB less one byte, since some readers
    take the 32 bit fields as signed [verified: CPython's zipfile ZIP64_LIMIT, 3.12 and 3.14, read for this unit]."""
    assert variants.ZIP64_LIMIT == zipfile.ZIP64_LIMIT == (1 << 31) - 1


def test_past_the_limit_the_zip_is_what_pythons_zipfile_writes_as_zip64() -> None:
    """With the limit lowered to 3,000 bytes: an entry past it (zip64 sizes in its local header and in the directory),
    one at an offset past it (its offset in the directory's zip64 field), and a directory past it (the zip64 end
    records), byte for byte as zipfile writes them, and read back whole."""
    contents = [bytes(range(256)) * 20, b"x" * 100, b"{}\n" * 600]
    written, expected = both_zips(contents, (2026, 11, 19, 10, 20, 0), limit=3_000)
    assert written == expected
    assert b"PK\x06\x06" in written and b"PK\x06\x07" in written
    archive, read = unzip(written)
    assert [archive.read(info) for info in read] == contents
    assert read[0].file_size == 5_120 and read[1].header_offset > 3_000


def test_an_entry_of_exactly_the_limit_is_not_zip64_and_reads_back() -> None:
    """zipfile itself disagrees with itself there (its local header goes zip64 from 1/1.05 of the limit, its directory
    past it); this writer keeps 32 bit fields up to the limit in both, which a reader taking them as signed still reads."""
    data = b"z" * 3_000
    written = b"".join(variants.stored_zip([zip_entry("a.png", data)], (2026, 11, 19, 10, 20, 0), zip64_limit=3_000))
    # The local header: version 2.0, both sizes 3,000, no extra field
    assert struct.unpack("<H", written[4:6]) == (20,) and struct.unpack("<IIHH", written[18:30]) == (3_000, 3_000, 5, 0)
    # The directory's record of it: version 2.0, both sizes, no extra field (the directory itself starts past the limit,
    # so the end records are zip64)
    directory = written.index(b"PK\x01\x02")
    assert struct.unpack("<HH", written[directory + 4:directory + 8]) == (0x0314, 20)
    assert struct.unpack("<IIHH", written[directory + 20:directory + 32]) == (3_000, 3_000, 5, 0)
    archive, read = unzip(written)
    assert archive.read(read[0]) == data


def test_past_four_gib_the_planned_records_hold_the_true_offsets_at_the_real_limit() -> None:
    """At the real limit, with no byte on disk (AUDIT-B's verdicts/restore-8c.md, finding 1): an entry declared 5 GiB,
    then a small one past 4 GiB. Only the plan is made, every header and the directory with its end records: the
    small entry's directory record names its true offset in its zip64 field, the zip64 end record the directory's
    true offset and length, its locator the zip64 end record's own offset, and the 32 bit end record holds 0xFFFFFFFF;
    the planned size is the sum of the parts."""
    big, small = 5 << 30, 300
    entries = [variants.ZipEntry("a.png", big, 0x1234ABCD, lambda: iter(())),
               variants.ZipEntry("a.png.json", small, 0x0BADF00D, lambda: iter(()))]
    headers, tail = variants._plan(entries, (2026, 11, 19, 10, 20, 0), variants.ZIP64_LIMIT)
    second_at = len(headers[0]) + big
    directory_at = second_at + len(headers[1]) + small
    assert second_at > 1 << 32 and directory_at > 1 << 32
    # The second entry's directory record: 46 fixed bytes, its name, then its zip64 extra holding the offset alone
    first_length = 46 + len("a.png") + 4 + 16
    record = tail[first_length:]
    assert struct.unpack("<I", record[:4]) == (0x02014B50,) and struct.unpack("<I", record[42:46]) == (0xFFFFFFFF,)
    extra = record[46 + len("a.png.json"):]
    assert struct.unpack("<HHQ", extra[:12]) == (1, 8, second_at)
    length = first_length + 46 + len("a.png.json") + 12
    zip64_end = tail[length:length + 56]
    fields = struct.unpack("<IQHHIIQQQQ", zip64_end)
    assert fields[0] == 0x06064B50 and fields[6:] == (2, 2, length, directory_at)
    locator = struct.unpack("<IIQI", tail[length + 56:length + 76])
    assert locator == (0x07064B50, 0, directory_at + length, 1)
    end = struct.unpack("<IHHHHIIH", tail[length + 76:])
    assert end == (0x06054B50, 0, 0, 2, 2, length, 0xFFFFFFFF, 0)
    assert variants.stored_zip_size(entries) == directory_at + len(tail)


def test_a_directory_past_the_limit_alone_gets_the_zip64_end_records() -> None:
    # Fifty empty entries: their local headers end before 3,000 bytes, their directory runs past 3,000
    contents = [b""] * 50
    written, expected = both_zips(contents, (2026, 11, 19, 10, 20, 0), limit=3_000)
    assert written == expected and b"PK\x06\x06" in written
    directory_start = struct.unpack("<I", written[-6:-2])[0]
    assert directory_start < 3_000


def test_more_than_65535_entries_get_the_zip64_end_records() -> None:
    contents = [b""] * 65_536
    written, expected = both_zips(contents, (2026, 11, 19, 10, 20, 0))
    assert written == expected and b"PK\x06\x06" in written
    # The end record's two counts, at 8 and 10 of its 22 bytes, are capped; the zip64 record holds the true one
    assert struct.unpack("<HH", written[-14:-10]) == (0xFFFF, 0xFFFF)
    assert len(zipfile.ZipFile(io.BytesIO(written)).infolist()) == 65_536


@settings(max_examples=60, deadline=None)
@given(sizes=st.lists(st.integers(0, 6_000), min_size=1, max_size=4))
def test_at_any_size_about_a_lowered_limit_the_zip_is_what_pythons_zipfile_writes(sizes) -> None:
    limit = 2_000
    # zipfile gives a local header zip64 from 1/1.05 of its limit, and its directory from the limit: between the two
    # its own two records disagree, which this writer does not copy
    assume(not any(limit / 1.05 < size <= limit for size in sizes))
    contents = [bytes([index]) * size for index, size in enumerate(sizes)]
    written, expected = both_zips(contents, (2026, 11, 19, 10, 20, 0), limit=limit)
    assert written == expected
    archive, read = unzip(written)
    assert [archive.read(info) for info in read] == contents


def test_a_zip_time_is_the_variants_own_utc_instant_at_two_second_steps() -> None:
    assert variants.zip_date_time("2026-11-19T06:18:43.250Z") == (2026, 11, 19, 6, 18, 42)
    assert variants.zip_date_time("2026-11-19T06:18:42Z") == (2026, 11, 19, 6, 18, 42)


def test_an_entry_that_changes_while_it_is_sent_stops_the_zip_before_its_directory() -> None:
    data = b"x" * 10_000
    crc = zlib.crc32(data) & 0xFFFFFFFF
    # Cut short; one byte changed; and nothing at all where four bytes whose CRC-32 is 0 were declared
    for size, declared, blocks in ((len(data), crc, lambda: iter((data[:-1],))), (len(data), crc, lambda: iter((data[:-1] + b"y",))),
                                   (4, 0, lambda: iter(()))):
        entry = variants.ZipEntry("a.png", size, declared, blocks)
        sent = []
        with pytest.raises(ValueError):
            for block in variants.stored_zip([entry], (2026, 11, 19, 0, 0, 0)):
                sent.append(block)
        assert b"PK\x01\x02" not in b"".join(sent) and b"PK\x05\x06" not in b"".join(sent)
