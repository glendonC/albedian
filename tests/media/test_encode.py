"""Encoding a restored still (`services/media/encode.py`; design section 8; REQ-RST-3.5, REQ-RST-6.13).

Covers:
- Light is stored as its curve's exact encode rounded to 16 bits, channel by channel: a named transfer, an ICC
  profile's parametric curves and its tables, three different curves in one profile.
- The unrounded encode the gate and the dissolve passes read gives back the light it was given, to within the light of
  half a 16 bit step there, a five hundredth of an 8 bit code (measured worst: 0.015 of a step within 13 codes of black
  on PQ, HLG and pure powers, 0.42 at an ICC table's knee between two of its entries).
- Light beyond the transfer's black or peak is stored at the end codes.
- The lift reads the green channel's curve, the channel the luma weights most.
- A stored still is the 16 bit PNG: read back through ffmpeg it is the stored codes, it carries the original's profile
  byte for byte, and two writes are the same bytes.
"""

from __future__ import annotations

import math
import struct
import zlib

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from services.media import colour, decode, encode, png16

# Reading the PNG back starts ffmpeg: each test has its own bound (child_bounds.py)
from child_bounds import bounded  # noqa: E402,F401

STEP = 1 / 65535


def icc(tags: dict[bytes, bytes]) -> bytes:
    """An ICC profile laid out as ICC.1:2022 clause 7.3: a 128 byte header, the tag count, the entries, the bodies."""
    offset, entries, bodies = 132 + 12 * len(tags), b"", b""
    for signature, body in tags.items():
        entries += signature + offset.to_bytes(4, "big") + len(body).to_bytes(4, "big")
        bodies += body
        offset += len(body)
    return bytes(128) + len(tags).to_bytes(4, "big") + entries + bodies


def para(function: int, *values: float) -> bytes:
    return b"para" + bytes(4) + function.to_bytes(2, "big") + bytes(2) + b"".join(
        round(v * 65536).to_bytes(4, "big", signed=True) for v in values)


def curv(entries: list[int]) -> bytes:
    return b"curv" + bytes(4) + len(entries).to_bytes(4, "big") + b"".join(e.to_bytes(2, "big") for e in entries)


SRGB_PARA = para(3, 2.4, 1 / 1.055, 0.055 / 1.055, 1 / 12.92, 0.04045)
TABLE = curv([round(65535 * (k / 1023) ** 2.2) for k in range(1024)])
PROFILES = {
    "icc_parametric": icc({b"rTRC": SRGB_PARA, b"gTRC": SRGB_PARA, b"bTRC": SRGB_PARA}),
    "icc_table": icc({b"rTRC": TABLE, b"gTRC": TABLE, b"bTRC": TABLE}),
    # Three different curves: each channel is stored by its own
    "icc_mixed": icc({b"rTRC": para(0, 1.8), b"gTRC": SRGB_PARA, b"bTRC": para(0, 2.6)}),
}
TRANSFERS = [("srgb", None), ("bt709", None), ("pq", None), ("hlg", None)] + [
    ("icc_parametric", PROFILES["icc_parametric"]), ("icc_table", PROFILES["icc_table"]),
    ("icc_parametric", PROFILES["icc_mixed"])]
IDS = ["srgb", "bt709", "pq", "hlg", "icc_parametric", "icc_table", "icc_mixed"]


