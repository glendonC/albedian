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
from PIL import Image, ImageCms

import icc_profiles as icc
from photo_frames import star_field
from services.contracts import media as media_contract
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


# restore-8e, as the lead decided at 13:04 with review row C20's words: a profile the rebuild cannot carry
def without_tag(profile: bytes, sig: bytes) -> bytes:
    return icc.assemble(profile, [(s, body) for s, body in icc.tag_data(profile).items() if s != sig], with_id=True)


# Little CMS still converts through a profile missing its white point, and refuses one missing a primary [tested]
READABLE = without_tag(PLANTED, b"wtpt")
UNREADABLE = without_tag(PLANTED, b"rXYZ")
SRGB_REBUILT = photo.profile_without_text(icc.srgb())


def converted_by_little_cms(rgb8: np.ndarray, profile: bytes) -> np.ndarray:
    """The test's own conversion: Pillow's Little CMS from the profile to its own sRGB, perceptual (the profile's
    intent), independent of services/media."""
    transform = ImageCms.buildTransform(ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                        ImageCms.ImageCmsProfile(io.BytesIO(SRGB_REBUILT)), "RGB", "RGB", renderingIntent=0)
    return np.asarray(ImageCms.applyTransform(Image.fromarray(rgb8, "RGB"), transform))


def samples_of(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


@pytest.mark.parametrize("bits", [8, 16])
def test_a_display_copy_of_a_profile_little_cms_reads_is_converted_to_srgb_through_it(tmp_path: Path, bits: int) -> None:
    assert photo.profile_without_text(READABLE) is None
    rgb = np.random.default_rng(bits).integers(0, 2**bits, size=(4, 5, 3)).astype(np.uint16 if bits == 16 else np.uint8)
    root, folder, original, digest = stored_original(tmp_path, png_with_profile(rgb, READABLE))
    copy = variants.make_display_copy(root, folder, original, source_sha256=digest, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready", copy.attempt
    graph = json.loads((copy.folder / "graph.json").read_text())
    assert [step["kind"] for step in graph["steps"]] == ["decode", "convert_colour", "encode"]
    assert graph["steps"][1]["parameters"] == [{"name": "renderingIntent", "value": "perceptual", "origin": "measured",
                                                "basis": "Read from the file's ICC profile."}]
    # The copy is 8 bit: from the original's own depth for an 8 bit photo, by design for a 16 bit one
    encode = {parameter["name"]: parameter for parameter in graph["steps"][2]["parameters"]}
    assert encode["bitDepth"]["value"] == 8 and encode["pixelFormat"]["value"] == "rgb24"
    assert encode["bitDepth"]["origin"] == ("default" if bits == 16 else "measured")
    assert copy.variant["disclosure"] == media_contract.CONVERTED_DISCLOSURES[1 if bits == 16 else 0]
    kept = profile_in(copy.folder / "variant.png")
    assert kept == SRGB_REBUILT
    assert copy.variant["colour"]["iccProfileSha256"] == sha(kept) and copy.variant["colour"]["bitDepth"] == 8
    assert copy.variant["colour"]["primaries"] == "icc" and copy.attempt["measured"]["colour"]["iccProfileSha256"] == sha(READABLE)
    rgb8 = rgb if bits == 8 else ((rgb.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    assert np.array_equal(samples_of(copy.folder / "variant.png"), converted_by_little_cms(rgb8, READABLE))
    manifest = variants.export_manifest(copy.variant, graph, copy.attempt, None)
    assert manifest["disclosure"] == copy.variant["disclosure"]


def test_a_display_copy_of_a_profile_nothing_reads_keeps_its_samples_untagged_with_the_assumption(tmp_path: Path) -> None:
    rgb = np.random.default_rng(6).integers(0, 256, size=(4, 5, 3), dtype=np.uint8)
    root, folder, original, digest = stored_original(tmp_path, png_with_profile(rgb, UNREADABLE))
    copy = variants.make_display_copy(root, folder, original, source_sha256=digest, job_id=JOB_ID, now=fixed_clock())
    assert copy.status == "ready", copy.attempt
    graph = json.loads((copy.folder / "graph.json").read_text())
    assert [step["kind"] for step in graph["steps"]] == ["decode", "encode"]
    assert copy.variant["disclosure"] == media_contract.DISCLOSURES["display_copy"]
    with Image.open(copy.folder / "variant.png") as image:
        assert "icc_profile" not in image.info
    assert np.array_equal(samples_of(copy.folder / "variant.png"), rgb)
    colour = copy.variant["colour"]
    reason = "The file's ICC profile could not be read, so sRGB is assumed."
    assert colour["transfer"] == "srgb" and colour["primaries"] == "bt709" and "iccProfileSha256" not in colour
    assert {"field": "transfer", "assumed": "srgb", "reason": reason} in colour["assumptions"]
    assert {"field": "primaries", "assumed": "bt709", "reason": reason} in colour["assumptions"]
    assert copy.attempt["measured"]["colour"]["iccProfileSha256"] == sha(UNREADABLE)


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


@pytest.mark.parametrize("which", ["readable", "unreadable"])
def test_a_restoration_of_a_profile_it_cannot_rebuild_is_refused_with_its_own_sentence(tmp_path: Path, monkeypatch, which) -> None:
    scene = star_field(11, size=96, count=6, noise_sigma=0.0015, peaks=(0.01, 0.2))
    lab = READABLE if which == "readable" else UNREADABLE
    root, folder, original, digest = stored_original(tmp_path, png16.write_png16(scene.codes.astype(np.uint16), lab))
    calls = []
    monkeypatch.setattr(photo, "restore_still", lambda *args, **kwargs: calls.append(args))
    out = media_jobs.restoration_in_child(str(root), str(folder), str(original), digest, JOB_ID, {"kind": "restoration"},
                                          None, no_progress)
    assert out["status"] == "failed", out
    assert out["failureReason"] == "Your photo's colour profile cannot be carried into a restored copy, so it was not restored."
    assert out["failureReason"] in media_contract.FAILURE_SENTENCES
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
