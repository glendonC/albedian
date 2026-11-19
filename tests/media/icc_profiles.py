"""ICC profiles for the tests of restore-8b: read a profile's tags, assemble one from tags, and plant text in every text
tag of a real one. Independent of `services/media/photo.py`, which rewrites a profile's text."""

from __future__ import annotations

import hashlib
import struct

from PIL import ImageCms

HEADER = 128
COLOUR_SIGNATURES = (b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"chad", b"chrm")


def u32(data: bytes, at: int) -> int:
    return int.from_bytes(data[at:at + 4], "big")


def srgb() -> bytes:
    """Little CMS's own sRGB, version 4, as Pillow builds it: a real profile, made at run time."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def tags(profile: bytes) -> list[tuple[bytes, int, int]]:
    """Each tag of the table, in order: its signature, offset and size."""
    return [(profile[132 + 12 * i:136 + 12 * i], u32(profile, 136 + 12 * i), u32(profile, 140 + 12 * i))
            for i in range(u32(profile, 128))]


def tag_data(profile: bytes) -> dict[bytes, bytes]:
    return {sig: profile[offset:offset + size] for sig, offset, size in tags(profile)}


def text_tags(profile: bytes) -> dict[bytes, tuple[bytes, str]]:
    """Each tag of the `text`, `desc` (version 2) and `mluc` (version 4) types: its type and its text, every record of a
    `mluc` joined by a space."""
    out = {}
    for sig, offset, size in tags(profile):
        kind = profile[offset:offset + 4]
        if kind == b"text":
            out[sig] = (kind, profile[offset + 8:offset + size].rstrip(b"\x00").decode("latin-1"))
        elif kind == b"desc":
            out[sig] = (kind, profile[offset + 12:offset + 12 + u32(profile, offset + 8)].rstrip(b"\x00").decode("latin-1"))
        elif kind == b"mluc":
            records = [(u32(profile, offset + 20 + 12 * r), u32(profile, offset + 24 + 12 * r)) for r in range(u32(profile, offset + 8))]
            out[sig] = (kind, " ".join(profile[offset + at:offset + at + n].decode("utf-16-be") for n, at in records))
    return out


def text_type(text: str) -> bytes:
    return b"text\x00\x00\x00\x00" + text.encode("latin-1") + b"\x00"


def desc_type(text: str) -> bytes:
    """A version 2 textDescriptionType whose Unicode part also holds the text, as some makers write it."""
    ascii_part = text.encode("latin-1") + b"\x00"
    unicode_part = text.encode("utf-16-be") + b"\x00\x00"
    return (b"desc\x00\x00\x00\x00" + struct.pack(">I", len(ascii_part)) + ascii_part + struct.pack(">II", 0x656E0000, len(unicode_part) // 2)
            + unicode_part + struct.pack(">HB", 0, len(text[:66]) + 1) + (text[:66].encode("latin-1") + b"\x00").ljust(67, b"\x00"))


def mluc_type(*texts: str) -> bytes:
    """A multiLocalizedUnicodeType with one record for each text, English then French."""
    names = [b"enUS", b"frFR", b"deDE"]
    encoded = [text.encode("utf-16-be") for text in texts]
    at = 16 + 12 * len(texts)
    records = b""
    for name, body in zip(names, encoded):
        records += name + struct.pack(">II", len(body), at)
        at += len(body)
    return b"mluc\x00\x00\x00\x00" + struct.pack(">II", len(texts), 12) + records + b"".join(encoded)


def assemble(header: bytes, table: list[tuple[bytes, bytes]], *, with_id: bool = False) -> bytes:
    """A profile of `header` and the tags given as (signature, data); tags whose data is the same object share it."""
    start = 132 + 12 * len(table)
    blocks, placed, entries = b"", {}, b""
    for sig, data in table:
        if id(data) not in placed:
            blocks = blocks.ljust((len(blocks) + 3) & ~3, b"\x00")
            placed[id(data)] = start + len(blocks)
            blocks += data
        entries += sig + struct.pack(">II", placed[id(data)], len(data))
    body = entries + blocks.ljust((len(blocks) + 3) & ~3, b"\x00")
    profile = bytearray(header[:HEADER] + struct.pack(">I", len(table)) + body)
    profile[0:4] = struct.pack(">I", len(profile))
    profile[84:100] = bytes(16)
    if with_id:
        profile[84:100] = profile_id(bytes(profile))
    return bytes(profile)


def profile_id(profile: bytes) -> bytes:
    """The MD5 an ICC profile's header names, of the profile with its flags, rendering intent and id set to zero."""
    zeroed = bytearray(profile)
    zeroed[44:48], zeroed[64:68], zeroed[84:100] = bytes(4), bytes(4), bytes(16)
    return hashlib.md5(bytes(zeroed)).digest()


