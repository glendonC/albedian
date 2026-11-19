"""A stored still's colour profile is built anew from the original's colour data alone (`services/media/photo.py`
`profile_without_text`; REQ-RST-9.4 as the lead decided on 2026-11-19 at 07:31 and 08:34; restore-8b v3; AUDIT-B's
verdicts/restore-8b.md and restore-8b-v2.md).

Covers, on Little CMS's own sRGB:
- A fresh header: the size, version, class, colour space, connection space, `acsp`, rendering intent and illuminant
  from the original, every other byte zero, and the id the MD5 of the profile with its flags, intent and id zero.
- Only the description and the copyright as text, each written fresh in its version's type with the fixed words;
  every colour tag written by its type at its defined length, equal to the original's data there; every other tag
  gone; identical curves shared.
- A marker planted in every byte that is no colour data (the free header bytes, the tag types' reserved bytes, past
  the data inside each colour tag's declared length, the gaps between blocks, every kind of text tag and tags of other
  types) changes nothing: the result is the clean profile's, byte for byte.
- Little CMS reads the result by its fixed name and turns every colour to and from sRGB exactly as from the original;
  a version 2 profile gets version 2 text types; the rewrite of a rewrite is itself; a profile it cannot rebuild from
  colour data is dropped (None).
- Property: the result is a function of the colour data alone (seeds 1 to 20).
"""

from __future__ import annotations

import hashlib
import io
import struct

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image, ImageCms

import icc_profiles as icc
from services.media import photo

TEXTS = {"desc": "planted description 1a2b3c", "cprt": "planted copyright 4d5e6f", "dmnd": "planted maker 7a8b9c",
         "dmdd": "planted model 0d1e2f", "vued": "planted viewing 3a4b5c", "mmod": "planted mmod 6d7e8f",
         "meta": "planted meta 9a0b1c"}
MARKER = b"PLANTED-0xC0FFEE!"
COLOUR_HEADER = [(8, 10), (12, 24), (36, 40), (64, 68), (68, 80)]


@pytest.fixture(scope="module")
def base() -> bytes:
    return icc.srgb()


def colour_sigs(profile: bytes) -> set[bytes]:
    return {sig for sig in icc.COLOUR_SIGNATURES if sig in icc.tag_data(profile)}


def test_only_the_description_and_copyright_hold_text_and_it_is_the_fixed_text(base) -> None:
    out = photo.profile_without_text(icc.planted(base, TEXTS))
    found = icc.text_tags(out)
    assert found == {b"desc": (b"mluc", photo.PROFILE_DESCRIPTION), b"cprt": (b"mluc", photo.PROFILE_TEXT)}
    for text in list(TEXTS.values()) + ["planted", "2021"]:
        for encoding in ("latin-1", "utf-8", "utf-16-be"):
            assert text.encode(encoding) not in out, (text, encoding)


def test_colour_tags_are_written_at_their_length_equal_to_the_originals_data_and_other_tags_are_gone(base) -> None:
    out = photo.profile_without_text(icc.planted(base, TEXTS))
    before, after = icc.tag_data(base), icc.tag_data(out)
    assert set(after) == colour_sigs(base) | {b"desc", b"cprt"}
    for sig in colour_sigs(base):
        assert after[sig] == before[sig], sig
    offsets = {sig: offset for sig, offset, _ in icc.tags(out)}
    assert offsets[b"rTRC"] == offsets[b"gTRC"] == offsets[b"bTRC"]
    for sig, offset, size in icc.tags(out):
        assert offset % 4 == 0 and 132 + 12 * len(icc.tags(out)) <= offset and offset + size <= len(out), sig


def test_the_header_is_fresh_from_the_colour_fields_and_its_id_is_the_md5(base) -> None:
    out = photo.profile_without_text(icc.planted_everywhere(base, MARKER))
    assert icc.u32(out, 0) == len(out) and len(out) % 4 == 0
    for start, end in COLOUR_HEADER:
        assert out[start:end] == base[start:end], (start, end)
    for start, end in icc.FREE_HEADER:
        if (start, end) != (84, 100):
            assert out[start:end] == bytes(end - start), (start, end)
    assert out[84:100] == icc.profile_id(out)


@pytest.mark.parametrize(("intent", "kept"), [(1, 1), (3, 3), (0x54455854, 0)])
def test_the_rendering_intent_is_kept_when_it_is_one_and_the_id_leaves_it_out(base, intent: int, kept: int) -> None:
    # Relative colorimetric, absolute colorimetric, and the four letters TEXT where an intent belongs
    out = photo.profile_without_text(base[:64] + struct.pack(">I", intent) + base[68:])
    assert icc.u32(out, 64) == kept
    assert out[84:100] == icc.profile_id(out)


def test_a_marker_in_every_byte_that_is_no_colour_data_changes_nothing(base) -> None:
    planted = icc.planted_everywhere(base, MARKER)
    assert planted.count(MARKER) >= 16
    out = photo.profile_without_text(planted)
    assert out == photo.profile_without_text(base)
    for needle in (MARKER, MARKER[:8], MARKER.decode().encode("utf-16-be")[:16]):
        assert needle not in out, needle