def reference_decode(name: str, profile: bytes | None, code: np.ndarray, channel: int) -> np.ndarray:
    """The decode by the colour module's own curves, not through the encoder's tables."""
    if profile is None:
        return colour.decode_transfer(name, code)
    return colour.icc_tone_curves(profile)[1][channel].decode(code)


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture], print_blob=False)
@given(light=st.lists(st.floats(0.0, 1.0, allow_nan=False), min_size=3, max_size=3))
@example(light=[0.0, 0.0, 0.0])
@example(light=[1e-7, 0.5, 1.0])
@example(light=[0.0, 0.0, 1e-12])
def test_light_is_stored_at_the_code_whose_half_steps_hold_it(name: str, profile: bytes | None, light: list[float]) -> None:
    # The curve's exact encode rounded: the code whose light half a step below is under the light and half a step
    # above is not, by the colour module's own decode
    transfer = encode.StillTransfer.of(name, profile)
    top = [float(reference_decode(name, profile, np.array(1.0), c)) for c in range(3)]
    sample = np.array([[[min(v, top[c]) for c, v in enumerate(light)]]])
    # BT.709's decode falls at its knee, from 0.018 to its power branch's value there, so near it the half steps do
    # not rise and no code holds the light alone [verified: arithmetic on BT.709-6 item 1.2]
    knee_low = float(colour.decode_transfer("bt709", np.array(colour.BT709_CODE_KNEE)))
    assume(name != "bt709" or not np.any((sample >= knee_low - 1e-6) & (sample <= colour.BT709_LIGHT_KNEE + 1e-6)))
    code = transfer.encode16(sample)[0, 0].astype(int)
    for channel in range(3):
        wanted = sample[0, 0, channel]
        below = -np.inf if code[channel] == 0 else float(reference_decode(name, profile, np.array((code[channel] - 0.5) / 65535), channel))
        above = np.inf if code[channel] == 65535 else float(reference_decode(name, profile, np.array((code[channel] + 0.5) / 65535), channel))
        assert below < wanted <= above or (below == wanted == above), (channel, int(code[channel]))


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
def test_the_unrounded_encode_gives_back_the_light_within_half_a_step(name: str, profile: bytes | None) -> None:
    rng = np.random.default_rng(4)
    codes = np.concatenate([rng.uniform(0.0, 1.0, (4000, 3)), np.linspace(0, 1, 300)[:, None].repeat(3, 1)])[None]
    transfer = encode.StillTransfer.of(name, profile)
    light = np.stack([reference_decode(name, profile, codes[..., c], c) for c in range(3)], axis=-1)
    back = np.stack([reference_decode(name, profile, transfer.encode(light)[..., c], c) for c in range(3)], axis=-1)
    # The light of one 16 bit step at each code, from the curve itself
    step = np.abs(np.stack([reference_decode(name, profile, np.minimum(codes[..., c] + STEP, 1.0), c) for c in range(3)], -1) - light)
    assert np.all(np.abs(back - light) <= 0.5 * step + 1e-15)


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
def test_the_decode_is_the_colour_modules_own(name: str, profile: bytes | None) -> None:
    codes = np.random.default_rng(5).uniform(0.0, 1.0, (50, 40, 3))
    transfer = encode.StillTransfer.of(name, profile)
    expected = np.stack([reference_decode(name, profile, codes[..., c], c) for c in range(3)], axis=-1)
    assert np.allclose(transfer.decode(codes), expected, rtol=0, atol=1e-12)


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
def test_light_beyond_black_or_peak_is_stored_at_the_end_codes(name: str, profile: bytes | None) -> None:
    transfer = encode.StillTransfer.of(name, profile)
    peak = max(float(reference_decode(name, profile, np.array(1.0), c)) for c in range(3))
    stored = transfer.encode16(np.array([[[-0.5, -1e-9, -2.0], [peak * 1.5, peak * 4, peak + 1]]]))
    assert stored.dtype == np.uint16
    assert stored[0, 0].tolist() == [0, 0, 0] and stored[0, 1].tolist() == [65535, 65535, 65535]


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
def test_the_lift_reads_the_green_curve(name: str, profile: bytes | None) -> None:
    transfer = encode.StillTransfer.of(name, profile)
    code = np.linspace(0.0, 1.0, 257)
    assert np.allclose(transfer.luma_decode(code), reference_decode(name, profile, code, 1), rtol=0, atol=1e-12)
    # The slope is the green encode's: over a small step its change in code over the change in light
    light = reference_decode(name, profile, np.linspace(0.05, 0.95, 19), 1)
    width = 1e-3 * light
    lo = reference_decode(name, profile, np.array(0.0), 1)
    up, down = np.stack([light + width] * 3, -1)[None], np.stack([light - width] * 3, -1)[None]
    numeric = (transfer.encode(up)[0, :, 1] - transfer.encode(down)[0, :, 1]) / (2 * width)
    assert np.allclose(transfer.luma_encode_slope(light), numeric, rtol=2e-2)
    assert float(lo) == pytest.approx(0.0, abs=1e-12)
    assert np.all(np.isfinite(transfer.luma_encode_slope(np.array([0.0, 1e-12, 1e-9]))))
    assert np.all(transfer.luma_encode_slope(np.array([0.0, 1e-12, 1e-9])) > 0)


