"""The export and its manifest (`services/media/router.py`, `services/media/variants.py`; restoration design section 12;
REQ-RST-9.1 to 9.7; P-RST-21; review row D22, `.lead/uiux/UIUX-export-READY.md`).

Covers, through the API on a store of its own:
- An export of a ready restoration is one zip stored without compression, an attachment named
  `Albedian restored photo.zip`, holding `Albedian restored photo.png`, the stored variant byte for byte, and
  `Albedian restored photo.png.json`, its manifest pretty printed; the manifest route answers the same manifest. The exported bytes hash to the manifest's variant hash,
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
- A restored clip, QuickTime and MP4, exports the same way: `Albedian restored clip.zip` holding
  `Albedian restored clip.mov` or `.mp4`, its stored file byte for byte (lossless, restore-7), with
  `Albedian restored clip.mov.json` or `.mp4.json`, its manifest (restore-8c's second half).
- No name a person sees (the zip's, its two entries', the manifest's alone) carries an id, a time, a place or the
  upload's own name: plain words and the file's extension (gap seven, DECIDED-BY-LEAD 13:09 on 24 Nov, beside D22);
  the manifest inside keeps its ids. Every entry carries the zip format's first date, 1980-01-01 00:00, not the
  restoration's time (DECIDED-BY-LEAD 18:04).
- The builder, given the contract fixture's own parts, writes the fixture's manifests key for key (the Vitest parity
  suite parses those under ExportManifestSchema, so the builder's shape parses in both languages), and refuses a
  manifest of a rejected attempt.
- The stored zip writer writes what Python's zipfile writes for the same entries stored, and its size is known before
  a byte is sent.
"""

from __future__ import annotations

import hashlib
import re
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
    assert answer.headers["content-disposition"] == 'attachment; filename="Albedian restored photo.zip"'
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["cache-control"] == "no-store"
    assert int(answer.headers["content-length"]) == len(answer.content)
    archive, entries = unzip(answer.content)
    assert [entry.filename for entry in entries] == ["Albedian restored photo.png", "Albedian restored photo.png.json"]
    assert all(entry.compress_type == zipfile.ZIP_STORED and entry.flag_bits == 0 for entry in entries)
    # Readable by everyone who unpacks it, whatever the store's own 0400
    assert all((entry.external_attr >> 16) == 0o100644 for entry in entries)
    stored = (folder / "variants" / vid / "variant.png").read_bytes()
    exported = archive.read("Albedian restored photo.png")
    assert exported == stored == client.get(f"/api/media/{sha}/variants/{vid}/file").content
    sidecar = archive.read("Albedian restored photo.png.json")
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
    assert alone.headers["content-disposition"] == 'attachment; filename="Albedian restored photo.png.json"'
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
        manifest = answer.json() if path == "manifest" else json.loads(unzip(answer.content)[0].read("Albedian restored photo.png.json"))
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
    exported, sidecar = archive.read("Albedian restored photo.png"), archive.read("Albedian restored photo.png.json").decode("utf-8")
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
    place, made, job_files = folder / "variants" / job["variantId"], [], []
    jobs = JobStore(IngestStore(client.store_root, max_bytes=200_000_000))

    def mark_succeeded(vid: str, idx: int) -> None:
        suffix = vid.removeprefix("var_")
        rec = jobs.write_record({
            "id": f"job_{sha[:16]}{900 + idx:016x}",
            "sourceAssetSha256": sha,
            "kind": "restoration",
            "state": "succeeded",
            "createdAtUTC": "2026-11-24T04:00:00Z",
            "startedAtUTC": "2026-11-24T04:00:00Z",
            "finishedAtUTC": "2026-11-24T04:00:01Z",
            "producer": {"name": "albedian.restore", "revision": media_jobs.MEDIA_PRODUCER.revision},
            "request": REQUEST,
            "graphId": f"graph_{suffix}",
            "attemptId": f"att_{suffix}",
            "variantId": vid,
            "stages": [],
        })
        rec_path = jobs.record_path(rec["id"])
        assert rec_path is not None
        job_files.append(rec_path)

    try:
        # A whole copy under its own consistent id and a succeeded job record exports: the cases below differ in one thing each
        ours = "var_" + "1" * 16
        renamed_copy(place, folder / "variants" / ours, ours, sha)
        made.append(folder / "variants" / ours)
        mark_succeeded(ours, 1)
        assert client.get(f"/api/media/{sha}/variants/{ours}/export").status_code == 200
        # A whole copy under its own consistent id with no succeeded job record: never exported or manifested (REQ-JOB-5.4)
        unnamed = "var_" + "6" * 16
        renamed_copy(place, folder / "variants" / unnamed, unnamed, sha)
        made.append(folder / "variants" / unnamed)
        # The folder is a link to such a copy kept outside the store
        outside, linked = tmp_path / "outside", "var_" + "2" * 16
        renamed_copy(place, outside, linked, sha)
        os.symlink(outside, folder / "variants" / linked)
        made.append(folder / "variants" / linked)
        mark_succeeded(linked, 2)
        # The folder is real, its file a link to the real variant's file
        file_linked = "var_" + "3" * 16
        renamed_copy(place, folder / "variants" / file_linked, file_linked, sha)
        made.append(folder / "variants" / file_linked)
        (folder / "variants" / file_linked / "variant.png").unlink()
        os.symlink(place / "variant.png", folder / "variants" / file_linked / "variant.png")
        mark_succeeded(file_linked, 3)
        # A real folder whose graph record is a folder, not a file
        not_a_file = "var_" + "5" * 16
        renamed_copy(place, folder / "variants" / not_a_file, not_a_file, sha)
        made.append(folder / "variants" / not_a_file)
        (folder / "variants" / not_a_file / "graph.json").unlink()
        (folder / "variants" / not_a_file / "graph.json").mkdir()
        mark_succeeded(not_a_file, 5)
        # A whole copy whose records name the variant it was copied from
        other = "var_" + "4" * 16
        shutil.copytree(place, folder / "variants" / other)
        os.chmod(folder / "variants" / other, 0o700)
        made.append(folder / "variants" / other)
        mark_succeeded(other, 4)
        for vid, names in ((unnamed, ("export", "manifest")), (linked, ("export", "manifest")),
                           (file_linked, ("export",)), (other, ("export", "manifest")),
                           (not_a_file, ("export", "manifest"))):
            for name in names:
                assert client.get(f"/api/media/{sha}/variants/{vid}/{name}").status_code == 404, (vid, name)
    finally:
        for rec_path in job_files:
            os.chmod(rec_path, 0o600)
            rec_path.unlink(missing_ok=True)
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
    assert answer.headers["content-disposition"] == 'attachment; filename="Albedian restored clip.zip"'
    assert int(answer.headers["content-length"]) == len(answer.content)
    archive, entries = unzip(answer.content)
    assert [entry.filename for entry in entries] == [f"Albedian restored clip.{extension}", f"Albedian restored clip.{extension}.json"]
    assert all(entry.compress_type == zipfile.ZIP_STORED for entry in entries)
    stored = (folder / "variants" / vid / f"variant.{extension}").read_bytes()
    exported = archive.read(f"Albedian restored clip.{extension}")
    assert exported == stored == client.get(f"/api/media/{sha}/variants/{vid}/file").content
    sidecar = archive.read(f"Albedian restored clip.{extension}.json")
    manifest = json.loads(sidecar.decode("utf-8"))
    # P-RST-21: the manifest parses and its variant hash is the SHA-256 of the exported bytes
    contracts.ExportManifest.model_validate(manifest)
    assert manifest["variant"]["mimeType"] == CLIP_TYPES[extension] and manifest["attempt"]["status"] == "ready"
    assert manifest["variant"]["sha256"] == hashlib.sha256(exported).hexdigest()
    assert manifest["variant"]["byteSize"] == len(exported)
    records = client.get(f"/api/media/{sha}/variants/{vid}").json()
    # The manifest holds the stored records: the variant served carries its playback copy composed in, and the
    # manifest never names the copy (DECIDED-BY-LEAD 22:24 on 24 Nov)
    served = {key: value for key, value in records["variant"].items() if key != "playbackCopy"}
    assert "playbackCopy" in records["variant"] and "playbackCopy" not in manifest["variant"]
    assert (manifest["variant"], manifest["graph"], manifest["attempt"]) == (served, records["graph"], records["attempt"])
    alone = client.get(f"/api/media/{sha}/variants/{vid}/manifest")
    assert alone.status_code == 200 and alone.content == sidecar
    assert alone.headers["content-disposition"] == f'attachment; filename="Albedian restored clip.{extension}.json"'
    assert client.get(f"/api/media/{sha}/variants/{vid}/export").content == answer.content
    # No planted title, comment, place, time or stream tag goes out, in the clip or in its manifest
    for value in CLIP_PLANTED:
        assert value.encode("utf-8") not in answer.content, value


