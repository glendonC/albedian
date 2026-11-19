"""A clip's frames between decode and encode (spec restoration-for-every-upload, design sections 10 and 11; REQ-RST-3.7,
4.1 to 4.5, 7.1 to 7.4, 7.8).

The stream's own Y'CbCr codes are taken to linear light by its own tags (design section 1), and back. The noise table
and the lift are measured once for the clip, from frames spread over the whole of it, so the lift cannot flicker.
Each frame is then made with its registered neighbours (`temporal`), its stars found on its own original pixels
(`stars`), filtered (`spatial`) and lifted (`tone`), in a run of the bounded pool (`frame_pool`). Without a noise table
there is no noise reduction (REQ-RST-4.3): the frame is only lifted. The dissolve passes and the gate belong to
`gate.py` (design section 7); restore-7 puts them between `prepare_frame` and `finish_frame`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from services.media import clip_io, colour, frame_pool, noise, spatial, stars, temporal, tone
from services.media.decode import (
    ASSUMED_IN_DESIGN,
    MEASURED_FROM_FORMAT,
    MEASURED_FROM_TAGS,
    STREAM_MATRICES,
    STREAM_RANGES,
    STREAM_SITINGS,
    UNTAGGED,
    Field,
    _description,
    _parameters,
)

STEP_VERSION = "1.0.0"
# The noise table and the lift are measured on this many frames spread over the whole clip, the first and the last
# among them [proposed: REQ-RST-4.1 asks for frames spread over the clip; eight 2.4 MP frames give the table about
# 300 000 blocks of 8 px, far more than its twelve bins need]
SAMPLE_FRAMES = 8
# A run carries one frame more than the merge's window either side, so each neighbour has the frame before it, which
# its codec copies are found against [proposed: design section 10 finds a copy against the previous original frame]
RUN_WINDOW = temporal.WINDOW_FRAMES + 1
# A frame 720 lines or taller with no matrix tag is taken as BT.709, shorter as BT.601 [verified: the contract's
# assumption reasons, services/contracts/media.py ASSUMPTION_REASONS]
HD_LINES = 720
# The derivative of the transfer's encode is taken by a central difference this share of the light either side
# [proposed: far under the curvature of any of the four transfers at the levels the lift reads]
SLOPE_STEP_SHARE = 1e-4
# and at least this much light [proposed: keeps the step positive at black, where the dark floor of tone.py is 1e-9]
SLOPE_STEP_FLOOR = 1e-12

LUMA = stars.LUMA_WEIGHTS
# ffmpeg's transfer tags by the curve each names [verified in design section 1: H.273 Table 3, values 1, 6, 14 and 15
# share BT.709's formula; 13 is sRGB, 16 PQ, 18 HLG; inference: ffmpeg's names for those values]
STREAM_TRANSFERS = {"bt709": "bt709", "smpte170m": "bt709", "bt2020-10": "bt709", "bt2020-12": "bt709",
                    "iec61966-2-1": "srgb", "arib-std-b67": "hlg", "smpte2084": "pq"}
# ffmpeg's primaries tags the contract can name; primaries are carried, never converted [verified in design section 1:
# H.273 Table 2 values 1 and 9, and 12 (SMPTE EG 432-1, Display P3)]
STREAM_PRIMARIES = {"bt709": "bt709", "bt2020": "bt2020", "smpte432": "p3_d65"}
# A pixel format's chroma shifts (across, down) by its subsampling's name [verified: clip_io.PIXEL_FORMATS, ffmpeg's
# log2 chroma shifts, and the contract's CHROMA_SUBSAMPLINGS]
SUBSAMPLINGS = {(0, 0): "444", (1, 0): "422", (1, 1): "420"}


@dataclass(frozen=True)
class ClipColour:
    """How the stream's samples are coded: its pixel format, bit depth, subsampling, size and colour fields, each read
    from its tags or assumed with the design's reason."""

    pix_fmt: str
    bits: int
    subsampling: str
    width: int
    height: int
    fields: dict = field(compare=False)

    @property
    def transfer(self) -> str:
        return self.fields["transfer"].value

    @property
    def matrix(self) -> str:
        return self.fields["matrix"].value

    @property
    def range(self) -> str:
        return self.fields["range"].value

    @property
    def siting(self) -> Optional[str]:
        return self.fields["chromaSiting"].value if "chromaSiting" in self.fields else None

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(np.uint8) if self.bits == 8 else np.dtype(np.uint16)

    def peak_code(self) -> int:
        """Luma's nominal white: 235 of 255 scaled for limited range, the top code for full range."""
        return 235 * 2 ** (self.bits - 8) if self.range == "limited" else 2**self.bits - 1

    def code_steps(self) -> int:
        """Luma's codes from black to white."""
        return 219 * 2 ** (self.bits - 8) if self.range == "limited" else 2**self.bits - 1

    def _factors(self) -> tuple[int, int]:
        across, down = colour.SUBSAMPLING[self.subsampling]
        return across, down

    def to_linear(self, planes: Sequence[np.ndarray]) -> np.ndarray:
        """Linear light H x W x 3 from the stream's Y', Cb and Cr codes."""
        y, cb, cr = (np.asarray(plane, dtype=np.float64) for plane in planes)
        height, width = y.shape
        if self.subsampling != "444":
            cb = colour.chroma_up(cb, height, width, self.subsampling, self.siting)
            cr = colour.chroma_up(cr, height, width, self.subsampling, self.siting)
        luma, blue, red = colour.codes_to_ycbcr(y, cb, cr, self.bits, self.range)
        return colour.decode_transfer(self.transfer, colour.ycbcr_to_rgb(luma, blue, red, self.matrix))

    def to_planes(self, light: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The stream's own codes from linear light: the inverse of `to_linear`, chroma brought down by the least
        squares inverse of its own interpolation."""
        luma, blue, red = colour.rgb_to_ycbcr(colour.encode_transfer(self.transfer, light), self.matrix)
        if self.subsampling != "444":
            blue = colour.chroma_down(blue, self.subsampling, self.siting)
            red = colour.chroma_down(red, self.subsampling, self.siting)
        codes = colour.ycbcr_to_codes(luma, blue, red, self.bits, self.range)
        return tuple(np.ascontiguousarray(plane.astype(self.dtype)) for plane in codes)

    def luma_codes(self, planes: Sequence[np.ndarray]) -> np.ndarray:
        """The Y' plane, the luma the detector and registration read."""
        return np.asarray(planes[0], dtype=np.float64)

    def stacked_codes(self, planes: Sequence[np.ndarray]) -> np.ndarray:
        """H x W x 3 integers: Y' and the chroma sample that covers each pixel, for exact comparisons."""
        y = np.asarray(planes[0])
        height, width = y.shape
        across, down = self._factors()
        chroma = [np.repeat(np.repeat(np.asarray(plane), down, axis=0), across, axis=1)[:height, :width] for plane in planes[1:]]
        return np.stack([y, *chroma], axis=-1).astype(np.int64)

    def decode(self, code: np.ndarray) -> np.ndarray:
        """Light from code values, 0 to 1, by the transfer."""
        return colour.decode_transfer(self.transfer, np.asarray(code, dtype=np.float64))

    def encode_slope(self, light: np.ndarray) -> np.ndarray:
        """The derivative of the transfer's encode, code over light, by a central difference."""
        light = np.asarray(light, dtype=np.float64)
        step = np.maximum(SLOPE_STEP_SHARE * np.abs(light), SLOPE_STEP_FLOOR)
        low = np.maximum(light - step, 0.0)
        high = light + step
        return (colour.encode_transfer(self.transfer, high) - colour.encode_transfer(self.transfer, low)) / (high - low)

    def description(self) -> dict:
        """The stream's `ColourDescription` (contract section 14)."""
        return _description("ycbcr", self.bits, self.fields, self.subsampling, None)

    def parameters(self) -> list[dict]:
        """The decode step's parameters, each measured with its basis or a default with the design's reason."""
        return _parameters(self.fields, self.subsampling, self.pix_fmt, self.bits)


def _refuse(detail: str) -> clip_io.ClipError:
    return clip_io.ClipError("unsupported", detail)


def clip_colour(video: clip_io.VideoFormat) -> ClipColour:
    """The stream's colour from its probe: each field from its own tag, a full range pixel format as full range, and
    the design's assumption where the stream states nothing. A value the contract cannot name is refused plainly."""
    bits, shift_x, shift_y = clip_io.PIXEL_FORMATS[video.pix_fmt]
    subsampling = SUBSAMPLINGS[(shift_x, shift_y)]
    tags = video.colour
    fields: dict[str, Field] = {}
    if tags.transfer in UNTAGGED:
        fields["transfer"] = Field("bt709", ASSUMED_IN_DESIGN, assumed=True)
    elif tags.transfer in STREAM_TRANSFERS:
        fields["transfer"] = Field(STREAM_TRANSFERS[tags.transfer], MEASURED_FROM_TAGS)
    else:
        raise _refuse("a transfer other than sRGB, BT.709, HLG or PQ")
    if tags.primaries in UNTAGGED:
        fields["primaries"] = Field("unspecified", ASSUMED_IN_DESIGN, assumed=True)
    elif tags.primaries in STREAM_PRIMARIES:
        fields["primaries"] = Field(STREAM_PRIMARIES[tags.primaries], MEASURED_FROM_TAGS)
    else:
        raise _refuse("colour primaries this pipeline cannot tag")
    if tags.matrix in UNTAGGED:
        fields["matrix"] = Field("bt709" if video.height >= HD_LINES else "bt601", ASSUMED_IN_DESIGN, assumed=True)
    elif tags.matrix in STREAM_MATRICES:
        fields["matrix"] = Field(STREAM_MATRICES[tags.matrix], MEASURED_FROM_TAGS)
    else:
        raise _refuse("a colour matrix other than BT.601, BT.709 or BT.2020 non-constant luminance")
    if tags.range not in UNTAGGED:
        fields["range"] = Field(STREAM_RANGES[tags.range], MEASURED_FROM_TAGS)
    elif video.pix_fmt.startswith("yuvj"):
        fields["range"] = Field("full", MEASURED_FROM_FORMAT)
    else:
        fields["range"] = Field("limited", ASSUMED_IN_DESIGN, assumed=True)
    if subsampling != "444":
        if tags.chroma_location in UNTAGGED:
            fields["chromaSiting"] = Field("left", ASSUMED_IN_DESIGN, assumed=True)
        elif tags.chroma_location in STREAM_SITINGS:
            fields["chromaSiting"] = Field(STREAM_SITINGS[tags.chroma_location], MEASURED_FROM_TAGS)
        else:
            raise _refuse("a chroma siting this pipeline cannot tag")
    return ClipColour(video.pix_fmt, bits, subsampling, video.width, video.height, fields)


# ---------------------------------------------------------------------------------------------------------------
# Once per clip
# ---------------------------------------------------------------------------------------------------------------


def sample_indices(frame_count: int, samples: int = SAMPLE_FRAMES) -> list[int]:
    """The frames the noise table and the lift are measured on: `samples` of them evenly spread from the first to the
    last, or every frame of a shorter clip."""
    if frame_count <= samples:
        return list(range(frame_count))
    return sorted({int(round(k * (frame_count - 1) / (samples - 1))) for k in range(samples)})


@dataclass(frozen=True)
class ClipSettings:
    """What is measured once for the clip and used for every frame: its colour, its noise table and its lift."""

    colour: ClipColour
    measurement: noise.NoiseMeasurement
    lift: tone.LiftSolution
    frames_measured: int
    stars_found: int
    stars_confirmed: int


def _whole_blocks(array: np.ndarray) -> np.ndarray:
    """The rows of a frame that make whole blocks of the noise table, so no block of a mosaic spans two frames."""
    return array[: array.shape[0] // noise.BLOCK_PX * noise.BLOCK_PX]


def measure_clip(clip: ClipColour, sampled: Sequence[Sequence[np.ndarray]], exposure_ev: Optional[float] = None) -> ClipSettings:
    """The clip's noise table and lift from its measure frames (`sample_indices`), side by side as one mosaic: the
    stars of each frame on its own luma, the table from the flattest blocks, the noise left after the spatial filter at
    the darkest blocks, and the lift solved on the frames' median and its measured bounds. Without a table the lift is
    held by the quantisation bound alone (REQ-RST-4.3)."""
    linear, codes, footprints, clipped, found, confirmed = [], [], [], [], 0, 0
    for planes in sampled:
        light = clip.to_linear(planes)
        luma = clip.luma_codes(planes)
        field_ = stars.find_stars(luma, peak=clip.peak_code())
        found += field_.found
        confirmed += field_.confirmed
        linear.append(_whole_blocks(light))
        # The luma decides whether a frame is at black: chroma is stored at half resolution and is flat wherever the
        # scene is grey [proposed]
        codes.append(_whole_blocks(np.repeat(luma.astype(np.int64)[..., None], 3, axis=-1)))
        footprints.append(_whole_blocks(field_.footprint))
        clipped.append(_whole_blocks(field_.clipped))
    mosaic = np.concatenate(linear, axis=0)
    footprint = np.concatenate(footprints, axis=0)
    clipped_map = np.concatenate(clipped, axis=0)
    measurement = noise.measure_noise(mosaic, np.concatenate(codes, axis=0), footprint, clipped_map,
                                      encode_slope=clip.encode_slope, code_steps=clip.code_steps(), luma_weights=LUMA)
    luminance = mosaic @ np.asarray(LUMA)
    original_noise = working_noise = None
    if measurement.reason is None:
        working = np.concatenate([_filtered(clip, light, foot, measurement) for light, foot in zip(linear, footprints)], axis=0)
        blocks = noise.darkest_quarter_blocks(luminance, footprint, clipped_map)
        original_noise = measurement.luma_fine_sigma_at(tone.dark_level(luminance))
        working_noise = noise.fine_noise(working @ np.asarray(LUMA), blocks)
    lift = tone.solve_lift(luminance, encode_slope=clip.encode_slope, decode=clip.decode, original_noise=original_noise,
                           working_noise=working_noise, exposure_ev=exposure_ev)
    return ClipSettings(clip, measurement, lift, len(sampled), found, confirmed)


def _filtered(clip: ClipColour, light: np.ndarray, footprint: np.ndarray, measurement: noise.NoiseMeasurement) -> np.ndarray:
    """The spatial filter of design section 5 on one frame, guided by its noise-stabilised luminance."""
    guide = noise.stabilised(light @ np.asarray(LUMA), measurement)
    return spatial.edge_avoiding(light, guide, footprint).frame


# ---------------------------------------------------------------------------------------------------------------
# Each frame
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """A frame before the dissolve passes and the lift: made with its neighbours and filtered, linear light; its stars on
    its own original pixels; how many neighbours helped."""

    working: np.ndarray
    original: np.ndarray
    field: stars.StarField
    neighbours: int


@dataclass(frozen=True)
class FrameResult:
    """A finished frame: its index in the clip, its codes for the encoder, how many neighbours helped, its stars, and
    the lift it was given (the clip's one)."""

    index: int
    planes: tuple[np.ndarray, np.ndarray, np.ndarray]
    neighbours: int
    stars_found: int
    stars_confirmed: int
    beta: float


class _RunFrames:
    """A run's frames decoded once: codes, linear light and luma, and the stacked codes the codec copies are found on
    only where a merge needs them."""

    def __init__(self, clip: ClipColour, frames: Sequence[Sequence[np.ndarray]], need_linear: Sequence[bool],
                 stacked: bool) -> None:
        self.planes = [tuple(np.asarray(p) for p in planes) for planes in frames]
        self.luma = [clip.luma_codes(planes) for planes in self.planes]
        self.linear = [clip.to_linear(planes) if need else None for planes, need in zip(self.planes, need_linear)]
        # Codes fit 16 bits; equality is all they are used for
        self.stacked = [clip.stacked_codes(planes).astype(np.int16) if stacked else None for planes in self.planes]


def prepare_frame(settings: ClipSettings, frames: _RunFrames, centre: int) -> Prepared:
    """Frame `centre` of a run made with its registered neighbours (only with a noise table), its stars found on its
    own original pixels, and the spatial filter (only with a noise table)."""
    clip = settings.colour
    original = frames.linear[centre]
    field_ = stars.find_stars(frames.luma[centre], peak=clip.peak_code())
    if settings.measurement.reason is not None:
        return Prepared(original, original, field_, 0)
    low = max(0, centre - temporal.WINDOW_FRAMES)
    high = min(len(frames.planes), centre + temporal.WINDOW_FRAMES + 1)
    copies = []
    for index in range(low, high):
        if index == 0:
            copies.append(None)
            continue
        between = temporal.pair_shift(frames.luma[index - 1], frames.luma[index])
        copies.append(temporal.codec_copies(frames.stacked[index - 1], frames.stacked[index], between))
    window = temporal.register_window(frames.luma[low:high], centre - low, copies)
    moved = [temporal.move(frames.linear[centre + n.offset], n.shift) for n in window.neighbours]
    merged = temporal.merge_frame(original, moved, [n.usable for n in window.neighbours], settings.measurement).frame
    working = _filtered(clip, merged, field_.footprint, settings.measurement)
    return Prepared(working, original, field_, len(window.neighbours))


def finish_frame(settings: ClipSettings, working: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The clip's one lift on a frame, back to the stream's own codes."""
    return settings.colour.to_planes(tone.apply_lift(working, settings.lift.beta))


def restore_run(settings: ClipSettings, run: frame_pool.Run) -> list[FrameResult]:
    """The pool's work for one run (`frame_pool.runs` with `window=RUN_WINDOW`): each of its own frames prepared and
    finished, in order. restore-7 puts the dissolve passes between the two."""
    measured = settings.measurement.reason is None
    if measured:
        # Linear light for every frame a merge reads; the frame before the window only for its codec copies
        need = [run.before - temporal.WINDOW_FRAMES <= k < run.before + run.count + temporal.WINDOW_FRAMES
                for k in range(len(run.frames))]
        frames = _RunFrames(settings.colour, run.frames, need, stacked=True)
        first = run.before
    else:
        # No merge: the run's own frames are all a frame needs, held alone [tested: restore-7b, the owner's clips]
        own = run.frames[run.before:run.before + run.count]
        frames = _RunFrames(settings.colour, own, [True] * len(own), stacked=False)
        first = 0
    results = []
    for k in range(run.count):
        centre = first + k
        prepared = prepare_frame(settings, frames, centre)
        results.append(FrameResult(run.first + k, finish_frame(settings, prepared.working), prepared.neighbours,
                                   prepared.field.found, prepared.field.confirmed, settings.lift.beta))
    return results


__all__ = ["ClipColour", "ClipSettings", "FrameResult", "Prepared", "clip_colour", "finish_frame",
                      "measure_clip", "prepare_frame", "restore_run", "sample_indices"]
