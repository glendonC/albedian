"""The 16 bit PNG writer (`services/media/png16.py`; design section 8).

The file is read back here by an independent reader written from the PNG specification: chunks with their CRC,
the zlib stream, and each row's filter undone byte by byte. Reading it through ffmpeg is `test_decode.py`'s.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from services.media import png16


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    found, position = [], 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        body = data[position + 8 : position + 8 + length]
        (crc,) = struct.unpack(">I", data[position + 8 + length : position + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        found.append((kind, body))
        position += 12 + length
    assert position == len(data)
    return found


def read_rows(data: bytes) -> np.ndarray:
    """The samples of a 16 bit RGB PNG, every filter type undone as the specification writes it."""
    parts = dict(chunks(data))
    width, height, depth, colour, compression, filter_method, interlace = struct.unpack(">IIBBBBB", parts[b"IHDR"])
    assert (depth, colour, compression, filter_method, interlace) == (16, 2, 0, 0, 0)
    raw = zlib.decompress(parts[b"IDAT"])
    stride, step = width * 6, 6
    rows, previous = [], bytearray(stride)
    for row in range(height):
        kind, line = raw[row * (stride + 1)], bytearray(raw[row * (stride + 1) + 1 : (row + 1) * (stride + 1)])
        assert kind in (0, 1, 2, 3, 4)
        for i in range(stride):
            left = line[i - step] if i >= step else 0
            up = previous[i]
            corner = previous[i - step] if i >= step else 0
            if kind == 1:
                line[i] = (line[i] + left) % 256
            elif kind == 2:
                line[i] = (line[i] + up) % 256
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) % 256
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) % 256
        rows.append(bytes(line))
        previous = line
    return np.frombuffer(b"".join(rows), dtype=">u2").reshape(height, width, 3).astype(np.uint16)


@settings(max_examples=40, deadline=None)
@given(
    height=st.integers(1, 9), width=st.integers(1, 9), seed=st.integers(0, 2**32 - 1),
    profile=st.one_of(st.none(), st.binary(min_size=1, max_size=300)),
)
@example(height=1, width=1, seed=0, profile=None)
@example(height=3, width=2, seed=7, profile=b"\x00" * 128)
def test_the_file_reads_back_to_every_sample_and_carries_the_profile_byte_for_byte(
    height: int, width: int, seed: int, profile: bytes | None
) -> None:
    rgb = np.random.default_rng(seed).integers(0, 65536, size=(height, width, 3), dtype=np.uint16)
    data = png16.write_png16(rgb, profile)
    assert np.array_equal(read_rows(data), rgb)
    kinds = [kind for kind, _ in chunks(data)]
    # IHDR first, the profile before the one IDAT, IEND last; no time and no text chunk
    assert kinds == [b"IHDR", *([b"iCCP"] if profile is not None else []), b"IDAT", b"IEND"]
    if profile is not None:
        body = dict(chunks(data))[b"iCCP"]
        name, rest = body.split(b"\x00", 1)
        assert 1 <= len(name) <= 79 and rest[0] == 0
        assert zlib.decompress(rest[1:]) == profile


def test_every_row_is_filtered_up_and_the_stream_is_at_level_6() -> None:
    rgb = np.arange(4 * 5 * 3, dtype=np.uint16).reshape(4, 5, 3) * 1031
    data = png16.write_png16(rgb)
    raw = zlib.decompress(dict(chunks(data))[b"IDAT"])
    stride = 5 * 6 + 1
    assert [raw[row * stride] for row in range(4)] == [2, 2, 2, 2]
    # Up on bytes: each byte minus the byte above, modulo 256, the first row against zeros
    big = rgb.astype(">u2").reshape(4, -1).view(np.uint8).astype(int)
    expected = np.vstack([big[:1], (big[1:] - big[:-1]) % 256])
    assert np.array_equal(np.frombuffer(raw, np.uint8).reshape(4, stride)[:, 1:], expected)
    assert dict(chunks(data))[b"IDAT"] == zlib.compress(raw, 6)


def test_two_runs_write_the_same_bytes() -> None:
    rgb = np.random.default_rng(3).integers(0, 65536, size=(6, 7, 3), dtype=np.uint16)
    assert png16.write_png16(rgb, b"profile") == png16.write_png16(rgb.copy(), b"profile")


@pytest.mark.parametrize(
    "array,match",
    [
        (np.zeros((2, 2, 3), dtype=np.uint8), "uint16"),
        (np.zeros((2, 2), dtype=np.uint16), "uint16"),
        (np.zeros((2, 2, 4), dtype=np.uint16), "uint16"),
        (np.zeros((0, 2, 3), dtype=np.uint16), "1 to 2"),
        (np.zeros((2, 0, 3), dtype=np.uint16), "1 to 2"),
    ],
    ids=["8 bit", "grey", "four channels", "no rows", "no columns"],
)
def test_what_is_not_a_16_bit_rgb_image_is_refused(array: np.ndarray, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        png16.write_png16(array)


def test_an_empty_profile_is_refused() -> None:
    with pytest.raises(ValueError):
        png16.write_png16(np.zeros((1, 1, 3), dtype=np.uint16), b"")