FIRST_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def names_seen(client: TestClient, sha: str, vid: str) -> list[str]:
    """Every name a person sees for one export: the zip's, its entries', and the manifest's when saved alone. Every
    entry carries the zip format's first date, not the restoration's time (DECIDED-BY-LEAD 18:04 on 24 Nov), so the
    files unpacked from it do not tell when the restoration was made."""
    export = client.get(f"/api/media/{sha}/variants/{vid}/export")
    alone = client.get(f"/api/media/{sha}/variants/{vid}/manifest")
    assert export.status_code == alone.status_code == 200
    _, entries = unzip(export.content)
    assert [entry.date_time for entry in entries] == [FIRST_ZIP_DATE, FIRST_ZIP_DATE]
    disposition = re.compile(r'attachment; filename="([^"]*)"')
    return [disposition.fullmatch(export.headers["content-disposition"]).group(1), *(e.filename for e in entries),
            disposition.fullmatch(alone.headers["content-disposition"]).group(1)]


def test_no_name_a_person_sees_carries_an_id_a_time_a_place_or_the_uploads_own_name(restored, restored_clips) -> None:
    client, sha, _, job = restored
    still = names_seen(client, sha, job["variantId"])
    assert still == ["Albedian restored photo.zip", "Albedian restored photo.png", "Albedian restored photo.png.json",
                     "Albedian restored photo.png.json"]
    seen = {"png": (client, sha, job, still)}
    clips_client, done = restored_clips
    for extension, (clip_sha, _, clip_job) in done.items():
        names = names_seen(clips_client, clip_sha, clip_job["variantId"])
        assert names == ["Albedian restored clip.zip", f"Albedian restored clip.{extension}",
                         f"Albedian restored clip.{extension}.json", f"Albedian restored clip.{extension}.json"]
        seen[extension] = (clips_client, clip_sha, clip_job, names)
    for extension, (who, upload, made, names) in seen.items():
        record = who.get(f"/api/ingest/{upload}").json()
        for name in names:
            # Plain words and the extension: no digit before the extension, so no id, hash, date or time stands in it
            assert re.fullmatch(r"Albedian restored (photo|clip)\.(zip|png|mov|mp4)(\.json)?", name), name
            assert not re.search(r"\d", name.split(".", 1)[0]), name
            for value in (made["variantId"], made["attemptId"], upload[:8], PLANTED_NAME.rsplit(".", 1)[0], "planted",
                          CLIP_PLANTED_PLACE, *CLIP_PLANTED):
                assert value not in name, (name, value)
            assert json.dumps(record).find(name) == -1, (extension, name)
        # The manifest inside keeps its ids
        manifest = who.get(f"/api/media/{upload}/variants/{made['variantId']}/manifest").json()
        assert (manifest["variant"]["id"], manifest["attempt"]["id"], manifest["sourceSha256"]) == (
            made["variantId"], made["attemptId"], upload)


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
    manifest = json.loads(archive.read("Albedian restored photo.png.json"))
    contracts.ExportManifest.model_validate(manifest)
    assert manifest["disclosure"] == contracts.DISCLOSURES["display_copy"] and "gate" not in manifest["attempt"]
    exported = archive.read("Albedian restored photo.png")
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
    # Four of today's kinds, and five made before the gate's two counts or at their version (gate-flux-floor-zero v2)
    assert len(manifests) == 9
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


