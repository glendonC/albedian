"""Colour decode and encode for restoration (spec restoration-for-every-upload, design section 1; REQ-RST-3.4 to 3.6):
the four transfer functions and the ICC curves, as pure functions on arrays.

A transfer's decode turns a code value V into linear light L, both 0 to 1, by the standard's own formula; its encode
is the exact inverse. Below 0 each function is continued as an odd function and above 1 by its slope at 1, so the
footroom and headroom of limited range and chroma noise at black pass through both ways without clipping [proposed
in design section 1: clipping at black would raise the dark level and break the exact round trip].
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.linalg import solveh_banded

TRANSFERS = ("srgb", "bt709", "hlg", "pq")

# sRGB [verified in design section 1: Rec. ITU-T H.273 (07/2024) Table 3, value 13, the two segment form]
SRGB_SLOPE, SRGB_CODE_KNEE, SRGB_LIGHT_KNEE = 12.92, 0.04045, 0.0031308
SRGB_SCALE, SRGB_OFFSET, SRGB_GAMMA = 1.055, 0.055, 2.4  # [verified in design section 1: H.273 Table 3, value 13]
# BT.709 [verified in design section 1: Rec. ITU-R BT.709-6 (06/2015) item 1.2]
BT709_SLOPE, BT709_CODE_KNEE, BT709_LIGHT_KNEE = 4.5, 0.081, 0.018
BT709_SCALE, BT709_OFFSET, BT709_POWER = 1.099, 0.099, 0.45  # [verified in design section 1: BT.709-6 item 1.2]
# HLG [verified in design section 1: H.273 Table 3, value 18]
HLG_A, HLG_B, HLG_C = 0.17883277, 0.28466892, 0.55991073
# PQ [verified in design section 1: H.273 Table 3, value 16; L = 1 is 10 000 candelas per square metre]
PQ_C1, PQ_C2, PQ_C3, PQ_M, PQ_N = 107 / 128, 2413 / 128, 2392 / 128, 2523 / 32, 1305 / 8192


def _srgb_decode(v: np.ndarray) -> np.ndarray:
    return np.where(v <= SRGB_CODE_KNEE, v / SRGB_SLOPE, ((v + SRGB_OFFSET) / SRGB_SCALE) ** SRGB_GAMMA)


def _srgb_encode(light: np.ndarray) -> np.ndarray:
    return np.where(light <= SRGB_LIGHT_KNEE, SRGB_SLOPE * light, SRGB_SCALE * light ** (1 / SRGB_GAMMA) - SRGB_OFFSET)


def _bt709_decode(v: np.ndarray) -> np.ndarray:
    return np.where(v < BT709_CODE_KNEE, v / BT709_SLOPE, ((v + BT709_OFFSET) / BT709_SCALE) ** (1 / BT709_POWER))


def _bt709_encode(light: np.ndarray) -> np.ndarray:
    return np.where(light < BT709_LIGHT_KNEE, BT709_SLOPE * light, BT709_SCALE * light**BT709_POWER - BT709_OFFSET)


def _hlg_decode(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.5, v * v / 3, (np.exp((v - HLG_C) / HLG_A) + HLG_B) / 12)


def _hlg_encode(light: np.ndarray) -> np.ndarray:
    # The log branch is evaluated only where it applies, so no log of a negative number is ever taken
    upper = np.maximum(light, 1 / 12)
    return np.where(light <= 1 / 12, np.sqrt(3 * light), HLG_A * np.log(12 * upper - HLG_B) + HLG_C)


def _pq_decode(v: np.ndarray) -> np.ndarray:
    p = v ** (1 / PQ_M)
    return (np.maximum(p - PQ_C1, 0.0) / (PQ_C2 - PQ_C3 * p)) ** (1 / PQ_N)


def _pq_encode(light: np.ndarray) -> np.ndarray:
    power = light**PQ_N
    return ((PQ_C1 + PQ_C2 * power) / (1 + PQ_C3 * power)) ** PQ_M


_CURVES: dict[str, tuple[Callable[[np.ndarray], np.ndarray], Callable[[np.ndarray], np.ndarray]]] = {
    "srgb": (_srgb_decode, _srgb_encode),
    "bt709": (_bt709_decode, _bt709_encode),
    "hlg": (_hlg_decode, _hlg_encode),
    "pq": (_pq_decode, _pq_encode),
}


def _end_slope(function: Callable[[np.ndarray], np.ndarray], at: float) -> float:
    """The slope at 1 of a curve on 0 to 1, from the left: a difference over a step of 2^-26 [proposed: its error is
    of the order of the step times the second derivative, far under the continuation's use]."""
    step = 2.0**-26
    return float((function(np.array(1.0)) - function(np.array(1.0 - step))) / step) if at == 1.0 else math.nan


def _continued(function: Callable[[np.ndarray], np.ndarray], x: np.ndarray) -> np.ndarray:
    """`function` on 0 to 1, odd below 0, and straight with its end slope above 1."""
    x = np.asarray(x, dtype=np.float64)
    size = np.abs(x)
    value = function(np.clip(size, 0.0, 1.0))
    beyond = size > 1.0
    if np.any(beyond):
        value = np.where(beyond, float(function(np.array(1.0))) + _end_slope(function, 1.0) * (size - 1.0), value)
    # Odd below 0 only: at 0 itself the formula holds (PQ encodes 0 as c1^m, not as 0)
    return np.where(x < 0, -value, value)


def decode_transfer(name: str, code: np.ndarray) -> np.ndarray:
    """Linear light from code values, by the transfer's own formula (continued beyond 0 to 1)."""
    if name not in _CURVES:
        raise ValueError(f"a transfer this pipeline does not carry: {name}")
    return _continued(_CURVES[name][0], code)


def encode_transfer(name: str, light: np.ndarray) -> np.ndarray:
    """Code values from linear light, the exact inverse of `decode_transfer`."""
    if name not in _CURVES:
        raise ValueError(f"a transfer this pipeline does not carry: {name}")
    decode, encode = _CURVES[name]
    light = np.asarray(light, dtype=np.float64)
    # Beyond the decode's end value, the inverse of its straight continuation
    end_light = float(decode(np.array(1.0)))
    size = np.abs(light)
    value = encode(np.clip(size, 0.0, end_light))
    beyond = size > end_light
    if np.any(beyond):
        value = np.where(beyond, 1.0 + (size - end_light) / _end_slope(decode, 1.0), value)
    return np.where(light < 0, -value, value)


# ICC parametric curves [verified in design section 1: ICC.1:2022 clause 10.18, Tables 67 and 68]: the number of
# parameters each type takes, in the order g, a, b, c, d, e, f
ICC_PARAMETERS = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}
# Halvings of 0 to 1 that invert a curve [proposed: 60 put the answer within 2^-60 of the value]
INVERSE_STEPS = 60
# Points on 0 to 1 at which a parametric curve is checked never to fall before it is inverted [proposed: a knee that
# drops is a jump at one point, which any grid that holds the knee's two sides shows; 4097 points are 2^-12 apart]
MONOTONE_CHECK_POINTS = 4097


