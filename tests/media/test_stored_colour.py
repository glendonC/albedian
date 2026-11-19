"""A stored still's record names the colour profile its own file holds (`services/media/photo.py` `stored_colour` and
the fixed illuminant of `profile_without_text`; REQ-RST-9.4; restore-8d, asked of the lead on 2026-11-19 in
EXPORT-restore-8b-QUESTION.md).

Covers:
- The colour description of a stored file: the decoded one with its profile hash naming the rebuilt profile, the
  decoded one unchanged when the original has no profile, and None when the original has a profile that cannot be
  rebuilt from its colour data (no description is then true of a file without one).
- The rebuilt header's illuminant is always the ICC's D50, whatever bytes the original holds there, as Little CMS
  writes it in its own sRGB.
- A display copy (8 and 16 bit) and a restoration name the profile in their own PNG; the attempt's measured colour
  keeps naming the original's, which it describes; a profile that cannot be rebuilt fails the attempt with the
  contract's sentence for an undecodable file and stores no variant, a restoration before any restoring.
- An export's manifest names the profile of the PNG beside it.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import icc_profiles as icc
from photo_frames import star_field
from services.media import decode, jobs as media_jobs, photo, png16, variants

JOB_ID = "job_" + "d" * 16
# The ICC's D50 as an XYZNumber, as Little CMS writes it in its own profiles' headers [tested below on its sRGB]
D50 = bytes.fromhex("0000f6d6" "00010000" "0000d32d")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lab_connected(profile: bytes) -> bytes:
    """`profile` with a Lab connection space: still decodable, since the decoder reads only its tone curves, but not
    a profile `profile_without_text` rebuilds."""
    return profile[:20] + b"Lab " + profile[24:]


def fixed_clock():
    moments = iter(datetime(2031, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=250 * k) for k in range(1000))
    return lambda: next(moments)


def stored_original(tmp_path: Path, data: bytes) -> tuple[Path, Path, Path, str]:
    digest = sha(data)
    folder = tmp_path / "store" / digest[:2] / digest
    folder.mkdir(parents=True)
    (folder / "original.png").write_bytes(data)
    return tmp_path / "store", folder, folder / "original.png", digest


def png_with_profile(rgb: np.ndarray, profile: bytes) -> bytes:
    if rgb.dtype == np.uint16:
        return png16.write_png16(rgb, profile)
    out = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(out, "PNG", icc_profile=profile)
    return out.getvalue()


def profile_in(path: Path) -> bytes:
    with Image.open(path) as image:
        return image.info["icc_profile"]


PLANTED = icc.planted(icc.srgb(), {key: f"planted {key} 81d" for key in ("desc", "cprt", "dmnd", "dmdd", "vued", "mmod", "meta")})


def test_the_stored_description_names_the_rebuilt_profile() -> None:
    colour = {"model": "rgb", "transfer": "icc_parametric", "primaries": "icc", "iccProfileSha256": sha(PLANTED)}
    out = photo.stored_colour(colour, PLANTED)
    assert out == {**colour, "iccProfileSha256": sha(photo.profile_without_text(PLANTED))}
    assert out["iccProfileSha256"] != sha(PLANTED) and colour["iccProfileSha256"] == sha(PLANTED)


def test_with_no_profile_the_description_is_the_decoded_one() -> None:
    colour = {"model": "rgb", "transfer": "srgb", "primaries": "bt709"}
    for none in (None, b""):
        assert photo.stored_colour(colour, none) == colour


def test_a_profile_it_cannot_rebuild_gives_no_description() -> None:
    lab = lab_connected(PLANTED)
    assert photo.profile_without_text(lab) is None
    assert photo.stored_colour({"transfer": "icc_parametric", "iccProfileSha256": sha(lab)}, lab) is None


def test_the_illuminant_is_always_d50_as_little_cms_writes_it() -> None:
    base = icc.srgb()
    assert base[68:80] == D50
    planted = base[:68] + b"PLANTED-0xC0" + base[80:]
    out = photo.profile_without_text(planted)
    assert out[68:80] == D50
    assert out == photo.profile_without_text(base)
    assert out[84:100] == icc.profile_id(out)


@pytest.mark.parametrize("bits", [8, 16])
def test_a_display_copy_names_the_profile_in_its_own_file_and_its_attempt_the_originals(tmp_path: Path, bits: int) -> None:
    rgb = np.random.default_rng(bits).integers(0, 2**bits, size=(3, 3, 3)).astype(np.uint16 if bits == 16 else np.uint8)
    root, folder, original, digest = stored_original(tmp_path, png_with_profile(rgb, PLANTED))
    copy = variants.make_display_copy(root, folder, original, source_sha256=digest, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready"
    kept = profile_in(copy.folder / "variant.png")
    assert copy.variant["colour"]["iccProfileSha256"] == sha(kept) == sha(photo.profile_without_text(PLANTED))
    assert json.loads((copy.folder / "variant.json").read_text())["colour"]["iccProfileSha256"] == sha(kept)
    assert copy.attempt["measured"]["colour"]["iccProfileSha256"] == sha(PLANTED)


def test_a_display_copy_of_a_profile_it_cannot_rebuild_fails_and_stores_nothing(tmp_path: Path) -> None:
    rgb = np.random.default_rng(4).integers(0, 256, size=(3, 3, 3), dtype=np.uint8)
    root, folder, original, digest = stored_original(tmp_path, png_with_profile(rgb, lab_connected(PLANTED)))
    assert decode.decode_still(original).icc_profile == lab_connected(PLANTED)
    copy = variants.make_display_copy(root, folder, original, source_sha256=digest, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "failed" and copy.variant is None
    assert copy.attempt["failureReason"] == decode.UNDECODABLE
    assert not (folder / "variants").exists()


def no_progress(*args) -> None:
    pass


def test_a_restoration_names_the_profile_in_its_own_file_and_its_attempt_the_originals(tmp_path: Path) -> None:
    scene = star_field(11, size=320, count=24, noise_sigma=0.0015, peaks=(0.01, 0.2))
    root, folder, original, digest = stored_original(tmp_path, png16.write_png16(scene.codes.astype(np.uint16), PLANTED))
    out = media_jobs.restoration_in_child(str(root), str(folder), str(original), digest, JOB_ID, {"kind": "restoration"},
                                          None, no_progress)
    assert out["status"] == "ready", out
    stored = folder / "variants" / out["variantId"]
    kept = profile_in(stored / "variant.png")
    assert kept == photo.profile_without_text(PLANTED)
    assert json.loads((stored / "variant.json").read_text())["colour"]["iccProfileSha256"] == sha(kept)
    assert json.loads((stored / "attempt.json").read_text())["measured"]["colour"]["iccProfileSha256"] == sha(PLANTED)


def test_a_restoration_of_a_profile_it_cannot_rebuild_fails_before_restoring(tmp_path: Path, monkeypatch) -> None:
    scene = star_field(11, size=96, count=6, noise_sigma=0.0015, peaks=(0.01, 0.2))
    lab = lab_connected(PLANTED)
    root, folder, original, digest = stored_original(tmp_path, png16.write_png16(scene.codes.astype(np.uint16), lab))
    calls = []
    monkeypatch.setattr(photo, "restore_still", lambda *args, **kwargs: calls.append(args))
    out = media_jobs.restoration_in_child(str(root), str(folder), str(original), digest, JOB_ID, {"kind": "restoration"},
                                          None, no_progress)
    assert out["status"] == "failed" and out["failureReason"] == decode.UNDECODABLE, out
    assert calls == []
    assert not (folder / "variants").exists()


def test_an_exports_manifest_names_the_profile_of_the_png_beside_it(tmp_path: Path) -> None:
    rgb = np.random.default_rng(9).integers(0, 256, size=(4, 5, 3), dtype=np.uint8)
    root, folder, original, digest = stored_original(tmp_path, png_with_profile(rgb, PLANTED))
    copy = variants.make_display_copy(root, folder, original, source_sha256=digest, job_id=JOB_ID, now=fixed_clock())
    records = {name: json.loads((copy.folder / f"{name}.json").read_text()) for name in ("variant", "graph", "attempt")}
    # The manifest the export route puts beside the stored file, built from the same three records
    manifest = variants.export_manifest(records["variant"], records["graph"], records["attempt"], None)
    kept = profile_in(copy.folder / "variant.png")
    assert manifest["variant"]["colour"]["iccProfileSha256"] == sha(kept)
    assert manifest["attempt"]["measured"]["colour"]["iccProfileSha256"] == sha(PLANTED)
    assert sha(kept) != sha(PLANTED)