# playback-job-one (clip-display-copy, DECIDED-BY-LEAD 21:57 and 22:05 on 24 Nov): a restored clip gets a playback copy
# beside its lossless file, H.264 High with a keyframe every half second and its index first, kept only when every frame
# is at least 45 dB of PSNR from the lossless file; the lossless file stays the variant's file, its download and its
# evidence.
def mp4_boxes(data: bytes) -> list[str]:
    boxes, pos = [], 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        boxes.append(kind.decode("latin-1"))
        if size < 8:
            break
        pos += size
    return boxes


def ffprobe_json(path: Path, *args: str) -> dict:
    import subprocess
    run = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)], capture_output=True, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-400:]
    return json.loads(run.stdout)


@pytest.mark.parametrize("extension", sorted(CLIP_TYPES))
def test_a_restored_clip_has_a_playback_copy_every_browser_draws_beside_its_lossless_file(restored_clips, extension: str,
                                                                                          tmp_path) -> None:
    client, done = restored_clips
    sha, folder, job = done[extension]
    vid = job["variantId"]
    records = client.get(f"/api/media/{sha}/variants/{vid}").json()
    variant = records["variant"]
    copy = variant["playbackCopy"]
    assert copy["uri"] == f"/api/media/{sha}/variants/{vid}/playback" and copy["mimeType"] == "video/mp4"
    assert media_jobs.NOTE_NO_PLAYBACK not in records["attempt"]["measured"]["notes"]
    assert copy["minPsnrDb"] >= contracts.PLAYBACK_COPY_MIN_PSNR_DB
    # The variant's own record never names the copy; the copy's record is its own file beside it, both 0400, and the
    # variant served is the stored record with the copy composed in (DECIDED-BY-LEAD 22:24 on 24 Nov)
    stored = folder / "variants" / vid
    record = json.loads((stored / "variant.json").read_bytes())
    assert "playbackCopy" not in record and variant == {**record, "playbackCopy": copy}
    assert json.loads((stored / "playback.json").read_bytes()) == copy
    assert all(os.stat(stored / name).st_mode & 0o777 == 0o400 for name in ("playback.json", "playback.mp4"))
    served = client.get(copy["uri"])
    assert served.status_code == 200 and served.headers["content-type"] == "video/mp4"
    assert (hashlib.sha256(served.content).hexdigest(), len(served.content)) == (copy["sha256"], copy["byteSize"])
    assert (stored / "playback.mp4").read_bytes() == served.content
    # The index first, as a browser seeks soonest
    boxes = mp4_boxes(served.content)
    assert boxes.index("moov") < boxes.index("mdat"), boxes
    local = tmp_path / "playback.mp4"
    local.write_bytes(served.content)
    stream = ffprobe_json(local, "-select_streams", "v:0", "-show_entries", "stream=profile,pix_fmt,level,nb_frames")["streams"][0]
    assert (stream["profile"], stream["pix_fmt"]) == ("High", "yuv420p")
    assert copy["codecs"] == f"avc1.6400{stream['level']:02X}"
    assert int(stream["nb_frames"]) == variant["frameCount"]
    # A keyframe at least every half second (15 frames at this clip's 30 a second)
    keys = [i for i, f in enumerate(ffprobe_json(local, "-select_streams", "v:0", "-show_entries", "frame=key_frame")["frames"])
            if f["key_frame"] == 1]
    assert keys[0] == 0 and all(b - a <= 15 for a, b in zip(keys, keys[1:] + [variant["frameCount"]]))
    # The lossless file is still the variant's own file and its export
    lossless = client.get(f"/api/media/{sha}/variants/{vid}/file").content
    assert hashlib.sha256(lossless).hexdigest() == variant["sha256"] != copy["sha256"]
    archive, entries = unzip(client.get(f"/api/media/{sha}/variants/{vid}/export").content)
    assert archive.read(entries[0].filename) == lossless and len(entries) == 2
    # The export and its manifest never name the copy: an export is the same before and after one is made
    assert not any(b"playback" in archive.read(entry.filename).lower() for entry in entries[1:])
    assert not any("playback" in entry.filename.lower() for entry in entries)
    # The write stage had no total while the copy was made: the web shows the time passed
    write = next(s for s in job["stages"] if s["stage"] == "write")
    assert write["total"] is None and write["done"] == variant["byteSize"] + copy["byteSize"]


def test_a_restored_still_has_no_playback_copy(restored) -> None:
    client, sha, folder, job = restored
    vid = job["variantId"]
    assert "playbackCopy" not in client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]
    answer = client.get(f"/api/media/{sha}/variants/{vid}/playback")
    assert (answer.status_code, answer.json()) == (404, {"detail": "that variant has no playback copy"})
    assert not (folder / "variants" / vid / "playback.mp4").exists()