def _invert(curve: Callable[[np.ndarray], np.ndarray], y: np.ndarray) -> np.ndarray:
    """The x in 0 to 1 at which a curve that never falls reaches y, by bisection, for every value at once."""
    y = np.asarray(y, dtype=np.float64)
    low, high = np.zeros_like(y), np.ones_like(y)
    for _ in range(INVERSE_STEPS):
        middle = (low + high) / 2
        under = curve(middle) < y
        low, high = np.where(under, middle, low), np.where(under, high, middle)
    return (low + high) / 2


@dataclass(frozen=True)
class IccParametric:
    """An ICC parametric curve: device value X to linear Y, both 0 to 1."""

    kind: int
    parameters: tuple[float, ...]

    def __post_init__(self) -> None:
        if ICC_PARAMETERS.get(self.kind) != len(self.parameters):
            raise ValueError("an ICC parametric curve takes the number of parameters its type names")
        if self.kind in (1, 2) and self.parameters[1] == 0:
            raise ValueError("an ICC parametric curve of type 1 or 2 divides by scale a, which is zero")

    def decode(self, x: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
        g = self.parameters[0]
        if self.kind == 0:
            return x**g
        a, b = self.parameters[1], self.parameters[2]
        on = np.maximum(a * x + b, 0.0) ** g
        if self.kind == 1:
            return np.where(x >= -b / a, on, 0.0)
        if self.kind == 2:
            return np.where(x >= -b / a, on + self.parameters[3], self.parameters[3])
        c, d = self.parameters[3], self.parameters[4]
        if self.kind == 3:
            return np.where(x >= d, on, c * x)
        e, f = self.parameters[5], self.parameters[6]
        return np.where(x >= d, on + e, c * x + f)

    def encode(self, y: np.ndarray) -> np.ndarray:
        grid = np.linspace(0.0, 1.0, MONOTONE_CHECK_POINTS)
        knee = [] if self.kind < 3 else [self.parameters[4], np.nextafter(self.parameters[4], -1.0)]
        with np.errstate(divide="ignore", invalid="ignore"):
            checked = self.decode(np.sort(np.concatenate([grid, np.clip(knee, 0.0, 1.0)])))
        if not np.all(np.isfinite(checked)) or np.any(np.diff(checked) < 0):
            raise ValueError("an ICC curve that falls somewhere has no inverse")
        return _invert(self.decode, y)


@dataclass(frozen=True)
class IccTable:
    """An ICC table curve (curv): no entry is the identity, one entry a gamma in u8Fixed8, more entries a table of 16
    bit values read by linear interpolation [inference in design section 1: ICC.1:2022 clause 10.6]."""

    entries: tuple[int, ...]

    def decode(self, x: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
        if not self.entries:
            return x.copy()
        if len(self.entries) == 1:
            return x ** (self.entries[0] / 256)
        table = np.asarray(self.entries, dtype=np.float64) / 65535
        return np.interp(x, np.linspace(0.0, 1.0, len(table)), table)

    def encode(self, y: np.ndarray) -> np.ndarray:
        y = np.asarray(y, dtype=np.float64)
        if len(self.entries) < 2:
            if not self.entries:
                return y.copy()
            if self.entries[0] <= 0:
                raise ValueError("an ICC table gamma of zero has no inverse")
            return np.clip(y, 0.0, 1.0) ** (256 / self.entries[0])
        table = np.asarray(self.entries, dtype=np.float64) / 65535
        if np.any(np.diff(table) <= 0):
            raise ValueError("an ICC table that does not rise strictly has no inverse")
        return np.interp(y, table, np.linspace(0.0, 1.0, len(table)))


# Y'CbCr. K_R and K_B of each matrix [verified in design section 1: H.273 Table 4, values 1, 5 and 6, 9]
MATRICES = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "bt2020nc": (0.2627, 0.0593)}
RANGES = ("limited", "full")


def legal_codes(bits: int, colour_range: str) -> tuple[int, int]:
    """The lowest and highest code a sample may hold [proposed in the restore-2a question: limited range keeps
    BT.709-6 item 3.4's timing codes free (8 bits: 0 and 255; 10 bits: 0 to 3 and 1020 to 1023); full range uses
    every code]."""
    if bits not in (8, 10, 16) or colour_range not in RANGES:
        raise ValueError("a range and a bit depth this pipeline carries")
    if colour_range == "full":
        return 0, 2**bits - 1
    reserved = 2 ** (bits - 8)
    return reserved, 2**bits - 1 - reserved


def codes_to_ycbcr(y: np.ndarray, cb: np.ndarray, cr: np.ndarray, bits: int, colour_range: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Y' in 0 to 1 and Cb, Cr in -1/2 to 1/2 from n bit codes [verified in design section 1: limited range BT.709-6
    item 3.4 and H.273 equations 30 to 32; full range H.273 equations 36 to 38]."""
    legal_codes(bits, colour_range)
    y, cb, cr = (np.asarray(plane, dtype=np.float64) for plane in (y, cb, cr))
    if colour_range == "limited":
        scale = 2.0 ** (bits - 8)
        return (y / scale - 16) / 219, (cb / scale - 128) / 224, (cr / scale - 128) / 224
    top, middle = 2.0**bits - 1, 2.0 ** (bits - 1)
    return y / top, (cb - middle) / top, (cr - middle) / top


def ycbcr_to_codes(y: np.ndarray, cb: np.ndarray, cr: np.ndarray, bits: int, colour_range: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The nearest n bit codes, kept inside the range's legal codes: the inverse of `codes_to_ycbcr`."""
    low, high = legal_codes(bits, colour_range)
    if colour_range == "limited":
        scale = 2.0 ** (bits - 8)
        values = ((219 * y + 16) * scale, (224 * cb + 128) * scale, (224 * cr + 128) * scale)
    else:
        top, middle = 2.0**bits - 1, 2.0 ** (bits - 1)
        values = (y * top, cb * top + middle, cr * top + middle)
    return tuple(np.clip(np.rint(np.asarray(v, dtype=np.float64)), low, high).astype(np.int64) for v in values)


def ycbcr_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray, matrix: str) -> np.ndarray:
    """R'G'B' from Y'CbCr, H x W x 3 [verified in design section 1: H.273 equations 45 to 47, solved for R', G', B']."""
    if matrix not in MATRICES:
        raise ValueError(f"a matrix this pipeline does not carry: {matrix}")
    kr, kb = MATRICES[matrix]
    r = y + 2 * (1 - kr) * cr
    b = y + 2 * (1 - kb) * cb
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    return np.stack([r, g, b], axis=-1)


