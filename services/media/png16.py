"""A PNG with 16 bits per channel, written with numpy and zlib (spec restoration-for-every-upload, design section 8):
Pillow opens a 16 bit RGB PNG as 8 bit and cannot write one (M17).

The file holds `IHDR` (depth 16, colour type 2, no interlace), an `iCCP` chunk carrying the original's profile when it
has one, one `IDAT` with every row filtered by `Up`, and `IEND` [verified in design section 8, chunk layout from the
PNG specification]. There is no time stamp and no text chunk, so two runs on the same pixels write the same bytes.
It is read back through ffmpeg as `rgb48le` (`decode.py`), never by this module.
"""

from __future__ import annotations

import struct
import zlib
from typing import Optional

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
# zlib level [proposed in design section 8: 46 MB in 4.2 s for 12 megapixels, against 48 MB in 0.9 s at level 1]
ZLIB_LEVEL = 6
BIT_DEPTH = 16  # [decided 2026-11-11: REQ-RST-6.13, 16 bits per channel]
COLOUR_TYPE_RGB = 2  # [verified in design section 8: PNG specification, colour type 2 is truecolour RGB]
FILTER_UP = 2  # [verified in design section 8: PNG specification, filter method 0 type 2 is Up]
# The name an iCCP chunk gives its profile: 1 to 79 Latin-1 bytes [proposed: the name Pillow writes]
PROFILE_NAME = b"ICC Profile"
# The largest width or height a PNG holds [verified: PNG specification, IHDR, four byte unsigned at most 2^31 - 1]
MAX_SIDE = 2**31 - 1


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def filtered_rows(rgb: np.ndarray) -> bytes:
    """The image's rows as big-endian samples, each byte minus the byte above it modulo 256 (filter `Up`, which works
    on bytes, not samples), each row led by its filter type."""
    rows = rgb.astype(">u2").reshape(rgb.shape[0], -1).view(np.uint8)
    above = np.zeros_like(rows)
    above[1:] = rows[:-1]
    up = rows - above  # uint8 arithmetic wraps modulo 256
    lead = np.full((rows.shape[0], 1), FILTER_UP, dtype=np.uint8)
    return np.concatenate([lead, up], axis=1).tobytes()


def write_png16(rgb: np.ndarray, icc_profile: Optional[bytes] = None) -> bytes:
    """The PNG bytes of an H x W x 3 array of 16 bit samples, with the profile's bytes carried unchanged when given."""
    if rgb.dtype != np.uint16 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("a 16 bit PNG is written from an H x W x 3 array of uint16")
    height, width = rgb.shape[:2]
    if not (1 <= height <= MAX_SIDE and 1 <= width <= MAX_SIDE):
        raise ValueError("a PNG is 1 to 2^31 - 1 samples on each side")
    header = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOUR_TYPE_RGB, 0, 0, 0)
    parts = [SIGNATURE, _chunk(b"IHDR", header)]
    if icc_profile is not None:
        if not icc_profile:
            raise ValueError("an ICC profile has bytes")
        # Name, a zero byte, compression method 0 (zlib), the compressed profile
        parts.append(_chunk(b"iCCP", PROFILE_NAME + b"\x00\x00" + zlib.compress(icc_profile, ZLIB_LEVEL)))
    parts.append(_chunk(b"IDAT", zlib.compress(filtered_rows(rgb), ZLIB_LEVEL)))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)
