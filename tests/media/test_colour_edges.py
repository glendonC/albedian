"""restore-2a's bounds, each at its inside end and just outside, from one table (LANE "Before you report", item 3):
the transfer functions' knees, their continuation past 0 and 1, PQ's black, the legal codes at each depth and range,
the depths carried, an ICC profile's header, tag table, tags and curves to the byte, the parametric types, counts and
knees, a table curve's rise, a tile grid's crop and offsets, a tile's planes to the byte, a PNG's sides, the bytes
read_png needs, and a whole PNG's one IEND.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from services.media import colour, decode, png16, variants
from test_photo_path import SOURCE_MARKERS, numeric_constants


def nudge(value: float, direction: float) -> float:
    return float(np.nextafter(value, direction))


def decoded(name: str, v: float) -> float:
    return float(colour.decode_transfer(name, np.array(v)))


def encoded(name: str, light: float) -> float:
    return float(colour.encode_transfer(name, np.array(light)))


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(out, "PNG")
    return out.getvalue()


def refused(error: type[BaseException], function, *args, match: str = "") -> bool:
    """Whether the call raises `error`, with a message holding `match` when one is given."""
    try:
        function(*args)
    except error as raised:
        return re.search(match, str(raised)) is not None
    return False


# An ICC profile as ICC.1:2022 clause 7.3 lays it out: a 128 byte header, the tag count, 12 byte entries, then the tag
# bodies with nothing after the last; `past` lengthens the last entry's size beyond the profile's end
def icc(tags: dict[bytes, bytes], past: int = 0) -> bytes:
    offset, entries, bodies = 132 + 12 * len(tags), b"", b""
    for number, (signature, body) in enumerate(tags.items(), 1):
        size = len(body) + (past if number == len(tags) else 0)
        entries += signature + offset.to_bytes(4, "big") + size.to_bytes(4, "big")
        bodies += body
        offset += len(body)
    return bytes(128) + len(tags).to_bytes(4, "big") + entries + bodies


def para(function: int, *values: float) -> bytes:
    return b"para" + bytes(4) + function.to_bytes(2, "big") + bytes(2) + b"".join(
        round(v * 65536).to_bytes(4, "big", signed=True) for v in values)


def curv(*entries: int) -> bytes:
    return b"curv" + bytes(4) + len(entries).to_bytes(4, "big") + b"".join(e.to_bytes(2, "big") for e in entries)


def curves_of(profile: bytes) -> tuple:
    return colour.icc_tone_curves(profile)[1]


# A value each parametric type could hold, by type (g, a, b, c, d, e, f as the type uses them)
PARAMETERS = {0: (2.2,), 1: (2.4, 1.1, -0.1), 2: (2.0, 1.2, -0.2, 0.05), 3: (2.0, 1.0, 0.0, 0.5, 0.25),
              4: (2.2, 0.95, 0.05, 0.2, 0.1, 0.01, 0.004)}


# A tile of 4 x 4 at 8 bits 4:2:0: 16 luma bytes, then 2 x 2 Cb and 2 x 2 Cr
TILE = bytes(range(16)) + bytes(4) + bytes(4)


def placed(coded: tuple[int, int], crop_at: tuple[int, int], size: tuple[int, int], tiles_at: tuple = ((0, 0),),
           tile: bytes = TILE) -> bool:
    """Whether the decoder's tile placement takes a grid of 4 x 4 yuv420p tiles at `tiles_at` on a coded canvas,
    cropped at `crop_at` to `size`; each tile's frame is `tile`, given in place of ffmpeg's."""
    grid = {"coded_width": coded[0], "coded_height": coded[1], "horizontal_offset": crop_at[0], "vertical_offset": crop_at[1],
            "subcomponents": [{"stream_index": i, "tile_horizontal_offset": x, "tile_vertical_offset": y}
                              for i, (x, y) in enumerate(tiles_at)]}
    streams = {i: {"pix_fmt": "yuv420p", "width": 4, "height": 4} for i in range(len(tiles_at))}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(decode, "_raw_frame", lambda path, pixel_format, stream=None: tile)
        try:
            decode._place_tiles(Path("not read"), grid, streams, size[0], size[1], "yuv420p")
        except decode.DecodeRefusal:
            return False
    return True


FOUR_TILES = ((0, 0), (4, 0), (0, 4), (4, 4))


def huge(height: int, width: int) -> np.ndarray:
    # An array of that shape over three samples, so the side check is reached without the memory such a picture needs
    return np.lib.stride_tricks.as_strided(np.zeros(3, np.uint16), shape=(height, width, 3), strides=(0, 0, 2))


def side_accepted(height: int, width: int) -> bool:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(png16, "filtered_rows", lambda _rgb: b"\x02\x00\x00\x00\x00\x00\x00")
        return png16.write_png16(huge(height, width)).startswith(png16.SIGNATURE)


# (the bound, the check at its inside end, the check just outside); each check returns True when the side it names holds
EDGES = [
    ("sRGB decode: the linear part up to 0.04045 inclusive",
     lambda: decoded("srgb", 0.04045) == pytest.approx(0.04045 / 12.92, abs=1e-15),
     lambda: decoded("srgb", nudge(0.04045, 1)) == pytest.approx(((nudge(0.04045, 1) + 0.055) / 1.055) ** 2.4, abs=1e-15)),
    ("sRGB encode: the linear part up to 0.0031308 inclusive",
     lambda: encoded("srgb", 0.0031308) == pytest.approx(12.92 * 0.0031308, abs=1e-15),
     lambda: encoded("srgb", nudge(0.0031308, 1)) == pytest.approx(1.055 * nudge(0.0031308, 1) ** (1 / 2.4) - 0.055, abs=1e-15)),
    ("BT.709 decode: the linear part below 0.081 only",
     lambda: decoded("bt709", nudge(0.081, 0)) == pytest.approx(nudge(0.081, 0) / 4.5, abs=1e-15),
     lambda: decoded("bt709", 0.081) == pytest.approx(((0.081 + 0.099) / 1.099) ** (1 / 0.45), abs=1e-15)),
    ("BT.709 encode: the linear part below 0.018 only",
     lambda: encoded("bt709", nudge(0.018, 0)) == pytest.approx(4.5 * nudge(0.018, 0), abs=1e-15),
     lambda: encoded("bt709", 0.018) == pytest.approx(1.099 * 0.018**0.45 - 0.099, abs=1e-15)),
    ("HLG decode: the square up to 0.5 inclusive",
     lambda: decoded("hlg", 0.5) == pytest.approx(0.25 / 3, abs=1e-15),
     lambda: decoded("hlg", nudge(0.5, 1)) == pytest.approx(
         (math.exp((nudge(0.5, 1) - 0.55991073) / 0.17883277) + 0.28466892) / 12, abs=1e-15)),
    ("every transfer: the formula at 1 itself, its straight continuation just past it",
     lambda: all(decoded(n, 1.0) == pytest.approx(decoded(n, nudge(1.0, 0)), rel=1e-12) for n in colour.TRANSFERS),
     lambda: all(decoded(n, 1.0 + 1e-6) > decoded(n, 1.0) for n in colour.TRANSFERS)),
    ("every transfer: the formula at 0 itself (PQ encodes 0 as c1^m), odd just below it",
     lambda: encoded("pq", 0.0) == pytest.approx((107 / 128) ** (2523 / 32), abs=1e-18),
     lambda: all(decoded(n, -1e-6) == -decoded(n, 1e-6) for n in colour.TRANSFERS)),
    ("limited 8 bit codes: 1 and 254 kept, 0 and 255 held to them",
     lambda: [c.tolist() for c in colour.ycbcr_to_codes(np.array([-15 / 219, (254 - 16) / 219]), np.zeros(2), np.zeros(2), 8, "limited")][0] == [1, 254],
     lambda: [c.tolist() for c in colour.ycbcr_to_codes(np.array([-16 / 219, (255 - 16) / 219]), np.zeros(2), np.zeros(2), 8, "limited")][0] == [1, 254]),
    ("a 16 bit PNG side: 1 sample written, 0 refused",
     lambda: png16.write_png16(np.zeros((1, 1, 3), np.uint16)).startswith(png16.SIGNATURE),
     lambda: refused(ValueError, png16.write_png16, np.zeros((0, 1, 3), np.uint16), match="1 to 2")
     and refused(ValueError, png16.write_png16, np.zeros((1, 0, 3), np.uint16), match="1 to 2")),
    ("an ICC curve: one entry is a gamma, two a table",
     lambda: colour.IccTable((256,)).decode(np.array([0.25]))[0] == pytest.approx(0.25, abs=1e-15),
     lambda: colour.IccTable((0, 65535)).decode(np.array([0.25]))[0] == pytest.approx(0.25, abs=1e-15)
     and colour.IccTable((0, 32768)).decode(np.array([1.0]))[0] == pytest.approx(32768 / 65535, abs=1e-15)),
    ("a whole PNG: one IEND, last; a second one is not whole",
     lambda: variants.png_intact(png_bytes()),
     lambda: not variants.png_intact(png_bytes() + png_bytes()[-12:])),
    ("HLG encode: the square root up to 1/12 inclusive",
     lambda: encoded("hlg", 1 / 12) == pytest.approx(math.sqrt(3 / 12), abs=1e-15),
     lambda: encoded("hlg", nudge(1 / 12, 1)) == pytest.approx(
         0.17883277 * math.log(12 * nudge(1 / 12, 1) - 0.28466892) + 0.55991073, abs=1e-15)),
    ("PQ decode: no light up to its black code c1^m (7.3e-7), light past it",
     lambda: decoded("pq", 0.0) == 0.0 and decoded("pq", 7e-7) == 0.0,
     lambda: decoded("pq", 8e-7) > 0.0),
    ("every transfer's encode: the formula up to decode(1), the straight inverse just past it",
     lambda: all(encoded(n, decoded(n, 1.0)) == pytest.approx(1.0, abs=1e-12) for n in colour.TRANSFERS),
     lambda: all(encoded(n, decoded(n, 1.0 + 1e-6)) == pytest.approx(1.0 + 1e-6, abs=1e-12) for n in colour.TRANSFERS)),
    ("every transfer's encode: positive just above 0, odd just below it",
     lambda: all(encoded(n, 1e-6) > 0.0 for n in colour.TRANSFERS),
     lambda: all(encoded(n, -1e-6) == -encoded(n, 1e-6) for n in colour.TRANSFERS)),
    ("limited 10 bit codes: 4 and 1019 kept, 3 and 1020 held to them",
     lambda: colour.ycbcr_to_codes(np.array([(4 / 4 - 16) / 219, (1019 / 4 - 16) / 219]), np.zeros(2), np.zeros(2), 10, "limited")[0].tolist() == [4, 1019],
     lambda: colour.ycbcr_to_codes(np.array([(3 / 4 - 16) / 219, (1020 / 4 - 16) / 219]), np.zeros(2), np.zeros(2), 10, "limited")[0].tolist() == [4, 1019]),
    ("full range 8 bit codes: 0 and 255 kept, past them held to them",
     lambda: [c.tolist() for c in colour.ycbcr_to_codes(np.array([0.0, 1.0]), np.array([-128 / 255, 127 / 255]), np.zeros(2), 8, "full")][:2] == [[0, 255], [0, 255]],
     lambda: [c.tolist() for c in colour.ycbcr_to_codes(np.array([-1 / 255, 256 / 255]), np.array([-129 / 255, 128 / 255]), np.zeros(2), 8, "full")][:2] == [[0, 255], [0, 255]]),
    ("a bit depth: 8, 10 and 16 carried, the depths beside them refused",
     lambda: all(colour.legal_codes(bits, "full") == (0, 2**bits - 1) for bits in (8, 10, 16)),
     lambda: all(refused(ValueError, colour.legal_codes, bits, "full") for bits in (7, 9, 11, 15, 17))),
    ("an ICC profile: 132 bytes read (and found to hold no curves), 131 shorter than its header",
     lambda: refused(colour.IccError, colour.icc_tone_curves, bytes(132), match="no tone curves"),
     lambda: refused(colour.IccError, colour.icc_tone_curves, bytes(131), match="shorter than its header")),
    ("an ICC tag table: ending at the profile's last byte read, one byte more than the profile refused",
     lambda: refused(colour.IccError, colour.icc_tone_curves, bytes(128) + (1).to_bytes(4, "big") + b"kTRC" + bytes(4) + (12).to_bytes(4, "big"),
                     match="neither curv nor para"),
     lambda: refused(colour.IccError, colour.icc_tone_curves, bytes(128) + (1).to_bytes(4, "big") + b"kTRC" + bytes(4) + (12).to_bytes(3, "big"),
                     match="tag table runs past its end")),
    ("an ICC tag: ending at the profile's last byte read, one byte past it refused",
     lambda: isinstance(curves_of(icc({b"kTRC": curv(256)}))[0], colour.IccTable),
     lambda: refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": curv(256)}, past=1), match="runs past the profile's end")),
    ("a para curve of each type: its parameters to the last byte read, one byte short cut",
     lambda: all(curves_of(icc({b"kTRC": para(kind, *values)}))[0] == colour.IccParametric(kind, tuple(round(v * 65536) / 65536 for v in values))
                 for kind, values in PARAMETERS.items()),
     lambda: all(refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": para(kind, *values)[:-1]}), match="cut short")
                 for kind, values in PARAMETERS.items())),
    ("a curv table: its entries to the last byte read, one byte short cut",
     lambda: curves_of(icc({b"kTRC": curv(0, 30000, 65535)}))[0] == colour.IccTable((0, 30000, 65535)),
     lambda: refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": curv(0, 30000, 65535)[:-1]}), match="table curve cut short")),
    ("a curve's 12 byte head: 12 bytes read, 11 not a curve",
     lambda: curves_of(icc({b"kTRC": curv()}))[0] == colour.IccTable(())
     and refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": para(0, 2.2)[:12]}), match="cut short"),
     lambda: refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": curv()[:11]}), match="neither curv nor para")
     and refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": para(0, 2.2)[:11]}), match="neither curv nor para")),
    ("a parametric type: 4 the last the clause lists, 5 refused",
     lambda: isinstance(curves_of(icc({b"kTRC": para(4, *PARAMETERS[4])}))[0], colour.IccParametric),
     lambda: refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": para(5, *PARAMETERS[4])}), match="does not list")),
    ("a parametric curve's parameters: its type's own count, one fewer or one more refused",
     lambda: all(colour.IccParametric(kind, values).parameters == values for kind, values in PARAMETERS.items()),
     lambda: all(refused(ValueError, colour.IccParametric, kind, values[:-1]) and refused(ValueError, colour.IccParametric, kind, values + (0.5,))
                 for kind, values in PARAMETERS.items())),
    ("parametric types 3 and 4: the power part from d inclusive, the line just below d",
     lambda: colour.IccParametric(3, PARAMETERS[3]).decode(np.array([0.25]))[0] == pytest.approx(0.25**2.0, abs=1e-15)
     and colour.IccParametric(4, PARAMETERS[4]).decode(np.array([0.1]))[0] == pytest.approx((0.95 * 0.1 + 0.05) ** 2.2 + 0.01, abs=1e-15),
     lambda: colour.IccParametric(3, PARAMETERS[3]).decode(np.array([nudge(0.25, 0)]))[0] == pytest.approx(0.5 * nudge(0.25, 0), abs=1e-15)
     and colour.IccParametric(4, PARAMETERS[4]).decode(np.array([nudge(0.1, 0)]))[0] == pytest.approx(0.2 * nudge(0.1, 0) + 0.004, abs=1e-15)),
    ("an ICC table to undo: rising by one code each step undone, one flat step refused",
     lambda: colour.IccTable((0, 1, 2, 65535)).encode(np.array([2 / 65535]))[0] == pytest.approx(2 / 3, abs=1e-12),
     lambda: refused(ValueError, colour.IccTable((0, 1, 1, 65535)).encode, np.array([0.5]), match="does not rise strictly")),
    ("an ICC single-entry gamma to undo: 1/256 undone, 0 refused",
     lambda: colour.IccTable((1,)).encode(colour.IccTable((1,)).decode(np.array([0.5])))[0] == pytest.approx(0.5, abs=1e-9),
     lambda: refused(ValueError, colour.IccTable((0,)).encode, np.array([0.5]), match="gamma of zero")),
    ("parametric types 1 and 2 scale a: non-zero accepted, zero refused",
     lambda: colour.IccParametric(1, (2.2, 1e-3, 0.0)).parameters[1] == 1e-3
     and colour.IccParametric(2, (2.2, -1e-3, 0.1, 0.05)).parameters[1] == -1e-3,
     lambda: refused(ValueError, colour.IccParametric, 1, (2.2, 0.0, 0.1))
     and refused(ValueError, colour.IccParametric, 2, (2.2, 0.0, 0.1, 0.05))
     and refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": para(1, 2.2, 0.0, 0.1)}))),
    ("a tile grid's crop: to the coded canvas's last sample accepted, one sample past it refused, across and down",
     lambda: placed((8, 8), (2, 2), (6, 6), FOUR_TILES),
     lambda: not placed((8, 8), (2, 2), (7, 6), FOUR_TILES) and not placed((8, 8), (2, 2), (6, 7), FOUR_TILES)),
    ("a crop offset on whole chroma samples at 4:2:0: 2 accepted, 1 refused, across and down",
     lambda: placed((8, 8), (2, 0), (2, 2), FOUR_TILES) and placed((8, 8), (0, 2), (2, 2), FOUR_TILES),
     lambda: not placed((8, 8), (1, 0), (2, 2), FOUR_TILES) and not placed((8, 8), (0, 1), (2, 2), FOUR_TILES)),
    ("a tile offset on whole chroma samples at 4:2:0: 2 accepted, 1 refused, across and down",
     lambda: placed((8, 8), (0, 0), (6, 6), ((0, 0), (2, 0), (0, 2), (2, 2))),
     lambda: not placed((8, 8), (0, 0), (4, 4), ((0, 0), (1, 0))) and not placed((8, 8), (0, 0), (4, 4), ((0, 0), (0, 1)))),
    ("every cropped sample from a tile: a crop the tiles cover accepted, one column or row more refused",
     lambda: placed((8, 8), (0, 0), (4, 4)),
     lambda: not placed((8, 8), (0, 0), (5, 4)) and not placed((8, 8), (0, 0), (4, 5))),
    ("a tile's frame: its three planes to the byte read, one byte fewer or more refused",
     lambda: placed((4, 4), (0, 0), (4, 4)),
     lambda: not placed((4, 4), (0, 0), (4, 4), tile=TILE[:-1]) and not placed((4, 4), (0, 0), (4, 4), tile=TILE + b"\x00")),
    ("a 16 bit PNG side: at most 2^31 - 1 accepted on either side, 2^31 refused on either side",
     lambda: side_accepted(1, 2**31 - 1) and side_accepted(2**31 - 1, 1),
     lambda: refused(ValueError, png16.write_png16, huge(1, 2**31), match="1 to 2") and refused(ValueError, png16.write_png16, huge(2**31, 1), match="1 to 2")),
    ("read_png: 25 bytes reach the depth byte (and go to the reader), 24 refused",
     lambda: refused(OSError, decode.read_png, png_bytes()[:25]),
     lambda: refused(decode.DecodeRefusal, decode.read_png, png_bytes()[:24])),
]


@pytest.mark.parametrize("bound,inside,outside", EDGES, ids=[row[0] for row in EDGES])
def test_each_bound_at_its_inside_end_and_just_outside(bound: str, inside, outside) -> None:
    assert inside(), f"{bound}: the inside end"
    assert outside(), f"{bound}: just outside"


def with_bit(data: bytes, at: int) -> bytes:
    """`data` with the lowest bit of byte `at` set."""
    return data[:at] + bytes([data[at] | 0x01]) + data[at + 1:]


def test_each_field_of_a_profile_is_read_at_its_full_width_and_no_reserved_byte_is_read() -> None:
    # Every test profile above has zero reserved bytes and numbers that fit in their lowest byte or two, where a field
    # read one byte short or long reads the same; here the header and each curve's reserved bytes 4 to 7 are 0xFF, and
    # a bit is set in the top byte of the tag count, a tag's offset and size, a para's type and a curv's count
    para_body, curv_body = para(3, *PARAMETERS[3]), curv(0, 30000, 65535)
    profile = icc({b"kTRC": para_body})
    assert curves_of(b"\xff" * 128 + profile[128:]) == curves_of(profile)
    assert curves_of(icc({b"kTRC": para_body[:4] + b"\xff" * 4 + para_body[8:]})) == curves_of(profile)
    assert curves_of(icc({b"kTRC": curv_body[:4] + b"\xff" * 4 + curv_body[8:]})) == curves_of(icc({b"kTRC": curv_body}))
    for at, field in ((128, "the tag count"), (136, "the tag's offset"), (140, "the tag's size")):
        assert refused(colour.IccError, colour.icc_tone_curves, with_bit(profile, at)), field
    assert refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": with_bit(para_body, 8)}), match="does not list"), "type 259"
    assert refused(colour.IccError, colour.icc_tone_curves, icc({b"kTRC": with_bit(curv_body, 8)}), match="cut short"), "2^24 + 3 entries"
    # A parameter is a whole s15Fixed16 number, its largest magnitudes included
    assert curves_of(icc({b"kTRC": para(1, 32767.5, -32768.0, 200.0)}))[0].parameters == (32767.5, -32768.0, 200.0)


def test_every_numeric_constant_of_restore_2a_names_its_source() -> None:
    unsourced = []
    for module in (colour, decode, png16, variants):
        constants = numeric_constants(module)
        assert constants, module.__name__
        for name, line, comment in constants:
            if not any(marker in comment for marker in SOURCE_MARKERS):
                unsourced.append(f"{module.__name__}:{line} {name}")
    assert unsourced == []