def test_a_playback_copy_below_its_gate_is_not_kept_and_one_above_it_keeps_the_display_orientation(restored_clips, tmp_path) -> None:
    from services.media import clip_io
    client, done = restored_clips
    sha, folder, job = done["mp4"]
    stored = tmp_path / "stored.mp4"
    stored.write_bytes((folder / "variants" / job["variantId"] / "variant.mp4").read_bytes())
    probe = clip_io.probe_clip(stored)
    # A floor no lossy copy meets: no copy, nothing left behind
    assert clip_io.make_playback_copy(stored, tmp_path / "a.encoded", tmp_path / "a.mp4", probe, None, threads=1, min_psnr_db=99) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["stored.mp4"]
    # The floor of the contract: a copy, turned as the lossless file is turned
    turned = {"quarterTurnsClockwise": 1, "mirrored": False}
    made = clip_io.make_playback_copy(stored, tmp_path / "b.encoded", tmp_path / "b.mp4", probe, turned, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    assert made is not None and made.path == tmp_path / "b.mp4" and not (tmp_path / "b.encoded").exists()
    assert clip_io.read_display_orientation(made.path, written_here=True) == turned
    assert made.min_psnr_db >= contracts.PLAYBACK_COPY_MIN_PSNR_DB and re.fullmatch(r"avc1\.64[0-9A-F]{4}", made.codecs)
    assert clip_io.keyframe_interval(probe) == 15


def test_a_playback_copy_keeps_the_lossless_files_own_pixels_and_turn_and_a_keyframe_every_half_second(tmp_path) -> None:
    import subprocess
    from services.media import clip_io
    # Synthetic: two seconds of the clip fixture's dark sky at 30 a second, 96 by 64, lossless as the restore stores it,
    # carrying a quarter turn in its display matrix
    raw = tmp_path / "raw.mp4"
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", CLIP_SKY.replace("d=0.5", "d=2"),
                           "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(raw)], capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]
    turned = {"quarterTurnsClockwise": 1, "mirrored": False}
    stored = clip_io.with_display_orientation(raw, tmp_path / "stored.mp4", turned, container="mp4")
    probe = clip_io.probe_clip(stored)
    copy = clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, turned, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    assert copy is not None
    stream = ffprobe_json(copy.path, "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames")["streams"][0]
    # The pixels as the lossless file holds them (not turned by its matrix), the same turn in the copy's own matrix
    assert (stream["width"], stream["height"], int(stream["nb_frames"])) == (96, 64, 60)
    assert clip_io.read_display_orientation(copy.path, written_here=True) == turned
    keys = [i for i, f in enumerate(ffprobe_json(copy.path, "-select_streams", "v:0", "-show_entries", "frame=key_frame")["frames"])
            if f["key_frame"] == 1]
    # A keyframe at least every half second: the encoder may place one sooner, never later
    assert keys[0] == 0 and len(keys) >= 4 and all(b - a <= 15 for a, b in zip(keys, keys[1:] + [60]))
    # Grain the restore keeps fails CRF 18's gate and is kept at a lower CRF of the ladder
    assert copy.min_psnr_db >= contracts.PLAYBACK_COPY_MIN_PSNR_DB


def test_a_variants_record_never_names_its_playback_copy(tmp_path) -> None:
    source = tmp_path / "store" / "ab" / ("ab" * 32)
    source.mkdir(parents=True, mode=0o700)
    records = {"variant": {"id": "var_" + "1" * 16, "mimeType": "image/png", "playbackCopy": {}}, "graph": {}, "attempt": {}}
    with pytest.raises(ValueError, match="never names its playback copy"):
        variants.publish_variant(tmp_path / "store", source, b"png", records)
    assert not (source / "variants" / ("var_" + "1" * 16)).exists()


def test_a_copy_made_after_its_restoration_was_stored_is_placed_and_served_by_the_same_path(restored_clips, tmp_path) -> None:
    """One path for new and stored restorations (DECIDED-BY-LEAD 22:24 on 24 Nov): the writer the job uses puts a copy
    beside a variant already published, and the route serves it; each refusal leaves the folder as it was."""
    client, done = restored_clips
    sha, folder, job = done["mp4"]
    vid = job["variantId"]
    stored = folder / "variants" / vid
    served = client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]
    copy, data = served["playbackCopy"], (stored / "playback.mp4").read_bytes()
    variant = json.loads((stored / "variant.json").read_bytes())
    aside = tmp_path / "aside.mp4"
    os.rename(stored / "playback.mp4", aside)
    os.rename(stored / "playback.json", tmp_path / "aside.json")
    names = sorted(p.name for p in stored.iterdir())

    def loose(name: str, content: bytes) -> Path:
        path = tmp_path / name
        path.write_bytes(content)
        return path

    try:
        # As a restoration stored before the copy: no copy served
        assert "playbackCopy" not in client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"]
        assert client.get(copy["uri"]).status_code == 404
        # A record the contract refuses on this variant: nothing moved
        with pytest.raises(ValidationError):
            variants.place_playback(stored, variant, variants.PlaybackFile({**copy, "minPsnrDb": 20}, aside))
        assert aside.exists() and sorted(p.name for p in stored.iterdir()) == names
        # A file that is not the one its record names: refused, the folder as it was
        other = loose("other.mp4", data[:-1] + bytes([data[-1] ^ 1]))
        with pytest.raises(ValueError, match="the file its record names"):
            variants.place_playback(stored, variant, variants.PlaybackFile(copy, other))
        assert sorted(p.name for p in stored.iterdir()) == names
        # A record already at its name: refused, the moved file taken out again, the record never before its file
        (stored / "playback.json").write_bytes(b"{}")
        with pytest.raises(FileExistsError):
            variants.place_playback(stored, variant, variants.PlaybackFile(copy, loose("early.mp4", data)))
        (stored / "playback.json").unlink()
        assert sorted(p.name for p in stored.iterdir()) == names
        # A file at its name with no record (a placing cut short): refused, and no record written for it
        (stored / "playback.mp4").write_bytes(data)
        with pytest.raises(FileExistsError):
            variants.place_playback(stored, variant, variants.PlaybackFile(copy, loose("late.mp4", data)))
        assert not (stored / "playback.json").exists()
        (stored / "playback.mp4").unlink()
        assert sorted(p.name for p in stored.iterdir()) == names
        # Placed: served as the job's own copy was, the variant's record untouched
        variants.place_playback(stored, variant, variants.PlaybackFile(copy, aside))
        assert client.get(f"/api/media/{sha}/variants/{vid}").json()["variant"] == served
        answer = client.get(copy["uri"])
        assert answer.status_code == 200 and answer.content == data
        assert json.loads((stored / "variant.json").read_bytes()) == variant
        # A second copy onto the first: refused, the first kept and served
        again = loose("again.mp4", data)
        with pytest.raises(FileExistsError):
            variants.place_playback(stored, variant, variants.PlaybackFile(copy, again))
        assert again.exists() and client.get(copy["uri"]).content == data
    finally:
        if not (stored / "playback.mp4").exists():
            os.rename(aside, stored / "playback.mp4")
        if not (stored / "playback.json").exists():
            os.rename(tmp_path / "aside.json", stored / "playback.json")


