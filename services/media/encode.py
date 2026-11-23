"""Encoding a restored still (spec restoration-for-every-upload, design section 8; REQ-RST-3.5, REQ-RST-6.13).

A still's restoration is stored as a PNG of 16 bits per channel, encoded with the original's own transfer and
carrying its ICC profile, and the gate runs on that stored file read back [decided 2026-11-11: what is served is the
file the gate ran on]. Light becomes the 16 bit code nearest its curve's exact encode (the encode rounded, as any
encoder quantises), through a table of the curve at every 16 bit code: the decode of the colour module (`colour.py`)
at each code and at each half step between codes, so the code chosen is the one whose half steps hold the light. The unrounded encode that the gate and
the dissolve passes read (design section 7) interpolates the same table.

Each table is the running maximum of its curve, so it never falls: BT.709's two branches meet 0.000295 code apart
and its decode falls by 5.4e-5 of peak light at the knee [verified: arithmetic on the formula of Rec. ITU-R BT.709-6
item 1.2 as `colour.py` writes it], and an ICC table may hold one value over a run of codes. Where the curve does not
rise, codes cannot be told apart by their light, and the encode gives the first of them.

The lift (`tone.py`) works on one luminance and needs one curve: it reads the green channel's, the channel the luma
weights most [proposed: a profile whose three curves differ is rare; on the five design photos they are equal, M1].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from services.media import colour, decode, png16

# The codes of a 16 bit channel, from black to peak [verified in design section 8: PNG specification, bit depth 16]
CODE_STEPS = 65535
# The channel whose curve the lift reads: green [proposed: the largest of the luma weights 0.2126, 0.7152, 0.0722]
LIFT_CHANNEL = 1
NAMED = colour.TRANSFERS
FROM_PROFILE = ("icc_parametric", "icc_table")

Curve = Callable[[np.ndarray], np.ndarray]


def _curves(transfer: str, icc_profile: Optional[bytes]) -> tuple[Curve, Curve, Curve]:
    if transfer in NAMED:
        return ((lambda code: colour.decode_transfer(transfer, code)),) * 3
    if transfer in FROM_PROFILE:
        if not icc_profile:
            raise ValueError("a transfer read from an ICC profile needs the profile")
        _, curves = colour.icc_tone_curves(icc_profile)
        return tuple(curve.decode for curve in curves)  # type: ignore[return-value]
    raise ValueError(f"a transfer this pipeline does not carry: {transfer}")


@dataclass(frozen=True)
class StillTransfer:
    """Each channel's decode and its tables: the light at every 16 bit code and at every half step between codes, as
    running maxima, and the green table's slope over each code step."""

    curves: tuple[Curve, Curve, Curve]
    at_codes: tuple[np.ndarray, np.ndarray, np.ndarray]
    at_halves: tuple[np.ndarray, np.ndarray, np.ndarray]
    lift_slopes: np.ndarray

    @classmethod
    def of(cls, transfer: str, icc_profile: Optional[bytes]) -> "StillTransfer":
        curves = _curves(transfer, icc_profile)
        codes = np.arange(CODE_STEPS + 1, dtype=np.float64) / CODE_STEPS
        halves = (np.arange(CODE_STEPS, dtype=np.float64) + 0.5) / CODE_STEPS
        at_codes = tuple(np.maximum.accumulate(np.asarray(curve(codes), dtype=np.float64)) for curve in curves)
        at_halves = tuple(np.maximum.accumulate(np.asarray(curve(halves), dtype=np.float64)) for curve in curves)
        return cls(curves, at_codes, at_halves, _step_slopes(at_codes[LIFT_CHANNEL]))  # type: ignore[arg-type]

    @classmethod
    def from_still(cls, still: decode.DecodedStill) -> "StillTransfer":
        """The transfer the still's colour description names, from its own profile where it names one."""
        return cls.of(still.colour["transfer"], still.icc_profile)

    def decode(self, code: np.ndarray) -> np.ndarray:
        """Linear light of H x W x 3 code values on 0 to 1, each channel by its own curve."""
        code = np.asarray(code, dtype=np.float64)
        return np.stack([np.asarray(self.curves[c](code[..., c]), dtype=np.float64) for c in range(3)], axis=-1)

    def encode(self, light: np.ndarray) -> np.ndarray:
        """Unrounded code values on 0 to 1 of H x W x 3 light, by each channel's table read backwards; light beyond
        black or peak reads as 0 or 1."""
        light = _rgb(light)
        steps = np.linspace(0.0, 1.0, CODE_STEPS + 1)
        out = np.empty(light.shape, dtype=np.float64)
        for c in range(3):
            out[..., c] = np.interp(light[..., c], self.at_codes[c], steps)
        return out

    def encode16(self, light: np.ndarray) -> np.ndarray:
        """Each sample's 16 bit code, its curve's exact encode rounded: the number of half steps whose light lies
        below it; a light exactly at a half step takes the lower code."""
        light = _rgb(light)
        # Filled channel by channel directly into uint16 so three int64 planes are never held together [tested:
        # test_encode]
        out = np.empty(light.shape, dtype=np.uint16)
        for c in range(3):
            out[..., c] = np.searchsorted(self.at_halves[c], light[..., c], side="left")
        return out

    def luma_decode(self, code: np.ndarray) -> np.ndarray:
        """The lift's decode: the green curve."""
        return np.asarray(self.curves[LIFT_CHANNEL](np.asarray(code, dtype=np.float64)), dtype=np.float64)

    def luma_encode_slope(self, light: np.ndarray) -> np.ndarray:
        """The lift's encode slope, code over light: the slope of the green table over the code step that holds
        `light`, the slope of the encode `encode` computes."""
        light = np.asarray(light, dtype=np.float64)
        index = np.clip(np.searchsorted(self.at_codes[LIFT_CHANNEL], light, side="right") - 1, 0, CODE_STEPS - 1)
        return self.lift_slopes[index]


def _step_slopes(table: np.ndarray) -> np.ndarray:
    """Code over light on each of the table's steps. A step over which the table does not rise holds no light of its
    own, and takes the slope of the next step that rises, or of the last one when none does after it."""
    rise = np.diff(table)
    rising = rise > 0
    if not np.any(rising):
        raise ValueError("a curve that never rises has no slope")
    index = np.arange(rise.size)
    following = np.where(rising, index, rise.size)
    following = np.minimum.accumulate(following[::-1])[::-1]
    preceding = np.maximum.accumulate(np.where(rising, index, -1))
    chosen = np.where(following < rise.size, following, preceding)
    return (1.0 / CODE_STEPS) / rise[chosen]


def _rgb(light: np.ndarray) -> np.ndarray:
    light = np.asarray(light, dtype=np.float64)
    if light.ndim != 3 or light.shape[2] != 3:
        raise ValueError("a still is encoded from an H x W x 3 array of light")
    return light


def write_still(codes: np.ndarray, icc_profile: Optional[bytes]) -> bytes:
    """The stored file: the 16 bit PNG of the codes, with the original's profile when it has one."""
    return png16.write_png16(codes, icc_profile)


def read_still(data: bytes) -> np.ndarray:
    """The stored file's codes, read back through ffmpeg as the gate reads them (`decode.read_png`)."""
    return decode.read_png(data)
