"""A stored still's colour profile is built anew from the original's colour data alone (`services/media/photo.py`
`profile_without_text`; REQ-RST-9.4 as the lead decided on 2026-11-19 at 07:31 and 08:34; restore-8b v3; AUDIT-B's
verdicts/restore-8b.md and restore-8b-v2.md).

Covers, on Little CMS's own sRGB:
- A fresh header: the size, version, class, colour space, connection space, `acsp` and rendering intent from the
  original, the illuminant D50 whatever the original holds there (restore-8d), every other byte zero, and the id the MD5 of the profile with its flags, intent and id zero.
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
- restore-8d, AUDIT-B's hand-built carriers (verdicts/restore-8b-v3.md): another space's tags (a grey curve on an RGB
  profile, primaries, RGB curves and chromaticities on a grey one) and the luminance are left behind; chromaticities
  of other than the space's channels are left out alone, as is any damaged optional tag (chad, bkpt, chrm, cicp),
  while a damaged needed tag still leaves no profile.
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


# restore-8d: the carriers AUDIT-B built by hand in verdicts/restore-8b-v3.md, each a colour tag no transform of the
# profile's space reads, or colour numbers no transform needs
XYZ_MARKER = b"XYZ " + bytes(4) + MARKER[:12]
CURVE_MARKER = b"curv" + bytes(4) + struct.pack(">I", 6) + MARKER[:12]


def test_a_grey_curve_and_the_luminance_on_an_rgb_profile_are_left_behind(base) -> None:
    table = list(icc.tag_data(base).items()) + [(b"kTRC", CURVE_MARKER), (b"lumi", XYZ_MARKER)]
    out = photo.profile_without_text(icc.assemble(base, table))
    assert out == photo.profile_without_text(base)
    assert MARKER[:12] not in out and b"kTRC" not in out and b"lumi" not in out


def test_primaries_and_chromaticities_on_a_grey_profile_are_left_behind(base) -> None:
    data = icc.tag_data(base)
    grey = base[:16] + b"GRAY" + base[20:]
    clean = icc.assemble(grey, [(b"wtpt", data[b"wtpt"]), (b"kTRC", data[b"rTRC"])])
    planted = icc.assemble(grey, [(b"wtpt", data[b"wtpt"]), (b"kTRC", data[b"rTRC"]), (b"rXYZ", XYZ_MARKER),
                                  (b"gTRC", CURVE_MARKER), (b"chrm", data[b"chrm"])])
    out = photo.profile_without_text(planted)
    assert out is not None and out == photo.profile_without_text(clean)
    assert MARKER[:12] not in out and set(icc.tag_data(out)) == {b"desc", b"cprt", b"wtpt", b"kTRC"}


def rebuilt_without(base: bytes, sig: bytes) -> bytes:
    return photo.profile_without_text(icc.assemble(base, [(s, body) for s, body in icc.tag_data(base).items() if s != sig]))


@pytest.mark.parametrize("channels", [0, 1, 2, 4, 20])
def test_chromaticities_of_other_than_the_spaces_channels_are_left_out_alone(base, channels: int) -> None:
    chrm = b"chrm" + bytes(4) + struct.pack(">HH", channels, 0) + (MARKER[:8] * max(channels, 1))[:8 * channels]
    table = [(sig, chrm if sig == b"chrm" else body) for sig, body in icc.tag_data(base).items()]
    out = photo.profile_without_text(icc.assemble(base, table))
    assert out is not None and out == rebuilt_without(base, b"chrm")
    assert b"chrm" not in out and MARKER[:8] not in out


# restore-8d (1), proposed to the lead at 13:05: a damaged optional tag (cut short, of a type it may not have, a
# function or count it cannot hold) is left out alone and the rest rebuilds [tested: leaving chad or bkpt out changes
# 0 codes on the 28 real profiles of this laptop that carry one]; a damaged needed tag still leaves no profile
OPTIONAL_DAMAGE = {
    b"chad": [b"sf32" + bytes(4) + bytes(20), b"XYZ " + bytes(16)],
    b"bkpt": [b"XYZ " + bytes(8), b"curv" + bytes(8)],
    b"chrm": [b"chrm" + bytes(4) + struct.pack(">HH", 3, 0) + bytes(8)],
    b"cicp": [b"cicp" + bytes(6), b"text" + bytes(4) + b"x\x00"],
}


@pytest.mark.parametrize(("sig", "damage"), [(sig, damage) for sig, damages in OPTIONAL_DAMAGE.items() for damage in damages])
def test_a_damaged_optional_tag_is_left_out_alone_and_the_rest_rebuilds(base, sig: bytes, damage: bytes) -> None:
    table = [(s, body) for s, body in icc.tag_data(base).items() if s != sig] + [(sig, damage)]
    out = photo.profile_without_text(icc.assemble(base, table))
    assert out is not None and out == rebuilt_without(base, sig)
    assert sig not in {s for s, _, _ in icc.tags(out)}


@pytest.mark.parametrize("sig", [b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC"])
def test_a_damaged_needed_tag_still_leaves_no_profile(base, sig: bytes) -> None:
    table = [(s, b"XYZ " + bytes(8) if s == sig else body) for s, body in icc.tag_data(base).items()]
    assert photo.profile_without_text(icc.assemble(base, table)) is None


def test_chromaticities_of_three_channels_on_an_rgb_profile_are_kept(base) -> None:
    out = photo.profile_without_text(base)
    assert icc.tag_data(out)[b"chrm"] == icc.tag_data(base)[b"chrm"]
    assert struct.unpack(">H", icc.tag_data(out)[b"chrm"][8:10]) == (3,)


@pytest.mark.parametrize("version", [2, 4])
def test_the_illuminant_is_d50_whatever_the_original_holds(base, version: int) -> None:
    original = base[:8] + bytes([version]) + base[9:68] + MARKER[:12] + base[80:]
    out = photo.profile_without_text(original)
    assert out[68:80] == base[68:80] == bytes.fromhex("0000f6d6" "00010000" "0000d32d")
    assert MARKER[:12] not in out and out[84:100] == icc.profile_id(out)