# playback-job-one version three (AUDIT's verdict on version two; DECIDED-BY-LEAD 02:22 on 25 Nov): the gate compares
# the copy with the lossless frames in the stored file's own format, and a clip with no copy is still restored, its
# record saying so. AUDIT's clip: ffmpeg's test pattern with pixel-scale colour detail (synthetic), stored lossless.
GATE_PATTERN = "testsrc2=size=160x96:rate=30:duration=1"
GATE_CHECKS = ("nullsrc=size=160x96:rate=30:duration=1,geq=lum='128':cb='if(mod(X+Y,2),40,216)'"
               ":cr='if(mod(floor(X/2)+Y,2),216,40)',format=yuv444p")


def gate_clip(path: Path, pixel_format: str) -> None:
    import subprocess
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", GATE_PATTERN, "-f", "lavfi", "-i",
                           GATE_CHECKS, "-filter_complex",
                           f"[0:v]format=yuv444p[a];[1:v]format=yuv444p[b];[a][b]blend=all_mode=average,format={pixel_format}",
                           "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", pixel_format, str(path)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]


def lowest_psnr_in(copy: Path, stored: Path, pixel_format: str, frames: int) -> float:
    """An independent measure: both decoded to raw samples in the stored format (the copy brought to it by ffmpeg's
    bicubic scaler, the stored frames as they are), each frame's PSNR over all its samples, the lowest."""
    import subprocess

    ten = pixel_format.endswith("10le")

    def raw(path: Path) -> np.ndarray:
        run = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-noautorotate", "-i", str(path), "-sws_flags", "bicubic",
                              "-pix_fmt", pixel_format, "-f", "rawvideo", "-"], capture_output=True, stdin=subprocess.DEVNULL)
        assert run.returncode == 0, run.stderr[-400:]
        return np.frombuffer(run.stdout, dtype="<u2" if ten else np.uint8).reshape(frames, -1).astype(np.float64)

    a, b = raw(copy), raw(stored)
    mse = ((a - b) ** 2).mean(axis=1)
    peak = 1023.0 if ten else 255.0
    return float(min(10 * np.log10(peak ** 2 / m) for m in mse if m > 0))


@pytest.mark.parametrize("pixel_format", ["yuv420p", "yuvj420p", "yuv444p", "yuv420p10le"])
def test_the_playback_gate_measures_the_copy_in_the_stored_files_own_format(tmp_path, pixel_format: str) -> None:
    from services.media import clip_io
    stored = tmp_path / "stored.mp4"
    gate_clip(stored, pixel_format)
    probe = clip_io.probe_clip(stored)
    assert clip_io.stored_pixel_format(stored) == pixel_format and probe.frame_count == 30
    # The gate's own measure of one encode is the independent one, in the stored format
    clip_io._encode_playback(stored, tmp_path / "one.encoded", tmp_path / "one.mp4", probe, None, 18, 1, 120,
                             pixel_format.endswith("10le"))
    gate_lowest = min(clip_io._psnr_frames(tmp_path / "one.mp4", stored, pixel_format, 120))
    assert gate_lowest == pytest.approx(lowest_psnr_in(tmp_path / "one.mp4", stored, pixel_format, 30), abs=0.05)
    made = clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, None, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    if pixel_format == "yuv444p":
        # Its chroma cut to 4:2:0, the copy is far from the lossless frames at every quality: no copy, nothing left
        assert gate_lowest < contracts.PLAYBACK_COPY_MIN_PSNR_DB and made is None
        assert not (tmp_path / "c.mp4").exists() and not (tmp_path / "c.encoded").exists()
    else:
        assert made is not None
        assert made.min_psnr_db == pytest.approx(lowest_psnr_in(made.path, stored, pixel_format, 30), abs=0.05)
        assert made.min_psnr_db >= contracts.PLAYBACK_COPY_MIN_PSNR_DB
        # A keyframe at least every half second (15 frames at 30 a second), in either codec: packets flagged K
        import subprocess
        flags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=flags,pts",
                                "-of", "csv=p=0", str(made.path)], capture_output=True, text=True).stdout.split()
        keys = sorted(int(entry.split(",")[0]) for entry in flags if "K" in entry.split(",")[-1])
        assert len(keys) >= 2, flags
        # Its own bit depth: H.264 High for 8 bits, HEVC Main 10 for 10, its string read from its own hvcC
        if pixel_format.endswith("10le"):
            assert made.codecs == clip_io.hevc_codec_string(made.path) and made.codecs.startswith("hvc1.2.4.L")
        else:
            assert made.codecs.startswith("avc1.64")