def test_little_cms_reads_it_by_its_fixed_name_and_turns_colours_as_the_original_does(base) -> None:
    planted = icc.planted_everywhere(base, MARKER)
    out = photo.profile_without_text(planted)
    profile = ImageCms.ImageCmsProfile(io.BytesIO(out))
    assert ImageCms.getProfileDescription(profile).strip() == photo.PROFILE_DESCRIPTION
    rgb = Image.fromarray(np.random.default_rng(5).integers(0, 256, (48, 64, 3), dtype=np.uint8), "RGB")
    srgb = ImageCms.createProfile("sRGB")
    for source in (base, planted):
        turned = [np.asarray(ImageCms.profileToProfile(rgb, ImageCms.ImageCmsProfile(io.BytesIO(p)), srgb)) for p in (source, out)]
        assert np.array_equal(turned[0], turned[1])
        back = [np.asarray(ImageCms.profileToProfile(rgb, srgb, ImageCms.ImageCmsProfile(io.BytesIO(p)))) for p in (source, out)]
        assert np.array_equal(back[0], back[1])


def test_a_version_2_profile_gets_version_2_text_types(base) -> None:
    v2 = base[:8] + bytes([2, 0x10, 0, 0]) + base[12:]
    out = photo.profile_without_text(v2)
    assert out[8:12] == bytes([2, 0x10, 0, 0])
    assert icc.text_tags(out) == {b"desc": (b"desc", photo.PROFILE_DESCRIPTION), b"cprt": (b"text", photo.PROFILE_TEXT)}


def test_the_rewrite_of_a_rewrite_is_itself(base) -> None:
    out = photo.profile_without_text(icc.planted_everywhere(base, MARKER))
    assert photo.profile_without_text(out) == out


def with_u32(profile: bytes, at: int, value: int) -> bytes:
    return profile[:at] + struct.pack(">I", value) + profile[at + 4:]


def entry_of(profile: bytes, sig: bytes) -> int:
    return next(132 + 12 * i for i, (s, _, _) in enumerate(icc.tags(profile)) if s == sig)


def damaged(base: bytes, damage: str):
    first = 132
    data = icc.tag_data(base)
    if damage == "nothing":
        return None
    if damage == "empty":
        return b""
    if damage == "short":
        return base[:100]
    if damage == "size past the end":
        return with_u32(base, 0, len(base) + 4)
    if damage == "no acsp":
        return base[:36] + b"xxxx" + base[40:]
    if damage == "too many tags":
        return with_u32(base, 128, 10_000)
    if damage == "tag past the end":
        return with_u32(base, first + 4, len(base) - 4)
    if damage == "tag inside the table":
        return with_u32(base, first + 4, 132)
    if damage == "two tags of one name":
        return base[:first + 12] + base[first:first + 4] + base[first + 16:]
    if damage == "a CMYK profile":
        return base[:16] + b"CMYK" + base[20:]
    if damage == "a Lab connection space":
        return base[:20] + b"Lab " + base[24:]
    if damage == "version 5":
        return base[:8] + bytes([5, 0, 0, 0]) + base[12:]
    if damage == "no green curve":
        return icc.assemble(base, [(sig, body) for sig, body in data.items() if sig != b"gTRC"])
    if damage == "a primary too short":
        return icc.assemble(base, [(sig, body[:16] if sig == b"rXYZ" else body) for sig, body in data.items()])
    if damage == "a curve of the wrong type":
        return icc.assemble(base, [(sig, data[b"wtpt"] if sig == b"bTRC" else body) for sig, body in data.items()])
    if damage == "a curve of no known function":
        return icc.assemble(base, [(sig, body[:8] + struct.pack(">H", 9) + body[10:] if sig == b"rTRC" else body)
                                   for sig, body in data.items()])
    if damage == "a table past its tag":
        return icc.assemble(base, [(sig, b"curv" + bytes(4) + struct.pack(">I", 1000) + bytes(20) if sig == b"rTRC" else body)
                                   for sig, body in data.items()])
    raise AssertionError(damage)


@pytest.mark.parametrize("damage", [
    "nothing", "empty", "short", "size past the end", "no acsp", "too many tags", "tag past the end",
    "tag inside the table", "two tags of one name", "a CMYK profile", "a Lab connection space", "version 5",
    "no green curve", "a primary too short", "a curve of the wrong type", "a curve of no known function",
    "a table past its tag",
])
def test_a_profile_it_cannot_rebuild_from_colour_data_is_dropped(base, damage: str) -> None:
    assert photo.profile_without_text(damaged(base, damage)) is None


def test_a_table_curve_is_written_with_its_entries_only(base) -> None:
    entries = list(range(0, 65536, 4096)) + [65535]
    curve = b"curv" + b"MARK" + struct.pack(">I", len(entries)) + b"".join(struct.pack(">H", e) for e in entries) + b"tail text"
    data = icc.tag_data(base)
    out = photo.profile_without_text(icc.assemble(base, [(sig, curve if sig.endswith(b"TRC") else body) for sig, body in data.items()]))
    expected = b"curv" + bytes(4) + struct.pack(">I", len(entries)) + b"".join(struct.pack(">H", e) for e in entries)
    assert icc.tag_data(out)[b"rTRC"] == expected and b"MARK" not in out and b"tail text" not in out
    assert hashlib.sha256(out).hexdigest() == hashlib.sha256(photo.profile_without_text(out)).hexdigest()


@settings(max_examples=40, deadline=None)
@given(one=st.binary(min_size=1, max_size=40), other=st.binary(min_size=1, max_size=40))
def test_the_result_is_a_function_of_the_colour_data_alone(one: bytes, other: bytes) -> None:
    base = icc.srgb()
    a = photo.profile_without_text(icc.planted_everywhere(base, one))
    b = photo.profile_without_text(icc.planted_everywhere(base, other))
    assert a is not None and a == b == photo.profile_without_text(base)