def test_a_transfer_this_pipeline_does_not_carry_is_refused() -> None:
    with pytest.raises(ValueError):
        encode.StillTransfer.of("log", None)
    with pytest.raises(ValueError):
        encode.StillTransfer.of("icc_parametric", None)


def test_the_transfer_of_a_decoded_still_is_its_colour_descriptions() -> None:
    samples = np.zeros((2, 2, 3), dtype=np.uint8)
    profile = PROFILES["icc_mixed"]
    still = decode.DecodedStill(samples, {}, {"transfer": "icc_parametric"}, [], profile, False)
    transfer = encode.StillTransfer.from_still(still)
    expected = encode.StillTransfer.of("icc_parametric", profile)
    code = np.random.default_rng(6).uniform(0, 1, (3, 3, 3))
    assert np.array_equal(transfer.decode(code), expected.decode(code))
    srgb = decode.DecodedStill(samples, {}, {"transfer": "srgb"}, [], None, False)
    assert np.array_equal(encode.StillTransfer.from_still(srgb).decode(code), colour.decode_transfer("srgb", code))


def iccp(data: bytes) -> bytes | None:
    position = 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        if data[position + 4:position + 8] == b"iCCP":
            body = data[position + 8:position + 8 + length]
            name_end = body.index(b"\x00")
            return zlib.decompress(body[name_end + 2:])
        position += 12 + length
    return None


@pytest.mark.parametrize("profile", [None, PROFILES["icc_parametric"]], ids=["no profile", "profile"])
def test_a_stored_still_reads_back_to_its_codes_and_carries_the_profile(profile: bytes | None) -> None:
    rng = np.random.default_rng(7)
    codes = rng.integers(0, 65536, (37, 53, 3), dtype=np.uint16)
    data = encode.write_still(codes, profile)
    assert data == png16.write_png16(codes, profile)
    assert data == encode.write_still(codes.copy(), profile)
    assert np.array_equal(encode.read_still(data), codes)
    assert iccp(data) == profile


def test_only_an_rgb_array_of_sixteen_bit_codes_is_stored() -> None:
    with pytest.raises(ValueError):
        encode.write_still(np.zeros((4, 4, 3), dtype=np.uint8), None)
    with pytest.raises(ValueError):
        encode.StillTransfer.of("srgb", None).encode16(np.zeros((4, 4)))


def test_at_bt709s_knee_and_over_a_flat_table_run_the_encode_gives_back_the_light() -> None:
    # BT.709's decode falls at its knee and an ICC table may hold a value over a run of codes: the tables are running
    # maxima, so light there is stored at a code whose light it is, not at an arbitrary one
    transfer = encode.StillTransfer.of("bt709", None)
    knee_low = float(colour.decode_transfer("bt709", np.array(colour.BT709_CODE_KNEE)))
    light = np.linspace(knee_low - 2e-5, colour.BT709_LIGHT_KNEE + 2e-5, 4001)
    sample = np.stack([light] * 3, -1)[None]
    stored = transfer.encode16(sample)[0, :, 1]
    back = colour.decode_transfer("bt709", stored / 65535)
    step = 1 / 65535 / colour.BT709_SLOPE  # one code step of the linear branch, in light
    assert np.max(np.abs(back - light)) <= step
    unrounded = colour.decode_transfer("bt709", transfer.encode(sample)[0, :, 1])
    assert np.max(np.abs(unrounded - light)) <= 0.5 * step
    # Light the linear branch's half steps hold is stored on it: the first codes that hold it, never the power
    # branch's past the knee, which hold it again after the fall (light above the last linear half step's, up to
    # 0.018, is first held past the knee)
    last_linear_half = (math.floor(colour.BT709_CODE_KNEE * 65535) - 0.5) / 65535 / colour.BT709_SLOPE
    dip = (light >= knee_low) & (light < last_linear_half)
    assert dip.any()
    assert np.all(stored[dip] <= round(colour.BT709_CODE_KNEE * 65535))
    assert np.all(transfer.encode(sample)[0, dip, 1] <= colour.BT709_CODE_KNEE + 0.5 / 65535)
    flat = encode.StillTransfer.of("icc_table", icc({b"rTRC": curv([0, 0, 0, 30000, 65535]), b"gTRC": curv([0, 0, 0, 30000, 65535]),
                                                     b"bTRC": curv([0, 0, 0, 30000, 65535])}))
    held = flat.encode16(np.zeros((1, 1, 3)))
    assert held.tolist() == [[[0, 0, 0]]]  # light 0 is held from code 0 to half way: the first of them