def test_a_stored_format_the_gate_does_not_measure_in_gets_no_copy(tmp_path, monkeypatch) -> None:
    # The gate measures 8- and 10-bit planar Y'CbCr only; the probe refuses a clip over 10 bits before any copy, so a
    # stored format outside the list is given here through the one reading the copy takes it from (synthetic clip)
    from services.media import clip_io
    stored = tmp_path / "plain.mp4"
    gate_clip(stored, "yuv420p")
    probe = clip_io.probe_clip(stored)
    monkeypatch.setattr(clip_io, "stored_pixel_format", lambda path: "yuv420p12le")
    assert clip_io.make_playback_copy(stored, tmp_path / "g.encoded", tmp_path / "g.mp4", probe, None,
                                      threads=1, min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.mp4"]


def restore_clip_in_child(tmp_path: Path, source: Path, extension: str = "mp4") -> tuple[dict, Path, Path]:
    """One clip restored in this process as the job's child restores it, with the playback partials the job passes:
    its result, its upload's folder and its store."""
    root = tmp_path / "store"
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    folder = root / sha[:2] / sha
    folder.mkdir(parents=True)
    original = folder / f"original.{extension}"
    original.write_bytes(source.read_bytes())
    (root / ".partial").mkdir()
    job_id = "job_" + "d" * 16
    partials = [str(root / ".partial" / f"{job_id}-{name}.part")
                for name in (f"clip_encoded.{extension}", f"variant.{extension}", "playback_encoded.mp4", "playback.mp4")]
    facts = {"mimeType": "video/mp4", "durationMs": 500.0, "audioStreams": []}
    out = media_jobs.clip_restoration_in_child(str(root), str(folder), str(original), sha, job_id, {"kind": "restoration"},
                                               None, facts, partials, lambda *args: None, workers=1)
    return out, folder, root


NOTE_NO_COPY = "No playback copy could be made within its gate, so this restoration has none."
# Each way a clip ends with no copy, and whether its record says the gate refused one (DECIDED-BY-LEAD 04:47 on 25 Nov:
# only where a copy was tried and its gate refused it)
NO_COPY_CASES = [("no quality passes", True), ("the copy stops at its time limit", False),
                 ("the copy raises anything else", False), ("the stored format is never measured", False),
                 ("the format gets none", False), ("its record is refused", False),
                 ("its record cannot be built", False), ("it cannot be placed", False)]


@pytest.mark.parametrize("why, noted", NO_COPY_CASES)
def test_a_clip_with_no_playback_copy_is_still_restored_and_says_so_only_where_the_gate_refused_it(
        tmp_path, monkeypatch, why: str, noted: bool) -> None:
    import subprocess
    from services.media import clip_io
    source = tmp_path / "sky.mp4"
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", CLIP_SKY, "-c:v", "libx264",
                           "-pix_fmt", "yuv420p", str(source)], capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]

    def raising(error):
        def raise_it(*args, **kwargs):
            raise error
        return raise_it

    def high_444(stored, encoded, out, *args, **kwargs):
        # A made copy whose record the contract refuses (High 4:4:4 Predictive's codec string): no copy is placed
        Path(out).write_bytes(b"made")
        return clip_io.PlaybackCopy(Path(out), "avc1.F40028", 50.0)

    if why == "no quality passes":
        monkeypatch.setattr(media_jobs, "PLAYBACK_COPY_MIN_PSNR_DB", 99)
    elif why == "the copy stops at its time limit":
        monkeypatch.setattr(clip_io, "make_playback_copy", raising(clip_io.ClipError("time_limit")))
    elif why == "the copy raises anything else":
        monkeypatch.setattr(clip_io, "make_playback_copy", raising(ValueError("a psnr line this does not read")))
    elif why == "the stored format is never measured":
        monkeypatch.setattr(clip_io, "stored_pixel_format", lambda stored: "nv12")
    elif why == "the format gets none":
        monkeypatch.setattr(clip_io, "playback_copy_supported", lambda video: False)
    elif why == "its record is refused":
        monkeypatch.setattr(clip_io, "make_playback_copy", high_444)
    elif why == "its record cannot be built":
        monkeypatch.setattr(media_jobs, "playback_uri", raising(RuntimeError("the record could not be built")))
    else:
        monkeypatch.setattr(variants, "place_playback", raising(OSError("the store could not take the copy")))
    out, folder, root = restore_clip_in_child(tmp_path, source)
    assert out["status"] == "ready", out
    stored = folder / "variants" / out["variantId"]
    assert sorted(p.name for p in stored.iterdir()) == ["attempt.json", "graph.json", "variant.json", "variant.mp4"]
    assert "playbackCopy" not in json.loads((stored / "variant.json").read_bytes())
    attempt = json.loads((stored / "attempt.json").read_bytes())
    contracts.ProcessingAttempt.model_validate(attempt)
    assert attempt["measured"]["notes"].count(NOTE_NO_COPY) == (1 if noted else 0)
    assert list((root / ".partial").iterdir()) == []


def test_a_ten_bit_clip_is_restored_with_its_copy_in_its_own_bit_depth(tmp_path) -> None:
    # playback-tenbit-job (DECIDED-BY-LEAD 07:16 on 25 Nov): a 10-bit clip (stored as HEVC, as an iPhone's HDR clip is)
    # gets its copy in HEVC Main 10, gated in its stored format, and no note (synthetic: the clip fixture's sky in 10 bits)
    import subprocess
    from services.media import clip_io
    source = tmp_path / "deep.mp4"
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", CLIP_SKY, "-c:v", "libx265",
                           "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(source)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]
    assert clip_io.playback_copy_supported(clip_io.probe_clip(source).video)
    out, folder, root = restore_clip_in_child(tmp_path, source)
    assert out["status"] == "ready", out
    stored = folder / "variants" / out["variantId"]
    copy = json.loads((stored / "playback.json").read_bytes())
    assert copy["codecs"] == clip_io.hevc_codec_string(stored / "playback.mp4") and copy["codecs"].startswith("hvc1.2.4.L")
    assert copy["minPsnrDb"] >= contracts.PLAYBACK_COPY_MIN_PSNR_DB
    variant = json.loads((stored / "variant.json").read_bytes())
    contracts.MediaVariant.model_validate({**variant, "playbackCopy": copy})
    attempt = json.loads((stored / "attempt.json").read_bytes())
    assert NOTE_NO_COPY not in attempt["measured"]["notes"]
    assert list((root / ".partial").iterdir()) == []