def planted(base: bytes, texts: dict[str, str], *, with_id: bool = True) -> bytes:
    """`base`'s colour tags, the three TRC tags sharing one curve, and text planted in every kind of text tag: a
    description and a copyright (mluc), a maker's (desc, version 2), a model's (text), a viewing condition's (mluc,
    two languages), and tags of types that may hold text and are no colour data: Apple's `mmod`, a `dict` of metadata,
    a calibration time (dtim)."""
    data = tag_data(base)
    curve = data[b"rTRC"]
    table = [(sig, curve if sig.endswith(b"TRC") else data[sig]) for sig in COLOUR_SIGNATURES if sig in data]
    table += [
        (b"desc", mluc_type(texts["desc"])),
        (b"cprt", mluc_type(texts["cprt"])),
        (b"dmnd", desc_type(texts["dmnd"])),
        (b"dmdd", text_type(texts["dmdd"])),
        (b"vued", mluc_type(texts["vued"], texts["vued"] + " fr")),
        (b"mmod", b"mmod\x00\x00\x00\x00" + texts["mmod"].encode("latin-1").ljust(32, b"\x00")),
        (b"meta", b"dict\x00\x00\x00\x00" + struct.pack(">II", 1, 16) + texts["meta"].encode("utf-16-be")),
        (b"calt", b"dtim\x00\x00\x00\x00" + struct.pack(">6H", 2021, 3, 4, 5, 6, 7)),
    ]
    return assemble(base, table, with_id=with_id)


# Every header byte that is no colour data (restore-8b v3, verdicts/restore-8b.md): the CMM, the version's reserved two
# bytes, the date and time, the platform, the flags, the maker and model, the attributes, the creator, the id and the
# reserved tail; the size, version, class, spaces, `acsp`, intent and illuminant stay
FREE_HEADER = [(4, 8), (10, 12), (24, 36), (40, 44), (44, 48), (48, 56), (56, 64), (80, 84), (84, 100), (100, 128)]


def fill(marker: bytes, length: int) -> bytes:
    return (marker * (length // len(marker) + 1))[:length]


def planted_everywhere(base: bytes, marker: bytes = b"PLANTED-0xC0FFEE!") -> bytes:
    """`base` with `marker` in every free header byte, in each tag type's reserved bytes, past the data inside each
    colour tag's declared length, in the gaps between blocks, and in every kind of text tag and tag of another type."""
    header = bytearray(base[:HEADER])
    for start, end in FREE_HEADER:
        header[start:end] = fill(marker, end - start)
    data = tag_data(base)
    table = []
    for sig in COLOUR_SIGNATURES:
        if sig not in data:
            continue
        body = bytearray(data[sig])
        body[4:8] = fill(marker, 4)
        if body[:4] == b"para":
            body[10:12] = fill(marker, 2)
        table.append((sig, bytes(body) + fill(marker, 28)))
    text = marker.decode("latin-1")
    table += [
        (b"desc", mluc_type(text)), (b"cprt", text_type(text)), (b"dmnd", desc_type(text)), (b"dmdd", text_type(text)),
        (b"vued", mluc_type(text, text)), (b"mmod", b"mmod" + bytes(4) + marker.ljust(32, b"\x00")),
        (b"meta", b"dict" + bytes(4) + struct.pack(">II", 1, 16) + marker),
        (b"calt", b"dtim" + bytes(4) + struct.pack(">6H", 2021, 3, 4, 5, 6, 7)),
    ]
    return assemble_with_gaps(bytes(header), table, marker)


def assemble_with_gaps(header: bytes, table: list[tuple[bytes, bytes]], marker: bytes) -> bytes:
    """As `assemble`, with `marker` bytes rather than zeros between and after the blocks, and its header kept whole
    but for the size."""
    start = 132 + 12 * len(table)
    blocks, entries = b"", b""
    for sig, data in table:
        blocks += fill(marker, 8 + (-len(blocks)) % 4)
        entries += sig + struct.pack(">II", start + len(blocks), len(data))
        blocks += data
    blocks += fill(marker, 8)
    profile = bytearray(header[:HEADER] + struct.pack(">I", len(table)) + entries + blocks)
    profile[0:4] = struct.pack(">I", len(profile))
    return bytes(profile)