def test_a_light_half_way_between_two_codes_is_stored_at_the_lower() -> None:
    transfer = encode.StillTransfer.of("srgb", None)
    for code in (10, 1000, 40000):
        tie = float(colour.decode_transfer("srgb", np.array((code + 0.5) / 65535)))
        assert transfer.encode16(np.full((1, 1, 3), tie))[0, 0].tolist() == [code] * 3


def test_the_lift_slope_is_the_slope_of_the_code_step_that_holds_the_light() -> None:
    transfer = encode.StillTransfer.of("icc_parametric", PROFILES["icc_mixed"])
    table = transfer.at_codes[encode.LIFT_CHANNEL]
    for step in (3, 40, 3000, 50000):
        light = 0.5 * (table[step] + table[step + 1])
        assert transfer.luma_encode_slope(np.array([light]))[0] == pytest.approx((1 / 65535) / (table[step + 1] - table[step]), rel=1e-12)
    # Over a run the table holds, the slope is the next rising step's, never a division by nothing
    entries = [0, 0, 0, 20000, 20000, 20000, 65535]
    held = encode.StillTransfer.of("icc_table", icc({b"rTRC": curv(entries), b"gTRC": curv(entries), b"bTRC": curv(entries)}))
    table = held.at_codes[encode.LIFT_CHANNEL]
    first_rise = int(np.argmax(np.diff(table) > 0))
    first_slope = (1 / 65535) / (table[first_rise + 1] - table[first_rise])
    # At black and below it, where the table holds 0 over its first steps
    assert held.luma_encode_slope(np.array([0.0, -1e-3]))[0] == pytest.approx(first_slope, rel=1e-12)
    assert held.luma_encode_slope(np.array([0.0, -1e-3]))[1] == pytest.approx(first_slope, rel=1e-12)
    middle = 20000 / 65535
    after = int(np.argmax((table[:-1] >= middle) & (np.diff(table) > 0)))
    assert held.luma_encode_slope(np.array([middle]))[0] == pytest.approx((1 / 65535) / (table[after + 1] - table[after]), rel=1e-12)


@pytest.mark.parametrize(("name", "profile"), TRANSFERS, ids=IDS)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture], print_blob=False)
@given(seed=st.integers(0, 2**16))
def test_the_channel_by_channel_encodes_match_the_stacked_encodes_to_the_bit(
        name: str, profile: bytes | None, seed: int) -> None:
    rng = np.random.default_rng(seed)
    light = rng.uniform(-0.05, 1.1, (17, 23, 3))
    light_before = light.copy()
    transfer = encode.StillTransfer.of(name, profile)
    steps = np.linspace(0.0, 1.0, encode.CODE_STEPS + 1)
    must_encode = np.stack([np.interp(light[..., c], transfer.at_codes[c], steps) for c in range(3)], axis=-1)
    must_encode16 = np.stack(
        [np.searchsorted(transfer.at_halves[c], light[..., c], side="left") for c in range(3)], axis=-1
    ).astype(np.uint16)
    assert np.array_equal(transfer.encode(light), must_encode)
    assert np.array_equal(transfer.encode16(light), must_encode16)
    assert np.array_equal(light, light_before)


def test_sixteen_bit_encode_holds_at_most_one_and_a_quarter_float_frames_beyond_its_input() -> None:
    # Stacking three int64 planes before casting held 2.00 float64 frames here; filling a uint16 frame channel by
    # channel holds 0.92
    import tracemalloc
    light = np.random.default_rng(46).uniform(0.0, 0.5, (300, 400, 3))
    transfer = encode.StillTransfer.of("srgb", None)
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    held = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    out = transfer.encode16(light)
    peak = tracemalloc.get_traced_memory()[1] - held
    if started:
        tracemalloc.stop()
    del out
    assert peak <= 1.25 * light.nbytes