def test_a_ten_bit_copys_codec_string_is_read_from_its_own_stream_and_never_names_level_8_5(tmp_path) -> None:
    # The RFC 6381 string from the hvcC box (synthetic streams): Main 10 at a level a decoder names, and none for an
    # exact x265 stream, which reports level 8.5, nor for an 8-bit HEVC Main one
    import subprocess
    from services.media import clip_io

    def hevc(name: str, *options: str) -> Path:
        path = tmp_path / name
        made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", GATE_PATTERN, "-c:v", "libx265",
                               *options, "-tag:v", "hvc1", str(path)], capture_output=True, stdin=subprocess.DEVNULL)
        assert made.returncode == 0, made.stderr[-400:]
        return path

    assert clip_io.hevc_codec_string(hevc("main10.mp4", "-x265-params", "log-level=error", "-crf", "18", "-pix_fmt",
                                          "yuv420p10le")) == "hvc1.2.4.L30.90"
    assert clip_io.hevc_codec_string(hevc("exact.mp4", "-x265-params", "log-level=error:lossless=1", "-pix_fmt", "yuv420p10le")) is None
    assert clip_io.hevc_codec_string(hevc("main.mp4", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p")) is None


# playback-tenbit-job version two (AUDIT-B's verdict of 25 Nov): a 10-bit clip of one frame, or of fewer than 3 frames a
# second, has a keyframe interval of one frame, and libx265 at keyint 1 writes Main 10 Intra, a Range Extensions profile
# (hvc1.4) with no codec string the contract takes; the copy keeps Main 10 (synthetic clips, AUDIT-B's three rates)
SLOW_CLIPS = [("one frame at 30 a second", "testsrc2=size=160x96:rate=30", 1),
              ("two frames at 2 a second", "testsrc2=size=160x96:rate=2", 2),
              ("five frames at 1 a second", "testsrc2=size=160x96:rate=1", 5)]
TEN_BIT_EXACT = ("-c:v", "libx265", "-x265-params", "log-level=error:lossless=1", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1")
EIGHT_BIT_EXACT = ("-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p")


def slow_clip(path: Path, source: str, frames: int, *codec: str) -> Path:
    import subprocess
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", source, "-frames:v", str(frames),
                           *codec, str(path)], capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]
    return path


def keyframes_in_order(path: Path) -> list[bool]:
    """Whether each of the copy's video packets is a keyframe, in presentation order."""
    import subprocess
    lines = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts,flags", "-of",
                            "csv=p=0", str(path)], capture_output=True, text=True).stdout.split()
    return [key for _, key in sorted((int(line.split(",")[0]), "K" in line.split(",")[1]) for line in lines)]


@pytest.mark.parametrize("why, source, frames", SLOW_CLIPS)
def test_a_ten_bit_clip_of_one_frame_or_a_slow_rate_keeps_main_10_in_its_copy(tmp_path, why: str, source: str,
                                                                               frames: int) -> None:
    from services.media import clip_io
    stored = slow_clip(tmp_path / "slow.mp4", source, frames, *TEN_BIT_EXACT)
    probe = clip_io.probe_clip(stored)
    assert probe.frame_count == frames and clip_io.keyframe_interval(probe) == 1
    made = clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, None, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    assert made is not None, why
    assert made.codecs == clip_io.hevc_codec_string(made.path) and made.codecs.startswith("hvc1.2.4.L")
    assert re.fullmatch(contracts.MEDIA_PATTERNS["playbackCodecs"], made.codecs)
    assert made.min_psnr_db == pytest.approx(lowest_psnr_in(made.path, stored, "yuv420p10le", frames), abs=0.05)
    # Every frame is kept, the first a keyframe, and never two frames in a row without one
    keys = keyframes_in_order(made.path)
    assert len(keys) == frames and keys[0] and "FF" not in "".join("K" if k else "F" for k in keys), keys


def test_an_eight_bit_clip_of_one_frame_keeps_h264_high_in_its_copy(tmp_path) -> None:
    # The 8-bit path at a keyframe interval of one frame, beside the 10-bit one (synthetic clip)
    from services.media import clip_io
    stored = slow_clip(tmp_path / "one.mp4", SLOW_CLIPS[0][1], 1, *EIGHT_BIT_EXACT)
    probe = clip_io.probe_clip(stored)
    made = clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, None, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    assert made is not None and made.codecs.startswith("avc1.64")
    assert re.fullmatch(contracts.MEDIA_PATTERNS["playbackCodecs"], made.codecs)
    assert keyframes_in_order(made.path) == [True]


def test_a_ten_bit_copy_whose_stream_is_not_main_10_is_refused_as_its_gate_refuses_never_as_an_internal_error(
        tmp_path, monkeypatch) -> None:
    # The encoder as version one called it (keyint 1): libx265 writes Main 10 Intra, and the copy is not kept, the
    # answer the gate's refusal gives, with nothing left behind (synthetic one-frame clip)
    from services.media import clip_io
    stored = slow_clip(tmp_path / "one.mp4", SLOW_CLIPS[0][1], 1, *TEN_BIT_EXACT)
    probe = clip_io.probe_clip(stored)
    monkeypatch.setattr(clip_io, "HEVC_MIN_KEYINT", 1, raising=False)
    assert clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, None, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["one.mp4"]