def rgb_to_ycbcr(rgb: np.ndarray, matrix: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Y'CbCr from R'G'B' (H.273 equations 45 to 47): the inverse of `ycbcr_to_rgb`."""
    if matrix not in MATRICES:
        raise ValueError(f"a matrix this pipeline does not carry: {matrix}")
    kr, kb = MATRICES[matrix]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = kr * r + (1 - kr - kb) * g + kb * b
    return y, (b - y) / (2 * (1 - kb)), (r - y) / (2 * (1 - kr))


# Chroma: subsampling as (horizontal, vertical) factors, and where each siting puts a chroma sample on each axis it
# is subsampled on, as an offset in luma samples from the first luma sample of its pair [verified in design section 1:
# BT.2020-2 Table 5 sites chroma on the top left luma sample, BT.709-6 item 4.3 on alternate luma samples of the
# line; inference in design section 1: `left` is co-sited across and centred between lines, `center` centred both
# ways, as ffmpeg's chroma_location names them]
SUBSAMPLING = {"444": (1, 1), "422": (2, 1), "420": (2, 2)}
# [verified in design section 1: BT.2020-2 Table 5 sites on (0, 0), BT.709-6 item 4.3 on (0, 0.5); center is (0.5, 0.5)]
SITING_OFFSETS = {"left": (0.0, 0.5), "center": (0.5, 0.5), "topleft": (0.0, 0.0)}


def interpolation_taps(chroma_count: int, luma_count: int, factor: int, offset: float) -> tuple[np.ndarray, np.ndarray]:
    """For each luma sample, the chroma sample at or before it and the weight of the next one: luma sample i reads
    (1 - t) c[k] + t c[k + 1] from chroma sited `offset` luma samples after the first luma sample of each group of
    `factor`; before the first and past the last chroma sample the edge value is held [proposed in design section 1:
    linear interpolation, no negative weight]."""
    i = np.arange(luma_count, dtype=np.float64)
    if factor == 1:
        return np.minimum(np.arange(luma_count), chroma_count - 1), np.zeros(luma_count)
    place = np.clip((i - offset) / factor, 0.0, chroma_count - 1.0)
    k = np.floor(place).astype(np.int64)
    return k, place - k


def interpolation_weights(chroma_count: int, luma_count: int, factor: int, offset: float) -> np.ndarray:
    """The luma_count x chroma_count matrix of `interpolation_taps`, for tests and small sizes."""
    k, t = interpolation_taps(chroma_count, luma_count, factor, offset)
    weights = np.zeros((luma_count, chroma_count))
    rows = np.arange(luma_count)
    np.add.at(weights, (rows, k), 1.0 - t)
    np.add.at(weights, (rows, np.minimum(k + 1, chroma_count - 1)), t)
    return weights


def _axis_offsets(subsampling: str, siting: str | None) -> tuple[tuple[int, float], tuple[int, float]]:
    """((factor, offset) down the rows, (factor, offset) across the columns) of a subsampling at a siting."""
    across, down = SUBSAMPLING[subsampling]
    if (across, down) == (1, 1):
        return (1, 0.0), (1, 0.0)
    if siting not in SITING_OFFSETS:
        raise ValueError(f"a chroma siting this pipeline does not carry: {siting}")
    offset_x, offset_y = SITING_OFFSETS[siting]
    return (down, offset_y if down > 1 else 0.0), (across, offset_x)


def _up_rows(plane: np.ndarray, luma_count: int, factor: int, offset: float) -> np.ndarray:
    k, t = interpolation_taps(plane.shape[0], luma_count, factor, offset)
    after = np.minimum(k + 1, plane.shape[0] - 1)
    return plane[k] * (1.0 - t)[:, None] + plane[after] * t[:, None]


def chroma_up(plane: np.ndarray, height: int, width: int, subsampling: str, siting: str | None) -> np.ndarray:
    """A chroma plane brought to the luma size by linear interpolation at its siting."""
    plane = np.asarray(plane, dtype=np.float64)
    (down, offset_y), (across, offset_x) = _axis_offsets(subsampling, siting)
    if (across, down) == (1, 1):
        return plane[:height, :width].copy()
    tall = _up_rows(plane, height, down, offset_y)
    return _up_rows(tall.T, width, across, offset_x).T


def _down_rows(full: np.ndarray, chroma_count: int, factor: int, offset: float) -> np.ndarray:
    """The chroma rows c minimising || W c - full ||^2 for this axis's weights W: the normal equations W^T W c =
    W^T full, tridiagonal because each luma sample reads two neighbouring chroma samples, solved by Cholesky."""
    k, t = interpolation_taps(chroma_count, full.shape[0], factor, offset)
    after = np.minimum(k + 1, chroma_count - 1)
    diagonal, upper = np.zeros(chroma_count), np.zeros(chroma_count)
    np.add.at(diagonal, k, (1.0 - t) ** 2)
    np.add.at(diagonal, after, t**2)
    # A weight t > 0 always has after = k + 1; its cross term lies above the diagonal at column k + 1
    np.add.at(upper, after, np.where(after > k, (1.0 - t) * t, 0.0))
    right = np.zeros((chroma_count, full.shape[1]))
    np.add.at(right, k, full * (1.0 - t)[:, None])
    np.add.at(right, after, full * t[:, None])
    if chroma_count == 1:
        # One unknown: the band solver takes no band wider than the system
        return right / diagonal[:, None]
    return solveh_banded(np.vstack([upper, diagonal]), right)


# The most a sample's error in the full size chroma can grow on the way down, per axis: the largest row sum of the
# left inverse's magnitudes [tested by test_colour.py from 2 to 1080 luma samples, measured up to 4032: 1 + 1/sqrt(2)
# = 1.7071 co-sited, 2.0000 centred], so 4:2:0 at most the product of its two axes
DOWN_GAIN_PER_AXIS = 2.0


def chroma_down(plane: np.ndarray, subsampling: str, siting: str | None) -> np.ndarray:
    """The subsampled chroma whose interpolation by `chroma_up`'s own weights at this siting is closest, in least
    squares over every sample, to the full size `plane` (LEAD ANSWER 2026-11-12 20:59). On a plane that was only
    interpolated it is the original chroma exactly; on a processed one it uses every sample. Its size is the luma
    size halved on each subsampled axis, rounded up."""
    plane = np.asarray(plane, dtype=np.float64)
    (down, offset_y), (across, offset_x) = _axis_offsets(subsampling, siting)
    if (across, down) == (1, 1):
        return plane.copy()
    height, width = plane.shape
    short = _down_rows(plane, math.ceil(height / down), down, offset_y) if down > 1 else plane
    return _down_rows(short.T, math.ceil(width / across), across, offset_x).T


# An ICC profile's tone curves [tested on sips HEIC ICC profiles: a 128 byte header before the tag count and 12 byte
# entries of signature, offset and size (ICC.1:2022 clause 7.3); verified in design section 1: para in clause 10.18]
ICC_HEADER_BYTES = 128
RGB_CURVE_TAGS = (b"rTRC", b"gTRC", b"bTRC")
GREY_CURVE_TAG = b"kTRC"


class IccError(ValueError):
    """A profile whose tone curves this reader cannot read, with the reason."""


def _icc_tags(profile: bytes) -> dict[bytes, bytes]:
    if len(profile) < ICC_HEADER_BYTES + 4:
        raise IccError("the profile is shorter than its header")
    count = int.from_bytes(profile[128:132], "big")
    if 132 + 12 * count > len(profile):
        raise IccError("the profile's tag table runs past its end")
    tags = {}
    for index in range(count):
        entry = profile[132 + 12 * index : 144 + 12 * index]
        offset, size = int.from_bytes(entry[4:8], "big"), int.from_bytes(entry[8:12], "big")
        if offset + size > len(profile):
            raise IccError("a tag runs past the profile's end")
        tags[bytes(entry[:4])] = bytes(profile[offset : offset + size])
    return tags


def _icc_curve(body: bytes) -> IccParametric | IccTable:
    kind = body[:4]
    if kind == b"para" and len(body) >= 12:
        function = int.from_bytes(body[8:10], "big")
        if function not in ICC_PARAMETERS or len(body) < 12 + 4 * ICC_PARAMETERS[function]:
            raise IccError("a parametric curve of a type the clause does not list, or cut short")
        values = tuple(int.from_bytes(body[12 + 4 * i : 16 + 4 * i], "big", signed=True) / 65536 for i in range(ICC_PARAMETERS[function]))
        try:
            return IccParametric(function, values)
        except ValueError as exc:
            raise IccError(str(exc)) from None
    if kind == b"curv" and len(body) >= 12:
        count = int.from_bytes(body[8:12], "big")
        if len(body) < 12 + 2 * count:
            raise IccError("a table curve cut short")
        return IccTable(tuple(int.from_bytes(body[12 + 2 * i : 14 + 2 * i], "big") for i in range(count)))
    raise IccError("a tone curve that is neither curv nor para")


def icc_tone_curves(profile: bytes) -> tuple[str, tuple[IccParametric | IccTable, ...]]:
    """The red, green and blue tone curves of a profile (its kTRC three times for a grey profile), and the transfer
    a colour description names for them: `icc_parametric` when all three are parametric, `icc_table` when all three
    are tables. A profile with no tone curves, or with curves of both kinds, is refused."""
    tags = _icc_tags(profile)
    if all(tag in tags for tag in RGB_CURVE_TAGS):
        curves = tuple(_icc_curve(tags[tag]) for tag in RGB_CURVE_TAGS)
    elif GREY_CURVE_TAG in tags:
        curves = (_icc_curve(tags[GREY_CURVE_TAG]),) * 3
    else:
        raise IccError("the profile has no tone curves")
    kinds = {type(curve) for curve in curves}
    if kinds == {IccParametric}:
        return "icc_parametric", curves
    if kinds == {IccTable}:
        return "icc_table", curves
    raise IccError("the profile mixes parametric and table curves")