def test_a_ten_bit_codec_string_is_read_only_from_an_hvc1_sample_entry(tmp_path) -> None:
    # AUDIT-B's note: the string names hvc1, so the same Main 10 stream in an hev1 sample entry (its parameter sets in
    # the samples, a form the copy is never written in) has none; in hvc1 it has one (synthetic streams)
    import subprocess
    from services.media import clip_io
    strings = {}
    for tag in ("hvc1", "hev1"):
        path = tmp_path / f"{tag}.mp4"
        made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", GATE_PATTERN, "-c:v", "libx265",
                               "-x265-params", "log-level=error", "-crf", "18", "-pix_fmt", "yuv420p10le", "-tag:v", tag, str(path)],
                              capture_output=True, stdin=subprocess.DEVNULL)
        assert made.returncode == 0, made.stderr[-400:]
        strings[tag] = clip_io.hevc_codec_string(path)
    assert strings == {"hvc1": "hvc1.2.4.L30.90", "hev1": None}


def test_a_ten_bit_copy_at_30_frames_a_second_still_has_a_keyframe_every_half_second(tmp_path) -> None:
    # The floor of two frames reaches only a clip slower than 3 frames a second (synthetic clip, 30 frames at 30)
    from services.media import clip_io
    stored = slow_clip(tmp_path / "thirty.mp4", "testsrc2=size=160x96:rate=30", 30, *TEN_BIT_EXACT)
    probe = clip_io.probe_clip(stored)
    assert clip_io.keyframe_interval(probe) == 15
    made = clip_io.make_playback_copy(stored, tmp_path / "c.encoded", tmp_path / "c.mp4", probe, None, threads=1,
                                      min_psnr_db=contracts.PLAYBACK_COPY_MIN_PSNR_DB)
    assert made is not None
    assert [i for i, key in enumerate(keyframes_in_order(made.path)) if key] == [0, 15]


def test_an_hvcc_box_outside_its_sample_entry_names_no_codec_string(tmp_path) -> None:
    # The same Main 10 file with its sample entry's size cut to its own header: the hvcC box after it is no longer in
    # the entry, and nothing is named (synthetic stream, its bytes edited)
    import subprocess
    from services.media import clip_io
    path = tmp_path / "main10.mp4"
    made = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", GATE_PATTERN, "-c:v", "libx265",
                           "-x265-params", "log-level=error", "-crf", "18", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(path)],
                          capture_output=True, stdin=subprocess.DEVNULL)
    assert made.returncode == 0, made.stderr[-400:]
    assert clip_io.hevc_codec_string(path) == "hvc1.2.4.L30.90"
    data = bytearray(path.read_bytes())
    stsd = data.find(b"stsd")
    data[stsd + 12:stsd + 16] = (8).to_bytes(4, "big")
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(bytes(data))
    assert clip_io.hevc_codec_string(cut) is None


# A store kept from main at restore revision 16 (927612bd3), the last before the gate's two counts: a small synthetic
# still and a small synthetic clip, each restored ready through the API by `.lanetools/make_old_store.py` of the
# RESTORE-CLIP lane, its records exactly as that revision wrote them (AUDIT-B's verdict gate-flux-floor-zero)
KEPT_BEFORE_COUNTS = ROOT / "tests" / "fixtures" / "stored_before_gate_counts"
GATE_COUNTS = ("starsNotMeasured", "framesFluxNotChecked")
KEPT_PIPELINE_VERSION = "1.16.0"


@pytest.fixture(scope="module")
def kept_before_counts(tmp_path_factory):
    """A copy of the kept store, served by this tree's routes: the client and the store's root."""
    store_root = tmp_path_factory.mktemp("kept-before-counts") / "store"
    shutil.copytree(KEPT_BEFORE_COUNTS, store_root)
    uploads = IngestStore(store_root, max_bytes=200_000_000)
    app = create_app()
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: uploads
    with TestClient(app) as client:
        yield client, store_root


def test_a_restoration_stored_before_the_gates_counts_still_exports_as_it_was_written(kept_before_counts) -> None:
    # A restoration a person already has keeps exporting: its manifest is its three stored records as they were
    # written, and its export holds its stored file byte for byte (gate-flux-floor-zero v2)
    client, store_root = kept_before_counts
    kinds = []
    for folder in sorted(p for p in store_root.glob("*/*") if p.is_dir()):
        sha = folder.name
        (variant,) = (folder / "variants").iterdir()
        record, graph, attempt = (json.loads((variant / name).read_bytes()) for name in ("variant.json", "graph.json", "attempt.json"))
        # Made at revision 16, before the two counts, and holding neither
        assert graph["pipelineVersion"] == KEPT_PIPELINE_VERSION, graph["pipelineVersion"]
        assert not set(GATE_COUNTS) & set(attempt["gate"]), attempt["gate"]
        manifest = client.get(f"/api/media/{sha}/variants/{variant.name}/manifest")
        assert manifest.status_code == 200, manifest.text
        body = manifest.json()
        assert (body["variant"], body["graph"], body["attempt"]) == (record, graph, attempt)
        contracts.ExportManifest.model_validate(body)
        export = client.get(f"/api/media/{sha}/variants/{variant.name}/export")
        assert export.status_code == 200, export.text
        archive, entries = unzip(export.content)
        assert archive.read(entries[0].filename) == (variant / variants.VARIANT_FILES[record["mimeType"]]).read_bytes()
        assert archive.read(entries[1].filename) == manifest.content
        kinds.append(record["mimeType"])
    assert sorted(kinds) == ["image/png", "video/quicktime"]


def test_this_pipeline_writes_the_gates_counts_so_its_restorations_must_carry_them() -> None:
    # The manifest asks the two counts of every restoration made at GATE_COUNTS_SINCE or later: this pipeline is one
    since = tuple(int(part) for part in contracts.GATE_COUNTS_SINCE.split("."))
    assert tuple(int(part) for part in variants.PIPELINE_VERSION.split(".")) >= since
    # and the kept store's was before it
    assert tuple(int(part) for part in KEPT_PIPELINE_VERSION.split(".")) < since
